# 股票池深度重构规划 v1.16

> 版本主题：详细设计：表Schema + 错误处理 + 层间依赖
> 设计原则：诚实不吹牛、正交拆分、三态逻辑、保守策略、错误隔离
> 目标：从概念架构转向详细设计，补全所有核心表的完整Schema，设计错误处理和异常恢复机制，明确层间依赖规则，澄清配置层内部分类

---

## v1.15 → v1.16 变更摘要表

**变更日期：** 2026-07-01

| # | 变更项 | v1.15 | v1.16 | 本质变化 |
|---|--------|------|-------|---------|
| 1 | **核心表字段定义** | 只有表名和用途说明，没有具体字段 | **所有核心表补全完整Schema（主键、字段名、类型、约束、说明）** | 从概念到详细设计：每张表的每个字段都有明确的类型和约束 |
| 2 | **错误处理和异常恢复** | 完全没有设计 | **完整的错误处理体系：错误分类、隔离策略、恢复机制** | 补全"脏活累活"：错误不是异常情况，是常态，必须有明确的处理策略 |
| 3 | **层间依赖规则** | 只有简单的四层架构图 | **明确的依赖规则 + 详细的依赖关系图 + 反向依赖禁止清单** | 从"有分层"到"有严格的依赖规则"：明确什么可以依赖什么，什么绝对不行 |
| 4 | **配置层内部分类** | 类型配置 + 实例配置（粗分） | **三大类：类型定义 + 实例定义 + 系统配置，每类职责/修改频率/维护者明确** | 配置层内部进一步正交拆分：谁改什么表，改的频率多高，清清楚楚 |
| 5 | **配置表清单更新** | 13张（类型6+实例5+时间2） | **15张（类型定义7 + 实例定义5 + 系统配置3）** | 分类更细：operator/combine/formula 单独归为算子公式类，trade_calendar 和 system_config 归为系统配置 |
| 6 | **运行时表更新** | 只有表名和用途，没有字段 | **所有运行时表补全字段结构定义** | 运行时表也有明确的结构，不是模糊的Dict |
| 7 | **功能-表操作对应表更新** | 按层分类，较粗 | **按错误处理维度补充：每个功能的错误场景和处理策略** | 功能表不仅说"读什么写什么"，还说"出错了怎么办" |

**一句话总结 v1.16 升级：** 落地——从概念架构到详细设计，每张表的每个字段都清清楚楚；健壮——错误不是例外，是常态，有完整的隔离和恢复机制；严谨——层间依赖有严格规则，配置内部分类清晰，谁改什么、怎么改、改了怎么恢复，明明白白。

---

## 一、核心表字段定义（完整Schema）

### 1.1 设计原则

所有表的Schema设计遵循以下原则：

1. **主键唯一**：每张表有明确的主键，全局唯一
2. **类型明确**：每个字段有明确的数据类型（string/integer/float/bool/array/object/timestamp）
3. **约束清晰**：必填/可选、默认值、取值范围、外键引用
4. **三态兼容**：支持 True/False/None 三态逻辑
5. **可追溯**：有创建时间、更新时间、版本号
6. **可扩展**：预留 ext 字段用于扩展，不破坏主结构

---

### 1.2 配置表：类型定义层（7张）

#### 1.2.1 node_type_table（节点类型定义表）

**用途：** 定义所有节点类型的身份、分类、结构属性。开发时定义，低修改频率。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `node_type_id` | string | **主键**，必填，唯一 | 节点类型唯一标识，如 "market_source"、"condition_node"、"state_pool" |
| `name` | string | 必填 | 类型名称（显示用），如 "市场数据源"、"条件选股"、"状态池" |
| `category` | string | 必填，枚举 | 节点分类：`source`（数据源）/ `filter`（过滤）/ `pool`（股票池）/ `sink`（丢弃）/ `auxiliary`（辅助）/ `alert`（告警） |
| `has_stocks` | bool | 必填，默认 false | 该类型节点是否持有股票列表（池类节点为true，其他为false） |
| `max_in_edges` | integer | 可选，默认 null | 最大入边数，null表示无限制；如源节点为0，状态池一般为多 |
| `max_out_edges` | integer | 可选，默认 null | 最大出边数，null表示无限制；如丢弃池为0 |
| `allowed_roles` | array[string] | 可选，默认 [] | 允许的池角色，如 ["primary", "backup", "discard"] |
| `is_container` | bool | 必填，默认 false | 是否是容器节点（可以包含子节点） |
| `default_params` | object | 可选，默认 {} | 该类型的默认参数值，实例可以覆盖 |
| `param_schema` | object | 可选，默认 {} | 参数的Schema定义，用于校验实例参数 |
| `created_at` | timestamp | 必填 | 创建时间 |
| `updated_at` | timestamp | 必填 | 更新时间 |
| `version` | string | 必填，默认 "1.0" | 版本号 |
| `ext` | object | 可选，默认 {} | 扩展字段，用于存放特定类型的额外属性 |

**示例数据：**
```json
{
  "node_type_id": "condition_node",
  "name": "条件选股节点",
  "category": "filter",
  "has_stocks": true,
  "max_in_edges": null,
  "max_out_edges": 1,
  "allowed_roles": ["filter"],
  "is_container": false,
  "default_params": {
    "period": "1d",
    "match_mode": "all"
  },
  "param_schema": {
    "period": { "type": "string", "required": true },
    "match_mode": { "type": "string", "enum": ["all", "any"] }
  }
}
```

---

#### 1.2.2 node_behavior_table（节点行为定义表）

**用途：** 定义节点类型的行为handler。开发时定义，低修改频率。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `node_type_id` | string | **主键**，必填，唯一，外键→node_type_table | 节点类型ID，与node_type_table一一对应 |
| `init_handler` | string | 可选，默认 null | 初始化handler函数名，节点创建时调用一次 |
| `in_edge_handler` | string | 可选，默认 null | 入边处理handler函数名，处理进入该节点的股票 |
| `out_edge_handler` | string | 可选，默认 null | 出边处理handler函数名，处理从该节点流出的股票 |
| `tick_handler` | string | 可选，默认 null | 每tick调用的handler函数名，用于定时任务 |
| `alert_handler` | string | 可选，默认 null | 告警处理handler函数名 |
| `validate_handler` | string | 可选，默认 null | 配置校验handler函数名，用于校验实例参数 |
| `cleanup_handler` | string | 可选，默认 null | 清理handler函数名，节点销毁时调用 |
| `error_handler` | string | 可选，默认 null | 错误处理handler函数名，节点执行出错时调用 |
| `created_at` | timestamp | 必填 | 创建时间 |
| `updated_at` | timestamp | 必填 | 更新时间 |
| `version` | string | 必填，默认 "1.0" | 版本号 |
| `ext` | object | 可选，默认 {} | 扩展字段 |

**示例数据：**
```json
{
  "node_type_id": "condition_node",
  "init_handler": "init_condition_node",
  "in_edge_handler": "in_edge_condition",
  "out_edge_handler": "out_edge_condition",
  "tick_handler": null,
  "alert_handler": null,
  "validate_handler": "validate_condition_node",
  "cleanup_handler": null,
  "error_handler": "default_node_error_handler"
}
```

---

#### 1.2.3 edge_type_table（边类型定义表）

**用途：** 定义所有边类型的身份、分类、结构属性。开发时定义，低修改频率。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `edge_type_id` | string | **主键**，必填，唯一 | 边类型唯一标识，如 "default_edge"、"condition_edge"、"time_driven_edge" |
| `name` | string | 必填 | 类型名称（显示用） |
| `category` | string | 必填，枚举 | 边分类：`flow`（流转）/ `condition`（条件）/ `time`（时间驱动）/ `alert`（告警） |
| `trigger_mode` | string | 必填，枚举 | 触发模式：`data_driven`（数据驱动）/ `time_driven`（时间驱动）/ `event_driven`（事件驱动） |
| `default_trigger_period` | string | 可选，默认 null | 默认触发周期，time_driven类型必填，如 "1m"、"1d" |
| `has_filter` | bool | 必填，默认 false | 是否支持filter配置（条件边为true） |
| `has_ttl` | bool | 必填，默认 false | 是否支持TTL（状态池的入边一般支持） |
| `flow_mode` | string | 可选，默认 "move" | 流转模式：`move`（移动）/ `copy`（复制）/ `cover`（覆盖）/ `force`（强制） |
| `default_params` | object | 可选，默认 {} | 该类型的默认参数值 |
| `param_schema` | object | 可选，默认 {} | 参数Schema定义 |
| `created_at` | timestamp | 必填 | 创建时间 |
| `updated_at` | timestamp | 必填 | 更新时间 |
| `version` | string | 必填，默认 "1.0" | 版本号 |
| `ext` | object | 可选，默认 {} | 扩展字段 |

**示例数据：**
```json
{
  "edge_type_id": "condition_edge",
  "name": "条件边",
  "category": "condition",
  "trigger_mode": "data_driven",
  "default_trigger_period": null,
  "has_filter": true,
  "has_ttl": false,
  "flow_mode": "move",
  "default_params": {
    "match_mode": "all"
  }
}
```

---

#### 1.2.4 edge_behavior_table（边行为定义表）

**用途：** 定义边类型的行为handler。开发时定义，低修改频率。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `edge_type_id` | string | **主键**，必填，唯一，外键→edge_type_table | 边类型ID，与edge_type_table一一对应 |
| `gate_handler` | string | 可选，默认 null | 门控handler函数名，判断边是否应该触发 |
| `filter_handler` | string | 可选，默认 null | 过滤handler函数名，筛选通过的股票 |
| `propagate_handler` | string | 必填 | 传播handler函数名，将股票传播到目标节点 |
| `validate_handler` | string | 可选，默认 null | 配置校验handler函数名 |
| `error_handler` | string | 可选，默认 null | 错误处理handler函数名 |
| `created_at` | timestamp | 必填 | 创建时间 |
| `updated_at` | timestamp | 必填 | 更新时间 |
| `version` | string | 必填，默认 "1.0" | 版本号 |
| `ext` | object | 可选，默认 {} | 扩展字段 |

**示例数据：**
```json
{
  "edge_type_id": "condition_edge",
  "gate_handler": "default_gate",
  "filter_handler": "configurable_filter",
  "propagate_handler": "default_propagate",
  "validate_handler": "validate_condition_edge",
  "error_handler": "default_edge_error_handler"
}
```

---

#### 1.2.5 ui_type_table（UI类型定义表）

**用途：** 定义节点/边类型的默认UI属性。开发时定义，低修改频率。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `type_id` | string | **主键**，必填，唯一 | UI类型ID，格式：`node:{node_type_id}` 或 `edge:{edge_type_id}` |
| `target_type` | string | 必填，枚举 | 目标类型：`node` / `edge` |
| `display_name` | string | 必填 | 默认显示名称 |
| `color` | string | 可选，默认 "#3b82f6" | 默认颜色（CSS颜色值） |
| `icon` | string | 可选，默认 null | 图标标识（前端图标库的图标名） |
| `default_width` | integer | 可选，默认 160 | 默认宽度（像素） |
| `default_height` | integer | 可选，默认 80 | 默认高度（像素） |
| `shape` | string | 可选，默认 "rect" | 形状：`rect`（矩形）/ `round`（圆角矩形）/ `circle`（圆形）/ `diamond`（菱形） |
| `layout_id` | string | 可选，外键→ui_layouts | 属性面板布局ID |
| `category_color` | string | 可选，默认 null | 分类颜色（按category统一颜色，覆盖color） |
| `created_at` | timestamp | 必填 | 创建时间 |
| `updated_at` | timestamp | 必填 | 更新时间 |
| `version` | string | 必填，默认 "1.0" | 版本号 |
| `ext` | object | 可选，默认 {} | 扩展字段 |

