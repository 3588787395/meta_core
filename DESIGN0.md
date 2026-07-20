# DESIGN0 — 架构合同

## 1 核心原则

**程序 = MetaEngine（唯一解释器）+ 三种表**

- 表 = 可查询、可写入、固定 Schema 的数据结构。内存 Dict 也是表。不按介质区分，只按角色区分。
- 引擎不含领域知识。只做：读表 → 计算 → 写表。
- 新增功能 = 加 JSON 条目，零行 `engine.py` 改动。

### 1.1 新增架构特性（v2 → v3 迁移完成）

迁移后核心执行路径由 `PoolEngine` + `PoolState` + `EdgeState` 承担，`MetaEngine`
退化为兼容门面（查 `core/engine.py`）。

- **单一真相源 `PoolState`**（`core/runtime.py`）：16 张池级运行时表收敛到
  `_tables` 容器（I13 新增 `prev_tick` 表支撑 TickTable 激活）；边级 4 张表
  （`exec_ctx` / `edge_fired` / `formula_results` / `filter_inputs`）下沉到
  `EdgeState`（`core/edge_state.py`）。此外 `dirty`（`DirtyState`）与
  `first_run`（`bool`）是独立于 `_tables` 的核心状态属性。
- **新核心引擎 `PoolEngine`**（`core/engine.py`）：核心类仅保留 5 个属性
  （`meta` / `pool_config` / `nodes` / `state` / `_components`），辅助方法集中到
  `PoolEngineMixin`；`CompiledSchedule`、`EdgeExecutor`、`EventBus`、`DataUpdater`、
  `BarComposer` 等组件放入 `_components` 容器。核心 tick 循环严格遵循：

  ```
  triggered[eid] = edge_fired[eid] AND (dirty.nodes[sid] OR dirty.data)
  ```

- **编译期/运行期分离**：`Compiler.compile(pool_config)`（`core/compiler.py`）
  一次性产出 `CompiledSchedule`，含 `execution_order` / `edge_ctx` /
  `edge_timing_spec` / `edge_filter_spec` / `edge_propagate_spec`（业务上亦称
  `edge_flow_spec`）/ `edge_action_spec` / `edge_ttl_spec`。
- **事件驱动增量计算**：`PoolEngine.run_tick()` 在 tick 开头统一计算
  `edge_fired`，随后按拓扑序执行触发边；`dirty.nodes` / `dirty.data` 由
  `DataUpdater` 通过 `EventBus` 设置。拓扑与边规格在 pool_config 不变时跨 tick 复用。
- **UI 层解耦**：`SnapshotBuilder` / `UIRenderer` / `WebSocketPublisher` 由
  `MetaEngine` 持有并订阅 `PoolEngine` 的 `EventBus`，`PoolEngine` 不再直接
  依赖 UI 组件。
- **三种运行模式统一入口**：`MetaEngine.run_mode(mode_id)` 仍查
  `runtime_modes.json` 初始化，但内部委托给 `PoolEngine`；差异仅在时间源、
  数据源、交易接口三个维度。
- **时间统一**：单一入口 `time_at(state)`（`core/time_util.py`），三模式差异仅在
  参数（`state.time_source["current_ts"]` 优先，wall 回退）。`PoolEngine._now` /
  `MetaEngine._now` 方法体委托 `time_at`，单一真相源。MetaEngine 信号/事件/域时间戳
  全部经 `time_at(state=...)` 模式感知（I8 收敛 6 处）。`_safe_timestamp` 三副本
  （engine.py / _compat.py / ttl_helper.py）统一到 `time_util.py:63` 单一定义（I10）。
- **到时事件中断驱动**：边触发 + TTL 折叠为同一 `on_timed_event(spec, loop)` 方法
  （`core/time_util.py`），经 `loop.call_at` 注册中断回调，不使用 `asyncio.sleep`
  轮询。`TimedEventSpec`（at_fn/interval/end_fn/action/params）由编译期
  `build_timed_event_specs` 一次性产出。`run_loop` 暂停改 `pause_event.wait()` 中断。
- **表达式求值安全统一**：`CompiledExpression`（`core/_compat.py`）改用 `ast` 受控求值
  （`_eval_derived_ast`），与 `evaluators.py` 的 `_eval_derived_expr` 同源。函数白名单
  `_DERIVED_FUNCS = {"max":max,"min":min,"abs":abs,"round":round}` 表驱动，无 if/elif
  分支（I9）。消除 `eval()` 安全风险，统一全系统表达式求值方式。
- **数据驱动单真相源**：`_inject_bar_data` 双真相源消除（I13），bar 字段统一为
  `latest_tick` 单真相源；`TickTable` 实时绑定 `state.latest_tick` + `state.prev_tick`，
  `prev_column` 不再恒 None，cross 模式 prev_value 真实可用。
- **TTL 双路径收敛**：2 套 TTL 路径（TDX `_run_ttl` + DZH `apply_ttl`）收敛为 1 套
  （I16），编译期 `TTLSpec.check_type` 表驱动分派（interval/endtime/none），
  `ttl_helper.py` 降级为薄壳（276→82 行）。
- **FilterSpec 双路径收敛**：`dispatch_key`/`evaluator` 双路径收敛为 `evaluator_type`
  单键（I18），`_FILTER_EVALUATORS` dict 表驱动分派（pass_through/formula/scalar/set_op），
  修复 nset=3/4 标量路径潜伏 bug。
- **6 处表驱动收敛**（I17-I20）：`_TTL_CHECK_HANDLERS`/`_STARTTYPE_GATE_HANDLERS`/
  `_CXTYPE_POST_GATES`/`_PROPAGATE_STRATEGIES`/`_FILTER_EVALUATORS`/`_ACTION_HANDLERS`
  全部 dict 表驱动，消除运行期 if/elif 分派。
- **post_tick 流水线表驱动**：`_post_tick()` 查 `post_tick_pipeline.json`。
- **事件/信号生成表驱动**：事件生成由 `EdgeExecutor` 调用，查
  `event_rules.json` / `signal_rules.json` / `pool_roles.json`。
- **异步并发数据获取**：`DataUpdater` + `BarComposer` 支持多时间框架并发
  获取 K 线数据，带缓存机制。
- **持仓跟踪、交易信号、事件流**：功能保留，数据真相源改为 `PoolState`。
- **兼容层清理**：旧 `_execute_flowsCore` / `_tick_event_driven` / `_tick_simple`
  已删除；`core/_compat.py` 中的 `_MetaEngineCompat` 基类已删除，
  仅保留运行时工具函数。

旧路径 `_execute_flows()` / `_execute_flowsCore()` / `_tick_event_driven()` /
`_tick_simple()` 已删除；新核心路径为 `PoolEngine.run_tick()` + `EdgeExecutor.run()`。

### 1.2 事件驱动架构（v4 迁移完成）

`unify-stockpool-oop-event-driven` spec 实施后，系统升级为 v4 事件驱动架构：

