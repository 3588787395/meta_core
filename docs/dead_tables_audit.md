# Meta Core 配置表全量引用审计报告

**审计日期**: 2026-06-26  
**审计范围**: `meta_core/config/` 目录下所有 `.json` 配置表  
**排除文件**: `.locks.json`, `table_categories.json`（系统文件）  
**搜索范围**: 整个 `meta_core/` 目录下的 `.py` 和 `.js` 文件（含间接引用）

---

## 一、统计摘要

| 类别 | 数量 | 占比 |
|------|------|------|
| 总配置表数 | 92 | 100% |
| 死表（0次引用） | 4 | 4.3% |
| 疑似死表（1-2次引用） | 10 | 10.9% |
| 活跃表（3次以上引用） | 78 | 84.8% |

**引用计数说明**:
- **代码引用**: 在 `.py` 和 `.js` 源代码中精确匹配表名字符串的次数
- **间接引用**: 通过其他配置表的 `config_table` 等字段动态引用的次数
- **总计**: 代码引用 + 间接引用

---

## 二、死表清单（0次引用）

共 **4** 张表，在代码和配置中均未发现任何引用。

| 序号 | 表名 | 代码引用 | 间接引用 | 总计 | 所属分类 |
|------|------|----------|----------|------|----------|
| 1 | `decision_rules` | 0 | 0 | 0 | 业务规则 |
| 2 | `dzh_trading_calendar` | 0 | 0 | 0 | 平台适配与模拟 |
| 3 | `dzh_ttl_units` | 0 | 0 | 0 | 平台适配与模拟 |
| 4 | `operators` | 0 | 0 | 0 | 业务规则 |

### 2.1 死表详情

#### 1. decision_rules
- **文件路径**: `config/decision_rules.json`
- **分类**: 业务规则 → 决策规则表
- **描述**: BUY/SELL/HOLD阈值与条件
- **二次确认结果**:
  - Python 代码：无引用
  - JavaScript 代码：无引用
  - 其他配置表间接引用：无
  - 大小写变体：无
  - 字符串拼接形式：无

#### 2. dzh_trading_calendar
- **文件路径**: `config/dzh_trading_calendar.json`
- **分类**: 平台适配与模拟 → DZH交易日历
- **描述**: 中国A股交易日历（2024-2026简化版），用于DZH deltype=1交易日计数
- **二次确认结果**:
  - Python 代码：无引用
  - JavaScript 代码：无引用
  - 其他配置表间接引用：无
  - 备注：在 `tdx_psatt.json` 中有 `dzh_trading_calendar_ref` 字段指向该文件名，但仅作为文档参考，非代码引用

#### 3. dzh_ttl_units
- **文件路径**: `config/dzh_ttl_units.json`
- **分类**: 平台适配与模拟 → DZH删除时间单位
- **描述**: DZH状态池删除时间语义：deltype单位与delstocktype时机模式
- **二次确认结果**:
  - Python 代码：无引用
  - JavaScript 代码：无引用
  - 其他配置表间接引用：无
  - 备注：在 `tdx_psatt.json` 中有 `dzh_ttl_units_ref` 字段指向该文件名，但仅作为文档参考，非代码引用

#### 4. operators
- **文件路径**: `config/operators.json`
- **分类**: 业务规则 → 运算符定义
- **描述**: 条件表达式运算符注册表
- **二次确认结果**:
  - Python 代码：无引用（`formula_router.py` 中的 `_OPERATORS` 为变量名，非表引用）
  - JavaScript 代码：无引用（node_modules 中的 operators 为第三方库内容）
  - 其他配置表间接引用：无
  - 大小写变体：无有效匹配

---

## 三、疑似死表清单（1-2次引用）

共 **10** 张表，引用次数较少，建议重点关注。

