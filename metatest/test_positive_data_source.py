"""Task：数据源提供者正测试。

验证 ``services/providers.py`` 中的核心抽象：
  - ``MockProvider`` 确定性（同种子同数据，使用 MD5 种子）
  - ``MockDataSource`` 输出 ``fz`` 前缀股票代码（``core.domain``）
  - ``HQChartProvider`` 继承 ``DataSourceProvider`` 协议
  - ``DataSourceManager`` 注册与获取提供者
  - ``_get_full_mock_provider`` 工厂函数
  - ``normalize_code`` / ``to_dzh_code`` / ``map_period`` 工具函数

使用 ``fz_stocks`` fixture 提供 fz 前缀股票代码。
"""
from __future__ import annotations

from typing import Any, List

import pytest


# ============================================================================
# SubTask：MockProvider 确定性
# ============================================================================


class TestMockProviderDeterminism:
    """MockProvider 使用 MD5 种子，相同输入产生相同输出。"""

    def test_kline_data_is_deterministic(self):
        """``get_kline_data`` 同参两次调用返回相同 K 线序列。"""
        from services.providers import MockProvider

        p1 = MockProvider()
        p2 = MockProvider()
        # 授权后才能 ready（保证 _probe 与 is_ready 行为分离）
        p1.grant_consent()
        p2.grant_consent()

        k1 = p1.get_kline_data(["fz000001"], period="1d", start_date=None, end_date=None)
        k2 = p2.get_kline_data(["fz000001"], period="1d", start_date=None, end_date=None)
        assert k1 == k2, "相同参数应产生相同 K 线数据（MD5 种子确定）"

    def test_snapshot_is_deterministic(self):
        """``get_snapshot`` 同参两次调用返回相同快照。"""
        from services.providers import MockProvider

        p1 = MockProvider()
        p2 = MockProvider()
        s1 = p1.get_snapshot(["fz000001"])
        s2 = p2.get_snapshot(["fz000001"])
        assert s1 == s2, "相同参数应产生相同快照"
        # 数据字段断言
        assert "fz000001" in s1, "快照应包含请求的股票代码"
        assert "close" in s1["fz000001"], "快照应包含 close 字段"

    def test_eval_indicator_is_deterministic(self):
        """``eval_indicator`` 同参两次调用返回相同指标结果。"""
        from services.providers import MockProvider

        p1 = MockProvider()
        p2 = MockProvider()
        r1 = p1.eval_indicator(["fz000001"], "MA5", "1d")
        r2 = p2.eval_indicator(["fz000001"], "MA5", "1d")
        assert r1 == r2, "相同参数应产生相同指标结果"

    def test_mock_provider_explicit_consent_required(self):
        """MockProvider 是 explicit_only，未授权时 is_ready 返回 False。"""
        from services.providers import MockProvider

        p = MockProvider()
        assert p.is_ready() is False, "未授权时 is_ready 应为 False"
        p.grant_consent()
        assert p.is_ready() is True, "授权后 is_ready 应为 True"
        p.revoke_consent()
        assert p.is_ready() is False, "撤销授权后 is_ready 应为 False"


# ============================================================================
# SubTask：MockDataSource 输出 fz 前缀股票代码
# ============================================================================


class TestMockDataSourceFzCodes:
    """``core.domain.MockDataSource`` 将所有代码统一为 fz 前缀。"""

    def test_create_tick_source_yields_fz_codes(self, fz_stocks):
        """``MockProvider.create_tick_source`` 返回 MockDataSource，codes 全部 fz 前缀。"""
        from services.providers import MockProvider

        p = MockProvider()
        codes = fz_stocks(5)
        ds = p.create_tick_source(codes, clock_start=34500.0)
        # 内部 _codes 经 _normalize_to_fz 处理后全部以 fz 开头
        internal_codes = list(ds._codes)
        assert len(internal_codes) == 5, f"应保留 5 只股票，实际 {len(internal_codes)}"
        assert all(c.startswith("fz") for c in internal_codes), (
            f"内部代码应全部 fz 前缀: {internal_codes}"
        )

    def test_normalize_to_fz_helper(self):
        """``_normalize_to_fz`` 工具将多种格式归一化为 fz 前缀。"""
        from core.domain import _normalize_to_fz

        assert _normalize_to_fz("600000.SH") == "fz600000"
        assert _normalize_to_fz("SZ000001") == "fz000001"
        assert _normalize_to_fz("fz000001") == "fz000001"
        assert _normalize_to_fz("BJ830001") == "fz830001"


# ============================================================================
# SubTask：HQChartProvider 协议
# ============================================================================


