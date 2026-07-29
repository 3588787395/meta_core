# 前端股票池界面综合完善与评审 V4 Spec

## Why

`specify-frontend-improvement` 已实现前端 18 个功能模块（FR-1 ~ FR-13），覆盖画布引擎、节点渲染、属性面板、四种运行模式、仿真股票流转、事件面板、配置中心、公式管理等核心能力。然而：

1. **未经过严格逐项评审**：现有功能仅完成实现，缺少双工程师协作的量化评审闭环。
2. **存在遗留问题**：包括 FlowCanvas 内存泄漏、运行模式编辑未完全禁用、条件边识别错误、字符串节点类型映射缺失等历史 Bug。
3. **架构一致性未验证**：表驱动（`cell_type_registry`/`field_definitions`/`edge_strategies`）与事件驱动（SSE/WebSocket/AppState.subscribe）原则需逐项核验。
4. **方法/属性/事件清单未盘点**：核心对象公开 API 清单缺失，未清理冗余代码。
5. **旧接口兼容代码残留**：`get_node_stocks`/`set_node_stocks`/`SimTickSource`/`EdgeFired.changed_codes`/运行时 `execution_order` 等需彻底清除。

本规范建立"架构工程师修复 + 评审工程师打分（≥98 分门槛）"的双轨协作流程，对前端界面逐项完善与评审，确保所有股票池功能正确、简洁，符合表驱动事件驱动架构。

## What Changes

- 基于 `specify-frontend-improvement` 已实现的 18 个功能模块，采用双工程师协作流程逐项评审。
- 由调度方（主 Agent）编排：架构工程师（sub-agent）修复 → 评审工程师（sub-agent）打分 → ≥98 进入下一任务 / <98 携扣分点打回重做。
- 重点验证：
  - **画布引擎 FlowCanvas**：核心方法、关键属性单一真相源、事件回调订阅/触发/取消订阅。
  - **节点类型与边渲染**：DZH 200/201/202/4、TDX 7/8/3、`condition` 紫色矩形、边线形/箭头/标签/执行顺序编号。
  - **属性面板 TableDrivenPanel**：表驱动渲染、字段联动、实时校验、位标志编解码、DZH 颜色可视化、20 种内置组件。
  - **四种模式切换**：设计/实盘/回放/仿真，模式指示器颜色、控制面板显隐、运行中切换阻止、事件面板默认显隐。
  - **仿真股票流转**：100 只 fz 股票、1-9 秒随机 tick 间隔、cond1/pool_A、cond2/pool_B、cond3/pool_C 买入卖出。
  - **事件面板 EventPanel**：9 类分类显示、已发生 vs 排队中、定时器队列、矩阵/散点视图、默认显隐、本地存储。
  - **表驱动与事件驱动架构一致性**：UI 组件/字段/校验由后端配置表决定、SSE/WebSocket/AppState/CustomEvent 事件流、模块间无直接引用。
  - **方法/属性/事件清单验证**：核心对象公开 API 盘点、单一真相源核验、junk code 清理。
  - **禁止兼容旧接口**：扫描 `get_node_stocks`/`set_node_stocks`/`SimTickSource`/`EdgeFired.changed_codes`/运行时 `execution_order`，使用 `StatePoolView` 视图接口。
- 由调度方在 Task 10 汇总各任务评分、修复清单、量化数据（事件计数/池状态快照），追加 `DESIGN.md` §22 章节，同步 `DESIGN0.md` 前端验证章节。

## Impact

- **前端代码**：`web/index.html`、`web/js/canvas.js`、`web/js/ui.js`、`web/js/app.js`、`web/js/event-panel.js`、`web/css/styles.css`
- **后端 API**：前端调用的所有 `/api/*` 端点（无修改，仅验证）
- **实例配置**：`config/pools/sim_test_pool_100.json`（仅验证，不修改）
- **文档**：`DESIGN.md`（追加 §22 章节）、`DESIGN0.md`（同步前端验证章节）
- **规范文档**：本 spec 目录下的 `spec.md`/`tasks.md`/`checklist.md`
- **历史规范**：整合并替代 `frontend-iterative-polish-v3`（Task 1 已完成 98/100）与 `frontend-stockpool-review-plan`（未启动）的剩余工作

