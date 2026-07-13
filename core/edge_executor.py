"""单条边执行器：gate → filter → propagate → callback → ttl。

按 ``execute-architecture-migration`` 规格 Task 5 实现。
``EdgeExecutor`` 只读 ``CompiledSchedule``，不写 ``pool_config``；所有行为差异
来自编译期表行内容，运行期只做查表与固定解释。
"""
from __future__ import annotations

import copy
import json
import logging
import operator
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .compiler import (
    ActionSpec,
    CompiledSchedule,
    EdgeContext,
    FilterSpec,
    PropagateSpec,
    TimingSpec,
    TTLSpec,
)
from .event_bus import (
    DomainEvent,
    EventBus,
    Executed,
    Signal,
)
from .evaluators import _scalar_compare, _NOPERATE_RULES, _RANK_MODES, _resolve_rank, eval_scalar_nset
from .formula import EvalContext, FormulaEngine, live_context
from ._market_utils import _stock_code
from .runtime import PoolState
from .time_util import time_at, time_now_unix, is_offset_of_day


logger = logging.getLogger(__name__)


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

    I35：消除双实现 — 原 MetaEngine._find_edge_condition 已删除，
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
_ACTION_HANDLERS: Dict[str, Callable[[Optional[EventBus], EdgeContext, str, str, float, float, str, str], None]] = {
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

_TIMING_CFG_PATH = Path(__file__).parent.parent / "config" / "timing.json"
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


# evaluator_type → handler（表驱动，无 if/elif 分派）
_FILTER_EVALUATORS: Dict[str, Callable[..., List[str]]] = {
    "pass_through": _eval_pass_through,
    "formula": _eval_formula_path,
    "scalar": _eval_scalar_path,
    "set_operation": _eval_set_op_path,
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

    def run(self, eid: str) -> bool:
        """执行单条边：gate → filter → propagate → callback。"""
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

        # 2. filter
        source_codes = [_stock_code(s) for s in self.state.get_node_stocks(ec.sid)]
        passed, _rejected = self._filter(filter_spec, source_codes, ec.eid)

        # 3. propagate
        entered, exited, target_cleared = self._propagate(propagate_spec, ec.sid, ec.tid, passed)
        propagate_mode = propagate_spec.mode if propagate_spec else "copy"

        # 4. tracker 初始化
        ts = _now_ts(self.state)
        prices = _init_entry_trackers(
            self.state, ec.tid, entered, ts, ec.eid, self._tick_table,
            ttl_spec=ttl_spec, event_driver=self.event_driver,
        ) if entered else {}
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
        self, spec: Optional[FilterSpec], codes: List[str], eid: str = ""
    ) -> Tuple[List[str], List[str]]:
        """强弱筛选：返回 passed / rejected 代码列表。

        I18：按 ``spec.evaluator_type`` dict 表驱动分派
        （消除 if/elif filter_type/evaluator + _eval_formula 双路径）。
        I53：evaluator_type 成为唯一运行期分派键，formula.py 的 filter_type
        if/elif 已收敛为 _eval_formula 单路径，双层分派彻底统一为单层。
        """
        if eid:
            self.state.filter_inputs[eid] = frozenset(codes)

        if spec is None:
            return list(codes), []

        handler = _FILTER_EVALUATORS.get(spec.evaluator_type, _eval_pass_through)
        passed = handler(
            self.state, self.schedule, self.formula_engine,
            self._tick_table, spec, codes, eid,
        )
        rejected = [c for c in codes if c not in set(passed)]
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


__all__ = ["EdgeExecutor"]
