# Tasks — 前端股票池界面综合完善与评审 V4

> **执行机制**：双工程师协作 + 98 分门槛。每个任务由调度方派发：架构工程师（sub-agent）修复 → 评审工程师（sub-agent）打分 → ≥98 进入下一任务 / <98 携扣分点打回重做。
>
> **源规范**：`specify-frontend-improvement`（已实现 18 个功能模块）
>
> **整合**：本 V4 计划整合并替代 `frontend-iterative-polish-v3`（Task 1 已完成 98/100）与 `frontend-stockpool-review-plan`（未启动）的剩余工作。Task 1 可直接复用 V3 评审结果，避免重复劳动。

## 执行机制

1. **架构工程师**（sub-agent_type=general_purpose_task）：依据 spec.md 修复对应模块，禁止兼容旧接口，禁止 workaround 掩盖 bug，禁止另开炉灶。
2. **评审工程师**（sub-agent_type=general_purpose_task）：依据 checklist.md 逐项打分，必须基于 Playwright 截图 + 浏览器控制台输出 + eventtest 量化结果，不得仅凭代码审查。
3. **门槛**：≥ 98 分进下一任务；< 98 分打回架构工程师重做（携带扣分点清单），直至 ≥ 98。
4. **依赖**：严格按 Task Dependencies 执行，前置任务未达 98 分不得开启后续任务。
5. 每个任务完成后，调度方在 tasks.md 勾选 `[x]`，在 checklist.md 勾选通过项。
6. **并行策略**：调度方可并行派发无依赖的任务对（如 Task 1+Task 4 可并行），但每个任务内部"架构工程师修复 → 评审工程师打分"必须串行。同一时刻最多并行 2 个任务对，避免 sub-agent 上下文过载。

---

## Task 1: 画布引擎（FlowCanvas）逐项评审与修复 ✅ 98/100

> **复用 V3 评审结果**：V3 中 Task 1 已完成 98/100，含 `destroy()` 方法新增、运行模式编辑禁用、6 处 `_runMode` 守卫。本任务直接复用，仅做快速回归验证即可。

- [x] **SubTask 1.1**: 验证 `FlowCanvas` 核心方法（render/setNodes/setEdges/fitToContent/setEdgeLineType/zoomIn/zoomOut/setZoom/selectNode/selectEdge/highlightNode/unhighlightNode）正确工作
- [x] **SubTask 1.2**: 验证关键属性单一真相源（`_nodes`/`_edges`/`transform`/`poolId`/`_runMode`/`selectedNodeId`/`selectedEdgeId`/`highlightedNodes`/`highlightedEdges`）
- [x] **SubTask 1.3**: 验证事件回调（onChange/onConnect/onNodeClick/onEdgeClick/onCanvasClick/onNodeDoubleClick/onNodeDragEnd/onZoomChangeCb）正确订阅、触发、取消订阅
- [x] **SubTask 1.4**: 验证 `destroy()` 方法（`canvas.js:1310-1359`）正确清理 RAF/document 监听/回调/缓存，无内存泄漏
- [x] **SubTask 1.5**: 验证运行模式（_runMode=true）下编辑功能正确禁用（拖拽/handle/框选/右键菜单均无效，实际 11 处 `_runMode` 守卫就位，超出 6 处要求）
- [x] **SubTask 1.6**: 评审工程师打分 98/100，通过（≥ 98 门槛）。扣分项：Playwright 替代验证扣 2 分（环境不可用）

## Task 2: 节点类型与边渲染逐项评审与修复 ✅ 98/100