---

## ADDED Requirements

### Requirement: 双工程师迭代评审流程

系统 SHALL 采用"架构工程师修复 + 评审工程师打分"的双轨流程，由调度方（主 Agent）编排：

1. **架构工程师**（sub-agent_type=general_purpose_task）：依据 spec.md 修复评审发现的问题，禁止兼容旧接口，禁止 workaround 掩盖 bug，禁止另开炉灶。
2. **评审工程师**（sub-agent_type=general_purpose_task）：依据 checklist.md 逐项验证，给出 0-100 分及扣分理由，必须基于 Playwright 截图 + 浏览器控制台输出 + eventtest 量化结果，不得仅凭代码审查。
3. **门槛**：≥ 98 分方可进入下一任务；< 98 分打回架构工程师重做（携带扣分点），直到 ≥ 98。
4. **依赖**：严格按 Task Dependencies 执行，前置任务未达 98 分不得开启后续任务。
5. **并行策略**：调度方可并行派发无依赖的任务对（如 Task 1+Task 4），但每个任务内部"架构工程师修复 → 评审工程师打分"必须串行。同一时刻最多并行 2 个任务对。

#### Scenario: 任务通过门槛

- **WHEN** 评审工程师对某任务给出 ≥ 98 分
- **THEN** 调度方在 `tasks.md` 勾选 `[x]`，在 `checklist.md` 勾选通过项
- **AND** 调度方派发下一任务给架构工程师

#### Scenario: 任务未达门槛

- **WHEN** 评审工程师对某任务给出 < 98 分
- **THEN** 调度方将扣分点清单派发给架构工程师重做
- **AND** 架构工程师修复后再次提交评审，循环直到 ≥ 98 分

### Requirement: 前端核心模块逐项评审

系统 SHALL 对前端核心模块逐项评审，确保每个功能正确、简洁、符合表驱动与事件驱动架构。

#### Scenario: 画布引擎评审

- **WHEN** 评审工程师审查画布引擎代码与运行效果
- **THEN** `FlowCanvas` 的核心方法（render/fitToContent/setEdgeLineType/zoomIn/selectNode/highlightNode 等）正确工作
- **AND** 关键属性保持单一真相源（`_nodes`/`_edges`/`transform`/`_runMode`/`selectedNodeId`）
- **AND** 事件回调正确订阅、触发、取消订阅（含 `destroy()` 清理无内存泄漏）
- **AND** 运行模式下编辑功能正确禁用（拖拽/handle/框选/右键菜单）

#### Scenario: 节点类型与边渲染评审

- **WHEN** 评审工程师审查节点渲染与边绘制
- **THEN** 所有节点类型按 `cell_type_registry` 配置正确渲染（DZH 200/201/202/4、TDX 7/8/3、`condition` 紫色矩形、装饰节点）
- **AND** 转移条件节点（cond1/cond2/cond3）显示为紫色圆角矩形（#8e44ad）
- **AND** 筛选条件从 `edge.params` 读取（func.accode/indi/indiparam/filter_spec），不从节点内部硬编码
- **AND** 边渲染（贝兹/横竖/直线）+ 箭头按 `edge_strategies` 颜色 + 边标签显示时间间隔/条件名称 + 执行顺序编号

#### Scenario: 属性面板表驱动评审

- **WHEN** 评审工程师审查 `TableDrivenPanel`
- **THEN** `showForNode/showForEdge/showForPool` 根据选中对象动态生成表单
- **AND** 字段联动（`depends_on`/`active_when`）正常显示/隐藏
- **AND** `ValidationEngine` 实时校验并显示错误信息
- **AND** `DataBinder.decodeAttrFlags`/`encodeAttrFlags` 位标志编解码正确
- **AND** DZH 颜色可视化（`renderDzhColorBadge`）正确显示颜色徽章
- **AND** 20 种内置组件类型正常工作

#### Scenario: 四种模式切换评审

