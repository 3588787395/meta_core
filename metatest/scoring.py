"""metatest v2 8 维评分引擎。

按「metatest 严格正反合测试量化评审规范」spec 实现 8 维加权评分：

  维度                       权重    评分逻辑
  ───────────────────────── ────── ──────────────────────────────────────
  module_coverage            15%    覆盖模块数 / 17 * 100
  test_pass_rate             20%    通过测试数 / 总测试数 * 100（跳过计为失败）
  assertion_density          10%    断言数 / (测试文件数 * 目标密度) * 100
  event_chain_integrity      15%    出现事件类型数 / 10 * 100（链顺序错误扣 20%）
  performance_benchmark      10%    1000 tick 耗时基准（≤10s 满分，线性衰减）
  frontend_e2e_pass_rate     10%    前端 E2E 真实通过数 / 总数 * 100（不再给信用分）
  logic_coverage             10%    5 项底层逻辑验证通过数 / 5 * 100
  isomorphism_elimination    10%    6 项同构代码 Grep 检查，0 违规满分

总分 = Σ(维度得分 × 权重)，门槛：总分 ≥ 95 且 8 维均 ≥ 80 判定 PASS。
跳过测试计为失败（不再给予信用分）。
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
        weight: 权重（0.0-1.0，6 维权重总和为 1.0）
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
        dimensions: 8 个维度的评分明细列表
        total_score: 加权总分（0-100）
        passed: 总分是否 ≥ 门槛（95）且 8 维均 ≥ 80
        deductions: 扣分项描述列表
        redo_list: 需重做的维度名列表（得分 < 80）
    """

    dimensions: List[ScoreDimension]
    total_score: float
    passed: bool  # total_score >= 95
    deductions: List[str]
    redo_list: List[str]


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 17 个核心模块（与 spec 模块覆盖率分母一致）
TOTAL_MODULES: int = 17

#: 10 类事件链（与 spec 事件链完整性分母一致）
TOTAL_EVENT_TYPES: int = 10

#: 断言密度目标：每个测试文件期望的断言数
ASSERTION_DENSITY_TARGET: int = 10

#: 性能基准：1000 tick 耗时门槛（秒），≤ 此值满分
PERFORMANCE_THRESHOLD_S: float = 10.0

#: 单维度重做门槛：得分低于此值需重做
REDO_THRESHOLD: float = 80.0

#: 底层逻辑验证项总数（5 项：水位线/编译-运行分离/三要素/角色表/正交化）
LOGIC_COVERAGE_TOTAL: int = 5

#: 同构代码检查项总数（6 项 Grep 验证）
ISOMORPHISM_CHECKS_TOTAL: int = 6


# ---------------------------------------------------------------------------
# 评分引擎
# ---------------------------------------------------------------------------


class ScoringEngine:
    """8 维加权评分引擎。

    ``calculate(test_results)`` 接收测试结果字典，计算 8 维加权总分，
    返回 ``ScoreReport``。总分 ≥ 95 且 8 维均 ≥ 80 判定 PASS。

    v2 严格规则：
      - 跳过测试计为失败（不再给予信用分）
      - 前端 E2E 环境缺失计为失败（不再给信用分）
      - 8 维分数均需 ≥ 80 才达标

    使用方式::

        engine = ScoringEngine()
        report = engine.calculate({
            "modules_covered": 15,
            "tests_passed": 80,
            "tests_total": 100,
            "assertions_count": 500,
            "test_files_count": 50,
            "event_types_seen": ["TickReceived", "DataChanged", ...],
            "event_chain_correct": True,
            "sim_1000_tick_time_s": 8.5,
            "frontend_e2e_passed": 5,
            "frontend_e2e_total": 6,
            "frontend_e2e_env_missing": False,
            "logic_coverage_passed": 5,
            "logic_coverage_total": 5,
            "isomorphism_violations": 0,
            "isomorphism_total_checks": 6,
        })
        print(report.total_score, report.passed)
    """

    #: 8 维度定义：(维度名, 权重) — 权重总和 = 1.0
    DIMENSIONS: List[Tuple[str, float]] = [
        ("module_coverage", 0.15),            # 模块覆盖率
        ("test_pass_rate", 0.20),             # 测试通过率（跳过计为失败）
        ("assertion_density", 0.10),          # 断言密度
        ("event_chain_integrity", 0.15),      # 事件链完整性
        ("performance_benchmark", 0.10),      # 性能基准
        ("frontend_e2e_pass_rate", 0.10),     # 前端 E2E 真实通过率
        ("logic_coverage", 0.10),             # 底层逻辑覆盖度
        ("isomorphism_elimination", 0.10),    # 同构代码消除度
    ]

    #: 通过门槛：总分 ≥ 95 判定 PASS
    THRESHOLD: float = 95.0

    def calculate(self, test_results: Dict[str, Any]) -> ScoreReport:
        """计算 8 维加权总分。

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
                - isomorphism_violations: int (6 项 Grep 违规数)
                - isomorphism_total_checks: int (6)

        Returns:
            ScoreReport: 评分报告（含 8 维明细、总分、PASS/FAIL、扣分项、重做列表）
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
        # v2: PASS 需总分 ≥ 95 且 8 维均 ≥ 80（redo_list 为空）
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

        v2 严格规则：跳过（skipped）计为失败，不在分子中。
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
        """断言密度：断言数 / (测试文件数 * 目标密度) * 100。"""
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

        v2 严格规则：不再给信用分。环境缺失时 passed=0，计为失败。
        """
        passed = int(results.get("frontend_e2e_passed", 0) or 0)
        total = int(results.get("frontend_e2e_total", 0) or 0)
        env_missing = bool(results.get("frontend_e2e_env_missing", False))
        if total <= 0:
            return 0.0, "无前端 E2E 测试（frontend_e2e_total=0）"
        score = min(100.0, (passed / total) * 100.0)
        if env_missing:
            detail = f"{passed}/{total} 通过（环境未就绪，不再给信用分）"
        else:
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
        """同构代码消除度：6 项 Grep 验证，0 违规满分。

        6 项同构代码模式（匹配数应为 0）：
          1. ``state.latest_tick[`` = 0（除 TickTable 内部）
          2. 运行时 ``json.loads`` / ``_parse_edge`` / ``_build_adjacency`` = 0
          3. ``_phase_dispatch`` / ``_phase_nset_filter`` / ``_dispatch_filter`` / ``_eval_primitive`` = 0
          4. ``if node.type ==`` = 0
          5. ``transfer_module`` 中 ``sound.play`` / ``popup.show`` = 0
          6. 死表引用 = 0

        每项违规扣 100/6 分，最低 0 分。
        """
        violations = int(results.get("isomorphism_violations", 0) or 0)
        total_checks = int(results.get("isomorphism_total_checks", ISOMORPHISM_CHECKS_TOTAL) or ISOMORPHISM_CHECKS_TOTAL)
        if total_checks <= 0:
            return 0.0, "无同构代码检查项"
        # 每项违规扣 100/total_checks 分
        score = max(0.0, 100.0 - (violations * (100.0 / total_checks)))
        detail = f"{violations} 处违规 / {total_checks} 项检查（0 违规满分）"
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
