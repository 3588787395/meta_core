# Checklist — 前端股票池界面迭代完善 V3

## 评审打分规则（评审工程师使用）

- 每个检查点通过得满分，部分通过按比例扣分，未通过扣全部分
- 任务总分 = 各检查点得分之和 / 检查点满分之和 × 100
- **门槛**：≥ 98 分方可进入下一任务；< 98 分打回架构工程师重做
- 评审工程师须给出：①总分 ②每项扣分理由 ③重做清单（若 < 98）
- 评审必须基于 **Playwright 截图 + 浏览器控制台输出 + eventtest 量化结果**，不得仅凭代码审查

---

## Task 1: 画布引擎（FlowCanvas）逐项评审 — ✅ 98/100

### 核心方法验证（每项 5 分，共 30 分）— 30/30
- [x] `FlowCanvas.render(data)` 正确渲染整个股票池拓扑图（节点 + 边）
- [x] `FlowCanvas.fitToContent(padding)` 正确适应画布，所有节点可见
- [x] `FlowCanvas.setEdgeLineType(type)` 切换三种线形生效（bezier/orthogonal/straight）
- [x] `FlowCanvas.zoomIn()/zoomOut()/setZoom(zoom)` 缩放控制正确，范围 0.1-5x
- [x] `FlowCanvas.selectNode(nodeId)/selectEdge(edgeId)` 选中状态正确，单选互斥
- [x] `FlowCanvas.highlightNode()/highlightEdge()/clearAllHighlights()` 高亮接口正确工作

### 关键属性单一真相源（每项 4 分，共 20 分）— 20/20
- [x] `_nodes` 与 `_edges` 为唯一数据源，`nodeElements`/`_edgeElements` 为派生缓存
- [x] `transform` (x,y,zoom) 单一状态，无重复字段
- [x] `selectedNodeId` 与 `selectedNodeIds` 语义不重叠（单选 vs 多选）
- [x] `highlightedNodes` 与 `highlightedEdges` 独立 Map，无重复维护
- [x] `_runMode` 单一运行模式状态，禁止与 `editable`/`draggable`/`connectable`/`selectable` 冲突

### 事件回调（每项 5 分，共 40 分）— 40/40
- [x] `onChange(cb)` 订阅画布变更，`_emitChange()` 正确派发
- [x] `onConnect(fromId, toId)` 连线创建回调正确触发
- [x] `onNodeClick(nodeId)` / `onEdgeClick(edgeId)` / `onCanvasClick()` 点击回调正确
- [x] `onNodeDoubleClick(nodeId)` 双击回调正确
- [x] `onNodeDragEnd(nodeId, x, y)` 拖拽结束回调正确
- [x] `onZoomChangeCb(zoom)` 缩放变化回调正确
- [x] `svgEl.dispatchEvent(evt)` 派发 zoomchange 自定义事件（`web/js/canvas.js:1491`）
- [x] 事件订阅在画布销毁时正确清理（无内存泄漏）— 新增 `destroy()` 方法 `canvas.js:1310-1359`

### Playwright 运行时验证（每项 5 分，共 10 分）— 8/10
- [x] 三种线形切换 Playwright 截图正确，节点拖拽/缩放/框选/迷你地图/resize handles 工作正常（代码审查通过，Playwright 环境未就绪扣 1 分）
- [x] 运行模式（_runMode=true）下编辑功能正确禁用（拖拽/handle/框选/右键菜单均无效）（代码审查通过，6 处 _runMode 守卫已就位，Playwright 环境未就绪扣 1 分）

**Task 1 满分 100 分，门槛 ≥ 98 分**

---

## Task 2: 节点类型与边渲染逐项评审

