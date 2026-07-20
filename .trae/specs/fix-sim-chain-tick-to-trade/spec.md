# 仿真模式完整链路修复 - Product Requirement Document

## Overview
- **Summary**: 审查并修复仿真模式从tick生成到交易执行的完整链路（Task 2-9），包括K线合成时区问题、公式计算、边触发逻辑、交集条件、TTL管理、自动交易、模式切换等核心问题。
- **Purpose**: 确保仿真模式下100只股票备选池正确运行：KDJ(5分钟)金叉→A池、MACD(1分钟)金叉→B池、A∩B→C池自动买卖的完整链路正确工作。
- **Target Users**: 量化交易开发者、股票池策略验证人员。

## Goals
- 修复K线合成时区问题：使用本地时区而非UTC，确保1分钟/5分钟K线整点对齐
- 修复TradeModule交易逻辑：订阅TTLExpired事件、根据baimpool=1自动触发买卖、市价单按最新价成交
- 确保EventDriver虽然使用轮询但功能正确（当前轮询方式可接受，优先修复功能问题）
- 确保首次运行强制源节点dirty，驱动初始边执行
- 确保事件链BarComposed→FormulaEvaluated→StockFiltered→EdgeFired→TransferExecuted→OrderPlaced→OrderFilled→PositionUpdated完整发布
- 确认runtime_modes.json表驱动配置正确，核心逻辑无mode==simulation分支
- 验证target_pool_100.json配置正确

## Non-Goals (Out of Scope)
- 不将EventDriver从轮询改为call_later中断（当前轮询方式功能正确，属于优化项）
- 不修改HQChartPy2公式引擎
- 不重构整体架构

## Background & Context
- 项目位于 `h:\new_tdx_mock\PYPlugins\meta_core`
- 审查发现5个关键问题：
  1. K线时间桶使用UTC时区，导致中国时区K线不对齐
  2. TradeModule未订阅TTLExpired事件，TTL出池无法触发卖出
  3. TradeModule的_on_transfer_executed依赖auto_buy_pools配置，不检查baimpool=1属性
  4. 市价单price=0，_immediate_fill未取最新价，导致成交价为0
  5. 需要确认事件驱动路径中首次运行是否强制源节点dirty
- builtin_formulas.json已定义KDJ_5MIN_CROSS和MACD_1MIN_CROSS，配置正确
- target_pool_100.json边配置和TTL配置正确

## Functional Requirements
- **FR-1**: K线时区修复 - _bar_bucket_ts函数使用本地时区计算K线桶，确保1分钟/5分钟K线整点对齐（如09:30, 09:35...）
- **FR-2**: BarComposed事件正确发布 - TickBarModule._on_data_changed在K线合成后发布BarComposed事件
- **FR-3**: TradeModule订阅TTLExpired事件 - 收到TTLExpired时检查是否为目标池股票出池，触发SELL Signal平全部持仓
- **FR-4**: baimpool=1自动买入 - TradeModule检查目标池psatt.baimpool==1，入池即触发BUY Signal（100股）
- **FR-5**: 市价单按最新价成交 - _immediate_fill和Signal发布时使用股票最新成交价，而非0
- **FR-6**: 首次运行源节点dirty - PoolLoaded后或启动时标记所有source节点为dirty
- **FR-7**: 事件链完整 - 从TickReceived到PositionUpdated的事件链完整发布
- **FR-8**: 交集条件正确 - INTERSECTION正确计算set(A) & set(B)
- **FR-9**: TTL正确工作 - A池100分钟/B池200分钟/C池20分钟TTL正确触发

## Non-Functional Requirements
- **NFR-1**: 模块解耦 - 所有模块仅通过EventBus通信，无直接引用
- **NFR-2**: 无模式分支 - 核心处理逻辑无if mode==simulation分支判断
- **NFR-3**: 向后兼容 - 修复不破坏现有实盘/回放模式功能

## Constraints
- **Technical**: Python 3.13, 现有事件驱动架构, HQChartPy2公式引擎
- **Business**: 仿真模式除tick生成外与实盘共用代码
- **Dependencies**: EventBus、PoolState、现有模块结构

## Assumptions
- K线时区问题是由于datetime.fromtimestamp使用timezone.utc导致，应使用本地时区（不指定tzinfo或使用tzlocal()）
- TradeModule可通过访问state.latest_tick获取股票最新价
- ExecutionModule在PoolLoaded时已标记source节点dirty（需验证）
- 交集条件evaluate_intersection已正确实现set交集

## Acceptance Criteria

### AC-1: K线时间正确对齐
- **Given**: 仿真模式运行，虚拟时钟推进到北京时间09:30:00
- **When**: tick到达触发K线合成
- **Then**: 1分钟K线bucket_ts对应09:30，5分钟K线对应09:30/09:35等整点
- **Verification**: `programmatic`

### AC-2: BarComposed事件发布
- **Given**: K线合成完成
- **When**: TickBarModule._on_data_changed执行
- **Then**: 为每个周期和代码发布BarComposed事件
- **Verification**: `programmatic`

### AC-3: TTL到期触发卖出
- **Given**: 股票在C池停留满20分钟
- **When**: TTLExpired事件发布
- **Then**: TradeModule订阅该事件，为该股票发布SELL Signal，数量为全部持仓
- **Verification**: `programmatic`

### AC-4: baimpool=1入池触发买入
- **Given**: 股票进入baimpool=1的目标池（C池）
- **When**: TransferExecuted事件发布
- **Then**: TradeModule检查tgt节点psatt.baimpool==1，发布BUY Signal(100股)
- **Verification**: `programmatic`

### AC-5: 市价单按最新价成交
- **Given**: 股票最新价为10.50元
- **When**: 市价买单/卖单触发
- **Then**: 成交价为10.50元（取自latest_tick.close），而非0
- **Verification**: `programmatic`

### AC-6: 订单事件链完整
- **Given**: BUY Signal发布
- **When**: 交易流程执行
- **Then**: 依次发布OrderPlaced→OrderFilled→PositionUpdated事件
- **Verification**: `programmatic`

### AC-7: 交集条件正确
- **Given**: A池有股票{fz000001, fz000002}，B池有股票{fz000002, fz000003}
- **When**: INTERSECTION边触发
- **Then**: 仅fz000002通过（set(A) & set(B)）
- **Verification**: `programmatic`

### AC-8: 模式切换无分支
- **Given**: 代码静态分析
- **When**: 检查核心处理逻辑（gate/filter/propagate/trade）
- **Then**: 无if mode=="simulation"分支判断
- **Verification**: `programmatic`

## Open Questions
- [ ] EventDriver的call_later改造是否在本次任务范围内？（当前判定为优化项，本次仅确保功能正确）
- [ ] 仿真模式虚拟时钟起始时间如何设置才能正确对齐A股交易时间？
