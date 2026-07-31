"""converters.py - 股票池转换器合并模块.

合并自原 converters/ 目录下的 4 个文件（SubTask 29.3）:
  - __init__.py    (原包入口, re-export 已合并到本文件)
  - dzh.py         (DZH XML 解析与导出)
  - tdx.py         (TDX XML 解析与导出)
  - json_xml.py    (JSON 格式导入导出)

合并后原 converters/ 包已删除, 所有符号直接定义在本模块中。
import 语法 `from converters import X` 在包/模块两种形态下都可用,
但子模块 import (`from converters.dzh import X`) 已不再支持, 需改为
`from converters import X`。
"""
from __future__ import annotations

import base64
import json
import logging
import operator
import os
import random
import re
from abc import ABC, abstractmethod
import sys
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Tuple, Union, runtime_checkable
from xml.sax.saxutils import escape as xml_escape

from core.schemas import (
    _parse_attr_bits,
    PoolMetaModel,
    DynamicCellModel,
    DynamicFlowModel,
    PositionModel,
    TdxPsattModel,
    TdxFuncModel,
    TdxSpinfoModel,
    TdxStkModel,
    TdxCellModel,
    TdxFlowModel,
    TdxPoolMetaModel,
    StockSnapshotModel,
    Cell201AttrBitsModel,
    FlowAttrBitsModel,
    FlowModel,
)
from native.validators import should_fire
from converters_common import safe_int, safe_float
from core.import_export_module import _hms_to_seconds
from core.domain import (
    _eval_op,
    _build_op_ctx,
    _resolve_rank,
    _NOPERATE_RULES,
    _RANK_MODES,
)
from core.table_engine import get_global_config_store

logger = logging.getLogger(__name__)


# ======================================================================
# BasePoolConverter / DzhPoolConverter / TdxPoolConverter
# 合并 DZH/TDX 同构解析与序列化逻辑（Task 1: 变更 P1 — meta-pattern convergence）
# ======================================================================

class _StkIO:
    """股票子元素解析混入：合并 _parse_stk_children(DZH) 与 _parse_stk_elements(TDX)。"""

    @staticmethod
    def parse_stks(cell_elem, *, fmt="dzh"):
        stks = []
        for stk_elem in cell_elem.findall("stk"):
            if fmt == "tdx":
                d = {}
                for k, v in stk_elem.attrib.items():
                    d[k] = safe_int(v, 0) if k == "setcode" else v
                stks.append(d)
            else:
                s = {"label": stk_elem.get("label", ""),
                     "t": stk_elem.get("t", ""),
                     "p": stk_elem.get("p", "")}
                tid = stk_elem.get("tid")
                if tid:
                    s["tid"] = tid
                hist = stk_elem.find("hist")
                if hist is not None:
                    s["hist"] = {"t": hist.get("t", ""), "p": hist.get("p", "")}
                ana = stk_elem.find("ana")
                if ana is not None:
                    s["ana"] = {"label": ana.get("label", ""),
                                "t": ana.get("t", ""),
                                "p": ana.get("p", "")}
                stks.append(s)
        return stks


class _StkWriter:
    """股票子元素写入混入：合并 _export_field_stocks(DZH) 与 _add_stks(TDX)。"""

    _TDX_STK_RUNTIME_FIELDS = ("indate", "intime", "inprice", "income", "now",
                               "rise", "volume", "maxrate", "maxperiod",
                               "maxtime", "maxprice", "idaynum")

    @classmethod
    def write_stks(cls, cell_elem, source, *, fmt="dzh"):
        if fmt == "tdx":
            stocks = (getattr(source, "tdx_stocks", None)
                      or getattr(source, "stocks", None)
                      or getattr(source, "stk_list", None))
            if not stocks or not isinstance(stocks, list):
                return
            for s in stocks:
                el = ET.SubElement(cell_elem, "stk")
                setcode_val = getattr(s, "setcode", None)
                code_val = getattr(s, "code", None)
                if setcode_val is not None and code_val is not None:
                    el.set("setcode", str(setcode_val))
                    el.set("code", str(code_val))
                    for f in cls._TDX_STK_RUNTIME_FIELDS:
                        fv = getattr(s, f, None)
                        if fv is not None and str(fv) not in ("", "0", "0.00"):
                            el.set(f, str(fv))
                else:
                    code = getattr(s, "tid", None) or getattr(s, "label", None) or ""
                    el.set("setcode", str(_stock_setcode(code)))
                    el.set("code", code)
        else:
            _orig = source.get("_orig_stks")
            stocks = _orig if _orig is not None else source.get("stocks")
            if not stocks or not isinstance(stocks, list):
                return
            for s in stocks:
                stk = ET.SubElement(cell_elem, "stk")
                stk.text = None
                stk.tail = None
                stk.set("label", s.get("label", ""))
                stk.set("t", s.get("t", ""))
                stk.set("p", s.get("p", ""))
                tid = s.get("tid")
                if tid is not None:
                    stk.set("tid", str(tid))
                hists = s.get("hist")
                if hists and isinstance(hists, list):
                    for h in hists:
                        he = ET.SubElement(stk, "hist")
                        he.text = None
                        he.tail = None
                        for hk, hv in h.items():
                            if hv is not None:
                                he.set(hk, str(hv))
                ana_data = s.get("ana")
                if ana_data and isinstance(ana_data, dict):
                    ae = ET.SubElement(stk, "ana")
                    ae.text = None
                    ae.tail = None
                    for ak, av in ana_data.items():
                        if av is not None:
                            ae.set(ak, str(av))


