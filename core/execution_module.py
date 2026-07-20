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
  - ``core/time_util.py``     → time_at / EventDriver / TtlTracker / TimedEventSpec
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
from typing import Any, Callable, Dict, Iterable, List, Literal, Optional, Set, Tuple, TYPE_CHECKING

from pydantic import BaseModel, Field

from .event_bus import (
    ConfigChanged,
    DataChanged,
    DomainEvent,
    EdgeFired,
    EventBus,
    Executed,
    ModeChanged,
    PoolLoaded,
    Signal,
    StockFiltered,
    TimeAdvanced,
    TransferExecuted,
    TTLExpired,
)
from .screening_module import (
    _NOPERATE_RULES,
    _RANK_MODES,
    _lookup_builtin_formula_info,
    _nperiod_to_period,
    _resolve_rank,
    _scalar_compare,
    eval_scalar_nset,
    evaluate_intersection,
)
from .formula_module import EvalContext, FormulaEngine, live_context
from .domain import (
    _stock_code,
    _hms_to_seconds,
    time_at,
    _safe_timestamp,
    is_offset_of_day,
    anchor_to_today,
    time_now_unix,
)

if TYPE_CHECKING:
    from .runtime_mode_module import PoolState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 配置表惰性加载与缓存（模块级，避免每次编译重复读文件）
# ---------------------------------------------------------------------------

_CONFIG_DIR = Path(__file__).parent.parent / "config"
_CONFIG_CACHE: Dict[str, Any] = {}


def _load_config(name: str) -> Dict[str, Any]:
    """加载 config/ 下的 JSON 配置表，缺失或解析失败时返回空字典。

    SubTask 27.14: 配置文件按模块分类到 architecture/data/runtime/ui/pools/
    子目录后，按文件名递归查找（跳过 _archived/）。
    """
    if name in _CONFIG_CACHE:
        return _CONFIG_CACHE[name]
    path = _CONFIG_DIR / name
    if not path.exists():
        # SubTask 27.14: 递归查找子目录（与 table_engine._find_table_path 一致）
        for candidate in _CONFIG_DIR.rglob(name):
            if "_archived" not in candidate.parts:
                path = candidate
                break
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        data = {}
    _CONFIG_CACHE[name] = data
    return data


# ===========================================================================
# 时序工具（原 core/time_util.py）
# ===========================================================================
# 三模式时间架构（state.time_source["driver_type"]）：
#   - wall_clock：实盘模式，由 run_tick 写入 current_ts（= _now().timestamp()）
#   - sequence：回放模式，由 ReplayRunner 写入 K 线时间戳
#   - virtual：仿真模式，由 Simulator 写入虚拟时钟
#
# 统一时间驱动：所有到时事件统一为 TimedEventSpec：
#   - at_fn() 返回到期时间（Unix 秒），<= now 表示到期
#   - action(params) 到期时调用，发布事件参数不同，引发的下个事件不同
#   - 边触发：action 发布 Executed → 订阅者执行 filter→propagate→callback
#   - TTL到期：action 发布 DomainEvent(TIMEOUT) → 订阅者执行批量删除
#
# TtlTracker 是单条边的 TTL 追踪器（面向对象），仅管理到期时间堆，
# 不发布事件——发布是 action 的职责，不是 tracker 的职责。
#
# EventDriver.fire_due(now) 统一扫描所有 TimedEventSpec，
# at_fn() <= now 就调 action——边触发和 TTL 完全同一套机制。


# ---------------------------------------------------------------------------
# TtlEntry + TtlTracker（面向对象：仅管理到期时间堆，不发布事件）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TtlEntry:
    """TTL 到期条目（值对象）：一只股票在特定目标池中的到期记录。

    入池时创建，入堆排序；出池时惰性删除（从 _entries 移除，堆弹出时跳过）。
    pop_expired 返回到期条目列表，由 action 消费并发布 DomainEvent(TIMEOUT)。
    """
    code: str
    tgt: str
    eid: str
    ttl_sec: float
    entry_ts: float
    expire_at: float

    def __lt__(self, other: "TtlEntry") -> bool:
        return self.expire_at < other.expire_at


class TtlTracker:
    """单条边的 TTL 追踪器（面向对象，仅管理到期时间堆）。

    职责单一：register / unregister / next_expire_at / pop_expired / clear。
    不发布事件——发布是 TimedEventSpec.action 的职责。

    next_expire_at() 使 TTL 的 at_fn 与边触发共用 ``at_fn() <= now`` 语义。
    """

    def __init__(self, tgt: str, eid: str) -> None:
        self._tgt = tgt
        self._eid = eid
        self._heap: List[TtlEntry] = []
        self._entries: Dict[str, TtlEntry] = {}

    @property
    def tgt(self) -> str:
        return self._tgt

    @property
    def eid(self) -> str:
        return self._eid

    def register(self, code: str, ttl_sec: float, entry_ts: float, now_unix: float) -> None:
        """股票入池时注册到期条目。expire_at = entry_ts + ttl_sec。

        已过期（expire_at <= now_unix）仍入堆，fire_due 时立即弹出。
        """
        if ttl_sec <= 0:
            return
        expire_at = entry_ts + ttl_sec
        entry = TtlEntry(
            code=code, tgt=self._tgt, eid=self._eid,
            ttl_sec=ttl_sec, entry_ts=entry_ts, expire_at=expire_at,
        )
        self._entries[code] = entry
        heapq.heappush(self._heap, entry)

    def unregister(self, code: str) -> None:
        """股票出池时取消注册（惰性删除）。"""
        self._entries.pop(code, None)

    def next_expire_at(self) -> float:
        """返回堆顶到期时间。空堆返回 inf（永不到期）。"""
        while self._heap:
            top = self._heap[0]
            if top.code in self._entries:
                return top.expire_at
            heapq.heappop(self._heap)
        return float("inf")

    def pop_expired(self, now_unix: float) -> List[TtlEntry]:
        """弹出所有到期条目（expire_at <= now_unix），跳过已取消的。"""
        expired: List[TtlEntry] = []
        while self._heap and self._heap[0].expire_at <= now_unix:
            entry = heapq.heappop(self._heap)
            if entry.code in self._entries:
                del self._entries[entry.code]
                expired.append(entry)
        return expired

    def clear(self) -> None:
        """清空所有追踪。"""
        self._heap.clear()
        self._entries.clear()


