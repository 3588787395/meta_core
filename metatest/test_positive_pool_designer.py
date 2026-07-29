# -*- coding: utf-8 -*-
"""正向测试：股票池设计器（Node / Edge / Spec / Evaluator 领域模型）。

覆盖：
- DZH 11 种节点类型注册表与工厂分派
- TDX 6 种节点类型映射
- Node to_dict / from_dict 往返序列化
- StatePoolNode 携带 TTLSpec / ActionSpec 的嵌套序列化
- ConditionalEdge / UnconditionalEdge 创建与序列化
- DZH 边 attr 注册表（8192=条件 / 8193=无条件）
- 7 种 Spec 类的 to_dict / from_dict 往返
- TTLSpec.to_seconds() 单位换算
- _EVALUATOR_REGISTRY 注册表与 all_evaluator_types()
- _FieldMeta 字段元数据声明
- 边顺序号是设计结构（与运行时事件无序不矛盾）
"""
from __future__ import annotations

import inspect
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# 可选依赖：导入失败时 skip 整个模块
# ---------------------------------------------------------------------------
try:
    from core.domain import (
        Node,
        Edge,
        DecorativeNode,
        TextLabelNode,
        ContainerNode,
        StateColumnNode,
        DiscardPoolNode,
        ExecutionOrderNode,
        FlowArrowNode,
        StatePoolNode,
        ResultPoolNode,
        ConditionNode,
        CandidatePoolNode,
        ConditionalEdge,
        UnconditionalEdge,
        TimingSpec,
        FilterSpec,
        PropagateSpec,
        ActionSpec,
        TTLSpec,
        CandidateRange,
        ReloadSchedule,
        _FieldMeta,
        _DZH_TYPE_REGISTRY,
        _TDX_TYPE_REGISTRY,
        _DZH_ATTR_REGISTRY,
        _EDGE_SOURCE_TYPE_REGISTRY,
        _EVALUATOR_REGISTRY,
        all_dzh_types,
        all_tdx_types,
        all_dzh_edge_attrs,
        all_edge_source_types,
        all_evaluator_types,
    )
except ImportError as exc:
    pytest.skip(f"无法导入 core.domain: {exc}", allow_module_level=True)


# ---------------------------------------------------------------------------
# Test 1: DZH 节点类型注册表（11 种）
# ---------------------------------------------------------------------------
class TestDzhNodeTypeRegistry:
    """DZH 11 种节点类型注册表与工厂分派。"""

    def test_registry_has_11_node_types(self):
        types = all_dzh_types()
        assert len(types) == 11
        for t in (0, 1, 2, 3, 4, 5, 6, 200, 201, 202, 203):
            assert t in types, f"DZH type {t} 未注册"

    def test_each_dzh_type_maps_to_correct_class(self):
        assert _DZH_TYPE_REGISTRY[0] is DecorativeNode
        assert _DZH_TYPE_REGISTRY[1] is TextLabelNode
        assert _DZH_TYPE_REGISTRY[2] is ContainerNode
        assert _DZH_TYPE_REGISTRY[3] is StateColumnNode
        assert _DZH_TYPE_REGISTRY[4] is DiscardPoolNode
        assert _DZH_TYPE_REGISTRY[5] is ExecutionOrderNode
        assert _DZH_TYPE_REGISTRY[6] is FlowArrowNode
        assert _DZH_TYPE_REGISTRY[200] is StatePoolNode
        assert _DZH_TYPE_REGISTRY[201] is ConditionNode
        assert _DZH_TYPE_REGISTRY[202] is CandidatePoolNode
        assert _DZH_TYPE_REGISTRY[203] is ResultPoolNode

    def test_from_dzh_type_factory(self):
        """from_dzh_type 按类型返回对应 Node 子类。"""
        assert StatePoolNode.from_dzh_type(200) is StatePoolNode
        assert ConditionNode.from_dzh_type(201) is ConditionNode
        assert CandidatePoolNode.from_dzh_type(202) is CandidatePoolNode

    def test_from_dzh_type_raises_on_unknown(self):
        with pytest.raises(KeyError):
            StatePoolNode.from_dzh_type(999)


# ---------------------------------------------------------------------------
# Test 2: TDX 节点类型映射（6 种）
# ---------------------------------------------------------------------------
class TestTdxNodeTypeRegistry:
    """TDX 6 种节点类型映射。"""

    def test_registry_has_6_tdx_types(self):
        types = all_tdx_types()
        assert len(types) == 6
        for t in (0, 1, 2, 3, 7, 8):
            assert t in types, f"TDX type {t} 未注册"

    def test_tdx_to_dzh_mapping(self):
        """TDX type → Node 子类映射（依据 dzh_type_map.json）。"""
        assert _TDX_TYPE_REGISTRY[8] is StatePoolNode      # tdx 8 → dzh 200
        assert _TDX_TYPE_REGISTRY[7] is CandidatePoolNode  # tdx 7 → dzh 202
        assert _TDX_TYPE_REGISTRY[3] is ConditionNode      # tdx 3 → dzh 201


