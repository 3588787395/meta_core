"""matchers.py - 时机判断与拓扑模式匹配（合并自 timing / topology_matcher）。"""
from datetime import datetime, time, timedelta
from typing import Any, Dict, List, Optional
import json
from pathlib import Path
from collections import deque


# ════════════════════════════════════════════════════════════════
# 时机判断（原 timing.py）
# ════════════════════════════════════════════════════════════════

def _now_dt() -> datetime:
    return datetime.now()


def _safe_int(val, default=0):
    if val is None or val == "":
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _time_to_seconds(t: time) -> int:
    return t.hour * 3600 + t.minute * 60 + t.second


def _parse_hhmmss(val) -> Optional[int]:
    if val is None or val == "" or val == "0":
        return None
    try:
        s = str(int(val)).zfill(6)
        h, m, sec = int(s[0:2]), int(s[2:4]), int(s[4:6])
        return h * 3600 + m * 60 + sec
    except (ValueError, TypeError):
        return None


def should_fire(
    edge: dict,
    current_time: datetime,
    param_aliases: dict = None,
    *,
    market_open: datetime = None,
    market_close: datetime = None,
    replay_start_time: float = None,
    flow_fire_counts: dict = None,
    flow_last_fire: dict = None,
) -> bool:
    """判断 Flow/Edge 是否应在当前时间触发。

    统一支持 DZH executor 和 K-line replay engine 的时机判断需求：
    - begin/begint: 开始时间窗口
    - end/endt: 结束时间窗口
    - interval_sec/interval: 触发间隔（秒）
    - cst/cet/cstt/cett/c_period: 自定义周期时间

    Args:
        edge: 包含时控参数的字典（通常含 params / attr）
        current_time: 当前时间
        param_aliases: 参数名映射，如 {"begin": ["starttype", "tdx_starttype"], ...}
        market_open: 开盘时间（replay 场景使用）
        market_close: 收盘时间（replay 场景使用）
        replay_start_time: 回放开始时间戳（用于 delay / duration 判断）
        flow_fire_counts: 各 flow 已触发次数（用于 end_mode==2 执行一次判断）
        flow_last_fire: 各 flow 上次触发时间戳（用于 interval 判断）
    """
    # 参数源：优先 params，其次 attr，最后 edge 本身
    raw_params = edge.get("params")
    params = raw_params if isinstance(raw_params, dict) else (edge if isinstance(edge, dict) else {})
    attr = edge.get("attr", {}) if isinstance(edge.get("attr"), dict) else {}

    def _get_param(key: str, default=None):
        val = params.get(key)
        if val is not None:
            return val
        val = attr.get(key)
        if val is not None:
            return val
        aliases = (param_aliases or {}).get(key, [])
        if isinstance(aliases, str):
            aliases = [aliases]
        for alias in aliases:
            val = params.get(alias)
            if val is not None:
                return val
            val = attr.get(alias)
            if val is not None:
                return val
        return default

    begin_mode = _safe_int(_get_param("begin"), 0)
    begint_val = _get_param("begint", "0") or "0"
    end_mode = _safe_int(_get_param("end"), 0)
    endt_val = _get_param("endt", "0") or "0"
    interval_sec = _safe_int(_get_param("interval_sec", _get_param("interval")), 0)

    cst = _get_param("cst")
    cet = _get_param("cet")
    cstt = _get_param("cstt")
    cett = _get_param("cett")

    now_sec = _time_to_seconds(current_time.time())
    now_ts = current_time.timestamp()

    # 自定义周期时间优先
    if cst is not None or cet is not None:
        try:
            cst_sec = int(cst) if cst else 0
            cet_sec = int(cet) if cet else 86400
            if now_sec < cst_sec or now_sec > cet_sec:
                return False
        except (ValueError, TypeError):
            pass

    if cstt is not None or cett is not None:
        try:
            cstt_sec = int(cstt) if cstt else 0
            cett_sec = int(cett) if cett else 86400
            if now_sec < cstt_sec or now_sec > cett_sec:
                return False
        except (ValueError, TypeError):
            pass

    # begin 窗口判断
    should_begin = False
    if begin_mode == 0:
        should_begin = True
    elif begin_mode == 1:
        delay = _safe_int(begint_val, 0)
        if replay_start_time is not None:
            should_begin = (now_ts - replay_start_time) >= delay
        else:
            should_begin = True  # delay 已在首次调度时处理
    elif begin_mode == 2:
        offset = _safe_int(begint_val, 0)
        if market_open is not None and offset > 0:
            fire_time = market_open - timedelta(seconds=offset)
            should_begin = current_time >= fire_time
        else:
            should_begin = True  # before_open，由外部交易日判断
    elif begin_mode == 3:
        offset = _safe_int(begint_val, 0)
        if market_open is not None:
            open_secs = _time_to_seconds(market_open.time())
            fire_secs = open_secs + offset
            should_begin = now_sec >= fire_secs
        else:
            # DZH 场景：mode 3 作为 HHMMSS 处理
            target_sec = _parse_hhmmss(begint_val)
            should_begin = now_sec >= target_sec if target_sec is not None else True
    elif begin_mode == 4:
        offset = _safe_int(begint_val, 0)
        if market_close is not None:
            close_secs = _time_to_seconds(market_close.time())
            fire_secs = close_secs - offset
            should_begin = now_sec >= fire_secs
        else:
            # DZH 场景：mode 4 作为 HHMMSS 处理
            target_sec = _parse_hhmmss(begint_val)
            should_begin = now_sec >= target_sec if target_sec is not None else True
    elif begin_mode == 5:
        offset = _safe_int(begint_val, 0)
        if market_close is not None:
            fire_time = market_close + timedelta(seconds=offset)
            should_begin = current_time >= fire_time
        else:
            should_begin = True
    elif begin_mode == 7:
        target_sec = _parse_hhmmss(begint_val)
        should_begin = now_sec >= target_sec if target_sec is not None else True
    else:
        should_begin = True

    if not should_begin:
        return False

    # end 窗口判断
    if end_mode == 0:
        pass
    elif end_mode == 1:
        duration = _safe_int(endt_val, 0)
        if replay_start_time is not None:
            elapsed = now_ts - replay_start_time
            if elapsed > duration:
                return False
        else:
            if duration <= 0:
                return False
    elif end_mode == 2:
        if flow_fire_counts is not None:
            eid = edge.get("id", "")
            if flow_fire_counts.get(eid, 0) > 0:
                return False
        # DZH 场景：执行一次由外部调度器管理
    elif end_mode in (3, 4, 7):
        target_sec = _parse_hhmmss(endt_val)
        if target_sec is not None:
            if now_sec > target_sec:
                return False
        # 解析失败则默认通过

    # interval 判断
    if interval_sec > 0 and flow_last_fire is not None:
        eid = edge.get("id", "")
        last_fire_ts = flow_last_fire.get(eid, 0.0)
        if last_fire_ts > 0:
            elapsed_since_fire = now_ts - last_fire_ts
            if elapsed_since_fire < interval_sec:
                return False

    return True