- [x] **SubTask 2.1**: 验证所有节点类型按 `cell_type_registry` 配置正确渲染：DZH 200（stock_state_pool）/201（transfer_condition）/202（market_source）/4（discard_pool）、TDX 7（tdx_candidate）/8（tdx_state_pool）/3（tdx_condition）、`condition`（紫色圆角矩形 #8e44ad）、`text_label`/`container`/`state_column`/`execution_order`/`flow_arrow` 装饰节点
- [x] **SubTask 2.2**: 验证 `sim_test_pool_100` 池中 cond1/cond2/cond3 显示为紫色圆角矩形（#8e44ad），且 `_isConditionEdge` 正确识别字符串类型节点（含 `market_source`/`statepool`/`tdx_candidate`/`tdx_state_pool`）
- [x] **SubTask 2.3**: 验证筛选条件从 `edge.params` 读取（func.accode/indi/indiparam/filter_spec.evaluator_type/noperate/formula_ref），不从节点内部硬编码；点击 cond1/cond2/cond3 右侧属性面板正确打开并显示上述字段
- [x] **SubTask 2.4**: 验证边渲染：三种线形（贝兹/横竖/直线）正确 + 箭头按 `edge_strategies` 颜色 + 边标签显示时间间隔/条件名称（`_buildEdgeConditionSummary`）+ 执行顺序编号（`_execOrderCounter`）+ 多选入边顺序号正确显示
- [x] **SubTask 2.5**: 验证 `_getNodeCellType` 字符串节点类型到数字 cell type 反向映射（`STR_TO_CELL_TYPE`）正确
- [x] **SubTask 2.6**: Playwright 截图验证所有节点类型视觉效果 + 浏览器控制台无 JavaScript 错误（代码审查+Node.js语法检查替代验证，扣 1 分）
- [x] **SubTask 2.7**: 评审工程师打分 98/100，通过（≥ 98 门槛）。扣分项：Playwright 替代验证扣 2 分

## Task 3: 属性面板（TableDrivenPanel）逐项评审与修复 ✅ 98/100

- [x] **SubTask 3.1**: 验证 `TableDrivenPanel.showForNode(nodeId)`/`showForEdge(edgeId)`/`showForPool(poolMeta)` 根据选中对象动态生成表单 — ui.js:1075/1214/1270
- [x] **SubTask 3.2**: 验证 `_renderPanel(config)` 通过 `field_config.type` 分发，无硬编码字段类型分支 — ui.js:1602 通过 ComponentRegistry.get(field.comp) 分发
- [x] **SubTask 3.3**: 验证字段联动：`depends_on` 父字段变更时子字段正确显示/隐藏；`active_when` 条件表达式正确求值 — ui.js:1655-1665/4137-4213/4368-4374
- [x] **SubTask 3.4**: 验证 `ValidationEngine.validate()` 实时校验并显示错误信息 — ui.js:95-142/4493-4517
- [x] **SubTask 3.5**: 验证 `DataBinder.decodeAttrFlags()`/`encodeAttrFlags()` 位标志编解码正确 — ui.js:243-262
- [x] **SubTask 3.6**: 验证 DZH 颜色可视化：`dzhColorToCss()` 正确转换 20 色调色板索引 + BGR 直接色解码（高位=B, 中位=G, 低位=R）+ `renderDzhColorBadge` 颜色徽章在面板显示 — canvas.js:84-157/627-651
- [x] **SubTask 3.7**: 验证 20 种内置组件类型正常工作（实际注册 33 个，超出要求） — ui.js:307-904 全部存在
- [x] **SubTask 3.8**: 评审工程师打分 98/100，通过（≥ 98 门槛）。扣分项：Playwright 替代验证扣 2 分

## Task 4: 四种模式切换逐项评审与修复

