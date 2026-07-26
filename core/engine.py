"""统一核心引擎：PoolEngine（Task 24 合并 MetaEngine + PoolEngine）。

按 ``unify-stockpool-oop-event-driven`` 规格 Task 24 实现：
- ``PoolEngine`` 持有编译期 ``CompiledSchedule``、``PoolState``、``EdgeExecutor``，
  执行事件驱动的 tick 循环。
- 原 ``MetaEngine`` 的配置加载与运行时辅助方法已合并入此类，消除双重引擎结构。
- ``CompiledExpression`` 已从 ``_compat.py`` 迁移至本模块顶部（SubTask 27.1）。
"""
from __future__ import annotations

import json
import logging
import asyncio
import copy
import ast
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime as _dt, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

try:
    from ..native import builtins as _builtins
except ImportError:
    try:
        from native import builtins as _builtins
    except ImportError:
        import builtins as _builtins

try:
    from ..native import builtins as _builtins_post_tick
except ImportError:
    try:
        from native import builtins as _builtins_post_tick
    except ImportError:
        import builtins as _builtins_post_tick

try:
    from ..native import builtins as _pipeline
except ImportError:
    try:
        from native import builtins as _pipeline
    except ImportError:
        import builtins as _pipeline

try:
    from ..native.validators import TopologyPatternMatcher
except ImportError:
    try:
        from native.validators import TopologyPatternMatcher
    except ImportError:
        TopologyPatternMatcher = None

try:
    from . import screening_module as tdx_evaluators
except ImportError:
    try:
        from ..core import screening_module as tdx_evaluators
    except ImportError:
        import screening_module as tdx_evaluators

try:
    from .domain import _hms_to_seconds
except ImportError:
    try:
        from ..core.domain import _hms_to_seconds
    except ImportError:
        from domain import _hms_to_seconds

try:
    # PoolState 已合并到 runtime_mode_module.py（SubTask 29.6）。
    # 此处不在顶部 import PoolState，因 runtime_mode_module 顶部 import
    # ``from core.engine import PoolEngine``，顶部互相 import 会触发循环依赖。
    # 改为在 __init__ 内使用处懒加载（与本方法已有的 trade_module /
    # monitoring_module 懒加载模式一致）。
    from ..core.execution_module import (
        Compiler, CompiledSchedule,
        _extract_edge_endpoint as _ce_extract_edge_endpoint,
        _resolve_node_type as _ce_resolve_node_type,
        _resolve_edge_type as _ce_resolve_edge_type,
        _normalize_nodes as _ce_normalize_nodes,
        build_timed_event_specs,
    )
    from ..core.formula_module import FormulaEngine
    from ..core.execution_module import EdgeExecutor, _lookup_edge_cond
    from ..core.event_bus import (
        EVENT_DOMAIN, EVENT_EXECUTED, EVENT_SIGNAL,
        EventBus, DataChanged, DomainEvent, Executed, Signal, TimeAdvanced, TickReceived,
    )
    from ..core.tick_bar_module import DataUpdater, BarComposer
    from ..core.execution_module import TTLHelper
    from ..core.execution_module import EventDriver
    from ..core.formula_module import ValueExtractor
    from ..core.domain import (
        TickSource, RealTickSource, MockDataSource,
        _stock_code, _normalize_stock_code, _MARKET_PREFIXES, _MARKET_SUFFIXES,
        time_at, _safe_timestamp, is_offset_of_day, anchor_to_today,
    )
except ImportError:
    try:
        from .execution_module import (
            Compiler, CompiledSchedule,
            _extract_edge_endpoint as _ce_extract_edge_endpoint,
            _resolve_node_type as _ce_resolve_node_type,
            _resolve_edge_type as _ce_resolve_edge_type,
            _normalize_nodes as _ce_normalize_nodes,
            build_timed_event_specs,
        )
        from .formula_module import FormulaEngine
        from .execution_module import EdgeExecutor, _lookup_edge_cond
        from .event_bus import (
            EVENT_DOMAIN, EVENT_EXECUTED, EVENT_SIGNAL,
            EventBus, DataChanged, DomainEvent, Executed, Signal, TimeAdvanced, TickReceived,
        )
        from .tick_bar_module import DataUpdater, BarComposer
        from .execution_module import TTLHelper
        from .execution_module import EventDriver
        from .formula_module import ValueExtractor
        from .domain import (
            TickSource, RealTickSource, MockDataSource,
            _stock_code, _normalize_stock_code, _MARKET_PREFIXES, _MARKET_SUFFIXES,
            time_at, _safe_timestamp, is_offset_of_day, anchor_to_today,
        )
    except ImportError:
        from execution_module import (
            Compiler, CompiledSchedule,
            _extract_edge_endpoint as _ce_extract_edge_endpoint,
            _resolve_node_type as _ce_resolve_node_type,
            _resolve_edge_type as _ce_resolve_edge_type,
            _normalize_nodes as _ce_normalize_nodes,
            build_timed_event_specs,
        )
        from formula import FormulaEngine
        from execution_module import EdgeExecutor, _lookup_edge_cond
        from event_bus import (
            EVENT_DOMAIN, EVENT_EXECUTED, EVENT_SIGNAL,
            EventBus, DataChanged, DomainEvent, Executed, Signal, TimeAdvanced, TickReceived,
        )
        from tick_bar_module import DataUpdater, BarComposer
        from execution_module import TTLHelper
        from execution_module import EventDriver
        from formula_module import ValueExtractor
        from domain import (
            TickSource, RealTickSource, MockDataSource,
            _stock_code, _normalize_stock_code, _MARKET_PREFIXES, _MARKET_SUFFIXES,
            time_at, _safe_timestamp, is_offset_of_day, anchor_to_today,
        )


logger = logging.getLogger(__name__)


class CompiledExpression:
    """解析并缓存单个表达式的 AST，提供安全的条件/值求值（ast 受控，禁 eval）。

    求值内核委托 ``tdx_evaluators._eval_derived_ast``，与
    evaluators._eval_derived_expr 同源，支持 +,-,*,/、比较、逻辑(and/or/not)、
    索引访问、_DERIVED_FUNCS 表内函数（max/min/abs/round）。
    AST 解析一次缓存复用，避免每 tick 重复解析。

    SubTask 27.1：从 core/_compat.py 迁移至 engine.py（_compat.py 已删除）。
    """

    _cache: Dict[str, "CompiledExpression"] = {}

    def __init__(self, source: str, tag: str = ""):
        self.source = source
        self.tag = tag or source
        try:
            self.tree = ast.parse(str(source), mode="eval")
        except SyntaxError as exc:
            logger.debug("CompiledExpression 解析失败 %s: %s", tag, exc)
            self.tree = None

    @classmethod
    def get(cls, source: str, tag: str = "") -> "CompiledExpression":
        key = f"{tag}::{source}"
        cached = cls._cache.get(key)
        if cached is not None:
            return cached
        inst = cls(source, tag)
        cls._cache[key] = inst
        return inst

    def evaluate(self, ctx: Dict[str, Any]) -> Any:
        if self.tree is None:
            raise ValueError(f"表达式未解析成功: {self.source}")
        return tdx_evaluators._eval_derived_ast(self.tree, ctx)

    def evaluate_conditional(self, cond_str: str, expr_str: str, ctx: Dict[str, Any]) -> Tuple[bool, Any]:
        """先求值条件表达式，条件为真时再求值结果表达式。"""
        cond_ok = bool(self.evaluate(ctx))
        if not cond_ok:
            return False, None
        expr = self.get(expr_str, f"expr_{self.tag}")
        return True, expr.evaluate(ctx)


# ---------------------------------------------------------------------------
# Protocol 接口：替代 services.* 跨层 import（SubTask 8.5）
# 通过构造函数注入工厂/可调用对象，消除 core→services 跨层依赖。
# ---------------------------------------------------------------------------


class IPoolValidator(Protocol):
    """池拓扑校验器接口（替代 services.pool_validator.validate_pool_topology）。

    作为可调用对象注入：``validator(nodes, edges, **kwargs)``。
    """

    def __call__(
        self,
        nodes: Dict[str, Any],
        edges: List[Dict[str, Any]],
        *,
        edge_semantics_cfg: Any = ...,
        dzh_type_map: Any = ...,
        dzh_full: Any = ...,
        topology_matcher: Any = ...,
    ) -> Any: ...


class IDataQuery(Protocol):
    """数据查询接口（替代 services.data.DataQuery / services.data_query.DataQuery）。

    作为工厂可调用对象注入：``DataQuery(**kwargs)`` 返回实例。
    """

    def query(self, code: str, field: str, period: str = "1d", count: int = 100) -> Any: ...
    def query_tick(self, code: str) -> Dict[str, Any]: ...


class IFormulaCache(Protocol):
    """公式缓存接口（替代 services.formula_cache.FormulaCache）。

    作为工厂可调用对象注入：``cache_factory()`` 返回实例。
    """

    def get(self, formula_ref: str, key: str) -> Any: ...
    def set(self, formula_ref: str, key: str, value: Any, ttl: int = 0) -> None: ...


class IMarketDataPort(Protocol):
    """行情数据端口接口（替代 services.market_data_port.TqAdapterMarketDataPort）。

    作为工厂可调用对象注入：``port_factory(tq_adapter)`` 返回实例。
    """

    def get_market_data(self, code: str) -> Dict[str, Any]: ...


_CFG = Path(__file__).parent.parent / "config"
_HR = {n: o for n, o in vars(_builtins).items() if callable(o) and not n.startswith("__")}


def _time_source_to_now(state: Any) -> _dt:
    """由 state.time_source 返回当前 datetime；wall_clock 返回系统时间。

    时间戳解析委托 ``time_at``（单一真相源）；I40：1e8 阈值与锚定逻辑收敛为
    ``is_offset_of_day`` + ``anchor_to_today``（time_util 单一真相源），消除散布。
    """
    sec = time_at(state=state)
    if is_offset_of_day(sec):
        return anchor_to_today(sec)
    try:
        return _dt.fromtimestamp(sec)
    except (OSError, ValueError):
        return _dt.now()


def _build_sig_dict(signal):
    """Signal dataclass → sig dict 单一构造点（I35 命名构造点 + I80 单一真相源）。

    I80：字段名 / 字段集 / 字段顺序的唯一真相源为 Signal dataclass（event_bus.py:79-95）。
    ``asdict`` 派生 dict，消除 _event_payload 第二份硬编码 8 字段列表 + 统一键名
    （"ts" 对齐 dataclass 字段名；原 API 路径 "time" 键废除，与 WS 路径统一）。
    I79 漂移根因消除：API 与 WS 两路径共享同一 asdict 派生，字段集不可能漂移。
    """
    return asdict(signal)


def _build_sim_tick_source(engine: "PoolEngine", ts_spec: Dict[str, Any], codes: List[str]) -> TickSource:
    """表驱动工厂：构造 MockDataSource 并注册 tick 定时器到 EventDriver（G5）。"""
    clock_start = time_at(state=engine.state)
    cfg = dict(ts_spec.get("config", {}))
    ds = MockDataSource(
        codes=codes,
        clock_start=clock_start,
        price_range=cfg.get("price_range", (5.0, 200.0)),
        change_pct_std=cfg.get("change_pct_std", 2.0),
        volume_lognorm_mu=cfg.get("volume_lognorm_mu", 14.0),
        volume_lognorm_sigma=cfg.get("volume_lognorm_sigma", 2.0),
    )
    # G5：tick 定时器注册到 EventDriver 统一优先队列（与边触发/TTL 同一队列）
    event_driver = engine._components.get("event_driver")
    event_bus = engine._components.get("event_bus")
    if event_driver is not None:
        ds.set_event_driver(event_driver, event_bus)
        ds.register_tick_timers(clock_start)
    return ds


def _build_real_tick_source(engine: "PoolEngine", ts_spec: Dict[str, Any], codes: List[str]) -> Optional[TickSource]:
    """表驱动工厂：构造 RealTickSource（需 PoolEngine 注入 tq_adapter）。"""
    tq = getattr(engine, "tq_adapter", None)
    if tq is None:
        return None
    return RealTickSource(
        snapshot_provider=lambda codes, _tq=tq: _tq.get_snapshot(codes),
        codes_provider=lambda: engine._collect_source_codes(engine.pool_config),
        default_interval=float(ts_spec.get("config", {}).get("interval", 1.0)),
    )


_TICK_SOURCE_FACTORIES: Dict[str, Callable[["PoolEngine", Dict[str, Any], List[str]], Optional[TickSource]]] = {
    "sim": _build_sim_tick_source,
    "real": _build_real_tick_source,
}


