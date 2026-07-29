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