class TestHQChartProviderProtocol:
    """``HQChartProvider`` 继承 ``DataSourceProvider``，遵循协议。"""

    def test_hqchart_is_subclass_of_provider(self):
        """``HQChartProvider`` 是 ``DataSourceProvider`` 子类。"""
        from services.providers import DataSourceProvider, HQChartProvider

        assert issubclass(HQChartProvider, DataSourceProvider), (
            "HQChartProvider 必须继承 DataSourceProvider"
        )

    def test_hqchart_has_required_methods(self):
        """``HQChartProvider`` 实现核心数据源方法（is_ready / get_mode_info）。"""
        from services.providers import HQChartProvider

        # 类方法存在性（实例化可能因 C++ 引擎不可用而失败，仅校验方法定义）
        for name in ("is_ready", "get_mode_info", "check_health", "_probe"):
            assert hasattr(HQChartProvider, name), f"HQChartProvider 缺少方法 {name}"


# ============================================================================
# SubTask：DataSourceManager 注册与获取
# ============================================================================


class TestDataSourceManager:
    """``DataSourceManager`` 注册 / 获取 / 切换活跃数据源。"""

    def test_manager_auto_registers_mock(self):
        """DataSourceManager 初始化时自动注册 mock 提供者。"""
        from services.providers import DataSourceManager, DataSourceProvider

        mgr = DataSourceManager()
        mock = mgr.get_provider("mock")
        assert mock is not None, "manager 应自动注册 mock 提供者"
        assert isinstance(mock, DataSourceProvider), "mock 必须是 DataSourceProvider 实例"

    def test_manager_get_provider_returns_none_for_unknown(self):
        """``get_provider`` 对未知名称返回 None。"""
        from services.providers import DataSourceManager

        mgr = DataSourceManager()
        assert mgr.get_provider("non_existent_provider_xyz") is None, (
            "未知提供者应返回 None"
        )

    def test_manager_default_chain_from_contract(self):
        """``default_chain`` 来自 ``data_source_contract.json``（``["tq_dll"]``）。"""
        from services.providers import DataSourceManager

        mgr = DataSourceManager()
        # data_source_contract.json 中 default_chain = ["tq_dll"]
        assert "tq_dll" in mgr.default_chain, (
            f"default_chain 应来自 data_source_contract.json，实际: {mgr.default_chain}"
        )
        assert mgr.default_chain[0] == "tq_dll", (
            f"default_chain 首项应为 tq_dll，实际: {mgr.default_chain[0]}"
        )

    def test_manager_active_provider_is_none_when_tq_dll_not_loaded(self):
        """``active_provider`` 在 tq_dll 未加载时返回 None（C++ 引擎不可用）。"""
        from services.providers import DataSourceManager

        mgr = DataSourceManager()
        # active_source 是 default_chain 首项 "tq_dll"，但 tq_dll 未注册到 _providers
        # （TqDllProvider 实例化需要 TPythClient.dll，测试环境不可用）
        assert mgr.active_provider is None, (
            "tq_dll 未加载时 active_provider 应为 None"
        )

    def test_manager_set_active_source_unknown_raises(self):
        """``set_active_source`` 对未知名称抛 DataSourceUnavailableError。"""
        from services.providers import DataSourceManager, DataSourceUnavailableError

        mgr = DataSourceManager()
        with pytest.raises(DataSourceUnavailableError):
            mgr.set_active_source("definitely_not_registered")


# ============================================================================
# SubTask：_get_full_mock_provider 工厂
# ============================================================================


class TestGetFullMockProvider:
    """``_get_full_mock_provider`` 返回可用 MockProvider。"""

    def test_returns_data_source_provider(self):
        """返回值是 ``DataSourceProvider`` 子类实例。"""
        from services.providers import DataSourceProvider, _get_full_mock_provider

        p = _get_full_mock_provider()
        assert isinstance(p, DataSourceProvider), (
            "_get_full_mock_provider 应返回 DataSourceProvider 实例"
        )

    def test_returns_mock_mode(self):
        """返回的提供者 mode_info 为 'mock'。"""
        from services.providers import _get_full_mock_provider

        p = _get_full_mock_provider()
        assert p.get_mode_info() == "mock", f"mode_info 应为 mock，实际 {p.get_mode_info()!r}"


# ============================================================================
# SubTask：工具函数 normalize_code / to_dzh_code / map_period
# ============================================================================


