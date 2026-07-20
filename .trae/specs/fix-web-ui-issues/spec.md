# Web UI问题修复 - Product Requirement Document

## Overview
- **Summary**: 审查并修复Meta Core股票池平台Web UI的多个关键问题，包括事件浮窗SSE实时推送、模式选择界面、股票池加载、运行控制按钮、画布节点显示、界面布局等，确保用户能够顺畅地设计、加载和运行股票池（特别是target_pool_100.json），并实时查看所有事件和节点状态。
- **Purpose**: 解决当前Web UI存在的功能缺失和体验问题，使事件流实时可见、模式切换清晰、股票池加载顺畅、控制按钮状态正确，满足实盘/回放/仿真三种运行模式的操作需求。
- **Target Users**: 股票池设计师、量化交易员、系统测试人员

## Goals
- 实现SSE事件流实时推送，替代轮询方式
- 完善事件浮窗功能：所有事件类型颜色区分、自动滚动到底部、显示时间戳和完整详情（股票代码、池名、边ID）、显示"未排队中"事件
- 清晰的模式选择界面：设计/实盘/回放/仿真四个模式互斥且视觉区分明显
- 明确的股票池加载入口，支持一键加载target_pool_100.json
- 统一的开始/暂停/停止控制按钮，状态正确切换
- 画布节点实时显示股票数量，点击节点可查看该池股票列表
- 整洁清晰的界面布局，事件浮窗默认可见

## Non-Goals (Out of Scope)
- 不修改核心业务逻辑和事件引擎
- 不重新设计整个UI框架，仅修复现有问题
- 不添加新的业务功能（如新增公式编辑器功能）
- 不修改移动端适配（本次聚焦桌面端体验）

## Background & Context
当前Web UI存在以下问题：
1. 事件获取使用3秒轮询而非SSE，实时性差
2. 事件浮窗默认隐藏，事件类型颜色不完整（仅8种），缺少TickReceived/BarComposed/FormulaEvaluated等关键事件类型
3. 事件添加到列表顶部而非底部，无自动滚动
4. 缺少"未排队中"事件显示区域
5. 模式按钮存在但无统一控制按钮组，状态联动不完善
6. 无明确的target_pool_100.json加载入口
7. 节点股票数量显示和点击查看列表功能需验证和修复
8. 后端缺少/api/events/stream SSE端点和/api/pools/{id}/stocks节点股票端点

## Functional Requirements
- **FR-1**: SSE事件流 - 后端实现/api/events/stream端点，前端使用EventSource接收实时事件
- **FR-2**: 事件浮窗完善 - 所有30种事件类型有不同颜色，事件追加到底部并自动滚动，显示时间戳/股票代码/池名/边ID
- **FR-3**: 未排队事件显示 - 事件浮窗显示"已记录"和"待处理"两个区域
- **FR-4**: 模式选择清晰 - 四个模式按钮互斥，选中状态明显，切换时自动显示/隐藏对应控制面板
- **FR-5**: target_pool_100加载 - 提供明确入口加载config/pools/target_pool_100.json，自动导入到storage
- **FR-6**: 运行控制按钮 - 统一的开始/暂停/停止按钮组，状态正确联动（开始→暂停/停止，暂停→继续/停止）
- **FR-7**: 节点股票显示 - 画布节点实时显示股票数量徽章，点击节点弹出/显示该池股票列表
- **FR-8**: 布局整洁 - 事件浮窗默认显示，工具栏按钮组织合理，控制面板位置统一
- **FR-9**: API端点完善 - 实现/api/pools/{id}/stocks端点获取节点股票列表，target_pool_100自动导入

## Non-Functional Requirements
- **NFR-1**: 事件延迟 < 500ms（从后端发布到前端显示）
- **NFR-2**: 事件列表支持最多500条事件，超过自动截断最早事件
- **NFR-3**: UI响应流畅，按钮点击反馈 < 100ms
- **NFR-4**: 兼容Chrome/Edge/Firefox现代浏览器

## Constraints
- **Technical**: 继续使用现有FastAPI后端+原生JS前端架构，不引入新框架
- **Business**: 必须支持target_pool_100.json的完整流程（加载→仿真运行→事件展示→节点股票查看）
- **Dependencies**: 依赖现有EventBus事件系统、RuntimeSimulator仿真引擎