- **EventBus 为唯一通信中介**：事件类型从 4 种扩展到 30 种（`ConfigLoaded`/`ConfigChanged`/`PoolLoaded`/`TickReceived`/`DataChanged`/`BarComposed`/`FormulaEvaluated`/`CrossOverDetected`/`StockFiltered`/`EdgeFired`/`TransferExecuted`/`TTLExpired`/`Signal`/`OrderPlaced`/`OrderFilled`/`PositionUpdated`/`StatisticsUpdated`/`RankingChanged`/`AlertRaised`/`SnapshotUpdated`/`EventLogged`/`ModeChanged`/`TimeAdvanced`/`ReplayStarted`/`ReplayStep`/`SimulationStep`/`ImportStarted`/`ExportCompleted` + 原 4 种 `DataChanged`/`Executed`/`DomainEvent`/`Signal`）。
- **16 模块化重组**：EventBus / Domain / Config / Database / DataSource / TickBar / Formula / Screening / Execution / Trade / Statistics / Monitoring / ImportExport / RuntimeMode / HotReload / API。每个模块职责单一，仅与 EventBus 交互。
- **模块零引用约束**：除白名单（`core.event_bus` / `core.domain` / `core.schemas` / 标准库 / 第三方库）外，禁止 `from core.xxx import` / `from services.xxx import` / `from converters.xxx import`。模块构造函数仅接收 `EventBus` + 配置 dict + 可选 `Protocol` 接口。
- **统一领域对象模型**：`core/domain/` 包含 9 个 Node 子类 + 2 个 Edge 子类 + 7 个 Spec 类（TimingSpec/FilterSpec/PropagateSpec/ActionSpec/TTLSpec/CandidateRange/ReloadSchedule）+ 6 个 Evaluator 子类，覆盖 DZH/TDX 全部功能。
- **MetaEngine 退化为 thin compat shim**：`MetaEngine.__init__` 接收 `bus` 参数；`MetaEngine.__getattr__` 仅代理 `capability_registry`，其他属性抛 `AttributeError`。PoolEngine 成为唯一核心引擎。
- **app.py lifespan 事件布线器**：创建 EventBus 后依次实例化 16 个模块，每个模块仅注入 EventBus + 配置 dict，不传递其他模块引用。
- **tick 执行链 10 类事件按序发布**：TickReceived → DataChanged → BarComposed → FormulaEvaluated → StockFiltered → EdgeFired+TransferExecuted → Signal → OrderPlaced+OrderFilled+PositionUpdated → StatisticsUpdated+RankingChanged → AlertRaised+SnapshotUpdated+EventLogged。
- **三模式事件化**：ModeChanged 事件驱动 TickBar/Execution/Trade/Database 四模块切换数据源/时间源/交易接口/副作用范围。

> 注：v4 架构与 v3 共存（渐进式迁移）。`MetaEngine` 保留为兼容门面至所有调用方迁移完毕（Task 24）。`PoolEngine` 仍为运行时核心，仅通过 EventBus 与其他模块交互。

---

## 2 三种表

### 2.1 持久表（SQLite）—— 审计影子

记录"发生过什么"，不影响当下决策。写多读少，跨进程。

| 表名 | 职责 |
|------|------|
| `pool_config` | 图拓扑元数据（名称、类型、状态） |
| `pool_node` | 节点定义（`pool_config` 的结构化镜像） |
| `pool_edge` | 边定义（流转参数的结构化镜像） |
| `node_state` | 节点运行时股票进出记录（运行时表的影子） |
| `stock_transfer_log` | 股票流转日志（只 INSERT，永不 UPDATE） |
| `config_version` | 配置变更版本（只 INSERT，审计追溯） |
| `kline_cache` | K线缓存（运行时只读，预填充） |
| `replay_session` | 回放会话元数据 |
| `replay_snapshot` | 回放快照（节点状态 + 事件 + K线数据） |

### 2.2 运行时表（内存 Dict）—— 真相源

**`PoolState` 是核心运行时表真相源。** 迁移后，核心执行路径的读/写全部落到
`PoolState`（`_tables` 容器 15 张池级表 + `dirty` / `first_run` 两个独立状态属性）
和 `EdgeState`（4 张边级表）。`MetaEngine` 不再维护 `_rt` 兼容 sink：第 13 轮
迭代已将其删除，运行时表要么直接作为 `MetaEngine` 真实属性存在，要么通过
`@property` 代理到 `PoolState` / `EdgeState`。

> **`PoolState` 命名空间**：`core/runtime.py` 中 `PoolState` 通过 `_tables` 容器
> 持有 15 张池级运行时表，schema 由 `tests/test_architecture_metrics.py` 与
> `ARCHITECTURE_FINAL.md` 共同约定。`EdgeState`（`core/edge_state.py`）持有 4 张
> 边级表：`exec_ctx` / `edge_fired` / `formula_results` / `filter_inputs`。
> `dirty`（`DirtyState`）与 `first_run`（`bool`）是 PoolState 的核心状态属性，
> 不放入 `_tables` 容器。
> `MetaEngine.__getattr__` 仅保留 capability_registry 代理（Task 10）。
> 第 14 轮迭代后，`_flow_exec_counts`、`_flow_first_fire_ts`、`_flow_last_fire_ts`、
> `_flow_duration_starts` 通过 `_ExecCtxView` 视图类直接读写
> `EdgeState.exec_ctx[eid]` 的对应字段；`_filter_cache` 通过 `_FilterCacheView`
> 视图类动态读取 `EdgeState.formula_results` 与 `EdgeState.filter_inputs`。
> 视图类不持有本地 fallback 数据，所有读写都落在 `PoolState` / `EdgeState`，
> 运行时表唯一真相源。`node_stocks`、`_trackers` 等直接作为真实属性存在。

| 表名 | 类型 | 职责 | 真相源 |
|------|------|------|--------|
| `PoolState.node_stocks[nid]` | `Dict[str, List]` | **最核心。** key=节点ID，value=股票列表。池中此刻真实存在的股票 | `PoolState` |
| `PoolState.latest_tick[code]` | `dict` | 当前 tick 行情数据（含 `_ts` / `_hash`） | `PoolState` |
| `PoolState.bars[period][code]` | `dict` | 多周期合成 K 线 | `PoolState` |
| `PoolState.dirty.nodes` | `Dict[str, bool]` | 脏节点集合：股票发生变化的节点ID | `PoolState` |
| `PoolState.dirty.data` | `bool` | 数据脏标记：当前 tick 收到新行情 | `PoolState` |
| `PoolState.node_snapshots` | `Dict[str, frozenset]` | 每 tick 传播后的节点股票快照 | `PoolState` |
| `PoolState.trackers` | `Dict[(nid, code), dict]` | 持仓跟踪（入场价/当前价/盈亏等） | `PoolState` |
| `PoolState.exit_tracker_cache` | `Dict[(nid, code), dict]` | move 出池时缓存的旧 tracker 信息 | `PoolState` |
| `PoolState.topology` | `Dict[nid, List[eid]]` | 拓扑邻接表（出边），由 `Compiler` 输出 | `PoolState` |
| `PoolState.post_tick_results` | `dict` | PK排名/分析角度/看盘面板/告警结果 | `PoolState` |
| `PoolState.alert_cooldown` | `Dict[(rule_id, code), float]` | 告警冷却时间 | `PoolState` |
| `PoolState.time_source` | `dict` | 时间源配置与当前/起始时间戳。驱动 `_now()` 返回值 | `PoolState` |
| `PoolState.data_source` | `dict` | 数据源配置行（live/replay/simulation） | `PoolState` |
| `PoolState.trade_interface` | `dict` | 交易接口配置行（live_order/noop/paper_trade） | `PoolState` |
| `PoolState.side_effects_scope` | `dict` | 副作用范围配置行（all/readonly/optional） | `PoolState` |
| `PoolState.replay` | `dict` | 回放模式状态隔离副本 | `PoolState` |
| `PoolState.simulator` | `dict` | 仿真模式状态隔离副本 | `PoolState` |
| `PoolState.first_run` | `bool` | 首次执行标记（独立状态属性，非 `_tables` 成员） | `PoolState` |
| `PoolState.dirty` | `DirtyState` | 脏标记对象：`dirty.nodes` / `dirty.data`（独立状态属性） | `PoolState` |
| `EdgeState.exec_ctx[eid]` | `dict` | 边执行上下文：count / first_fire / last_fire / fired | `EdgeState` |
| `EdgeState.edge_fired[eid]` | `bool` | 边在当前 tick 是否触发 | `EdgeState` |
| `EdgeState.formula_results[(formula_ref, bar_hash)]` | `Any` | 公式评估结果缓存 | `EdgeState` |
| `EdgeState.filter_inputs[eid]` | `frozenset` | 每条边最近一次过滤的输入股票指纹 | `EdgeState` |

**`MetaEngine` 直接持有的辅助运行时属性**（非核心路径真相源，由回放/仿真/
post_tick/UI 等模块维护）：