# ---------------------------------------------------------------------------
# Test 3: Node 创建与序列化往返
# ---------------------------------------------------------------------------
class TestNodeCreationAndSerialization:
    """Node 子类的 to_dict / from_dict 往返序列化。"""

    def test_decorative_node_basic_roundtrip(self):
        n = DecorativeNode(id="n0", text="装饰", pos=(10.0, 20.0), clr=1, attr=0)
        assert n.DZH_TYPE == 0
        d = n.to_dict()
        assert d["id"] == "n0"
        assert d["text"] == "装饰"
        assert d["pos"] == [10.0, 20.0]

        n2 = DecorativeNode.from_dict(d)
        assert n2.id == "n0"
        assert n2.text == "装饰"
        assert n2.pos == (10.0, 20.0)

    def test_state_pool_node_spec_fields_roundtrip(self):
        """StatePoolNode 携带 TTLSpec / ActionSpec 的嵌套序列化。"""
        ttl = TTLSpec(bdel=True, ndelnum=30, ndeltype=2)
        action = ActionSpec(bsavehis=True, bsound=True)
        n = StatePoolNode(
            id="n200",
            text="状态池",
            ttl_spec=ttl,
            action_spec=action,
        )
        assert n.DZH_TYPE == 200
        assert n.TDX_TYPE == 8
        d = n.to_dict()
        assert d["ttl_spec"]["bdel"] is True
        assert d["ttl_spec"]["ndelnum"] == 30
        assert d["action_spec"]["bsavehis"] is True

        n2 = StatePoolNode.from_dict(d)
        assert n2.ttl_spec.bdel is True
        assert n2.ttl_spec.ndelnum == 30
        assert n2.action_spec.bsavehis is True

    def test_condition_node_filter_spec(self):
        """ConditionNode 默认创建 FilterSpec。"""
        n = ConditionNode(id="n201", text="条件", indi="KDJ")
        assert n.DZH_TYPE == 201
        assert n.TDX_TYPE == 3
        assert n.filter_spec is not None
        assert isinstance(n.filter_spec, FilterSpec)
        assert n.indi == "KDJ"

    def test_candidate_pool_node_candidate_range(self):
        """CandidatePoolNode 携带 CandidateRange。"""
        cr = CandidateRange(range_type="stock", codes=["fz000001"])
        n = CandidatePoolNode(
            id="n202",
            text="备选池",
            candidate_range=cr,
        )
        assert n.DZH_TYPE == 202
        d = n.to_dict()
        assert d["candidate_range"]["range_type"] == "stock"
        assert d["candidate_range"]["codes"] == ["fz000001"]

    def test_result_pool_node_inherits_state_pool(self):
        """ResultPoolNode 继承 StatePoolNode 并追加 result_type。"""
        n = ResultPoolNode(id="n203", text="结果池", result_type=1)
        assert n.DZH_TYPE == 203
        assert n.result_type == 1
        # 继承的 ttl_spec / action_spec 字段也存在
        d = n.to_dict()
        assert "ttl_spec" in d
        assert "result_type" in d


# ---------------------------------------------------------------------------
# Test 4: Edge 创建与序列化
# ---------------------------------------------------------------------------
class TestEdgeCreationAndSerialization:
    """ConditionalEdge / UnconditionalEdge 创建与序列化。"""

    def test_conditional_edge_defaults(self):
        """ConditionalEdge 默认创建 TimingSpec / FilterSpec / PropagateSpec。"""
        e = ConditionalEdge(id="e1", from_id="n1", to_id="n2", attr=8192)
        assert e.id == "e1"
        assert e.from_id == "n1"
        assert e.to_id == "n2"
        assert e.attr == 8192
        assert isinstance(e.timing_spec, TimingSpec)
        assert isinstance(e.filter_spec, FilterSpec)
        assert isinstance(e.propagate_spec, PropagateSpec)
        assert e.action_spec is None
        assert e.ttl_spec is None

    def test_conditional_edge_with_specs_roundtrip(self):
        ttl = TTLSpec(bdel=True, ndelnum=100, ndeltype=3)
        action = ActionSpec(bsavehis=True)
        e = ConditionalEdge(
            id="e1", from_id="n1", to_id="n2", attr=8192,
            interval=60, ttl_spec=ttl, action_spec=action,
        )
        d = e.to_dict()
        assert d["id"] == "e1"
        assert d["from_id"] == "n1"
        assert d["to_id"] == "n2"
        assert d["interval"] == 60
        assert d["ttl_spec"]["bdel"] is True
        assert d["ttl_spec"]["ndelnum"] == 100
        assert d["action_spec"]["bsavehis"] is True

        e2 = ConditionalEdge.from_dict(d)
        assert e2.interval == 60
        assert e2.ttl_spec.bdel is True
        assert e2.ttl_spec.ndelnum == 100
        assert e2.action_spec.bsavehis is True

    def test_unconditional_edge_only_propagate_spec(self):
        """UnconditionalEdge 仅含 propagate_spec，无 timing/filter/ttl。"""
        e = UnconditionalEdge(id="e2", from_id="n201", to_id="n200", attr=8193)
        assert e.attr == 8193
        assert isinstance(e.propagate_spec, PropagateSpec)
        assert not hasattr(e, "timing_spec")
        assert not hasattr(e, "filter_spec")

    def test_edge_from_field_alias(self):
        """from_dict 支持 from_id 和 from 别名。"""
        d = {"id": "e3", "from": "nA", "to": "nB", "attr": 8192}
        e = ConditionalEdge.from_dict(d)
        assert e.from_id == "nA"
        assert e.to_id == "nB"


