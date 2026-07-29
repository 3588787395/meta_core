"""eventtest 共享 pytest fixture。

提供 5 个 fixture，全部复用 ``core/`` 现有类，禁止兼容已删除旧接口：
  - ``virtual_clock``：虚拟时钟对象，起点 34500.0（=09:30:00），提供 ``advance(seconds)``
  - ``fz_stocks``：工厂 fixture，``fz_stocks(n=100)`` 从 ``config/pools/sim_test_pool_100.json``
    动态读取 N 只 ``fz`` 前缀股票代码
  - ``pool_engine``：工厂 fixture，``pool_engine(pool_config_path=...)`` 装配并返回
    ``PoolEngine`` 实例（复用 ``core.engine.PoolEngine``，内部完成 EventBus /
    EventDriver / EdgeExecutor / CompiledSchedule 依赖注入）
  - ``event_collector``：工厂 fixture，``event_collector(bus)`` 返回 ``EventCollector``，
    订阅传入 EventBus 的全部事件类型，提供 ``.events`` / ``.count_by_type()``
    / ``.filter(code, type)`` / ``.clear()``
  - ``pool_snapshot``：工厂 fixture，``pool_snapshot(engine)`` 返回
    ``Dict[str, List[str]]``，键为池节点 id，值为该池当前股票代码列表
    （通过 ``engine.state.get_pool(nid).get_stock_codes()`` 获取）

另提供 ``report_state`` fixture，暴露共享报告状态字典，供合测试（Task 9-10）
填充事件计数表与池状态快照表，``run_eventtest.py`` 在测试运行后读取以生成量化报告。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pytest


# ---------------------------------------------------------------------------
# 项目根目录定位（meta_core/），使配置路径与 CWD 无关
# ---------------------------------------------------------------------------
_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent
_DEFAULT_POOL_CONFIG = _PROJECT_ROOT / "config" / "pools" / "sim_test_pool_100.json"


# ---------------------------------------------------------------------------
# 共享报告状态：run_eventtest.py 启动时初始化为空 dict，合测试通过
# ``report_state`` fixture 写入实际事件计数与池快照，运行器在测试结束后读取。
# Task 1 阶段无合测试，保持空 dict 时运行器输出占位符。
# ---------------------------------------------------------------------------
def _new_report_state() -> Dict[str, Any]:
    return {"event_counts": {}, "pool_snapshot": {}}


# 模块级单例：pytest.main() 在同进程内运行，状态跨 fixture 持久。
REPORT_STATE: Dict[str, Any] = _new_report_state()


# ---------------------------------------------------------------------------
# VirtualClock —— 仿真模式虚拟时钟（起点 34500.0 = 09:30:00）
# ---------------------------------------------------------------------------


class VirtualClock:
    """可推进的虚拟时钟对象。

    仿真模式虚拟时钟起点 ``34500.0``（=当日 09:30:00 的秒数偏移），
    与 ``core.runtime_mode_module.RuntimeSimulator.clock`` 语义一致。
    ``advance(seconds)`` 推进时钟并返回新时刻。
    """

    def __init__(self, start: float = 34500.0) -> None:
        self.start: float = float(start)
        self.now: float = float(start)

    def advance(self, seconds: float) -> float:
        """推进虚拟时钟 ``seconds`` 秒，返回推进后的时刻。"""
        self.now += float(seconds)
        return self.now

    def reset(self) -> float:
        """重置到起点。"""
        self.now = self.start
        return self.now

    def __float__(self) -> float:
        return self.now


# ---------------------------------------------------------------------------
# EventCollector —— 订阅 EventBus 全部事件并收集
# ---------------------------------------------------------------------------


class EventCollector:
    """订阅 EventBus 全部事件类型并收集。

    通过 ``EventBus.subscribe_any`` 注册通配订阅者，收集所有已发布事件。
    提供 ``.events``（按时间戳排序）/ ``.count_by_type()`` / ``.filter()`` /
    ``.clear()`` 接口，供正反合测试断言事件链顺序与计数。
    """

    def __init__(self, bus: Any) -> None:
        self._bus = bus
        self._events: List[Any] = []
        self._seq: int = 0
        # subscribe_any 返回取消订阅函数，保留以便 disconnect
        self._unsubscribe: Optional[Callable[[], None]] = bus.subscribe_any(self._on_event)

    def _on_event(self, event: Any) -> None:
        # 附带插入序号用于稳定排序（同 ts 时保持发布顺序）
        self._events.append(event)

    @staticmethod
    def _event_ts(event: Any) -> float:
        ts = getattr(event, "ts", None)
        if isinstance(ts, (int, float)):
            return float(ts)
        return 0.0

    @property
    def events(self) -> List[Any]:
        """按时间戳排序的事件列表（同 ts 按发布顺序稳定排序）。"""
        return sorted(self._events, key=lambda e: (self._event_ts(e),))

    def count_by_type(self) -> Dict[str, int]:
        """按事件类型名分组计数，返回 ``Dict[str, int]``。"""
        counts: Dict[str, int] = {}
        for ev in self._events:
            name = type(ev).__name__
            counts[name] = counts.get(name, 0) + 1
        return counts

    def filter(
        self,
        code: Optional[str] = None,
        type: Optional[str] = None,
    ) -> List[Any]:
        """过滤事件。

        Args:
            code: 仅保留携带该股票代码的事件（事件有 ``code`` 属性且相等）；
                  None 表示不过滤代码。
            type: 事件类型名（如 ``"TickReceived"``）；None 表示不过滤类型。

        Returns:
            过滤后的事件列表（按时间戳排序）。
        """
        result: List[Any] = []
        for ev in self._events:
            # 用 __class__.__name__ 避免 ``type`` 参数遮蔽内置 type()
            if type is not None and ev.__class__.__name__ != type:
                continue
            if code is not None:
                ev_code = getattr(ev, "code", None)
                if ev_code != code:
                    continue
            result.append(ev)
        return sorted(result, key=lambda e: (self._event_ts(e),))

    def clear(self) -> None:
        """清空已收集事件。"""
        self._events.clear()

    def disconnect(self) -> None:
        """取消 EventBus 订阅（清理资源）。"""
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def virtual_clock() -> VirtualClock:
    """虚拟时钟对象，起点 ``34500.0``（=09:30:00），提供 ``advance(seconds)`` 推进方法。"""
    return VirtualClock(start=34500.0)


@pytest.fixture
def fz_stocks() -> Callable[..., List[str]]:
    """工厂 fixture：``fz_stocks(n=100)`` 从 ``config/pools/sim_test_pool_100.json``
    动态读取 N 只 ``fz`` 前缀股票代码。

    股票代码从配置文件的 ``market_source`` 节点 ``params.stocks`` 读取，不硬编码。
    """

    def _fz_stocks(n: int = 100) -> List[str]:
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

    return _fz_stocks


@pytest.fixture
def pool_engine() -> Callable[..., Any]:
    """工厂 fixture：``pool_engine(pool_config_path=...)`` 装配并返回 ``PoolEngine`` 实例。

    复用 ``core.engine.PoolEngine``，通过 ``pool_config=cfg`` 构造触发
    ``_init_pool_runtime``，内部完成 EventBus / EventDriver / EdgeExecutor /
    CompiledSchedule / FormulaEngine / DataUpdater / BarComposer / TradeModule
    等组件的依赖注入。

    G2：额外装配 ``TickBarModule``，使其订阅 ``TickDue`` 事件并生成
    ``TickReceived`` / ``DataChanged``，完成事件驱动的 tick 链路。
    返回的实例可直接用于事件驱动测试。
    """

    def _pool_engine(
        pool_config_path: str = "config/pools/sim_test_pool_100.json",
    ) -> Any:
        cfg_path = Path(pool_config_path)
        if not cfg_path.is_absolute():
            cfg_path = _PROJECT_ROOT / cfg_path
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
        from core.engine import PoolEngine
        from core.tick_bar_module import TickBarModule

        engine = PoolEngine(pool_config=cfg)
        bus = engine._components["event_bus"]
        tick_bar = TickBarModule(bus=bus)
        engine._components["tick_bar_module"] = tick_bar
        return engine

    return _pool_engine


@pytest.fixture
def event_collector() -> Callable[..., EventCollector]:
    """工厂 fixture：``event_collector(bus)`` 返回 ``EventCollector`` 对象。

    订阅传入 EventBus 的全部事件类型（经 ``subscribe_any``），提供
    ``.events`` / ``.count_by_type()`` / ``.filter(code, type)`` / ``.clear()``。
    测试结束时应调用 ``.disconnect()`` 取消订阅以避免跨用例泄漏。
    """

    def _event_collector(bus: Any) -> EventCollector:
        return EventCollector(bus)

    return _event_collector


@pytest.fixture
def pool_snapshot() -> Callable[..., Dict[str, List[str]]]:
    """工厂 fixture：``pool_snapshot(engine)`` 返回 ``Dict[str, List[str]]``。

    键为池节点 id，值为该池当前股票代码列表（排序后）。通过
    ``engine.state.get_pool(nid).get_stock_codes()`` 获取（StatePoolView 视图接口）。
    """

    def _pool_snapshot(engine: Any) -> Dict[str, List[str]]:
        result: Dict[str, List[str]] = {}
        nodes = getattr(engine, "nodes", None) or {}
        for nid in nodes:
            pool = engine.state.get_pool(nid)
            result[nid] = sorted(pool.get_stock_codes())
        return result

    return _pool_snapshot


@pytest.fixture(scope="session")
def report_state() -> Dict[str, Any]:
    """暴露共享报告状态字典，供合测试（Task 9-10）填充事件计数表与池状态快照表。

    返回模块级 ``REPORT_STATE`` 单例，合测试在仿真运行后写入：
      - ``event_counts``: ``Dict[str, int]`` 按 EventType 分组计数
      - ``pool_snapshot``: ``Dict[str, List[str]]`` 池状态快照
    ``run_eventtest.py`` 在测试运行结束后读取以生成量化报告。

    session-scoped：使 module-scoped 合测试 fixture（如 sim_engine）能引用此 fixture
    （function-scoped fixture 不能被 module-scoped fixture 引用，会触发 ScopeMismatch）。
    ``REPORT_STATE`` 本身就是模块级单例，session scope 不改变其语义。
    """
    return REPORT_STATE