**示例数据：**
```json
{
  "type_id": "node:condition_node",
  "target_type": "node",
  "display_name": "条件选股",
  "color": "#10b981",
  "icon": "filter",
  "default_width": 180,
  "default_height": 100,
  "shape": "round",
  "layout_id": "condition_node_layout"
}
```

---

#### 1.2.6 formula_table（指标公式定义表）

**用途：** 定义所有可用的指标公式。开发/运营时定义，中修改频率。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `formula_id` | string | **主键**，必填，唯一 | 公式唯一标识，如 "MA"、"MACD"、"VOL" |
| `name` | string | 必填 | 公式名称（显示用） |
| `category` | string | 必填，枚举 | 分类：`trend`（趋势）/ `momentum`（动量）/ `volume`（成交量）/ `volatility`（波动率）/ `custom`（自定义） |
| `formula` | string | 必填 | 公式表达式（TDX公式语法） |
| `params` | array[object] | 可选，默认 [] | 参数定义列表，每个参数包含：name（参数名）、type（类型）、default（默认值）、min/max（范围） |
| `outputs` | array[object] | 可选，默认 [] | 输出线定义，每个输出包含：name（输出名）、color（颜色）、default_visible（默认是否显示） |
| `description` | string | 可选，默认 "" | 公式描述说明 |
| `is_builtin` | bool | 必填，默认 true | 是否是内置公式（用户不能删除） |
| `created_at` | timestamp | 必填 | 创建时间 |
| `updated_at` | timestamp | 必填 | 更新时间 |
| `version` | string | 必填，默认 "1.0" | 版本号 |
| `ext` | object | 可选，默认 {} | 扩展字段 |

**示例数据：**
```json
{
  "formula_id": "MA",
  "name": "移动平均线",
  "category": "trend",
  "formula": "MA(CLOSE, N)",
  "params": [
    { "name": "N", "type": "integer", "default": 5, "min": 1, "max": 1000 }
  ],
  "outputs": [
    { "name": "MA", "color": "#f59e0b", "default_visible": true }
  ],
  "description": "计算收盘价的N日简单移动平均",
  "is_builtin": true
}
```

---

#### 1.2.7 operator_table（算子与组合方式定义表）

**用途：** 定义所有比较算子和组合方式。开发时定义，低修改频率。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `operator_id` | string | **主键**，必填，唯一 | 算子唯一标识，如 "gt"、"lt"、"between"、"cross_up"、"and"、"or"、"top_n" |
| `name` | string | 必填 | 算子名称（显示用） |
| `category` | string | 必填，枚举 | 分类：`compare`（比较算子）/ `combine`（组合算子）/ `rank`（排名算子） |
| `handler` | string | 必填 | 处理函数名 |
| `param_schema` | object | 可选，默认 {} | 参数Schema定义 |
| `default_params` | object | 可选，默认 {} | 默认参数值 |
| `description` | string | 可选，默认 "" | 算子描述说明 |
| `supports_three_state` | bool | 必填，默认 true | 是否支持三态逻辑（True/False/None） |
| `is_builtin` | bool | 必填，默认 true | 是否是内置算子 |
| `created_at` | timestamp | 必填 | 创建时间 |
| `updated_at` | timestamp | 必填 | 更新时间 |
| `version` | string | 必填，默认 "1.0" | 版本号 |
| `ext` | object | 可选，默认 {} | 扩展字段 |

**示例数据：**
```json
{
  "operator_id": "gt",
  "name": "大于",
  "category": "compare",
  "handler": "op_greater_than",
  "param_schema": {
    "threshold": { "type": "float", "required": true }
  },
  "default_params": {
    "threshold": 0
  },
  "description": "判断指标值是否大于阈值",
  "supports_three_state": true,
  "is_builtin": true
}
```

---

### 1.3 配置表：实例定义层（5张）

#### 1.3.1 pool_table（股票池实例表）

**用途：** 存储股票池实例的基本信息和拓扑结构。用户设计时修改，高修改频率。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `pool_id` | string | **主键**，必填，唯一 | 股票池唯一标识 |
| `name` | string | 必填 | 股票池名称 |
| `description` | string | 可选，默认 "" | 股票池描述 |
| `pool_type` | string | 必填，枚举 | 池类型：`dzh`（大智慧风格）/ `tdx`（通达信风格）/ `custom`（自定义） |
| `node_ids` | array[string] | 必填，默认 [] | 节点ID列表，按拓扑排序 |
| `edge_ids` | array[string] | 必填，默认 [] | 边ID列表 |
| `entry_node_id` | string | 可选，外键→node_instance_table | 入口节点ID（股票初始进入的节点） |
| `exit_node_id` | string | 可选，外键→node_instance_table | 出口节点ID（最终输出的节点） |
| `status` | string | 必填，默认 "draft" | 状态：`draft`（草稿）/ `active`（运行中）/ `paused`（暂停）/ `archived`（归档） |
| `created_by` | string | 可选，默认 null | 创建者 |
| `created_at` | timestamp | 必填 | 创建时间 |
| `updated_at` | timestamp | 必填 | 更新时间 |
| `last_run_at` | timestamp | 可选，默认 null | 上次运行时间 |
| `version` | string | 必填，默认 "1.0" | 版本号 |
| `tags` | array[string] | 可选，默认 [] | 标签列表，用于分类筛选 |
| `ext` | object | 可选，默认 {} | 扩展字段 |

**示例数据：**
```json
{
  "pool_id": "pool_001",
  "name": "MA5金叉策略",
  "description": "基于MA5上穿MA10的短线选股策略",
  "pool_type": "custom",
  "node_ids": ["node_001", "node_002", "node_003"],
  "edge_ids": ["edge_001", "edge_002"],
  "entry_node_id": "node_001",
  "exit_node_id": "node_003",
  "status": "active",
  "tags": ["趋势", "短线"]
}
```

---

#### 1.3.2 node_instance_table（节点实例表）

**用途：** 存储具体节点实例的配置。用户设计时修改，高修改频率。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `node_id` | string | **主键**，必填，唯一 | 节点实例唯一标识 |
| `pool_id` | string | 必填，外键→pool_table | 所属股票池ID |
| `node_type_id` | string | 必填，外键→node_type_table | 节点类型ID（引用类型定义） |
| `name` | string | 可选，默认 null | 节点自定义名称（覆盖类型默认显示名） |
| `params` | object | 可选，默认 {} | 节点实例参数（覆盖类型默认参数） |
| `position_x` | float | 必填，默认 0 | X坐标（画布位置） |
| `position_y` | float | 必填，默认 0 | Y坐标（画布位置） |
| `width` | float | 可选，默认 null | 宽度（覆盖类型默认宽度） |
| `height` | float | 可选，默认 null | 高度（覆盖类型默认高度） |
| `color` | string | 可选，默认 null | 颜色（覆盖类型默认颜色） |
| `z_index` | integer | 可选，默认 0 | 层级（z轴顺序） |
| `locked` | bool | 必填，默认 false | 是否锁定（防止误操作） |
| `visible` | bool | 必填，默认 true | 是否可见 |
| `created_at` | timestamp | 必填 | 创建时间 |
| `updated_at` | timestamp | 必填 | 更新时间 |
| `version` | string | 必填，默认 "1.0" | 版本号 |
| `ext` | object | 可选，默认 {} | 扩展字段 |

**示例数据：**
```json
{
  "node_id": "node_002",
  "pool_id": "pool_001",
  "node_type_id": "condition_node",
  "name": "MA5金叉",
  "params": {
    "period": "1d",
    "match_mode": "all"
  },
  "position_x": 300,
  "position_y": 200,
  "width": 180,
  "height": 100,
  "color": null,
  "z_index": 1,
  "locked": false,
  "visible": true
}
```

---

#### 1.3.3 edge_instance_table（边实例表）

**用途：** 存储具体边实例的配置。用户设计时修改，高修改频率。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `edge_id` | string | **主键**，必填，唯一 | 边实例唯一标识 |
| `pool_id` | string | 必填，外键→pool_table | 所属股票池ID |
| `edge_type_id` | string | 必填，外键→edge_type_table | 边类型ID（引用类型定义） |
| `source_node_id` | string | 必填，外键→node_instance_table | 源节点ID |
| `target_node_id` | string | 必填，外键→node_instance_table | 目标节点ID |
| `name` | string | 可选，默认 null | 边自定义名称 |
| `params` | object | 可选，默认 {} | 边实例参数 |
| `filter_config` | object | 可选，默认 null | filter配置（条件边才有），包含 conditions（条件列表）和 combine（组合方式） |
| `trigger_period` | string | 可选，默认 null | 触发周期（时间驱动边才有，覆盖类型默认值） |
| `ttl_config` | object | 可选，默认 null | TTL配置，包含 enabled（是否启用）、ttl_sec（过期时间秒）、mode（模式：fixed/sliding） |
| `flow_mode` | string | 可选，默认 null | 流转模式（覆盖类型默认值） |
| `visible` | bool | 必填，默认 true | 是否可见 |
| `created_at` | timestamp | 必填 | 创建时间 |
| `updated_at` | timestamp | 必填 | 更新时间 |
| `version` | string | 必填，默认 "1.0" | 版本号 |
| `ext` | object | 可选，默认 {} | 扩展字段 |

**filter_config 结构：**
```json
{
  "conditions": [
    {
      "condition_id": "cond_1",
      "formula_id": "MA",
      "formula_params": { "N": 5 },
      "operator_id": "gt",
      "operator_params": { "threshold": 10 },
      "weight": 1.0
    }
  ],
  "combine": {
    "combine_id": "and",
    "params": {}
  }
}
```

**示例数据：**
```json
{
  "edge_id": "edge_001",
  "pool_id": "pool_001",
  "edge_type_id": "condition_edge",
  "source_node_id": "node_001",
  "target_node_id": "node_002",
  "name": "初选条件",
  "filter_config": {
    "conditions": [
      {
        "condition_id": "cond_1",
        "formula_id": "MA",
        "formula_params": { "N": 5 },
        "operator_id": "gt",
        "operator_params": { "threshold": 10 },
        "weight": 1.0
      }
    ],
    "combine": {
      "combine_id": "and",
      "params": {}
    }
  },
  "ttl_config": null,
  "flow_mode": "move"
}
```

---

#### 1.3.4 formula_instance_table（自定义公式实例表）

**用途：** 存储用户自定义的指标公式。用户设计时修改，中修改频率。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `formula_id` | string | **主键**，必填，唯一 | 自定义公式唯一标识 |
| `pool_id` | string | 可选，默认 null | 所属股票池ID（null表示全局可用） |
| `name` | string | 必填 | 公式名称 |
| `formula` | string | 必填 | 公式表达式 |
| `category` | string | 必填，默认 "custom" | 分类，用户自定义的都是 custom |
| `params` | array[object] | 可选，默认 [] | 参数定义列表 |
| `outputs` | array[object] | 可选，默认 [] | 输出线定义 |
| `description` | string | 可选，默认 "" | 公式描述 |
| `created_by` | string | 可选，默认 null | 创建者 |
| `created_at` | timestamp | 必填 | 创建时间 |
| `updated_at` | timestamp | 必填 | 更新时间 |
| `version` | string | 必填，默认 "1.0" | 版本号 |
| `ext` | object | 可选，默认 {} | 扩展字段 |

**示例数据：**
```json
{
  "formula_id": "custom_ma_cross",
  "pool_id": null,
  "name": "自定义均线金叉",
  "formula": "CROSS(MA(CLOSE, N1), MA(CLOSE, N2))",
  "params": [
    { "name": "N1", "type": "integer", "default": 5, "min": 1, "max": 100 },
    { "name": "N2", "type": "integer", "default": 10, "min": 1, "max": 100 }
  ],
  "description": "用户自定义的均线金叉指标"
}
```

