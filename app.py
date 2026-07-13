import logging, re, json, os
import asyncio
import xml.etree.ElementTree as ET
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request as _Request, HTTPException as _HTTPException, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import FileResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

try:
    from .core.engine import MetaEngine
except ImportError:
    from engine import MetaEngine

try:
    from .services.storage import Storage, safe_path_join
except ImportError:
    from services.storage import Storage, safe_path_join

try:
    from .services.tq_adapter import TqAdapter
except ImportError:
    from services.tq_adapter import TqAdapter

try:
    from .services.minute_aggregator import Min1Aggregator
except ImportError:
    from services.minute_aggregator import Min1Aggregator

try:
    from .services.data import DataQueryService
except ImportError:
    from services.data import DataQueryService

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
    from .api import config_api_router, config_api_init
except ImportError:
    from api import config_api_router, config_api_init

try:
    from .api import create_formula_router
except ImportError:
    from api import create_formula_router

try:
    from .core.replay import KLineReplayEngine
except ImportError:
    from replay import KLineReplayEngine

try:
    from .converters.json_xml import _build_tdx_xml, _tdx_pool_to_frontend, _load_tdx_pool_config
except ImportError:
    from converters.json_xml import _build_tdx_xml, _tdx_pool_to_frontend, _load_tdx_pool_config

try:
    from .api import _enrich_tdx_node_data
except ImportError:
    from api import _enrich_tdx_node_data

try:
    from .services.trading_service import _read_history_log, _dispatch_pool_enter_actions, _quote_filename
except ImportError:
    from services.trading_service import _read_history_log, _dispatch_pool_enter_actions, _quote_filename

try:
    from .core._market_utils import _stock_code
except ImportError:
    from core._market_utils import _stock_code

_BASE = Path(__file__).parent
_CONFIG = _BASE / "config"
_TDXPOOL_DIR = _BASE / "tdxpool"
web_dir = _BASE / "web"

@asynccontextmanager
async def lifespan(app):
    app.state.engine = MetaEngine()
    app.state.storage = Storage()
    app.state.tq = TqAdapter(mock_mode=False)
    app.state.engine.set_tq_adapter(app.state.tq)
    app.state.engine.set_storage(app.state.storage)
    logging.info("TqAdapter 初始化完成，数据模式: %s", app.state.tq.get_mode_info())
    # 实时分钟线合成器（Task 9: 接线 Min1Aggregator 实时流）
    # symbols 初始为空，由池加载/请求处理器按需更新监控标的集合
    app.state.minute_aggregator = Min1Aggregator(symbols=[])
    app.state.engine.set_minute_aggregator(app.state.minute_aggregator)
    logging.info("Min1Aggregator 初始化完成，监控标的数: %d", app.state.minute_aggregator.n)
    # 数据查询/同步服务（注入 minute_aggregator，供 K 线拼接与实时流复用）
    app.state.data_query_service = DataQueryService(
        storage=app.state.storage,
        minute_aggregator=app.state.minute_aggregator,
        tq_adapter=app.state.tq,
    )
    app.state.data_sync_service = DataSyncService(
        storage=app.state.storage,
        minute_aggregator=app.state.minute_aggregator,
    )
    logging.info("DataQueryService / DataSyncService 初始化完成（已注入 minute_aggregator）")
    set_table_engine(app.state.engine, str(Path(__file__).parent / "config"))
    app.state._simulators = {}  # 仿真会话池：{name: RuntimeSimulator}
    # 初始化热加载管理器
    from .services.hot_reload import HotReloadManager
    config_dir = str(Path(__file__).parent / "config")
    if hasattr(app.state.engine, '_config_store') and app.state.engine._config_store:
        _hrm = HotReloadManager(
            config_dir=config_dir,
            config_store=app.state.engine._config_store,
            storage=app.state.storage,
            on_change=lambda changed: logging.info(f"配置变更: {changed}")
        )
        config_api_init(
            config_store=app.state.engine._config_store,
            hot_reload_manager=_hrm
        )
    else:
        config_api_init(config_store=None)
    yield

# ─── API Key 认证中间件 ───────────────────────────────────────
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def _load_auth_config():
    """从 config/defaults.json 加载认证配置"""
    try:
        import json
        from pathlib import Path
        cfg_path = Path(__file__).parent / "config" / "defaults.json"
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
        cfg_path = Path(__file__).parent / "config" / "defaults.json"
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

