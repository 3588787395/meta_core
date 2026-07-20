"""UI 渲染与 WebSocket 增量推送（Task 9）。

UIRenderer 与 WebSocketPublisher 只订阅 EventBus 事件并读取 SnapshotBuilder 视图，
禁止直接读取 ``PoolState`` 的 ``node_stocks`` / ``latest_tick`` / ``exec_ctx`` 等核心运行时表。
"""
from __future__ import annotations

import json
import re
import sys
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
    from core.monitoring_module import _SnapshotBuilder
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
    from meta_core.core.monitoring_module import _SnapshotBuilder


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
        snapshot_builder: _SnapshotBuilder,
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


# === 工具函数层（自 web/js/_convert_table_driven.py 和 web/js/_reindent.py 合并）===


def convert_table_driven_main() -> None:
    """Convert TableDrivenPanel and TableDrivenForm to ES6 Class.

    临时脚本：将 table-driven-panel.js 中的 prototype 风格类转换为 ES6 class。
    自 web/js/_convert_table_driven.py 合并而来；调用以执行转换。
    """
    filepath = r'h:\new_tdx_mock\PYPlugins\meta_core\web\js\table-driven-panel.js'
    with open(filepath, 'r', encoding='utf-8') as f:
        raw = f.read()

    lines = raw.split('\n')

    # Patterns (2-space indent)
    ctor_pattern = re.compile(r'^  function (TableDrivenPanel|TableDrivenForm)\((.*?)\) \{$')
    proto_pattern = re.compile(r'^  (TableDrivenPanel|TableDrivenForm)\.prototype\.(\w+) = function \((.*?)\) \{$')
    static_prop_pattern = re.compile(r'^  (TableDrivenPanel)\.(_tdx\w+) = (.*);$')

    # Find constructor declarations and their closings (first `  }` after declaration)
    ctor_lines = {}        # line_index -> (classname, args)
    ctor_close_lines = {}  # line_index -> classname
    for i, line in enumerate(lines):
        m = ctor_pattern.match(line)
        if m:
            classname = m.group(1)
            args = m.group(2)
            ctor_lines[i] = (classname, args)
            # Find closing `  }` (exactly 2-space indent, no semicolon)
            for j in range(i + 1, len(lines)):
                if lines[j] == '  }':
                    ctor_close_lines[j] = classname
                    break

    # Find prototype method declarations and their closings (first `  };` after declaration)
    proto_lines = {}         # line_index -> (classname, methodname, args)
    method_close_lines = {}  # line_index -> classname
    for i, line in enumerate(lines):
        m = proto_pattern.match(line)
        if m:
            classname = m.group(1)
            methodname = m.group(2)
            args = m.group(3)
            proto_lines[i] = (classname, methodname, args)
            # Find closing `  };` (exactly 2-space indent, with semicolon)
            for j in range(i + 1, len(lines)):
                if lines[j] == '  };':
                    method_close_lines[j] = classname
                    break

    # Find static properties
    static_prop_lines = {}  # line_index -> original line
    for i, line in enumerate(lines):
        m = static_prop_pattern.match(line)
        if m:
            static_prop_lines[i] = line

    # Find the last method close for each class
    last_method_close = {}  # classname -> line_index
    for close_idx, classname in method_close_lines.items():
        if classname not in last_method_close or close_idx > last_method_close[classname]:
            last_method_close[classname] = close_idx

    # Build output
    output = []
    pending_static_props = []

    for i, line in enumerate(lines):
        # Constructor declaration -> class + constructor
        if i in ctor_lines:
            classname, args = ctor_lines[i]
            output.append('  class ' + classname + ' {')
            output.append('    constructor(' + args + ') {')
            continue

        # Constructor closing -> close constructor (stay in class)
        if i in ctor_close_lines:
            output.append('    }')
            continue

        # Static property -> skip (will add after class)
        if i in static_prop_lines:
            pending_static_props.append(line)
            continue

        # Prototype method declaration -> class method
        if i in proto_lines:
            classname, methodname, args = proto_lines[i]
            output.append('    ' + methodname + '(' + args + ') {')
            continue

        # Method closing -> close method, maybe close class
        if i in method_close_lines:
            output.append('    }')
            classname = method_close_lines[i]
            if i == last_method_close[classname]:
                # Close the class
                output.append('  }')
                # Add static props (if any)
                if pending_static_props:
                    output.append('')  # blank line separator
                    for prop in pending_static_props:
                        output.append(prop)
                    pending_static_props = []
            continue

        # Regular line - keep as-is
        output.append(line)

    # Close class if still open (safety)
    if pending_static_props:
        for prop in pending_static_props:
            output.append(prop)

    result = '\n'.join(output)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(result)

    # Verification output
    print('Conversion complete.')
    print('Original lines: ' + str(len(lines)))
    print('Output lines:   ' + str(len(output)))
    print('Constructor declarations: ' + str(ctor_lines))
    print('Constructor closings: ' + str(ctor_close_lines))
    print('Prototype methods: ' + str(len(proto_lines)))
    print('Method closings: ' + str(len(method_close_lines)))
    print('Static properties: ' + str(len(static_prop_lines)))
    print('Last method close per class: ' + str(last_method_close))

    # Verify no prototype patterns remain
    remaining_proto = sum(1 for l in output if proto_pattern.match(l))
    remaining_ctor = sum(1 for l in output if ctor_pattern.match(l))
    print('Remaining prototype patterns: ' + str(remaining_proto))
    print('Remaining constructor patterns: ' + str(remaining_ctor))