class BasePoolConverter(ABC):
    """DZH/TDX 股票池转换器基类：合并 4 组同构逻辑。

    子类只覆盖差异（int_fields 表、post_hooks、encoding_priority、cell 包络参数）。
    """

    encoding_priority: tuple = ("utf-8",)

    # ---- 1. 通用元素解析器（合并 _parse_func/psatt/spinfo_element）----
    def _parse_element(self, elem, schema_key, int_fields, post_hook=None):
        data: Dict[str, Any] = {}
        for k, v in elem.attrib.items():
            if k in int_fields:
                data[k] = safe_int(v, 0)
            else:
                data[k] = v
        if post_hook is not None:
            data = post_hook(data)
        return data

    # ---- 2. 通用元素序列化器（合并 _add_func/psatt/spinfo）----
    def _add_element(self, cell_elem, cell, attr_name, model_class, element_name):
        obj = getattr(cell, attr_name, None)
        if obj is None or not isinstance(obj, model_class):
            return
        el = ET.SubElement(cell_elem, element_name)
        for k, v in obj.to_xml_attrs().items():
            el.set(k, v)

    # ---- 3. 通用 pos 解码器（合并 _parse_pos / _parse_tdx_pos）----
    def _decode_pos(self, pos_str, *, as_dict=True):
        zero_dict = {"x": 0, "y": 0, "width": 0, "height": 0}
        zero_tup = (0, 0, 0, 0)
        if not pos_str:
            return zero_dict if as_dict else zero_tup
        try:
            parts = [int(x.strip()) for x in pos_str.split(",")]
            if len(parts) == 4:
                x1, y1, x2, y2 = parts
                if as_dict:
                    return {"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1}
                return (x1, y1, x2 - x1, y2 - y1)
        except (ValueError, TypeError):
            pass
        return zero_dict if as_dict else zero_tup

    # ---- 4. 通用 XML 字节解码器（合并 _decode_xml_content / _decode_tdx_xml）----
    def _decode_xml_bytes(self, raw, encoding_priority, post_process_fn=None):
        if isinstance(raw, bytes):
            text = None
            for enc in encoding_priority:
                try:
                    text = raw.decode(enc)
                    break
                except (UnicodeDecodeError, LookupError):
                    continue
            if text is None:
                text = raw.decode("utf-8", errors="replace")
        else:
            text = raw
        if post_process_fn is not None:
            text = post_process_fn(text)
        return text

    @staticmethod
    def _resolve_edge_endpoint(raw):
        """解析 edge 端点为 node_id 字符串（dict 取 node_id，其他 str 化）。"""
        return raw.get("node_id", "") if isinstance(raw, dict) else str(raw)

    # ---- 5. 主流程模板方法（v8：上提 parse/export 编排骨架）----
    def parse_pool(self, source):
        """parse 主流程模板方法：编排 6 步骨架，子类覆盖差异钩子。"""
        text = self._decode_source(source)
        root = ET.fromstring(text)
        pool_meta = self._extract_pool_meta(root, source)
        cells = self._parse_cells(root)
        flows = self._parse_flows(root)
        return self._build_result(pool_meta, cells, flows)

    def export_pool(self, config):
        """export 主流程模板方法：编排 5 步骨架，子类覆盖差异钩子。"""
        root = self._create_root(config)
        self._serialize_pool_attrs(root, config)
        self._serialize_cells(root, config)
        self._serialize_flows(root, config)
        return self._finalize_xml(root)

    # ---- 6. 差异钩子（@abstractmethod 早失败：实例化时即校验，子类必须覆盖）----
    @abstractmethod
    def _decode_source(self, source): ...
    @abstractmethod
    def _extract_pool_meta(self, root, source): ...
    @abstractmethod
    def _parse_cells(self, root): ...
    @abstractmethod
    def _parse_flows(self, root): ...
    @abstractmethod
    def _build_result(self, pool_meta, cells, flows): ...
    @abstractmethod
    def _create_root(self, config): ...
    @abstractmethod
    def _serialize_pool_attrs(self, root, config): ...
    @abstractmethod
    def _serialize_cells(self, root, config): ...
    @abstractmethod
    def _serialize_flows(self, root, config): ...
    @abstractmethod
    def _finalize_xml(self, root): ...

    # ---- 7. 前端化钩子（G1：默认透传；子类可覆盖为格式特定的前端 dict 转换）----
    def _to_frontend(self, result, name: str):
        """将 parse 结果转为前端可用结构。默认透传，由子类按需覆盖。"""
        return result


class DzhPoolConverter(BasePoolConverter):
    """DZH 股票池转换器：GB18030 优先解码 + DZH pos(dict) + 编码声明清洗。"""

    encoding_priority = ("gb18030", "gbk")

    # v8 表驱动：ency/warning/system 三段 if/elif 收敛为单循环
    _OPTIONAL_POOL_ATTRS = ("ency", "warning", "system")

    @staticmethod
    def _post_process_xml(text):
        text = re.sub(r'<\?xml\s+[^?]*encoding=["\'][^"\']*["\'][^?]*\?>',
                      '<?xml version="1.0"?>', text, count=1)
        text = text.replace("\t", "__DZH_TAB__")
        return text

    def decode_xml_content(self, xml_content):
        return self._decode_xml_bytes(xml_content, self.encoding_priority,
                                      self._post_process_xml)

    def parse_pos(self, pos_str):
        return self._decode_pos(pos_str, as_dict=True)

    # ---- v8 parse 钩子（原 parse_dzh_xml 编排逻辑原封移入）----
    def _decode_source(self, source):
        return self.decode_xml_content(source)

    def _extract_pool_meta(self, root, source):
        filename = getattr(self, "_current_filename", None)
        if filename:
            name = os.path.splitext(os.path.basename(filename))[0]
        else:
            name = "unknown"
        pool_attr_names = {"type", "ver", "mode", "nextid", "backcolor", "ency", "warning", "system"}
        pool_present_attrs = {k for k in root.attrib if k in pool_attr_names}
        pool_meta = {
            "type": root.get("type", "ss-pool"),
            "ver": root.get("ver", "1.0"),
            "mode": root.get("mode", "1"),
            "nextid": safe_int(root.get("nextid"), 0),
            "backcolor": safe_int(root.get("backcolor"), 0),
            "ency": root.get("ency"),
            "warning": root.get("warning"),
            "system": root.get("system"),
            "_has_trades": root.find(".//trades") is not None,
            "_has_opentrades": root.find(".//opentrades") is not None,
            "_present_attrs": pool_present_attrs,
        }
        self._name = name
        self._pool_meta = pool_meta
        self._root = root
        return pool_meta

    def _parse_cells(self, root):
        pool_meta = self._pool_meta
        raw_cells = {}
        for cell_elem in root.findall(".//cell"):
            cid = cell_elem.get("id", "")
            if not cid:
                continue
            _known_cell_attrs = {
                "id", "type", "attr", "pos", "clr", "text", "hold", "col",
                "width", "enter", "exit", "histana", "wizd", "deltype",
                "endtime", "delstocktype", "staattr", "stocknum", "tmpl",
                "inditype", "crc", "indi", "sorttype", "indiparam",
                "attrtext", "reload", "lastload", "url", "intersection_status",
                "stk_list", "setcode",
                "formula", "order", "top_n",
                "op_type", "source_nodes",
                "result_type",
                "line_type", "color", "style",
            }
            _present_attrs = {k for k in cell_elem.attrib if k in _known_cell_attrs}
            _orig_text = cell_elem.get("text")
            raw_cells[cid] = {
                "id": cid,
                "type": safe_int(cell_elem.get("type"), 0),
                "attr": cell_elem.get("attr", "0"),
                "pos": cell_elem.get("pos", ""),
                "clr": cell_elem.get("clr", ""),
                "text": cell_elem.get("text", ""),
                "_orig_text": _orig_text,
                "hold": cell_elem.get("hold"),
                "col": cell_elem.get("col"),
                "width": cell_elem.get("width"),
                "enter": cell_elem.get("enter"),
                "exit": cell_elem.get("exit"),
                "histana": cell_elem.get("histana"),
                "wizd": cell_elem.get("wizd"),
                "deltype": cell_elem.get("deltype"),
                "endtime": cell_elem.get("endtime"),
                "delstocktype": cell_elem.get("delstocktype"),
                "staattr": cell_elem.get("staattr"),
                "stocknum": cell_elem.get("stocknum"),
                "tmpl": cell_elem.get("tmpl"),
                "inditype": cell_elem.get("inditype"),
                "crc": cell_elem.get("crc"),
                "indi": cell_elem.get("indi"),
                "sorttype": cell_elem.get("sorttype"),
                "indiparam": cell_elem.get("indiparam"),
                "attrtext": cell_elem.get("attrtext"),
                "reload": cell_elem.get("reload"),
                "lastload": cell_elem.get("lastload"),
                "url": cell_elem.get("url"),
                "intersection_status": cell_elem.get("intersection_status"),
                "result_type": cell_elem.get("result_type"),
                "_present_attrs": _present_attrs,
            }
            ct_val = safe_int(cell_elem.get("type"), 0)
            if ct_val in (102, 103, 104, 200, 203):
                raw_cells[cid]["stocks"] = _parse_stk_children(cell_elem)
                raw_cells[cid]["anas"] = _parse_ana_children(cell_elem)
                raw_cells[cid]["tradeattr"] = _parse_tradeattr(cell_elem)

        node_id_map = {}
        nodes = []

        for cid, rc in raw_cells.items():
            if cid.startswith('m_'):
                node_id_map[cid] = cid
            else:
                node_id_map[cid] = "m_%s" % cid

        for cid, rc in raw_cells.items():
            ct = rc["type"]
            new_id = node_id_map[cid]
            pos = _parse_pos(rc["pos"])
            label = rc["text"] or ""
            attr_int = safe_int(rc["attr"], 0)

            node = _parse_cell_element(ct, rc, cid, new_id, pos, label, attr_int, safe_int(pool_meta.get("ency"), 0))
            if node is not None:
                nodes.append(node)
        self._node_id_map = node_id_map
        return nodes

    def _parse_flows(self, root):
        node_id_map = self._node_id_map
        raw_flows = []
        for order_idx, flow_elem in enumerate(root.findall(".//flow")):
            _has_timing = {k for k in ["begin", "begint", "end", "endt", "interval"] if flow_elem.get(k) is not None}
            _flow_present_attrs = {k for k in flow_elem.attrib if k in {
                "from", "to", "attr", "clr", "count", "begin", "begint",
                "end", "endt", "interval", "mid",
                "cst", "cet", "cstt", "cett", "c周期", "c_period",
            }}
            raw_flows.append({
                "from": flow_elem.get("from", ""),
                "to": flow_elem.get("to", ""),
                "attr": flow_elem.get("attr", "0"),
                "clr": flow_elem.get("clr", ""),
                "count": flow_elem.get("count"),
                "begin": flow_elem.get("begin"),
                "begint": flow_elem.get("begint"),
                "end": flow_elem.get("end"),
                "endt": flow_elem.get("endt"),
                "interval": flow_elem.get("interval"),
                "mid": flow_elem.get("mid"),
                "cst": flow_elem.get("cst"),
                "cet": flow_elem.get("cet"),
                "cstt": flow_elem.get("cstt"),
                "cett": flow_elem.get("cett"),
                "c_period": flow_elem.get("c_period") or flow_elem.get("c周期"),
                "_order": order_idx,
                "_has_timing": _has_timing,
                "_present_attrs": _flow_present_attrs,
            })

        edges = []
        schedules = []

        for rf in raw_flows:
            src_id = rf["from"]
            tgt_id = rf["to"]
            if not src_id or not tgt_id:
                continue

            src_mapped = node_id_map.get(src_id, src_id if src_id.startswith('m_') else "m_%s" % src_id)
            tgt_mapped = node_id_map.get(tgt_id, tgt_id if tgt_id.startswith('m_') else "m_%s" % tgt_id)

            attr_val = safe_int(rf.get("attr", "0"), 0)
            decoded = _decode_flow_attr(attr_val)
            begin_val = safe_int(rf.get("begin"), 0)
            begint_val = rf.get("begint", "0") or "0"
            end_val = safe_int(rf.get("end"), 0)
            endt_val = rf.get("endt", "0") or "0"
            interval_val = safe_int(rf.get("interval"), 60)
            mid_val = rf.get("mid")

            edge_params = {
                "dzh_attr": attr_val,
                "delete_source": decoded["delete_source"],
                "keep_source": decoded["keep_source"],
                "clear_dest_first": decoded["clear_dest_first"],
                "output_constituent": decoded["output_constituent"],
                "force_move": decoded["force_move"],
                "begin": begin_val,
                "begint": begint_val,
                "begint_format": "hhmmss" if begin_val in BEGINT_HHMMSS_MODES else "seconds",
                "end": end_val,
                "endt": endt_val,
                "interval_sec": interval_val,
                "mid": mid_val,
                "clr": rf.get("clr", "-1"),
                "count": rf.get("count"),
                "cst": rf.get("cst"),
                "cet": rf.get("cet"),
                "cstt": rf.get("cstt"),
                "cett": rf.get("cett"),
                "c_period": rf.get("c_period"),
                "_order": rf["_order"],
                "_has_timing": rf.get("_has_timing", set()),
                "_present_attrs": rf.get("_present_attrs", set()),
            }

            if begin_val != 0:
                time_str = _parse_time_str(begint_val)
                mode = BEGIN_MODE_MAP.get(begin_val, "specified_time")
                schedule_id = "sch_%s" % uuid.uuid4().hex[:6]
                edge_params["schedule_id"] = schedule_id
                schedules.append({
                    "id": schedule_id,
                    "mode": mode,
                    "time": time_str,
                    "begin_value": begin_val,
                    "begint_raw": begint_val,
                    "end_value": end_val,
                    "endt_raw": endt_val,
                    "interval_sec": interval_val,
                    "from_node": src_mapped,
                    "to_node": tgt_mapped,
                })

            edges.append({
                "id": "e_%s" % uuid.uuid4().hex[:8],
                "source": {"node_id": src_mapped},
                "target": {"node_id": tgt_mapped},
                "params": edge_params,
            })
        self._schedules = schedules
        return edges

    def _build_result(self, pool_meta, nodes, edges):
        root = self._root
        name = self._name
        schedules = self._schedules

        # trades / opentrades：合并双重解析循环为单一采集器
        def _collect_trades(xpath, attr_names):
            out = []
            for el in root.findall(xpath):
                d = {}
                for attr_name in attr_names:
                    val = el.get(attr_name)
                    if val is not None:
                        d[attr_name] = val
                if d:
                    out.append(d)
            return out

        raw_trades = _collect_trades(".//trades/trade", [
            "code", "name", "market", "price", "volume",
            "buyprice", "sellprice", "direction", "tradetime",
            "accountno", "tradetype", "rate", "fee"])
        raw_opentrades = _collect_trades(".//opentrades/trade", [
            "code", "name", "market", "targetprice",
            "orderprice", "volume", "direction",
            "tradetype", "accountno", "condition"])

        _STOCK_HOLDER_TYPES = {"stock_state_pool", "result_pool"}
        for n in nodes:
            if n.get("type") in _STOCK_HOLDER_TYPES:
                orig_stks = n.get("params", {}).get("stocks")
                n["params"]["_orig_stks"] = list(orig_stks) if orig_stks else []

        propagated = _propagate_stocks_to_downstream_pools(nodes, edges)

        # Task 23.4: 注入 DZH 全局配置（market_mappings / reload_schedule）到 pool_config，
        # 供下游 services/candidate_pool.py 通过 PoolLoaded 事件订阅获取，消除跨层 import
        return _assemble_pool_result(
            nodes, edges, pool_meta=pool_meta, propagated=propagated, name=name,
            schedules=schedules, trades=raw_trades, opentrades=raw_opentrades,
            market_mappings=load_dzh_market_mappings(), reload_schedule=_DZH_RELOAD_SCHEDULE)

    # ---- v8 export 钩子（原 export_dzh_xml 编排逻辑原封移入）----
    def _create_root(self, config):
        pool_meta = config.get("pool_meta", {})
        nodes = config.get("nodes", [])
        edges = config.get("edges", [])

        pool_present = pool_meta.get("_present_attrs", set())

        pool_type = pool_meta.get("type", _EXPORT_DEFAULTS.get("pool_type", "ss-pool"))
        pool_ver = pool_meta.get("ver", _EXPORT_DEFAULTS.get("pool_ver", "1.0"))
        pool_mode = pool_meta.get("mode", _EXPORT_DEFAULTS.get("pool_mode", 1))
        pool_backcolor = pool_meta.get("backcolor", _EXPORT_DEFAULTS.get("pool_backcolor", _DEFAULT_BACKCOLOR))

        root = ET.Element("pool")
        root.set("type", pool_type)
        root.set("ver", pool_ver)
        root.set("mode", str(pool_mode))
        root.set("backcolor", str(pool_backcolor))
        self._pool_meta = pool_meta
        self._nodes = nodes
        self._edges = edges
        self._pool_present = pool_present
        self._config = config
        return root

    def _serialize_pool_attrs(self, root, config):
        pool_meta = self._pool_meta
        pool_present = self._pool_present
        for attr_name in self._OPTIONAL_POOL_ATTRS:
            val = pool_meta.get(attr_name)
            if val is None:
                continue
            if (not pool_present or attr_name in pool_present) or (val != '' and val != 0):
                root.set(attr_name, str(val))

    def _serialize_cells(self, root, config):
        nodes = self._nodes
        cells_elem = ET.SubElement(root, "cells")

        all_cell_ids = set()
        for node in nodes:
            dzh_id = _get_dzh_cell_id(node)
            try:
                all_cell_ids.add(int(dzh_id))
            except (ValueError, TypeError):
                pass

        # 表驱动：从 dzh_type_map.json export_dispatch 加载节点类型→构建函数映射
        _dzh_tm = get_global_config_store().get_table("dzh_type_map") if get_global_config_store() else {}
        _export_dispatch = _dzh_tm.get('export_dispatch', {})
        _type_map = _dzh_tm.get('type_map', {})

        for node in nodes:
            node_type = node.get("type", "")
            dzh_cell_type = node.get("dzh_cell_type")

            if node.get("_visual_only"):
                continue

            cell = ET.SubElement(cells_elem, "cell")
            dzh_id = _get_dzh_cell_id(node)
            cell.set("id", dzh_id)

            # 表驱动：先按 node_type 查表，再按 dzh_cell_type 反查 type_map
            dispatch_entry = _export_dispatch.get(node_type)
            if dispatch_entry is None and dzh_cell_type is not None:
                resolved_type = _type_map.get(str(dzh_cell_type))
                dispatch_entry = _export_dispatch.get(resolved_type) if resolved_type else None

            if dispatch_entry:
                type_rule = dict(dispatch_entry)
                # 200/203 子类型分发：sub_type_dispatch 处理 203/4 子类型
                sub_dispatch = type_rule.get("sub_type_dispatch")
                if sub_dispatch and dzh_cell_type is not None:
                    sub_rule = sub_dispatch.get(str(dzh_cell_type))
                    if sub_rule:
                        if "override" in sub_rule:
                            # 子类型覆盖为另一规则（如 200→4 走 discard_pool）
                            type_rule = dict(_export_dispatch.get(sub_rule["override"], {}))
                        else:
                            if "force_type" in sub_rule:
                                type_rule["force_type"] = sub_rule["force_type"]
                            if "export_fields_add" in sub_rule:
                                type_rule["export_fields"] = list(type_rule.get("export_fields", [])) + sub_rule["export_fields_add"]
                # handler=null 的类型（如 flow_arrow）走内联逻辑
                if node_type == "flow_arrow" or dzh_cell_type == 6:
                    _build_flow_arrow_cell(cell, node)
                else:
                    _build_cell(cell, node, type_rule, dzh_cell_type)
            else:
                logger.warning("DZH导出: 未匹配的节点类型 node_type=%s dzh_cell_type=%s", node_type, dzh_cell_type)
        self._all_cell_ids = all_cell_ids

    def _serialize_flows(self, root, config):
        edges = self._edges
        nodes = self._nodes
        flows_elem = ET.SubElement(root, "flows")

        sorted_edges = sorted(
            [e for e in edges if isinstance(e, dict)],
            key=lambda e: e.get("params", {}).get("_order", e.get("params", {}).get("exec_order", 999)),
        )

        for edge in sorted_edges:
            if not isinstance(edge, dict):
                continue
            src_raw = edge.get("source", "")
            tgt_raw = edge.get("target", "")
            source_node_id = self._resolve_edge_endpoint(src_raw)
            target_node_id = self._resolve_edge_endpoint(tgt_raw)
            source_dzh_id = _find_node_dzh_id(nodes, source_node_id)
            target_dzh_id = _find_node_dzh_id(nodes, target_node_id)
            edge_params = edge.get("params", {})

            flow = ET.SubElement(flows_elem, "flow")
            flow.set("from", source_dzh_id)
            flow.set("to", target_dzh_id)
            flow.set("attr", _encode_flow_attr(edge_params))
            if _should_export_attr(edge_params, "clr"):
                flow.set("clr", str(edge_params.get("clr", "-1")))
            _add_timing_attrs(flow, edge_params)

            if _should_export_attr(edge_params, "count"):
                count = edge_params.get("count")
                if count is not None:
                    flow.set("count", str(count))

    def _finalize_xml(self, root):
        config = self._config
        pool_meta = self._pool_meta
        all_cell_ids = self._all_cell_ids

        # trades / opentrades：合并双重 if/elif 块为单一容器发射器
        def _emit_trade_container(tag, items, has_flag):
            if has_flag or items:
                container = ET.SubElement(root, tag)
                for it in (items or []):
                    te = ET.SubElement(container, "trade")
                    for key, val in it.items():
                        te.set(key, str(val))

        _emit_trade_container("trades", config.get("trades"),
                              pool_meta.get("_has_trades", False))
        _emit_trade_container("opentrades", config.get("opentrades"),
                              pool_meta.get("_has_opentrades", False))

        max_cell_id = max(all_cell_ids) if all_cell_ids else 0
        original_nextid = pool_meta.get("nextid")
        if original_nextid is not None:
            root.set("nextid", str(original_nextid))
        else:
            root.set("nextid", str(max_cell_id + 1))

        xml_str = ET.tostring(root, encoding="utf-8").decode("utf-8")
        xml_str = xml_str.replace("&#10;", "&#xA;")
        xml_str = xml_str.replace("&#13;", "\r")
        xml_str = xml_str.replace("__DZH_NEWLINE__", "&#xA;")
        xml_str = xml_str.replace("__DZH_TAB__", "\t")
        xml_declaration = '<?xml version="1.0" encoding="UTF-8"?>'
        print("[META_CORE EXPORTER] Using UTF-8 encoding")
        full_xml = xml_declaration + "\n" + xml_str
        return full_xml.encode("utf-8")


class TdxPoolConverter(BasePoolConverter):
    """TDX 股票池转换器：GB2312 优先解码 + TDX pos(tuple) + 元素 schema 表驱动。"""

    encoding_priority = ("gb2312", "gbk", "gb18030", "utf-8")

    # 元素序列化 3 行表（替代 _add_func/_add_psatt/_add_spinfo）
    _ELEMENT_SERIALIZERS = (
        ("tdx_func", TdxFuncModel, "func"),
        ("tdx_psatt", TdxPsattModel, "psatt"),
        ("tdx_spinfo", TdxSpinfoModel, "spinfo"),
    )

    # ---- 元素解析 post_hooks（替代 _parse_func/psatt/spinfo_element 的差异分支）----
    @staticmethod
    def _func_post_hook(data):
        if "fsecond" in data:
            data["fsecond"] = safe_float(data["fsecond"], 0.0)
        return data

    @staticmethod
    def _psatt_post_hook(data):
        if "bsavehis" not in data:
            data["bsavehis"] = 0
        return data

    @staticmethod
    def _spinfo_post_hook(data):
        spinfo_type = data.get("type", 0)
        type_info = SPINFO_TYPE_MAP.get(spinfo_type, {"label": "未知类型", "market": ""})
        data["type_label"] = type_info["label"]
        if "market" not in data:
            data["market"] = type_info["market"]
        if spinfo_type == 4:
            data["market"] = ""
        customblockname = data.get("customblockname", "")
        sector_type_map = _get_element_sector_type_map("spinfo")
        data["sector_type"] = sector_type_map.get(customblockname, 0)
        return data

    def parse_func_element(self, elem):
        return self._parse_element(elem, "func", _get_element_int_fields("func"),
                                   self._func_post_hook)

    def parse_psatt_element(self, elem):
        return self._parse_element(elem, "psatt", _get_element_int_fields("psatt"),
                                   self._psatt_post_hook)

    def parse_spinfo_element(self, elem):
        return self._parse_element(elem, "spinfo", _get_element_int_fields("spinfo"),
                                   self._spinfo_post_hook)

    def add_func(self, cell_elem, cell):
        self._add_element(cell_elem, cell, "tdx_func", TdxFuncModel, "func")

    def add_psatt(self, cell_elem, cell):
        self._add_element(cell_elem, cell, "tdx_psatt", TdxPsattModel, "psatt")

    def add_spinfo(self, cell_elem, cell):
        self._add_element(cell_elem, cell, "tdx_spinfo", TdxSpinfoModel, "spinfo")

    def add_stks(self, cell_elem, cell):
        _StkWriter.write_stks(cell_elem, cell, fmt="tdx")

    def decode_tdx_xml(self, raw_bytes):
        return self._decode_xml_bytes(raw_bytes, self.encoding_priority)

    def parse_tdx_pos(self, pos_str):
        return self._decode_pos(pos_str, as_dict=False)

    # ---- v8 parse 钩子（原 parse_tdx_xml 编排逻辑原封移入）----
    def _decode_source(self, source):
        with open(source, "rb") as f:
            raw_bytes = f.read()
        return self.decode_tdx_xml(raw_bytes)

    def _extract_pool_meta(self, root, source):
        filepath = source
        # 版本自动检测
        xml_version = detect_xml_version(root)

        # 兼容：<pool> 可能是根元素（TDX原生格式）或子元素
        if root.tag == "pool":
            pool_elem = root
        else:
            pool_elem = root.find("pool")
        if pool_elem is None:
            raise ValueError(f"No <pool> element found in TDX XML: {filepath}")

        # 解析 pool 级别属性
        nextid = safe_int(pool_elem.get("nextid"), 0)
        backcolor = safe_int(pool_elem.get("backcolor"), 16777216)
        self._xml_version = xml_version
        self._pool_elem = pool_elem
        return {"nextid": nextid, "backcolor": backcolor}

    def _parse_cells(self, root):
        xml_version = self._xml_version
        pool_elem = self._pool_elem
        # 解析 cells
        cells: List[TdxCellModel] = []
        cells_elem = pool_elem.find("cells")
        if cells_elem is not None:
            for cell_elem in cells_elem.findall("cell"):
                cid = safe_int(cell_elem.get("id"), 0)
                ctype = safe_int(cell_elem.get("type"), 0)
                cattr = safe_int(cell_elem.get("attr"), 0)
                cclr = safe_int(cell_elem.get("clr"), -1)
                cclrtext = safe_int(cell_elem.get("clrtext"), 0)
                csolid = safe_int(cell_elem.get("solid"), 0)
                ctext = cell_elem.get("text", "")

                # V7.x 新增: cell.setcode 属性 (0=深圳SZ, 1=上海SH, 2=北交所BJ)
                csetcode = cell_elem.get("setcode")
                if csetcode is not None:
                    csetcode = safe_int(csetcode, None)  # 保持原始值或 None
                else:
                    csetcode = None  # 未指定市场（兼容旧版）

                # V8.x 新增: disabled / show_row_num 标志
                cdisabled = cell_elem.get("disabled")
                if cdisabled is not None:
                    cdisabled = safe_int(cdisabled, 0)
                    cdisabled = True if cdisabled != 0 else False
                else:
                    cdisabled = False

                cshowrownum = cell_elem.get("show_row_num")  # 尝试多种可能的XML属性名
                if cshowrownum is None:
                    cshowrownum = cell_elem.get("showrownum")  # 备选命名
                if cshowrownum is not None:
                    cshowrownum = safe_int(cshowrownum, 0)
                    cshowrownum = True if cshowrownum != 0 else False
                else:
                    cshowrownum = False

                pos_x, pos_y, width, height = _parse_tdx_pos(cell_elem.get("pos", ""))

                cell_data: Dict[str, Any] = {
                    "id": cid,
                    "type": ctype,
                    "attr": cattr,
                    "pos_x": pos_x,
                    "pos_y": pos_y,
                    "width": width,
                    "height": height,
                    "clr": cclr,
                    "clrtext": cclrtext,
                    "solid": csolid,
                    "text": ctext,
                    "setcode": csetcode,       # V7.x: 市场代码
                    "disabled": cdisabled,           # V8.x: 停止运算标志
                    "show_row_num": cshowrownum,     # V8.x: 显示行号标志
                    "xml_version": xml_version, # 版本标识
                }

                # 子元素：func（条件 type=3）
                func_elem = cell_elem.find("func")
                if func_elem is not None:
                    cell_data["func"] = _parse_func_element(func_elem)

                # 子元素：psatt（状态池 type=8）
                psatt_elem = cell_elem.find("psatt")
                if psatt_elem is not None:
                    cell_data["psatt"] = _parse_psatt_element(psatt_elem)

                # 子元素：spinfo（候选池 type=7）
                spinfo_elem = cell_elem.find("spinfo")
                if spinfo_elem is not None:
                    cell_data["spinfo"] = _parse_spinfo_element(spinfo_elem)

                # 子元素：stk（股票列表）
                stks = _parse_stk_elements(cell_elem)
                if stks:
                    cell_data["stks"] = stks

                cells.append(TdxCellModel.from_dict(cell_data))
        return cells

    def _parse_flows(self, root):
        pool_elem = self._pool_elem
        # 解析 flows
        flows: List[TdxFlowModel] = []
        flows_elem = pool_elem.find("flows")
        if flows_elem is not None:
            for flow_elem in flows_elem.findall("flow"):
                flow_data: Dict[str, Any] = {}
                for k, v in flow_elem.attrib.items():
                    flow_data[k] = safe_int(v, 0)
                flows.append(TdxFlowModel.from_dict(flow_data))
        return flows

    def _build_result(self, pool_meta, cells, flows):
        nextid = pool_meta["nextid"]
        backcolor = pool_meta["backcolor"]
        return TdxPoolMetaModel(nextid=nextid, backcolor=backcolor, cells=cells, flows=flows)

    # ---- v8 export 钩子（原 _build_tdx_xml 编排逻辑原封移入）----
    def _create_root(self, config):
        pool_data = config
        mapping = _get_xml_mapping()
        self._tdx_pool_cfg, self._tdx_cell_cfg, self._tdx_flow_cfg = mapping['pool'], mapping['cell'], mapping['flow']
        _type_map = get_global_config_store().get_table("dzh_type_map") if get_global_config_store() else {}
        self._tdx_dzh_to_tdx = {int(k): v for k, v in _type_map.get("dzh_to_tdx", {}).items()}
        # 表驱动：前端类型名 → TDX数字类型
        self._tdx_frontend_to_tdx = _type_map.get("frontend_to_tdx", {})
        root = ET.Element(self._tdx_pool_cfg['root_element'])
        pool_el = ET.SubElement(root, self._tdx_pool_cfg['pool_element'])
        self._tdx_pool_el = pool_el
        self._tdx_containers = {cd['element']: ET.SubElement(pool_el, cd['element']) for cd in self._tdx_pool_cfg['pool_children']}
        # 表驱动：建立字符串ID→数字ID映射（TDX XML要求数字ID）
        nodes_list = pool_data.get("nodes", [])
        self._tdx_str_id_map = {}
        for idx, node in enumerate(nodes_list):
            nid = node.get("id", "")
            if nid and not str(nid).isdigit():
                self._tdx_str_id_map[nid] = str(idx + 1)
        return root

    def _serialize_pool_attrs(self, root, config):
        pool_data = config
        pool_el = self._tdx_pool_el
        pool_cfg = self._tdx_pool_cfg
        for ad in pool_cfg['pool_attributes']:
            pool_el.set(ad['attr'], str(_resolve_field(pool_data, ad['field'], ad.get('default'))))

    def _serialize_cells(self, root, config):
        pool_data = config
        cell_cfg = self._tdx_cell_cfg
        containers = self._tdx_containers
        dzh_to_tdx = self._tdx_dzh_to_tdx
        frontend_to_tdx = self._tdx_frontend_to_tdx
        _str_id_map = self._tdx_str_id_map
        nodes_list = pool_data.get("nodes", [])
        for node in nodes_list:
            params, pos = node.get("params", {}) or {}, node.get("position", {})
            # 类型解析优先级：dzh_cell_type > type(前端名) > 0
            raw_type = node.get("dzh_cell_type")
            if raw_type is None:
                ftype = node.get("type", "")
                raw_type = frontend_to_tdx.get(ftype) if ftype else 0
            tdx_type = dzh_to_tdx.get(int(raw_type), int(raw_type)) if raw_type is not None else 0
            # ID解析：字符串ID查表，数字ID直接用，否则自增
            nid = node.get("id", "0")
            cell_id = _str_id_map.get(nid, nid) if not str(nid).isdigit() else nid
            x1 = int(pos.get("x", 0)) if pos else 0
            y1 = int(pos.get("y", 0)) if pos else 0
            w = int(pos.get("width", 120)) if pos else 120
            h = int(pos.get("height", 64)) if pos else 64
            ctx = {
                '_tdx_type': tdx_type,
                '_pos': f"{x1},{y1},{x1 + w},{y1 + h}",
                '_text': params.get("text", node.get("text", node.get("label", ""))),
                'params': params,
                'node': node,
            }
            el = ET.SubElement(containers['cells'], cell_cfg['element'])
            el.set('id', str(cell_id))
            for ad in cell_cfg['attributes']:
                if ad.get('attr') == 'id':
                    continue  # 已在上面设置
                val = _apply_attr_defaults(_resolve_attr_tdx(ad['field'], ad, ctx), ad, tdx_type)
                if val is not None:
                    el.set(ad['attr'], str(val))
                elif 'default' in ad:
                    el.set(ad['attr'], str(ad['default']))
            for cd in cell_cfg.get('children_by_type', {}).get(str(tdx_type), []):
                src = _resolve_field(node, cd['field']) or (_resolve_field(node, cd['alt_field']) if cd.get('alt_field') else None)
                if cd['mode'] == 'dict_attrs' and isinstance(src, dict) and src:
                    ch = ET.SubElement(el, cd['element'])
                    for k, v in src.items():
                        ch.set(k, str(v))
                elif cd['mode'] == 'list_of_dicts' and isinstance(src, list):
                    for item in src:
                        # 兼容：stocks可能是纯字符串列表，自动转为{code: xxx}格式
                        if isinstance(item, str):
                            item = {'code': item}
                        if isinstance(item, dict):
                            ie = ET.SubElement(el, cd['element'])
                            for ia in cd.get('item_attrs', []):
                                ie.set(ia['attr'], str(item.get(ia['field'], ia.get('default', ''))))

    def _serialize_flows(self, root, config):
        pool_data = config
        flow_cfg = self._tdx_flow_cfg
        containers = self._tdx_containers
        _str_id_map = self._tdx_str_id_map
        for edge in pool_data.get("edges", []):
            ep = edge.get("params", {}) or {}
            # 兼容：source/target 可能是字符串或dict
            so_raw = edge.get("source", "")
            to_raw = edge.get("target", "")
            so_str = self._resolve_edge_endpoint(so_raw)
            to_str = self._resolve_edge_endpoint(to_raw)
            # 字符串ID通过映射表转数字ID
            start_id = _str_id_map.get(so_str, so_str) if so_str and not so_str.isdigit() else (so_str or "0")
            end_id = _str_id_map.get(to_str, to_str) if to_str and not to_str.isdigit() else (to_str or "0")
            ctx = {
                'params': ep,
                'edge': edge,
                'source.node_id': start_id,
                'target.node_id': end_id,
            }
            fe = ET.SubElement(containers['flows'], flow_cfg['element'])
            for ad in flow_cfg['attributes']:
                val = _apply_attr_defaults(_resolve_attr_tdx(ad['field'], ad, ctx), ad)
                if val is not None:
                    fe.set(ad['attr'], str(val))
                elif 'default' in ad:
                    fe.set(ad['attr'], str(ad['default']))

    def _finalize_xml(self, root):
        _indent_xml(root)
        filepath = self._current_filepath
        with open(filepath, 'wb') as fh:
            fh.write(b'<?xml version="1.0" encoding="GBK"?>\n')
            fh.write(ET.tostring(root, encoding='gbk', xml_declaration=False))

    # G2: 覆盖 BasePoolConverter._to_frontend，将 TdxPoolMetaModel 转为前端 dict
    def _to_frontend(self, result, name: str) -> dict:
        mapping = _get_xml_mapping()
        node_cfg, edge_cfg = mapping['frontend_node'], mapping['frontend_edge']
        _type_map = get_global_config_store().get_table("dzh_type_map") if get_global_config_store() else {}
        tdx_type_map = {int(k): v for k, v in _type_map.get("tdx_to_frontend", {}).items()}
        pos_cfg = node_cfg['position']
        nodes = []
        for cell in result.cells:
            node = {}
            for fd in node_cfg['fields']:
                src = fd['source']
                if src == 'id':
                    val = str(cell.id)
                elif src == 'type' and 'lookup' in fd:
                    val = tdx_type_map.get(cell.type, fd.get('default', ''))
                elif src == 'text':
                    val = cell.text or fd.get('default', '')
                else:
                    val = getattr(cell, src, fd.get('default', ''))
                if fd.get('transform') in _TRANSFORMS:
                    val = _TRANSFORMS[fd['transform']](val, cell)
                node[fd['target']] = val
            node['position'] = {
                k: max(getattr(cell, pos_cfg[k], 0), pos_cfg[f'min_{k}']) if k in ('width', 'height') else getattr(cell, pos_cfg[k], 0)
                for k in ('x', 'y', 'width', 'height')
            }
            node['params'] = {}
            for pd in node_cfg['params']:
                v = getattr(cell, pd['source'], None)
                node['params'][pd['target']] = _TRANSFORMS[pd['transform']](v, cell) if pd.get('transform') in _TRANSFORMS else v
            for sd in node_cfg['sub_objects']:
                sub = getattr(cell, sd['source_attr'], None)
                if sub is not None:
                    sd_data = [_model_to_dict(i) for i in sub] if isinstance(sub, list) else _model_to_dict(sub)
                    for tk in sd['target_keys']:
                        node['params'][tk] = sd_data
                    if 'count_key' in sd and isinstance(sd_data, list):
                        node['params'][sd['count_key']] = len(sd_data)
            nodes.append(node)
        edges = []
        for flow in result.flows:
            fid = str(getattr(flow, 'startid', getattr(flow, 'from', '0')))
            tid = str(getattr(flow, 'endid', getattr(flow, 'to', '0')))
            edge = {'id': f"{fid}_{tid}", 'source': {'node_id': fid}, 'target': {'node_id': tid}, 'params': {}}
            fd = _model_to_dict(flow)
            if fd:
                edge['params'].update({k: v for k, v in fd.items() if k != 'id'})
            for sp in edge_cfg['special_params']:
                val = getattr(flow, sp['source'], None)
                if val is not None:
                    edge['params'][sp['target']] = val
                    if sp.get('transform') in _TRANSFORMS:
                        edge['params'][sp['target']] = _TRANSFORMS[sp['transform']](val, flow)
                    cond = sp.get('conditional_set')
                    if cond and val == cond['when_value']:
                        edge['params'].update(cond['also_set'])
                elif 'default' in sp:
                    edge['params'][sp['target']] = sp['default']
            edges.append(edge)
        return {
            'name': name,
            'nodes': nodes,
            'edges': edges,
            'pool_meta': {'type': 'tdx', 'ver': '1.0', 'mode': '1', 'nextid': result.nextid, 'backcolor': result.backcolor},
        }

    @staticmethod
    def tdx_to_internal(tdx_pool: TdxPoolMetaModel) -> PoolMetaModel:
        """
        将 TdxPoolMetaModel 转换为内部统一模型 PoolMetaModel。

        映射规则：
          - TDX type=0,1 → internal type=1 (LabelCellModel)
          - TDX type=2   → internal type=2 (ContainerCellModel)
          - TDX type=3   → internal type=201 (ConditionCellModel) + tdx_func
          - TDX type=7   → internal type=202 (CandidateCellModel) + tdx_spinfo
          - TDX type=8   → internal type=200 (StatePoolCellModel) + tdx_psatt
          - TDX flow tran=1 → move mode; tran=0 → copy mode

        Args:
            tdx_pool: 解析后的 TDX 池模型。

        Returns:
            PoolMetaModel 统一内部模型，pool_type="tdx"。
        """
        internal_cells: List[Any] = []

        _tdx_to_dzh = (get_global_config_store().get_table("dzh_type_map") if get_global_config_store() else {}).get("tdx_to_dzh", {})
        for tdx_cell in tdx_pool.cells:
            internal_type = int(_tdx_to_dzh.get(str(tdx_cell.type), 1))

            pos = PositionModel(
                x=tdx_cell.pos_x,
                y=tdx_cell.pos_y,
                width=tdx_cell.width,
                height=tdx_cell.height,
            )

            if internal_type == 201:
                # type=3 → 条件单元
                cell = _make_tdx_cell(201, tdx_cell, pos, tdx_func=tdx_cell.func)

            elif internal_type == 202:
                # type=7 → 候选池/股票源
                # 保留原始 spinfo（可能为 None），不创建推断 spinfo 以保证 roundtrip 保真度
                spinfo = tdx_cell.spinfo

                # 当 spinfo 存在但 market 为空时，从 stk 推断 market 存为运行时属性
                inferred_market = None
                if spinfo is not None and not spinfo.market and tdx_cell.stks:
                    setcodes = set(stk.setcode for stk in tdx_cell.stks)
                    market_parts = []
                    if 0 in setcodes:
                        market_parts.append("SZ")
                    if 1 in setcodes:
                        market_parts.append("SH")
                    if 2 in setcodes:
                        market_parts.append("BJ")
                    inferred_market = ",".join(market_parts) if market_parts else ""

                cell = _make_tdx_cell(202, tdx_cell, pos,
                                      tdx_spinfo=spinfo, tdx_stocks=tdx_cell.stks)
                # 运行时推断的 market 信息（不影响导出）
                if inferred_market is not None:
                    cell.inferred_market = inferred_market

            elif internal_type == 200:
                # type=8 → 输出/状态池
                stocks: List[StockSnapshotModel] = []
                for stk in tdx_cell.stks:
                    stocks.append(StockSnapshotModel(
                        label=stk.tq_code, t=stk.indate, p=stk.inprice,
                        setcode=stk.setcode, code=stk.code,
                        indate=stk.indate, intime=stk.intime, inprice=stk.inprice,
                        income=stk.income, now=stk.now, rise=stk.rise,
                        volume=stk.volume, maxrate=stk.maxrate, maxperiod=stk.maxperiod,
                        maxtime=stk.maxtime, maxprice=stk.maxprice, idaynum=stk.idaynum,
                    ))
                cell = _make_tdx_cell(200, tdx_cell, pos,
                                      stocks=stocks, stk_list=stocks,
                                      tdx_psatt=tdx_cell.psatt)

            elif internal_type == 2:
                # type=2 → 容器
                cell = DynamicCellModel.from_dict({
                    'id': str(tdx_cell.id),
                    'type': 2,
                    'attr': tdx_cell.attr,
                    'pos': pos.to_tuple(),
                    'clr': tdx_cell.clr,
                    'text': tdx_cell.text,
                })
                cell.clrtext = tdx_cell.clrtext
                cell.solid = tdx_cell.solid
                cell.tdx_type = tdx_cell.type

            else:
                # type=0,1,4,5,6 等 → 标签/装饰
                cell = DynamicCellModel.from_dict({
                    'id': str(tdx_cell.id),
                    'type': 1,
                    'attr': tdx_cell.attr,
                    'pos': pos.to_tuple(),
                    'clr': tdx_cell.clr,
                    'text': tdx_cell.text,
                })
                cell.clrtext = tdx_cell.clrtext
                cell.solid = tdx_cell.solid
                cell.tdx_type = tdx_cell.type

            internal_cells.append(cell)

        # 转换 flows
        internal_flows: List[DynamicFlowModel] = []
        for tdx_flow in tdx_pool.flows:
            # tran=1 → move (delete_source=0x1)
            # tran=0 → copy (keep_source=0x1000)
            if tdx_flow.tran == 1:
                flow_attr = 0x1      # move mode
            else:
                flow_attr = 0x1000   # copy mode

            flow = DynamicFlowModel.from_dict({
                'from': str(tdx_flow.startid),
                'to': str(tdx_flow.endid),
                'attr': flow_attr,
                'clr': tdx_flow.clr,
            })
            # Attach TDX-specific metadata as extra attributes
            flow.tdx_clr = tdx_flow.clr
            flow.tdx_size = tdx_flow.size
            flow.tdx_tran = tdx_flow.tran
            flow.tdx_emptyps = tdx_flow.emptyps
            flow.tdx_starttype = tdx_flow.starttype
            flow.tdx_starttime = tdx_flow.starttime
            flow.tdx_starttimetype = tdx_flow.starttimetype
            flow.tdx_starttimehms = tdx_flow.starttimehms
            flow.tdx_cxtype = tdx_flow.cxtype
            flow.tdx_cxtime = tdx_flow.cxtime
            flow.tdx_cxtimetype = tdx_flow.cxtimetype
            flow.tdx_jgtime = tdx_flow.jgtime
            internal_flows.append(flow)

        return PoolMetaModel(
            pool_type="tdx",
            ver="1.0",
            mode="1",
            nextid=tdx_pool.nextid,
            backcolor=tdx_pool.backcolor,
            cells=internal_cells,
            flows=internal_flows,
        )

    @staticmethod
    def convert_tdx_to_config(pool_meta: PoolMetaModel) -> Dict[str, Any]:
        """
        将 TDX PoolMetaModel 转换为执行引擎可用的配置字典。

        Args:
            pool_meta: pool_type="tdx" 的 PoolMetaModel 实例。

        Returns:
            配置字典，包含以下键:
              - "pool_meta": 池级别元数据 {type, backcolor, nextid}
              - "nodes":     节点列表，每项含 id, type, position, params, label
              - "edges":     边列表，每项含 source, target, params
        """
        nodes: List[Dict[str, Any]] = []
        for cell in pool_meta.cells:
            ct = _get_dzh_cell_type(cell)
            converter = _CELL_CONVERTERS.get(ct)
            if converter is not None:
                nodes.append(converter(cell))
            else:
                nodes.append(_convert_decoration_cell(cell))

        edges: List[Dict[str, Any]] = [_convert_flow(f) for f in pool_meta.flows]

        return _assemble_pool_result(
            nodes, edges,
            pool_meta={"type": "tdx",
                       "backcolor": _safe_get(pool_meta, "backcolor", 16777216),
                       "nextid": _safe_get(pool_meta, "nextid", 0)})

    @staticmethod
    def load_tdx_pool_config(xml_path):
        try:
            _HR = getattr(TdxPoolConverter, '_HR', None)
            if _HR is None:
                _HR = {"fn": parse_tdx_xml}
                setattr(TdxPoolConverter, '_HR', _HR)
            return _TDX_CONVERTER._to_frontend(_HR["fn"](xml_path), os.path.basename(xml_path).replace('.xml', ''))
        except Exception as ex:
            # fail-fast 标记：返回带 error 字段的结构，禁止静默回退空字典
            logger.warning("加载TDX池配置失败: %s（已标记 error 字段）", ex, exc_info=True)
            return {"error": f"加载TDX池配置失败: {ex}", "xml_path": str(xml_path)}


# 模块级单例
_DZH_CONVERTER = DzhPoolConverter()
_TDX_CONVERTER = TdxPoolConverter()

# 向后兼容：原模块级函数委托到转换器单例（消除重复 def 定义）。
# 原签名 _decode_xml_content(xml_content) / _parse_pos(pos_str) /
# _decode_tdx_xml(raw_bytes) / _parse_tdx_pos(pos_str) /
# _parse_func_element(elem) / _parse_psatt_element(elem) /
# _parse_spinfo_element(elem) / _parse_stk_children(cell_elem) /
# _parse_stk_elements(cell_elem) 全部保留可调用。
_decode_xml_content = _DZH_CONVERTER.decode_xml_content
_parse_pos = _DZH_CONVERTER.parse_pos
_parse_stk_children = _StkIO.parse_stks
_decode_tdx_xml = _TDX_CONVERTER.decode_tdx_xml
_parse_tdx_pos = _TDX_CONVERTER.parse_tdx_pos
_parse_func_element = _TDX_CONVERTER.parse_func_element
_parse_psatt_element = _TDX_CONVERTER.parse_psatt_element
_parse_spinfo_element = _TDX_CONVERTER.parse_spinfo_element


def _parse_stk_elements(cell_elem: ET.Element) -> List[Dict[str, Any]]:
    """解析 <cell> 内的所有 <stk> 子元素（TDX 格式）。"""
    return _StkIO.parse_stks(cell_elem, fmt="tdx")


# ======================================================================
# DZH 股票池转换器（原 converters/dzh.py）
# ======================================================================



def _run_async(coro_factory):
    """同步运行异步协程，兼容已有事件循环的场景。

    FormulaRouter.eval_batch 为 async，而 converters 为同步入口，
    通过此桥接调用。当主线程已有运行中的事件循环时，在新线程中创建独立 loop 执行。
    """
    import asyncio
    import concurrent.futures
    try:
        asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(coro_factory())).result()
    except RuntimeError:
        return asyncio.run(coro_factory())


