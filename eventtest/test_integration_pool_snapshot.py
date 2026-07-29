# -*- coding: utf-8 -*-
"""合测试 — 仿真模式池状态快照（Task 10 SubTask 10.1）。

基于 spec.md L173-179 "池状态快照" Scenario：
    WHEN 仿真运行后取池状态快照
    THEN source 池 = 100 只 fz 股票
    AND  pool_A ⊆ source
    AND  pool_B ⊆ source
    AND  pool_C = pool_A ∩ pool_B
    AND  pool_C 中每只股票持仓 = 100 股

虚拟时钟推进说明：
    spec.md L174 要求"仿真运行 120 秒后取池状态快照"。与 Task 9 一致，
    本测试推进 300 秒虚拟时钟（5 分钟），产生足够 1m K 线触发 KDJ/MACD
    金叉信号，使股票进入 pool_A / pool_B / pool_C。

══════════════════════════════════════════════════════════════════════
pool_C > pool_A 根因调查结果（Task 9 评审黄旗）
══════════════════════════════════════════════════════════════════════

现象：
    仿真 300 秒后 pool_C(94 只) > pool_A(81 只)，pool_C 含 13 只不在
    pool_A 中的股票。spec.md L178 要求 pool_C = pool_A ∩ pool_B，
    理论上 pool_C ⊆ pool_A 应成立（pool_A 采用 copy 模式累积，
    TTL=6000s 在 300 秒仿真内不触发，pool_A 只增不减）。

根本原因（c）：交集路径退化 bug
    core/execution_module.py `_activate_condition` 方法中，当交集条件
    （evaluator_type="intersection"）的某个源池为空时：

    1. L2832-2833: ``if not source_codes: continue`` — 空源池的入边被
       跳过，``port_results`` 不记录该池的（空）股票列表。
    2. L2703-2704（``_apply_set_operation``）: ``if len(port_results) == 1:
       return list(next(iter(port_results.values())))`` — 当仅剩一个入边
       有股票时，直接返回该池的全量股票列表，**不做交集运算**。

    这导致：当 pool_A 为空但 pool_B 非空时（仿真前期 ec2 每 10s 触发
    cond2 填充 pool_B，而 ec1 每 60s 才触发 cond1 填充 pool_A），
    交集退化为返回 pool_B 的全量股票，这些股票被 _tgt_merge（copy 模式）
    累积到 pool_C。实测有 20 次退化交集调用（ts=34570~34600，
    pool_A=0 但 pool_B=48~58），导致 13 只从未进入 pool_A 的股票
    被错误地加入 pool_C。

是否为 bug：是
    交集运算的数学定义：A ∩ ∅ = ∅。空源池应产生空交集结果，
    而非返回另一池的全量股票。

修复建议：
    方案 A（推荐）：在 ``_activate_condition`` 中，对 intersection 类型，
    当 ``source_codes`` 为空时不 ``continue``，而是将空列表写入
    ``port_results[order] = []``，使 ``_apply_set_operation`` 正确计算
    交集为空集。

    方案 B：在 ``_apply_set_operation`` 中，对 intersection 类型，
    若任何 port 的列表为空，直接返回空列表。

相关代码位置：
    - core/execution_module.py:2832-2833（``continue`` 跳过空源池）
    - core/execution_module.py:2703-2704（单入边直接返回，不做交集）
    - core/execution_module.py:2835-2839（交集路径用 source_codes 全量）

测试策略：
    - 严格执行 spec.md L178 ``pool_C = pool_A ∩ pool_B`` 断言（不弱化）
    - 该断言将**失败**，暴露上述 bug
    - 另提供弱化断言（pool_C ⊆ source、pool_C ⊆ pool_A ∪ pool_B）
      作为 spec 偏差标记，这些断言通过
    - pool_C 每只股票持仓 = 100 股断言通过（买入链不受 bug 影响）

复用 core/ 现有类（PoolEngine / MockDataSource / EventDriver / EventBus），
不使用已删除旧接口。
"""
from __future__ import annotations

from typing import Any, Dict, List, Set

import pytest

# 复用 Task 9 的仿真装配辅助函数（避免重复代码）
from eventtest.test_integration_sim_full_flow import (
    _EXPECTED_EVENT_TYPES,
    _SIM_SECONDS,
    _CLOCK_START,
    _advance_virtual_clock,
    _build_pool_engine,
    _count_events_by_type,
    _load_fz_stocks,
    _setup_sim_engine,
)


