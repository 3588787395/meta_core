# RULES — 架构演进红线规则

> 提炼自 `DESIGN0.md`（架构合同）+ `DESIGN.md`（执行流视角）+ 实际代码验证。
> 目的：防止程序优化偏离目标，所有修改必须通过本清单自检。

---

## 一、架构核心（10 条）

1. **程序 = 引擎 + 三种表**：程序由唯一解释器 `PoolEngine`（`core/engine.py`）+ 持久表（SQLite）、运行时表（内存 Dict）、配置表（JSON）三种表构成。引擎不含领域知识，只做读表 → 计算 → 写表。

2. **新增功能 = 加 JSON 条目，零行引擎改动**：新增条件类型改 `dispatch.json`，新增时机改 `timing.json`，新增分析角度改 `analysis_config.json`，新增事件类型改 `event_rules.json`，新增运行模式改 `runtime_modes.json`。禁止在引擎中加 if/elif 分支处理新功能。

3. **MetaEngine 已合并删除**：Task 24 已将 `MetaEngine` 完全合并入 `PoolEngine`，`MetaEngine` 不再存在。禁止重新引入 `MetaEngine` 类或其兼容 shim。`PoolEngine` 是唯一核心引擎。

4. **PoolEngine 核心属性 ≤ 5**：`meta` / `pool_config` / `nodes` / `state` / `_components`。辅助方法集中到 `PoolEngineMixin`。禁止在 `PoolEngine` 上随意添加属性。

5. **PoolState 是运行时表唯一真相源**：15 张池级表收敛到 `_tables` 容器（`node_stocks` / `latest_tick` / `prev_tick` / `bars` / `bars_history` / `node_snapshots` / `topology` / `post_tick_results` / `alert_cooldown` / `time_source` / `data_source` / `trade_interface` / `side_effects_scope` / `replay` / `simulator`），`dirty` 与 `first_run` 是独立状态属性。禁止在 `_tables` 之外维护重复的运行时状态。

6. **EdgeState 仅 3 张边级表**：`exec_ctx` / `formula_results` / `filter_inputs`（位于 `core/domain.py`）。禁止新增第 4 张边级表，禁止用独立 dict 在引擎中维护边执行上下文。

7. **变换单元 = 三元组一体**：（条件转移边 + 转移条件 + 无条件转移边）是原子计算单位。gate/filter/propagate/callback/ttl 五步作用于变换单元而非单条边。

8. **边类型由源节点类型决定**：源为备选池/状态池/数据源（type ∈ {0,200,202}）→ 条件转移边（有 interval）；源为条件节点（type=201）→ 无条件转移边（无时间属性）。此语义经 42 个 DZH 文件 602 条边验证，不可违反。

9. **全局不变不运算**：所有节点和边若股票不变且行情不变，不进行再次运算。变更检测通过 `dirty.nodes` / `dirty.data` / `node_snapshots` frozenset 比较 + `latest_tick[code]._hash` MD5 摘要实现。

10. **触发条件严格为**：`triggered[eid] = edge_fired[eid] AND (dirty.nodes[sid] OR dirty.data)`。首次执行 `first_run=True` 时强制标记所有源节点 dirty。

---

## 二、编译期/运行期分离（5 条）

11. **编译期一次性产出 CompiledSchedule**：`Compiler.compile(pool_config)` 在池加载时一次性产出 `CompiledSchedule`（含 `execution_order` / `edge_ctx` / `edge_timing_spec` / `edge_filter_spec` / `edge_propagate_spec` / `edge_action_spec` / `edge_ttl_spec`）。运行期只按 `execution_order` 遍历，不重新编译。

12. **编译期产出 TimedEventSpec 表行**：边触发 + TTL 折叠为 `TimedEventSpec`，由 `build_timed_event_specs` 在编译期一次性产出。运行期 `EventDriver` 按 heapq 优先队列管理到时事件。

13. **运行期不处理拓扑**：拓扑排序、边上下文、6 维 spec、邻接表在编译期固定。运行期仅处理脏节点标记、级联传播、增量计算。

14. **数据层与计算层分离**：`DataUpdater.apply_data(tick)` 写 `latest_tick` 并置 `dirty.data`；`BarComposer` 订阅 `DataChanged(tick)` 事件写 `bars`；`PoolEngine.run_tick()` 纯计算，不直接读取外部行情。

15. **CompiledSchedule 跨 tick 复用**：pool_config 不变时，拓扑与边规格跨 tick 复用。禁止在 run_tick 中重新编译或修改 CompiledSchedule。

---

## 三、表驱动（8 条）

16. **所有分派用 dict 表驱动，禁止 if/elif 链**：`_TTL_CHECK_HANDLERS` / `_STARTTYPE_GATE_HANDLERS` / `_CXTYPE_POST_GATES` / `_PROPAGATE_STRATEGIES` / `_FILTER_EVALUATORS` / `_ACTION_HANDLERS` 全部 dict 表驱动。新增分派类型 = 加 dict 条目。

17. **nset→evaluator 路由查表**：`dispatch.json:nset_dispatch` 定义 nset(0~5) → dispatch_key + evaluator 映射。引擎通过 `getattr(tdx_evaluators, evaluator_name)` 动态调用，禁止硬编码 nset→evaluator 字典。

18. **starttype×cxtype 24 种组合查表**：`timing.json:starttype_rules` + `cxtype_rules` + `_dispatch_table` 驱动。禁止在引擎中写 60 行 if/elif 处理 8×3 组合。

19. **TTL 单位查表**：`tdx_psatt.json:ttl_units` 定义 {0:86400, 1:3600, 2:60, 3:1}。禁止在引擎中硬编码 TTL 单位字典。

20. **转移模式查表**：`flow_mode_registry.json:resolve_rules` 驱动 move/overwrite/copy/force_move 等 6 种模式。禁止在 `_propagate` 中写 if 分支。

21. **降级链查表**：`fallback_chain.json` 定义 TQ 不可用时的降级路径，最终降级为 `pass_through`（确定性透传）。禁止在各 evaluator 函数内部写 mock 降级分支。

22. **回调动作查表**：`action_table.json:pool_enter_actions` 定义 bsavehis/bsound/btip/bsavetoblock 动作，含 `args` 列表和 `log_on_success` 字段。禁止在 app.py 中硬编码回调分支。

