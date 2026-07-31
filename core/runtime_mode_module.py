"""RuntimeMode 模块：实盘/回放/仿真三模式事件驱动入口。"""
from __future__ import annotations

# === 合并自 core/runtime.py ===
import asyncio
import copy
import heapq
import json
import logging
import os
import random
import threading
import time
import tracemalloc
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, time as dt_time, timedelta
from typing import Any, Callable, ClassVar, Dict, Iterable, List, NamedTuple, Optional, Set, Tuple, TYPE_CHECKING

import pandas as pd

from core.domain import DZH_COL_MAP, MockDataSource, _hash_tick, _normalize_to_fz, _stock_code, time_at, _safe_timestamp, EdgeState, _build_adjacency, TimedEventSpec
from core.domain import get_global_config_store
from core._hashing import BarHashMixin, hash_dict_content, hash_tick_aggregate
from converters_common import safe_float, safe_int

if TYPE_CHECKING:
    from core.engine import PoolEngine

from core.event_bus import (
    _BaseModule,
    _event_handler,
    DataChanged,
    EventBus,
    ModeChanged,
    ReplayStarted,
    ReplayStep,
    SimulationStep,
    TickReceived,
    TimeAdvanced,
    _publish_tick_batch,
)
from core.schemas import (
    ConditionCellModel,
    FlowAttrBitsModel,
    PoolMetaModel,
    StatePoolCellModel,
    StockSnapshotModel,
)
# _publish_tick_batch 已下沉至 event_bus（白名单），消除 runtime_mode→tick_bar_module 跨 business 依赖。
from core.tick_table import TickTable

logger = logging.getLogger(__name__)

# 仿真速度倍率上下限（spec: 0.5x~20x）
_SIM_SPEED_MIN = 0.5
_SIM_SPEED_MAX = 20.0

# 配置表名（ConfigStore stem key，递归查找 config/runtime/*.json）
# Task 9.6: _load_json 已删除，改用 ConfigStore.get_table 加载
_CONFIG_FILES = {
    "runtime_modes": "runtime_modes",
    "time_sources": "time_sources",
    "trade_interfaces": "trade_interfaces",
}

# 回放引擎常量（原 core/replay.py）
DEFAULT_CODES = ["SH600000", "SZ000001", "SH600519", "SZ000858"]

SPEED_MAP: Dict = {1: 1.0, 2: 2.0, 5: 5.0, 10: 10.0, 100: 100.0, "MAX": 1000000.0}

BASE_INTERVAL: Dict[str, float] = {"day": 0.5, "5min": 0.2, "1min": 0.1}

# SubTask 11.1: 回放周期 -> KLineProvider 周期映射
_PERIOD_TO_KLP: Dict[str, str] = {
    '1min': '1m',
    '5min': '5m',
    '15min': '15m',
    '30min': '30m',
    '60min': '60m',
    'day': '1d',
    'week': '1wk',
}

_BASE_PERIOD_TARGETS: Dict[str, List[str]] = {
    "1min": ["5min", "15min", "30min", "60min", "day", "week", "month"],
    "5min": ["15min", "30min", "60min", "day", "week", "month"],
    "day": [],
}

MARKET_OPEN_AM = dt_time(9, 30)
MARKET_CLOSE_AM = dt_time(11, 30)
MARKET_OPEN_PM = dt_time(13, 0)
MARKET_CLOSE_PM = dt_time(15, 0)

EVENT_LOG_MAX_SIZE = 2000


def _is_trading_time(t: dt_time) -> bool:
    return (MARKET_OPEN_AM <= t <= MARKET_CLOSE_AM) or (MARKET_OPEN_PM <= t <= MARKET_CLOSE_PM)


def _get_market_open_time(dt: datetime) -> datetime:
    return dt.replace(hour=9, minute=30, second=0, microsecond=0)


def _get_market_close_time(dt: datetime) -> datetime:
    return dt.replace(hour=15, minute=0, second=0, microsecond=0)


_BAR_DT_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d")


def _parse_bar_dt(s: str) -> Optional[datetime]:
    """bar 时间字符串 → datetime——合并 4 处 strptime 多格式尝试。"""
    for fmt in _BAR_DT_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


# 仿真器辅助函数与数据类（原 core/simulator.py）
class _SimTick(NamedTuple):
    """仿真层轻量 Tick 数据结构（Task 4.4：解耦 services.minute_aggregator）。

    字段与 ``services.minute_aggregator.Tick`` 保持一致（symbol/time/price/volume），
    使外部注入的 ``Min1Aggregator`` 实例可通过 duck typing 直接消费本类型。
    本类仅在 ``core`` 内部使用，避免 ``core/simulator.py`` 跨层 import
    ``services.minute_aggregator``。
    """

    symbol: str
    time: int
    price: float
    volume: int


@dataclass
class MockStock:
    code: str
    name: str
    market: str
    price: float = 0.0
    change_pct: float = 0.0
    volume: int = 0
    amount: float = 0.0
    ddx: float = 0.0
    bbd: float = 0.0
    volume_ratio: float = 1.0
    turnover: float = 0.0
    high: float = 0.0
    low: float = 0.0
    open_price: float = 0.0
    pre_close: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_snapshot(self):
        return StockSnapshotModel(label=f"{self.code}{self.name}", t=self.code, p=f"{self.price:.2f}")


# _dict_to_mock 已消费字段（不进入 MockStock.extra）——frozenset 加速 membership 测试。
_MOCK_RESERVED_KEYS = frozenset({
    "code", "name", "label", "close", "price", "p", "now", "inprice", "rise",
    "market", "change_pct", "volume", "amount", "ddx", "bbd", "volume_ratio",
    "turnover", "high", "low", "open", "open_price", "pre_close", "_tracker",
})


@dataclass
class StatePool:
    cell_id: str
    stocks: List[MockStock] = field(default_factory=list)
    created_at: float = 0.0
    hold_seconds: int = 0  # 从 timing.json 读取默认值
    deltype: int = 0
    endtime: int = 0
    delstocktype: int = 0
    stock_expiry: Dict[str, float] = field(default_factory=dict)

    def add(self, stk, t=0.0, nd=0, nt=0):
        if self.hold_seconds > 0:
            self.stock_expiry[stk.code] = t + self.hold_seconds
        if nd > 0:
            self.stock_expiry[stk.code] = t + nd * {0: 86400, 1: 3600, 2: 60, 3: 1}.get(nt, 86400)
        if not any(s.code == stk.code for s in self.stocks):
            self.stocks.append(stk)

    def remove(self, stk):
        self.stocks = [s for s in self.stocks if s.code != stk.code]
        self.stock_expiry.pop(stk.code, None)

    def clear(self):
        self.stocks.clear()
        self.stock_expiry.clear()

    def get_expired_stocks(self, t):
        return [s for s in self.stocks if t >= self.stock_expiry.get(s.code, float("inf"))]

    def cleanup_expired(self, t):
        for s in self.get_expired_stocks(t):
            self.remove(s)

    def count(self):
        return len(self.stocks)

    def copy(self):
        return StatePool(
            self.cell_id,
            list(self.stocks),
            self.created_at,
            self.hold_seconds,
            self.deltype,
            self.endtime,
            self.delstocktype,
            dict(self.stock_expiry),
        )


# K 线回放引擎（原 core/replay.py: KLineReplayEngine）
def _run_coro_sync(coro, loop_holder, loop_attr="_sim_loop"):
    """复用持久事件循环同步执行协程：复用 loop → run_until_complete → 运行中降级线程。"""
    loop = getattr(loop_holder, loop_attr)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        setattr(loop_holder, loop_attr, loop)
    try:
        return loop.run_until_complete(coro)
    except RuntimeError:
        # 已在运行的循环中（如 Jupyter / uvicorn），降级到线程执行
        result = []

        def _runner():
            try:
                result.append(asyncio.run(coro))
            except Exception as exc:
                result.append(exc)

        t = threading.Thread(target=_runner)
        t.start()
        t.join()
        if result and isinstance(result[0], Exception):
            raise result[0]
        return result[0] if result else None


def _status(status: str, **extra) -> Dict[str, Any]:
    """状态返回值——合并 replay 控制方法的 {"success": True, "status": X, **extra}。"""
    return {"success": True, "status": status, **extra}


def _normalize_endpoint(edge: Dict[str, Any], key: str, alt_key: str) -> None:
    """归一化边的 source/target 端点为 ``{"node_id": str}``（合并两段同构分支）。"""
    if key not in edge and alt_key in edge:
        edge[key] = {"node_id": str(edge[alt_key])}
    elif key in edge:
        v = edge[key]
        if isinstance(v, str):
            edge[key] = {"node_id": v}
        elif isinstance(v, dict):
            nid = v.get("node_id", "")
            if nid:
                v["node_id"] = str(nid)


def _obj_field(obj: Any, attr: str, dict_keys: Tuple[str, ...], default: Any = None) -> Any:
    """统一对象属性 / 字典字段读取（合并 get_timeline_plan 的 hasattr vs dict.get 双分支）。"""
    if isinstance(obj, dict):
        for k in dict_keys:
            if k in obj:
                return obj[k]
        return default
    return getattr(obj, attr, default)


