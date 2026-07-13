"""api/system_api.py - 系统 API 统一入口（合并自 run_api / formula_api / import_api）。

合并保留三个分节：
    # === Run ===     — 运行时执行 API（原 run_api.py：execution / replay / sim）
    # === Formula === — 公式管理 API（原 formula_api.py）
    # === Import ===  — 导入导出 API（原 import_api.py：dzh / json）

向后兼容：api/__init__.py 通过 sys.modules 注册旧模块名，旧 import 路径继续可用。
"""

import base64
import json
import logging
import os
import random
import re
import struct
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Request, HTTPException, UploadFile, File, Form, Query, Body
from fastapi.responses import Response
from pydantic import BaseModel, Field

# === Run ===
try:
    from ..services.tq_adapter import TqAdapter
    from ..services.data import (
        DataSourceContract,
        DataSourceContractError,
        DataSourceUnavailableErrorContract,
        DataSourceMockExplicitOnlyError,
        get_default_contract,
    )
    from ..core.replay import KLineReplayEngine
    from ..core.simulator import RuntimeSimulator
except ImportError:
    from services.tq_adapter import TqAdapter
    from services.data import (
        DataSourceContract,
        DataSourceContractError,
        DataSourceUnavailableErrorContract,
        DataSourceMockExplicitOnlyError,
        get_default_contract,
    )
    from replay import KLineReplayEngine
    from simulator import RuntimeSimulator

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
#  Part 1: 来自 execution_api.py — 股票池 CRUD + 执行
# ══════════════════════════════════════════════════════════════════════

def _generate_mock_bar_data(pool_config: dict) -> dict:
    """为 mock 模式生成模拟行情数据，确保条件评估器能正常运行。
    从候选池/市场源节点提取股票代码，为每只股票生成随机 OHLCV 数据。
    """
    codes = set()
    for node in pool_config.get("nodes", []):
        ntype = node.get("type", "")
        params = node.get("params", {}) or {}
        if ntype in ("tdx_candidate", "market_source"):
            stocks = params.get("stocks") or params.get("tdx_stocks") or []
            for s in stocks:
                if isinstance(s, dict) and s.get("code"):
                    codes.add(s["code"])
                elif isinstance(s, str) and s:
                    codes.add(s)
    if not codes:
        return {}
    rng = random.Random(42)
    bar_data = {}
    for code in codes:
        base = rng.uniform(5.0, 200.0)
        cp = rng.gauss(0, 3.0)
        price = round(base * (1 + cp / 100), 2)
        bar_data[code] = {
            "close": price,
            "pre_close": round(base, 2),
            "open": round(price * (1 + rng.gauss(0, 0.01) / 100), 2),
            "high": round(price * (1 + abs(rng.gauss(0, 0.01)) / 100), 2),
            "low": round(price * (1 - abs(rng.gauss(0, 0.01)) / 100), 2),
            "volume": int(rng.lognormvariate(14, 2)),
            "amount": round(price * int(rng.lognormvariate(14, 2)), 2),
        }
    return bar_data


def _enrich_tdx_node_data(graph_data: dict) -> None:
    """确保 graph_data 中的 TDX 节点包含完整的嵌套结构。

    对以下节点类型进行增强:
    - tdx_state_pool: 确保 params 中有 tdx_psatt 嵌套字典和 stock_data
    - tdx_condition: 确保 params 中有 tdx_func 嵌套字典（16 字段）
    - tdx_candidate: 确保 params 中有 tdx_spinfo 嵌套字典和 tdx_stocks 数组
    - 所有 TDX 边: 确保包含所有 TDX 特有流属性
    """
    from ..core.schemas import TdxFuncModel, TdxPsattModel, TdxSpinfoModel

    nodes = graph_data.get("nodes", [])
    for node in nodes:
        node_type = node.get("type", "")
        params = node.get("params", {})
        if not isinstance(params, dict):
            continue

        if node_type == "tdx_condition":
            # 确保 tdx_func 嵌套字典存在且包含全部 16 字段
            if "tdx_func" not in params or not isinstance(params.get("tdx_func"), dict):
                tdx_func = {}
                for field in TdxFuncModel.model_fields:
                    # 优先从扁平键读取
                    val = params.get(field, params.get(f"tdx_func_{field}"))
                    tdx_func[field] = val if val is not None else TdxFuncModel.model_fields[field].default
                params["tdx_func"] = tdx_func

        elif node_type == "tdx_state_pool":
            # 确保 tdx_psatt 嵌套字典存在且包含全部 14 字段
            if "tdx_psatt" not in params or not isinstance(params.get("tdx_psatt"), dict):
                tdx_psatt = {}
                for field in TdxPsattModel.model_fields:
                    val = params.get(field, params.get(f"tdx_psatt_{field}"))
                    tdx_psatt[field] = val if val is not None else TdxPsattModel.model_fields[field].default
                params["tdx_psatt"] = tdx_psatt

            # 确保 stock_data 字段存在（从 stocks 推导）
            if "stock_data" not in params:
                stocks = params.get("stocks", [])
                if isinstance(stocks, list):
                    stock_data = []
                    for stk in stocks:
                        if isinstance(stk, dict):
                            entry = {
                                "setcode": stk.get("setcode", 0),
                                "code": stk.get("code", stk.get("label", "")),
                                "name": stk.get("name", ""),
                                "inprice": stk.get("inprice", stk.get("p", "0.00")),
                                "now": stk.get("now", "0.00"),
                                "rise": stk.get("rise", "0.00"),
                                "income": stk.get("income", "0.00"),
                                "volume": stk.get("volume", "0"),
                                "indate": stk.get("indate", stk.get("t", "")),
                                "intime": stk.get("intime", ""),
                                "maxrate": stk.get("maxrate", "0.00"),
                                "maxperiod": stk.get("maxperiod", "0"),
                                "maxtime": stk.get("maxtime", "0"),
                                "maxprice": stk.get("maxprice", "0.00"),
                                "idaynum": stk.get("idaynum", "0"),
                            }
                            stock_data.append(entry)
                    if stock_data:
                        params["stock_data"] = stock_data

        elif node_type == "tdx_candidate":
            # 确保 tdx_spinfo 嵌套字典存在且包含全部 5 字段
            if "tdx_spinfo" not in params or not isinstance(params.get("tdx_spinfo"), dict):
                tdx_spinfo = {}
                for field in TdxSpinfoModel.model_fields:
                    val = params.get(field, params.get(f"tdx_spinfo_{field}"))
                    default = TdxSpinfoModel.model_fields[field].default
                    tdx_spinfo[field] = val if val is not None else default
                params["tdx_spinfo"] = tdx_spinfo

            # 双向同步 stocks ↔ tdx_stocks：引擎读 stocks，TDX导出读 tdx_stocks
            stocks = params.get("stocks", [])
            tdx_stocks = params.get("tdx_stocks", [])
            if not stocks and isinstance(tdx_stocks, list) and tdx_stocks:
                # 从 tdx_stocks 反向生成 stocks（支持字符串和 {setcode,code} 字典）
                params["stocks"] = [
                    {"code": (s.get("code", "") if isinstance(s, dict) else s),
                     "label": (s.get("label", s.get("code", "")) if isinstance(s, dict) else s)}
                    for s in tdx_stocks if (isinstance(s, dict) and s.get("code")) or (isinstance(s, str) and s)
                ]
            elif not tdx_stocks and isinstance(stocks, list) and stocks:
                # 从 stocks 生成 tdx_stocks
                new_tdx = []
                for stk in stocks:
                    if isinstance(stk, dict):
                        code = stk.get("code", stk.get("label", ""))
                        setcode = 0
                        if code:
                            if code.startswith(('0', '3')):
                                setcode = 0
                            elif code.startswith('6'):
                                setcode = 1
                            elif code.startswith(('4', '8')):
                                setcode = 2
                        new_tdx.append({"setcode": setcode, "code": code})
                params["tdx_stocks"] = new_tdx

    # 确保边包含所有 TDX 特有流属性
    edges = graph_data.get("edges", [])
    tdx_flow_fields = [
        "tran", "emptyps", "starttype", "starttime", "starttimetype",
        "starttimehms", "cxtype", "cxtime", "cxtimetype", "jgtime", "clr", "size",
    ]
    for edge in edges:
        params = edge.get("params", {})
        if not isinstance(params, dict):
            continue
        # 只对 TDX 边（有 tdx_ 前缀属性的）进行增强
        has_tdx = any(params.get(f"tdx_{f}") is not None for f in tdx_flow_fields)
        if has_tdx:
            for field in tdx_flow_fields:
                # 确保非 tdx_ 前缀的键也存在（向后兼容）
                if field not in params and f"tdx_{field}" in params:
                    params[field] = params[f"tdx_{field}"]
                # 确保 tdx_ 前缀的键也存在
                if f"tdx_{field}" not in params and field in params:
                    params[f"tdx_{field}"] = params[field]


class PoolCreateRequest(BaseModel):
    name: str
    pool_type: str = "dzh"
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    pool_meta: Dict[str, Any] = {}


class PoolUpdateRequest(BaseModel):
    name: str = ""
    pool_type: str = ""
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    pool_meta: Dict[str, Any] = {}


class RunPoolRequest(BaseModel):
    mode: str = "mock"
    mock_mode: bool = False  # Task 11：显式 mock 模式开关，未传或 false 时按契约探测数据源
    data_source: str = ""     # 可选：显式指定数据源名称（覆盖 default_chain）


class TestNodeRequest(BaseModel):
    node_id: str
    mode: str = "mock"
    mock_mode: bool = False


def create_execution_router() -> APIRouter:
    router = APIRouter()

    @router.post("/pools")
    async def create_pool(req: PoolCreateRequest, request: Request):
        storage = request.app.state.storage
        pool_id = str(uuid.uuid4())
        now = datetime.now().isoformat()

        pool_data = {
            "id": pool_id,
            "name": req.name,
            "pool_type": req.pool_type,
            "nodes": req.nodes,
            "edges": req.edges,
            "created_at": now,
            "updated_at": now,
        }
        storage.save_pool(pool_id, pool_data)
        logger.info("创建股票池: %s (%s)", req.name, pool_id)
        return {"code": 0, "data": {"pool_id": pool_id, "name": req.name}}

    @router.get("/pools")
    async def list_pools(request: Request):
        storage = request.app.state.storage
        pools = storage.list_pools()
        for pool in pools:
            pool["id"] = pool.get("pool_id", "")
            counts = storage.get_pool_counts(pool["id"])
            pool["node_count"] = counts["node_count"]
            pool["edge_count"] = counts["edge_count"]
        return {"code": 0, "data": pools}

    @router.get("/pools/{pool_id}")
    async def get_pool(pool_id: str, request: Request):
        storage = request.app.state.storage
        pool = storage.get_pool(pool_id)
        if pool is None:
            raise HTTPException(status_code=404, detail="股票池不存在")
        graph_data = pool.get("params") or {}
        graph_data["name"] = pool.get("name", "")

        # 增强：确保 TDX 节点数据包含完整的嵌套结构
        _enrich_tdx_node_data(graph_data)

        return {"code": 0, "data": graph_data}

    @router.put("/pools/{pool_id}")
    async def update_pool(pool_id: str, req: PoolUpdateRequest, request: Request):
        storage = request.app.state.storage
        pool = storage.get_pool(pool_id)
        if pool is None:
            raise HTTPException(status_code=404, detail="股票池不存在")

        now = datetime.now().isoformat()
        pool_data = {
            "id": pool_id,
            "name": req.name or pool.get("name", ""),
            "pool_type": req.pool_type or pool.get("pool_type", "dzh"),
            "nodes": req.nodes,
            "edges": req.edges,
            "updated_at": now,
        }
        storage.save_pool(pool_id, pool_data)

        logger.info("更新股票池: %s (%s)", req.name, pool_id)
        return {"code": 0, "data": {"pool_id": pool_id, "name": req.name}}

    @router.delete("/pools/{pool_id}")
    async def delete_pool(pool_id: str, request: Request):
        storage = request.app.state.storage
        storage.delete_pool(pool_id)
        logger.info("删除股票池: %s", pool_id)
        return {"code": 0, "data": None}

    @router.post("/pools/{pool_id}/run")
    async def run_pool(pool_id: str, req: RunPoolRequest, request: Request):
        engine = request.app.state.engine
        storage = request.app.state.storage
        tq = request.app.state.tq

        # ------------------------------------------------------------------
        # Task 11: 实盘模式数据源契约集成
        # 启动前先 _probe() 当前数据源
        # 失败且未显式 mock_mode=true → 返回 503 + 错误码 data_source_unavailable
        # 显式 mock_mode=true → 正常执行，记录 "data_source": "mock" 到 execution_record
        # ------------------------------------------------------------------
        contract: DataSourceContract = get_default_contract()
        mock_explicit = bool(req.mock_mode) or req.mode == "mock"
        active_data_source = ""

        if mock_explicit:
            # 用户显式选择 mock：set_active_source('mock')，记录 data_source="mock"
            tq.set_active_source("mock")
            active_data_source = "mock"
            logger.info(
                "run_pool 显式 mock_mode=true，pool_id=%s", pool_id
            )
        else:
            # 实盘/回放等模式：先清除之前 mock 运行留下的显式 source 锁定，
            # 否则 TqAdapter._probe() 会优先使用 _explicit_source 而忽略本次请求的目标数据源。
            tq._explicit_source = None
            # 实盘模式：必须先通过契约探测
            target_source = req.data_source or ""
            if not target_source:
                # 用 default_chain 中第一个真实数据源
                for src in contract.default_chain:
                    if src != "mock":
                        target_source = src
                        break
                if not target_source:
                    target_source = "tq_dll"
            try:
                probe_result = tq._probe(source_name=target_source, contract=contract)
                active_data_source = probe_result.get("name", target_source)
                logger.info(
                    "run_pool 探测通过: data_source=%s, elapsed=%dms, pool_id=%s",
                    active_data_source,
                    probe_result.get("elapsed_ms", -1),
                    pool_id,
                )
            except DataSourceUnavailableErrorContract as e:
                logger.error(
                    "run_pool 数据源 %s 不可用，返回 503: %s",
                    e.source_name,
                    e.message,
                )
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "data_source_unavailable",
                        "message": e.message,
                        "source": e.source_name,
                        "explicit_only": False,
                    },
                )
            except DataSourceContractError as e:
                logger.error("run_pool 契约违反: %s", e)
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "data_source_contract_violation",
                        "message": str(e),
                    },
                )

        # 同步设置 tq_adapter 数据源
        if not mock_explicit:
            tq.set_active_source(active_data_source)
            engine.set_tq_adapter(tq)

        pool = storage.get_pool(pool_id)
        if pool is None:
            raise HTTPException(status_code=404, detail="股票池不存在")

        # 构建引擎需要的 pool_config（nodes/edges 可能在 params 中）
        pool_config = dict(pool)
        params = pool_config.get("params") or {}
        if isinstance(params, dict):
            if "nodes" in params and "nodes" not in pool_config:
                pool_config["nodes"] = params["nodes"]
            if "edges" in params and "edges" not in pool_config:
                pool_config["edges"] = params["edges"]

        # 增强：确保 TDX 节点数据包含完整的嵌套结构（tdx_func/tdx_psatt/tdx_spinfo/stocks 同步）
        _enrich_tdx_node_data(pool_config)

        # 确保节点存在于 pool_node 表，避免 stock_transfer_log 外键约束失败
        for n in pool_config.get('nodes', []):
            nid = n.get('id', '')
            if nid:
                storage.save_pool_node(nid, pool_id, n.get('type', ''), n.get('label', ''))

        # mock 模式：生成模拟行情数据，确保条件评估器（nset=4 等）能正常评估
        mock_bar_data = _generate_mock_bar_data(pool_config) if mock_explicit else None

        engine.events.clear()
        try:
            result = engine.run_pool(pool_config, current_bar_data=mock_bar_data)
            success = isinstance(result, dict) and result.get("success", True)
        except Exception as e:
            logger.error("执行股票池失败: %s", e, exc_info=True)
            result = {"error": str(e)}
            success = False
        # 表驱动：以引擎 self.events 为权威事件流（pool_start / flow_fired / pool_end）
        execution_events = list(engine.events)
        if not execution_events and isinstance(result, dict):
            execution_events = result.get("events", []) or []

        execution_record = {
            "pool_id": pool_id,
            "executed_at": datetime.now().isoformat(),
            "success": success,
            "output_count": result.get("output_count", result.get("total_transferred", 0)),
            "output_stocks": result.get("output_stocks", []),
            "events": execution_events,
            "data_source": active_data_source,  # Task 11: 记录实际使用的数据源
            "mock_mode": mock_explicit,          # Task 11: 记录是否显式 mock 模式
        }
        storage.save_execution(pool_id, execution_record)

        response_data = {
            "code": 0,
            "data": {
                "pool_id": pool_id,
                "success": success,
                "output_stocks": result.get("output_stocks", []),
                "output_count": result.get("output_count", result.get("total_transferred", 0)),
                "events": execution_events,
                "node_states": result.get("node_states", {}),
                "edge_results": result.get("edge_results", []),
                "total_transferred": result.get("total_transferred", 0),
                "total_passed": result.get("total_passed", 0),
                "total_rejected": result.get("total_rejected", 0),
                "data_source": active_data_source,
                "mock_mode": mock_explicit,
            },
        }

        if not success:
            response_data["code"] = 1
            response_data["data"]["error"] = result.get("error", "执行失败")

        return response_data

    @router.post("/pools/{pool_id}/test")
    async def test_node(pool_id: str, req: TestNodeRequest, request: Request):
        engine = request.app.state.engine
        storage = request.app.state.storage

        pool = storage.get_pool(pool_id)
        if pool is None:
            raise HTTPException(status_code=404, detail="股票池不存在")

        # 表驱动：nodes/edges 持久化在 pool.params 中，需先解包到顶层
        if not pool.get("nodes"):
            params = pool.get("params") or {}
            if isinstance(params, dict):
                if "nodes" in params and "nodes" not in pool:
                    pool["nodes"] = params["nodes"]
                if "edges" in params and "edges" not in pool:
                    pool["edges"] = params["edges"]

        nodes = {n["id"]: n for n in pool.get("nodes", [])}
        node = nodes.get(req.node_id)
        if node is None:
            raise HTTPException(status_code=404, detail="节点不存在")

        if req.mode == "real":
            tq = TqAdapter(mock_mode=False)
            engine.set_tq_adapter(tq)

        inputs: Dict = {}
        for k in ("stock_list", "passed", "rejected", "stocks"):
            if k in pool:
                inputs[k] = pool[k]
        if node.get("params"):
            inputs.update(node["params"])
        for k in ("conditions", "match_mode"):
            if k in node:
                inputs[k] = node[k]

        result = engine._run_module(node["type"], inputs)

        return {
            "code": 0,
            "data": {
                "node_id": req.node_id,
                "inputs": inputs,
                "output": result,
            },
        }

    @router.get("/pools/{pool_id}/events")
    async def get_pool_events(pool_id: str, request: Request):
        storage = request.app.state.storage
        pool = storage.get_pool(pool_id)
        if pool is None:
            raise HTTPException(status_code=404, detail="股票池不存在")

        last_execution = storage.get_last_execution(pool_id)
        if last_execution is None:
            return {"code": 0, "data": {"pool_id": pool_id, "events": [], "message": "暂无执行记录"}}

        # 表驱动：execution_record 通过 save_execution 写入时，结构被存为 result JSON
        # 解包后真正的事件/时间戳位于 last_execution["result"] 内
        result = last_execution.get("result") or {}
        if not isinstance(result, dict):
            result = {}
        return {
            "code": 0,
            "data": {
                "pool_id": pool_id,
                "executed_at": result.get("executed_at") or last_execution.get("created_at"),
                "events": result.get("events", []),
            },
        }

    return router


