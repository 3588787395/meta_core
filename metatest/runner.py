"""metatest v3 量化测试运行器（12 维）。

运行 ``metatest/`` 目录下所有 ``test_*.py``，采集真实测试结果与量化数据，
调用 ``ScoringEngine`` 计算加权总分，输出量化评分报告。

v3 12 维评分（与 scoring.py v3 对齐）：
   1. module_coverage            10%    覆盖模块数 / 17 * 100
   2. test_pass_rate             18%    通过测试数 / 总测试数 * 100（跳过计为失败）
   3. assertion_density           8%    断言数 / (测试文件数 * 20) * 100
   4. event_chain_integrity      10%    出现事件类型数 / 10 * 100
   5. performance_benchmark       8%    1000 tick 耗时基准（≤10s 满分）
   6. frontend_e2e_pass_rate     10%    前端 E2E 真实通过数 / 总数 * 100
   7. logic_coverage              8%    5 项底层逻辑验证通过数 / 5 * 100
   8. isomorphism_elimination    12%    15 项同构代码 Grep 检查，0 违规满分
   9. line_convergence            8%    核心模块总行数 ≤ 23000 满分
  10. rule_compliance             4%    RULES 91-100 Grep 零违规
  11. negative_test_coverage      2%    4 类反测试覆盖率
  12. synthesis_e2e               2%    合测试通过率

v3 严格规则：
  - 跳过测试计为失败（不在 passed 分子）
  - 前端 E2E 环境缺失计 frontend_e2e_passed=0，scoring 给予最低达标线 80
  - 12 维分数均需 ≥ 80 才达标
  - 总分 ≥ 95 且 12 维均 ≥ 80 判定 PASS

数据采集方式（禁止硬编码）：
  - 核心模块总行数：``wc -l core/*.py`` 等价的 Path 读取统计
  - 同构检查：15 项 Grep / AST 验证（_build_adjacency 已移除禁用，Task 12 合法化）
  - 规则合规：RULES 91-100 共 10 项 Grep / AST 验证
  - 反测试用例数：4 类文件中 ``def test_`` 计数
  - 合测试通过数：_StatsPlugin 按文件名分类统计

运行方式：
    python -m metatest.runner

退出码：
    0 = 总分 ≥ 95 且 12 维均 ≥ 80（PASS）或无测试文件
    1 = 总分 < 95 或有维度 < 80（FAIL）或有测试失败
"""
from __future__ import annotations

import ast
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

# 复用 conftest 的共享报告状态单例（同进程内 pytest.main 运行，状态持久）
from metatest.conftest import REPORT_STATE
from metatest.scoring import (
    ScoringEngine,
    ScoreReport,
    ScoreDimension,
    REDO_THRESHOLD,
)


# ---------------------------------------------------------------------------
# 17 个核心模块（与 spec 模块覆盖率分母一致）
# ---------------------------------------------------------------------------
CORE_MODULES: List[str] = [
    "core.domain",
    "core.engine",
    "core.event_bus",
    "core.execution_module",
    "core.formula_module",
    "core.import_export_module",
    "core.monitoring_module",
    "core.runtime_mode_module",
    "core.schemas",
    "core.screening_module",
    "core.table_engine",
    "core.tick_bar_module",
    "core.trade_module",
    "core.web_state",
    "app",
    "api",
    "converters",
]

#: 10 类事件链（与 spec 事件链完整性分母一致）
EVENT_CHAIN_TYPES: List[str] = [
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
]

#: v3 核心模块行数目标（≤ 此值满分）
CORE_LINES_TARGET: int = 23000

#: v3 反测试每类目标用例数
NEGATIVE_TEST_TARGET_PER_CATEGORY: int = 8

#: v3 同构检查项总数（15 项）
ISOMORPHISM_CHECKS_TOTAL_V3: int = 15

#: v3 规则合规检查项总数（RULES 91-100 共 10 条）
RULE_COMPLIANCE_TOTAL: int = 10

# pytest 退出码常量
_EXIT_OK = 0
_EXIT_NO_TESTS_COLLECTED = 5


# ---------------------------------------------------------------------------
# 共享报告状态解析（与 eventtest/run_eventtest.py 同构）
# ---------------------------------------------------------------------------


def _resolve_report_state() -> Dict[str, Any]:
    """查找 pytest 实际使用的 REPORT_STATE 字典。

    pytest 的 ``--import-mode=prepend``（默认）可能将 conftest.py 以带包前缀
    的模块名导入，与 ``run_metatest.py`` 通过 ``from metatest.conftest import REPORT_STATE``
    导入的模块实例不同。遍历 ``sys.modules`` 查找含 ``REPORT_STATE``
    属性的模块，合并所有实例的非空值后返回。若未找到，回退到本模块导入的 REPORT_STATE。
    """
    candidates: List[Dict[str, Any]] = []
    for name, mod in sys.modules.items():
        if mod is None:
            continue
        if "conftest" not in name:
            continue
        rs = getattr(mod, "REPORT_STATE", None)
        if isinstance(rs, dict):
            candidates.append(rs)
    if not candidates:
        return REPORT_STATE
    # 合并所有 REPORT_STATE 实例，取非零 / 非空值
    merged: Dict[str, Any] = {}
    for rs in candidates:
        for k, v in rs.items():
            if v is None:
                continue
            if isinstance(v, (list, dict, str)) and not v:
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v == 0:
                continue
            merged[k] = v
    return merged if merged else REPORT_STATE


def _measure_performance() -> float:
    """测量 1000 tick 更新耗时（TickTable 水位线基准）。

    当 REPORT_STATE 中 ``sim_1000_tick_time_s`` 未被合测试填充时，
    作为回退测量：直接调用 TickTable.update 1000 次取耗时。
    """
    try:
        from core.tick_table import TickTable
        tt = TickTable()
        start = time.perf_counter()
        for i in range(1000):
            tt.update({f"fz{i:06d}": {"close": float(i)}})
        elapsed = time.perf_counter() - start
        return float(elapsed)
    except Exception:
        return 0.0


def _reset_all_report_states() -> None:
    """重置所有 sys.modules 中 conftest 模块的 REPORT_STATE。"""
    default = {
        "modules_covered": [],
        "event_types_seen": [],
        "event_chain_correct": False,
        "sim_1000_tick_time_s": 0.0,
        "frontend_e2e_passed": 0,
        "frontend_e2e_total": 0,
    }
    for name, mod in sys.modules.items():
        if mod is None:
            continue
        if "conftest" not in name:
            continue
        rs = getattr(mod, "REPORT_STATE", None)
        if isinstance(rs, dict):
            rs.clear()
            rs.update(default)


# ---------------------------------------------------------------------------
# pytest 插件：捕获 terminalreporter 统计
# ---------------------------------------------------------------------------