class PoolEngineMixin:
    """PoolEngine 辅助方法集合。

    将运行期辅助、生命周期、同步、模式入口等方法从核心类中剥离，
    使 ``PoolEngine`` 仅保留构造函数与真相属性。
    """

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    def _build_topology(self) -> Dict[str, List[str]]:
        adj: Dict[str, List[str]] = {nid: [] for nid in self.nodes}
        for ec in self._components["schedule"].edge_ctx.values():
            adj.setdefault(ec.sid, []).append(ec.eid)
        return adj

    @staticmethod
    def _snapshot_stocks(stocks: List[Any]) -> frozenset:
        return frozenset(_stock_code(s) for s in stocks if isinstance(s, dict))

    def _node_type(self, nid: str) -> str:
        """从编译产物读取节点类型，避免运行期调用 ``_resolve_node_type``。"""
        return self._components["schedule"].node_types.get(nid, "")

    def _init_node_stocks(self) -> None:
        """从 pool_config 节点初始化 node_stocks 表，并将预填股票注册 TTL 到 heapq（G1）。"""
        for nid, node in self.nodes.items():
            params = node.get("params", {})
            stocks = params.get("stocks", [])
            pool = self.state.get_pool(nid)
            pool.remove_stocks(list(pool.get_stock_codes()))
            if stocks:
                pool.add_stocks(list(stocks))

        driver = self._components.get("event_driver")
        if driver is not None:
            from .execution_module import time_at, register_ttl_spec
            from .execution_module import _stock_entry_time, _now_ts
            now_val = time_at(state=self.state)
            schedule = self._components.get("schedule")
            bus = self._components.get("event_bus")
            if schedule is not None:
                for nid, node in self.nodes.items():
                    params = node.get("params", {})
                    stocks = params.get("stocks", [])
                    if not stocks:
                        continue
                    for eid, ec in schedule.edge_ctx.items():
                        if ec.tid == nid:
                            edge_ttl = schedule.edge_ttl_spec.get(eid)
                            if edge_ttl is not None and edge_ttl.bdel == 1 and edge_ttl.check_type == "interval" and edge_ttl.ttl_sec > 0:
                                for stock in stocks:
                                    if isinstance(stock, dict):
                                        code = stock.get("code", "")
                                        if code:
                                            entry_ts = _stock_entry_time(stock) or now_val
                                            register_ttl_spec(driver, self.state, ec.tid, eid, code, edge_ttl.ttl_sec, entry_ts, bus)
                                break
                    node_ttl = schedule.node_ttl_spec.get(nid)
                    if node_ttl is not None and node_ttl.bdel == 1 and node_ttl.check_type == "interval" and node_ttl.ttl_sec > 0:
                        for stock in stocks:
                            if isinstance(stock, dict):
                                code = stock.get("code", "")
                                if code:
                                    entry_ts = _stock_entry_time(stock) or now_val
                                    register_ttl_spec(driver, self.state, nid, f"node_ttl:{nid}", code, node_ttl.ttl_sec, entry_ts, bus)

    def _mark_source_nodes_dirty(self) -> None:
        """首次运行时将源节点（modules.json 中 type=source）与带初始股票的状态池标脏，驱动初始边执行。"""
        source_ids = self._components["schedule"].source_node_ids
        for nid, node in self.nodes.items():
            params = node.get("params", {})
            has_initial_stocks = bool(params.get("stocks"))
            if nid in source_ids or has_initial_stocks:
                self.state.mark_node_dirty(nid)

    @staticmethod
    def _collect_source_codes(pool_config: Dict[str, Any]) -> List[str]:
        """从 pool_config 的 source 节点与初始股票中收集所有股票代码。

        用于为 TickSource 提供监控标的集合；非 source 节点的股票由转移事件动态产生，
        不在此处收集。
        """
        codes: List[str] = []
        seen: set = set()
        for node in pool_config.get("nodes", []):
            if not isinstance(node, dict):
                continue
            node_type = node.get("type", "")
            params = node.get("params", {}) or {}
            stocks = params.get("stocks", []) or []
            # 收集所有带 code 的股票；source 节点、候选池、状态池均可能带初始股票
            for stock in stocks:
                if not isinstance(stock, dict):
                    continue
                code = stock.get("code")
                if code and code not in seen:
                    codes.append(str(code))
                    seen.add(str(code))
        return codes

    @staticmethod
    def _derive_trade_pools(pool_config: Dict[str, Any]) -> Tuple[List[str], List[str]]:
        """从池配置推导自动买入/卖出池列表。

        - auto_buy_pools：psatt.baimpool == 1 的节点（入池即买入）。
        - auto_sell_pools：psatt.bdel == 1 且有 exit_action 的节点（TTL 出池即卖出）。
        """
        auto_buy: List[str] = []
        auto_sell: List[str] = []
        for node in pool_config.get("nodes", []) or []:
            if not isinstance(node, dict):
                continue
            nid = node.get("id", "")
            params = node.get("params") or {}
            psatt = params.get("psatt") or {}
            if not isinstance(psatt, dict):
                psatt = {}
            if psatt.get("baimpool") == 1 or psatt.get("baimpool") == "1":
                auto_buy.append(str(nid))
            if psatt.get("bdel") == 1 or psatt.get("bdel") == "1":
                exit_action = params.get("exit_action")
                if isinstance(exit_action, dict):
                    auto_sell.append(str(nid))
        return auto_buy, auto_sell

    def _build_tick_source(self, ds_cfg: Dict[str, Any]) -> Optional[TickSource]:
        """根据 data_source 配置行构建对应的 TickSource 实现。

        表驱动：配置行中的 ``tick_source.impl`` 查注册表获取工厂函数，避免核心循环
        出现 ``if is_simulation`` / ``if impl == "sim"`` 等模式分支。未配置
        tick_source 时返回 None，由调用方通过外部传入 current_bar_data 驱动。
        """
        ts_spec = ds_cfg.get("tick_source") if isinstance(ds_cfg, dict) else None
        if not ts_spec:
            return None
        impl = ts_spec.get("impl", "")
        codes = self._collect_source_codes(self.pool_config)
        factory = _TICK_SOURCE_FACTORIES.get(impl)
        if factory is None:
            return None
        return factory(self, ts_spec, codes)

    # ------------------------------------------------------------------
    # EventBus 接口
    # ------------------------------------------------------------------
    def get_events(self, event_type: Optional[str] = None) -> List[Any]:
        """暴露 ``EventBus`` 事件日志，供外部读取。"""
        return self._components["event_bus"].get_events(event_type)

    def _sync_events_to_meta(self) -> None:
        """将 ``EventBus`` 当前 tick 新增 ``Executed`` 同步到 ``transfer_events``。

        I67：修复历史重复处理 bug。旧实现每 tick 读取 ALL 历史 Executed 填入
        transfer_events，导致 ``_emit_transfer_events`` 对历史 transferred_codes
        重复发 ENTER（实测 copy 模式 2 stocks × 3 ticks → 12 ENTER，应 4）。

        修复：``_run_tick_body`` 入口记录 ``_tick_event_offset``（tick 边界），
        本方法仅同步 offset 之后新增事件。语义等价：``transfer_events`` 始终为
        "本 tick 新增转移"视图，与 ``_emit_transfer_events`` 的 per-tick 处理契约
        一致。``run_pool`` 的 ``EventBus.clear()`` 在 ``_run_tick_body`` 之前，
        offset 自动归零，无需额外 reset。

        I55/I61 后 ``EventBus`` 为唯一真相源；``_event_queue`` / ``_signal_queue``
        由订阅派生（永久视图），本方法不涉及。DomainEvent / Signal 由
        ``_emit_transfer_events`` 统一发射，本方法仅同步 Executed。
        """
        self._components["transfer_events"].clear()
        all_events = self._components["event_bus"].get_events()
        offset = self._components.get("_tick_event_offset", 0)
        for ev in all_events[offset:]:
            if isinstance(ev, Executed):
                self._components["transfer_events"].append({
                    "flow_id": ev.eid,
                    "source_id": ev.sid,
                    "target_id": ev.tid,
                    "fired": True,
                    "transferred_codes": list(ev.entered),
                    "exited_codes": list(ev.exited),
                    "mode": getattr(ev, "mode", "copy"),
                    "source_remaining": len(self.state.get_pool(ev.sid).get_stocks()),
                    "target_count": len(self.state.get_pool(ev.tid).get_stocks()),
                })

    # ------------------------------------------------------------------
    # 核心 tick 循环
    # ------------------------------------------------------------------
    async def run_tick(self) -> None:
        """新核心循环 async 入口：委托 ``_run_tick_body``（单一真相源）。

        ``run_tick`` 自身不含 await；保留 async 签名仅为兼容
        ``await engine.run_tick()`` 调用方（run_loop / _tick）。
        I21：消除 run_tick 与 _run_tick_body 的 33 行逐行重复——async 包装器
        不再维护逻辑副本，仅做同步委托。
        """
        self._run_tick_body()

    def _run_tick_body(self) -> None:
        """核心循环：统一时间驱动。

        driver.fire_due(now) 统一触发所有到时事件（边触发+TTL+tick，G1 heapq 弹出），
        到时即调 action 发布事件，订阅者执行逻辑。G2：引擎只发事件不执行计算，
        tick 数据生成由 TickBarModule 订阅 TickDue 后完成，本方法不再直接驱动 tick。
        """
        if self.state.time_source.get("driver_type") == "wall_clock":
            self.state.time_source["current_ts"] = _safe_timestamp(self._now())
        self._components["_tick_event_offset"] = len(self._components["event_bus"].get_events())
        if self.state.first_run:
            self._mark_source_nodes_dirty()
        # G2：删除旧路径中 tick_source.next_ticks() 直接驱动逻辑。
        # tick 由 EventDriver heapq 统一调度：MockDataSource 定时器发布 TickDue，
        # TickBarModule 订阅后生成 TickReceived → DataChanged → BarComposed。
        driver = self._components.get("event_driver")
        now = time_at(state=self.state)
        if driver is not None:
            driver.fire_due(now)
        self.state.clear_dirty()
        self.state.first_run = False
        self.state.snapshot_nodes()
        self._sync_events_to_meta()
        # SubTask 21.1: 引擎自身发布 TimeAdvanced，消除 RuntimeMode 反向订阅
        # （runtime_mode_module._on_tick_received 保留向后兼容，PoolEngine 成为主发布者）
        self._components["event_bus"].publish(TimeAdvanced(
            ts=now,
            source=self.state.time_source.get("driver_type", "wall_clock"),
        ))

    # SubTask 21.2: DataChanged 事件触发核心循环（可选订阅，默认关闭）
    def _on_data_changed_event(self, event: DataChanged) -> None:
        """DataChanged 事件 handler：更新当前时间戳后驱动核心循环。

        仅在 ``PoolEngine(subscribe_data_changed=True)`` 时注册。本 handler
        直接调用 ``_run_tick_body``（driver.fire_due 统一驱动）。G2 后引擎只发
        事件不执行计算，不存在 ExecutionModule per-edge 直接驱动路径。
        """
        self.state.time_source["current_ts"] = event.ts
        self._run_tick_body()

    def _on_tick_received(self, event: TickReceived) -> None:
        """TickReceived 事件 handler：将单只股票 tick 经 DataUpdater 注入 state。

        G5/G2 后 tick 由 EventDriver heapq 统一调度：MockDataSource 定时器发布
        TickDue，TickBarModule 订阅后调用 ``get_tick`` 生成 tick 数据并发布
        TickReceived。本 handler 将 tick 回注 DataUpdater，使 ``state.latest_tick``
        更新，并标记源节点脏，确保后续边触发 action 的 is_source_dirty 检查通过。
        """
        data_updater = self._components.get("data_updater")
        if data_updater is None:
            return
        tick_data = {event.code: event.tick_data}
        data_updater.apply_data(tick_data)
        # 标记源节点脏：边触发 action 需检测源节点脏才会执行筛选
        schedule = self._components.get("schedule")
        if schedule is not None:
            for nid in schedule.source_node_ids:
                self.state.mark_node_dirty(nid)

    def rebuild_timed_specs(self) -> None:
        """清空 EventDriver heap 并用当前 state.time_source 重新注册边触发规格。

        修复：``_init_pool_runtime`` 在 ``time_source.current_ts`` 设置前调用
        ``build_timed_event_specs``，导致 ``first_fire_time`` 使用 wall clock
        而非虚拟时钟。仿真模式下 ``fire_due(virtual_clock)`` 永远无法达到
        wall_clock + interval 的 ``first_fire_time``，边触发永不执行。

        此方法在 ``RuntimeSimulator.initialize()`` 设置 ``current_ts`` 后调用，
        确保边触发 ``first_fire_time`` 与虚拟时钟对齐。tick 定时器由
        ``_configure_sim_tick_source`` 在此方法之后注册，不受影响。
        """
        event_driver = self._components.get("event_driver")
        if event_driver is None:
            return
        event_driver._heap.clear()
        event_driver._seq = 0
        event_driver._tick_seq = getattr(type(event_driver), "_TICK_SEQ_BASE", -10**9)
        schedule = self._components.get("schedule")
        edge_executor = self._components.get("edge_executor")
        event_bus = self._components.get("event_bus")
        if schedule is None or edge_executor is None:
            return
        build_timed_event_specs(
            schedule, self.state, self, edge_executor,
            event_driver=event_driver, bus=event_bus,
        )

    # ------------------------------------------------------------------
    # PoolEngine 公共 API 适配
    # ------------------------------------------------------------------
    def run_pool(self, current_bar_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self.state.first_run = True
        self.state.clear_dirty()
        self._components["transfer_events"].clear()
        self._components["event_bus"].clear()
        self.events = []
        self._loop_pool_config = self.pool_config
        while not self._event_queue.empty():
            try:
                self._event_queue.get_nowait()
            except Exception:
                break
        while not self._signal_queue.empty():
            try:
                self._signal_queue.get_nowait()
            except Exception:
                break

        # 以单一时间源初始化：未配置 virtual/sequence 时默认 wall_clock。
        # 测试 patch engine._now() 时，wall_clock 分支委托 PoolEngine._now()，
        # gate 仍能读到 mock 时间。
        ts_cfg = self.state.time_source
        if not ts_cfg or ts_cfg.get("driver_type") in (None, "wall_clock"):
            now_ts = _safe_timestamp(self._now())
            self.state.time_source = {
                "kind": "run_pool",
                "current_ts": now_ts,
                "start_ts": now_ts,
                "driver_type": "wall_clock",
            }

        if current_bar_data:
            self._components["data_updater"].apply_data(current_bar_data)
        else:
            # I13：.clear() 而非 = {}，保留 dict 对象身份使 TickTable view 引用稳定
            self.state.latest_tick.clear()
            self.state.bars.clear()

        self._init_node_stocks()
        # I13：删除 _inject_bar_data —— bar 字段统一经 latest_tick/TickTable 读取，
        # node_stocks 仅保留身份/池级字段（code/label/_tracker/indate/intime/inprice）。

        self.events.append({
            'event': 'pool_start',
            'pool_id': self.pool_config.get('id') or self.pool_config.get('pool_id', ''),
            'pool_name': self.pool_config.get('name', ''),
            'started_at': _dt.now().isoformat(),
            'node_count': len(self.nodes),
            'edge_count': len(self.pool_config.get('edges', [])),
        })

        success = True
        error_msg = None
        try:
            self._run_tick_body()
        except Exception as ex:
            success = False
            error_msg = str(ex)
            logger.error("新核心 run_tick 执行失败: %s", ex, exc_info=True)

        # I16：DZH TTL 路径收敛——_build_ttl_spec 编译期已解析 DZH endtime/hold 三模式，
        # _run_ttl 按 check_type 分派，EventDriver.fire_due（_run_tick_body 内）统一驱动（G1 heapq）。
        # 旧 apply_ttl post-tick 循环已删除，2 套 TTL 路径收敛为 1 套。
        # Task 24+：三方法（_update_trackers / _emit_transfer_events / _post_tick）已删除，
        # tracker 更新/事件生成/post_tick 流水线由 Statistics/Monitoring 模块通过事件订阅实现。
        # DomainEvent(ENTER/EXIT) 发射已由 I23 合并入 Executed.details，_emit_transfer_events
        # 的批量发射为冗余路径，删除后 _event_queue 不再收到 ENTER/EXIT（Executed.details 仍携带）。
        for ev in self._components["transfer_events"]:
            self.events.append({'event': 'flow_fired', **ev})

        self.events.append({
            'event': 'pool_end',
            'pool_id': self.pool_config.get('id') or self.pool_config.get('pool_id', ''),
            'finished_at': _dt.now().isoformat(),
            'total_transferred': sum(len(ss) for ss in self.state.node_stocks.values()),
            'flow_fired_count': len(self._components["transfer_events"]),
        })

        ns_result = {}
        for nid, ss in self.state.node_stocks.items():
            node_info = self.nodes.get(nid, {})
            ns_result[nid] = {
                'stocks': ss,
                'type': node_info.get('type', ''),
                'name': node_info.get('name', '') or node_info.get('label', ''),
            }

        return {
            'success': success,
            'error': error_msg,
            'node_states': ns_result,
            'total_transferred': sum(len(ss) for ss in self.state.node_stocks.values()),
            'events': self._components["transfer_events"],
        }

    async def run_loop(self, current_bar_data: Optional[Dict[str, Any]] = None) -> Dict[str, List[Any]]:
        self._components["_stopped"] = False
        self._components["_paused"] = False
        # I39: 表驱动时间源 — 从 time_sources.json 加载（含 driver_type），
        # 替代手工构造 {"kind": "live", ...}（缺 driver_type 导致 _run_tick_body
        # 的 wall_clock 刷新分支被跳过，current_ts 冻结，gate/TTL 评估使用过期时间）。
        # 模式 ID 由 _current_mode_id 驱动（默认 live），不再硬编码 "live"。
        mode_id = self._current_mode_id or "live"
        mode_cfg = self._runtime_modes.get(mode_id, {})
        tick_interval = float(mode_cfg.get("tick_interval", 1.0))
        ts_id = mode_cfg.get("time_source_id", "realtime")
        ts_cfg = dict(self._time_sources.get(ts_id, {}))
        now_ts = _safe_timestamp(self._now())
        ts_cfg.setdefault("current_ts", now_ts)
        ts_cfg.setdefault("start_ts", now_ts)
        self.state.set_time_source(ts_cfg)
        self._init_node_stocks()
        # I90：移除 attach_to_loop——tick 调度本身已中断驱动（loop.call_at + Event），
        # 边触发由 heapq 优先队列按 fire_time 独立触发（G6 运行时事件无序）。
        # 原 attach_to_loop 注册的中断回调与 _run_tick_body 扫描路径双重执行边，
        # 且中断路径无统一调度保证，破坏 A→B→C 拓扑传播。
        loop = asyncio.get_running_loop()
        tick_event = asyncio.Event()
        pause_event = self._components["_pause_event"]

        def _wake_tick() -> None:
            tick_event.set()

        def _schedule_next_tick(delay: float) -> None:
            """用 loop.call_at 调度下一次唤醒（中断驱动，非 sleep 轮询）。"""
            tick_event.clear()
            loop.call_at(loop.time() + delay, _wake_tick)

        while not self._components["_stopped"]:
            if self._components["_paused"]:
                # I6: 中断驱动 —— 暂停态 clear+wait，resume 时 _pause_event.set() 唤醒，无 sleep 轮询
                pause_event.clear()
                await pause_event.wait()
                continue
            if not self._is_trading_time():
                _schedule_next_tick(tick_interval or 1.0)
                await tick_event.wait()
                continue
            # Task 1：行情注入收敛到 _run_tick_body 内的 TickSource，
            # run_loop 不再按模式调用 refresh_handler，避免核心循环分支。
            await self.run_tick()
            _schedule_next_tick(tick_interval or 1.0)
            await tick_event.wait()
        return self.state.node_stocks

    def _mode_config_row(self, table_name: str, key: str, default_key: str = "") -> Dict[str, Any]:
        """PoolEngine 委托 _read_config_row 读取配置表行。"""
        return self._read_config_row(table_name, key, default_key)

    def _now(self) -> _dt:
        """PoolEngine 时间统一入口：wall_clock 委托 ``PoolEngine._now()``（保测试 patch），
        virtual/sequence 委托 ``_time_source_to_now``（基于 ``time_at``）。

        测试 patch ``engine._now()`` 时，wall_clock 分支委托 PoolEngine._now()，
        gate 仍能读到 mock 时间。
        """
        ts_cfg = self.state.time_source
        if ts_cfg.get("driver_type", "wall_clock") == "wall_clock":
            return self._now()
        return _time_source_to_now(self.state)

    async def run_mode(self, mode_id: str) -> Dict[str, Any]:
        """三模式统一入口：仅替换 time/data/trade/side-effects 四张表行。

        核心循环 ``run_tick()`` 不再按模式分支；模式差异完全体现在
        ``state.time_source`` / ``state.data_source`` /
        ``state.trade_interface`` / ``state.side_effects_scope`` 四行。
        """
        mode_cfg = self._runtime_modes.get(mode_id, {})
        ts_cfg = self._time_sources.get(mode_cfg.get("time_source_id", "realtime"), {})
        ds_cfg = self._mode_config_row("data_sources", mode_cfg.get("data_source_id", ""))
        ti_cfg = self._trade_interfaces.get(mode_cfg.get("trade_interface_id", "noop"), {})
        se_cfg = self._mode_config_row("side_effect_scopes", mode_cfg.get("side_effects_scope", ""))

        self.state.set_time_source(ts_cfg)
        self.state.set_data_source(ds_cfg)
        self.state.set_trade_interface(ti_cfg)
        self.state.set_side_effects_scope(se_cfg)

        # Task 1：根据 data_source 配置注入 TickSource，核心循环无模式分支
        self._components["tick_source"] = self._build_tick_source(ds_cfg)

        # 仿真/回放模式：注入 bars_history_getter 到 FormulaEngine 的 DataQuery
        if ts_cfg.get("driver_type") == "virtual":
            self._inject_bars_history_getter()

        # 运行时不变量：模式切换不得替换核心循环/边执行函数对象。
        # 仿真与实盘必须调用 _run_tick_body / EdgeExecutor.run 的同一实现。
        assert self._run_tick_body.__func__ is PoolEngineMixin._run_tick_body
        assert self._components["edge_executor"].run.__func__ is EdgeExecutor.run

        self._current_mode_id = mode_id
        self._loop_pool_config = self.pool_config

        # run_mode 是模式（重新）启动入口，每次进入新模式均重置 first_run
        self.state.first_run = True

        # 回放模式进入隔离副本；切换到非回放模式时自动恢复实盘状态
        if mode_id == "replay":
            self.state.enter_replay()
        elif self.state.is_replay_active():
            self.state.exit_replay()

        try:
            nodes = {n['id']: n for n in self.pool_config.get('nodes', [])}
            # SubTask 8.5: 跨层 import 改为构造函数注入 Protocol
            _validator = getattr(self, '_pool_validator', None)
            if _validator is not None:
                _validator(
                    nodes,
                    self.pool_config.get('edges', []),
                    edge_semantics_cfg=self._edge_semantics_cfg,
                    dzh_type_map=self._dzh_type_map,
                    dzh_full=self._dzh_full,
                    topology_matcher=self._topology_matcher,
                )
        except Exception as ex:
            logger.warning("拓扑校验失败: %s", ex)
        self._init_node_stocks()
        if mode_cfg.get("loop_entry_policy") != "internal_loop":
            return {"node_stocks": self.state.node_stocks, "inject": True}
        self._components["_stopped"] = False
        self._components["_paused"] = False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return {"node_stocks": self.state.node_stocks, "inject": True}
        self._components["_loop_task"] = loop.create_task(self.run_loop())
        return {"node_stocks": self.state.node_stocks, "inject": True, "task": self._components["_loop_task"]}

    async def _tick(
        self,
        node_stocks: Dict[str, List[Any]],
        current_bar_data: Optional[Dict[str, Any]] = None,
        mode_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, List[Any]]:
        self._loop_pool_config = self.pool_config
        self._components["transfer_events"].clear()
        for nid, stocks in node_stocks.items():
            pool = self.state.get_pool(nid)
            pool.remove_stocks(list(pool.get_stock_codes()))
            pool.add_stocks(list(stocks))
        if current_bar_data:
            self._components["data_updater"].apply_data(current_bar_data)
        # I13：删除 _inject_bar_data + mode_state["inject"] 门控 ——
        # apply_data 已是事件驱动唯一真相源，node_stocks 不再携带 bar 字段副本。
        # mode_state["inject"] 标志仍由 run_mode 返回（replay 测试 oracle 比对依赖），但不再驱动注入。
        # 镜像旧 _tick：检测 node_stocks 变更并标脏
        for nid in self.nodes:
            old = self.state.node_snapshots.get(nid)
            new = self._snapshot_stocks(self.state.get_pool(nid).get_stocks())
            if old != new:
                self.state.mark_node_dirty(nid)
        await self.run_tick()
        # Task 24+：_emit_transfer_events 调用已删除（DomainEvent 发射由事件订阅路径接管）。
        return dict(self.state.node_stocks)


# 允许的 override 标志键白名单（消除 == 'is_move' / == 'is_overwrite' 双 if 字面量分派）
_OVERRIDE_FLAGS = frozenset({"is_move", "is_overwrite"})


class PoolEngine(PoolEngineMixin):
    """统一核心引擎（Task 24 合并 MetaEngine + PoolEngine）。

    持有编译期静态表、PoolState、EdgeExecutor，执行事件驱动的 tick 循环。
    原 MetaEngine 的配置加载与运行时辅助方法已合并入此类，消除双重引擎结构。

    构造函数两种用法：
      1. ``PoolEngine()`` — 仅加载配置，延迟初始化池运行时（等 ``run_pool`` 等方法触发）
      2. ``PoolEngine(pool_config=cfg)`` — 加载配置并立即初始化池运行时
    """

    def _init_pool_runtime(self, pool_config: Dict[str, Any],
                           subscribe_data_changed: bool = False) -> None:
        """初始化池运行时：编译 schedule、装配 EdgeExecutor/EventDriver 等组件。

        原 ``PoolEngine.__init__`` 主体，现由 ``__init__`` 或 ``_ensure_pool_engine``
        按需调用。``self._pool_engine`` 在初始化完成后置为 ``self``，使原
        ``PoolEngine._pool_engine`` 引用路径保持兼容（Task 24 合并前 MetaEngine 持有
        ``_pool_engine`` 子组件引用，合并后 ``_pool_engine`` 即 ``self``）。
        """
        self.pool_config = pool_config
        self.nodes = {
            n['id']: n for n in pool_config.get('nodes', [])
            if isinstance(n, dict) and n.get('id')
        }
        # SubTask 29.6: PoolState 已合并到 runtime_mode_module.py，懒加载避免循环依赖
        from .runtime_mode_module import PoolState
        self.state = PoolState(pool_config)

        event_bus = self._injected_bus or EventBus()
        data_updater = DataUpdater(self.state, event_bus)
        bar_composer = BarComposer(self.state, event_bus)
        bar_composer.subscribe()
        from .trade_module import TradeModule
        auto_buy_pools, auto_sell_pools = self._derive_trade_pools(pool_config)
        trade_module = TradeModule(
            event_bus,
            config={
                "storage": getattr(self, "storage", None),
                "pool_id": pool_config.get("id") or pool_config.get("pool_id", ""),
                "trade_interface": "paper_trade",
                "auto_buy_pools": auto_buy_pools,
                "auto_sell_pools": auto_sell_pools,
            },
        )
        trade_executor = trade_module._trade_executor
        from .execution_module import EventDriver
        event_driver = EventDriver(state=self.state, bus=event_bus)
        from .monitoring_module import _EventPanel
        event_panel = _EventPanel(event_bus, event_driver)
        event_panel.subscribe()
        # I34：BUY 信号收敛 — EventBus Signal 订阅者将 BUY 信号写入 _signal_queue
        self._event_bus = event_bus
        event_bus.subscribe(EVENT_SIGNAL, self._on_signal_event)
        event_bus.subscribe(EVENT_DOMAIN, self._on_domain_event)
        # G5 桥接：MockDataSource 通过 EventDriver 逐只发布 TickReceived，
        # 需在此订阅将 tick 回注 DataUpdater → DataChanged(tick) → BarComposed
        event_bus.subscribe(TickReceived, self._on_tick_received)
        schedule = Compiler.compile(pool_config)
        _dq = self._data_query
        formula_engine = FormulaEngine(self.state, data_query=_dq)
        # 依赖注入：将公式/选股模块实现注入 EdgeExecutor，避免 execution_module 跨模块 import。
        from .formula_module import EvalContext, live_context
        from .screening_module import eval_scalar_nset
        edge_executor = EdgeExecutor(
            self.state,
            schedule,
            formula_engine,
            event_bus=event_bus,
            event_driver=event_driver,
            scalar_nset_fn=eval_scalar_nset,
            eval_ctx_factory=lambda *args, **kwargs: EvalContext(*args, **kwargs),
            live_ctx_fn=live_context,
        )

        # G1: build_timed_event_specs 直接注册到 event_driver heapq（边触发+TTL endtime）
        build_timed_event_specs(
            schedule, self.state, self, edge_executor,
            event_driver=event_driver, bus=event_bus,
        )

        self._components = {
            "schedule": schedule,
            "formula_engine": formula_engine,
            "event_bus": event_bus,
            "data_updater": data_updater,
            "bar_composer": bar_composer,
            "edge_executor": edge_executor,
            "event_driver": event_driver,
            "trade_executor": trade_executor,
            "event_panel": event_panel,
            "_stopped": False,
            "_paused": False,
            "_pause_event": asyncio.Event(),
            "_loop_task": None,
            "transfer_events": [],
        }
        self._components["_pause_event"].set()
        self.state.topology = self._build_topology()
        self._subscribe_data_changed = subscribe_data_changed
        if subscribe_data_changed:
            self._components["event_bus"].subscribe(DataChanged, self._on_data_changed_event)
        # self._pool_engine 指向 self，使 simulator/replay 中 self._engine._pool_engine 路径保持兼容
        self._pool_engine = self

    @staticmethod
    def _config_signature(pool_config: Dict[str, Any]) -> str:
        """生成 pool_config 的边/节点结构签名，用于检测配置实质变化（同 id 不同边时重建）。"""
        import hashlib
        import json
        try:
            edges_sig = sorted(
                (str(e.get("id", "")), str(e.get("source", "")), str(e.get("target", "")),
                 str(sorted((e.get("params") or {}).keys())))
                for e in (pool_config.get("edges") or [])
            )
            nodes_sig = sorted(
                (str(n.get("id", "")), str(n.get("type", "")))
                for n in (pool_config.get("nodes") or [])
            )
            raw = json.dumps({"edges": edges_sig, "nodes": nodes_sig}, sort_keys=True, ensure_ascii=False)
            return hashlib.md5(raw.encode("utf-8")).hexdigest()
        except Exception:
            return ""

    def _ensure_pool_engine(self, pool_config: Dict[str, Any]) -> 'PoolEngine':
        """按需创建/复用池运行时（Task 24 合并后 ``_pool_engine`` 即 ``self``）。``pool_config`` 变更时重建组件。"""
        pool_id = pool_config.get('id') if isinstance(pool_config, dict) else None
        current_id = self.pool_config.get('id') if getattr(self, 'pool_config', None) else None
        new_sig = self._config_signature(pool_config)
        current_sig = getattr(self, '_config_sig', None)
        need_reinit = (self._pool_engine is None or current_id != pool_id or current_sig != new_sig)
        if need_reinit:
            self._config_sig = new_sig
            self._init_pool_runtime(pool_config)
            self._attach_ui_layer(self)
        return self

    def _inject_bars_history_getter(self) -> None:
        """仿真模式：将 bars_history_getter 注入 FormulaEngine._data_query。"""
        fe = self._components.get("formula_engine") if getattr(self, '_components', None) else None
        if fe is None:
            return
        try:
            from .tick_bar_module import make_bars_history_getter
            getter = make_bars_history_getter(self.state)
        except Exception:
            return
        dq = fe._data_query
        if dq is not None:
            dq.bars_history_getter = getter
        else:
            _dq_factory = getattr(self, '_data_query_factory', None)
            if _dq_factory is not None:
                dq = _dq_factory(bars_history_getter=getter)
                fe._data_query = dq

    def __getattr__(self, name: str) -> Any:
        """允许旧代码通过 self.event_bus 等形式访问 _components 容器中的组件。"""
        components = self.__dict__.get('_components', {})
        if name in components:
            return self._components[name]
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    # ------------------------------------------------------------------
    # PoolEngine 兼容包装方法：接受 pool_config 参数，延迟初始化后委托 PoolEngineMixin
    # ------------------------------------------------------------------
    def run_pool(self, pool_config: Optional[Dict[str, Any]] = None,
                 current_bar_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """运行池：若提供 pool_config 则延迟初始化，然后委托 PoolEngineMixin.run_pool。"""
        if pool_config is not None:
            self._ensure_pool_engine(pool_config)
        return PoolEngineMixin.run_pool(self, current_bar_data)

    async def run_loop(self, pool_config: Optional[Dict[str, Any]] = None,
                       current_bar_data: Optional[Dict[str, Any]] = None) -> Dict[str, List[Any]]:
        """运行循环：若提供 pool_config 则延迟初始化，然后委托 PoolEngineMixin.run_loop。"""
        if pool_config is not None:
            self._ensure_pool_engine(pool_config)
        return await PoolEngineMixin.run_loop(self, current_bar_data)

    async def run_mode(self, mode_id: str,
                       pool_config: Optional[Dict[str, Any]] = None,
                       current_bar_data: Optional[Dict[str, Any]] = None,
                       **kwargs: Any) -> Dict[str, Any]:
        """切换模式：若提供 pool_config 则延迟初始化，然后委托 PoolEngineMixin.run_mode。"""
        if pool_config is not None:
            self._ensure_pool_engine(pool_config)
        return await PoolEngineMixin.run_mode(self, mode_id)

    async def _tick(self, pool_config: Optional[Dict[str, Any]] = None,
                    node_stocks: Optional[Dict[str, List[Any]]] = None,
                    current_bar_data: Optional[Dict[str, Any]] = None,
                    mode_state: Optional[Dict[str, Any]] = None) -> Dict[str, List[Any]]:
        """单步 tick：若提供 pool_config 则延迟初始化，然后委托 PoolEngineMixin._tick。"""
        if pool_config is not None:
            self._ensure_pool_engine(pool_config)
        return await PoolEngineMixin._tick(self, node_stocks or {}, current_bar_data, mode_state)

    def execute_pool(self, pool_config: Dict[str, Any],
                     current_bar_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """``PoolEngine.execute_pool`` 兼容入口。"""
        return self.run_pool(pool_config, current_bar_data)

    # ------------------------------------------------------------------
    # 运行时辅助方法（原 _MetaEngineCompat 中仍被使用的方法）
    # ------------------------------------------------------------------
    def _build_event_detail(self, detail_map, mapping_key, ctx):
        """从 detail_mapping 构建事件详情字典（与原 _build_detail 闭包逻辑一致）。"""
        mapping = detail_map.get(mapping_key, {})
        detail = {}
        for fname, fspec in mapping.get("fields", {}).items():
            val = ctx.get(fspec.get("source", ""))
            if val is None:
                val = fspec.get("default")
            if val is not None:
                detail[fname] = val
        return detail
    def _emit_domain_event(self, domain, domain_ctx, code):
        """通用领域事件发射器：根据 event_domain_templates 表执行标准事件+信号发射流程。

        I57：消除 vestigial 批量抽象——``codes`` 字段恒为 ``[code]``（``_resolve_domain_ctx``
        per-code 构造上下文），``for code in codes`` 单次迭代为冗余控制流。``code`` 提升为
        显式参数，使 per-code 契约从数据暗示（list 单元素）升级为签名明示。

        domain_ctx 必须包含模板引用的所有变量：
        - pool_enter/move_exit: sid, tid, fid, mode, transferred=[], target_role/source_role, stock_index, lpc, ...
        - ttl_expire: nid, resolved_role, tracker_info, prev_stock_index, stock_index, lpc, ...
        """
        tpl = self._event_domain_templates.get(domain, {})
        if not tpl:
            return

        trigger_match = tpl.get('trigger_match', {})
        signal_trigger = tpl.get('signal_trigger', '')
        role_ref = tpl.get('role_ref', '')
        context_fields = tpl.get('context_fields', {})
        # I71：pool_id 表驱动 — per-domain 声明 pool_id 来源字段，消除 hardcoded
        # tid→nid fallback。move_exit pool_id=sid（源池，语义="离开源池"），pool_enter
        # pool_id=tid（目标池，语义="进入目标池"），ttl_expire pool_id=nid。修复 move_exit
        # DomainEvent(EXIT)+SELL 信号 pool_id=tid 语义错位（外部消费者 WS/API 收到错误池 ID）。
        # I59: pool_id hoist (was duplicated in event loop + signal loop)
        pool_id_field = tpl.get('pool_id_field', 'tid')
        pool_id = domain_ctx.get(pool_id_field, domain_ctx.get('nid', ''))

        # 解析角色
        role = domain_ctx.get(role_ref) if role_ref else None

        # 解析事件 defs
        event_defs = self._event_rules.get("events", {}) if hasattr(self, '_event_rules') else {}
        signal_defs = self._signal_rules.get("signals", {}) if hasattr(self, '_signal_rules') else {}
        detail_map = self._event_rules.get("detail_mapping", {}) if hasattr(self, '_event_rules') else {}

        # I57：per-code 契约——code 由签名传入，不再从 domain_ctx['codes'] 解包单元素 list。
        # 构建上下文
        ctx = {}
        for field_key, field_spec in context_fields.items():
            ctx[field_key] = self._resolve_context_field(field_spec, domain_ctx)

        # 发射事件（I61：_push_event 经 EventBus.publish(DomainEvent) 发布，
        # _on_domain_event 订阅者桥接至 _event_queue，与 I55 Signal 同构）
        for etype, erule in event_defs.items():
            if erule.get("trigger", {}).get("type") != trigger_match.get("type"):
                continue
            detail_key = erule.get("detail_mapping_key", "")
            detail = self._build_event_detail(detail_map, detail_key, ctx) if detail_map else {}
            self._push_event(etype, code, pool_id, detail)

        # 发射信号
        # I42/I55：信号路由表驱动 — tpl.signal_route 声明路由方式：
        #   "eventbus"=由上游 EdgeExecutor._action_baimpool 经 EventBus 发布（跳过此处），
        #   "direct"=由此处经 EventBus.publish(Signal) 发布（I55：统一到 EventBus，
        #            旧 _push_signal 直写已删除，SELL 亦经 _on_signal_event 订阅写入）。
        # 消除原 signal_trigger=="pool_enter" 硬编码分支，路由决策入 edge_strategies.json 表。
        if tpl.get('signal_route') == 'eventbus':
            return

        # I77：require_holding_tracker — 表驱动 condition 求值收敛。signal_rules.json:26 声明
        # move_exit condition 含 `tracker.status == 'holding'`（3-AND），但
        # _should_emit_signal_for_domain（engine.py:1240-1255）运行期仅检 trigger.type +
        # is_target（2-AND），第 3 条件路径死 → fallback 无条件发 SELL。预填股票
        #（params.stocks，engine.py:235 / tdx.py:1753）无 _tracker（_init_entry_trackers
        # 仅 edge-entered 创建，edge_executor.py:147-190）→ _tracker_detail 返回 {}
        # → ctx['tracker.snapshot']={} → price resolve_field default 0 → SELL(price=0)
        # 发给非持仓股票，经 WS(ui_renderer.py Signal 分支)/API(app.py:407-420) 转发给外部消费者。
        # 本守卫在 Signal 发射前（DomainEvent 已发，不影响 EXIT/TIMEOUT 事件）校验 tracker
        # 携带 status=='holding'，使运行期行为与声明 condition 语义等价。与 I76 同构
        #（config 声明条件 vs runtime 消费子集 → 路径死 → fallback masks bug）。
        # I78：守卫参数化 — tracker ctx key 由 require_holding_tracker_field 声明
        #（move_exit=tracker.snapshot, ttl_expire=tracker.detail），消除 I77 形状硬编码。
        if tpl.get('require_holding_tracker'):
            tracker_key = tpl.get('require_holding_tracker_field', 'tracker.snapshot')
            tracker_info = ctx.get(tracker_key, {})
            if tracker_info.get('status') != 'holding':
                return

        # I59: per-event 不变量分离（two-pass 范式）。Signal 除 signal_type 外所有字段
        # （cond/price/profit_pct/hold_days/ts）均 per-event 不变量——依赖 ctx/domain_ctx，
        # 不依赖 sig_type/sig_rule。旧 per-signal 解析在 N≥2 匹配时重复解析不变量 N 次。
        # two-pass：先过滤匹配信号，再解析不变量一次（lazy，仅 ≥1 匹配时），最后发射。
        # ts hoist 一次确保 tick 内时间戳一致（I57 开放关注点，作为 per-event 范式自然推论）。
        # 逻辑隐于数据结构（Signal 字段除 signal_type 外均 per-event），差异显于 signal_type。
        field_res = tpl.get('field_resolution', {})
        price_spec = field_res.get('price', {'chain': ['quote.price'], 'default': 0})
        profit_pct_spec = field_res.get('profit_pct', {'chain': [], 'default': 0})
        hold_days_spec = field_res.get('hold_days', {'chain': [], 'default': 0})

        matching = [(st, sr) for st, sr in signal_defs.items()
                    if self._should_emit_signal_for_domain(sr, signal_trigger, role)]
        if not matching or self._event_bus is None:
            return

        # per-event 不变量：解析一次，跨所有匹配信号复用
        cond = domain_ctx.get('cond', '')
        price = self._value_extractor.resolve_field(price_spec, ctx)
        profit_pct = self._value_extractor.resolve_field(profit_pct_spec, ctx)
        hold_days = self._value_extractor.resolve_field(hold_days_spec, ctx)
        ts = time_at(state=getattr(self._pool_engine, 'state', None))

        # I55：SELL 信号路径统一到 EventBus（范式升级），EventBus 为 ALL Signal 单一真相源。
        for sig_type, _ in matching:
            self._event_bus.publish(Signal(
                signal_type=sig_type, code=code, pool_id=pool_id,
                price=price, ts=ts, condition=cond,
                profit_pct=profit_pct, hold_days=int(hold_days) if hold_days else 0,
            ))
    def _get_stock_price(self, code, current_bar_data):
        if not current_bar_data:
            return 0.0
        # Build candidate keys: original code, normalized pure number,
        # and common suffix/prefix variants to handle format mismatches
        norm = _normalize_stock_code(code)
        candidates = [code, norm]
        for suffix in _MARKET_SUFFIXES:
            candidates.append(norm + suffix)
        for prefix in _MARKET_PREFIXES:
            candidates.append(prefix + norm)
        # Deduplicate while preserving order
        seen = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            bar = current_bar_data.get(candidate)
            if isinstance(bar, dict):
                for item in self._price_fields.get("priority", [{"field": "close"}]):
                    k = item if isinstance(item, str) else item.get("field")
                    if (v := bar.get(k)) is not None:
                        try: return float(v)
                        except (ValueError, TypeError): continue
        return 0.0

    def _log_transfer_batch(self, sid, tid, transferred, mode, cond):
        """批量写入 stock_transfer_log（保留副作用）。"""
        if not hasattr(self, 'storage') or not self.storage or not transferred:
            return
        lpc = self._loop_pool_config
        try:
            pe = self._pool_engine
            kline_time = None
            if pe is not None:
                kline_time = _time_source_to_now(pe.state).isoformat()
            logger.info("写入stock_transfer_log: %d条, sid=%s tid=%s", len(transferred), sid, tid)
            for code in transferred:
                self.storage.log_stock_transfer(
                    lpc.get('name', '') if lpc else '', sid, tid, code, mode,
                    trigger=cond, kline_time=kline_time
                )
        except Exception as ex:
            logger.warning("stock_transfer_log写入失败: %s", ex)
    def _push_event(self, et, code, pool_id, detail=None):
        """I61：DomainEvent 统一经 EventBus 发布（与 I55 Signal 同构）。

        旧直写 _event_queue 已删除；_event_queue 由 _on_domain_event 订阅派生。
        EventBus 成为 ALL DomainEvent（ENTER/EXIT/TIMEOUT/RANK_CHANGED）单一真相源，
        UIRenderer（订阅 EVENT_DOMAIN）自动获得全部 DomainEvent 推送。
        I70：TIMEOUT 由 _run_ttl 直发（不经 _push_event），_push_event 仅发 ENTER/EXIT/RANK_CHANGED。
        """
        if self._event_bus is None: return
        self._event_bus.publish(DomainEvent(
            event_type=et, code=code, pool_id=pool_id, details=detail or {}
        ))
    @property
    def _signal_events(self):
        """I33：_signal_events 收敛为 _signal_queue 的派生视图（单一写入点）。

        I33 收敛为 _signal_queue.put_nowait 单写，_signal_events 派生自队列快照。
        _signal_queue 是异步消费视图，_signal_events 是同步日志视图，
        两者均派生自同一队列真相源。I34：BUY 信号经 EventBus → _on_signal_event
        订阅写入 _signal_queue，消除 _action_baimpool 与 _emit_domain_event 双发 BUY。
        I55：SELL 信号亦统一经 EventBus → _on_signal_event 写入，EventBus 成为
        ALL Signal 单一真相源，_signal_queue 纯派生视图（所有条目均经 _on_signal_event）。
        """
        return list(self._signal_queue._queue)
    def _on_signal_event(self, signal):
        """I34/I55：EventBus Signal 订阅者 — 将 Signal dataclass 转为 sig dict 写入 _signal_queue。

        I34：BUY 信号由 EdgeExecutor._action_baimpool 发布到 EventBus，经此订阅者
        转为 _signal_queue 条目。消除 BUY 双发：原 _emit_domain_event 的 pool_enter BUY
        与 _action_baimpool 的 BUY 重复，现 _emit_domain_event 跳过 pool_enter BUY，
        BUY 单一经 _action_baimpool → EventBus → 此订阅者。
        I55：SELL 信号（move_exit/ttl_expire）亦由 _emit_domain_event 经 EventBus 发布，
        经此订阅者写入 _signal_queue。EventBus 成为 ALL Signal（BUY+SELL）单一真相源，
        UIRenderer（订阅 EVENT_SIGNAL）自动获得 SELL 推送。删除 _push_signal 方法
        （与 _on_signal_event 逻辑完全相同，均为 _build_sig_dict + put_nowait）。
        I35：sig dict 构造委托 _build_sig_dict（单一构造点）。
        """
        sig = _build_sig_dict(signal)
        try: self._signal_queue.put_nowait(sig)
        except asyncio.QueueFull: logger.warning("信号队列已满: %s %s", signal.signal_type, signal.code)
    def _on_domain_event(self, event):
        """I61：EventBus DomainEvent 订阅者 — 将 DomainEvent dataclass 转为 event dict 写入 _event_queue。

        I55 的"另一半"：Signal 已统一到 EventBus（BUY+SELL），DomainEvent 现统一到 EventBus
        （ENTER/EXIT/TIMEOUT/RANK_CHANGED）。EventBus 成为 ALL DomainEvent 单一真相源，
        _event_queue 纯派生视图（所有条目均经此订阅者），UIRenderer（订阅 EVENT_DOMAIN）
        自动获得全部 DomainEvent 推送。消除旧 _push_event 直写 _event_queue 双路径：4 类经
        _push_event 直写、1 类（TTL TIMEOUT）经 EventBus，现 5 类统一经 EventBus。I70：TTL
        事件 event_type 从 EXIT 改为 TIMEOUT（_run_ttl 直发），消除 EXIT+TIMEOUT 双发。
        I81：DomainEvent dataclass 为字段名/字段集单一真相源（与 I80 Signal 同构），
        asdict 派生 dict 消除硬编码字段列表 + 统一键名（"event_type"/"details" 对齐 dataclass
        字段名；原 "type"/"detail" 键废除，与 WS 路径 _event_payload 统一）。time 为运行期
        时间戳（订阅者侧填充），不在 dataclass 中（DomainEvent 发布时不携带 time）。

        Task 5：EXIT/TIMEOUT 同时持久化到 node_state（将股票标为 out），使历史状态可查询。
        """
        ev = asdict(event)
        ev["time"] = time_at(state=getattr(self._pool_engine, 'state', None))
        if event.event_type in ("EXIT", "TIMEOUT"):
            self._persist_exit_timeout(event, ev.get("time"))
        try: self._event_queue.put_nowait(ev)
        except asyncio.QueueFull: logger.warning("事件队列已满: %s %s", event.event_type, event.code)

    def _persist_exit_timeout(self, event, ts):
        """Task 5：将 EXIT/TIMEOUT 持久化到 node_state。

        pool_id 优先取事件 pool_id，details 中的 source_id/target_id 作为 node_id 回退。
        storage 不存在时静默跳过，兼容测试/无持久化环境。
        """
        storage = getattr(self, "storage", None)
        if storage is None or not hasattr(storage, "remove_stock_from_node"):
            return
        node_id = event.pool_id or ""
        if not node_id and isinstance(event.details, dict):
            node_id = event.details.get("source_id") or event.details.get("target_id") or ""
        if not node_id or not event.code:
            return
        pool_id = getattr(self, "_loop_pool_config", {}) or {}
        pool_id = pool_id.get("id") or pool_id.get("pool_id", "") if isinstance(pool_id, dict) else ""
        try:
            left_at = _dt.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S') if ts and ts > 1e8 else None
            storage.remove_stock_from_node(pool_id, node_id, event.code, left_at=left_at)
        except Exception as ex:
            logger.warning("EXIT/TIMEOUT node_state 持久化失败: %s", ex)
    def _resolve_codes(self, source_spec, ctx):
        """通用域代码解析器：按 codes_source.op 查 codes_ops 表执行。不区分 domain。

        I75：返回 [(code, code_ctx), ...] per-occurrence pairs 契约。code_ctx 为 per-occurrence
        不可变上下文（transfer→tev；diff→{'nid':N}；literal→{}），由调用方直接传入
        _skip_domain_code / _resolve_domain_ctx，消除 shared mutable _code_ctx dict。
        """
        if not source_spec:
            return []
        op = source_spec.get('op', 'literal')
        ops_table = self._edge_cfg.get('codes_ops', {})
        op_def = ops_table.get(op)
        if not op_def:
            return []
        method = getattr(self, op_def.get('method', ''), None)
        return method(source_spec, ctx) if method else []
    def _resolve_context_field(self, field_spec, domain_ctx):
        """通用事件字段解析器：按 field_spec.path 导航取值。

        路径格式：
        - ctx.x → domain_ctx.get(x)
        - stock.x.y → 从 domain_ctx['stock'] 导航属性链
        - literal.x → 返回字面字符串 x
        - 裸字符串（旧格式）→ 归一为 {path: str, default: None}，落入 fallback（I47）

        _now 统一解析：path=='_now'（旧 str 格式）或 'ctx._now'（新 dict 格式）均返回
        time_at(state=pe.state)。I47：消除原 str 分支与 ctx. 分支各一份 _now 检查的重复。

        逻辑隐于表结构（path 字段+导航规则），差异显于表内容（同字段不同取值）。
        """
        # I47：str field_spec 为 ctx 键的旧格式，归一为 dict 后统一走 path 分派
        # （str 分支与 fallback 行为等价：均 domain_ctx.get(path)，default=None）
        if isinstance(field_spec, str):
            field_spec = {'path': field_spec, 'default': None}

        path = field_spec.get('path', '')
        default = field_spec.get('default')

        if not path:
            return default

        # _now 统一解析（str '_now' 与 dict 'ctx._now' 共用，I47 消除两处重复）
        if path == '_now' or path == 'ctx._now':
            return time_at(state=getattr(self._pool_engine, 'state', None))

        # ctx.x → domain_ctx.get(x)
        if path.startswith('ctx.'):
            val = domain_ctx.get(path[4:])  # strip 'ctx.'
            return val if val is not None else default

        # stock.x.y → navigate from domain_ctx['stock']
        if path.startswith('stock.'):
            parts = path.split('.')
            stk = domain_ctx.get('stock')
            if not isinstance(stk, dict):
                return default
            val = stk
            for p in parts[1:]:  # skip 'stock'
                if isinstance(val, dict):
                    val = val.get(p)
                else:
                    val = getattr(val, p, None)
                if val is None:
                    return default
            return val

        # literal.x → return string x
        if path.startswith('literal.'):
            return path[8:]  # strip 'literal.'

        # Fallback: treat as ctx key（含归一后的旧 str 格式）
        val = domain_ctx.get(path)
        return val if val is not None else default
    def _resolve_domain_ctx(self, tpl, base_ctx, code, code_ctx):
        """按模板规则构造 _emit_domain_event 期望的 domain_ctx。

        role/cond/tracker source 统一委托 _resolve_domain_source 查 resolvers 表分派，
        消除 type if/elif 链。逻辑隐于表结构（resolvers[category][type] → method），
        差异显于表内容（同字段 type 不同取值）。

        I75：code_ctx 由调用方传入（per-occurrence pairs），不再从 base_ctx['_code_ctx']
        共享 dict 查找。消除多池共存下 last-wins 错位（EXIT/SELL pool_id 错位 + tracker 错位）。
        """
        ctx = {
            'lpc': base_ctx.get('lpc'),
            'stock_index': base_ctx.get('stock_index'),
        }

        # I72：transfer 事件自带 sid/tid/fid/mode（transfer 专属字段）。
        # nid 非 transfer 专属——pool_enter 不需要、move_exit=source_id、ttl_expire=nid，
        # 故 nid 不在此分支赋值，改由 role_source.backfill_ctx_key 表驱动回填（与 I42
        # backfill 范式同构）。消除旧 if/elif nid 分支：pool_enter nid=source_id 死赋值
        # （语义错位，pool_enter 语义节点=target_id）+ move_exit nid 与 backfill 冗余 +
        # ttl_expire elif 分支特例。3 域 nid 统一收敛到 backfill_ctx_key 声明。
        if 'source_id' in code_ctx:
            ctx['sid'] = code_ctx['source_id']
            ctx['tid'] = code_ctx['target_id']
            ctx['fid'] = code_ctx.get('flow_id', '')
            ctx['mode'] = code_ctx.get('mode', 'copy')

        # 表驱动：role/cond/tracker source 按 resolvers 表分派，消除 type if/elif
        self._resolve_domain_source('role_source', 'role_source_types', tpl, ctx, code_ctx, base_ctx, code)
        self._resolve_domain_source('cond_source', 'cond_source_types', tpl, ctx, code_ctx, base_ctx, code)
        self._resolve_domain_source('tracker_source', 'tracker_source_types', tpl, ctx, code_ctx, base_ctx, code)

        # stock 仅在入池场景用于价格 enriched
        if 'tid' in ctx:
            ctx['stock'] = base_ctx.get('stock_index', {}).get(ctx['tid'], {}).get(code)

        return ctx
    def _resolve_domain_source(self, source_key, resolver_category, tpl, ctx, code_ctx, base_ctx, code):
        """通用域源解析器：按 resolvers[category][source.type] 表查 method 反射调用。

        新增 source type 只需加 JSON 表行 + 1 个 handler 方法，零行 if/elif 改动。
        """
        source_spec = tpl.get(source_key)
        if not source_spec:
            return
        resolvers = self._event_domain_templates.get('resolvers', {})
        type_map = resolvers.get(resolver_category, {})
        source_type = source_spec.get('type', '')
        resolver_def = type_map.get(source_type)
        if not resolver_def:
            return
        method = getattr(self, resolver_def.get('method', ''), None)
        if method:
            method(source_spec, ctx, code_ctx, base_ctx, code, tpl)
    def _resolve_node_type(self, n):
        """统一节点类型解析：先查 tdx_type_map，再查 dzh_type_map。

        替代各处的内联 _rn() 闭包，保持类型解析逻辑一致。
        """
        rt = n.get('type', '')
        if isinstance(rt, int) or (isinstance(rt, str) and rt):
            k = str(rt)
        elif rt:
            k = str(n.get('dzh_cell_type', 0) or '')
        else:
            k = ''
        if not k:
            return ''
        return self._value_extractor.resolve_chain([
            {"type": "dict_key", "source": self._dzh_full.get('tdx_type_map', {}), "key": k},
            {"type": "dict_key", "source": self._dzh_type_map, "key": k},
            {"type": "literal_value", "value": k},
        ], default=k)

    # ------------------------------------------------------------------
    # 拓扑与处理计划（兼容旧 API）
    # ------------------------------------------------------------------
    def _extract_edge_endpoint(self, edge, *keys):
        """从边中提取端点ID，支持多种格式：from/to 字段、source/target 字符串、source.node_id dict"""
        for k in keys:
            v = edge.get(k, '')
            if not v:
                continue
            if isinstance(v, str) and v:
                return v
            if isinstance(v, dict):
                nid = v.get('node_id', '') or v.get('id', '')
                if nid:
                    return nid
        return ''

    def _resolve_edge_type(self, source_type):
        """查 edge_semantics.json 判断边类型（基于源节点类型）。"""
        result = self._edge_type_lookup.get(source_type)
        if result is not None:
            return result
        logger.warning("未知 source_type=%s，回退为 conditional", source_type)
        return 'conditional'

    def _resolve_edge_context(self, edge, nodes):
        """从边和节点字典中提取标准化的边上下文（9字段）。"""
        sid = self._extract_edge_endpoint(edge, 'from', 'source', 'startid')
        tid = self._extract_edge_endpoint(edge, 'to', 'target', 'endid')
        ep = edge.get('params', {}) if isinstance(edge, dict) else {}
        eid = edge.get('id', '') or edge.get('flow_id', '') if isinstance(edge, dict) else ''
        sn = nodes.get(sid, {}) if nodes else {}
        tn = nodes.get(tid, {}) if nodes else {}
        st = self._resolve_node_type(sn) if isinstance(sn, dict) else ''
        tt = self._resolve_node_type(tn) if isinstance(tn, dict) else ''
        edge_out = dict(edge) if isinstance(edge, dict) else {}
        if isinstance(edge_out, dict):
            edge_out.setdefault('id', eid)
            edge_out.setdefault('params', ep)
        return {'sid': sid, 'tid': tid, 'sn': sn, 'tn': tn,
                'st': st, 'tt': tt, 'ep': ep, 'eid': eid, 'edge': edge_out}

    def _should_emit_signal_for_domain(self, sig_rule, trigger_type, role_info):
        """通用信号触发判断（与原 _should_emit_signal 闭包逻辑一致）。"""
        trigger = sig_rule.get("trigger", {})
        if trigger.get("type") == trigger_type:
            return bool(role_info and role_info.get("is_target", False))
        conditions = trigger.get("conditions", [])
        match_policy = trigger.get("match_policy", "any")
        matched = False
        for cond in conditions:
            if cond.get("type") != trigger_type:
                continue
            if role_info and role_info.get("is_target", False):
                matched = True
            if matched and match_policy == "any":
                return True
        return matched
    def _skip_domain_code(self, tpl, code, base_ctx, code_ctx):
        """应用 mode_filter 与 skip_if_present_in 过滤 domain 代码。

        I75：code_ctx 由调用方传入（per-occurrence pairs），不再从 base_ctx['_code_ctx']
        共享 dict 查找（消除 last-wins 错位）。语义等价：单源场景 code_ctx 与旧 dict[code] 同。
        """
        mf = tpl.get('mode_filter')
        if mf and code_ctx.get(mf.get('field')) != mf.get('value'):
            return True
        skip_key = tpl.get('skip_if_present_in')
        if skip_key:
            skip_set = base_ctx.get(skip_key, set())
            if code in skip_set:
                return True
            nid = code_ctx.get('nid')
            if nid is not None and (nid, code) in skip_set:
                return True
        return False

    # ------------------------------------------------------------------
    # Domain 事件原语：codes / role / cond / tracker 解析
    # ------------------------------------------------------------------
    def _codes_transfer(self, source_spec, ctx):
        """原语：从 transfer_events 提取 (code, code_ctx) per-occurrence pairs。

        I75：per-occurrence immutable pairs 替代 shared mutable _code_ctx dict。
        旧实现 `ctx['_code_ctx'][code] = tev` 假设 code 唯一（last-wins），但多池共存
        （属性功能总表.md:828 copy 模式 _src_keep）允许同 code 在多源池被 move / 同 code
        进入多目标池 → 多 transfer_event 含同 code → last-wins 使前一 occurrence 的
        EXIT/SELL pool_id 错位（应 sid=A 实 sid=B）+ tracker 错位（应 A 的 tracker 实 B 的）。
        pairs 范式：每 occurrence 自带 code_ctx，消除共享 dict 键碰撞。与 I73 同构
        （运行层键假设与声明层语义不一致 → 键碰撞 last-wins）。
        """
        field = source_spec.get('field', 'transferred_codes')
        return [(code, tev) for tev in ctx.get('transfer_events', [])
                for code in (tev.get(field) or [])]

    def _codes_diff(self, source_spec, ctx):
        """原语：计算 prev 与 curr 的差集，返回 (code, code_ctx) per-occurrence pairs。

        I75：同 _codes_transfer，消除 _code_ctx dict last-wins。多池共存下同 code 可同 tick
        从多池 TTL 过期 → 多 occurrence 各自带 nid，旧 dict last-wins 使前一 SELL pool_id 错位。
        """
        prev = ctx.get(source_spec.get('prev', 'prev_stock_index'), {})
        curr = ctx.get(source_spec.get('curr', 'stock_index'), {})
        pairs = []
        for nid, prev_entry in prev.items():
            if isinstance(prev_entry, frozenset):
                prev_codes = prev_entry
            elif isinstance(prev_entry, dict):
                prev_codes = set(prev_entry.keys())
            else:
                prev_codes = set()
            curr_entry = curr.get(nid, {})
            curr_codes = set(curr_entry.keys()) if isinstance(curr_entry, dict) else set()
            for code in (prev_codes - curr_codes):
                pairs.append((code, {'nid': nid}))
        return pairs

    def _codes_literal(self, source_spec, ctx):
        """原语：返回 (code, {}) per-occurrence pairs。I75：与 transfer/diff 统一 pairs 契约。"""
        return [(code, {}) for code in list(source_spec.get('value', []))]

    def _resolve_pool_role(self, nid):
        """解析节点池角色：config.pool_role → psatt 规则匹配。

        I56：nid→role 缓存（_role_cache），同节点多 code 共享角色解析结果，
        将 per-code 角色解析收敛为 per-nid（类似 node_map/stock_index 预建索引）。
        缓存随 _emit_transfer_events 每 tick 重置（_role_* 系列同生命周期）。
        """
        cache = getattr(self, '_role_cache', None)
        if cache is not None and nid in cache:
            return cache[nid]
        role = self._resolve_pool_role_compute(nid)
        if cache is not None:
            cache[nid] = role
        return role

    def _resolve_pool_role_compute(self, nid):
        """nid→role 解析原逻辑（无缓存，I56 抽取自 _resolve_pool_role 保语义等价）。"""
        lpc = getattr(self, '_role_lpc', None)
        if not lpc:
            return None
        node_map = getattr(self, '_role_node_map', {})
        pool_roles = getattr(self, '_role_pool_roles', {})
        sorted_rules = getattr(self, '_role_sorted_rules', [])
        n = node_map.get(nid)
        if n is None:
            return None
        cfg_role = (n.get('config') or {}).get('pool_role')
        if cfg_role and cfg_role in pool_roles:
            return pool_roles[cfg_role]
        psatt = (n.get('params') or {}).get('tdx_psatt') or (n.get('params') or {}).get('psatt') or {}
        nt = n.get('type', '')
        rctx = {"psatt": psatt, "node_type": nt}
        for r in sorted_rules:
            handler_name = r.get("handler", "")
            h = _HR.get(handler_name)
            if h and h(r, rctx):
                return pool_roles.get(r.get("role"))
        return None

    def _build_exit_tracker_info(self, code, sid, prev_stock_index):
        """构建离开池时的 tracker 详情快照。"""
        # I73：修复声明 vs 运行分裂——prev_stock_index 值恒为 dict（_emit_transfer_events
        # line 804 从 list 构建 {_stock_code(s): s}），frozenset 守卫恒 False 导致函数恒返回 {}。
        # 守卫扩展为 (frozenset, dict)：dict 走 code in prev_entry（键集 = 股票代码集），
        # frozenset 保留为防御性回退。修复后 tracker_info 非空，SELL 信号 price/profit_pct/hold_days 可解析。
        prev_entry = prev_stock_index.get(sid)
        stk_in_prev = code in prev_entry if isinstance(prev_entry, (frozenset, dict)) else False
        if not stk_in_prev:
            return {}
        prev_dict = prev_stock_index.get(sid, {})
        stocks_for_lookup = [s for s in (prev_dict.values() if isinstance(prev_dict, dict) else [])]
        target_stock = None
        for s in stocks_for_lookup:
            if isinstance(s, dict) and _stock_code(s) == code:
                target_stock = s
                break
        if target_stock:
            return self._tracker_detail(target_stock)
        return {}

    def _tracker_detail(self, stock):
        if not isinstance(stock, dict) or (t := stock.get('_tracker')) is None:
            return {}
        return {k: round(v, 4) if isinstance(v, float) else v for k, v in t.items()}

    def _resolve_role_from_node(self, rs, ctx, code_ctx, base_ctx, code, tpl):
        """域源 handler：从节点 nid 解析池角色；按 role_source.backfill_ctx_key 回填 nid。"""
        nid = code_ctx.get(rs.get('nid_field'))
        ctx[tpl['role_ref']] = self._resolve_pool_role(nid)
        backfill_key = rs.get('backfill_ctx_key')
        if backfill_key:
            ctx[backfill_key] = nid

    def _resolve_cond_from_edge(self, cs, ctx, code_ctx, base_ctx, code, tpl):
        """域源 handler：从边条件解析转移条件字符串。"""
        fid = code_ctx.get(cs.get('fid_field'), '')
        ctx['cond'] = _lookup_edge_cond(base_ctx.get('lpc'), fid)

    def _resolve_cond_from_constant(self, cs, ctx, code_ctx, base_ctx, code, tpl):
        """域源 handler：常量条件。"""
        ctx['cond'] = cs.get('value')

    def _resolve_tracker_from_exit(self, ts, ctx, code_ctx, base_ctx, code, tpl):
        """域源 handler：从 prev_stock_index 构建离池 tracker 详情，按 tracker_source.ctx_field 选字段键。

        I60：消除 vestigial _exit_tracker_cache。该缓存被 init/clear/pop 但**从不写入**
        （Grep 全仓 0 处 _exit_tracker_cache[k]=...），.pop() 恒返回 None，cached or 恒走
        _build_exit_tracker_info 分支。设计意图（EdgeExecutor move 时写缓存→此处 pop）从未落地。
        删除死状态 + 死分支，直接构建。语义等价（cached 恒 None → cached or X ≡ X）。
        """
        nid = code_ctx.get(ts.get('nid_field'))
        tracker_info = self._build_exit_tracker_info(code, nid, base_ctx.get('prev_stock_index'))
        field_key = ts.get('ctx_field', '_tracker_info')
        ctx[field_key] = tracker_info

    # ------------------------------------------------------------------
    # 外部 API 与生命周期方法（原 _MetaEngineCompat）
    # ------------------------------------------------------------------
    def _now(self):
        """PoolEngine 时间统一入口：wall_clock 返回 ``_dt.now()``，
        virtual/sequence 委托 ``_time_source_to_now``（基于 ``time_at``）。

        ``_time_source_to_now`` 自动区分当日秒数偏移（< 1e8）与 Unix 绝对时间戳。
        """
        pe = self._pool_engine
        ts = pe.state.time_source if pe is not None else {}
        driver = ts.get('driver_type', 'wall_clock') if ts else 'wall_clock'
        if driver == 'wall_clock':
            return _dt.now()
        return _time_source_to_now(pe.state)

    def _read_config_row(self, table_name: str, key: str, default_key: str = "") -> Dict[str, Any]:
        """从已加载的配置表中读取某一行。"""
        table = self.tables.get(table_name, {})
        rows = table.get(table_name, {}) if isinstance(table, dict) else {}
        if not rows and isinstance(table, dict):
            for candidate in (table_name, table_name.rstrip("s") + "s"):
                rows = table.get(candidate, {})
                if rows:
                    break
        return rows.get(key, {}) or rows.get(default_key, {})

    def _setup_mode(self, mode_id: str, pool_config: Dict[str, Any]) -> None:
        """模式初始化：绑定时间源、数据源、交易接口，并创建/复用 PoolEngine。

        供测试与旧调用方同步使用；不启动内部循环。
        """
        self._current_mode_id = mode_id
        self._loop_pool_config = pool_config
        mode_cfg = self._runtime_modes.get(mode_id, {})
        ts_id = mode_cfg.get('time_source_id', 'realtime')
        ts_cfg = dict(self._time_sources.get(ts_id, {}))
        ts_cfg.setdefault('time_source_id', ts_id)

        # 创建/复用 PoolEngine 并把模式配置写入运行时表
        pe = self._ensure_pool_engine(pool_config)
        pe.state.set_time_source(ts_cfg)
        ds_cfg = self._read_config_row('data_sources', mode_cfg.get('data_source_id', ''))
        ti_cfg = self._trade_interfaces.get(mode_cfg.get('trade_interface_id', 'noop'), {})
        se_cfg = self._read_config_row('side_effect_scopes', mode_cfg.get('side_effects_scope', ''))
        pe.state.set_data_source(ds_cfg)
        pe.state.set_trade_interface(ti_cfg)
        pe.state.set_side_effects_scope(se_cfg)

    def set_tq_adapter(self, a):
        self.tq_adapter = a
        # 懒加载 formula_router（双引擎公式路由）
        if not hasattr(self, 'formula_router') or self.formula_router is None:
            try:
                try:
                    from .formula_module import FormulaRouter, PythonFormulaEngine
                except ImportError:
                    try:
                        from ..core.formula_module import FormulaRouter, PythonFormulaEngine
                    except ImportError:
                        from core.formula_module import FormulaRouter, PythonFormulaEngine
                # SubTask 8.5: 跨层 import 改为构造函数注入工厂
                _cache_factory = getattr(self, '_formula_cache_factory', None)
                _cache = _cache_factory() if _cache_factory is not None else None
                # 获取 HQChartProvider（从 DataSourceManager 中取）
                hqchart_provider = None
                if hasattr(a, '_manager') and a._manager:
                    providers = getattr(a._manager, '_providers', {})
                    hqchart_provider = providers.get('hqchart')
                self.formula_router = FormulaRouter(
                    hqchart_provider=hqchart_provider,
                    python_engine=PythonFormulaEngine(),
                    cache=_cache,
                    kline_provider=a,
                )
            except Exception as e:
                self.formula_router = None
                import logging as _logging
                _logging.getLogger(__name__).debug("formula_router 初始化失败: %s", e)
        # 懒加载 market_data_port（Task 10：评估器标量数据接口）
        if not hasattr(self, 'market_data_port') or self.market_data_port is None:
            try:
                # SubTask 8.5: 跨层 import 改为构造函数注入工厂
                _mdp_factory = getattr(self, '_market_data_port_factory', None)
                if _mdp_factory is not None:
                    self.market_data_port = _mdp_factory(a)
                else:
                    self.market_data_port = None
            except Exception as e:
                self.market_data_port = None
                import logging as _logging
                _logging.getLogger(__name__).debug("market_data_port 初始化失败: %s", e)

    def set_storage(self, s): self.storage = s

    def set_minute_aggregator(self, ma):
        """注入 Min1Aggregator 实例（Task 9: 实时分钟线合成器接线）。

        Args:
            ma: Min1Aggregator 实例，或 None 取消注入。
        """
        self.minute_aggregator = ma

    def set_table_engine(self, config_store, rule_engine=None, panel_generator=None, ownership_manager=None):
        """注入表驱动引擎组件（ConfigStore/RuleEngine/PanelGenerator/PropertyOwnershipManager）
        
        注入 ConfigStore 后，self.tables 将指向 ConfigStore._tables，
        实现统一数据源，避免两份配置不同步。
        """
        self._config_store = config_store
        self._rule_engine = rule_engine
        self._panel_generator = panel_generator
        self._ownership_manager = ownership_manager
        # 统一数据源：让 self.tables 指向 ConfigStore 的内存缓存
        if config_store is not None:
            self.tables = config_store._tables

    def check_hot_reload(self) -> list:
        """检测并执行配置热加载，委托给 ConfigStore.check_hot_reload()"""
        if self._config_store is None:
            return []
        changed = self._config_store.check_hot_reload()
        if changed:
            # 刷新 ConfigStore 及所有关联引擎缓存
            self._config_store.invalidate_all_caches()
            if self._rule_engine:
                self._rule_engine.invalidate_cache()
            if self._panel_generator:
                self._panel_generator.invalidate_cache()
            if self._ownership_manager:
                self._ownership_manager.invalidate_cache()
        return changed

    def get_modules(self): return self.tables.get("modules", {})

    def get_conditions(self): return {"dispatch_index": self.dispatch_index, "nset_dispatch": self._nset_dispatch}

    def get_engines(self): return self.tables.get("engines", {})

    def start_loop(self, pool_config, current_bar_data=None):
        if self._loop_running: logger.warning("已在运行中"); return self._loop_task
        self._stop_event.clear(); self._pause_event.set()
        try: loop = asyncio.get_running_loop()
        except RuntimeError:
            raise RuntimeError(
                "start_loop 必须在异步上下文中调用（例如在 async 函数内或已运行的事件循环中）。"
                "当前没有正在运行的事件循环，create_task 创建的任务将无法执行。"
                "请使用 asyncio.run() 或在已有的 async 函数中调用 start_loop。"
            )
        self._loop_task = loop.create_task(self.run_loop(pool_config, current_bar_data)); return self._loop_task

    def pause_loop(self):
        if not self._loop_running: return
        self._loop_paused = True; self._pause_event.clear(); logger.info("已暂停")

    def resume_loop(self):
        if not self._loop_paused: return
        self._loop_paused = False; self._pause_event.set(); logger.info("已恢复")

    async def stop_loop(self):
        if not self._loop_running: return
        self._stop_event.set(); self._pause_event.set()
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
            try: await self._loop_task
            except asyncio.CancelledError: pass
        self._loop_running = False; self._loop_paused = False; logger.info("已停止")

    def _run_module(self, module_type: str, inputs: Dict[str, Any]) -> Any:
        """按 module_type 调度到 modules 表注册的 handler。

        表驱动：通过 self.module_map[module_type]["handler"] 查得 handler 名，
        再到 _HR（native.builtins 注册表）找到可调用对象。

        解析顺序：
          1) 直接按 module_type 查 module_map（如 candidate_provider / condition_filter）
          2) 用 dzh_type_map 反查（202 → market_source → 进一步别名解析）
          3) 用 dzh_type_map.aliases 反查（market_source → candidate_provider）
        """
        candidates = [module_type]
        # dzh 数值类型 → 字符串类型
        rt = self._dzh_type_map.get(str(module_type))
        if rt:
            candidates.append(rt)
        # 别名反查（market_source → candidate_provider）
        aliases = self._dzh_full.get("aliases", {}) or {}
        for c in list(candidates):
            a = aliases.get(c)
            if a and a not in candidates:
                candidates.append(a)
        # tdx_aliases 也考虑
        tdx_aliases = self._dzh_full.get("tdx_aliases", {}) or {}
        for c in list(candidates):
            a = tdx_aliases.get(c)
            if a and a not in candidates:
                candidates.append(a)

        for mt in candidates:
            m = self.module_map.get(mt) or {}
            h_name = m.get("handler")
            if h_name and (h := _HR.get(h_name)):
                try:
                    return h({**inputs, "tq_adapter": self.tq_adapter, "dispatch_index": self.dispatch_index, "pool_config": inputs.get("pool_config", {})})
                except Exception as ex:
                    logger.error("执行模块 %s 出错: %s", module_type, ex, exc_info=True)
                    return {"error": str(ex), "module": module_type}
        logger.warning("_run_module 找不到 module_type=%s 的 handler，candidates=%s", module_type, candidates)
        return {"error": f"未知模块类型: {module_type}", "module": module_type, "candidates_tried": candidates}

    def _build_node_stocks(self, nodes):
        """从给定 nodes dict 构建并返回 node_stocks dict（不修改 state）。

        与 PoolEngineMixin._init_node_stocks() 区分：后者从 self.nodes 读取并写入
        self.state.node_stocks（mutate state）；本方法接受外部 nodes dict，返回
        新构建的 dict，供 KLineReplayEngine 等外部调用方使用。
        """
        ns = {}; pa = self._param_aliases
        def _rp(p, k, ak=None):
            for a in pa.get(ak, [k]) if ak else [k]:
                if (v := p.get(a)) is not None: return v
            return None
        for nid, node in nodes.items():
            # 统一类型解析：使用 _resolve_node_type 方法
            rt = self._resolve_node_type(node)
            cfg = self._node_init.get(rt)
            if cfg:
                _op = cfg.get("op", "")
                _m = self._edge_cfg.get("node_init_ops", {}).get(_op, {}).get("method", "")
                h = _HR.get(_m) if _m else None
            else:
                h = None
            if h:
                ns[nid] = h({"node": node, "tq_adapter": self.tq_adapter, "resolve_param": _rp})
            else:
                # 默认初始化：从 node.params.stocks 读取
                stocks = node.get('params', {}).get('stocks', []) if isinstance(node, dict) else []
                ns[nid] = [{'code': s['code'], 'label': s.get('label', s['code']), **{k: v for k, v in s.items() if k not in ('code', 'label')}} for s in stocks if isinstance(s, dict) and s.get('code')]
        return ns


    @staticmethod
    def _compute_formula_order(formulas):
        """基于公式 fields / depends_on 构建依赖图并拓扑排序，返回公式计算顺序。"""
        targets = list(formulas.keys())
        output_by_field = {tgt: tgt for tgt in targets}
        graph = {tgt: set() for tgt in targets}
        in_degree = {tgt: 0 for tgt in targets}

        for tgt, fspec in formulas.items():
            deps = fspec.get("depends_on") if isinstance(fspec, dict) else None
            if deps is None:
                fields = fspec.get("fields", []) if isinstance(fspec, dict) else []
                deps = [f for f in fields if f in output_by_field and f != tgt]
            else:
                deps = deps if isinstance(deps, list) else [deps]
            for dep in deps:
                dep_tgt = output_by_field.get(dep)
                if dep_tgt and dep_tgt != tgt and dep_tgt not in graph[tgt]:
                    graph[dep_tgt].add(tgt)
                    in_degree[tgt] += 1

        queue = [t for t in targets if in_degree[t] == 0]
        order = []
        while queue:
            cur = queue.pop(0)
            order.append(cur)
            for nxt in list(graph[cur]):
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    queue.append(nxt)

        if len(order) != len(targets):
            logger.warning("tracker 公式存在循环依赖，回退到字典顺序")
            return targets
        return order

    # ------------------------------------------------------------------
    # PoolState / EdgeState 写透视图（第 14 轮：移除本地 fallback）
    # 这些属性不保存数据，只作为 EdgeState.exec_ctx / formula_results 的视图。
    # ------------------------------------------------------------------
    class _ExecCtxView:
        __slots__ = ("_engine", "_field")

        def __init__(self, engine: "PoolEngine", field: str) -> None:
            self._engine = engine
            self._field = field

        def _ctx(self) -> Dict[str, Dict[str, Any]]:
            pe = self._engine._pool_engine
            return pe.state.exec_ctx if pe is not None else {}

        def __getitem__(self, eid: str) -> Any:
            return self._ctx().get(eid, {}).get(self._field)

        def __setitem__(self, eid: str, value: Any) -> None:
            ctx = self._ctx()
            if eid not in ctx:
                ctx[eid] = {"count": 0, "first_fire": None, "last_fire": None, "fired": False}
            ctx[eid][self._field] = value

        def get(self, eid: str, default: Any = None) -> Any:
            return self._ctx().get(eid, {}).get(self._field, default)

        def __contains__(self, eid: object) -> bool:
            return eid in self._ctx()

        def __iter__(self):
            return iter(self._ctx())

        def __len__(self) -> int:
            return len(self._ctx())

        def keys(self):
            return self._ctx().keys()

        def values(self):
            return (c.get(self._field) for c in self._ctx().values())

        def items(self):
            return ((eid, c.get(self._field)) for eid, c in self._ctx().items())

        def clear(self) -> None:
            default = 0 if self._field == "count" else None
            for c in self._ctx().values():
                c[self._field] = default

    @property
    def _flow_exec_counts(self) -> "PoolEngine._ExecCtxView":
        return self._ExecCtxView(self, "count")

    @_flow_exec_counts.setter
    def _flow_exec_counts(self, value: Any) -> None:
        self._ExecCtxView(self, "count").clear()

    @property
    def _flow_first_fire_ts(self) -> "PoolEngine._ExecCtxView":
        return self._ExecCtxView(self, "first_fire")

    @_flow_first_fire_ts.setter
    def _flow_first_fire_ts(self, value: Any) -> None:
        self._ExecCtxView(self, "first_fire").clear()

    # I28：删除 _flow_last_fire_ts 死属性（零生产读取——edge_executor.py:879 直接读 exec_ctx["last_fire"]，
    # 不经此视图；仅 test_backward_compatibility + conftest 引用，已同步清理）
    # I29：删除 _FilterCacheView 类 + _filter_cache property（68+10 行死代码，零生产读取——
    # 生产公式缓存经 formula.py:134 直接读 state.formula_results，不经此视图；
    # 仅测试 isinstance/dict() 检查 + setter 重置引用，已同步清理）

    def __init__(self, config_dir=None, *,
                 pool_config: Optional[Dict[str, Any]] = None,
                 bus: Optional['EventBus'] = None,
                 pool_validator: Optional[Callable] = None,
                 data_query_factory: Optional[Callable] = None,
                 formula_cache_factory: Optional[Callable] = None,
                 market_data_port_factory: Optional[Callable] = None,
                 subscribe_data_changed: bool = False):
        # Task 24: 合并 MetaEngine + PoolEngine 后的统一构造函数。
        # pool_config：若提供则立即初始化池运行时（_init_pool_runtime）；
        #              若不提供则延迟初始化（等 run_pool/_tick 等方法触发）。
        # bus：注入共享 EventBus 实例；若不注入则 _init_pool_runtime 内部新建。
        # subscribe_data_changed：是否订阅 DataChanged 事件触发核心循环（默认关闭，
        #                        避免与 ExecutionModule._on_data_changed 双重触发）。
        self._injected_bus: Optional['EventBus'] = bus
        # SubTask 8.5: 跨层依赖改为构造函数注入工厂/可调用对象（替代 services.* import）
        self._pool_validator = pool_validator
        self._data_query_factory = data_query_factory
        self._formula_cache_factory = formula_cache_factory
        self._market_data_port_factory = market_data_port_factory
        path = Path(config_dir) if config_dir else _CFG; self._meta_dir = Path(__file__).parent.parent
        # SubTask 27.14: 配置文件分类到子目录后需递归扫描；与 ConfigStore._iter_config_files
        # 保持一致：跳过 _archived/ 与 .locks.json，避免重复加载。
        _excluded_stems = {"api_routes", "ui_layouts", "field_definitions"}
        self.tables = {
            p.stem: json.load(open(p, encoding="utf-8"))
            for p in path.rglob("*.json")
            if p.stem not in _excluded_stems
            and "_archived" not in p.parts
            and p.name != ".locks.json"
        }
        # I92：消除 defaults.json 双重加载——已在 self.tables（ConfigStore glob）中，
        # 原 _load_defaults() 重复加载创建类内双重真相源 + 独立 _defaults_cache。
        self._defaults = self.tables.get("defaults", {})
        # I89：消除双重加载——以下 4 表已在 self.tables（ConfigStore glob）中加载，
        # 原 _lj() 直接文件加载创建同一类内双重真相源，统一到 ConfigStore 单一引用。
        self._timing_cfg = self.tables.get("timing") or {}
        self._psatt_cfg = self.tables.get("tdx_psatt") or {}
        dzh = self.tables.get("dzh_type_map") or {}
        self._dzh_type_map = dzh.get('type_map', {}); self._dzh_full = dzh
        _mv = lambda d: d if isinstance(d, list) else d.values(); _tbl = lambda k: self.tables[k].get(k, self.tables[k])
        self.module_map = {m["id"]: m for m in _mv(_tbl("modules"))}
        self._edge_cfg = self.tables.get("edge_strategies", {}); self._edge_strategies = self._edge_cfg.get("strategies", {}); self._node_init = self._edge_cfg.get("node_init", {}); self._event_domain_templates = self._edge_cfg.get("event_domain_templates", {})
        self._edge_semantics_cfg = self.tables.get("edge_semantics") or {}
        # Build edge type reverse lookup: source_type → edge_type（边类型由源节点决定，见 edge_semantics.json v3）
        self._edge_type_lookup = {}
        for etype, ecfg in self._edge_semantics_cfg.get('edge_types', {}).items():
            for st in ecfg.get('source_types', []):
                self._edge_type_lookup[st] = etype
        self._param_aliases = self.tables.get("defaults", {}).get("parameter_aliases", {})
        self._protected_fields = frozenset(self.tables.get('defaults', {}).get('protected_fields', ['indate', 'intime', 'inprice', '_tracker', 'code', 'label']))
        # tracker 公式求值改用 ast 受控求值（_eval_derived_ast），安全函数 max/min/abs/round
        # 由 evaluators._DERIVED_FUNCS 表提供，不再需要 _safe_funcs 注入 eval 上下文
        self.events = []; self.tq_adapter = None  # I30：删除 highlight_events + _highlight_listeners 死属性对（零生产读取）
        self.formula_router = None; self.market_data_port = None; self.minute_aggregator = None
        dd = self.tables.get("dispatch", {}); self.dispatch_index = {}
        for k, e in (dd.get("dispatch_rules", {}).items() if isinstance(dd, dict) else [(x.get("condition_type"), x) for x in dd] if isinstance(dd, list) else []):
            e = dict(e)
            if isinstance(e.get("bit_mask"), str) and e["bit_mask"]: e["bit_mask"] = int(e["bit_mask"], 16)
            self.dispatch_index[k] = e
        self._nset_dispatch = self.tables.get("dispatch", {}).get("nset_dispatch", {})
        self._event_queue = asyncio.Queue(); self._signal_queue = asyncio.Queue(); self._loop_running = False; self._loop_paused = False; self._event_bus = None  # I34: PoolEngine.__init__ 注入，供 _on_signal_event 订阅收敛
        self._pause_event = asyncio.Event(); self._pause_event.set(); self._stop_event = asyncio.Event(); self._loop_task = None; self._loop_pool_config = None
        self._sim_init_lock = asyncio.Lock()  # Task 14：串行化仿真会话 heavyweight 初始化
        self.node_stocks: Dict[str, List[Any]] = {}
        self._trackers: Dict = {}
        self._tick_interval = self._timing_cfg.get("tick_interval", 1)
        self._tracker_schema = self.tables.get("tracker_schema", {}); self._tracker_fields = self._tracker_schema.get("fields", {}); self._tracker_formulas = self._tracker_schema.get("formulas", {}); self._event_rules = self.tables.get("event_rules", {})
        self._signal_rules = self.tables.get("signal_rules", {}); self._pool_roles = self.tables.get("pool_roles", {})
        self._data_config = self.tables.get("data_config", {}); self._price_fields = self.tables.get("price_fields", {})
        # 初始化 DataQuery 与 FormulaRouter（公式路由在 engine 层统一接入，不回落到 tq_adapter）
        # SubTask 8.5: 跨层 import 改为构造函数注入工厂
        self._data_query = None
        if self._data_query_factory is not None:
            try:
                self._data_query = self._data_query_factory()
            except Exception as e:
                logger.warning("DataQuery 初始化失败: %s", e)
        try:
            try:
                from .formula_module import FormulaRouter
            except ImportError:
                from core.formula_module import FormulaRouter
            self.formula_router = FormulaRouter(data_query=self._data_query)
        except Exception as e:
            logger.warning("FormulaRouter 初始化失败: %s", e)
            self.formula_router = None
        self._market_cfg = (self._data_config.get('market_code_prefixes', ["SH", "SZ", "BJ"]), self._data_config.get('market_code_suffixes', [".SH", ".SZ", ".BJ"]))
        # I29：删除 _data_cache 死属性（LRUCache 初始化 6 行，零生产读取——
        # 仅 test_backward_compatibility test_data_cache_attr hasattr 检查引用，已同步清理）
        self._pk_rankings: Dict = {}; self._angle_results: Dict = {}; self._dashboard_data: Dict = {}; self._alert_events: list = []; self._alert_queue = asyncio.Queue(); self._alert_cooldown: Dict = {}
        # Task 6: 迁移后新核心引擎实例（延迟创建）
        self._pool_engine: Optional['PoolEngine'] = None
        self._post_tick_pipeline = self.tables.get("post_tick_pipeline", {}).get("pipeline", [])
        self._runtime_modes = self.tables.get("runtime_modes", {}).get("modes", {}); self._time_sources = self.tables.get("time_sources", {}).get("time_sources", {}); self._trade_interfaces = self.tables.get("trade_interfaces", {}).get("trade_interfaces", {})
        # 拓扑模式识别器（配置化）
        self._topology_matcher = TopologyPatternMatcher()
        # tracker 公式计算顺序：从 tracker_schema.json 的 depends_on / fields 动态拓扑排序
        self._formula_order = self._compute_formula_order(self._tracker_formulas)
        # 备选池刷新管理器（延迟初始化，需要 resolver）
        self.refresh_manager = None  # type: Optional[Any]
        self._resolver = None  # type: Optional[Any]
        # tracker 公式由 CompiledExpression（本模块顶部）缓存 AST 并用 ast 受控求值
        # 表驱动：当前运行模式 ID（Task 13/15/16 由 _current_mode_id 驱动）
        self._current_mode_id = 'live'
        # TTL helper：独立类处理 DZH/TDX TTL 过期逻辑
        # 依赖注入：PoolState 类由本组装层注入 TTLHelper，避免 execution_module
        # 跨模块 import runtime_mode_module（模块零引用约束）。
        from .runtime_mode_module import PoolState as _PoolStateCls
        self._ttl = TTLHelper(self._psatt_cfg, self._defaults, self._now,
                              pool_state_cls=_PoolStateCls)
        # ValueExtractor helper：表驱动值提取与路径导航
        self._value_extractor = ValueExtractor(self.tables, self)
        # Task 24: 若提供 pool_config 则立即初始化池运行时
        if pool_config is not None:
            self._init_pool_runtime(pool_config, subscribe_data_changed)

    def _attach_ui_layer(self, pe: 'PoolEngine') -> None:
        """初始化 SnapshotBuilder（core 层内部组件），绑定到 PoolEngine 的 EventBus。

        Web UI 层组件（UIRenderer / WebSocketPublisher）属于 web 层，
        应由外部入口层（app.py）在 core 层之外创建并通过属性赋值注入，
        core 层不直接依赖 web 包。
        """
        from .monitoring_module import _SnapshotBuilder
        self.snapshot_builder = _SnapshotBuilder(pe.event_bus, nodes=pe.nodes)
        self.ui_renderer = None
        self.ws_publisher = None

    def _refresh_bar_data(self, mode_cfg, current_bar_data):
        """兼容保留：行情刷新 handler。

        Task 1 后核心循环 ``run_loop`` 统一通过注入的 ``TickSource`` 获取 tick，
        不再主动调用本方法；保留以兼容外部显式刷新行情的场景。
        """
        handler_name = mode_cfg.get('refresh_handler', 'noop_refresh')
        handler = getattr(_pipeline, handler_name, None)
        if not handler:
            return current_bar_data
        return handler(mode_cfg, current_bar_data, engine=self) or current_bar_data

    def _is_trading_time(self) -> bool:
        """表驱动：判断当前是否为交易时间（Task 6）。"""
        cal = self._timing_cfg.get('market_calendar', {})
        n = self._now()
        cs = _hms_to_seconds(n.hour, n.minute, n.second)
        sessions = cal.get('sessions', [])
        if sessions:
            return any(s.get('open_sec', 0) <= cs <= s.get('close_sec', 0) for s in sessions)
        _tc = self._defaults.get('trading_calendar', {})
        _o = cal.get('open_sec', _tc.get('open_sec')); _c = cal.get('close_sec', _tc.get('close_sec'))
        return _o is not None and _c is not None and _o <= cs <= _c