# ══════════════════════════════════════════════════════════════════════
#  Part 2: 来自 replay_api.py — K 线回放 API
# ══════════════════════════════════════════════════════════════════════
# 注：原 replay_api.py 通过 `from .execution_api import _enrich_tdx_node_data`
# 导入 _enrich_tdx_node_data；合并后该函数已在本模块顶部定义，可直接使用。


def create_replay_router() -> APIRouter:
    router = APIRouter(prefix="/api/replay", tags=["回放"])

    def _replays(request: Request) -> dict:
        if not hasattr(request.app.state, "_replay_sessions"):
            request.app.state._replay_sessions = {}
        return request.app.state._replay_sessions

    @router.post("/start")
    async def replay_start(request: Request):
        """创建回放会话并加载 K 线。

        body:
          - pool_id | config   必填，二选一
          - start_date         选填，YYYY-MM-DD
          - end_date           选填，YYYY-MM-DD
          - speed              选填，1.0 / 2.0 / 5.0 / 10.0 / 100.0 / "MAX"
          - base_period        选填，day / 5min / 1min
        返回: { session_id, timeline_plan, total_bars, ... }
        """
        try:
            body = await request.json()
        except Exception as e:
            return {
                "code": 1,
                "msg": f"请求解析失败: {e}",
                "data": None,
            }

        config = body.get("config")
        pool_id = body.get("pool_id")
        # db_pool_id: 用于 SQLite 外键，必须是对应 pool_config 表中的 UUID
        db_pool_id = pool_id
        if not config and not pool_id:
            return {
                "code": 1,
                "msg": "缺少pool_id或config参数（必须至少传一个）",
                "data": {"required": ["pool_id | config"]},
            }

        start_date = body.get("start_date") or "2024-01-01"
        end_date = body.get("end_date") or "2024-03-01"
        speed = body.get("speed", 1.0)
        base_period = body.get("base_period", "day")
        session_id = str(uuid4())

        if pool_id and not config:
            storage = request.app.state.storage
            pool = storage.get_pool(pool_id)
            if pool is None:
                return {
                    "code": 1,
                    "msg": f"股票池不存在: {pool_id}",
                    "data": None,
                }
            params = pool.get("params") or {}
            config = dict(params)
            if not config.get("nodes") and pool.get("nodes"):
                config["nodes"] = pool["nodes"]
            if not config.get("edges") and pool.get("edges"):
                config["edges"] = pool["edges"]
            if not config.get("name"):
                config["name"] = pool.get("name", pool_id)

        # 增强：确保 TDX 节点数据包含完整的嵌套结构
        _enrich_tdx_node_data(config)

        try:
            engine = request.app.state.engine
            storage = getattr(request.app.state, "storage", None)
            replay = KLineReplayEngine(engine, storage=storage)
            result = replay.load_kline_data(
                config,
                base_period=base_period,
                date_range=[start_date, end_date],
                pool_id=db_pool_id or "",
            )
        except Exception as e:
            return {
                "code": 1,
                "msg": f"K线加载失败: {e}",
                "data": None,
            }

        if not isinstance(result, dict) or not result.get("success"):
            return {
                "code": 1,
                "msg": (result or {}).get("error", "K线加载失败"),
                "data": result,
            }

        # 速度初始化
        try:
            replay.set_speed(speed)
        except Exception:
            pass

        # timeline_plan: 把每根 K 线时间做成可读时间轴
        timeline_plan = []
        for i, entry in enumerate(replay._timeline[:200]):
            timeline_plan.append({
                "bar_index": i,
                "time": entry.get("time", ""),
                "stocks_count": len(entry.get("stocks", {})),
            })

        replays = _replays(request)
        replays[session_id] = {
            "engine": replay,
            "pool_id": pool_id,
            "config": config,
            "start_date": start_date,
            "end_date": end_date,
            "speed": speed,
            "base_period": base_period,
            "event_log": [],
        }

        return {
            "code": 0,
            "msg": "ok",
            "data": {
                "session_id": session_id,
                "pool_id": pool_id,
                "timeline_plan": timeline_plan,
                "total_bars": replay._total_bars,
                "date_range": [start_date, end_date],
                "speed": speed,
                "base_period": base_period,
                "codes": result.get("codes", []),
            }
        }

    @router.post("/control")
    async def replay_control(request: Request):
        """回放控制：next/prev/play/pause/speed/jump_to_date/stop"""
        try:
            body = await request.json()
        except Exception as e:
            return {
                "code": 1,
                "msg": f"请求解析失败: {e}",
                "data": None,
            }

        session_id = body.get("session_id")
        action = body.get("action")
        params = body.get("params") or {}

        if not session_id and not action:
            return {
                "code": 1,
                "msg": "缺少session_id和action参数",
                "data": {"required": ["session_id", "action"]},
            }
        if not session_id:
            return {
                "code": 1,
                "msg": "缺少session_id参数",
                "data": {"required": ["session_id"]},
            }
        if not action:
            return {
                "code": 1,
                "msg": "缺少action参数（合法值: next/prev/play/pause/speed/jump_to_date/stop）",
                "data": {
                    "required": ["action"],
                    "allowed": ["next", "prev", "play", "pause", "speed", "jump_to_date", "stop"],
                },
            }

        replays = _replays(request)
        session = replays.get(session_id)
        if not session:
            return {
                "code": 1,
                "msg": f"回放会话不存在: {session_id}",
                "data": None,
            }

        replay: KLineReplayEngine = session["engine"]

        try:
            if action == "next":
                result = replay.step()
                if isinstance(result, dict) and "error" in result:
                    return {
                        "code": 0,
                        "msg": result.get("error", "已到末尾"),
                        "data": result,
                    }
                _record_event(session, "bar_advance", result)
                return {"code": 0, "msg": "ok", "data": result}

            elif action == "prev":
                # 单步回退：直接将 _current_index 减 1
                if replay._current_index <= 0:
                    return {
                        "code": 0,
                        "msg": "已在第一根 K 线",
                        "data": {"current_index": replay._current_index, "total_bars": replay._total_bars},
                    }
                replay._current_index -= 1
                _record_event(session, "bar_rewind", {"bar_index": replay._current_index})
                return {
                    "code": 0,
                    "msg": "ok",
                    "data": {"bar_index": replay._current_index, "time": _current_time(replay)},
                }

            elif action == "play":
                replay.play()
                _record_event(session, "play", {"speed": replay._speed})
                return {"code": 0, "msg": "ok", "data": {"status": "playing", "speed": replay._speed}}

            elif action == "pause":
                replay.pause()
                _record_event(session, "pause", {})
                return {"code": 0, "msg": "ok", "data": {"status": "paused"}}

            elif action == "speed":
                if "value" not in params and "speed" not in params:
                    return {
                        "code": 1,
                        "msg": "speed 操作需要 params.value (或 params.speed) 参数，建议 1/2/5/10/100/MAX",
                        "data": {"required": ["params.value"]},
                    }
                raw = params.get("value", params.get("speed"))
                try:
                    sp = float(raw)
                except (TypeError, ValueError):
                    return {
                        "code": 1,
                        "msg": f"speed 必须为数字，收到: {raw}",
                        "data": None,
                    }
                if sp <= 0:
                    return {
                        "code": 1,
                        "msg": f"speed 必须 > 0，收到: {sp}",
                        "data": None,
                    }
                replay.set_speed(sp)
                session["speed"] = sp
                _record_event(session, "speed", {"speed": sp})
                return {"code": 0, "msg": "ok", "data": {"speed": sp}}

            elif action == "jump_to_date":
                date_str = params.get("date")
                if not date_str:
                    return {
                        "code": 1,
                        "msg": "jump_to_date 操作需要 params.date 参数，格式：YYYY-MM-DD",
                        "data": {"required": ["params.date"], "examples": ["2024-02-15"]},
                    }
                # 在 timeline 中找该日期的第一根 K 线
                target_prefix = str(date_str).strip()
                target_idx = None
                for i, entry in enumerate(replay._timeline):
                    t = str(entry.get("time", ""))
                    if t.startswith(target_prefix):
                        target_idx = i
                        break
                if target_idx is None:
                    # 退化：找日期段中的最大前缀匹配
                    for i, entry in enumerate(replay._timeline):
                        t = str(entry.get("time", ""))
                        if target_prefix in t:
                            target_idx = i
                            break
                if target_idx is None:
                    return {
                        "code": 1,
                        "msg": f"未在时间轴中找到日期 {date_str}",
                        "data": None,
                    }
                # 单向推进到目标
                if target_idx < replay._current_index:
                    replay._current_index = target_idx
                else:
                    safety = 0
                    while replay._current_index < target_idx and safety < 1000000:
                        r = replay.step()
                        if isinstance(r, dict) and "error" in r:
                            break
                        safety += 1
                _record_event(session, "jump_to_date", {"date": date_str, "bar_index": replay._current_index})
                return {
                    "code": 0,
                    "msg": f"已跳转至 {date_str}",
                    "data": {
                        "bar_index": replay._current_index,
                        "time": _current_time(replay),
                        "total_bars": replay._total_bars,
                    },
                }

            elif action == "stop":
                replay.stop()
                replays.pop(session_id, None)
                return {"code": 0, "msg": "ok", "data": {"session_id": session_id, "action": "stop"}}

            else:
                return {
                    "code": 1,
                    "msg": f"不支持的操作: {action}",
                    "data": {"allowed": ["next", "prev", "play", "pause", "speed", "jump_to_date", "stop"]},
                }

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            return {"code": 2, "msg": f"操作执行失败: {e}", "data": {"error": str(e), "traceback": tb[:2000]}}

    @router.get("/state")
    async def replay_state(session_id: str, request: Request):
        replays = _replays(request)
        session = replays.get(session_id)
        if not session:
            return {
                "code": 1,
                "msg": f"回放会话不存在: {session_id}",
                "data": None,
            }
        replay: KLineReplayEngine = session["engine"]
        try:
            snapshot = replay.get_current_snapshot()
            snapshot["session_id"] = session_id
            snapshot["pool_id"] = session.get("pool_id")
            snapshot["speed"] = session.get("speed", 1.0)
            return {"code": 0, "msg": "ok", "data": snapshot}
        except Exception as e:
            return {"code": 1, "msg": f"获取状态失败: {e}", "data": None}

    @router.get("/events")
    async def replay_events(session_id: str, request: Request, since: int = 0, limit: int = 50):
        replays = _replays(request)
        session = replays.get(session_id)
        if not session:
            return {
                "code": 1,
                "msg": f"回放会话不存在: {session_id}",
                "data": None,
            }
        try:
            log = session.get("event_log", [])
            total = len(log)
            events = log[since:since + limit]
            return {
                "code": 0,
                "msg": "ok",
                "data": {
                    "events": events,
                    "total": total,
                    "since": since,
                    "limit": limit,
                    "has_more": (since + limit) < total,
                },
            }
        except Exception as e:
            return {"code": 1, "msg": f"获取事件失败: {e}", "data": None}

    @router.post("/clear_all")
    async def replay_clear_all(request: Request):
        replays = _replays(request)
        n = len(replays)
        for sid, session in list(replays.items()):
            try:
                session["engine"].stop()
            except Exception:
                pass
        replays.clear()
        return {"code": 0, "msg": f"已清理 {n} 个会话", "data": {"cleared": n}}

    return router


