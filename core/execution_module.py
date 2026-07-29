"""Execution 模块：编译 + 核心循环 + 边执行 + 时序驱动 + 边状态。

按 ``unify-stockpool-oop-event-driven`` spec Task 8 实现。
``ExecutionModule`` 持有原 4 个组件实例（Compiler / PoolEngine / EdgeExecutor /
EventDriver），外部仅通过 ``EventBus`` 与之交互。

订阅 StockFiltered / DataChanged / TimeAdvanced / ConfigChanged / PoolLoaded 事件，
执行 gate→filter→propagate→callback→ttl 流水线，
发布 EdgeFired / TransferExecuted / TTLExpired / Signal 事件。

SubTask 27.1：``core/ttl_helper.py`` 的 ``_do_ttl_check`` 函数与 ``TTLHelper`` 类
已迁移至本模块（``ttl_helper.py`` 已删除）。

SubTask 27.4：将原 4 个 Execution 模块相关源文件高内聚合并到本文件：
  - ``core/compiler.py``     → Compiler / CompiledSchedule / Pydantic spec 模型 /
    节点边解析辅助 / ``build_timed_event_specs``
  - ``core/edge_executor.py`` → EdgeExecutor / TickTable / gate/filter/propagate
    表驱动分派辅助
  - ``core/time_util.py``     → time_at / EventDriver / TimedEventSpec
    等时序驱动基础设施
  - ``core/edge_state.py``    → EdgeState / EdgeStateMixin 边级运行时表
上述 4 个源文件已删除，``core/execution_module.py`` 成为 Execution 模块的唯一入口。
"""
from __future__ import annotations

import copy
import heapq
import json
import logging
import operator
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Literal, Optional, Protocol, Set, Tuple, Type, TYPE_CHECKING

from pydantic import BaseModel, Field

from .event_bus import (
    _event_handler,
    ConfigChanged,
    DataChanged,
    DomainEvent,
    EdgeFired,
    EventBus,
    Executed,
    FormulaEvaluated,
    ModeChanged,
    PoolLoaded,
    Signal,
    StockFiltered,
    TickDue,
    TimeAdvanced,
    TransferExecuted,
    TTLDue,
    TTLExpired,
)
# 依赖注入（Protocol）：标量评估器 / 评估上下文工厂 / 实时上下文工厂 / 公式引擎协议
# 由 engine.py 组装层经 EdgeExecutor 构造函数注入，满足模块零引用约束。
from .domain import (
    _stock_code,
    _hms_to_seconds,
    time_at,
    _safe_timestamp,
    is_offset_of_day,
    anchor_to_today,
    _lookup_builtin_formula_info,
    time_now_unix,
    # TDX nperiod → period 映射（已迁移至 domain 白名单）
    _nperiod_to_period,
    # 交集条件评估器（已迁移至 domain 白名单）
    evaluate_intersection,
    # EdgeState 边级运行时表（已迁移至 domain 白名单）
    EdgeState,
    EdgeStateMixin,
    # TimedEventSpec 已下沉至 domain（纯数据结构），经此模块级 import 引入并 re-export
    # （见 __all__），避免 core/domain 反向函数级懒加载本模块（模块零引用约束）。
    TimedEventSpec,
)
from .schemas import StepResult
from .table_engine import get_global_config_store, load_config_table
# Task 3：向量 mode 分派（rank/inflection/compare）下沉至 screening_module，与标量版共用
# _NOPERATE_RULES + _MODE_HANDLERS_SERIES 真值源，消除本模块 mode 硬编码分支。
from .screening_module import _apply_noperate_mode_series, _resolve_series_lookback

if TYPE_CHECKING:
    from .runtime_mode_module import PoolState

logger = logging.getLogger(__name__)


# ===========================================================================
# 依赖注入协议（避免 execution_module 跨模块引用公式/选股模块）
# ===========================================================================
# 组装层（core/engine.py）持有具体实现并经 EdgeExecutor 构造函数注入；
# 本模块仅依赖以下 Protocol/容器结构类型，满足模块零引用约束。


class FormulaEngineProtocol(Protocol):
    """公式引擎协议：本模块仅依赖此结构类型，不直接 import 公式模块。"""

    def eval_series(self, spec: Any, codes: List[str], ctx: Any, lookback: int) -> Dict[str, Any]:
        ...

    def eval_scalar(
        self,
        spec: Any,
        codes: List[str],
        ctx: Any,
        evaluator_fn: Callable[[List[str], Any], Dict[str, Any]],
    ) -> Dict[str, Any]:
        ...


@dataclass
class _FilterEvalDeps:
    """筛选评估依赖注入容器。

    由 engine.py 组装层注入具体实现：
      - scalar_nset_fn: 标量 nset 评估器（nset=3/4 财务/行情选股）
      - eval_ctx_factory: 评估上下文工厂（标量路径构造求值上下文）
      - live_ctx_fn: 实时上下文工厂（公式路径构造实盘上下文）
      - bus: EventBus 实例（spec.md L128：公式求值异常时发布携带 error
        字段的 FormulaEvaluated 事件供下游诊断）
    """

    scalar_nset_fn: Optional[Callable] = None
    eval_ctx_factory: Optional[Callable] = None
    live_ctx_fn: Optional[Callable] = None
    bus: Any = None


# ---------------------------------------------------------------------------
# 配置表加载：已统一到 ConfigStore.get_table(name)（Task 9.9）
# 模块级 _load_config 帮助函数已删除，调用方通过 get_global_config_store().get_table(name) 访问
# ---------------------------------------------------------------------------

_CONFIG_DIR = Path(__file__).parent.parent / "config"


# ===========================================================================
# 时序工具（原 core/time_util.py）
# ===========================================================================
# 三模式时间架构（state.time_source["driver_type"]）：
#   - wall_clock：实盘模式，由 run_tick 写入 current_ts（= _now().timestamp()）
#   - sequence：回放模式，由 ReplayRunner 写入 K 线时间戳
#   - virtual：仿真模式，由 Simulator 写入虚拟时钟
#
# 统一时间驱动（G1 heapq 优先队列）：所有到时事件统一为 TimedEventSpec：
#   - 注册到 EventDriver 单一 heapq，元素 [fire_time, seq, spec]
#   - fire_due(now) 弹出堆顶 fire_time <= now 的事件，调 action(params) 发布事件
#   - 立即注册下次：next = fire_time + interval（不是 now + interval）
#   - interval=None 表示一次性（TTL），interval>0 表示周期触发（边触发）
#   - 边触发：action 发布 EdgeFired → 订阅者执行 filter→propagate→callback
#   - TTL到期：action 发布 DomainEvent(TIMEOUT) → 订阅者执行批量删除


# ---------------------------------------------------------------------------
# TimedEventSpec（统一到时事件规格）— 已下沉至 core/domain.py（纯数据结构）
# 经下方 ``from .domain import TimedEventSpec`` 引入并 re-export（见 __all__），
# 避免 core/domain 反向函数级懒加载本模块（模块零引用约束）。
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# EventDriver — 统一时间驱动器（G1 单一 heapq 优先队列）
# ---------------------------------------------------------------------------


class EventDriver:
    """统一时间驱动器：所有 TimedEventSpec 注册到单一 heapq 优先队列。

    fire_due(now) 弹出堆顶到时事件，发布事件 + 立即注册下次（fire_time + interval）。
    与模块计算无关——触发即注册下次，结束。

    heapq 元素格式：``(fire_time, seq, spec)``，seq 用于同时间稳定排序。
    同 fire_time 时，kind="tick" 的 spec 使用更小的 seq，确保 tick 数据事件
    （TickReceived → DataChanged → BarComposed）先于边触发事件（EdgeFired）执行。
    """

    # tick 定时器 seq 起始值：足够小的负数，保证同 fire_time 时始终排在边触发/TTL 之前
    _TICK_SEQ_BASE = -10**9

    def __init__(self, state: Any = None, bus: Any = None) -> None:
        self._state = state
        self._bus = bus
        self._heap: List[tuple[float, int, TimedEventSpec]] = []
        self._seq = 0
        self._tick_seq = self._TICK_SEQ_BASE

    def _next_seq(self, spec: TimedEventSpec) -> int:
        """按 spec 类型分配 seq：tick 优先，其余按全局顺序。"""
        kind = spec.params.get("kind") if isinstance(spec.params, dict) else None
        if kind == "tick":
            seq = self._tick_seq
            self._tick_seq += 1
            return seq
        seq = self._seq
        self._seq += 1
        return seq

    def add_spec(self, spec: TimedEventSpec, first_fire_time: float) -> None:
        """注册到时事件规格到 heapq（边触发和 TTL 统一入口）。"""
        heapq.heappush(self._heap, (first_fire_time, self._next_seq(spec), spec))

    def _register_next(self, fire_time: float, spec: TimedEventSpec) -> None:
        """按规则为周期规格注册下次触发时间。"""
        if spec.interval is not None and spec.interval > 0:
            next_time = fire_time + spec.interval
            if spec.end_fn is None or next_time <= spec.end_fn():
                heapq.heappush(self._heap, (next_time, self._next_seq(spec), spec))

    def fire_due(self, now: float) -> None:
        """弹出堆顶到时事件，发布事件 + 立即注册下次。

        next = fire_time + interval（不是 now + interval），保证固定间隔。
        interval=None 或 <=0 表示一次性，不注册下次。
        end_fn 判定是否继续注册（None=永久）。

        为保证每个 tick 循环内"数据先于计算"，本次 fire_due 调用内到期的
        kind="tick" 规格（生成 TickReceived / DataChanged / BarComposed）先于
        kind="edge" / "ttl" 规格（EdgeFired / TTLDue）执行。同时保留"追回"
        行为：周期规格执行后若 next <= now 会继续入队并在本次 fire_due 内处理。
        """
        while True:
            # 第一阶段：弹出所有到期项；tick 立即执行，非 tick 先缓冲
            buffered: List[tuple[float, int, TimedEventSpec]] = []
            while self._heap and self._heap[0][0] <= now:
                fire_time, seq, spec = heapq.heappop(self._heap)
                kind = spec.params.get("kind") if isinstance(spec.params, dict) else None
                if kind == "tick":
                    try:
                        spec.action(spec.params, fire_time)
                    except Exception:
                        logger.warning("TimedEventSpec action 异常", exc_info=True)
                    self._register_next(fire_time, spec)
                else:
                    buffered.append((fire_time, seq, spec))
            if not buffered:
                break
            # 第二阶段：执行缓冲的非 tick 规格，可能产生新的到期项
            for fire_time, _seq, spec in buffered:
                try:
                    spec.action(spec.params, fire_time)
                except Exception:
                    logger.warning("TimedEventSpec action 异常", exc_info=True)
                self._register_next(fire_time, spec)
            # 循环：新入队的周期项若仍 <= now 需要继续处理（追回）


# ===========================================================================
# 边级运行时表（原 core/edge_state.py）
# ===========================================================================
# 按 ``ARCHITECTURE_FINAL.md`` 第 3.2.2 节实现，将原本散落在
# ``PoolState`` 中的边级运行时表收敛为 ``EdgeState``：
#
# - ``exec_ctx``: 边执行上下文（count / first_fire / last_fire）
# - ``formula_results``: 公式级结果缓存（亦称 ``filter_cache``）
# - ``filter_inputs``: 每条边最近一次过滤的输入股票指纹
#
# I94：删除 ``edge_fired`` 字典与 ``exec_ctx[eid]["fired"]`` 字段——两者均
# 零生产读取。``edge_fired`` 被 engine.py 写入但 L322
# 读局部变量；``exec_ctx.fired`` 被 set_exec_ctx_fired 写入但无消费方。
# edge_fired 非非 exec_ctx.fired 的视图（语义不同：前者为当前 tick 时间
# 门控结果，后者为边是否曾执行过），原 L7 注释错误。
#
# EdgeState / EdgeStateMixin 已迁移至 core/domain（白名单模块），
# 此处通过顶部 from .domain import 导入，并在 __all__ 中 re-export 保持向后兼容。





# ===========================================================================
# 编译期静态调度表生成器（原 core/compiler.py）
# ===========================================================================
# 按 ``execute-architecture-migration`` 规格 Task 3 实现：
# ``Compiler.compile(pool_config)`` 一次性产出 ``CompiledSchedule``，
# 运行期只读，不再重复解析边端点、filter 类型、边类型、处理计划等。


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
    """筛选分派规则（读 dispatch.json + tdx_func 16参数）。

    I18：编译期解析 dispatch_key → evaluator_type，运行期按 evaluator_type dict 分派。
    消除 dispatch_key + evaluator 双路径；evaluator_params 承载 scalar 路径 nset_cfg。
    I53：evaluator_type 为唯一运行期分派键；filter_type 降级为元数据（审计追溯），
    不再参与控制流，公式引擎.eval 不再按 filter_type 分派。

    TDX func 节点16个参数完整支持：
    - nset: 条件类型路由(0技术指标,1条件选股,2专家系统,3最新财务,4实时行情,5逻辑运算)
    - accode: 指标代码/公式名
    - ntjindexno: 系统指标编号/财务/行情字段索引/集合操作类型(0并/1差/2交)
    - nperiod: 分析周期(0日线,1周线,2月线,3多分钟,45分钟线等)
    - nfirst: 第一条指标线索引
    - cfirst: 第一条指标线名称
    - noperate: 操作符(0-9)
    - nsecond: 第二条指标线索引
    - csecond: 第二条指标线名称
    - fsecond: 数值阈值(threshold)
    - nbeginday: 时间窗口起始日
    - nendday: 时间窗口结束日
    - bnost: 不包含ST股票
    - bnotp: 不包含新股/次新股
    - bnotq: 不包含停牌股票
    - nperiodnum: 回溯K线数量(排名窗口)
    """

    filter_type: str = ""
    formula_ref: str = ""
    formula_period: str = ""
    threshold: float = 0.0
    noperate: int = 0
    sorttype: int = 0
    compare_mode: str = ""
    evaluator_type: Literal["pass_through", "formula", "scalar", "set_operation", "intersection"] = "pass_through"
    evaluator_params: Dict[str, Any] = Field(default_factory=dict)

    nset: int = 0
    accode: str = ""
    ntjindexno: int = 0
    nperiod: int = 0
    nfirst: int = 0
    cfirst: str = ""
    nsecond: int = -1
    csecond: str = ""
    nbeginday: int = 0
    nendday: int = 0
    bnost: bool = False
    bnotp: bool = False
    bnotq: bool = False
    nperiodnum: int = 0
    formula_args: Dict[str, Any] = Field(default_factory=dict)

    # TDX 转移节点指标面板参数（func/indi/indiparam）
    func: Dict[str, Any] = Field(default_factory=dict)
    indi: str = ""
    indiparam: List[Dict[str, Any]] = Field(default_factory=list)


class PropagateSpec(BaseModel):
    """状态流转规则（读 edge params）。"""

    mode: Literal["copy", "move", "overwrite", "overwrite_copy"] = "copy"
    clear_dest_first: bool = False
    preserve_source: bool = True


class ActionSpec(BaseModel):
    """目标节点副作用动作（读 action_table.json + 目标节点 tdx_psatt）。"""

    target_pool_actions: List[str] = Field(default_factory=list)
    exit_pool_actions: List[str] = Field(default_factory=list)
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
    # G6：运行时事件无序，不保留 execution_order/topo_order/depths 等运行时拓扑排序属性。
    # 边顺序号保留在 edge_index[eid].params._order，供条件节点集合运算排序入边使用。
    nodes: Dict[str, Any] = Field(default_factory=dict)
    edge_index: Dict[str, Any] = Field(default_factory=dict)
    steps: List[Any] = Field(default_factory=list)  # List[StepSpec]，编译期从 edge_strategies.json 读取


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

    dzh_cfg = get_global_config_store().get_table("dzh_type_map") if get_global_config_store() else {}
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
    cfg = get_global_config_store().get_table("modules") if get_global_config_store() else {}
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
    sem_cfg = get_global_config_store().get_table("edge_semantics") if get_global_config_store() else {}
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
    sem_cfg = get_global_config_store().get_table("edge_semantics") if get_global_config_store() else {}
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
    action_table = get_global_config_store().get_table("action_table") if get_global_config_store() else {}
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

    # params.actions / params.exit_actions 直接声明的动作（如 auto_buy / auto_sell）
    if isinstance(tgt_params, dict):
        for a in tgt_params.get("actions", []):
            if a and a not in actions:
                actions.append(a)

    exit_actions: List[str] = []
    if isinstance(tgt_params, dict):
        for a in tgt_params.get("exit_actions", []):
            if a:
                exit_actions.append(a)

    return ActionSpec(target_pool_actions=actions, exit_pool_actions=exit_actions, callbacks=callbacks)