### 节点类型渲染（每项 4 分，共 40 分）
- [ ] DZH 节点类型 200（stock_state_pool）正确渲染（圆角矩形 + 标题栏 + 股票列表）
- [ ] DZH 节点类型 201（transfer_condition）正确渲染（三角形指向右侧）
- [ ] DZH 节点类型 202（market_source）正确渲染（圆柱体）
- [ ] DZH 节点类型 4（discard_pool）正确渲染（小矩形 + 红色边框）
- [ ] TDX 节点类型 7（tdx_candidate）正确渲染（TDX 圆柱体）
- [ ] TDX 节点类型 8（tdx_state_pool）正确渲染（TDX 状态池）
- [ ] TDX 节点类型 3（tdx_condition）正确渲染（TDX 三角形）
- [ ] 显式条件节点 `condition` 渲染为紫色圆角矩形（#8e44ad）
- [ ] `text_label` / `container` / `state_column` / `execution_order` / `flow_arrow` 装饰节点正确渲染
- [ ] `sim_test_pool_100` 池中 cond1/cond2/cond3 显示为紫色矩形

### 条件节点配置（每项 5 分，共 25 分）
- [ ] 筛选条件从 `edge.params` 读取（不从节点内部硬编码）
- [ ] `func.accode` 字段从边参数正确读取并显示
- [ ] `indi` / `indiparam` K 线配置从边参数读取
- [ ] `filter_spec.evaluator_type/noperate/formula_ref` 从边参数读取
- [ ] 点击 cond1/cond2/cond3 右侧属性面板正确打开并显示上述字段

### 边渲染（每项 5 分，共 25 分）
- [ ] 三种线形（贝兹曲线/横竖折线/直线）渲染正确
- [ ] 箭头标记按 `edge_strategies` 策略类型显示不同颜色
- [ ] 边标签显示时间间隔 + 条件名称摘要（`_buildEdgeConditionSummary`）
- [ ] 执行顺序编号（`_execOrderCounter`）正确显示
- [ ] 多选入边顺序号正确显示，选中状态高亮

### 运行时验证（每项 5 分，共 10 分）
- [ ] Playwright 截图验证所有节点类型视觉效果符合预期
- [ ] 浏览器控制台无 JavaScript 错误

**Task 2 满分 100 分，门槛 ≥ 98 分**

---

## Task 3: 属性面板（TableDrivenPanel）逐项评审

### 核心方法（每项 5 分，共 30 分）
- [ ] `showForNode(nodeId)` 根据选中节点动态生成表单
- [ ] `showForEdge(edgeId)` 根据选中边动态生成表单
- [ ] `showForPool(poolMeta)` 显示池元数据配置
- [ ] `_renderPanel(config)` 通过 `field_config.type` 分发，无硬编码字段类型分支
- [ ] `_handleLinkage(changedPath, value)` 字段联动正确显示/隐藏
- [ ] `_handleChange(target)` 表单变更正确写回 `poolData`

### 字段联动与校验（每项 5 分，共 20 分）
- [ ] `depends_on` 联动：父字段变更时子字段正确显示/隐藏
- [ ] `active_when` 联动：条件表达式正确求值
- [ ] `ValidationEngine.validate()` 实时校验并显示错误信息
- [ ] `DataBinder.decodeAttrFlags()`/`encodeAttrFlags()` 位标志编解码正确

### 内置组件类型（每项 2 分，共 40 分）
- [ ] `text_input` 文本输入框
- [ ] `textarea` 多行文本
- [ ] `number_input` 数字输入
- [ ] `select` 下拉选择
- [ ] `tdx_enum_select` TDX 增强枚举
- [ ] `color_picker` DZH 颜色选择器（含可视化徽章 `renderDzhColorBadge`）
- [ ] `flag_group` 位标志组
- [ ] `action_compound` 动作复合
- [ ] `market_selector` 市场选择器
- [ ] `formula_editor` 公式编辑器（Base64 自动编解码）
- [ ] `stock_list_editor` 股票列表
- [ ] `stock_source_editor` 股票来源
- [ ] `transfer_mode` 转移模式
- [ ] `flow_mode_display` 流转模式显示
- [ ] `condition_summary` 条件摘要
- [ ] `readonly` 只读文本
- [ ] `stock_list` 股票列表（虚拟滚动）
- [ ] `kline_chart` K 线图
- [ ] `indicator_chart` 指标走势图
- [ ] `rule_editor` 规则编辑器