---

#### 1.3.5 ui_instance_table（UI实例表）

**用途：** 存储实例的UI自定义配置。用户设计时修改，高修改频率。

**注意：** 此表的大部分字段已合并到 node_instance_table 和 edge_instance_table 中。此表主要存储复杂的UI状态，如折叠状态、选中状态等运行时UI状态。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `ui_id` | string | **主键**，必填，唯一 | UI实例唯一标识，格式：`node:{node_id}` 或 `edge:{edge_id}` 或 `pool:{pool_id}` |
| `pool_id` | string | 可选，外键→pool_table | 所属股票池ID |
| `target_type` | string | 必填，枚举 | 目标类型：`node` / `edge` / `pool` |
| `target_id` | string | 必填 | 目标ID（node_id / edge_id / pool_id） |
| `collapsed` | bool | 可选，默认 false | 是否折叠（容器节点用） |
| `expanded_sections` | array[string] | 可选，默认 [] | 展开的面板分区ID列表（属性面板用） |
| `selected` | bool | 可选，默认 false | 是否选中（运行时UI状态） |
| `highlighted` | bool | 可选，默认 false | 是否高亮（运行时UI状态） |
| `view_state` | object | 可选，默认 {} | 其他视图状态（如滚动位置、缩放比例等） |
| `created_at` | timestamp | 必填 | 创建时间 |
| `updated_at` | timestamp | 必填 | 更新时间 |
| `ext` | object | 可选，默认 {} | 扩展字段 |

---

### 1.4 配置表：系统配置层（3张）

#### 1.4.1 period_table（周期定义表）

**用途：** 定义所有可用的时间周期。开发时定义，低修改频率。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `period_id` | string | **主键**，必填，唯一 | 周期唯一标识，如 "1m"、"5m"、"15m"、"1h"、"1d"、"1w"、"1M" |
| `name` | string | 必填 | 周期名称（显示用） |
| `category` | string | 必填，枚举 | 分类：`tick`（tick级）/ `intraday`（日内分钟）/ `daily`（日线）/ `weekly`（周线）/ `monthly`（月线） |
| `seconds` | integer | 必填 | 周期长度（秒）。日K线及以上为交易日数量 |
| `is_tick` | bool | 必填，默认 false | 是否是tick级周期 |
| `confirm_mode` | string | 必填，默认 "time_boundary" | 确认方式：`time_boundary`（时间边界）/ `next_bar`（下一根K线出现）/ `volume`（成交量确认） |
| `align_to` | string | 可选，默认 null | 对齐方式，如 "market_open"（开盘对齐） |
| `data_source_period` | string | 可选，默认 null | 数据源中的周期标识（用于映射不同数据源的周期命名） |
| `description` | string | 可选，默认 "" | 周期描述 |
| `created_at` | timestamp | 必填 | 创建时间 |
| `updated_at` | timestamp | 必填 | 更新时间 |
| `version` | string | 必填，默认 "1.0" | 版本号 |
| `ext` | object | 可选，默认 {} | 扩展字段 |

**示例数据：**
```json
{
  "period_id": "1d",
  "name": "日线",
  "category": "daily",
  "seconds": 86400,
  "is_tick": false,
  "confirm_mode": "next_bar",
  "align_to": "market_open",
  "description": "日K线周期，每日一根"
}
```

---

#### 1.4.2 trade_calendar_table（交易日历表）

**用途：** 存储交易日历配置。运维时修改，低修改频率。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `calendar_id` | string | **主键**，必填，唯一 | 日历唯一标识，如 "SH"（上海）、"SZ"（深圳）、"HK"（香港） |
| `name` | string | 必填 | 日历名称 |
| `timezone` | string | 必填，默认 "Asia/Shanghai" | 时区 |
| `trading_days` | array[string] | 必填，默认 [] | 交易日列表（YYYY-MM-DD格式） |
| `holidays` | array[string] | 可选，默认 [] | 节假日列表（用于排除） |
| `extra_trading_days` | array[string] | 可选，默认 [] | 额外交易日（周末但交易的情况） |
| `morning_session_start` | string | 必填，默认 "09:30" | 早盘开始时间（HH:MM格式） |
| `morning_session_end` | string | 必填，默认 "11:30" | 早盘结束时间 |
| `afternoon_session_start` | string | 必填，默认 "13:00" | 午盘开始时间 |
| `afternoon_session_end` | string | 必填，默认 "15:00" | 午盘结束时间 |
| `has_lunch_break` | bool | 必填，默认 true | 是否有午间休市 |
| `weekend_days` | array[integer] | 可选，默认 [5, 6] | 周末星期几（0=周一，6=周日） |
| `created_at` | timestamp | 必填 | 创建时间 |
| `updated_at` | timestamp | 必填 | 更新时间 |
| `version` | string | 必填，默认 "1.0" | 版本号 |
| `ext` | object | 可选，默认 {} | 扩展字段 |

**示例数据：**
```json
{
  "calendar_id": "SH",
  "name": "上交所交易日历",
  "timezone": "Asia/Shanghai",
  "trading_days": [],
  "holidays": ["2026-01-01", "2026-01-28", "2026-01-29"],
  "extra_trading_days": [],
  "morning_session_start": "09:30",
  "morning_session_end": "11:30",
  "afternoon_session_start": "13:00",
  "afternoon_session_end": "15:00",
  "has_lunch_break": true,
  "weekend_days": [5, 6]
}
```

---

#### 1.4.3 system_config_table（系统配置表）

**用途：** 存储系统级全局配置。运维/开发时修改，低修改频率。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `config_key` | string | **主键**，必填，唯一 | 配置项键名 |
| `config_value` | any | 必填 | 配置项值 |
| `value_type` | string | 必填，枚举 | 值类型：`string` / `integer` / `float` / `bool` / `object` / `array` |
| `category` | string | 必填，默认 "general" | 配置分类：`general`（通用）/ `performance`（性能）/ `error_handling`（错误处理）/ `security`（安全）/ `logging`（日志） |
| `description` | string | 可选，默认 "" | 配置描述 |
| `default_value` | any | 可选，默认 null | 默认值 |
| `is_readonly` | bool | 必填，默认 false | 是否只读（用户不能修改） |
| `validation` | object | 可选，默认 null | 校验规则，如 { "min": 0, "max": 100 } 或 { "pattern": "^[a-z]+$" } |
| `created_at` | timestamp | 必填 | 创建时间 |
| `updated_at` | timestamp | 必填 | 更新时间 |
| `updated_by` | string | 可选，默认 null | 最后修改者 |
| `version` | string | 必填，默认 "1.0" | 版本号 |
| `ext` | object | 可选，默认 {} | 扩展字段 |

**常用配置项示例：**

| config_key | value_type | 默认值 | 说明 |
|------------|-----------|--------|------|
| `tick_interval_sec` | float | 1.0 | tick轮询间隔（秒） |
| `data_timeout_sec` | integer | 30 | 数据超时时间（秒） |
| `max_calc_time_ms` | integer | 1000 | 单次计算最大耗时（毫秒），超过则告警 |
| `max_stocks_per_pool` | integer | 5000 | 单股票池最大股票数 |
| `error_isolation_level` | string | "stock" | 错误隔离级别：`stock`（单股票隔离）/ `edge`（单条边隔离）/ `node`（单个节点隔离） |
| `enable_hot_reload` | bool | true | 是否启用配置热加载 |
| `hot_reload_interval_sec` | integer | 2 | 热加载检测间隔（秒） |
| `log_level` | string | "INFO" | 日志级别：DEBUG/INFO/WARNING/ERROR |
| `enable_perf_monitor` | bool | true | 是否启用性能监控 |
| `perf_sample_interval_sec` | integer | 60 | 性能采样间隔（秒） |

---

### 1.5 运行时表（完整Schema）

#### 1.5.1 latest_tick（最新Tick数据表）

**用途：** 存储每只股票的最新tick数据。**唯一真相源**。运行时动态更新。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `code` | string | **主键**，唯一 | 股票代码 |
| `price` | float | 必填 | 最新价 |
| `open` | float | 可选 | 今日开盘价 |
| `high` | float | 可选 | 今日最高价 |
| `low` | float | 可选 | 今日最低价 |
| `volume` | integer | 可选 | 今日成交量（股） |
| `amount` | float | 可选 | 今日成交额（元） |
| `prev_close` | float | 可选 | 昨收价 |
| `timestamp` | timestamp | 必填 | 数据时间戳（tick_data_ts） |
| `data_source` | string | 可选 | 数据源标识 |
| `raw_data` | object | 可选 | 原始数据（保留所有字段） |

**类型：** `Dict[code → tick_dict]`

**生命周期：** 运行时持续更新，重启后从数据源重新获取

---

#### 1.5.2 stock_status_table（股票状态表）

**用途：** 存储每只股票的运行状态。运行时动态更新。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `code` | string | **主键**，唯一 | 股票代码 |
| `status` | string | 必填，枚举 | 状态：`normal`（正常）/ `suspended`（停牌）/ `insufficient_data`（数据不足）/ `error`（异常） |
| `status_reason` | string | 可选，默认 null | 状态原因描述 |
| `last_data_ts` | timestamp | 可选，默认 null | 最后一次收到数据的时间戳 |
| `last_error` | string | 可选，默认 null | 最后一次错误信息 |
| `last_error_ts` | timestamp | 可选，默认 null | 最后一次错误时间 |
| `error_count` | integer | 必填，默认 0 | 连续错误次数 |
| `suspended_reason` | string | 可选，默认 null | 停牌原因 |
| `data_lag_sec` | float | 可选，默认 null | 数据延迟（秒） |
| `updated_at` | timestamp | 必填 | 更新时间 |

**类型：** `Dict[code → status_dict]`

**生命周期：** 运行时持续更新，重启后重新检测

---

#### 1.5.3 node_stocks（节点股票列表）

**用途：** 存储每个节点当前持有的股票列表。运行时动态更新。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `node_id` | string | **主键**，唯一 | 节点ID |
| `stocks` | array[object] | 必填，默认 [] | 股票列表，每个元素包含 code（股票代码）、in_pool_ts（入池时间戳，系统时间）、source_edge_id（来源边ID）、entry_price（入池价） |

**stock 元素结构：**
```json
{
  "code": "000001.SZ",
  "in_pool_ts": 1751356800.0,
  "source_edge_id": "edge_001",
  "entry_price": 12.34
}
```

**类型：** `Dict[node_id → List[stock_dict]]`

**生命周期：** 运行时持续更新，重启后从初始状态重新计算

---

#### 1.5.4 ttl_expiry_queue（TTL过期队列）

**用途：** TTL过期最小堆，按过期时间排序。运行时动态更新。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `expire_ts` | float | **主键**（堆排序键） | 过期时间戳（系统时间，Unix时间戳秒） |
| `node_id` | string | 必填 | 节点ID |
| `code` | string | 必填 | 股票代码 |
| `edge_id` | string | 必填 | 来源边ID（哪个边设置的TTL） |
| `entry_ts` | float | 必填 | 入池时间戳 |
| `ttl_sec` | float | 必填 | TTL时长（秒） |

**类型：** `Heap[Tuple[expire_ts, node_id, code]]` 或 `List[ttl_item]`

**生命周期：** 运行时持续更新，重启后清空

---

#### 1.5.5 dirty_stocks（脏股票集合）

**用途：** 本tick数据更新了的股票集合。**全局水位线**。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `code` | string | 集合元素，唯一 | 股票代码 |

**类型：** `Set[code]`