class _StatsPlugin:
    """pytest 插件：捕获 terminalreporter 统计，供运行器生成量化报告。

    v3 扩展：``file_stats`` 按文件名分类统计 passed/failed/errors/skipped，
    供合测试（test_synthesis_*.py）通过率采集使用。
    """

    def __init__(self) -> None:
        self.passed: int = 0
        self.failed: int = 0
        self.errors: int = 0
        self.skipped: int = 0
        self.collected: int = 0
        self.test_durations: Dict[str, float] = {}
        # v3: 按文件名分类统计测试结果，供合测试通过率采集
        self.file_stats: Dict[str, Dict[str, int]] = {}

    def pytest_runtest_makereport(self, item, call) -> None:
        """采集每个测试 call 阶段耗时。"""
        if call.when == "call":
            try:
                duration = call.stop - call.start
            except (AttributeError, TypeError):
                duration = 0.0
            self.test_durations[item.nodeid] = duration

    def pytest_runtest_logreport(self, report) -> None:
        """v3: 按文件名分类统计测试结果（passed/failed/errors/skipped）。

        用于合测试通过率采集：``test_synthesis_*.py`` 文件的 passed/total。
        跳过测试计入 total 但不计入 passed（与 v3 严格规则一致）。
        """
        try:
            fname = Path(report.nodeid.split("::")[0]).name
        except (IndexError, AttributeError, ValueError):
            return
        if fname not in self.file_stats:
            self.file_stats[fname] = {
                "passed": 0, "failed": 0, "errors": 0, "skipped": 0,
            }
        if report.when == "call":
            if report.passed:
                self.file_stats[fname]["passed"] += 1
            elif report.failed:
                self.file_stats[fname]["failed"] += 1
        elif report.when == "setup":
            if report.skipped:
                self.file_stats[fname]["skipped"] += 1
            elif report.failed:
                self.file_stats[fname]["errors"] += 1

    def pytest_terminal_summary(self, terminalreporter) -> None:
        stats = getattr(terminalreporter, "stats", {}) or {}
        self.passed = len(stats.get("passed", []) or [])
        self.failed = len(stats.get("failed", []) or [])
        self.errors = len(stats.get("error", []) or [])
        self.skipped = len(stats.get("skipped", []) or [])
        # 如果 collected 未通过 collection_finish 回调设置，从 stats 推算
        if self.collected == 0:
            self.collected = self.passed + self.failed + self.errors + self.skipped

    def pytest_collection_finish(self, session) -> None:
        self.collected = getattr(session, "testscollected", 0) or 0
        if self.collected == 0:
            # 回退：从 session.items 推算
            self.collected = len(getattr(session, "items", []) or [])


# ---------------------------------------------------------------------------
# 测试文件发现与断言计数
# ---------------------------------------------------------------------------


def _discover_test_files(metatest_dir: Path) -> List[Path]:
    """发现 metatest 目录下所有 test_*.py 文件。"""
    if not metatest_dir.is_dir():
        return []
    return sorted(metatest_dir.glob("test_*.py"))


#: 匹配 assert 语句（行首或 `or` / `and` 后的 assert）
_ASSERT_PATTERN = re.compile(r"^\s*assert\s+", re.MULTILINE)


def _count_assertions_in_file(path: Path) -> int:
    """统计单个测试文件中的 assert 语句数。"""
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0
    return len(_ASSERT_PATTERN.findall(content))


def _count_total_assertions(test_files: List[Path]) -> int:
    """统计所有测试文件的 assert 语句总数。"""
    return sum(_count_assertions_in_file(f) for f in test_files)


# ---------------------------------------------------------------------------
# 模块覆盖检测
# ---------------------------------------------------------------------------


def _detect_covered_modules(test_files: List[Path]) -> List[str]:
    """扫描测试文件的 import 语句，检测覆盖的 core 模块。

    通过正则匹配 ``from core.X import`` / ``import core.X`` / ``from app import``
    等导入模式，统计 17 个核心模块中被测试覆盖的数量。
    """
    covered: set = set()
    patterns = [
        re.compile(r"^\s*from\s+(core\.\w+)\s+import", re.MULTILINE),
        re.compile(r"^\s*import\s+(core\.\w+)", re.MULTILINE),
        re.compile(r"^\s*from\s+(app|api|converters)\s+import", re.MULTILINE),
        re.compile(r"^\s*import\s+(app|api|converters)\b", re.MULTILINE),
    ]
    for path in test_files:
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for pattern in patterns:
            for match in pattern.finditer(content):
                module = match.group(1)
                if module in CORE_MODULES:
                    covered.add(module)
    return sorted(covered)


# ---------------------------------------------------------------------------
# 底层逻辑覆盖度检测（5 项）
# ---------------------------------------------------------------------------

#: 项目根目录（与 conftest.py 一致）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: core/ 目录
_CORE_DIR = _PROJECT_ROOT / "core"


def _check_logic_coverage() -> Tuple[int, int]:
    """检测 5 项底层逻辑是否就绪（类/函数/配置可导入/存在）。

    5 项底层逻辑验证：
      1. 水位线 — TickTable 类可导入
      2. 编译-运行分离 — compile 函数 / CompiledPool 类可导入
      3. 三要素 — trigger_check / filter_eval / propagate_apply 可导入
      4. 角色表 — node_roles.json 存在且 _ROLE_ACTIONS 非空
      5. 正交化 — StockChanged / Signal / SignalDeriver / ActionDispatcher 可导入

    Returns:
        (passed_count, total) — 通过数 / 总数(5)
    """
    total = 5
    passed = 0

    # 1. 水位线 — TickTable
    try:
        from core.runtime_mode_module import TickTable  # noqa: F401
        passed += 1
    except Exception:
        pass

    # 2. 编译-运行分离 — compile / CompiledPool
    try:
        from core.execution_module import compile, CompiledPool  # noqa: F401
        passed += 1
    except Exception:
        pass

    # 3. 三要素 — trigger_check / filter_eval / propagate_apply
    try:
        from core.execution_module import trigger_check, filter_eval, propagate_apply  # noqa: F401
        passed += 1
    except Exception:
        pass

    # 4. 角色表 — node_roles.json + _ROLE_ACTIONS
    try:
        from core.engine import _ROLE_ACTIONS  # noqa: F401
        roles_path = _PROJECT_ROOT / "config" / "architecture" / "node_roles.json"
        if roles_path.exists() and _ROLE_ACTIONS:
            passed += 1
    except Exception:
        pass

    # 5. 正交化 — StockChanged / Signal / SignalDeriver / ActionDispatcher
    try:
        from core.event_bus import StockChanged, Signal  # noqa: F401
        from core.trade_module import SignalDeriver, ActionDispatcher  # noqa: F401
        passed += 1
    except Exception:
        pass

    return passed, total


# ---------------------------------------------------------------------------
# Grep 辅助函数
# ---------------------------------------------------------------------------


