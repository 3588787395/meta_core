# v3.2 回归基础：核心类 · 属性 · 方法 · 事件

> 纯理解笔记。所有结论均从代码中提取，有文件+行号引用。不发明任何概念。

---

## 1. 核心类清单（从代码里找出来的）

以下是代码中真实存在的、与股票池核心逻辑相关的类：

| 类名 | 所在文件 | 行号 | 一句话职责 |
|------|----------|------|-----------|
| **MetaEngine** | `core/engine.py` | 298 | 股票池核心引擎，驱动整个 tick 循环、节点计算、边流转、事件发射 |
| **CompiledSchedule** | `core/engine.py` | 228 | 编译期静态调度表（拓扑序、边索引、处理计划等），一次编译跨 tick 只读 |
| **CompiledExpression** | `core/engine.py` | 146 | 封装表达式编译+缓存+安全执行的统一抽象 |
| **LRUCache** | `core/engine.py` | 98 | 基于 OrderedDict 的 LRU + TTL 缓存 |
| **DynamicCellModel** | `core/schemas.py` | 168 | 通用节点（Cell）数据模型，支持 dict/属性访问、attr 位标志展开 |
| **DynamicFlowModel** | `core/schemas.py` | 459 | 通用边（Flow）数据模型，支持 attr 位标志展开、流转模式识别 |
| **PythonFormulaEngine** | `core/formula_engine.py` | 581 | 纯 Python 公式引擎（轻量级、numpy/pandas 向量化） |
| **CompiledFormula** | `core/formula_engine.py` | 452 | 已编译的公式，保存按顺序执行的语句及其 code 对象 |
| **ConfigStore** | `core/table_engine.py` | 27 | 配置表存储：加载、缓存、校验、热加载 |
| **RuleEngine** | `core/table_engine.py` | 542 | 规则执行引擎：根据触发条件匹配并执行规则 |
| **DataBinder** | `core/table_engine.py` | 627 | 数据绑定引擎：前端字段与后端数据模型的双向绑定 |
| **PanelGenerator** | `core/table_engine.py` | 825 | 面板生成引擎：根据布局配置生成前端面板描述 |
| **PropertyOwnershipManager** | `core/table_engine.py` | 1072 | 属性所有权管理器：不同池类型下的属性所有权管理 |
| **FormulaRouter** | `core/formula_router.py` | 81 | 公式路由器（双引擎路由：HQChart + Python） |
| **LRUCache** | `core/engine.py` | 98 | 基于 OrderedDict 的 LRU + TTL 缓存 |

> 共计 **14 个核心类**，均从代码中提取，无任何发明概念。

> **重要发现**：代码中**没有**独立的 `Pool` 类、`Node` 类、`Edge` 类、`Event` 类、`Signal` 类。
> - 节点是 `dict` 或 `DynamicCellModel`（数据模型，不是行为类）
> - 边是 `dict` 或 `DynamicFlowModel`（数据模型，不是行为类）
> - 池配置就是一个包含 `nodes` 和 `edges` 的 `dict`
> - 事件/信号是 `dict` 结构，通过队列传递
> - 所有计算/执行逻辑都在 **MetaEngine** 中

---

## 2. 每个核心类的详细说明

---

### 2.1 MetaEngine（核心引擎）

**职责**：股票池的核心执行引擎，负责拓扑编译、tick 循环、节点股票流转、公式求值、事件发射等所有核心逻辑。

**代码位置**：`core/engine.py:298`

#### 属性列表（精选核心属性）

