# Tasks

- [x] Task 1: 前端职责边界梳理与复杂逻辑后移
  - [x] SubTask 1.1: 审计 `web/js/app.js` 中的 `AppState`、LRUCache、PoolDataManager、Charts、FormulaEditor、RuleEditor、ComprehensiveSettings 等模块，明确哪些属于业务逻辑
  - [x] SubTask 1.2: 将业务状态（池运行时状态、模式状态、事件队列、计时器队列）从 `AppState` 剥离，改为通过 `/api/state/*` 或 SSE 从后端获取
  - [x] SubTask 1.3: 将复杂计算（如事件时间归一化、TTL 计算、事件分类聚合、仿真时间换算）从 `event-panel.js` 和 `app.js` 后移到后端 `core/` 或新增 `web_engine` 模块
  - [x] SubTask 1.4: 保留在前端的仅限：UI 渲染、用户输入转发、SSE 订阅、纯界面状态（面板折叠/缩放/选中）

- [x] Task 2: 表驱动 UI 唯一路径实现
  - [x] SubTask 2.1: 梳理 `config/ui/ui_components.json`、`field_definitions.json`、`fields.json`、`ui_layouts.json`、`action_table.json`、`action_pipeline.json`、`api_routes.json`，补全缺失的节点/边/工具栏/菜单定义
  - [x] SubTask 2.2: 改造 `TableDrivenPanel`，使其渲染流程完全由配置表驱动，删除硬编码节点类型分支
  - [x] SubTask 2.3: 改造工具栏、右键菜单、移动端溢出菜单，统一从配置表生成，消除重复写死的菜单条目
  - [x] SubTask 2.4: 校验所有 UI 组件类型（`text_input`、`select`、`color_picker`、`flag_group` 等）在配置表与渲染器之间一一对应

- [x] Task 3: 事件驱动唯一路径实现
  - [x] SubTask 3.1: 统一前端事件入口为单一 `EventSource('/api/events/stream')`，事件格式与 `core/event_bus.py` 事件契约对齐
  - [x] SubTask 3.2: 删除前端自行构造的伪造事件、独立事件队列真值源，以及 `event-panel.js` 中的复杂事件状态机
  - [x] SubTask 3.3: 确保运行控制（开始/暂停/停止/模式切换）调用后端 API 后，由后端推送状态变更事件，前端仅做展示更新
  - [x] SubTask 3.4: 如后端缺少必要的事件 API（如 `/api/events/timer-queue`），在 `app.py`/`core/` 中补充，禁止前端绕过 API

- [x] Task 4: 股票池功能正确性与模式一致性
  - [x] SubTask 4.3: 修复 TDX XML 自动检测导入（`/api/dzh/import`）
  - [x] SubTask 4.4: 修复回放模式启动（`/api/replay/start`）
  - [x] SubTask 4.1: 验证节点 CRUD（备选池、转移条件、状态池、丢弃池、文字标签）在画布与配置表双向同步
  - [x] SubTask 4.2: 验证连线与属性（条件/无条件、线形、描述文字、线条宽度）正确保存与回显
  - [x] SubTask 4.5: 验证事件面板、计时器队列、K线/公式面板能够正确展示后端事件，无前端私自推断的状态

- [ ] Task 5: 清理多路径与兼容代码
  - [ ] SubTask 5.1: 扫描并删除 `try old else new`、`if (legacy)`、`if (compat)`、重复实现的函数路径
  - [ ] SubTask 5.2: 收敛重复的事件处理入口（如 `window.timelineAddEvent`、`window.logSystemEvent`、`window.eventPanelLoad` 等全局钩子）到统一 API
  - [ ] SubTask 5.3: 删除未使用的变量、函数、DOM 元素引用和 CSS 类
  - [ ] SubTask 5.4: 确保同一功能只有唯一调用路径，例如事件接收只通过 SSE，状态获取只通过后端 API

- [ ] Task 6: 设计文档更新与最终集成
  - [ ] SubTask 6.1: 更新 `DESIGN.md`，明确前端仅作为展示层、后端持有真值源、表驱动与事件驱动的唯一路径
  - [ ] SubTask 6.2: 更新 `docs/` 下相关文档（如 `SYSTEM_REFERENCE.md`、`SPEC.md`）中关于前端架构的描述
  - [ ] SubTask 6.3: 运行前端静态检查与现有测试，确保无回归
  - [ ] SubTask 6.4: 每个 Task 完成后提交并 push 到远程；Task 6 完成后最终 push 并汇总变更日志

# Task Dependencies

- Task 2 依赖 Task 1（先明确前端边界，再改造表驱动渲染）
- Task 3 依赖 Task 1（先明确前端边界，再统一事件驱动）
- Task 4 依赖 Task 2 与 Task 3（功能正确性建立在表驱动与事件驱动之上）
- Task 5 依赖 Task 2、Task 3、Task 4（清理多路径需在功能稳定后进行）
- Task 6 依赖 Task 5（文档更新在最后）
