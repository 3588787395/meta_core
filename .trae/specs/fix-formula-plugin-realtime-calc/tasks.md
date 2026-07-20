# 公式插件化与实时计算架构修正 - The Implementation Plan (Decomposed and Prioritized Task List)

## [ ] Task 1: 验证公式算子CROSS配置与实现
- **Priority**: high
- **Depends On**: None
- **Description**: 
  - 检查config/data/formula_funcs.json中CROSS函数的配置是否正确（handler=cross_op, direction_field等）
  - 确认_ExprParser能正确解析CROSS(A,B)函数调用并通过_dispatch_func分派
  - 编写单元测试验证CROSS(K,D)在公式中正确计算金叉
  - 确保业务代码(execution_module/screening_module)中没有直接调用cross_op或硬编码金叉检测
- **Acceptance Criteria Addressed**: AC-1
- **Test Requirements**:
  - `programmatic` TR-1.1: formula_funcs.json中CROSS条目存在且handler为cross_op
  - `programmatic` TR-1.2: 公式`CROSS(MA(C,5),MA(C,10))`编译求值无错误
  - `programmatic` TR-1.3: 构造已知K线数据，验证CROSS返回正确的布尔序列（最后一根True当短期均线上穿长期均线）
  - `human-judgement` TR-1.4: 代码审查确认screening_module/execution_module中无cross_op直接import
- **Notes**: 这是基础验证，CROSS算子本身已实现，只需确认配置和调用链正确

## [ ] Task 2: 修正公式引擎K线数据获取（含未闭合K线）
- **Priority**: high
- **Depends On**: Task 1
- **Description**: 
  - 修改formula_module.py中的_get_period_bars函数（当前仅返回bars_history已闭合K线），改为返回：闭合K线列表 + [当前未闭合K线]
  - 确保未闭合K线从state.bars[period][code]获取，包含最新OHLC
  - 修正_eval_formula中的fetcher函数，正确合并history+current为完整DataFrame
  - 多周期处理：1分钟/5分钟等周期均需正确获取并合并不闭合K线
  - 移除_get_period_bars中"基于最新闭合K线判定"的错误注释
- **Acceptance Criteria Addressed**: AC-2, AC-3
- **Test Requirements**:
  - `programmatic` TR-2.1: 单只股票，N根闭合1分钟K线+1根未闭合K线，fetcher返回DataFrame行数=N+1
  - `programmatic` TR-2.2: 最后一行close等于latest_tick中的最新价
  - `programmatic` TR-2.3: 5分钟周期同样正确合并不闭合K线
  - `programmatic` TR-2.4: KDJ/MACD公式使用含未闭合K线的数据计算不报错
- **Notes**: 这是核心修正，解决"tick变了就必须以最新价参与计算"的要求

## [ ] Task 3: 重构状态池为视图模型（移除独立脏标记）
- **Priority**: high
- **Depends On**: Task 2
- **Description**: 
  - 修改runtime_mode_module.py的DirtyState，移除node_entered_codes/node_exited_codes字段
  - 保留changed_codes作为全局Tick变化股票集合
  - 修改get_node_stocks逻辑，不再维护独立的node_stocks列表副本（或简化为缓存+视图计算）
  - entry_trackers保留：记录code→{entry_ts, entry_price}，用于TTL和交易
  - 修改execution_module.py的_make_edge_action，移除对node_entered_codes/node_exited_codes的依赖
  - changed_codes与源池股票集合取交集，自然得到该边需增量处理的股票
  - TTL到期删除时，从entry_trackers移除code即可，无需复杂的脏标记
- **Acceptance Criteria Addressed**: AC-4
- **Test Requirements**:
  - `programmatic` TR-3.1: DirtyState类定义中无node_entered_codes/node_exited_codes字段
  - `programmatic` TR-3.2: 源池有100只股票，changed_codes有10只，筛选时仅处理这10只
  - `programmatic` TR-3.3: TTL到期后股票自动从池成员中消失（entry_trackers移除+下次筛选不包含）
  - `programmatic` TR-3.4: 首次运行时源池全量计算，changed_codes为空时使用源池全部股票作为兜底
