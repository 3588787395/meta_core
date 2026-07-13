"""dzh.py - DZH 股票池全流程：XML 解析 / 转换 / 执行 / 导出（合并自 dzh_xml_raw / dzh_converter / dzh_executor / dzh_exporter / dzh_xml_exporter）。"""
import xml.etree.ElementTree as ET
import json
import uuid
import base64
import os
import logging
import re
import time
import random
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape as xml_escape

from ..core.schemas import _parse_attr_bits
from ..native.matchers import should_fire

try:
    from ._common import safe_int
except ImportError:
    from _common import safe_int

logger = logging.getLogger(__name__)


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

_DZH_CFG_DIR = Path(__file__).resolve().parent.parent / "config"
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


def _eval_mask_check(expr, attr):
    """评估位掩码检查表达式。

    支持 &/and/not/|/== 及 FLOW_ATTR_* 常量名。表达式来自
    flow_mode_rules.json，属可信配置；以受限命名空间求值。
    FLOW_ATTR_* 常量由 config/attr_flag_map.json:flow_attr_masks 动态注入。
    """
    masks = _load_dzh_cfg("attr_flag_map.json").get("flow_attr_masks", {})
    namespace = {"attr": attr}
    for key, mask in masks.items():
        namespace["FLOW_ATTR_" + key.upper()] = mask
    return bool(eval(expr, {"__builtins__": {}}, namespace))


def _identify_flow_mode(attr, rules):
    """通用流转模式识别器，按表顺序匹配，首个匹配即返回。"""
    for rule in rules:
        if rule.get("mask_check") == "default":
            return rule["mode"], rule.get("is_outflow", False)
        if _eval_mask_check(rule["mask_check"], attr):
            return rule["mode"], rule.get("is_outflow", False)
    return "pass_through", False


def identify_flow_mode(attr_int):
    masks = _load_dzh_cfg("attr_flag_map.json").get("flow_attr_masks", {})
    bits = {k: bool(attr_int & v) for k, v in masks.items()}
    # 表驱动：从 config/flow_mode_rules.json 按优先级匹配首个命中规则
    rules = _load_dzh_cfg("flow_mode_rules.json").get("flow_mode_rules", [])
    mode, is_outflow = _identify_flow_mode(attr_int, rules)
    return {"mode_name": mode, "is_outflow": is_outflow, "bits": bits}


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


def decode_indiparam(indiparam_str):
    """解析 indiparam 括号包裹的参数元组，如 '(1,25,100,30,15,30)'。

    Args:
        indiparam_str: str 或 None，如 '(1,25,100,30,15,30)' 或空/None。

    Returns:
        list[int] | None: 整数参数列表，解析失败或为空时返回 None。
    """
    if not indiparam_str or not isinstance(indiparam_str, str):
        return None
    s = indiparam_str.strip()
    if not s:
        return None
    # 去除外层括号
    if s.startswith('(') and s.endswith(')'):
        s = s[1:-1]
    if not s:
        return None
    try:
        params = [int(x.strip()) for x in s.split(',') if x.strip()]
        return params if params else None
    except (ValueError, TypeError):
        logger.warning("Failed to decode indiparam: %r", indiparam_str)
        return None


# 表驱动：动作编解码单一数据源，从 filter_action_rules.json 加载映射表
_ACTION_RULES_CACHE = None
def _load_action_rules():
    global _ACTION_RULES_CACHE
    if _ACTION_RULES_CACHE is None:
        cfg_path = Path(__file__).parent.parent / "config" / "filter_action_rules.json"
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


def encode_action(action_type, param=0):
    """共用编码函数：反向求逆 high_type_map/byte_type_map，按 encoding 位移掩码组装整数。"""
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
_CONFIG_DIR = os.path.join(os.path.dirname(_THIS_DIR), "config")

def _load_begin_mode_map():
    """从 config/timing.json 的 begin_mode_map 段加载 begin 值→模式名映射。"""
    cfg_path = os.path.join(_CONFIG_DIR, "timing.json")
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

    cfg_path = os.path.join(_CONFIG_DIR, "dzh_market_mappings.json")
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
_RELOAD_CFG_PATH = os.path.join(_CONFIG_DIR, "dzh_reload_schedule.json")
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





def _decode_xml_content(xml_content):
    if isinstance(xml_content, bytes):
        try:
            text = xml_content.decode("gb18030")
        except Exception:
            try:
                text = xml_content.decode("gbk")
            except Exception:
                text = xml_content.decode("utf-8", errors="replace")
    else:
        text = xml_content
    # Remove encoding declaration to prevent ET.fromstring from re-encoding
    # (the string is already decoded; keeping the declaration causes mojibake)
    import re as _re
    text = _re.sub(r'<\?xml\s+[^?]*encoding=["\'][^"\']*["\'][^?]*\?>', '<?xml version="1.0"?>', text, count=1)
    text = text.replace("\t", "__DZH_TAB__")
    return text


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


def _parse_pos(pos_str):
    if not pos_str:
        return {"x": 0, "y": 0, "width": 0, "height": 0}
    try:
        parts = [int(x.strip()) for x in pos_str.split(",")]
        if len(parts) == 4:
            return {
                "x": parts[0],
                "y": parts[1],
                "width": parts[2] - parts[0],
                "height": parts[3] - parts[1],
            }
    except (ValueError, TypeError):
        pass
    return {"x": 0, "y": 0, "width": 0, "height": 0}


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
        try:
            from ..core.schemas import Cell201AttrBitsModel, FlowAttrBitsModel
            _model_cls = {"Cell201AttrBitsModel": Cell201AttrBitsModel,
                          "FlowAttrBitsModel": FlowAttrBitsModel}[_model_name]
        except ImportError:
            from schemas import Cell201AttrBitsModel, FlowAttrBitsModel
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