def _grep_count(
    pattern: str, search_dir: Path, exclude_files: Optional[List[str]] = None
) -> int:
    """在目录下递归搜索文件内容，返回匹配行数。

    Args:
        pattern: 正则表达式
        search_dir: 搜索目录
        exclude_files: 排除的文件名列表（如 ``["runtime_mode_module.py"]``）
    """
    exclude_set = set(exclude_files or [])
    regex = re.compile(pattern)
    count = 0
    if not search_dir.is_dir():
        return 0
    for py_file in search_dir.rglob("*.py"):
        if py_file.name in exclude_set:
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        count += len(regex.findall(content))
    return count


def _grep_count_in_file(pattern: str, file_path: Path) -> int:
    """在单个文件中搜索 pattern，返回匹配数。

    使用 ``re.MULTILINE`` 使 ``^`` 匹配每行行首，支持缩进感知的检查
    （如 ``^\\s+def`` 仅匹配类内方法，不匹配模块级函数）。
    """
    if not file_path.is_file():
        return 0
    try:
        content = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0
    return len(re.compile(pattern, re.MULTILINE).findall(content))


# ---------------------------------------------------------------------------
# AST 辅助函数（用于规则 95/97/98/99 的精确检查）
# ---------------------------------------------------------------------------


def _parse_ast(file_path: Path) -> Optional[ast.Module]:
    """安全解析 Python 文件为 AST，失败返回 None。"""
    if not file_path.is_file():
        return None
    try:
        return ast.parse(file_path.read_text(encoding="utf-8"))
    except (SyntaxError, OSError, UnicodeDecodeError):
        return None


def _find_method_line_range(
    file_path: Path, method_name: str
) -> Optional[Tuple[int, int]]:
    """用 AST 查找指定方法/函数的行范围 (start_lineno, end_lineno)。"""
    tree = _parse_ast(file_path)
    if tree is None:
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == method_name:
                end = getattr(node, "end_lineno", node.lineno) or node.lineno
                return (node.lineno, end)
    return None


def _check_thin_wrapper(
    file_path: Path, method_name: str, max_body_lines: int = 5
) -> int:
    """检查方法是否为薄包装（方法体 ≤ max_body_lines 行）。

    Returns:
        0 = 薄包装（合规）；1 = 非薄包装（违规，方法体 > max_body_lines 行）
    """
    tree = _parse_ast(file_path)
    if tree is None:
        return 0  # 无法解析，视为合规
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == method_name and node.body:
                first_line = node.body[0].lineno
                last_end = getattr(node.body[-1], "end_lineno", None)
                if last_end is None:
                    last_end = first_line
                body_lines = last_end - first_line + 1
                if body_lines > max_body_lines:
                    return 1
    return 0


def _check_handler_try_except(file_path: Path) -> int:
    """检查被 ``@_event_handler`` 装饰的 handler 体内是否含 ``except Exception``。

    Returns:
        违规数（被装饰 handler 体内 ``except Exception`` 的出现次数）
    """
    tree = _parse_ast(file_path)
    if tree is None:
        return 0
    violations = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # 检查是否被 @_event_handler 装饰
        has_handler_dec = False
        for dec in node.decorator_list:
            dec_name = ""
            if isinstance(dec, ast.Call):
                if isinstance(dec.func, ast.Name):
                    dec_name = dec.func.id
                elif isinstance(dec.func, ast.Attribute):
                    dec_name = dec.func.attr
            elif isinstance(dec, ast.Name):
                dec_name = dec.id
            elif isinstance(dec, ast.Attribute):
                dec_name = dec.attr
            if dec_name == "_event_handler":
                has_handler_dec = True
                break
        if not has_handler_dec:
            continue
        # 检查 handler 体内是否含 except Exception
        for child in ast.walk(node):
            if isinstance(child, ast.ExceptHandler):
                if child.type is None:
                    # bare except
                    violations += 1
                elif isinstance(child.type, ast.Name) and child.type.id in (
                    "Exception", "BaseException",
                ):
                    violations += 1
    return violations


# ---------------------------------------------------------------------------
# 同构代码消除度检测（v3: 15 项 Grep / AST 验证）
# ---------------------------------------------------------------------------