| 属性名 | 类型 | 含义 | 代码位置 |
|--------|------|------|----------|
| `tables` | `dict` | 所有配置表的字典 {表名: 表数据} | engine.py:346 |
| `_rt` | `dict` | 运行时表统一命名空间（一等公民化） | engine.py:349 |
| `module_map` | `dict` | 模块映射 {模块ID: 模块配置} | engine.py:358 |
| `engine_index` | `dict` | 公式引擎索引 {引擎ID: 引擎配置} | engine.py:358 |
| `dispatch_index` | `dict` | 分派规则索引 | engine.py:373 |
| `events` | `list` | 事件列表（池级别事件） | engine.py:371 |
| `highlight_events` | `list` | 高亮事件列表 | engine.py:371 |
| `_highlight_listeners` | `list` | 高亮事件监听器列表 | engine.py:371 |
| `formula_router` | `FormulaRouter` | 公式路由器 | engine.py:372 |
| `_event_queue` | `asyncio.Queue` | 事件队列 | engine.py:383 |
| `_signal_queue` | `asyncio.Queue` | 信号队列 | engine.py:383 |
| `_signal_events` | `list` | 信号事件列表 | engine.py:386 |
| `_loop_running` | `bool` | 循环是否在运行 | engine.py:383 |
| `_loop_paused` | `bool` | 循环是否暂停 | engine.py:383 |
| `_loop_node_stocks` | `dict` | 循环中的节点股票数据 {nid: [stock,...]} | engine.py:384 |
| `_loop_pool_config` | `dict` | 当前循环的池配置 | engine.py:384 |
| `_dirty_nodes` | `set` | 脏节点集合（股票列表变化的节点） | engine.py:385 |
| `_node_snapshots` | `dict` | 节点股票快照（用于变更检测） | engine.py:385 |
| `_data_dirty` | `bool` | 数据是否脏（行情数据是否更新） | engine.py:466 |
| `_edge_fired` | `dict` | 边时间触发记录 {eid: bool} | engine.py:467 |
| `_first_run` | `bool` | 是否首次运行 | engine.py:418 |
| `_last_snapshot` | `dict` | 上一次节点股票快照 | engine.py:412 |
| `_last_bar_hash` | `str` | 上一根 K 线的 hash | engine.py:413 |
| `_current_bar_hash` | `str` | 当前 K 线的 hash | engine.py:414 |
| `_current_bar_data` | `dict` | 当前 bar 行情数据 | engine.py:415 |
| `_current_compiled` | `CompiledSchedule` | 当前编译后的调度表引用 | engine.py:424 |
| `_compiled_cache` | `dict` | 编译期静态调度表缓存 {pool_config哈希: CompiledSchedule} | engine.py:460 |
| `_latest_tick` | `dict` | 最新 tick 行情数据（唯一真相源） | engine.py:462 |
| `_current_mode_id` | `str` | 当前运行模式 ID（live/replay/simulation） | engine.py:457 |
| `_current_time_source` | `dict` | 当前时间源配置 | engine.py:420 |
| `_current_bar_time` | `datetime` | 当前 bar 时间（回放/扫描模式） | engine.py:420 |
| `_virtual_clock` | `float` | 仿真虚拟时钟时间戳 | engine.py:420 |
| `_pool_start_time` | `datetime` | 池启动时间 | engine.py:378 |
| `_flow_exec_counts` | `dict` | 边执行次数字典 {eid: count} | engine.py:378 |
| `_flow_first_fire_ts` | `dict` | 边首次触发时间戳 {eid: ts} | engine.py:381 |
| `_flow_last_fire_ts` | `dict` | 边末次触发时间戳 {eid: ts} | engine.py:380 |
| `_filter_cache` | `LRUCache` | 筛选结果缓存 | engine.py:417 |
| `_data_cache` | `LRUCache` | 数据缓存（K线等） | engine.py:406 |
| `_tracker_formulas` | `dict` | tracker 公式配置 | engine.py:387 |
| `_formula_order` | `list` | tracker 公式计算顺序（拓扑排序） | engine.py:427 |
| `_timing_cfg` | `dict` | 时机配置（timing.json） | engine.py:356 |
| `_psatt_cfg` | `dict` | TDX 状态池属性配置（tdx_psatt.json） | engine.py:356 |
| `_edge_cfg` | `dict` | 边策略配置（edge_strategies.json） | engine.py:359 |
| `_edge_semantics_cfg` | `dict` | 边语义配置（edge_semantics.json） | engine.py:360 |
| `tq_adapter` | `Any` | 行情适配器 | engine.py:372 |
| `refresh_manager` | `Any` | 备选池刷新管理器 | engine.py:429 |

#### 核心方法列表