def _get_formula_size(indi_b64):
    if not indi_b64 or indi_b64 == "0;":
        return 0
    try:
        decoded = base64.b64decode(indi_b64)
        return len(decoded)
    except Exception:
        return len(indi_b64) if indi_b64 else 0


def _parse_col_list(col_str):
    if not col_str:
        return [2, -1, -2, -3, 7, 14, 8, 10, 17, 45]
    try:
        parts = [int(x.strip()) for x in col_str.split(",") if x.strip()]
        return parts if parts else [2, -1, -2, -3, 7, 14, 8, 10, 17, 45]
    except (ValueError, TypeError):
        return [2, -1, -2, -3, 7, 14, 8, 10, 17, 45]


def _parse_stk_children(cell_elem):
    stocks = []
    for stk in cell_elem.findall("stk"):
        s = {
            "label": stk.get("label", ""),
            "t": stk.get("t", ""),
            "p": stk.get("p", ""),
        }
        tid = stk.get("tid")
        if tid:
            s["tid"] = tid

        hist_elem = stk.find("hist")
        if hist_elem is not None:
            s["hist"] = {
                "t": hist_elem.get("t", ""),
                "p": hist_elem.get("p", ""),
            }

        ana_elem = stk.find("ana")
        if ana_elem is not None:
            s["ana"] = {
                "label": ana_elem.get("label", ""),
                "t": ana_elem.get("t", ""),
                "p": ana_elem.get("p", ""),
            }

        stocks.append(s)
    return stocks


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
        _topo_cfg_path = os.path.join(os.path.dirname(__file__), "..", "config", "topology.json")
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
        try:
            from ..services.providers._common import decode_formula as _decode_indi
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

def _build_cell_default(node, entry, models):
    """默认 CellModel 工厂：按 schema 的 param_mapping 实例化。"""
    params = node.get("params", {})
    pos_data = node.get("position", {})
    position = models.PositionModel(**{k: safe_int(v, 0) for k, v in pos_data.items()
                                       if k in models.PositionModel.model_fields})
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
    params = node.get("params", {})
    pos_data = node.get("position", {})
    position = models.PositionModel(**{k: safe_int(v, 0) for k, v in pos_data.items()
                                       if k in models.PositionModel.model_fields})
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
    params = node.get("params", {})
    pos_data = node.get("position", {})
    position = models.PositionModel(**{k: safe_int(v, 0) for k, v in pos_data.items()
                                       if k in models.PositionModel.model_fields})
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
    from ..core import schemas as models
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


def parse_dzh_xml(xml_content, filename=None):
    text = _decode_xml_content(xml_content)
    root = ET.fromstring(text)

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

    raw_trades = []
    for trade_elem in root.findall(".//trades/trade"):
        td = {}
        for attr_name in ["code", "name", "market", "price", "volume",
                          "buyprice", "sellprice", "direction", "tradetime",
                          "accountno", "tradetype", "rate", "fee"]:
            val = trade_elem.get(attr_name)
            if val is not None:
                td[attr_name] = val
        if td:
            raw_trades.append(td)

    raw_opentrades = []
    for ot_elem in root.findall(".//opentrades/trade"):
        od = {}
        for attr_name in ["code", "name", "market", "targetprice",
                          "orderprice", "volume", "direction",
                          "tradetype", "accountno", "condition"]:
            val = ot_elem.get(attr_name)
            if val is not None:
                od[attr_name] = val
        if od:
            raw_opentrades.append(od)

    _STOCK_HOLDER_TYPES = {"stock_state_pool", "result_pool"}
    for n in nodes:
        if n.get("type") in _STOCK_HOLDER_TYPES:
            orig_stks = n.get("params", {}).get("stocks")
            n["params"]["_orig_stks"] = list(orig_stks) if orig_stks else []

    propagated = _propagate_stocks_to_downstream_pools(nodes, edges)

    result = {
        "name": name,
        "nodes": nodes,
        "edges": edges,
        "schedules": schedules,
        "pool_meta": pool_meta,
        "trades": raw_trades,
        "opentrades": raw_opentrades,
    }
    cell_type_breakdown = {}
    for n in nodes:
        t = n.get("type", "unknown")
        cell_type_breakdown[t] = cell_type_breakdown.get(t, 0) + 1
    result["_meta"] = {
        "cell_count": len(nodes),
        "flow_count": len(edges),
        "stock_count": sum(len(n.get("params", {}).get("stocks", [])) for n in nodes),
        "cell_type_breakdown": cell_type_breakdown,
        "has_ency": pool_meta.get("ency") is not None,
        "topology_mode": _detect_topology_mode(nodes, edges),
        "_propagated_stocks": list(propagated),
    }
    return result