# ---------------------------------------------------------------------------
# TimedEventSpec（统一到时事件规格）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TimedEventSpec:
    """到时事件规格表行——边触发与 TTL 共用。

    到时触发是到时触发，执行事件是执行事件。
    所有到时事件统一为 TimedEventSpec，区别仅在 params 不同、引发的下个事件不同：
      - 边触发：action 发布 Executed → 订阅者执行 filter→propagate→callback
      - TTL到期：action 发布 DomainEvent(TIMEOUT) → 订阅者执行批量删除

    Attributes:
        at_fn:   计算下次触发时间（Unix 秒）。<= now 表示到期。
        interval: 触发间隔（秒）。None=一次性事件。
        end_fn:  计算结束时间（Unix 秒）。None=永久。
        action:  事件回调，签名为 ``action(params)``。
        params:  事件参数字典。
    """

    at_fn: Callable[[], float]
    interval: Optional[float]
    end_fn: Optional[Callable[[], float]]
    action: Callable[[Any], None]
    params: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# EventDriver — 统一时间驱动器
# ---------------------------------------------------------------------------


class EventDriver:
    """统一时间驱动器：所有 TimedEventSpec 共用 at_fn() <= now 到期判定。

    fire_due(now) 统一扫描所有 spec，at_fn() <= now 就调 action。
    边触发和 TTL 完全同一套机制——区别仅在 action 发布的事件不同。

    TtlTracker 仅供 TTL 类型的 at_fn 委托 next_expire_at()，
    以及运行期 register/unregister 到期条目。
    """

    def __init__(self, state: Any = None, bus: Any = None) -> None:
        self._state = state
        self._bus = bus
        self._specs: List[TimedEventSpec] = []
        self._ttl_trackers: Dict[str, TtlTracker] = {}

    def add_spec(self, spec: TimedEventSpec) -> None:
        """注册到时事件规格（边触发和 TTL 统一入口）。"""
        self._specs.append(spec)

    def add_ttl_tracker(self, eid: str, tracker: TtlTracker) -> None:
        """注册 TTL 追踪器（interval 类型，运行期 register/unregister）。"""
        self._ttl_trackers[eid] = tracker

    def register_ttl(self, eid: str, code: str, ttl_sec: float, entry_ts: float, now_unix: float) -> None:
        """运行期：股票入池时注册 TTL 到期。"""
        tracker = self._ttl_trackers.get(eid)
        if tracker is not None:
            tracker.register(code, ttl_sec, entry_ts, now_unix)

    def unregister_ttl(self, eid: str, code: str) -> None:
        """运行期：股票出池时取消 TTL 到期。"""
        tracker = self._ttl_trackers.get(eid)
        if tracker is not None:
            tracker.unregister(code)

    def is_edge_due(self, eid: str, now: float) -> bool:
        """边触发到期判定（兼容旧接口，tick body 中使用）。"""
        for spec in self._specs:
            if spec.params.get("eid") == eid:
                return spec.at_fn() <= now
        return True

    def fire_due(self, now: float) -> None:
        """统一到期触发：遍历所有 spec，at_fn() <= now 就调 action。

        边触发和 TTL 完全同一套机制——at_fn 判定到期，action 发布事件，
        订阅者执行具体逻辑。区别仅在 params 不同、引发的下个事件不同。
        """
        for spec in self._specs:
            try:
                if spec.at_fn() <= now:
                    spec.action(spec.params)
            except Exception:
                logger.warning("TimedEventSpec action 异常", exc_info=True)

    def fire_ttl_due(self, now: float) -> None:
        """TTL 到期触发（兼容旧接口，仅处理 TTL 类型 spec）。"""
        for spec in self._specs:
            if spec.params.get("kind") != "ttl":
                continue
            try:
                if spec.at_fn() <= now:
                    spec.action(spec.params)
            except Exception:
                logger.warning("TTL spec action 异常", exc_info=True)

    def clear_ttl(self) -> None:
        """清空所有 TTL 追踪器。"""
        for tracker in self._ttl_trackers.values():
            tracker.clear()


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
# 零生产读取。``edge_fired`` 被 engine.py 写入（is_edge_due 结果）但 L322
# 读局部变量；``exec_ctx.fired`` 被 set_exec_ctx_fired 写入但无消费方。
# edge_fired 非非 exec_ctx.fired 的视图（语义不同：前者为当前 tick 时间
# 门控结果，后者为边是否曾执行过），原 L7 注释错误。


class EdgeStateMixin:
    """EdgeState 表级访问方法集合。

    将公式结果缓存与过滤输入指纹的读写从 ``EdgeState`` 核心类中剥离，
    使其属性/方法数满足架构约束。
    """

    # ------------------------------------------------------------------
    # formula_results（公式级结果缓存，亦称 filter_cache）
    # ------------------------------------------------------------------
    def get_formula_result(self, formula_ref: Any, bar_hash: str) -> Any:
        return self.formula_results.get((formula_ref, bar_hash))

    def set_formula_result(self, formula_ref: Any, bar_hash: str, result: Any) -> None:
        self.formula_results[(formula_ref, bar_hash)] = result

    # ------------------------------------------------------------------
    # filter_inputs
    # ------------------------------------------------------------------
    def set_filter_input(self, eid: str, codes: Iterable[str]) -> None:
        self.filter_inputs[eid] = frozenset(codes)

    def get_filter_input(self, eid: str) -> Optional[frozenset]:
        return self.filter_inputs.get(eid)


