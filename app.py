import logging, re, json, os, time
import asyncio
import uuid
import dataclasses
import xml.etree.ElementTree as ET
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Any, Callable, Dict, List, Optional
from fastapi import FastAPI, Request as _Request, HTTPException as _HTTPException, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import FileResponse, StreamingResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

try:
    from .core.engine import PoolEngine
except ImportError:
    from core.engine import PoolEngine

try:
    from .services.storage import Storage, safe_path_join
except ImportError:
    from services.storage import Storage, safe_path_join

try:
    from .services.tq_adapter import TqAdapter
except ImportError:
    from services.tq_adapter import TqAdapter

try:
    from .core.tick_bar_module import Min1Aggregator
except ImportError:
    from core.tick_bar_module import Min1Aggregator

try:
    from .services.data import DataQueryService, _normalize_period
except ImportError:
    from services.data import DataQueryService, _normalize_period

try:
    from .services.data import DataSyncService
except ImportError:
    from services.data import DataSyncService

try:
    from .api import create_meta_router
except ImportError:
    from api import create_meta_router

try:
    from .api import create_execution_router
except ImportError:
    from api import create_execution_router

try:
    from .api import create_dzh_router
except ImportError:
    from api import create_dzh_router

try:
    from .api import create_json_router
except ImportError:
    from api import create_json_router

try:
    from .api import create_sim_router
except ImportError:
    from api import create_sim_router

try:
    from .api import create_replay_router
except ImportError:
    from api import create_replay_router

try:
    from .api import table_router, set_table_engine, table_config_router
except ImportError:
    from api import table_router, set_table_engine, table_config_router

try:
    from .api import config_api_router, config_api_init, config_ws_router
except ImportError:
    from api import config_api_router, config_api_init, config_ws_router

try:
    from .api import create_formula_router
except ImportError:
    from api import create_formula_router

try:
    from .core.runtime_mode_module import KLineReplayEngine
except ImportError:
    from core.runtime_mode_module import KLineReplayEngine

try:
    from .converters import _tdx_pool_to_frontend, _load_tdx_pool_config
    from .core.import_export_module import _call_converter
except ImportError:
    from converters import _tdx_pool_to_frontend, _load_tdx_pool_config
    from core.import_export_module import _call_converter

try:
    from .api import _enrich_tdx_node_data
except ImportError:
    from api import _enrich_tdx_node_data

try:
    from .core.trade_module import _read_history_log, _dispatch_pool_enter_actions, _quote_filename
except ImportError:
    from core.trade_module import _read_history_log, _dispatch_pool_enter_actions, _quote_filename

try:
    from .core.domain import _stock_code
except ImportError:
    from core.domain import _stock_code

# ─── Task 15: 事件布线器装配层 import（app.py 是装配层，允许 import 所有模块） ──
try:
    from .core.event_bus import EventBus
except ImportError:
    from core.event_bus import EventBus

try:
    from .core.web_state import format_event, format_timer_queue, runtime_state, normalize_display_ms
except ImportError:
    from core.web_state import format_event, format_timer_queue, runtime_state, normalize_display_ms

try:
    from .core.table_engine import ConfigStore, set_global_config_store
except ImportError:
    from core.table_engine import ConfigStore, set_global_config_store

try:
    from .core.tick_bar_module import TickBarModule
except ImportError:
    from core.tick_bar_module import TickBarModule

try:
    from .core.formula_module import FormulaModule
except ImportError:
    from core.formula_module import FormulaModule

try:
    from .core.screening_module import ScreeningModule
except ImportError:
    from core.screening_module import ScreeningModule

try:
    from .core.execution_module import ExecutionModule
except ImportError:
    from core.execution_module import ExecutionModule

try:
    from .core.trade_module import TradeModule
except ImportError:
    from core.trade_module import TradeModule

try:
    from .core.monitoring_module import MonitoringModule, StatisticsModule
except ImportError:
    from core.monitoring_module import MonitoringModule, StatisticsModule

try:
    from .core.import_export_module import ImportExportModule
except ImportError:
    from core.import_export_module import ImportExportModule

try:
    from .core.runtime_mode_module import RuntimeModeModule
except ImportError:
    from core.runtime_mode_module import RuntimeModeModule

try:
    from .services.data import CandidatePoolResolver
except ImportError:
    from services.data import CandidatePoolResolver

try:
    from .services.data import DataSourceContract
except ImportError:
    from services.data import DataSourceContract

try:
    from .core.table_engine import HotReloadManager
except ImportError:
    from core.table_engine import HotReloadManager

_BASE = Path(__file__).parent
_CONFIG = _BASE / "config"
_TDXPOOL_DIR = _BASE / "tdxpool"
web_dir = _BASE / "web"

def _import_demo_pools(storage):
    """从config/pools目录导入所有示例股票池到storage，优先导入target_pool_100.json"""
    import json as _json
    pools_dir = _CONFIG / "pools"
    
    target_file = pools_dir / "target_pool_100.json"
    if target_file.exists():
        files_to_import = [target_file]
        for f in pools_dir.glob("*.json"):
            if f.name != "target_pool_100.json" and f.name != "pool_types.json":
                files_to_import.append(f)
    else:
        files_to_import = list(pools_dir.glob("*.json"))
        files_to_import = [f for f in files_to_import if f.name != "pool_types.json"]
    
    for f in files_to_import:
        try:
            with open(f, 'r', encoding='utf-8') as fp:
                pool_data = _json.load(fp)
            pool_id = pool_data.get("id") or f.stem
            existing = storage.get_pool(pool_id)
            if existing:
                continue
            pool_name = pool_data.get("name") or pool_id
            nodes = pool_data.get("nodes", [])
            edges = pool_data.get("edges", [])
            pool_meta = {}
            for k, v in pool_data.items():
                if k not in ("id", "name", "nodes", "edges"):
                    pool_meta[k] = v
            pool_type = pool_data.get("pool_type", "dzh")
            storage.save_pool(pool_id, {
                "name": pool_name,
                "pool_type": pool_type,
                "description": pool_data.get("description", ""),
                "topology_mode": pool_data.get("topology_mode", "flow"),
                "status": "active",
                "nodes": nodes,
                "edges": edges,
                "pool_meta": pool_meta,
            })
            logging.info("导入示例股票池: %s (%s)", pool_name, pool_id)
        except Exception as ex:
            logging.warning("导入示例股票池失败 %s: %s", f.name, ex)