23. **post_tick 流水线查表**：`post_tick_pipeline.json` 定义 pipeline stages 顺序/启用/配置表映射/写入目标。禁止在 `_post_tick` 中硬编码 stage 执行顺序。

---

## 四、事件驱动（7 条）

24. **EventBus 为唯一模块间通信中介**：模块间禁止直接引用（`from core.xxx import Yyy`），仅通过 EventBus 事件交互。白名单仅限 `core.event_bus` / `core.domain` / `core.schemas` / 标准库 / 第三方库。例外：`app.py` lifespan 装配处可 import 任何模块。

25. **tick 执行链 10 类事件按序发布**：TickReceived → DataChanged(tick) → BarComposed → DataChanged(bar) → EdgeFired → FormulaEvaluated → StockFiltered → TransferExecuted → Signal → OrderPlaced+OrderFilled+PositionUpdated → StatisticsUpdated+RankingChanged → AlertRaised+SnapshotUpdated+EventLogged。禁止乱序。

26. **EdgeFired 先于 FormulaEvaluated 和 StockFiltered**：先触发才有公式计算和筛选——时间门打开才触发条件评估，而非计算完才触发边。

27. **changed_codes 增量传递**：EdgeFired 事件携带 `changed_codes`（本周期有 Tick/Bar 更新的股票集合），单事件携带集合参数传递，非每股票单独发事件。筛选器仅对这些股票增量评估公式。

28. **节点独立脏状态**：每个状态池独立维护 `dirty.nodes[nid]` 标记，边触发时根据源节点 dirty 状态决定是否全量/增量评估。

29. **三模式事件化**：ModeChanged 事件驱动 TickBar/Execution/Trade/Database 四模块切换数据源/时间源/交易接口/副作用范围。禁止用 if/else 分支处理模式差异。

30. **EventDriver 用 heapq 优先队列**：到时事件通过 heapq 按 fire_time 排序弹出，tick 调度通过 `loop.call_at` 中断驱动。禁止用 `asyncio.sleep` 轮询检查到时事件。

---

## 五、三模式统一（6 条）

31. **三模式不是三套代码**：live/replay/simulation 共享同一个 `PoolEngine.run_tick()` 核心循环，差异仅在 `PoolState.time_source` / `data_source` / `trade_interface` / `side_effects_scope` 四张表行 + `runtime_modes.json` 中 `gate_evaluator_id` / `data_injector_id` / `refresh_handler` 三个路由字段。

32. **G2 硬约束：仿真/实盘同代码**：仿真模式与实盘模式除 tick 生成逻辑外，其他处理流程必须使用相同代码路径，禁止分别处理。时间源差异仅由 `state.time_source.driver_type` 在 `time_at` 内部决定，不在调用方写 if/else。

33. **时间统一入口 `time_at`**：所有时间戳获取必须通过 `time_at(state=state)`（位于 `core/domain.py`），三模式差异仅在参数。`PoolEngine._now` / 信号/事件/域时间戳全部经 `time_at`。禁止在代码中直接调用 `time.time()` 或 `datetime.now()`。

34. **模式切换 = 改配置表，零行 Python**：新增运行模式改 `runtime_modes.json`，新增时间源改 `time_sources.json`，新增交易接口改 `trade_interfaces.json`，新增副作用范围改 `side_effect_scopes.json`。

35. **_publish_bar_changed 接收 ts 参数**：ts 来源于上游 `DataChanged(tick)` 事件的 `event.ts`，不在此处重复调用 `time_at(state)`。禁止在 `_publish_bar_changed` 中使用 `time_at(state=composer.state)`。

36. **事件 ts 坐标系统一**：后端事件 ts 全部为仿真相对秒（< 1e9）或真实 Unix 秒（≥ 1e9），禁止真实 Unix 秒泄漏到仿真坐标系。前端通过 `normalizeToModeMs()` 归一化到当前模式毫秒坐标系。

---

## 六、前端架构（6 条）

37. **前端仅作展示层**：前端不参与业务真值计算，不持有股票池节点/运行时状态/事件队列真值源。后端持有全部业务真值源。

38. **表驱动 UI 唯一路径**：所有 UI 组件、布局、字段、动作统一由 `config/ui/*.json` 配置表驱动。`_renderPanel()` 通过 `ComponentRegistry.get(field.comp)` 分发，`ToolbarRenderer.renderToolbar()` 按 `config.groups/buttons` 渲染。禁止硬编码节点类型分支或写死菜单条目。

39. **事件驱动唯一路径**：前端事件入口唯一为 `EventSource('/api/events/stream')`，事件格式与 `core/event_bus.py` 事件契约对齐。禁止前端伪造事件、独立维护事件队列真值源。

40. **四模式共享执行路径**：设计/仿真/回放/实盘四种模式共享同一条执行路径，差异仅在于数据源与时间推进机制。

41. **事件面板通过 SSE 订阅**：事件面板通过 SSE/WebSocket 订阅后端 `EventLogged` / `SnapshotUpdated` / `TimerQueued` 等事件，禁止直接访问后端运行时表。

42. **事件面板 9 分类 Y 轴统一**：Tick / Bar / Formula / Edge / Transfer / Signal / Order / TTL / System 九种分类，矩阵与散点视图的 Y 轴语义相同，切换视图仅改变 X-Y 布局，不改变分类编码、颜色、图标与筛选状态。

---

## 七、前端事件面板细节（8 条）

43. **散点视图支持两种布局**：分类显示（按 9 分类分行）和同行显示（全部事件在单行），通过切换按钮切换。两种布局共享相同的标签区 DOM 和 Canvas 绘制函数。

44. **矩阵/散点标签区统一用 DOM**：分类标签由 DOM 元素绘制（支持点击交互、无障碍访问），Canvas 不重复绘制分类标签文字。`drawCategoryLabels` 已删除。

45. **事件面板默认隐藏**：设计/实盘模式 `display:none`，仅仿真/回放模式通过 `.visible` 类显示。退出仿真/回放时正确隐藏。

46. **事件面板默认位置**：固定右下角（`right:16px; bottom:16px`），尺寸 560px×400px，不覆盖顶部工具栏。

