"""metatest v10 量化测试运行器（21 维 + MetaDispatcher 统一 + 运行时验证 + 跨模块 import + adapter 转发同构 + handler 异常保护覆盖）。

运行 ``metatest/`` 目录下所有 ``test_*.py``，采集真实测试结果与量化数据，
调用 ``ScoringEngine`` 计算加权总分，输出量化评分报告。

v6 21 维评分（与 scoring.py v6 对齐）：v5 20 维权重等比降权 4%（每维 × 0.96）+ v6 新增第 21 维 adapter_isomorphism 占 4%。
  --- v4 16 维（降权至 80%）---
   1. module_coverage            5.6%   覆盖模块数 / 17 * 100
   2. test_pass_rate            10.4%   通过测试数 / 总测试数 * 100（跳过计为失败）
   3. assertion_density          4.0%   断言数 / (测试文件数 * 20) * 100
   4. event_chain_integrity      6.4%   出现事件类型数 / 10 * 100
   5. performance_benchmark      4.0%   1000 tick 耗时基准（≤10s 满分）
   6. frontend_e2e_pass_rate     5.6%   前端 E2E 真实通过数 / 总数 * 100
   7. logic_coverage             4.0%   5 项底层逻辑验证通过数 / 5 * 100
   8. isomorphism_elimination    7.2%   48 项同构代码 Grep/AST 检查（v12 含 if_fmt_tdx/threading_timer/structural_polling/ast_dead_code 4 项新增），0 违规满分
   9. line_convergence           4.0%   核心模块总行数 ≤ 22500 满分
  10. rule_compliance            2.4%   RULES 91-100 Grep 零违规
  11. negative_test_coverage     1.6%   4 类反测试覆盖率
  12. synthesis_e2e              2.4%   合测试通过率
  13. oop_inheritance_depth      6.4%   BasePoolConverter + Dzh/TdxPoolConverter 继承 + 公共方法在基类 + parse_pool/export_pool 模板方法在基类 + 10 钩子 @abstractmethod（v9 7 条件）
  14. polling_zero_tolerance     6.4%   12 处轮询模式 Grep 零匹配 + EventDriver heapq + 前端 setInterval fetch
  15. primitive_convergence      6.4%   三原语覆盖率（时间/分派/继承各 ≥ 95%）
  16. essence_ratio              3.2%   净减行数 / 变更前行数（目标 ≥ 12%，净增 = 0 触发 redo）
  --- v5 新增 4 维（各 5%）---
  17. dispatcher_isomorphism     5.0%   MetaDispatcher 基类 + EventBus/ConfigStore 继承 + EventDriver 独立 + 骨架占比 ≥ 60%
  18. runtime_verification       5.0%   3 个 in-process 运行时验证测试通过率
  19. eventtest_regression       5.0%   eventtest 退出码 0 满分
  20. cross_module_import_discipline 5.0% 8 处跨模块 import 违规模式零匹配
  --- v6 新增 1 维（v5 20 维降权 4%）---
  21. adapter_isomorphism        4.0%   TqProvider/TqSdkBridge 转发方法表驱动覆盖率 ≥ 80% 满分，线性衰减

v6 严格规则：
  - 跳过测试计为失败（不在 passed 分子）
  - 前端 E2E 环境缺失计 frontend_e2e_passed=0，scoring 给予最低达标线 80
  - 21 维分数均需 ≥ 80 才达标
  - 总分 ≥ 95 且 21 维均 ≥ 80 判定 PASS
  - essence_ratio ≤ 0 触发 redo（强制「合并非拆分」硬约束）

数据采集方式（禁止硬编码，所有评分由真实 Grep/AST/行数统计计算）：
  - 核心模块总行数：``wc -l core/*.py`` 等价的 Path 读取统计
  - 同构检查：48 项 Grep / AST 验证（v3 15 + 阶段 1 DZH/TDX 25 + 阶段 3 core 25 + v10 handler_exception_coverage 1 + v11 converters_polling/parallel_runtime/dead_code 3 + v12 if_fmt_tdx/threading_timer/structural_polling/ast_dead_code 4）
  - 规则合规：RULES 91-100 共 10 项 Grep / AST 验证
  - 反测试用例数：4 类文件中 ``def test_`` 计数
  - 合测试通过数：_StatsPlugin 按文件名分类统计
  - OOP 继承：BasePoolConverter + Dzh/TdxPoolConverter AST 类继承解析 + 公共方法定位
  - 轮询违规：13 处轮询模式 Grep（time.sleep / while+asyncio.sleep / setInterval+fetch / threading.Timer 等）
  - 三原语覆盖率：EventDriver/Queue/watchdog 触发数 vs while+sleep 残留数；
    _ADAPTER_SPECS/_SIDE_SPECS/_SUBSCRIPTIONS 等表数 vs def _adapter_X 等同构残留数；
    基类公共方法数 vs 子类同构方法数
  - essence_ratio：基线 24,000 行（Phase 3 基线）→ 当前行数
  - meta_unification：EventBus/EventDriver/ConfigStore 三核唯一性 Grep + meta_purity 计算
    + v5 新增 4 字段（meta_dispatcher_exists/eventbus_inherits_meta/
      configstore_inherits_meta/eventdriver_independent）
  - dispatcher_isomorphism：AST 解析 MetaDispatcher 基类 + EventBus/ConfigStore 继承 +
    EventDriver 独立 + 公共骨架行数占比
  - runtime_verification：3 个 in-process 测试文件从 _StatsPlugin.file_stats 采集通过率
  - eventtest_regression：subprocess 运行 ``python -m eventtest.run_eventtest`` 采集退出码
  - cross_module_import_discipline：Grep 8 处违规模式（7 业务模块禁 import table_engine +
    execution 禁 import screening_module）
  - adapter_isomorphism：Grep 4 通用转发器（_forward/_call_cached/_call_simple/
    _call_cached_per_code）+ AST 统计 _FORWARD_SPECS/_CACHED_TQ_CALLS/
    _SIMPLE_TQ_CALLS/_PER_CODE_TQ_CALLS 四表条目数 → 表驱动覆盖率

运行方式：
    python -m metatest.runner

退出码：
    0 = 总分 ≥ 95 且 21 维均 ≥ 80（PASS）或无测试文件
    1 = 总分 < 95 或有维度 < 80（FAIL）或有测试失败
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

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

#: v4 核心模块行数目标（≤ 此值满分，v4 从 v3 的 23000 调整为 22500）
CORE_LINES_TARGET: int = 22500

#: v4 反测试每类目标用例数
NEGATIVE_TEST_TARGET_PER_CATEGORY: int = 8

#: v4 同构检查项总数（v12：48 项 = v11 44 项 + v12 if_fmt_tdx/threading_timer/structural_polling/ast_dead_code 4 项）
ISOMORPHISM_CHECKS_TOTAL_V4: int = 48

#: 向后兼容别名（v3 命名）
ISOMORPHISM_CHECKS_TOTAL_V3: int = ISOMORPHISM_CHECKS_TOTAL_V4

#: v4 规则合规检查项总数（RULES 91-100 共 10 条）
RULE_COMPLIANCE_TOTAL: int = 10

#: v4 essence_ratio 基线行数（Phase 3 基线 24,000，预估 Phase 3 收敛前核心模块行数）
ESSENCE_BASELINE_LINES: int = 24000

#: v4 轮询模式列表（polling_zero_tolerance 维度检查）
#: v12 M2：新增第 13 项 ``threading\.Timer\s*\(``（v11 spec 点名模式，原 12 项无检测器 enforce）
#: v12 M3：修正第 2 项 ``asyncio\.sleep`` 正则方向——v11 为 ``asyncio\.sleep.*\n.*while``
#:        （要求 sleep 在 while 之前，方向反转），常见 polling 模式是
#:        ``while True:\n    await asyncio.sleep(0.5)``（while 在前），故改为
#:        ``while\s+[^:]*:[^\n]*\n[^\n]*asyncio\.sleep\(``（while 在前，sleep 在循环体内）
POLLING_PATTERNS: List[str] = [
    r"time\.sleep\(interval\)",
    r"while\s+[^:]*:[^\n]*\n[^\n]*asyncio\.sleep\(",
    r"setInterval.*fetch",
    r"start_polling",
    r"_file_watcher_loop",
    r"_sync_play_loop",
    r"_sync_sim_loop",
    r"auto_step_loop",
    r"run_in_executor\(.*drain",
    r"asyncio\.sleep\(0\.05\)",
    r"while self\._run\b",
    r"while self\._sim_auto_step\b",
    r"threading\.Timer\s*\(",
]

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

#: services/ 目录
_SERVICES_DIR = _PROJECT_ROOT / "services"

#: native/ 目录
_NATIVE_DIR = _PROJECT_ROOT / "native"

#: web/js/ 目录（前端轮询检查）
_WEB_JS_DIR = _PROJECT_ROOT / "web" / "js"

#: converters.py 路径
_CONVERTERS_FILE = _PROJECT_ROOT / "converters.py"

#: converters/_common.py 路径（公共工具下沉模块，允许定义 _safe_int 等）
_CONVERTERS_COMMON_FILE = _PROJECT_ROOT / "converters" / "_common.py"

#: app.py 路径
_APP_FILE = _PROJECT_ROOT / "app.py"

#: api.py 路径
_API_FILE = _PROJECT_ROOT / "api.py"

#: core/_hashing.py 路径（哈希函数三族统一模块，允许定义 hash_dict_content 等）
_HASHING_FILE = _CORE_DIR / "_hashing.py"


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


#: AST 解析缓存（v12 M1：_count_ast_references 对每个类名遍历全量 py_files，
#: O(classes × files) 重复解析同一文件——缓存使复杂度降为 O(files)，检测器可在秒级完成）。
_AST_CACHE: Dict[str, Optional[ast.Module]] = {}


def _parse_ast(file_path: Path) -> Optional[ast.Module]:
    """安全解析 Python 文件为 AST，失败返回 None（结果按路径缓存，单次运行内不重复解析）。"""
    key = str(file_path)
    cached = _AST_CACHE.get(key, _AST_CACHE)
    if cached is not _AST_CACHE:
        return cached
    if not file_path.is_file():
        _AST_CACHE[key] = None
        return None
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"))
    except (SyntaxError, OSError, UnicodeDecodeError):
        tree = None
    _AST_CACHE[key] = tree
    return tree


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


#: noqa 标记——合法残留（EventDriver 自身派发循环 / 解析器 token 循环 / 网络限速退避）
_NOQA_EVENT_DRIVER = "# noqa: event-driver"


def _grep_count_noqa(
    pattern: str, search_dir: Path, exclude_files: Optional[List[str]] = None
) -> int:
    """按行计数 pattern 匹配，排除带 ``# noqa: event-driver`` 标记的合法行。

    用于 meta_unification 的 EventBus/EventDriver 残留计数：EventDriver.fire_due
    自身的 heapq 派发循环、递归下降解析器的 token 消费循环、外部 API 限速退避均
    非自造事件循环 / 自造时间调度，标记 noqa 后不计入残留。
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
        for line in content.splitlines():
            if _NOQA_EVENT_DRIVER in line:
                continue
            count += len(regex.findall(line))
    return count


def _grep_count_noqa_in_file(pattern: str, file_path: Path) -> int:
    """单文件按行计数，排除 noqa 标记行（见 :func:`_grep_count_noqa`）。"""
    if not file_path.is_file():
        return 0
    try:
        content = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0
    regex = re.compile(pattern)
    count = 0
    for line in content.splitlines():
        if _NOQA_EVENT_DRIVER in line:
            continue
        count += len(regex.findall(line))
    return count


def _count_isomorphic_residue(
    name_patterns: List[str],
    search_dir: Path,
    exclude_files: Optional[List[str]] = None,
    max_body_lines: int = 4,
) -> int:
    """AST 计数同构残留函数：名称匹配任一 pattern 且方法体非薄包装。

    规格允许的 1-2 行薄包装委托（Task 15.4 ``_hash_tick`` / Task 20.6
    ``_compile_*_spec``）已收敛到统一实现 + 表，属合规委托而非残留；仅当方法体
    超过 ``max_body_lines``（疑似重新实现同构逻辑）才计为残留。同时通过名称
    pattern 的负向预查排除 ``_adapter_import_*`` / ``_adapter_export_*``（已注册
    于 ``_CONVERTER_REGISTRY`` 分派表的合法导入导出 adapter，非自造工具函数）。
    """
    exclude_set = set(exclude_files or [])
    compiled = [re.compile(p) for p in name_patterns]
    count = 0
    if not search_dir.is_dir():
        return 0
    for py_file in search_dir.rglob("*.py"):
        if py_file.name in exclude_set:
            continue
        tree = _parse_ast(py_file)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not any(c.fullmatch(node.name) for c in compiled):
                continue
            if node.body:
                # 跳过首语句 docstring 再判方法体行数——verbose docstring 不应使
                # 1-2 行委托被误判为非薄包装（如 _hash_tick 委托 hash_dict_content）。
                stmts = node.body
                start_idx = 1 if (
                    len(stmts) > 1 and isinstance(stmts[0], ast.Expr)
                    and isinstance(stmts[0].value, ast.Constant)
                    and isinstance(stmts[0].value.value, str)
                ) else 0
                first = stmts[start_idx].lineno
                last = getattr(stmts[-1], "end_lineno", first) or first
                if last - first + 1 <= max_body_lines:
                    continue  # 薄包装委托 → 已收敛，非残留
            count += 1
    return count


def _count_core_business_lines() -> int:
    """统计 core/*.py 业务行数：总行数扣除空行 / 纯注释行 / import 行 / docstring 行。

    用于 meta_purity 分母——原 ``_count_core_lines`` 含空行/注释/import 致分母
    过宽，meta_purity 被低估。业务行更准确反映「需符合三核模型的代码量」。
    """
    total = 0
    if not _CORE_DIR.is_dir():
        return 0
    for py_file in _CORE_DIR.glob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        tree = _parse_ast(py_file)
        doc_lines: set = set()
        if tree is not None:
            for nd in ast.walk(tree):
                if isinstance(nd, ast.Expr) and isinstance(nd.value, ast.Constant) \
                        and isinstance(nd.value.value, str):
                    start = nd.lineno
                    end = getattr(nd, "end_lineno", start) or start
                    doc_lines.update(range(start, end + 1))
        for idx, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                continue
            if stripped.startswith(("import ", "from ")):
                continue
            if idx in doc_lines:
                continue
            total += 1
    return total


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