# ============================================================
# 配置加载（表驱动：从 config/*.json 加载，带缓存）
# ============================================================

_DZH_CFG_DIR = Path(__file__).resolve().parent / "config"
_dzh_cfg_cache = {}


def _load_dzh_cfg(filename):
    """加载 config 目录下的 JSON 配置（带缓存，失败返回空 dict）。"""
    if filename not in _dzh_cfg_cache:
        path = _DZH_CFG_DIR / filename
        try:
            with open(path, "r", encoding="utf-8") as f:
                _dzh_cfg_cache[filename] = json.load(f)
        except Exception:
            _dzh_cfg_cache[filename] = {}
    return _dzh_cfg_cache[filename]


# ================================================================
# XML 原始解析（原 dzh_xml_raw.py）
# ================================================================

# ============================================================
# Cell/Flow 位标志定义已迁移至 config/attr_flag_map.json（深表驱动）
# cell200_attr_masks / cell201_attr_masks / flow_attr_masks
# ============================================================

# ============================================================
# Flow / Cell attr 编码辅助函数（正向合成 + 反向识别）
# ============================================================


def encode_attr_flags(decoded, mask_table_name):
    """通用位标志编码器：查 attr_flag_map.json 的 mask_table_name 表合成 attr 整数。

    深表驱动：mask 定义全部在 JSON，无 if/elif 分支。
    Args:
        decoded: 位标志字典 {flag_name: bool}
        mask_table_name: attr_flag_map.json 中的表名
            （cell200_attr_masks / cell201_attr_masks / flow_attr_masks）
    Returns:
        合成的 attr 整数
    """
    masks = _load_dzh_cfg("attr_flag_map.json").get(mask_table_name, {})
    result = 0
    for key, mask in masks.items():
        if decoded.get(key):
            result |= mask
    return result


# 表驱动：动作编解码单一数据源，从 filter_action_rules.json 加载映射表
_ACTION_RULES_CACHE = None
def _load_action_rules():
    global _ACTION_RULES_CACHE
    if _ACTION_RULES_CACHE is None:
        cfg_path = Path(__file__).parent / "config" / "runtime" / "filter_action_rules.json"
        try:
            with open(cfg_path, encoding="utf-8") as f:
                _ACTION_RULES_CACHE = json.load(f)
        except Exception:
            _ACTION_RULES_CACHE = {}
    return _ACTION_RULES_CACHE


def decode_action(action_int):
    """共用解码函数：从 filter_action_rules.json 加载 high_type_map/byte_type_map 与 encoding 位移掩码。"""
    if action_int is None or action_int == 0 or action_int == "":
        return None
    try:
        action_int = int(action_int)
    except (ValueError, TypeError):
        return None
    if action_int == 0:
        return None

    rules = _load_action_rules()
    enc = rules.get("encoding", {})

    high_type = (action_int >> enc.get("high_type_shift", 28)) & enc.get("high_type_mask", 0xF)
    if high_type != 0:
        param = action_int & enc.get("param_mask", 0xFFFF)
        ht_cfg = rules.get("high_type_map", {}).get(str(high_type))
        if ht_cfg:
            result = {"type": ht_cfg["type"], "param": param, "raw": action_int}
            if ht_cfg.get("unit") is not None:
                result["param_unit"] = ht_cfg["unit"]
            return result
        return {"type": "unknown", "action_type": high_type, "param": param, "raw": action_int}

    byte_type = (action_int >> enc.get("byte_type_shift", 16)) & enc.get("byte_type_mask", 0xFF)
    if byte_type != 0:
        param1 = (action_int >> enc.get("param1_shift", 8)) & enc.get("param1_mask", 0xFF)
        param2 = action_int & enc.get("param2_mask", 0xFF)
        bt_cfg = rules.get("byte_type_map", {}).get(str(byte_type))
        if bt_cfg:
            return {
                "type": bt_cfg["type"],
                "type_id": byte_type,
                "param1": param1,
                "param2": param2,
                "raw": action_int,
            }
        return {
            "type": "unknown",
            "type_id": byte_type,
            "param1": param1,
            "param2": param2,
            "raw": action_int,
        }

    return None


def _encode_action_raw(action_type, param=0):
    """共用编码函数：反向求逆 high_type_map/byte_type_map，按 encoding 位移掩码组装整数。

    重命名自 encode_action（SubTask 29.3 合并时发现重复定义，行 3565 的 encode_action(dict) 委托本函数）。
    """
    rules = _load_action_rules()
    enc = rules.get("encoding", {})
    # high_type_map 反向求逆：type → id
    for tid_str, cfg in rules.get("high_type_map", {}).items():
        if cfg.get("type") == action_type:
            tid = int(tid_str)
            param = max(0, min(param, enc.get("param_mask", 0xFFFF)))
            return (tid << enc.get("high_type_shift", 28)) | param
    # byte_type_map 反向求逆：type → id
    for btid_str, cfg in rules.get("byte_type_map", {}).items():
        if cfg.get("type") == action_type:
            btid = int(btid_str)
            param1 = max(0, min(param, enc.get("param1_mask", 0xFF)))
            return (btid << enc.get("byte_type_shift", 16)) | (param1 << enc.get("param1_shift", 8))
    return 0

# --- enter/exit 执行动作 位标志 ---
ACTION_BASE_TRADE = 0x2710                     # 基础买入/卖出指定股数
ACTION_SELL_ALL = 0x30000000                   # 卖出全部/买入金额

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_DIR = os.path.join(_THIS_DIR, "config")

def _load_begin_mode_map():
    """从 config/timing.json 的 begin_mode_map 段加载 begin 值→模式名映射。"""
    cfg_path = os.path.join(_CONFIG_DIR, "architecture", "timing.json")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        logger.warning("无法加载 %s，BEGIN_MODE_MAP 使用空配置", cfg_path, exc_info=True)
        return {}
    raw = cfg.get("begin_mode_map", {})
    return {int(k): v for k, v in raw.items()}


BEGIN_MODE_MAP = _load_begin_mode_map()

# ============================================================
# DZH attrtext 市场/板块映射配置（配置表驱动）
# ============================================================

_DZH_MARKET_MAPPINGS = None
_DZH_MARKET_PATTERN_MAP = {}


def _load_dzh_market_mappings():
    """加载 config/dzh_market_mappings.json 并缓存 compiled 正则。"""
    global _DZH_MARKET_MAPPINGS, _DZH_MARKET_PATTERN_MAP
    if _DZH_MARKET_MAPPINGS is not None:
        return _DZH_MARKET_MAPPINGS

    cfg_path = os.path.join(_CONFIG_DIR, "data", "dzh_market_mappings.json")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        logger.warning("无法加载 %s，attrtext 映射将使用空配置", cfg_path, exc_info=True)
        cfg = {}

    mappings = cfg.get("mappings", [])
    _DZH_MARKET_MAPPINGS = mappings
    _DZH_MARKET_PATTERN_MAP = {}
    for idx, m in enumerate(mappings):
        pattern = m.get("pattern")
        if not pattern:
            continue
        try:
            _DZH_MARKET_PATTERN_MAP[idx] = re.compile(pattern)
        except Exception as e:
            logger.warning("dzh_market_mappings.json 中正则编译失败 %r: %s", pattern, e)
    return _DZH_MARKET_MAPPINGS


def load_dzh_market_mappings():
    """返回 DZH attrtext 映射表原始条目列表。"""
    return list(_load_dzh_market_mappings())


def _lookup_market_mapping(entry):
    """根据 attrtext 单条字符串查找匹配的配置映射条目。"""
    mappings = _load_dzh_market_mappings()
    for idx, m in enumerate(mappings):
        pat = _DZH_MARKET_PATTERN_MAP.get(idx)
        if pat and pat.match(entry):
            return m
    return None


# 备选池 attrtext 条目类型正则模式
_RE_STOCK_DIRECT = re.compile(r'^(SH|SZ)\d{6}$')          # 个股: SH600000
_RE_MARKET = re.compile(r'^(SH|SZ)#.+$')                 # 市场: SH#上证A股
_RE_BLK_GROUP = re.compile(r'^BLK-自选股\d+$')            # 自选组: BLK-自选股1
_RE_BLK_CONCEPT = re.compile(r'^BLK-概念\S+\d{7}$')       # 概念板块: BLK-概念新材料0700008
_RE_BLK_INDUSTRY = re.compile(r'^BLK-(?!自选股|概念)[^\d]+\d{7}$')  # 行业板块: BLK-农业0110324
_RE_BLK_CLASSIC = re.compile(r'^BLK-(?!自选股|概念)[^\d]+$')       # 行业经典: BLK-证监板块 (无数字ID)

# panel.js sector_tree 写入的格式：BLK-{分类标签}{板块名}（无数字ID）
# 例如 BLK-概念锂电池, BLK-自定义板块0722JD, BLK-行业银行
# 必须在 _RE_BLK_CONCEPT/_RE_BLK_INDUSTRY 之后匹配，以保留旧 DZH 格式（带7位ID）的解析。
_RE_BLK_LABELED = re.compile(r'^BLK-(概念|行业|指数|风格|地区|自定义板块|自选股)(.+)$')
_BLK_LABEL_TO_CATEGORY = {
    '概念': 'concept',
    '行业': 'industry',
    '指数': 'index',
    '风格': 'style',
    '地区': 'region',
    '自定义板块': 'custom',
    '自选股': 'favorite',
}
_BLK_LABEL_TO_TYPE = {
    '概念': 'concept_sector',
    '行业': 'industry_sector',
    '指数': 'sector',
    '风格': 'sector',
    '地区': 'sector',
    '自定义板块': 'sector',
    '自选股': 'group',
}
# sel_type → parse_attrtext_triple 结果 dict key（消除 4 路 if/elif，默认 classic_sectors）
_SEL_TYPE_TO_KEY = {
    'concept_sector': 'concept_sectors',
    'industry_sector': 'industry_sectors',
    'group': 'groups',
}

# reload 模式特殊值（从 dzh_reload_schedule.json 派生，单一数据源）
# 加载 DZH 备选池 reload 调度配置（fail-fast：配置缺失时抛出异常，禁止回退硬编码）
_RELOAD_CFG_PATH = os.path.join(_CONFIG_DIR, "runtime", "dzh_reload_schedule.json")
try:
    with open(_RELOAD_CFG_PATH, encoding="utf-8") as _rf:
        _DZH_RELOAD_SCHEDULE = json.load(_rf)
except (OSError, json.JSONDecodeError) as _ex:
    raise RuntimeError(
        f"无法加载配置表 {_RELOAD_CFG_PATH}: {_ex}（fail-fast：禁止回退硬编码 reload 配置）"
    ) from _ex

# 从配置表派生魔数常量（单一数据源，消除硬编码）
_RELOAD_MAGIC_NUMBERS = _DZH_RELOAD_SCHEDULE.get("magic_numbers", {})
RELOAD_NEVER = _RELOAD_MAGIC_NUMBERS.get("RELOAD_NEVER")
RELOAD_ON_FILE_LOAD = _RELOAD_MAGIC_NUMBERS.get("RELOAD_ON_FILE_LOAD")
RELOAD_ON_STARTUP = _RELOAD_MAGIC_NUMBERS.get("RELOAD_ON_STARTUP")

# 预构建查表索引：magic_value → list of mode names（消除 decode_reload_mode 的 if/elif 分支）
_RELOAD_MAGIC_TO_MODES: Dict[int, List[str]] = {}
for _mode_name, _mode_cfg in _DZH_RELOAD_SCHEDULE.get("modes", {}).items():
    _magic = _mode_cfg.get("magic")
    if isinstance(_magic, int):
        _RELOAD_MAGIC_TO_MODES.setdefault(_magic, []).append(_mode_name)

_RELOAD_DEFAULT_MODE = _DZH_RELOAD_SCHEDULE.get("default_mode", "on_startup")

_FUNC_TYPES = {200, 201, 202, 4, 203}

BEGINT_HHMMSS_MODES = {3, 4, 7}

_formula_patterns_cache = None





def is_tdx_format(xml_content):
    """检测 XML 内容是否为通达信 (TDX) 股票池格式。

    TDX 格式特征：
      - XML 声明 encoding 为 GB2312/GBK/GB18030（DZH 通常为 UTF-8）
      - 根结构为 <root><pool><cells>...（DZH 为 <root><cell>... 直接子元素）
      - 包含 TDX 特有属性：spinfo/psatt/func 的 TDX 特有字段

    Args:
        xml_content: bytes 或 str，原始 XML 内容。

    Returns:
        bool: True 表示检测为 TDX 格式。
    """
    text = _decode_xml_content(xml_content) if isinstance(xml_content, (bytes, str)) else ""
    if not text:
        return False

    # 快速特征1: XML 声明中的编码
    if 'encoding="GB2312"' in text or 'encoding="gb2312"' in text:
        return True
    if 'encoding="GBK"' in text or 'encoding="gbk"' in text:
        return True

    # 快速特征2: TDX 特有结构标记
    # TDX 有 <root><pool> 包裹，DZH 的 pool 属性直接在 root 上
    if '<pool' in text and 'backcolor="' in text and '<cells>' in text:
        # 进一步确认是 TDX 的 pool（有 backcolor 属性且包含 cells 子元素）
        try:
            root = ET.fromstring(text)
            pool_elem = root.find("pool")
            if pool_elem is not None and pool_elem.find("cells") is not None:
                return True
        except Exception:
            pass

    # 快速特征3: TDX 特有元素/属性
    tdx_markers = ["spinfo", "psatt", 'nset="', 'ndeltype=', 'accode="']
    found = sum(1 for m in tdx_markers if m in text)
    if found >= 2:
        return True

    return False


# attr 解码分派表（type_key → 解码器配置：mask_table 走 attr_flag_map.json，model 走 schemas）
_ATTR_DECODERS = {
    "200": {"mask_table": "cell200_attr_masks"},
    "201": {"model": "Cell201AttrBitsModel"},
    "flow": {"model": "FlowAttrBitsModel"},
}


def decode_attr_flags(attr_int, type_key):
    """通用 attr 解码器：按 type_key 解码位标志为 dict 并附加 raw。

    统一 _decode_type200_attr/_decode_type201_attr/_decode_flow_attr 实现，
    数据源保持不变（200 走 attr_flag_map.json，201/flow 走 schemas Model）。
    """
    cfg = _ATTR_DECODERS.get(type_key, {})
    if "mask_table" in cfg:
        masks = _load_dzh_cfg("attr_flag_map.json").get(cfg["mask_table"], {})
        result = {k: bool(attr_int & v) for k, v in masks.items()}
    else:
        _model_name = cfg["model"]
        from core.schemas import Cell201AttrBitsModel, FlowAttrBitsModel
        _model_cls = {"Cell201AttrBitsModel": Cell201AttrBitsModel,
                      "FlowAttrBitsModel": FlowAttrBitsModel}[_model_name]
        result = _model_cls.from_int(attr_int).model_dump(exclude={"raw_attr"})
    result["raw"] = attr_int
    return result


def _decode_type200_attr(attr_int):
    return decode_attr_flags(attr_int, "200")


def _decode_type201_attr(attr_int):
    return decode_attr_flags(attr_int, "201")


def _decode_flow_attr(attr_int):
    return decode_attr_flags(attr_int, "flow")


def _decode_enter_exit_action(action_val):
    if action_val is None or action_val == 0 or action_val == "":
        return None
    return decode_action(action_val)


def _parse_time_str(t):
    if t is None:
        return None
    if isinstance(t, int):
        if t <= 0 or t >= _NEVER_EXPIRE:
            return None
        s = str(t).zfill(6)
    elif isinstance(t, str):
        if not t.isdigit() or t == "0":
            return None
        s = t.zfill(6)
    else:
        return None
    return "%s:%s:%s" % (s[:2], s[2:4], s[4:6])


def _parse_markets(attrtext):
    """解析 attrtext 为结构化选择列表。

    Returns:
        tuple: (markets_list, raw_attrtext)
        - markets_list: 内部market id列表 (如 ["sh_a", "sz_a"])，向后兼容
        - raw_attrtext: 原始字符串
    """
    # 保持原有默认行为
    if not attrtext:
        return ["sh_a", "sz_a"], ""

    entries = re.split(r"[\t\s]+", attrtext.strip())
    markets = []
    for entry in entries:
        if not entry:
            continue
        mapping = _lookup_market_mapping(entry)
        if mapping:
            codes = mapping.get("default_codes") or []
            if codes:
                markets.append(codes[0])
            elif mapping.get("type") == "market":
                markets.append(mapping.get("name", "unknown_market"))
            else:
                markets.append(f"{mapping['type']}:{entry}")
        elif _RE_MARKET.match(entry):
            if "上证A" in entry or "SH" in entry.upper():
                markets.append("sh_a")
            elif "深证A" in entry or "中小企业" in entry:
                markets.append("sz_a")
            elif "创业板" in entry:
                markets.append("gem")
            else:
                markets.append("unknown_market")
        elif _RE_STOCK_DIRECT.match(entry):
            markets.append(f"stock:{entry}")
        elif _RE_BLK_GROUP.match(entry):
            markets.append(f"group:{entry}")
        elif _RE_BLK_CONCEPT.match(entry):
            markets.append(f"concept_sector:{entry}")
        elif _RE_BLK_INDUSTRY.match(entry):
            markets.append(f"industry_sector:{entry}")
        elif _RE_BLK_LABELED.match(entry):
            # panel.js 格式 BLK-{分类标签}{板块名}（无数字ID）
            m = _RE_BLK_LABELED.match(entry)
            label_prefix = m.group(1)
            sel_type = _BLK_LABEL_TO_TYPE.get(label_prefix, 'sector')
            markets.append(f"{sel_type}:{entry}")
        elif _RE_BLK_CLASSIC.match(entry):
            markets.append(f"classic_sector:{entry}")
        elif "板块" in entry:
            markets.append("sector_index")
        elif "概念" in entry:
            markets.append("hot_concept")
        else:
            if "上证A" in entry or "SH" in entry.upper():
                markets.append("sh_a")
            elif "深证A" in entry or "中小企业" in entry:
                markets.append("sz_a")
            elif "创业板" in entry:
                markets.append("gem")
            else:
                markets.append(f"raw:{entry}")

    if not markets:
        markets = ["sh_a", "sz_a"]
    return markets, attrtext


def parse_attrtext_triple(attrtext_str):
    """解析 attrtext 为结构化分类列表。

    Returns:
        dict: {"markets": [], "sectors": [], "stocks": [], "groups": [],
               "concept_sectors": [], "industry_sectors": [], "classic_sectors": [], "raw": []}
    """
    result = {
        "markets": [], "sectors": [], "stocks": [], "groups": [],
        "concept_sectors": [], "industry_sectors": [], "classic_sectors": [], "raw": []
    }
    if not attrtext_str:
        return result

    entries = attrtext_str.split("\t")
    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue
        if _RE_MARKET.match(entry):
            mapping = _lookup_market_mapping(entry)
            if mapping:
                codes = mapping.get("default_codes") or []
                result["markets"].append({"code": entry, "mapped": codes[0] if codes else None})
            else:
                result["markets"].append({"code": entry, "mapped": None})
        elif re.match(r'^B\$#', entry):
            result["sectors"].append(entry)
        elif _RE_STOCK_DIRECT.match(entry):
            result["stocks"].append(entry)
        elif _RE_BLK_GROUP.match(entry):
            result["groups"].append(entry)
        elif _RE_BLK_CONCEPT.match(entry):
            result["concept_sectors"].append(entry)
        elif _RE_BLK_INDUSTRY.match(entry):
            result["industry_sectors"].append(entry)
        elif _RE_BLK_LABELED.match(entry):
            # panel.js 格式 BLK-{分类标签}{板块名}（无数字ID）
            m = _RE_BLK_LABELED.match(entry)
            label_prefix = m.group(1)
            sel_type = _BLK_LABEL_TO_TYPE.get(label_prefix, 'sector')
            result_key = _SEL_TYPE_TO_KEY.get(sel_type, 'classic_sectors')
            result[result_key].append(entry)
        elif _RE_BLK_CLASSIC.match(entry):
            result["classic_sectors"].append(entry)
        elif re.match(r'^\d{6}$', entry) or re.match(r'^\d{6}\.\w{2}$', entry):
            result["stocks"].append(entry)
        else:
            result["raw"].append(entry)

    return result


def _extract_reload_param(node_context):
    """从节点上下文中提取 daily_time 参数（兼容 reload_param / daily_time 字段）。"""
    if not isinstance(node_context, dict):
        return None
    for key in _DZH_RELOAD_SCHEDULE.get("disambiguation", {}).get("fields", ["reload_param", "daily_time"]):
        val = node_context.get(key)
        if val is not None and val != "":
            return val
    return None


def decode_reload_mode(reload_value, node_context=None):
    """解码备选池 reload 整数值为语义化重载模式（查表驱动，无 if/elif 硬编码分支）。

    基于 dzh_reload_schedule.json 的 modes/magic_numbers/disambiguation 配置：
      - 2147483647 (0x7FFFFFFF) = 不重载(never)
      - 2147483646 (0x7FFFFFFE) = 文件载入时(on_file_load)
      - -57387 (0xFFFF4C5D)     = 每次启动(on_startup) 或 每天指定时间(daily_time)
      - 正整数                  = 每隔N秒(interval)，值为秒数
      - 0                       = 默认(on_startup)

    歧义消解：-57387 同时对应 on_startup 与 daily_time，若 node_context 中存在
    reload_param / daily_time 字段，则解码为 daily_time 并返回 param；否则为 on_startup。

    Args:
        reload_value: reload 属性的整数值（字符串或int）
        node_context: 可选节点参数字典，用于区分 on_startup/daily_time

    Returns:
        dict: {"mode": str, "param": int|str|None}
              mode ∈ {"on_startup", "daily_time", "never", "interval", "on_file_load"}
    """
    try:
        val = int(reload_value)
    except (TypeError, ValueError):
        val = 0

    # 查表：magic_value → 匹配的 mode 列表
    matched = _RELOAD_MAGIC_TO_MODES.get(val, [])

    if matched:
        if len(matched) == 1:
            return {"mode": matched[0], "param": None}
        # 歧义消解：多模式匹配同一魔数（如 -57387 → on_startup/daily_time）
        param = _extract_reload_param(node_context)
        if param is not None and "daily_time" in matched:
            return {"mode": "daily_time", "param": param}
        # 无参数字段或 daily_time 不在匹配列表：返回 default_mode（若在匹配列表）或首个
        if _RELOAD_DEFAULT_MODE in matched:
            return {"mode": _RELOAD_DEFAULT_MODE, "param": None}
        return {"mode": matched[0], "param": None}

    # 正整数 → interval 模式
    if val > 0:
        return {"mode": "interval", "param": val}

    # 0 或无匹配 → default_mode
    return {"mode": _RELOAD_DEFAULT_MODE, "param": None}