| 序号 | 表名 | 代码引用 | 间接引用 | 总计 | 备注 |
|------|------|----------|----------|------|------|
| 1 | `api_routes` | 1 | 0 | 1 | API路由表，可能仅在启动时加载一次 |
| 2 | `dzh_extra_fields` | 1 | 0 | 1 | DZH扩展字段 |
| 3 | `alert_rules` | 0 | 2 | 2 | 通过 post_tick_pipeline 间接引用 |
| 4 | `dashboard_schema` | 0 | 2 | 2 | 通过 post_tick_pipeline 间接引用 |
| 5 | `dzh_cell_type_schema` | 2 | 0 | 2 | DZH单元格类型模式 |
| 6 | `formula_funcs` | 2 | 0 | 2 | 公式函数配置 |
| 7 | `mock_field_ranges` | 2 | 0 | 2 | 模拟字段范围 |
| 8 | `pk_config` | 0 | 2 | 2 | 通过 post_tick_pipeline 间接引用 |
| 9 | `tdx_indicator_formula_map` | 2 | 0 | 2 | TDX指标公式映射 |
| 10 | `tdx_indicators` | 2 | 0 | 2 | TDX指标定义 |

### 3.1 疑似死表说明

- **alert_rules / dashboard_schema / pk_config**: 这三张表虽然代码直接引用为0，但通过 `post_tick_pipeline.json` 的 `config_table` 字段被动态引用（`core/engine.py:3607`），属于运行时动态加载，实际是在用的表。
- **api_routes**: 在 `engine.py` 初始化时被排除在 `self.tables` 之外，可能有独立的加载路径。
- **其他表**: 引用次数较少，建议进一步确认其使用场景。

---

## 四、活跃表清单（3次以上引用）

共 **78** 张表，按引用次数升序排列。

### 4.1 低活跃表（3-9次引用）

| 引用次数 | 表名 |
|----------|------|
| 3 | column_definitions, data_mappings, price_fields, topology_patterns |
| 4 | action_pipeline, action_table, filter_action_rules, formula_modes, match_modes, runtime_tables_schema, theme_config |
| 5 | dzh_reload_schedule, local_file_paths, tdx_field_visibility, xml_mapping |
| 6 | analysis_config, context_menu_config, dzh_condition_fallback, fallback_chain, flow_mode_rules, highlight_rules, history_schema, keyboard_shortcuts, pre_tick_pipeline, signal_rules, tdx_noperate_rules, tdx_ntjindexno_lookup |
| 7 | tdx_element_schemas, value_extractors |
| 8 | capability_registry, chart_config, formula_routing, post_tick_pipeline, trade_interfaces, ui_state |
| 9 | market_classifications, tdx_system_indicators, tracker_schema |

### 4.2 中活跃表（10-49次引用）

| 引用次数 | 表名 |
|----------|------|
| 10 | custom_formulas |
| 11 | data_pipeline, event_rules, mock_data, runtime_modes, toolbar_config |
| 12 | data_source_routes, pool_types |
| 13 | builtin_formulas, tdx_enums |
| 14 | time_sources |
| 17 | dzh_market_mappings, edge_semantics, ui_components |
| 20 | data_source_mappings, edge_strategies, table_schemas |
| 21 | data_config, engines |
| 25 | data_providers |
| 26 | pool_roles, data_source_contract |
| 36 | behavior_actions, dzh_type_map |
| 37 | property_ownership |
| 41 | field_definitions |
| 42 | flow_mode_registry |
| 43 | attr_flag_map |
| 47 | action_rules |

### 4.3 高活跃表（50次以上引用）

| 排名 | 表名 | 引用次数 | 占比 |
|------|------|----------|------|
| 1 | `fields` | 447 | 核心基础表 |
| 2 | `markets` | 197 | 核心基础表 |
| 3 | `dispatch` | 194 | 核心调度表 |
| 4 | `actions` | 132 | 核心动作表 |
| 5 | `modules` | 130 | 核心模块表 |
| 6 | `timing` | 117 | 核心时序表 |
| 7 | `defaults` | 105 | 全局默认值 |
| 8 | `tdx_psatt` | 96 | TDX属性表 |
| 9 | `cell_type_registry` | 70 | 核心注册表 |
| 10 | `topology` | 70 | 拓扑模式表 |

---

## 五、审计方法说明

