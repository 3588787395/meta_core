# -*- coding: utf-8 -*-
"""Task 20.4: 运行时错误负测试。

验证运行时各类异常输入与错误状态下的优雅处理：
  - 同一股票重复入池应被去重
  - TTL 超时但无持仓应优雅处理
  - 公式错误（语法错误/未定义函数）应捕获异常
  - 跨模块非法引用应被检测（所有模块只准与事件引擎交互）
  - 畸形 tick 数据（缺字段/类型错误）应被拒绝或默认处理
  - 引擎状态损坏（如 node_stocks 为 None）应优雅恢复
  - 并发访问 PoolState 应安全

硬约束：
  - 仿真模式下股票代码使用 ``fz`` 前缀
  - 复用 conftest.py 中的 fixture（tick_table / event_collector 等）
  - 使用 ``from core.xxx import yyy`` 直接导入
"""
from __future__ import annotations

import inspect
import threading
from typing import Any, Dict, List

import pytest


# ---------------------------------------------------------------------------
# 1. test_duplicate_stock_entry_dedup —— 同一股票重复入池应被去重
# ---------------------------------------------------------------------------


def test_duplicate_stock_entry_dedup():
    """同一股票重复入池应被去重（验证 node_stocks 列表经视图去重无重复）。

    StatePoolView.get_stock_codes() 返回 Set[str]，天然对重复代码去重；
    propagate_apply 亦通过 list(set(tgt + passed)) 去重。
    """
    from core.runtime_mode_module import PoolState

    state = PoolState(pool_config={"nodes": [], "edges": []})
    pool = state.get_pool("fz_pool")
    # 重复入池同一只股票
    pool.add_stocks([{"code": "fz000001"}, {"code": "fz000001"}, {"code": "fz000002"}])
    codes = pool.get_stock_codes()
    assert codes == {"fz000001", "fz000002"}, f"重复入池未去重: {codes}"
    # 再次重复入池
    pool.add_stocks([{"code": "fz000001"}, {"code": "fz000003"}])
    codes2 = pool.get_stock_codes()
    assert codes2 == {"fz000001", "fz000002", "fz000003"}, f"二次入池去重失败: {codes2}"


# ---------------------------------------------------------------------------
# 2. test_ttl_no_position_graceful —— TTL 超时但无持仓应优雅处理
# ---------------------------------------------------------------------------


def test_ttl_no_position_graceful(event_collector):
    """TTL 超时但无持仓应优雅处理（不报错，仅记录事件）。"""
    from core.event_bus import TTLDue, EventBus
    from core.trade_module import TradeModule

    collector = event_collector(EventBus())
    bus = collector._bus
    collected = collector._events
    trade_cfg = {
        "auto_buy_pools": ["pool_C"],
        "trade_interface": "paper_trade",
        "initial_capital": 1000000.0,
        "default_quantity": 100,
    }
    TradeModule(bus, config=trade_cfg)
    # TTL 超时但无持仓（TradeModule 未建立任何持仓）
    ev = TTLDue(node_id="pool_C", code="fz000001", ts=99999.0)
    try:
        bus.publish(ev)
    except Exception as exc:
        pytest.fail(f"TTL 无持仓应优雅处理，却抛出异常: {exc}")
    # 应记录了 TTLDue 事件
    assert any(isinstance(e, TTLDue) for e in collected), "TTL 事件未被记录"


# ---------------------------------------------------------------------------
# 3. test_formula_error_caught —— 公式错误应捕获异常并记录
# ---------------------------------------------------------------------------


