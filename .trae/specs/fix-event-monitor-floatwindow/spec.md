# 事件监控与浮窗显示修正 - Product Requirement Document

## Overview
- **Summary**: 修正股票池平台的事件监控模块与前端事件浮窗显示功能，确保所有关键事件类型被正确订阅、记录、通过SSE实时推送到前端，并在浮窗中按指定颜色正确显示，事件浮窗默认可见且自动滚动到底部。
- **Purpose**: 解决当前事件监控存在的事件订阅遗漏、颜色显示错误、浮窗默认隐藏、SSE事件格式不统一等问题，使用户能够实时、直观地观察系统运行时的各类事件流转。
- **Target Users**: 股票池平台的使用者（交易员、量化开发者），需要通过事件浮窗监控系统运行状态。

## Goals
- 补全MonitoringModule对所有关键事件类型的订阅（TickReceived, DataChanged, BarComposed, FormulaEvaluated, EdgeFired, TransferExecuted, Signal(BUY/SELL), TTLExpired, OrderPlaced, OrderFilled, PositionUpdated等）
- 统一事件记录格式，确保SSE推送的事件包含event_type、time、code、details等字段
- 修正前端事件浮窗默认显示（不隐藏）
- 按用户要求设置不同事件类型的显示颜色
- 确保最新事件显示在底部且自动滚动
- SSE事件流正确连接并实时推送新事件
- 事件显示时间、类型、代码、详情信息

## Non-Goals (Out of Scope)
- 不修改事件总线核心逻辑
- 不新增事件类型
- 不修改业务逻辑处理流程
- 不重构前端整体架构
- 不添加事件过滤或搜索功能（本次仅修正显示）

## Background & Context
当前系统采用事件驱动架构，通过EventBus在各模块间传递事件。MonitoringModule负责订阅事件并记录，app.py提供/api/events/stream SSE端点向前端推送事件，前端ui.js处理SSE事件并在事件浮窗中显示。
目前存在的问题：
1. MonitoringModule的_EVENT_HANDLERS缺少部分关键事件类型的订阅
2. 事件浮窗CSS默认display:none，用户打开页面看不到事件流
3. 事件颜色映射与用户要求不一致
4. 前端事件项样式类不完整

## Functional Requirements
- **FR-1**: MonitoringModule必须订阅所有11类关键事件：TickReceived、DataChanged、BarComposed、FormulaEvaluated、EdgeFired、TransferExecuted、Signal(BUY/SELL)、TTLExpired/Timeout、OrderPlaced、OrderFilled、PositionUpdated
- **FR-2**: 所有事件必须按时间顺序存储，带有时间戳
- **FR-3**: SSE /api/events/stream端点必须正确推送所有类型事件，JSON格式包含event_type、time、code、node_id、edge_id、details、timestamp字段
- **FR-4**: 事件浮窗初始状态为显示（非隐藏），无需用户手动点击展开
- **FR-5**: 不同事件类型必须使用指定颜色显示：
  - TickReceived: 灰色
  - DataChanged/BarComposed: 蓝色
  - FormulaEvaluated: 绿色
  - EdgeFired: 橙色
  - TransferExecuted: 紫色
  - Signal(BUY/SELL): 红色
  - OrderPlaced/OrderFilled/PositionUpdated: 黄色
  - TTLExpired: 深红色
- **FR-6**: 最新事件必须追加在列表底部，列表自动滚动到底部
- **FR-7**: 每条事件必须显示时间、事件类型、股票代码（如有）、详情信息
- **FR-8**: SSE连接断开时自动尝试重连

## Non-Functional Requirements
- **NFR-1**: 事件记录必须是线程安全的，不阻塞事件发布
- **NFR-2**: SSE推送延迟不超过100ms
- **NFR-3**: 事件列表最大长度限制在500-1000条，避免内存溢出
- **NFR-4**: 前端事件渲染性能流畅，不卡顿

## Constraints
- **Technical**: 基于现有FastAPI后端、原生JavaScript前端、EventBus事件总线架构
- **Business**: 必须保持向后兼容，不破坏现有API
- **Dependencies**: 依赖core/event_bus.py中已定义的事件类

## Assumptions
- EventBus的subscribe_any方法能够捕获所有已发布的事件
- 前端EventSource API在目标浏览器中可用
- 现有事件类定义完整，无需新增

## Acceptance Criteria

### AC-1: MonitoringModule订阅所有关键事件
- **Given**: MonitoringModule已初始化并注册到EventBus
- **When**: 任意关键事件被发布到EventBus
- **Then**: 该事件被MonitoringModule捕获并添加到事件列表
- **Verification**: `programmatic`
- **Notes**: 通过代码检查_EVENT_HANDLERS包含所有事件类

### AC-2: SSE端点正确推送事件
- **Given**: 服务器运行中，SSE客户端连接到/api/events/stream
- **When**: 系统发布任意事件
- **Then**: SSE流中推送该事件的JSON数据，包含所有必要字段
- **Verification**: `programmatic`
- **Notes**: 使用curl或浏览器开发者工具验证

### AC-3: 事件浮窗默认显示
- **Given**: 用户打开主页index.html
- **When**: 页面加载完成
- **Then**: 事件浮窗在右下角可见，不需要额外点击显示
- **Verification**: `human-judgment`

### AC-4: 事件颜色正确
- **Given**: 事件浮窗中有多种类型事件显示
- **When**: 观察各事件项的边框/背景颜色
- **Then**: 颜色符合FR-5中规定的颜色映射
- **Verification**: `human-judgment`

### AC-5: 事件自动滚动
- **Given**: 事件列表已有内容
- **When**: 新事件到达并添加到列表
- **Then**: 列表自动滚动到底部，最新事件可见
- **Verification**: `human-judgment`

### AC-6: 事件信息完整显示
- **Given**: 事件浮窗显示事件
- **When**: 查看单条事件
- **Then**: 显示时间、事件类型、代码（如有）、关键详情
- **Verification**: `human-judgment`

## Open Questions
- 无
