"""Monitoring 模块：事件驱动的监控记录 + 浮窗 + 看盘面板 + 告警 + 交易统计。

合并原 ``core/event_panel.py`` + ``core/snapshot_builder.py`` + dashboard/alerts
为统一 ``MonitoringModule`` 类。仅与 EventBus 交互，订阅所有事件类型，构建
节点股票快照、事件列表、看盘面板、告警，发布 ``SnapshotUpdated`` /
``EventLogged`` / ``AlertRaised`` 事件。

向后兼容：原 ``EventPanel`` / ``SnapshotBuilder`` 已合并为本模块内的私有
``_EventPanel`` / ``_SnapshotBuilder`` 类；公共方法签名不变，仍可被其他模块直接
导入使用（迁移期）。本模块内部组合这两个组件实例（不暴露给外部），由
MonitoringModule 统一注册新事件类型订阅；旧组件仍订阅旧事件类型
（DataChanged/DomainEvent/Executed）以兼容存量 UI 渲染路径。

SubTask 28.2：原 ``core/statistics_module.py`` 已合并入本文件，作为
``StatisticsModule`` 类内联（见文件末尾「统计模块层」段）。本文件现作为
监控 + 统计的单一入口文件；``from core.statistics_module import StatisticsModule``
引用须改为 ``from core.monitoring_module import StatisticsModule``。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Callable, ClassVar, Dict, List, Optional, Set, Tuple

from core.event_bus import (
    _BaseModule,
    _event_handler,
    BarComposed,
    DataChanged,
    DomainEvent,
    EVENT_DATA_CHANGED,
    EVENT_DOMAIN,
    EVENT_EXECUTED,
    EVENT_SIGNAL,
    EventBus,
    Executed,
    is_event_bus,
    PositionUpdated,
    RankingChanged,
    Signal,
    SnapshotUpdated,
    StatisticsUpdated,
)
from core.domain import get_global_config_store

logger = logging.getLogger(__name__)


def _get_table(name: str) -> Dict[str, Any]:
    """通过 ConfigStore.get_table 加载配置表（Task 9.7/9.8 统一入口）。

    替代 MonitoringModule/StatisticsModule._load_json。name 为表名 stem（不含路径和 .json）。
    """
    cs = get_global_config_store()
    return cs.get_table(name) if cs else {}


def _import_stock_code():
    """惰性导入 _stock_code，避免 _EventPanel 单独导入时强依赖 core.domain 子包。"""
    try:
        from core.domain import _stock_code
    except ImportError:
        from domain import _stock_code  # type: ignore[no-redef]
    return _stock_code

# 浮窗事件列表最大长度（避免内存无限增长）
_MAX_EVENT_LIST = 1000

# 快照中携带的最近事件数量
_SNAPSHOT_EVENT_LIMIT = 100

# 默认告警冷却时间（秒）
_DEFAULT_ALERT_COOLDOWN_SEC = 60

# EventPanel 浮窗内部事件记录最大长度（原 event_panel.py 的 _MAX_EVENTS）
_MAX_PANEL_EVENTS = 500


# === 原子组件：_EventPanel（合并自 event_panel.py） ===


class _EventPanel:
    """事件浮窗：订阅 EventBus，收集事件记录供 UI 渲染。"""

    def __init__(self, bus: Optional[Any] = None, event_driver: Optional[Any] = None) -> None:
        self.bus = bus
        self._event_driver = event_driver
        self._events: List[Dict[str, Any]] = []
        self._pending: List[Dict[str, Any]] = []
        self._enabled = False

    def subscribe(self) -> None:
        if self._enabled or not is_event_bus(self.bus):
            return
        self.bus.subscribe_any(self._on_any_event)
        self._enabled = True

    @_event_handler("_on_any_event")
    def _on_any_event(self, event) -> None:
        """统一事件处理：查表转换 + 加入事件列表。"""
        record = event_to_record(event)
        self._append(record)

    def _append(self, record: Dict[str, Any]) -> None:
        # 统一记录格式：顶层包含 event_type / code / node_id / timestamp / details
        normalized = {
            "event_type": record.get("event_type", "UNKNOWN"),
            "code": record.get("code", ""),
            "node_id": record.get("node_id") or record.get("pool_id", ""),
            "pool_id": record.get("pool_id", ""),
            "timestamp": record.get("timestamp") or record.get("ts", 0.0),
            "details": record.get("details", {}),
        }
        self._events.append(normalized)
        self._pending.append(normalized)
        if len(self._events) > _MAX_PANEL_EVENTS:
            del self._events[0]
        if len(self._pending) > _MAX_PANEL_EVENTS:
            del self._pending[0]

    def get_events(self) -> List[Dict[str, Any]]:
        return list(self._events)

    def get_pending(self, clear: bool = False) -> List[Dict[str, Any]]:
        """返回未排队事件列表；clear=True 时清空内部 pending 缓存。"""
        snapshot = list(self._pending)
        if clear:
            self._pending.clear()
        return snapshot

    def clear_pending(self) -> None:
        """清空未排队事件缓存。"""
        self._pending.clear()

    def get_pending_specs(self) -> List[Dict[str, Any]]:
        if self._event_driver is None:
            return []
        specs = []
        for entry in getattr(self._event_driver, '_heap', []):
            fire_time = entry[0] if isinstance(entry, (list, tuple)) and len(entry) >= 2 else 0
            spec = entry[2] if isinstance(entry, (list, tuple)) and len(entry) >= 3 else None
            params = getattr(spec, 'params', {}) if spec else {}
            specs.append({
                "edge_id": params.get("eid", ''),
                "pool_id": params.get("tgt", ''),
                "code": params.get("code", ''),
                "kind": params.get("kind", ''),
                "next_fire_time": fire_time,
                "interval": getattr(spec, 'interval', None) if spec else None,
            })
        return specs

    def clear(self) -> None:
        self._events.clear()
        self._pending.clear()


# === 原子组件：_SnapshotBuilder（合并自 snapshot_builder.py） ===


class _SnapshotBuilder:
    """通过订阅 EventBus 事件累积节点股票的物化视图。

    属性（实例级，≤ 5）:
      - _view:  ``{nid: {codes: set, count: int, meta: dict}}``
      - _data_meta: 最新行情/Bar 数据，按 code 索引
      - _lock: 线程安全锁

    方法（≤ 6）:
      - __init__
      - on_executed
      - on_data_changed
      - on_domain_event
      - snapshot
      - get_node

    订阅事件（≤ 3）:
      - Executed
      - DataChanged
      - DomainEvent (仅 TIMEOUT，I70：_run_ttl 改发 TIMEOUT 替代 EXIT)
    """

    def __init__(
        self,
        event_bus: EventBus,
        nodes: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        self._view: Dict[str, Dict[str, Any]] = {}
        self._data_meta: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

        if nodes:
            # 惰性导入 _stock_code：仅当传入 nodes 时才触发 core.domain.tick_source 加载
            _stock_code = _import_stock_code()
            for nid, node in nodes.items():
                meta: Dict[str, Any] = {}
                initial_codes: Set[str] = set()
                if isinstance(node, dict):
                    meta["type"] = node.get("type", "")
                    meta["name"] = node.get("name", "") or node.get("label", "")
                    for stock in node.get("params", {}).get("stocks", []):
                        if stock is None:
                            continue
                        code = _stock_code(stock)
                        if code:
                            initial_codes.add(code)
                self._view[nid] = {
                    "codes": initial_codes,
                    "count": len(initial_codes),
                    "meta": meta,
                }

        event_bus.subscribe(EVENT_EXECUTED, self.on_executed)
        event_bus.subscribe(EVENT_DATA_CHANGED, self.on_data_changed)
        event_bus.subscribe(EVENT_DOMAIN, self.on_domain_event)

    def on_executed(self, event: Executed) -> None:
        """``Executed`` 事件处理器：更新源节点与目标节点股票集合。

        I69：``target_cleared`` 携带被覆盖出目标池的代码（overwrite 模式），
        必须从 target view 移除——否则真相源已 REPLACE 但 view 只 ADD
        不 DISCARD，view 累积陈旧代码（view drift）。
        """
        with self._lock:
            target = self._view.setdefault(
                event.tid, {"codes": set(), "count": 0, "meta": {}}
            )
            for code in event.target_cleared:
                target["codes"].discard(code)
            for code in event.entered:
                target["codes"].add(code)
            target["count"] = len(target["codes"])
            target["meta"]["last_executed_eid"] = event.eid
            target["meta"]["last_executed_sid"] = event.sid

            # ``exited`` 表示从源节点移除的股票（move 模式）
            if event.exited and event.sid in self._view:
                source = self._view[event.sid]
                for code in event.exited:
                    source["codes"].discard(code)
                source["count"] = len(source["codes"])

    def on_data_changed(self, event: DataChanged) -> None:
        """``DataChanged`` 事件处理器：更新各 code 的最新数据元信息。"""
        payload: Dict[str, Any] = {
            "ts": event.ts,
            "bar_hash": event.bar_hash,
            "source": event.source,
            "period": event.period,
        }
        if event.data is not None:
            payload["data"] = event.data
        with self._lock:
            for code in event.codes:
                self._data_meta[code] = payload

    def on_domain_event(self, event: DomainEvent) -> None:
        """``DomainEvent(TIMEOUT)`` 处理器：TTL 淘汰出池同步到 view。

        I70：``_run_ttl`` 发布 ``DomainEvent(TIMEOUT, reason=TTL_EXPIRED, pool_id=tgt)``
        per removed code（event_type 从 EXIT 改为 TIMEOUT，与 event_rules.json 语义
        对齐：EXIT=move 出池，TIMEOUT=TTL 超时）。SnapshotBuilder 必须据此从 view
        移除，否则 view 累积陈旧代码——与 overwrite ``target_cleared`` 同构的 view
        drift（真相源 REPLACE vs view 缺失 DISCARD）。

        仅处理 TIMEOUT 事件：move_exit 的 ``DomainEvent(EXIT, reason=move_exit)``
        已由 ``Executed.exited`` 在 ``on_executed`` 中处理（discard from source view），
        此处按 event_type 过滤避免重复/误删。TIMEOUT 唯一由 _run_ttl 直发
        （I70：event_rules.json 删除 TIMEOUT event def，ttl_expire 域仅发 SELL 信号）。
        """
        if event.event_type != "TIMEOUT":
            return
        with self._lock:
            node = self._view.get(event.pool_id)
            if node is not None:
                node["codes"].discard(event.code)
                node["count"] = len(node["codes"])

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        """返回完整物化视图：``{nid: {codes, count, meta}}``。"""
        with self._lock:
            result: Dict[str, Dict[str, Any]] = {}
            for nid, view in self._view.items():
                codes = list(view["codes"])
                meta = dict(view["meta"])
                meta["codes_data"] = {
                    code: self._data_meta[code]
                    for code in codes
                    if code in self._data_meta
                }
                result[nid] = {
                    "codes": codes,
                    "count": len(codes),
                    "meta": meta,
                }
            return result

    def get_node(self, nid: str) -> Dict[str, Any]:
        """返回单个节点视图；若不存在则返回空视图。"""
        with self._lock:
            view = self._view.get(nid)
            if view is None:
                return {"codes": [], "count": 0, "meta": {}}
            codes = list(view["codes"])
            meta = dict(view["meta"])
            meta["codes_data"] = {
                code: self._data_meta[code]
                for code in codes
                if code in self._data_meta
            }
            return {
                "codes": codes,
                "count": len(codes),
                "meta": meta,
            }


# === 事件记录适配器（表驱动，无 if/elif 链）===


def _truncate_codes(codes) -> str:
    """合并 4 处 codes 截断显示（前 5 + 省略号）。"""
    codes = list(codes) if codes else []
    return ",".join(codes[:5]) + ("..." if len(codes) > 5 else "")


def _envelope(event_type: str, ts, code: str = "", details: Optional[dict] = None, **extra) -> dict:
    """统一事件记录 envelope 构造器（合并 ~15 处重复骨架）。"""
    record: dict = {"event_type": event_type, "ts": ts}
    if code:
        record["code"] = code
    if details is not None:
        record["details"] = details
    if extra:
        record.update(extra)
    return record


def _payload_adapter(event_type: str, payload_src, ts, payload_fields: List[Tuple[str, str]], code_key: str = "code") -> dict:
    """合并 OrderPlaced/OrderFilled/AlertRaised 的 ``dict(event.X or {})`` + ``.get`` 提取骨架。"""
    payload = dict(payload_src or {})
    code = str(payload.get(code_key, "") or "")
    details = {dk: payload.get(pk, 0 if dk in ("qty", "price") else "") for dk, pk in payload_fields}
    return _envelope(event_type, ts, code=code, details=details)


def _tick_record(event):
    code = event.code or ""
    tick_data = event.tick_data or {}
    if not code and isinstance(tick_data, dict):
        code = tick_data.get("code", "") or tick_data.get("symbol", "")
    details = {
        "price": tick_data.get("price", 0) if isinstance(tick_data, dict) else 0,
        "volume": tick_data.get("volume", 0) if isinstance(tick_data, dict) else 0,
    }
    return _envelope("TickReceived", event.ts, code=code, details=details)


def _formula_record(event):
    result = event.result
    result_str = ""
    if isinstance(result, (int, float, bool)):
        result_str = str(result)
    elif isinstance(result, (list, tuple)):
        result_str = f"len={len(result)}"
    ts = event.ts if hasattr(event, 'ts') and event.ts else time.time()
    details = {"formula": event.formula_ref, "result": result_str}
    return _envelope("FormulaEvaluated", ts, code=event.code, details=details)


def _executed_record(event):
    d = event.details if isinstance(event.details, dict) else {}
    codes = list(event.entered) if event.entered else []
    details = {
        "sid": event.sid, "tid": event.tid, "mode": event.mode,
        "entered": len(event.entered or []),
        "exited": len(event.exited or []),
        "target_cleared": len(event.target_cleared or []),
    }
    return _envelope(
        "Executed", d.get("timestamp", time.time()), code=_truncate_codes(codes),
        node_id=event.tid, pool_id=event.tid, edge_id=event.eid, details=details,
    )


def _alert_record(event):
    alert = event.alert or {}
    code = str(alert.get("code", "") or "")
    details = {
        "rule": str(alert.get("rule_id", "") or ""),
        "severity": alert.get("severity", ""),
        "message": alert.get("message", ""),
    }
    return _envelope("AlertRaised", float(event.ts or 0.0), code=code, details=details)


def _position_record(event):
    tracker = event.tracker or {}
    node_id = str(tracker.get("node_id", "") or "")
    code = str(tracker.get("code", "") or "")
    details = {
        "qty": tracker.get("qty", 0),
        "entry_price": tracker.get("entry_price", 0),
        "pnl": round(float(tracker.get("pnl", 0) or 0), 2),
    }
    return _envelope(
        "PositionUpdated", event.ts, code=code,
        node_id=node_id, pool_id=node_id, details=details,
    )


def _stats_record(event):
    stats = dict(event.stats or {})
    details = {
        "total_pnl": round(float(stats.get("total_pnl", 0) or 0), 2),
        "trade_count": stats.get("trade_count", 0),
        "win_rate": round(float(stats.get("win_rate", 0) or 0), 1),
    }
    return _envelope("StatisticsUpdated", event.ts, details=details)


# _ADAPTER_SPECS: event_type_name -> build callable (event -> record dict | None)
_ADAPTER_SPECS: Dict[str, Callable[[Any], Optional[dict]]] = {
    "TickReceived": _tick_record,
    "DataChanged": lambda e: _envelope("DataChanged", e.ts, code=_truncate_codes(list(e.codes) if e.codes else []), details={"source": e.source or "", "period": e.period or "", "count": len(list(e.codes) if e.codes else [])}),
    "BarComposed": lambda e: _envelope("BarComposed", e.ts, code=e.code, details={"period": e.period, "close": ((e.bar or {}).get("close", 0) if isinstance(e.bar or {}, dict) else 0)}),
    "FormulaEvaluated": _formula_record,
    "StockFiltered": lambda e: _envelope("StockFiltered", time.time(), edge_id=e.eid, details={"passed": len(e.passed), "rejected": len(e.rejected), "filter": e.filter_ref}),
    "TimeAdvanced": lambda e: _envelope("TimeAdvanced", e.ts, details={"source": e.source or ""}),
    "SnapshotUpdated": lambda e: _envelope("SnapshotUpdated", e.ts, details={"nodes": (len(e.snapshot.get("node_snapshots", {})) if isinstance(e.snapshot, dict) else 0)}),
    "EventLogged": lambda e: None,
    "Executed": _executed_record,
    "DomainEvent": lambda e: _envelope(e.event_type, time.time(), code=e.code, node_id=e.pool_id, pool_id=e.pool_id, details=(dict(e.details) if isinstance(e.details, dict) else {})),
    "PoolLoaded": lambda e: _envelope("PoolLoaded", time.time(), details={"format": e.source_format}),
    "ConfigLoaded": lambda e: _envelope("ConfigLoaded", time.time(), details={"tables": (len(e.config_tables) if isinstance(e.config_tables, dict) else 0)}),
    "ConfigChanged": lambda e: _envelope("ConfigChanged", time.time(), details={"changed": e.changed_tables or []}),
    "TransferExecuted": lambda e: _envelope("TransferExecuted", e.ts, code=_truncate_codes(list(e.codes) if e.codes else []), node_id=e.tgt, pool_id=e.tgt, details={"src": e.src, "tgt": e.tgt, "mode": e.mode, "count": len(list(e.codes) if e.codes else [])}),
    "TTLExpired": lambda e: _envelope("TTLExpired", e.ts, code=_truncate_codes(list(e.codes) if e.codes else []), node_id=e.node_id, pool_id=e.node_id, details={"node": e.node_id, "count": len(list(e.codes) if e.codes else [])}),
    "OrderPlaced": lambda e: _payload_adapter("OrderPlaced", e.order, e.ts, [("side", "side"), ("qty", "qty"), ("price", "price")]),
    "OrderFilled": lambda e: _payload_adapter("OrderFilled", e.fill, e.ts, [("side", "side"), ("qty", "qty"), ("price", "price")]),
    "AlertRaised": _alert_record,
    "PositionUpdated": _position_record,
    "StatisticsUpdated": _stats_record,
    "RankingChanged": lambda e: _envelope("RankingChanged", e.ts, details={"dimension": e.dimension}),
    "EdgeFired": lambda e: _envelope("EdgeFired", e.ts, edge_id=e.eid, eid=e.eid, code="", details={}),
    "Signal": lambda e: _envelope(e.signal_type, e.ts, code=e.code, node_id=e.pool_id, pool_id=e.pool_id, details={"signal_type": e.signal_type, "price": e.price, "quantity": e.quantity, "condition": e.condition, "profit_pct": e.profit_pct, "hold_days": e.hold_days}),
    "CrossOverDetected": lambda e: _envelope("CrossOverDetected", e.ts, code=e.code, details={"cross_type": e.cross_type, "formula_ref": e.formula_ref}),
    "ModeChanged": lambda e: _envelope("ModeChanged", float(time.time()), details={"mode_id": e.mode_id, "prev_mode": e.prev_mode}),
}


def _default_adapter(event):
    """默认 adapter：提取通用字段。"""
    return {
        "ts": getattr(event, 'ts', 0) or 0,
        "event_type": type(event).__name__,
        "code": getattr(event, 'code', '') or '',
        "details": dict(event.details) if isinstance(getattr(event, 'details', None), dict) else {},
    }


def _build_adapter_record(spec_key: str, event) -> Optional[dict]:
    """通用 adapter builder——查 _ADAPTER_SPECS 表按 spec_key 分派构建 record。"""
    spec = _ADAPTER_SPECS.get(spec_key)
    if spec is None:
        return _default_adapter(event)
    return spec(event)


def event_to_record(event):
    """将事件对象转换为监控记录 dict。表驱动查 _ADAPTER_SPECS。"""
    return _build_adapter_record(type(event).__name__, event)


class MonitoringModule:
    """Monitoring 模块：监控记录 + 浮窗 + 看盘面板 + 告警。仅与 EventBus 交互。"""

    def __init__(self, bus: EventBus, config: Optional[Dict[str, Any]] = None) -> None:
        self._bus = bus
        self._config = config or {}
        # 加载配置表（Task 9.7: 通过 ConfigStore.get_table 加载，参与热加载）
        self._dashboard_schema = _get_table("dashboard_schema")
        self._alert_rules = self._normalize_alert_rules(
            _get_table("alert_rules")
        )
        # 持有原 2 个组件实例（不暴露给外部）
        self._event_panel = _EventPanel(bus=bus)
        self._snapshot_builder = _SnapshotBuilder(
            bus, nodes=self._config.get("nodes"),
        )
        # 浮窗事件列表（按时间排序，限长 _MAX_EVENT_LIST）
        self._event_list: List[Dict[str, Any]] = []
        # 未排队事件列表
        self._pending_events: List[Dict[str, Any]] = []
        # 节点股票快照 {node_id: set(code)}
        self._node_snapshots: Dict[str, Any] = {}
        # 告警冷却时间表 {(rule_id, code): last_ts}
        self._alert_cooldown: Dict[Tuple[str, str], float] = {}
        # 看盘面板数据
        self._dashboard_data: Dict[str, Any] = {}
        # 注册所有事件类型订阅
        self.subscribe(self._bus)

    # === 初始化辅助 ===
    # Task 9.7: _load_json 已删除，统一改用模块级 _get_table()（通过 ConfigStore.get_table）

    @staticmethod
    def _normalize_alert_rules(raw: Dict[str, Any]) -> Dict[str, Any]:
        """将 alert_rules.json 的 {"rules": {...}} 结构扁平化为 {rule_id: rule}。"""
        if not raw:
            return {}
        rules = raw.get("rules", raw)
        if not isinstance(rules, dict):
            return {}
        return {
            rule_id: rule
            for rule_id, rule in rules.items()
            if isinstance(rule, dict)
        }

    def subscribe(self, bus) -> None:
        """订阅所有事件，统一通过 _on_any_event 处理。"""
        if not is_event_bus(bus):
            return
        bus.subscribe_any(self._on_any_event)

    @_event_handler("_on_any_event")
    def _on_any_event(self, event) -> None:
        """统一事件处理：查表转换 + 加入事件列表。"""
        record = event_to_record(event)
        if record:
            self._add_to_event_list(record)

    # === 浮窗事件列表管理 ===

    def _add_to_event_list(self, event_dict: Dict[str, Any]) -> None:
        """添加事件到浮窗列表（按时间排序，限长 _MAX_EVENT_LIST）。"""
        from datetime import datetime
        
        ts = event_dict.get("ts", 0.0) or event_dict.get("timestamp", 0.0) or time.time()
        if isinstance(ts, (int, float)) and ts > 0:
            try:
                time_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S")
            except (ValueError, OSError):
                time_str = datetime.now().strftime("%H:%M:%S")
                ts = time.time()
        else:
            time_str = datetime.now().strftime("%H:%M:%S")
            ts = time.time()
        
        normalized = {
            "event_type": event_dict.get("event_type", "UNKNOWN"),
            "code": str(event_dict.get("code", "") or ""),
            "node_id": str(event_dict.get("node_id", "") or event_dict.get("pool_id", "") or ""),
            "edge_id": str(event_dict.get("edge_id", "") or event_dict.get("eid", "") or ""),
            "pool_id": str(event_dict.get("pool_id", "") or ""),
            "details": event_dict.get("details", {}),
            "time": time_str,
            "timestamp": float(ts),
        }
        
        self._event_list.append(normalized)
        self._pending_events.append(normalized)
        if len(self._event_list) > _MAX_EVENT_LIST:
            self._event_list = self._event_list[-_MAX_EVENT_LIST:]
        if len(self._pending_events) > _MAX_EVENT_LIST:
            self._pending_events = self._pending_events[-_MAX_EVENT_LIST:]

    def _update_snapshot(self, ts: float) -> None:
        """构建并发布快照。"""
        try:
            ts_val = float(ts or 0.0)
            snapshot = {
                "events": list(self._event_list[-_SNAPSHOT_EVENT_LIMIT:]),
                "dashboard": dict(self._dashboard_data),
                "node_snapshots": {
                    k: list(v) if isinstance(v, set) else list(v)
                    for k, v in self._node_snapshots.items()
                },
                "ts": ts_val,
            }
            self._bus.publish(SnapshotUpdated(snapshot=snapshot, ts=ts_val))
        except Exception as ex:
            logger.warning("MonitoringModule _update_snapshot failed: %s", ex)

    # === 向后兼容：暴露原组件的查询方法 ===

    def get_events(self) -> List[Dict[str, Any]]:
        """返回浮窗事件列表（向后兼容 EventPanel.get_events）。"""
        return list(self._event_list)

    def get_pending(self, clear: bool = False) -> List[Dict[str, Any]]:
        """返回未排队事件列表；clear=True 时清空内部 pending 缓存。"""
        snapshot = list(self._pending_events)
        if clear:
            self._pending_events.clear()
        return snapshot

    def clear_pending(self) -> None:
        """清空未排队事件缓存。"""
        self._pending_events.clear()

    def get_node_snapshot(self, node_id: str) -> List[str]:
        """返回指定节点的股票代码列表。"""
        node = self._node_snapshots.get(node_id)
        return list(node) if isinstance(node, set) else []

    def get_dashboard_data(self) -> Dict[str, Any]:
        """返回看盘面板数据。"""
        return dict(self._dashboard_data)


# === 统计模块层（自 core/statistics_module.py 合并）===


# 5 种收益分析类型 → 统一入口 _compute_pnl_metric(metric_name)（表驱动，无 if/elif 链）
# key 与 analysis_config.json 的 analysis_types 对齐，metric_name 即 _PNL_METRIC_SPECS 的 key
_ANALYSIS_HANDLERS: Dict[str, str] = {
    "intraday": "_compute_pnl_metric",
    "market_impact": "_compute_pnl_metric",
    "historical": "_compute_pnl_metric",
    "distribution": "_compute_pnl_metric",
    "positioning": "_compute_pnl_metric",
}

# 5 种收益分析指标 spec（filter/extract/agg/key，统一由 _compute_pnl_metric 分派）
# filter(t): 筛选 tracker；extract(t): 提取值（market_impact 返回 (sector, pnl) 元组）；
# agg(self, values): 聚合（historical 读 self._stats，无需 tracker）；key: 单一返回键，None 表示 agg 已返回完整 dict。
_PNL_METRIC_SPECS: Dict[str, Dict[str, Any]] = {
    "intraday": {"filter": lambda t: int(t.get("qty", 0) or 0) > 0, "extract": lambda t: float(t.get("pnl", 0.0) or 0.0), "agg": lambda self, v: sum(v), "key": "unrealized_pnl"},
    "market_impact": {"filter": lambda t: int(t.get("qty", 0) or 0) > 0, "extract": lambda t: (str(t.get("market", "") or t.get("sector", "") or "default"), float(t.get("pnl", 0.0) or 0.0)), "agg": lambda self, v: {k: sum(p for kk, p in v if kk == k) for k, _ in v}, "key": "by_sector"},
    "historical": {"filter": lambda t: False, "extract": lambda t: 0.0, "agg": lambda self, v: float(self._stats["total_pnl"]), "key": "total_realized_pnl"},
    "distribution": {"filter": lambda t: True, "extract": lambda t: float(t.get("pnl", 0.0) or 0.0), "agg": lambda self, v: {"max": max(v) if v else 0.0, "min": min(v) if v else 0.0, "avg": sum(v) / len(v) if v else 0.0}, "key": None},
    "positioning": {"filter": lambda t: int(t.get("qty", 0) or 0) > 0, "extract": lambda t: int(t.get("qty", 0) or 0) * float(t.get("cur_price", 0.0) or 0.0), "agg": lambda self, v: {"active_positions": len(v), "total_market_value": sum(v)}, "key": None},
}

# 多分析角度维度 → 排序键 lambda（表驱动，无 if/elif 链）
# key 与 analysis_config.json 的 angles 对齐（动量/趋势/价值）
# item = ((node_id, code), tracker_dict)；3 个角度仅字段提取与变换不同。
_ANGLE_SORT_KEYS: Dict[str, Callable[[Tuple[Tuple[str, str], Dict[str, Any]]], float]] = {
    "momentum": lambda item: 0.0 if (entry := float(item[1].get("entry_price", 0.0) or 0.0)) <= 0 else float(item[1].get("pnl", 0.0) or 0.0) / entry,
    "trend": lambda item: float(item[1].get("pnl", 0.0) or 0.0),
    "value": lambda item: -float(item[1].get("entry_price", 0.0) or 0.0),
}


# PK 排名 / 多分析角度 builder——由 _compute_ranking 共享骨架调用（candidates 已过滤）
def _build_pk_ranking(candidates: List[Any]) -> Dict[str, List[Any]]:
    ordered = sorted(candidates, key=lambda x: float(x[1].get("pnl", 0.0) or 0.0), reverse=True)
    return {"by_pnl": [{"code": k[1], "node_id": k[0], "pnl": float(t.get("pnl", 0.0) or 0.0), "rank": i + 1} for i, (k, t) in enumerate(ordered)]}


def _build_analysis_angles(candidates: List[Any]) -> Dict[str, List[Any]]:
    return {angle: [{"code": k[1], "node_id": k[0], "rank": i + 1} for i, (k, _t) in enumerate(sorted(candidates, key=key_fn, reverse=True))] for angle, key_fn in _ANGLE_SORT_KEYS.items()}


# _RANKING_SPECS: (cfg_attr, store_attr, builder, label, dimension)
# 合并 compute_pk_ranking / compute_analysis_angles 共享骨架（cfg 空检查 → 过滤候选 →
# builder → 存回 → 异常日志返回 {}），publish_rankings 迭代本表逐项发布 RankingChanged。
_RANKING_SPECS: List[Tuple[str, str, Callable[[List[Any]], Dict[str, Any]], str, str]] = [
    ("_pk_cfg", "_pk_rankings", _build_pk_ranking, "compute_pk_ranking", "pk"),
    ("_analysis_cfg", "_angle_results", _build_analysis_angles, "compute_analysis_angles", "analysis_angles"),
]


class StatisticsModule(_BaseModule):
    """Statistics 模块：交易统计 + 收益分析 + PK 排名 + 多分析角度。仅与 EventBus 交互。"""

    def __init__(self, bus: EventBus, config: Optional[Dict[str, Any]] = None) -> None:
        self._bus = bus
        self._config = config or {}
        # 加载配置表（Task 9.8: 通过 ConfigStore.get_table 加载，参与热加载）
        self._pk_cfg = _get_table("pk_config")
        self._analysis_cfg = _get_table("analysis_config")
        # 持仓跟踪表镜像（从 PositionUpdated 事件维护）
        self._trackers: Dict[Tuple[str, str], Dict[str, Any]] = {}
        # 累计统计
        self._stats: Dict[str, Any] = {
            "start_ts": 0.0,
            "total_pnl": 0.0,
            "total_invested": 0.0,
            "max_invested": 0.0,
            "trade_count": 0,
            "win_count": 0,
            "loss_count": 0,
        }
        # PK 排名结果
        self._pk_rankings: Dict[str, List[Any]] = {}
        # 多分析角度结果
        self._angle_results: Dict[str, List[Any]] = {}
        self.register_subscribers()

    _SUBSCRIPTIONS: ClassVar[List[Tuple[type, str]]] = [
        (PositionUpdated, "_on_position_updated"),
        (BarComposed, "_on_bar_composed"),
        (StatisticsUpdated, "_on_statistics_updated"),
    ]

    @_event_handler("_on_statistics_updated")
    def _on_statistics_updated(self, event: StatisticsUpdated) -> None:
        """统计更新触发排名发布（SubTask 19.6）。

        ``_on_position_updated`` / ``_on_bar_composed`` 计算完统计后发布
        ``StatisticsUpdated``，本 handler 订阅该事件并调用 ``publish_rankings``
        发布 ``RankingChanged``（PK 排名 + 多分析角度），补全事件驱动链路。
        """
        self.publish_rankings(ts=event.ts)

    # Task 9.8: _load_json 已删除，统一改用模块级 _get_table()（通过 ConfigStore.get_table）

    # === SubTask 10.2: 订阅事件计算交易统计 + 5 种收益分析 ===

    @_event_handler("_on_position_updated")
    def _on_position_updated(self, event: PositionUpdated) -> None:
        """持仓更新触发统计计算。"""
        tracker = event.tracker or {}
        key = (tracker.get("node_id", ""), tracker.get("code", ""))
        self._trackers[key] = tracker
        # 首次更新时记录起始时间戳（用于运行天数计算）
        if not self._stats["start_ts"]:
            self._stats["start_ts"] = float(event.ts or time.time())
        # 累计统计
        self._stats["trade_count"] += 1
        pnl = float(tracker.get("pnl", 0.0) or 0.0)
        self._stats["total_pnl"] += pnl
        qty = int(tracker.get("qty", 0) or 0)
        if qty > 0:
            invested = qty * float(tracker.get("entry_price", 0.0) or 0.0)
            self._stats["total_invested"] += invested
            self._stats["max_invested"] = max(self._stats["max_invested"], invested)
        if pnl > 0:
            self._stats["win_count"] += 1
        elif pnl < 0:
            self._stats["loss_count"] += 1
        # 发布 StatisticsUpdated 事件
        self._bus.publish(StatisticsUpdated(stats=self._compute_full_stats(), ts=event.ts))

    @_event_handler("_on_bar_composed")
    def _on_bar_composed(self, event: BarComposed) -> None:
        """K 线合成触发未实现盈亏更新。"""
        code = event.code
        close = float((event.bar or {}).get("close", 0.0) or 0.0)
        if not code or close <= 0:
            return
        # 更新持仓的 cur_price 与 pnl
        for key, tracker in self._trackers.items():
            if tracker.get("code") != code:
                continue
            if int(tracker.get("qty", 0) or 0) <= 0:
                continue
            entry = float(tracker.get("entry_price", 0.0) or 0.0)
            tracker["cur_price"] = close
            if entry > 0:
                tracker["pnl"] = (close - entry) * int(tracker.get("qty", 0) or 0)
        # 重新计算并发布统计
        self._bus.publish(StatisticsUpdated(stats=self._compute_full_stats(), ts=event.ts))

    def _compute_full_stats(self) -> Dict[str, Any]:
        """计算完整统计指标 + 5 种收益分析。"""
        cur_ts = time.time()
        start_ts = float(self._stats.get("start_ts", 0.0) or 0.0)
        days = max(1.0, (cur_ts - start_ts) / 86400.0) if start_ts else 1.0
        total_pnl = float(self._stats["total_pnl"])
        total_invested = float(self._stats["total_invested"])
        trade_count = int(self._stats["trade_count"])
        max_invested = float(self._stats["max_invested"])
        win_count = int(self._stats["win_count"])
        loss_count = int(self._stats["loss_count"])

        # 5 种收益分析（表驱动分派，无 if/elif 链）
        analyses: Dict[str, Any] = {}
        for name, method_name in _ANALYSIS_HANDLERS.items():
            handler = getattr(self, method_name, None)
            if not callable(handler):
                analyses[name] = {}
                continue
            try:
                analyses[name] = handler(name)
            except Exception as ex:
                logger.warning("StatisticsModule %s failed: %s", method_name, ex)
                analyses[name] = {}

        return {
            "running_days": days,
            "total_pnl": total_pnl,
            "total_return_pct": (total_pnl / total_invested * 100.0) if total_invested else 0.0,
            "avg_return_pct": (total_pnl / trade_count * 100.0) if trade_count else 0.0,
            "daily_return_pct": (total_pnl / days / total_invested * 100.0) if (total_invested and days) else 0.0,
            "max_invested": max_invested,
            "avg_invested": (total_invested / trade_count) if trade_count else 0.0,
            "trade_count": trade_count,
            "win_rate": (win_count / trade_count * 100.0) if trade_count else 0.0,
            "win_count": win_count,
            "loss_count": loss_count,
            # 5 种收益分析（key 与 analysis_config.json 的 analysis_types 对齐）
            "intraday_pnl": analyses.get("intraday", {}),
            "market_impact_pnl": analyses.get("market_impact", {}),
            "historical_pnl": analyses.get("historical", {}),
            "distribution_pnl": analyses.get("distribution", {}),
            "positioning_pnl": analyses.get("positioning", {}),
        }

    # === 收益分析指标计算（表驱动，见模块级 _PNL_METRIC_SPECS）===

    def _compute_pnl_metric(self, metric_name: str) -> Dict[str, Any]:
        """统一收益分析指标计算：查表 → filter trackers → extract → agg → return {key: result}。"""
        spec = _PNL_METRIC_SPECS[metric_name]
        values = [spec["extract"](t) for t in self._trackers.values() if spec["filter"](t)]
        result = spec["agg"](self, values)
        return result if spec["key"] is None else {spec["key"]: result}

    # === SubTask 10.3: PK 排名 + 多分析角度（表驱动，见模块级 _RANKING_SPECS）===

    def _compute_ranking(self, spec: Tuple[str, str, Callable[[List[Any]], Dict[str, Any]], str, str]) -> Dict[str, Any]:
        """排名计算共享骨架——合并 compute_pk_ranking / compute_analysis_angles。"""
        cfg_attr, store_attr, builder, label, _dimension = spec
        if not getattr(self, cfg_attr):
            return {}
        try:
            candidates = [
                (k, t) for k, t in self._trackers.items()
                if int(t.get("qty", 0) or 0) > 0
            ]
            result = builder(candidates)
            setattr(self, store_attr, result)
            return result
        except Exception as ex:
            logger.warning("StatisticsModule %s failed: %s", label, ex)
            return {}

    def compute_pk_ranking(self) -> Dict[str, List[Any]]:
        """执行 PK 排名（pk_config.json，按持仓 pnl 降序）。"""
        return self._compute_ranking(_RANKING_SPECS[0])

    def compute_analysis_angles(self) -> Dict[str, List[Any]]:
        """执行多分析角度（analysis_config.json 动量/趋势/价值）。"""
        return self._compute_ranking(_RANKING_SPECS[1])

    def publish_rankings(self, ts: float = 0.0) -> None:
        """发布排名变化事件——迭代 _RANKING_SPECS 表逐项发布 RankingChanged。"""
        for cfg_attr, store_attr, builder, label, dimension in _RANKING_SPECS:
            try:
                rankings = self._compute_ranking((cfg_attr, store_attr, builder, label, dimension))
                self._bus.publish(RankingChanged(rankings=rankings, dimension=dimension, ts=ts))
            except Exception as ex:
                logger.warning("StatisticsModule publish %s failed: %s", label, ex)


__all__ = ["MonitoringModule", "StatisticsModule"]