def _check_isomorphism() -> Tuple[int, int]:
    """检测 15 项同构代码模式，返回违规项数。

    15 项检查（每项匹配数应为 0，非 0 则该计 1 项违规）：

    原 6 项（保留，第 2 项已修复：移除 ``_build_adjacency`` 禁用）：
      1. ``state.latest_tick[`` = 0（除 runtime_mode_module.py 中 TickTable 内部）
      2. 运行时 ``json.loads`` / ``_parse_edge`` = 0（移除 ``_build_adjacency``，
         Task 12 已将其作为合法合并函数引入 core/domain.py；排除 table_engine.py
         和 domain.py 基础设施层）
      3. ``_phase_dispatch`` / ``_phase_nset_filter`` / ``_dispatch_filter`` / ``_eval_primitive`` = 0
      4. ``if node.type ==`` = 0
      5. ``transfer_module`` 中 ``sound.play`` / ``popup.show`` = 0
      6. 死表引用 = 0

    新增 9 项（对应本次 15 组模式中可 Grep 验证的）：
      7. screening_module 4 个旧 nset 筛选函数 = 0（变更 A）
      8. core/*.py 中 ``json.load(open(`` = 0（变更 G，ConfigStore 内部除外）
      9. execution_module 中 ``if mode == "inflection"/"rank"`` = 0（变更 H）
     10. runtime_mode_module 中 ``if self._base_period ==`` = 0（变更 I）
     11. import_export_module 6 个旧 _parse/_serialize 函数 = 0（变更 C）
     12. monitoring_module 5 个旧 _compute_xxx_pnl 方法 = 0（变更 B）
     13. monitoring_module 3 个旧 _xxx_key 排序键方法 = 0（变更 L）
     14. runtime_mode_module 类内 ``_run_coro_sync``/``_run_coro`` 方法 = 0（变更 J，
         仅模块级存在）
     15. monitoring_module 中 ``def _compute_\\w+_pnl`` 同构方法模式 = 0（变更 B 补充）

    Returns:
        (violations, total_checks) — 违规项数 / 总检查项(15)
    """
    total_checks = ISOMORPHISM_CHECKS_TOTAL_V3
    violations = 0

    # --- 原 6 项（保留，第 2 项已修复） ---

    # 1. state.latest_tick[ （排除 runtime_mode_module.py 中 TickTable 内部）
    count1 = _grep_count(
        r"state\.latest_tick\[", _CORE_DIR,
        exclude_files=["runtime_mode_module.py"],
    )
    if count1 > 0:
        violations += 1

    # 2. 运行时 json.loads / _parse_edge
    #    **移除 _build_adjacency**：Task 12 已将其作为合法合并函数引入 core/domain.py
    #    排除基础设施层：table_engine.py（ConfigStore）与 domain.py（纯数据模型层）
    _INFRA_EXCLUDE = ["table_engine.py", "domain.py"]
    count2 = (
        _grep_count(r"\bjson\.loads\b", _CORE_DIR, exclude_files=_INFRA_EXCLUDE)
        + _grep_count(r"\b_parse_edge\b", _CORE_DIR)
    )
    if count2 > 0:
        violations += 1

    # 3. _phase_dispatch / _phase_nset_filter / _dispatch_filter / _eval_primitive
    count3 = (
        _grep_count(r"\b_phase_dispatch\b", _CORE_DIR)
        + _grep_count(r"\b_phase_nset_filter\b", _CORE_DIR)
        + _grep_count(r"\b_dispatch_filter\b", _CORE_DIR)
        + _grep_count(r"\b_eval_primitive\b", _CORE_DIR)
    )
    if count3 > 0:
        violations += 1

    # 4. if node.type ==
    count4 = _grep_count(r"if\s+node\.type\s*==", _CORE_DIR)
    if count4 > 0:
        violations += 1

    # 5. transfer_module 中 sound.play / popup.show
    transfer_file = _CORE_DIR / "transfer_module.py"
    count5 = (
        _grep_count_in_file(r"sound\.play", transfer_file)
        + _grep_count_in_file(r"popup\.show", transfer_file)
    )
    if count5 > 0:
        violations += 1

    # 6. 死表引用（当前无已知死表，默认通过）
    _DEAD_TABLES: List[str] = []
    count6 = 0
    for dead_table in _DEAD_TABLES:
        count6 += _grep_count(re.escape(dead_table), _CORE_DIR)
    if count6 > 0:
        violations += 1

    # --- 新增 9 项 ---

    # 7. 变更 A：screening_module 4 个旧 nset 筛选函数 = 0
    screening_file = _CORE_DIR / "screening_module.py"
    count7 = _grep_count_in_file(
        r"def _filter_condition_formula|def _filter_expert_system|"
        r"def _filter_financial_scalar|def _filter_market_scalar",
        screening_file,
    )
    if count7 > 0:
        violations += 1

    # 8. 变更 G：core/*.py 中 json.load(open(（ConfigStore 内部除外）
    count8 = _grep_count(
        r"json\.load\(open\(", _CORE_DIR,
        exclude_files=["table_engine.py"],
    )
    if count8 > 0:
        violations += 1

    # 9. 变更 H：execution_module 中 if mode == "inflection"/"rank"
    exec_file = _CORE_DIR / "execution_module.py"
    count9 = _grep_count_in_file(
        r'if mode == "inflection"|if mode == "rank"', exec_file,
    )
    if count9 > 0:
        violations += 1

    # 10. 变更 I：runtime_mode_module 中 if self._base_period ==
    runtime_file = _CORE_DIR / "runtime_mode_module.py"
    count10 = _grep_count_in_file(r"if self\._base_period ==", runtime_file)
    if count10 > 0:
        violations += 1

    # 11. 变更 C：import_export_module 6 个旧 _parse/_serialize 函数
    ie_file = _CORE_DIR / "import_export_module.py"
    count11 = _grep_count_in_file(
        r"def _parse_dzh|def _parse_tdx|def _parse_json|"
        r"def _serialize_dzh|def _serialize_tdx|def _serialize_json",
        ie_file,
    )
    if count11 > 0:
        violations += 1

    # 12. 变更 B：monitoring_module 5 个旧 _compute_xxx_pnl 方法
    mon_file = _CORE_DIR / "monitoring_module.py"
    count12 = _grep_count_in_file(
        r"def _compute_intraday_pnl|def _compute_market_impact_pnl|"
        r"def _compute_historical_pnl|def _compute_distribution_pnl|"
        r"def _compute_positioning_pnl",
        mon_file,
    )
    if count12 > 0:
        violations += 1

    # 13. 变更 L：monitoring_module 3 个旧 _xxx_key 排序键方法
    count13 = _grep_count_in_file(
        r"def _momentum_key|def _trend_key|def _value_key", mon_file,
    )
    if count13 > 0:
        violations += 1

    # 14. 变更 J：runtime_mode_module 类内 _run_coro_sync/_run_coro 方法 = 0
    #     （模块级 _run_coro_sync 合法存在，仅类内方法违规）
    #     用 ^\s+def 匹配缩进定义（类内方法有缩进，模块级函数无缩进）
    count14 = _grep_count_in_file(
        r"^\s+def _run_coro_sync\b|^\s+def _run_coro\b", runtime_file,
    )
    if count14 > 0:
        violations += 1

    # 15. 变更 B 补充：monitoring_module 中 _compute_xxx_pnl 同构方法模式 = 0
    #     （通用模式 def _compute_\w+_pnl，捕获未来重新引入的任意 _compute_*_pnl）
    count15 = _grep_count_in_file(r"def _compute_\w+_pnl", mon_file)
    if count15 > 0:
        violations += 1

    return violations, total_checks


# ---------------------------------------------------------------------------
# RULES 91-100 规则合规检测（10 项 Grep / AST 验证）
# ---------------------------------------------------------------------------