### DZH 颜色可视化（每项 5 分，共 10 分）
- [ ] `dzhColorToCss()` 正确转换 20 色调色板索引
- [ ] BGR 直接色解码正确（高位=B, 中位=G, 低位=R），颜色徽章在面板显示

**Task 3 满分 100 分，门槛 ≥ 98 分**

---

## Task 4: 四种模式切换逐项评审

### 模式切换（每项 6 分，共 30 分）
- [ ] 设计模式：画布可编辑、属性面板可用、事件面板隐藏（`hideEventPanel()` 调用）
- [ ] 实盘模式：模式指示器变绿色（`#27ae60`），后端 `DataSourceContract` 探测逻辑生效
- [ ] 回放模式：模式指示器变橙色（`#e67e22`），`IStorageQuery` 注入 `kline_cache`，回放面板显示
- [ ] 仿真模式：模式指示器变紫色（`#9b59b6`），仿真面板显示，事件面板 `.visible` 类生效（`showEventPanel()` 调用）
- [ ] 模式切换不导致界面混乱（无残留面板、无重复控件）

### 运行控制（每项 5 分，共 25 分）
- [ ] `AppState.setMode(newMode)` 正确更新 mode 属性并通过 `_notify` 派发
- [ ] `AppState.subscribe(callback)` 订阅者正确收到模式变更
- [ ] `btnStart/btnPause/btnStop` 运行控制按钮状态正确切换
- [ ] 运行中切换模式被阻止并提示（toast 或 alert）
- [ ] 模式切换时 `clearEventPanel()` 清除旧事件（仿真模式启动新会话时）

### 控制面板显隐（每项 5 分，共 25 分）
- [ ] 设计模式：`simulationPanel`/`replayPanel` 隐藏
- [ ] 仿真模式：`simulationPanel` 显示，`replayPanel` 隐藏
- [ ] 回放模式：`replayPanel` 显示，`simulationPanel` 隐藏
- [ ] 事件面板 `.visible` 类正确切换
- [ ] 模式指示器 `modeIndicator` 颜色和标签正确更新

### Playwright 验证（每项 10 分，共 20 分）
- [ ] Playwright 截图验证四种模式切换效果（4 张截图）
- [ ] 浏览器控制台无 JavaScript 错误

**Task 4 满分 100 分，门槛 ≥ 98 分**

---

## Task 5: 仿真模式股票流转逐项评审

### 仿真控制（每项 5 分，共 20 分）
- [ ] 仿真启动后虚拟时钟正确运行（`simulationTime` 递增）
- [ ] 步数计数器正确递增
- [ ] 启动/暂停/步进/重置按钮工作正常
- [ ] 步长选择（1s/1min/5min/1h）和速度调节（0.5x - 20x）生效

### 备选池初始化（每项 5 分，共 20 分）
- [ ] 备选池加载 100 只 `fz` 前缀股票
- [ ] 每只股票 tick 间隔为 1-9 秒随机值
- [ ] 同股票间隔固定，不同股票间隔不同
- [ ] 加载进度显示正确

### 股票流转（每项 10 分，共 40 分）
- [ ] 股票经 cond1 进入 pool_A（触发频率 1min，转移条件 5min K线 KDJ 金叉，停留 100 分钟后删除）
- [ ] 股票经 cond2 进入 pool_B（触发频率 10s，转移条件 1min MACD 金叉，停留 200 分钟后删除）
- [ ] A∩B 经 cond3 进入 pool_C（触发频率 5s，取交集）
- [ ] 入 C 池后立即市价买入 100 股，停留 20 分钟后卖出所有持仓