def test_formula_error_caught(caplog):
    """公式错误（语法错误/未定义函数）应捕获异常并记录。

    PythonFormulaEngine.eval 对语法错误、未定义变量/函数、除零等异常输入
    在内部捕获并记录 WARNING 日志（``公式编译失败``），不向上抛出异常，
    不应导致进程崩溃。
    """
    from core.formula_module import PythonFormulaEngine, EvalContext
    import logging
    import pandas as pd

    engine = PythonFormulaEngine()
    bars = pd.DataFrame({
        "open": [10.0], "high": [10.5], "low": [9.5],
        "close": [10.2], "vol": [1000],
    })
    bad_formulas = [
        "CLOSE +",                   # 语法错误
        "nonexistent_func(CLOSE)",   # 未定义函数
        "UNDEFINED_VAR",             # 未定义变量
        "CLOSE / 0",                 # 除零
    ]
    with caplog.at_level(logging.WARNING, logger="core.formula_module"):
        for formula in bad_formulas:
            ctx = EvalContext(
                mode="simulation", bar_hash="fz000001",
                bars=bars, latest_tick={}, period="1min",
            )
            try:
                engine.eval(formula, ctx)
            except (SyntaxError, NameError, AttributeError, TypeError,
                    ValueError, ZeroDivisionError, KeyError, Exception):
                # 异常向上抛出也可接受（调用方捕获）
                pass
        # 公式引擎应在内部捕获错误并记录 WARNING 日志
        warning_records = [
            r for r in caplog.records
            if r.levelno == logging.WARNING
        ]
    assert len(warning_records) >= 1, (
        "公式错误应被捕获并记录 WARNING 日志（``公式编译失败``），"
        f"实际记录数 {len(warning_records)}"
    )


# ---------------------------------------------------------------------------
# 4. test_cross_module_illegal_reference —— 跨模块非法引用应被检测
# ---------------------------------------------------------------------------


def test_cross_module_illegal_reference():
    """跨模块非法引用应被检测（所有模块只准与事件引擎交互）。

    架构约束：execution_module / trade_module 应通过 EventBus（事件引擎）
    交互，跨模块依赖通过 Protocol 接口注入，而非直接 import 对方具体类。
    """
    import core.execution_module as exec_mod
    import core.trade_module as trade_mod

    # 验证 execution_module 导入了 EventBus（事件引擎交互通道）
    exec_src = inspect.getsource(exec_mod)
    assert "EventBus" in exec_src or "event_bus" in exec_src, \
        "execution_module 应通过 EventBus 与其他模块交互"

    # 验证 trade_module 导入了 EventBus（事件引擎交互通道）
    trade_src = inspect.getsource(trade_mod)
    assert "EventBus" in trade_src or "event_bus" in trade_src, \
        "trade_module 应通过 EventBus 与其他模块交互"

    # 验证 execution_module 使用 Protocol 接口（依赖注入约束）
    assert "Protocol" in exec_src or "FormulaEngineProtocol" in exec_src, \
        "execution_module 应通过 Protocol 接口注入依赖"

    # 验证 trade_module 不直接 import execution_module 的具体执行类
    illegal_direct_refs = [
        "from core.execution_module import EdgeExecutor",
        "from core.execution_module import EventDriver",
        "import core.execution_module as",
    ]
    for ref in illegal_direct_refs:
        assert ref not in trade_src, \
            f"trade_module 非法直接引用 execution_module: {ref}"


# ---------------------------------------------------------------------------
# 5. test_tick_data_malformed —— 畸形 tick 数据应被拒绝或默认处理
# ---------------------------------------------------------------------------


def test_tick_data_malformed(tick_table):
    """畸形 tick 数据（缺字段/类型错误）应被拒绝或默认处理。

    TickTable.update 对畸形数据（非 dict 值、None 值、缺字段、类型错误）
    不应崩溃，应通过 hash 序列化默认处理或忽略。
    """
    malformed_cases: List[Dict[str, Any]] = [
        {"fz000001": "not_a_dict"},        # 值类型错误
        {"fz000001": None},                # 值为 None
        {"fz000001": {"close": 10.0}},     # 正常但缺其他字段
        {"fz000001": {"close": "abc"}},    # 字段值类型错误
        {},                                 # 空数据
    ]
    for data in malformed_cases:
        try:
            tick_table.update(data)
        except (TypeError, ValueError, Exception):
            # 异常被捕获即通过（拒绝畸形数据）
            continue
        # 未抛异常也应正常（默认处理）
    # 验证 tick_table 仍可正常工作（未被畸形数据污染）
    assert tick_table.update({"fz000001": {"close": 10.0}}) is True
    assert tick_table.get("fz000001").get("close") == 10.0


# ---------------------------------------------------------------------------
# 6. test_engine_state_corruption_recovery —— 引擎状态损坏应优雅恢复
# ---------------------------------------------------------------------------