def _parse_dzh_xml_raw(xml_content):
    text = _decode_xml_content(xml_content)
    root = ET.fromstring(text)
    result = {"cells": [], "flows": [], "trades": [], "opentrades": []}

    _FULL_CELL_ATTRS = [
        "id", "type", "name", "attr", "hold", "col",
        "posx", "posy", "pos", "enter", "exit",
        "indi", "inditype", "crc", "sorttype",
        "clr", "text", "width", "histana", "wizd",
        "deltype", "endtime", "delstocktype", "staattr",
        "stocknum", "tmpl", "indiparam", "attrtext",
        "reload", "lastload", "url", "intersection_status",
        "stk_list", "setcode",
        "formula", "order", "top_n",
        "op_type", "source_nodes",
        "result_type",
        "line_type", "color", "style",
    ]

    for cell_elem in root.findall(".//cell"):
        cell_data = {}
        for attr_name in _FULL_CELL_ATTRS:
            val = cell_elem.get(attr_name)
            if val is not None:
                cell_data[attr_name] = val

        if not cell_data.get("id"):
            continue

        cell_data["_type_int"] = cell_data.get("type", "")

        pos_str = cell_data.get("pos", "")
        if pos_str:
            try:
                parts = [int(x.strip()) for x in pos_str.split(",")]
                if len(parts) == 4:
                    cell_data["cx"] = (parts[0] + parts[2]) // 2
                    cell_data["cy"] = (parts[1] + parts[3]) // 2
            except (ValueError, TypeError):
                pass

        ct_val = safe_int(cell_elem.get("type"), 0)

        if ct_val in (102, 103, 104, 200, 203):
            stocks = _parse_stk_children(cell_elem)
            if stocks:
                cell_data["stocks"] = stocks

            anas = _parse_ana_children(cell_elem)
            if anas:
                cell_data["anas"] = anas

            tradeattr = _parse_tradeattr(cell_elem)
            if tradeattr:
                cell_data["tradeattr"] = tradeattr

        result["cells"].append(cell_data)

    for order_idx, flow_elem in enumerate(root.findall(".//flow")):
        flow_data = {}
        for attr_name in ["id", "from", "to", "attr", "begin", "begint",
                          "end", "endt", "interval", "mid", "clr", "count",
                          "cst", "cet", "cstt", "cett", "c_period", "c周期"]:
            val = flow_elem.get(attr_name)
            if val is not None:
                flow_data[attr_name] = val

        src = flow_data.get("from", "")
        tgt = flow_data.get("to", "")
        if src and tgt:
            flow_data["_key"] = "%s>%s" % (src, tgt)
            flow_data["_order"] = order_idx
            result["flows"].append(flow_data)

    for trade_elem in root.findall(".//trades/trade"):
        td = {}
        for attr_name in ["code", "name", "market", "price", "volume",
                          "buyprice", "sellprice", "direction", "tradetime",
                          "accountno", "tradetype", "rate", "fee"]:
            val = trade_elem.get(attr_name)
            if val is not None:
                td[attr_name] = val
        if td:
            result["trades"].append(td)

    for ot_elem in root.findall(".//opentrades/trade"):
        od = {}
        for attr_name in ["code", "name", "market", "targetprice",
                          "orderprice", "volume", "direction",
                          "tradetype", "accountno", "condition"]:
            val = ot_elem.get(attr_name)
            if val is not None:
                od[attr_name] = val
        if od:
            result["opentrades"].append(od)

    return result


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

# 保留 dzh_xml_raw 版本的别名，防止后续 dzh_xml_exporter 同名函数覆盖
_encode_action_raw = encode_action


# ================================================================
# 转换器（原 dzh_converter.py）
# ================================================================


def _v(obj, attr, default=None):
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


def _decode_cell_attr(attr_val, cell=None):
    from ..core.schemas import _parse_attr_bits
    if attr_val is None:
        return {}
    try:
        attr_int = int(attr_val)
    except (ValueError, TypeError):
        return {}
    # Bug fix: 优先使用 cell 元素的 type 字段确定节点类型，而非 attr 位推断
    cell_type = None
    if cell is not None:
        if isinstance(cell, dict):
            cell_type = cell.get('type', '')
        elif hasattr(cell, 'get'):
            cell_type = cell.get('type', '')
    if cell_type:
        result = _parse_attr_bits(str(cell_type), attr_int)
    else:
        # Fallback to attr inference
        if attr_int & 0x800:
            result = _parse_attr_bits("201", attr_int)
        else:
            result = _parse_attr_bits("200", attr_int)
    result["raw"] = attr_int
    return result


def _decode_flow_attr_from_int(attr_val):
    from ..core.schemas import _parse_attr_bits
    if attr_val is None:
        return {}
    try:
        attr_int = int(attr_val)
    except (ValueError, TypeError):
        return {}
    result = _parse_attr_bits("flow", attr_int)
    result["raw"] = attr_int
    return result


def _parse_time_str_converter(t):
    if isinstance(t, int) and t > 99999:
        s = str(t).zfill(6)
    elif isinstance(t, int) and t > 0:
        s = f"{t:06d}"
    elif isinstance(t, str) and t.isdigit():
        s = t.zfill(6)
    else:
        return None
    return f"{s[:2]}:{s[2:4]}:{s[4:6]}"


def _decode_indi_base64(indi_b64):
    if not indi_b64 or indi_b64 == "0;":
        return "", 0
    try:
        decoded = base64.b64decode(indi_b64)
        size = len(decoded)
        try:
            text = decoded.decode("gbk", errors="surrogateescape")
        except Exception:
            try:
                text = decoded.decode("gb18030", errors="surrogateescape")
            except Exception:
                text = decoded.decode("utf-8", errors="surrogateescape")
        return text, size
    except Exception:
        return indi_b64, len(indi_b64)


def _decode_enter_exit_action_converter(action_val):
    if action_val is None:
        return None
    if isinstance(action_val, dict):
        return action_val
    try:
        action_int = int(action_val)
    except (ValueError, TypeError):
        return None
    if action_int == 0:
        return None

    decoded = decode_action(action_int)
    if decoded is None:
        return None

    return decoded


def _decode_flow_attr_with_model(attr_val):
    from ..core.schemas import _parse_attr_bits
    if attr_val is None:
        return {}
    if isinstance(attr_val, dict):
        return attr_val
    try:
        attr_int = int(attr_val)
    except (ValueError, TypeError):
        return {}

    decoded = _parse_attr_bits("flow", attr_int)
    decoded["raw"] = attr_int
    return decoded


def import_dzh_xml_to_meta(xml_content, filename="unknown.xml"):
    return parse_dzh_xml(xml_content, filename)


