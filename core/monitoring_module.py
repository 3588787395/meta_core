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
from typing import Any, Dict, List, Optional, Set, Tuple

from core.event_bus import (
    AlertRaised,
    BarComposed,
    ConfigChanged,
    ConfigLoaded,
    CrossOverDetected,
    DataChanged,
    DomainEvent,
    EdgeFired,
    EVENT_DATA_CHANGED,
    EVENT_DOMAIN,
    EVENT_EXECUTED,
    EVENT_SIGNAL,
    EventBus,
    EventLogged,
    Executed,
    FormulaEvaluated,
    is_event_bus,
    ModeChanged,
    OrderFilled,
    OrderPlaced,
    PoolLoaded,
    PositionUpdated,
    RankingChanged,
    Signal,
    SnapshotUpdated,
    StatisticsUpdated,
    StockFiltered,
    TickReceived,
    TimeAdvanced,
    TransferExecuted,
    TTLExpired,
)

logger = logging.getLogger(__name__)


def _import_stock_code():
    """惰性导入 _stock_code，避免 _EventPanel 单独导入时强依赖 core.domain 子包。

    原 ``snapshot_builder.py`` 在模块顶部 import _stock_code；合并后为避免
    ``_EventPanel`` 被单独导入时连带触发 ``core.domain.__init__`` →
    ``core.domain.evaluators`` 的重量级初始化，改为在 _SnapshotBuilder 内部按需导入。
    """
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
    """事件浮窗：订阅 EventBus，收集事件记录供 UI 渲染。

    属性（≤ 5）:
      - bus: EventBus
      - _events: 事件记录列表（maxlen=500）
      - _pending_specs: 待触发 TimedEventSpec 引用
      - _enabled: 是否已订阅
    """

    def __init__(self, bus: Optional[Any] = None, event_driver: Optional[Any] = None) -> None:
        self.bus = bus
        self._event_driver = event_driver
        self._events: List[Dict[str, Any]] = []
        self._pending: List[Dict[str, Any]] = []
        self._enabled = False

    def subscribe(self) -> None:
        if self._enabled or not is_event_bus(self.bus):
            return
        self.bus.subscribe(EVENT_DATA_CHANGED, self._on_data_changed)
        self.bus.subscribe(EVENT_DOMAIN, self._on_domain_event)
        self.bus.subscribe(EVENT_EXECUTED, self._on_executed)
        self.bus.subscribe(EVENT_SIGNAL, self._on_signal)
        self._enabled = True

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

    def _on_data_changed(self, event: DataChanged) -> None:
        self._append({
            "ts": event.ts if hasattr(event, 'ts') else 0,
            "event_type": "DataChanged",
            "details": {
                "source": event.source,
                "codes": list(event.codes) if event.codes else [],
                "period": event.period or "",
            },
        })

    def _on_domain_event(self, event: DomainEvent) -> None:
        # DomainEvent 直接对应 ENTER/EXIT/TIMEOUT/RANK_CHANGED 等业务事件
        self._append({
            "timestamp": event.ts if hasattr(event, 'ts') else 0,
            "event_type": event.event_type,
            "code": event.code,
            "node_id": event.pool_id,
            "pool_id": event.pool_id,
            "details": dict(event.details) if isinstance(event.details, dict) else {},
        })

    def _on_executed(self, event: Executed) -> None:
        d = event.details if isinstance(event.details, dict) else {}
        self._append({
            "timestamp": d.get("timestamp", 0),
            "event_type": "Executed",
            "code": "",
            "node_id": event.tid,
            "pool_id": event.tid,
            "details": {
                "edge_id": event.eid,
                "sid": event.sid,
                "tid": event.tid,
                "entered": list(event.entered) if event.entered else [],
                "exited": list(event.exited) if event.exited else [],
                "actions": d.get("actions", []),
            },
        })

    def _on_signal(self, event: Signal) -> None:
        # Signal 的 signal_type 为 BUY/SELL，直接作为顶层 event_type
        self._append({
            "timestamp": event.ts,
            "event_type": event.signal_type,
            "code": event.code,
            "node_id": event.pool_id,
            "pool_id": event.pool_id,
            "details": {
                "signal_type": event.signal_type,
                "price": event.price,
                "quantity": event.quantity,
                "condition": event.condition,
                "profit_pct": event.profit_pct,
                "hold_days": event.hold_days,
            },
        })

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
        for spec in getattr(self._event_driver, '_specs', []):
            specs.append({
                "edge_id": getattr(spec, 'edge_id', ''),
                "next_fire_time": getattr(spec, 'next_fire_time', 0),
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


# 表驱动：事件类型 → handler 方法名（无 if/elif 链）
_EVENT_HANDLERS: Dict[type, str] = {
    TickReceived: "_on_tick_received",
    DataChanged: "_on_data_changed_monitor",
    BarComposed: "_on_bar_composed",
    FormulaEvaluated: "_on_formula_evaluated",
    StockFiltered: "_on_stock_filtered",
    EdgeFired: "_on_edge_fired",
    Executed: "_on_executed_monitor",
    TransferExecuted: "_on_transfer_executed",
    DomainEvent: "_on_domain_event_monitor",
    TTLExpired: "_on_ttl_expired",
    OrderPlaced: "_on_order_placed",
    OrderFilled: "_on_order_filled",
    PositionUpdated: "_on_position_updated",
    Signal: "_on_signal",
    StatisticsUpdated: "_on_statistics_updated",
    RankingChanged: "_on_ranking_changed",
    AlertRaised: "_on_alert_raised",
    CrossOverDetected: "_on_crossover_detected",
    ModeChanged: "_on_mode_changed",
    TimeAdvanced: "_on_time_advanced",
    PoolLoaded: "_on_pool_loaded",
    ConfigLoaded: "_on_config_loaded",
    ConfigChanged: "_on_config_changed",
    SnapshotUpdated: "_on_snapshot_updated",
    EventLogged: "_on_event_logged",
}

# 表驱动：排名维度 → 看盘面板字段（无 if/elif 链）
_RANKING_DIMENSION_FIELDS: Dict[str, str] = {
    "pk": "pk_rankings",
    "analysis_angles": "analysis_angles",
}


class MonitoringModule:
    """Monitoring 模块：监控记录 + 浮窗 + 看盘面板 + 告警。仅与 EventBus 交互。

    订阅所有事件类型，构建节点股票快照、事件列表、看盘面板、告警，
    发布 SnapshotUpdated/EventLogged/AlertRaised 事件。

    属性（实例级，≤ 5）:
      - _bus: EventBus
      - _config: 配置 dict
      - _dashboard_schema / _alert_rules: 看盘面板/告警规则配置表
      - _event_panel / _snapshot_builder: 原 2 个组件实例（向后兼容，不暴露）
      - _event_list / _pending_events: 浮窗事件列表 / 未排队事件列表
      - _node_snapshots: 节点股票快照
      - _alert_cooldown: 告警冷却时间表
      - _dashboard_data: 看盘面板数据
    """

    def __init__(self, bus: EventBus, config: Optional[Dict[str, Any]] = None) -> None:
        self._bus = bus
        self._config = config or {}
        # 加载配置表
        self._dashboard_schema = self._load_json("config/ui/dashboard_schema.json")
        self._alert_rules = self._normalize_alert_rules(
            self._load_json("config/alert_rules.json")
        )
        # 持有原 2 个组件实例（不暴露给外部）
        # EventPanel 不调用 subscribe()，避免与 MonitoringModule 的 Signal 订阅重复；
        # SnapshotBuilder 在构造函数中自动订阅旧事件类型（Executed/DataChanged/DomainEvent），
        # 用于兼容存量 UI 渲染路径
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
        self._register_subscribers()

    # === 初始化辅助 ===

    @staticmethod
    def _load_json(rel_path: str) -> Dict[str, Any]:
        """加载 JSON 配置表（基于工作目录解析相对路径）。"""
        try:
            full = rel_path if os.path.isabs(rel_path) else os.path.join(os.getcwd(), rel_path)
            with open(full, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError, OSError) as ex:
            logger.warning("MonitoringModule 加载配置表失败 %s: %s", rel_path, ex)
            return {}

    @staticmethod
    def _normalize_alert_rules(raw: Dict[str, Any]) -> Dict[str, Any]:
        """将 alert_rules.json 的 {"rules": {...}} 结构扁平化为 {rule_id: rule}。

        使 ``self._alert_rules.get(rule_id, {})`` 直接命中规则体，
        与 ``alert.get("rule_id")`` 对齐。
        """
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

    def _register_subscribers(self) -> None:
        """注册所有事件类型订阅（表驱动，无 if/elif 链）。"""
        for event_cls, handler_name in _EVENT_HANDLERS.items():
            handler = getattr(self, handler_name, None)
            if callable(handler):
                self._bus.subscribe(event_cls, handler)

    # === 浮窗事件列表管理 ===

    def _add_to_event_list(self, event_dict: Dict[str, Any]) -> None:
        """添加事件到浮窗列表（按时间排序，限长 _MAX_EVENT_LIST）。
        
        统一事件格式：
        {
            "event_type": str,
            "code": str,
            "node_id": str,
            "edge_id": str,
            "pool_id": str,
            "details": dict,
            "time": str (HH:MM:SS),
            "timestamp": float,
        }
        """
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

    # === SubTask 11.2: 订阅事件构建浮窗/面板/告警 ===

    def _on_tick_received(self, event: TickReceived) -> None:
        """Tick接收事件加入浮窗。"""
        try:
            code = event.code or ""
            tick_data = event.tick_data or {}
            if not code and isinstance(tick_data, dict):
                code = tick_data.get("code", "") or tick_data.get("symbol", "")
            self._add_to_event_list({
                "event_type": "TickReceived",
                "code": code,
                "ts": event.ts,
                "details": {
                    "price": tick_data.get("price", 0) if isinstance(tick_data, dict) else 0,
                    "volume": tick_data.get("volume", 0) if isinstance(tick_data, dict) else 0,
                },
            })
        except Exception as ex:
            logger.warning("MonitoringModule _on_tick_received failed: %s", ex)

    def _on_data_changed_monitor(self, event: DataChanged) -> None:
        """数据变更事件加入浮窗。"""
        try:
            codes = list(event.codes) if event.codes else []
            code_str = ",".join(codes[:5]) + ("..." if len(codes) > 5 else "")
            self._add_to_event_list({
                "event_type": "DataChanged",
                "code": code_str,
                "ts": event.ts,
                "details": {
                    "source": event.source or "",
                    "period": event.period or "",
                    "count": len(codes),
                },
            })
        except Exception as ex:
            logger.warning("MonitoringModule _on_data_changed_monitor failed: %s", ex)

    def _on_bar_composed(self, event: BarComposed) -> None:
        """K线合成事件加入浮窗。"""
        try:
            bar = event.bar or {}
            self._add_to_event_list({
                "event_type": "BarComposed",
                "code": event.code,
                "ts": event.ts,
                "details": {
                    "period": event.period,
                    "close": bar.get("close", 0) if isinstance(bar, dict) else 0,
                },
            })
            # BarComposed也需要通知StatisticsModule更新（已在StatisticsModule单独订阅）
        except Exception as ex:
            logger.warning("MonitoringModule _on_bar_composed failed: %s", ex)

    def _on_formula_evaluated(self, event: FormulaEvaluated) -> None:
        """公式求值完成事件加入浮窗。"""
        try:
            result = event.result
            result_str = ""
            if isinstance(result, (int, float, bool)):
                result_str = str(result)
            elif isinstance(result, (list, tuple)):
                result_str = f"len={len(result)}"
            ts = event.ts if hasattr(event, 'ts') and event.ts else time.time()
            self._add_to_event_list({
                "event_type": "FormulaEvaluated",
                "code": event.code,
                "ts": ts,
                "details": {
                    "formula": event.formula_ref,
                    "result": result_str,
                },
            })
        except Exception as ex:
            logger.warning("MonitoringModule _on_formula_evaluated failed: %s", ex)

    def _on_stock_filtered(self, event: StockFiltered) -> None:
        """股票过滤事件加入浮窗。"""
        try:
            self._add_to_event_list({
                "event_type": "StockFiltered",
                "edge_id": event.eid,
                "ts": time.time(),
                "details": {
                    "passed": len(event.passed),
                    "rejected": len(event.rejected),
                    "filter": event.filter_ref,
                },
            })
        except Exception as ex:
            logger.warning("MonitoringModule _on_stock_filtered failed: %s", ex)

    def _on_time_advanced(self, event: TimeAdvanced) -> None:
        """时间推进事件加入浮窗。"""
        try:
            self._add_to_event_list({
                "event_type": "TimeAdvanced",
                "ts": event.ts,
                "details": {
                    "source": event.source or "",
                },
            })
            self._update_snapshot(event.ts)
        except Exception as ex:
            logger.warning("MonitoringModule _on_time_advanced failed: %s", ex)

    def _on_snapshot_updated(self, event: SnapshotUpdated) -> None:
        """快照更新事件，仅记录不发布（避免循环）。"""
        try:
            self._add_to_event_list({
                "event_type": "SnapshotUpdated",
                "ts": event.ts,
                "details": {
                    "nodes": len(event.snapshot.get("node_snapshots", {})) if isinstance(event.snapshot, dict) else 0,
                },
            })
        except Exception as ex:
            logger.warning("MonitoringModule _on_snapshot_updated failed: %s", ex)

    def _on_event_logged(self, event: EventLogged) -> None:
        """事件日志事件，不需要重复记录到浮窗（避免循环）。"""
        pass

    def _on_executed_monitor(self, event: Executed) -> None:
        """Executed事件（股票转移执行）加入浮窗 + 持久化 + 更新节点快照。"""
        try:
            d = event.details if isinstance(event.details, dict) else {}
            codes = list(event.entered) if event.entered else []
            code_str = ",".join(codes[:5]) + ("..." if len(codes) > 5 else "")
            self._add_to_event_list({
                "event_type": "Executed",
                "code": code_str,
                "node_id": event.tid,
                "pool_id": event.tid,
                "edge_id": event.eid,
                "ts": d.get("timestamp", time.time()),
                "details": {
                    "sid": event.sid,
                    "tid": event.tid,
                    "mode": event.mode,
                    "entered": len(event.entered or []),
                    "exited": len(event.exited or []),
                    "target_cleared": len(event.target_cleared or []),
                },
            })
            self._bus.publish(EventLogged(
                event={
                    "kind": "transfer",
                    "eid": event.eid,
                    "sid": event.sid,
                    "tid": event.tid,
                    "entered": list(event.entered or []),
                    "exited": list(event.exited or []),
                    "mode": event.mode,
                },
                event_kind="executed",
                ts=d.get("timestamp", time.time()),
            ))
            self._update_snapshot(d.get("timestamp", time.time()))
        except Exception as ex:
            logger.warning("MonitoringModule _on_executed_monitor failed: %s", ex)

    def _on_domain_event_monitor(self, event: DomainEvent) -> None:
        """DomainEvent事件（ENTER/EXIT/TIMEOUT/RANK_CHANGED）加入浮窗。"""
        try:
            self._add_to_event_list({
                "event_type": event.event_type,
                "code": event.code,
                "node_id": event.pool_id,
                "pool_id": event.pool_id,
                "ts": time.time(),
                "details": dict(event.details) if isinstance(event.details, dict) else {},
            })
        except Exception as ex:
            logger.warning("MonitoringModule _on_domain_event_monitor failed: %s", ex)

    def _on_pool_loaded(self, event: PoolLoaded) -> None:
        """PoolLoaded事件加入浮窗。"""
        try:
            self._add_to_event_list({
                "event_type": "PoolLoaded",
                "ts": time.time(),
                "details": {
                    "format": event.source_format,
                },
            })
        except Exception as ex:
            logger.warning("MonitoringModule _on_pool_loaded failed: %s", ex)

    def _on_config_loaded(self, event: ConfigLoaded) -> None:
        """ConfigLoaded事件加入浮窗。"""
        try:
            self._add_to_event_list({
                "event_type": "ConfigLoaded",
                "ts": time.time(),
                "details": {
                    "tables": len(event.config_tables) if isinstance(event.config_tables, dict) else 0,
                },
            })
        except Exception as ex:
            logger.warning("MonitoringModule _on_config_loaded failed: %s", ex)

    def _on_config_changed(self, event: ConfigChanged) -> None:
        """ConfigChanged事件加入浮窗。"""
        try:
            self._add_to_event_list({
                "event_type": "ConfigChanged",
                "ts": time.time(),
                "details": {
                    "changed": event.changed_tables if event.changed_tables else [],
                },
            })
        except Exception as ex:
            logger.warning("MonitoringModule _on_config_changed failed: %s", ex)

    def _on_transfer_executed(self, event: TransferExecuted) -> None:
        """股票流转事件加入浮窗 + 持久化 + 更新节点快照。"""
        try:
            codes = list(event.codes) if event.codes else []
            code_str = ",".join(codes[:5]) + ("..." if len(codes) > 5 else "")
            self._add_to_event_list({
                "event_type": "TransferExecuted",
                "code": code_str,
                "node_id": event.tgt,
                "pool_id": event.tgt,
                "ts": event.ts,
                "details": {
                    "src": event.src,
                    "tgt": event.tgt,
                    "mode": event.mode,
                    "count": len(codes),
                },
            })
            # 发布 EventLogged 事件供 Database 持久化
            self._bus.publish(EventLogged(
                event={
                    "kind": "transfer",
                    "src": event.src, "tgt": event.tgt,
                    "codes": codes, "mode": event.mode,
                },
                event_kind="transfer_executed",
                ts=event.ts,
            ))
            # 更新节点股票快照
            self._apply_transfer_to_snapshot(event)
            self._update_snapshot(event.ts)
        except Exception as ex:
            logger.warning("MonitoringModule _on_transfer_executed failed: %s", ex)

    def _on_ttl_expired(self, event: TTLExpired) -> None:
        """TTL 超时事件加入浮窗 + 持久化 + 从节点快照移除。"""
        try:
            codes = list(event.codes) if event.codes else []
            code_str = ",".join(codes[:5]) + ("..." if len(codes) > 5 else "")
            self._add_to_event_list({
                "event_type": "TTLExpired",
                "code": code_str,
                "node_id": event.node_id,
                "pool_id": event.node_id,
                "ts": event.ts,
                "details": {
                    "node": event.node_id,
                    "count": len(codes),
                },
            })
            self._bus.publish(EventLogged(
                event={
                    "kind": "ttl_expired", "node_id": event.node_id, "codes": codes,
                },
                event_kind="ttl_expired",
                ts=event.ts,
            ))
            # 从节点快照移除超时代码
            node = self._node_snapshots.get(event.node_id)
            if isinstance(node, set):
                for code in codes:
                    node.discard(code)
            self._update_snapshot(event.ts)
        except Exception as ex:
            logger.warning("MonitoringModule _on_ttl_expired failed: %s", ex)

    def _on_order_placed(self, event: OrderPlaced) -> None:
        """订单提交事件加入浮窗 + 持久化。"""
        try:
            order = dict(event.order or {})
            code = order.get("code", "")
            self._add_to_event_list({
                "event_type": "OrderPlaced",
                "code": code,
                "ts": event.ts,
                "details": {
                    "side": order.get("side", ""),
                    "qty": order.get("qty", 0),
                    "price": order.get("price", 0),
                },
            })
            self._bus.publish(EventLogged(
                event={"kind": "order_placed", "order": order},
                event_kind="order_placed",
                ts=event.ts,
            ))
        except Exception as ex:
            logger.warning("MonitoringModule _on_order_placed failed: %s", ex)

    def _on_order_filled(self, event: OrderFilled) -> None:
        """成交事件加入浮窗 + 持久化。"""
        try:
            fill = dict(event.fill or {})
            code = fill.get("code", "")
            self._add_to_event_list({
                "event_type": "OrderFilled",
                "code": code,
                "ts": event.ts,
                "details": {
                    "side": fill.get("side", ""),
                    "qty": fill.get("qty", 0),
                    "price": fill.get("price", 0),
                },
            })
            self._bus.publish(EventLogged(
                event={"kind": "order_filled", "fill": fill},
                event_kind="order_filled",
                ts=event.ts,
            ))
        except Exception as ex:
            logger.warning("MonitoringModule _on_order_filled failed: %s", ex)

    def _on_alert_raised(self, event: AlertRaised) -> None:
        """告警事件加入浮窗 + 更新告警冷却 + 持久化。"""
        try:
            alert = event.alert or {}
            rule_id = str(alert.get("rule_id", "") or "")
            code = str(alert.get("code", "") or "")
            cur_ts = float(event.ts or 0.0)
            cooldown_key = (rule_id, code)
            last_ts = self._alert_cooldown.get(cooldown_key, 0.0)
            cooldown_sec = float(
                self._alert_rules.get(rule_id, {}).get("cooldown_sec", _DEFAULT_ALERT_COOLDOWN_SEC)
                or _DEFAULT_ALERT_COOLDOWN_SEC
            )
            if cur_ts - last_ts < cooldown_sec:
                return
            self._alert_cooldown[cooldown_key] = cur_ts
            self._add_to_event_list({
                "event_type": "AlertRaised",
                "code": code,
                "ts": cur_ts,
                "details": {
                    "rule": rule_id,
                    "severity": alert.get("severity", ""),
                    "message": alert.get("message", ""),
                },
            })
            self._bus.publish(EventLogged(
                event={"kind": "alert", "alert": dict(alert)},
                event_kind="alert_raised",
                ts=cur_ts,
            ))
            self._update_snapshot(cur_ts)
        except Exception as ex:
            logger.warning("MonitoringModule _on_alert_raised failed: %s", ex)

    # === SubTask 11.3: 订阅事件构建快照，发布 SnapshotUpdated ===

    def _on_position_updated(self, event: PositionUpdated) -> None:
        """持仓更新触发快照重建。"""
        try:
            tracker = event.tracker or {}
            node_id = str(tracker.get("node_id", "") or "")
            code = str(tracker.get("code", "") or "")
            if node_id and code:
                node_set = self._node_snapshots.setdefault(node_id, set())
                if isinstance(node_set, set):
                    node_set.add(code)
            self._add_to_event_list({
                "event_type": "PositionUpdated",
                "code": code,
                "node_id": node_id,
                "pool_id": node_id,
                "ts": event.ts,
                "details": {
                    "qty": tracker.get("qty", 0),
                    "entry_price": tracker.get("entry_price", 0),
                    "pnl": round(float(tracker.get("pnl", 0) or 0), 2),
                },
            })
            positions = self._dashboard_data.setdefault("positions", {})
            if code:
                positions[code] = tracker
            self._update_snapshot(event.ts)
        except Exception as ex:
            logger.warning("MonitoringModule _on_position_updated failed: %s", ex)

    def _on_statistics_updated(self, event: StatisticsUpdated) -> None:
        """统计更新触发看盘面板数据刷新 + 快照重建。"""
        try:
            stats = dict(event.stats or {})
            self._dashboard_data["stats"] = stats
            self._add_to_event_list({
                "event_type": "StatisticsUpdated",
                "ts": event.ts,
                "details": {
                    "total_pnl": round(float(stats.get("total_pnl", 0) or 0), 2),
                    "trade_count": stats.get("trade_count", 0),
                    "win_rate": round(float(stats.get("win_rate", 0) or 0), 1),
                },
            })
            self._update_snapshot(event.ts)
        except Exception as ex:
            logger.warning("MonitoringModule _on_statistics_updated failed: %s", ex)

    def _on_ranking_changed(self, event: RankingChanged) -> None:
        """排名变化更新看盘面板（表驱动分派，无 if/elif 链）。"""
        try:
            field_name = _RANKING_DIMENSION_FIELDS.get(event.dimension, "")
            if field_name:
                self._dashboard_data[field_name] = dict(event.rankings or {})
            self._add_to_event_list({
                "event_type": "RankingChanged",
                "ts": event.ts,
                "details": {
                    "dimension": event.dimension,
                },
            })
            self._update_snapshot(event.ts)
        except Exception as ex:
            logger.warning("MonitoringModule _on_ranking_changed failed: %s", ex)

    def _apply_transfer_to_snapshot(self, event: TransferExecuted) -> None:
        """将转移事件应用到节点股票快照（copy/move 模式）。"""
        codes = list(event.codes) if event.codes else []
        if not codes:
            return
        # 目标节点添加
        tgt_set = self._node_snapshots.setdefault(event.tgt, set())
        if isinstance(tgt_set, set):
            for code in codes:
                tgt_set.add(code)
        # move 模式：从源节点移除
        if event.mode == "move":
            src_set = self._node_snapshots.get(event.src)
            if isinstance(src_set, set):
                for code in codes:
                    src_set.discard(code)

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

    # === SubTask 11.4: 发布 EventLogged 事件供 Database 持久化 ===

    def _on_edge_fired(self, event: EdgeFired) -> None:
        """边触发事件加入浮窗 + 持久化。"""
        try:
            changed_codes = list(event.changed_codes) if event.changed_codes else []
            code_str = ",".join(changed_codes[:5]) + ("..." if len(changed_codes) > 5 else "")
            self._add_to_event_list({
                "event_type": "EdgeFired",
                "edge_id": event.eid,
                "eid": event.eid,
                "code": code_str,
                "ts": event.ts,
                "details": {
                    "changed_count": len(changed_codes),
                },
            })
            self._bus.publish(EventLogged(
                event={"kind": "edge_fired", "eid": event.eid, "changed_codes": changed_codes},
                event_kind="edge_fired",
                ts=event.ts,
            ))
        except Exception as ex:
            logger.warning("MonitoringModule _on_edge_fired failed: %s", ex)

    def _on_signal(self, event: Signal) -> None:
        """交易信号事件加入浮窗 + 持久化。"""
        try:
            self._add_to_event_list({
                "event_type": event.signal_type,
                "code": event.code,
                "node_id": event.pool_id,
                "pool_id": event.pool_id,
                "ts": event.ts,
                "details": {
                    "signal_type": event.signal_type,
                    "price": event.price,
                    "quantity": event.quantity,
                    "condition": event.condition,
                    "profit_pct": event.profit_pct,
                    "hold_days": event.hold_days,
                },
            })
            self._bus.publish(EventLogged(
                event={
                    "kind": "signal",
                    "signal_type": event.signal_type,
                    "code": event.code,
                    "price": event.price,
                },
                event_kind="signal",
                ts=event.ts,
            ))
        except Exception as ex:
            logger.warning("MonitoringModule _on_signal failed: %s", ex)

    def _on_crossover_detected(self, event: CrossOverDetected) -> None:
        """金叉/死叉事件加入浮窗 + 持久化。"""
        try:
            self._add_to_event_list({
                "event_type": "CrossOverDetected",
                "code": event.code,
                "ts": event.ts,
                "details": {
                    "cross_type": event.cross_type,
                    "formula_ref": event.formula_ref,
                },
            })
            self._bus.publish(EventLogged(
                event={
                    "kind": "crossover",
                    "code": event.code,
                    "cross_type": event.cross_type,
                    "formula_ref": event.formula_ref,
                },
                event_kind="crossover_detected",
                ts=event.ts,
            ))
        except Exception as ex:
            logger.warning("MonitoringModule _on_crossover_detected failed: %s", ex)

    def _on_mode_changed(self, event: ModeChanged) -> None:
        """模式切换事件加入浮窗 + 更新看盘面板模式。"""
        try:
            cur_ts = float(time.time())
            self._add_to_event_list({
                "event_type": "ModeChanged",
                "ts": cur_ts,
                "details": {
                    "mode_id": event.mode_id,
                    "prev_mode": event.prev_mode,
                },
            })
            self._dashboard_data["mode"] = event.mode_id
            self._update_snapshot(cur_ts)
        except Exception as ex:
            logger.warning("MonitoringModule _on_mode_changed failed: %s", ex)

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


# 5 种收益分析类型 → 计算方法名（表驱动，无 if/elif 链）
# key 与 analysis_config.json 的 analysis_types 对齐
_ANALYSIS_HANDLERS: Dict[str, str] = {
    "intraday": "_compute_intraday_pnl",
    "market_impact": "_compute_market_impact_pnl",
    "historical": "_compute_historical_pnl",
    "distribution": "_compute_distribution_pnl",
    "positioning": "_compute_positioning_pnl",
}

# 多分析角度维度 → 排序键提取方法名（表驱动，无 if/elif 链）
# key 与 analysis_config.json 的 angles 对齐（动量/趋势/价值）
_ANGLE_SORT_KEYS: Dict[str, str] = {
    "momentum": "_momentum_key",
    "trend": "_trend_key",
    "value": "_value_key",
}


class StatisticsModule:
    """Statistics 模块：交易统计 + 收益分析 + PK 排名 + 多分析角度。仅与 EventBus 交互。

    订阅 ``PositionUpdated`` / ``BarComposed`` 事件，计算交易统计与收益分析，
    执行 PK 排名与多分析角度，发布 ``StatisticsUpdated`` / ``RankingChanged`` 事件。

    属性（实例级，≤ 5）:
      - _bus: EventBus
      - _config: 配置 dict
      - _pk_cfg / _analysis_cfg: pk_config.json / analysis_config.json 配置表
      - _trackers: 持仓跟踪表镜像 {(node_id, code): tracker}
      - _stats: 累计统计指标
      - _pk_rankings / _angle_results: PK 排名 / 多分析角度结果
    """

    def __init__(self, bus: EventBus, config: Optional[Dict[str, Any]] = None) -> None:
        self._bus = bus
        self._config = config or {}
        # 加载配置表
        self._pk_cfg = self._load_json("config/pk_config.json")
        self._analysis_cfg = self._load_json("config/analysis_config.json")
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
        # 注册事件订阅
        self._register_subscribers()

    # === 初始化辅助 ===

    def _register_subscribers(self) -> None:
        """注册事件订阅：PositionUpdated / BarComposed / StatisticsUpdated。

        SubTask 19.6: 订阅 StatisticsUpdated 事件，统计更新后自动触发
        ``publish_rankings`` 发布 RankingChanged 事件（原为公开方法但无
        事件驱动触发，导致 PK 排名/多分析角度链路断裂）。
        """
        self._bus.subscribe(PositionUpdated, self._on_position_updated)
        self._bus.subscribe(BarComposed, self._on_bar_composed)
        # SubTask 19.6: StatisticsUpdated → publish_rankings → RankingChanged
        self._bus.subscribe(StatisticsUpdated, self._on_statistics_updated)

    def _on_statistics_updated(self, event: StatisticsUpdated) -> None:
        """统计更新触发排名发布（SubTask 19.6）。

        ``_on_position_updated`` / ``_on_bar_composed`` 计算完统计后发布
        ``StatisticsUpdated``，本 handler 订阅该事件并调用 ``publish_rankings``
        发布 ``RankingChanged``（PK 排名 + 多分析角度），补全事件驱动链路。
        """
        try:
            self.publish_rankings(ts=event.ts)
        except Exception as ex:
            logger.warning("StatisticsModule _on_statistics_updated failed: %s", ex)

    @staticmethod
    def _load_json(rel_path: str) -> Dict[str, Any]:
        """加载 JSON 配置表（基于工作目录解析相对路径）。"""
        try:
            full = rel_path if os.path.isabs(rel_path) else os.path.join(os.getcwd(), rel_path)
            with open(full, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError, OSError) as ex:
            logger.warning("StatisticsModule 加载配置表失败 %s: %s", rel_path, ex)
            return {}

    # === SubTask 10.2: 订阅事件计算交易统计 + 5 种收益分析 ===

    def _on_position_updated(self, event: PositionUpdated) -> None:
        """持仓更新触发统计计算。"""
        try:
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
        except Exception as ex:
            logger.warning("StatisticsModule _on_position_updated failed: %s", ex)

    def _on_bar_composed(self, event: BarComposed) -> None:
        """K 线合成触发未实现盈亏更新。"""
        try:
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
        except Exception as ex:
            logger.warning("StatisticsModule _on_bar_composed failed: %s", ex)

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
                analyses[name] = handler()
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

    # === 5 种收益分析实现 ===

    def _compute_intraday_pnl(self) -> Dict[str, Any]:
        """日内收益分析：当前持仓的未实现盈亏。"""
        unrealized = sum(
            float(t.get("pnl", 0.0) or 0.0)
            for t in self._trackers.values()
            if int(t.get("qty", 0) or 0) > 0
        )
        return {"unrealized_pnl": unrealized}

    def _compute_market_impact_pnl(self) -> Dict[str, Any]:
        """市场冲击收益分析：按板块/市场分组的收益。"""
        by_sector: Dict[str, float] = {}
        for t in self._trackers.values():
            if int(t.get("qty", 0) or 0) <= 0:
                continue
            sector = str(t.get("market", "") or t.get("sector", "") or "default")
            by_sector[sector] = by_sector.get(sector, 0.0) + float(t.get("pnl", 0.0) or 0.0)
        return {"by_sector": by_sector}

    def _compute_historical_pnl(self) -> Dict[str, Any]:
        """历史收益分析：累计已实现盈亏。"""
        return {"total_realized_pnl": float(self._stats["total_pnl"])}

    def _compute_distribution_pnl(self) -> Dict[str, Any]:
        """收益分布分析：最大/最小/平均盈亏。"""
        pnls = [float(t.get("pnl", 0.0) or 0.0) for t in self._trackers.values()]
        if not pnls:
            return {"max": 0.0, "min": 0.0, "avg": 0.0}
        return {
            "max": max(pnls),
            "min": min(pnls),
            "avg": sum(pnls) / len(pnls),
        }

    def _compute_positioning_pnl(self) -> Dict[str, Any]:
        """持仓定位分析：活跃持仓数 + 总市值。"""
        active = [
            t for t in self._trackers.values()
            if int(t.get("qty", 0) or 0) > 0
        ]
        total_mv = sum(
            int(t.get("qty", 0) or 0) * float(t.get("cur_price", 0.0) or 0.0)
            for t in active
        )
        return {"active_positions": len(active), "total_market_value": total_mv}

    # === SubTask 10.3: PK 排名 + 多分析角度 ===

    def compute_pk_ranking(self) -> Dict[str, List[Any]]:
        """执行 PK 排名（pk_config.json 多维加权）。

        简化实现：按当前持仓的 pnl 降序排名。原 engine.py 的多维加权评分
        （profit/momentum/trend/volume/volatility）需 bar_data + tracker formula
        求值，依赖 FormulaModule；迁移期此处采用 tracker.pnl 简化评分，
        避免跨模块强耦合。
        """
        if not self._pk_cfg:
            return {}
        try:
            candidates = [
                (k, t) for k, t in self._trackers.items()
                if int(t.get("qty", 0) or 0) > 0
            ]
            candidates.sort(key=lambda x: float(x[1].get("pnl", 0.0) or 0.0), reverse=True)
            rankings = [
                {
                    "code": k[1],
                    "node_id": k[0],
                    "pnl": float(t.get("pnl", 0.0) or 0.0),
                    "rank": i + 1,
                }
                for i, (k, t) in enumerate(candidates)
            ]
            self._pk_rankings = {"by_pnl": rankings}
            return self._pk_rankings
        except Exception as ex:
            logger.warning("StatisticsModule compute_pk_ranking failed: %s", ex)
            return {}

    def compute_analysis_angles(self) -> Dict[str, List[Any]]:
        """执行多分析角度（analysis_config.json 动量/趋势/价值）。

        简化实现：按角度维度分组排序。原 engine.py 的公式求值依赖 FormulaModule，
        迁移期此处采用 tracker 现有字段派生的排序键（表驱动，无 if/elif 链）。
        """
        if not self._analysis_cfg:
            return {}
        results: Dict[str, List[Any]] = {}
        try:
            candidates = [
                (k, t) for k, t in self._trackers.items()
                if int(t.get("qty", 0) or 0) > 0
            ]
            for angle_name, key_method in _ANGLE_SORT_KEYS.items():
                key_fn = getattr(self, key_method, None)
                if not callable(key_fn):
                    continue
                ordered = sorted(candidates, key=key_fn, reverse=True)
                results[angle_name] = [
                    {"code": k[1], "node_id": k[0], "rank": i + 1}
                    for i, (k, _t) in enumerate(ordered)
                ]
            self._angle_results = results
            return results
        except Exception as ex:
            logger.warning("StatisticsModule compute_analysis_angles failed: %s", ex)
            return {}

    # === 多分析角度排序键（表驱动分派的目标方法） ===

    @staticmethod
    def _momentum_key(item: Tuple[Tuple[str, str], Dict[str, Any]]) -> float:
        """动量排序键：收益率（pnl / 成本）。"""
        _k, t = item
        entry = float(t.get("entry_price", 0.0) or 0.0)
        if entry <= 0:
            return 0.0
        return float(t.get("pnl", 0.0) or 0.0) / entry

    @staticmethod
    def _trend_key(item: Tuple[Tuple[str, str], Dict[str, Any]]) -> float:
        """趋势排序键：当前盈亏。"""
        return float(item[1].get("pnl", 0.0) or 0.0)

    @staticmethod
    def _value_key(item: Tuple[Tuple[str, str], Dict[str, Any]]) -> float:
        """价值排序键：成本价越低价值越高（升序 → 取负值与 desc 统一）。"""
        return -float(item[1].get("entry_price", 0.0) or 0.0)

    def publish_rankings(self, ts: float = 0.0) -> None:
        """发布排名变化事件（PK 排名 + 多分析角度）。

        PK 排名与多分析角度分别发布 ``RankingChanged`` 事件，dimension 字段
        区分（``pk`` / ``analysis_angles``），下游订阅者按 dimension 过滤。
        """
        try:
            pk = self.compute_pk_ranking()
            self._bus.publish(RankingChanged(rankings=pk, dimension="pk", ts=ts))
        except Exception as ex:
            logger.warning("StatisticsModule publish pk ranking failed: %s", ex)
        try:
            angles = self.compute_analysis_angles()
            self._bus.publish(RankingChanged(rankings=angles, dimension="analysis_angles", ts=ts))
        except Exception as ex:
            logger.warning("StatisticsModule publish analysis angles failed: %s", ex)


__all__ = ["MonitoringModule", "StatisticsModule"]
