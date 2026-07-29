# -*- coding: utf-8 -*-
"""core.domain — DZH/TDX 股票池统一 OOP 领域模型（合并自原 core/domain/ 6 文件）。

SubTask 29.1 合并：将原 ``core/domain/`` 包下的 6 个源文件合并为单一模块。
纯数据模型（含 Evaluator 接口层），仅依赖标准库，可被任意模块 import。

合并来源（按文件内顺序）：
- base.py: Node / Edge 抽象基类
- nodes.py: 11 个 Node 子类 + from_dzh_type / from_tdx_type 工厂
- edges.py: ConditionalEdge / UnconditionalEdge + from_dzh_attr / from_tdx_source_type 工厂
- specs.py: TimingSpec / FilterSpec / PropagateSpec / ActionSpec / TTLSpec / CandidateRange / ReloadSchedule
- evaluators.py: Evaluator 层次（nset 0-5）+ FINANCIAL_INDICATORS / MARKET_FIELDS 常量
- tick_source.py: TickSource / RealTickSource / MockDataSource + 市场代码工具

注意：原 ``core/domain/{base,nodes,edges,specs,evaluators,tick_source}.py`` 中的
``Path(__file__).parent.parent.parent`` 路径已修正为 ``parent.parent``（文件上移一级）。
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import logging
import operator
import random
import re
import time
from abc import ABC, abstractmethod
from collections import namedtuple
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Type


# ════════════════════════════════════════════════════════════════
# Section: 字段元数据（Task 11.1：_FieldMeta 用于 _NodeBase/_EdgeBase 子类
# 声明类型特有字段的序列化规则，消除 to_dict / from_dict 手写样板）
# ════════════════════════════════════════════════════════════════
# serializer 取值约定：
#   None          : 普通字段（直接取值 / d.get(name, default)）
#   "list"        : 列表字段（to_dict 做 list() 拷贝）
#   "dict"        : 字典字段（to_dict 做 dict() 拷贝）
#   ("spec", Name): Spec 对象字段（to_dict 调 obj.to_dict() or None；
#                   from_dict 调 SpecClass.from_dict(v) if v else None）
#                   Name 为 Spec 类名字符串，运行时经 globals() 解析，
#                   避免 Node/Edge 类定义早于 Spec 类的前向引用问题。
_FieldMeta = namedtuple("_FieldMeta", ["name", "default", "serializer"])


# ════════════════════════════════════════════════════════════════
# Section: base.py — 领域对象抽象基类：Node 与 Edge
# ════════════════════════════════════════════════════════════════


class Node(ABC):
    """图节点抽象基类，对应 DZH/TDX 股票池中的节点元素。"""

    id: str
    legacy_type: int
    pos: Tuple[float, float]
    clr: int
    text: str
    attr: int

    @abstractmethod
    def to_dict(self) -> Dict[str, Any]:
        """序列化为可往返的字典。"""

    @classmethod
    @abstractmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Node":
        """从字典反序列化，与 to_dict 互逆。"""


class Edge(ABC):
    """图边抽象基类，对应 DZH/TDX 股票池中的流转边元素。"""

    id: str
    from_id: str
    to_id: str
    attr: int
    clr: int
    size: int

    @abstractmethod
    def to_dict(self) -> Dict[str, Any]:
        """序列化为可往返的字典。"""

    @classmethod
    @abstractmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Edge":
        """从字典反序列化，与 to_dict 互逆。"""


# ════════════════════════════════════════════════════════════════
# Section: nodes.py — Node 子类：DZH/TDX 节点类型的统一 OOP 模型
# 覆盖 DZH 11 种节点类型（0/1/2/3/4/5/6/200/201/202/203）与 TDX 6 种（0/1/2/3/7/8）
# 表驱动：DZH/TDX type → Node 子类映射使用 dict 常量，无 if/elif 链。
# ════════════════════════════════════════════════════════════════


def _norm_pos(pos: Any) -> Tuple[float, float]:
    """将 pos 归一化为 2 元组（list/tuple 兼容）。"""
    if isinstance(pos, (list, tuple)):
        if len(pos) >= 2:
            return (float(pos[0]), float(pos[1]))
    return (0.0, 0.0)


class _NodeBase(Node):
    """Node ABC 的具体基类，处理 6 个公共字段的序列化与工厂分派。

    Task 11.3：基类提供统一的 to_dict / from_dict，遍历子类 _FIELDS
    自动序列化/反序列化类型特有字段，消除子类手写样板。
    """

    DZH_TYPE: Optional[int] = None
    TDX_TYPE: Optional[int] = None
    # 子类覆盖：声明类型特有字段的序列化规则（见 _FieldMeta 注释）
    _FIELDS: Tuple[_FieldMeta, ...] = ()

    def __init__(
        self,
        id: str = "",
        legacy_type: int = 0,
        pos: Tuple[float, float] = (0.0, 0.0),
        clr: int = 0,
        text: str = "",
        attr: int = 0,
    ) -> None:
        self.id = id
        self.legacy_type = legacy_type
        self.pos = pos
        self.clr = clr
        self.text = text
        self.attr = attr

    def _common_to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "legacy_type": self.legacy_type,
            "pos": list(self.pos),
            "clr": self.clr,
            "text": self.text,
            "attr": self.attr,
        }

    @staticmethod
    def _common_kwargs(d: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": d.get("id", ""),
            "legacy_type": d.get("legacy_type", 0),
            "pos": _norm_pos(d.get("pos", (0.0, 0.0))),
            "clr": d.get("clr", 0),
            "text": d.get("text", ""),
            "attr": d.get("attr", 0),
        }

    def to_dict(self) -> Dict[str, Any]:
        """统一序列化：公共字段 + 遍历 _FIELDS 序列化类型特有字段。"""
        d = self._common_to_dict()
        for fm in self._FIELDS:
            val = getattr(self, fm.name)
            ser = fm.serializer
            if ser == "list":
                d[fm.name] = list(val)
            elif ser == "dict":
                d[fm.name] = dict(val)
            elif isinstance(ser, tuple) and ser[0] == "spec":
                d[fm.name] = val.to_dict() if val else None
            else:  # None / "plain"
                d[fm.name] = val
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "_NodeBase":
        """统一反序列化：公共字段 + 遍历 _FIELDS 解析类型特有字段。"""
        kwargs = cls._common_kwargs(d)
        for fm in cls._FIELDS:
            ser = fm.serializer
            if isinstance(ser, tuple) and ser[0] == "spec":
                v = d.get(fm.name)
                spec_cls = globals()[ser[1]]
                kwargs[fm.name] = spec_cls.from_dict(v) if v else None
            else:
                kwargs[fm.name] = d.get(fm.name, fm.default)
        return cls(**kwargs)

    # ── 工厂方法（表驱动分派，忽略 cls，按 type 查注册表）──
    @classmethod
    def from_dzh_type(cls, t: int) -> Type[Node]:
        """按 DZH 节点 type 返回对应 Node 子类。"""
        klass = _DZH_TYPE_REGISTRY.get(int(t))
        if klass is None:
            raise KeyError(f"未注册的 DZH 节点 type: {t}")
        return klass

    @classmethod
    def from_tdx_type(cls, t: int) -> Type[Node]:
        """按 TDX 节点 type 返回对应 Node 子类。"""
        klass = _TDX_TYPE_REGISTRY.get(int(t))
        if klass is None:
            raise KeyError(f"未注册的 TDX 节点 type: {t}")
        return klass


class DecorativeNode(_NodeBase):
    """装饰节点（DZH type=0），仅 text/clr。"""

    DZH_TYPE = 0


class TextLabelNode(_NodeBase):
    """文字标签（DZH type=1），独有 url。"""

    DZH_TYPE = 1
    _FIELDS = (
        _FieldMeta("url", "", None),
    )

    def __init__(self, url: str = "", **common: Any) -> None:
        super().__init__(**common)
        self.url = url


class ContainerNode(_NodeBase):
    """容器（DZH type=2），独有 children。"""

    DZH_TYPE = 2
    _FIELDS = (
        _FieldMeta("children", [], "list"),
    )

    def __init__(self, children: Optional[List[str]] = None, **common: Any) -> None:
        super().__init__(**common)
        self.children = list(children) if children else []


class StateColumnNode(_NodeBase):
    """状态列（DZH type=3），布局元素，无独有字段。"""

    DZH_TYPE = 3


class DiscardPoolNode(_NodeBase):
    """丢弃池（DZH type=4），StatePoolNode 简化版。"""

    DZH_TYPE = 4
    _FIELDS = (
        _FieldMeta("enter", {}, "dict"),
        _FieldMeta("exit", {}, "dict"),
        _FieldMeta("tradeattr", {}, "dict"),
        _FieldMeta("psatt", {}, "dict"),
    )

    def __init__(
        self,
        enter: Optional[Dict[str, Any]] = None,
        exit: Optional[Dict[str, Any]] = None,
        tradeattr: Optional[Dict[str, Any]] = None,
        psatt: Optional[Dict[str, Any]] = None,
        **common: Any,
    ) -> None:
        super().__init__(**common)
        self.enter = dict(enter) if enter else {}
        self.exit = dict(exit) if exit else {}
        self.tradeattr = dict(tradeattr) if tradeattr else {}
        self.psatt = dict(psatt) if psatt else {}


class ExecutionOrderNode(_NodeBase):
    """执行顺序节点（DZH type=5），独有 order_type。"""

    DZH_TYPE = 5
    _FIELDS = (
        _FieldMeta("order_type", "", None),
    )

    def __init__(self, order_type: str = "", **common: Any) -> None:
        super().__init__(**common)
        self.order_type = order_type


class FlowArrowNode(_NodeBase):
    """流向箭头（DZH type=6），无独有字段。"""

    DZH_TYPE = 6


class StatePoolNode(_NodeBase):
    """股票状态池（DZH type=200 / TDX type=8）。"""

    DZH_TYPE = 200
    TDX_TYPE = 8
    # Spec 类引用通过 ("spec", "ClassName") 字符串延迟解析，
    # 避免 Node 类定义早于 Spec 类的前向引用问题。
    _FIELDS = (
        _FieldMeta("enter", {}, "dict"),
        _FieldMeta("exit", {}, "dict"),
        _FieldMeta("tradeattr", {}, "dict"),
        _FieldMeta("psatt", {}, "dict"),
        _FieldMeta("ttl_spec", None, ("spec", "TTLSpec")),
        _FieldMeta("action_spec", None, ("spec", "ActionSpec")),
    )

    def __init__(
        self,
        enter: Optional[Dict[str, Any]] = None,
        exit: Optional[Dict[str, Any]] = None,
        tradeattr: Optional[Dict[str, Any]] = None,
        psatt: Optional[Dict[str, Any]] = None,
        ttl_spec: Optional[TTLSpec] = None,
        action_spec: Optional[ActionSpec] = None,
        **common: Any,
    ) -> None:
        super().__init__(**common)
        self.enter = dict(enter) if enter else {}
        self.exit = dict(exit) if exit else {}
        self.tradeattr = dict(tradeattr) if tradeattr else {}
        self.psatt = dict(psatt) if psatt else {}
        self.ttl_spec = ttl_spec
        self.action_spec = action_spec


class ResultPoolNode(StatePoolNode):
    """结果池（DZH type=203），StatePoolNode 变体，独有 result_type。"""

    DZH_TYPE = 203
    TDX_TYPE = None
    # 继承 StatePoolNode._FIELDS 并追加 result_type
    _FIELDS = StatePoolNode._FIELDS + (
        _FieldMeta("result_type", 0, None),
    )

    def __init__(self, result_type: int = 0, **common: Any) -> None:
        super().__init__(**common)
        self.result_type = result_type


class ConditionNode(_NodeBase):
    """转移条件节点（DZH type=201 / TDX type=3），独有 filter_spec。"""

    DZH_TYPE = 201
    TDX_TYPE = 3
    _FIELDS = (
        _FieldMeta("func", {}, "dict"),
        _FieldMeta("indi", "", None),
        _FieldMeta("indiparam", [], "list"),
        _FieldMeta("filter_spec", None, ("spec", "FilterSpec")),
    )

    def __init__(
        self,
        func: Optional[Dict[str, Any]] = None,
        indi: str = "",
        indiparam: Optional[List[Dict[str, Any]]] = None,
        filter_spec: Optional[FilterSpec] = None,
        **common: Any,
    ) -> None:
        super().__init__(**common)
        self.func = dict(func) if func else {}
        self.indi = indi
        self.indiparam = list(indiparam) if indiparam else []
        self.filter_spec = filter_spec or FilterSpec()


class CandidatePoolNode(_NodeBase):
    """备选池节点（DZH type=202 / TDX type=7），独有 candidate_range。"""

    DZH_TYPE = 202
    TDX_TYPE = 7
    _FIELDS = (
        _FieldMeta("attrtext", "", None),
        _FieldMeta("reload", 0, None),
        _FieldMeta("spinfo", {}, "dict"),
        _FieldMeta("candidate_range", None, ("spec", "CandidateRange")),
        _FieldMeta("reload_schedule", None, ("spec", "ReloadSchedule")),
    )

    def __init__(
        self,
        attrtext: str = "",
        reload: int = 0,
        spinfo: Optional[Dict[str, Any]] = None,
        candidate_range: Optional[CandidateRange] = None,
        reload_schedule: Optional[ReloadSchedule] = None,
        **common: Any,
    ) -> None:
        super().__init__(**common)
        self.attrtext = attrtext
        self.reload = reload
        self.spinfo = dict(spinfo) if spinfo else {}
        self.candidate_range = candidate_range or CandidateRange()
        self.reload_schedule = reload_schedule


# 表驱动注册表：DZH/TDX type → Node 子类（无 if/elif 链）
_DZH_TYPE_REGISTRY: Dict[int, Type[Node]] = {
    0: DecorativeNode,
    1: TextLabelNode,
    2: ContainerNode,
    3: StateColumnNode,
    4: DiscardPoolNode,
    5: ExecutionOrderNode,
    6: FlowArrowNode,
    200: StatePoolNode,
    201: ConditionNode,
    202: CandidatePoolNode,
    203: ResultPoolNode,
}

# TDX type → Node 子类（依据 dzh_type_map.json:tdx_to_dzh 跨格式映射）
_TDX_TYPE_REGISTRY: Dict[int, Type[Node]] = {
    0: TextLabelNode,      # tdx_to_dzh 0 → 1
    1: TextLabelNode,      # tdx_to_dzh 1 → 1
    2: ContainerNode,      # tdx_to_dzh 2 → 2
    3: ConditionNode,      # tdx_to_dzh 3 → 201
    7: CandidatePoolNode,  # tdx_to_dzh 7 → 202
    8: StatePoolNode,      # tdx_to_dzh 8 → 200
}


def all_dzh_types() -> List[int]:
    """返回全部已注册的 DZH 节点 type。"""
    return list(_DZH_TYPE_REGISTRY.keys())


def all_tdx_types() -> List[int]:
    """返回全部已注册的 TDX 节点 type。"""
    return list(_TDX_TYPE_REGISTRY.keys())


# ════════════════════════════════════════════════════════════════
# Section: edges.py — Edge 子类：DZH/TDX 流转边的统一 OOP 模型
# 两种边类型：
# - ConditionalEdge（条件转移边）：源为备选池/状态池/数据源，含时机/筛选/流转/动作/TTL 规格
# - UnconditionalEdge（无条件转移边）：源为条件节点，仅含流转规格
# 表驱动：DZH attr / 源 type → Edge 子类映射使用 dict 常量，无 if/elif 链。
# ════════════════════════════════════════════════════════════════


class _EdgeBase(Edge):
    """Edge ABC 的具体基类，处理 6 个公共字段的序列化与工厂分派。

    Task 11.3：基类提供统一的 to_dict / from_dict，遍历子类 _FIELDS
    自动序列化/反序列化类型特有字段，消除子类手写样板。
    """

    # 子类覆盖：声明类型特有字段的序列化规则（见 _FieldMeta 注释）
    _FIELDS: Tuple[_FieldMeta, ...] = ()

    def __init__(
        self,
        id: str = "",
        from_id: str = "",
        to_id: str = "",
        attr: int = 0,
        clr: int = 0,
        size: int = 1,
    ) -> None:
        self.id = id
        self.from_id = from_id
        self.to_id = to_id
        self.attr = attr
        self.clr = clr
        self.size = size

    def _common_to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "from_id": self.from_id,
            "to_id": self.to_id,
            "attr": self.attr,
            "clr": self.clr,
            "size": self.size,
        }

    @staticmethod
    def _common_kwargs(d: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": d.get("id", ""),
            "from_id": d.get("from_id", d.get("from", "")),
            "to_id": d.get("to_id", d.get("to", "")),
            "attr": d.get("attr", 0),
            "clr": d.get("clr", 0),
            "size": d.get("size", 1),
        }

    def to_dict(self) -> Dict[str, Any]:
        """统一序列化：公共字段 + 遍历 _FIELDS 序列化类型特有字段。"""
        d = self._common_to_dict()
        for fm in self._FIELDS:
            val = getattr(self, fm.name)
            ser = fm.serializer
            if ser == "list":
                d[fm.name] = list(val)
            elif ser == "dict":
                d[fm.name] = dict(val)
            elif isinstance(ser, tuple) and ser[0] == "spec":
                d[fm.name] = val.to_dict() if val else None
            else:  # None / "plain"
                d[fm.name] = val
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "_EdgeBase":
        """统一反序列化：公共字段 + 遍历 _FIELDS 解析类型特有字段。"""
        kwargs = cls._common_kwargs(d)
        for fm in cls._FIELDS:
            ser = fm.serializer
            if isinstance(ser, tuple) and ser[0] == "spec":
                v = d.get(fm.name)
                spec_cls = globals()[ser[1]]
                kwargs[fm.name] = spec_cls.from_dict(v) if v else None
            else:
                kwargs[fm.name] = d.get(fm.name, fm.default)
        return cls(**kwargs)

    # ── 工厂方法（表驱动分派）──
    @classmethod
    def from_dzh_attr(cls, attr: int) -> Type[Edge]:
        """按 DZH 边 attr 返回对应 Edge 子类（8192=条件边, 8193=无条件边）。"""
        klass = _DZH_ATTR_REGISTRY.get(int(attr))
        if klass is None:
            raise KeyError(f"未注册的 DZH 边 attr: {attr}")
        return klass

    @classmethod
    def from_tdx_source_type(cls, src_type: int) -> Type[Edge]:
        """按源节点 type 返回对应 Edge 子类。"""
        klass = _EDGE_SOURCE_TYPE_REGISTRY.get(int(src_type))
        if klass is None:
            raise KeyError(f"未注册的边源节点 type: {src_type}")
        return klass


class ConditionalEdge(_EdgeBase):
    """条件转移边：源为备选池/状态池/数据源，含时机/筛选/流转/动作/TTL 规格。"""

    _FIELDS = (
        _FieldMeta("interval", 0, None),
        _FieldMeta("begin", 0, None),
        _FieldMeta("end", 0, None),
        _FieldMeta("timing_spec", None, ("spec", "TimingSpec")),
        _FieldMeta("filter_spec", None, ("spec", "FilterSpec")),
        _FieldMeta("propagate_spec", None, ("spec", "PropagateSpec")),
        _FieldMeta("action_spec", None, ("spec", "ActionSpec")),
        _FieldMeta("ttl_spec", None, ("spec", "TTLSpec")),
    )

    def __init__(
        self,
        interval: int = 0,
        begin: int = 0,
        end: int = 0,
        timing_spec: Optional[TimingSpec] = None,
        filter_spec: Optional[FilterSpec] = None,
        propagate_spec: Optional[PropagateSpec] = None,
        action_spec: Optional[ActionSpec] = None,
        ttl_spec: Optional[TTLSpec] = None,
        **common: Any,
    ) -> None:
        super().__init__(**common)
        self.interval = interval
        self.begin = begin
        self.end = end
        self.timing_spec = timing_spec or TimingSpec()
        self.filter_spec = filter_spec or FilterSpec()
        self.propagate_spec = propagate_spec or PropagateSpec()
        self.action_spec = action_spec
        self.ttl_spec = ttl_spec


class UnconditionalEdge(_EdgeBase):
    """无条件转移边：源为条件节点，仅含流转规格，无时间属性。"""

    _FIELDS = (
        _FieldMeta("propagate_spec", None, ("spec", "PropagateSpec")),
    )

    def __init__(
        self,
        propagate_spec: Optional[PropagateSpec] = None,
        **common: Any,
    ) -> None:
        super().__init__(**common)
        self.propagate_spec = propagate_spec or PropagateSpec()


# ════════════════════════════════════════════════════════════
# 表驱动注册表（无 if/elif 链）
# ════════════════════════════════════════════════════════════
# DZH 边 attr → Edge 子类（依据 DESIGN.md 验证：8192=条件边, 8193=无条件边）
_DZH_ATTR_REGISTRY: Dict[int, Type[Edge]] = {
    8192: ConditionalEdge,
    8193: UnconditionalEdge,
}

# 源节点 type → Edge 子类
# 条件边源：备选池/状态池/数据源（type ∈ {0,200,202,7,8}）
# 无条件边源：条件节点（type ∈ {201,3}）
_EDGE_SOURCE_TYPE_REGISTRY: Dict[int, Type[Edge]] = {
    0: ConditionalEdge,
    200: ConditionalEdge,
    202: ConditionalEdge,
    7: ConditionalEdge,
    8: ConditionalEdge,
    201: UnconditionalEdge,
    3: UnconditionalEdge,
}


def all_dzh_edge_attrs() -> list:
    """返回全部已注册的 DZH 边 attr。"""
    return list(_DZH_ATTR_REGISTRY.keys())


def all_edge_source_types() -> list:
    """返回全部已注册的边源节点 type。"""
    return list(_EDGE_SOURCE_TYPE_REGISTRY.keys())


# ════════════════════════════════════════════════════════════════
# Section: specs.py — 领域规范对象（Spec）：纯数据 dataclass
# 每个 Spec 使用 @dataclass，继承 _SpecBase 获得统一 to_dict / from_dict
# 往返实现（基于 dataclass 字段内省），并提供单位换算辅助方法。
# Task 11：_SpecBase 消除 7 个 Spec 子类的 to_dict / from_dict 手写样板。
# ════════════════════════════════════════════════════════════════


def _as_dict(obj: Any) -> Any:
    """将 dataclass 实例转为字典（递归处理嵌套 dataclass / dict / list）。

    注意：不能通过 hasattr(obj, 'to_dict') 分派，否则 Spec.to_dict() → _as_dict(self)
    → obj.to_dict() 会无限递归。改用 is_datacase 按字段迭代。
    """
    if obj is None:
        return None
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _as_dict(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, dict):
        return {k: _as_dict(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_as_dict(v) for v in obj]
    return obj


def _from_dict_spec(cls, d: Optional[Dict[str, Any]]) -> Any:
    """通用 from_dict：按字段名取值，缺失则用默认。"""
    if d is None:
        return cls()
    if isinstance(d, cls):
        return d
    if not isinstance(d, dict):
        return cls()
    kwargs: Dict[str, Any] = {}
    for f in fields(cls):
        if f.name in d:
            kwargs[f.name] = d[f.name]
    return cls(**kwargs)


class _SpecBase:
    """Spec 类基类，提供基于 dataclass 字段内省的统一 to_dict / from_dict。

    Task 11：所有 @dataclass Spec 子类继承本类，消除手写 to_dict / from_dict 样板。
    不引入 _FIELDS（dataclass 已有 fields() 内省机制，无需重复声明）。
    """

    def to_dict(self) -> Dict[str, Any]:
        return _as_dict(self)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]] = None) -> "_SpecBase":
        return _from_dict_spec(cls, d)


@dataclass
class TimingSpec(_SpecBase):
    """时机门控规格：starttype(0-7) × cxtype(0-2) 共 24 种组合。"""

    starttype: int = 0
    cxtype: int = 0
    starttime: str = ""
    cxtime: int = 0
    cxtimetype: int = 0
    jgtime: int = 0


# cxtimetype 单位 → 秒换算因子（0=秒, 1=分, 2=时, 3=天）
_CXTIME_UNIT_TO_SEC: Dict[int, int] = {0: 1, 1: 60, 2: 3600, 3: 86400}


@dataclass
class FilterSpec(_SpecBase):
    """强弱筛选规格：评估器类型 × nset(0-5) × noperate(0-9)。"""

    evaluator_type: str = "indicator"
    nset: int = 0
    noperate: int = 0
    formula_ref: str = ""
    fsecond: Any = 0
    rank_rule: str = ""


@dataclass
class PropagateSpec(_SpecBase):
    """状态流转规格：copy/move/overwrite/force_move/output_components。"""

    mode: str = "copy"
    tran: int = 0
    emptyps: bool = False


@dataclass
class ActionSpec(_SpecBase):
    """回调副作用规格：6 种副作用（bdel/bsound/btip/bsavehis/bsavetoblock/baimpool）。"""

    bsavehis: bool = False
    bsound: bool = False
    btip: bool = False
    bsavetoblock: bool = False
    baimpool: bool = False
    bhighlight: bool = False
    tradeattr: Dict[str, Any] = field(default_factory=dict)
    psatt: Dict[str, Any] = field(default_factory=dict)
    soundfile: str = ""
    nsyssound: int = 0
    blockfile: str = ""
    bclearblock: bool = False
    nsoundtype: int = 0


# TTL 单位 → 秒换算因子（0=天, 1=时, 2=分, 3=秒, 4=秒[DZH 兼容]）
_TTL_UNIT_TO_SEC: Dict[int, int] = {0: 86400, 1: 3600, 2: 60, 3: 1, 4: 1}


@dataclass
class TTLSpec(_SpecBase):
    """超时淘汰规格：bdel × ndelnum × ndeltype（5 时间单位）。"""

    bdel: bool = False
    ndelnum: int = 0
    ndeltype: int = 0
    deltype: int = 0
    hold: int = 0
    endtime: int = 0
    delstocktype: int = 0

    def to_seconds(self) -> int:
        """将 ndelnum × ndeltype 单位换算为总秒数；未启用或非法单位返回 0。"""
        if not self.bdel:
            return 0
        unit_sec = _TTL_UNIT_TO_SEC.get(self.ndeltype, 0)
        return self.ndelnum * unit_sec


@dataclass
class CandidateRange(_SpecBase):
    """备选池范围规格：8 种来源类型（stock/market/self_select/sector/...）。"""

    range_type: str = "stock"
    codes: List[str] = field(default_factory=list)
    sector_id: str = ""
    spinfo_type: int = 0
    attrtext_raw: str = ""


@dataclass
class ReloadSchedule(_SpecBase):
    """备选池重载调度规格：5 种模式（never/on_file_load/on_startup/interval/daily_time）。"""

    mode: str = "never"
    interval_sec: int = 0
    daily_time: str = ""


# ── DZH 列定义（领域常量，兼容老代码）─────────────────────────────────
# 列 ID → {name/key/type} 映射，描述通达信/大智慧行情表格的列规格。
# 原定义在 services/tq_adapter.py，现移至 core/domain 作为领域知识，
# 供 services 与 core 层共同 import，消除 core → services 的跨层依赖。
DZH_COL_MAP: Dict[int, Dict[str, str]] = {
    2: {'name': '代码', 'key': 'code', 'type': 'string'},
    -1: {'name': '名称', 'key': 'name', 'type': 'string'},
    -2: {'name': '最新价', 'key': 'latest_price', 'type': 'number'},
    -3: {'name': '涨跌幅', 'key': 'change_pct', 'type': 'number'},
    -5: {'name': '涨跌额', 'key': 'change_amt', 'type': 'number'},
    -6: {'name': '成交量', 'key': 'volume', 'type': 'number'},
    1: {'name': '序号', 'key': 'seq', 'type': 'number'},
    7: {'name': '入池时间', 'key': 'enter_time', 'type': 'string'},
    8: {'name': '现价', 'key': 'current_price', 'type': 'number'},
    10: {'name': '收益率', 'key': 'profit_pct', 'type': 'number'},
    14: {'name': '入池价', 'key': 'enter_price', 'type': 'number'},
    17: {'name': '最大收益', 'key': 'max_profit', 'type': 'number'},
    24: {'name': '换手率', 'key': 'turnover_rate', 'type': 'number'},
    45: {'name': '保留天数', 'key': 'hold_days', 'type': 'number'},
    101: {'name': 'DDX连续飘红天数', 'key': 'ddx_red_days', 'type': 'number'},
    108: {'name': '量比', 'key': 'volume_ratio', 'type': 'number'},
    251: {'name': '特大单买入', 'key': 'huge_buy', 'type': 'number'},
    285: {'name': '大单买入', 'key': 'big_buy', 'type': 'number'},
    287: {'name': 'BBD', 'key': 'bbd', 'type': 'number'},
    401: {'name': 'DDX', 'key': 'ddx', 'type': 'number'},
}


# ════════════════════════════════════════════════════════════════
# Section: evaluators.py — 筛选评估器层次：按 nset(0-5) 划分的评估器抽象接口 + 数据载体
# 领域层定义评估器接口与数据持有，同时承载被 converters 等上层复用的纯计算
# 评估器操作函数（Task 23.3：从 core/evaluators.py 迁移至 core/domain/ 白名单，
# 消除 converters/tdx.py → core.evaluators 跨层违规 import）。
# 表驱动：evaluator_type → Evaluator 子类映射使用 dict 常量。
# 仅依赖标准库 + config/ 下 JSON 配置表。
# ════════════════════════════════════════════════════════════════


# ════════════════════════════════════════════════════════════════
# Task 12：Evaluator 注册表（装饰器驱动，消除静态 dict 维护）
# _EVALUATOR_REGISTRY 由 @register_evaluator 装饰器在子类定义时填充，
# Evaluator.from_filter_spec 工厂方法查表分派到子类实现。
# ════════════════════════════════════════════════════════════════
_EVALUATOR_REGISTRY: Dict[str, Type[Evaluator]] = {}


def register_evaluator(evaluator_type: str):
    """Evaluator 子类注册装饰器：将 (evaluator_type → cls) 写入 _EVALUATOR_REGISTRY。

    用法::

        @register_evaluator("indicator")
        class IndicatorEvaluator(Evaluator):
            ...
    """
    def decorator(cls: Type[Evaluator]) -> Type[Evaluator]:
        _EVALUATOR_REGISTRY[evaluator_type] = cls
        return cls
    return decorator


class Evaluator(ABC):
    """筛选评估器抽象基类。

    Task 12.3：``from_filter_spec`` 为工厂方法，按 ``filter_spec.evaluator_type``
    查 ``_EVALUATOR_REGISTRY`` 分派到子类实现的 ``from_filter_spec``。
    子类需覆盖 ``from_filter_spec`` 完成实际构造。
    """

    nset: int = -1

    @abstractmethod
    def evaluate(self, context: Dict[str, Any]) -> List[str]:
        """返回 passed 的股票列表（领域层占位，真实逻辑在 core/evaluators.py）。"""

    @classmethod
    def from_filter_spec(cls, filter_spec: FilterSpec) -> "Evaluator":
        """工厂方法：按 filter_spec.evaluator_type 查表分派到子类。"""
        klass = _EVALUATOR_REGISTRY.get(filter_spec.evaluator_type)
        if klass is None:
            raise KeyError(f"未注册的 evaluator_type: {filter_spec.evaluator_type}")
        return klass.from_filter_spec(filter_spec)


@register_evaluator("indicator")
class IndicatorEvaluator(Evaluator):
    """技术指标评估器（nset=0，DZH 技术指标）。"""

    nset = 0

    def __init__(self, formula_ref: str = "", noperate: int = 0, fsecond: Any = 0) -> None:
        self.formula_ref = formula_ref
        self.noperate = noperate
        self.fsecond = fsecond

    def evaluate(self, context: Dict[str, Any]) -> List[str]:
        return []

    @classmethod
    def from_filter_spec(cls, filter_spec: FilterSpec) -> "IndicatorEvaluator":
        return cls(formula_ref=filter_spec.formula_ref,
                   noperate=filter_spec.noperate, fsecond=filter_spec.fsecond)


@register_evaluator("condition_formula")
class ConditionFormulaEvaluator(Evaluator):
    """条件选股公式评估器（nset=1，DZH 条件选股）。"""

    nset = 1

    def __init__(self, formula_ref: str = "") -> None:
        self.formula_ref = formula_ref

    def evaluate(self, context: Dict[str, Any]) -> List[str]:
        return []

    @classmethod
    def from_filter_spec(cls, filter_spec: FilterSpec) -> "ConditionFormulaEvaluator":
        return cls(formula_ref=filter_spec.formula_ref)


@register_evaluator("expert_system")
class ExpertSystemEvaluator(Evaluator):
    """专家系统评估器（nset=2，DZH 交易系统）。"""

    nset = 2

    def __init__(self, formula_ref: str = "", noperate: int = 0) -> None:
        self.formula_ref = formula_ref
        self.noperate = noperate

    def evaluate(self, context: Dict[str, Any]) -> List[str]:
        return []

    @classmethod
    def from_filter_spec(cls, filter_spec: FilterSpec) -> "ExpertSystemEvaluator":
        return cls(formula_ref=filter_spec.formula_ref, noperate=filter_spec.noperate)


# 30 个财务指标映射常量（DZH 基本面条件，nset=3）
FINANCIAL_INDICATORS: Dict[str, str] = {
    "pe": "市盈率", "pb": "市净率", "roe": "净资产收益率", "roa": "总资产收益率",
    "eps": "每股收益", "bps": "每股净资产", "gross_margin": "销售毛利率",
    "net_margin": "销售净利率", "revenue": "营业收入", "net_profit": "净利润",
    "total_assets": "总资产", "net_assets": "净资产", "current_ratio": "流动比率",
    "quick_ratio": "速动比率", "debt_ratio": "资产负债率", "turnover": "总资产周转率",
    "inventory_turnover": "存货周转率", "receivables_turnover": "应收账款周转率",
    "operating_cashflow": "经营现金流", "free_cashflow": "自由现金流",
    "dividend_yield": "股息率", "market_cap": "总市值", "circ_market_cap": "流通市值",
    "revenue_yoy": "营收同比增长", "profit_yoy": "净利润同比增长",
    "q_profit": "单季净利润", "q_revenue": "单季营收",
    "undist_ps": "每股未分配利润", "capreserve_ps": "每股资本公积金", "cfr": "现金流比率",
}


@register_evaluator("financial_scalar")
class FinancialScalarEvaluator(Evaluator):
    """最新财务标量评估器（nset=3，DZH 基本面条件，30 财务指标）。"""

    nset = 3
    INDICATORS = FINANCIAL_INDICATORS

    def __init__(self, formula_ref: str = "", noperate: int = 0, fsecond: Any = 0) -> None:
        self.formula_ref = formula_ref
        self.noperate = noperate
        self.fsecond = fsecond

    def evaluate(self, context: Dict[str, Any]) -> List[str]:
        return []

    @classmethod
    def from_filter_spec(cls, filter_spec: FilterSpec) -> "FinancialScalarEvaluator":
        return cls(formula_ref=filter_spec.formula_ref,
                   noperate=filter_spec.noperate, fsecond=filter_spec.fsecond)


# 12 个动态行情字段映射常量（DZH 动态行情，nset=4）
MARKET_FIELDS: Dict[str, str] = {
    "price": "最新价", "open": "开盘价", "high": "最高价", "low": "最低价",
    "close": "收盘价", "volume": "成交量", "amount": "成交额",
    "pct_change": "涨跌幅", "turnover": "换手率", "volume_ratio": "量比",
    "bid_ask_spread": "买卖价差", "amplitude": "振幅",
}


@register_evaluator("market_scalar")
class MarketScalarEvaluator(Evaluator):
    """实时行情标量评估器（nset=4，DZH 动态行情，12 行情字段）。"""

    nset = 4
    FIELDS = MARKET_FIELDS

    def __init__(self, formula_ref: str = "", noperate: int = 0, fsecond: Any = 0) -> None:
        self.formula_ref = formula_ref
        self.noperate = noperate
        self.fsecond = fsecond

    def evaluate(self, context: Dict[str, Any]) -> List[str]:
        return []

    @classmethod
    def from_filter_spec(cls, filter_spec: FilterSpec) -> "MarketScalarEvaluator":
        return cls(formula_ref=filter_spec.formula_ref,
                   noperate=filter_spec.noperate, fsecond=filter_spec.fsecond)


# noperate → 集合运算名称（nset=5）
_SET_OPERATION_MAP: Dict[int, str] = {0: "union", 1: "difference", 2: "intersection"}


@register_evaluator("set_operation")
class SetOperationEvaluator(Evaluator):
    """集合运算评估器（nset=5，DZH 板块成员，并/差/交）。"""

    nset = 5
    OPERATIONS = ("union", "difference", "intersection")

    def __init__(self, operation: str = "union") -> None:
        if operation not in self.OPERATIONS:
            raise ValueError(f"非法集合运算: {operation}，须为 {self.OPERATIONS}")
        self.operation = operation

    def evaluate(self, context: Dict[str, Any]) -> List[str]:
        return []

    @classmethod
    def from_filter_spec(cls, filter_spec: FilterSpec) -> "SetOperationEvaluator":
        op = _SET_OPERATION_MAP.get(filter_spec.noperate, "union")
        return cls(operation=op)


# ════════════════════════════════════════════════════════════
# 表驱动：evaluator_type → Evaluator 子类（无 if/elif 链）
# Task 12：注册表由 @register_evaluator 装饰器自动填充，
# 模块级辅助函数复用 _EVALUATOR_REGISTRY，无需静态 dict。
# ════════════════════════════════════════════════════════════


def evaluator_from_filter_spec(filter_spec: FilterSpec) -> Evaluator:
    """按 FilterSpec.evaluator_type 路由到对应 Evaluator 子类实例。"""
    klass = _EVALUATOR_REGISTRY.get(filter_spec.evaluator_type)
    if klass is None:
        raise KeyError(f"未注册的 evaluator_type: {filter_spec.evaluator_type}")
    return klass.from_filter_spec(filter_spec)


def all_evaluator_types() -> List[str]:
    """返回全部已注册的 evaluator_type。"""
    return list(_EVALUATOR_REGISTRY.keys())


# ════════════════════════════════════════════════════════════════
# Task 23.3: 评估器操作函数（从 core/evaluators.py 迁移至 core/domain/ 白名单）
#
# converters/tdx.py 等上层模块需使用 _eval_op / _build_op_ctx / _resolve_rank /
# _NOPERATE_RULES / _RANK_MODES。原 core/evaluators.py 不在白名单中，直接 import
# 会导致跨层违规。将这些纯计算函数（含其依赖的派生表达式求值器与配置表加载）
# 迁移至 core/domain/evaluators.py（白名单），core/evaluators.py 通过 re-export
# 保持向后兼容。
# ════════════════════════════════════════════════════════════════

_domain_logger = logging.getLogger("core.evaluators")

# 表驱动：noperate 操作符规则配置（config/tdx_noperate_rules.json）
# records 的 expr/prev_expr/curr_expr/combine 字段（表达式字符串）驱动通用比较器 _eval_op
# rank_modes 驱动排名处理器 _resolve_rank
# 注意：合并后 __file__ 为 core/domain.py，需上溯 2 级到项目根再进入 config/
_noperate_data = json.loads(
    (Path(__file__).parent.parent / "config" / "data" / "tdx_noperate_rules.json").read_text("utf-8")
)
_NOPERATE_RULES = {r["id"]: r for r in _noperate_data.get("records", [])}
_RANK_MODES = _noperate_data.get("rank_modes", {})

# combine 字段消费表：将表中的 "and"/"or" 字符串映射为逻辑运算，消除 if/elif 分支
_COMBINE_OPS = {"and": lambda a, b: a and b, "or": lambda a, b: a or b}


def _build_op_ctx(line1: list, line2: list, params: dict | None = None) -> dict:
    """构建 _eval_op 的 ctx 字典。

    ctx 字段约定（由 tdx_noperate_rules.json 的 expr/prev_expr/curr_expr 消费）：
        a / b       : 当前值（line1[-1] / line2[-1]）
        line1/line2 : 向量序列（供索引访问 line1[-2]/line1[-3] 等）
        tol_abs/tol_rel : 容差参数
        abs_diff    : abs(a - b)（预计算，便于 expr 直接引用，避免重复求值）
        tol         : max(tol_abs, abs(b) * tol_rel)（预计算，同上）
    """
    params = params or {}
    tol_abs = params.get("tolerance_abs", 1e-8)
    tol_rel = params.get("tolerance_rel", 1e-4)
    a = line1[-1] if line1 else 0.0
    b = line2[-1] if line2 else 0.0
    return {
        "a": a, "b": b, "line1": line1, "line2": line2,
        "tol_abs": tol_abs, "tol_rel": tol_rel,
        "abs_diff": abs(a - b), "tol": max(tol_abs, abs(b) * tol_rel),
    }


def _eval_op(rule: dict, ctx: dict) -> bool | list:
    """通用比较器，按 rule 的 expr/prev_expr/curr_expr/combine 字段执行。

    计算逻辑由表字段（表达式字符串）承载，由 _eval_derived_expr 统一求值，
    无 if/elif 比较分支。rank 类型由 _resolve_rank 统一处理，此处返回占位 []。

    分派依据（表内容驱动，非代码分支）：
        - rule["expr"] 存在 → 单表达式求值（abs_lt/gt/lt）
        - rule["prev_expr"]+["curr_expr"] 存在 → 双表达式按 combine 组合（cross/inflection）
        - rule["compare"] == "rank" → 占位 []（排名由 _resolve_rank 处理）
    """
    if rule.get("compare") == "rank":
        return []
    expr = rule.get("expr")
    if expr is not None:
        return _eval_derived_expr(expr, ctx)
    prev = _eval_derived_expr(rule["prev_expr"], ctx)
    curr = _eval_derived_expr(rule["curr_expr"], ctx)
    return _COMBINE_OPS[rule.get("combine", "and")](prev, curr)


def _tie_exact_rank(ranked: list, n: int) -> list[str]:
    """精确排名第N名（处理并列）：同值并列占用相同名次。"""
    result, current_rank, prev_val = [], 0, None
    for idx, (code, val) in enumerate(ranked):
        if val != prev_val:
            current_rank = idx + 1
            prev_val = val
        if current_rank == n:
            result.append(code)
        elif current_rank > n:
            break
    return result


def _tie_slice(ranked: list, n: int) -> list[str]:
    """直接切片取前 N 名（不处理并列）。"""
    return [code for code, _ in ranked[:max(n, 1)]]


# 表驱动：tie_handling 处理器分派表，消除 _resolve_rank 中的 if/elif
_TIE_HANDLERS = {"exact_rank": _tie_exact_rank, "none": _tie_slice}


def _resolve_rank(ranked: list, fsecond: float, rank_rule: dict) -> list[str]:
    """根据 rank_modes 表的 rank_rule 处理排名结果。

    rank_rule 字段驱动差异：
        - order: 排序方向（desc 降序 / asc 升序）
        - tie_handling: 并列处理（由 _TIE_HANDLERS 表分派，无 if/elif）
        - params.default_n: fsecond<=0 时的默认 N
    """
    if not ranked: return []
    n = int(fsecond) if fsecond > 0 else rank_rule.get("params", {}).get("default_n", 10)
    order = rank_rule.get("order", "desc")
    tie = rank_rule.get("tie_handling", "none")
    ranked.sort(key=lambda x: x[1], reverse=(order == "desc"))
    handler = _TIE_HANDLERS.get(tie, _TIE_HANDLERS["none"])
    return handler(ranked, n)


# ast 受控求值器支持的二元运算符
_DERIVED_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
# ast 受控求值器支持的比较运算符
_DERIVED_CMP_OPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}
# ast 受控求值器支持的逻辑运算符
_DERIVED_BOOL_OPS = {
    ast.And: lambda a, b: a and b,
    ast.Or: lambda a, b: a or b,
}
# ast 受控求值器支持的安全函数表（表驱动：函数名 → 实现）
# 覆盖 tracker formulas 的 max/min 与 _build_op_ctx 历史注释提到的 abs/round
_DERIVED_FUNCS = {
    "max": max,
    "min": min,
    "abs": abs,
    "round": round,
}


def _eval_derived_ast(tree, ctx: dict):
    """对已解析的 ast.Expression 受控求值（无 eval）。

    支持 +,-,*,/ 四则运算、比较运算、逻辑运算（and/or/not）、
    索引访问（line1[-1]）、数字字面量、字段名变量、_DERIVED_FUNCS 表内函数调用。
    变量从 ctx 字典查找（数值字段转 float，布尔/列表等非数值类型原样返回
    以支持逻辑运算和索引访问）。None 值通过运算传播（类似 SQL NULL 语义）：
    任意 None 操作数 → None（逻辑运算中 None 视为 False）。
    """
    def _eval(node):
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in ctx:
                v = ctx[node.id]
                if v is None:
                    return None
                if isinstance(v, bool):
                    return v
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return v
            return None
        if isinstance(node, ast.BinOp) and type(node.op) in _DERIVED_BIN_OPS:
            left = _eval(node.left)
            right = _eval(node.right)
            if left is None or right is None:
                return None
            try:
                return _DERIVED_BIN_OPS[type(node.op)](left, right)
            except (TypeError, ZeroDivisionError):
                return None
        if isinstance(node, ast.Compare):
            left = _eval(node.left)
            for op, comp in zip(node.ops, node.comparators):
                right = _eval(comp)
                if left is None or right is None:
                    return None
                try:
                    if not _DERIVED_CMP_OPS[type(op)](left, right):
                        return False
                except TypeError:
                    return None
                left = right
            return True
        if isinstance(node, ast.BoolOp) and type(node.op) in _DERIVED_BOOL_OPS:
            is_and = isinstance(node.op, ast.And)
            result = _eval(node.values[0])
            for val_node in node.values[1:]:
                if is_and and not result:
                    return result
                if not is_and and result:
                    return result
                result = _eval(val_node)
            return result
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.USub):
                operand = _eval(node.operand)
                return None if operand is None else -operand
            if isinstance(node.op, ast.Not):
                operand = _eval(node.operand)
                return None if operand is None else not operand
        if isinstance(node, ast.Subscript):
            if isinstance(node.value, ast.Name):
                seq = ctx.get(node.value.id)
            else:
                seq = _eval(node.value)
            if seq is None:
                return None
            # Python 3.8: slice 包在 ast.Index 中；3.9+: 直接含 value
            sl = node.slice
            if isinstance(sl, ast.Index):  # Python 3.8 compat
                sl = sl.value
            idx = _eval(sl)
            if idx is None:
                return None
            try:
                return seq[idx]
            except (IndexError, TypeError, KeyError):
                return None
        if isinstance(node, ast.Call):
            # 表驱动函数调用：仅允许 _DERIVED_FUNCS 表中的函数，禁止任意调用
            if not isinstance(node.func, ast.Name) or node.func.id not in _DERIVED_FUNCS:
                raise ValueError(f"不支持的函数调用: {ast.dump(node.func)}")
            if node.keywords or any(isinstance(a, ast.Starred) for a in node.args):
                raise ValueError("不支持关键字参数或星号参数")
            args = [_eval(a) for a in node.args]
            if any(a is None for a in args):
                return None
            try:
                return _DERIVED_FUNCS[node.func.id](*args)
            except (TypeError, ValueError, ZeroDivisionError):
                return None
        raise ValueError(f"不支持的表达式节点: {ast.dump(node)}")
    return _eval(tree)


def _eval_derived_expr(expr: str, ctx: dict, guard: str | None = None) -> float | None:
    """受控表达式求值器，用 ast 模块解析，禁止 eval()。

    支持 +,-,*,/ 四则运算、比较运算、逻辑运算（and/or/not）、
    索引访问（line1[-1]）、数字字面量、字段名变量、_DERIVED_FUNCS 表内函数调用
    （max/min/abs/round）。None 值通过运算传播（类似 SQL NULL 语义）：
    任意 None 操作数 → None（逻辑运算中 None 视为 False）。
    guard 为条件表达式，先求值 guard，False 或 None 则返回 None。
    """
    # 先求值 guard，False 或 None 则返回 None
    if guard:
        try:
            guard_tree = ast.parse(guard, mode="eval")
        except SyntaxError:
            return None
        if not _eval_derived_ast(guard_tree, ctx):
            return None
    # 求值 expr
    try:
        expr_tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None
    return _eval_derived_ast(expr_tree, ctx)


# ════════════════════════════════════════════════════════════════
# Section: TDX nperiod → 周期映射（从 screening_module 迁移，供所有模块共用）
# ════════════════════════════════════════════════════════════════

# TDX nperiod 整数码 → 标准周期字符串映射（项目实例规范）
# 按 spec.md R6：nperiod=1 表示 1 分钟 K 线，nperiod=5 表示 5 分钟 K 线。
# 为兼容既有配置，保留原通达信部分约定：0=日线, 2=月线, 6=30分钟线,
# 7=60分钟线, 9/10/11=日线, 同时 3/8 也映射为 1 分钟线。
_TDX_NPERIOD_TO_PERIOD: Dict[int, str] = {
    0: '1d', 1: '1m', 2: '1mon', 3: '1m', 4: '5m', 5: '5m',
    6: '30m', 7: '60m', 8: '1m', 9: '1d', 10: '1d', 11: '1d',
}


def _nperiod_to_period(nperiod) -> str:
    """TDX nperiod 整数码映射为标准周期字符串。

    1 → '1m'（1 分钟线），4/5 → '5m'（5 分钟线），
    3/8 → '1m'，6 → '30m'，7 → '60m'，0/2/9/10/11 → '1d'。
    缺失或无效返回 '1d'。
    """
    try:
        return _TDX_NPERIOD_TO_PERIOD.get(int(nperiod), '1d')
    except (TypeError, ValueError):
        return '1d'


# ════════════════════════════════════════════════════════════════
# Section: 交集条件评估器（从 screening_module 迁移，供 execution_module 共用）
# ════════════════════════════════════════════════════════════════


def evaluate_intersection(codes: List[str], state: Any, edge_params: dict) -> List[str]:
    """交集条件评估器：筛选同时存在于指定源状态池中的股票。

    Args:
        codes: 当前待筛选的股票代码列表。
        state: 运行期状态对象，需提供 get_pool(nid) 方法。
        edge_params: 边参数字典，需包含 intersection_source 键指定源状态池 ID。

    Returns:
        交集结果：codes 中同时存在于 intersection_source 指定状态池的股票代码列表。
    """
    source_pool = edge_params.get('intersection_source', '')
    other_stocks: set = set()
    for s in (state.get_pool(source_pool).get_stocks() if source_pool else []):
        other_stocks.add(s.get('code', '') if isinstance(s, dict) else str(s))
    return [c for c in codes if c in other_stocks]


# ════════════════════════════════════════════════════════════════
# Section: 内置公式查找（builtin_formulas.json）
# I96 fail-fast 策略：模块加载时一次性读取并构建索引，消除重复 I/O 与静默回退。
# 从 core/screening_module.py 迁移至此（白名单模块），解除 formula_module → screening_module 耦合。
# ════════════════════════════════════════════════════════════════

_builtin_formulas_cache = None

def _load_builtin_formulas():
    global _builtin_formulas_cache
    if _builtin_formulas_cache is not None:
        return _builtin_formulas_cache
    path = Path(__file__).parent.parent / "config" / "data" / "builtin_formulas.json"
    try:
        _builtin_formulas_cache = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as ex:
        raise RuntimeError(
            f"无法加载配置表 {path}: {ex}（fail-fast：禁止静默回退空字符串）"
        ) from ex
    return _builtin_formulas_cache

_BUILTIN_FORMULAS = _load_builtin_formulas()
_BUILTIN_FORMULA_INDEX = {f.get("name"): f.get("script", "") for f in _BUILTIN_FORMULAS.get("formulas", [])}
_BUILTIN_FORMULA_INFO = {f.get("name"): f for f in _BUILTIN_FORMULAS.get("formulas", []) if f.get("name")}


def _lookup_builtin_script(name: str) -> str:
    """从 config/builtin_formulas.json 按名称查找公式脚本。

    Args:
        name: 公式名称（如 "MA"、"MACD"）。

    Returns:
        公式脚本文本；未找到时返回空字符串。
    """
    if not name:
        return ""
    return _BUILTIN_FORMULA_INDEX.get(name, "")


def _lookup_builtin_formula_info(name: str) -> dict:
    """从 config/builtin_formulas.json 按名称查找完整公式信息。

    Args:
        name: 公式名称（如 "KDJ_5MIN_CROSS"）。

    Returns:
        完整公式信息字典（含 script/period/eval_field 等）；未找到时返回空字典。
    """
    if not name:
        return {}
    return _BUILTIN_FORMULA_INFO.get(name, {})


# ════════════════════════════════════════════════════════════════
# Section: tick_source.py — 行情 tick 源抽象与实现（领域层白名单模块）
# Task 23.5：从 core/tick_source.py 迁移至 core/domain/ 白名单，
# 消除 services/providers/mock_provider.py → core.tick_source 跨层违规 import。
# SubTask 27.1：core/_market_utils.py 的 _stock_code / _normalize_stock_code /
# _MARKET_PREFIXES / _MARKET_SUFFIXES / _load_market_cfg 已迁移至本模块。
# ════════════════════════════════════════════════════════════════

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 市场代码工具（SubTask 27.1：从 core/_market_utils.py 迁移至此）
# 表驱动：_MARKET_PREFIXES/_MARKET_SUFFIXES 由 data_config.json 配置表决定。
# ---------------------------------------------------------------------------

# 注意：合并后 __file__ 为 core/domain.py，需上溯 2 级到项目根
_CFG_ROOT = Path(__file__).parent.parent


def _load_market_cfg() -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """从 data_config.json 加载市场代码前缀/后缀配置（fail-fast）。"""
    path = _CFG_ROOT / "config" / "data" / "data_config.json"
    try:
        _dc = json.load(open(path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as ex:
        raise RuntimeError(
            f"无法加载配置表 {path}: {ex}（fail-fast：禁止静默回退硬编码值）"
        ) from ex
    return (
        tuple(_dc.get("market_code_prefixes", ["SH", "SZ", "BJ"])),
        tuple(_dc.get("market_code_suffixes", [".SH", ".SZ", ".BJ"])),
    )


#: 需要被替换为 ``fz`` 的市场前缀/后缀集合（SubTask 27.1：改为 config 表驱动）。
_MARKET_PREFIXES, _MARKET_SUFFIXES = _load_market_cfg()


def _stock_code(s: Any) -> str:
    """从股票对象提取代码：dict 取 code（fallback label），其余 str()。

    I36 统一语义：合并 engine.py 的 label fallback 与 edge_executor.py 的
    str() wrap，消除两套语义分歧。dict 无 code/label 时返回 ''。
    """
    if isinstance(s, dict):
        return str(s.get('code', s.get('label', '')))
    return str(s)


def _normalize_stock_code(code: Any) -> str:
    """归一化股票代码：统一为 ``fz`` 前缀的 8 字符格式。

    先去除市场前缀(SH/SZ/BJ)、后缀(.SH/.SZ/.BJ)以及已有的 ``fz`` 前缀，
    保留纯数字部分并补零到 6 位，最后返回 ``fz<6位数字>``。
    例如 ``SH600000`` / ``600000.SH`` / ``fz1`` 均归一化为 ``fz600000``。
    """
    if code is None:
        return ''
    if not isinstance(code, str):
        code = str(code)
    c = code.strip()
    # 去除已有的 fz 前缀（不区分大小写），避免重复
    if c.lower().startswith("fz"):
        c = c[2:]
    for prefix in _MARKET_PREFIXES:
        if c.upper().startswith(prefix) and len(c) > len(prefix) and c[len(prefix)].isdigit():
            c = c[len(prefix):]
            break
    for suffix in _MARKET_SUFFIXES:
        if c.upper().endswith(suffix) and len(c) > len(suffix):
            c = c[:-len(suffix)]
            break
    numeric = "".join(ch for ch in c if ch.isdigit())
    return f"fz{numeric.zfill(6)}"


def _normalize_to_fz(code: str) -> str:
    """将 ``600000.SH`` / ``SZ000001`` 等统一归一化为 ``fzNNNNNN`` 格式。

    规则：
      - 已以 ``fz`` 开头（含 ``fz_`` 或 ``fz000001``）提取数字部分。
      - 移除 ``.SH`` / ``.SZ`` / ``.BJ`` 等后缀，再移除 ``SH/SZ/BJ`` 前缀。
      - 保留纯数字部分，补零到 6 位，前接 ``fz``。
    输出格式：``fz`` + 6位数字（如 fz000001, fz600000）。
    """
    if not isinstance(code, str):
        code = str(code)
    code = code.strip()
    if code.lower().startswith("fz"):
        # fz000001 / fz_1 / FZ000001 → 提取数字部分
        remainder = code[2:]
        if remainder.startswith("_"):
            remainder = remainder[1:]
        numeric = "".join(ch for ch in remainder if ch.isdigit())
    else:
        code_upper = code.upper()
        for suffix in (".SH", ".SZ", ".BJ"):
            if code_upper.endswith(suffix):
                code = code[: -len(suffix)]
                code_upper = code_upper[: -len(suffix)]
                break
        for prefix in _MARKET_PREFIXES:
            if code_upper.startswith(prefix):
                code = code[len(prefix) :]
                break
        numeric = "".join(ch for ch in code if ch.isdigit())
    numeric = numeric.zfill(6) if numeric else "000000"
    return f"fz{numeric}"


_FZ_CODE_PATTERN = re.compile(r"^fz\d{6}$")
_FZ_CODE_STRICT_PATTERN = _FZ_CODE_PATTERN


def is_fz_code(code: str) -> bool:
    """验证代码是否匹配 fz 格式（``fz`` + 6位数字，严格小写 fz 前缀）。"""
    return bool(_FZ_CODE_PATTERN.match(str(code).strip()))


def _code_seed(code: str) -> int:
    """基于股票代码生成确定性随机种子。"""
    digest = hashlib.md5(code.encode("utf-8")).hexdigest()
    return int(digest, 16) % (2 ** 31)


# ---------------------------------------------------------------------------
# TimedEventSpec（统一到时事件规格）— 纯数据结构，下沉至 domain 避免跨模块懒加载
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TimedEventSpec:
    """到时事件规格表行——边触发与 TTL 共用（G1 heapq 优先队列）。

    所有到时事件统一为 TimedEventSpec，注册到 EventDriver 的单一 heapq。
    到时触发是到时触发，执行事件是执行事件。区别仅在 params 不同：
      - 边触发：interval>0，到时发布 EdgeFired + 立即注册下次
      - TTL到期：interval=None（一次性），到时发布 DomainEvent(TIMEOUT)

    原位于 ``core/execution_module.py``，现下沉至本纯数据模型模块，
    使 ``execution_module`` 经白名单 ``from .domain import TimedEventSpec``
    获取，避免 ``domain`` 反向函数级懒加载 ``execution_module``（模块零引用约束）。

    Attributes:
        action:   事件回调，签名为 ``action(params, fire_time=None)``，发布事件。
                  fire_time 由 EventDriver.fire_due 注入，为 spec 实际触发的
                  精确时刻（heapq 弹出时的 fire_time），用于让事件的 ts 反映
                  实际触发顺序，避免同一仿真步内所有事件共享 self.clock 导致
                  前端时间轴上堆叠为一条线。
        params:   事件参数字典。
        interval: 触发间隔（秒）。None=一次性事件，>0=周期触发。
        end_fn:   结束时间判定函数（可选）。None=永久。
    """

    action: Callable[..., None]
    params: dict = field(default_factory=dict)
    interval: Optional[float] = None
    end_fn: Optional[Callable[[], float]] = None


class TickSource(ABC):
    """行情 tick 源抽象基类。

    核心循环通过 ``next_ticks(now)`` 获取当前到期的 per-code tick 字典，
    再经 ``DataUpdater.apply_data`` 写入 ``PoolState.latest_tick``。
    """

    @abstractmethod
    def next_ticks(self, now: float) -> Dict[str, Dict[str, Any]]:
        """返回当前到期的 tick 数据。

        Args:
            now: 当前时间戳（由 ``time_at(state)`` 提供，可为 Unix 时间或虚拟时钟）。

        Returns:
            ``{code: {open, high, low, close, volume, amount, _ts, ...}}`` 字典。
        """
        ...

    @abstractmethod
    def interval_for(self, code: str) -> float:
        """返回某只股票的固定 tick 间隔（秒）。"""
        ...


class RealTickSource(TickSource):
    """实盘 tick 源：通过外部 live 数据源（如 TQ adapter）获取快照。

    保留现有实盘接入能力，将 ``tq_adapter.get_snapshot`` 等接口适配为
    ``TickSource`` 协议。
    """

    def __init__(
        self,
        snapshot_provider: Callable[[List[str]], Dict[str, Dict[str, Any]]],
        codes_provider: Optional[Callable[[], List[str]]] = None,
        default_interval: float = 1.0,
    ):
        self._snapshot_provider = snapshot_provider
        self._codes_provider = codes_provider
        self._default_interval = float(default_interval)

    def next_ticks(self, now: float) -> Dict[str, Dict[str, Any]]:
        codes = self._codes_provider() if self._codes_provider else []
        if not codes:
            return {}
        snapshot = self._snapshot_provider(codes)
        result: Dict[str, Dict[str, Any]] = {}
        for code, tick in snapshot.items():
            if not isinstance(tick, dict):
                continue
            normalized = dict(tick)
            normalized.setdefault("code", code)
            normalized.setdefault("_ts", now)
            result[code] = normalized
        return result

    def interval_for(self, code: str) -> float:
        return self._default_interval


class MockDataSource(TickSource):
    """仿真 mock 数据源：产生 per-code tick 数据，定时器注册到 EventDriver 统一优先队列。

    G5 重构：原仿真 tick 源重命名为 MockDataSource。tick 定时器不再使用内部 heapq，
    而是注册到 EventDriver 的统一优先队列（与边触发/TTL 同一队列）。

    关键行为：
      - 所有输出股票代码统一替换为 ``fz`` 前缀。
      - 每只股票分配 1~9 秒之间的固定随机间隔，同一只股票每次到达间隔相同，
        不同股票间隔不同（基于股票代码 hash 的确定性随机种子）。
      - tick 字段至少包含 code / open / high / low / close / volume / amount / _ts。
      - 定时器由 EventDriver 统一管理，MockDataSource 仅负责产生 tick 数据。
      - 仿真与实盘除 tick 请求方式外共用同一套代码。
    """

    def __init__(
        self,
        codes: Iterable[str],
        clock_start: float = 0.0,
        price_range: Iterable[float] = (5.0, 200.0),
        change_pct_std: float = 2.0,
        volume_lognorm_mu: float = 14.0,
        volume_lognorm_sigma: float = 2.0,
    ):
        self._clock_start = float(clock_start)
        self._price_range = tuple(price_range)
        self._change_pct_std = float(change_pct_std)
        self._volume_lognorm_mu = float(volume_lognorm_mu)
        self._volume_lognorm_sigma = float(volume_lognorm_sigma)

        self._codes: List[str] = sorted({_normalize_to_fz(c) for c in codes})
        self._intervals: Dict[str, int] = {}
        self._rngs: Dict[str, random.Random] = {}
        self._prev_prices: Dict[str, float] = {}
        self._price_trend: Dict[str, float] = {}
        self._event_driver: Any = None
        self._event_bus: Any = None

        self._init_state()

    # ------------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------------
    def _init_state(self) -> None:
        for code in self._codes:
            self._init_code(code)

    def _init_code(self, code: str) -> None:
        """初始化单只股票的随机状态与固定间隔（同股票固定，不同股票不同）。"""
        seed = _code_seed(code)
        rng = random.Random(seed)
        self._rngs[code] = rng
        self._intervals[code] = rng.randint(1, 9)
        self._prev_prices[code] = rng.uniform(*self._price_range)
        self._price_trend[code] = 0.0

    # ------------------------------------------------------------------
    # EventDriver 集成（G5：tick 定时器注册到统一优先队列）
    # ------------------------------------------------------------------
    def set_event_driver(self, event_driver: Any, event_bus: Any = None) -> None:
        """注入 EventDriver 与 EventBus 引用，供 register_tick_timers 使用。"""
        self._event_driver = event_driver
        self._event_bus = event_bus

    def register_tick_timers(self, now: float) -> None:
        """为每只股票创建 TimedEventSpec 并注册到 EventDriver 统一优先队列。

        tick 定时器与边触发/TTL 共用同一优先队列。到时由 EventDriver.fire_due
        触发 action，action 只发布 TickDue(code, ts) 事件；tick 数据生成由
        TickBarModule 订阅 TickDue 后完成。

        Args:
            now: 当前时间戳，首次触发时间 = now + interval。
        """
        if self._event_driver is None:
            return
        from .event_bus import TickDue

        for code in self._codes:
            interval = float(self._intervals[code])
            action = self._make_tick_action(code, TickDue)
            spec = TimedEventSpec(
                action=action,
                params={"kind": "tick", "code": code},
                interval=interval,
            )
            self._event_driver.add_spec(spec, first_fire_time=now + interval)

    def _make_tick_action(self, code: str, TickDueCls: Any) -> Callable[..., None]:
        """创建 tick 定时器 action：到时只发布 TickDue(code, ts) 事件。

        G2：引擎只发事件不执行计算，tick 数据生成由 TickBarModule 订阅 TickDue
        后调用 ``get_tick`` 完成。

        fire_time 由 EventDriver.fire_due 注入（spec 在 heapq 中实际到期的时刻），
        使 TickDue.ts 反映真实触发顺序，避免同一仿真步内所有 tick 事件共享
        self.clock 导致前端时间轴堆叠为一条线。
        """
        def action(params: Any, fire_time: Optional[float] = None) -> None:
            ts = fire_time if fire_time is not None else self._current_ts()
            if self._event_bus is not None:
                try:
                    self._event_bus.publish(TickDueCls(
                        code=code, ts=ts,
                    ))
                except Exception:
                    pass
        return action

    def _current_ts(self) -> float:
        """获取当前时间戳，与 EventDriver.fire_due 的 now 一致。

        G2 硬约束：统一委托 ``time_at(state)``，无 ``time.time()`` fallback。
        EventDriver 在 ``_init_pool_runtime`` 装配时即注入（engine.py:925），
        其 ``_state`` 即 PoolState 实例；``time_at`` 按 ``state.time_source.driver_type``
        分派：仿真返回虚拟秒（current_ts 缺失返回 0），实盘返回墙钟。
        """
        if self._event_driver is not None:
            state = getattr(self._event_driver, "_state", None)
            if state is not None:
                return time_at(state=state)
        # 仅在 EventDriver 未注入（异常装配路径）时退化到 _clock_start；
        # 不调用 time.time()——那会污染仿真时间坐标系。
        return float(self._clock_start) if self._clock_start else 0.0

    # ------------------------------------------------------------------
    # TickSource 接口
    # ------------------------------------------------------------------
    def next_ticks(self, now: float) -> Dict[str, Dict[str, Any]]:
        """返回空字典——tick 由 EventDriver 统一驱动，不再使用内部 heapq 调度。

        G5 重构后，tick 定时器注册到 EventDriver 统一优先队列，到时由
        ``EventDriver.fire_due`` 触发 action → ``get_tick`` → 发布 ``TickReceived``。
        本方法保留以满足 ``TickSource`` 抽象接口（实盘 ``RealTickSource`` 仍使用拉取模式）。
        """
        return {}

    def interval_for(self, code: str) -> float:
        return float(self._intervals.get(_normalize_to_fz(code), 1.0))

    # ------------------------------------------------------------------
    # tick 数据生成
    # ------------------------------------------------------------------
    def get_tick(self, code: str, ts: Optional[float] = None) -> Dict[str, Any]:
        """生成单只股票的 tick 数据（供 TickBarModule 订阅 TickDue 后调用）。

        Args:
            code: 股票代码（自动归一化为 fz 前缀）。
            ts: 时间戳；None 时使用当前虚拟时钟或墙钟。

        Returns:
            ``{code, open, high, low, close, volume, amount, pre_close, _ts}`` 字典。
        """
        code = _normalize_to_fz(code)
        if code not in self._rngs:
            self.add_code(code)
        if ts is None:
            ts = self._current_ts()
        tick = self._generate_tick(code, ts)
        self._prev_prices[code] = float(tick.get("close", 0.0))
        return tick

    def _generate_tick(self, code: str, tick_time: float) -> Dict[str, Any]:
        rng = self._rngs[code]
        prev_close = self._prev_prices.get(code, 0.0)
        if prev_close <= 0:
            prev_close = rng.uniform(*self._price_range)

        # 价格随机游走：保留均值回归，避免趋势无限放大
        # I98：trend 衰减系数从 0.9 降到 0.5，shock 权重从 0.1 升到 0.5，
        # 让 trend 更快响应 shock，在 +/– 之间切换，产生金叉/死叉交替
        # （0.9 衰减太慢，trend 一旦形成就持续单调，KDJ/MACD 无法穿越）
        shock = rng.gauss(0, self._change_pct_std)
        trend = self._price_trend.get(code, 0.0) * 0.5 + shock * 0.5
        self._price_trend[code] = trend
        price = prev_close * (1 + trend / 100)
        # 价格限制在仿真配置区间内，避免随机游走过高导致资金不足
        price = max(min(price, self._price_range[1]), self._price_range[0])
        price = max(price, 0.01)

        open_p = round(price * (1 + rng.gauss(0, 0.01) / 100), 2)
        high = round(price * (1 + abs(rng.gauss(0, 0.01)) / 100), 2)
        low = round(price * (1 - abs(rng.gauss(0, 0.01)) / 100), 2)
        high, low = max(high, low, price), min(high, low, price)
        high = min(high, self._price_range[1])
        low = max(low, self._price_range[0])
        volume = int(rng.lognormvariate(self._volume_lognorm_mu, self._volume_lognorm_sigma))
        amount = round(volume * price, 2)

        return {
            "code": code,
            "open": open_p,
            "high": high,
            "low": low,
            "close": round(price, 2),
            "volume": volume,
            "amount": amount,
            "pre_close": round(prev_close, 2),
            "_ts": tick_time,
        }

    # ------------------------------------------------------------------
    # 公共查询
    # ------------------------------------------------------------------
    @property
    def codes(self) -> List[str]:
        return list(self._codes)

    @property
    def intervals(self) -> Dict[str, int]:
        return dict(self._intervals)

    @property
    def _tick_intervals(self) -> Dict[str, int]:
        return dict(self._intervals)

    def add_code(self, code: str) -> None:
        """运行时动态增加监控代码，并注册 tick 定时器到 EventDriver。"""
        norm = _normalize_to_fz(code)
        if norm in self._codes:
            return
        self._codes.append(norm)
        self._codes.sort()
        self._init_code(norm)
        self._register_tick_timer_for(norm)

    def _register_tick_timer_for(self, code: str) -> None:
        """为单只股票注册 tick 定时器到 EventDriver（若已注入）。"""
        if self._event_driver is None:
            return
        from .event_bus import TickDue
        now = self._current_ts()
        interval = float(self._intervals[code])
        action = self._make_tick_action(code, TickDue)
        spec = TimedEventSpec(
            action=action,
            params={"kind": "tick", "code": code},
            interval=interval,
        )
        self._event_driver.add_spec(spec, first_fire_time=now + interval)

    # ------------------------------------------------------------------
    # 步进接口（供 RuntimeSimulator._step_once 调用）
    # ------------------------------------------------------------------
    def step(self, delta_seconds: float) -> Dict[str, Any]:
        """步进虚拟时钟 delta_seconds 秒。

        G5/G2 重构后，tick 生成由 EventDriver 统一驱动（register_tick_timers 注册的
        定时器），本方法仅推进虚拟时钟，返回空 tick_data。实际 tick 定时器由
        ``EventDriver.fire_due`` 触发 action → 发布 ``TickDue``，下游
        ``TickBarModule`` 订阅后调用 ``get_tick`` → 发布 ``TickReceived`` 处理。

        返回字典格式::

            {
                "changed_codes": [],
                "tick_data": {},
                "bar_data": {},
                "virtual_clock": <new_clock>,
            }
        """
        self._clock_start += float(delta_seconds)
        return {
            "changed_codes": [],
            "tick_data": {},
            "bar_data": {},
            "virtual_clock": self._clock_start,
        }

    @property
    def virtual_clock(self) -> float:
        """当前虚拟时钟值。"""
        return self._clock_start


def _hash_tick(tick: Dict[str, Any]) -> str:
    """对单只股票 tick 做确定性摘要（I26：与 data_updater 路径统一的 per-code hash）。

    排除 ``_ts`` / ``_hash`` 元数据字段，使 per-code _hash 仅依赖行情内容
    （open/high/low/close/volume/...），不受时间戳影响。这保证：
      - 相同行情内容在不同时间到达（replay vs live）产生相同 per-code _hash
      - ``update_latest_tick``（全量替换）与 ``apply_data``（增量更新）两条路径
        对相同内容产生相同 per-code _hash，进而相同聚合 hash 与缓存键
      - ``update_latest_tick`` 重复调用（无 ``_ts`` 输入）保持幂等
    """
    content = {k: v for k, v in tick.items() if k not in ("_ts", "_hash")}
    try:
        payload = json.dumps(content, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        payload = str(sorted(content.items()))
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


# ════════════════════════════════════════════════════════════════
# Section: 公共时间工具函数（从 execution_module 迁移，供所有模块使用）
# ════════════════════════════════════════════════════════════════


def _hms_to_seconds(h: int, m: int, s: int) -> int:
    """将时分秒转换为当天秒数（h*3600 + m*60 + s）。"""
    return h * 3600 + m * 60 + s


def time_at(source: Optional[str] = None, state: Any = None) -> float:
    """统一时间入口。三模式差异仅在参数（driver_type），不在代码分支。

    G2 硬约束：仿真/实盘同代码，仅由 ``state.time_source.driver_type`` 决定时间源。
    - ``source="wall"`` 或 ``state is None``：显式墙钟入口（如 ``_now()`` 无 state 上下文），
      返回 ``time.time()``。
    - ``driver_type in ("virtual", "sequence")``：返回 ``current_ts``（虚拟秒）；
      ``current_ts`` 缺失返回 0.0（仿真冷启动前合法值）。
    - ``driver_type in ("wall_clock", None)``：实盘模式，``current_ts`` 优先，否则 ``time.time()``。

    不做任何"current_ts 是真实秒则返回 0"的 hack——那会形成仿真专用分支，违反 G2。
    current_ts 的正确性由设置方保证（``_post_init_mode_state`` 仿真启动时设虚拟时钟，
    ``run_pool`` 实盘启动时设墙钟）。
    """
    if source == "wall" or state is None:
        return time.time()
    ts_cfg = getattr(state, "time_source", None) or {}
    driver = ts_cfg.get("driver_type") if isinstance(ts_cfg, dict) else None
    cur_ts = ts_cfg.get("current_ts") if isinstance(ts_cfg, dict) else None
    if driver in ("virtual", "sequence"):
        if cur_ts is None:
            return 0.0
        try:
            return float(cur_ts)
        except (TypeError, ValueError):
            return 0.0
    # 实盘模式（wall_clock / None）：current_ts 优先，否则 time.time()
    if cur_ts is not None:
        try:
            return float(cur_ts)
        except (TypeError, ValueError):
            pass
    return time.time()


def _safe_timestamp(dt_obj: Any) -> float:
    """安全获取 datetime 的 timestamp，捕获 Windows 上旧时间戳的 OSError。"""
    try:
        return dt_obj.timestamp()
    except (OSError, ValueError):
        return time.time()


_OFFSET_THRESHOLD = 1e8


def is_offset_of_day(sec: float) -> bool:
    """判断 sec 是当日秒数偏移（< 1e8）还是 Unix 时间戳。"""
    return abs(sec) < _OFFSET_THRESHOLD


def anchor_to_today(sec: float):
    """将当日秒数偏移锚定到本日 00:00，返回 datetime。"""
    from datetime import datetime, timedelta
    base = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return base + timedelta(seconds=sec)


def time_now_unix(state: Any) -> float:
    """返回当前时间的 Unix 时间戳，用于 TTL entry_time 比较。"""
    sec = time_at(state=state)
    if is_offset_of_day(sec):
        return anchor_to_today(sec).timestamp()
    return sec


# ════════════════════════════════════════════════════════════════
# Section: EdgeState 边级运行时表（从 execution_module 迁移，供所有模块共用）
# ════════════════════════════════════════════════════════════════


class EdgeStateMixin:
    """EdgeState 表级访问方法集合。

    将公式结果缓存与过滤输入指纹的读写从 ``EdgeState`` 核心类中剥离，
    使其属性/方法数满足架构约束。
    """

    def get_formula_result(self, formula_ref: Any, bar_hash: str) -> Any:
        return self.formula_results.get((formula_ref, bar_hash))

    def set_formula_result(self, formula_ref: Any, bar_hash: str, result: Any) -> None:
        self.formula_results[(formula_ref, bar_hash)] = result

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


__all__ = [
    # base
    "Node", "Edge",
    # specs
    "TimingSpec", "FilterSpec", "PropagateSpec", "ActionSpec", "TTLSpec",
    "CandidateRange", "ReloadSchedule", "DZH_COL_MAP",
    # nodes
    "DecorativeNode", "TextLabelNode", "ContainerNode", "StateColumnNode",
    "DiscardPoolNode", "ExecutionOrderNode", "FlowArrowNode",
    "StatePoolNode", "ResultPoolNode", "ConditionNode", "CandidatePoolNode",
    "all_dzh_types", "all_tdx_types",
    # edges
    "ConditionalEdge", "UnconditionalEdge",
    "all_dzh_edge_attrs", "all_edge_source_types",
    # evaluators
    "Evaluator", "IndicatorEvaluator", "ConditionFormulaEvaluator",
    "ExpertSystemEvaluator", "FinancialScalarEvaluator", "MarketScalarEvaluator",
    "SetOperationEvaluator", "FINANCIAL_INDICATORS", "MARKET_FIELDS",
    "evaluator_from_filter_spec", "all_evaluator_types",
    # noperate evaluation (re-exported for screening_module backward compat)
    "_noperate_data", "_NOPERATE_RULES", "_RANK_MODES", "_COMBINE_OPS",
    "_build_op_ctx", "_eval_op",
    "_tie_exact_rank", "_tie_slice", "_TIE_HANDLERS", "_resolve_rank",
    "_DERIVED_BIN_OPS", "_DERIVED_CMP_OPS", "_DERIVED_BOOL_OPS", "_DERIVED_FUNCS",
    "_eval_derived_ast", "_eval_derived_expr",
    # builtin formulas lookup
    "_BUILTIN_FORMULAS", "_BUILTIN_FORMULA_INDEX", "_BUILTIN_FORMULA_INFO",
    "_lookup_builtin_script", "_lookup_builtin_formula_info",
    # tick_source
    "TickSource", "RealTickSource", "MockDataSource", "_normalize_to_fz", "is_fz_code",
    # utilities
    "_hash_tick",
    # time utilities
    "_hms_to_seconds", "time_at", "_safe_timestamp",
    "is_offset_of_day", "anchor_to_today", "time_now_unix",
    # TDX nperiod → period mapping (migrated from screening_module)
    "_TDX_NPERIOD_TO_PERIOD", "_nperiod_to_period",
    # intersection evaluator (migrated from screening_module)
    "evaluate_intersection",
    # edge state (migrated from execution_module)
    "EdgeState", "EdgeStateMixin",
]