| 方法名 | 签名 | 作用 | 代码位置 |
|--------|------|------|----------|
| `run_pool` | `(pool_config, current_bar_data=None)` | 单次执行股票池（同步单 tick） | engine.py:3815 |
| `run_mode` | `async (mode_id, pool_config, ...)` | 按指定模式运行股票池（循环） | engine.py:3706 |
| `run_loop` | `async (pool_config, ...)` | 运行实时模式循环（live 模式） | engine.py:3722 |
| `start_loop` | `(pool_config, ...)` | 启动异步循环（需在事件循环中调用） | engine.py:3744 |
| `stop_loop` | `async ()` | 停止循环 | engine.py:3761 |
| `pause_loop` | `()` | 暂停循环 | engine.py:3755 |
| `resume_loop` | `()` | 恢复循环 | engine.py:3758 |
| `_tick` | `async (pool_config, node_stocks, ...)` | 单次 tick 执行（pre_tick + 核心逻辑 + post_tick） | engine.py:3650 |
| `_run_tick_event_driven` | `(pool_config, node_stocks, ...)` | 事件驱动核心循环（编译期缓存版） | engine.py:3578 |
| `_compile_pool` | `(pool_config)` | 编译 pool_config 为静态调度表并缓存 | engine.py:1354 |
| `_prepare_topology` | `(pool_config)` | 拓扑预计算（排序、变换单元分组、处理计划） | engine.py:1191 |
| `_should_trigger_edge` | `(edge, ctx=None)` | 统一 gate 评估：所有模式下执行全部时机守门 | engine.py:1757 |
| `_process_edge_pipeline` | `(ctx)` | 统一边处理流水线（筛选 + 后处理 hooks） | engine.py:2713 |
| `_apply_edge_filter` | `(ctx)` | 统一筛选入口（按 filter_type 分派） | engine.py:2459 |
| `_filter_unconditional` | `(ctx)` | 筛选模式1：无条件直通 | engine.py:2476 |
| `_filter_conditional` | `(ctx)` | 筛选模式2：条件过滤（nset_dispatch） | engine.py:2528 |
| `_filter_formula_eval` | `(ctx)` | 筛选模式3：公式批量求值 | engine.py:2629 |
| `_init_node_stocks` | `(nodes)` | 初始化节点股票数据 | engine.py:758 |
| `_inject_bar_data` | `(nodes, node_stocks, current_bar_data)` | 注入 bar 行情数据到节点股票 | engine.py:1993 |
| `_update_trackers` | `(node_stocks, current_bar_data)` | 更新所有股票的 tracker 指标 | engine.py:3049 |
| `_emit_transfer_events` | `(prev_stocks, updated_stocks, transfer_events)` | 发射转移事件（事件+信号） | engine.py:3492 |
| `_emit_domain_event` | `(domain, domain_ctx)` | 通用领域事件发射器 | engine.py:3197 |
| `_push_event` | `(et, code, pool_id, detail=None)` | 推送事件到事件队列 | engine.py:3078 |
| `_push_signal` | `(sig_type, code, price, ts, ...)` | 推送信号到信号队列 | engine.py:3081 |
| `_mark_dirty` | `(nid)` | 标记节点为脏节点 | engine.py:2062 |
| `_is_dirty` | `(nid)` | 检查节点是否为脏 | engine.py:2073 |
| `_clear_dirty` | `()` | 清除所有脏节点标记 | engine.py:2084 |
| `_refresh_latest_tick` | `(bar_data)` | 刷新 latest_tick 运行时表 | engine.py:2095 |
| `_on_data_updated` | `(node_ids, current_bar_data)` | 数据更新事件处理（标记脏节点） | engine.py:2129 |
| `_update_node_snapshot` | `(nid, stocks)` | 更新节点股票快照，变化则标脏 | engine.py:2188 |
| `_resolve_edge_context` | `(edge, nodes)` | 从边和节点提取标准化的边上下文 | engine.py:1153 |
| `_resolve_node_type` | `(n)` | 统一节点类型解析 | engine.py:1116 |
| `_resolve_edge_type` | `(source_type)` | 查边类型（基于源节点类型） | engine.py:2209 |
| `_resolve_flow_attrs` | `(fa, ep)` | 深度表驱动解析 flow 属性 | engine.py:2435 |
| `_apply_tdx_psatt_ttl` | `(node_id, node, node_stocks)` | 应用 TTL 过期淘汰 | engine.py:1902 |
| `fire_rules` | `(trigger_type, field, value, context)` | 触发规则引擎执行 | engine.py:737 |
| `get_event_queue` | `()` | 获取事件队列 | engine.py:3771 |

#### 事件

**发出的事件**：
- `pool_start` - 池启动事件（engine.py:3827）
- `pool_end` - 池结束事件（engine.py:3867）
- `flow_fired` - 边触发事件（engine.py:3856）
- 领域事件（通过 `_emit_domain_event`）：入池、出池、TTL 过期等（engine.py:3197）
- 信号（通过 `_push_signal`）：买卖信号等（engine.py:3081）
- 高亮事件（通过 `_emit_highlight`）（engine.py:3016）

**响应的事件**：
- 数据更新事件（通过 `_on_data_updated` 接收外部数据更新）（engine.py:2129）
- 外部 tick 驱动（通过 `run_pool` / `_tick` 方法调用）

---

### 2.2 CompiledSchedule（编译期静态调度表）

**职责**：一次性编译 pool_config 为静态调度表，跨 tick 只读，避免每 tick 重算拓扑。

**代码位置**：`core/engine.py:228`（@dataclass）

#### 属性列表

