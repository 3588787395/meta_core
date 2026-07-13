"""编译期静态调度表生成器。

按 ``execute-architecture-migration`` 规格 Task 3 实现：
``Compiler.compile(pool_config)`` 一次性产出 ``CompiledSchedule``，
运行期只读，不再重复解析边端点、filter 类型、边类型、处理计划等。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Set, Tuple

from pydantic import BaseModel, Field

from .evaluators import _nperiod_to_period


# ---------------------------------------------------------------------------
# 配置表惰性加载与缓存（模块级，避免每次编译重复读文件）
# ---------------------------------------------------------------------------

_CONFIG_DIR = Path(__file__).parent.parent / "config"
_CONFIG_CACHE: Dict[str, Any] = {}


def _load_config(name: str) -> Dict[str, Any]:
    """加载 config/ 下的 JSON 配置表，缺失或解析失败时返回空字典。"""
    if name in _CONFIG_CACHE:
        return _CONFIG_CACHE[name]
    path = _CONFIG_DIR / name
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        data = {}
    _CONFIG_CACHE[name] = data
    return data


# ---------------------------------------------------------------------------
# Pydantic v2 模型：编译产物 spec
# ---------------------------------------------------------------------------


class EdgeContext(BaseModel):
    """边静态上下文：运行期只读。

    保留 ``st`` / ``tt`` 别名与 dict-like 访问，兼容 Task 14 之前依赖旧
    ``_resolve_edge_context`` 返回结构的测试与 facade 代码。
    """

    eid: str
    sid: str
    tid: str
    edge_type: Literal["conditional", "unconditional"]
    src_type: str
    tgt_type: str
    is_output: bool
    role: Literal["in_edge", "out_edge"]

    @property
    def st(self) -> str:
        return self.src_type

    @property
    def tt(self) -> str:
        return self.tgt_type

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


class TimingSpec(BaseModel):
    """时机门控规则（读 timing.json + edge params）。"""

    starttype: int = 0
    starttime: int = 0
    starttimetype: int = 0
    starttimehms: int = 0
    cxtype: int = 0
    cxtime: int = 0
    interval_sec: int = 0
    duration_sec: int = 0
    gate_expr: str = ""


class FilterSpec(BaseModel):
    """筛选分派规则（读 dispatch.json）。

    I18：编译期解析 dispatch_key → evaluator_type，运行期按 evaluator_type dict 分派。
    消除 dispatch_key + evaluator 双路径；evaluator_params 承载 scalar 路径 nset_cfg。
    I53：evaluator_type 为唯一运行期分派键；filter_type 降级为元数据（审计追溯），
    不再参与控制流，FormulaEngine.eval 不再按 filter_type 分派。
    """

    filter_type: str = ""
    formula_ref: str = ""
    formula_period: str = ""
    threshold: float = 0.0
    noperate: int = 0
    sorttype: int = 0
    compare_mode: str = ""
    evaluator_type: Literal["pass_through", "formula", "scalar", "set_operation"] = "pass_through"
    evaluator_params: Dict[str, Any] = Field(default_factory=dict)


class PropagateSpec(BaseModel):
    """状态流转规则（读 edge params）。"""

    mode: Literal["copy", "move", "overwrite", "overwrite_copy"] = "copy"
    clear_dest_first: bool = False
    preserve_source: bool = True


class ActionSpec(BaseModel):
    """目标节点副作用动作（读 action_table.json + 目标节点 tdx_psatt）。"""

    target_pool_actions: List[str] = Field(default_factory=list)
    callbacks: List[str] = Field(default_factory=list)


class TTLSpec(BaseModel):
    """TTL 超时淘汰规则（读 tdx_psatt.json + 目标节点 tdx_psatt/params）。

    I16：编译期解析三模式，运行期 ``_run_ttl`` 按 ``check_type`` 分派：
      - ``none``:     bdel=0 或无 TTL 配置，早退
      - ``interval``: TDX 风格（ndelnum × ttl_units[ndeltype]）或 DZH hold
                      （hold × ttl_units[deltype_map[deltype]]），按 ttl_sec 比较 entry_time
      - ``endtime``:  DZH delstocktype=1 + endtime，当前时刻 >= endtime_sec 时
                      （hold_for_ttl>0 按 hold_for_ttl 比较 entry_time，否则删除全部）
    """

    bdel: int = 0
    check_type: Literal["none", "interval", "endtime"] = "none"
    ndelnum: int = 0
    ndeltype: int = 0
    ttl_sec: int = 0
    endtime_sec: int = 0
    hold_for_ttl: int = 0


class CompiledSchedule(BaseModel):
    """编译期静态调度表。"""

    execution_order: List[str]
    edge_ctx: Dict[str, EdgeContext]
    edge_timing_spec: Dict[str, TimingSpec]
    edge_filter_spec: Dict[str, FilterSpec]
    edge_propagate_spec: Dict[str, PropagateSpec]
    edge_action_spec: Dict[str, ActionSpec]
    edge_ttl_spec: Dict[str, TTLSpec]
    node_types: Dict[str, str] = Field(default_factory=dict)
    source_node_ids: Set[str] = Field(default_factory=set)
    # I16：无入边节点的 TTL spec（如预填股票的状态池无入边时仍需 TTL 驱动）
    node_ttl_spec: Dict[str, TTLSpec] = Field(default_factory=dict)
    # 以下字段为 Task 14 兼容性 facade 保留，供旧测试 / MetaEngine 旧 API 读取
    topo_order: List[str] = Field(default_factory=list)
    depths: Dict[str, int] = Field(default_factory=dict)
    nodes: Dict[str, Any] = Field(default_factory=dict)
    edge_index: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 纯函数辅助：节点/边解析、配置表查询
# ---------------------------------------------------------------------------


def _extract_edge_endpoint(edge: Dict[str, Any], keys: Tuple[str, ...]) -> str:
    """从边字典中提取端点 ID。"""
    for k in keys:
        v = edge.get(k)
        if not v:
            continue
        if isinstance(v, str):
            return v
        if isinstance(v, dict):
            nid = v.get("node_id") or v.get("id")
            if nid:
                return str(nid)
    return ""


def _resolve_node_type(node: Dict[str, Any]) -> str:
    """统一节点类型解析：先查 tdx_type_map，再查 dzh_type_map / aliases。"""
    if not isinstance(node, dict):
        return ""

    raw = node.get("type", "")
    dzh_cell = node.get("dzh_cell_type")

    dzh_cfg = _load_config("dzh_type_map.json")
    type_map = dzh_cfg.get("type_map", {})
    aliases = dzh_cfg.get("aliases", {})
    tdx_map = dzh_cfg.get("tdx_type_map", {})

    for key in (raw, dzh_cell):
        if key is None:
            continue
        k = str(key)
        if k in tdx_map:
            return tdx_map[k]
        if k in type_map:
            return type_map[k]

    if isinstance(raw, str):
        if raw in aliases:
            return aliases[raw]
        if raw in type_map.values():
            return raw
        return raw
    return ""


def _load_source_types() -> Set[str]:
    """从 modules.json 读取所有 source 类型模块的 dzh_cell_types + node_type + layout_id，作为源节点判定集合。"""
    cfg = _load_config("modules.json")
    modules = cfg.get("modules", {}) if isinstance(cfg, dict) else {}
    source_types: Set[str] = set()
    for mod in modules.values():
        if not isinstance(mod, dict):
            continue
        if mod.get("type") == "source":
            for ct in mod.get("dzh_cell_types", []):
                source_types.add(str(ct))
            for key in ("node_type", "layout_id"):
                val = mod.get(key)
                if val:
                    source_types.add(str(val))
    return source_types


_SOURCE_TYPES: Set[str] = _load_source_types()


def _is_source_node(node: Dict[str, Any]) -> bool:
    """判断节点是否为源节点（数据入口），依据 modules.json 中 type=source 的 dzh_cell_types。"""
    if not isinstance(node, dict):
        return False
    raw = str(node.get("type", ""))
    dzh = str(node.get("dzh_cell_type", ""))
    return raw in _SOURCE_TYPES or dzh in _SOURCE_TYPES


def _resolve_edge_type(src_type: str) -> Literal["conditional", "unconditional"]:
    """查 edge_semantics.json，按源节点类型判定边类型。"""
    sem_cfg = _load_config("edge_semantics.json")
    edge_types = sem_cfg.get("edge_types", {})
    conditional_sources = set(edge_types.get("conditional", {}).get("source_types", []))
    unconditional_sources = set(edge_types.get("unconditional", {}).get("source_types", []))

    if src_type in conditional_sources:
        return "conditional"
    if src_type in unconditional_sources:
        return "unconditional"
    # 默认回退与旧引擎一致：未知类型视为 conditional
    return "conditional"


def _group_transformation_units(
    edges: List[Dict[str, Any]], nodes: Dict[str, Any]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """将边分组为变换单元（三元组），返回 (units, standalone_edges)。"""
    sem_cfg = _load_config("edge_semantics.json")
    tu_cfg = sem_cfg.get("transformation_unit", {})
    hub_types = set(tu_cfg.get("hub_node_types", []))
    if not hub_types:
        return [], list(edges)

    in_edges_by_hub: Dict[str, List[Dict[str, Any]]] = {}
    out_edges_by_hub: Dict[str, List[Dict[str, Any]]] = {}

    for edge in edges:
        if not isinstance(edge, dict):
            continue
        sid = _extract_edge_endpoint(edge, ("from", "source", "startid"))
        tid = _extract_edge_endpoint(edge, ("to", "target", "endid"))
        st = _resolve_node_type(nodes.get(sid, {}))
        tt = _resolve_node_type(nodes.get(tid, {}))
        if tt in hub_types:
            in_edges_by_hub.setdefault(tid, []).append(edge)
        elif st in hub_types:
            out_edges_by_hub.setdefault(sid, []).append(edge)

    units: List[Dict[str, Any]] = []
    paired = set()
    for hub_id in set(in_edges_by_hub) | set(out_edges_by_hub):
        in_list = in_edges_by_hub.get(hub_id, [])
        out_list = out_edges_by_hub.get(hub_id, [])
        if len(in_list) == 1 and len(out_list) == 1:
            in_edge = in_list[0]
            out_edge = out_list[0]
            units.append({"in_edge": in_edge, "hub_id": hub_id, "out_edge": out_edge})
            paired.add(id(in_edge))
            paired.add(id(out_edge))

    standalone_edges = [e for e in edges if id(e) not in paired]
    return units, standalone_edges


def _build_action_spec(tid: str, nodes: Dict[str, Any]) -> ActionSpec:
    """从目标节点 tdx_psatt 与 action_table.json 编译动作规则。"""
    action_table = _load_config("action_table.json")
    pool_actions = action_table.get("pool_enter_actions", {})
    callbacks_cfg = action_table.get("callback_ops", {})

    tgt_node = nodes.get(tid, {}) if nodes else {}
    tgt_params = tgt_node.get("params", {}) if isinstance(tgt_node, dict) else {}
    psatt = tgt_params.get("tdx_psatt") or tgt_params.get("psatt") or {}
    if not isinstance(psatt, dict):
        psatt = {}

    actions: List[str] = []
    callbacks: List[str] = []
    for key, cfg in pool_actions.items():
        if psatt.get(key) == 1:
            actions.append(key)
            op = cfg.get("op")
            if op:
                callbacks.append(op)

    # baimpool 是目标池标记，未在 action_table 中声明，但属于目标池动作语义
    if psatt.get("baimpool") == 1 and "baimpool" not in actions:
        actions.append("baimpool")

    return ActionSpec(target_pool_actions=actions, callbacks=callbacks)


def _decode_endtime(endtime: int, psatt_cfg: Dict[str, Any], defaults: Dict[str, Any]) -> Optional[Tuple[int, int, int]]:
    """解码 DZH endtime 编码 (3600*HH - 900*MM + SS) 为 (HH, MM, SS)。

    I16：从 ttl_helper.py 迁移到 compiler.py（编译期执行）。
    """
    try:
        et = int(endtime)
    except (ValueError, TypeError):
        return None
    if et <= 0:
        return None
    ss = et % 100
    prefix = et // 100
    candidates: List[Tuple[int, int, int]] = []
    for mm in range(60):
        r = prefix + 9 * mm
        if r >= 0 and r % 36 == 0:
            hh = r // 36
            if 0 <= hh <= 23:
                candidates.append((hh, mm, ss))
    if not candidates:
        return None
    dc = psatt_cfg.get("dzh_endtime_decode", {})
    _tc = defaults.get("trading_calendar", {})
    ts = dc.get("trading_start_sec", _tc.get("open_sec"))
    te = dc.get("trading_end_sec", _tc.get("close_sec"))
    pm = dc.get("preferred_minute", _tc.get("preferred_minute", 30))
    if ts is None or te is None:
        return candidates[0]
    try:
        from ..converters._common import _hms_to_seconds
    except ImportError:
        try:
            from converters._common import _hms_to_seconds
        except ImportError:
            from _common import _hms_to_seconds
    trading = [t for t in candidates if ts <= _hms_to_seconds(t[0], t[1], t[2]) <= te]
    pool = trading if trading else candidates
    pool.sort(key=lambda t: (abs(t[1] - pm), _hms_to_seconds(t[0], t[1], t[2])))
    return pool[0]


def _build_ttl_spec(tid: str, nodes: Dict[str, Any]) -> TTLSpec:
    """从目标节点参数与 tdx_psatt.json 编译 TTL 规则（三模式编译期解析）。

    I16：将 ttl_helper.py 的 _resolve_params + _decode_endtime 迁移到编译期，
    运行期 _run_ttl 只读 TTLSpec.check_type 分派，无参数解析。

    三模式优先级（与旧 _resolve_params 一致）：
      1. delstocktype=1 + endtime>0 → check_type="endtime"
      2. tdx_psatt.bdel=1 → check_type="interval"（ndelnum × ttl_units[ndeltype]）
      3. hold>0 → check_type="interval"（hold × ttl_units[deltype_map[deltype]]）
    """
    psatt_cfg = _load_config("tdx_psatt.json")
    defaults = _load_config("defaults.json")
    ttl_units = psatt_cfg.get("ttl_units", {"0": 86400, "1": 3600, "2": 60, "3": 1, "4": 1})

    tgt_node = nodes.get(tid, {}) if nodes else {}
    tgt_params = tgt_node.get("params", {}) if isinstance(tgt_node, dict) else {}
    if not isinstance(tgt_params, dict):
        tgt_params = {}
    psatt = tgt_params.get("tdx_psatt") or tgt_params.get("psatt") or {}
    if not isinstance(psatt, dict):
        psatt = {}

    # 模式 1: DZH delstocktype=1 + endtime（指定时刻删除）
    _dst = int(tgt_params.get("delstocktype", 0) or 0)
    _etv = int(tgt_params.get("endtime", 0) or 0)
    if _dst == 1 and _etv > 0:
        _ehms = _decode_endtime(_etv, psatt_cfg, defaults)
        if _ehms is not None:
            try:
                from ..converters._common import _hms_to_seconds
            except ImportError:
                try:
                    from converters._common import _hms_to_seconds
                except ImportError:
                    from _common import _hms_to_seconds
            endtime_sec = _hms_to_seconds(_ehms[0], _ehms[1], _ehms[2])
            hold_for_ttl = 0
            _hv = int(tgt_params.get("hold", 0) or 0)
            if _hv > 0:
                _hc = psatt_cfg.get("dzh_hold_compat", {})
                _mp = _hc.get("deltype_map", {}).get(str(int(tgt_params.get("deltype", 0) or 0)), 0)
                hold_for_ttl = _hv * int(ttl_units.get(str(_mp), 1))
            return TTLSpec(
                bdel=1, check_type="endtime",
                endtime_sec=endtime_sec, hold_for_ttl=hold_for_ttl,
            )

    # 模式 2: TDX 风格 bdel=1
    bdel = int(psatt.get("bdel", 0) or 0)
    if bdel == 1:
        ndelnum = int(psatt.get("ndelnum", 0) or 0)
        ndeltype = int(psatt.get("ndeltype", 0) or 0)
        if ndelnum <= 0:
            return TTLSpec(bdel=1, check_type="none", ndelnum=0, ndeltype=ndeltype, ttl_sec=0)
        unit_sec = int(ttl_units.get(str(ndeltype), 0) or 0)
        return TTLSpec(
            bdel=1, check_type="interval",
            ndelnum=ndelnum, ndeltype=ndeltype, ttl_sec=ndelnum * unit_sec,
        )

    # 模式 3: DZH hold（hold + deltype → deltype_map → ndeltype）
    hold = tgt_params.get("hold")
    if hold is not None and int(hold) > 0:
        _hc = psatt_cfg.get("dzh_hold_compat", {})
        _hold_mode = _hc.get("hold_mode", {"bdel": 1, "ndeltype": 4})
        ndeltype = int(_hold_mode.get("ndeltype", 4))
        _deltype_map = _hc.get("deltype_map", {})
        _deltype_default = _hc.get("deltype_default", 4)
        _raw_deltype = int(tgt_params.get("deltype", 0) or 0)
        _mapped = _deltype_map.get(str(_raw_deltype), _deltype_default)
        ndeltype = int(_mapped)
        ndelnum = int(hold)
        unit_sec = int(ttl_units.get(str(ndeltype), 1) or 1)
        return TTLSpec(
            bdel=1, check_type="interval",
            ndelnum=ndelnum, ndeltype=ndeltype, ttl_sec=ndelnum * unit_sec,
        )

    return TTLSpec(bdel=0, check_type="none", ttl_sec=0)


# ---------------------------------------------------------------------------
# Compiler
# ---------------------------------------------------------------------------


def _normalize_nodes(pool_config: Dict[str, Any]) -> Dict[str, Any]:
    """统一 nodes 为 {nid: node} 字典。"""
    nodes_input = pool_config.get("nodes", {})
    if isinstance(nodes_input, dict):
        return dict(nodes_input)
    nodes: Dict[str, Any] = {}
    for n in nodes_input:
        if isinstance(n, dict):
            nid = n.get("id") or n.get("node_id")
            if nid:
                nodes[str(nid)] = n
    return nodes


class Compiler:
    """股票池配置编译器：输出 ``CompiledSchedule`` 供运行期只读使用。"""

    # 类方法数 = 6：compile / _build_execution_order / _build_edge_ctx /
    # _build_timing_spec / _build_filter_spec / _build_propagate_spec
    # action/ttl spec 构造已抽到模块级纯函数，在 compile 中调用。

    @staticmethod
    def _build_execution_order(edges: List[Dict[str, Any]]) -> List[str]:
        """按 edges 的 ``_order`` 字段生成执行顺序；缺失时按出现顺序。"""
        keyed: List[Tuple[Any, int, str]] = []
        for idx, edge in enumerate(edges):
            if not isinstance(edge, dict):
                continue
            eid = edge.get("id") or edge.get("flow_id")
            if not eid:
                continue
            order = edge.get("_order")
            if order is None and isinstance(edge.get("params"), dict):
                order = edge["params"].get("_order")
            keyed.append((order if order is not None else idx, idx, str(eid)))
        keyed.sort(key=lambda x: (x[0], x[1]))
        return [eid for _, _, eid in keyed]

    @staticmethod
    def _build_edge_ctx(
        nodes: Dict[str, Any], edges: List[Dict[str, Any]]
    ) -> Dict[str, EdgeContext]:
        """预计算每条边的端点上下文、边类型与角色。"""
        ctx_map: Dict[str, EdgeContext] = {}
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            sid = _extract_edge_endpoint(edge, ("from", "source", "startid"))
            tid = _extract_edge_endpoint(edge, ("to", "target", "endid"))
            eid = str(edge.get("id") or edge.get("flow_id") or "")
            if not eid:
                continue
            src_type = _resolve_node_type(nodes.get(sid, {}))
            tgt_type = _resolve_node_type(nodes.get(tid, {}))
            edge_type = _resolve_edge_type(src_type)
            role: Literal["in_edge", "out_edge"] = (
                "in_edge" if edge_type == "conditional" else "out_edge"
            )
            ctx_map[eid] = EdgeContext(
                eid=eid,
                sid=sid,
                tid=tid,
                edge_type=edge_type,
                src_type=src_type,
                tgt_type=tgt_type,
                is_output=(role == "out_edge"),
                role=role,
            )
        return ctx_map

    @staticmethod
    def _build_timing_spec(edge: Dict[str, Any]) -> TimingSpec:
        """从 edge params 与 timing.json 编译时机规则。"""
        params = edge.get("params", {}) if isinstance(edge, dict) else {}
        timing_cfg = _load_config("timing.json")

        starttype = int(params.get("starttype", 0) or 0)
        cxtype = int(params.get("cxtype", 0) or 0)
        starttime = int(params.get("starttime", 0) or 0)
        starttimetype = int(params.get("starttimetype", 0) or 0)
        starttimehms = int(params.get("starttimehms", 0) or 0)
        cxtime = int(params.get("cxtime", 0) or 0)
        jgtime = int(params.get("jgtime", 0) or 0)
        cxtimetype = int(params.get("cxtimetype", 0) or 0)

        cxtime_units = timing_cfg.get(
            "cxtime_units", {"0": 1, "1": 60, "2": 3600, "3": 86400}
        )
        duration_sec = cxtime * int(cxtime_units.get(str(cxtimetype), 1) or 1)

        st_rule = timing_cfg.get("starttype_rules", {}).get(str(starttype), {})
        cx_rule = timing_cfg.get("cxtype_rules", {}).get(str(cxtype), {})
        gate_expr = f"{st_rule.get('name', 'immediate')}/{cx_rule.get('name', 'forever')}"

        return TimingSpec(
            starttype=starttype,
            starttime=starttime,
            starttimetype=starttimetype,
            starttimehms=starttimehms,
            cxtype=cxtype,
            cxtime=cxtime,
            interval_sec=jgtime,
            duration_sec=duration_sec,
            gate_expr=gate_expr,
        )

    # I18：dispatch_key → evaluator_type 编译期映射（消除运行期 dispatch_key/evaluator 双路径）
    _DISPATCH_KEY_TO_EVALUATOR_TYPE: Dict[str, str] = {
        "TDX_INDICATOR": "formula",
        "TDX_CONDITION_FORMULA": "formula",
        "TDX_EXPERT_SYSTEM": "formula",
        "TDX_FINANCIAL": "scalar",
        "TDX_MARKET": "scalar",
        "TDX_SETOP": "set_operation",
    }

    # I18：scalar 路径 nset_cfg 字段白名单（从 nset_dispatch 条目提取至 evaluator_params）
    _SCALAR_NSET_CFG_KEYS = frozenset({
        "nset", "field_table", "data_method",
        "supports_derived", "supports_bar_fallback", "apply_field_map",
    })

    @staticmethod
    def _build_filter_spec(edge: Dict[str, Any], nodes: Dict[str, Any]) -> FilterSpec:
        """从 edge params、dispatch.json 编译筛选分派规则。

        I18：编译期解析 dispatch_key → evaluator_type，scalar 路径 nset_cfg
        转存至 evaluator_params，运行期不再查 dispatch.json / _SCALAR_NSET_CFG。
        """
        params = edge.get("params", {}) if isinstance(edge, dict) else {}
        dispatch_cfg = _load_config("dispatch.json")

        nset_dispatch = dispatch_cfg.get("nset_dispatch", {})

        tdx_func = params.get("tdx_func")

        # 若边 params 未携带 tdx_func，但目标节点是条件节点，则从条件节点继承公式配置
        if not isinstance(tdx_func, dict) or not tdx_func:
            tid = _extract_edge_endpoint(edge, ("to", "target", "endid"))
            tgt_node = nodes.get(tid, {})
            tgt_type = str(tgt_node.get("type", tgt_node.get("dzh_cell_type", "")))
            if tgt_type in ("3", "201", "transfer_condition", "condition_filter"):
                tdx_func = tgt_node.get("params", {}).get("tdx_func")

        if isinstance(tdx_func, dict) and tdx_func:
            nset = int(tdx_func.get("nset", 0) or 0)
            nset_key = str(nset)
            nset_entry = nset_dispatch.get(nset_key, {})
            dispatch_key = nset_entry.get("dispatch_key", "")

            # I18：编译期解析 evaluator_type（dispatch_key → 类型映射）
            # accode/ntjindexno 均空时退化为 pass_through（保留旧行为：无公式则全通过）
            # 注意：ntjindexno=0 是合法值（nset=4 字段索引 0=现价），不能用 `or ""` 提取
            accode_raw = tdx_func.get("accode")
            accode = str(accode_raw) if accode_raw is not None else ""
            ntjindexno_raw = tdx_func.get("ntjindexno")
            ntjindexno = str(ntjindexno_raw) if ntjindexno_raw is not None else ""
            if not accode and not ntjindexno:
                evaluator_type = "pass_through"
            else:
                evaluator_type = Compiler._DISPATCH_KEY_TO_EVALUATOR_TYPE.get(
                    dispatch_key, "formula"
                )

            # I18：formula_ref 按 evaluator_type 选择
            # - formula: accode（公式表达式，如 "MACD"）
            # - scalar: ntjindexno（字段索引，如 "0"、"7"）
            # - set_operation: ntjindexno（操作码，如 "0"）
            # 旧代码统一用 accode or ntjindexno，对 scalar 路径误取 accode 标签（如"现价"）
            if evaluator_type == "formula":
                formula_ref = accode or ntjindexno
            else:
                formula_ref = ntjindexno or accode

            # I18：scalar 路径 nset_cfg 转存至 evaluator_params（运行期不再查 _SCALAR_NSET_CFG）
            evaluator_params: Dict[str, Any] = {}
            if evaluator_type == "scalar":
                evaluator_params = {
                    k: v for k, v in nset_entry.items()
                    if k in Compiler._SCALAR_NSET_CFG_KEYS
                }

            formula_period = _nperiod_to_period(tdx_func.get("nperiod"))
            return FilterSpec(
                filter_type=dispatch_key or "formula",
                formula_ref=formula_ref,
                formula_period=formula_period,
                threshold=float(tdx_func.get("fsecond") or 0),
                noperate=int(tdx_func.get("noperate", 0) or 0),
                sorttype=int(tdx_func.get("sorttype", 0) or 0),
                compare_mode=str(tdx_func.get("compare_mode") or ""),
                evaluator_type=evaluator_type,
                evaluator_params=evaluator_params,
            )

        # 无 tdx_func 时：先检查边 params 是否直接携带 formula_ref
        formula_ref_direct = params.get("formula_ref", "")
        if formula_ref_direct:
            return FilterSpec(
                filter_type="formula",
                formula_ref=formula_ref_direct,
                formula_period=params.get("formula_period", ""),
                threshold=float(params.get("fsecond") or params.get("threshold") or 0),
                noperate=int(params.get("noperate", 0) or 0),
                sorttype=int(params.get("sorttype", 0) or 0),
                compare_mode=str(params.get("compare_mode") or ""),
                evaluator_type="formula",
                evaluator_params={},
            )

        # 无 formula_ref 也无 tdx_func 时，按源节点类型决定 filter_type（均退化为 pass_through）
        sid = _extract_edge_endpoint(edge, ("from", "source", "startid"))
        src_type = _resolve_node_type(nodes.get(sid, {}))
        edge_type = _resolve_edge_type(src_type)
        return FilterSpec(
            filter_type=edge_type,
            formula_ref="",
            threshold=0.0,
            noperate=0,
            sorttype=0,
            compare_mode="",
            evaluator_type="pass_through",
        )

    @staticmethod
    def _build_propagate_spec(edge: Dict[str, Any]) -> PropagateSpec:
        """从 edge params 编译状态流转规则。

        attr 位域与 field_definitions.json 的 bit_fields.flow 对齐：
          - bit 0 (0x1): delete_source → 移动
          - bit 1 (0x2): force_move → 与 bit0 组合为 0x3 时强制覆盖
          - bit 12 (0x1000): keep_source → 复制（保留源）
          - bit 13 (0x2000): clear_dest_first → 先清空目的状态
        """
        params = edge.get("params", {}) if isinstance(edge, dict) else {}
        attr = edge.get("attr", 0) if isinstance(edge, dict) else 0
        attr_from_params = params.get("attr", 0)
        attr_int = (int(attr) if attr is not None else 0) | (
            int(attr_from_params) if attr_from_params is not None else 0
        )

        tran = int(params.get("tran", 0) or 0)
        emptyps = int(params.get("emptyps", 0) or 0)

        delete_source = bool(attr_int & 0x1)
        force_move = bool(attr_int & 0x2)
        keep_source = bool(attr_int & 0x1000)
        clear_dest_first = (
            bool(params.get("clear_dest_first"))
            or (emptyps == 1)
            or bool(attr_int & 0x2000)
            or (delete_source and force_move)
        )

        is_move = (tran == 1) or (delete_source and not keep_source)

        if clear_dest_first:
            mode: Literal["copy", "move", "overwrite", "overwrite_copy"] = (
                "overwrite_copy" if not is_move else "overwrite"
            )
        elif is_move:
            mode = "move"
        else:
            mode = "copy"

        return PropagateSpec(
            mode=mode,
            clear_dest_first=clear_dest_first,
            preserve_source=not is_move or keep_source,
        )

    @classmethod
    def compile(cls, pool_config: Dict[str, Any]) -> CompiledSchedule:
        """编译 ``pool_config`` 为 ``CompiledSchedule``。"""
        nodes = _normalize_nodes(pool_config)
        edges = pool_config.get("edges", [])
        if not isinstance(edges, list):
            edges = []

        edge_ctx = cls._build_edge_ctx(nodes, edges)
        execution_order = cls._build_execution_order(edges)
        node_types = {nid: _resolve_node_type(node) for nid, node in nodes.items()}
        source_node_ids = {nid for nid, node in nodes.items() if _is_source_node(node)}

        timing_spec: Dict[str, TimingSpec] = {}
        filter_spec: Dict[str, FilterSpec] = {}
        propagate_spec: Dict[str, PropagateSpec] = {}
        action_spec: Dict[str, ActionSpec] = {}
        ttl_spec: Dict[str, TTLSpec] = {}

        for edge in edges:
            if not isinstance(edge, dict):
                continue
            eid = str(edge.get("id") or edge.get("flow_id") or "")
            if not eid:
                continue
            timing_spec[eid] = cls._build_timing_spec(edge)
            filter_spec[eid] = cls._build_filter_spec(edge, nodes)
            propagate_spec[eid] = cls._build_propagate_spec(edge)
            tid = _extract_edge_endpoint(edge, ("to", "target", "endid"))
            action_spec[eid] = _build_action_spec(tid, nodes)
            ttl_spec[eid] = _build_ttl_spec(tid, nodes)

        # I16：为无入边节点（如预填股票的状态池）注册 TTL spec，
        # 替代旧 engine.py:442-448 apply_ttl 全扫循环。
        targeted_nodes = {ec.tid for ec in edge_ctx.values()}
        node_ttl_spec: Dict[str, TTLSpec] = {}
        for nid in nodes:
            if nid in targeted_nodes:
                continue
            spec = _build_ttl_spec(nid, nodes)
            if spec.bdel == 1 and spec.check_type != "none":
                node_ttl_spec[nid] = spec

        # Task 14: 兼容性 facade 需要的深度 / 拓扑序 / 边索引
        depths = {nid: 0 for nid in nodes}
        changed = True
        while changed:
            changed = False
            for ec in edge_ctx.values():
                if ec.sid in depths and ec.tid in depths and depths[ec.tid] < depths[ec.sid] + 1:
                    depths[ec.tid] = depths[ec.sid] + 1
                    changed = True
        topo_order = sorted(nodes.keys(), key=lambda n: depths.get(n, 0))
        edge_index = {eid: edge for edge in edges if isinstance(edge, dict) and (edge.get("id") or edge.get("flow_id"))}

        return CompiledSchedule(
            execution_order=execution_order,
            edge_ctx=edge_ctx,
            edge_timing_spec=timing_spec,
            edge_filter_spec=filter_spec,
            edge_propagate_spec=propagate_spec,
            edge_action_spec=action_spec,
            edge_ttl_spec=ttl_spec,
            node_types=node_types,
            source_node_ids=source_node_ids,
            node_ttl_spec=node_ttl_spec,
            topo_order=topo_order,
            depths=depths,
            nodes=nodes,
            edge_index=edge_index,
        )


__all__ = [
    "CompiledSchedule",
    "Compiler",
    "EdgeContext",
    "TimingSpec",
    "FilterSpec",
    "PropagateSpec",
    "ActionSpec",
    "TTLSpec",
    "build_timed_event_specs",
]


# ---------------------------------------------------------------------------
# 统一时间驱动：所有到时事件统一为 TimedEventSpec
# ---------------------------------------------------------------------------


def _make_edge_at_fn(state: Any, eid: str, timing: "TimingSpec",
                     edge_executor: Any) -> Callable[[], float]:
    """构造边触发的 at_fn：委托 ``edge_executor._gate`` 判定，返回 <= now 表示到期。

    at_fn 语义：
        - ``_gate`` 通过 → 返回 0.0（已到期，应触发 action）
        - ``_gate`` 拒绝 → 返回 now + 1.0（未到期，下一 tick 再评估）
    """

    def at_fn() -> float:
        try:
            if edge_executor._gate(timing, eid):
                return 0.0
        except Exception:
            pass
        return _state_now(state) + 1.0

    return at_fn


def _make_edge_action(bus: Any, eid: str, sid: str, tid: str, edge_executor: Any, state: Any, schedule: Any, source_ids: set) -> Callable[[Any], None]:
    """构造边触发的 action：发布 Executed 事件。

    订阅者（EdgeExecutor）检查 dirty 后执行 filter→propagate→callback。
    时间触发与执行分离——at_fn 判定时间，action 发布事件，订阅者执行逻辑。
    """

    def action(params: Any) -> None:
        ec = schedule.edge_ctx.get(eid)
        if ec is None:
            return
        src = ec.sid
        dirty = state.dirty
        trigger = dirty.nodes.get(src) or (dirty.data and src in source_ids)
        if trigger:
            edge_executor.run(eid)

    return action


def _make_ttl_interval_at_fn(tracker: Any) -> Callable[[], float]:
    """构造 TTL interval 类型的 at_fn：委托 TtlTracker.next_expire_at()。

    堆空返回 inf（永不到期），与边触发 at_fn 共用 at_fn() <= now 语义。
    """

    def at_fn() -> float:
        return tracker.next_expire_at()

    return at_fn


def _make_ttl_interval_action(state: Any, tracker: Any, bus: Any) -> Callable[[Any], None]:
    """构造 TTL interval 类型的 action：pop_expired → 发布 DomainEvent(TIMEOUT)。

    到时触发与执行分离——TtlTracker 管理到期时间，action 发布事件，
    订阅者（engine）执行批量删除。Tracker 不发布事件。
    """

    def action(params: Any) -> None:
        try:
            from .time_util import time_at
        except ImportError:
            from time_util import time_at
        try:
            from .event_bus import DomainEvent, Signal
        except ImportError:
            from event_bus import DomainEvent, Signal

        now_val = time_at(state=state)
        expired = tracker.pop_expired(now_val)
        if not expired:
            return
        tgt = tracker.tgt
        eid = tracker.eid
        codes = [e.code for e in expired]
        try:
            from .edge_executor import _stock_code
        except ImportError:
            from edge_executor import _stock_code

        expired_prices: Dict[str, float] = {}
        for s in state.get_node_stocks(tgt):
            if isinstance(s, dict) and _stock_code(s) in set(codes):
                tr = s.get("_tracker")
                if isinstance(tr, dict):
                    expired_prices[_stock_code(s)] = float(
                        tr.get("current_price", tr.get("entry_price", 0))
                    )

        kept = [s for s in state.get_node_stocks(tgt) if _stock_code(s) not in set(codes)]
        if len(kept) < len(state.get_node_stocks(tgt)):
            state.set_node_stocks(tgt, kept)
            state.mark_node_dirty(tgt)
            import logging
            logging.getLogger(__name__).info("TTL expire: removed %s from %s", codes, tgt)

        for entry in expired:
            price = expired_prices.get(entry.code, 0)
            if entry.code in expired_prices:
                bus.publish(Signal(
                    signal_type="SELL",
                    code=entry.code,
                    pool_id=tgt,
                    price=price,
                    quantity=0,
                ))
            bus.publish(DomainEvent(
                event_type="TIMEOUT",
                code=entry.code,
                pool_id=tgt,
                details={
                    "reason": "TTL_EXPIRED",
                    "flow_id": eid,
                    "ttl_sec": entry.ttl_sec,
                    "timestamp": entry.expire_at,
                },
            ))

    return action


def _make_ttl_endtime_at_fn(state: Any, endtime_sec: int) -> Callable[[], float]:
    """构造 TTL endtime 类型的 at_fn：当前时刻 >= endtime_sec 时返回 0.0（到期）。"""

    def at_fn() -> float:
        try:
            from .time_util import time_at, is_offset_of_day
            from .edge_executor import _current_seconds_of_day
        except ImportError:
            from time_util import time_at, is_offset_of_day
            from edge_executor import _current_seconds_of_day
        now = time_at(state=state)
        now_sec = _current_seconds_of_day(now)
        if now_sec >= endtime_sec:
            return 0.0
        return now + 1.0

    return at_fn


def _make_ttl_endtime_action(state: Any, ttl_spec: "TTLSpec", tgt: str, bus: Any, eid: str) -> Callable[[Any], None]:
    """构造 TTL endtime 类型的 action：扫描 hold 超时股票 → 发布 DomainEvent(TIMEOUT)。

    endtime 模式在时钟到达 endtime_sec 时触发，检查 hold_for_ttl 过滤超时股票。
    这不是轮询——是时钟驱动的单次/周期触发。
    """

    def action(params: Any) -> None:
        try:
            from .edge_executor import _stock_code, _stock_entry_time, _now_ts, _current_seconds_of_day
            from .event_bus import DomainEvent
            from .time_util import time_at
        except ImportError:
            from edge_executor import _stock_code, _stock_entry_time, _now_ts, _current_seconds_of_day
            from event_bus import DomainEvent
            from time_util import time_at

        now_unix = _now_ts(state)
        now_sec_of_day = _current_seconds_of_day(time_at(state=state))
        if now_sec_of_day < ttl_spec.endtime_sec:
            return
        removed_codes: List[str] = []
        stocks = state.get_node_stocks(tgt)
        kept: List[Any] = []
        for stock in stocks:
            if ttl_spec.hold_for_ttl > 0:
                entry_ts = _stock_entry_time(stock)
                if entry_ts is not None and (now_unix - entry_ts) >= ttl_spec.hold_for_ttl:
                    removed_codes.append(_stock_code(stock))
                    continue
                kept.append(stock)
            else:
                removed_codes.append(_stock_code(stock))
        if removed_codes:
            state.set_node_stocks(tgt, kept)
            state.mark_node_dirty(tgt)
            import logging
            logging.getLogger(__name__).info("TTL endtime expire: removed %s from %s", removed_codes, tgt)
            for code in removed_codes:
                bus.publish(DomainEvent(
                    event_type="TIMEOUT",
                    code=code,
                    pool_id=tgt,
                    details={
                        "reason": "TTL_ENDTIME",
                        "flow_id": eid,
                        "ttl_sec": ttl_spec.hold_for_ttl,
                        "timestamp": now_unix,
                    },
                ))

    return action


def _state_now(state: Any) -> float:
    """从 state 读取当前时间戳（三模式统一入口）。"""
    try:
        from .time_util import time_at
    except ImportError:
        from time_util import time_at
    return time_at(state=state)


def build_timed_event_specs(
    schedule: "CompiledSchedule",
    state: Any,
    engine: Any,
    edge_executor: Any,
    event_driver: Any = None,
    bus: Any = None,
) -> List["TimedEventSpec"]:
    """编译期统一构造所有 TimedEventSpec——边触发和 TTL 共用。

    所有到时事件统一为 TimedEventSpec，区别仅在 params 不同、引发的下个事件不同：
      - 边触发：action 发布 Executed → 订阅者执行 filter→propagate→callback
      - TTL interval：at_fn 委托 TtlTracker.next_expire_at，action 发布 DomainEvent(TIMEOUT)
      - TTL endtime：at_fn 判定时钟时间，action 发布 DomainEvent(TIMEOUT)

    Returns:
        List[TimedEventSpec]：按 execution_order 排列的到时事件规格列表
    """
    try:
        from .time_util import TimedEventSpec, TtlTracker
    except ImportError:
        from time_util import TimedEventSpec, TtlTracker

    specs: list[TimedEventSpec] = []
    source_ids = schedule.source_node_ids
    if bus is None:
        bus = getattr(edge_executor, "bus", None)

    for eid in schedule.execution_order:
        ec = schedule.edge_ctx.get(eid)
        if ec is None:
            continue

        # 边触发 TimedEventSpec
        timing = schedule.edge_timing_spec.get(eid)
        if timing is not None:
            edge_at_fn = _make_edge_at_fn(state, eid, timing, edge_executor)
            edge_action = _make_edge_action(bus, eid, ec.sid, ec.tid, edge_executor, state, schedule, source_ids)
            specs.append(TimedEventSpec(
                at_fn=edge_at_fn,
                interval=None,
                end_fn=None,
                action=edge_action,
                params={"kind": "edge", "eid": eid, "sid": ec.sid, "tid": ec.tid},
            ))

        # TTL TimedEventSpec（按 check_type 分派）
        ttl = schedule.edge_ttl_spec.get(eid)
        if ttl is not None and ttl.bdel == 1 and ttl.check_type != "none" and (
            ttl.ttl_sec > 0 or ttl.endtime_sec > 0
        ):
            if ttl.check_type == "interval" and ttl.ttl_sec > 0:
                tracker = TtlTracker(tgt=ec.tid, eid=eid)
                if event_driver is not None:
                    event_driver.add_ttl_tracker(eid, tracker)
                ttl_at_fn = _make_ttl_interval_at_fn(tracker)
                ttl_action = _make_ttl_interval_action(state, tracker, bus)
                specs.append(TimedEventSpec(
                    at_fn=ttl_at_fn,
                    interval=None,
                    end_fn=None,
                    action=ttl_action,
                    params={"kind": "ttl", "eid": eid, "tgt": ec.tid, "check_type": "interval"},
                ))
            elif ttl.check_type == "endtime" and ttl.endtime_sec > 0:
                ttl_at_fn = _make_ttl_endtime_at_fn(state, ttl.endtime_sec)
                ttl_action = _make_ttl_endtime_action(state, ttl, ec.tid, bus, eid)
                specs.append(TimedEventSpec(
                    at_fn=ttl_at_fn,
                    interval=None,
                    end_fn=None,
                    action=ttl_action,
                    params={"kind": "ttl", "eid": eid, "tgt": ec.tid, "check_type": "endtime"},
                ))

    # 无入边节点的 TTL spec（如预填股票的状态池）
    for nid, ttl in schedule.node_ttl_spec.items():
        if ttl.bdel == 1 and ttl.check_type != "none" and (
            ttl.ttl_sec > 0 or ttl.endtime_sec > 0
        ):
            if ttl.check_type == "interval" and ttl.ttl_sec > 0:
                tracker = TtlTracker(tgt=nid, eid=f"node_ttl:{nid}")
                if event_driver is not None:
                    event_driver.add_ttl_tracker(f"node_ttl:{nid}", tracker)
                ttl_at_fn = _make_ttl_interval_at_fn(tracker)
                ttl_action = _make_ttl_interval_action(state, tracker, bus)
                specs.append(TimedEventSpec(
                    at_fn=ttl_at_fn,
                    interval=None,
                    end_fn=None,
                    action=ttl_action,
                    params={"kind": "ttl", "eid": f"node_ttl:{nid}", "tgt": nid, "check_type": "interval"},
                ))
            elif ttl.check_type == "endtime" and ttl.endtime_sec > 0:
                ttl_at_fn = _make_ttl_endtime_at_fn(state, ttl.endtime_sec)
                ttl_action = _make_ttl_endtime_action(state, ttl, nid, bus, f"node_ttl:{nid}")
                specs.append(TimedEventSpec(
                    at_fn=ttl_at_fn,
                    interval=None,
                    end_fn=None,
                    action=ttl_action,
                    params={"kind": "ttl", "eid": f"node_ttl:{nid}", "tgt": nid, "check_type": "endtime"},
                ))

    return specs