**生命周期：** 每tick开始时清空，数据更新时填充，tick结束时清空

---

#### 1.5.6 node_changes（节点变化三集合）

**用途：** 每个节点的股票变化情况。增量计算的基础。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `node_id` | string | **主键**，唯一 | 节点ID |
| `entered` | Set[string] | 必填，默认 set() | 新进入的股票集合 |
| `exited` | Set[string] | 必填，默认 set() | 离开的股票集合 |
| `updated` | Set[string] | 必填，默认 set() | 还在池中但数据更新了的股票集合 |

**类型：** `Dict[node_id → { entered, exited, updated }]`

**生命周期：** 每tick开始时计算，tick结束时消费并清空

---

#### 1.5.7 edge_compare_results（三态比较结果表）

**用途：** 每条边的filter比较结果。三态逻辑：True/False/None。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `edge_id` | string | **主键**，唯一 | 边ID |
| `results` | Dict[code → bool/None] | 必填 | 每只股票的比较结果：True=通过，False=不通过，None=不确定（数据不足/停牌/异常） |

**类型：** `Dict[edge_id → Dict[code → True/False/None]]`

**生命周期：** 每tick增量更新，数据变化的股票重新计算

---

#### 1.5.8 edge_filter_results（filter最终结果表）

**用途：** 每条边的filter最终结果（通过的股票）。排名型用有序列表，独立型用Set。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `edge_id` | string | **主键**，唯一 | 边ID |
| `result_type` | string | 必填，枚举 | 结果类型：`set`（集合，无序）/ `list`（有序列表，带排名） |
| `passed_stocks` | Set[string] 或 List[object] | 必填 | 通过的股票。set类型：股票代码集合；list类型：[{code, rank, score}, ...] |

**list 元素结构：**
```json
{
  "code": "000001.SZ",
  "rank": 1,
  "score": 0.95
}
```

**类型：** `Dict[edge_id → filter_result_dict]`

**生命周期：** 每tick重新计算（从edge_compare_results聚合而来）

---

#### 1.5.9 period_data（周期K线数据表）

**用途：** 各周期的K线数据。运行时动态更新。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `period` | string | **主键**，唯一 | 周期ID（如 "1d"、"1h"） |
| `completed_bars` | Dict[code → List[bar_dict]] | 必填 | 每只股票的已完成K线列表（按时间倒序） |
| `current_bar` | Dict[code → bar_dict] | 必填 | 每只股票的当前未完成K线 |

**bar_dict 结构：**
```json
{
  "timestamp": 1751356800.0,
  "open": 12.34,
  "high": 12.50,
  "low": 12.20,
  "close": 12.45,
  "volume": 1000000,
  "amount": 12345000.0
}
```

**类型：** `Dict[period → { completed_bars, current_bar }]`

**生命周期：** 运行时持续更新，重启后从数据源重新加载历史数据

---

#### 1.5.10 period_confirmed_events（周期确认事件队列）

**用途：** 周期确认事件队列。数据时间驱动。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `period` | string | 必填 | 周期ID |
| `confirmed_stocks` | Set[string] | 必填 | 哪些股票的该周期确认了 |
| `confirmed_time` | timestamp | 必填 | 确认时间（数据时间） |

**类型：** `Queue[event_dict]`

**生命周期：** 每tick开始时检测周期确认，事件在本tick内消费

---

#### 1.5.11 error_log_table（错误日志表）

**用途：** 记录运行时错误。用于错误追踪和统计。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `error_id` | string | **主键**，唯一 | 错误唯一标识（UUID） |
| `error_type` | string | 必填，枚举 | 错误类型：`data_source`（数据源错误）/ `formula`（公式计算错误）/ `config`（配置错误）/ `propagation`（传播错误）/ `system`（系统错误） |
| `severity` | string | 必填，枚举 | 严重程度：`info` / `warning` / `error` / `critical` |
| `message` | string | 必填 | 错误消息 |
| `stack_trace` | string | 可选，默认 null | 堆栈信息（如果有） |
| `context` | object | 可选，默认 {} | 上下文信息，如 { "pool_id": "...", "node_id": "...", "code": "..." } |
| `occurred_at` | timestamp | 必填 | 发生时间（系统时间） |
| `recoverable` | bool | 必填，默认 true | 是否可恢复 |
| `recovered` | bool | 必填，默认 false | 是否已恢复 |
| `recovered_at` | timestamp | 可选，默认 null | 恢复时间 |
| `retry_count` | integer | 必填，默认 0 | 重试次数 |

**类型：** `List[error_dict]` 或环形缓冲区

**生命周期：** 运行时持续追加，按配置的保留策略清理

---

#### 1.5.12 perf_metrics_table（性能指标表）

**用途：** 存储性能监控指标。运行时持续采样。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `metric_id` | string | **主键**，唯一 | 指标ID |
| `metric_name` | string | 必填 | 指标名称，如 "tick_total_ms"、"calc_ms"、"poll_ms" |
| `category` | string | 必填，默认 "general" | 指标分类 |
| `current_value` | float | 必填 | 当前值 |
| `avg_value` | float | 可选，默认 null | 平均值 |
| `max_value` | float | 可选，默认 null | 最大值 |
| `min_value` | float | 可选，默认 null | 最小值 |
| `p95_value` | float | 可选，默认 null | 95分位值 |
| `p99_value` | float | 可选，默认 null | 99分位值 |
| `sample_count` | integer | 必填，默认 0 | 采样次数 |
| `last_sample_at` | timestamp | 必填 | 最后采样时间 |

**类型：** `Dict[metric_id → metric_dict]`

**生命周期：** 运行时持续采样，重启后重置

---

### 1.6 配置表汇总（15张）

| 分类 | 表数 | 表名 | 修改频率 | 谁来改 |
|------|------|------|---------|-------|
| **类型定义层** | **7张** | | | |
| 节点类型 | 1 | node_type_table | 低（开发时） | 开发人员 |
| 节点行为 | 1 | node_behavior_table | 低（开发时） | 开发人员 |
| 边类型 | 1 | edge_type_table | 低（开发时） | 开发人员 |
| 边行为 | 1 | edge_behavior_table | 低（开发时） | 开发人员 |
| UI类型 | 1 | ui_type_table | 低（开发时） | 开发/设计人员 |
| 指标公式 | 1 | formula_table | 中（开发/运营） | 开发/运营人员 |
| 算子组合 | 1 | operator_table | 低（开发时） | 开发人员 |
| **实例定义层** | **5张** | | | |
| 股票池实例 | 1 | pool_table | 高（用户设计时） | 用户/运营 |
| 节点实例 | 1 | node_instance_table | 高（用户设计时） | 用户/运营 |
| 边实例 | 1 | edge_instance_table | 高（用户设计时） | 用户/运营 |
| 自定义公式 | 1 | formula_instance_table | 中（用户设计时） | 用户/运营 |
| UI实例状态 | 1 | ui_instance_table | 高（用户设计时） | 用户 |
| **系统配置层** | **3张** | | | |
| 周期定义 | 1 | period_table | 低（开发时） | 开发人员 |
| 交易日历 | 1 | trade_calendar_table | 低（运维时） | 运维人员 |
| 系统配置 | 1 | system_config_table | 低（运维/开发） | 运维/开发人员 |
| **合计** | **15张** | | | |

---

### 1.7 运行时表汇总（12张）

| 类别 | 表数 | 表名 | 更新频率 | 生命周期 |
|------|------|------|---------|---------|
| **核心状态表** | **8张** | | | |
| 最新Tick数据 | 1 | latest_tick | 每tick | 运行时持续 |
| 股票状态 | 1 | stock_status_table | 每tick | 运行时持续 |
| 节点股票 | 1 | node_stocks | 每tick | 运行时持续 |
| TTL过期队列 | 1 | ttl_expiry_queue | 每tick | 运行时持续 |
| 脏股票集合 | 1 | dirty_stocks | 每tick | tick内有效 |
| 节点变化 | 1 | node_changes | 每tick | tick内有效 |
| 三态比较结果 | 1 | edge_compare_results | 每tick | 运行时持续 |
| Filter最终结果 | 1 | edge_filter_results | 每tick | tick内有效 |
| **时间相关表** | **2张** | | | |
| 周期K线数据 | 1 | period_data | 每tick | 运行时持续 |
| 周期确认事件 | 1 | period_confirmed_events | 每tick | tick内有效 |
| **运维相关表** | **2张** | | | |
| 错误日志 | 1 | error_log_table | 事件驱动 | 按保留策略清理 |
| 性能指标 | 1 | perf_metrics_table | 定时采样 | 运行时持续 |
| **合计** | **12张** | | | |

---

## 二、错误处理和异常恢复设计

### 2.1 设计原则

1. **错误是常态，不是异常**：不要假设一切正常，要假设会出错
2. **错误隔离**：局部错误不扩散，单只股票/单条边/单个节点出错不影响整体
3. **优雅降级**：出错了也要尽可能提供服务，而不是直接崩溃
4. **可观测**：所有错误都要记录，可追踪、可统计
5. **可恢复**：尽可能自动恢复，不能自动恢复的要明确告警
6. **三态逻辑**：不确定的就是 None，不要硬猜 True 或 False

---

### 2.2 错误分类

#### 2.2.1 按错误类型分类

| 错误类型 | 说明 | 严重程度 | 可恢复性 | 示例 |
|----------|------|---------|---------|------|
| **数据源错误** | 数据获取失败、断开、超时 | warning ~ error | 大部分可恢复 | 网络断开、数据源限流、数据格式错误 |
| **公式计算错误** | 指标计算失败 | warning | 部分可恢复 | 除以零、数据不足、参数错误、公式语法错误 |
| **配置错误** | 配置无效、引用不存在、格式错误 | error | 需人工修复 | 引用不存在的节点类型、无效的公式ID、参数校验失败 |
| **传播错误** | 股票传播失败 | warning ~ error | 大部分可恢复 | 目标节点不存在、目标节点已满、流转模式不支持 |
| **系统错误** | 内存溢出、性能问题、未知异常 | error ~ critical | 部分可恢复 | OOM、计算超时、死锁 |

#### 2.2.2 按严重程度分类

| 严重程度 | 说明 | 处理策略 | 告警级别 |
|----------|------|---------|---------|
| `info` | 提示性信息，不影响功能 | 记录日志 | 不告警 |
| `warning` | 警告，功能降级可用 | 记录 + 自动恢复尝试 | 低优先级告警 |
| `error` | 错误，部分功能不可用 | 记录 + 隔离 + 重试 | 中优先级告警 |
| `critical` | 严重错误，系统可能不可用 | 记录 + 熔断 + 人工介入 | 高优先级告警 |

---

### 2.3 错误隔离策略

#### 2.3.1 三级隔离机制

```
┌─────────────────────────────────────────────────────────┐
│                    股票池整体（最高级别）                  │
│  ┌───────────────────────────────────────────────────┐  │
│  │              节点级隔离（第二级）                    │  │
│  │  ┌─────────────────────────────────────────────┐  │  │
│  │  │           边级隔离（第一级）                  │  │  │
│  │  │  ┌───────────────────────────────────────┐  │  │  │
│  │  │  │       股票级隔离（第零级）              │  │  │  │
│  │  │  │  单只股票出错 → 只影响这只股票          │  │  │  │
│  │  │  │  其他股票正常运行                      │  │  │  │
│  │  │  └───────────────────────────────────────┘  │  │  │
│  │  │  单条边出错 → 只影响这条边的传播              │  │  │
│  │  │  其他边正常运行                              │  │  │
│  │  └─────────────────────────────────────────────┘  │  │
│  │  单个节点出错 → 只影响这个节点的计算                │  │
│  │  其他节点正常运行                                  │  │
│  └───────────────────────────────────────────────────┘  │
│  整个股票池 → 熔断保护，避免雪崩                        │
└─────────────────────────────────────────────────────────┘
```

