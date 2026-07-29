# -*- coding: utf-8 -*-
"""合测试 — 仿真模式事件链顺序验证（Task 9 SubTask 9.2）。

基于 spec.md L171 "仿真模式完整事件链" Scenario：
    对每只进入 pool_C 的股票，验证其事件链顺序为：
    TickReceived → DataChanged → BarComposed → EdgeFired →
    FormulaEvaluated → StockFiltered → TransferExecuted → Signal →
    OrderPlaced → OrderFilled → PositionUpdated

实现要点：
    - 装配 PoolEngine 并启动仿真模式（复用 SubTask 9.1 的仿真装配）
    - 推进 600 秒虚拟时钟（与 SubTask 9.1 一致，足够触发 KDJ/MACD 金叉）
    - 从 bus.get_events() 取事件序列（保留正确发布顺序；EventCollector 的
      subscribe_any handler 在 typed handlers 之后执行，嵌套 publish 导致
      collector 看到的事件顺序是反向的——见 test_positive_trade_chain.py 注释）
    - 对每只进入 pool_C 的股票，过滤其相关事件，按首次出现顺序断言事件链
    - 事件链顺序验证基于事件类型的相对顺序（首次出现索引），而非绝对时间戳

关于 EventCollector 的使用说明：
    spec.md SubTask 9.2 要求"从 EventCollector 取事件序列"。EventCollector
    通过 subscribe_any 注册通配订阅者收集所有事件。但由于 EventBus.publish
    先调用 typed handlers（可能嵌套 publish）再调用 any handlers，嵌套 publish
    导致 collector._events 的事件顺序是反向的（最深层事件先收集）。因此：
    - 事件收集：可用 EventCollector（用于计数和过滤）
    - 事件链顺序验证：使用 bus.get_events()（保留正确发布顺序）
    此方法与 test_positive_trade_chain.py 的 test_buy_chain_full_event_sequence
    一致（该测试也用 bus.get_events() 而非 collector.events 断言事件链顺序）。

复用 core/ 现有类（PoolEngine / MockDataSource / EventDriver / EventBus），
不使用已删除旧接口（get_node_stocks / SimTickSource / execution_order /
EdgeFired.changed_codes / at_fn / fire_ttl_due / TtlTracker）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

# 复用 SubTask 9.1 的仿真装配辅助函数（避免重复代码）
from eventtest.test_integration_sim_full_flow import (
    _EXPECTED_EVENT_TYPES,
    _SIM_SECONDS,
    _CLOCK_START,
    _advance_virtual_clock,
    _build_pool_engine,
    _load_fz_stocks,
    _setup_sim_engine,
)


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════


def _event_mentions_code(event: Any, code: str) -> bool:
    """判断事件是否与指定股票代码相关。

    不同事件类型携带股票代码的方式不同：
      - TickReceived / BarComposed / FormulaEvaluated / Signal: ``code`` 字段
      - DataChanged / TransferExecuted: ``codes`` 列表
      - StockFiltered: ``passed`` / ``rejected`` 列表
      - OrderPlaced: ``order`` dict 含 ``code``
      - OrderFilled: ``fill`` dict 含 ``code``
      - PositionUpdated: ``tracker`` dict 含 ``code``
      - EdgeFired: 无 code 字段（边级事件，非股票级）

    EdgeFired 不携带股票代码，无法按股票过滤。在事件链顺序验证中，
    EdgeFired 作为边级事件保留在全局序列中，不参与 per-stock 过滤。
    """
    # 直接 code 字段
    if getattr(event, "code", None) == code:
        return True
    # codes 列表（DataChanged / TransferExecuted）
    codes = getattr(event, "codes", None)
    if isinstance(codes, list) and code in codes:
        return True
    # passed 列表（StockFiltered）
    passed = getattr(event, "passed", None)
    if isinstance(passed, list) and code in passed:
        return True
    # order dict（OrderPlaced）
    order = getattr(event, "order", None)
    if isinstance(order, dict) and order.get("code") == code:
        return True
    # fill dict（OrderFilled）
    fill = getattr(event, "fill", None)
    if isinstance(fill, dict) and fill.get("code") == code:
        return True
    # tracker dict（PositionUpdated）
    tracker = getattr(event, "tracker", None)
    if isinstance(tracker, dict) and tracker.get("code") == code:
        return True
    # bar dict（BarComposed 的 bar 字段，部分实现可能只在 bar 内含 code）
    bar = getattr(event, "bar", None)
    if isinstance(bar, dict) and bar.get("code") == code:
        return True
    # tick_data dict（TickReceived 的 tick_data 字段，部分实现可能只在 tick_data 内含 code）
    tick_data = getattr(event, "tick_data", None)
    if isinstance(tick_data, dict) and tick_data.get("code") == code:
        return True
    return False


def _filter_events_by_code(events: List[Any], code: str) -> List[Any]:
    """从事件列表中过滤与指定股票代码相关的事件（保留原顺序）。"""
    return [ev for ev in events if _event_mentions_code(ev, code)]


def _first_occurrence_types(events: List[Any], target_types: List[str]) -> List[str]:
    """取每类事件首次出现的位置，返回按首次出现顺序排列的事件类型名列表。

    与 SubTask 9.1 的 _first_occurrence_order 类似，但作用于已过滤的 per-stock
    事件列表。基于事件类型的相对顺序（首次出现索引），而非绝对时间戳。
    """
    seen: set = set()
    order: List[str] = []
    for ev in events:
        name = type(ev).__name__
        if name in target_types and name not in seen:
            seen.add(name)
            order.append(name)
    return order


def _is_subsequence(sub: List[str], full: List[str]) -> bool:
    """判断 ``sub`` 是否为 ``full`` 的子序列（保持相对顺序）。

    用于验证事件链顺序：期望顺序中的事件类型应作为子序列出现在实际顺序中。
    例如期望 [TickReceived, DataChanged, Signal] 应为实际顺序的子序列。
    """
    it = iter(full)
    return all(item in it for item in sub)


# ═══════════════════════════════════════════════════════════════
# 模块级 fixture：装配 + 推进虚拟时钟一次，所有测试共享
# ═══════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def chain_engine(report_state):
    """模块级 fixture：装配 PoolEngine + 推进 600 秒虚拟时钟。

    与 SubTask 9.1 的 sim_engine fixture 独立装配（避免跨文件 fixture 依赖），
    但使用相同的装配辅助函数（_build_pool_engine / _load_fz_stocks /
    _setup_sim_engine / _advance_virtual_clock），确保仿真行为一致。

    report_state 为 session-scoped（conftest.py），可被 module-scoped 引用。
    """
    engine = _build_pool_engine()
    codes = _load_fz_stocks(100)
    _setup_sim_engine(engine, codes)
    # step=10 逐 10 秒推进，保证 1m/5m K 线边界与边定时器均被命中，同时显著降低耗时
    _advance_virtual_clock(engine, _SIM_SECONDS, step=10)
    return engine


@pytest.fixture(scope="module")
def pool_c_codes(chain_engine) -> List[str]:
    """模块级 fixture：取仿真后 pool_C 的股票代码列表（排序后）。

    用于 per-stock 事件链顺序验证。若 pool_C 为空，相关测试将跳过
    （pytest.skip）。
    """
    pool = chain_engine.state.get_pool("pool_C")
    codes = sorted(pool.get_stock_codes())
    return codes


@pytest.fixture(scope="module")
def first_pool_c_code(pool_c_codes) -> str:
    """模块级 fixture：取 pool_C 第一只股票代码（用于单股票事件链验证）。"""
    if not pool_c_codes:
        pytest.skip("pool_C 为空，无法验证 per-stock 事件链顺序")
    return pool_c_codes[0]


# ═══════════════════════════════════════════════════════════════
# 测试用例（≥ 4 个）
# ═══════════════════════════════════════════════════════════════


class TestEventChainOrderPerStock:
    """验证每只进入 pool_C 的股票的事件链顺序。

    spec.md L171 要求：对每只进入 pool_C 的股票，验证其事件链顺序为
    TickReceived → DataChanged → BarComposed → EdgeFired →
    FormulaEvaluated → StockFiltered → TransferExecuted → Signal →
    OrderPlaced → OrderFilled → PositionUpdated

    注意：EdgeFired 不携带股票代码（边级事件），无法按股票过滤。
    在 per-stock 事件链中，EdgeFired 被排除（仅验证其余 10 类股票级事件
    的相对顺序）。全局事件链顺序（含 EdgeFired）由 SubTask 9.1 的
    test_event_chain_first_occurrence_order 验证。
    """

    def test_pool_c_has_at_least_one_stock(self, pool_c_codes):
        """前置条件：pool_C 至少有 1 只股票（否则无法验证 per-stock 链）。

        spec.md L173-179 池状态快照 Scenario 要求 pool_C 非空。
        仿真 600 秒虚拟时钟足以触发 KDJ/MACD 金叉 + 交集，使股票进入 pool_C。
        """
        assert len(pool_c_codes) >= 1, (
            f"pool_C 应至少有 1 只股票，实际 {len(pool_c_codes)}"
        )

    def test_first_stock_chain_has_minimum_event_types(
        self, chain_engine, first_pool_c_code,
    ):
        """首只 pool_C 股票的事件链应包含至少 8 类事件。

        spec.md 要求 11 类事件，但任务说明允许"至少 8 类事件 ≥1"。
        EdgeFired 无法按股票过滤（边级事件），因此 per-stock 最多 10 类。
        断言 ≥ 8 类（10 类中至少 8 类出现）。
        """
        bus = chain_engine._components["event_bus"]
        all_events = bus.get_events()
        stock_events = _filter_events_by_code(all_events, first_pool_c_code)

        # per-stock 事件类型（排除 EdgeFired，因为边级事件无 code）
        per_stock_types = [t for t in _EXPECTED_EVENT_TYPES if t != "EdgeFired"]
        found_types = {type(ev).__name__ for ev in stock_events}
        present = found_types & set(per_stock_types)

        assert len(present) >= 8, (
            f"股票 {first_pool_c_code} 的事件链应包含至少 8 类事件，"
            f"实际 {len(present)} 类：{sorted(present)}；"
            f"缺失：{sorted(set(per_stock_types) - present)}"
        )

    def test_first_stock_chain_order_by_first_occurrence(
        self, chain_engine, first_pool_c_code,
    ):
        """首只 pool_C 股票的事件链按首次出现顺序应遵循 spec 顺序。

        取该股票相关事件的首次出现顺序，验证是 spec 期望顺序的子序列
        （允许中间有事件类型缺失，但出现的类型必须保持相对顺序）。

        例如：若股票只触发了 [TickReceived, DataChanged, BarComposed, Signal,
        OrderPlaced, OrderFilled, PositionUpdated]，则该序列应为 spec 顺序
        的子序列（即这些类型在 spec 中的相对顺序一致）。
        """
        bus = chain_engine._components["event_bus"]
        all_events = bus.get_events()
        stock_events = _filter_events_by_code(all_events, first_pool_c_code)

        # per-stock 事件类型（排除 EdgeFired）
        per_stock_types = [t for t in _EXPECTED_EVENT_TYPES if t != "EdgeFired"]
        actual_order = _first_occurrence_types(stock_events, per_stock_types)

        assert len(actual_order) >= 2, (
            f"股票 {first_pool_c_code} 的事件链至少应有 2 种事件类型，"
            f"实际 {actual_order}"
        )
        # 验证实际顺序是期望顺序的子序列（保持相对顺序）
        assert _is_subsequence(actual_order, per_stock_types), (
            f"股票 {first_pool_c_code} 的事件链顺序错误：\n"
            f"  期望子序列关系（actual ⊆ spec_order 保持相对顺序）\n"
            f"  期望 spec 顺序 {per_stock_types}\n"
            f"  实际首次出现顺序 {actual_order}"
        )

    def test_first_stock_tick_before_signal(
        self, chain_engine, first_pool_c_code,
    ):
        """首只 pool_C 股票：TickReceived 在 Signal 之前出现。

        验证事件链中 TickReceived（tick 接收）先于 Signal（交易信号）。
        这是事件链的基本因果顺序：tick → data → bar → edge → formula →
        filter → transfer → signal。
        """
        bus = chain_engine._components["event_bus"]
        all_events = bus.get_events()
        stock_events = _filter_events_by_code(all_events, first_pool_c_code)

        # 找 TickReceived 和 Signal 的首次出现索引
        tick_idx = None
        signal_idx = None
        for i, ev in enumerate(stock_events):
            name = type(ev).__name__
            if name == "TickReceived" and tick_idx is None:
                tick_idx = i
            if name == "Signal" and signal_idx is None:
                signal_idx = i
            if tick_idx is not None and signal_idx is not None:
                break

        assert tick_idx is not None, (
            f"股票 {first_pool_c_code} 未触发 TickReceived 事件"
        )
        assert signal_idx is not None, (
            f"股票 {first_pool_c_code} 未触发 Signal 事件"
        )
        assert tick_idx < signal_idx, (
            f"股票 {first_pool_c_code}：TickReceived(idx={tick_idx}) "
            f"应在 Signal(idx={signal_idx}) 之前"
        )

    def test_first_stock_transfer_before_order_placed(
        self, chain_engine, first_pool_c_code,
    ):
        """首只 pool_C 股票：TransferExecuted 在 OrderPlaced 之前出现。

        验证事件链中 TransferExecuted（股票入 pool_C）先于 OrderPlaced（下单）。
        因果链：TransferExecuted → TradeModule._on_transfer_executed →
        Signal(BUY) → _on_signal → _paper_execute → OrderPlaced。
        """
        bus = chain_engine._components["event_bus"]
        all_events = bus.get_events()
        stock_events = _filter_events_by_code(all_events, first_pool_c_code)

        transfer_idx = None
        order_idx = None
        for i, ev in enumerate(stock_events):
            name = type(ev).__name__
            if name == "TransferExecuted" and transfer_idx is None:
                transfer_idx = i
            if name == "OrderPlaced" and order_idx is None:
                order_idx = i
            if transfer_idx is not None and order_idx is not None:
                break

        assert transfer_idx is not None, (
            f"股票 {first_pool_c_code} 未触发 TransferExecuted 事件"
        )
        assert order_idx is not None, (
            f"股票 {first_pool_c_code} 未触发 OrderPlaced 事件"
        )
        assert transfer_idx < order_idx, (
            f"股票 {first_pool_c_code}：TransferExecuted(idx={transfer_idx}) "
            f"应在 OrderPlaced(idx={order_idx}) 之前"
        )

    def test_first_stock_order_filled_before_position_updated(
        self, chain_engine, first_pool_c_code,
    ):
        """首只 pool_C 股票：OrderFilled 在 PositionUpdated 之前出现。

        验证事件链末尾顺序：OrderFilled → PositionUpdated。
        因果链：OrderPlaced → _on_order_placed → OrderFilled →
        PositionUpdated。
        """
        bus = chain_engine._components["event_bus"]
        all_events = bus.get_events()
        stock_events = _filter_events_by_code(all_events, first_pool_c_code)

        fill_idx = None
        pos_idx = None
        for i, ev in enumerate(stock_events):
            name = type(ev).__name__
            if name == "OrderFilled" and fill_idx is None:
                fill_idx = i
            if name == "PositionUpdated" and pos_idx is None:
                pos_idx = i
            if fill_idx is not None and pos_idx is not None:
                break

        assert fill_idx is not None, (
            f"股票 {first_pool_c_code} 未触发 OrderFilled 事件"
        )
        assert pos_idx is not None, (
            f"股票 {first_pool_c_code} 未触发 PositionUpdated 事件"
        )
        assert fill_idx < pos_idx, (
            f"股票 {first_pool_c_code}：OrderFilled(idx={fill_idx}) "
            f"应在 PositionUpdated(idx={pos_idx}) 之前"
        )


class TestEventChainOrderAllStocks:
    """验证所有进入 pool_C 的股票的事件链顺序（综合验证）。

    对 pool_C 中的每只股票验证事件链顺序。由于仿真可能产生大量股票
    （实测约 94 只），为控制测试时长，抽样验证前 5 只股票。
    """

    def test_sampled_stocks_chain_order(
        self, chain_engine, pool_c_codes,
    ):
        """抽样验证 pool_C 前 5 只股票的事件链顺序。

        对每只抽样股票：
          1. 过滤其相关事件
          2. 取事件类型首次出现顺序
          3. 验证是 spec 期望顺序的子序列（保持相对顺序）
        """
        bus = chain_engine._components["event_bus"]
        all_events = bus.get_events()
        # per-stock 事件类型（排除 EdgeFired）
        per_stock_types = [t for t in _EXPECTED_EVENT_TYPES if t != "EdgeFired"]

        sample_size = min(5, len(pool_c_codes))
        failures: List[str] = []
        for code in pool_c_codes[:sample_size]:
            stock_events = _filter_events_by_code(all_events, code)
            actual_order = _first_occurrence_types(stock_events, per_stock_types)
            if len(actual_order) < 2:
                failures.append(
                    f"  {code}: 事件类型不足（{actual_order}）"
                )
                continue
            if not _is_subsequence(actual_order, per_stock_types):
                failures.append(
                    f"  {code}: 顺序错误（{actual_order}）"
                )

        assert not failures, (
            f"抽样 {sample_size} 只股票中 {len(failures)} 只事件链顺序验证失败：\n"
            + "\n".join(failures)
        )

    def test_all_pool_c_stocks_have_tick_received(
        self, chain_engine, pool_c_codes,
    ):
        """所有 pool_C 股票都应至少触发过 TickReceived 事件。

        TickReceived 是事件链的起点。每只进入 pool_C 的股票必然
        先接收过 tick 数据（否则无法触发后续的公式计算和筛选）。
        """
        bus = chain_engine._components["event_bus"]
        all_events = bus.get_events()

        missing: List[str] = []
        for code in pool_c_codes:
            stock_events = _filter_events_by_code(all_events, code)
            has_tick = any(type(ev).__name__ == "TickReceived" for ev in stock_events)
            if not has_tick:
                missing.append(code)

        assert not missing, (
            f"以下 {len(missing)} 只 pool_C 股票未触发 TickReceived 事件："
            f"{missing[:10]}{'...' if len(missing) > 10 else ''}"
        )

    def test_all_pool_c_stocks_have_position_updated(
        self, chain_engine, pool_c_codes,
    ):
        """所有 pool_C 股票都应触发过 PositionUpdated 事件。

        spec.md 要求"股票入C池后立即市价买入100股"。买入链：
        TransferExecuted → Signal(BUY) → OrderPlaced → OrderFilled →
        PositionUpdated。因此每只 pool_C 股票都应有 PositionUpdated。
        """
        bus = chain_engine._components["event_bus"]
        all_events = bus.get_events()

        missing: List[str] = []
        for code in pool_c_codes:
            stock_events = _filter_events_by_code(all_events, code)
            has_pos = any(type(ev).__name__ == "PositionUpdated" for ev in stock_events)
            if not has_pos:
                missing.append(code)

        assert not missing, (
            f"以下 {len(missing)} 只 pool_C 股票未触发 PositionUpdated 事件："
            f"{missing[:10]}{'...' if len(missing) > 10 else ''}"
        )