def encode_reload_mode(mode, param=None):
    """将语义化重载模式编码为 reload 整数值（查表驱动，无 if/elif 硬编码分支）。

    Args:
        mode: 重载模式字符串
        param: 模式参数（interval模式=秒数，daily_time模式=HHMMSS）

    Returns:
        int: 可写入XML的reload属性值
    """
    modes = _DZH_RELOAD_SCHEDULE.get("modes", {})
    mode_cfg = modes.get(mode)

    if mode_cfg is None:
        return 0  # 未知模式

    if mode == "interval":
        if param is None:
            return 0
        try:
            return int(param)
        except (TypeError, ValueError):
            return 0

    magic = mode_cfg.get("magic")
    if isinstance(magic, int):
        return magic

    return 0  # magic 非整数（如 "positive_integer"）兜底


def parse_attrtext_selections(attrtext_str):
    """解析 attrtext 为前端可用的结构化 selections 列表。

    每个selection包含 type/label/code 字段，供前端渲染使用。

    Returns:
        list[dict]: [{"type": str, "label": str, "code": str}, ...]
          type ∈ {"stock", "market", "group", "concept_sector", "industry_sector",
                   "classic_sector", "sector", "raw"}
    """
    selections = []
    if not attrtext_str:
        return selections

    entries = attrtext_str.split("\t")
    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue

        if _RE_STOCK_DIRECT.match(entry):
            m = _RE_STOCK_DIRECT.match(entry)
            selections.append({
                "type": "stock",
                "label": entry,  # e.g., "SH600000"
                "code": entry,
            })
        elif _RE_MARKET.match(entry):
            # 提取 # 后面的名称部分
            label = entry.split("#", 1)[-1] if "#" in entry else entry
            selections.append({
                "type": "market",
                "label": label,
                "code": entry,
            })
        elif _RE_BLK_GROUP.match(entry):
            label = entry[4:]  # 去掉 "BLK-" 前缀
            selections.append({
                "type": "group",
                "label": label,
                "code": entry,
            })
        elif _RE_BLK_CONCEPT.match(entry):
            # BLK-概念{Name}{ID} → label=Name, 去掉ID
            name_part = entry[6:-7]  # 去掉 "BLK-概念" 和末尾7位ID
            selections.append({
                "type": "concept_sector",
                "label": name_part,
                "code": entry,
            })
        elif _RE_BLK_INDUSTRY.match(entry):
            # BLK-{Name}{ID} → label=Name
            m = _RE_BLK_INDUSTRY.match(entry)
            name_part = entry[4:-7]  # 去掉 "BLK-" 和末尾7位ID
            selections.append({
                "type": "industry_sector",
                "label": name_part,
                "code": entry,
            })
        elif _RE_BLK_LABELED.match(entry):
            # panel.js 写入的格式：BLK-{分类标签}{板块名}（无数字ID）
            # 例如 BLK-概念锂电池 → type=concept_sector, label=锂电池, category=concept
            m = _RE_BLK_LABELED.match(entry)
            label_prefix = m.group(1)
            sector_name = m.group(2)
            category = _BLK_LABEL_TO_CATEGORY.get(label_prefix, '')
            sel_type = _BLK_LABEL_TO_TYPE.get(label_prefix, 'sector')
            sel = {
                "type": sel_type,
                "label": sector_name,
                "code": entry,
            }
            if category:
                sel["category"] = category
                sel["sector_name"] = sector_name
            selections.append(sel)
        elif _RE_BLK_CLASSIC.match(entry):
            label = entry[4:]
            selections.append({
                "type": "classic_sector",
                "label": label,
                "code": entry,
            })
        elif re.match(r'^B\$#', entry):
            label = entry.split("#", 1)[-1] if "#" in entry else entry
            selections.append({
                "type": "sector",
                "label": label,
                "code": entry,
            })
        else:
            selections.append({
                "type": "raw",
                "label": entry,
                "code": entry,
            })

    return selections


def build_attrtext_from_selections(selections):
    """从结构化 selections 列表重建 attrtext 字符串。

    是 parse_attrtext_selections() 的逆函数。

    Args:
        selections: list[dict], 每项含 code 字段

    Returns:
        str: Tab分隔的 attrtext 字符串
    """
    if not selections:
        return ""
    codes = [s.get("code", "") for s in selections if s.get("code")]
    return "\t".join(codes)


def _parse_col_list(col_str):
    if not col_str:
        return [2, -1, -2, -3, 7, 14, 8, 10, 17, 45]
    try:
        parts = [int(x.strip()) for x in col_str.split(",") if x.strip()]
        return parts if parts else [2, -1, -2, -3, 7, 14, 8, 10, 17, 45]
    except (ValueError, TypeError):
        return [2, -1, -2, -3, 7, 14, 8, 10, 17, 45]


def _parse_ana_children(cell_elem):
    anas = []
    for ana in cell_elem.findall("ana"):
        anas.append({
            "label": ana.get("label", ""),
            "t": ana.get("t", ""),
            "p": ana.get("p", ""),
        })
    return anas


def _parse_tradeattr(cell_elem):
    ta = cell_elem.find("tradeattr")
    if ta is None:
        return None
    result = {}
    for key in [
        "accountno", "entertradetype", "enterrate", "enterbuytype",
        "enterbuyvalue", "enterbuypricetype", "enterselltype",
        "entersellvalue", "entersellpricetype",
        "leavetradetype", "leaverate", "leavebuytype",
        "leavebuyvalue", "leavebuypricetype",
        "leaveselltype", "leavesellvalue", "leavesellpricetype",
        "buycontrolnbhs", "buycontrolmbsc",
    ]:
        val = ta.get(key)
        if val is not None:
            result[key] = val
    return result if result else None


def _parse_width_list(width_str):
    if not width_str:
        return None
    try:
        parts = [int(x.strip()) for x in width_str.split(",") if x.strip()]
        return parts if parts else None
    except (ValueError, TypeError):
        return None


def _detect_topology_mode(nodes, edges):
    # 表驱动：从 config/topology.json 加载拓扑模式配置
    try:
        _topo_cfg_path = os.path.join(os.path.dirname(__file__), "config", "topology.json")
        with open(_topo_cfg_path, encoding="utf-8") as _tf:
            _topo_cfg = json.load(_tf)
    except Exception:
        _topo_cfg = {"modes": {}, "detection_priority": [], "fallback_mode": "mixed"}

    in_deg = {}
    out_deg = {}
    node_ids = {n["id"] for n in nodes}
    node_type_map = {}
    for n in nodes:
        nid = n["id"]
        in_deg[nid] = 0
        out_deg[nid] = 0
        # 收集节点类型用于多指标并行判断
        nt = n.get("type", n.get("cell_type", n.get("dzh_cell_type", "")))
        node_type_map[nid] = str(nt) if nt else ""
    for e in edges:
        src = e["source"]["node_id"]
        tgt = e["target"]["node_id"]
        if src in out_deg:
            out_deg[src] += 1
        if tgt in in_deg:
            in_deg[tgt] += 1

    adj = {}
    for nid in node_ids:
        adj[nid] = []
    for e in edges:
        src = e["source"]["node_id"]
        tgt = e["target"]["node_id"]
        if src in adj and tgt in node_ids:
            adj[src].append(tgt)

    def _has_cycle():
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {nid: WHITE for nid in node_ids}

        def dfs(u):
            color[u] = GRAY
            for v in adj.get(u, []):
                if v not in color:
                    continue
                if color[v] == GRAY:
                    return True
                if color[v] == WHITE and dfs(v):
                    return True
            color[u] = BLACK
            return False

        for nid in node_ids:
            if color[nid] == WHITE:
                if dfs(nid):
                    return True
        return False

    def _max_chain_depth():
        """计算最长链式路径深度（串行链式/多层漏斗判断用）。含环路保护。"""
        memo = {}
        def _depth(nid, visiting=None):
            if nid in memo:
                return memo[nid]
            visiting = visiting or set()
            if nid in visiting:
                return 0  # 环路保护：遇到回边返回0，避免无限递归
            visiting = visiting | {nid}
            children = adj.get(nid, [])
            if not children:
                memo[nid] = 1
            else:
                memo[nid] = 1 + max((_depth(c, visiting) for c in children if c in node_ids), default=0)
            return memo[nid]
        return max((_depth(nid) for nid in node_ids), default=0)

    has_cycle = _has_cycle()
    sources = [n for n in node_ids if in_deg.get(n, 0) == 0]
    sinks = [n for n in node_ids if out_deg.get(n, 0) == 0]
    max_out = max(out_deg.values()) if out_deg else 0
    max_in = max(in_deg.values()) if in_deg else 0
    chain_depth = _max_chain_depth()

    # 条件节点类型集合（用于多指标并行判断）
    _condition_types = {"3", "201", "condition", "transfer_condition"}

    # 表驱动：按优先级检测拓扑模式（含循环反馈/双源并行等DZH模式）
    for mode_key in _topo_cfg.get("detection_priority", []):
        rules = _topo_cfg.get("modes", {}).get(mode_key, {}).get("detect_rules", {})
        if not rules:
            continue
        ok = True
        if ok and rules.get("has_cycle") and not has_cycle:
            ok = False
        if ok and "source_count" in rules and len(sources) != rules["source_count"]:
            ok = False
        if ok and "source_count_gte" in rules and len(sources) < rules["source_count_gte"]:
            ok = False
        if ok and "sink_count" in rules and len(sinks) != rules["sink_count"]:
            ok = False
        if ok and "max_out" in rules and max_out != rules["max_out"]:
            ok = False
        if ok and "max_in" in rules and max_in != rules["max_in"]:
            ok = False
        if ok and "max_out_gte" in rules and max_out < rules["max_out_gte"]:
            ok = False
        if ok and "max_in_gte" in rules and max_in < rules["max_in_gte"]:
            ok = False
        if ok and "min_nodes" in rules and len(node_ids) < rules["min_nodes"]:
            ok = False
        if ok and "chain_depth_gte" in rules and chain_depth < rules["chain_depth_gte"]:
            ok = False
        # 多指标并行：扇出目标需为条件节点
        if ok and rules.get("target_is_condition"):
            cond_targets = 0
            for e in edges:
                tgt = e["target"]["node_id"]
                if tgt in node_type_map and node_type_map[tgt] in _condition_types:
                    cond_targets += 1
            if cond_targets < 2:
                ok = False
        if ok:
            return mode_key

    return _topo_cfg.get("fallback_mode", "mixed")


def _propagate_stocks_to_downstream_pools(nodes, edges):
    adj = {}
    node_map = {}
    for n in nodes:
        nid = n["id"]
        node_map[nid] = n
        adj[nid] = []
    for e in edges:
        src = e["source"]["node_id"]
        tgt = e["target"]["node_id"]
        if src in adj:
            adj[src].append(tgt)

    source_nodes = []
    for n in nodes:
        stocks = n.get("params", {}).get("stocks", [])
        if stocks:
            source_nodes.append(n["id"])

    POOL_TYPES = {"stock_state_pool", "result_pool"}
    propagated = set()
    for src_id in source_nodes:
        src_node = node_map.get(src_id)
        if not src_node:
            continue
        src_stocks = src_node.get("params", {}).get("stocks", [])
        if not src_stocks:
            continue
        visited = set()
        queue = [tgt_id for tgt_id in adj.get(src_id, []) if tgt_id not in visited]
        while queue:
            cur_id = queue.pop(0)
            if cur_id in visited:
                continue
            visited.add(cur_id)
            cur_node = node_map.get(cur_id)
            if cur_node and cur_node.get("type") in POOL_TYPES:
                existing = cur_node.get("params", {}).get("stocks", [])
                if not existing:
                    cur_node["params"]["stocks"] = list(src_stocks)
                    propagated.add(cur_id)
            for next_id in adj.get(cur_id, []):
                if next_id not in visited:
                    queue.append(next_id)

    return propagated


# ============================================================
# Cell 类型 schema 驱动解析/构建（表驱动：消除 if/elif ct 链）
# ============================================================

_DZH_CELL_SCHEMA = None


def _load_dzh_cell_schema():
    """加载 dzh_cell_type_schema.json（带缓存）。"""
    global _DZH_CELL_SCHEMA
    if _DZH_CELL_SCHEMA is None:
        _DZH_CELL_SCHEMA = _load_dzh_cfg("dzh_cell_type_schema.json")
    return _DZH_CELL_SCHEMA


def _resolve_mapped_param(spec, ctx):
    """按 param_mapping spec 从上下文解析参数值。

    spec 格式：
        {"src": "cell_id"} → ctx["cell_id"]
        {"src": "label"} → ctx["label"]
        {"src": "position"} → ctx["position"]
        {"src": "params", "key": "attr", "transform": "safe_int", "default": 0}
            → safe_int(ctx["params"].get("attr"), 0)
        {"src": "params", "key": "inditype", "default": "indi"}
            → ctx["params"].get("inditype", "indi")
    """
    src = spec.get("src")
    if src in ("cell_id", "label", "position"):
        return ctx.get(src)
    if src == "params":
        key = spec.get("key")
        params = ctx.get("params", {})
        transform = spec.get("transform")
        if transform == "safe_int":
            return safe_int(params.get(key), spec.get("default", 0))
        if "default" in spec:
            return params.get(key, spec["default"])
        return params.get(key)
    return None


# ---- Cell 解析器（parse_dzh_xml 用：rc → node dict）----

def _parse_cell_default(ct, rc, entry, ctx):
    """默认 cell 解析器：按 schema 的 params_fields/params_extra 构建 node dict。"""
    params = {}
    for field_name in entry.get("params_fields", []):
        params[field_name] = rc.get(field_name)
    for param_name, spec in entry.get("params_extra", {}).items():
        if "expr" in spec:
            params[param_name] = eval(spec["expr"], {"attr_int": ctx["attr_int"], "bool": bool})
        elif "src" in spec:
            params[param_name] = ctx.get(spec["src"])
        elif "const" in spec:
            params[param_name] = spec["const"]

    label = ctx["label"]
    if entry.get("label_mode") == "orig_text":
        _text_val = rc.get("_orig_text")
        label = "cell_%s" % ctx["cid"] if _text_val is None else _text_val

    pos = ctx["pos"]
    if entry.get("position_mode") == "func_pos":
        position = {"x": pos["x"], "y": pos["y"], "width": pos.get("width", 0), "height": pos.get("height", 0)}
    else:
        position = pos

    node = {
        "id": ctx["new_id"],
        "type": entry["node_type"],
        "label": label,
        "params": params,
        "position": position,
        "dzh_cell_type": ct,
    }
    for k, v in entry.get("node_extra", {}).items():
        node[k] = v
    return node


def _parse_cell_condition(ct, rc, entry, ctx):
    """201 transfer_condition 解析器：含 formula 解码。"""
    indi_raw = rc.get("indi")
    formula_decoded = ""
    if indi_raw and indi_raw != "0;":
        # 变更 P4: 从 converters_common 导入（_decode_formula 单一来源）
        from converters_common import decode_formula as _decode_indi
        try:
            formula_decoded = _decode_indi(indi_raw, ctx.get("ency", 0))
        except Exception:
            pass
    attr_int = ctx["attr_int"]
    params = {
        "inditype": rc.get("inditype"),
        "crc": rc.get("crc"),
        "indi": rc.get("indi"),
        "formula_decoded": formula_decoded,
        "ency": ctx.get("ency", 0),
        "sorttype": rc.get("sorttype"),
        "indiparam": rc.get("indiparam"),
        "attr": rc["attr"],
        "attr_int": attr_int,
        "attr_decoded": _decode_type201_attr(attr_int),
        "clr": rc["clr"],
        "text": rc["text"],
        "_orig_text": rc.get("_orig_text"),
        "_present_attrs": rc.get("_present_attrs", set()),
    }
    return {
        "id": ctx["new_id"],
        "type": entry["node_type"],
        "label": ctx["label"],
        "params": params,
        "position": ctx["pos"],
        "dzh_cell_type": ct,
    }


def _parse_cell_pool(ct, rc, entry, ctx):
    """200/203 pool 解析器：复杂参数提取。"""
    pos = ctx["pos"]
    func_pos = {"x": pos["x"], "y": pos["y"], "width": pos.get("width", 0), "height": pos.get("height", 0)}
    attr_int = ctx["attr_int"]
    attr_decoded = _decode_type200_attr(attr_int)
    is_203 = entry.get("pool_variant") == "203"
    params = {
        "hold_sec": safe_int(rc.get("hold"), DEFAULT_HOLD_SEC),
        "col_list": _parse_col_list(rc.get("col")),
        "width_list": _parse_width_list(rc.get("width")),
        "attr": rc["attr"],
        "attr_int": attr_int,
        "dzh_attr": attr_decoded,
        "clr": rc["clr"],
        "histana": rc.get("histana"),
        "wizd": rc.get("wizd"),
        "deltype": rc.get("deltype"),
        "endtime": rc.get("endtime"),
        "delstocktype": rc.get("delstocktype"),
        "staattr": rc.get("staattr"),
        "enter": safe_int(rc["enter"], 0),
        "exit": safe_int(rc["exit"], 0),
        "stocknum": rc.get("stocknum"),
        "tmpl": rc.get("tmpl"),
        "sorttype": rc.get("sorttype"),
        "intersection_status": rc.get("intersection_status"),
        "reload": rc.get("reload"),
        "lastload": rc.get("lastload"),
        "attrtext": rc.get("attrtext"),
        "stocks": rc.get("stocks") or [],
        "anas": rc.get("anas") or [],
        "tradeattr": rc.get("tradeattr"),
        "_present_attrs": rc.get("_present_attrs", set()),
        "text": rc["text"],
        "_orig_text": rc.get("_orig_text"),
    }
    if is_203:
        params["result_type"] = rc.get("result_type")

    enter_action = _decode_enter_exit_action(rc["enter"])
    if enter_action is not None:
        params["enter_action"] = enter_action
    exit_action = _decode_enter_exit_action(rc["exit"])
    if exit_action is not None:
        params["exit_action"] = exit_action

    return {
        "id": ctx["new_id"],
        "type": entry["node_type"],
        "label": ctx["label"],
        "params": params,
        "position": func_pos,
        "dzh_cell_type": ct,
    }


def _parse_cell_market(ct, rc, entry, ctx):
    """202 market_source 解析器：含 markets/sectors 解析。"""
    pos = ctx["pos"]
    func_pos = {"x": pos["x"], "y": pos["y"], "width": pos.get("width", 0), "height": pos.get("height", 0)}
    attr_int = ctx["attr_int"]
    label = ctx["label"]
    _attrtext_raw = rc.get("attrtext", "")
    if _attrtext_raw:
        _attrtext_raw = _attrtext_raw.replace("__DZH_TAB__", "\t")
    markets, raw_attrtext = _parse_markets(_attrtext_raw)
    triple = parse_attrtext_triple(_attrtext_raw)
    params = {
        "markets": markets,
        "raw_attrtext": raw_attrtext,
        "sectors": triple.get("sectors", []),
        "stocks_raw": triple.get("stocks", []),
        "name": label,
        "reload_sec": safe_int(rc.get("reload"), 0),
        "lastload": safe_int(rc.get("lastload"), 0),
        "clr": rc["clr"],
        "attr_int": attr_int,
        "attr": attr_int,
        "text": rc["text"],
        "_orig_text": rc.get("_orig_text"),
        "_present_attrs": rc.get("_present_attrs", set()),
    }
    return {
        "id": ctx["new_id"],
        "type": entry["node_type"],
        "label": label,
        "params": params,
        "position": func_pos,
        "dzh_cell_type": ct,
    }


_DZH_CELL_PARSERS = {
    "_parse_cell_default": _parse_cell_default,
    "_parse_cell_condition": _parse_cell_condition,
    "_parse_cell_pool": _parse_cell_pool,
    "_parse_cell_market": _parse_cell_market,
}


def _parse_cell_element(ct, rc, cid, new_id, pos, label, attr_int, ency=0):
    """通用 cell 解析器：按 schema 分派，构建 node dict。无 if/elif ct 分支。"""
    schema = _load_dzh_cell_schema()
    entry = schema.get("cell_types", {}).get(str(ct))
    if entry is None:
        return None
    ctx = {"cid": cid, "new_id": new_id, "pos": pos, "label": label, "attr_int": attr_int, "ency": ency}
    parser_name = entry.get("parser", "_parse_cell_default")
    parser = _DZH_CELL_PARSERS.get(parser_name, _parse_cell_default)
    return parser(ct, rc, entry, ctx)


# ---- CellModel 工厂（_convert_node_to_cell 用：node → CellModel）----

def _build_cell_base(node, models):
    """提取 node 的 params 与 PositionModel（_build_cell_default/pool/market 共享）。"""
    params = node.get("params", {})
    pos_data = node.get("position", {})
    position = models.PositionModel(**{k: safe_int(v, 0) for k, v in pos_data.items()
                                       if k in models.PositionModel.model_fields})
    return params, position


def _build_cell_default(node, entry, models):
    """默认 CellModel 工厂：按 schema 的 param_mapping 实例化。"""
    params, position = _build_cell_base(node, models)
    ctx = {
        "cell_id": node.get("id", ""),
        "label": node.get("label", ""),
        "position": position,
        "params": params,
    }
    mapped = {}
    for model_param, spec in entry.get("param_mapping", {}).items():
        mapped[model_param] = _resolve_mapped_param(spec, ctx)
    model_class = getattr(models, entry["model_class"])
    return model_class(**mapped)


def _build_cell_pool(node, entry, models):
    """200/203 pool CellModel 工厂：共享复杂构建逻辑。"""
    params, position = _build_cell_base(node, models)
    label = node.get("label", "")
    cell_id = node.get("id", "")
    dzh_ct = node.get("dzh_cell_type", 0)

    attr_int = safe_int(params.get("attr_int", params.get("attr")), 0)
    attr_decoded = None
    dzh_attr = params.get("dzh_attr")
    if isinstance(dzh_attr, dict):
        attr_decoded = models.Cell200AttrBitsModel.from_int(attr_int)
    elif attr_int != 0:
        attr_decoded = models.Cell200AttrBitsModel.from_int(attr_int)

    col_list = params.get("col_list")
    col_def = ",".join(str(c) for c in col_list) if isinstance(col_list, list) else params.get("col", "2,-1,-2,-3,7,14,8,10,17,45")

    width_list = params.get("width_list")
    width_def = ",".join(str(w) for w in width_list) if isinstance(width_list, list) else None

    enter_action = None
    ea = params.get("enter_action")
    if isinstance(ea, dict):
        enter_action = models.ActionModel(
            action_type=ea.get("type", "none"),
            param=safe_int(ea.get("param"), 0),
            param_unit=ea.get("param_unit", ""),
            raw=safe_int(ea.get("raw"), 0),
        )
    exit_action = None
    xa = params.get("exit_action")
    if isinstance(xa, dict):
        exit_action = models.ActionModel(
            action_type=xa.get("type", "none"),
            param=safe_int(xa.get("param"), 0),
            param_unit=xa.get("param_unit", ""),
            raw=safe_int(xa.get("raw"), 0),
        )

    stocks_raw = params.get("stocks", [])
    stocks = []
    if isinstance(stocks_raw, list):
        for s in stocks_raw:
            stocks.append(models.StockSnapshotModel(
                label=s.get("label", ""),
                t=s.get("t", ""),
                p=s.get("p", ""),
                tid=s.get("tid"),
            ))

    tradeattr_raw = params.get("tradeattr")
    tradeattr = None
    if isinstance(tradeattr_raw, dict):
        tradeattr = models.TradeAttrModel.from_dict(tradeattr_raw)
    elif tradeattr_raw is not None:
        tradeattr = models.TradeAttrModel.from_dict(tradeattr_raw if isinstance(tradeattr_raw, dict) else {})

    common = {
        "id": cell_id,
        "attr": attr_int,
        "attr_decoded": attr_decoded,
        "position": position,
        "clr": safe_int(params.get("clr"), -1),
        "text": label,
        "hold": safe_int(params.get("hold_sec"), DEFAULT_HOLD_SEC),
        "col_def": col_def,
        "width_def": width_def,
        "histana": safe_int(params.get("histana")) if params.get("histana") is not None else None,
        "wizd": params.get("wizd"),
        "deltype": safe_int(params.get("deltype"), 0),
        "endtime": safe_int(params.get("endtime"), 0),
        "delstocktype": safe_int(params.get("delstocktype"), 0),
        "staattr": safe_int(params.get("staattr")) if params.get("staattr") is not None else None,
        "stocknum": safe_int(params.get("stocknum")) if params.get("stocknum") is not None else None,
        "tmpl": params.get("tmpl"),
        "sorttype": safe_int(params.get("sorttype")) if params.get("sorttype") is not None else None,
        "intersection_status": params.get("intersection_status"),
        "reload": safe_int(params.get("reload")) if params.get("reload") is not None else None,
        "lastload": safe_int(params.get("lastload")) if params.get("lastload") is not None else None,
        "attrtext": params.get("attrtext"),
        "enter_action": enter_action,
        "exit_action": exit_action,
        "stocks": stocks,
        "tradeattr": tradeattr,
    }

    if entry.get("pool_variant") == "203" or dzh_ct == 203 or params.get("_is_fallback_203"):
        return models.Type203CellModel(**common)
    common_200 = {k: v for k, v in common.items() if k not in ("reload", "lastload", "attrtext")}
    return models.StatePoolCellModel(**common_200)