| 属性名 | 类型 | 含义 | 代码位置 |
|--------|------|------|----------|
| `nodes` | `dict` | 节点字典 {nid: node} | engine.py:257 |
| `edges` | `list` | 边列表 | engine.py:258 |
| `edge_index` | `dict` | 边索引 {eid: edge} | engine.py:259 |
| `depths` | `dict` | 节点深度字典 {nid: depth}（longest-path） | engine.py:260 |
| `topo_order` | `list` | 节点 id 列表，按深度升序（预定的执行顺序） | engine.py:261 |
| `processing_plan` | `list` | 处理计划 [(edge, filter_type), ...]，按源深度排序 | engine.py:262 |
| `out_edges` | `dict` | 节点出边邻接表 {nid: [edge, ...]} | engine.py:263 |
| `in_edges` | `dict` | 节点入边邻接表 {nid: [edge, ...]} | engine.py:264 |
| `units` | `list` | 变换单元三元组列表 | engine.py:265 |
| `standalone_edges` | `list` | 独立边列表 | engine.py:266 |
| `edge_ctx` | `dict` | 预计算的边上下文 + filter_type {eid: {sid,tid,...}} | engine.py:267 |
| `edge_timing` | `dict` | 编译期时机门控规则 | engine.py:268 |
| `edge_filter_spec` | `dict` | 编译期筛选分派规则 | engine.py:269 |
| `edge_flow_spec` | `dict` | 编译期状态流转规则 | engine.py:270 |
| `edge_action_spec` | `dict` | 编译期 callback 副作用规则 | engine.py:271 |
| `edge_ttl_spec` | `dict` | 编译期 ttl 超时淘汰规则 | engine.py:272 |

#### 方法列表

无（纯数据类，dataclass）

#### 事件

无（纯数据类）

---

### 2.3 DynamicCellModel（通用节点数据模型）

**职责**：通用节点（Cell）数据模型，根据 field_definitions.json 动态加载字段定义。

**代码位置**：`core/schemas.py:168`

#### 属性列表

| 属性名 | 类型 | 含义 | 代码位置 |
|--------|------|------|----------|
| `_data` | `dict` | 已知字段数据存储 | schemas.py:179 |
| `_extra` | `dict` | 未知字段数据存储 | schemas.py:180 |
| `_present_attrs` | `Set[str]` | 原始输入中存在的属性集合 | schemas.py:181 |

> 实际节点属性通过 dict-style 或 attribute-style 访问，如 `model['type']`、`model.type`
> 常见字段：`id`, `type`, `pos`, `clr`, `text`, `attr`, `params`, `name`, `label` 等

#### 方法列表

| 方法名 | 签名 | 作用 | 代码位置 |
|--------|------|------|----------|
| `from_dict` | `classmethod (data: dict)` | 从原始字典创建 DynamicCellModel | schemas.py:184 |
| `__getitem__` | `(key)` | dict 风格取值 | schemas.py:272 |
| `__setitem__` | `(key, value)` | dict 风格设值 | schemas.py:279 |
| `__contains__` | `(key)` | dict 风格包含判断 | schemas.py:282 |
| `get` | `(key, default=None)` | dict 风格 get | schemas.py:285 |
| `keys` | `()` | 返回所有数据键的视图 | schemas.py:293 |
| `__getattr__` | `(name)` | 属性风格访问 | schemas.py:299 |

#### 事件

无（纯数据模型）

---

### 2.4 DynamicFlowModel（通用边数据模型）

**职责**：通用边（Flow）数据模型，支持 attr 位标志展开、流转模式识别。

**代码位置**：`core/schemas.py:459`

#### 属性列表

| 属性名 | 类型 | 含义 | 代码位置 |
|--------|------|------|----------|
| `_data` | `dict` | 已知字段数据存储 | schemas.py:470 |
| `_extra` | `dict` | 未知字段数据存储 | schemas.py:471 |
| `_present_attrs` | `Set[str]` | 原始输入中存在的属性集合 | schemas.py:472 |

> 实际边属性通过 dict-style 或 attribute-style 访问
> 常见字段：`from`, `to`, `attr`, `params`, `id`, `label`, `begin`, `end`, `interval` 等
> attr 位标志自动展开为：`delete_source`, `keep_source`, `clear_dest_first` 等

#### 方法列表

