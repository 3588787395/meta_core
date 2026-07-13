"""json_xml.py - JSON 转换与 XML 构建（合并自 json_converter / xml_builder）。"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ..core.schemas import (
    PoolMetaModel, DynamicCellModel, DynamicFlowModel,
    PositionModel, TdxPsattModel, TdxFuncModel, TdxSpinfoModel,
)

logger = logging.getLogger(__name__)


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
    if version != 1:
        raise ValueError(f"不支持的版本号: {version}，当前仅支持版本 1")

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

_BASE = Path(__file__).parent.parent
_CONFIG = _BASE / "config"


def _load_json_cache(attr_name):
    cache = globals().get(attr_name)
    if cache is None:
        fname = {'_XML_MAP': 'xml_mapping.json', '_HIST_SCHEMA': 'history_schema.json', '_ACTION_CFG': 'action_table.json'}[attr_name]
        try:
            with open(_CONFIG / fname, encoding="utf-8-sig" if 'xml' in fname else "utf-8") as f:
                cache = json.load(f)
        except (OSError, json.JSONDecodeError) as ex:
            # fail-fast 标记：返回带 _load_error 的非空 dict，禁止静默回退空字典
            logger.warning("加载配置表 %s 失败: %s（已标记 _load_error）", fname, ex, exc_info=True)
            cache = {"_load_error": f"无法加载 {fname}: {ex}", "_load_error_file": fname}
        globals()[attr_name] = cache
    return cache


_get_xml_mapping = lambda: _load_json_cache('_XML_MAP')

_STOCK_NAMES = {}
try:
    with open(_CONFIG / "mock_data.json", encoding="utf-8") as f:
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


def _resolve_attr(field, attr_def, ctx):
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
    """表驱动构建TDX XML。兼容两种格式：
      - 前端格式: nodes[i].id=字符串, type='tdx_candidate', edges[j].source/target=字符串
      - TDX格式:  nodes[i].id=数字, dzh_cell_type=数字, edges[j].source/target={node_id:N}
    """
    mapping = _get_xml_mapping()
    pool_cfg, cell_cfg, flow_cfg = mapping['pool'], mapping['cell'], mapping['flow']
    dzh_to_tdx = {int(k): v for k, v in mapping['dzh_to_tdx_type'].items()}
    # 表驱动：前端类型名 → TDX数字类型
    frontend_to_tdx = mapping.get('frontend_to_tdx_type', {})
    root = ET.Element(pool_cfg['root_element'])
    pool_el = ET.SubElement(root, pool_cfg['pool_element'])
    for ad in pool_cfg['pool_attributes']:
        pool_el.set(ad['attr'], str(_resolve_field(pool_data, ad['field'], ad.get('default'))))
    containers = {cd['element']: ET.SubElement(pool_el, cd['element']) for cd in pool_cfg['pool_children']}
    # 表驱动：建立字符串ID→数字ID映射（TDX XML要求数字ID）
    nodes_list = pool_data.get("nodes", [])
    _str_id_map = {}
    for idx, node in enumerate(nodes_list):
        nid = node.get("id", "")
        if nid and not str(nid).isdigit():
            _str_id_map[nid] = str(idx + 1)
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
            val = _apply_attr_defaults(_resolve_attr(ad['field'], ad, ctx), ad, tdx_type)
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
    for edge in pool_data.get("edges", []):
        ep = edge.get("params", {}) or {}
        # 兼容：source/target 可能是字符串或dict
        so_raw = edge.get("source", "")
        to_raw = edge.get("target", "")
        so_str = so_raw.get("node_id", "") if isinstance(so_raw, dict) else str(so_raw)
        to_str = to_raw.get("node_id", "") if isinstance(to_raw, dict) else str(to_raw)
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
            val = _apply_attr_defaults(_resolve_attr(ad['field'], ad, ctx), ad)
            if val is not None:
                fe.set(ad['attr'], str(val))
            elif 'default' in ad:
                fe.set(ad['attr'], str(ad['default']))
    _indent_xml(root)
    with open(filepath, 'wb') as fh:
        fh.write(b'<?xml version="1.0" encoding="GBK"?>\n')
        fh.write(ET.tostring(root, encoding='gbk', xml_declaration=False))


_TRANSFORMS = {
    'str': lambda v, _: str(v),
    'clr_to_str': lambda v, _: str(v) if v != -1 else '',
    'clrtext_to_str': lambda v, _: str(v) if v else '',
}


def _tdx_pool_to_frontend(tdx_pool, name: str) -> dict:
    mapping = _get_xml_mapping()
    node_cfg, edge_cfg = mapping['frontend_node'], mapping['frontend_edge']
    tdx_type_map = {int(k): v for k, v in mapping['tdx_to_frontend_type'].items()}
    pos_cfg = node_cfg['position']
    nodes = []
    for cell in tdx_pool.cells:
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
    for flow in tdx_pool.flows:
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
        'pool_meta': {'type': 'tdx', 'ver': '1.0', 'mode': '1', 'nextid': tdx_pool.nextid, 'backcolor': tdx_pool.backcolor},
    }


def _load_tdx_pool_config(xml_path):
    try:
        _HR = getattr(_load_tdx_pool_config, '_HR', None)
        if _HR is None:
            from .tdx import parse_tdx_xml
            _HR = {"fn": parse_tdx_xml}
            setattr(_load_tdx_pool_config, '_HR', _HR)
        return _tdx_pool_to_frontend(_HR["fn"](xml_path), os.path.basename(xml_path).replace('.xml', ''))
    except Exception as ex:
        # fail-fast 标记：返回带 error 字段的结构，禁止静默回退空字典
        logger.warning("加载TDX池配置失败: %s（已标记 error 字段）", ex, exc_info=True)
        return {"error": f"加载TDX池配置失败: {ex}", "xml_path": str(xml_path)}