def _build_cell_market(node, entry, models):
    """202 market_source CellModel 工厂：含 reload_mode/selections 解析。"""
    params, position = _build_cell_base(node, models)
    label = node.get("label", "")
    cell_id = node.get("id", "")

    _reload_val = params.get("reload_sec", 0)
    _reload_info = decode_reload_mode(_reload_val, node_context=params)
    _selections = parse_attrtext_selections(params.get("raw_attrtext", ""))

    return models.CandidateCellModel(
        id=cell_id,
        attr=safe_int(params.get("attr_int"), 0),
        position=position,
        clr=safe_int(params.get("clr"), -1),
        text=label,
        attrtext=params.get("raw_attrtext", ""),
        reload_sec=safe_int(params.get("reload_sec"), 0),
        lastload=safe_int(params.get("lastload"), 0),
        reload_mode=_reload_info.get("mode", "on_startup"),
        reload_param=_reload_info.get("param"),
        selections=_selections,
    )


_DZH_CELL_BUILDERS = {
    "_build_cell_default": _build_cell_default,
    "_build_cell_pool": _build_cell_pool,
    "_build_cell_market": _build_cell_market,
}

# node_type → ct 反向索引（延迟构建）
_DZH_CELL_NODE_TYPE_INDEX = None


def _lookup_cell_entry_by_node_type(node_type):
    """按 node_type 反查 schema entry（含 aliases）。"""
    global _DZH_CELL_NODE_TYPE_INDEX
    if _DZH_CELL_NODE_TYPE_INDEX is None:
        schema = _load_dzh_cell_schema()
        idx = {}
        for ct_key, entry in schema.get("cell_types", {}).items():
            nt = entry.get("node_type")
            if nt:
                idx[nt] = ct_key
            for alias in entry.get("aliases", []):
                idx[alias] = ct_key
        _DZH_CELL_NODE_TYPE_INDEX = idx
    ct_key = _DZH_CELL_NODE_TYPE_INDEX.get(node_type)
    if ct_key is None:
        return None
    return _load_dzh_cell_schema().get("cell_types", {}).get(ct_key)


def _build_cell_model(node):
    """通用 CellModel 工厂：按 schema 分派实例化。无 if/elif ct 分支。"""
    from core import schemas as models
    schema = _load_dzh_cell_schema()
    cell_types = schema.get("cell_types", {})
    dzh_ct = node.get("dzh_cell_type", 0)
    node_type = node.get("type", "")

    entry = cell_types.get(str(dzh_ct))
    if entry is None:
        entry = _lookup_cell_entry_by_node_type(node_type)
    if entry is None:
        logger.warning("未知节点类型: dzh_cell_type=%s, node_type=%s, id=%s — 跳过该节点",
                       dzh_ct, node_type, node.get("id", ""))
        return None

    builder_name = entry.get("builder", "_build_cell_default")
    builder = _DZH_CELL_BUILDERS.get(builder_name, _build_cell_default)
    return builder(node, entry, models)


def _assemble_pool_result(cells, flows, *, pool_meta=None, propagated=None, **extras):
    """合并 convert_tdx_to_config + parse_dzh_xml final-assembly 骨架（含 _meta 统计）。"""
    result = {"nodes": cells, "edges": flows}
    if pool_meta is not None:
        result["pool_meta"] = pool_meta
    result.update(extras)
    breakdown = {}
    for n in cells:
        t = n.get("type", "unknown")
        breakdown[t] = breakdown.get(t, 0) + 1
    result["_meta"] = {
        "cell_count": len(cells),
        "flow_count": len(flows),
        "stock_count": sum(len(n.get("params", {}).get("stocks", [])) for n in cells),
        "cell_type_breakdown": breakdown,
        "topology_mode": _detect_topology_mode(cells, flows),
    }
    if pool_meta is not None:
        result["_meta"]["has_ency"] = pool_meta.get("ency") is not None
    if propagated is not None:
        result["_meta"]["_propagated_stocks"] = list(propagated)
    return result


def parse_dzh_xml(xml_content, filename=None):
    _DZH_CONVERTER._current_filename = filename
    return _DZH_CONVERTER.parse_pool(xml_content)


_CELL_TYPE_MAP = {
    1: {"type_id": 1, "type_name": "text_label", "category": "decoration",
        "has_stocks": False, "has_tradeattr": False, "has_conditions": False, "is_core_node": False},
    2: {"type_id": 2, "type_name": "container", "category": "layout",
        "has_stocks": False, "has_tradeattr": False, "has_conditions": False, "is_core_node": False},
    3: {"type_id": 3, "type_name": "state_column", "category": "layout",
        "has_stocks": False, "has_tradeattr": False, "has_conditions": False, "is_core_node": False},
    4: {"type_id": 4, "type_name": "discard_pool", "category": "terminal",
        "has_stocks": False, "has_tradeattr": False, "has_conditions": False, "is_core_node": True},
    5: {"type_id": 5, "type_name": "drawing_tool", "category": "decoration",
        "has_stocks": False, "has_tradeattr": False, "has_conditions": False, "is_core_node": False},
    6: {"type_id": 6, "type_name": "flow_arrow_v2", "category": "decoration",
        "has_stocks": False, "has_tradeattr": False, "has_conditions": False, "is_core_node": False},
    200: {"type_id": 200, "type_name": "stock_state_pool", "category": "core",
          "has_stocks": True, "has_tradeattr": True, "has_conditions": True, "is_core_node": True},
    201: {"type_id": 201, "type_name": "transfer_condition", "category": "logic",
          "has_stocks": False, "has_tradeattr": False, "has_conditions": True, "is_core_node": True},
    202: {"type_id": 202, "type_name": "market_source", "category": "source",
          "has_stocks": False, "has_tradeattr": False, "has_conditions": False, "is_core_node": True},
    203: {"type_id": 203, "type_name": "result_pool", "category": "core",
          "has_stocks": True, "has_tradeattr": True, "has_conditions": True, "is_core_node": True},
}


def get_cell_type_info(cell_type_int):
    info = _CELL_TYPE_MAP.get(cell_type_int)
    if info is not None:
        return dict(info)
    return {
        "type_id": cell_type_int,
        "type_name": "unknown",
        "category": "unknown",
        "has_stocks": False,
        "has_tradeattr": False,
        "has_conditions": False,
        "is_core_node": False,
    }


def get_all_cell_types():
    return [dict(v) for v in _CELL_TYPE_MAP.values()]

# _encode_action_raw 已直接定义于文件顶部（原 dzh_xml_raw.py 区段，行 291），
# 不再需要此处的别名赋值（合并后无 dzh_xml_exporter 同名覆盖问题）。



# ================================================================
# 执行器（原 dzh_executor.py）
# ================================================================

DEFAULT_HOLD_SEC = 432000
# DZH 默认背景色 0x1000000（RGB 0,0,0 的 1<<24 编码）
_DEFAULT_BACKCOLOR = 16777216
# 32 位有符号整数最大值：表示"永不重载"/"永不过期"
_NEVER_EXPIRE = 2147483647
# enter/exit action.type → param_unit 映射（消除 enter/exit 两处内联重复）
_ACTION_TYPE_UNITS = {"buy_amount": "元", "buy_shares": "股", "sell_shares": "股"}

# DZH 条件评估降级策略缓存
_DZH_CONDITION_FALLBACK_CFG = None


def _load_dzh_condition_fallback() -> Dict[str, Any]:
    """加载 config/dzh_condition_fallback.json 并缓存。"""
    global _DZH_CONDITION_FALLBACK_CFG
    if _DZH_CONDITION_FALLBACK_CFG is not None:
        return _DZH_CONDITION_FALLBACK_CFG
    try:
        cfg_path = Path(__file__).parent / "config" / "runtime" / "dzh_condition_fallback.json"
        with open(cfg_path, "r", encoding="utf-8") as f:
            _DZH_CONDITION_FALLBACK_CFG = json.load(f)
    except Exception:
        logger.warning("无法加载 dzh_condition_fallback.json，降级策略将使用内置默认值", exc_info=True)
        _DZH_CONDITION_FALLBACK_CFG = {}
    return _DZH_CONDITION_FALLBACK_CFG


def _strategy_pass_ratio(stock_list, policy):
    """pass_ratio / deterministic_seed：按比例保留，确定性模式下用固定种子随机抽样。"""
    pass_ratio = float(policy.get("pass_ratio", 0.5))
    if not 0 <= pass_ratio <= 1:
        pass_ratio = 0.5
    seed = policy.get("seed")
    deterministic = bool(policy.get("deterministic", True))
    n = max(1, int(len(stock_list) * pass_ratio)) if stock_list else 0
    if deterministic and seed is not None and stock_list:
        rng = random.Random(seed)
        return rng.sample(stock_list, min(n, len(stock_list)))
    return stock_list[:n]


def _strategy_slice(stock_list, policy):
    """未知策略缺省：稳定取前段切片（按 pass_ratio 比例）。"""
    pass_ratio = float(policy.get("pass_ratio", 0.5))
    if not 0 <= pass_ratio <= 1:
        pass_ratio = 0.5
    n = max(1, int(len(stock_list) * pass_ratio)) if stock_list else 0
    return stock_list[:n]


# 条件降级策略分派表（消除 3 路 strategy if/elif，差异在表内容非代码分支）
_STRATEGY_HANDLERS = {
    "all_pass": lambda sl, p: sl,
    "all_reject": lambda sl, p: [],
    "pass_ratio": _strategy_pass_ratio,
    "deterministic_seed": _strategy_pass_ratio,
}


def _apply_condition_fallback(stock_list: List[Dict],
                              condition_type: str,
                              fallback_cfg: Optional[Dict[str, Any]] = None) -> List[Dict]:
    """根据条件类型查找配置并执行降级策略（表驱动分派）。

    支持策略：all_pass / all_reject / pass_ratio / deterministic_seed，
    未知策略缺省走 _strategy_slice 稳定切片。
    """
    cfg = fallback_cfg if fallback_cfg is not None else _load_dzh_condition_fallback()
    policies = cfg.get("policies", {}) if isinstance(cfg, dict) else {}
    policy = policies.get(condition_type) or policies.get("default", {})
    if not isinstance(policy, dict):
        policy = {}

    strategy = policy.get("strategy", "deterministic_seed")
    handler = _STRATEGY_HANDLERS.get(strategy, _strategy_slice)
    return handler(stock_list, policy)


class NodeStateMachine:
    """节点状态机：维护每个状态池节点的股票集合与进出历史"""

    def __init__(self):
        self._stocks: Dict[str, Dict[str, Dict]] = {}
        self._history: Dict[str, List[Dict]] = {}
        self._lock = threading.RLock()

    def ensure_node(self, node_id: str):
        with self._lock:
            if node_id not in self._stocks:
                self._stocks[node_id] = {}
                self._history[node_id] = []

    def add_stock(self, node_id: str, code: str, data: Optional[Dict] = None,
                  entry_time: Optional[float] = None):
        self.ensure_node(node_id)
        with self._lock:
            et = entry_time if entry_time is not None else time.time()
            old = self._stocks[node_id].get(code)
            if old is not None:
                old['data'] = data or old.get('data', {})
                old['entry_time'] = et
                return
            self._stocks[node_id][code] = {
                'code': code,
                'entry_time': et,
                'exit_time': None,
                'data': data or {},
            }
            self._history[node_id].append({
                'code': code,
                'event': 'enter',
                'timestamp': et,
            })

    def remove_stock(self, node_id: str, code: str,
                     exit_time: Optional[float] = None) -> bool:
        with self._lock:
            if node_id not in self._stocks:
                return False
            xt = exit_time if exit_time is not None else time.time()
            stock = self._stocks[node_id].pop(code, None)
            if stock is not None:
                self._history[node_id].append({
                    'code': code,
                    'event': 'exit',
                    'timestamp': xt,
                })
                return True
            return False

    def get_stocks(self, node_id: str) -> Dict[str, Dict]:
        with self._lock:
            return dict(self._stocks.get(node_id, {}))

    def get_stock_codes(self, node_id: str) -> List[str]:
        with self._lock:
            return list(self._stocks.get(node_id, {}).keys())

    def get_history(self, node_id: str, limit: int = 0) -> List[Dict]:
        with self._lock:
            h = self._history.get(node_id, [])
            if limit > 0:
                return list(h[-limit:])
            return list(h)

    def clear_node(self, node_id: str):
        with self._lock:
            if node_id not in self._stocks:
                return
            xt = time.time()
            for code in list(self._stocks[node_id].keys()):
                self._stocks[node_id].pop(code, None)
                self._history[node_id].append({
                    'code': code,
                    'event': 'exit',
                    'timestamp': xt,
                    'reason': 'clear',
                })

    def check_hold_expiry(self, node_id: str, hold_sec: int,
                          current_time: Optional[float] = None) -> List[str]:
        if hold_sec <= 0 or hold_sec >= 43200000:
            return []
        ct = current_time if current_time is not None else time.time()
        removed = []
        with self._lock:
            for code, info in list(self._stocks.get(node_id, {}).items()):
                entry_t = info.get('entry_time', 0)
                if entry_t > 0 and (ct - entry_t) > hold_sec:
                    self._stocks[node_id].pop(code, None)
                    self._history[node_id].append({
                        'code': code,
                        'event': 'exit',
                        'timestamp': ct,
                        'reason': 'hold_expired',
                        'hold_sec': hold_sec,
                    })
                    removed.append(code)
        return removed

    def get_all_node_codes(self) -> Dict[str, List[str]]:
        with self._lock:
            return {nid: list(s.keys()) for nid, s in self._stocks.items()}

    def get_all_counts(self) -> Dict[str, int]:
        with self._lock:
            return {nid: len(s) for nid, s in self._stocks.items()}


def should_trigger(flow: dict, current_time: Optional[datetime] = None) -> bool:
    """判断Flow是否应在当前时间触发（向后兼容封装）。"""
    if current_time is None:
        current_time = datetime.now()
    return should_fire(flow, current_time)


def execute_transfer(source_node: dict, target_node: dict,
                     flow: dict, stocks: List[Dict]) -> Dict[str, Any]:
    """执行股票转移，支持4种模式：copy/move/overwrite/constituent。

    Args:
        source_node: 源节点配置
        target_node: 目标节点配置
        flow: Flow/Edge配置
        stocks: 要转移的股票列表

    Returns:
        {'transferred': List[dict], 'mode': str, 'cleared': bool}
    """
    params = flow.get('params', flow)
    attr_int = safe_int(params.get('dzh_attr'), 0)

    delete_source = bool(params.get('delete_source', False))
    keep_source = bool(params.get('keep_source', False))
    clear_dest_first = bool(params.get('clear_dest_first', False))
    output_constituent = bool(params.get('output_constituent', False))
    force_move = bool(params.get('force_move', False))

    # 模式识别
    if output_constituent or (attr_int & 0x4000):
        mode = 'constituent'
    elif clear_dest_first or (attr_int & 0x2000):
        mode = 'overwrite'
    elif keep_source or (attr_int & 0x1000):
        mode = 'copy'
    elif delete_source or force_move or (attr_int & 0x1) or (attr_int & 0x2):
        mode = 'move'
    else:
        mode = 'copy'

    transferred = [dict(s) for s in stocks]

    # 表驱动：模式→标志映射
    _MODE_FLAGS = {
        'move': {'delete_source': True},
        'overwrite': {'clear_dest': True},
        'constituent': {'output_constituent': True},
    }
    flags = {'transferred': transferred, 'mode': mode}
    flags.update(_MODE_FLAGS.get(mode, {}))
    return flags


def evaluate_condition(condition: dict, stocks: List[Dict],
                       current_data: Optional[Dict] = None,
                       tq_adapter=None,
                       all_codes: Optional[List[str]] = None,
                       formula_router=None) -> Dict[str, Any]:
    """评估转移条件，返回通过和未通过的股票。

    支持：
    - 基本公式 (basic_condition)
    - 指标公式 (indicator_condition)
    - 排序公式 (ranking_condition)
    - 反向转移 (reverse_transfer)
    - 板块成员 (sector_membership)
    - 横向统计 (cross_section)

    Args:
        formula_router: FormulaRouter 实例（Task 13：替代 tq_adapter.eval_indicator）。
            优先使用 formula_router 评估指标公式；不可用时降级到 fallback。
    """
    current_data = current_data or {}
    params = condition.get('params', condition)
    attr_raw = params.get('attr_int', 0) or params.get('dzh_attr', {})
    fallback_cfg = _load_dzh_condition_fallback()
    if isinstance(attr_raw, dict):
        attr = attr_raw
    else:
        attr = _parse_attr_bits("201", int(attr_raw) if attr_raw else 0)
        attr["raw"] = int(attr_raw) if attr_raw else 0

    sorttype = params.get('sorttype', '')
    stock_list = list(stocks)

    if not stock_list:
        return {'passed': [], 'rejected': [], 'condition_type': 'empty'}

    # 指标条件
    if attr.get('indicator_condition', False):
        formula = params.get('indi', '')
        formula_text = ''
        if formula:
            try:
                formula_text = base64.b64decode(formula).decode('gbk', errors='replace')
            except Exception:
                formula_text = formula
        if formula_router and formula_text and hasattr(formula_router, 'eval_batch'):
            try:
                codes = [s.get('code', str(s)) if isinstance(s, dict) else str(s)
                         for s in stock_list]
                # 通过 FormulaRouter.eval_batch 路由公式评估（替代 tq_adapter.eval_indicator）
                batch_result = _run_async(
                    lambda: formula_router.eval_batch(formula_text, codes, period='1d')
                )
                if isinstance(batch_result, dict):
                    # 成功响应：{symbol: bool} → 转为 {code: value}
                    passed = []
                    rejected = []
                    for s in stock_list:
                        code = s.get('code', str(s)) if isinstance(s, dict) else str(s)
                        val = 1 if batch_result.get(code, False) else 0
                        if val > 0:
                            passed.append(s)
                        else:
                            rejected.append(s)
                    stock_list = passed
                else:
                    stock_list = _apply_condition_fallback(stock_list, 'indicator', fallback_cfg)
            except Exception:
                stock_list = _apply_condition_fallback(stock_list, 'indicator', fallback_cfg)
        else:
            stock_list = _apply_condition_fallback(stock_list, 'indicator', fallback_cfg)

    # 基本条件（非指标时）
    elif attr.get('basic_condition', False):
        stock_list = _apply_condition_fallback(stock_list, 'basic', fallback_cfg)

    # 排序条件
    if attr.get('ranking_condition', False) and sorttype:
        try:
            top_n = int(sorttype)
            if 0 < top_n < len(stock_list):
                stock_list = stock_list[:top_n]
        except (ValueError, TypeError):
            pass

    # 横向统计
    if attr.get('cross_section', False):
        stock_list = _apply_condition_fallback(stock_list, 'cross_section', fallback_cfg)

    # 反向转移
    if attr.get('reverse_transfer', False):
        if all_codes:
            result_set = set(s.get('code', str(s)) if isinstance(s, dict) else str(s)
                             for s in stock_list)
            passed = []
            for c in all_codes:
                if c not in result_set:
                    passed.append({'code': c} if isinstance(c, str) else c)
            stock_list = passed
        else:
            stock_list = []

    # 板块成员
    if attr.get('sector_membership', False):
        # 保留当前列表（板块过滤需外部提供sector_code）
        pass

    passed = stock_list
    passed_codes = set(s.get('code', str(s)) if isinstance(s, dict) else str(s)
                       for s in passed)
    rejected = [s for s in stocks
                if (s.get('code', str(s)) if isinstance(s, dict) else str(s))
                not in passed_codes]

    return {
        'passed': passed,
        'rejected': rejected,
        'condition_type': 'indicator' if attr.get('indicator_condition') else (
            'basic' if attr.get('basic_condition') else 'passthrough'),
        'attr': attr,
    }


class DZHPoolExecutor:
    """DZH股票池运行时执行器（增强版）

    核心功能：
    - 节点状态机：维护每个状态池的股票集合与进出历史
    - Flow触发评估：时间窗口 + 定时器 + 自定义周期
    - 4种转移模式：copy / move / overwrite / constituent
    - 条件评估：基本/指标/排序/反向/板块/横向统计
    """

    def __init__(self, config: dict, tq_adapter=None, formula_router=None):
        self.config = config
        self.tq_adapter = tq_adapter
        self.formula_router = formula_router
        self.state = NodeStateMachine()
        self._nodes: Dict[str, dict] = {}
        self._edges: List[dict] = []
        self._init_nodes_edges()

    def _init_nodes_edges(self):
        self._nodes = {}
        for node in self.config.get('nodes', []):
            nid = node.get('id')
            if nid:
                self._nodes[nid] = node
                self.state.ensure_node(nid)
        self._edges = self.config.get('edges', [])

    def _get_node_hold_sec(self, node_id: str) -> int:
        node = self._nodes.get(node_id)
        if not node:
            return DEFAULT_HOLD_SEC
        return safe_int(node.get('params', {}).get('hold_sec'), DEFAULT_HOLD_SEC)

    def _init_mock_stocks(self):
        """初始化备选池的模拟股票数据（兼容旧版行为）"""
        for node in self.config.get('nodes', []):
            node_id = node.get('id')
            if not node_id:
                continue
            node_type = node.get('type')
            if node_type == 'market_source':
                markets = node.get('params', {}).get('markets', [])
                for market in markets[:3]:
                    prefix = 'SH' if market.startswith('sh') else 'SZ'
                    base_code = 600000 if prefix == 'SH' else 1
                    for i in range(5):
                        code = f"{prefix}{base_code + i:06d}"
                        self.state.add_stock(
                            node_id, code,
                            {'name': f'模拟股{code}',
                             'price': round(10.0 + i * 0.5, 2),
                             'change_pct': round(random.uniform(-5, 5), 2)}
                        )
            elif node_type in ('dzh_condition_pool', 'discard_pool',
                               'stock_state_pool', 'result_pool'):
                # 从XML解析的初始股票加载
                for s in node.get('params', {}).get('stocks', []):
                    label = s.get('label', '')
                    if label:
                        self.state.add_stock(
                            node_id, label,
                            {'t': s.get('t', ''), 'p': s.get('p', '')}
                        )

    def _check_all_hold_expiry(self, current_time: Optional[float] = None):
        ct = current_time if current_time is not None else time.time()
        total_removed = 0
        for node_id in list(self.state._stocks.keys()):
            hold_sec = self._get_node_hold_sec(node_id)
            removed = self.state.check_hold_expiry(node_id, hold_sec, ct)
            if removed:
                total_removed += len(removed)
        return total_removed

    def _resolve_transfer_condition(self, edge: dict) -> Optional[dict]:
        """从edge的via_201或mid解析条件节点"""
        params = edge.get('params', {})
        via_201 = params.get('via_201')
        if via_201:
            return via_201
        mid = params.get('mid')
        if mid:
            return self._nodes.get(mid)
        return None

    def _execute_edge(self, edge: dict) -> dict:
        source = edge.get('source', {})
        target = edge.get('target', {})
        params = edge.get('params', {})

        src_node_id = source.get('node_id')
        tgt_node_id = target.get('node_id')
        edge_id = edge.get('id', '?')

        now_ts = time.time()
        result = {
            'edge_id': edge_id,
            'source_node': src_node_id,
            'target_node': tgt_node_id,
            'passed': 0,
            'rejected': 0,
            'transferred': 0,
            'mode': 'copy',
            'flags_applied': [],
            'enter_actions': [],
            'exit_actions': [],
            'error': None,
        }

        if not src_node_id or not tgt_node_id:
            result['error'] = '源或目标节点ID缺失'
            return result

        src_node = self._nodes.get(src_node_id)
        tgt_node = self._nodes.get(tgt_node_id)

        if src_node_id not in self.state._stocks:
            result['error'] = f'源节点不存在或为空: {src_node_id}'
            return result

        source_stocks = list(self.state.get_stocks(src_node_id).values())
        source_codes = [s.get('code', '') for s in source_stocks]

        # 板块展开：output_constituent
        if params.get('output_constituent'):
            expanded = []
            for stock in source_stocks:
                code = stock.get('code', '')
                # 模拟板块展开：如果是板块代码则展开
                if code.startswith('88') or 'sector' in code.lower() or 'concept' in code.lower():
                    if self.tq_adapter and hasattr(self.tq_adapter, 'get_block_members'):
                        try:
                            members = self.tq_adapter.get_block_members(code)
                            for m in members:
                                expanded.append({'code': m, '_from_sector': code})
                        except Exception:
                            prefix = 'SH' if random.random() > 0.5 else 'SZ'
                            base = random.choice([600, 1, 300])
                            count = random.randint(3, 8)
                            for i in range(count):
                                expanded.append({
                                    'code': f"{prefix}{base + i:06d}",
                                    '_from_sector': code,
                                })
                    else:
                        prefix = 'SH' if random.random() > 0.5 else 'SZ'
                        base = random.choice([600, 1, 300])
                        count = random.randint(3, 8)
                        for i in range(count):
                            expanded.append({
                                'code': f"{prefix}{base + i:06d}",
                                '_from_sector': code,
                            })
                else:
                    expanded.append(dict(stock))
            source_stocks = expanded
            source_codes = [s.get('code', '') for s in source_stocks]
            result['flags_applied'].append('output_constituent')

        # 条件评估
        condition = self._resolve_transfer_condition(edge)
        is_discard = params.get('is_discard_path', False)
        is_pass = params.get('is_pass_path', True)

        if is_discard:
            passed, rejected = [], list(source_stocks)
        elif condition and not is_pass:
            passed, rejected = [], list(source_stocks)
        elif condition:
            eval_result = evaluate_condition(
                condition, source_stocks,
                tq_adapter=self.tq_adapter,
                all_codes=None,
                formula_router=self.formula_router,
            )
            passed = eval_result.get('passed', [])
            rejected = eval_result.get('rejected', [])
        else:
            passed = list(source_stocks)
            rejected = []

        # 排序筛选（sort_type）
        sorttype = params.get('sorttype')
        if sorttype and passed:
            try:
                top_n = int(sorttype)
                if 0 < top_n < len(passed):
                    passed = passed[:top_n]
                    result['flags_applied'].append(f'sort_top_{top_n}')
            except (ValueError, TypeError):
                pass

        # 执行转移
        transfer_result = execute_transfer(
            src_node or {}, tgt_node or {}, edge, passed
        )
        mode = transfer_result['mode']
        result['mode'] = mode
        transferred_stocks = transfer_result['transferred']

        # 处理源节点
        if mode == 'move' or transfer_result.get('delete_source'):
            for s in transferred_stocks:
                code = s.get('code', '')
                if code:
                    self.state.remove_stock(src_node_id, code, now_ts)
            result['flags_applied'].append('delete_source')
            exit_action = src_node.get('params', {}).get('exit_action') if src_node else None
            if exit_action:
                result['exit_actions'].append({'node': src_node_id, 'action': exit_action})

        # 处理目标节点
        self.state.ensure_node(tgt_node_id)
        if mode == 'overwrite' or transfer_result.get('clear_dest'):
            self.state.clear_node(tgt_node_id)
            result['flags_applied'].append('clear_dest')

        # 写入目标
        stocks_to_write = rejected if is_discard else transferred_stocks
        for s in stocks_to_write:
            code = s.get('code', '')
            if code:
                self.state.add_stock(tgt_node_id, code, dict(s), now_ts)

        # enter动作
        if stocks_to_write and not is_discard:
            enter_action = tgt_node.get('params', {}).get('enter_action') if tgt_node else None
            if enter_action:
                result['enter_actions'].append({
                    'node': tgt_node_id,
                    'action': enter_action,
                    'count': len(stocks_to_write),
                })

        result['passed'] = len(passed)
        result['rejected'] = len(rejected)
        result['transferred'] = len(stocks_to_write)

        return result

    def execute_once(self) -> Dict[str, Any]:
        """单次执行所有边（兼容旧版API）"""
        self._init_mock_stocks()
        self._check_all_hold_expiry()

        events = []
        for edge in self._edges:
            event_result = self._execute_edge(edge)
            events.append({
                'event': 'edge_execute',
                'data': event_result,
                'edge_id': edge.get('id'),
                'source': edge.get('source', {}),
                'target': edge.get('target', {}),
            })

        output_stocks = self.get_output_stocks(20)
        return {
            'output_count': len(output_stocks),
            'output_stocks': output_stocks,
            'events': events,
            'node_states': self.get_node_states(),
        }

    def get_node_states(self) -> Dict[str, int]:
        return self.state.get_all_counts()

    def get_node_stocks(self, node_id: str) -> List[str]:
        """获取节点当前股票代码列表"""
        return self.state.get_stock_codes(node_id)

    def get_node_history(self, node_id: str, limit: int = 0) -> List[Dict]:
        """获取节点股票进出历史"""
        return self.state.get_history(node_id, limit)

    def get_output_stocks(self, limit: int = 20) -> List[str]:
        output_stocks = []
        for node in self.config.get('nodes', []):
            node_id = node.get('id')
            if not node_id:
                continue
            node_type = node.get('type')
            if node_type in ('market_source', 'discard_pool', 'text_label',
                             'flow_arrow', 'container', 'state_column',
                             'drawing_tool'):
                continue
            codes = self.state.get_stock_codes(node_id)
            output_stocks.extend(codes)
        seen = []
        seen_set = set()
        for s in output_stocks:
            if s not in seen_set:
                seen_set.add(s)
                seen.append(s)
        return seen[:limit]

    def get_node_full_state(self, node_id: str) -> Dict[str, Any]:
        """获取节点完整状态（含股票详情和历史）"""
        stocks = self.state.get_stocks(node_id)
        history = self.state.get_history(node_id)
        return {
            'node_id': node_id,
            'stock_count': len(stocks),
            'stocks': list(stocks.values()),
            'history_count': len(history),
            'history': history,
        }

    def get_all_node_states(self) -> Dict[str, Dict[str, Any]]:
        result = {}
        for node_id in self._nodes:
            result[node_id] = self.get_node_full_state(node_id)
        return result