class KLineReplayEngine:
    def __init__(self, meta_engine: PoolEngine, storage: Optional[Any] = None,
                 bus: Optional[Any] = None):
        # SubTask 22.1: 接收 bus 参数，供未来订阅 ReplayStarted 事件触发首次加载；
        # 当前 bus 仅作为依赖注入保存（实际 K 线加载仍由 load_kline_data 显式触发）。
        self._engine = meta_engine
        # 表驱动：设置 _current_mode_id 驱动 gate_evaluator / data_injector 路由（Task 16）
        self._engine._current_mode_id = 'replay'
        # storage 由 app.py 在 lifespan 注入（实现 services.storage.IStorageQuery Protocol）；
        # 不再在此处实例化 Storage，消除 core → services 跨层依赖。
        # 为 None 时跳过持久化（_create_db_session / _record_snapshot 内部会判空）。
        self._storage = storage
        # Task 24+（Item 3）：EventBus 实例（由 app.py lifespan 注入）。
        self._bus = bus
        self._bars: Dict[str, List[Dict]] = {}
        self._timeline: List[Dict] = []
        self._current_index: int = -1
        self._total_bars: int = 0
        self._base_period: str = "day"
        self._playing: bool = False
        self._paused: bool = True
        self._speed: float = 1.0
        self._pool_model: Optional[Dict] = None
        self._pool_id: str = ""
        self._snapshots: List[Dict] = []
        self._synthesized_bars: Dict[str, Dict[str, List[Dict]]] = {}
        self._mode_state: Optional[Dict] = None

        self._thread_lock = threading.Lock()
        self._event_log: List[Dict] = []
        self._last_bar_events: List[Dict] = []
        self._replay_loop: Optional[asyncio.AbstractEventLoop] = None
        # Task 6：replay 步进改为 EventDriver heapq 调度（取消 while+sleep 轮询）。
        self._replay_step_spec: Optional[TimedEventSpec] = None
        self._db_write_counter: int = 0

        # Task 24+（Item 3）：订阅 ReplayStarted 事件，记录会话信息。
        if self._bus is not None:
            try:
                self._bus.subscribe(ReplayStarted, self._on_replay_started)
            except Exception as ex:
                logger.warning("KLineReplayEngine 订阅 ReplayStarted 失败: %s", ex)

    @_event_handler("_on_replay_started")
    def _on_replay_started(self, event: ReplayStarted) -> None:
        """Task 24+（Item 3）：ReplayStarted 事件订阅者。

        当 RuntimeModeModule.start_replay 发布 ReplayStarted 事件时，本方法被调用。
        记录会话信息（session_id/codes）供后续步进使用。

        .. note::
            实际 K 线加载仍由 ``load_kline_data`` 显式触发，因为该方法需要
            ``pool_model`` / ``base_period`` / ``date_range`` 参数，这些参数
            不在 ``ReplayStarted.session`` 中，由 API 端点直接传入。本订阅者
            仅记录会话信息，实现事件驱动的感知能力（TickBar 等下游模块通过
            ReplayStarted 事件感知回放启动）。
        """
        try:
            session = getattr(event, "session", {}) or {}
            session_id = str(session.get("session_id", ""))
            codes = list(session.get("codes", []) or [])
            logger.info(
                "KLineReplayEngine 收到 ReplayStarted 事件: session=%s codes=%d",
                session_id, len(codes),
            )
        except Exception as ex:
            logger.warning("KLineReplayEngine _on_replay_started 失败: %s", ex)

    def load_kline_data(self, pool_model: Dict, base_period: str, date_range: List[str],
                        pool_id: str = "") -> Dict:
        codes = self._extract_codes(pool_model)
        if not codes:
            codes = list(DEFAULT_CODES)

        # SubTask 11.1: 通过 KLineProvider 获取 K 线（不降级到 tq_adapter）
        kline_provider = getattr(self._engine, 'kline_provider', None)
        if kline_provider is None:
            return {"success": False, "error": "kline_provider 未注入，无法加载 K 线数据（不降级到 tq_adapter）"}

        start, end = date_range[0], date_range[1]

        # 周期映射：replay 格式 -> KLineProvider 格式
        klp_period = _PERIOD_TO_KLP.get(base_period, base_period)

        # end_time 转为 datetime
        end_dt = _parse_bar_dt(end)

        # 对每个 symbol 调用 get_kline_series 获取 K 线
        self._bars = {}
        for code in codes:
            try:
                coro = kline_provider.get_kline_series(
                    code, period=klp_period, end_time=end_dt, count=10000
                )
                df = _run_coro_sync(coro, self, "_replay_loop")
                self._bars[code] = self._df_to_bar_list(df, start)
            except Exception as e:
                logger.warning("通过 KLineProvider 获取 K 线失败 code=%s: %s", code, e)
                self._bars[code] = []

        timeline_raw: Dict[str, Dict] = {}
        for code, bars in self._bars.items():
            for bar in bars:
                t = bar["time"]
                if t not in timeline_raw:
                    timeline_raw[t] = {"time": t, "stocks": {}}
                timeline_raw[t]["stocks"][code] = bar

        self._timeline = sorted(timeline_raw.values(), key=lambda x: x["time"])
        for i, entry in enumerate(self._timeline):
            entry["bar_index"] = i

        self._total_bars = len(self._timeline)
        self._current_index = -1
        self._base_period = base_period
        self._pool_model = pool_model
        self._normalize_edges()
        self._pool_id = pool_id
        self._snapshots = []
        self._synthesized_bars = {}
        self._event_log = []
        self._last_bar_events = []

        self._build_synthesized_bars()

        nodes = {n['id']: n for n in pool_model.get('nodes', [])}
        self._mode_state = {'node_stocks': self._engine._build_node_stocks(nodes), 'inject': True}

        # run_mode 已通过 state.time_source 配置 sequence 时间源，无需再写 PoolEngine 字段。

        if self._pool_id:
            self._create_db_session(base_period, start, end)

        first_time = self._timeline[0]["time"] if self._timeline else ""
        last_time = self._timeline[-1]["time"] if self._timeline else ""

        synthesis_stats = self._calc_synthesis_stats()

        per_code_stats = {}
        for code, bars in self._bars.items():
            per_code_stats[code] = len(bars)

        return {
            "success": True,
            "total_bars": self._total_bars,
            "date_range": [first_time, last_time],
            "codes": codes,
            "period": base_period,
            "code_count": len(codes),
            "per_code_stats": per_code_stats,
            "synthesis_stats": synthesis_stats,
        }

    def _create_db_session(self, base_period: str, start: str, end: str) -> None:
        if self._storage is None:
            self._session_id = ""
            return
        self._session_id = self._storage.create_replay_session(
            self._pool_id, base_period, start, end
        )

    def _df_to_bar_list(self, df, start: str) -> List[Dict]:
        """SubTask 11.1: 将 KLineProvider 返回的 DataFrame 转为 bar dict 列表，并按 start 过滤。

        KLineProvider.get_kline_series() 返回 pd.DataFrame（列含
        time/open/high/low/close/volume/amount），回放引擎内部使用 bar dict 列表，
        此方法负责格式转换与起始日期过滤。
        """
        if df is None or len(df) == 0:
            return []
        bars: List[Dict] = []
        for _, row in df.iterrows():
            t_val = row.get('time', '')
            if isinstance(t_val, datetime):
                t_str = t_val.strftime('%Y-%m-%d %H:%M:%S')
            else:
                t_str = str(t_val)
            # 按 start 过滤（字符串字典序与 'YYYY-MM-DD' 格式兼容）
            if start and t_str < start:
                continue
            bars.append({
                'time': t_str,
                'open': float(row.get('open', 0) or 0),
                'high': float(row.get('high', 0) or 0),
                'low': float(row.get('low', 0) or 0),
                'close': float(row.get('close', 0) or 0),
                'volume': float(row.get('volume', 0) or 0),
                'amount': float(row.get('amount', 0) or 0),
            })
        return bars

    def _calc_synthesis_stats(self) -> Dict[str, Dict[str, int]]:
        stats: Dict[str, Dict[str, int]] = {}
        for code, periods in self._synthesized_bars.items():
            for period, bars in periods.items():
                if period not in stats:
                    stats[period] = {"code_count": 0, "total_bars": 0}
                stats[period]["code_count"] += 1
                stats[period]["total_bars"] += len(bars)
        return stats

    def _extract_codes(self, pool_model: Dict) -> List[str]:
        # 注：当前实现通过 stocks/tdx_stocks 双字段读取已覆盖主要场景，
        # data_config.json:source_node_rules 作为未来扩展点
        codes: List[str] = []
        for node in pool_model.get("nodes", []):
            ntype = node.get("type", "")
            params = node.get("params", {})
            # 优先读 stocks，回退读 tdx_stocks（TDX 原生格式）
            stocks = params.get("stocks") or params.get("tdx_stocks") or []
            if isinstance(stocks, list) and stocks:
                for s in stocks:
                    if isinstance(s, dict):
                        code = s.get("code", "")
                        if code:
                            codes.append(code)
                    elif isinstance(s, str):
                        codes.append(s)
            if ntype == "tdx_candidate" and not stocks:
                spinfo = params.get("tdx_spinfo", params)
                spinfo_type = spinfo.get("type", params.get("type", 0))
                if spinfo_type == 2:
                    codes.extend(DEFAULT_CODES)
        if not codes:
            for node in pool_model.get("nodes", []):
                if node.get("type") not in ("market_source", "tdx_candidate"):
                    continue
                params = node.get("params", {})
                markets = params.get("markets", [])
                if isinstance(markets, str):
                    markets = [m.strip() for m in markets.split(",") if m.strip()]
                if markets:
                    adapter = self._engine.tq_adapter
                    if adapter and hasattr(adapter, "resolve_market"):
                        resolved = adapter.resolve_market(markets)
                        for stock_list in resolved.values():
                            codes.extend(stock_list)
        return list(dict.fromkeys(codes))

    def _normalize_edges(self) -> None:
        # 注：当前归一化逻辑简单（source/target 格式转换），提取到配置表收益不大
        if not self._pool_model:
            return
        edges = self._pool_model.get("edges", [])
        for edge in edges:
            # source/target 同构：合并 startid/endid 兼容 + str/dict 归一化为 {node_id: str}
            _normalize_endpoint(edge, "source", "startid")
            _normalize_endpoint(edge, "target", "endid")

            if not edge.get("id"):
                src = edge.get("source", {}).get("node_id", "")
                tgt = edge.get("target", {}).get("node_id", "")
                if src and tgt:
                    edge["id"] = f"edge_{src}_{tgt}"

            params = edge.get("params", {})
            if isinstance(params, dict):
                if "tran" in params and "mode" not in params:
                    tran_val = params["tran"]
                    tran_int = safe_int(tran_val, 0)
                    params["mode"] = "move" if tran_int == 1 else "copy"

                if 'starttype' in params and 'begin' not in params:
                    params['begin'] = params['starttype']
                if 'starttime' in params and 'begint' not in params:
                    params['begint'] = params['starttime']
                if 'jgtime' in params and 'interval_sec' not in params:
                    params['interval_sec'] = params['jgtime']

    def set_pool_model(self, pool_model: Dict) -> None:
        self._pool_model = pool_model
        self._normalize_edges()

    def _get_node_info(self, node_id: str) -> Dict:
        if not self._pool_model:
            return {}
        for node in self._pool_model.get("nodes", []):
            if node.get("id") == node_id:
                return node
        return {}

    def _build_synthesized_bars(self) -> None:
        self._synthesized_bars = {}
        target_periods = _BASE_PERIOD_TARGETS.get(self._base_period, [])
        if not target_periods:
            return
        for code, bars in self._bars.items():
            self._synthesized_bars[code] = {}
            for tp in target_periods:
                try:
                    self._synthesized_bars[code][tp] = synthesize_kline(
                        list(bars), self._base_period, tp
                    )
                except Exception:
                    self._synthesized_bars[code][tp] = []

    def _get_current_datetime(self) -> Optional[datetime]:
        if self._current_index < 0 or self._current_index >= self._total_bars:
            return None
        return _parse_bar_dt(self._timeline[self._current_index]["time"])

    def _do_step(self) -> Dict:
        self._current_index += 1
        bar_entry = self._timeline[self._current_index]
        current_bar_data = bar_entry["stocks"]
        self._last_bar_events = []
        # 表驱动：本 bar 触发的 flow_id 集合（供回放统计使用）
        self._flows_fired_this_bar: Dict[str, bool] = {}

        # Set the engine's time source to current K-line time
        current_dt = self._get_current_datetime()
        pe = self._engine._pool_engine
        if current_dt and pe is not None:
            pe.state.time_source["current_ts"] = _safe_timestamp(current_dt)
            # G2 硬约束：回放模式必须用 sequence driver，直接覆盖不用 setdefault
            # （engine.py 默认 wall_clock，setdefault 不覆盖会污染时间坐标系）
            pe.state.time_source["driver_type"] = "sequence"

        coro = self._engine._tick(
            self._pool_model,
            self._mode_state['node_stocks'],
            current_bar_data,
            self._mode_state
        )
        self._mode_state['node_stocks'] = _run_coro_sync(coro, self, "_replay_loop")

        # 表驱动：_run_tick_event_driven 已自增 _flow_exec_counts，对比上一步基线
        prev_counts = getattr(self, "_prev_flow_counts", {}) or {}
        cur_counts = dict(self._engine._flow_exec_counts)
        for fid, c in cur_counts.items():
            if c > prev_counts.get(fid, 0):
                self._flows_fired_this_bar[fid] = True
        self._prev_flow_counts = cur_counts

        self._record_snapshot(bar_entry["time"])

        current_dt = self._get_current_datetime()
        if current_dt:
            self._append_event({
                "timestamp": current_dt.isoformat(),
                "timestamp_ts": current_dt.timestamp(),
                "bar_index": self._current_index,
                "event_type": "bar_advance",
                "bar_time": bar_entry["time"],
                "stocks_in_bar": list(current_bar_data.keys()),
            })

        # Task 24+（Item 3）：发布 DataChanged 事件由 TickBar 模块处理。
        # 将当前 bar 的行情数据通过 EventBus 广播，TickBar 订阅 DataChanged
        # 合成 K 线并驱动下游公式计算/筛选/执行链路，实现回放数据流的事件驱动。
        if self._bus is not None:
            try:
                bar_ts = current_dt.timestamp() if current_dt else 0.0
                codes_in_bar = list(current_bar_data.keys())
                bar_hash = str(hash((bar_entry["time"], tuple(sorted(codes_in_bar)))))
                self._bus.publish(DataChanged(
                    ts=bar_ts,
                    bar_hash=bar_hash,
                    codes=codes_in_bar,
                    source="bar",
                    period=self._base_period,
                    data=current_bar_data,
                ))
            except Exception as ex:
                logger.warning("KLineReplayEngine 发布 DataChanged 失败: %s", ex)

        return {"success": True, "time": bar_entry["time"], "index": self._current_index, "flows_fired": list(self._flows_fired_this_bar.keys())}

    def _record_snapshot(self, time_str: str) -> None:
        node_stocks = self._mode_state.get('node_stocks', {}) if self._mode_state else {}
        snap_entry = {
            "time": time_str,
            "bar_index": self._current_index,
            "state_pools": {
                nid: [dict(s) if isinstance(s, dict) else s for s in stocks]
                for nid, stocks in node_stocks.items()
            },
        }
        self._snapshots.append(snap_entry)
        # 内存快照限制：保留最近100个
        if len(self._snapshots) > 100:
            self._snapshots = self._snapshots[-100:]

        if hasattr(self, '_session_id') and self._session_id and self._pool_id and self._storage is not None:
            self._db_write_counter += 1
            # 批量 DB 写入：每10步刷盘一次
            if self._db_write_counter % 10 == 0 or self._current_index >= self._total_bars - 1:
                try:
                    current_bar_data = self._timeline[self._current_index]["stocks"]
                    self._storage.save_replay_snapshot(
                        self._session_id,
                        self._current_index,
                        snap_entry["state_pools"],
                        self._last_bar_events[-20:] if self._last_bar_events else [],
                        current_bar_data,
                    )
                    self._storage.update_replay_session(
                        self._session_id,
                        current_time=time_str,
                        current_bar_index=self._current_index,
                    )
                except Exception:
                    pass

    def _append_event(self, event: Dict) -> None:
        self._event_log.append(event)
        if len(self._event_log) > EVENT_LOG_MAX_SIZE:
            self._event_log = self._event_log[-EVENT_LOG_MAX_SIZE:]
        self._last_bar_events.append(event)

    def play(self) -> Dict:
        with self._thread_lock:
            self._playing = True
            self._paused = False
            # Task 6：步进由 EventDriver heapq 调度；spec 已存在（暂停恢复）则复用。
            if self._replay_step_spec is None:
                pe = self._engine._pool_engine
                ed = pe._components.get("event_driver") if pe is not None else None
                if ed is not None:
                    spec = TimedEventSpec(action=self._replay_step_action, params={"kind": "tick"},
                                          interval=BASE_INTERVAL.get(self._base_period, 0.5) / self._speed,
                                          end_fn=lambda: float('inf') if self._playing else 0.0)
                    loop = ed.get_loop()
                    if loop is not None:
                        ed.schedule_periodic(spec, loop, first_delay=0)
                    else:
                        logger.warning("EventDriver loop 未设置，replay 步进降级 heapq（wall-clock 不触发）")
                        ed.add_spec(spec, time.time())
                    self._replay_step_spec = spec
        return _status("playing", speed=self._speed)

    def _replay_step_action(self, params, fire_time=None) -> None:
        """EventDriver 回调：推进一格 K 线（未播放/暂停时跳过，由 heapq 自然续程）。"""
        if not self._playing or self._paused:
            return
        self._do_step()
        if self._current_index >= self._total_bars - 1:
            self._playing = False
            logger.info("回放结束: 已到达最后一根K线")

    def pause(self) -> Dict:
        self._paused = True
        return _status("paused")

    def stop(self) -> Dict:
        self._playing = False
        self._paused = True
        with self._thread_lock:
            if self._replay_step_spec is not None:
                pe = self._engine._pool_engine
                ed = pe._components.get("event_driver") if pe is not None else None
                if ed is not None:
                    ed.cancel(self._replay_step_spec)
                self._replay_step_spec = None
        if self._replay_loop and not self._replay_loop.is_closed():
            self._replay_loop.close()
        self._replay_loop = None
        return _status("stopped")

    def step(self) -> Dict:
        if self._current_index >= self._total_bars - 1:
            return {
                "error": "已播放完毕",
                "current_index": self._current_index,
                "total_bars": self._total_bars,
            }
        result = self._do_step()
        self._paused = True
        return result

    def next_bar(self) -> Dict:
        """step() 的语义别名：与回放接口契约一致 —— 推进一格 K 线。"""
        return self.step()

    def set_speed(self, speed: Any) -> Dict:
        self._speed = SPEED_MAP.get(speed, 1.0)
        # Task 6：速度变更需重建 spec（interval 不可变），取消旧 spec 后由 play() 重注册。
        if self._replay_step_spec is not None:
            pe = self._engine._pool_engine
            ed = pe._components.get("event_driver") if pe is not None else None
            if ed is not None:
                ed.cancel(self._replay_step_spec)
            self._replay_step_spec = None
            if self._playing and not self._paused:
                self.play()
        return {"success": True, "speed": self._speed}

    def get_current_snapshot(self) -> Dict:
        current_time = ""
        current_dt = None
        if 0 <= self._current_index < self._total_bars:
            current_time = self._timeline[self._current_index]["time"]
            current_dt = self._get_current_datetime()

        state_pools: Dict = {}
        node_stocks = self._mode_state.get('node_stocks', {}) if self._mode_state else {}
        for nid, stocks in node_stocks.items():
            node_label = nid
            node_type = ""
            if self._pool_model:
                for node in self._pool_model.get("nodes", []):
                    if node.get("id") == nid:
                        node_label = node.get("label", nid)
                        node_type = node.get("type", "")
                        break
            pool_info: Dict = {
                "label": node_label,
                "type": node_type,
                "stock_count": len(stocks),
                "stocks": [
                    {
                        "code": _stock_code(stock),
                        "name": stock.get("name", "") if isinstance(stock, dict) else "",
                    }
                    for stock in (stocks or [])[:100]
                ],
            }
            state_pools[nid] = pool_info

        market_open = ""
        market_close = ""
        if current_dt:
            market_open = _get_market_open_time(current_dt).strftime("%H:%M:%S")
            market_close = _get_market_close_time(current_dt).strftime("%H:%M:%S")

        recent_events = self._last_bar_events[-20:] if self._last_bar_events else []
        if not recent_events and self._event_log:
            recent_events = self._event_log[-20:]

        return {
            "current_index": self._current_index,
            "total_bars": self._total_bars,
            "current_time": current_time,
            "progress": max(0, self._current_index) / self._total_bars * 100 if self._total_bars > 0 else 0,
            "playing": self._playing,
            "paused": self._paused,
            "speed": self._speed,
            "state_pools": state_pools,
            "flow_fire_counts": {},
            "flows_fired_this_bar": {},
            "market_open": market_open,
            "market_close": market_close,
            "recent_events": recent_events,
            "event_log_count": len(self._event_log),
        }

    def get_progress(self) -> Dict:
        current_time = ""
        if 0 <= self._current_index < self._total_bars:
            current_time = self._timeline[self._current_index]["time"]

        progress_pct = max(0, self._current_index) / self._total_bars * 100 if self._total_bars > 0 else 0

        return {
            "current_index": self._current_index,
            "total_bars": self._total_bars,
            "current_time": current_time,
            "progress": round(progress_pct, 2),
            "playing": self._playing,
            "paused": self._paused,
            "speed": self._speed,
            "is_completed": self._current_index >= self._total_bars - 1 and self._total_bars > 0,
        }

    def seek(self, progress: float) -> Dict:
        if self._total_bars <= 0:
            return self.get_progress()
        target_index = int(progress / 100.0 * (self._total_bars - 1))
        target_index = max(0, min(target_index, self._total_bars - 1))
        if target_index <= self._current_index:
            self._current_index = target_index
        else:
            while self._current_index < target_index:
                self._do_step()
        return self.get_progress()

    def get_stock_table_data(self, node_id: str) -> Dict:
        stocks = self._mode_state.get('node_stocks', {}).get(node_id, []) if self._mode_state else []
        if not stocks:
            return {"data": [], "columns": []}
        codes = []
        stk_info_map = {}
        for stock in stocks:
            code = _stock_code(stock)
            codes.append(code)
            if isinstance(stock, dict):
                stk_info_map[code] = dict(stock)

        # SubTask 11.2: 通过 MarketDataPort 获取行情数据（不降级到 tq_adapter）
        market_data_port = getattr(self._engine, 'market_data_port', None)
        if market_data_port is None:
            raise RuntimeError(
                "market_data_port 未注入，无法获取行情快照表（不降级到 tq_adapter）"
            )

        col_ids = [2, -1, -2, -3, 7, 14, 8, 10, 17, 45]

        # 通过 MarketDataPort 批量获取行情标量，组装为 snapshot 字典
        # MarketDataPort.get_market_scalars_batch 为 async，使用 _run_coro_sync 同步调用
        snap: Dict[str, Dict[str, Any]] = {c: {} for c in codes}
        market_fields = [
            'current_price', 'latest_price', 'change_pct',
            'max_profit', 'name', 'pre_close', 'volume',
        ]
        for field in market_fields:
            try:
                values = _run_coro_sync(
                    market_data_port.get_market_scalars_batch(codes, field), self, "_replay_loop"
                )
            except Exception:
                values = {}
            if not isinstance(values, dict):
                values = {}
            for c in codes:
                v = values.get(c)
                if v is not None:
                    snap[c][field] = v

        # 构建列定义
        columns = [
            {
                'id': cid,
                'name': DZH_COL_MAP[cid]['name'],
                'key': DZH_COL_MAP[cid]['key'],
                'type': DZH_COL_MAP[cid]['type'],
            }
            for cid in col_ids if cid in DZH_COL_MAP
        ]

        # 构建行数据
        rows = [
            self._build_table_row_via_port(idx, code, snap.get(code, {}), stk_info_map.get(code, {}), col_ids)
            for idx, code in enumerate(codes)
        ]
        return {'data': rows, 'columns': columns}

    def _build_table_row_via_port(
        self, idx: int, code: str, snap: Dict[str, Any], info: Dict[str, Any], col_ids: List[int]
    ) -> Dict[str, Any]:
        """SubTask 11.2: 通过 MarketDataPort 快照 + stk_info 构建表格行。

        替代原 adapter 内部的行构建逻辑：从 MarketDataPort
        获取的标量快照中读取行情字段，从 stk_info 读取入池字段，按 DZH_COL_MAP
        映射输出。同时写入 col_id 与 col key，兼容老代码的两种访问方式。
        """
        current_price = float(snap.get('current_price', snap.get('latest_price', 0)) or 0)
        enter_price_raw = info.get('p') or info.get('enter_price') or snap.get('enter_price')
        enter_price = safe_float(enter_price_raw, 0.0)
        profit_pct = round((current_price - enter_price) / enter_price * 100, 2) if enter_price > 0 else 0
        max_profit = round(profit_pct * 1.2, 2) if enter_price > 0 else float(snap.get('max_profit', 0) or 0)

        # I45：表驱动行值——key→value 映射从 11 路 elif 链提取为 dict。
        row_values: Dict[str, Any] = {
            'code': code,
            'name': snap.get('name') or info.get('name') or code,
            'seq': idx + 1,
            'hold_days': 0,
            'enter_time': info.get('t') or info.get('enter_time') or snap.get('enter_time', '-'),
            'current_price': current_price,
            'enter_price': round(enter_price, 2) if enter_price else '-',
            'profit_pct': profit_pct,
            'max_profit': max_profit,
            'latest_price': float(snap.get('latest_price', current_price) or 0),
            'change_pct': float(snap.get('change_pct', 0) or 0),
        }

        row: Dict[str, Any] = {}
        for cid in col_ids:
            col_def = DZH_COL_MAP.get(cid, {})
            key = col_def.get('key', '')
            col_name = col_def.get('name', '')
            val = row_values[key] if key in row_values else snap.get(key, 0)
            # 同时写入 col_id 和 col key（兼容两种访问方式）
            row[str(cid)] = val
            if key:
                row[key] = val
            if col_name:
                row[col_name] = val
        return row


