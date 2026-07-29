# 前端股票池界面迭代完善与评审规范 V3

## Why

`specify-frontend-improvement`（V1）已实现 18 项前端功能且全部勾选完成，`specify-frontend-improvement-v2` 完成 V2 收尾。但代码体量庞大（`web/js/app.js` 13150 行 / `ui.js` 7028 行 / `canvas.js` 3581 行 / `event-panel.js` 2172 行，共约 2.6 万行），存在方法冗余、属性语义重叠、事件回调松散等问题。用户要求：

1. **逐项完善 + 评审流程**：每个任务由架构工程师修复、由评审工程师打分，**≥ 98 分**才能进入下一任务，< 98 分打回重做，直至全部通过。
2. **所有股票池功能正确并且简洁**：消除冗余方法/属性/事件，对象职责单一。
3. **符合表驱动 + 事件驱动**：组件类型/字段/校验由后端配置表决定；模块间仅通过 API/事件总线交互，禁止互相引用。
4. **验证所有方法/属性/事件正确工作**：以 Playwright + 浏览器控制台为最终凭证。
5. **由调度方总结更新 DESIGN.md 设计文档**。

## What Changes

- 新增**双工程师协作流程**：架构工程师（sub-agent）修复 → 评审工程师（sub-agent）打分 → ≥98 进下一任务 / <98 打回重做。
- 逐项评审 10 个核心模块：画布引擎、节点/边渲染、属性面板、模式切换、仿真流转、事件面板、表/事件驱动一致性、方法/属性/事件清单验证、junk code 清理、DESIGN.md 更新。
- 每个任务含**验证手段**：Playwright 截图 + 浏览器控制台 + eventtest 量化结果。
- **禁止兼容旧接口**：`get_node_stocks` / `set_node_stocks` / `SimTickSource` / `EdgeFired.changed_codes` / 运行时 `execution_order`（编译期 `execution_order` 字段除外）。
- 最终由调度方汇总评审结果并更新 `DESIGN.md` 前端验证章节。

## Impact

- 受影响代码：`web/index.html`、`web/js/canvas.js`、`web/js/ui.js`、`web/js/app.js`、`web/js/event-panel.js`、`web/css/styles.css`
- 受影响文档：`DESIGN.md`、`DESIGN0.md`（前端验证章节）
- 受影响测试：Playwright 验证脚本、eventtest 量化断言
- 实例配置：`config/pools/sim_test_pool_100.json`

---

## ADDED Requirements

### Requirement: 双工程师迭代评审流程

系统 SHALL 采用"架构工程师修复 + 评审工程师打分"的双轨流程，由调度方（主 Agent）编排：

1. **架构工程师**（Architect Engineer，sub-agent）：依据 spec.md 修复评审发现的问题，禁止兼容旧接口，禁止另开炉灶，禁止 workaround 掩盖 bug。
2. **评审工程师**（Review Engineer，sub-agent）：依据 checklist.md 逐项验证，给出 0-100 分及扣分理由，必须基于 Playwright 截图、浏览器控制台、eventtest 量化结果。
3. **门槛**：≥ 98 分方可进入下一任务；< 98 分打回架构工程师重做（携带扣分点），直至 ≥ 98。
4. **依赖**：严格按 Task Dependencies 执行，前置任务未达 98 不得开启后续任务。
5. 每个任务完成后，调度方在 tasks.md 勾选 `[x]`，并在 checklist.md 勾选通过项。

#### Scenario: 单任务闭环

- **WHEN** 调度方派发 Task N 给架构工程师
- **THEN** 架构工程师依据 spec 修复对应模块并提交 diff
- **AND** 调度方派发评审任务给评审工程师
- **AND** 评审工程师按 checklist 打分并给出扣分理由
- **AND** 若分数 < 98，调度方携带扣分点打回架构工程师重做
- **AND** 若分数 ≥ 98，调度方勾选 tasks.md/checklist.md 并进入下一任务

### Requirement: 表驱动一致性

系统 SHALL 保证前端 UI 完全由后端配置表驱动：

- 组件类型、字段定义、校验规则、枚举值均从 `/api/registry/*` 端点动态读取
- `TableDrivenPanel` 不应硬编码字段类型，所有组件类型由 `field_definitions` 配置表决定
- `ToolbarRenderer` 工具栏按钮的显隐/可用状态由 `toolbar_config.json` 决定
- 节点渲染参数（颜色/尺寸/字体）由 `cell_type_registry` + `dzh_type_map` 决定

#### Scenario: 表驱动验证