@dataclass
class EdgeState(EdgeStateMixin):
    """边级运行时表真相源。

    属性（按架构 ≤5 个）：
      - exec_ctx
      - formula_results
      - filter_inputs
    """

    exec_ctx: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    formula_results: Dict[Tuple[Any, str], Any] = field(default_factory=dict)
    filter_inputs: Dict[str, frozenset] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # exec_ctx（边执行上下文：count / first_fire / last_fire）
    # ------------------------------------------------------------------
    def get_exec_ctx(self, eid: str) -> Dict[str, Any]:
        if eid not in self.exec_ctx:
            self.exec_ctx[eid] = {
                "count": 0,
                "first_fire": None,
                "last_fire": None,
            }
        return self.exec_ctx[eid]

    def set_exec_ctx_fired(self, eid: str, now: Optional[float] = None) -> None:
        ctx = self.get_exec_ctx(eid)
        if now is None:
            now = time.time()
        ctx["count"] = ctx.get("count", 0) + 1
        if ctx["first_fire"] is None:
            ctx["first_fire"] = now
        ctx["last_fire"] = now

    # ------------------------------------------------------------------
    # 快照 / 恢复
    # ------------------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        return {
            "exec_ctx": copy.deepcopy(self.exec_ctx),
            "formula_results": copy.deepcopy(self.formula_results),
            "filter_inputs": copy.deepcopy(self.filter_inputs),
        }

    def restore(self, data: Dict[str, Any]) -> None:
        self.exec_ctx = copy.deepcopy(data.get("exec_ctx", {}))
        self.formula_results = copy.deepcopy(data.get("formula_results", {}))
        self.filter_inputs = copy.deepcopy(data.get("filter_inputs", {}))

    def fresh(self) -> None:
        self.exec_ctx.clear()
        self.formula_results.clear()
        self.filter_inputs.clear()


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
    evaluator_type: Literal["pass_through", "formula", "scalar", "set_operation", "intersection"] = "pass_through"
    evaluator_params: Dict[str, Any] = Field(default_factory=dict)


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
    # 以下字段为 Task 14 兼容性 facade 保留，供旧测试 / PoolEngine 旧 API 读取
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
        if not jgtime:
            jgtime = int(params.get("time_gate_interval", 0) or 0)
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
        "INTERSECTION": "intersection",
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
            formula_period = params.get("formula_period", "")
            if not formula_period:
                builtin_info = _lookup_builtin_formula_info(formula_ref_direct)
                if builtin_info and builtin_info.get("period"):
                    formula_period = builtin_info["period"]
            return FilterSpec(
                filter_type="formula",
                formula_ref=formula_ref_direct,
                formula_period=formula_period,
                threshold=float(params.get("fsecond") or params.get("threshold") or 0),
                noperate=int(params.get("noperate", 0) or 0),
                sorttype=int(params.get("sorttype", 0) or 0),
                compare_mode=str(params.get("compare_mode") or ""),
                evaluator_type="formula",
                evaluator_params={},
            )

        # 无 formula_ref 也无 tdx_func 时，检查 condition_type（如 INTERSECTION）
        condition_type = str(params.get("condition_type", "") or "")
        if condition_type == "INTERSECTION":
            return FilterSpec(
                filter_type="INTERSECTION",
                formula_ref="",
                threshold=0.0,
                noperate=0,
                sorttype=0,
                compare_mode="",
                evaluator_type="intersection",
                evaluator_params={"intersection_source": params.get("intersection_source", "")},
            )

        # 无 condition_type 时，按源节点类型决定 filter_type（均退化为 pass_through）
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


# ===========================================================================
# 单条边执行器（原 core/edge_executor.py）
# ===========================================================================
# 按 ``execute-architecture-migration`` 规格 Task 5 实现。
# ``EdgeExecutor`` 只读 ``CompiledSchedule``，不写 ``pool_config``；所有行为差异
# 来自编译期表行内容，运行期只做查表与固定解释。