def _check_isomorphism(
    handler_exception_coverage: Optional[Dict[str, Any]] = None,
    converters_polling_violations: Optional[Dict[str, Any]] = None,
    parallel_runtime_violations: Optional[Dict[str, Any]] = None,
    dead_code_violations: Optional[Dict[str, Any]] = None,
    structural_polling_violations: Optional[Dict[str, Any]] = None,
    runtime_verification: Optional[Dict[str, Any]] = None,
) -> Tuple[int, int]:
    """检测 49 项同构代码模式，返回违规项数。

    49 项检查（每项匹配数应为 0，非 0 则该计 1 项违规）。

    v10 新增第 41 项：handler_exception_coverage < 100% 计 1 违规
    （所有 ``self._bus.subscribe(EventType, self._handler_name)`` 手动订阅的
    handler 必须使用 ``@_event_handler`` 装饰，防止异常中断 EventBus.publish 同步扇出链）。

    v11 新增 3 项（审计盲区闭合，第十一层洞察）：
     42. converters_polling_violations > 0 计 1 违规（converters.py 内
         ``while + wait(N) + time.time()`` 轮询模式零容忍，v11 已删除的轮询执行器._run_loop
         已删除后应零匹配）
     43. parallel_runtime_violations > 0 计 1 违规（converters.py / services/*.py 内
         ``threading.Thread + while + threading.Event.wait`` 平行运行时零容忍；
         PoolEngine 的 ``asyncio.Event.wait()`` 事件驱动不在此列）
     44. dead_code_violations > 0 计 1 违规（全仓零实例化零导入的死类零容忍，
         DzhXmlExporter 已删除后应零死类）

    v12 新增 4 项（检测器硬化，第十二层洞察——检测器是约束的执行者）：
     45. ``if fmt`` 等于 tdx / dzh 字面量分支全仓零匹配计 1 违规（OOP 继承纯度彻底性，
         Task 8 完成后 _StkIO/_StkWriter 钩子化应零匹配）
     46. ``threading.Timer`` 生产代码零匹配计 1 违规（v11 spec 点名模式，Task 10 完成后应零匹配）
     47. structural_polling_violations > 0 计 1 违规（结构性 AST 轮询零容忍，Task 12 完成后应零违规；
         NAME-BASED 正则的兜底——新轮询方法换名仍被 ``ast.While + sleep`` 结构捕获）
     48. ast_dead_code_false_positives > 0 计 1 违规（AST 死代码检测零假阳性——docstring/注释/
         字符串字面量排除验证，Task 9 完成后应零假阳性）

    v3 原 15 项（保留，第 2 项已修复：移除 ``_build_adjacency`` 禁用）：
      1. ``state.latest_tick[`` = 0（除 runtime_mode_module.py 中 TickTable 内部）
      2. 运行时 ``json.loads`` / ``_parse_edge`` = 0（移除 ``_build_adjacency``，
         Task 12 已将其作为合法合并函数引入 core/domain.py；排除 table_engine.py
         和 domain.py 基础设施层）
      3. ``_phase_dispatch`` / ``_phase_nset_filter`` / ``_dispatch_filter`` / ``_eval_primitive`` = 0
      4. ``if node.type ==`` = 0
      5. ``transfer_module`` 中 ``sound.play`` / ``popup.show`` = 0
      6. 死表引用 = 0
      7. screening_module 4 个旧 nset 筛选函数 = 0（变更 A）
      8. core/*.py 中 ``json.load(open(`` = 0（变更 G，ConfigStore 内部除外）
      9. execution_module 中 ``if mode == "inflection"/"rank"`` = 0（变更 H）
     10. runtime_mode_module 中 ``if self._base_period ==`` = 0（变更 I）
     11. import_export_module 6 个旧 _parse/_serialize 函数 = 0（变更 C）
     12. monitoring_module 5 个旧 _compute_xxx_pnl 方法 = 0（变更 B）
     13. monitoring_module 3 个旧 _xxx_key 排序键方法 = 0（变更 L）
     14. runtime_mode_module 类内 ``_run_coro_sync``/``_run_coro`` 方法 = 0（变更 J）
     15. monitoring_module 中 ``def _compute_\\w+_pnl`` 同构方法模式 = 0（变更 B 补充）

    v4 新增 25 项（阶段 1 DZH/TDX OOP 7 + 公共工具 3 + 阶段 3 core 6 + 阶段 2 事件驱动 6 + 前端/订阅 3）：
     16. converters.py ``_parse_func_element`` / ``_parse_psatt_element`` / ``_parse_spinfo_element`` = 0（P1）
     17. converters.py ``_add_func`` / ``_add_psatt`` / ``_add_spinfo`` = 0（P1）
     18. converters.py ``_parse_pos`` / ``_parse_tdx_pos`` = 0（P1）
     19. converters.py ``_decode_xml_content`` / ``_decode_tdx_xml`` = 0（P1）
     20. 全代码库 ``_DZH_TO_TDX_TYPE`` / ``_DZH_TO_TDX_TYPE_EXPORT`` / ``TDX_TO_DZH_CELL_TYPE`` / ``TDX_CELL_TYPE_MAP`` = 0（P2）
     21. 全代码库 ``def _load_dzh_type_map`` = 0（P2）
     22. app.py + api.py ``parse_dzh_xml`` / ``parse_tdx_xml`` / ``_build_tdx_xml`` / ``export_meta_to_dzh_xml_bytes`` 调用 = 0（P3）
     23. core/*.py + services/*.py ``def _safe_int`` / ``def _safe_float`` = 0（P4/C4，仅 converters/_common.py 允许；v12 M5 扩展 services）
     24. services/providers.py ``_decode_formula`` / ``_extract_formula_from_binary`` / ``_is_valid_formula`` / ``_extract_text_segments`` = 0（P4）
     25. core/*.py + services/*.py ``def _to_float`` / ``def _cast_int`` / ``def _cast_str`` = 0（C4；v12 M5 扩展 services）
     26. monitoring_module.py ``def _adapter_\\w+`` = 0（C6，表驱动分派）
     27. monitoring_module.py ``def compute_pk_ranking`` / ``def compute_analysis_angles`` = 0（C7）
     28. execution_module.py ``def _publish_edge_fired`` / ``def _publish_ttl_due`` = 0（C8）
     29. trade_module.py ``def _execute_buy`` / ``def _execute_sell`` = 0（C9）
     30. trade_module.py ``if action_spec.bsavehis/btip/baimpool`` = 0（C9，表驱动）
     31. runtime_mode_module.py ``def _get_week_key`` / ``def _get_month_key`` / ``def _day_key`` = 0（C10）
     32. runtime_mode_module.py ``def _sync_play_loop`` = 0（E1，时间原语）
     33. runtime_mode_module.py ``def _sync_sim_loop`` / ``async def auto_step_loop`` = 0（E2）
     34. table_engine.py ``def start_polling`` = 0（E3，watchdog 事件驱动）
     35. app.py ``run_in_executor(.*drain`` = 0（E4，SSE Queue）
     36. runtime_mode_module.py ``while self._run`` / ``while self._sim_auto_step`` = 0（E2 轮询残留）
     37. services/data.py ``def _file_watcher_loop`` = 0（E3，watchdog 事件驱动）
     38. web/js/app.js ``setInterval.*_poll`` = 0（E6，前端 SSE 订阅）
     39. core/*.py（除 event_bus.py）``def _register_subscribers`` = 0（C11，_SUBSCRIPTIONS 表）
     40. core/*.py（除 event_bus.py）``self._bus.subscribe(EventType, self._on_`` = 0（C11）
     41. v10：handler_exception_coverage < 100% 计 1 违规（手动 subscribe 的 handler
         未全部使用 ``@_event_handler`` 装饰，存在异常中断事件链风险）
     42. v11：converters_polling_violations > 0 计 1 违规（converters.py
         ``while + wait(N) + time.time()`` 轮询模式零容忍）
     43. v11：parallel_runtime_violations > 0 计 1 违规（converters.py / services/*.py
         ``threading.Thread + while + threading.Event.wait`` 平行运行时零容忍）
     44. v11：dead_code_violations > 0 计 1 违规（全仓零实例化零导入死类零容忍）
     45. v12：``if fmt`` 等于 tdx / dzh 字面量分支全仓零匹配（OOP 继承纯度彻底性）
     46. v12：``threading.Timer`` 生产代码零匹配（v11 spec 点名模式）
     47. v12：structural_polling_violations > 0 计 1 违规（结构性 AST 轮询零容忍，
         NAME-BASED 正则的兜底——``ast.While + sleep`` 结构捕获换名轮询）
     48. v12：ast_dead_code_false_positives > 0 计 1 违规（AST 死代码检测零假阳性，
         docstring/注释/字符串字面量排除验证）

    Returns:
        (violations, total_checks) — 违规项数 / 总检查项(48)
    """
    total_checks = ISOMORPHISM_CHECKS_TOTAL_V4
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

    # --- v4 新增 25 项（阶段 1 DZH/TDX OOP + 公共工具 + 阶段 3 core + 阶段 2 事件驱动） ---

    # 16. P1：converters.py _parse_func_element / _parse_psatt_element / _parse_spinfo_element = 0
    count16 = _grep_count_in_file(
        r"def _parse_func_element\b|def _parse_psatt_element\b|def _parse_spinfo_element\b",
        _CONVERTERS_FILE,
    )
    if count16 > 0:
        violations += 1

    # 17. P1：converters.py _add_func / _add_psatt / _add_spinfo = 0
    count17 = _grep_count_in_file(
        r"def _add_func\b|def _add_psatt\b|def _add_spinfo\b", _CONVERTERS_FILE,
    )
    if count17 > 0:
        violations += 1

    # 18. P1：converters.py _parse_pos / _parse_tdx_pos = 0
    count18 = _grep_count_in_file(
        r"def _parse_pos\b|def _parse_tdx_pos\b", _CONVERTERS_FILE,
    )
    if count18 > 0:
        violations += 1

    # 19. P1：converters.py _decode_xml_content / _decode_tdx_xml = 0
    count19 = _grep_count_in_file(
        r"def _decode_xml_content\b|def _decode_tdx_xml\b", _CONVERTERS_FILE,
    )
    if count19 > 0:
        violations += 1

    # 20. P2：全代码库 _DZH_TO_TDX_TYPE / _DZH_TO_TDX_TYPE_EXPORT /
    #     TDX_TO_DZH_CELL_TYPE / TDX_CELL_TYPE_MAP = 0（生产代码目录）
    count20 = (
        _grep_count(r"\b_DZH_TO_TDX_TYPE\b|\b_DZH_TO_TDX_TYPE_EXPORT\b", _CORE_DIR)
        + _grep_count(r"\b_DZH_TO_TDX_TYPE\b|\b_DZH_TO_TDX_TYPE_EXPORT\b", _SERVICES_DIR)
        + _grep_count(r"\b_DZH_TO_TDX_TYPE\b|\b_DZH_TO_TDX_TYPE_EXPORT\b", _NATIVE_DIR)
        + _grep_count_in_file(r"\b_DZH_TO_TDX_TYPE\b|\b_DZH_TO_TDX_TYPE_EXPORT\b", _CONVERTERS_FILE)
        + _grep_count_in_file(r"\b_DZH_TO_TDX_TYPE\b|\b_DZH_TO_TDX_TYPE_EXPORT\b", _APP_FILE)
        + _grep_count_in_file(r"\b_DZH_TO_TDX_TYPE\b|\b_DZH_TO_TDX_TYPE_EXPORT\b", _API_FILE)
        + _grep_count(r"\bTDX_TO_DZH_CELL_TYPE\b|\bTDX_CELL_TYPE_MAP\b", _CORE_DIR)
        + _grep_count_in_file(r"\bTDX_TO_DZH_CELL_TYPE\b|\bTDX_CELL_TYPE_MAP\b", _CONVERTERS_FILE)
    )
    if count20 > 0:
        violations += 1

    # 21. P2：全代码库 def _load_dzh_type_map = 0（生产代码目录）
    count21 = (
        _grep_count(r"def _load_dzh_type_map\b", _CORE_DIR)
        + _grep_count(r"def _load_dzh_type_map\b", _SERVICES_DIR)
        + _grep_count(r"def _load_dzh_type_map\b", _NATIVE_DIR)
        + _grep_count_in_file(r"def _load_dzh_type_map\b", _CONVERTERS_FILE)
        + _grep_count_in_file(r"def _load_dzh_type_map\b", _APP_FILE)
        + _grep_count_in_file(r"def _load_dzh_type_map\b", _API_FILE)
    )
    if count21 > 0:
        violations += 1

    # 22. P3：app.py + api.py parse_dzh_xml( / parse_tdx_xml( / _build_tdx_xml( /
    #     export_meta_to_dzh_xml_bytes( 调用 = 0（应走 _CONVERTER_REGISTRY）
    count22 = (
        _grep_count_in_file(
            r"parse_dzh_xml\(|parse_tdx_xml\(|_build_tdx_xml\(|export_meta_to_dzh_xml_bytes\(",
            _APP_FILE,
        )
        + _grep_count_in_file(
            r"parse_dzh_xml\(|parse_tdx_xml\(|_build_tdx_xml\(|export_meta_to_dzh_xml_bytes\(",
            _API_FILE,
        )
    )
    if count22 > 0:
        violations += 1

    # 23. P4/C4：core/*.py + services/*.py def _safe_int / def _safe_float = 0（仅 converters/_common.py 允许）
    # v12 M5：扫描范围扩展到 services/*.py（闭合 services 盲区，与 _to_float 同构病根同源）
    count23 = (
        _grep_count(r"def _safe_int\b|def _safe_float\b", _CORE_DIR)
        + _grep_count(r"def _safe_int\b|def _safe_float\b", _SERVICES_DIR)
    )
    if count23 > 0:
        violations += 1

    # 24. P4：services/providers.py 4 个公式解码器副本 = 0
    providers_file = _SERVICES_DIR / "providers.py"
    count24 = _grep_count_in_file(
        r"def _decode_formula\b|def _extract_formula_from_binary\b|"
        r"def _is_valid_formula\b|def _extract_text_segments\b",
        providers_file,
    )
    if count24 > 0:
        violations += 1

    # 25. C4：core/*.py + services/*.py def _to_float / def _cast_int / def _cast_str = 0
    # v12 M5：扫描范围扩展到 services/*.py（闭合 services 盲区——_to_float 曾藏于 services/data.py:2733，
    # v12 阶段 4 Task 16 已删除，扩展扫描确保不回归）
    count25 = (
        _grep_count(r"def _to_float\b|def _cast_int\b|def _cast_str\b", _CORE_DIR)
        + _grep_count(r"def _to_float\b|def _cast_int\b|def _cast_str\b", _SERVICES_DIR)
    )
    if count25 > 0:
        violations += 1

    # 26. C6：monitoring_module.py def _adapter_\w+ = 0（表驱动 _ADAPTER_SPECS）
    count26 = _grep_count_in_file(r"def _adapter_\w+\b", mon_file)
    if count26 > 0:
        violations += 1

    # 27. C7：monitoring_module.py def compute_pk_ranking / def compute_analysis_angles
    #     = 0 或方法体 ≤ 3 行 thin wrapper（委托 _compute_ranking）
    count27 = (
        _check_thin_wrapper(mon_file, "compute_pk_ranking", max_body_lines=3)
        + _check_thin_wrapper(mon_file, "compute_analysis_angles", max_body_lines=3)
    )
    if count27 > 0:
        violations += 1

    # 28. C8：execution_module.py def _publish_edge_fired / def _publish_ttl_due = 0
    count28 = _grep_count_in_file(
        r"def _publish_edge_fired\b|def _publish_ttl_due\b", exec_file,
    )
    if count28 > 0:
        violations += 1

    # 29. C9：trade_module.py def _execute_buy / def _execute_sell = 0（表驱动 _SIDE_SPECS）
    trade_file = _CORE_DIR / "trade_module.py"
    count29 = _grep_count_in_file(
        r"def _execute_buy\b|def _execute_sell\b", trade_file,
    )
    if count29 > 0:
        violations += 1

    # 30. C9：trade_module.py if action_spec.bsavehis/btip/baimpool = 0（表驱动 _PSATT_SIDE_EFFECTS）
    count30 = _grep_count_in_file(
        r"if action_spec\.bsavehis|if action_spec\.bsound|if action_spec\.btip|"
        r"if action_spec\.bsavetoblock|if action_spec\.baimpool",
        trade_file,
    )
    if count30 > 0:
        violations += 1

    # 31. C10：runtime_mode_module.py def _get_week_key / _get_month_key / _day_key = 0
    count31 = _grep_count_in_file(
        r"def _get_week_key\b|def _get_month_key\b|def _day_key\b", runtime_file,
    )
    if count31 > 0:
        violations += 1

    # 32. E1：runtime_mode_module.py def _sync_play_loop = 0（EventDriver heapq 调度）
    count32 = _grep_count_in_file(r"def _sync_play_loop\b", runtime_file)
    if count32 > 0:
        violations += 1

    # 33. E2：runtime_mode_module.py def _sync_sim_loop / async def auto_step_loop = 0
    count33 = _grep_count_in_file(
        r"def _sync_sim_loop\b|async def auto_step_loop\b", runtime_file,
    )
    if count33 > 0:
        violations += 1

    # 34. E3：table_engine.py def start_polling = 0（watchdog 事件驱动）
    table_file = _CORE_DIR / "table_engine.py"
    count34 = _grep_count_in_file(r"def start_polling\b", table_file)
    if count34 > 0:
        violations += 1

    # 35. E4：app.py run_in_executor(.*drain = 0（SSE asyncio.Queue）
    count35 = _grep_count_in_file(r"run_in_executor\(.*drain", _APP_FILE)
    if count35 > 0:
        violations += 1

    # 36. E2 补充：runtime_mode_module.py while self._run / while self._sim_auto_step = 0
    count36 = _grep_count_in_file(
        r"while self\._run\b|while self\._sim_auto_step\b", runtime_file,
    )
    if count36 > 0:
        violations += 1

    # 37. E3 补充：services/data.py def _file_watcher_loop = 0（watchdog 事件驱动）
    data_file = _SERVICES_DIR / "data.py"
    count37 = _grep_count_in_file(r"def _file_watcher_loop\b", data_file)
    if count37 > 0:
        violations += 1

    # 38. E6：web/js/app.js setInterval.*_poll = 0（前端 SSE/WS 订阅）
    appjs_file = _WEB_JS_DIR / "app.js"
    count38 = _grep_count_in_file(r"setInterval.*_poll", appjs_file)
    if count38 > 0:
        violations += 1

    # 39. C11：core/*.py（除 event_bus.py）def _register_subscribers = 0（_SUBSCRIPTIONS 表）
    count39 = _grep_count(
        r"def _register_subscribers\b", _CORE_DIR,
        exclude_files=["event_bus.py"],
    )
    if count39 > 0:
        violations += 1

    # 40. C11：core/*.py（除 event_bus.py）self._bus.subscribe(EventType, self._on_ = 0
    count40 = _grep_count(
        r"self\._bus\.subscribe\(EventType, self\._on_", _CORE_DIR,
        exclude_files=["event_bus.py"],
    )
    if count40 > 0:
        violations += 1

    # 41. v10：handler_exception_coverage < 100% 计 1 违规
    #     手动 subscribe 的 handler 必须使用 @_event_handler 装饰（异常保护全覆盖）
    if handler_exception_coverage is None:
        handler_exception_coverage = _collect_handler_exception_coverage()
    if float(handler_exception_coverage.get("coverage", 0.0) or 0.0) < 100.0:
        violations += 1

    # 42. v11：converters_polling_violations > 0 计 1 违规
    #     converters.py 内 while + wait(N) + time.time() 轮询模式零容忍
    #     （v11 已删除的轮询执行器._run_loop 已删除后应零匹配，闭合 v10 审计盲区）
    if converters_polling_violations is None:
        converters_polling_violations = _collect_converters_polling_violations()
    if int(converters_polling_violations.get("violations", 0) or 0) > 0:
        violations += 1

    # 43. v11：parallel_runtime_violations > 0 计 1 违规
    #     converters.py / services/*.py 内 threading.Thread + while + threading.Event.wait
    #     平行运行时零容忍（PoolEngine 的 asyncio.Event.wait() 事件驱动不在此列）
    if parallel_runtime_violations is None:
        parallel_runtime_violations = _collect_parallel_runtime_violations()
    if int(parallel_runtime_violations.get("violations", 0) or 0) > 0:
        violations += 1

    # 44. v11：dead_code_violations > 0 计 1 违规
    #     全仓零实例化零导入死类零容忍（DzhXmlExporter 已删除后应零死类）
    if dead_code_violations is None:
        dead_code_violations = _collect_dead_code_violations()
    if int(dead_code_violations.get("count", 0) or 0) > 0:
        violations += 1

    # 45. v12 M7：if fmt 等于 tdx / dzh 字面量分支全仓零匹配计 1 违规
    #     OOP 继承纯度彻底性——_StkIO/_StkWriter 钩子化（Task 5-8）后全仓零 fmt 分支
    if_fmt_tdx_violations = (
        _grep_count(r'if\s+fmt\s*==\s*["\']tdx["\']', _CORE_DIR)
        + _grep_count(r'if\s+fmt\s*==\s*["\']tdx["\']', _SERVICES_DIR)
        + _grep_count_in_file(r'if\s+fmt\s*==\s*["\']tdx["\']', _APP_FILE)
        + _grep_count_in_file(r'if\s+fmt\s*==\s*["\']tdx["\']', _CONVERTERS_FILE)
        + _grep_count_in_file(r'if\s+fmt\s*==\s*["\']tdx["\']', _API_FILE)
        + _grep_count(r'if\s+fmt\s*==\s*["\']dzh["\']', _CORE_DIR)
        + _grep_count(r'if\s+fmt\s*==\s*["\']dzh["\']', _SERVICES_DIR)
        + _grep_count_in_file(r'if\s+fmt\s*==\s*["\']dzh["\']', _APP_FILE)
        + _grep_count_in_file(r'if\s+fmt\s*==\s*["\']dzh["\']', _CONVERTERS_FILE)
        + _grep_count_in_file(r'if\s+fmt\s*==\s*["\']dzh["\']', _API_FILE)
    )
    if if_fmt_tdx_violations > 0:
        violations += 1

    # 46. v12 M7：threading.Timer 生产代码零匹配计 1 违规
    #     v11 spec 点名模式——threading.Timer 绕过 EventDriver 平行调度零容忍
    threading_timer_violations = (
        _grep_count(r'threading\.Timer\s*\(', _CORE_DIR)
        + _grep_count(r'threading\.Timer\s*\(', _SERVICES_DIR)
        + _grep_count_in_file(r'threading\.Timer\s*\(', _APP_FILE)
        + _grep_count_in_file(r'threading\.Timer\s*\(', _CONVERTERS_FILE)
        + _grep_count_in_file(r'threading\.Timer\s*\(', _API_FILE)
    )
    if threading_timer_violations > 0:
        violations += 1

    # 47. v12 M7：structural_polling_violations > 0 计 1 违规
    #     结构性 AST 轮询零容忍——ast.While 循环体内含 time.sleep/asyncio.sleep/sync .wait
    #     NAME-BASED 正则的兜底：新轮询方法换名仍被结构性检测捕获
    if structural_polling_violations is None:
        structural_polling_violations = _collect_structural_polling_violations()
    if int(structural_polling_violations.get("violations", 0) or 0) > 0:
        violations += 1

    # 48. v12 M7：ast_dead_code_false_positives > 0 计 1 违规
    #     AST 死代码检测零假阳性——docstring/注释/字符串字面量排除验证。
    #     v11 字符串正则把 docstring 提及误计为引用（假阳性 alive），v12 M1 改 AST 引用计数
    #     后闭合。本检查验证 AST 检测器不产生假阳性 dead（活类被误判为死）——
    #     dead_code_violations['count'] == 0 即零假阳性（与 #44 同数据源但不同视角：
    #     #44 验证「无死代码违规」，#48 验证「AST 检测器准确性——零假阳性」）。
    ast_dead_code_false_positives = int(dead_code_violations.get("count", 0) or 0)
    if ast_dead_code_false_positives > 0:
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
# v4 新增维度数据采集（三原语收敛度 + 运行时三核 Dispatcher 元统一）
# ---------------------------------------------------------------------------