- **WHEN** 评审工程师检查 `TableDrivenPanel._renderPanel()` 实现
- **THEN** 字段渲染逻辑通过 `field_config.type` 分发，无硬编码字段类型分支
- **AND** 修改后端 `field_definitions` 配置后，前端面板自动反映变化
- **AND** 所有内置组件类型（text_input/select/color_picker/flag_group 等）通过 `ComponentRegistry` 注册

### Requirement: 事件驱动一致性

系统 SHALL 保证前端模块间仅通过事件/API 交互，禁止相互引用：

- 模块间通信仅通过：① 后端 API ② SSE/WebSocket ③ `AppState.subscribe` pub-sub ④ `CustomEvent` 派发
- `FlowCanvas`、`PoolDataManager`、`TableDrivenPanel`、`EventPanel` 之间禁止直接持有对方引用
- 事件订阅必须在 `destroy()` 中正确取消订阅，无内存泄漏

#### Scenario: 事件驱动验证

- **WHEN** 评审工程师检查模块间通信代码
- **THEN** `FlowCanvas` 通过 `onChange()` 回调通知外部，不直接调用 `PoolDataManager`
- **AND** `TableDrivenPanel` 通过 `onChange()` 回调通知，不直接调用 `FlowCanvas`
- **AND** `event-panel.js` 通过 `window.clearEventPanel()` 等显式桥接函数与 `app.js` 交互，使用 `typeof window.xxx === 'function'` 守卫
- **AND** `HighlightManager.destroy()` 正确取消 WebSocket 与定时器

### Requirement: 方法/属性/事件清单验证

系统 SHALL 为前端核心对象维护方法/属性/事件清单并逐项验证：

| 核心对象 | 关键方法 | 关键属性 | 事件回调 |
|---------|---------|---------|---------|
| `FlowCanvas` | render/fitToContent/setEdgeLineType/zoomIn/selectNode/highlightNode | _nodes/_edges/transform/poolId/_runMode | onChange/onConnect/onNodeClick/onEdgeClick |
| `PoolDataManager` | addNode/removeNode/addEdge/updateEdge/undo/redo/importXML/exportXML | _data/_history/_cache/_listeners | _notify/onChange |
| `TableDrivenPanel` | showForNode/showForEdge/renderPanel/_handleLinkage | _data/_nodeType/_layoutCache/_readOnly | onChange |
| `EventPanel` (event-panel.js) | addEvent/clearEvents/renderMatrix/renderScatter/renderTimerQueue | events/pendingEvents/timerQueue/activeFilters | SSE onmessage |
| `AppState` | setMode/setSimulationState/subscribe/_notify | mode/simulationState/simulationTime/_subscribers | _notify |

#### Scenario: 清单逐项验证

- **WHEN** 评审工程师运行 Playwright 脚本调用每个公开方法
- **THEN** 方法输入输出符合 spec，无未捕获异常
- **AND** 关键属性保持单一真相源，无冗余状态字段
- **AND** 事件回调正确订阅、触发、取消订阅
- **AND** 未使用的冗余方法/属性/事件已删除

### Requirement: 禁止兼容旧接口

系统 SHALL 不含以下禁用 token 的实际引用（节点类型字符串除外）：

- `get_node_stocks` / `set_node_stocks`
- `SimTickSource`
- `EdgeFired.changed_codes`
- 运行时 `execution_order`（编译期 `CompiledSchedule.execution_order` 字段除外）

#### Scenario: 禁用接口扫描

- **WHEN** 评审工程师在 `web/` 目录全局搜索禁用 token
- **THEN** 除节点类型字符串 `execution_order` 外，其余 token 零匹配
- **AND** 前端 API 调用使用 `StatePoolView` 视图接口，不直接操作 `node_stocks`

### Requirement: DESIGN.md 文档同步更新

最终任务 SHALL 由调度方汇总所有评审结果并更新 `DESIGN.md`：

- 新增 §22 "前端迭代评审 V3 结果" 章节
- 包含：每个任务的最终评分、修复清单、量化数据（事件计数/池状态快照）、Playwright 验证截图引用
- 同步更新 `DESIGN0.md` 前端验证章节

#### Scenario: 文档同步

- **WHEN** 所有 10 个任务达到 ≥ 98 分
- **THEN** 调度方在 `DESIGN.md` 追加 §22 章节
- **AND** 在 `DESIGN0.md` 同步更新前端验证章节
- **AND** 文档简洁清晰，无冗余文字

---

## MODIFIED Requirements

### Requirement: 前端架构原则（来自 V1）

V1 已定义"表驱动 UI + 事件驱动 + 单一真相源 + 前端后端解耦"原则。V3 在此基础上增加：