- **WHEN** 用户在设计/实盘/回放/仿真模式间切换
- **THEN** 模式指示器颜色和标签正确更新（设计=蓝/实盘=绿/回放=橙/仿真=紫）
- **AND** 对应控制面板正确显示/隐藏（simulationPanel/replayPanel）
- **AND** 事件面板默认隐藏（display:none），仅仿真/回放模式 `.visible` 类生效
- **AND** 运行中切换模式被阻止并提示，模式切换不导致界面混乱

#### Scenario: 仿真模式股票流转评审

- **WHEN** 切换到仿真模式并启动
- **THEN** 备选池加载 100 只 `fz` 前缀股票，每只股票 tick 间隔为 1-9 秒随机值（同股票间隔固定，不同股票间隔不同）
- **AND** 股票经 cond1 进入 pool_A（触发频率 1min，转移条件 5min K线 KDJ 金叉，停留 100 分钟后删除）
- **AND** 股票经 cond2 进入 pool_B（触发频率 10s，转移条件 1min MACD 金叉，停留 200 分钟后删除）
- **AND** A∩B 经 cond3 进入 pool_C（触发频率 5s，取交集），入 C 池后立即市价买入 100 股，停留 20 分钟后卖出
- **AND** 事件面板显示完整事件链（9 类事件 + 定时器队列）

#### Scenario: 事件面板评审

- **WHEN** 仿真运行产生事件
- **THEN** 事件面板按 9 类分类显示（Tick/Bar/Formula/Edge/Transfer/Signal/Order/TTL/System），图标和颜色正确
- **AND** 已发生事件（实心图标）与排队中事件（黄色虚线框）区分显示
- **AND** 定时器队列显示预计触发时间（fire_at）、事件类型、股票代码、详情、queue_position
- **AND** 矩阵视图时间轴水平方向（左→右），秒数标签在底部（实际时间如 '09:35:14'），分类标签垂直排列在左侧（宽度 86px，半透明暗色背景 rgba(0,0,0,0.25)），NOW 红色垂直线
- **AND** 散点视图与矩阵视图同步统一标签宽度、标签区背景、网格线范围、emoji 字体、实际时间显示
- **AND** 事件面板默认隐藏，仅仿真/回放模式 `.visible` 类生效，每次启动新仿真 `clearEventPanel()` 清除旧事件

### Requirement: 表驱动与事件驱动架构一致性

系统 SHALL 验证前端架构符合表驱动与事件驱动原则。

#### Scenario: 表驱动一致性

- **WHEN** 评审工程师审查前端架构
- **THEN** UI 组件类型、属性字段、校验规则由后端 `/api/registry/*` 端点动态读取
- **AND** `TableDrivenPanel._renderPanel()` 通过 `field_config.type` 分发，无硬编码字段类型分支
- **AND** 所有内置组件通过 `ComponentRegistry` 注册（`registerFromConfig`）
- **AND** `ToolbarRenderer` 按钮显隐/可用状态由 `toolbar_config.json` 决定
- **AND** 节点渲染参数（颜色/尺寸/字体）由 `cell_type_registry` + `dzh_type_map` 决定
- **AND** 修改后端 `field_definitions` 配置后前端面板自动反映变化

#### Scenario: 事件驱动一致性

- **WHEN** 评审工程师审查事件流
- **THEN** SSE（`/api/events/stream`）正确接收后端事件流
- **AND** WebSocket（`/api/config/ws`）`ConfigSync` 正确接收配置变更
- **AND** `AppState.subscribe()` 自定义 pub-sub 正确工作
- **AND** `document.dispatchEvent(new CustomEvent('configChanged', ...))` 正确派发
- **AND** `tdx:historyView` CustomEvent 正确派发与监听
- **AND** `zoomchange` 自定义事件正确派发与监听

#### Scenario: 模块间无直接引用

- **WHEN** 评审工程师审查模块依赖
- **THEN** `FlowCanvas` 通过 `onChange()` 回调通知外部，不直接调用 `PoolDataManager`
- **AND** `TableDrivenPanel` 通过 `onChange()` 回调通知，不直接调用 `FlowCanvas`
- **AND** `event-panel.js` 通过 `window.xxx` 桥接函数与 `app.js` 交互，使用 `typeof window.xxx === 'function'` 守卫
- **AND** `HighlightManager` 通过 `canvas.highlightNode()` 接口调用，不直接操作 SVG
- **AND** `ConfigManager` 通过 API 与后端交互，不直接操作 `PoolDataManager._data`

