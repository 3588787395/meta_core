"""正合测试：ImportExportModule 导入导出模块。

覆盖场景：
1. import_pool(path, format) with json format
2. export_pool(config, path, format) with json format
3. _IMPORT_RULES 表有 3 个条目（dzh / tdx / json）
4. _EXPORT_RULES 表有 3 个条目
5. JSON 往返：export → import → config 关键字段匹配
6. ImportExportModule 发布 ImportStarted 与 PoolLoaded 事件
7. export_pool 发布 ExportCompleted 事件
8. 表驱动分派（无 if/elif 选择格式）
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

# 覆盖 converters 模块（DZH/TDX 格式转换器）
try:
    import converters  # noqa: F401
except ImportError:
    pass

# 可选依赖：converters 与 event_bus 由 core.import_export_module 间接引入
pytest.importorskip("fastapi")  # 项目整体依赖 fastapi 环境

from core.import_export_module import (
    ImportExportModule,
    _EXPORT_RULES,
    _IMPORT_RULES,
)
from core.event_bus import (
    EventBus,
    ExportCompleted,
    ImportStarted,
    PoolLoaded,
)


# ------------------------------------------------------------------
# 辅助：构造一个最小的合法 pool_config dict（pool_config 格式1）
# ------------------------------------------------------------------
def _make_pool_config() -> dict:
    return {
        "name": "test_roundtrip_pool",
        "pool_type": "dzh",
        "nodes": [
            {
                "id": "1",
                "type": "200",
                "label": "备选池",
                "params": {"dzh_cell_type": 200},
                "position": {"x": 10, "y": 20, "width": 100, "height": 100},
            },
            {
                "id": "2",
                "type": "202",
                "label": "状态池",
                "params": {"dzh_cell_type": 202},
                "position": {"x": 200, "y": 20, "width": 100, "height": 100},
            },
        ],
        "edges": [
            {"id": "e1", "from": "1", "to": "2", "params": {}},
        ],
    }


# ------------------------------------------------------------------
# 测试 3 & 4：_IMPORT_RULES / _EXPORT_RULES 表条目数
# ------------------------------------------------------------------
def test_import_rules_has_three_entries():
    """_IMPORT_RULES 表驱动规则含 dzh / tdx / json 三个条目。"""
    assert isinstance(_IMPORT_RULES, dict)
    assert len(_IMPORT_RULES) == 3
    assert set(_IMPORT_RULES.keys()) == {"dzh", "tdx", "json"}
    # 每个值是 (callable, str) 元组
    for fmt, rule in _IMPORT_RULES.items():
        assert isinstance(rule, tuple) and len(rule) == 2
        parser_fn, format_name = rule
        assert callable(parser_fn)
        assert format_name == fmt


def test_export_rules_has_three_entries():
    """_EXPORT_RULES 表驱动规则含 dzh / tdx / json 三个条目。"""
    assert isinstance(_EXPORT_RULES, dict)
    assert len(_EXPORT_RULES) == 3
    assert set(_EXPORT_RULES.keys()) == {"dzh", "tdx", "json"}
    for fmt, rule in _EXPORT_RULES.items():
        assert isinstance(rule, tuple) and len(rule) == 2
        serializer_fn, format_name = rule
        assert callable(serializer_fn)
        assert format_name == fmt


# ------------------------------------------------------------------
# 测试 8：表驱动分派（无 if/elif）
# ------------------------------------------------------------------
def test_table_driven_dispatch_no_if_elif():
    """import_pool / export_pool 通过查表分派，不依赖 if/elif 链。

    验证：传入未知格式时返回 False / 空串（表未命中），而非抛出 ValueError。
    同时验证 _IMPORT_RULES 是 dict（表驱动标志），而非函数链。
    """
    assert isinstance(_IMPORT_RULES, dict)
    assert isinstance(_EXPORT_RULES, dict)
    bus = EventBus()
    mod = ImportExportModule(bus=bus)
    # 未知格式：表未命中，返回 False（import）/ 空串（export），不抛异常
    assert mod.import_pool(path="nonexistent.json", format="unknown") is False
    assert mod.export_pool(config={}, path="out.unknown", format="unknown") == ""


# ------------------------------------------------------------------
# 测试 1 & 6：import_pool(json) 发布 ImportStarted + PoolLoaded
# ------------------------------------------------------------------
def test_import_pool_json_publishes_events(event_collector):
    """import_pool 以 json 格式导入，返回 True 并发布 ImportStarted + PoolLoaded。"""
    cfg = _make_pool_config()
    # 写入临时 json 文件（export_pool_to_json 产出的标准格式）
    export_doc = {
        "version": 1,
        "pool_meta": {
            "name": cfg["name"],
            "pool_type": cfg["pool_type"],
            "ver": "1.0",
            "mode": "1",
            "backcolor": 16777216,
        },
        "nodes": cfg["nodes"],
        "edges": cfg["edges"],
    }
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(export_doc, f, ensure_ascii=False)
        tmp_path = f.name

    try:
        from metatest.conftest import EventCollector
        bus = EventBus()
        collector = EventCollector(bus)
        mod = ImportExportModule(bus=bus)
        result = mod.import_pool(path=tmp_path, format="json")
        assert result is True

        types = collector.count_by_type()
        assert types.get("ImportStarted", 0) == 1
        assert types.get("PoolLoaded", 0) == 1

        # ImportStarted 携带 format / path
        imp_events = collector.filter(type="ImportStarted")
        assert len(imp_events) == 1
        assert imp_events[0].format == "json"
        assert imp_events[0].path == tmp_path

        # PoolLoaded 携带 pool_config / source_format
        loaded_events = collector.filter(type="PoolLoaded")
        assert len(loaded_events) == 1
        assert loaded_events[0].source_format == "json"
        assert isinstance(loaded_events[0].pool_config, dict)
        assert loaded_events[0].pool_config.get("name") == cfg["name"]
    finally:
        os.unlink(tmp_path)
        collector.disconnect()


# ------------------------------------------------------------------
# 测试 2 & 7：export_pool(json) 发布 ExportCompleted
# ------------------------------------------------------------------
def test_export_pool_json_publishes_completed(event_collector):
    """export_pool 以 json 格式导出，返回路径并发布 ExportCompleted。"""
    cfg = _make_pool_config()
    tmp_dir = tempfile.mkdtemp()
    out_path = os.path.join(tmp_dir, "exported.json")

    try:
        from metatest.conftest import EventCollector
        bus = EventBus()
        collector = EventCollector(bus)
        mod = ImportExportModule(bus=bus)
        result_path = mod.export_pool(config=cfg, path=out_path, format="json")

        assert result_path == out_path
        assert os.path.isfile(out_path)

        types = collector.count_by_type()
        assert types.get("ExportCompleted", 0) == 1

        exp_events = collector.filter(type="ExportCompleted")
        assert len(exp_events) == 1
        assert exp_events[0].format == "json"
        assert exp_events[0].path == out_path
        assert exp_events[0].count == len(cfg["nodes"])
    finally:
        if os.path.exists(out_path):
            os.unlink(out_path)
        os.rmdir(tmp_dir)
        collector.disconnect()


# ------------------------------------------------------------------
# 测试 5：JSON 往返 export → import → config 匹配
# ------------------------------------------------------------------
def test_json_roundtrip_export_then_import(event_collector):
    """JSON 往返：export_pool 写出 → import_pool 读回，关键字段匹配。"""
    cfg = _make_pool_config()
    tmp_dir = tempfile.mkdtemp()
    out_path = os.path.join(tmp_dir, "roundtrip.json")

    try:
        from metatest.conftest import EventCollector
        bus = EventBus()
        collector = EventCollector(bus)
        mod = ImportExportModule(bus=bus)

        # 导出
        mod.export_pool(config=cfg, path=out_path, format="json")
        assert os.path.isfile(out_path)

        # 导入
        imported = mod.import_pool(path=out_path, format="json")
        assert imported is True

        # 取 PoolLoaded 事件中的 pool_config
        loaded_events = collector.filter(type="PoolLoaded")
        assert len(loaded_events) == 1
        rt = loaded_events[0].pool_config

        # 关键字段往返保持
        assert rt.get("name") == cfg["name"]
        assert rt.get("pool_type") == cfg["pool_type"]
        assert len(rt.get("nodes", [])) == len(cfg["nodes"])
        assert len(rt.get("edges", [])) == len(cfg["edges"])
        # 节点 id 保持
        rt_node_ids = sorted(n.get("id") for n in rt.get("nodes", []))
        assert rt_node_ids == ["1", "2"]
    finally:
        if os.path.exists(out_path):
            os.unlink(out_path)
        os.rmdir(tmp_dir)
        collector.disconnect()


# ------------------------------------------------------------------
# 测试：import_pool 不支持的格式返回 False，不发布 PoolLoaded
# ------------------------------------------------------------------
def test_import_pool_unsupported_format_no_events(event_collector):
    """import_pool 未知格式返回 False 且不发布 PoolLoaded。"""
    from metatest.conftest import EventCollector
    bus = EventBus()
    collector = EventCollector(bus)
    mod = ImportExportModule(bus=bus)
    result = mod.import_pool(path="whatever", format="csv")
    assert result is False
    types = collector.count_by_type()
    # ImportStarted 也不会发布（表未命中直接 return）
    assert "ImportStarted" not in types
    assert "PoolLoaded" not in types
    collector.disconnect()


# ------------------------------------------------------------------
# 测试：ImportExportModule 构造函数订阅 ExportCompleted 计数
# ------------------------------------------------------------------
def test_module_counts_exports(event_collector):
    """ImportExportModule 内部订阅 ExportCompleted 用于计数。"""
    cfg = _make_pool_config()
    tmp_dir = tempfile.mkdtemp()
    out_path = os.path.join(tmp_dir, "count.json")
    try:
        from metatest.conftest import EventCollector
        bus = EventBus()
        collector = EventCollector(bus)
        mod = ImportExportModule(bus=bus)
        assert mod._export_count == 0
        mod.export_pool(config=cfg, path=out_path, format="json")
        assert mod._export_count == 1
    finally:
        if os.path.exists(out_path):
            os.unlink(out_path)
        os.rmdir(tmp_dir)
        collector.disconnect()