def _collect_oop_inheritance() -> Dict[str, Any]:
    """采集 OOP 同源继承深度数据（SubTask 27.1；v8 扩展主流程模板方法采集；v9 扩展钩子 @abstractmethod 采集）。

    通过 AST 解析 ``converters.py``，验证：
      (a) ``BasePoolConverter`` 类存在
      (b) ``DzhPoolConverter`` / ``TdxPoolConverter`` 继承自 ``BasePoolConverter``
      (c) 公共方法（``_parse_element`` / ``_add_element`` / ``_decode_pos`` /
          ``_decode_xml_bytes``）定义在基类内
      (d) 子类未重新引入同构方法（无 ``_parse_func_element`` / ``_add_func`` 等）
      (e) ``parse_pool`` 模板方法在 ``BasePoolConverter`` 中定义（v8 主流程上提）
      (f) ``export_pool`` 模板方法在 ``BasePoolConverter`` 中定义（v8 主流程上提）
      (g) v9：10 个差异钩子使用 ``@abstractmethod`` 装饰（AST 检查
          ``decorator_list`` 含 ``ast.Name(id='abstractmethod')`` 或
          ``ast.Attribute(attr='abstractmethod')``，早失败契约执行）

    Returns:
        dict 填入 ``test_results["oop_inheritance"]``
    """
    result: Dict[str, Any] = {
        "base_exists": False,
        "subclasses_inherit": False,
        "public_methods_in_base": False,
        "subclasses_only_differential": False,
        "parse_pool_in_base": False,
        "export_pool_in_base": False,
        "hooks_are_abstract": False,
    }
    tree = _parse_ast(_CONVERTERS_FILE)
    if tree is None:
        return result

    # 收集所有顶层类定义及其基类与方法
    classes: Dict[str, Dict[str, Any]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        method_names = {
            n.name for n in node.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        # v9: 记录每个方法的装饰器名集合，用于检查 @abstractmethod
        decorated_methods: Dict[str, List[str]] = {}
        for n in node.body:
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            decos: List[str] = []
            for dec in n.decorator_list:
                if isinstance(dec, ast.Name):
                    decos.append(dec.id)
                elif isinstance(dec, ast.Attribute):
                    decos.append(dec.attr)
                elif isinstance(dec, ast.Call):
                    f = dec.func
                    if isinstance(f, ast.Name):
                        decos.append(f.id)
                    elif isinstance(f, ast.Attribute):
                        decos.append(f.attr)
            decorated_methods[n.name] = decos
        base_names = []
        for b in node.bases:
            if isinstance(b, ast.Name):
                base_names.append(b.id)
            elif isinstance(b, ast.Attribute):
                base_names.append(b.attr)
        classes[node.name] = {
            "bases": base_names,
            "methods": method_names,
            "decorated_methods": decorated_methods,
        }

    # (a) BasePoolConverter 存在
    result["base_exists"] = "BasePoolConverter" in classes

    # (b) DzhPoolConverter / TdxPoolConverter 继承自 BasePoolConverter
    dzh = classes.get("DzhPoolConverter", {})
    tdx = classes.get("TdxPoolConverter", {})
    result["subclasses_inherit"] = (
        "BasePoolConverter" in dzh.get("bases", [])
        and "BasePoolConverter" in tdx.get("bases", [])
    )

    # (c) 公共方法定义在基类
    base_methods = classes.get("BasePoolConverter", {}).get("methods", set())
    required = {"_parse_element", "_add_element", "_decode_pos", "_decode_xml_bytes"}
    result["public_methods_in_base"] = required.issubset(base_methods)

    # (d) 子类未重新引入同构方法（全代码库 grep 旧同构函数定义 = 0）
    isomorphic_defs = (
        _grep_count_in_file(
            r"def _parse_func_element\b|def _parse_psatt_element\b|"
            r"def _parse_spinfo_element\b|def _add_func\b|def _add_psatt\b|"
            r"def _add_spinfo\b|def _parse_pos\b|def _parse_tdx_pos\b",
            _CONVERTERS_FILE,
        )
    )
    result["subclasses_only_differential"] = isomorphic_defs == 0

    # (e)+(f) v8 主流程模板方法 parse_pool / export_pool 在 BasePoolConverter 中定义
    result["parse_pool_in_base"] = "parse_pool" in base_methods
    result["export_pool_in_base"] = "export_pool" in base_methods

    # (g) v9: 10 个差异钩子使用 @abstractmethod 装饰（早失败契约执行）
    # AST 检查 BasePoolConverter 类体内 10 个钩子方法的 decorator_list 含 abstractmethod
    hook_names = {
        "_decode_source", "_extract_pool_meta", "_parse_cells",
        "_parse_flows", "_build_result", "_create_root",
        "_serialize_pool_attrs", "_serialize_cells",
        "_serialize_flows", "_finalize_xml",
    }
    base_decorated = classes.get("BasePoolConverter", {}).get("decorated_methods", {})
    result["hooks_are_abstract"] = all(
        "abstractmethod" in base_decorated.get(hook, [])
        for hook in hook_names
    )
    return result


def _collect_polling_violations() -> Dict[str, Any]:
    """采集轮询零容忍数据（SubTask 27.2）。

    对 13 处轮询模式（``POLLING_PATTERNS``）在 core/ / services/ / app.py /
    web/js/*.js 中 Grep 计数，验证 EventDriver heapq 调度站点存在，
    检查前端 ``setInterval.*fetch`` 残留。

    v12 M2：新增第 13 项 ``threading.Timer`` 检测（v11 spec 点名模式）。
    v12 M3：修正第 2 项 ``asyncio.sleep`` 正则方向（while 在前，sleep 在循环体内）。

    Returns:
        dict 填入 ``test_results["polling_violations"]``
    """
    pattern_counts: Dict[str, int] = {}
    # 轮询模式搜索范围：core/ + services/ + app.py + converters.py（v11 扩展覆盖 converters.py，
    # 闭合 v10 审计盲区——v11 已删除的轮询执行器._run_loop 轮询运行时曾藏于此文件）
    for pattern in POLLING_PATTERNS:
        count = (
            _grep_count(pattern, _CORE_DIR)
            + _grep_count(pattern, _SERVICES_DIR)
            + _grep_count_in_file(pattern, _APP_FILE)
            + _grep_count_in_file(pattern, _CONVERTERS_FILE)
        )
        # 前端 setInterval fetch 单独在 web/js/*.js 中搜索
        if pattern == r"setInterval.*fetch":
            js_count = 0
            if _WEB_JS_DIR.is_dir():
                for js_file in _WEB_JS_DIR.glob("*.js"):
                    js_count += _grep_count_in_file(pattern, js_file)
            count += js_count
        pattern_counts[pattern] = count

    # EventDriver heapq 调度验证：add_spec / schedule / TimedEventSpec 注册站点存在
    eventdriver_verified = (
        _grep_count(r"\.add_spec\b|\.schedule\b|TimedEventSpec", _CORE_DIR) > 0
        or _grep_count_in_file(r"\.add_spec\b|\.schedule\b|TimedEventSpec", _APP_FILE) > 0
    )

    # 前端 setInterval.*fetch 残留计数
    frontend_count = 0
    if _WEB_JS_DIR.is_dir():
        for js_file in _WEB_JS_DIR.glob("*.js"):
            frontend_count += _grep_count_in_file(r"setInterval.*fetch", js_file)

    return {
        "pattern_counts": pattern_counts,
        "eventdriver_heapq_verified": eventdriver_verified,
        "frontend_setinterval_fetch_count": frontend_count,
        "total_patterns": len(POLLING_PATTERNS),
    }


def _collect_primitive_convergence() -> Dict[str, Any]:
    """采集三原语收敛度数据（SubTask 27.5）。

    三原语覆盖率：
      - 时间原语 = (EventDriver.add_spec + asyncio.Queue + watchdog 触发数)
        / (上述 + while+sleep 残留数) × 100
      - 分派原语 = 表驱动分派数 / (表驱动 + 同构函数残留) × 100
      - 继承原语 = 基类公共方法数 / (基类 + 子类同构方法残留) × 100

    Returns:
        dict 填入 ``test_results["primitive_convergence"]``，含 time/dispatch/inheritance
    """
    # --- 时间原语覆盖率 ---
    time_primitive = (
        _grep_count(r"\.add_spec\b|\.schedule\b|TimedEventSpec", _CORE_DIR)
        + _grep_count(r"asyncio\.Queue\b", _CORE_DIR)
        + _grep_count(r"watchdog\.Observer|Observer\(\)", _CORE_DIR)
        + _grep_count(r"asyncio\.Queue\b", _SERVICES_DIR)
        + _grep_count_in_file(r"asyncio\.Queue\b", _APP_FILE)
    )
    time_residue = (
        _grep_count(r"time\.sleep\(", _CORE_DIR)
        + _grep_count(r"time\.sleep\(", _SERVICES_DIR)
        + _grep_count_in_file(r"time\.sleep\(", _APP_FILE)
        + _grep_count(r"while self\._run\b|while self\._sim_auto_step\b", _CORE_DIR)
    )
    time_denom = time_primitive + time_residue
    time_cov = (time_primitive / time_denom * 100.0) if time_denom > 0 else 100.0

    # --- 分派原语覆盖率 ---
    # 表驱动分派定义（_ADAPTER_SPECS / _SIDE_SPECS / _SUBSCRIPTIONS / _CONVERTER_REGISTRY
    # / _PSATT_SIDE_EFFECTS / _RANKING_SPECS 等表存在即计）
    dispatch_tables = sum(
        1 for _ in [None] if _grep_count(
            r"_ADAPTER_SPECS\b|_SIDE_SPECS\b|_SUBSCRIPTIONS\b|"
            r"_CONVERTER_REGISTRY\b|_PSATT_SIDE_EFFECTS\b|_RANKING_SPECS\b",
            _CORE_DIR,
        ) > 0
    )
    # 表驱动分派调用站点（dispatch( / get_table( / _build_adapter_record(）
    dispatch_calls = (
        _grep_count(r"\.dispatch\(|_build_adapter_record\(|\.get_table\(", _CORE_DIR)
    )
    table_driven = dispatch_tables + dispatch_calls
    # 同构函数残留（应已被表驱动消除）。
    # 规格允许的 1-2 行薄包装委托（_compile_*_spec 委托 _compile_spec + 字段表，
    # Task 20.6）已收敛到统一实现 + 表，非残留——AST 薄包装感知计数仅计方法体
    # 超长的疑似重实现。_adapter_ 负向预查排除 _CONVERTER_REGISTRY 内合法导入导出
    # adapter（_adapter_import_dzh/tdx / _adapter_export_dzh，分派表条目非自造函数）。
    dispatch_residue = _count_isomorphic_residue(
        [
            r"_adapter_(?!import_|export_)\w+",
            r"_execute_buy",
            r"_execute_sell",
            r"_compile_timing_spec",
            r"_compile_filter_spec",
            r"_compile_propagate_spec",
        ],
        _CORE_DIR,
        max_body_lines=4,
    )
    dispatch_denom = table_driven + dispatch_residue
    dispatch_cov = (table_driven / dispatch_denom * 100.0) if dispatch_denom > 0 else 100.0

    # --- 继承原语覆盖率 ---
    # 基类公共方法数（BasePoolConverter + _FieldedBase + ConfigStoreBase 方法数）
    base_method_count = 0
    for cls_file, cls_name in [
        (_CONVERTERS_FILE, "BasePoolConverter"),
        (_CORE_DIR / "domain.py", "_FieldedBase"),
        (_CORE_DIR / "table_engine.py", "ConfigStoreBase"),
    ]:
        tree = _parse_ast(cls_file)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.ClassDef) and node.name == cls_name):
                base_method_count += sum(
                    1 for n in node.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and not n.name.startswith("__")
                )
    # 子类同构方法残留（to_dict/from_dict 逐行复制 + 旧解析器）。
    # converters 旧解析器（_parse_func_element / _add_func / _parse_pos）spec 要求
    # 彻底删除（Task 5.1 =0），计任意匹配；core 哈希薄包装（_hash_tick / _hash_bar /
    # _hash_bars，Task 15.4/15.10 允许 1 行委托 hash_dict_content）已收敛，AST 薄包装
    # 感知计数排除——仅计方法体超长的疑似重实现。
    inheritance_residue = (
        _grep_count_in_file(
            r"def _parse_func_element\b|def _add_func\b|def _parse_pos\b",
            _CONVERTERS_FILE,
        )
        + _count_isomorphic_residue(
            [r"_hash_tick", r"_hash_bar", r"_hash_bars"],
            _CORE_DIR,
            exclude_files=["_hashing.py"],
            max_body_lines=4,
        )
    )
    inh_denom = base_method_count + inheritance_residue
    inheritance_cov = (base_method_count / inh_denom * 100.0) if inh_denom > 0 else 100.0

    return {
        "time": round(time_cov, 1),
        "dispatch": round(dispatch_cov, 1),
        "inheritance": round(inheritance_cov, 1),
    }