# ================================================================
# 导出器（原 dzh_exporter.py）
# ================================================================

# 从 config/defaults.json 加载导出默认值
def _load_export_defaults():
    try:
        cfg_path = Path(__file__).parent / "config" / "runtime" / "defaults.json"
        with open(cfg_path, encoding="utf-8") as f:
            defaults = json.load(f)
        return defaults.get("export", {})
    except Exception:
        return {}

_EXPORT_DEFAULTS = _load_export_defaults()

MARKET_ID_TO_DZH = {
    "sh_a": "SH#上证A股",
    "sh_b": "SH#上证B股",
    "sz_a": "SZ#深证A股",
    "sz_b": "SZ#深证B股",
    "sme": "SZ#中小企业",
    "gem": "SZ#创业板",
    "sector_index": "B$#板块指数",
    "hot_concept": "B$#热门概念",
}

_CELL_SIZES = {
    200: (117, 100),
    201: (117, 100),
    202: (105, 94),
    4: (100, 100),
    1: (80, 30),
    2: (200, 200),
    3: (100, 200),
    5: (100, 30),
    6: (100, 30),
}

_CELL_COLORS = {
    200: "16744448",
    202: "16777227",
    4: "-1",
}

_EMPTYI_DEFAULT = -999999
_EMPTYS_DEFAULT = "@N/A"


def _extract_dzh_id(node_id):
    if "_" in node_id:
        return node_id.split("_", 1)[-1]
    return node_id


def _get_dzh_cell_id(node):
    if not isinstance(node, dict):
        return ""
    params = node.get("params", {})
    dzh_cell_id = params.get("dzh_cell_id")
    if dzh_cell_id is not None:
        return str(dzh_cell_id)
    node_id = node.get("id", "")
    return _extract_dzh_id(node_id) if node_id else ""


def _make_pos(x, y, cell_type, width=None, height=None):
    if width is not None and height is not None:
        w = int(width)
        h = int(height)
    else:
        w, h = _CELL_SIZES.get(cell_type, (100, 100))
    return f"{int(x)},{int(y)},{int(x) + w},{int(y) + h}"


def _make_pos_from_rect(x, y, width, height):
    return f"{int(x)},{int(y)},{int(x) + int(width)},{int(y) + int(height)}"


def _encode_flow_attr(params):
    dzh_attr = params.get('dzh_attr')
    if dzh_attr is not None:
        try:
            return str(int(dzh_attr))
        except (ValueError, TypeError):
            pass

    flag_keys = ["delete_source", "force_move", "keep_source",
                 "clear_dest_first", "output_constituent"]
    attr = encode_attr_flags({k: params.get(k, False) for k in flag_keys}, "flow_attr_masks")
    return str(attr)


def _set_cell_text(cell_elem, params, label):
    if params.get("text") is not None:
        cell_elem.set("text", params["text"])
    elif params.get("_orig_text") is not None:
        cell_elem.set("text", params["_orig_text"])
    elif label:
        cell_elem.set("text", label)


def _should_export_attr(params, attr_name):
    present = params.get("_present_attrs")
    if present is not None:
        if isinstance(present, list):
            for item in present:
                if isinstance(item, dict) and item.get("_set_value") == attr_name:
                    return True
            if any(item == attr_name for item in present if isinstance(item, str)):
                return True
        elif attr_name in present:
            return True

    value = params.get(attr_name)

    # 特殊字段默认值：emptyi / emptys
    emptyi_map = params.get("_emptyi", {})
    emptys_map = params.get("_emptys", {})

    if value is None or value == '':
        if attr_name in emptyi_map or attr_name in emptys_map:
            return False
        if present is None:
            return True
        return False

    if present is not None and attr_name not in present:
        try:
            num_val = int(value)
            if attr_name in emptyi_map and num_val == emptyi_map[attr_name]:
                return False
            if num_val == 0 or num_val == -1:
                return False
        except (ValueError, TypeError):
            if attr_name in emptys_map and str(value) == emptys_map[attr_name]:
                return False
            pass
    return True


def _set_elem_attr(elem, attr_name, value, params):
    if not _should_export_attr(params, attr_name):
        return
    if value is not None:
        elem.set(attr_name, str(value))
        return
    # 值为None时，若声明了emptyi/emptys，按特定值导出（否则不导出）
    emptyi_map = params.get("_emptyi", {})
    emptys_map = params.get("_emptys", {})
    if attr_name in emptyi_map:
        elem.set(attr_name, str(emptyi_map[attr_name]))
    elif attr_name in emptys_map:
        elem.set(attr_name, str(emptys_map[attr_name]))


def _add_timing_attrs(flow_elem, edge_params):
    for key in ("begin", "begint", "end", "endt"):
        if _should_export_attr(edge_params, key):
            val = edge_params.get(key)
            if val is not None:
                flow_elem.set(key, str(val))
    if _should_export_attr(edge_params, "interval"):
        interval_val = edge_params.get("interval_sec")
        if interval_val is not None:
            flow_elem.set("interval", str(interval_val))
    if _should_export_attr(edge_params, "mid"):
        mid = edge_params.get("mid")
        if mid is not None:
            flow_elem.set("mid", str(mid))

    # 自定义时间属性
    for key in ("cst", "cet", "cstt", "cett"):
        if _should_export_attr(edge_params, key):
            val = edge_params.get(key)
            if val is not None:
                flow_elem.set(key, str(val))

    c_period = edge_params.get("c_period") or edge_params.get("c周期")
    if c_period is not None and _should_export_attr(edge_params, "c_period"):
        flow_elem.set("c周期", str(c_period))


# ================================================================
# 表驱动 cell 构建器：attr 策略 + 字段导出器 + 通用 _build_cell
# ================================================================

_ATTR_SKIP = object()

# cell200 / cell201 位标志键（attr_flag_map.json 单一数据源，消除内联重复）
_CELL200_FLAG_KEYS = ["show_overview", "simple_intermediate", "no_delete_source",
                      "clear_dest_first", "calc_profit_from_prev", "record_history",
                      "alert_popup", "alert_sound", "highlight_flash",
                      "alert_scroll_window", "hidden_pool", "show_overview_container"]
_CELL201_FLAG_KEYS = ["indicator_condition", "ranking_condition", "sector_membership",
                      "reverse_transfer", "cross_section", "basic_condition",
                      "show_overview", "bit16_reserved"]


def _resolve_attr(params, cell_type, cfg):
    """通用 attr 解析器（表驱动）：按 cfg["sources"] 顺序尝试解析，未命中走 flag_encode/default。

    合并原 6 个 _resolve_attr_* 独立函数，差异收敛到 _ATTR_STRATEGIES 配置：
      - default：最终缺省值（int / _ATTR_SKIP）
      - flag_encode：位标志编码配置（table/keys/from）
      - sources：有序源列表 [(key, mode)]，mode ∈
          int / int_if_int / int_if_int_or_attr / raw / dict_raw / dzh_dict
      - cell_type_default：cell_type → default 覆盖
      - skip + conditional_default_attr：仅当 _should_export_attr 时返回 default 否则 _ATTR_SKIP
    """
    default = cfg.get("default", 0)
    flag_cfg = cfg.get("flag_encode")

    for key, mode in cfg.get("sources", []):
        if mode == "raw":
            # simple_attr_int: params.get(key, default)
            return params.get(key, default)
        if mode == "int_if_int_or_attr":
            # attr_int_or_attr_default_0: get(key,0) → int 直返, 否则 attr 回退 "0"
            val = params.get(key, 0)
            if isinstance(val, int):
                return val
            return params.get("attr", "0")
        val = params.get(key)
        if val is None:
            continue
        if mode == "int":
            try:
                return int(val)
            except (ValueError, TypeError):
                pass
        elif mode == "int_if_int":
            if isinstance(val, int):
                return val
        elif mode == "dict_raw":
            if isinstance(val, dict):
                raw = val.get("raw")
                if raw is not None:
                    try:
                        return int(raw)
                    except (ValueError, TypeError):
                        pass
        elif mode == "dzh_dict":
            # flag_encoded_200 的 dzh_attr 特殊处理：raw 非零直用, 否则位标志编码
            if isinstance(val, dict):
                raw_val = val.get("raw")
                if raw_val is not None and raw_val != 0:
                    return int(raw_val)
                if flag_cfg and any(k in val for k in flag_cfg["keys"]):
                    encoded = encode_attr_flags({k: val.get(k, False) for k in flag_cfg["keys"]}, flag_cfg["table"])
                    val['raw'] = encoded
                    return encoded
                elif raw_val is not None:
                    return int(raw_val)
            elif isinstance(val, int):
                return val

    # 后置 flag 编码（flag_encoded_201: 从 params 直接读位标志）
    if flag_cfg and flag_cfg.get("from") == "params":
        if any(params.get(k) for k in flag_cfg["keys"]):
            return encode_attr_flags({k: params.get(k, False) for k in flag_cfg["keys"]}, flag_cfg["table"])

    # cell_type 相关默认值覆盖
    cell_type_defaults = cfg.get("cell_type_default")
    if cell_type_defaults:
        default = cell_type_defaults.get(cell_type, default)

    # 条件默认值（flag_encoded_200: 仅当 _should_export_attr 时返回 default，否则 _ATTR_SKIP）
    if cfg.get("skip"):
        cond_attr = cfg.get("conditional_default_attr")
        if (not cond_attr) or _should_export_attr(params, cond_attr):
            return default
        return _ATTR_SKIP
    return default


_ATTR_STRATEGIES = {
    "attr_int_or_attr_default_128": {
        "sources": [("attr_int", "int"), ("attr", "int")],
        "default": 128,
    },
    "flag_encoded_200": {
        "sources": [("dzh_attr", "dzh_dict"), ("attr_int", "int"), ("attr", "int")],
        "flag_encode": {"table": "cell200_attr_masks", "keys": _CELL200_FLAG_KEYS, "from": "dzh_attr"},
        "default": 128,
        "conditional_default_attr": "attr",
        "skip": True,
    },
    "flag_encoded_201": {
        "sources": [("attr_int", "int"), ("attr_decoded", "dict_raw"), ("attr", "int")],
        "flag_encode": {"table": "cell201_attr_masks", "keys": _CELL201_FLAG_KEYS, "from": "params"},
        "default": 0,
    },
    "simple_attr_int": {
        "sources": [("attr_int", "raw")],
        "default": 0,
    },
    "attr_int_or_attr_default_0": {
        "sources": [("attr_int", "int_if_int_or_attr")],
        "default": "0",
    },
    "attr_int_or_default_512_or_0": {
        "sources": [("attr_int", "int_if_int")],
        "default": 0,
        "cell_type_default": {"2": 512},
    },
}


# ----------------------------------------------------------------
# 字段导出器
# ----------------------------------------------------------------

def _export_simple_field(cell_elem, params, field, cell_type=None):
    """通用字段导出器：_should_export_attr + cell_elem.set"""
    if _should_export_attr(params, field):
        val = params.get(field)
        if val is not None:
            cell_elem.set(field, str(val))


def _export_field_attrtext(cell_elem, params, field, cell_type=None):
    """attrtext: 202 重建逻辑 / 200 简单直传"""
    if not _should_export_attr(params, "attrtext"):
        return
    if cell_type == "202":
        raw_attrtext = params.get("raw_attrtext")
        # 优先使用 selections 列表重建 attrtext
        selections = params.get("selections")
        if selections and isinstance(selections, list):
            attrtext = build_attrtext_from_selections(selections)
        elif raw_attrtext is not None:
            attrtext = raw_attrtext
        else:
            parts = []
            markets = params.get("markets", [])
            for m in markets:
                parts.append(MARKET_ID_TO_DZH.get(m, m))
            sectors = params.get("sectors", [])
            for s in sectors:
                parts.append(s)
            stocks_raw = params.get("stocks_raw", [])
            for s in stocks_raw:
                parts.append(s)
            attrtext = "\t".join(parts)
        attrtext = attrtext.replace("\t", "__DZH_TAB__")
        cell_elem.set("attrtext", attrtext)
    else:
        attrtext = params.get("attrtext")
        if attrtext is not None:
            cell_elem.set("attrtext", str(attrtext))


def _export_field_reload(cell_elem, params, field, cell_type=None):
    """reload: 202 reload_sec/reload_mode 编码 / 200 简单直传"""
    if not _should_export_attr(params, "reload"):
        return
    if cell_type == "202":
        reload_sec = params.get("reload_sec")
        # 优先使用 reload_mode + reload_param 编码 reload 值
        reload_mode = params.get("reload_mode")
        if reload_mode and not reload_sec:
            reload_sec = encode_reload_mode(reload_mode, params.get("reload_param"))
        cell_elem.set("reload", str(reload_sec) if reload_sec is not None else "0")
    else:
        reload_val = params.get("reload")
        if reload_val is not None:
            cell_elem.set("reload", str(reload_val))


def _export_field_lastload(cell_elem, params, field, cell_type=None):
    """lastload: 202 缺省 '0' / 200 仅非 None 写入"""
    if not _should_export_attr(params, "lastload"):
        return
    lastload = params.get("lastload")
    if cell_type == "202":
        cell_elem.set("lastload", str(lastload) if lastload is not None else "0")
    else:
        if lastload is not None:
            cell_elem.set("lastload", str(lastload))


# 简单字段导出表（合并 _export_field_hold/col/width/wizd）：
# (source_key, transform, guard) — guard: "not_none" / "truthy_list" / "truthy"
def _join_csv(v):
    return ",".join(str(x) for x in v) if isinstance(v, list) else str(v)


_EXPORT_FIELD_TABLE = {
    "hold":  ("hold_sec", str, "not_none"),
    "col":   ("col_list", _join_csv, "not_none"),
    "width": ("width_list", lambda v: ",".join(str(w) for w in v), "truthy_list"),
    "wizd":  ("wizd", lambda v: str(v).replace("\n", "__DZH_NEWLINE__"), "truthy"),
}


def _export_field_simple_table(cell_elem, params, field, cell_type=None):
    """表驱动的简单字段导出器（hold/col/width/wizd 共用）。"""
    spec = _EXPORT_FIELD_TABLE.get(field)
    if spec is None or not _should_export_attr(params, field):
        return
    source_key, transform, guard = spec
    val = params.get(source_key)
    if guard == "not_none":
        if val is None:
            return
    elif guard == "truthy_list":
        if not (val and isinstance(val, list)):
            return
    elif guard == "truthy":
        if not val:
            return
    cell_elem.set(field, transform(val))


def _export_field_action(cell_elem, params, field, cell_type=None):
    """200 enter/exit 通用导出器：{field}_action dict/int → {field} 回退 → 缺省 '0'。

    合并原 _export_field_enter/_export_field_exit 近重复函数，差异仅在
    field='enter'/'exit'，unit 映射统一查 _ACTION_TYPE_UNITS 常量。
    """
    written = False
    action = params.get(field + "_action")
    if action is not None:
        if isinstance(action, dict):
            raw_val = action.get("raw")
            if raw_val is not None:
                if "param_unit" not in action and "type" in action:
                    action["param_unit"] = _ACTION_TYPE_UNITS.get(action["type"])
                cell_elem.set(field, str(int(raw_val)))
                written = True
        elif isinstance(action, int):
            cell_elem.set(field, str(action))
            written = True
    if not written:
        val = params.get(field)
        if val is not None:
            try:
                int_val = int(val)
                if int_val != 0 or field in params.get("_present_attrs", set()):
                    cell_elem.set(field, str(int_val))
                    written = True
            except (ValueError, TypeError):
                pass
    if not written and _should_export_attr(params, field):
        cell_elem.set(field, "0")


def _export_field_stocks(cell_elem, params, field, cell_type=None):
    """200 stocks: 导出 stk 子元素（含 hist/ana 子元素）—— 委托到 _StkWriter。"""
    _StkWriter.write_stks(cell_elem, params, fmt="dzh")


def _export_field_anas(cell_elem, params, field, cell_type=None):
    """200 anas: 导出 ana 子元素"""
    anas = params.get("anas")
    if anas and isinstance(anas, list):
        for a in anas:
            ana_elem = ET.SubElement(cell_elem, "ana")
            ana_elem.text = None
            ana_elem.tail = None
            ana_elem.set("label", a.get("label", ""))
            ana_elem.set("t", a.get("t", ""))
            ana_elem.set("p", a.get("p", ""))


def _export_field_tradeattr(cell_elem, params, field, cell_type=None):
    """200 tradeattr: 导出 tradeattr 子元素"""
    tradeattr = params.get("tradeattr")
    if tradeattr and isinstance(tradeattr, dict):
        ta = ET.SubElement(cell_elem, "tradeattr")
        for key, val in tradeattr.items():
            ta.set(key, str(val))


_FIELD_EXPORTERS = {
    "attrtext": _export_field_attrtext,
    "reload": _export_field_reload,
    "lastload": _export_field_lastload,
    "hold": _export_field_simple_table,
    "col": _export_field_simple_table,
    "width": _export_field_simple_table,
    "wizd": _export_field_simple_table,
    "enter": _export_field_action,
    "exit": _export_field_action,
    "stocks": _export_field_stocks,
    "anas": _export_field_anas,
    "tradeattr": _export_field_tradeattr,
}

# dzh_type → 属性追加方法名（消除 _export_node 中 dzh_type 属性分派 if/elif）
# 200/203 → 状态池属性, 201 → 条件属性, 202 → 源属性
_TYPE_ATTR_APPENDERS = {
    200: "_append_state_pool_attrs",
    203: "_append_state_pool_attrs",
    201: "_append_condition_attrs",
    202: "_append_source_attrs",
}
# 需导出 tradeattr 子元素的 dzh_type 集合（仅 200/203）
_TRADEATTR_TYPES = {200, 203}


def _build_cell(cell_elem, node, type_rule, dzh_cell_type=None):
    """通用 cell 构建器：按 type_rule 表配置逐字段写入。"""
    params = node.get("params", {})
    pos = node.get("position", {"x": 0, "y": 0})
    x, y = int(pos.get("x", 0)), int(pos.get("y", 0))
    pos_w, pos_h = pos.get("width"), pos.get("height")
    label = node.get("label", "")

    # 1. set type
    cell_type = type_rule.get("force_type") or str(type_rule.get("dzh_cell_type", dzh_cell_type or 0))
    cell_elem.set("type", str(cell_type))

    # 2. resolve attr (表驱动策略)
    attr_strategy = type_rule.get("attr_strategy", "simple_attr_int")
    attr_cfg = _ATTR_STRATEGIES.get(attr_strategy)
    if attr_cfg:
        attr_val = _resolve_attr(params, cell_type, attr_cfg)
        if attr_val is not _ATTR_SKIP:
            cell_elem.set("attr", str(attr_val))
    else:
        cell_elem.set("attr", "0")

    # 3. set pos (表驱动策略)
    pos_strategy = type_rule.get("pos_strategy", "standard")
    if pos_strategy == "rect_or_standard" and pos_w is not None and pos_h is not None:
        cell_elem.set("pos", _make_pos_from_rect(x, y, pos_w, pos_h))
    else:
        cell_elem.set("pos", _make_pos(x, y, int(cell_type), pos_w, pos_h))

    # 4. set clr (表驱动条件)
    if type_rule.get("clr_default") == "cell_colors":
        clr_default = _CELL_COLORS.get(int(cell_type), -1)
    else:
        clr_default = -1
    if type_rule.get("clr_conditional", False):
        if _should_export_attr(params, "clr"):
            cell_elem.set("clr", str(params.get("clr", clr_default)))
    else:
        cell_elem.set("clr", str(params.get("clr", clr_default)))

    # 5. set text
    _set_cell_text(cell_elem, params, label)

    # 6. export fields (表驱动字段列表)
    for field in type_rule.get("export_fields", []):
        exporter = _FIELD_EXPORTERS.get(field, _export_simple_field)
        exporter(cell_elem, params, field, cell_type)


def _build_flow_arrow_cell(cell_elem, node):
    """表驱动：flow_arrow (type=6) 的构建函数"""
    params = node.get("params", {})
    pos = node.get("position", {"x": 0, "y": 0})
    x = int(pos.get("x", 0))
    y = int(pos.get("y", 0))
    pos_w = pos.get("width")
    pos_h = pos.get("height")
    label = node.get("label", "")

    cell_elem.set("type", "6")
    cell_elem.set("attr", "0")
    if pos_w is not None and pos_h is not None:
        cell_elem.set("pos", _make_pos_from_rect(x, y, pos_w, pos_h))
    else:
        cell_elem.set("pos", _make_pos(x, y, 6))
    cell_elem.set("clr", str(params.get("clr", "-1")))
    _set_cell_text(cell_elem, params, label)


def _find_node_by_id(nodes, node_id):
    for n in nodes:
        if not isinstance(n, dict):
            continue
        if n.get("id") == node_id:
            return n
    return None


def _find_node_dzh_id(nodes, node_id):
    node = _find_node_by_id(nodes, node_id)
    if node:
        return _get_dzh_cell_id(node)
    if node_id.startswith("m_"):
        return node_id[2:]
    return ""


def export_dzh_xml(config):
    return _DZH_CONVERTER.export_pool(config)


def export_meta_to_dzh_xml_bytes(pool_config):
    return export_dzh_xml(pool_config)

# ================================================================
# XML 导出器（原 dzh_xml_exporter.py）
# ================================================================

def encode_action(action: Dict) -> int:
    """将action字典编码为整数（委托共用 encode_action(action_type, param) 单一数据源）。"""
    if not action or not isinstance(action, dict):
        return 0
    raw = action.get("raw")
    if raw is not None:
        return int(raw)
    action_type = action.get("type", "none")
    if action_type == "none":
        return 0
    param = action.get("param", 0)
    val = _encode_action_raw(action_type, param)
    if val:
        return val
    # byte 类型（buy/sell）使用 param1 字段
    param1 = action.get("param1", action.get("param", 0))
    return _encode_action_raw(action_type, param1)


def encode_formula(formula_text: str) -> str:
    """将公式文本编码为base64"""
    if not formula_text:
        return ""
    try:
        return base64.b64encode(formula_text.encode("gbk")).decode("ascii")
    except Exception:
        try:
            return base64.b64encode(formula_text.encode("utf-8")).decode("ascii")
        except Exception:
            return formula_text


