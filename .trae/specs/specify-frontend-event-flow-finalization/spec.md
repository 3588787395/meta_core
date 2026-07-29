# Meta Core 股票池平台 — 前端与事件流程最终验证规范

## Why

`specify-stockpool-event-flow` 已完成 13 个后端架构任务，但 **Task 14（Playwright 手动验证）与 Task 15（设计文档更新）尚未闭环**；`specify-frontend-improvement-v2` 针对事件面板、转移条件节点可视化、仿真模式事件链等前端项仍有缺漏。本规范作为最终验证与文档收口的新执行规范，采用双工程师协作 + 98 分门槛，确保前后端实现一致、界面简洁、所有股票池功能可验证。

## What Changes

- **完成后端事件流程规范遗留项**：通过 Playwright 验证仿真模式 ≥300 秒虚拟时间的事件链与股票流转，并更新 `DESIGN.md` / `DESIGN0.md`。
- **建立前端最终验证规范 V3**：聚焦未闭环项（转移条件节点可视化、边条件摘要、事件面板时间分布图、仿真模式事件链、模式切换状态）。
- **双工程师量化评审**：架构工程师负责实现/修复，评审工程师负责 Playwright + eventtest 验证并打分，< 98 分打回重做。
- **设计文档同步更新**：将验证结果、修复清单、验证方法写入 `DESIGN.md` 与 `DESIGN0.md`。

## Impact

- 受影响代码：`web/js/canvas.js`、`web/js/ui.js`、`web/js/app.js`、`web/js/event-panel.js`、`web/css/styles.css`、`web/index.html`。
- 受影响文档：`docs/DESIGN.md`、`docs/DESIGN0.md`。
- 验证方式：Playwright（mcp_playwright）+ `eventtest` 量化测试 + 浏览器截图。

---

## ADDED Requirements

### R1: 仿真模式完整事件链验证

系统 SHALL 在仿真模式运行 ≥300 秒虚拟时间后，通过事件面板展示完整事件链：

- **WHEN** 用户切换到仿真模式并启动运行
- **THEN** 备选池加载 100 只 `fz` 前缀股票
- **THEN** 事件面板出现 Tick → Bar → Formula → Edge → Transfer → Signal → Order → TTL 事件
- **THEN** 股票按 `备选池 → A池（5m KDJ 金叉）`、`备选池 → B池（1m MACD 金叉）`、`A∩B → C池` 流转
- **THEN** C池入池触发市价买入 100 股，TTL 20 分钟到期后卖出所有持仓并出池

### R2: 转移条件节点可视化

系统 SHALL 在股票池画布中正确渲染转移条件节点：

- **WHEN** 股票池包含 `transfer_condition` 类型节点
- **THEN** 渲染为橙色三角形并指向右侧
- **WHEN** 股票池包含显式 `condition` 类型节点
- **THEN** 渲染为紫色矩形，与状态池节点明显区分
- **THEN** 节点支持选中、拖拽、缩放、适应画布
- **THEN** 运行模式下条件节点不可编辑

### R3: 边条件摘要与属性面板

系统 SHALL 从连接配置读取转移条件并展示：

- **WHEN** 用户选中一条边
- **THEN** 属性面板显示 `edge.params` 中的计算参数：`formula_ref`、`operator`、`threshold`、`nset`、`noperate`
- **THEN** 属性面板显示 K 线配置：`period`、`length`、`bar_type`
- **THEN** 边标签显示触发频率 + 条件摘要（例如 `60s / 5m KDJ 金叉`）
- **THEN** 禁止硬编码实例内容到程序

### R4: 事件面板可视化

系统 SHALL 提供可视化事件面板：

- **WHEN** 事件到达
- **THEN** 分类矩阵每类事件一行，显示图标/名称/计数，右侧按时间轴分布事件图标
- **THEN** 散点分布在同一 Canvas 绘制全部事件，Y 轴语义与矩阵一致，包含统计数量，支持点击交互
- **THEN** 定时器队列顶部 Canvas 绘制 `fire_at` 时间分布图，显示所有排队事件、当前时间线、过期事件标识
- **THEN** 点击分类行/图标在详情区显示事件文本记录
- **THEN** 面板拖拽位置、高度、折叠/展开、关闭状态保存到 localStorage

### R5: 四种模式切换

系统 SHALL 清晰管理四种运行模式：

- **WHEN** 用户切换 设计/实盘/回放/仿真 模式
- **THEN** 模式指示器颜色和标签正确（设计=蓝、实盘=绿、回放=橙、仿真=紫）
- **THEN** 对应控制面板正确显示/隐藏
- **THEN** 运行中切换模式被阻止并显示提示
- **THEN** 仿真面板虚拟时钟、步数、启动/暂停/步进/重置、步长、速度调节正常工作

### R6: 设计文档更新

系统文档 SHALL 与实现保持一致：

- **WHEN** 前端与事件流程验证全部通过
- **THEN** `DESIGN.md` 补充前端验证章节（流程、Playwright 结果、eventtest 量化结果、已修复 bug 清单、验证方法）
- **THEN** `DESIGN0.md` 以架构合同风格补充验证合同、验证范围、eventtest 量化基线、已修复 bug 清单、与设计原则一致性关联

## MODIFIED Requirements

### M1: 事件面板渲染触发时机

修改 `web/js/event-panel.js`，在面板从隐藏变为显示时强制重绘，避免 Canvas 因初始尺寸为 0 而空白。

### M2: 仿真模式 currentMode 作用域

修复 `web/js/app.js` 中 `currentMode` 局部变量导致仿真运行按钮与自动步进失效的问题，确保模式状态全局一致。

## REMOVED Requirements

| 已删除需求 | 删除原因 | 迁移方式 |
|-----------|---------|---------|
| 旧事件面板列表视图 | 已被可视化矩阵/散点/定时器队列替代 | 使用新事件面板 |
| 仿真模式分别处理逻辑 | 仿真与实盘必须共用代码 | 统一运行时路径 |
| 旧测试用例反向断言 | 测试通过条件必须是 spec 被满足 | 使用 eventtest 严格正反合测试 |
