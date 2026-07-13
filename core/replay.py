import asyncio
import logging
import threading
import time
from datetime import datetime, time as dt_time, timedelta
from typing import Any, Dict, List, Optional

from .engine import MetaEngine, _safe_timestamp
from ..services.storage import Storage
from ..services.tq_adapter import DZH_COL_MAP
from ._market_utils import _stock_code

logger = logging.getLogger(__name__)

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


class KLineReplayEngine:
    def __init__(self, meta_engine: MetaEngine, storage: Storage = None):
        self._engine = meta_engine
        # 表驱动：设置 _current_mode_id 驱动 gate_evaluator / data_injector 路由（Task 16）
        self._engine._current_mode_id = 'replay'
        self._storage = storage or Storage()
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

        self._replay_thread: Optional[threading.Thread] = None
        self._thread_lock = threading.Lock()
        self._resume_event = threading.Event()
        self._resume_event.set()  # 初始非暂停态
        self._event_log: List[Dict] = []
        self._last_bar_events: List[Dict] = []
        self._replay_loop: Optional[asyncio.AbstractEventLoop] = None
        self._db_write_counter: int = 0

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
        end_dt: Optional[datetime] = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                end_dt = datetime.strptime(end, fmt)
                break
            except ValueError:
                continue

        # 对每个 symbol 调用 get_kline_series 获取 K 线
        self._bars = {}
        for code in codes:
            try:
                coro = kline_provider.get_kline_series(
                    code, period=klp_period, end_time=end_dt, count=10000
                )
                df = self._run_coro_sync(coro)
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
        self._mode_state = {'node_stocks': self._engine._init_node_stocks(nodes), 'inject': True}

        # run_mode 已通过 state.time_source 配置 sequence 时间源，无需再写 MetaEngine 字段。

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
            # 兼容前端格式：source/target可能是字符串ID
            if "source" not in edge and "startid" in edge:
                edge["source"] = {"node_id": str(edge["startid"])}
            elif "source" in edge:
                if isinstance(edge["source"], str):
                    edge["source"] = {"node_id": edge["source"]}
                elif isinstance(edge["source"], dict):
                    nid = edge["source"].get("node_id", "")
                    if nid:
                        edge["source"]["node_id"] = str(nid)

            if "target" not in edge and "endid" in edge:
                edge["target"] = {"node_id": str(edge["endid"])}
            elif "target" in edge:
                if isinstance(edge["target"], str):
                    edge["target"] = {"node_id": edge["target"]}
                elif isinstance(edge["target"], dict):
                    nid = edge["target"].get("node_id", "")
                    if nid:
                        edge["target"]["node_id"] = str(nid)

            if not edge.get("id"):
                src = edge.get("source", {}).get("node_id", "")
                tgt = edge.get("target", {}).get("node_id", "")
                if src and tgt:
                    edge["id"] = f"edge_{src}_{tgt}"

            params = edge.get("params", {})
            if isinstance(params, dict):
                if "tran" in params and "mode" not in params:
                    tran_val = params["tran"]
                    try:
                        tran_int = int(tran_val)
                    except (ValueError, TypeError):
                        tran_int = 0
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
        if self._base_period == "day":
            return
        target_periods = []
        if self._base_period == "5min":
            target_periods = ["15min", "30min", "60min", "day", "week", "month"]
        elif self._base_period == "1min":
            target_periods = ["5min", "15min", "30min", "60min", "day", "week", "month"]
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
        time_str = self._timeline[self._current_index]["time"]
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(time_str, fmt)
            except ValueError:
                continue
        return None

    def _run_coro_sync(self, coro):
        """复用持久事件循环，避免每步 new_event_loop 开销"""
        if self._replay_loop is None or self._replay_loop.is_closed():
            self._replay_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._replay_loop)
        try:
            return self._replay_loop.run_until_complete(coro)
        except RuntimeError:
            # 已在运行的循环中（如 Jupyter），降级到线程执行
            result = [None]
            exc = [None]
            def run_in_thread():
                try:
                    new_loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(new_loop)
                    result[0] = new_loop.run_until_complete(coro)
                except Exception as e:
                    exc[0] = e
                finally:
                    new_loop.close()
            t = threading.Thread(target=run_in_thread)
            t.start()
            t.join()
            if exc[0] is not None:
                raise exc[0]
            return result[0]

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
            pe.state.time_source.setdefault("driver_type", "sequence")

        coro = self._engine._tick(
            self._pool_model,
            self._mode_state['node_stocks'],
            current_bar_data,
            self._mode_state
        )
        self._mode_state['node_stocks'] = self._run_coro_sync(coro)

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

        if hasattr(self, '_session_id') and self._pool_id:
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
            self._resume_event.set()
            if self._replay_thread is None or not self._replay_thread.is_alive():
                self._replay_thread = threading.Thread(target=self._sync_play_loop, daemon=True)
                self._replay_thread.start()
        return {"success": True, "status": "playing", "speed": self._speed}

    def _sync_play_loop(self) -> None:
        base_interval = BASE_INTERVAL.get(self._base_period, 0.5)
        try:
            while True:
                if not self._playing:
                    break
                if self._paused:
                    self._resume_event.wait()
                    self._resume_event.clear()
                    continue
                if self._current_index >= self._total_bars - 1:
                    self._playing = False
                    self._paused = True
                    logger.info("回放结束: 已到达最后一根K线")
                    break
                self._do_step()
                interval = base_interval / self._speed if self._speed < 1000 else 0
                if interval > 0:
                    time.sleep(interval)
        finally:
            if self._replay_loop and not self._replay_loop.is_closed():
                self._replay_loop.close()
            self._replay_loop = None

    def pause(self) -> Dict:
        self._paused = True
        self._resume_event.clear()
        return {"success": True, "status": "paused"}

    def stop(self) -> Dict:
        self._playing = False
        self._paused = True
        self._resume_event.set()  # 唤醒可能阻塞在 wait() 的循环
        with self._thread_lock:
            if self._replay_thread and self._replay_thread.is_alive():
                self._replay_thread.join(timeout=2.0)
            self._replay_thread = None
        if self._replay_loop and not self._replay_loop.is_closed():
            self._replay_loop.close()
        self._replay_loop = None
        return {"success": True, "status": "stopped"}

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
                values = self._run_coro_sync(
                    market_data_port.get_market_scalars_batch(codes, field)
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
        try:
            enter_price = float(enter_price_raw) if enter_price_raw not in (None, '', '-') else 0.0
        except (TypeError, ValueError):
            enter_price = 0.0
        profit_pct = round((current_price - enter_price) / enter_price * 100, 2) if enter_price > 0 else 0
        max_profit = round(profit_pct * 1.2, 2) if enter_price > 0 else float(snap.get('max_profit', 0) or 0)

        # I45：表驱动行值——key→value 映射从 11 路 elif 链提取为 dict。
        # 前提：current_price/enter_price/profit_pct/max_profit 已在循环前预算
        # （lines 685-692），code/idx/snap/info 均为函数参数，故所有分支取值
        # 不依赖循环变量 cid，可一次性物化为 dict。O(1) 查找替代 O(n) elif 扫描。
        # 对比 tq_adapter.get_stock_table_data 的同类 elif 链：彼处分支含 try/except
        # + 多源回退 + 公式计算，无法预算物化，故彼处保持显式分支（已注释说明）。
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


# ═══════════════════════════════════════════════════════════════
# K线合成器（原 services/kline_synthesizer.py）
# ═══════════════════════════════════════════════════════════════

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


def _aggregate_bars(bars: List[Dict], n: int) -> List[Dict]:
    result: List[Dict] = []
    for i in range(0, len(bars), n):
        group = bars[i : i + n]
        if len(group) < n:
            break
        result.append({
            "time": group[0]["time"],
            "open": group[0]["open"],
            "close": group[-1]["close"],
            "high": max(b["high"] for b in group),
            "low": min(b["low"] for b in group),
            "volume": sum(b["volume"] for b in group),
            "amount": sum(b["amount"] for b in group),
        })
    return result


def synthesize_from_1min(bars: List[Dict], target_period: str) -> List[Dict]:
    n = _PERIOD_BARS_1MIN[target_period]
    return _aggregate_bars(bars, n)


def synthesize_from_5min(bars: List[Dict], target_period: str) -> List[Dict]:
    n = _PERIOD_BARS_5MIN[target_period]
    return _aggregate_bars(bars, n)


def _get_week_key(dt: datetime) -> str:
    monday = dt - timedelta(days=dt.weekday())
    return monday.strftime("%Y-%m-%d")


def _get_month_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


def _group_and_synthesize(bars: List[Dict], key_func) -> List[Dict]:
    groups: Dict[str, List[Dict]] = {}
    order: List[str] = []
    for bar in bars:
        dt = datetime.strptime(bar["time"], "%Y-%m-%d %H:%M:%S")
        key = key_func(dt)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(bar)

    result: List[Dict] = []
    for key in order:
        group = groups[key]
        result.append({
            "time": group[0]["time"],
            "open": group[0]["open"],
            "close": group[-1]["close"],
            "high": max(b["high"] for b in group),
            "low": min(b["low"] for b in group),
            "volume": sum(b["volume"] for b in group),
            "amount": sum(b["amount"] for b in group),
        })
    return result


def synthesize_from_daily(bars: List[Dict], target_period: str) -> List[Dict]:
    if target_period == "week":
        return _group_and_synthesize(bars, _get_week_key)
    if target_period == "month":
        return _group_and_synthesize(bars, _get_month_key)
    return []


def _synthesize_day_from_intraday(bars: List[Dict]) -> List[Dict]:
    def _day_key(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%d")

    result = _group_and_synthesize(bars, _day_key)
    for bar in result:
        dt = datetime.strptime(bar["time"], "%Y-%m-%d %H:%M:%S")
        bar["time"] = dt.strftime("%Y-%m-%d") + " 00:00:00"
    return result


def synthesize_kline(bars: List[Dict], source_period: str, target_period: str) -> List[Dict]:
    if source_period == target_period:
        return bars

    if source_period == "1min":
        if target_period in _PERIOD_BARS_1MIN:
            return synthesize_from_1min(bars, target_period)
        intermediate = synthesize_from_1min(bars, "60min")
        return synthesize_kline(intermediate, "60min", target_period)

    if source_period == "5min":
        if target_period in _PERIOD_BARS_5MIN:
            return synthesize_from_5min(bars, target_period)
        intermediate = synthesize_from_5min(bars, "60min")
        return synthesize_kline(intermediate, "60min", target_period)

    if source_period == "60min":
        if target_period == "day":
            return _synthesize_day_from_intraday(bars)
        if target_period in ("week", "month"):
            daily = _synthesize_day_from_intraday(bars)
            return synthesize_from_daily(daily, target_period)

    if source_period == "day":
        return synthesize_from_daily(bars, target_period)

    return bars


if __name__ == "__main__":
    bars_1min = []
    for i in range(30):
        bars_1min.append({
            "time": f"2024-01-15 09:{30 + i:02d}:00",
            "open": float(10 + i),
            "high": float(10 + i + 0.5),
            "low": float(10 + i - 0.5),
            "close": float(10 + i + 0.2),
            "volume": 1000 + i * 100,
            "amount": 10000.0 + i * 1000.0,
        })

    result_5min = synthesize_from_1min(bars_1min, "5min")
    assert len(result_5min) == 6, f"Expected 6 5min bars, got {len(result_5min)}"
    assert result_5min[0]["open"] == 10.0
    assert result_5min[0]["close"] == 14.2
    assert result_5min[0]["high"] == 14.5
    assert result_5min[0]["low"] == 9.5
    assert result_5min[0]["volume"] == 6000
    assert result_5min[0]["amount"] == 60000.0
    assert result_5min[-1]["open"] == 35.0
    assert result_5min[-1]["close"] == 39.2
    print("PASS: synthesize_from_1min -> 5min")

    result_15min = synthesize_from_1min(bars_1min, "15min")
    assert len(result_15min) == 2, f"Expected 2 15min bars, got {len(result_15min)}"
    assert result_15min[0]["open"] == 10.0
    assert result_15min[0]["close"] == 24.2
    assert result_15min[0]["high"] == 24.5
    assert result_15min[0]["low"] == 9.5
    assert result_15min[0]["volume"] == sum(1000 + i * 100 for i in range(15))
    assert result_15min[1]["open"] == 25.0
    assert result_15min[1]["close"] == 39.2
    print("PASS: synthesize_from_1min -> 15min")

    bars_5min = []
    for i in range(24):
        h = 9 + (i * 5) // 60
        m = (i * 5) % 60
        bars_5min.append({
            "time": f"2024-01-15 {h:02d}:{m:02d}:00",
            "open": float(20 + i),
            "high": float(20 + i + 0.8),
            "low": float(20 + i - 0.3),
            "close": float(20 + i + 0.1),
            "volume": 2000 + i * 200,
            "amount": 20000.0 + i * 2000.0,
        })

    result_30min = synthesize_from_5min(bars_5min, "30min")
    assert len(result_30min) == 4, f"Expected 4 30min bars, got {len(result_30min)}"
    assert result_30min[0]["open"] == 20.0
    assert result_30min[0]["close"] == 25.1
    assert result_30min[0]["high"] == 25.8
    assert result_30min[0]["low"] == 19.7
    assert result_30min[0]["volume"] == sum(2000 + i * 200 for i in range(6))
    print("PASS: synthesize_from_5min -> 30min")

    result_60min = synthesize_from_5min(bars_5min, "60min")
    assert len(result_60min) == 2, f"Expected 2 60min bars, got {len(result_60min)}"
    assert result_60min[0]["open"] == 20.0
    assert result_60min[0]["close"] == 31.1
    assert result_60min[1]["open"] == 32.0
    assert result_60min[1]["close"] == 43.1
    print("PASS: synthesize_from_5min -> 60min")

    bars_daily = []
    start = datetime(2024, 1, 1)
    for i in range(60):
        dt = start + timedelta(days=i)
        bars_daily.append({
            "time": dt.strftime("%Y-%m-%d") + " 00:00:00",
            "open": float(50 + i),
            "high": float(50 + i + 1.0),
            "low": float(50 + i - 1.0),
            "close": float(50 + i + 0.5),
            "volume": 5000 + i * 500,
            "amount": 50000.0 + i * 5000.0,
        })

    result_week = synthesize_from_daily(bars_daily, "week")
    assert len(result_week) >= 8, f"Expected >=8 week bars, got {len(result_week)}"
    assert result_week[0]["open"] == 50.0
    assert result_week[0]["time"].startswith("2024-01-01")
    assert result_week[1]["time"].startswith("2024-01-08")
    print(f"PASS: synthesize_from_daily -> week ({len(result_week)} bars)")

    result_month = synthesize_from_daily(bars_daily, "month")
    assert len(result_month) >= 2, f"Expected >=2 month bars, got {len(result_month)}"
    assert result_month[0]["open"] == 50.0
    assert result_month[0]["time"].startswith("2024-01-01")
    assert result_month[1]["time"].startswith("2024-02-01")
    print(f"PASS: synthesize_from_daily -> month ({len(result_month)} bars)")

    assert synthesize_kline(bars_1min, "1min", "1min") == bars_1min
    print("PASS: synthesize_kline identity (1min -> 1min)")

    assert synthesize_kline(bars_daily, "day", "day") == bars_daily
    print("PASS: synthesize_kline identity (day -> day)")

    k_1min_to_5min = synthesize_kline(bars_1min, "1min", "5min")
    assert len(k_1min_to_5min) == 6
    assert k_1min_to_5min[0]["open"] == 10.0
    print("PASS: synthesize_kline 1min -> 5min")

    k_5min_to_60min = synthesize_kline(bars_5min, "5min", "60min")
    assert len(k_5min_to_60min) == 2
    print("PASS: synthesize_kline 5min -> 60min")

    k_day_to_week = synthesize_kline(bars_daily, "day", "week")
    assert len(k_day_to_week) >= 8
    print("PASS: synthesize_kline day -> week")

    k_day_to_month = synthesize_kline(bars_daily, "day", "month")
    assert len(k_day_to_month) >= 2
    print("PASS: synthesize_kline day -> month")

    k_5min_to_30min = synthesize_kline(bars_5min, "5min", "30min")
    assert len(k_5min_to_30min) == 4
    print("PASS: synthesize_kline 5min -> 30min")

    print("\n=== ALL TESTS PASSED ===")