DZH_COL_INVERSE = {
    "code": -1,
    "name": -2,
    "change_pct": -3,
    "current_price": 2,
    "change_amt": 7,
    "bid_price": 8,
    "ask_price": 9,
    "volume": 10,
    "amount": 14,
    "turnover_rate": 17,
    "pe_ratio": 45,
    "seq": 1,
    "change_amt2": -5,
    "volume2": -6,
    "turnover_rate2": 24,
    "ddx_red_days": 101,
    "volume_ratio": 108,
    "bbd": 287,
    "ddx": 401,
}

NODE_TYPE_NAME_MAP = {
    "stock_state_pool": 200,
    "transfer_condition": 201,
    "market_source": 202,
    "result_pool": 203,
    "discard_pool": 4,
    "text_label": 1,
    "container": 2,
    "state_column": 3,
    "flow_arrow": 6,
    "drawing_tool": 5,
    "flow_arrow_v2": 6,
}


# ======================================================================
# TDX 股票池转换器（原 converters/tdx.py）
# ======================================================================



# _run_async 已在文件顶部定义（原 dzh.py 与 tdx.py 共享），此处不再重复。


# ================================================================
# XML 原始解析（原 tdx_xml_raw.py）
# ================================================================


# 表驱动 noperate 委托：复用 evaluators.py 的通用比较器与排名器，
# 不在转换层另起 if/elif 体系或 lambda 字典
# Task 23.3: 改为从 core.domain 导入（白名单），消除跨层违规


# ═══════════════════════════════════════════════════════════════
# 增强的内部模型子类，携带 TDX 特有的扩展字段
# ═══════════════════════════════════════════════════════════════

# 使用 DynamicCellModel 替代已删除的特定模型类。
# TDX 特有字段（tdx_psatt、tdx_func 等）通过属性注入方式添加到 DynamicCellModel 实例上。

def _make_tdx_cell(internal_type: int, tdx_cell: Any, pos: Any, **extra) -> DynamicCellModel:
    """创建携带 TDX 扩展字段的 DynamicCellModel 实例（合并 3 分支公共 kwargs）。

    TDX 特有字段（如 tdx_func, tdx_psatt, tdx_spinfo, tdx_stocks）作为
    额外属性附加到模型实例上，透传给后续引擎使用。公共字段（id/attr/position/
    clr/text/tdx_id/tdx_type/clrtext/solid）由 tdx_cell 统一提取，差异通过 **extra 传入。
    """
    data = {
        "type": internal_type,
        "id": str(tdx_cell.id),
        "attr": tdx_cell.attr,
        "position": pos,
        "clr": tdx_cell.clr,
        "text": tdx_cell.text,
        "tdx_id": tdx_cell.id,
        "tdx_type": tdx_cell.type,
        "clrtext": tdx_cell.clrtext,
        "solid": tdx_cell.solid,
    }
    data.update(extra)
    model = DynamicCellModel.from_dict(data)
    # 附加 TDX 特有字段（不参与序列化）
    for key in ('tdx_func', 'tdx_psatt', 'tdx_spinfo', 'tdx_stocks',
                'tdx_id', 'tdx_type', 'clrtext', 'solid'):
        if key in data:
            setattr(model, key, data[key])
    return model


# ═══════════════════════════════════════════════════════════════
# 版本检测与兼容性常量
# ═══════════════════════════════════════════════════════════════

# spinfo.type 枚举映射
SPINFO_TYPE_MAP = {
    0: {"label": "自设监控品种", "market": "SZ,SH"},
    1: {"label": "沪深300+中证500", "market": "SZ,SH"},
    2: {"label": "所有A股", "market": "SZ,SH"},
    3: {"label": "自选股", "market": ""},
    4: {"label": "自定义板块", "market": ""},
    5: {"label": "板块指数", "market": "SZ,SH"},
    6: {"label": "ETF基金", "market": "SZ,SH"},
    7: {"label": "可转债", "market": "SZ,SH"},
}

# cell.setcode 市场映射
SETCODE_MARKET_MAP = {
    0: "SZ",   # 深圳
    1: "SH",   # 上海
    2: "BJ",   # 北交所
}

# V7.x func 元素最小属性数量阈值（V6.x 约11个，V7.x 16个）
V7_FUNC_ATTR_THRESHOLD = 12


def detect_xml_version(root: ET.Element) -> str:
    """
    检测 TDX XML 版本。

    Args:
        root: XML 根元素 (<root>)

    Returns:
        'v7': V7.x 新版 (16-field func / 14-field psatt / bsavehis / setcode)
        'v6': V6.x 旧版 (8-field func / 9-field stk / nsyssound)
    """
    # 检测方法1: func 元素属性数量
    first_cell = root.find('.//cell')
    if first_cell is not None:
        func = first_cell.find('func')
        if func is not None:
            attr_count = len(func.attrib)
            if attr_count >= V7_FUNC_ATTR_THRESHOLD:
                return 'v7'

    # 检测方法2: 是否有 bsavehis 字段（V7.x 新增）
    pool = root.find('.//pool')
    if pool is not None:
        first_psatt = pool.find('.//psatt')
        if first_psatt is not None and 'bsavehis' in first_psatt.attrib:
            return 'v7'
        if first_psatt is not None and 'nsyssound' in first_psatt.attrib:
            return 'v6'

    return 'v7'  # 默认按新版处理


# ═══════════════════════════════════════════════════════════════
# 解析函数
# ═══════════════════════════════════════════════════════════════

# ── 配置表目录与表驱动加载器（供 XML 解析与执行器共用） ─────────────────
# 配置表位于 meta_core/config/，模块加载时读入内存，避免硬编码字典
_CONFIG_DIR = Path(__file__).parent / "config"


def _resolve_config_path(filename: str) -> Path:
    """按文件名定位配置表路径。

    SubTask 27.14: 配置文件按模块分类到 architecture/data/runtime/ui/pools/
    子目录后，先尝试 _CONFIG_DIR/filename，再递归查找（跳过 _archived/），
    与 table_engine._find_table_path 行为一致。
    """
    direct = _CONFIG_DIR / filename
    if direct.exists():
        return direct
    for candidate in _CONFIG_DIR.rglob(filename):
        if "_archived" not in candidate.parts:
            return candidate
    return direct  # 返回直连路径以保留原有失败日志行为


def _load_tdx_element_schemas(filename: str) -> Dict[str, dict]:
    """从 JSON 配置表读取元素解析 schema，构建 element_name->schema 映射。"""
    path = _resolve_config_path(filename)
    try:
        data = json.loads(path.read_text("utf-8"))
        return data.get("elements", {})
    except Exception as e:
        logger.warning("加载 TDX 元素 schema %s 失败: %s", filename, e)
        return {}


def _load_tdx_period_map(filename: str) -> Dict[int, str]:
    """从 JSON 配置表读取 period_map 段，构建 int->周期字符串 映射（单份定义）。"""
    path = _resolve_config_path(filename)
    fallback = {0: "1d", 1: "5m", 2: "15m", 3: "30m",
                4: "1d", 5: "1w", 6: "1mon"}
    try:
        data = json.loads(path.read_text("utf-8"))
        raw = data.get("period_map", {})
        if not raw:
            return fallback
        return {int(k): v for k, v in raw.items()}
    except Exception as e:
        logger.warning("加载 TDX period_map 失败: %s", e)
        return fallback


# 元素解析 schema（int_fields / sector_type_map 等字段映射表驱动）
_TDX_ELEMENT_SCHEMAS: Dict[str, dict] = _load_tdx_element_schemas(
    "tdx_element_schemas.json"
)

# nperiod 数字 -> 周期字符串 映射（单份定义，从 tdx_enums.json 加载）
_TDX_PERIOD_MAP: Dict[int, str] = _load_tdx_period_map("tdx_enums.json")


def _get_element_int_fields(element: str) -> set:
    """从 schema 表查询元素的 int_fields 集合，缺失时返回空集。"""
    schema = _TDX_ELEMENT_SCHEMAS.get(element, {})
    return set(schema.get("int_fields", []))


def _get_element_sector_type_map(element: str) -> Dict[str, int]:
    """从 schema 表查询元素的 sector_type_map，缺失时返回空映射。"""
    schema = _TDX_ELEMENT_SCHEMAS.get(element, {})
    return dict(schema.get("sector_type_map", {}))


# _parse_func_element / _parse_psatt_element / _parse_spinfo_element /
# _parse_stk_elements 已合并到 TdxPoolConverter._parse_element + post_hooks
# 与 _StkIO.parse_stks；模块级名称在文件顶部委托到 _TDX_CONVERTER / _StkIO。


def parse_tdx_xml(filepath: str) -> TdxPoolMetaModel:
    return _TDX_CONVERTER.parse_pool(filepath)


# ═══════════════════════════════════════════════════════════════
# 转换函数：TDX 池模型 → 内部统一池模型
# ═══════════════════════════════════════════════════════════════

def tdx_to_internal(tdx_pool: TdxPoolMetaModel) -> PoolMetaModel:
    """向后兼容包装：委托到 TdxPoolConverter.tdx_to_internal（G2 归入类内）。"""
    return TdxPoolConverter.tdx_to_internal(tdx_pool)
# ================================================================
# 转换器（原 tdx_converter.py）
# ================================================================

# ═══════════════════════════════════════════════════════════════
# TDX 类型重建
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════

def _get_dzh_cell_type(cell: Any) -> int:
    """从 cell 模型获取 DZH 内部 cell_type。

    TdxCellModel.type 存储 TDX 原生类型 (0~8)，需通过 dzh_type_map:tdx_to_dzh 映射。
    DynamicCellModel.type / cell_type 已是 DZH 内部类型 (200/201/202/203)，直接使用。
    """
    ct = getattr(cell, "type", getattr(cell, "cell_type", 0))
    # 如果 ct 在 tdx_to_dzh 中，说明是 TDX 原生类型，需要映射
    _tdx_to_dzh = (get_global_config_store().get_table("dzh_type_map") if get_global_config_store() else {}).get("tdx_to_dzh", {})
    if str(ct) in _tdx_to_dzh:
        return int(_tdx_to_dzh[str(ct)])
    # 否则 ct 已经是 DZH 内部类型（200/201/202/203 等），直接返回
    return ct


def _build_position(cell: Any) -> Dict[str, int]:
    """从 cell.position 构建位置字典。"""
    pos = getattr(cell, "position", None)
    if pos is not None:
        return {
            "x": getattr(pos, "x", 0),
            "y": getattr(pos, "y", 0),
            "width": getattr(pos, "width", 0),
            "height": getattr(pos, "height", 0),
        }
    return {"x": 0, "y": 0, "width": 0, "height": 0}


def _code_to_setcode(code: str) -> int:
    """根据股票代码前缀推断 setcode：0=SZ, 1=SH, 2=BJ。

    B股代码范围：
      - 900xxx.SH → 上海B股 (setcode=1)
      - 200xxx.SZ → 深圳B股 (setcode=0)
    三板(新三板)代码范围：
      - 83xxxx / 43xxxx → 北交所 (setcode=2)
    """
    if not code:
        return 0
    # 处理 tq_code 格式 (如 "600000.SH", "000001.SZ")
    if "." in code:
        num_part, suffix = code.rsplit(".", 1)
        suffix = suffix.upper()
        setcode = _SUFFIX_TO_SETCODE.get(suffix)
        if setcode is not None:
            return setcode
        # 未知后缀，继续用数字部分推断
        code = num_part

    ch = code[0]
    # B股：900xxx → SH, 200xxx → SZ（特判，不在通用前缀表中）
    if ch == "9" and len(code) >= 3 and code[1] == "0":
        return 1  # SH B股
    if ch == "2" and len(code) >= 3 and code[0:3] == "200":
        return 0  # SZ B股
    setcode = _PREFIX_TO_SETCODE.get(ch)
    if setcode is not None:
        return setcode
    # 歧义代码：无法确定市场，记录警告并默认 SZ
    logger.warning("歧义股票代码无法推断市场: %s, 默认 setcode=0 (SZ)", code)
    return 0


# ═══════════════════════════════════════════════════════════════
# Cell 转换函数
# ═══════════════════════════════════════════════════════════════

class _CellEnvelope:
    """TDX cell → node 包络合并基类：合并 _convert_candidate/state_pool/condition/decoration_cell。

    抽取三者共享的子对象提取、外观属性收集与 9-key 返回包络。
    """

    @staticmethod
    def _visual_params(cell):
        return {
            "clr": _safe_get(cell, "clr", -1),
            "clrtext": _safe_get(cell, "clrtext", 0),
            "solid": _safe_get(cell, "solid", 0),
        }

    @staticmethod
    def _envelope(cell, node_type, params):
        return {
            "id": _safe_get(cell, "id", ""),
            "type": node_type,
            "position": _build_position(cell),
            "clr": _safe_get(cell, "clr", -1),
            "clrtext": _safe_get(cell, "clrtext", 0),
            "solid": _safe_get(cell, "solid", 0),
            "text": _safe_get(cell, "text", ""),
            "params": params,
            "label": _safe_get(cell, "text", ""),
        }

    @staticmethod
    def _extract_subobj(cell, primary_attr, tdx_attr, model_class, default_fn):
        """提取子对象字段：扁平键 + 嵌套 tdx_<name> 分组 + 短别名 + tdx_<name>_<field> 扁平键。

        返回 (params, obj)；obj 为 None 时 params 为空 dict。
        """
        obj = getattr(cell, primary_attr, getattr(cell, tdx_attr, None))
        params: Dict[str, Any] = {}
        if obj is None:
            return params, None
        name = tdx_attr[len("tdx_"):]
        group = {}
        for field in model_class.model_fields:
            d = default_fn(field)
            val = _safe_get(obj, field, d)
            params[field] = val
            group[field] = val
        params[tdx_attr] = group
        params[name] = group
        for field in model_class.model_fields:
            params[f"{tdx_attr}_{field}"] = _safe_get(obj, field, default_fn(field))
        return params, obj

    @staticmethod
    def _tdx_stocks_from(stocks):
        """统一 tdx_stocks 嵌套分组：[{setcode, code}, ...]。"""
        return [{"setcode": _code_to_setcode(s["code"]), "code": s["code"]} for s in stocks]


def _convert_candidate_cell(cell: Any) -> Dict[str, Any]:
    """转换候选池 cell (cell_type=202) → node type="tdx_candidate"。"""
    params, spinfo = _CellEnvelope._extract_subobj(
        cell, "spinfo", "tdx_spinfo", TdxSpinfoModel,
        lambda f: "" if f in ("market", "customblockname") else 0)
    if spinfo is not None:
        if params.get("customblockname") is None:
            params["customblockname"] = ""
        if params.get("market") is None:
            params["market"] = ""
    stocks = [{"code": _safe_get(stk, "tq_code", "")}
              for stk in (getattr(cell, "stks", getattr(cell, "tdx_stocks", None)) or [])]
    params["stocks"] = stocks
    params["tdx_stocks"] = _CellEnvelope._tdx_stocks_from(stocks)
    params.update(_CellEnvelope._visual_params(cell))
    return _CellEnvelope._envelope(cell, "tdx_candidate", params)


def _convert_state_pool_cell(cell: Any) -> Dict[str, Any]:
    """转换状态池 cell (cell_type=200) → node type="tdx_state_pool"。"""
    params, _psatt = _CellEnvelope._extract_subobj(
        cell, "psatt", "tdx_psatt", TdxPsattModel, lambda f: 0)
    stocks: List[Dict[str, str]] = []
    tdx_stocks = getattr(cell, "stks", getattr(cell, "tdx_stocks", None)) or []
    if tdx_stocks:
        for stk in tdx_stocks:
            stocks.append({"code": _safe_get(stk, "tq_code", "")})
    else:
        stk_list = getattr(cell, "stocks", None) or getattr(cell, "stk_list", None) or []
        for stk in stk_list:
            stocks.append({"code": _safe_get(stk, "label", ""),
                           "t": _safe_get(stk, "t", ""),
                           "p": _safe_get(stk, "p", "")})
    params["stocks"] = stocks
    params["tdx_stocks"] = _CellEnvelope._tdx_stocks_from(stocks)
    params.update(_CellEnvelope._visual_params(cell))
    return _CellEnvelope._envelope(cell, "tdx_state_pool", params)


def _convert_condition_cell(cell: Any) -> Dict[str, Any]:
    """转换条件 cell (cell_type=201) → node type="tdx_condition"。"""
    params, _func = _CellEnvelope._extract_subobj(
        cell, "func", "tdx_func", TdxFuncModel, lambda f: 0)
    params.update(_CellEnvelope._visual_params(cell))
    return _CellEnvelope._envelope(cell, "tdx_condition", params)


def _convert_decoration_cell(cell: Any) -> Dict[str, Any]:
    """转换装饰性 cell (cell_type=1,2,3,4,6) → node type="decoration"。"""
    params: Dict[str, Any] = {"_visual_only": True,
                              "dzh_cell_type": _get_dzh_cell_type(cell)}
    params.update(_CellEnvelope._visual_params(cell))
    return _CellEnvelope._envelope(cell, "decoration", params)


# Cell 类型 → 转换函数分发表
_CELL_CONVERTERS: Dict[int, callable] = {
    200: _convert_state_pool_cell,
    201: _convert_condition_cell,
    202: _convert_candidate_cell,
    203: _convert_state_pool_cell,
}


# ═══════════════════════════════════════════════════════════════
# Flow 转换函数
# ═══════════════════════════════════════════════════════════════

# 表驱动：TDX flow 字段提取（合并 11 个 getattr 重复块）。
# 每行 = (key, default, with_tdx_prefix)；均带 tdx_<key> 回退，None → default。
_FLOW_ATTR_FIELDS = (
    ("tran", 0, True),
    ("size", 1, True),
    ("emptyps", 0, False),
    ("starttype", 0, False),
    ("starttime", 0, False),
    ("starttimetype", 0, False),
    ("starttimehms", 0, False),
    ("cxtype", 0, False),
    ("cxtime", 0, False),
    ("cxtimetype", 0, False),
    ("jgtime", 60, False),
)


def _convert_flow(flow: Any) -> Dict[str, Any]:
    """转换 FlowModel 为 edge dict（表驱动，合并 11 个 getattr 重复块）。"""
    params: Dict[str, Any] = {}
    for key, default, with_prefix in _FLOW_ATTR_FIELDS:
        val = getattr(flow, key, getattr(flow, "tdx_" + key, default))
        if val is None:
            val = default
        params[key] = val
        if with_prefix:
            params["tdx_" + key] = val
    # clr: 无 tdx_ 回退、不强制 None→default（与原实现一致）
    tdx_clr = getattr(flow, "clr", -1)
    params["tdx_clr"] = tdx_clr
    params["clr"] = tdx_clr
    params["mode"] = "move" if params["tran"] == 1 else "copy"
    source_id = str(getattr(flow, "startid", getattr(flow, "from_cell_id", "")))
    target_id = str(getattr(flow, "endid", getattr(flow, "to_cell_id", "")))
    return {
        "source": {"node_id": source_id},
        "target": {"node_id": target_id},
        "params": params,
    }


# ═══════════════════════════════════════════════════════════════
# 主转换函数
# ═══════════════════════════════════════════════════════════════

def convert_tdx_to_config(pool_meta: PoolMetaModel) -> Dict[str, Any]:
    """向后兼容包装：委托到 TdxPoolConverter.convert_tdx_to_config（G2 归入类内）。"""
    return TdxPoolConverter.convert_tdx_to_config(pool_meta)



# ── 股票代码 → setcode 映射表（0=SZ, 1=SH, 2=BJ）──────────────────────
# 后缀映射（tq_code 格式 "600000.SH"）
_SUFFIX_TO_SETCODE = {"SH": 1, "SZ": 0, "BJ": 2}
# 数字前缀映射（无后缀时按首位推断；B股 900xxx/200xxx 由调用方特判）
_PREFIX_TO_SETCODE = {"0": 0, "3": 0, "6": 1, "8": 2, "4": 2}





# ================================================================
# 导出器（原 tdx_exporter.py）
# ================================================================

# DZH cell_type → TDX type 映射统一由 dzh_type_map.json:dzh_to_tdx 提供（ConfigStore）。

# ── Default TDX colors per type ───────────────────────────────────────────────
_TDX_DEFAULT_CLR: Dict[int, int] = {
    7: 12615935,
    3: 16711680,
    8: 16711935,
}

_TDX_DEFAULT_CLRTEXT: Dict[int, int] = {
    7: 255,
    3: 16777215,
    8: 16777215,
    0: 0,
    1: 0,
    2: 0,
}

# ── solid=1 for functional cells, solid=0 for decorative ──────────────────────
_FUNCTIONAL_TDX_TYPES = frozenset({3, 7, 8})


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _get_tdx_type_export(cell: Any) -> int:
    """Map a DZH cell model to its TDX cell type.

    If the cell carries the original TDX type (tdx_type), prefer it
    to preserve the exact type through roundtrip (e.g. TDX type 1 vs 0,
    or uncommon types like 4, 5, 6).
    """
    tdx_type = getattr(cell, 'tdx_type', None)
    if tdx_type is not None:
        return int(tdx_type)
    ct = getattr(cell, 'cell_type', 0)
    _dzh_to_tdx = (get_global_config_store().get_table("dzh_type_map") if get_global_config_store() else {}).get("dzh_to_tdx", {})
    return int(_dzh_to_tdx.get(str(ct), ct))


def _pos_to_str(pos: Any) -> str:
    """Convert position model/object to 'x1,y1,x2,y2' string."""
    if pos is None:
        return "0,0,100,100"
    x1 = int(getattr(pos, 'x', 0))
    y1 = int(getattr(pos, 'y', 0))
    w  = int(getattr(pos, 'width', 0))
    h  = int(getattr(pos, 'height', 0))
    return f"{x1},{y1},{x1 + w},{y1 + h}"


def _clr_for(cell: Any, tdx_type: int) -> int:
    """Return cell colour; preserve original value including -1."""
    clr = getattr(cell, 'clr', -1)
    if clr is None:
        return _TDX_DEFAULT_CLR.get(tdx_type, -1)
    return int(clr)


def _clrtext_for(tdx_type: int) -> int:
    return _TDX_DEFAULT_CLRTEXT.get(tdx_type, 0)


def _solid_for(tdx_type: int) -> int:
    return 1 if tdx_type in _FUNCTIONAL_TDX_TYPES else 0


def _stock_setcode(code: str) -> int:
    """0=SZ, 1=SH, 2=BJ.  Heuristic: code starting with '6' → SH."""
    if not code:
        return 0
    if code.startswith("6"):
        return 1
    return 0


# ═══════════════════════════════════════════════════════════════════════════════
# Child-element builders (func / psatt / spinfo / stk)
# 已合并到 TdxPoolConverter._add_element + _StkWriter.write_stks；
# _add_func/_add_psatt/_add_spinfo/_add_stks 模块级名称委托到 _TDX_CONVERTER。
# ═══════════════════════════════════════════════════════════════════════════════

# tdx_type → 子元素构造方法列表（表驱动，替代原 if/elif 链与重复 def）
_TYPE_CHILD_BUILDERS = {
    3: [_TDX_CONVERTER.add_func],
    8: [_TDX_CONVERTER.add_psatt, _TDX_CONVERTER.add_stks],
    7: [_TDX_CONVERTER.add_spinfo, _TDX_CONVERTER.add_stks],
}

# 向后兼容：保留模块级名称委托到转换器单例
_add_func = _TDX_CONVERTER.add_func
_add_psatt = _TDX_CONVERTER.add_psatt
_add_spinfo = _TDX_CONVERTER.add_spinfo
_add_stks = _TDX_CONVERTER.add_stks


# ═══════════════════════════════════════════════════════════════════════════════
# Cell builder
# ═══════════════════════════════════════════════════════════════════════════════

def _add_cell(cells_elem: ET.Element, cell: Any) -> None:
    """Build one <cell> element from a DZH cell model and append to parent."""
    tdx_type = _get_tdx_type_export(cell)
    cell_id  = str(getattr(cell, 'id', '0'))
    attr     = int(getattr(cell, 'attr', 0))
    pos_str  = _pos_to_str(getattr(cell, 'position', None))
    clr      = _clr_for(cell, tdx_type)
    clrtext = getattr(cell, 'clrtext', None)
    if clrtext is None:
        clrtext = _clrtext_for(tdx_type)
    solid = getattr(cell, 'solid', None)
    if solid is None:
        solid = _solid_for(tdx_type)
    text     = getattr(cell, 'text', '')

    el = ET.SubElement(cells_elem, 'cell')
    el.set('id', cell_id)
    el.set('type', str(tdx_type))
    el.set('attr', str(attr))
    el.set('pos', pos_str)
    el.set('clr', str(clr))
    el.set('clrtext', str(clrtext))
    el.set('solid', str(solid))
    el.set('text', text)

    # Type‑specific children（表驱动）
    for builder in _TYPE_CHILD_BUILDERS.get(tdx_type, ()):
        builder(el, cell)


# ═══════════════════════════════════════════════════════════════════════════════
# Flow builder
# ═══════════════════════════════════════════════════════════════════════════════

def _flow_tran(flow: FlowModel) -> int:
    """move / force_move → tran=1; copy/overwrite/pass_through → tran=0."""
    mode = flow.mode_name
    return 1 if mode in ('move', 'force_move') else 0