app = FastAPI(title="MetaEngine Stock Pool", version="1.0", lifespan=lifespan)
_cors_cfg = _load_cors_config()
_cors_origins = _cors_cfg.get('allowed_origins', ["http://localhost:*", "http://127.0.0.1:*"])
app.add_middleware(CORSMiddleware, allow_origins=_cors_origins, allow_methods=["*"], allow_headers=["*"], allow_credentials=True)
app.include_router(create_meta_router(), prefix="/api/meta", tags=["元数据"], dependencies=[Depends(verify_api_key)])
app.include_router(create_execution_router(), prefix="/api", tags=["执行"], dependencies=[Depends(verify_api_key)])
app.include_router(create_dzh_router(), dependencies=[Depends(verify_api_key)])
app.include_router(create_json_router(), prefix="/api/json", tags=["JSON导入导出"], dependencies=[Depends(verify_api_key)])
app.include_router(create_sim_router(), dependencies=[Depends(verify_api_key)])
app.include_router(create_replay_router(), dependencies=[Depends(verify_api_key)])
app.include_router(table_router, dependencies=[Depends(verify_api_key)])
app.include_router(table_config_router, dependencies=[Depends(verify_api_key)])
app.include_router(config_api_router, dependencies=[Depends(verify_api_key)])
app.include_router(create_formula_router(), prefix="/api/formula", tags=["公式"], dependencies=[Depends(verify_api_key)])

@app.get("/api/tdx/pools", tags=["tdx"])
async def tdx_list_pools():
    d = os.path.join(os.path.dirname(__file__), 'tdxpool')
    if not os.path.isdir(d): return {"success": True, "data": []}
    try:
        return {"success": True, "data": [{"name": f[:-4], "filename": f, "has_screenshot": os.path.isfile(os.path.join(d, f[:-4] + '.png'))} for f in sorted(os.listdir(d)) if f.endswith('.xml')]}
    except Exception as ex: return {"success": False, "error": str(ex)}

@app.get("/api/tdx/pools/{name:path}/load", tags=["tdx"])
async def tdx_load_pool(name: str):
    if ".." in name or "/" in name or "\\" in name:
        raise _HTTPException(status_code=400, detail="Invalid pool name")
    try:
        from .converters.tdx import parse_tdx_xml
        xml_path = os.path.join(os.path.dirname(__file__), 'tdxpool', name + '.xml')
        if not os.path.isfile(xml_path): raise _HTTPException(status_code=404, detail=f"文件未找到: {name}.xml")
        pool = parse_tdx_xml(xml_path)
        return {"success": True, "data": _tdx_pool_to_frontend(pool, name), "stats": {"cells": len(pool.cells), "flows": len(pool.flows), "name": name}}
    except _HTTPException: raise
    except Exception as ex: return {"success": False, "error": f"TDX 加载失败: {str(ex)}"}

@app.post("/api/tdx/export", tags=["tdx"], dependencies=[Depends(verify_api_key)])
async def tdx_export_xml(request: _Request):
    try:
        import tempfile
        from starlette.background import BackgroundTask
        _body = await request.json()
        pool_data = _body.get("pool_data", _body) if isinstance(_body, dict) else {}
        with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp: tmp_path = tmp.name
        _build_tdx_xml(pool_data, tmp_path)
        return FileResponse(tmp_path, media_type="application/xml", filename=pool_data.get("name", "tdx_pool") + ".xml", background=BackgroundTask(os.remove, tmp_path))
    except Exception as ex: return {"success": False, "error": f"TDX导出失败: {str(ex)}"}

