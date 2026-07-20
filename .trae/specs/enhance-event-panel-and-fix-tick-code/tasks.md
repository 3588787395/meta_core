# 事件面板增强与Tick代码修复 - The Implementation Plan

## [x] Task 1: 修改HTML - 添加事件过滤按钮和暂停滚动按钮
- **Priority**: high
- **Depends On**: None
- **Description**: 
  - 在event-panel-header中添加事件分类过滤按钮组
  - 添加"暂停滚动"按钮
  - 调整event-panel布局确保按钮区域显示正常
- **Acceptance Criteria Addressed**: [AC-3, AC-5, AC-6]
- **Test Requirements**:
  - `human-judgement` TR-1.1: HTML中过滤按钮组和暂停滚动按钮已添加
  - `programmatic` TR-1.2: 按钮ID正确可被JS选择器找到
- **Notes**: 修改web/index.html的eventPanel部分

## [x] Task 2: 修改CSS - 更新事件面板样式和颜色
- **Priority**: high
- **Depends On**: Task 1
- **Description**:
  - 更新.event-item的颜色样式，严格按照FR-2的颜色规范
  - 添加过滤按钮和暂停滚动按钮的样式
  - 确保emoji图标显示正常
  - 调整event-panel默认显示位置为右侧/右下角
- **Acceptance Criteria Addressed**: [AC-2, AC-6]
- **Test Requirements**:
  - `human-judgement` TR-2.1: 各事件类型边框颜色符合规范
  - `human-judgement` TR-2.2: 按钮样式美观协调
- **Notes**: 修改web/css/styles.css的event-panel相关部分

## [x] Task 3: 修改JavaScript - 添加emoji图标映射、过滤逻辑、暂停滚动功能
- **Priority**: high
- **Depends On**: Task 1, Task 2
- **Description**:
  - 添加getEventIcon函数返回事件对应的emoji
  - 更新getEventColor函数严格按照FR-2的颜色值
  - 重写createEventItem函数，格式为[时间] [图标] [事件类型] [代码] [详情摘要]
  - 实现事件分类过滤逻辑
  - 实现暂停/继续滚动功能
  - 更新autoScroll逻辑支持暂停状态
- **Acceptance Criteria Addressed**: [AC-1, AC-3, AC-4, AC-5]
- **Test Requirements**:
  - `programmatic` TR-3.1: getEventIcon为每种事件类型返回正确emoji
  - `programmatic` TR-3.2: 过滤按钮点击可隐藏/显示对应类别事件
  - `programmatic` TR-3.3: 暂停滚动按钮点击后新事件不触发滚动
  - `human-judgement` TR-3.4: 事件显示格式符合要求
- **Notes**: 修改web/js/ui.js的EventPanel部分（约5758-6060行）

## [x] Task 4: 修复TickReceived事件code为空问题
- **Priority**: high
- **Depends On**: None
- **Description**:
  - 检查_on_simulation_step中tick生成和发布逻辑
  - 确保SimTickSource._generate_tick返回的tick包含code字段（已确认包含）
  - 检查_on_pool_loaded是否正确提取codes重建SimTickSource
  - 检查是否存在初始状态下codes为空导致next_ticks返回空字典的情况
  - 添加防御性代码：在发布TickReceived前再次确认code不为空
  - 修复BarComposed事件等批量事件中code字段问题
- **Acceptance Criteria Addressed**: [AC-6, AC-7]
- **Test Requirements**:
  - `programmatic` TR-4.1: 仿真步进时不再出现"code empty"警告
  - `programmatic` TR-4.2: TickReceived事件始终携带非空code字段
  - `programmatic` TR-4.3: _on_tick_received能成功处理tick并发布DataChanged
- **Notes**: 修改core/tick_bar_module.py，可能需要检查core/domain.py和core/engine.py

## [x] Task 5: 验证事件流逻辑完整性
- **Priority**: medium
- **Depends On**: Task 4
- **Description**:
  - 检查事件链中每个事件的发布位置
  - 确认每个事件都携带ts、code/codes等必要字段
  - 确认没有发布空code的事件
  - 添加日志或断言帮助调试事件流
- **Acceptance Criteria Addressed**: [AC-7]
- **Test Requirements**:
  - `programmatic` TR-5.1: 事件链按正确顺序触发
  - `programmatic` TR-5.2: 每个事件都有ts和code/codes字段
  - `programmatic` TR-5.3: 没有code为空的事件被发布
- **Notes**: 检查core/execution_module.py、core/formula_module.py、core/trade_module.py等事件发布点
