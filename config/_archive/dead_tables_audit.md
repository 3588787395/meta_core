# 死表审计报告 (Dead Tables Audit Report)

**审计日期**: 2026-07-28
**审计范围**: `config/architecture/`, `config/data/`, `config/runtime/`, `config/ui/`, `config/formulas/`, `config/pools/`
**审计目标**: 识别代码库中零引用的"死表"，为归档提供依据

## 审计摘要

| 指标 | 数值 |
|---|---|
| 审计表总数 | 87 |
| 死表数 (0 引用) | 9 |
| 存活表数 (≥1 引用) | 78 |
| 死表占比 | 10.3% |

## 审计方法

1. **枚举表**: 使用 Glob/LS 列出 6 个配置子目录下的所有 `.json` 文件
2. **搜索引用**: 使用 Grep 在以下 Python 代码位置搜索每张表的引用:
   - `core/` 目录下所有 `.py` 文件
   - `services/` 目录下所有 `.py` 文件
   - `native/` 目录下所有 `.py` 文件
   - `api.py`、`app.py`、`converters.py` 根级文件
3. **搜索模式**: 同时搜索两种引用形式:
   - 不带扩展名的引号字符串: `"table_name"` 或 `'table_name'`
   - 带扩展名的引号字符串: `"table_name.json"` 或 `'table_name.json'`
   - 裸词搜索 (针对零引用候选表进行二次确认)
4. **判定规则**: 保守策略 — 只要表名以任何引号字符串形式出现一次，即标记为 alive；仅当所有搜索均无匹配时才标记为 dead

## 死表明细 (9 张)

以下 9 张表在 `core/`、`services/`、`native/`、`api.py`、`app.py`、`converters.py` 中均无任何引用（包括带 `.json` 扩展名和不带扩展名的搜索）：

| # | 表名 | 所在目录 | 引用数 | 状态 | 建议操作 |
|---|---|---|---|---|---|
| 1 | `capability_registry.json` | architecture/ | 0 | dead | archive |
| 2 | `tdx_system_indicators.json` | data/ | 0 | dead | archive |
| 3 | `sim_demo_pool.json` | pools/ | 0 | dead | archive |
| 4 | `sim_test_pool.json` | pools/ | 0 | dead | archive |
| 5 | `sim_test_pool_100.json` | pools/ | 0 | dead | archive |
| 6 | `pre_tick_pipeline.json` | runtime/ | 0 | dead | archive |
| 7 | `flow_mode_rules.json` | runtime/ | 0 | dead | archive |
| 8 | `chart_config.json` | ui/ | 0 | dead | archive |
| 9 | `ui_state.json` | ui/ | 0 | dead | archive |

## 完整审计表 (87 张)

### architecture/ (15 张)

| 表名 | 引用数 | 状态 | 建议操作 | 引用位置示例 |
|---|---|---|---|---|
| capability_registry.json | 0 | dead | archive | — |
| cell_type_registry.json | ~6 | alive | keep | core/table_engine.py, app.py, native/validators.py |
| dispatch.json | ~3 | alive | keep | core/execution_module.py, core/engine.py |
| dzh_type_map.json | ~3 | alive | keep | core/execution_module.py, core/engine.py, app.py |
| edge_semantics.json | ~3 | alive | keep | core/execution_module.py, core/engine.py |
| edge_strategies.json | ~4 | alive | keep | core/execution_module.py, core/engine.py, app.py |
| engines.json | ~1 | alive | keep | core/engine.py |
| flow_mode_registry.json | ~10 | alive | keep | core/schemas.py, native/validators.py, native/builtins.py, app.py |
| modules.json | ~10 | alive | keep | core/execution_module.py, core/engine.py, app.py, native/validators.py |
| pool_roles.json | ~1 | alive | keep | core/engine.py |
| property_ownership.json | ~2 | alive | keep | core/table_engine.py |
| table_categories.json | ~6 | alive | keep | core/table_engine.py, app.py |
| table_schemas.json | ~9 | alive | keep | core/table_engine.py, app.py |
| tdx_psatt.json | ~10 | alive | keep | core/trade_module.py, core/execution_module.py, core/engine.py, core/schemas.py, converters.py |
| timing.json | ~3 | alive | keep | core/execution_module.py, core/engine.py, core/runtime_mode_module.py |