def _decode_endtime(endtime: int, psatt_cfg: Dict[str, Any], defaults: Dict[str, Any]) -> Optional[Tuple[int, int, int]]:
    """解码 DZH endtime 编码 (3600*HH - 900*MM + SS) 为 (HH, MM, SS)。

    I16：从 ttl_helper.py 迁移到 compiler.py（编译期执行）。
    SubTask 27.4：``_hms_to_seconds`` 已随 ``time_util.py`` 一并迁移至本模块，
    原动态 import 链移除，直接使用本地 ``_hms_to_seconds``。
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
    psatt_cfg = get_global_config_store().get_table("tdx_psatt") if get_global_config_store() else {}
    defaults = get_global_config_store().get_table("defaults") if get_global_config_store() else {}
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


# TDX 转移节点指标面板参数解析辅助（indi / indiparam / func）
# 加载 ntjindexno → formula_name/args 映射表，用于把前端指标选择解析为 builtin formula
_TDX_INDICATOR_FORMULA_MAP: Dict[int, Dict[str, Any]] = {}
try:
    _tdx_indi_map_cfg = get_global_config_store().get_table("tdx_indicator_formula_map") if get_global_config_store() else {}
    for rec in _tdx_indi_map_cfg.get("records", []):
        ntj = rec.get("ntjindexno")
        if ntj is not None:
            _TDX_INDICATOR_FORMULA_MAP[int(ntj)] = rec
except Exception:
    _TDX_INDICATOR_FORMULA_MAP = {}


def _parse_indiparam(indiparam: Any) -> Dict[str, Any]:
    """把 indiparam 列表/字典解析为 {参数名: 值}。"""
    args: Dict[str, Any] = {}
    if isinstance(indiparam, dict):
        for k, v in indiparam.items():
            args[str(k)] = v
    elif isinstance(indiparam, list):
        for item in indiparam:
            if isinstance(item, dict):
                name = item.get("name") or item.get("n")
                value = item.get("value") if "value" in item else item.get("v")
                if name is not None:
                    args[str(name)] = value
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                args[str(item[0])] = item[1]
    return args


def _resolve_indicator_formula(
    indi: str,
    indiparam: Any,
    func: Optional[Dict[str, Any]] = None,
    ntjindexno: Optional[int] = None,
) -> Tuple[str, str, Dict[str, Any]]:
    """根据指标面板参数解析出 formula_ref / formula_period / formula_args。

    优先级：
      1. func.accode / indi 直接对应 builtin formula 名称（如 KDJ/MACD）
      2. ntjindexno 查 tdx_indicator_formula_map 映射
      3. 兜底返回 indi 作为 formula_ref
    """
    func = func or {}
    accode = str(func.get("accode", "")).strip() or indi.strip()
    period = _nperiod_to_period(func.get("nperiod")) if func.get("nperiod") is not None else ""
    args = _parse_indiparam(indiparam)

    # 若 func 内带了 args，也合并进来（indi 面板参数优先）
    func_args = func.get("args") or func.get("formula_args") or {}
    if isinstance(func_args, dict):
        merged_args = dict(func_args)
        merged_args.update(args)
        args = merged_args

    # 尝试用 ntjindexno 查映射表
    if ntjindexno is not None and int(ntjindexno) in _TDX_INDICATOR_FORMULA_MAP:
        rec = _TDX_INDICATOR_FORMULA_MAP[int(ntjindexno)]
        formula_name = str(rec.get("formula_name", accode or indi))
        # 映射表里的 arg 优先级高于面板参数
        map_arg = rec.get("formula_arg", "")
        if map_arg:
            for idx, v in enumerate(str(map_arg).split(",")):
                v = v.strip()
                if v:
                    args.setdefault(f"P{idx + 1}", v)
        return formula_name, period or "1d", args

    if accode:
        return accode, period or "1d", args

    return indi, period or "1d", args


def _build_tdx_func_from_panel(params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """从通达信转移节点指标面板参数（func / indi / indiparam）合成 tdx_func。

    面板参数常见形态：
      - params["func"] = {"accode": "KDJ", "nperiod": 4, "noperate": 3, ...}
      - params["indi"] = "KDJ"
      - params["indiparam"] = [{"name": "N", "value": 9}, ...]

    合成规则：
      - func 字典作为基础，缺失字段由 indi / indiparam 推导
      - indi 作为 accode 回退
      - indiparam 解析为 formula_args
      - 若三者皆空返回 None
    """
    func = params.get("func") if isinstance(params.get("func"), dict) else {}
    indi = str(params.get("indi", "")).strip()
    indiparam = params.get("indiparam")
    if not func and not indi and not indiparam:
        return None

    tdx_func: Dict[str, Any] = dict(func)
    if indi and not tdx_func.get("accode"):
        tdx_func["accode"] = indi
    if indiparam is not None and "formula_args" not in tdx_func:
        tdx_func["formula_args"] = _parse_indiparam(indiparam)
    # 确保 nset 默认技术指标
    if "nset" not in tdx_func:
        tdx_func["nset"] = 0
    return tdx_func


# ===========================================================================
# 变更 F：build_spec 提取器统一 — 4 个 _build_xxx_spec 共享「提取 edge.params → 查 config → 构造 Spec」骨架
# ===========================================================================

def _extract_edge_params(edge: Dict[str, Any]) -> Dict[str, Any]:
    """一次性提取 ``edge.params``（4 个 ``_build_xxx_spec`` 共享的字段提取骨架）。"""
    return edge.get("params", {}) if isinstance(edge, dict) else {}


def _to_bool(v) -> bool:
    """TDX func 布尔字段归一化（bool 是 int 子类，int/float 分支已覆盖 bool 取值）。"""
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(v, (int, float)):
        return v != 0
    return False


def _cast_int(v) -> int:
    return int(v or 0)


def _cast_str(v) -> str:
    return str(v or "").strip()


# TimingSpec 直接字段映射；duration_sec/gate_expr 由 timing.json 派生
_TIMING_SPEC_FIELDS: Dict[str, Callable[[Any], int]] = {
    f: _cast_int for f in ("starttype", "starttime", "starttimetype", "starttimehms", "cxtype", "cxtime", "cxtimetype")
}

# PropagateSpec 直接字段映射（tran/emptyps），attr 位域与 mode 由派生计算
_PROPAGATE_SPEC_FIELDS: Dict[str, Callable[[Any], int]] = {f: _cast_int for f in ("tran", "emptyps")}

# PropagateSpec mode 派发表：(clear_dest_first, is_move) → mode（消除原 if/elif/else 三分支）
_PROPAGATE_MODE_TABLE: Dict[Tuple[bool, bool], str] = {
    (True, False): "overwrite_copy", (True, True): "overwrite",
    (False, True): "move", (False, False): "copy",
}

# TDX func 参数字段映射（供 _build_filter_spec tdx_func 分支表驱动提取）
_TDX_FUNC_SPEC_FIELDS: Dict[str, Callable[[Any], Any]] = {
    "nperiod": _cast_int, "nfirst": _cast_int, "cfirst": _cast_str,
    "nsecond": lambda v: int(v) if v is not None else -1, "csecond": _cast_str,
    "nbeginday": _cast_int, "nendday": _cast_int,
    "bnost": _to_bool, "bnotp": _to_bool, "bnotq": _to_bool,
    "nperiodnum": _cast_int, "formula_args": lambda v: v or {},
}


class Compiler:
    """股票池配置编译器：输出 ``CompiledSchedule`` 供运行期只读使用。"""

    # 类方法数 = 5：compile / _build_edge_ctx /
    # _build_timing_spec / _build_filter_spec / _build_propagate_spec
    # action/ttl spec 构造已抽到模块级纯函数，在 compile 中调用。
    # G6：已删除 _build_execution_order（运行时事件无序，边顺序号 _order 保留于 edge_ctx 供交集/差集运算）。

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
            # spec.md L130-133: 自环边（source == target）禁止，抛明确异常而非触发
            # depths 计算的 while changed 无限循环（L1213-1220）。
            if sid == tid:
                raise ValueError(
                    f"自环边检测到: eid={eid}, sid=tid={sid}；"
                    f"CompiledSchedule 不允许自环边（spec.md L130-133）"
                )
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
        params = _extract_edge_params(edge)
        timing_cfg = get_global_config_store().get_table("timing") if get_global_config_store() else {}
        tfields = {name: cast(params.get(name)) for name, cast in _TIMING_SPEC_FIELDS.items()}
        jgtime = int(params.get("jgtime", 0) or 0) or int(params.get("time_gate_interval", 0) or 0)
        cxtime_units = timing_cfg.get("cxtime_units", {"0": 1, "1": 60, "2": 3600, "3": 86400})
        duration_sec = tfields["cxtime"] * int(cxtime_units.get(str(tfields["cxtimetype"]), 1) or 1)
        st_rule = timing_cfg.get("starttype_rules", {}).get(str(tfields["starttype"]), {})
        cx_rule = timing_cfg.get("cxtype_rules", {}).get(str(tfields["cxtype"]), {})
        gate_expr = f"{st_rule.get('name', 'immediate')}/{cx_rule.get('name', 'forever')}"

        return TimingSpec(interval_sec=jgtime, duration_sec=duration_sec, gate_expr=gate_expr, **tfields)

    # I18：dispatch_key → evaluator_type 编译期映射（消除运行期 dispatch_key/evaluator 双路径）
    _DISPATCH_KEY_TO_EVALUATOR_TYPE: Dict[str, str] = {
        "TDX_INDICATOR": "formula",
        "TDX_CONDITION_FORMULA": "formula",
        "TDX_EXPERT_SYSTEM": "formula",
        "TDX_FINANCIAL": "scalar",
        "TDX_MARKET": "scalar",
        "TDX_SETOP": "set_operation",
        "INTERSECTION": "intersection",
    }

    # I18：scalar 路径 nset_cfg 字段白名单（从 nset_dispatch 条目提取至 evaluator_params）
    _SCALAR_NSET_CFG_KEYS = frozenset({
        "nset", "field_table", "data_method",
        "supports_derived", "supports_bar_fallback", "apply_field_map",
    })

    @staticmethod
    def _build_filter_spec(edge: Dict[str, Any], nodes: Dict[str, Any]) -> FilterSpec:
        """从 edge params、dispatch.json 编译筛选分派规则，4 个构造分支查 ``_FILTER_SPEC_BUILDERS`` 表路由。"""
        params = _extract_edge_params(edge)
        dispatch_cfg = get_global_config_store().get_table("dispatch") if get_global_config_store() else {}
        nset_dispatch = dispatch_cfg.get("nset_dispatch", {})

        # 解析 tdx_func：边 params → 面板参数合成 → 条件节点继承（前置预处理，非分派分支）
        tdx_func = params.get("tdx_func")
        if not isinstance(tdx_func, dict) or not tdx_func:
            tdx_func = _build_tdx_func_from_panel(params)
        if not isinstance(tdx_func, dict) or not tdx_func:
            tid = _extract_edge_endpoint(edge, ("to", "target", "endid"))
            tgt_node = nodes.get(tid, {})
            tgt_type = str(tgt_node.get("type", tgt_node.get("dzh_cell_type", "")))
            if tgt_type in ("3", "201", "transfer_condition", "condition_filter"):
                tdx_func = tgt_node.get("params", {}).get("tdx_func")
                if not isinstance(tdx_func, dict) or not tdx_func:
                    tdx_func = _build_tdx_func_from_panel(tgt_node.get("params", {}))

        # 三元组特征路由（参数化，无 if/elif 分支）：tdx_func 优先于 formula_ref 优先于 condition_type
        has_tdx_func = isinstance(tdx_func, dict) and bool(tdx_func)
        has_formula_ref = bool(params.get("formula_ref", "")) and not has_tdx_func
        raw_cond = str(params.get("condition_type", "") or "")
        condition_type = "INTERSECTION" if (raw_cond == "INTERSECTION" and not has_tdx_func and not has_formula_ref) else ""
        key = (has_tdx_func, has_formula_ref, condition_type)
        return _FILTER_SPEC_BUILDERS[key](params, tdx_func, nset_dispatch, edge, nodes)

    @staticmethod
    def _build_propagate_spec(edge: Dict[str, Any]) -> PropagateSpec:
        """从 edge params 编译状态流转规则（attr 位域对齐 field_definitions.json bit_fields.flow）。"""
        params = _extract_edge_params(edge)
        attr = edge.get("attr", 0) if isinstance(edge, dict) else 0
        attr_from_params = params.get("attr", 0)
        attr_int = (int(attr) if attr is not None else 0) | (
            int(attr_from_params) if attr_from_params is not None else 0
        )

        pfields = {name: cast(params.get(name)) for name, cast in _PROPAGATE_SPEC_FIELDS.items()}
        delete_source = bool(attr_int & 0x1)        # bit0 移动
        force_move = bool(attr_int & 0x2)           # bit1 与 bit0 组合 0x3 强制覆盖
        keep_source = bool(attr_int & 0x1000)       # bit12 复制（保留源）
        clear_dest_first = (
            bool(params.get("clear_dest_first"))
            or (pfields["emptyps"] == 1)
            or bool(attr_int & 0x2000)              # bit13 先清空目的状态
            or (delete_source and force_move)
        )
        is_move = (pfields["tran"] == 1) or (delete_source and not keep_source)
        mode: Literal["copy", "move", "overwrite", "overwrite_copy"] = _PROPAGATE_MODE_TABLE[(clear_dest_first, is_move)]

        return PropagateSpec(mode=mode, clear_dest_first=clear_dest_first, preserve_source=not is_move or keep_source)

    @classmethod
    def compile(cls, pool_config: Dict[str, Any]) -> CompiledSchedule:
        """编译 ``pool_config`` 为 ``CompiledSchedule``。"""
        nodes = _normalize_nodes(pool_config)
        edges = pool_config.get("edges", [])
        if not isinstance(edges, list):
            edges = []

        edge_ctx = cls._build_edge_ctx(nodes, edges)
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

        # G6：运行时事件无序，不再计算 execution_order/topo_order/depths。
        # 仅保留 edge_index 用于读取边顺序号 _order（条件节点集合运算排序入边）。
        edge_index = {str(edge.get("id") or edge.get("flow_id")): edge
                      for edge in edges
                      if isinstance(edge, dict) and (edge.get("id") or edge.get("flow_id"))}

        # 步骤表驱动：从 edge_strategies.json 读取 steps 序列（编译期产出）
        edge_strategies_cfg = get_global_config_store().get_table("edge_strategies") if get_global_config_store() else {}
        steps_cfg = edge_strategies_cfg.get("steps", [
            {"step_name": "gate"},
            {"step_name": "filter"},
            {"step_name": "propagate"},
            {"step_name": "ttl"},
            {"step_name": "callback"},
        ])

        return CompiledSchedule(
            edge_ctx=edge_ctx,
            edge_timing_spec=timing_spec,
            edge_filter_spec=filter_spec,
            edge_propagate_spec=propagate_spec,
            edge_action_spec=action_spec,
            edge_ttl_spec=ttl_spec,
            node_types=node_types,
            source_node_ids=source_node_ids,
            node_ttl_spec=node_ttl_spec,
            nodes=nodes,
            edge_index=edge_index,
            steps=steps_cfg,
        )


# ===========================================================================
# 变更 F：_FILTER_SPEC_BUILDERS 表 — 4 个 FilterSpec 构造分支同构合并
# key 为 (has_tdx_func, has_formula_ref, condition_type) 三元组特征，value 为构造器，统一签名由分派器查表路由
# ===========================================================================


def _build_filter_spec_from_tdx_func(
    params: Dict[str, Any], tdx_func: Dict[str, Any], nset_dispatch: Dict[str, Any],
    edge: Dict[str, Any], nodes: Dict[str, Any],
) -> FilterSpec:
    """tdx_func 分支：从 tdx_func 参数 + dispatch.json 编译 FilterSpec。"""
    nset = int(tdx_func.get("nset", 0) or 0)
    nset_entry = nset_dispatch.get(str(nset), {})
    dispatch_key = nset_entry.get("dispatch_key", "")
    # accode/ntjindexno 均空时退化为 pass_through；ntjindexno=0 是合法值（nset=4 现价），不能用 `or ""`
    accode_raw = tdx_func.get("accode")
    accode = str(accode_raw) if accode_raw is not None else ""
    ntjindexno_raw = tdx_func.get("ntjindexno")
    ntjindexno = int(ntjindexno_raw) if ntjindexno_raw is not None else 0
    evaluator_type = ("pass_through" if (not accode and ntjindexno_raw is None)
                      else Compiler._DISPATCH_KEY_TO_EVALUATOR_TYPE.get(dispatch_key, "formula"))
    # formula_ref 按 evaluator_type 选择：formula→accode，scalar/set_operation→ntjindexno
    formula_ref = ((accode or str(ntjindexno)) if evaluator_type == "formula"
                   else (str(ntjindexno) if ntjindexno_raw is not None else accode))
    evaluator_params = ({k: v for k, v in nset_entry.items() if k in Compiler._SCALAR_NSET_CFG_KEYS}
                        if evaluator_type == "scalar" else {})
    tdx_fields = {name: cast(tdx_func.get(name)) for name, cast in _TDX_FUNC_SPEC_FIELDS.items()}
    return FilterSpec(
        filter_type=dispatch_key or "formula", formula_ref=formula_ref,
        formula_period=_nperiod_to_period(tdx_func.get("nperiod")),
        threshold=float(tdx_func.get("fsecond") or 0), noperate=int(tdx_func.get("noperate", 0) or 0),
        sorttype=int(tdx_func.get("sorttype", 0) or 0), compare_mode=str(tdx_func.get("compare_mode") or ""),
        evaluator_type=evaluator_type, evaluator_params=evaluator_params, nset=nset, accode=accode, ntjindexno=ntjindexno,
        func=params.get("func") if isinstance(params.get("func"), dict) else {},
        indi=str(params.get("indi", "")).strip(),
        indiparam=params.get("indiparam") if isinstance(params.get("indiparam"), list) else [],
        **tdx_fields,
    )


def _build_filter_spec_from_formula_ref(
    params: Dict[str, Any], tdx_func: Any, nset_dispatch: Dict[str, Any],
    edge: Dict[str, Any], nodes: Dict[str, Any],
) -> FilterSpec:
    """formula_ref 直接分支：边 params 携带 formula_ref，evaluator_type=formula。"""
    formula_ref = params.get("formula_ref", "")
    formula_period = params.get("formula_period", "")
    if not formula_period:
        builtin = _lookup_builtin_formula_info(formula_ref)
        if builtin and builtin.get("period"):
            formula_period = builtin["period"]
    return FilterSpec(
        filter_type="formula", formula_ref=formula_ref, formula_period=formula_period,
        threshold=float(params.get("fsecond") or params.get("threshold") or 0),
        noperate=int(params.get("noperate", 0) or 0), sorttype=int(params.get("sorttype", 0) or 0),
        compare_mode=str(params.get("compare_mode") or ""), evaluator_type="formula", evaluator_params={},
        func=params.get("func") if isinstance(params.get("func"), dict) else {},
        indi=str(params.get("indi", "")).strip(),
        indiparam=params.get("indiparam") if isinstance(params.get("indiparam"), list) else [],
    )


def _build_filter_spec_from_intersection(
    params: Dict[str, Any], tdx_func: Any, nset_dispatch: Dict[str, Any],
    edge: Dict[str, Any], nodes: Dict[str, Any],
) -> FilterSpec:
    """INTERSECTION 分支：condition_type=INTERSECTION，evaluator_type=intersection。"""
    return FilterSpec(
        filter_type="INTERSECTION", evaluator_type="intersection",
        evaluator_params={"intersection_source": params.get("intersection_source", "")},
    )


def _build_filter_spec_passthrough(
    params: Dict[str, Any], tdx_func: Any, nset_dispatch: Dict[str, Any],
    edge: Dict[str, Any], nodes: Dict[str, Any],
) -> FilterSpec:
    """passthrough 分支：按源节点类型退化为 pass_through（其余字段走 FilterSpec 默认值）。"""
    sid = _extract_edge_endpoint(edge, ("from", "source", "startid"))
    edge_type = _resolve_edge_type(_resolve_node_type(nodes.get(sid, {})))
    return FilterSpec(filter_type=edge_type, evaluator_type="pass_through")


# FilterSpec 构造分派表：key 为 (has_tdx_func, has_formula_ref, condition_type) 三元组特征
_FILTER_SPEC_BUILDERS: Dict[Tuple[bool, bool, str], Callable[..., FilterSpec]] = {
    (True, False, ""): _build_filter_spec_from_tdx_func,
    (False, True, ""): _build_filter_spec_from_formula_ref,
    (False, False, "INTERSECTION"): _build_filter_spec_from_intersection,
    (False, False, ""): _build_filter_spec_passthrough,
}


# ===========================================================================
# Task 4：编译-运行分离 — CompiledPool 与 compile 函数
# ===========================================================================
# 按 ``deepen-meta-pattern-strict-metatest-v2`` spec Task 4 实现。
# ``compile(pool_config)`` 一次性产出 ``CompiledPool``，运行期只读预编译结构，
# 不再重复解析节点/边/邻接表/边顺序/边类型/规格。
#
# 与现有 ``Compiler.compile -> CompiledSchedule`` 的关系：
#   - ``CompiledPool`` 是更扁平的编译产物（dict 而非 Spec 对象），
#     直接对齐 spec.md L97-121 的字段定义。
#   - 现有 ``CompiledSchedule`` 仍保留供 ``EdgeExecutor`` 使用，本函数不替换它。
#   - ``edge_order`` 来自 ``edge.params._order``（设计时用户指定），
#     不是拓扑排序——对齐 G6「运行时事件无序」硬约束。


# 节点 legacy_type → role 映射（依据 domain.py 节点子类注册表）
_NODE_LEGACY_TYPE_TO_ROLE: Dict[int, str] = {
    202: "candidate",  # CandidatePoolNode DZH
    7: "candidate",    # CandidatePoolNode TDX
    200: "state",      # StatePoolNode DZH
    8: "state",        # StatePoolNode TDX
    203: "target",     # ResultPoolNode DZH
    201: "condition",  # ConditionNode DZH
    3: "condition",    # ConditionNode TDX
    4: "discard",      # DiscardPoolNode DZH
}


def _resolve_node_role(node: Dict[str, Any]) -> str:
    """从节点 legacy_type / type 字段推断角色。

    role ∈ {candidate, state, condition, target, discard}。
    优先按 legacy_type 整数码查表（与 domain.py 节点子类注册表一致），
    未命中时按 type 字符串子串匹配兜底，均未命中返回空串。
    """
    if not isinstance(node, dict):
        return ""
    lt = node.get("legacy_type")
    if lt is not None:
        try:
            role = _NODE_LEGACY_TYPE_TO_ROLE.get(int(lt))
            if role:
                return role
        except (TypeError, ValueError):
            pass
    t = str(node.get("type", "")).lower()
    if "candidate" in t:
        return "candidate"
    if "condition" in t:
        return "condition"
    if "target" in t or "result" in t:
        return "target"
    if "discard" in t:
        return "discard"
    if "state" in t:
        return "state"
    return ""


# 边条件性判定关键字：params 含任一非默认值 → conditional
_CONDITIONAL_SPEC_KEYS: Tuple[str, ...] = (
    "starttype", "cxtype", "starttime", "cxtime", "cxtimetype", "jgtime",
    "nset", "noperate", "formula_ref", "evaluator_type", "fsecond", "rank_rule",
)


def _is_conditional_edge(params: Dict[str, Any]) -> bool:
    """边类型判定：params 含 filter/timing 相关键且非默认值 → conditional。"""
    for k in _CONDITIONAL_SPEC_KEYS:
        v = params.get(k)
        if v not in (None, 0, "", False):
            return True
    return False


def _compile_timing_spec(params: Dict[str, Any]) -> Dict[str, Any]:
    """从 edge params 编译 timing spec 字典。"""
    return {
        "starttype": int(params.get("starttype", 0) or 0),
        "cxtype": int(params.get("cxtype", 0) or 0),
        "starttime": int(params.get("starttime", 0) or 0),
        "cxtime": int(params.get("cxtime", 0) or 0),
        "cxtimetype": int(params.get("cxtimetype", 0) or 0),
        "jgtime": int(params.get("jgtime", 0) or 0),
    }


def _compile_filter_spec(params: Dict[str, Any]) -> Dict[str, Any]:
    """从 edge params 编译 filter spec 字典。"""
    return {
        "evaluator_type": str(params.get("evaluator_type", "indicator") or "indicator"),
        "nset": int(params.get("nset", 0) or 0),
        "noperate": int(params.get("noperate", 0) or 0),
        "formula_ref": str(params.get("formula_ref", "") or ""),
        "fsecond": params.get("fsecond", 0),
        "rank_rule": str(params.get("rank_rule", "") or ""),
    }


def _compile_propagate_spec(params: Dict[str, Any]) -> Dict[str, Any]:
    """从 edge params 编译 propagate spec 字典。"""
    return {
        "mode": str(params.get("mode", "copy") or "copy"),
        "tran": int(params.get("tran", 0) or 0),
        "emptyps": bool(params.get("emptyps", False)),
    }


@dataclass
class CompiledPool:
    """编译期产出的扁平池结构（spec.md L97-121）。

    运行期只读本结构，不再解析 pool_config。所有索引、顺序、类型判定、
    角色映射均在编译期一次性完成。

    ``edge_order`` 来自 ``edge.params._order``（设计时用户指定），
    非拓扑排序——对齐 G6「运行时事件无序」硬约束。
    """

    nodes: Dict[str, dict] = field(default_factory=dict)
    node_type: Dict[str, str] = field(default_factory=dict)
    edges: Dict[str, dict] = field(default_factory=dict)
    edge_endpoints: Dict[str, tuple] = field(default_factory=dict)
    edge_order: List[str] = field(default_factory=list)
    edge_type: Dict[str, str] = field(default_factory=dict)
    edge_filter_spec: Dict[str, dict] = field(default_factory=dict)
    edge_timing_spec: Dict[str, dict] = field(default_factory=dict)
    edge_propagate_spec: Dict[str, dict] = field(default_factory=dict)
    out_edges: Dict[str, List[str]] = field(default_factory=dict)
    in_edges: Dict[str, List[str]] = field(default_factory=dict)
    source_nodes: List[str] = field(default_factory=list)
    node_role: Dict[str, str] = field(default_factory=dict)


def compile(pool_config: dict) -> CompiledPool:
    """编译 ``pool_config`` 为 ``CompiledPool``（编译-运行分离）。

    一次性产出运行期所需的全部预编译结构：节点/边字典、邻接表、
    源节点列表、边执行顺序（来自 ``edge.params._order``，非拓扑排序）、
    边类型、边规格、节点角色。

    Args:
        pool_config: 股票池配置字典，含 ``nodes`` 与 ``edges``。

    Returns:
        ``CompiledPool`` 扁平编译产物。
    """
    nodes: Dict[str, dict] = _normalize_nodes(pool_config)
    raw_edges = pool_config.get("edges", [])
    if not isinstance(raw_edges, list):
        raw_edges = []

    edges: Dict[str, dict] = {}
    edge_endpoints: Dict[str, tuple] = {}
    out_edges: Dict[str, List[str]] = {nid: [] for nid in nodes}
    in_edges: Dict[str, List[str]] = {nid: [] for nid in nodes}
    edge_type: Dict[str, str] = {}
    edge_filter_spec: Dict[str, dict] = {}
    edge_timing_spec: Dict[str, dict] = {}
    edge_propagate_spec: Dict[str, dict] = {}

    for edge in raw_edges:
        if not isinstance(edge, dict):
            continue
        eid = str(edge.get("id") or edge.get("flow_id") or "")
        if not eid:
            continue
        sid = _extract_edge_endpoint(edge, ("from", "source", "startid"))
        tid = _extract_edge_endpoint(edge, ("to", "target", "endid"))

        edges[eid] = edge
        edge_endpoints[eid] = (sid, tid)
        # 邻接表：端点可能不在 nodes 中（外部引用），用 setdefault 兜底
        out_edges.setdefault(sid, []).append(eid)
        in_edges.setdefault(tid, []).append(eid)

        params = edge.get("params", {})
        if not isinstance(params, dict):
            params = {}
        edge_timing_spec[eid] = _compile_timing_spec(params)
        edge_filter_spec[eid] = _compile_filter_spec(params)
        edge_propagate_spec[eid] = _compile_propagate_spec(params)
        edge_type[eid] = "conditional" if _is_conditional_edge(params) else "unconditional"

    # edge_order：按 edge.params._order 升序（设计时用户指定，非拓扑排序）
    def _order_key(eid: str) -> Tuple[int, str]:
        params = edges[eid].get("params", {})
        if not isinstance(params, dict):
            params = {}
        order = params.get("_order", 0)
        try:
            return (int(order), eid)
        except (TypeError, ValueError):
            return (0, eid)

    edge_order = sorted(edges.keys(), key=_order_key)

    # source_nodes：入度为 0 的节点
    source_nodes = [nid for nid in nodes if not in_edges.get(nid)]

    # node_type / node_role 编译期一次性产出
    node_type: Dict[str, str] = {
        nid: _resolve_node_type(node) for nid, node in nodes.items()
    }
    node_role: Dict[str, str] = {
        nid: _resolve_node_role(node) for nid, node in nodes.items()
    }

    return CompiledPool(
        nodes=nodes,
        node_type=node_type,
        edges=edges,
        edge_endpoints=edge_endpoints,
        edge_order=edge_order,
        edge_type=edge_type,
        edge_filter_spec=edge_filter_spec,
        edge_timing_spec=edge_timing_spec,
        edge_propagate_spec=edge_propagate_spec,
        out_edges=out_edges,
        in_edges=in_edges,
        source_nodes=source_nodes,
        node_role=node_role,
    )


# ===========================================================================
# 单条边执行器（原 core/edge_executor.py）
# ===========================================================================
# 按 ``execute-architecture-migration`` 规格 Task 5 实现。
# ``EdgeExecutor`` 只读 ``CompiledSchedule``，不写 ``pool_config``；所有行为差异
# 来自编译期表行内容，运行期只做查表与固定解释。


def trigger_check(edge_timing_spec: dict, now_ts: float, flow_state: dict, node_dirty: bool) -> bool:
    """Check if edge should trigger: time_ok AND node_dirty."""
    if not node_dirty:
        return False
    starttype = edge_timing_spec.get("starttype", "immediate")
    cxtype = edge_timing_spec.get("cxtype", "always")
    start_ok = _START_RULES.get(starttype, lambda *a: True)(edge_timing_spec, now_ts, flow_state)
    if not start_ok:
        return False
    return _CX_RULES.get(cxtype, lambda *a: True)(edge_timing_spec, now_ts, flow_state)


def filter_eval(codes: list, filter_spec: dict, tick_table) -> tuple:
    """Evaluate filter: returns (passed_codes, rejected_codes)."""
    if not filter_spec or not filter_spec.get("enabled", True):
        return list(codes), []
    nset = filter_spec.get("nset", "all")
    noperate = filter_spec.get("noperate", "gt")
    evaluator = _NSET_EVALUATORS.get(nset, _eval_all)
    operator = _NOPERATE_OPERATORS.get(noperate, _op_gt)
    passed, rejected = [], []
    for code in codes:
        values = evaluator(code, filter_spec, tick_table)
        if operator(values, filter_spec.get("threshold", 0)):
            passed.append(code)
        else:
            rejected.append(code)
    return passed, rejected


def propagate_apply(src_stocks: list, tgt_stocks: list, passed: list, propagate_spec: dict) -> list:
    """Apply propagation: copy/move/overwrite.

    通过 ``_PROPAGATE_MODES`` 字典查表派发；模式描述性 schema 见
    ``config/architecture/propagate_modes.json``（由 ``_PROPAGATE_SPECS_SCHEMA`` 加载）。
    """
    mode = propagate_spec.get("mode", "copy")
    handler = _PROPAGATE_MODES.get(mode, lambda s, t, p: list(set(t + p)))
    return handler(src_stocks, tgt_stocks, passed)


_PROPAGATE_CFG_PATH = Path(__file__).parent.parent / "config" / "architecture" / "propagate_modes.json"
_PROPAGATE_SPECS_SCHEMA: Optional[Dict[str, Any]] = None


def _load_propagate_specs_schema() -> Dict[str, Any]:
    """模块级缓存加载 propagate_modes.json（描述性 schema）。"""
    global _PROPAGATE_SPECS_SCHEMA
    if _PROPAGATE_SPECS_SCHEMA is None:
        try:
            with open(_PROPAGATE_CFG_PATH, "r", encoding="utf-8") as f:
                _PROPAGATE_SPECS_SCHEMA = json.load(f)
        except (OSError, json.JSONDecodeError):
            _PROPAGATE_SPECS_SCHEMA = {}
    return _PROPAGATE_SPECS_SCHEMA


# 与 _NSET_EVALUATORS 同样的 lambda 模式：JSON 不能序列化函数，故派发表内联在代码中，
# 描述性字段（src_action/tgt_action）保存在 propagate_modes.json。
_PROPAGATE_MODES = {
    "copy": lambda src, tgt, p: list(set(tgt + p)),
    "move": lambda src, tgt, p: list(set(tgt + p)),
    "overwrite": lambda src, tgt, p: list(p),
}


def _eval_all(code: str, spec: dict, tick_table) -> list:
    return [1]


def _op_gt(values: list, threshold: float) -> bool:
    return any(x > threshold for x in values)


_START_RULES = {
    "immediate": lambda spec, now, state: True,
    "delay": lambda spec, now, state: now >= (state.get("start_ts", 0) + spec.get("delay", 0)),
    "open_before": lambda spec, now, state: True,
    "open_after": lambda spec, now, state: True,
    "close_before": lambda spec, now, state: True,
    "close_after": lambda spec, now, state: True,
    "fixed_time": lambda spec, now, state: now >= spec.get("fixed_ts", 0),
    "fixed_trading": lambda spec, now, state: True,
}

_CX_RULES = {
    "always": lambda spec, now, state: True,
    "once": lambda spec, now, state: state.get("exec_count", 0) == 0,
    "duration": lambda spec, now, state: now <= (state.get("start_ts", 0) + spec.get("duration", 0)),
}

_NSET_EVALUATORS = {
    "all": lambda code, spec, tt: [1],
    "formula": lambda code, spec, tt: [1],
    "condition": lambda code, spec, tt: [1],
    "indicator": lambda code, spec, tt: [1],
    "expert": lambda code, spec, tt: [1],
    "realtime": lambda code, spec, tt: [tt.get(code).get("price", 0)],
}

_NOPERATE_OPERATORS = {
    "gt": lambda v, t: any(x > t for x in v),
    "ge": lambda v, t: any(x >= t for x in v),
    "lt": lambda v, t: any(x < t for x in v),
    "le": lambda v, t: any(x <= t for x in v),
    "eq": lambda v, t: any(x == t for x in v),
    "ne": lambda v, t: any(x != t for x in v),
    "top": lambda v, t: True,
    "bottom": lambda v, t: True,
    "top_n": lambda v, t: True,
    "bottom_n": lambda v, t: True,
}

# ---------------------------------------------------------------------------
# filter_specs.json 描述性 schema（与 timing.json 加载模式一致）
# ---------------------------------------------------------------------------
# JSON 不能序列化函数，故运行时函数派发仍使用上面的 ``_NSET_EVALUATORS``
# 与 ``_NOPERATE_OPERATORS`` lambda 字典；本 schema 仅用于描述性文档/校验，
# 是未来将 evaluator/operator 元数据外部化为配置的入口。
_FILTER_SPECS_PATH = Path(__file__).parent.parent / "config" / "architecture" / "filter_specs.json"
_FILTER_SPECS_SCHEMA: Optional[Dict[str, Any]] = None


def _load_filter_specs_schema() -> Dict[str, Any]:
    """模块级缓存加载 filter_specs.json。"""
    global _FILTER_SPECS_SCHEMA
    if _FILTER_SPECS_SCHEMA is None:
        try:
            with open(_FILTER_SPECS_PATH, "r", encoding="utf-8") as f:
                _FILTER_SPECS_SCHEMA = json.load(f)
        except (OSError, json.JSONDecodeError):
            _FILTER_SPECS_SCHEMA = {}
    return _FILTER_SPECS_SCHEMA


def _now_ts(state: PoolState) -> float:
    """从 ``state.time_source`` 或本地时间获取当前时间戳。

    返回 ``time_at(state)`` 原值——与 ``EventDriver.fire_due(now)`` 中 ``now`` 单位一致。
    不再转换为 Unix 时间戳，因为 TTL 的 ``fire_due`` / ``add_spec``
    全链路共享 ``time_at`` 返回的统一时间单位（wall_clock=Unix秒，virtual=当日秒数偏移）。
    """
    return time_at(state=state)


# TDX noperate 编码 → 比较操作符（差异显于表内容，无 if/elif 分派）。
_NOPERATE_TO_OP: Dict[int, str] = {
    0: ">",
    1: "<",
    2: "==",
    3: ">=",
    4: "<=",
    5: "!=",
}

# 比较操作符 → Python operator 函数（无 if/elif 分派）。
_OP_FUNCS: Dict[str, Callable[[Any, Any], bool]] = {
    ">": operator.gt,
    "<": operator.lt,
    "==": operator.eq,
    ">=": operator.ge,
    "<=": operator.le,
    "!=": operator.ne,
}


def _parse_noperate(noperate: int) -> str:
    """TDX noperate 编码 → 比较操作符。"""
    return _NOPERATE_TO_OP.get(int(noperate), ">")


def _value_passes(value: Any, threshold: float, op: str) -> bool:
    """按操作符比较公式返回值与阈值。"""
    if value is None:
        return False
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, dict):
        return False
    try:
        v = float(value)
        t = float(threshold)
    except (TypeError, ValueError):
        return bool(value)
    return _OP_FUNCS.get(op, operator.gt)(v, t)


def _stock_entry_time(stock: Any) -> Optional[float]:
    """提取股票的入池时间戳，用于 TTL。"""
    if not isinstance(stock, dict):
        return None
    # 显式记录的入池时间
    for key in ("_entry_time", "entry_time", "entry_ts"):
        val = stock.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
    # tracker 中记录的入场时间
    tracker = stock.get("_tracker")
    if isinstance(tracker, dict):
        for key in ("entry_time", "entry_ts", "_entry_time"):
            val = tracker.get(key)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
    # 兼容 DZH 风格的 indate + intime
    indate = stock.get("indate")
    intime = stock.get("intime")
    if indate is not None and intime is not None:
        try:
            return _parse_indate_intime(str(indate), str(intime))
        except Exception:
            pass
    return None


def _parse_indate_intime(indate: str, intime: str) -> float:
    """将 indate(YYYYMMDD) 与 intime(HHMMSS) 解析为时间戳。"""
    from datetime import datetime

    d = str(indate).zfill(8)
    t = str(intime).zfill(6)
    dt = datetime(
        int(d[:4]),
        int(d[4:6]),
        int(d[6:8]),
        int(t[:2]),
        int(t[2:4]),
        int(t[4:6]),
    )
    return dt.timestamp()


# ---------------------------------------------------------------------------
# callback / ttl 作为模块级纯函数，保证 EdgeExecutor 方法数 ≤ 6
# ---------------------------------------------------------------------------


def _init_entry_trackers(
    state: PoolState,
    tgt: str,
    entered: List[str],
    ts: float,
    eid: str,
    tick_table: "TickTable",
    ttl_spec: Optional[Any] = None,
    event_driver: Optional[Any] = None,
    bus: Any = None,
) -> Dict[str, float]:
    """为新进入目标池的股票创建/初始化 tracker，并注册 interval 类型 TTL。

    G1 heapq 驱动：
      - check_type="interval"：per-code 注册到 heapq（一次性，fire_time=ts+ttl_sec）
      - check_type="endtime"：编译期已注册 TimedEventSpec（时钟触发），无需运行期注册
      - check_type="none"：无 TTL，跳过
    """
    prices: Dict[str, float] = {}
    tgt_stocks = state.get_pool(tgt).get_stocks()
    tgt_index = {_stock_code(s): s for s in tgt_stocks if isinstance(s, dict)}

    for code in entered:
        close = tick_table.column(code, "close")
        price = float(close or 0.0)
        prices[code] = price

        stock = tgt_index.get(code)
        if isinstance(stock, dict):
            tracker = {
                "market": str(stock.get("market", "0")),
                "code": code,
                "entry_price": price,
                "entry_time": ts,
                "current_price": price,
                "pool_id": tgt,
                "flow_id": eid,
                "ttl": int(stock.get("ttl", 0) or 0),
                "status": "holding",
            }
            stock["_tracker"] = tracker

            if ttl_spec is not None and event_driver is not None and ttl_spec.bdel == 1 and ttl_spec.check_type == "interval" and ttl_spec.ttl_sec > 0:
                register_ttl_spec(event_driver, state, tgt, eid, code, ttl_spec.ttl_sec, ts, bus)

    return prices


# ---------------------------------------------------------------------------
# target_pool_actions 表驱动分派（I20：消除 _run_callback 内 if action == "baimpool"）
# I23：DomainEvent(ENTER) 合并入 Executed.details，_action_enter 删除；
#      _ACTION_HANDLERS 仅保留 baimpool（产生 BUY Signal），其它动作信息
#      由 Executed.details.actions 携带，不再 per-code 发布 DomainEvent。
# I34：_action_baimpool 扩展 Signal 字段（condition/profit_pct/hold_days），
#      BUY 信号经 EventBus → _on_signal_event 订阅写入 _signal_queue，
#      消除与 _emit_domain_event 的双发重复。
# ---------------------------------------------------------------------------

def _lookup_edge_cond(pool_config: Dict[str, Any], eid: str) -> str:
    """I34：从池配置解析边条件标识（accode/label/eid），供 BUY Signal.condition 字段。

    I35：消除双实现 — 原 PoolEngine._find_edge_condition 已删除，
    engine.py 现直接导入本函数复用。优先 tdx_func.accode，其次 edge.label，
    最后回退 eid。
    """
    if not pool_config:
        return eid
    for e in pool_config.get('edges', []):
        if e.get('id') == eid:
            ep = e.get('params', {}) if isinstance(e.get('params'), dict) else {}
            tf = ep.get('tdx_func', {})
            return tf.get('accode', '') if isinstance(tf, dict) and tf.get('accode') else (e.get('label', '') or eid)
    return eid


def _action_baimpool(
    bus: Optional[EventBus], ec: EdgeContext, code: str, tgt: str,
    price: float, ts: float, action: str, cond: str = "",
) -> None:
    """baimpool 动作：发布 BUY 信号（目标池入池）。

    I34：扩展 condition 字段（profit_pct/hold_days 对新入池为 0，由 Signal
    dataclass 默认值提供）。BUY 经 EventBus → _on_signal_event → _signal_queue。
    """
    _publish(bus, Signal(
        signal_type="BUY",
        code=code,
        pool_id=tgt,
        price=price,
        ts=ts,
        quantity=100,
        condition=cond,
    ))


# action → handler（表驱动，无 if/elif 分派）。I23：仅 baimpool 注册（产生 Signal）；
# 未注册 action（bsound/btip/bsavetoblock/bsavehis）不再产生独立事件，其 action
# 名由 Executed.details.actions 列表携带，订阅者从 Executed 即可获取完整入池语义。
_ACTION_HANDLERS: Dict[str, Callable] = {
    "baimpool": _action_baimpool,
}


def _run_callback(
    state: PoolState,
    ec: EdgeContext,
    action_spec: ActionSpec,
    tgt: str,
    entered: List[str],
    ts: float,
    prices: Dict[str, float],
    bus: Optional[EventBus],
) -> None:
    """目标节点副作用：发布 baimpool BUY Signal。

    I23：``DomainEvent(ENTER)`` 已合并入 ``Executed.details``（actions/prices/timestamp），
    不再 per-code 发布。``_init_entry_trackers`` 移至 ``run()`` 以便 Executed.details
    携带 prices。本函数仅处理 baimpool 的 per-code BUY Signal；未注册 action
    不再产生独立事件，其 action 名由 ``Executed.details.actions`` 携带。
    I34：解析边条件 cond 传入 _action_baimpool，使 BUY Signal.condition 字段非空。
    """
    if not entered or bus is None:
        return

    cond = _lookup_edge_cond(state.pool_config, ec.eid)
    for code in entered:
        price = prices.get(code, 0.0)
        for action in action_spec.target_pool_actions:
            handler = _ACTION_HANDLERS.get(action)
            if handler is not None:
                handler(bus, ec, code, tgt, price, ts, action, cond)


def _publish(bus: Optional[EventBus], event: Any) -> None:
    """辅助：``bus`` 不为 None 时发布事件。

    I22：删除原 ``try/except + logger.debug`` 双重异常吞掉——``EventBus.publish``
    内部已隔离订阅者异常（I22 改为 ``logger.warning``），外层 try/except 是冗余防御，
    且 ``logger.debug`` 级别在生产中默认不可见，等于静默吞掉总线自身异常。
    """
    if bus is not None:
        bus.publish(event)


def _publish_edge_fired(bus: Optional[EventBus], eid: str, ts: float) -> None:
    """统一发布 EdgeFired 事件（bus 为 None 时跳过）。"""
    if bus is not None:
        bus.publish(EdgeFired(eid=eid, ts=ts))


def _publish_ttl_due(bus: Optional[EventBus], node_id: str, code: str, ts: float) -> None:
    """统一发布 TTLDue 事件（bus 为 None 时跳过）。"""
    if bus is not None:
        bus.publish(TTLDue(node_id=node_id, code=code, ts=ts))


# ---------------------------------------------------------------------------
# TTL check_type 表驱动分派（I17：消除 if/else，差异显于注册表内容）
# ---------------------------------------------------------------------------

_TTLResult = Tuple[List[Any], List[str], int]


def _seconds_of_day(dt) -> int:
    """返回 datetime 的当天秒数。"""
    return dt.hour * 3600 + dt.minute * 60 + dt.second


def _parse_hms_int(hms: int) -> int:
    """将 HHMMSS 整数解析为当天秒数。"""
    s = str(int(hms)).zfill(6)
    return int(s[:2]) * 3600 + int(s[2:4]) * 60 + int(s[4:6])


# ---------------------------------------------------------------------------
# 时机门控表驱动：starttype → handler，差异显于注册表内容。
# ---------------------------------------------------------------------------

_TIMING_CFG_PATH = Path(__file__).parent.parent / "config" / "architecture" / "timing.json"
_TIMING_CFG: Optional[Dict[str, Any]] = None


def _load_timing_cfg() -> Dict[str, Any]:
    """模块级缓存加载 timing.json。"""
    global _TIMING_CFG
    if _TIMING_CFG is None:
        try:
            with open(_TIMING_CFG_PATH, "r", encoding="utf-8") as f:
                _TIMING_CFG = json.load(f)
        except (OSError, json.JSONDecodeError):
            _TIMING_CFG = {}
    return _TIMING_CFG


def _market_seconds(cfg: Dict[str, Any]) -> Tuple[int, int]:
    """从 timing.json 读取开盘/收盘秒数。"""
    market = cfg.get("market_calendar", {})
    return int(market.get("open_sec", 34500)), int(market.get("close_sec", 54000))


def _offset_seconds(spec: "TimingSpec", cfg: Dict[str, Any]) -> int:
    """starttime 按 starttimetype 换算为秒。"""
    units = cfg.get("offset_units", {"0": 1, "1": 60, "2": 3600})
    return spec.starttime * int(units.get(str(spec.starttimetype), 1))


def _current_seconds_of_day(now: float) -> int:
    """当前时间对应当天秒数。

    virtual/sequence 模式下 ``current_ts`` 直接保存当日秒数偏移（如 34500），
    此时直接返回该值；wall_clock / 真实时间戳模式下从 datetime 解析。
    I40：1e8 阈值收敛为 ``is_offset_of_day``（time_util 单一真相源）。
    """
    if is_offset_of_day(now):
        return int(now)
    from datetime import datetime

    return _seconds_of_day(datetime.fromtimestamp(now))


def _gate_always(spec: "TimingSpec", state: PoolState, eid: str, now_unix: float, now_sec: int, cfg: Dict[str, Any]) -> bool:
    return True


def _gate_never(spec: "TimingSpec", state: PoolState, eid: str, now_unix: float, now_sec: int, cfg: Dict[str, Any]) -> bool:
    return False


def _gate_elapsed(spec: "TimingSpec", state: PoolState, eid: str, now_unix: float, now_sec: int, cfg: Dict[str, Any]) -> bool:
    start_ts = state.time_source.get("start_ts")
    if start_ts is None:
        start_ts = state.get_exec_ctx(eid).get("first_fire")
    if start_ts is None:
        return False
    return now_unix - float(start_ts) >= _offset_seconds(spec, cfg)


def _gate_before_open(spec: "TimingSpec", state: PoolState, eid: str, now_unix: float, now_sec: int, cfg: Dict[str, Any]) -> bool:
    open_sec, _close_sec = _market_seconds(cfg)
    offset = _offset_seconds(spec, cfg)
    return open_sec - offset <= now_sec <= open_sec


def _gate_after_open(spec: "TimingSpec", state: PoolState, eid: str, now_unix: float, now_sec: int, cfg: Dict[str, Any]) -> bool:
    open_sec, _close_sec = _market_seconds(cfg)
    offset = _offset_seconds(spec, cfg)
    return now_sec >= open_sec + offset


def _gate_before_close(spec: "TimingSpec", state: PoolState, eid: str, now_unix: float, now_sec: int, cfg: Dict[str, Any]) -> bool:
    _open_sec, close_sec = _market_seconds(cfg)
    offset = spec.starttime * 60
    return close_sec - offset <= now_sec <= close_sec


def _gate_after_close(spec: "TimingSpec", state: PoolState, eid: str, now_unix: float, now_sec: int, cfg: Dict[str, Any]) -> bool:
    _open_sec, close_sec = _market_seconds(cfg)
    offset = spec.starttime * 60
    return now_sec >= close_sec + offset


def _gate_hhmmss(spec: "TimingSpec", state: PoolState, eid: str, now_unix: float, now_sec: int, cfg: Dict[str, Any]) -> bool:
    return now_sec >= _parse_hms_int(spec.starttimehms)


# starttype → gate handler（表驱动，无 if/elif 分派）。
# I42：handler 签名 (spec, state, eid, now_unix, now_sec, cfg) 双时间参数——
# now_unix 服务 elapsed（Unix 算术），now_sec 服务 5 个市场时间 gate（秒数比较）。
# 消除 _gate 内 offset→anchor→Unix→datetime.fromtimestamp→秒数往返。
_STARTTYPE_GATE_HANDLERS: Dict[int, Callable[["TimingSpec", PoolState, str, float, int, Dict[str, Any]], bool]] = {
    0: _gate_always,
    1: _gate_elapsed,
    2: _gate_before_open,
    3: _gate_after_open,
    4: _gate_before_close,
    5: _gate_after_close,
    6: _gate_hhmmss,
    7: _gate_hhmmss,
}


def _starttype_gate(spec: "TimingSpec", state: PoolState, eid: str, now_unix: float, now_sec: int) -> bool:
    """按 TimingSpec.starttype 计算 gate 是否放行。

    市场时间从 timing.json 读取；所有分支差异收敛到上表。
    I42：双时间参数（now_unix / now_sec）由调用方一次性计算，消除 handler 内
    反向解 anchoring 的往返。
    """
    cfg = _load_timing_cfg()
    handler = _STARTTYPE_GATE_HANDLERS.get(spec.starttype, _gate_never)
    return handler(spec, state, eid, now_unix, now_sec, cfg)


# ---------------------------------------------------------------------------
# cxtype 后置门控表驱动（I19）：cxtype → handler，与 _STARTTYPE_GATE_HANDLERS 对称。
# 消除 _gate 内 `if cxtype == 2` + `if duration_sec > 0` 双 if 分派；
# duration 检查收敛进 cxtype=1 handler，不再对 cxtype=0/2 误触发（latent bug 修复）。
# ---------------------------------------------------------------------------

def _cxtype_forever(spec: "TimingSpec", exec_ctx: Dict[str, Any], now: float) -> bool:
    """cxtype=0: 永远不 expire（无后置检查）。"""
    return True


def _cxtype_duration(spec: "TimingSpec", exec_ctx: Dict[str, Any], now: float) -> bool:
    """cxtype=1: 持续窗口检查（first_fire + duration_sec 后 expire）。"""
    if spec.duration_sec <= 0:
        return True
    first_fire = exec_ctx.get("first_fire")
    if first_fire is not None and now - first_fire > spec.duration_sec:
        return False
    return True


def _cxtype_once(spec: "TimingSpec", exec_ctx: Dict[str, Any], now: float) -> bool:
    """cxtype=2: 只执行一次（count >= 1 后 expire）。"""
    return exec_ctx.get("count", 0) < 1


_CXTYPE_POST_GATES: Dict[int, Callable[["TimingSpec", Dict[str, Any], float], bool]] = {
    0: _cxtype_forever,
    1: _cxtype_duration,
    2: _cxtype_once,
}


# ---------------------------------------------------------------------------
# 股票过滤：bnst(排除ST) / bnotp(排除新股) / bnotq(排除停牌)
# ---------------------------------------------------------------------------

_STOCK_NAMES_CACHE: Optional[Dict[str, str]] = None


def _load_stock_names() -> Dict[str, str]:
    """加载股票名称数据（用于ST判断）。"""
    global _STOCK_NAMES_CACHE
    if _STOCK_NAMES_CACHE is not None:
        return _STOCK_NAMES_CACHE
    try:
        data = load_config_table("mock_data")
        _STOCK_NAMES_CACHE = data.get("stock_names", {})
    except Exception:
        _STOCK_NAMES_CACHE = {}
    return _STOCK_NAMES_CACHE


def _is_st_stock(code: str, stock_names: Dict[str, str]) -> bool:
    """判断是否为ST股票（名称包含ST或*ST）。"""
    if not code:
        return False
    pure_code = code.split(".")[0] if "." in code else code
    name = stock_names.get(pure_code, "") or stock_names.get(code, "")
    if not name:
        return False
    name_upper = name.upper()
    return "ST" in name_upper


def _is_suspended(code: str, state: "PoolState") -> bool:
    """判断是否为停牌股票（latest_tick无数据或成交量为0且价格未变化）。"""
    if state is None:
        return False
    latest_tick = getattr(state, "latest_tick", {}) or {}
    tick = latest_tick.get(code)
    if tick is None:
        return True
    if not isinstance(tick, dict):
        return False
    vol = tick.get("volume", tick.get("vol", 0))
    try:
        vol_val = float(vol or 0)
    except (TypeError, ValueError):
        vol_val = 0
    if vol_val <= 0:
        return True
    return False


def _is_new_stock(code: str, state: "PoolState") -> bool:
    """判断是否为新股/次新股（上市不足60交易日）。

    Mock环境无上市日期数据，保守返回False（不过滤）。
    """
    return False


def _apply_stock_filters(
    codes: List[str],
    spec: FilterSpec,
    state: "PoolState",
) -> List[str]:
    """应用bnst/bnotp/bnotq股票排除开关。

    Args:
        codes: 待筛选代码列表
        spec: FilterSpec（含bnst/bnotp/bnotq开关）
        state: PoolState（用于判断停牌）

    Returns:
        过滤后的代码列表
    """
    if not codes:
        return []
    need_st = spec.bnost
    need_new = spec.bnotp
    need_suspend = spec.bnotq
    if not (need_st or need_new or need_suspend):
        return list(codes)
    stock_names = _load_stock_names() if need_st else {}
    result = []
    for code in codes:
        if need_st and _is_st_stock(code, stock_names):
            continue
        if need_new and _is_new_stock(code, state):
            continue
        if need_suspend and _is_suspended(code, state):
            continue
        result.append(code)
    return result


def _extract_line_from_series(series_result: Dict[str, Any], line_name: str, line_idx: int) -> Optional[List[float]]:
    """从公式序列结果中提取指定名称的指标线序列。

    Args:
        series_result: 单只股票的eval_series返回值，如 {"K": [1.1, 1.2, 1.3], "D": [...]}
        line_name: 指标线名称（如"K"、"DIF"）
        line_idx: 指标线索引（备用，当line_name为空时按索引取）

    Returns:
        数值序列列表（最近N个值，最后一个为最新值）
    """
    if not series_result or not isinstance(series_result, dict):
        return None
    if line_name and line_name in series_result:
        val = series_result[line_name]
        if isinstance(val, list):
            return [float(x) for x in val if x is not None]
        return None
    if line_name:
        upper_name = line_name.upper()
        for k, v in series_result.items():
            if k.upper() == upper_name:
                if isinstance(v, list):
                    return [float(x) for x in v if x is not None]
                return None
    if line_idx >= 0:
        keys = list(series_result.keys())
        if line_idx < len(keys):
            val = series_result[keys[line_idx]]
            if isinstance(val, list):
                return [float(x) for x in val if x is not None]
            return None
    keys = list(series_result.keys())
    if keys:
        val = series_result[keys[0]]
        if isinstance(val, list):
            return [float(x) for x in val if x is not None]
    return None


# nset5 集合运算：0=并集 1=差集 2=交集
_NSET5_OPS: Dict[int, Callable[[set, set], set]] = {
    0: lambda a, b: a | b,
    1: lambda a, b: a - b,
    2: lambda a, b: a & b,
}


def _eval_set_operation(
    state: PoolState,
    schedule: CompiledSchedule,
    eid: str,
    codes: List[str],
    op_code: int,
) -> Tuple[List[str], List[str]]:
    """计算 nset=5 条件节点的集合运算结果。

    对当前边的源股票与所有流入同一目标节点的其它边的源股票做集合运算：
      - 0 (union):     源 ∪ 其它 = 全部源股票
      - 1 (difference):源 - 其它
      - 2 (intersection): 源 ∩ 其它
    单输入边时，差集/并集返回源股票，交集返回空。
    """
    ec = schedule.edge_ctx.get(eid)
    if ec is None:
        return list(codes), []

    source_set = set(codes)
    tid = ec.tid
    sid = ec.sid

    other_stocks: set = set()
    in_edges = [e for e in schedule.edge_ctx.values() if e.tid == tid and e.eid != eid]
    if len(in_edges) < 1 and op_code == 2:
        # 单输入求交集为空（in_edges 已排除当前边，<1 即无其它输入边）
        return [], list(codes)

    for other in in_edges:
        if other.sid == sid:
            continue
        other_stocks |= {_stock_code(s) for s in state.get_pool(other.sid).get_stocks()}

    op = _NSET5_OPS.get(op_code)
    if op is None:
        return list(codes), []

    passed_set = op(source_set, other_stocks)
    passed = [c for c in codes if c in passed_set]
    rejected = [c for c in codes if c not in passed_set]
    return passed, rejected


# ---------------------------------------------------------------------------
# FilterSpec evaluator_type 表驱动分派（I18：消除 _filter if/elif + _eval_formula 双路径）
# 每个 handler 接收 (state, schedule, formula_engine, tick_table, spec, codes, eid)，
# 返回 passed 代码列表。rejected 由 _filter 统一计算。


def _eval_pass_through(
    state: PoolState,
    schedule: CompiledSchedule,
    formula_engine: FormulaEngineProtocol,
    tick_table: "TickTable",
    spec: FilterSpec,
    codes: List[str],
    eid: str,
    eval_deps: Optional["_FilterEvalDeps"] = None,
) -> List[str]:
    """透传：全部通过（无条件边 / 无公式条件边）。"""
    return list(codes)


def _eval_formula_path(
    state: PoolState,
    schedule: CompiledSchedule,
    formula_engine: FormulaEngineProtocol,
    tick_table: "TickTable",
    spec: FilterSpec,
    codes: List[str],
    eid: str,
    eval_deps: Optional["_FilterEvalDeps"] = None,
) -> List[str]:
    """公式求值路径：nset=0/1/2，支持全部10种noperate操作符。

    完整支持TDX func节点16参数：
    - nset/accode/ntjindexno/nperiod/nfirst/cfirst/noperate/nsecond/csecond/fsecond
    - 通过eval_series获取序列数据，支持cross/inflection/rank/compare
    - nset=1/2条件选股/专家系统：公式返回XG信号，直接判断XG>0
    - bnost/bnotp/bnotq股票过滤在结果后应用

    spec.md L128：公式求值异常或无效配置（formula_ref 缺失）时，
    通过 ``eval_deps.bus`` 发布携带 ``error`` 字段的 FormulaEvaluated 事件，
    供下游订阅者诊断；求值成功路径 ``error`` 保持默认空串。
    """
    if not codes:
        return []

    # spec.md L128：formula_ref 缺失视为无效配置，发布携带 error 的事件并降级返回。
    formula_ref = getattr(spec, "formula_ref", "") or ""
    bus = getattr(eval_deps, "bus", None) if eval_deps is not None else None
    if not formula_ref:
        logger.warning("公式求值跳过：formula_ref 为空 eid=%s", eid)
        if bus is not None:
            _publish(bus, FormulaEvaluated(
                formula_ref=formula_ref,
                result=None,
                code="",
                bar_hash="",
                error="formula_ref 为空，无法执行公式求值",
            ))
        return []

    try:
        period = spec.formula_period or "1d"
        ctx = eval_deps.live_ctx_fn(state, period=period)
        ctx.period = period
        noperate = spec.noperate
        lookback = _resolve_series_lookback(noperate)
        series_results = formula_engine.eval_series(spec, codes, ctx, lookback=lookback)
    except Exception as ex:
        logger.warning("公式序列求值失败 %s: %s", spec.formula_ref, ex)
        # spec.md L128：异常路径发布携带 error 的 FormulaEvaluated 事件
        if bus is not None:
            _publish(bus, FormulaEvaluated(
                formula_ref=formula_ref,
                result=None,
                code="",
                bar_hash="",
                error=str(ex),
            ))
        return []

    nset = spec.nset
    fsecond = spec.threshold
    cfirst = spec.cfirst
    csecond = spec.csecond
    nfirst = spec.nfirst
    nsecond = spec.nsecond

    if nset in (1, 2):
        passed = []
        for code in codes:
            sres = series_results.get(code)
            if sres is None:
                continue
            xg_line = None
            if "XG" in sres:
                xg_line = sres["XG"]
            else:
                for k, v in sres.items():
                    if k.upper() == "XG":
                        xg_line = v
                        break
            if xg_line is None:
                line1 = _extract_line_from_series(sres, cfirst, nfirst)
                if line1 and len(line1) > 0:
                    try:
                        if float(line1[-1]) > 0:
                            passed.append(code)
                    except (TypeError, ValueError):
                        pass
                continue
            if isinstance(xg_line, list) and len(xg_line) > 0:
                try:
                    last_val = xg_line[-1]
                    if last_val is not None and float(last_val) > 0:
                        passed.append(code)
                except (TypeError, ValueError):
                    pass
        return passed

    passed = _apply_noperate_mode_series(
        series_results, codes, noperate, fsecond,
        cfirst, nfirst, csecond, nsecond, _extract_line_from_series,
    )
    return passed


def _eval_scalar_path(
    state: PoolState,
    schedule: CompiledSchedule,
    formula_engine: FormulaEngineProtocol,
    tick_table: "TickTable",
    spec: FilterSpec,
    codes: List[str],
    eid: str,
    eval_deps: Optional["_FilterEvalDeps"] = None,
) -> List[str]:
    """标量评估路径：nset=3/4（财务/行情选股），委托注入的标量 nset 评估器。

    使用spec.ntjindexno作为字段索引（而非旧的formula_ref），
    传递完整的tdx_func参数，结果后应用股票过滤。
    """
    if not codes:
        return []

    kind = state.time_source.get("kind", "live")
    formula_mode = kind if kind in ("live", "replay", "simulation") else "live"
    ctx = eval_deps.eval_ctx_factory(
        mode=formula_mode,
        bar_hash=tick_table.bar_hash(),
        bars={},
        latest_tick=state.latest_tick,
    )

    def _evaluator(codes: List[str], ctx: Any) -> Dict[str, Any]:
        prev_lookup = lambda c: tick_table.prev_column(c, "line1")
        action_inputs = {
            "src_params": {"tdx_func": {
                "ntjindexno": spec.ntjindexno,
                "noperate": spec.noperate,
                "fsecond": spec.threshold,
                "accode": spec.accode,
                "cfirst": spec.cfirst,
                "csecond": spec.csecond,
                "nfirst": spec.nfirst,
                "nsecond": spec.nsecond,
            }},
            "stock_list": codes,
            "market_data_port": getattr(state, "market_data_port", None),
            "current_bar_data": getattr(state, "current_bar_data", {}),
        }
        nset_cfg = spec.evaluator_params or {"nset": 0}
        if "nset" not in nset_cfg:
            nset_cfg["nset"] = spec.nset if spec.nset in (3, 4) else 3
        passed = eval_deps.scalar_nset_fn(action_inputs, nset_cfg, prev_lookup=prev_lookup)
        passed_set = set(passed)
        return {c: (c in passed_set) for c in codes}

    results = formula_engine.eval_scalar(spec, codes, ctx, _evaluator)
    passed = [c for c in codes if results.get(c)]
    return passed


def _eval_set_op_path(
    state: PoolState,
    schedule: CompiledSchedule,
    formula_engine: FormulaEngineProtocol,
    tick_table: "TickTable",
    spec: FilterSpec,
    codes: List[str],
    eid: str,
    eval_deps: Optional["_FilterEvalDeps"] = None,
) -> List[str]:
    """集合运算路径：nset=5（交集/并集/差集）。

    使用spec.ntjindexno作为操作码（0=并集/1=差集/2=交集），
    而非旧的formula_ref，结果后应用股票过滤。
    """
    op_code = int(spec.ntjindexno) if spec.ntjindexno is not None else 0
    passed, _rejected = _eval_set_operation(state, schedule, eid, codes, op_code)
    return passed


def _eval_intersection_path(
    state: PoolState,
    schedule: CompiledSchedule,
    formula_engine: FormulaEngineProtocol,
    tick_table: "TickTable",
    spec: FilterSpec,
    codes: List[str],
    eid: str,
    eval_deps: Optional["_FilterEvalDeps"] = None,
) -> List[str]:
    """交集条件路径：委托 evaluate_intersection 筛选与源状态池的交集。"""
    edge_params = spec.evaluator_params or {}
    return evaluate_intersection(codes, state, edge_params)


# evaluator_type → handler（表驱动，无 if/elif 分派）
# formula/scalar/set_operation 在注册时套 _with_stock_filters 后过滤包装器，
# 统一应用 bnost/bnotp/bnotq 排除（pass_through/intersection 豁免，保留原语义）。
def _with_stock_filters(handler: Callable[..., List[str]]) -> Callable[..., List[str]]:
    """后过滤包装器：在 handler 返回的 passed 列表上统一应用股票排除开关。"""

    def _wrapped(state, schedule, formula_engine, tick_table, spec, codes, eid, eval_deps=None):
        passed = handler(
            state, schedule, formula_engine, tick_table, spec, codes, eid,
            eval_deps=eval_deps,
        )
        return _apply_stock_filters(passed, spec, state)

    return _wrapped


_FILTER_EVALUATORS: Dict[str, Callable[..., List[str]]] = {
    "pass_through": _eval_pass_through,
    "formula": _with_stock_filters(_eval_formula_path),
    "scalar": _with_stock_filters(_eval_scalar_path),
    "set_operation": _with_stock_filters(_eval_set_op_path),
    "intersection": _eval_intersection_path,
}


# ---------------------------------------------------------------------------
# Task 8：条件节点激活模型常量
# ---------------------------------------------------------------------------
# 条件节点类型集合：识别 EdgeFired 目标节点是否为条件节点。
# 含 DZH/TDX 标准类型 + 实例 JSON 使用的 "condition" 简写。
_CONDITION_NODE_TYPES: frozenset = frozenset({
    "condition", "transfer_condition", "tdx_condition",
    "dzh_condition_pool", "condition_filter",
})

# JSON filter_spec.evaluator_type="indicator" → 运行期 "formula"（HQChartPy2 公式路径）
_COND_EVALUATOR_TYPE_MAP: Dict[str, str] = {
    "indicator": "formula",
    "intersection": "intersection",
    "union": "set_operation",
    "difference": "set_operation",
}

# 多入边集合运算：evaluator_type → 集合操作函数（表驱动，无 if/elif 分派）
_SET_OP_FUNCS: Dict[str, Callable[[Set[str], Set[str]], Set[str]]] = {
    "intersection": lambda a, b: a & b,
    "union": lambda a, b: a | b,
    "difference": lambda a, b: a - b,
    "set_operation": lambda a, b: a & b,  # 默认交集
}


# ---------------------------------------------------------------------------
# PropagateSpec mode 表驱动分派（I17：消除 if/else，4 模式 → 2 策略组合）
# ---------------------------------------------------------------------------
# 每个模式分解为 (target_strategy, source_strategy) 二元组：
#   - target_strategy: 决定如何写入目标节点（merge 去重 / overwrite 清空）
#   - source_strategy: 决定是否删除源节点已转移股票（delete / keep）
# 消除 I16 之前 ``if spec.mode == "overwrite" or spec.clear_dest_first`` 双路径分派。


def _tgt_merge(state: PoolState, tid: str, transferred: List[Any], tgt_stocks: List[Any]) -> Tuple[List[str], List[str]]:
    """追加去重写入目标，返回 (新入池代码, 被清空代码)。

    merge 模式不清空目标，target_cleared 恒为空列表。
    """
    existing = {_stock_code(s) for s in tgt_stocks}
    new_stocks = [s for s in transferred if _stock_code(s) not in existing]
    state.get_pool(tid).add_stocks(new_stocks)
    return [_stock_code(s) for s in new_stocks], []


def _tgt_overwrite(state: PoolState, tid: str, transferred: List[Any], tgt_stocks: List[Any]) -> Tuple[List[str], List[str]]:
    """清空目标写入 transferred，返回 (新入池代码, 被覆盖出目标池代码)。

    I66：entered 语义统一 + tracker 保全。旧实现返回 ALL transferred codes，
    且全量替换（pool.remove+add_stocks）用 transferred 的 fresh _tracker（仅 entry_time）覆盖
    已持仓 stock 的完整 _tracker，导致 overwrite + multi-tick 三重 bug：
      1. BUY spam：_run_callback 对 ALL entered 发 BUY（已持仓重复）
      2. tracker 重置：_init_entry_trackers 对 ALL entered 重置 + 全量替换
         用 fresh _tracker 覆盖 → entry_price/entry_time 丢失 → profit_pct/hold_days
         恒 0、TTL 永不触发
      3. ENTER spam：_emit_transfer_events 对 ALL transferred_codes 发 ENTER
    修复：返回 NEW codes（与 _tgt_merge 同构），且对已持仓代码保全原 _tracker
    （未实际离池再入池）。entered 成为"新入池代码集"单一真相源。
    I69：同时返回 target_cleared（先前持有但不在本轮 transferred 中的代码），
    使 SnapshotBuilder view 能同步移除陈旧代码——旧实现 node_stocks 已 REPLACE
    但 Executed 事件不携带被覆盖代码，view 只 ADD 不 DISCARD → view drift。
    """
    existing_map = {_stock_code(s): s for s in tgt_stocks}
    transferred_codes = {_stock_code(s) for s in transferred}
    for s in transferred:
        old = existing_map.get(_stock_code(s))
        if old is not None and isinstance(old, dict) and isinstance(s, dict) and old.get("_tracker"):
            s["_tracker"] = old["_tracker"]
    pool = state.get_pool(tid)
    pool.remove_stocks(list(pool.get_stock_codes()))
    pool.add_stocks(transferred)
    entered = [_stock_code(s) for s in transferred if _stock_code(s) not in existing_map]
    target_cleared = [c for c in existing_map if c not in transferred_codes]
    return entered, target_cleared


def _src_delete(state: PoolState, sid: str, src_stocks: List[Any], passed_set: set) -> List[str]:
    """从源池删除已转移股票并标记脏。返回实际离开源池的代码列表。"""
    deleted = [_stock_code(s) for s in src_stocks if _stock_code(s) in passed_set]
    state.get_pool(sid).remove_stocks(list(passed_set))
    state.mark_node_dirty(sid)
    return deleted


def _src_keep(state: PoolState, sid: str, src_stocks: List[Any], passed_set: set) -> List[str]:
    """保留源池不变（no-op）。返回空列表（无股票离开源池）。"""
    return []


# mode → (target_strategy, source_strategy)（表驱动，无 if/elif 分派）。
# target_strategy 返回 (entered, target_cleared) 二元组；source_strategy 返回 exited 代码。
# I21：source_strategy 返回值取代 run() 中 source_before/after 双 get_stocks diff。
# I69：target_strategy 返回值扩展为 (entered, target_cleared)，使 Executed 事件携带
# 被覆盖出目标池的代码，修复 SnapshotBuilder view drift。
_PROPAGATE_STRATEGIES: Dict[str, Tuple[Callable[..., List[str]], Callable[..., List[str]]]] = {
    "copy": (_tgt_merge, _src_keep),
    "move": (_tgt_merge, _src_delete),
    "overwrite": (_tgt_overwrite, _src_delete),
    "overwrite_copy": (_tgt_overwrite, _src_keep),
}


class TickTable:
    """tick 表视图：latest_tick + prev_tick 双 dict。

    I24：激活 ``_latest_tick``（I13 引入后一直是死属性）——新增 ``column`` 与
    ``bar_hash``，使 EdgeExecutor 数据读取统一收敛到 TickTable，不再绕过视图
    直接访问 ``state.latest_tick``。
    """

    def __init__(self, latest_tick: dict[str, dict[str, float]], prev_tick: dict[str, dict[str, float]]):
        self._latest_tick = latest_tick
        self._prev_tick = prev_tick

    def column(self, code: str, col: str) -> float | None:
        """返回当前周期 col 列值；缺失返回 None。"""
        return self._latest_tick.get(code, {}).get(col)

    def prev_column(self, code: str, col: str) -> float | None:
        """返回上一周期 col 列值；缺失返回 None。"""
        return self._prev_tick.get(code, {}).get(col)

    def bar_hash(self) -> str:
        """返回 latest_tick 顶层 ``_hash``（缓存键）；缺失返回空串。"""
        return self._latest_tick.get("_hash", "")


class EdgeExecutor:
    """执行单条边：gate → filter → propagate → callback → ttl。

    属性（实例级，≤ 5）:
      - state: PoolState
      - schedule: CompiledSchedule
      - formula_engine: FormulaEngineProtocol
      - bus: Optional[EventBus]

    方法（≤ 6）:
      - __init__
      - run
      - _gate
      - _filter
      - _propagate
    """

    def __init__(
        self,
        state: PoolState,
        schedule: CompiledSchedule,
        formula_engine: FormulaEngineProtocol,
        event_bus: Optional[EventBus] = None,
        event_driver: Optional[Any] = None,
        *,
        scalar_nset_fn: Optional[Callable] = None,
        eval_ctx_factory: Optional[Callable] = None,
        live_ctx_fn: Optional[Callable] = None,
    ) -> None:
        self.state = state
        self.schedule = schedule
        self.formula_engine = formula_engine
        self.bus = event_bus
        self.event_driver = event_driver  # I4：用于注册 TTL 到时事件
        # 依赖注入容器：避免 execution_module 跨模块 import 公式/选股模块。
        self._eval_deps = _FilterEvalDeps(
            scalar_nset_fn=scalar_nset_fn,
            eval_ctx_factory=eval_ctx_factory,
            live_ctx_fn=live_ctx_fn,
            bus=self.bus,
        )
        # I13：TickTable 实时绑定 state.latest_tick / state.prev_tick（不再空 dict）。
        # DataUpdater._apply_code_tick 推进前快照 prev_tick，使 cross 模式 prev_column 真实可用。
        self._tick_table = TickTable(state.latest_tick, state.prev_tick)
        # SubTask 21.3: EdgeExecutor 订阅 EdgeFired 事件执行
        # EdgeFired 由 ExecutionModule（_on_stock_filtered / _run_tick）发布，
        # 订阅后 EdgeExecutor 经事件触发 run(eid)，消除 ExecutionModule 直接调用。
        if self.bus is not None:
            self.bus.subscribe(EdgeFired, self._on_edge_fired)

    def _on_edge_fired(self, event: EdgeFired) -> None:
        """EdgeFired 事件 handler — 条件节点激活模型（Task 8）+ 非 condition 回退（G3）。

        EdgeFired 只携带 eid+ts（G3）。定位 eid 目标节点：
        - 条件节点：调用 _activate_condition（SubTask 8.1-8.6 完整流程）
        - 非条件节点：回退到 run()（gate→filter→propagate，保留原逻辑）
        """
        ec = self.schedule.edge_ctx.get(event.eid)
        if ec is None:
            return
        # Task 8 SubTask 8.1：条件节点激活分支
        if self._is_condition_node(ec.tid):
            self._activate_condition(event.eid)
            return
        # 非条件节点：保留原 run() 路径（G3 从源池 StatePoolView 取脏股票）
        source_pool = self.state.get_pool(ec.sid)
        dirty_codes = source_pool.get_dirty_codes()
        # first_run 兜底：脏股票为空且首次运行时，用源池全量股票
        if not dirty_codes and getattr(self.state, "first_run", False):
            dirty_codes = set(_stock_code(s) for s in source_pool.get_stocks() if isinstance(s, dict))
        self.run(event.eid, changed_codes=list(dirty_codes) if dirty_codes else None)

    def run(self, eid: str, changed_codes: Optional[List[str]] = None) -> bool:
        """执行单条边：按 CompiledSchedule.steps 表驱动循环执行。

        步骤序列由编译期从 edge_strategies.json:steps 读取，运行期按表循环。
        新增步骤 = 加 JSON 条目 + 实现 EdgeStep，零行 run 改动。

        changed_codes: 本 tick 有数据变化的股票代码集合。筛选器对这些股票
        重新评估公式，其余股票使用 filter_inputs 中的缓存结果。首次执行
        （changed_codes=None）时对所有源池股票全量评估。
        """
        ec = self.schedule.edge_ctx.get(eid)
        if ec is None:
            logger.warning("EdgeExecutor.run: 未知边 eid=%s", eid)
            return False

        timing_spec = self.schedule.edge_timing_spec.get(eid)
        filter_spec = self.schedule.edge_filter_spec.get(eid)
        propagate_spec = self.schedule.edge_propagate_spec.get(eid)
        action_spec = self.schedule.edge_action_spec.get(eid)
        ttl_spec = self.schedule.edge_ttl_spec.get(eid)

        source_codes = [_stock_code(s) for s in self.state.get_pool(ec.sid).get_stocks()]

        ctx = {
            "eid": eid,
            "ec": ec,
            "timing_spec": timing_spec,
            "filter_spec": filter_spec,
            "propagate_spec": propagate_spec,
            "action_spec": action_spec,
            "ttl_spec": ttl_spec,
            "changed_codes": changed_codes,
            "source_codes": source_codes,
        }

        steps = self.schedule.steps or [
            {"step_name": "gate"},
            {"step_name": "filter"},
            {"step_name": "propagate"},
            {"step_name": "ttl"},
            {"step_name": "callback"},
        ]

        for step_spec in steps:
            step_name = step_spec.get("step_name", "") if isinstance(step_spec, dict) else getattr(step_spec, "step_name", "")
            step_factory = STEP_REGISTRY.get(step_name)
            if step_factory is None:
                logger.warning("EdgeExecutor.run: 未知步骤 %s", step_name)
                continue
            step = step_factory(self)
            result = step.run(ctx)
            if not result.should_continue:
                break

        return True

    def _gate(self, spec: Optional[TimingSpec], eid: str) -> bool:
        """时机门控：基于 ``TimingSpec`` 与 ``state.exec_ctx`` 判断是否允许执行。

        I19：starttype + cxtype 双表驱动（_STARTTYPE_GATE_HANDLERS + _CXTYPE_POST_GATES），
        消除 cxtype if/elif + duration_sec 隐式分派；duration 检查仅对 cxtype=1 生效。
        I42：双时间值一次性计算——now_unix 服务 elapsed/cxtype/interval（Unix 算术），
        now_sec 服务 5 个市场时间 gate（秒数比较）。消除 virtual 模式下
        offset→anchor→Unix→datetime.fromtimestamp→秒数往返（_current_seconds_of_day 反向解）。
        """
        if spec is None:
            return True

        now_unix = _now_ts(self.state)
        now_sec = _current_seconds_of_day(time_at(state=self.state))
        exec_ctx = self.state.get_exec_ctx(eid)

        # starttype 门控（0-7）表驱动
        if not _starttype_gate(spec, self.state, eid, now_unix, now_sec):
            exec_ctx["fired"] = False
            return False

        # cxtype 后置门控（0=一直, 1=持续窗口, 2=只一次）表驱动
        post_gate = _CXTYPE_POST_GATES.get(spec.cxtype, _cxtype_forever)
        if not post_gate(spec, exec_ctx, now_unix):
            exec_ctx["fired"] = False
            return False

        # 触发间隔（与 cxtype 正交）
        if spec.interval_sec > 0:
            last_fire = exec_ctx.get("last_fire")
            if last_fire is not None and now_unix - last_fire < spec.interval_sec:
                return False

        return True

    def _filter(
        self, spec: Optional[FilterSpec], codes: List[str], eid: str = "",
        changed_codes: Optional[List[str]] = None,
    ) -> Tuple[List[str], List[str]]:
        """强弱筛选：返回 passed / rejected 代码列表（批量增量）。

        changed_codes: 本 tick 有数据变化的股票集合。
          - None（首次/全量）：对所有 codes 全量评估。
          - 空集合：沿用上一次缓存，不重新评估（缓存命中则直接返回）。
          - 非空：仅对 changed_codes & codes 重新评估，其余沿用缓存。

        增量合并公式：passed_set = (cached_passed - changed_set) | newly_passed
        结果存储在 state.filter_inputs[eid] 为 frozenset。
        """
        codes_set = set(codes)
        cached_passed = self.state.filter_inputs.get(eid) if eid else None

        if spec is None:
            if eid:
                self.state.filter_inputs[eid] = frozenset(codes)
            return list(codes), []

        if changed_codes is None:
            eval_codes = list(codes)
            prev_passed: Set[str] = set()
        elif not changed_codes:
            if cached_passed is not None:
                passed_set = set(cached_passed) & codes_set
                passed = [c for c in codes if c in passed_set]
                rejected = [c for c in codes if c not in passed_set]
                return passed, rejected
            eval_codes = list(codes)
            prev_passed = set()
        else:
            changed_set = set(changed_codes) & codes_set
            if cached_passed is not None:
                prev_passed = set(cached_passed) - changed_set
                eval_codes = list(changed_set)
            else:
                eval_codes = list(codes)
                prev_passed = set()

        if not eval_codes:
            passed_set = prev_passed & codes_set
            passed = [c for c in codes if c in passed_set]
            rejected = [c for c in codes if c not in passed_set]
            if eid:
                self.state.filter_inputs[eid] = frozenset(passed_set)
            return passed, rejected

        handler = _FILTER_EVALUATORS.get(spec.evaluator_type, _eval_pass_through)
        newly_passed = handler(
            self.state, self.schedule, self.formula_engine,
            self._tick_table, spec, eval_codes, eid,
            eval_deps=self._eval_deps,
        )
        passed_set = (prev_passed | set(newly_passed)) & codes_set

        if eid:
            self.state.filter_inputs[eid] = frozenset(passed_set)

        passed = [c for c in codes if c in passed_set]
        rejected = [c for c in codes if c not in passed_set]
        return passed, rejected


    def _propagate(
        self,
        spec: Optional[PropagateSpec],
        sid: str,
        tid: str,
        passed: List[str],
    ) -> Tuple[List[str], List[str], List[str]]:
        """状态流转：copy / move / overwrite / overwrite_copy。

        返回 ``(entered, exited, target_cleared)`` 三元组：
          - entered: 实际进入目标节点的代码（target_strategy 返回值）
          - exited:  实际离开源节点的代码（source_strategy 返回值）
          - target_cleared: 被覆盖出目标节点的代码（仅 overwrite 模式非空）

        I17：``spec.mode`` 分派改为 ``_PROPAGATE_STRATEGIES`` dict 表驱动
        （消除 ``if spec.mode == "overwrite" or spec.clear_dest_first`` 双路径）。
        4 模式分解为 (target_strategy, source_strategy) 二元组，运行期只查表。
        I21：source_strategy 返回 exited 列表，取代 run() 中 source_before/after
        双 ``get_stocks`` diff——消除 2 次冗余读取，propagate 契约完备
        （同时知道 entered 与 exited 两个方向的状态变更）。
        I69：target_strategy 返回 (entered, target_cleared) 二元组，使 Executed
        事件携带三个方向的完整状态变更——entered/exited/target_cleared。
        """
        if spec is None:
            spec = PropagateSpec()

        passed_set = set(passed)
        src_stocks = self.state.get_pool(sid).get_stocks()
        tgt_stocks = self.state.get_pool(tid).get_stocks()

        now_ts = _now_ts(self.state)
        transferred = []
        for s in src_stocks:
            if _stock_code(s) not in passed_set:
                continue
            ns = copy.deepcopy(s)
            if isinstance(ns, dict) and not ns.get("_tracker"):
                ns["_tracker"] = {"entry_time": now_ts}
            transferred.append(ns)

        tgt_strategy, src_strategy = _PROPAGATE_STRATEGIES.get(
            spec.mode, (_tgt_merge, _src_keep)
        )
        entered, target_cleared = tgt_strategy(self.state, tid, transferred, tgt_stocks)
        exited = src_strategy(self.state, sid, src_stocks, passed_set)

        # G1 heapq：TTL 惰性删除——股票出池后 TTL 到时 action 检测 pool 后自动跳过，
        # 无需主动 unregister（heapq 不支持高效随机删除）。

        self.state.mark_node_dirty(tid)
        return entered, exited, target_cleared

    # ------------------------------------------------------------------
    # Task 8：条件节点激活模型（SubTask 8.1-8.6）
    # ------------------------------------------------------------------

    def _is_condition_node(self, nid: str) -> bool:
        """SubTask 8.1：判断节点是否为条件节点。

        表驱动查 _CONDITION_NODE_TYPES，覆盖 DZH/TDX 标准类型 + 实例 JSON "condition" 简写。
        """
        node_type = self.schedule.node_types.get(nid, "")
        return node_type in _CONDITION_NODE_TYPES

    def _build_cond_filter_spec(self, cond_params: Dict[str, Any]) -> FilterSpec:
        """SubTask 8.2/8.6：从条件节点 params 合成运行期 FilterSpec。

        条件节点承载计算参数（func/indi/indiparam/filter_spec），本方法在激活时
        合成 FilterSpec，不修改编译期 schedule.edge_filter_spec。

        公式/筛选分离（SubTask 8.6）：
        - FilterSpec 携带 formula_ref/formula_period/formula_args 供公式引擎.eval_series
          计算（公式 = 添加列）
        - noperate/nset 供 _eval_op 按 _NOPERATE_RULES prev_expr/curr_expr 比较
          （筛选 = 列操作），无 cross 函数，金叉通过 noperate=3 实现

        JSON evaluator_type 映射（_COND_EVALUATOR_TYPE_MAP）：
        - "indicator" → "formula"（HQChartPy2 公式路径）
        - "intersection" → "intersection"（多入边集合运算）
        - "union"/"difference" → "set_operation"
        """
        fs_dict = cond_params.get("filter_spec") or {}
        if not isinstance(fs_dict, dict):
            fs_dict = {}
        json_eval_type = str(fs_dict.get("evaluator_type", "")).strip()
        runtime_eval_type = _COND_EVALUATOR_TYPE_MAP.get(
            json_eval_type, json_eval_type or "pass_through"
        )

        func = cond_params.get("func") if isinstance(cond_params.get("func"), dict) else {}
        indi = str(cond_params.get("indi", "")).strip()
        indiparam = (
            cond_params.get("indiparam")
            if isinstance(cond_params.get("indiparam"), list)
            else []
        )

        # 复用 _build_tdx_func_from_panel 合成 tdx_func（func + indi + indiparam）
        tdx_func = _build_tdx_func_from_panel(cond_params) or dict(func)

        formula_ref = (
            str(tdx_func.get("accode", "")).strip()
            or indi
            or str(fs_dict.get("formula_ref", "")).strip()
        )
        formula_period = _nperiod_to_period(tdx_func.get("nperiod"))
        formula_args = tdx_func.get("formula_args") or {}
        noperate = int(tdx_func.get("noperate", fs_dict.get("noperate", 0)) or 0)
        nset = int(tdx_func.get("nset", fs_dict.get("nset", 0)) or 0)
        fsecond = float(fs_dict.get("fsecond", 0) or 0)

        # nfirst/cfirst/nsecond/csecond 从 func 读取（指标线索引/名称）
        nfirst = int(tdx_func.get("nfirst", 0) or 0)
        cfirst = str(tdx_func.get("cfirst", "") or "").strip()
        nsecond_raw = tdx_func.get("nsecond")
        nsecond = int(nsecond_raw) if nsecond_raw is not None else -1
        csecond = str(tdx_func.get("csecond", "") or "").strip()

        return FilterSpec(
            filter_type="formula" if runtime_eval_type == "formula" else runtime_eval_type,
            formula_ref=formula_ref,
            formula_period=formula_period,
            threshold=fsecond,
            noperate=noperate,
            evaluator_type=runtime_eval_type,
            nset=nset,
            accode=formula_ref,
            nperiod=int(tdx_func.get("nperiod", 0) or 0),
            nfirst=nfirst,
            cfirst=cfirst,
            nsecond=nsecond,
            csecond=csecond,
            formula_args=formula_args,
            func=func,
            indi=indi,
            indiparam=indiparam,
        )

    def _collect_in_edges_ordered(self, cond_nid: str) -> List[EdgeContext]:
        """SubTask 8.2：收集条件节点的所有入边，按 _order 排序。

        入边 = edge_ctx 中 tid == cond_nid 的边。
        顺序号从 edge_index[eid].params._order 读取（G6 保留边顺序号用于交集/差集运算次序）。
        """
        in_edges = [ec for ec in self.schedule.edge_ctx.values() if ec.tid == cond_nid]

        def _order_key(ec: EdgeContext) -> int:
            edge_dict = self.schedule.edge_index.get(ec.eid, {})
            params = edge_dict.get("params", {}) if isinstance(edge_dict, dict) else {}
            return int(params.get("_order", 0) or 0)

        return sorted(in_edges, key=_order_key)

    def _apply_set_operation(
        self, port_results: Dict[int, List[str]], eval_type: str
    ) -> List[str]:
        """SubTask 8.3：多入边集合运算。

        表驱动查 _SET_OP_FUNCS，无 if/elif 分派：
        - 单入边：直接输出
        - 多入边：按 eval_type 做交集/差集/并集（默认交集）
        """
        if not port_results:
            return []
        if len(port_results) == 1:
            return list(next(iter(port_results.values())))
        op_fn = _SET_OP_FUNCS.get(eval_type, _SET_OP_FUNCS["intersection"])
        result: Optional[Set[str]] = None
        for order in sorted(port_results.keys()):
            codes = set(port_results[order])
            result = codes if result is None else op_fn(result, codes)
        return list(result) if result else []

    def _transfer_to_target(
        self,
        out_edge: EdgeContext,
        cond_nid: str,
        passed: List[str],
    ) -> Tuple[List[str], List[str]]:
        """SubTask 8.4/8.5：通过出边输出到目标池 + TTL 注册 + 事件链。

        - add_stocks + 标脏（StatePoolView.add_stocks 内部调 add_changed_codes）
        - 注册 per-code TTL 一次性定时器到 heapq（G1 统一队列）
        - 发布 Executed + TransferExecuted（TradeModule 订阅后触发买入链：
          TransferExecuted → Signal(buy,100) → OrderPlaced → OrderFilled → PositionUpdated）

        条件节点不持有股票，transferred 从 passed codes 构造 minimal stock dict。
        """
        if not passed:
            return [], []
        tgt = out_edge.tid
        tgt_pool = self.state.get_pool(tgt)
        now_ts = _now_ts(self.state)

        # 构造 transferred stocks（条件节点无股票池，用 passed codes 构造 minimal dict）
        transferred = []
        for code in passed:
            ns = {"code": code}
            ns["_tracker"] = {"entry_time": now_ts}
            transferred.append(ns)

        # 复用 _tgt_merge（copy 模式）：追加去重写入目标池 + 标脏
        tgt_stocks = tgt_pool.get_stocks()
        entered, _target_cleared = _tgt_merge(self.state, tgt, transferred, tgt_stocks)
        exited: List[str] = []  # 条件节点不持有股票，无 exited

        # 注册 per-code TTL 一次性定时器（SubTask 8.4）
        ttl_spec = self.schedule.node_ttl_spec.get(tgt)
        if (
            entered
            and self.event_driver is not None
            and ttl_spec is not None
            and ttl_spec.bdel == 1
            and ttl_spec.check_type == "interval"
            and ttl_spec.ttl_sec > 0
        ):
            node_ttl_eid = f"node_ttl:{tgt}"
            for code in entered:
                register_ttl_spec(
                    self.event_driver, self.state, tgt,
                    node_ttl_eid, code, ttl_spec.ttl_sec, now_ts, self.bus,
                )

        # tracker 初始化（记录 entry_price 等，供卖出时计算 profit_pct/hold_days）
        if entered:
            _init_entry_trackers(
                self.state, tgt, entered, now_ts, out_edge.eid, self._tick_table,
                ttl_spec=ttl_spec, event_driver=self.event_driver, bus=self.bus,
            )

        # 发布 Executed + TransferExecuted（SubTask 8.5：C 池入池触发买入链）
        if self.bus is not None and (entered or exited):
            propagate_mode = "copy"
            _publish(self.bus, Executed(
                eid=out_edge.eid,
                sid=cond_nid,
                tid=tgt,
                entered=list(entered),
                exited=exited,
                target_cleared=[],
                mode=propagate_mode,
                details={"timestamp": now_ts},
            ))
            _publish(self.bus, TransferExecuted(
                src=cond_nid,
                tgt=tgt,
                codes=list(entered) if entered else [],
                mode=propagate_mode,
                ts=now_ts,
                entered_codes=list(entered) if entered else [],
                exited_codes=exited,
            ))
        return entered, exited

    def _activate_condition(self, eid: str) -> bool:
        """SubTask 8.1-8.6：条件节点激活主流程。

        EdgeFired(eid) → 定位条件节点 → 收集所有入边（按 _order 排序）
        → 每条入边取源池脏股票 + 公式计算 + 筛选 → port_results[order]
        → 集合运算（单入边直接输出，多入边交集/差集/并集）
        → 出边输出到目标池 + TTL + 事件链

        公式/筛选分离（SubTask 8.6）：
        - 公式 = 添加列（公式引擎.eval_series 写入 series_results）
        - 筛选 = 列操作（_eval_op 按 noperate 规则做 prev_expr/curr_expr 比较，无 cross）
        - 增量评估：仅对 dirty_codes 重新评估，合并规则 passed = (cached - dirty) | newly_passed
        """
        ec = self.schedule.edge_ctx.get(eid)
        if ec is None:
            return False
        cond_nid = ec.tid
        if not self._is_condition_node(cond_nid):
            return False

        cond_node = self.schedule.nodes.get(cond_nid, {})
        cond_params = cond_node.get("params", {}) if isinstance(cond_node, dict) else {}
        filter_spec = self._build_cond_filter_spec(cond_params)

        # SubTask 8.2：收集所有入边按 _order 排序
        in_edges = self._collect_in_edges_ordered(cond_nid)
        if not in_edges:
            return False

        port_results: Dict[int, List[str]] = {}
        for in_edge in in_edges:
            source_pool = self.state.get_pool(in_edge.sid)
            dirty_codes = source_pool.get_dirty_codes()
            source_codes = [
                _stock_code(s) for s in source_pool.get_stocks() if isinstance(s, dict)
            ]
            # first_run 兜底：脏股票为空且首次运行时，用源池全量股票
            if not dirty_codes and getattr(self.state, "first_run", False):
                dirty_codes = set(source_codes)
            # 提前计算 order，供空源池分支记录占位空列表
            edge_dict = self.schedule.edge_index.get(in_edge.eid, {})
            params = edge_dict.get("params", {}) if isinstance(edge_dict, dict) else {}
            order = int(params.get("_order", 0) or 0)
            if not source_codes:
                # 交集运算中，空源池应记录为空列表而非跳过，否则
                # _apply_set_operation 会因 len(port_results)==1 退化为
                # 单入边直接返回（数学定义 A∩∅=∅，原代码退化为 A∩∅=B）
                if filter_spec.evaluator_type == "intersection":
                    port_results[order] = []
                continue

            if filter_spec.evaluator_type == "intersection":
                # SubTask 8.3 交集条件：port_results 记录源池当前股票全集，集合运算在后面做
                # 用 source_codes（当前股票全集）而非 dirty_codes，否则不同池的脏股票
                # 通常无交集，恒为空，无法发现"当前同时在两个池中"的股票。
                passed_list = list(source_codes)
            else:
                # SubTask 8.2/8.6 公式条件：_filter 内部调公式引擎.eval_series
                # （公式 = 添加列）+ _eval_op（筛选 = 列操作），增量评估 dirty_codes
                changed = list(dirty_codes) if dirty_codes else None
                passed_list, _rejected = self._filter(
                    filter_spec, source_codes, eid=in_edge.eid,
                    changed_codes=changed,
                )
                # 发布 FormulaEvaluated → StockFiltered（与 run() 方法保持一致，
                # 使条件节点激活路径也产生公式评估与筛选事件）
                if self.bus is not None:
                    formula_ref = getattr(filter_spec, 'formula_ref', '')
                    if formula_ref:
                        all_evaluated = list(passed_list) + list(_rejected)
                        for code in all_evaluated:
                            result = code in passed_list
                            _publish(self.bus, FormulaEvaluated(
                                formula_ref=formula_ref,
                                result=result,
                                code=code,
                                bar_hash="",
                            ))
                    _publish(self.bus, StockFiltered(
                        eid=in_edge.eid,
                        passed=list(passed_list),
                        rejected=list(_rejected),
                        filter_ref=getattr(filter_spec, 'formula_ref', ''),
                        ts=_now_ts(self.state),
                    ))

            # order 已在循环开头提前计算
            port_results[order] = passed_list

        # SubTask 8.3：集合运算
        final_passed = self._apply_set_operation(port_results, filter_spec.evaluator_type)
        if not final_passed:
            return True  # 触发成功但无股票通过

        # SubTask 8.4：通过出边输出到目标池
        out_edges = [e for e in self.schedule.edge_ctx.values() if e.sid == cond_nid]
        for out_edge in out_edges:
            self._transfer_to_target(out_edge, cond_nid, final_passed)
        return True


# ===========================================================================
# 边执行步骤（EdgeExecutor 表驱动步骤化）
# ===========================================================================
# 每个 Step 类持有 executor 引用，委托现有 _gate/_filter/_propagate 方法及
# 模块级辅助函数。STEP_REGISTRY 编译期由 edge_strategies.json:steps 驱动。


class GateStep:
    """步骤1：时机门控。"""
    def __init__(self, executor):
        self._executor = executor

    def run(self, ctx):
        eid = ctx["eid"]
        timing_spec = ctx.get("timing_spec")
        if not self._executor._gate(timing_spec, eid):
            return StepResult(should_continue=False)
        self._executor.state.set_exec_ctx_fired(eid, now=_now_ts(self._executor.state))
        return StepResult(should_continue=True)


class FilterStep:
    """步骤2：强弱筛选 + 发布 FormulaEvaluated/StockFiltered。"""
    def __init__(self, executor):
        self._executor = executor

    def run(self, ctx):
        eid = ctx["eid"]
        ec = ctx["ec"]
        filter_spec = ctx.get("filter_spec")
        changed_codes = ctx.get("changed_codes")
        source_codes = ctx.get("source_codes")
        passed, rejected = self._executor._filter(filter_spec, source_codes, ec.eid, changed_codes=changed_codes)
        ctx["passed"] = passed
        ctx["rejected"] = rejected
        # 发布 FormulaEvaluated + StockFiltered
        if self._executor.bus is not None and filter_spec is not None:
            formula_ref = getattr(filter_spec, 'formula_ref', '')
            if formula_ref:
                all_evaluated = list(passed) + list(rejected)
                for code in all_evaluated:
                    result = code in passed
                    _publish(self._executor.bus, FormulaEvaluated(
                        formula_ref=formula_ref, result=result, code=code, bar_hash="",
                    ))
            _publish(self._executor.bus, StockFiltered(
                eid=ec.eid, passed=list(passed), rejected=list(rejected),
                filter_ref=getattr(filter_spec, 'formula_ref', ''), ts=_now_ts(self._executor.state),
            ))
        return StepResult(should_continue=True)


class PropagateStep:
    """步骤3：状态流转 + 发布 Executed/TransferExecuted。"""
    def __init__(self, executor):
        self._executor = executor

    def run(self, ctx):
        ec = ctx["ec"]
        propagate_spec = ctx.get("propagate_spec")
        passed = ctx.get("passed", [])
        entered, exited, target_cleared = self._executor._propagate(propagate_spec, ec.sid, ec.tid, passed)
        ctx["entered"] = entered
        ctx["exited"] = exited
        ctx["target_cleared"] = target_cleared
        ctx["propagate_mode"] = propagate_spec.mode if propagate_spec else "copy"
        return StepResult(should_continue=True)


class TTLStep:
    """步骤4：TTL 注册。"""
    def __init__(self, executor):
        self._executor = executor

    def run(self, ctx):
        eid = ctx["eid"]
        ec = ctx["ec"]
        entered = ctx.get("entered", [])
        ttl_spec = ctx.get("ttl_spec")
        ts = _now_ts(self._executor.state)
        if entered:
            prices = _init_entry_trackers(
                self._executor.state, ec.tid, entered, ts, ec.eid,
                self._executor._tick_table,
                ttl_spec=ttl_spec, event_driver=self._executor.event_driver, bus=self._executor.bus,
            )
            ctx["prices"] = prices
            if self._executor.event_driver is not None:
                node_ttl = self._executor.schedule.node_ttl_spec.get(ec.tid)
                if node_ttl is not None and node_ttl.bdel == 1 and node_ttl.check_type == "interval" and node_ttl.ttl_sec > 0:
                    node_ttl_eid = f"node_ttl:{ec.tid}"
                    for code in entered:
                        register_ttl_spec(self._executor.event_driver, self._executor.state, ec.tid, node_ttl_eid, code, node_ttl.ttl_sec, ts, self._executor.bus)
        else:
            ctx["prices"] = {}
        ctx["ts"] = ts
        return StepResult(should_continue=True)


class CallbackStep:
    """步骤5：回调执行。"""
    def __init__(self, executor):
        self._executor = executor

    def run(self, ctx):
        ec = ctx["ec"]
        action_spec = ctx.get("action_spec")
        entered = ctx.get("entered", [])
        ts = ctx.get("ts", _now_ts(self._executor.state))
        prices = ctx.get("prices", {})
        # 发布 Executed/TransferExecuted
        if self._executor.bus is not None:
            details = {
                "actions": list(action_spec.target_pool_actions) if action_spec else [],
                "prices": dict(prices),
                "timestamp": ts,
            } if entered else None
            _publish(self._executor.bus, Executed(
                eid=ec.eid, sid=ec.sid, tid=ec.tid,
                entered=list(entered), exited=ctx.get("exited", []),
                target_cleared=ctx.get("target_cleared", []),
                mode=ctx.get("propagate_mode", "copy"), details=details,
            ))
            if entered or ctx.get("exited"):
                _publish(self._executor.bus, TransferExecuted(
                    src=ec.sid, tgt=ec.tid,
                    codes=list(entered) if entered else [], mode=ctx.get("propagate_mode", "copy"), ts=ts,
                    entered_codes=list(entered) if entered else [],
                    exited_codes=list(ctx.get("exited", [])) if ctx.get("exited") else [],
                ))
        _run_callback(self._executor.state, ec, action_spec, ec.tid, entered, ts, prices, self._executor.bus)
        return StepResult(should_continue=True)


STEP_REGISTRY = {
    "gate": lambda executor: GateStep(executor),
    "filter": lambda executor: FilterStep(executor),
    "propagate": lambda executor: PropagateStep(executor),
    "ttl": lambda executor: TTLStep(executor),
    "callback": lambda executor: CallbackStep(executor),
}


# ===========================================================================
# 统一时间驱动（G1 heapq）：所有到时事件注册到 EventDriver 单一 heapq
# ===========================================================================


def _make_edge_action(bus: Any, eid: str, state: Any) -> Callable[..., None]:
    """构造边触发的 action：只发布 EdgeFired 事件（G2/G3 只携带 eid+ts）。

    G1 heapq 驱动：定时器到时→action 发布 EdgeFired + fire_due 立即注册下次。
    EdgeExecutor 订阅 EdgeFired 后执行 gate→filter→propagate→callback。
    脏股票由 EdgeExecutor._on_edge_fired 从源池 StatePoolView.get_dirty_codes() 取，
    action 不再计算/携带 changed_codes，也不调用 edge_executor.run()。

    fire_time 由 EventDriver.fire_due 注入（spec 在 heapq 中实际到期的时刻），
    使 EdgeFired.ts 反映真实触发顺序，避免同一仿真步内所有边触发共享
    self.clock 导致前端时间轴堆叠为一条线。
    """

    def action(params: Any, fire_time: Optional[float] = None) -> None:
        # G2：action 只发布 EdgeFired 事件，不执行计算
        # fire_time 优先（来自 heapq 弹出的精确时刻），None 时退回 time_at(state)
        ts = fire_time if fire_time is not None else time_at(state=state)
        _publish_edge_fired(bus, eid, ts)

    return action


def _make_ttl_interval_action(state: Any, tgt: str, eid: str, ttl_sec: float, bus: Any) -> Callable[..., None]:
    """构造 TTL interval 类型的 action（G1 per-code 一次性触发，G2 只发事件）。

    每只股票入池时注册独立的 TimedEventSpec（interval=None 一次性），
    到时 action 从 params 读取 code，发布 TTLDue(node_id=tgt, code=code, ts)。
    不执行删除/卖出逻辑（由 TradeModule 订阅 TTLDue 后自行完成）。
    若股票已出池（惰性删除），action 检测后跳过。

    Args:
        state:   PoolState 实例
        tgt:     目标池 ID
        eid:     边/流程 ID
        ttl_sec: TTL 间隔秒数（用于事件 details）
        bus:     EventBus 实例

    fire_time 由 EventDriver.fire_due 注入，使 TTLDue.ts 反映真实触发时刻，
    避免 self.clock 在仿真步内为常量导致前端时间轴堆叠。
    """

    def action(params: Any, fire_time: Optional[float] = None) -> None:
        code = params.get("code")
        if not code:
            return
        pool = state.get_pool(tgt)
        stocks = pool.get_stocks()
        # 惰性删除：若股票已出池则跳过（由 move 边传播移除）
        code_in_pool = False
        for s in stocks:
            if isinstance(s, dict) and _stock_code(s) == code:
                code_in_pool = True
                break
        if not code_in_pool:
            return
        # G2：action 只发布 TTLDue 事件，不执行删除/卖出逻辑
        # fire_time 优先（来自 heapq 弹出的精确时刻），None 时退回 time_at(state)
        now_val = fire_time if fire_time is not None else time_at(state=state)
        _publish_ttl_due(bus, tgt, code, now_val)

    return action


def _make_ttl_endtime_action(state: Any, ttl_spec: "TTLSpec", tgt: str, bus: Any, eid: str) -> Callable[..., None]:
    """构造 TTL endtime 类型的 action：扫描 hold 超时股票 → 发布 TTLDue（G2 只发事件）。

    endtime 模式在时钟到达 endtime_sec 时触发，检查 hold_for_ttl 过滤超时股票。
    这不是轮询——是时钟驱动的单次/周期触发。
    不执行删除/卖出逻辑（由 TradeModule 订阅 TTLDue 后自行完成）。

    SubTask 27.4：``_stock_code`` / ``_stock_entry_time`` / ``_now_ts`` /
    ``_current_seconds_of_day`` / ``TTLDue`` / ``time_at`` 已随相关源文件
    一并迁移至本模块，原动态 import 链移除，直接使用本地名称。

    fire_time 由 EventDriver.fire_due 注入，使 TTLDue.ts 反映真实触发时刻。
    endtime 模式下 fire_time 应为 endtime_sec 当日秒数；hold_for_ttl 比较仍用
    now_unix（实盘 Unix 秒），因为 entry_ts 也是 Unix 秒——两套坐标系独立。
    """

    def action(params: Any, fire_time: Optional[float] = None) -> None:
        now_unix = _now_ts(state)
        now_sec_of_day = _current_seconds_of_day(time_at(state=state))
        if now_sec_of_day < ttl_spec.endtime_sec:
            return
        # fire_time 优先（来自 heapq 弹出的精确时刻），None 时退回 now_unix
        event_ts = fire_time if fire_time is not None else now_unix
        expired_codes: List[str] = []
        stocks = state.get_pool(tgt).get_stocks()
        for stock in stocks:
            should_expire = False
            if ttl_spec.hold_for_ttl > 0:
                entry_ts = _stock_entry_time(stock)
                if entry_ts is not None and (now_unix - entry_ts) >= ttl_spec.hold_for_ttl:
                    should_expire = True
            else:
                should_expire = True
            if should_expire:
                code = _stock_code(stock)
                expired_codes.append(code)
        # G2：action 只发布 TTLDue 事件，不执行删除/卖出逻辑
        for code in expired_codes:
            _publish_ttl_due(bus, tgt, code, event_ts)

    return action


def _state_now(state: Any) -> float:
    """从 state 读取当前时间戳（三模式统一入口）。"""
    return time_at(state=state)


def _compute_endtime_fire_time(state: Any, endtime_sec: int) -> float:
    """计算 endtime TTL 的首次触发时间。

    virtual 模式下 ``time_at`` 返回当日秒数偏移，直接与 endtime_sec 比较；
    wall_clock 模式下为 Unix 时间戳，需转换。
    """
    now = time_at(state=state)
    now_sec = _current_seconds_of_day(now)
    if now_sec >= endtime_sec:
        return now  # 已过 endtime，立即触发
    if is_offset_of_day(now):
        return float(endtime_sec)  # virtual 模式：endtime_sec 即当日秒数
    return now + (endtime_sec - now_sec)  # wall_clock：加上剩余秒数


def register_ttl_spec(
    event_driver: Any,
    state: Any,
    tgt: str,
    eid: str,
    code: str,
    ttl_sec: float,
    entry_ts: float,
    bus: Any = None,
) -> None:
    """注册 per-code TTL 一次性定时器到 heapq（G1 统一队列）。

    股票入池时调用：创建 TimedEventSpec（interval=None 一次性），
    first_fire_time = entry_ts + ttl_sec，到时 action 发布 TTLDue(node_id=tgt, code=code, ts)。
    删除/卖出逻辑由 TradeModule 订阅 TTLDue 后自行完成。
    """
    action = _make_ttl_interval_action(state, tgt, eid, ttl_sec, bus)
    spec = TimedEventSpec(
        action=action,
        params={"kind": "ttl", "eid": eid, "tgt": tgt, "code": code, "check_type": "interval"},
        interval=None,  # 一次性
    )
    event_driver.add_spec(spec, first_fire_time=entry_ts + ttl_sec)


def build_timed_event_specs(
    schedule: "CompiledSchedule",
    state: Any,
    engine: Any,
    edge_executor: Any,
    event_driver: Any = None,
    bus: Any = None,
) -> None:
    """编译期注册所有周期性 TimedEventSpec 到 heapq（G1 统一队列）。

    G1 heapq 驱动：
      - 边触发：interval=timing.interval_sec，first_fire_time=now+interval，
        到时发布 EdgeFired + 立即注册下次（fire_time + interval）
      - TTL interval：不在编译期注册（per-code，运行期入池时通过 register_ttl_spec 注册）
      - TTL endtime：interval=None（一次性），first_fire_time=计算得到的 endtime 时刻，
        到时发布 TTLDue

    SubTask 27.4：``TimedEventSpec`` 已随 ``time_util.py`` 一并迁移至本模块。
    """
    source_ids = schedule.source_node_ids
    if bus is None:
        bus = getattr(edge_executor, "bus", None)
    if event_driver is None:
        return
    now = time_at(state=state)

    # G6：运行时事件无序，不存在 execution_order 拓扑排序。
    # 每条边的触发定时器独立注册到 heapq 优先队列，由 fire_time 决定触发先后。
    for eid, ec in schedule.edge_ctx.items():
        if ec is None:
            continue

        # 边触发 TimedEventSpec（interval > 0 才注册周期定时器）
        timing = schedule.edge_timing_spec.get(eid)
        if timing is not None and timing.interval_sec > 0:
            edge_action = _make_edge_action(bus, eid, state)
            spec = TimedEventSpec(
                action=edge_action,
                params={"kind": "edge", "eid": eid, "sid": ec.sid, "tid": ec.tid},
                interval=float(timing.interval_sec),
                end_fn=None,
            )
            event_driver.add_spec(spec, first_fire_time=now + timing.interval_sec)

        # TTL endtime TimedEventSpec（一次性，编译期注册）
        ttl = schedule.edge_ttl_spec.get(eid)
        if ttl is not None and ttl.bdel == 1 and ttl.check_type == "endtime" and ttl.endtime_sec > 0:
            ttl_action = _make_ttl_endtime_action(state, ttl, ec.tid, bus, eid)
            spec = TimedEventSpec(
                action=ttl_action,
                params={"kind": "ttl", "eid": eid, "tgt": ec.tid, "check_type": "endtime"},
                interval=None,
                end_fn=None,
            )
            event_driver.add_spec(spec, first_fire_time=_compute_endtime_fire_time(state, ttl.endtime_sec))

    # 无入边节点的 TTL endtime spec（如预填股票的状态池）
    for nid, ttl in schedule.node_ttl_spec.items():
        if ttl.bdel == 1 and ttl.check_type == "endtime" and ttl.endtime_sec > 0:
            ttl_action = _make_ttl_endtime_action(state, ttl, nid, bus, f"node_ttl:{nid}")
            spec = TimedEventSpec(
                action=ttl_action,
                params={"kind": "ttl", "eid": f"node_ttl:{nid}", "tgt": nid, "check_type": "endtime"},
                interval=None,
                end_fn=None,
            )
            event_driver.add_spec(spec, first_fire_time=_compute_endtime_fire_time(state, ttl.endtime_sec))


# ===========================================================================
# TTL 兼容入口（SubTask 27.1 从 ttl_helper.py 迁入）
# ===========================================================================


def _do_ttl_check(state: PoolState, ttl_spec: Any, tgt: str, bus: Any = None, eid: str = "") -> List[str]:
    """TTL 检查（兼容入口）：按 check_type 分派，删除超时股票，发布 SELL Signal + TIMEOUT。

    从 ``core/ttl_helper.py`` 迁移至 ``execution_module.py``（SubTask 27.1）。
    SubTask 27.4：``_now_ts`` / ``_current_seconds_of_day`` / ``time_at`` /
    ``_stock_entry_time`` / ``_stock_code`` 已随 ``edge_executor.py`` /
    ``time_util.py`` 一并迁移至本模块，直接使用本地名称。
    
    修复：添加 SELL Signal 发布，价格从 latest_tick 获取。
    """
    if ttl_spec.bdel != 1:
        return []

    now_unix = _now_ts(state)
    now_sec_of_day = _current_seconds_of_day(time_at(state=state))

    removed: List[str] = []
    removed_prices: Dict[str, float] = {}
    kept: List[Any] = []
    latest_tick = getattr(state, "latest_tick", {}) or {}

    if ttl_spec.check_type == "interval" and ttl_spec.ttl_sec > 0:
        for stock in state.get_pool(tgt).get_stocks():
            entry_ts = _stock_entry_time(stock)
            if entry_ts is not None and (now_unix - entry_ts) >= ttl_spec.ttl_sec:
                code = _stock_code(stock)
                removed.append(code)
                tick_data = latest_tick.get(code, {})
                tick_price = 0.0
                if isinstance(tick_data, dict):
                    tick_price = float(tick_data.get("close", tick_data.get("price", 0.0)) or 0.0)
                if tick_price > 0:
                    removed_prices[code] = tick_price
                else:
                    tr = stock.get("_tracker") if isinstance(stock, dict) else None
                    if isinstance(tr, dict):
                        removed_prices[code] = float(
                            tr.get("current_price", tr.get("entry_price", 0))
                        )
                continue
            kept.append(stock)
    elif ttl_spec.check_type == "endtime":
        if now_sec_of_day < ttl_spec.endtime_sec:
            return []
        for stock in state.get_pool(tgt).get_stocks():
            should_remove = False
            if ttl_spec.hold_for_ttl > 0:
                entry_ts = _stock_entry_time(stock)
                if entry_ts is not None and (now_unix - entry_ts) >= ttl_spec.hold_for_ttl:
                    should_remove = True
            else:
                should_remove = True
            if should_remove:
                code = _stock_code(stock)
                removed.append(code)
                tick_data = latest_tick.get(code, {})
                tick_price = 0.0
                if isinstance(tick_data, dict):
                    tick_price = float(tick_data.get("close", tick_data.get("price", 0.0)) or 0.0)
                if tick_price > 0:
                    removed_prices[code] = tick_price
                else:
                    tr = stock.get("_tracker") if isinstance(stock, dict) else None
                    if isinstance(tr, dict):
                        removed_prices[code] = float(
                            tr.get("current_price", tr.get("entry_price", 0))
                        )
            else:
                kept.append(stock)
    else:
        return []

    if removed:
        state.get_pool(tgt).remove_stocks(removed)
        state.mark_node_dirty(tgt)
        logger.info("TTL expire: removed %s from %s (check=%s)",
                    removed, tgt, ttl_spec.check_type)
        if bus is not None:
            for code in removed:
                bus.publish(DomainEvent(
                    event_type="TIMEOUT",
                    code=code,
                    pool_id=tgt,
                    details={"reason": "TTL_EXPIRED", "flow_id": eid, "ttl_sec": ttl_spec.ttl_sec, "timestamp": now_unix},
                ))
    return removed


class TTLHelper:
    """TTL 兼容入口：apply_ttl 供 simtests 调用。

    从 ``core/ttl_helper.py`` 迁移至 ``execution_module.py``（SubTask 27.1）。
    """

    def __init__(self, psatt_cfg: Dict[str, Any] = None, defaults: Dict[str, Any] = None,
                 now_fn: Callable[[], Any] = None,
                 pool_state_cls: Optional[Type[Any]] = None):
        self._psatt_cfg = psatt_cfg or {}
        self._defaults = defaults or {}
        self._now = now_fn
        # 依赖注入：PoolState 类由 engine.py 组装层注入，避免本模块跨模块 import
        # runtime_mode_module（满足模块零引用约束，与 FormulaEngineProtocol 同一模式）。
        self._pool_state_cls = pool_state_cls

    def apply_ttl(self, node_id: str, node: Any, node_stocks: Dict[str, list],
                  bus: Any = None, eid: str = "") -> None:
        """对指定状态池节点执行 TTL 过期淘汰。"""
        ttl_spec = _build_ttl_spec(node_id, {node_id: node})
        if ttl_spec.bdel != 1 or ttl_spec.check_type == "none":
            return

        if self._pool_state_cls is None:
            raise RuntimeError(
                "TTLHelper.apply_ttl 需要 pool_state_cls 依赖注入"
                "（由 core/engine.py 组装层经构造函数注入 PoolState）"
            )
        state = self._pool_state_cls({"nodes": [], "edges": []})
        if self._now is not None:
            try:
                ts = _safe_timestamp(self._now())
                state.time_source = {
                    "driver_type": "wall_clock",
                    "current_ts": ts,
                    "kind": "ttl_helper",
                }
            except Exception:
                state.time_source = {"driver_type": "wall_clock", "current_ts": 0.0}
        pool = state.get_pool(node_id)
        pool.remove_stocks(list(pool.get_stock_codes()))
        pool.add_stocks(list(node_stocks.get(node_id, [])))

        _do_ttl_check(state, ttl_spec, node_id, bus=bus, eid=eid)

        node_stocks[node_id] = list(state.get_pool(node_id).get_stocks())


# ===========================================================================
# ExecutionModule — 对外统一入口
# ===========================================================================


class ExecutionModule:
    """Execution 模块：编译 + 核心循环 + 边执行 + 时序驱动。仅与 EventBus 交互。

    订阅 StockFiltered / DataChanged / TimeAdvanced 事件，
    执行 gate→filter→propagate→callback→ttl 流水线，
    发布 EdgeFired / TransferExecuted / TTLExpired / Signal 事件。

    内部持有原 4 个组件实例（Compiler / PoolEngine / EdgeExecutor / EventDriver），
    通过可选的 ``PoolEngine`` 注入实现惰性创建；外部只能通过 EventBus 与之交互。
    """

    def __init__(
        self,
        bus: EventBus,
        config: Optional[Dict[str, Any]] = None,
        *,
        meta_engine: Optional[Any] = None,
        pool_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._bus = bus
        self._config = config or {}
        self._meta_engine = meta_engine
        self._pool_config: Dict[str, Any] = pool_config or {}
        self._compiled: Optional[CompiledSchedule] = None
        self._engine: Optional[Any] = None  # PoolEngine 实例，惰性创建
        # 缓存 ScreeningModule 发布的筛选结果，供边执行读取
        self._filter_results: Dict[str, Tuple[List[str], List[str]]] = {}
        # SubTask 19.5: 已由 StockFiltered 触发 EdgeFired 的边集合，用于去重
        # （防止 _run_tick 重复发布同一边的 EdgeFired）
        self._fired_edges: set = set()
        # 时间源（由 ModeChanged 事件切换）：
        #   "live"        -> wall_clock（time.time()）
        #   "replay"      -> sequence（按 ReplayStep 推进）
        #   "simulation"  -> virtual（按 SimulationStep 推进）
        self._time_source: str = "wall_clock"
        # 注册事件订阅
        self._register_subscribers()
        # 若提供了 pool_config，立即编译
        if pool_config:
            try:
                self._compiled = Compiler.compile(pool_config)
            except Exception as ex:
                logger.warning("ExecutionModule 初始编译失败: %s", ex)

    # ------------------------------------------------------------------
    # 订阅注册
    # ------------------------------------------------------------------
    def _register_subscribers(self) -> None:
        """注册事件订阅：上游模块事件 + 内部事件转发。"""
        self._bus.subscribe(StockFiltered, self._on_stock_filtered)
        self._bus.subscribe(DataChanged, self._on_data_changed)
        self._bus.subscribe(TimeAdvanced, self._on_time_advanced)
        self._bus.subscribe(ConfigChanged, self._on_config_changed)
        self._bus.subscribe(PoolLoaded, self._on_pool_loaded)
        # 订阅 EdgeExecutor 发布的内部事件，转发为外部事件契约
        self._bus.subscribe(Executed, self._on_executed)
        self._bus.subscribe(DomainEvent, self._on_domain_event)
        # SubTask 20.2：订阅 ModeChanged 切换时间源
        self._bus.subscribe(ModeChanged, self._on_mode_changed)

    # ------------------------------------------------------------------
    # SubTask 20.2：ModeChanged → 切换时间源
    # ------------------------------------------------------------------
    @_event_handler("_on_mode_changed")
    def _on_mode_changed(self, event: ModeChanged) -> None:
        """模式切换时切换内部时间源。

        时间源影响 TTL 计算 / 时序 gate 判定使用的时间基准：
          - ``live``:        ``wall_clock``（使用 ``time.time()``）
          - ``replay``:      ``sequence``（按 ``ReplayStep`` 推进，使用 step.ts）
          - ``simulation``:  ``virtual``（按 ``SimulationStep`` 推进，使用
                              step.virtual_ts）

        实现：仅记录 ``self._time_source``，由 ``_check_ttl_expired`` /
        ``_run_tick`` 等方法读取并使用对应时间戳。当前时间戳已由事件
        payload 携带（``DataChanged.ts`` / ``TimeAdvanced.ts``），模式切换
        仅切换标记，下游读取时按 ``_time_source`` 选择时间基准来源。
        """
        mode_id = event.mode_id or "live"
        mapping = {
            "live": "wall_clock",
            "replay": "sequence",
            "simulation": "virtual",
        }
        new_source = mapping.get(mode_id, "wall_clock")
        prev = self._time_source
        self._time_source = new_source
        logger.info(
            "ExecutionModule 时间源切换: %s -> %s（mode=%s）",
            prev, new_source, mode_id,
        )

    # ------------------------------------------------------------------
    # 组件访问（惰性创建）
    # ------------------------------------------------------------------
    def _ensure_engine(self) -> Optional[Any]:
        """惰性创建/复用 PoolEngine 实例，返回其引用。

        通过 ``PoolEngine._ensure_pool_engine`` 复用现有创建逻辑，避免重复
        PoolEngine.__init__ 中的组件装配（EventBus / DataUpdater / BarComposer /
        TradeExecutor / EventPanel / 公式引擎 / EdgeExecutor / EventDriver）。
        """
        if self._engine is not None:
            return self._engine
        if self._meta_engine is None or not self._pool_config:
            return None
        try:
            self._engine = self._meta_engine._ensure_pool_engine(self._pool_config)
        except Exception as ex:
            logger.warning("ExecutionModule 创建 PoolEngine 失败: %s", ex)
            return None
        return self._engine

    def _get_edge_executor(self) -> Optional[Any]:
        """获取 EdgeExecutor 组件实例。"""
        pe = self._ensure_engine()
        if pe is None:
            return None
        return pe._components.get("edge_executor")

    def _get_event_driver(self) -> Optional[Any]:
        """获取 EventDriver 组件实例。"""
        pe = self._ensure_engine()
        if pe is None:
            return None
        return pe._components.get("event_driver")

    # ------------------------------------------------------------------
    # 事件 handler
    # ------------------------------------------------------------------
    @_event_handler("_on_pool_loaded")
    def _on_pool_loaded(self, event: PoolLoaded) -> None:
        """池配置加载触发编译（SubTask 8.4 关联）。"""
        self._pool_config = event.pool_config or {}
        self._compiled = Compiler.compile(self._pool_config)
        self._engine = None  # 重置，下次 _ensure_engine 重建

    @_event_handler("_on_config_changed")
    def _on_config_changed(self, event: ConfigChanged) -> None:
        """配置变更触发 CompiledSchedule 重建（SubTask 8.4）。"""
        if self._pool_config:
            self._compiled = Compiler.compile(self._pool_config)
            self._engine = None  # 配置变更后重建引擎

    @_event_handler("_on_stock_filtered")
    def _on_stock_filtered(self, event: StockFiltered) -> None:
        """筛选结果写入边 filter_inputs，供边执行读取（SubTask 8.2）。

        SubTask 19.5: StockFiltered → EdgeFired
        筛选结果缓存后立即发布 EdgeFired 事件，使边触发由筛选结果驱动
        （而非仅由 DataChanged 驱动）。使用 ``_fired_edges`` 集合去重，
        避免与 ``_run_tick`` 的 fallback 发布重复。

        注意：新 EventDriver 架构下（event_driver 已创建），边级联由
        driver.fire_due() 统一驱动，此处理器仅缓存筛选结果，不再重新发布
        EdgeFired，避免重复触发和 eid 不一致。
        """
        new_arch = self._is_new_arch_active()
        pe = None
        if not new_arch:
            pe = self._ensure_engine()
        else:
            for eng in (self._engine, self._meta_engine):
                if eng is not None:
                    pe = eng
                    break
        if pe is not None:
            pe.state.filter_inputs[event.eid] = frozenset(event.passed)
            logger.debug(
                "ExecutionModule._on_stock_filtered eid=%s passed=%d",
                event.eid, len(event.passed),
            )
        self._filter_results[event.eid] = (list(event.passed), list(event.rejected))
        if not new_arch and event.eid not in self._fired_edges:
            self._fired_edges.add(event.eid)
            _publish_edge_fired(self._bus, event.eid, event.ts or time.time())

    def _is_new_arch_active(self) -> bool:
        """检测新 EventDriver 架构是否已激活（通过 meta_engine 或 self._engine 判断）。"""
        for eng in (self._engine, self._meta_engine):
            if eng is not None:
                components = getattr(eng, '_components', None)
                if components is not None and components.get("event_driver") is not None:
                    return True
        return False

    @_event_handler("_on_data_changed")
    def _on_data_changed(self, event: DataChanged) -> None:
        """数据变更触发核心 tick 执行（SubTask 8.2）。

        当引擎已启用新 EventDriver 架构（_components 含 event_driver）时，
        边触发由 ``_run_tick_body → driver.fire_due()`` 统一驱动，此处直接返回
        以避免双重执行和 eid 不一致问题。仅在旧版 run_pool 路径（无 event_driver）
        下才执行本模块的 _run_tick。
        """
        if self._compiled is None:
            return
        if self._is_new_arch_active():
            return
        self._run_tick(event)

    @_event_handler("_on_time_advanced")
    def _on_time_advanced(self, event: TimeAdvanced) -> None:
        """时间推进触发 TTL 检查（SubTask 8.2）。"""
        self._check_ttl_expired(event.ts)

    @_event_handler("_on_executed")
    def _on_executed(self, event: Executed) -> None:
        """EdgeExecutor 发布 Executed → 转发为 TransferExecuted（SubTask 8.3）。

        ``_emit_transfer_events`` 的事件化版本：原 ``PoolEngine._emit_transfer_events``
        在 tick 末尾批量处理 transfer_events，现改为 per-Executed 即时转发，
        由 Statistics/Monitoring 模块订阅 TransferExecuted 处理。
        """
        if event.entered or event.exited:
            self._bus.publish(TransferExecuted(
                src=event.sid,
                tgt=event.tid,
                codes=list(event.entered),
                mode=event.mode,
                ts=time.time(),
            ))

    @_event_handler("_on_domain_event")
    def _on_domain_event(self, event: DomainEvent) -> None:
        """DomainEvent(TIMEOUT) → TTLExpired（SubTask 8.3）。

        TTL 到期由 ``EventDriver.fire_due`` 触发（G1 heapq 弹出），内部 action 发布
        DomainEvent(TIMEOUT) + Signal(SELL)。此处转发为 TTLExpired 事件，
        供 Database/Monitoring 模块订阅。其他 DomainEvent 类型（ENTER/EXIT/
        RANK_CHANGED）不经此转发，保留由原 _on_domain_event 订阅者处理。
        """
        if event.event_type != "TIMEOUT":
            return
        self._bus.publish(TTLExpired(
            node_id=event.pool_id,
            codes=[event.code],
            ts=time.time(),
        ))

    # ------------------------------------------------------------------
    # 核心循环
    # ------------------------------------------------------------------
    def _run_tick(self, event: DataChanged) -> None:
        """执行核心 tick：fire_due 统一驱动到时事件（G2 引擎只发事件）。

        G2 重构：引擎只发事件不执行计算。边触发由 ``EventDriver.fire_due`` → action
        发布 ``EdgeFired`` → ``EdgeExecutor._on_edge_fired`` 订阅触发 ``run(eid)``
        自行完成 gate→filter→propagate→callback。本方法不遍历边列表，
        由 heapq 优先队列按 fire_time 独立触发各边（G6 运行时事件无序）。

        TTL action 发布 DomainEvent(TIMEOUT)，由 ``_on_domain_event`` 转发为
        ``TTLExpired`` 事件，由 ``TradeModule`` 订阅后自行完成卖出。
        """
        pe = self._ensure_engine()
        if pe is None:
            return
        if self._compiled is None:
            return
        # tick 末尾触发到时事件检查（EventDriver G1 heapq 统一驱动）
        event_driver = self._get_event_driver()
        if event_driver is not None:
            try:
                event_driver.fire_due(event.ts)
            except Exception as ex:
                logger.warning("ExecutionModule fire_due 失败: %s", ex)
        # 清理本 tick 的去重集合，为下一 tick 准备
        self._fired_edges.clear()

    def _should_fire(self, eid: str, pe: Any) -> bool:
        """判定边是否应触发：源节点 dirty + timing gate 放行。

        判定逻辑与 ``compiler._make_edge_action`` 中的 trigger 检查一致：
        源节点被标脏（dirty.nodes）或源节点为 source 节点且数据已更新（dirty.data）。
        对于交集边（intersection），intersection_source 节点被标脏也会触发。
        """
        if self._compiled is None:
            return False
        ec = self._compiled.edge_ctx.get(eid)
        if ec is None:
            return False
        state = pe.state
        dirty = state.dirty
        source_ids = self._compiled.source_node_ids
        trigger = dirty.nodes.get(ec.sid) or (dirty.data and ec.sid in source_ids)
        if not trigger:
            fspec = self._compiled.edge_filter_spec.get(eid)
            if fspec is not None and fspec.evaluator_type == "intersection":
                inter_src = (fspec.evaluator_params or {}).get("intersection_source", "")
                if inter_src and dirty.nodes.get(inter_src):
                    trigger = True
        if not trigger:
            return False
        edge_executor = self._get_edge_executor()
        if edge_executor is None:
            return False
        timing = self._compiled.edge_timing_spec.get(eid)
        if timing is None:
            return True
        try:
            return edge_executor._gate(timing, eid)
        except Exception:
            return False

    def _check_ttl_expired(self, ts: float) -> None:
        """TTL 过期检查：委托 EventDriver.fire_due 统一驱动（G1 heapq）。

        ``EventDriver`` 内部 action 会发布 DomainEvent(TIMEOUT) + Signal(SELL)，
        由 ``_on_domain_event`` 订阅者转发为 ``TTLExpired`` 事件。
        """
        event_driver = self._get_event_driver()
        if event_driver is None:
            return
        try:
            event_driver.fire_due(ts)
        except Exception as ex:
            logger.warning("ExecutionModule TTL fire_due 失败: %s", ex)


__all__ = [
    # 时序工具（time_util）
    "time_at", "time_now_unix", "is_offset_of_day", "anchor_to_today",
    "TimedEventSpec", "EventDriver",
    # 边状态（edge_state）
    "EdgeState", "EdgeStateMixin",
    # 编译产物（compiler）
    "CompiledSchedule", "Compiler", "EdgeContext", "TimingSpec",
    "FilterSpec", "PropagateSpec", "ActionSpec", "TTLSpec",
    "build_timed_event_specs", "register_ttl_spec",
    # 边执行器（edge_executor）
    "EdgeExecutor", "TickTable",
    # TTL 兼容入口
    "TTLHelper", "_do_ttl_check",
    # 对外统一入口
    "ExecutionModule",
    # Task 4：编译-运行分离
    "CompiledPool", "compile",
    # Task 6/7/8: table-driven three elements
    "trigger_check", "filter_eval", "propagate_apply",
]


# ---------------------------------------------------------------------------
# Task 4 内联测试：验证 CompiledPool 编译产物结构正确性
# 运行方式：python -m core.execution_module（需在项目根目录，依赖包上下文）
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    _minimal_pool = {
        "nodes": [
            {"id": "n1", "legacy_type": 202, "type": "candidate_pool", "text": "备选池"},
            {"id": "n2", "legacy_type": 201, "type": "condition", "text": "条件节点"},
            {"id": "n3", "legacy_type": 203, "type": "target", "text": "目标池"},
        ],
        "edges": [
            {
                "id": "e1", "from": "n1", "to": "n2",
                "params": {"_order": 1, "starttype": 2, "nset": 1, "formula_ref": "MA"},
            },
            {
                "id": "e2", "from": "n2", "to": "n3",
                "params": {"_order": 2, "mode": "copy"},
            },
        ],
    }

    _cp = compile(_minimal_pool)
    assert set(_cp.nodes.keys()) == {"n1", "n2", "n3"}, f"nodes mismatch: {_cp.nodes.keys()}"
    assert set(_cp.edges.keys()) == {"e1", "e2"}, f"edges mismatch: {_cp.edges.keys()}"
    assert _cp.edge_endpoints["e1"] == ("n1", "n2"), f"endpoints e1: {_cp.edge_endpoints['e1']}"
    assert _cp.edge_endpoints["e2"] == ("n2", "n3"), f"endpoints e2: {_cp.edge_endpoints['e2']}"
    assert _cp.edge_order == ["e1", "e2"], f"edge_order: {_cp.edge_order}"
    assert _cp.edge_type["e1"] == "conditional", f"e1 type: {_cp.edge_type['e1']}"
    assert _cp.edge_type["e2"] == "unconditional", f"e2 type: {_cp.edge_type['e2']}"
    assert _cp.out_edges["n1"] == ["e1"], f"out_edges n1: {_cp.out_edges['n1']}"
    assert _cp.in_edges["n3"] == ["e2"], f"in_edges n3: {_cp.in_edges['n3']}"
    assert _cp.source_nodes == ["n1"], f"source_nodes: {_cp.source_nodes}"
    assert _cp.node_role["n1"] == "candidate", f"role n1: {_cp.node_role['n1']}"
    assert _cp.node_role["n2"] == "condition", f"role n2: {_cp.node_role['n2']}"
    assert _cp.node_role["n3"] == "target", f"role n3: {_cp.node_role['n3']}"
    assert _cp.edge_timing_spec["e1"]["starttype"] == 2, f"timing e1: {_cp.edge_timing_spec['e1']}"
    assert _cp.edge_filter_spec["e1"]["formula_ref"] == "MA", f"filter e1: {_cp.edge_filter_spec['e1']}"
    assert _cp.edge_propagate_spec["e2"]["mode"] == "copy", f"propagate e2: {_cp.edge_propagate_spec['e2']}"
    print("CompiledPool inline test PASSED")
