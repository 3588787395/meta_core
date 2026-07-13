"""TTL 过期管理：simtests/单测兼容入口。

生产路径完全走统一时间驱动（EventDriver.fire_due → TimedEventSpec.action），
不调用 apply_ttl。本模块仅作为兼容入口供 simtests 使用。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

try:
    from ..core.compiler import _build_ttl_spec
    from ..core.edge_executor import _stock_code, _stock_entry_time, _now_ts, _current_seconds_of_day
    from ..core.runtime import PoolState
    from ..core.time_util import _safe_timestamp, time_at
    from ..core.event_bus import DomainEvent
except ImportError:
    try:
        from .compiler import _build_ttl_spec
        from .edge_executor import _stock_code, _stock_entry_time, _now_ts, _current_seconds_of_day
        from .runtime import PoolState
        from .time_util import _safe_timestamp, time_at
        from .event_bus import DomainEvent
    except ImportError:
        from compiler import _build_ttl_spec
        from edge_executor import _stock_code, _stock_entry_time, _now_ts, _current_seconds_of_day
        from runtime import PoolState
        from time_util import _safe_timestamp, time_at
        from event_bus import DomainEvent

logger = logging.getLogger(__name__)


def _do_ttl_check(state: PoolState, ttl_spec: Any, tgt: str, bus: Any = None, eid: str = "") -> List[str]:
    """TTL 检查（兼容入口）：按 check_type 分派，删除超时股票，发布 TIMEOUT。"""
    if ttl_spec.bdel != 1:
        return []

    now_unix = _now_ts(state)
    now_sec_of_day = _current_seconds_of_day(time_at(state=state))

    removed: List[str] = []
    kept: List[Any] = []

    if ttl_spec.check_type == "interval" and ttl_spec.ttl_sec > 0:
        for stock in state.get_node_stocks(tgt):
            entry_ts = _stock_entry_time(stock)
            if entry_ts is not None and (now_unix - entry_ts) >= ttl_spec.ttl_sec:
                removed.append(_stock_code(stock))
                continue
            kept.append(stock)
    elif ttl_spec.check_type == "endtime":
        if now_sec_of_day < ttl_spec.endtime_sec:
            return []
        for stock in state.get_node_stocks(tgt):
            if ttl_spec.hold_for_ttl > 0:
                entry_ts = _stock_entry_time(stock)
                if entry_ts is not None and (now_unix - entry_ts) >= ttl_spec.hold_for_ttl:
                    removed.append(_stock_code(stock))
                    continue
                kept.append(stock)
            else:
                removed.append(_stock_code(stock))
    else:
        return []

    if removed:
        state.set_node_stocks(tgt, kept)
        state.mark_node_dirty(tgt)
        logger.info("TTL expire: removed %s from %s (check=%s)",
                    removed, tgt, ttl_spec.check_type)
        if bus is not None:
            for code in removed:
                bus.publish(DomainEvent(
                    event_type="TIMEOUT",
                    code=code,
                    pool_id=tgt,
                    details={"reason": "TTL_EXPIRED", "flow_id": eid, "ttl_sec": ttl_spec.ttl_sec, "timestamp": now_unix},
                ))
    return removed


class TTLHelper:
    """TTL 兼容入口：apply_ttl 供 simtests 调用。"""

    def __init__(self, psatt_cfg: Dict[str, Any] = None, defaults: Dict[str, Any] = None,
                 now_fn: Callable[[], Any] = None):
        self._psatt_cfg = psatt_cfg or {}
        self._defaults = defaults or {}
        self._now = now_fn

    def apply_ttl(self, node_id: str, node: Any, node_stocks: Dict[str, list],
                  bus: Any = None, eid: str = "") -> None:
        """对指定状态池节点执行 TTL 过期淘汰。"""
        ttl_spec = _build_ttl_spec(node_id, {node_id: node})
        if ttl_spec.bdel != 1 or ttl_spec.check_type == "none":
            return

        state = PoolState({"nodes": [], "edges": []})
        if self._now is not None:
            try:
                ts = _safe_timestamp(self._now())
                state.time_source = {
                    "driver_type": "wall_clock",
                    "current_ts": ts,
                    "kind": "ttl_helper",
                }
            except Exception:
                state.time_source = {"driver_type": "wall_clock", "current_ts": 0.0}
        state.set_node_stocks(node_id, list(node_stocks.get(node_id, [])))

        _do_ttl_check(state, ttl_spec, node_id, bus=bus, eid=eid)

        node_stocks[node_id] = list(state.get_node_stocks(node_id))