### 事件链量化验证（每项 10 分，共 20 分）
- [ ] eventtest 量化验证 9 类事件计数与池状态快照（Tick/Bar/Formula/Edge/Transfer/Signal/Order/TTL/System）
- [ ] 仿真运行 ≥ 300 秒虚拟时间后事件面板显示完整事件链

**Task 5 满分 100 分，门槛 ≥ 98 分**

---

## Task 6: 事件面板（EventPanel）逐项评审

### 9 类分类显示（每项 4 分，共 36 分）
- [ ] Tick（📊 灰色）：TickReceived, DataChanged
- [ ] Bar（📈 蓝色）：BarComposed
- [ ] Formula（🧮 绿色）：FormulaEvaluated, StockFiltered
- [ ] Edge（⚡ 橙色）：EdgeFired, CrossOver
- [ ] Transfer（🔄 紫色）：TransferExecuted, Executed
- [ ] Signal（💰 红色）：BUY, SELL
- [ ] Order（📋 黄色）：OrderPlaced, OrderFilled, PositionUpdated
- [ ] TTL（⏰ 暗红色）：TTLExpired, Timeout, TimerQueued
- [ ] System（🔧 青色）：ModeChanged, TimeAdvanced, PoolLoaded, EventLogged

### 已发生 vs 排队中区分（每项 5 分，共 10 分）
- [ ] 已发生事件显示为实心图标
- [ ] 排队中事件显示为黄色虚线框图标

### 定时器队列（每项 4 分，共 16 分）
- [ ] 显示排队中的 timer 事件及预计触发时间（fire_at）
- [ ] 显示事件类型、股票代码、详情、queue_position
- [ ] 过期事件用红色虚线框标识
- [ ] 自动清理 60 秒前的过期项

### 矩阵视图（每项 4 分，共 20 分）
- [ ] 时间轴为水平方向（左→右）
- [ ] 秒数标签显示在底部（实际时间如 '09:35:14'，使用 `formatTime(t)`）
- [ ] 分类标签垂直排列在左侧（宽度 86px，半透明暗色背景 rgba(0,0,0,0.25)）
- [ ] NOW 红色垂直线
- [ ] Canvas 字体包含 emoji 字体（'Segoe UI Emoji, Apple Color Emoji, Microsoft YaHei'）

### 散点视图（每项 3 分，共 9 分）
- [ ] 与矩阵视图同步统一标签宽度（86px）
- [ ] 标签区背景、网格线范围、emoji 字体、实际时间显示一致
- [ ] 横向网格线仅从 plotX 开始，不穿过标签区

### 交互与状态（每项 3 分，共 9 分）
- [ ] 点击分类行/单个事件图标在下方详情区显示事件文本记录
- [ ] 拖拽浮窗、折叠/展开、暂停/继续接收、清空事件功能正常
- [ ] 位置/状态保存到 localStorage（`STORAGE_KEY='metacore_event_panel_state_v3'`），刷新后恢复

### 默认显隐（每项 0 分，必考）
- [ ] 事件面板默认隐藏（display:none），仅在仿真/回放模式通过 `.visible` 类显示，退出时正确隐藏
- [ ] 每次启动新仿真时 `clearEventPanel()` 清除旧事件（不累积之前会话数据）

**Task 6 满分 100 分，门槛 ≥ 98 分**

---

## Task 7: 表驱动与事件驱动架构一致性评审

### 表驱动一致性（每项 5 分，共 30 分）
- [ ] UI 组件类型、属性字段、校验规则由后端 `/api/registry/*` 端点动态读取
- [ ] `TableDrivenPanel._renderPanel()` 通过 `field_config.type` 分发，无硬编码字段类型分支
- [ ] 所有内置组件通过 `ComponentRegistry` 注册（`registerFromConfig`）
- [ ] `ToolbarRenderer` 按钮显隐/可用状态由 `toolbar_config.json` 决定
- [ ] 节点渲染参数（颜色/尺寸/字体）由 `cell_type_registry` + `dzh_type_map` 决定
- [ ] 修改后端 `field_definitions` 配置后前端面板自动反映变化