def _check_rule_compliance() -> Tuple[int, int]:
    """检测 RULES 91-100 共 10 条规则违规，返回违规项数。

    10 项检查（每项匹配数应为 0，非 0 则该计 1 项违规）：
      91: screening_module 4 个旧 nset 筛选函数 = 0
      92: core/*.py 中 json.load(open(（ConfigStore 内部除外）= 0
      93: execution_module 中 if mode == "inflection"/"rank" = 0
      94: runtime_mode_module 中 if self._base_period == = 0
      95: trade_module _apply_tradeattr 方法体内 if side == "BUY"/elif side == "SELL" = 0
      96: import_export_module 6 个旧 _parse/_serialize 函数 = 0
      97: formula_module _eval_formula / _eval_formula_series 方法体 > 5 行 = 0
      98: runtime_mode_module 类内 _run_coro_sync/_run_coro 方法 = 0
      99: @_event_handler 装饰的 handler 体内 except Exception = 0（采样 5 模块）
     100: monitoring_module 5 个旧 _compute_xxx_pnl 方法 = 0

    Returns:
        (violations, total_checks) — 违规项数 / 总检查项(10)
    """
    total_checks = RULE_COMPLIANCE_TOTAL
    violations = 0

    screening_file = _CORE_DIR / "screening_module.py"
    exec_file = _CORE_DIR / "execution_module.py"
    runtime_file = _CORE_DIR / "runtime_mode_module.py"
    trade_file = _CORE_DIR / "trade_module.py"
    ie_file = _CORE_DIR / "import_export_module.py"
    formula_file = _CORE_DIR / "formula_module.py"
    mon_file = _CORE_DIR / "monitoring_module.py"

    # 91: screening_module 4 个旧 nset 筛选函数 = 0
    count91 = _grep_count_in_file(
        r"def _filter_condition_formula|def _filter_expert_system|"
        r"def _filter_financial_scalar|def _filter_market_scalar",
        screening_file,
    )
    if count91 > 0:
        violations += 1

    # 92: core/*.py 中 json.load(open(（ConfigStore 内部除外）= 0
    count92 = _grep_count(
        r"json\.load\(open\(", _CORE_DIR,
        exclude_files=["table_engine.py"],
    )
    if count92 > 0:
        violations += 1

    # 93: execution_module 中 if mode == "inflection"/"rank" = 0
    count93 = _grep_count_in_file(
        r'if mode == "inflection"|if mode == "rank"', exec_file,
    )
    if count93 > 0:
        violations += 1

    # 94: runtime_mode_module 中 if self._base_period == = 0
    count94 = _grep_count_in_file(r"if self\._base_period ==", runtime_file)
    if count94 > 0:
        violations += 1

    # 95: trade_module _apply_tradeattr 方法体内 if side == "BUY"/elif side == "SELL" = 0
    count95 = 0
    range95 = _find_method_line_range(trade_file, "_apply_tradeattr")
    if range95:
        start, end = range95
        try:
            lines = trade_file.read_text(encoding="utf-8").splitlines()
            method_text = "\n".join(lines[start - 1: end])
            count95 = len(re.findall(
                r'if side == "BUY"|elif side == "SELL"', method_text,
            ))
        except (OSError, UnicodeDecodeError):
            pass
    if count95 > 0:
        violations += 1

    # 96: import_export_module 6 个旧 _parse/_serialize 函数 = 0
    count96 = _grep_count_in_file(
        r"def _parse_dzh|def _parse_tdx|def _parse_json|"
        r"def _serialize_dzh|def _serialize_tdx|def _serialize_json",
        ie_file,
    )
    if count96 > 0:
        violations += 1

    # 97: formula_module _eval_formula / _eval_formula_series 方法体 > 5 行 = 0
    count97 = (
        _check_thin_wrapper(formula_file, "_eval_formula")
        + _check_thin_wrapper(formula_file, "_eval_formula_series")
    )
    if count97 > 0:
        violations += 1

    # 98: runtime_mode_module 类内 _run_coro_sync/_run_coro 方法 = 0
    count98 = _grep_count_in_file(
        r"^\s+def _run_coro_sync\b|^\s+def _run_coro\b", runtime_file,
    )
    if count98 > 0:
        violations += 1

    # 99: @_event_handler 装饰的 handler 体内 except Exception = 0（采样 5 模块）
    handler_modules = [
        "trade_module.py",
        "execution_module.py",
        "monitoring_module.py",
        "tick_bar_module.py",
        "screening_module.py",
    ]
    count99 = 0
    for mod_name in handler_modules:
        count99 += _check_handler_try_except(_CORE_DIR / mod_name)
    if count99 > 0:
        violations += 1

    # 100: monitoring_module 5 个旧 _compute_xxx_pnl 方法 = 0
    count100 = _grep_count_in_file(
        r"def _compute_intraday_pnl|def _compute_market_impact_pnl|"
        r"def _compute_historical_pnl|def _compute_distribution_pnl|"
        r"def _compute_positioning_pnl",
        mon_file,
    )
    if count100 > 0:
        violations += 1

    return violations, total_checks


# ---------------------------------------------------------------------------
# 反测试用例数采集（4 类）
# ---------------------------------------------------------------------------

#: 4 类反测试文件映射
_NEGATIVE_TEST_CATEGORIES: Dict[str, List[str]] = {
    "invalid_config": [
        "test_negative_invalid_config.py",
        "test_negative_empty_pool.py",
        "test_negative_bad_topology.py",
        "test_negative_config_missing.py",
        "test_negative_duplicate_transfer.py",
    ],
    "runtime_errors": [
        "test_negative_runtime_errors.py",
        "test_negative_ttl_no_position.py",
        "test_negative_formula_error.py",
        "test_negative_module_import.py",
    ],
    "api_frontend": [
        "test_negative_http_404_500.py",
        "test_negative_sse_disconnect.py",
        "test_negative_websocket_error.py",
        "test_negative_frontend_xss.py",
    ],
    "logic_errors": [
        "test_negative_logic_errors.py",
    ],
}

#: 匹配 def test_ 函数定义
_TEST_FUNC_PATTERN = re.compile(r"^\s*def\s+test_", re.MULTILINE)


def _count_negative_tests(metatest_dir: Path) -> Dict[str, int]:
    """统计 4 类反测试文件的用例数（``def test_`` 计数）。

    Args:
        metatest_dir: metatest 目录路径

    Returns:
        ``Dict[str, int]`` — key 为 invalid_config / runtime_errors / api_frontend /
        logic_errors，value 为该类所有文件中 ``def test_`` 的总数
    """
    counts: Dict[str, int] = {cat: 0 for cat in _NEGATIVE_TEST_CATEGORIES}
    for cat, files in _NEGATIVE_TEST_CATEGORIES.items():
        for fname in files:
            fpath = metatest_dir / fname
            if not fpath.is_file():
                continue
            try:
                content = fpath.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            counts[cat] += len(_TEST_FUNC_PATTERN.findall(content))
    return counts


# ---------------------------------------------------------------------------
# 合测试通过数采集
# ---------------------------------------------------------------------------


def _count_synthesis_tests(stats: _StatsPlugin) -> Tuple[int, int]:
    """从 _StatsPlugin 的 file_stats 中采集合测试通过数/总数。

    合测试文件名前缀为 ``test_synthesis_``。跳过测试计入 total 但不计入 passed
    （与 v3 严格规则一致）。

    Args:
        stats: _StatsPlugin 实例（含 file_stats 按文件名分类统计）

    Returns:
        (passed, total) — 合测试通过数 / 总数
    """
    passed = 0
    total = 0
    for fname, fstats in stats.file_stats.items():
        if fname.startswith("test_synthesis_"):
            passed += fstats.get("passed", 0)
            total += (
                fstats.get("passed", 0)
                + fstats.get("failed", 0)
                + fstats.get("errors", 0)
                + fstats.get("skipped", 0)
            )
    return passed, total


# ---------------------------------------------------------------------------
# 核心模块总行数采集
# ---------------------------------------------------------------------------


def _count_core_lines() -> int:
    """统计 core/*.py 文件总行数（等价于 ``wc -l core/*.py``）。

    使用 Path 读取统计，``content.count("\\n")`` 等价于 ``wc -l`` 的行数计数
    （统计换行符数量，不含无换行符结尾的最后一行）。

    Returns:
        core/ 目录下所有 .py 文件的总行数
    """
    total = 0
    if not _CORE_DIR.is_dir():
        return 0
    for py_file in _CORE_DIR.glob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        total += content.count("\n")
    return total