def convert_dzh_pool_to_meta(dzh_pool_config, raw_xml_data=None):
    if raw_xml_data and (raw_xml_data.get("cells") or raw_xml_data.get("flows")):
        xml_content = _reconstruct_xml(raw_xml_data)
        return parse_dzh_xml(xml_content)
    return dzh_pool_config


def _xml_attr_escape(value):
    """Escape a value for use inside an XML attribute (double-quoted)."""
    return xml_escape(str(value), {'"': "&quot;", "\t": "&#9;", "\n": "&#10;", "\r": "&#13;"})


def _reconstruct_xml(raw_xml_data):
    parts = ['<pool>']
    for cell in raw_xml_data.get("cells", []):
        attrs = []
        for k, v in cell.items():
            if k.startswith("_"):
                continue
            attrs.append(f'{k}="{_xml_attr_escape(v)}"')
        parts.append("<cell " + " ".join(attrs) + "/>")
    for flow in raw_xml_data.get("flows", []):
        attrs = []
        for k, v in flow.items():
            if k.startswith("_"):
                continue
            attrs.append(f'{k}="{_xml_attr_escape(v)}"')
        parts.append("<flow " + " ".join(attrs) + "/>")
    parts.append("</pool>")
    return "\n".join(parts)


def _convert_node_to_cell(node):
    return _build_cell_model(node)


def _convert_edge_to_flow(edge):
    from ..core.schemas import FlowModel, FlowAttrBitsModel
    params = edge.get("params", {})
    source = edge.get("source", {})
    target = edge.get("target", {})
    attr_val = safe_int(params.get("dzh_attr", params.get("attr")), 0)
    attr_decoded = None
    if attr_val != 0:
        attr_decoded = FlowAttrBitsModel.from_int(attr_val)
    return FlowModel(
        from_cell_id=source.get("node_id", ""),
        to_cell_id=target.get("node_id", ""),
        attr=attr_val,
        attr_decoded=attr_decoded,
        clr=safe_int(params.get("clr"), -1),
        count=safe_int(params.get("count")) if params.get("count") is not None else None,
        begin_type=safe_int(params.get("begin"), 0),
        begin_param=safe_int(params.get("begint"), 0),
        end_type=safe_int(params.get("end"), 0),
        end_param=safe_int(params.get("endt"), _NEVER_EXPIRE),
        interval_sec=safe_int(params.get("interval_sec"), 60),
        mid_offset=safe_int(params.get("mid")) if params.get("mid") is not None else None,
        exec_order=safe_int(params.get("exec_order", params.get("_order", 999)), 999),
    )


def raw_dict_to_pool_meta(raw_dict):
    from ..core.schemas import PoolMetaModel
    pool_meta_raw = raw_dict.get("pool_meta", {})
    nodes = raw_dict.get("nodes", [])
    edges = raw_dict.get("edges", [])

    cells = []
    for node in nodes:
        cell = _convert_node_to_cell(node)
        if cell is not None:
            cells.append(cell)

    flows = [_convert_edge_to_flow(e) for e in edges]

    kw = {
        "pool_type": pool_meta_raw.get("type", "ss-pool"),
        "ver": pool_meta_raw.get("ver", "1.0"),
        "mode": pool_meta_raw.get("mode", "1"),
        "nextid": safe_int(pool_meta_raw.get("nextid"), 0),
        "backcolor": safe_int(pool_meta_raw.get("backcolor"), _DEFAULT_BACKCOLOR),
        "ency": pool_meta_raw.get("ency"),
        "cells": cells,
        "flows": flows,
        "stocks": [],
        "trades": raw_dict.get("trades", []),
        "opentrades": raw_dict.get("opentrades", []),
        "warning": pool_meta_raw.get("warning"),
        "system": pool_meta_raw.get("system"),
    }
    return PoolMetaModel(**kw)


def convert_dzh_xml_to_model(xml_content, filename="unknown.xml"):
    raw = import_dzh_xml_to_meta(xml_content, filename)
    return raw_dict_to_pool_meta(raw)

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
        cfg_path = Path(__file__).parent.parent / "config" / "dzh_condition_fallback.json"
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
        self.running = False
        self._timers: List[threading.Timer] = []
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._events: List[Dict] = []
        self._start_time: Optional[float] = None
        self._edge_last_trigger: Dict[str, float] = {}
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

    def _log_event(self, event_type: str, data: Dict):
        event = {
            'event_type': event_type,
            'timestamp': datetime.now().isoformat(),
            'timestamp_ts': time.time(),
            **data,
        }
        self._events.append(event)

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
                self._log_event('hold_expiry', {
                    'node_id': node_id,
                    'removed_count': len(removed),
                    'removed_codes': removed,
                    'hold_sec': hold_sec,
                })
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

        self._log_event('edge_execute', {
            'edge_id': edge_id,
            'source': src_node_id,
            'target': tgt_node_id,
            'mode': mode,
            'passed': result['passed'],
            'rejected': result['rejected'],
            'flags': result['flags_applied'],
        })

        return result

    def _run_loop(self):
        logger.info("执行循环启动")
        while self.running and not self._stop_event.is_set():
            try:
                self._check_all_hold_expiry()

                for edge in self._edges:
                    if not self.running or self._stop_event.is_set():
                        break
                    edge_id = edge.get('id', '')
                    params = edge.get('params', {})
                    interval_sec = safe_int(params.get('interval_sec'), 60)

                    if should_trigger(edge):
                        last = self._edge_last_trigger.get(edge_id, 0)
                        if time.time() - last >= interval_sec:
                            self._execute_edge(edge)
                            self._edge_last_trigger[edge_id] = time.time()
                        else:
                            self._stop_event.wait(1)
                    else:
                        self._stop_event.wait(1)

                self._stop_event.wait(1)
            except Exception as e:
                logger.error("执行循环错误: %s", e)
                self._stop_event.wait(5)
        logger.info("执行循环结束")

    def start(self):
        if self.running:
            logger.warning("执行器已在运行中")
            return
        self.running = True
        self._stop_event.clear()
        self._start_time = time.time()
        self._init_mock_stocks()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._log_event('executor_start', {'config_nodes': len(self._nodes), 'config_edges': len(self._edges)})
        logger.info("DZH执行器已启动")

    def stop(self):
        self.running = False
        self._stop_event.set()
        for timer in self._timers:
            timer.cancel()
        self._timers.clear()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._log_event('executor_stop', {'total_events': len(self._events)})
        logger.info("DZH执行器已停止")

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

    def get_events(self, limit: int = 50) -> List[Dict]:
        return self._events[-limit:]

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
        cfg_path = Path(__file__).parent.parent / "config" / "defaults.json"
        with open(cfg_path, encoding="utf-8") as f:
            defaults = json.load(f)
        return defaults.get("export", {})
    except Exception:
        return {}

