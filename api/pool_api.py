"""
池配置管理 API（合并自 config_api / table_api / meta_api）
========================================================
提供配置表读写、表驱动架构、备选池管理、数据源切换等端点。

导出对象：
  - router              : config_api 的 /api/config 路由
  - config_api_init     : config_api 的 init() 初始化函数
  - table_router        : table_api 的 /api/v1/table 路由
  - table_config_router : table_api 的 /api/config 路由
  - set_table_engine    : table_api 的 set_engine() 注入函数
  - create_meta_router  : meta_api 的工厂函数（生成 /api/meta 路由）
"""

# ══════════════════════════════════════════════════════════════════════
#  通用导入
# ══════════════════════════════════════════════════════════════════════
import json
import os
import re
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
#  Part 1: 来自 config_api.py — 配置管理 API
# ══════════════════════════════════════════════════════════════════════

router = APIRouter(prefix="/api/config", tags=["config"])

# 合法表名模式：字母/下划线开头，仅含字母、数字、下划线（防止路径遍历）
TABLE_NAME_PATTERN = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')

# 引擎实例（由app.py注入）
_config_store = None
_hot_reload_manager = None
_schema_validator = None


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
    if not _config_store:
        raise HTTPException(500, "引擎未初始化")
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
    if not _config_store:
        raise HTTPException(500, "引擎未初始化")
    return _config_store.get_categories()


@router.get("/status")
def config_status():
    """返回配置中心状态：已加载表、分类一致性、schema 覆盖率与锁状态。"""
    if not _config_store:
        raise HTTPException(500, "引擎未初始化")
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
    if not _config_store:
        raise HTTPException(500, "引擎未初始化")
    data = _config_store.get(table_name)
    if data is None:
        raise HTTPException(404, f"配置表 {table_name} 不存在")
    return data


class TableUpdateRequest(BaseModel):
    content: Dict[str, Any]


@router.put("/tables/{table_name}")
def update_table(table_name: str, req: TableUpdateRequest):
    """更新指定配置表（写入文件 + 触发热加载）"""
    if not _config_store:
        raise HTTPException(500, "引擎未初始化")

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

        return {"ok": True, "table": table_name, "changed": changed}
    except Exception as e:
        raise HTTPException(500, f"更新配置表失败: {e}")


# ─── 校验 ────────────────────────────────────────────────────

@router.post("/validate")
def validate_all():
    """校验所有配置表"""
    if not _config_store:
        raise HTTPException(500, "引擎未初始化")
    from ..native.validators import ConfigIntegrityValidator
    validator = ConfigIntegrityValidator(str(_config_store._config_dir))
    return validator.validate_all()


@router.post("/validate/{table_name}")
def validate_table(table_name: str):
    """校验指定配置表"""
    if not _config_store:
        raise HTTPException(500, "引擎未初始化")
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
    if not _config_store:
        raise HTTPException(500, "引擎未初始化")

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
    if not _config_store:
        raise HTTPException(500, "引擎未初始化")

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
    if not _config_store:
        raise HTTPException(500, "引擎未初始化")

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
    if not _config_store:
        raise HTTPException(500, "引擎未初始化")
    if _config_store.is_table_locked(table_name):
        return {"locked": True, "message": "Already locked",
                "lock_info": _config_store._locks.get(table_name)}
    lock_info = _config_store.lock_table(table_name, reason)
    return {"locked": True, "lock_info": lock_info}


@router.delete("/lock/{table_name}")
def unlock_table(table_name: str):
    """解锁一张表。"""
    if not _config_store:
        raise HTTPException(500, "引擎未初始化")
    was_locked = _config_store.unlock_table(table_name)
    return {"locked": False, "was_locked": was_locked}


# ─── 热加载控制 ──────────────────────────────────────────────

@router.post("/reload")
def trigger_reload():
    """手动触发热加载"""
    if not _config_store:
        raise HTTPException(500, "引擎未初始化")

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
    if not _config_store or not _config_store._storage:
        return {"history": [], "total": 0}
    try:
        history = _config_store._storage.get_config_versions(table_name, limit=limit)
        return {"history": history, "total": len(history)}
    except Exception as e:
        return {"history": [], "error": str(e)}


@router.post("/rollback/{version_id}")
def rollback_config(version_id: str):
    """回滚配置到指定版本"""
    if not _config_store:
        raise HTTPException(500, "引擎未初始化")
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
    if not _config_store:
        raise HTTPException(500, "引擎未初始化")

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
    if not _config_store:
        raise HTTPException(500, "引擎未初始化")
    mappings = _config_store.get("data_mappings", {}).get("mappings", [])
    return {"mappings": mappings, "total": len(mappings)}