- [ ] **SubTask 4.1**: 验证 `AppState.setMode(newMode)` 正确更新 mode 属性并通过 `_notify` 派发；`AppState.subscribe(callback)` 订阅者正确收到模式变更
- [ ] **SubTask 4.2**: 验证设计模式：画布可编辑、属性面板可用、事件面板隐藏（`hideEventPanel()` 调用）
- [ ] **SubTask 4.3**: 验证实盘模式：模式指示器变绿色（`#27ae60`），后端 `DataSourceContract` 探测逻辑生效
- [ ] **SubTask 4.4**: 验证回放模式：模式指示器变橙色（`#e67e22`），`IStorageQuery` 注入 `kline_cache`，回放面板显示
- [ ] **SubTask 4.5**: 验证仿真模式：模式指示器变紫色（`#9b59b6`），仿真面板显示，事件面板 `.visible` 类生效（`showEventPanel()` 调用）
- [ ] **SubTask 4.6**: 验证运行中切换模式被阻止并提示（toast 或 alert），模式切换不导致界面混乱（无残留面板、无重复控件）
- [ ] **SubTask 4.7**: 验证模式切换时 `clearEventPanel()` 清除旧事件（仿真模式启动新会话时）
- [ ] **SubTask 4.8**: Playwright 截图验证四种模式切换效果（4 张截图）+ 浏览器控制台无 JavaScript 错误
- [ ] **SubTask 4.9**: 评审工程师打分，≥ 98 方可进入 Task 5

## Task 5: 仿真模式股票流转逐项评审与修复（依赖 Task 4）

- [ ] **SubTask 5.1**: 验证仿真启动后虚拟时钟正确运行（`simulationTime` 递增），步数计数器正确递增，启动/暂停/步进/重置按钮工作正常，步长选择（1s/1min/5min/1h）和速度调节（0.5x - 20x）生效
- [ ] **SubTask 5.2**: 验证备选池加载 100 只 `fz` 前缀股票，每只股票 tick 间隔为 1-9 秒随机值（同股票间隔固定，不同股票间隔不同），加载进度显示正确
- [ ] **SubTask 5.3**: 验证股票经 cond1 进入 pool_A（触发频率 1min，转移条件 5min K线 KDJ 金叉，股票在 A 池停留 100 分钟后删除）
- [ ] **SubTask 5.4**: 验证股票经 cond2 进入 pool_B（触发频率 10s，转移条件 1min MACD 金叉，股票在 B 池停留 200 分钟后删除）
- [ ] **SubTask 5.5**: 验证 A∩B 经 cond3 进入 pool_C（触发频率 5s，取交集），入 C 池后立即市价买入 100 股，停留 20 分钟后出池并卖出所有持仓
- [ ] **SubTask 5.6**: eventtest 量化验证完整事件链：9 类事件计数（Tick/Bar/Formula/Edge/Transfer/Signal/Order/TTL/System）+ 池状态快照 + 定时器队列
- [ ] **SubTask 5.7**: 验证仿真运行 ≥ 300 秒虚拟时间后事件面板显示完整事件链
- [ ] **SubTask 5.8**: 评审工程师打分，≥ 98 方可进入 Task 6

## Task 6: 事件面板（EventPanel）逐项评审与修复（依赖 Task 5）

- [ ] **SubTask 6.1**: 验证事件面板按 9 类分类显示：Tick（📊 灰色）/Bar（📈 蓝色）/Formula（🧮 绿色）/Edge（⚡ 橙色）/Transfer（🔄 紫色）/Signal（💰 红色）/Order（📋 黄色）/TTL（⏰ 暗红色）/System（🔧 青色），图标和颜色正确
- [ ] **SubTask 6.2**: 验证已发生事件（实心图标）与排队中事件（黄色虚线框图标）区分显示
- [ ] **SubTask 6.3**: 验证定时器队列显示排队中的 timer 事件及预计触发时间（fire_at）、事件类型、股票代码、详情、queue_position；过期事件用红色虚线框标识；自动清理 60 秒前的过期项
- [ ] **SubTask 6.4**: 验证矩阵视图：时间轴水平方向（左→右），秒数标签在底部（实际时间如 '09:35:14' 使用 `formatTime(t)`），分类标签垂直排列在左侧（宽度 86px，半透明暗色背景 rgba(0,0,0,0.25)），NOW 红色垂直线，Canvas 字体包含 emoji 字体（'Segoe UI Emoji, Apple Color Emoji, Microsoft YaHei'）
- [ ] **SubTask 6.5**: 验证散点视图与矩阵视图同步统一标签宽度（86px）、标签区背景、网格线范围（横向网格线仅从 plotX 开始，不穿过标签区）、emoji 字体、显示实际时间（formatTime）
- [ ] **SubTask 6.6**: 验证点击分类行/单个事件图标在下方详情区显示事件文本记录
- [ ] **SubTask 6.7**: 验证事件面板默认隐藏（display:none），仅在仿真/回放模式通过 `.visible` 类显示，退出时正确隐藏；每次启动新仿真时 `clearEventPanel()` 清除旧事件（不累积之前会话数据）
- [ ] **SubTask 6.8**: 验证拖拽浮窗、折叠/展开、暂停/继续接收、清空事件功能正常，位置/状态保存到 localStorage（`STORAGE_KEY='metacore_event_panel_state_v3'`），刷新后恢复
- [ ] **SubTask 6.9**: 评审工程师打分，≥ 98 方可进入 Task 7

