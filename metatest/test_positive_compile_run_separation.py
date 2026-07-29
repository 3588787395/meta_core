"""Task 18.2: 编译-运行分离验证正测试。

验证 ``core/execution_module.compile(pool_config) -> CompiledPool`` 的编译期
预计算语义：
  - ``compile`` 返回 ``CompiledPool`` 实例（非 dict）
  - ``CompiledPool`` 含 13 个字段（dataclass fields）
  - ``edge_order`` 来自 ``edge.params._order``，非拓扑排序
  - 边端点解析（sid/tid）正确
  - 边类型判定（conditional/unconditional）编译期完成
  - 节点角色映射编译期产出
  - 运行时无 ``json.loads`` / ``_parse_edge`` / ``_build_adjacency`` 调用
    （通过 Grep engine.py 验证源代码无此调用）
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest


# 项目根目录定位（与 conftest.py 一致）
_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent
_ENGINE_PY = _PROJECT_ROOT / "core" / "engine.py"


# ---------------------------------------------------------------------------
# 1. compile 返回 CompiledPool 实例
# ---------------------------------------------------------------------------


def test_compile_returns_compiled_pool():
    """compile(pool_config) 应返回 CompiledPool 实例（非 dict）。"""
    from core.execution_module import compile, CompiledPool

    pool_config = {
        "nodes": {
            "src": {"type": "candidate", "label": "源节点"},
            "tgt": {"type": "target", "label": "目标节点"},
        },
        "edges": [
            {"id": "e1", "from": "src", "to": "tgt",
             "params": {"_order": 0, "starttype": 0, "cxtype": 0}},
        ],
    }
    compiled = compile(pool_config)
    # 应为 CompiledPool 实例
    assert isinstance(compiled, CompiledPool)
    # 非裸 dict
    assert not isinstance(compiled, dict)


# ---------------------------------------------------------------------------
# 2. CompiledPool 含 13 个字段
# ---------------------------------------------------------------------------


def test_compiled_pool_structure(compiled_pool):
    """CompiledPool dataclass 应包含 13 个字段（spec.md L97-121 定义）。

    13 字段：nodes / node_type / edges / edge_endpoints / edge_order /
    edge_type / edge_filter_spec / edge_timing_spec / edge_propagate_spec /
    out_edges / in_edges / source_nodes / node_role
    """
    expected_fields = {
        "nodes",
        "node_type",
        "edges",
        "edge_endpoints",
        "edge_order",
        "edge_type",
        "edge_filter_spec",
        "edge_timing_spec",
        "edge_propagate_spec",
        "out_edges",
        "in_edges",
        "source_nodes",
        "node_role",
    }
    # 通过 dataclasses.fields 验证
    from dataclasses import fields as dc_fields

    actual_field_names = {f.name for f in dc_fields(compiled_pool)}
    assert expected_fields == actual_field_names, (
        f"CompiledPool 字段不匹配：期望 {expected_fields}，实际 {actual_field_names}"
    )
    assert len(actual_field_names) == 13


# ---------------------------------------------------------------------------
# 3. edge_order 从 edge.params._order 读取，非拓扑排序
# ---------------------------------------------------------------------------


def test_edge_order_from_params_order():
    """edge_order 严格按 edge.params._order 升序排列，非拓扑排序。

    构造一个拓扑上「逆序」的池：tgt 在前 src 在后，但 _order 标记 src→tgt
    应在前执行，验证 edge_order 不按节点出现顺序而是按 _order。
    """
    from core.execution_module import compile

    # 构造 3 条边，_order 分别为 2、0、1
    pool_config = {
        "nodes": {
            "n1": {"type": "candidate"},
            "n2": {"type": "state"},
            "n3": {"type": "target"},
        },
        "edges": [
            {"id": "e_late", "from": "n2", "to": "n3",
             "params": {"_order": 2, "starttype": 0, "cxtype": 0}},
            {"id": "e_first", "from": "n1", "to": "n2",
             "params": {"_order": 0, "starttype": 0, "cxtype": 0}},
            {"id": "e_mid", "from": "n2", "to": "n3",
             "params": {"_order": 1, "starttype": 0, "cxtype": 0}},
        ],
    }
    compiled = compile(pool_config)
    # edge_order 应按 _order 升序排列：e_first(_order=0), e_mid(_order=1), e_late(_order=2)
    assert compiled.edge_order == ["e_first", "e_mid", "e_late"], (
        f"edge_order 应按 _order 升序，实际：{compiled.edge_order}"
    )


# ---------------------------------------------------------------------------
# 4. 边端点解析（sid/tid）正确
# ---------------------------------------------------------------------------


def test_edge_endpoints_resolved(compiled_pool):
    """edge_endpoints[eid] 应为 (sid, tid) 元组，端点解析正确。"""
    # compiled_pool fixture 使用 src → tgt 单边配置
    # edge_endpoints 应包含 ('src', 'tgt')
    assert "e1" in compiled_pool.edge_endpoints
    sid, tid = compiled_pool.edge_endpoints["e1"]
    assert sid == "src"
    assert tid == "tgt"
    # 端点应为元组
    assert isinstance(compiled_pool.edge_endpoints["e1"], tuple)


def test_edge_endpoints_multiple_nodes():
    """多节点多边的端点解析全部正确。"""
    from core.execution_module import compile

    pool_config = {
        "nodes": {
            "a": {"type": "candidate"},
            "b": {"type": "state"},
            "c": {"type": "state"},
            "d": {"type": "target"},
        },
        "edges": [
            {"id": "e1", "from": "a", "to": "b", "params": {"_order": 0}},
            {"id": "e2", "from": "b", "to": "c", "params": {"_order": 1}},
            {"id": "e3", "from": "c", "to": "d", "params": {"_order": 2}},
        ],
    }
    compiled = compile(pool_config)
    assert compiled.edge_endpoints == {
        "e1": ("a", "b"),
        "e2": ("b", "c"),
        "e3": ("c", "d"),
    }


# ---------------------------------------------------------------------------
# 5. 边类型判定（conditional/unconditional）编译期完成
# ---------------------------------------------------------------------------


def test_edge_type_judgment_at_compile_time():
    """边类型（conditional/unconditional）应在编译期判定并写入 edge_type。"""
    from core.execution_module import compile

    pool_config = {
        "nodes": {
            "src": {"type": "candidate"},
            "tgt": {"type": "target"},
        },
        "edges": [
            # 无条件边：params 仅含 _order，无 timing/filter 关键字非默认值
            {"id": "e_uncond", "from": "src", "to": "tgt",
             "params": {"_order": 0, "starttype": 0, "cxtype": 0}},
            # 条件边：含 starttype=非默认值（spec 字符串而非 0）
            {"id": "e_cond", "from": "src", "to": "tgt",
             "params": {"_order": 1, "starttype": 1, "cxtype": 1,
                        "starttime": 100, "cxtime": 200}},
        ],
    }
    compiled = compile(pool_config)
    # 无条件边判定为 'unconditional'
    assert compiled.edge_type.get("e_uncond") == "unconditional"
    # 条件边判定为 'conditional'
    assert compiled.edge_type.get("e_cond") == "conditional"
    # 类型字段在编译期一次性写入，运行期只读
    assert "e_uncond" in compiled.edge_type
    assert "e_cond" in compiled.edge_type


# ---------------------------------------------------------------------------
# 6. 节点角色映射编译期产出
# ---------------------------------------------------------------------------


def test_node_role_mapping_at_compile_time():
    """node_role 字典在编译期一次性产出，运行期只读。"""
    from core.execution_module import compile

    pool_config = {
        "nodes": {
            "cand": {"type": "candidate"},
            "st": {"type": "state"},
            "tgt": {"type": "target"},
            "cond": {"type": "condition"},
            "dsc": {"type": "discard"},
        },
        "edges": [],
    }
    compiled = compile(pool_config)
    # 5 种角色全部正确映射
    assert compiled.node_role == {
        "cand": "candidate",
        "st": "state",
        "tgt": "target",
        "cond": "condition",
        "dsc": "discard",
    }
    # node_role 在编译期产出（非运行期延迟计算）
    assert isinstance(compiled.node_role, dict)
    assert len(compiled.node_role) == 5


# ---------------------------------------------------------------------------
# 7. 运行时无 json.loads / _parse_edge / _build_adjacency 调用
# ---------------------------------------------------------------------------


def test_runtime_zero_parsing():
    """运行期 engine.py 中应无 json.loads / _parse_edge / _build_adjacency 调用。

    验证编译-运行分离硬约束：所有解析在编译期完成，运行期只读预编译结构。
    通过 Grep engine.py 源代码验证匹配数为 0。
    """
    # 引擎源文件路径
    assert _ENGINE_PY.exists(), f"engine.py 不存在：{_ENGINE_PY}"
    src = _ENGINE_PY.read_text(encoding="utf-8")
    # 验证无 json.loads 直接调用（json.load 用于启动期配置加载是允许的，
    # 但 json.loads 通常用于运行期反序列化字符串，违反编译-运行分离）
    # 实际查 json.loads 与 _parse_edge / _build_adjacency 函数调用
    # 使用 word boundary 避免误匹配子字符串
    json_loads_calls = re.findall(r"\bjson\.loads\b", src)
    parse_edge_calls = re.findall(r"\b_parse_edge\b", src)
    build_adjacency_calls = re.findall(r"\b_build_adjacency\b", src)
    assert len(json_loads_calls) == 0, (
        f"engine.py 不应在运行期调用 json.loads，发现 {len(json_loads_calls)} 处"
    )
    assert len(parse_edge_calls) == 0, (
        f"engine.py 不应包含 _parse_edge 调用/定义，发现 {len(parse_edge_calls)} 处"
    )
    assert len(build_adjacency_calls) == 0, (
        f"engine.py 不应包含 _build_adjacency 调用/定义，发现 {len(build_adjacency_calls)} 处"
    )