def _current_time(replay: KLineReplayEngine) -> str:
    if 0 <= replay._current_index < replay._total_bars:
        return replay._timeline[replay._current_index].get("time", "")
    return ""


def _record_event(session: dict, event_type: str, payload: dict) -> None:
    log = session.setdefault("event_log", [])
    log.append({
        "event_type": event_type,
        "timestamp_idx": len(log),
        **payload,
    })
    if len(log) > 2000:
        session["event_log"] = log[-2000:]


# ══════════════════════════════════════════════════════════════════════
#  Part 3: 来自 sim_api.py — 仿真 API
# ══════════════════════════════════════════════════════════════════════
# 注：原 sim_api.py 通过 `from .execution_api import _enrich_tdx_node_data`
# 导入 _enrich_tdx_node_data；合并后该函数已在本模块顶部定义，可直接使用。


def create_sim_router() -> APIRouter:
    router = APIRouter(prefix="/api/sim", tags=["模拟"])

    @router.post("/start")
    async def sim_start(request: Request):
        """启动一个新的模拟会话"""
        try:
            body = await request.json()
        except Exception as e:
            return {"code": 1, "msg": f"请求解析失败: {e}", "data": None}

        config = body.get("config")
        pool_id = body.get("pool_id")
        if not config and not pool_id:
            return {
                "code": 1,
                "msg": "缺少config或pool_id参数（必须至少传一个）",
                "data": {"required": ["config | pool_id"]},
            }

        speed = body.get("speed", 1.0)
        session_id = str(uuid4())

        if pool_id and not config:
            storage = request.app.state.storage
            pool = storage.get_pool(pool_id)
            if pool is None:
                return {"code": 1, "msg": f"股票池不存在: {pool_id}", "data": None}
            params = pool.get("params") or {}
            config = dict(params)
            # storage.get_pool 的 nodes/edges 通常在 params 中，兜底从顶层取
            if not config.get("nodes") and pool.get("nodes"):
                config["nodes"] = pool["nodes"]
            if not config.get("edges") and pool.get("edges"):
                config["edges"] = pool["edges"]

        # 增强：确保 TDX 节点数据包含完整的嵌套结构
        _enrich_tdx_node_data(config)

        # 确保节点存在于 pool_node 表，避免 stock_transfer_log 外键约束失败
        storage = request.app.state.storage
        for n in config.get('nodes', []):
            nid = n.get('id', '')
            if nid:
                storage.save_pool_node(nid, pool_id, n.get('type', ''), n.get('label', ''))

        pool_data = config.get("pool_data") or config

        try:
            simulator = RuntimeSimulator(pool_data, seed=None)
            simulator.initialize()
        except Exception as e:
            return {"code": 1, "msg": f"模拟器初始化失败: {e}", "data": None}

        # speed 字段写入 simulator，便于 speed action 共享
        try:
            simulator.speed = float(speed)
        except Exception:
            simulator.speed = 1.0

        if not hasattr(request.app.state, "_simulators"):
            request.app.state._simulators = {}
        request.app.state._simulators[session_id] = simulator

        timeline_plan = simulator.get_timeline_plan()

        return {
            "code": 0,
            "msg": "ok",
            "data": {
                "session_id": session_id,
                "timeline_plan": timeline_plan,
                "speed": speed,
            }
        }

    @router.post("/control")
    async def sim_control(request: Request):
        """控制模拟会话（暂停/恢复/单步/停止/跳转/变速）"""
        try:
            body = await request.json()
        except Exception as e:
            return {"code": 1, "msg": f"请求解析失败: {e}", "data": None}

        session_id = body.get("session_id")
        action = body.get("action")
        params = body.get("params") or {}

        if not session_id and not action:
            return {
                "code": 1,
                "msg": "缺少session_id和action参数",
                "data": {"required": ["session_id", "action"]},
            }
        if not session_id:
            return {
                "code": 1,
                "msg": "缺少session_id参数",
                "data": {"required": ["session_id"]},
            }
        if not action:
            return {
                "code": 1,
                "msg": "缺少action参数（合法值: pause/resume/step/stop/jump/speed）",
                "data": {"required": ["action"], "allowed": ["pause", "resume", "step", "stop", "jump", "speed"]},
            }

        simulators = getattr(request.app.state, "_simulators", {})
        simulator = simulators.get(session_id)

        if not simulator:
            return {"code": 1, "msg": f"模拟会话不存在: {session_id}", "data": None}

        try:
            if action == "pause":
                simulator.pause()
                return {"code": 0, "msg": "已暂停", "data": {"session_id": session_id, "action": "pause"}}

            elif action == "resume":
                simulator.resume()
                return {"code": 0, "msg": "已恢复", "data": {"session_id": session_id, "action": "resume"}}

            elif action == "step":
                # step 操作可省略 delta，默认 1.0
                if "delta" not in params:
                    delta = 1.0
                else:
                    try:
                        delta = float(params.get("delta"))
                    except (TypeError, ValueError):
                        return {
                            "code": 1,
                            "msg": "step 操作的 params.delta 必须为数字",
                            "data": None,
                        }
                # 速度因子：speed 倍实际时间步进
                effective_delta = delta * float(getattr(simulator, "speed", 1.0) or 1.0)
                result = simulator.step_with_snapshot(effective_delta)
                return {"code": 0, "msg": "ok", "data": result}

            elif action == "stop":
                simulator.reset()
                del simulators[session_id]
                return {"code": 0, "msg": "已停止并清理会话", "data": {"session_id": session_id, "action": "stop"}}

            elif action == "jump":
                time_str = params.get("time_str")
                if not time_str:
                    return {
                        "code": 1,
                        "msg": "jump 操作需要 params.time_str 参数，格式：HH:MM:SS / YYYY-MM-DD / 纯数字秒数",
                        "data": {"required": ["params.time_str"], "examples": ["14:30:00", "2024-02-15", "34200"]},
                    }
                simulator.jump_to(time_str)
                snapshot = simulator.get_state_snapshot()
                return {"code": 0, "msg": f"已跳转至{time_str}", "data": snapshot}

            elif action == "speed":
                # speed 操作修改 simulator 内部时钟步进速度
                if "value" not in params and "speed" not in params:
                    return {
                        "code": 1,
                        "msg": "speed 操作需要 params.value (或 params.speed) 参数，建议范围 0.1 ~ 1000",
                        "data": {"required": ["params.value"], "examples": [0.5, 1.0, 2.0, 10.0, 100.0]},
                    }
                raw_val = params.get("value", params.get("speed"))
                try:
                    new_speed = float(raw_val)
                except (TypeError, ValueError):
                    return {
                        "code": 1,
                        "msg": f"speed 操作的 value 参数必须为数字，收到: {raw_val}",
                        "data": None,
                    }
                if new_speed <= 0:
                    return {
                        "code": 1,
                        "msg": f"speed 必须 > 0，收到: {new_speed}",
                        "data": None,
                    }
                simulator.speed = new_speed
                return {
                    "code": 0,
                    "msg": f"已设置速度为 {new_speed}",
                    "data": {"session_id": session_id, "action": "speed", "speed": new_speed},
                }

            else:
                return {
                    "code": 1,
                    "msg": f"不支持的操作: {action}",
                    "data": {"allowed": ["pause", "resume", "step", "stop", "jump", "speed"]},
                }

        except Exception as e:
            return {"code": 1, "msg": f"操作执行失败: {e}", "data": None}

    @router.get("/state")
    async def sim_state(session_id: str, request: Request):
        """获取模拟会话的当前状态快照"""
        simulators = getattr(request.app.state, "_simulators", {})
        simulator = simulators.get(session_id)

        if not simulator:
            return {"code": 1, "msg": f"模拟会话不存在: {session_id}", "data": None}

        try:
            snapshot = simulator.get_state_snapshot()
            snapshot["speed"] = float(getattr(simulator, "speed", 1.0) or 1.0)
            return {"code": 0, "msg": "ok", "data": snapshot}
        except Exception as e:
            return {"code": 1, "msg": f"获取状态失败: {e}", "data": None}

    @router.get("/events")
    async def sim_events(session_id: str, request: Request, since: int = 0, limit: int = 50):
        """获取模拟会话的事件日志"""
        simulators = getattr(request.app.state, "_simulators", {})
        simulator = simulators.get(session_id)

        if not simulator:
            return {"code": 1, "msg": f"模拟会话不存在: {session_id}", "data": None}

        try:
            all_events = [e if isinstance(e, dict) else e.to_dict() for e in simulator.event_log]
            total = len(all_events)
            events = all_events[since:since + limit]
            return {
                "code": 0,
                "msg": "ok",
                "data": {
                    "events": events,
                    "total": total,
                    "since": since,
                    "limit": limit,
                    "has_more": (since + limit) < total,
                }
            }
        except Exception as e:
            return {"code": 1, "msg": f"获取事件失败: {e}", "data": None}

    @router.post("/clear_all")
    async def sim_clear_all(request: Request):
        """清理所有模拟会话（开发用）。"""
        simulators = getattr(request.app.state, "_simulators", {})
        n = len(simulators)
        simulators.clear()
        return {"code": 0, "msg": f"已清理 {n} 个会话", "data": {"cleared": n}}

    return router


# === Formula ===


# Config path
_BASE = Path(__file__).parent.parent
_CONFIG = _BASE / "config"
_BUILTIN_FORMULAS_PATH = _CONFIG / "builtin_formulas.json"
_CUSTOM_FORMULAS_PATH = _CONFIG / "custom_formulas.json"


# ─── Pydantic 请求模型 ───────────────────────────────────────

class FormulaCreateRequest(BaseModel):
    name: str = Field(..., description="公式名称")
    description: str = ""
    category: str = Field("indicator", description="公式分类: indicator / xg / exp")
    script: str = Field(..., description="公式脚本内容")
    args: List[Dict[str, Any]] = Field(default_factory=list, description="参数列表")
    formula_type: str = "indicator"


class FormulaUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, description="公式名称")
    description: Optional[str] = Field(None, description="描述")
    category: Optional[str] = Field(None, description="公式分类")
    script: Optional[str] = Field(None, description="公式脚本")
    args: Optional[List[Dict[str, Any]]] = Field(None, description="参数列表")
    formula_type: Optional[str] = Field(None, description="公式类型")


class FormulaTestRequest(BaseModel):
    script: str = Field("MA1:MA(CLOSE,5);", description="公式脚本")
    stock_code: str = Field("000001", description="股票代码")
    period: str = Field("1d", description="周期: 1d / 1wk / 1mon / 1m / 5m / 15m / 30m / 60m")
    args: Dict[str, Any] = Field(default_factory=dict, description="参数值")


class FormulaTestXgRequest(BaseModel):
    script: str = Field(..., description="选股公式脚本")
    stock_list: List[str] = Field(default_factory=lambda: ["000001", "000002"], description="待选股票列表")
    period: str = Field("1d", description="周期")


class FormulaDecodeRequest(BaseModel):
    indi_b64: str = Field(..., description="base64 编码的公式数据")
    ency: int = Field(0, description="DZH 股票池 ency 加密密钥")


class FormulaEncodeRequest(BaseModel):
    formula_text: str = Field(..., description="公式明文文本")
    ency: int = Field(0, description="DZH 股票池 ency 加密密钥")


# ─── 辅助函数 ────────────────────────────────────────────────