class TestCodeHelpers:
    """``normalize_code`` / ``to_dzh_code`` / ``map_period`` 工具函数。"""

    def test_normalize_code_cases(self):
        """``normalize_code`` 处理 SH/SZ/BJ 前缀与已带后缀的代码。"""
        from services.providers import normalize_code

        assert normalize_code("SH600000") == "600000.SH"
        assert normalize_code("sz000001") == "000001.SZ"
        assert normalize_code("BJ830001") == "830001.BJ"
        # 已带点的代码原样返回（大写）
        assert normalize_code("600000.sh") == "600000.SH"
        # 空串原样返回
        assert normalize_code("") == ""

    def test_to_dzh_code_cases(self):
        """``to_dzh_code`` 将 ``600000.SH`` 转为 ``SH600000``。"""
        from services.providers import to_dzh_code

        assert to_dzh_code("600000.SH") == "SH600000"
        assert to_dzh_code("000001.SZ") == "SZ000001"
        # 无后缀代码原样大写返回
        assert to_dzh_code("fz000001") == "FZ000001"
        # 空串原样返回
        assert to_dzh_code("") == ""

    def test_map_period_cases(self):
        """``map_period`` 将周期字符串映射为整数 ID。"""
        from services.providers import map_period

        assert map_period("1m") == 1
        assert map_period("5m") == 2
        assert map_period("15m") == 3
        assert map_period("30m") == 4
        assert map_period("60m") == 5
        assert map_period("1d") == 6
        assert map_period("日") == 6, "中文'日'应映射为 6"
        assert map_period("1w") == 7
        assert map_period("月") == 8
        # 未知周期回退到 6（日）
        assert map_period("unknown_cycle") == 6


# ============================================================================
# SubTask：MockDataSource tick 间隔确定性
# ============================================================================


class TestTickIntervalDeterminism:
    """MockDataSource 每只股票的 tick 间隔固定（基于 code hash 种子）。"""

    def test_same_stock_has_fixed_interval(self, fz_stocks):
        """同一只股票多次实例化 MockDataSource，间隔值不变。"""
        from core.domain import MockDataSource

        codes = fz_stocks(3)
        ds1 = MockDataSource(codes=codes, clock_start=34500.0)
        ds2 = MockDataSource(codes=codes, clock_start=34500.0)
        for c in ds1._codes:
            assert ds1._intervals[c] == ds2._intervals[c], (
                f"股票 {c} 的间隔应跨实例固定: {ds1._intervals[c]} vs {ds2._intervals[c]}"
            )

    def test_interval_within_one_to_nine(self, fz_stocks):
        """每只股票的间隔值在 [1, 9] 范围内。"""
        from core.domain import MockDataSource

        codes = fz_stocks(20)
        ds = MockDataSource(codes=codes, clock_start=34500.0)
        for c, iv in ds._intervals.items():
            assert 1 <= iv <= 9, f"股票 {c} 间隔 {iv} 不在 [1, 9] 范围"

    def test_different_stocks_have_different_intervals(self, fz_stocks):
        """不同股票间隔值不同（取足够多样本使至少出现两种间隔）。"""
        from core.domain import MockDataSource

        codes = fz_stocks(100)
        ds = MockDataSource(codes=codes, clock_start=34500.0)
        intervals = set(ds._intervals.values())
        assert len(intervals) >= 2, (
            f"100 只股票应至少出现 2 种不同间隔，实际 {len(intervals)} 种"
        )


# === Task 28.6 回归断言：converge-meta-essence-v4 阶段 2 E3 + E5 收敛状态 ===


class TestConvergenceRegressionV4:
    """SubTask 28.6：converge-meta-essence-v4 E3 watchdog + E5 services/data heapq 收敛回归。"""

    def test_no_file_watcher_loop_in_services_data(self):
        """services/data.py 不含 _file_watcher_loop（E3 已改 watchdog 事件驱动）。"""
        import re
        from pathlib import Path
        data_path = Path(__file__).resolve().parent.parent / "services" / "data.py"
        if not data_path.is_file():
            return
        src = data_path.read_text(encoding="utf-8")
        count = len(re.findall(r"def _file_watcher_loop\b", src))
        assert count == 0, \
            f"services/data.py 不应含 _file_watcher_loop（E3 已改 watchdog），实际 {count} 处"

    def test_no_refresh_with_backoff_in_services_data(self):
        """services/data.py 不含 _refresh_with_backoff（E5 已改 heapq 调度）。"""
        import re
        from pathlib import Path
        data_path = Path(__file__).resolve().parent.parent / "services" / "data.py"
        if not data_path.is_file():
            return
        src = data_path.read_text(encoding="utf-8")
        count = len(re.findall(r"def _refresh_with_backoff\b", src))
        assert count == 0, \
            f"services/data.py 不应含 _refresh_with_backoff（E5 已改 heapq），实际 {count} 处"

    def test_watchdog_observer_used_in_table_engine(self):
        """table_engine.py 引用 watchdog.Observer（E3 文件监视事件驱动）。"""
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "core" / "table_engine.py").read_text(encoding="utf-8")
        assert "watchdog.observers" in src or "Observer()" in src, \
            "table_engine.py 应使用 watchdog.Observer（E3 文件监视事件驱动）"
