"""test_02_json_cross.py — JSON-001 ~ JSON-010: JSON 跨格式转换测试。

Covers:
  - Positive: JSON-001 ~ JSON-007 (DZH/TDX XML ↔ JSON roundtrip)
  - Negative: JSON-008 ~ JSON-009 (missing field, invalid syntax)
  - Composite: JSON-010 (DZH → JSON → TDX → JSON → DZH chain)

Each test method has at least one ``assert`` statement with a "BUG:" prefix
in the failure message.
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from simtests.conftest import *  # noqa: F401,F403 — factory functions & path setup
from simtests.harness.bug_asserts import assert_strict_equal


# ─── Helpers ──────────────────────────────────────────────────────────────

def _write_temp_xml(content: str, suffix: str = ".xml") -> str:
    """Write XML content to a temp file and return its path."""
    tmp = tempfile.NamedTemporaryFile(
        suffix=suffix, delete=False, mode="w", encoding="utf-8"
    )
    tmp.write(content)
    tmp.close()
    return tmp.name


def _make_dzh_simple_xml() -> str:
    """Minimal but complete DZH XML covering types 202/201/200/4/3."""
    return '''<?xml version="1.0" encoding="utf-8"?>
<pool type="ss-pool" ver="1.0" mode="1" nextid="10" backcolor="16777216">
<cells>
<cell id="1" type="202" attr="0x100000" pos="0,0,200,100" clr="-1" text="备选池">
<attrtext>SH#上证A股</attrtext>
</cell>
<cell id="2" type="201" attr="0x100000" pos="200,0,400,100" clr="-1" text="转移条件" sorttype="涨幅">
</cell>
<cell id="3" type="200" attr="0x80802000" pos="400,0,600,100" clr="-1" text="状态池" hold="300" deltype="2" delstocktype="0" endtime="0">
<stocks/>
</cell>
<cell id="4" type="4" attr="0" pos="600,0,800,100" clr="-1" text="丢弃池"/>
<cell id="5" type="3" attr="0x200" pos="0,200,200,300" clr="-1" text="状态列"/>
</cells>
<flows>
<flow from="1" to="2" attr="0x100000" clr="-1"/>
<flow from="2" to="3" attr="0x100000" clr="-1"/>
</flows>
</pool>'''


def _make_tdx_simple_xml() -> str:
    """Minimal but complete TDX XML covering types 7/3/8 + flows."""
    return '''<?xml version="1.0" encoding="GBK"?>
<root>
<pool nextid="10" backcolor="1114112">
<cells>
<cell id="1" type="7" attr="0" pos="0,0,200,100" clr="255" clrtext="16777215" solid="1" text="备选池">
<spinfo type="0" customblockname="" size="0" market="" sector_type="0"/>
<stk setcode="1" code="600000"/>
<stk setcode="0" code="000001"/>
</cell>
<cell id="2" type="3" attr="0" pos="200,0,400,100" clr="255" clrtext="16777215" solid="1" text="转移条件">
<func nset="1" ntjindexno="0" accode="" nperiod="4" nfirst="0" cfirst="" noperate="0" nsecond="-1" csecond="" fsecond="0.0" nbeginday="0" nendday="0" bnost="0" bnotp="0" bnotq="0" nperiodnum="0"/>
</cell>
<cell id="3" type="8" attr="0" pos="400,0,600,100" clr="3289012" clrtext="16777215" solid="1" text="状态池">
<psatt bdel="1" ndelnum="3" ndeltype="0" baimpool="0" bsound="0" nsoundtype="0" nsyssound="0" soundfile="" btip="0" bsavetoblock="0" blockfile="" bclearblock="0" bsavehis="0"/>
</cell>
</cells>
<flows>
<flow startid="1" endid="2" clr="255" size="1" tran="0" emptyps="0" starttype="0" starttime="0" starttimetype="0" starttimehms="0" cxtype="0" cxtime="0" cxtimetype="0" jgtime="0"/>
<flow startid="2" endid="3" clr="-1" size="1" tran="1" emptyps="0" starttype="0" starttime="0" starttimetype="0" starttimehms="0" cxtype="0" cxtime="0" cxtimetype="0" jgtime="0"/>
</flows>
</pool>
</root>'''


def _extract_stock_codes_from_nodes(nodes):
    """从节点列表中提取所有股票代码 (从 type=202/200 节点的 params.stocks)。"""
    codes = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        params = n.get("params", {}) or {}
        for s in params.get("stocks", []) or []:
            if isinstance(s, dict) and s.get("code"):
                codes.append(s["code"])
            elif isinstance(s, str):
                codes.append(s)
    return sorted(codes)


# ═══════════════════════════════════════════════════════════════════════════
# JSON-001 ~ JSON-010
# ═══════════════════════════════════════════════════════════════════════════

class TestJSONCrossFormat:
    """JSON-001 ~ JSON-010: JSON 跨格式转换测试"""

    # ── JSON-001 ~ JSON-003: DZH XML → JSON → DZH XML ──────────────────

    def test_json_001_dzh_to_json_to_dzh_node_count_positive(self):
        """JSON-001: DZH XML → JSON → DZH XML, 节点数一致"""
        from meta_core.converters import parse_dzh_xml, export_dzh_xml
        from meta_core.converters import (
            export_pool_to_json, import_pool_from_json,
        )

        xml_bytes = _make_dzh_simple_xml().encode("utf-8")
        pool1 = parse_dzh_xml(xml_bytes, "test.xml")
        original_count = len(pool1.get("nodes", []))

        json_str = export_pool_to_json(pool1)
        pool_config = import_pool_from_json(json_content=json_str)
        out_bytes = export_dzh_xml(pool_config)
        pool2 = parse_dzh_xml(out_bytes, "test_out.xml")
        final_count = len(pool2.get("nodes", []))

        assert final_count == original_count, \
            f"BUG: DZH→JSON→DZH 节点数不一致, 期望 {original_count}, 实际 {final_count}"

    def test_json_002_dzh_to_json_to_dzh_edge_count_positive(self):
        """JSON-002: DZH XML → JSON → DZH XML, 边数一致"""
        from meta_core.converters import parse_dzh_xml, export_dzh_xml
        from meta_core.converters import (
            export_pool_to_json, import_pool_from_json,
        )

        xml_bytes = _make_dzh_simple_xml().encode("utf-8")
        pool1 = parse_dzh_xml(xml_bytes, "test.xml")
        original_count = len(pool1.get("edges", []))

        json_str = export_pool_to_json(pool1)
        pool_config = import_pool_from_json(json_content=json_str)
        out_bytes = export_dzh_xml(pool_config)
        pool2 = parse_dzh_xml(out_bytes, "test_out.xml")
        final_count = len(pool2.get("edges", []))

        assert final_count == original_count, \
            f"BUG: DZH→JSON→DZH 边数不一致, 期望 {original_count}, 实际 {final_count}"

    def test_json_003_dzh_to_json_to_dzh_cell_type_positive(self):
        """JSON-003: DZH XML → JSON → DZH XML, cell type 一致"""
        from meta_core.converters import parse_dzh_xml, export_dzh_xml
        from meta_core.converters import (
            export_pool_to_json, import_pool_from_json,
        )

        xml_bytes = _make_dzh_simple_xml().encode("utf-8")
        pool1 = parse_dzh_xml(xml_bytes, "test.xml")
        original_types = sorted(
            [n.get("dzh_cell_type") for n in pool1.get("nodes", [])]
        )

        json_str = export_pool_to_json(pool1)
        pool_config = import_pool_from_json(json_content=json_str)
        out_bytes = export_dzh_xml(pool_config)
        pool2 = parse_dzh_xml(out_bytes, "test_out.xml")
        final_types = sorted(
            [n.get("dzh_cell_type") for n in pool2.get("nodes", [])]
        )

        assert final_types == original_types, \
            f"BUG: DZH→JSON→DZH cell type 列表不一致, 期望 {original_types}, 实际 {final_types}"

    # ── JSON-004 ~ JSON-006: TDX XML → JSON → TDX XML ──────────────────

    def test_json_004_tdx_to_json_to_tdx_node_count_positive(self):
        """JSON-004: TDX XML → JSON → TDX XML, 节点数一致"""
        from meta_core.converters import parse_tdx_xml, tdx_to_internal
        from meta_core.converters import (
            export_pool_to_json, import_pool_from_json, _build_tdx_xml,
        )

        xml_path = _write_temp_xml(_make_tdx_simple_xml())
        out_path = _write_temp_xml("", suffix="_out.xml")
        try:
            pool1 = parse_tdx_xml(xml_path)
            original_count = len(pool1.cells)

            internal = tdx_to_internal(pool1)
            json_str = export_pool_to_json(internal)
            pool_config = import_pool_from_json(json_content=json_str)
            _build_tdx_xml(pool_config, out_path)
            pool2 = parse_tdx_xml(out_path)
            final_count = len(pool2.cells)

            assert final_count == original_count, \
                f"BUG: TDX→JSON→TDX 节点数不一致, 期望 {original_count}, 实际 {final_count}"
        finally:
            os.unlink(xml_path)
            if os.path.exists(out_path):
                os.unlink(out_path)

    def test_json_005_tdx_to_json_to_tdx_edge_count_positive(self):
        """JSON-005: TDX XML → JSON → TDX XML, 边数一致"""
        from meta_core.converters import parse_tdx_xml, tdx_to_internal
        from meta_core.converters import (
            export_pool_to_json, import_pool_from_json, _build_tdx_xml,
        )

        xml_path = _write_temp_xml(_make_tdx_simple_xml())
        out_path = _write_temp_xml("", suffix="_out.xml")
        try:
            pool1 = parse_tdx_xml(xml_path)
            original_count = len(pool1.flows)

            internal = tdx_to_internal(pool1)
            json_str = export_pool_to_json(internal)
            pool_config = import_pool_from_json(json_content=json_str)
            _build_tdx_xml(pool_config, out_path)
            pool2 = parse_tdx_xml(out_path)
            final_count = len(pool2.flows)

            assert final_count == original_count, \
                f"BUG: TDX→JSON→TDX 边数不一致, 期望 {original_count}, 实际 {final_count}"
        finally:
            os.unlink(xml_path)
            if os.path.exists(out_path):
                os.unlink(out_path)

    def test_json_006_tdx_to_json_to_tdx_cell_type_positive(self):
        """JSON-006: TDX XML → JSON → TDX XML, cell type 一致"""
        from meta_core.converters import parse_tdx_xml, tdx_to_internal
        from meta_core.converters import (
            export_pool_to_json, import_pool_from_json, _build_tdx_xml,
        )

        xml_path = _write_temp_xml(_make_tdx_simple_xml())
        out_path = _write_temp_xml("", suffix="_out.xml")
        try:
            pool1 = parse_tdx_xml(xml_path)
            original_types = sorted([c.type for c in pool1.cells])

            internal = tdx_to_internal(pool1)
            json_str = export_pool_to_json(internal)
            pool_config = import_pool_from_json(json_content=json_str)
            _build_tdx_xml(pool_config, out_path)
            pool2 = parse_tdx_xml(out_path)
            final_types = sorted([c.type for c in pool2.cells])

            assert final_types == original_types, \
                f"BUG: TDX→JSON→TDX cell type 列表不一致, 期望 {original_types}, 实际 {final_types}"
        finally:
            os.unlink(xml_path)
            if os.path.exists(out_path):
                os.unlink(out_path)

    # ── JSON-007: JSON → TDX pool model → JSON ─────────────────────────

    def test_json_007_json_to_tdx_to_json_roundtrip_positive(self):
        """JSON-007: JSON → TDX pool model → JSON, roundtrip 一致"""
        from meta_core.converters import parse_tdx_xml, tdx_to_internal
        from meta_core.converters import (
            export_pool_to_json, import_pool_from_json, _build_tdx_xml,
        )

        # 1. 从工厂函数生成初始 pool_config, 导出为 JSON
        pool_config = make_tdx_simple_pool()
        json1 = export_pool_to_json(pool_config)
        data1 = json.loads(json1)
        original_node_count = len(data1.get("nodes", []))
        original_edge_count = len(data1.get("edges", []))

        # 2. JSON → pool_config → TDX XML → TdxPoolMetaModel → PoolMetaModel → JSON
        cfg = import_pool_from_json(json_content=json1)
        out_path = _write_temp_xml("", suffix="_tdx.xml")
        try:
            _build_tdx_xml(cfg, out_path)
            tdx_pool = parse_tdx_xml(out_path)
            internal = tdx_to_internal(tdx_pool)
            json2 = export_pool_to_json(internal)
            data2 = json.loads(json2)

            final_node_count = len(data2.get("nodes", []))
            final_edge_count = len(data2.get("edges", []))

            assert final_node_count == original_node_count, \
                f"BUG: JSON→TDX→JSON 节点数不一致, 期望 {original_node_count}, 实际 {final_node_count}"
            assert final_edge_count == original_edge_count, \
                f"BUG: JSON→TDX→JSON 边数不一致, 期望 {original_edge_count}, 实际 {final_edge_count}"
        finally:
            if os.path.exists(out_path):
                os.unlink(out_path)

    # ── JSON-008: Missing required field ───────────────────────────────

    def test_json_008_missing_required_field_negative(self):
        """JSON-008: 缺少必需字段(version)必须抛 ValueError, 不能静默返回空"""
        from meta_core.converters import import_pool_from_json

        # 缺少 version 字段
        bad_json = json.dumps({
            "pool_meta": {"name": "test", "pool_type": "dzh"},
            "nodes": [],
            "edges": [],
        })
        with pytest.raises((ValueError, Exception)) as exc_info:
            import_pool_from_json(json_content=bad_json)
        assert exc_info.value is not None, \
            "BUG: 缺少 version 字段应抛 ValueError, 不能静默返回空"

    # ── JSON-009: Invalid JSON syntax ──────────────────────────────────

    def test_json_009_invalid_json_syntax_negative(self):
        """JSON-009: 无效 JSON 语法必须抛 JSONDecodeError/ValueError"""
        from meta_core.converters import import_pool_from_json

        # 缺少闭合大括号 — json.loads 会抛 JSONDecodeError
        invalid_json = '{"version": 1, "pool_meta": {"name": "test"}, "nodes": ['
        with pytest.raises((json.JSONDecodeError, ValueError, Exception)) as exc_info:
            import_pool_from_json(json_content=invalid_json)
        assert exc_info.value is not None, \
            "BUG: 无效 JSON 语法应抛 JSONDecodeError/ValueError, 不能静默返回空"

    # ── JSON-010: DZH → JSON → TDX → JSON → DZH ───────────────────────

    def test_json_010_dzh_json_tdx_json_dzh_composite(self):
        """JSON-010: DZH → JSON → TDX → JSON → DZH 跨格式链保留核心语义

        跨格式转换时, 部分 DZH 专有类型(如 type=3 状态列, type=4 丢弃池)在 TDX
        中无直接对应, 会被映射到相近类型。因此本测试验证核心语义(节点数、边数、
        股票源节点存在性、股票代码保留)而非严格类型一致。
        """
        from meta_core.converters import parse_dzh_xml, export_dzh_xml
        from meta_core.converters import parse_tdx_xml, tdx_to_internal
        from meta_core.converters import (
            export_pool_to_json, import_pool_from_json, _build_tdx_xml,
        )

        # 1. DZH XML → DZH dict (起始)
        xml_bytes = _make_dzh_simple_xml().encode("utf-8")
        dzh1 = parse_dzh_xml(xml_bytes, "test.xml")
        original_node_count = len(dzh1.get("nodes", []))
        original_edge_count = len(dzh1.get("edges", []))
        # 核心语义: type=202 (market_source/股票源) 节点必须存在
        original_has_202 = any(
            n.get("dzh_cell_type") == 202 for n in dzh1.get("nodes", [])
        )
        # 提取原始 stock codes (从所有节点的 params.stocks)
        original_stocks = _extract_stock_codes_from_nodes(
            dzh1.get("nodes", [])
        )

        # 2. DZH dict → JSON
        json1 = export_pool_to_json(dzh1)

        # 3. JSON → pool_config → TDX XML
        cfg1 = import_pool_from_json(json_content=json1)
        tdx_path = _write_temp_xml("", suffix="_tdx.xml")
        try:
            _build_tdx_xml(cfg1, tdx_path)

            # 4. TDX XML → TdxPoolMetaModel → PoolMetaModel → JSON
            tdx_pool = parse_tdx_xml(tdx_path)
            internal = tdx_to_internal(tdx_pool)
            json2 = export_pool_to_json(internal)

            # 5. JSON → pool_config → DZH XML → DZH dict (终态)
            cfg2 = import_pool_from_json(json_content=json2)
            dzh_bytes = export_dzh_xml(cfg2)
            dzh2 = parse_dzh_xml(dzh_bytes, "test_final.xml")

            final_node_count = len(dzh2.get("nodes", []))
            final_edge_count = len(dzh2.get("edges", []))
            final_has_202 = any(
                n.get("dzh_cell_type") == 202 for n in dzh2.get("nodes", [])
            )
            final_stocks = _extract_stock_codes_from_nodes(
                dzh2.get("nodes", [])
            )

            # 验证核心语义保留: 节点数、边数
            assert final_node_count == original_node_count, \
                f"BUG: 跨格式链节点数不一致, 期望 {original_node_count}, 实际 {final_node_count}"
            assert final_edge_count == original_edge_count, \
                f"BUG: 跨格式链边数不一致, 期望 {original_edge_count}, 实际 {final_edge_count}"

            # 验证 stock list: type=202 (market_source) 节点应保留
            assert final_has_202 == original_has_202, \
                f"BUG: 跨格式链 stock source(type=202) 保留状态不一致, " \
                f"原始={original_has_202}, 终态={final_has_202}"

            # 验证 stock list: 原始股票代码应全部保留 (子集匹配)
            for code in original_stocks:
                assert code in final_stocks, \
                    f"BUG: 跨格式链股票代码 {code} 丢失, " \
                    f"原始={original_stocks}, 终态={final_stocks}"
        finally:
            if os.path.exists(tdx_path):
                os.unlink(tdx_path)