# ═══════════════════════════════════════════════════════════════
# 模块级 fixture：装配 + 推进虚拟时钟一次，所有测试共享
# ═══════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def snapshot_engine(report_state):
    """模块级 fixture：装配 PoolEngine + 推进 600 秒虚拟时钟。

    与 Task 9 的 sim_engine / chain_engine fixture 独立装配（避免跨文件
    fixture 依赖），但使用相同的装配辅助函数，确保仿真行为一致。

    同时填充共享报告状态（report_state session-scoped fixture）供
    run_eventtest.py 生成量化报告。
    """
    engine = _build_pool_engine()
    codes = _load_fz_stocks(100)
    _setup_sim_engine(engine, codes)
    # step=10 逐 10 秒推进，保证 1m/5m K 线边界与边定时器均被命中，同时显著降低耗时
    _advance_virtual_clock(engine, _SIM_SECONDS, step=10)

    bus = engine._components["event_bus"]
    counts = _count_events_by_type(bus)
    report_state["event_counts"] = {
        t: counts.get(t, 0) for t in _EXPECTED_EVENT_TYPES
    }
    report_state["sim_seconds"] = _SIM_SECONDS
    report_state["virtual_clock_start"] = _CLOCK_START

    pool_snap: Dict[str, List[str]] = {}
    for nid in ("source", "pool_A", "pool_B", "pool_C"):
        pool = engine.state.get_pool(nid)
        pool_snap[nid] = sorted(pool.get_stock_codes())
    report_state["pool_snapshot"] = pool_snap

    return engine


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════


def _get_pool_codes(engine: Any, nid: str) -> Set[str]:
    """取指定池的股票代码集合。"""
    return set(engine.state.get_pool(nid).get_stock_codes())


def _get_position_qty_map(engine: Any) -> Dict[str, int]:
    """从 PositionUpdated 事件构建 code → qty 映射。

    PositionUpdated 事件的 tracker dict 含 ``qty`` 字段（买入数量）。
    取每只股票最后一次 PositionUpdated 的 qty（覆盖写入）。
    """
    bus = engine._components["event_bus"]
    events = bus.get_events()
    qty_map: Dict[str, int] = {}
    for ev in events:
        if type(ev).__name__ != "PositionUpdated":
            continue
        tracker = getattr(ev, "tracker", None)
        if isinstance(tracker, dict):
            code = tracker.get("code")
            qty = tracker.get("qty")
            if code is not None and qty is not None:
                qty_map[str(code)] = int(qty)
    return qty_map


# ═══════════════════════════════════════════════════════════════
# 测试用例（≥ 6 个）
# ═══════════════════════════════════════════════════════════════


class TestPoolSnapshotSource:
    """验证 source 池状态（spec.md L175）。"""

    def test_source_pool_has_100_fz_stocks(self, snapshot_engine):
        """source 池应保持 100 只 fz 前缀股票（spec.md L175）。

        source 池是 market_source 节点，在 _init_node_stocks 时填入
        100 只 fz 股票，仿真过程中不修改 source 池。
        """
        pool = snapshot_engine.state.get_pool("source")
        codes = pool.get_stock_codes()
        assert len(codes) == 100, (
            f"source 池应有 100 只股票，实际 {len(codes)}"
        )
        for code in codes:
            assert code.startswith("fz"), (
                f"股票代码应以 fz 前缀：{code}"
            )


class TestPoolSnapshotSubsetRelation:
    """验证 pool_A / pool_B / pool_C 与 source 的子集关系（spec.md L176-177）。"""

    def test_pool_a_subset_of_source(self, snapshot_engine):
        """pool_A ⊆ source（spec.md L176）。

        pool_A 由 cond1（KDJ 金叉）从 source 筛选，copy 模式累积。
        所有 pool_A 股票都应来自 source 池。
        """
        source_codes = _get_pool_codes(snapshot_engine, "source")
        pool_a_codes = _get_pool_codes(snapshot_engine, "pool_A")
        assert pool_a_codes.issubset(source_codes), (
            f"pool_A 不在 source 子集内："
            f"pool_a - source = {pool_a_codes - source_codes}"
        )

    def test_pool_b_subset_of_source(self, snapshot_engine):
        """pool_B ⊆ source（spec.md L177）。

        pool_B 由 cond2（MACD 金叉）从 source 筛选，copy 模式累积。
        所有 pool_B 股票都应来自 source 池。
        """
        source_codes = _get_pool_codes(snapshot_engine, "source")
        pool_b_codes = _get_pool_codes(snapshot_engine, "pool_B")
        assert pool_b_codes.issubset(source_codes), (
            f"pool_B 不在 source 子集内："
            f"pool_b - source = {pool_b_codes - source_codes}"
        )

    def test_pool_c_subset_of_source(self, snapshot_engine):
        """pool_C ⊆ source（弱化断言，spec 偏差标记）。

        spec.md L178 要求 pool_C = pool_A ∩ pool_B。由于交集路径退化 bug
        （见模块 docstring），pool_C 可能包含不在 pool_A 中的股票。
        但 pool_C 的所有股票都应来自 source 池（交集的源都是 source 子集，
        退化时返回的 pool_B 股票也来自 source）。
        """
        source_codes = _get_pool_codes(snapshot_engine, "source")
        pool_c_codes = _get_pool_codes(snapshot_engine, "pool_C")
        assert pool_c_codes.issubset(source_codes), (
            f"pool_C 不在 source 子集内："
            f"pool_c - source = {pool_c_codes - source_codes}"
        )


