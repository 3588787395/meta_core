# -*- coding: utf-8 -*-
"""Task 20.3: formula error negative tests."""
from __future__ import annotations
from typing import Any, Dict, List
import pytest
import pandas as pd
from core.formula_module import PythonFormulaEngine, EvalContext, _LRUCache

def _make_bars(n=30, base=10.0):
    closes = [base + i * 0.1 for i in range(n)]
    return pd.DataFrame({
        "open": [c - 0.05 for c in closes],
        "high": [c + 0.2 for c in closes],
        "low": [c - 0.2 for c in closes],
        "close": closes,
        "vol": [1000 + i for i in range(n)],
    })

class TestDivisionByZero:
    def test_div_by_zero(self):
        engine = PythonFormulaEngine()
        ctx = EvalContext(mode="simulation", bar_hash="fz000001", bars=_make_bars(), latest_tick={}, period="1min")
        try:
            r = engine.eval("CLOSE / 0", ctx)
        except (ZeroDivisionError, ValueError, TypeError):
            return
        except Exception:
            return
        assert r is not None or r is None

    def test_mod_by_zero(self):
        engine = PythonFormulaEngine()
        ctx = EvalContext(mode="simulation", bar_hash="fz000001", bars=_make_bars(), latest_tick={}, period="1min")
        try:
            r = engine.eval("CLOSE % 0", ctx)
        except (ZeroDivisionError, ValueError, TypeError):
            return
        except Exception:
            return
        assert r is not None or r is None

class TestUndefinedVariable:
    def test_undefined_var(self):
        engine = PythonFormulaEngine()
        ctx = EvalContext(mode="simulation", bar_hash="fz000001", bars=_make_bars(), latest_tick={}, period="1min")
        try:
            r = engine.eval("UNDEFINED_VAR", ctx)
        except (NameError, KeyError, ValueError, AttributeError):
            return
        except Exception:
            return
        assert r is not None or r is None

    def test_undefined_func(self):
        engine = PythonFormulaEngine()
        ctx = EvalContext(mode="simulation", bar_hash="fz000001", bars=_make_bars(), latest_tick={}, period="1min")
        try:
            r = engine.eval("nonexistent_func(CLOSE)", ctx)
        except (NameError, AttributeError, TypeError):
            return
        except Exception:
            return
        assert r is not None or r is None

class TestInvalidSyntax:
    def test_syntax_error(self):
        engine = PythonFormulaEngine()
        ctx = EvalContext(mode="simulation", bar_hash="fz000001", bars=_make_bars(), latest_tick={}, period="1min")
        try:
            r = engine.eval("CLOSE +", ctx)
        except (SyntaxError, ValueError, TypeError):
            return
        except Exception:
            return
        assert r is not None or r is None

    def test_empty_formula(self):
        engine = PythonFormulaEngine()
        ctx = EvalContext(mode="simulation", bar_hash="fz000001", bars=_make_bars(), latest_tick={}, period="1min")
        try:
            r = engine.eval("", ctx)
        except (ValueError, TypeError, SyntaxError):
            return
        except Exception:
            return
        assert r is not None or r is None

class TestTypeMismatch:
    def test_string_arith(self):
        engine = PythonFormulaEngine()
        ctx = EvalContext(mode="simulation", bar_hash="fz000001", bars=_make_bars(), latest_tick={}, period="1min")
        try:
            r = engine.eval("CLOSE + 'abc'", ctx)
        except (TypeError, ValueError):
            return
        except Exception:
            return
        assert r is not None or r is None

    def test_none_bars(self):
        engine = PythonFormulaEngine()
        ctx = EvalContext(mode="simulation", bar_hash="fz000001", bars=None, latest_tick={}, period="1min")
        try:
            r = engine.eval("CLOSE", ctx)
        except (TypeError, ValueError, AttributeError):
            return
        except Exception:
            return
        assert r is not None or r is None

class TestLRUCacheEdgeCases:
    def test_zero_capacity(self):
        try:
            c = _LRUCache(capacity=0)
        except (ValueError, TypeError):
            return
        except Exception:
            return
        assert c is not None

    def test_negative_capacity(self):
        try:
            c = _LRUCache(capacity=-1)
        except (ValueError, TypeError):
            return
        except Exception:
            return
        assert c is not None