47. **每次启动新仿真清除旧事件**：调用 `clearEventPanel()` 清除旧事件，不累积之前会话数据。`STORAGE_KEY` 使用 v2/v3 避免旧位置数据污染。

48. **Canvas emoji 字体必须包含 emoji 字体族**：`'Segoe UI Emoji, Apple Color Emoji, Microsoft YaHei'`，否则 ⚡🔀📊⏱🔔📈 等图标无法正确渲染。

49. **矩阵视图时间轴水平方向**：从左到右，秒数标签显示在底部，分类标签垂直排列在左侧，红色 NOW 线为垂直线。

50. **render() 必须 200ms 节流**：通过 `setTimeout` 实现渲染节流，高频事件下禁止每事件立即重建 DOM/Canvas。可视化绘制使用 Canvas，避免大量 DOM 节点。

---

## 八、时间与定时器（5 条）

51. **定时间隔为 1-9 秒随机值**：同股票间隔固定，不同股票间隔不同。禁止所有股票使用统一间隔。

52. **定时器触发类型表驱动**：`TIMER_TRIGGER_TYPES` 6 类（边定时器/TTL超时/Tick定时器/一次性/循环/默认），正则匹配需包含 `\btick\b` 以匹配单独的 'tick' 类型。

53. **事件时间戳归一化**：前端 `normalizeToModeMs()` 区分仿真相对秒（< 1e9）和真实 Unix 秒（≥ 1e9），仿真模式下减去 `simStartRealTime` 转为仿真相对毫秒。

54. **BarComposer.on_tick 接收 event_ts**：上游 `DataChanged(tick)` 事件的 `event.ts` 传递给 `on_tick`，再传递给 `_publish_bar_changed`。禁止在 `on_tick` 中调用 `time_at` 重新计算时间戳。

55. **暂停用中断等待**：`run_loop` 暂停改 `pause_event.wait()` 中断，禁止用 `asyncio.sleep` 轮询。

---

## 九、WebSocket 与 API（3 条）

56. **HTTP 路由与 WebSocket 路由必须分离**：挂载到不同 `APIRouter`，禁止在含 WebSocket 的 router 上挂载 HTTP-only dependencies。`APIKeyHeader` 依赖 HTTP `Request`，会破坏 WebSocket 路由。

57. **WebSocket 鉴权方案**：如需鉴权，在路由函数体内通过 `websocket.headers.get(...)` 或 `websocket.query_params.get(...)` 主动校验，禁止 `Depends(APIKeyHeader)` 形式。

58. **事件 API 会话隔离**：`sim_get_events` 实现会话隔离 + EventBus 增量读取 + 事件规范化。timer-queue 需支持 session_id。

---

## 十、反模式禁止（10 条）

59. **禁止 `if type == "xxx"` / `if nset == X` / `if pool_type == "custom"` 硬编码分支**：所有类型映射进 JSON 配置表。

60. **禁止 `from xxx_native import yyy` 显式导入领域函数**：通过 `_handler_registry` 字典动态查表调用。

61. **禁止 `eval()` 执行表达式**：统一用 `CompiledExpression` 的 `ast` 受控求值（`_eval_derived_ast`），函数白名单 `_DERIVED_FUNCS = {"max":max,"min":min,"abs":abs,"round":round}` 表驱动。

62. **禁止 `baimpool == 1` 硬编码判断目标池**：查 `pool_roles.json:role_resolution.rules` 驱动。

63. **禁止 workaround 掩盖 bug**：发现生产 bug 必须修复生产代码 + 改正向断言验证 spec 被满足。禁止测试反向断言（测试通过条件必须是 "spec 被满足" 而非 "bug 存在"）。

64. **禁止 cross 函数**：必须使用 Python 3.13 实现公式计算插件。

65. **禁止旧路径复活**：`_execute_flows()` / `_execute_flowsCore()` / `_tick_event_driven()` / `_tick_simple()` / `_should_fire_edge` / `_run_ttl_for_state_pools` 已删除，禁止重新引入。

66. **禁止 `_rt` 兼容 sink**：第 13 轮迭代已彻底移除 `_rt` 字典。运行时表直接作为 `PoolEngine` 真实属性或通过 `@property` 代理到 `PoolState` / `EdgeState`。

67. **禁止视图类持有本地 fallback 数据**：`_ExecCtxView` / `_FilterCacheView` 等视图类所有读写都落在 `PoolState` / `EdgeState`，不持有本地副本。

68. **禁止前端使用禁用 token**：`get_node_stocks` / `set_node_stocks` / `SimTickSource` / `EdgeFired.changed_codes` / `changed_codes` / `execution_order`（运行时）在前端代码中零匹配。前端 API 调用强制使用 `StatePoolView` 视图接口。

---

## 十一、代码质量与工程（7 条）

69. **所有模块必须完整自包含**：禁止代码分散。模块高内聚，对外仅通过 EventBus 交互。

70. **前端、后端、代码、配置必须解耦**：高 cohesion 低 coupling。前端不引用后端模块，后端不引用前端组件，配置表不嵌入代码逻辑。

71. **公式计算 + 股票筛选严格分离**：不可简化为选股公式或交易系统选股。公式计算写入 `latest_tick[code][formula_ref]`（公式=列），筛选做列比较/排序/集合操作（筛选=列操作）。

72. **转移条件配置在连接上**：转移条件必须明确配置在连接（edge）上，而非节点内部。触发条件设置在连接上，条件节点可有多条入边，每条有不同触发条件。

73. **连接优先级顺序从模板解析**：从 pool_config 模板解析并显示，不在代码中硬编码优先级。

74. **对象导向 + 事件驱动**：实现必须面向对象且事件驱动。时间相关功能单一方法不同参数 + 不同执行事件，定时事件用中断方法（直接或间接），不用轮询。

75. **迭代改进直到最简**：通过至少 10 轮（最多 100 轮）设计评审，直到类属性、方法、事件最简洁清晰。程序应越来越清晰，文档越来越简洁。

---

## 十二、验证与评审（5 条）

76. **必须包含运行时验证**：禁止仅凭代码审查 + 语法检查结项。必须实际启动仿真验证事件 ts 坐标系，必须浏览器真实验证事件面板所有修复点。

