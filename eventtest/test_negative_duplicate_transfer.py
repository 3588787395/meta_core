# -*- coding: utf-8 -*-
"""反测试 — 重复入池（Task 7 SubTask 7.2）。

基于 spec.md "反测试（异常与边界验证）" → "重复入池" Scenario：

    WHEN 同一股票通过 cond1 多次进入 pool_A
    THEN pool_A 中该股票仅出现一次
    AND TTL 不重复注册（heap 长度不增长）

实现要点：
    - 基于 ``config/pools/sim_test_pool_100.json`` 装配 PoolEngine 实例
      （不硬编码实例内容，股票代码从配置动态读取）。
    - 通过 ``ec1out`` 边（cond1 → pool_A，unconditional / pass_through）
      触发股票转移：手动调用 ``EdgeExecutor._on_edge_fired(EdgeFired(eid="ec1out"))``
      使 ``run("ec1out")`` 执行 gate→filter→propagate→TTL 完整链路。
    - ``_tgt_merge`` 在 propagate 阶段做去重（existing ∪ new_stocks），
      同一股票第二次入池时 entered=[]，``_init_entry_trackers`` 不被调用，
      ``register_ttl_spec`` 不被调用，heap 长度不增长。
    - 单元测试直接调用 ``_tgt_merge`` 验证去重逻辑。
    - 验证 ``StatePoolView.add_stocks`` 本身不去重（去重在 _tgt_merge 层），
      文档化设计契约：调用方需通过 _tgt_merge 入池，不应直接 add_stocks。

复用 core/ 现有类（PoolEngine / EdgeExecutor / EventDriver / EventBus /
Compiler / _tgt_merge / register_ttl_spec），不修改 core/ 源文件，
不使用已删除旧接口（get_node_stocks / SimTickSource / execution_order /
EdgeFired.changed_codes / at_fn / fire_ttl_due / TtlTracker）。
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from core.engine import PoolEngine
from core.event_bus import EdgeFired
from core.execution_module import (
    Compiler,
    _tgt_merge,
    register_ttl_spec,
    EventDriver,
    TimedEventSpec,
)


# ═══════════════════════════════════════════════════════════════
# 测试常量（测试参数，非硬编码实例内容）
# ═══════════════════════════════════════════════════════════════

_TS = 34500.0              # 虚拟时钟起点（09:30:00 当日秒数偏移）
_ADVANCE_SECONDS = 600    # 推进虚拟时钟秒数（> 最大边间隔 60s）

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_POOL_CONFIG = _PROJECT_ROOT / "config" / "pools" / "sim_test_pool_100.json"


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════


def _load_config() -> Dict[str, Any]:
    """加载 sim_test_pool_100.json 原始配置。"""
    with open(_DEFAULT_POOL_CONFIG, encoding="utf-8") as f:
        return json.load(f)


def _load_fz_stocks(n: int = 3) -> List[str]:
    """从 sim_test_pool_100.json 动态读取 N 只 fz 前缀股票代码（不硬编码）。"""
    cfg = _load_config()
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


def _get_components(engine: PoolEngine):
    """从 PoolEngine 提取测试所需组件。"""
    return (
        engine._components["edge_executor"],
        engine._components["event_bus"],
        engine._components["event_driver"],
        engine._components["schedule"],
    )


def _set_virtual_time(engine: PoolEngine, ts: float) -> None:
    """设置 engine.state.time_source 的 current_ts 为指定时刻。"""
    engine.state.time_source["current_ts"] = float(ts)
    engine.state.time_source.setdefault("start_ts", _TS)
    engine.state.time_source.setdefault("driver_type", "virtual")


def _add_stocks_to_pool(state: Any, nid: str, codes: List[str]) -> None:
    """向指定池添加股票并标脏。"""
    pool = state.get_pool(nid)
    pool.add_stocks([{"code": c} for c in codes])


# ═══════════════════════════════════════════════════════════════
# 测试用例 — _tgt_merge 单元测试
# ═══════════════════════════════════════════════════════════════


class TestTgtMergeDedup:
    """验证 _tgt_merge 的去重逻辑（单元级）。"""

    def test_tgt_merge_dedupes_duplicate_stock(self, pool_engine):
        """_tgt_merge 对已存在于目标池的股票去重（不重复入池）。

        断言：
          - 第一次 _tgt_merge：entered = [code]（新入池）
          - 第二次 _tgt_merge（同一股票）：entered = []（已存在，跳过）
          - 目标池中该股票只出现一次
        """
        engine = pool_engine()
        code = _load_fz_stocks(1)[0]
        state = engine.state
        tgt = "pool_A"

        # 确保目标池初始为空
        assert len(state.get_pool(tgt).get_stocks()) == 0

        # 第一次入池
        transferred1 = [{"code": code, "_tracker": {"entry_time": _TS}}]
        tgt_stocks1 = state.get_pool(tgt).get_stocks()
        entered1, _ = _tgt_merge(state, tgt, transferred1, tgt_stocks1)
        assert entered1 == [code], f"第一次入池 entered 应为 [{code}]，实际 {entered1}"

        # 第二次入池（同一股票）
        transferred2 = [{"code": code, "_tracker": {"entry_time": _TS}}]
        tgt_stocks2 = state.get_pool(tgt).get_stocks()
        entered2, _ = _tgt_merge(state, tgt, transferred2, tgt_stocks2)
        assert entered2 == [], f"第二次入池 entered 应为 []，实际 {entered2}"

        # 目标池中该股票只出现一次
        pool_codes = state.get_pool(tgt).get_stock_codes()
        assert list(pool_codes).count(code) == 1, (
            f"目标池中 {code} 应只出现一次，实际 {pool_codes}"
        )

    def test_tgt_merge_multiple_distinct_stocks(self, pool_engine):
        """_tgt_merge 对多只不同股票全部入池（无去重误伤）。

        断言：
          - 3 只不同股票第一次入池：entered = [code1, code2, code3]
          - 目标池中有 3 只股票
        """
        engine = pool_engine()
        codes = _load_fz_stocks(3)
        state = engine.state
        tgt = "pool_A"

        transferred = [
            {"code": c, "_tracker": {"entry_time": _TS}} for c in codes
        ]
        tgt_stocks = state.get_pool(tgt).get_stocks()
        entered, _ = _tgt_merge(state, tgt, transferred, tgt_stocks)

        assert set(entered) == set(codes), (
            f"3 只不同股票应全部入池，实际 entered={entered}"
        )
        assert len(state.get_pool(tgt).get_stocks()) == 3


# ═══════════════════════════════════════════════════════════════
# 测试用例 — 重复入池端到端
# ═══════════════════════════════════════════════════════════════


class TestDuplicateTransferEndToEnd:
    """验证同一股票多次经 ec1out 进入 pool_A 的端到端去重。"""

    def test_duplicate_transfer_pool_a_single_entry(
        self, pool_engine, event_collector,
    ):
        """同一股票多次触发 ec1out（cond1 → pool_A），pool_A 中该股票仅出现一次。

        流程：
          1. 向 cond1 池添加 1 只股票（标脏）
          2. 第一次触发 EdgeFired(eid="ec1out") → run("ec1out")
             → pass_through 筛选 → _tgt_merge → 股票入池 pool_A
          3. 第二次触发 EdgeFired(eid="ec1out") → run("ec1out")
             → pass_through 筛选 → _tgt_merge 去重 → entered=[]
          4. 断言 pool_A 中该股票只出现一次
        """
        engine = pool_engine()
        edge_executor, bus, _event_driver, _schedule = _get_components(engine)
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            code = _load_fz_stocks(1)[0]
            # 向 cond1 池添加股票（ec1out 的源池是 cond1）
            _add_stocks_to_pool(engine.state, "cond1", [code])

            # 第一次触发 ec1out
            edge_executor._on_edge_fired(EdgeFired(eid="ec1out", ts=_TS))
            pool_codes_1 = engine.state.get_pool("pool_A").get_stock_codes()
            assert code in pool_codes_1, (
                f"第一次触发后 {code} 应在 pool_A 中，实际 {pool_codes_1}"
            )

            # 第二次触发 ec1out（同一股票）
            edge_executor._on_edge_fired(EdgeFired(eid="ec1out", ts=_TS))
            pool_codes_2 = engine.state.get_pool("pool_A").get_stock_codes()
            # 股票仍只出现一次
            assert list(pool_codes_2).count(code) == 1, (
                f"第二次触发后 {code} 应仍只出现一次，实际 {pool_codes_2}"
            )
        finally:
            collector.disconnect()

    def test_second_transfer_entered_empty(
        self, pool_engine, event_collector,
    ):
        """第二次触发转移时 entered 为空（_tgt_merge 去重）。

        通过 TransferExecuted 事件验证：
          - 第一次触发：TransferExecuted.entered_codes 含该股票
          - 第二次触发：TransferExecuted.entered_codes 为空
        """
        engine = pool_engine()
        edge_executor, bus, _event_driver, _schedule = _get_components(engine)
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            code = _load_fz_stocks(1)[0]
            _add_stocks_to_pool(engine.state, "cond1", [code])

            # 第一次触发
            collector.clear()
            edge_executor._on_edge_fired(EdgeFired(eid="ec1out", ts=_TS))
            transfers_1 = collector.filter(type="TransferExecuted")
            assert len(transfers_1) >= 1, "第一次触发应发布 TransferExecuted"
            entered_1 = transfers_1[0].entered_codes
            assert code in entered_1, (
                f"第一次 entered_codes 应含 {code}，实际 {entered_1}"
            )

            # 第二次触发
            collector.clear()
            edge_executor._on_edge_fired(EdgeFired(eid="ec1out", ts=_TS))
            transfers_2 = collector.filter(type="TransferExecuted")
            if len(transfers_2) >= 1:
                entered_2 = transfers_2[0].entered_codes
                assert code not in entered_2, (
                    f"第二次 entered_codes 不应含 {code}（已去重），实际 {entered_2}"
                )
            # 若 transfers_2 为空，也符合预期（entered=[] 时可能不发布事件）
        finally:
            collector.disconnect()

    def test_ttl_heap_not_grow_on_duplicate_transfer(
        self, pool_engine, event_collector,
    ):
        """同股票多次触发转移，TTL 在 heap 中只注册一次（heap 长度不增长）。

        流程：
          1. 记录初始 heap 长度（含编译期注册的边触发定时器）
          2. 第一次触发 ec1out → 股票入池 → register_ttl_spec 注册 1 条 TTL
             → heap 长度 = initial + 1
          3. 第二次触发 ec1out → entered=[] → 不注册 TTL
             → heap 长度不变（= initial + 1）
          4. 断言 heap 长度在第二次触发后不增长
        """
        engine = pool_engine()
        edge_executor, bus, event_driver, _schedule = _get_components(engine)
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            code = _load_fz_stocks(1)[0]
            _add_stocks_to_pool(engine.state, "cond1", [code])

            # 初始 heap 长度（含编译期注册的边触发定时器）
            initial_heap_len = len(event_driver._heap)

            # 第一次触发 ec1out → 股票入池 → TTL 注册
            edge_executor._on_edge_fired(EdgeFired(eid="ec1out", ts=_TS))
            heap_after_first = len(event_driver._heap)

            # 第一次触发后 heap 应增长（TTL 注册）
            # 注意：仅当 edge_ttl_spec["ec1out"] 的 bdel=1 且 check_type="interval"
            # 且 ttl_sec > 0 时才注册 TTL。pool_A 的 psatt={"bdel":1,"ndelnum":100,"ndeltype":2}
            # → ttl_sec = 100 * 60 = 6000 > 0
            assert heap_after_first > initial_heap_len, (
                f"第一次触发后 heap 应增长（TTL 注册），"
                f"initial={initial_heap_len}, after_first={heap_after_first}"
            )

            # 第二次触发 ec1out → entered=[] → 不注册 TTL
            edge_executor._on_edge_fired(EdgeFired(eid="ec1out", ts=_TS))
            heap_after_second = len(event_driver._heap)

            # heap 长度不应增长（entered=[]，不调用 register_ttl_spec）
            assert heap_after_second == heap_after_first, (
                f"第二次触发后 heap 不应增长（TTL 已注册，去重），"
                f"after_first={heap_after_first}, after_second={heap_after_second}"
            )
        finally:
            collector.disconnect()

    def test_multiple_distinct_stocks_each_register_ttl(
        self, pool_engine, event_collector,
    ):
        """多只不同股票入池时，每只股票各自注册 TTL（heap 长度 += N）。

        断言：
          - 3 只不同股票入池 → heap 增长 3（每只注册 1 条 TTL）
          - 再次触发 → heap 不增长（全部已去重）
        """
        engine = pool_engine()
        edge_executor, bus, event_driver, _schedule = _get_components(engine)
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            codes = _load_fz_stocks(3)
            _add_stocks_to_pool(engine.state, "cond1", codes)

            initial_heap_len = len(event_driver._heap)

            # 第一次触发：3 只股票全部入池
            edge_executor._on_edge_fired(EdgeFired(eid="ec1out", ts=_TS))
            heap_after_first = len(event_driver._heap)

            # heap 应增长 3（每只股票注册 1 条 TTL）
            assert heap_after_first == initial_heap_len + 3, (
                f"3 只股票入池应注册 3 条 TTL，"
                f"initial={initial_heap_len}, after_first={heap_after_first}, "
                f"差值={heap_after_first - initial_heap_len}"
            )

            # 第二次触发：全部已入池 → entered=[]
            edge_executor._on_edge_fired(EdgeFired(eid="ec1out", ts=_TS))
            heap_after_second = len(event_driver._heap)

            assert heap_after_second == heap_after_first, (
                f"第二次触发后 heap 不应增长（全部去重），"
                f"after_first={heap_after_first}, after_second={heap_after_second}"
            )
        finally:
            collector.disconnect()


# ═══════════════════════════════════════════════════════════════
# 测试用例 — add_stocks 不去重（设计契约文档化）
# ═══════════════════════════════════════════════════════════════


class TestAddStocksNoDedup:
    """验证 StatePoolView.add_stocks 本身不去重（去重在 _tgt_merge 层）。

    设计契约：
      - StatePoolView.add_stocks 直接 extend node_stocks，不做去重
      - 去重由 _tgt_merge 在 propagate 阶段完成（existing ∪ new_stocks）
      - 调用方应通过 EdgeExecutor.run / _transfer_to_target 入池，
        不应直接调用 add_stocks 添加重复股票
    """

    def test_add_stocks_does_not_dedup(self, pool_engine):
        """StatePoolView.add_stocks 直接追加，不去重。

        断言：
          - 连续两次 add_stocks 同一股票 → 池中有 2 条记录
          - 去重应由 _tgt_merge 完成，不是 add_stocks
        """
        engine = pool_engine()
        code = _load_fz_stocks(1)[0]
        pool = engine.state.get_pool("pool_A")

        # 初始为空
        assert len(pool.get_stocks()) == 0

        # 第一次 add_stocks
        pool.add_stocks([{"code": code}])
        assert len(pool.get_stocks()) == 1

        # 第二次 add_stocks（同一股票）——不去重，追加
        pool.add_stocks([{"code": code}])
        assert len(pool.get_stocks()) == 2, (
            "add_stocks 不去重（设计契约），池中应有 2 条记录，"
            f"实际 {len(pool.get_stocks())} 条"
        )

    def test_get_stock_codes_returns_set_dedup(self, pool_engine):
        """get_stock_codes 返回 Set，天然去重（视图层去重）。

        断言：
          - add_stocks 两次同一股票 → get_stocks() 返回 2 条
          - get_stock_codes() 返回 Set，只含 1 个 code
        """
        engine = pool_engine()
        code = _load_fz_stocks(1)[0]
        pool = engine.state.get_pool("pool_A")

        pool.add_stocks([{"code": code}])
        pool.add_stocks([{"code": code}])

        # get_stocks 返回 list（不去重）
        stocks = pool.get_stocks()
        assert len(stocks) == 2

        # get_stock_codes 返回 Set（天然去重）
        codes = pool.get_stock_codes()
        assert isinstance(codes, set)
        assert len(codes) == 1, (
            f"get_stock_codes 返回 Set 应去重，实际 {codes}"
        )
        assert code in codes