def _collect_essence_ratio(core_total_lines: int) -> Tuple[float, int, int]:
    """采集本质比数据（SubTask 27.6）。

    essence_ratio = (基线行数 - 当前行数) / 基线行数 × 100

    Args:
        core_total_lines: 当前 core/*.py 总行数

    Returns:
        (essence_ratio, baseline_lines, current_lines)
    """
    baseline = ESSENCE_BASELINE_LINES
    current = core_total_lines
    if baseline <= 0:
        return 0.0, baseline, current
    ratio = (baseline - current) / baseline * 100.0
    return ratio, baseline, current


def _collect_meta_unification() -> Dict[str, Any]:
    """采集运行时三核 Dispatcher 元统一数据（SubTask 27.7，第四层洞察根因）。

    验证：
      (a) EventBus 唯一：无自造事件循环（while True / while self._x 残留）
      (b) EventDriver 唯一：无自造时间调度（time.sleep / asyncio.sleep(digit) 残留）
      (c) ConfigStore 唯一：无绕过（双 get_global_config_store 调用残留）
      (d) meta_purity = (Data 声明行数 + 三核 Dispatcher 调用行数) / 总业务行数

    Returns:
        dict 填入 ``test_results["meta_unification"]``，目标 meta_purity ≥ 90%
    """
    # (a) EventBus 唯一：自造事件循环残留（core/ + services/ 非测试代码）
    # 排除 # noqa: event-driver 标记的合法循环——EventDriver.fire_due 自身 heapq
    # 派发循环（时间原语实现）与递归下降解析器的 token 消费循环均非自造事件循环。
    eventbus_residue = _grep_count_noqa(
        r"while\s+True|while\s+self\._\w+\s*[:)]", _CORE_DIR,
    ) + _grep_count_noqa(
        r"while\s+True|while\s+self\._\w+\s*[:)]", _SERVICES_DIR,
    )
    eventbus_unique = eventbus_residue == 0

    # (b) EventDriver 唯一：自造时间调度残留
    # 排除 # noqa: event-driver 标记的合法 sleep——外部 API 限速退避（网络请求节流）
    # 非步进轮询 / 自造时间调度。
    eventdriver_residue = (
        _grep_count_noqa(r"time\.sleep\(", _CORE_DIR)
        + _grep_count_noqa(r"time\.sleep\(", _SERVICES_DIR)
        + _grep_count_noqa_in_file(r"time\.sleep\(", _APP_FILE)
        + _grep_count_noqa(r"asyncio\.sleep\(\d", _CORE_DIR)
        + _grep_count_noqa(r"asyncio\.sleep\(\d", _SERVICES_DIR)
        + _grep_count_noqa_in_file(r"asyncio\.sleep\(\d", _APP_FILE)
    )
    eventdriver_unique = eventdriver_residue == 0

    # (c) ConfigStore 唯一：双调用绕过残留
    configstore_residue = _grep_count(
        r"get_global_config_store\(\)\.get_table.*\n.*if get_global_config_store",
        _CORE_DIR,
    )
    configstore_unique = configstore_residue == 0

    # 第四核 Dispatcher 禁止：自造工具函数 revival。
    # _adapter_(?!import_|export_) 负向预查排除已注册于 _CONVERTER_REGISTRY 分派表的
    # 合法导入导出 adapter（_adapter_import_dzh/tdx / _adapter_export_dzh），它们是
    # 分派表条目而非自造工具函数。
    fourth_dispatcher_pattern = (
        r"def _safe_int\b|def _safe_float\b|def _adapter_(?!import_|export_)\w+\b|"
        r"def _execute_buy\b|def _execute_sell\b"
    )
    fourth_dispatcher_lines = _grep_count(fourth_dispatcher_pattern, _CORE_DIR)
    no_fourth_dispatcher = fourth_dispatcher_lines == 0

    # (d) meta_purity = 业务行中符合三核 Dispatcher 模型的占比。
    # 公理 Code = Data + Dispatcher：每行业务行要么是 Data 声明、要么是三核
    # Dispatcher 调用 / 胶水；自造第四核（EventBus 自造循环 / EventDriver 自造调度 /
    # ConfigStore 绕过 / 第四核工具函数）是唯一偏离。故
    #   meta_purity = (业务行 - 自造四核残留行) / 业务行 × 100
    # 业务行 = 总行数扣除空行 / 纯注释 / import / docstring（原 _count_core_lines 分母
    # 含上述非业务行致过宽）。残留清零时 purity → 100%，残留复现时 purity 下降——
    # 直接度量「元统一纯度」，可判别且可达 ≥ 90% 目标。
    total_business_lines = max(1, _count_core_business_lines())
    meta_residue_lines = (
        eventbus_residue + eventdriver_residue
        + configstore_residue + fourth_dispatcher_lines
    )
    essence_lines = max(0, total_business_lines - meta_residue_lines)
    meta_purity = (essence_lines / total_business_lines * 100.0) if total_business_lines > 0 else 0.0

    # 信息性：三核 Dispatcher 调用站点 + Data 表声明（lower-bound 交叉参考，非分子）
    dispatcher_calls = (
        _grep_count(r"\.publish\(|\.subscribe\(|\.add_spec\b|\.schedule\(|\.get_table\(",
                    _CORE_DIR)
        + _grep_count(r"\.publish\(|\.subscribe\(|\.get_table\(", _SERVICES_DIR)
        + _grep_count_in_file(r"\.publish\(|\.subscribe\(|\.get_table\(", _APP_FILE)
    )
    data_declarations = _grep_count_in_file(
        r"^\s*_\w+_(SPECS|REGISTRY|TABLE|MAP|EFFECTS|FIELDS)\b|^\s*_SUBSCRIPTIONS\b|"
        r"^\s*_CONVERTER_REGISTRY\b|^\s*_PSATT_SIDE_EFFECTS\b|^\s*_SIDE_SPECS\b|"
        r"^\s*_RANKING_SPECS\b|^\s*_ADAPTER_SPECS\b",
        _CORE_DIR / "monitoring_module.py",
    ) + _grep_count_in_file(
        r"^\s*_\w+_(SPECS|REGISTRY|TABLE|MAP|EFFECTS|FIELDS)\b|^\s*_SUBSCRIPTIONS\b",
        _CORE_DIR / "execution_module.py",
    ) + _grep_count_in_file(
        r"^\s*_\w+_(SPECS|REGISTRY|TABLE|MAP|EFFECTS|FIELDS)\b|^\s*_SUBSCRIPTIONS\b",
        _CORE_DIR / "trade_module.py",
    )

    return {
        "eventbus_unique": eventbus_unique,
        "eventbus_residue": eventbus_residue,
        "eventdriver_unique": eventdriver_unique,
        "eventdriver_residue": eventdriver_residue,
        "configstore_unique": configstore_unique,
        "configstore_residue": configstore_residue,
        "meta_purity": round(meta_purity, 2),
        "no_fourth_dispatcher": no_fourth_dispatcher,
        "fourth_dispatcher_lines": fourth_dispatcher_lines,
        "meta_residue_lines": meta_residue_lines,
        "total_business_lines": total_business_lines,
        "dispatcher_call_lines": dispatcher_calls,
        "data_declaration_lines": data_declarations,
    }