| 方法名 | 签名 | 作用 | 代码位置 |
|--------|------|------|----------|
| `from_dict` | `classmethod (data: dict)` | 从原始字典创建 DynamicFlowModel | schemas.py:474 |
| `from_int` | `classmethod (attr_int: int)` | 从 int 解析 flow attr bits | schemas.py:528 |
| `to_int` | `()` | 将 boolean 属性重新组合成 attr int | schemas.py:549 |
| `model_dump` | `(exclude=None)` | 兼容旧 pydantic model_dump() 接口 | schemas.py:540 |
| `identify_mode` | `()` | 识别流转模式 | schemas.py:555 |
| `to_dict` | `()` | 序列化回字典 | schemas.py:618 |
| `__getitem__` | `(key)` | dict 风格取值 | schemas.py:576 |
| `get` | `(key, default=None)` | dict 风格 get | schemas.py:589 |

#### 事件

无（纯数据模型）

---

### 2.5 PythonFormulaEngine（纯 Python 公式引擎）

**职责**：轻量级公式引擎，使用 numpy/pandas 向量化实现 TDX 风格公式。

**代码位置**：`core/formula_engine.py:581`

#### 属性列表

| 属性名 | 类型 | 含义 | 代码位置 |
|--------|------|------|----------|
| `data_query` | `Any` | 数据查询对象（可选） | formula_engine.py:591 |
| `_compiled_cache` | `_LRUCache` | 公式编译结果缓存 | formula_engine.py:592 |

#### 方法列表

| 方法名 | 签名 | 作用 | 代码位置 |
|--------|------|------|----------|
| `_compile` | `(formula: str)` | 将公式字符串编译为 CompiledFormula，结果缓存 | formula_engine.py:594 |
| `eval` | `(formula, bars, args=None)` | 对单只股票的 bars 求值 | formula_engine.py:604 |
| `eval_batch` | `(formula, symbols, period, ...)` | 批量求值：为每只标的取数据并分别求值 | formula_engine.py:620 |

#### 事件

无

---

### 2.6 CompiledFormula（已编译公式）

**职责**：保存已编译的公式语句（赋值/输出）及其 code 对象。

**代码位置**：`core/formula_engine.py:452`（@dataclass）

#### 属性列表

| 属性名 | 类型 | 含义 | 代码位置 |
|--------|------|------|----------|
| `formula` | `str` | 原始公式字符串 | formula_engine.py:455 |
| `statements` | `List[Tuple]` | 语句列表 [(kind, name, code), ...] | formula_engine.py:456 |

#### 方法列表

| 方法名 | 签名 | 作用 | 代码位置 |
|--------|------|------|----------|
| `eval` | `(bars, args=None)` | 对单只股票的 K 线数据进行求值 | formula_engine.py:486 |
| `_last_value` | `staticmethod (value)` | 取序列最后一个标量值 | formula_engine.py:466 |

#### 事件

无（纯数据 + 计算类）

---

### 2.7 CompiledExpression（编译表达式）

**职责**：封装表达式编译+缓存+安全执行的统一抽象。

**代码位置**：`core/engine.py:146`

#### 属性列表

| 属性名 | 类型 | 含义 | 代码位置 |
|--------|------|------|----------|
| `expr_str` | `str` | 表达式字符串 | engine.py:155 |
| `name` | `str` | 表达式名称（用于调试） | engine.py:156 |
| `_compiled` | `code` | 编译后的 code 对象 | engine.py:157 |
| `_cache` | `dict` | 类级缓存 {expr_str: CompiledExpression} | engine.py:152 |

#### 方法列表

| 方法名 | 签名 | 作用 | 代码位置 |
|--------|------|------|----------|
| `get` | `classmethod (expr_str, name="")` | 工厂方法：带缓存的实例获取 | engine.py:168 |
| `evaluate` | `(context_dict=None)` | 安全执行编译后的表达式 | engine.py:176 |
| `evaluate_conditional` | `(condition_expr, expression, ...)` | 带条件的双表达式求值 | engine.py:196 |

#### 事件

无

---

### 2.8 ConfigStore（配置表存储）

**职责**：配置表的加载、缓存、校验、热加载。

**代码位置**：`core/table_engine.py:27`

#### 属性列表

| 属性名 | 类型 | 含义 | 代码位置 |
|--------|------|------|----------|
| `_config_dir` | `Path` | 配置目录路径 | table_engine.py:31 |
| `_tables` | `Dict[str, Dict]` | 配置表字典 {表名: 表数据} | table_engine.py:32 |
| `_hashes` | `Dict[str, str]` | 文件 hash（用于热加载检测） | table_engine.py:33 |
| `_load_times` | `Dict[str, float]` | 加载时间 | table_engine.py:34 |
| `_validators` | `Dict[str, Callable]` | 自定义校验器 | table_engine.py:35 |
| `_storage` | `Any` | Storage 引用（版本记录和回滚） | table_engine.py:37 |
| `_schema_validator` | `Any` | SchemaValidator 引用 | table_engine.py:38 |
| `_categories` | `List[Dict]` | 分类元数据 | table_engine.py:40 |
| `_locks` | `Dict[str, Dict]` | 表锁定状态 | table_engine.py:47 |

