"""pool_validator.py - 池拓扑校验逻辑（从 core/engine.py 剥离的非核心逻辑，Task 5）。

职责：
- 检查条件节点入边/出边数（期望各1条）
- 检查条件节点直连情况（缺少中间状态池时告警）
- 调用 TopologyPatternMatcher 识别当前池的拓扑模式

仅告警不阻断主流程。
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _resolve_node_type(node: Dict, dzh_type_map: Dict, tdx_type_map: Dict) -> str:
    """解析节点的归一化类型字符串。

    Args:
        node: 节点配置字典
        dzh_type_map: dzh_type_map.json:type_map
        tdx_type_map: dzh_type_map.json:tdx_type_map

    Returns:
        归一化后的类型字符串
    """
    rt = node.get('type', '')
    if isinstance(rt, int) or (isinstance(rt, str) and rt):
        k = str(rt)
    elif rt:
        k = str(node.get('dzh_cell_type', 0) or '')
    else:
        k = ''
    if not k:
        return ''
    return tdx_type_map.get(k, dzh_type_map.get(k, k))


def _edge_endpoint(edge: Dict, *keys: str) -> str:
    """从边字典中按 keys 顺序提取端点节点 ID。"""
    for k in keys:
        v = edge.get(k, '')
        if not v:
            continue
        if isinstance(v, str) and v:
            return v
        if isinstance(v, dict):
            nid = v.get('node_id', '') or v.get('id', '')
            if nid:
                return nid
    return ''


def validate_pool_topology(
    nodes: Dict,
    edges: list,
    edge_semantics_cfg: Optional[Dict] = None,
    dzh_type_map: Optional[Dict] = None,
    dzh_full: Optional[Dict] = None,
    topology_matcher: Any = None,
) -> Optional[Dict]:
    """拓扑校验：检查条件节点入边/出边数（期望各1条）及条件节点直连情况。仅告警不阻断。

    同时调用 TopologyPatternMatcher 识别当前池的拓扑模式，返回识别结果。

    Args:
        nodes: 节点字典 {node_id: node_config}
        edges: 边列表
        edge_semantics_cfg: edge_semantics.json 配置（含 transformation_unit.hub_node_types）
        dzh_type_map: dzh_type_map.json:type_map
        dzh_full: 完整的 dzh_type_map.json（含 tdx_type_map）
        topology_matcher: TopologyPatternMatcher 实例

    Returns:
        拓扑模式识别结果字典；无 matcher 时返回 None
    """
    edge_semantics_cfg = edge_semantics_cfg or {}
    dzh_type_map = dzh_type_map or {}
    dzh_full = dzh_full or {}
    tdx_type_map = dzh_full.get('tdx_type_map', {}) if isinstance(dzh_full, dict) else {}

    try:
        tu_cfg = edge_semantics_cfg.get('transformation_unit', {})
        hub_types = set(tu_cfg.get('hub_node_types', []))

        def _rn(n):
            return _resolve_node_type(n, dzh_type_map, tdx_type_map)

        in_count = {}
        out_count = {}
        for edge in edges:
            sid = _edge_endpoint(edge, 'from', 'source', 'startid')
            tid = _edge_endpoint(edge, 'to', 'target', 'endid')
            out_count[sid] = out_count.get(sid, 0) + 1
            in_count[tid] = in_count.get(tid, 0) + 1
            sn = nodes.get(sid, {})
            tn = nodes.get(tid, {})
            st, tt = _rn(sn), _rn(tn)
            if st in hub_types and tt in hub_types:
                logger.warning("拓扑校验：条件节点 %s → 条件节点 %s 直连，缺少中间状态池", sid, tid)
        for nid, nd in nodes.items():
            if _rn(nd) in hub_types:
                ic = in_count.get(nid, 0)
                oc = out_count.get(nid, 0)
                if ic != 1 or oc != 1:
                    logger.warning("拓扑校验：条件节点 %s 入边数=%d 出边数=%d，期望各1条", nid, ic, oc)

        # 拓扑模式识别（配置化）
        pattern = None
        if topology_matcher is not None:
            resolved_types = {nid: _rn(nd) for nid, nd in nodes.items()}
            pattern = topology_matcher.match_pattern(nodes, edges, resolved_types)
            logger.info(
                "拓扑模式识别: %s (%s), 策略=%s, 缓存=%s",
                pattern.get('pattern_id'),
                pattern.get('name', ''),
                pattern.get('execution_strategy'),
                pattern.get('cache_policy'),
            )
        return pattern
    except Exception as ex:
        logger.warning("拓扑校验异常: %s", ex)
        return None