@router.get("/data-mappings/{mapping_id}")
def get_data_mapping(mapping_id: str):
    """获取指定数据映射"""
    if not _config_store:
        raise HTTPException(500, "引擎未初始化")
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
    if not _config_store:
        raise HTTPException(500, "引擎未初始化")

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
    if not _config_store:
        raise HTTPException(500, "引擎未初始化")
    render_configs = _config_store.get("cell_type_registry", {}).get("render_config", {})
    config = render_configs.get(type_id)
    if not config:
        raise HTTPException(404, f"类型 {type_id} 的渲染配置不存在")
    return config


# ─── WebSocket 配置变更推送 ──────────────────────────────────

@router.websocket("/ws")
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

def set_engine(engine, config_dir=None):
    global _engine, _panel_generator, _data_binder, _ownership_manager, _table_config_store, _rule_engine
    _engine = engine
    if config_dir:
        from ..core.table_engine import ConfigStore, PanelGenerator, DataBinder, PropertyOwnershipManager, RuleEngine
        _table_config_store = ConfigStore(config_dir)
        _table_config_store.load_all()
        _panel_generator = PanelGenerator(_table_config_store)
        _data_binder = DataBinder()
        _ownership_manager = PropertyOwnershipManager(_table_config_store)
        # 创建规则引擎并注册 handler
        _rule_engine = RuleEngine(_table_config_store)
        try:
            from ..native.builtins import _HANDLERS
            for name, fn in _HANDLERS.items():
                _rule_engine.register_handler(name, fn)
        except Exception:
            pass
        # 将表驱动组件桥接到 MetaEngine
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
        # 同步 MetaEngine 桥接
        if _engine and hasattr(_engine, 'check_hot_reload'):
            _engine.check_hot_reload()
    return {"changed": changed, "count": len(changed)}


@table_router.get("/validate")
def validate_configs():
    """校验所有配置表"""
    if not _table_config_store:
        raise HTTPException(500, "引擎未初始化")
    from ..native.validators import ConfigIntegrityValidator
    validator = ConfigIntegrityValidator(_table_config_store._config_dir)
    errors = validator.validate_all()
    return {"valid": len(errors) == 0, "errors": errors}


@table_router.get("/validate/integrity")
def validate_integrity():
    """运行完整的配置完整性校验（覆盖所有节点类型及属性）"""
    if not _engine:
        raise HTTPException(500, "引擎未初始化")
    from ..native.validators import ConfigIntegrityValidator
    import os
    config_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")
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
    config_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")
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
        from ..native.builtins import _HANDLERS
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
        from ..native.builtins import _HANDLERS
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
        data = request.app.state.engine.get_modules()
        return {"code": 0, "msg": "ok", "data": data}

    @router.get("/conditions")
    async def get_conditions(request: Request):
        data = request.app.state.engine.get_conditions()
        return {"code": 0, "msg": "ok", "data": data}

    @router.get("/engines")
    async def get_engines(request: Request):
        data = request.app.state.engine.get_engines()
        return {"code": 0, "msg": "ok", "data": data}

    # ─── 配置表查询端点 ──────────────────────────────────────

    @router.get("/config/{table_name}")
    async def get_config_table(request: Request, table_name: str):
        """通过 MetaEngine 桥接查询配置表"""
        engine = request.app.state.engine
        if hasattr(engine, '_config_store') and engine._config_store:
            data = engine._config_store.get(table_name)
            if data is not None:
                return {"code": 0, "msg": "ok", "data": data}
        return {"code": 1, "msg": f"配置表 {table_name} 不存在"}

    @router.get("/table-names")
    async def get_table_names(request: Request):
        """列出所有配置表名称"""
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
    from ..services.candidate_pool import CandidatePoolResolver, CandidatePoolRefreshManager

    app_state = request.app.state

    # 延迟初始化：仅在首次调用时创建 resolver 和 refresh_manager
    if not hasattr(app_state, '_candidate_pool_resolver'):
        storage = app_state.storage
        tq = app_state.tq

        # 构建 providers 字典
        providers = {}

        # 优先注入 LocalFileProvider（本地文件数据源）
        try:
            from ..services.providers.local_file_provider import LocalFileProvider
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
    from ..services.db_sync_service import DatabaseSyncService

    app_state = request.app.state
    if not hasattr(app_state, '_db_sync_service'):
        storage = app_state.storage
        # 复用 resolver 已初始化的 providers 字典
        resolver = _get_resolver(request)
        providers = getattr(resolver, '_providers', {}) or {}
        app_state._db_sync_service = DatabaseSyncService(storage, providers)
        logger.info("已初始化 DatabaseSyncService，providers=%s", list(providers.keys()))

    return app_state._db_sync_service