@app.post("/api/tdx/pools", tags=["tdx"], dependencies=[Depends(verify_api_key)])
async def tdx_create_pool(request: _Request):
    import uuid
    try:
        body = await request.json(); name = body.get("name", "").strip()
        if not name: return {"success": False, "error": "缺少 name 参数"}
        if ".." in name or "/" in name or "\\" in name:
            raise _HTTPException(status_code=400, detail="Invalid pool name")
        d = os.path.join(os.path.dirname(__file__), 'tdxpool'); os.makedirs(d, exist_ok=True)
        xml_path = os.path.join(d, f"{name}.xml")
        if os.path.exists(xml_path): return {"success": False, "error": f"股票池 {name} 已存在"}
        bc = body.get("backcolor", 1114112)
        with open(xml_path, 'w', encoding='gbk') as f:
            f.write(f'<?xml version="1.0" encoding="GBK"?>\n<root>\n<pool nextid="1" backcolor="{bc}">\n<cells>\n</cells>\n<flows>\n</flows>\n</pool>\n</root>')
        pool_id = "pool_" + uuid.uuid4().hex[:12]
        request.app.state.storage.save_pool(pool_id, {"name": name, "pool_type": "tdx", "description": f"TDX pool: {name}", "topology_mode": "flow", "status": "draft"})
        return {"success": True, "data": {"name": name, "filename": f"{name}.xml", "backcolor": bc, "pool_id": pool_id}}
    except _HTTPException: raise
    except Exception as ex: return {"success": False, "error": f"TDX 创建失败: {str(ex)}"}

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

@app.delete("/api/tdx/pools/{name:path}", tags=["tdx"], dependencies=[Depends(verify_api_key)])
async def tdx_delete_pool(name: str):
    try:
        d = os.path.join(os.path.dirname(__file__), 'tdxpool')
        try:
            xml_path = safe_path_join(d, name + '.xml')
        except ValueError as ex:
            raise _HTTPException(status_code=400, detail=str(ex))
        if not os.path.isfile(xml_path): raise _HTTPException(status_code=404, detail=f"文件未找到: {name}.xml")
        os.remove(xml_path)
        png = os.path.join(d, name + '.png')
        if os.path.isfile(png): os.remove(png)
        return {"success": True}
    except _HTTPException: raise
    except Exception as ex: return {"success": False, "error": str(ex)}

@app.put("/api/tdx/pools/{name:path}", tags=["tdx"], dependencies=[Depends(verify_api_key)])
async def tdx_save_pool(name: str, request: _Request):
    try:
        body = await request.json()
        pool_data = body.get("pool_data", body)
        if not pool_data or not isinstance(pool_data, dict): return {"success": False, "error": "无效的 pool_data"}
        d = os.path.join(os.path.dirname(__file__), 'tdxpool'); os.makedirs(d, exist_ok=True)
        try:
            _xml_path = safe_path_join(d, f"{name}.xml")
        except ValueError as ex:
            return {"success": False, "error": str(ex)}
        _build_tdx_xml(pool_data, _xml_path)
        storage = request.app.state.storage
        pm = pool_data.get("pool_meta", {})
        pool_id = pool_data.get("pool_id") or pool_data.get("id") or f"tdx_{name}"
        if not storage.get_pool(pool_id):
            for p in storage.list_pools():
                if p.get("name") == name and p.get("pool_type") == "tdx": pool_id = p.get("pool_id", pool_id); break
        storage.save_pool(pool_id, {"name": pool_data.get("name", name), "pool_type": "tdx" if pm.get("type") == "tdx" else "dzh",
                                     "description": pool_data.get("description", ""), "xml_source": _xml_path,
                                     "topology_mode": "flow", "status": "active",
                                     "nodes": pool_data.get("nodes", []), "edges": pool_data.get("edges", []),
                                     "pool_meta": pm})
        return {"success": True, "data": {"name": name, "filename": f"{name}.xml"}}
    except Exception as ex: return {"success": False, "error": f"TDX 保存失败: {str(ex)}"}


# ══════════════════════════════════════════════════════════════════════
#  数据源状态端点（H2 修复：暴露状态 + 显式切换）
# ══════════════════════════════════════════════════════════════════════

# [重复端点说明] /api/data_source/status 与 /api/meta/datasource/list (meta_api.py) 功能重叠
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


# [重复端点说明] /api/data_source/select/{name} 与 /api/meta/datasource/switch (meta_api.py) 功能重叠
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
        replay_engine = KLineReplayEngine(engine, storage=app.state.storage)
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
        from .core.simulator import RuntimeSimulator
        simulator = RuntimeSimulator(pool_config, engine=engine)
        simulator.initialize()
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
        events = simulator.step(d=delta)
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


