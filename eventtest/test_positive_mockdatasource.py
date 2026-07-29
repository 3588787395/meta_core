"""正测试：MockDataSource 生成确定性 tick（G5）

验证：
- MD5 种子确定性
- 同股票 tick 间隔固定（同种子下两次 tick 间隔相同）
- 不同股票 tick 间隔不同
- 所有股票代码以 fz 前缀
- tick 数量 ≥ 备选池股票数 × 期望触发轮数

基于 spec.md "MockDataSource 生成确定性 tick" Scenario。
复用 ``core/`` 现有 ``MockDataSource`` 与 ``EventDriver``，禁止兼容已删除旧接口。
"""
from __future__ import annotations

from core.domain import MockDataSource, is_fz_code
from core.event_bus import EventBus, TickReceived
from core.execution_module import EventDriver

# 仿真模式虚拟时钟起点（= 09:30:00 当日秒数偏移），与 conftest.VirtualClock 一致
_CLOCK_START = 34500.0


# ────────────────────────────────────────────────────────────────
# 确定性：MD5 种子 → per-code 固定间隔
# ────────────────────────────────────────────────────────────────


def test_md5_seed_deterministic(fz_stocks):
    """同 MD5 种子（同代码集）下两次实例化的 per-code 间隔字典完全一致。

    MockDataSource._init_code 用 ``hashlib.md5(code)`` 派生确定性随机种子，
    进而生成 1~9 秒的固定间隔。同代码集 → 同种子 → 同间隔。
    """
    codes = fz_stocks(100)
    ds1 = MockDataSource(codes, clock_start=_CLOCK_START)
    ds2 = MockDataSource(codes, clock_start=_CLOCK_START)
    # 同种子下两次实例化的间隔字典完全一致
    assert ds1.intervals == ds2.intervals
    # 子集也一致：同代码在不同实例集中产生相同间隔（MD5 种子按 code 独立确定性）
    subset = codes[:10]
    ds3 = MockDataSource(subset, clock_start=_CLOCK_START)
    for code in subset:
        assert ds1.intervals[code] == ds3.intervals[code]


# ────────────────────────────────────────────────────────────────
# 同股票 tick 间隔固定
# ────────────────────────────────────────────────────────────────


def test_same_stock_interval_fixed(fz_stocks):
    """同一只股票多次查询 interval_for 返回相同值；跨实例（同种子）也相同。"""
    codes = fz_stocks(50)
    ds = MockDataSource(codes, clock_start=_CLOCK_START)
    for code in codes:
        first = ds.interval_for(code)
        second = ds.interval_for(code)
        # 同一实例内多次查询返回固定值
        assert first == second
        # 与 intervals 字典一致（间隔固定存储）
        assert first == ds.intervals[code]
    # 跨实例：同种子下同股票间隔相同
    ds_other = MockDataSource(codes, clock_start=_CLOCK_START)
    for code in codes:
        assert ds.interval_for(code) == ds_other.interval_for(code)


# ────────────────────────────────────────────────────────────────
# 不同股票 tick 间隔不同
# ────────────────────────────────────────────────────────────────


def test_different_stocks_different_interval(fz_stocks):
    """不同股票 tick 间隔不同（至少存在两只间隔不同），且间隔落在 1~9 秒。"""
    codes = fz_stocks(100)
    ds = MockDataSource(codes, clock_start=_CLOCK_START)
    intervals = ds.intervals
    values = list(intervals.values())
    # 100 只股票、间隔取值 1~9，必然存在差异
    assert len(set(values)) > 1
    # 间隔范围在 1~9 秒（spec 约束 tick 间隔 1-9s）
    for v in values:
        assert 1 <= v <= 9


# ────────────────────────────────────────────────────────────────
# 所有股票代码以 fz 前缀
# ────────────────────────────────────────────────────────────────


def test_all_codes_fz_prefix(fz_stocks):
    """所有 MockDataSource 输出代码以 ``fz`` 前缀且符合 ``fz<6位数字>`` 格式。"""
    codes = fz_stocks(100)
    ds = MockDataSource(codes, clock_start=_CLOCK_START)
    out_codes = ds.codes
    # 数量不少于输入（去重归一化后）
    assert len(out_codes) >= 1
    # 去重后数量不变（已归一化）
    assert len(out_codes) == len(set(out_codes))
    for code in out_codes:
        assert code.startswith("fz")
        assert is_fz_code(code), f"非法 fz 代码格式: {code!r}"


# ────────────────────────────────────────────────────────────────
# tick 数量 ≥ 备选池股票数 × 期望触发轮数
# ────────────────────────────────────────────────────────────────


def test_tick_count_geq_stocks_times_rounds(fz_stocks, virtual_clock, event_collector):
    """tick 定时器触发数量 ≥ 备选池股票数 × 期望触发轮数（N=3）。

    G2：MockDataSource 定时器 action 只发布 TickDue(code, ts)，不直接生成 tick 数据。
    本测试注册 tick 定时器到 EventDriver 单一 heapq，推进虚拟时钟逐秒触发 fire_due，
    统计 TickDue 事件数，断言 ≥ 备选池股票数 × 3。
    """
    codes = fz_stocks(100)
    bus = EventBus()
    collector = event_collector(bus)
    driver = EventDriver(state=None, bus=bus)
    ds = MockDataSource(codes, clock_start=virtual_clock.start)
    ds.set_event_driver(driver, bus)
    ds.register_tick_timers(now=virtual_clock.now)

    # 推进 100 秒虚拟时钟，逐秒触发 fire_due
    # 每只股票间隔 1~9s，100s 内每只至少触发 floor(100/9)=11 次 >> 3
    sim_seconds = 100
    for _ in range(sim_seconds):
        virtual_clock.advance(1)
        driver.fire_due(virtual_clock.now)

    n_ticks = collector.count_by_type().get("TickDue", 0)
    expected_rounds = 3
    assert n_ticks >= len(codes) * expected_rounds

    collector.disconnect()
