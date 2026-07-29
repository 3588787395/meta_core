"""Test 10: Data Integrity — DI-001 ~ DI-007.

I14：迁移至 DataUpdater.apply_data 路径（_inject_bar_data 已于 I13 删除）。
验证 apply_data 写 latest_tick 不污染 node_stocks 身份字段、bar 字段正确落表、
空/NaN/Inf 输入不崩溃，以及 LRUCache TTL/LRU 行为。

Key invariants:
  - apply_data 只写 latest_tick/prev_tick，绝不触碰 node_stocks 身份字段
    （code/label/_tracker/indate/intime/inprice）——结构性保护
  - Bar fields (open/high/low/close/volume/amount) 必须正确写入 latest_tick[code]
  - LRUCache TTL expiry must auto-clean expired entries
  - LRUCache LRU eviction must evict oldest entries when max_entries exceeded
  - Abnormal data (empty/NaN/Inf) must not crash the engine
"""
from __future__ import annotations

import math
import time
from typing import Any

import pytest

from meta_core.core.engine import PoolEngine
from tests._test_cache import LRUCache
from meta_core.core.tick_bar_module import DataUpdater
from meta_core.core.runtime_mode_module import PoolState
from simtests.conftest import make_tdx_simple_pool


def _make_engine():
    """Create a PoolEngine configured for simulation mode."""
    eng = PoolEngine()
    return eng


def _make_pool_with_stocks():
    """Build a simple pool with pre-populated candidate stocks."""
    stocks = [
        {'code': '600000', 'label': '浦发银行', '_tracker': {'entry_price': 10.5, 'entry_time': 1234567890.0}},
        {'code': '000001', 'label': '平安银行', '_tracker': {'entry_price': 15.2, 'entry_time': 1234567891.0}},
    ]
    return make_tdx_simple_pool(
        candidate_stocks=stocks,
        condition_nset=5,
        condition_params={'ntjindexno': 0},
        edge_tran=0,
    )


def _make_data_updater():
    """构造 DataUpdater + PoolState，用于 apply_data 路径测试。"""
    state = PoolState()
    return DataUpdater(state, None), state


