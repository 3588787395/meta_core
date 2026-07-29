# 前端股票池界面逐项评审规范 Spec

## Why

`specify-frontend-improvement` 已实现前端各功能模块，但尚未经过逐项严格评审。用户要求对前端界面进行完善与评审流程，验证所有股票池功能正确、简洁，并符合表驱动与事件驱动架构；验证所有方法、属性、事件都能正确工作。

## What Changes

- 基于 `specify-frontend-improvement` 已实现的 18 个任务，逐项进行评审验证。
- 采用双工程师协作 + 98 分门槛机制。
- 重点验证：表驱动一致性、事件驱动正确性、股票池核心功能（设计/实盘/回放/仿真）、条件节点与转移条件配置、事件面板、仿真运行股票流转。
- 验证所有前端对象的方法、属性、事件回调正确工作。
- 修复评审中发现的问题，禁止兼容旧接口（`get_node_stocks` / `set_node_stocks` / `SimTickSource` / `execution_order` / `EdgeFired.changed_codes`）。
- 最终更新 `docs/DESIGN.md` 和 `docs/DESIGN0.md` 的前端验证章节。

## Impact

- 前端代码：`web/index.html`、`web/js/canvas.js`、`web/js/ui.js`、`web/js/app.js`、`web/css/styles.css`
- 后端 API：前端调用的所有 `/api/*` 端点
- 实例配置：`config/pools/sim_test_pool_100.json`
- 文档：`docs/DESIGN.md`、`docs/DESIGN0.md`

---

## ADDED Requirements

### Requirement: 逐项评审前端功能

系统 SHALL 对 `specify-frontend-improvement` 中已完成的前端功能逐项评审，确保每个功能正确、简洁、符合架构原则。

#### Scenario: 画布引擎评审

- **WHEN** 评审工程师审查画布引擎代码与运行效果
- **THEN** `FlowCanvas` 的方法、属性、事件回调正确工作
- **AND** 节点渲染、拖拽、缩放、框选、迷你地图、边渲染均符合表驱动配置
- **AND** 运行模式下正确禁用编辑功能

#### Scenario: 节点与条件节点评审

- **WHEN** 评审工程师审查节点渲染与条件节点配置
- **THEN** 所有节点类型按 `cell_type_registry` 配置正确渲染
- **AND** 转移条件节点（cond1/cond2/cond3）显示为紫色圆角矩形
- **AND** 筛选条件从边的计算参数和 K 线配置中读取
- **AND** 条件节点配置面板可正确打开并显示 func/indi/indiparam/filter_spec

#### Scenario: 四种模式切换评审

- **WHEN** 用户在设计/实盘/回放/仿真模式间切换
- **THEN** 模式指示器颜色和标签正确更新
- **AND** 对应控制面板正确显示/隐藏
- **AND** 运行中切换模式被阻止并提示
- **AND** 界面不出现混乱

#### Scenario: 仿真模式股票流转评审

- **WHEN** 切换到仿真模式并启动
- **THEN** 备选池加载 100 只 `fz` 前缀股票
- **AND** 股票经 cond1 进入 pool_A、经 cond2 进入 pool_B
- **AND** A∩B 经 cond3 进入 pool_C 并触发买入
- **AND** 事件面板显示完整事件链

#### Scenario: 事件面板评审

- **WHEN** 仿真运行产生事件
- **THEN** 事件面板按 9 类分类显示（Tick/Bar/Formula/Edge/Transfer/Signal/Order/TTL/System）
- **AND** 已发生事件与排队中事件区分显示
- **AND** 定时器队列显示预计触发时间
- **AND** 事件用图标和颜色展示

#### Scenario: 表驱动与事件驱动一致性评审

- **WHEN** 评审工程师审查前端架构
- **THEN** UI 组件类型、属性字段、校验规则由后端配置表动态决定
- **AND** 前端通过 EventBus / WebSocket 接收事件并更新界面
- **AND** 前端不直接调用后端业务模块，仅通过 API 与事件总线交互

### Requirement: 方法/属性/事件正确性评审

系统 SHALL 验证前端核心对象的所有方法、属性、事件回调正确工作。

#### Scenario: 核心对象 API 评审

- **WHEN** 评审工程师审查 `FlowCanvas`、`PoolDataManager`、`TableDrivenPanel`、`EventPanel` 等核心对象
- **THEN** 每个公开方法都有明确输入输出
- **AND** 关键属性保持单一真相源
- **AND** 事件回调正确订阅和发布
- **AND** 不存在未使用的冗余方法或属性

### Requirement: 问题修复与回归验证

评审中发现的前端问题 SHALL 被修复，并通过 Playwright + eventtest 量化验证无回归。

### Requirement: 双工程师协作与量化评审

- **架构工程师**：负责修复评审中发现的问题。
- **评审工程师**：负责逐项验证、运行 Playwright、给出 0-100 分评分。
- **门槛**：≥ 98 分进入下一任务；< 98 分打回重做。

### Requirement: 文档更新

完成后 SHALL 更新 `docs/DESIGN.md` 和 `docs/DESIGN0.md` 的前端验证章节，记录评审结果、修复清单和量化数据。
