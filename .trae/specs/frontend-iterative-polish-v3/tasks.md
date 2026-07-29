# Tasks — 前端股票池界面迭代完善 V3

> **执行机制**：双工程师协作 + 98 分门槛。每个任务由调度方派发：架构工程师（sub-agent）修复 → 评审工程师（sub-agent）打分 → ≥98 进入下一任务 / <98 携扣分点打回重做。

## 执行机制

1. **架构工程师**（sub-agent_type=general_purpose_task）：依据 spec.md 修复对应模块，禁止兼容旧接口，禁止 workaround 掩盖 bug。
2. **评审工程师**（sub-agent_type=general_purpose_task）：依据 checklist.md 逐项打分，必须基于 Playwright + 浏览器控制台 + eventtest 量化结果。
3. **门槛**：≥ 98 分进下一任务；< 98 分打回架构工程师重做，直至 ≥ 98。
4. **依赖**：严格按 Task Dependencies 执行。
5. 每个任务完成后，调度方在 tasks.md 勾选 `[x]`，在 checklist.md 勾选通过项。

---

## Task 1: 画布引擎（FlowCanvas）逐项评审与修复

- [x] **SubTask 1.1**: 验证 `FlowCanvas` 核心方法（render/setNodes/setEdges/fitToContent/setEdgeLineType/zoomIn/zoomOut/setZoom/selectNode/selectEdge/highlightNode/unhighlightNode）正确工作
- [x] **SubTask 1.2**: 验证关键属性单一真相源（`_nodes`/`_edges`/`transform`/`poolId`/`_runMode`/`selectedNodeId`/`selectedEdgeId`）
- [x] **SubTask 1.3**: 验证事件回调（onChange/onConnect/onNodeClick/onEdgeClick/onCanvasClick/onNodeDoubleClick/onNodeDragEnd/onZoomChangeCb）正确订阅、触发、取消订阅
- [x] **SubTask 1.4**: Playwright 验证三种线形（贝兹曲线/横竖折线/直线）切换生效，节点拖拽/缩放/框选/迷你地图/resize handles 正确工作
- [x] **SubTask 1.5**: Playwright 验证运行模式（_runMode=true）下编辑功能正确禁用（拖拽/handle/框选/右键菜单均无效）
- [x] **SubTask 1.6**: 评审工程师打分 98/100，通过（≥ 98 门槛）

## Task 2: 节点类型与边渲染逐项评审与修复

- [ ] **SubTask 2.1**: 验证所有节点类型按 `cell_type_registry` 配置正确渲染：DZH 200/201/202/4、TDX 7/8/3、`condition`（紫色矩形 #8e44ad）、`text_label`/`container`/`state_column`/`execution_order`/`flow_arrow`
- [ ] **SubTask 2.2**: 验证转移条件节点（cond1/cond2/cond3）在 `sim_test_pool_100` 池中显示为紫色圆角矩形
- [ ] **SubTask 2.3**: 验证筛选条件从 `edge.params` 读取（func.accode/indi/indiparam/filter_spec），不从节点内部硬编码
- [ ] **SubTask 2.4**: 验证边渲染（贝兹/横竖/直线）+ 箭头按 `edge_strategies` 颜色 + 边标签显示时间间隔/条件名称 + 执行顺序编号
- [ ] **SubTask 2.5**: Playwright 截图 + 浏览器控制台无错误
- [ ] **SubTask 2.6**: 评审工程师打分，≥ 98 方可进入 Task 3

## Task 3: 属性面板（TableDrivenPanel）逐项评审与修复

- [ ] **SubTask 3.1**: 验证 `TableDrivenPanel.showForNode/showForEdge/showForPool` 根据选中对象动态生成表单
- [ ] **SubTask 3.2**: 验证字段联动（`depends_on`/`active_when`）正常显示/隐藏
- [ ] **SubTask 3.3**: 验证 `ValidationEngine` 实时校验并显示错误信息
- [ ] **SubTask 3.4**: 验证 `DataBinder.decodeAttrFlags`/`encodeAttrFlags` 位标志编解码正确
- [ ] **SubTask 3.5**: 验证 DZH 颜色值可视化（`renderDzhColorBadge`）正确显示颜色徽章
- [ ] **SubTask 3.6**: 验证所有内置组件类型（text_input/textarea/number_input/select/tdx_enum_select/color_picker/flag_group/action_compound/market_selector/formula_editor/stock_list_editor/stock_source_editor/transfer_mode/flow_mode_display/condition_summary/readonly/kline_chart/indicator_chart/rule_editor）正常工作
- [ ] **SubTask 3.7**: 评审工程师打分，≥ 98 方可进入 Task 4

## Task 4: 四种模式切换逐项评审与修复