def _now_ts(state: PoolState) -> float:
    """从 ``state.time_source`` 或本地时间获取当前时间戳。

    返回 ``time_at(state)`` 原值——与 ``EventDriver.fire_due(now)`` 中 ``now`` 单位一致。
    不再转换为 Unix 时间戳，因为 TTL 的 ``at_fn`` / ``pop_expired`` / ``fire_due``
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
) -> Dict[str, float]:
    """为新进入目标池的股票创建/初始化 tracker，并注册 interval 类型 TTL。

    TTL 类型分派（表驱动）：
      - check_type="interval"：注册到 TtlTracker（堆，O(log N)），到期由 fire_ttl_due 批量弹出
      - check_type="endtime"：编译期已注册 TimedEventSpec（时钟触发），无需运行期注册
      - check_type="none"：无 TTL，跳过
    """
    prices: Dict[str, float] = {}
    tgt_stocks = state.get_node_stocks(tgt)
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
                event_driver.register_ttl(eid, code, ttl_spec.ttl_sec, ts, ts)

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
        other_stocks |= {_stock_code(s) for s in state.get_node_stocks(other.sid)}

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
    formula_engine: FormulaEngine,
    tick_table: "TickTable",
    spec: FilterSpec,
    codes: List[str],
    eid: str,
) -> List[str]:
    """透传：全部通过（无条件边 / 无公式条件边）。"""
    return list(codes)


def _eval_formula_path(
    state: PoolState,
    schedule: CompiledSchedule,
    formula_engine: FormulaEngine,
    tick_table: "TickTable",
    spec: FilterSpec,
    codes: List[str],
    eid: str,
) -> List[str]:
    """公式求值路径：nset=0/1/2 + 通用公式，委托 FormulaEngine.eval。"""
    if not codes:
        return []
    try:
        period = spec.formula_period or "1d"
        ctx = live_context(state, period=period)
        ctx.period = period
        results = formula_engine.eval(spec, codes, ctx)
    except Exception as ex:
        logger.warning("公式求值失败 %s: %s", spec.formula_ref, ex)
        return []
    op = spec.compare_mode or _parse_noperate(spec.noperate)
    return [c for c in codes if _value_passes(results.get(c), spec.threshold, op)]


def _eval_scalar_path(
    state: PoolState,
    schedule: CompiledSchedule,
    formula_engine: FormulaEngine,
    tick_table: "TickTable",
    spec: FilterSpec,
    codes: List[str],
    eid: str,
) -> List[str]:
    """标量评估路径：nset=3/4，委托 evaluators.eval_scalar_nset。

    I18 修复：nset=3/4 现在正确路由至 eval_scalar_nset（旧路径 evaluator 字段
    承载 "tdx_eval_nset3/4" 而非 "eval_scalar_nset"，导致标量分支永不触发）。
    I54：缓存收敛到 FormulaEngine.eval_scalar（消除本函数重复的 cache_key
    构造与 formula_results 读写）。mode 从 time_source 派生，保留原缓存隔离语义。
    """
    if not codes:
        return []

    # I54：构造 EvalContext，mode 从 time_source 派生（保留原缓存隔离语义）
    # I25：tick_table.bar_hash() 与 state.bar_hash() 双层一致。
    kind = state.time_source.get("kind", "live")
    formula_mode = kind if kind in ("live", "replay", "simulation") else "live"
    ctx = EvalContext(
        mode=formula_mode,
        bar_hash=tick_table.bar_hash(),
        bars={},
        latest_tick=state.latest_tick,
    )

    def _evaluator(codes: List[str], ctx: EvalContext) -> Dict[str, Any]:
        prev_lookup = lambda c: tick_table.prev_column(c, "line1")
        action_inputs = {
            "src_params": {"tdx_func": {
                "ntjindexno": spec.formula_ref,
                "noperate": spec.noperate,
                "fsecond": spec.threshold,
            }},
            "stock_list": codes,
            "market_data_port": getattr(state, "market_data_port", None),
            "current_bar_data": getattr(state, "current_bar_data", {}),
        }
        nset_cfg = spec.evaluator_params or {"nset": 0}
        passed = eval_scalar_nset(action_inputs, nset_cfg, prev_lookup=prev_lookup)
        passed_set = set(passed)
        return {c: (c in passed_set) for c in codes}

    results = formula_engine.eval_scalar(spec, codes, ctx, _evaluator)
    return [c for c in codes if results.get(c)]


def _eval_set_op_path(
    state: PoolState,
    schedule: CompiledSchedule,
    formula_engine: FormulaEngine,
    tick_table: "TickTable",
    spec: FilterSpec,
    codes: List[str],
    eid: str,
) -> List[str]:
    """集合运算路径：nset=5，委托 _eval_set_operation。"""
    op_code = int(spec.formula_ref or 0)
    passed, _rejected = _eval_set_operation(state, schedule, eid, codes, op_code)
    return passed


def _eval_intersection_path(
    state: PoolState,
    schedule: CompiledSchedule,
    formula_engine: FormulaEngine,
    tick_table: "TickTable",
    spec: FilterSpec,
    codes: List[str],
    eid: str,
) -> List[str]:
    """交集条件路径：委托 evaluate_intersection 筛选与源状态池的交集。"""
    edge_params = spec.evaluator_params or {}
    return evaluate_intersection(codes, state, edge_params)


# evaluator_type → handler（表驱动，无 if/elif 分派）
_FILTER_EVALUATORS: Dict[str, Callable[..., List[str]]] = {
    "pass_through": _eval_pass_through,
    "formula": _eval_formula_path,
    "scalar": _eval_scalar_path,
    "set_operation": _eval_set_op_path,
    "intersection": _eval_intersection_path,
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
    state.set_node_stocks(tid, tgt_stocks + new_stocks)
    return [_stock_code(s) for s in new_stocks], []


def _tgt_overwrite(state: PoolState, tid: str, transferred: List[Any], tgt_stocks: List[Any]) -> Tuple[List[str], List[str]]:
    """清空目标写入 transferred，返回 (新入池代码, 被覆盖出目标池代码)。

    I66：entered 语义统一 + tracker 保全。旧实现返回 ALL transferred codes，
    且 set_node_stocks 用 transferred 的 fresh _tracker（仅 entry_time）覆盖
    已持仓 stock 的完整 _tracker，导致 overwrite + multi-tick 三重 bug：
      1. BUY spam：_run_callback 对 ALL entered 发 BUY（已持仓重复）
      2. tracker 重置：_init_entry_trackers 对 ALL entered 重置 + set_node_stocks
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
    state.set_node_stocks(tid, transferred)
    entered = [_stock_code(s) for s in transferred if _stock_code(s) not in existing_map]
    target_cleared = [c for c in existing_map if c not in transferred_codes]
    return entered, target_cleared


def _src_delete(state: PoolState, sid: str, src_stocks: List[Any], passed_set: set) -> List[str]:
    """从源池删除已转移股票并标记脏。返回实际离开源池的代码列表。"""
    deleted = [_stock_code(s) for s in src_stocks if _stock_code(s) in passed_set]
    state.set_node_stocks(sid, [s for s in src_stocks if _stock_code(s) not in passed_set])
    state.mark_node_dirty(sid)
    return deleted


def _src_keep(state: PoolState, sid: str, src_stocks: List[Any], passed_set: set) -> List[str]:
    """保留源池不变（no-op）。返回空列表（无股票离开源池）。"""
    return []