#### 2.3.2 股票级隔离（最细粒度）

**原则：** 单只股票的错误不影响其他股票。

**适用场景：**
- 某只股票的数据缺失/异常
- 某只股票的公式计算失败
- 某只股票的状态异常（停牌、退市等）

**处理策略：**
1. 该股票的比较结果设为 `None`（不确定）
2. 该股票不参与传播（保守策略：不确定的不入池）
3. 记录错误日志，标记该股票的错误状态
4. 不影响其他股票的计算

**代码模式：**
```python
for code in stock_codes:
    try:
        result = calculate_for_stock(code, ...)
        results[code] = result
    except Exception as e:
        logger.warning(f"计算股票 {code} 失败: {e}")
        results[code] = None  # 三态：不确定
        stock_status_table[code]['status'] = 'error'
        stock_status_table[code]['last_error'] = str(e)
        stock_status_table[code]['error_count'] += 1
```

#### 2.3.3 边级隔离

**原则：** 单条边的错误不影响其他边，也不影响源节点和目标节点的其他边。

**适用场景：**
- 某条边的filter配置错误
- 某条边的传播逻辑出错
- 某条边引用了不存在的公式/算子

**处理策略：**
1. 该边的filter结果设为全 `None` 或空集合
2. 该边不传播股票（相当于这条边"断开"了）
3. 记录错误日志，标记该边的错误状态
4. 源节点和目标节点的其他边正常运行
5. 如果是配置错误，热加载修复后自动恢复

**代码模式：**
```python
for eid, edge_inst in edge_instances.items():
    try:
        result = execute_edge(eid, edge_inst, ...)
        edge_filter_results[eid] = result
    except Exception as e:
        logger.error(f"边 {eid} 执行失败: {e}")
        # 这条边的结果设为空（保守策略）
        edge_filter_results[eid] = set()
        # 记录错误
        record_error('propagation', 'error', str(e), {'edge_id': eid})
```

#### 2.3.4 节点级隔离

**原则：** 单个节点的错误不影响整个股票池，其他节点正常运行。

**适用场景：**
- 某个节点的handler执行出错
- 某个节点的配置严重错误
- 某个节点的计算超时

**处理策略：**
1. 该节点的股票列表保持不变（不更新）
2. 该节点的出边暂停传播（避免传播错误状态）
3. 记录错误日志，标记该节点的错误状态
4. 其他节点正常计算
5. 如果错误持续，触发告警，通知人工介入

**代码模式：**
```python
for nid in topo_order:
    if not is_node_dirty(nid, node_changes):
        continue
    try:
        process_node(nid, ...)
    except Exception as e:
        logger.error(f"节点 {nid} 处理失败: {e}")
        # 节点状态保持不变，不传播
        node_changes[nid]['entered'].clear()
        node_changes[nid]['exited'].clear()
        node_changes[nid]['updated'].clear()
        # 记录错误
        record_error('system', 'error', str(e), {'node_id': nid})
```

---

### 2.4 具体错误场景和处理策略

#### 2.4.1 数据源断开/重连

**场景：** 数据源网络断开、超时、限流、服务不可用。

**错误检测：**
- 轮询超时（`sys_ts - last_poll_ts > timeout_threshold`）
- 数据源返回错误状态码
- 数据格式解析失败

**处理策略：**

| 阶段 | 行为 | 说明 |
|------|------|------|
| **第一次超时** | 记录 warning 日志，继续尝试 | 可能是临时网络波动 |
| **连续3次超时** | 记录 error 日志，触发数据源降级 | 切换到备用数据源（如果有） |
| **连续10次超时** | 记录 critical 日志，触发告警 | 通知运维人员 |
| **重连成功** | 自动恢复，记录恢复日志 | 重新加载历史数据，补算缺失的K线 |

**重连后的恢复：**
1. 检测到数据源恢复后，先获取中断期间的历史数据
2. 按时间顺序重放历史数据，确保K线连续性
3. 重算中断期间可能错过的周期确认事件
4. 更新所有相关的运行时状态
5. 记录恢复事件，重置错误计数

**降级策略：**
- 主数据源 → 备用数据源1 → 备用数据源2 → ... → 本地缓存
- 每级降级都有明确的切换条件和恢复条件
- 降级期间数据质量可能下降（如延迟增加、精度降低），但服务不中断

---

#### 2.4.2 公式计算错误

**场景：** 除以零、数据不足、参数错误、公式语法错误。

**错误分类和处理：**

| 错误类型 | 原因 | 处理策略 | 严重程度 |
|----------|------|---------|---------|
| **数据不足** | 历史K线数量不够、股票刚上市 | 结果设为 None，等待数据充足 | warning |
| **除以零** | 分母为零（如涨跌幅计算中昨收为0） | 结果设为 None，记录日志 | warning |
| **参数错误** | 参数超出范围、类型不对 | 结果设为 None，记录日志，告警通知配置错误 | error |
| **公式语法错误** | 公式表达式语法不对 | 结果设为 None，记录日志，告警通知配置错误 | error |
| **计算超时** | 公式太复杂，计算时间太长 | 中断计算，结果设为 None，记录日志 | error |

**三态逻辑的应用：**
- 所有公式计算错误都导致结果为 `None`（不确定）
- `None` 在比较层传播：只要有一个条件是 None，组合结果就可能是 None
- `None` 在传播层的处理：保守策略，None 的股票不入池

---

#### 2.4.3 节点配置错误

**场景：** 引用不存在的节点类型、无效的参数、缺少必填字段。

**错误检测时机：**
1. **加载时校验**：股票池加载时，一次性校验所有配置
2. **运行时校验**：热加载后，校验新配置
3. **执行前校验**：节点执行前，校验参数

**处理策略：**

| 错误严重度 | 处理策略 | 示例 |
|-----------|---------|------|
| **轻微** | 使用默认值，记录 warning | 可选参数缺失，用默认值代替 |
| **中等** | 该节点/边暂停运行，记录 error，告警 | 必填参数缺失、引用不存在的类型 |
| **严重** | 整个股票池暂停加载，记录 critical，告警 | 拓扑结构错误、循环依赖 |

**配置校验的三个层次：**
1. **语法校验**：JSON格式是否正确、字段类型是否对
2. **逻辑校验**：引用是否存在、参数范围是否合法、拓扑是否有环
3. **业务规则校验**：业务规则是否满足（如状态池不能没有入边）

---

#### 2.4.4 传播错误

**场景：** 目标节点不存在、目标节点已满、流转模式不支持。

**处理策略：**

| 错误类型 | 处理策略 |
|----------|---------|
| 目标节点不存在 | 跳过传播，记录 error 日志，告警 |
| 目标节点已满（有容量限制） | 按优先级淘汰或拒绝入池，记录 warning |
| 流转模式不支持 | 降级为默认模式（move），记录 warning |
| 股票已在目标节点 | 按流转模式处理（覆盖/忽略/报错），通常是更新状态 |

---

#### 2.4.5 内存溢出、性能问题

**场景：** 股票太多、计算太复杂、内存泄漏。

**检测指标：**
- 内存使用率超过阈值（如 80%）
- 单次计算耗时超过阈值（如 1000ms）
- tick 处理延迟越来越大（队列堆积）

**处理策略：**

| 严重程度 | 处理策略 |
|---------|---------|
| **预警（80%）** | 记录 warning，触发GC，减少非必要计算 |
| **告警（90%）** | 记录 error，启动降级策略：<br>1. 降低计算频率<br>2. 减少历史K线缓存数量<br>3. 暂停非核心功能（如PK排名、分析角度） |
| **危险（95%）** | 记录 critical，触发熔断：<br>1. 暂停新股票入池<br>2. 只保留核心状态池<br>3. 清理历史数据<br>4. 通知人工介入 |

**性能保护机制：**
1. **超时中断**：单只股票/单个节点/单次tick的计算有超时限制
2. **批量限流**：控制每次处理的股票数量，避免一次性处理太多
3. **缓存策略**：合理的缓存过期策略，避免内存无限增长
4. **懒加载**：非核心数据按需加载，不预加载所有数据

---

### 2.5 异常恢复机制

#### 2.5.1 数据源重连后的恢复

**恢复流程：**

```
数据源断开
    ↓
检测到超时 → 记录日志 → 尝试重连
    ↓
重连失败 → 等待 → 重试（指数退避）
    ↓
重连成功
    ↓
┌─────────────────────────────┐
│  1. 获取中断期间的历史数据    │
│  2. 按时间顺序重放数据        │
│  3. 补算缺失的K线             │
│  4. 重算周期确认事件          │
│  5. 更新所有运行时状态        │
│  6. 触发一次全量重算          │
└─────────────────────────────┘
    ↓
恢复完成 → 记录恢复日志 → 重置错误计数
```

**关键设计点：**
- **数据补全**：重连后要补全中断期间的数据，不能有缺口
- **状态一致性**：补算后确保所有运行时表状态一致
- **幂等性**：重复计算不会导致状态错误
- **重放顺序**：按时间顺序重放，不能乱序

---

#### 2.5.2 公式错误修复后的重算

**场景：** 用户修改了公式配置，热加载后需要重新计算。

**恢复流程：**
1. 检测到公式配置变更（热加载）
2. 校验新公式的语法和参数
3. 校验通过后，使相关缓存失效
4. 标记所有相关股票为"脏"
5. 下一个tick自动重新计算
6. 记录重算事件

**影响范围控制：**
- 只重算受影响的边和节点
- 不受影响的部分正常运行，不中断
- 重算过程中旧结果仍然可用（直到新结果计算完成）

---

#### 2.5.3 配置修改后的热更新

**热更新流程：**

```
配置文件变更
    ↓
检测到变更（文件监控）
    ↓
读取新配置
    ↓
┌─────────────────────────────┐
│  三级校验：                    │
│  1. 语法校验（JSON格式）       │
│  2. 逻辑校验（引用/范围）      │
│  3. 业务规则校验               │
└─────────────────────────────┘
    ↓
校验失败 → 保留旧配置 → 记录错误 → 告警
    ↓
校验通过
    ↓
┌─────────────────────────────┐
│  原子替换：                    │
│  1. 备份旧配置                 │
│  2. 替换为新配置               │
│  3. 使相关缓存失效             │
│  4. 标记相关部分为脏           │
└─────────────────────────────┘
    ↓
更新完成 → 记录版本 → 下一个tick生效
```

**关键设计点：**
- **原子性**：配置替换是原子的，不会出现"半新半旧"的状态
- **回滚能力**：新配置有问题可以快速回滚到旧配置
- **版本记录**：每次配置变更都有版本记录，可追溯
- **平滑过渡**：更新过程中服务不中断，旧配置继续生效直到更新完成

---

### 2.6 错误处理的架构位置

