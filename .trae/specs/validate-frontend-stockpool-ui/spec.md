# 前端股票池界面验证与完善 Spec

## Why

后端事件流程已通过 eventtest 173 个正反合测试验证（100% 通过率，退出码 0），但前端界面尚未经过系统性的 Playwright 浏览器验证。用户要求"对前端界面进行完善与评审流程，验证所有股票池功能正确"。现有前端代码（`web/index.html` + `web/js/canvas.js` + `web/js/ui.js` + `web/js/app.js`）已包含条件节点渲染、三种运行模式、事件面板、综合设置等组件，但功能正确性未经量化验证。

## What Changes

- 通过 Playwright 浏览器测试验证所有前端功能正确性
- 修复验证过程中发现的前端 bug
- 验证条件节点矩形在画布上正确显示（cond1/cond2/cond3）
- 验证条件节点配置面板可打开并配置 func/indi/indiparam/filter_spec
- 验证三种运行模式（实盘/回放/仿真）切换正确
- 验证仿真模式启动后加载 100 只 fz 股票
- 验证事件面板用图标和颜色展示事件
- 验证 K 线合成和公式计算筛选可展示
- 验证股票经 cond1 进入 A 池、经 cond2 进入 B 池、A∩B 经 cond3 进入 C 池并触发买入

## Impact

- 前端代码：`web/index.html`、`web/js/canvas.js`、`web/js/ui.js`、`web/js/app.js`、`web/css/styles.css`
- 后端 API：`api.py`（前端调用的 API 端点）
- 实例配置：`config/pools/sim_test_pool_100.json`
- 文档：`docs/DESIGN.md`、`docs/DESIGN0.md`（新增"前端验证"章节）

---

## ADDED Requirements

### Requirement: Playwright 浏览器验证

系统 SHALL 通过 Playwright MCP 工具打开浏览器，加载前端页面，验证所有股票池功能正确性。

#### Scenario: 加载实例股票池

- **WHEN** 用户点击"加载示例池"按钮
- **THEN** 画布显示 source(备选池) → cond1 → pool_A → cond3 → pool_C 拓扑，source → cond2 → pool_B → cond3 拓扑
- **AND** 条件节点 cond1/cond2/cond3 显示为紫色圆角矩形
- **AND** 边显示触发频率和多入边顺序号

#### Scenario: 条件节点配置面板

- **WHEN** 用户点击条件节点 cond1 矩形
- **THEN** 右侧属性面板打开，显示 func/indi/indiparam/filter_spec 配置
- **AND** 配置值与 `config/pools/sim_test_pool_100.json` 中 cond1 节点配置一致

#### Scenario: 仿真模式启动

- **WHEN** 用户切换到仿真模式并点击"开始"按钮
- **THEN** 仿真面板显示虚拟时钟和步数
- **AND** 备选池加载 100 只 fz 前缀股票
- **AND** 事件面板显示 TickReceived/DataChanged/BarComposed 事件

#### Scenario: 完整事件链可视化

- **WHEN** 仿真运行足够时间（≥300 秒虚拟时钟）
- **THEN** 事件面板显示 11 类事件（TickReceived/DataChanged/BarComposed/EdgeFired/FormulaEvaluated/StockFiltered/TransferExecuted/Signal/OrderPlaced/OrderFilled/PositionUpdated）
- **AND** 事件用图标和颜色分类展示
- **AND** 股票经 cond1 进入 pool_A、经 cond2 进入 pool_B、A∩B 经 cond3 进入 pool_C
- **AND** pool_C 入池触发买入信号

#### Scenario: 三种模式切换

- **WHEN** 用户在设计/实盘/回放/仿真模式间切换
- **THEN** 模式指示器更新
- **AND** 各模式对应的控制面板正确显示/隐藏
- **AND** 不出现界面混乱

### Requirement: 前端 bug 修复

验证过程中发现的前端 bug SHALL 被修复，修复后重新验证通过。

### Requirement: 量化评审

评审工程师 SHALL 基于 Playwright 截图和事件面板输出给出量化评分，门槛 ≥ 98 分。

### Requirement: 双工程师协作

- **架构工程师**（sub-agent）：负责前端 bug 修复和功能完善
- **评审工程师**（sub-agent）：负责 Playwright 验证和打分
- **门槛**：≥ 98 分进入下一任务，< 98 分打回重做

### Requirement: 文档更新

完成后 SHALL 更新 `docs/DESIGN.md` 和 `docs/DESIGN0.md`，新增"前端验证"章节。
