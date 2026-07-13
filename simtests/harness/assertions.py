from __future__ import annotations

from typing import List, Optional, Any


def _get_logs(result: Any, logs: Optional[List[str]] = None) -> List[str]:
    if logs is not None:
        return logs
    if result is not None and hasattr(result, 'logs'):
        return result.logs or []
    return []


def _format_failure(test_name: str, expected: Any, actual: Any, logs: List[str]) -> str:
    return f"BUG: {test_name} 期望 {expected}, 实际 {actual}, 日志={logs[-5:]}"


def assert_pool_state(result, node_id, expected_stocks, logs: Optional[List[str]] = None) -> None:
    """Assert that a node's stock list matches the expected stocks (order-insensitive)."""
    actual = list(result.node_stocks.get(node_id, []))
    expected = list(expected_stocks)
    log_lines = _get_logs(result, logs)
    if sorted(actual) != sorted(expected):
        raise AssertionError(_format_failure('assert_pool_state', expected, actual, log_lines))


def assert_event_emitted(result, event_type, flow_id=None, logs: Optional[List[str]] = None) -> None:
    """Assert that at least one event with the given event_type (and optional flow_id) was emitted.

    I82: 消费者收敛——event dict 键名统一为 event_type（DomainEvent dataclass asdict 单一真相源，
    I81 改生产者 asdict 产出 event_type 键，本处补齐遗漏的消费者）。
    """
    log_lines = _get_logs(result, logs)
    for event in result.events:
        if not isinstance(event, dict):
            continue
        if event.get('event_type', '') != event_type:
            continue
        if flow_id is not None:
            # I84：消费者收敛——flow_id 在 details dict（event_rules.json detail_mapping），非顶层键。
            ev_flow = (event.get('details') or {}).get('flow_id')
            if ev_flow != flow_id:
                continue
        return
    raise AssertionError(
        _format_failure(
            'assert_event_emitted',
            f"event(event_type={event_type}, flow_id={flow_id})",
            'not found',
            log_lines,
        )
    )


def assert_no_fallback_used(result, logs: Optional[List[str]] = None) -> None:
    """Assert no _random_filter / pass_through (silent) / unmarked degraded markers in logs."""
    log_lines = _get_logs(result, logs)
    bad_markers = ['_random_filter', 'pass_through (silent)']
    found: List[str] = []
    for line in log_lines:
        for marker in bad_markers:
            if marker in line:
                found.append(line)
        if 'degraded=True' in line and 'test_only' not in line and 'expected' not in line:
            found.append(line)
    if found:
        raise AssertionError(
            _format_failure('assert_no_fallback_used', 'no fallback markers', found, log_lines)
        )


def assert_perf_within(result, total_sec, per_tick_ms, mem_mb, logs: Optional[List[str]] = None) -> None:
    """Assert that performance metrics are within the specified thresholds."""
    log_lines = _get_logs(result, logs)
    perf = result.perf or {}
    actual_total = perf.get('total_sec', float('inf'))
    actual_per_tick = perf.get('per_tick_ms', float('inf'))
    actual_mem = perf.get('memory_peak_mb', float('inf'))
    if actual_total > total_sec or actual_per_tick > per_tick_ms or actual_mem > mem_mb:
        actual = (
            f"total_sec={actual_total}, per_tick_ms={actual_per_tick}, mem_mb={actual_mem}"
        )
        expected = (
            f"total_sec<={total_sec}, per_tick_ms<={per_tick_ms}, mem_mb<={mem_mb}"
        )
        raise AssertionError(_format_failure('assert_perf_within', expected, actual, log_lines))


def _search_field(obj: Any, field: str, value: Any) -> bool:
    if isinstance(obj, dict):
        if field in obj and obj[field] == value:
            return True
        for v in obj.values():
            if _search_field(v, field, value):
                return True
    elif isinstance(obj, list):
        for item in obj:
            if _search_field(item, field, value):
                return True
    return False


def assert_known_field_preserved(result, field, value, logs: Optional[List[str]] = None) -> None:
    """Assert that a known field's value is preserved in the pool config (not overwritten by defaults)."""
    log_lines = _get_logs(result, logs)
    pool_config = getattr(result, 'pool_config', None)
    if not isinstance(pool_config, dict):
        raise AssertionError(
            _format_failure('assert_known_field_preserved', f"{field}={value}", 'pool_config missing', log_lines)
        )
    found = _search_field(pool_config, field, value)
    if not found:
        raise AssertionError(
            _format_failure('assert_known_field_preserved', f"{field}={value}", 'not found', log_lines)
        )


def assert_propagate_deep_copy(result, src_id, tgt_id, logs: Optional[List[str]] = None) -> None:
    """Assert that source and target stock lists are independent references (deep copy, not shallow)."""
    log_lines = _get_logs(result, logs)
    if src_id not in result.node_stocks:
        raise AssertionError(
            _format_failure('assert_propagate_deep_copy', f"src_id={src_id} in node_stocks", 'missing', log_lines)
        )
    if tgt_id not in result.node_stocks:
        raise AssertionError(
            _format_failure('assert_propagate_deep_copy', f"tgt_id={tgt_id} in node_stocks", 'missing', log_lines)
        )
    src_list = result.node_stocks[src_id]
    tgt_list = result.node_stocks[tgt_id]
    if src_list is tgt_list:
        raise AssertionError(
            _format_failure('assert_propagate_deep_copy', 'independent references', 'same list object', log_lines)
        )