# K线合成器（原 core/replay.py 末尾，原 services/kline_synthesizer.py）

PERIOD_MAP: Dict[str, int] = {
    "1min": 1,
    "5min": 5,
    "15min": 15,
    "30min": 30,
    "60min": 60,
    "day": 1440,
    "week": 10080,
    "month": 43200,
}

_PERIOD_BARS_1MIN: Dict[str, int] = {"5min": 5, "15min": 15, "30min": 30, "60min": 60}
_PERIOD_BARS_5MIN: Dict[str, int] = {"15min": 3, "30min": 6, "60min": 12}


def _aggregate_ohlcv(group: List[Dict[str, Any]]) -> Dict[str, Any]:
    """OHLCV 聚合——合并 _aggregate_bars / _group_and_synthesize 的 7 字段字面量。"""
    return {
        "time": group[0]["time"],
        "open": group[0]["open"],
        "close": group[-1]["close"],
        "high": max(b["high"] for b in group),
        "low": min(b["low"] for b in group),
        "volume": sum(b["volume"] for b in group),
        "amount": sum(b["amount"] for b in group),
    }


def _aggregate_bars(bars: List[Dict], n: int) -> List[Dict]:
    result: List[Dict] = []
    for i in range(0, len(bars), n):
        group = bars[i : i + n]
        if len(group) < n:
            break
        result.append(_aggregate_ohlcv(group))
    return result


_DATE_KEYS: Dict[str, str] = {"day": "%Y-%m-%d", "month": "%Y-%m"}


def _get_date_key(dt: datetime, kind: str) -> str:
    """日期键——合并 _get_week_key / _get_month_key / _day_key。week 取周一。"""
    if kind == "week":
        return (dt - timedelta(days=dt.weekday())).strftime("%Y-%m-%d")
    return dt.strftime(_DATE_KEYS[kind])


def _group_and_synthesize(bars: List[Dict], key_func) -> List[Dict]:
    groups: Dict[str, List[Dict]] = {}
    order: List[str] = []
    for bar in bars:
        dt = _parse_bar_dt(bar["time"])
        key = key_func(dt)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(bar)
    return [_aggregate_ohlcv(groups[key]) for key in order]


# SubTask 13.1: 周期 → 分组 key 函数表（委托 _get_date_key）
_PERIOD_KEY_FUNCS: Dict[str, Callable[[datetime], str]] = {
    "day": lambda dt: _get_date_key(dt, "day"),
    "week": lambda dt: _get_date_key(dt, "week"),
    "month": lambda dt: _get_date_key(dt, "month"),
}


def _synthesize_intraday_to_day(bars: List[Dict]) -> List[Dict]:
    """分钟级 → day：按日分组并将 time 重写为当日 ``00:00:00``。"""
    result = _group_and_synthesize(bars, lambda dt: _get_date_key(dt, "day"))
    for bar in result:
        dt = _parse_bar_dt(bar["time"])
        bar["time"] = dt.strftime("%Y-%m-%d") + " 00:00:00"
    return result


def _synthesize_by_period(bars: List[Dict], target_period: str) -> List[Dict]:
    """day → week/month：按目标周期 key 函数分组（查 ``_PERIOD_KEY_FUNCS``）。"""
    key_func = _PERIOD_KEY_FUNCS[target_period]
    return _group_and_synthesize(bars, key_func)