def load_global_config() -> dict:
    """加载全局配置（config/defaults.json），供模块构造函数注入。"""
    try:
        cfg_path = _CONFIG / "runtime" / "defaults.json"
        with open(cfg_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _generate_mock_kline_bars(stock_code: str, period: str, limit: int = 300) -> List[Dict[str, Any]]:
    """在无真实数据时生成确定性模拟 K 线，确保前端 K 线/指标面板可展示。"""
    import random
    import pandas as pd
    rng = random.Random(abs(hash(stock_code)) % (2 ** 31))
    count = min(limit, 200)
    base = rng.uniform(5.0, 200.0)
    last_close = base
    now = pd.Timestamp.now().normalize()
    p = str(period).lower()
    if p in ("1d", "day", "d"):
        freq = pd.Timedelta(days=1)
        start = now - pd.Timedelta(days=count - 1)
    elif p.endswith("m") or p.endswith("min"):
        digits = "".join(ch for ch in p if ch.isdigit())
        n = int(digits) if digits else 1
        freq = pd.Timedelta(minutes=n)
        start = now + pd.Timedelta(hours=9, minutes=30) - freq * (count - 1)
    else:
        freq = pd.Timedelta(days=1)
        start = now - pd.Timedelta(days=count - 1)
    bars = []
    for i in range(count):
        ts = start + i * freq
        cp = rng.gauss(0, 1.5)
        close = max(0.01, round(last_close * (1 + cp / 100), 2))
        oc = rng.gauss(0, 0.8)
        open_p = max(0.01, round(last_close * (1 + oc / 100), 2))
        high = max(open_p, close) * (1 + abs(rng.gauss(0, 0.5)) / 100)
        low = min(open_p, close) * (1 - abs(rng.gauss(0, 0.5)) / 100)
        volume = int(rng.lognormvariate(14, 2))
        bars.append({
            "datetime": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "open": round(open_p, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "close": round(close, 2),
            "volume": volume,
        })
        last_close = close
    return bars


def _df_to_kline_bars(df) -> List[Dict[str, Any]]:
    """将 K 线 DataFrame 统一转为前端需要的 dict 列表。"""
    import pandas as pd
    if df is None or len(df) == 0:
        return []
    col_map = {
        "datetime": "datetime", "time": "datetime", "date": "datetime",
        "open": "open", "high": "high", "low": "low",
        "close": "close", "volume": "volume",
    }
    result = []
    for _, row in df.iterrows():
        r: Dict[str, Any] = {}
        for target_col, src_col in col_map.items():
            if src_col in row.index:
                v = row[src_col]
                r[target_col] = None if pd.isna(v) else (float(v) if isinstance(v, (int, float)) else str(v))
        result.append(r)
    return result


def _get_kline_bars_from_state(state_bars_history, stock_code: str, period: str, limit: int):
    """从 bars_history 字典中读取指定代码/周期的 K 线。"""
    import pandas as pd
    if not isinstance(state_bars_history, dict):
        return None
    period_data = state_bars_history.get(period)
    if not isinstance(period_data, dict):
        return None
    code_list = period_data.get(stock_code)
    if code_list:
        return pd.DataFrame(code_list[-limit:])
    return None


async def _get_kline_bars(request, stock_code: str, period: str, limit: int = 300) -> List[Dict[str, Any]]:
    """统一读取 K 线：优先 tick_bar / engine state，其次 DataQueryService，最后 mock。"""
    period_norm = _normalize_period(period)

    # 1) TickBarModule 的 bars_history（事件驱动合成）
    tick_bar = getattr(request.app.state, "tick_bar", None)
    df = None
    if tick_bar is not None:
        df = _get_kline_bars_from_state(getattr(tick_bar, "bars_history", None), stock_code, period_norm, limit)
    if df is not None and not df.empty:
        return _df_to_kline_bars(df)

    # 2) PoolEngine state 的 bars_history
    engine = getattr(request.app.state, "engine", None)
    st = getattr(engine, "state", None) if engine else None
    df = _get_kline_bars_from_state(getattr(st, "bars_history", None), stock_code, period_norm, limit)
    if df is not None and not df.empty:
        return _df_to_kline_bars(df)

    # 3) DataQueryService 本地历史数据
    dqs = getattr(request.app.state, "data_query_service", None)
    if dqs is not None:
        try:
            df = await dqs.get_kline_series(stock_code, period=period_norm, count=limit)
            if df is not None and not df.empty:
                return _df_to_kline_bars(df)
        except Exception as ex:
            logger.warning("DataQueryService 读取 %s %s 失败: %s", stock_code, period_norm, ex)

    # 4) 兜底：确定性 mock 数据，保证前端始终可展示
    return _generate_mock_kline_bars(stock_code, period_norm, limit)


@asynccontextmanager
async def lifespan(app):
    """事件布线器：创建 EventBus，依次实例化所有模块，注入 EventBus。

    Task 15（unify-stockpool-oop-event-driven）：
        - 创建 EventBus 作为模块间唯一通信中介
        - 依次实例化 16 个模块（ConfigStore / Storage / CandidatePoolResolver /
          DataSourceContract / TickBar / Formula / Screening / Execution / Trade /
          Statistics / Monitoring / ImportExport / RuntimeMode / HotReload），
          每个模块仅注入 EventBus + 配置 dict
        - 保留 legacy PoolEngine / TqAdapter / Min1Aggregator / DataQueryService /
          DataSyncService 供现有 API 路由调用（Task 16 再迁移为事件驱动）
        - 删除 engine.set_xxx() 跨模块依赖注入（模块间只通过 EventBus 交互）
    """
    # ═══ 1. 创建 EventBus（模块间唯一通信中介） ═══
    bus = EventBus()
    app.state.bus = bus

    # ═══ 2. 加载全局配置 ═══
    config = load_global_config()
    config_dir = str(_CONFIG)

    # ═══ 3. 依次实例化 16 个模块（仅注入 EventBus + 配置 dict） ═══

    # ── 3.1 Config 模块（加载配置表，订阅 ConfigChanged 事件重载） ──
    config_store = ConfigStore(config_dir=config_dir, bus=bus)
    config_store.load_all()
    app.state.config_store = config_store
    # 注入全局 ConfigStore 引用，供模块级函数通过 get_global_config_store() 访问
    set_global_config_store(config_store)

    # ── 3.2 Database 模块（Storage 已事件化，构造函数接收 bus） ──
    storage = Storage(bus=bus)
    app.state.storage = storage

    # ── 3.3 DataSource 模块（候选池解析器 + 数据源契约） ──
    candidate_resolver = CandidatePoolResolver(storage=storage, providers={}, bus=bus)
    app.state.candidate_resolver = candidate_resolver
    data_contract = DataSourceContract(bus=bus)
    app.state.data_contract = data_contract

    # ── 3.4 TickBar 模块 ──
    tick_bar = TickBarModule(bus=bus, config=config)
    app.state.tick_bar = tick_bar

    # ── 3.5 Formula 模块 ──
    # 注入 data_query（从 TickBarModule._state 获取 bars_history）+ HQChartProvider
    # 解决 FormulaModule._on_bar_composed 中 "data_query is required" 错误
    from core.tick_bar_module import make_bars_history_getter
    from services.providers import HQChartProvider

    class _StateBackedDataQuery:
        """从 TickBarModule._state 派生的 data_query 适配器。

        实现 IDataQuery.get_kline_series(symbol, period) 协议，
        返回 bars_history[period][symbol] + 当前 bar 的拼接 DataFrame。
        """
        def __init__(self, tick_bar_module):
            self._tick_bar = tick_bar_module
            self._getter = None

        def _ensure_getter(self):
            state = getattr(self._tick_bar, "_state", None)
            if state is None:
                return None
            # 延迟构造 getter，每次调用都重新创建以反映最新 state
            return make_bars_history_getter(state)

        def get_kline_series(self, symbol: str, period: str):
            getter = self._ensure_getter()
            if getter is None:
                import pandas as pd
                return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
            return getter(symbol, period)

    _data_query = _StateBackedDataQuery(tick_bar)
    try:
        _hqchart_provider = HQChartProvider(bus=bus, config=config)
    except Exception as ex:
        logger.warning("HQChartProvider 初始化失败: %s", ex)
        _hqchart_provider = None
    formula = FormulaModule(
        bus=bus, config=config,
        data_query=_data_query,
        hqchart_provider=_hqchart_provider,
    )
    app.state.formula = formula

    # ── 3.6 Screening 模块 ──
    screening = ScreeningModule(bus=bus, config=config)
    app.state.screening = screening

    # ── 3.7 Execution 模块 ──
    # SubTask 8.2: 注入 PoolEngine 实例作为 meta_engine，使 ExecutionModule
    # 在 _on_stock_filtered 中能通过 _ensure_engine() 获取 PoolEngine，
    # 将筛选结果写入 state.filter_inputs 供 EdgeExecutor._filter 读取。
    # 不注入会导致 StockFiltered → EdgeExecutor._filter 缓存链断裂，
    # entered=[] 永远为空，TransferExecuted/OrderPlaced 等下游事件全部缺失。
    pool_engine_for_execution = PoolEngine(bus=bus)
    execution = ExecutionModule(bus=bus, config=config, meta_engine=pool_engine_for_execution)
    app.state.execution = execution

    # ── 3.8 Trade 模块 ──
    trade = TradeModule(bus=bus, config=config)
    app.state.trade = trade

    # ── 3.9 Statistics 模块 ──
    statistics = StatisticsModule(bus=bus, config=config)
    app.state.statistics = statistics

    # ── 3.10 Monitoring 模块 ──
    monitoring = MonitoringModule(bus=bus, config=config)
    app.state.monitoring = monitoring

    # ── 3.11 ImportExport 模块 ──
    import_export = ImportExportModule(bus=bus, config=config)
    app.state.import_export = import_export

    # ── 3.12 RuntimeMode 模块 ──
    runtime_mode = RuntimeModeModule(bus=bus, config=config)
    app.state.runtime_mode = runtime_mode

    # ── 3.13 HotReload 模块（bus 注入后发布 ConfigChanged 事件） ──
    hot_reload = HotReloadManager(
        config_dir=config_dir,
        config_store=config_store,
        storage=storage,
        bus=bus,
        on_change=lambda changed: logging.info(f"配置变更: {changed}"),
    )
    app.state.hot_reload = hot_reload

    # ═══ 4. 启动 HotReload watchdog 监听 ═══
    if hasattr(hot_reload, 'start_watchdog'):
        hot_reload.start_watchdog()

    # ═══ 5. Legacy 保留：PoolEngine / TqAdapter / Min1Aggregator /
    #         DataQueryService / DataSyncService（供现有 API 路由调用） ═══
    # Task 15.2: 删除 engine.set_xxx() 跨模块依赖注入——模块间只通过 EventBus 交互。
    # PoolEngine 保留为 API 层状态读取适配器（_pk_rankings / event_panel 等），
    # 不再注入 tq_adapter / storage / minute_aggregator。
    # SubTask 22.3: PoolEngine 接收 bus 参数，与所有模块共享同一 EventBus 实例。
    # Task 24: MetaEngine 已合并入 PoolEngine，统一使用 PoolEngine 类名（已完成合并）。
    # SubTask 8.2: 复用注入给 ExecutionModule 的 PoolEngine 实例，避免重复
    # 创建导致 EventBus 重复订阅（同一事件被处理两次）。
    app.state.engine = pool_engine_for_execution
    app.state.tq = TqAdapter(mock_mode=False)
    logging.info("TqAdapter 初始化完成，数据模式: %s", app.state.tq.get_mode_info())
    app.state.minute_aggregator = Min1Aggregator(symbols=[])
    logging.info("Min1Aggregator 初始化完成，监控标的数: %d", app.state.minute_aggregator.n)
    app.state.data_query_service = DataQueryService(
        storage=app.state.storage,
        minute_aggregator=app.state.minute_aggregator,
        tq_adapter=app.state.tq,
    )
    # 注入 K 线数据源供回放引擎统一使用（后端提供，不降级到前端伪造）
    app.state.engine.kline_provider = app.state.data_query_service
    app.state.data_sync_service = DataSyncService(
        storage=app.state.storage,
        minute_aggregator=app.state.minute_aggregator,
    )
    logging.info("DataQueryService / DataSyncService 初始化完成（legacy 保留）")

    # ═══ 6. API 层接线（路由注册在 app.py 主体） ═══
    # SubTask 22.5: 传递 bus 给 set_table_engine，供命令类端点发布事件
    set_table_engine(app.state.engine, config_dir, bus=bus)
    app.state._simulators = {}  # 仿真会话池：{name: RuntimeSimulator}
    app.state._sim_session_map = {}  # 基于 session_id 的仿真会话：{session_id: {simulator, pool_id, events}}

    # 自动导入示例股票池
    _import_demo_pools(app.state.storage)
    # 初始化 config API（注入新 config_store + hot_reload_manager）
    config_api_init(config_store=config_store, hot_reload_manager=hot_reload)

    yield

    # ═══ 7. 关闭清理 ═══
    if hasattr(hot_reload, 'stop_watchdog'):
        hot_reload.stop_watchdog()

# ─── API Key 认证中间件 ───────────────────────────────────────
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def _load_auth_config():
    """从 config/defaults.json 加载认证配置"""
    try:
        import json
        from pathlib import Path
        cfg_path = Path(__file__).parent / "config" / "runtime" / "defaults.json"
        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        return cfg.get('auth', {})
    except Exception:
        return {}

def _load_cors_config():
    """从 config/defaults.json 加载 CORS 配置"""
    try:
        import json
        from pathlib import Path
        cfg_path = Path(__file__).parent / "config" / "runtime" / "defaults.json"
        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        return cfg.get('cors', {})
    except Exception:
        return {}

async def verify_api_key(api_key: str = Depends(_api_key_header)):
    """API Key 认证依赖：校验 X-API-Key 请求头"""
    auth_cfg = _load_auth_config()
    if not auth_cfg.get('enabled', False):
        return  # 认证关闭，向后兼容
    expected_key = auth_cfg.get('api_key') or os.environ.get('META_CORE_API_KEY', '')
    if not expected_key:
        return  # 未配置密钥，跳过校验
    if api_key != expected_key:
        raise HTTPException(status_code=401, detail="无效或缺失的 API Key")

app = FastAPI(title="PoolEngine Stock Pool", version="1.0", lifespan=lifespan)
_cors_cfg = _load_cors_config()
_cors_origins = _cors_cfg.get('allowed_origins', ["http://localhost:*", "http://127.0.0.1:*"])
app.add_middleware(CORSMiddleware, allow_origins=_cors_origins, allow_methods=["*"], allow_headers=["*"], allow_credentials=True)

# ══════════════════════════════════════════════════════════════════════
#  已保存股票池 API（前端 web/js/main.js 调用，无需 API Key）
#  放在 include_router 之前，确保优先于 /api 下 execution router 的同名端点
# ══════════════════════════════════════════════════════════════════════

@app.get("/api/pools", tags=["pools"])
async def api_list_pools(request: _Request):
    """返回已保存股票池列表。"""
    try:
        storage = request.app.state.storage
        pools = storage.list_pools()
        result = []
        for p in pools:
            pool_id = p.get("pool_id") or p.get("id", "")
            counts = storage.get_pool_counts(pool_id)
            result.append({
                "pool_id": pool_id,
                "id": pool_id,
                "name": p.get("name", ""),
                "pool_type": p.get("pool_type", ""),
                "node_count": counts.get("node_count", 0),
                "edge_count": counts.get("edge_count", 0),
                "updated_at": p.get("updated_at", ""),
            })
        return {"code": 0, "data": result}
    except Exception as ex:
        return {"code": 1, "msg": str(ex)}


@app.get("/api/pools/{pool_id}", tags=["pools"])
async def api_get_pool(pool_id: str, request: _Request):
    """返回指定股票池的图数据。"""
    try:
        storage = request.app.state.storage
        pool = storage.get_pool(pool_id)
        if not pool:
            return {"code": 1, "msg": f"股票池不存在: {pool_id}"}
        params = pool.get("params") or {}
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except json.JSONDecodeError:
                params = {}
        graph_data = dict(params) if isinstance(params, dict) else {}
        graph_data["name"] = pool.get("name", "")
        graph_data["pool_type"] = pool.get("pool_type", "")
        if "pool_meta" not in graph_data or not isinstance(graph_data["pool_meta"], dict):
            graph_data["pool_meta"] = {}
        graph_data["pool_meta"].setdefault("name", pool.get("name", ""))
        graph_data["pool_meta"].setdefault("type", pool.get("pool_type", ""))
        graph_data["pool_meta"].setdefault("pool_id", pool_id)
        _enrich_tdx_node_data(graph_data)
        return {"code": 0, "data": graph_data}
    except Exception as ex:
        return {"code": 1, "msg": str(ex)}


@app.delete("/api/pools/{pool_id}", tags=["pools"])
async def api_delete_pool(pool_id: str, request: _Request):
    """删除指定股票池。"""
    try:
        storage = request.app.state.storage
        if not storage.get_pool(pool_id):
            return {"code": 1, "msg": f"股票池不存在: {pool_id}"}
        storage.delete_pool(pool_id)
        return {"code": 0, "data": None}
    except Exception as ex:
        return {"code": 1, "msg": str(ex)}


@app.post("/api/pools/{pool_id}/state-pools/{node_id}/prefetch-klines", tags=["pools"])
async def api_prefetch_pool_klines(pool_id: str, node_id: str, period: str = "day", request: _Request = None):
    """预取状态池 K 线（当前仅返回空成功）。"""
    return {"code": 0, "data": None}


app.include_router(create_meta_router(), prefix="/api/meta", tags=["元数据"], dependencies=[Depends(verify_api_key)])
app.include_router(create_execution_router(), prefix="/api", tags=["执行"], dependencies=[Depends(verify_api_key)])
app.include_router(create_dzh_router(), dependencies=[Depends(verify_api_key)])
app.include_router(create_json_router(), prefix="/api/json", tags=["JSON导入导出"], dependencies=[Depends(verify_api_key)])
app.include_router(create_replay_router(), dependencies=[Depends(verify_api_key)])
# /api/sim/* 端点由本文件下方基于 session_id 的实现提供，不再挂载 api.py 中的旧版 sim router（原 api/system_api.py 已合并到 api.py）
app.include_router(table_router, dependencies=[Depends(verify_api_key)])
app.include_router(table_config_router, dependencies=[Depends(verify_api_key)])
app.include_router(config_api_router, dependencies=[Depends(verify_api_key)])
# WebSocket 路由独立挂载，不带 API key dependencies（WebSocket 上下文不支持 APIKeyHeader）
app.include_router(config_ws_router)
app.include_router(create_formula_router(), prefix="/api/formula", tags=["公式"], dependencies=[Depends(verify_api_key)])

@app.get("/api/tdx/pools", tags=["tdx"])
async def tdx_list_pools():
    d = os.path.join(os.path.dirname(__file__), 'tdxpool')
    if not os.path.isdir(d): return {"success": True, "data": []}
    try:
        return {"success": True, "data": [{"name": f[:-4], "filename": f, "has_screenshot": os.path.isfile(os.path.join(d, f[:-4] + '.png'))} for f in sorted(os.listdir(d)) if f.endswith('.xml')]}
    except Exception as ex: return {"success": False, "error": str(ex)}

def _tdx_path_guard(name: str) -> Path:
    """校验 tdx 池名称合法性并返回对应的 xml 文件路径。"""
    if ".." in name or "/" in name or "\\" in name:
        raise _HTTPException(status_code=400, detail="非法名称")
    return Path(__file__).parent / "tdxpool" / f"{name}.xml"


async def _tdx_load(name: str, request: _Request) -> Dict[str, Any]:
    try:
        xml_path = _tdx_path_guard(name)
        if not xml_path.is_file():
            raise _HTTPException(status_code=404, detail=f"文件未找到: {name}.xml")
        pool = _call_converter(str(xml_path), "tdx", "import")
        return {"success": True, "data": _tdx_pool_to_frontend(pool, name), "stats": {"cells": len(pool.cells), "flows": len(pool.flows), "name": name}}
    except _HTTPException:
        raise
    except Exception as ex:
        return {"success": False, "error": f"TDX 加载失败: {str(ex)}"}


async def _tdx_create(name: str, request: _Request) -> Dict[str, Any]:
    try:
        body = await request.json()
        xml_path = _tdx_path_guard(name)
        xml_path.parent.mkdir(parents=True, exist_ok=True)
        if xml_path.exists():
            return {"success": False, "error": f"股票池 {name} 已存在"}
        bc = body.get("backcolor", 1114112)
        xml_path.write_text(
            f'<?xml version="1.0" encoding="GBK"?>\n<root>\n<pool nextid="1" backcolor="{bc}">\n<cells>\n</cells>\n<flows>\n</flows>\n</pool>\n</root>',
            encoding='gbk',
        )
        pool_id = "pool_" + uuid.uuid4().hex[:12]
        request.app.state.storage.save_pool(pool_id, {"name": name, "pool_type": "tdx", "description": f"TDX pool: {name}", "topology_mode": "flow", "status": "draft"})
        return {"success": True, "data": {"name": name, "filename": f"{name}.xml", "backcolor": bc, "pool_id": pool_id}}
    except _HTTPException:
        raise
    except Exception as ex:
        return {"success": False, "error": f"TDX 创建失败: {str(ex)}"}


async def _tdx_save(name: str, request: _Request) -> Dict[str, Any]:
    try:
        body = await request.json()
        pool_data = body.get("pool_data", body)
        if not pool_data or not isinstance(pool_data, dict):
            return {"success": False, "error": "无效的 pool_data"}
        xml_path = _tdx_path_guard(name)
        xml_path.parent.mkdir(parents=True, exist_ok=True)
        _call_converter(str(xml_path), "tdx", "export", config=pool_data)
        storage = request.app.state.storage
        pm = pool_data.get("pool_meta", {})
        pool_id = pool_data.get("pool_id") or pool_data.get("id") or f"tdx_{name}"
        if not storage.get_pool(pool_id):
            for p in storage.list_pools():
                if p.get("name") == name and p.get("pool_type") == "tdx":
                    pool_id = p.get("pool_id", pool_id)
                    break
        storage.save_pool(pool_id, {"name": pool_data.get("name", name), "pool_type": "tdx" if pm.get("type") == "tdx" else "dzh",
                                     "description": pool_data.get("description", ""), "xml_source": str(xml_path),
                                     "topology_mode": "flow", "status": "active",
                                     "nodes": pool_data.get("nodes", []), "edges": pool_data.get("edges", []),
                                     "pool_meta": pm})
        return {"success": True, "data": {"name": name, "filename": f"{name}.xml"}}
    except _HTTPException:
        raise
    except Exception as ex:
        return {"success": False, "error": f"TDX 保存失败: {str(ex)}"}


async def _tdx_delete(name: str, request: _Request) -> Dict[str, Any]:
    try:
        xml_path = _tdx_path_guard(name)
        if not xml_path.is_file():
            raise _HTTPException(status_code=404, detail=f"文件未找到: {name}.xml")
        xml_path.unlink()
        png_path = xml_path.with_suffix('.png')
        if png_path.is_file():
            png_path.unlink()
        return {"success": True}
    except _HTTPException:
        raise
    except Exception as ex:
        return {"success": False, "error": f"TDX 删除失败: {str(ex)}"}


_TDX_ACTIONS: Dict[str, Callable] = {
    "GET": _tdx_load,
    "POST": _tdx_create,
    "PUT": _tdx_save,
    "DELETE": _tdx_delete,
}


@app.api_route("/api/tdx/pool/{name}", methods=["GET", "POST", "PUT", "DELETE"], tags=["tdx"], dependencies=[Depends(verify_api_key)])
async def tdx_pool_dispatch(name: str, request: _Request):
    action = _TDX_ACTIONS.get(request.method)
    if action is None:
        raise _HTTPException(status_code=405, detail="Method not allowed")
    return await action(name, request)

@app.post("/api/tdx/export", tags=["tdx"], dependencies=[Depends(verify_api_key)])
async def tdx_export_xml(request: _Request):
    try:
        import tempfile
        from starlette.background import BackgroundTask
        _body = await request.json()
        pool_data = _body.get("pool_data", _body) if isinstance(_body, dict) else {}
        with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp: tmp_path = tmp.name
        _call_converter(tmp_path, "tdx", "export", config=pool_data)
        return FileResponse(tmp_path, media_type="application/xml", filename=pool_data.get("name", "tdx_pool") + ".xml", background=BackgroundTask(os.remove, tmp_path))
    except Exception as ex: return {"success": False, "error": f"TDX导出失败: {str(ex)}"}

@app.post("/api/tdx/execute-pool", tags=["tdx"], dependencies=[Depends(verify_api_key)])
async def tdx_execute_pool(request: _Request):
    try:
        body = await request.json(); filename, pool_data = body.get("filename", ""), body.get("pool_data")
        engine, _saved_count = request.app.state.engine, [0]
        if pool_data and isinstance(pool_data, dict) and "nodes" in pool_data:
            pool_name = pool_data.get("name", "") or (filename.replace(".xml", "") if filename else "")
            result = engine.run_pool(pool_data)
        elif filename:
            try:
                xml_path = safe_path_join(os.path.join(os.path.dirname(__file__), 'tdxpool'), filename)
            except ValueError as ex:
                return {"success": False, "error": str(ex)}
            if not os.path.isfile(xml_path): return {"success": False, "error": f"文件未找到: {filename}"}
            pool_name = filename.replace(".xml", ""); cfg = _load_tdx_pool_config(xml_path) or {}; nm = {n.get('id', ''): n for n in cfg.get('nodes', [])}
            def _on_enter(nid, info, stocks): _dispatch_pool_enter_actions(pool_name, nid, nm.get(nid, info) or {}, stocks, saved_counter=_saved_count)
            engine._on_stock_enter_target_pool = _on_enter
            try: result = engine.run_pool(cfg)
            finally: engine._on_stock_enter_target_pool = None
        else: return {"success": False, "error": "缺少 filename 或 pool_data 参数"}
        resp = {"success": True, "data": result}
        if _saved_count[0] > 0: resp["history_saved"] = {"realtime_entries": _saved_count[0], "mode": "incremental"}
        return resp
    except Exception as ex: return {"success": False, "error": f"TDX 执行失败: {str(ex)}"}

# ══════════════════════════════════════════════════════════════════════
#  数据源状态端点（H2 修复：暴露状态 + 显式切换）
# ══════════════════════════════════════════════════════════════════════

# [重复端点说明] /api/data_source/status 与 /api/meta/datasource/list (api.py 中合并自 meta_api) 功能重叠
# 主端点: /api/meta/datasource/list（更完整的数据源列表，含 ready 状态）
@app.get("/api/data_source/status", tags=["data_source"])
async def get_data_source_status():
    """返回当前数据源状态。

    状态值:
      - tdx_tq_ready: 通达信TQ量化(TPythClient.dll) 已连接
      - akshare_ready: AkShare 可用
      - no_real_source: 无任何真实数据源（**不**静默回退到mock）
      - user_selected_mock: 用户显式选择 mock
      - unknown: 状态未知
    """
    tq = app.state.tq
    state = tq.get_data_source_state() if hasattr(tq, 'get_data_source_state') else {}
    available = tq.get_available_sources() if hasattr(tq, 'get_available_sources') else []
    return {
        "status": state.get("status", "unknown"),
        "active": state.get("active"),
        "available": [s["name"] for s in available if s.get("ready")],
        "all_sources": available,
        "last_check": str(state.get("last_check")) if state.get("last_check") else None,
        "error": state.get("error"),
    }


# [重复端点说明] /api/data_source/select/{name} 与 /api/meta/datasource/switch (api.py 中合并自 meta_api) 功能重叠
# 主端点: /api/meta/datasource/switch（统一数据源切换接口）
@app.post("/api/data_source/select/{name}", tags=["data_source"], dependencies=[Depends(verify_api_key)])
async def select_data_source(name: str):
    """显式切换数据源。

    **仅在用户显式调用时才使用 mock**。通达信客户端未启动时，调用此端点选 mock 才能用 mock 数据。
    """
    tq = app.state.tq
    if not hasattr(tq, 'set_active_source'):
        return {"success": False, "error": "数据源切换不支持"}
    try:
        result = tq.set_active_source(name)
        return result
    except Exception as ex:
        return {"success": False, "error": str(ex)}


# ══════════════════════════════════════════════════════════════════════
#  池运行时表端点（node_stocks / 事件 / 信号）
# ══════════════════════════════════════════════════════════════════════


def _get_engine_event_panel(request: Request):
    """获取当前引擎 EventPanel：优先活跃仿真/回放，其次活跃池引擎。"""
    engine = request.app.state.engine
    sims = getattr(request.app.state, "_simulators", {})
    if sims:
        for sim in reversed(list(sims.values())):
            pe = getattr(sim, "_pool_engine", None)
            if pe is None:
                inner = getattr(sim, "_engine", None)
                pe = getattr(inner, "_pool_engine", None) if inner else None
            ep = getattr(pe, "event_panel", None) if pe else None
            if ep is not None:
                return ep
    sim_session_map = getattr(request.app.state, "_sim_session_map", {})
    if sim_session_map:
        for session in reversed(list(sim_session_map.values())):
            simulator = session.get("simulator")
            if simulator is None:
                continue
            pe = getattr(simulator, "_pool_engine", None)
            if pe is None:
                inner = getattr(simulator, "_engine", None)
                pe = getattr(inner, "_pool_engine", None) if inner else None
            ep = getattr(pe, "event_panel", None) if pe else None
            if ep is not None:
                return ep
    replays = getattr(request.app.state, "_replay_engines", {})
    if replays:
        for re in reversed(list(replays.values())):
            pe = getattr(re, "_pool_engine", None)
            ep = getattr(pe, "event_panel", None) if pe else None
            if ep is not None:
                return ep
    pe = getattr(engine, "_pool_engine", None)
    if pe is not None:
        return getattr(pe, "event_panel", None)
    return getattr(engine, "event_panel", None)


def _event_to_dict(ev: Any) -> dict:
    """将 EventBus 事件对象或字典统一转为字典供前端格式化使用。"""
    if isinstance(ev, dict):
        return ev
    if dataclasses.is_dataclass(ev) and not isinstance(ev, type):
        return dataclasses.asdict(ev)
    if hasattr(ev, "__dict__"):
        return vars(ev)
    return {"event_type": str(type(ev).__name__)}


def _infer_display_mode(request: Request) -> str:
    """根据当前活跃会话推断展示时间坐标系。"""
    if getattr(request.app.state, "_sim_session_map", {}) or getattr(request.app.state, "_simulators", {}):
        return "simulation"
    if getattr(request.app.state, "_replay_engines", {}):
        return "replay"
    runtime_mode = getattr(request.app.state, "runtime_mode", None)
    if runtime_mode is not None:
        return runtime_mode.current_mode
    return "live"


def _active_session_id(request: Request) -> Optional[str]:
    sim_map = getattr(request.app.state, "_sim_session_map", {})
    if sim_map:
        return next(iter(sim_map.keys()))
    return None


@app.get("/api/state/runtime", tags=["state"])
async def get_runtime_state(request: Request):
    """返回当前运行时展示态（模式、当前展示时间、活跃会话 ID）。

    前端通过本端点或 SSE 获取业务真值源，不再自行维护 simulationTime 等状态。
    """
    try:
        mode = _infer_display_mode(request)
        now_ts = None
        sim_map = getattr(request.app.state, "_sim_session_map", {})
        if sim_map:
            session = next(iter(sim_map.values()))
            simulator = session.get("simulator")
            if simulator is not None:
                now_ts = getattr(simulator, "clock", None)
        if now_ts is None:
            runtime_mode = getattr(request.app.state, "runtime_mode", None)
            if runtime_mode is not None:
                now_ts = getattr(runtime_mode, "current_ts", None)
        return runtime_state(mode=mode, now_ts=now_ts, active_session_id=_active_session_id(request))
    except Exception as ex:
        return {"success": False, "error": str(ex)}


@app.get("/api/events/recent", tags=["events"])
async def get_events_recent(limit: int = 100, request: Request = None):
    """返回最近已记录的事件（EventPanel 视图），已按前端展示要求格式化。"""
    try:
        ep = _get_engine_event_panel(request)
        if ep is None:
            return {"success": True, "events": []}
        raw_events = ep.get_events()[-limit:]
        formatted = [format_event(_event_to_dict(ev)) for ev in raw_events]
        return {"success": True, "count": len(formatted), "events": formatted}
    except Exception as ex:
        return {"success": False, "error": str(ex)}


@app.get("/api/events/pending", tags=["events"])
async def get_events_pending(clear: int = 1, request: Request = None):
    """返回未排队事件（自上次清空后新增），已按前端展示要求格式化。"""
    try:
        ep = _get_engine_event_panel(request)
        if ep is None:
            return {"success": True, "events": []}
        raw_events = ep.get_pending(clear=bool(clear))
        formatted = [format_event(_event_to_dict(ev)) for ev in raw_events]
        return {"success": True, "count": len(formatted), "events": formatted}
    except Exception as ex:
        return {"success": False, "error": str(ex)}


@app.get("/api/events/timer-queue", tags=["events"])
async def get_events_timer_queue(request: Request, session_id: str = ""):
    """返回当前定时器队列（EventDriver heap 中的待触发定时器），已按前端展示要求格式化。"""
    try:
        now_ts = None
        event_driver = None
        sim_session_map = getattr(request.app.state, "_sim_session_map", {})
        if session_id and session_id in sim_session_map:
            session = sim_session_map[session_id]
            simulator = session.get("simulator")
            if simulator is not None:
                now_ts = getattr(simulator, "clock", None)
                pe = getattr(simulator, "_pool_engine", None)
                if pe is None:
                    inner = getattr(simulator, "_engine", None)
                    pe = getattr(inner, "_pool_engine", None) if inner else None
                if pe is not None:
                    event_driver = pe._components.get("event_driver") if hasattr(pe, "_components") else None
        if event_driver is None:
            ep = _get_engine_event_panel(request)
            if ep is not None:
                event_driver = getattr(ep, "_event_driver", None)
                pe = getattr(ep, "bus", None)
        specs = []
        if event_driver is not None:
            for idx, entry in enumerate(getattr(event_driver, '_heap', [])):
                fire_time = entry[0] if isinstance(entry, (list, tuple)) and len(entry) >= 2 else 0
                spec = entry[2] if isinstance(entry, (list, tuple)) and len(entry) >= 3 else None
                params = getattr(spec, 'params', {}) if spec else {}
                kind = params.get("kind", '')
                event_type = params.get("event_type") or ("EdgeTimer" if kind == "edge" else "TimerQueued")
                specs.append({
                    "edge_id": params.get("eid", ''),
                    "pool_id": params.get("tgt", ''),
                    "code": params.get("code", ''),
                    "kind": kind,
                    "event_type": event_type,
                    "fire_at": fire_time,
                    "interval": getattr(spec, 'interval', None) if spec else None,
                    "details": {
                        "fire_at": fire_time,
                        "kind": kind,
                        "queue_position": idx,
                    },
                })
            if now_ts is None:
                state = getattr(event_driver, "_state", None)
                if state is not None:
                    now_ts = state.time_source.get("current_ts")
        now_ms = normalize_display_ms(now_ts) if now_ts is not None else None
        return format_timer_queue(specs, now_ms=now_ms)
    except Exception as ex:
        return {"success": False, "error": str(ex), "count": 0, "now": None, "timers": []}


@app.get("/api/events/stream", tags=["events"])
async def events_stream(request: Request):
    """SSE事件流端点：订阅EventBus所有事件，实时推送到前端。"""
    bus = request.app.state.bus
    from datetime import datetime
    import time as _time
    
    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue(maxsize=10000)
        
        def event_callback(event):
            try:
                ev_type = type(event).__name__
                ev_dict = {}
                if hasattr(event, '__dict__'):
                    ev_dict = {k: v for k, v in event.__dict__.items() if not k.startswith('_')}
                else:
                    ev_dict = dict(event) if isinstance(event, dict) else {}
                
                code = ''
                pool_id = ''
                edge_id = ''
                node_id = ''
                ts_val = ev_dict.get('ts', 0.0) or ev_dict.get('timestamp', 0.0) or _time.time()
                
                if ev_type == 'Signal':
                    signal_type = ev_dict.get('signal_type', '')
                    if signal_type in ('BUY', 'SELL'):
                        ev_type = signal_type
                    code = str(ev_dict.get('code', '') or '')
                    pool_id = str(ev_dict.get('pool_id', '') or '')
                elif ev_type == 'OrderPlaced':
                    order = ev_dict.get('order', {}) or {}
                    code = str(order.get('code', '') or '')
                elif ev_type == 'OrderFilled':
                    fill = ev_dict.get('fill', {}) or {}
                    code = str(fill.get('code', '') or '')
                elif ev_type == 'PositionUpdated':
                    tracker = ev_dict.get('tracker', {}) or {}
                    code = str(tracker.get('code', '') or '')
                    node_id = str(tracker.get('node_id', '') or '')
                    pool_id = node_id
                elif ev_type == 'TransferExecuted':
                    codes = ev_dict.get('codes', []) or []
                    code = ','.join(list(codes)[:5]) + ('...' if len(codes) > 5 else '')
                    pool_id = str(ev_dict.get('tgt', '') or '')
                    node_id = pool_id
                elif ev_type == 'TTLExpired':
                    codes = ev_dict.get('codes', []) or []
                    code = ','.join(list(codes)[:5]) + ('...' if len(codes) > 5 else '')
                    node_id = str(ev_dict.get('node_id', '') or '')
                    pool_id = node_id
                elif ev_type == 'EdgeFired':
                    edge_id = str(ev_dict.get('eid', '') or '')
                    changed_codes = ev_dict.get('changed_codes', []) or []
                    code = ','.join(list(changed_codes)[:5]) + ('...' if len(changed_codes) > 5 else '')
                elif ev_type == 'TickReceived':
                    tick_data = ev_dict.get('tick_data', {}) or {}
                    code = str(ev_dict.get('code', '') or tick_data.get('code', '') or tick_data.get('symbol', '') or '')
                elif ev_type == 'BarComposed':
                    code = str(ev_dict.get('code', '') or '')
                elif ev_type == 'FormulaEvaluated':
                    code = str(ev_dict.get('code', '') or '')
                    if not ts_val or ts_val < 10000:
                        ts_val = _time.time()
                elif ev_type == 'DataChanged':
                    codes = ev_dict.get('codes', []) or []
                    code = ','.join(list(codes)[:5]) + ('...' if len(codes) > 5 else '')
                else:
                    code = str(ev_dict.get('code', '') or ev_dict.get('stock_code', '') or '')
                    pool_id = str(ev_dict.get('pool_id', '') or ev_dict.get('target_id', '') or ev_dict.get('source_id', '') or '')
                    edge_id = str(ev_dict.get('edge_id', '') or ev_dict.get('eid', '') or '')
                    node_id = str(ev_dict.get('node_id', '') or ev_dict.get('nid', '') or pool_id)
                
                if isinstance(ts_val, (int, float)) and ts_val > 0:
                    try:
                        if ts_val > 1e12:
                            ts_val = ts_val / 1000.0
                        ts = datetime.fromtimestamp(ts_val)
                    except (ValueError, OSError):
                        ts = datetime.now()
                        ts_val = _time.time()
                else:
                    ts = datetime.now()
                    ts_val = _time.time()
                
                event_data = {
                    "event_type": ev_type,
                    "code": code,
                    "pool_id": pool_id,
                    "node_id": node_id,
                    "edge_id": edge_id,
                    "details": {k: v for k, v in ev_dict.items() if k not in ('code', 'pool_id', 'edge_id', 'node_id', 'time', 'ts', 'timestamp', 'tick_data', 'order', 'fill', 'tracker')},
                    "time": ts.strftime("%H:%M:%S"),
                    "timestamp": float(ts_val)
                }
                try:
                    queue.put_nowait(format_event(event_data))
                except asyncio.QueueFull:
                    pass
            except Exception:
                import traceback
                traceback.print_exc()
        
        if bus is not None and hasattr(bus, 'subscribe_any'):
            unsubscribe = bus.subscribe_any(event_callback)
        else:
            unsubscribe = None
        
        try:
            yield f": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event_data = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(event_data, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield f": heartbeat\n\n"
        finally:
            if unsubscribe is not None:
                try:
                    unsubscribe()
                except Exception:
                    pass
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@app.get("/api/pools/{pool_id}/nodes/{node_id}/stocks", tags=["pools"])
async def api_get_node_stocks(pool_id: str, node_id: str, request: _Request):
    """返回指定池指定节点的股票列表。"""
    try:
        engine = request.app.state.engine
        storage = request.app.state.storage
        
        node_stocks = []
        
        sims = getattr(request.app.state, "_simulators", {})
        for sim in sims.values():
            ms = getattr(sim, "_mode_state", None)
            if ms and isinstance(ms.get("node_stocks"), dict):
                ns = ms["node_stocks"].get(node_id)
                if ns and isinstance(ns, list):
                    node_stocks = ns
                    break
            pe = getattr(sim, "_pool_engine", None)
            if pe is None:
                inner = getattr(sim, "_engine", None)
                pe = getattr(inner, "_pool_engine", None) if inner else None
            if pe and hasattr(pe, 'state') and hasattr(pe.state, 'get_pool'):
                ns = pe.state.get_pool(node_id).get_stocks()
                if ns and isinstance(ns, list):
                    node_stocks = ns
                    break
        
        if not node_stocks:
            sim_session_map = getattr(request.app.state, "_sim_session_map", {})
            for session in sim_session_map.values():
                simulator = session.get("simulator")
                if simulator is None:
                    continue
                ms = getattr(simulator, "_mode_state", None)
                if ms and isinstance(ms.get("node_stocks"), dict):
                    ns = ms["node_stocks"].get(node_id)
                    if ns and isinstance(ns, list):
                        node_stocks = ns
                        break
                pe = getattr(simulator, "_pool_engine", None)
                if pe is None:
                    inner = getattr(simulator, "_engine", None)
                    pe = getattr(inner, "_pool_engine", None) if inner else None
                if pe and hasattr(pe, 'state') and hasattr(pe.state, 'get_pool'):
                    ns = pe.state.get_pool(node_id).get_stocks()
                    if ns and isinstance(ns, list):
                        node_stocks = ns
                        break
        
        if not node_stocks:
            replays = getattr(request.app.state, "_replay_engines", {})
            for re in replays.values():
                snapshot = re.get_current_snapshot() if hasattr(re, 'get_current_snapshot') else {}
                sp = snapshot.get("state_pools", {})
                ni = sp.get(node_id, {})
                if isinstance(ni, dict):
                    stocks = ni.get("stocks", ni.get("stock_list", []))
                    if stocks:
                        node_stocks = stocks
                        break
        
        if not node_stocks:
            pool = storage.get_pool(pool_id)
            if pool:
                for n in pool.get("nodes", []):
                    nid = str(n.get("id", ""))
                    if nid == str(node_id) or n.get("label") == node_id:
                        ns = n.get("params", {}).get("stocks", n.get("stocks", []))
                        if isinstance(ns, list):
                            node_stocks = ns
                        break
        
        result = []
        for s in node_stocks:
            if isinstance(s, dict):
                code = s.get("code", s.get("symbol", ""))
                code = str(code)
                if len(code) == 7 and code.startswith("fz"):
                    code = "fz" + code[2:].zfill(6)
                elif len(code) == 6 and code.isdigit():
                    pass
                elif len(code) == 5 and code.isdigit():
                    code = code.zfill(6)
                result.append({
                    "code": code,
                    "name": s.get("name", s.get("stock_name", "")),
                    "price": s.get("price", s.get("close", s.get("last_price", 0))),
                    "change_pct": s.get("change_pct", s.get("pct_change", s.get("changepercent", 0))),
                    "enter_time": s.get("enter_time", s.get("in_time", "")),
                    "volume": s.get("volume", s.get("vol", 0))
                })
            elif isinstance(s, str):
                code = str(s)
                if len(code) == 7 and code.startswith("fz"):
                    code = "fz" + code[2:].zfill(6)
                elif len(code) == 5 and code.isdigit():
                    code = code.zfill(6)
                result.append({"code": code, "name": "", "price": 0, "change_pct": 0, "enter_time": "", "volume": 0})
        
        return {"code": 0, "data": {"node_id": node_id, "stocks": result, "count": len(result)}}
    except Exception as ex:
        return {"code": 1, "msg": str(ex)}


@app.post("/api/pools/{pool_id}/control/{action}", tags=["pools"])
async def api_control_pool(pool_id: str, action: str, request: _Request):
    """控制股票池运行：start/pause/stop。

    Task 3：仿真模式运行控制操作在执行后通过 EventBus 发布
    SimulationStateChanged 事件，前端仅订阅事件更新 UI。
    """
    try:
        engine = request.app.state.engine
        action = action.lower()

        if action not in ("start", "pause", "resume", "stop"):
            return {"code": 1, "msg": f"未知action: {action}，支持 start/pause/resume/stop"}

        bus = getattr(request.app.state, "bus", None)
        from core.event_bus import SimulationStateChanged

        def _publish_state(state: str, session_id: str = ""):
            if bus is not None:
                try:
                    bus.publish(SimulationStateChanged(state=state, session_id=session_id, ts=time.time()))
                except Exception:
                    pass

        sims = getattr(request.app.state, "_simulators", {})
        sim = sims.get(pool_id)
        if sim is not None:
            if action == "start" or action == "resume":
                if hasattr(sim, 'resume'):
                    sim.resume()
                _publish_state("running")
                return {"code": 0, "data": {"pool_id": pool_id, "action": action, "status": "running"}}
            elif action == "pause":
                if hasattr(sim, 'pause'):
                    sim.pause()
                _publish_state("paused")
                return {"code": 0, "data": {"pool_id": pool_id, "action": action, "status": "paused"}}
            elif action == "stop":
                if hasattr(sim, 'stop'):
                    try:
                        sim.stop()
                    except Exception:
                        pass
                sims.pop(pool_id, None)
                if hasattr(sim, 'reset'):
                    try:
                        sim.reset()
                    except Exception:
                        pass
                _publish_state("stopped")
                return {"code": 0, "data": {"pool_id": pool_id, "action": action, "status": "stopped"}}

        sim_session_map = getattr(request.app.state, "_sim_session_map", {})
        target_session = None
        for sid, session in sim_session_map.items():
            if session.get("pool_id") == pool_id:
                target_session = (sid, session)
                break

        if target_session is not None:
            sid, session = target_session
            simulator = session.get("simulator")
            if action == "start" or action == "resume":
                if simulator and hasattr(simulator, 'resume'):
                    simulator.resume()
                _publish_state("running", session_id=sid)
                return {"code": 0, "data": {"pool_id": pool_id, "session_id": sid, "action": action, "status": "running"}}
            elif action == "pause":
                if simulator and hasattr(simulator, 'pause'):
                    simulator.pause()
                _publish_state("paused", session_id=sid)
                return {"code": 0, "data": {"pool_id": pool_id, "session_id": sid, "action": action, "status": "paused"}}
            elif action == "stop":
                if simulator and hasattr(simulator, 'stop'):
                    try:
                        simulator.stop()
                    except Exception:
                        pass
                sim_session_map.pop(sid, None)
                if simulator and hasattr(simulator, 'reset'):
                    try:
                        simulator.reset()
                    except Exception:
                        pass
                _publish_state("stopped", session_id=sid)
                return {"code": 0, "data": {"pool_id": pool_id, "session_id": sid, "action": action, "status": "stopped"}}

        if action == "start":
            ok, err = _ensure_mock_data_source()
            if not ok:
                pass
            pool_config = _resolve_pool_config(pool_id)
            if pool_config is None:
                return {"code": 1, "msg": f"池不存在: {pool_id}"}
            simulator = _create_runtime_simulator(pool_config)
            if not hasattr(request.app.state, "_simulators"):
                request.app.state._simulators[pool_id] = simulator

            try:
                simulator.initialize()
            except Exception as ex:
                logger.warning("simulator.initialize() 失败: %s", ex)
                return {"code": 1, "msg": f"仿真器初始化失败: {ex}"}

            if not hasattr(request.app.state, "_sim_session_map"):
                request.app.state._sim_session_map = {}
            sid = uuid.uuid4().hex
            request.app.state._sim_session_map[sid] = {
                "simulator": simulator,
                "pool_id": pool_id,
                "config": pool_config,
                "events": [],
                "created_at": asyncio.get_event_loop().time(),
            }

            try:
                if bus is not None:
                    from core.event_bus import PoolLoaded, ModeChanged
                    bus.publish(PoolLoaded(pool_config=pool_config, source_format="json"))
                    bus.publish(ModeChanged(mode_id="simulation", prev_mode="live"))
                    bus.publish(SimulationStateChanged(state="running", session_id=sid, ts=time.time()))
            except Exception as ex:
                logger.warning("发布 PoolLoaded/ModeChanged/SimulationStateChanged 事件失败: %s", ex)

            return {"code": 0, "data": {"pool_id": pool_id, "session_id": sid, "action": action, "status": "running"}}
        elif action == "pause":
            if hasattr(engine, 'pause_loop'):
                engine.pause_loop()
            return {"code": 0, "data": {"pool_id": pool_id, "action": action, "status": "paused"}}
        elif action == "stop":
            if hasattr(engine, 'stop_loop'):
                try:
                    await engine.stop_loop()
                except Exception:
                    pass
            return {"code": 0, "data": {"pool_id": pool_id, "action": action, "status": "stopped"}}

        return {"code": 1, "msg": f"未找到活跃的池会话: {pool_id}"}
    except Exception as ex:
        return {"code": 1, "msg": str(ex)}


@app.get("/api/pool/{name:path}/events", tags=["pool"])
async def get_pool_events(name: str, max_n: int = 100):
    """返回引擎事件队列的内容（最多 max_n 条）。"""
    engine = app.state.engine
    events = []
    q = getattr(engine, '_event_queue', None)
    if q is None:
        return {"success": False, "error": "事件队列未初始化"}
    # 排空队列
    while not q.empty() and len(events) < max_n:
        try:
            events.append(q.get_nowait())
        except Exception:
            break
    return {"success": True, "count": len(events), "data": events}


@app.get("/api/pool/{name:path}/signals", tags=["pool"])
async def get_pool_signals(name: str, max_n: int = 100):
    """返回引擎信号队列的内容（BUY/SELL）。"""
    engine = app.state.engine
    signals = []
    q = getattr(engine, '_signal_queue', None)
    if q is None:
        return {"success": False, "error": "信号队列未初始化"}
    while not q.empty() and len(signals) < max_n:
        try:
            signals.append(q.get_nowait())
        except Exception:
            break
    return {"success": True, "count": len(signals), "data": signals}


@app.post("/api/pool/{name:path}/replay", tags=["pool"], dependencies=[Depends(verify_api_key)])
async def replay_pool(name: str, request: _Request):
    """回放模式：执行 num_bars 根 K 线后返回 node_stocks 状态。"""
    try:
        body = await request.json()
        num_bars = int(body.get("num_bars", 30))
    except Exception:
        num_bars = 30
    engine = app.state.engine
    pool_config = _resolve_pool_config(name)
    if pool_config is None:
        return {"success": False, "error": f"池不存在: {name}（未找到 XML 文件或 SQLite 记录）"}
    try:
        replay_engine = KLineReplayEngine(engine, storage=app.state.storage, bus=app.state.bus)
        engine.kline_replay_engine = replay_engine
        load_res = replay_engine.load_kline_data(
            pool_config,
            base_period="day",
            date_range=["2024-01-01", "2024-03-01"],
            pool_id=name,
        )
        if not load_res.get("success"):
            return {"success": False, "error": f"加载 K 线数据失败: {load_res.get('error', '未知错误')}"}
        steps = []
        for _ in range(num_bars):
            step_res = replay_engine.step()
            if step_res.get("error"):
                break
            steps.append(step_res)
        snapshot = replay_engine.get_current_snapshot()
        node_summary = {
            nid: info.get("stock_count", 0)
            for nid, info in snapshot.get("state_pools", {}).items()
        }
        events = snapshot.get("recent_events", []) or replay_engine._last_bar_events[-20:]
        session_id = getattr(replay_engine, "_session_id", name)
        return {
            "success": True,
            "data": {
                "session_id": session_id,
                "steps": steps,
                "node_summary": node_summary,
                "events": events,
            },
        }
    except Exception as ex:
        return {"success": False, "error": f"回放失败: {str(ex)}"}


def _get_or_create_simulator(name: str, engine) -> tuple:
    """获取或创建仿真器实例（按池名缓存，保证状态连续性）。

    返回 (simulator, error_str)；成功时 error_str 为 None。
    """
    if not hasattr(app.state, "_simulators"):
        app.state._simulators = {}
    simulator = app.state._simulators.get(name)
    if simulator is not None:
        return simulator, None

    pool_config = _resolve_pool_config(name)
    if pool_config is None:
        return None, f"池不存在: {name}（未找到 XML 文件或 SQLite 记录）"
    try:
        try:
            from .core.runtime_mode_module import RuntimeSimulator
        except ImportError:
            from core.runtime_mode_module import RuntimeSimulator
        # 必须注入 bus，使 RuntimeSimulator.step() 末尾发布 SimulationStep 事件，
        # 驱动 TickBarModule→ExecutionModule→TradeModule 完整事件链
        simulator = RuntimeSimulator(
            pool_config,
            engine=engine,
            bus=getattr(app.state, "bus", None),
        )
        simulator.initialize()
        pe = engine._pool_engine if hasattr(engine, '_pool_engine') else None
        if pe is not None:
            tick_source = pe._components.get("tick_source")
            tick_bar = getattr(app.state, "tick_bar", None)
            if tick_source is not None and tick_bar is not None:
                tick_bar._tick_source = tick_source
                tick_bar._mode_id = "simulation"
                logger.info("synced MockDataSource to TickBarModule: codes=%d clock_start=%.1f",
                            len(getattr(tick_source, '_codes', [])),
                            getattr(tick_source, '_clock_start', 0))
        app.state._simulators[name] = simulator
        return simulator, None
    except Exception as ex:
        return None, f"创建 RuntimeSimulator 失败: {str(ex)}"


async def _run_simulation_step(name: str, delta: float = 60.0) -> dict:
    """仿真 step 公共实现（支持 XML 和 SQLite 池）。

    simulator 实例按池名缓存在 app.state._simulators 中，
    保证多次 step 调用时 virtual_clock 持续递增、node_stocks 持续演化。
    """
    engine = app.state.engine
    tq = app.state.tq
    state = tq.get_data_source_state() if hasattr(tq, 'get_data_source_state') else {}
    if state.get('active') != 'mock':
        return {"success": False, "error": "仿真模式需先调用 POST /api/data_source/select/mock 显式选 mock"}

    simulator, err = _get_or_create_simulator(name, engine)
    if simulator is None:
        return {"success": False, "error": err}

    try:
        effective_delta = delta * float(getattr(simulator, "speed", 1.0) or 1.0)
        result = await simulator.astep(effective_delta)
        events = result.get("events", [])
        if not events:
            events = []
        node_stocks = (
            simulator._mode_state.get("node_stocks", {})
            if simulator._mode_state else {}
        )
        node_summary = {nid: len(ss) for nid, ss in node_stocks.items() if isinstance(ss, list) and not nid.startswith("_")}
        return {
            "success": True,
            "data": {
                "step": "ok",
                "virtual_clock": getattr(simulator, 'clock', None),
                "node_summary": node_summary,
                "node_stocks": {nid: len(ss) for nid, ss in node_stocks.items() if isinstance(ss, list) and not nid.startswith("_")},
                "events": events[:20],
                "event_count": len(events),
            }
        }
    except Exception as ex:
        return {"success": False, "error": f"仿真 step 失败: {str(ex)}"}


@app.post("/api/pool/{name:path}/simulation/step", tags=["pool"], dependencies=[Depends(verify_api_key)])
async def simulation_step(name: str, request: _Request):
    """仿真模式：step 一次（需先显式选 mock）。

    simulator 实例按池名缓存，多次调用时 virtual_clock 持续递增。
    """
    try:
        body = await request.json()
        delta = float(body.get("delta", 60.0))
    except Exception:
        delta = 60.0
    return await _run_simulation_step(name, delta)


@app.post("/api/pool/{name:path}/sim/init", tags=["pool"], dependencies=[Depends(verify_api_key)])
async def sim_init(name: str):
    """初始化（或重置）仿真会话：创建新 simulator 并缓存。"""
    engine = app.state.engine
    tq = app.state.tq
    state = tq.get_data_source_state() if hasattr(tq, 'get_data_source_state') else {}
    if state.get('active') != 'mock':
        return {"success": False, "error": "仿真模式需先调用 POST /api/data_source/select/mock 显式选 mock"}
    if not hasattr(app.state, "_simulators"):
        app.state._simulators = {}
    old = app.state._simulators.pop(name, None)
    simulator, err = _get_or_create_simulator(name, engine)
    if simulator is None:
        return {"success": False, "error": err}
    return {
        "success": True,
        "data": {
            "pool": name,
            "virtual_clock": simulator.clock,
            "reset": old is not None,
        }
    }


@app.post("/api/pool/{name:path}/sim/start", tags=["pool"], dependencies=[Depends(verify_api_key)])
async def sim_start(name: str, request: _Request):
    """仿真模式别名端点：同 /api/pool/{name}/simulation/step。"""
    try:
        body = await request.json()
        delta = float(body.get("delta", 60.0))
    except Exception:
        delta = 60.0
    return await _run_simulation_step(name, delta)


def get_simulator(name: str):
    """FastAPI 依赖：按池名查找仿真会话，不存在则 404。

    init / start 涉及创建会话，不走此依赖；其余 sim 动作统一依赖此函数。
    """
    simulators = getattr(app.state, "_simulators", {})
    sim = simulators.get(name)
    if sim is None:
        raise _HTTPException(404, f"仿真会话不存在: {name}")
    return sim


# 表驱动 sim 动作映射：URL action → RuntimeSimulator 方法名
# init / start 涉及创建会话，保留为独立路由；pause/resume/stop/get_state/set_speed 走合并路由
_SIM_ACTIONS: Dict[str, str] = {
    "pause": "pause",
    "resume": "resume",
    "stop": "reset",
    "get_state": "get_state_snapshot",
    "set_speed": "set_speed",
}


@app.post("/api/pool/{name:path}/sim/{action}", tags=["pool"], dependencies=[Depends(verify_api_key)])
async def sim_action(name: str, action: str, request: _Request, simulator=Depends(get_simulator)):
    """表驱动的仿真会话控制（合并 sim_pause/resume/stop/get_state/set_speed）。

    init / start 涉及创建会话，保留为独立路由；其余 5 个动作统一走此端点。
    """
    if action not in _SIM_ACTIONS:
        raise _HTTPException(400, f"未知仿真操作: {action}")
    # stop：从会话池移除并 reset
    if action == "stop":
        getattr(app.state, "_simulators", {}).pop(name, None)
        try:
            simulator.reset()
        except Exception:
            pass
        return {"success": True, "data": {"pool": name, "stopped": True}}
    # set_speed：从请求体读取 speed 并设置属性（RuntimeSimulator 无 set_speed 方法）
    if action == "set_speed":
        try:
            body = await request.json()
            speed = float(body.get("speed", 1.0))
        except Exception:
            speed = 1.0
        if speed <= 0:
            speed = 1.0
        simulator.speed = speed
        return {"success": True, "data": {"pool": name, "speed": speed}}
    # pause / resume / get_state：方法调用
    method = getattr(simulator, _SIM_ACTIONS[action])
    if action == "get_state":
        try:
            return {"success": True, "data": method()}
        except Exception as ex:
            return {"success": False, "error": str(ex)}
    method()
    return {"success": True, "data": {"pool": name, "paused": action == "pause"}}


# ══════════════════════════════════════════════════════════════════════
#  基于 session_id 的仿真 API（供前端 web/js/main.js / web/js/event-panel.js 调用）
# ══════════════════════════════════════════════════════════════════════

def _ensure_mock_data_source():
    """确保当前数据源为 mock，供仿真模式使用。"""
    tq = app.state.tq
    state = tq.get_data_source_state() if hasattr(tq, 'get_data_source_state') else {}
    if state.get('active') == 'mock':
        return True, None
    if not hasattr(tq, 'set_active_source'):
        return False, "数据源不支持切换到 mock"
    try:
        tq.set_active_source('mock')
        return True, None
    except Exception as ex:
        return False, f"切换 mock 数据源失败: {ex}"


def _create_runtime_simulator(pool_config: dict):
    """创建 RuntimeSimulator 实例（初始化延迟到第一次 step/astep）。

    必须注入 ``bus=app.state.bus``，使 RuntimeSimulator.step() 末尾发布
    ``SimulationStep`` 事件，驱动 TickBarModule→ExecutionModule→TradeModule
    完整事件链。不注入 bus 会导致 SimulationStep 不发布、TickReceived/
    BarComposed/FormulaEvaluated/StockFiltered/EdgeFired 等事件全部缺失。
    """
    from core.runtime_mode_module import RuntimeSimulator
    simulator = RuntimeSimulator(
        pool_config,
        engine=app.state.engine,
        bus=getattr(app.state, "bus", None),
    )
    return simulator


def _normalize_sim_event(ev: dict) -> dict:
    """统一事件格式为 {event_type, code, pool_id, details, time}。

    details 包含事件特有字段（eid/sid/tid/entered/exited/formula_ref/result 等），
    使前端能展示完整事件信息，避免字段丢失。
    """
    if not isinstance(ev, dict):
        return {"event_type": "UNKNOWN", "code": "", "pool_id": "", "details": {}, "time": 0}
    # 统一字段优先取事件本身字段
    code = ev.get("code", "")
    pool_id = ev.get("pool_id", "")
    t = ev.get("time")
    if t is None:
        t = ev.get("ts")
    if t is None:
        t = ev.get("timestamp", 0)
    # details 包含所有非统一字段（保留事件特有字段供前端展示）
    # 注意 "details" 必须在 unified_keys 中，否则遍历 ev.items() 时会把
    # details 字段本身合并到 details 字典中，造成 details["details"] = details
    # 的循环引用，触发 FastAPI jsonable_encoder RecursionError。
    unified_keys = {"event_type", "code", "pool_id", "time", "ts", "timestamp",
                    "action", "detail", "details"}
    # 使用 dict(...) 创建副本，避免修改原事件字典（防止多次调用累积污染）
    raw_details = ev.get("details") or ev.get("detail") or {}
    if isinstance(raw_details, dict):
        details = dict(raw_details)
    else:
        details = {"value": raw_details}
    # 合并事件特有字段（eid/sid/tid/entered/exited/formula_ref/result 等）
    for k, v in ev.items():
        if k in unified_keys or k in details:
            continue
        details[k] = v
    # 从 details 中提取 pool_id（Executed 事件的 sid/tid 等）
    if not pool_id:
        pool_id = details.get("target_id") or details.get("source_id") or details.get("sid") or details.get("tid") or ""
    return {
        "event_type": ev.get("event_type", ev.get("action", "UNKNOWN")),
        "code": code,
        "pool_id": pool_id,
        "details": details,
        "time": t,
    }


def _get_session(sim_session_map: dict, session_id: str):
    """获取会话，仅校验 session 是否存在。

    仿真器可能仍在后台初始化中，因此不再要求 simulator 字段非空；
    各 action 分支自行判断初始化状态。
    """
    session = sim_session_map.get(session_id)
    if session is None:
        return None, "会话不存在"
    return session, None


async def _warm_simulator(session_id: str, pool_config: dict, speed: float):
    """后台预热仿真器：创建、初始化并执行 astep(0.0) 完成 heavyweight 初始化。

    在 /api/sim/start 返回后立即启动，使首次正式 step 直接走已初始化状态。
    异常会被捕获并记录，不会导致未捕获任务错误。
    """
    sim_session_map = getattr(app.state, "_sim_session_map", {})
    session = sim_session_map.get(session_id)
    if session is None:
        return
    try:
        # 切换数据源；该操作涉及全局 TqAdapter 状态，保留在事件循环中执行。
        ok, err = _ensure_mock_data_source()
        if not ok:
            raise RuntimeError(err)

        # 创建 RuntimeSimulator 可能涉及较重的 PoolEngine 配置读取，
        # 放到线程池避免阻塞 Uvicorn 事件循环。
        simulator = await asyncio.to_thread(_create_runtime_simulator, pool_config)
        simulator.speed = speed

        # initialize() 创建 asyncio.Queue/Event，必须绑定到 Uvicorn 事件循环。
        simulator.initialize()

        # astep(0.0) 仅完成 mode_state 初始化，不推进虚拟时间。
        await simulator.astep(0.0)

        session["simulator"] = simulator
        session["speed"] = speed
        try:
            pe = getattr(simulator, "_engine", None)
            if pe is not None:
                pe = getattr(pe, "_pool_engine", None)
            if pe is not None:
                bus = pe._components.get("event_bus")
                if bus is not None and hasattr(bus, "total_published"):
                    session["event_offset"] = bus.total_published
        except Exception:
            session["event_offset"] = 0
    except Exception as ex:
        logger.exception("仿真器后台预热失败 (session_id=%s): %s", session_id, ex)
        session["init_error"] = str(ex)
    finally:
        session["init_event"].set()


@app.post("/api/sim/start", tags=["sim"])
async def sim_start_session(request: _Request):
    """启动基于 session_id 的仿真会话。

    请求体: {pool_id: string, speed: number} 或 {config: object, speed: number}
    响应: {code: 0, data: {session_id: string}}
    """
    try:
        body = await request.json()
    except Exception as ex:
        return {"code": 1, "msg": f"请求体解析失败: {ex}"}

    pool_id = body.get("pool_id")
    config = body.get("config")
    speed = float(body.get("speed", 1.0) or 1.0)
    if speed <= 0:
        speed = 1.0

    if pool_id:
        pool_config = _resolve_pool_config(pool_id)
        if pool_config is None:
            return {"code": 1, "msg": f"池不存在: {pool_id}"}
    elif config and isinstance(config, dict):
        pool_config = dict(config)
        if "nodes" not in pool_config:
            return {"code": 1, "msg": "config 缺少 nodes 字段"}
        pool_id = pool_config.get("id") or pool_config.get("name") or f"sim_{uuid.uuid4().hex[:12]}"
        pool_config["id"] = pool_id
        _enrich_tdx_node_data(pool_config)
    else:
        return {"code": 1, "msg": "缺少 pool_id 或 config 参数"}

    session_id = uuid.uuid4().hex
    if not hasattr(request.app.state, "_sim_session_map"):
        request.app.state._sim_session_map = {}
    request.app.state._sim_session_map[session_id] = {
        "simulator": None,
        "pool_id": pool_id,
        "config": pool_config,
        "events": [],
        "event_offset": 0,
        "created_at": asyncio.get_event_loop().time(),
        "init_event": asyncio.Event(),
        "init_task": None,
        "init_error": None,
    }

    # Task 14：后台执行创建/初始化/astep(0.0)，完成 run_mode 重量级初始化，
    # 不阻塞 start 响应。
    task = asyncio.create_task(_warm_simulator(session_id, pool_config, speed))
    request.app.state._sim_session_map[session_id]["init_task"] = task

    return {"code": 0, "data": {"session_id": session_id, "pool_id": pool_id}}


@app.post("/api/sim/control", tags=["sim"])
async def sim_control_session(request: _Request):
    """控制仿真会话。

    请求体: {session_id: string, action: string, params?: object}
    action: stop | step | pause | resume

    Task 3：所有运行控制操作在执行后通过 EventBus 发布 SimulationStateChanged 事件，
    前端仅订阅事件更新 UI，禁止按钮回调直接修改前端状态。
    """
    try:
        body = await request.json()
    except Exception as ex:
        return {"code": 1, "msg": f"请求体解析失败: {ex}"}

    session_id = body.get("session_id", "")
    action = body.get("action", "")
    params = body.get("params") or {}

    sim_session_map = getattr(request.app.state, "_sim_session_map", {})
    session, err = _get_session(sim_session_map, session_id)
    if session is None:
        return {"code": 1, "msg": err}

    simulator = session.get("simulator")
    init_event = session.get("init_event")
    initializing = init_event is not None and not init_event.is_set()

    bus = getattr(request.app.state, "bus", None)
    from core.event_bus import SimulationStateChanged

    def _publish_state(state: str):
        if bus is not None:
            try:
                bus.publish(SimulationStateChanged(state=state, session_id=session_id, ts=time.time()))
            except Exception:
                pass

    if action == "stop":
        init_task = session.get("init_task")
        if init_task is not None and not init_task.done():
            init_task.cancel()
        if simulator is not None:
            try:
                simulator.stop()
            except Exception:
                pass
            try:
                simulator.reset()
            except Exception:
                pass
        sim_session_map.pop(session_id, None)
        _publish_state("stopped")
        return {"code": 0, "data": {"session_id": session_id, "stopped": True}}

    if action == "pause":
        if initializing or simulator is None:
            return {"code": 102, "status": "initializing", "msg": "仿真器正在后台初始化，请稍后再试"}
        simulator.pause()
        _publish_state("paused")
        return {"code": 0, "data": {"session_id": session_id, "paused": True}}

    if action == "resume":
        if initializing or simulator is None:
            return {"code": 102, "status": "initializing", "msg": "仿真器正在后台初始化，请稍后再试"}
        simulator.resume()
        _publish_state("running")
        return {"code": 0, "data": {"session_id": session_id, "paused": False}}

    if action == "step":
        if initializing or simulator is None:
            return {
                "code": 102,
                "status": "initializing",
                "data": {"session_id": session_id},
                "msg": "仿真器正在后台初始化，请稍后再试",
            }
        if session.get("init_error"):
            return {"code": 1, "status": "error", "msg": f"仿真器初始化失败: {session['init_error']}"}
        delta = float(params.get("delta", 60.0))
        if delta <= 0:
            delta = 60.0
        effective_delta = delta * float(session.get("speed", 1.0) or 1.0)
        try:
            result = await simulator.astep(effective_delta)
            # astep 返回 dict: {events, bar_data, changed_codes}
            normalized = [_normalize_sim_event(ev) for ev in result.get("events", [])]
            session["events"].extend(normalized)
            return {
                "code": 0,
                "data": {
                    "session_id": session_id,
                    "clock": simulator.clock,
                    "events": normalized,
                    "event_count": len(normalized),
                    "changed_codes": result.get("changed_codes", []),
                },
            }
        except Exception as ex:
            return {"code": 1, "msg": f"仿真 step 失败: {ex}"}

    return {"code": 1, "msg": f"未知 action: {action}"}


@app.get("/api/sim/events", tags=["sim"])
async def sim_get_events(session_id: str = "", since: str = "0", limit: int = 200):
    """获取仿真会话自 since 索引之后的事件列表。"""
    sim_session_map = getattr(app.state, "_sim_session_map", {})
    session, err = _get_session(sim_session_map, session_id)
    if session is None:
        return {"code": 1, "msg": err}
    try:
        since_idx = int(since)
    except Exception:
        since_idx = 0
    if since_idx < 0:
        since_idx = 0

    simulator = session.get("simulator")
    clock = getattr(simulator, "clock", None) if simulator else None
    bus_offset = session.get("bus_offset", session.get("event_offset", 0))

    ALLOWED_EVENTS = {
        "TickReceived", "DataChanged", "BarComposed", "FormulaEvaluated",
        "EdgeFired", "Executed", "TransferExecuted", "Signal", "TTLExpired",
        "OrderPlaced", "OrderFilled", "PositionUpdated", "ConfigLoaded",
        "SimulationStep", "TimeAdvanced", "StockFiltered", "AlertRaised",
        "EventLogged", "TickDue"
    }

    def _normalize_event(ev, ev_type, fallback_ts):
        if dataclasses.is_dataclass(ev) and not isinstance(ev, type):
            try:
                ev_dict = dataclasses.asdict(ev)
            except Exception:
                ev_dict = {}
                for attr in dir(ev):
                    if not attr.startswith("_"):
                        try:
                            val = getattr(ev, attr)
                            if not callable(val):
                                ev_dict[attr] = val
                        except Exception:
                            pass
        elif isinstance(ev, dict):
            ev_dict = dict(ev)
        else:
            return None
        ev_dict["event_type"] = ev_type
        ev_ts = ev_dict.get("ts") or ev_dict.get("time") or ev_dict.get("timestamp")
        if ev_ts is None:
            ev_ts = fallback_ts or 0
        ev_dict["time"] = ev_ts
        code = ""
        for k in ("code", "stock_code", "symbol"):
            if ev_dict.get(k):
                code = str(ev_dict[k])
                break
        ev_dict["code"] = code
        pool_id = str(ev_dict.get("pool_id", "") or ev_dict.get("target_id", "") or ev_dict.get("source_id", "") or ev_dict.get("sid", "") or ev_dict.get("tid", "") or ev_dict.get("node_id", "") or ev_dict.get("nid", ""))
        node_id = str(ev_dict.get("node_id", "") or ev_dict.get("nid", ""))
        edge_id = str(ev_dict.get("edge_id", "") or ev_dict.get("eid", ""))
        skip_keys = {"event_type", "type", "code", "stock_code", "symbol", "pool_id",
                     "target_id", "source_id", "node_id", "nid", "edge_id", "eid",
                     "time", "ts", "timestamp", "sid", "tid"}
        details = {}
        for k, v in ev_dict.items():
            if k in skip_keys:
                continue
            details[k] = v
        # DataChanged/BarComposed 关键字段提顶层（前端 event-panel.js 直接读 ev.codes/source/period/bar）
        result = {
            "event_type": ev_type,
            "type": ev_type,
            "code": code,
            "pool_id": pool_id,
            "node_id": node_id,
            "edge_id": edge_id,
            "details": details,
            "time": ev_ts,
            "ts": ev_ts,
        }
        for top_key in ("codes", "source", "period", "bar", "formula_ref", "result",
                        "entered", "exited", "order_id", "side", "qty", "price",
                        "fill_ts", "eid", "tid", "sid"):
            if top_key in ev_dict and top_key not in result:
                result[top_key] = ev_dict[top_key]
        return result

    if "events" not in session:
        session["events"] = []

    if simulator is not None:
        try:
            pe = getattr(simulator._engine, "_pool_engine", None) if hasattr(simulator, "_engine") and simulator._engine else None
            if pe is not None and hasattr(pe, "_components"):
                pe_bus = pe._components.get("event_bus")
                new_raw_events = []
                if pe_bus is not None and hasattr(pe_bus, "get_events_since"):
                    new_raw_events = pe_bus.get_events_since(bus_offset)
                elif pe_bus is not None and hasattr(pe_bus, "get_events"):
                    new_raw_events = pe_bus.get_events()[bus_offset:]
                else:
                    new_raw_events = []

                for ev in new_raw_events:
                    ev_type = type(ev).__name__
                    if ev_type not in ALLOWED_EVENTS:
                        continue
                    normalized_ev = _normalize_event(ev, ev_type, clock)
                    if normalized_ev is not None:
                        session["events"].append(normalized_ev)

                session["bus_offset"] = bus_offset + len(new_raw_events)
        except Exception as ex:
            logger.warning("sim_get_events EventBus 读取失败: %s", ex)

    MAX_EVENTS_CAP = 50000
    if len(session["events"]) > MAX_EVENTS_CAP:
        session["events"] = session["events"][-MAX_EVENTS_CAP:]

    all_events = session["events"]
    total = len(all_events)
    sliced = all_events[since_idx:since_idx + limit] if limit > 0 else all_events[since_idx:]

    normalized = []
    for ev in sliced:
        if not isinstance(ev, dict):
            continue
        if "details" in ev:
            normalized.append(ev)
        else:
            ev_type = ev.get("event_type", "UNKNOWN")
            code = str(ev.get("code", "") or ev.get("stock_code", ""))
            pool_id = str(ev.get("pool_id", "") or ev.get("target_id", "") or ev.get("source_id", "") or ev.get("sid", "") or ev.get("tid", "") or ev.get("node_id", "") or ev.get("nid", ""))
            node_id = str(ev.get("node_id", "") or ev.get("nid", ""))
            edge_id = str(ev.get("edge_id", "") or ev.get("eid", ""))
            skip_keys = {"event_type", "type", "code", "stock_code", "symbol", "pool_id",
                         "target_id", "source_id", "node_id", "nid", "edge_id", "eid",
                         "time", "ts", "timestamp", "sid", "tid"}
            details = {}
            for k, v in ev.items():
                if k in skip_keys:
                    continue
                details[k] = v
            ev_time = ev.get("time", ev.get("ts", 0))
            normalized.append({
                "event_type": ev_type,
                "type": ev_type,
                "code": code,
                "pool_id": pool_id,
                "node_id": node_id,
                "edge_id": edge_id,
                "details": details,
                "time": ev_time,
                "ts": ev_time,
            })

    formatted = [format_event(ev) for ev in normalized]
    return {"code": 0, "data": {"events": formatted, "total": total, "clock": clock}}



@app.get("/api/sim/state", tags=["sim"])
async def sim_get_session_state(session_id: str = ""):
    """获取仿真会话当前状态快照。"""
    sim_session_map = getattr(app.state, "_sim_session_map", {})
    session, err = _get_session(sim_session_map, session_id)
    if session is None:
        return {"code": 1, "msg": err}
    try:
        snapshot = session["simulator"].get_state_snapshot()
        return {"code": 0, "data": snapshot}
    except Exception as ex:
        return {"code": 1, "msg": f"获取状态失败: {ex}"}


@app.get("/api/sim/bars", tags=["sim"])
async def sim_get_bars(session_id: str = "", code: str = "", period: str = "1min", request: _Request = None):
    """获取仿真会话中某代码、某周期的 K 线数据。"""
    sim_session_map = getattr(app.state, "_sim_session_map", {})
    session, err = _get_session(sim_session_map, session_id)
    if session is None:
        return {"code": 1, "msg": err}

    simulator = session["simulator"]
    period_norm = _normalize_period(period)

    bars = []
    try:
        # 优先使用 simulator.get_bars()（读取 _bar_agg）
        if hasattr(simulator, "get_bars"):
            all_bars = simulator.get_bars(period=period_norm)
            df = all_bars.get(code) if isinstance(all_bars, dict) else None
            if df is None and code:
                from core.domain import _normalize_to_fz
                fz_code = _normalize_to_fz(code)
                df = all_bars.get(fz_code) if fz_code != code else None
            if df is not None and not df.empty:
                records = df.to_dict("records")
                bars = [
                    {
                        "datetime": r.get("datetime") or r.get("time"),
                        "open": float(r["open"]) if "open" in r else None,
                        "high": float(r["high"]) if "high" in r else None,
                        "low": float(r["low"]) if "low" in r else None,
                        "close": float(r["close"]) if "close" in r else None,
                        "volume": float(r["volume"]) if "volume" in r else None,
                    }
                    for r in records
                ]
        # 兜底：从 _mode_state['bars'] 读取
        if not bars and simulator._mode_state:
            ms_bars = simulator._mode_state.get("bars", {})
            code_bars = ms_bars.get(code) if isinstance(ms_bars, dict) else None
            if code_bars:
                bars = list(code_bars)
    except Exception as ex:
        logger.warning("读取 simulator bars 失败: %s", ex)

    if not bars and request is not None:
        bars = await _get_kline_bars(request, code, period_norm)

    data = {"bars": bars}
    # 若 simulator 上有公式结果/持仓，透传给前端
    if hasattr(simulator, "_formula_result"):
        data["formula_result"] = simulator._formula_result
    if hasattr(simulator, "_position"):
        data["position"] = simulator._position
    return {"code": 0, "data": data}


@app.get("/api/sim/batch_step", tags=["sim"])
async def sim_batch_step(session_id: str = "", steps: int = 10, delta: float = 60.0):
    """批量步进仿真会话：一次推进多步并汇总事件。

    参数:
      - session_id: 会话ID
      - steps: 步数（1~2000，默认10）
      - delta: 每步虚拟秒数（默认60.0）
    """
    sim_session_map = getattr(app.state, "_sim_session_map", {})
    session, err = _get_session(sim_session_map, session_id)
    if session is None:
        return {"code": 1, "msg": err}

    simulator = session["simulator"]
    steps = max(1, min(int(steps), 2000))
    if delta <= 0:
        delta = 60.0
    effective_delta = delta * float(getattr(simulator, "speed", 1.0) or 1.0)

    all_events = []
    done = 0
    last_err = None
    for _ in range(steps):
        try:
            result = await simulator.astep(effective_delta)
            events = result.get("events", [])
            done += 1
            if events:
                normalized = [_normalize_sim_event(ev) for ev in events]
                session["events"].extend(normalized)
                all_events.extend(normalized)
        except Exception as ex:
            last_err = str(ex)
            break

    return {
        "code": 0,
        "data": {
            "session_id": session_id,
            "stepped": done,
            "delta": delta,
            "clock": simulator.clock,
            "step": getattr(simulator, "step", 0),
            "event_count": len(all_events),
            "events": all_events,
            "error": last_err,
        },
    }


@app.get("/api/pool/{name:path}/pk/ranking", tags=["pool"])
async def get_pool_pk_ranking(name: str):
    """PK 排名别名端点：同 /api/pool/{name}/rankings。"""
    return await get_pool_rankings(name)


def _resolve_pool_config(name: str) -> dict | None:
    """按名称解析股票池配置：先 tdxpool/{name}.xml，再 config/{name}.json，再 SQLite（pool_id 或 name）。"""
    base_dir = os.path.join(os.path.dirname(__file__), 'tdxpool')
    xml_path = None
    try:
        xml_path = safe_path_join(base_dir, name + '.xml')
    except ValueError:
        pass
    if xml_path and os.path.isfile(xml_path):
        pool = _call_converter(xml_path, "tdx", "import")
        return _tdx_pool_to_frontend(pool, name) if hasattr(pool, 'cells') else pool

    config_dir = os.path.join(os.path.dirname(__file__), 'config')
    json_path = None
    try:
        json_path = safe_path_join(config_dir, name + '.json')
    except ValueError:
        pass
    if json_path and os.path.isfile(json_path):
        import json
        with open(json_path, 'r', encoding='utf-8') as f:
            pool = json.load(f)
        if isinstance(pool, dict) and "nodes" in pool:
            if "name" not in pool:
                pool["name"] = name
            if "pool_type" not in pool:
                pool["pool_type"] = "sim"
            return pool

    storage = app.state.storage
    pool = storage.get_pool(name)
    if pool is None or not isinstance(pool, dict):
        try:
            for p in storage.list_pools():
                if isinstance(p, dict) and p.get("name") == name:
                    pool = p
                    break
        except Exception:
            pass
    if pool is not None and isinstance(pool, dict):
        pool_config = dict(pool)
        params = pool_config.get("params") or {}
        if isinstance(params, dict):
            if "nodes" in params and "nodes" not in pool_config:
                pool_config["nodes"] = params["nodes"]
            if "edges" in params and "edges" not in pool_config:
                pool_config["edges"] = params["edges"]
        _enrich_tdx_node_data(pool_config)
        return pool_config
    return None


@app.post("/api/pool/{name:path}/live/start", tags=["pool"], dependencies=[Depends(verify_api_key)])
async def live_start(name: str):
    """实盘模式：启动持续循环（支持 tdxpool XML 文件名或 SQLite pool_id）。"""
    engine = app.state.engine
    tq = app.state.tq
    state = tq.get_data_source_state() if hasattr(tq, 'get_data_source_state') else {}
    _valid_statuses = getattr(app.state, '_valid_live_statuses', None)
    if _valid_statuses is None:
        import json as _json
        _cfg_path = os.path.join(os.path.dirname(__file__), 'config', 'data', 'data_providers.json')
        with open(_cfg_path, 'r', encoding='utf-8') as f:
            _valid_statuses = _json.load(f).get('valid_live_statuses', ['tdx_tq_ready', 'akshare_ready', 'user_selected_mock'])
        setattr(app.state, '_valid_live_statuses', _valid_statuses)
    if state.get('status') not in _valid_statuses:
        return {"success": False, "error": f"实盘模式需数据源就绪，当前状态: {state.get('status')}"}
    try:
        pool_config = _resolve_pool_config(name)
        if pool_config is None:
            return {"success": False, "error": f"池不存在: {name}（未找到 XML 文件或 SQLite 记录）"}
        if hasattr(engine, 'start_loop'):
            engine._loop_pool_config = pool_config
            engine.start_loop(pool_config, current_bar_data={})
        return {"success": True, "data": {"pool": name, "state": state}}
    except _HTTPException: raise
    except Exception as ex:
        return {"success": False, "error": f"启动实盘失败: {str(ex)}"}


@app.post("/api/pool/{name:path}/live/pause", tags=["pool"], dependencies=[Depends(verify_api_key)])
async def live_pause(name: str):
    engine = app.state.engine
    if hasattr(engine, 'pause_loop'):
        engine.pause_loop()
    return {"success": True}


@app.post("/api/pool/{name:path}/live/resume", tags=["pool"], dependencies=[Depends(verify_api_key)])
async def live_resume(name: str):
    engine = app.state.engine
    if hasattr(engine, 'resume_loop'):
        engine.resume_loop()
    return {"success": True}


@app.post("/api/pool/{name:path}/live/stop", tags=["pool"], dependencies=[Depends(verify_api_key)])
async def live_stop(name: str):
    engine = app.state.engine
    if hasattr(engine, 'stop_loop'):
        await engine.stop_loop()
    return {"success": True}


@app.get("/api/pool/{name:path}/rankings", tags=["pool"])
async def get_pool_rankings(name: str):
    """返回 PK 排名（_pk_rankings）。"""
    engine = app.state.engine
    return {"success": True, "data": getattr(engine, '_pk_rankings', {})}


@app.get("/api/pool/{name:path}/dashboard", tags=["pool"])
async def get_pool_dashboard(name: str):
    """返回看盘面板数据（_dashboard_data）。"""
    engine = app.state.engine
    return {"success": True, "data": getattr(engine, '_dashboard_data', {})}


@app.get("/api/pool/{name:path}/alerts", tags=["pool"])
async def get_pool_alerts(name: str):
    """返回告警事件（_alert_events）。"""
    engine = app.state.engine
    return {"success": True, "data": getattr(engine, '_alert_events', [])}


_REGISTRY_FILES = {"cell-types": "cell_type_registry.json", "modules": "modules.json", "dzh-type-map": "dzh_type_map.json",
                   "defaults": "defaults.json", "flow-modes": "flow_mode_registry.json", "edge-strategies": "edge_strategies.json",
                   "column-definitions": "column_definitions.json", "theme-config": "theme_config.json",
                   "toolbar": "toolbar_config.json", "context-menu": "context_menu_config.json",
                   "keyboard-shortcuts": "keyboard_shortcuts.json", "data-providers": "data_providers.json",
                   "field-definitions": "field_definitions.json"}
_FILE_DIRS = {"tdxpool": "tdxpool", "dzhpool": "dzhpool", "examples": "examples"}

@app.get("/api/registry/{reg_name}", tags=["registry"])
async def registry_generic(reg_name: str):
    fname = _REGISTRY_FILES.get(reg_name)
    if not fname: raise _HTTPException(status_code=404, detail=f"未知注册表: {reg_name}")
    p = _CONFIG / fname
    if p.exists():
        with open(p, "r", encoding="utf-8") as f: return {"code": 0, "data": json.load(f)}
    return {"code": 0, "data": {}}

@app.get("/api/registry/cache-version", tags=["registry"])
async def registry_cache_version(): return {"code": 0, "data": {"version": 1}}


# ══════════════════════════════════════════════════════════════════════
#  HighlightManager 公开配置端点
# ══════════════════════════════════════════════════════════════════════

_DEFAULT_HIGHLIGHT_RULES = {
    "rules": {
        "cell": {
            "default": {
                "duration_ms": {
                    "runtime": 2000,
                    "replay": 4000
                }
            }
        },
        "flow": {
            "default": {
                "duration_ms": {
                    "runtime": 1500
                }
            }
        }
    },
    "polling_interval_ms": 500
}

_DEFAULT_TABLES_DEFAULTS = {
    "highlight": {
        "ws": {
            "scheme": "ws",
            "path": "/ws/highlight"
        }
    }
}


@app.get("/api/config/tables/highlight_rules", tags=["highlight"])
async def get_highlight_rules():
    """返回高亮规则配置。"""
    _cs = app.state.config_store if hasattr(app, "state") and hasattr(app.state, "config_store") else None
    if _cs is not None:
        _t = _cs.get_table("highlight_rules")
        return _t if _t else _DEFAULT_HIGHLIGHT_RULES
    return _DEFAULT_HIGHLIGHT_RULES


@app.get("/api/config/tables/defaults", tags=["highlight"])
async def get_tables_defaults():
    """返回表格默认配置（含高亮 WebSocket 路径）。"""
    _cs = app.state.config_store if hasattr(app, "state") and hasattr(app.state, "config_store") else None
    if _cs is not None:
        _t = _cs.get_table("defaults")
        return _t if _t else _DEFAULT_TABLES_DEFAULTS
    return _DEFAULT_TABLES_DEFAULTS


@app.post("/api/dzh/tdx/import", tags=["tdx"], dependencies=[Depends(verify_api_key)])
async def tdx_import_file(request: _Request):
    try:
        content, filename = None, "upload.xml"
        form = await request.form(); uf = form.get("file")
        if uf and hasattr(uf, "filename") and uf.filename:
            content, filename = await uf.read(), uf.filename
        else:
            try:
                body = await request.json()
                if body and isinstance(body, dict) and isinstance(body.get("xml_content", ""), str) and body["xml_content"].strip():
                    content, filename = body["xml_content"].encode("utf-8"), body.get("filename", "upload.xml")
            except Exception: pass
        if content is None: return {"success": False, "error": "请上传文件或提供 xml_content"}
        pool = _call_converter(content, "tdx", "import")
        return {"success": True, "data": _tdx_pool_to_frontend(pool, filename.rsplit(".", 1)[0] if "." in filename else filename)}
    except Exception as ex: return {"success": False, "error": f"TDX导入失败: {str(ex)}"}

@app.get("/api/files/{dir_name}", tags=["files"])
async def list_dir_files(dir_name: str):
    subdir = _FILE_DIRS.get(dir_name)
    if not subdir: raise _HTTPException(status_code=404, detail=f"未知目录: {dir_name}")
    try:
        d = os.path.join(os.path.dirname(__file__), subdir)
        if not os.path.isdir(d): return {"success": True, "data": []}
        return {"success": True, "data": [{"name": f, "ext": f.rsplit('.', 1)[-1].lower() if '.' in f else '', "size": os.path.getsize(os.path.join(d, f))} for f in sorted(os.listdir(d)) if os.path.isfile(os.path.join(d, f))]}
    except _HTTPException: raise
    except Exception as ex: return {"success": False, "error": str(ex)}

@app.get("/api/files/dzhpool/{filename:path}/load", tags=["files"])
async def load_dzhpool_file(filename: str):
    # 路径遍历防护：确保解析后的路径在 dzhpool 目录内
    base_dir = os.path.join(os.path.dirname(__file__), 'dzhpool')
    resolved = os.path.realpath(os.path.join(base_dir, filename))
    if not resolved.startswith(os.path.realpath(base_dir)):
        raise _HTTPException(status_code=400, detail="Invalid filename")
    try:
        xml_path = resolved
        if not os.path.isfile(xml_path): return {"success": False, "error": "文件不存在"}
        pool = _call_converter(xml_path, "dzh", "import", name=filename)
        return {"success": True, "data": pool, "name": filename.rsplit(".", 1)[0] if "." in filename else filename}
    except _HTTPException: raise
    except Exception as ex: return {"success": False, "error": f"DZH加载失败: {str(ex)}"}

@app.get("/api/files/examples/{filename:path}/load", tags=["files"])
async def load_example_file(filename: str):
    # 路径遍历防护：确保解析后的路径在 examples 目录内
    base_dir = os.path.join(os.path.dirname(__file__), 'examples')
    resolved = os.path.realpath(os.path.join(base_dir, filename))
    if not resolved.startswith(os.path.realpath(base_dir)):
        raise _HTTPException(status_code=400, detail="Invalid filename")
    try:
        fpath = resolved
        if not os.path.isfile(fpath): return {"success": False, "error": "文件不存在"}
        with open(fpath, 'r', encoding='utf-8') as f: raw = json.load(f)
        # 如果是 JSON 股票池格式（有 version 字段），需要经过 import_pool_from_json 转换
        if isinstance(raw, dict) and "version" in raw:
            from converters import import_pool_from_json
            data = import_pool_from_json(json_content=json.dumps(raw, ensure_ascii=False))
        else:
            data = raw
        return {"success": True, "data": data}
    except _HTTPException: raise
    except Exception as ex: return {"success": False, "error": f"加载失败: {str(ex)}"}

@app.get("/api/tdx/history/{pool_name}/{node_id}/dates", tags=["tdx-history"])
async def list_history_dates(pool_name: str, node_id: str):
    try:
        base = _TDXPOOL_DIR / pool_name / node_id
        if not base.exists(): return {"success": True, "dates": []}
        return {"success": True, "dates": sorted({f.stem for f in base.iterdir() if f.stem.isdigit() and len(f.stem) == 8}, reverse=True)}
    except Exception as ex: return {"success": False, "error": str(ex)}

@app.get("/api/tdx/history/{pool_name}/{node_id}/log", tags=["tdx-history"])
async def get_full_entry_log(pool_name: str, node_id: str):
    try:
        base = _TDXPOOL_DIR / pool_name / node_id
        if not base.exists(): return {"success": True, "log": []}
        date_files = {}
        for f in base.iterdir():
            if f.stem.isdigit() and len(f.stem) == 8 and (f.stem not in date_files or f.suffix.lower() == '.dat'): date_files[f.stem] = f
        log = []
        for ds in sorted(date_files, reverse=True):
            stocks = _read_history_log(pool_name, node_id, ds)
            log.append({"date": ds, "date_fmt": f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}", "count": len(stocks), "stocks": stocks})
        return {"success": True, "node_id": node_id, "total_dates": len(log), "total_entries": sum(e["count"] for e in log), "log": log}
    except Exception as ex: return {"success": False, "error": str(ex)}

@app.get("/api/tdx/history/{pool_name}/{node_id}/{date_str}", tags=["tdx-history"])
async def get_history_data(pool_name: str, node_id: str, date_str: str):
    try:
        stocks = _read_history_log(pool_name, node_id, date_str)
        return {"success": True, "date": date_str, "count": len(stocks), "stocks": stocks}
    except Exception as ex: return {"success": False, "error": str(ex)}

@app.get("/api/tdx/history/{pool_name}/{node_id}/{date_str}/export", tags=["tdx-history"])
async def export_history_data(pool_name: str, node_id: str, date_str: str):
    from fastapi.responses import Response
    try:
        stocks = _read_history_log(pool_name, node_id, date_str)
        lines = ["市场|代码|名称|进入日期|进入时间|进入价|最高收益率|最高周期|最高日期|最高价格"]
        lines += [f"{s['market']}|{s['code']}|{s['name']}|{s['indate']}|{s['intime']}|{s['inprice']}|{s['maxrate']}%|{s['maxperiod']}|{s['maxdate']}|{s['maxprice']}" for s in stocks]
        fn = f"{pool_name}_{node_id}_{date_str}_status_his.txt"
        return Response(content="\n".join(lines), media_type="text/plain; charset=utf-8", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{_quote_filename(fn)}"})
    except Exception as ex: return Response(content=f"导出失败: {ex}", status_code=500, media_type="text/plain")

class SPAMiddleware:
    def __init__(self, app: ASGIApp): self.app = app
    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or scope.get("path", "").startswith(("/api/", "/ws/")): await self.app(scope, receive, send); return
        rel = scope["path"].lstrip("/")
        for pfx in ("web/", "static/", "tdxpool/", "dzhpool/"):
            if rel.startswith(pfx):
                rel = rel[len(pfx):];
                break
        # tdxpool/dzhpool 在 _BASE 下（与 web/ 同级），不在 web_dir 下
        if scope["path"].startswith(("/tdxpool/", "/dzhpool/")):
            fp = _BASE / Path(scope["path"].lstrip("/"))
            # 路径遍历防护：确保解析后的路径在 _BASE 下
            resolved = fp.resolve()
            if not str(resolved).startswith(str(_BASE.resolve())):
                await self.app(scope, receive, send); return
        else:
            fp = web_dir / rel
            # 路径遍历防护：确保解析后的路径在 web_dir 下
            resolved = fp.resolve()
            if not str(resolved).startswith(str(web_dir.resolve())):
                await self.app(scope, receive, send); return
        if scope["path"] != "/" and fp.exists() and fp.is_file(): await FileResponse(str(fp))(scope, receive, send); return
        await FileResponse(str(web_dir / "index.html"))(scope, receive, send)

# [重复端点说明] /api/pk_rankings 与 /api/pool/{name}/rankings 功能重复
# 主端点: /api/pool/{name}/rankings（支持按池名查询）
@app.get("/api/pk_rankings")
async def get_pk_rankings(request: _Request): return {"success": True, "data": request.app.state.engine._pk_rankings}

# [重复端点说明] /api/dashboard 与 /api/pool/{name}/dashboard 功能重复
# 主端点: /api/pool/{name}/dashboard（支持按池名查询）
@app.get("/api/dashboard")
async def get_dashboard(request: _Request): return {"success": True, "data": request.app.state.engine._dashboard_data}

# [重复端点说明] /api/alerts 与 /api/pool/{name}/alerts 功能重复
# 主端点: /api/pool/{name}/alerts（支持按池名查询）
@app.get("/api/alerts")
async def get_alerts(request: _Request):
    """返回告警事件（_alert_queue）。"""
    engine = request.app.state.engine
    alerts = []
    q = getattr(engine, '_alert_queue', None)
    if q is None:
        return {"success": True, "data": []}
    while not q.empty() and len(alerts) < 100:
        try:
            alerts.append(q.get_nowait())
        except Exception:
            break
    return {"success": True, "data": alerts}

# ─── 回放模式端点 ────────────────────────────────────────────
# [重复端点说明] 以下 /api/pool/{name}/replay/* 端点与 api.py（合并自原 replay_api + dzh_api）中的端点功能重复：
#   - /api/pool/{name}/replay/start  ≈ /api/replay/start (api.py 中合并自 replay_api, 主端点) ≈ /api/dzh/replay/start (api.py 中合并自 dzh_api)
#   - /api/pool/{name}/replay/step   ≈ /api/replay/control?action=next (api.py, 主端点) ≈ /api/dzh/replay/step (api.py)
#   - /api/pool/{name}/replay/state  ≈ /api/replay/state (api.py, 主端点) ≈ /api/dzh/replay/snapshot (api.py)
#   - /api/pool/{name}/replay/stop   ≈ /api/replay/control?action=stop (api.py, 主端点) ≈ /api/dzh/replay/control (api.py)
# 主端点为 api.py 中合并自 replay_api 的实现（基于 session_id 的会话式回放，功能最完整）
# 此处保留以兼容旧前端调用，新代码应使用 /api/replay/* 端点

@app.get("/api/pool/{name}/node_stocks")
async def get_pool_node_stocks(name: str, request: _Request):
    """获取指定池的当前 node_stocks 状态（优先回放/仿真状态，fallback 到引擎）"""
    try:
        engine = request.app.state.engine
        # 优先检查活跃回放会话
        replays = getattr(request.app.state, "_replay_engines", {})
        if name in replays:
            re = replays[name]
            ns = re._mode_state.get("node_stocks", {}) if re._mode_state else {}
            return {"success": True, "mode": "replay", "node_stocks": {nid: [_stock_code(s) for s in ss] for nid, ss in ns.items()}}
        # 优先检查活跃仿真会话（按池名直接查找缓存）
        sims = getattr(request.app.state, "_simulators", {})
        sim = sims.get(name)
        if sim is not None:
            ns = sim._mode_state.get("node_stocks", {}) if sim._mode_state else {}
            return {"success": True, "mode": "simulation", "session_id": name, "node_stocks": {nid: [_stock_code(s) for s in ss] for nid, ss in ns.items()}}
        # fallback 到引擎 state.node_stocks（I28：_loop_node_stocks 死属性已删，改用活路径）
        st = getattr(engine, "state", None)
        ns = st.node_stocks if st is not None else {}
        if ns:
            return {"success": True, "mode": "live", "node_stocks": {nid: [_stock_code(s) for s in ss] for nid, ss in ns.items()}}
        return {"success": True, "mode": "none", "node_stocks": {}}
    except Exception as ex:
        return {"success": False, "error": str(ex)}

@app.get("/api/kline", tags=["kline"])
async def get_kline(stock_code: str = "", period: str = "5m", limit: int = 300, request: _Request = None):
    """获取K线数据：优先事件驱动合成的 bars_history，其次本地历史，最后 mock 兜底。"""
    try:
        period_norm = _normalize_period(period)
        bars = await _get_kline_bars(request, stock_code, period_norm, limit)
        return {
            "code": 0,
            "data": bars,
            "stock_code": stock_code,
            "period": period_norm,
            "count": len(bars),
        }
    except Exception as ex:
        logger.warning("获取 K 线失败: %s", ex)
        return {"code": 1, "msg": str(ex), "data": []}


@app.get("/api/indicator/values", tags=["indicator"])
async def get_indicator_values(request: _Request, node_id: str = Query(...), formula: str = Query(""), period: str = Query("1d")):
    """指标面板：根据 node_id（股票代码）与公式返回时序值。公式为空时返回收盘价序列。"""
    try:
        code = node_id
        if not code:
            return {"code": 1, "msg": "缺少 node_id", "data": []}

        period_norm = _normalize_period(period)
        bars = await _get_kline_bars(request, code, period_norm)
        if not bars:
            return {"code": 0, "data": [], "stock_code": code, "period": period_norm}

        if not formula:
            data = [{"time": b.get("datetime", ""), "value": b.get("close")} for b in bars]
            return {"code": 0, "data": data, "stock_code": code, "period": period_norm}

        formula_module = getattr(request.app.state, "formula", None)
        if formula_module is None:
            return {"code": 1, "msg": "Formula engine not initialized", "data": []}

        import pandas as pd
        df = pd.DataFrame(bars)
        series_result = await formula_module.eval_indicator_series(formula, df)
        if series_result is None or not series_result:
            return {"code": 1, "msg": "公式求值失败", "data": []}
        series = next(iter(series_result.values()))
        data = [
            {"time": b.get("datetime", ""), "value": series[i] if i < len(series) else None}
            for i, b in enumerate(bars)
        ]
        return {"code": 0, "data": data, "stock_code": code, "period": period_norm}
    except Exception as ex:
        logger.warning("指标求值失败: %s", ex)
        return {"code": 1, "msg": str(ex), "data": []}


@app.get("/api/pool/{name:path}/event-panel", tags=["pool"])
async def get_event_panel(name: str, limit: int = 100, request: _Request = None):
    """获取事件面板数据（仿真模式读sim.event_log，live模式读EventPanel），已按前端展示要求格式化。"""
    try:
        engine = request.app.state.engine
        sims = getattr(request.app.state, "_simulators", {})
        sim = sims.get(name)
        if sim is not None:
            event_log = getattr(sim, "event_log", None)
            if event_log is not None and len(event_log) > 0:
                events = event_log[-limit:]
                return {"success": True, "pool": name, "count": len(events), "events": [format_event(ev) for ev in events]}
            pe = getattr(sim, "_pool_engine", None)
            if pe is None:
                inner = getattr(sim, "_engine", None)
                pe = getattr(inner, "_pool_engine", None) if inner else None
            ep = getattr(pe, "event_panel", None) if pe else None
            if ep is not None:
                events = ep.get_events()[-limit:]
                return {"success": True, "pool": name, "count": len(events), "events": [format_event(_event_to_dict(ev)) for ev in events]}
            return {"success": True, "events": []}
        ep = getattr(engine, "event_panel", None)
        if ep is None:
            return {"success": True, "events": []}
        events = ep.get_events()[-limit:]
        return {"success": True, "pool": name, "count": len(events), "events": [format_event(_event_to_dict(ev)) for ev in events]}
    except Exception as ex:
        return {"success": False, "error": str(ex)}

@app.post("/api/pool/{name}/replay/start", dependencies=[Depends(verify_api_key)])
async def replay_start(name: str, request: _Request):
    """启动回放模式：加载 K 线数据并初始化回放引擎"""
    # 路径遍历防护：确保解析后的路径在 tdxpool 目录内
    base_dir = os.path.join(os.path.dirname(__file__), 'tdxpool')
    resolved = os.path.realpath(os.path.join(base_dir, name + '.xml'))
    if not resolved.startswith(os.path.realpath(base_dir)):
        raise _HTTPException(status_code=400, detail="Invalid pool name")
    try:
        body = await request.json()
        xml_path = resolved
        if not os.path.isfile(xml_path):
            raise _HTTPException(status_code=404, detail=f"池文件未找到: {name}.xml")
        cfg = _load_tdx_pool_config(xml_path) or {}
        if not cfg.get('nodes'):
            return {"success": False, "error": "池配置为空，无节点"}
        base_period = body.get("base_period", "day")
        date_range = body.get("date_range", ["2024-01-01", "2024-03-01"])
        use_mock = body.get("mock", True)  # 默认mock，避免网络阻塞
        engine = request.app.state.engine
        # 保存原始值，以便 replay_stop 时恢复（修复 Bug #11 和 #12）
        if not hasattr(request.app.state, "_replay_saved_state"):
            request.app.state._replay_saved_state = {}
        saved = request.app.state._replay_saved_state
        # 如请求mock，临时切换adapter到mock模式
        if use_mock and hasattr(engine.tq_adapter, '_manager'):
            if "_default_chain" not in saved:
                saved["_default_chain"] = list(engine.tq_adapter._manager._default_chain)
            engine.tq_adapter._manager._default_chain = ["mock"]
        if not hasattr(request.app.state, "_replay_engines"):
            request.app.state._replay_engines = {}
        # 确保 pool_id 在持久表中存在，避免外键约束失败
        storage = request.app.state.storage
        if not storage.get_pool(name):
            storage.save_pool(name, {"name": name, "pool_type": "tdx", "status": "active"})
        # 确保节点存在于 pool_node 表，避免 stock_transfer_log 外键约束失败
        for n in cfg.get('nodes', []):
            nid = n.get('id', '')
            if nid:
                storage.save_pool_node(nid, name, n.get('type', ''), n.get('label', ''))
        # 回放模式下，避免直接注入状态池，让股票通过边流转产生 transfer_events
        orig_injection = engine._data_config.get("injection_rules", {}).get("bar_data", {}).get("source_node_types", [])
        if "injection_rules_source_node_types" not in saved:
            saved["injection_rules_source_node_types"] = list(orig_injection)
        engine._data_config.setdefault("injection_rules", {}).setdefault("bar_data", {})["source_node_types"] = [t for t in orig_injection if t not in ("tdx_state_pool", "stock_state_pool")]
        re = KLineReplayEngine(engine, storage=storage, bus=app.state.bus)
        # 设置入池回调，使回放也能触发 history / stock_transfer_log 记录
        nm = {n.get('id', ''): n for n in cfg.get('nodes', [])}
        def _on_enter(nid, info, stocks):
            _dispatch_pool_enter_actions(name, nid, nm.get(nid, info) or {}, stocks)
        engine._on_stock_enter_target_pool = _on_enter
        result = re.load_kline_data(cfg, base_period, date_range, pool_id=name)
        if not result.get("success"):
            return {"success": False, "error": result.get("error", "K线加载失败")}
        request.app.state._replay_engines[name] = re
        return {"success": True, "data": result}
    except _HTTPException:
        raise
    except Exception as ex:
        return {"success": False, "error": f"回放启动失败: {str(ex)}"}

@app.post("/api/pool/{name}/replay/step", dependencies=[Depends(verify_api_key)])
async def replay_step(name: str, request: _Request):
    """回放单步推进"""
    try:
        replays = getattr(request.app.state, "_replay_engines", {})
        re = replays.get(name)
        if not re:
            return {"success": False, "error": "回放会话不存在，请先调用 /replay/start"}
        result = re.step()
        return {"success": True, "data": result}
    except Exception as ex:
        return {"success": False, "error": str(ex)}

@app.get("/api/pool/{name}/replay/state")
async def replay_state(name: str, request: _Request):
    """获取回放当前状态快照"""
    try:
        replays = getattr(request.app.state, "_replay_engines", {})
        re = replays.get(name)
        if not re:
            return {"success": False, "error": "回放会话不存在"}
        return {"success": True, "data": re.get_current_snapshot()}
    except Exception as ex:
        return {"success": False, "error": str(ex)}

@app.post("/api/pool/{name}/replay/stop", dependencies=[Depends(verify_api_key)])
async def replay_stop(name: str, request: _Request):
    """停止回放并清理会话"""
    try:
        replays = getattr(request.app.state, "_replay_engines", {})
        re = replays.pop(name, None)
        if re:
            re.stop()
        # 恢复 replay_start 中保存的全局状态（修复 Bug #11 和 #12）
        engine = request.app.state.engine
        saved = getattr(request.app.state, "_replay_saved_state", {})
        if "_default_chain" in saved and hasattr(engine.tq_adapter, '_manager'):
            engine.tq_adapter._manager._default_chain = saved.pop("_default_chain")
        if "injection_rules_source_node_types" in saved:
            engine._data_config.setdefault("injection_rules", {}).setdefault("bar_data", {})["source_node_types"] = saved.pop("injection_rules_source_node_types")
        # 如果所有保存的状态都已恢复，清理属性
        if not saved and hasattr(request.app.state, "_replay_saved_state"):
            del request.app.state._replay_saved_state
        return {"success": True, "data": {"stopped": re is not None}}
    except Exception as ex:
        return {"success": False, "error": str(ex)}


# ─── Task 9: WebSocket 增量推送端点 ───────────────────────────────────────────

@app.websocket("/ws/pool/{name}")
async def pool_websocket(websocket: WebSocket, name: str):
    """股票池运行时增量事件 WebSocket 端点。

    首次连接时发送 ``SnapshotBuilder.snapshot()`` 完整快照，
    后续通过 ``WebSocketPublisher`` 转发 ``Executed`` / ``DomainEvent`` /
    ``Signal`` / ``DataChanged`` 增量事件。
    """
    await websocket.accept()
    engine = app.state.engine
    pool_config = _resolve_pool_config(name)
    if pool_config is None:
        await websocket.close(code=1008, reason="Pool not found")
        return

    engine._ensure_pool_engine(pool_config)

    if engine.ws_publisher is None or getattr(engine.ui_renderer, '_snapshot_builder', None) is not engine.snapshot_builder:
        try:
            from web.ui_renderer import UIRenderer, WebSocketPublisher
            pool_id = pool_config.get("id", "") if pool_config else ""
            engine.ui_renderer = UIRenderer(
                engine.event_bus,
                engine.snapshot_builder,
                pool_id=pool_id,
            )
            engine.ws_publisher = WebSocketPublisher(engine.ui_renderer)
            engine.ui_renderer.attach_publisher(engine.ws_publisher)
        except ImportError:
            await websocket.close(code=1011, reason="Web UI module not available")
            return

    publisher = engine.ws_publisher

    def send_callback(text: str) -> None:
        asyncio.create_task(websocket.send_text(text))

    publisher.add_client(send_callback)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        publisher.remove_client(send_callback)


@app.websocket("/ws/highlight")
async def highlight_websocket(websocket: WebSocket):
    """高亮事件 WebSocket 端点。

    接受 ``subscribe_highlight`` 订阅消息并返回确认，之后保持连接。
    当前无主动推送事件源，静默保持连接即可。
    """
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
            except Exception:
                msg = {"type": data.strip()}
            msg_type = msg.get("type", "")
            if msg_type == "subscribe_highlight":
                await websocket.send_json({
                    "type": "subscribe_highlight_ack",
                    "status": "ok"
                })
            elif msg_type == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


@app.websocket("/api/config/ws")
async def config_ws(websocket: WebSocket):
    """配置同步 WebSocket 端点。

    对应前端 ``ConfigSync`` 客户端（``web/js/app.js``）。
    连接建立后发送一次 ``status`` 消息，之后保持连接并响应 ``ping`` 心跳。
    当前无主动配置变更推送源，静默保持连接以避免前端控制台报错。
    """
    await websocket.accept()
    try:
        await websocket.send_json({"type": "status", "status": "connected"})
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


app.add_middleware(SPAMiddleware)

