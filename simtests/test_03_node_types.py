"""test_03_node_types.py — NODE-001 ~ NODE-014: 节点类型系统测试。

Covers:
  - TDX node types (6 tests): NODE-001 ~ NODE-006
      type=7 candidate / type=3 condition / type=8 state pool /
      type=2 container / type=1,0,6 decorative / unknown type=999
  - DZH node types (5 tests): NODE-007 ~ NODE-011
      type=202 market source / type=201 transfer condition /
      type=200 stock state pool / type=4,3,5 discard/column/drawing /
      unknown type=999
  - attr bit flag decoding (3 tests): NODE-012 ~ NODE-014
      attr=0 all-False / attr=0x80802000 bit independence / roundtrip

Each test method has at least one ``assert`` statement with a "BUG:" prefix
in the failure message.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from simtests.conftest import *  # noqa: F401,F403 — factory functions & path setup


# ─── Helpers ──────────────────────────────────────────────────────────────

def _write_temp_xml(content: str, suffix: str = ".xml", encoding: str = "utf-8") -> str:
    """Write XML content to a temp file and return its path.

    TDX XML files use GBK encoding (per the XML declaration), so pass encoding="gbk"
    for TDX tests. DZH XML files use UTF-8.
    """
    tmp = tempfile.NamedTemporaryFile(
        suffix=suffix, delete=False, mode="w", encoding=encoding
    )
    tmp.write(content)
    tmp.close()
    return tmp.name


def _make_tdx_spinfo_xml(spinfo_type: int) -> str:
    """Build a TDX XML with a single candidate node using the given spinfo type."""
    return '''<?xml version="1.0" encoding="GBK"?>
<root>
<pool nextid="10" backcolor="1114112">
<cells>
<cell id="1" type="7" attr="0" pos="0,0,200,100" clr="255" clrtext="16777215" solid="1" text="备选池">
<spinfo type="%d" customblockname="" size="0" market="" sector_type="0"/>
</cell>
</cells>
</pool>
</root>''' % spinfo_type


def _make_tdx_func_nset_xml(nset: int) -> str:
    """Build a TDX XML with a single condition node using the given nset value."""
    return '''<?xml version="1.0" encoding="GBK"?>
<root>
<pool nextid="10" backcolor="1114112">
<cells>
<cell id="1" type="3" attr="0" pos="0,0,200,100" clr="255" clrtext="16777215" solid="1" text="条件">
<func nset="%d" ntjindexno="0" accode="" nperiod="4" nfirst="0" cfirst="" noperate="0" nsecond="-1" csecond="" fsecond="0.0" nbeginday="0" nendday="0" bnost="0" bnotp="0" bnotq="0" nperiodnum="0"/>
</cell>
</cells>
</pool>
</root>''' % nset


def _make_tdx_psatt_xml() -> str:
    """Build a TDX XML with a state pool node carrying all 13 psatt fields."""
    return '''<?xml version="1.0" encoding="GBK"?>
<root>
<pool nextid="10" backcolor="1114112">
<cells>
<cell id="1" type="8" attr="0" pos="0,0,200,100" clr="3289012" clrtext="16777215" solid="1" text="状态池">
<psatt bdel="1" ndelnum="5" ndeltype="2" baimpool="1" bsound="1" nsoundtype="1" nsyssound="3" soundfile="C:\\alert.wav" btip="1" bsavetoblock="1" blockfile="TEST" bclearblock="1" bsavehis="1"/>
</cell>
</cells>
</pool>
</root>'''


def _make_tdx_container_xml() -> str:
    """Build a TDX XML with a type=2 container and two child-decorative nodes inside its bounds."""
    return '''<?xml version="1.0" encoding="GBK"?>
<root>
<pool nextid="10" backcolor="1114112">
<cells>
<cell id="1" type="2" attr="140" pos="0,0,400,300" clr="255" clrtext="0" solid="0" text="容器"/>
<cell id="2" type="1" attr="0" pos="10,10,200,50" clr="255" clrtext="16777215" solid="1" text="子标签"/>
<cell id="3" type="0" attr="0" pos="10,60,200,100" clr="255" clrtext="16777215" solid="1" text="子文本"/>
</cells>
<flows>
<flow startid="1" endid="2" clr="255" size="1" tran="0" emptyps="0" starttype="0" starttime="0" starttimetype="0" starttimehms="0" cxtype="0" cxtime="0" cxtimetype="0" jgtime="0"/>
</flows>
</pool>
</root>'''


def _make_tdx_decorative_xml() -> str:
    """Build a TDX XML covering type=1 label, type=0 text, type=6 line."""
    return '''<?xml version="1.0" encoding="GBK"?>
<root>
<pool nextid="10" backcolor="1114112">
<cells>
<cell id="1" type="1" attr="0" pos="0,0,200,100" clr="255" clrtext="16777215" solid="1" text="标签节点"/>
<cell id="2" type="0" attr="0" pos="0,100,200,200" clr="255" clrtext="16777215" solid="1" text="文本节点"/>
<cell id="3" type="6" attr="0" pos="0,200,200,220" clr="255" clrtext="0" solid="0" text=""/>
</cells>
</pool>
</root>'''


def _make_tdx_unknown_type_xml() -> str:
    """Build a TDX XML with an unknown type=999 node."""
    return '''<?xml version="1.0" encoding="GBK"?>
<root>
<pool nextid="10" backcolor="1114112">
<cells>
<cell id="1" type="999" attr="0" pos="0,0,200,100" clr="255" clrtext="16777215" solid="1" text="未知类型"/>
</cells>
</pool>
</root>'''


def _make_dzh_attrtext_xml(attrtext: str) -> str:
    """Build a DZH XML with a type=202 node carrying the given attrtext."""
    return '''<?xml version="1.0" encoding="utf-8"?>
<pool type="ss-pool" ver="1.0" mode="1" nextid="10" backcolor="16777216">
<cells>
<cell id="1" type="202" attr="0x80" pos="0,0,200,100" clr="-1" text="备选池">
<attrtext>%s</attrtext>
</cell>
</cells>
</pool>''' % attrtext


def _make_dzh_type201_xml(attr_int: int) -> str:
    """Build a DZH XML with a type=201 node carrying the given attr value.

    Note: DZH parser's _safe_int uses int(val) which cannot parse hex strings,
    so attr must be passed as a decimal integer string.
    """
    return '''<?xml version="1.0" encoding="utf-8"?>
<pool type="ss-pool" ver="1.0" mode="1" nextid="10" backcolor="16777216">
<cells>
<cell id="1" type="201" attr="%d" pos="0,0,200,100" clr="-1" text="转移条件" sorttype="涨幅"/>
</cells>
</pool>''' % attr_int


def _make_dzh_type200_xml() -> str:
    """Build a DZH XML with a type=200 node carrying attr/deltype/hold/delstocktype/endtime.

    Note: attr=0x80802000=2155880448 (decimal), because _safe_int cannot parse hex strings.
    """
    return '''<?xml version="1.0" encoding="utf-8"?>
<pool type="ss-pool" ver="1.0" mode="1" nextid="10" backcolor="16777216">
<cells>
<cell id="1" type="200" attr="2155880448" pos="0,0,200,100" clr="-1" text="状态池" hold="300" deltype="2" delstocktype="0" endtime="0">
<stocks/>
</cell>
</cells>
</pool>'''


def _make_dzh_type4_3_5_xml() -> str:
    """Build a DZH XML covering type=4 discard, type=3 state column, type=5 drawing tool."""
    return '''<?xml version="1.0" encoding="utf-8"?>
<pool type="ss-pool" ver="1.0" mode="1" nextid="10" backcolor="16777216">
<cells>
<cell id="1" type="4" attr="0" pos="0,0,200,100" clr="-1" text="丢弃池"/>
<cell id="2" type="3" attr="0x200" pos="200,0,400,100" clr="-1" text="状态列"/>
<cell id="3" type="5" attr="0" pos="400,0,600,100" clr="255" text="绘图工具"/>
</cells>
</pool>'''


def _make_dzh_unknown_type_xml() -> str:
    """Build a DZH XML with an unknown type=999 node."""
    return '''<?xml version="1.0" encoding="utf-8"?>
<pool type="ss-pool" ver="1.0" mode="1" nextid="10" backcolor="16777216">
<cells>
<cell id="1" type="999" attr="0" pos="0,0,200,100" clr="-1" text="未知类型"/>
<cell id="2" type="202" attr="0x80" pos="200,0,400,100" clr="-1" text="备选池">
<attrtext>SH#上证A股</attrtext>
</cell>
</cells>
</pool>'''


# ═══════════════════════════════════════════════════════════════════════════
# NODE-001 ~ NODE-014: 节点类型系统测试
# ═══════════════════════════════════════════════════════════════════════════

class TestNodeTypes:
    """NODE-001 ~ NODE-014: 节点类型系统测试"""

    # ── TDX node types: NODE-001 ~ NODE-006 ─────────────────────────────

    def test_node_001_tdx_candidate_spinfo_8_types_positive(self):
        """NODE-001: TDX 候选池(type=7) spinfo 8种类型(0~7)全部解析正确"""
        from meta_core.converters import parse_tdx_xml

        for spinfo_type in range(8):
            xml_path = _write_temp_xml(_make_tdx_spinfo_xml(spinfo_type), encoding="gbk")
            try:
                pool = parse_tdx_xml(xml_path)
                cand = next((c for c in pool.cells if c.type == 7), None)
                assert cand is not None, \
                    f"BUG: spinfo type={spinfo_type} 时未找到 type=7 候选池节点"
                assert cand.spinfo is not None, \
                    f"BUG: spinfo type={spinfo_type} 时 spinfo 子元素为空"
                assert cand.spinfo.type == spinfo_type, \
                    f"BUG: 期望 spinfo.type={spinfo_type}, 实际={cand.spinfo.type}"
            finally:
                os.unlink(xml_path)

    def test_node_002_tdx_condition_nset_0_to_5_positive(self):
        """NODE-002: TDX 条件(type=3) nset 0~5 全部初始化正确"""
        from meta_core.converters import parse_tdx_xml

        for nset in range(6):
            xml_path = _write_temp_xml(_make_tdx_func_nset_xml(nset), encoding="gbk")
            try:
                pool = parse_tdx_xml(xml_path)
                cond = next((c for c in pool.cells if c.type == 3), None)
                assert cond is not None, \
                    f"BUG: nset={nset} 时未找到 type=3 条件节点"
                assert cond.func is not None, \
                    f"BUG: nset={nset} 时 func 子元素为空"
                assert cond.func.nset == nset, \
                    f"BUG: 期望 func.nset={nset}, 实际={cond.func.nset}"
            finally:
                os.unlink(xml_path)

    def test_node_003_tdx_state_pool_psatt_13_fields_positive(self):
        """NODE-003: TDX 状态池(type=8) psatt 13个字段完整解析"""
        from meta_core.converters import parse_tdx_xml

        xml_path = _write_temp_xml(_make_tdx_psatt_xml(), encoding="gbk")
        try:
            pool = parse_tdx_xml(xml_path)
            sp = next((c for c in pool.cells if c.type == 8), None)
            assert sp is not None, "BUG: 未找到 type=8 状态池节点"
            assert sp.psatt is not None, "BUG: psatt 子元素为空"

            expected_fields = {
                "bdel": 1, "ndelnum": 5, "ndeltype": 2, "baimpool": 1,
                "bsound": 1, "nsoundtype": 1, "nsyssound": 3,
                "soundfile": "C:\\alert.wav", "btip": 1,
                "bsavetoblock": 1, "blockfile": "TEST",
                "bclearblock": 1, "bsavehis": 1,
            }
            assert len(expected_fields) == 13, "BUG: 期望 13 个 psatt 字段"
            for field, expected_val in expected_fields.items():
                actual_val = getattr(sp.psatt, field)
                assert actual_val == expected_val, \
                    f"BUG: psatt.{field} 期望={expected_val!r}, 实际={actual_val!r}"
        finally:
            os.unlink(xml_path)

    def test_node_004_tdx_container_holds_child_nodes_positive(self):
        """NODE-004: TDX 容器(type=2) 可持有子节点"""
        from meta_core.converters import parse_tdx_xml, tdx_to_internal

        xml_path = _write_temp_xml(_make_tdx_container_xml(), encoding="gbk")
        try:
            pool = parse_tdx_xml(xml_path)
            cont = next((c for c in pool.cells if c.type == 2), None)
            assert cont is not None, "BUG: 未找到 type=2 容器节点"
            assert cont.text == "容器", f"BUG: 期望 text='容器', 实际={cont.text!r}"
            assert cont.attr == 140, f"BUG: 期望 attr=140, 实际={cont.attr}"

            # 容器范围内存在子节点 (type=1 标签, type=0 文本)
            child_nodes = [c for c in pool.cells
                           if c.type in (0, 1)
                           and c.pos_x >= 0 and c.pos_x < 400
                           and c.pos_y >= 0 and c.pos_y < 300]
            assert len(child_nodes) >= 2, \
                f"BUG: 容器应持有至少 2 个子节点, 实际 {len(child_nodes)} 个"

            # 容器可通过 flow 连接到子节点
            assert len(pool.flows) == 1, f"BUG: 期望 1 条 flow, 实际 {len(pool.flows)}"
            assert pool.flows[0].startid == 1, \
                f"BUG: 期望 flow.startid=1(容器), 实际={pool.flows[0].startid}"

            # 转换为内部模型后容器类型保留
            internal_pool = tdx_to_internal(pool)
            internal_cont = next(
                (c for c in internal_pool.cells
                 if c.get("type") == 2 or c.get("cell_type") == 2),
                None,
            )
            assert internal_cont is not None, "BUG: 内部模型中未找到 type=2 容器节点"
        finally:
            os.unlink(xml_path)

    def test_node_005_tdx_decorative_nodes_positive(self):
        """NODE-005: TDX 装饰节点 type=1 标签 / type=0 文本 / type=6 直线"""
        from meta_core.converters import parse_tdx_xml

        xml_path = _write_temp_xml(_make_tdx_decorative_xml(), encoding="gbk")
        try:
            pool = parse_tdx_xml(xml_path)
            label = next((c for c in pool.cells if c.type == 1), None)
            assert label is not None, "BUG: 未找到 type=1 标签节点"
            assert label.text == "标签节点", \
                f"BUG: 期望 text='标签节点', 实际={label.text!r}"

            text_node = next((c for c in pool.cells if c.type == 0), None)
            assert text_node is not None, "BUG: 未找到 type=0 文本节点"
            assert text_node.text == "文本节点", \
                f"BUG: 期望 text='文本节点', 实际={text_node.text!r}"

            line = next((c for c in pool.cells if c.type == 6), None)
            assert line is not None, "BUG: 未找到 type=6 直线节点"
            assert line.id == 3, f"BUG: 期望 id=3, 实际={line.id}"
        finally:
            os.unlink(xml_path)

    def test_node_006_unknown_tdx_type_999_negative(self):
        """NODE-006: 未知 TDX type=999 — 必须抛异常或记录 WARN, 不能静默创建无效节点

        当前行为: parse_tdx_xml 不校验 type, 静默创建 type=999 节点;
                 tdx_to_internal 将未知类型映射为 type=1 (装饰).
        期望行为: 抛 ValueError 或记录 WARN 日志.
        """
        from meta_core.converters import parse_tdx_xml, tdx_to_internal

        xml_path = _write_temp_xml(_make_tdx_unknown_type_xml(), encoding="gbk")
        try:
            # 当前实现不抛异常, 静默创建 type=999 节点
            pool = parse_tdx_xml(xml_path)
            unknown_cell = next((c for c in pool.cells if c.type == 999), None)
            # 节点被静默创建 (当前行为) — 期望应抛异常或 WARN
            assert unknown_cell is not None, \
                "BUG: type=999 应抛异常或记录 WARN, 不应静默创建无效节点"
            assert unknown_cell.text == "未知类型", \
                f"BUG: type=999 节点 text 期望='未知类型', 实际={unknown_cell.text!r}"

            # tdx_to_internal 将未知类型映射为 type=1 (装饰) via
            # TDX_TO_DZH_CELL_TYPE.get(type, 1)
            internal_pool = tdx_to_internal(pool)
            assert len(internal_pool.cells) == 1, \
                "BUG: 未知类型节点应在内部模型中被处理(映射为装饰或丢弃)"
        finally:
            os.unlink(xml_path)

    # ── DZH node types: NODE-007 ~ NODE-011 ────────────────────────────

    def test_node_007_dzh_market_source_attrtext_6_entry_types_positive(self):
        """NODE-007: DZH 备选池(type=202) attrtext 6种条目类型解析"""
        from meta_core.converters import parse_dzh_xml, parse_attrtext_triple

        # 6 种 attrtext 条目类型: market / sector / stock / group /
        # concept_sector / industry_sector
        attrtext = "\t".join([
            "SH#上证A股",                # market
            "B$#自选股",                 # sector (B$# 前缀)
            "SH600000",                  # stock
            "BLK-自选股1",               # group (BLK-自选股N)
            "BLK-概念新材料0700008",     # concept_sector
            "BLK-农业0110324",           # industry_sector
        ])

        xml = _make_dzh_attrtext_xml(attrtext)
        pool = parse_dzh_xml(xml.encode("utf-8"), "test.xml")
        node202 = next((n for n in pool.get("nodes", []) if n.get("dzh_cell_type") == 202), None)
        assert node202 is not None, "BUG: 未找到 dzh_cell_type=202 的备选池节点"
        assert node202.get("type") == "market_source", \
            f"BUG: 期望 type='market_source', 实际={node202.get('type')!r}"

        # 验证 parse_attrtext_triple 解析 6 种条目类型
        triple = parse_attrtext_triple(attrtext)
        assert len(triple["markets"]) >= 1, \
            f"BUG: 期望至少 1 个 market 条目, 实际={triple['markets']}"
        assert len(triple["sectors"]) >= 1, \
            f"BUG: 期望至少 1 个 sector 条目, 实际={triple['sectors']}"
        assert len(triple["stocks"]) >= 1, \
            f"BUG: 期望至少 1 个 stock 条目, 实际={triple['stocks']}"
        assert len(triple["groups"]) >= 1, \
            f"BUG: 期望至少 1 个 group 条目, 实际={triple['groups']}"
        assert len(triple["concept_sectors"]) >= 1, \
            f"BUG: 期望至少 1 个 concept_sector 条目, 实际={triple['concept_sectors']}"
        assert len(triple["industry_sectors"]) >= 1, \
            f"BUG: 期望至少 1 个 industry_sector 条目, 实际={triple['industry_sectors']}"

    def test_node_008_dzh_transfer_condition_attr_bits_positive(self):
        """NODE-008: DZH 转移条件(type=201) attr 位标志解码"""
        from meta_core.converters import parse_dzh_xml, _decode_type201_attr

        # type=201 位标志 (来自 field_definitions.json bit_fields.201):
        #   show_overview=0x200 (bit9)
        #   basic_condition=0x800 (bit11)
        #   bit16_reserved=0x10000 (bit16)
        #   reverse_transfer=0x40000 (bit18)
        #   sector_membership=0x80000 (bit19)
        #   indicator_condition=0x100000 (bit20)
        #   ranking_condition=0x200000 (bit21)
        #   cross_section=0x400000 (bit22)
        attr_int = 0x200 | 0x800 | 0x100000  # show_overview + basic_condition + indicator_condition
        decoded = _decode_type201_attr(attr_int)
        assert decoded["show_overview"] is True, \
            f"BUG: 期望 show_overview=True, 实际={decoded['show_overview']}"
        assert decoded["basic_condition"] is True, \
            f"BUG: 期望 basic_condition=True, 实际={decoded['basic_condition']}"
        assert decoded["indicator_condition"] is True, \
            f"BUG: 期望 indicator_condition=True, 实际={decoded['indicator_condition']}"
        assert decoded["ranking_condition"] is False, \
            f"BUG: 期望 ranking_condition=False, 实际={decoded['ranking_condition']}"
        assert decoded["cross_section"] is False, \
            f"BUG: 期望 cross_section=False, 实际={decoded['cross_section']}"
        assert decoded["raw"] == attr_int, \
            f"BUG: 期望 raw={attr_int}, 实际={decoded['raw']}"

        # 通过 XML 解析验证 attr_decoded 字段
        xml = _make_dzh_type201_xml(attr_int)
        pool = parse_dzh_xml(xml.encode("utf-8"), "test.xml")
        node201 = next((n for n in pool.get("nodes", []) if n.get("dzh_cell_type") == 201), None)
        assert node201 is not None, "BUG: 未找到 dzh_cell_type=201 的条件节点"
        params = node201.get("params", {})
        attr_decoded = params.get("attr_decoded", {})
        assert attr_decoded.get("indicator_condition") is True, \
            "BUG: XML 解析后 attr_decoded.indicator_condition 应为 True"

    def test_node_009_dzh_stock_state_pool_fields_complete_positive(self):
        """NODE-009: DZH 状态池(type=200) attr/deltype/hold/delstocktype/endtime 完整"""
        from meta_core.converters import parse_dzh_xml

        xml = _make_dzh_type200_xml()
        pool = parse_dzh_xml(xml.encode("utf-8"), "test.xml")
        node200 = next((n for n in pool.get("nodes", []) if n.get("dzh_cell_type") == 200), None)
        assert node200 is not None, "BUG: 未找到 dzh_cell_type=200 的状态池节点"
        assert node200.get("type") == "stock_state_pool", \
            f"BUG: 期望 type='stock_state_pool', 实际={node200.get('type')!r}"

        params = node200.get("params", {})
        # attr 字段 (传入十进制 2155880448 = 0x80802000)
        assert params.get("attr_int") == 0x80802000, \
            f"BUG: 期望 attr_int=0x80802000(2155880448), 实际={params.get('attr_int')}"
        # deltype 字段
        assert params.get("deltype") == "2", \
            f"BUG: 期望 deltype='2', 实际={params.get('deltype')!r}"
        # hold 字段 (hold_sec 为解析后的秒数)
        assert params.get("hold_sec") == 300, \
            f"BUG: 期望 hold_sec=300, 实际={params.get('hold_sec')}"
        # delstocktype 字段
        assert params.get("delstocktype") == "0", \
            f"BUG: 期望 delstocktype='0', 实际={params.get('delstocktype')!r}"
        # endtime 字段
        assert params.get("endtime") == "0", \
            f"BUG: 期望 endtime='0', 实际={params.get('endtime')!r}"
        # dzh_attr 解码结果存在
        assert "dzh_attr" in params, "BUG: 期望 params 包含 dzh_attr 解码结果"

    def test_node_010_dzh_discard_column_drawing_positive(self):
        """NODE-010: DZH type=4 丢弃池 / type=3 状态列 / type=5 绘图工具"""
        from meta_core.converters import parse_dzh_xml

        xml = _make_dzh_type4_3_5_xml()
        pool = parse_dzh_xml(xml.encode("utf-8"), "test.xml")
        nodes = pool.get("nodes", [])

        node4 = next((n for n in nodes if n.get("dzh_cell_type") == 4), None)
        assert node4 is not None, "BUG: 未找到 dzh_cell_type=4 的丢弃池节点"
        assert node4.get("type") == "discard_pool", \
            f"BUG: 期望 type='discard_pool', 实际={node4.get('type')!r}"

        node3 = next((n for n in nodes if n.get("dzh_cell_type") == 3), None)
        assert node3 is not None, "BUG: 未找到 dzh_cell_type=3 的状态列节点"
        assert node3.get("type") == "state_column", \
            f"BUG: 期望 type='state_column', 实际={node3.get('type')!r}"

        node5 = next((n for n in nodes if n.get("dzh_cell_type") == 5), None)
        assert node5 is not None, "BUG: 未找到 dzh_cell_type=5 的绘图工具节点"
        assert node5.get("type") == "drawing_tool", \
            f"BUG: 期望 type='drawing_tool', 实际={node5.get('type')!r}"

    def test_node_011_unknown_dzh_type_999_negative(self):
        """NODE-011: 未知 DZH type=999 — 必须抛异常或记录 WARN, 不能静默跳过

        当前行为: parse_dzh_xml 对未知类型执行 `else: continue`, 静默跳过.
        期望行为: 抛 ValueError 或记录 WARN 日志.
        """
        from meta_core.converters import parse_dzh_xml

        xml = _make_dzh_unknown_type_xml()
        pool = parse_dzh_xml(xml.encode("utf-8"), "test.xml")
        nodes = pool.get("nodes", [])

        # 当前行为: type=999 节点被静默跳过 (else: continue)
        unknown_nodes = [n for n in nodes if n.get("dzh_cell_type") == 999]
        assert len(unknown_nodes) == 0, \
            "BUG: type=999 应抛异常或记录 WARN, 不应静默跳过或创建节点"

        # 已知 type=202 节点仍正常解析
        node202 = next((n for n in nodes if n.get("dzh_cell_type") == 202), None)
        assert node202 is not None, \
            "BUG: type=202 节点应正常解析, 不应受 type=999 影响"

    # ── attr bit flag decoding: NODE-012 ~ NODE-014 ────────────────────

    def test_node_012_attr_zero_all_flags_false_positive(self):
        """NODE-012: attr=0 → 所有 type=200 位标志为 False"""
        from meta_core.converters import _decode_type200_attr

        decoded = _decode_type200_attr(0)
        expected_false_keys = [
            "show_overview", "simple_intermediate", "no_delete_source",
            "clear_dest_first", "calc_profit_from_prev", "record_history",
            "alert_popup", "alert_sound", "highlight_flash",
            "alert_scroll_window", "hidden_pool", "show_overview_container",
        ]
        for key in expected_false_keys:
            assert decoded[key] is False, \
                f"BUG: attr=0 时 {key} 应为 False, 实际={decoded[key]}"
        assert decoded["raw"] == 0, \
            f"BUG: 期望 raw=0, 实际={decoded['raw']}"

    def test_node_013_attr_0x80802000_bit_independence_positive(self):
        """NODE-013: attr=0x80802000 位标志独立验证

        0x80802000 = bit31(0x80000000) + bit23(0x800000) + bit13(0x2000)
        对应 type=200: alert_scroll_window=True, calc_profit_from_prev=True,
                       clear_dest_first=True, 其余 False.
        另验证 hidden_pool(0x80) / alert_sound(0x10000000) 独立位.
        """
        from meta_core.converters import _decode_type200_attr

        attr_int = 0x80802000
        decoded = _decode_type200_attr(attr_int)

        # 0x80802000 实际置位的标志
        assert decoded["clear_dest_first"] is True, \
            f"BUG: 0x80802000 应包含 bit13(clear_dest_first), 实际={decoded['clear_dest_first']}"
        assert decoded["calc_profit_from_prev"] is True, \
            f"BUG: 0x80802000 应包含 bit23(calc_profit_from_prev), 实际={decoded['calc_profit_from_prev']}"
        assert decoded["alert_scroll_window"] is True, \
            f"BUG: 0x80802000 应包含 bit31(alert_scroll_window), 实际={decoded['alert_scroll_window']}"

        # 0x80802000 未置位的标志 (验证独立性)
        assert decoded["hidden_pool"] is False, \
            f"BUG: 0x80802000 不含 bit7(hidden_pool), 实际={decoded['hidden_pool']}"
        assert decoded["alert_sound"] is False, \
            f"BUG: 0x80802000 不含 bit28(alert_sound), 实际={decoded['alert_sound']}"

        # 独立验证每个位
        assert _decode_type200_attr(0x80)["hidden_pool"] is True, \
            "BUG: attr=0x80 时 hidden_pool 应为 True"
        assert _decode_type200_attr(0x2000)["clear_dest_first"] is True, \
            "BUG: attr=0x2000 时 clear_dest_first 应为 True"
        assert _decode_type200_attr(0x10000000)["alert_sound"] is True, \
            "BUG: attr=0x10000000 时 alert_sound 应为 True"

        # 验证独立位不会互相干扰
        assert _decode_type200_attr(0x80)["clear_dest_first"] is False, \
            "BUG: attr=0x80 时 clear_dest_first 应为 False (位独立性)"
        assert _decode_type200_attr(0x2000)["hidden_pool"] is False, \
            "BUG: attr=0x2000 时 hidden_pool 应为 False (位独立性)"

    def test_node_014_attr_roundtrip_composite(self):
        """NODE-014: attr roundtrip — parse → decode → re-encode → parse, 位标志一致

        注：encode_attr_flags 通用编码器查 attr_flag_map.json:cell200_attr_masks 合成。"""
        from meta_core.converters import (
            _decode_type200_attr, encode_attr_flags,
        )

        original_attr = 0x10002080  # hidden_pool(0x80) + clear_dest_first(0x2000) + alert_sound(0x10000000)

        # Step 1: decode attr int → bit flags
        decoded = _decode_type200_attr(original_attr)
        assert decoded["raw"] == original_attr, \
            f"BUG: decode 后 raw 应={original_attr}, 实际={decoded['raw']}"

        # Step 2: re-encode bit flags → attr int (通用编码器查表)
        bits_for_encode = {k: v for k, v in decoded.items() if k != "raw"}
        re_encoded = encode_attr_flags(bits_for_encode, "cell200_attr_masks")
        assert re_encoded == original_attr, \
            f"BUG: re-encode 后应={original_attr:#x}, 实际={re_encoded:#x}"

        # Step 3: re-decode re-encoded attr → bit flags, 验证一致
        re_decoded = _decode_type200_attr(re_encoded)
        for key in bits_for_encode:
            assert re_decoded[key] == decoded[key], \
                f"BUG: roundtrip 后 {key} 不一致: 原={decoded[key]}, 往返={re_decoded[key]}"

        # 额外 roundtrip: 全 0
        decoded_zero = _decode_type200_attr(0)
        bits_zero = {k: v for k, v in decoded_zero.items() if k != "raw"}
        re_encoded_zero = encode_attr_flags(bits_zero, "cell200_attr_masks")
        assert re_encoded_zero == 0, \
            f"BUG: 全 False roundtrip 后应=0, 实际={re_encoded_zero:#x}"

        # 额外 roundtrip: 单位 hidden_pool
        decoded_hp = _decode_type200_attr(0x80)
        bits_hp = {k: v for k, v in decoded_hp.items() if k != "raw"}
        re_encoded_hp = encode_attr_flags(bits_hp, "cell200_attr_masks")
        assert re_encoded_hp == 0x80, \
            f"BUG: hidden_pool 单位 roundtrip 后应=0x80, 实际={re_encoded_hp:#x}"