### data/ (28 张)

| 表名 | 引用数 | 状态 | 建议操作 | 引用位置示例 |
|---|---|---|---|---|
| builtin_formulas.json | ~2 | alive | keep | core/domain.py, api.py |
| custom_formulas.json | ~1 | alive | keep | api.py |
| data_config.json | ~1 | alive | keep | core/engine.py |
| data_mappings.json | ~2 | alive | keep | app.py |
| data_pipeline.json | ~2 | alive | keep | core/formula_module.py, services/data.py |
| data_providers.json | ~3 | alive | keep | services/tq_adapter.py, app.py |
| data_source_contract.json | ~1 | alive | keep | services/data.py |
| data_source_mappings.json | ~12 | alive | keep | core/screening_module.py, native/validators.py |
| data_source_routes.json | ~1 | alive | keep | services/providers.py |
| data_sources.json | ~3 | alive | keep | core/engine.py, app.py |
| dzh_market_mappings.json | ~2 | alive | keep | services/data.py, converters.py |
| formula_funcs.json | ~1 | alive | keep | core/formula_module.py |
| formula_modes.json | ~1 | alive | keep | native/builtins.py |
| formula_routing.json | ~1 | alive | keep | core/formula_module.py |
| local_file_paths.json | ~1 | alive | keep | services/providers.py |
| market_classifications.json | ~4 | alive | keep | services/providers.py |
| markets.json | ~3 | alive | keep | native/builtins.py, native/validators.py |
| mock_data.json | ~1 | alive | keep | core/runtime_mode_module.py |
| mock_field_ranges.json | ~1 | alive | keep | native/builtins.py |
| price_fields.json | ~1 | alive | keep | core/engine.py |
| tdx_element_schemas.json | ~1 | alive | keep | converters.py |
| tdx_enums.json | ~2 | alive | keep | core/table_engine.py, app.py |
| tdx_field_visibility.json | ~2 | alive | keep | app.py |
| tdx_indicator_formula_map.json | ~1 | alive | keep | core/execution_module.py |
| tdx_indicators.json | ~1 | alive | keep | app.py |
| tdx_noperate_rules.json | ~6 | alive | keep | core/screening_module.py, core/domain.py |
| tdx_ntjindexno_lookup.json | ~2 | alive | keep | app.py |
| tdx_system_indicators.json | 0 | dead | archive | — |

### formulas/ (1 张)

| 表名 | 引用数 | 状态 | 建议操作 | 备注 |
|---|---|---|---|---|
| builtin.json | ~1 | alive | keep | 引用为字符串值 `f["source"] = "builtin"`，可能为假阳性；保守标记为 alive |

### pools/ (5 张)

| 表名 | 引用数 | 状态 | 建议操作 | 引用位置示例 |
|---|---|---|---|---|
| pool_types.json | ~3 | alive | keep | core/table_engine.py, app.py |
| sim_demo_pool.json | 0 | dead | archive | — |
| sim_test_pool.json | 0 | dead | archive | — |
| sim_test_pool_100.json | 0 | dead | archive | — |
| target_pool_100.json | ~2 | alive | keep | app.py |

### runtime/ (20 张)

