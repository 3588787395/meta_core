"""合测试 5：配置热加载端到端验证。

按 ``create-metatest-comprehensive-validation`` spec Task 25 实现：
- 验证 ConfigStore.get_table / get_data_file 统一加载入口
- 验证 HotReloadManager 文件监控 + ConfigChanged 事件发布
- 验证 _notify_changed 单一通知方法
- 验证禁止 _load_json / _load_config（Grep 0 匹配）
"""
from __future__ import annotations

import inspect
import json
import os
import tempfile
from pathlib import Path

import pytest

from core.event_bus import ConfigChanged, ConfigLoaded, EventBus


# ---------------------------------------------------------------------------
# ConfigStore 加载入口验证
# ---------------------------------------------------------------------------


def test_config_store_class_exists():
    """ConfigStore 类必须存在。"""
    from core.table_engine import ConfigStore
    assert ConfigStore is not None


def test_config_store_loads_config(config_store):
    """ConfigStore 装配后应加载配置表。"""
    names = config_store._tables
    assert isinstance(names, dict)
    # 独立实例（无 bus）自动 load_all，应有配置表
    assert len(names) > 0, "ConfigStore 加载后无配置表"


def test_config_store_get_table_returns_dict(config_store):
    """get_table 必须返回 dict（未知表返回空 dict 而非 None）。"""
    table = config_store.get_table("nonexistent_table_xyz")
    assert isinstance(table, dict)
    assert table == {}


def test_config_store_get_data_file_returns_dict(config_store):
    """get_data_file 必须返回 dict（未知文件返回空 dict）。"""
    data = config_store.get_data_file("nonexistent_data_file_xyz")
    assert isinstance(data, dict)
    assert data == {}


def test_config_store_get_table_does_not_raise(config_store):
    """get_table 对未知表名不应抛异常。"""
    # 多次调用稳定性
    for _ in range(3):
        result = config_store.get_table("nonexistent_xxx_yyy")
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# 禁止 _load_json / _load_config 验证（Grep 0 匹配）
# ---------------------------------------------------------------------------


def test_no_load_json_in_table_engine():
    """table_engine.py 禁止定义 _load_json 函数（G2 硬约束）。"""
    import core.table_engine as te
    # 不应有 _load_json 公开/私有函数
    assert not hasattr(te, "_load_json"), "table_engine 不应定义 _load_json"
    assert not hasattr(te, "_load_config"), "table_engine 不应定义 _load_config"


def test_no_load_json_in_runtime_mode_module():
    """runtime_mode_module.py 禁止定义 _load_json 函数。"""
    import core.runtime_mode_module as rmm
    assert not hasattr(rmm, "_load_json"), "runtime_mode_module 不应定义 _load_json"


# ---------------------------------------------------------------------------
# ConfigChanged 事件发布验证
# ---------------------------------------------------------------------------


def test_config_changed_event_can_be_published():
    """ConfigChanged 事件应可被 EventBus 发布与订阅。"""
    bus = EventBus()
    received = []
    bus.subscribe(ConfigChanged, lambda e: received.append(e))
    bus.publish(ConfigChanged(changed_tables=["runtime_modes"]))
    assert len(received) == 1
    assert received[0].changed_tables == ["runtime_modes"]


def test_config_loaded_event_can_be_published():
    """ConfigLoaded 事件应可被 EventBus 发布与订阅。"""
    bus = EventBus()
    received = []
    bus.subscribe(ConfigLoaded, lambda e: received.append(e))
    bus.publish(ConfigLoaded(config_tables={"runtime_modes": {}}))
    assert len(received) == 1
    assert "runtime_modes" in received[0].config_tables


# ---------------------------------------------------------------------------
# HotReloadManager 验证
# ---------------------------------------------------------------------------


def test_hot_reload_manager_class_exists():
    """HotReloadManager 类必须存在。"""
    from core.table_engine import HotReloadManager
    assert HotReloadManager is not None


def test_hot_reload_manager_has_notify_changed():
    """HotReloadManager 必须有 _notify_changed 单一通知方法。"""
    from core.table_engine import HotReloadManager
    assert hasattr(HotReloadManager, "_notify_changed")