77. **eventtest 171 项不可替代**：必须全部通过，退出码 0。

78. **手动 MCP playwright 验证**：必须通过手动 MCP 的 playwright 打开浏览器验证所有功能及事件节点正确性。

79. **JS 版本号必须更新**：每次修改 `event-panel.js` / `styles.css` / `app.js` 等前端文件后，必须更新 `index.html` 中的 `?v=N` 版本号以刷新浏览器缓存。

80. **配置表变更同步文档**：新增或修改配置表后，必须同步更新 `DESIGN0.md` 的配置表清单和 `DESIGN.md` 的表操作速查矩阵。

81. **DataChanged 发布统一为 `publish_data_changed` 单一函数**：禁止在 tick_bar_module.py 或其他模块中重新引入 `_publish_tick_changed` / `_publish_bar_changed` 同构函数。所有 DataChanged 事件发布必须通过 `publish_data_changed(bus, state, source, codes, ts, data=None, period=None, bar_hash="")` 统一入口，ts 由调用方传入，禁止内部调用 `time_at(state)`。

82. **EdgeExecutor 步骤序列表驱动化**：`EdgeExecutor.run` 按 `CompiledSchedule.steps` 表驱动循环执行，步骤序列由 `edge_strategies.json:steps` 编译期产出。新增执行步骤 = 在 JSON 中加 `StepSpec` 条目 + 实现 `EdgeStep` Protocol 的 `run` 方法 + 注册到 `STEP_REGISTRY`，零行 `run` 改动。禁止在 `run` 方法中写过程式 if/调用序列。

83. **MonitoringModule 事件记录表驱动化**：事件记录通过 `EVENT_RECORD_ADAPTERS` dict + `event_to_record(event)` 函数 + `subscribe_any` 单一订阅。新增事件类型适配 = 在 dict 中加 `{event_type_name: adapter_func}` 条目，零行 `MonitoringModule` 代码改动。禁止重新引入逐个 `bus.subscribe(EventType, self._on_xxx)` 注册模式。

84. **同步/异步双路径统一为 `_step_once_impl(async_mode)` 单一骨架**：禁止重新引入 `_step_once` / `_astep_once` 双路径过程式展开；`_on_simulation_step` / `_on_replay_step` 统一为 `_on_step_event(driver_type, provider_fn)` 单一处理器；`TickReceived` 发布循环统一为 `_publish_tick_batch(bus, tick_data, ts)` 模块级函数。

85. **公式引擎统一为 `IFormulaEngine` Protocol**：`CompiledFormula` / `PythonFormulaEngine` / `FormulaEngine` / `FormulaRouter` 全部实现该协议；引擎分派通过 `_ENGINE_DISPATCH` dict 查表，禁止新增引擎类时绕过 Protocol 或使用 if/elif 链。

86. **HTTP 路由引擎未初始化检查统一为 FastAPI `Depends(require_config_store)`**：禁止在路由处理器内重新引入 `if not _config_store` 样板；sim 控制路由统一为 `POST /api/pool/{name}/sim/{action}` + `_SIM_ACTIONS` 表驱动，禁止重新引入 `sim_pause` / `sim_resume` 等独立路由。

87. **配置加载统一到 `ConfigStore.get_table` / `get_data_file`**：禁止在模块级重新定义 `_load_json` / `_load_config` / `_load_json_file` / `_load_json_cache` 帮助函数；所有 JSON 配置加载必须通过 ConfigStore，确保热加载能力。

88. **K 线合成统一为 `synthesize(bars, source_period, target_period)` + `_SYNTHESIS_RULES` 表**：禁止重新引入 `synthesize_from_1min` / `synthesize_from_5min` / `_synthesize_day_from_intraday` / `synthesize_from_daily` 同构函数；周期 key 函数通过 `_PERIOD_KEY_FUNCS` 表驱动。

89. **导入/导出统一为 `import_pool(path, format)` / `export_pool(config, path, format)` + `_IMPORT_RULES` / `_EXPORT_RULES` 表**：禁止重新引入 `import_dzh_xml` / `import_tdx_xml` / `import_json` / `export_to_dzh_xml` / `export_to_tdx_xml` / `export_to_json` 同构方法。

90. **前端事件画布统一为 `renderEventCanvas(ctx, state, layoutMode)` + `_DRAW_LAYERS` 表 + `_STYLE` 配置**：禁止重新引入矩阵视图/散点视图的独立渲染函数；样式从 `_STYLE` 配置对象读取，禁止硬编码颜色/线宽/字体；`_DRAW_LAYERS` 表驱动图层顺序。

91. **nset 筛选函数值驱动**：screening_module 的 nset 筛选必须通过 `_NSET_FILTER_HANDLERS` 表驱动分派，禁止重新引入 `_filter_condition_formula` / `_filter_expert_system` / `_filter_financial_scalar` / `_filter_market_scalar` 同构函数。nset=1/2 共用 `_filter_truthy`，nset=3/4 共用 `_filter_scalar`（nset_label 由 `str(nset)` 派生）。

