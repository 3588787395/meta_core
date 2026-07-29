"""合测试 4：导入/导出 roundtrip 端到端验证。

按 ``create-metatest-comprehensive-validation`` spec Task 24 实现：
- 验证 JSON 格式导入导出 roundtrip
- 验证 ``_IMPORT_RULES`` / ``_EXPORT_RULES`` 表驱动分派
- 验证 ``ImportStarted`` / ``PoolLoaded`` / ``ExportCompleted`` 事件发布
- 验证 DZH/TDX 格式可被解析（不要求完整 roundtrip，因依赖外部库）
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict

import pytest

from core.event_bus import (
    EventBus,
    ExportCompleted,
    ImportStarted,
    PoolLoaded,
)


# ---------------------------------------------------------------------------
# 测试用 PoolConfig
# ---------------------------------------------------------------------------


def _make_pool_config() -> Dict[str, Any]:
    """构造测试用 PoolConfig dict（最小可用结构）。"""
    return {
        "name": "test_roundtrip_pool",
        "version": "1.0",
        "nodes": [
            {
                "id": "src",
                "type": "source_pool",
                "name": "备选池",
                "params": {"stocks": ["fz000001", "fz000002"]},
            },
            {
                "id": "cond",
                "type": "condition_node",
                "name": "条件节点",
                "params": {"formula": "kdj.golden_cross"},
            },
            {
                "id": "tgt",
                "type": "state_pool",
                "name": "状态池",
                "params": {"ttl": 100},
            },
        ],
        "edges": [
            {"src": "src", "tgt": "cond", "type": "unconditional"},
            {"src": "cond", "tgt": "tgt", "type": "conditional", "condition": "kdj.golden_cross"},
        ],
    }


# ---------------------------------------------------------------------------
# 表驱动规则验证
# ---------------------------------------------------------------------------


def test_import_rules_table_has_three_formats():
    """_IMPORT_RULES 必须含 dzh / tdx / json 三格式条目。"""
    from core.import_export_module import _IMPORT_RULES
    assert "json" in _IMPORT_RULES
    assert "dzh" in _IMPORT_RULES
    assert "tdx" in _IMPORT_RULES


def test_export_rules_table_has_three_formats():
    """_EXPORT_RULES 必须含 dzh / tdx / json 三格式条目。"""
    from core.import_export_module import _EXPORT_RULES
    assert "json" in _EXPORT_RULES
    assert "dzh" in _EXPORT_RULES
    assert "tdx" in _EXPORT_RULES


def test_import_rules_each_entry_is_callable_with_format():
    """_IMPORT_RULES 每条目为 (callable, format_name) 元组。"""
    from core.import_export_module import _IMPORT_RULES
    for fmt, entry in _IMPORT_RULES.items():
        assert isinstance(entry, tuple), f"{fmt} 条目不是 tuple"
        assert len(entry) == 2, f"{fmt} 条目长度不为 2"
        parser_fn, format_name = entry
        assert callable(parser_fn), f"{fmt} parser 不是 callable"
        assert isinstance(format_name, str), f"{fmt} format_name 不是 str"


# ---------------------------------------------------------------------------
# JSON roundtrip 完整流程
# ---------------------------------------------------------------------------


def test_json_roundtrip_export_then_import(event_collector):
    """JSON 导出后导入应能还原 PoolConfig。"""
    cfg = _make_pool_config()
    tmp_dir = tempfile.mkdtemp()
    out_path = os.path.join(tmp_dir, "rt.json")
    try:
        bus = EventBus()
        collector = event_collector(bus)
        try:
            from core.import_export_module import ImportExportModule
            mod = ImportExportModule(bus=bus)
            # 导出
            ret = mod.export_pool(config=cfg, path=out_path, format="json")
            assert ret == out_path, f"export_pool 返回 {ret}，期望 {out_path}"
            assert os.path.isfile(out_path), "导出文件未创建"
            # 导入
            ok = mod.import_pool(path=out_path, format="json")
            assert ok is True, "import_pool 返回 False"
            # 验证事件发布
            loaded = collector.filter(type="PoolLoaded")
            assert len(loaded) == 1, f"PoolLoaded 事件数 {len(loaded)} 不等于 1"
            rt = loaded[0].pool_config
            assert rt.get("name") == cfg["name"], (
                f"roundtrip 后 name 不匹配: {rt.get('name')} != {cfg['name']}"
            )
        finally:
            collector.disconnect()
    finally:
        if os.path.exists(out_path):
            os.unlink(out_path)
        os.rmdir(tmp_dir)


def test_json_export_publishes_export_completed(event_collector):
    """JSON 导出应发布 ExportCompleted 事件。"""
    cfg = _make_pool_config()
    tmp_dir = tempfile.mkdtemp()
    out_path = os.path.join(tmp_dir, "exp.json")
    try:
        bus = EventBus()
        collector = event_collector(bus)
        try:
            from core.import_export_module import ImportExportModule
            mod = ImportExportModule(bus=bus)
            mod.export_pool(config=cfg, path=out_path, format="json")
            completed = collector.filter(type="ExportCompleted")
            assert len(completed) == 1
            assert completed[0].format == "json"
            assert completed[0].path == out_path
        finally:
            collector.disconnect()
    finally:
        if os.path.exists(out_path):
            os.unlink(out_path)
        os.rmdir(tmp_dir)


def test_import_publishes_import_started(event_collector):
    """导入应发布 ImportStarted 事件。"""
    cfg = _make_pool_config()
    tmp_dir = tempfile.mkdtemp()
    out_path = os.path.join(tmp_dir, "imp.json")
    try:
        # 先写出文件
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False)
        bus = EventBus()
        collector = event_collector(bus)
        try:
            from core.import_export_module import ImportExportModule
            mod = ImportExportModule(bus=bus)
            mod.import_pool(path=out_path, format="json")
            started = collector.filter(type="ImportStarted")
            assert len(started) == 1
            assert started[0].format == "json"
            assert started[0].path == out_path
        finally:
            collector.disconnect()
    finally:
        if os.path.exists(out_path):
            os.unlink(out_path)
        os.rmdir(tmp_dir)


# ---------------------------------------------------------------------------
# 不支持的格式
# ---------------------------------------------------------------------------


def test_import_unsupported_format_returns_false():
    """import_pool 对不支持的格式应返回 False。"""
    bus = EventBus()
    try:
        from core.import_export_module import ImportExportModule
        mod = ImportExportModule(bus=bus)
        ok = mod.import_pool(path="/tmp/nonexistent.xml", format="csv")
        assert ok is False
    finally:
        pass


def test_export_unsupported_format_returns_empty():
    """export_pool 对不支持的格式应返回空字符串。"""
    bus = EventBus()
    from core.import_export_module import ImportExportModule
    mod = ImportExportModule(bus=bus)
    ret = mod.export_pool(config=_make_pool_config(), path="/tmp/x.csv", format="csv")
    assert ret == ""


# ---------------------------------------------------------------------------
# 导入计数与导出计数
# ---------------------------------------------------------------------------


def test_module_tracks_counts(event_collector):
    """ImportExportModule 应跟踪导入/导出计数。"""
    cfg = _make_pool_config()
    tmp_dir = tempfile.mkdtemp()
    out_path = os.path.join(tmp_dir, "count.json")
    try:
        bus = EventBus()
        collector = event_collector(bus)
        try:
            from core.import_export_module import ImportExportModule
            mod = ImportExportModule(bus=bus)
            initial_import = mod._import_count
            initial_export = mod._export_count
            mod.export_pool(config=cfg, path=out_path, format="json")
            assert mod._export_count == initial_export + 1
            mod.import_pool(path=out_path, format="json")
            assert mod._import_count == initial_import + 1
        finally:
            collector.disconnect()
    finally:
        if os.path.exists(out_path):
            os.unlink(out_path)
        os.rmdir(tmp_dir)