class TestDataInjectionIntegrity:
    """DI-001 ~ DI-003: apply_data 写 latest_tick 不污染 node_stocks 身份字段。"""

    def test_di_001_injection_preserves_code_and_label(self):
        """DI-001: apply_data 不覆盖 node_stocks 的 code/label（正向）。

        I14：apply_data 只写 latest_tick，结构性保证 node_stocks 身份字段不被触碰。
        即便 bar_data 携带伪造 code/label，node_stocks 仍保持原值。
        """
        eng = _make_engine()
        pool = _make_pool_with_stocks()
        nodes = {n['id']: n for n in pool.get('nodes', [])}
        node_stocks = eng._init_node_stocks(nodes)

        orig_codes = [s.get('code') for s in node_stocks.get('candidate_1', [])]
        orig_labels = [s.get('label') for s in node_stocks.get('candidate_1', [])]

        du, state = _make_data_updater()
        bar_data = {
            '600000': {'close': 11.0, 'open': 10.8, 'high': 11.2, 'low': 10.6, 'volume': 1000, 'amount': 11000.0,
                        'code': 'HACKED', 'label': 'HACKED_LABEL'},
            '000001': {'close': 16.0, 'open': 15.8, 'high': 16.2, 'low': 15.6, 'volume': 2000, 'amount': 32000.0,
                        'code': 'HACKED', 'label': 'HACKED_LABEL'},
        }
        du.apply_data(bar_data)

        # node_stocks 身份字段不变（apply_data 从不触碰 node_stocks）
        injected = node_stocks.get('candidate_1', [])
        assert len(injected) == 2, f"BUG: DI-001 node_stocks 应有 2 只股票, 实际 {len(injected)}"
        for i, s in enumerate(injected):
            assert s.get('code') == orig_codes[i], \
                f"BUG: DI-001 node_stocks code 被覆盖: 期望 {orig_codes[i]}, 实际 {s.get('code')}"
            assert s.get('label') == orig_labels[i], \
                f"BUG: DI-001 node_stocks label 被覆盖: 期望 {orig_labels[i]}, 实际 {s.get('label')}"
        # latest_tick 接收 bar 字段（含伪造 code/label，但那是 latest_tick 的副本，非 node_stocks）
        assert state.latest_tick['600000']['close'] == 11.0
        assert state.latest_tick['000001']['close'] == 16.0

    def test_di_002_injection_preserves_tracker(self):
        """DI-002: apply_data 不覆盖 node_stocks 的 _tracker（正向）。

        I14：apply_data 只写 latest_tick，结构性保证 node_stocks._tracker 不被触碰。
        """
        eng = _make_engine()
        pool = _make_pool_with_stocks()
        nodes = {n['id']: n for n in pool.get('nodes', [])}
        node_stocks = eng._init_node_stocks(nodes)

        orig_trackers = [s.get('_tracker') for s in node_stocks.get('candidate_1', [])]

        du, state = _make_data_updater()
        bar_data = {
            '600000': {'close': 11.0, 'open': 10.8, 'high': 11.2, 'low': 10.6, 'volume': 1000, 'amount': 11000.0,
                        '_tracker': {'entry_price': 999.0, 'HACKED': True}},
            '000001': {'close': 16.0, 'open': 15.8, 'high': 16.2, 'low': 15.6, 'volume': 2000, 'amount': 32000.0,
                        '_tracker': {'entry_price': 888.0, 'HACKED': True}},
        }
        du.apply_data(bar_data)

        injected = node_stocks.get('candidate_1', [])
        for i, s in enumerate(injected):
            tracker = s.get('_tracker')
            assert tracker == orig_trackers[i], \
                f"BUG: DI-002 node_stocks _tracker 被覆盖: 期望 {orig_trackers[i]}, 实际 {tracker}"
            assert not tracker.get('HACKED'), \
                f"BUG: DI-002 _tracker 含注入的 HACKED 字段: {tracker}"

    def test_di_003_injection_writes_bar_fields_correctly(self):
        """DI-003: apply_data 正确写入 latest_tick bar 字段（正向）。

        I14：open/high/low/close/volume/amount 必须正确落入 latest_tick[code]。
        """
        du, state = _make_data_updater()
        bar_data = {
            '600000': {'close': 11.0, 'open': 10.8, 'high': 11.2, 'low': 10.6, 'volume': 1000, 'amount': 11000.0},
            '000001': {'close': 16.0, 'open': 15.8, 'high': 16.2, 'low': 15.6, 'volume': 2000, 'amount': 32000.0},
        }
        du.apply_data(bar_data)

        s1 = state.latest_tick.get('600000')
        assert s1 is not None, "BUG: DI-003 latest_tick 未找到 600000"
        assert s1.get('close') == 11.0, f"BUG: DI-003 close 应为 11.0, 实际 {s1.get('close')}"
        assert s1.get('open') == 10.8, f"BUG: DI-003 open 应为 10.8, 实际 {s1.get('open')}"
        assert s1.get('high') == 11.2, f"BUG: DI-003 high 应为 11.2, 实际 {s1.get('high')}"
        assert s1.get('low') == 10.6, f"BUG: DI-003 low 应为 10.6, 实际 {s1.get('low')}"
        assert s1.get('volume') == 1000, f"BUG: DI-003 volume 应为 1000, 实际 {s1.get('volume')}"
        assert s1.get('amount') == 11000.0, f"BUG: DI-003 amount 应为 11000.0, 实际 {s1.get('amount')}"


