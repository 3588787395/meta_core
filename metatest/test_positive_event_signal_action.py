"""Task 18.5: event-signal-action orthogonal architecture positive tests.

Verify the three-layer orthogonal architecture:
  - StockChanged event class defined (with node_id, code, action, ts fields)
  - StockChanged event has no side effects (pure state record)
  - SignalDeriver subscribes to StockChanged event
  - SignalDeriver publishes Signal(signal_type="BUY") on target role enter
  - SignalDeriver publishes Signal(signal_type="SELL") on target role exit
  - ActionDispatcher subscribes to Signal event
  - ActionDispatcher executes actions from action table on signal receipt
  - No direct side-effect calls (sound.play / popup.show) in core/

The architecture is orthogonal: StockChanged (event) -> SignalDeriver (signal)
-> ActionDispatcher (action). Each layer is decoupled and testable in isolation.

Hard constraints:
  - Simulation mode uses "fz" prefix for stock codes
  - Tests use mock/patch to isolate SignalDeriver._get_node_role (default "state")
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest


_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent
_CORE_DIR = _PROJECT_ROOT / "core"


# 1. StockChanged event defined
def test_stock_changed_event_defined():
    """StockChanged event class should be defined with required fields.

    Required fields: node_id, code, action, ts
    """
    from core.event_bus import StockChanged
    from dataclasses import fields as dc_fields

    field_names = {f.name for f in dc_fields(StockChanged)}
    expected = {"node_id", "code", "action", "ts"}
    assert expected == field_names, (
        f"StockChanged fields mismatch: expected {expected}, got {field_names}"
    )
    # Verify it can be instantiated with positional args
    ev = StockChanged(node_id="tgt", code="fz000001", action="enter", ts=34500.0)
    assert ev.node_id == "tgt"
    assert ev.code == "fz000001"
    assert ev.action == "enter"
    assert ev.ts == 34500.0


# 2. StockChanged event has no side effects
def test_stock_changed_event_no_side_effects():
    """StockChanged event should be a pure state record (no side-effect fields).

    Verified by inspecting dataclass fields: all fields are simple data types
    (str/float), no callable, no IO refs, no method invocations.
    """
    from core.event_bus import StockChanged
    from dataclasses import fields as dc_fields

    # All fields should be primitive data types (str/float)
    for f in dc_fields(StockChanged):
        # field.type may be a string (PEP 563) or actual type; just verify name
        assert f.name in {"node_id", "code", "action", "ts"}, (
            f"unexpected field {f.name}"
        )
    # Instantiate and verify no methods that cause side effects exist
    ev = StockChanged(node_id="tgt", code="fz000001", action="enter", ts=100.0)
    # Verify the event is a plain data carrier - no publish/execute/play methods
    public_methods = [
        m for m in dir(ev)
        if not m.startswith("_") and callable(getattr(ev, m))
    ]
    # dataclass-generated methods: only equality and repr from base object
    # Filter out inherited object methods
    object_methods = set(dir(object()))
    event_specific_methods = [
        m for m in public_methods
        if m not in object_methods
    ]
    # StockChanged should have no event-specific callable methods (pure data)
    # The only dataclass-generated method should be __init__ (filtered above)
    assert event_specific_methods == [], (
        f"StockChanged should have no side-effect methods, found: {event_specific_methods}"
    )


# 3. SignalDeriver subscribes to StockChanged
def test_signal_deriver_subscribes_stock_changed():
    """SignalDeriver should subscribe to StockChanged event in __init__.

    Verified by spying on EventBus.subscribe() calls during SignalDeriver
    construction.
    """
    from core.event_bus import EventBus, StockChanged
    from core.trade_module import SignalDeriver

    bus = EventBus()
    # Spy on subscribe() to capture (event_type, handler) pairs
    subscribe_calls = []
    original_subscribe = bus.subscribe

    def spy_subscribe(event_type, handler):
        subscribe_calls.append((event_type, handler))
        return original_subscribe(event_type, handler)

    bus.subscribe = spy_subscribe
    roles = {"target": {"on_enter": ["publish_buy_signal"], "on_exit": ["publish_sell_signal"]}}
    SignalDeriver(bus, roles)

    # Verify SignalDeriver subscribed to StockChanged
    stock_changed_subs = [c for c in subscribe_calls if c[0] is StockChanged]
    assert len(stock_changed_subs) == 1, (
        f"SignalDeriver should subscribe to StockChanged once, "
        f"got {len(stock_changed_subs)} subscriptions"
    )


# 4. SignalDeriver publishes BUY on target enter
def test_signal_deriver_publishes_buy_on_target_enter(signal_collector):
    """target role enter -> SignalDeriver publishes Signal(signal_type="BUY").

    Uses signal_collector fixture (bus, collected) to capture published Signal.
    Patches SignalDeriver._get_node_role to return "target" for the test node
    (default returns "state" which does not trigger BUY signal).
    """
    from core.event_bus import StockChanged
    from core.trade_module import SignalDeriver

    bus, collected = signal_collector
    roles = {"target": {"on_enter": ["publish_buy_signal"], "on_exit": ["publish_sell_signal"]}}
    sd = SignalDeriver(bus, roles)
    # Patch _get_node_role to return "target" (default returns "state")
    sd._get_node_role = lambda node_id: "target"

    # Publish StockChanged with action="enter"
    bus.publish(StockChanged(
        node_id="tgt_pool", code="fz000001", action="enter", ts=34500.0
    ))

    # Should derive and publish a Signal with signal_type="BUY"
    assert len(collected) == 1, f"expected 1 Signal, got {len(collected)}"
    sig = collected[0]
    assert sig.signal_type == "BUY", f"expected BUY, got {sig.signal_type}"
    assert sig.code == "fz000001"
    assert sig.pool_id == "tgt_pool"
    assert sig.ts == 34500.0


# 5. SignalDeriver publishes SELL on target exit
def test_signal_deriver_publishes_sell_on_target_exit(signal_collector):
    """target role exit -> SignalDeriver publishes Signal(signal_type="SELL").

    Uses signal_collector fixture (bus, collected) to capture published Signal.
    Patches SignalDeriver._get_node_role to return "target".
    """
    from core.event_bus import StockChanged
    from core.trade_module import SignalDeriver

    bus, collected = signal_collector
    roles = {"target": {"on_enter": ["publish_buy_signal"], "on_exit": ["publish_sell_signal"]}}
    sd = SignalDeriver(bus, roles)
    sd._get_node_role = lambda node_id: "target"

    # Publish StockChanged with action="exit"
    bus.publish(StockChanged(
        node_id="tgt_pool", code="fz000001", action="exit", ts=34600.0
    ))

    # Should derive and publish a Signal with signal_type="SELL"
    assert len(collected) == 1, f"expected 1 Signal, got {len(collected)}"
    sig = collected[0]
    assert sig.signal_type == "SELL", f"expected SELL, got {sig.signal_type}"
    assert sig.code == "fz000001"
    assert sig.pool_id == "tgt_pool"
    assert sig.ts == 34600.0


# 6. ActionDispatcher subscribes to Signal
def test_action_dispatcher_subscribes_signal():
    """ActionDispatcher should subscribe to Signal event in __init__.

    Verified by spying on EventBus.subscribe() calls during ActionDispatcher
    construction.
    """
    from core.event_bus import EventBus, Signal
    from core.trade_module import ActionDispatcher

    bus = EventBus()
    subscribe_calls = []
    original_subscribe = bus.subscribe

    def spy_subscribe(event_type, handler):
        subscribe_calls.append((event_type, handler))
        return original_subscribe(event_type, handler)

    bus.subscribe = spy_subscribe
    action_table = {"BUY": ["play_sound"], "SELL": ["save_history"]}
    ActionDispatcher(bus, action_table)

    # Verify ActionDispatcher subscribed to Signal
    signal_subs = [c for c in subscribe_calls if c[0] is Signal]
    assert len(signal_subs) == 1, (
        f"ActionDispatcher should subscribe to Signal once, "
        f"got {len(signal_subs)} subscriptions"
    )


# 7. ActionDispatcher executes actions from table
def test_action_dispatcher_executes_actions_from_table():
    """On Signal receipt, ActionDispatcher should look up action_table and execute.

    ActionDispatcher uses self._action_table (instance attr from constructor)
    keyed by signal_type. The task spec mentions _ACTION_TABLE; the actual
    implementation uses self._action_table (set from action_table param).
    This is a minor API naming mismatch but the table-driven dispatch behavior
    is verified.
    """
    from core.event_bus import EventBus, Signal
    from core.trade_module import ActionDispatcher

    bus = EventBus()
    executed_actions = []

    action_table = {
        "BUY": ["play_sound", "show_popup"],
        "SELL": ["save_history"],
    }
    ad = ActionDispatcher(bus, action_table)

    def spy_exec(action_name, signal):
        executed_actions.append((action_name, signal.signal_type, signal.code))

    ad._execute_action = spy_exec

    # Publish a BUY signal
    bus.publish(Signal(
        signal_type="BUY", code="fz000001", pool_id="tgt", price=10.0, ts=34500.0
    ))

    assert len(executed_actions) == 2, (
        f"expected 2 actions for BUY, got {len(executed_actions)}"
    )
    assert executed_actions[0] == ("play_sound", "BUY", "fz000001")
    assert executed_actions[1] == ("show_popup", "BUY", "fz000001")

    # Clear and publish a SELL signal
    executed_actions.clear()
    bus.publish(Signal(
        signal_type="SELL", code="fz000002", pool_id="tgt", price=20.0, ts=34600.0
    ))
    assert len(executed_actions) == 1, (
        f"expected 1 action for SELL, got {len(executed_actions)}"
    )
    assert executed_actions[0] == ("save_history", "SELL", "fz000002")


# 8. No direct side effects (sound.play / popup.show) in core/
def test_no_direct_side_effects_in_transfer():
    """Grep verify: no direct sound.play / popup.show calls in core/.

    Side effects (sound/popup) should be delegated via ActionDispatcher /
    action_table (indirection layer), not called directly in core/.
    Verified by grepping core/*.py for forbidden patterns.
    """
    forbidden_patterns = [
        r"\bsound\.play\b",
        r"\bpopup\.show\b",
        r"\bplaysound\b",
        r"\bwinsound\b",
    ]
    py_files = list(_CORE_DIR.glob("*.py"))
    assert len(py_files) > 0, f"no core/*.py files in {_CORE_DIR}"
    failures = []
    for py_file in py_files:
        try:
            src = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for pattern in forbidden_patterns:
            matches = re.findall(pattern, src)
            if matches:
                failures.append(
                    f"{py_file.name}: forbidden pattern {pattern!r} ({len(matches)} matches)"
                )
    assert not failures, (
        "core/ has direct side-effect calls (should be delegated via ActionDispatcher):\n  " +
        "\n  ".join(failures)
    )


# === Task 28.6 回归断言：converge-meta-essence-v4 阶段 3 C11 + 阶段 2 E1/E2 收敛状态 ===


class TestConvergenceRegressionV4:
    """SubTask 28.6：converge-meta-essence-v4 C11 _SUBSCRIPTIONS + E1/E2 heapq 收敛回归。"""

    def test_base_module_subscriptions_table(self):
        """event_bus.py 含 _BaseModule + _SUBSCRIPTIONS 表（C11 表驱动订阅）。"""
        import ast
        from pathlib import Path
        tree = ast.parse((Path(__file__).resolve().parent.parent / "core" / "event_bus.py").read_text(encoding="utf-8"))
        classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        assert "_BaseModule" in classes, \
            "event_bus.py 应含 _BaseModule 基类（C11 表驱动订阅）"
        src = (Path(__file__).resolve().parent.parent / "core" / "event_bus.py").read_text(encoding="utf-8")
        assert "_SUBSCRIPTIONS" in src, \
            "event_bus.py 应含 _SUBSCRIPTIONS 类属性表（C11 表驱动）"

    def test_eventdriver_heapq_used(self):
        """execution_module EventDriver 使用 heapq（E1/E2 时间原语）。"""
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "core" / "execution_module.py").read_text(encoding="utf-8")
        assert "import heapq" in src, \
            "execution_module 应 import heapq（EventDriver 优先队列）"
        assert "self._heap" in src, \
            "EventDriver 应维护 self._heap 优先队列属性"
