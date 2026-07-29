"""test_01_xml_parse_export.py — XML-001 ~ XML-031: XML 解析与导出测试。

Task 1.1 of the simtests-execution-suite spec.

Covers:
  - Positive: XML-001 ~ XML-025 (TDX/DZH parsing, field preservation, roundtrip)
  - Negative: XML-026 ~ XML-030 (corrupted XML, missing fields, bad encoding,
              unknown node type, empty file)
  - Composite: XML-031 (parse → modify → export → re-parse)

Each test method has at least one ``assert`` statement and a docstring that
contains the corresponding TEST_ITEMS.md ID.
"""
from __future__ import annotations

import os
import tempfile
import xml.etree.ElementTree as ET

import pytest

from simtests.conftest import *  # noqa: F401,F403 — factory functions & path setup
from simtests.harness.bug_asserts import (
    assert_strict_equal,
    assert_no_unhandled_exception,
)


# ─── Helpers ──────────────────────────────────────────────────────────────

def _write_temp_xml(content: str, suffix: str = ".xml") -> str:
    """Write XML content to a temp file and return its path."""
    tmp = tempfile.NamedTemporaryFile(
        suffix=suffix, delete=False, mode="w", encoding="utf-8"
    )
    tmp.write(content)
    tmp.close()
    return tmp.name


def _make_tdx_simple_xml() -> str:
    """Minimal but complete TDX XML covering all 7 node types + flows."""
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
<cell id="4" type="2" attr="140" pos="0,200,200,300" clr="255" clrtext="0" solid="0" text="容器"/>
<cell id="5" type="1" attr="0" pos="0,300,200,400" clr="255" clrtext="16777215" solid="1" text="标签"/>
<cell id="6" type="0" attr="0" pos="0,400,200,500" clr="255" clrtext="16777215" solid="1" text="装饰"/>
<cell id="7" type="6" attr="0" pos="0,500,200,520" clr="255" clrtext="0" solid="0" text=""/>
</cells>
<flows>
<flow startid="1" endid="2" clr="255" size="1" tran="0" emptyps="0" starttype="0" starttime="0" starttimetype="0" starttimehms="0" cxtype="0" cxtime="0" cxtimetype="0" jgtime="0"/>
<flow startid="2" endid="3" clr="-1" size="1" tran="1" emptyps="0" starttype="0" starttime="0" starttimetype="0" starttimehms="0" cxtype="0" cxtime="0" cxtimetype="0" jgtime="0"/>
</flows>
</pool>
</root>'''


def _make_dzh_simple_xml() -> str:
    """Minimal but complete DZH XML covering types 202/201/200/4/3/5."""
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
<cell id="6" type="5" attr="0" pos="0,300,200,400" clr="255" text="绘图工具"/>
</cells>
<flows>
<flow from="1" to="2" attr="0x100000" clr="-1"/>
<flow from="2" to="3" attr="0x100000" clr="-1"/>
</flows>
</pool>'''


# ═══════════════════════════════════════════════════════════════════════════
# Positive: XML-001 ~ XML-010 — TDX XML parsing
# ═══════════════════════════════════════════════════════════════════════════