@app.post("/api/pool/{name:path}/sim/pause", tags=["pool"], dependencies=[Depends(verify_api_key)])
async def sim_pause(name: str):
    """暂停仿真会话。"""
    simulators = getattr(app.state, "_simulators", {})
    simulator = simulators.get(name)
    if simulator is None:
        return {"success": False, "error": f"仿真会话不存在: {name}（请先调用 /sim/init）"}
    simulator.pause()
    return {"success": True, "data": {"pool": name, "paused": True}}


@app.post("/api/pool/{name:path}/sim/resume", tags=["pool"], dependencies=[Depends(verify_api_key)])
async def sim_resume(name: str):
    """恢复仿真会话。"""
    simulators = getattr(app.state, "_simulators", {})
    simulator = simulators.get(name)
    if simulator is None:
        return {"success": False, "error": f"仿真会话不存在: {name}"}
    simulator.resume()
    return {"success": True, "data": {"pool": name, "paused": False}}


@app.post("/api/pool/{name:path}/sim/stop", tags=["pool"], dependencies=[Depends(verify_api_key)])
async def sim_stop(name: str):
    """停止仿真会话并清理实例。"""
    simulators = getattr(app.state, "_simulators", {})
    simulator = simulators.pop(name, None)
    if simulator is not None:
        try:
            simulator.reset()
        except Exception:
            pass
    return {"success": True, "data": {"pool": name, "stopped": simulator is not None}}


@app.get("/api/pool/{name:path}/sim/state", tags=["pool"])
async def sim_get_state(name: str):
    """获取仿真会话状态快照。"""
    simulators = getattr(app.state, "_simulators", {})
    simulator = simulators.get(name)
    if simulator is None:
        return {"success": False, "error": f"仿真会话不存在: {name}"}
    try:
        snapshot = simulator.get_state_snapshot()
        return {"success": True, "data": snapshot}
    except Exception as ex:
        return {"success": False, "error": str(ex)}


@app.post("/api/pool/{name:path}/sim/start", tags=["pool"], dependencies=[Depends(verify_api_key)])
async def sim_start(name: str, request: _Request):
    """仿真模式别名端点：同 /api/pool/{name}/simulation/step。"""
    try:
        body = await request.json()
        delta = float(body.get("delta", 60.0))
    except Exception:
        delta = 60.0
    return await _run_simulation_step(name, delta)


@app.get("/api/pool/{name:path}/pk/ranking", tags=["pool"])
async def get_pool_pk_ranking(name: str):
    """PK 排名别名端点：同 /api/pool/{name}/rankings。"""
    return await get_pool_rankings(name)


def _resolve_pool_config(name: str) -> dict | None:
    """按名称解析股票池配置：先 tdxpool/{name}.xml，再 SQLite（pool_id 或 name）。"""
    base_dir = os.path.join(os.path.dirname(__file__), 'tdxpool')
    xml_path = None
    try:
        xml_path = safe_path_join(base_dir, name + '.xml')
    except ValueError:
        pass
    if xml_path and os.path.isfile(xml_path):
        from .converters.tdx import parse_tdx_xml
        pool = parse_tdx_xml(xml_path)
        return _tdx_pool_to_frontend(pool, name) if hasattr(pool, 'cells') else pool

    storage = app.state.storage
    pool = storage.get_pool(name)
    if pool is None or not isinstance(pool, dict):
        # 兼容通过 name 查找 SQLite 池
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
        _cfg_path = os.path.join(os.path.dirname(__file__), 'config', 'data_providers.json')
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

