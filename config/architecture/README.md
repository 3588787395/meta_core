# Configuration Tables

## 8 Core Runtime Tables (引擎核心循环直接读取)

| # | 表名 | 作用 | 运行时机 |
|---|------|------|---------|
| 1 | `timing.json` | 时间触发规则（starttype + cxtype） | gate 判定 |
| 2 | `filter_specs.json` | 过滤条件规格（nset + noperate） | filter 计算 |
| 3 | `propagate_modes.json` | 传播模式（copy/move/overwrite） | propagate |
| 4 | `node_roles.json` | 节点角色行为定义 | 事件/信号生成 |
| 5 | `edge_semantics.json` | 边类型语义（条件/无条件） | 边类型判定 |
| 6 | `runtime_modes.json` | 运行模式（实盘/回放/仿真） | 模式切换 |
| 7 | `alert_rules.json` | 预警规则 | 预警检查 |
| 8 | `ttl_rules.json` | TTL 淘汰规则 | TTL 计算 |

引擎核心循环只直接读这 8 张表。

## Peripheral Tables (外围表)

### UI 表 (`config/ui/`)
- `action_table.json` - 动作表（信号触发动作）
- `action_rules.json` - 动作规则
- `actions.json` - 动作定义
- `api_routes.json` - API 路由
- `column_definitions.json` - 列定义
- `context_menu_config.json` - 右键菜单
- `dashboard_schema.json` - 仪表盘
- `field_definitions.json` - 字段定义
- `fields.json` - 字段
- `keyboard_shortcuts.json` - 键盘快捷键
- `theme_config.json` - 主题配置
- `toolbar_config.json` - 工具栏
- `ui_components.json` - UI 组件
- `ui_layouts.json` - UI 布局

### Data 表 (`config/data/`)
- `data_config.json` - 数据配置
- `data_mappings.json` - 数据映射
- `data_pipeline.json` - 数据管道
- `data_providers.json` - 数据提供者
- `data_source_contract.json` - 数据源契约
- `data_source_mappings.json` - 数据源映射
- `data_source_routes.json` - 数据源路由
- `data_sources.json` - 数据源
- `mock_data.json` - 模拟数据
- 等等

### Runtime 表 (`config/runtime/`)
- `runtime_modes.json` - 运行模式（也是核心表）
- `event_rules.json` - 事件规则
- `time_sources.json` - 时间源
- `trade_interfaces.json` - 交易接口
- 等等

### Pools 表 (`config/pools/`)
- `pool_types.json` - 池类型
- `sim_demo_pool.json` - 仿真演示池（死表）
- `sim_test_pool.json` - 仿真测试池（死表）
- `sim_test_pool_100.json` - 仿真测试池100（死表）
- `target_pool_100.json` - 目标池100

## Dead Tables (死表，已归档)

参见 `config/_archive/dead_tables_audit.md` 获取完整的死表审计报告。

以下 9 张表已识别为死表（0 引用），建议归档到 `config/_archive/`：
1. `architecture/capability_registry.json`
2. `data/tdx_system_indicators.json`
3. `pools/sim_demo_pool.json`
4. `pools/sim_test_pool.json`
5. `pools/sim_test_pool_100.json`
6. `runtime/pre_tick_pipeline.json`
7. `runtime/flow_mode_rules.json`
8. `ui/chart_config.json`
9. `ui/ui_state.json`