# SubTask 13.2: (source_period, target_period) → 合成函数表
# 1min/5min 用固定 bar 数聚合；60min → day 单独处理 time 重写；
# day → week/month 走 _PERIOD_KEY_FUNCS 分组。
_SYNTHESIS_RULES: Dict[Tuple[str, str], Callable[[List[Dict]], List[Dict]]] = {
    ("1min", "5min"): lambda bars: _aggregate_bars(bars, 5),
    ("1min", "15min"): lambda bars: _aggregate_bars(bars, 15),
    ("1min", "30min"): lambda bars: _aggregate_bars(bars, 30),
    ("1min", "60min"): lambda bars: _aggregate_bars(bars, 60),
    ("5min", "15min"): lambda bars: _aggregate_bars(bars, 3),
    ("5min", "30min"): lambda bars: _aggregate_bars(bars, 6),
    ("5min", "60min"): lambda bars: _aggregate_bars(bars, 12),
    ("60min", "day"): _synthesize_intraday_to_day,
    ("day", "week"): lambda bars: _synthesize_by_period(bars, "week"),
    ("day", "month"): lambda bars: _synthesize_by_period(bars, "month"),
}


# SubTask 13.3: 单一合成入口：查表 + 调用
def synthesize(bars: List[Dict], source_period: str, target_period: str) -> List[Dict]:
    rule = _SYNTHESIS_RULES.get((source_period, target_period))
    if rule is None:
        return bars
    return rule(bars)


def synthesize_kline(bars: List[Dict], source_period: str, target_period: str) -> List[Dict]:
    if source_period == target_period:
        return bars

    # 直接映射：查 _SYNTHESIS_RULES
    rule = _SYNTHESIS_RULES.get((source_period, target_period))
    if rule is not None:
        return synthesize(bars, source_period, target_period)

    # 递归分派：1min/5min → 60min 中转
    if source_period in ("1min", "5min"):
        intermediate = synthesize(bars, source_period, "60min")
        return synthesize_kline(intermediate, "60min", target_period)

    # 60min → week/month：先合成 day，再合成 week/month
    if source_period == "60min" and target_period in ("week", "month"):
        daily = synthesize(bars, "60min", "day")
        return synthesize(daily, "day", target_period)

    return bars