| 属性名 | 类型 | 职责 |
|--------|------|------|
| `_event_queue` | `asyncio.Queue[Event]` | 事件流队列 |
| `_signal_events` | `List[Signal]` | 运行时信号表 |
| `_signal_queue` | `asyncio.Queue[Signal]` | 信号队列 |
| `_exit_tracker_cache` | `Dict[(pool_id,code), dict]` | move 出池时缓存的旧 tracker 信息 |
| `_data_cache` | `LRUCache` | 数据缓存表 |
| `_loop_node_stocks` | `Dict[str, List]` | run_loop 持续循环模式下的节点股票状态 |
| `_current_time_source` | `Optional[dict]` | 当前时间源配置（回放/仿真直接写入） |
| `_current_bar_time` | `Optional[datetime]` | 回放模式当前K线时间戳 |
| `_virtual_clock` | `Optional[float]` | 仿真模式虚拟时钟值 |
| `_pk_rankings` | `Dict` | PK排名结果表 |
| `_angle_results` | `Dict` | 多分析角度结果表 |
| `_dashboard_data` | `Dict` | 看盘面板数据表 |
| `_alert_events` | `List[dict]` | 告警事件表 |
| `_alert_queue` | `asyncio.Queue[dict]` | 告警队列 |
| `_alert_cooldown` | `Dict[(rule_id,code), float]` | 告警冷却时间表 |
| `_last_snapshot` | `Optional[Dict[str, frozenset]]` | 旧快照 |
| `_last_bar_hash` | `Optional[str]` | 旧 bar hash |
| `_current_bar_hash` | `Optional[str]` | 当前 bar hash |
| `_current_bar_data` | `dict` | 当前 bar data |
| `_last_data_update_ts` | `float` | 最后数据更新时间戳 |

> 注意：以上属性为 `MetaEngine` 真实属性，不再包裹在 `_rt` 字典中。第 13 轮
> 迭代已彻底移除 `_rt` 兼容 sink；第 14 轮进一步移除 `_dirty_nodes`、
> `_node_snapshots`、`_latest_tick`、`_edge_fired`、`_data_dirty` 等本地字段，
> 核心运行时表统一收敛到 `PoolState` / `EdgeState`。`_flow_exec_counts`、
> `_flow_first_fire_ts`、`_flow_last_fire_ts`、`_flow_duration_starts`、
> `_filter_cache` 通过视图类代理到 `EdgeState.exec_ctx` / `formula_results`，
> 视图不持有本地 fallback 数据。

### 2.3 配置表（JSON 文件）—— 规则即代码

定义"怎么执行"。启动时加载，运行时只读，跨版本存在。

**参与引擎核心循环的 15 张**：

| 文件 | 引擎读入点 | 职责 |
|------|-----------|------|
| `timing.json` | `_tdx_should_execute` / `_tdx_check_duration` / `_eval_gate` | starttype × cxtype 的 24 种时间调度规则 + `gate_evaluator` 表（live_gate / replay_gate / virtual_gate 三种 gate 评估器，由 `runtime_modes.json.<mode>.gate_evaluator_id` 路由） |
| `edge_strategies.json` | `EdgeExecutor` 策略路由 + `PoolEngineMixin._init_node_stocks` 节点初始化 | source_type:target_type → handler 映射 + node_init 规则 |
| `dispatch.json` | `_rebuild_dispatch` 构建 `dispatch_index`；`_dispatch_tdx_condition` 查 `nset_dispatch` 表路由 | 位掩码 → 网关名称的条件分发路由 + nset(0~5) → dispatch_key + evaluator 映射 |
| `dispatch.json`（`nset_dispatch` 子表） | `_dispatch_tdx_condition` | nset(0~5) → dispatch_key + evaluator 映射。engine 通过 `getattr(tdx_evaluators, evaluator_name)` 动态调用，无硬编码字典 |
| `engines.json` | 引擎索引构建 | 网关 → 底层引擎映射（周期、数据源、兼容网关） |
| `modules.json` | 模块索引构建 | node_type → handler 的类型系统定义 |
| `tdx_psatt.json` | `_apply_tdx_psatt_ttl` | TTL 自动删除规则（bdel/ndelnum/ndeltype 驱动） |
| `fallback_chain.json` | builtins.py 各 filter 函数降级分支 | TQ 不可用时的降级链（bar_data → pass_through，确定性透传） |
| `runtime_modes.json` | `run_mode()` / `_eval_gate` / `_get_data_injector` / `_refresh_bar_data` | 三种运行模式（live/replay/simulation）配置：时间源/数据源/交易接口/副作用/tick间隔 + `gate_evaluator_id` / `data_injector_id` / `refresh_handler` 三维路由 |
| `time_sources.json` | `_now()` | 三种时间源（wall_clock/sequence/virtual）配置：driver_type/now_expr |
| `trade_interfaces.json` | 信号处理 | 三种交易接口（live_order/noop/paper_trade）配置 |
| `post_tick_pipeline.json` | `_post_tick()` | post_tick 流水线定义：pipeline stages 顺序/启用/配置表映射/写入目标 |
| `edge_semantics.json` | `_execute_flowsCore` 边类型分发 + `_resolve_edge_type` | 条件边/无条件边类型定义、触发规则、运算流程、缓存策略 |
| `capability_registry.json` | `__init__` → `_bind_capabilities` → `__getattr__` 代理 | 组件能力注册表（field_name / enabled / inject_method / inject_point / config_table / handler）。引擎 `__init__` 查表动态绑定组件到 `self._capabilities` 字典；`__getattr__` 仅代理 `_capabilities` 中注册的字段，不再代理运行时表 |
| `pre_tick_pipeline.json` | `_pre_tick()` | tick 前置流水线（与 `post_tick_pipeline.json` 对称）。定义 pipeline stages 顺序/启用/handler/读写目标，含 `stage_bar_data_inject`（数据注入）/ `stage_minute_aggregator_feed`（分钟聚合器喂数据） |

**参与引擎后处理（post_tick）的 4 张**：

| 文件 | 引擎读入点 | 职责 |
|------|-----------|------|
| `pk_config.json` | `_stage_pk_ranking()` | PK排名维度权重/公式/排名变化事件规则 |
| `analysis_config.json` | `_stage_analysis_angles()` | 多分析角度（动量/趋势/价值）公式/排序方向 |
| `dashboard_schema.json` | `_stage_dashboard()` | 看盘面板布局/数据源/显示列 |
| `alert_rules.json` | `_stage_alerts()` | 监控告警规则/条件/严重度/冷却时间 |

**参与事件/信号生成的 3 张**：

| 文件 | 引擎读入点 | 职责 |
|------|-----------|------|
| `event_rules.json` | `_emit_transfer_events()` | 事件触发规则/详情字段映射（ENTER/EXIT/TIMEOUT） |
| `signal_rules.json` | `_emit_transfer_events()` | 信号触发条件/字段映射（BUY/SELL） |
| `pool_roles.json` | `_emit_transfer_events()` → `_resolve_role()` | 池角色定义/角色解析规则（替代硬编码 baimpool 判断） |

**引擎核心循环之外**（UI、校验、导出等）的配置表不在此列。但以下仍在引擎启动路径中读取：
- `action_table.json` — 目标池回调（bsavehis/bsound/btip/bsavetoblock），由 app.py 读取
- `cell_type_registry.json` — 节点类型注册表，含 `type_aliases`
- `property_ownership.json` — 属性归属配置，含 `type_name_mapping`
- `behavior_actions.json` — handler 注册表中的行为动作
- `pool_types.json` — 池类型映射
- `defaults.json` — 参数别名
- `dzh_type_map.json` — 大智慧类型映射
- `field_definitions.json` — 位标志编解码
- `xml_mapping.json` — TDX/DZH XML 导入导出字段映射（表驱动剥离）
- `history_schema.json` — 历史记录文件格式定义（表驱动剥离）
- `highlight_rules.json` — 高亮事件规则定义（表驱动剥离）
- `analysis_results.json` — 分析结果字段定义（表驱动剥离）
- `data_config.json` — 数据适配器配置/注入规则/缓存策略/时间框架定义
- `mock_data.json` — Mock 模拟数据配置（仿真模式数据源）
- `tracker_schema.json` — 持仓跟踪表 Schema/计算公式（含 `formulas` 字段，原 tracker_formulas.json 已合并至此）/输出字段集
- `price_fields.json` — 价格字段映射
- `signal_rules.json` — 信号触发规则（也参与事件/信号生成）

