"""SnapshotBuilder：节点股票物化视图构建器（Task 9）。

作为 ``EventBus`` 的订阅者，仅通过 ``Executed`` / ``DataChanged`` 事件增量更新视图，
禁止直接读取 ``PoolState.node_stocks`` / ``latest_tick`` / ``exec_ctx`` 等核心运行时表。
"""
from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional, Set

try:
    from ..core.event_bus import EVENT_DATA_CHANGED, EVENT_DOMAIN, EVENT_EXECUTED, DataChanged, DomainEvent, EventBus, Executed
    from ..core._market_utils import _stock_code
except ImportError:
    try:
        from .event_bus import EVENT_DATA_CHANGED, EVENT_DOMAIN, EVENT_EXECUTED, DataChanged, DomainEvent, EventBus, Executed
        from ._market_utils import _stock_code
    except ImportError:
        from event_bus import EVENT_DATA_CHANGED, EVENT_DOMAIN, EVENT_EXECUTED, DataChanged, DomainEvent, EventBus, Executed
        from _market_utils import _stock_code


class SnapshotBuilder:
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
