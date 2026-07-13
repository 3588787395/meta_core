from __future__ import annotations

import logging
import sys
import time
import tracemalloc
from collections import defaultdict
from typing import Dict, List, Any

_logger = logging.getLogger(__name__)

_PHASE_NAMES = ('gate', 'filter', 'propagate', 'callback', 'ttl')


class PerfRecorder:
    """Records wall-clock and memory performance metrics for simulation ticks."""

    def __init__(self) -> None:
        self._tick_starts: Dict[int, float] = {}
        self._tick_ends: Dict[int, float] = {}
        self._phases: Dict[str, List[float]] = defaultdict(list)
        self._start_time: float = time.perf_counter()
        self._stopped: bool = False
        self._peak_mb: float = 0.0
        try:
            tracemalloc.start()
        except ValueError as exc:
            _logger.warning("tracemalloc already started: %s", exc)

    def begin_tick(self, tick_seq: int) -> None:
        self._tick_starts[tick_seq] = time.perf_counter()

    def end_tick(self, tick_seq: int) -> None:
        self._tick_ends[tick_seq] = time.perf_counter()

    def record_phase(self, phase: str, duration_ms: float) -> None:
        self._phases[phase].append(float(duration_ms))

    @property
    def memory_peak_mb(self) -> float:
        if not self._stopped:
            try:
                _current, peak = tracemalloc.get_traced_memory()
                self._peak_mb = max(self._peak_mb, peak / (1024.0 * 1024.0))
            except Exception as exc:
                _logger.warning("tracemalloc get_traced_memory failed: %s", exc)
        return self._peak_mb

    def summary(self) -> Dict[str, Any]:
        if not self._stopped:
            self.memory_peak_mb
            try:
                tracemalloc.stop()
            except Exception as exc:
                _logger.warning("tracemalloc stop failed: %s", exc)
            self._stopped = True

        total_sec: float = time.perf_counter() - self._start_time
        tick_count: int = len(self._tick_starts)
        per_tick_ms: float = (total_sec * 1000.0 / tick_count) if tick_count > 0 else 0.0

        result: Dict[str, Any] = {
            'total_sec': total_sec,
            'per_tick_ms': per_tick_ms,
            'memory_peak_mb': self._peak_mb,
            'events_emitted': 0,
        }
        for phase in _PHASE_NAMES:
            durations = self._phases.get(phase, [])
            result[f'{phase}_ms'] = (sum(durations) / len(durations)) if durations else 0.0
        return result

    def __del__(self) -> None:
        if not self._stopped:
            try:
                tracemalloc.stop()
                self._stopped = True
            except Exception as exc:
                sys.stderr.write(f"PerfRecorder cleanup error: {exc}\n")