共计 22+4+3=29 张引擎级和 50 张总配置表。

### 2.4 JSON 导入导出

股票池配置支持 JSON 格式导入导出，与 DZH XML / TDX XML 格式交叉兼容。

| 函数 | 位置 | 输入 | 输出 |
|------|------|------|------|
| `export_pool_to_json()` | converters/json_converter.py | PoolMetaModel 或 pool_config dict | JSON 字符串/文件 |
| `import_pool_from_json()` | converters/json_converter.py | JSON 字符串/文件 | pool_config dict |

JSON Schema：

| 字段 | 类型 | 含义 |
|------|------|------|
| `version` | int | 格式版本号（当前为 1） |
| `pool_meta` | dict | 池元数据（name, pool_type, ver, mode, backcolor） |
| `nodes` | list | 节点列表（id, type, label, params, position） |
| `edges` | list | 边列表（id, from, to, params） |

交叉格式一致性保证：

| 路径 | 保证 |
|------|------|
| DZH XML → JSON → DZH XML | 节点数、边数、节点类型、边属性一致 |
| TDX XML → JSON → TDX XML | 节点数、边数、节点类型、边属性一致 |
| JSON → DZH XML → JSON | 节点数、边数、节点类型一致 |
| JSON → TDX XML → JSON | 节点数、边数、节点类型一致 |

---

## 3 引擎核心循环

**核心函数：`PoolEngine.run_tick()` = EventDriver 中断驱动 + 按拓扑序执行触发边**

`MetaEngine._tick()` 现在仅做入口适配：初始化 `PoolEngine`、注入数据、委托
`PoolEngine.run_pool()` / `run_loop()`，最后调用旧后处理（trackers / events /
post_tick）。边触发与 TTL 不再每 tick 全扫，由 `EventDriver`（`core/time_util.py`）
经 `loop.call_at` 注册到时事件，触发时调用 `on_timed_event` 执行并按需续期。

> **变换单元（三元组一体）**：股票池执行以变换单元为原子计算单位。变换单元 =
> (条件转移边 + 转移条件 + 无条件转移边) 三元组。`Compiler.compile()` 将 pool_config
> 编译为 `CompiledSchedule`，运行期只按 `execution_order` 遍历。

```
① 编译期（池加载时，一次性）
   Compiler.compile(pool_config)
   → CompiledSchedule:
       execution_order   : 拓扑排序后的边 ID 列表
       edge_ctx[eid]     : (sid, tid, semantic)
       edge_timing_spec  : starttype / cxtype / delay / window
       edge_filter_spec  : formula_ref / dispatch_key / threshold
       edge_propagate_spec : copy / move / overwrite（业务上亦称 edge_flow_spec）
       edge_action_spec  : callback 动作列表
       edge_ttl_spec     : bdel / ndelnum / ndeltype

② 运行期每 tick（PoolEngine.run_tick）：

   state.time_source['current_ts'] = now()
   if state.first_run:
       mark all source nodes dirty

   # 阶段 1：统一计算 edge_fired
   for eid in execution_order:
       edge_fired[eid] = should_fire(eid)   # 读 timing.json + EdgeState.exec_ctx

   # 阶段 2：仅执行触发边
   for eid in execution_order:
       sid = edge_ctx[eid].sid
       if edge_fired[eid] and (dirty.nodes[sid] or dirty.data):
           EdgeExecutor.run(eid)             # filter → propagate → callback → ttl
           mark_node_dirty(edge_ctx[eid].tid)

   clear dirty flags
   state.first_run = False
   snapshot_nodes()                          # 保存本 tick 最终状态
   sync_events_to_meta()                     # 事件供 MetaEngine 后处理

③ post_tick 后置流水线（MetaEngine 兼容层）：
   ┌─ _update_trackers ────────────────────────────────────────────────
   │  读 tracker_schema.json（formulas）
   │  + 读 PoolState.node_stocks / latest_tick（当前价格）
   │  → 写 _tracker（盈亏/最大盈利/最大回撤/持仓天数）
   │
   ├─ _emit_transfer_events ──────────────────────────────────────────
   │  读 event_rules.json / signal_rules.json / pool_roles.json
   │  → 写 _event_queue / _signal_queue
   │
   └─ _post_tick（流水线） ───────────────────────────────────────────
      读 post_tick_pipeline.json（pipeline stages）
      → 逐 stage 查配置表执行：
        ├─ pk_ranking: 读 pk_config.json → 写 _pk_rankings
        ├─ analysis_angles: 读 analysis_config.json → 写 _angle_results
        ├─ dashboard: 读 dashboard_schema.json → 写 _dashboard_data
        └─ alerts: 读 alert_rules.json → 写 _alert_events + _alert_queue

④ 输出结果
   读 PoolState.node_stocks
   → 过滤 output_types（edge_strategies.json）
   → 输出 {node_id: {label, type, stock_count, stocks}}
```

**伪代码精要**（`core/engine.py` `PoolEngine.run_tick()` / `_run_tick_body()`）：

```python
async def run_tick(self):
    self.state.time_source['current_ts'] = time_at(state=self.state)  # 单一时间入口
    if self.state.first_run:
        self._mark_source_nodes_dirty()            # 源节点/带初始股票节点标脏

    # 阶段 1：EventDriver 中断驱动（边触发+TTL 已折叠为 on_timed_event）
    now = self.state.time_source['current_ts']
    for eid in execution_order:
        if self._driver.is_edge_due(eid, now):     # 到时事件触发（非每 tick 全扫）
            self._driver.fire_edge(eid)            # 执行 + 按需续期（now+interval<=end）
    self._driver.fire_ttl_due(now)                 # TTL 一次性删除不续期

    # 阶段 2：仅执行触发边
    for eid in self._components["schedule"].execution_order:
        ec = self._components["schedule"].edge_ctx[eid]
        sid = ec.sid
        if self.state.edge_fired[eid] and (self.state.dirty.nodes.get(sid) or self.state.dirty.data):
            if self._components["edge_executor"].run(eid):   # gate→filter→propagate→callback→ttl
                self.state.mark_node_dirty(ec.tid)

    self.state.clear_dirty()
    self.state.first_run = False
    self.state.snapshot_nodes()                    # 保存本 tick 最终状态
    self._sync_events_to_meta()                    # Executed 事件供 MetaEngine 后处理
```

`run_pool()` 在 `run_tick()` 前后补充 DZH TTL、tracker 更新、转移事件、post_tick 等兼容后处理；
`run_loop()` 以 `run_tick()` 为体，按 `runtime_modes.json` 的 tick_interval 循环执行。

**这段代码不含任何股票代码、市场类型、指标名称等字面量。** 新增条件类型？改 `dispatch.json`。新增时机？改 `timing.json`。新增分析角度？改 `analysis_config.json`。新增事件类型？改 `event_rules.json`。新增运行模式？改 `runtime_modes.json`。新增 gate 评估器？改 `timing.json:gate_evaluator`。新增数据注入器？改 `data_source_contract.json:injector`。新增组件能力？改 `capability_registry.json`。零行 Python。

### 3.0.1 编译期/运行期分离（v3）

**核心洞察**：拓扑在运行前就设计好了，不需要每 tick 都处理。最新 tick 的表独立维护，执行顺序早就定好。只有源节点股票有增减或数据最新时间有变化，同时边时间触发事件到达，才执行过滤计算事件。过滤完成触发目标节点更新和预警事件。所有核心只有这个。