def reindent_main() -> None:
    """Re-indent class bodies in table-driven-panel.js after ES6 conversion.

    临时脚本：在 ES6 转换后重新缩进 table-driven-panel.js 的类体。
    自 web/js/_reindent.py 合并而来；调用以执行重缩进。
    """
    filepath = r'h:\new_tdx_mock\PYPlugins\meta_core\web\js\table-driven-panel.js'
    with open(filepath, 'r', encoding='utf-8') as f:
        raw = f.read()

    lines = raw.split('\n')

    # State machine using brace depth:
    # depth 0: outside class
    # depth 1: inside class body (between methods)
    # depth 2+: inside method/constructor body
    depth = 0
    in_class = False
    class_decl_pattern = re.compile(r'^  class \w+ \{$')

    output = []

    for line in lines:
        # Outside class: output as-is, detect class start
        if not in_class:
            output.append(line)
            if class_decl_pattern.match(line):
                in_class = True
                depth = 1
            continue

        # Inside class
        stripped = line.strip()

        # Blank lines: keep as-is
        if stripped == '':
            output.append(line)
            continue

        # Strip single-line comment for brace counting
        # (verified: no // inside strings in this file)
        code_part = line
        comment_idx = line.find('//')
        if comment_idx >= 0:
            code_part = line[:comment_idx]

        opens = code_part.count('{')
        closes = code_part.count('}')
        new_depth = depth + opens - closes

        if depth == 1:
            # Inside class body (between methods or at method declaration)
            if new_depth >= 2:
                # Method/constructor declaration - keep at 4-space indent
                output.append(line)
            elif new_depth == 1:
                # No depth change - comment/content at class body level
                # Add 2 spaces if not already at 4+ space indent
                if line.startswith('    '):
                    output.append(line)
                else:
                    output.append('  ' + line)
            elif new_depth == 0:
                # Class closing brace - keep as-is
                output.append(line)
                in_class = False
                depth = 0
                continue
            else:
                # Shouldn't happen (would mean negative depth)
                output.append(line)
        else:
            # Inside method body (depth >= 2)
            if new_depth == 1:
                # Method closing - keep at 4-space indent
                output.append(line)
            else:
                # Body line - add 2 spaces for proper indentation
                output.append('  ' + line)

        depth = new_depth

    result = '\n'.join(output)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(result)

    print('Re-indentation complete.')
    print('Output lines: ' + str(len(output)))