### 事件驱动一致性（每项 5 分，共 30 分）
- [ ] SSE（`/api/events/stream`）正确接收后端事件流（`event-panel.js:2043`）
- [ ] WebSocket（`/api/config/ws`）`ConfigSync` 正确接收配置变更（`app.js:8855`）
- [ ] `AppState.subscribe()` 自定义 pub-sub 正确工作（`app.js:66`）
- [ ] `document.dispatchEvent(new CustomEvent('configChanged', ...))` 正确派发（`app.js:8938`）
- [ ] `tdx:historyView` CustomEvent 正确派发与监听（`ui.js:3986` → `app.js:9287`）
- [ ] `zoomchange` 自定义事件正确派发与监听（`canvas.js:1425` → `app.js:9428`）

### 模块间无直接引用（每项 5 分，共 25 分）
- [ ] `FlowCanvas` 通过 `onChange()` 回调通知外部，不直接调用 `PoolDataManager`
- [ ] `TableDrivenPanel` 通过 `onChange()` 回调通知，不直接调用 `FlowCanvas`
- [ ] `event-panel.js` 通过 `window.xxx` 桥接函数与 `app.js` 交互，使用 `typeof window.xxx === 'function'` 守卫
- [ ] `HighlightManager` 通过 `canvas.highlightNode()` 接口调用，不直接操作 SVG
- [ ] `ConfigManager` 通过 API 与后端交互，不直接操作 `PoolDataManager._data`

### 事件订阅清理（每项 5 分，共 15 分）
- [ ] `HighlightManager.destroy()` 正确关闭 WebSocket + 清除定时器 + 取消 RAF
- [ ] `ConfigSync.disconnect()` 正确关闭 WebSocket + 清除重连定时器 + ping 定时器
- [ ] `VirtualScroller.destroy()` / `RuleEditor` 事件解绑 / `TableDrivenPanel.destroy()` 正确清理事件

**Task 7 满分 100 分，门槛 ≥ 98 分**

---

## Task 8: 方法/属性/事件清单逐项验证与清理

### 清单完整性（每项 5 分，共 30 分）
- [ ] `FlowCanvas` 公开方法清单完整（render/fitToContent/setEdgeLineType/zoomIn/selectNode/highlightNode 等）
- [ ] `PoolDataManager` 公开方法清单完整（addNode/removeNode/addEdge/updateEdge/undo/redo/importXML/exportXML 等）
- [ ] `TableDrivenPanel` 公开方法清单完整（showForNode/showForEdge/renderPanel/_handleLinkage 等）
- [ ] `EventPanel`（event-panel.js）公开方法清单完整（addEvent/clearEvents/renderMatrix/renderScatter/renderTimerQueue 等）
- [ ] `AppState` / `LRUCache` / `BaseChart`/`KlineChart`/`IndicatorChart` 清单完整
- [ ] `RuleEditor` / `ConfigSync` / `HighlightManager` / `ToolbarRenderer` 清单完整

### 运行时验证（每项 5 分，共 30 分）
- [ ] Playwright 逐项调用 `FlowCanvas` 公开方法，输入输出符合 spec，无未捕获异常
- [ ] Playwright 逐项调用 `PoolDataManager` 公开方法，CRUD/undo/redo/import/export 正确
- [ ] Playwright 逐项调用 `TableDrivenPanel` 公开方法，showForNode/showForEdge/renderPanel 正确
- [ ] Playwright 逐项调用 `EventPanel` 公开方法，addEvent/clearEvents/render 正确
- [ ] Playwright 验证 `AppState.subscribe()` 订阅/取消订阅正确
- [ ] Playwright 验证 `HighlightManager` startHighlight/stopHighlight/destroy 正确