#### 方法列表

| 方法名 | 签名 | 作用 | 代码位置 |
|--------|------|------|----------|
| `load_all` | `()` | 加载所有配置表 | table_engine.py:49 |
| `get` | `(name, default=None)` | 获取配置表 | table_engine.py:248 |
| `get_layout` | `(layout_id)` | 获取 UI 布局配置 | table_engine.py:252 |
| `get_layout_for_type` | `(target_type, pool_type)` | 根据节点类型和池类型查找布局 | table_engine.py:257 |
| `check_hot_reload` | `()` | 检测配置表变更并热加载 | table_engine.py:326 |
| `rollback_config` | `(version_id)` | 回滚配置到指定版本 | table_engine.py:410 |
| `validate_table_with_report` | `(name, data)` | 校验配置表并返回富报告 | table_engine.py:181 |
| `register_validator` | `(name, validator)` | 注册自定义校验器 | table_engine.py:212 |
| `is_table_locked` | `(table_name)` | 检查表是否被锁定 | table_engine.py:512 |
| `lock_table` | `(table_name, reason)` | 锁定一张表 | table_engine.py:516 |
| `unlock_table` | `(table_name)` | 解锁一张表 | table_engine.py:527 |
| `invalidate_all_caches` | `()` | 使所有关联引擎缓存失效 | table_engine.py:453 |

#### 事件

无（但通过热加载检测触发配置变更通知）

---

### 2.9 RuleEngine（规则执行引擎）

**职责**：根据触发条件匹配并执行规则。

**代码位置**：`core/table_engine.py:542`

#### 属性列表

| 属性名 | 类型 | 含义 | 代码位置 |
|--------|------|------|----------|
| `_store` | `ConfigStore` | 配置存储引用 | table_engine.py:546 |
| `_handlers` | `Dict[str, Callable]` | 动作处理器字典 | table_engine.py:547 |
| `_context` | `Dict[str, Any]` | 执行上下文 | table_engine.py:548 |

#### 方法列表

| 方法名 | 签名 | 作用 | 代码位置 |
|--------|------|------|----------|
| `register_handler` | `(action, handler)` | 注册动作处理器 | table_engine.py:558 |
| `set_context` | `(key, value)` | 设置执行上下文 | table_engine.py:562 |
| `fire` | `(trigger_type, field, value, context)` | 触发规则执行，返回执行结果列表 | table_engine.py:566 |
| `invalidate_cache` | `()` | 使内部缓存失效 | table_engine.py:550 |

#### 事件

- 响应 `fire` 调用，根据触发条件匹配规则并执行动作

---

### 2.10 FormulaRouter（公式路由器）

**职责**：双引擎公式路由（HQChart + Python）。

**代码位置**：`core/formula_router.py:81`

（注：具体方法需进一步读取 formula_router.py，此处为概要）

---

## 3. 类关系图（文字版）

### 3.1 继承关系

```
BaseModel (pydantic)
  ├── PositionModel
  ├── TradeAttrModel
  ├── ActionModel
  ├── StockSnapshotModel
  ├── PoolMetaModel
  └── ... (其他 pydantic 模型)

dict
  └── LRUCache

_CellAttrBitsCompat
  ├── Cell200AttrBitsModel
  ├── Cell201AttrBitsModel
  └── Cell202AttrBitsModel
```

### 3.2 组合关系（MetaEngine 的组件）

```
MetaEngine
  ├── CompiledSchedule (_current_compiled, _compiled_cache)
  │     ├── nodes (dict of node dicts)
  │     ├── edges (list of edge dicts)
  │     ├── edge_index, depths, topo_order
  │     ├── processing_plan
  │     ├── out_edges, in_edges
  │     └── edge_ctx / edge_timing / edge_filter_spec / edge_flow_spec / ...
  ├── LRUCache (_data_cache, _filter_cache)
  ├── CompiledExpression (通过类方法调用，非实例持有)
  ├── PythonFormulaEngine (通过 formula_router 间接持有)
  ├── FormulaRouter (formula_router)
  ├── ConfigStore (通过 set_table_engine 注入)
  ├── RuleEngine (_rule_engine, 通过 set_table_engine 注入)
  ├── PanelGenerator (_panel_generator, 通过 set_table_engine 注入)
  ├── PropertyOwnershipManager (动态创建)
  ├── DataBinder (通过 PanelGenerator 间接使用)
  ├── asyncio.Queue (_event_queue, _signal_queue, _alert_queue)
  └── 各种配置字典 (_timing_cfg, _psatt_cfg, _edge_cfg, ...)
```