_EXPORT_DEFAULTS = _load_export_defaults()

# 表驱动：延迟加载 dzh_type_map.json
_DZH_TYPE_MAP_CACHE = None
def _load_dzh_type_map():
    global _DZH_TYPE_MAP_CACHE
    if _DZH_TYPE_MAP_CACHE is None:
        try:
            cfg_path = Path(__file__).parent.parent / "config" / "dzh_type_map.json"
            with open(cfg_path, encoding="utf-8") as f:
                _DZH_TYPE_MAP_CACHE = json.load(f)
        except Exception:
            _DZH_TYPE_MAP_CACHE = {}
    return _DZH_TYPE_MAP_CACHE

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
    params = node.get("params", {})
    dzh_cell_id = params.get("dzh_cell_id")
    if dzh_cell_id is not None:
        return str(dzh_cell_id)
    return _extract_dzh_id(node["id"])


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


def _export_field_hold(cell_elem, params, field, cell_type=None):
    if _should_export_attr(params, "hold"):
        hold = params.get("hold_sec")
        if hold is not None:
            cell_elem.set("hold", str(hold))


def _export_field_col(cell_elem, params, field, cell_type=None):
    if _should_export_attr(params, "col"):
        col_list = params.get("col_list")
        if col_list is not None:
            if isinstance(col_list, list):
                cell_elem.set("col", ",".join(str(c) for c in col_list))
            else:
                cell_elem.set("col", str(col_list))


def _export_field_width(cell_elem, params, field, cell_type=None):
    if _should_export_attr(params, "width"):
        width_list = params.get("width_list")
        if width_list and isinstance(width_list, list):
            cell_elem.set("width", ",".join(str(w) for w in width_list))


def _export_field_wizd(cell_elem, params, field, cell_type=None):
    if _should_export_attr(params, "wizd"):
        wizd = params.get("wizd")
        if wizd:
            cell_elem.set("wizd", str(wizd).replace("\n", "__DZH_NEWLINE__"))


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
    """200 stocks: 导出 stk 子元素（含 hist/ana 子元素）"""
    _orig = params.get("_orig_stks")
    stocks = _orig if _orig is not None else params.get("stocks")
    if stocks and isinstance(stocks, list):
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

            # 导出 hist 子元素
            hists = s.get("hist")
            if hists and isinstance(hists, list):
                for h in hists:
                    hist_elem = ET.SubElement(stk, "hist")
                    hist_elem.text = None
                    hist_elem.tail = None
                    for hk, hv in h.items():
                        if hv is not None:
                            hist_elem.set(hk, str(hv))

            # 导出 ana 子元素（stk 级别）
            ana_data = s.get("ana")
            if ana_data and isinstance(ana_data, dict):
                ana_elem = ET.SubElement(stk, "ana")
                ana_elem.text = None
                ana_elem.tail = None
                for ak, av in ana_data.items():
                    if av is not None:
                        ana_elem.set(ak, str(av))


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
    "hold": _export_field_hold,
    "col": _export_field_col,
    "width": _export_field_width,
    "wizd": _export_field_wizd,
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
        if n["id"] == node_id:
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

    _enc_val = pool_meta.get("ency")
    if (not pool_present or "ency" in pool_present) and _enc_val is not None:
        root.set("ency", str(_enc_val))
    elif _enc_val is not None and _enc_val != '' and _enc_val != 0:
        root.set("ency", str(_enc_val))

    _warn_val = pool_meta.get("warning")
    if (not pool_present or "warning" in pool_present) and _warn_val is not None:
        root.set("warning", str(_warn_val))
    elif _warn_val is not None and _warn_val != '':
        root.set("warning", str(_warn_val))

    _sys_val = pool_meta.get("system")
    if (not pool_present or "system" in pool_present) and _sys_val is not None:
        root.set("system", str(_sys_val))
    elif _sys_val is not None and _sys_val != '':
        root.set("system", str(_sys_val))

    cells_elem = ET.SubElement(root, "cells")
    flows_elem = ET.SubElement(root, "flows")

    all_cell_ids = set()
    for node in nodes:
        dzh_id = _get_dzh_cell_id(node)
        try:
            all_cell_ids.add(int(dzh_id))
        except (ValueError, TypeError):
            pass

    # 表驱动：从 dzh_type_map.json export_dispatch 加载节点类型→构建函数映射
    _export_dispatch = _load_dzh_type_map().get('export_dispatch', {})
    _type_map = _load_dzh_type_map().get('type_map', {})

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

    sorted_edges = sorted(edges, key=lambda e: e.get("params", {}).get("_order", e.get("params", {}).get("exec_order", 999)))

    for edge in sorted_edges:
        source_node_id = edge.get("source", {}).get("node_id", "")
        target_node_id = edge.get("target", {}).get("node_id", "")
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

    trades = config.get("trades")
    has_trades = pool_meta.get("_has_trades", False)
    if has_trades:
        trades_elem = ET.SubElement(root, "trades")
        if trades:
            for trade in trades:
                trade_elem = ET.SubElement(trades_elem, "trade")
                for key, val in trade.items():
                    trade_elem.set(key, str(val))
    elif trades:
        trades_elem = ET.SubElement(root, "trades")
        for trade in trades:
            trade_elem = ET.SubElement(trades_elem, "trade")
            for key, val in trade.items():
                trade_elem.set(key, str(val))

    opentrades = config.get("opentrades")
    has_opentrades = pool_meta.get("_has_opentrades", False)
    if has_opentrades:
        opentrades_elem = ET.SubElement(root, "opentrades")
        if opentrades:
            for ot in opentrades:
                ot_elem = ET.SubElement(opentrades_elem, "trade")
                for key, val in ot.items():
                    ot_elem.set(key, str(val))
    elif opentrades:
        opentrades_elem = ET.SubElement(root, "opentrades")
        for ot in opentrades:
            ot_elem = ET.SubElement(opentrades_elem, "trade")
            for key, val in ot.items():
                ot_elem.set(key, str(val))

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