def _add_flow(flows_elem: ET.Element, flow: FlowModel) -> None:
    """Build one <flow> element from a FlowModel and append to parent."""
    tran  = _flow_tran(flow)
    clr   = getattr(flow, 'clr', -1)
    # 保留原始 clr 值（包括 -1），不替换为默认值 127

    el = ET.SubElement(flows_elem, 'flow')
    el.set('startid',       str(flow.from_cell_id))
    el.set('endid',         str(flow.to_cell_id))
    el.set('clr',           str(clr))
    # 优先使用 TDX 原始字段
    size_val = getattr(flow, 'tdx_size', None)
    if size_val is None:
        size_val = 1

    tran_val = getattr(flow, 'tdx_tran', None)
    if tran_val is None:
        tran_val = tran

    emptyps_val = getattr(flow, 'tdx_emptyps', None)
    if emptyps_val is None:
        emptyps_val = 0

    starttype_val = getattr(flow, 'tdx_starttype', None)
    if starttype_val is None:
        starttype_val = getattr(flow, 'begin_type', 0)

    starttime_val = getattr(flow, 'tdx_starttime', None)
    if starttime_val is None:
        starttime_val = getattr(flow, 'begin_param', 0)

    starttimetype_val = getattr(flow, 'tdx_starttimetype', None)
    if starttimetype_val is None:
        starttimetype_val = 0

    starttimehms_val = getattr(flow, 'tdx_starttimehms', None)
    if starttimehms_val is None:
        starttimehms_val = 0

    cxtype_val = getattr(flow, 'tdx_cxtype', None)
    if cxtype_val is None:
        cxtype_val = getattr(flow, 'end_type', 0)

    cxtime_val = getattr(flow, 'tdx_cxtime', None)
    if cxtime_val is None:
        cxtime_val = getattr(flow, 'end_param', 2147483647)

    cxtimetype_val = getattr(flow, 'tdx_cxtimetype', None)
    if cxtimetype_val is None:
        cxtimetype_val = 0

    jgtime_val = getattr(flow, 'tdx_jgtime', None)
    if jgtime_val is None:
        jgtime_val = getattr(flow, 'interval_sec', 60)

    el.set('size',          str(size_val))
    el.set('tran',          str(tran_val))
    el.set('emptyps',       str(emptyps_val))
    el.set('starttype',     str(starttype_val))
    el.set('starttime',     str(starttime_val))
    el.set('starttimetype', str(starttimetype_val))
    el.set('starttimehms',  str(starttimehms_val))
    el.set('cxtype',        str(cxtype_val))
    el.set('cxtime',        str(cxtime_val))
    el.set('cxtimetype',    str(cxtimetype_val))
    el.set('jgtime',        str(jgtime_val))


# ═══════════════════════════════════════════════════════════════════════════════
# Pretty‑print helper
# ═══════════════════════════════════════════════════════════════════════════════

def _indent(elem: ET.Element, level: int = 0) -> None:
    """Recursively indent XML elements for readability."""
    pad   = "\n" + "  " * level
    inner = pad + "  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = inner
        if not elem.tail or not elem.tail.strip():
            elem.tail = pad
        for child in elem:
            _indent(child, level + 1)
        if not child.tail or not child.tail.strip():
            child.tail = pad
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = pad


# ═══════════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════════

def export_tdx_xml(pool_config: PoolMetaModel, filepath: str,
                   recalc_nextid: bool = True) -> None:
    """Export a unified PoolMetaModel to a TDX XML file.

    Parameters
    ----------
    pool_config : PoolMetaModel
        The internal pool model to export.
    filepath : str
        Output file path (will be written with GB2312 encoding).
    recalc_nextid : bool, optional
        When True (default), recalculate nextid as max(cell_id)+1 if the
        stored value is invalid (<=0 or <= max cell id).  When False, the
        stored nextid is used verbatim — useful for strict roundtrip
        verification where the original value must be preserved exactly.
    """
    # ── nextid ──────────────────────────────────────────────────────────────
    nextid = getattr(pool_config, 'nextid', 0)
    if recalc_nextid:
        max_cid = 0
        for cell in pool_config.cells:
            try:
                cid = int(getattr(cell, 'id', '0'))
                if cid > max_cid:
                    max_cid = cid
            except (ValueError, TypeError):
                pass
        if nextid <= 0 or nextid <= max_cid:
            nextid = max_cid + 1

    backcolor = pool_config.backcolor

    # ── Build XML tree ────────────────────────────────────────────────────────
    root = ET.Element('root')
    pool = ET.SubElement(root, 'pool')
    pool.set('nextid', str(nextid))
    pool.set('backcolor', str(backcolor))

    cells_elem = ET.SubElement(pool, 'cells')
    flows_elem = ET.SubElement(pool, 'flows')

    for cell in pool_config.cells:
        _add_cell(cells_elem, cell)

    for flow in pool_config.flows:
        _add_flow(flows_elem, flow)

    _indent(root)

    # ── Serialize: GB2312 encoding, own declaration ──────────────────────────
    xml_bytes = ET.tostring(root, encoding='gb2312', xml_declaration=False)
    declaration = b'<?xml version="1.0" encoding="GB2312"?>\n'

    with open(filepath, 'wb') as fh:
        fh.write(declaration)
        fh.write(xml_bytes)


# Convenience alias


# ======================================================================
# JSON/XML 转换器（原 converters/json_xml.py）
# ======================================================================



# ================================================================
# JSON 转换器（原 json_converter.py）
# ================================================================

# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════

def _safe_get(obj: Any, attr: str, default: Any = None) -> Any:
    """安全获取属性值，支持 dict 和对象两种访问方式。"""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


def _position_to_dict(pos: Any) -> Dict[str, int]:
    """将 PositionModel 或 position dict 转换为标准字典。"""
    if pos is None:
        return {"x": 0, "y": 0, "width": 0, "height": 0}
    if isinstance(pos, dict):
        return {
            "x": pos.get("x", 0),
            "y": pos.get("y", 0),
            "width": pos.get("width", 0),
            "height": pos.get("height", 0),
        }
    if isinstance(pos, PositionModel):
        return {
            "x": pos.x,
            "y": pos.y,
            "width": pos.width,
            "height": pos.height,
        }
    # 尝试属性访问
    return {
        "x": getattr(pos, "x", 0),
        "y": getattr(pos, "y", 0),
        "width": getattr(pos, "width", 0),
        "height": getattr(pos, "height", 0),
    }


def _model_to_serializable(obj: Any) -> Any:
    """将 Pydantic 模型或 DynamicCellModel/FlowModel 转换为可 JSON 序列化的值。"""
    if obj is None:
        return None
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, (int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {k: _model_to_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_model_to_serializable(item) for item in obj]
    if isinstance(obj, PositionModel):
        return _position_to_dict(obj)
    if isinstance(obj, (TdxPsattModel, TdxFuncModel, TdxSpinfoModel)):
        return obj.model_dump()
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            pass
    if hasattr(obj, "to_dict"):
        try:
            return obj.to_dict()
        except Exception:
            pass
    # 兜底：尝试转为字符串
    try:
        return str(obj)
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════
# Cell / Flow 转换（PoolMetaModel → JSON nodes/edges）
# ═══════════════════════════════════════════════════════════════

# DynamicCellModel 中需要排除的标准字段，不放入 params
_CELL_STANDARD_KEYS = {
    "id", "type", "cell_type", "text", "attr", "pos", "position",
    "clr", "clrtext", "solid",
}

# DynamicFlowModel 中需要排除的标准字段
_FLOW_STANDARD_KEYS = {
    "from", "to", "attr", "begin", "begint", "end", "endt",
    "interval", "clr", "mid", "count",
    "from_cell_id", "to_cell_id", "begin_type", "begin_param",
    "end_type", "end_param", "interval_sec",
}


def _normalize_dzh_node(node: Dict[str, Any]) -> Dict[str, Any]:
    """将 parse_dzh_xml 返回的节点 dict 规范化为 JSON 标准格式。

    parse_dzh_xml 返回的节点结构:
        {id, type, label, dzh_cell_type, params: {...}, position: {...}, ...}

    JSON 标准格式:
        {id, type, label, params: {...}, position: {...}}
    """
    params = dict(node.get("params", {}))

    # 确保 params 中的所有值都是可序列化的
    serializable_params = {}
    for k, v in params.items():
        serializable_params[k] = _model_to_serializable(v)

    # 保留 dzh_cell_type 到 params 中（用于往返还原）
    dzh_cell_type = node.get("dzh_cell_type")
    if dzh_cell_type is not None:
        serializable_params["dzh_cell_type"] = dzh_cell_type

    # 保留 _visual_only 标记
    if node.get("_visual_only"):
        serializable_params["_visual_only"] = True

    return {
        "id": str(node.get("id", "")),
        "type": str(node.get("dzh_cell_type", node.get("type", 0))),
        "label": node.get("label", node.get("text", "")),
        "params": serializable_params,
        "position": _position_to_dict(node.get("position")),
    }


def _normalize_dzh_edge(edge: Dict[str, Any]) -> Dict[str, Any]:
    """将 parse_dzh_xml 返回的边 dict 规范化为 JSON 标准格式。

    parse_dzh_xml 返回的边结构:
        {id, source: {node_id}, target: {node_id}, params: {...}}

    也兼容 pool_config 中的边结构:
        {id, from, to, params: {...}}

    JSON 标准格式:
        {id, from, to, params: {...}}
    """
    # 提取 from/to：兼容 source/target dict 和 from/to 字符串两种格式
    source = edge.get("source")
    target = edge.get("target")
    if source is not None:
        if isinstance(source, dict):
            from_id = source.get("node_id", "")
        else:
            from_id = str(source)
    elif "from" in edge:
        from_id = str(edge["from"])
    else:
        from_id = ""

    if target is not None:
        if isinstance(target, dict):
            to_id = target.get("node_id", "")
        else:
            to_id = str(target)
    elif "to" in edge:
        to_id = str(edge["to"])
    else:
        to_id = ""

    params = dict(edge.get("params", {}))

    # 确保 params 中的所有值都是可序列化的
    serializable_params = {}
    for k, v in params.items():
        serializable_params[k] = _model_to_serializable(v)

    # 生成边 ID
    edge_id = edge.get("id", f"e_{from_id}_{to_id}")

    return {
        "id": str(edge_id),
        "from": str(from_id),
        "to": str(to_id),
        "params": serializable_params,
    }


def _cell_to_node(cell: Any) -> Dict[str, Any]:
    """将 DynamicCellModel 转换为 JSON 节点字典。

    输出格式:
        {
            "id": "...",
            "type": "7",
            "label": "备选池",
            "params": {...},
            "position": {"x": 0, "y": 0, "width": 100, "height": 100}
        }
    """
    if isinstance(cell, dict):
        # 已经是字典格式，使用 _normalize_dzh_node 规范化
        return _normalize_dzh_node(cell)

    # DynamicCellModel 对象
    cell_id = str(cell.get("id", ""))
    cell_type = cell.get("cell_type", cell.get("type", 0))
    label = cell.get("text", "")
    position = _position_to_dict(cell.get("position"))

    # 收集 params：从 cell 的所有数据中提取非标准字段
    params: Dict[str, Any] = {}

    if isinstance(cell, DynamicCellModel):
        # 从 _data 和 _extra 中提取非标准字段
        all_keys = cell.keys()
        for key in all_keys:
            if key in _CELL_STANDARD_KEYS:
                continue
            if key.startswith("_"):
                continue
            val = cell.get(key)
            params[key] = _model_to_serializable(val)
    else:
        # 其他类型的 cell 对象
        for attr_name in dir(cell):
            if attr_name.startswith("_"):
                continue
            if attr_name in _CELL_STANDARD_KEYS:
                continue
            try:
                val = getattr(cell, attr_name)
                if callable(val):
                    continue
                params[attr_name] = _model_to_serializable(val)
            except Exception:
                continue

    return {
        "id": cell_id,
        "type": str(cell_type),
        "label": label,
        "params": params,
        "position": position,
    }


def _flow_to_edge(flow: Any) -> Dict[str, Any]:
    """将 DynamicFlowModel 转换为 JSON 边字典。

    输出格式:
        {
            "id": "e1",
            "from": "1",
            "to": "2",
            "params": {...}
        }
    """
    if isinstance(flow, dict):
        # 已经是字典格式，使用 _normalize_dzh_edge 规范化
        return _normalize_dzh_edge(flow)

    # DynamicFlowModel 对象
    from_id = str(flow.get("from_cell_id", flow.get("from", "")))
    to_id = str(flow.get("to_cell_id", flow.get("to", "")))

    # 生成边 ID
    flow_id = str(flow.get("mid", "")) if flow.get("mid") else f"e_{from_id}_{to_id}"

    # 收集 params
    params: Dict[str, Any] = {}
    if isinstance(flow, DynamicFlowModel):
        all_keys = flow.keys()
        for key in all_keys:
            if key in _FLOW_STANDARD_KEYS:
                continue
            if key.startswith("_"):
                continue
            val = flow.get(key)
            params[key] = _model_to_serializable(val)
    else:
        for attr_name in dir(flow):
            if attr_name.startswith("_"):
                continue
            if attr_name in _FLOW_STANDARD_KEYS:
                continue
            try:
                val = getattr(flow, attr_name)
                if callable(val):
                    continue
                params[attr_name] = _model_to_serializable(val)
            except Exception:
                continue

    return {
        "id": flow_id,
        "from": from_id,
        "to": to_id,
        "params": params,
    }


# ═══════════════════════════════════════════════════════════════
# _normalize_pool_data: 三种格式统一转换
# ═══════════════════════════════════════════════════════════════

def _normalize_pool_data(pool_data: Any) -> Dict[str, Any]:
    """将三种输入格式统一转换为标准化字典 {pool_meta, nodes, edges}。

    支持的输入格式:
      1. pool_config dict: {name, pool_type, nodes, edges}
      2. 解析后的 XML dict: {name, pool_meta, nodes, edges, ...}
      3. PoolMetaModel 对象

    Returns:
        {
            "pool_meta": {"name": ..., "pool_type": ..., "ver": ..., "mode": ..., "backcolor": ...},
            "nodes": [...],
            "edges": [...]
        }
    """
    # 格式3: PoolMetaModel 对象
    if isinstance(pool_data, PoolMetaModel):
        pool_meta = {
            "pool_type": pool_data.pool_type,
            "ver": pool_data.ver,
            "mode": pool_data.mode,
            "backcolor": pool_data.backcolor,
        }
        if pool_data.ency is not None:
            pool_meta["ency"] = pool_data.ency
        if pool_data.warning is not None:
            pool_meta["warning"] = pool_data.warning
        if pool_data.system is not None:
            pool_meta["system"] = pool_data.system

        nodes = [_cell_to_node(cell) for cell in pool_data.cells]
        edges = [_flow_to_edge(flow) for flow in pool_data.flows]

        return {
            "pool_meta": pool_meta,
            "nodes": nodes,
            "edges": edges,
        }

    # 格式1/2: dict 格式
    if isinstance(pool_data, dict):
        # 格式2: 包含 pool_meta 键（来自 parse_dzh_xml）
        if "pool_meta" in pool_data:
            raw_pool_meta = pool_data["pool_meta"]
            pool_meta = {
                "pool_type": raw_pool_meta.get("type", "ss-pool"),
                "ver": raw_pool_meta.get("ver", "1.0"),
                "mode": raw_pool_meta.get("mode", "1"),
                "backcolor": raw_pool_meta.get("backcolor", 16777216),
            }
            # 保留可选字段
            for opt_key in ("ency", "warning", "system", "nextid"):
                if raw_pool_meta.get(opt_key) is not None:
                    pool_meta[opt_key] = raw_pool_meta[opt_key]

            # name 从顶层取
            pool_meta["name"] = pool_data.get("name", "")

            nodes = pool_data.get("nodes", [])
            edges = pool_data.get("edges", [])

            # 规范化节点和边：统一使用 _normalize_dzh_node / _normalize_dzh_edge
            nodes = [_cell_to_node(n) for n in nodes]
            edges = [_flow_to_edge(e) for e in edges]

            return {
                "pool_meta": pool_meta,
                "nodes": nodes,
                "edges": edges,
            }

        # 格式1: pool_config dict（来自 run_pool 输出或 conftest 工厂）
        if "nodes" in pool_data or "edges" in pool_data:
            pool_type = pool_data.get("pool_type", "dzh")
            pool_meta = {
                "name": pool_data.get("name", ""),
                "pool_type": pool_type,
                "ver": "1.0",
                "mode": "1",
                "backcolor": 16777216,
            }

            # 如果 pool_config 中有 pool_meta，保留其中的 nextid 和 backcolor
            raw_meta = pool_data.get("pool_meta", {})
            if isinstance(raw_meta, dict):
                if raw_meta.get("nextid") is not None:
                    pool_meta["nextid"] = raw_meta["nextid"]
                if raw_meta.get("backcolor") is not None:
                    pool_meta["backcolor"] = raw_meta["backcolor"]

            nodes = pool_data.get("nodes", [])
            edges = pool_data.get("edges", [])

            # 规范化节点和边
            nodes = [_cell_to_node(n) for n in nodes]
            edges = [_flow_to_edge(e) for e in edges]

            return {
                "pool_meta": pool_meta,
                "nodes": nodes,
                "edges": edges,
            }

    raise ValueError(
        f"无法识别的 pool_data 格式: {type(pool_data).__name__}。"
        "支持 pool_config dict、解析后的 XML dict 或 PoolMetaModel 对象。"
    )


# ═══════════════════════════════════════════════════════════════
# 导出函数
# ═══════════════════════════════════════════════════════════════

def export_pool_to_json(pool_data: Any, file_path: Optional[str] = None) -> str:
    """将股票池配置导出为 JSON 字符串。

    支持三种输入格式:
      1. pool_config dict: {name, pool_type, nodes, edges}
      2. 解析后的 XML dict: {name, pool_meta, nodes, edges, ...}
      3. PoolMetaModel 对象

    Args:
        pool_data: 股票池数据，支持上述三种格式。
        file_path: 可选的文件路径，若提供则将 JSON 写入文件（UTF-8 编码）。

    Returns:
        JSON 字符串（ensure_ascii=False, indent=2）。

    Raises:
        ValueError: pool_data 格式无法识别时抛出。
    """
    normalized = _normalize_pool_data(pool_data)

    result = {
        "version": 1,
        "pool_meta": normalized["pool_meta"],
        "nodes": normalized["nodes"],
        "edges": normalized["edges"],
    }

    json_str = json.dumps(result, ensure_ascii=False, indent=2, default=_json_default)

    if file_path is not None:
        dir_name = os.path.dirname(os.path.abspath(file_path))
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".tmp", dir=dir_name)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json_str)
            os.replace(tmp_path, file_path)
        except Exception:
            # 清理临时文件
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            raise

    return json_str


def _json_default(obj: Any) -> Any:
    """JSON 序列化兜底函数，处理 set 等不可直接序列化的类型。"""
    if isinstance(obj, set):
        return sorted(obj, key=str)
    return str(obj)


# ═══════════════════════════════════════════════════════════════
# 导入函数
# ═══════════════════════════════════════════════════════════════

def import_pool_from_json(json_content: Optional[str] = None,
                          file_path: Optional[str] = None) -> Dict[str, Any]:
    """从 JSON 内容或文件导入股票池配置。

    返回与 run_pool() 兼容的 pool_config dict:
        {
            "name": "...",
            "pool_type": "tdx",
            "nodes": [...],
            "edges": [...]
        }

    Args:
        json_content: JSON 字符串内容。
        file_path: JSON 文件路径。

    Returns:
        pool_config 字典。

    Raises:
        ValueError: 内容为空、JSON 格式无效、版本不支持时抛出。
    """
    if json_content is None and file_path is None:
        raise ValueError("必须提供 json_content 或 file_path 参数之一")

    if json_content is None:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                json_content = f.read()
        except FileNotFoundError:
            raise ValueError(f"文件不存在: {file_path}")
        except Exception as e:
            raise ValueError(f"读取文件失败: {file_path}, 错误: {e}")

    if not json_content or not json_content.strip():
        raise ValueError("JSON 内容为空")

    try:
        data = json.loads(json_content)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 格式无效: {e}")

    if not isinstance(data, dict):
        raise ValueError(f"JSON 根元素必须是对象(dict)，实际为 {type(data).__name__}")

    # 版本校验
    if "version" not in data:
        raise ValueError("JSON 缺少 version 字段")
    version = data["version"]
    if version not in (1, 2):
        raise ValueError(f"不支持的版本号: {version}，当前仅支持版本 1 或 2")

    # 提取 pool_meta
    pool_meta = data.get("pool_meta", {})
    name = pool_meta.get("name", "")
    pool_type = pool_meta.get("pool_type", "dzh")

    raw_nodes = data.get("nodes", [])
    raw_edges = data.get("edges", [])

    # 将 JSON 标准格式转换为前端期望的格式
    # JSON 标准格式: {id, type: "202", label, params: {dzh_cell_type: 202, ...}, position}
    # 前端期望格式: {id, type: "market_source", label, dzh_cell_type: 202, params: {...}, position}
    # 边: JSON {from, to} → 前端 {source: {node_id}, target: {node_id}}
    nodes = []
    for n in raw_nodes:
        node = dict(n)
        params = dict(node.get("params", {}))
        # 从 params 中提取 dzh_cell_type 到顶层
        if "dzh_cell_type" in params:
            node["dzh_cell_type"] = params.pop("dzh_cell_type")
        # 如果 type 是字符串形式的数字，转为 dzh_cell_type 并保留 type 以支持往返还原
        if isinstance(node.get("type"), str) and node["type"].isdigit():
            if "dzh_cell_type" not in node:
                node["dzh_cell_type"] = int(node["type"])
            # 保留 type 字段，避免 JSON 往返丢失
            # node["type"] 保持原始字符串值
        node["params"] = params
        nodes.append(node)

    edges = []
    for e in raw_edges:
        edge = dict(e)
        params = dict(edge.get("params", {}))
        # 将 {from, to} 转换为 {source: {node_id}, target: {node_id}}
        if "from" in edge and "source" not in edge:
            edge["source"] = {"node_id": str(edge.pop("from"))}
        if "to" in edge and "target" not in edge:
            edge["target"] = {"node_id": str(edge.pop("to"))}
        edge["params"] = params
        edges.append(edge)

    result = {
        "name": name,
        "pool_type": pool_type,
        "nodes": nodes,
        "edges": edges,
    }

    # 保留 pool_meta 中的额外字段（用于往返还原）
    result["pool_meta"] = {}
    for key in ("nextid", "backcolor", "ver", "mode", "ency", "warning", "system"):
        if key in pool_meta:
            result["pool_meta"][key] = pool_meta[key]
    result["pool_meta"]["type"] = pool_type

    return result

# ================================================================
# XML 构建器（原 xml_builder.py）
# ================================================================

_BASE = Path(__file__).parent
_CONFIG = _BASE / "config"


# Task 9.10: _load_json_cache 已删除，改为通过 ConfigStore.get_table 加载
_get_xml_mapping = lambda: (get_global_config_store().get_table("xml_mapping") if get_global_config_store() else {})

_STOCK_NAMES = {}
try:
    with open(_CONFIG / "data" / "mock_data.json", encoding="utf-8") as f:
        _STOCK_NAMES = json.load(f).get('stock_names', {})
except (OSError, json.JSONDecodeError) as ex:
    # fail-fast 标记：加载失败时记录 warning（非 try/except pass），保留空 dict 以兼容查表语义
    logger.warning("加载 mock_data.json 的 stock_names 失败: %s（_get_stock_name 将回退到原始 code）", ex, exc_info=True)


def _get_stock_name(market, code):
    return _STOCK_NAMES.get(code, code)


def _resolve_field(obj, path, default=None):
    if obj is None:
        return default
    cur = obj
    for p in path.split('.'):
        cur = cur.get(p) if isinstance(cur, dict) else getattr(cur, p, None)
        if cur is None:
            return default
    return cur


def _model_to_dict(obj):
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, 'model_dump'):
        return obj.model_dump()
    if hasattr(obj, 'dict'):
        return obj.dict()
    return obj


def _indent_xml(elem, level=0):
    pad = "\n" + "  " * level
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = pad + "  "
        if not elem.tail or not elem.tail.strip():
            elem.tail = pad
        for child in elem:
            _indent_xml(child, level + 1)
        if not child.tail or not child.tail.strip():
            child.tail = pad
    elif level and (not elem.tail or not elem.tail.strip()):
        elem.tail = pad


def _resolve_attr_tdx(field, attr_def, ctx):
    compute = attr_def.get('compute')
    if compute:
        return ctx.get(compute)
    if '.' in field:
        obj_part, key = field.rsplit('.', 1)
        src = ctx.get(obj_part)
        if isinstance(src, dict):
            val = src.get(key)
            if val is None and attr_def.get('alt_field'):
                alt = attr_def['alt_field']
                _, alt_key = alt.rsplit('.', 1) if '.' in alt else (None, alt)
                val = src.get(alt_key) if alt_key else ctx.get('node', {}).get(alt)
            return val
        return _resolve_field(ctx.get('node', ctx.get('edge', {})), field, attr_def.get('default'))
    return ctx.get('node', ctx.get('edge', {})).get(field, attr_def.get('default'))


def _apply_attr_defaults(val, attr_def, tdx_type=None):
    if val is None and 'type_conditional_default' in attr_def:
        tcd = attr_def['type_conditional_default']
        val = tcd['value'] if tdx_type in tcd['for_types'] else tcd['else_value']
    if attr_def.get('empty_as_default') and (val is None or val == ''):
        val = attr_def.get('default', '')
    return val


def _build_tdx_xml(pool_data: dict, filepath: str) -> None:
    """表驱动构建TDX XML（薄包装：委托 _TDX_CONVERTER.export_pool 模板方法）。"""
    _TDX_CONVERTER._current_filepath = filepath
    return _TDX_CONVERTER.export_pool(pool_data)


_TRANSFORMS = {
    'str': lambda v, _: str(v),
    'clr_to_str': lambda v, _: str(v) if v != -1 else '',
    'clrtext_to_str': lambda v, _: str(v) if v else '',
}


def _tdx_pool_to_frontend(tdx_pool, name: str) -> dict:
    """向后兼容包装：委托到 TdxPoolConverter._to_frontend（G2 归入类内）。"""
    return _TDX_CONVERTER._to_frontend(tdx_pool, name)


def _load_tdx_pool_config(xml_path):
    """向后兼容包装：委托到 TdxPoolConverter.load_tdx_pool_config（G2 归入类内）。"""
    return TdxPoolConverter.load_tdx_pool_config(xml_path)