- [ ] **SubTask 4.1**: Playwright 验证设计模式加载，画布可编辑、属性面板可用、事件面板隐藏
- [ ] **SubTask 4.2**: Playwright 验证实盘模式切换路径正确，模式指示器变绿色，后端 `DataSourceContract` 探测逻辑生效
- [ ] **SubTask 4.3**: Playwright 验证回放模式面板正确显示，`IStorageQuery` 注入 `kline_cache`，模式指示器变橙色
- [ ] **SubTask 4.4**: Playwright 验证仿真模式切换 active，仿真面板显示，模式指示器变紫色，事件面板 `.visible` 类生效
- [ ] **SubTask 4.5**: Playwright 验证运行中切换模式被阻止并提示，模式切换不导致界面混乱
- [ ] **SubTask 4.6**: 评审工程师打分，≥ 98 方可进入 Task 5

## Task 5: 仿真模式股票流转逐项评审与修复（依赖 Task 4）

- [ ] **SubTask 5.1**: Playwright 验证仿真启动后虚拟时钟和步数计数器正确运行
- [ ] **SubTask 5.2**: 验证备选池加载 100 只 `fz` 前缀股票，每只股票 tick 间隔为 1-9 秒随机值（同股票间隔固定，不同股票间隔不同）
- [ ] **SubTask 5.3**: 验证股票经 cond1 进入 pool_A（触发频率 1min，转移条件 5min K线 KDJ 金叉，停留 100 分钟后删除）
- [ ] **SubTask 5.4**: 验证股票经 cond2 进入 pool_B（触发频率 10s，转移条件 1min MACD 金叉，停留 200 分钟后删除）
- [ ] **SubTask 5.5**: 验证 A∩B 经 cond3 进入 pool_C（触发频率 5s，取交集），入 C 池后立即市价买入 100 股，停留 20 分钟后卖出
- [ ] **SubTask 5.6**: eventtest 量化验证完整事件链（9 类事件计数 + 池状态快照 + 定时器队列）
- [ ] **SubTask 5.7**: 评审工程师打分，≥ 98 方可进入 Task 6

## Task 6: 事件面板（EventPanel）逐项评审与修复（依赖 Task 5）

- [ ] **SubTask 6.1**: 验证事件面板按 9 类分类显示（Tick/Bar/Formula/Edge/Transfer/Signal/Order/TTL/System），图标和颜色正确
- [ ] **SubTask 6.2**: 验证已发生事件（实心图标）与排队中事件（黄色虚线框）区分显示
- [ ] **SubTask 6.3**: 验证定时器队列显示预计触发时间（fire_at）、事件类型、股票代码、详情、queue_position，过期事件用红色虚线框
- [ ] **SubTask 6.4**: 验证矩阵视图：时间轴水平方向（左→右），秒数标签在底部，分类标签垂直排列在左侧（宽度 86px，半透明暗色背景），NOW 红色垂直线
- [ ] **SubTask 6.5**: 验证散点视图：与矩阵视图同步统一标签宽度、标签区背景、网格线范围、emoji 字体、显示实际时间（formatTime）
- [ ] **SubTask 6.6**: 验证点击分类行/单个事件图标在下方详情区显示事件文本记录
- [ ] **SubTask 6.7**: 验证事件面板默认隐藏（display:none），仅在仿真/回放模式通过 `.visible` 类显示，退出时正确隐藏；每次启动新仿真时 `clearEventPanel()` 清除旧事件
- [ ] **SubTask 6.8**: 验证拖拽浮窗、折叠/展开、暂停/继续接收、清空事件功能正常，位置/状态保存到 localStorage（STORAGE_KEY='metacore_event_panel_state_v3'）
- [ ] **SubTask 6.9**: 评审工程师打分，≥ 98 方可进入 Task 7

## Task 7: 表驱动与事件驱动架构一致性评审与修复

- [ ] **SubTask 7.1**: 验证 UI 组件类型、属性字段、校验规则由后端 `/api/registry/*` 端点动态读取，前端无硬编码字段类型分支
- [ ] **SubTask 7.2**: 验证 `TableDrivenPanel._renderPanel()` 通过 `field_config.type` 分发，组件通过 `ComponentRegistry` 注册
- [ ] **SubTask 7.3**: 验证 `ToolbarRenderer` 按钮显隐/可用状态由 `toolbar_config.json` 决定
- [ ] **SubTask 7.4**: 验证节点渲染参数（颜色/尺寸/字体）由 `cell_type_registry` + `dzh_type_map` 决定
- [ ] **SubTask 7.5**: 验证前端通过 SSE（`/api/events/stream`）/ WebSocket（`/api/config/ws`）/ `AppState.subscribe` / `CustomEvent` 接收后端事件并更新界面
- [ ] **SubTask 7.6**: 验证模块间无直接引用：`FlowCanvas` 通过 `onChange()` 回调，`TableDrivenPanel` 通过 `onChange()` 回调，`event-panel.js` 通过 `window.xxx` 桥接函数（`typeof window.xxx === 'function'` 守卫）
- [ ] **SubTask 7.7**: 验证事件订阅在 `destroy()` 中正确取消订阅（无内存泄漏），重点检查 `HighlightManager.destroy()`、`ConfigSync.disconnect()`、`VirtualScroller.destroy()`、`RuleEditor` 事件解绑
- [ ] **SubTask 7.8**: 评审工程师打分，≥ 98 方可进入 Task 8

