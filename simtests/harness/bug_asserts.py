from __future__ import annotations

from typing import List, Optional, Any, Iterable


def _get_logs(result: Any, logs: Optional[List[str]] = None) -> List[str]:
    if logs is not None:
        return logs
    if result is not None and hasattr(result, 'logs'):
        return result.logs or []
    return []


def _format_failure(test_name: str, expected: Any, actual: Any, logs: List[str]) -> str:
    return f"BUG: {test_name} 期望 {expected}, 实际 {actual}, 日志={logs[-5:]}"


def assert_no_random_filter(result, logs: Optional[List[str]] = None) -> None:
    """Fail if 'random_filter' appears in any log line."""
    log_lines = _get_logs(result, logs)
    found = [line for line in log_lines if 'random_filter' in line]
    if found:
        raise AssertionError(
            _format_failure('assert_no_random_filter', "no 'random_filter' in logs", found, log_lines)
        )


def assert_no_silent_pass_through(result, logs: Optional[List[str]] = None) -> None:
    """Fail if 'pass_through (silent)' appears in any log line."""
    log_lines = _get_logs(result, logs)
    found = [line for line in log_lines if 'pass_through (silent)' in line]
    if found:
        raise AssertionError(
            _format_failure('assert_no_silent_pass_through', "no 'pass_through (silent)' in logs", found, log_lines)
        )


def assert_no_unhandled_exception(result, logs: Optional[List[str]] = None) -> None:
    """Fail if 'Traceback' appears in any log line."""
    log_lines = _get_logs(result, logs)
    found = [line for line in log_lines if 'Traceback' in line]
    if found:
        raise AssertionError(
            _format_failure('assert_no_unhandled_exception', "no 'Traceback' in logs", found, log_lines)
        )


def assert_event_has_required_keys(event, keys: Iterable[str], logs: Optional[List[str]] = None) -> None:
    """Fail if any required key is missing from the event dict."""
    log_lines = _get_logs(None, logs)
    keys_list = list(keys)
    if not isinstance(event, dict):
        raise AssertionError(
            _format_failure('assert_event_has_required_keys', keys_list, f'event is not a dict: {type(event)}', log_lines)
        )
    missing = [k for k in keys_list if k not in event]
    if missing:
        raise AssertionError(
            _format_failure('assert_event_has_required_keys', keys_list, f"missing {missing}, event={event}", log_lines)
        )


def assert_strict_equal(actual, expected, msg: str = '', logs: Optional[List[str]] = None) -> None:
    """Fail if actual != expected (strict equality)."""
    log_lines = _get_logs(None, logs)
    if actual != expected:
        raise AssertionError(
            _format_failure(
                f'assert_strict_equal{(" " + msg) if msg else ""}',
                repr(expected),
                repr(actual),
                log_lines,
            )
        )
