"""eventtest 量化测试运行器。

运行 ``eventtest/`` 目录下所有 ``test_*.py``，输出量化报告：
测试总数 / 通过数 / 失败数 / 通过率 / 总耗时 / 事件计数表 / 池状态快照表 / 退出码。

运行方式：
    python -m eventtest.run_eventtest

退出码：
    0 = 全部通过（或无测试文件）
    1 = 有失败
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

# 复用 conftest 的共享报告状态单例（同进程内 pytest.main 运行，状态持久）
from eventtest.conftest import REPORT_STATE


def _resolve_report_state() -> Dict[str, Any]:
    """查找 pytest 实际使用的 REPORT_STATE 字典。

    pytest 的 ``--import-mode=prepend``（默认）可能将 conftest.py 以带包前缀
    的模块名（如 ``meta_core.eventtest.conftest``）导入，与 ``run_eventtest.py``
    通过 ``from eventtest.conftest import REPORT_STATE`` 导入的模块实例不同。
    这导致 fixture 修改的 REPORT_STATE 与运行器读取的不是同一对象。

    修复：在 pytest.main() 运行后，遍历 ``sys.modules`` 查找含 ``REPORT_STATE``
    属性且 ``event_counts`` 或 ``pool_snapshot`` 非空的模块，返回该字典。
    若未找到，回退到本模块导入的 REPORT_STATE。
    """
    # 收集所有含 REPORT_STATE 的模块
    candidates: List[Dict[str, Any]] = []
    for name, mod in sys.modules.items():
        if mod is None:
            continue
        if "conftest" not in name:
            continue
        rs = getattr(mod, "REPORT_STATE", None)
        if isinstance(rs, dict):
            candidates.append(rs)
    # 优先返回 event_counts 或 pool_snapshot 非空的字典
    for rs in candidates:
        if rs.get("event_counts") or rs.get("pool_snapshot"):
            return rs
    # 回退到本模块导入的 REPORT_STATE
    return REPORT_STATE


def _reset_all_report_states() -> None:
    """重置所有 sys.modules 中 conftest 模块的 REPORT_STATE。

    确保跨次运行无残留。对每个含 REPORT_STATE 的 conftest 模块重置
    event_counts 和 pool_snapshot。
    """
    for name, mod in sys.modules.items():
        if mod is None:
            continue
        if "conftest" not in name:
            continue
        rs = getattr(mod, "REPORT_STATE", None)
        if isinstance(rs, dict):
            rs["event_counts"] = {}
            rs["pool_snapshot"] = {}


# 事件计数表中固定展示的事件类型顺序（与 spec 事件链顺序一致）
_EVENT_TYPES_ORDER: List[str] = [
    "TickReceived",
    "DataChanged",
    "BarComposed",
    "EdgeFired",
    "FormulaEvaluated",
    "StockFiltered",
    "TransferExecuted",
    "Signal",
    "OrderPlaced",
    "OrderFilled",
    "PositionUpdated",
]

# 池状态快照表中固定展示的池节点顺序
_POOL_NODE_ORDER: List[str] = ["source", "pool_A", "pool_B", "pool_C"]

# pytest 退出码常量
_EXIT_OK = 0
_EXIT_NO_TESTS_COLLECTED = 5


class _StatsPlugin:
    """pytest 插件：捕获 terminalreporter 统计，供运行器生成量化报告。

    通过 ``pytest_terminal_summary`` hook 在测试结束后从 terminalreporter.stats
    读取 passed/failed/error/skipped 等类别计数，避免依赖退出码粗粒度语义。
    """

    def __init__(self) -> None:
        self.passed: int = 0
        self.failed: int = 0
        self.errors: int = 0
        self.skipped: int = 0
        self.collected: int = 0
        # nodeid -> 该测试 call 阶段耗时（秒）
        self.test_durations: Dict[str, float] = {}

    def pytest_runtest_makereport(self, item, call) -> None:
        """采集每个测试 call 阶段耗时（call.stop - call.start）。"""
        if call.when == "call":
            try:
                duration = call.stop - call.start
            except (AttributeError, TypeError):
                duration = 0.0
            self.test_durations[item.nodeid] = duration

    def pytest_terminal_summary(self, terminalreporter) -> None:
        stats = getattr(terminalreporter, "stats", {}) or {}
        self.passed = len(stats.get("passed", []) or [])
        self.failed = len(stats.get("failed", []) or [])
        self.errors = len(stats.get("error", []) or [])
        self.skipped = len(stats.get("skipped", []) or [])
        # collected 总数 = passed + failed + errors + skipped + xfailed 等
        # 用 terminalreporter 报告的 collected 行兜底
        self.collected = (
            self.passed + self.failed + self.errors + self.skipped
            + len(stats.get("xfailed", []) or [])
            + len(stats.get("xpassed", []) or [])
        )

    def pytest_collection_finish(self, session) -> None:
        # collected 精确数：session.testscollected
        self.collected = getattr(session, "testscollected", self.collected)


def _discover_test_files(eventtest_dir: Path) -> List[Path]:
    """发现 eventtest 目录下所有 test_*.py 文件。"""
    if not eventtest_dir.is_dir():
        return []
    return sorted(eventtest_dir.glob("test_*.py"))


def _format_percent(passed: int, total: int) -> str:
    if total <= 0:
        return "0.00%"
    return f"{(passed / total) * 100:.2f}%"


def _render_event_counts_table(event_counts: Dict[str, int]) -> List[str]:
    """渲染事件计数表（按 EventType 分组）。

    Task 1 阶段合测试尚未编写，``event_counts`` 为空时输出占位符。
    """
    lines: List[str] = []
    lines.append("事件计数表（按 EventType 分组）：")
    if not event_counts:
        lines.append("  （待 Task 9-10 填充：合测试运行后从 EventCollector 收集）")
        return lines
    for etype in _EVENT_TYPES_ORDER:
        count = event_counts.get(etype, 0)
        lines.append(f"  {etype:<20} {count}")
    extra = sorted(set(event_counts) - set(_EVENT_TYPES_ORDER))
    for etype in extra:
        lines.append(f"  {etype:<20} {event_counts[etype]}")
    return lines


def _render_pool_snapshot_table(pool_snapshot: Dict[str, List[str]]) -> List[str]:
    """渲染池状态快照表。

    Task 1 阶段合测试尚未编写，``pool_snapshot`` 为空时输出占位符。
    """
    lines: List[str] = []
    lines.append("池状态快照表：")
    if not pool_snapshot:
        lines.append("  （待 Task 9-10 填充：合测试运行后取 engine.state 各池快照）")
        return lines
    for nid in _POOL_NODE_ORDER:
        codes = pool_snapshot.get(nid)
        if codes is None:
            lines.append(f"  {nid}:  (节点未在配置中)")
        else:
            lines.append(f"  {nid}:  {len(codes)} stocks")
    extra = sorted(set(pool_snapshot) - set(_POOL_NODE_ORDER))
    for nid in extra:
        codes = pool_snapshot.get(nid, [])
        lines.append(f"  {nid}:  {len(codes)} stocks")
    return lines


def _render_test_durations_table(test_durations: Dict[str, float]) -> List[str]:
    """渲染各测试耗时表（按耗时降序）。

    Task 1 阶段无测试文件或未采集到耗时时输出占位符。
    """
    lines: List[str] = []
    lines.append("各测试耗时：")
    if not test_durations:
        lines.append("  （待 Task 2+ 填充：每个测试的耗时）")
        return lines
    # 按耗时降序排列
    for nodeid, dur in sorted(test_durations.items(), key=lambda kv: kv[1], reverse=True):
        lines.append(f"  {nodeid}  ........  {dur:.3f}s")
    return lines


def _print_report(
    total: int,
    passed: int,
    failed: int,
    duration: float,
    test_durations: Dict[str, float],
    event_counts: Dict[str, int],
    pool_snapshot: Dict[str, List[str]],
    exit_code: int,
    no_tests: bool,
) -> None:
    """打印量化测试报告。"""
    sep = "=" * 60
    print(sep)
    print("eventtest 量化测试报告")
    print(sep)
    if no_tests:
        print("无测试文件：eventtest/ 目录下未发现 test_*.py")
        print("测试总数: 0")
        print("通过数: 0")
        print("失败数: 0")
        print("通过率: N/A")
    else:
        print(f"测试总数: {total}")
        print(f"通过数: {passed}")
        print(f"失败数: {failed}")
        print(f"通过率: {_format_percent(passed, total)}")
    print()
    for line in _render_test_durations_table(test_durations):
        print(line)
    print()
    for line in _render_event_counts_table(event_counts):
        print(line)
    print()
    for line in _render_pool_snapshot_table(pool_snapshot):
        print(line)
    print()
    print(f"总耗时: {duration:.2f}s")
    if no_tests:
        print("退出码: 0 (无测试文件，正常退出)")
    elif exit_code == _EXIT_OK:
        print("退出码: 0 (全部通过)")
    else:
        print(f"退出码: 1 (有失败，pytest 原始退出码={exit_code})")
    print(sep)


def main() -> int:
    """运行 eventtest 测试套件并输出量化报告。

    Returns:
        0 = 全部通过（或无测试文件）；1 = 有失败。
    """
    eventtest_dir = Path(__file__).resolve().parent
    test_files = _discover_test_files(eventtest_dir)

    # 测试运行前重置共享报告状态，避免跨次运行残留
    # 重置所有 sys.modules 中的 conftest REPORT_STATE（pytest 可能以不同
    # 模块名导入 conftest.py，需全部重置）
    _reset_all_report_states()

    no_tests = len(test_files) == 0
    stats = _StatsPlugin()

    start_ts = time.perf_counter()
    if no_tests:
        pytest_exit = _EXIT_NO_TESTS_COLLECTED
    else:
        # 通过 pytest.main 在同进程运行，注册 _StatsPlugin 捕获精确计数，
        # 并使共享 REPORT_STATE 跨 fixture 持久。
        pytest_exit = pytest.main([
            str(eventtest_dir),
            "-v",
            "--tb=short",
        ], plugins=[stats])
    duration = time.perf_counter() - start_ts

    if no_tests or pytest_exit == _EXIT_NO_TESTS_COLLECTED:
        total = passed = failed = 0
        no_tests = True
        report_exit = 0
    else:
        total = stats.collected
        failed = stats.failed + stats.errors
        passed = max(total - failed - stats.skipped, stats.passed)
        # 优先以 stats.passed 为准（更精确）
        passed = stats.passed
        total = max(total, passed + failed + stats.skipped)
        report_exit = 0 if (pytest_exit == _EXIT_OK and failed == 0) else 1

    # 查找 pytest 实际使用的 REPORT_STATE（pytest 可能以不同模块名导入
    # conftest.py，需从 sys.modules 中找到 fixture 实际修改的字典）
    active_report_state = _resolve_report_state()
    event_counts = active_report_state.get("event_counts", {}) or {}
    pool_snapshot = active_report_state.get("pool_snapshot", {}) or {}
    test_durations = stats.test_durations

    _print_report(
        total=total,
        passed=passed,
        failed=failed,
        duration=duration,
        test_durations=test_durations,
        event_counts=event_counts,
        pool_snapshot=pool_snapshot,
        exit_code=pytest_exit,
        no_tests=no_tests,
    )
    return report_exit


if __name__ == "__main__":
    sys.exit(main())