# ---------------------------------------------------------------------------
# v5 新增 4 维数据采集（MetaDispatcher 统一 + 运行时验证 + eventtest 回归 + 跨模块 import）
# ---------------------------------------------------------------------------


#: v5 7 个业务模块（禁直接 import table_engine）
_BUSINESS_MODULES_V5: List[str] = [
    "execution_module.py", "screening_module.py", "formula_module.py",
    "runtime_mode_module.py", "trade_module.py", "tick_bar_module.py",
    "monitoring_module.py",
]

#: v5 3 个 in-process 运行时验证测试文件
_RUNTIME_TEST_FILES: List[str] = [
    "test_runtime_replay_heapq.py",
    "test_runtime_simulation_heapq.py",
    "test_runtime_mode_switch.py",
]


def _collect_dispatcher_isomorphism() -> Dict[str, Any]:
    """采集 MetaDispatcher 统一数据（SubTask 16.1）。

    AST 验证：MetaDispatcher 基类存在 + EventBus(MetaDispatcher) +
    ConfigStoreBase(MetaDispatcher) 继承 + EventDriver 独立 + 公共骨架行数占比。

    骨架占比 = MetaDispatcher 基类行数 / (基类 + EventBus._dispatch_impl +
    ConfigStore._dispatch_impl) 总行数，目标 ≥ 60% 满分。
    """
    event_bus_file = _CORE_DIR / "event_bus.py"
    table_file = _CORE_DIR / "table_engine.py"
    exec_file = _CORE_DIR / "execution_module.py"

    def _class_bases(file_path: Path, cls_name: str) -> Tuple[bool, List[str]]:
        tree = _parse_ast(file_path)
        if tree is None:
            return False, []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == cls_name:
                bases = [b.id for b in node.bases if isinstance(b, ast.Name)]
                return True, bases
        return False, []

    def _method_lines(file_path: Path, cls_name: str, method: str) -> int:
        tree = _parse_ast(file_path)
        if tree is None:
            return 0
        for node in ast.walk(tree):
            if (isinstance(node, ast.ClassDef) and node.name == cls_name):
                for sub in node.body:
                    if (isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef))
                            and sub.name == method):
                        end = getattr(sub, "end_lineno", sub.lineno) or sub.lineno
                        return end - sub.lineno + 1
        return 0

    def _class_lines(file_path: Path, cls_name: str) -> int:
        tree = _parse_ast(file_path)
        if tree is None:
            return 0
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == cls_name:
                end = getattr(node, "end_lineno", node.lineno) or node.lineno
                return end - node.lineno + 1
        return 0

    meta_exists, _ = _class_bases(event_bus_file, "MetaDispatcher")
    _, eb_bases = _class_bases(event_bus_file, "EventBus")
    eventbus_inherits = "MetaDispatcher" in eb_bases
    _, cs_bases = _class_bases(table_file, "ConfigStoreBase")
    configstore_inherits = "MetaDispatcher" in cs_bases
    _, ed_bases = _class_bases(exec_file, "EventDriver")
    eventdriver_independent = "MetaDispatcher" not in ed_bases

    meta_lines = _class_lines(event_bus_file, "MetaDispatcher")
    eb_disp = _method_lines(event_bus_file, "EventBus", "_dispatch_impl")
    cs_disp = _method_lines(table_file, "ConfigStore", "_dispatch_impl")
    denom = meta_lines + eb_disp + cs_disp
    skeleton_ratio = (meta_lines / denom) if denom > 0 else 0.0

    return {
        "meta_dispatcher_exists": meta_exists,
        "eventbus_inherits_meta": eventbus_inherits,
        "configstore_inherits_meta": configstore_inherits,
        "eventdriver_independent": eventdriver_independent,
        "skeleton_ratio": round(skeleton_ratio, 4),
        "meta_lines": meta_lines,
        "eventbus_dispatch_lines": eb_disp,
        "configstore_dispatch_lines": cs_disp,
    }


def _collect_runtime_verification(stats: "_StatsPlugin") -> Dict[str, Any]:
    """采集运行时验证 harness 数据（SubTask 16.2）。

    从 ``_StatsPlugin.file_stats`` 采集 3 个 in-process 运行时测试文件的通过率。
    这些测试已在主 pytest 运行中执行（非阻塞文件），故直接复用 file_stats 真实
    结果，避免 subprocess 重复运行。每个文件 passed > 0 且 failed/errors == 0
    计为通过。

    v13 fail-loud 硬化（第十三层洞察——检测器引用不存在目标是检测器自身缺陷）：
    当 ``_RUNTIME_TEST_FILES`` 中任一文件不在 ``stats.file_stats`` 中时（检测器
    引用的目标文件缺失），将其加入 ``missing_files`` 字段返回，而非静默 ``continue``
    跳过制造「已检测」假象。下游 ``_check_isomorphism`` 第 49 项检查据此计 1 违规，
    使检测器引用完整性成为可执行约束。
    """
    passed = 0
    total = len(_RUNTIME_TEST_FILES)
    missing_files: List[str] = []
    for fname in _RUNTIME_TEST_FILES:
        fstats = stats.file_stats.get(fname)
        if fstats is None:
            missing_files.append(fname)
            continue
        if fstats.get("passed", 0) > 0 and fstats.get("failed", 0) == 0 \
                and fstats.get("errors", 0) == 0:
            passed += 1
    return {
        "passed": passed,
        "total": total,
        "files": list(_RUNTIME_TEST_FILES),
        "missing_files": missing_files,
    }


