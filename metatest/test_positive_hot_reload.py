"""正合测试：ConfigStore + HotReloadManager 配置存储与热加载。

覆盖场景：
1. ConfigStore.get_table(name) 加载配置表
2. ConfigStore.get_data_file(name) 加载 data/ 目录文件
3. ConfigStore 实例加载 config/ 目录
4. get_global_config_store() / set_global_config_store() 全局引用
5. HotReloadManager._notify_changed 是单一通知方法
6. ConfigChanged 事件在变更时发布
7. 不存在 _load_json / _load_config 函数（表驱动取代）
8. ConfigStore 校验配置表
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from core.table_engine import (
    ConfigStore,
    HotReloadManager,
    get_global_config_store,
    set_global_config_store,
)
from core import table_engine as te_module
from core.event_bus import ConfigChanged, EventBus


# ------------------------------------------------------------------
# 测试 1：ConfigStore.get_table(name) 加载配置表
# ------------------------------------------------------------------
def test_get_table_loads_config(config_store):
    """get_table 按名加载配置表，返回 dict。"""
    table = config_store.get_table("table_schemas")
    assert isinstance(table, dict)
    assert "schemas" in table
    assert isinstance(table.get("schemas"), dict)
    # table_schemas 至少有一个 schema 定义
    assert len(table.get("schemas", {})) > 0


def test_get_table_returns_dict_for_missing():
    """get_table 对不存在的表名返回空 dict（非 None）。"""
    cs = ConfigStore()
    table = cs.get_table("definitely_does_not_exist_xyz")
    assert isinstance(table, dict)
    assert table == {}


# ------------------------------------------------------------------
# 测试 2：ConfigStore.get_data_file(name) 加载 data/ 文件
# ------------------------------------------------------------------
def test_get_data_file_missing_returns_empty(config_store):
    """get_data_file 对不存在的文件返回空 dict 且不抛异常。"""
    data = config_store.get_data_file("nonexistent_data_file")
    assert isinstance(data, dict)
    assert data == {}


def test_get_data_file_loads_json(tmp_path):
    """get_data_file 从 config_dir 同级 data/ 目录加载 JSON 文件。"""
    # 构造临时目录：root/config/dummy.json + root/data/sample.json
    root = tmp_path
    config_dir = root / "config"
    data_dir = root / "data"
    config_dir.mkdir()
    data_dir.mkdir()
    (config_dir / "table_schemas.json").write_text(
        json.dumps({"version": "1.0", "schemas": {}}), encoding="utf-8"
    )
    (data_dir / "sample.json").write_text(
        json.dumps({"version": 1, "items": [1, 2, 3]}), encoding="utf-8"
    )
    cs = ConfigStore(config_dir=str(config_dir))
    data = cs.get_data_file("sample")
    assert isinstance(data, dict)
    assert data.get("version") == 1
    assert data.get("items") == [1, 2, 3]


def test_get_data_file_caches(config_store):
    """get_data_file 缓存：连续两次返回同一对象。"""
    d1 = config_store.get_data_file("cached_missing")
    d2 = config_store.get_data_file("cached_missing")
    assert d1 is d2


# ------------------------------------------------------------------
# 测试 3：ConfigStore 实例加载 config/ 目录
# ------------------------------------------------------------------
def test_config_store_loads_config_dir(config_store):
    """ConfigStore 实例自动加载 config/ 目录下所有 .json 配置表。"""
    names = config_store.table_names
    assert isinstance(names, list)
    # config/ 下有数十个 .json，至少加载了若干表
    assert len(names) > 0
    # 关键表应存在
    assert "table_schemas" in names
    assert "table_categories" in names


# ------------------------------------------------------------------
# 测试 4：get_global_config_store / set_global_config_store
# ------------------------------------------------------------------
def test_global_config_store_roundtrip(config_store):
    """set_global_config_store 注入后 get_global_config_store 返回同一实例。"""
    original = get_global_config_store()
    try:
        set_global_config_store(config_store)
        assert get_global_config_store() is config_store
        # 注入 None 后恢复 None
        set_global_config_store(None)
        assert get_global_config_store() is None
    finally:
        # 恢复原始引用，避免污染其他测试
        set_global_config_store(original)


def test_global_config_store_default_none():
    """未注入时 get_global_config_store 返回 None。"""
    original = get_global_config_store()
    try:
        set_global_config_store(None)
        assert get_global_config_store() is None
    finally:
        set_global_config_store(original)


# ------------------------------------------------------------------
# 测试 5 & 6：HotReloadManager._notify_changed 发布 ConfigChanged
# ------------------------------------------------------------------
def test_notify_changed_publishes_config_changed(tmp_path, event_collector):
    """_notify_changed 发布 ConfigChanged 事件到 EventBus。"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "dummy.json").write_text(
        json.dumps({"version": "1.0"}), encoding="utf-8"
    )
    from metatest.conftest import EventCollector
    bus = EventBus()
    collector = EventCollector(bus)
    mgr = HotReloadManager(
        config_dir=str(config_dir), config_store=None, bus=bus
    )
    try:
        mgr._notify_changed(["dummy"])
        types = collector.count_by_type()
        assert types.get("ConfigChanged", 0) == 1
        events = collector.filter(type="ConfigChanged")
        assert len(events) == 1
        assert events[0].changed_tables == ["dummy"]
    finally:
        collector.disconnect()