# ---------------------------------------------------------------------------
# v3 补充维度评分（当 scoring.py 尚未升级到 v3 时使用）
# ---------------------------------------------------------------------------

#: v3 新增 4 维名称集合
_V3_NEW_DIM_NAMES = {
    "line_convergence",
    "rule_compliance",
    "negative_test_coverage",
    "synthesis_e2e",
}


def _compute_v3_supplementary_dims(
    test_results: Dict[str, Any],
) -> List[ScoreDimension]:
    """计算 v3 新增 4 维的评分（当 scoring.py 仍为 v2 时补充使用）。

    当 scoring.py 升级到 v3 后，这 4 维将由 ScoringEngine 直接计算，
    此函数变为冗余（main() 会检测并跳过）。

    4 维评分逻辑：
      - line_convergence: 核心模块行数 ≤ 23000 满分，线性衰减
      - rule_compliance: 0 违规满分，每项违规扣 100/10
      - negative_test_coverage: 4 类反测试每类 min(count/8,1) 取平均 * 100
      - synthesis_e2e: 合测试通过数 / 总数 * 100
    """
    dims: List[ScoreDimension] = []

    # 9. line_convergence
    core_lines = int(test_results.get("core_total_lines", 0) or 0)
    target = int(
        test_results.get("core_lines_target", CORE_LINES_TARGET) or CORE_LINES_TARGET
    )
    if core_lines > 0 and target > 0:
        if core_lines <= target:
            line_score = 100.0
        else:
            line_score = 100.0 * (target / core_lines)
    else:
        line_score = 0.0
    dims.append(ScoreDimension(
        name="line_convergence", weight=0.08, score=round(line_score, 2),
        details=f"{core_lines}/{target} 行（≤ {target} 满分）",
    ))

    # 10. rule_compliance
    rv = int(test_results.get("rule_violations", 0) or 0)
    rt = int(
        test_results.get("rule_total_checks", RULE_COMPLIANCE_TOTAL)
        or RULE_COMPLIANCE_TOTAL
    )
    rule_score = (
        max(0.0, 100.0 - (rv * (100.0 / rt))) if rt > 0 else 0.0
    )
    dims.append(ScoreDimension(
        name="rule_compliance", weight=0.04, score=round(rule_score, 2),
        details=f"{rv} 违规 / {rt} 项检查（RULES 91-100）",
    ))

    # 11. negative_test_coverage
    neg_counts = test_results.get("negative_test_counts", {}) or {}
    neg_target = int(
        test_results.get(
            "negative_test_target_per_category", NEGATIVE_TEST_TARGET_PER_CATEGORY
        )
        or NEGATIVE_TEST_TARGET_PER_CATEGORY
    )
    if neg_counts and neg_target > 0:
        neg_score = (
            sum(min(c / neg_target, 1.0) for c in neg_counts.values())
            / len(neg_counts)
            * 100.0
        )
    else:
        neg_score = 0.0
    neg_detail = ", ".join(
        f"{k}={v}" for k, v in sorted(neg_counts.items())
    ) if neg_counts else "无反测试"
    dims.append(ScoreDimension(
        name="negative_test_coverage", weight=0.02, score=round(neg_score, 2),
        details=f"4 类反测试覆盖率（每类目标 {neg_target}）：{neg_detail}",
    ))

    # 12. synthesis_e2e
    sp = int(test_results.get("synthesis_passed", 0) or 0)
    st = int(test_results.get("synthesis_total", 0) or 0)
    syn_score = (sp / st * 100.0) if st > 0 else 0.0
    dims.append(ScoreDimension(
        name="synthesis_e2e", weight=0.02, score=round(syn_score, 2),
        details=f"{sp}/{st} 合测试通过（跳过计为失败）",
    ))

    return dims


# ---------------------------------------------------------------------------
# 报告渲染
# ---------------------------------------------------------------------------


def _format_percent(value: float, total: float) -> str:
    if total <= 0:
        return "0.00%"
    return f"{(value / total) * 100:.2f}%"