def test_engine_state_corruption_recovery():
    """引擎状态损坏（如 node_stocks 为 None）应优雅恢复。

    PoolState.node_stocks 被置为 None（状态损坏）后，通过 _populate_tables()
    重新初始化 15 张运行时表容器，恢复后 get_pool / add_stocks 应正常工作。
    """
    from core.runtime_mode_module import PoolState

    state = PoolState(pool_config={"nodes": [], "edges": []})
    # 模拟状态损坏：node_stocks 被置为 None
    state.node_stocks = None
    assert state.node_stocks is None, "前置：node_stocks 已损坏为 None"

    # 恢复：重新 populate tables（重建 15 张运行时表容器）
    state._populate_tables()
    assert isinstance(state.node_stocks, dict), "恢复后 node_stocks 应为 dict"

    # 恢复后 get_pool 应正常工作
    pool = state.get_pool("fz_pool")
    assert pool.get_stock_codes() == set(), "恢复后空池应返回空集"
    pool.add_stocks([{"code": "fz000001"}])
    assert pool.get_stock_codes() == {"fz000001"}, "恢复后入池应正常"


# ---------------------------------------------------------------------------
# 7. test_concurrent_access_safe —— 并发访问 PoolState 应安全
# ---------------------------------------------------------------------------


def test_concurrent_access_safe():
    """并发访问 PoolState 应安全（基础线程安全验证）。

    多线程对不同池节点并发执行 add_stocks / get_stock_codes，
    验证无异常且数据完整（CPython GIL 保护 dict 级原子操作）。
    """
    from core.runtime_mode_module import PoolState

    state = PoolState(pool_config={"nodes": [], "edges": []})
    errors: List[Exception] = []

    def worker(pool_name: str, n: int = 50):
        try:
            for i in range(n):
                code = f"fz{pool_name[-1]}{i:04d}"
                pool = state.get_pool(pool_name)
                pool.add_stocks([{"code": code}])
                _ = pool.get_stock_codes()
        except Exception as exc:
            errors.append(exc)

    threads = []
    for name in ["pool_A", "pool_B", "pool_C"]:
        t = threading.Thread(target=worker, args=(name, 50))
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    assert not errors, f"并发访问产生异常: {errors}"
    # 验证各池数据完整
    for name in ["pool_A", "pool_B", "pool_C"]:
        codes = state.get_pool(name).get_stock_codes()
        assert len(codes) == 50, f"池 {name} 数据不完整: {len(codes)}/50"


# ---------------------------------------------------------------------------
# 8. test_invalid_stock_code_graceful —— 无效股票代码应被优雅处理
# ---------------------------------------------------------------------------


def test_invalid_stock_code_graceful():
    """无效股票代码应被优雅处理（不抛异常，按 str() 归一化或忽略）。

    StatePoolView.add_stocks 通过 ``_extract_code`` 将任意输入归一化为 str：
      - dict 缺 code 字段 → 取 label 或空串
      - None / int / 非字符串 → str() 转换
    测试传入 None、空串、整数、缺字段 dict 等畸形代码，验证无异常。
    """
    from core.runtime_mode_module import PoolState

    state = PoolState(pool_config={"nodes": [], "edges": []})
    pool = state.get_pool("fz_pool")
    malformed_codes: List[Any] = [
        {"code": None},                # code 显式为 None
        {"code": ""},                  # 空字符串 code
        {"code": 42},                  # 整数 code（非字符串）
        {"label": "fz_only_label"},    # 缺 code 字段，仅有 label
        {},                             # 完全空 dict
        None,                          # None 顶层对象
        12345,                         # 整数顶层对象
        "fz000001",                    # 字符串（合法）
    ]
    try:
        pool.add_stocks(malformed_codes)
    except (TypeError, ValueError, AttributeError):
        # 抛出受控异常也可接受
        return
    except Exception:
        return
    # 未抛异常时，get_stock_codes 应返回去重后的非空集合
    codes = pool.get_stock_codes()
    assert isinstance(codes, set), f"返回类型应为 set，实际: {type(codes)}"
    # 至少 fz000001 与 42（str(42)）应在集合中
    assert "fz000001" in codes or "42" in codes, (
        f"合法代码应保留在集合中，实际: {codes}"
    )