## Task 7: 表驱动与事件驱动架构一致性评审与修复

- [ ] **SubTask 7.1**: 验证 UI 组件类型、属性字段、校验规则由后端 `/api/registry/*` 端点动态读取（cell-types/modules/dzh-type-map/defaults/flow-modes/edge-strategies/field-definitions）
- [ ] **SubTask 7.2**: 验证 `TableDrivenPanel._renderPanel()` 通过 `field_config.type` 分发，组件通过 `ComponentRegistry` 注册（`registerFromConfig`），无硬编码字段类型分支
- [ ] **SubTask 7.3**: 验证 `ToolbarRenderer` 按钮显隐/可用状态由 `toolbar_config.json` 决定
- [ ] **SubTask 7.4**: 验证节点渲染参数（颜色/尺寸/字体）由 `cell_type_registry` + `dzh_type_map` 决定
- [ ] **SubTask 7.5**: 验证事件流：SSE（`/api/events/stream`）+ WebSocket（`/api/config/ws`）`ConfigSync` + `AppState.subscribe` 自定义 pub-sub + `document.dispatchEvent(new CustomEvent('configChanged', ...))` + `tdx:historyView` + `zoomchange` 自定义事件
- [ ] **SubTask 7.6**: 验证模块间无直接引用：`FlowCanvas` 通过 `onChange()` 回调，`TableDrivenPanel` 通过 `onChange()` 回调，`event-panel.js` 通过 `window.xxx` 桥接函数（`typeof window.xxx === 'function'` 守卫），`HighlightManager` 通过 `canvas.highlightNode()` 接口，`ConfigManager` 通过 API
- [ ] **SubTask 7.7**: 验证事件订阅在 `destroy()` 中正确取消订阅（无内存泄漏）：`HighlightManager.destroy()` 关闭 WebSocket + 清除定时器 + 取消 RAF；`ConfigSync.disconnect()` 关闭 WebSocket + 清除重连/ping 定时器；`VirtualScroller.destroy()`/`RuleEditor` 事件解绑/`TableDrivenPanel.destroy()`/`FlowCanvas.destroy()` 正确清理
- [ ] **SubTask 7.8**: 评审工程师打分，≥ 98 方可进入 Task 8

## Task 8: 方法/属性/事件清单逐项验证与清理（依赖 Task 1-7）

- [ ] **SubTask 8.1**: 列出核心对象公开方法/属性/事件清单：`FlowCanvas`、`PoolDataManager`、`TableDrivenPanel`、`EventPanel`、`AppState`、`LRUCache`、`BaseChart`/`KlineChart`/`IndicatorChart`、`RuleEditor`、`ConfigSync`、`HighlightManager`、`ToolbarRenderer`、`ComponentRegistry`、`ValidationEngine`、`DataBinder`、`FormulaEditor`、`ConfigManager`、`ComprehensiveSettings`
- [ ] **SubTask 8.2**: Playwright 逐项调用每个公开方法，验证输入输出符合 spec，无未捕获异常
- [ ] **SubTask 8.3**: 逐项验证关键属性保持单一真相源：`_nodes` 与 `nodeElements`（派生缓存）不同步、`selectedNodeId` 与 `selectedNodeIds`（单选 vs 多选）语义不重叠、`simulationState` 单一状态字段、`_data` 单一数据源无快照副本、`transform` (x,y,zoom) 单一状态
- [ ] **SubTask 8.4**: 逐项验证事件回调正确订阅、触发和取消订阅
- [ ] **SubTask 8.5**: 删除未使用的冗余方法/属性/事件/变量/导入（junk code 零容忍），通过 ESLint 静态分析 + Playwright 运行时验证确认无回归
- [ ] **SubTask 8.6**: 评审工程师打分，≥ 98 方可进入 Task 9