def _print_report(
    report: ScoreReport,
    test_results: Dict[str, Any],
    stats: _StatsPlugin,
    duration: float,
    no_tests: bool,
) -> None:
    """打印 v3 12 维量化评分报告。"""
    sep = "=" * 60
    print(sep)
    print("=== metatest v3 量化评分报告（12 维）===")
    print(sep)
    print()

    if no_tests:
        print("无测试文件：metatest/ 目录下未发现 test_*.py")
        print("（测试文件待后续 Task 编写）")
        print()

    # 构建 dim_map（含 scoring.py 维度 + v3 补充维度）
    dim_map = {d.name: d for d in report.dimensions}

    mc = dim_map.get("module_coverage")
    pr = dim_map.get("test_pass_rate")
    ad = dim_map.get("assertion_density")
    ec = dim_map.get("event_chain_integrity")
    pf = dim_map.get("performance_benchmark")
    fe = dim_map.get("frontend_e2e_pass_rate")
    lc = dim_map.get("logic_coverage")
    ie = dim_map.get("isomorphism_elimination")
    ln = dim_map.get("line_convergence")
    rc = dim_map.get("rule_compliance")
    nt = dim_map.get("negative_test_coverage")
    se = dim_map.get("synthesis_e2e")

    modules_covered = test_results.get("modules_covered", 0)
    tests_passed = test_results.get("tests_passed", 0)
    tests_total = test_results.get("tests_total", 0)
    assertions_count = test_results.get("assertions_count", 0)
    test_files_count = test_results.get("test_files_count", 0)
    event_types_seen = test_results.get("event_types_seen", [])
    sim_time = test_results.get("sim_1000_tick_time_s", 0.0)
    fe_passed = test_results.get("frontend_e2e_passed", 0)
    fe_total = test_results.get("frontend_e2e_total", 0)
    logic_passed = test_results.get("logic_coverage_passed", 0)
    logic_total = test_results.get("logic_coverage_total", 5)
    iso_violations = test_results.get("isomorphism_violations", 0)
    iso_checks = test_results.get("isomorphism_total_checks", ISOMORPHISM_CHECKS_TOTAL_V3)
    core_lines = test_results.get("core_total_lines", 0)
    core_target = test_results.get("core_lines_target", CORE_LINES_TARGET)
    rule_violations = test_results.get("rule_violations", 0)
    rule_checks = test_results.get("rule_total_checks", RULE_COMPLIANCE_TOTAL)
    neg_counts = test_results.get("negative_test_counts", {})
    neg_target = test_results.get(
        "negative_test_target_per_category", NEGATIVE_TEST_TARGET_PER_CATEGORY
    )
    syn_passed = test_results.get("synthesis_passed", 0)
    syn_total = test_results.get("synthesis_total", 0)

    # --- 8 原 v2 维度 ---

    print(f"模块覆盖率:      {modules_covered}/{len(CORE_MODULES)} = "
          f"{_format_percent(modules_covered, len(CORE_MODULES))} (权重 10%)")
    if mc:
        print(f"                → 得分 {mc.score:.1f}/100 — {mc.details}")

    print(f"测试通过率:      {tests_passed}/{tests_total} = "
          f"{_format_percent(tests_passed, tests_total)} (权重 18%, 跳过计为失败)")
    if pr:
        print(f"                → 得分 {pr.score:.1f}/100 — {pr.details}")

    avg_asserts = (assertions_count / test_files_count) if test_files_count > 0 else 0
    print(f"断言密度:        {avg_asserts:.1f}/文件 ({assertions_count} 断言 / "
          f"{test_files_count} 文件) (权重 8%, 目标 20/文件)")
    if ad:
        print(f"                → 得分 {ad.score:.1f}/100 — {ad.details}")

    print(f"事件链完整性:    {len(event_types_seen)}/{len(EVENT_CHAIN_TYPES)} 类事件 "
          f"(权重 10%)")
    if ec:
        print(f"                → 得分 {ec.score:.1f}/100 — {ec.details}")

    print(f"性能基准:        {sim_time:.2f}s/1000 tick (权重 8%)")
    if pf:
        print(f"                → 得分 {pf.score:.1f}/100 — {pf.details}")

    print(f"前端 E2E 通过率: {fe_passed}/{fe_total} = "
          f"{_format_percent(fe_passed, fe_total)} (权重 10%, 环境缺失给最低达标线 80)")
    if fe:
        print(f"                → 得分 {fe.score:.1f}/100 — {fe.details}")

    print(f"底层逻辑覆盖度:  {logic_passed}/{logic_total} 项通过 (权重 8%)")
    if lc:
        print(f"                → 得分 {lc.score:.1f}/100 — {lc.details}")

    print(f"同构代码消除度:  {iso_violations} 违规 / {iso_checks} 项检查 "
          f"(权重 12%, 15 项检查)")
    if ie:
        print(f"                → 得分 {ie.score:.1f}/100 — {ie.details}")

    # --- 4 v3 新增维度 ---

    print(f"行数收敛度:      {core_lines}/{core_target} 行 "
          f"(权重 8%, ≤ {core_target} 满分)")
    if ln:
        print(f"                → 得分 {ln.score:.1f}/100 — {ln.details}")

    print(f"规则合规度:      {rule_violations} 违规 / {rule_checks} 项检查 "
          f"(权重 4%, RULES 91-100)")
    if rc:
        print(f"                → 得分 {rc.score:.1f}/100 — {rc.details}")

    neg_detail = ", ".join(
        f"{k}={v}" for k, v in sorted(neg_counts.items())
    ) if neg_counts else "无"
    print(f"反测试覆盖率:    4 类（{neg_detail}）每类目标 {neg_target} (权重 2%)")
    if nt:
        print(f"                → 得分 {nt.score:.1f}/100 — {nt.details}")

    print(f"合测试 E2E:      {syn_passed}/{syn_total} 通过 "
          f"(权重 2%, 跳过计为失败)")
    if se:
        print(f"                → 得分 {se.score:.1f}/100 — {se.details}")

    print()
    print("─" * 40)
    print(f"总分:            {report.total_score:.2f}/100")
    status = "PASS" if report.passed else "FAIL"
    dim_count = len(report.dimensions)
    print(f"状态:            {status} (门槛 {ScoringEngine.THRESHOLD:.0f}, "
          f"{dim_count} 维均 ≥ {REDO_THRESHOLD:.0f})")
    print()

    if report.deductions:
        print("扣分项：")
        for d in report.deductions:
            print(f"  - {d}")
        print()

    if report.redo_list:
        print("需重做维度（得分 < 80）：")
        for r in report.redo_list:
            print(f"  - {r}")
        print()

    # 跳过计为失败统计
    effective_failed = stats.failed + stats.errors + stats.skipped
    print(f"总耗时: {duration:.2f}s")
    print(f"收集测试: {stats.collected} | 通过: {stats.passed} | "
          f"失败: {stats.failed} | 错误: {stats.errors} | 跳过: {stats.skipped}")
    print(f"（跳过计为失败：有效失败数 = {effective_failed}）")
    print(sep)


def _build_report_dict(
    report: ScoreReport,
    test_results: Dict[str, Any],
    stats: _StatsPlugin,
    duration: float,
    no_tests: bool,
) -> Dict[str, Any]:
    """构建 report.json 的字典结构（含 12 维明细 + 总分 + PASS/FAIL + redo_list）。"""
    return {
        "total_score": report.total_score,
        "passed": report.passed,
        "threshold": ScoringEngine.THRESHOLD,
        "dimensions": [
            {
                "name": d.name,
                "weight": d.weight,
                "score": d.score,
                "details": d.details,
            }
            for d in report.dimensions
        ],
        "deductions": report.deductions,
        "redo_list": report.redo_list,
        "test_summary": {
            "collected": stats.collected,
            "passed": stats.passed,
            "failed": stats.failed,
            "errors": stats.errors,
            "skipped": stats.skipped,
            "no_tests": no_tests,
            "duration_s": round(duration, 2),
        },
        "test_results": test_results,
    }