class TestCacheTTLAndLRU:
    """DI-004 ~ DI-005: Cache TTL expiry and LRU eviction."""

    def test_di_004_cache_ttl_expiry_auto_cleans(self):
        """DI-004: 缓存 TTL 过期自动清理（正向）。

        设置短 TTL，等待过期后，get() 应返回 default（条目已清理）。
        """
        cache = LRUCache(max_entries=100, default_ttl=0.1)  # 100ms TTL
        cache.set("key1", {"data": "value1"})

        # Immediately accessible
        assert cache.get("key1") is not None, "BUG: DI-004 设置后立即读取应成功"

        # Wait for TTL to expire
        time.sleep(0.15)

        # Should be expired now
        result = cache.get("key1", default="EXPIRED")
        assert result == "EXPIRED", \
            f"BUG: DI-004 TTL 过期后应返回 default, 实际 {result}"
        assert "key1" not in cache, \
            f"BUG: DI-004 TTL 过期后 key1 不应在缓存中, 实际 {list(cache.keys())}"

    def test_di_005_cache_lru_eviction_on_max_entries(self):
        """DI-005: 缓存 LRU 淘汰机制（正向）。

        超过 max_entries 时，最旧条目应被淘汰。
        """
        cache = LRUCache(max_entries=3, default_ttl=300)
        cache.set("k1", "v1")
        cache.set("k2", "v2")
        cache.set("k3", "v3")

        # All 3 should be present
        assert cache.get("k1") == "v1", "BUG: DI-005 k1 应存在"
        assert cache.get("k2") == "v2", "BUG: DI-005 k2 应存在"
        assert cache.get("k3") == "v3", "BUG: DI-005 k3 应存在"

        # Add k4 → should evict k1 (oldest, since k2/k3 were just accessed via get)
        cache.set("k4", "v4")

        assert cache.get("k1") is None, \
            f"BUG: DI-005 k1 应被 LRU 淘汰, 实际仍存在: {cache.get('k1')}"
        assert cache.get("k4") == "v4", "BUG: DI-005 k4 应存在"
        # k2 and k3 were accessed (move_to_end), so they should still be present
        assert cache.get("k2") == "v2", "BUG: DI-005 k2 应存在（刚被访问）"
        assert cache.get("k3") == "v3", "BUG: DI-005 k3 应存在（刚被访问）"


class TestExceptionDataHandling:
    """DI-006 ~ DI-007: Abnormal data handling."""

    def test_di_006_empty_bar_data_no_crash(self):
        """DI-006: 空 bar_data apply_data 不崩溃（反向）。

        I14：传入空字典或 None，apply_data 返回 False，latest_tick 不变。
        """
        du, state = _make_data_updater()

        # Empty dict
        ret = du.apply_data({})
        assert ret is False, f"BUG: DI-006 空字典应返回 False, 实际 {ret}"
        assert not state.latest_tick or all(k.startswith('_') for k in state.latest_tick), \
            "BUG: DI-006 空字典不应写入非元数据 tick"

        # None
        ret = du.apply_data(None)
        assert ret is False, f"BUG: DI-006 None 应返回 False, 实际 {ret}"

    def test_di_007_nan_inf_values_no_crash(self):
        """DI-007: NaN/Inf 值不崩溃（反向）。

        I14：bar_data 含 NaN/Inf 值时，apply_data 不崩溃，值原样落入 latest_tick。
        """
        du, state = _make_data_updater()
        bar_data = {
            '600000': {'close': float('nan'), 'open': float('inf'), 'high': float('-inf'),
                        'low': 10.6, 'volume': 1000, 'amount': 11000.0},
            '000001': {'close': 16.0, 'open': float('nan'), 'high': 16.2, 'low': 15.6,
                        'volume': float('inf'), 'amount': 32000.0},
        }
        # Should not raise
        du.apply_data(bar_data)

        s1 = state.latest_tick.get('600000')
        assert s1 is not None, "BUG: DI-007 latest_tick 未找到 600000"
        # close should be NaN (injected as-is, engine doesn't sanitize)
        assert math.isnan(s1.get('close', 0)), \
            f"BUG: DI-007 close 应为 NaN, 实际 {s1.get('close')}"