def _load_builtin_formulas() -> List[Dict[str, Any]]:
    """加载内置公式，标记 source=builtin。"""
    try:
        if _BUILTIN_FORMULAS_PATH.exists():
            with open(_BUILTIN_FORMULAS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            formulas = data.get("formulas", [])
            for idx, f in enumerate(formulas):
                f["source"] = "builtin"
                f["id"] = f"builtin_{idx}"
            return formulas
    except Exception as e:
        logger.warning("加载内置公式失败: %s", e)
    return []


def _load_custom_formulas() -> List[Dict[str, Any]]:
    """加载用户自定义公式，标记 source=custom。"""
    try:
        if _CUSTOM_FORMULAS_PATH.exists():
            with open(_CUSTOM_FORMULAS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            formulas = data.get("formulas", [])
            for f in formulas:
                f["source"] = "custom"
            return formulas
    except Exception as e:
        logger.warning("加载自定义公式失败: %s", e)
    return []


def _save_custom_formulas(formulas: List[Dict[str, Any]]) -> None:
    """保存用户自定义公式到文件。"""
    _CONFIG.mkdir(parents=True, exist_ok=True)
    # 保存前去掉 source 字段（运行时标记）
    clean = []
    for f in formulas:
        item = {k: v for k, v in f.items() if k != "source"}
        clean.append(item)
    with open(_CUSTOM_FORMULAS_PATH, "w", encoding="utf-8") as f:
        json.dump({"version": "1.0", "formulas": clean}, f, ensure_ascii=False, indent=2)


def _encode_formula(formula_text: str, ency: int = 0) -> str:
    """将公式明文编码为 DZH base64 格式。

    Args:
        formula_text: 公式明文文本。
        ency: DZH 股票池 ency 加密密钥，0 表示不加密。

    Returns:
        base64 编码的公式数据。
    """
    if not formula_text:
        return ''
    raw = formula_text.encode('gbk', errors='replace')
    # 追加终止符
    raw += b';\x00'
    # XOR 加密
    if ency != 0:
        ency_bytes = struct.pack('<q', ency)
        raw = bytes(raw[i] ^ ency_bytes[i % 8] for i in range(len(raw)))
    return base64.b64encode(raw).decode('ascii')


from ..services.providers._common import decode_formula as _decode_formula


def _get_formula_router(request: Request):
    """获取 FormulaRouter 实例（统一公式评估入口）。

    通过 engine.formula_router 获取，避免直连 HQChartProvider 或访问
    底层 provider 管理器的私有字典。
    """
    try:
        engine = request.app.state.engine
        return getattr(engine, 'formula_router', None)
    except Exception as e:
        logger.warning("获取 FormulaRouter 失败: %s", e)
        return None


# 公式保留字集合（输出变量名不可与这些保留字冲突）
# 仅保留数据字段名和关键字：函数名（MA/EMA/SAR/MACD/KDJ 等）作为输出变量名是合法的，
# 不应纳入保留字；数据字段名作为输出变量名会导致字段引用与变量引用冲突。
_FORMULA_RESERVED_WORDS = frozenset({
    # 数据字段（作为输出变量名会导致字段引用与变量引用冲突）
    "CLOSE", "C", "OPEN", "O", "HIGH", "H", "LOW", "L",
    "VOL", "V", "VOLUME", "AMOUNT", "AMO",
    # 关键字
    "AND", "OR", "NOT", "IF", "CROSS",
    # 特殊输出变量（选股公式和专家系统专用，不应被普通指标公式占用）
    "XG", "ENTERLONG", "EXITLONG",
})

# 输出变量正则：匹配 ``NAME:`` 但不匹配 ``NAME:=``（赋值语句）
_OUTVAR_RE = re.compile(r"([A-Za-z_]\w*)\s*:(?!\s*=)")


def _validate_formula_script(script: str, formula_type: str = "indicator"):
    """对公式脚本做语法检查。

    检查项：
      1. 分号完整性 — 每条非空非注释语句必须以 ``;`` 结尾。
      2. 输出变量存在性 — 至少包含一个以 ``:`` 开头的输出变量（非 ``:=``）。
      3. 保留字冲突 — 输出变量名不可与保留字冲突。
      4. 选股公式 XG 输出 — formula_type=xg 时必须包含 ``XG:`` 输出变量。

    Args:
        script: 公式脚本文本。
        formula_type: 公式分类（``indicator`` / ``xg`` / ``exp``）。

    Returns:
        ``(valid, outvars, errors)`` 三元组：
        - valid: 是否通过全部检查。
        - outvars: 检测到的输出变量名列表。
        - errors: 错误信息列表（空列表表示无错误）。
    """
    errors: List[str] = []
    outvars: List[str] = []

    if not script or not script.strip():
        return False, [], ["公式脚本为空"]

    # 去除 {...} 块注释和 //... 行注释
    cleaned = re.sub(r"\{[^}]*\}", " ", script)
    cleaned = re.sub(r"//[^\n]*", " ", cleaned)

    # 1. 分号完整性检查（按行检查）
    stmt_idx = 0
    for line in cleaned.split("\n"):
        stripped = line.strip()
        if not stripped or stripped == ";":
            continue
        stmt_idx += 1
        if not stripped.endswith(";"):
            errors.append(f"第 {stmt_idx} 条语句缺少分号")

    # 2. 输出变量存在性检查
    matches = _OUTVAR_RE.findall(cleaned)
    if matches:
        # 去重保序
        seen = set()
        for m in matches:
            if m not in seen:
                seen.add(m)
                outvars.append(m)
    else:
        errors.append("未找到输出变量（以 : 开头），请至少定义一个输出变量")

    # 3. 保留字冲突检查
    for var in outvars:
        if var.upper() in _FORMULA_RESERVED_WORDS:
            errors.append(f"输出变量名 '{var}' 与保留字冲突")

    # 4. 选股公式 XG 输出检查
    if formula_type == "xg":
        if not any(v.upper() == "XG" for v in outvars):
            errors.append("选股公式必须包含 XG: 输出变量")

    valid = len(errors) == 0
    return valid, outvars, errors


# ─── 工厂函数 ────────────────────────────────────────────────

def create_formula_router() -> APIRouter:
    router = APIRouter()

    # GET /api/formula/health
    @router.get("/health")
    async def formula_health(request: Request):
        """检查 HQChart provider 健康状态。"""
        formula_router = _get_formula_router(request)
        if formula_router is None:
            return {
                "status": "unavailable",
                "error": "FormulaRouter 未初始化",
            }
        provider = getattr(formula_router, '_hqchart_provider', None) or getattr(formula_router, 'hqchart', None)
        if provider is None:
            return {
                "status": "unavailable",
                "error": "HQChart provider 未找到",
            }
        try:
            health = provider.check_health()
            return {
                "status": health.get("status", "unavailable"),
                "version": health.get("version", "unknown"),
                "error": health.get("error"),
            }
        except Exception as e:
            logger.error("HQChart 健康检查失败: %s", e)
            return {
                "status": "unavailable",
                "error": str(e),
            }

    # POST /api/formula/validate
    @router.post("/validate")
    async def validate_formula(request: Request, payload: dict = Body(...)):
        """对公式脚本做语法检查（分号完整性、输出变量存在性、保留字冲突、XG 输出）。"""
        script = payload.get("script", "")
        formula_type = payload.get("formula_type", "indicator")
        valid, outvars, errors = _validate_formula_script(script, formula_type)
        return {"valid": valid, "outvars": outvars, "errors": errors}

    # GET /api/formula/list
    @router.get("/list")
    async def formula_list(request: Request, nset: Optional[int] = Query(None, description="公式集过滤: 0=indicator 1=xg 2=exp 3/4=空 5=全部")):
        """获取所有公式列表（内置 + 自定义）。

        可选查询参数 ``nset`` 按公式集过滤：
          - 0 → 仅 indicator
          - 1 → 仅 xg
          - 2 → 仅 exp
          - 3 → 空列表（基本面条件，无公式库公式）
          - 4 → 空列表（动态行情，无公式库公式）
          - 5 → 全部（集合运算）
          - 未传 → 全部（向后兼容）
        """
        # nset=3/4 对应基本面/动态行情，无公式库公式
        if nset in (3, 4):
            return {"success": True, "data": []}

        builtin = _load_builtin_formulas()
        custom = _load_custom_formulas()
        all_formulas = builtin + custom

        # nset → category 映射
        _NSET_CATEGORY_MAP = {0: "indicator", 1: "xg", 2: "exp"}
        if nset is not None and nset in _NSET_CATEGORY_MAP:
            target_category = _NSET_CATEGORY_MAP[nset]
            all_formulas = [f for f in all_formulas if f.get("category") == target_category]
        # nset=5 或 nset=None → 返回全部

        return {
            "success": True,
            "data": all_formulas,
        }

    # POST /api/formula/create
    @router.post("/create")
    async def formula_create(request: Request, body: FormulaCreateRequest):
        """创建用户自定义公式。"""
        formula_id = uuid.uuid4().hex[:12]
        formula = {
            "id": formula_id,
            "name": body.name,
            "description": body.description,
            "category": body.category,
            "script": body.script,
            "args": body.args,
            "formula_type": body.formula_type,
        }
        custom = _load_custom_formulas()
        custom.append(formula)
        _save_custom_formulas(custom)
        logger.info("创建公式: %s (id=%s)", body.name, formula_id)
        return {
            "success": True,
            "data": {"id": formula_id},
        }

    # PUT /api/formula/{formula_id}
    @router.put("/{formula_id}")
    async def formula_update(formula_id: str, request: Request, body: FormulaUpdateRequest):
        """更新用户自定义公式（内置公式不可修改）。"""
        # 检查是否为内置公式
        builtin = _load_builtin_formulas()
        for bf in builtin:
            if bf.get("id") == formula_id:
                raise HTTPException(400, f"内置公式 '{bf.get('name', formula_id)}' 不可修改")

        custom = _load_custom_formulas()
        found = False
        for f in custom:
            if f.get("id") == formula_id:
                found = True
                if body.name is not None:
                    f["name"] = body.name
                if body.description is not None:
                    f["description"] = body.description
                if body.category is not None:
                    f["category"] = body.category
                if body.script is not None:
                    f["script"] = body.script
                if body.args is not None:
                    f["args"] = body.args
                if body.formula_type is not None:
                    f["formula_type"] = body.formula_type
                break

        if not found:
            raise HTTPException(404, f"公式 {formula_id} 不存在")

        _save_custom_formulas(custom)
        logger.info("更新公式: %s", formula_id)
        return {"success": True}

    # DELETE /api/formula/{formula_id}
    @router.delete("/{formula_id}")
    async def formula_delete(formula_id: str, request: Request):
        """删除用户自定义公式（内置公式不可删除）。"""
        # 检查是否为内置公式
        builtin = _load_builtin_formulas()
        for bf in builtin:
            if bf.get("id") == formula_id:
                raise HTTPException(400, f"内置公式 '{bf.get('name', formula_id)}' 不可删除")

        custom = _load_custom_formulas()
        new_custom = [f for f in custom if f.get("id") != formula_id]
        if len(new_custom) == len(custom):
            raise HTTPException(404, f"公式 {formula_id} 不存在")

        _save_custom_formulas(new_custom)
        logger.info("删除公式: %s", formula_id)
        return {"success": True}

    # POST /api/formula/test
    @router.post("/test")
    async def formula_test(request: Request, body: FormulaTestRequest):
        """测试指标公式计算。

        支持多输出变量公式：单输出返回 ``{code: scalar}``（向后兼容），
        多输出返回 ``{code: {outvar1: val, outvar2: val}}``。
        """
        formula_router = _get_formula_router(request)
        if formula_router is None:
            return {
                "success": False,
                "error": "HQChart 引擎不可用",
            }

        try:
            # eval_outvars 返回 {outvar_name: last_value} 或 None；失败时 raise RuntimeError
            outvars_result = await formula_router.eval_outvars(
                formula=body.script,
                symbol=body.stock_code,
                period=body.period,
                args=body.args,
            )

            if outvars_result is None or not outvars_result:
                return {
                    "success": False,
                    "error": "公式评估未返回结果",
                }

            # 单输出变量：保持向后兼容，返回标量
            # 多输出变量：返回 {outvar: val} 字典
            if len(outvars_result) == 1:
                scalar_value = next(iter(outvars_result.values()))
                test_result = {body.stock_code: scalar_value}
            else:
                test_result = {body.stock_code: outvars_result}

            return {
                "success": True,
                "data": {
                    "result": test_result,
                    "inditype": 0,
                    "outvars": list(outvars_result.keys()),
                },
            }
        except Exception as e:
            logger.error("公式测试失败: %s", e)
            return {
                "success": False,
                "error": str(e),
            }

    # POST /api/formula/test-xg
    @router.post("/test-xg")
    async def formula_test_xg(request: Request, body: FormulaTestXgRequest):
        """测试选股公式。"""
        formula_router = _get_formula_router(request)
        if formula_router is None:
            return {
                "success": False,
                "error": "HQChart 引擎不可用",
            }

        try:
            result = await formula_router.eval_batch(
                formula=body.script,
                symbols=body.stock_list,
                period=body.period,
            )
            # 成功：result 是 {symbol: bool}；失败时 raise RuntimeError（由 except 捕获）
            selected_codes = [code for code, ok in result.items() if ok]
            return {
                "success": True,
                "data": {
                    "success": True,
                    "result": result,
                    "selected_codes": selected_codes,
                },
            }
        except Exception as e:
            logger.error("选股公式测试失败: %s", e)
            return {
                "success": False,
                "error": str(e),
            }

    # POST /api/formula/decode
    @router.post("/decode")
    async def formula_decode(request: Request, body: FormulaDecodeRequest):
        """解码 DZH base64 编码的公式文本。

        返回解码后的明文公式，用于在转移条件面板中预览公式内容。
        """
        try:
            ency_val = body.ency
            # 也尝试从请求上下文中获取 pool ency
            if not ency_val:
                try:
                    ency_val = request.app.state.engine._loop_pool_config.get("ency", 0) if hasattr(request.app.state.engine, '_loop_pool_config') else 0
                except Exception:
                    ency_val = 0
            decoded = _decode_formula(body.indi_b64, ency_val)
            return {
                "success": True,
                "data": {
                    "decoded": decoded,
                    "original_length": len(body.indi_b64),
                    "decoded_length": len(decoded),
                    "ency": ency_val,
                },
            }
        except Exception as e:
            logger.error("公式解码失败: %s", e)
            return {
                "success": False,
                "error": str(e),
            }

    # POST /api/formula/encode
    @router.post("/encode")
    async def formula_encode(request: Request, body: FormulaEncodeRequest):
        """将公式明文编码为 DZH base64 格式。

        用于将公式库中的公式文本编码为转移条件面板所需的 base64 格式。
        """
        try:
            ency_val = body.ency
            if not ency_val:
                try:
                    ency_val = request.app.state.engine._loop_pool_config.get("ency", 0) if hasattr(request.app.state.engine, '_loop_pool_config') else 0
                except Exception:
                    ency_val = 0
            encoded = _encode_formula(body.formula_text, ency_val)
            return {
                "success": True,
                "data": {
                    "encoded": encoded,
                    "original_text": body.formula_text,
                    "ency": ency_val,
                },
            }
        except Exception as e:
            logger.error("公式编码失败: %s", e)
            return {
                "success": False,
                "error": str(e),
            }

    return router

# === Import ===


try:
    from ..converters.dzh import (
        parse_dzh_xml,
        get_all_cell_types,
        get_cell_type_info,
        load_dzh_market_mappings,
        _detect_topology_mode,
        _decode_type200_attr,
        _decode_type201_attr,
        _decode_flow_attr,
        decode_action,
    )
    from ..converters.dzh import export_meta_to_dzh_xml_bytes
    from ..converters.json_xml import import_pool_from_json, export_pool_to_json
    from ..services.tq_adapter import DZH_COL_MAP, TqAdapter
    from ..core.replay import KLineReplayEngine
    from ..services.storage import safe_path_join
except ImportError:
    from converters.dzh import (
        parse_dzh_xml,
        get_all_cell_types,
        get_cell_type_info,
        load_dzh_market_mappings,
        _detect_topology_mode,
        _decode_type200_attr,
        _decode_type201_attr,
        _decode_flow_attr,
        decode_action,
    )
    from converters.dzh import export_meta_to_dzh_xml_bytes
    from converters.json_xml import import_pool_from_json, export_pool_to_json
    from services.tq_adapter import DZH_COL_MAP, TqAdapter
    from replay import KLineReplayEngine
    from services.storage import safe_path_join


# 文件上传大小限制：10MB
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10MB


_this_dir = Path(__file__).parent
_config_dir = _this_dir.parent / "config"
_MODULES_JSON_PATH = _config_dir / "modules.json"

def _load_cell_attr_flag_map():
    """从 config/attr_flag_map.json 加载 cell_attr_flag_map，解析 inherit_from 继承关系。"""
    cfg_path = _config_dir / "attr_flag_map.json"
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return {}
    raw = cfg.get("cell_attr_flag_map", {})
    result = {}
    inherit_pending = {}
    for type_key, flags in raw.items():
        if isinstance(flags, dict) and "inherit_from" in flags:
            inherit_pending[int(type_key)] = flags
        else:
            result[int(type_key)] = dict(flags)
    for type_key, flags in inherit_pending.items():
        parent_key = int(flags["inherit_from"])
        resolved = dict(result.get(parent_key, {}))
        for k, v in flags.items():
            if k == "inherit_from":
                continue
            resolved[k] = v
        result[type_key] = resolved
    return result


_CELL_ATTR_FLAG_MAP = _load_cell_attr_flag_map()

def _load_flow_attr_flag_map():
    """从 config/attr_flag_map.json 加载 flow_attr_masks（深表驱动）。"""
    cfg_path = _config_dir / "attr_flag_map.json"
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            return json.load(f).get("flow_attr_masks", {})
    except Exception:
        return {}


_FLOW_ATTR_FLAG_MAP = _load_flow_attr_flag_map()


def _load_modules_json():
    if _MODULES_JSON_PATH.exists():
        with open(_MODULES_JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


_MODULES_DATA = _load_modules_json()


def encode_cell_attr(cell_type, params, updates):
    flag_map = _CELL_ATTR_FLAG_MAP.get(cell_type, {})
    current = params.get("dzh_attr", {})
    if isinstance(current, dict):
        attr_int = current.get("raw", 0)
    else:
        attr_int = int(current) if current else 0
    if "dzh_attr" in updates and isinstance(updates["dzh_attr"], int):
        return updates["dzh_attr"]
    for key, mask in flag_map.items():
        if key in updates:
            if updates[key]:
                attr_int |= mask
            else:
                attr_int &= ~mask
    return attr_int


def encode_flow_attr(params, updates):
    current = params.get("dzh_attr", 0)
    if isinstance(current, dict):
        attr_int = current.get("raw", 0)
    else:
        attr_int = int(current) if current else 0
    if "dzh_attr" in updates and isinstance(updates["dzh_attr"], int):
        return updates["dzh_attr"]
    for key, mask in _FLOW_ATTR_FLAG_MAP.items():
        if key in updates:
            if updates[key]:
                attr_int |= mask
            else:
                attr_int &= ~mask
    return attr_int


def encode_action_value(action_dict):
    if not isinstance(action_dict, dict):
        return 0
    action_type = action_dict.get("type", "")
    value = action_dict.get("value", 0)
    type_id_map = {"none": 0, "buy_amount": 1, "buy_shares": 2, "sell_shares": 3}
    tid = type_id_map.get(action_type, 0)
    if tid == 0:
        return 0
    value = max(0, min(int(value), 0xFFFF))
    return (tid << 28) | value


class _PoolStore:
    def __init__(self):
        self._pool = None

    @property
    def pool(self):
        if self._pool is None:
            self._pool = {
                "name": "untitled",
                "nodes": [],
                "edges": [],
                "schedules": [],
                "pool_meta": {},
                "trades": [],
                "opentrades": [],
            }
        return self._pool

    @pool.setter
    def pool(self, value):
        self._pool = value

    def clear(self):
        self._pool = None


_store = _PoolStore()


def _get_replay_engine(request: Request):
    """从 app.state 获取回放引擎实例，避免全局单例的线程安全问题"""
    return getattr(request.app.state, '_dzh_replay_engine', None)


def _set_replay_engine(request: Request, engine):
    """设置回放引擎实例到 app.state"""
    request.app.state._dzh_replay_engine = engine


def _find_xml_file_fuzzy(base_dir: str, filename: str):
    exact_path = os.path.join(base_dir, filename)
    if os.path.exists(exact_path):
        return exact_path
    name_without_ext = filename.rsplit('.', 1)[0] if '.' in filename else filename
    try:
        for f in os.listdir(base_dir):
            if f.lower().endswith('.xml') or f.lower().endswith('.XML'):
                f_base = f.rsplit('.', 1)[0]
                if f_base == name_without_ext:
                    return os.path.join(base_dir, f)
    except OSError:
        pass
    try:
        for f in os.listdir(base_dir):
            if f.lower().endswith('.xml') or f.lower().endswith('.XML'):
                f_base = f.rsplit('.', 1)[0]
                if len(name_without_ext) >= 2 and f_base.startswith(name_without_ext[:2]):
                    return os.path.join(base_dir, f)
    except OSError:
        pass
    return None


def _err(msg, status_code=400):
    raise HTTPException(status_code=status_code, detail={"success": False, "error": msg})


def _import_as_tdx(content: bytes, filename: str) -> dict:
    """将 TDX 格式的 XML 内容解析并转换为前端兼容格式。

    当 is_tdx_format() 检测到 TDX 格式时，由 /api/dzh/import 和
    /api/dzh/import-and-save 端点调用，替代 parse_dzh_xml() 路径。
    """
    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        try:
            from ..converters.tdx import parse_tdx_xml
        except ImportError:
            from converters.tdx import parse_tdx_xml
        tdx_pool = parse_tdx_xml(tmp_path)
        # 延迟导入避免循环依赖：app → import_api → app
        import importlib
        app_mod = importlib.import_module("meta_core.app")
        _tdx_pool_to_frontend = getattr(app_mod, "_tdx_pool_to_frontend", None)
        if _tdx_pool_to_frontend is None:
            raise RuntimeError("_tdx_pool_to_frontend 未找到，无法转换 TDX 格式")
        pool_name = filename.rsplit(".", 1)[0] if "." in filename else filename
        return _tdx_pool_to_frontend(tdx_pool, pool_name)
    finally:
        os.unlink(tmp_path)


# ══════════════════════════════════════════════════════════════════════
#  Part 1: 来自 dzh_api.py — DZH 导入导出 API
# ══════════════════════════════════════════════════════════════════════

def create_dzh_router() -> APIRouter:
    router = APIRouter(prefix="/api/dzh", tags=["dzh"])

    # ================================================================
    # 1. POST /api/dzh/import — 导入XML（文件上传 或 JSON体）
    # ================================================================
    @router.post("/import")
    async def dzh_import(
        request: Request,
        xml_content: str | None = Form(None),
        execute: bool = Form(False),
    ):
        content = None
        filename = "upload.xml"

        form = await request.form()

        uploaded_file = form.get("file")
        if uploaded_file and hasattr(uploaded_file, "filename") and uploaded_file.filename:
            content = await uploaded_file.read()
            if len(content) > MAX_UPLOAD_SIZE:
                return {"success": False, "error": f"文件大小超过限制 ({MAX_UPLOAD_SIZE // 1024 // 1024}MB)"}
            filename = uploaded_file.filename

        elif xml_content:
            content = xml_content.encode("utf-8")

        else:
            try:
                body = await request.json()
            except Exception:
                body = None

            if body and isinstance(body, dict):
                raw = body.get("xml_content", "")
                if isinstance(raw, str) and raw.strip():
                    if raw.startswith("<"):
                        content = raw.encode("utf-8")
                    elif len(raw) > 100:
                        try:
                            import base64 as b64
                            content = b64.b64decode(raw)
                        except Exception:
                            content = raw.encode("utf-8")
                    else:
                        content = raw.encode("utf-8")
                    filename = body.get("filename", "upload.xml")

                # 如果提供了 filename，从 dzhpool 目录读取
                if content is None:
                    fn = body.get('filename', '') if body else ''
                    if fn:
                        try:
                            xml_path = safe_path_join(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'dzhpool'), fn)
                        except ValueError as e:
                            return {"success": False, "error": str(e)}
                        if os.path.isfile(xml_path):
                            # DZH XML 通常是 GBK 编码，回退到 UTF-8
                            raw = open(xml_path, 'rb').read()
                            for enc in ('gbk', 'gb2312', 'utf-8', 'latin-1'):
                                try:
                                    content = raw.decode(enc).encode('utf-8')
                                    break
                                except (UnicodeDecodeError, UnicodeEncodeError):
                                    continue
                            filename = fn

        if content is None:
            return {"success": False, "error": "请上传文件或提供 xml_content 或 filename"}

        try:
            # ── 自动检测 TDX 格式并路由到正确的解析器 ──
            try:
                from ..converters.dzh import is_tdx_format
            except ImportError:
                from converters.dzh import is_tdx_format
            if is_tdx_format(content):
                parsed = _import_as_tdx(content, filename)
            else:
                parsed = parse_dzh_xml(content, filename=filename)
        except Exception as e:
            return {"success": False, "error": f"XML解析失败: {e}"}

        _store.pool = parsed
        bus_nodes = [n for n in parsed["nodes"] if n["type"] not in ("text_label", "flow_arrow")]
        result = {
            "success": True,
            "data": parsed,
            "meta": {
                "name": parsed.get("name", filename),
                "node_count": len(bus_nodes),
                "edge_count": len(parsed.get("edges", [])),
                "stock_count": sum(
                    len(n.get("params", {}).get("stocks", [])) for n in parsed["nodes"]
                ),
                "topology_mode": parsed.get("_meta", {}).get("topology_mode", "unknown"),
            },
        }

        if execute:
            from ..converters.dzh import DZHPoolExecutor
            engine = request.app.state.engine
            try:
                exec_result = engine.execute_pool(parsed)
                result["execution"] = exec_result
            except Exception as e:
                result["execution"] = {"success": False, "error": str(e)}

        return result

    # ================================================================
    # 1b. POST /api/dzh/import-and-save — 导入XML并持久化到SQLite
    # ================================================================
    @router.post("/import-and-save")
    async def dzh_import_and_save(
        request: Request,
        xml_content: str | None = Form(None),
        execute: bool = Form(False),
    ):
        content = None
        filename = "upload.xml"

        form = await request.form()

        uploaded_file = form.get("file")
        if uploaded_file and hasattr(uploaded_file, "filename") and uploaded_file.filename:
            content = await uploaded_file.read()
            if len(content) > MAX_UPLOAD_SIZE:
                return {"success": False, "error": f"文件大小超过限制 ({MAX_UPLOAD_SIZE // 1024 // 1024}MB)"}
            filename = uploaded_file.filename

        elif xml_content:
            content = xml_content.encode("utf-8")

        else:
            try:
                body = await request.json()
            except Exception:
                body = None

            if body and isinstance(body, dict):
                raw = body.get("xml_content", "")
                if isinstance(raw, str) and raw.strip():
                    if raw.startswith("<"):
                        content = raw.encode("utf-8")
                    elif len(raw) > 100:
                        try:
                            import base64 as b64
                            content = b64.b64decode(raw)
                        except Exception:
                            content = raw.encode("utf-8")
                    else:
                        content = raw.encode("utf-8")
                    filename = body.get("filename", "upload.xml")

                # 如果提供了 filename，从 dzhpool 目录读取
                if content is None:
                    fn = body.get('filename', '') if body else ''
                    if fn:
                        try:
                            xml_path = safe_path_join(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'dzhpool'), fn)
                        except ValueError as e:
                            return {"success": False, "error": str(e)}
                        if os.path.isfile(xml_path):
                            # DZH XML 通常是 GBK 编码，回退到 UTF-8
                            raw = open(xml_path, 'rb').read()
                            for enc in ('gbk', 'gb2312', 'utf-8', 'latin-1'):
                                try:
                                    content = raw.decode(enc).encode('utf-8')
                                    break
                                except (UnicodeDecodeError, UnicodeEncodeError):
                                    continue
                            filename = fn

        if content is None:
            return {"success": False, "error": "请上传文件或提供 xml_content 或 filename"}

        try:
            # ── 自动检测 TDX 格式并路由到正确的解析器 ──
            try:
                from ..converters.dzh import is_tdx_format
            except ImportError:
                from converters.dzh import is_tdx_format
            if is_tdx_format(content):
                parsed = _import_as_tdx(content, filename)
            else:
                parsed = parse_dzh_xml(content, filename=filename)
        except Exception as e:
            return {"success": False, "error": f"XML解析失败: {e}"}

        pool_id = "pool_" + uuid.uuid4().hex[:12]

        _store.pool = parsed

        # 持久化到 Storage（修复：原端点未写入storage）
        try:
            storage = getattr(request.app.state, 'storage', None)
            if storage:
                pool_name = parsed.get("name", filename.rsplit(".", 1)[0] if "." in filename else filename)
                storage.save_pool(pool_id, {
                    "name": pool_name,
                    "pool_type": "dzh",
                    "description": f"Imported from {filename}",
                    "topology_mode": parsed.get("_meta", {}).get("topology_mode", "unknown"),
                    "status": "active",
                    "params": parsed,
                })
        except Exception as e:
            import logging as _logging
            _logging.getLogger(__name__).warning("import-and-save storage写入失败: %s", e)

        bus_nodes = [n for n in parsed["nodes"] if n["type"] not in ("text_label", "flow_arrow")]
        result = {
            "success": True,
            "pool_id": pool_id,
            "data": parsed,
            "meta": {
                "name": parsed.get("name", filename),
                "node_count": len(bus_nodes),
                "edge_count": len(parsed.get("edges", [])),
                "stock_count": sum(
                    len(n.get("params", {}).get("stocks", [])) for n in parsed["nodes"]
                ),
                "topology_mode": parsed.get("_meta", {}).get("topology_mode", "unknown"),
            },
        }

        if execute:
            from ..converters.dzh import DZHPoolExecutor
            engine = request.app.state.engine
            try:
                exec_result = engine.execute_pool(parsed)
                result["execution"] = exec_result
            except Exception as e:
                result["execution"] = {"success": False, "error": str(e)}

        return result

    # ================================================================
    # 2. POST /api/dzh/export — 导出为XML（JSON体输入）
    # ================================================================
    @router.post("/export")
    async def dzh_export(request: Request):
        try:
            body = await request.json()
        except Exception as e:
            return {"success": False, "error": f"请求解析失败: {e}"}

        config = body.get("config") or body.get("pool_data") or body
        if not config or "nodes" not in config:
            return {"success": False, "error": "配置无效，缺少 nodes"}

        try:
            xml_bytes = export_meta_to_dzh_xml_bytes(config)
            fname = config.get("name", "pool") + ".xml"
            ascii_fname = quote(fname)
            return Response(
                content=xml_bytes,
                media_type="application/xml",
                headers={
                    "Content-Disposition": f"attachment; filename*=UTF-8''{ascii_fname}"
                },
            )
        except Exception as e:
            return {"success": False, "error": f"导出失败: {e}"}

    # ================================================================
    # 3. GET /api/dzh/cells — 获取当前池中所有 cell
    # ================================================================
    @router.get("/cells")
    async def get_cells():
        pool = _store.pool
        cells = []
        for node in pool.get("nodes", []):
            cell_data = {
                "id": node.get("id"),
                "type": node.get("type"),
                "dzh_cell_type": node.get("dzh_cell_type"),
                "label": node.get("label", ""),
                "position": node.get("position", {}),
                "params": node.get("params", {}),
            }
            cells.append(cell_data)
        return {"cells": cells, "total": len(cells)}

    # ================================================================
    # 4. POST /api/dzh/cells — 创建新 cell
    # ================================================================
    @router.post("/cells")
    async def create_cell(request: Request):
        try:
            body = await request.json()
        except Exception as e:
            return {"success": False, "error": f"请求解析失败: {e}"}

        cell_type = body.get("cell_type", 200)
        position = body.get("position", {"x": 100, "y": 100, "width": 117, "height": 100})
        params = body.get("params", {})

        type_info = get_cell_type_info(cell_type)
        new_id = f"m_{uuid.uuid4().hex[:8]}"
        label = params.get("text") or params.get("label") or f"{type_info['type_name']}_{len(_store.pool['nodes'])}"

        new_cell = {
            "id": new_id,
            "type": type_info["type_name"],
            "dzh_cell_type": cell_type,
            "label": label,
            "position": position,
            "params": {**params},
        }
        _store.pool["nodes"].append(new_cell)

        return {"success": True, "data": new_cell}

    # ================================================================
    # 5. PUT /api/dzh/cells/{cell_id} — 更新 cell 参数
    # ================================================================
    @router.put("/cells/{cell_id}")
    async def update_cell(cell_id: str, request: Request):
        try:
            body = await request.json()
        except Exception as e:
            return {"success": False, "error": f"请求解析失败: {e}"}

        pool = _store.pool
        target = None
        for node in pool.get("nodes", []):
            if node.get("id") == cell_id:
                target = node
                break

        if target is None:
            return {"success": False, "error": f"Cell 不存在: {cell_id}"}

        updates = body.get("params", body)
        if "position" in body:
            target["position"] = {**target.get("position", {}), **body["position"]}
        if "label" in body:
            target["label"] = body["label"]

        params = target.get("params", {})
        cell_type = target.get("dzh_cell_type", 0)
        flag_map = _CELL_ATTR_FLAG_MAP.get(cell_type, {})
        bit_flag_keys = [k for k in updates if k in flag_map]

        if "dzh_attr" in updates and isinstance(updates["dzh_attr"], int):
            new_attr_int = updates["dzh_attr"]
        elif bit_flag_keys:
            new_attr_int = encode_cell_attr(cell_type, params, updates)
        else:
            new_attr_int = None

        if new_attr_int is not None:
            params["attr_int"] = new_attr_int
            params["attr"] = str(new_attr_int)
            if cell_type in (200, 203):
                params["dzh_attr"] = _decode_type200_attr(new_attr_int)
            elif cell_type == 201:
                params["dzh_attr"] = _decode_type201_attr(new_attr_int)
            else:
                params["dzh_attr"] = new_attr_int

        for action_key in ("enter_action", "exit_action"):
            if action_key in updates and isinstance(updates[action_key], dict):
                action_int = encode_action_value(updates[action_key])
                raw_key = action_key.replace("_action", "")
                params[raw_key] = action_int
                if action_int != 0:
                    params[action_key] = decode_action(action_int)
                else:
                    params.pop(action_key, None)

        handled = set(bit_flag_keys) | {"dzh_attr", "enter_action", "exit_action"}
        for k, v in updates.items():
            if k not in handled:
                params[k] = v

        # === 备选池(type=202)特殊字段处理 ===
        if cell_type == 202:
            # 处理 selections 更新
            if "selections" in updates and isinstance(updates["selections"], list):
                params["selections"] = updates["selections"]
                # 同步重建 raw_attrtext
                from ..converters.dzh import build_attrtext_from_selections
                params["raw_attrtext"] = build_attrtext_from_selections(updates["selections"])
                params["attrtext"] = params["raw_attrtext"]

            # 处理 reload_mode 更新
            if "reload_mode" in updates:
                from ..converters.dzh import encode_reload_mode
                mode = updates["reload_mode"]
                param = updates.get("reload_param")
                reload_val = encode_reload_mode(mode, param)
                params["reload_sec"] = reload_val
                params["reload"] = str(reload_val)
                # 保留参数以便 decode_reload_mode 消解 -57387 歧义
                if param is not None:
                    params["reload_param"] = param
                    params["daily_time"] = param

        target["params"] = params

        # 确保 type=202 返回数据包含解析后的字段
        if cell_type == 202:
            if "selections" not in params and params.get("raw_attrtext"):
                from ..converters.dzh import parse_attrtext_selections
                params["selections"] = parse_attrtext_selections(params.get("raw_attrtext", ""))
            if "reload_mode" not in params:
                from ..converters.dzh import decode_reload_mode
                _ri = decode_reload_mode(params.get("reload_sec", 0), node_context=params)
                params["reload_mode"] = _ri["mode"]
                params["reload_param"] = _ri["param"]

        return {"success": True, "data": target}

    # ================================================================
    # 6. DELETE /api/dzh/cells/{cell_id} — 删除 cell 及其连接的 flow
    # ================================================================
    @router.delete("/cells/{cell_id}")
    async def delete_cell(cell_id: str):
        pool = _store.pool
        nodes = pool.get("nodes", [])
        edges = pool.get("edges", [])

        found = False
        new_nodes = []
        for node in nodes:
            if node.get("id") == cell_id:
                found = True
            else:
                new_nodes.append(node)

        if not found:
            return {"success": False, "error": f"Cell 不存在: {cell_id}"}

        removed_edges = []
        new_edges = []
        for edge in edges:
            src = edge.get("from", "") or edge.get("source", {}).get("node_id", "")
            tgt = edge.get("to", "") or edge.get("target", {}).get("node_id", "")
            if src == cell_id or tgt == cell_id:
                removed_edges.append(edge)
            else:
                new_edges.append(edge)

        pool["nodes"] = new_nodes
        pool["edges"] = new_edges

        return {
            "success": True,
            "data": {
                "deleted_cell": cell_id,
                "removed_flows": len(removed_edges),
                "remaining_cells": len(new_nodes),
                "remaining_flows": len(new_edges),
            },
        }

    # ================================================================
    # 7. GET /api/dzh/flows — 获取所有 flow
    # ================================================================
    @router.get("/flows")
    async def get_flows():
        pool = _store.pool
        flows = []
        for edge in pool.get("edges", []):
            flow_data = {
                "id": edge.get("id"),
                "from": edge.get("source", {}).get("node_id", ""),
                "to": edge.get("target", {}).get("node_id", ""),
                "params": edge.get("params", {}),
            }
            flows.append(flow_data)
        return {"flows": flows, "total": len(flows)}

    # ================================================================
    # 8. POST /api/dzh/flows — 创建新 flow（含拓扑校验）
    # ================================================================
    @router.post("/flows")
    async def create_flow(request: Request):
        try:
            body = await request.json()
        except Exception as e:
            return {"success": False, "error": f"请求解析失败: {e}"}

        src_id = body.get("from")
        tgt_id = body.get("to")
        params = body.get("params", {})

        if not src_id or not tgt_id:
            return {"success": False, "error": "缺少 from 或 to 参数"}

        pool = _store.pool
        node_ids = {n["id"] for n in pool.get("nodes", [])}
        if src_id not in node_ids:
            return {"success": False, "error": f"源节点不存在: {src_id}"}
        if tgt_id not in node_ids:
            return {"success": False, "error": f"目标节点不存在: {tgt_id}"}

        for edge in pool.get("edges", []):
            existing_src = edge.get("from", "") or edge.get("source", {}).get("node_id", "")
            existing_tgt = edge.get("to", "") or edge.get("target", {}).get("node_id", "")
            if existing_src == src_id and existing_tgt == tgt_id:
                return {"success": False, "error": f"Flow 已存在: {src_id} -> {tgt_id}"}

        new_edge = {
            "id": f"e_{uuid.uuid4().hex[:8]}",
            "from": src_id,
            "to": tgt_id,
            "params": {
                "dzh_attr": 0,
                "delete_source": False,
                "keep_source": False,
                "clear_dest_first": False,
                "output_constituent": False,
                "force_move": False,
                "begin": 0,
                "begint": "0",
                "end": 0,
                "endt": "0",
                "interval_sec": 60,
                "clr": "-1",
                **params,
            },
        }
        pool["edges"].append(new_edge)

        mode = _detect_topology_mode(pool["nodes"], pool["edges"])
        new_edge["params"]["_topology_mode"] = mode

        return {"success": True, "data": new_edge, "topology_mode": mode}

    # ================================================================
    # 9. PUT /api/dzh/flows/{flow_id} — 更新 flow 参数
    # ================================================================
    @router.put("/flows/{flow_id}")
    async def update_flow(flow_id: str, request: Request):
        try:
            body = await request.json()
        except Exception as e:
            return {"success": False, "error": f"请求解析失败: {e}"}

        pool = _store.pool
        target = None
        for edge in pool.get("edges", []):
            if edge.get("id") == flow_id:
                target = edge
                break

        if target is None:
            return {"success": False, "error": f"Flow 不存在: {flow_id}"}

        updates = body.get("params", body)
        params = target.get("params", {})

        bit_flag_keys = [k for k in updates if k in _FLOW_ATTR_FLAG_MAP]

        if "dzh_attr" in updates and isinstance(updates["dzh_attr"], int):
            new_attr_int = updates["dzh_attr"]
        elif bit_flag_keys:
            new_attr_int = encode_flow_attr(params, updates)
        else:
            new_attr_int = None

        if new_attr_int is not None:
            params["dzh_attr"] = new_attr_int
            decoded = _decode_flow_attr(new_attr_int)
            for k in ("delete_source", "keep_source", "clear_dest_first",
                       "output_constituent", "force_move"):
                params[k] = decoded[k]

        timing_keys = {"begin", "begint", "end", "endt", "interval"}
        for tk in timing_keys:
            if tk in updates:
                if tk == "interval":
                    params["interval_sec"] = updates[tk]
                else:
                    params[tk] = updates[tk]

        handled = set(bit_flag_keys) | {"dzh_attr"} | timing_keys
        for k, v in updates.items():
            if k not in handled:
                params[k] = v

        if "from" in body:
            target["from"] = body["from"]
        if "to" in body:
            target["to"] = body["to"]

        return {"success": True, "data": target}

    # ================================================================
    # 10. DELETE /api/dzh/flows/{flow_id} — 删除 flow
    # ================================================================
    @router.delete("/flows/{flow_id}")
    async def delete_flow(flow_id: str):
        pool = _store.pool
        edges = pool.get("edges", [])
        found = False
        new_edges = []
        for edge in edges:
            if edge.get("id") == flow_id:
                found = True
            else:
                new_edges.append(edge)

        if not found:
            return {"success": False, "error": f"Flow 不存在: {flow_id}"}

        pool["edges"] = new_edges
        return {
            "success": True,
            "data": {
                "deleted_flow": flow_id,
                "remaining_flows": len(new_edges),
            },
        }

    # ================================================================
    # 10.5. POST /api/dzh/flows/reorder — 重排 flow 执行顺序
    # ================================================================
    @router.post("/flows/reorder")
    async def reorder_flows(request: Request):
        try:
            body = await request.json()
        except Exception as e:
            return {"success": False, "error": f"请求解析失败: {e}"}

        flow_ids = body.get("flow_ids")
        if not flow_ids or not isinstance(flow_ids, list):
            return {"success": False, "error": "flow_ids 必须是非空列表"}

        pool = _store.pool
        edges = pool.get("edges", [])
        edge_map = {e.get("id"): e for e in edges}

        # 校验所有 flow_id 存在
        missing = [fid for fid in flow_ids if fid not in edge_map]
        if missing:
            return {"success": False, "error": f"Flow 不存在: {missing}"}

        # 按 flow_ids 顺序重排，未列出的追加到末尾
        reordered = []
        seen = set()
        for idx, fid in enumerate(flow_ids):
            e = edge_map.get(fid)
            if e and fid not in seen:
                if not e.get("params"):
                    e["params"] = {}
                e["params"]["_order"] = idx
                reordered.append(e)
                seen.add(fid)

        next_order = len(reordered)
        for e in edges:
            if e.get("id") not in seen:
                if not e.get("params"):
                    e["params"] = {}
                e["params"]["_order"] = next_order
                next_order += 1
                reordered.append(e)

        pool["edges"] = reordered
        return {
            "success": True,
            "reordered": len(flow_ids),
            "edges": [{"id": e.get("id"), "_order": e.get("params", {}).get("_order")} for e in reordered],
        }

    # ================================================================
    # 11. POST /api/dzh/validate-roundtrip — 往返校验
    # ================================================================
    @router.post("/validate-roundtrip")
    async def validate_roundtrip(
        request: Request,
        file: UploadFile | None = File(None),
    ):
        try:
            body = await request.json()
        except Exception:
            body = None

        filepath = None
        content = None
        filename = "roundtrip_test.xml"

        if file and file.filename:
            content = await file.read()
            if len(content) > MAX_UPLOAD_SIZE:
                return {"success": False, "error": f"文件大小超过限制 ({MAX_UPLOAD_SIZE // 1024 // 1024}MB)"}
            filename = file.filename
        elif body and isinstance(body, dict):
            fp = body.get("filepath")
            if fp and os.path.exists(fp):
                filepath = fp
                filename = os.path.basename(fp)
            raw = body.get("xml_content")
            if isinstance(raw, str) and raw.strip():
                content = raw.encode("utf-8")

        if content is None and filepath is None:
            if body and isinstance(body, dict) and "config" in body:
                config = body["config"]
            else:
                config = _store.pool
            if not config or "nodes" not in config:
                return {"success": False, "error": "无有效数据可验证"}
        else:
            if filepath:
                with open(filepath, "rb") as f:
                    content = f.read()

            try:
                config = parse_dzh_xml(content, filename=filename)
            except Exception as e:
                return {"success": False, "error": f"首次解析失败: {e}", "diffs": [], "stats": {}}

        original_json = json.dumps(config, ensure_ascii=False, sort_keys=True, default=str)

        try:
            xml_bytes = export_meta_to_dzh_xml_bytes(config)
        except Exception as e:
            return {
                "success": False,
                "error": f"导出步骤失败: {e}",
                "diffs": [{"stage": "export", "error": str(e)}],
                "stats": {"node_count": len(config.get("nodes", [])), "edge_count": len(config.get("edges", []))},
            }

        try:
            re_parsed = parse_dzh_xml(xml_bytes, filename="roundtrip_reparse.xml")
        except Exception as e:
            return {
                "success": False,
                "error": f"重新解析失败: {e}",
                "diffs": [{"stage": "re-parse", "error": str(e)}],
                "stats": {"node_count": len(config.get("nodes", [])), "edge_count": len(config.get("edges", []))},
            }

        re_parsed_json = json.dumps(re_parsed, ensure_ascii=False, sort_keys=True, default=str)

        diffs = []

        orig_nodes = sorted(config.get("nodes", []), key=lambda n: n.get("id", ""))
        re_nodes = sorted(re_parsed.get("nodes", []), key=lambda n: n.get("id", ""))
        if len(orig_nodes) != len(re_nodes):
            diffs.append({
                "field": "node_count",
                "original": len(orig_nodes),
                "roundtrip": len(re_nodes),
                "status": "mismatch",
            })

        orig_edges = sorted(config.get("edges", []), key=lambda e: e.get("id", ""))
        re_edges = sorted(re_parsed.get("edges", []), key=lambda e: e.get("id", ""))
        if len(orig_edges) != len(re_edges):
            diffs.append({
                "field": "edge_count",
                "original": len(orig_edges),
                "roundtrip": len(re_edges),
                "status": "mismatch",
            })

        for i, (on, rn) in enumerate(zip(orig_nodes, re_nodes)):
            oid = on.get("id", f"unknown_{i}")
            rid = rn.get("id", f"unknown_{i}")
            if oid != rid:
                diffs.append({"field": f"node[{i}].id", "original": oid, "roundtrip": rid, "status": "mismatch"})
            opos = on.get("position", {})
            rpos = rn.get("position", {})
            if opos != rpos:
                diffs.append({"field": f"node[{oid}].position", "original": opos, "roundtrip": rpos, "status": "changed"})
            oct_val = on.get("dzh_cell_type")
            rct_val = rn.get("dzh_cell_type")
            if oct_val != rct_val:
                diffs.append({"field": f"node[{oid}].dzh_cell_type", "original": oct_val, "roundtrip": rct_val, "status": "mismatch"})
            olbl = on.get("label", "")
            rlbl = rn.get("label", "")
            if olbl != rlbl:
                diffs.append({"field": f"node[{oid}].label", "original": olbl, "roundtrip": rlbl, "status": "changed"})

        for i, (oe, re_) in enumerate(zip(orig_edges, re_edges)):
            eid = oe.get("id", f"e_unknown_{i}")
            osrc = oe.get("source", {}).get("node_id", "")
            otgt = oe.get("target", {}).get("node_id", "")
            rsrc = re_.get("source", {}).get("node_id", "")
            rtgt = re_.get("target", {}).get("node_id", "")
            if osrc != rsrc or otgt != rtgt:
                diffs.append({
                    "field": f"edge[{eid}].connection",
                    "original": f"{osrc}->{otgt}",
                    "roundtrip": f"{rsrc}->{rtgt}",
                    "status": "mismatch",
                })

        opm = config.get("pool_meta", {})
        rpm = re_parsed.get("pool_meta", {})
        for key in ("type", "ver", "mode"):
            ov = opm.get(key)
            rv = rpm.get(key)
            if ov != rv:
                diffs.append({"field": f"pool_meta.{key}", "original": ov, "roundtrip": rv, "status": "changed"})

        success = len(diffs) == 0
        stats = {
            "node_count_original": len(orig_nodes),
            "node_count_roundtrip": len(re_nodes),
            "edge_count_original": len(orig_edges),
            "edge_count_roundtrip": len(re_edges),
            "diff_count": len(diffs),
            "xml_size_bytes": len(xml_bytes) if xml_bytes else 0,
            "original_pool_name": config.get("name", ""),
            "roundtrip_pool_name": re_parsed.get("name", ""),
        }

        return {"success": success, "diffs": diffs, "stats": stats}

    # ================================================================
    # 12. GET /api/dzh/cell-types — 所有可用 cell 类型元数据
    # ================================================================
    @router.get("/cell-types")
    async def get_cell_types():
        types_list = get_all_cell_types()
        modules = _MODULES_DATA.get("modules", {})
        for t in types_list:
            cell_type_id = t.get("type_id")
            for mod in modules.values():
                if cell_type_id in mod.get("dzh_cell_types", []):
                    t["fields"] = mod.get("fields", {})
                    t["module_id"] = mod.get("id", "")
                    t["module_name"] = mod.get("name", "")
                    break
        return {"types": types_list}

    # ================================================================
    # 12b. GET /api/dzh/flow-schema — Flow 字段定义
    # ================================================================
    @router.get("/flow-schema")
    async def get_flow_schema():
        flow_schema = _MODULES_DATA.get("flow_schema", {})
        return {"success": True, "data": flow_schema}

    # ================================================================
    # 13. GET /api/dzh/markets — 市场定义
    # ================================================================
    @router.get("/markets")
    async def get_markets():
        markets = []
        for m in load_dzh_market_mappings():
            pattern = m.get("pattern", "")
            # 从锚定正则中提取可读的 dzh_key，例如 ^SH#上证A股$ -> SH#上证A股
            dzh_key = pattern.strip("^$") if pattern.startswith("^") and pattern.endswith("$") else pattern
            default_codes = m.get("default_codes") or []
            markets.append({
                "dzh_key": dzh_key,
                "code": default_codes[0] if default_codes else m.get("name", ""),
                "name": m.get("name", ""),
                "type": m.get("type", ""),
                "resolver": m.get("resolver", ""),
            })
        return {"markets": markets}

    # ================================================================
    # 14. GET /api/dzh/schedules — 计划模板（返回内置定义）
    # ================================================================
    @router.get("/schedules")
    async def get_schedules():
        return {
            "schedules": [],
            "begin_types": {
                "0": "立即开始", "1": "延迟开始", "2": "开市前",
                "3": "开市后", "4": "收市前", "5": "收市后",
                "6": "交易日", "7": "指定时间"
            },
            "transfer_modes": {
                "copy": {"bit": 12, "attr": 0x1000, "name": "复制"},
                "move": {"bit": 0, "attr": 0x1, "name": "移动"},
                "overwrite": {"bit": 13, "attr": 0x2000, "name": "覆盖"},
                "constituent": {"bit": 14, "attr": 0x4000, "name": "输出成份股"}
            },
        }

    # ================================================================
    # 15. GET /api/dzh/col-definitions — 列定义
    # ================================================================
    @router.get("/col-definitions")
    async def get_col_definitions():
        cols = [{'id': k, 'name': v['name'], 'key': v['key'], 'type': v['type']} for k, v in DZH_COL_MAP.items()]
        cols.sort(key=lambda x: (x['id'] < 0, abs(x['id'])))
        return {'success': True, 'data': cols}

    # ================================================================
    # 16. GET /api/dzh/stock-data — 股票数据查询
    # ================================================================
    @router.get("/stock-data")
    async def get_stock_data(request: Request, codes: str = '', col_list: str = '2,-1,-2,-3,7,14,8,10,17,45', mode: str = 'mock'):
        code_list = [c.strip() for c in codes.split(',') if c.strip()] if codes else []
        col_ids = []
        for c in col_list.split(','):
            c = c.strip()
            if c:
                try:
                    col_ids.append(int(c))
                except ValueError:
                    pass
        if not col_ids:
            col_ids = [2, -1, -2, -3, 7, 14, 8, 10, 17, 45]
        tq = request.app.state.tq
        if mode == 'real':
            tq = TqAdapter(mock_mode=False)
        elif mode == 'sdk':
            tq = TqAdapter(mock_mode=False)
        result = tq.get_stock_table_data(code_list, col_ids)
        return {'success': True, 'data': result['data'], 'columns': result['columns']}

    # ================================================================
    # 17. GET /api/dzh/cells/{cell_id}/stocks — Cell 内股票数据
    # ================================================================
    @router.get("/cells/{cell_id}/stocks")
    async def get_cell_stocks(cell_id: str, request: Request, mode: str = 'mock'):
        pool = _store.pool
        target = None
        for node in pool.get('nodes', []):
            if node.get('id') == cell_id:
                target = node
                break
        if target is None:
            return {'success': False, 'error': f'Cell 不存在: {cell_id}'}
        params = target.get('params', {})
        stocks = params.get('stocks', [])
        stk_info = []
        codes = []
        for s in stocks:
            label = s.get('label', '')
            if not label:
                continue
            code = label
            if label.startswith('SH') or label.startswith('SZ') or label.startswith('BJ'):
                code = label[2:] + '.' + label[:2]
            codes.append(code)
            stk_info.append({'label': code, 't': s.get('t', ''), 'p': s.get('p', '')})
        col_list_str = params.get('col_list', '2,-1,-2,-3,7,14,8,10,17,45')
        if isinstance(col_list_str, list):
            col_ids = col_list_str
        else:
            col_ids = []
            for c in str(col_list_str).split(','):
                c = c.strip()
                if c:
                    try:
                        col_ids.append(int(c))
                    except ValueError:
                        pass
        if not col_ids:
            col_ids = [2, -1, -2, -3, 7, 14, 8, 10, 17, 45]
        hold_sec = int(params.get('hold_sec', 0))
        tq = request.app.state.tq
        if mode == 'real':
            tq = TqAdapter(mock_mode=False)
        elif mode == 'sdk':
            tq = TqAdapter(mock_mode=False)
        result = tq.get_stock_table_data(codes, col_ids, stk_info=stk_info, hold_sec=hold_sec)
        return {'success': True, 'data': result['data'], 'columns': result['columns'], 'node_label': target.get('label', ''), 'stock_count': len(codes)}

    # ================================================================
    # 18. POST /api/dzh/execute-pool — 执行当前池
    # ================================================================
    @router.post("/execute-pool")
    async def execute_pool(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}

        pool_id = body.get("pool_id")
        pool_data = body.get("pool_data")

        # 1. If pool_data is provided directly, use it
        if pool_data and isinstance(pool_data, dict) and "nodes" in pool_data:
            pool = pool_data
        # 2. If pool_id is provided, try loading from storage
        elif pool_id:
            pool = None
            try:
                storage = request.app.state.storage
                pool_row = storage.get_pool(pool_id)
                pool = pool_row.get("params", {}) if pool_row else {}
            except Exception:
                pass
            # 3. Fall back to in-memory store
            if not pool or "nodes" not in pool:
                pool = _store.pool
        # 4. No pool_id and no pool_data — use in-memory store
        else:
            pool = _store.pool

        if not pool or not pool.get("nodes"):
            return {'success': False, 'error': 'No pool loaded'}

        engine = request.app.state.engine
        re = _get_replay_engine(request)
        if not re:
            re = KLineReplayEngine(engine)
            _set_replay_engine(request, re)
        result = engine.execute_pool(pool)
        if result.get('success') and result.get('data'):
            inner = result['data'].get('data', result['data'])
            for nid, info in (inner.items() if isinstance(inner, dict) else []):
                if not isinstance(info, dict) or 'stocks' not in info:
                    continue
                for node in pool.get('nodes', []):
                    if node.get('id') == nid:
                        stock_labels = info.get('stocks', [])
                        node.setdefault('params', {})['stocks'] = [
                            {'label': s, 't': '', 'p': ''} for s in stock_labels
                        ]
                        break
        return {'success': True, 'data': result}

    # ================================================================
    # 兼容旧端点
    # ================================================================
    @router.post("/convert")
    async def dzh_convert(file: UploadFile = File(...)):
        content = await file.read()
        if len(content) > MAX_UPLOAD_SIZE:
            return {"code": 1, "msg": f"文件大小超过限制 ({MAX_UPLOAD_SIZE // 1024 // 1024}MB)", "data": None}
        try:
            meta_config = parse_dzh_xml(content, file.filename)
        except Exception as e:
            return {"code": 1, "msg": f"转换失败: {e}", "data": None}
        return {"code": 0, "msg": "ok", "data": meta_config}

    @router.post("/export-file")
    async def dzh_export_file(file: UploadFile = File(...)):
        content = await file.read()
        if len(content) > MAX_UPLOAD_SIZE:
            return {"code": 1, "msg": f"文件大小超过限制 ({MAX_UPLOAD_SIZE // 1024 // 1024}MB)", "data": None}
        try:
            meta_config = parse_dzh_xml(content, file.filename)
        except Exception as e:
            return {"code": 1, "msg": f"导入失败: {e}", "data": None}
        try:
            xml_bytes = export_meta_to_dzh_xml_bytes(meta_config)
            fname = file.filename or 'pool.xml'
            ascii_fname = quote(fname)
            return Response(
                content=xml_bytes,
                media_type="application/xml",
                headers={"Content-Disposition": f"attachment; filename*=UTF-8''{ascii_fname}"}
            )
        except Exception as e:
            return {"code": 1, "msg": f"导出失败: {e}", "data": None}

    @router.post("/export-meta")
    async def dzh_export_meta(request: Request):
        try:
            body = await request.json()
        except Exception as e:
            return {"code": 1, "msg": f"请求解析失败: {e}", "data": None}
        try:
            xml_bytes = export_meta_to_dzh_xml_bytes(body)
            return Response(
                content=xml_bytes,
                media_type="application/xml",
                headers={"Content-Disposition": "attachment; filename=pool.xml"}
            )
        except Exception as e:
            return {"code": 1, "msg": f"导出失败: {e}", "data": None}

    @router.get("/test-import")
    async def dzh_test_import(filename: str = "超赢1号.xml"):
        base = str(_this_dir.parent)
        try:
            path = safe_path_join(base, filename)
        except ValueError as e:
            return {"code": 1, "msg": f"非法文件名: {e}", "data": None}
        if not os.path.exists(path):
            path = _find_xml_file_fuzzy(base, filename)
        if not path:
            return {"code": 1, "msg": f"文件不存在: {os.path.join(base, filename)}", "data": None}
        with open(path, "rb") as f:
            content = f.read()
        try:
            meta_config = parse_dzh_xml(content, filename)
        except Exception as e:
            return {"code": 1, "msg": f"XML解析失败: {e}", "data": None}
        bus_nodes = [n for n in meta_config["nodes"] if n["type"] not in ("text_label", "flow_arrow")]
        result = {
            "pool_name": meta_config["name"],
            "node_count": len(bus_nodes),
            "edge_count": len(meta_config["edges"]),
            "config": meta_config,
        }
        return {"code": 0, "msg": "ok", "data": result}

    @router.post("/pool/run")
    async def run_pool(request: Request):
        try:
            body = await request.json()
        except Exception as e:
            return {"code": 1, "msg": f"请求解析失败: {e}", "data": None}
        config = body.get('config') or body
        if not config or 'nodes' not in config:
            return {"code": 1, "msg": "配置无效，缺少nodes", "data": None}
        from ..converters.dzh import DZHPoolExecutor
        executor = DZHPoolExecutor(config)
        result = executor.execute_once()
        return {
            "code": 0,
            "msg": "ok",
            "data": {
                "output_count": result['output_count'],
                "output_stocks": result['output_stocks'],
                "events": result['events'],
                "node_states": result['node_states']
            }
        }

    @router.post("/pool/start")
    async def start_pool(request: Request):
        try:
            body = await request.json()
        except Exception as e:
            return {"code": 1, "msg": f"请求解析失败: {e}", "data": None}
        config = body.get('config') or body
        if not config or 'nodes' not in config:
            return {"code": 1, "msg": "配置无效，缺少nodes", "data": None}
        from ..converters.dzh import DZHPoolExecutor
        executor = DZHPoolExecutor(config)
        executor._init_mock_stocks()
        executor.start()
        pool_id = body.get('pool_id', f"pool_{id(executor)}")
        if not hasattr(request.app.state, '_dzh_executors'):
            request.app.state._dzh_executors = {}
        request.app.state._dzh_executors[pool_id] = executor
        return {
            "code": 0,
            "msg": "执行器已启动",
            "data": {
                "pool_id": pool_id,
                "status": "running",
                "node_count": len(config.get('nodes', [])),
                "edge_count": len(config.get('edges', []))
            }
        }

    @router.post("/pool/stop")
    async def stop_pool(request: Request):
        try:
            body = await request.json()
        except Exception as e:
            return {"code": 1, "msg": f"请求解析失败: {e}", "data": None}
        pool_id = body.get('pool_id')
        executors = getattr(request.app.state, '_dzh_executors', {})
        executor = executors.get(pool_id)
        if not executor:
            return {"code": 1, "msg": f"执行器不存在: {pool_id}", "data": None}
        executor.stop()
        result = {
            "output_stocks": executor.get_output_stocks(50),
            "node_states": executor.get_node_states(),
            "events": executor.get_events(100),
            "total_events": len(executor._events)
        }
        del executors[pool_id]
        return {"code": 0, "msg": "执行器已停止", "data": result}

    @router.get("/pool/status/{pool_id}")
    async def get_pool_status(pool_id: str, request: Request):
        executors = getattr(request.app.state, '_dzh_executors', {})
        executor = executors.get(pool_id)
        if not executor:
            return {"code": 1, "msg": f"执行器不存在: {pool_id}", "data": None}
        return {
            "code": 0,
            "msg": "ok",
            "data": {
                "pool_id": pool_id,
                "running": executor.running,
                "node_states": executor.get_node_states(),
                "recent_events": executor.get_events(10),
                "total_events": len(executor._events)
            }
        }

    @router.get("/formula-list")
    async def get_formula_list():
        import json as _json
        from pathlib import Path as _Path
        dispatch_path = _Path(__file__).resolve().parent.parent / 'config' / 'dispatch.json'
        if dispatch_path.exists():
            with open(dispatch_path, 'r', encoding='utf-8') as f:
                dispatch_data = _json.load(f)
            rules = dispatch_data.get('dispatch_rules', {})
            entries = rules.values() if isinstance(rules, dict) else (rules if isinstance(rules, list) else [])
            formulas = []
            for entry in entries:
                formulas.append({
                    'condition_type': entry.get('condition_type', ''),
                    'name': entry.get('name', ''),
                    'required_fields': entry.get('required_fields', []),
                    'extra': entry.get('extra', {}),
                })
            return {'success': True, 'data': formulas}
        return {'success': True, 'data': []}

    @router.post("/validate-formula")
    async def validate_formula(request: Request):
        body = await request.json()
        formula = body.get('formula', '')
        # inditype 保留向后兼容（请求体仍可传入），FormulaRouter 不区分 inditype
        # FormulaRouter.eval 需要标的与周期，允许调用方提供，默认样本股
        symbol = body.get('symbol', '600000.SH')
        period = body.get('period', '1d')
        engine = request.app.state.engine
        formula_router = getattr(engine, 'formula_router', None)
        if not formula_router:
            logger.warning("formula_router not available; formula evaluation skipped")
            return {'success': False, 'error': 'formula_router not available', 'result': None}
        if not hasattr(formula_router, 'eval'):
            logger.warning("formula_router.eval not available; formula evaluation skipped")
            return {'success': False, 'error': 'formula_router.eval not available', 'result': None}
        try:
            result = await formula_router.eval(formula, symbol, period=period)
            return {'success': True, 'error': None, 'result': result}
        except Exception as e:
            return {'success': False, 'error': str(e), 'result': None}

    # ================================================================
    # 25. POST /api/dzh/replay/load — 加载K线数据
    # ================================================================
    @router.post("/replay/load")
    async def replay_load(request: Request):
        body = await request.json()
        pool_model = body.get("pool_model")
        pool_id = body.get("pool_id")
        base_period = body.get("base_period", "day")
        start_date = body.get("start_date", "2024-01-01")
        end_date = body.get("end_date", "2024-12-31")

        if not pool_model and pool_id:
            # Try database storage first
            storage = getattr(request.app.state, 'storage', None)
            if storage:
                pool_row = storage.get_pool(pool_id)
                pool_model = pool_row.get("params") or {} if pool_row else None
            # Try loading from TDX pool XML files
            if not pool_model:
                import os
                tdxpool_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'tdxpool')
                xml_path = os.path.join(tdxpool_dir, f"{pool_id}.xml")
                if os.path.isfile(xml_path):
                    from ..converters.tdx import parse_tdx_xml, tdx_to_internal
                    tdx_pool = parse_tdx_xml(xml_path)
                    pool_model = tdx_to_internal(tdx_pool, filename=f"{pool_id}.xml")
            if not pool_model:
                return {"success": False, "error": f"池不存在: {pool_id}"}

        if not pool_model:
            pool_model = _store.pool

        if not pool_model or not pool_model.get("nodes"):
            return {"success": False, "error": "无有效的池配置"}

        re = _get_replay_engine(request)
        engine = request.app.state.engine
        if not re and engine:
            re = KLineReplayEngine(engine)
            _set_replay_engine(request, re)
        if not re:
            return {"success": False, "error": "引擎未初始化"}

        result = re.load_kline_data(pool_model, base_period, [start_date, end_date])
        return {"success": True, "data": result}

    # ================================================================
    # 26. POST /api/dzh/replay/start — 启动回放引擎（前端兼容接口）
    # ================================================================
    @router.post("/replay/start")
    async def replay_start(request: Request):
        try:
            body = await request.json()
        except Exception as e:
            return {"code": -1, "error": f"请求解析失败: {e}", "data": None}
        try:
            pool_id = body.get("pool_id")
            base_period = body.get("period", "day")
            start_date = body.get("start_date", "2024-01-01")
            end_date = body.get("end_date", "2024-12-31")
            filename = body.get("filename")
            pool_data = body.get("pool_data")

            # Load pool model
            pool_model = None
            if pool_id:
                storage = getattr(request.app.state, 'storage', None)
                if storage:
                    pool_row = storage.get_pool(pool_id)
                    if pool_row:
                        pool_model = pool_row.get("params") or {}
                        # 如果params中没有nodes，尝试从xml_source加载
                        if not pool_model.get("nodes") and pool_row.get("xml_source"):
                            import os as _os
                            xml_path = pool_row["xml_source"]
                            if _os.path.isfile(xml_path):
                                from ..converters.tdx import parse_tdx_xml, tdx_to_internal
                                tdx_pool = parse_tdx_xml(xml_path)
                                internal = tdx_to_internal(tdx_pool, filename=_os.path.basename(xml_path))
                                pool_model = {
                                    "nodes": [c.to_dict() if hasattr(c, 'to_dict') else c for c in internal.cells],
                                    "edges": [f.to_dict() if hasattr(f, 'to_dict') else f for f in internal.flows],
                                    "pool_meta": {"type": "tdx", "ver": internal.ver, "mode": internal.mode,
                                                  "nextid": internal.nextid, "backcolor": internal.backcolor},
                                }
            if not pool_model:
                pool_model = _store.pool

            # Fallback: load from filename or pool_data passed by frontend
            if (not pool_model or not pool_model.get("nodes")) and filename:
                import os as _os
                tdxpool_dir = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), 'tdxpool')
                try:
                    xml_path = safe_path_join(tdxpool_dir, filename)
                except ValueError as e:
                    return {"code": -1, "error": str(e), "data": None}
                if _os.path.isfile(xml_path):
                    from ..converters.tdx import parse_tdx_xml, tdx_to_internal
                    tdx_pool = parse_tdx_xml(xml_path)
                    internal = tdx_to_internal(tdx_pool, filename=filename)
                    # Convert PoolMetaModel to dict with nodes/edges keys
                    pool_model = {
                        "nodes": [c.to_dict() if hasattr(c, 'to_dict') else c for c in internal.cells],
                        "edges": [f.to_dict() if hasattr(f, 'to_dict') else f for f in internal.flows],
                        "pool_meta": {"type": "tdx", "ver": internal.ver, "mode": internal.mode,
                                      "nextid": internal.nextid, "backcolor": internal.backcolor},
                    }
            if (not pool_model or not pool_model.get("nodes")) and pool_data and pool_data.get("nodes"):
                pool_model = pool_data

            if not pool_model or not pool_model.get("nodes"):
                return {"code": -1, "error": "无有效的池配置", "data": None}

            engine = request.app.state.engine
            re = _get_replay_engine(request)
            if not re and engine:
                re = KLineReplayEngine(engine)
                _set_replay_engine(request, re)
            if not re:
                return {"code": -1, "error": "引擎未初始化", "data": None}

            # Load K-line data if not already loaded
            load_result = re.load_kline_data(pool_model, base_period, [start_date, end_date])

            re.play()
            pg = re.get_progress() or {}
            return {
                "code": 0,
                "data": {
                    "session_id": "replay_session",
                    "total_bars": pg.get("total_bars", 0),
                    "speed": 1,
                    "progress_summary": {
                        "current_time": pg.get("current_time", ""),
                        "current_index": pg.get("current_index", -1),
                        "total_bars": pg.get("total_bars", 0),
                        "progress": pg.get("progress", 0),
                        "playing": False,
                        "paused": True,
                    }
                }
            }
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"code": -1, "error": f"回放启动失败: {e}", "data": None}

    # ================================================================
    # 27. POST /api/dzh/replay/pause — 暂停回放
    # ================================================================
    @router.post("/replay/pause")
    async def replay_pause(request: Request):
        re = _get_replay_engine(request)
        if not re:
            return {"success": False, "error": "回放引擎未初始化"}
        re.pause()
        return {"success": True, "snapshot": re.get_current_snapshot()}

    # ================================================================
    # 28. POST /api/dzh/replay/step — 步进一根K线
    # ================================================================
    @router.post("/replay/step")
    async def replay_step(request: Request):
        re = _get_replay_engine(request)
        if not re:
            return {"success": False, "error": "回放引擎未初始化"}
        re.step()
        return {"success": True, "snapshot": re.get_current_snapshot()}

    # ================================================================
    # 29. POST /api/dzh/replay/speed — 设置回放速度
    # ================================================================
    @router.post("/replay/speed")
    async def replay_speed(request: Request):
        body = await request.json()
        speed = body.get("speed", 1)
        re = _get_replay_engine(request)
        if not re:
            return {"success": False, "error": "回放引擎未初始化"}
        re.set_speed(speed)
        return {"success": True, "speed": speed}

    # ================================================================
    # 30. GET /api/dzh/replay/snapshot — 获取当前回放状态快照
    # ================================================================
    @router.get("/replay/snapshot")
    async def replay_snapshot(request: Request):
        re = _get_replay_engine(request)
        if not re:
            return {"success": False, "error": "回放引擎未初始化"}
        return {"success": True, "data": re.get_current_snapshot()}

    # ================================================================
    # 0. GET /api/dzh/modules — 获取模块定义 schema
    # ================================================================
    @router.get("/modules")
    async def get_modules_schema():
        modules_path = _config_dir / "modules.json"
        if modules_path.exists():
            with open(modules_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"modules": {}, "flow_schema": {}}

    # ================================================================
    # 0b. GET /api/dzh/load-demo — 从 dzhpool 目录加载演示股票池
    # ================================================================
    @router.get("/load-demo")
    async def dzh_load_demo(name: str = ""):
        if not name:
            return {"success": False, "error": "缺少 name 参数"}

        dzhpool_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'dzhpool')

        # 1. 精确匹配
        exact_path = os.path.join(dzhpool_dir, name + '.xml')
        if os.path.isfile(exact_path):
            xml_path = exact_path
        else:
            # 2. 去除前导数字后的模糊匹配
            xml_path = None
            try:
                for f in os.listdir(dzhpool_dir):
                    if f.lower().endswith('.xml'):
                        f_base = f.rsplit('.', 1)[0]
                        # 去除前导数字
                        stripped = f_base.lstrip('0123456789')
                        if stripped == name:
                            xml_path = os.path.join(dzhpool_dir, f)
                            break
            except OSError:
                pass

        if not xml_path or not os.path.isfile(xml_path):
            return {"success": False, "error": f"找不到文件: {name}.xml"}

        # DZH XML 通常是 GBK 编码，回退到 UTF-8
        raw = open(xml_path, 'rb').read()
        content = None
        for enc in ('gbk', 'gb2312', 'utf-8', 'latin-1'):
            try:
                content = raw.decode(enc).encode('utf-8')
                break
            except (UnicodeDecodeError, UnicodeEncodeError):
                continue

        if content is None:
            return {"success": False, "error": "文件编码无法识别"}

        try:
            from ..converters.dzh import is_tdx_format
            if is_tdx_format(content):
                parsed = _import_as_tdx(content, os.path.basename(xml_path))
            else:
                parsed = parse_dzh_xml(content, filename=os.path.basename(xml_path))
        except Exception as e:
            return {"success": False, "error": f"XML解析失败: {e}"}

        _store.pool = parsed

        return {"success": True, "data": parsed}

    # ================================================================
    # 31. GET /api/dzh/replay/progress — 获取回放进度
    # ================================================================
    @router.get("/replay/progress")
    async def replay_progress(request: Request):
        re = _get_replay_engine(request)
        if not re:
            return {"success": False, "error": "回放引擎未初始化"}
        return {"success": True, "data": re.get_progress()}

    # ================================================================
    # 32. POST /api/dzh/replay/control — 统一回放控制（前端兼容接口）
    # ================================================================
    @router.post("/replay/control")
    async def replay_control(request: Request):
        re = _get_replay_engine(request)
        if not re:
            return {"code": -1, "error": "回放引擎未初始化", "data": None}
        body = await request.json()
        command = body.get("command", "")
        speed = body.get("speed", 1)
        progress = body.get("progress", None)

        try:
            if command == "play":
                re.play()
            elif command == "pause":
                re.pause()
            elif command == "step":
                re.step()
            elif command == "speed":
                re.set_speed(speed)
            elif command == "seek":
                if progress is not None:
                    re.seek(progress)
            else:
                return {"code": -1, "error": f"未知命令: {command}", "data": None}

            pg = re.get_progress() or {}
            snapshot = re.get_current_snapshot() or {}
            return {
                "code": 0,
                "data": {
                    "progress": {
                        "current_time": pg.get("current_time", ""),
                        "current_index": pg.get("current_index", -1),
                        "total_bars": pg.get("total_bars", 0),
                        "progress": pg.get("progress", 0),
                        "playing": pg.get("playing", False),
                        "paused": pg.get("paused", True),
                        "speed": pg.get("speed", 1),
                    },
                    "snapshot": snapshot,
                }
            }
        except Exception as e:
            return {"code": -1, "error": str(e), "data": None}

    # ================================================================
    # 33. GET /api/dzh/replay/status — 获取回放状态（前端兼容接口）
    # ================================================================
    @router.get("/replay/status")
    async def replay_status(request: Request):
        re = _get_replay_engine(request)
        if not re:
            return {"code": -1, "error": "回放引擎未初始化", "data": None}
        pg = re.get_progress() or {}
        snapshot = re.get_current_snapshot() or {}
        return {
            "code": 0,
            "data": {
                "progress": {
                    "current_time": pg.get("current_time", ""),
                    "current_index": pg.get("current_index", -1),
                    "total_bars": pg.get("total_bars", 0),
                    "progress": pg.get("progress", 0),
                    "playing": pg.get("playing", False),
                    "paused": pg.get("paused", True),
                    "speed": pg.get("speed", 1),
                },
                "snapshot": snapshot,
            }
        }

    # ================================================================
    # 34. GET /api/dzh/color-info — DZH 颜色值查询
    # ================================================================
    @router.get("/color-info")
    async def get_dzh_color_info(value: str = ''):
        from user.stock_pool_system.core.dzh_constant import decode_dzh_color

        if not value or not value.strip():
            return {
                "success": False,
                "error": "missing_parameter",
                "message": "缺少必需参数: value",
                "input": value
            }

        try:
            result = decode_dzh_color(value)
            if result.get('type') == 'invalid':
                return {
                    "success": False,
                    "error": "invalid_color_value",
                    "message": f"无法解析的颜色值: '{value}'。支持整数、字符串形式的十进制/十六进制颜色值。",
                    "input": value
                }

            return {
                "success": True,
                "input": str(value),
                "parsed": result
            }

        except Exception as e:
            return {
                "success": False,
                "error": "internal_error",
                "message": str(e),
                "input": value
            }

    return router