```
┌─────────────────────────────────────────────────────────┐
│                    接口层（Interface）                    │
│  - 对外暴露错误状态                                       │
│  - 接收配置修复指令                                       │
├─────────────────────────────────────────────────────────┤
│                    运行时层（Runtime）                    │
│                                                           │
│  ┌───────────────────────────────────────────────────┐  │
│  │  错误隔离层                                         │  │
│  │  - 股票级隔离 try-catch                            │  │
│  │  - 边级隔离 try-catch                              │  │
│  │  - 节点级隔离 try-catch                            │  │
│  └───────────────────────────────────────────────────┘  │
│                                                           │
│  ┌───────────────────────────────────────────────────┐  │
│  │  错误恢复层                                         │  │
│  │  - 数据源重连恢复                                   │  │
│  │  - 配置热更新恢复                                   │  │
│  │  - 公式修复重算                                     │  │
│  └───────────────────────────────────────────────────┘  │
│                                                           │
│  ┌───────────────────────────────────────────────────┐  │
│  │  错误监控层                                         │  │
│  │  - error_log_table（错误日志）                      │  │
│  │  - perf_metrics_table（性能指标）                   │  │
│  │  - 告警触发                                         │  │
│  └───────────────────────────────────────────────────┘  │
│                                                           │
├─────────────────────────────────────────────────────────┤
│                    配置层（Config）                       │
│  - system_config_table（错误处理相关配置）               │
│  - 配置校验规则                                          │
├─────────────────────────────────────────────────────────┤
│                 基础设施层（Infrastructure）              │
│  - 日志系统（错误日志输出）                               │
│  - 告警系统（错误告警通知）                               │
│  - 配置存储（版本管理、回滚）                             │
└─────────────────────────────────────────────────────────┘
```

---

## 三、层间依赖规则

### 3.1 四层架构回顾

```
┌─────────────────────────────────────────────────────────┐
│                    接口层（Interface Layer）              │
│  前端API  │  事件推送  │  WebSocket  │  REST API         │
├─────────────────────────────────────────────────────────┤
│                                                           │
│                    运行时层（Runtime Layer）              │
│  状态表  │  事件循环  │  脏驱动  │  计算引擎  │  TTL管理  │
│  错误隔离  │  错误恢复  │  性能监控                        │
│                                                           │
├─────────────────────────────────────────────────────────┤
│                                                           │
│                    配置层（Config Layer）                 │
│  ┌───────────────┐  ┌───────────────┐  ┌─────────────┐  │
│  │  类型定义      │  │  实例定义      │  │  系统配置     │  │
│  │  node_type    │  │  pool         │  │  period      │  │
│  │  edge_type    │  │  node_instance│  │  trade_cal   │  │
│  │  behavior     │  │  edge_instance│  │  system_cfg  │  │
│  │  ui_type      │  │  formula_inst │  │             │  │
│  │  formula      │  │  ui_instance  │  │             │  │
│  │  operator     │  │               │  │             │  │
│  └───────────────┘  └───────────────┘  └─────────────┘  │
│                                                           │
├─────────────────────────────────────────────────────────┤
│                                                           │
│                 基础设施层（Infrastructure Layer）        │
│  交易日历  │  数据提供者  │  公式引擎  │  日志  │  配置存储 │
│  告警系统  │  时间工具                                     │
│                                                           │
└─────────────────────────────────────────────────────────┘
```

---

### 3.2 依赖规则

#### 3.2.1 基本规则

| 规则 | 说明 | 违反后果 |
|------|------|---------|
| **上层可以依赖下层** | 接口层 → 运行时层 → 配置层 → 基础设施层 | — |
| **下层不能依赖上层** | 基础设施层不能依赖配置层，配置层不能依赖运行时层，运行时层不能依赖接口层 | 循环依赖、架构腐化 |
| **同层之间可以依赖，但要尽量少** | 同层内的模块可以互相调用，但要控制依赖数量 | 耦合度高、难以测试 |
| **反向依赖绝对禁止** | 任何情况下都不允许下层调用上层的接口 | 架构崩塌、难以维护 |
| **跨层依赖尽量避免** | 接口层尽量不要直接调用基础设施层，应该通过中间层 | 分层失去意义 |

#### 3.2.2 依赖方向图（文字版）

```
                    允许的依赖方向
                        ▲
                        │
┌───────────────────────┴───────────────────────┐
│                                               │
│   接口层 ────────────┐                        │
│     │                │ 可以直接依赖（尽量少）   │
│     ▼                ▼                        │
│   运行时层 ────────► 配置层                    │
│     │                │                        │
│     ▼                ▼                        │
│   基础设施层 ◄────────┘                        │
│                                               │
└───────────────────────────────────────────────┘
                        ▲
                        │
                    禁止的依赖方向
```

---

### 3.3 详细的依赖关系

#### 3.3.1 接口层 → 运行时层

**依赖内容：**
- 读取运行时状态（node_stocks、stock_status_table、时间戳等）
- 触发计算指令（手动触发计算、暂停/继续）
- 订阅事件（股票入池/出池、预警事件）
- 性能监控数据读取

**接口示例：**
```python
# 读取运行时状态
def get_pool_status(pool_id: str) -> PoolStatus: ...
def get_node_stocks(node_id: str) -> List[StockInfo]: ...
def get_stock_status(code: str) -> StockStatus: ...

# 控制指令
def start_pool(pool_id: str) -> bool: ...
def stop_pool(pool_id: str) -> bool: ...
def trigger_calculation(pool_id: str) -> bool: ...

# 事件订阅
def subscribe_events(callback: Callable[[Event], None]) -> None: ...
```

**不做的事：**
- 接口层不直接修改运行时状态（只能通过API调用）
- 接口层不直接操作运行时表（只能通过运行时层提供的接口）

---

#### 3.3.2 接口层 → 配置层

**依赖内容：**
- 读取配置（类型配置、实例配置、系统配置）
- 修改配置（保存股票池、修改参数）
- 配置校验
- 配置版本管理（查看历史、回滚）

**接口示例：**
```python
# 读取配置
def get_pool_list() -> List[PoolInfo]: ...
def get_node_types() -> List[NodeTypeInfo]: ...
def get_formula_list() -> List[FormulaInfo]: ...
def get_system_config() -> SystemConfig: ...

# 修改配置
def save_pool(pool_data: PoolData) -> bool: ...
def update_node_config(node_id: str, config: dict) -> bool: ...
def update_system_config(key: str, value: Any) -> bool: ...

# 配置管理
def validate_config(config_type: str, data: dict) -> ValidationResult: ...
def get_config_history(table_name: str) -> List[ConfigVersion]: ...
def rollback_config(version_id: str) -> bool: ...
```

---

#### 3.3.3 运行时层 → 配置层（只读）

**依赖内容：**
- 读取类型配置（查表：node_type_table、edge_type_table、formula_table、operator_table）
- 读取实例配置（加载股票池配置）
- 读取系统配置（超时时间、性能阈值等）
- 监听配置变更（热加载回调）

**关键原则：运行时层只读取配置层，不修改配置**

**读取方式：**
```python
# 初始化时加载
node_type_table = config_store.get('node_type_table')
edge_type_table = config_store.get('edge_type_table')
formula_table = config_store.get('formula_table')

# 运行时查表
def get_node_type(node_type_id: str) -> NodeType:
    return node_type_table.get(node_type_id)

# 热加载回调（配置变更时通知运行时层）
def on_config_changed(changed_tables: List[str]):
    # 使缓存失效，标记相关部分为脏
    invalidate_cache(changed_tables)
    mark_dirty(changed_tables)
```

---

#### 3.3.4 运行时层 → 基础设施层

**依赖内容：**
- 数据提供者（获取行情数据）
- 公式引擎（计算技术指标）
- 交易日历（判断交易日/交易时段）
- 日志系统（记录日志）
- 时间工具（时间转换、格式化）
- 告警系统（触发告警）

**接口示例：**
```python
# 数据提供者
class DataProvider:
    def poll_latest_data(self) -> Dict[str, BarDict]: ...
    def get_history_bars(self, code: str, period: str, count: int) -> List[BarDict]: ...

# 公式引擎
class FormulaEngine:
    def eval(self, formula: str, data: List[BarDict], params: dict = None) -> float: ...
    def eval_batch(self, formula: str, codes: List[str], period: str, params: dict = None) -> Dict[str, float]: ...

# 交易日历
class TradeCalendar:
    def is_trading_day(self, date: date) -> bool: ...
    def is_trading_time(self, dt: datetime) -> bool: ...
    def next_trading_day(self, date: date) -> date: ...
```

---

#### 3.3.5 配置层 → 基础设施层

**依赖内容：**
- 配置存储（加载、保存、热加载检测）
- 日志系统（记录配置变更日志）
- 版本管理（配置版本历史、回滚）

**关键原则：配置层使用基础设施层的能力来管理配置本身**

---

### 3.4 反向依赖禁止清单

以下依赖是**绝对禁止**的：

| 禁止的依赖 | 为什么禁止 | 替代方案 |
|-----------|-----------|---------|
| 基础设施层 → 配置层 | 基础设施是最底层，不应该知道配置的存在 | 通过依赖注入，把配置传给基础设施 |
| 基础设施层 → 运行时层 | 基础设施不应该知道运行时的存在 | 通过回调/观察者模式，运行时订阅基础设施事件 |
| 配置层 → 运行时层 | 配置是静态的，不应该依赖动态的运行时 | 运行时读取配置，配置变更通知运行时（事件通知，不是依赖） |
| 运行时层 → 接口层 | 运行时不应该知道接口的存在 | 通过事件/回调，接口层订阅运行时事件 |
| 配置层 → 接口层 | 配置不应该知道谁在用它 | 接口层读取配置，配置变更通过事件通知 |

**正确的反向通信方式：事件/回调/观察者模式**

下层通知上层，不是通过直接调用，而是通过：
- 事件队列（Event Queue）
- 回调函数（Callback）
- 观察者模式（Observer）
- 发布订阅（Pub/Sub）

示例：
```python
# 运行时层定义事件接口（不依赖接口层，只是定义接口）
class RuntimeEventListener:
    def on_stock_entered(self, node_id: str, code: str): ...
    def on_stock_exited(self, node_id: str, code: str): ...
    def on_error(self, error: ErrorInfo): ...

# 接口层实现这个接口（依赖运行时层的接口定义）
class FrontendEventHandler(RuntimeEventListener):
    def on_stock_entered(self, node_id: str, code: str):
        # 推送给前端
        push_to_frontend(...)
```

---

### 3.5 跨层依赖说明

**尽量避免，但不是绝对禁止：**

| 跨层依赖 | 是否允许 | 说明 |
|---------|---------|------|
| 接口层 → 基础设施层（日志） | 允许（少量） | 接口层需要打日志，这是合理的 |
| 接口层 → 基础设施层（配置存储） | 尽量避免 | 应该通过配置层，但如果是文件上传等操作可以直接用 |
| 运行时层 → 基础设施层（全部） | 允许 | 运行时层需要大量使用基础设施 |
| 配置层 → 基础设施层（全部） | 允许 | 配置层需要使用基础设施来管理配置 |

**原则：** 跨层依赖只允许在"工具类"的场景下使用（日志、工具函数等），业务逻辑必须走中间层。

---

### 3.6 层间依赖验证

怎么验证依赖关系是正确的？

1. **代码结构检查**：
   - 接口层的 import 应该只有：运行时层、配置层、基础设施层（少量）
   - 运行时层的 import 应该只有：配置层、基础设施层
   - 配置层的 import 应该只有：基础设施层
   - 基础设施层的 import 应该只有：标准库、第三方库

2. **依赖方向测试**：
   - 尝试单独测试基础设施层 → 应该可以独立运行（不需要上层）
   - 尝试单独测试配置层 + 基础设施层 → 应该可以运行（不需要运行时层）
   - 尝试单独测试运行时层 + 配置层 + 基础设施层 → 应该可以运行（不需要接口层）

3. **替换测试**：
   - 替换接口层（如从Web换成桌面） → 运行时层不用改
   - 替换数据提供者（如从天勤换成AKShare） → 运行时层不用改
   - 替换配置存储（如从JSON换成数据库） → 运行时层不用改

---

## 四、配置层内部分类澄清

### 4.1 三大分类总览

配置层内部分为三大类：类型定义、实例定义、系统配置。

