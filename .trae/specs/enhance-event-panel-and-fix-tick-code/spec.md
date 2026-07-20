# 事件面板增强与Tick代码修复 - Product Requirement Document

## Overview
- **Summary**: 完善前端事件浮窗的显示效果，添加事件分类图标、颜色标识、过滤功能和滚动控制；修复仿真模式下TickReceived事件code字段为空导致事件被跳过的问题；验证完整的事件流链路确保所有事件都携带正确的字段。
- **Purpose**: 提升事件监控面板的可读性和可用性，让用户能够直观区分不同类型的事件；解决tick数据code为空导致事件链断裂的关键bug；确保从TickReceived到OrderFilled的完整事件流正确传递。
- **Target Users**: 股票池平台开发者、量化交易策略调试人员。

## Goals
- 事件面板支持emoji图标和分类颜色，一眼识别事件类型
- 支持按事件分类过滤显示/隐藏
- 事件显示格式统一为[时间] [图标] [事件类型] [代码] [详情摘要]
- 添加暂停自动滚动功能
- 修复SimTickSource生成的tick确保code字段正确传递
- 确保TickReceived到OrderFilled的事件链完整且字段正确
- 事件浮窗默认显示在页面右侧/右下角

## Non-Goals (Out of Scope)
- 不修改事件总线的核心架构
- 不添加新的事件类型
- 不修改K线面板或其他UI组件
- 不重构后端模块结构

## Background & Context
当前事件面板已存在但功能不完善：
1. 事件项缺少emoji图标标识，不同类型事件区分度不高
2. 颜色配置分散在JS和CSS中，部分颜色不符合要求（如ModeChanged当前是#3F51B5蓝色，要求#00bcd4青色）
3. 没有事件分类过滤功能，事件量大时难以定位问题
4. 没有暂停滚动按钮，查看历史事件时会被新事件冲到底部
5. 仿真模式下存在"_on_tick_received skip: code empty"警告，导致tick数据被跳过，事件链断裂
6. 需要验证事件流中每个事件都携带正确的ts、code/codes、详情字段

## Functional Requirements
- **FR-1**: 每个事件类型显示对应的emoji图标
  - 📊 Tick/DataChanged
  - 📈 Bar/BarComposed
  - 🧮 Formula/FormulaEvaluated/StockFiltered
  - ⚡ Edge/EdgeFired
  - 🔄 Transfer/TransferExecuted
  - 💰 Signal/Signal(BUY/SELL)
  - 📋 Order/OrderPlaced/OrderFilled/PositionUpdated
  - ⏰ TTL/TTLExpired/Timeout
  - 🔧 ModeChanged/TimeAdvanced
- **FR-2**: 不同事件类型使用指定的左侧边框颜色
  - Tick/DataChanged: 灰色 (#9e9e9e)
  - Bar/BarComposed: 蓝色 (#2196f3)
  - Formula/FormulaEvaluated/StockFiltered: 绿色 (#4caf50)
  - Edge/EdgeFired: 橙色 (#ff9800)
  - Transfer/TransferExecuted: 紫色 (#9c27b0)
  - Signal/Signal(BUY/SELL): 红色 (#f44336)
  - Order/OrderPlaced/OrderFilled/PositionUpdated: 黄色 (#ffc107)
  - TTL/TTLExpired/Timeout: 深红色 (#b71c1c)
  - ModeChanged/TimeAdvanced: 青色 (#00bcd4)
- **FR-3**: 添加事件分类过滤按钮组，可选择显示/隐藏某类事件
- **FR-4**: 事件显示格式：[时间] [图标] [事件类型] [代码] [详情摘要]
- **FR-5**: 默认自动滚动到底部，提供"暂停滚动"按钮
- **FR-6**: 事件浮窗默认显示，位于页面右下角/右侧
- **FR-7**: SimTickSource生成的tick必须包含正确的code字段
- **FR-8**: TickReceived事件发布时必须携带正确的code参数和tick_data.code
- **FR-9**: 验证事件链：TickReceived → DataChanged(tick) → BarComposed → FormulaEvaluated → EdgeFired(changed_codes) → TransferExecuted → Signal(BUY) → OrderFilled
- **FR-10**: 每个事件都携带正确的ts时间戳、code/codes字段、详情信息
- **FR-11**: 不发布code为空的空事件

## Non-Functional Requirements
- **NFR-1**: 前端修改不影响现有SSE事件接收逻辑
- **NFR-2**: 过滤功能响应时间<100ms
- **NFR-3**: 事件面板性能：500条事件滚动流畅无卡顿
- **NFR-4**: 修复后不应再有"code empty"警告日志

## Constraints
- **Technical**: 纯前端修改（HTML/CSS/JS）+ 后端Python小范围修复
- **Business**: 必须保持与现有事件格式兼容
- **Dependencies**: 依赖现有EventBus、SimTickSource、SSE事件流

## Assumptions
- 现有CSS中的.event-panel基础样式可用，只需增强
- SimTickSource._generate_tick已返回code字段，问题出在事件发布链路
- SSE事件推送格式保持不变

## Acceptance Criteria

### AC-1: 事件图标显示正确
- **Given**: 事件面板已加载，SSE连接正常
- **When**: 收到不同类型的事件
- **Then**: 每个事件前显示对应emoji图标
- **Verification**: `human-judgment`

### AC-2: 事件边框颜色正确
- **Given**: 事件面板显示事件
- **When**: 查看不同类型事件
- **Then**: 左侧边框颜色符合FR-2的颜色规范
- **Verification**: `human-judgment`

### AC-3: 事件分类过滤功能
- **Given**: 事件面板有过滤按钮
- **When**: 点击某类事件的过滤按钮取消选中
- **Then**: 该类事件从列表中隐藏；再次点击恢复显示
- **Verification**: `programmatic` + `human-judgment`

### AC-4: 事件显示格式正确
- **Given**: 事件面板收到新事件
- **When**: 事件添加到列表
- **Then**: 格式为 [HH:MM:SS] [emoji] [EventType] [code] [details]
- **Verification**: `human-judgment`

### AC-5: 暂停滚动功能
- **Given**: 事件面板正在自动滚动
- **When**: 点击"暂停滚动"按钮
- **Then**: 新事件到达时不自动滚动到底部；按钮变为"继续滚动"，点击恢复
- **Verification**: `programmatic` + `human-judgment`

### AC-6: Tick事件code不为空
- **Given**: 仿真模式运行中
- **When**: SimTickSource生成tick并发布TickReceived事件
- **Then**: 日志中不应出现"code empty"警告；_on_tick_received能正常处理tick
- **Verification**: `programmatic`

### AC-7: 事件链完整性
- **Given**: 仿真模式运行，股票池加载完成
- **When**: 步进或自动运行产生tick
- **Then**: 事件链按TickReceived→DataChanged→BarComposed→...→OrderFired顺序触发，每个事件都有ts和code/codes
- **Verification**: `programmatic`

## Open Questions
- 无
