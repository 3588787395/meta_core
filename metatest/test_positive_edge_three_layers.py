"""Task 18.3: edge execution three layers positive tests.

Verify the three-element functions in core/execution_module:
  - trigger_check(edge_timing_spec, now_ts, flow_state, node_dirty) -> bool
  - filter_eval(codes, filter_spec, tick_table) -> (passed, rejected)
  - propagate_apply(src_stocks, tgt_stocks, passed, propagate_spec) -> list
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

import pytest


_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent
_CORE_DIR = _PROJECT_ROOT / "core"


# 1. trigger_check returns bool
def test_trigger_check_returns_bool():
    """trigger_check(...) should return a bool value."""
    from core.execution_module import trigger_check

    result = trigger_check({"starttype": "immediate", "cxtype": "always"}, 100.0, {}, True)
    assert isinstance(result, bool), f"expected bool, got {type(result)}"
    assert result is True

    result2 = trigger_check({"starttype": "immediate", "cxtype": "always"}, 100.0, {}, False)
    assert isinstance(result2, bool)
    assert result2 is False


# 2. trigger_check node_dirty=False returns False
def test_trigger_check_node_dirty_false_returns_false():
    """node_dirty=False short-circuits trigger_check to False."""
    from core.execution_module import trigger_check

    result = trigger_check(
        edge_timing_spec={"starttype": "immediate", "cxtype": "always"},
        now_ts=100.0,
        flow_state={},
        node_dirty=False,
    )
    assert result is False
    result2 = trigger_check(
        edge_timing_spec={"starttype": "delay", "cxtype": "once", "delay": 10},
        now_ts=1000.0,
        flow_state={"start_ts": 0, "exec_count": 0},
        node_dirty=False,
    )
    assert result2 is False


# 3. trigger_check immediate+always+node_dirty=True returns True
def test_trigger_check_immediate_always():
    """starttype=immediate + cxtype=always + node_dirty=True -> True."""
    from core.execution_module import trigger_check

    result = trigger_check(
        edge_timing_spec={"starttype": "immediate", "cxtype": "always"},
        now_ts=34500.0,
        flow_state={},
        node_dirty=True,
    )
    assert result is True, "immediate+always+node_dirty=True should return True"
    assert trigger_check(
        {"starttype": "immediate", "cxtype": "always"},
        99999.9, {"exec_count": 5}, True
    ) is True


# 4. filter_eval returns (passed, rejected) tuple
def test_filter_eval_returns_tuple(tick_table):
    """filter_eval(...) returns a 2-tuple (passed_codes, rejected_codes)."""
    from core.execution_module import filter_eval

    codes = ["fz000001", "fz000002", "fz000003"]
    result = filter_eval(codes, {}, tick_table)
    assert isinstance(result, tuple), f"expected tuple, got {type(result)}"
    assert len(result) == 2
    passed, rejected = result
    assert isinstance(passed, list)
    assert isinstance(rejected, list)


# 5. filter_eval empty codes returns ([], [])
def test_filter_eval_empty_codes_returns_empty(tick_table):
    """codes=[] returns ([], [])."""
    from core.execution_module import filter_eval

    passed, rejected = filter_eval([], {}, tick_table)
    assert passed == []
    assert rejected == []
    passed2, rejected2 = filter_eval(
        [], {"enabled": True, "nset": "all", "noperate": "gt", "threshold": 0},
        tick_table,
    )
    assert passed2 == []
    assert rejected2 == []


# 6. filter_eval all passes when no filter
def test_filter_eval_all_passes_when_no_filter(tick_table):
    """filter_spec empty or enabled=False means all codes pass."""
    from core.execution_module import filter_eval

    codes = ["fz000001", "fz000002", "fz000003"]
    passed1, rejected1 = filter_eval(codes, {}, tick_table)
    assert passed1 == codes
    assert rejected1 == []
    passed2, rejected2 = filter_eval(
        codes, {"enabled": False, "nset": "all", "noperate": "gt"}, tick_table
    )
    assert passed2 == codes
    assert rejected2 == []


# 7. propagate_apply copy mode returns list(set(tgt + passed))
def test_propagate_apply_copy_mode():
    """copy mode: returns list(set(tgt + passed)), src not merged."""
    from core.execution_module import propagate_apply

    src_stocks = ["fz000001", "fz000002"]
    tgt_stocks = ["fz000003"]
    passed = ["fz000001", "fz000004"]
    result = propagate_apply(src_stocks, tgt_stocks, passed, {"mode": "copy"})
    expected = list(set(tgt_stocks + passed))
    assert sorted(result) == sorted(expected), f"copy: {result}"
    assert "fz000002" not in result


# 8. propagate_apply move mode returns list(set(tgt + passed))
def test_propagate_apply_move_mode():
    """move mode: also returns list(set(tgt + passed))."""
    from core.execution_module import propagate_apply

    src_stocks = ["fz000001", "fz000002"]
    tgt_stocks = ["fz000003"]
    passed = ["fz000001", "fz000004"]
    result = propagate_apply(src_stocks, tgt_stocks, passed, {"mode": "move"})
    expected = list(set(tgt_stocks + passed))
    assert sorted(result) == sorted(expected)


# 9. propagate_apply overwrite mode returns list(passed)
def test_propagate_apply_overwrite_mode():
    """overwrite mode: returns list(passed), replacing target pool content."""
    from core.execution_module import propagate_apply

    src_stocks = ["fz000001", "fz000002"]
    tgt_stocks = ["fz000003", "fz000099"]
    passed = ["fz000001", "fz000004"]
    result = propagate_apply(src_stocks, tgt_stocks, passed, {"mode": "overwrite"})
    assert sorted(result) == sorted(passed)
    assert "fz000003" not in result
    assert "fz000099" not in result


# 10. propagate_apply unknown mode defaults to copy
def test_propagate_apply_unknown_mode_defaults():
    """Unknown mode value defaults to copy behavior."""
    from core.execution_module import propagate_apply

    src_stocks = ["fz000001"]
    tgt_stocks = ["fz000003"]
    passed = ["fz000001", "fz000004"]
    result_unknown = propagate_apply(
        src_stocks, tgt_stocks, passed, {"mode": "nonexistent_mode"}
    )
    result_missing = propagate_apply(
        src_stocks, tgt_stocks, passed, {}
    )
    expected = list(set(tgt_stocks + passed))
    assert sorted(result_unknown) == sorted(expected), f"unknown mode: {result_unknown}"
    assert sorted(result_missing) == sorted(expected), f"missing mode: {result_missing}"


# 11. No six-layer intermediate functions
def test_no_six_layer_intermediate_functions():
    """Grep verify: no six-layer intermediate function definitions in core/.

    Forbidden functions (must all be absent):
      - _phase_dispatch
      - _phase_nset_filter
      - _dispatch_filter
      - _eval_primitive
      - _extract_prim_params_table
      - _extract_single_param
    """
    forbidden_funcs = [
        "_phase_dispatch",
        "_phase_nset_filter",
        "_dispatch_filter",
        "_eval_primitive",
        "_extract_prim_params_table",
        "_extract_single_param",
    ]
    py_files = list(_CORE_DIR.glob("*.py"))
    assert len(py_files) > 0, f"no core/*.py files in {_CORE_DIR}"
    failures = []
    for py_file in py_files:
        try:
            src = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for func_name in forbidden_funcs:
            pattern = r"\bdef\s+" + re.escape(func_name) + r"\b"
            matches = re.findall(pattern, src)
            if matches:
                failures.append(
                    f"{py_file.name}: forbidden function {func_name} ({len(matches)} defs)"
                )
    assert not failures, (
        "core/ has forbidden six-layer intermediate function definitions:\n  " +
        "\n  ".join(failures)
    )