@app.post("/api/dzh/tdx/import", tags=["tdx"], dependencies=[Depends(verify_api_key)])
async def tdx_import_file(request: _Request):
    try:
        from .converters.tdx import parse_tdx_xml
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
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp: tmp.write(content); tmp_path = tmp.name
        try:
            pool = parse_tdx_xml(tmp_path)
            return {"success": True, "data": _tdx_pool_to_frontend(pool, filename.rsplit(".", 1)[0] if "." in filename else filename)}
        finally: os.unlink(tmp_path)
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
        from .converters.dzh import parse_dzh_xml
        xml_path = resolved
        if not os.path.isfile(xml_path): return {"success": False, "error": "文件不存在"}
        with open(xml_path, 'rb') as f: pool = parse_dzh_xml(f.read(), filename)
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
            from .converters.json_xml import import_pool_from_json
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
# [重复端点说明] 以下 /api/pool/{name}/replay/* 端点与 api/replay_api.py 和 api/dzh_api.py 中的端点功能重复：
#   - /api/pool/{name}/replay/start  ≈ /api/replay/start (replay_api.py, 主端点) ≈ /api/dzh/replay/start (dzh_api.py)
#   - /api/pool/{name}/replay/step   ≈ /api/replay/control?action=next (replay_api.py, 主端点) ≈ /api/dzh/replay/step (dzh_api.py)
#   - /api/pool/{name}/replay/state  ≈ /api/replay/state (replay_api.py, 主端点) ≈ /api/dzh/replay/snapshot (dzh_api.py)
#   - /api/pool/{name}/replay/stop   ≈ /api/replay/control?action=stop (replay_api.py, 主端点) ≈ /api/dzh/replay/control (dzh_api.py)
# 主端点为 api/replay_api.py（基于 session_id 的会话式回放，功能最完整）
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
    """获取K线数据（仿真模式从bars_history读取，live模式从DataQuery读取）"""
    try:
        engine = request.app.state.engine
        sims = getattr(request.app.state, "_simulators", {})
        bars = None
        for _name, sim in sims.items():
            pe = getattr(sim, "_pool_engine", None)
            if pe is None:
                inner = getattr(sim, "_engine", None)
                pe = getattr(inner, "_pool_engine", None) if inner else None
            if pe is None:
                continue
            st = getattr(pe, "state", None)
            if st is None:
                continue
            bh = getattr(st, "bars_history", None)
            if bh is None:
                continue
            period_data = bh.get(period) if isinstance(bh, dict) else None
            if period_data is None:
                continue
            code_list = period_data.get(stock_code) if isinstance(period_data, dict) else None
            if code_list is not None and len(code_list) > 0:
                import pandas as pd
                bars = pd.DataFrame(code_list[-limit:])
                break
        if bars is None:
            st = getattr(engine, "state", None)
            bh = getattr(st, "bars_history", None) if st else None
            if bh and isinstance(bh, dict):
                period_data = bh.get(period)
                if isinstance(period_data, dict):
                    code_list = period_data.get(stock_code)
                    if code_list is not None and len(code_list) > 0:
                        import pandas as pd
                        bars = pd.DataFrame(code_list[-limit:])
        if bars is None or len(bars) == 0:
            return {"success": True, "stock_code": stock_code, "period": period, "bars": []}
        import pandas as pd
        col_map = {"datetime": "datetime", "open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume"}
        result = []
        for _, row in bars.iterrows():
            r = {}
            for target_col, src_col in col_map.items():
                if src_col in row.index:
                    v = row[src_col]
                    r[target_col] = None if pd.isna(v) else (float(v) if isinstance(v, (int, float)) else str(v))
            result.append(r)
        return {"success": True, "stock_code": stock_code, "period": period, "count": len(result), "bars": result}
    except Exception as ex:
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(ex)}

@app.get("/api/pool/{name:path}/event-panel", tags=["pool"])
async def get_event_panel(name: str, limit: int = 100, request: _Request = None):
    """获取事件面板数据（仿真模式读sim.event_log，live模式读EventPanel）"""
    try:
        engine = request.app.state.engine
        sims = getattr(request.app.state, "_simulators", {})
        sim = sims.get(name)
        if sim is not None:
            event_log = getattr(sim, "event_log", None)
            if event_log is not None and len(event_log) > 0:
                events = event_log[-limit:]
                return {"success": True, "pool": name, "count": len(events), "events": events}
            pe = getattr(sim, "_pool_engine", None)
            if pe is None:
                inner = getattr(sim, "_engine", None)
                pe = getattr(inner, "_pool_engine", None) if inner else None
            ep = getattr(pe, "_event_panel", None) if pe else None
            if ep is not None:
                events = ep.get_events()[-limit:]
                return {"success": True, "pool": name, "count": len(events), "events": events}
            return {"success": True, "events": []}
        ep = getattr(engine, "_event_panel", None)
        if ep is None:
            return {"success": True, "events": []}
        events = ep.get_events()[-limit:]
        return {"success": True, "pool": name, "count": len(events), "events": events}
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
        re = KLineReplayEngine(engine, storage=storage)
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


app.add_middleware(SPAMiddleware)