```
┌─────────────────────────────────────────────────────────────┐
│                        配置层（Config Layer）                 │
│                                                               │
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐  │
│  │   类型定义层     │  │   实例定义层     │  │  系统配置层    │  │
│  │  （7张表）      │  │  （5张表）      │  │  （3张表）    │  │
│  │                 │  │                 │  │              │  │
│  │  开发时定义      │  │  用户设计时定义  │  │  运维时配置   │  │
│  │  修改频率：低    │  │  修改频率：高    │  │  修改频率：低  │  │
│  │  维护者：开发    │  │  维护者：用户/运营│  │  维护者：运维  │  │
│  └─────────────────┘  └─────────────────┘  └──────────────┘  │
│         │                      │                    │         │
│         │ 引用（通过 type_id）  │                    │         │
│         ◄──────────────────────┤                    │         │
│                                  引用系统配置参数     │         │
│         ◄───────────────────────────────────────────┤         │
└─────────────────────────────────────────────────────────────┘
```

---

### 4.2 类型定义层（Type Definition Layer）

#### 4.2.1 定位

**"是什么"**：定义系统中有哪些类型的节点、边、公式、算子。

**类比**：编程语言中的"类"（class）定义，或者数据库中的"表结构"定义。

**谁来定义**：开发人员（开发时）。

**修改频率**：低（几周甚至几个月改一次，加新功能时才改）。

#### 4.2.2 包含的表（7张）

| 表名 | 职责 | 谁来改 | 修改频率 | 改了影响谁 |
|------|------|-------|---------|-----------|
| node_type_table | 节点类型定义：身份、分类、结构属性 | 开发人员 | 很低 | 所有该类型的实例 |
| node_behavior_table | 节点行为定义：handler | 开发人员 | 很低 | 所有该类型的实例 |
| edge_type_table | 边类型定义：身份、分类、结构属性 | 开发人员 | 很低 | 所有该类型的实例 |
| edge_behavior_table | 边行为定义：handler + trigger_mode | 开发人员 | 很低 | 所有该类型的实例 |
| ui_type_table | UI类型定义：默认显示名、颜色、图标 | 开发/设计 | 低 | 所有该类型的实例UI |
| formula_table | 指标公式定义：公式表达式、参数 | 开发/运营 | 中 | 引用该公式的filter配置 |
| operator_table | 算子与组合方式定义：比较算子、组合算子 | 开发人员 | 很低 | 引用该算子的filter配置 |

#### 4.2.3 特点

1. **稳定性高**：类型定义一旦确定，很少修改
2. **影响面广**：改一个类型，所有该类型的实例都受影响
3. **质量要求高**：类型定义有bug，所有实例都有bug
4. **需要版本管理**：类型变更要谨慎，最好有向后兼容

#### 4.2.4 与实例层的关系

```
类型定义层（class）
  定义了"有什么类型"
  定义了"每种类型的行为是什么"
  定义了"每种类型的默认参数是什么"
       │
       │ 被引用（通过 type_id）
       │ 一个类型可以被多个实例引用
       ▼
实例定义层（instance）
  定义了"具体有哪些节点/边"
  定义了"每个节点/边用什么类型"
  定义了"每个节点/边的参数值是什么（覆盖默认值）"
```

---

### 4.3 实例定义层（Instance Definition Layer）

#### 4.3.1 定位

**"有哪些"**：定义具体的股票池、节点、边、自定义公式。

**类比**：编程语言中的"对象"（instance），或者数据库中的"记录"（row）。

**谁来定义**：用户/运营人员（设计股票池时）。

**修改频率**：高（用户每天都可能改，设计股票池时频繁修改）。

#### 4.3.2 包含的表（5张）

| 表名 | 职责 | 谁来改 | 修改频率 | 改了影响谁 |
|------|------|-------|---------|-----------|
| pool_table | 股票池实例：基本信息、拓扑结构 | 用户/运营 | 高 | 只影响这个股票池 |
| node_instance_table | 节点实例：类型引用、参数值 | 用户/运营 | 高 | 只影响这个节点 |
| edge_instance_table | 边实例：类型引用、filter配置 | 用户/运营 | 高 | 只影响这条边 |
| formula_instance_table | 自定义公式：用户自己写的公式 | 用户/运营 | 中 | 引用该公式的filter |
| ui_instance_table | UI实例状态：折叠、选中、视图状态 | 用户 | 高 | 只影响这个实例的UI |

#### 4.3.3 特点

1. **灵活性高**：用户可以自由创建、修改、删除实例
2. **影响面小**：改一个实例，只影响这个实例自己
3. **数量多**：实例数量远多于类型数量（一个类型可以有上百个实例）
4. **需要校验**：用户输入的配置需要校验，确保合法有效

#### 4.3.4 与类型层的关系

- 实例必须引用一个存在的类型（通过 type_id）
- 实例可以覆盖类型的默认参数
- 实例不能修改类型的行为（行为由类型定义）
- 类型删除前，必须先删除所有引用该类型的实例

---

### 4.4 系统配置层（System Configuration Layer）

#### 4.4.1 定位

**"系统怎么运行"**：定义系统级的全局配置、周期定义、交易日历。

**类比**：操作系统的"系统设置"，或者应用的"全局配置"。

**谁来定义**：运维/开发人员。

**修改频率**：低（系统部署时配置，平时很少改）。

#### 4.4.2 包含的表（3张）

| 表名 | 职责 | 谁来改 | 修改频率 | 改了影响谁 |
|------|------|-------|---------|-----------|
| period_table | 周期定义：有哪些时间周期、各周期的参数 | 开发人员 | 很低 | 全局所有使用周期的地方 |
| trade_calendar_table | 交易日历：交易日、交易时段、节假日 | 运维人员 | 低（每年更新一次节假日） | 全局所有时间判断 |
| system_config_table | 系统配置：超时时间、性能阈值、开关等 | 运维/开发 | 低 | 全局系统行为 |

#### 4.4.3 特点

1. **全局性**：影响整个系统，不是某个股票池
2. **稳定性高**：一旦配置好，很少修改
3. **影响面广**：改一个系统配置，可能影响很多地方
4. **需要权限控制**：不能让普通用户修改系统配置

---

### 4.5 三类配置的对比

| 维度 | 类型定义 | 实例定义 | 系统配置 |
|------|---------|---------|---------|
| **定位** | "是什么"（类定义） | "有哪些"（对象实例） | "怎么运行"（系统设置） |
| **类比** | 类（class） | 对象（instance） | 系统设置 |
| **谁来改** | 开发人员 | 用户/运营 | 运维/开发 |
| **修改频率** | 很低（几周~几月） | 很高（每天都可能） | 很低（几月一次） |
| **影响面** | 广（所有该类型的实例） | 小（只影响这个实例） | 很广（全系统） |
| **数量级** | 少（几十种） | 多（成百上千个） | 少（几十项） |
| **质量要求** | 极高（有bug影响所有实例） | 中（单个实例有bug不影响其他） | 高（有bug影响全系统） |
| **版本管理** | 需要（向后兼容） | 需要（保存历史版本） | 需要（配置变更审计） |
| **校验要求** | 开发时人工评审 | 运行时自动校验 | 运维时人工校验 |
| **热加载** | 支持（加新类型） | 支持（修改实例） | 部分支持（部分配置需要重启） |
| **持久化位置** | 代码/配置文件 | 数据库/配置文件 | 配置文件/环境变量 |

---

### 4.6 配置层内部依赖关系

```
                    ┌─────────────────┐
                    │   系统配置层     │
                    │  period_table   │
                    │  trade_calendar │
                    │  system_config  │
                    └────────┬────────┘
                             │
              被所有层读取系统参数
                             │
┌─────────────────┐          │          ┌─────────────────┐
│   类型定义层     │          │          │   实例定义层     │
│  node_type      │          │          │  pool_table     │
│  edge_type      │          │          │  node_instance  │
│  behavior       │◄─────────┤          │  edge_instance  │
│  ui_type        │  引用类型 │          │  formula_inst   │
│  formula        │          │          │  ui_instance    │
│  operator       │          │          │                 │
└────────┬────────┘          │          └─────────────────┘
         │                   │
         │ 被实例引用（type_id）
         │
         └───────────────────────────────►
```

**依赖规则：**
1. 类型定义层和实例定义层都可以读取系统配置层
2. 实例定义层引用类型定义层（通过 type_id）
3. 类型定义层不依赖实例定义层（类型不知道有哪些实例）
4. 系统配置层不依赖其他层（最基础的配置）

---

## 五、功能-表操作对应表（v1.16 更新版）

### 5.1 基础设施层

| 功能 | 读什么表/模块 | 写什么表/模块 | 计算 | 错误场景 | 处理策略 |
|------|-------------|-------------|------|---------|---------|
| **交易日判断** | trade_calendar_table | — | 排除周末、节假日，加上额外交易日 | 日历数据缺失 | 使用默认日历，记录warning |
| **交易时段判断** | trade_calendar_table（trading_sessions） | — | 当前时间在不在任一交易时段内 | 时段配置错误 | 保守判断：不确认就不算交易时间 |
| **数据轮询** | data_provider | latest_tick + tick_data_ts + stock_status_table | 主动 pull 最新数据，对比变化 | 数据源断开/超时 | 指数退避重试，降级到备用数据源，记录错误 |
| **指标计算** | formula_table + formula_engine | formula_engine内部表 | 公式引擎向量化批量计算 | 公式错误/数据不足 | 结果设为None，股票级隔离，记录warning |
| **配置加载** | config_store + table_schemas | — | 加载、缓存、校验配置表 | 配置格式错误/校验失败 | 保留旧配置，记录error，告警 |

---

### 5.2 配置层-类型定义

| 功能 | 读什么表 | 写什么表 | 说明 | 错误场景 | 处理策略 |
|------|---------|---------|------|---------|---------|
| **节点类型定义** | node_type_table | node_type_table | 开发时定义节点类型 | — | — |
| **节点行为定义** | node_behavior_table | node_behavior_table | 开发时定义handler | — | — |
| **边类型定义** | edge_type_table | edge_type_table | 开发时定义边类型 | — | — |
| **边行为定义** | edge_behavior_table | edge_behavior_table | 开发时定义handler | — | — |
| **UI类型定义** | ui_type_table | ui_type_table | 开发时定义默认UI | — | — |
| **公式定义** | formula_table | formula_table | 开发/运营时定义指标公式 | 公式语法错误 | 校验不通过，拒绝保存 |
| **算子定义** | operator_table | operator_table | 开发时定义比较/组合算子 | — | — |
| **类型配置热加载** | 文件系统 + table_schemas | node_type_table等 | 检测文件变更，校验后替换 | 新配置校验失败 | 保留旧配置，记录error，告警 |

---

### 5.3 配置层-实例定义

| 功能 | 读什么表 | 写什么表 | 说明 | 错误场景 | 处理策略 |
|------|---------|---------|------|---------|---------|
| **股票池创建/保存** | pool_table + node_instance_table + edge_instance_table | pool_table + node_instance_table + edge_instance_table | 用户创建/保存股票池 | 拓扑有环/引用不存在 | 校验不通过，拒绝保存，提示错误 |
| **节点实例编辑** | node_type_table + node_instance_table | node_instance_table | 用户编辑节点参数 | 参数校验失败 | 拒绝修改，提示错误 |
| **边实例编辑** | edge_type_table + formula_table + operator_table + edge_instance_table | edge_instance_table | 用户编辑边的filter配置 | 引用不存在的公式/算子 | 校验不通过，拒绝保存 |
| **UI实例编辑** | ui_instance_table | ui_instance_table | 用户拖动节点、改大小 | — | — |
| **自定义公式** | formula_instance_table | formula_instance_table | 用户自定义指标 | 公式语法错误 | 校验不通过，拒绝保存 |
| **配置热加载** | 文件系统 + table_schemas | pool_table等 | 检测文件变更，校验后替换 | 新配置校验失败 | 保留旧配置，记录error，告警 |

