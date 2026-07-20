from __future__ import annotations

import asyncio
import traceback
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional

from .log_capture import LogCapture
from .perf import PerfRecorder


@dataclass
class RunResult:
    """Result of a simulation run.

    Attributes:
        node_stocks: node_id -> list of stock codes
        events: list of event dicts
        signals: list of signal dicts
        perf: performance summary dict
        logs: captured log lines
        degraded_flags: list of degradation markers
        tick_count: number of ticks executed
        final_clock: final virtual_clock value
        pool_config: the pool config used
    """
    node_stocks: Dict[str, List[str]] = field(default_factory=dict)
    events: List[dict] = field(default_factory=list)
    signals: List[dict] = field(default_factory=list)
    perf: Dict[str, Any] = field(default_factory=dict)
    logs: List[str] = field(default_factory=list)
    degraded_flags: List[str] = field(default_factory=list)
    tick_count: int = 0
    final_clock: float = 0.0
    pool_config: Dict[str, Any] = field(default_factory=dict)


def _stock_to_code(stock: Any) -> str:
    if isinstance(stock, dict):
        return str(stock.get('code', stock.get('label', '')))
    if isinstance(stock, str):
        return stock
    code = getattr(stock, 'code', None)
    if code is not None:
        return str(code)
    return str(stock)


def _collect_node_stocks(simulator: Any) -> Dict[str, List[str]]:
    node_stocks: Dict[str, List[str]] = {}
    mode_state = getattr(simulator, '_mode_state', None)
    if mode_state and isinstance(mode_state, dict):
        ns = mode_state.get('node_stocks', {})
        if isinstance(ns, dict):
            for cell_id, stocks in ns.items():
                codes: List[str] = []
                if isinstance(stocks, list):
                    for s in stocks:
                        codes.append(_stock_to_code(s))
                node_stocks[str(cell_id)] = codes
    pools = getattr(simulator, 'pools', None)
    if isinstance(pools, dict) and pools:
        for cell_id, pool in pools.items():
            if cell_id in node_stocks:
                continue
            stocks = getattr(pool, 'stocks', None)
            if isinstance(stocks, list):
                codes = [_stock_to_code(s) for s in stocks]
                node_stocks[str(cell_id)] = codes
    return node_stocks


def _collect_events(simulator: Any) -> List[dict]:
    event_log = getattr(simulator, 'event_log', None)
    if not isinstance(event_log, list):
        return []
    return list(event_log)


def _collect_signals(simulator: Any) -> List[dict]:
    engine = getattr(simulator, '_engine', None)
    if engine is None:
        return []
    sig_events = getattr(engine, '_signal_events', None)
    if not isinstance(sig_events, list):
        return []
    return [dict(s) for s in sig_events]


def _build_empty_result(pool_config: Any, logs: List[str], degraded_flags: List[str]) -> RunResult:
    return RunResult(
        node_stocks={},
        events=[],
        signals=[],
        perf={},
        logs=logs,
        degraded_flags=degraded_flags,
        tick_count=0,
        final_clock=0.0,
        pool_config=pool_config if isinstance(pool_config, dict) else {},
    )


def run(pool_config, dataset=None, ticks: int = 1, seed: int = 42) -> RunResult:
    """Synchronous entry: build RuntimeSimulator, run N ticks, collect results."""
    logs: List[str] = []
    degraded_flags: List[str] = []
    perf_recorder = PerfRecorder()
    log_capture = LogCapture()

    simulator: Optional[Any] = None
    node_stocks: Dict[str, List[str]] = {}
    events: List[dict] = []
    signals: List[dict] = []
    perf: Dict[str, Any] = {}
    final_clock: float = 0.0

    try:
        with log_capture:
            try:
                from meta_core.core.runtime_mode_module import RuntimeSimulator
            except ImportError as e:
                logs.append(f"BUG: Failed to import RuntimeSimulator: {e}")
                logs.append(traceback.format_exc())
                return _build_empty_result(pool_config, logs, degraded_flags)

            try:
                simulator = RuntimeSimulator(pool_config, seed=seed)
            except Exception as e:
                logs.append(f"BUG: Failed to construct RuntimeSimulator: {e}")
                logs.append(traceback.format_exc())
                return _build_empty_result(pool_config, logs, degraded_flags)

            try:
                simulator.initialize()
            except Exception as e:
                logs.append(f"BUG: simulator.initialize() failed: {e}")
                logs.append(traceback.format_exc())
                try:
                    simulator._ini = True
                except Exception:
                    pass

            for tick_seq in range(ticks):
                perf_recorder.begin_tick(tick_seq)
                try:
                    simulator.step()
                except Exception as e:
                    logs.append(f"BUG: tick {tick_seq} failed: {e}")
                    logs.append(traceback.format_exc())
                perf_recorder.end_tick(tick_seq)

            try:
                node_stocks = _collect_node_stocks(simulator)
            except Exception as e:
                logs.append(f"BUG: _collect_node_stocks failed: {e}")
                logs.append(traceback.format_exc())

            try:
                events = _collect_events(simulator)
            except Exception as e:
                logs.append(f"BUG: _collect_events failed: {e}")
                logs.append(traceback.format_exc())

            try:
                signals = _collect_signals(simulator)
            except Exception as e:
                logs.append(f"BUG: _collect_signals failed: {e}")
                logs.append(traceback.format_exc())

            final_clock = float(getattr(simulator, 'clock', 0.0))

            try:
                perf = perf_recorder.summary()
            except Exception as e:
                logs.append(f"BUG: perf_recorder.summary() failed: {e}")
                logs.append(traceback.format_exc())
                perf = {}
            perf['tick_count'] = ticks
            perf['events_emitted'] = len(events)

            try:
                perf_summary = getattr(simulator, 'perf_summary', None)
                if callable(perf_summary):
                    sim_perf = perf_summary()
                    if isinstance(sim_perf, dict):
                        perf.update(sim_perf)
            except Exception as e:
                logs.append(f"BUG: simulator.perf_summary() failed: {e}")
                logs.append(traceback.format_exc())

            log_lines = log_capture.get_lines()
            logs.extend(log_lines)

            for line in log_lines:
                if 'degraded=True' in line:
                    degraded_flags.append(line)

    except Exception as e:
        logs.append(f"BUG: driver.run() outer exception: {e}")
        logs.append(traceback.format_exc())

    return RunResult(
        node_stocks=node_stocks,
        events=events,
        signals=signals,
        perf=perf,
        logs=logs,
        degraded_flags=degraded_flags,
        tick_count=ticks,
        final_clock=final_clock,
        pool_config=pool_config if isinstance(pool_config, dict) else {},
    )


async def run_async(pool_config, dataset=None, ticks: int = 1, seed: int = 42) -> RunResult:
    """Async entry for event loop contexts."""
    return await asyncio.to_thread(run, pool_config, dataset, ticks, seed)