## Assumptions
- EventBus已经发布所有需要的事件类型
- RuntimeSimulator和PoolEngine能正确提供node_stocks数据
- config/pools/target_pool_100.json文件存在且格式正确

## Acceptance Criteria

### AC-1: SSE事件流正常工作
- **Given**: 服务器运行中，股票池已加载并启动
- **When**: 前端连接/api/events/stream
- **Then**: 事件实时推送到前端，延迟<500ms，无需轮询
- **Verification**: `programmatic`

### AC-2: 所有事件类型有颜色区分
- **Given**: 事件浮窗显示中
- **When**: 不同类型事件到达
- **Then**: 每种事件类型（TickReceived/DataChanged/BarComposed/FormulaEvaluated/StockFiltered/EdgeFired/TransferExecuted/OrderPlaced/OrderFilled/PositionUpdated/StatisticsUpdated/RankingChanged/AlertRaised/SnapshotUpdated/EventLogged/ModeChanged/PoolLoaded/ENTER/EXIT/TIMEOUT/BUY/SELL/EXECUTED/SIGNAL等）都有独特的边框色和背景色
- **Verification**: `human-judgment`

### AC-3: 事件自动滚动到底部
- **Given**: 事件浮窗已显示
- **When**: 新事件到达
- **Then**: 事件追加到列表底部，列表自动滚动到最新事件
- **Verification**: `human-judgment`

### AC-4: 事件显示完整详情
- **Given**: 事件浮窗中某条事件
- **When**: 查看事件内容
- **Then**: 显示时间戳、事件类型、股票代码、池名/节点ID、边ID（如有）、关键参数（价格/数量/公式值等）
- **Verification**: `human-judgment`

### AC-5: "未排队中"事件可见
- **Given**: 系统运行中，有已触发但未执行的边
- **When**: 查看事件浮窗
- **Then**: "待处理"区域显示这些事件，与"已记录"事件有视觉区分
- **Verification**: `human-judgment`

### AC-6: 四个模式互斥且清晰
- **Given**: 页面加载完成
- **When**: 点击不同模式按钮
- **Then**: 只有一个模式处于active状态，按钮颜色/样式明显区分，对应控制面板显示/隐藏
- **Verification**: `human-judgment`

### AC-7: 一键加载target_pool_100
- **Given**: 页面加载完成
- **When**: 点击"加载示例池"或选择target_pool_100
- **Then**: config/pools/target_pool_100.json成功加载，画布显示S/A/B/C四个节点和连线
- **Verification**: `programmatic` + `human-judgment`

### AC-8: 开始/暂停/停止按钮状态正确
- **Given**: 股票池已加载，当前为仿真/实盘/回放模式
- **When**: 点击开始→暂停→继续→停止
- **Then**: 按钮状态正确切换（开始后禁用开始，启用暂停/停止；暂停后禁用暂停，启用继续；停止后回到初始状态）
- **Verification**: `human-judgment`

### AC-9: 节点显示股票数量
- **Given**: 股票池运行中，节点内有股票
- **When**: 查看画布节点
- **Then**: 每个节点右下角/右上角显示股票数量徽章，数字随运行实时更新
- **Verification**: `human-judgment`

### AC-10: 点击节点查看股票列表
- **Given**: 股票池运行中，某节点有股票
- **When**: 点击该节点
- **Then**: 属性面板或弹窗显示该池股票列表（代码/名称/现价/涨幅/入池时间等）
- **Verification**: `human-judgment`

### AC-11: 事件浮窗默认可见
- **Given**: 页面加载完成
- **When**: 进入主页面
- **Then**: 事件浮窗在右下角默认显示（展开状态），无需手动点击打开
- **Verification**: `human-judgment`

### AC-12: 后端API端点正确
- **Given**: 服务器运行
- **When**: 调用/api/pools、/api/pools/{id}/stocks、/api/events/stream等端点
- **Then**: 返回正确数据格式，无错误
- **Verification**: `programmatic`

## Open Questions
- [ ] "未排队中"事件具体指哪些状态的事件？是否是EdgeFired但TransferExecuted还未执行？
- [ ] 节点点击后股票列表显示在属性面板还是单独弹窗？
- [ ] 实盘模式在没有真实数据源时是否允许使用mock数据运行？