# 仿真器（原 core/simulator.py: RuntimeSimulator）
class RuntimeSimulator:
    def __init__(
        self,
        pool_model: Any,
        seed: Optional[int] = None,
        engine: Optional[PoolEngine] = None,
        bus: Optional[EventBus] = None,
        bar_aggregator: Optional[Any] = None,
    ):
        # Task 4.4：bus 注入用于发布 SimulationStep 事件；bar_aggregator 注入替代
        # 内部 Min1Aggregator 创建，消除 core → services.minute_aggregator 跨层 import。
        # 两者均为 Optional，None 时保持向后兼容（不发布事件 / 跳过 bar 聚合）。
        self._bus: Optional[EventBus] = bus
        if isinstance(pool_model, dict) and "nodes" in pool_model and "edges" in pool_model:
            self.pool = pool_model
        elif isinstance(pool_model, dict):
            self.pool = PoolMetaModel.from_dict(pool_model)
        else:
            self.pool = pool_model
        self.pool_config = self._build_pool_config(self.pool)
        self.clock = 34500.0  # 从 09:30:00 开始（A 股开盘时间），避免 virtual_clock=0 时的时间戳转换问题
        self.pools: Dict[str, StatePool] = {}
        self.event_log: List[dict] = []
        self._ini = False
        self._run = False
        self._pau = False
        # I89：消除 ConfigStore 绕过——复用 engine.tables 单一真相源，替代 _lc 直接文件加载
        # 懒加载 PoolEngine 避免循环依赖（runtime_mode_module ↔ engine）
        if engine is not None:
            self._engine = engine
        else:
            from core.engine import PoolEngine as _PoolEngine
            self._engine = _PoolEngine()
        self._tm = self._engine.tables.get("timing") or {}
        self._mk = self._engine.tables.get("mock_data") or {}
        self._sc2 = self._tm.get("simulator", {})
        self._default_hold_seconds = self._sc2.get("default_hold_seconds", 432000)
        self._gn = self._mk.get("generator", {})
        self.rng = random.Random(seed)
        self._cds = set()
        self._mode_state: Optional[Dict[str, Any]] = None
        self._sim_loop: Optional[asyncio.AbstractEventLoop] = None
        # 性能埋点：按阶段采集耗时（gate/filter/propagate/ttl 在 engine._tick 内部，
        # simulator 层采集 tick 总耗时 + mock 数据生成耗时 + 事件收集耗时）
        self._perf_phases: Dict[str, List[float]] = defaultdict(list)
        self._perf_tick_count: int = 0
        self._perf_start: float = time.perf_counter()
        self._perf_peak_mb: float = 0.0
        self._tracemalloc_started: bool = False
        self.speed: float = 1.0
        self._bar_agg = bar_aggregator
        self._thread_lock = threading.Lock()
        # Task 7：auto-step 改为 EventDriver heapq 调度（取消 while+sleep 轮询）。
        self._sim_step_spec: Optional[TimedEventSpec] = None
        self._astep_mode: bool = False

    # ------------------------------------------------------------------
    # Pool config conversion
    # ------------------------------------------------------------------
    def _build_pool_config(self, pool):
        if isinstance(pool, dict):
            config = dict(pool)
            if "nodes" in config and "edges" in config:
                config.pop("cells", None)
                config.pop("flows", None)
                return config
            # cells/flows → nodes/edges 同构：统一 to_dict 透传
            config["nodes"] = [n.to_dict() if hasattr(n, "to_dict") else n for n in config.get("cells", [])]
            config["edges"] = [e.to_dict() if hasattr(e, "to_dict") else e for e in config.get("flows", [])]
            config.pop("cells", None)
            config.pop("flows", None)
            return config
        return {
            "nodes": [c.to_dict() for c in pool.cells],
            "edges": [f.to_dict() for f in pool.flows],
        }

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------
    def _dict_to_mock(self, d):
        if isinstance(d, MockStock):
            return d
        if not isinstance(d, dict):
            code = getattr(d, "code", getattr(d, "t", ""))
            name = getattr(d, "name", getattr(d, "label", code))
            price = float(getattr(d, "p", getattr(d, "now", 0)) or 0)
            return MockStock(code=code, name=name, market="SH", price=price)
        code = d.get("code", d.get("t", ""))
        name = d.get("name", d.get("label", code))
        tracker = d.get("_tracker") or {}
        # 价格字段优先级遍历：close(bar_data) > price/p(通用) > now(TDX) > inprice(成本) > tracker
        _INVALID = {"", None, 0, "0", "0.00", "0.0", 0.0}
        price = 0.0
        for k in ("close", "price", "p", "now", "inprice"):
            v = d.get(k)
            if v not in _INVALID:
                if (p := safe_float(v, None)) is not None:
                    price = p
                    break
        if not price:
            for k in ("current_price", "entry_price"):
                v = tracker.get(k)
                if v:
                    if (p := safe_float(v, None)) is not None:
                        price = p
                        break
        rise = d.get("rise", d.get("change_pct"))
        if rise in _INVALID:
            entry = tracker.get("entry_price", 0)
            if entry and price:
                rise = (price - entry) / entry * 100
            else:
                rise = 0
        change_pct = safe_float(rise, 0.0)
        _market = d.get("market", "FZ" if code.startswith("fz") else "SH")
        return MockStock(
            code=code,
            name=name,
            market=_market,
            price=price,
            change_pct=change_pct,
            volume=d.get("volume", 0),
            amount=d.get("amount", 0.0),
            ddx=d.get("ddx", 0.0),
            bbd=d.get("bbd", 0.0),
            volume_ratio=d.get("volume_ratio", 1.0),
            turnover=d.get("turnover", 0.0),
            high=d.get("high", price),
            low=d.get("low", price),
            open_price=d.get("open_price", d.get("open", price)),
            pre_close=d.get("pre_close", price),
            extra={k: v for k, v in d.items() if k not in _MOCK_RESERVED_KEYS},
        )

    def _all_node_ids(self):
        ids = set()
        for n in self.pool_config.get("nodes", []):
            nid = n.get("id")
            if nid:
                ids.add(nid)
        for e in self.pool_config.get("edges", []):
            src = e.get("from") or e.get("from_cell_id")
            if src is None:
                src_obj = e.get("source")
                src = src_obj.get("node_id", "") if isinstance(src_obj, dict) else (src_obj or "")
            tgt = e.get("to") or e.get("to_cell_id")
            if tgt is None:
                tgt_obj = e.get("target")
                tgt = tgt_obj.get("node_id", "") if isinstance(tgt_obj, dict) else (tgt_obj or "")
            if src:
                ids.add(src)
            if tgt:
                ids.add(tgt)
        return ids

    def _generate_mock_bar_data(self):
        codes = set()
        if self._mode_state:
            for stocks in self._mode_state.get("node_stocks", {}).values():
                for s in stocks:
                    codes.add(_stock_code(s))
        if not codes:
            for i in range(1, self._SIM_FZ_CODE_COUNT + 1):
                codes.add(f"fz{i:06d}")
        codes_list = sorted(codes)
        bar_data = {}
        pr = self._gn.get("price_range", [5.0, 200.0])
        for code in codes_list:
            base = self.rng.uniform(*pr)
            cp = self.rng.gauss(0, self._gn.get("change_pct_std", 3.0))
            price = base * (1 + cp / 100)
            bar_data[code] = {
                "close": round(price, 2),
                "open": round(price * (1 + self.rng.gauss(0, 0.01) / 100), 2),
                "high": round(price * (1 + abs(self.rng.gauss(0, 0.01)) / 100), 2),
                "low": round(price * (1 - abs(self.rng.gauss(0, 0.01)) / 100), 2),
                "pre_close": round(base, 2),
                "volume": int(
                    self.rng.lognormvariate(
                        self._gn.get("volume_lognorm_mu", 14),
                        self._gn.get("volume_lognorm_sigma", 2),
                    )
                ),
            }
        return bar_data

    def _markets_to_codes(self, market):
        """表驱动：market → 候选股票代码集（查 mock_data.json 的 market_scopes）"""
        scopes = self._mk.get("market_scopes", {})
        items = scopes.get(market, scopes.get("all_a", []))
        codes = set()
        for it in items:
            pfx, sfx = it.get("prefix", ""), it.get("suffix", "")
            rs, re_ = it.get("range_start", 0), it.get("range_end", 9)
            for n in range(rs, re_ + 1):
                if sfx:
                    codes.add("%s%03d.%s" % (pfx, n, sfx))
                elif pfx == "fz":
                    codes.add("fz%06d" % n)
                else:
                    codes.add("%s%06d" % (pfx, n))
        return codes

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def _sync_stock_prices(self, bar_data: dict):
        """用 bar_data 更新 node_stocks 中股票字典的价格字段。"""
        if not bar_data:
            return
        node_stocks = self._mode_state.get("node_stocks", {})
        for nid, stocks in node_stocks.items():
            for s in stocks:
                if not isinstance(s, dict):
                    continue
                code = s.get("code", "")
                if not code:
                    continue
                bar = bar_data.get(code)
                if not bar or not isinstance(bar, dict):
                    continue
                s["close"] = bar.get("close", 0)
                s["open"] = bar.get("open", s.get("close", 0))
                s["high"] = bar.get("high", s.get("close", 0))
                s["low"] = bar.get("low", s.get("close", 0))
                s["pre_close"] = bar.get("pre_close", s.get("close", 0))
                s["volume"] = bar_data.get("volume", s.get("volume", 0))

    def _normalize_mode_codes(self):
        """Task 1：将 _mode_state 中所有 node_stocks 的股票代码归一化为 fz 前缀。"""
        if not self._mode_state:
            return
        node_stocks = self._mode_state.get("node_stocks", {})
        for nid, stocks in node_stocks.items():
            norm_stocks = []
            for s in stocks:
                if isinstance(s, dict):
                    code = s.get("code")
                    if code:
                        s["code"] = _normalize_to_fz(code)
                norm_stocks.append(s)
            node_stocks[nid] = norm_stocks
        pe = self._engine._pool_engine
        if pe is not None:
            for nid, stocks in node_stocks.items():
                pool = pe.state.get_pool(nid)
                pool.remove_stocks(list(pool.get_stock_codes()))
                pool.add_stocks(list(stocks))

    def get_bars(self, period='1min'):
        """返回已闭合的K线bar数据，支持 1min 和 5min 周期。"""
        if self._bar_agg is None:
            return {}
        result = {}
        for sym in self._bar_agg.symbols:
            if period == '1min':
                b = list(self._bar_agg.closed_bars.get(sym, []))
                if b:
                    result[sym] = pd.DataFrame(b)
            elif period == '5min':
                b5 = self._bar_agg.get_5min_bars(sym)
                if b5 is not None and not b5.empty:
                    result[sym] = b5
        return result

    def _seed_market_source_stocks(self):
        """仿真模式下为 market_source 节点填充 fz000001~fz000100 标的。"""
        node_stocks = self._mode_state.get("node_stocks", {})
        pe = self._engine._pool_engine
        fz_codes = [f"fz{i:06d}" for i in range(1, self._SIM_FZ_CODE_COUNT + 1)]
        for n in self.pool_config.get("nodes", []):
            if n.get("type") != "market_source":
                continue
            nid = n.get("id", "")
            if node_stocks.get(nid):
                continue
            stocks = [{"code": c, "name": c} for c in fz_codes]
            node_stocks[nid] = stocks
            if pe is not None:
                pe.state.get_pool(nid).add_stocks(list(stocks))
            logger.info("seeded market_source %s with %d fz stocks", nid, len(stocks))

    _SIM_FZ_CODE_COUNT = 20

    def _collect_all_fz_codes(self) -> List[str]:
        """返回仿真固定标的集 fz000001~fz000100，并合并 node_stocks 中已有的 fz 代码。"""
        codes: Set[str] = set()
        for i in range(1, self._SIM_FZ_CODE_COUNT + 1):
            codes.add(f"fz{i:06d}")
        if self._mode_state:
            for stocks in self._mode_state.get("node_stocks", {}).values():
                for s in stocks:
                    code = _stock_code(s)
                    if code:
                        codes.add(_normalize_to_fz(code))
        return sorted(codes)

    def _configure_sim_tick_source(self) -> None:
        """配置 engine._components["tick_source"] 为正确的 MockDataSource 实例。

        在 _normalize_mode_codes 和 _seed_market_source_stocks 之后调用，
        确保所有 codes 都是 fz 前缀且 clock_start 与虚拟时钟对齐。
        G5：MockDataSource 的 tick 定时器注册到 EventDriver 统一优先队列。
        """
        pe = self._engine._pool_engine
        if pe is None:
            return
        codes_list = self._collect_all_fz_codes()
        cfg = self._gn.get("price_range", [5.0, 200.0])
        tick_source = MockDataSource(
            codes=codes_list,
            clock_start=self.clock,
            price_range=cfg if isinstance(cfg, (tuple, list)) and len(cfg) >= 2 else (5.0, 200.0),
            change_pct_std=float(self._gn.get("change_pct_std", 2.0)),
            volume_lognorm_mu=float(self._gn.get("volume_lognorm_mu", 14.0)),
            volume_lognorm_sigma=float(self._gn.get("volume_lognorm_sigma", 2.0)),
        )
        # G5：注入 EventDriver/EventBus 并注册 tick 定时器到统一优先队列
        event_driver = pe._components.get("event_driver")
        event_bus = pe._components.get("event_bus")
        if event_driver is not None:
            tick_source.set_event_driver(event_driver, event_bus)
            tick_source.register_tick_timers(self.clock)
        pe._components["tick_source"] = tick_source
        logger.info("configured MockDataSource for %d fz codes, clock_start=%.1f", len(codes_list), self.clock)

    def _post_init_mode_state(self):
        """run_mode 得到初始 node_stocks 后的后处理：对齐虚拟时钟、归一化代码、注入 tick_source、同步 Pool 股票。"""
        pe = self._engine._pool_engine
        if pe is not None:
            pe.state.time_source["start_ts"] = self.clock
            pe.state.time_source["current_ts"] = self.clock
            # G2 硬约束：仿真模式必须用 virtual driver，直接覆盖不用 setdefault
            pe.state.time_source["driver_type"] = "virtual"
            # 修复：current_ts 设置后重建边触发规格。
            # _init_pool_runtime 在 current_ts 设置前调用 build_timed_event_specs，
            # 导致 first_fire_time 使用 wall clock 而非虚拟时钟，边触发永不执行。
            pe.rebuild_timed_specs()
        self._normalize_mode_codes()
        self._seed_market_source_stocks()
        self._configure_sim_tick_source()
        if pe is not None:
            for nid, stocks in self._mode_state.get("node_stocks", {}).items():
                pool = pe.state.get_pool(nid)
                pool.remove_stocks(list(pool.get_stock_codes()))
                pool.add_stocks(list(stocks))

    def _ensure_mode_state(self):
        """同步路径：延迟调用 run_mode 获取初始 mode_state（首次 step 时执行）。"""
        if self._mode_state is not None:
            return
        self._mode_state = _run_coro_sync(
            self._engine.run_mode("simulation", self.pool_config), self, "_sim_loop"
        )
        self._post_init_mode_state()

    async def _aensure_mode_state(self):
        """异步路径：延迟 await run_mode 获取初始 mode_state（首次 astep 时执行）。

        Task 14：run_mode 内部无 await 点，直接在 Uvicorn 事件循环中执行会阻塞。
        使用 asyncio.to_thread 在后台线程中运行 run_mode，同时用引擎级锁避免
        多个仿真会话并发修改共享 engine.state。
        """
        if self._mode_state is not None:
            return
        lock = getattr(self._engine, "_sim_init_lock", None)
        if lock is not None:
            await lock.acquire()
        try:
            if self._mode_state is not None:
                return

            def _run_mode_sync():
                # run_mode 是 async def，但本质上为同步计算；在线程内新建临时事件循环运行，
                # 避免占用 Uvicorn 事件循环。
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    return loop.run_until_complete(
                        self._engine.run_mode("simulation", self.pool_config)
                    )
                finally:
                    loop.close()

            self._mode_state = await asyncio.to_thread(_run_mode_sync)
            self._post_init_mode_state()
        finally:
            if lock is not None:
                lock.release()

    def initialize(self):
        if self._ini:
            return
        logger.info("simulator initialize: pool_id=%s", self.pool_config.get('id', ''))
        try:
            if not tracemalloc.is_tracing():
                tracemalloc.start()
                self._tracemalloc_started = True
        except ValueError as ex:
            logger.warning("tracemalloc already started: %s", ex)
        # Task 14：启动只做轻量同步初始化，run_mode 的 heavyweight 执行延迟到第一次 step，
        # 避免在 Uvicorn 事件循环中同步阻塞。
        self._engine._ensure_pool_engine(self.pool_config)
        self._ini = True
        self._run = True
        logger.info("simulator initialized: time_source=virtual_clock, clock=%.1f", self.clock)

    def step(self, d=1.0):
        if self._pau:
            return {"bar_data": {}, "changed_codes": [], "events": []}
        if not self._ini:
            self.initialize()
        self._ensure_mode_state()
        all_events = []
        all_bar_data = {}
        all_changed_codes = []
        remaining = float(d)
        sub_step = 1.0
        while remaining > 0:
            sd = min(sub_step, remaining)
            self.clock += sd
            remaining -= sd
            step_result = self._step_once(sd)
            events = step_result.get("events", [])
            all_events.extend(events)
            all_bar_data.update(step_result.get("bar_data", {}))
            all_changed_codes.extend(step_result.get("changed_codes", []))
        return {
            "events": all_events,
            "bar_data": all_bar_data,
            "changed_codes": sorted(set(all_changed_codes)),
        }

    async def astep(self, d=1.0):
        """async 版本的 step：直接 await engine._tick()，避免跨 loop 死锁。"""
        self._astep_mode = True
        try:
            if self._pau:
                return {"bar_data": {}, "changed_codes": [], "events": []}
            if not self._ini:
                self.initialize()
            await self._aensure_mode_state()
            all_events = []
            all_bar_data = {}
            all_changed_codes = []
            remaining = float(d)
            sub_step = 1.0
            while remaining > 0:
                sd = min(sub_step, remaining)
                self.clock += sd
                remaining -= sd
                step_result = await self._astep_once(sd)
                events = step_result.get("events", [])
                all_events.extend(events)
                all_bar_data.update(step_result.get("bar_data", {}))
                all_changed_codes.extend(step_result.get("changed_codes", []))
            return {
                "events": all_events,
                "bar_data": all_bar_data,
                "changed_codes": sorted(set(all_changed_codes)),
            }
        finally:
            self._astep_mode = False

    def _ev_to_dict(self, ev) -> Dict[str, Any]:
        """事件 → 记录 dict（合并 3 处 asdict/dict + event_type/time 注入）。"""
        try:
            ev_dict = asdict(ev) if hasattr(ev, '__dataclass_fields__') else dict(ev)
        except Exception:
            ev_dict = {"repr": repr(ev)}
        ev_dict["event_type"] = type(ev).__name__
        ev_dict["time"] = self.clock
        return ev_dict

    async def _step_once_impl(self, d, *, async_mode: bool):
        """_step_once 与 _astep_once 的统一骨架（11 步，async_mode 分派同步/异步路径）。"""
        tick_seq = self._perf_tick_count
        pe = self._engine._pool_engine
        if pe is not None:
            # G2 硬约束：仿真模式 driver_type 由 _post_init_mode_state 一次性设置（virtual），
            # 此处仅推进虚拟时钟 current_ts（每 tick 必须推进，否则 fire_due 永不触发）。
            # 不重复覆盖 driver_type——_post_init_mode_state 已设置，重复覆盖是冗余。
            pe.state.time_source["current_ts"] = self.clock
        logger.info("tick=%d clock=%.1f (%s) %s=%.1f",
                     tick_seq, self.clock, self._ft(self.clock),
                     "astep" if async_mode else "step", d)

        event_bus = pe._components.get("event_bus") if pe is not None else None
        event_offset = event_bus.total_published if event_bus is not None else 0

        t_tick_start = time.perf_counter()

        # ── 1. 从 MockDataSource 获取本步 tick 更新 ──
        tick_source = pe._components.get("tick_source") if pe is not None else None
        changed_codes: List[str] = []
        bar_data: Dict[str, Any] = {}
        tick_data: Dict[str, Any] = {}

        if tick_source is not None:
            t_mock_start = time.perf_counter()
            step_result = tick_source.step(d)
            t_mock_ms = (time.perf_counter() - t_mock_start) * 1000.0
            self._perf_phases['mock_generate'].append(t_mock_ms)

            changed_codes = step_result.get("changed_codes", [])
            bar_data = step_result.get("bar_data", {})
            tick_data = step_result.get("tick_data", {})
            # 同步虚拟时钟（MockDataSource 内部已推进）；sync/async 路径统一处理。
            if step_result.get("virtual_clock") is not None:
                self.clock = step_result["virtual_clock"]
                if pe is not None:
                    pe.state.time_source["current_ts"] = self.clock

            logger.info("tick=%d tick_source: changed=%d bar_data_keys=%d mock_ms=%.2f",
                         tick_seq, len(changed_codes), len(bar_data), t_mock_ms)

            # ── 1b. 通过 EventBus 发布 TickReceived 事件 ──
            # tick_data 来自 tick_source.step()；TickReceived→DataChanged→BarComposed
            # 桥接由 PoolEngine._on_tick_received 完成。委托 _publish_tick_batch 统一发布。
            if event_bus is not None and tick_data:
                _publish_tick_batch(event_bus, tick_data, None, ts_fallback=self.clock)
        else:
            # 无 tick_source（兼容旧路径）：使用 _generate_mock_bar_data
            t_mock_start = time.perf_counter()
            bar_data = self._generate_mock_bar_data()
            t_mock_ms = (time.perf_counter() - t_mock_start) * 1000.0
            self._perf_phases['mock_generate'].append(t_mock_ms)
            changed_codes = list(bar_data.keys())

        # ── 2. 同步 bar_data 到 node_stocks 价格字段 ──
        self._sync_stock_prices(bar_data)

        # ── 3. 记录 changed_codes 到 DirtyState ──
        if pe is not None and changed_codes:
            pe.state.add_changed_codes(changed_codes)

        # ── 4. 执行引擎 tick（统一代码路径：live/sim 共用） ──
        # async_mode 分派：同步用 _run_coro_sync，异步用 await。
        t_engine_start = time.perf_counter()
        if async_mode:
            self._mode_state["node_stocks"] = await self._engine._tick(
                None,
                self._mode_state["node_stocks"],
                bar_data if bar_data else None,
                self._mode_state,
            )
        else:
            self._mode_state["node_stocks"] = _run_coro_sync(
                self._engine._tick(
                    None,
                    self._mode_state["node_stocks"],
                    bar_data if bar_data else None,
                    self._mode_state,
                ), self, "_sim_loop"
            )
        t_engine_ms = (time.perf_counter() - t_engine_start) * 1000.0
        self._perf_phases['engine_tick'].append(t_engine_ms)
        logger.info("tick=%d phase=engine_tick duration_ms=%.2f", tick_seq, t_engine_ms)

        # ── 5. 收集事件 ──
        # async_mode=True：仅收集 _ASTEP_KEY_TYPES 关键事件 + 200 条上限；False：全收集。
        t_evt_start = time.perf_counter()
        events = []
        if event_bus is not None:
            new_events = event_bus.get_events_since(event_offset)
            if async_mode:
                _ASTEP_KEY_TYPES = frozenset({
                    "EdgeFired", "TransferExecuted", "Signal",
                    "OrderPlaced", "OrderFilled", "PositionUpdated",
                    "StockFiltered", "FormulaEvaluated",
                })
                for ev in new_events:
                    if type(ev).__name__ not in _ASTEP_KEY_TYPES:
                        continue
                    events.append(self._ev_to_dict(ev))
                    if len(events) >= 200:
                        break
            else:
                events = [self._ev_to_dict(ev) for ev in new_events]
        while not self._engine._event_queue.empty():
            try:
                self._engine._event_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        while not self._engine._signal_queue.empty():
            try:
                self._engine._signal_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        t_evt_ms = (time.perf_counter() - t_evt_start) * 1000.0
        self._perf_phases['event_collect'].append(t_evt_ms)
        self.event_log.extend(events)
        if len(self.event_log) > EVENT_LOG_MAX_SIZE:
            del self.event_log[:len(self.event_log) - EVENT_LOG_MAX_SIZE]

        # ── 6. SimulationStep 事件发布 ──
        # 仅同步路径（async_mode=False）在 _step_once_impl 内发布；
        # 异步路径的 SimulationStep 由调用方处理。
        if not async_mode and self._bus is not None and not self._astep_mode:
            try:
                pre_step_offset = event_bus.total_published if event_bus is not None else 0
                self._bus.publish(SimulationStep(
                    step={
                        "step_idx": tick_seq,
                        "virtual_ts": self.clock,
                        "interval": d,
                        "events_count": len(events),
                        "changed_codes": changed_codes,
                    },
                    session_id=str(self.pool_config.get("id", "")),
                ))
                if event_bus is not None:
                    post_events = event_bus.get_events_since(pre_step_offset)
                    _KEY_EVENT_TYPES = frozenset({
                        "TickReceived", "DataChanged", "BarComposed",
                        "FormulaEvaluated", "StockFiltered", "EdgeFired",
                        "Executed", "TransferExecuted", "Signal",
                        "OrderPlaced", "OrderFilled", "PositionUpdated",
                        "TTLExpired", "SimulationStep", "TimeAdvanced",
                        "EventLogged", "AlertRaised",
                    })
                    for ev in post_events:
                        ev_type = type(ev).__name__
                        if ev_type not in _KEY_EVENT_TYPES:
                            continue
                        ev_dict = self._ev_to_dict(ev)
                        events.append(ev_dict)
                        self.event_log.append(ev_dict)
                    if len(self.event_log) > EVENT_LOG_MAX_SIZE:
                        del self.event_log[:len(self.event_log) - EVENT_LOG_MAX_SIZE]
            except Exception as ex:
                logger.warning("SimulationStep 发布失败 (tick=%d): %s", tick_seq, ex)

        t_tick_total = (time.perf_counter() - t_tick_start) * 1000.0
        self._perf_phases['tick_total'].append(t_tick_total)
        self._perf_tick_count += 1

        ns = self._mode_state.get("node_stocks", {}) if self._mode_state else {}
        for nid, stocks in ns.items():
            if isinstance(stocks, list) and stocks:
                codes = [_stock_code(s) for s in stocks[:5]]
                logger.info("tick=%d node_id=%s stock_count=%d sample=%s",
                             tick_seq, nid, len(stocks), codes)

        logger.info("tick=%d done total_ms=%.2f events=%d changed=%d",
                     tick_seq, t_tick_total, len(events), len(changed_codes))
        return {
            "events": events,
            "bar_data": bar_data,
            "changed_codes": changed_codes,
        }

    def _step_once(self, d):
        """同步路径：通过 _run_coro_sync 调用 async 骨架；末尾根据 not _astep_mode 发布 SimulationStep。"""
        return _run_coro_sync(self._step_once_impl(d, async_mode=False), self, "_sim_loop")

    async def _astep_once(self, d):
        """异步路径：直接 await async 骨架；SimulationStep 由调用方处理。"""
        return await self._step_once_impl(d, async_mode=True)

    def run_to(self, t):
        ts = self._parse_target_seconds(t)
        r = []
        while self.clock < ts:
            r.extend(self.step(min(1.0, ts - self.clock)))
        return r

    @property
    def _sim_node_stocks(self) -> Dict[str, Any]:
        """仿真 node_stocks 视图——合并 4 处 ``_mode_state.get("node_stocks", {}) if _mode_state else {}``。"""
        return self._mode_state.get("node_stocks", {}) if self._mode_state else {}

    def get_state(self, c):
        if not self._mode_state:
            return StatePool(c, [], self.clock, hold_seconds=self._default_hold_seconds)
        stocks = self._sim_node_stocks.get(c, [])
        return StatePool(c, [self._dict_to_mock(s) for s in stocks], self.clock, hold_seconds=self._default_hold_seconds)

    def get_all_states(self):
        return {c: self.get_state(c) for c in self._all_node_ids()}

    def get_event_log(self):
        return list(self.event_log)

    def get_statistics(self):
        ac, mc, tt = defaultdict(int), defaultdict(int), 0
        for e in self.event_log:
            action = e.get("event_type", "unknown")
            # I84：消费者收敛——mode 在 details dict，非顶层键。
            d = e.get("details") or {}
            mode = d.get("mode", "")
            passed = [e["code"]] if e.get("code") else []
            ac[action] += 1
            mc[mode] += 1
            tt += len(passed)
        node_stocks = self._sim_node_stocks
        pool_states = {}
        for nid, stocks in node_stocks.items():
            pool_states[nid] = {"count": len(stocks), "hold_seconds": self._default_hold_seconds}
        return {
            "current_time": self._ft(self.clock),
            "total_events": len(self.event_log),
            "action_distribution": dict(ac),
            "mode_distribution": dict(mc),
            "total_stocks_transferred": tt,
            "pool_states": pool_states,
            "feedback_loops_detected": 0,
            "active_timers": 0,
            "tdx_timer_count": 0,
            "active_tdx_timers": 0,
        }

    def perf_summary(self) -> Dict[str, Any]:
        """返回仿真性能摘要字典。"""
        total_sec = time.perf_counter() - self._perf_start
        tick_count = self._perf_tick_count
        per_tick_ms = (total_sec * 1000.0 / tick_count) if tick_count > 0 else 0.0

        # 采集内存峰值
        try:
            if tracemalloc.is_tracing():
                _current, peak = tracemalloc.get_traced_memory()
                self._perf_peak_mb = max(self._perf_peak_mb, peak / (1024.0 * 1024.0))
        except Exception as ex:
            logger.warning("tracemalloc get_traced_memory failed: %s", ex)

        result: Dict[str, Any] = {
            'sim_total_sec': total_sec,
            'sim_tick_count': tick_count,
            'sim_per_tick_ms': per_tick_ms,
            'sim_memory_peak_mb': self._perf_peak_mb,
        }
        for phase, durations in self._perf_phases.items():
            result[f'sim_{phase}_ms'] = (sum(durations) / len(durations)) if durations else 0.0
        return result

    def reset(self):
        self.stop()
        self.clock = 34500.0
        self.event_log.clear()
        self.pools.clear()
        self._ini = False
        self._run = False
        self._pau = False
        self._mode_state = None
        from core.engine import PoolEngine as _PoolEngine
        self._engine = _PoolEngine()
        self._perf_phases.clear()
        self._perf_tick_count = 0
        self._perf_start = time.perf_counter()
        self._perf_peak_mb = 0.0
        self.initialize()

    def get_state_snapshot(self):
        # 注：快照输出是运行时聚合，不属于核心循环，延后到输出规则系统统一设计
        node_stocks = self._sim_node_stocks
        pi = {}
        for c, stocks in node_stocks.items():
            mock_stocks = [self._dict_to_mock(s) for s in stocks[:20]]
            pi[str(c)] = {
                "count": len(stocks),
                "hold_seconds": self._default_hold_seconds,
                "stocks": [
                    {
                        "code": s.code,
                        "name": s.name,
                        "price": round(s.price, 2),
                        "change_pct": round(s.change_pct, 2),
                    }
                    for s in mock_stocks
                ],
                "total_count": len(stocks),
            }
        # 仿真虚拟时钟 1 步 = 1 秒
        current_index = int(self.clock)
        # 总步数：A 股一个完整交易日 09:30:00-15:00:00 = 5.5h = 19800 秒
        total_bars = 19800
        return {
            "clock": self.clock,
            "clock_str": self._ft(self.clock),
            "pools": pi,
            "node_stocks": {str(k): v for k, v in node_stocks.items()},
            "total_events": len(self.event_log),
            "feedback_loops": 0,
            "is_running": self._run,
            "current_index": current_index,
            "total_bars": total_bars,
            "progress": round(min(1.0, self.clock / total_bars), 4) if total_bars > 0 else 0.0,
        }

    def step_with_snapshot(self, d=1.0):
        bf = {c: len(stocks) for c, stocks in self._sim_node_stocks.items()}
        eb = len(self.event_log)
        self.step(d)
        af = {c: len(stocks) for c, stocks in self._sim_node_stocks.items()}
        ch = {
            c: {
                "before": bf.get(c, 0),
                "after": af.get(c, 0),
                "delta": af.get(c, 0) - bf.get(c, 0),
            }
            for c in set(list(bf) + list(af))
            if bf.get(c, 0) != af.get(c, 0)
        }
        new_events = []
        for e in self.event_log[eb:][-10:]:
            # I87：_event_queue 全 dict（I61 asdict 派生），ExecutionEvent 类已删除，
            d = e.get("details") or {}
            # I85：消费者 schema 收敛——flow_from/flow_to 加 pool_id fallback，
            new_events.append(
                {
                    "timestamp": e.get("time", self.clock),
                    "flow_id": d.get("flow_id", ""),
                    "flow_from": d.get("source_id", "") or e.get("pool_id", ""),
                    "flow_to": d.get("target_id", "") or e.get("pool_id", ""),
                    "action": e.get("event_type", ""),
                    "stocks_passed": 1 if e.get("code") else 0,
                    "mode": d.get("mode", ""),
                    "detail": d,
                }
            )
        total_bars = 19800
        return {
            "clock": self.clock,
            "clock_str": self._ft(self.clock),
            "pool_changes": ch,
            "new_events": new_events,
            "new_event_count": len(self.event_log) - eb,
            "current_index": int(self.clock),
            "total_bars": total_bars,
            "progress": round(min(1.0, self.clock / total_bars), 4) if total_bars > 0 else 0.0,
        }

    def get_timeline_plan(self):
        lb = self._sc2.get("begin_type_labels", {})
        edges = self.pool_config.get("edges", [])
        plan = []
        for i, f in enumerate(edges):
            # 对象属性 / 字典字段统一读取（合并 hasattr vs dict.get 双分支）
            from_cell = _obj_field(f, "from_cell_id", ("from", "from_cell_id"), "")
            to_cell = _obj_field(f, "to_cell_id", ("to", "to_cell_id"), "")
            begin_type = _obj_field(f, "begin_type", ("begin_type", "begin"), 0)
            begin_param = _obj_field(f, "begin_param", ("begin_param", "begint"), 0)
            end_type = _obj_field(f, "end_type", ("end_type", "end"), 0)
            interval_sec = _obj_field(f, "interval_sec", ("interval_sec", "interval"), 0)
            attr = _obj_field(f, "attr", ("attr",), 0)
            plan.append(
                {
                    "flow_id": i,
                    "from_cell": from_cell,
                    "to_cell": to_cell,
                    "begin_type": begin_type,
                    "begin_time": self._db(begin_type, begin_param, lb),
                    "end_type": end_type,
                    "interval": interval_sec,
                    "attr": attr,
                }
            )
        return plan

    def pause(self):
        self._pau = True

    def resume(self):
        self._pau = False
        if self._sim_step_spec is None and self._ini:
            self.start_auto()

    def start_auto(self):
        """启动自动步进：注册 sim_step TimedEventSpec 到 EventDriver heapq。"""
        with self._thread_lock:
            self._run = True
            self._pau = False
            if self._sim_step_spec is None:
                pe = self._engine._pool_engine
                ed = pe._components.get("event_driver") if pe is not None else None
                if ed is not None:
                    spec = TimedEventSpec(action=self._sim_step_action, params={"kind": "tick"},
                                          interval=1.0 / max(self.speed, 0.1),
                                          end_fn=lambda: float('inf') if self._run else 0.0)
                    loop = ed.get_loop()
                    if loop is not None:
                        ed.schedule_periodic(spec, loop, first_delay=0)
                    else:
                        logger.warning("EventDriver loop 未设置，sim 步进降级 heapq（wall-clock 不触发）")
                        ed.add_spec(spec, time.time())
                    self._sim_step_spec = spec
        logger.info("simulator auto-step started, speed=%.1fx", self.speed)

    def _sim_step_action(self, params, fire_time=None) -> None:
        """EventDriver 回调：推进虚拟时间一格（未运行/暂停时跳过，heapq 自然续程）。"""
        if not self._run or self._pau:
            return
        self.step(d=1.0)

    def stop(self):
        """停止仿真：取消步进 spec，重置状态。"""
        self._run = False
        self._pau = False
        with self._thread_lock:
            if self._sim_step_spec is not None:
                pe = self._engine._pool_engine
                ed = pe._components.get("event_driver") if pe is not None else None
                if ed is not None:
                    ed.cancel(self._sim_step_spec)
                self._sim_step_spec = None

    # ------------------------------------------------------------------
    # 时间表达解析（统一入口）
    # ------------------------------------------------------------------
    def _parse_target_seconds(self, ts):
        """将多种时间表达解析为相对当天 00:00:00 的秒数。"""
        if ts is None:
            raise ValueError("时间表达不能为空")
        s = str(ts).strip()
        if not s:
            raise ValueError("时间表达不能为空")
        # 1) 纯数字 → 直接当作秒数
        r = safe_float(s, None)
        if r is not None:
            return r
        # 2) 替换分隔符后解析
        normalized = s.replace("/", "-").replace("T", " ").replace("_", " ")
        # 3) "HH:MM:SS" 或 "HH:MM"
        if ":" in normalized and "-" not in normalized:
            parts = normalized.split(":")
            if len(parts) == 2:
                h, m = int(parts[0]), int(parts[1])
                return h * 3600 + m * 60
            if len(parts) == 3:
                h, m, sec = int(parts[0]), int(parts[1]), int(parts[2])
                return h * 3600 + m * 60 + sec
            raise ValueError(f"无法解析时间: {ts}")
        # 4) 日期+时间或仅日期
        import re
        m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{1,2})(?::(\d{1,2}))?)?$", normalized)
        if m:
            # 仅日期默认 09:30:00（A 股集合竞价起）
            hh = int(m.group(4)) if m.group(4) else 9
            mm = int(m.group(5)) if m.group(5) else 30
            ss = int(m.group(6)) if m.group(6) else 0
            return hh * 3600 + mm * 60 + ss
        raise ValueError(f"无法解析时间: {ts}")

    def jump_to(self, ts):
        """跳转到指定时间（秒，相对当天 00:00:00）。"""
        target = self._parse_target_seconds(ts)
        if target < 0:
            raise ValueError(f"目标时间不能为负: {ts}")
        # 已超过目标 → 直接重置到目标（前进方向）
        if self.clock > target:
            self.clock = target
        # 步进到目标
        safety = 0
        while self.clock < target and self._run:
            self.step(min(60, target - self.clock))
            safety += 1
            if safety > 100000:
                raise RuntimeError("jump_to 步数过多，可能进入死循环")

    @property
    def running(self):
        return self._run

    # ------------------------------------------------------------------
    # Mock generators / formatters (保留)
    # ------------------------------------------------------------------
    def _gs(self, code=None):
        g = self._gn
        code = code or self._gc()
        mkt = next(
            (
                r["market"]
                for r in g.get("market_detect_rules", [])
                if code.startswith(r["prefix"])
            ),
            "SH",
        )
        nm = next(
            (
                f"{n}{cs.index(code) + 1}"
                for n, cs in g.get("legacy_sectors", {}).items()
                if code in cs
            ),
            None,
        )
        if not nm:
            p = g.get("names_pool", [])
            nm = p[hash(code) % len(p)] if p else code
        pr = g.get("price_range", [5.0, 200.0])
        base = self.rng.uniform(*pr)
        cp = self.rng.gauss(0, g.get("change_pct_std", 3.0))
        price = base * (1 + cp / 100)
        vol = int(
            self.rng.lognormvariate(
                g.get("volume_lognorm_mu", 14), g.get("volume_lognorm_sigma", 2)
            )
        )
        hl = g.get("high_low_std", 0.01)
        hi = price * (1 + abs(self.rng.gauss(0, hl)))
        lo = price * (1 - abs(self.rng.gauss(0, hl)))
        return MockStock(
            code,
            nm,
            mkt,
            round(price, 2),
            round(cp, 2),
            vol,
            round(vol * price, 2),
            round(self.rng.gauss(0, g.get("ddx_std", 0.5)), 4),
            round(self.rng.gauss(0, g.get("bbd_std", 100.0)), 2),
            round(self.rng.uniform(*g.get("volume_ratio_range", [0.5, 5.0])), 2),
            round(self.rng.uniform(*g.get("turnover_range", [0.5, 15.0])), 2),
            round(hi, 2),
            round(lo, 2),
            round(lo + (hi - lo) * self.rng.random(), 2),
            round(price / (1 + cp / 100), 2),
        )

    def _gc(self):
        ms = self._gn.get("markets", {})
        for _ in range(1000):
            mk = self.rng.choice(list(ms.keys()))
            r = ms[mk].get("range", [600000, 605000])
            c = str(self.rng.randint(r[0], r[1]))
            if c not in self._cds:
                self._cds.add(c)
                return c
        fb = str(self.rng.randint(100000, 999999))
        self._cds.add(fb)
        return fb

    def _ft(self, s):
        s = max(0, s)
        return f"{int(s // 3600):02d}:{int((s % 3600) // 60):02d}:{int(s % 60):02d}"

    def _db(self, bt, bp, lb=None):
        """显示 begin_type 标签：按 timing.json:simulator.begin_type_formatters
        查表决定格式化方式（label_only / label_with_offset / hhmmss），
        避免硬编码 if bt == 7 分支。"""
        lb = lb or self._sc2.get("begin_type_labels", {})
        formatters = self._sc2.get("begin_type_formatters", {})
        b = lb.get(str(bt), f"类型{bt}")
        fmt = formatters.get(str(bt), "label_only")
        if fmt == "hhmmss" and bp > 0:
            return f"{(bp // 10000):02d}:{((bp // 100) % 100):02d}:{bp % 100:02d}"
        if bp > 0:
            return f"{b}{bp}秒"
        return b