# mode → (target_strategy, source_strategy)（表驱动，无 if/elif 分派）。
# target_strategy 返回 (entered, target_cleared) 二元组；source_strategy 返回 exited 代码。
# I21：source_strategy 返回值取代 run() 中 source_before/after 双 get_node_stocks diff。
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
      - formula_engine: FormulaEngine
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
        formula_engine: FormulaEngine,
        event_bus: Optional[EventBus] = None,
        event_driver: Optional[Any] = None,
    ) -> None:
        self.state = state
        self.schedule = schedule
        self.formula_engine = formula_engine
        self.bus = event_bus
        self.event_driver = event_driver  # I4：用于注册 TTL 到时事件
        # I13：TickTable 实时绑定 state.latest_tick / state.prev_tick（不再空 dict）。
        # DataUpdater._apply_code_tick 推进前快照 prev_tick，使 cross 模式 prev_column 真实可用。
        self._tick_table = TickTable(state.latest_tick, state.prev_tick)
        # SubTask 21.3: EdgeExecutor 订阅 EdgeFired 事件执行
        # EdgeFired 由 ExecutionModule（_on_stock_filtered / _run_tick）发布，
        # 订阅后 EdgeExecutor 经事件触发 run(eid)，消除 ExecutionModule 直接调用。
        if self.bus is not None:
            self.bus.subscribe(EdgeFired, self._on_edge_fired)

    def _on_edge_fired(self, event: EdgeFired) -> None:
        """EdgeFired 事件 handler — 经事件触发边执行，携带 changed_codes 增量参数。"""
        changed = getattr(event, "changed_codes", None)
        self.run(event.eid, changed_codes=changed)

    def run(self, eid: str, changed_codes: Optional[List[str]] = None) -> bool:
        """执行单条边：gate → filter → propagate → callback。

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

        # 1. gate
        if not self._gate(timing_spec, eid):
            return False

        self.state.set_exec_ctx_fired(eid, now=_now_ts(self.state))

        # 2. filter（changed_codes 驱动增量筛选）
        source_codes = [_stock_code(s) for s in self.state.get_node_stocks(ec.sid)]
        passed, _rejected = self._filter(filter_spec, source_codes, ec.eid, changed_codes=changed_codes)

        # 3. propagate
        entered, exited, target_cleared = self._propagate(propagate_spec, ec.sid, ec.tid, passed)
        propagate_mode = propagate_spec.mode if propagate_spec else "copy"

        # 4. tracker 初始化
        ts = _now_ts(self.state)
        prices = _init_entry_trackers(
            self.state, ec.tid, entered, ts, ec.eid, self._tick_table,
            ttl_spec=ttl_spec, event_driver=self.event_driver,
        ) if entered else {}
        # 4b. 节点级 TTL 注册（状态池/目标池的 hold 时间，例如 pool_C 20 分钟）
        if entered and self.event_driver is not None:
            node_ttl = self.schedule.node_ttl_spec.get(ec.tid)
            if node_ttl is not None and node_ttl.bdel == 1 and node_ttl.check_type == "interval" and node_ttl.ttl_sec > 0:
                node_ttl_eid = f"node_ttl:{ec.tid}"
                for code in entered:
                    self.event_driver.register_ttl(node_ttl_eid, code, node_ttl.ttl_sec, ts, ts)
        actions = action_spec.target_pool_actions if action_spec else []

        # 5. 发布 Executed 事件
        if self.bus is not None:
            details = {
                "actions": list(actions),
                "prices": dict(prices),
                "timestamp": ts,
            } if entered else None
            _publish(self.bus, Executed(
                eid=ec.eid,
                sid=ec.sid,
                tid=ec.tid,
                entered=list(entered),
                exited=exited,
                target_cleared=target_cleared,
                mode=propagate_mode,
                details=details,
            ))

        # 6. callback
        _run_callback(self.state, ec, action_spec, ec.tid, entered, ts, prices, self.bus)

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
        双 ``get_node_stocks`` diff——消除 2 次冗余读取，propagate 契约完备
        （同时知道 entered 与 exited 两个方向的状态变更）。
        I69：target_strategy 返回 (entered, target_cleared) 二元组，使 Executed
        事件携带三个方向的完整状态变更——entered/exited/target_cleared。
        """
        if spec is None:
            spec = PropagateSpec()

        passed_set = set(passed)
        src_stocks = self.state.get_node_stocks(sid)
        tgt_stocks = self.state.get_node_stocks(tid)

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

        if self.event_driver is not None and target_cleared:
            for eid_key, ec in self.schedule.edge_ctx.items():
                if ec.tid == tid:
                    for code in target_cleared:
                        self.event_driver.unregister_ttl(eid_key, code)
                    break

        self.state.mark_node_dirty(tid)
        return entered, exited, target_cleared


# ===========================================================================
# 统一时间驱动：所有到时事件统一为 TimedEventSpec（原 core/compiler.py 尾部）
# ===========================================================================


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
    """构造边触发的 action：发布 EdgeFired 事件（携带 changed_codes）。

    时间触发与执行分离——at_fn 判定时间，action 发布事件，EdgeExecutor 订阅
    EdgeFired 后执行 gate→filter→propagate→callback。
    changed_codes 取自 DirtyState.changed_codes（本 tick 有 Tick/Bar 更新的股票集合），
    使筛选器可增量评估，仅对变化股票重新计算公式，未变化股票沿用上一次缓存结果。

    新逻辑（视图模型）：
    - is_source_dirty：dirty.data 为 True 且源节点在 source_ids 中
    - is_node_dirty：dirty.nodes.get(src, False)（边到期触发时）
    - changed = dirty.changed_codes ∩ 源池股票代码集合（当 is_source_dirty 时）
    - 首次运行或 changed_codes 为空时，changed = 源池所有股票（全量评估）
    """

    def action(params: Any) -> None:
        ec = schedule.edge_ctx.get(eid)
        if ec is None:
            return
        src = ec.sid
        dirty = state.dirty
        is_node_dirty = dirty.nodes.get(src, False)
        is_source_dirty = dirty.data and src in source_ids
        if not (is_node_dirty or is_source_dirty):
            return
        source_stocks = state.get_node_stocks(src)
        source_codes = set(_stock_code(s) for s in source_stocks if isinstance(s, dict))
        if not dirty.changed_codes:
            changed = set(source_codes)
        else:
            changed = dirty.changed_codes & source_codes
        if bus is not None:
            from .event_bus import EdgeFired as _EdgeFired
            bus.publish(_EdgeFired(
                eid=eid,
                ts=time_at(state=state),
                changed_codes=list(changed),
            ))
        else:
            edge_executor.run(eid, changed_codes=list(changed))

    return action