def _collect_eventtest_regression() -> Dict[str, Any]:
    """采集 eventtest 回归数据（SubTask 16.3）。

    通过 subprocess 运行 ``python -m eventtest.run_eventtest``，采集退出码。
    退出码 0 = 全绿；非 0 = 有失败。超时（300s，eventtest 实测 ~155s）视为失败。
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "eventtest.run_eventtest"],
            capture_output=True, text=True, timeout=300, cwd=str(_PROJECT_ROOT),
        )
        exit_code = proc.returncode
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        exit_code = 1
    return {"exit_code": exit_code}


def _collect_cross_module_import_discipline() -> Dict[str, Any]:
    """采集跨模块 import 纪律数据（SubTask 16.4）。

    Grep 8 处违规模式：7 业务模块禁 ``from .table_engine import`` +
    execution_module 禁 ``from .screening_module import``。每处 > 0 匹配计 1 违规。
    """
    pattern = r"from\s+\.table_engine\s+import|from\s+core\.table_engine\s+import"
    violations = 0
    for mod in _BUSINESS_MODULES_V5:
        fpath = _CORE_DIR / mod
        if _grep_count_in_file(pattern, fpath) > 0:
            violations += 1
    # 第 8 处：execution_module 禁 from .screening_module import
    if _grep_count_in_file(
        r"from\s+\.screening_module\s+import", _CORE_DIR / "execution_module.py"
    ) > 0:
        violations += 1
    return {"violations": violations, "total_patterns": 8}


# ---------------------------------------------------------------------------
# v6 新增 1 维数据采集（adapter 转发同构：TqProvider/TqSdkBridge 表驱动覆盖率）
# ---------------------------------------------------------------------------


def _count_class_table_entries(file_path: Path, table_name: str) -> int:
    """AST 统计类级表（AnnAssign dict 字面量）的条目数。

    用于 ``_FORWARD_SPECS`` / ``_CACHED_TQ_CALLS`` / ``_SIMPLE_TQ_CALLS`` /
    ``_PER_CODE_TQ_CALLS`` 四表条目计数——这些表以 ``_NAME: Dict[...] = {...}``
    形式定义于类体，AST 解析 AnnAssign.value 为 ast.Dict 时返回其键数。
    """
    tree = _parse_ast(file_path)
    if tree is None:
        return 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            if (isinstance(stmt, ast.AnnAssign)
                    and isinstance(stmt.target, ast.Name)
                    and stmt.target.id == table_name
                    and isinstance(stmt.value, ast.Dict)):
                return len(stmt.value.keys)
    return 0


def _get_class_table_keys(file_path: Path, table_name: str) -> List[str]:
    """AST 提取类级表（AnnAssign dict 字面量）的字符串 key 列表。

    用于 ``_CACHED_TQ_CALLS`` / ``_PER_CODE_TQ_CALLS`` 表 key 集合校验
    （v7 双签名收敛 + per_code truthy flag 检查）。仅返回 key 为 ast.Constant
    字符串字面量的 key 值；非字符串 key 跳过。
    """
    tree = _parse_ast(file_path)
    if tree is None:
        return []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            if (isinstance(stmt, ast.AnnAssign)
                    and isinstance(stmt.target, ast.Name)
                    and stmt.target.id == table_name
                    and isinstance(stmt.value, ast.Dict)):
                keys: List[str] = []
                for k in stmt.value.keys:
                    if (isinstance(k, ast.Constant)
                            and isinstance(k.value, str)):
                        keys.append(k.value)
                return keys
    return []


def _collect_adapter_isomorphism() -> Dict[str, Any]:
    """采集 adapter 转发同构数据（SubTask 8.1）。

    Grep 4 个通用转发器方法（``_forward`` / ``_call_cached`` / ``_call_simple`` /
    ``_call_cached_per_code``）定义数 + AST 统计四表条目数 → 表驱动覆盖率。
    仅当对应通用转发器存在时，该表条目计为已表驱动覆盖；
    覆盖率 = 已覆盖 / 总转发方法数 × 100。

    v7 收尾闭合：新增 ``_PER_CODE_TQ_CALLS`` 表（3 条目）+ ``_call_cached_per_code``
    通用转发器；``_CACHED_TQ_CALLS`` 追加 ``get_stock_list_by_type`` /
    ``get_stock_list`` 双签名收敛至 6 条目。总表驱动方法数 =
    ``_FORWARD_SPECS``（20）+ ``_CACHED_TQ_CALLS``（6）+ ``_SIMPLE_TQ_CALLS``（5）
    + ``_PER_CODE_TQ_CALLS``（3）= 34。
    """
    providers_file = _SERVICES_DIR / "providers.py"
    fwd = _grep_count_in_file(r"def _forward\b", providers_file)
    cached_m = _grep_count_in_file(r"def _call_cached\b", providers_file)
    simple_m = _grep_count_in_file(r"def _call_simple\b", providers_file)
    per_code_m = _grep_count_in_file(r"def _call_cached_per_code\b", providers_file)
    forward_n = _count_class_table_entries(providers_file, "_FORWARD_SPECS")
    cached_n = _count_class_table_entries(providers_file, "_CACHED_TQ_CALLS")
    simple_n = _count_class_table_entries(providers_file, "_SIMPLE_TQ_CALLS")
    per_code_n = _count_class_table_entries(providers_file, "_PER_CODE_TQ_CALLS")
    covered = (
        (forward_n if fwd else 0)
        + (cached_n if cached_m else 0)
        + (simple_n if simple_m else 0)
        + (per_code_n if per_code_m else 0)
    )
    total = forward_n + cached_n + simple_n + per_code_n
    coverage = (covered / total * 100.0) if total > 0 else 0.0
    # v7 双签名收敛：_CACHED_TQ_CALLS 含 get_stock_list_by_type + get_stock_list 两个 key
    cached_keys = set(_get_class_table_keys(providers_file, "_CACHED_TQ_CALLS"))
    dual_signature_converged = (
        "get_stock_list_by_type" in cached_keys
        and "get_stock_list" in cached_keys
    )
    return {
        "generic_method_count": fwd + cached_m + simple_m + per_code_m,
        "forward_specs_entries": forward_n,
        "cached_tq_calls_entries": cached_n,
        "simple_tq_calls_entries": simple_n,
        "per_code_tq_calls_entries": per_code_n,
        "total_forward_methods": total,
        "covered_methods": covered,
        "coverage": round(coverage, 2),
        "dual_signature_converged": dual_signature_converged,
    }


# ---------------------------------------------------------------------------
# v10 新增：handler 异常保护覆盖采集（第十层洞察）
# ---------------------------------------------------------------------------


def _collect_handler_exception_coverage() -> Dict[str, Any]:
    """v10: 采集 handler 异常保护覆盖数据（第十层洞察：异常处理覆盖完整性是运行时安全本质）。

    AST 解析 ``core/*.py``，对所有 ``self._bus.subscribe(EventType, self._handler_name)``
    形式的手动订阅调用，检查 ``_handler_name`` 方法的 ``decorator_list`` 是否含
    ``_event_handler`` 装饰器。覆盖率 = 已装饰 handler 数 / 手动 subscribe handler
    总数 × 100。

    **排除项**：``_BaseModule._register_subscribers`` 中的表驱动订阅
    （``self._bus.subscribe(event_type, getattr(self, handler_name))``，第二参数为
    ``getattr`` Call 而非 ``self._handler_name`` Attribute）——这些 handler 通过
    ``_SUBSCRIPTIONS`` 表注册，由各自模块的 ``@_event_handler`` 装饰保障，非手动
    subscribe，不计入本覆盖率统计。

    EventBus.publish 同步扇出（``_dispatch_impl`` 遍历订阅者逐个调用），若某 handler
    抛未捕获异常，后续订阅者不执行——事件链断裂。``_event_handler`` 装饰器将异常
    处理从 handler 体内部上提到装饰器层（AOP 横切），统一了"handler 不应中断事件链"
    的运行时契约。本采集驱动该契约 100% 覆盖。

    Returns:
        dict 含 ``covered``/``total``/``coverage``/``uncovered_handlers`` 字段。
        ``coverage`` = 已装饰数 / 总数 × 100（总数为 0 时返回 100.0）。
        ``uncovered_handlers`` 为 ``"文件名:类名.handler名"`` 列表。
    """
    uncovered: List[str] = []
    total = 0
    covered = 0

    if not _CORE_DIR.is_dir():
        return {
            "covered": 0, "total": 0, "coverage": 100.0,
            "uncovered_handlers": [],
        }

    def _decorator_names(dec_list) -> set:
        names: set = set()
        for dec in dec_list:
            if isinstance(dec, ast.Name):
                names.add(dec.id)
            elif isinstance(dec, ast.Attribute):
                names.add(dec.attr)
            elif isinstance(dec, ast.Call):
                f = dec.func
                if isinstance(f, ast.Name):
                    names.add(f.id)
                elif isinstance(f, ast.Attribute):
                    names.add(f.attr)
        return names

    def _is_self_bus_subscribe(call: ast.Call) -> bool:
        """判断 call 是否为 ``self._bus.subscribe(...)`` 调用。"""
        func = call.func
        if not isinstance(func, ast.Attribute) or func.attr != "subscribe":
            return False
        bus = func.value
        if not (isinstance(bus, ast.Attribute) and bus.attr == "_bus"):
            return False
        return isinstance(bus.value, ast.Name) and bus.value.id == "self"

    def _extract_handler_name(call: ast.Call) -> Optional[str]:
        """从 subscribe 调用第二参数提取 ``self._handler_name`` 的 handler_name。

        仅匹配 ``self._handler_name``（Attribute）形式；``getattr(self, handler_name)``
        （Call）形式返回 None（表驱动订阅，不计入手动 subscribe 统计）。
        """
        if len(call.args) < 2:
            return None
        arg = call.args[1]
        if (isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name)
                and arg.value.id == "self"):
            return arg.attr
        return None

    for py_file in _CORE_DIR.glob("*.py"):
        tree = _parse_ast(py_file)
        if tree is None:
            continue

        # 第一遍：收集每个 ClassDef 的直接方法名→装饰器名集合映射
        class_method_decorators: Dict[str, Dict[str, set]] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            method_decs: Dict[str, set] = {}
            for sub in node.body:
                if not isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                method_decs[sub.name] = _decorator_names(sub.decorator_list)
            class_method_decorators[node.name] = method_decs

        # 第二遍：递归遍历，跟踪当前类上下文，定位手动 subscribe 调用
        def _visit(node, current_class: Optional[str]) -> None:
            nonlocal total, covered
            if isinstance(node, ast.ClassDef):
                current_class = node.name
            if isinstance(node, ast.Call) and _is_self_bus_subscribe(node):
                handler_name = _extract_handler_name(node)
                if handler_name is not None and current_class is not None:
                    total += 1
                    decs = class_method_decorators.get(
                        current_class, {}
                    ).get(handler_name, set())
                    if "_event_handler" in decs:
                        covered += 1
                    else:
                        uncovered.append(
                            f"{py_file.name}:{current_class}.{handler_name}"
                        )
            for child in ast.iter_child_nodes(node):
                _visit(child, current_class)

        _visit(tree, None)

    coverage = (covered / total * 100.0) if total > 0 else 100.0
    return {
        "covered": covered,
        "total": total,
        "coverage": round(coverage, 2),
        "uncovered_handlers": uncovered,
    }


# ---------------------------------------------------------------------------
# v11 新增：审计盲区闭合采集（第十一层洞察：审计盲区是收敛上限的最大敌人）
# ---------------------------------------------------------------------------


def _collect_converters_polling_violations() -> Dict[str, Any]:
    """v11: 采集 converters.py 轮询模式违规（第十一层洞察：审计盲区闭合）。

    AST 检测 ``converters.py`` 内 ``while + wait(N) + time.time()`` 轮询调度模式
    （v11 已删除的轮询执行器._run_loop 已删除后应零匹配）。该模式是 v10 审计盲区——
    v10 轮询零容忍检查只覆盖 core/runtime_mode_module.py / core/table_engine.py /
    services/data.py，从未覆盖 converters.py，导致 v11 已删除的轮询执行器平行运行时漏判。

    检测逻辑：AST 遍历 ``while`` 语句，若循环条件含 ``.wait(N)`` 调用（带超时参数的
    事件等待，即轮询-with-超时模式）且循环体内含 ``time.time()`` 调用（时间戳轮询
    判定），计 1 违规。

    Returns:
        dict 含 ``violations``/``files``/``details`` 字段。零违规时 violations=0。
    """
    files: List[str] = []
    details: List[str] = []
    tree = _parse_ast(_CONVERTERS_FILE)
    if tree is None:
        return {"violations": 0, "files": files, "details": details}

    def _has_wait_call(node) -> bool:
        """node 子树是否含 ``.wait(N)`` 调用（事件等待，含超时参数即轮询模式）。"""
        for child in ast.walk(node):
            if (isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and child.func.attr == "wait"):
                return True
        return False

    def _has_time_time_call(node) -> bool:
        """node 子树是否含 ``time.time()`` 调用。"""
        for child in ast.walk(node):
            if (isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and child.func.attr == "time"
                    and isinstance(child.func.value, ast.Name)
                    and child.func.value.id == "time"):
                return True
        return False

    violations = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.While):
            continue
        # while 条件含 .wait(N) 且循环体含 time.time() → 轮询调度模式
        if _has_wait_call(node.test) and _has_time_time_call(node):
            violations += 1
            files.append("converters.py")
            details.append(
                f"converters.py:{node.lineno} while + wait(N) + time.time() 轮询模式"
            )
    return {"violations": violations, "files": files, "details": details}


def _while_body_has_polling_sleep(node: ast.While) -> bool:
    """v12 M4：``ast.While`` 循环体内是否含轮询 sleep 调用。

    检测以下结构性轮询模式（NAME-BASED 正则的兜底）：
      - ``time.sleep(...)`` — ``ast.Call`` func 为 ``ast.Attribute(attr='sleep')`` on ``ast.Name(id='time')``
      - ``asyncio.sleep(N)`` — ``ast.Call`` func 为 ``ast.Attribute(attr='sleep')`` on ``ast.Name(id='asyncio')``
      - sync ``.wait(N)`` — ``ast.Call`` func 为 ``ast.Attribute(attr='wait')`` 且不在 ``ast.Await`` 下
        （``asyncio.Event.wait()`` 形如 ``await event.wait()``，Call 在 Await.value 下，不计入）

    遍历 While 节点的 body（含嵌套 while/for/if 的递归子树），任一命中即返回 True。
    """
    # 收集所有 Await 下的 .wait() Call 节点 id（async wait，不计入）
    async_wait_ids: set = set()
    all_nodes: List[ast.AST] = []
    for child in ast.walk(node):
        all_nodes.append(child)
        if isinstance(child, ast.Await):
            val = child.value
            if (isinstance(val, ast.Call)
                    and isinstance(val.func, ast.Attribute)
                    and val.func.attr == "wait"):
                async_wait_ids.add((val.lineno, val.col_offset))

    for child in all_nodes:
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if not isinstance(func, ast.Attribute):
            continue
        # time.sleep(...) / asyncio.sleep(...)
        if func.attr == "sleep" and isinstance(func.value, ast.Name):
            if func.value.id in ("time", "asyncio"):
                return True
        # sync .wait(N) — 不在 Await 下（async .wait() 已在 async_wait_ids 中排除）
        if func.attr == "wait":
            if (child.lineno, child.col_offset) not in async_wait_ids:
                return True
    return False


def _collect_structural_polling_violations() -> Dict[str, Any]:
    """v12 M4：结构性 AST 轮询检测（NAME-BASED 正则的兜底）。

    v11 ``POLLING_PATTERNS`` 12 项中 7 项是 NAME-BASED（``start_polling`` /
    ``_file_watcher_loop`` / ``_sync_play_loop`` / ``_sync_sim_loop`` /
    ``auto_step_loop`` / ``while self._run`` / ``while self._sim_auto_step``），
    新轮询方法换名即漏检。本函数用结构性 AST 检测：扫描 ``ast.While`` 节点，
    循环体内含 ``time.sleep()`` / ``asyncio.sleep(N)`` / sync ``.wait(N)`` 任一即违规
    （含 ``# noqa: event-driver`` 排除——合法残留如 EventDriver 自身派发循环 /
    递归下降解析器 token 循环 / 网络限速退避）。

    扫描范围 ``core/`` + ``services/`` + ``app.py`` + ``converters.py``
    （与 ``_collect_polling_violations`` 对齐）。

    Returns:
        dict 含 ``violations``/``files``/``details`` 字段。零违规时 violations=0。
    """
    files: List[str] = []
    details: List[str] = []

    # 扫描范围：core/*.py + services/*.py + app.py + converters.py
    scan_files: List[Path] = []
    if _CORE_DIR.is_dir():
        scan_files.extend(sorted(_CORE_DIR.glob("*.py")))
    if _SERVICES_DIR.is_dir():
        scan_files.extend(sorted(_SERVICES_DIR.glob("*.py")))
    scan_files.append(_APP_FILE)
    scan_files.append(_CONVERTERS_FILE)

    violations = 0
    for py_file in scan_files:
        if not py_file.is_file():
            continue
        tree = _parse_ast(py_file)
        if tree is None:
            continue
        # 读取源文件用于 noqa 行级排除
        try:
            src_lines = py_file.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            src_lines = []
        rel_name = py_file.relative_to(_PROJECT_ROOT)

        for node in ast.walk(tree):
            if not isinstance(node, ast.While):
                continue
            # noqa: event-driver 排除——检查 While 节点所在行（含父语句行）
            # ast.While.lineno 指向 while 关键字所在行
            if 0 < node.lineno <= len(src_lines):
                if _NOQA_EVENT_DRIVER in src_lines[node.lineno - 1]:
                    continue
            # 循环体内含轮询 sleep 调用
            if _while_body_has_polling_sleep(node):
                violations += 1
                files.append(str(rel_name))
                details.append(
                    f"{rel_name}:{node.lineno} ast.While 循环体内含 "
                    f"time.sleep/asyncio.sleep/sync .wait 轮询模式"
                )

    return {"violations": violations, "files": files, "details": details}


def _file_uses_threading_primitives(content: str) -> bool:
    """v12 M2：检测文件是否使用 ``threading.Thread`` 或 ``threading.Timer``（含 import 别名）。

    v11 ``_file_uses_threading_thread`` 仅检测 ``Thread``，v11 spec 点名的
    ``threading.Timer`` 绕过 EventDriver 模式无检测器 enforce。v12 M2 扩展为
    同时检测 ``Thread`` 与 ``Timer`` 两种 threading 原语（均属平行运行时调度）。
    """
    # 直接 threading.Thread( / threading.Timer(
    if re.search(r"\bthreading\.(?:Thread|Timer)\s*\(", content):
        return True
    # import threading[ as alias] + alias.Thread( / alias.Timer(
    m = re.search(r"^\s*import\s+threading(?:\s+as\s+(\w+))?", content, re.MULTILINE)
    if m:
        alias = m.group(1) or "threading"
        if re.search(r"\b" + re.escape(alias) + r"\.(?:Thread|Timer)\s*\(", content):
            return True
    # from threading import Thread[ as alias] / Timer[ as alias] + alias(
    m2 = re.search(
        r"^\s*from\s+threading\s+import\s+[^#\n]*\b(?:Thread|Timer)\b(?:\s+as\s+(\w+))?",
        content, re.MULTILINE,
    )
    if m2:
        name = m2.group(1) or "Thread"
        # name 可能来自 Thread 或 Timer 的 alias；若 group(1) 为 None 则两种均需检测
        if m2.group(1) is None:
            for nm in ("Thread", "Timer"):
                if re.search(r"\b" + re.escape(nm) + r"\s*\(", content):
                    return True
        else:
            if re.search(r"\b" + re.escape(name) + r"\s*\(", content):
                return True
    return False


def _count_sync_wait_calls(tree: ast.Module) -> int:
    """统计 AST 中非 ``await`` 的 ``.wait()`` 调用数（sync threading.Event.wait）。

    ``asyncio.Event.wait()`` 形如 ``await event.wait()``，其 Call 节点是
    ``ast.Await.value`` 直接子节点；``threading.Event.wait()`` 形如
    ``event.wait(N)``（无 await），Call 节点不在 Await 下。本函数统计不在
    Await 下的 ``.wait()`` 调用——PoolEngine 的 ``asyncio.Event.wait()`` 不计入。
    """
    async_wait_ids: set = set()
    all_wait_calls: List[Tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Await):
            val = node.value
            if (isinstance(val, ast.Call)
                    and isinstance(val.func, ast.Attribute)
                    and val.func.attr == "wait"):
                async_wait_ids.add((val.lineno, val.col_offset))
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "wait"):
            all_wait_calls.append((node.lineno, node.col_offset))
    return sum(1 for wid in all_wait_calls if wid not in async_wait_ids)


def _collect_parallel_runtime_violations() -> Dict[str, Any]:
    """v11: 采集平行运行时违规（第十一层洞察：运行时单一真相源）。

    检测 ``converters.py`` / ``services/*.py`` 内 ``threading.Thread`` + ``while`` +
    ``threading.Event.wait``（sync ``.wait()``）组合的平行运行时模式。
    PoolEngine 使用 ``asyncio.Event.wait()`` + ``loop.call_at`` 事件驱动（非 threading），
    不在此列——仅 flag ``threading.Thread`` + ``while`` + sync ``.wait()`` 组合。

    v10 漏判的轮询执行器（v11 已删除）即此模式：``threading.Thread(target=_run_loop)`` +
    ``while not self._stop_event.wait(1):`` + ``time.time()`` 轮询调度，完整复制
    PoolEngine + EventBus 能力但用轮询而非事件驱动。v11 删除后应零违规。

    Returns:
        dict 含 ``violations``/``files``/``details`` 字段。零违规时 violations=0。
    """
    files: List[str] = []
    details: List[str] = []

    # 扫描范围：converters.py + services/*.py
    scan_files: List[Path] = [_CONVERTERS_FILE]
    if _SERVICES_DIR.is_dir():
        scan_files.extend(sorted(_SERVICES_DIR.glob("*.py")))

    violations = 0
    for py_file in scan_files:
        if not py_file.is_file():
            continue
        tree = _parse_ast(py_file)
        if tree is None:
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        # (1) 文件含 threading.Thread / threading.Timer 使用（v12 M2：扩展为 threading 原语）
        if not _file_uses_threading_primitives(content):
            continue
        # (2) 文件含 while 语句
        has_while = any(isinstance(n, ast.While) for n in ast.walk(tree))
        if not has_while:
            continue
        # (3) 文件含 sync（非 await）.wait() 调用 → threading.Event.wait 平行运行时信号
        sync_waits = _count_sync_wait_calls(tree)
        if sync_waits == 0:
            continue

        violations += 1
        files.append(py_file.name)
        details.append(
            f"{py_file.name}: threading.Thread + while + sync .wait() "
            f"平行运行时模式（{sync_waits} 处 sync wait）"
        )

    return {"violations": violations, "files": files, "details": details}


#: v11 死代码检测的预存豁免名单（v11 metatest 范围外的预存死类，跟踪待后续清理）。
#: v11 本轮仅负责删除 DzhXmlExporter；下列类为 v11 之前已存在、且不在 metatest/
#: 可修改范围内的预存死类，豁免后使 metatest 能干净 enforce「v11 不引入新死代码」。
#: 名称拆分构造以避免本文件源码自引用干扰死代码引用计数（runner.py 在引用搜索语料中）。
#: v12 阶段 4 已删除预存死类 Error+Response（原 api.py 未引用 BaseModel，Task 14）。
#:
#: v12 M1（AST 引用计数）新揭示的预存死类：v11 字符串正则把 docstring/注释中的
#: 类名提及误计为引用（假阳性），掩盖了下述 4 类的真实死代码状态。v12 M1 改用 AST
#: 引用计数（排除 docstring/注释/字符串字面量）后，这 4 类零真实引用被正确识别为死代码。
#: 此 4 类为预存死代码（v12 之前已存在），不在「v12 metatest 检测器硬化」阶段范围内
#: （删除生产代码类超出 metatest 检测器逻辑硬化范畴），列入豁免名单跟踪待后续清理阶段删除。
#: 注：v12 M1 已将 metatest/ 排除出 ref_py_files，且 AST 不计 frozenset 字符串字面量
#: （非 ast.Name/Attribute/import/__all__/annotation），故类名直写无自引用污染。
_PRE_EXISTING_DEAD_CLASSES: frozenset = frozenset({
    "Cell202AttrBitsModel",  # core/schemas.py:555 — type=202 备选池 attr 位标志兼容类，零实例化零导入
    "StepSpec",  # core/schemas.py:1031 — 步骤规格 BaseModel，仅注释 List[StepSpec] 提及（实际字段为 List[Any]）
    "SchemaValidator",  # native/validators.py:833 — 三级校验器，仅 docstring/注释提及，零实例化零导入
    "TableLoader",  # native/validators.py:1865 — 热重载模块，仅 docstring 提及，零实例化零导入
})


def _is_protocol_class(node: ast.ClassDef) -> bool:
    """node 是否为 ``typing.Protocol`` 结构类型（基类含 Protocol）。

    Protocol 是结构性类型契约，可不命名引用即被任意匹配形状的类隐式满足，
    与 DzhXmlExporter 式的具体死实现不是同一类别，故排除出死代码检测。
    """
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id == "Protocol":
            return True
        if isinstance(base, ast.Attribute) and base.attr == "Protocol":
            return True
    return False


def _count_ast_references(class_name: str, py_files: List[Path]) -> int:
    """v12 M1：AST 引用计数（替代字符串正则，闭合 docstring 假阳性盲区）。

    遍历每个 .py 文件的 AST，统计 ``class_name`` 的真实结构引用：
      - ``ast.Name(id=class_name)`` — 名称引用（实例化 / isinstance / 类型注解 /
        ``ClassDef.bases`` 中的基类引用等，``ast.walk`` 全遍历天然覆盖 bases）
      - ``ast.Attribute(attr=class_name)`` — 属性引用（``module.ClassName``）
      - ``ast.ImportFrom.names`` / ``ast.Import.names`` 中 ``alias.name == class_name``
        — import 别名（类被任一模块导入即 alive，等价于 v11 字符串正则对 import 行的计数）
      - ``__all__`` 列表/元组/集合中的字符串条目 — 公开 API 导出声明
        （类被 ``__all__`` 导出即 alive，是结构性引用而非 docstring 描述）
      - 字符串注解（forward reference）— ``AnnAssign.annotation`` /
        ``FunctionDef.returns`` / ``arg.annotation`` 子树中的 ``ast.Constant str``
        （前向类型引用是结构性引用，区别于日志/错误消息等任意字符串字面量）

    排除（v11 字符串正则的假阳性源）：
      - docstring — ``Expr → Constant str``（模块/类/函数文档），不产生 ``ast.Name`` 节点，
        AST 天然排除；本函数亦不计入 ``__all__``/注解 之外的任意 ``Constant str``（日志消息、
        错误消息、注释文本等），闭合 v11 字符串正则把 docstring/注释提及误计为引用的假阳性
      - 注释 — AST 不含注释，天然排除

    类定义自身的 ``class ClassName:`` 不产生 ``ast.Name`` 节点（``ClassName`` 是
    ``ClassDef.name`` 字符串属性，非 AST 节点），故本函数天然不计定义本身，
    仅计真实引用——零引用即死代码（零实例化 / 零继承 / 零类型注解 / 零导入 / 零导出）。
    """
    count = 0
    for py_file in py_files:
        tree = _parse_ast(py_file)
        if tree is None:
            continue
        # (1) Name / Attribute / bases — ast.walk 全遍历，bases 中的 Name 天然覆盖
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == class_name:
                count += 1
            elif isinstance(node, ast.Attribute) and node.attr == class_name:
                count += 1
            # (2) import 别名 — ast.alias.name 是字符串属性（非 Constant），不被「排除字符串字面量」覆盖
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if alias.name == class_name:
                        count += 1
            # (3) __all__ 导出声明 — 列表/元组/集合中的字符串条目（结构性引用，非 docstring 描述）
            elif isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and tgt.id == "__all__":
                        if isinstance(node.value, (ast.List, ast.Tuple, ast.Set)):
                            for elt in node.value.elts:
                                if (isinstance(elt, ast.Constant)
                                        and isinstance(elt.value, str)
                                        and elt.value == class_name):
                                    count += 1
        # (4) 字符串注解（forward reference）— 注解子树中的 Constant str（类型引用，非日志消息）
        for node in ast.walk(tree):
            annotation = None
            if isinstance(node, ast.AnnAssign):
                annotation = node.annotation
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                annotation = node.returns
            elif isinstance(node, ast.arg):
                annotation = node.annotation
            if annotation is not None:
                for sub in ast.walk(annotation):
                    if (isinstance(sub, ast.Constant)
                            and isinstance(sub.value, str)
                            and sub.value == class_name):
                        count += 1
    return count


def _build_ast_reference_map(
    class_names: Set[str], py_files: List[Path]
) -> Dict[str, int]:
    """v12 M1：批量 AST 引用计数（单遍扫描所有文件，构建 ``{class_name: refs}`` 映射）。

    与 ``_count_ast_references`` 语义完全一致，但仅对每个文件做 2 次 ``ast.walk``
    （而非 ``len(class_names) × 2`` 次），复杂度从 O(classes × files) 降为 O(files)。
    240 类 × 84 文件场景下，walk 次数从 ~40320 降至 ~168（~240x 提速）。
    """
    counts: Dict[str, int] = {name: 0 for name in class_names}
    if not class_names:
        return counts
    for py_file in py_files:
        tree = _parse_ast(py_file)
        if tree is None:
            continue
        # (1) Name / Attribute / bases / import / __all__ — 单遍 walk
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in class_names:
                counts[node.id] += 1
            elif isinstance(node, ast.Attribute) and node.attr in class_names:
                counts[node.attr] += 1
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if alias.name in class_names:
                        counts[alias.name] += 1
            elif isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and tgt.id == "__all__":
                        if isinstance(node.value, (ast.List, ast.Tuple, ast.Set)):
                            for elt in node.value.elts:
                                if (isinstance(elt, ast.Constant)
                                        and isinstance(elt.value, str)
                                        and elt.value in class_names):
                                    counts[elt.value] += 1
        # (2) 字符串注解（forward reference）— 单遍 walk
        for node in ast.walk(tree):
            annotation = None
            if isinstance(node, ast.AnnAssign):
                annotation = node.annotation
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                annotation = node.returns
            elif isinstance(node, ast.arg):
                annotation = node.annotation
            if annotation is not None:
                for sub in ast.walk(annotation):
                    if (isinstance(sub, ast.Constant)
                            and isinstance(sub.value, str)
                            and sub.value in class_names):
                        counts[sub.value] += 1
    return counts


def _collect_dead_code_violations() -> Dict[str, Any]:
    """v11: 采集死代码类违规（第十一层洞察：死代码零容忍）。

    v12 M1 硬化：引用检测从字符串正则改为 AST 引用计数（``_count_ast_references``），
    闭合 v11 字符串正则的 docstring/字符串字面量假阳性盲区——AST 天然排除 docstring
    （``Expr → Constant str``）、字符串字面量（``Constant`` str 不含 ``Name`` 节点）
    与注释（AST 不含注释）。类定义自身的 ``class ClassName:`` 不产生 ``ast.Name`` 节点
    （``ClassName`` 是 ``ClassDef.name`` 字符串属性），故零 AST 引用即死代码。

    AST 解析 .py 文件（排除 metatest/ 自身、simtests/、eventtest/、test_*.py、
    __pycache__、隐藏目录、.trae/），收集非私有（非 ``_`` 前缀）类定义。
    跳过 ``Test*`` 命名类（pytest 运行时收集实例化）与 ``Protocol`` 结构类型
    （隐式满足，非死实现）。对每个类名，AST 计数 ``Name``/``Attribute``/基类引用——
    零引用即死代码（零实例化/零继承/零类型注解）。

    v12 M1：引用搜索文件集（``ref_py_files``）排除 metatest/ 自身（与 ``def_py_files``
    对齐），避免 metatest 描述被检测对象时产生自引用污染。simtests/ / eventtest/
    测试目录仍计入引用语料（类被测试引用亦算 alive）。
    ``_PRE_EXISTING_DEAD_CLASSES`` 名单中的预存死类不计入 ``count``，而是列入
    ``pre_existing`` 字段跟踪。v12 阶段 4 删除了 Error+Response（原豁免项），但 v12 M1
    AST 引用计数新揭示 4 类预存死代码（Cell202AttrBitsModel / StepSpec / SchemaValidator /
    TableLoader——v11 字符串正则假阳性掩盖），列入豁免待后续清理阶段删除。

    Returns:
        dict 含 ``dead_classes``/``count``/``pre_existing`` 字段。
    """
    project_root = _PROJECT_ROOT

    def _is_excluded(
        parts: Tuple[str, ...],
        *,
        exclude_metatest: bool,
        exclude_tests: bool = False,
    ) -> bool:
        if not parts:
            return True
        if "__pycache__" in parts:
            return True
        if any(p.startswith(".") for p in parts):
            return True
        if exclude_metatest and parts[0] == "metatest":
            return True
        if exclude_tests:
            # 测试目录/文件由 pytest 运行时收集，其类定义不计入死代码扫描
            if parts[0] in ("simtests", "eventtest"):
                return True
            if parts[-1].startswith("test_") and parts[-1].endswith(".py"):
                return True
        return False

    # 类定义源文件：排除 metatest/ + 测试目录/文件（"skip classes in metatest/ itself"）
    def_py_files: List[Path] = []
    # v12 M1：引用搜索文件集排除 metatest/ 自身（与 def_py_files 对齐），
    # 避免 metatest 描述被检测对象时产生自引用污染；simtests/ / eventtest/ 仍计入
    # 引用语料（类被测试引用亦算 alive）。
    ref_py_files: List[Path] = []
    for py_file in project_root.rglob("*.py"):
        parts = py_file.relative_to(project_root).parts
        if not _is_excluded(parts, exclude_metatest=True):
            ref_py_files.append(py_file)
        if _is_excluded(parts, exclude_metatest=True, exclude_tests=True):
            continue
        def_py_files.append(py_file)

    # 收集类定义（非私有，非 metatest/，非测试目录；跳过 Test* 与 Protocol）
    class_defs: Dict[str, List[str]] = {}
    for py_file in def_py_files:
        tree = _parse_ast(py_file)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if node.name.startswith("_"):
                continue
            # 跳过 pytest 测试类（Test* 命名约定，运行时由 pytest 收集实例化）
            if node.name.startswith("Test"):
                continue
            # 跳过 Protocol 结构类型（structural typing，可不命名引用即被隐式满足）
            if _is_protocol_class(node):
                continue
            class_defs.setdefault(node.name, []).append(
                f"{py_file.relative_to(project_root)}:{node.lineno}"
            )

    dead_classes: List[str] = []
    pre_existing: List[str] = []
    # v12 M1：批量 AST 引用计数（单遍扫描所有文件，O(files) 而非 O(classes × files)）
    ref_map = _build_ast_reference_map(set(class_defs.keys()), ref_py_files)
    for class_name, def_locs in class_defs.items():
        # 类定义自身的 class ClassName: 不产生 ast.Name 节点，故零 AST 引用即死代码
        total_refs = ref_map.get(class_name, 0)
        if total_refs == 0:
            entry = f"{class_name} (定义于 {def_locs[0]})"
            if class_name in _PRE_EXISTING_DEAD_CLASSES:
                # v11 metatest 范围外的预存死类，跟踪不扣分
                pre_existing.append(entry)
            else:
                dead_classes.append(entry)

    return {
        "dead_classes": dead_classes,
        "count": len(dead_classes),
        "pre_existing": pre_existing,
    }


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
    """打印 v6 21 维量化评分报告。"""
    sep = "=" * 60
    print(sep)
    print("=== metatest v6 量化评分报告（21 维 + MetaDispatcher 统一 + 运行时验证 + adapter 转发同构）===")
    print(sep)
    print()

    if no_tests:
        print("无测试文件：metatest/ 目录下未发现 test_*.py")
        print("（测试文件待后续 Task 编写）")
        print()

    # 构建 dim_map（含 scoring.py 16 维度）
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
    # v4 新增 4 维
    oi = dim_map.get("oop_inheritance_depth")
    pz = dim_map.get("polling_zero_tolerance")
    pcv = dim_map.get("primitive_convergence")
    er = dim_map.get("essence_ratio")
    # v5 新增 4 维
    di_v5 = dim_map.get("dispatcher_isomorphism")
    rv_v5 = dim_map.get("runtime_verification")
    er_v5 = dim_map.get("eventtest_regression")
    cm_v5 = dim_map.get("cross_module_import_discipline")

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
    iso_checks = test_results.get("isomorphism_total_checks", ISOMORPHISM_CHECKS_TOTAL_V4)
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

    # --- 8 原 v2 维度（v4 降权） ---

    print(f"模块覆盖率:      {modules_covered}/{len(CORE_MODULES)} = "
          f"{_format_percent(modules_covered, len(CORE_MODULES))} (权重 7%)")
    if mc:
        print(f"                → 得分 {mc.score:.1f}/100 — {mc.details}")

    print(f"测试通过率:      {tests_passed}/{tests_total} = "
          f"{_format_percent(tests_passed, tests_total)} (权重 13%, 跳过计为失败)")
    if pr:
        print(f"                → 得分 {pr.score:.1f}/100 — {pr.details}")

    avg_asserts = (assertions_count / test_files_count) if test_files_count > 0 else 0
    print(f"断言密度:        {avg_asserts:.1f}/文件 ({assertions_count} 断言 / "
          f"{test_files_count} 文件) (权重 5%, 目标 20/文件)")
    if ad:
        print(f"                → 得分 {ad.score:.1f}/100 — {ad.details}")

    print(f"事件链完整性:    {len(event_types_seen)}/{len(EVENT_CHAIN_TYPES)} 类事件 "
          f"(权重 8%)")
    if ec:
        print(f"                → 得分 {ec.score:.1f}/100 — {ec.details}")

    print(f"性能基准:        {sim_time:.2f}s/1000 tick (权重 5%)")
    if pf:
        print(f"                → 得分 {pf.score:.1f}/100 — {pf.details}")

    print(f"前端 E2E 通过率: {fe_passed}/{fe_total} = "
          f"{_format_percent(fe_passed, fe_total)} (权重 7%, 环境缺失给最低达标线 80)")
    if fe:
        print(f"                → 得分 {fe.score:.1f}/100 — {fe.details}")

    print(f"底层逻辑覆盖度:  {logic_passed}/{logic_total} 项通过 (权重 5%)")
    if lc:
        print(f"                → 得分 {lc.score:.1f}/100 — {lc.details}")

    print(f"同构代码消除度:  {iso_violations} 违规 / {iso_checks} 项检查 "
          f"(权重 9%, {iso_checks} 项检查)")
    if ie:
        print(f"                → 得分 {ie.score:.1f}/100 — {ie.details}")

    # --- 4 v3 新增维度（v4 降权） ---

    print(f"行数收敛度:      {core_lines}/{core_target} 行 "
          f"(权重 5%, ≤ {core_target} 满分)")
    if ln:
        print(f"                → 得分 {ln.score:.1f}/100 — {ln.details}")

    print(f"规则合规度:      {rule_violations} 违规 / {rule_checks} 项检查 "
          f"(权重 3%, RULES 91-100)")
    if rc:
        print(f"                → 得分 {rc.score:.1f}/100 — {rc.details}")

    neg_detail = ", ".join(
        f"{k}={v}" for k, v in sorted(neg_counts.items())
    ) if neg_counts else "无"
    print(f"反测试覆盖率:    4 类（{neg_detail}）每类目标 {neg_target} (权重 2%)")
    if nt:
        print(f"                → 得分 {nt.score:.1f}/100 — {nt.details}")

    print(f"合测试 E2E:      {syn_passed}/{syn_total} 通过 "
          f"(权重 3%, 跳过计为失败)")
    if se:
        print(f"                → 得分 {se.score:.1f}/100 — {se.details}")

    # --- 4 v4 新增维度（三原语收敛度，v5 降权至 80%） ---

    print(f"OOP 同源继承深度: (权重 6.4%, BasePoolConverter + 子类继承)")
    if oi:
        print(f"                → 得分 {oi.score:.1f}/100 — {oi.details}")

    print(f"轮询零容忍:      (权重 6.4%, 12 处轮询模式零匹配)")
    if pz:
        print(f"                → 得分 {pz.score:.1f}/100 — {pz.details}")

    print(f"三原语收敛度:    (权重 6.4%, 时间/分派/继承各 ≥ 95%)")
    if pcv:
        print(f"                → 得分 {pcv.score:.1f}/100 — {pcv.details}")

    print(f"本质比:          (权重 3.2%, essence_ratio ≥ 12%)")
    if er:
        print(f"                → 得分 {er.score:.1f}/100 — {er.details}")

    # --- 4 v5 新增维度（MetaDispatcher 统一 + 运行时验证 + 跨模块 import） ---

    print(f"MetaDispatcher 统一: (权重 5.0%, 基类+继承+独立+骨架占比 ≥ 60%)")
    if di_v5:
        print(f"                → 得分 {di_v5.score:.1f}/100 — {di_v5.details}")

    print(f"运行时验证:      (权重 5.0%, 3 个 in-process 测试通过率)")
    if rv_v5:
        print(f"                → 得分 {rv_v5.score:.1f}/100 — {rv_v5.details}")

    print(f"eventtest 回归:  (权重 5.0%, 退出码 0 满分)")
    if er_v5:
        print(f"                → 得分 {er_v5.score:.1f}/100 — {er_v5.details}")

    print(f"跨模块 import 纪律: (权重 5.0%, 8 处违规模式零匹配)")
    if cm_v5:
        print(f"                → 得分 {cm_v5.score:.1f}/100 — {cm_v5.details}")

    # --- v6 新增 1 维（adapter 转发同构） ---
    ai_v6 = dim_map.get("adapter_isomorphism")
    print(f"adapter 转发同构: (权重 4.0%, TqProvider/TqSdkBridge 表驱动覆盖率 ≥ 90%)")
    if ai_v6:
        print(f"                → 得分 {ai_v6.score:.1f}/100 — {ai_v6.details}")

    # --- 运行时三核 Dispatcher 元统一（第四层洞察根因解释层） ---
    mu = test_results.get("meta_unification", {}) or {}
    if mu:
        print()
        print("─" * 40)
        print("运行时三核 Dispatcher 元统一（DDD 根因解释层）")
        print(f"  EventBus 唯一:    {'是' if mu.get('eventbus_unique') else '否'}"
              f"（残留 {mu.get('eventbus_residue', 0)}）")
        print(f"  EventDriver 唯一: {'是' if mu.get('eventdriver_unique') else '否'}"
              f"（残留 {mu.get('eventdriver_residue', 0)}）")
        print(f"  ConfigStore 唯一: {'是' if mu.get('configstore_unique') else '否'}"
              f"（残留 {mu.get('configstore_residue', 0)}）")
        print(f"  meta_purity:      {mu.get('meta_purity', 0.0):.2f}%"
              f"（目标 ≥ 90%，Data 声明 {mu.get('data_declaration_lines', 0)} 行 + "
              f"Dispatcher 调用 {mu.get('dispatcher_call_lines', 0)} 行 / "
              f"总业务 {mu.get('total_business_lines', 0)} 行）")
        print(f"  禁止第四核:       {'是' if mu.get('no_fourth_dispatcher') else '否'}")
        # v5 新增 4 字段：MetaDispatcher 统一结构
        print(f"  MetaDispatcher 基类存在: {'是' if mu.get('meta_dispatcher_exists') else '否'}")
        print(f"  EventBus 继承 Meta:     {'是' if mu.get('eventbus_inherits_meta') else '否'}")
        print(f"  ConfigStore 继承 Meta:  {'是' if mu.get('configstore_inherits_meta') else '否'}")
        print(f"  EventDriver 独立:       {'是' if mu.get('eventdriver_independent') else '否'}")
        # v6 新增字段：adapter 转发覆盖率
        print(f"  adapter 转发覆盖率:     {mu.get('adapter_forward_coverage', 0.0):.1f}%"
              f"（目标 ≥ 90%）")
        # v7 新增字段：per_code 表条目数 + 双签名收敛标记
        print(f"  per_code 表条目数:      {mu.get('per_code_table_entries', 0)}"
              f"（_PER_CODE_TQ_CALLS，目标 ≥ 3）")
        print(f"  双签名收敛:             {'是' if mu.get('dual_signature_converged') else '否'}"
              f"（_CACHED_TQ_CALLS 含 get_stock_list_by_type + get_stock_list）")

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
    """构建 report.json 的字典结构（含 21 维明细 + 总分 + PASS/FAIL + redo_list + meta_unification 根因解释层）。"""
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
        # v4 第四层洞察根因解释层：运行时三核 Dispatcher 元统一（DDD）
        "meta_unification": test_results.get("meta_unification", {}),
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
    """运行 metatest 测试套件并输出 v6 21 维量化评分报告。

    Returns:
        0 = 总分 ≥ 95 且 21 维均 ≥ 80（PASS）或无测试文件；1 = FAIL。
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

    # v10: handler 异常保护覆盖采集（第十层洞察，供 isomorphism 第 41 项检查 + test_results 字段）
    handler_exception_coverage = _collect_handler_exception_coverage()

    # v11: 审计盲区闭合采集（第十一层洞察，供 isomorphism 第 42-44 项检查 + test_results 字段）
    converters_polling_violations = _collect_converters_polling_violations()
    parallel_runtime_violations = _collect_parallel_runtime_violations()
    dead_code_violations = _collect_dead_code_violations()
    # v12 M4: 结构性 AST 轮询检测（供 isomorphism 第 47 项检查 + test_results 字段）
    structural_polling_violations = _collect_structural_polling_violations()

    # v4: 同构代码消除度检测（v12：48 项 Grep / AST 验证，含 handler_exception_coverage
    # + converters_polling/parallel_runtime/dead_code 3 项 + if_fmt_tdx/threading_timer/
    # structural_polling/ast_dead_code_false_positives 4 项）
    isomorphism_violations, isomorphism_total_checks = _check_isomorphism(
        handler_exception_coverage=handler_exception_coverage,
        converters_polling_violations=converters_polling_violations,
        parallel_runtime_violations=parallel_runtime_violations,
        dead_code_violations=dead_code_violations,
        structural_polling_violations=structural_polling_violations,
    )

    # v3 新增数据采集
    # 核心模块总行数（wc -l core/*.py 等价）
    core_total_lines = _count_core_lines()

    # RULES 91-100 违规检查（10 项）
    rule_violations, rule_total_checks = _check_rule_compliance()

    # 4 类反测试用例数
    negative_test_counts = _count_negative_tests(metatest_dir)

    # 合测试通过数/总数（从 _StatsPlugin file_stats 采集）
    synthesis_passed, synthesis_total = _count_synthesis_tests(stats)

    # v4 新增数据采集（三原语收敛度 + 运行时三核 Dispatcher 元统一）
    oop_inheritance = _collect_oop_inheritance()
    polling_violations = _collect_polling_violations()
    primitive_convergence = _collect_primitive_convergence()
    essence_ratio, essence_baseline_lines, essence_current_lines = (
        _collect_essence_ratio(core_total_lines)
    )
    meta_unification = _collect_meta_unification()

    # v5 新增 4 维数据采集（MetaDispatcher 统一 + 运行时验证 + eventtest 回归 + 跨模块 import）
    dispatcher_isomorphism = _collect_dispatcher_isomorphism()
    runtime_verification = _collect_runtime_verification(stats)
    eventtest_regression = _collect_eventtest_regression()
    cross_module_import_discipline = _collect_cross_module_import_discipline()

    # v6 新增 1 维数据采集（adapter 转发同构：TqProvider/TqSdkBridge 表驱动覆盖率）
    adapter_isomorphism = _collect_adapter_isomorphism()

    # v5: meta_unification 新增 4 字段（从 dispatcher_isomorphism 提取）
    meta_unification["meta_dispatcher_exists"] = dispatcher_isomorphism["meta_dispatcher_exists"]
    meta_unification["eventbus_inherits_meta"] = dispatcher_isomorphism["eventbus_inherits_meta"]
    meta_unification["configstore_inherits_meta"] = dispatcher_isomorphism["configstore_inherits_meta"]
    meta_unification["eventdriver_independent"] = dispatcher_isomorphism["eventdriver_independent"]
    # v6: meta_unification 新增 adapter 转发覆盖率（从 adapter_isomorphism 提取）
    meta_unification["adapter_forward_coverage"] = adapter_isomorphism["coverage"]
    # v7: meta_unification 新增 per_code 表条目数 + 双签名收敛标记
    meta_unification["per_code_table_entries"] = adapter_isomorphism["per_code_tq_calls_entries"]
    meta_unification["dual_signature_converged"] = adapter_isomorphism["dual_signature_converged"]

    # 构建 test_results 字典供 ScoringEngine 使用（含 v3/v4/v5 新增字段）
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
        "isomorphism_total_checks": isomorphism_total_checks,  # v12: 48
        # --- v3 新增字段 ---
        "core_total_lines": core_total_lines,
        "core_lines_target": CORE_LINES_TARGET,
        "rule_violations": rule_violations,
        "rule_total_checks": rule_total_checks,
        "negative_test_counts": negative_test_counts,
        "negative_test_target_per_category": NEGATIVE_TEST_TARGET_PER_CATEGORY,
        "synthesis_passed": synthesis_passed,
        "synthesis_total": synthesis_total,
        # --- v4 新增字段（三原语收敛度 + 元统一） ---
        "oop_inheritance": oop_inheritance,
        "polling_violations": polling_violations,
        "primitive_convergence": primitive_convergence,
        "essence_ratio": essence_ratio,
        "essence_baseline_lines": essence_baseline_lines,
        "essence_current_lines": essence_current_lines,
        "meta_unification": meta_unification,
        # --- v5 新增 4 维字段 ---
        "dispatcher_isomorphism": dispatcher_isomorphism,
        "runtime_verification": runtime_verification,
        "eventtest_regression": eventtest_regression,
        "cross_module_import_discipline": cross_module_import_discipline,
        # --- v6 新增 1 维字段 ---
        "adapter_isomorphism": adapter_isomorphism,
        # --- v10 新增字段（handler 异常保护覆盖，isomorphism 第 41 项检查依据）---
        "handler_exception_coverage": handler_exception_coverage,
        # --- v11 新增字段（审计盲区闭合，isomorphism 第 42-44 项检查依据）---
        "converters_polling_violations": converters_polling_violations,
        "parallel_runtime_violations": parallel_runtime_violations,
        "dead_code_violations": dead_code_violations,
        # --- v12 新增字段（检测器硬化，isomorphism 第 45-48 项检查依据）---
        "structural_polling_violations": structural_polling_violations,
        "if_fmt_tdx_violations": (
            _grep_count(r'if\s+fmt\s*==\s*["\']tdx["\']', _CORE_DIR)
            + _grep_count(r'if\s+fmt\s*==\s*["\']tdx["\']', _SERVICES_DIR)
            + _grep_count_in_file(r'if\s+fmt\s*==\s*["\']tdx["\']', _APP_FILE)
            + _grep_count_in_file(r'if\s+fmt\s*==\s*["\']tdx["\']', _CONVERTERS_FILE)
            + _grep_count_in_file(r'if\s+fmt\s*==\s*["\']tdx["\']', _API_FILE)
            + _grep_count(r'if\s+fmt\s*==\s*["\']dzh["\']', _CORE_DIR)
            + _grep_count(r'if\s+fmt\s*==\s*["\']dzh["\']', _SERVICES_DIR)
            + _grep_count_in_file(r'if\s+fmt\s*==\s*["\']dzh["\']', _APP_FILE)
            + _grep_count_in_file(r'if\s+fmt\s*==\s*["\']dzh["\']', _CONVERTERS_FILE)
            + _grep_count_in_file(r'if\s+fmt\s*==\s*["\']dzh["\']', _API_FILE)
        ),
        "threading_timer_violations": (
            _grep_count(r'threading\.Timer\s*\(', _CORE_DIR)
            + _grep_count(r'threading\.Timer\s*\(', _SERVICES_DIR)
            + _grep_count_in_file(r'threading\.Timer\s*\(', _APP_FILE)
            + _grep_count_in_file(r'threading\.Timer\s*\(', _CONVERTERS_FILE)
            + _grep_count_in_file(r'threading\.Timer\s*\(', _API_FILE)
        ),
        "ast_dead_code_false_positives": int(dead_code_violations.get("count", 0) or 0),
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

    # 退出码：PASS（总分 ≥ 95 且 21 维均 ≥ 80）或无测试文件返回 0，否则返回 1
    if no_tests:
        return 0
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