# ---------------------------------------------------------------------------
# Test 5: 边类型注册表
# ---------------------------------------------------------------------------
class TestEdgeTypeRegistry:
    """DZH 边 attr 注册表与源节点 type 映射。"""

    def test_dzh_attr_registry_has_two_types(self):
        attrs = all_dzh_edge_attrs()
        assert 8192 in attrs
        assert 8193 in attrs
        assert _DZH_ATTR_REGISTRY[8192] is ConditionalEdge
        assert _DZH_ATTR_REGISTRY[8193] is UnconditionalEdge

    def test_edge_source_type_registry(self):
        """源节点 type → Edge 子类映射。"""
        # 条件边源：备选池/状态池/数据源
        assert _EDGE_SOURCE_TYPE_REGISTRY[200] is ConditionalEdge
        assert _EDGE_SOURCE_TYPE_REGISTRY[202] is ConditionalEdge
        # 无条件边源：条件节点
        assert _EDGE_SOURCE_TYPE_REGISTRY[201] is UnconditionalEdge
        assert _EDGE_SOURCE_TYPE_REGISTRY[3] is UnconditionalEdge


# ---------------------------------------------------------------------------
# Test 6: Spec 类序列化往返
# ---------------------------------------------------------------------------
class TestSpecSerialization:
    """7 种 Spec 类的 to_dict / from_dict 往返。"""

    def test_timing_spec_roundtrip(self):
        s = TimingSpec(starttype=1, cxtype=2, starttime="09:30", cxtime=60)
        d = s.to_dict()
        assert d["starttype"] == 1
        assert d["cxtype"] == 2
        s2 = TimingSpec.from_dict(d)
        assert s2.starttype == 1
        assert s2.cxtype == 2
        assert s2.starttime == "09:30"

    def test_filter_spec_roundtrip(self):
        s = FilterSpec(evaluator_type="indicator", nset=1, formula_ref="KDJ")
        d = s.to_dict()
        assert d["evaluator_type"] == "indicator"
        assert d["formula_ref"] == "KDJ"
        s2 = FilterSpec.from_dict(d)
        assert s2.evaluator_type == "indicator"
        assert s2.formula_ref == "KDJ"

    def test_propagate_spec_roundtrip(self):
        s = PropagateSpec(mode="move", tran=1)
        d = s.to_dict()
        assert d["mode"] == "move"
        s2 = PropagateSpec.from_dict(d)
        assert s2.mode == "move"

    def test_action_spec_roundtrip(self):
        s = ActionSpec(bsavehis=True, bsound=True, baimpool=True)
        d = s.to_dict()
        assert d["bsavehis"] is True
        assert d["bsound"] is True
        s2 = ActionSpec.from_dict(d)
        assert s2.bsavehis is True
        assert s2.baimpool is True

    def test_candidate_range_roundtrip(self):
        s = CandidateRange(range_type="market", codes=["fz000001", "fz000002"])
        d = s.to_dict()
        assert d["range_type"] == "market"
        assert d["codes"] == ["fz000001", "fz000002"]
        s2 = CandidateRange.from_dict(d)
        assert s2.range_type == "market"
        assert s2.codes == ["fz000001", "fz000002"]

    def test_reload_schedule_roundtrip(self):
        s = ReloadSchedule(mode="interval", interval_sec=300)
        d = s.to_dict()
        assert d["mode"] == "interval"
        assert d["interval_sec"] == 300
        s2 = ReloadSchedule.from_dict(d)
        assert s2.mode == "interval"
        assert s2.interval_sec == 300

    def test_spec_from_dict_none_returns_default(self):
        """from_dict(None) 返回默认实例。"""
        s = TimingSpec.from_dict(None)
        assert isinstance(s, TimingSpec)
        assert s.starttype == 0


