"""Task 18.4: node role table-driven positive tests.

Verify the node role table architecture:
  - config/architecture/node_roles.json exists
  - Contains 5 roles: candidate / state / condition / target / discard
  - Each role defines on_enter and on_exit action lists
  - target role on_enter contains "publish_buy_signal"
  - target role on_exit contains "publish_sell_signal"
  - _ROLE_ACTIONS dict is defined in core/engine.py
  - No `if node.type ==` chain in engine.py (table-driven dispatch only)
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

import pytest


_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent
_NODE_ROLES_JSON = _PROJECT_ROOT / "config" / "architecture" / "node_roles.json"
_ENGINE_PY = _PROJECT_ROOT / "core" / "engine.py"


# 1. node_roles.json file exists
def test_node_roles_json_exists():
    """config/architecture/node_roles.json should exist on disk."""
    assert _NODE_ROLES_JSON.exists(), (
        f"node_roles.json not found at {_NODE_ROLES_JSON}"
    )
    assert _NODE_ROLES_JSON.is_file(), (
        f"{_NODE_ROLES_JSON} is not a regular file"
    )


# 2. node_roles.json has five roles
def test_node_roles_json_has_five_roles():
    """node_roles.json should contain all 5 roles.

    Expected roles: candidate / state / condition / target / discard
    """
    with open(_NODE_ROLES_JSON, encoding="utf-8") as f:
        roles = json.load(f)
    expected_roles = {"candidate", "state", "condition", "target", "discard"}
    actual_roles = set(roles.keys())
    assert expected_roles == actual_roles, (
        f"roles mismatch: expected {expected_roles}, got {actual_roles}"
    )
    assert len(actual_roles) == 5


# 3. each role has on_enter and on_exit
def test_each_role_has_on_enter_and_on_exit():
    """Each of the 5 roles must define on_enter and on_exit action lists."""
    with open(_NODE_ROLES_JSON, encoding="utf-8") as f:
        roles = json.load(f)
    for role_name, role_config in roles.items():
        assert "on_enter" in role_config, (
            f"role {role_name} missing on_enter"
        )
        assert "on_exit" in role_config, (
            f"role {role_name} missing on_exit"
        )
        assert isinstance(role_config["on_enter"], list), (
            f"role {role_name} on_enter must be a list"
        )
        assert isinstance(role_config["on_exit"], list), (
            f"role {role_name} on_exit must be a list"
        )


# 4. target role on_enter contains "publish_buy_signal"
def test_target_role_has_buy_signal_on_enter():
    """target role on_enter must contain "publish_buy_signal" action."""
    with open(_NODE_ROLES_JSON, encoding="utf-8") as f:
        roles = json.load(f)
    target_on_enter = roles["target"]["on_enter"]
    assert "publish_buy_signal" in target_on_enter, (
        f"target.on_enter must contain 'publish_buy_signal', got {target_on_enter}"
    )


# 5. target role on_exit contains "publish_sell_signal"
def test_target_role_has_sell_signal_on_exit():
    """target role on_exit must contain "publish_sell_signal" action."""
    with open(_NODE_ROLES_JSON, encoding="utf-8") as f:
        roles = json.load(f)
    target_on_exit = roles["target"]["on_exit"]
    assert "publish_sell_signal" in target_on_exit, (
        f"target.on_exit must contain 'publish_sell_signal', got {target_on_exit}"
    )


# 6. _ROLE_ACTIONS registry defined in engine.py
def test_role_actions_registry_defined():
    """_ROLE_ACTIONS dict should be defined in core/engine.py.

    Verified by grepping the source for the assignment statement.
    """
    assert _ENGINE_PY.exists(), f"engine.py not found: {_ENGINE_PY}"
    src = _ENGINE_PY.read_text(encoding="utf-8")
    # Match `_ROLE_ACTIONS:` (annotation) or `_ROLE_ACTIONS =` (assignment)
    pattern = r"\b_ROLE_ACTIONS\s*[:=]"
    matches = re.findall(pattern, src)
    assert len(matches) >= 1, (
        "engine.py should define _ROLE_ACTIONS dict (no matches found)"
    )
    # Also verify it is referenced as a registry (via .get() lookup pattern)
    registry_uses = re.findall(r"\b_ROLE_ACTIONS\.get\b", src)
    assert len(registry_uses) >= 1, (
        "engine.py should use _ROLE_ACTIONS.get() for table-driven dispatch"
    )


# 7. No `if node.type ==` chain in engine.py
def test_no_if_node_type_chain():
    """Grep verify: engine.py has no `if node.type ==` chain.

    Only comments are allowed to mention this pattern (the additive migration
    note). Actual code uses table-driven dispatch via _ROLE_ACTIONS.

    Match pattern: `if node.type ==` at the start of a code statement
    (after optional whitespace, not inside a comment line).
    """
    assert _ENGINE_PY.exists(), f"engine.py not found: {_ENGINE_PY}"
    src = _ENGINE_PY.read_text(encoding="utf-8")
    # Find all candidate matches (any occurrence of `if node.type ==`)
    code_matches = []
    for line in src.splitlines():
        stripped = line.lstrip()
        # Skip comment lines
        if stripped.startswith("#"):
            continue
        # Count occurrences of the pattern in this non-comment line
        line_matches = re.findall(r"\bif\s+node\.type\s*==", line)
        code_matches.extend(line_matches)
    assert len(code_matches) == 0, (
        f"engine.py should not contain `if node.type ==` chains in code, "
        f"found {len(code_matches)} occurrences (only comments allowed)"
    )


# 8. _ROLE_ACTIONS registry is a dict with on_enter/on_exit structure
def test_role_actions_registry_is_dict():
    """_ROLE_ACTIONS 应为 dict 类型。"""
    from core.engine import _ROLE_ACTIONS
    assert isinstance(_ROLE_ACTIONS, dict), \
        "_ROLE_ACTIONS 应为 dict"


# 9. _resolve_action resolves known action names to callables
def test_resolve_action_returns_callable_for_known_actions():
    """_resolve_action 对已知 action 名返回可调用 lambda。"""
    from core.engine import _resolve_action
    known_actions = [
        "mark_out_edges_dirty",
        "publish_enter_event",
        "publish_exit_event",
        "publish_buy_signal",
        "publish_sell_signal",
    ]
    for action_name in known_actions:
        fn = _resolve_action(action_name)
        assert callable(fn), f"_resolve_action({action_name!r}) 应返回可调用"


# 10. _resolve_action returns no-op for unknown action
def test_resolve_action_unknown_returns_noop():
    """_resolve_action 对未知 action 名返回 no-op（不抛异常）。"""
    from core.engine import _resolve_action
    fn = _resolve_action("definitely_not_an_action")
    assert callable(fn), "未知 action 也应返回可调用 no-op"
    # 调用 no-op 不抛异常
    result = fn(None, "node_1", "fz000001", 34500.0)
    assert result is None


# 11. _init_role_actions and _dispatch_role_action exist
def test_init_and_dispatch_role_action_functions_exist():
    """_init_role_actions 与 _dispatch_role_action 函数存在且可调用。"""
    from core.engine import _init_role_actions, _dispatch_role_action
    assert callable(_init_role_actions), "_init_role_actions 应为可调用函数"
    assert callable(_dispatch_role_action), "_dispatch_role_action 应为可调用函数"


# 12. _init_role_actions populates _ROLE_ACTIONS from roles config
def test_init_role_actions_populates_from_config():
    """_init_role_actions 从 roles 配置填充 _ROLE_ACTIONS 表。"""
    from core.engine import _ROLE_ACTIONS, _init_role_actions
    # 用最小 roles 配置初始化
    test_roles = {
        "test_role": {
            "on_enter": ["publish_buy_signal"],
            "on_exit": ["publish_sell_signal"],
        }
    }
    _init_role_actions(test_roles)
    assert "test_role" in _ROLE_ACTIONS, \
        "_init_role_actions 应将 test_role 填入 _ROLE_ACTIONS"
    role_entry = _ROLE_ACTIONS["test_role"]
    assert isinstance(role_entry, dict), "role 条目应为 dict"
    assert "on_enter" in role_entry, "role 条目应含 on_enter"
    assert "on_exit" in role_entry, "role 条目应含 on_exit"
    assert isinstance(role_entry["on_enter"], list), "on_enter 应为 list"
    assert isinstance(role_entry["on_exit"], list), "on_exit 应为 list"
    # on_enter 列表中的元素应为可调用（经 _resolve_action 解析）
    assert len(role_entry["on_enter"]) == 1
    assert callable(role_entry["on_enter"][0]), \
        "on_enter 元素应为可调用 action"
    assert len(role_entry["on_exit"]) == 1
    assert callable(role_entry["on_exit"][0]), \
        "on_exit 元素应为可调用 action"


# 13. engine.py defines 5 action handler lambdas
def test_engine_defines_five_action_handlers():
    """engine.py 定义 5 个 action handler（mark_out_edges_dirty 等）。"""
    src = _ENGINE_PY.read_text(encoding="utf-8")
    expected_handlers = [
        "mark_out_edges_dirty",
        "publish_enter_event",
        "publish_exit_event",
        "publish_buy_signal",
        "publish_sell_signal",
    ]
    for handler in expected_handlers:
        assert f'"{handler}"' in src, \
            f"engine.py 应定义 action handler: {handler}"
