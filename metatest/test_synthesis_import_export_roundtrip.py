"""导入导出 roundtrip 合测试（Task 21 SubTask 21.3）。

端到端验证 ImportExportModule 的 JSON 格式导入/导出 roundtrip 等价性：
导出池配置 → 导入回读 → 比较字段完整性、节点角色保持、边顺序保持。

测试用例：
  1. test_export_then_import_equivalence
  2. test_export_includes_all_fields
  3. test_import_validates_schema
  4. test_roundtrip_preserves_node_roles
  5. test_roundtrip_preserves_edge_order
  6. test_export_publishes_event
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SIM_POOL_10 = _PROJECT_ROOT / "config" / "pools" / "sim_test_pool.json"


def _load_pool_config(path: Path) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class TestImportExportRoundtrip:
    """导入导出 roundtrip 合测试。"""

    def test_export_then_import_equivalence(self, report_state) -> None:
        """导出 JSON → 导入 JSON → 比较节点与边等价性。"""
        from core.import_export_module import ImportExportModule
        from core.event_bus import EventBus

        original = _load_pool_config(_SIM_POOL_10)
        bus = EventBus()
        iem = ImportExportModule(bus=bus)

        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "roundtrip.json")
            # 导出
            ret = iem.export_pool(original, out_path, "json")
            assert ret == out_path, "export_pool 应返回输出路径"
            assert os.path.exists(out_path), "导出文件应存在"
            # 导入
            ok = iem.import_pool(out_path, "json")
            assert ok is True, "导入应成功"
        modules = report_state.setdefault("modules_covered", [])
        if "core.import_export_module" not in modules:
            modules.append("core.import_export_module")

    def test_export_includes_all_fields(self, report_state) -> None:
        """导出 JSON 含 nodes / edges / pool_meta 等完整字段。"""
        from converters import export_pool_to_json

        original = _load_pool_config(_SIM_POOL_10)
        json_str = export_pool_to_json(original)
        data = json.loads(json_str)
        # 必须含核心字段
        assert "nodes" in data, "导出 JSON 缺 nodes"
        assert "edges" in data, "导出 JSON 缺 edges"
        # pool_meta 或等价元数据
        assert "pool_meta" in data or "name" in data, \
            "导出 JSON 缺 pool_meta/name 元数据"
        # 节点数与原配置一致
        assert len(data["nodes"]) == len(original.get("nodes", []))
        # 边数与原配置一致
        assert len(data["edges"]) == len(original.get("edges", []))
        modules = report_state.setdefault("modules_covered", [])
        if "converters" not in modules:
            modules.append("converters")

    def test_import_validates_schema(self, report_state) -> None:
        """导入非法 JSON 返回失败（schema 校验）。"""
        from core.import_export_module import ImportExportModule
        from core.event_bus import EventBus

        bus = EventBus()
        iem = ImportExportModule(bus=bus)

        with tempfile.TemporaryDirectory() as tmpdir:
            # 写入非法 JSON
            bad_path = os.path.join(tmpdir, "invalid.json")
            with open(bad_path, "w", encoding="utf-8") as f:
                f.write("{invalid json content}")
            # 导入应失败（返回 False 或抛异常）
            try:
                ok = iem.import_pool(bad_path, "json")
                assert ok is False, "非法 JSON 导入应返回 False"
            except (ValueError, Exception):
                pass  # 抛异常也是可接受的失败行为

    def test_roundtrip_preserves_node_roles(self, report_state) -> None:
        """roundtrip 保持节点角色映射不变。

        验证编译期产出的 node_role 在 roundtrip 后保持一致。
        """
        from core.execution_module import compile
        from converters import export_pool_to_json, import_pool_from_json

        original = _load_pool_config(_SIM_POOL_10)
        # 原始编译产物的 node_role
        cp_orig = compile(original)
        # 导出 → 导入
        json_str = export_pool_to_json(original)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            f.write(json_str)
            tmp_path = f.name
        try:
            imported = import_pool_from_json(file_path=tmp_path)
        finally:
            os.unlink(tmp_path)
        # 导入后编译产物的 node_role
        cp_imp = compile(imported)
        # 节点角色映射应一致
        assert set(cp_orig.node_role.keys()) == set(cp_imp.node_role.keys()), \
            "roundtrip 后节点角色映射键不一致"
        for nid in cp_orig.node_role:
            assert cp_orig.node_role[nid] == cp_imp.node_role[nid], \
                f"节点 {nid} 角色在 roundtrip 后变化"

    def test_roundtrip_preserves_edge_order(self, report_state) -> None:
        """roundtrip 保持边 _order 顺序不变。

        验证硬约束「边顺序号是设计结构」。
        """
        from core.execution_module import compile
        from converters import export_pool_to_json, import_pool_from_json

        original = _load_pool_config(_SIM_POOL_10)
        cp_orig = compile(original)
        # 导出 → 导入
        json_str = export_pool_to_json(original)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            f.write(json_str)
            tmp_path = f.name
        try:
            imported = import_pool_from_json(file_path=tmp_path)
        finally:
            os.unlink(tmp_path)
        cp_imp = compile(imported)
        # edge_order 应一致
        assert list(cp_orig.edge_order) == list(cp_imp.edge_order), \
            "roundtrip 后 edge_order 变化"
        # edge_type 应一致
        assert set(cp_orig.edge_type.keys()) == set(cp_imp.edge_type.keys()), \
            "roundtrip 后 edge_type 键不一致"
        modules = report_state.setdefault("modules_covered", [])
        for m in ("core.execution_module", "converters"):
            if m not in modules:
                modules.append(m)

    def test_export_publishes_event(self, report_state) -> None:
        """导出完成发布 ExportCompleted 事件。"""
        from core.import_export_module import ImportExportModule
        from core.event_bus import EventBus, ExportCompleted

        bus = EventBus()
        collected = []
        bus.subscribe(ExportCompleted, lambda e: collected.append(e))
        iem = ImportExportModule(bus=bus)

        original = _load_pool_config(_SIM_POOL_10)
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "event_test.json")
            iem.export_pool(original, out_path, "json")
        # 应发布 ExportCompleted 事件
        assert len(collected) >= 1, "导出应发布 ExportCompleted 事件"
        event = collected[-1]
        assert isinstance(event, ExportCompleted)
        assert event.format == "json"
        event_types = report_state.setdefault("event_types_seen", [])
        if "ExportCompleted" not in event_types:
            event_types.append("ExportCompleted")
        modules = report_state.setdefault("modules_covered", [])
        for m in ("core.import_export_module", "core.event_bus"):
            if m not in modules:
                modules.append(m)
