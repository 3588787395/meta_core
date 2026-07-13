"""统一公式引擎接口（Task 4）。

`FormulaEngine` 是无状态求值组件，按 `FilterSpec` 对股票代码集合求值，
并将结果缓存在 `PoolState.formula_results[("formula", formula_ref, bar_hash)]`。
filter 结果（passed / rejected 集合）不缓存，由调用方实时生成。
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import field
from typing import Any, Callable, Dict, List, Literal

import pandas as pd
from pydantic.dataclasses import dataclass

from .compiler import FilterSpec
from .evaluators import _lookup_builtin_script
from .formula_engine import PythonFormulaEngine
from .runtime import PoolState

logger = logging.getLogger(__name__)


def _hash_bars(bars: Dict[str, Any]) -> str:
    """对 bars 做确定性摘要，生成 bar_hash。"""
    try:
        payload = json.dumps(bars, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        payload = str(sorted(bars.items())) if isinstance(bars, dict) else str(bars)
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def _get_period_bars(state_bars: Dict[str, Any], period: str = "1d") -> Dict[str, Any]:
    """从 `PoolState.bars` 中提取指定周期的 code->bar 映射。

    兼容两种结构：
      - `bars[period][code]`（多周期 truth source）
      - `bars[code]`（已按周期归一化的映射）
    """
    if not isinstance(state_bars, dict):
        return {}
    period_bars = state_bars.get(period)
    if isinstance(period_bars, dict):
        return period_bars
    # 若顶层本身就是 code->bar，则直接返回
    return state_bars


@dataclass
class EvalContext:
    """公式求值上下文。

    字段数 = 5，满足架构约束。
    """

    mode: Literal["live", "replay", "simulation"]
    bar_hash: str
    bars: Dict[str, Dict[str, Any]]
    latest_tick: Dict[str, Any]
    period: str = "1d"
    extra: Dict[str, Any] = field(default_factory=dict)


def live_context(state: PoolState, period: str = "1d") -> EvalContext:
    """构造实盘模式求值上下文。

    - `bar_hash` 取 `PoolState.bar_hash()`（I25：收敛到唯一访问器）
    - `bars` 取 `state.bars[period]`
    - `latest_tick` 取 `state.latest_tick`
    """
    return EvalContext(
        mode="live",
        bar_hash=state.bar_hash(),
        bars=_get_period_bars(state.bars, period),
        latest_tick=state.latest_tick,
        period=period,
    )


def replay_context(
    state: PoolState, bars: Dict[str, Dict[str, Any]], bar_hash: str = ""
) -> EvalContext:
    """构造回放模式求值上下文。

    若未提供 `bar_hash`，则根据 `bars` 内容自动生成。
    """
    return EvalContext(
        mode="replay",
        bar_hash=bar_hash or _hash_bars(bars),
        bars=bars,
        latest_tick=state.latest_tick,
    )


def simulation_context(
    state: PoolState, mock_bars: Dict[str, Dict[str, Any]], bar_hash: str = ""
) -> EvalContext:
    """构造仿真模式求值上下文。

    `mock_bars` 由 mock 数据生成器当前 tick 提供；未提供 hash 时自动生成。
    """
    return EvalContext(
        mode="simulation",
        bar_hash=bar_hash or _hash_bars(mock_bars),
        bars=mock_bars,
        latest_tick=state.latest_tick,
    )


class FormulaEngine:
    """统一公式引擎。

    属性 ≤ 5、方法 ≤ 6、事件 ≤ 3：
      - 属性：state, _python_engine, _logger
      - 方法：__init__, eval, eval_scalar, _cached_eval, _eval_formula, _cache_key
      - 事件：本实现保持无事件发布（0 个），满足 ≤ 3 约束

    I54：缓存逻辑（键构造/读/写）收敛到 _cached_eval，formula 与 scalar 路径共享，
    消除 edge_executor._eval_scalar_path 中重复的 cache_key 构造与 formula_results 读写。
    """

    def __init__(self, state: PoolState, data_query: Any = None):
        self.state = state
        self._data_query = data_query
        self._python_engine = PythonFormulaEngine()
        self._logger = logging.getLogger(__name__)

    def eval(self, spec: FilterSpec, codes: List[str], ctx: EvalContext) -> Dict[str, Any]:
        """公式求值路径：委托 _eval_formula，缓存经 _cached_eval 统一管理。

        I53：filter_type 降级为元数据，evaluator_type 为唯一运行期分派键。
        I54：缓存逻辑收敛到 _cached_eval，与 scalar 路径共享。
        """
        return self._cached_eval(
            spec, codes, ctx,
            lambda c, x: self._eval_formula(spec.formula_ref, c, x),
            writeback=True,
        )

    def eval_scalar(
        self,
        spec: FilterSpec,
        codes: List[str],
        ctx: EvalContext,
        evaluator_fn: Callable[[List[str], EvalContext], Dict[str, Any]],
    ) -> Dict[str, Any]:
        """标量求值路径：委托外部 evaluator_fn，缓存经 _cached_eval 统一管理。

        I54：scalar 路径缓存收敛到 FormulaEngine，消除 edge_executor 中
        重复的 cache_key 构造与 formula_results 读写。evaluator_fn 接收
        (codes, ctx)，返回 {code: bool}。writeback=False（标量结果不写回 tick 列）。
        """
        return self._cached_eval(spec, codes, ctx, evaluator_fn, writeback=False)

    def _cached_eval(
        self,
        spec: FilterSpec,
        codes: List[str],
        ctx: EvalContext,
        evaluator_fn: Callable[[List[str], EvalContext], Dict[str, Any]],
        writeback: bool,
    ) -> Dict[str, Any]:
        """统一缓存求值：键构造/读/写集中于此。"""
        key = ("formula", ctx.mode, spec.formula_ref, ctx.bar_hash)
        cached = self.state.formula_results.get(key)
        if cached is not None:
            return cached

        result = evaluator_fn(codes, ctx)

        if writeback and spec.formula_ref:
            for code, value in result.items():
                tick = self.state.latest_tick.get(code)
                if isinstance(tick, dict) and spec.formula_ref not in tick:
                    tick[spec.formula_ref] = value

        self.state.formula_results[key] = result
        return result

    def _eval_formula(
        self, formula_ref: str, codes: List[str], ctx: EvalContext
    ) -> Dict[str, Any]:
        """调用底层 Python 公式引擎逐只求值。"""
        formula = formula_ref or ""
        if not formula:
            return {code: None for code in codes}

        builtin_script = _lookup_builtin_script(formula)
        if builtin_script:
            formula = builtin_script

        period = getattr(ctx, 'period', '1d') or '1d'

        if self._data_query is not None:
            def fetcher(symbol: str, p: str) -> pd.DataFrame | None:
                df = self._data_query.get_kline_series(symbol, p or period)
                if len(codes) <= 3 and symbol == codes[0]:
                    import logging
                    logging.getLogger("formula_debug").warning(
                        "FETCHER symbol=%s period=%s rows=%d cols=%s df=%s",
                        symbol, p or period, len(df) if df is not None else 0,
                        list(df.columns) if df is not None and not df.empty else [],
                        df.head(3).to_string() if df is not None and not df.empty else "EMPTY"
                    )
                return df
        else:
            def fetcher(symbol: str, p: str) -> pd.DataFrame | None:
                bar = ctx.bars.get(symbol)
                if bar is None:
                    tick = ctx.latest_tick.get(symbol)
                    if isinstance(tick, dict):
                        bar = tick
                if isinstance(bar, dict):
                    return pd.DataFrame([bar])
                if isinstance(bar, list):
                    return pd.DataFrame(bar)
                return None

        try:
            batch = self._python_engine.eval_batch(
                formula, codes, period=period, data_fetcher=fetcher, args=None
            )
        except Exception as exc:
            self._logger.debug("公式求值异常: %s", exc)
            return {code: None for code in codes}

        return {code: batch.get(code) for code in codes}


__all__ = [
    "EvalContext",
    "FormulaEngine",
    "live_context",
    "replay_context",
    "simulation_context",
]
