"""运行时边级表真相源。

按 ``ARCHITECTURE_FINAL.md`` 第 3.2.2 节实现，将原本散落在
``PoolState`` 中的边级运行时表收敛为 ``EdgeState``：

- ``exec_ctx``: 边执行上下文（count / first_fire / last_fire）
- ``formula_results``: 公式级结果缓存（亦称 ``filter_cache``）
- ``filter_inputs``: 每条边最近一次过滤的输入股票指纹

I94：删除 ``edge_fired`` 字典与 ``exec_ctx[eid]["fired"]`` 字段——两者均
零生产读取。``edge_fired`` 被 engine.py 写入（is_edge_due 结果）但 L322
读局部变量；``exec_ctx.fired`` 被 set_exec_ctx_fired 写入但无消费方。
edge_fired 非非 exec_ctx.fired 的视图（语义不同：前者为当前 tick 时间
门控结果，后者为边是否曾执行过），原 L7 注释错误。
"""
from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional, Tuple


class EdgeStateMixin:
    """EdgeState 表级访问方法集合。

    将公式结果缓存与过滤输入指纹的读写从 ``EdgeState`` 核心类中剥离，
    使其属性/方法数满足架构约束。
    """

    # ------------------------------------------------------------------
    # formula_results（公式级结果缓存，亦称 filter_cache）
    # ------------------------------------------------------------------
    def get_formula_result(self, formula_ref: Any, bar_hash: str) -> Any:
        return self.formula_results.get((formula_ref, bar_hash))

    def set_formula_result(self, formula_ref: Any, bar_hash: str, result: Any) -> None:
        self.formula_results[(formula_ref, bar_hash)] = result

    # ------------------------------------------------------------------
    # filter_inputs
    # ------------------------------------------------------------------
    def set_filter_input(self, eid: str, codes: Iterable[str]) -> None:
        self.filter_inputs[eid] = frozenset(codes)

    def get_filter_input(self, eid: str) -> Optional[frozenset]:
        return self.filter_inputs.get(eid)


@dataclass
class EdgeState(EdgeStateMixin):
    """边级运行时表真相源。

    属性（按架构 ≤5 个）：
      - exec_ctx
      - formula_results
      - filter_inputs
    """

    exec_ctx: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    formula_results: Dict[Tuple[Any, str], Any] = field(default_factory=dict)
    filter_inputs: Dict[str, frozenset] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # exec_ctx（边执行上下文：count / first_fire / last_fire）
    # ------------------------------------------------------------------
    def get_exec_ctx(self, eid: str) -> Dict[str, Any]:
        if eid not in self.exec_ctx:
            self.exec_ctx[eid] = {
                "count": 0,
                "first_fire": None,
                "last_fire": None,
            }
        return self.exec_ctx[eid]

    def set_exec_ctx_fired(self, eid: str, now: Optional[float] = None) -> None:
        ctx = self.get_exec_ctx(eid)
        if now is None:
            now = time.time()
        ctx["count"] = ctx.get("count", 0) + 1
        if ctx["first_fire"] is None:
            ctx["first_fire"] = now
        ctx["last_fire"] = now

    # ------------------------------------------------------------------
    # 快照 / 恢复
    # ------------------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        return {
            "exec_ctx": copy.deepcopy(self.exec_ctx),
            "formula_results": copy.deepcopy(self.formula_results),
            "filter_inputs": copy.deepcopy(self.filter_inputs),
        }

    def restore(self, data: Dict[str, Any]) -> None:
        self.exec_ctx = copy.deepcopy(data.get("exec_ctx", {}))
        self.formula_results = copy.deepcopy(data.get("formula_results", {}))
        self.filter_inputs = copy.deepcopy(data.get("filter_inputs", {}))

    def fresh(self) -> None:
        self.exec_ctx.clear()
        self.formula_results.clear()
        self.filter_inputs.clear()