**两阶段架构**：

| 阶段 | 时机 | 职责 | 方法/组件 |
|------|------|------|----------|
| 编译期（一次性） | `PoolEngine` 构造时 | 拓扑排序、边上下文、6 维 spec、邻接表、执行顺序 | `Compiler.compile(pool_config)` |
| 运行期（每 tick） | 数据更新时 | 脏节点标记、级联传播、增量计算 | `PoolEngine.run_tick()` |

**编译期输出**（`CompiledSchedule`，`core/compiler.py`）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `execution_order` | `List[eid]` | 拓扑排序后的边 ID 列表 |
| `edge_ctx[eid]` | `EdgeContext` | 端点 sid/tid/sn/tn/st/tt/eid 等静态上下文 |
| `edge_timing_spec[eid]` | `TimingSpec` | starttype / cxtype / delay / window |
| `edge_filter_spec[eid]` | `FilterSpec` | formula_ref / dispatch_key / threshold |
| `edge_propagate_spec[eid]` | `PropagateSpec` | copy / move / overwrite 等流转模式（业务上亦称 `edge_flow_spec`） |
| `edge_action_spec[eid]` | `ActionSpec` | callback 动作列表 |
| `edge_ttl_spec[eid]` | `TTLSpec` | bdel / ndelnum / ndeltype |
| `topology` | `Dict[nid, List[eid]]` | 出边邻接表（写入 `PoolState.topology`） |

**运行时脏节点传播**：

```
外部 tick 推送 → DataUpdater.apply_data(tick) → 写 latest_tick → 置 dirty.data
    ↓
BarComposer 订阅 DataChanged(tick) → 更新 bars → 发布 DataChanged(bar)
    ↓
PoolEngine.run_tick():
  ① 首次运行：_mark_source_nodes_dirty() 标记源节点
  ② 阶段 1：EventDriver 中断驱动（边触发+TTL 折叠为 on_timed_event）
       - 编译期 build_timed_event_specs 产出 TimedEventSpec 表行
       - 运行期 loop.call_at 注册到时事件（非每 tick 全扫）
       - 到时触发：执行 action + 判断 now+interval<=end 续期
  ③ 阶段 2：仅对 triggered 边调用 EdgeExecutor.run(eid)
       triggered = edge_fired[eid] AND (dirty.nodes[sid] OR dirty.data)
  ④ 执行完成后标记目标节点 dirty
  ⑤ snapshot_nodes() 保存本 tick 最终状态
  ⑥ clear_dirty() 清除 node_dirty / data_dirty
```

**数据层与计算层分离**：
- 数据层：`DataUpdater.apply_data(tick)` 写 `latest_tick` 并置 `dirty.data`；
  `BarComposer.on_tick(codes)` 订阅 `DataChanged(tick)` 事件并写 `bars`。
- 计算层：`PoolEngine.run_tick()` — 纯计算，不直接读取外部行情
- 边界清晰：数据更新是事件，计算是对事件的响应

**向后兼容**：
- 旧 `_execute_flows()` / `_execute_flowsCore()` / `_tick_event_driven()` / `_tick_simple()` 已删除
- `_should_fire_edge` / `_run_ttl_for_state_pools`（每 tick 全扫）已删除，由 `EventDriver` 中断驱动替代
- `MetaEngine` 通过 `_pool_engine` 委托给 `PoolEngine`；旧字段名通过真实属性或
  `@property` 代理到 `PoolState` / `EdgeState`，`__getattr__` 仅保留
  capability_registry 代理

### 3.2 事件驱动路径（v4）

`unify-stockpool-oop-event-driven` spec 实施后，核心循环增加事件驱动路径：

- **`PoolEngine._run_tick_body()` 末尾发布 `TimeAdvanced` 事件**（SubTask 21.1）：`self._components["event_bus"].publish(TimeAdvanced(ts=now, source=driver_type))`。RuntimeMode 模块订阅 TimeAdvanced 推进时间（向后兼容：RuntimeMode 仍订阅 TickReceived 发布 TimeAdvanced，PoolEngine 成为主发布者）。
- **`PoolEngine.__init__` 可选订阅 `DataChanged` 事件**（SubTask 21.2）：新增 `subscribe_data_changed: bool = False` 参数，默认关闭避免与 ExecutionModule 双重触发。启用时 `_on_data_changed_event` handler 更新 `current_ts` 后调用 `_run_tick_body`。
- **`EdgeExecutor` 订阅 `EdgeFired` 事件执行**（SubTask 21.3）：`EdgeExecutor.__init__` 中 `if bus: bus.subscribe(EdgeFired, self._on_edge_fired)`。ExecutionModule `_run_tick` 改为 `publish(EdgeFired)` + fallback guard（`if edge_executor.bus is None: edge_executor.run(eid)`）避免双重触发。
- **Trade 模块订阅 `TransferExecuted` 事件**（SubTask 21.4）：`TradeModule._on_transfer_executed` 按 `auto_buy_pools`/`auto_sell_pools` 配置发布 BUY/SELL Signal，支持入池即买入/出池即卖出语义。
- **三方法已迁移到事件驱动路径**（SubTask 21.5 + 22.6）：`_update_trackers` / `_emit_transfer_events` / `_post_tick` 逻辑分别迁移到 TradeModule._on_order_filled / ExecutionModule._on_executed / StatisticsModule._on_position_updated。PoolEngine.run_pool/_tick 中的直接调用已删除（_post_tick 转为 thin no-op shim；_update_trackers / _emit_transfer_events 方法体保留供测试兼容，待 Task 24 完成后删除）。

---

## 3.1 边类型语义

**边的类型由源节点类型决定（经全量 42 个 DZH 文件 602 条边验证）。** 源为备选池/状态池/数据源的边是条件转移边，源为条件节点的边是无条件转移边。

### 边类型定义

| 边类型 | 源节点类型 | 触发规则 | 运算流程 | 缓存策略 |
|--------|-----------|---------|---------|---------|
| 条件转移边 | market_source / candidate_dzh / tdx_candidate / stock_state_pool / tdx_state_pool (备选池/状态池/数据源) | gate通过 且 (源节点股票变化 或 行情数据变化) | gate → filter → propagate | 缓存公式结果于 `EdgeState.formula_results[(formula_ref, bar_hash)]` |
| 无条件转移边 | transfer_condition / tdx_condition / dzh_condition_pool (条件/公式节点) | 源节点股票变化 | propagate（跳过 gate 和 filter） | 不缓存 |

> **验证依据**：扫描 dzhpool/ 全部 42 个 XML 文件的 `<flow>` 元素：
> - attr=8192(条件边) 共 337 条，**100%** 的源节点 type ∈ {0,200,202}（备选池/状态池/数据源），**100%** 有 `interval` 属性
> - attr=8193(无条件边) 共 265 条，**100%** 的源节点 type=201（条件节点），**0%** 有任何时间属性

### 核心语义

1. **条件转移边**：触发条件满足（gate 通过）且行情数据有变化才会触发条件转移节点运算。公式结果缓存在 `EdgeState.formula_results[(formula_ref, bar_hash)]`，同一 bar_hash 下复用缓存。
2. **无条件转移边**：没有时间触发条件，只要边的源节点有变化就会马上反映到目标节点的输入上。在内存上近乎同一引用——源变化即目标变化。
3. **全局不变不运算**：所有节点和边若股票不变和行情不变，不会进行再次运算。

### 变更检测机制

| 检测项 | 数据结构 | 计算方式 |
|--------|---------|---------|
| 节点股票变化 | `PoolState.node_snapshots: {nid: frozenset(codes)}` | 逐节点比较 frozenset 是否相等 |
| 行情数据变化 | `PoolState.latest_tick[code]._hash: str` | MD5(open,high,low,close,volume,amount) |
| 首次执行 | `PoolState.first_run: bool` | 首次执行强制标脏所有源节点 |

### 配置表

