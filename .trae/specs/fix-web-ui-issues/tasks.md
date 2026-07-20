# Web UI问题修复 - The Implementation Plan

## [ ] Task 1: 后端SSE事件流端点实现
- **Priority**: high
- **Depends On**: None
- **Description**: 
  - 在app.py中实现/api/events/stream SSE端点
  - 订阅EventBus的所有事件，实时推送到前端
  - 事件格式统一为{event_type, code, pool_id, edge_id, details, time}
  - 实现target_pool_100.json自动导入到storage
  - 添加/api/pools/{pool_id}/nodes/{node_id}/stocks端点获取节点股票列表
- **Acceptance Criteria Addressed**: AC-1, AC-12
- **Test Requirements**:
  - `programmatic` TR-1.1: GET /api/events/stream返回text/event-stream内容类型
  - `programmatic` TR-1.2: 事件发布后SSE端点在500ms内推送
  - `programmatic` TR-1.3: GET /api/pools返回包含target_pool_100的列表
  - `programmatic` TR-1.4: GET /api/pools/{id}/nodes/{nodeId}/stocks返回股票列表
- **Notes**: 使用sse-starlette或原生StreamingResponse实现SSE

## [ ] Task 2: 完善CSS事件类型颜色样式
- **Priority**: high
- **Depends On**: None
- **Description**: 
  - 在styles.css中补充所有事件类型的颜色样式
  - 添加TickReceived/DataChanged/BarComposed/FormulaEvaluated/StockFiltered/EdgeFired/TransferExecuted/OrderPlaced/OrderFilled/PositionUpdated/StatisticsUpdated/AlertRaised/SnapshotUpdated/EventLogged/ModeChanged/PoolLoaded/SYSTEM等类型
  - 每种类型有独特的边框色、背景色和ev-type文字颜色
  - 添加"待处理"事件区域的样式
- **Acceptance Criteria Addressed**: AC-2
- **Test Requirements**:
  - `human-judgement` TR-2.1: 所有事件类型视觉上可区分
  - `human-judgement` TR-2.2: 颜色搭配合理，不刺眼
- **Notes**: 使用现有色系扩展，保持深色主题一致

## [ ] Task 3: 重构事件浮窗UI和逻辑
- **Priority**: high
- **Depends On**: Task 1, Task 2
- **Description**: 
  - 修改ui.js中EventPanel代码：
    - 使用EventSource连接/api/events/stream替代轮询
    - 事件append到列表底部（而非prepend到顶部）
    - 自动滚动到底部
    - 添加"已记录"和"待处理"两个区域（或标签）
    - 显示edge_id字段
    - 默认显示事件浮窗（添加visible类）
  - 修改index.html：
    - 事件浮窗默认有visible类
    - 如有需要，调整事件浮窗结构支持双区域
- **Acceptance Criteria Addressed**: AC-1, AC-3, AC-4, AC-5, AC-11
- **Test Requirements**:
  - `human-judgement` TR-3.1: 事件到达时自动追加到底部
  - `human-judgement` TR-3.2: 列表自动滚动到最新事件
  - `human-judgement` TR-3.3: 事件显示edge_id（如有）
  - `human-judgement` TR-3.4: 页面加载后事件浮窗默认展开显示
  - `programmatic` TR-3.5: EventSource连接成功，无报错

## [ ] Task 4: 添加target_pool_100快捷加载入口
- **Priority**: high
- **Depends On**: Task 1
- **Description**: 
  - 在顶部工具栏添加"📊 示例池"按钮
  - 点击后直接加载config/pools/target_pool_100.json
  - 或在池列表侧栏添加"示例"标签页，显示target_pool_100
  - 确保_import_demo_pools函数也导入target_pool_100
- **Acceptance Criteria Addressed**: AC-7
- **Test Requirements**:
  - `human-judgement` TR-4.1: 工具栏有明确的加载示例池入口
  - `programmatic` TR-4.2: 点击后成功加载target_pool_100，画布显示4个节点
  - `human-judgement` TR-4.3: 加载成功有提示

## [ ] Task 5: 统一运行控制按钮组
- **Priority**: high
- **Depends On**: None
- **Description**: 
  - 在顶部工具栏添加统一的开始/暂停/停止按钮组
  - 按钮根据当前模式和状态正确启用/禁用
  - 与simulationPanel/replayPanel的控制按钮状态联动
  - 点击按钮调用对应API（仿真用/api/sim/control，实盘用/api/pool/{name}/live/*）
- **Acceptance Criteria Addressed**: AC-6, AC-8
- **Test Requirements**:
  - `human-judgement` TR-5.1: 有清晰的▶开始/⏸暂停/⏹停止按钮
  - `human-judgement` TR-5.2: 初始状态只有开始按钮可用
  - `human-judgement` TR-5.3: 点击开始后开始禁用，暂停/停止可用
  - `human-judgement` TR-5.4: 点击暂停后暂停禁用，继续/停止可用
  - `human-judgement` TR-5.5: 点击停止后回到初始状态

## [ ] Task 6: 修复画布节点股票数量显示
- **Priority**: high
- **Depends On**: Task 1
- **Description**: 
  - 修改canvas.js，确保节点渲染时显示stock_count徽章
  - 在app.js中监听状态更新事件，实时更新节点的stock_count
  - 定期轮询或通过SSE接收node_stocks更新
  - 点击节点时，在属性面板显示该节点的股票列表
- **Acceptance Criteria Addressed**: AC-9, AC-10
- **Test Requirements**:
  - `human-judgement` TR-6.1: 节点上有股票数量显示
  - `human-judgement` TR-6.2: 运行时数量实时更新
  - `human-judgement` TR-6.3: 点击节点在属性面板看到股票列表
  - `programmatic` TR-6.4: 节点股票数据与/api/pools/{id}/nodes/{nodeId}/stocks一致

## [ ] Task 7: 优化界面布局和模式切换
- **Priority**: medium
- **Depends On**: Task 3, Task 5
- **Description**: 
  - 调整顶部工具栏按钮布局，减少拥挤感
  - 模式切换时自动显示/隐藏对应控制面板
  - 确保事件浮窗不遮挡关键内容
  - 统一仿真/回放面板的样式和位置
- **Acceptance Criteria Addressed**: AC-6, AC-11
- **Test Requirements**:
  - `human-judgement` TR-7.1: 工具栏布局整洁，按钮不重叠
  - `human-judgement` TR-7.2: 切换模式时控制面板正确显示/隐藏
  - `human-judgement` TR-7.3: 事件浮窗可调整大小/折叠，不影响操作