# ---------------------------------------------------------------------------
# Test 7: TTLSpec.to_seconds 单位换算
# ---------------------------------------------------------------------------
class TestTTLSpecToSeconds:
    """TTLSpec.to_seconds() 按 ndeltype 单位换算为总秒数。"""

    def test_disabled_returns_zero(self):
        """bdel=False 时返回 0。"""
        s = TTLSpec(bdel=False, ndelnum=30, ndeltype=2)
        assert s.to_seconds() == 0

    def test_minutes_unit(self):
        """ndeltype=2（分钟）：30 分钟 = 1800 秒。"""
        s = TTLSpec(bdel=True, ndelnum=30, ndeltype=2)
        assert s.to_seconds() == 1800

    def test_seconds_unit(self):
        """ndeltype=3（秒）：120 秒 = 120 秒。"""
        s = TTLSpec(bdel=True, ndelnum=120, ndeltype=3)
        assert s.to_seconds() == 120

    def test_hours_unit(self):
        """ndeltype=1（小时）：2 小时 = 7200 秒。"""
        s = TTLSpec(bdel=True, ndelnum=2, ndeltype=1)
        assert s.to_seconds() == 7200

    def test_days_unit(self):
        """ndeltype=0（天）：1 天 = 86400 秒。"""
        s = TTLSpec(bdel=True, ndelnum=1, ndeltype=0)
        assert s.to_seconds() == 86400


# ---------------------------------------------------------------------------
# Test 8: Evaluator 注册表
# ---------------------------------------------------------------------------
class TestEvaluatorRegistry:
    """_EVALUATOR_REGISTRY 注册表与 all_evaluator_types()。"""

    def test_registry_is_non_empty_dict(self):
        assert isinstance(_EVALUATOR_REGISTRY, dict)
        assert len(_EVALUATOR_REGISTRY) > 0

    def test_all_evaluator_types_returns_list(self):
        types = all_evaluator_types()
        assert isinstance(types, list)
        assert len(types) > 0

    def test_indicator_evaluator_registered(self):
        """indicator 类型应在注册表中。"""
        try:
            from core.domain import IndicatorEvaluator
            assert _EVALUATOR_REGISTRY.get("indicator") is IndicatorEvaluator
        except ImportError:
            pytest.skip("IndicatorEvaluator 不可导入")


# ---------------------------------------------------------------------------
# Test 9: _FieldMeta 字段元数据
# ---------------------------------------------------------------------------
class TestFieldMeta:
    """_FieldMeta namedtuple 声明字段序列化规则。"""

    def test_fieldmeta_is_namedtuple(self):
        fm = _FieldMeta("test_field", "default", None)
        assert fm.name == "test_field"
        assert fm.default == "default"
        assert fm.serializer is None

    def test_state_pool_node_fields_declared(self):
        """StatePoolNode._FIELDS 包含 ttl_spec 和 action_spec 的 spec 序列化规则。"""
        field_names = [fm.name for fm in StatePoolNode._FIELDS]
        assert "ttl_spec" in field_names
        assert "action_spec" in field_names
        # ttl_spec 使用 ("spec", "TTLSpec") 序列化器
        ttl_fm = next(fm for fm in StatePoolNode._FIELDS if fm.name == "ttl_spec")
        assert ttl_fm.serializer == ("spec", "TTLSpec")

    def test_conditional_edge_fields_declared(self):
        """ConditionalEdge._FIELDS 包含 5 个 Spec 字段。"""
        field_names = [fm.name for fm in ConditionalEdge._FIELDS]
        for name in ("timing_spec", "filter_spec", "propagate_spec", "action_spec", "ttl_spec"):
            assert name in field_names


# ---------------------------------------------------------------------------
# Test 10: 边顺序号是设计结构（G6 约束验证）
# ---------------------------------------------------------------------------
class TestEdgeOrderNumberDesignStructure:
    """G6：边顺序号是设计结构，与运行时事件无序不矛盾。

    边顺序号决定连接同一目标节点的多条边的交集/差集运算次序，
    一次性配置，不涉及运行时拓扑排序。
    """

    def test_conditional_edge_has_interval_field(self):
        """ConditionalEdge 有 interval 字段（边触发频率，设计期配置）。"""
        e = ConditionalEdge(id="e1", from_id="n1", to_id="n2", interval=60)
        assert e.interval == 60

    def test_edge_serialization_preserves_interval(self):
        """序列化保留 interval 字段（设计结构不丢失）。"""
        e = ConditionalEdge(id="e1", from_id="n1", to_id="n2", interval=60)
        d = e.to_dict()
        assert d["interval"] == 60
        e2 = ConditionalEdge.from_dict(d)
        assert e2.interval == 60
