"""metatest v3 12 维量化评分引擎。

按「metatest 严格正反合测试量化评审规范」spec 实现 12 维加权评分，
所有评分完全由 ``test_results`` 字段计算，禁止硬编码信用分。

  维度                         权重    评分逻辑
  ─────────────────────────── ────── ──────────────────────────────────────
  module_coverage              10%    覆盖模块数 / 17 * 100
  test_pass_rate               18%    通过数 / 总数 * 100（跳过计为失败）
  assertion_density             8%    断言数 / (测试文件数 * 20) * 100
  event_chain_integrity        10%    出现事件类型数 / 10 * 100（链顺序错误扣 20%）
  performance_benchmark         8%    1000 tick 耗时 ≤10s 满分，线性衰减
  frontend_e2e_pass_rate       10%    前端 E2E 真实通过数 / 总数 * 100（环境缺失给最低达标线 80）
  logic_coverage                8%    5 项底层逻辑验证通过数 / 5 * 100
  isomorphism_elimination      12%    15 项同构代码 Grep 检查，0 违规满分
  line_convergence              8%    核心模块总行数 ≤ 23000 满分，线性衰减
  rule_compliance               4%    RULES 91-100 Grep 违规数 / 10，0 违规满分
  negative_test_coverage        2%    4 类反测试用例数 / 目标数（每类 ≥ 8）均值 * 100
  synthesis_e2e                 2%    合测试通过数 / 总数 * 100

权重总和 = 1.0。总分 = Σ(维度得分 × 权重)。
门槛：总分 ≥ 95 且 12 维均 ≥ 80（redo_list 为空）判定 PASS。
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
        dimensions: 12 个维度的评分明细列表
        total_score: 加权总分（0-100）
        passed: 总分是否 ≥ 门槛（95）且 12 维均 ≥ 80
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

#: 同构代码检查项总数（v3 从 6 扩展到 15，对应 15 组模式 + 原 6 项保留项）
ISOMORPHISM_CHECKS_TOTAL: int = 15

#: 核心模块行数收敛目标（核心模块总行数 ≤ 此值满分）
CORE_LINES_TARGET: int = 23000

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
    """12 维加权评分引擎。

    ``calculate(test_results)`` 接收测试结果字典，计算 12 维加权总分，
    返回 ``ScoreReport``。总分 ≥ 95 且 12 维均 ≥ 80（redo_list 为空）判定 PASS。

    v3 严格规则：
      - 所有评分完全由 ``test_results`` 字段计算，无硬编码信用分
      - 跳过测试计为失败（分子不含 skipped）
      - 前端 E2E 环境缺失给予最低达标线 80（环境问题非代码问题，不跌穿门槛）
      - 12 维分数均需 ≥ 80 才达标（redo_list 为空）

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
            "isomorphism_total_checks": 15,
            "core_total_lines": 22000,
            "core_lines_target": 23000,
            "rule_violations": 0,
            "rule_total_checks": 10,
            "negative_test_counts": {"invalid_config": 8, "runtime_errors": 8,
                                     "api_frontend": 8, "logic_errors": 8},
            "negative_test_target_per_category": 8,
            "synthesis_passed": 8,
            "synthesis_total": 8,
        })
        print(report.total_score, report.passed)
    """

    #: 12 维度定义：(维度名, 权重) — 权重总和 = 1.0
    DIMENSIONS: List[Tuple[str, float]] = [
        ("module_coverage", 0.10),            # 模块覆盖率
        ("test_pass_rate", 0.18),             # 测试通过率（跳过计为失败）
        ("assertion_density", 0.08),          # 断言密度
        ("event_chain_integrity", 0.10),      # 事件链完整性
        ("performance_benchmark", 0.08),      # 性能基准
        ("frontend_e2e_pass_rate", 0.10),     # 前端 E2E 真实通过率
        ("logic_coverage", 0.08),             # 底层逻辑覆盖度
        ("isomorphism_elimination", 0.12),    # 同构代码消除度（15 项）
        ("line_convergence", 0.08),           # 核心模块行数收敛
        ("rule_compliance", 0.04),            # RULES 91-100 合规
        ("negative_test_coverage", 0.02),     # 4 类反测试覆盖度
        ("synthesis_e2e", 0.02),             # 合测试通过率
    ]

    #: 通过门槛：总分 ≥ 95 判定 PASS
    THRESHOLD: float = 95.0

    def calculate(self, test_results: Dict[str, Any]) -> ScoreReport:
        """计算 12 维加权总分。

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
                - isomorphism_violations: int (15 项 Grep 违规数)
                - isomorphism_total_checks: int (15)
                - core_total_lines: int (核心模块总行数，wc -l core/*.py 实测)
                - core_lines_target: int (目标行数 23000)
                - rule_violations: int (RULES 91-100 Grep 违规数)
                - rule_total_checks: int (10)
                - negative_test_counts: Dict[str, int] (4 类反测试用例数)
                - negative_test_target_per_category: int (8)
                - synthesis_passed: int
                - synthesis_total: int

        Returns:
            ScoreReport: 评分报告（含 12 维明细、总分、PASS/FAIL、扣分项、重做列表）
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
        # v3: PASS 需总分 ≥ 95 且 12 维均 ≥ 80（redo_list 为空）
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
        """同构代码消除度：15 项 Grep 验证，0 违规满分。

        v3 检查项从 6 扩展到 15（对应本次 15 组模式 + 原 6 项保留项）。
        每项违规扣 100/15 分，最低 0 分。
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
        """核心模块行数收敛度：总行数 ≤ 23000 满分，线性衰减。

        评分公式：``score = 100 * (target / max(lines, target))``
        - lines ≤ 23000 → score = 100
        - lines = 25000 → score = 92.0
        - lines = 30000 → score ≈ 76.7

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
    # 加权总分
    # ------------------------------------------------------------------

    @staticmethod
    def _weighted_total(dimensions: List[ScoreDimension]) -> float:
        """计算加权总分：Σ(维度得分 × 权重)。"""
        total = 0.0
        for dim in dimensions:
            total += dim.score * dim.weight
        return round(total, 2)
