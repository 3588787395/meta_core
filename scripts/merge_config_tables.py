"""配置表收敛脚本（Task 11）。

将 ``meta_core/config/`` 下职责重叠的旧表合并到 30 张目标核心引擎配置表中，
原表保留在原位置以确保向后兼容与审计追溯，同时把被合并的旧表复制到
``config/_archived/`` 作为归档。

合并策略：在目标表中新增以旧表 stem 命名的顶层键，旧表完整内容作为该键的值，
因此不会覆盖目标表已有字段，也不会丢失旧表语义。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List


CONFIG_DIR = Path(__file__).parent.parent / "config"
ARCHIVE_DIR = CONFIG_DIR / "_archived"

# 30 张目标核心引擎配置表（按 execute-architecture-migration Task 11 规格）
TARGET_TABLES = {
    "timing.json",
    "edge_strategies.json",
    "dispatch.json",
    "engines.json",
    "modules.json",
    "tdx_psatt.json",
    "fallback_chain.json",
    "runtime_modes.json",
    "time_sources.json",
    "data_sources.json",
    "trade_interfaces.json",
    "side_effect_scopes.json",
    "post_tick_pipeline.json",
    "pre_tick_pipeline.json",
    "edge_semantics.json",
    "capability_registry.json",
    "pk_config.json",
    "analysis_config.json",
    "dashboard_schema.json",
    "alert_rules.json",
    "event_rules.json",
    "signal_rules.json",
    "pool_roles.json",
    "action_table.json",
    "cell_type_registry.json",
    "dzh_type_map.json",
    "defaults.json",
    "field_definitions.json",
    "xml_mapping.json",
    "data_config.json",
}

# 旧表 -> 目标表的合并映射。每个旧表会被归档到 config/_archived/。
MERGE_MAP: Dict[str, List[str]] = {
    "engines.json": [
        "formula_funcs.json",
        "formula_routing.json",
        "formula_modes.json",
        "builtin_formulas.json",
        "custom_formulas.json",
    ],
    "dispatch.json": [
        "tdx_system_indicators.json",
        "tdx_indicators.json",
        "tdx_ntjindexno_lookup.json",
        "tdx_indicator_formula_map.json",
        "tdx_noperate_rules.json",
    ],
    "action_table.json": [
        "behavior_actions.json",
        "filter_action_rules.json",
        "actions.json",
        "action_rules.json",
        "action_pipeline.json",
    ],
    "data_config.json": [
        "data_source_contract.json",
        "data_source_mappings.json",
        "data_source_routes.json",
        "data_providers.json",
        "mock_data.json",
        "mock_field_ranges.json",
        "data_mappings.json",
        "local_file_paths.json",
    ],
    "edge_strategies.json": [
        "flow_mode_registry.json",
        "flow_mode_rules.json",
        "topology.json",
        "topology_patterns.json",
    ],
    "field_definitions.json": [
        "fields.json",
        "column_definitions.json",
        "price_fields.json",
        "tdx_field_visibility.json",
    ],
    "dzh_type_map.json": [
        "dzh_cell_type_schema.json",
        "dzh_extra_fields.json",
        "dzh_market_mappings.json",
        "dzh_condition_fallback.json",
        "dzh_reload_schedule.json",
    ],
    "defaults.json": [
        "match_modes.json",
        "tdx_enums.json",
        "value_extractors.json",
    ],
    "pre_tick_pipeline.json": [
        "data_pipeline.json",
    ],
}


def _load_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: Path, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def merge() -> Dict[str, List[str]]:
    """执行合并并返回实际归档清单。"""
    ARCHIVE_DIR.mkdir(exist_ok=True)
    archived: Dict[str, List[str]] = {}

    for target_name, source_names in MERGE_MAP.items():
        target_path = CONFIG_DIR / target_name
        if not target_path.exists():
            # 目标表不存在时创建一个空字典容器
            target_data: Dict[str, Any] = {"version": "2.0"}
        else:
            target_data = _load_json(target_path)
            if not isinstance(target_data, dict):
                # 极少数目标表为列表时，包装为字典保留原内容
                target_data = {"_original": target_data, "version": "2.0"}

        merged_sources: List[str] = []
        for source_name in source_names:
            source_path = CONFIG_DIR / source_name
            if not source_path.exists():
                continue

            # 归档：复制到 _archived/，保留原位置
            archive_path = ARCHIVE_DIR / source_name
            shutil.copy2(source_path, archive_path)

            stem = Path(source_name).stem
            if stem in target_data:
                raise RuntimeError(
                    f"目标表 {target_name} 已存在键 {stem}，无法安全合并 {source_name}"
                )
            target_data[stem] = _load_json(source_path)
            merged_sources.append(source_name)

        if merged_sources:
            # 记录收敛来源，便于追踪
            target_data.setdefault("_convergence", {})
            target_data["_convergence"]["merged_from"] = merged_sources
            target_data["_convergence"]["target_count"] = len(TARGET_TABLES)
            _save_json(target_path, target_data)
            archived[target_name] = merged_sources

    return archived


def verify() -> None:
    """简单自校验：目标表存在且被合并旧表字段可找到。"""
    missing_targets = [t for t in TARGET_TABLES if not (CONFIG_DIR / t).exists()]
    if missing_targets:
        raise RuntimeError(f"缺失目标表: {missing_targets}")

    for target_name, source_names in MERGE_MAP.items():
        target_path = CONFIG_DIR / target_name
        target_data = _load_json(target_path)
        if not isinstance(target_data, dict):
            raise RuntimeError(f"目标表 {target_name} 不是字典，无法验证合并")
        for source_name in source_names:
            source_path = CONFIG_DIR / source_name
            if not source_path.exists():
                continue
            stem = Path(source_name).stem
            if stem not in target_data:
                raise RuntimeError(
                    f"目标表 {target_name} 缺少被合并旧表 {source_name} 的内容（键 {stem}）"
                )
            old_data = _load_json(source_path)
            if target_data[stem] != old_data:
                raise RuntimeError(f"目标表 {target_name}.{stem} 与原表 {source_name} 不一致")
            archive_path = ARCHIVE_DIR / source_name
            if not archive_path.exists():
                raise RuntimeError(f"旧表 {source_name} 未归档到 {ARCHIVE_DIR}")

    print("配置表合并校验通过")


if __name__ == "__main__":
    archived = merge()
    print("已归档的旧表：")
    for target, sources in archived.items():
        print(f"  {target} <- {sources}")
    verify()