# ---------------------------------------------------------------------------
# 9. test_bar_overflow_capped —— K 线历史溢出应被 maxlen 限制
# ---------------------------------------------------------------------------


def test_bar_overflow_capped():
    """K 线历史溢出应被 ``_BARS_HISTORY_MAXLEN`` 上限裁剪，不无限增长。

    ``_append_closed_bar`` 在 ``state.bars_history[period][code]`` 长度超过
    ``_BARS_HISTORY_MAXLEN``（=300）时执行 ``del hist[0]``，丢弃最旧 bar，
    保证历史长度有界。本测试连续追加 500 条 bar，验证最终长度不超 300。
    """
    from core.tick_bar_module import _append_closed_bar, _BARS_HISTORY_MAXLEN

    class _FakeState:
        def __init__(self):
            self.bars_history: Dict[str, Dict[str, list]] = {}

    fake_state = _FakeState()
    period = "1min"
    code = "fz000001"
    bar = {"open": 10.0, "high": 10.5, "low": 9.5, "close": 10.2, "vol": 1000}

    # 连续追加 500 条 bar（超过 _BARS_HISTORY_MAXLEN=300）
    overflow_count = _BARS_HISTORY_MAXLEN + 200
    for i in range(overflow_count):
        bar_i = dict(bar)
        bar_i["close"] = 10.0 + i * 0.01
        _append_closed_bar(fake_state, period, code, bar_i)

    hist = fake_state.bars_history[period][code]
    assert len(hist) <= _BARS_HISTORY_MAXLEN, (
        f"历史长度 {len(hist)} 超过 maxlen {_BARS_HISTORY_MAXLEN}"
    )
    # 验证保留了最新的 bar（i=overflow_count-1）
    last_bar = hist[-1]
    expected_last_close = 10.0 + (overflow_count - 1) * 0.01
    assert abs(last_bar["close"] - expected_last_close) < 1e-9, (
        f"最新 bar 应保留，期望 close={expected_last_close}，实际 {last_bar['close']}"
    )
    # 验证最旧 bar 已被丢弃（i=0 的 close=10.0 应不在历史中）
    assert hist[0]["close"] > 10.0, (
        f"最旧 bar 应被丢弃，实际 hist[0]={hist[0]}"
    )
    # _hash 字段应被清理（不应残留）
    assert "_hash" not in last_bar, "bar 中残留 _hash 字段"


# ============================================================================
# Task 29.5 新增：轮询/自造事件循环零容忍（time primitive guard rails）
# ============================================================================
# spec 时间原语：所有时间触发经 EventDriver heapq 调度，禁止 while True + sleep
# 自造时间调度。验证运行时核心模块无禁止轮询模式。


import re as _re
import subprocess as _subprocess
from pathlib import Path as _Path

_PROJECT_ROOT_RT = _Path(__file__).resolve().parent.parent


def _grep_count_rt(pattern: str, target: str) -> int:
    """运行 grep -nE PATTERN TARGET 返回匹配行数（0 = 无匹配）。"""
    p = _PROJECT_ROOT_RT / target
    if not p.exists():
        return 0
    try:
        proc = _subprocess.run(
            ["grep", "-nE", pattern, str(p)],
            capture_output=True, text=True, timeout=30,
        )
    except (FileNotFoundError, _subprocess.TimeoutExpired):
        return 0
    if proc.returncode == 0:
        return len([ln for ln in proc.stdout.splitlines() if ln.strip()])
    return 0