class DzhXmlExporter:
    def __init__(self, pretty: bool = True):
        self.pretty = pretty
        self.indent = "  "

    def export(self, graph_data: Dict) -> str:
        """将图数据导出为DZH XML格式"""
        lines = ['<?xml version="1.0" encoding="GB2312"?>']

        pool_meta = graph_data.get("pool_meta", {})
        pool_type = pool_meta.get("type", "ss-pool")
        ver = pool_meta.get("ver", "1.0")
        mode = pool_meta.get("mode", "1")
        nextid = pool_meta.get("nextid", 0)
        backcolor = pool_meta.get("backcolor", _DEFAULT_BACKCOLOR)

        attrs = [f'type="{pool_type}"', f'ver="{ver}"', f'mode="{mode}"',
                 f'nextid="{nextid}"', f'backcolor="{backcolor}"']
        if pool_meta.get("ency"):
            attrs.append(f'ency="{pool_meta["ency"]}"')
        if pool_meta.get("warning"):
            attrs.append(f'warning="{pool_meta["warning"]}"')
        if pool_meta.get("system") is not None:
            attrs.append(f'system="{pool_meta["system"]}"')

        lines.append(f'<pool {" ".join(attrs)}>')

        nodes = graph_data.get("nodes", [])
        for node in nodes:
            cell_xml = self._export_node(node)
            lines.append(cell_xml)

        edges = graph_data.get("edges", [])
        edges = sorted(edges, key=lambda e: e.get("params", {}).get("_order", 0))
        for edge in edges:
            flow_xml = self._export_flow(edge)
            lines.append(flow_xml)

        trades = graph_data.get("trades", [])
        if trades:
            lines.append("<trades>")
            for trade in trades:
                trade_xml = self._export_trade(trade)
                lines.append(trade_xml)
            lines.append("</trades>")

        opentrades = graph_data.get("opentrades", [])
        if opentrades:
            lines.append("<opentrades>")
            for ot in opentrades:
                ot_xml = self._export_opentrade(ot)
                lines.append(ot_xml)
            lines.append("</opentrades>")

        lines.append("</pool>")
        return "\n".join(lines) if self.pretty else "".join(lines)

    @staticmethod
    def _safe_attr_val(val) -> str:
        """将值转为 XML 属性安全字符串，保留 \\n 为 &#10;。"""
        s = str(val)
        s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
        s = s.replace("\n", "&#10;")
        s = s.replace("\t", "__DZH_TAB__")
        return s

    def _export_node(self, node: Dict) -> str:
        params = node.get("params", {})
        pos = node.get("position", {})

        # 兼容两种格式：dzh_cell_type 在顶层或在 params 中
        dzh_type = node.get("dzh_cell_type", 0)
        if dzh_type == 0:
            dzh_type = params.get("dzh_cell_type", 0)

        if dzh_type == 0:
            node_type = node.get("type", "")
            # 尝试从 type 字符串解析为整数
            if isinstance(node_type, str) and node_type.isdigit():
                dzh_type = int(node_type)
            else:
                dzh_type = NODE_TYPE_NAME_MAP.get(node_type, 0)

        if dzh_type == 0:
            return ""

        cell_attrs = [f'id="{node.get("id", "")}"', f'type="{dzh_type}"']

        attr_int = params.get("attr_int", params.get("attr", 0))
        if isinstance(attr_int, str):
            try:
                attr_int = int(attr_int)
            except (ValueError, TypeError):
                attr_int = 0
        if attr_int or "attr" in params.get("_present_attrs", set()):
            cell_attrs.append(f'attr="{attr_int}"')

        x = pos.get("x", 0)
        y = pos.get("y", 0)
        w = pos.get("width", 100)
        h = pos.get("height", 50)
        cell_attrs.append(f'pos="{x},{y},{x + w},{y + h}"')

        clr = params.get("clr", -1)
        if clr != -1:
            cell_attrs.append(f'clr="{clr}"')

        # text 属性：空字符串也要保留（原始 XML 中 text="" 是合法的）
        text = params.get("text", params.get("_orig_text", node.get("label", "")))
        if text is not None:
            safe_text = self._safe_attr_val(text)
            cell_attrs.append(f'text="{safe_text}"')

        # ── 按节点类型将关键字段写入 <cell> 属性（表驱动分派，非 if/elif）──
        # 原始 DZH XML 中这些字段是 <cell> 的属性，parse_dzh_xml 也从属性读取，
        # 因此导出时必须写为属性才能保证往返一致。
        _appender_name = _TYPE_ATTR_APPENDERS.get(dzh_type)
        if _appender_name:
            getattr(self, _appender_name)(params, cell_attrs)

        lines = [f"<cell {' '.join(cell_attrs)}>"]

        # type 200/203 的 tradeattr 仍作为子元素
        if dzh_type in _TRADEATTR_TYPES:
            self._export_tradeattr_lines(params, lines)
        # type 4 无子元素

        stocks = params.get("stocks", [])
        if stocks:
            for stk in stocks:
                stk_attrs = [f'label="{stk.get("label", "")}"']
                if stk.get("t"):
                    stk_attrs.append(f't="{stk.get("t")}"')
                if stk.get("p"):
                    stk_attrs.append(f'p="{stk.get("p")}"')
                if stk.get("tid"):
                    stk_attrs.append(f'tid="{stk.get("tid")}"')
                lines.append(f"<stk {' '.join(stk_attrs)}/>")

        anas = params.get("anas", [])
        if anas:
            for ana in anas:
                ana_attrs = [f'label="{ana.get("label", "")}"']
                if ana.get("t"):
                    ana_attrs.append(f't="{ana.get("t")}"')
                if ana.get("p"):
                    ana_attrs.append(f'p="{ana.get("p")}"')
                lines.append(f"<ana {' '.join(ana_attrs)}/>")

        lines.append("</cell>")
        return "\n".join(lines) if self.pretty else "".join(lines)

    # ── 将各类型节点的字段追加为 <cell> 属性 ──

    def _append_state_pool_attrs(self, params: Dict, cell_attrs: List) -> None:
        """type=200/203 状态池/结果池：将 hold/col/width/histana/wizd 等写入属性。"""
        # hold: 优先使用 hold_sec（秒数），否则使用 hold（天数格式）
        hold_sec = params.get("hold_sec")
        hold = params.get("hold")
        if hold_sec is not None and hold_sec != 0:
            cell_attrs.append(f'hold="{hold_sec}"')
        elif hold is not None and hold != 0:
            cell_attrs.append(f'hold="{hold}"')

        # col: 从 col_list 列表还原为逗号分隔字符串
        col_list = params.get("col_list", [])
        if col_list:
            col_str = ",".join(str(x) for x in col_list)
            cell_attrs.append(f'col="{col_str}"')

        # width: 从 width_list 列表还原
        width_list = params.get("width_list", [])
        if width_list:
            width_str = ",".join(str(x) for x in width_list)
            cell_attrs.append(f'width="{width_str}"')

        for key in ("histana", "wizd", "stocknum", "sorttype", "enter", "exit",
                     "deltype", "tmpl", "intersection_status", "reload", "lastload",
                     "attrtext", "delstocktype", "endtime", "staattr", "result_type"):
            val = params.get(key)
            if val is not None and val != "" and val != 0:
                cell_attrs.append(f'{key}="{self._safe_attr_val(val)}"')

        # enter/exit 的 action 编码值（如果 enter_action/exit_action 存在且 enter/exit 不存在）
        enter_action = params.get("enter_action")
        if enter_action and "enter" not in params:
            enter_val = encode_action(enter_action)
            if enter_val:
                cell_attrs.append(f'enter="{enter_val}"')
        exit_action = params.get("exit_action")
        if exit_action and "exit" not in params:
            exit_val = encode_action(exit_action)
            if exit_val:
                cell_attrs.append(f'exit="{exit_val}"')

    def _append_condition_attrs(self, params: Dict, cell_attrs: List) -> None:
        """type=201 条件节点：将 inditype/crc/indi/sorttype/indiparam 写入属性。"""
        for key in ("inditype", "crc", "sorttype", "indiparam"):
            val = params.get(key)
            if val is not None and val != "":
                cell_attrs.append(f'{key}="{self._safe_attr_val(val)}"')

        indi = params.get("indi")
        if indi is not None and indi != "":
            # 如果 indi 已经是合法 base64，直接使用；否则先编码
            if not all(c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=' for c in indi):
                indi = encode_formula(indi)
            cell_attrs.append(f'indi="{self._safe_attr_val(indi)}"')

    def _append_source_attrs(self, params: Dict, cell_attrs: List) -> None:
        """type=202 源节点：将 attrtext/reload/lastload 写入属性。"""
        raw_attrtext = params.get("raw_attrtext", params.get("attrtext", ""))
        if raw_attrtext:
            cell_attrs.append(f'attrtext="{self._safe_attr_val(raw_attrtext)}"')

        reload_val = params.get("reload_sec", params.get("reload"))
        if reload_val is not None and reload_val != 0:
            cell_attrs.append(f'reload="{reload_val}"')

        lastload = params.get("lastload")
        if lastload is not None and lastload != 0:
            cell_attrs.append(f'lastload="{lastload}"')

    def _export_tradeattr_lines(self, params: Dict, lines: List) -> None:
        """导出 tradeattr 子元素（仅 type=200/203）。"""
        tradeattr = params.get("tradeattr")
        if tradeattr and isinstance(tradeattr, dict):
            ta_attrs = []
            for key, val in tradeattr.items():
                if val is not None and val != "":
                    ta_attrs.append(f'{key}="{self._safe_attr_val(val)}"')
            if ta_attrs:
                lines.append(f'<tradeattr {" ".join(ta_attrs)}/>')

    def _export_flow(self, edge: Dict) -> str:
        params = edge.get("params", {})

        # 兼容两种格式：source/target dict 或 from/to 字符串
        source = edge.get("source", {})
        target = edge.get("target", {})
        if isinstance(source, dict) and "node_id" in source:
            src_id = source.get("node_id", "")
            tgt_id = target.get("node_id", "")
        else:
            src_id = str(edge.get("from", source if isinstance(source, str) else ""))
            tgt_id = str(edge.get("to", target if isinstance(target, str) else ""))

        if src_id.startswith("m_"):
            src_id = src_id[2:]
        if tgt_id.startswith("m_"):
            tgt_id = tgt_id[2:]

        flow_attrs = [f'from="{src_id}"', f'to="{tgt_id}"']

        attr_int = params.get("dzh_attr", params.get("attr_int", 0))
        if isinstance(attr_int, str):
            try:
                attr_int = int(attr_int)
            except (ValueError, TypeError):
                attr_int = 0
        if attr_int:
            flow_attrs.append(f'attr="{attr_int}"')

        clr = params.get("clr", -1)
        if clr != -1:
            flow_attrs.append(f'clr="{clr}"')

        if params.get("count"):
            flow_attrs.append(f'count="{params["count"]}"')

        begin = params.get("begin", 0)
        begint = params.get("begint", "0")
        end = params.get("end", 0)
        endt = params.get("endt", str(_NEVER_EXPIRE))
        interval = params.get("interval_sec", params.get("interval", 60))

        if begin != 0:
            flow_attrs.append(f'begin="{begin}"')
        if begint and begint != "0":
            flow_attrs.append(f'begint="{begint}"')
        if end != 0:
            flow_attrs.append(f'end="{end}"')
        if endt and endt != "0":
            flow_attrs.append(f'endt="{endt}"')
        if interval != 60:
            flow_attrs.append(f'interval="{interval}"')

        if params.get("mid"):
            flow_attrs.append(f'mid="{params["mid"]}"')

        return f"<flow {' '.join(flow_attrs)}/>"

    def _export_trade(self, trade: Dict) -> str:
        attrs = []
        for key in ["code", "name", "market", "price", "volume", "buyprice", "sellprice",
                    "direction", "tradetime", "accountno", "tradetype", "rate", "fee"]:
            if trade.get(key):
                attrs.append(f'{key}="{trade[key]}"')
        return f"<trade {' '.join(attrs)}/>"

    def _export_opentrade(self, ot: Dict) -> str:
        attrs = []
        for key in ["code", "name", "market", "targetprice", "orderprice", "volume",
                    "direction", "tradetype", "accountno", "condition"]:
            if ot.get(key):
                attrs.append(f'{key}="{ot[key]}"')
        return f"<trade {' '.join(attrs)}/>"


def export_pool_to_xml(pool_data: Dict, pretty: bool = True) -> str:
    """便捷函数：导出股票池为DZH XML格式"""
    exporter = DzhXmlExporter(pretty=pretty)
    return exporter.export(pool_data)


def export_pool_to_file(pool_data: Dict, file_path: str, encoding: str = "GB2312") -> None:
    """便捷函数：导出股票池为DZH XML文件"""
    exporter = DzhXmlExporter(pretty=True)
    xml_content = exporter.export(pool_data)
    with open(file_path, "w", encoding=encoding) as f:
        f.write(xml_content)


def compare_xml_content(xml1: str, xml2: str) -> Tuple[bool, List[str]]:
    """比较两个XML内容，忽略格式差异"""
    import xml.etree.ElementTree as ET

    def normalize_xml(content: str) -> str:
        try:
            root = ET.fromstring(content)
            return ET.tostring(root, encoding="unicode")
        except Exception:
            return content.strip()

    norm1 = normalize_xml(xml1)
    norm2 = normalize_xml(xml2)

    differences = []
    if norm1 != norm2:
        differences.append("XML内容不一致")

    return norm1 == norm2, differences


def verify_roundtrip(original_path: str, parsed_data: Dict, exporter: DzhXmlExporter = None) -> Tuple[bool, List[str]]:
    """验证往返转换"""
    if exporter is None:
        exporter = DzhXmlExporter(pretty=True)

    with open(original_path, "rb") as f:
        original_content = f.read()

    parsed = parse_dzh_xml(original_content, original_path)

    for key in ["name", "nodes", "edges", "trades", "opentrades"]:
        if key in parsed_data and key not in parsed:
            parsed[key] = parsed_data[key]

    exported = exporter.export(parsed)

    norm1 = original_content.decode("gb18030", errors="replace").strip()
    try:
        norm2 = exported.encode("gb18030").decode("gb18030").strip()
    except Exception:
        norm2 = exported.strip()

    import xml.etree.ElementTree as ET
    differences = []

    try:
        root1 = ET.fromstring(norm1)
        root2 = ET.fromstring(norm2)

        cells1 = {c.get("id"): c for c in root1.findall(".//cell")}
        cells2 = {c.get("id"): c for c in root2.findall(".//cell")}

        for cell_id in set(cells1.keys()) | set(cells2.keys()):
            if cell_id not in cells1:
                differences.append(f"导出新增cell: {cell_id}")
            elif cell_id not in cells2:
                differences.append(f"导出丢失cell: {cell_id}")

        flows1 = [(f.get("from"), f.get("to")) for f in root1.findall(".//flow")]
        flows2 = [(f.get("from"), f.get("to")) for f in root2.findall(".//flow")]

        for f1 in flows1:
            if f1 not in flows2:
                differences.append(f"导出丢失flow: {f1[0]}->{f1[1]}")

        for f2 in flows2:
            if f2 not in flows1:
                differences.append(f"导出新增flow: {f2[0]}->{f2[1]}")

    except ET.ParseError as e:
        differences.append(f"XML解析错误: {e}")

    return len(differences) == 0, differences