---

### 5.4 配置层-系统配置

| 功能 | 读什么表 | 写什么表 | 说明 | 错误场景 | 处理策略 |
|------|---------|---------|------|---------|---------|
| **周期定义管理** | period_table | period_table | 开发时定义周期 | — | — |
| **交易日历维护** | trade_calendar_table | trade_calendar_table | 运维时更新节假日 | 日期格式错误 | 校验不通过，拒绝保存 |
| **系统配置修改** | system_config_table | system_config_table | 运维修改全局配置 | 配置值超出范围 | 校验不通过，拒绝保存 |

---

### 5.5 运行时层-主循环

| 功能 | 读什么表 | 写什么表 | 计算 | 错误场景 | 处理策略 |
|------|---------|---------|------|---------|---------|
| **数据轮询** | data_provider + system_config_table | latest_tick + tick_data_ts + dirty_stocks + stock_status_table | 主动 pull，对比变化，标记脏股票 | 数据源超时/断开 | 重试→降级→告警，股票状态设为异常 |
| **交易时间判断** | trade_calendar_table | — | 先判断交易日，再判断交易时段 | 日历数据缺失 | 保守判断：不确认就不算交易时间 |
| **股票状态检测** | 最新数据 + 状态检测规则 | stock_status_table | 检测停牌/数据不足/异常 | 检测逻辑出错 | 设为normal（保守），记录error |
| **超时检测** | sys_ts + last_poll_ts + system_config_table | — | sys_ts - last_poll_ts > 阈值 | — | — |
| **周期更新** | period_table + 最新数据 | period_data + bar_data_ts | 更新未完成K线 | K线计算出错 | 股票级隔离，该股票周期数据不更新 |
| **周期确认事件** | period_data + bar_data_ts | period_confirmed_events | 数据时间跨过边界，发确认事件 | — | — |
| **脏驱动跳过** | dirty_stocks + period_confirmed_events | — | 没变化就跳过计算 | — | — |
| **节点变化计算** | dirty_stocks + node_stocks | node_changes | entered/exited/updated三集合 | 计算出错 | 节点级隔离，该节点变化清空 |
| **拓扑序处理** | node_changes + pool_table.topology | 各层状态表 | 按拓扑序处理脏节点 | 拓扑排序失败 | 退化为按ID顺序，记录error |

---

### 5.6 运行时层-节点处理

| 功能 | 读什么表 | 写什么表 | 计算 | 错误场景 | 处理策略 |
|------|---------|---------|------|---------|---------|
| **节点实例信息** | node_instance_table | — | 查表得到type_id、参数 | 实例不存在 | 跳过该节点，记录error |
| **节点类型信息** | node_type_table + type_id | — | 通过type_id查类型定义 | 类型不存在 | 节点级隔离，该节点暂停，告警 |
| **节点行为判断** | node_behavior_table + type_id | — | 查表得到handler（L1分发） | handler不存在 | 节点级隔离，记录error，告警 |
| **初始化handler** | node_behavior_table.init_handler | node_stocks | 查表调用初始化函数 | 初始化失败 | 节点级隔离，该节点不参与计算 |
| **入边handler** | node_behavior_table.in_edge_handler | node_changes | 查表调用入边处理函数 | 处理失败 | 节点级隔离，该节点变化回滚 |
| **出边handler** | node_behavior_table.out_edge_handler | 各边状态表 | 查表调用出边处理函数 | 处理失败 | 边级隔离，单条边失败不影响其他边 |
| **脏节点判断** | node_changes[nid] | — | 三集合全空就是干净的 | — | — |

---

### 5.7 运行时层-边执行层

| 功能 | 读什么表 | 写什么表 | 计算 | 错误场景 | 处理策略 |
|------|---------|---------|------|---------|---------|
| **边实例信息** | edge_instance_table | — | 查表得到type_id、filter_config | 实例不存在 | 跳过该边，记录error |
| **边类型信息** | edge_type_table + type_id | — | 通过type_id查类型定义 | 类型不存在 | 边级隔离，该边暂停，告警 |
| **触发模式判断** | edge_behavior_table.trigger_mode | — | 数据驱动还是时间驱动 | — | — |
| **时间条件检查** | period_confirmed_events + trade_calendar_table | — | 周期确认 + 交易时段检查 | — | — |
| **股票状态过滤** | stock_status_table | — | 停牌/异常的按保守策略处理 | — | 结果设为None，不传播 |
| **第一层：指标计算** | formula_table + 公式引擎 | 公式引擎内部表 | 查类型表得到公式，批量计算 | 公式计算错误 | 股票级隔离，该股票结果设为None |
| **第二层：比较判断** | operator_table + 指标值 + 股票状态 | edge_compare_results | 查类型表调算子，三态逻辑 | 算子执行错误 | 股票级隔离，结果设为None |
| **第三层：组合运算** | operator_table（combine类） + 比较结果 | edge_filter_results | 查类型表调组合函数，三态逻辑 | 组合运算错误 | 边级隔离，结果设为空集 |
| **propagate** | propagate_handler + filter结果 | node_stocks + node_changes | 股票传播，保守策略 | 传播失败 | 边级隔离，不传播，记录error |
| **事件发射** | node_changes[tid] | 事件队列 | entered/exited发事件 | 事件发送失败 | 不影响主流程，记录warning |

---

### 5.8 运行时层-TTL淘汰层

| 功能 | 读什么表 | 写什么表 | 计算 | 错误场景 | 处理策略 |
|------|---------|---------|------|---------|---------|
| 股票入池记录TTL | edge_instance_table.ttl_config | ttl_expiry_queue 插入 | expire_ts = sys_ts + ttl_sec | TTL配置错误 | 不设置TTL（永不过期），记录warning |
| TTL过期检查 | ttl_expiry_queue + sys_ts | 弹出过期项 | 最小堆：堆顶过期就弹出 | 堆操作出错 | 全量扫描（降级），记录error |
| 过期股票移除 | node_stocks[nid] | node_stocks[nid] | 从节点移除 | 移除失败 | 跳过该股票，继续处理其他，记录error |
| **过期触发级联** | — | node_changes[nid].exited | 加入exited集合 | — | — |

---

### 5.9 运行时层-错误处理

| 功能 | 读什么表 | 写什么表 | 计算 | 触发条件 | 处理策略 |
|------|---------|---------|------|---------|---------|
| **股票级错误隔离** | stock_status_table | error_log_table + stock_status_table | try-catch包裹单股票计算 | 单只股票计算失败 | 结果设为None，更新股票状态，记录错误 |
| **边级错误隔离** | edge_instance_table | error_log_table + edge状态 | try-catch包裹单条边执行 | 单条边执行失败 | 边结果设为空，不传播，记录错误 |
| **节点级错误隔离** | node_instance_table | error_log_table + node状态 | try-catch包裹单个节点处理 | 单个节点处理失败 | 节点状态不变，不传播，记录错误 |
| **数据源重连** | data_provider + system_config_table | latest_tick + period_data + error_log_table | 重连→补数据→重算 | 数据源断开后恢复 | 自动补全数据，触发全量重算，记录恢复 |
| **配置热更新** | 配置文件 + table_schemas | 配置表 + error_log_table | 检测→校验→替换→失效缓存 | 配置文件变更 | 校验通过才替换，失败保留旧配置 |
| **性能监控** | perf_metrics_table + system_config_table | perf_metrics_table + error_log_table | 采样→统计→阈值判断 | 性能指标超过阈值 | 记录warning→降级→熔断→告警 |
| **错误统计告警** | error_log_table + system_config_table | 告警系统 | 错误计数→频率统计→阈值判断 | 错误频率超过阈值 | 触发告警，通知人工介入 |

---

### 5.10 接口层

| 功能 | 读什么层 | 写什么层 | 说明 | 错误场景 | 处理策略 |
|------|---------|---------|------|---------|---------|
| **获取股票池列表** | 配置层（实例配置） | — | 读 pool_table | 配置读取失败 | 返回空列表，记录error |
| **保存股票池** | — | 配置层（实例配置） | 写 pool_table + node_instance_table + edge_instance_table | 校验失败 | 拒绝保存，返回错误信息 |
| **获取运行状态** | 运行时层 | — | 读 node_stocks + stock_status_table + 时间戳 | 运行时未启动 | 返回未启动状态 |
| **手动触发计算** | — | 运行时层 | 触发一次计算循环 | 计算失败 | 返回错误信息，不影响自动运行 |
| **事件订阅** | 运行时层 | — | 订阅 node_changes 事件 | 连接断开 | 自动重连，补发错过的事件 |
| **读取配置** | 配置层 | — | 读类型配置或实例配置 | 配置不存在 | 返回404 |
| **修改配置** | — | 配置层 | 写配置 + 触发热加载 | 校验失败 | 拒绝修改，返回错误信息 |
| **错误查询** | 运行时层（error_log_table） | — | 查询错误日志 | — | — |
| **性能查询** | 运行时层（perf_metrics_table） | — | 查询性能指标 | — | — |

---

## 六、统计总结（v1.15 → v1.16）

### 6.1 概念数量变化

| 统计项 | v1.15 | v1.16 | 变化 |
|--------|------|-------|------|
| 配置表数 | 13 张 | **15 张** | **+2（operator_table独立，system_config_table新增）** |
| 配置表分类 | 类型 + 实例 + 时间（粗分） | **类型定义 + 实例定义 + 系统配置（三大类）** | 配置层内部进一步正交拆分 |
| 核心运行时表 | 8 张 | **12 张** | **+4（error_log_table, perf_metrics_table等运维表）** |
| 表字段定义 | 只有表名和用途 | **每张表完整Schema（主键/字段/类型/约束/说明）** | 从概念到详细设计 |
| 错误处理 | 完全没有设计 | **完整的错误处理体系** | 补全错误隔离、恢复、监控 |
| 层间依赖 | 简单的四层图 | **明确的依赖规则 + 禁止清单 + 验证方法** | 从"有分层"到"有严格规则" |
| 错误场景 | 0 个 | **8+ 个具体场景 + 处理策略** | 每个错误场景都有明确的处理方式 |
| 恢复机制 | 0 个 | **3 种恢复机制（数据源/公式/配置）** | 出错了怎么恢复，清清楚楚 |

### 6.2 为什么是 v1.16？

**v1.16 是"详细设计 + 健壮性 + 严谨性"的版本：**

1. **详细设计**：所有核心表都有完整的Schema，每个字段的类型、约束、说明都清清楚楚
2. **健壮性**：错误不是例外，是常态。有完整的三级隔离机制、多种恢复策略
3. **严谨性**：层间依赖有严格规则，配置内部分类清晰，谁改什么、怎么改、改了怎么恢复，明明白白

```
演进路径：
  v1.5 ~ v1.6：概念精简阶段（从多到少，先做对）
  v1.7：性能优化阶段（股票级水位线，增量计算）
  v1.8：状态显式化阶段（三层状态表，每层结果都有表）
  v1.9：架构完善阶段（一致性 + 事件驱动 + 生命周期）
  v1.10：深度澄清阶段（并发性能 + 批次定位 + 三态传播）
  v1.11：根本性纠错（删除时间批次化，零延迟事件模型）
  v1.12：三态逻辑完善（三态贯穿全链路 + 保守策略）
  v1.13：表驱动架构升级（节点/边/算子三层表驱动）
  v1.14：L2组合表驱动落地（+公式引擎 + filter三层组合）
  v1.15：诚实化 + 类型实例分离 + 五时间戳 + 四层架构
  v1.16：详细设计 + 表Schema + 错误处理 + 层间依赖 ◀ 当前
```
