"""meta_core.api — 合并 HTTP 端点模块（SubTask 29.4）。

合并自 api/ 包 3 文件：
- api/__init__.py：导出 __all__ + sys.modules 向后兼容注册
- api/pool_api.py：池配置管理 API（合并自 config_api / table_api / meta_api）
- api/system_api.py：系统 API（合并自 run_api / formula_api / import_api / dzh_api / json_api）

合并后保持原导出符号不变：
- 路由对象：router / table_router / table_config_router
- 工厂函数：create_meta_router / create_execution_router / create_replay_router
           create_sim_router / create_dzh_router / create_json_router / create_formula_router
- 初始化/注入：init / set_engine
- 辅助函数：_enrich_tdx_node_data / _generate_mock_bar_data
- 向后兼容别名：config_api_router=router / config_api_init=init / set_table_engine=set_engine
"""

# ══════════════════════════════════════════════════════════════════════
#  通用导入（合并 pool_api + system_api 顶部，去重）
# ══════════════════════════════════════════════════════════════════════
import asyncio
import base64
import json
import logging
import os
import random
import re
import struct
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote
from uuid import uuid4

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import Response
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# Task 16: 事件驱动并行通道——订阅 EventBus 事件推送给前端
try:
    from core.event_bus import (
        ConfigChanged,
        EventBus,
        EventLogged,
        ModeChanged,
        PoolLoaded,
        ReplayStarted,
        SimulationStep,
        SnapshotUpdated,
        StatisticsUpdated,
    )
except ImportError:
    from ..core.event_bus import (
        ConfigChanged,
        EventBus,
        EventLogged,
        ModeChanged,
        PoolLoaded,
        ReplayStarted,
        SimulationStep,
        SnapshotUpdated,
        StatisticsUpdated,
    )

# === Run ===
try:
    from services.tq_adapter import DZH_COL_MAP, TqAdapter
    from services.data import (
        CandidatePoolResolver,
        CandidatePoolRefreshManager,
        DataSourceContract,
        DataSourceContractError,
        DataSourceMockExplicitOnlyError,
        DataSourceUnavailableErrorContract,
        get_default_contract,
    )
    from core.runtime_mode_module import KLineReplayEngine, RuntimeSimulator
except ImportError:
    from ..services.tq_adapter import DZH_COL_MAP, TqAdapter
    from ..services.data import (
        CandidatePoolResolver,
        CandidatePoolRefreshManager,
        DataSourceContract,
        DataSourceContractError,
        DataSourceMockExplicitOnlyError,
        DataSourceUnavailableErrorContract,
        get_default_contract,
    )
    from ..core.runtime_mode_module import KLineReplayEngine, RuntimeSimulator


# === Import ===
try:
    from converters import (
        DZHPoolExecutor,
        build_attrtext_from_selections,
        decode_action,
        decode_reload_mode,
        encode_reload_mode,
        export_pool_to_json,
        get_all_cell_types,
        get_cell_type_info,
        import_pool_from_json,
        load_dzh_market_mappings,
        parse_attrtext_selections,
        tdx_to_internal,
        _decode_flow_attr,
        _decode_type200_attr,
        _decode_type201_attr,
        _detect_topology_mode,
    )
    from core.import_export_module import _call_converter
    from services.storage import DatabaseSyncService, safe_path_join
except ImportError:
    from ..converters import (
        DZHPoolExecutor,
        build_attrtext_from_selections,
        decode_action,
        decode_reload_mode,
        encode_reload_mode,
        export_pool_to_json,
        get_all_cell_types,
        get_cell_type_info,
        import_pool_from_json,
        load_dzh_market_mappings,
        parse_attrtext_selections,
        tdx_to_internal,
        _decode_flow_attr,
        _decode_type200_attr,
        _decode_type201_attr,
        _detect_topology_mode,
    )
    from ..core.import_export_module import _call_converter


# 延迟导入（保留在原位置——在函数内按需导入，避免循环依赖）
# pool_api 内部仍含以下延迟 import（已转换为绝对路径）：
#   - from core.table_engine import ConfigStore, PanelGenerator, DataBinder, PropertyOwnershipManager, RuleEngine
#   - from native.validators import ConfigIntegrityValidator
#   - from native.builtins import _HANDLERS
#   - from services.providers import LocalFileProvider
# system_api 内部仍含延迟 import：
#   - from core.schemas import TdxFuncModel, TdxPsattModel, TdxSpinfoModel
#   - from services.providers import decode_formula as _decode_formula


# ══════════════════════════════════════════════════════════════════════
#  __all__（合并自 api/__init__.py）
# ══════════════════════════════════════════════════════════════════════
__all__ = [
    # 池配置 API（pool_api）
    "router",
    "init",
    "table_router",
    "table_config_router",
    "set_engine",
    "create_meta_router",
    # 系统 API（system_api）
    "create_execution_router",
    "create_replay_router",
    "create_sim_router",
    "create_dzh_router",
    "create_json_router",
    "create_formula_router",
    "_enrich_tdx_node_data",
    "_generate_mock_bar_data",
    # 向后兼容别名（原 __init__.py 导出）
    "config_api_router",
    "config_api_init",
    "set_table_engine",
]


# ══════════════════════════════════════════════════════════════════════
#  Part 1: 来自 pool_api.py（合并自 config_api / table_api / meta_api）
# ══════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════
#  Part 1: 来自 config_api.py — 配置管理 API
# ══════════════════════════════════════════════════════════════════════

# 引擎实例（由app.py注入）
_config_store = None
_hot_reload_manager = None
_schema_validator = None


def require_config_store():
    """FastAPI 依赖：确保 _config_store 已注入，未初始化则返回 500。"""
    if _config_store is None:
        raise HTTPException(status_code=500, detail="引擎未初始化")
    return _config_store


router = APIRouter(prefix="/api/config", tags=["config"],
                   dependencies=[Depends(require_config_store)])

# 合法表名模式：字母/下划线开头，仅含字母、数字、下划线（防止路径遍历）
TABLE_NAME_PATTERN = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')


def init(config_store, hot_reload_manager=None, schema_validator=None):
    """初始化配置管理API"""
    global _config_store, _hot_reload_manager, _schema_validator
    _config_store = config_store
    _hot_reload_manager = hot_reload_manager
    _schema_validator = schema_validator


# ─── 配置表读写 ──────────────────────────────────────────────

@router.get("/tables")
def list_tables():
    """列出所有已加载的配置表"""
    tables = {}
    for name in _config_store.table_names:
        data = _config_store.get(name, {})
        collection_key = _config_store._get_collection_key(name, data)
        entry_count = 0
        if collection_key and isinstance(data, dict):
            collection = data.get(collection_key)
            if isinstance(collection, dict):
                entry_count = len(collection)
            elif isinstance(collection, list):
                entry_count = len(collection)
        tables[name] = {
            "name": name,
            "version": data.get("version", ""),
            "entry_count": entry_count,
            "load_time": _config_store.load_times.get(name, 0),
            "hash": _config_store._hashes.get(name, "")[:8],
        }
    return {"tables": tables, "total": len(tables)}


@router.get("/categories")
def get_categories():
    """返回分类树，含 schema 覆盖统计与一致性信息。"""
    return _config_store.get_categories()


@router.get("/status")
def config_status():
    """返回配置中心状态：已加载表、分类一致性、schema 覆盖率与锁状态。"""
    return {
        "tables_loaded": len(_config_store.table_names),
        "table_names": _config_store.table_names,
        "category_consistency": _config_store._category_consistency,
        "schema_coverage": _config_store.get_schema_coverage(),
        "locks": _config_store.get_lock_status(),
    }


@router.get("/tables/{table_name}")
def get_table(table_name: str):
    """获取指定配置表的完整内容"""
    data = _config_store.get(table_name)
    if data is None:
        raise HTTPException(404, f"配置表 {table_name} 不存在")
    return data


class TableUpdateRequest(BaseModel):
    content: Dict[str, Any]


@router.put("/tables/{table_name}")
def update_table(table_name: str, req: TableUpdateRequest, request: Request):
    """更新指定配置表（写入文件 + 触发热加载）"""

    # 锁定检查（Task 13.3）
    if _config_store.is_table_locked(table_name):
        raise HTTPException(409, detail={"locked": True, "table": table_name})

    config_path = _config_store._config_dir / f"{table_name}.json"
    if not config_path.exists():
        raise HTTPException(404, f"配置表文件 {table_name}.json 不存在")

    try:
        # 写入文件
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(req.content, f, ensure_ascii=False, indent=2)

        # 触发热加载
        if _hot_reload_manager:
            changed = _hot_reload_manager.check_and_reload()
        else:
            changed = _config_store.check_hot_reload()

        # Task 16: 事件驱动并行通道——发布 ConfigChanged 事件触发 Execution 模块重建 CompiledSchedule
        try:
            bus: "EventBus" = request.app.state.bus
            changed_tables = changed if isinstance(changed, list) else [table_name]
            bus.publish(ConfigChanged(changed_tables=changed_tables))
        except Exception as _bus_ex:
            logger.warning("update_table 发布 ConfigChanged 事件失败: %s", _bus_ex)

        return {"ok": True, "table": table_name, "changed": changed}
    except Exception as e:
        raise HTTPException(500, f"更新配置表失败: {e}")


# ─── 校验 ────────────────────────────────────────────────────

@router.post("/validate")
def validate_all():
    """校验所有配置表"""
    from native.validators import ConfigIntegrityValidator
    validator = ConfigIntegrityValidator(str(_config_store._config_dir))
    return validator.validate_all()


@router.post("/validate/{table_name}")
def validate_table(table_name: str):
    """校验指定配置表"""
    data = _config_store.get(table_name)
    if data is None:
        raise HTTPException(404, f"配置表 {table_name} 不存在")

    report = _config_store.validate_table_with_report(table_name, data)
    return {"table": table_name, **report}


@router.post("/validate-all")
def validate_all_tables():
    """校验所有配置表并返回聚合报告（Task 7.1）。

    遍历所有已加载表（跳过系统元数据表），调用
    ``validate_table_with_report`` 生成逐表报告，并汇总通过/失败计数。
    """

    report: Dict[str, Any] = {}
    for table_name, data in _config_store._tables.items():
        if table_name in ("table_schemas", "table_categories"):
            continue
        try:
            report[table_name] = _config_store.validate_table_with_report(table_name, data)
        except Exception as e:
            logger.error("校验表 %s 异常: %s", table_name, e)
            report[table_name] = {"valid": False, "errors": [f"校验异常: {e}"], "schema": "none"}

    total = len(report)
    passed = sum(1 for r in report.values() if r.get("valid"))
    failed = total - passed
    return {
        "summary": {"total": total, "passed": passed, "failed": failed},
        "tables": report,
    }


# ─── 批量导出/导入（Task 8） ─────────────────────────────────

@router.get("/export")
def export_all():
    """导出全部配置表为 JSON envelope（Task 8.1）。

    返回 ``{version, exported_at, tables: {name: data}}``，跳过系统元数据表。
    """

    tables: Dict[str, Any] = {}
    for name, data in _config_store._tables.items():
        if name in ("table_schemas", "table_categories"):
            continue
        tables[name] = data

    return {
        "version": "1.0",
        "exported_at": datetime.now().isoformat(),
        "tables": tables,
    }