### 5.1 搜索范围
- **配置表目录**: `meta_core/config/*.json`
- **排除系统文件**: `.locks.json`, `table_categories.json`
- **代码搜索范围**: `meta_core/` 下所有 `.py` 和 `.js` 文件
- **排除目录**: `node_modules/`, `.git/`

### 5.2 引用计数规则

#### 直接引用（代码引用）
在源代码中精确匹配表名字符串，包括：
- 字符串字面量：`"operators"`, `'operators'`
- 字典键访问：`self.tables["operators"]`
- 函数参数：`_load_cfg("operators.json")`
- 变量名包含：不单独计数（避免误判）

#### 间接引用
通过其他配置表的字段动态引用，包括：
- `config_table` 字段（如 `post_tick_pipeline.json` 中的引用）
- 其他配置表中值为表名的字段

### 5.3 二次确认方法

对死表进行了以下额外检查：
1. **大小写变体搜索**: 忽略大小写的匹配
2. **下划线变体搜索**: 移除下划线后的匹配
3. **字符串拼接检测**: 检测可能的字符串拼接形式
4. **JS 源码确认**: 排除 `node_modules` 第三方库干扰
5. **配置表交叉引用**: 检查所有配置表中的引用

---

## 六、建议

### 6.1 死表处理建议（4张）

| 表名 | 建议 | 风险等级 |
|------|------|----------|
| `decision_rules` | 确认是否为预留表，如无用可删除 | 低 |
| `dzh_trading_calendar` | 确认是否为预留表，如无用可删除 | 低 |
| `dzh_ttl_units` | 确认是否为预留表，如无用可删除 | 低 |
| `operators` | 确认是否被公式引擎替代，如无用可删除 | 中 |

### 6.2 疑似死表处理建议（10张）

建议逐一确认使用场景，特别关注：
- 仅通过间接引用的表（alert_rules, dashboard_schema, pk_config）
- 引用次数极少的表（api_routes, dzh_extra_fields）

### 6.3 后续优化建议

1. 建立配置表生命周期管理机制
2. 新增表时必须有对应的代码引用或文档说明
3. 定期执行引用审计（如每季度一次）
4. 考虑为预留表添加 `status: "reserved"` 标记

---

## 七、附录：全表引用次数明细表