- **Notes**: 视图模型简化架构，状态池不需要知道K线，只保存入池元数据

## [ ] Task 4: 实现per-code公式缓存与增量筛选
- **Priority**: high
- **Depends On**: Task 3
- **Description**: 
  - 修改FormulaEngine._cache_key，从全局bar_hash改为per-code缓存键：(formula_ref, code, period, code_bar_hash)
  - code_bar_hash计算单只股票该周期K线的哈希，而非全局
  - 修改_cached_eval逻辑：按code分别缓存，某只股票K线变化仅失效该股票缓存
  - 修改execution_module.py的_filter方法，正确实现增量筛选：
    - changed_codes=None：全量评估
    - changed_codes=[]：缓存命中则直接返回，否则全量
    - changed_codes非空：仅对changed_codes中的股票重新评估，其他沿用cached_passed
    - passed_set = (cached_passed - changed_set) | newly_passed
  - 缓存存储在state.filter_inputs[eid]为frozenset，以及state.formula_results按per-code存储
- **Acceptance Criteria Addressed**: AC-5
- **Test Requirements**:
  - `programmatic` TR-4.1: 100只股票中2只Tick变化，仅这2只重新计算公式
  - `programmatic` TR-4.2: 未变化股票结果与上一次一致，不重新计算
  - `programmatic` TR-4.3: 增量合并后passed_set正确（新增通过/不再通过的股票正确反映）
  - `programmatic` TR-4.4: 性能测试：100只股票增量筛选耗时<50ms
- **Notes**: 这是性能关键，确保每轮Tick不做无用计算

## [ ] Task 5: 修正K线合成与实时更新逻辑
- **Priority**: high
- **Depends On**: Task 2
- **Description**: 
  - 检查tick_bar_module.py的BarComposer.on_tick，确保：
    - Tick到达时立即更新对应周期未闭合K线的high/low/close/vol
    - 新周期开始（bucket_ts变化）时，将旧K线归档到bars_history[period][code]
    - 归档后创建新的未闭合K线
  - 确保state.bars[period][code]始终是当前未闭合K线（最新值）
  - 确保state.bars_history[period][code]是已闭合K线列表（追加，不修改）
  - BarComposer发布DataChanged(source="bar")时携带正确的codes列表
  - TickBarModule在apply_data时调用state.add_changed_codes(codes)记录脏股票
- **Acceptance Criteria Addressed**: AC-3
- **Test Requirements**:
  - `programmatic` TR-5.1: 同一分钟内多个Tick，未闭合K线的high取最大值、low取最小值、close取最新价
  - `programmatic` TR-5.2: 分钟切换时旧K线进入bars_history，新K线创建
  - `programmatic` TR-5.3: 5分钟K线从1分钟闭合K线正确合成
  - `programmatic` TR-5.4: DataChanged事件codes列表正确反映有更新的股票
- **Notes**: K线合成是实时计算的基础，必须正确维护history+current双存储

## [ ] Task 6: 修正仿真模式SimTickSource与fz前缀
- **Priority**: high
- **Depends On**: None
- **Description**: 
  - 检查domain.py中_normalize_to_fz函数，确保numeric.zfill(6)生成8字符fz代码（fz000001而非fz00001）
  - 确保SimTickSource生成的Tick代码全部为fz前缀，间隔1-9秒随机
  - 同一只股票Tick间隔固定，不同股票间隔不同（在__init__时为每只股票随机分配固定间隔）
  - 仿真模式时钟推进逻辑正确：step()按delta_time推进，小步长（1秒）内部分步处理
  - 确保从TickBarModule开始无`if mode=="sim"`分支
