# 前端界面规范化改进 Spec

## Why

当前 `/workspace/web/` 前端经过多轮合并后，存在职责边界模糊、复杂逻辑堆积在前端、仿真/回放/实盘模式存在特殊处理分支、部分股票池交互功能未收敛到唯一正确路径等问题。为保证股票池平台长期可维护、前端保持简单简洁，需要制定本规范计划：将复杂逻辑后移到对应模块/引擎，统一事件总线与表驱动渲染，确保所有股票池功能正确且与实盘路径一致。

## What Changes

- 重构前端核心三件套（`app.js`/`canvas.js`/`ui.js`/`event-panel.js`），剥离业务状态机、复杂计算、兼容分支到后端或专用引擎。
- 统一表驱动渲染：所有节点属性面板、工具栏、右键菜单、运行控制必须由 `config/ui/*.json` 配置表驱动，消除硬编码分支。
- 统一事件驱动：前端所有状态变更统一通过后端 SSE/EventSource + 标准化事件契约（与 `core/event_bus.py` 对齐），禁止前端私自维护多套状态。
- 模式对齐：设计/仿真/回放/实盘四种模式必须与实盘执行路径一致，禁止为仿真/回放单独写特殊处理代码。
- 验证所有方法、属性、事件正确工作，删除未使用或重复的实现路径。
- 更新 `DESIGN.md` 与相关设计文档，记录新的前端架构边界。
- 每个任务完成后必须提交并 push 到远程。

## Impact

- Affected specs: `audit-frontend-stockpool`（作为问题输入参考，但不重复创建）
- Affected code:
  - `/workspace/web/js/app.js`
  - `/workspace/web/js/canvas.js`
  - `/workspace/web/js/ui.js`
  - `/workspace/web/js/event-panel.js`
  - `/workspace/web/index.html`
  - `/workspace/web/css/styles.css`
  - `/workspace/config/ui/*.json`
  - `/workspace/core/event_bus.py`（事件契约对齐）
  - `/workspace/DESIGN.md`（设计文档更新）

## ADDED Requirements

### Requirement: 前端职责边界清晰

前端 SHALL 只负责：
1. 渲染由配置表描述的 UI 结构；
2. 将用户操作转换为标准化 API 请求；
3. 订阅并展示后端推送的事件流；
4. 维护少量纯界面状态（如面板折叠、画布缩放、选中项）。

所有业务状态（股票池节点数据、运行时状态、模式切换、公式求值、事件分类与计时器队列）SHALL 由后端模块/引擎持有真值源。

#### Scenario: 复杂逻辑剥离
- **WHEN** 架构工程师审查前端代码
- **THEN** 不存在股票池执行逻辑、条件计算、TTL 计算、时间归一化、事件分类聚合等复杂业务逻辑残留

### Requirement: 表驱动 UI 唯一正确路径

UI 渲染 SHALL 仅通过 `config/ui/*.json` 配置表驱动。禁止在前端代码中写死节点类型、字段类型、工具栏按钮、右键菜单项、运行控制按钮。

#### Scenario: 属性面板渲染
- **WHEN** 用户选中节点或连线
- **THEN** `TableDrivenPanel` 仅读取 `ui_components.json` + `field_definitions.json` + 节点 `params` 渲染，不依赖任何硬编码分支

#### Scenario: 工具栏与菜单
- **WHEN** 页面加载
- **THEN** 顶部工具栏、右键菜单、移动端悬浮按钮、溢出菜单均从配置表生成，且条目唯一

### Requirement: 事件驱动唯一正确路径

前端 SHALL 通过单一 `EventSource('/api/events/stream')` 接收后端事件，事件格式与 `core/event_bus.py` 契约对齐。禁止前端自行生成伪造事件、禁止维护独立的事件队列作为真值源。

#### Scenario: 事件面板
- **WHEN** 后端发布事件
- **THEN** `event-panel.js` 仅做分类展示，不做时间归一化/模式推断/计时器状态机等复杂计算

#### Scenario: 运行状态同步
- **WHEN** 用户切换模式或点击开始/暂停/停止
- **THEN** 前端发送请求到后端，后端返回状态变更事件，前端据此更新 UI

### Requirement: 股票池功能正确且简洁

所有股票池相关交互 SHALL 正确工作：
- 节点增删改查（备选池、转移条件、状态池、丢弃池、文字标签）
- 连线与连线属性（条件/无条件、线形、描述文字、线条宽度）
- 右键菜单（添加节点、属性、复制/剪切/粘贴、层级、删除、综合设置入口）
- 导入导出（DZH XML、TDX XML、JSON）
- 运行控制（开始、暂停、停止、模式切换）
- 事件面板与计时器队列展示
- 仿真/回放/实盘模式切换

#### Scenario: 模式一致性
- **WHEN** 用户在仿真/回放/实盘模式间切换
- **THEN** 执行路径与实盘相同，仅数据源与时间推进机制不同，无特殊处理分支

### Requirement: 唯一正确路径，禁止兼容与特殊处理

同一条业务链路 SHALL 只有唯一实现路径。禁止保留"旧路径兼容"、"旧 API 兜底"、"如果存在则用否则新建"等多分支代码。

#### Scenario: 代码审查
- **WHEN** 评审工程师检查关键路径
- **THEN** 不存在多条路径实现同一功能，不存在 `if (legacy)` / `if (compat)` / `try old else new` 等兼容分支

## MODIFIED Requirements

无。

## REMOVED Requirements

### Requirement: 前端维护业务状态真值源

**Reason**: 前端应仅作为展示层，业务状态真值源必须位于后端引擎。
**Migration**: 将 `AppState` 中涉及池状态、运行状态、事件队列、计时器队列的状态迁移到后端；`AppState` 仅保留纯 UI 状态。