class TestPoolSnapshotIntersection:
    """验证 pool_C = pool_A ∩ pool_B（spec.md L178）。

    严格执行 spec.md L178 断言，不得弱化为 pool_C ⊆ source。
    """

    def test_pool_c_equals_intersection_strict(self, snapshot_engine):
        """pool_C 应等于 pool_A ∩ pool_B（spec.md L178 严格断言）。

        spec.md L178: ``pool_C = pool_A ∩ pool_B``。

        理论分析：pool_A 和 pool_B 都采用 copy 模式累积，TTL 分别为
        6000s / 12000s（在 300 秒仿真内不触发），两者只增不减。
        pool_C 采用 copy 模式累积交集结果。若交集路径正确实现
        （A ∩ ∅ = ∅），则：
          pool_C = ∪(pool_A(t) ∩ pool_B(t)) = pool_A(T) ∩ pool_B(T)

        **此测试预期失败**，暴露交集路径退化 bug：
        当 pool_A 为空但 pool_B 非空时，交集退化为返回 pool_B 全量股票，
        导致 pool_C 含有从未进入 pool_A 的股票。

        修复后此测试应通过。
        """
        pool_a_codes = _get_pool_codes(snapshot_engine, "pool_A")
        pool_b_codes = _get_pool_codes(snapshot_engine, "pool_B")
        pool_c_codes = _get_pool_codes(snapshot_engine, "pool_C")
        expected_intersection = pool_a_codes & pool_b_codes
        assert pool_c_codes == expected_intersection, (
            f"pool_C 应等于 pool_A ∩ pool_B，但不相等：\n"
            f"  pool_A = {len(pool_a_codes)} 只\n"
            f"  pool_B = {len(pool_b_codes)} 只\n"
            f"  pool_A ∩ pool_B = {len(expected_intersection)} 只\n"
            f"  pool_C = {len(pool_c_codes)} 只\n"
            f"  pool_C - (pool_A ∩ pool_B) = "
            f"{sorted(pool_c_codes - expected_intersection)}\n"
            f"  (pool_A ∩ pool_B) - pool_C = "
            f"{sorted(expected_intersection - pool_c_codes)}\n"
            f"根因：交集路径退化 bug（core/execution_module.py:2832-2833 "
            f"空源池 continue + :2703-2704 单入边直接返回）"
        )

    def test_pool_c_subset_of_union_spec_deviation(self, snapshot_engine):
        """pool_C ⊆ pool_A ∪ pool_B（弱化断言，spec 偏差标记）。

        spec.md L178 要求 pool_C = pool_A ∩ pool_B（严格）。
        由于交集路径退化 bug（见模块 docstring），pool_C ≠ pool_A ∩ pool_B。
        此弱化断言验证 pool_C 的所有股票至少来自 pool_A 或 pool_B，
        作为 spec 偏差的兜底检查。

        修复交集 bug 后，pool_C ⊆ pool_A ∩ pool_B ⊆ pool_A ∪ pool_B
        自然成立。
        """
        pool_a_codes = _get_pool_codes(snapshot_engine, "pool_A")
        pool_b_codes = _get_pool_codes(snapshot_engine, "pool_B")
        pool_c_codes = _get_pool_codes(snapshot_engine, "pool_C")
        union = pool_a_codes | pool_b_codes
        assert pool_c_codes.issubset(union), (
            f"pool_C 不在 pool_A ∪ pool_B 子集内："
            f"pool_c - union = {pool_c_codes - union}"
        )


class TestPoolSnapshotPosition:
    """验证 pool_C 中每只股票持仓 = 100 股（spec.md L179）。"""

    def test_pool_c_stocks_position_100_shares(self, snapshot_engine):
        """pool_C 中每只股票持仓应为 100 股（spec.md L179）。

        pool_C 的 enter_action 为 ``{"type": "market_buy", "volume": 100}``，
        每只股票入池后触发买入链：
        TransferExecuted → Signal(BUY, 100) → OrderPlaced → OrderFilled →
        PositionUpdated(qty=100)。

        从 PositionUpdated 事件取每只 pool_C 股票的持仓数量，
        断言全部 = 100。
        """
        pool_c_codes = _get_pool_codes(snapshot_engine, "pool_C")
        qty_map = _get_position_qty_map(snapshot_engine)

        missing = pool_c_codes - set(qty_map.keys())
        assert not missing, (
            f"以下 {len(missing)} 只 pool_C 股票无 PositionUpdated 事件："
            f"{sorted(missing)}"
        )

        wrong_qty = {c: q for c, q in qty_map.items()
                     if c in pool_c_codes and q != 100}
        assert not wrong_qty, (
            f"以下 pool_C 股票持仓 ≠ 100 股：{wrong_qty}"
        )


class TestPoolSnapshotReportState:
    """验证 report_state 被正确填充（供 run_eventtest.py 生成量化报告）。"""

    def test_report_state_pool_snapshot_filled(self, snapshot_engine, report_state):
        """report_state["pool_snapshot"] 应包含 4 个池的股票代码列表。"""
        pool_snap = report_state.get("pool_snapshot", {})
        for nid in ("source", "pool_A", "pool_B", "pool_C"):
            assert nid in pool_snap, f"pool_snapshot 缺少 {nid}"
            assert isinstance(pool_snap[nid], list)
            assert len(pool_snap[nid]) >= 0