- **Acceptance Criteria Addressed**: AC-6, AC-7
- **Test Requirements**:
  - `programmatic` TR-6.1: 所有仿真代码匹配正则`^fz\d{6}$`
  - `programmatic` TR-6.2: 单只股票连续Tick间隔在[1,9]秒内且固定
  - `programmatic` TR-6.3: 不同股票Tick间隔可以不同
  - `programmatic` TR-6.4: 代码审查：core/目录下grep 'mode.*==.*sim'无业务逻辑分支
- **Notes**: 仿真与实盘代码路径必须一致，仅TickSource不同

## [ ] Task 7: 配置target_pool_100.json股票池
- **Priority**: high
- **Depends On**: Task 6
- **Description**: 
  - 创建/修正config/pools/target_pool_100.json配置：
    - 备选池节点：100只股票（fz000001-fz000100）
    - 边e1：备选池→A池，interval=60秒，formula=KDJ_5MIN_CROSS（5分钟周期KDJ金叉），A池TTL=6000秒（100分钟）
    - 边e2：备选池→B池，interval=10秒，formula=MACD_1MIN_CROSS（1分钟周期MACD金叉），B池TTL=12000秒（200分钟）
    - 边e3：A池→C池，interval=5秒，nset=5集合运算交集（同时在A和B中），C池TTL=1200秒（20分钟），baimpool买入100股
    - 边e4：B池→C池，同上（交集需要两条入边）
    - C池exit动作：卖出所有持仓
  - 确保builtin_formulas.json中KDJ_5MIN_CROSS和MACD_1MIN_CROSS正确使用CROSS语法
  - 配置交集运算使用nset=5, ntjindexno=2（交集）
- **Acceptance Criteria Addressed**: AC-8
- **Test Requirements**:
  - `programmatic` TR-7.1: 配置JSON合法，能被Compiler正确解析
  - `programmatic` TR-7.2: 编译后schedule包含4条边，TimingSpec interval分别为60/10/5/5秒
  - `programmatic` TR-7.3: KDJ_5MIN_CROSS公式period="5min", eval_field="CROSS_K_D"
  - `programmatic` TR-7.4: MACD_1MIN_CROSS公式period="1min", eval_field="CROSS_DIF_DEA"
  - `programmatic` TR-7.5: C池边配置baimpool(买入)动作，TTL=1200秒
- **Notes**: 这是用户指定的测试股票池配置，必须严格符合要求

## [ ] Task 8: 实现自动交易（C池买卖）
- **Priority**: high
- **Depends On**: Task 7
- **Description**: 
  - 检查trade_module.py：订阅TransferExecuted/Executed事件，当股票进入C池时
    - 发布Signal(action="BUY", code, price=latest_tick[code].close, volume=100)
    - 记录持仓（paper_trade模式）
  - 订阅TTLExpired/DomainEvent(TIMEOUT)事件，当股票从C池TTL到期时
    - 发布Signal(action="SELL", code, price=latest_tick[code].close, volume=全部持仓)
    - 清除持仓记录
  - 确保仿真模式使用paper_trade，不触发真实交易
  - 交易事件通过EventBus发布，不直接调用其他模块
- **Acceptance Criteria Addressed**: AC-8
- **Test Requirements**:
  - `programmatic` TR-8.1: 股票入C池时发布BUY Signal，volume=100
  - `programmatic` TR-8.2: C池TTL到期（20分钟）后发布SELL Signal，volume=持仓数量
  - `programmatic` TR-8.3: 仿真模式side_effects_scope="simulation"允许paper_order
  - `programmatic` TR-8.4: 持仓记录正确更新（买入增加、卖出减少）
- **Notes**: 交易通过Signal事件驱动，TradeModule订阅执行

