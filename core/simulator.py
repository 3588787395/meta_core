"""DZH股票池运行时模拟器 — 表驱动版本
时序→timing.json | Mock数据→mock_data.json | 传输模式由 schemas.py + engine.py 统一解析"""
from __future__ import annotations
import asyncio
import heapq
import logging
import random
import threading
import time
import tracemalloc
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from collections import defaultdict

from .schemas import PoolMetaModel, FlowAttrBitsModel, StatePoolCellModel, ConditionCellModel, StockSnapshotModel

from .engine import MetaEngine

logger = logging.getLogger(__name__)


def _hm(v):
    return ((v // 10000) % 100, (v // 100) % 100, v % 100)


def _fid(x):
    return int(x) if x.isdigit() else hash(x) % 10000


def _scode(s):
    if isinstance(s, dict):
        return s.get("code", s.get("label", ""))
    return str(s)


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


class RuntimeSimulator:
    def __init__(self, pool_model: Any, seed: Optional[int] = None, engine: Optional[MetaEngine] = None):
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
        self._engine = engine if engine is not None else MetaEngine()
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
        self._tick_queue: List[tuple] = []
        self._tick_intervals: Dict[str, int] = {}
        self._tick_codes_initialized: bool = False

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
            nodes = []
            for n in config.get("cells", []):
                if hasattr(n, "to_dict"):
                    n = n.to_dict()
                nodes.append(n)
            edges = []
            for e in config.get("flows", []):
                if hasattr(e, "to_dict"):
                    e = e.to_dict()
                edges.append(e)
            config["nodes"] = nodes
            config["edges"] = edges
            config.pop("cells", None)
            config.pop("flows", None)
            return config
        return {
            "nodes": [c.to_dict() for c in pool.cells],
            "edges": [f.to_dict() for f in pool.flows],
        }

    # ------------------------------------------------------------------
    # Async helper
    # ------------------------------------------------------------------
    def _run_coro(self, coro):
        """复用持久事件循环，避免每步 new_event_loop 开销"""
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None

        if running is None:
            # 无运行中的循环 — 复用持久循环
            if self._sim_loop is None or self._sim_loop.is_closed():
                self._sim_loop = asyncio.new_event_loop()
            return self._sim_loop.run_until_complete(coro)

        if not running.is_running():
            return running.run_until_complete(coro)

        # 已有运行中的循环（如 Jupyter）— 在线程中执行
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
                try:
                    price = float(v)
                    break
                except (TypeError, ValueError):
                    continue
        if not price:
            for k in ("current_price", "entry_price"):
                v = tracker.get(k)
                if v:
                    try:
                        price = float(v)
                        break
                    except (TypeError, ValueError):
                        continue
        rise = d.get("rise", d.get("change_pct"))
        if rise in _INVALID:
            entry = tracker.get("entry_price", 0)
            if entry and price:
                rise = (price - entry) / entry * 100
            else:
                rise = 0
        try:
            change_pct = float(rise or 0)
        except (TypeError, ValueError):
            change_pct = 0.0
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
            extra={
                k: v
                for k, v in d.items()
                if k
                not in {
                    "code",
                    "name",
                    "label",
                    "close",
                    "price",
                    "p",
                    "now",
                    "inprice",
                    "rise",
                    "market",
                    "change_pct",
                    "volume",
                    "amount",
                    "ddx",
                    "bbd",
                    "volume_ratio",
                    "turnover",
                    "high",
                    "low",
                    "open",
                    "open_price",
                    "pre_close",
                    "_tracker",
                }
            },
        )

    def _all_node_ids(self):
        ids = set()
        for n in self.pool_config.get("nodes", []):
            nid = n.get("id")
            if nid:
                ids.add(nid)
        for e in self.pool_config.get("edges", []):
            fid = (
                e.get("from")
                or e.get("from_cell_id")
                or e.get("source", {}).get("node_id", "")
            )
            tid = (
                e.get("to")
                or e.get("to_cell_id")
                or e.get("target", {}).get("node_id", "")
            )
            if fid:
                ids.add(fid)
            if tid:
                ids.add(tid)
        return ids

    def _generate_mock_bar_data(self):
        # 表驱动：候选股票 codes 取自 (a) 当前所有 node_stocks 残留 + (b) market_source 节点
        # 的 markets 配置 — 避免 move 后 n1 空导致 bar_data 空、股票池不可恢复
        codes = set()
        if self._mode_state:
            for stocks in self._mode_state.get("node_stocks", {}).values():
                for s in stocks:
                    codes.add(_scode(s))
        # 补足：从 market_source 节点读取 markets，解析出全市场代码作为后备候选
        for n in self.pool_config.get("nodes", []):
            if n.get("type") == "market_source":
                p = n.get("params", {}) or {}
                markets = p.get("markets") or p.get("attrtext") or []
                if isinstance(markets, str):
                    markets = [m.strip() for m in markets.replace('\t', ',').split(',') if m.strip()]
                if not markets:
                    markets = ["sh_a", "sz_a"]
                for m in markets:
                    try:
                        codes.update(self._markets_to_codes(m))
                    except Exception:
                        pass
        if not codes:
            return {}
        # 为所有 node_stocks 中的代码生成 bar_data，确保边转移时能获取价格；
        # market_source 补充代码限量 200 避免性能问题
        node_codes = set()
        if self._mode_state:
            for stocks in self._mode_state.get("node_stocks", {}).values():
                for s in stocks:
                    node_codes.add(_scode(s))
        market_codes = codes - node_codes
        if len(market_codes) > 200:
            market_codes = set(self.rng.sample(list(market_codes), 200))
        codes_list = sorted(node_codes | market_codes)
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
            pfx, sfx = it.get("prefix", ""), it.get("suffix", "SH")
            rs, re_ = it.get("range_start", 0), it.get("range_end", 9)
            for n in range(rs, re_ + 1):
                if sfx:
                    codes.add("%s%03d.%s" % (pfx, n, sfx))
                else:
                    codes.add("%s%06d" % (pfx, n))
        return codes

    def _generate_tick_from_queue(self) -> dict:
        """从优先队列弹出到时股票，生成 per-stock tick，推回队列。"""
        if not self._tick_queue:
            return {}
        bar_data = {}
        while self._tick_queue and self._tick_queue[0][0] <= self.clock:
            _, code = heapq.heappop(self._tick_queue)
            bar_data[code] = self._generate_single_tick(code)
            interval = self._tick_intervals.get(code, 5)
            heapq.heappush(self._tick_queue, (self.clock + interval, code))
        return bar_data

    def _generate_single_tick(self, code: str) -> dict:
        """基于前一根 close 加随机波动生成单只股票 tick。"""
        pe = self._engine._pool_engine
        prev_close = 0.0
        if pe is not None:
            prev_tick = pe.state.latest_tick.get(code)
            if isinstance(prev_tick, dict):
                prev_close = float(prev_tick.get("close", 0))
        pr = self._gn.get("price_range", [5.0, 200.0])
        if prev_close <= 0:
            prev_close = self.rng.uniform(*pr)
        cp = self.rng.gauss(0, self._gn.get("change_pct_std", 3.0))
        price = prev_close * (1 + cp / 100)
        price = max(price, 0.01)
        return {
            "close": round(price, 2),
            "open": round(price * (1 + self.rng.gauss(0, 0.01) / 100), 2),
            "high": round(price * (1 + abs(self.rng.gauss(0, 0.01)) / 100), 2),
            "low": round(price * (1 - abs(self.rng.gauss(0, 0.01)) / 100), 2),
            "pre_close": round(prev_close, 2),
            "volume": int(
                self.rng.lognormvariate(
                    self._gn.get("volume_lognorm_mu", 14),
                    self._gn.get("volume_lognorm_sigma", 2),
                )
            ),
            "_ts": self.clock,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def _sync_stock_prices(self, bar_data: dict):
        """用 bar_data 更新 node_stocks 中股票字典的价格字段。

        仿真模式下源节点股票字典初始化时只有 code/name（无价格），
        边转移后目标节点通过 _tracker 获取价格，但源节点本身仍无价格字段。
        此方法在每 step 用生成的 bar_data 同步价格到所有 node_stocks 字典，
        使 _dict_to_mock 的 close 字段优先级能直接命中。
        """
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
                s["volume"] = bar.get("volume", s.get("volume", 0))

    def _seed_market_source_stocks(self):
        """仿真模式下为 market_source 节点填充 mock 候选股票。

        market_source 节点 stocks 为空（依赖实时市场数据），仿真模式需从
        mock_data.json 的 market_scopes 生成候选代码写入 node_stocks，
        否则源节点为空导致边执行无股票可转移、events=0。
        """
        node_stocks = self._mode_state.get("node_stocks", {})
        pe = self._engine._pool_engine
        for n in self.pool_config.get("nodes", []):
            if n.get("type") != "market_source":
                continue
            nid = n.get("id", "")
            if node_stocks.get(nid):
                continue
            p = n.get("params", {}) or {}
            markets = p.get("markets") or p.get("attrtext") or []
            if isinstance(markets, str):
                markets = [m.strip() for m in markets.replace('\t', ',').split(',') if m.strip()]
            if not markets:
                markets = ["sh_a", "sz_a"]
            codes = set()
            for m in markets:
                try:
                    codes.update(self._markets_to_codes(m))
                except Exception:
                    pass
            stocks = [{"code": c, "name": c} for c in sorted(codes)]
            node_stocks[nid] = stocks
            if pe is not None:
                pe.state.set_node_stocks(nid, list(stocks))
            logger.info("seeded market_source %s with %d stocks", nid, len(stocks))

    def _init_tick_intervals(self):
        """为每只仿真股票分配 1-9s 随机 tick 间隔，初始化优先队列。"""
        if self._tick_codes_initialized:
            return
        codes = set()
        if self._mode_state:
            for stocks in self._mode_state.get("node_stocks", {}).values():
                for s in stocks:
                    codes.add(_scode(s))
        for code in sorted(codes):
            interval = self.rng.randint(1, 9)
            self._tick_intervals[code] = interval
            heapq.heappush(self._tick_queue, (self.clock + interval, code))
        self._tick_codes_initialized = True
        logger.info("initialized tick intervals for %d codes", len(codes))

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
        self._mode_state = self._run_coro(
            self._engine.run_mode("simulation", self.pool_config)
        )
        pe = self._engine._pool_engine
        if pe is not None:
            pe.state.time_source["start_ts"] = self.clock
        self._seed_market_source_stocks()
        self._init_tick_intervals()
        # run_mode 已通过 state.time_source 配置 virtual 时间源，无需再写 MetaEngine 字段。
        self._ini = True
        self._run = True
        logger.info("simulator initialized: time_source=virtual_clock, clock=%.1f", self.clock)

    def step(self, d=1.0):
        if self._pau:
            return []
        if not self._ini:
            self.initialize()
        self.clock += d
        tick_seq = self._perf_tick_count
        pe = self._engine._pool_engine
        if pe is not None:
            pe.state.time_source["current_ts"] = self.clock
            pe.state.time_source.setdefault("driver_type", "virtual")
        logger.info("tick=%d clock=%.1f (%s) step=%.1f",
                     tick_seq, self.clock, self._ft(self.clock), d)

        t_tick_start = time.perf_counter()

        t_mock_start = time.perf_counter()
        current_bar_data = self._generate_tick_from_queue()
        t_mock_ms = (time.perf_counter() - t_mock_start) * 1000.0
        self._perf_phases['mock_generate'].append(t_mock_ms)
        bar_count = len(current_bar_data)
        logger.info("tick=%d phase=mock_generate bar_count=%d duration_ms=%.2f",
                     tick_seq, bar_count, t_mock_ms)

        if not current_bar_data:
            current_bar_data = self._generate_mock_bar_data()
            if not current_bar_data:
                logger.warning("tick=%d degraded=True mock_bar_data empty", tick_seq)

        self._sync_stock_prices(current_bar_data)

        t_engine_start = time.perf_counter()
        self._mode_state["node_stocks"] = self._run_coro(
            self._engine._tick(
                self.pool_config,
                self._mode_state["node_stocks"],
                current_bar_data,
                self._mode_state,
            )
        )
        t_engine_ms = (time.perf_counter() - t_engine_start) * 1000.0
        self._perf_phases['engine_tick'].append(t_engine_ms)
        logger.info("tick=%d phase=engine_tick duration_ms=%.2f", tick_seq, t_engine_ms)

        # Phase: event collection
        t_evt_start = time.perf_counter()
        events = []
        while not self._engine._event_queue.empty():
            try:
                events.append(self._engine._event_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        while not self._engine._signal_queue.empty():
            try:
                sig = self._engine._signal_queue.get_nowait()
                sig["event_type"] = "Signal"
                events.append(sig)
            except asyncio.QueueEmpty:
                break
        t_evt_ms = (time.perf_counter() - t_evt_start) * 1000.0
        self._perf_phases['event_collect'].append(t_evt_ms)
        self.event_log.extend(events)

        t_tick_total = (time.perf_counter() - t_tick_start) * 1000.0
        self._perf_phases['tick_total'].append(t_tick_total)
        self._perf_tick_count += 1

        # Log node stock counts for observability
        ns = self._mode_state.get("node_stocks", {}) if self._mode_state else {}
        for nid, stocks in ns.items():
            if isinstance(stocks, list) and stocks:
                codes = [_scode(s) for s in stocks[:5]]
                logger.info("tick=%d node_id=%s stock_count=%d sample=%s",
                             tick_seq, nid, len(stocks), codes)

        if events:
            for ev in events:
                if isinstance(ev, dict):
                    action = ev.get('event_type', '')
                    # I84：消费者收敛——source_id/target_id/mode 在 details dict（event_rules.json
                    # detail_mapping），非顶层键。transferred_codes 不在 DomainEvent 中（per-code
                    # 事件，每事件 1 code），n=1 if code else 0。
                    d = ev.get('details') or {}
                    tgt = d.get('target_id', '') or d.get('source_id', '') or ev.get('pool_id', '')
                    n = 1 if ev.get('code') else 0
                    logger.info("tick=%d event action=%s target_id=%s stocks_passed=%d",
                                 tick_seq, action, tgt, n)

        logger.info("tick=%d done total_ms=%.2f events=%d",
                     tick_seq, t_tick_total, len(events))
        return events

    def run_to(self, t):
        ts = self._parse_target_seconds(t)
        r = []
        while self.clock < ts:
            r.extend(self.step(min(1.0, ts - self.clock)))
        return r

    def get_state(self, c):
        if not self._mode_state:
            return StatePool(c, [], self.clock, hold_seconds=self._default_hold_seconds)
        stocks = self._mode_state.get("node_stocks", {}).get(c, [])
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
        node_stocks = (
            self._mode_state.get("node_stocks", {}) if self._mode_state else {}
        )
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
        """返回仿真性能摘要字典。

        采集各阶段耗时（mock_generate / engine_tick / event_collect / tick_total）、
        总耗时、单 tick 均值、内存峰值。供 simtests/harness/driver.py 调用。
        """
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
        self.clock = 34500.0  # 从 09:30:00 开始（A 股开盘时间）
        self.event_log.clear()
        self.pools.clear()
        self._ini = False
        self._run = False
        self._pau = False
        self._mode_state = None
        self._engine = MetaEngine()
        self._perf_phases.clear()
        self._perf_tick_count = 0
        self._perf_start = time.perf_counter()
        self._perf_peak_mb = 0.0
        self.initialize()

    def get_state_snapshot(self):
        # 注：快照输出是运行时聚合，不属于核心循环，延后到输出规则系统统一设计
        node_stocks = (
            self._mode_state.get("node_stocks", {}) if self._mode_state else {}
        )
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
        node_stocks = (
            self._mode_state.get("node_stocks", {}) if self._mode_state else {}
        )
        bf = {c: len(stocks) for c, stocks in node_stocks.items()}
        eb = len(self.event_log)
        self.step(d)
        node_stocks = (
            self._mode_state.get("node_stocks", {}) if self._mode_state else {}
        )
        af = {c: len(stocks) for c, stocks in node_stocks.items()}
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
            # isinstance(e, dict) 检查 + else 分支已消除。
            # I84：消费者收敛——flow_id/source_id/target_id/mode 在 details dict
            # （event_rules.json detail_mapping），transferred_codes 不在 DomainEvent
            # 中（per-code 事件），stocks_passed=1 if code else 0。
            d = e.get("details") or {}
            # I85：消费者 schema 收敛——flow_from/flow_to 加 pool_id fallback，
            # 与 tick log（L482 3-way fallback）同构。ENTER details 有 source_id 无
            # target_id → flow_to 回退 pool_id（目标池）；EXIT details 有 target_id 无
            # source_id → flow_from 回退 pool_id（源池）；TIMEOUT details 两者皆无 →
            # 均回退 pool_id（过期池）。消除 new_events flow_from/flow_to 空值透传 HTTP。
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
            if hasattr(f, "from_cell_id"):
                from_cell = f.from_cell_id
                to_cell = f.to_cell_id
                begin_type = getattr(f, "begin_type", 0)
                begin_param = getattr(f, "begin_param", 0)
                end_type = getattr(f, "end_type", 0)
                interval_sec = getattr(f, "interval_sec", 0)
                attr = getattr(f, "attr", 0)
            else:
                from_cell = f.get("from", f.get("from_cell_id", ""))
                to_cell = f.get("to", f.get("to_cell_id", ""))
                begin_type = f.get("begin_type", f.get("begin", 0))
                begin_param = f.get("begin_param", f.get("begint", 0))
                end_type = f.get("end_type", f.get("end", 0))
                interval_sec = f.get("interval_sec", f.get("interval", 0))
                attr = f.get("attr", 0)
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

    # ------------------------------------------------------------------
    # 时间表达解析（统一入口）
    # ------------------------------------------------------------------
    def _parse_target_seconds(self, ts):
        """将多种时间表达解析为相对当天 00:00:00 的秒数。

        支持：
          - "HH:MM:SS" / "HH:MM"  →  当天该时间点的秒数
          - "YYYY-MM-DD"           →  默认 09:30:00 (A 股集合竞价起)
          - "YYYY-MM-DD HH:MM:SS" / "YYYY-MM-DDTHH:MM:SS" / "YYYY/MM/DD HH:MM:SS"
          - 纯数字                 →  视为秒数
          - 浮点数字符串           →  视为秒数
        """
        if ts is None:
            raise ValueError("时间表达不能为空")
        s = str(ts).strip()
        if not s:
            raise ValueError("时间表达不能为空")
        # 1) 纯数字 → 直接当作秒数
        try:
            return float(s)
        except (TypeError, ValueError):
            pass
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