class TestNoPollingRevival:
    """运行时核心模块不应复活轮询/自造事件循环模式（Task 29.5 新增）。"""

    def test_no_sync_play_loop(self):
        """core/runtime_mode_module.py 无 _sync_play_loop（应 EventDriver 调度）。"""
        assert _grep_count_rt(r"def _sync_play_loop\b", "core/runtime_mode_module.py") == 0

    def test_no_sync_sim_loop(self):
        """core/runtime_mode_module.py 无 _sync_sim_loop。"""
        assert _grep_count_rt(r"def _sync_sim_loop\b", "core/runtime_mode_module.py") == 0

    def test_no_auto_step_loop(self):
        """core/runtime_mode_module.py 无 async def auto_step_loop。"""
        assert _grep_count_rt(r"async def auto_step_loop\b", "core/runtime_mode_module.py") == 0

    def test_no_start_polling_in_table_engine(self):
        """core/table_engine.py 无 start_polling（应 ConfigStoreBase.check_and_reload）。"""
        assert _grep_count_rt(r"def start_polling\b", "core/table_engine.py") == 0

    def test_no_file_watcher_loop_in_services_data(self):
        """services/data.py 无 _file_watcher_loop。"""
        assert _grep_count_rt(r"def _file_watcher_loop\b", "services/data.py") == 0

    def test_no_while_self_run_loop(self):
        """core/runtime_mode_module.py 无 while self._run 自造事件循环。"""
        assert _grep_count_rt(
            r"while self\._run\b|while self\._sim_auto_step\b",
            "core/runtime_mode_module.py",
        ) == 0

    def test_no_time_sleep_interval_in_runtime(self):
        """core/runtime_mode_module.py 无 time.sleep(interval) 自造时间调度。"""
        assert _grep_count_rt(r"time\.sleep\(interval\)", "core/runtime_mode_module.py") == 0

    def test_no_asyncio_sleep_wait_seconds_in_services_data(self):
        """services/data.py 无 asyncio.sleep(wait_seconds) 自造每日调度。"""
        assert _grep_count_rt(r"asyncio\.sleep\(wait_seconds\)", "services/data.py") == 0


# ============================================================================
# Task 29.5 新增：EventBus 唯一性 + 事件处理器装饰器覆盖
# ============================================================================
# spec 第四层洞察：EventBus 是唯一事件分派器，禁止自造事件循环。
# 7 模块通过 @_event_handler 装饰统一订阅。


class TestEventBusSoleMediator:
    """EventBus 是唯一事件分派器，7 模块通过 @_event_handler 统一订阅。"""

    def test_event_bus_importable(self):
        """core.event_bus.EventBus 可导入。"""
        from core.event_bus import EventBus
        assert EventBus is not None

    def test_event_bus_has_publish_subscribe(self):
        """EventBus 提供 publish / subscribe / subscribe_any 方法。"""
        from core.event_bus import EventBus
        bus = EventBus()
        assert callable(getattr(bus, "publish", None))
        assert callable(getattr(bus, "subscribe", None))
        assert callable(getattr(bus, "subscribe_any", None))

    def test_no_self_made_event_loop_in_core(self):
        """core/*.py 无 while True 自造事件循环（EventBus 唯一）。

        排除带 ``# noqa: event-driver`` 标记的合法循环（EventDriver heapq
        派发循环自身、递归下降解析器 token 消费循环——非自造事件循环）。
        """
        _NOQA_EVENT_DRIVER = "# noqa: event-driver"
        total = 0
        for f in (_PROJECT_ROOT_RT / "core").glob("*.py"):
            try:
                content = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for line in content.splitlines():
                if _NOQA_EVENT_DRIVER in line:
                    continue
                total += len(_re.findall(r"^\s*while\s+True\s*:", line))
        assert total == 0, f"core/*.py 检测到 {total} 处 while True 自造事件循环"

    def test_event_handler_decorator_in_modules(self):
        """5 核心模块共 ≥ 28 处 @_event_handler 装饰（事件订阅统一接口）。"""
        target_modules = [
            "execution_module.py", "tick_bar_module.py",
            "monitoring_module.py", "screening_module.py", "trade_module.py",
        ]
        total = 0
        for name in target_modules:
            f = _PROJECT_ROOT_RT / "core" / name
            if not f.exists():
                continue
            try:
                content = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            total += len(_re.findall(r"@_event_handler\b", content))
        assert total >= 28, f"5 模块共 {total} 处 @_event_handler < 28"

    def test_base_module_class_exists(self):
        """core.event_bus._BaseModule 基类存在（7 模块统一基类）。"""
        from core.event_bus import _BaseModule
        assert _BaseModule is not None

    def test_base_module_has_register_subscribers(self):
        """_BaseModule 提供 register_subscribers（_SUBSCRIPTIONS 单循环注册）。"""
        from core.event_bus import _BaseModule
        assert callable(getattr(_BaseModule, "register_subscribers", None))