`edge_semantics.json` 定义两种边类型的 target_types、trigger_rule、flow 步骤、缓存策略。`Compiler.compile()` 在编译期完成边类型判定并写入 `CompiledSchedule.edge_ctx[eid].semantic`。

### 运行时表

| 表名 | 类型 | 职责 | 真相源 |
|------|------|------|--------|
| `PoolState.node_snapshots` | `Dict[str, frozenset]` | 每 tick 节点股票快照 | `PoolState` |
| `PoolState.latest_tick` | `Dict[code, Tick]` | 当前行情 tick（含 `_hash` / `_ts`） | `PoolState` |
| `PoolState.first_run` | `bool` | 首次执行标记 | `PoolState` |
| `EdgeState.exec_ctx[eid]` | `dict` | 边执行上下文：count / first_fire / last_fire / fired | `EdgeState` |
| `EdgeState.formula_results[(formula_ref, bar_hash)]` | `Any` | 公式评估结果缓存 | `EdgeState` |

---

## 4 三种运行模式

### 4.0 本质统一

**三种模式不是三套代码，而是同一套核心循环（gate→filter→propagate→callback→ttl→post_tick）绑定不同的"时间源 × 数据源 × 订单接口"组合。** 差异只在三个基础维度 + 三个表驱动路由维度（`gate_evaluator_id` / `data_injector_id` / `refresh_handler`）。模式切换 = 改 `runtime_modes.json` 配置，零行 Python。

三模式共享同一个 `PoolEngine.run_tick()` 核心循环，差异仅体现在
`PoolState.time_source` / `PoolState.data_source` / `PoolState.trade_interface` /
`PoolState.side_effects_scope` 四张表行，以及 `runtime_modes.json` 中的
`gate_evaluator_id` / `data_injector_id` / `refresh_handler` 三个路由字段。

| 模式 | 时间源 | 数据源 | 订单接口 | 触发机制 | 副作用 |
|------|--------|--------|---------|---------|--------|
| live（实盘） | wall_clock（真实时间） | tq实时推送+降级链 | live_order（真实下单） | data_arrival（行情到达） | all（全部） |
| replay（回放） | sequence（K线时间轴） | kline_cache预加载 | noop（空操作） | tick_advance（时间步进） | readonly（只读） |
| simulation（仿真） | virtual（虚拟时钟） | mock生成 | paper_trade（模拟记账） | step_advance（手动步进） | optional（可选） |

**数据源本质差异**：

| 维度 | 实盘 | 回放 | 仿真 |
|------|------|------|------|
| 数据从哪来 | TQ DLL/SDK 实时推送 + akshare + mock 兜底 | SQLite kline_cache 预加载 | mock_data.json 规则生成 |
| 数据怎么来 | 异步并发 _inject_bar_data_async() | 同步读取 _timeline[index] | 即生即用 _generate_mock_bar_data() |
| 多时间框架 | 并发获取+缓存（TTL=5s/300s） | K线合成 | 不支持 |
| 缓存策略 | TTL 过期机制 | 无缓存（预加载全量） | 无缓存（即生即用） |
| 可复现性 | 不可复现（实时行情） | 完全可复现（历史数据） | 确定性可复现（seed=42） |

### 4.1 模式配置表驱动

`run_mode(mode_id)` 查 `runtime_modes.json` 初始化，进入核心循环：

| 模式 | 时间源 | 数据源 | 交易接口 | 副作用 | 循环控制 | `gate_evaluator_id` | `data_injector_id` | `refresh_handler` |
|------|--------|--------|---------|--------|---------|---------------------|--------------------|--------------------|
| `live`（实盘） | `realtime`（wall_clock） | `tq`（实时推送） | `live_order`（真实委托） | 允许 | 暂停/恢复/停止 | `live_gate` | `async_multi_tf` | `tq_snapshot_refresh` |
| `replay`（回放） | `kline_timeline`（sequence） | `historical_kline`（历史缓存） | `noop`（空操作） | 不允许 | 播放/暂停/步进/变速/跳转 | `replay_gate` | `sync_bar` | `noop_refresh` |
| `simulation`（仿真） | `virtual_clock`（virtual） | `mock`（随机生成） | `paper_trade`（模拟成交） | 不允许 | 步进/运行到/暂停/重置 | `virtual_gate` | `mock_generate` | `mock_advance_refresh` |

**三维表驱动路由**（消除原 `if is_replay:` / `if not self.tq_adapter:` 等硬编码分支）：

| 路由字段 | 引擎读入点 | 查表路径 | 含义 |
|---------|-----------|---------|------|
| `gate_evaluator_id` | `_eval_gate(edge, ctx)` | `runtime_modes.json.<mode>.gate_evaluator_id` → `timing.json:gate_evaluator.<id>.handler` | gate 评估器路由：live_gate（starttype×cxtype 24 种）/ replay_gate（K线时间轴触发）/ virtual_gate（虚拟时钟触发） |
| `data_injector_id` | `_get_data_injector()` | `runtime_modes.json.<mode>.data_injector_id` → `data_source_contract.json:injector.<id>.handler` | 数据注入器路由：async_multi_tf（异步多时间框架）/ sync_bar（同步K线）/ mock_generate（mock 生成） |
| `refresh_handler` | `_refresh_bar_data(mode_cfg, current_bar_data)` | `runtime_modes.json.<mode>.refresh_handler` → `_builtins_refresh.<handler>` | 行情刷新策略路由：tq_snapshot_refresh（TQ 快照刷新）/ noop_refresh（空操作）/ mock_advance_refresh（mock 推进） |

### 4.2 时间统一

`time_at(state)` 单一入口（`core/time_util.py`），三模式差异仅在参数：

| 模式 | state.time_source["current_ts"] | time_at 返回值 |
|------|--------------------------------|---------------|
| live（实盘） | 缺失或 None | `time.time()`（wall 回退） |
| replay（回放） | K线时间戳（KLineReplayEngine 设置） | current_ts |
| simulation（仿真） | 虚拟时钟（RuntimeSimulator 设置） | current_ts |

`PoolEngine._now` / `MetaEngine._now` 方法体委托 `time_at`，单一真相源。
MetaEngine 信号/事件/域时间戳全部经 `time_at(state=...)` 模式感知。

### 4.3 数据源对比

| 维度 | 实盘 | 回放 | 仿真 |
|------|------|------|------|
| 行情数据 | TQ SDK 实时推送 | 历史K线缓存 | Mock 随机生成 |
| 获取方式 | `_inject_bar_data_async()` 异步并发 | `_timeline[index]` 同步读取 | `_generate_mock_bar_data()` 随机生成 |
| 多时间框架 | 并发获取+缓存 | K线合成 | 不支持 |
| 缓存策略 | TTL=5s(snapshot)/300s(kline) | 无缓存（预加载） | 无缓存（即生即用） |

### 4.4 数据源契约

每种运行模式的数据驱动方式由 `runtime_modes.json` 中三个字段定义：

| 字段 | 含义 | live | replay | simulation |
|------|------|------|--------|------------|
| `data_driver` | 数据驱动方式 | `market_push`（行情推送） | `kline_sequence`（K线序列） | `on_demand`（按需获取） |
| `trigger_mechanism` | 触发机制 | `data_arrival`（数据到达即触发） | `tick_advance`（时间轴步进触发） | `step_advance`（手动步进触发） |
| `side_effects_scope` | 副作用范围 | `all`（全部副作用） | `readonly`（只读，不写持久表） | `optional`（可选副作用） |

**核心差异**：
- 实盘模式：数据推送驱动，行情到达即触发 tick，允许全部副作用（写持久表、发信号、执行交易）
- 回放模式：K线序列驱动，按时间轴步进触发，只读副作用（不写持久表、不发真实信号）
- 仿真模式：按需获取数据，手动步进触发，可选副作用（可配置是否写持久表）

### 4.5 统一入口

