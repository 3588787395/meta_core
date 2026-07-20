from __future__ import annotations

import json
import logging
import sys
from typing import List, Dict, Any, Optional, Tuple


_DEFAULT_LOGGERS: Tuple[str, ...] = (
    'engine', 'simulator', 'tq_adapter', 'builtins',
    'meta_core.engine', 'meta_core.core.runtime_mode_module',
    'meta_core.services.tq_adapter', 'meta_core.native.builtins',
    'meta_core.core.engine', 'meta_core.core.execution_module', 'core.engine',
)


class _CaptureHandler(logging.Handler):
    """Logging handler that captures records into a LogCapture instance."""

    def __init__(self, capture: "LogCapture") -> None:
        super().__init__(logging.DEBUG)
        self._capture = capture

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry: Dict[str, Any] = {
                'timestamp': record.created,
                'level': record.levelname,
                'component': record.name,
                'message': record.getMessage(),
                'tick_seq': getattr(record, 'tick_seq', None),
                'node_id': getattr(record, 'node_id', None),
            }
            self._capture._entries.append(entry)
        except Exception as exc:
            sys.stderr.write(f"LogCapture emit error: {exc}\n")


class LogCapture:
    """Context manager that captures log output from engine/simulator/tq_adapter/builtins loggers."""

    def __init__(
        self,
        components: Tuple[str, ...] = ('engine', 'simulator', 'tq_adapter', 'builtins'),
    ) -> None:
        self._components: Tuple[str, ...] = tuple(components)
        self._entries: List[Dict[str, Any]] = []
        self._handler: Optional[_CaptureHandler] = None
        self._attached_loggers: List[logging.Logger] = []

    def __enter__(self) -> "LogCapture":
        self._handler = _CaptureHandler(self)
        for name in _DEFAULT_LOGGERS:
            logger = logging.getLogger(name)
            logger.addHandler(self._handler)
            if logger.level > logging.DEBUG or logger.level == logging.NOTSET:
                logger.setLevel(logging.DEBUG)
            self._attached_loggers.append(logger)
        return self

    def __exit__(self, *args: Any) -> None:
        if self._handler is not None:
            for logger in self._attached_loggers:
                logger.removeHandler(self._handler)
            self._attached_loggers.clear()
            self._handler = None

    @property
    def entries(self) -> List[Dict[str, Any]]:
        return list(self._entries)

    def filter(
        self,
        component: Optional[str] = None,
        level: Optional[str] = None,
        tick_seq: Optional[int] = None,
        node_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        for entry in self._entries:
            if component is not None and component not in str(entry.get('component', '')):
                continue
            if level is not None and entry.get('level') != level:
                continue
            if tick_seq is not None and entry.get('tick_seq') != tick_seq:
                continue
            if node_id is not None and entry.get('node_id') != node_id:
                continue
            result.append(entry)
        return result

    def dump_to_file(self, path: str) -> None:
        with open(path, 'w', encoding='utf-8') as f:
            for entry in self._entries:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + '\n')

    def get_lines(self) -> List[str]:
        lines: List[str] = []
        for entry in self._entries:
            level = entry.get('level', '')
            comp = entry.get('component', '')
            msg = entry.get('message', '')
            lines.append(f"[{level}] {comp}: {msg}")
        return lines
