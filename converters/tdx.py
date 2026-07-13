"""tdx.py - TDX 股票池全流程：XML 解析 / 转换 / 执行 / 导出（合并自 tdx_xml_raw / tdx_converter / tdx_executor / tdx_exporter）。"""
from __future__ import annotations

import xml.etree.ElementTree as ET
import logging
import operator
import os
import sys
import json
import random
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..services.candidate_pool import CandidatePoolResolver

try:
    from ._common import safe_int, safe_float, _hms_to_seconds
except ImportError:
    from _common import safe_int, safe_float, _hms_to_seconds

logger = logging.getLogger(__name__)


def _run_async(coro_factory):
    """同步运行异步协程，兼容已有事件循环的场景。

    FormulaRouter.eval_batch 为 async，而 TdxPoolExecutor 为同步入口，
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


# ================================================================
# XML 原始解析（原 tdx_xml_raw.py）
# ================================================================

try:
    from ..core.schemas import (
        TdxFuncModel, TdxPsattModel, TdxSpinfoModel, TdxStkModel,
        TdxCellModel, TdxFlowModel, TdxPoolMetaModel,
        PoolMetaModel,
        DynamicCellModel, DynamicFlowModel,
        PositionModel, StockSnapshotModel,
        TDX_TO_DZH_CELL_TYPE,
    )
except ImportError:
    from schemas import (
        TdxFuncModel, TdxPsattModel, TdxSpinfoModel, TdxStkModel,
        TdxCellModel, TdxFlowModel, TdxPoolMetaModel,
        PoolMetaModel,
        DynamicCellModel, DynamicFlowModel,
        PositionModel, StockSnapshotModel,
        TDX_TO_DZH_CELL_TYPE,
    )

# 表驱动 noperate 委托：复用 evaluators.py 的通用比较器与排名器，
# 不在转换层另起 if/elif 体系或 lambda 字典
try:
    from ..core.evaluators import (
        _eval_op, _build_op_ctx, _resolve_rank, _NOPERATE_RULES, _RANK_MODES,
    )
except ImportError:
    try:
        from core.evaluators import (
            _eval_op, _build_op_ctx, _resolve_rank, _NOPERATE_RULES, _RANK_MODES,
        )
    except ImportError:
        _eval_op = None
        _build_op_ctx = None
        _resolve_rank = None
        _NOPERATE_RULES = {}
        _RANK_MODES = {}


def _get_compare_type(noperate) -> str:
    """从 _NOPERATE_RULES 表查询 compare 类型（数据驱动，替代 if noperate ==）。

    compare 字段值编码比较语义差异：
        abs_lt（等于）/ gt（大于）/ lt（小于）/ cross（上穿下破）/
        rank（排名）/ inflection（上拐下拐）
    """
    rule = _NOPERATE_RULES.get(str(noperate))
    return rule.get('compare', '') if rule else ''


# ═══════════════════════════════════════════════════════════════
# 增强的内部模型子类，携带 TDX 特有的扩展字段
# ═══════════════════════════════════════════════════════════════

# 使用 DynamicCellModel 替代已删除的特定模型类。
# TDX 特有字段（tdx_psatt、tdx_func 等）通过属性注入方式添加到 DynamicCellModel 实例上。

def _make_tdx_cell(cell_type: int, **data) -> DynamicCellModel:
    """创建携带 TDX 扩展字段的 DynamicCellModel 实例。
    
    TDX 特有字段（如 tdx_func, tdx_psatt, tdx_spinfo, tdx_stocks）作为
    额外属性附加到模型实例上，透传给后续引擎使用。
    """
    model = DynamicCellModel.from_dict({**data, 'type': cell_type})
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
# 辅助函数
# ═══════════════════════════════════════════════════════════════

def _parse_tdx_pos(pos_str):
    """
    解析 TDX pos 格式 "x1,y1,x2,y2"。
    返回 (pos_x, pos_y, width, height)。
    """
    if not pos_str:
        return 0, 0, 0, 0
    try:
        parts = [int(x.strip()) for x in pos_str.split(",")]
        if len(parts) == 4:
            x1, y1, x2, y2 = parts
            return x1, y1, x2 - x1, y2 - y1
    except (ValueError, TypeError):
        pass
    return 0, 0, 0, 0


def _decode_tdx_xml(raw_bytes: bytes) -> str:
    """
    尝试多种中文编码解码 TDX XML 的字节内容。
    优先尝试 GB2312，然后依次回退到 GBK、GB18030、UTF-8。
    """
    for enc in ("gb2312", "gbk", "gb18030", "utf-8"):
        try:
            return raw_bytes.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    # 最终回退：强制 UTF-8 替换不可解码字节
    return raw_bytes.decode("utf-8", errors="replace")


# ═══════════════════════════════════════════════════════════════
# 解析函数
# ═══════════════════════════════════════════════════════════════

# ── 配置表目录与表驱动加载器（供 XML 解析与执行器共用） ─────────────────
# 配置表位于 meta_core/config/，模块加载时读入内存，避免硬编码字典
_CONFIG_DIR = Path(__file__).parent.parent / "config"


def _load_tdx_element_schemas(filename: str) -> Dict[str, dict]:
    """从 JSON 配置表读取元素解析 schema，构建 element_name->schema 映射。"""
    path = _CONFIG_DIR / filename
    try:
        data = json.loads(path.read_text("utf-8"))
        return data.get("elements", {})
    except Exception as e:
        logger.warning("加载 TDX 元素 schema %s 失败: %s", filename, e)
        return {}


def _load_tdx_period_map(filename: str) -> Dict[int, str]:
    """从 JSON 配置表读取 period_map 段，构建 int->周期字符串 映射（单份定义）。"""
    path = _CONFIG_DIR / filename
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


def _parse_func_element(func_elem: ET.Element) -> Dict[str, Any]:
    """解析 <func> 子元素为字典。"""
    func_data: Dict[str, Any] = {}
    # 表驱动：int_fields 由 config/tdx_element_schemas.json 声明
    int_fields = _get_element_int_fields("func")
    for k, v in func_elem.attrib.items():
        if k in int_fields:
            func_data[k] = safe_int(v, 0)
        elif k == "fsecond":
            func_data[k] = safe_float(v, 0.0)
        else:
            func_data[k] = v
    return func_data


def _parse_psatt_element(psatt_elem: ET.Element) -> Dict[str, Any]:
    """解析 <psatt> 子元素为字典。

    V7.x 新增字段:
      - bsavehis: 保存历史记录 (默认 0，兼容旧版)
    V6.x 特有字段:
      - nsyssound: 系统声音标志 (V7.x 中已被 bsavehis 替代)
    """
    psatt_data: Dict[str, Any] = {}
    # 表驱动：int_fields 由 config/tdx_element_schemas.json 声明
    int_fields = _get_element_int_fields("psatt")
    for k, v in psatt_elem.attrib.items():
        if k in int_fields:
            psatt_data[k] = safe_int(v, 0)
        else:
            psatt_data[k] = v

    # 向后兼容: 如果没有 bsavehis 字段（旧版 V6.x），默认值为 0
    if "bsavehis" not in psatt_data:
        psatt_data["bsavehis"] = 0

    # 区分版本: nsyssound 是 V6.x 字段，bsavehis 是 V7.x 字段
    # 两者可能同时存在或只存在一个，均保留以供后续逻辑判断
    return psatt_data


def _parse_spinfo_element(spinfo_elem: ET.Element) -> Dict[str, Any]:
    """解析 <spinfo> 子元素为字典，并推导 market、sector_type 和 type_label 字段。

    V7.x spinfo.type 属性:
      - 0: 全市场自动选股
      - 2: 全部A股
      - 3: 自选股
      - 4: 自定义板块

    存储到节点的 params.spinfo.type 字段。
    """
    spinfo_data: Dict[str, Any] = {}
    # 表驱动：int_fields 由 config/tdx_element_schemas.json 声明
    int_fields = _get_element_int_fields("spinfo")
    for k, v in spinfo_elem.attrib.items():
        if k in int_fields:
            spinfo_data[k] = safe_int(v, 0)
        else:
            spinfo_data[k] = v

    # 使用 SPINFO_TYPE_MAP 推导 type_label 字段
    spinfo_type = spinfo_data.get("type", 0)
    type_info = SPINFO_TYPE_MAP.get(spinfo_type,
                                     {"label": "未知类型", "market": ""})
    spinfo_data["type_label"] = type_info["label"]
    # 只在 XML 中没有 market 属性时才使用 SPINFO_TYPE_MAP 的默认值
    if "market" not in spinfo_data:
        spinfo_data["market"] = type_info["market"]

    # type=4 自定义板块时，市场取决于板块内容（可能为空）
    if spinfo_type == 4:
        spinfo_data["market"] = ""

    # 根据 customblockname 推导 sector_type 字段
    # 表驱动：sector_type_map 由 config/tdx_element_schemas.json 声明
    customblockname = spinfo_data.get("customblockname", "")
    sector_type_map = _get_element_sector_type_map("spinfo")
    spinfo_data["sector_type"] = sector_type_map.get(customblockname, 0)

    return spinfo_data


def _parse_stk_elements(cell_elem: ET.Element) -> List[Dict[str, Any]]:
    """解析 <cell> 内的所有 <stk> 子元素。"""
    stks = []
    for stk_elem in cell_elem.findall("stk"):
        stk_data: Dict[str, Any] = {}
        for k, v in stk_elem.attrib.items():
            if k == "setcode":
                stk_data[k] = safe_int(v, 0)
            else:
                stk_data[k] = v
        stks.append(stk_data)
    return stks


def parse_tdx_xml(filepath: str) -> TdxPoolMetaModel:
    """
    解析 TDX 股票池 XML 文件，返回 TdxPoolMetaModel。

    支持 V6.x 和 V7.x 双版本自动检测与兼容解析。

    Args:
        filepath: TDX XML 文件路径。

    Returns:
        TdxPoolMetaModel 实例，包含解析后的 cells 和 flows。
        模型附带 xml_version 属性标识检测到的版本 ('v6' 或 'v7')。
    """
    with open(filepath, "rb") as f:
        raw_bytes = f.read()

    text = _decode_tdx_xml(raw_bytes)
    root = ET.fromstring(text)

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

    # 解析 flows
    flows: List[TdxFlowModel] = []
    flows_elem = pool_elem.find("flows")
    if flows_elem is not None:
        for flow_elem in flows_elem.findall("flow"):
            flow_data: Dict[str, Any] = {}
            for k, v in flow_elem.attrib.items():
                flow_data[k] = safe_int(v, 0)
            flows.append(TdxFlowModel.from_dict(flow_data))

    return TdxPoolMetaModel(nextid=nextid, backcolor=backcolor, cells=cells, flows=flows)


# ═══════════════════════════════════════════════════════════════
# 转换函数：TDX 池模型 → 内部统一池模型
# ═══════════════════════════════════════════════════════════════

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

    for tdx_cell in tdx_pool.cells:
        internal_type = TDX_TO_DZH_CELL_TYPE.get(tdx_cell.type, 1)

        pos = PositionModel(
            x=tdx_cell.pos_x,
            y=tdx_cell.pos_y,
            width=tdx_cell.width,
            height=tdx_cell.height,
        )

        if internal_type == 201:
            # type=3 → 条件单元
            cell = _make_tdx_cell(
                201,
                id=str(tdx_cell.id),
                attr=tdx_cell.attr,
                position=pos,
                clr=tdx_cell.clr,
                text=tdx_cell.text,
                tdx_func=tdx_cell.func,
                tdx_id=tdx_cell.id,
                tdx_type=tdx_cell.type,
                clrtext=tdx_cell.clrtext,
                solid=tdx_cell.solid,
            )

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

            cell = _make_tdx_cell(
                202,
                id=str(tdx_cell.id),
                attr=tdx_cell.attr,
                position=pos,
                clr=tdx_cell.clr,
                text=tdx_cell.text,
                tdx_spinfo=spinfo,
                tdx_stocks=tdx_cell.stks,
                tdx_id=tdx_cell.id,
                tdx_type=tdx_cell.type,
                clrtext=tdx_cell.clrtext,
                solid=tdx_cell.solid,
            )
            # 运行时推断的 market 信息（不影响导出）
            if inferred_market is not None:
                cell.inferred_market = inferred_market

        elif internal_type == 200:
            # type=8 → 输出/状态池
            stocks: List[StockSnapshotModel] = []
            stk_entries: List[StockSnapshotModel] = []
            for stk in tdx_cell.stks:
                snap = StockSnapshotModel(
                    label=stk.tq_code,
                    t=stk.indate,
                    p=stk.inprice,
                    setcode=stk.setcode,
                    code=stk.code,
                    indate=stk.indate,
                    intime=stk.intime,
                    inprice=stk.inprice,
                    income=stk.income,
                    now=stk.now,
                    rise=stk.rise,
                    volume=stk.volume,
                    maxrate=stk.maxrate,
                    maxperiod=stk.maxperiod,
                    maxtime=stk.maxtime,
                    maxprice=stk.maxprice,
                    idaynum=stk.idaynum,
                )
                stocks.append(snap)
                stk_entries.append(snap)

            cell = _make_tdx_cell(
                200,
                id=str(tdx_cell.id),
                attr=tdx_cell.attr,
                position=pos,
                clr=tdx_cell.clr,
                text=tdx_cell.text,
                stocks=stocks,
                stk_list=stk_entries,
                tdx_psatt=tdx_cell.psatt,
                tdx_id=tdx_cell.id,
                tdx_type=tdx_cell.type,
                clrtext=tdx_cell.clrtext,
                solid=tdx_cell.solid,
            )

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
# ================================================================
# 转换器（原 tdx_converter.py）
# ================================================================

# ═══════════════════════════════════════════════════════════════
# DZH cell_type → TDX type 反向映射
# (TDX_TO_DZH_CELL_TYPE = {0:1, 1:1, 2:2, 3:201, 7:202, 8:200})
# ═══════════════════════════════════════════════════════════════

_DZH_TO_TDX_TYPE: Dict[int, int] = {
    200: 8,   # StatePoolCellModel  → 状态池
    201: 3,   # ConditionCellModel  → 条件
    202: 7,   # CandidateCellModel → 备选池
    1:   0,   # LabelCellModel      → 装饰文字
    2:   2,   # ContainerCellModel  → 容器
    3:   0,   # StateColumnModel    → 装饰(无 TDX 对应)
    4:   0,   # DiscardCellModel    → 装饰(无 TDX 对应)
    6:   0,   # ArrowCellModel      → 装饰(无 TDX 对应)
    203: 8,   # Type203CellModel    → 状态池（兜底）
}


# ═══════════════════════════════════════════════════════════════
# TDX 类型重建
# ═══════════════════════════════════════════════════════════════

def _get_tdx_type(cell: Any) -> int:
    """
    根据内部 cell 模型重构原始 TDX cell type。

    规则:
      - type=202 且有 spinfo  → 7 (候选池)
      - type=200 且有 psatt   → 8 (状态池)
      - type=201 且有 func    → 3 (条件)
      - type=203              → 8 (状态池兜底)
      - 其他                  → 查反向映射表，默认 0
    """
    ct = _get_dzh_cell_type(cell)
    if ct == 202 and getattr(cell, "spinfo", getattr(cell, "tdx_spinfo", None)) is not None:
        return 7
    if ct == 200 and getattr(cell, "psatt", getattr(cell, "tdx_psatt", None)) is not None:
        return 8
    if ct == 201 and getattr(cell, "func", getattr(cell, "tdx_func", None)) is not None:
        return 3
    if ct == 203:
        return 8
    return _DZH_TO_TDX_TYPE.get(ct, 0)


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════

def _get_dzh_cell_type(cell: Any) -> int:
    """从 cell 模型获取 DZH 内部 cell_type。

    TdxCellModel.type 存储 TDX 原生类型 (0~8)，需通过 TDX_TO_DZH_CELL_TYPE 映射。
    DynamicCellModel.type / cell_type 已是 DZH 内部类型 (200/201/202/203)，直接使用。
    """
    ct = getattr(cell, "type", getattr(cell, "cell_type", 0))
    # 如果 ct 在 TDX_TO_DZH_CELL_TYPE 中，说明是 TDX 原生类型，需要映射
    if ct in TDX_TO_DZH_CELL_TYPE:
        return TDX_TO_DZH_CELL_TYPE[ct]
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


def _safe_get(obj: Any, attr: str, default: Any = 0) -> Any:
    """安全获取属性值。"""
    if obj is None:
        return default
    return getattr(obj, attr, default)


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

def _convert_candidate_cell(cell: Any) -> Dict[str, Any]:
    """
    转换候选池 cell (cell_type=202) → node type="tdx_candidate"。

    返回字典包含：
    - id / type / position / label：通用节点属性
    - clr / clrtext / solid / text：外观与显示属性
    - params：spinfo 字段 (type, customblockname, size, market, sector_type) + stocks 列表
    """
    spinfo = getattr(cell, "spinfo", getattr(cell, "tdx_spinfo", None))
    params: Dict[str, Any] = {}
    if spinfo is not None:
        for field in TdxSpinfoModel.model_fields:
            params[field] = _safe_get(spinfo, field, 0 if field != "market" and field != "customblockname" else "")
        # 确保字符串字段默认值正确
        if "customblockname" not in params or params["customblockname"] is None:
            params["customblockname"] = ""
        if "market" not in params or params["market"] is None:
            params["market"] = ""
        # 嵌套分组：tdx_spinfo 包含全部 5 个 spinfo 字段
        params["tdx_spinfo"] = {
            field: params[field]
            for field in TdxSpinfoModel.model_fields
        }
        # 短别名：spinfo → tdx_spinfo，与 ui_layouts.json data_path 一致
        params["spinfo"] = params["tdx_spinfo"]

    stocks: List[Dict[str, str]] = []
    tdx_stocks = getattr(cell, "stks", getattr(cell, "tdx_stocks", None)) or []
    for stk in tdx_stocks:
        stocks.append({"code": _safe_get(stk, "tq_code", "")})
    params["stocks"] = stocks

    # 嵌套分组：tdx_stocks 为 TDX 格式 [{setcode, code}, ...]
    params["tdx_stocks"] = [
        {"setcode": _code_to_setcode(s["code"]), "code": s["code"]}
        for s in stocks
    ]

    # 扁平键：为向后兼容保留 tdx_spinfo_* 前缀的扁平键
    if spinfo is not None:
        for field in TdxSpinfoModel.model_fields:
            flat_key = f"tdx_spinfo_{field}"
            params[flat_key] = _safe_get(spinfo, field, 0 if field != "market" and field != "customblockname" else "")

    # 外观属性也放入 params
    params["clr"] = _safe_get(cell, "clr", -1)
    params["clrtext"] = _safe_get(cell, "clrtext", 0)
    params["solid"] = _safe_get(cell, "solid", 0)

    return {
        "id": _safe_get(cell, "id", ""),
        "type": "tdx_candidate",
        "position": _build_position(cell),
        "clr": _safe_get(cell, "clr", -1),
        "clrtext": _safe_get(cell, "clrtext", 0),
        "solid": _safe_get(cell, "solid", 0),
        "text": _safe_get(cell, "text", ""),
        "params": params,
        "label": _safe_get(cell, "text", ""),
    }


def _convert_state_pool_cell(cell: Any) -> Dict[str, Any]:
    """
    转换状态池 cell (cell_type=200) → node type="tdx_state_pool"。

    params 包含 psatt 字段 (bdel, ndelnum, ndeltype, baimpool,
    bsound, nsoundtype, nsyssound, soundfile, btip, bsavetoblock,
    blockfile, bclearblock, bsavehis) 和 stocks 列表。
    """
    psatt = getattr(cell, "psatt", getattr(cell, "tdx_psatt", None))
    params: Dict[str, Any] = {}
    if psatt is not None:
        for field in TdxPsattModel.model_fields:
            params[field] = _safe_get(psatt, field, 0)
        # 嵌套分组：tdx_psatt 包含全部 psatt 字段
        params["tdx_psatt"] = {field: params[field] for field in TdxPsattModel.model_fields}
        # 短别名：psatt → tdx_psatt，与 ui_layouts.json data_path 一致
        params["psatt"] = params["tdx_psatt"]

    # stocks: 优先从 tdx_stocks 读取（TdxStkModel 列表），否则从 stocks/stk_list 读取
    stocks: List[Dict[str, str]] = []
    tdx_stocks = getattr(cell, "stks", getattr(cell, "tdx_stocks", None)) or []
    if tdx_stocks:
        for stk in tdx_stocks:
            stocks.append({"code": _safe_get(stk, "tq_code", "")})
    else:
        stk_list = getattr(cell, "stocks", None) or getattr(cell, "stk_list", None) or []
        for stk in stk_list:
            stocks.append({
                "code": _safe_get(stk, "label", ""),
                "t": _safe_get(stk, "t", ""),
                "p": _safe_get(stk, "p", ""),
            })
    params["stocks"] = stocks

    # 嵌套分组：tdx_stocks 为 TDX 格式 [{setcode, code}, ...]
    params["tdx_stocks"] = [
        {"setcode": _code_to_setcode(s["code"]), "code": s["code"]}
        for s in stocks
    ]

    # 扁平键：为向后兼容保留 tdx_psatt_* 前缀的扁平键
    if psatt is not None:
        for field in TdxPsattModel.model_fields:
            flat_key = f"tdx_psatt_{field}"
            params[flat_key] = _safe_get(psatt, field, 0)

    # 外观属性也放入 params
    params["clr"] = _safe_get(cell, "clr", -1)
    params["clrtext"] = _safe_get(cell, "clrtext", 0)
    params["solid"] = _safe_get(cell, "solid", 0)

    return {
        "id": _safe_get(cell, "id", ""),
        "type": "tdx_state_pool",
        "position": _build_position(cell),
        "clr": _safe_get(cell, "clr", -1),
        "clrtext": _safe_get(cell, "clrtext", 0),
        "solid": _safe_get(cell, "solid", 0),
        "text": _safe_get(cell, "text", ""),
        "params": params,
        "label": _safe_get(cell, "text", ""),
    }


def _convert_condition_cell(cell: Any) -> Dict[str, Any]:
    """
    转换条件 cell (cell_type=201) → node type="tdx_condition"。

    params 包含 func 字段 (nset, ntjindexno, accode, nperiod,
    nfirst, cfirst, noperate, nsecond, csecond, fsecond,
    nbeginday, nendday, bnost, bnotp, bnotq, nperiodnum)。
    """
    func = getattr(cell, "func", getattr(cell, "tdx_func", None))
    params: Dict[str, Any] = {}
    if func is not None:
        for field in TdxFuncModel.model_fields:
            params[field] = _safe_get(func, field, 0)
        # 嵌套分组：tdx_func 包含全部 16 个 func 字段
        params["tdx_func"] = {field: params[field] for field in TdxFuncModel.model_fields}
        # 短别名：func → tdx_func，与 ui_layouts.json data_path 一致
        params["func"] = params["tdx_func"]

    # 外观属性也放入 params
    params["clr"] = _safe_get(cell, "clr", -1)
    params["clrtext"] = _safe_get(cell, "clrtext", 0)
    params["solid"] = _safe_get(cell, "solid", 0)

    return {
        "id": _safe_get(cell, "id", ""),
        "type": "tdx_condition",
        "position": _build_position(cell),
        "clr": _safe_get(cell, "clr", -1),
        "clrtext": _safe_get(cell, "clrtext", 0),
        "solid": _safe_get(cell, "solid", 0),
        "text": _safe_get(cell, "text", ""),
        "params": params,
        "label": _safe_get(cell, "text", ""),
    }


def _convert_decoration_cell(cell: Any) -> Dict[str, Any]:
    """
    转换装饰性 cell (cell_type=1,2,3,4,6) → node type="decoration"。

    params 包含 _visual_only=True 和 dzh_cell_type。
    dzh_cell_type 使用内部 type，该值已是通过
    TDX_TO_DZH_CELL_TYPE 从原始 TDX 类型映射的结果。
    """
    ct = _get_dzh_cell_type(cell)
    return {
        "id": _safe_get(cell, "id", ""),
        "type": "decoration",
        "position": _build_position(cell),
        "clr": _safe_get(cell, "clr", -1),
        "clrtext": _safe_get(cell, "clrtext", 0),
        "solid": _safe_get(cell, "solid", 0),
        "text": _safe_get(cell, "text", ""),
        "params": {
            "_visual_only": True,
            "dzh_cell_type": ct,
            "clr": _safe_get(cell, "clr", -1),
            "clrtext": _safe_get(cell, "clrtext", 0),
            "solid": _safe_get(cell, "solid", 0),
        },
        "label": _safe_get(cell, "text", ""),
    }


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

def _convert_flow(flow: Any) -> Dict[str, Any]:
    """
    转换 FlowModel 为 edge dict。

    edge 结构:
      {
        "source": {"node_id": flow.startid},
        "target": {"node_id": flow.endid},
        "params": {mode, clr, size, tran, emptyps, starttype, jgtime}
      }
    """
    tdx_tran = getattr(flow, "tran", getattr(flow, "tdx_tran", 0))
    if tdx_tran is None:
        tdx_tran = 0
    mode = "move" if tdx_tran == 1 else "copy"

    tdx_clr = getattr(flow, "clr", -1)

    tdx_size = getattr(flow, "size", getattr(flow, "tdx_size", 1))
    if tdx_size is None:
        tdx_size = 1

    # TDX 时间调度参数
    tdx_emptyps = getattr(flow, "emptyps", getattr(flow, "tdx_emptyps", 0))
    if tdx_emptyps is None:
        tdx_emptyps = 0

    tdx_starttype = getattr(flow, "starttype", getattr(flow, "tdx_starttype", 0))
    if tdx_starttype is None:
        tdx_starttype = 0

    tdx_starttime = getattr(flow, "starttime", getattr(flow, "tdx_starttime", 0))
    if tdx_starttime is None:
        tdx_starttime = 0

    tdx_starttimetype = getattr(flow, "starttimetype", getattr(flow, "tdx_starttimetype", 0))
    if tdx_starttimetype is None:
        tdx_starttimetype = 0

    tdx_starttimehms = getattr(flow, "starttimehms", getattr(flow, "tdx_starttimehms", 0))
    if tdx_starttimehms is None:
        tdx_starttimehms = 0

    tdx_cxtype = getattr(flow, "cxtype", getattr(flow, "tdx_cxtype", 0))
    if tdx_cxtype is None:
        tdx_cxtype = 0

    tdx_cxtime = getattr(flow, "cxtime", getattr(flow, "tdx_cxtime", 0))
    if tdx_cxtime is None:
        tdx_cxtime = 0

    tdx_cxtimetype = getattr(flow, "cxtimetype", getattr(flow, "tdx_cxtimetype", 0))
    if tdx_cxtimetype is None:
        tdx_cxtimetype = 0

    tdx_jgtime = getattr(flow, "jgtime", getattr(flow, "tdx_jgtime", 60))
    if tdx_jgtime is None:
        tdx_jgtime = 60

    source_id = str(getattr(flow, "startid", getattr(flow, "from_cell_id", "")))
    target_id = str(getattr(flow, "endid", getattr(flow, "to_cell_id", "")))

    return {
        "source": {"node_id": source_id},
        "target": {"node_id": target_id},
        "params": {
            "mode": mode,
            "tdx_clr": tdx_clr,
            "tdx_size": tdx_size,
            "tdx_tran": tdx_tran,
            "clr": tdx_clr,
            "size": tdx_size,
            "tran": tdx_tran,
            "emptyps": tdx_emptyps,
            "starttype": tdx_starttype,
            "starttime": tdx_starttime,
            "starttimetype": tdx_starttimetype,
            "starttimehms": tdx_starttimehms,
            "cxtype": tdx_cxtype,
            "cxtime": tdx_cxtime,
            "cxtimetype": tdx_cxtimetype,
            "jgtime": tdx_jgtime,
        },
    }


# ═══════════════════════════════════════════════════════════════
# 主转换函数
# ═══════════════════════════════════════════════════════════════

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

    return {
        "pool_meta": {
            "type": "tdx",
            "backcolor": _safe_get(pool_meta, "backcolor", 16777216),
            "nextid": _safe_get(pool_meta, "nextid", 0),
        },
        "nodes": nodes,
        "edges": edges,
    }
# ================================================================
# 执行器（原 tdx_executor.py）
# ================================================================

# ── 加载 TDX 系统指标与公式映射配置表 ─────────────────────────────────────
# 配置表位于 meta_core/config/，模块加载时读入内存，避免硬编码字典
# _CONFIG_DIR 已在解析函数区定义，此处复用


def _load_tdx_config_table(filename: str, key_field: str) -> Dict[int, dict]:
    """从 JSON 配置表读取记录列表，按 key_field 构建 int->record 映射。"""
    path = _CONFIG_DIR / filename
    table: Dict[int, dict] = {}
    try:
        data = json.loads(path.read_text("utf-8"))
        for record in data.get("records", []):
            key = record.get(key_field)
            if key is not None:
                table[int(key)] = record
    except Exception as e:
        logger.warning("加载 TDX 配置表 %s 失败: %s", filename, e)
    return table


# 系统指标编号 → 可评估逻辑映射
# 原 _TDX_SYSTEM_INDICATORS 已迁移到 config/tdx_system_indicators.json
# 内存表保留为 Dict[int, dict]，结构与原 tuple 兼容：
#   record["name"], record["handler_type"] 对应原 (name, handler_type)
_TDX_SYSTEM_INDICATORS: Dict[int, dict] = _load_tdx_config_table(
    "tdx_system_indicators.json", "ntjindexno"
)

# 系统指标编号 → 公式计算映射
# 原 _TDX_INDICATOR_FORMULA_MAP 已迁移到 config/tdx_indicator_formula_map.json
# 内存表保留为 Dict[int, dict]，通过 _get_formula_config() 提供原 tuple 兼容访问
_TDX_INDICATOR_FORMULA_MAP: Dict[int, dict] = _load_tdx_config_table(
    "tdx_indicator_formula_map.json", "ntjindexno"
)


def _get_system_indicator(ntjindexno: int) -> Tuple[str, str]:
    """从配置表查询系统指标，缺失时返回默认透传项。"""
    record = _TDX_SYSTEM_INDICATORS.get(ntjindexno)
    if record is None:
        logger.debug("未配置的系统指标 ntjindexno=%d，使用默认透传", ntjindexno)
        return ("unknown", "pass")
    return (record.get("name", ""), record.get("handler_type", "pass"))


def _get_formula_config(ntjindexno: int) -> Optional[Tuple[str, str, int]]:
    """从配置表查询公式配置，返回 (formula_name, formula_arg, return_count) 元组。"""
    record = _TDX_INDICATOR_FORMULA_MAP.get(ntjindexno)
    if record is None:
        return None
    return (
        record.get("formula_name", ""),
        record.get("formula_arg", ""),
        int(record.get("return_count", 1)),
    )


# ── 通用规则评估器：表驱动基础设施 ──────────────────────────────────────
# 比较操作符表（eval_rule.field/op/threshold 声明式规则使用）
_COMPARE_OPS = {
    ">=": lambda v, t: v >= t,
    ">": lambda v, t: v > t,
    "<": lambda v, t: v < t,
    "<=": lambda v, t: v <= t,
    "==": lambda v, t: v == t,
    "!=": lambda v, t: v != t,
}


def _check_op_pass_through(evaluator, code, tq_code, snap, params, rule):
    """透传检查：始终通过。"""
    return True


def _check_op_not_st(evaluator, code, tq_code, snap, params, rule):
    """去ST检查：名称不以 ST/*ST 开头。"""
    name = snap.get('name', '')
    return not name.startswith(('ST', '*ST'))


def _check_op_consecutive_rises(evaluator, code, tq_code, snap, params, rule):
    """连涨检查：通过 TqAdapter K线数据验证连续上涨天数。"""
    change_pct = float(snap.get('change_pct', 0) or 0)
    days_field = rule.get("days_field", "nperiodnum")
    default_days = int(rule.get("default_days", 0))
    nperiodnum = (params or {}).get(days_field, 0)
    required_days = nperiodnum if nperiodnum and nperiodnum > 0 else default_days
    if evaluator.tq_adapter and evaluator.tq_adapter.is_ready():
        return evaluator._check_consecutive_rises(tq_code, required_days)
    # Fallback: 当日上涨即通过
    return change_pct > 0


# 特殊检查分派表（按 check 类型分派，非 ntjindexno 值分派）
_CHECK_OPS = {
    "pass_through": _check_op_pass_through,
    "not_st": _check_op_not_st,
    "consecutive_rises": _check_op_consecutive_rises,
}


def _get_eval_rule(ntjindexno: int) -> dict:
    """从配置表查询指标的 eval_rule，缺失时返回 pass_through 默认规则。"""
    record = _TDX_SYSTEM_INDICATORS.get(ntjindexno)
    if record is None:
        return {"check": "pass_through"}
    return record.get("eval_rule") or {"check": "pass_through"}


def _get_mock_pass_threshold(ntjindexno: int) -> int:
    """从配置表查询指标的 mock_pass_threshold，缺失时返回默认 40。"""
    record = _TDX_SYSTEM_INDICATORS.get(ntjindexno)
    if record is None:
        return 40
    return int(record.get("mock_pass_threshold", 40))


def _get_formula_logic(ntjindexno: int) -> set:
    """从配置表查询指标的 formula_logic 集合，缺失时返回空集。"""
    record = _TDX_SYSTEM_INDICATORS.get(ntjindexno)
    if record is None:
        return set()
    logic = record.get("formula_logic")
    if not logic:
        return set()
    if isinstance(logic, str):
        return {logic}
    return set(logic)


def _get_sort_field(ntjindexno: int) -> str:
    """从配置表查询排序指标的 sort_field，缺失时返回空串。"""
    record = _TDX_SYSTEM_INDICATORS.get(ntjindexno)
    if record is None:
        return ""
    return record.get("sort_field", "")


# ── handler_type 处理策略表驱动 ────────────────────────────────────────
# handler_strategies 段定义于 config/tdx_system_indicators.json，
# 将 handler_type 值映射到通用策略名（direct_pass / rank / eval_rule），
# 差异显于表内容，计算逻辑隐于 _dispatch_handler 分派。
def _load_tdx_handler_strategies(filename: str) -> Dict[str, dict]:
    """从 JSON 配置表读取 handler_strategies 段，构建 handler_type->策略 映射。"""
    path = _CONFIG_DIR / filename
    try:
        data = json.loads(path.read_text("utf-8"))
        return data.get("handler_strategies", {})
    except Exception as e:
        logger.warning("加载 TDX handler_strategies 失败: %s", e)
        return {}


_TDX_HANDLER_STRATEGIES: Dict[str, dict] = _load_tdx_handler_strategies(
    "tdx_system_indicators.json"
)


def _get_handler_strategy(handler_type: str) -> dict:
    """从配置表查询 handler_type 的处理策略，缺失时默认 direct_pass。"""
    return _TDX_HANDLER_STRATEGIES.get(
        handler_type, {"strategy": "direct_pass"}
    )


def _handle_rank(ctx, strategy_cfg):
    """排名处理：调用 ctx 中注入的 rank_fn 回调，由调用方提供上下文相关实现。"""
    rank_fn = ctx.get("rank_fn")
    if rank_fn is not None:
        return rank_fn(ctx, strategy_cfg)
    return ctx.get("stocks", [])


def _handle_eval_rule(ctx):
    """规则评估处理：调用 ctx 中注入的 eval_rule_fn 回调，由调用方提供上下文相关实现。"""
    eval_rule_fn = ctx.get("eval_rule_fn")
    if eval_rule_fn is not None:
        return eval_rule_fn(ctx)
    return ctx.get("stocks", [])


def _dispatch_handler(handler_type, ctx, strategy_cfg):
    """按 handler_strategies 表配置分派处理。

    Args:
        handler_type: handler_type 值（pass/sort/condition），仅用于日志
        ctx: 上下文字典，至少包含:
            - stocks: direct_pass 策略直接返回的透传结果
            - rank_fn(ctx, strategy_cfg): rank 策略回调
            - eval_rule_fn(ctx): eval_rule 策略回调
        strategy_cfg: handler_strategies 表中该 handler_type 的策略配置
    """
    strategy = strategy_cfg.get("strategy")
    if strategy == "direct_pass":
        return ctx["stocks"]
    elif strategy == "rank":
        return _handle_rank(ctx, strategy_cfg)
    elif strategy == "eval_rule":
        return _handle_eval_rule(ctx)
    return ctx.get("stocks", [])


def _normalize_code_for_tq(code: str) -> str:
    """将 6 位 TDX 股票代码转换为 TqAdapter 标准格式。
    
    规则: 0/3 开头 → 深圳(.SZ), 6 开头 → 上海(.SH)
    """
    code = code.strip()
    if len(code) == 6:
        if code.startswith(('0', '3')):
            return f"{code}.SZ"
        elif code.startswith('6'):
            return f"{code}.SH"
    return code


# ── 简易股票名称映射（无 TqAdapter 时降级使用）──────────────────────────
_MOCK_NAMES: Dict[str, str] = {
    "000001": "平安银行", "000002": "万科A", "000333": "美的集团",
    "000651": "格力电器", "000858": "五粮液", "000725": "京东方A",
    "002415": "海康威视", "002594": "比亚迪", "300750": "宁德时代",
    "300059": "东方财富", "600000": "浦发银行", "600519": "贵州茅台",
    "600030": "中信证券", "601318": "中国平安", "600036": "招商银行",
    "601398": "工商银行",
}


# ── TDX starttype 枚举常量 ────────────────────────────────────────────
_STARTTYPE_IMMEDIATE = 0       # 立即执行
_STARTTYPE_DELAY_SECONDS = 1   # 延迟N秒
_STARTTYPE_BEFORE_OPEN = 2     # 开市前N(秒/分)
_STARTTYPE_AFTER_OPEN = 3      # 开市后N(秒/分)
_STARTTYPE_BEFORE_CLOSE = 4    # 收市前N分钟
_STARTTYPE_AFTER_CLOSE = 5     # 收市后N分钟
_STARTTYPE_TRADING_TIME = 6    # 指定交易日时间
_STARTTYPE_SPECIFIC_TIME = 7   # 指定时间

# A股标准交易时段（用于开市/收市计算）
_MARKET_OPEN_TIME = _hms_to_seconds(9, 25, 0)   # 09:25:00（集合竞价开始）
_MARKET_CLOSE_TIME = _hms_to_seconds(15, 0, 0)  # 15:00:00（收盘）

# ── TDX cxtype 枚举常量（持续时长类型）─────────────────────────────────
_CXTYPE_FOREVER = 0   # 一直执行（永不过期）
_CXTYPE_DURATION = 1  # 有限时间窗口模式
_CXTYPE_ONCE = 2      # 只执行一次

# ── 股票代码 → setcode 映射表（0=SZ, 1=SH, 2=BJ）──────────────────────
# 后缀映射（tq_code 格式 "600000.SH"）
_SUFFIX_TO_SETCODE = {"SH": 1, "SZ": 0, "BJ": 2}
# 数字前缀映射（无后缀时按首位推断；B股 900xxx/200xxx 由调用方特判）
_PREFIX_TO_SETCODE = {"0": 0, "3": 0, "6": 1, "8": 2, "4": 2}


# ── 公式评估表驱动基础设施 ──────────────────────────────────────────────
# 比较操作符表（cross_rules 的 prev_cmp/curr_cmp 与 threshold 的 compare 共用）：
#   lt/gt/lte/gte 用于 cross_rules；less/greater 兼容原 threshold compare 字段
_CMP_OPS = {
    "lt": operator.lt, "gt": operator.gt,
    "lte": operator.le, "gte": operator.ge, "eq": operator.eq,
    "less": operator.lt, "greater": operator.gt,
    "le": operator.le, "ge": operator.ge,
}


def _load_tdx_formula_tables(filename: str) -> dict:
    """从 JSON 配置表读取公式评估相关表段
    （eval_rules / cross_rules / formula_name_logic / logic_priority）。"""
    path = _CONFIG_DIR / filename
    tables: dict = {
        "eval_rules": {}, "cross_rules": {},
        "formula_name_logic": {}, "logic_priority": [],
    }
    try:
        data = json.loads(path.read_text("utf-8"))
        tables["eval_rules"] = data.get("eval_rules", {})
        tables["cross_rules"] = data.get("cross_rules", {})
        tables["formula_name_logic"] = data.get("formula_name_logic", {})
        tables["logic_priority"] = data.get("logic_priority", [])
    except Exception as e:
        logger.warning("加载 TDX 公式评估表 %s 失败: %s", filename, e)
    return tables


_TDX_FORMULA_TABLES = _load_tdx_formula_tables("tdx_system_indicators.json")
_EVAL_RULES: Dict[str, dict] = _TDX_FORMULA_TABLES["eval_rules"]
_CROSS_RULES: Dict[str, dict] = _TDX_FORMULA_TABLES["cross_rules"]
_FORMULA_NAME_LOGIC: Dict[str, str] = _TDX_FORMULA_TABLES["formula_name_logic"]
_LOGIC_PRIORITY: List[str] = _TDX_FORMULA_TABLES["logic_priority"]


def _resolve_formula_logic_tag(logic_set: set, formula_name: str) -> Optional[str]:
    """按 logic_priority 优先级解析 logic_tag：logic_set 或 formula_name 命中即返回。

    等价原 is_macd/is_kdj/is_rsi/is_boll 的 OR 组合 + 优先级顺序，差异显于
    formula_name_logic 与 logic_priority 表内容，不在代码里 if logic_tag 分派。
    """
    fname_tag = _FORMULA_NAME_LOGIC.get(formula_name.upper())
    for tag in _LOGIC_PRIORITY:
        if tag in logic_set or tag == fname_tag:
            return tag
    return None


def _get_formula_eval_rule(logic_tag: str) -> dict:
    """从 eval_rules 表查询 logic_tag 的评估规则，缺失时返回空 dict。"""
    return _EVAL_RULES.get(logic_tag, {})


# ── 公式评估方法处理器（按 eval_method 分派，差异隐于 eval_rules 表内容）──
def _eval_method_cross(outvars: dict, params: dict, ctx: dict) -> Optional[List[str]]:
    """cross 方法：按 cross_rules 表声明的前后比较方向判断金叉/死叉。

    line_a/line_b 由 eval_rules 声明（替代原 DIF/DEA vs K/D auto-detect）；
    prev_cmp/curr_cmp 由 cross_rules 声明（替代原 golden/death if/elif）。
    """
    stock_list = ctx["stock_list"]
    tq_codes = ctx["tq_codes"]
    line_a_key = params.get("line_a")
    line_b_key = params.get("line_b")
    cross_type = params.get("cross_type", "golden")
    cross_rule = _CROSS_RULES.get(cross_type, {})
    prev_op = _CMP_OPS.get(cross_rule.get("prev_cmp"))
    curr_op = _CMP_OPS.get(cross_rule.get("curr_cmp"))
    if prev_op is None or curr_op is None or not line_a_key or not line_b_key:
        return None
    # 探测样本：声明的 line keys 不存在则视为不支持（等价原 auto-detect 兜底）
    sample = None
    for tq_code in tq_codes:
        d = outvars.get(tq_code, {})
        if d:
            sample = d
            break
    if not sample or line_a_key not in sample or line_b_key not in sample:
        return None
    passed = []
    for code, tq_code in zip(stock_list, tq_codes):
        detail = outvars.get(tq_code, {})
        line_a = detail.get(line_a_key, [])
        line_b = detail.get(line_b_key, [])
        if len(line_a) < 2 or len(line_b) < 2:
            continue
        try:
            a_prev = float(line_a[-2]) if line_a[-2] is not None else None
            a_curr = float(line_a[-1]) if line_a[-1] is not None else None
            b_prev = float(line_b[-2]) if line_b[-2] is not None else None
            b_curr = float(line_b[-1]) if line_b[-1] is not None else None
        except (TypeError, ValueError):
            continue
        if any(v is None for v in (a_prev, a_curr, b_prev, b_curr)):
            continue
        if prev_op(a_prev, b_prev) and curr_op(a_curr, b_curr):
            passed.append(code)
    return passed


def _eval_method_threshold(outvars: dict, params: dict, ctx: dict) -> Optional[List[str]]:
    """threshold 方法：按 compare 字段比较指标值与阈值（超买/超卖等）。"""
    stock_list = ctx["stock_list"]
    tq_codes = ctx["tq_codes"]
    indicator_key = params.get("indicator_key")
    threshold = params.get("threshold", 0)
    cmp_op = _CMP_OPS.get(params.get("compare", "greater"))
    if cmp_op is None or not indicator_key:
        return None
    passed = []
    for code, tq_code in zip(stock_list, tq_codes):
        detail = outvars.get(tq_code, {})
        values = detail.get(indicator_key, [])
        if not values:
            continue
        try:
            val = float(values[-1]) if values[-1] is not None else None
        except (TypeError, ValueError):
            continue
        if val is None:
            continue
        if cmp_op(val, threshold):
            passed.append(code)
    return passed


def _eval_method_cross_and_threshold(outvars: dict, params: dict, ctx: dict) -> Optional[List[str]]:
    """cross_and_threshold 方法：先 cross 判断，cross 不支持时降级 threshold。

    对应原 KDJ 逻辑：金叉优先（K/D 线存在则返回金叉结果），否则降级 K<20 超卖。
    """
    cross_result = _eval_method_cross(outvars, params, ctx)
    if cross_result is not None:
        return cross_result
    return _eval_method_threshold(outvars, params, ctx)


# eval_method 分派表（差异显于 eval_rules 表，计算逻辑隐于分派）
_EVAL_METHODS = {
    "cross": _eval_method_cross,
    "threshold": _eval_method_threshold,
    "cross_and_threshold": _eval_method_cross_and_threshold,
}


class TdxPoolExecutor:
    """TDX 股票池运行时执行器

    接收 convert_tdx_to_config() 输出的配置字典，模拟通达信股票池的
    单轮执行逻辑：按顺序遍历边，对条件边评估筛选，对普通边执行
    copy/move 转移，并对状态池应用 psatt 配置。
    """

    def __init__(self, config: dict, tq_adapter=None, storage=None,
                 current_kline_time: str = "", formula_router=None):
        self.config = config
        self.tq_adapter = tq_adapter
        self.formula_router = formula_router
        self.storage = storage
        self.current_kline_time = current_kline_time
        self._nodes: Dict[str, dict] = {}
        self._edges: List[dict] = []
        self._stocks: Dict[str, Dict[str, Dict]] = {}

        self._init_nodes_edges()
        self._init_candidate_stocks()

        # 持续时长执行计数器 {id(edge): execution_count}
        self._duration_exec_counts: Dict[int, int] = {}
        # 持续时长窗口起始时间 {id(edge): first_execution_datetime}
        self._duration_start_times: Dict[int, datetime] = {}

    # ------------------------------------------------------------------
    # TDX 参数读取辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _get_tdx_param(params: dict, key: str, nested_key: str,
                       nested_sub_key: str, default=None):
        """从嵌套键或扁平字段读取 TDX 参数。

        优先读取 params[nested_key][nested_sub_key]（如 params["tdx_func"]["nset"]），
        不存在时回退到 params[key]（如 params["nset"]），再不存在返回 default。
        """
        nested = params.get(nested_key)
        if isinstance(nested, dict) and nested_sub_key in nested:
            return nested[nested_sub_key]
        if key in params:
            return params[key]
        return default

    # ------------------------------------------------------------------
    # FormulaRouter 桥接（Task 13：替代 tq_adapter.eval_formula_*）
    # ------------------------------------------------------------------

    def _eval_formula_via_router(
        self, formula_name: str, formula_arg: str,
        tq_codes: List[str], period: str,
    ) -> Optional[dict]:
        """通过 FormulaRouter.eval_batch() 评估公式。

        替代已移除的 tq_adapter.eval_formula_zb / eval_formula_xg。
        FormulaRouter 返回 ``{symbol: bool}``，此处转换为兼容格式
        ``{'success': True, 'result': {tq_code: [value]}, 'result_detail': {}}``。

        Args:
            formula_name: 公式名称/表达式（如 "MACD"）。
            formula_arg: 公式参数（如 "12,26,9"），可为空。
            tq_codes: TQ 格式股票代码列表。
            period: 周期字符串（如 "1d" / "5m"）。

        Returns:
            兼容格式的结果字典；formula_router 不可用或评估失败时返回 None。
        """
        if not self.formula_router or not formula_name or not tq_codes:
            return None
        if not hasattr(self.formula_router, 'eval_batch'):
            return None
        formula = f"{formula_name}({formula_arg})" if formula_arg else formula_name
        try:
            result = _run_async(
                lambda: self.formula_router.eval_batch(
                    formula, tq_codes, period=period
                )
            )
        except Exception as e:
            logger.debug(
                "FormulaRouter.eval_batch 失败 formula=%s: %s", formula_name, e
            )
            return None
        if not result or not isinstance(result, dict):
            return None
        # 成功响应：{symbol: bool} → 转为 {tq_code: [value]}
        converted = {}
        for code in tq_codes:
            val = result.get(code, False)
            converted[code] = [1.0 if val else 0.0]
        return {
            'success': True,
            'result': converted,
            'result_detail': {},
        }

    def _eval_indicator_via_router(
        self, formula_text: str, tq_codes: List[str], period: str = '1d',
    ) -> Optional[dict]:
        """通过 FormulaRouter.eval_batch() 评估选股/条件公式。

        替代已移除的 tq_adapter.eval_indicator / eval_formula_xg。
        返回 ``{'result': {tq_code: scalar_value}}`` 兼容格式。

        Args:
            formula_text: 公式文本（如 "CROSS(MA(C,5),MA(C,10))"）。
            tq_codes: TQ 格式股票代码列表。
            period: 周期字符串。

        Returns:
            兼容格式的结果字典；不可用或失败时返回 None。
        """
        if not self.formula_router or not formula_text or not tq_codes:
            return None
        if not hasattr(self.formula_router, 'eval_batch'):
            return None
        try:
            result = _run_async(
                lambda: self.formula_router.eval_batch(
                    formula_text, tq_codes, period=period
                )
            )
        except Exception as e:
            logger.debug(
                "FormulaRouter.eval_batch 失败 formula=%s: %s", formula_text, e
            )
            return None
        if not result or not isinstance(result, dict):
            return None
        # 成功响应：{symbol: bool} → 转为 {tq_code: scalar}
        converted = {
            code: (1 if result.get(code, False) else 0)
            for code in tq_codes
        }
        return {'result': converted}

    # ------------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------------

    def _init_nodes_edges(self) -> None:
        """从 config 构建节点索引、边列表，并为每个节点初始化空股票列表。"""
        for node in self.config.get('nodes', []):
            nid = str(node.get('id', ''))
            if nid:
                self._nodes[nid] = node
                self._stocks[nid] = {}

        self._edges = self.config.get('edges', [])
        # 确保边引用一致性：统一为 source/target node_id 格式
        for edge in self._edges:
            # 兼容 startid/endid 格式（前端创建的边）
            if 'startid' in edge and 'source' not in edge:
                edge['source'] = {'node_id': str(edge['startid'])}
            if 'endid' in edge and 'target' not in edge:
                edge['target'] = {'node_id': str(edge['endid'])}
            src = edge.get('source', {})
            tgt = edge.get('target', {})
            if 'node_id' in src:
                src['node_id'] = str(src['node_id'])
            if 'node_id' in tgt:
                tgt['node_id'] = str(tgt['node_id'])

    def _init_candidate_stocks(self) -> None:
        """为所有 tdx_candidate 节点预填充初始股票。

        支持全部8种 spinfo type (0~7) 的初始化方式：
        0 = 自设监控品种（显式stk列表或user_blocks表）
        1 = 沪深300+中证500成分股
        2 = 所有A股
        3 = 自选股（支持30秒自动刷新）
        4 = 自定义板块
        5 = 板块指数
        6 = ETF基金
        7 = 可转债
        无 spinfo 或 spinfo 为空: 使用 params.stocks 列表（老版 stk 方式）

        优先使用 CandidatePoolResolver 进行解析（已完整实现type 0~7）。
        """
        for node_id, node in self._nodes.items():
            if node.get('type') != 'tdx_candidate':
                continue
            if self._stocks.get(node_id):
                continue

            params = node.get('params', {})
            spinfo = params.get('tdx_spinfo', params)
            spinfo_type = spinfo.get('type', params.get('type', 0))
            customblockname = spinfo.get('customblockname', params.get('customblockname', ''))
            market = spinfo.get('market', params.get('market', ''))

            # 优先使用 params.stocks 列表（老版 stk 方式 / 前端传入的固定股票列表）
            preset_stocks = params.get('stocks', [])
            if preset_stocks:
                for s in preset_stocks:
                    code = s.get('code', '') if isinstance(s, dict) else str(s)
                    if code:
                        self._stocks[node_id][code] = {
                            "entry_time": self.current_kline_time or datetime.now().isoformat(),
                            "data": s if isinstance(s, dict) else {},
                        }
                continue

            # 无预设股票时，根据 spinfo.type 动态加载候选股票
            loaded = self._load_stocks_by_spinfo(spinfo_type, customblockname, market)

            if loaded:
                self._stocks[node_id] = {
                    code: {
                        "entry_time": self.current_kline_time or datetime.now().isoformat(),
                        "data": {},
                    }
                    for code in loaded
                }
            # 动态加载也失败时，节点保持空列表

    def _load_stocks_by_spinfo(
        self, spinfo_type: int, customblockname: str, market: str
    ) -> List[str]:
        """根据 spinfo.type 动态加载候选股票列表。

        支持 spinfo type 0~7 全部8种类型。
        优先使用 CandidatePoolResolver 解析（已完整实现type 0~7的解析逻辑、
        缓存策略和数据源故障转移链），降级时保留原有的直接加载逻辑。

        Args:
            spinfo_type: spinfo.type 枚举值 (0~7)
            customblockname: 自定义板块名称（type=0/4时有效）
            market: 市场选择 ("SZ"/"SH"/"SZ,SH")

        Returns:
            股票代码列表，加载失败返回空列表
        """
        if not self.tq_adapter:
            return []

        # TqAdapter 存在但不可用时，跳过动态加载（避免 akshare 网络超时）
        if not self.tq_adapter.is_ready():
            return []

        try:
            # === 策略1: 优先使用 CandidatePoolResolver（覆盖全部8种type）===
            resolver = CandidatePoolResolver(tq_adapter=self.tq_adapter)

            # 构建kwargs传递给resolver
            resolve_kwargs = {}
            if customblockname:
                resolve_kwargs["customblockname"] = customblockname

            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # 如果已在事件循环中，创建task
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        result = pool.submit(
                            asyncio.run, resolver.resolve(spinfo_type, **resolve_kwargs)
                        ).result(timeout=30)
                else:
                    result = loop.run_until_complete(
                        resolver.resolve(spinfo_type, **resolve_kwargs)
                    )

                if result and isinstance(result, list):
                    codes = self._tq_codes_to_tdx(result)
                    if codes:
                        return codes
            except Exception as e:
                logger.debug(
                    "CandidatePool.resolve(%d) 失败，降级到内置逻辑: %s",
                    spinfo_type, e
                )

            # === 策略2: 降级到原有的直接加载逻辑（仅type 0/2/4）===
            if spinfo_type == 2:
                return self._load_all_a_stocks(market)
            elif spinfo_type == 0:
                if customblockname:
                    if '行业' in customblockname:
                        stocks = self._load_stocks_by_sector_list(
                            customblockname, list_type=1
                        )
                        if stocks:
                            return stocks
                    elif '概念' in customblockname:
                        stocks = self._load_stocks_by_sector_list(
                            customblockname, list_type=2
                        )
                        if stocks:
                            return stocks
                    sector_stocks = self._load_sector_stocks(customblockname)
                    if sector_stocks:
                        return sector_stocks
                return self._load_all_a_stocks(market)
            elif spinfo_type == 4:
                if customblockname:
                    try:
                        if hasattr(self.tq_adapter, 'get_sector_stocks'):
                            result = self.tq_adapter.get_sector_stocks(
                                customblockname, block_type=1
                            )
                            if result and isinstance(result, list):
                                stocks = self._tq_codes_to_tdx(result)
                                if stocks:
                                    return stocks
                    except Exception as e:
                        logger.debug(
                            "get_sector_stocks('%s', block_type=1) 失败，降级: %s",
                            customblockname, e
                        )
                sector_stocks = self._load_sector_stocks(customblockname)
                if sector_stocks:
                    return sector_stocks

            # type 1/3/5/6/7 无降级路径（依赖CandidatePoolResolver）
            logger.info("spinfo.type=%d 无法通过降级逻辑加载", spinfo_type)

        except Exception as e:
            logger.warning("根据 spinfo.type=%d 加载候选股票失败: %s", spinfo_type, e)

        return []

    def _load_stocks_by_sector_list(
        self, sector_name: str, list_type: int
    ) -> List[str]:
        """通过板块列表查找匹配板块并加载成员股票。

        Args:
            sector_name: 板块名称（用于匹配）
            list_type: 板块列表类型 (1=行业板块, 2=概念板块)

        Returns:
            股票代码列表，未找到返回空列表
        """
        try:
            if not hasattr(self.tq_adapter, 'get_sector_list'):
                return []
            sector_list = self.tq_adapter.get_sector_list(list_type=list_type)
            if not sector_list or not isinstance(sector_list, list):
                return []
            # 在板块列表中查找匹配的板块
            matched_code = None
            for sector in sector_list:
                if isinstance(sector, dict):
                    name = sector.get('name', '') or sector.get('sector_name', '')
                    code = sector.get('code', '') or sector.get('sector_code', '')
                    if sector_name in name or name in sector_name:
                        matched_code = code
                        break
                elif isinstance(sector, str):
                    # 如果列表项是字符串，直接尝试作为板块代码使用
                    if sector_name in sector:
                        matched_code = sector
                        break
            if not matched_code:
                logger.debug("在板块列表(type=%d)中未找到匹配 '%s' 的板块", list_type, sector_name)
                return []
            # 使用匹配到的板块代码获取成员股票
            if hasattr(self.tq_adapter, 'get_sector_stocks'):
                result = self.tq_adapter.get_sector_stocks(matched_code, block_type=0)
                if result and isinstance(result, list):
                    return self._tq_codes_to_tdx(result)
        except Exception as e:
            logger.debug("通过板块列表加载 '%s' (list_type=%d) 失败: %s", sector_name, list_type, e)
        return []

    def _load_all_a_stocks(self, market: str) -> List[str]:
        """加载全部A股股票代码。

        Args:
            market: 市场选择 ("SZ"/"SH"/"SZ,SH")

        Returns:
            股票代码列表
        """
        codes = []
        try:
            # 优先使用 get_stock_list_by_type(2) 获取全部A股 (spinfo.type=2)
            if hasattr(self.tq_adapter, 'get_stock_list_by_type'):
                try:
                    result = self.tq_adapter.get_stock_list_by_type(2)
                    if result and isinstance(result, list):
                        codes = self._tq_codes_to_tdx(result)
                        if codes:
                            return codes
                except Exception as e:
                    logger.debug("get_stock_list_by_type(2) 失败，降级: %s", e)

                # 降级: 使用 get_stock_list_by_type('50') 获取沪深A股
                try:
                    result = self.tq_adapter.get_stock_list_by_type('50')
                    if result and isinstance(result, list):
                        codes = self._tq_codes_to_tdx(result)
                        if codes:
                            return codes
                except Exception as e:
                    logger.debug("get_stock_list_by_type('50') 失败，降级: %s", e)

                # 降级: 使用 get_stock_list_by_type('5') 获取所有A股
                try:
                    result = self.tq_adapter.get_stock_list_by_type('5')
                    if result and isinstance(result, list):
                        codes = self._tq_codes_to_tdx(result)
                        if codes:
                            return codes
                except Exception as e:
                    logger.debug("get_stock_list_by_type('5') 失败，降级: %s", e)

            # 降级: 使用原有 get_stock_list / resolve_market 逻辑
            if hasattr(self.tq_adapter, 'get_stock_list'):
                markets = [m.strip() for m in market.split(',') if m.strip()]
                for m in markets:
                    result = self.tq_adapter.get_stock_list(m)
                    if result and isinstance(result, list):
                        codes.extend(self._tq_codes_to_tdx(result))
            elif hasattr(self.tq_adapter, 'resolve_market'):
                try:
                    result = self.tq_adapter.resolve_market(market)
                    if result and isinstance(result, list):
                        codes = self._tq_codes_to_tdx(result)
                except Exception as e:
                    logger.debug("resolve_market('%s') 失败: %s", market, e)
        except Exception as e:
            logger.debug("加载全A股失败: %s", e)
        return codes

    def _load_sector_stocks(self, sector_name: str) -> List[str]:
        """加载指定板块的股票代码。

        Args:
            sector_name: 板块名称（如"行业板块"、"概念板块"等）

        Returns:
            股票代码列表
        """
        codes = []
        try:
            # 优先使用 get_sector_stocks 桥接方法
            if hasattr(self.tq_adapter, 'get_sector_stocks'):
                try:
                    result = self.tq_adapter.get_sector_stocks(sector_name, block_type=0)
                    if result and isinstance(result, list):
                        codes = self._tq_codes_to_tdx(result)
                        if codes:
                            return codes
                except Exception as e:
                    logger.debug("get_sector_stocks('%s') 失败，降级: %s", sector_name, e)

            # 降级: 使用原有 get_block_members
            if hasattr(self.tq_adapter, 'get_block_members'):
                result = self.tq_adapter.get_block_members(sector_name)
                if result and isinstance(result, list):
                    codes = self._tq_codes_to_tdx(result)
        except Exception as e:
            logger.debug("加载板块 '%s' 失败: %s", sector_name, e)
        return codes

    def _normalize_code_from_tq(self, tq_code: str) -> str:
        """将TQ格式代码(如'000001.SZ')转换为TDX格式(如'000001')"""
        if '.' in tq_code:
            return tq_code.split('.')[0]
        return tq_code

    def _tq_codes_to_tdx(self, tq_codes: list) -> list:
        """批量将TQ格式代码列表转换为TDX格式

        支持两种输入格式:
        1. 字符串列表: ['000001.SZ', '600000.SH']
        2. 字典列表: [{'setcode': 0, 'code': '000001', 'name': '平安银行'}, ...]
        """
        result = []
        for item in (tq_codes or []):
            if isinstance(item, dict):
                # 新格式: {setcode, code, name}
                code = item.get('code', '')
                if code:
                    result.append(code)
            elif isinstance(item, str):
                # 旧格式: TQ 代码字符串
                result.append(self._normalize_code_from_tq(item))
            else:
                result.append(str(item))
        return result

    # ------------------------------------------------------------------
    # 评估前过滤
    # ------------------------------------------------------------------

    def _apply_pre_filters(self, stock_list: list, func: dict) -> list:
        """评估前过滤：bnost/bnotp/bnotq"""
        if not func or not stock_list:
            return stock_list

        filtered = list(stock_list)
        for flag_key, pred_key, fallback_name in self._PRE_FILTERS:
            if func.get(flag_key, 0) == 1:
                fallback = getattr(self, fallback_name) if fallback_name else None
                filtered = self._filter_by_predicate(
                    filtered, pred_key, no_adapter_fallback=fallback)
        return filtered

    # 预过滤分派表：(func_flag, predicate_key, no_adapter_fallback_method_or_None)
    _PRE_FILTERS = (
        ("bnost", "st", "_fallback_st_no_adapter"),
        ("bnotp", "suspended", None),
        ("bnotq", "no_quote", None),
    )

    def _fallback_st_no_adapter(self, stock_list: list) -> list:
        """无 TqAdapter 时 ST 过滤回退：通过 MOCK_NAMES 过滤。"""
        return [s for s in stock_list
                if not self._get_stock_name(s).startswith(('ST', '*ST'))]

    # 过滤谓词表：predicate_key → predicate_fn(snap) -> bool（True=保留）
    # 合并原 _filter_st_stocks / _filter_suspended_stocks / _filter_no_quote_stocks
    _FILTER_PREDICATES = {
        "st": lambda snap: not str(snap.get('name', '')).startswith(('ST', '*ST')),
        "suspended": lambda snap: float(snap.get('volume', 0) or 0) > 0,
        "no_quote": lambda snap: float(snap.get('close', 0) or 0) > 0,
    }

    def _filter_by_predicate(
        self,
        stock_list: list,
        predicate_key: str,
        no_adapter_fallback=None,
    ) -> list:
        """通用股票过滤：依据 predicate_key 对应的谓词过滤 stock_list。

        Args:
            stock_list: 待过滤的股票代码列表
            predicate_key: _FILTER_PREDICATES 中的键（st/suspended/no_quote）
            no_adapter_fallback: 无 TqAdapter 时使用的回退函数，接收 stock_list
                返回过滤后列表；None 表示无 TqAdapter 时原样返回（保守不过滤）

        Returns:
            过滤后的股票代码列表；过滤异常时原样返回（保守策略）
        """
        predicate = self._FILTER_PREDICATES[predicate_key]
        if not self.tq_adapter or not self.tq_adapter.is_ready():
            if no_adapter_fallback is not None:
                return no_adapter_fallback(stock_list)
            return stock_list
        try:
            tq_codes = [_normalize_code_for_tq(s) for s in stock_list]
            snapshots = self.tq_adapter.get_snapshot(tq_codes) or {}
            return [code for code, tq_code in zip(stock_list, tq_codes)
                    if predicate(snapshots.get(tq_code, {}))]
        except Exception:
            return stock_list  # 过滤失败时不过滤

    # ------------------------------------------------------------------
    # 条件评估
    # ------------------------------------------------------------------

    def _evaluate_tdx_condition(
        self, condition_node: dict, stock_list: List[str]
    ) -> List[str]:
        """评估 TDX 条件节点，返回通过筛选的股票代码列表。

        Args:
            condition_node: type="tdx_condition" 的节点字典
            stock_list: 待筛选的股票代码列表

        Returns:
            通过条件筛选的股票代码列表

        参数说明:
          nset 条件类型路由:
          - nset=0: 技术指标公式（accode指定指标，cfirst/csecond选线）
          - nset=1: 条件选股公式（内置条件如连涨/连跌）
          - nset=2: 专家系统公式（MACD等买卖信号）
          - nset=3: 最新财务选股（ntjindexno选择财务指标）
          - nset=4: 实时行情选股（现价/成交量/涨幅等）
          - nset=5: 逻辑运算选股（noperate变为集合操作:0=并集,1=差集,2=交集）

          noperate 操作符（完整枚举 0~9）:
          - noperate=0: 等于 (=)
          - noperate=1: 大于 (>)
          - noperate=2: 小于 (<)
          - noperate=3: 上穿/金叉 (cross above)
          - noperate=4: 下破/死叉 (cross below) 或 排名(nset=3时)
          - noperate=5: 排名为 (rank equals N) ← 新增
          - noperate=6: 排名前 (top N) ← 已有
          - noperate=7: 排名后 (bottom N) ← 已有
          - noperate=8: 上拐 (turning up, slope>0) ← 新增
          - noperate=9: 下拐 (turning down, slope<0) ← 完善
        """
        if not stock_list:
            return []

        params = condition_node.get('params', {})
        nset = self._get_tdx_param(params, 'nset', 'tdx_func', 'nset', 1)
        ntjindexno = self._get_tdx_param(params, 'ntjindexno', 'tdx_func', 'ntjindexno', 0)
        noperate = self._get_tdx_param(params, 'noperate', 'tdx_func', 'noperate', 0)
        label = condition_node.get('label', '')
        accode = params.get('accode', '')
        func = params.get('tdx_func', params.get('func', {}))

        # ── 评估前过滤：bnost/bnotp/bnotq ────────────────────────
        stock_list = self._apply_pre_filters(stock_list, func)
        if not stock_list:
            return []

        # ── nset=5: 直接转移，不评估 ────────────────────────────────
        if nset == 5:
            return list(stock_list)

        # ── 尝试使用 TqAdapter 真实评估 ──────────────────────────────
        if self.tq_adapter is not None and self.tq_adapter.is_ready():
            passed = self._evaluate_with_adapter(
                stock_list, nset, ntjindexno, noperate, accode, label, params
            )
            if passed is not None:
                return passed
            # 评估失败则降级到 mock 过滤
            logger.warning(
                "TDX条件评估失败(ntjindexno=%d, label=%s)，降级为mock过滤",
                ntjindexno, label
            )
            return self._evaluate_mock_condition(
                stock_list, nset, ntjindexno, noperate, label, params
            )
        else:
            logger.debug(
                "TqAdapter 不可用，TDX条件 ntjindexno=%d 使用 mock 过滤",
                ntjindexno
            )
            return self._evaluate_mock_condition(
                stock_list, nset, ntjindexno, noperate, label, params
            )

    def _evaluate_mock_condition(
        self, stock_list: List[str], nset: int, ntjindexno: int,
        noperate: int, label: str, params: dict
    ) -> List[str]:
        """Mock condition evaluation when TqAdapter is unavailable.
        Uses deterministic hash-based filtering."""
        if nset == 5:
            return list(stock_list)

        # Determine pass rate based on indicator type
        indicator_info = _get_system_indicator(ntjindexno)
        handler_type = indicator_info[1]

        # 表驱动：按 handler_strategies 配置分派 mock 降级处理
        # direct_pass → 透传全部；rank → 取前 N；eval_rule → hash 过滤
        def _mock_rank(ctx, strategy_cfg):
            # sort 分支依赖 nfirst/noperate 等本地参数
            nfirst = int((params or {}).get('nfirst', 0))
            func = (params or {}).get('tdx_func', (params or {}).get('func', {}))
            if isinstance(func, dict):
                nfirst = int(func.get('nfirst', nfirst))
            if _get_compare_type(noperate) == 'rank' and nfirst > 0:
                return stock_list[:nfirst]
            count = max(1, len(stock_list) // 2)
            return stock_list[:count]

        def _mock_eval_rule(ctx):
            # 表驱动：mock_pass_threshold 由 config/tdx_system_indicators.json 声明
            threshold = _get_mock_pass_threshold(ntjindexno)
            passed = []
            for code in stock_list:
                h = hash(f"{code}_{ntjindexno}_{label}") % 100
                if h < threshold:
                    passed.append(code)
            if not passed and stock_list:
                passed = stock_list[:1]  # at least 1 passes
            return passed

        ctx = {
            "stocks": list(stock_list),
            "rank_fn": _mock_rank,
            "eval_rule_fn": _mock_eval_rule,
        }
        strategy_cfg = _get_handler_strategy(handler_type)
        return _dispatch_handler(handler_type, ctx, strategy_cfg)

    # ── 降级: 透传所有股票 (已由 _evaluate_mock_condition 替代) ──────

    def _evaluate_with_adapter(
        self, stock_list: List[str], nset: int, ntjindexno: int,
        noperate: int, accode: str, label: str, params=None
    ) -> Optional[List[str]]:
        """使用 TqAdapter 真实评估条件。返回 None 表示降级到透传。"""
        try:
            # 转换代码格式为 TqAdapter 标准格式
            tq_codes = [_normalize_code_for_tq(c) for c in stock_list]

            if nset == 0 and accode:
                # 自定义公式：尝试通过 TqAdapter 公式引擎评估
                func = (params or {}).get('tdx_func', (params or {}).get('func', {}))
                return self._eval_custom_formula(tq_codes, stock_list, accode, func, params)

            if nset in (1, 4):
                # 系统指标（nset=1）或 nset=4：使用行情快照数据评估
                return self._eval_system_indicator(
                    tq_codes, stock_list, ntjindexno, noperate, label, params
                )

            return None
        except Exception as e:
            logger.warning("TqAdapter 评估异常: %s", e)
            return None

    def _eval_system_indicator(
        self, tq_codes: List[str], stock_list: List[str],
        ntjindexno: int, noperate: int, label: str, params=None
    ) -> Optional[List[str]]:
        """使用 TqAdapter 行情快照评估系统指标条件。"""
        # 获取行情快照
        snapshots = self.tq_adapter.get_snapshot(tq_codes)
        if not snapshots:
            return None

        func = (params or {}).get('tdx_func', (params or {}).get('func', {}))
        noperate = func.get('noperate', (params or {}).get('noperate', 0))

        # ── 尝试使用公式计算（优先路径） ────────────────────────────
        formula_results = None
        formula_detail = None
        formula_config = _get_formula_config(ntjindexno)
        # 如果映射表找不到，尝试从func参数中读取自定义公式配置
        if not formula_config:
            custom_fname = func.get('formula_name', '')
            if custom_fname:
                custom_farg = func.get('formula_arg', '')
                custom_fcount = func.get('return_count', 3)
                formula_config = (custom_fname, custom_farg, custom_fcount)
        if formula_config and self.tq_adapter and self.tq_adapter.is_ready():
            try:
                fname, farg, fcount = formula_config
                nperiod = func.get('nperiod', (params or {}).get('nperiod', 4))
                # 表驱动：period_map 由 config/tdx_enums.json 单份定义
                period_str = _TDX_PERIOD_MAP.get(nperiod, "1d")
                nperiodnum = func.get('nperiodnum',
                                      (params or {}).get('nperiodnum', 0))
                count = nperiodnum if nperiodnum > 0 else 5

                kt = self.current_kline_time or ""
                formula_result = self._eval_formula_via_router(
                    formula_name=fname, formula_arg=farg,
                    tq_codes=tq_codes, period=period_str,
                )
                if formula_result and formula_result.get('success'):
                    formula_results = formula_result.get('result', {})
                    formula_detail = formula_result.get('result_detail', {})
                    logger.debug(
                        "公式计算成功 ntjindexno=%d(%s) formula=%s, "
                        "结果数=%d, detail数=%d", ntjindexno, label, fname,
                        len(formula_results), len(formula_detail)
                    )
            except Exception as e:
                logger.debug(
                    "公式计算失败 ntjindexno=%d(%s): %s，降级到快照评估",
                    ntjindexno, label, e
                )

        # ── noperate=0 时，优先使用 formula_detail 进行金叉/死叉等复杂判断 ──
        # 差异由 compare 字段表达（abs_lt=等于），不在代码里 if noperate
        if _get_compare_type(noperate) == 'abs_lt' and formula_detail:
            cross_result = self._eval_formula_cross_logic(
                stock_list, tq_codes, formula_detail, ntjindexno,
                formula_name=formula_config[0] if formula_config else ''
            )
            if cross_result is not None and cross_result:
                return cross_result

        # ── noperate=2-9: 表驱动委托 evaluators._eval_op 执行 ──────────
        # 差异由 compare 字段表达（cross/inflection/rank 等），不在代码里 if noperate
        if 2 <= noperate <= 9:
            return self._eval_noperate_dispatch(
                stock_list, tq_codes, func, snapshots, formula_results,
                formula_detail
            )

        # ── noperate=0/1 及其他：按指标类型分发 ─────────────────────
        # 查找已知指标映射；缺失时透传但有真实行情数据
        if ntjindexno not in _TDX_SYSTEM_INDICATORS:
            logger.debug(
                "未知TDX系统指标 ntjindexno=%d(%s)，有 %d 只股票通过行情验证",
                ntjindexno, label, len(snapshots)
            )
            # 只返回有行情数据的股票
            passed = []
            for code, tq_code in zip(stock_list, tq_codes):
                if tq_code in snapshots:
                    passed.append(code)
            return passed if passed else None

        indicator_name, handler_type = _get_system_indicator(ntjindexno)

        # 表驱动：按 handler_strategies 配置分派系统指标评估
        # direct_pass → 返回有行情数据的股票；rank → _eval_sort_indicator；
        # eval_rule → _eval_condition_indicator
        # 透传结果：有行情数据的股票（空则 None 触发降级）
        pass_passed = []
        for code, tq_code in zip(stock_list, tq_codes):
            if tq_code in snapshots:
                pass_passed.append(code)
        pass_result = pass_passed if pass_passed else None

        def _real_rank(ctx, strategy_cfg):
            return self._eval_sort_indicator(
                stock_list, tq_codes, snapshots, ntjindexno, noperate
            )

        def _real_eval_rule(ctx):
            return self._eval_condition_indicator(
                stock_list, tq_codes, snapshots, ntjindexno, noperate, params,
                formula_detail
            )

        ctx = {
            "stocks": pass_result,
            "rank_fn": _real_rank,
            "eval_rule_fn": _real_eval_rule,
        }
        strategy_cfg = _get_handler_strategy(handler_type)
        return _dispatch_handler(handler_type, ctx, strategy_cfg)

    def _eval_formula_by_rule(
        self, rule: dict, outvars: dict, ctx: dict
    ) -> Optional[List[str]]:
        """通用公式评估器：按 eval_method + params 执行，差异隐于 eval_rules 表。

        Args:
            rule: eval_rules 表中该 logic_tag 的规则 {eval_method, params}
            outvars: {tq_code: {indicator_name: [values]}} 完整指标数据
            ctx: 上下文 {stock_list, tq_codes}

        Returns:
            通过判断的股票代码列表；None=不支持（由上层返回）
        """
        handler = _EVAL_METHODS.get(rule.get("eval_method"))
        if handler is None:
            return None
        return handler(outvars, rule.get("params", {}), ctx)

    def _eval_formula_cross_logic(
        self, stock_list: List[str], tq_codes: List[str],
        formula_detail: dict, ntjindexno: int,
        formula_name: str = ''
    ) -> Optional[List[str]]:
        """根据 ntjindexno 或 formula_name 判断应使用金叉/死叉/超买/超卖逻辑。

        表驱动：logic_tag 由 formula_logic 集合 + formula_name 按 logic_priority
        优先级解析，评估差异显于 eval_rules 表内容，不在代码里 if logic_tag 分派。
        返回通过判断的股票列表；不支持 formula_detail 判断时返回 None，
        由调用方继续走原有逻辑。

        Args:
            stock_list: TDX 格式股票代码列表
            tq_codes: TQ 格式股票代码列表
            formula_detail: {tq_code: {indicator_name: [values]}} 完整指标数据
            ntjindexno: 系统指标编号
            formula_name: 公式名称（用于自定义指标编号时判断逻辑）
        """
        logic_set = _get_formula_logic(ntjindexno)
        logic_tag = _resolve_formula_logic_tag(logic_set, formula_name)
        if logic_tag is None:
            return None
        rule = _get_formula_eval_rule(logic_tag)
        if not rule:
            return None
        ctx = {"stock_list": stock_list, "tq_codes": tq_codes}
        return self._eval_formula_by_rule(rule, formula_detail, ctx)

    def _eval_sort_indicator(
        self, stock_list: List[str], tq_codes: List[str],
        snapshots: dict, ntjindexno: int, noperate: int
    ) -> Optional[List[str]]:
        """评估排序类指标（如涨幅排序、量比排序）。"""
        scored = []
        for code, tq_code in zip(stock_list, tq_codes):
            snap = snapshots.get(tq_code)
            if not snap:
                continue

            # 表驱动：sort_field 由 config/tdx_system_indicators.json 声明
            sort_field = _get_sort_field(ntjindexno)
            score = float(snap.get(sort_field, 0) or 0) if sort_field else 0
            scored.append((code, score))

        if not scored:
            return None

        # 按分数降序排列
        scored.sort(key=lambda x: x[1], reverse=True)

        if _get_compare_type(noperate) == 'gt':
            # 比较操作: 取前 50-80%
            count = max(1, int(len(scored) * random.uniform(0.5, 0.8)))
            return [c for c, _ in scored[:count]]

        # 存在性检查: 全部通过
        return [c for c, _ in scored]

    def _eval_condition_indicator(
        self, stock_list: List[str], tq_codes: List[str],
        snapshots: dict, ntjindexno: int, noperate: int, params=None,
        formula_detail: dict = None
    ) -> Optional[List[str]]:
        """评估条件类指标。

        当有 formula_detail 时优先使用公式数据进行条件判断，
        降级使用行情快照涨跌幅。
        """
        # ── 优先使用 formula_detail 进行金叉/死叉/超买超卖判断 ──
        if formula_detail:
            detail_result = self._eval_formula_cross_logic(
                stock_list, tq_codes, formula_detail, ntjindexno,
                formula_name=''
            )
            if detail_result is not None:
                return detail_result

        # ── 降级：基于行情快照判断 ──
        passed = []
        for code, tq_code in zip(stock_list, tq_codes):
            snap = snapshots.get(tq_code)
            if not snap:
                continue

            # 表驱动：eval_rule 由 config/tdx_system_indicators.json 声明
            if self._eval_snapshot_by_rule(code, tq_code, snap, ntjindexno, params):
                passed.append(code)

        return passed if passed else None

    def _eval_snapshot_by_rule(self, code, tq_code, snap, ntjindexno, params):
        """通用规则评估器：按 eval_rule 声明式规则评估单只股票快照。

        规则格式（由 config/tdx_system_indicators.json 的 eval_rule 字段声明）：
          - 字段比较: {"field": "change_pct", "op": ">=", "threshold": 9.5}
          - 特殊检查: {"check": "consecutive_rises", "days_field": "nperiodnum", "default_days": 3}
          - 特殊检查: {"check": "not_st"}
          - 特殊检查: {"check": "pass_through"}
        """
        rule = _get_eval_rule(ntjindexno)
        # 字段比较类规则
        if "field" in rule:
            field = rule["field"]
            op = rule.get("op", ">=")
            threshold = float(rule.get("threshold", 0))
            value = float(snap.get(field, 0) or 0)
            compare = _COMPARE_OPS.get(op)
            if compare is None:
                return True
            return compare(value, threshold)
        # 特殊检查类规则（按 check 类型分派，非 ntjindexno 值分派）
        check = rule.get("check", "pass_through")
        handler = _CHECK_OPS.get(check, _check_op_pass_through)
        return handler(self, code, tq_code, snap, params, rule)

    def _check_consecutive_rises(self, tq_code: str, required_days: int) -> bool:
        """检查指定股票最近是否连续上涨 required_days 天。

        通过 TqAdapter 获取日线 K 线数据，排除未来日期后检查最近
        required_days 根日 K 是否都满足 Close[i] > Close[i-1]。

        Args:
            tq_code: TqAdapter 标准格式的股票代码（如 '000001.SZ'）
            required_days: 要求的连续上涨天数

        Returns:
            True 如果最近 required_days 天都上涨；否则 False。
            如果 TqAdapter 不可用或数据不足，则返回 True（降级透传）。
        """
        if not self.tq_adapter or not self.tq_adapter.is_ready():
            return True

        try:
            kline_data = self.tq_adapter.get_kline_data(
                [tq_code], period=6, count=required_days + 5
            )
        except Exception as e:
            logger.debug("获取K线数据失败(%s): %s", tq_code, e)
            return True

        data = kline_data.get(tq_code) if isinstance(kline_data, dict) else None
        if not data:
            return True

        closes = data.get("Close") or data.get("close") or []
        dates = data.get("Date") or data.get("date") or []
        if not closes or len(closes) < required_days + 1:
            return True

        # 排除未来日期，只保留今天及之前的K线
        today_str = datetime.now().strftime("%Y%m%d")
        valid_indices = []
        for i, d in enumerate(dates):
            d_str = str(d).replace("-", "").replace(" ", "").replace(":", "")[:8]
            if d_str <= today_str:
                valid_indices.append(i)

        if len(valid_indices) < required_days + 1:
            return True

        # 取最近 required_days 天的收盘价（相对于前一天是否上涨）
        recent_indices = valid_indices[-required_days - 1 :]
        for i in range(1, len(recent_indices)):
            prev_idx = recent_indices[i - 1]
            curr_idx = recent_indices[i]
            prev_close = float(closes[prev_idx]) if closes[prev_idx] is not None else 0.0
            curr_close = float(closes[curr_idx]) if closes[curr_idx] is not None else 0.0
            if curr_close <= prev_close:
                return False
        return True

    def _eval_custom_formula(
        self, tq_codes: List[str], stock_list: List[str], accode: str,
        func: dict = None, params: dict = None
    ) -> Optional[List[str]]:
        """使用 TqAdapter 公式引擎评估自定义公式。

        优先使用 eval_formula_xg（选股公式），失败时降级到
        eval_formula_zb（指标公式）。
        cfirst 参数传递：当 nset=0 且 cfirst 非空时，作为公式变量名
        传入公式引擎（如 VAR1/VAR2/DDX/连续涨停数 等）。
        """
        func = func or {}
        nperiod = func.get("nperiod", (params or {}).get("nperiod", 4))
        nperiodnum = func.get("nperiodnum", 0)
        # ── 提取 cfirst 参数并传递给公式引擎 ──
        cfirst = func.get('cfirst', (params or {}).get('cfirst', ''))
        noperate = int(func.get('noperate', (params or {}).get('noperate', 0)))
        fsecond = float(func.get('fsecond', 0))
        nsecond = float(func.get('nsecond', 0))

        # 将nperiod数字映射为周期字符串
        # 表驱动：period_map 由 config/tdx_enums.json 单份定义
        period_str = _TDX_PERIOD_MAP.get(nperiod, "1d")

        kt = self.current_kline_time or ""
        # ── 优先通过 FormulaRouter 评估选股公式 ────────────────────
        try:
            result = self._eval_indicator_via_router(
                formula_text=accode, tq_codes=tq_codes, period=period_str,
            )
            if result and 'result' in result:
                passed = self._filter_by_noperate(
                    result['result'], stock_list, tq_codes,
                    noperate, fsecond, nsecond
                )
                if passed:
                    return passed
        except Exception as e:
            logger.debug("自定义公式 FormulaRouter 评估失败: %s", e)

        # ── 降级: 通过 FormulaRouter 评估指标公式 ──────────────────
        try:
            count = nperiodnum if nperiodnum > 0 else 5
            result = self._eval_formula_via_router(
                formula_name=accode, formula_arg=cfirst or "",
                tq_codes=tq_codes, period=period_str,
            )
            if result and result.get('success'):
                zb_result = result.get('result', {})
                passed = self._filter_by_noperate(
                    zb_result, stock_list, tq_codes,
                    noperate, fsecond, nsecond
                )
                if passed:
                    return passed
        except Exception as e:
            logger.debug("自定义公式指标公式降级失败: %s", e)

        # 公式评估失败，降级为透传
        return None

    def _filter_by_noperate(
        self, eval_result: dict, stock_list: List[str], tq_codes: List[str],
        noperate: int, fsecond: float, nsecond: float
    ) -> Optional[List[str]]:
        """根据 noperate 操作符过滤公式评估结果（表驱动委托 _eval_op）。

        差异由 compare 字段表达（rank/cross/inflection 需历史或外部排序→存在即通过；
        gt/lt/abs_lt→委托 _eval_op 标量比较），不在代码里 if noperate。
        """
        compare_type = _get_compare_type(noperate)
        rule = _NOPERATE_RULES.get(str(noperate))
        # 排名/交叉/拐点类：标量无法评估，存在即通过（排名由外部排序后截取）
        pass_through = (compare_type == 'rank' or
                        compare_type in ('abs_lt', 'cross', 'inflection'))
        passed = []
        for code, tq_code in zip(stock_list, tq_codes):
            val = eval_result.get(tq_code)
            if val is True or (val is not None and val != 0 and val != False):
                if pass_through:
                    passed.append(code)
                elif isinstance(val, (int, float)) and rule and _eval_op:
                    try:
                        ctx = _build_op_ctx([val], [fsecond], rule.get("params", {}))
                        if _eval_op(rule, ctx):
                            passed.append(code)
                    except (IndexError, TypeError):
                        pass
                else:
                    passed.append(code)
        return passed if passed else None

    # ------------------------------------------------------------------
    # noperate 表驱动评估（委托 evaluators._eval_op，差异由 expr/prev_expr/curr_expr 表达）
    # ------------------------------------------------------------------

    def _extract_indicator_line(
        self, code: str, tq_code: str, func: dict,
        snapshots: dict, formula_results: dict = None,
        formula_detail: dict = None
    ) -> List[float]:
        """提取单只股票的指标值序列（时间正序：旧→新，l1[-1] 为当前值）。

        优先使用 formula_results 的时间序列，降级使用行情快照 change_pct。
        - formula_results[tq_code] = [v1, v2, ...]（时间正序，直接使用）
        - formula_results[code]['detail'] = [curr, prev, prev2, ...]（时间倒序，需反转）
        """
        line: List[float] = []
        if formula_results and isinstance(formula_results, dict):
            values = formula_results.get(tq_code)
            if isinstance(values, list) and values:
                line = [
                    float(v) if isinstance(v, (int, float)) else 0.0
                    for v in values
                ]
            else:
                raw = formula_results.get(code)
                if isinstance(raw, dict) and 'detail' in raw:
                    detail = raw['detail']
                    if isinstance(detail, list) and detail:
                        line = [
                            float(v) if isinstance(v, (int, float)) else 0.0
                            for v in reversed(detail)
                        ]
        if not line and snapshots:
            snap = snapshots.get(tq_code, {})
            if snap:
                change_pct = float(snap.get('change_pct', 0) or 0)
                line = [change_pct]
        return line

    def _eval_noperate_dispatch(
        self, stock_list: List[str], tq_codes: List[str],
        func: dict, snapshots: dict, formula_results: dict = None,
        formula_detail: dict = None
    ) -> Optional[List[str]]:
        """表驱动 noperate 评估：委托 evaluators._eval_op / _resolve_rank 执行。

        差异由 expr/prev_expr/curr_expr/combine 字段表达（不在代码里 if noperate）：
        - rank 系列：跨股排名，委托 _resolve_rank 按 rank_modes 表返回 code 列表
        - gt/lt/abs_lt/cross/inflection：
          逐股比较，line1 = 单只股票指标序列，line2 = [fsecond]，_eval_op 返回 bool
        - cross/inflection 差异由 prev_expr/curr_expr 表达式字段表达，无需 turning_up 参数
        """
        noperate = int(func.get('noperate', 0))
        fsecond = float(func.get('fsecond', 0))

        compare_type = _get_compare_type(noperate)
        rule = _NOPERATE_RULES.get(str(noperate))
        if not rule:
            return None

        # 提取每只股票的指标值序列（优先 formula_results，降级 snapshots）
        stock_lines = [
            self._extract_indicator_line(
                code, tq_code, func, snapshots, formula_results, formula_detail
            )
            for code, tq_code in zip(stock_list, tq_codes)
        ]

        # 根据 compare 决定执行模式（数据驱动，非 noperate 分支）
        if compare_type == 'rank':
            # 跨股排名：委托 _resolve_rank 按 rank_modes 表处理
            # ranked = [(code, val), ...]，_resolve_rank 返回 code 列表
            rank_rule = _RANK_MODES.get(str(noperate), {})
            ranked = [
                (code, line[-1] if line else 0.0)
                for code, line in zip(stock_list, stock_lines)
            ]
            if not _resolve_rank:
                return None
            try:
                result = _resolve_rank(ranked, fsecond, rank_rule)
            except (IndexError, TypeError):
                return None
            return result if result else None
        else:
            # 逐股比较：line1 = 单只股票指标序列，line2 = [fsecond]
            line2 = [fsecond]
            passed = []
            for code, line in zip(stock_list, stock_lines):
                if not line:
                    continue
                try:
                    ctx = _build_op_ctx(line, line2, rule.get("params", {}))
                    if _eval_op(rule, ctx):
                        passed.append(code)
                except (IndexError, TypeError):
                    continue
            return passed if passed else None

    # ------------------------------------------------------------------
    # 状态池参数应用
    # ------------------------------------------------------------------

    def _apply_psatt(self, state_pool_node: dict, node_id: str) -> None:
        """对 tdx_state_pool 节点应用 psatt 参数。

        基于通达信客户端「股票池状态属性」对话框 + 10个控制变量XML样本验证。
        核心修正：ndeltype 是时间单位（0=天,1=小时,2=分钟,3=秒），不是删除策略。

        ⚠️ DZH vs TDX 范围差异（✅ 已确认 2026-06-10）：
          此方法处理的是 TDX psatt 格式（ndeltype: 0=天,1=小时,2=分钟,3=秒）。
          DZH 原生格式使用 deltype (0=自然日,1=交易日,2=小时,3=分钟,4=秒)，
          由 runtime_simulator.py 的 DelType 枚举处理。DZH的deltype=0/1都是"天"
          但含义不同（自然日 vs 交易日），TDX将两者合并为ndeltype=0。

        - bdel=1 + ndelnum + ndeltype: 启用基于时间的TTL自动删除
          ndeltype=0 → 天(×86400s), 1→小时(×3600s), 2→分钟(×60s), 3→秒(×1s)
          每只股票根据 intime(入池时间) 计算存活时长，超时则淘汰
        - baimpool=1: 标记目标池指向（日志标记）
        - btip=1: 右下角弹窗提示（日志标记）
        - bsound=1 + nsoundtype: 声音预警（系统/自定义WAV）
        - bsavetoblock=1 + blockfile: 自动保存到通达信板块
        - bclearblock=1: 覆盖模式（先清空板块再写入）；0=追加模式
        - bsavehis=1: 保存历史入池记录
        """
        params = state_pool_node.get('params', {})
        psatt = params.get('tdx_psatt', params)
        for flag_key, extra_guard, handler_name in self._PSATT_FLAG_HANDLERS:
            flag_val = psatt.get(flag_key, params.get(flag_key, 0))
            if flag_val == 1 and extra_guard(psatt, params):
                getattr(self, handler_name)(psatt, params, node_id)

    # psatt flag 处理分派表：(flag_key, extra_guard, handler_method_name)
    # flag==1 且 extra_guard(psatt, params) 为真时调用对应 handler
    _PSATT_FLAG_HANDLERS = (
        ("bdel", lambda ps, pm: ps.get("ndelnum", pm.get("ndelnum", 0)) > 0,
         "_handle_psatt_bdel"),
        ("baimpool", lambda ps, pm: True, "_handle_psatt_baimpool"),
        ("btip", lambda ps, pm: True, "_handle_psatt_btip"),
        ("bsound", lambda ps, pm: True, "_handle_psatt_bsound"),
        ("bsavetoblock", lambda ps, pm: bool(ps.get("blockfile", pm.get("blockfile", ""))),
         "_handle_psatt_bsavetoblock"),
    )

    def _handle_psatt_bdel(self, psatt, params, node_id):
        """时间TTL自动删除（核心修正：ndeltype=时间单位）。"""
        import time as _time
        ndelnum = psatt.get('ndelnum', params.get('ndelnum', 0))
        ndeltype = psatt.get('ndeltype', params.get('ndeltype', 0))
        # 时间单位换算表（已通过4个对照XML+截图100%确认）
        _UNIT_SECONDS = {0: 86400, 1: 3600, 2: 60, 3: 1}
        _UNIT_LABELS = {0: '天', 1: '小时', 2: '分钟', 3: '秒'}
        ttl_seconds = ndelnum * _UNIT_SECONDS.get(ndeltype, 86400)
        now_ts = _time.time()

        current_stocks = self._stocks.get(node_id, {})
        surviving = {}
        removed_count = 0
        for code, stock_info in list(current_stocks.items()):
            entry_ts = self._get_stock_entry_timestamp(stock_info)
            if entry_ts is not None and (now_ts - entry_ts) >= ttl_seconds:
                removed_count += 1
                logger.debug(
                    "TTL淘汰: 股票 %s 入池超时(%d%s)，从池 %s 移除",
                    code, ndelnum, _UNIT_LABELS.get(ndeltype, '?'), node_id
                )
            else:
                surviving[code] = stock_info

        if removed_count > 0:
            self._stocks[node_id] = surviving
            logger.info(
                "状态池 %s TTL清理完成: 移除 %d 只，剩余 %d 只 (阈值=%d%s)",
                node_id, removed_count, len(surviving),
                ndelnum, _UNIT_LABELS.get(ndeltype, '?')
            )

    def _handle_psatt_baimpool(self, psatt, params, node_id):
        """目标池标记。"""
        logger.debug("状态池 %s 设置为目标池", node_id)

    def _handle_psatt_btip(self, psatt, params, node_id):
        """弹窗提示标记。"""
        logger.debug("状态池 %s 启用右下角弹窗提示", node_id)

    def _handle_psatt_bsound(self, psatt, params, node_id):
        """声音预警标记。"""
        nsoundtype = psatt.get('nsoundtype', params.get('nsoundtype', 0))
        soundfile = psatt.get('soundfile', params.get('soundfile', ''))
        sound_desc = "系统声音" if nsoundtype == 0 else f"自定义:{soundfile}"
        logger.debug("状态池 %s 启用声音预警 (%s)", node_id, sound_desc)

    def _handle_psatt_bsavetoblock(self, psatt, params, node_id):
        """板块保存（覆盖/追加模式）。"""
        blockfile = psatt.get('blockfile', params.get('blockfile', ''))
        bclearblock = psatt.get('bclearblock', params.get('bclearblock', 0))
        pool_stocks = self._stocks.get(node_id, {})
        if self.tq_adapter is not None:
            mode_label = "覆盖(清空后写)" if bclearblock == 1 else "追加"
            # 覆盖模式：先清空目标板块
            if bclearblock == 1:
                try:
                    self.tq_adapter.clear_sector(block_code=blockfile)
                except Exception as e:
                    logger.warning("清空板块 %s 失败: %s", blockfile, e)
            # 写入股票列表到目标板块
            try:
                self.tq_adapter.send_user_block(
                    block_code=blockfile,
                    stocks=list(pool_stocks.keys())
                )
                logger.info(
                    "状态池 %s 已保存 %d 只股票到板块 %s (模式=%s)",
                    node_id, len(pool_stocks), blockfile, mode_label
                )
            except Exception as e:
                logger.warning("保存股票到板块 %s 失败: %s", blockfile, e)
        else:
            logger.warning("TqAdapter 不可用，无法保存股票到板块 %s", blockfile)

    def _get_stock_entry_timestamp(self, stock_info: dict):
        """从股票信息中提取入池时间戳。

        Args:
            stock_info: 股票字典，含 indate(YYYYMMDD) 和 intime(HHMMSS)

        Returns:
            float 时间戳，或 None（如果缺少时间信息则不执行TTL淘汰）
        """
        import time as _time
        try:
            indate = str(stock_info.get('indate', ''))
            intime_str = str(stock_info.get('intime', '000000'))
            if not indate or len(indate) != 8:
                return None
            # 解析 YYYYMMDD + HHMMSS 为时间戳
            year = int(indate[0:4])
            month = int(indate[4:6])
            day = int(indate[6:8])
            hour = int(intime_str[0:2]) if len(intime_str) >= 2 else 0
            minute = int(intime_str[2:4]) if len(intime_str) >= 4 else 0
            second = int(intime_str[4:6]) if len(intime_str) >= 6 else 0
            import datetime
            dt = datetime.datetime(year, month, day, hour, minute, second)
            return _time.mktime(dt.timetuple())
        except (ValueError, TypeError):
            return None

    # ------------------------------------------------------------------
    # Flow 时间调度
    # ------------------------------------------------------------------

    def _should_execute_by_schedule(self, edge: dict) -> bool:
        """判断当前边(flow)是否应按时间调度执行。

        解析 edge.params 中的 starttype(0~7) 及相关参数，
        判断当前时刻是否满足执行条件。

        Args:
            edge: 边字典，包含 params 中的 tdx 时间调度参数

        Returns:
            True 表示应该执行，False 表示应跳过
        """
        params = edge.get('params', {})
        starttype = int(params.get('starttype', 0))
        handler_name = self._SCHEDULE_DISPATCH.get(starttype)
        if handler_name is None:
            # 未知starttype，默认放行
            logger.warning("未知 starttype=%d，默认放行", starttype)
            return True
        now = datetime.now()
        current_seconds = _hms_to_seconds(now.hour, now.minute, now.second)
        return getattr(self, handler_name)(params, current_seconds)

    # starttype → handler 方法名分派表（替代原 8 路 if/elif）
    _SCHEDULE_DISPATCH = {
        _STARTTYPE_IMMEDIATE: "_sched_immediate",
        _STARTTYPE_DELAY_SECONDS: "_sched_delay_seconds",
        _STARTTYPE_BEFORE_OPEN: "_sched_before_open",
        _STARTTYPE_AFTER_OPEN: "_sched_after_open",
        _STARTTYPE_BEFORE_CLOSE: "_sched_before_close",
        _STARTTYPE_AFTER_CLOSE: "_sched_after_close",
        _STARTTYPE_TRADING_TIME: "_sched_absolute_hms",
        _STARTTYPE_SPECIFIC_TIME: "_sched_absolute_hms",
    }

    def _sched_immediate(self, params, current_seconds):
        # starttype=0: 立即执行，始终返回True
        return True

    def _sched_delay_seconds(self, params, current_seconds):
        # starttype=1: 延迟N秒执行（单次执行模式下放行）
        starttime = int(params.get('starttime', 0))
        logger.debug(
            "flow starttype=1(延迟%d秒)，单次执行模式下放行",
            starttime
        )
        return True

    def _sched_before_open(self, params, current_seconds):
        # starttype=2: 开市前N(秒/分)
        starttime = int(params.get('starttime', 0))
        starttimetype = int(params.get('starttimetype', 0))
        offset_sec = self._resolve_offset_seconds(starttime, starttimetype)
        target = _MARKET_OPEN_TIME - offset_sec
        if current_seconds >= target and current_seconds < _MARKET_OPEN_TIME:
            return True
        if current_seconds < _MARKET_OPEN_TIME:
            return True
        return False

    def _sched_after_open(self, params, current_seconds):
        # starttype=3: 开市后N(秒/分)
        starttime = int(params.get('starttime', 0))
        starttimetype = int(params.get('starttimetype', 0))
        offset_sec = self._resolve_offset_seconds(starttime, starttimetype)
        target = _MARKET_OPEN_TIME + offset_sec
        return current_seconds >= target

    def _sched_before_close(self, params, current_seconds):
        # starttype=4: 收市前N分钟
        offset_min = int(params.get('starttime', 0))
        target = _MARKET_CLOSE_TIME - offset_min * 60
        if current_seconds >= target and current_seconds <= _MARKET_CLOSE_TIME:
            return True
        return False

    def _sched_after_close(self, params, current_seconds):
        # starttype=5: 收市后N分钟
        offset_min = int(params.get('starttime', 0))
        target = _MARKET_CLOSE_TIME + offset_min * 60
        return current_seconds >= target

    def _sched_absolute_hms(self, params, current_seconds):
        # starttype=6/7: 指定(交易日)时间，按 HHMMSS 解析后比较
        target_hms = int(params.get('starttimehms', 0))
        target_h = target_hms // 10000
        target_m = (target_hms % 10000) // 100
        target_s = target_hms % 100
        target_seconds = _hms_to_seconds(target_h, target_m, target_s)
        return current_seconds >= target_seconds

    @staticmethod
    def _resolve_offset_seconds(value: int, timetype: int) -> int:
        """将 starttime/cxtime 值转换为秒数。

        Args:
            value: 原始数值
            timetype: 0=秒(直接返回), 1=分钟(×60), 2=小时(×3600)

        Returns:
            偏移秒数
        """
        if timetype == 1:
            return value * 60
        elif timetype == 2:
            return value * 3600
        return value

    def _check_duration_expired(self, edge: dict) -> bool:
        """检查flow的持续时长窗口是否已到期。

        持续时长(cxtype)是总时间窗口，配合执行间隔(jgtime)决定循环次数：
        - cxtype=0: 一直执行（无限循环），永不过期
        - cxtype=1: 执行N(秒/分)的**时间窗口**内按jgtime间隔循环，超时停止
          例：cxtime=10秒, jgtime=5秒 → t=0执行, t=5执行, t=10到期 → 共2次
        - cxtype=2: 只执行一次后停止

        Args:
            edge: 边字典，包含 params 中的 tdx 时间调度参数

        Returns:
            True 表示持续时长已到期，不应再执行；False 表示可以继续执行
        """
        params = edge.get('params', {})
        cxtype = int(params.get('cxtype', 0))
        handler_name = self._CXTYPE_DISPATCH.get(cxtype)
        if handler_name is None:
            # 未知cxtype，默认不过期
            return False
        now = datetime.now()
        edge_key = id(edge)
        return getattr(self, handler_name)(params, edge_key, now)

    # cxtype → handler 方法名分派表（替代原 3 路 if/elif）
    _CXTYPE_DISPATCH = {
        _CXTYPE_FOREVER: "_cxtype_forever",
        _CXTYPE_DURATION: "_cxtype_duration",
        _CXTYPE_ONCE: "_cxtype_once",
    }

    def _cxtype_forever(self, params, edge_key, now):
        # cxtype=0: 一直执行，永不过期
        return False

    def _cxtype_once(self, params, edge_key, now):
        # cxtype=2: 只执行一次
        exec_count = self._duration_exec_counts.get(edge_key, 0)
        return exec_count >= 1

    def _cxtype_duration(self, params, edge_key, now):
        # cxtype=1: 有限时间窗口模式
        cxtime = int(params.get('cxtime', 0))
        cxtimetype = int(params.get('cxtimetype', 0))

        # 将cxtime转换为秒数
        if cxtimetype == 1:
            window_seconds = cxtime * 60   # 分钟→秒
        else:
            window_seconds = cxtime         # 秒

        # 首次执行时记录起始时间
        if edge_key not in self._duration_start_times:
            self._duration_start_times[edge_key] = now
            return False  # 首次执行，允许通过

        # 检查是否超出时间窗口
        start_time = self._duration_start_times[edge_key]
        elapsed = (now - start_time).total_seconds()

        if elapsed >= window_seconds:
            logger.debug(
                "flow cxtype=1(窗口%d秒) 已到期(已过%.1f秒)，停止执行",
                window_seconds, elapsed
            )
            return True

        return False

    # ------------------------------------------------------------------
    # _ensure_source_stocks
    # ------------------------------------------------------------------

    def _ensure_source_stocks(self, source_id: str) -> List[str]:
        """获取源节点的股票列表；若为空且为候选池则尝试惰性初始化。"""
        source_node = self._nodes.get(source_id, {})
        stocks = list(self._stocks.get(source_id, {}).keys())

        # 类型检查（不可剥离）：仅候选池节点支持惰性初始化（从 params.stocks 注入），
        # 其他节点类型无此语义，是数据完整性检查
        if not stocks and source_node.get('type') == 'tdx_candidate':
            candidate_stocks = source_node.get('params', {}).get('stocks', [])
            for s in candidate_stocks:
                code = s.get('code', '') if isinstance(s, dict) else str(s)
                if code and code not in self._stocks[source_id]:
                    self._stocks[source_id][code] = {
                        "entry_time": self.current_kline_time or datetime.now().isoformat(),
                        "data": s if isinstance(s, dict) else {},
                    }
            stocks = list(self._stocks.get(source_id, {}).keys())

        return stocks

    # ------------------------------------------------------------------
    # 转移日志
    # ------------------------------------------------------------------

    def _log_transfers(self, source_id: str, target_id: str,
                       passed_stocks: List[str], mode: str) -> None:
        """将股票转移记录写入 stock_transfer_log。"""
        try:
            import uuid
            ts = self.current_kline_time or datetime.now().isoformat()
            for code in passed_stocks:
                log_entry = {
                    "log_id": str(uuid.uuid4()),
                    "ts": ts,
                    "source_node_id": source_id,
                    "target_node_id": target_id,
                    "stock_code": code,
                    "transfer_mode": mode,
                    "trigger_condition": f"tdx_flow[{source_id}→{target_id}]",
                    "kline_time": self.current_kline_time,
                }
                self.storage.insert_transfer_log(log_entry)
        except Exception as e:
            logger.debug("写入TDX转移日志失败: %s", e)

    # ------------------------------------------------------------------
    # 核心执行
    # ------------------------------------------------------------------

    def execute_once(self) -> dict:
        """按顺序遍历所有边并执行一次，返回结构化结果。

        Returns:
            {
                "edges_executed": int,
                "total_transferred": int,
                "total_passed": int,
                "total_rejected": int,
                "edge_results": [
                    {"source": str, "target": str, "mode": str,
                     "transferred": int, "passed": int, "rejected": int},
                    ...
                ],
                "node_states": {node_id: stock_count, ...}
            }
        """
        edge_results: List[dict] = []
        total_transferred = 0
        total_passed = 0
        total_rejected = 0

        # ── 提前批量获取所有股票的行情快照 ──────────────────────────
        snapshots: Dict[str, dict] = {}
        if self.tq_adapter and self.tq_adapter.is_ready():
            try:
                all_codes = set()
                for nid, stocks in self._stocks.items():
                    all_codes.update(stocks.keys())
                if all_codes:
                    tq_codes = [_normalize_code_for_tq(c) for c in all_codes]
                    snapshots = self.tq_adapter.get_snapshot(tq_codes) or {}
            except Exception:
                pass

        for edge in self._edges:
            source_id = edge.get('source', {}).get('node_id')
            target_id = edge.get('target', {}).get('node_id')
            params = edge.get('params', {})
            # 兼容前端 tran 字段：0=copy, 1=move
            mode = params.get('mode', 'move' if params.get('tran') == 1 else 'copy')

            if not source_id or not target_id:
                continue

            # 确保运行时库存存在
            if source_id not in self._stocks:
                self._stocks[source_id] = {}
            if target_id not in self._stocks:
                self._stocks[target_id] = {}

            # 获取源股票（含惰性初始化）
            source_stocks = self._ensure_source_stocks(source_id)

            # ── 时间调度检查：starttype 决定是否在当前时刻执行此flow ──
            if not self._should_execute_by_schedule(edge):
                logger.debug(
                    "flow %s→%s 因时间调度未满足条件，跳过执行",
                    source_id, target_id
                )
                continue

            # emptyps 检查：emptyps != 1 且源股票为空时跳过该边
            edge_params = edge.get('params', {})
            emptyps = edge_params.get('emptyps', 0)
            if emptyps != 1 and not source_stocks:
                continue

            # 条件评估
            target_node = self._nodes.get(target_id, {})

            # 类型检查（不可剥离）：仅条件节点需要调用 _evaluate_tdx_condition 进行筛选，
            # 其他节点类型直接透传，是节点语义的数据完整性检查
            if target_node.get('type') == 'tdx_condition':
                # 目标是条件节点 → 在其上评估筛选，结果存入条件节点
                passed_stocks = self._evaluate_tdx_condition(
                    target_node, source_stocks
                )
            else:
                # 非条件节点 → 全部通过
                passed_stocks = list(source_stocks)

            passed_set = set(passed_stocks)
            rejected_stocks = [s for s in source_stocks if s not in passed_set]

            passed_count = len(passed_stocks)
            rejected_count = len(rejected_stocks)

            # 执行转移
            # 类型检查（不可剥离）：move/copy 是两种转移语义（移除源/保留源），
            # 是转移行为的必要分支，无法表驱动
            if mode == 'move':
                # 移动模式：从源节点移除已转移的股票
                for code in passed_stocks:
                    if code in self._stocks[source_id]:
                        del self._stocks[source_id][code]
            # copy 模式：源节点保留不变

            # 写入目标节点（条件节点也写入，作为中间暂存）
            transferred = []
            entry_time = self.current_kline_time or datetime.now().isoformat()
            for code in passed_stocks:
                if code not in self._stocks[target_id]:
                    self._stocks[target_id][code] = {
                        "entry_time": entry_time,
                        "data": {},
                    }
                    transferred.append(code)

            # 在股票转移到状态池时记录入池信息
            # 类型检查（不可剥离）：仅状态池节点需要记录 indate/intime/inprice 入池元数据，
            # 其他节点类型无此字段语义，是数据完整性检查
            if target_node.get('type') == 'tdx_state_pool' and transferred:
                now_dt = datetime.now()
                for code in transferred:
                    tq_code = _normalize_code_for_tq(code)
                    snap = snapshots.get(tq_code, {}) if snapshots else {}
                    existing = self._stocks[target_id].get(code, {})
                    existing.update({
                        "indate": now_dt.strftime("%Y%m%d"),
                        "intime": now_dt.strftime("%H%M%S"),
                        "inprice": float(snap.get("close", 0) or 0) if snap else 0.0,
                    })
                    self._stocks[target_id][code] = existing

            # 对状态池节点应用 psatt 设置
            # 类型检查（不可剥离）：psatt（盘后属性）仅对状态池节点生效，
            # 是节点类型语义的数据完整性检查
            if target_node.get('type') == 'tdx_state_pool':
                self._apply_psatt(target_node, target_id)

            # ── 写入转移日志 ──────────────────────────────────────
            if self.storage and passed_stocks:
                self._log_transfers(source_id, target_id, passed_stocks, mode)

            total_transferred += passed_count
            total_passed += passed_count
            total_rejected += rejected_count

            edge_results.append({
                'source': source_id,
                'target': target_id,
                'mode': mode,
                'transferred': passed_count,
                'passed': passed_count,
                'rejected': rejected_count,
            })

        # 构建 node_states（含股票详情）
        node_states = {}
        for nid, stocks in self._stocks.items():
            node_info: dict = {"count": len(stocks)}
            # 获取前 20 只股票的代码和名称
            stock_details = []
            for code in list(stocks.keys())[:20]:
                name = self._get_stock_name(code)
                stock_details.append({"code": code, "name": name})
            node_info["stocks"] = stock_details
            node_states[nid] = node_info

        # 对 tdx_state_pool 节点增强为 rich market data
        for nid, node in self._nodes.items():
            # 类型检查（不可剥离）：仅状态池节点需要增强为 rich market data（含入池价/快照），
            # 其他节点类型无此展示语义，是数据完整性检查
            if node.get('type') == 'tdx_state_pool' and nid in node_states:
                rich_stocks = []
                for code in list(self._stocks.get(nid, {}).keys()):
                    entry = self._stocks[nid].get(code, {})
                    tq_code = _normalize_code_for_tq(code)
                    snap = snapshots.get(tq_code, {}) if snapshots else {}

                    inprice = float(entry.get('inprice', 0.0) or 0.0)
                    name = self._get_stock_name(code)

                    # setcode: 0=SZ, 1=SH, 2=BJ
                    if code.startswith(('0', '3')):
                        setcode = 0
                    elif code.startswith('6'):
                        setcode = 1
                    elif code.startswith(('4', '8')):
                        setcode = 2
                    else:
                        setcode = 0

                    if snap and snap.get('close') is not None:
                        # 有真实行情数据
                        now = float(snap.get('close', inprice) or inprice)
                        rise = float(snap.get('change_pct', 0.0) or 0.0)
                        volume = int(snap.get('volume', 0) or 0)
                        snap_high = float(snap.get('high', now) or now)
                        snap_low = float(snap.get('low', now) or now)
                        snap_open = float(snap.get('open', now) or now)
                        snap_amount = float(snap.get('amount', 0) or 0)
                        # name 优先从快照获取真实名称
                        name = snap.get('name', '') or name
                        # 收益 = (现价 - 入池价格) / 入池价格 * 100，百分比
                        if inprice > 0:
                            income = round((now - inprice) / inprice * 100, 2)
                        else:
                            income = 0.0
                        # maxrate: 从入池价到最高价的最大涨幅
                        if inprice > 0:
                            maxrate = round((snap_high - inprice) / inprice * 100, 2)
                        else:
                            maxrate = 0.0
                    else:
                        # 无真实行情数据：使用合理 mock 数据填充，保持格式完整
                        # 确保 inprice 合理，使得衍生字段计算有意义
                        if inprice <= 0:
                            inprice = round(random.uniform(5.0, 80.0), 2)
                        now = round(inprice * random.uniform(0.92, 1.12), 2)
                        now = max(0.01, now)
                        rise = round((now - inprice) / inprice * 100, 2)
                        income = rise
                        volume = random.randint(100000, 50000000)
                        snap_high = round(now * random.uniform(1.0, 1.08), 2)
                        snap_low = round(now * random.uniform(0.92, 1.0), 2)
                        snap_open = round(now * random.uniform(0.97, 1.03), 2)
                        snap_amount = round(volume * now, 2)
                        maxrate = round((snap_high - inprice) / inprice * 100, 2)

                    indate = entry.get('indate', datetime.now().strftime("%Y%m%d"))
                    intime = entry.get('intime', datetime.now().strftime("%H%M%S"))

                    # idaynum: 从入池日期到今天的天数
                    idaynum = 0
                    if indate:
                        try:
                            entry_date = datetime.strptime(str(indate), "%Y%m%d")
                            idaynum = max(0, (datetime.now() - entry_date).days)
                        except (ValueError, TypeError):
                            idaynum = random.randint(0, 30) if not snap else 0

                    # maxprice: 入池价 * (1 + maxrate/100)
                    maxprice = round(inprice * (1 + maxrate / 100), 2) if inprice > 0 else round(now, 2)

                    # maxperiod: 从入池到最大涨幅的天数 (简化: 取 idaynum 的一部分)
                    if idaynum > 0 and maxrate > 0:
                        maxperiod = max(1, int(idaynum * random.uniform(0.3, 0.8)))
                    else:
                        maxperiod = 0

                    # maxtime: 达到最大涨幅的时间 (HHMMSS 格式)
                    if maxrate > 0:
                        maxtime = int(random.randint(93000, 150000))
                    else:
                        maxtime = 0

                    rich_stocks.append({
                        "code": code,
                        "name": name,
                        "setcode": setcode,
                        "inprice": inprice,
                        "now": now,
                        "rise": rise,
                        "income": income,
                        "volume": volume,
                        "indate": indate,
                        "intime": intime,
                        "maxrate": maxrate,
                        "maxperiod": maxperiod,
                        "maxtime": maxtime,
                        "maxprice": maxprice,
                        "idaynum": idaynum,
                    })

                node_states[nid]['stocks'] = rich_stocks
                node_states[nid]['stock_data'] = rich_stocks
                node_states[nid]['has_market_data'] = bool(snapshots)

        return {
            'success': True,
            'edges_executed': len(edge_results),
            'total_transferred': total_transferred,
            'transferred': total_transferred,
            'total_passed': total_passed,
            'passed': total_passed,
            'total_rejected': total_rejected,
            'rejected': total_rejected,
            'edge_results': edge_results,
            'node_states': node_states,
        }

    # ------------------------------------------------------------------
    # 查询方法
    # ------------------------------------------------------------------

    def _get_stock_name(self, code: str) -> str:
        """获取股票名称：优先通过 TqAdapter，降级使用 _MOCK_NAMES。"""
        # 尝试通过 TqAdapter 获取快照
        if self.tq_adapter is not None:
            try:
                tq_code = _normalize_code_for_tq(code)
                snapshots = self.tq_adapter.get_snapshot([tq_code])
                if snapshots:
                    snap = snapshots.get(tq_code)
                    if snap and snap.get('name'):
                        return snap['name']
            except Exception:
                pass
        # 降级：使用内置 mock 名称
        return _MOCK_NAMES.get(code, code)

    def get_node_stocks(self, node_id: str) -> List[str]:
        """获取指定节点的当前股票代码列表。"""
        return list(self._stocks.get(node_id, {}).keys())

    def get_all_states(self) -> Dict[str, List[str]]:
        """获取所有节点的股票状态。"""
        return {nid: list(stocks.keys()) for nid, stocks in self._stocks.items()}

    def check_hold_expiry(self, node_id: str, current_time: datetime, max_hold_seconds: int = None) -> List[str]:
        if max_hold_seconds is None:
            return []
        expired = []
        for code, info in list(self._stocks.get(node_id, {}).items()):
            entry_time = info.get("entry_time")
            if entry_time:
                entry_dt = datetime.fromisoformat(entry_time) if isinstance(entry_time, str) else entry_time
                if (current_time - entry_dt).total_seconds() > max_hold_seconds:
                    expired.append(code)
        return expired
# ================================================================
# 导出器（原 tdx_exporter.py）
# ================================================================

# ── DZH cell_type → TDX type mapping ──────────────────────────────────────────
_DZH_TO_TDX_TYPE_EXPORT: Dict[int, int] = {
    200: 8,   # StatePoolCellModel  → 状态池
    201: 3,   # ConditionCellModel  → 条件
    202: 7,   # CandidateCellModel → 备选池
    1:   0,   # LabelCellModel      → 装饰文字
    2:   2,   # ContainerCellModel  → 容器
    3:   3,   # StateColumnModel    → 条件列
    4:   2,   # DiscardCellModel    → 容器（兜底）
    5:   0,   # ArrowCellModel      → 装饰
    6:   0,   # ArrowCellModel (dup)→ 装饰
    203: 8,   # Type203CellModel    → 状态池（兜底）
}

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
    return _DZH_TO_TDX_TYPE_EXPORT.get(ct, ct)


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
# ═══════════════════════════════════════════════════════════════════════════════

def _add_func(cell_elem: ET.Element, cell: Any) -> None:
    """<func> for type=3 cells.  Only emitted when func is not None."""
    func = getattr(cell, 'tdx_func', None)
    if func is None:
        return
    if isinstance(func, TdxFuncModel):
        attrs = func.to_xml_attrs()
    else:
        return
    el = ET.SubElement(cell_elem, 'func')
    for k, v in attrs.items():
        el.set(k, v)


def _add_psatt(cell_elem: ET.Element, cell: Any) -> None:
    """<psatt> for type=8 cells.  Only emitted when psatt is not None."""
    psatt = getattr(cell, 'tdx_psatt', None)
    if psatt is None:
        return
    if isinstance(psatt, TdxPsattModel):
        attrs = psatt.to_xml_attrs()
    else:
        return
    el = ET.SubElement(cell_elem, 'psatt')
    for k, v in attrs.items():
        el.set(k, v)


def _add_spinfo(cell_elem: ET.Element, cell: Any) -> None:
    """<spinfo> for type=7 cells. Only emitted when tdx_spinfo is present."""
    spinfo = getattr(cell, 'tdx_spinfo', None)
    if spinfo is None:
        return
    if isinstance(spinfo, TdxSpinfoModel):
        attrs = spinfo.to_xml_attrs()
    else:
        return
    el = ET.SubElement(cell_elem, 'spinfo')
    for k, v in attrs.items():
        el.set(k, v)


def _add_stks(cell_elem: ET.Element, cell: Any) -> None:
    """<stk> elements for type=7 cells – derive setcode from code prefix."""
    # stocks or stk_list may be present on candidate / state-pool cells
    stocks = getattr(cell, 'tdx_stocks', None) or getattr(cell, 'stocks', None) or getattr(cell, 'stk_list', None)
    if not stocks or not isinstance(stocks, list):
        return
    for s in stocks:
        el = ET.SubElement(cell_elem, 'stk')
        # 优先使用 TDX 扩展字段
        setcode_val = getattr(s, 'setcode', None)
        code_val = getattr(s, 'code', None)

        if setcode_val is not None and code_val is not None:
            # 有完整的 TDX stk 数据
            el.set('setcode', str(setcode_val))
            el.set('code', str(code_val))
            # 输出运行时字段（如果有值）
            for field_name in ['indate', 'intime', 'inprice', 'income', 'now', 'rise', 'volume', 'maxrate', 'maxperiod', 'maxtime', 'maxprice', 'idaynum']:
                field_val = getattr(s, field_name, None)
                if field_val is not None and str(field_val) != '' and str(field_val) != '0' and str(field_val) != '0.00':
                    el.set(field_name, str(field_val))
        else:
            # 回退到推断逻辑
            if hasattr(s, 'tid') and s.tid:
                code = s.tid
            elif hasattr(s, 'label') and s.label:
                code = s.label
            else:
                code = ""
            el.set('setcode', str(_stock_setcode(code)))
            el.set('code', code)


# tdx_type → 子元素构造函数列表（替代原 if/elif 链）
_TYPE_CHILD_BUILDERS = {
    3: [_add_func],
    8: [_add_psatt, _add_stks],
    7: [_add_spinfo, _add_stks],
}


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
export_meta_to_tdx_xml = export_tdx_xml