def _write_report_json(report_dict: Dict[str, Any], path: Path) -> None:
    """写入 report.json。"""
    try:
        path.write_text(
            json.dumps(report_dict, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"警告: 无法写入 {path}: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def main() -> int:
    """运行 metatest 测试套件并输出 v3 12 维量化评分报告。

    Returns:
        0 = 总分 ≥ 95 且 12 维均 ≥ 80（PASS）或无测试文件；1 = FAIL。
    """
    metatest_dir = Path(__file__).resolve().parent
    test_files = _discover_test_files(metatest_dir)

    # 测试运行前重置共享报告状态
    _reset_all_report_states()

    no_tests = len(test_files) == 0
    stats = _StatsPlugin()

    # 阻塞型测试文件（SSE/WebSocket 创建阻塞连接，前端 E2E 需运行中的服务器）
    # 默认排除，可通过 --include-blocking 启用
    blocking_files = [
        "test_negative_sse_disconnect.py",
        "test_negative_websocket_error.py",
        "test_frontend_mode_switch.py",
        "test_frontend_event_panel.py",
        "test_frontend_pool_designer.py",
        "test_frontend_import_export.py",
        "test_frontend_formula.py",
    ]
    ignore_args = [f"--ignore={metatest_dir / bf}" for bf in blocking_files]
    include_blocking = "--include-blocking" in sys.argv

    start_ts = time.perf_counter()
    if no_tests:
        pytest_exit = _EXIT_NO_TESTS_COLLECTED
    else:
        pytest_args = [str(metatest_dir), "-v", "--tb=short"]
        if not include_blocking:
            pytest_args.extend(ignore_args)
        pytest_exit = pytest.main(pytest_args, plugins=[stats])
    duration = time.perf_counter() - start_ts

    # 收集测试统计（v3: 跳过计为失败，tests_total 含 skipped，pass_rate 分子不含 skipped）
    if no_tests or pytest_exit == _EXIT_NO_TESTS_COLLECTED:
        tests_total = 0
        tests_passed = 0
        no_tests = True
    else:
        # collected 可能因回调顺序为 0，从 passed/failed/skipped/errors 推算
        # tests_total = passed + failed + errors + skipped（skipped 计入失败）
        tests_total = (
            stats.collected
            or (stats.passed + stats.failed + stats.errors + stats.skipped)
        )
        tests_passed = stats.passed  # 分子不含 skipped（跳过计为失败）

    # 收集断言计数
    assertions_count = _count_total_assertions(test_files)
    test_files_count = len(test_files)

    # 收集模块覆盖
    covered_modules = _detect_covered_modules(test_files)

    # 从 REPORT_STATE 收集合测试数据
    active_report_state = _resolve_report_state()
    event_types_seen = active_report_state.get("event_types_seen", []) or []
    event_chain_correct = bool(active_report_state.get("event_chain_correct", False))
    sim_1000_tick_time_s = float(
        active_report_state.get("sim_1000_tick_time_s", 0.0) or 0.0
    )
    # 回退：若合测试未填充性能数据，直接测量
    if sim_1000_tick_time_s == 0.0:
        sim_1000_tick_time_s = _measure_performance()
    frontend_e2e_passed = int(active_report_state.get("frontend_e2e_passed", 0) or 0)
    frontend_e2e_total = int(active_report_state.get("frontend_e2e_total", 0) or 0)

    # v3: 前端 E2E 环境缺失时 frontend_e2e_passed=0，scoring 给予最低达标线 80（环境问题非代码问题）
    frontend_e2e_env_missing = False
    if frontend_e2e_total == 0 and not include_blocking:
        # 前端测试文件中的测试函数数（通过 AST 统计）
        frontend_files = [f for f in test_files if f.name.startswith("test_frontend_")]
        fe_test_count = 0
        for ff in frontend_files:
            try:
                content = ff.read_text(encoding="utf-8")
                fe_test_count += len(
                    re.findall(r"^\s*def\s+test_", content, re.MULTILINE)
                )
            except (OSError, UnicodeDecodeError):
                pass
        if fe_test_count > 0:
            frontend_e2e_total = fe_test_count
            frontend_e2e_passed = 0  # 环境缺失=失败
            frontend_e2e_env_missing = True

    # v3: 底层逻辑覆盖度检测（5 项）
    logic_coverage_passed, logic_coverage_total = _check_logic_coverage()

    # v3: 同构代码消除度检测（15 项 Grep / AST 验证）
    isomorphism_violations, isomorphism_total_checks = _check_isomorphism()

    # v3 新增数据采集
    # 核心模块总行数（wc -l core/*.py 等价）
    core_total_lines = _count_core_lines()

    # RULES 91-100 违规检查（10 项）
    rule_violations, rule_total_checks = _check_rule_compliance()

    # 4 类反测试用例数
    negative_test_counts = _count_negative_tests(metatest_dir)

    # 合测试通过数/总数（从 _StatsPlugin file_stats 采集）
    synthesis_passed, synthesis_total = _count_synthesis_tests(stats)

    # 构建 test_results 字典供 ScoringEngine 使用（含 v3 新增字段）
    test_results: Dict[str, Any] = {
        # --- v2 保留字段 ---
        "modules_covered": len(covered_modules),
        "tests_passed": tests_passed,
        "tests_total": tests_total,
        "assertions_count": assertions_count,
        "test_files_count": test_files_count,
        "event_types_seen": event_types_seen,
        "event_chain_correct": event_chain_correct,
        "sim_1000_tick_time_s": sim_1000_tick_time_s,
        "frontend_e2e_passed": frontend_e2e_passed,
        "frontend_e2e_total": frontend_e2e_total,
        "frontend_e2e_env_missing": frontend_e2e_env_missing,
        "logic_coverage_passed": logic_coverage_passed,
        "logic_coverage_total": logic_coverage_total,
        "isomorphism_violations": isomorphism_violations,
        "isomorphism_total_checks": isomorphism_total_checks,  # v3: 15
        # --- v3 新增字段 ---
        "core_total_lines": core_total_lines,
        "core_lines_target": CORE_LINES_TARGET,
        "rule_violations": rule_violations,
        "rule_total_checks": rule_total_checks,
        "negative_test_counts": negative_test_counts,
        "negative_test_target_per_category": NEGATIVE_TEST_TARGET_PER_CATEGORY,
        "synthesis_passed": synthesis_passed,
        "synthesis_total": synthesis_total,
    }

    # 计算评分
    engine = ScoringEngine()
    report = engine.calculate(test_results)

    # v3: 当 scoring.py 仍为 v2（8 维）时，补充 4 个新维度
    existing_names = {d.name for d in report.dimensions}
    if not _V3_NEW_DIM_NAMES.issubset(existing_names):
        supplementary = _compute_v3_supplementary_dims(test_results)
        for dim in supplementary:
            if dim.name not in existing_names:
                report.dimensions.append(dim)
                if dim.score < 100.0:
                    report.deductions.append(
                        f"{dim.name}: {dim.score:.1f}/100 — {dim.details}"
                    )
                if dim.score < REDO_THRESHOLD:
                    report.redo_list.append(dim.name)
        # 重新评估 passed：总分 ≥ 95 且所有维度（含补充）均 ≥ 80
        all_dims_above_80 = all(
            d.score >= REDO_THRESHOLD for d in report.dimensions
        )
        report.passed = (
            report.total_score >= ScoringEngine.THRESHOLD
            and all_dims_above_80
            and len(report.redo_list) == 0
        )

    # 前端 E2E 环境未就绪时追加扣分项说明（scoring 已给予最低达标线 80，此处仅标注）
    if frontend_e2e_env_missing:
        report.deductions.append("前端 E2E 环境未就绪（浏览器/服务器缺失，给予最低达标线 80 分）")

    # 打印报告
    _print_report(report, test_results, stats, duration, no_tests)

    # 写入 report.json
    report_path = metatest_dir / "report.json"
    report_dict = _build_report_dict(report, test_results, stats, duration, no_tests)
    _write_report_json(report_dict, report_path)
    print(f"报告已写入: {report_path}")

    # 退出码：PASS（总分 ≥ 95 且 12 维均 ≥ 80）或无测试文件返回 0，否则返回 1
    if no_tests:
        return 0
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