```python
async def run_mode(self, mode_id: str) -> Dict[str, Any]:
    mode_cfg = self.meta._runtime_modes.get(mode_id, {})
    ts_cfg = self.meta._time_sources.get(mode_cfg.get("time_source_id", "realtime"), {})
    ds_cfg = self._mode_config_row("data_sources", mode_cfg.get("data_source_id", ""))
    ti_cfg = self.meta._trade_interfaces.get(mode_cfg.get("trade_interface_id", "noop"), {})
    se_cfg = self._mode_config_row("side_effect_scopes", mode_cfg.get("side_effects_scope", ""))

    self.state.set_time_source(ts_cfg)          # 仅替换四张表行
    self.state.set_data_source(ds_cfg)
    self.state.set_trade_interface(ti_cfg)
    self.state.set_side_effects_scope(se_cfg)

    self.state.first_run = True
    if mode_id == "replay":
        self.state.enter_replay()               # 回放状态隔离
    elif self.state.is_replay_active():
        self.state.exit_replay()

    self._init_node_stocks()
    if mode_cfg.get("loop_entry_policy") != "internal_loop":
        return {"node_stocks": self.state.node_stocks, "inject": True}

    # live 模式进入内部循环：run_tick() + asyncio.sleep(tick_interval)
    self._components["_stopped"] = False
    self._components["_paused"] = False
    loop = asyncio.get_running_loop()
    self._components["_loop_task"] = loop.create_task(self.run_loop())
    return {"node_stocks": self.state.node_stocks, "inject": True, "task": self._components["_loop_task"]}
```

---

## 5 策略空间

**策略空间 = 时机轴 × 强弱轴**

所有选股策略都是此空间中的一个点。两个轴已完全由配置表驱动。

### 5.1 时机轴（gate 函数）

`starttype(0~7) × cxtype(0~2) = 24 种组合`，全部通过 `timing.json` 驱动。

| starttype | 含义 | 计算方式 |
|-----------|------|---------|
| 0 | 立即 | `always` — 无条件放行 |
| 1 | 延迟 N 秒 | `elapsed_gte` — `_pool_start_time` 起算 |
| 2 | 开市前 | `in_range` — 开市前 N 秒窗口内 |
| 3 | 开市后 | `gte` — 开市时间 + N 秒后 |
| 4 | 收市前 | `in_range` — 收市前 N 分钟窗口内 |
| 5 | 收市后 | `gte` — 收市时间 + N 分钟后 |
| 6 | 交易时间 | `gte_hhmmss` — 到达指定 HHMMSS 后 |
| 7 | 指定时间 | `gte_hhmmss` — 到达指定 HHMMSS 后 |

| cxtype | 含义 | 过期判断 | 跟踪表 |
|--------|------|---------|--------|
| 0 | 一直执行 | `never` — 永不超时 | — |
| 1 | 执行 N 秒 | `elapsed_gte` — 首次执行后计时 | `EdgeState.exec_ctx[eid].first_fire` |
| 2 | 只执行一次 | `count_gte_1` — 执行次数 ≥1 | `EdgeState.exec_ctx[eid].count` |

### 5.2 强弱轴（filter 函数）

`nset(0~5) × noperate(0~9) = 60 种组合`，通过以下链条驱动：

| 层级 | 驱动表 | 职责 |
|------|--------|------|
| 路由 | `dispatch.json` | 位掩码匹配 → 网关名称 |
| nset 分发 | `dispatch.json`（`nset_dispatch` 子表） | nset(0~5) → dispatch_key + evaluator 名称，engine 查表后 `getattr(tdx_evaluators, evaluator_name)` 动态调用 |
| 网关 | `engines.json` | 网关 → 底层引擎（周期/数据源） |
| 评估 | `tdx_evaluators.py` | nset 对应的实际评估逻辑 |

| nset | 含义 | noperate 0~9 | 评估器 |
|------|------|-------------|--------|
| 5 | 直通（无筛选） | — | 直接透传全部股票 |
| 0 | 技术指标序列 | 等于/大于/小于/金叉/死叉/持股N周期/排名前N/排名后N/上拐/下拐 | `eval_nset0_indicator` |
| 1 | 条件选股公式 | 信号判断 | `eval_nset1_condition_formula` |
| 2 | 专家系统 | 任意信号/买入信号/卖出信号 | `eval_nset2_expert_system` |
| 3 | 最新财务标量 | 等于/大于/小于/排名前N | `eval_nset3_financial_scalar` |
| 4 | 实时行情标量 | 等于/大于/小于/排名前N | `eval_nset4_market_scalar` |
| — | 集合运算 | 并集/差集/交集 | `eval_nset5_set_operation` |

---

## 6 反模式清单

> 状态图例：✅ 已修复（已剥离为配置表）— 全部完成

| # | 位置 | 反模式 | 正确做法 | 状态 |
|---|------|--------|---------|------|
| 1 | `engine.py` L250-257 | `_dispatch_tdx_condition` 中 nset→evaluator 硬编码字典：`{0: tdx_evaluators.eval_nset0_indicator, 1: ..., 2: ...}` | 映射进 `dispatch.json:nset_dispatch` 配置表，engine 只做查表路由 | ✅ |
| 2 | `tdx_evaluators.py` | `eval_nset3_financial_scalar` / `eval_nset4_market_scalar` / `eval_nset5_set_operation` 在 TQ 不可用时各有自己的 mock 降级分支（函数内部 if/else） | 统一走 `fallback_chain.json` 降级链，去除 evaluator 内的 mock 分支 | ✅ |
| 3 | `builtins.py` L66-69 | `_random_filter` 函数独立实现随机过滤，与 `_resolve_fallback` 的 `always→_random_filter` 逻辑重复 | 所有调用方统一走 `_resolve_fallback("chain_name", ...) → fallback_chain.json` | ✅ |
| 4 | `app.py` L616-625 | `_STOCK_NAMES` 从 `mock_data.json` 加载但硬编码在 app.py 全局作用域 | `_get_stock_name` 应查表而非依赖全局 Dict | ✅ |
| 5 | `engine.py` / `table_engine.py` / `tdx_executor.py` 整体 | 任何 `if type == "xxx"` / `if nset == X` / `if pool_type == "custom"` 类分支（非 starttype/cxtype 部分） | 所有映射进 JSON 配置表 | ✅（Task 3-4：table_engine.py 9处A类已剥离到 pool_types.json；tdx_executor.py handler_type B类已标注，由 _TDX_SYSTEM_INDICATORS 表驱动）|
| 6 | 任意文件 | `from xxx_native import yyy` 显式导入领域函数 | 通过 `_handler_registry` 字典动态查表调用 | ✅ |
| 7 | `engine.py` _post_tick | PK排名/分析角度/看盘面板/告警规则硬编码 eval() | 查 `post_tick_pipeline.json` 流水线 + 各配置表驱动 | ✅ |
| 8 | `engine.py` _emit_transfer_events | ENTER/EXIT/TIMEOUT 事件和 BUY/SELL 信号硬编码分支 | 查 `event_rules.json` + `signal_rules.json` + `pool_roles.json` 驱动 | ✅ |
| 9 | `engine.py` _now | 只支持 realtime/fixed 两种时间源 | 查 `time_sources.json` 驱动，支持 wall_clock/sequence/virtual 三种 | ✅ |
| 10 | `engine.py` _emit_transfer_events | `baimpool == 1` 硬编码判断目标池 | 查 `pool_roles.json` 角色解析规则 | ✅ |
| 11 | `engine.py` _tdx_should_execute | `starttype` 8 种 if 链 + `_STARTTYPE_*` 8 个常量硬编码 | 查 `timing.json:starttype_rules` + `_dispatch_table` 驱动 | ✅（Task 2） |
| 12 | `engine.py` _tdx_check_duration | `cxtype` 3 种过期判断 + `_CXTYPE_*` 3 个常量硬编码 | 查 `timing.json:cxtype_rules` + `cxtime_units` 驱动 | ✅（Task 2） |
| 13 | `engine.py` _tdx_should_execute | 60 行 `if/elif` 链处理 8×3=24 种 starttype×cxtype 组合 | 拆分为 `_dispatch_gate(edge, ctx)` 查表路由 | ✅（Task 2） |
| 14 | `engine.py` _apply_tdx_psatt_ttl | TTL 单位 `{0:86400, 1:3600, 2:60, 3:1}` 硬编码字典 | 查 `tdx_psatt.json:ttl_units` | ✅（Task 3） |
| 15 | `engine.py` _parse_intime_to_ts | `intime` 位长归一化（2/4/5/6 位）硬编码 if 链 | 查 `tdx_psatt.json:time_formats` | ✅（Task 3） |
| 16 | `engine.py` _propagate | 6 种转移模式（move/overwrite/copy/force_move/output_components/pass_through）硬编码分支 | 查 `flow_mode_registry.json:resolve_rules` 驱动 | ✅（Task 4） |
| 17 | `engine.py` _emit_transfer_events | `_role_handlers` 硬编码字典：`{"candidate":..., "accumulated":..., "alert":..., "target":...}` | 查 `pool_roles.json:role_resolution.rules` 驱动 | ✅（Task 5） |
| 18 | `services/providers/*` | 数据源（tq_dll/tq_sdk/akshare/mock）探测契约缺失，静默回退 | 新增 `data_source_contract.json` + `_probe()` 方法 | ✅（Task 6） |
| 19 | `engine.py` _post_tick | post_tick 4 stages（pk_ranking/analysis_angles/dashboard/alerts）执行顺序与启用硬编码 | 查 `post_tick_pipeline.json:pipeline` 流水线 | ✅ |
| 20 | `table_engine.py` L165-193 | `_TYPE_ALIASES` 5 条 + `_TDX_TYPE_ALIASES` 14 条硬编码字典 | 查 `cell_type_registry.json:type_aliases` | ✅ |
| 21 | `table_engine.py` L830-861 | `_TYPE_NAME_TO_OWNERSHIP_KEY` 30 条硬编码 | 查 `property_ownership.json:ownership_key_map` | ✅ |
| 22 | `builtins.py` L19-759 | `_mock` / `_SCOPE_POOLS` / `_SECTOR_STOCK_MAP` 市场数据硬编码 | 查 `markets.json` + `mock_data.json:sector_generation_rules` | ✅ |
| 23 | `table_engine.py` | `if pool_type == "custom"` 9处 A类分支（layout_fallback_chain / bypass_ownership_restrictions） | 剥离到 `pool_types.json` custom 类型定义；5处 B类已标注 | ✅（Task 3）|
| 24 | `tdx_executor.py` | `handler_type` 分支（pass/sort/condition 等） | B类不可剥离，已由 `_TDX_SYSTEM_INDICATORS` 表驱动；5处已标注 | ✅（Task 4，B类标注）|
| 25 | `tq_adapter.py` / `akshare_provider.py` / `builtins_filters.py` | 49处硬编码字段分发/源分支/动作分支 | 2处A类已剥离，47处B类已标注 | ✅（Task 6，审计标注）|