### 3.3 引用关系

```
池配置 (dict)
  ├── nodes (list/dict of node dicts)
  │     每个 node: {id, type, name, params: {...}, pos, attr, ...}
  └── edges (list of edge dicts)
        每个 edge: {id, from, to, params: {...}, attr, ...}

节点股票数据 (dict)
  └── {nid: [stock_dict, ...]}
        每个 stock: {code, label, _tracker: {...}, indate, intime, ...}
```

### 3.4 数据模型关系

```
DynamicCellModel (节点模型)
  ├── 支持 dict/attribute 访问
  ├── attr 自动展开为 bit_fields
  └── 嵌套对象解析（tdx_psatt, tdx_func, ...）

DynamicFlowModel (边模型)
  ├── 支持 dict/attribute 访问
  ├── attr 自动展开为 bit_fields
  └── 流转模式识别（identify_mode）
```

---

## 4. 核心类之间的交互流程（从 tick 到事件发出）

### 4.1 单次 tick 完整流程

```
外部调用 run_pool(pool_config, bar_data)
    │
    ▼
1. _init_node_stocks(nodes)
   └── 初始化每个节点的股票列表
    │
    ▼
2. _validate_pool_topology(nodes, edges)
   └── 拓扑校验（仅告警）
    │
    ▼
3. _inject_bar_data(nodes, node_stocks, current_bar_data)
   └── 将行情数据注入到节点股票中
    │
    ▼
4. _update_node_snapshot(nid, stocks)  [每个节点]
   └── 更新节点快照，变化则标脏
    │
    ▼
5. _run_tick_event_driven(pool_config, node_stocks, bar_data)
   │
   ├─ 5.1 _get_compiled(pool_config) → CompiledSchedule
   │    └── 首次调用编译，后续从缓存取
   │
   ├─ 5.2 _refresh_latest_tick(bar_data)
   │    └── 刷新 latest_tick，hash 变化则置 data_dirty=True
   │
   ├─ 5.3 首次运行时标脏所有源节点（入度=0）
   │
   └─ 5.4 按拓扑序遍历所有边：
         │
         ├─ fired = _should_trigger_edge(edge)
         │    └── 时机守门（starttype + cxtype + begin/end/interval）
         │
         ├─ triggered = fired AND (node_dirty[sid] OR data_dirty)
         │
         └─ 如果 triggered:
              │
              ├─ _build_pipeline_ctx(ec, ...)
              │    └── 构建流水线上下文字典
              │
              ├─ _process_edge_pipeline(ctx)
              │    │
              │    ├─ _apply_edge_filter(ctx)  [按 filter_type 分派]
              │    │    ├─ _filter_unconditional(ctx)   （直通）
              │    │    ├─ _filter_conditional(ctx)     （nset_dispatch 条件过滤）
              │    │    └─ _filter_formula_eval(ctx)   （公式批量求值）
              │    │
              │    └── _run_post_propagate_hooks(ctx, ...)
              │         ├── _post_record_execution(ctx)
              │         ├── _post_handle_new_entries(ctx)
              │         └── _post_apply_ttl(ctx)
              │
              └── _mark_dirty(tid)  （级联传播，标脏目标节点）
    │
    ▼
6. _update_trackers(node_stocks, current_bar_data)
   └── 更新所有股票的 tracker 指标
    │
    ▼
7. _emit_transfer_events(prev, node_stocks, tevs)
   │
   ├─ 7.1 预建索引（node_map, stock_index, prev_stock_index）
   │
   ├─ 7.2 批量写日志
   │
   └─ 7.3 遍历 event_domain_templates：
        ├── _resolve_codes(codes_source) → 代码列表
        ├── _resolve_domain_ctx(tpl, base_ctx, code)
        └── _emit_domain_event(domain, domain_ctx)
             ├── _push_event(etype, code, pool_id, detail)
             └── _push_signal(sig_type, code, price, ts, ...)
    │
    ▼
8. _post_tick(node_stocks, current_bar_data)
   └── post_tick 四阶段流水线（pk_ranking/analysis_angles/dashboard/alerts）
    │
    ▼
9. 返回结果 {'success', 'node_states', 'events', ...}
```

### 4.2 触发判定核心逻辑

```
边是否执行 = edge_fired AND (node_dirty[src] OR data_dirty)

edge_fired = 时机守门结果（_should_trigger_edge）
  ├── starttype 守门（开始时间/条件）
  ├── cxtype 守门（持续时间/结束条件）
  └── begin/end/interval 守门（回放模式）

node_dirty[src] = 源节点股票列表是否变化
data_dirty = 行情数据是否更新
```