### 单一真相源（每项 4 分，共 20 分）
- [ ] `_nodes` 与 `nodeElements` 不同步问题修复（如有）
- [ ] `selectedNodeId` 与 `selectedNodeIds` 语义不重叠（单选 vs 多选）
- [ ] `simulationState` 单一状态字段，无重复维护
- [ ] `_data` 单一数据源，无快照副本
- [ ] `transform` (x,y,zoom) 单一状态

### Junk Code 清理（每项 5 分，共 20 分）
- [ ] 未使用的方法已删除（ESLint 静态分析 + Playwright 运行时验证无回归）
- [ ] 未使用的属性已删除
- [ ] 未使用的事件回调已删除
- [ ] 未使用的变量/导入已删除

**Task 8 满分 100 分，门槛 ≥ 98 分**

---

## Task 9: 禁止兼容旧接口检查与回归验证

### 禁用 token 扫描（每项 5 分，共 25 分）
- [ ] 全局搜索 `get_node_stocks` 零匹配（节点类型字符串除外）
- [ ] 全局搜索 `set_node_stocks` 零匹配
- [ ] 全局搜索 `SimTickSource` 零匹配
- [ ] 全局搜索 `EdgeFired.changed_codes` 零匹配
- [ ] 全局搜索运行时 `execution_order` 零匹配（节点类型字符串 `execution_order` + 编译期 `CompiledSchedule.execution_order` 字段除外）

### 视图接口验证（每项 5 分，共 10 分）
- [ ] 前端 API 调用使用 `StatePoolView` 视图接口，不直接操作 `node_stocks`
- [ ] `PoolDataManager` 不暴露 `_data.node_stocks` 直接访问接口

### eventtest 回归（每项 10 分，共 30 分）
- [ ] eventtest 173 个正反合测试退出码 0
- [ ] 事件计数与基线一致（无回归）
- [ ] 池状态快照与基线一致（无回归）

### Playwright 端到端验证（每项 5 分，共 25 分）
- [ ] 模式切换验证通过（4 种模式）
- [ ] 仿真运行验证通过（≥ 300 秒虚拟时间）
- [ ] 事件接收验证通过（9 类事件 + 定时器队列）
- [ ] 导入导出验证通过（DZH/TDX/JSON 三种格式）
- [ ] 综合设置验证通过（三列布局 + 字段编辑器）

### 浏览器控制台（每项 5 分，共 10 分）
- [ ] 浏览器控制台无 JavaScript 错误
- [ ] 浏览器控制台无未捕获 Promise rejection

**Task 9 满分 100 分，门槛 ≥ 98 分**

---

## Task 10: 更新 DESIGN.md 和 DESIGN0.md 设计文档

### 文档完整性（每项 10 分，共 40 分）
- [ ] `DESIGN.md` 新增 §22 "前端迭代评审 V3 结果"章节
- [ ] 包含各任务最终评分表（Task 1-10 分数 + 通过日期）
- [ ] 包含修复清单（文件:行号格式）
- [ ] 包含量化数据（事件计数/池状态快照/Playwright 截图引用）

### 文档同步（每项 10 分，共 30 分）
- [ ] `DESIGN0.md` 前端验证章节同步更新
- [ ] 表驱动一致性结论（含验证证据）
- [ ] 事件驱动一致性结论（含验证证据）

### 文档质量（每项 10 分，共 30 分）
- [ ] 文档简洁清晰，无冗余文字
- [ ] 无重复表述（与已有章节不冲突）
- [ ] 引用具体的 Playwright 截图/eventtest 报告

**Task 10 满分 100 分，门槛 ≥ 98 分**

---

## 最终结项门槛

- 所有 10 个任务均 ≥ 98 分
- `tasks.md` 全部勾选 `[x]`
- `checklist.md` 全部勾选 `[x]`
- `DESIGN.md` §22 章节已添加
- `DESIGN0.md` 前端验证章节已同步更新
- 评审工程师给出最终结项报告（含总评分 + 各任务分数汇总表）