# ════════════════════════════════════════════════════════════════
# 拓扑模式匹配（原 topology_matcher.py）
# ════════════════════════════════════════════════════════════════

class TopologyPatternMatcher:
    """拓扑模式匹配器：从配置表加载模式并按优先级匹配。"""

    # 被视为"源"的节点类型（market_source / candidate）
    _SOURCE_TYPES = {"market_source", "tdx_candidate", "candidate_provider"}
    # 被视为"条件/枢纽"的节点类型
    _CONDITION_TYPES = {"transfer_condition", "tdx_condition", "dzh_condition_pool", "condition_filter"}
    # 被视为"汇/状态池"的节点类型
    _SINK_TYPES = {"stock_state_pool", "tdx_state_pool", "stock_state_fallback", "discard_pool"}

    def __init__(self, config_path=None):
        self._patterns = []
        self._priority = []
        self._fallback = {"pattern_id": "unknown", "execution_strategy": "serial",
                          "cache_policy": "source_fingerprint", "cache_key": "source_id"}
        if config_path is None:
            config_path = Path(__file__).parent.parent / "config" / "topology_patterns.json"
        self.load(config_path)

    def load(self, path):
        """从 JSON 文件加载拓扑模式配置。"""
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        self._patterns = {p["pattern_id"]: p for p in cfg.get("patterns", [])}
        self._priority = cfg.get("detection_priority", list(self._patterns.keys()))
        self._fallback = cfg.get("fallback", self._fallback)

    def _resolve_node_type(self, node):
        """统一解析节点类型字符串。"""
        rt = node.get("type", "") if isinstance(node, dict) else ""
        if isinstance(rt, int):
            rt = str(rt)
        if not rt and isinstance(node, dict):
            rt = str(node.get("dzh_cell_type", 0) or "")
        return rt

    def _is_type(self, node_type, category):
        if category == "source":
            return node_type in self._SOURCE_TYPES
        if category == "condition":
            return node_type in self._CONDITION_TYPES
        if category == "sink":
            return node_type in self._SINK_TYPES
        return node_type == category

    def _build_graph(self, nodes, edges):
        """构建有向图特征：邻接表、入度、出度、源节点、汇节点。"""
        adj = {nid: [] for nid in nodes}
        radj = {nid: [] for nid in nodes}
        in_degree = {nid: 0 for nid in nodes}
        out_degree = {nid: 0 for nid in nodes}

        def _endpoint(edge, *keys):
            for k in keys:
                v = edge.get(k, "")
                if not v:
                    continue
                if isinstance(v, str):
                    return v
                if isinstance(v, dict):
                    nid = v.get("node_id", "") or v.get("id", "")
                    if nid:
                        return nid
            return ""

        for edge in edges:
            sid = _endpoint(edge, "from", "source", "startid")
            tid = _endpoint(edge, "to", "target", "endid")
            if sid in nodes and tid in nodes:
                adj[sid].append(tid)
                radj[tid].append(sid)
                out_degree[sid] += 1
                in_degree[tid] += 1
        return adj, radj, in_degree, out_degree

    def _has_cycle(self, nodes, adj):
        """DFS 检测有向图环。"""
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {nid: WHITE for nid in nodes}

        def dfs(nid):
            color[nid] = GRAY
            for nxt in adj.get(nid, []):
                if nxt not in color:
                    continue
                if color[nxt] == GRAY:
                    return True
                if color[nxt] == WHITE and dfs(nxt):
                    return True
            color[nid] = BLACK
            return False

        for nid in nodes:
            if color[nid] == WHITE and dfs(nid):
                return True
        return False

    def _longest_chain_depth(self, nodes, adj, in_degree):
        """从源节点出发的最长链深度（节点数）。"""
        indeg = {nid: in_degree.get(nid, 0) for nid in nodes}
        q = deque([nid for nid in nodes if indeg[nid] == 0])
        depth = {nid: 1 for nid in nodes}
        max_depth = 1
        while q:
            cur = q.popleft()
            for nxt in adj.get(cur, []):
                depth[nxt] = max(depth[nxt], depth[cur] + 1)
                max_depth = max(max_depth, depth[nxt])
                indeg[nxt] -= 1
                if indeg[nxt] == 0:
                    q.append(nxt)
        return max_depth

    def _evaluate_rule(self, rule, features):
        """单条规则求值。"""
        metric = rule.get("metric")
        expected = rule.get("value")
        min_val = rule.get("min")
        max_val = rule.get("max")

        if metric == "has_cycle":
            return bool(features["has_cycle"]) == bool(expected)
        if metric == "targets_are_condition":
            return bool(features["targets_are_condition"]) == bool(expected)

        value = features.get(metric)
        if value is None:
            return False

        if expected is not None:
            return value == expected
        if min_val is not None and value < min_val:
            return False
        if max_val is not None and value > max_val:
            return False
        return True

    def match_pattern(self, nodes, edges, resolved_types=None):
        """识别拓扑模式，返回匹配到的 pattern 字典（含 pattern_id / execution_strategy 等）。

        Args:
            nodes: dict {node_id: node_dict}
            edges: list of edge dicts
            resolved_types: optional dict {node_id: resolved_type_str}; 若提供则直接使用，
                            否则基于节点自身 type / dzh_cell_type 解析。

        Returns:
            dict: 匹配到的 pattern 配置（包含 pattern_id, execution_strategy, cache_policy, cache_key）
        """
        if not nodes or not edges:
            return self._fallback

        if resolved_types is None:
            resolved_types = {nid: self._resolve_node_type(nd) for nid, nd in nodes.items()}

        adj, radj, in_degree, out_degree = self._build_graph(nodes, edges)
        has_cycle = self._has_cycle(nodes, adj)
        chain_depth = self._longest_chain_depth(nodes, adj, in_degree)

        source_nodes = [nid for nid, nt in resolved_types.items() if self._is_type(nt, "source")]
        sink_nodes = [nid for nid, nt in resolved_types.items() if self._is_type(nt, "sink")]
        condition_nodes = [nid for nid, nt in resolved_types.items() if self._is_type(nt, "condition")]

        # 源节点出度统计
        source_out_degrees = [out_degree.get(nid, 0) for nid in source_nodes]
        max_source_out = max(source_out_degrees) if source_out_degrees else 0

        # 汇节点入度统计
        sink_in_degrees = [in_degree.get(nid, 0) for nid in sink_nodes]
        max_sink_in = max(sink_in_degrees) if sink_in_degrees else 0

        # 全图最大入度/出度
        max_in = max(in_degree.values()) if in_degree else 0
        max_out = max(out_degree.values()) if out_degree else 0

        # 源节点所有出边目标是否都是条件节点
        targets_are_condition = False
        if source_nodes:
            targets = set()
            for sid in source_nodes:
                for tid in adj.get(sid, []):
                    targets.add(tid)
            targets_are_condition = bool(targets) and all(
                self._is_type(resolved_types.get(tid, ""), "condition") for tid in targets
            )

        features = {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "source_count": len(source_nodes),
            "min_source_count": len(source_nodes),
            "sink_count": len(sink_nodes),
            "condition_node_count": len(condition_nodes),
            "max_in_degree": max_in,
            "max_out_degree": max_out,
            "max_source_out_degree": max_source_out,
            "max_sink_in_degree": max_sink_in,
            "min_chain_depth": chain_depth,
            "has_cycle": has_cycle,
            "targets_are_condition": targets_are_condition,
        }

        for pid in self._priority:
            pattern = self._patterns.get(pid)
            if not pattern:
                continue
            rules = pattern.get("match_rules", [])
            if all(self._evaluate_rule(rule, features) for rule in rules):
                return {k: v for k, v in pattern.items() if k != "match_rules"}

        return self._fallback