## Task 8: 方法/属性/事件清单逐项验证与清理（依赖 Task 1-7）

- [ ] **SubTask 8.1**: 列出 `FlowCanvas`、`PoolDataManager`、`TableDrivenPanel`、`EventPanel`、`AppState`、`LRUCache`、`BaseChart`/`KlineChart`/`IndicatorChart`、`RuleEditor`、`ConfigSync`、`HighlightManager`、`ToolbarRenderer` 等核心对象的公开方法/属性/事件清单
- [ ] **SubTask 8.2**: Playwright 逐项调用每个公开方法，验证输入输出符合 spec，无未捕获异常
- [ ] **SubTask 8.3**: 逐项验证关键属性保持单一真相源，无冗余状态字段（如 `_nodes` 与 `nodeElements` 不同步、`selectedNodeId` 与 `selectedNodeIds` 语义重叠等）
- [ ] **SubTask 8.4**: 逐项验证事件回调正确订阅、触发和取消订阅
- [ ] **SubTask 8.5**: 删除未使用的冗余方法/属性/事件（junk code 零容忍），通过 ESLint 静态分析 + Playwright 运行时验证确认无回归
- [ ] **SubTask 8.6**: 评审工程师打分，≥ 98 方可进入 Task 9

## Task 9: 禁止兼容旧接口检查与回归验证（依赖 Task 1-8）

- [ ] **SubTask 9.1**: 全局搜索确认前端代码无 `get_node_stocks` / `set_node_stocks` 引用
- [ ] **SubTask 9.2**: 全局搜索确认前端代码无 `SimTickSource` 引用
- [ ] **SubTask 9.3**: 全局搜索确认前端代码无 `EdgeFired.changed_codes` 引用
- [ ] **SubTask 9.4**: 全局搜索确认运行时 `execution_order` 引用（节点类型字符串 `execution_order` 除外，编译期 `CompiledSchedule.execution_order` 字段除外）
- [ ] **SubTask 9.5**: 验证前端 API 调用使用 `StatePoolView` 视图接口，不直接操作 `node_stocks`
- [ ] **SubTask 9.6**: 运行 eventtest 173 个正反合测试，确认退出码 0
- [ ] **SubTask 9.7**: Playwright 验证所有核心场景通过（模式切换、仿真运行、事件接收、导入导出、综合设置）
- [ ] **SubTask 9.8**: 确认无回归（事件计数、池状态快照与基线一致）
- [ ] **SubTask 9.9**: 评审工程师打分，≥ 98 方可进入 Task 10

## Task 10: 更新 DESIGN.md 和 DESIGN0.md 设计文档（依赖 Task 1-9）

- [ ] **SubTask 10.1**: 由调度方（主 Agent）汇总 Task 1-9 的最终评分、修复清单、量化数据（事件计数/池状态快照）
- [ ] **SubTask 10.2**: 在 `DESIGN.md` 追加 §22 "前端迭代评审 V3 结果"章节，包含：① 各任务最终评分表 ② 修复清单（文件:行号） ③ 量化数据（事件计数/池状态快照） ④ Playwright 验证截图引用
- [ ] **SubTask 10.3**: 同步更新 `DESIGN0.md` 前端验证章节，确认表驱动/事件驱动一致性结论
- [ ] **SubTask 10.4**: 文档简洁清晰，无冗余文字，无重复表述
- [ ] **SubTask 10.5**: 评审工程师打分，≥ 98 方可结项

---

# Task Dependencies

- **Task 1** (画布引擎) → 无依赖
- **Task 2** (节点/边渲染) → 依赖 Task 1
- **Task 3** (属性面板) → 依赖 Task 1
- **Task 4** (模式切换) → 无依赖（可与 Task 1-3 并行）
- **Task 5** (仿真股票流转) → 依赖 Task 4
- **Task 6** (事件面板) → 依赖 Task 5
- **Task 7** (表/事件驱动一致性) → 依赖 Task 1, Task 3, Task 6
- **Task 8** (方法/属性/事件清单) → 依赖 Task 1-7
- **Task 9** (禁止旧接口 + 回归) → 依赖 Task 1-8
- **Task 10** (DESIGN.md 更新) → 依赖 Task 1-9

---

# 评分规则（评审工程师使用）

- 每个检查点通过得满分，部分通过按比例扣分，未通过扣全部分
- 任务总分 = 各检查点得分之和 / 检查点满分之和 × 100
- **门槛**：≥ 98 分方可进入下一任务；< 98 分打回架构工程师重做
- 评审工程师须给出：①总分 ②每项扣分理由 ③重做清单（若 < 98）
- 评审必须基于 Playwright 截图、浏览器控制台输出和 eventtest 量化结果，不得仅凭代码审查

---

# 并行策略

调度方可并行派发无依赖的任务对（如 Task 1+Task 4 可并行），但每个任务内部的"架构工程师修复 → 评审工程师打分"必须串行。同一时刻最多并行 2 个任务对，避免 sub-agent 上下文过载。