def _make_ttl_interval_at_fn(tracker: Any) -> Callable[[], float]:
    """构造 TTL interval 类型的 at_fn：委托 TtlTracker.next_expire_at()。

    堆空返回 inf（永不到期），与边触发 at_fn 共用 at_fn() <= now 语义。
    """

    def at_fn() -> float:
        return tracker.next_expire_at()

    return at_fn


def _make_ttl_interval_action(state: Any, tracker: Any, bus: Any) -> Callable[[Any], None]:
    """构造 TTL interval 类型的 action：pop_expired → 发布 SELL Signal + DomainEvent(TIMEOUT)。

    到时触发与执行分离——TtlTracker 管理到期时间，action 发布事件，
    订阅者（engine）执行批量删除。Tracker 不发布事件。

    SubTask 27.4：``time_at`` / ``_stock_code`` / ``DomainEvent`` / ``Signal``
    已随 ``time_util.py`` / ``edge_executor.py`` / ``event_bus`` 一并迁移至本模块，
    原动态 import 链移除，直接使用本地名称。
    
    修复：价格优先从 state.latest_tick 获取，tracker 价格作为后备。
    """

    def action(params: Any) -> None:
        now_val = time_at(state=state)
        expired = tracker.pop_expired(now_val)
        if not expired:
            return
        tgt = tracker.tgt
        eid = tracker.eid
        codes = [e.code for e in expired]

        expired_prices: Dict[str, float] = {}
        latest_tick = getattr(state, "latest_tick", {}) or {}
        for s in state.get_node_stocks(tgt):
            if isinstance(s, dict) and _stock_code(s) in set(codes):
                code = _stock_code(s)
                tick_price = 0.0
                tick_data = latest_tick.get(code, {})
                if isinstance(tick_data, dict):
                    tick_price = float(tick_data.get("close", tick_data.get("price", 0.0)) or 0.0)
                if tick_price > 0:
                    expired_prices[code] = tick_price
                else:
                    tr = s.get("_tracker")
                    if isinstance(tr, dict):
                        expired_prices[code] = float(
                            tr.get("current_price", tr.get("entry_price", 0))
                        )

        kept = [s for s in state.get_node_stocks(tgt) if _stock_code(s) not in set(codes)]
        if len(kept) < len(state.get_node_stocks(tgt)):
            state.set_node_stocks(tgt, kept)
            state.mark_node_dirty(tgt)
            logging.getLogger(__name__).info("TTL expire: removed %s from %s", codes, tgt)

        for entry in expired:
            price = expired_prices.get(entry.code, 0)
            bus.publish(Signal(
                signal_type="SELL",
                code=entry.code,
                pool_id=tgt,
                price=price,
                ts=now_val,
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
    """构造 TTL endtime 类型的 at_fn：当前时刻 >= endtime_sec 时返回 0.0（到期）。

    SubTask 27.4：``time_at`` / ``is_offset_of_day`` / ``_current_seconds_of_day``
    已随 ``time_util.py`` / ``edge_executor.py`` 一并迁移至本模块，原动态 import
    链移除，直接使用本地名称。
    """

    def at_fn() -> float:
        now = time_at(state=state)
        now_sec = _current_seconds_of_day(now)
        if now_sec >= endtime_sec:
            return 0.0
        return now + 1.0

    return at_fn


def _make_ttl_endtime_action(state: Any, ttl_spec: "TTLSpec", tgt: str, bus: Any, eid: str) -> Callable[[Any], None]:
    """构造 TTL endtime 类型的 action：扫描 hold 超时股票 → 发布 SELL Signal + DomainEvent(TIMEOUT)。

    endtime 模式在时钟到达 endtime_sec 时触发，检查 hold_for_ttl 过滤超时股票。
    这不是轮询——是时钟驱动的单次/周期触发。
    
    修复：添加 SELL Signal 发布（与 interval 类型一致），价格从 latest_tick 获取。

    SubTask 27.4：``_stock_code`` / ``_stock_entry_time`` / ``_now_ts`` /
    ``_current_seconds_of_day`` / ``DomainEvent`` / ``time_at`` 已随相关源文件
    一并迁移至本模块，原动态 import 链移除，直接使用本地名称。
    """

    def action(params: Any) -> None:
        now_unix = _now_ts(state)
        now_sec_of_day = _current_seconds_of_day(time_at(state=state))
        if now_sec_of_day < ttl_spec.endtime_sec:
            return
        removed_codes: List[str] = []
        removed_prices: Dict[str, float] = {}
        stocks = state.get_node_stocks(tgt)
        kept: List[Any] = []
        latest_tick = getattr(state, "latest_tick", {}) or {}
        for stock in stocks:
            should_remove = False
            if ttl_spec.hold_for_ttl > 0:
                entry_ts = _stock_entry_time(stock)
                if entry_ts is not None and (now_unix - entry_ts) >= ttl_spec.hold_for_ttl:
                    should_remove = True
            else:
                should_remove = True
            
            if should_remove:
                code = _stock_code(stock)
                removed_codes.append(code)
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
        if removed_codes:
            state.set_node_stocks(tgt, kept)
            state.mark_node_dirty(tgt)
            logging.getLogger(__name__).info("TTL endtime expire: removed %s from %s", removed_codes, tgt)
            for code in removed_codes:
                price = removed_prices.get(code, 0)
                bus.publish(Signal(
                    signal_type="SELL",
                    code=code,
                    pool_id=tgt,
                    price=price,
                    ts=now_unix,
                    quantity=0,
                ))
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
    """从 state 读取当前时间戳（三模式统一入口）。

    SubTask 27.4：``time_at`` 已随 ``time_util.py`` 一并迁移至本模块，
    原动态 import 链移除，直接使用本地 ``time_at``。
    """
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

    SubTask 27.4：``TimedEventSpec`` / ``TtlTracker`` 已随 ``time_util.py``
    一并迁移至本模块，原动态 import 链移除，直接使用本地名称。
    """
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
        for stock in state.get_node_stocks(tgt):
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
        for stock in state.get_node_stocks(tgt):
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
        state.set_node_stocks(tgt, kept)
        state.mark_node_dirty(tgt)
        logger.info("TTL expire: removed %s from %s (check=%s)",
                    removed, tgt, ttl_spec.check_type)
        if bus is not None:
            for code in removed:
                price = removed_prices.get(code, 0)
                bus.publish(Signal(
                    signal_type="SELL",
                    code=code,
                    pool_id=tgt,
                    price=price,
                    ts=now_unix,
                    quantity=0,
                ))
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
                 now_fn: Callable[[], Any] = None):
        self._psatt_cfg = psatt_cfg or {}
        self._defaults = defaults or {}
        self._now = now_fn

    def apply_ttl(self, node_id: str, node: Any, node_stocks: Dict[str, list],
                  bus: Any = None, eid: str = "") -> None:
        """对指定状态池节点执行 TTL 过期淘汰。"""
        ttl_spec = _build_ttl_spec(node_id, {node_id: node})
        if ttl_spec.bdel != 1 or ttl_spec.check_type == "none":
            return

        from .runtime_mode_module import PoolState
        state = PoolState({"nodes": [], "edges": []})
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
        state.set_node_stocks(node_id, list(node_stocks.get(node_id, [])))

        _do_ttl_check(state, ttl_spec, node_id, bus=bus, eid=eid)

        node_stocks[node_id] = list(state.get_node_stocks(node_id))


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
        try:
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
        except Exception as ex:
            logger.warning("ExecutionModule _on_mode_changed 异常: %s", ex)

    # ------------------------------------------------------------------
    # 组件访问（惰性创建）
    # ------------------------------------------------------------------
    def _ensure_engine(self) -> Optional[Any]:
        """惰性创建/复用 PoolEngine 实例，返回其引用。

        通过 ``PoolEngine._ensure_pool_engine`` 复用现有创建逻辑，避免重复
        PoolEngine.__init__ 中的组件装配（EventBus / DataUpdater / BarComposer /
        TradeExecutor / EventPanel / FormulaEngine / EdgeExecutor / EventDriver）。
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
    def _on_pool_loaded(self, event: PoolLoaded) -> None:
        """池配置加载触发编译（SubTask 8.4 关联）。"""
        try:
            self._pool_config = event.pool_config or {}
            self._compiled = Compiler.compile(self._pool_config)
            self._engine = None  # 重置，下次 _ensure_engine 重建
        except Exception as ex:
            logger.warning("ExecutionModule 编译失败: %s", ex)

    def _on_config_changed(self, event: ConfigChanged) -> None:
        """配置变更触发 CompiledSchedule 重建（SubTask 8.4）。"""
        try:
            if self._pool_config:
                self._compiled = Compiler.compile(self._pool_config)
                self._engine = None  # 配置变更后重建引擎
        except Exception as ex:
            logger.warning("ExecutionModule 重建 CompiledSchedule 失败: %s", ex)

    def _on_stock_filtered(self, event: StockFiltered) -> None:
        """筛选结果写入边 filter_inputs，供边执行读取（SubTask 8.2）。

        SubTask 19.5: StockFiltered → EdgeFired
        筛选结果缓存后立即发布 EdgeFired 事件，使边触发由筛选结果驱动
        （而非仅由 DataChanged 驱动）。使用 ``_fired_edges`` 集合去重，
        避免与 ``_run_tick`` 的 fallback 发布重复。
        """
        try:
            pe = self._ensure_engine()
            if pe is not None:
                pe.state.filter_inputs[event.eid] = frozenset(event.passed)
                logger.info(
                    "ExecutionModule._on_stock_filtered eid=%s passed=%d state_id=%d filter_inputs_size=%d",
                    event.eid, len(event.passed), id(pe.state), len(pe.state.filter_inputs),
                )
            else:
                logger.warning(
                    "ExecutionModule._on_stock_filtered pe=None eid=%s passed=%d",
                    event.eid, len(event.passed),
                )
            self._filter_results[event.eid] = (list(event.passed), list(event.rejected))
            if event.eid not in self._fired_edges:
                self._fired_edges.add(event.eid)
                self._bus.publish(EdgeFired(
                    eid=event.eid,
                    ts=event.ts or time.time(),
                    changed_codes=list(event.passed) if event.passed else [],
                ))
        except Exception as ex:
            logger.warning("ExecutionModule 缓存筛选结果失败: %s", ex)

    def _on_data_changed(self, event: DataChanged) -> None:
        """数据变更触发核心 tick 执行（SubTask 8.2）。"""
        if self._compiled is None:
            return
        try:
            self._run_tick(event)
        except Exception as ex:
            logger.warning("ExecutionModule tick failed: %s", ex)

    def _on_time_advanced(self, event: TimeAdvanced) -> None:
        """时间推进触发 TTL 检查（SubTask 8.2）。"""
        try:
            self._check_ttl_expired(event.ts)
        except Exception as ex:
            logger.warning("ExecutionModule TTL 检查失败: %s", ex)

    def _on_executed(self, event: Executed) -> None:
        """EdgeExecutor 发布 Executed → 转发为 TransferExecuted（SubTask 8.3）。

        ``_emit_transfer_events`` 的事件化版本：原 ``PoolEngine._emit_transfer_events``
        在 tick 末尾批量处理 transfer_events，现改为 per-Executed 即时转发，
        由 Statistics/Monitoring 模块订阅 TransferExecuted 处理。
        """
        try:
            if event.entered or event.exited:
                self._bus.publish(TransferExecuted(
                    src=event.sid,
                    tgt=event.tid,
                    codes=list(event.entered),
                    mode=event.mode,
                    ts=time.time(),
                ))
        except Exception as ex:
            logger.warning("ExecutionModule TransferExecuted 发布失败: %s", ex)

    def _on_domain_event(self, event: DomainEvent) -> None:
        """DomainEvent(TIMEOUT) → TTLExpired（SubTask 8.3）。

        TTL 到期由 ``EventDriver.fire_ttl_due`` 触发，内部 action 发布
        DomainEvent(TIMEOUT) + Signal(SELL)。此处转发为 TTLExpired 事件，
        供 Database/Monitoring 模块订阅。其他 DomainEvent 类型（ENTER/EXIT/
        RANK_CHANGED）不经此转发，保留由原 _on_domain_event 订阅者处理。
        """
        if event.event_type != "TIMEOUT":
            return
        try:
            self._bus.publish(TTLExpired(
                node_id=event.pool_id,
                codes=[event.code],
                ts=time.time(),
            ))
        except Exception as ex:
            logger.warning("ExecutionModule TTLExpired 发布失败: %s", ex)

    # ------------------------------------------------------------------
    # 核心循环
    # ------------------------------------------------------------------
    def _run_tick(self, event: DataChanged) -> None:
        """执行核心 tick：gate→filter→propagate→callback→ttl（SubTask 8.2/8.3）。

        遍历 ``CompiledSchedule.execution_order``，对每条满足触发条件的边：
          1. 发布 ``EdgeFired`` 事件
          2. 调用 ``EdgeExecutor.run(eid)`` 执行边
             （EdgeExecutor 内部发布 ``Executed`` + ``Signal`` 事件）
          3. 由 ``_on_executed`` 订阅者转发为 ``TransferExecuted`` 事件

        ``Signal`` 事件由 ``EdgeExecutor._run_callback`` 直接发布到 EventBus
        （baimpool 动作产生 BUY Signal），无需此处重复发布——EdgeExecutor 为
        ExecutionModule 持有的内部组件，其发布即代表 ExecutionModule 发布。

        tick 末尾触发 ``EventDriver.fire_ttl_due`` 统一驱动 TTL 到期检查，
        TTL action 发布 DomainEvent(TIMEOUT)，由 ``_on_domain_event`` 转发为
        ``TTLExpired`` 事件。
        """
        pe = self._ensure_engine()
        if pe is None:
            return
        edge_executor = self._get_edge_executor()
        if edge_executor is None or self._compiled is None:
            return
        schedule = self._compiled
        state = pe.state
        dirty = state.dirty
        for eid in schedule.execution_order:
            try:
                if not self._should_fire(eid, pe):
                    continue
                ec = schedule.edge_ctx.get(eid)
                if ec is None:
                    continue
                src = ec.sid
                source_stocks = state.get_node_stocks(src)
                source_codes = set(_stock_code(s) for s in source_stocks if isinstance(s, dict))
                if not dirty.changed_codes:
                    changed = set(source_codes)
                else:
                    changed = dirty.changed_codes & source_codes
                # SubTask 19.5: 去重——若 StockFiltered 已发布 EdgeFired 则跳过
                # （_run_tick 作为 fallback，仅对未经筛选驱动的边发布 EdgeFired）
                if eid not in self._fired_edges:
                    self._bus.publish(EdgeFired(
                        eid=eid,
                        ts=event.ts,
                        changed_codes=list(changed),
                    ))
                # TODO(SubTask 21.3): 待 EdgeExecutor 完全事件化后移除直接调用
                # EdgeExecutor 已订阅 EdgeFired，上方 publish（或 _on_stock_filtered 的
                # publish）已经事件触发 run(eid)。此处 fallback 仅在 EdgeExecutor 未订阅
                # （bus=None 场景）时直接调用，避免双重触发。
                if edge_executor.bus is None:
                    edge_executor.run(eid, changed_codes=list(changed))
            except Exception as ex:
                logger.warning("ExecutionModule 边执行失败 eid=%s: %s", eid, ex)
        # tick 末尾触发 TTL 到期检查（EventDriver 统一驱动）
        event_driver = self._get_event_driver()
        if event_driver is not None:
            try:
                event_driver.fire_ttl_due(event.ts)
            except Exception as ex:
                logger.warning("ExecutionModule fire_ttl_due 失败: %s", ex)
        # SubTask 19.5: 清理本 tick 的去重集合，为下一 tick 准备
        self._fired_edges.clear()

    def _should_fire(self, eid: str, pe: Any) -> bool:
        """判定边是否应触发：源节点 dirty + timing gate 放行。

        判定逻辑与 ``compiler._make_edge_action`` 中的 trigger 检查一致：
        源节点被标脏（dirty.nodes）或源节点为 source 节点且数据已更新（dirty.data）。
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
        """TTL 过期检查：委托 EventDriver.fire_ttl_due 统一驱动（SubTask 8.3）。

        ``EventDriver`` 内部 action 会发布 DomainEvent(TIMEOUT) + Signal(SELL)，
        由 ``_on_domain_event`` 订阅者转发为 ``TTLExpired`` 事件。
        """
        event_driver = self._get_event_driver()
        if event_driver is None:
            return
        try:
            event_driver.fire_ttl_due(ts)
        except Exception as ex:
            logger.warning("ExecutionModule TTL fire_due 失败: %s", ex)


__all__ = [
    # 时序工具（time_util）
    "time_at", "time_now_unix", "is_offset_of_day", "anchor_to_today",
    "TtlEntry", "TtlTracker", "TimedEventSpec", "EventDriver",
    # 边状态（edge_state）
    "EdgeState", "EdgeStateMixin",
    # 编译产物（compiler）
    "CompiledSchedule", "Compiler", "EdgeContext", "TimingSpec",
    "FilterSpec", "PropagateSpec", "ActionSpec", "TTLSpec",
    "build_timed_event_specs",
    # 边执行器（edge_executor）
    "EdgeExecutor", "TickTable",
    # TTL 兼容入口
    "TTLHelper", "_do_ttl_check",
    # 对外统一入口
    "ExecutionModule",
]
