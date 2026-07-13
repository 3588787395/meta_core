"""运行时辅助工具：编译表达式缓存。

本模块不再包含兼容层方法；_MetaEngineCompat 已删除。
CompiledExpression 用 ast 受控求值（_eval_derived_ast），禁用 eval()。
_safe_timestamp 已统一至 time_util.py（I10 消除三处副本）。
I36：_stock_code/_normalize_stock_code/_load_market_cfg/_MARKET_PREFIXES/
_MARKET_SUFFIXES 已迁至 _market_utils.py（消除死代码副本）。
"""
from __future__ import annotations

import ast
import logging
from typing import Any, Dict, Tuple

try:
    from .evaluators import _eval_derived_ast
except ImportError:
    try:
        from ..core.evaluators import _eval_derived_ast
    except ImportError:
        from evaluators import _eval_derived_ast

logger = logging.getLogger(__name__)


class CompiledExpression:
    """解析并缓存单个表达式的 AST，提供安全的条件/值求值（ast 受控，禁 eval）。

    求值内核委托 _eval_derived_ast，与 evaluators._eval_derived_expr 同源，
    支持 +,-,*,/、比较、逻辑(and/or/not)、索引访问、_DERIVED_FUNCS 表内函数
    （max/min/abs/round）。AST 解析一次缓存复用，避免每 tick 重复解析。
    """

    _cache: Dict[str, "CompiledExpression"] = {}

    def __init__(self, source: str, tag: str = ""):
        self.source = source
        self.tag = tag or source
        try:
            self.tree = ast.parse(str(source), mode="eval")
        except SyntaxError as exc:
            logger.debug("CompiledExpression 解析失败 %s: %s", tag, exc)
            self.tree = None

    @classmethod
    def get(cls, source: str, tag: str = "") -> "CompiledExpression":
        key = f"{tag}::{source}"
        cached = cls._cache.get(key)
        if cached is not None:
            return cached
        inst = cls(source, tag)
        cls._cache[key] = inst
        return inst

    def evaluate(self, ctx: Dict[str, Any]) -> Any:
        if self.tree is None:
            raise ValueError(f"表达式未解析成功: {self.source}")
        return _eval_derived_ast(self.tree, ctx)

    def evaluate_conditional(self, cond_str: str, expr_str: str, ctx: Dict[str, Any]) -> Tuple[bool, Any]:
        """先求值条件表达式，条件为真时再求值结果表达式。"""
        cond_ok = bool(self.evaluate(ctx))
        if not cond_ok:
            return False, None
        expr = self.get(expr_str, f"expr_{self.tag}")
        return True, expr.evaluate(ctx)