| 表名 | 引用数 | 状态 | 建议操作 | 引用位置示例 |
|---|---|---|---|---|
| attr_flag_map.json | ~4 | alive | keep | api.py, converters.py |
| defaults.json | ~6 | alive | keep | core/execution_module.py, core/engine.py, app.py |
| dzh_condition_fallback.json | ~1 | alive | keep | converters.py |
| dzh_extra_fields.json | ~1 | alive | keep | native/builtins.py |
| dzh_reload_schedule.json | ~2 | alive | keep | services/data.py, converters.py |
| event_rules.json | ~1 | alive | keep | core/engine.py |
| fallback_chain.json | ~1 | alive | keep | native/builtins.py |
| filter_action_rules.json | ~1 | alive | keep | converters.py |
| flow_mode_rules.json | 0 | dead | archive | — |
| highlight_rules.json | ~1 | alive | keep | app.py |
| history_schema.json | ~1 | alive | keep | core/trade_module.py |
| match_modes.json | ~1 | alive | keep | native/builtins.py |
| post_tick_pipeline.json | ~1 | alive | keep | core/engine.py |
| pre_tick_pipeline.json | 0 | dead | archive | — |
| runtime_modes.json | ~4 | alive | keep | core/engine.py, core/runtime_mode_module.py |
| side_effect_scopes.json | ~2 | alive | keep | core/engine.py |
| signal_rules.json | ~1 | alive | keep | core/engine.py |
| time_sources.json | ~3 | alive | keep | core/engine.py, core/runtime_mode_module.py |
| tracker_schema.json | ~1 | alive | keep | core/engine.py |
| trade_interfaces.json | ~3 | alive | keep | core/engine.py, core/runtime_mode_module.py |

### ui/ (18 张)

| 表名 | 引用数 | 状态 | 建议操作 | 备注 |
|---|---|---|---|---|
| action_pipeline.json | ~1 | alive | keep | native/builtins.py |
| action_rules.json | ~1 | alive | keep | core/table_engine.py |
| action_table.json | ~2 | alive | keep | core/trade_module.py, core/execution_module.py |
| actions.json | ~1 | alive | keep | 引用为字典键 `data.get("actions", {})`，可能为假阳性；保守标记为 alive |
| api_routes.json | ~1 | alive | keep | core/engine.py |
| behavior_actions.json | ~1 | alive | keep | native/validators.py |
| chart_config.json | 0 | dead | archive | — |
| column_definitions.json | ~1 | alive | keep | app.py |
| context_menu_config.json | ~1 | alive | keep | app.py |
| dashboard_schema.json | ~1 | alive | keep | core/monitoring_module.py |
| field_definitions.json | ~2 | alive | keep | core/table_engine.py |
| fields.json | ~1 | alive | keep | 引用为字典键 `schema['fields']`，可能为假阳性；保守标记为 alive |
| keyboard_shortcuts.json | ~1 | alive | keep | app.py |
| theme_config.json | ~1 | alive | keep | app.py |
| toolbar_config.json | ~1 | alive | keep | app.py |
| ui_components.json | ~12 | alive | keep | native/validators.py |
| ui_layouts.json | ~3 | alive | keep | core/table_engine.py, app.py |
| ui_state.json | 0 | dead | archive | — |

## 假阳性说明

以下 3 张表的引用匹配可能为假阳性（表名作为通用字典键或字符串值出现，而非真正的表引用）。按保守策略仍标记为 alive，建议人工复核：

| 表名 | 匹配形式 | 实际语义 |
|---|---|---|
| formulas/builtin.json | `f["source"] = "builtin"` | 字符串值赋值，非表引用 |
| ui/actions.json | `data.get("actions", {})` | 字典键访问，"actions" 是其他数据结构中的字段名 |
| ui/fields.json | `schema['read_mapping']['fields']` | 字典键访问，"fields" 是通用字段名 |

## 后续操作建议

1. **归档死表**: 将上述 9 张死表移动到 `config/_archive/` 目录（本报告仅做审计，未实际移动文件）
2. **更新锁文件**: 从 `config/.locks.json` 中删除死表条目
3. **人工复核假阳性**: 对 3 张假阳性表进行人工确认，如确认为死表可一并归档
4. **核心表标识**: 按照 Task 16 要求，在 `config/architecture/README.md` 中标注 8 张核心运行时表