---

## 5. 对股票池本质的最新理解（一句话）

**股票池就是一个由节点（股票容器）和边（转移规则）组成的有向图，每个 tick 通过时机守门 + 数据变更检测触发边的筛选逻辑，将符合条件的股票从源节点转移到目标节点，并发出事件和信号。**

---

## 6. 之前理解错了的地方（至少 5 条）

### 错误 1：发明了"变换单元"概念
- **错误**：v3.1 及之前版本把"变换单元"当作核心架构概念，认为它是股票池的基本执行单元
- **真相**：代码中 `_group_transformation_units` 只是一个**拓扑优化手段**（engine.py:2217），将特定模式的边分组以优化执行顺序，不是核心概念。CompiledSchedule 中的 `units` 字段也只是优化数据结构，不是一等公民
- **证据**：`edge_strategies.json` 中才配置了 `transformation_unit_strategies`，如果配置不存在则直接返回 `([], list(edges))` 全部回退逐边（engine.py:2222-2224）

### 错误 2：认为边有"计算执行功能"
- **错误**：之前认为边是执行单元，有自己的计算逻辑
- **真相**：边本质上只是**参数和触发条件的配置**（dict 结构），所有计算执行逻辑都在 **MetaEngine** 中。边的 `params` 定义了触发条件、筛选方式、流转模式等，但边本身不执行任何计算
- **证据**：边就是普通 dict，没有 class，没有方法。所有处理逻辑都在 MetaEngine 的 `_filter_unconditional` / `_filter_conditional` / `_filter_formula_eval` 等方法中

### 错误 3：认为"触发条件"是边的执行逻辑
- **错误**：把触发条件理解为边的"执行器"
- **真相**：触发条件只是**节点的事件配置**，是时机守门（gate）的判断依据，决定"什么时候允许这条边被评估"，不是边的执行本身。边的执行是筛选+流转逻辑
- **证据**：`_should_trigger_edge` 返回 bool 表示"是否允许触发"，实际执行在 `_process_edge_pipeline` 中（engine.py:1757 vs engine.py:2713）

### 错误 4：认为有独立的 Pool/Node/Edge 类
- **错误**：假设存在 Pool 类、Node 类、Edge 类作为面向对象的核心类
- **真相**：代码中**没有**这些行为类。池配置就是 dict，节点就是 dict，边就是 dict。只有数据模型（DynamicCellModel、DynamicFlowModel）用于解析和验证，没有行为类。所有行为都在 MetaEngine 中
- **证据**：Grep 搜索 `class Pool|class Node|class Edge` 在核心代码中无匹配，只有 schemas.py 中的数据模型

### 错误 5：认为节点有"计算执行功能"
- **错误**：认为节点会主动计算、主动执行逻辑
- **真相**：节点只是**股票的容器**（存股票列表的 dict），没有计算功能。所有计算都是由 MetaEngine 驱动的，节点被动接收股票流入流出
- **证据**：节点就是 `{'id': ..., 'type': ..., 'params': {...}, ...}` 这样的 dict，node_stocks 是 `{nid: [stock, ...]}` 的字典

### 错误 6：认为"无转移节点"需要特殊处理
- **错误**：认为无转移节点是一种特殊类型的节点，需要特殊逻辑
- **真相**：没有出边的节点就是"无转移节点"，它就是一个普通的股票容器节点，不需要任何计算。MetaEngine 按拓扑序遍历边时，自然不会处理不存在的出边
- **证据**：`out_edges` 邻接表中，没有出边的节点对应空列表，遍历时自然跳过（engine.py:3608-3609）

### 错误 7：认为事件是独立的一等公民
- **错误**：假设存在 Event 类、Signal 类，有复杂的事件系统
- **真相**：事件和信号就是 dict 结构，通过 `asyncio.Queue` 传递。没有独立的 Event/Signal 类，也没有复杂的事件总线
- **证据**：`_push_event` 直接构造 dict 放入队列（engine.py:3079），`_push_signal` 同理（engine.py:3082）

### 错误 8：认为公式系统是独立的核心子系统
- **错误**：把 FormulaEngine 理解为和 MetaEngine 平级的核心组件
- **真相**：公式引擎只是**筛选的一种手段**（filter_type=formula_eval），是 MetaEngine 筛选逻辑的一个分支，不是独立的核心子系统
- **证据**：`_filter_formula_eval` 只是三个筛选模式之一（另外两个是 unconditional 和 conditional），由 `_apply_edge_filter` 分派调用（engine.py:2459-2474）

---

> 本文档所有结论均从代码提取，未发明任何新概念。