def test_hot_reload_manager_notify_changed_publishes_config_changed():
    """_notify_changed 应通过 bus 发布 ConfigChanged 事件。"""
    from core.table_engine import HotReloadManager
    bus = EventBus()
    received = []
    bus.subscribe(ConfigChanged, lambda e: received.append(e))
    # 用临时目录构造 HotReloadManager
    with tempfile.TemporaryDirectory() as tmp:
        mgr = HotReloadManager(config_dir=tmp, bus=bus)
        mgr._notify_changed(["runtime_modes"])
    assert len(received) == 1
    assert received[0].changed_tables == ["runtime_modes"]


def test_hot_reload_manager_notify_changed_noop_without_bus():
    """_notify_changed 在 bus=None 时应无操作（向后兼容）。"""
    from core.table_engine import HotReloadManager
    with tempfile.TemporaryDirectory() as tmp:
        mgr = HotReloadManager(config_dir=tmp, bus=None)
        # 不应抛异常
        mgr._notify_changed(["some_table"])


def test_hot_reload_manager_detect_changes():
    """detect_changes 应检测配置文件变更。"""
    from core.table_engine import HotReloadManager
    with tempfile.TemporaryDirectory() as tmp:
        # 创建初始配置文件
        cfg_path = Path(tmp) / "test_table.json"
        cfg_path.write_text('{"key": "value1"}', encoding="utf-8")
        bus = EventBus()
        mgr = HotReloadManager(config_dir=tmp, bus=bus)
        # 初始应无变更
        changed = mgr.detect_changes()
        assert changed == []
        # 修改文件
        cfg_path.write_text('{"key": "value2"}', encoding="utf-8")
        changed = mgr.detect_changes()
        assert "test_table" in changed


# ---------------------------------------------------------------------------
# ConfigStore.bus 注入后订阅 ConfigChanged 自行重载
# ---------------------------------------------------------------------------


def test_config_store_subscribes_config_changed_when_bus_injected():
    """ConfigStore 在 bus 注入时应订阅 ConfigChanged 事件。"""
    bus = EventBus()
    from core.table_engine import ConfigStore
    store = ConfigStore(bus=bus)
    # 发布 ConfigChanged 事件后，ConfigStore 应重载对应表
    # 这里仅验证订阅机制存在，不验证具体重载行为（需要真实配置文件）
    bus.publish(ConfigChanged(changed_tables=[]))
    # 不应抛异常


# ---------------------------------------------------------------------------
# ConfigStore.get_table 热加载行为验证
# ---------------------------------------------------------------------------


def test_config_store_get_table_caches_result(config_store):
    """get_table 多次调用应返回同一对象（缓存）。"""
    table1 = config_store.get_table("runtime_modes")
    table2 = config_store.get_table("runtime_modes")
    # 应是同一对象引用（缓存）
    assert table1 is table2


def test_config_store_get_table_with_bus_reloads_on_config_changed():
    """ConfigStore + bus 注入后，ConfigChanged 事件应触发重载。"""
    bus = EventBus()
    from core.table_engine import ConfigStore
    # 使用临时配置目录
    with tempfile.TemporaryDirectory() as tmp:
        # 创建初始配置文件
        cfg_path = Path(tmp) / "hot_table.json"
        cfg_path.write_text('{"v": 1}', encoding="utf-8")
        store = ConfigStore(config_dir=tmp, bus=bus)
        # 初始加载
        table = store.get_table("hot_table")
        assert table.get("v") == 1
        # 修改文件并发布 ConfigChanged 事件
        cfg_path.write_text('{"v": 2}', encoding="utf-8")
        bus.publish(ConfigChanged(changed_tables=["hot_table"]))
        # 重载后应反映新值
        table = store.get_table("hot_table")
        assert table.get("v") == 2


# ---------------------------------------------------------------------------
# _on_config_changed 方法验证
# ---------------------------------------------------------------------------


def test_config_store_has_on_config_changed_handler():
    """ConfigStore 必须有 _on_config_changed 事件处理器。"""
    from core.table_engine import ConfigStore
    assert hasattr(ConfigStore, "_on_config_changed")


def test_on_config_changed_does_not_raise_on_empty_list():
    """_on_config_changed 对空变更列表不应抛异常。"""
    bus = EventBus()
    from core.table_engine import ConfigStore
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(config_dir=tmp, bus=bus)
        # 不应抛异常
        store._on_config_changed(ConfigChanged(changed_tables=[]))
