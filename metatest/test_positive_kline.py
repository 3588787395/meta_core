# -*- coding: utf-8 -*-
"""K 线合成正测试（Task 6）。

覆盖 SubTask 6.1 - 6.8：
  - 6.1 1min→5min/15min/30min/60min 合成
  - 6.2 60min→day 合成（time 重写为 00:00:00）
  - 6.3 day→week/month 合成（_PERIOD_KEY_FUNCS 表）
  - 6.4 _SYNTHESIS_RULES 表 10 个映射
  - 6.5 synthesize(bars, source, target) 单一入口
  - 6.6 BarComposer.on_tick 接受 event_ts 参数
  - 6.7 publish_data_changed 统一发布器
  - 6.8 _publish_tick_batch 批量发布

测试可能因源码 bug 而失败，这是正常的，不修改源码。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pytest

from core.event_bus import (
    BarComposed,
    DataChanged,
    EventBus,
    TickReceived,
)
from core.runtime_mode_module import (
    _PERIOD_KEY_FUNCS,
    _SYNTHESIS_RULES,
    synthesize,
    synthesize_kline,
)
from core.tick_bar_module import (
    BarComposer,
    DataUpdater,
    _publish_tick_batch,
    publish_data_changed,
)


# ---------------------------------------------------------------------------
# 测试数据构造辅助
# ---------------------------------------------------------------------------

def _make_1min_bars(n: int = 60, start_dt: datetime = None) -> List[Dict]:
    """构造 n 根 1 分钟 K 线，时间从 start_dt 起。"""
    if start_dt is None:
        start_dt = datetime(2024, 1, 2, 9, 30, 0)
    bars: List[Dict] = []
    for i in range(n):
        dt = start_dt + timedelta(minutes=i)
        bars.append({
            "time": dt.strftime("%Y-%m-%d %H:%M:%S"),
            "open": 10.0 + i * 0.01,
            "high": 10.5 + i * 0.01,
            "low": 9.5 + i * 0.01,
            "close": 10.2 + i * 0.01,
            "volume": 1000 + i,
            "amount": (1000 + i) * 10.0,
        })
    return bars


def _make_60min_bars(n: int = 4, start_dt: datetime = None) -> List[Dict]:
    """构造 n 根 60 分钟 K 线（同一交易日）。"""
    if start_dt is None:
        start_dt = datetime(2024, 1, 2, 9, 30, 0)
    bars: List[Dict] = []
    for i in range(n):
        dt = start_dt + timedelta(hours=i)
        bars.append({
            "time": dt.strftime("%Y-%m-%d %H:%M:%S"),
            "open": 10.0 + i,
            "high": 10.5 + i,
            "low": 9.5 + i,
            "close": 10.2 + i,
            "volume": 10000 + i,
            "amount": (10000 + i) * 10.0,
        })
    return bars


def _make_day_bars(n: int = 10, start_dt: datetime = None) -> List[Dict]:
    """构造 n 根日线，每根跨一个自然日。"""
    if start_dt is None:
        start_dt = datetime(2024, 1, 2, 0, 0, 0)
    bars: List[Dict] = []
    for i in range(n):
        dt = start_dt + timedelta(days=i)
        bars.append({
            "time": dt.strftime("%Y-%m-%d 00:00:00"),
            "open": 10.0 + i,
            "high": 10.5 + i,
            "low": 9.5 + i,
            "close": 10.2 + i,
            "volume": 100000 + i,
            "amount": (100000 + i) * 10.0,
        })
    return bars


class _StubState:
    """最小 PoolState 桩件，提供 BarComposer / publish_data_changed 所需接口。"""

    def __init__(self):
        self.latest_tick: Dict[str, Any] = {}
        self.prev_tick: Dict[str, Any] = {}
        self.bars: Dict[str, Any] = {}
        self.bars_history: Dict[str, Any] = {}
        self.changed_codes: set = set()

    def add_changed_codes(self, codes):
        for c in codes:
            self.changed_codes.add(str(c))

    def mark_data_dirty(self):
        pass


# ---------------------------------------------------------------------------
# SubTask 6.1: 1min→5min/15min/30min/60min 合成
# ---------------------------------------------------------------------------

class TestSynthesize1minToIntraday:
    """验证 1 分钟 K 线合成为更高频分钟周期。"""

    def test_1min_to_5min_aggregates_5_bars(self):
        """1min → 5min：每 5 根 1min 合成 1 根 5min。"""
        bars = _make_1min_bars(60)
        result = synthesize(bars, "1min", "5min")
        assert len(result) == 12  # 60 / 5
        # 第一根 5min 应取首根 1min 的 open
        assert result[0]["open"] == bars[0]["open"]
        # 第一根 5min close 取第 5 根 1min 的 close
        assert result[0]["close"] == bars[4]["close"]

    def test_1min_to_15min_aggregates_15_bars(self):
        """1min → 15min：每 15 根 1min 合成 1 根 15min。"""
        bars = _make_1min_bars(60)
        result = synthesize(bars, "1min", "15min")
        assert len(result) == 4  # 60 / 15
        assert result[0]["open"] == bars[0]["open"]
        assert result[0]["close"] == bars[14]["close"]

    def test_1min_to_30min_aggregates_30_bars(self):
        """1min → 30min：每 30 根 1min 合成 1 根 30min。"""
        bars = _make_1min_bars(60)
        result = synthesize(bars, "1min", "30min")
        assert len(result) == 2  # 60 / 30
        assert result[0]["close"] == bars[29]["close"]

    def test_1min_to_60min_aggregates_60_bars(self):
        """1min → 60min：每 60 根 1min 合成 1 根 60min。"""
        bars = _make_1min_bars(120)
        result = synthesize(bars, "1min", "60min")
        assert len(result) == 2  # 120 / 60
        assert result[0]["open"] == bars[0]["open"]
        assert result[0]["close"] == bars[59]["close"]

    def test_1min_aggregation_high_low_volume_correct(self):
        """合成 bar 的 high/low/volume 正确聚合。"""
        bars = _make_1min_bars(5)
        result = synthesize(bars, "1min", "5min")
        assert len(result) == 1
        expected_high = max(b["high"] for b in bars)
        expected_low = min(b["low"] for b in bars)
        expected_vol = sum(b["volume"] for b in bars)
        assert result[0]["high"] == expected_high
        assert result[0]["low"] == expected_low
        assert result[0]["volume"] == expected_vol


# ---------------------------------------------------------------------------
# SubTask 6.2: 60min→day 合成（time 重写为 00:00:00）
# ---------------------------------------------------------------------------

class TestSynthesize60minToDay:
    """验证 60 分钟 → 日线合成，time 字段重写为 00:00:00。"""

    def test_60min_to_day_single_day_aggregation(self):
        """同一交易日的多根 60min 合成为一根 day bar。"""
        bars = _make_60min_bars(4, start_dt=datetime(2024, 1, 2, 9, 30, 0))
        result = synthesize(bars, "60min", "day")
        assert len(result) == 1
        # time 应重写为 YYYY-MM-DD 00:00:00
        assert result[0]["time"].endswith(" 00:00:00")
        assert result[0]["time"].startswith("2024-01-02")

    def test_60min_to_day_open_close_correct(self):
        """day bar 的 open 取首根 60min open，close 取末根 60min close。"""
        bars = _make_60min_bars(4)
        result = synthesize(bars, "60min", "day")
        assert result[0]["open"] == bars[0]["open"]
        assert result[0]["close"] == bars[-1]["close"]

    def test_60min_to_day_multi_day_grouping(self):
        """跨交易日的 60min K 线按日分组，每日一根 day bar。"""
        day1 = _make_60min_bars(4, start_dt=datetime(2024, 1, 2, 9, 30, 0))
        day2 = _make_60min_bars(4, start_dt=datetime(2024, 1, 3, 9, 30, 0))
        all_bars = day1 + day2
        result = synthesize(all_bars, "60min", "day")
        assert len(result) == 2
        assert result[0]["time"].startswith("2024-01-02")
        assert result[1]["time"].startswith("2024-01-03")
        # 两根 day bar 的 time 均以 00:00:00 结尾
        for bar in result:
            assert bar["time"].endswith(" 00:00:00")


# ---------------------------------------------------------------------------
# SubTask 6.3: day→week/month 合成（_PERIOD_KEY_FUNCS 表）
# ---------------------------------------------------------------------------

class TestSynthesizeDayToWeekMonth:
    """验证 day → week/month 合成，依赖 _PERIOD_KEY_FUNCS 表。"""

    def test_period_key_funcs_table_has_day_week_month(self):
        """_PERIOD_KEY_FUNCS 表包含 day/week/month 三个周期键函数。"""
        assert "day" in _PERIOD_KEY_FUNCS
        assert "week" in _PERIOD_KEY_FUNCS
        assert "month" in _PERIOD_KEY_FUNCS

    def test_day_to_week_groups_by_monday(self):
        """day → week：按周一日期分组（ISO 周，周一至周日）。"""
        # 构造 10 根连续日 K（含周末），2024-01-01 是周一
        # _get_week_key 按 ISO 周（周一至周日）分组：
        #   第 1 周: 01-01(周一) ~ 01-07(周日) → bars[0]..bars[6]
        #   第 2 周: 01-08(周一) ~ 01-10(周三) → bars[7]..bars[9]
        bars = _make_day_bars(10, start_dt=datetime(2024, 1, 1, 0, 0, 0))
        result = synthesize(bars, "day", "week")
        assert len(result) == 2  # 2 周
        # 第一周 open 取首根，close 取该周最后一根（bars[6]，周日）
        assert result[0]["open"] == bars[0]["open"]
        assert result[0]["close"] == bars[6]["close"]

    def test_day_to_month_groups_by_year_month(self):
        """day → month：按 YYYY-MM 分组。"""
        # 构造跨 2 个月的日线
        bars = []
        bars.extend(_make_day_bars(3, start_dt=datetime(2024, 1, 2, 0, 0, 0)))
        bars.extend(_make_day_bars(3, start_dt=datetime(2024, 2, 2, 0, 0, 0)))
        result = synthesize(bars, "day", "month")
        assert len(result) == 2  # 2 个月

    def test_day_to_week_aggregation_correct(self):
        """day → week 合成的 high/low/volume 正确。"""
        bars = _make_day_bars(5, start_dt=datetime(2024, 1, 1, 0, 0, 0))
        result = synthesize(bars, "day", "week")
        assert len(result) == 1
        assert result[0]["high"] == max(b["high"] for b in bars)
        assert result[0]["low"] == min(b["low"] for b in bars)
        assert result[0]["volume"] == sum(b["volume"] for b in bars)


# ---------------------------------------------------------------------------
# SubTask 6.4: _SYNTHESIS_RULES 表 10 个映射
# ---------------------------------------------------------------------------

class TestSynthesisRulesTable:
    """验证 _SYNTHESIS_RULES 表包含 10 个 (source, target) 映射。"""

    def test_synthesis_rules_table_size(self):
        """_SYNTHESIS_RULES 表应有 10 个映射条目。"""
        assert len(_SYNTHESIS_RULES) == 10

    def test_synthesis_rules_contains_1min_mappings(self):
        """表包含 1min → 5min/15min/30min/60min 四个映射。"""
        for target in ("5min", "15min", "30min", "60min"):
            assert ("1min", target) in _SYNTHESIS_RULES

    def test_synthesis_rules_contains_5min_mappings(self):
        """表包含 5min → 15min/30min/60min 三个映射。"""
        for target in ("15min", "30min", "60min"):
            assert ("5min", target) in _SYNTHESIS_RULES

    def test_synthesis_rules_contains_60min_to_day(self):
        """表包含 60min → day 映射。"""
        assert ("60min", "day") in _SYNTHESIS_RULES

    def test_synthesis_rules_contains_day_to_week_month(self):
        """表包含 day → week 与 day → month 映射。"""
        assert ("day", "week") in _SYNTHESIS_RULES
        assert ("day", "month") in _SYNTHESIS_RULES

    def test_synthesis_rules_all_callables(self):
        """表中所有值必须是可调用对象。"""
        for key, fn in _SYNTHESIS_RULES.items():
            assert callable(fn), f"{key} 的值不是 callable"

    def test_synthesis_rules_keys_complete_set(self):
        """10 个 (source, target) 键完整集合验证。"""
        expected = {
            ("1min", "5min"), ("1min", "15min"), ("1min", "30min"), ("1min", "60min"),
            ("5min", "15min"), ("5min", "30min"), ("5min", "60min"),
            ("60min", "day"),
            ("day", "week"), ("day", "month"),
        }
        assert set(_SYNTHESIS_RULES.keys()) == expected


# ---------------------------------------------------------------------------
# SubTask 6.5: synthesize(bars, source, target) 单一入口
# ---------------------------------------------------------------------------

class TestSynthesizeSingleEntry:
    """验证 synthesize() 是合成的单一入口。"""

    def test_synthesize_returns_input_when_no_rule(self):
        """无对应规则时返回输入 bars 原样。"""
        bars = _make_1min_bars(5)
        result = synthesize(bars, "1min", "1min")  # 同周期无规则
        assert result is bars or result == bars

    def test_synthesize_returns_list(self):
        """synthesize 返回 list。"""
        bars = _make_1min_bars(10)
        result = synthesize(bars, "1min", "5min")
        assert isinstance(result, list)

    def test_synthesize_empty_bars_returns_empty(self):
        """空 bars 输入返回空列表。"""
        result = synthesize([], "1min", "5min")
        assert isinstance(result, list)
        assert len(result) == 0

    def test_synthesize_kline_recursive_dispatch(self):
        """synthesize_kline 支持递归分派（1min → day 经 60min 中转）。"""
        bars = _make_1min_bars(240)  # 4 小时
        # 1min → day 需递归：先 1min → 60min，再 60min → day
        result = synthesize_kline(bars, "1min", "day")
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_synthesize_kline_same_period_returns_input(self):
        """同周期 synthesize_kline 返回输入。"""
        bars = _make_1min_bars(5)
        result = synthesize_kline(bars, "1min", "1min")
        assert result is bars


# ---------------------------------------------------------------------------
# SubTask 6.6: BarComposer.on_tick 接受 event_ts 参数
# ---------------------------------------------------------------------------

class TestBarComposerOnTickEventTs:
    """验证 BarComposer.on_tick 接受可选 event_ts 参数。"""

    def test_on_tick_signature_accepts_event_ts(self):
        """on_tick 方法签名接受 event_ts 参数。"""
        import inspect
        sig = inspect.signature(BarComposer.on_tick)
        params = list(sig.parameters.keys())
        # 至少包含 self, codes, event_ts
        assert "event_ts" in params

    def test_on_tick_uses_event_ts_when_provided(self):
        """传入 event_ts 时使用该时间戳而非系统时钟。"""
        state = _StubState()
        # 提前写入 latest_tick
        state.latest_tick["code_a"] = {
            "code": "code_a",
            "open": 10.0, "high": 10.5, "low": 9.5,
            "close": 10.2, "volume": 100,
            "_ts": 34500.0,  # 09:30:00
            "_hash": "fake",
        }
        composer = BarComposer(state, bus=None, periods=["1m"])
        # event_ts 显式传入
        composer.on_tick(["code_a"], event_ts=34500.0)
        bar = composer.get_bar("1m", "code_a")
        assert bar is not None
        assert bar["code"] == "code_a"

    def test_on_tick_event_ts_none_falls_back_to_state_time(self):
        """event_ts 为 None 时退化到 time_at(state)。"""
        state = _StubState()
        state.latest_tick["code_a"] = {
            "code": "code_a",
            "open": 10.0, "high": 10.5, "low": 9.5,
            "close": 10.2, "volume": 100,
            "_ts": 34500.0,
            "_hash": "fake",
        }
        composer = BarComposer(state, bus=None, periods=["1m"])
        # 不传 event_ts，应不抛异常
        composer.on_tick(["code_a"])
        bar = composer.get_bar("1m", "code_a")
        assert bar is not None

    def test_on_tick_empty_codes_returns_silently(self):
        """空 codes 列表应静默返回。"""
        state = _StubState()
        composer = BarComposer(state, bus=None, periods=["1m"])
        # 不应抛异常
        composer.on_tick([], event_ts=34500.0)


# ---------------------------------------------------------------------------
# SubTask 6.7: publish_data_changed 统一发布器
# ---------------------------------------------------------------------------

class TestPublishDataChanged:
    """验证 publish_data_changed 统一发布器。"""

    def test_publish_data_changed_tick_source(self):
        """source='tick' 发布 DataChanged 事件。"""
        bus = EventBus()
        state = _StubState()
        captured: List[Any] = []

        def _handler(event):
            captured.append(event)

        bus.subscribe(DataChanged, _handler)
        publish_data_changed(bus, state, "tick", ["code_a"], 34500.0)
        assert len(captured) == 1
        assert captured[0].source == "tick"
        assert captured[0].codes == ["code_a"]
        assert captured[0].ts == 34500.0

    def test_publish_data_changed_bar_source_publishes_bar_composed(self):
        """source='bar' 同时发布 DataChanged(bar) 与每只 code 的 BarComposed。"""
        bus = EventBus()
        state = _StubState()
        # 预置 state.bars["5m"]["code_a"]
        state.bars["5m"] = {"code_a": {"code": "code_a", "close": 10.0}}
        captured: List[Any] = []

        def _handler(event):
            captured.append(event)

        bus.subscribe(DataChanged, _handler)
        bus.subscribe(BarComposed, _handler)
        publish_data_changed(
            bus, state, "bar", ["code_a"], 34600.0, period="5m"
        )
        # 应至少发布 1 个 DataChanged + 1 个 BarComposed
        assert len(captured) >= 2
        types = [type(e).__name__ for e in captured]
        assert "DataChanged" in types
        assert "BarComposed" in types

    def test_publish_data_changed_empty_codes_noop(self):
        """空 codes 时静默返回不发布。"""
        bus = EventBus()
        state = _StubState()
        captured: List[Any] = []

        def _handler(event):
            captured.append(event)

        bus.subscribe(DataChanged, _handler)
        publish_data_changed(bus, state, "tick", [], 34500.0)
        assert len(captured) == 0

    def test_publish_data_changed_invalid_bus_noop(self):
        """bus 非 EventBus 时静默返回。"""
        state = _StubState()
        # 不应抛异常
        publish_data_changed(None, state, "tick", ["code_a"], 34500.0)
        publish_data_changed("not_a_bus", state, "tick", ["code_a"], 34500.0)


# ---------------------------------------------------------------------------
# SubTask 6.8: _publish_tick_batch 批量发布
# ---------------------------------------------------------------------------

class TestPublishTickBatch:
    """验证 _publish_tick_batch 批量发布 TickReceived 事件。"""

    def test_publish_tick_batch_publishes_per_code(self):
        """对 tick_data 中每个 code 发布一个 TickReceived 事件。"""
        bus = EventBus()
        captured: List[Any] = []

        def _handler(event):
            captured.append(event)

        bus.subscribe(TickReceived, _handler)
        tick_data = {
            "code_a": {"close": 10.0, "_ts": 34500.0},
            "code_b": {"close": 20.0, "_ts": 34500.0},
        }
        _publish_tick_batch(bus, tick_data, 34500.0)
        assert len(captured) == 2
        codes = {e.code for e in captured}
        assert codes == {"code_a", "code_b"}

    def test_publish_tick_batch_uses_ts_param(self):
        """传入 ts 时所有 tick 使用该 ts。"""
        bus = EventBus()
        captured: List[Any] = []

        def _handler(event):
            captured.append(event)

        bus.subscribe(TickReceived, _handler)
        _publish_tick_batch(
            bus, {"code_a": {"close": 10.0, "_ts": 99999.0}}, 34500.0
        )
        assert captured[0].ts == 34500.0

    def test_publish_tick_batch_ts_none_uses_tick_ts(self):
        """ts=None 时使用 tick._ts 字段。"""
        bus = EventBus()
        captured: List[Any] = []

        def _handler(event):
            captured.append(event)

        bus.subscribe(TickReceived, _handler)
        _publish_tick_batch(
            bus, {"code_a": {"close": 10.0, "_ts": 34700.0}}, None
        )
        assert captured[0].ts == 34700.0

    def test_publish_tick_batch_empty_data_noop(self):
        """空 tick_data 或非 dict 静默返回。"""
        bus = EventBus()
        captured: List[Any] = []

        def _handler(event):
            captured.append(event)

        bus.subscribe(TickReceived, _handler)
        _publish_tick_batch(bus, {}, 34500.0)
        _publish_tick_batch(bus, None, 34500.0)
        _publish_tick_batch(bus, "not_dict", 34500.0)
        assert len(captured) == 0

    def test_publish_tick_batch_skips_invalid_tick(self):
        """非 dict tick 条目跳过。"""
        bus = EventBus()
        captured: List[Any] = []

        def _handler(event):
            captured.append(event)

        bus.subscribe(TickReceived, _handler)
        _publish_tick_batch(
            bus,
            {"code_a": {"close": 10.0}, "code_b": "invalid", "": {"close": 5.0}},
            34500.0,
        )
        # code_b 非 dict 跳过，空 code 跳过，只发 code_a
        assert len(captured) == 1
        assert captured[0].code == "code_a"


# ============================================================================
# 变更 I：_BASE_PERIOD_TARGETS 表合并 if self._base_period == 链回归断言
# ============================================================================


class TestChangeIBasePeriodTargetsTable:
    """变更 I：_BASE_PERIOD_TARGETS 表含 1min/5min/day，消除 if self._base_period == 链。"""

    def test_base_period_targets_table_exists(self):
        """_BASE_PERIOD_TARGETS 表存在。"""
        from core.runtime_mode_module import _BASE_PERIOD_TARGETS
        assert isinstance(_BASE_PERIOD_TARGETS, dict), \
            "_BASE_PERIOD_TARGETS 应为 dict"

    def test_base_period_targets_contains_three_keys(self):
        """_BASE_PERIOD_TARGETS 表含 1min/5min/day 三键。"""
        from core.runtime_mode_module import _BASE_PERIOD_TARGETS
        assert "1min" in _BASE_PERIOD_TARGETS, \
            "_BASE_PERIOD_TARGETS 应含 1min 键"
        assert "5min" in _BASE_PERIOD_TARGETS, \
            "_BASE_PERIOD_TARGETS 应含 5min 键"
        assert "day" in _BASE_PERIOD_TARGETS, \
            "_BASE_PERIOD_TARGETS 应含 day 键"

    def test_base_period_targets_values_are_lists(self):
        """_BASE_PERIOD_TARGETS 每个键映射到 list（合成目标周期列表）。"""
        from core.runtime_mode_module import _BASE_PERIOD_TARGETS
        for key in ("1min", "5min", "day"):
            val = _BASE_PERIOD_TARGETS[key]
            assert isinstance(val, list), \
                f"_BASE_PERIOD_TARGETS[{key}] 应为 list，实际 {type(val)}"

    def test_no_if_self_base_period_chain_in_runtime_module(self):
        """Grep 验证：runtime_mode_module.py 中 if self._base_period == = 0。"""
        import re
        from pathlib import Path
        rt_path = Path(__file__).resolve().parent.parent / "core" / "runtime_mode_module.py"
        src = rt_path.read_text(encoding="utf-8")
        legacy_pattern = r"if self\._base_period =="
        matches = re.findall(legacy_pattern, src)
        assert len(matches) == 0, (
            f"runtime_mode_module 不应含 if self._base_period == 硬编码分支"
            f"（变更 I 已表驱动化），实际 {len(matches)} 处"
        )

    def test_base_period_targets_used_for_synthesis(self):
        """runtime_mode_module 引用 _BASE_PERIOD_TARGETS 作为合成目标源。"""
        import re
        from pathlib import Path
        rt_path = Path(__file__).resolve().parent.parent / "core" / "runtime_mode_module.py"
        src = rt_path.read_text(encoding="utf-8")
        assert "_BASE_PERIOD_TARGETS" in src, \
            "runtime_mode_module 应引用 _BASE_PERIOD_TARGETS 表（变更 I 表驱动）"


# === Task 28.6 回归断言：converge-meta-essence-v4 阶段 3 C3 + C10 收敛状态 ===


class TestConvergenceRegressionV4:
    """SubTask 28.6：converge-meta-essence-v4 C3 BarHashMixin + C10 _aggregate_ohlcv 收敛回归。"""

    def test_bar_hash_mixin_present(self):
        """_hashing.py 含 BarHashMixin（C3 族3 bar_hash accessor 合并）。"""
        import ast
        from pathlib import Path
        tree = ast.parse((Path(__file__).resolve().parent.parent / "core" / "_hashing.py").read_text(encoding="utf-8"))
        classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        assert "BarHashMixin" in classes, \
            "_hashing.py 应定义 BarHashMixin（C3 族3 bar_hash accessor 合并）"

    def test_aggregate_ohlcv_helper_present(self):
        """runtime_mode_module 含 _aggregate_ohlcv helper（C10 OHLCV 字面量合并）。"""
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "core" / "runtime_mode_module.py").read_text(encoding="utf-8")
        assert "def _aggregate_ohlcv" in src, \
            "runtime_mode_module 应含 _aggregate_ohlcv helper（C10 OHLCV 字面量合并）"

    def test_date_keys_table_present(self):
        """runtime_mode_module 含 _DATE_KEYS 表（C10 日期函数合并）。"""
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "core" / "runtime_mode_module.py").read_text(encoding="utf-8")
        assert "_DATE_KEYS" in src, \
            "runtime_mode_module 应含 _DATE_KEYS 表（C10 日期函数表驱动）"