# ══════════════════════════════════════════════════════════════════════
#  Part 2: 来自 json_api.py — JSON 导入导出 API
# ══════════════════════════════════════════════════════════════════════
# 注：原 json_api.py 通过 `from ..converters.json_xml import ...`
# 导入；合并后该导入已在文件顶部完成。


def create_json_router() -> APIRouter:
    router = APIRouter(tags=["JSON导入导出"])

    # ================================================================
    # 1. POST /import — 导入JSON（文件上传 或 JSON体）
    # ================================================================
    @router.post("/import")
    async def json_import(request: Request):
        json_content = None

        form = await request.form()
        uploaded_file = form.get("file")
        if uploaded_file and hasattr(uploaded_file, "filename") and uploaded_file.filename:
            file_bytes = await uploaded_file.read()
            if len(file_bytes) > MAX_UPLOAD_SIZE:
                return {"success": False, "error": f"文件大小超过限制 ({MAX_UPLOAD_SIZE // 1024 // 1024}MB)"}
            json_content = file_bytes.decode("utf-8")
        else:
            try:
                body = await request.json()
            except Exception:
                body = None

            if body is not None:
                if isinstance(body, str):
                    json_content = body
                elif isinstance(body, dict):
                    json_content = json.dumps(body, ensure_ascii=False)

        if json_content is None:
            return {"success": False, "error": "请上传文件或提供 JSON 内容"}

        try:
            pool_config = import_pool_from_json(json_content=json_content)
        except Exception as e:
            return {"success": False, "error": f"JSON导入失败: {e}"}

        return {"success": True, "data": pool_config}

    # ================================================================
    # 2. POST /export — 导出为JSON（JSON体输入）
    # ================================================================
    @router.post("/export")
    async def json_export(request: Request):
        try:
            body = await request.json()
        except Exception as e:
            return {"success": False, "error": f"请求解析失败: {e}"}

        config = body.get("config") or body
        if not config or "nodes" not in config:
            return {"success": False, "error": "配置无效，缺少 nodes"}

        try:
            json_str = export_pool_to_json(pool_data=config)
            name = config.get("name", "pool")
            ascii_fname = quote(f"{name}.json")
            return Response(
                content=json_str,
                media_type="application/json",
                headers={
                    "Content-Disposition": f"attachment; filename*=UTF-8''{ascii_fname}"
                },
            )
        except Exception as e:
            return {"success": False, "error": f"导出失败: {e}"}

    return router