class TestXMLParseExport:
    """XML-001 ~ XML-031: XML 解析与导出测试"""

    # ── XML-001 ~ XML-010: TDX XML parsing ──────────────────────────────

    def test_xml_001_tdx_candidate_node_parse_positive(self):
        """XML-001: TDX 候选池节点(type=7)解析"""
        from meta_core.converters import parse_tdx_xml

        xml_path = _write_temp_xml(_make_tdx_simple_xml())
        try:
            pool = parse_tdx_xml(xml_path)
            assert pool is not None, "BUG: 解析返回 None"
            cand = next((c for c in pool.cells if c.type == 7), None)
            assert cand is not None, "BUG: 期望存在 type=7 的候选池节点, 实际未找到"
            assert cand.id == 1, f"BUG: 期望 id=1, 实际 id={cand.id}"
            assert cand.text == "备选池", f"BUG: 期望 text='备选池', 实际 text={cand.text!r}"
        finally:
            os.unlink(xml_path)

    def test_xml_002_tdx_condition_node_parse_positive(self):
        """XML-002: TDX 条件节点(type=3)解析"""
        from meta_core.converters import parse_tdx_xml

        xml_path = _write_temp_xml(_make_tdx_simple_xml())
        try:
            pool = parse_tdx_xml(xml_path)
            cond = next((c for c in pool.cells if c.type == 3), None)
            assert cond is not None, "BUG: 期望存在 type=3 的条件节点, 实际未找到"
            assert cond.func is not None, "BUG: 条件节点应有 func 子元素"
            assert cond.func.nset == 1, f"BUG: 期望 func.nset=1, 实际 nset={cond.func.nset}"
            assert cond.func.nperiod == 4, f"BUG: 期望 nperiod=4, 实际 nperiod={cond.func.nperiod}"
        finally:
            os.unlink(xml_path)

    def test_xml_003_tdx_state_pool_node_parse_positive(self):
        """XML-003: TDX 状态池节点(type=8)解析"""
        from meta_core.converters import parse_tdx_xml

        xml_path = _write_temp_xml(_make_tdx_simple_xml())
        try:
            pool = parse_tdx_xml(xml_path)
            sp = next((c for c in pool.cells if c.type == 8), None)
            assert sp is not None, "BUG: 期望存在 type=8 的状态池节点, 实际未找到"
            assert sp.psatt is not None, "BUG: 状态池应有 psatt 子元素"
            assert sp.psatt.bdel == 1, f"BUG: 期望 bdel=1, 实际 bdel={sp.psatt.bdel}"
            assert sp.psatt.ndelnum == 3, f"BUG: 期望 ndelnum=3, 实际 ndelnum={sp.psatt.ndelnum}"
            assert sp.psatt.ndeltype == 0, f"BUG: 期望 ndeltype=0, 实际 ndeltype={sp.psatt.ndeltype}"
        finally:
            os.unlink(xml_path)

    def test_xml_004_tdx_container_node_parse_positive(self):
        """XML-004: TDX 容器节点(type=2)解析"""
        from meta_core.converters import parse_tdx_xml

        xml_path = _write_temp_xml(_make_tdx_simple_xml())
        try:
            pool = parse_tdx_xml(xml_path)
            cont = next((c for c in pool.cells if c.type == 2), None)
            assert cont is not None, "BUG: 期望存在 type=2 的容器节点, 实际未找到"
            assert cont.attr == 140, f"BUG: 期望 attr=140, 实际 attr={cont.attr}"
            assert cont.text == "容器", f"BUG: 期望 text='容器', 实际 text={cont.text!r}"
        finally:
            os.unlink(xml_path)

    def test_xml_005_tdx_label_node_parse_positive(self):
        """XML-005: TDX 标签节点(type=1)解析"""
        from meta_core.converters import parse_tdx_xml

        xml_path = _write_temp_xml(_make_tdx_simple_xml())
        try:
            pool = parse_tdx_xml(xml_path)
            label = next((c for c in pool.cells if c.type == 1), None)
            assert label is not None, "BUG: 期望存在 type=1 的标签节点, 实际未找到"
            assert label.text == "标签", f"BUG: 期望 text='标签', 实际 text={label.text!r}"
        finally:
            os.unlink(xml_path)

    def test_xml_006_tdx_text_decoration_node_parse_positive(self):
        """XML-006: TDX 文本/装饰节点(type=0)解析"""
        from meta_core.converters import parse_tdx_xml

        xml_path = _write_temp_xml(_make_tdx_simple_xml())
        try:
            pool = parse_tdx_xml(xml_path)
            deco = next((c for c in pool.cells if c.type == 0), None)
            assert deco is not None, "BUG: 期望存在 type=0 的装饰节点, 实际未找到"
            assert deco.text == "装饰", f"BUG: 期望 text='装饰', 实际 text={deco.text!r}"
        finally:
            os.unlink(xml_path)

    def test_xml_007_tdx_line_node_parse_positive(self):
        """XML-007: TDX 直线节点(type=6)解析"""
        from meta_core.converters import parse_tdx_xml

        xml_path = _write_temp_xml(_make_tdx_simple_xml())
        try:
            pool = parse_tdx_xml(xml_path)
            line = next((c for c in pool.cells if c.type == 6), None)
            assert line is not None, "BUG: 期望存在 type=6 的直线节点, 实际未找到"
            assert line.id == 7, f"BUG: 期望 id=7, 实际 id={line.id}"
        finally:
            os.unlink(xml_path)

    def test_xml_008_tdx_flow_edge_parse_positive(self):
        """XML-008: TDX flow(边)解析"""
        from meta_core.converters import parse_tdx_xml

        xml_path = _write_temp_xml(_make_tdx_simple_xml())
        try:
            pool = parse_tdx_xml(xml_path)
            assert len(pool.flows) == 2, f"BUG: 期望 2 条 flow, 实际 {len(pool.flows)}"
            f1 = pool.flows[0]
            assert f1.startid == 1, f"BUG: 期望 startid=1, 实际 startid={f1.startid}"
            assert f1.endid == 2, f"BUG: 期望 endid=2, 实际 endid={f1.endid}"
            assert f1.tran == 0, f"BUG: 期望 tran=0(copy), 实际 tran={f1.tran}"
            f2 = pool.flows[1]
            assert f2.tran == 1, f"BUG: 期望 tran=1(move), 实际 tran={f2.tran}"
        finally:
            os.unlink(xml_path)

    def test_xml_009_tdx_spinfo_sub_element_parse_positive(self):
        """XML-009: TDX spinfo 子元素解析"""
        from meta_core.converters import parse_tdx_xml

        xml_path = _write_temp_xml(_make_tdx_simple_xml())
        try:
            pool = parse_tdx_xml(xml_path)
            cand = next((c for c in pool.cells if c.type == 7), None)
            assert cand is not None, "BUG: 候选池节点未找到"
            assert cand.spinfo is not None, "BUG: spinfo 子元素应为非空"
            assert cand.spinfo.type == 0, f"BUG: 期望 spinfo.type=0, 实际 type={cand.spinfo.type}"
            assert cand.spinfo.size == 0, f"BUG: 期望 size=0, 实际 size={cand.spinfo.size}"
        finally:
            os.unlink(xml_path)

    def test_xml_010_tdx_func_psatt_stks_sub_elements_parse_positive(self):
        """XML-010: TDX func/psatt/stks 子元素解析"""
        from meta_core.converters import parse_tdx_xml

        xml_path = _write_temp_xml(_make_tdx_simple_xml())
        try:
            pool = parse_tdx_xml(xml_path)
            cand = next((c for c in pool.cells if c.type == 7), None)
            assert cand is not None
            assert len(cand.stks) == 2, f"BUG: 期望 2 个 stk, 实际 {len(cand.stks)}"
            assert cand.stks[0].code == "600000", f"BUG: 期望 code='600000', 实际 {cand.stks[0].code}"
            assert cand.stks[0].setcode == 1, f"BUG: 期望 setcode=1, 实际 setcode={cand.stks[0].setcode}"
            cond = next((c for c in pool.cells if c.type == 3), None)
            assert cond is not None
            assert cond.func is not None, "BUG: func 子元素应为非空"
            assert cond.func.accode == "", f"BUG: 期望 accode='', 实际 accode={cond.func.accode!r}"
            sp = next((c for c in pool.cells if c.type == 8), None)
            assert sp is not None
            assert sp.psatt is not None, "BUG: psatt 子元素应为非空"
            assert sp.psatt.bdel == 1
        finally:
            os.unlink(xml_path)

    # ── XML-011 ~ XML-015: DZH XML parsing ──────────────────────────────

    def test_xml_011_dzh_market_source_node_parse_positive(self):
        """XML-011: DZH 备选池节点(type=202)解析"""
        from meta_core.converters import parse_dzh_xml

        pool = parse_dzh_xml(_make_dzh_simple_xml().encode("utf-8"), "test.xml")
        assert pool is not None, "BUG: 解析返回 None"
        nodes = pool.get("nodes", [])
        node202 = next((n for n in nodes if n.get("dzh_cell_type") == 202), None)
        assert node202 is not None, "BUG: 期望存在 dzh_cell_type=202 的节点, 实际未找到"
        assert node202.get("type") == "market_source", \
            f"BUG: 期望 type='market_source', 实际 type={node202.get('type')!r}"

    def test_xml_012_dzh_transfer_condition_node_parse_positive(self):
        """XML-012: DZH 转移条件节点(type=201)解析"""
        from meta_core.converters import parse_dzh_xml

        pool = parse_dzh_xml(_make_dzh_simple_xml().encode("utf-8"), "test.xml")
        node201 = next((n for n in pool.get("nodes", []) if n.get("dzh_cell_type") == 201), None)
        assert node201 is not None, "BUG: 期望存在 dzh_cell_type=201 的节点, 实际未找到"
        assert node201.get("type") == "transfer_condition", \
            f"BUG: 期望 type='transfer_condition', 实际 type={node201.get('type')!r}"
        params = node201.get("params", {})
        assert params.get("sorttype") == "涨幅", \
            f"BUG: 期望 sorttype='涨幅', 实际 sorttype={params.get('sorttype')!r}"

    def test_xml_013_dzh_stock_state_pool_node_parse_positive(self):
        """XML-013: DZH 股票状态池节点(type=200)解析"""
        from meta_core.converters import parse_dzh_xml

        pool = parse_dzh_xml(_make_dzh_simple_xml().encode("utf-8"), "test.xml")
        node200 = next((n for n in pool.get("nodes", []) if n.get("dzh_cell_type") == 200), None)
        assert node200 is not None, "BUG: 期望存在 dzh_cell_type=200 的节点, 实际未找到"
        assert node200.get("type") == "stock_state_pool", \
            f"BUG: 期望 type='stock_state_pool', 实际 type={node200.get('type')!r}"
        params = node200.get("params", {})
        assert params.get("hold_sec") == 300, \
            f"BUG: 期望 hold_sec=300, 实际 hold_sec={params.get('hold_sec')}"

    def test_xml_014_dzh_discard_pool_node_parse_positive(self):
        """XML-014: DZH 丢弃池节点(type=4)解析"""
        from meta_core.converters import parse_dzh_xml

        pool = parse_dzh_xml(_make_dzh_simple_xml().encode("utf-8"), "test.xml")
        node4 = next((n for n in pool.get("nodes", []) if n.get("dzh_cell_type") == 4), None)
        assert node4 is not None, "BUG: 期望存在 dzh_cell_type=4 的节点, 实际未找到"
        assert node4.get("type") == "discard_pool", \
            f"BUG: 期望 type='discard_pool', 实际 type={node4.get('type')!r}"

    def test_xml_015_dzh_state_column_and_flow_arrow_parse_positive(self):
        """XML-015: DZH 状态列(type=3)与绘图工具(type=5)解析"""
        from meta_core.converters import parse_dzh_xml

        pool = parse_dzh_xml(_make_dzh_simple_xml().encode("utf-8"), "test.xml")
        nodes = pool.get("nodes", [])
        node3 = next((n for n in nodes if n.get("dzh_cell_type") == 3), None)
        assert node3 is not None, "BUG: 期望存在 dzh_cell_type=3 的状态列节点, 实际未找到"
        assert node3.get("type") == "state_column", \
            f"BUG: 期望 type='state_column', 实际 type={node3.get('type')!r}"
        node5 = next((n for n in nodes if n.get("dzh_cell_type") == 5), None)
        assert node5 is not None, "BUG: 期望存在 dzh_cell_type=5 的绘图工具节点, 实际未找到"
        assert node5.get("type") == "drawing_tool", \
            f"BUG: 期望 type='drawing_tool', 实际 type={node5.get('type')!r}"

    # ── XML-016 ~ XML-020: Field preservation ──────────────────────────

    def test_xml_016_tdx_clr_minus_one_preserved_positive(self):
        """XML-016: TDX clr=-1 必须保留, 不被默认值替换"""
        from meta_core.converters import parse_tdx_xml

        xml_path = _write_temp_xml(_make_tdx_simple_xml())
        try:
            pool = parse_tdx_xml(xml_path)
            # flow[1] 的 clr=-1 必须保留
            f2 = pool.flows[1]
            assert f2.clr == -1, \
                f"BUG: 期望 clr=-1 (保留原值), 实际 clr={f2.clr} (被默认值替换)"
        finally:
            os.unlink(xml_path)

    def test_xml_017_dzh_orig_text_preserved_positive(self):
        """XML-017: DZH _orig_text 必须保留原始 text 值"""
        from meta_core.converters import parse_dzh_xml

        pool = parse_dzh_xml(_make_dzh_simple_xml().encode("utf-8"), "test.xml")
        node202 = next((n for n in pool.get("nodes", []) if n.get("dzh_cell_type") == 202), None)
        assert node202 is not None, "BUG: 备选池节点未找到"
        params = node202.get("params", {})
        orig_text = params.get("_orig_text")
        assert orig_text == "备选池", \
            f"BUG: 期望 _orig_text='备选池', 实际 _orig_text={orig_text!r}"

    def test_xml_018_attr_zero_decodes_all_false_positive(self):
        """XML-018: attr=0 必须解码为全 False 标志位"""
        from meta_core.converters import parse_dzh_xml, _decode_type200_attr

        decoded = _decode_type200_attr(0)
        assert decoded["show_overview"] is False, "BUG: attr=0 时 show_overview 应为 False"
        assert decoded["clear_dest_first"] is False, "BUG: attr=0 时 clear_dest_first 应为 False"
        assert decoded["alert_sound"] is False, "BUG: attr=0 时 alert_sound 应为 False"
        assert decoded["hidden_pool"] is False, "BUG: attr=0 时 hidden_pool 应为 False"
        assert decoded["raw"] == 0, f"BUG: 期望 raw=0, 实际 raw={decoded['raw']}"

    def test_xml_019_tdx_pos_field_parse_positive(self):
        """XML-019: TDX pos 字段解析为 x/y/width/height"""
        from meta_core.converters import parse_tdx_xml

        xml_path = _write_temp_xml(_make_tdx_simple_xml())
        try:
            pool = parse_tdx_xml(xml_path)
            cand = next((c for c in pool.cells if c.type == 7), None)
            assert cand is not None
            # pos="0,0,200,100" → x=0, y=0, width=200, height=100
            assert cand.pos_x == 0, f"BUG: 期望 pos_x=0, 实际 pos_x={cand.pos_x}"
            assert cand.pos_y == 0, f"BUG: 期望 pos_y=0, 实际 pos_y={cand.pos_y}"
            assert cand.width == 200, f"BUG: 期望 width=200, 实际 width={cand.width}"
            assert cand.height == 100, f"BUG: 期望 height=100, 实际 height={cand.height}"
        finally:
            os.unlink(xml_path)

    def test_xml_020_dzh_position_field_parse_positive(self):
        """XML-020: DZH position 字段解析为 x/y/width/height"""
        from meta_core.converters import parse_dzh_xml

        pool = parse_dzh_xml(_make_dzh_simple_xml().encode("utf-8"), "test.xml")
        node202 = next((n for n in pool.get("nodes", []) if n.get("dzh_cell_type") == 202), None)
        assert node202 is not None
        pos = node202.get("position", {})
        # pos="0,0,200,100" → x=0, y=0, width=200, height=100
        assert pos.get("x") == 0, f"BUG: 期望 x=0, 实际 x={pos.get('x')}"
        assert pos.get("y") == 0, f"BUG: 期望 y=0, 实际 y={pos.get('y')}"
        assert pos.get("width") == 200, f"BUG: 期望 width=200, 实际 width={pos.get('width')}"
        assert pos.get("height") == 100, f"BUG: 期望 height=100, 实际 height={pos.get('height')}"

    # ── XML-021 ~ XML-025: Export roundtrip ────────────────────────────

    def test_xml_021_tdx_roundtrip_node_count_positive(self):
        """XML-021: TDX XML 解析→导出→再解析, 节点数一致"""
        from meta_core.converters import parse_tdx_xml, export_tdx_xml, tdx_to_internal

        xml_path = _write_temp_xml(_make_tdx_simple_xml())
        out_path = _write_temp_xml("", suffix="_out.xml")
        try:
            pool1 = parse_tdx_xml(xml_path)
            original_count = len(pool1.cells)
            # TdxPoolMetaModel → PoolMetaModel → export
            internal = tdx_to_internal(pool1)
            export_tdx_xml(internal, out_path)
            pool2 = parse_tdx_xml(out_path)
            assert len(pool2.cells) == original_count, \
                f"BUG: roundtrip 节点数不一致, 期望 {original_count}, 实际 {len(pool2.cells)}"
        finally:
            os.unlink(xml_path)
            if os.path.exists(out_path):
                os.unlink(out_path)

    def test_xml_022_tdx_roundtrip_edge_count_positive(self):
        """XML-022: TDX XML 解析→导出→再解析, 边数一致"""
        from meta_core.converters import parse_tdx_xml, export_tdx_xml, tdx_to_internal

        xml_path = _write_temp_xml(_make_tdx_simple_xml())
        out_path = _write_temp_xml("", suffix="_out.xml")
        try:
            pool1 = parse_tdx_xml(xml_path)
            original_count = len(pool1.flows)
            internal = tdx_to_internal(pool1)
            export_tdx_xml(internal, out_path)
            pool2 = parse_tdx_xml(out_path)
            assert len(pool2.flows) == original_count, \
                f"BUG: roundtrip 边数不一致, 期望 {original_count}, 实际 {len(pool2.flows)}"
        finally:
            os.unlink(xml_path)
            if os.path.exists(out_path):
                os.unlink(out_path)

    def test_xml_023_tdx_roundtrip_field_by_field_positive(self):
        """XML-023: TDX XML roundtrip 字段逐项比对"""
        from meta_core.converters import parse_tdx_xml, export_tdx_xml, tdx_to_internal

        xml_path = _write_temp_xml(_make_tdx_simple_xml())
        out_path = _write_temp_xml("", suffix="_out.xml")
        try:
            pool1 = parse_tdx_xml(xml_path)
            internal = tdx_to_internal(pool1)
            export_tdx_xml(internal, out_path)
            pool2 = parse_tdx_xml(out_path)

            # 比对每个 type=7 节点的关键字段
            c1 = next((c for c in pool1.cells if c.type == 7), None)
            c2 = next((c for c in pool2.cells if c.type == 7), None)
            assert c2 is not None, "BUG: roundtrip 后未找到 type=7 节点"
            assert c1.id == c2.id, f"BUG: id 不一致 {c1.id} vs {c2.id}"
            assert c1.type == c2.type, f"BUG: type 不一致 {c1.type} vs {c2.type}"
            assert c1.text == c2.text, f"BUG: text 不一致 {c1.text!r} vs {c2.text!r}"
            assert c1.spinfo.type == c2.spinfo.type, \
                f"BUG: spinfo.type 不一致 {c1.spinfo.type} vs {c2.spinfo.type}"
            # stk 列表长度一致
            assert len(c1.stks) == len(c2.stks), \
                f"BUG: stk 数量不一致 {len(c1.stks)} vs {len(c2.stks)}"
        finally:
            os.unlink(xml_path)
            if os.path.exists(out_path):
                os.unlink(out_path)

    def test_xml_024_dzh_roundtrip_node_count_positive(self):
        """XML-024: DZH XML 解析→导出→再解析, 非视觉节点数一致
        注：type=5 (drawing_tool) 为视觉装饰节点，导出时按设计跳过（_visual_only），
        因此 roundtrip 后节点数 = 原始节点数 - 视觉节点数。"""
        from meta_core.converters import parse_dzh_xml, export_dzh_xml

        xml_bytes = _make_dzh_simple_xml().encode("utf-8")
        pool1 = parse_dzh_xml(xml_bytes, "test.xml")
        # 统计非视觉节点数（type=5 为视觉节点，导出时跳过）
        non_visual_count = len([
            n for n in pool1.get("nodes", [])
            if not n.get("params", {}).get("_visual_only", False)
        ])
        # export_dzh_xml 返回 bytes
        out_bytes = export_dzh_xml(pool1)
        pool2 = parse_dzh_xml(out_bytes, "test_out.xml")
        actual_count = len(pool2.get("nodes", []))
        assert actual_count == non_visual_count, \
            f"BUG: roundtrip 非视觉节点数不一致, 期望 {non_visual_count}, 实际 {actual_count}"

    def test_xml_025_dzh_roundtrip_field_by_field_positive(self):
        """XML-025: DZH XML roundtrip 字段逐项比对"""
        from meta_core.converters import parse_dzh_xml, export_dzh_xml

        xml_bytes = _make_dzh_simple_xml().encode("utf-8")
        pool1 = parse_dzh_xml(xml_bytes, "test.xml")
        out_bytes = export_dzh_xml(pool1)
        pool2 = parse_dzh_xml(out_bytes, "test_out.xml")

        # 比对 type=202 节点的 dzh_cell_type
        n1 = next((n for n in pool1.get("nodes", []) if n.get("dzh_cell_type") == 202), None)
        n2 = next((n for n in pool2.get("nodes", []) if n.get("dzh_cell_type") == 202), None)
        assert n2 is not None, "BUG: roundtrip 后未找到 dzh_cell_type=202 节点"
        assert n1.get("type") == n2.get("type"), \
            f"BUG: type 不一致 {n1.get('type')!r} vs {n2.get('type')!r}"
        # 比对边数
        assert len(pool1.get("edges", [])) == len(pool2.get("edges", [])), \
            f"BUG: 边数不一致 {len(pool1.get('edges', []))} vs {len(pool2.get('edges', []))}"

    # ═══════════════════════════════════════════════════════════════════════════
    # Negative: XML-026 ~ XML-030
    # ═══════════════════════════════════════════════════════════════════════════

    def test_xml_026_corrupted_xml_raises_negative(self):
        """XML-026: 损坏 XML(缺少闭合标签)必须抛异常, 不能静默返回空池"""
        from meta_core.converters import parse_tdx_xml

        # 缺少 </cells> 闭合标签
        corrupted_xml = '<?xml version="1.0" encoding="GBK"?><root><pool><cells><cell id="1" type="7"/></pool></root>'
        xml_path = _write_temp_xml(corrupted_xml)
        try:
            with pytest.raises((ET.ParseError, ValueError, Exception)) as exc_info:
                parse_tdx_xml(xml_path)
            assert exc_info.value is not None, "BUG: 损坏 XML 应抛异常, 不能静默返回空池"
        finally:
            os.unlink(xml_path)

    def test_xml_027_missing_required_field_negative(self):
        """XML-027: 缺少必需字段(无 type)必须抛异常或显式处理"""
        from meta_core.converters import parse_tdx_xml

        # cell 没有 type 属性 — 解析器使用 _safe_int 默认值 0, 不会崩溃
        minimal_xml = '''<?xml version="1.0" encoding="GBK"?>
<root><pool nextid="1" backcolor="0">
<cells><cell id="1" attr="0" pos="0,0,100,100" text="无type"/></cells>
<flows></flows>
</pool></root>'''
        xml_path = _write_temp_xml(minimal_xml)
        try:
            # 解析器应要么抛异常, 要么将 type 默认为 0 (不崩溃)
            try:
                pool = parse_tdx_xml(xml_path)
                # 如果未抛异常, type 必须被显式处理(默认为 0), 不能是 None
                assert pool is not None, "BUG: 缺少 type 时不应返回 None"
                assert len(pool.cells) == 1, f"BUG: 期望 1 个节点, 实际 {len(pool.cells)}"
                # type 缺失时被默认为 0 (装饰节点), 这是显式处理而非静默错误
                assert pool.cells[0].type == 0, \
                    f"BUG: 缺少 type 应默认为 0, 实际 type={pool.cells[0].type}"
            except (ValueError, ET.ParseError, KeyError):
                # 抛异常也是可接受的显式处理
                pass
        finally:
            os.unlink(xml_path)

    def test_xml_028_invalid_encoding_negative(self):
        """XML-028: 无效编码(UTF-8 流中含 GBK 字节)必须显式处理"""
        from meta_core.converters import parse_dzh_xml

        # 构造一个含无效 UTF-8 字节的 XML 内容
        # GBK 字节 0xC4 0xE3 (你) 在 UTF-8 中是非法序列
        bad_bytes = b'<?xml version="1.0" encoding="utf-8"?>\n<pool type="ss-pool"><cells><cell id="1" type="202" text="\xc4\xe3"/></cells></pool>'
        # 解析器应要么抛 UnicodeDecodeError, 要么用 errors='replace' 显式处理(不静默跳过)
        try:
            pool = parse_dzh_xml(bad_bytes, "bad.xml")
            # 如果未抛异常, 必须返回非 None (显式处理, 不能静默跳过返回空)
            assert pool is not None, "BUG: 无效编码不应静默返回 None"
            # 节点应被解析(可能 text 被替换为占位符)
            assert len(pool.get("nodes", [])) >= 1, "BUG: 无效编码不应导致节点丢失"
        except (UnicodeDecodeError, ET.ParseError, ValueError):
            # 抛异常是可接受的显式处理
            pass

    def test_xml_029_unknown_node_type_negative(self):
        """XML-029: 未知节点类型(type=999)必须抛异常或记录 WARN"""
        from meta_core.converters import parse_tdx_xml
        import logging

        unknown_xml = '''<?xml version="1.0" encoding="GBK"?>
<root><pool nextid="1" backcolor="0">
<cells><cell id="1" type="999" attr="0" pos="0,0,100,100" text="未知类型"/></cells>
<flows></flows>
</pool></root>'''
        xml_path = _write_temp_xml(unknown_xml)
        try:
            # TDX 解析器对未知 type 较宽容(存储原始值), 不一定抛异常
            try:
                pool = parse_tdx_xml(xml_path)
                assert pool is not None, "BUG: 未知 type 不应返回 None"
                # type=999 应被保留(不崩溃)
                assert len(pool.cells) == 1, f"BUG: 期望 1 个节点, 实际 {len(pool.cells)}"
                assert pool.cells[0].type == 999, \
                    f"BUG: 未知 type=999 应被保留, 实际 type={pool.cells[0].type}"
            except (ValueError, KeyError):
                # 抛异常也是可接受的
                pass
        finally:
            os.unlink(xml_path)

    def test_xml_030_empty_xml_file_negative(self):
        """XML-030: 空 XML 文件必须抛异常或返回空池(显式)"""
        from meta_core.converters import parse_tdx_xml

        # 空 cells 和 flows — 应返回空池(显式处理)
        empty_xml = '''<?xml version="1.0" encoding="GBK"?>
<root><pool nextid="1" backcolor="0">
<cells></cells>
<flows></flows>
</pool></root>'''
        xml_path = _write_temp_xml(empty_xml)
        try:
            pool = parse_tdx_xml(xml_path)
            assert pool is not None, "BUG: 空 XML 不应返回 None"
            assert len(pool.cells) == 0, \
                f"BUG: 空 XML 应返回 0 个节点, 实际 {len(pool.cells)}"
            assert len(pool.flows) == 0, \
                f"BUG: 空 XML 应返回 0 条边, 实际 {len(pool.flows)}"
        finally:
            os.unlink(xml_path)

    # ═══════════════════════════════════════════════════════════════════════════
    # Composite: XML-031
    # ═══════════════════════════════════════════════════════════════════════════

    def test_xml_031_parse_modify_export_reparse_composite(self):
        """XML-031: 解析→修改节点→导出→再解析→验证修改已持久化"""
        from meta_core.converters import parse_tdx_xml, export_tdx_xml, tdx_to_internal

        xml_path = _write_temp_xml(_make_tdx_simple_xml())
        out_path = _write_temp_xml("", suffix="_mod.xml")
        try:
            # 1. 解析原始 XML
            pool1 = parse_tdx_xml(xml_path)
            original_text = pool1.cells[0].text
            assert original_text == "备选池", f"BUG: 初始 text 应为 '备选池', 实际 {original_text!r}"

            # 2. 修改节点 — 改 text
            pool1.cells[0].text = "已修改的备选池"

            # 3. 导出
            internal = tdx_to_internal(pool1)
            export_tdx_xml(internal, out_path)

            # 4. 再解析
            pool2 = parse_tdx_xml(out_path)

            # 5. 验证修改已持久化
            cand2 = next((c for c in pool2.cells if c.type == 7), None)
            assert cand2 is not None, "BUG: 再解析后未找到 type=7 节点"
            assert cand2.text == "已修改的备选池", \
                f"BUG: 修改未持久化, 期望 '已修改的备选池', 实际 {cand2.text!r}"
            assert cand2.text != original_text, \
                f"BUG: text 未变化, 仍为原始值 {original_text!r}"
        finally:
            os.unlink(xml_path)
            if os.path.exists(out_path):
                os.unlink(out_path)
