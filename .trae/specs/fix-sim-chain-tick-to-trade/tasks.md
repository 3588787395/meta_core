# 仿真模式完整链路修复 - The Implementation Plan

## [ ] Task 1: 修复K线时区问题
- **Priority**: high
- **Depends On**: None
- **Description**: 
  - 修改core/tick_bar_module.py中的_bar_bucket_ts函数，使用本地时区而非UTC
  - 同步检查_bucket_ts_to_hhmmss函数的时区处理
  - 确保K线时间整点对齐（如每分钟00秒、每5分钟0/5分）
- **Acceptance Criteria Addressed**: AC-1, AC-2
- **Test Requirements**:
  - `programmatic` TR-1.1: 测试_bar_bucket_ts(ts, "1m")在北京时间09:30:00返回09:30对应的bucket
  - `programmatic` TR-1.2: 测试_bar_bucket_ts(ts, "5m")在09:32返回09:30，在09:36返回09:35
  - `human-judgement` TR-1.3: 代码审查确认移除timezone.utc，使用本地datetime
- **Notes**: 使用datetime.fromtimestamp(ts)不带tzinfo参数，或使用datetime.now().astimezone()的tzinfo

## [ ] Task 2: 修复TradeModule - 订阅TTLExpired事件
- **Priority**: high
- **Depends On**: None
- **Description**:
  - 在TradeModule._register_subscribers中添加TTLExpired事件订阅
  - 实现_on_ttl_expired方法：收到TTLExpired时，检查该股票在该池的持仓，若有持仓则发布SELL Signal平全部仓位
  - 确保TTL出池（exited）的股票能正确触发卖出
- **Acceptance Criteria Addressed**: AC-3
- **Test Requirements**:
  - `programmatic` TR-2.1: _register_subscribers中包含self._bus.subscribe(TTLExpired, self._on_ttl_expired)
  - `programmatic` TR-2.2: TTLExpired事件触发时，若有持仓则发布Signal(signal_type="SELL", qty=持仓量)
  - `human-judgement` TR-2.3: 代码审查确认SELL数量为全部持仓（从tracker.qty获取）
- **Notes**: TTLExpired事件携带code/pool_id/ts字段，需从event中提取

## [ ] Task 3: 修复TradeModule - baimpool=1自动买入逻辑
- **Priority**: high
- **Depends On**: None
- **Description**:
  - 修改_on_transfer_executed方法，不依赖auto_buy_pools配置
  - 改为检查目标节点(tgt)的psatt.baimpool是否为1
  - 需要从state或pool_config获取目标节点的psatt配置
  - 入池(entered)baimpool=1的池即触发BUY Signal(100股)
  - TTL出池(exited)baimpool=1的池即触发SELL Signal(全部持仓)
- **Acceptance Criteria Addressed**: AC-4
- **Test Requirements**:
  - `programmatic` TR-3.1: 股票进入pool_C(baimpool=1)时发布BUY Signal
  - `programmatic` TR-3.2: BUY Signal quantity=100
  - `human-judgement` TR-3.3: 代码审查确认逻辑检查psatt.baimpool==1而非配置列表
- **Notes**: 需要在TradeModule中持有pool_config引用或通过事件获取节点psatt

## [ ] Task 4: 修复市价单价格问题
- **Priority**: high
- **Depends On**: None
- **Description**:
  - 修改_immediate_fill方法，当price为0（市价单）时，从latest_tick获取该股票最新收盘价
  - TradeModule需要访问state.latest_tick以获取最新价
  - 发布Signal时，BUY单price=0（市价）由执行层填充最新价
  - SELL单也需要填充最新价
- **Acceptance Criteria Addressed**: AC-5, AC-6
- **Test Requirements**:
  - `programmatic` TR-4.1: 市价单price=0时，_immediate_fill返回fill.price=latest_tick[code].close
  - `programmatic` TR-4.2: OrderPlaced→OrderFilled→PositionUpdated事件链完整
  - `human-judgement` TR-4.3: 代码审查确认成交价非0
- **Notes**: 需要将state或latest_tick引用传入TradeModule

## [ ] Task 5: 确认首次运行源节点dirty
- **Priority**: medium
- **Depends On**: None
- **Description**:
  - 验证ExecutionModule或Engine在PoolLoaded后标记source节点dirty
  - 若事件驱动路径缺少此逻辑，添加：收到PoolLoaded事件后标记source_node_ids为dirty
  - 确保首次运行时边能被触发
- **Acceptance Criteria Addressed**: AC-8
- **Test Requirements**:
  - `programmatic` TR-5.1: PoolLoaded事件处理后，source节点在dirty.nodes中为True
  - `human-judgement` TR-5.2: 代码审查确认首次运行路径正确标脏
- **Notes**: engine.py中已有_mark_source_nodes_dirty，需确认事件驱动路径是否调用

## [ ] Task 6: 验证交集条件和TTL逻辑
- **Priority**: medium
- **Depends On**: None
- **Description**:
  - 验证_eval_intersection_path正确调用evaluate_intersection
  - 验证evaluate_intersection正确计算set(A) & set(B)
  - 验证TTL配置解析：ndeltype=2表示分钟，ndelnum=100表示100分钟=6000秒
  - 验证node_ttl正确注册和触发
- **Acceptance Criteria Addressed**: AC-7
- **Test Requirements**:
  - `programmatic` TR-6.1: evaluate_intersection返回codes与other_stocks的交集
  - `programmatic` TR-6.2: TTLSpec正确解析psatt.ndelnum/ndeltype为ttl_sec
  - `human-judgement` TR-6.3: 代码审查确认TTL时间单位正确（分钟转秒）
- **Notes**: ndeltype=2是分钟单位，需转换为秒(*60)

## [ ] Task 7: 验证模式切换逻辑
- **Priority**: medium
- **Depends On**: None
- **Description**:
  - 验证RuntimeModeModule.switch_mode发布ModeChanged事件
  - 验证TickBarModule、TradeModule、ExecutionModule订阅ModeChanged并正确切换
  - 验证核心处理逻辑(gate/filter/propagate)无mode==simulation分支
  - 验证runtime_modes.json表驱动配置正确加载
- **Acceptance Criteria Addressed**: AC-8
- **Test Requirements**:
  - `programmatic` TR-7.1: ModeChanged事件发布后，TradeModule._interface_type切换为paper_trade
  - `programmatic` TR-7.2: 静态检查核心处理文件无if.*mode.*==.*simulation
  - `human-judgement` TR-7.3: 代码审查确认模式差异仅在配置和数据源层面
- **Notes**: runtime_mode_module.py中的step_simulation有if _current_mode != "simulation"判断，这是模块自身逻辑，不是核心处理分支，可接受

## [ ] Task 8: 端到端验证
- **Priority**: high
- **Depends On**: Task 1, Task 2, Task 3, Task 4, Task 5, Task 6, Task 7
- **Description**:
  - 启动服务器，加载target_pool_100.json
  - 切换到仿真模式，运行
  - 验证备选池100只股票代码为fz000001格式（8字符）
  - 验证tick生成、K线合成、公式计算
  - 验证A/B池有股票进入
  - 验证C池有交集股票
  - 验证C池入池触发买单（持仓100股）
  - 验证事件日志显示完整事件链
- **Acceptance Criteria Addressed**: AC-1到AC-8
- **Test Requirements**:
  - `programmatic` TR-8.1: 服务器启动无错误
  - `programmatic` TR-8.2: API返回各池股票列表正确
  - `human-judgement` TR-8.3: MCP Playwright验证UI显示正确
- **Notes**: 如MCP不可用，通过API和日志验证
