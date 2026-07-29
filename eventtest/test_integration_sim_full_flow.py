# -*- coding: utf-8 -*-
"""合测试 — 仿真模式完整事件链端到端（Task 9 SubTask 9.1）。

基于 spec.md "仿真模式完整事件链" Scenario（L158-171）：
    WHEN 加载 config/pools/sim_test_pool_100.json 并启动仿真 120 秒虚拟时钟
    THEN TickReceived ≥ 1
    AND  DataChanged ≥ 1
    AND  BarComposed ≥ 1
    AND  EdgeFired ≥ 1
    AND  FormulaEvaluated ≥ 1
    AND  StockFiltered ≥ 1
    AND  TransferExecuted ≥ 1
    AND  Signal ≥ 1
    AND  OrderPlaced ≥ 1
    AND  OrderFilled ≥ 1
    AND  PositionUpdated ≥ 1
    AND  事件链顺序：TickReceived → DataChanged → BarComposed → EdgeFired →
        FormulaEvaluated → StockFiltered → TransferExecuted → Signal →
        OrderPlaced → OrderFilled → PositionUpdated

虚拟时钟推进说明：
    spec.md L159 要求"启动仿真 120 秒虚拟时钟"。实测 120 秒不足以触发 KDJ/MACD
    金叉所需的 K 线数量（cond1 KDJ 需要 5 分钟 K 线 nperiod=5，cond2 MACD
    需要 1 分钟 K 线 nperiod=1）。本测试推进 600 秒虚拟时钟（10 分钟），
    产生足够 5m/1m K 线触发金叉信号，使 11 类事件全部 ≥ 1。
    依据任务说明"可适当延长虚拟时钟"，且断言事件链顺序对已触发事件有效。

实现要点：
    - 装配 PoolEngine（pool_engine fixture 自动装配）
    - 设置虚拟时钟（driver_type=virtual, current_ts=34500.0=09:30:00）
    - 调用 _init_node_stocks 将 source 池填入 100 只 fz 股票
    - rebuild_timed_specs 重建边定时器与虚拟时钟对齐
    - MockDataSource + register_tick_timers 注册 tick 定时器到 EventDriver
    - 逐秒推进虚拟时钟并调用 driver.fire_due(now) 触发事件
    - 提升 EventBus._max_events 避免事件日志截断（仿真 300 秒约 15 万事件）
    - 用 bus.get_events() 取发布顺序（避免 collector.subscribe_any 反向顺序问题）

复用 core/ 现有类（PoolEngine / MockDataSource / EventDriver / EventBus），
不使用已删除旧接口（get_node_stocks / SimTickSource / execution_order /
EdgeFired.changed_codes / at_fn / fire_ttl_due / TtlTracker）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from core.domain import MockDataSource
from core.engine import PoolEngine
from core.tick_bar_module import TickBarModule


# ═══════════════════════════════════════════════════════════════
# 测试常量
# ═══════════════════════════════════════════════════════════════

# 项目根目录（meta_core/），与 conftest.py 一致
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_POOL_CONFIG = _PROJECT_ROOT / "config" / "pools" / "sim_test_pool_100.json"

# 虚拟时钟起点（= 09:30:00 当日秒数偏移），与 conftest.VirtualClock 一致
_CLOCK_START = 34500.0

# 仿真推进秒数（spec 要求 120 秒；延长到 600 秒以触发 KDJ/MACD 金叉所需 K 线数）
# cond1 KDJ 使用 5 分钟 K 线（nperiod=5），cond2 MACD 使用 1 分钟 K 线（nperiod=1），
# 600 秒虚拟时钟可产生 2 根 5m K 线与 10 根 1m K 线，满足公式计算与金叉穿越。
_SIM_SECONDS = 600

# spec.md L160-170 要求的 11 类事件（按事件链顺序）
_EXPECTED_EVENT_TYPES: List[str] = [
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


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════


def _setup_sim_engine(engine: Any, codes: List[str]) -> None:
    """配置仿真模式：虚拟时钟 + 初始化股票 + 重建 specs + MockDataSource。

    1. 设置 state.time_source 为 virtual 模式（current_ts=_CLOCK_START）
    2. _init_node_stocks 将 source 池填入 100 只 fz 股票
    3. rebuild_timed_specs 重建边定时器（first_fire_time 与虚拟时钟对齐）
    4. MockDataSource + register_tick_timers 注册 tick 定时器
    5. 装配 TickBarModule，订阅 TickDue 生成 TickReceived / DataChanged
    6. 提升 EventBus._max_events 避免事件日志截断（仿真 600 秒约 30 万事件）
    """
    bus = engine._components["event_bus"]
    # 提升 max_events 避免仿真 600 秒产生的事件日志被截断（实测约 30 万事件）
    bus._max_events = 999999

    # G2：装配 TickBarModule，将 EventDriver 发布的 TickDue 转为 TickReceived
    tick_bar = TickBarModule(bus=bus)
    engine._components["tick_bar_module"] = tick_bar

    engine.state.time_source = {
        "driver_type": "virtual",
        "current_ts": _CLOCK_START,
        "start_ts": _CLOCK_START,
        "kind": "test_virtual",
    }
    engine.state.first_run = True
    # 手动初始化各池股票（run_pool/run_mode 中调用，但我们不走 run_pool）
    engine._init_node_stocks()
    # 重建边定时器（first_fire_time 与虚拟时钟对齐）
    engine.rebuild_timed_specs()

    driver = engine._components["event_driver"]
    ds = MockDataSource(codes=codes, clock_start=_CLOCK_START)
    ds.set_event_driver(driver, bus)
    # 为降低大规模仿真耗时，将 tick 间隔统一固定为 10 秒（仍能保证 1m/5m K 线
    # 边界被命中、事件链完整触发）。这仅改变测试数据密度，不影响配置校验。
    for code in ds.codes:
        ds._intervals[code] = 10
    ds.register_tick_timers(now=_CLOCK_START)
    # 缓存 MockDataSource 引用，供测试断言使用
    engine._components["mock_data_source"] = ds


def _load_fz_stocks(n: int = 100) -> List[str]:
    """从 ``config/pools/sim_test_pool_100.json`` 动态读取 N 只 fz 前缀股票代码。

    与 conftest.fz_stocks fixture 等价，但为模块级 fixture 直接调用而设
    （function-scoped fixture 不能被 module-scoped fixture 引用）。
    """
    with open(_DEFAULT_POOL_CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    codes: List[str] = []
    for node in cfg.get("nodes", []):
        if not isinstance(node, dict):
            continue
        if node.get("type") != "market_source":
            continue
        for stock in node.get("params", {}).get("stocks", []):
            if isinstance(stock, dict) and stock.get("code"):
                codes.append(str(stock["code"]))
    return codes[:n]


def _build_pool_engine() -> Any:
    """装配并返回 ``PoolEngine`` 实例（与 conftest.pool_engine fixture 等价）。

    为模块级 fixture 直接调用而设（function-scoped fixture 不能被
    module-scoped fixture 引用）。
    """
    with open(_DEFAULT_POOL_CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    return PoolEngine(pool_config=cfg)


def _advance_virtual_clock(
    engine: Any, seconds: int, step: int = 1
) -> float:
    """逐秒推进虚拟时钟并触发 fire_due。

    每步：
      1. current_ts += step
      2. driver.fire_due(current_ts) — 弹出堆顶到时事件，发布事件 + 注册下次
    """
    driver = engine._components["event_driver"]
    final_ts = _CLOCK_START
    for _ in range(seconds // step):
        final_ts += step
        engine.state.time_source["current_ts"] = final_ts
        driver.fire_due(final_ts)
    return final_ts


def _count_events_by_type(bus: Any) -> Dict[str, int]:
    """按事件类型名分组计数（基于 bus.get_events()，保留完整发布日志）。"""
    events = bus.get_events()
    counts: Dict[str, int] = {}
    for ev in events:
        name = type(ev).__name__
        counts[name] = counts.get(name, 0) + 1
    return counts


def _first_occurrence_order(bus: Any, target_types: List[str]) -> List[str]:
    """取每类事件首次出现的位置，返回按首次出现顺序排列的事件类型名列表。

    用 bus.get_events() 取事件发布顺序（EventBus.publish 中 self._events.append
    先于 handlers 调用，因此 _events 保留正确的发布顺序；嵌套 publish 使
    内层事件在外层事件之后 append）。
    """
    events = bus.get_events()
    seen: set = set()
    order: List[str] = []
    for ev in events:
        name = type(ev).__name__
        if name in target_types and name not in seen:
            seen.add(name)
            order.append(name)
    return order


# ═══════════════════════════════════════════════════════════════
# 模块级 fixture：装配 + 推进虚拟时钟一次，所有测试共享
# ═══════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def sim_engine(report_state):
    """模块级 fixture：装配 PoolEngine + 推进 600 秒虚拟时钟。

    所有测试共享同一个 engine 实例（避免重复装配 + 推进的耗时）。
    断言均为只读，不修改 engine 状态。
    同时填充共享报告状态（report_state session-scoped fixture）供
    run_eventtest.py 生成量化报告。

    注意：不依赖 function-scoped 的 pool_engine / fz_stocks fixture，
    因为 module-scoped fixture 不能引用 function-scoped fixture（ScopeMismatch）。
    改为直接调用 _build_pool_engine / _load_fz_stocks 等价构造。
    report_state 为 session-scoped（conftest.py 中已调整），可被 module-scoped 引用。
    """
    engine = _build_pool_engine()
    codes = _load_fz_stocks(100)
    _setup_sim_engine(engine, codes)
    # step=10 逐 10 秒推进，保证 1m/5m K 线边界与边定时器均被命中，同时显著降低耗时
    _advance_virtual_clock(engine, _SIM_SECONDS, step=10)

    bus = engine._components["event_bus"]
    counts = _count_events_by_type(bus)
    # 填充共享报告状态（run_eventtest.py 读取生成量化报告）
    report_state["event_counts"] = {
        t: counts.get(t, 0) for t in _EXPECTED_EVENT_TYPES
    }
    report_state["sim_seconds"] = _SIM_SECONDS
    report_state["virtual_clock_start"] = _CLOCK_START

    # 池状态快照
    pool_snap: Dict[str, List[str]] = {}
    for nid in ("source", "pool_A", "pool_B", "pool_C"):
        pool = engine.state.get_pool(nid)
        pool_snap[nid] = sorted(pool.get_stock_codes())
    report_state["pool_snapshot"] = pool_snap

    return engine


# ═══════════════════════════════════════════════════════════════
# 测试用例
# ═══════════════════════════════════════════════════════════════


class TestSimFullFlowEventCounts:
    """验证仿真模式 11 类事件计数 ≥ 1。"""

    def test_tick_received_ge_1(self, sim_engine):
        """TickReceived ≥ 1（MockDataSource 通过 EventDriver 定时器发布）。"""
        counts = _count_events_by_type(sim_engine._components["event_bus"])
        assert counts.get("TickReceived", 0) >= 1, (
            f"TickReceived={counts.get('TickReceived', 0)}，期望 ≥ 1"
        )

    def test_data_changed_ge_1(self, sim_engine):
        """DataChanged ≥ 1（DataUpdater.apply_data 发布 tick 源 DataChanged）。"""
        counts = _count_events_by_type(sim_engine._components["event_bus"])
        assert counts.get("DataChanged", 0) >= 1, (
            f"DataChanged={counts.get('DataChanged', 0)}，期望 ≥ 1"
        )

    def test_bar_composed_ge_1(self, sim_engine):
        """BarComposed ≥ 1（BarComposer 在 1m K 线边界推进时发布）。"""
        counts = _count_events_by_type(sim_engine._components["event_bus"])
        assert counts.get("BarComposed", 0) >= 1, (
            f"BarComposed={counts.get('BarComposed', 0)}，期望 ≥ 1"
        )

    def test_edge_fired_ge_1(self, sim_engine):
        """EdgeFired ≥ 1（边定时器到时由 EventDriver.fire_due 弹出并发布）。"""
        counts = _count_events_by_type(sim_engine._components["event_bus"])
        assert counts.get("EdgeFired", 0) >= 1, (
            f"EdgeFired={counts.get('EdgeFired', 0)}，期望 ≥ 1"
        )

    def test_formula_evaluated_ge_1(self, sim_engine):
        """FormulaEvaluated ≥ 1（cond1/cond2 激活后公式引擎.eval_series 触发）。"""
        counts = _count_events_by_type(sim_engine._components["event_bus"])
        assert counts.get("FormulaEvaluated", 0) >= 1, (
            f"FormulaEvaluated={counts.get('FormulaEvaluated', 0)}，期望 ≥ 1"
        )

    def test_stock_filtered_ge_1(self, sim_engine):
        """StockFiltered ≥ 1（条件节点激活后 _filter 完成发布）。"""
        counts = _count_events_by_type(sim_engine._components["event_bus"])
        assert counts.get("StockFiltered", 0) >= 1, (
            f"StockFiltered={counts.get('StockFiltered', 0)}，期望 ≥ 1"
        )

    def test_transfer_executed_ge_1(self, sim_engine):
        """TransferExecuted ≥ 1（_propagate 后发布，标志股票入池 pool_A/pool_B/pool_C）。"""
        counts = _count_events_by_type(sim_engine._components["event_bus"])
        assert counts.get("TransferExecuted", 0) >= 1, (
            f"TransferExecuted={counts.get('TransferExecuted', 0)}，期望 ≥ 1"
        )

    def test_signal_ge_1(self, sim_engine):
        """Signal ≥ 1（TransferExecuted 到 pool_C 触发 TradeModule._on_transfer_executed 发布 BUY）。"""
        counts = _count_events_by_type(sim_engine._components["event_bus"])
        assert counts.get("Signal", 0) >= 1, (
            f"Signal={counts.get('Signal', 0)}，期望 ≥ 1"
        )

    def test_order_placed_ge_1(self, sim_engine):
        """OrderPlaced ≥ 1（Signal 触发 TradeModule._on_signal 发布 OrderPlaced）。"""
        counts = _count_events_by_type(sim_engine._components["event_bus"])
        assert counts.get("OrderPlaced", 0) >= 1, (
            f"OrderPlaced={counts.get('OrderPlaced', 0)}，期望 ≥ 1"
        )

    def test_order_filled_ge_1(self, sim_engine):
        """OrderFilled ≥ 1（OrderPlaced 触发 _on_order_placed 模拟成交发布）。"""
        counts = _count_events_by_type(sim_engine._components["event_bus"])
        assert counts.get("OrderFilled", 0) >= 1, (
            f"OrderFilled={counts.get('OrderFilled', 0)}，期望 ≥ 1"
        )

    def test_position_updated_ge_1(self, sim_engine):
        """PositionUpdated ≥ 1（OrderFilled 后立即发布 PositionUpdated）。"""
        counts = _count_events_by_type(sim_engine._components["event_bus"])
        assert counts.get("PositionUpdated", 0) >= 1, (
            f"PositionUpdated={counts.get('PositionUpdated', 0)}，期望 ≥ 1"
        )


class TestSimFullFlowChainOrder:
    """验证仿真模式 11 类事件链顺序。"""

    def test_all_11_event_types_emitted(self, sim_engine):
        """断言 spec 要求的 11 类事件全部 ≥ 1（综合断言）。

        spec.md L160-170 列出 11 类事件，本测试断言全部触发。
        仿真 600 秒虚拟时钟足以产生 KDJ/MACD 金叉所需 K 线数，11 类事件全部 ≥ 1。
        """
        counts = _count_events_by_type(sim_engine._components["event_bus"])
        missing = [
            t for t in _EXPECTED_EVENT_TYPES if counts.get(t, 0) < 1
        ]
        assert not missing, (
            f"以下事件类型未触发（计数 < 1）：{missing}；"
            f"完整计数：{counts}"
        )

    def test_at_least_8_event_types_emitted(self, sim_engine):
        """放宽断言：至少 8 类事件 ≥ 1（spec 任务说明允许）。

        spec.md L160-170 要求 11 类事件 ≥ 1，但任务说明允许"若 120 秒内某些
        事件未触发，可断言至少 8 类事件 ≥ 1"。本测试作为兜底断言，
        实际 11 类事件在 300 秒虚拟时钟下全部触发（由 test_all_11_event_types_emitted 验证）。
        """
        counts = _count_events_by_type(sim_engine._components["event_bus"])
        emitted = sum(1 for t in _EXPECTED_EVENT_TYPES if counts.get(t, 0) >= 1)
        assert emitted >= 8, (
            f"至少 8 类事件应 ≥ 1，实际 {emitted} 类；"
            f"计数：{counts}"
        )

    def test_event_chain_first_occurrence_order(self, sim_engine):
        """断言事件链顺序：TickReceived → DataChanged → BarComposed → EdgeFired →
        FormulaEvaluated → StockFiltered → TransferExecuted → Signal →
        OrderPlaced → OrderFilled → PositionUpdated。

        取每类事件的首次出现位置，按首次出现顺序排列应等于 spec 期望顺序。
        用 bus.get_events() 取发布顺序（EventBus._events 保留正确发布顺序，
        嵌套 publish 使内层事件在外层之后 append）。
        """
        bus = sim_engine._components["event_bus"]
        order = _first_occurrence_order(bus, _EXPECTED_EVENT_TYPES)
        # 过滤掉未触发的事件类型（仅比较已触发的）
        # 但 spec 要求 11 类全部触发，这里先断言全部触发
        assert set(order) == set(_EXPECTED_EVENT_TYPES), (
            f"未触发的事件类型：{set(_EXPECTED_EVENT_TYPES) - set(order)}"
        )
        # 期望首次出现顺序与 spec 事件链顺序一致
        assert order == _EXPECTED_EVENT_TYPES, (
            f"事件链顺序错误：\n"
            f"  期望 {_EXPECTED_EVENT_TYPES}\n"
            f"  实际 {order}"
        )

    def test_first_tick_before_first_data_changed(self, sim_engine):
        """首条 TickReceived 在首条 DataChanged 之前发布。

        TickReceived 由 MockDataSource 的 tick 定时器发布，
        DataChanged(tick) 由 DataUpdater.apply_data 在收到 TickReceived 后发布。
        """
        bus = sim_engine._components["event_bus"]
        events = bus.get_events()
        first_tick_idx = None
        first_data_changed_idx = None
        for i, ev in enumerate(events):
            name = type(ev).__name__
            if name == "TickReceived" and first_tick_idx is None:
                first_tick_idx = i
            if name == "DataChanged" and first_data_changed_idx is None:
                first_data_changed_idx = i
            if first_tick_idx is not None and first_data_changed_idx is not None:
                break
        assert first_tick_idx is not None, "未发布 TickReceived"
        assert first_data_changed_idx is not None, "未发布 DataChanged"
        assert first_tick_idx < first_data_changed_idx, (
            f"TickReceived(idx={first_tick_idx}) 应在 DataChanged"
            f"(idx={first_data_changed_idx}) 之前"
        )

    def test_first_bar_composed_after_first_data_changed(self, sim_engine):
        """首条 BarComposed 在首条 DataChanged 之后发布。

        BarComposer 订阅 DataChanged(tick) 事件后调用 on_tick 合成 K 线，
        当 1m K 线推进时发布 BarComposed。
        """
        bus = sim_engine._components["event_bus"]
        events = bus.get_events()
        first_data_changed_idx = None
        first_bar_idx = None
        for i, ev in enumerate(events):
            name = type(ev).__name__
            if name == "DataChanged" and first_data_changed_idx is None:
                first_data_changed_idx = i
            if name == "BarComposed" and first_bar_idx is None:
                first_bar_idx = i
            if first_data_changed_idx is not None and first_bar_idx is not None:
                break
        assert first_data_changed_idx is not None, "未发布 DataChanged"
        assert first_bar_idx is not None, "未发布 BarComposed"
        assert first_data_changed_idx < first_bar_idx, (
            f"DataChanged(idx={first_data_changed_idx}) 应在 BarComposed"
            f"(idx={first_bar_idx}) 之前"
        )


class TestSimFullFlowReportState:
    """验证 report_state 被正确填充（供 run_eventtest.py 生成量化报告）。"""

    def test_report_state_event_counts_filled(self, sim_engine, report_state):
        """report_state["event_counts"] 应包含 11 类事件计数。"""
        event_counts = report_state.get("event_counts", {})
        for t in _EXPECTED_EVENT_TYPES:
            assert t in event_counts, f"event_counts 缺少 {t}"
            assert isinstance(event_counts[t], int)
            assert event_counts[t] >= 0

    def test_report_state_pool_snapshot_filled(self, sim_engine, report_state):
        """report_state["pool_snapshot"] 应包含 4 个池的股票代码列表。"""
        pool_snap = report_state.get("pool_snapshot", {})
        for nid in ("source", "pool_A", "pool_B", "pool_C"):
            assert nid in pool_snap, f"pool_snapshot 缺少 {nid}"
            assert isinstance(pool_snap[nid], list)

    def test_report_state_sim_seconds_filled(self, sim_engine, report_state):
        """report_state["sim_seconds"] 应记录实际推进秒数。"""
        assert report_state.get("sim_seconds") == _SIM_SECONDS

    def test_report_state_virtual_clock_start_filled(self, sim_engine, report_state):
        """report_state["virtual_clock_start"] 应记录虚拟时钟起点。"""
        assert report_state.get("virtual_clock_start") == _CLOCK_START


class TestSimFullFlowPoolState:
    """验证仿真后池状态（spec.md L173-179 池状态快照 Scenario）。"""

    def test_source_pool_has_100_fz_stocks(self, sim_engine):
        """source 池应保持 100 只 fz 前缀股票（不丢失）。"""
        pool = sim_engine.state.get_pool("source")
        codes = pool.get_stock_codes()
        assert len(codes) == 100, f"source 池应有 100 只股票，实际 {len(codes)}"
        for code in codes:
            assert code.startswith("fz"), f"股票代码应以 fz 前缀：{code}"

    def test_pool_a_subset_of_source(self, sim_engine):
        """pool_A ⊆ source（KDJ 金叉筛选后的股票应在 source 中）。"""
        source_codes = set(sim_engine.state.get_pool("source").get_stock_codes())
        pool_a_codes = set(sim_engine.state.get_pool("pool_A").get_stock_codes())
        assert pool_a_codes.issubset(source_codes), (
            f"pool_A 不在 source 子集内："
            f"pool_a-source={pool_a_codes - source_codes}"
        )

    def test_pool_b_subset_of_source(self, sim_engine):
        """pool_B ⊆ source（MACD 金叉筛选后的股票应在 source 中）。"""
        source_codes = set(sim_engine.state.get_pool("source").get_stock_codes())
        pool_b_codes = set(sim_engine.state.get_pool("pool_B").get_stock_codes())
        assert pool_b_codes.issubset(source_codes), (
            f"pool_B 不在 source 子集内："
            f"pool_b-source={pool_b_codes - source_codes}"
        )

    def test_pool_c_subset_of_source(self, sim_engine):
        """pool_C ⊆ source（pool_C 的股票都来自 source 池）。

        pool_C 由 cond3 交集（pool_A ∩ pool_B）输出，采用 copy 模式累积。
        由于 pool_C 是多次交集触发的累积，而 pool_A/pool_B 是当前快照，
        pool_C 可能包含已离开 pool_A/pool_B 的股票（如 TTL 过期后），
        因此断言 pool_C ⊆ pool_A 不一定成立。但 pool_C 的所有股票
        都应来自 source 池（交集的源都是 source 子集）。
        """
        source_codes = set(sim_engine.state.get_pool("source").get_stock_codes())
        pool_c_codes = set(sim_engine.state.get_pool("pool_C").get_stock_codes())
        assert pool_c_codes.issubset(source_codes), (
            f"pool_C 不在 source 子集内："
            f"pool_c-source={pool_c_codes - source_codes}"
        )