92. **ConfigStore 配置加载统一**：core/*.py 的所有配置加载必须通过 `get_global_config_store().get_table(...)` 统一入口，禁止重新引入 `json.load(open(...))` 样板（ConfigStore 内部除外）。确保热加载能力（无模块级缓存冻结）。

93. **noperate mode 表驱动**：execution_module 的 noperate mode 分派必须通过 `_MODE_HANDLERS` 表驱动，禁止重新引入 `if mode == "inflection"` / `if mode == "rank"` 硬编码分支。execution_module 与 screening_module 共享同一 mode 真相源，通过 `_apply_noperate_mode_series` 向量变体复用。

94. **base_period 目标表驱动**：runtime_mode_module 的 base_period 目标周期必须通过 `_BASE_PERIOD_TARGETS` 表驱动，禁止重新引入 `if/elif base_period` 分支。day 基周期通过空列表短路返回。

95. **tradeattr BUY/SELL 表驱动**：trade_module 的 `_apply_tradeattr` 必须通过 `_TRADEATTR_FIELD_MAP` 表驱动 + 单循环，禁止重新引入 `if side == "BUY"` / `elif side == "SELL"` 双分支。BUY 用 `enter*` 字段，SELL 用 `exit*` 字段，共用 `_TRADEATTR_TARGET_KEYS` 目标 key 列表。

96. **导入导出 converter 统一入口**：import_export_module 的格式转换必须通过 `_CONVERTER_REGISTRY` 表 + `_call_converter(path, fmt, direction, config=None)` 单一入口，禁止重新引入 `_parse_dzh` / `_parse_tdx` / `_parse_json` / `_serialize_dzh` / `_serialize_tdx` / `_serialize_json` 同构函数。

97. **公式 eval 核心合并**：formula_module 的公式求值必须通过 `_eval_formula_core(series=False)` 统一入口，`_eval_formula` 与 `_eval_formula_series` 改为薄包装委托（方法体 ≤ 5 行），禁止重新引入双实现非薄包装。

98. **同步协程执行器统一**：runtime_mode_module 的同步协程执行必须通过模块级 `_run_coro_sync(coro, loop_holder, loop_attr="_sim_loop")` 统一入口，禁止重新引入类内 `_run_coro_sync` / `_run_coro` 双方法。KLineReplayEngine 用 `"_replay_loop"`，RuntimeSimulator 用 `"_sim_loop"`。

99. **事件 handler 装饰器统一**：trade_module / execution_module / monitoring_module / tick_bar_module / screening_module 5 模块的 `_on_xxx` 事件 handler 必须通过 `@_event_handler(name)` 装饰器统一异常处理，禁止在 handler 函数体内重新引入 try/except 样板。装饰器统一 `exc_info=True`，异常被捕获并 `logger.warning`，返回 None。

100. **pnl 计算表驱动**：monitoring_module 的 pnl 指标计算必须通过 `_PNL_METRIC_SPECS` 表 + 单一 `_compute_pnl_metric(metric_name)` 方法，禁止重新引入 `_compute_intraday_pnl` / `_compute_market_impact_pnl` / `_compute_historical_pnl` / `_compute_distribution_pnl` / `_compute_positioning_pnl` 同构方法。排序键必须通过 `_ANGLE_SORT_KEYS` lambda dict，禁止重新引入 `_momentum_key` / `_trend_key` / `_value_key` 方法。

---

## 十三、meta-pattern 收敛约束（10 条）

101. **DZH/TDX OOP 同源继承**：DZH 与 TDX 池转换器必须继承 `converters.py` 中 `BasePoolConverter` 抽象基类，基础功能（`_parse_element` / `_add_element` / `_decode_pos` / `_decode_xml_bytes`）使用相同代码，差异仅通过子类（`DzhPoolConverter` / `TdxPoolConverter`）的 `int_fields` 表 / `post_hook` / `encoding_priority` / cell envelope 参数体现。禁止重新引入 25 组同构函数：`_parse_func_element` / `_parse_psatt_element` / `_parse_spinfo_element` / `_add_func` / `_add_psatt` / `_add_spinfo` / `_parse_pos` / `_parse_tdx_pos` / `_decode_xml_content` / `_decode_tdx_xml` / `_build_cell_default` / `_build_cell_pool` / `_build_cell_market` / `_parse_stk_children` / `_parse_stk_elements` / `_export_field_stocks` / `_add_stks` / `_export_field_*` / `_make_tdx_cell` 3 分支版本等。Enforcement：metatest v4 `oop_inheritance_depth` 维度 + `isomorphism_elimination` 检查 1-14（Grep `def _parse_func_element|def _add_func\b|def _parse_pos\b|def _decode_xml_content\b` 等在 converters.py = 0）。

102. **DZH↔TDX 类型映射单一真相源**：所有 DZH↔TDX 类型映射必须定义在 `config/architecture/dzh_type_map.json`，含四向映射 `dzh_to_tdx` / `tdx_to_dzh` / `tdx_to_frontend` / `frontend_to_tdx`。DZH type 3 必须映射到唯一 TDX 值（消除原 `_DZH_TO_TDX_TYPE`→3 与 `_DZH_TO_TDX_TYPE_EXPORT`→0 的矛盾），`dzh_to_tdx` 与 `tdx_to_dzh` 必须互为逆映射（单元测试断言）。`core/schemas.py` 与 `converters.py` 必须通过 `ConfigStore.get_table("dzh_type_map")` 派生。禁止重新引入并行映射表 `_DZH_TO_TDX_TYPE` / `_DZH_TO_TDX_TYPE_EXPORT` / `TDX_TO_DZH_CELL_TYPE` / `TDX_CELL_TYPE_MAP` / `_load_dzh_type_map`。Enforcement：metatest v4 `isomorphism_elimination` 检查 15-19（Grep `_DZH_TO_TDX_TYPE\b|_DZH_TO_TDX_TYPE_EXPORT\b|TDX_TO_DZH_CELL_TYPE\b|TDX_CELL_TYPE_MAP\b|def _load_dzh_type_map\b` 在 *.py = 0）。

103. **`_CONVERTER_REGISTRY` 完整 OOP 路由**：所有 DZH/TDX 导入导出操作必须通过 `_CONVERTER_REGISTRY` 表 + `_call_converter(path_or_data, fmt, direction, config=None)` 单一入口分派（`fmt=None` 时由 `is_tdx_format` 自动探测）。`dzh_import` / `dzh_import_and_save` / `dzh_load_demo` / `dzh_export` / `load_dzhpool_file` / `_tdx_load` / `_tdx_create` / `_tdx_save` / `tdx_export_xml` 全部走 `_call_converter`。禁止 `api.py` / `app.py` 直接调用 `parse_dzh_xml` / `parse_tdx_xml` / `_build_tdx_xml` / `export_meta_to_dzh_xml_bytes`。禁止 `if is_tdx_format:` 分支判断在 api.py 出现（`is_tdx_format` 仅可作为 fmt 探测函数调用）。Enforcement：metatest v4 `isomorphism_elimination` 检查 20-24（Grep `parse_dzh_xml\(|parse_tdx_xml\(|_build_tdx_xml\(|export_meta_to_dzh_xml_bytes\(` 在 api.py / app.py = 0；Grep `is_tdx_format` 在 api.py 内 if 分支 = 0）。

104. **公共工具函数下沉 `converters/_common.py`**：公共工具函数 `safe_int` / `safe_float` / `safe_str` / `safe_cast` / `decode_formula` / `extract_formula_from_binary` / `is_valid_formula` / `extract_text_segments` / `decode_xml_bytes` / `decode_pos` / `hash_dict_content` 必须定义在 `converters/_common.py` 平铺模块。禁止在 `core/*.py` / `native/*.py` / `services/*.py` 模块级重新定义 `_safe_int` / `_safe_float` / `_to_float` / `_cast_int` / `_cast_str` / `_decode_formula` / `_extract_formula_from_binary` / `_is_valid_formula` / `_extract_text_segments` / `_load_dzh_type_map`（仅 `converters/_common.py` 内允许核心实现，其他模块改为 thin wrapper 委托或直接 import）。`native/builtins.py:_decode_formula_base64` 必须为 thin wrapper 调用 `converters._common.decode_formula`。Enforcement：metatest v4 `isomorphism_elimination` 检查 + Grep `def _safe_int\b|def _safe_float\b|def _to_float\b|def _cast_int\b|def _cast_str\b|def _decode_formula\b|def _extract_formula_from_binary\b|def _is_valid_formula\b|def _extract_text_segments\b|def _load_dzh_type_map\b` 在 core/*.py + native/*.py + services/*.py = 0。

105. **replay/simulation heapq 调度禁轮询**：replay 与 simulation 步进必须由 `EventDriver` heapq + `loop.call_at`（wall-clock pacing）或 `fire_due(now)`（虚拟时钟）调度。`play()` 必须调用 `EventDriver.schedule(step_event, fire_time=now + base_interval/speed)`，步进回调中调用 `_do_step()` 并重新调度；`pause()` 通过 `EventDriver.cancel(step_event_id)` 取消；停止通过 `ModeChanged` 事件订阅或 `EventDriver.cancel`。禁止 `while True + time.sleep(interval)` 步进循环（`_sync_play_loop`）。禁止 `asyncio.sleep` 步进循环（`_sync_sim_loop` / `auto_step_loop`）。禁止 `_run` / `_sim_auto_step` / `_current_mode` 标志轮询（`while self._run:` 等）。Enforcement：metatest v4 `polling_zero_tolerance` 维度（Grep `def _sync_play_loop\b|def _sync_sim_loop\b|async def auto_step_loop\b|while self\._run\b|while self\._sim_auto_step\b|time\.sleep\(interval\)` 在 runtime_mode_module.py = 0）。

106. **文件监视 watchdog 禁 mtime 轮询**：文件监视必须使用 `watchdog.Observer` 事件驱动，发布 `FileModified` 事件到 EventBus，`services/data.py` 订阅事件触发重载，`app.py` 必须调用 `start_watchdog` 启动监视器。禁止 `while _running + asyncio.sleep + 比较 mtime` 轮询（`_file_watcher_loop` / `start_polling` 已删除不得复活）。禁止 `asyncio.sleep(3)` 出现在文件监视路径。Enforcement：metatest v4 `polling_zero_tolerance` 维度（Grep `def start_polling\b` 在 table_engine.py = 0；Grep `def _file_watcher_loop\b` 在 services/data.py = 0；Grep `asyncio\.sleep\(3\)` 在 services/data.py 文件监视路径 = 0）。

107. **SSE 流 asyncio.Queue 禁 50ms 轮询**：SSE 事件流（`app.py:events_stream`）必须使用 `asyncio.Queue(maxsize=10000)` + `asyncio.wait_for(queue.get(), timeout=15.0)` 阻塞等待事件，超时发心跳。EventBus 订阅回调必须使用 `queue.put_nowait(event_data)` 投递事件，`request.is_disconnected()` 检测保留作为退出条件，镜像 `api.py:events_ws` 已验证的 `asyncio.Queue + await queue.get()` 模式。禁止 `run_in_executor(drain) + asyncio.sleep(0.05)` 50ms 队列轮询。Enforcement：metatest v4 `polling_zero_tolerance` 维度（Grep `run_in_executor\(.*drain` 在 app.py = 0；Grep `asyncio\.sleep\(0\.05\)` 在 app.py SSE 路径 = 0）。

108. **前端 setInterval 禁轮询改 SSE/WS**：所有前端状态更新必须由 SSE `EventSource('/api/events/stream')` 或 WebSocket 订阅推送驱动。禁止 `setInterval + fetch('/api/state/runtime')` 轮询（`RuntimeState._poll`，改订阅 SSE 的 `ModeChanged` / `SnapshotUpdated`）。禁止 `setInterval + POST /reload` 轮询（`_startHotReload`，改订阅 `/api/config/ws` 的 `ConfigChanged`）。禁止 `setInterval + fetch /api/highlight-events` 轮询（`HighlightManager.startPolling`，改依赖 `/ws/highlight` WebSocket）。禁止 `setInterval + syncTimerQueue + fetch /api/events/timer-queue` 轮询（改从 SSE 流的 `TimerQueued` / `TimerFired` 事件更新）。`/api/highlight-events` GET 端点必须删除（已无前端调用）。每次修改 JS 必须更新 `index.html` 中 `?v=N` 版本号（第 79 条）。Enforcement：metatest v4 `polling_zero_tolerance` 维度（Grep `setInterval.*_poll|setInterval.*\/reload|setInterval.*\/api\/highlight-events|setInterval.*syncTimerQueue` 在 web/js/*.js = 0）。

109. **`_FieldedBase` / `_ADAPTER_SPECS` / `_SUBSCRIPTIONS` / `_SIDE_SPECS` / `_PSATT_SIDE_EFFECTS` / `_RANKING_SPECS` 表驱动**：所有事件记录 / 交易执行 / 事件订阅 / 排名计算 / 字段序列化必须使用声明式表驱动分派。`_FieldedBase` mixin（`core/domain.py`）：`_NodeBase` / `_EdgeBase` 的 `to_dict` / `from_dict` 必须继承，禁止 per-class 实现。`_DictConstructible` mixin（`core/schemas.py`）：TDX Pydantic 模型 `from_dict` 必须继承。`_ADAPTER_SPECS` 表（`monitoring_module.py`）：24 个事件类型声明式 spec + `_build_adapter_record` 通用 builder，禁止 `_adapter_X` 函数。`_SUBSCRIPTIONS` 类属性（`event_bus._BaseModule`）：7 模块继承 `register_subscribers`，禁止 per-module `_register_subscribers`。`_SIDE_SPECS` 表（`trade_module.py`）：BUY/SELL 分派，禁止 `_execute_buy` / `_execute_sell`。`_PSATT_SIDE_EFFECTS` 表（`trade_module.py`）：5 条副作用，禁止 `if action_spec.bX:` 链。`_RANKING_SPECS` 表（`monitoring_module.py`）：pk_ranking / analysis_angles。`_aggregate_ohlcv` / `_DATE_KEYS` / `_status`（`runtime_mode_module.py`）；`_compile_spec` / `_make_publishing_action` / `_gate_window` + `Step` 基类 5 子类（`execution_module.py`）。禁止重新引入 if/elif 类型链、同构函数族或 per-class 实现。Enforcement：metatest v4 `primitive_convergence` 维度（分派原语）+ `isomorphism_elimination` 检查（Grep `def _adapter_\w+\b|def _execute_buy\b|def _execute_sell\b|def _register_subscribers\b|if action_spec\.bsavehis|def _compile_timing_spec\b` 等在对应模块 = 0 或仅 thin wrapper）。

110. **哈希函数三族统一到 `core/_hashing.py`**：所有哈希函数必须定义在 `core/_hashing.py`：`hash_dict_content(content, exclude)` 合并 per-content MD5（`_hash_tick` / `_hash_bar` / `_hash_bars` / `_hash_object` / `_hash_code_bars`）；`hash_tick_aggregate(tick_data, per_code_hasher)` 合并 aggregate tick hash（`PoolStateMixin._hash_tick_data` / `_InternalState._hash_tick_data` / `_hash_period_bars`）；`BarHashMixin` 含 `bar_hash` property 合并 3 处 accessor；`hash_object(obj, none_default, serializer)` 任意对象哈希。跨模块引用必须为 thin wrapper（1 行委托）或直接 import。禁止跨模块重新定义 `_hash_tick` / `_hash_bar` / `_hash_bars` / `_hash_object` / `_hash_code_bars` / `_hash_tick_data` / `_hash_period_bars`（core/*.py 内仅允许 thin wrapper 委托 `hash_dict_content` / `hash_tick_aggregate`，非 dict 分支保留 ≤ 4 行）。Enforcement：metatest v4 `primitive_convergence` 维度（继承原语）+ `isomorphism_elimination` 检查（Grep `def _hash_tick\b|def _hash_bar\b|def _hash_bars\b|def _hash_object\b|def _hash_code_bars\b|def _hash_tick_data\b|def _hash_period_bars\b` 在 core/*.py 仅匹配 thin wrapper，AST 瘦包装识别 ≤ 4 行方法体）。

---

## 十四、v5 元模式收敛（5 条）

111. **MetaDispatcher 统一**：EventBus 与 ConfigStore 必须继承 `MetaDispatcher` 抽象基类（`core/event_bus.py`），覆盖 `_dispatch_impl` 实现各自派发语义（EventBus 扇出+副作用 / ConfigStore 查找无副作用）。EventDriver 因 heapq 时序优先队列 + fire_time 排序 + 自动续程语义特化，保持独立，不继承 MetaDispatcher。公理：`Code = Data + MetaDispatcher`。禁止再造第四核 Dispatcher。

112. **跨模块 import 纪律**：7 个业务模块（execution_module / screening_module / formula_module / runtime_mode_module / trade_module / tick_bar_module / monitoring_module）禁止直接 `from .table_engine import` 或 `from .screening_module import`。跨模块依赖必须经依赖注入（构造函数注入）或下沉到白名单基础模块（`core/domain` re-export `load_config_table` / `get_global_config_store`，`converters_common` 提供 `safe_cast` 等）。函数级延迟 import 仅在依赖模块内部状态时允许。

113. **EventDriver action 签名**：所有 `TimedEventSpec.action` 可调用对象必须接受 `action(params, fire_time=None)` 签名，与 `EventDriver.fire_due` 调用 `spec.action(spec.params, fire_time)` 一致。测试侧 action 函数必须同步此签名，禁止 `action(params)` 单参数形式。

114. **运行时验证 harness**：replay/simulation/mode-switch 必须有 in-process 测试（`metatest/test_runtime_*.py`），通过 `EventDriver.fire_due(now)` 手动推进时间验证 heapq 调度，禁止启动 uvicorn/浏览器/asyncio loop。3 个测试文件：`test_runtime_replay_heapq.py` / `test_runtime_simulation_heapq.py` / `test_runtime_mode_switch.py`，闭合沙箱缺口。

115. **handler 表驱动**：≥ 3 个同构 `_on_*` event handler 必须收敛为 `_HANDLERS: Dict[type, spec]` 表 + 1 个通用方法（如 `_persist_event` / `_forward_event`）。非同构 handler（含特殊控制流）保留。禁止无效抽象（< 3 个同构 handler 不强制表驱动）。

116. **外部 SDK 适配器转发同构表驱动**：≥ 5 个同构转发方法（如 `if self._bridge is None: return DEFAULT → return self._bridge.METHOD(args)` 或 `if not self._tq: return None → try: SDK 调用 → except: return None`）必须收敛为声明式表 + 通用转发器：`_FORWARD_SPECS` + `_forward`（TqProvider 转发）、`_CACHED_TQ_CALLS` + `_call_cached`（带缓存 SDK 调用）、`_SIMPLE_TQ_CALLS` + `_call_simple`（无缓存 SDK 调用）。参数转发签名差异过大使表条目复杂度 > 方法体的，保留现状（避免抽象税负收益）。这是 MetaDispatcher 之外「声明 Data + Dispatcher 通用转发器」元模式投影（第六层洞察）。

117. **外部 SDK 适配器 per-code 循环调用必须表驱动**：≥ 3 个同构 per-code 方法（per-code 循环 + cache_key 构建 + 缓存检查 + SDK 调用 + 缓存写入 + 异常兜底）必须收敛为 `_PER_CODE_TQ_CALLS` 表（映射方法名 → (cache_prefix, sdk_method, cache_only_if_truthy)）+ 通用 `_call_cached_per_code` 方法。`cache_only_if_truthy` 标志保留真值判断行为差异（如 get_report_data 的 `if data:` 空快照不污染缓存）。双签名同构方法（如 get_stock_list 新/旧签名两分支）必须收敛为 `_CACHED_TQ_CALLS` 多条目 + if/else 双调用。禁止重新引入独立 per-code 循环方法或双签名过程式展开。

118. **Converter 主流程必须模板方法化**：`parse_pool` / `export_pool` 模板方法必须在 `BasePoolConverter` 中编排骨架（parse 6 步：`_decode_source` → `ET.fromstring` → `_extract_pool_meta` → `_parse_cells` → `_parse_flows` → `_build_result`；export 5 步：`_create_root` → `_serialize_pool_attrs` → `_serialize_cells` → `_serialize_flows` → `_finalize_xml`），子类（`DzhPoolConverter` / `TdxPoolConverter`）仅覆盖 10 个差异钩子，禁止重新引入模块级并行主流程函数。模块级函数（`parse_dzh_xml` / `parse_tdx_xml` / `export_dzh_xml` / `_build_tdx_xml`）仅作薄包装委托（函数体 ≤ 3 行，委托 `_DZH_CONVERTER` / `_TDX_CONVERTER` 单例的 `parse_pool` / `export_pool`），保留原签名向后兼容。这是 OOP 继承的「主流程编排层」收敛（第八层洞察）：v4 已收敛 4 原子操作（`_parse_element` / `_add_element` / `_decode_pos` / `_decode_xml_bytes`），v8 将两个真正同构的主流程上提为模板方法，闭合「大智慧和通达信只作为继承，所有基础功能用相同代码」的最后一层。

119. **模板方法差异钩子必须 @abstractmethod 早失败**：`BasePoolConverter` 的 10 个差异钩子（`_decode_source` / `_extract_pool_meta` / `_parse_cells` / `_parse_flows` / `_build_result` / `_create_root` / `_serialize_pool_attrs` / `_serialize_cells` / `_serialize_flows` / `_finalize_xml`）必须使用 `@abstractmethod` 装饰（非 `raise NotImplementedError`），实现 construction-time 早失败契约执行——子类未覆盖任一钩子时实例化即报 `TypeError`，优于 invocation-time 晚失败（`raise NotImplementedError` 在钩子被调用时才报错，系统可能静默运行至深层流程才暴露缺口）。这是「极致本质的运行时」的第九层洞察：类型系统级契约执行优于运行时异常契约执行。同时，元模式同构收敛已达上限时须文档化「知止」——v8 已达 DZH/TDX 同构收敛上限，10 钩子方法体是结构同构但数据/派发异构，禁止强行合并（抽象税 > 收益，引入表条目爆炸 + roundtrip 回归风险）。

120. **所有事件 handler 必须 @_event_handler 装饰 + 审计盲区闭合后的全局收敛上限**：所有通过 `self._bus.subscribe()` 注册的事件 handler 必须使用 `@_event_handler` 装饰器（禁止裸 handler），防止 handler 异常中断 EventBus.publish 同步扇出链——一个 handler 抛未捕获异常会导致后续订阅者不执行，事件链断裂。`@_event_handler`（event_bus.py:15-26）将异常处理从 handler 体内部上提到装饰器层（AOP 横切），统一"handler 不应中断事件链"的运行时契约（捕获→logger.warning→返回 None）。这是「极致本质的运行时」的第十层洞察：异常处理覆盖完整性是运行时安全本质。**v11 范围修正**：v10 声明的「全局收敛上限」仅适用于 `core/` 内部；`converters.py` 与跨域审计盲区在 v11 闭合后（DZHPoolExecutor 平行运行时消除 + DzhXmlExporter 死代码删除 + TDX 函数归入 TdxPoolConverter + 同构函数合并），全局收敛上限声明才真正成立。

121. **禁止平行运行时 + 审计盲区零容忍**：不得在 `converters.py` / `services/*.py` 内实现 `threading.Thread + while + wait(N)` 轮询调度平行运行时；所有长时执行必须委托 `PoolEngine.run_loop()`（`asyncio.Event.wait()` + `loop.call_at` 事件驱动），所有一次性执行必须委托 `PoolEngine.execute_pool()`。运行时只有一个真相源。这是「极致本质的运行时」的第十一层洞察：审计盲区是收敛上限的最大敌人——v10 因 metatest 轮询零容忍检查只 grep `core/runtime_mode_module.py` / `core/table_engine.py` / `services/data.py` 三个文件、从未覆盖 `converters.py`，而漏判 `DZHPoolExecutor` 这个 ~400 行活体平行运行时（`threading.Thread + while + _stop_event.wait(1) + time.time() - last >= interval_sec` 轮询，维护私有 `self._events` 列表绕过 EventBus，被 `api.py /pool/start` 端点活体启动）。v11 闭合此盲区：metatest 扫描文件列表扩展到 `converters.py`，新增平行运行时零容忍检测 + 死代码零容忍检测（`_collect_parallel_runtime_violations` / `_collect_dead_code_violations`），`isomorphism_elimination` 维度 41→44 项。

---

## 附：文件路径映射（文档 vs 实际代码）

> DESIGN0.md / DESIGN.md 中部分文件路径引用已与实际代码脱节，以下为正确映射：

| 文档引用路径 | 实际代码路径 | 说明 |
|------------|------------|------|
| `core/runtime.py` | `core/runtime_mode_module.py` (行 2711+) | PoolState 已合并 |
| `core/edge_state.py` | `core/domain.py` (行 2045) | EdgeState 已合并 |
| `core/compiler.py` | `core/execution_module.py` (行 447/880) | Compiler 已合并 |
| `core/time_util.py` | `core/domain.py` (行 1953) | time_at 已合并 |
| `MetaEngine` (engine.py) | `PoolEngine` (engine.py) | Task 24 已合并删除 |
| `core/data_updater.py` | `core/tick_bar_module.py` | DataUpdater 已合并 |
| `core/bar_composer.py` | `core/tick_bar_module.py` | BarComposer 已合并 |
