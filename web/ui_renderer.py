"""UI 渲染与 WebSocket 增量推送（Task 9）。

UIRenderer 与 WebSocketPublisher 只订阅 EventBus 事件并读取 SnapshotBuilder 视图，
禁止直接读取 ``PoolState`` 的 ``node_stocks`` / ``latest_tick`` / ``exec_ctx`` 等核心运行时表。
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict
from typing import Any, Callable, Dict, List, Optional

try:
    from core.event_bus import (
        EVENT_DATA_CHANGED,
        EVENT_DOMAIN,
        EVENT_EXECUTED,
        EVENT_SIGNAL,
        DataChanged,
        DomainEvent,
        EventBus,
        Executed,
        Signal,
    )
    from core.snapshot_builder import SnapshotBuilder
except ImportError:
    from meta_core.core.event_bus import (
        EVENT_DATA_CHANGED,
        EVENT_DOMAIN,
        EVENT_EXECUTED,
        EVENT_SIGNAL,
        DataChanged,
        DomainEvent,
        EventBus,
        Executed,
        Signal,
    )
    from meta_core.core.snapshot_builder import SnapshotBuilder


def _event_payload(event: Any) -> Dict[str, Any]:
    """将 EventBus 事件转换为轻量 payload。"""
    if isinstance(event, Executed):
        payload = {
            "eid": event.eid,
            "sid": event.sid,
            "tid": event.tid,
            "entered": list(event.entered),
            "exited": list(event.exited),
        }
        # I23：转发 Executed.details（actions/prices/timestamp，原 DomainEvent(ENTER) 独有信息）
        if event.details:
            payload["details"] = event.details
        return payload
    if isinstance(event, DomainEvent):
        # I81：DomainEvent dataclass 为字段名/字段集单一真相源（与 I80 Signal 同构），
        # asdict 派生 dict 消除硬编码 4 字段列表 + 统一键名（"event_type" 对齐 dataclass
        # 字段名；原 "type" 键废除，与 API 路径 _on_domain_event 统一）。
        return asdict(event)
    if isinstance(event, Signal):
        # I80：Signal dataclass 为字段名/字段集单一真相源，asdict 派生 dict
        # （与 engine._build_sig_dict 同源），消除第二份硬编码 8 字段列表 + 统一键名。
        return asdict(event)
    if isinstance(event, DataChanged):
        return {
            "ts": event.ts,
            "bar_hash": event.bar_hash,
            "codes": event.codes,
            "source": event.source,
            "period": event.period,
        }
    return {}


class UIRenderer:
    """UI 渲染器：订阅 EventBus，将 SnapshotBuilder 视图格式化为前端消息。

    不直接读取运行时核心表；所有数据来自 ``SnapshotBuilder.snapshot()``
    或 EventBus 事件。

    属性（≤ 5）:
      - _snapshot_builder
      - _pool_id
      - _publishers

    方法（≤ 6）:
      - __init__
      - render_snapshot
      - render_event
      - on_event
      - attach_publisher
      - detach_publisher

    类属性:
      - _UI_EVENT_ROLES: EventBus 事件类型 → UI 消息类型映射表（I43 表驱动订阅）
    """

    # I43: 事件→消息类型映射表驱动，替代 4 处 lambda subscribe。
    # 新增 UI 事件只需加表行，无需改 __init__ 代码。
    _UI_EVENT_ROLES: Dict[str, str] = {
        EVENT_EXECUTED: "executed",
        EVENT_DOMAIN: "domain",
        EVENT_SIGNAL: "signal",
        EVENT_DATA_CHANGED: "data_changed",
    }

    def __init__(
        self,
        event_bus: EventBus,
        snapshot_builder: SnapshotBuilder,
        pool_id: str = "",
    ) -> None:
        self._snapshot_builder = snapshot_builder
        self._pool_id = pool_id
        self._publishers: List["WebSocketPublisher"] = []
        # I43: 循环订阅 _UI_EVENT_ROLES，mt=msg_type 默认参数绑定避免闭包延迟绑定。
        for event_type, msg_type in self._UI_EVENT_ROLES.items():
            event_bus.subscribe(
                event_type, lambda ev, mt=msg_type: self.on_event(ev, mt)
            )

    def render_snapshot(self) -> Dict[str, Any]:
        """渲染当前完整快照消息。"""
        return {
            "msg_type": "snapshot",
            "pool_id": self._pool_id,
            "ts": time.time(),
            "payload": self._snapshot_builder.snapshot(),
        }

    def render_event(self, event: Any, msg_type: str, seq: int) -> Dict[str, Any]:
        """渲染单个增量事件消息。"""
        return {
            "msg_type": msg_type,
            "pool_id": self._pool_id,
            "ts": time.time(),
            "seq": seq,
            "payload": _event_payload(event),
        }

    def on_event(self, event: Any, msg_type: str) -> None:
        """EventBus 事件处理器：格式化并分发给所有 WebSocketPublisher。"""
        for publisher in list(self._publishers):
            publisher.publish(self.render_event(event, msg_type, publisher.next_seq()))

    def attach_publisher(self, publisher: "WebSocketPublisher") -> None:
        """关联一个 WebSocketPublisher，后续消息会转发给它。"""
        if publisher not in self._publishers:
            self._publishers.append(publisher)

    def detach_publisher(self, publisher: "WebSocketPublisher") -> None:
        """解除关联 WebSocketPublisher。"""
        try:
            self._publishers.remove(publisher)
        except ValueError:
            pass


class WebSocketPublisher:
    """WebSocket 客户端管理与消息发送。

    本身不直接订阅 EventBus；由 UIRenderer 调用 ``publish()`` 进行广播。
    首次连接时通过 UIRenderer 渲染快照并发送。

    属性（≤ 5）:
      - _ui_renderer
      - _clients
      - _seq
      - _lock

    方法（≤ 6）:
      - __init__
      - next_seq
      - add_client
      - remove_client
      - publish
    """

    def __init__(
        self,
        ui_renderer: UIRenderer,
    ) -> None:
        self._ui_renderer = ui_renderer
        self._clients: List[Callable[[str], None]] = []
        self._seq = 0
        self._lock = threading.Lock()

    def next_seq(self) -> int:
        """获取下一个单调递增序列号。"""
        with self._lock:
            self._seq += 1
            return self._seq

    def add_client(self, send_callback: Callable[[str], None]) -> None:
        """添加客户端并发送首次快照。"""
        self._clients.append(send_callback)
        snapshot = self._ui_renderer.render_snapshot()
        snapshot["seq"] = self.next_seq()
        send_callback(json.dumps(snapshot, ensure_ascii=False, default=str))

    def remove_client(self, send_callback: Callable[[str], None]) -> None:
        """移除客户端。"""
        try:
            self._clients.remove(send_callback)
        except ValueError:
            pass

    def publish(self, message: Dict[str, Any]) -> None:
        """向所有客户端广播消息。"""
        text = json.dumps(message, ensure_ascii=False, default=str)
        for send in list(self._clients):
            try:
                send(text)
            except Exception:
                pass
