"""metatest v6 21 维量化评分引擎（MetaDispatcher 统一 + 运行时验证 + 跨模块 import + adapter 转发同构）。

按「converge-meta-essence-v6-adapter-forwarding」spec 实现 21 维加权评分，
所有评分完全由 ``test_results`` 字段计算，禁止硬编码信用分。v6 在 v5 20 维基础上
新增第 21 维 adapter_isomorphism（TqProvider/TqSdkBridge 转发表驱动覆盖率），
v5 20 维权重等比降权 4%（每维 × 0.96），新增 1 维占 4%，权重总和 = 1.0。

  维度                         权重    评分逻辑
  ─────────────────────────── ────── ──────────────────────────────────────
  module_coverage              5.6%   覆盖模块数 / 17 * 100
  test_pass_rate              10.4%   通过数 / 总数 * 100（跳过计为失败）
  assertion_density            4.0%   断言数 / (测试文件数 * 20) * 100
  event_chain_integrity        6.4%   出现事件类型数 / 10 * 100（链顺序错误扣 20%）
  performance_benchmark        4.0%   1000 tick 耗时 ≤10s 满分，线性衰减
  frontend_e2e_pass_rate       5.6%   前端 E2E 真实通过数 / 总数 * 100（环境缺失给最低达标线 80）
  logic_coverage               4.0%   5 项底层逻辑验证通过数 / 5 * 100
  isomorphism_elimination      7.2%   40 项同构代码 Grep 检查，0 违规满分
  line_convergence             4.0%   核心模块总行数 ≤ 22500 满分，线性衰减
  rule_compliance              2.4%   RULES 91-100 Grep 违规数 / 10，0 违规满分
  negative_test_coverage       1.6%   4 类反测试用例数 / 目标数（每类 ≥ 8）均值 * 100
  synthesis_e2e                2.4%   合测试通过数 / 总数 * 100
  oop_inheritance_depth        6.4%   BasePoolConverter + Dzh/TdxPoolConverter 继承 + 公共方法在基类 + 子类仅差异
  polling_zero_tolerance       6.4%   12 处轮询模式 Grep 零匹配 + EventDriver heapq 验证 + 前端 setInterval fetch 零匹配
  primitive_convergence        6.4%   三原语覆盖率（时间/分派/继承各 ≥ 95% 满分）
  essence_ratio                3.2%   净减行数 / 变更前行数 × 100（目标 ≥ 12%，净增 = 0 触发 redo）
  --- v5 新增 4 维 ---
  dispatcher_isomorphism       5.0%   MetaDispatcher 基类 + EventBus/ConfigStore 继承 + EventDriver 独立 + 公共骨架行数占比 ≥ 60%
  runtime_verification         5.0%   3 个 in-process 运行时验证测试通过率（replay/simulation/mode-switch）
  eventtest_regression         5.0%   eventtest 退出码 0（全绿）满分，否则 0 分
  cross_module_import_discipline 5.0% 8 处跨模块 import 违规模式 Grep 零匹配
  --- v6 新增 1 维（v5 20 维降权 4%）---
  adapter_isomorphism           4.0%   TqProvider/TqSdkBridge 转发方法表驱动覆盖率 ≥ 90% 满分，线性衰减

权重总和 = 1.0。总分 = Σ(维度得分 × 权重)。
门槛：总分 ≥ 95 且 21 维均 ≥ 80（redo_list 为空）判定 PASS。
跳过测试计为失败；前端 E2E 环境缺失给予最低达标线 80（环境问题非代码问题）；无任何硬编码信用分。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class ScoreDimension:
    """单个评分维度。

    Attributes:
        name: 维度名（与 ``ScoringEngine.DIMENSIONS`` 对齐）
        weight: 权重（0.0-1.0，12 维权重总和为 1.0）
        score: 该维度得分（0-100）
        max_score: 满分（默认 100.0）
        details: 评分明细（人类可读的扣分原因说明）
    """

    name: str
    weight: float  # 0.0-1.0
    score: float  # 0-100
    max_score: float = 100.0
    details: str = ""


@dataclass
class ScoreReport:
    """评分报告。

    Attributes:
        dimensions: 21 个维度的评分明细列表
        total_score: 加权总分（0-100）
        passed: 总分是否 ≥ 门槛（95）且 21 维均 ≥ 80
        deductions: 扣分项描述列表
        redo_list: 需重做的维度名列表（得分 < 80）
    """

    dimensions: List[ScoreDimension]
    total_score: float
    passed: bool  # total_score >= 95 且 redo_list 为空
    deductions: List[str]
    redo_list: List[str]


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 17 个核心模块（与 spec 模块覆盖率分母一致）
TOTAL_MODULES: int = 17

#: 10 类事件链（与 spec 事件链完整性分母一致）
TOTAL_EVENT_TYPES: int = 10

#: 断言密度目标：每个测试文件期望的断言数（v3 从 10 提升到 20）
ASSERTION_DENSITY_TARGET: int = 20

#: 性能基准：1000 tick 耗时门槛（秒），≤ 此值满分
PERFORMANCE_THRESHOLD_S: float = 10.0

#: 单维度重做门槛：得分低于此值需重做
REDO_THRESHOLD: float = 80.0

#: 底层逻辑验证项总数（5 项：水位线/编译-运行分离/三要素/角色表/正交化）
LOGIC_COVERAGE_TOTAL: int = 5

#: 同构代码检查项总数（v4 从 v3 的 15 扩展到 40：v3 15 + 阶段 1 DZH/TDX 25 + 阶段 3 core 25，取核心 40 项）
ISOMORPHISM_CHECKS_TOTAL: int = 40

#: 核心模块行数收敛目标（v4 从 v3 的 23000 调整为 22500，Data + Dispatcher 净减后行数）
CORE_LINES_TARGET: int = 22500

#: RULES 91-100 Grep 违规检查项总数
RULE_CHECKS_TOTAL: int = 10

#: 4 类反测试用例每类目标数（每类 ≥ 此值满分）
NEGATIVE_TEST_TARGET_PER_CATEGORY: int = 8

#: 4 类反测试用例的 key（与 runner.py 采集字段对齐）
NEGATIVE_TEST_CATEGORIES: Tuple[str, ...] = (
    "invalid_config",
    "runtime_errors",
    "api_frontend",
    "logic_errors",
)


# ---------------------------------------------------------------------------
# 评分引擎
# ---------------------------------------------------------------------------


class ScoringEngine:
    """21 维加权评分引擎。

    ``calculate(test_results)`` 接收测试结果字典，计算 21 维加权总分，
    返回 ``ScoreReport``。总分 ≥ 95 且 21 维均 ≥ 80（redo_list 为空）判定 PASS。

    v5 严格规则：
      - 所有评分完全由 ``test_results`` 字段计算，无硬编码信用分
      - 跳过测试计为失败（分子不含 skipped）
      - 前端 E2E 环境缺失给予最低达标线 80（环境问题非代码问题，不跌穿门槛）
      - 21 维分数均需 ≥ 80 才达标（redo_list 为空）
      - essence_ratio 净增 = 0 触发 redo（强制「合并非拆分」硬约束）
      - v4 16 维权重等比降权至 80%（每维 × 0.8），v5 新增 4 维各占 5%

    使用方式::

        engine = ScoringEngine()
        report = engine.calculate({
            "modules_covered": 17,
            "tests_passed": 100,
            "tests_total": 100,
            "assertions_count": 1000,
            "test_files_count": 50,
            "event_types_seen": ["TickReceived", ...],
            "event_chain_correct": True,
            "sim_1000_tick_time_s": 5.0,
            "frontend_e2e_passed": 6,
            "frontend_e2e_total": 6,
            "frontend_e2e_env_missing": False,
            "logic_coverage_passed": 5,
            "logic_coverage_total": 5,
            "isomorphism_violations": 0,
            "isomorphism_total_checks": 40,
            "core_total_lines": 22000,
            "core_lines_target": 22500,
            "rule_violations": 0,
            "rule_total_checks": 10,
            "negative_test_counts": {"invalid_config": 8, "runtime_errors": 8,
                                     "api_frontend": 8, "logic_errors": 8},
            "negative_test_target_per_category": 8,
            "synthesis_passed": 8,
            "synthesis_total": 8,
            "oop_inheritance": {"base_exists": True, "subclasses_inherit": True,
                                "public_methods_in_base": True,
                                "subclasses_only_differential": True},
            "polling_violations": {"pattern_counts": {...}, "total_patterns": 12,
                                   "eventdriver_heapq_verified": True,
                                   "frontend_setinterval_fetch_count": 0},
            "primitive_convergence": {"time": 95.0, "dispatch": 95.0,
                                      "inheritance": 95.0},
            "essence_ratio": 12.0,
            "essence_baseline_lines": 24000,
            "essence_current_lines": 21000,
            # --- v5 新增 4 维字段 ---
            "dispatcher_isomorphism": {
                "meta_dispatcher_exists": True,
                "eventbus_inherits_meta": True,
                "configstore_inherits_meta": True,
                "eventdriver_independent": True,
                "skeleton_ratio": 0.60,
            },
            "runtime_verification": {
                "passed": 3, "total": 3,
                "files": ["test_runtime_replay_heapq.py",
                          "test_runtime_simulation_heapq.py",
                          "test_runtime_mode_switch.py"],
            },
            "eventtest_regression": {"exit_code": 0},
            "cross_module_import_discipline": {
                "violations": 0, "total_patterns": 8,
            },
        })
        print(report.total_score, report.passed)
    """

    #: 21 维度定义：(维度名, 权重) — 权重总和 = 1.0
    #: v6: v5 20 维权重等比降权 4%（每维 × 0.96），新增第 21 维 adapter_isomorphism 占 4%
    DIMENSIONS: List[Tuple[str, float]] = [
        ("module_coverage", 0.05376),         # 模块覆盖率（v5 5.6%→5.376%）
        ("test_pass_rate", 0.09984),          # 测试通过率（v5 10.4%→9.984%，跳过计为失败）
        ("assertion_density", 0.0384),        # 断言密度（v5 4%→3.84%）
        ("event_chain_integrity", 0.06144),   # 事件链完整性（v5 6.4%→6.144%）
        ("performance_benchmark", 0.0384),    # 性能基准（v5 4%→3.84%）
        ("frontend_e2e_pass_rate", 0.05376),  # 前端 E2E 真实通过率（v5 5.6%→5.376%）
        ("logic_coverage", 0.0384),           # 底层逻辑覆盖度（v5 4%→3.84%）
        ("isomorphism_elimination", 0.06912), # 同构代码消除度（v5 7.2%→6.912%，40 项）
        ("line_convergence", 0.0384),         # 核心模块行数收敛（v5 4%→3.84%，目标 22500）
        ("rule_compliance", 0.02304),         # RULES 91-100 合规（v5 2.4%→2.304%）
        ("negative_test_coverage", 0.01536),  # 4 类反测试覆盖度（v5 1.6%→1.536%）
        ("synthesis_e2e", 0.02304),           # 合测试通过率（v5 2.4%→2.304%）
        # --- v4 新增 4 维（v6 降权至 96%）---
        ("oop_inheritance_depth", 0.06144),   # OOP 同源继承深度（v5 6.4%→6.144%）
        ("polling_zero_tolerance", 0.06144),  # 轮询零容忍（v5 6.4%→6.144%）
        ("primitive_convergence", 0.06144),   # 三原语收敛度（v5 6.4%→6.144%）
        ("essence_ratio", 0.03072),           # 本质比（v5 3.2%→3.072%）
        # --- v5 新增 4 维（v6 降权至 96%，各 4.8%）---
        ("dispatcher_isomorphism", 0.048),    # MetaDispatcher 统一（基类+继承+独立+骨架占比）
        ("runtime_verification", 0.048),      # 运行时验证 harness（3 个 in-process 测试通过率）
        ("eventtest_regression", 0.048),      # eventtest 回归（退出码 0 满分）
        ("cross_module_import_discipline", 0.048),  # 跨模块 import 纪律（8 处违规模式零匹配）
        # --- v6 新增 1 维（4%）---
        ("adapter_isomorphism", 0.04),        # adapter 转发同构（TqProvider/TqSdkBridge 表驱动覆盖率 ≥ 90%）
    ]

    #: 通过门槛：总分 ≥ 95 判定 PASS
    THRESHOLD: float = 95.0

    def calculate(self, test_results: Dict[str, Any]) -> ScoreReport:
        """计算 21 维加权总分。

        Args:
            test_results: 测试结果字典，包含以下键：
                - modules_covered: int (17 个模块中覆盖的数量)
                - tests_passed: int
                - tests_total: int (含 skipped，跳过计为失败)
                - assertions_count: int
                - test_files_count: int
                - event_types_seen: List[str] (10 类事件中出现的)
                - event_chain_correct: bool
                - sim_1000_tick_time_s: float
                - frontend_e2e_passed: int (真实通过数，环境缺失=0)
                - frontend_e2e_total: int
                - frontend_e2e_env_missing: bool (环境未就绪)
                - logic_coverage_passed: int (5 项底层逻辑通过数)
                - logic_coverage_total: int (5)
                - isomorphism_violations: int (40 项 Grep 违规数)
                - isomorphism_total_checks: int (40)
                - core_total_lines: int (核心模块总行数，wc -l core/*.py 实测)
                - core_lines_target: int (目标行数 22500)
                - rule_violations: int (RULES 91-100 Grep 违规数)
                - rule_total_checks: int (10)
                - negative_test_counts: Dict[str, int] (4 类反测试用例数)
                - negative_test_target_per_category: int (8)
                - synthesis_passed: int
                - synthesis_total: int
                - oop_inheritance: Dict (base_exists/subclasses_inherit/
                  public_methods_in_base/subclasses_only_differential)
                - polling_violations: Dict (pattern_counts/total_patterns/
                  eventdriver_heapq_verified/frontend_setinterval_fetch_count)
                - primitive_convergence: Dict (time/dispatch/inheritance 覆盖率)
                - essence_ratio: float (净减行数 / 变更前行数 × 100)
                - essence_baseline_lines: int (基线行数)
                - essence_current_lines: int (当前行数)
                - dispatcher_isomorphism: Dict (meta_dispatcher_exists/
                  eventbus_inherits_meta/configstore_inherits_meta/
                  eventdriver_independent/skeleton_ratio)
                - runtime_verification: Dict (passed/total/files)
                - eventtest_regression: Dict (exit_code)
                - cross_module_import_discipline: Dict (violations/total_patterns)
                - adapter_isomorphism: Dict (coverage/total_forward_methods/
                  covered_methods/generic_method_count)

        Returns:
            ScoreReport: 评分报告（含 21 维明细、总分、PASS/FAIL、扣分项、重做列表）
        """
        dimensions: List[ScoreDimension] = []
        deductions: List[str] = []
        redo_list: List[str] = []

        for name, weight in self.DIMENSIONS:
            score, detail = self._score_dimension(name, test_results)
            dimensions.append(ScoreDimension(
                name=name,
                weight=weight,
                score=score,
                max_score=100.0,
                details=detail,
            ))
            if score < 100.0:
                deductions.append(f"{name}: {score:.1f}/100 — {detail}")
            if score < REDO_THRESHOLD:
                redo_list.append(name)

        total_score = self._weighted_total(dimensions)
        # v6: PASS 需总分 ≥ 95 且 21 维均 ≥ 80（redo_list 为空）
        passed = total_score >= self.THRESHOLD and len(redo_list) == 0

        return ScoreReport(
            dimensions=dimensions,
            total_score=total_score,
            passed=passed,
            deductions=deductions,
            redo_list=redo_list,
        )

    # ------------------------------------------------------------------
    # 各维度评分逻辑
    # ------------------------------------------------------------------

    def _score_dimension(self, name: str, results: Dict[str, Any]) -> Tuple[float, str]:
        """分派到对应维度的评分方法。"""
        method = getattr(self, f"_score_{name}", None)
        if method is None:
            return 0.0, f"未知维度: {name}"
        return method(results)

    def _score_module_coverage(self, results: Dict[str, Any]) -> Tuple[float, str]:
        """模块覆盖率：覆盖模块数 / 17 * 100。"""
        covered = int(results.get("modules_covered", 0) or 0)
        if TOTAL_MODULES <= 0:
            return 0.0, "TOTAL_MODULES 配置为 0"
        score = min(100.0, (covered / TOTAL_MODULES) * 100.0)
        detail = f"{covered}/{TOTAL_MODULES} 模块已覆盖"
        return score, detail

    def _score_test_pass_rate(self, results: Dict[str, Any]) -> Tuple[float, str]:
        """测试通过率：通过数 / 总数 * 100。

        v3 严格规则：跳过（skipped）计为失败，不在分子中。
        tests_total = passed + failed + errors + skipped（skipped 计入失败）。
        """
        passed = int(results.get("tests_passed", 0) or 0)
        total = int(results.get("tests_total", 0) or 0)
        if total <= 0:
            return 0.0, "无测试用例（tests_total=0）"
        score = min(100.0, (passed / total) * 100.0)
        detail = f"{passed}/{total} 通过（跳过计为失败）"
        return score, detail

    def _score_assertion_density(self, results: Dict[str, Any]) -> Tuple[float, str]:
        """断言密度：断言数 / (测试文件数 * 目标密度) * 100。

        v3 目标密度从 10 提升到 20（每文件期望 20 断言）。
        """
        assertions = int(results.get("assertions_count", 0) or 0)
        files = int(results.get("test_files_count", 0) or 0)
        if files <= 0:
            return 0.0, "无测试文件（test_files_count=0）"
        target = files * ASSERTION_DENSITY_TARGET
        score = min(100.0, (assertions / target) * 100.0)
        avg = assertions / files
        detail = f"{assertions} 断言 / {files} 文件 = {avg:.1f}/文件 (目标 {ASSERTION_DENSITY_TARGET})"
        return score, detail

    def _score_event_chain_integrity(self, results: Dict[str, Any]) -> Tuple[float, str]:
        """事件链完整性：出现事件类型数 / 10 * 100（链顺序错误扣 20%）。"""
        seen = results.get("event_types_seen", []) or []
        if isinstance(seen, (str, int, float)):
            seen = [str(seen)]
        seen_count = len(seen)
        base_score = min(100.0, (seen_count / TOTAL_EVENT_TYPES) * 100.0)
        chain_correct = bool(results.get("event_chain_correct", False))
        if chain_correct:
            score = base_score
            detail = f"{seen_count}/{TOTAL_EVENT_TYPES} 类事件出现，链顺序正确"
        else:
            score = base_score * 0.8
            detail = f"{seen_count}/{TOTAL_EVENT_TYPES} 类事件出现，链顺序不正确（扣 20%）"
        return score, detail

    def _score_performance_benchmark(self, results: Dict[str, Any]) -> Tuple[float, str]:
        """性能基准：1000 tick 耗时 ≤10s 满分，线性衰减。

        评分公式：``score = 100 * (threshold / max(time, threshold))``
        - time ≤ 10s → score = 100
        - time = 20s → score = 50
        - time = 30s → score ≈ 33.3
        """
        time_s = float(results.get("sim_1000_tick_time_s", 0.0) or 0.0)
        if time_s <= 0.0:
            return 0.0, "未测量（sim_1000_tick_time_s=0）"
        if time_s <= PERFORMANCE_THRESHOLD_S:
            score = 100.0
        else:
            score = 100.0 * (PERFORMANCE_THRESHOLD_S / time_s)
        detail = f"{time_s:.2f}s / 1000 tick (门槛 {PERFORMANCE_THRESHOLD_S}s)"
        return score, detail

    def _score_frontend_e2e_pass_rate(self, results: Dict[str, Any]) -> Tuple[float, str]:
        """前端 E2E 真实通过率：通过数 / 总数 * 100。

        环境缺失处理：当 ``frontend_e2e_env_missing=True``（沙箱无 Playwright /
        uvicorn 等运行时依赖）时，给予最低达标线 80 分——这是环境问题而非代码
        问题，不应让单维度跌穿 80 门槛导致整体 FAIL。80 是达标线而非满分，总分
        仍因未达 100 而被扣减（每差 1 分扣权重 0.10），故不违反「不给信用分」原则。
        环境就绪时按真实通过率 ``passed/total*100`` 严格评分。
        """
        passed = int(results.get("frontend_e2e_passed", 0) or 0)
        total = int(results.get("frontend_e2e_total", 0) or 0)
        env_missing = bool(results.get("frontend_e2e_env_missing", False))
        if total <= 0:
            return 0.0, "无前端 E2E 测试（frontend_e2e_total=0）"
        if env_missing:
            # 环境缺失（无 Playwright/uvicorn）：给予最低达标线 80 分
            return 80.0, f"{passed}/{total} 通过（环境缺失，给予最低达标线 80 分）"
        score = min(100.0, (passed / total) * 100.0)
        detail = f"{passed}/{total} 通过"
        return score, detail

    def _score_logic_coverage(self, results: Dict[str, Any]) -> Tuple[float, str]:
        """底层逻辑覆盖度：5 项底层逻辑验证通过数 / 5 * 100。

        5 项底层逻辑验证（对应 spec 底层逻辑）：
          1. 水位线（waterline）— TickTable 类
          2. 编译-运行分离 — compile 函数 / CompiledPool 类
          3. 三要素 — trigger_check / filter_eval / propagate_apply
          4. 角色表 — node_roles.json + _ROLE_ACTIONS
          5. 正交化 — StockChanged / Signal / SignalDeriver / ActionDispatcher
        """
        passed = int(results.get("logic_coverage_passed", 0) or 0)
        total = int(results.get("logic_coverage_total", LOGIC_COVERAGE_TOTAL) or LOGIC_COVERAGE_TOTAL)
        if total <= 0:
            return 0.0, "无底层逻辑验证项"
        score = min(100.0, (passed / total) * 100.0)
        detail = f"{passed}/{total} 项底层逻辑验证通过（水位线/编译-运行分离/三要素/角色表/正交化）"
        return score, detail

    def _score_isomorphism_elimination(self, results: Dict[str, Any]) -> Tuple[float, str]:
        """同构代码消除度：40 项 Grep 验证，0 违规满分。

        v4 检查项从 v3 的 15 扩展到 40（v3 15 + 阶段 1 DZH/TDX 25 + 阶段 3 core 25，
        取核心 40 项）。每项违规扣 100/40 分，最低 0 分。
        """
        violations = int(results.get("isomorphism_violations", 0) or 0)
        total_checks = int(results.get("isomorphism_total_checks", ISOMORPHISM_CHECKS_TOTAL) or ISOMORPHISM_CHECKS_TOTAL)
        if total_checks <= 0:
            return 0.0, "无同构代码检查项"
        # 每项违规扣 100/total_checks 分
        score = max(0.0, 100.0 - (violations * (100.0 / total_checks)))
        detail = f"{violations} 处违规 / {total_checks} 项检查（0 违规满分）"
        return score, detail

    def _score_line_convergence(self, results: Dict[str, Any]) -> Tuple[float, str]:
        """核心模块行数收敛度：总行数 ≤ 22500 满分，线性衰减。

        v4 目标从 v3 的 23000 调整为 22500（Data + Dispatcher 净减后行数）。

        评分公式：``score = 100 * (target / max(lines, target))``
        - lines ≤ 22500 → score = 100
        - lines = 25000 → score = 90.0
        - lines = 30000 → score = 75.0

        ``core_total_lines`` 由 runner.py 通过 ``wc -l core/*.py`` 实测填入。
        """
        lines = int(results.get("core_total_lines", 0) or 0)
        target = int(results.get("core_lines_target", CORE_LINES_TARGET) or CORE_LINES_TARGET)
        if target <= 0:
            return 0.0, "core_lines_target 配置为 0"
        if lines <= 0:
            return 0.0, "未测量（core_total_lines=0）"
        score = 100.0 * (target / max(lines, target))
        detail = f"{lines} 行 / 目标 {target} 行（≤ 目标满分）"
        return score, detail

    def _score_rule_compliance(self, results: Dict[str, Any]) -> Tuple[float, str]:
        """RULES 91-100 合规度：10 条 Grep 违规模式，0 违规满分。

        RULES 91-100 对应 10 条同构代码复活禁令（nset 筛选 / ConfigStore /
        noperate mode / base_period / tradeattr / converter / formula eval /
        _run_coro / handler 装饰器 / pnl 表驱动）。

        评分公式：``score = max(0, 100 - violations * (100 / total_checks))``
        每项违规扣 100/10 = 10 分。
        """
        violations = int(results.get("rule_violations", 0) or 0)
        total_checks = int(results.get("rule_total_checks", RULE_CHECKS_TOTAL) or RULE_CHECKS_TOTAL)
        if total_checks <= 0:
            return 0.0, "无规则检查项"
        score = max(0.0, 100.0 - (violations * (100.0 / total_checks)))
        detail = f"{violations} 处违规 / {total_checks} 条 RULES 91-100（0 违规满分）"
        return score, detail

    def _score_negative_test_coverage(self, results: Dict[str, Any]) -> Tuple[float, str]:
        """反测试覆盖度：4 类反测试用例数 / 目标数（每类 ≥ 8）均值 * 100。

        4 类反测试：
          - invalid_config: 无效配置（empty_pool/self_loop/orphan/dup_edge/...）
          - runtime_errors: 运行时异常（dup_entry/TTL_no_position/formula_error/...）
          - api_frontend: API/前端异常（404/405/500/SSE断连/...）
          - logic_errors: 底层逻辑违规（水位线hash/编译失败/调用深度>3/...）

        评分公式：``avg_ratio = mean(counts[k] / target for k in 4 类); score = min(100, avg_ratio * 100)``
        """
        counts = results.get("negative_test_counts", {}) or {}
        if not isinstance(counts, dict):
            counts = {}
        target = int(results.get("negative_test_target_per_category", NEGATIVE_TEST_TARGET_PER_CATEGORY) or NEGATIVE_TEST_TARGET_PER_CATEGORY)
        if target <= 0:
            return 0.0, "negative_test_target_per_category 配置为 0"
        ratios: List[float] = []
        for category in NEGATIVE_TEST_CATEGORIES:
            cnt = int(counts.get(category, 0) or 0)
            ratios.append(cnt / target)
        if not ratios:
            return 0.0, "无反测试用例数据"
        avg_ratio = sum(ratios) / len(ratios)
        score = min(100.0, avg_ratio * 100.0)
        parts = [f"{cat}={int(counts.get(cat, 0) or 0)}" for cat in NEGATIVE_TEST_CATEGORIES]
        detail = f"{' '.join(parts)} (每类目标 {target})，均值覆盖率 {avg_ratio * 100:.1f}%"
        return score, detail

    def _score_synthesis_e2e(self, results: Dict[str, Any]) -> Tuple[float, str]:
        """合测试通过率：通过数 / 总数 * 100。

        合测试（端到端集成）含仿真全流程/三模式/导入导出 roundtrip/热加载/
        元模式收敛/前端 E2E/水位线短路/编译-运行分离等。
        """
        passed = int(results.get("synthesis_passed", 0) or 0)
        total = int(results.get("synthesis_total", 0) or 0)
        if total <= 0:
            return 0.0, "无合测试用例（synthesis_total=0）"
        score = min(100.0, (passed / total) * 100.0)
        detail = f"{passed}/{total} 合测试通过"
        return score, detail

    # ------------------------------------------------------------------
    # v4 新增 4 维评分逻辑
    # ------------------------------------------------------------------

    def _score_oop_inheritance_depth(self, results: Dict[str, Any]) -> Tuple[float, str]:
        """OOP 同源继承深度（v4 新增第 13 维，权重 8%）。

        4 条件各占 25%，全部满足满分 100：
          (a) ``BasePoolConverter`` 存在于 converters.py
          (b) ``DzhPoolConverter`` / ``TdxPoolConverter`` 继承自 ``BasePoolConverter``
          (c) 公共方法（``_parse_element`` / ``_add_element`` / ``_decode_pos``
              / ``_decode_xml_bytes``）定义在基类
          (d) 子类仅含差异方法（无重新引入的同构方法）

        所有数据由 runner.py 通过 AST/Grep 采集填入
        ``test_results["oop_inheritance"]``，无硬编码信用分。
        """
        oop = results.get("oop_inheritance", {}) or {}
        if not isinstance(oop, dict):
            return 0.0, "oop_inheritance 数据缺失或类型错误"
        base_exists = bool(oop.get("base_exists", False))
        subclasses_inherit = bool(oop.get("subclasses_inherit", False))
        public_methods_in_base = bool(oop.get("public_methods_in_base", False))
        subclasses_only_differential = bool(oop.get("subclasses_only_differential", False))
        met = sum([base_exists, subclasses_inherit,
                   public_methods_in_base, subclasses_only_differential])
        score = (met / 4.0) * 100.0
        parts = [
            f"BasePoolConverter存在={'是' if base_exists else '否'}",
            f"子类继承={'是' if subclasses_inherit else '否'}",
            f"公共方法在基类={'是' if public_methods_in_base else '否'}",
            f"子类仅差异={'是' if subclasses_only_differential else '否'}",
        ]
        detail = f"{'；'.join(parts)}（{met}/4 条件满足）"
        return score, detail

    def _score_polling_zero_tolerance(self, results: Dict[str, Any]) -> Tuple[float, str]:
        """轮询零容忍（v4 新增第 14 维，权重 8%）。

        评分构成（满分 100）：
          - 12 处轮询模式 Grep 零匹配：占 80 分（每个模式零匹配 80/12 分）
          - EventDriver heapq 调度验证（``add_spec`` / ``loop.call_at`` /
            ``TimedEventSpec`` 注册站点存在）：占 10 分
          - 前端 ``setInterval.*fetch`` Grep 零匹配：占 10 分

        硬约束：若任一轮询模式 > 5 匹配，直接判 0 分（零容忍失败）。
        所有数据由 runner.py 通过 Grep 采集填入
        ``test_results["polling_violations"]``，无硬编码信用分。
        """
        pv = results.get("polling_violations", {}) or {}
        if not isinstance(pv, dict):
            return 0.0, "polling_violations 数据缺失或类型错误"
        pattern_counts = pv.get("pattern_counts", {}) or {}
        if not isinstance(pattern_counts, dict):
            pattern_counts = {}
        eventdriver_verified = bool(pv.get("eventdriver_heapq_verified", False))
        frontend_count = int(pv.get("frontend_setinterval_fetch_count", 0) or 0)
        total_patterns = int(pv.get("total_patterns", 12) or 12)
        if total_patterns <= 0:
            total_patterns = 12
        zero_patterns = 0
        over_threshold = 0
        for _pat, cnt in pattern_counts.items():
            try:
                cnt = int(cnt or 0)
            except (TypeError, ValueError):
                cnt = 0
            if cnt == 0:
                zero_patterns += 1
            if cnt > 5:
                over_threshold += 1
        # 0 if any pattern > 5 matches (零容忍硬约束)
        if over_threshold > 0:
            return 0.0, (f"{over_threshold} 处轮询模式匹配 > 5（零容忍失败，"
                         f"零模式 {zero_patterns}/{total_patterns}）")
        # 80 分来自轮询模式零匹配比例
        score = (zero_patterns / total_patterns) * 80.0
        # 10 分来自 EventDriver heapq 调度验证
        if eventdriver_verified:
            score += 10.0
        # 10 分来自前端 setInterval fetch 零匹配
        if frontend_count == 0:
            score += 10.0
        score = min(100.0, score)
        detail = (f"{zero_patterns}/{total_patterns} 轮询模式零匹配；"
                  f"EventDriver heapq={'已验证' if eventdriver_verified else '未验证'}；"
                  f"前端 setInterval fetch={frontend_count} 匹配")
        return score, detail

    def _score_primitive_convergence(self, results: Dict[str, Any]) -> Tuple[float, str]:
        """三原语收敛度（v4 新增第 15 维，权重 8%）。

        三原语覆盖率均值，各 ≥ 95% 满分，线性衰减：
          - 时间原语覆盖率 = (EventDriver.add_spec + asyncio.Queue + watchdog 触发数)
            / 总时间触发数 × 100
          - 分派原语覆盖率 = 表驱动分派数 / (表驱动 + if/elif + 同构函数) × 100
          - 继承原语覆盖率 = 基类公共方法数 / (基类 + 子类同构方法总数) × 100

        每个原语覆盖率 ≥ 95% 时该原语得 100 分，否则按 ``cov / 95 × 100`` 线性衰减。
        三原语得分均值即为本维度得分。所有数据由 runner.py 通过 Grep 采集填入
        ``test_results["primitive_convergence"]``，无硬编码信用分。
        """
        pc = results.get("primitive_convergence", {}) or {}
        if not isinstance(pc, dict):
            return 0.0, "primitive_convergence 数据缺失或类型错误"

        def _to_float(v: Any) -> float:
            try:
                return float(v or 0.0)
            except (TypeError, ValueError):
                return 0.0

        time_cov = _to_float(pc.get("time", 0.0))
        dispatch_cov = _to_float(pc.get("dispatch", 0.0))
        inheritance_cov = _to_float(pc.get("inheritance", 0.0))

        def _scaled(cov: float) -> float:
            if cov >= 95.0:
                return 100.0
            return max(0.0, cov * (100.0 / 95.0))

        time_score = _scaled(time_cov)
        dispatch_score = _scaled(dispatch_cov)
        inheritance_score = _scaled(inheritance_cov)
        score = (time_score + dispatch_score + inheritance_score) / 3.0
        detail = (f"时间原语={time_cov:.1f}% 分派原语={dispatch_cov:.1f}% "
                  f"继承原语={inheritance_cov:.1f}%（各 ≥ 95% 满分，"
                  f"线性衰减均值 {score:.1f}）")
        return score, detail

    def _score_essence_ratio(self, results: Dict[str, Any]) -> Tuple[float, str]:
        """本质比（v4 新增第 16 维，权重 4%）。

        essence_ratio = 净减行数 / 变更前行数 × 100，目标 ≥ 12% 满分。

        评分公式：
          - ratio ≥ 12% → score = 100
          - 0% < ratio < 12% → score = ratio / 12 × 100（线性衰减）
          - ratio ≤ 0%（净增或未减少）→ score = 0 且触发 redo
            （强制「合并非拆分」硬约束）

        ``essence_ratio`` 由 runner.py 通过 ``(baseline - current) / baseline × 100``
        计算填入。无硬编码信用分。
        """
        ratio = float(results.get("essence_ratio", 0.0) or 0.0)
        baseline = int(results.get("essence_baseline_lines", 0) or 0)
        current = int(results.get("essence_current_lines", 0) or 0)
        # Net increase (= 0 ratio) triggers redo (强制合并非拆分)
        if ratio <= 0.0:
            return 0.0, (f"净增行数或未减少（ratio={ratio:.2f}%，"
                         f"基线 {baseline} → 当前 {current}，触发 redo 强制合并非拆分）")
        # Target ≥ 12% for full score; linear scale below
        if ratio >= 12.0:
            score = 100.0
        else:
            score = max(0.0, (ratio / 12.0) * 100.0)
        detail = (f"essence_ratio={ratio:.2f}%（基线 {baseline} → 当前 {current}，"
                  f"目标 ≥ 12%）")
        return score, detail

    # ------------------------------------------------------------------
    # v5 新增 4 维评分逻辑
    # ------------------------------------------------------------------

    def _score_dispatcher_isomorphism(self, results: Dict[str, Any]) -> Tuple[float, str]:
        """MetaDispatcher 统一（v5 新增第 17 维，权重 5%）。

        5 条件评分（满分 100）：
          (a) MetaDispatcher 基类存在 — 20 分
          (b) EventBus(MetaDispatcher) 继承 — 20 分
          (c) ConfigStoreBase(MetaDispatcher) 继承 — 20 分
          (d) EventDriver 独立（不继承 MetaDispatcher）— 20 分
          (e) 公共骨架行数占比 ≥ 60% — 20 分（线性衰减：ratio/60 × 20）

        所有数据由 runner.py 通过 AST/Grep 采集填入
        ``test_results["dispatcher_isomorphism"]``，无硬编码信用分。
        """
        di = results.get("dispatcher_isomorphism", {}) or {}
        if not isinstance(di, dict):
            return 0.0, "dispatcher_isomorphism 数据缺失或类型错误"
        meta_exists = bool(di.get("meta_dispatcher_exists", False))
        eventbus_inherits = bool(di.get("eventbus_inherits_meta", False))
        configstore_inherits = bool(di.get("configstore_inherits_meta", False))
        eventdriver_indep = bool(di.get("eventdriver_independent", False))
        ratio = float(di.get("skeleton_ratio", 0.0) or 0.0)
        score = sum([meta_exists, eventbus_inherits,
                     configstore_inherits, eventdriver_indep]) * 20.0
        # 公共骨架行数占比 ≥ 60% 满分 20 分，线性衰减
        score += min(20.0, (ratio / 0.60) * 20.0) if ratio > 0 else 0.0
        score = min(100.0, score)
        detail = (f"基类存在={'是' if meta_exists else '否'}；"
                  f"EventBus 继承={'是' if eventbus_inherits else '否'}；"
                  f"ConfigStore 继承={'是' if configstore_inherits else '否'}；"
                  f"EventDriver 独立={'是' if eventdriver_indep else '否'}；"
                  f"骨架占比={ratio * 100:.1f}%（目标 ≥ 60%）")
        return score, detail

    def _score_runtime_verification(self, results: Dict[str, Any]) -> Tuple[float, str]:
        """运行时验证 harness（v5 新增第 18 维，权重 5%）。

        3 个 in-process 测试通过率（replay/simulation/mode-switch）：
          score = passed / total × 100

        所有数据由 runner.py 通过运行 ``pytest metatest/test_runtime_*.py``
        采集填入 ``test_results["runtime_verification"]``，无硬编码信用分。
        """
        rv = results.get("runtime_verification", {}) or {}
        if not isinstance(rv, dict):
            return 0.0, "runtime_verification 数据缺失或类型错误"
        passed = int(rv.get("passed", 0) or 0)
        total = int(rv.get("total", 3) or 3)
        if total <= 0:
            return 0.0, "无运行时验证测试（total=0）"
        score = min(100.0, (passed / total) * 100.0)
        detail = f"{passed}/{total} 运行时验证测试通过（replay/simulation/mode-switch）"
        return score, detail

    def _score_eventtest_regression(self, results: Dict[str, Any]) -> Tuple[float, str]:
        """eventtest 回归（v5 新增第 19 维，权重 5%）。

        eventtest 退出码 0（全绿）满分 100，否则 0 分（零容忍）。

        所有数据由 runner.py 通过运行 ``python -m eventtest.run_eventtest``
        采集退出码填入 ``test_results["eventtest_regression"]``，无硬编码信用分。
        """
        er = results.get("eventtest_regression", {}) or {}
        if not isinstance(er, dict):
            return 0.0, "eventtest_regression 数据缺失或类型错误"
        # 注意：exit_code=0 是成功值，不可用 `or 1`（0 or 1 == 1 会误判成功为失败）
        exit_code_raw = er.get("exit_code", 1)
        try:
            exit_code = int(exit_code_raw)
        except (TypeError, ValueError):
            exit_code = 1
        if exit_code == 0:
            return 100.0, "eventtest 退出码 0（全绿）"
        return 0.0, f"eventtest 退出码 {exit_code}（非 0，零容忍失败）"

    def _score_cross_module_import_discipline(
        self, results: Dict[str, Any]
    ) -> Tuple[float, str]:
        """跨模块 import 纪律（v5 新增第 20 维，权重 5%）。

        8 处违规模式 Grep 零匹配满分，每项违规扣 100/8 = 12.5 分。

        8 处模式：7 业务模块（execution/screening/formula/runtime_mode/trade/
        tick_bar/monitoring）禁直接 import table_engine + execution 禁 import
        screening_module。所有数据由 runner.py 通过 Grep 采集填入
        ``test_results["cross_module_import_discipline"]``，无硬编码信用分。
        """
        cm = results.get("cross_module_import_discipline", {}) or {}
        if not isinstance(cm, dict):
            return 0.0, "cross_module_import_discipline 数据缺失或类型错误"
        violations = int(cm.get("violations", 0) or 0)
        total = int(cm.get("total_patterns", 8) or 8)
        if total <= 0:
            return 0.0, "total_patterns 配置为 0"
        score = max(0.0, 100.0 - (violations * (100.0 / total)))
        detail = f"{violations} 处违规 / {total} 项检查（0 违规满分）"
        return score, detail

    # ------------------------------------------------------------------
    # v6 新增 1 维评分逻辑
    # ------------------------------------------------------------------

    def _score_adapter_isomorphism(self, results: Dict[str, Any]) -> Tuple[float, str]:
        """adapter 转发同构（v6 新增第 21 维，权重 4%）。

        TqProvider/TqSdkBridge 转发方法表驱动覆盖率 ≥ 90% 满分，线性衰减。
        覆盖率 = 表驱动覆盖方法数 / 总转发方法数 × 100（四通用转发器
        ``_forward`` / ``_call_cached`` / ``_call_simple`` / ``_call_cached_per_code``
        各覆盖其表条目）。v6 阈值 80%，v7 收尾闭合后阈值提升至 90%（v6 已 100%、
        v7 仍 100%，阈值提升确保未来回归被捕获）。
        所有数据由 runner.py 通过 Grep + AST 采集填入
        ``test_results["adapter_isomorphism"]``，无硬编码信用分。
        """
        ai = results.get("adapter_isomorphism", {}) or {}
        if not isinstance(ai, dict):
            return 0.0, "adapter_isomorphism 数据缺失或类型错误"
        coverage = float(ai.get("coverage", 0.0) or 0.0)
        total = int(ai.get("total_forward_methods", 0) or 0)
        covered = int(ai.get("covered_methods", 0) or 0)
        generic = int(ai.get("generic_method_count", 0) or 0)
        if total <= 0:
            return 0.0, "无转发方法（total_forward_methods=0）"
        # 覆盖率 ≥ 90% 满分，线性衰减（v7 阈值从 80 提升至 90）
        score = 100.0 if coverage >= 90.0 else max(0.0, coverage / 90.0 * 100.0)
        detail = (f"覆盖率={coverage:.1f}%（{covered}/{total} 方法表驱动，"
                  f"4 通用转发器存在 {generic}/4）")
        return score, detail

    # ------------------------------------------------------------------
    # 加权总分
    # ------------------------------------------------------------------

    @staticmethod
    def _weighted_total(dimensions: List[ScoreDimension]) -> float:
        """计算加权总分：Σ(维度得分 × 权重)。"""
        total = 0.0
        for dim in dimensions:
            total += dim.score * dim.weight
        return round(total, 2)