@router.post("/import")
def import_all(payload: dict):
    """导入配置表 JSON envelope，原子写入（Task 8.2）。

    先对 envelope 中所有表执行校验与锁检查，任一失败返回 422 且不写入；
    全部通过后逐表写盘并触发 reload。
    """

    tables = payload.get("tables", {}) if isinstance(payload, dict) else {}
    if not tables:
        raise HTTPException(400, "No tables in envelope")

    # Phase 1: 校验全部表（含锁检查）
    errors: Dict[str, Any] = {}
    for table_name, data in tables.items():
        if not TABLE_NAME_PATTERN.match(table_name):
            errors[table_name] = "Invalid table name"
            continue
        if table_name in ("table_schemas", "table_categories"):
            errors[table_name] = "Cannot import system metadata tables"
            continue
        if _config_store.is_table_locked(table_name):
            errors[table_name] = "Table is locked"
            continue
        try:
            result = _config_store.validate_table_with_report(table_name, data)
        except Exception as e:
            errors[table_name] = [f"校验异常: {e}"]
            continue
        if not result.get("valid"):
            errors[table_name] = result.get("errors", [])

    if errors:
        raise HTTPException(422, detail={"message": "Validation failed", "errors": errors})

    # Phase 2: 原子写入全部表
    written: List[str] = []
    try:
        for table_name, data in tables.items():
            if table_name in ("table_schemas", "table_categories"):
                continue
            table_path = _config_store._config_dir / f"{table_name}.json"
            # 防御性校验：解析后路径必须在配置目录内（防止路径遍历）
            try:
                table_path.resolve().relative_to(_config_store._config_dir.resolve())
            except ValueError:
                errors[table_name] = "Path traversal detected"
                continue
            with open(table_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            written.append(table_name)

        # 重新加载以应用变更
        _config_store.load_all()

        return {"imported": True, "tables_written": len(written), "table_names": written}
    except Exception as ex:
        # 文件已写入无法真正回滚，仅记录错误
        logger.error("Import failed after writing some tables: %s", ex)
        raise HTTPException(500, detail={"message": "Import partially failed",
                                         "written": written, "error": str(ex)})


# ─── 表锁定（Task 13） ───────────────────────────────────────

@router.post("/lock/{table_name}")
def lock_table(table_name: str, reason: str = ""):
    """锁定一张表以阻止编辑。"""
    if _config_store.is_table_locked(table_name):
        return {"locked": True, "message": "Already locked",
                "lock_info": _config_store._locks.get(table_name)}
    lock_info = _config_store.lock_table(table_name, reason)
    return {"locked": True, "lock_info": lock_info}


@router.delete("/lock/{table_name}")
def unlock_table(table_name: str):
    """解锁一张表。"""
    was_locked = _config_store.unlock_table(table_name)
    return {"locked": False, "was_locked": was_locked}


# ─── 热加载控制 ──────────────────────────────────────────────

@router.post("/reload")
def trigger_reload():
    """手动触发热加载"""

    if _hot_reload_manager:
        changed = _hot_reload_manager.check_and_reload()
    else:
        changed = _config_store.check_hot_reload()

    return {"changed": changed, "count": len(changed)}


@router.get("/hot-reload/status")
def hot_reload_status():
    """获取热加载状态"""
    if _hot_reload_manager:
        return _hot_reload_manager.get_status()
    return {"watchdog_active": False, "polling_active": False, "ws_connections": 0}


@router.post("/hot-reload/start-watchdog")
def start_watchdog():
    """启动 watchdog 文件监控"""
    if not _hot_reload_manager:
        raise HTTPException(500, "热加载管理器未初始化")
    success = _hot_reload_manager.start_watchdog()
    return {"ok": success, "mode": "watchdog" if success else "polling_fallback"}


@router.post("/hot-reload/stop-watchdog")
def stop_watchdog():
    """停止 watchdog 文件监控"""
    if not _hot_reload_manager:
        raise HTTPException(500, "热加载管理器未初始化")
    _hot_reload_manager.stop_watchdog()
    return {"ok": True}


# ─── 变更历史 ────────────────────────────────────────────────

@router.get("/history/{table_name}")
def get_config_history(table_name: str, limit: int = 20):
    """获取指定配置表的变更历史"""
    storage = _config_store._storage
    if not storage:
        return {"history": [], "total": 0}
    try:
        history = _config_store._storage.get_config_versions(table_name, limit=limit)
        return {"history": history, "total": len(history)}
    except Exception as e:
        return {"history": [], "error": str(e)}


@router.post("/rollback/{version_id}")
def rollback_config(version_id: str):
    """回滚配置到指定版本"""
    if _hot_reload_manager:
        success = _hot_reload_manager.rollback(version_id)
    else:
        success = _config_store.rollback_config(version_id)
    return {"ok": success, "version_id": version_id}


@router.get("/diff/{table_name}")
def diff_versions(table_name: str, from_version: str = None, to_version: str = None):
    """返回两个版本之间的结构化 diff（Task 11.1）。

    ``from_version``/``to_version`` 为版本 ID；为 ``None``/``"current"`` 时取当前表数据。
    返回 ``{table, from_version, to_version, diff: {added, removed, modified}}``。
    """

    from_data = _get_version_data(_config_store, table_name, from_version)
    to_data = _get_version_data(_config_store, table_name, to_version)

    if from_data is None:
        raise HTTPException(404, f"Version {from_version} not found for {table_name}")
    if to_data is None:
        raise HTTPException(404, f"Version {to_version} not found for {table_name}")

    diff = _compute_diff(from_data, to_data)
    return {
        "table": table_name,
        "from_version": from_version,
        "to_version": to_version,
        "diff": diff,
    }


def _get_version_data(store, table_name, version_id):
    """加载指定版本（version_id）的表数据。

    - ``version_id`` 为 ``None``/``""``/``"current"`` 时返回当前内存中的表数据；
    - 否则从 ``config_version`` 表查询该版本，校验 ``table_name`` 一致后返回
      ``new_content`` 解析结果（即该版本变更后的整表快照）。
    - 未找到或异常时返回 ``None``。
    """
    if version_id in (None, "", "current"):
        return store._tables.get(table_name)

    storage = getattr(store, "_storage", None)
    if storage is None:
        return None

    try:
        with storage._conn() as conn:
            row = conn.execute(
                "SELECT * FROM config_version WHERE version_id=?", (version_id,)
            ).fetchone()
            if row is None:
                return None
            # 校验版本归属的表名一致，避免跨表误读
            if row["table_name"] != table_name:
                return None
            new_content = row["new_content"]
            if not new_content:
                return None
            return json.loads(new_content) if isinstance(new_content, str) else new_content
    except Exception as e:
        logger.error("加载版本 %s 数据失败: %s", version_id, e)
        return None


def _compute_diff(from_data, to_data):
    """计算两个表数据结构之间的结构化 diff。

    返回 ``{"added": [...], "removed": [...], "modified": {field_path: {old, new}}}``。
    顶层按 key 比较；当两端同 key 均为 dict 时递归一层，field_path 以 ``.`` 拼接。
    list 视为原子值整体比较。
    """
    added: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []
    modified: Dict[str, Any] = {}

    if from_data is None and to_data is None:
        return {"added": added, "removed": removed, "modified": modified}
    if from_data is None:
        to_dict = to_data if isinstance(to_data, dict) else {"_root": to_data}
        for k, v in to_dict.items():
            added.append({"key": k, "value": v})
        return {"added": added, "removed": removed, "modified": modified}
    if to_data is None:
        from_dict = from_data if isinstance(from_data, dict) else {"_root": from_data}
        for k, v in from_dict.items():
            removed.append({"key": k, "value": v})
        return {"added": added, "removed": removed, "modified": modified}

    from_dict = from_data if isinstance(from_data, dict) else {"_root": from_data}
    to_dict = to_data if isinstance(to_data, dict) else {"_root": to_data}

    for key in from_dict:
        if key not in to_dict:
            removed.append({"key": key, "value": from_dict[key]})
    for key in to_dict:
        if key not in from_dict:
            added.append({"key": key, "value": to_dict[key]})

    for key in from_dict:
        if key not in to_dict:
            continue
        old_val = from_dict[key]
        new_val = to_dict[key]
        if old_val == new_val:
            continue
        # 两端均为 dict 时递归一层，生成更细粒度的 field_path
        if isinstance(old_val, dict) and isinstance(new_val, dict):
            nested = _compute_diff(old_val, new_val)
            for sub in nested["added"]:
                modified[f"{key}.{sub['key']}"] = {"old": None, "new": sub.get("value")}
            for sub in nested["removed"]:
                modified[f"{key}.{sub['key']}"] = {"old": sub.get("value"), "new": None}
            for sub_key, sub_val in nested["modified"].items():
                modified[f"{key}.{sub_key}"] = sub_val
            # 递归未产生差异（理论上不会发生，因为外层 !=），兜底记录整体
            if not (nested["added"] or nested["removed"] or nested["modified"]):
                modified[key] = {"old": old_val, "new": new_val}
        else:
            modified[key] = {"old": old_val, "new": new_val}

    return {"added": added, "removed": removed, "modified": modified}


# ─── 数据映射 ────────────────────────────────────────────────

@router.get("/data-mappings")
def list_data_mappings():
    """获取所有数据映射"""
    mappings = _config_store.get("data_mappings", {}).get("mappings", [])
    return {"mappings": mappings, "total": len(mappings)}


@router.get("/data-mappings/{mapping_id}")
def get_data_mapping(mapping_id: str):
    """获取指定数据映射"""
    mappings = _config_store.get("data_mappings", {}).get("mappings", [])
    for m in mappings:
        if m.get("mapping_id") == mapping_id:
            return m
    raise HTTPException(404, f"数据映射 {mapping_id} 不存在")


# ─── 内容级搜索（Task 9） ─────────────────────────────────────

@router.get("/search")
def search_content(q: str, scope: str = "content", limit: int = 100):
    """在表内容中检索关键字（Task 9.1）。

    返回命中列表，每项含 ``table/row_key/field/snippet``。
    ``scope=metadata`` 时仅返回空结果（元数据搜索由前端处理）。
    """

    if not q or len(q) < 1:
        return {"results": [], "total": 0}

    results: List[Dict[str, Any]] = []
    query_lower = q.lower()

    for table_name, data in _config_store._tables.items():
        if table_name in ("table_schemas", "table_categories"):
            continue
        if scope == "metadata":
            # 元数据搜索（表名/标签/描述）由前端处理
            continue
        _search_in_data(table_name, data, query_lower, results, limit)
        if len(results) >= limit:
            break

    return {
        "query": q,
        "scope": scope,
        "results": results[:limit],
        "total": len(results),
    }


def _search_in_data(table_name, data, query_lower, results, limit):
    """递归搜索表数据中的查询字符串，命中项追加到 results。"""
    if len(results) >= limit:
        return

    if isinstance(data, dict):
        for key, value in data.items():
            if len(results) >= limit:
                return
            if isinstance(value, (dict, list)):
                _search_in_data(table_name, value, query_lower, results, limit)
            else:
                val_str = str(value).lower()
                if query_lower in val_str:
                    results.append({
                        "table": table_name,
                        "row_key": str(key),
                        "field": str(key),
                        "snippet": _make_snippet(str(value), query_lower),
                    })
    elif isinstance(data, list):
        for idx, item in enumerate(data):
            if len(results) >= limit:
                return
            if isinstance(item, dict):
                pk = (item.get("id") or item.get("name") or item.get("code")
                      or item.get("key") or f"row_{idx}")
                for field, value in item.items():
                    if len(results) >= limit:
                        return
                    if isinstance(value, (dict, list)):
                        _search_in_data(table_name, value, query_lower, results, limit)
                    else:
                        val_str = str(value).lower()
                        if query_lower in val_str:
                            results.append({
                                "table": table_name,
                                "row_key": str(pk),
                                "field": str(field),
                                "snippet": _make_snippet(str(value), query_lower),
                            })
            elif isinstance(item, str) and query_lower in item.lower():
                results.append({
                    "table": table_name,
                    "row_key": f"row_{idx}",
                    "field": "",
                    "snippet": _make_snippet(item, query_lower),
                })


def _make_snippet(text, query, context=30):
    """围绕匹配位置生成片段。"""
    idx = text.lower().find(query.lower())
    if idx < 0:
        return text[:60]
    start = max(0, idx - context)
    end = min(len(text), idx + len(query) + context)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    return prefix + text[start:end] + suffix


# ─── 渲染配置 ────────────────────────────────────────────────

@router.get("/render-config/{type_id}")
def get_render_config(type_id: str):
    """获取指定类型的渲染配置"""
    render_configs = _config_store.get("cell_type_registry", {}).get("render_config", {})
    config = render_configs.get(type_id)
    if not config:
        raise HTTPException(404, f"类型 {type_id} 的渲染配置不存在")
    return config


# ─── WebSocket 配置变更推送 ──────────────────────────────────
# 注意：WebSocket 路由必须挂载到独立的 config_ws_router 上，不能挂在主 router 上。
# 原因：app.py 中 `app.include_router(config_api_router, dependencies=[Depends(verify_api_key)])`
# 会把 dependencies 应用到 router 内所有路由，包括 WebSocket。而 verify_api_key 依赖
# APIKeyHeader，其 __call__ 需要 HTTP Request 参数，WebSocket 上下文无法提供，
# 导致 TypeError: APIKeyHeader.__call__() missing 1 required positional argument: 'request'
config_ws_router = APIRouter(prefix="/api/config", tags=["config-ws"])


@config_ws_router.websocket("/ws")
async def config_ws(websocket: WebSocket):
    """WebSocket 配置变更推送端点"""
    await websocket.accept()
    if _hot_reload_manager:
        _hot_reload_manager.add_ws_connection(websocket)

    try:
        while True:
            # 保持连接，等待客户端消息（心跳等）
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
            elif data == "status":
                status = _hot_reload_manager.get_status() if _hot_reload_manager else {}
                await websocket.send_text(json.dumps({"type": "status", **status}))
    except WebSocketDisconnect:
        pass
    finally:
        if _hot_reload_manager:
            _hot_reload_manager.remove_ws_connection(websocket)


# ─── WebSocket 事件订阅推送（Task 16.3：事件驱动并行通道） ─────

@config_ws_router.websocket("/ws/events")
async def events_ws(websocket: WebSocket):
    """WebSocket 事件订阅端点：订阅 EventBus 事件并推送给前端。

    订阅 ``SnapshotUpdated`` / ``EventLogged`` / ``StatisticsUpdated`` 三类事件，
    将事件载荷推送给前端。与 ``config_ws``（配置热重载）互不影响，作为
    事件驱动并行通道，不破坏现有前端通信。
    """
    await websocket.accept()
    bus: "EventBus" = websocket.app.state.bus

    queue: "asyncio.Queue" = asyncio.Queue()

    def _on_snapshot(event: SnapshotUpdated) -> None:
        try:
            queue.put_nowait({"type": "snapshot", "data": event.snapshot})
        except Exception:
            pass

    def _on_event_logged(event: EventLogged) -> None:
        try:
            queue.put_nowait({"type": "event_logged", "data": event.event})
        except Exception:
            pass

    def _on_statistics(event: StatisticsUpdated) -> None:
        try:
            queue.put_nowait({"type": "statistics", "data": event.stats})
        except Exception:
            pass

    bus.subscribe(SnapshotUpdated, _on_snapshot)
    bus.subscribe(EventLogged, _on_event_logged)
    bus.subscribe(StatisticsUpdated, _on_statistics)

    try:
        while True:
            msg = await queue.get()
            await websocket.send_json(msg)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        # 取消订阅，避免向已关闭连接推送
        for handler, evt_cls in (
            (_on_snapshot, SnapshotUpdated),
            (_on_event_logged, EventLogged),
            (_on_statistics, StatisticsUpdated),
        ):
            try:
                name = evt_cls.__name__
                handlers = bus._subscribers.get(name, [])
                if handler in handlers:
                    handlers.remove(handler)
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════
#  Part 2: 来自 table_api.py — 表驱动架构 API
# ══════════════════════════════════════════════════════════════════════

table_router = APIRouter(prefix="/api/v1/table", tags=["table-driven"])
table_config_router = APIRouter(prefix="/api/config", tags=["config"])

# 引擎实例（由app.py注入）
_engine = None
_panel_generator = None
_data_binder = None
_ownership_manager = None
_table_config_store = None
_rule_engine = None
# SubTask 22.5: EventBus 引用（由 set_engine 注入，供命令类端点发布事件）
_bus = None

def set_engine(engine, config_dir=None, bus=None):
    global _engine, _panel_generator, _data_binder, _ownership_manager, _table_config_store, _rule_engine, _bus
    _engine = engine
    # SubTask 22.5: 保存 bus 引用供命令类端点发布事件
    _bus = bus
    if config_dir:
        from core.table_engine import ConfigStore, PanelGenerator, DataBinder, PropertyOwnershipManager, RuleEngine
        _table_config_store = ConfigStore(config_dir)
        _table_config_store.load_all()
        _panel_generator = PanelGenerator(_table_config_store)
        _data_binder = DataBinder()
        _ownership_manager = PropertyOwnershipManager(_table_config_store)
        # 创建规则引擎并注册 handler
        _rule_engine = RuleEngine(_table_config_store)
        try:
            from native.builtins import _HANDLERS
            for name, fn in _HANDLERS.items():
                _rule_engine.register_handler(name, fn)
        except Exception:
            pass
        # 将表驱动组件桥接到 PoolEngine
        if hasattr(engine, 'set_table_engine'):
            engine.set_table_engine(_table_config_store, _rule_engine, _panel_generator, _ownership_manager)


class PanelRequest(BaseModel):
    node_type: str
    pool_type: str = "dzh"
    data: Dict[str, Any] = {}


class FieldChangeRequest(BaseModel):
    node_type: str
    pool_type: str = "dzh"
    data: Dict[str, Any] = {}
    field_key: str
    value: Any


class ValidateFieldRequest(BaseModel):
    node_type: str
    pool_type: str = "dzh"
    field_key: str
    value: Any


@table_router.get("/layouts")
def list_layouts():
    """列出所有可用的UI布局"""
    if not _table_config_store:
        raise HTTPException(500, "引擎未初始化")
    layouts = _table_config_store.get("ui_layouts", {}).get("layouts", {})
    return {
        "layouts": [
            {"layout_id": k, "name": v.get("name"), "target_type": v.get("target_type"),
             "target_scope": v.get("target_scope"), "pool_type": v.get("pool_type")}
            for k, v in layouts.items()
        ]
    }


@table_router.get("/layouts/{layout_id}")
def get_layout(layout_id: str):
    """获取指定布局的完整配置"""
    if not _table_config_store:
        raise HTTPException(500, "引擎未初始化")
    layout = _table_config_store.get_layout(layout_id)
    if not layout:
        raise HTTPException(404, f"布局 {layout_id} 不存在")
    return layout


@table_router.post("/panel")
def generate_panel(req: PanelRequest):
    """根据节点类型和数据生成面板配置"""
    if not _panel_generator:
        raise HTTPException(500, "引擎未初始化")
    return _panel_generator.generate_panel(req.node_type, req.pool_type, req.data)


@table_router.post("/panel/apply")
def apply_field_change(req: FieldChangeRequest):
    """应用字段变更"""
    if not _data_binder:
        raise HTTPException(500, "引擎未初始化")
    result = _panel_generator.apply_change(
        req.node_type, req.pool_type, req.data, req.field_key, req.value
    )
    return result


@table_router.post("/panel/validate")
def validate_field(req: ValidateFieldRequest):
    """校验字段值"""
    if not _ownership_manager:
        raise HTTPException(500, "引擎未初始化")
    errors = _ownership_manager.validate_field(req.node_type, req.pool_type, req.field_key, req.value)
    return {"valid": len(errors) == 0, "errors": errors}


@table_router.post("/reload")
def hot_reload():
    """触发配置表热加载"""
    if not _table_config_store:
        raise HTTPException(500, "引擎未初始化")
    changed = _table_config_store.check_hot_reload()
    # 热加载后使关联引擎缓存失效
    if changed:
        if _rule_engine:
            _rule_engine.invalidate_cache()
        if _panel_generator:
            _panel_generator.invalidate_cache()
        if _ownership_manager:
            _ownership_manager.invalidate_cache()
        # 同步 PoolEngine 桥接
        if _engine and hasattr(_engine, 'check_hot_reload'):
            _engine.check_hot_reload()
        # SubTask 22.5: 命令类端点——发布 ConfigChanged 事件通知所有模块重载
        # （过渡期与 _engine.check_hot_reload 并行，后续删除 engine 直调）
        if _bus is not None:
            try:
                _bus.publish(ConfigChanged(changed_tables=list(changed)))
            except Exception as _ex:
                logger.warning("hot_reload 发布 ConfigChanged 失败: %s", _ex)
    return {"changed": changed, "count": len(changed)}


@table_router.get("/validate")
def validate_configs():
    """校验所有配置表"""
    if not _table_config_store:
        raise HTTPException(500, "引擎未初始化")
    from native.validators import ConfigIntegrityValidator
    validator = ConfigIntegrityValidator(_table_config_store._config_dir)
    errors = validator.validate_all()
    return {"valid": len(errors) == 0, "errors": errors}


@table_router.get("/validate/integrity")
def validate_integrity():
    """运行完整的配置完整性校验（覆盖所有节点类型及属性）"""
    if not _engine:
        raise HTTPException(500, "引擎未初始化")
    from native.validators import ConfigIntegrityValidator
    import os
    config_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")
    validator = ConfigIntegrityValidator(config_dir)
    return validator.validate_all()


@table_router.get("/status")
def engine_status():
    """获取引擎状态"""
    if not _table_config_store:
        raise HTTPException(500, "引擎未初始化")
    return {
        "tables_loaded": len(_table_config_store.table_names),
        "table_names": _table_config_store.table_names,
        "load_times": _table_config_store.load_times
    }


@table_router.get("/enums/{pool_type}")
def get_enums(pool_type: str):
    """获取指定池类型的枚举数据"""
    if not _table_config_store:
        raise HTTPException(500, "引擎未初始化")
    if pool_type == "tdx":
        enums = _table_config_store.get("tdx_enums", {}).get("enums", {})
        indicators = _table_config_store.get("tdx_indicators", {}).get("indicators", [])
        # TDX lookup数据：ntjindexno查找表 + 字段可见性矩阵
        ntjindexno_lookup = _table_config_store.get("tdx_ntjindexno_lookup", {})
        field_visibility = _table_config_store.get("tdx_field_visibility", {})
        return {
            "enums": enums,
            "indicators": indicators,
            "tdx_ntjindexno_lookup": ntjindexno_lookup,
            "tdx_field_visibility": field_visibility,
        }
    else:
        # DZH枚举从modules.json的flow_schema中获取
        modules = _table_config_store.get("modules", {})
        flow_schema = modules.get("flow_schema", {})
        return {"enums": {}, "flow_schema": flow_schema}


@table_router.get("/ownership/{pool_type}")
def get_ownership(pool_type: str):
    """获取指定池类型的属性所有权配置"""
    if not _ownership_manager:
        raise HTTPException(500, "引擎未初始化")
    ownership = _ownership_manager
    return {
        "pool_type": pool_type,
        "blocked_attrs": ownership.get_blocked_attrs(pool_type),
        "type_mapping": ownership.get_type_mapping(pool_type, "tdx" if pool_type == "dzh" else "dzh"),
        "rules": ownership.ownership.get("rules", {}).get(f"{pool_type}_import", {})
    }


@table_router.get("/ownership/{pool_type}/{node_type}")
def check_attr_ownership(pool_type: str, node_type: str):
    """检查指定节点类型在指定池类型下的属性所有权"""
    if not _ownership_manager:
        raise HTTPException(500, "引擎未初始化")
    ownership = _ownership_manager
    return {
        "pool_type": pool_type,
        "node_type": node_type,
        "allowed_attrs": ownership.get_allowed_attrs(pool_type, node_type),
        "disabled_fields": ownership.get_disabled_fields(pool_type, node_type),
        "pool_type_for_type": ownership.get_pool_type_for_type(node_type)
    }


@table_router.post("/ownership/validate")
def validate_ownership(req: PanelRequest):
    """校验数据对象是否符合指定池类型的属性所有权"""
    if not _ownership_manager:
        raise HTTPException(500, "引擎未初始化")
    ownership = _ownership_manager
    filtered = ownership.filter_data(req.pool_type, req.node_type, req.data)
    blocked = ownership.get_blocked_attrs(req.pool_type)
    violations = {k: v for k, v in req.data.items() if k in blocked}
    return {
        "valid": len(violations) == 0,
        "violations": violations,
        "filtered_data": filtered
    }


# ─── 规则管理 API ──────────────────────────────────────────────

class RuleRequest(BaseModel):
    rule_id: str
    trigger: Dict[str, Any] = {}
    guard: Optional[Any] = None
    action: Optional[str] = None
    handler_ref: Optional[str] = None
    params_template: Optional[Dict[str, Any]] = None
    priority: int = 100
    stop_on_match: bool = True
    tags: Optional[List[str]] = None
    description: Optional[str] = None


class RuleExportRequest(BaseModel):
    rules: List[Dict[str, Any]]


class RuleReorderRequest(BaseModel):
    rule_ids: List[str]


def _get_action_rules_path() -> str:
    """获取 action_rules.json 文件路径"""
    config_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")
    return os.path.join(config_dir, "action_rules.json")


def _read_action_rules() -> Dict:
    """读取 action_rules.json"""
    path = _get_action_rules_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        raise HTTPException(500, f"读取规则文件失败: {e}")


def _write_action_rules(data: Dict) -> None:
    """写入 action_rules.json"""
    path = _get_action_rules_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        raise HTTPException(500, f"写入规则文件失败: {e}")


@table_router.get("/rules")
def list_rules(tags: Optional[str] = None):
    """列出行为规则"""
    if not _table_config_store:
        raise HTTPException(500, "引擎未初始化")
    tag_list = tags.split(",") if tags else None
    rules = _table_config_store.get_rules(tag_list)
    return {"rules": rules}


@table_router.get("/rules/handlers")
def list_handlers():
    """列出可用的 handler 函数（从 builtins.py 的 _HANDLERS 注册表获取）"""
    if not _engine:
        raise HTTPException(500, "引擎未初始化")
    try:
        from native.builtins import _HANDLERS
        handlers = []
        for name, fn in _HANDLERS.items():
            desc = fn.__doc__ or ""
            # 取第一行作为简短描述
            short_desc = desc.strip().split("\n")[0] if desc.strip() else ""
            handlers.append({
                "name": name,
                "description": short_desc,
            })
        return {"handlers": handlers}
    except Exception as e:
        raise HTTPException(500, f"获取 handler 列表失败: {e}")


@table_router.post("/rules")
def save_rule(req: RuleRequest):
    """创建或更新一条规则"""
    data = _read_action_rules()
    rules = data.get("rules", [])

    rule_dict = {
        "rule_id": req.rule_id,
        "trigger": req.trigger,
        "guard": req.guard,
        "action": req.action,
        "priority": req.priority,
        "stop_on_match": req.stop_on_match,
    }
    if req.handler_ref:
        rule_dict["handler_ref"] = req.handler_ref
    if req.params_template:
        rule_dict["params_template"] = req.params_template
    if req.tags:
        rule_dict["tags"] = req.tags
    if req.description:
        rule_dict["description"] = req.description

    # 查找是否已存在同 rule_id 的规则
    existing_idx = None
    for i, r in enumerate(rules):
        if r.get("rule_id") == req.rule_id:
            existing_idx = i
            break

    if existing_idx is not None:
        rules[existing_idx] = rule_dict
    else:
        rules.append(rule_dict)

    data["rules"] = rules
    _write_action_rules(data)

    # 触发热加载
    if _table_config_store:
        _table_config_store.check_hot_reload()

    return {"ok": True, "rule_id": req.rule_id, "action": "updated" if existing_idx is not None else "created"}


@table_router.delete("/rules/{rule_id}")
def delete_rule(rule_id: str):
    """删除一条规则"""
    data = _read_action_rules()
    rules = data.get("rules", [])

    new_rules = [r for r in rules if r.get("rule_id") != rule_id]
    if len(new_rules) == len(rules):
        raise HTTPException(404, f"规则 {rule_id} 不存在")

    data["rules"] = new_rules
    _write_action_rules(data)

    # 触发热加载
    if _table_config_store:
        _table_config_store.check_hot_reload()

    return {"ok": True, "deleted": rule_id}


@table_router.post("/rules/export")
def export_rules(req: RuleExportRequest):
    """导出规则到 action_rules.json"""
    export_data = {
        "version": "1.0",
        "description": "行为规则表：提取硬编码逻辑为规则条目，统一驱动业务逻辑与UI联动",
        "rules": req.rules
    }
    _write_action_rules(export_data)

    # 触发热加载
    if _table_config_store:
        _table_config_store.check_hot_reload()

    return {"ok": True, "count": len(req.rules)}


@table_router.post("/rules/reorder")
def reorder_rules(req: RuleReorderRequest):
    """重排规则顺序（按新的 rule_ids 顺序重新分配 priority）"""
    data = _read_action_rules()
    rules = data.get("rules", [])

    # 建立 rule_id -> rule 的映射
    rule_map = {r.get("rule_id"): r for r in rules}

    # 按 rule_ids 的顺序重新分配 priority
    reordered = []
    for idx, rule_id in enumerate(req.rule_ids):
        rule = rule_map.get(rule_id)
        if rule:
            rule["priority"] = (idx + 1) * 10
            reordered.append(rule)

    # 添加不在 rule_ids 中的规则（保持原 priority）
    remaining = [r for r in rules if r.get("rule_id") not in set(req.rule_ids)]
    reordered.extend(remaining)

    data["rules"] = reordered
    _write_action_rules(data)

    # 触发热加载
    if _table_config_store:
        _table_config_store.check_hot_reload()

    return {"ok": True, "reordered": len(req.rule_ids)}


# ─── 规则配置 API（/api/config/ 前缀） ──────────────────────────

@table_config_router.get("/action_rules")
def get_action_rules():
    """获取完整的 action_rules.json 内容"""
    data = _read_action_rules()
    return data


class ActionRulesUpdateRequest(BaseModel):
    version: str = "1.0"
    description: Optional[str] = None
    rules: List[Dict[str, Any]]


@table_config_router.post("/action_rules")
def update_action_rules(req: ActionRulesUpdateRequest):
    """更新 action_rules.json（校验后写入并触发热加载）"""
    # 校验每条规则必须有 rule_id 和 trigger
    errors = []
    for i, rule in enumerate(req.rules):
        if not rule.get("rule_id"):
            errors.append(f"rules[{i}] 缺少 rule_id")
        if not rule.get("trigger"):
            errors.append(f"rules[{i}] 缺少 trigger")
    if errors:
        raise HTTPException(400, f"规则校验失败: {'; '.join(errors)}")

    # 保留现有顶层字段（如 handlers），仅更新 version/description/rules
    existing = _read_action_rules()
    existing["version"] = req.version
    existing["description"] = req.description or "行为规则表：提取硬编码逻辑为规则条目，统一驱动业务逻辑与UI联动"
    existing["rules"] = req.rules
    _write_action_rules(existing)

    # 触发热加载
    if _table_config_store:
        _table_config_store.check_hot_reload()

    return {"ok": True, "count": len(req.rules)}


@table_config_router.get("/handlers")
def get_handlers():
    """获取可用的 handler 函数列表"""
    try:
        from native.builtins import _HANDLERS
        handlers = []
        for name, fn in _HANDLERS.items():
            desc = fn.__doc__ or ""
            short_desc = desc.strip().split("\n")[0] if desc.strip() else ""
            handlers.append({
                "name": name,
                "description": short_desc,
            })
        return {"handlers": handlers}
    except Exception as e:
        # 回退到硬编码列表
        hardcoded = [
            "init_market_source", "init_stock_state_pool", "edge_default_transfer",
            "tdx_convert_from_file", "tdx_convert_from_pool", "stock_pool_hold",
            "transfer_condition_check", "discard_sink_drop", "dzh_condition_pool",
            "formula_eval", "cross_section_eval", "basic_filter",
            "condition_dispatcher",
            "resolve_immediate", "resolve_delayed", "resolve_before_open",
            "resolve_after_open", "resolve_before_close", "resolve_after_close",
            "resolve_trading_time", "resolve_specific_time",
            "candidate_resolve", "resolve_market", "sector_filter",
            "pass_through", "profit_analysis_calc", "time_trigger_check",
            "render_label", "render_shape", "accumulate_state",
            "discard_stocks", "transfer_with_market_data_handler",
            "log_transfer_handler", "condition_dispatch_handler",
        ]
        return {"handlers": [{"name": h, "description": ""} for h in hardcoded]}


# ══════════════════════════════════════════════════════════════════════
#  Part 3: 来自 meta_api.py — 元数据 / 备选池管理 API
# ══════════════════════════════════════════════════════════════════════

# ─── Pydantic 请求/响应模型 - 备选池管理 API ───

class StockItem(BaseModel):
    """股票项"""
    setcode: int = Field(..., description="市场代码: 0=深圳, 1=上海, 2=北京")
    code: str = Field(..., description="股票代码")
    name: Optional[str] = Field(None, description="股票名称")


class ResolveRequest(BaseModel):
    """统一解析请求"""
    spinfo_type: int = Field(..., ge=0, le=7, description="备选池类型 0-7")
    customblockname: Optional[str] = Field(None, description="type=4时的板块名称")
    stks: Optional[List[StockItem]] = Field(None, description="type=0时的显式股票列表")
    force_refresh: bool = Field(False, description="是否强制刷新缓存")
    selections: Optional[List[Dict[str, Any]]] = Field(
        None, description="设计时 attrtext 解析后的 selections，用于转换为代码集合"
    )


class BuildRequest(BaseModel):
    """从板块构建备选池请求"""
    sector_id: str = Field(..., description="源板块ID")
    target_block_code: Optional[str] = Field(None, description="目标板块代码（可选）")


class AddFavoritesRequest(BaseModel):
    """添加自选股请求"""
    stocks: List[StockItem] = Field(..., description="要添加的股票列表")


class RemoveFavoritesRequest(BaseModel):
    """删除自选股请求"""
    stock_codes: List[str] = Field(..., description="要删除的股票代码列表")


class CreateBlockRequest(BaseModel):
    """创建自定义板块请求"""
    block_code: str = Field(..., description="板块代码")
    block_name: str = Field(..., description="板块名称")
    description: Optional[str] = Field(None, description="描述")
    members: Optional[List[StockItem]] = Field(None, description="初始成员列表")


class UpdateBlockRequest(BaseModel):
    """更新自定义板块请求"""
    block_name: Optional[str] = Field(None, description="板块名称")
    description: Optional[str] = Field(None, description="描述")
    members: Optional[List[StockItem]] = Field(None, description="成员列表（全量替换）")


class AddMembersRequest(BaseModel):
    """添加板块成员请求"""
    stocks: List[StockItem] = Field(..., description="要添加的股票列表")


class RemoveMembersRequest(BaseModel):
    """删除板块成员请求"""
    stock_codes: List[str] = Field(..., description="要删除的股票代码列表")


class ErrorResponse(BaseModel):
    """统一错误响应"""
    success: bool = False
    error: Dict[str, Any] = Field(..., description="错误详情")


def create_error_response(code: str, message: str, status_code: int = 400,
                          details: Any = None) -> Dict:
    """构建统一错误响应"""
    return {
        "success": False,
        "error": {
            "code": code,
            "message": message,
            "details": details,
        }
    }


def create_meta_router() -> APIRouter:
    router = APIRouter()

    @router.get("/tq-status")
    async def get_tq_status(request: Request):
        tq = request.app.state.tq
        return {
            "code": 0, "msg": "ok",
            "data": {
                "mode": tq.get_mode_info(),
                "mock_mode": tq.mock_mode,
                "is_ready": tq.is_ready(),
            }
        }

    @router.get("/datasource/list")
    async def list_datasources(request: Request):
        """获取所有可用数据源列表及其状态。"""
        tq = request.app.state.tq
        sources = tq.get_available_sources()
        active = tq.get_data_source_state().get('active')
        label_map = {
            'mock': 'Mock 模拟',
            'tq_dll': '通达信 DLL',
            'tq_sdk': '通达信 SDK',
            'akshare': 'AKShare',
        }
        cap_map = {
            'mock': ['kline', 'snapshot', 'market', 'formula', 'sector', 'replay', 'financial', 'user_block'],
            'tq_dll': ['kline', 'snapshot', 'market', 'formula', 'sector', 'financial', 'user_block'],
            'tq_sdk': ['kline', 'snapshot', 'market', 'formula', 'sector', 'financial'],
            'akshare': ['kline', 'market', 'sector', 'financial'],
        }
        for s in sources:
            name = s.get('name', '')
            s['label'] = label_map.get(name, name)
            s['active'] = (name == active)
            s['capabilities'] = cap_map.get(name, [])
        return {"code": 0, "msg": "ok", "data": sources}

    @router.post("/datasource/switch")
    async def switch_datasource(request: Request):
        """切换当前活跃数据源。请求体: {"source": "sdk"|"dll"|"akshare"|"mock"}"""
        body = await request.json()
        source = body.get("source", "")
        if not source:
            return {"code": 1, "msg": "缺少 source 参数"}
        tq = request.app.state.tq
        result = tq.set_active_source(source)
        if result.get("success"):
            return {"code": 0, "msg": "ok", "data": result}
        else:
            return {"code": 2, "msg": result.get("error", "切换失败"), "data": result}

    @router.get("/modules")
    async def get_modules(request: Request):
        # SubTask 22.5: 查询类端点——保留直接调用 engine（依赖注入查询接口）
        data = request.app.state.engine.get_modules()
        return {"code": 0, "msg": "ok", "data": data}

    @router.get("/conditions")
    async def get_conditions(request: Request):
        # SubTask 22.5: 查询类端点——保留直接调用 engine（依赖注入查询接口）
        data = request.app.state.engine.get_conditions()
        return {"code": 0, "msg": "ok", "data": data}

    @router.get("/engines")
    async def get_engines(request: Request):
        # SubTask 22.5: 查询类端点——保留直接调用 engine（依赖注入查询接口）
        data = request.app.state.engine.get_engines()
        return {"code": 0, "msg": "ok", "data": data}

    # ─── 配置表查询端点 ──────────────────────────────────────

    @router.get("/config/{table_name}")
    async def get_config_table(request: Request, table_name: str):
        """通过 PoolEngine 桥接查询配置表"""
        # SubTask 22.5: 查询类端点——保留直接调用 engine（依赖注入查询接口）
        engine = request.app.state.engine
        if hasattr(engine, '_config_store') and engine._config_store:
            data = engine._config_store.get(table_name)
            if data is not None:
                return {"code": 0, "msg": "ok", "data": data}
        return {"code": 1, "msg": f"配置表 {table_name} 不存在"}

    @router.get("/table-names")
    async def get_table_names(request: Request):
        """列出所有配置表名称"""
        # SubTask 22.5: 查询类端点——保留直接调用 engine（依赖注入查询接口）
        engine = request.app.state.engine
        if hasattr(engine, '_config_store') and engine._config_store:
            return {"code": 0, "msg": "ok", "data": engine._config_store.table_names}
        return {"code": 0, "msg": "ok", "data": []}

    # ══════════════════════════════════════════════════════════════════════
    #  备选池管理 API 路由 (9.1-9.9)
    # ══════════════════════════════════════════════════════════════════════

    # 9.1: 创建备选池路由组
    cp_router = APIRouter(prefix="/candidate-pool", tags=["备选池管理"])

    # 9.2: POST /resolve — 统一解析接口
    @cp_router.post("/resolve")
    async def resolve_candidate_pool(request: Request, body: ResolveRequest):
        """统一解析备选池配置"""
        logger.info("API: resolve_candidate_pool spinfo_type=%d", body.spinfo_type)
        try:
            resolver = _get_resolver(request)
            kwargs = {}
            if body.customblockname:
                kwargs['customblockname'] = body.customblockname
            if body.stks:
                kwargs['stks'] = [s.model_dump() for s in body.stks]
            if body.force_refresh:
                kwargs['force_refresh'] = True

            stocks = await resolver.resolve(body.spinfo_type, **kwargs)

            if not stocks:
                raise HTTPException(
                    status_code=503,
                    detail=create_error_response(
                        "SERVICE_UNAVAILABLE",
                        "所有数据源不可用或无数据返回",
                        503,
                        {"spinfo_type": body.spinfo_type}
                    )
                )

            # Task 5.3: 设计时转换 — 如果请求体包含 selections，调用 convert_to_code_set
            code_set = None
            if body.selections:
                try:
                    if hasattr(resolver, 'convert_to_code_set'):
                        code_set = resolver.convert_to_code_set(body.selections)
                    else:
                        logger.warning("resolver 未实现 convert_to_code_set，跳过设计时转换")
                except Exception as e:
                    logger.warning("convert_to_code_set 转换失败: %s", e)

            data = {
                "stocks": stocks,
                "count": len(stocks),
                "source": "resolved",
                "spinfo_type": body.spinfo_type,
                "resolved_at": datetime.now().isoformat(),
            }
            if code_set is not None:
                data["code_set"] = code_set

            return {
                "success": True,
                "data": data,
            }
        except ValueError as e:
            logger.warning("API: resolve 参数错误: %s", e)
            raise HTTPException(
                status_code=400,
                detail=create_error_response("INVALID_PARAM", str(e), 400)
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error("API: resolve 异常: %s", e, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=create_error_response("INTERNAL_ERROR", str(e), 500)
            )

    # 9.3: GET /category-tree — 分类树查询
    @cp_router.get("/category-tree")
    async def get_category_tree(
        request: Request,
        source: str = "tdx",
        category: Optional[str] = None
    ):
        """获取分类树（用于type=0的自设监控品种选择界面）

        Task 7.2: 优先从数据库 storage.get_sectors_catalog() 构建分类树，
        按 category 分组。数据库无数据时降级到 resolver.get_category_tree()。

        Returns:
            {'categories': [{'name': 'concept', 'label': '概念', 'sectors': [...]}, ...]}
        """
        logger.info("API: get_category_tree source=%s category=%s", source, category)
        # 分类中文标签映射
        category_labels = {
            'concept': '概念',
            'industry': '行业',
            'index': '指数',
            'style': '风格',
            'region': '地区',
            'other': '其他',
        }
        try:
            storage = request.app.state.storage
            sectors = []

            # 优先从数据库读取
            try:
                if hasattr(storage, 'get_sectors_catalog'):
                    # category='all' 时不过滤分类
                    cat_filter = None if (category is None or category == 'all') else category
                    sectors = storage.get_sectors_catalog(category=cat_filter)
            except Exception as e:
                logger.warning("get_category_tree: 数据库读取失败: %s", e)
                sectors = []

            if sectors:
                # 按 category 分组构建树
                grouped: Dict[str, List[Dict]] = {}
                for sec in sectors:
                    cat = sec.get('category', 'other')
                    grouped.setdefault(cat, []).append({
                        'sector_id': sec.get('sector_id', ''),
                        'sector_code': sec.get('sector_code', ''),
                        'sector_name': sec.get('sector_name', ''),
                        'category': cat,
                        'source': sec.get('source', ''),
                        'member_count': sec.get('member_count', 0),
                    })

                # 按固定顺序输出已知分类，再追加未知分类
                ordered = ['concept', 'industry', 'index', 'style', 'region']
                cat_names = [c for c in ordered if c in grouped]
                cat_names += [c for c in grouped if c not in ordered]

                categories = []
                for cat_name in cat_names:
                    categories.append({
                        'name': cat_name,
                        'label': category_labels.get(cat_name, cat_name),
                        'sectors': grouped[cat_name],
                    })

                tree_data = {
                    'categories': categories,
                    'count': sum(len(c['sectors']) for c in categories),
                    'source': 'database',
                }
            else:
                # 数据库无数据，降级到 resolver.get_category_tree()
                resolver = _get_resolver(request)
                tree_data = await resolver.get_category_tree(source=source, category=category)
                tree_data['source'] = 'resolver'

            return {"success": True, "data": tree_data}
        except ValueError as e:
            raise HTTPException(
                status_code=400,
                detail=create_error_response("INVALID_PARAM", str(e), 400)
            )
        except Exception as e:
            logger.error("API: get_category_tree 异常: %s", e, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=create_error_response("INTERNAL_ERROR", str(e), 500)
            )

    # 9.4: POST /build — 从板块构建备选池
    @cp_router.post("/build")
    async def build_from_sector(request: Request, body: BuildRequest):
        """从板块构建备选池"""
        logger.info("API: build_from_sector sector_id=%s", body.sector_id)
        try:
            resolver = _get_resolver(request)
            result = await resolver.build_from_sector(
                sector_id=body.sector_id,
                target_block_code=body.target_block_code
            )
            return {"success": True, "data": result}
        except ValueError as e:
            raise HTTPException(
                status_code=400,
                detail=create_error_response("INVALID_PARAM", str(e), 400)
            )
        except Exception as e:
            logger.error("API: build_from_sector 异常: %s", e, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=create_error_response("INTERNAL_ERROR", str(e), 500)
            )

    # 9.5: GET /sectors/{sector_id}/members — 板块成分股查询
    @cp_router.get("/sectors/{sector_id}/members")
    async def get_sector_members(request: Request, sector_id: str, include_info: bool = False):
        """查询指定板块的成分股详情"""
        logger.info("API: get_sector_members sector_id=%s include_info=%s", sector_id, include_info)
        try:
            storage = request.app.state.storage
            resolver = _get_resolver(request)

            # 先从数据库查板块基本信息（含 sector_name）
            sectors_catalog = storage.get_sectors_catalog() if hasattr(storage, 'get_sectors_catalog') else []
            sector_info = next((s for s in sectors_catalog if s.get('sector_id') == sector_id), {})
            sector_name = sector_info.get('sector_name', '')

            # 1. 直接调用 storage.get_sector_members() 获取含名称的成分股列表
            members = storage.get_sector_members(sector_id)

            # 2. 数据库无数据，用 sector_name 调用 LocalFileProvider 获取本地文件成分股
            if not members and sector_name:
                local_provider = resolver._providers.get('local_file')
                if local_provider and hasattr(local_provider, 'get_block_members'):
                    try:
                        import asyncio as _asyncio
                        raw_codes = local_provider.get_block_members(sector_name)
                        if _asyncio.iscoroutine(raw_codes):
                            raw_codes = await raw_codes
                        if raw_codes:
                            members = [{'stock_code': c, 'name': ''} for c in raw_codes if c]
                            logger.info("get_sector_members: LocalFileProvider 获取板块 '%s' 成分股 %d 只", sector_name, len(members))
                    except Exception as e:
                        logger.debug("get_sector_members: LocalFileProvider 获取失败: %s", e)

            # 3. 仍无数据，降级到 resolver._fetch_sector_members
            if not members:
                members = await resolver._fetch_sector_members(sector_id)

            result = {
                "sector_id": sector_id,
                "sector_name": sector_name or sector_id,
                "members": members,
                "count": len(members) if members else 0,
            }

            if include_info and members:
                # 可选：补充价格、涨跌幅等详细信息
                tq = request.app.state.tq
                if hasattr(tq, 'get_snapshot'):
                    codes = [m.get('stock_code', '') for m in members if m.get('stock_code')]
                    try:
                        snapshots = tq.get_snapshot(codes[:50])  # 限制数量避免超时
                        for m in members:
                            code = m.get('stock_code', '')
                            if code in snapshots:
                                m.update(snapshots[code])
                    except Exception as e:
                        logger.debug("获取快照信息失败: %s", e)

            return {"success": True, "data": result}
        except ValueError as e:
            raise HTTPException(
                status_code=404,
                detail=create_error_response("NOT_FOUND", str(e), 404)
            )
        except Exception as e:
            logger.error("API: get_sector_members 异常: %s", e, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=create_error_response("INTERNAL_ERROR", str(e), 500)
            )

    # 9.5b: GET /sectors/by-index/{index_code}/members — 由板块指数代码输出成分股
    # 板块指数代码（如 880201 黑龙江）与板块名一一对应，本端点建立"板块指数→成分股"映射
    @cp_router.get("/sectors/by-index/{index_code}/members")
    async def get_members_by_sector_index_code(
        request: Request, index_code: str, source: Optional[str] = None
    ):
        """由板块指数代码输出板块成分股。

        板块指数代码（880xxx）是衡量板块走势的指数代码，与板块本身一一对应。
        - 概念/风格/指数板块: 直接从 infoharbor_block.dat 匹配
        - 行业板块: 通过 tdxzs.cfg type=2 的 desc(Txxxxxx) → tdxhy.cfg 成分股
        - 地区板块: 通过 tdxzs.cfg type=3 的 desc(序列号) → base.dbf DY字段成分股
        """
        logger.info("API: get_members_by_sector_index_code index_code=%s source=%s",
                    index_code, source)
        try:
            resolver = _get_resolver(request)
            local_provider = resolver._providers.get('local_file')
            if not local_provider:
                raise HTTPException(500, "本地文件数据源未加载")

            result = local_provider.get_members_by_sector_index_code(index_code, source=source)
            if not result:
                raise HTTPException(
                    status_code=404,
                    detail=create_error_response(
                        "NOT_FOUND", f"板块指数代码 {index_code} 未找到对应板块", 404)
                )

            # 补全个股名称
            name_map = {}
            if hasattr(local_provider, 'get_stock_name_map'):
                try:
                    name_map = local_provider.get_stock_name_map()
                except Exception:
                    pass
            members_with_name = []
            for code in result.get('members', []):
                members_with_name.append({
                    'stock_code': code,
                    'name': name_map.get(code, ''),
                })

            return {
                "success": True,
                "data": {
                    "sector_index_code": result['sector_index_code'],
                    "sector_code": result.get('sector_code', ''),
                    "sector_name": result['sector_name'],
                    "category": result['category'],
                    "source": result['source'],
                    "members": members_with_name,
                    "member_count": result['member_count'],
                }
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error("API: get_members_by_sector_index_code 异常: %s", e, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=create_error_response("INTERNAL_ERROR", str(e), 500)
            )

    # 9.5c: GET /stocks/{stock_code}/sectors — 由个股代码反查所属板块
    # 建立"个股→板块"反向映射，支持多数据源独立查询
    @cp_router.get("/stocks/{stock_code}/sectors")
    async def get_sectors_by_stock(
        request: Request, stock_code: str, source: Optional[str] = None
    ):
        """由个股代码反查所属所有板块（板块↔个股反向映射）。

        返回该个股所属的所有板块列表，按数据源分组，不跨数据源匹配。
        """
        logger.info("API: get_sectors_by_stock stock_code=%s source=%s", stock_code, source)
        try:
            resolver = _get_resolver(request)
            local_provider = resolver._providers.get('local_file')
            if not local_provider:
                raise HTTPException(500, "本地文件数据源未加载")

            sectors = local_provider.get_sectors_by_stock(stock_code, source=source)

            # 按数据源分组
            grouped = {}
            for s in sectors:
                grouped.setdefault(s['source'], []).append(s)

            return {
                "success": True,
                "data": {
                    "stock_code": stock_code,
                    "sectors": sectors,
                    "grouped": grouped,
                    "total_count": len(sectors),
                }
            }
        except Exception as e:
            logger.error("API: get_sectors_by_stock 异常: %s", e, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=create_error_response("INTERNAL_ERROR", str(e), 500)
            )

    # ─── 9.6: 自选股管理API (type=3) ─────────────────────────────

    @cp_router.get("/favorites")
    async def get_favorites(request: Request):
        """获取自选股列表"""
        logger.info("API: get_favorites")
        try:
            resolver = _get_resolver(request)
            favorites = await resolver.resolve(3)
            return {
                "success": True,
                "data": {
                    "stocks": favorites,
                    "count": len(favorites),
                }
            }
        except Exception as e:
            logger.error("API: get_favorites 异常: %s", e, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=create_error_response("INTERNAL_ERROR", str(e), 500)
            )

    @cp_router.post("/favorites")
    async def add_favorites(request: Request, body: AddFavoritesRequest):
        """批量添加自选股到列表"""
        logger.info("API: add_favorites count=%d", len(body.stocks))
        try:
            # 通过 TQ 接口添加自选股
            tq = request.app.state.tq
            added = []
            failed = []

            for stk in body.stocks:
                try:
                    market_map = {0: 'SZ', 1: 'SH', 2: 'BJ'}
                    market = market_map.get(stk.setcode, 'SZ')
                    stock_code = f"{market}{stk.code}"

                    if hasattr(tq, 'add_favorite'):
                        tq.add_favorite(stock_code)
                        added.append(stk.code)
                    else:
                        failed.append(stk.code)
                except Exception as ex:
                    logger.warning("添加自选股 %s 失败: %s", stk.code, ex)
                    failed.append(stk.code)

            return {
                "success": True,
                "data": {
                    "added_count": len(added),
                    "added_codes": added,
                    "failed_codes": failed,
                },
                "message": f"成功添加 {len(added)} 只，失败 {len(failed)} 只",
            }
        except Exception as e:
            logger.error("API: add_favorites 异常: %s", e, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=create_error_response("INTERNAL_ERROR", str(e), 500)
            )

    @cp_router.delete("/favorites")
    async def remove_favorites(request: Request, body: RemoveFavoritesRequest):
        """批量删除自选股"""
        logger.info("API: remove_favorites count=%d", len(body.stock_codes))
        try:
            tq = request.app.state.tq
            removed = []
            failed = []

            for code in body.stock_codes:
                try:
                    if hasattr(tq, 'remove_favorite'):
                        tq.remove_favorite(code)
                        removed.append(code)
                    else:
                        failed.append(code)
                except Exception as ex:
                    logger.warning("删除自选股 %s 失败: %s", code, ex)
                    failed.append(code)

            return {
                "success": True,
                "data": {
                    "removed_count": len(removed),
                    "removed_codes": removed,
                    "failed_codes": failed,
                },
                "message": f"成功删除 {len(removed)} 只，失败 {len(failed)} 只",
            }
        except Exception as e:
            logger.error("API: remove_favorites 异常: %s", e, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=create_error_response("INTERNAL_ERROR", str(e), 500)
            )

    @cp_router.put("/favorites/refresh")
    async def refresh_favorites(request: Request):
        """手动触发自选股数据刷新"""
        logger.info("API: refresh_favorites")
        try:
            refresh_mgr = _get_refresh_manager(request)
            await refresh_mgr.refresh_favorites(interval=None)
            data = await refresh_mgr.get_latest_data('favorites')
            return {
                "success": True,
                "message": "自选股刷新已触发",
                "data": {
                    "stock_count": len(data),
                    "refreshed_at": datetime.now().isoformat(),
                }
            }
        except Exception as e:
            logger.error("API: refresh_favorites 异常: %s", e, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=create_error_response("INTERNAL_ERROR", str(e), 500)
            )

    # ─── 9.7: 自定义板块管理API (type=4) ─────────────────────────

    @cp_router.get("/blocks")
    async def get_blocks(request: Request, include_members: bool = False):
        """返回所有自定义板块列表"""
        logger.info("API: get_blocks include_members=%s", include_members)
        try:
            storage = request.app.state.storage

            # 查询所有自定义板块
            blocks = []
            if hasattr(storage, 'get_user_block'):
                # 遍历所有板块（需要 list_blocks 方法或直接查询）
                with storage._conn() as conn:
                    rows = conn.execute("""
                        SELECT * FROM user_blocks ORDER BY block_type, block_name
                    """).fetchall()
                    for row in rows:
                        block = dict(row)
                        if include_members and hasattr(storage, 'get_user_block'):
                            full_block = storage.get_user_block(block['block_code'])
                            if full_block:
                                block['members'] = full_block.get('members', [])
                                block['member_count'] = len(block.get('members', []))
                        blocks.append(block)

            return {
                "success": True,
                "data": {
                    "blocks": blocks,
                    "count": len(blocks),
                }
            }
        except Exception as e:
            logger.error("API: get_blocks 异常: %s", e, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=create_error_response("INTERNAL_ERROR", str(e), 500)
            )

    @cp_router.post("/blocks")
    async def create_block(request: Request, body: CreateBlockRequest):
        """创建新的自定义板块"""
        logger.info("API: create_block block_code=%s", body.block_code)
        try:
            storage = request.app.state.storage

            # 准备成员数据
            members = []
            if body.members:
                for idx, stk in enumerate(body.members):
                    market_map = {0: 'SZ', 1: 'SH', 2: 'BJ'}
                    market = market_map.get(stk.setcode, 'SZ')
                    members.append({
                        'stock_code': f"{market}{stk.code}",
                        'name': stk.name or '',
                    })

            # 创建板块记录
            success = storage.create_resolved_block(
                block_code=body.block_code,
                block_name=body.block_name,
                source_sector_id='manual',
                members=members,
                description=body.description,
            )

            if not success:
                raise HTTPException(
                    status_code=409,
                    detail=create_error_response(
                        "CONFLICT",
                        f"板块 '{body.block_code}' 已存在或创建失败",
                        409
                    )
                )

            return {
                "success": True,
                "data": {
                    "block_code": body.block_code,
                    "block_name": body.block_name,
                    "member_count": len(members),
                    "created_at": datetime.now().isoformat(),
                },
                "message": "板块创建成功",
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error("API: create_block 异常: %s", e, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=create_error_response("INTERNAL_ERROR", str(e), 500)
            )

    @cp_router.put("/blocks/{block_code}")
    async def update_block(request: Request, block_code: str, body: UpdateBlockRequest):
        """更新板块基本信息或成员"""
        logger.info("API: update_block block_code=%s", block_code)
        try:
            storage = request.app.state.storage

            # 检查板块是否存在
            existing = storage.get_user_block(block_code) if hasattr(storage, 'get_user_block') else None
            if not existing:
                raise HTTPException(
                    status_code=404,
                    detail=create_error_response("NOT_FOUND", f"板块 '{block_code}' 不存在", 404)
                )

            # 更新基本信息
            updates = []
            params = []
            if body.block_name is not None:
                updates.append("block_name=?")
                params.append(body.block_name)
            if body.description is not None:
                updates.append("description=?")
                params.append(body.description)

            if updates:
                with storage._conn() as conn:
                    conn.execute(
                        f"UPDATE user_blocks SET {', '.join(updates)}, updated_at=datetime('now','localtime') WHERE block_code=?",
                        tuple(params + [block_code])
                    )
                    conn.commit()

            # 更新成员列表（如果提供）
            member_count = 0
            if body.members is not None:
                members_data = []
                for stk in body.members:
                    market_map = {0: 'SZ', 1: 'SH', 2: 'BJ'}
                    market = market_map.get(stk.setcode, 'SZ')
                    members_data.append({
                        'stock_code': f"{market}{stk.code}",
                    })
                member_count = storage.update_user_block_members(block_code, members_data, clear_existing=True)

            return {
                "success": True,
                "data": {
                    "block_code": block_code,
                    "updated_fields": list(body.model_dump(exclude_none=True).keys()),
                    "member_count": member_count,
                },
                "message": "板块更新成功",
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error("API: update_block 异常: %s", e, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=create_error_response("INTERNAL_ERROR", str(e), 500)
            )

    @cp_router.delete("/blocks/{block_code}")
    async def delete_block(request: Request, block_code: str):
        """删除自定义板块及其成员"""
        logger.info("API: delete_block block_code=%s", block_code)
        try:
            storage = request.app.state.storage

            # 检查板块是否存在
            existing = storage.get_user_block(block_code) if hasattr(storage, 'get_user_block') else None
            if not existing:
                raise HTTPException(
                    status_code=404,
                    detail=create_error_response("NOT_FOUND", f"板块 '{block_code}' 不存在", 404)
                )

            # 删除成员和板块记录
            with storage._conn() as conn:
                conn.execute("DELETE FROM user_block_members WHERE block_code=?", (block_code,))
                conn.execute("DELETE FROM user_blocks WHERE block_code=?", (block_code,))
                conn.commit()

            return {
                "success": True,
                "message": f"板块 '{block_code}' 已删除",
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error("API: delete_block 异常: %s", e, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=create_error_response("INTERNAL_ERROR", str(e), 500)
            )

    @cp_router.post("/blocks/{block_code}/members")
    async def add_block_members(request: Request, block_code: str, body: AddMembersRequest):
        """向板块添加成员"""
        logger.info("API: add_block_members block_code=%s count=%d", block_code, len(body.stocks))
        try:
            storage = request.app.state.storage

            # 检查板块是否存在
            existing = storage.get_user_block(block_code) if hasattr(storage, 'get_user_block') else None
            if not existing:
                raise HTTPException(
                    status_code=404,
                    detail=create_error_response("NOT_FOUND", f"板块 '{block_code}' 不存在", 404)
                )

            # 追加成员（不清除已有成员）
            members_data = []
            for stk in body.stocks:
                market_map = {0: 'SZ', 1: 'SH', 2: 'BJ'}
                market = market_map.get(stk.setcode, 'SZ')
                members_data.append({
                    'stock_code': f"{market}{stk.code}",
                })

            count = storage.update_user_block_members(block_code, members_data, clear_existing=False)

            return {
                "success": True,
                "data": {
                    "block_code": block_code,
                    "added_count": count,
                },
                "message": f"成功添加 {count} 只股票到板块",
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error("API: add_block_members 异常: %s", e, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=create_error_response("INTERNAL_ERROR", str(e), 500)
            )

    @cp_router.delete("/blocks/{block_code}/members")
    async def remove_block_members(request: Request, block_code: str, body: RemoveMembersRequest):
        """从板块移除成员"""
        logger.info("API: remove_block_members block_code=%s count=%d", block_code, len(body.stock_codes))
        try:
            storage = request.app.state.storage

            with storage._conn() as conn:
                # 构造 IN 子句
                placeholders = ",".join(["?"] * len(body.stock_codes))
                cursor = conn.execute(
                    f"DELETE FROM user_block_members WHERE block_code=? AND stock_code IN ({placeholders})",
                    tuple([block_code] + body.stock_codes)
                )
                conn.commit()
                deleted_count = cursor.rowcount

            return {
                "success": True,
                "data": {
                    "block_code": block_code,
                    "removed_count": deleted_count,
                },
                "message": f"成功移除 {deleted_count} 只股票",
            }
        except Exception as e:
            logger.error("API: remove_block_members 异常: %s", e, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=create_error_response("INTERNAL_ERROR", str(e), 500)
            )

    @cp_router.put("/blocks/{block_code}/refresh")
    async def refresh_block(request: Request, block_code: str):
        """手动触发指定板块的数据刷新"""
        logger.info("API: refresh_block block_code=%s", block_code)
        try:
            refresh_mgr = _get_refresh_manager(request)
            await refresh_mgr.refresh_custom_block(block_code, interval=None)
            task_key = f'block_{block_code}'
            data = await refresh_mgr.get_latest_data(task_key)
            return {
                "success": True,
                "message": f"板块 '{block_code}' 刷新已触发",
                "data": {
                    "block_code": block_code,
                    "stock_count": len(data),
                    "refreshed_at": datetime.now().isoformat(),
                }
            }
        except Exception as e:
            logger.error("API: refresh_block 异常: %s", e, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=create_error_response("INTERNAL_ERROR", str(e), 500)
            )

    # ─── 9.8: 刷新状态查询API ──────────────────────────────────

    @cp_router.get("/refresh/status")
    async def get_refresh_status(request: Request):
        """查询当前刷新任务的状态"""
        logger.info("API: get_refresh_status")
        try:
            refresh_mgr = _get_refresh_manager(request)
            running_tasks = refresh_mgr.get_running_tasks()

            tasks = {}
            for task_name, is_running in running_tasks.items():
                task_info = {
                    "status": "running" if is_running else "idle",
                    "error_count": 0,
                }
                # 尝试获取最新数据时间戳
                try:
                    data = await refresh_mgr.get_latest_data(task_name)
                    if data:
                        task_info["last_refresh"] = datetime.now().isoformat()
                except Exception:
                    pass
                tasks[task_name] = task_info

            return {
                "success": True,
                "data": {
                    "is_running": refresh_mgr.is_running(),
                    "tasks": tasks,
                }
            }
        except Exception as e:
            logger.error("API: get_refresh_status 异常: %s", e, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=create_error_response("INTERNAL_ERROR", str(e), 500)
            )

    # ─── 9.9: 数据同步API ──────────────────────────────────────

    @cp_router.get("/local-sectors")
    async def get_local_sectors(
        request: Request,
        type: Optional[str] = None,
        category: Optional[str] = None,
        source: Optional[str] = None,
        keyword: Optional[str] = None,
        include_members: bool = False,
        preview_count: int = 5,
    ):
        """获取本地板块列表（按分类分组）。

        Task 7.1: 优先从数据库 storage.get_sectors_catalog() 读取，
        支持按 category/source/keyword 过滤。数据库无数据时降级到
        LocalFileProvider.get_system_sectors()。

        Task 2.2/2.3: 新增 include_members 参数，为 true 时每个板块
        附带 members_preview 字段（前 preview_count 只成分股）。

        Args:
            type: 旧参数，等价于 category（向后兼容）
            category: 分类过滤（concept/industry/index/style/region 等）
            source: 数据源过滤（dfcf/akshare/tdx/local_file 等）
            keyword: 关键词搜索（匹配板块名称）
            include_members: 是否附带成员预览（默认 False）
            preview_count: 成员预览数量（默认 5，仅在 include_members=true 时生效）

        Returns:
            {'concept': [...], 'industry': [...], 'count': N}
            每个板块对象含 sector_id/sector_code/sector_name/category/source/member_count
            include_members=true 时额外含 members_preview: [{stock_code, name}, ...]
        """
        # type 参数作为 category 的向后兼容别名
        if category is None and type is not None:
            category = type
        logger.info("API: get_local_sectors category=%s source=%s keyword=%s", category, source, keyword)
        try:
            storage = request.app.state.storage
            grouped: Dict[str, List[Dict]] = {}
            used_fallback = False

            # 本地数据源直接走 LocalFileProvider（确保使用程序最新实时数据，不依赖数据库缓存）
            # 数据库缓存可能陈旧（如 TDX infoharbor_block.dat 更新后数据库未同步）
            # source=None 时也走 LocalFileProvider（本 API 名为 get_local_sectors，应返回本地数据）
            LOCAL_FILE_SOURCES = {'tdx_local', 'dzh_local', 'ths_local', 'local_file'}
            is_local_source = (source in LOCAL_FILE_SOURCES) or (source is None)
            sectors = []  # 本地数据源跳过数据库读取

            if not is_local_source:
                # 非本地数据源（dfcf/akshare 等）优先从数据库读取
                try:
                    if hasattr(storage, 'get_sectors_catalog'):
                        sectors = storage.get_sectors_catalog(
                            category=category, source=source, keyword=keyword
                        )
                    else:
                        sectors = []
                except Exception as e:
                    logger.warning("get_local_sectors: 数据库读取失败，将降级到本地文件: %s", e)
                    sectors = []

            if sectors and not is_local_source:
                # 数据库路径：为每个板块附加 members 字段
                # 各数据源独立解析，禁止跨数据源名称匹配
                source_to_members: Dict[str, Dict[str, List[str]]] = {}
                local_provider = None
                try:
                    resolver = _get_resolver(request)
                    local_provider = resolver._providers.get('local_file')
                except Exception:
                    local_provider = None
                if local_provider and hasattr(local_provider, 'get_system_sectors'):
                    try:
                        raw_grouped = local_provider.get_system_sectors()
                        source_map = {
                            'tdx': 'tdx_local', 'dzh': 'dzh_local', 'ths': 'ths_local'
                        }
                        for _src_key, _cats in raw_grouped.items():
                            src_name = source_map.get(_src_key, _src_key)
                            src_members: Dict[str, List[str]] = {}
                            for _cat, _sec_list in _cats.items():
                                for _sec in _sec_list:
                                    _name = _sec.get('name', '') or _sec.get('code', '')
                                    _members = _sec.get('members', [])
                                    if _name and _members:
                                        src_members[_name] = _members
                            source_to_members[src_name] = src_members
                    except Exception as e:
                        logger.debug("构建各数据源板块→成分股映射失败: %s", e)

                for sec in sectors:
                    cat = sec.get('category', 'other')
                    sec_id = sec.get('sector_id', '')
                    sec_name = sec.get('sector_name', '')
                    sec_source = sec.get('source', '')
                    # 先从数据库查成分股
                    db_members = storage.get_sector_members(sec_id) if sec_id else []
                    if db_members:
                        # 数据库有成分股，直接使用
                        members_field = [{'stock_code': m.get('stock_code', ''), 'name': m.get('name', '')} for m in db_members]
                    else:
                        # 从同数据源的映射查找（仅匹配相同数据源，禁止跨源匹配）
                        src_map = source_to_members.get(sec_source, {})
                        raw_codes = src_map.get(sec_name, [])
                        if raw_codes:
                            members_field = [{'stock_code': c, 'name': ''} for c in raw_codes if c]
                        else:
                            members_field = []
                    grouped.setdefault(cat, []).append({
                        'sector_id': sec_id,
                        'sector_code': sec.get('sector_code', ''),
                        'sector_index_code': sec.get('sector_index_code', ''),
                        'sector_name': sec_name,
                        'category': cat,
                        'source': sec_source,
                        'member_count': len(members_field) if members_field else sec.get('member_count', 0),
                        'members': members_field,
                    })
            else:
                # 数据库无数据，降级到 LocalFileProvider
                # 本地数据源也直接走此路径（确保使用程序最新实时数据）
                used_fallback = True
                resolver = _get_resolver(request)
                local_provider = resolver._providers.get('local_file')
                # 获取股票名映射，用于补全 members 的 name 字段
                stock_names: Dict[str, str] = {}
                if local_provider and hasattr(local_provider, 'get_stock_name_map'):
                    try:
                        stock_names = local_provider.get_stock_name_map()
                    except Exception as e:
                        logger.debug("获取股票名映射失败: %s", e)
                if local_provider and hasattr(local_provider, 'get_system_sectors'):
                    raw_grouped = local_provider.get_system_sectors()
                    # 新格式：{source_key: {cat: [sec_list]}}
                    # 展平为 {cat: [sec_list]} 并保留 source 信息
                    source_map = {
                        'tdx': 'tdx_local', 'dzh': 'dzh_local', 'ths': 'ths_local'
                    }
                    for source_key, categories in raw_grouped.items():
                        source_name = source_map.get(source_key, source_key)
                        # source 参数过滤：只返回指定数据源的板块
                        if source and source != source_name:
                            continue
                        for cat, sec_list in categories.items():
                            if category and cat != category:
                                continue
                            items = []
                            for sec in sec_list:
                                sec_name = sec.get('name', '') or sec.get('code', '')
                                # keyword 过滤
                                if keyword and keyword not in sec_name:
                                    continue
                                # 成分股列表：代码→{stock_code, name} 字典，补全个股名
                                # 名称缺失时用代码作兜底（比空字符串更友好）
                                raw_members = sec.get('members', [])
                                members_field = [
                                    {'stock_code': c, 'name': stock_names.get(c, c)}
                                    for c in raw_members if c
                                ]
                                items.append({
                                    'sector_id': f'{source_name}_{cat}_{sec_name}',
                                    'sector_code': sec.get('code', ''),
                                    'sector_index_code': sec.get('sector_index_code', ''),
                                    'sector_name': sec_name,
                                    'category': cat,
                                    'source': source_name,
                                    'member_count': len(members_field),
                                    'members': members_field,
                                    'level': sec.get('level', 0),
                                    'parent_id': sec.get('parent_id', ''),
                                    'parent_name': sec.get('parent_name', ''),
                                    'is_category': sec.get('is_category', False),
                                })
                            if items:
                                grouped.setdefault(cat, []).extend(items)

            total = sum(len(v) for v in grouped.values())

            # Task 2.2/2.3: include_members=true 时为每个板块附带成员预览
            if include_members:
                # 性能考虑：最多对前 50 个板块预览
                preview_sector_limit = 50
                processed = 0
                for sec_list in grouped.values():
                    for sec in sec_list:
                        if processed >= preview_sector_limit:
                            break
                        # 优先使用已有 members 字段（本地数据源直接解析的成分股）
                        existing_members = sec.get('members', [])
                        if existing_members:
                            sec['members_preview'] = existing_members[:preview_count]
                        else:
                            sec_id = sec.get('sector_id', '')
                            if sec_id:
                                try:
                                    raw_members = storage.get_sector_members(sec_id, limit=preview_count)
                                    sec['members_preview'] = [
                                        {'stock_code': m.get('stock_code', ''), 'name': m.get('name', '')}
                                        for m in raw_members
                                    ]
                                except Exception as e:
                                    logger.debug("获取板块 %s 成员预览失败: %s", sec_id, e)
                                    sec['members_preview'] = []
                            else:
                                sec['members_preview'] = []
                        processed += 1
                    if processed >= preview_sector_limit:
                        break

            return {
                "success": True,
                "data": {
                    "sectors": grouped,
                    "count": total,
                    "source": "local_file" if used_fallback else "database",
                },
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error("API: get_local_sectors 异常: %s", e, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=create_error_response("INTERNAL_ERROR", str(e), 500)
            )

    # ─── 9.10: 板块指数/自定义板块/自选股 独立查询 API ────────
    # 三类数据各自独立端点，不混入 /local-sectors，前端可分别调用显示

    @cp_router.get("/sector-indices")
    async def get_sector_indices(
        request: Request,
        source: Optional[str] = None,
        sub_type: Optional[str] = None,
    ):
        """获取各软件的板块指数列表（仅定义，无成分股）。

        板块指数代码（880xxx）是衡量板块走势的指数代码，与板块本身一一对应。
        - 仅 TDX 有独立板块指数定义文件（tdxzs.cfg，605 条）
        - DZH/THS 当前无独立板块指数定义文件，对应数据源返回空列表
        - 成分股查询请用 `/sectors/by-index/{index_code}/members` 端点

        Args:
            source: 数据源过滤（tdx/dzh/ths，None=全部）
            sub_type: 子类型过滤（industry/region/concept/style，None=全部）

        Returns:
            {'success': True, 'data': {'sectors': {tdx: [...], dzh: [...], ths: [...]},
             'count': N}}
        """
        logger.info("API: get_sector_indices source=%s sub_type=%s", source, sub_type)
        try:
            resolver = _get_resolver(request)
            local_provider = resolver._providers.get('local_file')
            if not local_provider or not hasattr(local_provider, 'get_sector_index_list'):
                return {"success": True, "data": {"sectors": {}, "count": 0}}
            grouped = local_provider.get_sector_index_list(source=source)
            # 子类型过滤
            if sub_type:
                for src, sec_list in grouped.items():
                    grouped[src] = [s for s in sec_list if s.get('sub_type') == sub_type]
            total = sum(len(v) for v in grouped.values())
            return {
                "success": True,
                "data": {
                    "sectors": grouped,
                    "count": total,
                },
            }
        except Exception as e:
            logger.error("API: get_sector_indices 异常: %s", e, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=create_error_response("INTERNAL_ERROR", str(e), 500)
            )

    @cp_router.get("/custom-blocks")
    async def get_custom_blocks(
        request: Request,
        source: Optional[str] = None,
        include_members: bool = False,
        preview_count: int = 10,
    ):
        """获取各软件的自定义板块（含成分股）。

        各数据源独立解析：
        - TDX: blocknew/*.blk（含成分股）
        - DZH: cfg/block.ini SysBlock + ABK"自定义板块"section 成分股
        - THS: hexin/Block.cfg 或 custom_block/ 目录

        Args:
            source: 数据源过滤（tdx/dzh/ths，None=全部）
            include_members: 是否在响应中附带完整成员列表（默认 False 仅返回 member_count）
            preview_count: 当 include_members=False 时附带的预览数量（默认 10）

        Returns:
            {'success': True, 'data': {'sectors': {tdx: [...], dzh: [...], ths: [...]},
             'count': N}}
        """
        logger.info("API: get_custom_blocks source=%s include_members=%s",
                    source, include_members)
        try:
            resolver = _get_resolver(request)
            local_provider = resolver._providers.get('local_file')
            if not local_provider or not hasattr(local_provider, 'get_custom_blocks_grouped'):
                return {"success": True, "data": {"sectors": {}, "count": 0}}
            grouped = local_provider.get_custom_blocks_grouped(source=source)
            # include_members=False 时不返回完整 members，仅返回预览
            if not include_members:
                for src, sec_list in grouped.items():
                    for sec in sec_list:
                        full = sec.get('members', []) or []
                        sec['members_preview'] = full[:preview_count]
                        sec.pop('members', None)
            total = sum(len(v) for v in grouped.values())
            return {
                "success": True,
                "data": {
                    "sectors": grouped,
                    "count": total,
                },
            }
        except Exception as e:
            logger.error("API: get_custom_blocks 异常: %s", e, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=create_error_response("INTERNAL_ERROR", str(e), 500)
            )

    @cp_router.get("/favorites-list")
    async def get_favorites_list(
        request: Request,
        source: Optional[str] = None,
        include_members: bool = False,
    ):
        """获取各软件的自选股（含完整成员列表）。

        各数据源独立解析：
        - TDX: zxg.blk
        - DZH: cfg/zxg.cfg
        - THS: hexin/ZXG.cfg

        Args:
            source: 数据源过滤（tdx/dzh/ths，None=全部）
            include_members: 是否在响应中附带完整成员列表（默认 False 仅返回 member_count）

        Returns:
            {'success': True, 'data': {'sectors': {tdx: [...], dzh: [...], ths: [...]},
             'count': N}}
            每个数据源仅返回一条记录（该客户端的全部自选股）。
        """
        logger.info("API: get_favorites_list source=%s include_members=%s",
                    source, include_members)
        try:
            resolver = _get_resolver(request)
            local_provider = resolver._providers.get('local_file')
            if not local_provider or not hasattr(local_provider, 'get_favorites_grouped'):
                return {"success": True, "data": {"sectors": {}, "count": 0}}
            grouped = local_provider.get_favorites_grouped(source=source)
            if not include_members:
                for src, sec_list in grouped.items():
                    for sec in sec_list:
                        sec.pop('members', None)
            total = sum(len(v) for v in grouped.values())
            return {
                "success": True,
                "data": {
                    "sectors": grouped,
                    "count": total,
                },
            }
        except Exception as e:
            logger.error("API: get_favorites_list 异常: %s", e, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=create_error_response("INTERNAL_ERROR", str(e), 500)
            )

    # ─── 9.11: 市场分类树查询API ───────────────────────────────

    @cp_router.get("/markets")
    async def get_markets(request: Request):
        """返回市场分类树（基于 DZH 文档的静态分类）。

        Task 2.4: 返回 SH/SZ 交易所下的市场分类列表，用于备选池属性面板
        按市场筛选板块。结构为 {交易所: [{name, code}, ...]}。
        """
        # 静态市场分类树（基于 DZH 文档）
        market_tree = {
            "SH": [
                {"name": "上证A股", "code": "SH#上证A股"},
                {"name": "上证B股", "code": "SH#上证B股"},
                {"name": "上证基金", "code": "SH#上证基金"},
                {"name": "上证债券", "code": "SH#上证债券"},
                {"name": "上证转债", "code": "SH#上证转债"},
            ],
            "SZ": [
                {"name": "深证A股", "code": "SZ#深证A股"},
                {"name": "深证B股", "code": "SZ#深证B股"},
                {"name": "创业板", "code": "SZ#创业板"},
                {"name": "中小企业", "code": "SZ#中小企业"},
            ],
        }
        return {"success": True, "data": market_tree}

    @cp_router.post("/sync/stocks")
    async def sync_stocks(request: Request, source: str = "akshare"):
        """从指定数据源同步股票基础信息到数据库"""
        logger.info("API: sync_stocks source=%s", source)
        try:
            storage = request.app.state.storage
            tq = request.app.state.tq
            started_at = datetime.now()
            synced_count = 0
            updated_count = 0

            providers = getattr(tq, '_manager', None)
            if providers:
                provider = getattr(providers, f'{source}_provider', None) or providers._providers.get(source)
                if provider and hasattr(provider, 'get_all_a_stocks'):
                    stocks_data = await provider.get_all_a_stocks()
                    if stocks_data:
                        synced_count = storage.upsert_stocks(stocks_data)

            completed_at = datetime.now()
            return {
                "success": True,
                "message": "同步完成",
                "data": {
                    "synced_count": synced_count,
                    "updated_count": updated_count,
                    "source": source,
                    "started_at": started_at.isoformat(),
                    "completed_at": completed_at.isoformat(),
                }
            }
        except Exception as e:
            logger.error("API: sync_stocks 异常: %s", e, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=create_error_response("INTERNAL_ERROR", str(e), 500)
            )

    @cp_router.post("/sync/sectors")
    async def sync_sectors(request: Request, source: str = "tdx"):
        """从指定数据源同步板块数据到数据库"""
        logger.info("API: sync_sectors source=%s", source)
        try:
            storage = request.app.state.storage
            resolver = _get_resolver(request)
            started_at = datetime.now()
            synced_sectors = 0
            synced_members = 0

            # 通过 resolver 的分类树接口获取板块数据
            tree_data = await resolver.get_category_tree(source=source, category='all')

            if tree_data and tree_data.get('tree'):
                sectors_data = []
                for cat in tree_data['tree']:
                    # source='local' 时按分类节点 id 提取真实分类（如 cat_concept → concept）
                    if source == 'local':
                        cat_id = cat.get('id', '')
                        sector_category = cat_id[4:] if cat_id.startswith('cat_') else cat_id
                    else:
                        sector_category = 'concept'
                    for sec in cat.get('children', []):
                        sectors_data.append({
                            'sector_id': sec.get('sector_id', ''),
                            'sector_code': sec.get('id', ''),
                            'sector_name': sec.get('name', ''),
                            'category': sector_category,
                            'source': source,
                            'member_count': sec.get('member_count', 0),
                        })
                if sectors_data:
                    synced_sectors = storage.upsert_sectors(sectors_data)

            completed_at = datetime.now()
            return {
                "success": True,
                "data": {
                    "synced_sectors": synced_sectors,
                    "synced_members": synced_members,
                    "source": source,
                    "started_at": started_at.isoformat(),
                    "completed_at": completed_at.isoformat(),
                }
            }
        except Exception as e:
            logger.error("API: sync_sectors 异常: %s", e, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=create_error_response("INTERNAL_ERROR", str(e), 500)
            )

    @cp_router.get("/sync/status")
    async def get_sync_status(request: Request):
        """查询最近一次同步的状态和进度"""
        logger.info("API: get_sync_status")
        try:
            storage = request.app.state.storage

            # 查询最近更新的股票和板块数量作为同步状态指标
            with storage._conn() as conn:
                stocks_row = conn.execute(
                    "SELECT COUNT(*), MAX(updated_at) FROM stocks"
                ).fetchone()
                sectors_row = conn.execute(
                    "SELECT COUNT(*), MAX(updated_at) FROM sectors"
                ).fetchone()

            return {
                "success": True,
                "data": {
                    "stocks": {
                        "total_count": stocks_row[0] if stocks_row else 0,
                        "last_sync": stocks_row[1] if stocks_row else None,
                    },
                    "sectors": {
                        "total_count": sectors_row[0] if sectors_row else 0,
                        "last_sync": sectors_row[1] if sectors_row else None,
                    },
                }
            }
        except Exception as e:
            logger.error("API: get_sync_status 异常: %s", e, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=create_error_response("INTERNAL_ERROR", str(e), 500)
            )

    @cp_router.post("/sync/all")
    async def sync_all(request: Request):
        """全量同步所有数据源到数据库（Task 7.7）。

        调用 DatabaseSyncService.sync_all()，依次同步股票列表、板块、
        板块成分股、自选股、自定义板块。
        """
        logger.info("API: sync_all 全量同步开始")
        try:
            sync_service = _get_db_sync_service(request)
            started_at = datetime.now()
            report = await sync_service.sync_all()
            completed_at = datetime.now()
            return {
                "success": True,
                "message": "全量同步完成",
                "data": {
                    "report": report,
                    "started_at": started_at.isoformat(),
                    "completed_at": completed_at.isoformat(),
                },
            }
        except Exception as e:
            logger.error("API: sync_all 异常: %s", e, exc_info=True)
            return {
                "success": False,
                "error": str(e),
            }

    @cp_router.post("/sync/source/{source}")
    async def sync_by_source(request: Request, source: str):
        """按数据源同步板块和成分股到数据库（Task 8）。

        支持的 source 值：
            - tdx_local: 仅同步通达信本地文件板块
            - dzh_local: 仅同步大智慧本地文件板块
            - ths_local: 仅同步同花顺本地文件板块
            - dfcf:      仅同步东方财富板块
            - all:       同步所有数据源（等价于 /sync/all 的板块部分）

        调用 DatabaseSyncService.sync_sector_members_by_source(source)，
        先同步板块列表，再同步成分股，返回该数据源的同步结果。
        """
        valid_sources = {'tdx_local', 'dzh_local', 'ths_local', 'dfcf', 'all'}
        if source not in valid_sources:
            raise HTTPException(
                status_code=400,
                detail=create_error_response(
                    "INVALID_SOURCE",
                    f"无效的 source 参数: {source}，可选值: "
                    f"tdx_local/dzh_local/ths_local/dfcf/all",
                    400,
                ),
            )
        logger.info("API: sync_by_source source=%s", source)
        try:
            sync_service = _get_db_sync_service(request)
            started_at = datetime.now()
            started_ts = time.time()

            if source == 'all':
                # 全量同步板块和成分股
                sectors_report = await sync_service.sync_sectors(source='all')
                members_report = await sync_service.sync_sector_members(source='all')
                report = {
                    'source': 'all',
                    'sectors': sectors_report,
                    'sector_members': members_report,
                }
            else:
                # 按子数据源同步（先板块后成分股）
                report = await sync_service.sync_sector_members_by_source(source)

            completed_ts = time.time()
            completed_at = datetime.now()
            elapsed_ms = int((completed_ts - started_ts) * 1000)

            # 提取汇总数据
            providers = report.get('sectors', {}).get('providers', {})
            total_sectors = sum(
                p.get('count', 0) for p in providers.values()
                if isinstance(p, dict)
            )
            members_providers = report.get(
                'sector_members', {}).get('providers', {})
            total_members = sum(
                p.get('count', 0) for p in members_providers.values()
                if isinstance(p, dict)
            )

            return {
                "success": True,
                "message": f"数据源 {source} 同步完成",
                "data": {
                    "source": source,
                    "report": report,
                    "summary": {
                        "total_sectors": total_sectors,
                        "total_members": total_members,
                        "elapsed_ms": elapsed_ms,
                    },
                    "started_at": started_at.isoformat(),
                    "completed_at": completed_at.isoformat(),
                },
            }
        except Exception as e:
            logger.error(
                "API: sync_by_source source=%s 异常: %s",
                source, e, exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "source": source,
            }

    @cp_router.post("/refresh-runtime")
    async def refresh_runtime(request: Request):
        """股票池运行中手动触发自选股/自定义板块刷新（Task 6.4）。

        请求体（可选）:
            {"block_code": "CSBK_TEST"}  # 指定自定义板块代码，不传则仅刷新自选股

        Returns:
            {'success': True, 'data': {'favorites_count': N, 'block_code': ..., 'block_count': N}}
        """
        logger.info("API: refresh_runtime 手动触发运行时刷新")
        try:
            # 解析可选请求体
            block_code = None
            try:
                body = await request.json()
                if isinstance(body, dict):
                    block_code = body.get("block_code")
            except Exception:
                # 无请求体或非 JSON，忽略
                pass

            refresh_mgr = _get_refresh_manager(request)
            resolver = _get_resolver(request)
            result: Dict[str, Any] = {"favorites_count": 0}

            # 刷新自选股（type=3）一次性强制刷新
            try:
                favorites = await resolver.resolve(3, force_refresh=True)
                if favorites:
                    # 更新刷新管理器快照
                    if hasattr(refresh_mgr, '_update_snapshot_cow'):
                        refresh_mgr._update_snapshot_cow('favorites', favorites)
                    if hasattr(refresh_mgr, '_notify_change'):
                        refresh_mgr._notify_change('favorites', favorites)
                    result["favorites_count"] = len(favorites)
            except Exception as e:
                logger.warning("refresh_runtime: 刷新自选股失败: %s", e)
                result["favorites_error"] = str(e)

            # 刷新指定自定义板块（type=4）一次性刷新
            if block_code:
                try:
                    await refresh_mgr.refresh_custom_block(block_code, interval=None)
                    # 读取最新快照获取数量
                    snapshot_key = f'block_{block_code}'
                    snapshot = refresh_mgr._latest_data.get(snapshot_key, [])
                    result["block_code"] = block_code
                    result["block_count"] = len(snapshot) if snapshot else 0
                except Exception as e:
                    logger.warning("refresh_runtime: 刷新自定义板块 %s 失败: %s", block_code, e)
                    result["block_code"] = block_code
                    result["block_error"] = str(e)

            return {"success": True, "data": result}
        except Exception as e:
            logger.error("API: refresh_runtime 异常: %s", e, exc_info=True)
            return {
                "success": False,
                "error": str(e),
            }

    # 将备选池路由挂载到主路由器
    router.include_router(cp_router)

    return router


# ══════════════════════════════════════════════════════════════════════
#  内部辅助函数（来自 meta_api.py）
# ══════════════════════════════════════════════════════════════════════

def _get_resolver(request: Request):
    """获取 CandidatePoolResolver 实例"""
    from services.data import CandidatePoolResolver, CandidatePoolRefreshManager

    app_state = request.app.state

    # 延迟初始化：仅在首次调用时创建 resolver 和 refresh_manager
    if not hasattr(app_state, '_candidate_pool_resolver'):
        storage = app_state.storage
        tq = app_state.tq

        # 构建 providers 字典
        providers = {}

        # 优先注入 LocalFileProvider（本地文件数据源）
        try:
            from services.providers import LocalFileProvider
            providers['local_file'] = LocalFileProvider()
            logger.info("已加载 LocalFileProvider 作为备选池优先数据源")
        except Exception as e:
            logger.warning("加载 LocalFileProvider 失败，将降级到运行时数据源: %s", e)

        # 注入 tq_dll / akshare 等运行时 providers（保留原有逻辑）
        manager = getattr(tq, '_manager', None)
        if manager:
            providers.update(getattr(manager, '_providers', {}) or {})

        app_state._candidate_pool_resolver = CandidatePoolResolver(storage, providers)
        app_state._candidate_pool_refresh_manager = CandidatePoolRefreshManager(
            app_state._candidate_pool_resolver
        )

        # 启动刷新管理器
        import asyncio
        asyncio.create_task(app_state._candidate_pool_refresh_manager.start())

    return app_state._candidate_pool_resolver


def _get_refresh_manager(request: Request):
    """获取 CandidatePoolRefreshManager 实例"""
    _get_resolver(request)  # 确保 refresh_manager 已初始化
    return request.app.state._candidate_pool_refresh_manager


def _get_db_sync_service(request: Request):
    """获取 DatabaseSyncService 实例（延迟初始化）。

    复用 _get_resolver() 中已构建的 providers 字典（包含 local_file /
    dfcf / akshare / tq_dll 等），与 storage 一起注入 DatabaseSyncService。
    """
    from services.storage import DatabaseSyncService

    app_state = request.app.state
    if not hasattr(app_state, '_db_sync_service'):
        storage = app_state.storage
        # 复用 resolver 已初始化的 providers 字典
        resolver = _get_resolver(request)
        providers = getattr(resolver, '_providers', {}) or {}
        app_state._db_sync_service = DatabaseSyncService(storage, providers)
        logger.info("已初始化 DatabaseSyncService，providers=%s", list(providers.keys()))

    return app_state._db_sync_service

# ══════════════════════════════════════════════════════════════════════
#  Part 2: 来自 system_api.py（合并自 run_api / formula_api / import_api / dzh_api / json_api）
# ══════════════════════════════════════════════════════════════════════

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
    from core.schemas import TdxFuncModel, TdxPsattModel, TdxSpinfoModel

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
        # SubTask 22.5: 命令类端点改为事件发布——bus 引用提前获取，供后续 publish 使用
        bus: "EventBus" = request.app.state.bus

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
            # SubTask 22.5: 命令类端点——发布 ConfigChanged 事件通知 DataSource 模块
            # （过渡期与 engine.set_tq_adapter 并行，后续删除 engine 直调）
            try:
                bus.publish(ConfigChanged(changed_tables=["data_sources"]))
            except Exception as _cfg_ex:
                logger.warning("set_tq_adapter 发布 ConfigChanged 失败: %s", _cfg_ex)

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

        # Task 16: 事件驱动并行通道——发布 PoolLoaded 事件触发 ExecutionModule 编译
        # （与下方 engine.run_pool() 并行，不破坏现有执行路径）
        # SubTask 22.5: 命令类端点——PoolLoaded 事件已发布，下游模块订阅编译
        try:
            bus.publish(PoolLoaded(pool_config=pool_config, source_format="json"))
        except Exception as _bus_ex:
            logger.warning("run_pool 发布 PoolLoaded 事件失败: %s", _bus_ex)

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
            # SubTask 22.5: 命令类端点——执行完成后发布 PoolLoaded 事件通知下游模块
            # （PoolLoaded 在执行前已发布一次，此处为执行后再次发布以触发下游刷新；
            #   后续待 ExecutionModule 完整接管 run_pool 后删除 engine 直调）
            try:
                bus.publish(PoolLoaded(pool_config=pool_config, source_format="json"))
            except Exception as _exec_ex:
                logger.warning("run_pool 执行后发布 PoolLoaded 事件失败: %s", _exec_ex)
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
# 注：原 replay_api.py 通过 `from execution_api import _enrich_tdx_node_data`
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

        # Task 16: 事件驱动并行通道——通过 RuntimeModeModule 切换模式并发布 ReplayStarted 事件
        # （与上方 KLineReplayEngine 直接调用并行，不破坏现有回放路径）
        try:
            runtime_mode = request.app.state.runtime_mode
            runtime_mode.switch_mode("replay")
            runtime_mode.start_replay(
                session_id=session_id,
                start_ts=0.0,
                end_ts=0.0,
                codes=list(result.get("codes", [])),
            )
        except Exception as _rm_ex:
            logger.warning("replay_start 发布 RuntimeMode 事件失败: %s", _rm_ex)

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
# 注：原 sim_api.py 通过 `from execution_api import _enrich_tdx_node_data`
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
        # 直接传 config（浏览器 event-panel 路径）时 pool_id 可能为空，
        # 用 config.id 兜底，避免 save_pool_node 的 NOT NULL 约束失败。
        if not pool_id and isinstance(config, dict):
            pool_id = config.get("id") or "sim_demo"

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

        # 确保节点存在于 pool_node 表，避免 stock_transfer_log 外键约束失败。
        # 直接传 config 启动仿真时 pool_id 可能不在 storage 中，先确保父池行存在，
        # 并将节点写入做成 best-effort（FK 失败不影响仿真启动）。
        storage = request.app.state.storage
        try:
            if hasattr(storage, "save_pool"):
                storage.save_pool(pool_id, config.get("name", pool_id),
                                  json.dumps(config, ensure_ascii=False), config)
        except Exception:
            pass
        for n in config.get('nodes', []):
            nid = n.get('id', '')
            if nid:
                try:
                    storage.save_pool_node(nid, pool_id, n.get('type', ''), n.get('label', ''))
                except Exception:
                    pass

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

        # Task 16: 事件驱动并行通道——通过 RuntimeModeModule 切换到仿真模式并设置速度
        # （与上方 RuntimeSimulator 直接调用并行，不破坏现有仿真路径）
        try:
            runtime_mode = request.app.state.runtime_mode
            runtime_mode.switch_mode("simulation")
            runtime_mode.set_simulation_speed(float(speed))
        except Exception as _rm_ex:
            logger.warning("sim_start 发布 RuntimeMode 事件失败: %s", _rm_ex)

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
        logger.warning("[STEP-DEBUG] sim_control handler entered")
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
                logger.warning("[STEP-DEBUG] entering step branch, params=%s", params)
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
                logger.warning("[STEP-DEBUG] effective_delta=%s, calling step_with_snapshot", effective_delta)
                import concurrent.futures
                _step_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                result = await asyncio.get_event_loop().run_in_executor(_step_pool, simulator.step_with_snapshot, effective_delta)
                logger.warning("[STEP-DEBUG] step_with_snapshot returned, result keys=%s", list(result.keys()) if isinstance(result, dict) else type(result))
                # Task 16: 事件驱动并行通道——发布 SimulationStep 事件
                try:
                    runtime_mode = request.app.state.runtime_mode
                    runtime_mode.step_simulation(step_idx=int(result.get("step", 0)) if isinstance(result, dict) else 0)
                except Exception as _rm_ex:
                    logger.warning("sim_control step 发布 SimulationStep 事件失败: %s", _rm_ex)
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
                # Task 16: 事件驱动并行通道——同步仿真速度到 RuntimeModeModule
                try:
                    runtime_mode = request.app.state.runtime_mode
                    runtime_mode.set_simulation_speed(new_speed)
                except Exception as _rm_ex:
                    logger.warning("sim_control speed 同步仿真速度失败: %s", _rm_ex)
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

    @router.get("/batch_step")
    async def sim_batch_step(session_id: str, steps: int = 10, delta: float = 1.0, request: Request = None):
        """服务端批量步进：一次 HTTP 调用内推进多步，绕过浏览器每步 ~14s 的瓶颈。
        delta 为每步虚拟秒数（默认 1.0）；设为 60 可一步跨过 1 分钟边界以快速合成 K 线。"""
        simulators = getattr(request.app.state, "_simulators", {})
        simulator = simulators.get(session_id)
        if not simulator:
            return {"code": 1, "msg": f"会话不存在: {session_id}", "data": None}
        steps = max(1, min(int(steps), 2000))
        done = 0
        last_err = None
        for _ in range(steps):
            try:
                simulator.step(delta)
                done += 1
            except Exception as e:
                last_err = str(e)
                break
        snap = simulator.get_state_snapshot()
        return {
            "code": 0,
            "msg": f"已步进 {done} 步（delta={delta}）" + (f"，中止于: {last_err}" if last_err else ""),
            "data": {
                "session_id": session_id,
                "stepped": done,
                "clock": snap.get("clock"),
                "step": snap.get("step"),
                "pool_counts": snap.get("pool_counts"),
            },
        }

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
            # 诊断：暴露 bus 内所有事件类型分布，便于排查事件链断点
            bus_diag = {}
            sim_bus = getattr(simulator, "_bus", None)
            if sim_bus is not None and hasattr(sim_bus, "get_events"):
                try:
                    bus_events = sim_bus.get_events()
                    bus_diag["bus_total"] = len(bus_events)
                    type_count = {}
                    for ev in bus_events:
                        ev_type = type(ev).__name__
                        type_count[ev_type] = type_count.get(ev_type, 0) + 1
                    bus_diag["bus_type_distribution"] = type_count
                    bus_diag["sim_bus_id"] = id(sim_bus)
                except Exception as _ex:
                    bus_diag["error"] = str(_ex)
            # 诊断：pe event_bus 与 sim_bus 是否同一实例
            pe_diag = {}
            try:
                pe = getattr(simulator._engine, "_pool_engine", None) if simulator._engine else None
                if pe is not None:
                    pe_bus = pe._components.get("event_bus") if hasattr(pe, "_components") else None
                    pe_diag["pe_event_bus_id"] = id(pe_bus) if pe_bus is not None else None
                    pe_diag["same_as_sim_bus"] = (pe_bus is sim_bus)
                    # 同时收集 pe event_bus 的事件分布
                    if pe_bus is not None and hasattr(pe_bus, "get_events"):
                        pe_events = pe_bus.get_events()
                        pe_type_count = {}
                        for ev in pe_events:
                            ev_type = type(ev).__name__
                            pe_type_count[ev_type] = pe_type_count.get(ev_type, 0) + 1
                        pe_diag["pe_event_bus_total"] = len(pe_events)
                        pe_diag["pe_type_distribution"] = pe_type_count
            except Exception as _ex:
                pe_diag["error"] = str(_ex)
            return {
                "code": 0,
                "msg": "ok",
                "data": {
                    "events": events,
                    "total": total,
                    "since": since,
                    "limit": limit,
                    "has_more": (since + limit) < total,
                    "diag_sim_bus": bus_diag,
                    "diag_pe_bus": pe_diag,
                }
            }
        except Exception as e:
            return {"code": 1, "msg": f"获取事件失败: {e}", "data": None}

    @router.get("/bars")
    async def sim_bars(session_id: str, request: Request, code: str = "", period: str = "1min"):
        """获取某标的的K线bar（复用 simulator.get_bars 实盘同构结构）。

        event-panel.js 调用：/api/sim/bars?session_id=..&code=..&period=1min|5min
        返回 {code:0, data:{bars:[{time,open,high,low,close,volume}], formula_result, position}}
        """
        simulators = getattr(request.app.state, "_simulators", {})
        simulator = simulators.get(session_id)
        if not simulator:
            return {"code": 1, "msg": f"模拟会话不存在: {session_id}", "data": None}

        try:
            period = period if period in ("1min", "5min") else "1min"
            all_bars = simulator.get_bars(period)
            # 若未指定 code，取第一只已合成K线的标的，便于默认展示
            if not code and all_bars:
                code = sorted(all_bars.keys())[0]
            raw = all_bars.get(code)
            bars = []
            if raw is not None:
                if hasattr(raw, "to_dict"):
                    # DataFrame（1min 路径）
                    recs = raw.to_dict(orient="records")
                elif isinstance(raw, dict):
                    recs = [raw]
                else:
                    recs = list(raw)
                for r in recs:
                    bars.append({
                        "time": int(r.get("time", 0)),
                        "open": float(r.get("open", 0) or 0),
                        "high": float(r.get("high", 0) or 0),
                        "low": float(r.get("low", 0) or 0),
                        "close": float(r.get("close", 0) or 0),
                        "volume": int(r.get("volume", 0) or 0),
                    })

            # 持仓：从引擎的 TradeExecutor 取该标的当前持仓
            position = None
            try:
                te = getattr(simulator._engine, "trade_executor", None)
                if te is not None and hasattr(te, "get_position"):
                    position = te.get_position(code)
            except Exception:
                position = None

            # 公式结果：基于该标的 K 线算最近一次 KDJ/MACD 简述（轻量，便于展示）
            formula_result = ""
            try:
                if len(bars) >= 2:
                    closes = [b["close"] for b in bars]
                    formula_result = f"bars={len(bars)} last_close={closes[-1]:.2f} change={(closes[-1]-closes[0]):.2f}"
            except Exception:
                formula_result = ""

            return {
                "code": 0,
                "msg": "ok",
                "data": {
                    "session_id": session_id,
                    "code": code,
                    "period": period,
                    "bars": bars,
                    "formula_result": formula_result,
                    "position": position,
                },
            }
        except Exception as e:
            return {"code": 1, "msg": f"获取事件失败: {e}", "data": None}

    @router.get("/pool_config")
    async def sim_pool_config(request: Request, pool_id: str = "sim_demo"):
        """返回演示股票池配置（event-panel 启动仿真用）。"""
        try:
            from pathlib import Path
            cfg_path = Path(__file__).parent / "config" / f"{pool_id}_pool.json"
            if not cfg_path.exists():
                return {"code": 1, "msg": f"配置不存在: {pool_id}", "data": None}
            import json
            with open(cfg_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            return {"code": 0, "msg": "ok", "data": {"config": config, "pool_id": pool_id}}
        except Exception as e:
            return {"code": 1, "msg": f"读取配置失败: {e}", "data": None}

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
_BASE = Path(__file__).parent
_CONFIG = _BASE / "config"
_BUILTIN_FORMULAS_PATH = _CONFIG / "data" / "builtin_formulas.json"
_CUSTOM_FORMULAS_PATH = _CONFIG / "data" / "custom_formulas.json"


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


from services.providers import decode_formula as _decode_formula


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
    from converters import (
        get_all_cell_types,
        get_cell_type_info,
        load_dzh_market_mappings,
        _detect_topology_mode,
        _decode_type200_attr,
        _decode_type201_attr,
        _decode_flow_attr,
        decode_action,
    )
    from converters import import_pool_from_json, export_pool_to_json
    from services.tq_adapter import DZH_COL_MAP, TqAdapter
    from core.runtime_mode_module import KLineReplayEngine
    from services.storage import safe_path_join
except ImportError:
    from converters import (
        get_all_cell_types,
        get_cell_type_info,
        load_dzh_market_mappings,
        _detect_topology_mode,
        _decode_type200_attr,
        _decode_type201_attr,
        _decode_flow_attr,
        decode_action,
    )
    from converters import import_pool_from_json, export_pool_to_json
    from services.tq_adapter import DZH_COL_MAP, TqAdapter
    from runtime_mode_module import KLineReplayEngine
    from services.storage import safe_path_join


# 文件上传大小限制：10MB
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10MB


_this_dir = Path(__file__).parent
_config_dir = _this_dir / "config"
_MODULES_JSON_PATH = _config_dir / "architecture" / "modules.json"

def _load_cell_attr_flag_map():
    """从 config/attr_flag_map.json 加载 cell_attr_flag_map，解析 inherit_from 继承关系。"""
    cfg_path = _config_dir / "runtime" / "attr_flag_map.json"
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
    cfg_path = _config_dir / "runtime" / "attr_flag_map.json"
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


async def _load_xml_content_from_request(request, form, xml_content=None):
    """从请求加载 XML 内容：form 文件 > xml_content 表单字段 > JSON body（含 dzhpool filename 回退）。

    合并 dzh_import / dzh_import_and_save 共享的内容加载骨架（含多编码解码循环，
    仅在此处出现一次）。返回 (content_bytes, filename, error_msg)；error_msg 非空
    表示应直接返回给客户端的错误。
    """
    content = None
    filename = "upload.xml"
    uploaded_file = form.get("file")
    if uploaded_file and hasattr(uploaded_file, "filename") and uploaded_file.filename:
        content = await uploaded_file.read()
        if len(content) > MAX_UPLOAD_SIZE:
            return None, filename, f"文件大小超过限制 ({MAX_UPLOAD_SIZE // 1024 // 1024}MB)"
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
                        xml_path = safe_path_join(os.path.join(os.path.dirname(__file__), 'dzhpool'), fn)
                    except ValueError as e:
                        return None, filename, str(e)
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
    return content, filename, None


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
        form = await request.form()
        content, filename, err = await _load_xml_content_from_request(request, form, xml_content)
        if err:
            return {"success": False, "error": err}
        if content is None:
            return {"success": False, "error": "请上传文件或提供 xml_content 或 filename"}

        try:
            # fmt=None 时由 _call_converter 自动探测 dzh/tdx 格式
            parsed = _call_converter(content, fmt=None, direction="import", name=filename)
        except Exception as e:
            return {"success": False, "error": f"XML解析失败: {e}"}

        _store.pool = parsed
        # Task 16: 事件驱动并行通道——发布 PoolLoaded 事件触发 Execution 模块编译 + Database 持久化
        try:
            _bus: "EventBus" = request.app.state.bus
            _bus.publish(PoolLoaded(pool_config=parsed, source_format="dzh"))
        except Exception as _bus_ex:
            logger.warning("dzh_import 发布 PoolLoaded 事件失败: %s", _bus_ex)
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
            from converters import DZHPoolExecutor
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
        form = await request.form()
        content, filename, err = await _load_xml_content_from_request(request, form, xml_content)
        if err:
            return {"success": False, "error": err}
        if content is None:
            return {"success": False, "error": "请上传文件或提供 xml_content 或 filename"}

        try:
            # fmt=None 时由 _call_converter 自动探测 dzh/tdx 格式
            parsed = _call_converter(content, fmt=None, direction="import", name=filename)
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
            from converters import DZHPoolExecutor
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
            xml_bytes = _call_converter(None, "dzh", "export", config=config)
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
                from converters import build_attrtext_from_selections
                params["raw_attrtext"] = build_attrtext_from_selections(updates["selections"])
                params["attrtext"] = params["raw_attrtext"]

            # 处理 reload_mode 更新
            if "reload_mode" in updates:
                from converters import encode_reload_mode
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
                from converters import parse_attrtext_selections
                params["selections"] = parse_attrtext_selections(params.get("raw_attrtext", ""))
            if "reload_mode" not in params:
                from converters import decode_reload_mode
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
            if not isinstance(edge, dict):
                continue
            src = edge.get("source", {})
            tgt = edge.get("target", {})
            src_id = src.get("node_id", "") if isinstance(src, dict) else str(src)
            tgt_id = tgt.get("node_id", "") if isinstance(tgt, dict) else str(tgt)
            if src_id == cell_id or tgt_id == cell_id:
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
            if not isinstance(edge, dict):
                continue
            src = edge.get("source", {})
            tgt = edge.get("target", {})
            if isinstance(src, str):
                src = {"node_id": src}
            if isinstance(tgt, str):
                tgt = {"node_id": tgt}
            flow_data = {
                "id": edge.get("id"),
                "source": src,
                "target": tgt,
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

        src_id = body.get("source", {}).get("node_id") if isinstance(body.get("source"), dict) else body.get("source")
        tgt_id = body.get("target", {}).get("node_id") if isinstance(body.get("target"), dict) else body.get("target")
        params = body.get("params", {})

        if not src_id or not tgt_id:
            return {"success": False, "error": "缺少 source 或 target 参数"}

        pool = _store.pool
        node_ids = {n["id"] for n in pool.get("nodes", []) if isinstance(n, dict)}
        if src_id not in node_ids:
            return {"success": False, "error": f"源节点不存在: {src_id}"}
        if tgt_id not in node_ids:
            return {"success": False, "error": f"目标节点不存在: {tgt_id}"}

        for edge in pool.get("edges", []):
            if not isinstance(edge, dict):
                continue
            existing_src = edge.get("source", {}).get("node_id", "") if isinstance(edge.get("source"), dict) else edge.get("source", "")
            existing_tgt = edge.get("target", {}).get("node_id", "") if isinstance(edge.get("target"), dict) else edge.get("target", "")
            if existing_src == src_id and existing_tgt == tgt_id:
                return {"success": False, "error": f"Flow 已存在: {src_id} -> {tgt_id}"}

        new_edge = {
            "id": f"e_{uuid.uuid4().hex[:8]}",
            "source": {"node_id": src_id},
            "target": {"node_id": tgt_id},
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

        if "source" in body:
            target["source"] = body["source"] if isinstance(body["source"], dict) else {"node_id": body["source"]}
        if "target" in body:
            target["target"] = body["target"] if isinstance(body["target"], dict) else {"node_id": body["target"]}

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
                config = _call_converter(content, "dzh", "import", name=filename)
            except Exception as e:
                return {"success": False, "error": f"首次解析失败: {e}", "diffs": [], "stats": {}}

        original_json = json.dumps(config, ensure_ascii=False, sort_keys=True, default=str)

        try:
            xml_bytes = _call_converter(None, "dzh", "export", config=config)
        except Exception as e:
            return {
                "success": False,
                "error": f"导出步骤失败: {e}",
                "diffs": [{"stage": "export", "error": str(e)}],
                "stats": {"node_count": len(config.get("nodes", [])), "edge_count": len(config.get("edges", []))},
            }

        try:
            re_parsed = _call_converter(xml_bytes, "dzh", "import", name="roundtrip_reparse.xml")
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
            # SubTask 2.4: 注入 storage (IStorageQuery) 以启用 kline_cache 持久化
            re = KLineReplayEngine(engine, storage=getattr(request.app.state, 'storage', None))
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
            meta_config = _call_converter(content, "dzh", "import", name=file.filename)
        except Exception as e:
            return {"code": 1, "msg": f"转换失败: {e}", "data": None}
        return {"code": 0, "msg": "ok", "data": meta_config}

    @router.post("/export-file")
    async def dzh_export_file(file: UploadFile = File(...)):
        content = await file.read()
        if len(content) > MAX_UPLOAD_SIZE:
            return {"code": 1, "msg": f"文件大小超过限制 ({MAX_UPLOAD_SIZE // 1024 // 1024}MB)", "data": None}
        try:
            meta_config = _call_converter(content, "dzh", "import", name=file.filename)
        except Exception as e:
            return {"code": 1, "msg": f"导入失败: {e}", "data": None}
        try:
            xml_bytes = _call_converter(None, "dzh", "export", config=meta_config)
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
            xml_bytes = _call_converter(None, "dzh", "export", config=body)
            return Response(
                content=xml_bytes,
                media_type="application/xml",
                headers={"Content-Disposition": "attachment; filename=pool.xml"}
            )
        except Exception as e:
            return {"code": 1, "msg": f"导出失败: {e}", "data": None}

    @router.get("/test-import")
    async def dzh_test_import(filename: str = "超赢1号.xml"):
        base = str(_this_dir)
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
            meta_config = _call_converter(content, "dzh", "import", name=filename)
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
        from converters import DZHPoolExecutor
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
        from converters import DZHPoolExecutor
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
        dispatch_path = _Path(__file__).resolve().parent / 'config' / 'architecture' / 'dispatch.json'
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
                tdxpool_dir = os.path.join(os.path.dirname(__file__), 'tdxpool')
                xml_path = os.path.join(tdxpool_dir, f"{pool_id}.xml")
                if os.path.isfile(xml_path):
                    from converters import tdx_to_internal
                    tdx_pool = _call_converter(xml_path, "tdx", "import")
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
            # SubTask 2.4: 注入 storage (IStorageQuery) 以启用 kline_cache 持久化
            re = KLineReplayEngine(engine, storage=getattr(request.app.state, 'storage', None))
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
                                from converters import tdx_to_internal
                                tdx_pool = _call_converter(xml_path, "tdx", "import")
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
                tdxpool_dir = _os.path.join(_os.path.dirname(__file__), 'tdxpool')
                try:
                    xml_path = safe_path_join(tdxpool_dir, filename)
                except ValueError as e:
                    return {"code": -1, "error": str(e), "data": None}
                if _os.path.isfile(xml_path):
                    from converters import tdx_to_internal
                    tdx_pool = _call_converter(xml_path, "tdx", "import")
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
                # SubTask 2.4: 注入 storage (IStorageQuery) 以启用 kline_cache 持久化
                re = KLineReplayEngine(engine, storage=getattr(request.app.state, 'storage', None))
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
        modules_path = _config_dir / "architecture" / "modules.json"
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

        dzhpool_dir = os.path.join(os.path.dirname(__file__), 'dzhpool')

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

        # fmt=None 时由 _call_converter 自动探测 dzh/tdx（parse_dzh_xml 内部处理 GBK 解码）
        raw = open(xml_path, 'rb').read()
        try:
            parsed = _call_converter(raw, fmt=None, direction="import", name=os.path.basename(xml_path))
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
# 注：原 json_api.py 通过 `from converters import ...`
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

        # Task 16: 事件驱动并行通道——发布 PoolLoaded 事件触发 Execution 模块编译
        try:
            _bus: "EventBus" = request.app.state.bus
            _bus.publish(PoolLoaded(pool_config=pool_config, source_format="json"))
        except Exception as _bus_ex:
            logger.warning("json_import 发布 PoolLoaded 事件失败: %s", _bus_ex)

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



# ══════════════════════════════════════════════════════════════════════
#  向后兼容别名（来自原 api/__init__.py）
# ══════════════════════════════════════════════════════════════════════
# 旧 import 路径 `from api import config_api_router / config_api_init / set_table_engine`
# 继续可用，分别等同于 router / init / set_engine。
config_api_router = router
# config_ws_router 已在 Part 1 中直接定义为模块级变量，可直接 import
config_api_init = init
set_table_engine = set_engine