#### Scenario: 事件订阅清理

- **WHEN** 评审工程师审查 `destroy()` 方法
- **THEN** `HighlightManager.destroy()` 正确关闭 WebSocket + 清除定时器 + 取消 RAF
- **AND** `ConfigSync.disconnect()` 正确关闭 WebSocket + 清除重连定时器 + ping 定时器
- **AND** `VirtualScroller.destroy()` / `RuleEditor` 事件解绑 / `TableDrivenPanel.destroy()` 正确清理事件

### Requirement: 方法/属性/事件清单验证与清理

系统 SHALL 盘点前端核心对象的公开方法/属性/事件清单，验证输入输出正确，清理冗余代码。

#### Scenario: 核心对象清单完整性

- **WHEN** 评审工程师审查 `FlowCanvas`/`PoolDataManager`/`TableDrivenPanel`/`EventPanel`/`AppState`/`LRUCache`/`BaseChart`/`KlineChart`/`IndicatorChart`/`RuleEditor`/`ConfigSync`/`HighlightManager`/`ToolbarRenderer`
- **THEN** 每个核心对象的公开方法/属性/事件清单完整
- **AND** 每个公开方法输入输出符合 spec，无未捕获异常
- **AND** 关键属性保持单一真相源，无冗余状态字段
- **AND** 事件回调正确订阅、触发和取消订阅
- **AND** 未使用的方法/属性/事件/变量/导入已删除（junk code 零容忍）

### Requirement: 禁止兼容旧接口检查与回归验证

系统 SHALL 扫描并清除旧接口兼容代码，通过 eventtest + Playwright 验证无回归。

#### Scenario: 旧接口扫描

- **WHEN** 评审工程师全局搜索前端代码
- **THEN** 无 `get_node_stocks` / `set_node_stocks` 引用（节点类型字符串除外）
- **AND** 无 `SimTickSource` 引用
- **AND** 无 `EdgeFired.changed_codes` 引用
- **AND** 无运行时 `execution_order` 引用（节点类型字符串 `execution_order` + 编译期 `CompiledSchedule.execution_order` 字段除外）
- **AND** 前端 API 调用使用 `StatePoolView` 视图接口，不直接操作 `node_stocks`
- **AND** `PoolDataManager` 不暴露 `_data.node_stocks` 直接访问接口

#### Scenario: 回归验证

- **WHEN** 评审工程师运行 eventtest + Playwright
- **THEN** eventtest 173 个正反合测试退出码 0
- **AND** 事件计数与基线一致（无回归）
- **AND** 池状态快照与基线一致（无回归）
- **AND** Playwright 端到端验证通过（模式切换、仿真运行、事件接收、导入导出、综合设置）
- **AND** 浏览器控制台无 JavaScript 错误，无未捕获 Promise rejection

### Requirement: 文档更新

完成后 SHALL 更新 `DESIGN.md` 和 `DESIGN0.md` 的前端验证章节，记录评审结果、修复清单和量化数据。

#### Scenario: DESIGN.md 更新

- **WHEN** 所有 10 个任务通过评审（≥98 分）
- **THEN** `DESIGN.md` 追加 §22 "前端迭代评审 V4 结果"章节
- **AND** 包含各任务最终评分表（Task 1-10 分数 + 通过日期）
- **AND** 包含修复清单（文件:行号格式）
- **AND** 包含量化数据（事件计数/池状态快照/Playwright 截图引用）
- **AND** 文档简洁清晰，无冗余文字，无重复表述

#### Scenario: DESIGN0.md 同步

- **WHEN** `DESIGN.md` §22 章节已完成
- **THEN** `DESIGN0.md` 前端验证章节同步更新
- **AND** 表驱动一致性结论（含验证证据）
- **AND** 事件驱动一致性结论（含验证证据）
