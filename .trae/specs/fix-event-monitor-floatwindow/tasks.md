# 事件监控与浮窗显示修正 - The Implementation Plan (Decomposed and Prioritized Task List)

## [ ] Task 1: 检查并修正core/monitoring_module.py的事件订阅
- **Priority**: high
- **Depends On**: None
- **Description**: 
  - 检查core/monitoring_module.py的_EVENT_HANDLERS字典
  - 补全缺失的关键事件类型：TickReceived、BarComposed、FormulaEvaluated
  - 确保所有11类关键事件都有对应的处理方法
  - 验证事件按时间顺序存储，包含时间戳
- **Acceptance Criteria Addressed**: [AC-1]
- **Test Requirements**:
  - `programmatic` TR-1.1: 检查_EVENT_HANDLERS包含TickReceived、DataChanged、BarComposed、FormulaEvaluated、EdgeFired、TransferExecuted、Signal、TTLExpired、OrderPlaced、OrderFilled、PositionUpdated
  - `programmatic` TR-1.2: 每个事件类型都有对应的_on_xxx处理方法
  - `human-judgement` TR-1.3: 代码审查确认事件记录逻辑包含时间戳
- **Notes**: 使用EventBus.subscribe_any可能更简单，但需要确保MonitoringModule自己的事件列表功能正常

## [ ] Task 2: 检查并修正SSE API端点事件格式
- **Priority**: high
- **Depends On**: [Task 1]
- **Description**: 
  - 检查app.py中/api/events/stream端点
  - 确保事件JSON格式包含event_type、time、code、node_id、edge_id、details、timestamp字段
  - 验证subscribe_any能正确捕获所有事件
  - 确保事件实时推送到前端
- **Acceptance Criteria Addressed**: [AC-2]
- **Test Requirements**:
  - `programmatic` TR-2.1: 验证SSE事件JSON包含所有必要字段
  - `programmatic` TR-2.2: 使用curl测试SSE端点能正常推送事件
  - `human-judgement` TR-2.3: 代码审查事件序列化逻辑正确处理所有事件类型
- **Notes**: 当前app.py已经有subscribe_any实现，需要验证其正确性

## [ ] Task 3: 修正web前端CSS - 事件浮窗默认显示
- **Priority**: high
- **Depends On**: None
- **Description**: 
  - 修改web/css/styles.css中#eventPanel的初始display属性
  - 将display从none改为flex，确保默认显示
  - 移除或调整visible类的依赖
- **Acceptance Criteria Addressed**: [AC-3]
- **Test Requirements**:
  - `programmatic` TR-3.1: CSS中#eventPanel的display初始值不是none
  - `human-judgement` TR-3.2: 页面加载后事件浮窗在右下角可见
- **Notes**: 需要同时检查ui.js中是否有代码在初始化时隐藏面板

## [ ] Task 4: 修正web前端事件颜色映射
- **Priority**: high
- **Depends On**: None
- **Description**: 
  - 修改web/js/ui.js中的getEventColor函数
  - 按要求设置颜色：TickReceived灰色、DataChanged/BarComposed蓝色、FormulaEvaluated绿色、EdgeFired橙色、TransferExecuted紫色、Signal红色、OrderPlaced/OrderFilled/PositionUpdated黄色、TTLExpired深红色
  - 同步更新web/css/styles.css中的事件样式类
- **Acceptance Criteria Addressed**: [AC-4]
- **Test Requirements**:
  - `programmatic` TR-4.1: getEventColor函数返回的颜色值符合要求
  - `programmatic` TR-4.2: CSS中所有事件类型都有对应的样式类
  - `human-judgement` TR-4.3: 各事件类型在浮窗中显示正确颜色
- **Notes**: 颜色使用十六进制值，确保边框和背景色搭配合理

## [ ] Task 5: 确保事件自动滚动和信息完整显示
- **Priority**: medium
- **Depends On**: [Task 3, Task 4]
- **Description**: 
  - 检查addEvent函数确保新事件追加在底部
  - 添加自动滚动到底部逻辑
  - 确保事件显示时间、类型、代码、详情
  - 验证SSE连接和错误重连逻辑
- **Acceptance Criteria Addressed**: [AC-5, AC-6]
- **Test Requirements**:
  - `programmatic` TR-5.1: addEvent函数在列表末尾追加新事件
  - `programmatic` TR-5.2: 添加事件后调用scrollTop自动滚动
  - `human-judgement` TR-5.3: 事件项显示完整信息（时间、类型、代码、详情）
  - `human-judgement` TR-5.4: SSE连接断开后能自动重连

## [ ] Task 6: 启动服务器验证SSE连接和事件流
- **Priority**: high
- **Depends On**: [Task 1, Task 2, Task 3, Task 4, Task 5]
- **Description**: 
  - 启动FastAPI服务器
  - 使用curl或浏览器验证SSE连接
  - 运行模拟池验证事件流
  - 检查前端浮窗实时更新
- **Acceptance Criteria Addressed**: [AC-1, AC-2, AC-3, AC-4, AC-5, AC-6]
- **Test Requirements**:
  - `programmatic` TR-6.1: 服务器成功启动无错误
  - `programmatic` TR-6.2: curl连接/api/events/stream能接收心跳和事件
  - `human-judgement` TR-6.3: 浏览器打开页面后事件浮窗默认显示
  - `human-judgement` TR-6.4: 运行模拟时各类事件按颜色正确显示，自动滚动
- **Notes**: 使用playwright MCP进行浏览器验证