## Task 9: 禁止兼容旧接口检查与回归验证（依赖 Task 1-8）

- [ ] **SubTask 9.1**: 全局搜索确认前端代码无 `get_node_stocks` / `set_node_stocks` 引用（节点类型字符串除外）
- [ ] **SubTask 9.2**: 全局搜索确认前端代码无 `SimTickSource` 引用
- [ ] **SubTask 9.3**: 全局搜索确认前端代码无 `EdgeFired.changed_codes` 引用
- [ ] **SubTask 9.4**: 全局搜索确认运行时 `execution_order` 引用（节点类型字符串 `execution_order` + 编译期 `CompiledSchedule.execution_order` 字段除外）
- [ ] **SubTask 9.5**: 验证前端 API 调用使用 `StatePoolView` 视图接口，不直接操作 `node_stocks`；`PoolDataManager` 不暴露 `_data.node_stocks` 直接访问接口
- [ ] **SubTask 9.6**: 运行 eventtest 173 个正反合测试，确认退出码 0
- [ ] **SubTask 9.7**: Playwright 验证所有核心场景通过（模式切换 4 种 + 仿真运行 ≥300 秒 + 事件接收 9 类 + 导入导出 DZH/TDX/JSON + 综合设置三列布局）
- [ ] **SubTask 9.8**: 确认无回归（事件计数、池状态快照与基线一致），浏览器控制台无 JavaScript 错误，无未捕获 Promise rejection
- [ ] **SubTask 9.9**: 评审工程师打分，≥ 98 方可进入 Task 10

## Task 10: 更新 DESIGN.md 和 DESIGN0.md 设计文档（依赖 Task 1-9）

> **由调度方（主 Agent）执行**，无需派发 sub-agent。

- [ ] **SubTask 10.1**: 汇总 Task 1-9 的最终评分、修复清单、量化数据（事件计数/池状态快照/Playwright 截图引用）
- [ ] **SubTask 10.2**: 在 `DESIGN.md` 追加 §22 "前端迭代评审 V4 结果"章节，包含：① 各任务最终评分表（Task 1-10 分数 + 通过日期）② 修复清单（文件:行号格式）③ 量化数据（事件计数/池状态快照）④ Playwright 验证截图引用
- [ ] **SubTask 10.3**: 同步更新 `DESIGN0.md` 前端验证章节，确认表驱动/事件驱动一致性结论（含验证证据）
- [ ] **SubTask 10.4**: 文档简洁清晰，无冗余文字，无重复表述（与已有章节不冲突），引用具体的 Playwright 截图/eventtest 报告
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
- 评审必须基于 **Playwright 截图 + 浏览器控制台输出 + eventtest 量化结果**，不得仅凭代码审查
- **特殊说明**：若 Playwright 环境不可用，可采用"代码审查 + 语法检查 + HTTP 加载验证 + Node.js 测试脚本"作为替代验证手段，但每项替代验证扣 1 分（最高扣 5 分）

---

# 最终结项门槛

- 所有 10 个任务均 ≥ 98 分
- `tasks.md` 全部勾选 `[x]`
- `checklist.md` 全部勾选 `[x]`
- `DESIGN.md` §22 章节已添加
- `DESIGN0.md` 前端验证章节已同步更新
- 评审工程师给出最终结项报告（含总评分 + 各任务分数汇总表）
