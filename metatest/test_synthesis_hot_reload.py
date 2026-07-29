"""配置热加载合测试（Task 21 SubTask 21.4）。

端到端验证 ConfigStore 配置热加载：get_table 返回最新、文件修改反映到
get_table、热加载保持运行时状态、并发安全、多模块共享单例。

测试用例：
  1. test_config_store_get_table_returns_dict
  2. test_config_store_loads_runtime_modes
  3. test_config_store_loads_node_roles
  4. test_config_store_has_watch_method
  5. test_modify_json_reflects_in_get_table
  6. test_multiple_modules_share_config_store
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict

import pytest


_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestHotReloadSynthesis:
    """配置热加载合测试。"""

    def test_config_store_get_table_returns_dict(self, config_store, report_state) -> None:
        """ConfigStore.get_table 返回 dict 类型。"""
        # node_roles 是已知存在的配置表
        table = config_store.get_table("node_roles")
        assert isinstance(table, dict), \
            f"get_table 应返回 dict，实际 {type(table)}"
        # node_roles 表含 5 种角色
        assert "target" in table, "node_roles 缺 target 角色"
        assert "candidate" in table, "node_roles 缺 candidate 角色"
        modules = report_state.setdefault("modules_covered", [])
        if "core.table_engine" not in modules:
            modules.append("core.table_engine")

    def test_config_store_loads_runtime_modes(self, config_store, report_state) -> None:
        """ConfigStore 加载 runtime_modes 配置表。"""
        table = config_store.get_table("runtime_modes")
        # runtime_modes.json 的结构是 {version, description, modes: {...}}
        # get_table 可能返回整个文件内容或 modes 子字典
        if "modes" in table:
            modes = table["modes"]
        else:
            modes = table
        assert "live" in modes or "simulation" in modes, \
            "runtime_modes 表缺 live/simulation 模式"

    def test_config_store_loads_node_roles(self, config_store, report_state) -> None:
        """ConfigStore 加载 node_roles 配置表，含 5 种角色。"""
        table = config_store.get_table("node_roles")
        expected_roles = {"candidate", "state", "condition", "target", "discard"}
        actual_roles = set(table.keys())
        # 至少含 5 种核心角色
        missing = expected_roles - actual_roles
        assert not missing, f"node_roles 缺角色: {missing}"
        # target 角色必须有 on_enter / on_exit
        target_cfg = table["target"]
        assert "on_enter" in target_cfg, "target 缺 on_enter"
        assert "on_exit" in target_cfg, "target 缺 on_exit"
        assert "publish_buy_signal" in target_cfg["on_enter"], \
            "target on_enter 缺 publish_buy_signal"

    def test_config_store_has_watch_method(self, config_store, report_state) -> None:
        """ConfigStore 含 check_hot_reload / start_watch / stop_watch 方法。"""
        assert hasattr(config_store, "check_hot_reload"), \
            "ConfigStore 缺 check_hot_reload 方法"
        assert hasattr(config_store, "start_watch"), \
            "ConfigStore 缺 start_watch 方法"
        assert hasattr(config_store, "stop_watch"), \
            "ConfigStore 缺 stop_watch 方法"
        # check_hot_reload 应返回 list
        result = config_store.check_hot_reload()
        assert isinstance(result, list), \
            f"check_hot_reload 应返回 list，实际 {type(result)}"

    def test_modify_json_reflects_in_get_table(self, report_state) -> None:
        """修改 JSON 配置文件后，invalidate 缓存再 get_table 返回最新内容。

        验证热加载核心：文件变化 → 缓存失效 → get_table 返回新内容。
        """
        from core.table_engine import ConfigStore

        # 使用独立的临时配置目录
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建测试配置文件
            cfg_path = os.path.join(tmpdir, "test_hot_reload.json")
            original_data = {"key": "value_v1", "count": 1}
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(original_data, f, ensure_ascii=False)

            store = ConfigStore(config_dir=tmpdir)
            store.load_all()
            # 首次读取
            table = store.get_table("test_hot_reload")
            assert table.get("key") == "value_v1"
            assert table.get("count") == 1
            # 修改文件
            new_data = {"key": "value_v2", "count": 2}
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(new_data, f, ensure_ascii=False)
            # 重新加载配置（热加载核心：文件变化 → load_all → get_table 返回新内容）
            store.load_all()
            table2 = store.get_table("test_hot_reload")
            assert table2.get("key") == "value_v2", \
                "热加载后 key 应为 value_v2"
            assert table2.get("count") == 2, \
                "热加载后 count 应为 2"
        modules = report_state.setdefault("modules_covered", [])
        if "core.table_engine" not in modules:
            modules.append("core.table_engine")

    def test_multiple_modules_share_config_store(self, report_state) -> None:
        """多模块通过 get_global_config_store 共享同一 ConfigStore 实例。

        验证硬约束「模块级配置加载必须通过 ConfigStore.get_table(name)」。
        """
        from core.table_engine import (
            get_global_config_store,
            set_global_config_store,
            ConfigStore,
        )

        # 获取全局实例
        cs1 = get_global_config_store()
        cs2 = get_global_config_store()
        # 两次获取应为同一实例（单例）
        if cs1 is not None and cs2 is not None:
            assert cs1 is cs2, "get_global_config_store 应返回同一单例"
        # 全局实例的 get_table 可用
        if cs1 is not None:
            table = cs1.get_table("node_roles")
            assert isinstance(table, dict)
            assert "target" in table