| 序号 | 表名 | 代码引用 | 间接引用 | 总计 |
|------|------|----------|----------|------|
| 1 | decision_rules | 0 | 0 | 0 |
| 2 | dzh_trading_calendar | 0 | 0 | 0 |
| 3 | dzh_ttl_units | 0 | 0 | 0 |
| 4 | operators | 0 | 0 | 0 |
| 5 | api_routes | 1 | 0 | 1 |
| 6 | dzh_extra_fields | 1 | 0 | 1 |
| 7 | alert_rules | 0 | 2 | 2 |
| 8 | dashboard_schema | 0 | 2 | 2 |
| 9 | dzh_cell_type_schema | 2 | 0 | 2 |
| 10 | formula_funcs | 2 | 0 | 2 |
| 11 | mock_field_ranges | 2 | 0 | 2 |
| 12 | pk_config | 0 | 2 | 2 |
| 13 | tdx_indicator_formula_map | 2 | 0 | 2 |
| 14 | tdx_indicators | 2 | 0 | 2 |
| 15 | column_definitions | 3 | 0 | 3 |
| 16 | data_mappings | 3 | 0 | 3 |
| 17 | price_fields | 3 | 0 | 3 |
| 18 | topology_patterns | 1 | 2 | 3 |
| 19 | action_pipeline | 4 | 0 | 4 |
| 20 | action_table | 4 | 0 | 4 |
| 21 | filter_action_rules | 4 | 0 | 4 |
| 22 | formula_modes | 2 | 2 | 4 |
| 23 | match_modes | 3 | 1 | 4 |
| 24 | runtime_tables_schema | 4 | 0 | 4 |
| 25 | theme_config | 4 | 0 | 4 |
| 26 | dzh_reload_schedule | 5 | 0 | 5 |
| 27 | local_file_paths | 5 | 0 | 5 |
| 28 | tdx_field_visibility | 5 | 0 | 5 |
| 29 | xml_mapping | 5 | 0 | 5 |
| 30 | analysis_config | 4 | 2 | 6 |
| 31 | context_menu_config | 6 | 0 | 6 |
| 32 | dzh_condition_fallback | 6 | 0 | 6 |
| 33 | fallback_chain | 6 | 0 | 6 |
| 34 | flow_mode_rules | 6 | 0 | 6 |
| 35 | highlight_rules | 6 | 0 | 6 |
| 36 | history_schema | 6 | 0 | 6 |
| 37 | keyboard_shortcuts | 6 | 0 | 6 |
| 38 | pre_tick_pipeline | 6 | 0 | 6 |
| 39 | signal_rules | 6 | 0 | 6 |
| 40 | tdx_noperate_rules | 6 | 0 | 6 |
| 41 | tdx_ntjindexno_lookup | 6 | 0 | 6 |
| 42 | tdx_element_schemas | 7 | 0 | 7 |
| 43 | value_extractors | 7 | 0 | 7 |
| 44 | capability_registry | 8 | 0 | 8 |
| 45 | chart_config | 8 | 0 | 8 |
| 46 | formula_routing | 8 | 0 | 8 |
| 47 | post_tick_pipeline | 8 | 0 | 8 |
| 48 | trade_interfaces | 7 | 1 | 8 |
| 49 | ui_state | 8 | 0 | 8 |
| 50 | market_classifications | 9 | 0 | 9 |
| 51 | tdx_system_indicators | 9 | 0 | 9 |
| 52 | tracker_schema | 9 | 0 | 9 |
| 53 | custom_formulas | 10 | 0 | 10 |
| 54 | data_pipeline | 11 | 0 | 11 |
| 55 | event_rules | 11 | 0 | 11 |
| 56 | mock_data | 11 | 0 | 11 |
| 57 | runtime_modes | 11 | 0 | 11 |
| 58 | toolbar_config | 11 | 0 | 11 |
| 59 | data_source_routes | 12 | 0 | 12 |
| 60 | builtin_formulas | 13 | 0 | 13 |
| 61 | pool_types | 12 | 1 | 13 |
| 62 | tdx_enums | 13 | 0 | 13 |
| 63 | time_sources | 13 | 1 | 14 |
| 64 | dzh_market_mappings | 17 | 0 | 17 |
| 65 | edge_semantics | 17 | 0 | 17 |
| 66 | ui_components | 17 | 0 | 17 |
| 67 | data_source_mappings | 20 | 0 | 20 |
| 68 | edge_strategies | 20 | 0 | 20 |
| 69 | table_schemas | 20 | 0 | 20 |
| 70 | data_config | 21 | 0 | 21 |
| 71 | engines | 21 | 1 | 22 |
| 72 | data_providers | 25 | 0 | 25 |
| 73 | pool_roles | 26 | 0 | 26 |
| 74 | data_source_contract | 26 | 2 | 28 |
| 75 | behavior_actions | 36 | 0 | 36 |
| 76 | dzh_type_map | 36 | 0 | 36 |
| 77 | property_ownership | 37 | 0 | 37 |
| 78 | field_definitions | 41 | 0 | 41 |
| 79 | flow_mode_registry | 42 | 0 | 42 |
| 80 | attr_flag_map | 43 | 0 | 43 |
| 81 | action_rules | 47 | 0 | 47 |
| 82 | ui_layouts | 53 | 0 | 53 |
| 83 | cell_type_registry | 70 | 0 | 70 |
| 84 | topology | 70 | 0 | 70 |
| 85 | tdx_psatt | 96 | 0 | 96 |
| 86 | defaults | 105 | 0 | 105 |
| 87 | timing | 117 | 0 | 117 |
| 88 | modules | 129 | 1 | 130 |
| 89 | actions | 130 | 2 | 132 |
| 90 | dispatch | 188 | 6 | 194 |
| 91 | markets | 194 | 3 | 197 |
| 92 | fields | 447 | 0 | 447 |

---

**报告生成时间**: 2026-06-26  
**审计工具**: 自定义 Python 脚本（`audit_tables_v2.py`）  
**数据文件**: `audit_results_v2.json`（完整结构化数据）