- **方法/属性单一职责**：每个公开方法只做一件事，每个属性只有一个真相源
- **事件回调可取消**：所有 `addEventListener` 必须有对应 `removeEventListener`（或在 `destroy()` 中显式清理）
- **junk code 零容忍**：未使用的方法/属性/事件/变量/导入必须删除

## REMOVED Requirements

### Requirement: 旧 V1 中"超长任务列表"模式

**Reason**: V1 列出 18 个独立任务并行执行，导致评审不充分。V3 改为 10 个串行任务，每个任务必须 ≥ 98 分才能进入下一任务。
**Migration**: V1 已完成的实现保留，V3 在其基础上做"逐项评审 + 修复"，不重写已通过的代码。

---

## Non-Goals (Out of Scope)

- 后端公式计算引擎、数据库持久化、事件驱动引擎核心逻辑（由后端 PoolEngine 负责）
- 引入第三方前端框架（保持原生 JavaScript）
- 重写已通过 V1/V2 的功能（仅在评审发现问题时修复）
- 新增功能特性（本规范聚焦"完善与评审"，不增加新功能）

---

## Constraints

### 双工程师协作约束

- **架构工程师**：必须使用 sub-agent，禁止主 Agent 直接写代码
- **评审工程师**：必须使用 sub-agent，禁止主 Agent 直接打分
- 两者串行执行，不能并行
- 评审必须基于 Playwright 截图 + 浏览器控制台 + eventtest 量化结果，不得仅凭代码审查

### 技术约束

- 前端框架：原生 JavaScript（无第三方框架）
- 浏览器兼容：Chrome ≥ 80, Firefox ≥ 75
- ES Module：IIFE 封装，全局变量导出
- 版本号管理：每次修改后递增 `index.html` 中的 `?v=N` 查询参数

### 业务约束

- 仿真模式下所有股票代码必须用 `fz` 替代原市场代码
- 数据 tick 更新间隔为 1-9 秒随机值，同股票间隔固定，不同股票间隔不同
- 事件面板默认隐藏，仅在仿真/回放模式通过 `.visible` 类显示
- 事件面板默认固定在右下角（right:16px; bottom:16px），尺寸 560×400

---

## Acceptance Criteria

### AC-1: 双工程师流程闭环

- **Given**：调度方派发任务给架构工程师
- **When**：架构工程师修复后评审工程师打分
- **Then**：分数 ≥ 98 才能进入下一任务；< 98 打回重做
- **Verification**：`programmatic`（tasks.md 勾选状态 + checklist.md 评分记录）

### AC-2: 表驱动一致性

- **Given**：评审工程师检查前端代码
- **When**：搜索硬编码字段类型分支
- **Then**：所有字段渲染通过 `field_config.type` 分发，组件通过 `ComponentRegistry` 注册
- **Verification**：`programmatic`

### AC-3: 事件驱动一致性

- **Given**：评审工程师检查模块间通信
- **When**：搜索跨模块直接引用
- **Then**：模块仅通过 API/事件/回调交互，无直接持有对方引用
- **Verification**：`programmatic`

### AC-4: 方法/属性/事件清单验证

- **Given**：核心对象方法清单
- **When**：Playwright 逐项调用
- **Then**：所有方法正确工作，无未捕获异常，无冗余
- **Verification**：`programmatic`

### AC-5: 禁用接口零匹配

- **Given**：前端代码库
- **When**：搜索禁用 token
- **Then**：除节点类型字符串外，零匹配
- **Verification**：`programmatic`

### AC-6: DESIGN.md 同步更新

- **Given**：所有任务 ≥ 98 分
- **When**：调度方汇总评审结果
- **Then**：`DESIGN.md` 新增 §22 章节，`DESIGN0.md` 同步更新
- **Verification**：`human-judgment`

### AC-7: 仿真股票流转正确

- **Given**：仿真模式启动并运行 ≥ 300 秒虚拟时间
- **When**：评审工程师检查事件面板
- **Then**：完整事件链（Tick→Bar→Formula→Edge→Transfer→Signal→Order）正确显示，股票正确流转（备选池→cond1→pool_A、备选池→cond2→pool_B、A∩B→cond3→pool_C→买入）
- **Verification**：`programmatic`（eventtest 量化断言）

### AC-8: 事件面板可视化正确

- **Given**：仿真运行产生事件
- **When**：评审工程师检查事件面板
- **Then**：9 类分类显示正确，已发生/排队中区分显示，定时器队列显示预计触发时间，矩阵/散点视图切换正常
- **Verification**：`programmatic`（Playwright 截图 + DOM 断言）