# RuntimeMode 模块统一入口
class RuntimeModeModule(_BaseModule):
    """RuntimeMode 模块：实盘/回放/仿真三模式。仅与 EventBus 交互。"""

    def __init__(self, bus: EventBus, config: Optional[Dict[str, Any]] = None) -> None:
        self._bus = bus
        self._config = config or {}
        # 加载配置表（Task 9.6: 通过 ConfigStore.get_table 加载，参与热加载）
        _cs = get_global_config_store()
        self._runtime_modes = _cs.get_table(_CONFIG_FILES["runtime_modes"]) if _cs else {}
        self._time_sources = _cs.get_table(_CONFIG_FILES["time_sources"]) if _cs else {}
        self._trade_interfaces = _cs.get_table(_CONFIG_FILES["trade_interfaces"]) if _cs else {}
        # 当前模式
        self._current_mode: str = "live"
        # Task 24+：attach_replay_engine / attach_simulator 已删除，
        self._virtual_clock: float = 0.0
        # 仿真速度倍率
        self._sim_speed: float = 1.0
        # 仿真自动步进开关
        self._sim_auto_step: bool = False
        # 仿真步进计数器
        self._sim_step_idx: int = 0
        # 回放会话信息
        self._replay_session: Dict[str, Any] = {}
        self.register_subscribers()

    # ------------------------------------------------------------------
    # 内部辅助

    _SUBSCRIPTIONS: ClassVar[List[Tuple[type, str]]] = [
        (TickReceived, "_on_tick_received"),
    ]

    # ------------------------------------------------------------------
    # SubTask 13.2：模式切换发布 ModeChanged 事件
    # ------------------------------------------------------------------
    def switch_mode(self, mode_id: str) -> None:
        """切换运行模式，发布 ``ModeChanged`` 事件。"""
        try:
            modes = self._runtime_modes.get("modes", {}) if isinstance(
                self._runtime_modes, dict
            ) else {}
            if mode_id not in modes:
                # ConfigStore 未初始化时 modes 为空，放行 3 种已知模式
                if not modes and mode_id in ("live", "replay", "simulation"):
                    pass
                else:
                    logger.warning("Unknown mode: %s", mode_id)
                    return
            prev = self._current_mode
            self._current_mode = mode_id
            # 模式切换时重置仿真/回放运行态
            self._sim_auto_step = False
            # 发布 ModeChanged 事件（所有模块订阅并重置自身状态）
            self._bus.publish(ModeChanged(mode_id=mode_id, prev_mode=prev))
            logger.info("RuntimeMode 切换: %s -> %s", prev, mode_id)
        except Exception as ex:
            logger.warning("RuntimeMode switch_mode 失败: %s", ex)

    # Task 24+：attach_replay_engine / attach_simulator 已完全删除。

    # ------------------------------------------------------------------
    # SubTask 13.3：实盘模式发布 TimeAdvanced 事件
    # ------------------------------------------------------------------
    def _on_tick_received(self, event: TickReceived) -> None:
        """实盘模式下收到 tick 推进时间，发布 ``TimeAdvanced`` 事件。"""
        if self._current_mode != "live":
            return
        try:
            # wall_clock 时间源
            ts = event.ts or time.time()
            self._bus.publish(TimeAdvanced(ts=ts, source="wall_clock"))
        except Exception as ex:
            logger.warning("RuntimeMode time advance failed: %s", ex)

    # ------------------------------------------------------------------
    # SubTask 13.4：回放模式发布 ReplayStarted/ReplayStep 事件
    # ------------------------------------------------------------------
    def start_replay(
        self,
        session_id: str,
        start_ts: float,
        end_ts: float,
        codes: List[str],
    ) -> None:
        """启动回放会话，发布 ``ReplayStarted`` 事件。

        Task 24+：本方法仅发布 ``ReplayStarted`` 事件通知下游；
        ``KLineReplayEngine`` 通过订阅 ``ReplayStarted`` 事件触发首次 K 线
        加载（Item 3），``TickBar`` 订阅 ``ReplayStep`` 生成 tick。
        """
        if self._current_mode != "replay":
            return
        try:
            session = {
                "session_id": session_id,
                "start_ts": start_ts,
                "end_ts": end_ts,
                "codes": list(codes) if codes else [],
            }
            self._replay_session = session
            self._bus.publish(ReplayStarted(session=session))
            logger.info("ReplayStarted: session=%s codes=%d", session_id, len(codes))
        except Exception as ex:
            logger.warning("start_replay 失败: %s", ex)

    def step_replay(self, step_idx: int = 0) -> None:
        """回放单步：发布 ``ReplayStep`` 事件（纯事件驱动，不持有引擎引用）。

        Task 24+：``self._replay_engine`` 引用已删除。本方法仅发布 ``ReplayStep``
        事件通知下游；``KLineReplayEngine`` 通过订阅 ``ReplayStep`` 事件推进
        内部状态并发布 ``DataChanged`` 事件回送实际 bar 数据（Item 3）。
        ``ts`` / ``bar`` 为占位值，实际数据由 ``DataChanged`` 事件携带。
        """
        if self._current_mode != "replay":
            return
        try:
            step = {
                "step_idx": step_idx,
                "ts": 0.0,
                "bar": {},
            }
            session_id = str(self._replay_session.get("session_id", ""))
            self._bus.publish(ReplayStep(step=step, session_id=session_id))
        except Exception as ex:
            logger.warning("step_replay 失败: %s", ex)

    # ------------------------------------------------------------------
    # SubTask 13.5：仿真模式发布 SimulationStep 事件 + 速度调节
    # ------------------------------------------------------------------
    def step_simulation(self, step_idx: int = 0) -> None:
        """仿真单步：推进虚拟时钟并发布 ``SimulationStep`` 事件（纯事件驱动）。

        Task 24+：``self._simulator`` 引用已删除。本方法仅推进本地虚拟时钟
        并发布 ``SimulationStep`` 事件通知下游；``RuntimeSimulator`` 由各自
        创建方（app.py / api.py）直接驱动其 ``step()`` 方法，
        并在内部发布自己的 ``SimulationStep`` 事件。
        """
        if self._current_mode != "simulation":
            return
        try:
            # 推进虚拟时钟（按 speed 调节）
            # 1x = 1秒, 2x = 0.5秒, 0.5x = 2秒
            interval_sec = 1.0 / self._sim_speed
            self._virtual_clock += interval_sec
            step = {
                "step_idx": step_idx,
                "virtual_ts": self._virtual_clock,
                "interval": interval_sec,
                "events_count": 0,
            }
            self._bus.publish(SimulationStep(step=step, session_id=""))
        except Exception as ex:
            logger.warning("step_simulation 失败: %s", ex)

    def set_simulation_speed(self, speed: float) -> None:
        """设置仿真速度倍率（0.5x~20x）。"""
        try:
            self._sim_speed = max(_SIM_SPEED_MIN, min(_SIM_SPEED_MAX, float(speed)))
            logger.info("仿真速度设置为: %.2fx", self._sim_speed)
        except Exception as ex:
            logger.warning("set_simulation_speed 失败: %s", ex)

    def start_auto_step(self) -> None:
        """启动自动步进。"""
        try:
            self._sim_auto_step = True
            self._sim_step_idx = 0
        except Exception as ex:
            logger.warning("start_auto_step 失败: %s", ex)

    def stop_auto_step(self) -> None:
        """停止自动步进。"""
        try:
            self._sim_auto_step = False
        except Exception as ex:
            logger.warning("stop_auto_step 失败: %s", ex)

    # ------------------------------------------------------------------
    # 查询接口（供 API 层读取当前状态，非事件路径）
    # ------------------------------------------------------------------
    @property
    def current_mode(self) -> str:
        """返回当前模式 ID。"""
        return self._current_mode

    @property
    def virtual_clock(self) -> float:
        """返回仿真虚拟时钟。"""
        return self._virtual_clock

    @property
    def simulation_speed(self) -> float:
        """返回仿真速度倍率。"""
        return self._sim_speed

    @property
    def is_auto_stepping(self) -> bool:
        """返回是否处于自动步进状态。"""
        return self._sim_auto_step

    def get_mode_config(self, mode_id: Optional[str] = None) -> Dict[str, Any]:
        """返回指定模式配置；``mode_id`` 为 None 时返回当前模式配置。"""
        try:
            target = mode_id or self._current_mode
            modes = self._runtime_modes.get("modes", {}) if isinstance(
                self._runtime_modes, dict
            ) else {}
            return dict(modes.get(target, {}))
        except Exception as ex:
            logger.warning("get_mode_config 失败: %s", ex)
            return {}

    def get_replay_session(self) -> Dict[str, Any]:
        """返回当前回放会话信息。"""
        return dict(self._replay_session)