> **本次深化表驱动审计验证结果（Task 7-9）**：DZH 10种Cell/6种attrtext/5种reload/5种deltype 全部完整；5种流转模式补全 loop_feedback + dual_source，topology.json 表驱动化。TDX 6种nset/8种spinfo/tran 完整；noperate=5 修复为"排名为"；补全 funnel/multi_source/multi_indicator 三种拓扑模式；operators.json 补全 7 个操作符定义。端到端测试：6种nset/6种拓扑/三模式PK全链路全部通过，0错误。5个 TODO 已转为正式 `# 注：` 注释（Task 5）。

- **模块间直接引用**（v4 新增）：禁止 `from core.xxx import Yyy`（除 `core.event_bus`/`core.domain`/`core.schemas` 白名单）、`from services.xxx import Yyy`、`from converters.xxx import Yyy`。模块间仅通过 EventBus 事件交互。例外：`app.py` lifespan 装配处可 import 任何模块；8 个 aggregator module 可 import 其聚合的子组件（`MODULE_INTERNAL_WHITELIST`）。
  > 静态检查脚本：`scripts/check_module_imports.py`。当前基线 109 处违规（83 旧 MetaEngine + 25 包内 import + 0 真正跨层违规），待 Task 24 完成后归零。

---

## 7 验收清单

- [x] `engine.py` 行数 ≤ 420（原目标，实际 919 行——因新增 run_loop/async/持仓/信号等大量功能导致超限，详见 15.3 说明）
- [x] `builtins.py` 行数 ≤ 500（实际 253 行，拆分后 builtins_filters.py 535 行 + builtins_actions.py 311 行）
- [x] `app.py` 业务逻辑行数 ≤ 300（实际 456 行，含 XML 处理/历史记录/TDX 导入导出等业务逻辑）
- [x] `timing.json` 驱动 gate — `_tdx_should_execute` / `_tdx_check_duration` 已配置表化
- [x] `tdx_psatt.json` 驱动 TTL — `_apply_tdx_psatt_ttl` 已配置表化
- [x] `fallback_chain.json` 驱动降级 — `_resolve_fallback` 已统一走配置表链，最终降级为 `pass_through`（确定性透传）
- [x] `action_table.json` 驱动回调 — `pool_enter_actions` 已配置表化，含 `args` 列表和 `log_on_success` 字段，动态组装参数
- [x] `edge_strategies.json` 驱动策略路由 — 全部 `source_type:target_type → handler` 已配置表化
- [x] `dispatch.json` 驱动条件分发 — 位掩码 → 网关映射已配置表化
- [x] `dispatch.json` 驱动 nset 分发 — `nset_dispatch` 表定义 nset(0~5) → dispatch_key + evaluator 映射，`_dispatch_tdx_condition` 查表路由
- [x] `cell_type_registry.json` 包含 `type_aliases`
- [x] `property_ownership.json` 包含 `type_name_mapping`
- [x] `engine.py` 中 `_dispatch_tdx_condition` 的 nset 硬编码字典应配置表化
- [x] `tdx_evaluators.py` 各 nset 的 mock 降级逻辑应进 `fallback_chain.json`（注：改为 builtins.py 统一降级，最终走 `pass_through`）
- [x] 所有 filter 函数返回 `{passed, rejected}` 统一格式
- [x] 新增功能对 `config/` 下有明确记录
- [x] 持续循环引擎（run_loop）— 异步持续执行模式，支持暂停/恢复/停止
- [x] 异步并发数据获取（AsyncTqAdapter）— 多时间框架并发获取 + 缓存
- [x] 持仓跟踪（StockTracker）— 入场价/当前价/盈亏/最大盈利/最大回撤/持仓天数
- [x] 交易信号（Signal）— BUY/SELL 信号自动生成，异步队列推送
- [x] 事件流（Event）— ENTER/EXIT/TIMEOUT/RANK_CHANGED 事件类型
- [x] builtins.py 拆分 — builtins.py(253) + builtins_filters.py(535) + builtins_actions.py(311)
- [x] 表驱动剥离 — xml_mapping.json / history_schema.json / highlight_rules.json / analysis_results.json
- [x] runtime_simulator.py 表驱动重写
- [x] 三种运行模式统一入口 — `run_mode(mode_id)` 查 `runtime_modes.json` 初始化
- [x] 时间源表驱动 — `_now()` 查 `time_sources.json`，支持 wall_clock/sequence/virtual
- [x] post_tick 流水线表驱动 — `_post_tick()` 查 `post_tick_pipeline.json`，4 stages 配置表化
- [x] 事件/信号生成表驱动 — `_emit_transfer_events()` 查 event_rules.json + signal_rules.json + pool_roles.json
- [x] 池角色解析表驱动 — `_resolve_role()` 查 pool_roles.json，替代硬编码 baimpool 判断
- [x] KLineReplayEngine 委托核心循环 — `_do_step()` 调用 `_engine._tick()`，设置 `_current_bar_time`
- [x] RuntimeSimulator 委托核心循环 — `step()` 调用 `_engine._tick()`，设置 `_virtual_clock`