def test_notify_changed_empty_list_noop(tmp_path, event_collector):
    """_notify_changed 空列表时不发布事件（guard 子句）。"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    from metatest.conftest import EventCollector
    bus = EventBus()
    collector = EventCollector(bus)
    mgr = HotReloadManager(
        config_dir=str(config_dir), config_store=None, bus=bus
    )
    try:
        mgr._notify_changed([])
        types = collector.count_by_type()
        assert "ConfigChanged" not in types
    finally:
        collector.disconnect()


def test_notify_changed_no_bus_skipped(tmp_path):
    """_notify_changed 在 bus=None 时不发布（保持向后兼容）。"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    mgr = HotReloadManager(config_dir=str(config_dir), config_store=None, bus=None)
    # 不抛异常即通过
    mgr._notify_changed(["any"])
    # 验证 _notify_changed 是单一通知方法（非多个分散方法）
    assert callable(getattr(mgr, "_notify_changed", None))


def test_notify_changed_is_single_method(tmp_path):
    """HotReloadManager 仅有 _notify_changed 一个通知方法，无 _notify_* 分散方法。"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    mgr = HotReloadManager(config_dir=str(config_dir), bus=None)
    notify_methods = [
        name for name in dir(mgr) if name.startswith("_notify_")
    ]
    assert notify_methods == ["_notify_changed"]


# ------------------------------------------------------------------
# 测试 7：不存在 _load_json / _load_config 函数
# ------------------------------------------------------------------
def test_no_legacy_load_functions():
    """core.table_engine 不含 _load_json / _load_config 旧函数（已表驱动化）。"""
    public_names = dir(te_module)
    assert "_load_json" not in public_names
    assert "_load_config" not in public_names
    # 替代入口存在
    assert hasattr(te_module, "ConfigStore")
    assert hasattr(te_module, "get_global_config_store")


# ------------------------------------------------------------------
# 测试 8：ConfigStore 校验配置表
# ------------------------------------------------------------------
def test_config_store_validates_tables(config_store):
    """validate_table_with_report 返回含 valid/errors/schema 的报告。"""
    report = config_store.validate_table_with_report(
        "table_schemas", config_store.get_table("table_schemas")
    )
    assert isinstance(report, dict)
    assert "valid" in report
    assert "errors" in report
    assert "schema" in report
    assert isinstance(report["errors"], list)


def test_config_store_validate_rejects_bad_data(config_store):
    """validate_table_with_report 对非 dict 数据返回 invalid。"""
    report = config_store.validate_table_with_report("bad", "not_a_dict")
    assert report["valid"] is False
    assert len(report["errors"]) > 0


def test_register_validator(config_store):
    """register_validator 注册自定义校验器后被调用。"""
    called = []

    def custom_validator(data):
        called.append(data)
        return ["custom_error"]

    config_store.register_validator("__test_custom__", custom_validator)
    # 用一个简单 dict 触发校验路径
    report = config_store.validate_table_with_report(
        "__test_custom__", {"version": "1.0"}
    )
    assert len(called) == 1
    assert "custom_error" in report["errors"]


def test_set_schema_validator(config_store):
    """set_schema_validator 可注入三级校验器实例。"""
    sentinel = object()
    config_store.set_schema_validator(sentinel)
    assert config_store._schema_validator is sentinel


# ============================================================================
# 变更 G：公式模块配置加载经 ConfigStore 统一入口回归断言
# ============================================================================


class TestChangeGFormulaConfigStore:
    """变更 G：formula_module._load_simple_functions 经 ConfigStore 统一入口，禁止 json.load(open(。"""

    def test_load_simple_functions_uses_config_store_entry(self):
        """formula_module._load_simple_functions 经统一配置入口加载 data_pipeline 表。

        接受 load_config_table("data_pipeline")（ConfigStore 统一入口）或
        get_global_config_store().get_table("data_pipeline") 两种等价写法。
        """
        import inspect
        import core.formula_module as fm
        # FormulaRouter 类含 _load_simple_functions 静态方法
        router_cls = getattr(fm, "FormulaRouter", None)
        assert router_cls is not None, "formula_module 应含 FormulaRouter 类"
        method = getattr(router_cls, "_load_simple_functions", None)
        assert method is not None, "FormulaRouter 应含 _load_simple_functions 方法"
        src = inspect.getsource(method)
        # 必须引用 data_pipeline 配置表（经 ConfigStore 统一入口）
        assert "data_pipeline" in src, \
            "_load_simple_functions 应加载 data_pipeline 配置表"
        # 经 ConfigStore 统一入口：load_config_table 或 get_global_config_store().get_table
        uses_store_entry = (
            "load_config_table" in src
            or "get_global_config_store" in src
            or "get_table" in src
        )
        assert uses_store_entry, \
            "_load_simple_functions 应经 ConfigStore 统一入口加载（变更 G）"

    def test_no_json_load_open_in_core_except_table_engine(self):
        """Grep 验证：core/*.py 中 json.load(open( = 0（ConfigStore 内部 table_engine.py 除外）。"""
        import re
        core_dir = Path(__file__).resolve().parent.parent / "core"
        legacy_pattern = r"json\.load\(open\("
        violations = []
        for py_file in core_dir.glob("*.py"):
            if py_file.name == "table_engine.py":
                continue  # ConfigStore 基础设施层合法使用
            try:
                src = py_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            matches = re.findall(legacy_pattern, src)
            if matches:
                violations.append(f"{py_file.name}: {len(matches)} 处")
        assert len(violations) == 0, (
            f"core/*.py 不应含 json.load(open(（变更 G，ConfigStore 内部除外），"
            f"违规：{violations}"
        )

    def test_formula_module_loads_config_table_helper(self):
        """formula_module 从 table_engine 导入 load_config_table 统一入口。"""
        import inspect
        import core.formula_module as fm
        src = inspect.getsource(fm)
        assert "load_config_table" in src, \
            "formula_module 应导入/使用 load_config_table 统一配置入口（变更 G）"

    def test_table_engine_load_config_table_uses_global_store(self):
        """load_config_table 优先走 get_global_config_store().get_table（热加载友好）。"""
        import inspect
        from core import table_engine as te
        src = inspect.getsource(te.load_config_table)
        assert "get_global_config_store" in src, \
            "load_config_table 应优先走 get_global_config_store().get_table（变更 G）"
        assert "get_table" in src, \
            "load_config_table 应调用 store.get_table(name)"

    def test_no_legacy_load_json_in_table_engine(self):
        """table_engine 不再含 _load_json / _load_config 旧函数（已表驱动化）。"""
        from core import table_engine as te
        public_names = dir(te)
        assert "_load_json" not in public_names, \
            "table_engine 不应含 _load_json 旧函数（变更 G 已移除）"
        assert "_load_config" not in public_names, \
            "table_engine 不应含 _load_config 旧函数（变更 G 已移除）"
        assert hasattr(te, "load_config_table"), \
            "table_engine 应提供 load_config_table 统一入口"