# === 合并自 core/runtime.py ===

# SubTask 29.6: _hash_tick 已上移至文件顶部（engine import 之前），此处删除原重复定义。


# 15 张运行时表名（按 ARCHITECTURE_FINAL.md 收敛；I13 新增 prev_tick 供 TickTable 双周期视图；
_TABLE_NAMES: frozenset[str] = frozenset({
    "node_stocks",
    "latest_tick",
    "prev_tick",
    "bars",
    "node_snapshots",
    "topology",
    "post_tick_results",
    "alert_cooldown",
    "time_source",
    "data_source",
    "trade_interface",
    "side_effects_scope",
    "replay",
    "simulator",
    "bars_history",
})


@dataclass
class DirtyState:
    """脏标记对象：合并原 ``_dirty_nodes`` 与 ``_data_dirty`` 两张表。"""

    nodes: Dict[str, bool] = field(default_factory=dict)
    data: bool = False
    changed_codes: Set[str] = field(default_factory=set)

    @property
    def node_dirty(self) -> Dict[str, bool]:
        return self.nodes

    @property
    def data_dirty(self) -> bool:
        return self.data


class StatePoolView:
    """状态池视图：脏股票在 tick 表变更列（state.dirty.changed_codes），本类是访问接口。"""

    def __init__(self, state: 'PoolState', nid: str):
        self._state = state
        self._nid = nid

    def get_stocks(self) -> List[Any]:
        return list(self._state.node_stocks.get(self._nid, []))

    def get_stock_codes(self) -> Set[str]:
        return {_stock_code(s) for s in self._state.node_stocks.get(self._nid, [])}

    def get_dirty_codes(self) -> Set[str]:
        return set(self._state.get_changed_codes()) & self.get_stock_codes()

    def add_stocks(self, stocks: List[Any]) -> None:
        self._state.node_stocks.setdefault(self._nid, []).extend(stocks)
        self._state.add_changed_codes({_stock_code(s) for s in stocks})

    def remove_stocks(self, codes: List[Any]) -> None:
        code_set = {_stock_code(c) for c in codes}
        self._state.node_stocks[self._nid] = [
            s for s in self._state.node_stocks.get(self._nid, [])
            if _stock_code(s) not in code_set
        ]
        self._state.add_changed_codes(code_set)


