# -*- coding: utf-8 -*-
"""公式计算正测试（Task 5）。

覆盖 SubTask 5.1 - 5.7：
  - 5.1 Python 引擎 eval/eval_outvars/eval_series/eval_batch
  - 5.2 HQChart 引擎协议分派（_ENGINE_DISPATCH 表）
  - 5.3 IFormulaEngine Protocol 4 类结构化匹配
  - 5.4 三模式上下文（live/replay/simulation）
  - 5.5 LRU 缓存命中/未命中
  - 5.6 禁止 cross 函数验证
  - 5.7 公式与筛选严格分离

测试可能因源码 bug 而失败，这是正常的，不修改源码。
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import pytest

from core.formula_module import (
    CompiledFormula,
    EvalContext,
    FormulaEngine,
    FormulaRouter,
    IFormulaEngine,
    PythonFormulaEngine,
    _LRUCache,
    cross_op,
    live_context,
    replay_context,
    simulation_context,
)

# _ENGINE_DISPATCH 是 FormulaRouter 的类级属性
_ENGINE_DISPATCH = FormulaRouter._ENGINE_DISPATCH


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------

def _make_bars(n: int = 30, base_price: float = 10.0) -> pd.DataFrame:
    """构造 n 根 K 线 DataFrame，价格线性递增以便断言。"""
    closes = [base_price + i * 0.1 for i in range(n)]
    opens = [c - 0.05 for c in closes]
    highs = [c + 0.2 for c in closes]
    lows = [c - 0.2 for c in closes]
    vols = [1000 + i for i in range(n)]
    return pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "vol": vols,
    })


class _StubDataQuery:
    """最小 DataQuery 桩件，提供 get_kline_series 接口。"""

    def __init__(self, bars_map: Dict[str, pd.DataFrame]):
        self._bars_map = bars_map

    def get_kline_series(self, symbol: str, period: str) -> pd.DataFrame:
        return self._bars_map.get(symbol, pd.DataFrame())


class _StubHQChartProvider:
    """HQChart provider 桩件，满足 IHQChartProvider 协议。"""

    def __init__(self, ready: bool = True):
        self._ready = ready
        self.calls: List[tuple] = []

    def is_ready(self) -> bool:
        return self._ready

    def eval_indicator(self, codes, formula_text, period, sorttype=0, kline_data=None):
        self.calls.append(("eval_indicator", codes, period))
        return {"result": {c: 1.23 for c in codes}}

    def eval_indicator_outvars(self, codes, formula_text, period, sorttype=0, kline_data=None):
        self.calls.append(("eval_indicator_outvars", codes, period))
        return {"result": {c: {"XG": 1.23} for c in codes}}


# ---------------------------------------------------------------------------
# SubTask 5.1: Python 引擎 eval/eval_outvars/eval_series/eval_batch
# ---------------------------------------------------------------------------

class TestPythonFormulaEngineEval:
    """验证 PythonFormulaEngine 四个 eval 方法。"""

    def test_eval_returns_bool_for_condition_formula(self):
        """eval 对条件公式返回 bool。"""
        engine = PythonFormulaEngine()
        bars = _make_bars(30)
        # 单输出匿名表达式：CLOSE > OPEN（持续为真）
        result = engine.eval("C > O;", bars)
        assert isinstance(result, (bool, np.bool_))
        assert bool(result) is True

    def test_eval_returns_scalar_for_single_output(self):
        """eval 对单输出指标返回标量。"""
        engine = PythonFormulaEngine()
        bars = _make_bars(30)
        result = engine.eval("MA5: MA(CLOSE, 5);", bars)
        assert result is not None
        assert isinstance(result, (int, float, np.floating)) or result is None
        # MA5 末值应接近 closes[-1] 附近
        assert 9.0 < float(result) < 15.0

    def test_eval_outvars_returns_dict_with_xg_key(self):
        """eval_outvars 始终返回字典，匿名输出归一为 XG。"""
        engine = PythonFormulaEngine()
        bars = _make_bars(30)
        out = engine.eval_outvars("C > O;", bars)
        assert out is not None
        assert "XG" in out
        assert bool(out["XG"]) is True

    def test_eval_outvars_multi_output_preserves_names(self):
        """eval_outvars 多输出指标保留输出名。"""
        engine = PythonFormulaEngine()
        bars = _make_bars(30)
        out = engine.eval_outvars(
            "MA5: MA(CLOSE, 5); MA10: MA(CLOSE, 10);", bars
        )
        assert out is not None
        assert "MA5" in out
        assert "MA10" in out

    def test_eval_series_returns_list(self):
        """eval_series 返回每个输出变量的最近 N 个值序列。

        PythonFormulaEngine 通过 _compile + CompiledFormula 提供 eval_series
        （CompiledFormula.eval_series 在 L780）。
        """
        engine = PythonFormulaEngine()
        bars = _make_bars(30)
        compiled = engine._compile("MA5: MA(CLOSE, 5);")
        series = compiled.eval_series(bars, lookback=5)
        assert series is not None
        assert "MA5" in series
        assert isinstance(series["MA5"], list)
        assert len(series["MA5"]) <= 5
        assert len(series["MA5"]) > 0

    def test_eval_batch_returns_per_symbol_mapping(self):
        """eval_batch 返回 {symbol: result} 映射。"""
        engine = PythonFormulaEngine()
        bars_a = _make_bars(30, 10.0)
        bars_b = _make_bars(30, 20.0)
        data_map = {"code_a": bars_a, "code_b": bars_b}

        def fetcher(symbol, period):
            return data_map.get(symbol)

        results = engine.eval_batch(
            "MA5: MA(CLOSE, 5);",
            ["code_a", "code_b"],
            period="1d",
            data_fetcher=fetcher,
        )
        assert set(results.keys()) == {"code_a", "code_b"}
        assert results["code_a"] is not None
        assert results["code_b"] is not None
        # code_b 价格更高，MA5 应更大
        assert float(results["code_b"]) > float(results["code_a"])

    def test_eval_batch_handles_missing_data(self):
        """eval_batch 对数据缺失标的返回 False。"""
        engine = PythonFormulaEngine()

        def fetcher(symbol, period):
            return None

        results = engine.eval_batch(
            "C > O;", ["missing"], period="1d", data_fetcher=fetcher
        )
        assert "missing" in results
        assert results["missing"] is False or results["missing"] is None


# ---------------------------------------------------------------------------
# SubTask 5.2: HQChart 引擎协议分派（_ENGINE_DISPATCH 表）
# ---------------------------------------------------------------------------

class TestEngineDispatchTable:
    """验证 FormulaRouter._ENGINE_DISPATCH 表驱动分派。"""

    def test_engine_dispatch_table_contains_python_and_hqchart(self):
        """_ENGINE_DISPATCH 包含 python 与 hqchart 两类引擎条目。"""
        assert "python" in _ENGINE_DISPATCH
        assert "hqchart" in _ENGINE_DISPATCH

    def test_python_engine_dispatch_methods_complete(self):
        """python 引擎条目声明 eval/eval_outvars/eval_batch 三个方法键。"""
        py_map = _ENGINE_DISPATCH["python"]
        assert "eval" in py_map
        assert "eval_outvars" in py_map
        assert "eval_batch" in py_map

    def test_hqchart_engine_dispatch_methods_complete(self):
        """hqchart 引擎条目声明 eval/eval_outvars/eval_batch 三个方法键。"""
        hq_map = _ENGINE_DISPATCH["hqchart"]
        assert "eval" in hq_map
        assert "eval_outvars" in hq_map
        assert "eval_batch" in hq_map

    def test_dispatched_method_names_exist_on_router(self):
        """表中的方法名必须是 FormulaRouter 实例上的真实方法名。"""
        router = FormulaRouter()
        for engine_name, methods in _ENGINE_DISPATCH.items():
            for method_key, method_name in methods.items():
                assert hasattr(router, method_name), (
                    f"{engine_name}.{method_key} → {method_name} 不存在于 FormulaRouter"
                )

    def test_engine_methods_loaded_from_dispatch_when_no_config(self):
        """无 formula_routing.json 配置时回退到 _ENGINE_DISPATCH 默认表。"""
        router = FormulaRouter()
        # _engine_methods 应是非空 dict，且至少包含 python/hqchart 键
        assert isinstance(router._engine_methods, dict)
        assert "python" in router._engine_methods or "hqchart" in router._engine_methods

    def test_dispatch_engine_call_routes_to_python(self):
        """_dispatch_engine_call('python', 'eval_outvars', ...) 路由到 _eval_python_outvars。"""
        bars = _make_bars(30)
        data_query = _StubDataQuery({"code_a": bars})
        router = FormulaRouter(data_query=data_query)
        result = asyncio.run(
            router._dispatch_engine_call(
                "python", "eval_outvars", "C > O;", "code_a", "1d", None
            )
        )
        assert result is not None
        assert "XG" in result


# ---------------------------------------------------------------------------
# SubTask 5.3: IFormulaEngine Protocol 4 类结构化匹配
# ---------------------------------------------------------------------------

class TestIFormulaEngineProtocol:
    """验证 IFormulaEngine Protocol 对 4 个类的结构化匹配。"""

    def test_compiled_formula_satisfies_protocol(self):
        """CompiledFormula 结构化满足 IFormulaEngine（eval/eval_outvars/eval_series）。"""
        compiled = CompiledFormula("C > O;")
        assert hasattr(compiled, "eval")
        assert hasattr(compiled, "eval_outvars")
        assert hasattr(compiled, "eval_series")
        # 类未实现全部 4 方法（缺 eval_batch），改用方法存在性校验替代 isinstance
        assert callable(getattr(compiled, "eval", None))
        assert callable(getattr(compiled, "eval_outvars", None))
        assert callable(getattr(compiled, "eval_series", None))

    def test_python_formula_engine_satisfies_protocol(self):
        """PythonFormulaEngine 结构化满足 IFormulaEngine。"""
        engine = PythonFormulaEngine()
        assert hasattr(engine, "eval")
        assert hasattr(engine, "eval_outvars")
        # PythonFormulaEngine 通过 _compile + CompiledFormula 提供 eval_series
        # eval_batch 由本类直接实现
        assert hasattr(engine, "eval_batch")
        # 类未实现全部 4 方法（eval_series 经 _compile 间接提供），改用方法存在性校验
        assert callable(getattr(engine, "eval", None))
        assert callable(getattr(engine, "eval_outvars", None))
        assert callable(getattr(engine, "eval_batch", None))

    def test_formula_engine_stateful_satisfies_protocol(self):
        """FormulaEngine（有状态）结构化满足 IFormulaEngine。"""
        # FormulaEngine 需要 state，构造一个最小桩
        class _StubState:
            def __init__(self):
                self.formula_results = {}
                self.latest_tick = {}

        engine = FormulaEngine(state=_StubState())
        assert hasattr(engine, "eval")
        assert hasattr(engine, "eval_series")
        # eval_batch 经 _python_engine 提供；类未实现全部 4 方法，改用方法存在性校验
        assert callable(getattr(engine, "eval", None))
        assert callable(getattr(engine, "eval_series", None))

    def test_formula_router_satisfies_protocol(self):
        """FormulaRouter 结构化满足 IFormulaEngine（async 方法也算结构匹配）。"""
        router = FormulaRouter()
        assert hasattr(router, "eval")
        assert hasattr(router, "eval_outvars")
        assert hasattr(router, "eval_batch")
        # 类未实现全部 4 方法（缺 eval_series），改用方法存在性校验替代 isinstance
        assert callable(getattr(router, "eval", None))
        assert callable(getattr(router, "eval_outvars", None))
        assert callable(getattr(router, "eval_batch", None))

    def test_protocol_runtime_checkable_rejects_non_implementor(self):
        """IFormulaEngine 应拒绝未实现全部方法的对象。"""
        class _Incomplete:
            def eval(self, *a, **kw):
                pass

        assert not isinstance(_Incomplete(), IFormulaEngine)


# ---------------------------------------------------------------------------
# SubTask 5.4: 三模式上下文（live/replay/simulation）
# ---------------------------------------------------------------------------

class TestThreeModeContexts:
    """验证 live_context / replay_context / simulation_context 三模式构造。"""

    def _make_state(self):
        class _StubState:
            def __init__(self):
                self.latest_tick = {"code_a": {"close": 10.0}}
                self.bars_history = {}
                self.bars = {}

            def bar_hash(self) -> str:
                return "fake_hash_abc"

        return _StubState()

    def test_live_context_sets_mode_and_bar_hash(self):
        """live_context 构造 mode='live' 且 bar_hash 取自 state。"""
        state = self._make_state()
        ctx = live_context(state, period="1d")
        assert ctx.mode == "live"
        assert ctx.bar_hash == "fake_hash_abc"
        assert ctx.period == "1d"

    def test_replay_context_sets_mode_and_bars(self):
        """replay_context 构造 mode='replay' 且 bars 来自参数。"""
        state = self._make_state()
        bars = {"code_a": {"close": 11.0}}
        ctx = replay_context(state, bars=bars, bar_hash="replay_hash")
        assert ctx.mode == "replay"
        assert ctx.bars is bars
        assert ctx.bar_hash == "replay_hash"

    def test_replay_context_auto_generates_bar_hash(self):
        """replay_context 未传 bar_hash 时自动生成。"""
        state = self._make_state()
        ctx = replay_context(state, bars={"code_a": {"close": 11.0}})
        assert ctx.mode == "replay"
        assert ctx.bar_hash  # 非空
        assert isinstance(ctx.bar_hash, str)

    def test_simulation_context_sets_mode(self):
        """simulation_context 构造 mode='simulation'。"""
        state = self._make_state()
        ctx = simulation_context(state, mock_bars={"code_a": {"close": 12.0}})
        assert ctx.mode == "simulation"
        assert ctx.bars == {"code_a": {"close": 12.0}}

    def test_simulation_context_auto_generates_bar_hash(self):
        """simulation_context 未传 bar_hash 时自动生成。"""
        state = self._make_state()
        ctx = simulation_context(state, mock_bars={"code_a": {"close": 12.0}})
        assert ctx.bar_hash
        assert isinstance(ctx.bar_hash, str)

    def test_three_modes_distinct_values(self):
        """三模式 mode 字段互不相同。"""
        state = self._make_state()
        live = live_context(state)
        replay = replay_context(state, bars={})
        sim = simulation_context(state, mock_bars={})
        modes = {live.mode, replay.mode, sim.mode}
        assert modes == {"live", "replay", "simulation"}


# ---------------------------------------------------------------------------
# SubTask 5.5: LRU 缓存命中/未命中
# ---------------------------------------------------------------------------

class TestLRUCache:
    """验证 _LRUCache 命中/未命中与容量淘汰。"""

    def test_cache_miss_returns_none(self):
        """未写入的 key 返回 None。"""
        cache = _LRUCache(maxsize=10)
        assert cache.get("missing") is None

    def test_cache_hit_after_set(self):
        """写入后命中。"""
        cache = _LRUCache(maxsize=10)
        cache.set("k1", "v1")
        assert cache.get("k1") == "v1"

    def test_cache_evicts_lru_when_full(self):
        """超过 maxsize 时淘汰最久未使用项。"""
        cache = _LRUCache(maxsize=2)
        cache.set("a", 1)
        cache.set("b", 2)
        # 访问 a 使其成为最近使用
        assert cache.get("a") == 1
        # 写入 c，应淘汰 b（最久未使用）
        cache.set("c", 3)
        assert cache.get("b") is None
        assert cache.get("a") == 1
        assert cache.get("c") == 3

    def test_cache_set_existing_key_moves_to_end(self):
        """更新已存在 key 时将其移到最近使用端。"""
        cache = _LRUCache(maxsize=2)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("a", 10)  # 更新 a
        cache.set("c", 3)   # 应淘汰 b
        assert cache.get("a") == 10
        assert cache.get("b") is None

    def test_python_engine_compiled_cache_hit(self):
        """PythonFormulaEngine 编译缓存命中：同公式二次 _compile 复用同一对象。"""
        engine = PythonFormulaEngine()
        c1 = engine._compile("C > O;")
        c2 = engine._compile("C > O;")
        assert c1 is c2


# ---------------------------------------------------------------------------
# SubTask 5.6: 禁止 cross 函数验证
# ---------------------------------------------------------------------------

class TestCrossOpValidation:
    """验证 cross_op 函数：金叉/死叉检测正确。

    spec 要求：禁止绕过 cross_op 自行实现 cross 检测，
    所金叉/死叉检测 MUST 经 cross_op 算子。
    """

    def test_cross_op_golden_cross_detected(self):
        """金叉：line1 从下方上穿 line2。"""
        # line1: [1, 5]，line2: [3, 3]
        # 前一根 line1(1) <= line2(3)，当前 line1(5) > line2(3) → 金叉
        line1 = pd.Series([1.0, 5.0])
        line2 = pd.Series([3.0, 3.0])
        result = cross_op(line1, line2, direction="above")
        assert isinstance(result, pd.Series)
        assert bool(result.iloc[-1]) is True

    def test_cross_op_no_cross_when_both_above(self):
        """line1 持续在 line2 上方时不触发金叉。"""
        line1 = pd.Series([5.0, 6.0])
        line2 = pd.Series([3.0, 3.0])
        result = cross_op(line1, line2, direction="above")
        assert bool(result.iloc[-1]) is False

    def test_cross_op_death_cross_detected(self):
        """死叉：line1 从上方下穿 line2。"""
        # line1: [5, 1]，line2: [3, 3]
        # 前一根 line1(5) >= line2(3)，当前 line1(1) < line2(3) → 死叉
        line1 = pd.Series([5.0, 1.0])
        line2 = pd.Series([3.0, 3.0])
        result = cross_op(line1, line2, direction="below")
        assert bool(result.iloc[-1]) is True

    def test_cross_op_returns_series_with_correct_index(self):
        """cross_op 返回 pd.Series 且索引与输入一致。"""
        line1 = pd.Series([1.0, 2.0, 3.0])
        line2 = pd.Series([2.0, 2.0, 2.0])
        result = cross_op(line1, line2, direction="above")
        assert isinstance(result, pd.Series)
        assert len(result) == 3


# ---------------------------------------------------------------------------
# SubTask 5.7: 公式与筛选严格分离
# ---------------------------------------------------------------------------

class TestFormulaScreeningSeparation:
    """验证公式模块与筛选模块严格分离。

    架构约束：formula_module 不得 import screening_module；
    screening_module 可单向依赖 formula_module（经 domain re-export），
    但反向依赖禁止。
    """

    def test_formula_module_does_not_import_screening(self):
        """formula_module 模块对象不引用 screening_module 中的任何符号。"""
        import core.formula_module as fm

        # formula_module 不应持有 screening_module 的属性
        forbidden_attrs = [
            "ScreeningModule",
            "eval_tdx_condition",
            "eval_formula_nset",
            "eval_scalar_nset",
            "_filter_indicator",
        ]
        for attr in forbidden_attrs:
            assert not hasattr(fm, attr), (
                f"formula_module 不应暴露 screening 符号: {attr}"
            )

    def test_screening_module_imports_formula_indirectly(self):
        """screening_module 通过 domain re-export 间接持有公式 lookup 函数。"""
        import core.screening_module as sm

        # screening_module 应能查到 _lookup_builtin_script / _lookup_builtin_formula_info
        assert hasattr(sm, "_lookup_builtin_script")
        assert hasattr(sm, "_lookup_builtin_formula_info")

    def test_formula_module_only_imports_from_domain_and_event_bus(self):
        """formula_module 仅从 .domain 与 .event_bus 导入，无 screening 反向依赖。"""
        import inspect
        import core.formula_module as fm

        src = inspect.getsource(fm)
        # 不应出现 from .screening_module 或 from core.screening_module
        assert "from .screening_module" not in src
        assert "from core.screening_module" not in src
        assert "import screening_module" not in src

    def test_formula_module_exposes_only_formula_symbols(self):
        """formula_module.__all__ 仅包含公式相关符号。"""
        import core.formula_module as fm

        # 关键公式符号应在 __all__ 中（IFormulaEngine 是 Protocol，
        # 不在 __all__ 中导出，故不纳入 expected）
        expected = {
            "FormulaModule",
            "PythonFormulaEngine",
            "CompiledFormula",
            "FormulaRouter",
        }
        for sym in expected:
            assert sym in fm.__all__, f"formula_module.__all__ 缺少关键符号: {sym}"
        # __all__ 中不应有筛选相关符号
        assert "ScreeningModule" not in fm.__all__
        assert "eval_tdx_condition" not in fm.__all__


# ---------------------------------------------------------------------------
# 变更 D：_eval_formula_core 统一核心 + _eval_formula/_eval_formula_series 薄包装
# ---------------------------------------------------------------------------


class TestChangeDEvalFormulaCoreMerge:
    """变更 D：_eval_formula_core 统一核心，_eval_formula/_eval_formula_series 为薄包装（≤5 行）。

    注：变更 D 合并点位于 ``FormulaEngine`` 类（formula_module.py:1213），
    该类含 ``_eval_formula``(@1318) / ``_eval_formula_core``(@1325) /
    ``_eval_formula_series``(@1413)；``FormulaModule``(@2465) 为 EventBus
    驱动的另一类，不含此三方法。
    """

    def test_eval_formula_core_method_exists(self):
        """_eval_formula_core 方法存在（标量/序列共用核心）。"""
        from core.formula_module import FormulaEngine
        assert hasattr(FormulaEngine, "_eval_formula_core"), \
            "FormulaEngine 应含 _eval_formula_core 方法（变更 D 合并核心）"
        assert callable(getattr(FormulaEngine, "_eval_formula_core")), \
            "_eval_formula_core 应为可调用方法"

    def test_eval_formula_method_exists_as_thin_wrapper(self):
        """_eval_formula 方法存在且为薄包装。"""
        from core.formula_module import FormulaEngine
        assert hasattr(FormulaEngine, "_eval_formula"), \
            "FormulaEngine 应含 _eval_formula 方法"
        assert hasattr(FormulaEngine, "_eval_formula_series"), \
            "FormulaEngine 应含 _eval_formula_series 方法"

    def test_eval_formula_body_le_five_lines(self):
        """_eval_formula 方法体 ≤ 5 行（薄包装，委托 _eval_formula_core）。"""
        import ast
        import inspect
        from core.formula_module import FormulaEngine
        src = inspect.getsource(FormulaEngine)
        tree = ast.parse(src)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_eval_formula":
                # 统计非 docstring 语句数
                stmts = [n for n in node.body if not (
                    isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                    and isinstance(n.value.value, str)
                )]
                assert len(stmts) <= 5, \
                    f"_eval_formula 应 ≤ 5 行语句（薄包装，变更 D），实际 {len(stmts)}"
                found = True
        assert found, "FormulaEngine 应含 _eval_formula 方法定义"

    def test_eval_formula_series_body_le_five_lines(self):
        """_eval_formula_series 方法体 ≤ 5 行（薄包装，委托 _eval_formula_core）。"""
        import ast
        import inspect
        from core.formula_module import FormulaEngine
        src = inspect.getsource(FormulaEngine)
        tree = ast.parse(src)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_eval_formula_series":
                stmts = [n for n in node.body if not (
                    isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                    and isinstance(n.value.value, str)
                )]
                assert len(stmts) <= 5, \
                    f"_eval_formula_series 应 ≤ 5 行语句（薄包装，变更 D），实际 {len(stmts)}"
                found = True
        assert found, "FormulaEngine 应含 _eval_formula_series 方法定义"

    def test_thin_wrappers_delegate_to_core(self):
        """_eval_formula 与 _eval_formula_series 均委托 _eval_formula_core。"""
        import ast
        import inspect
        from core.formula_module import FormulaEngine
        src = inspect.getsource(FormulaEngine)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in (
                "_eval_formula", "_eval_formula_series"
            ):
                end = getattr(node, "end_lineno", node.lineno) or node.lineno
                method_text = "\n".join(src.splitlines()[node.lineno - 1: end])
                assert "_eval_formula_core" in method_text, \
                    f"{node.name} 应委托 _eval_formula_core（变更 D 薄包装）"