## [ ] Task 9: 修正事件监控与浮窗显示
- **Priority**: medium
- **Depends On**: Task 5, Task 8
- **Description**: 
  - 检查monitoring_module.py，订阅所有关键事件类型：TickReceived/DataChanged/BarComposed/FormulaEvaluated/EdgeFired/TransferExecuted/Signal(BUY/SELL)/TTLExpired/OrderFilled
  - 事件按时间顺序存储，提供SSE推送接口
  - 修正web前端事件浮窗：
    - 默认显示（不隐藏）
    - 不同事件类型不同颜色
    - 最新事件在底部，自动滚动
    - 显示事件时间、类型、代码、详情
  - 确保所有事件都被记录，包括未排队的事件
- **Acceptance Criteria Addressed**: AC-9
- **Test Requirements**:
  - `human-judgement` TR-9.1: 浏览器打开页面，事件浮窗默认可见
  - `human-judgement` TR-9.2: Tick/Bar/Formula/Edge/Transfer/Signal/TLL事件颜色不同
  - `human-judgement` TR-9.3: 事件自动滚动到最新
  - `programmatic` TR-9.4: SSE事件流包含所有事件类型
- **Notes**: 事件浮窗是验证系统运行的关键UI

## [ ] Task 10: 清理垃圾代码与静态检查
- **Priority**: medium
- **Depends On**: Task 1-9
- **Description**: 
  - 删除不再使用的node_entered_codes/node_exited_codes相关代码
  - 删除错误的"仅使用已闭合K线"注释和相关死代码
  - 删除模块间直接import，确保仅通过EventBus交互
  - 运行静态检查，确认无违规引用
  - 清理重复代码、未使用的import、注释掉的代码块
  - 确保每个模块职责单一，高内聚低耦合
- **Acceptance Criteria Addressed**: NFR-5
- **Test Requirements**:
  - `programmatic` TR-10.1: 静态检查脚本无违规报告
  - `programmatic` TR-10.2: 模块间import仅允许：标准库、同模块内部、event_bus事件类
  - `human-judgement` TR-10.3: 代码审查确认无垃圾代码、无注释掉的代码块、无死代码
- **Notes**: 代码清理是持续过程，此任务处理本次修改引入/暴露的问题

## [ ] Task 11: Playwright端到端验证
- **Priority**: high
- **Depends On**: Task 1-10
- **Description**: 
  - 启动服务器（uvicorn app:app --port 8000）
  - 使用Playwright打开浏览器访问http://localhost:8000
  - 操作步骤：
    1. 切换到仿真模式
    2. 导入/加载target_pool_100.json股票池配置
    3. 启动仿真运行
    4. 观察事件浮窗事件流：Tick更新→K线合成→公式计算→边触发→股票转移→交易信号
    5. 快进虚拟时间至少30分钟，观察：
       - 备选池100只fz前缀股票
       - Tick间隔1-9秒随机更新
       - 1分钟/5分钟K线实时更新（含未闭合K线）
       - 有股票满足KDJ/MACD金叉时进入A/B池
       - 同时在A/B的股票进入C池并触发买单
       - C池股票20分钟后触发卖单
       - 事件浮窗完整记录所有事件且颜色正确
    6. 验证界面操作简便，解决使用中出现的问题
- **Acceptance Criteria Addressed**: AC-10
- **Test Requirements**:
  - `human-judgement` TR-11.1: Playwright手动验证备选池100只fz股票显示
  - `human-judgement` TR-11.2: Tick随机间隔更新，K线实时变化
  - `human-judgement` TR-11.3: 金叉条件满足时股票正确转移到A/B池
  - `human-judgement` TR-11.4: 交集条件满足时股票转移到C池并买入
  - `human-judgement` TR-11.5: C池TTL到期后卖出
  - `human-judgement` TR-11.6: 事件浮窗完整、颜色正确、自动滚动
  - `human-judgement` TR-11.7: 界面操作流畅无报错
- **Notes**: 这是最终验收，必须手动验证所有功能正确