class PoolStateMixin:
    """PoolState 表级访问方法集合。"""

    def _populate_tables(self) -> None:
        """初始化 15 张运行时表容器。"""
        self._tables = {name: {} for name in _TABLE_NAMES}

    def _build_topology(self) -> None:
        """根据 pool_config 的 nodes/edges 预建 topology 邻接表。"""
        cfg = self.pool_config
        node_ids = {n["id"] for n in cfg.get("nodes", []) if isinstance(n, dict) and "id" in n}
        self.topology = _build_adjacency(node_ids, cfg.get("edges", []), lambda e: e.get("source") or e.get("from") or e.get("sid") if isinstance(e, dict) else None, lambda e: e.get("id") or e.get("flow_id") if isinstance(e, dict) else None)

    def __getattr__(self, name: str) -> Any:
        if name in _TABLE_NAMES:
            return self._tables[name]
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    def __setattr__(self, name: str, value: Any) -> None:
        if name in _TABLE_NAMES and hasattr(self, "_tables"):
            self._tables[name] = value
        else:
            object.__setattr__(self, name, value)

    # ------------------------------------------------------------------
    # 边级运行时表代理（由 EdgeState 持有）
    # ------------------------------------------------------------------
    @property
    def exec_ctx(self) -> Dict[str, Dict[str, Any]]:
        return self.edge_state.exec_ctx

    @property
    def formula_results(self) -> Dict[Tuple[Any, str], Any]:
        return self.edge_state.formula_results

    @property
    def filter_inputs(self) -> Dict[str, frozenset]:
        return self.edge_state.filter_inputs

    # ------------------------------------------------------------------
    # node_stocks（G4：通过 StatePoolView 视图访问，删除扁平接口）
    # ------------------------------------------------------------------
    def get_pool(self, nid: str) -> StatePoolView:
        return StatePoolView(self, nid)

    # ------------------------------------------------------------------
    # latest_tick（行情唯一真相源）
    # ------------------------------------------------------------------
    def get_latest_tick(self) -> Dict[str, Any]:
        return self.latest_tick

    def bar_hash(self) -> str:
        """返回 ``latest_tick`` 顶层 ``_hash``（缓存键 / 事件 payload）；缺失返回空串。"""
        return self.latest_tick.get("_hash", "")

    def update_latest_tick(self, tick_data: Optional[Dict[str, Any]]) -> bool:
        """刷新 latest_tick，自动计算 hash 与水位线 _ts。"""
        if not tick_data:
            return False
        now = time_at(state=self)
        normalized: Dict[str, Any] = {}
        for code, raw in tick_data.items():
            if isinstance(code, str) and code.startswith("_"):
                # 顶层元数据键（如 _hash/_ts）跳过——由本方法重新计算
                continue
            if not isinstance(raw, dict):
                continue
            tick = dict(raw)
            tick["code"] = str(code)
            if "_ts" not in tick:
                tick["_ts"] = now
            tick["_hash"] = _hash_tick(tick)
            normalized[str(code)] = tick
        if not normalized:
            return False
        new_hash = self._hash_tick_data(normalized)
        if self.bar_hash() == new_hash:
            return False
        self.latest_tick.clear()
        self.latest_tick.update(normalized)
        self.latest_tick["_hash"] = new_hash
        self.latest_tick["_ts"] = now
        # Task 2: sync tick_table (normalized has no _ metadata keys)
        self.tick_table.update(normalized)
        self.mark_data_dirty()
        return True

    @staticmethod
    def _hash_tick_data(tick_data: Dict[str, Any]) -> str:
        """对行情数据做聚合 hash——委托 core._hashing.hash_tick_aggregate。"""
        def _per_code(code: Any, tick: Dict[str, Any]) -> str:
            per_hash = tick.get("_hash")
            if not per_hash:
                tick_copy = dict(tick)
                tick_copy.setdefault("code", str(code))
                per_hash = _hash_tick(tick_copy)
            return per_hash
        return hash_tick_aggregate(tick_data, _per_code)

    # ------------------------------------------------------------------
    # dirty 标记
    # ------------------------------------------------------------------
    def mark_node_dirty(self, nid: str) -> None:
        self.dirty.nodes[nid] = True

    def mark_data_dirty(self) -> None:
        self.dirty.data = True

    def add_changed_codes(self, codes) -> None:
        """记录本 tick 有数据变化的股票代码（Tick/Bar 更新）。"""
        for c in codes:
            if c:
                self.dirty.changed_codes.add(str(c))

    def get_changed_codes(self):
        return self.dirty.changed_codes

    def is_node_dirty(self, nid: str) -> bool:
        return self.dirty.nodes.get(nid, False)

    def is_data_dirty(self) -> bool:
        return self.dirty.data

    def clear_dirty(self) -> None:
        self.dirty.nodes.clear()
        self.dirty.data = False
        self.dirty.changed_codes.clear()

    # ------------------------------------------------------------------
    # exec_ctx —— 委托给 EdgeState
    # ------------------------------------------------------------------
    def get_exec_ctx(self, eid: str) -> Dict[str, Any]:
        return self.edge_state.get_exec_ctx(eid)

    def set_exec_ctx_fired(self, eid: str, now: Optional[float] = None) -> None:
        self.edge_state.set_exec_ctx_fired(eid, now=now)

    # ------------------------------------------------------------------
    # formula_results —— 委托给 EdgeState
    # ------------------------------------------------------------------
    def get_formula_result(self, formula_ref: Any, bar_hash: str) -> Any:
        return self.edge_state.get_formula_result(formula_ref, bar_hash)

    def set_formula_result(self, formula_ref: Any, bar_hash: str, result: Any) -> None:
        self.edge_state.set_formula_result(formula_ref, bar_hash, result)

    # ------------------------------------------------------------------
    # node_snapshots
    # ------------------------------------------------------------------
    def snapshot_nodes(self) -> Dict[str, frozenset]:
        """将当前 node_stocks 聚合为 ``{nid: frozenset(code)}`` 并保存。"""
        snapshots: Dict[str, frozenset] = {}
        for nid, stocks in self.node_stocks.items():
            snapshots[nid] = self._snapshot_stocks(stocks)
        self.node_snapshots.update(snapshots)
        return snapshots

    def restore_snapshots(self) -> Dict[str, List[Dict[str, Any]]]:
        """从 node_snapshots 还原 node_stocks（仅保留 code 字段）。"""
        restored: Dict[str, List[Dict[str, Any]]] = {}
        for nid, codes in self.node_snapshots.items():
            restored[nid] = [{"code": code} for code in codes]
        self.node_stocks = restored
        return restored

    @staticmethod
    def _snapshot_stocks(stocks: List[Any]) -> frozenset:
        codes = set()
        for s in stocks:
            if isinstance(s, dict):
                code = s.get("code")
                if code is not None:
                    codes.add(str(code))
            elif s is not None:
                codes.add(str(s))
        return frozenset(codes)

    # ------------------------------------------------------------------
    # time_source / 三模式配置行
    def set_time_source(self, ts_config: Dict[str, Any]) -> None:
        self.time_source = ts_config or {}

    def set_data_source(self, ds_config: Dict[str, Any]) -> None:
        self.data_source = ds_config or {}

    def set_trade_interface(self, ti_config: Dict[str, Any]) -> None:
        self.trade_interface = ti_config or {}

    def set_side_effects_scope(self, se_config: Dict[str, Any]) -> None:
        self.side_effects_scope = se_config or {}

    def set_state_scope(self, cfg: Dict[str, Any]) -> None:
        """根据模式配置的 state_scope 字段切换回放/实盘状态作用域。"""
        if cfg.get("state_scope", "live") == "replay":
            self.enter_replay()
        elif self.is_replay_active():
            self.exit_replay()

    # ------------------------------------------------------------------
    # 回放状态隔离
    # ------------------------------------------------------------------
    def _snapshot_edge_state(self) -> Dict[str, Any]:
        """快照当前边级状态。"""
        return self.edge_state.snapshot()

    def _fresh_edge_state(self) -> Dict[str, Any]:
        """创建全新的回放边级状态副本。"""
        return {
            "exec_ctx": {},
            "formula_results": {},
            "filter_inputs": {},
        }

    def enter_replay(self) -> None:
        """进入回放模式：快照实盘状态并切换到回放副本。"""
        self.replay["live_node_stocks"] = copy.deepcopy(self.node_stocks)
        self.replay["live_edge_state"] = self._snapshot_edge_state()
        self.replay["live_node_snapshots"] = copy.deepcopy(self.node_snapshots)
        self.replay["live_dirty"] = copy.deepcopy(self.dirty)
        self.replay["live_first_run"] = self.first_run
        self.replay["node_stocks"] = copy.deepcopy(self.node_stocks)
        self.replay["edge_state"] = EdgeState()
        self.replay["node_snapshots"] = {}
        self.replay["dirty"] = DirtyState()
        self.replay["first_run"] = True
        self.replay["active"] = True
        self._swap_to_replay()

    def exit_replay(self) -> None:
        """退出回放模式：恢复实盘 ``node_stocks`` 与边级状态。"""
        live_node_stocks = self.replay.get("live_node_stocks", {})
        live_edge_state = self.replay.get("live_edge_state", {})
        self.node_stocks = live_node_stocks
        self.node_snapshots = self.replay.get("live_node_snapshots", {})
        self.dirty = self.replay.get("live_dirty", DirtyState())
        self.first_run = self.replay.get("live_first_run", self.first_run)
        self.edge_state.restore(live_edge_state)
        self.replay["active"] = False

    def _swap_to_replay(self) -> None:
        """将运行时装态切换到回放副本。"""
        self.node_stocks = self.replay["node_stocks"]
        self.node_snapshots = self.replay.get("node_snapshots", {})
        self.dirty = self.replay.get("dirty", DirtyState())
        self.first_run = self.replay.get("first_run", True)
        self.edge_state = self.replay.get("edge_state", EdgeState())

    def is_replay_active(self) -> bool:
        return bool(self.replay.get("active"))


class PoolState(PoolStateMixin):
    """池级运行时表真相源。"""

    def __init__(self, pool_config: Optional[Dict[str, Any]] = None) -> None:
        self.pool_config = pool_config or {}
        self._tables = {}
        self.dirty = DirtyState()
        self.edge_state = EdgeState()
        self.first_run = True
        self._populate_tables()
        self._build_topology()
        # Task 2: TickTable coexists with latest_tick (dual-write).
        self.tick_table = TickTable()


# TickTable 已抽离至 core.tick_table（共享值对象），顶部 import 保持向后兼容导出


__all__ = ["RuntimeModeModule", "KLineReplayEngine", "RuntimeSimulator", "DirtyState", "PoolState"]
