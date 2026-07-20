# 股票池仿真模式完整修复与验证 - The Implementation Plan

## [ ] Task 1: 清理根目录垃圾文件并修复股票代码截断bug
- **Priority**: high
- **Depends On**: None
- **Description**:
  - 删除根目录所有临时/诊断文件：diagnose_sim.py ~ diagnose_sim6.py、diagnose_py313_deps.py、smoke_step.py、test_golden_cross_debug.py、test_sim_*.py（共约13个文件）
  - 修复domain.py中_normalize_stock_code和_stock_code函数：仿真模式下'fz'前缀后必须保证6位数字，代码总长度8字符（如fz000001、fz600000，而非fz00001、fz60000）
  - 检查所有代码路径中股票代码的生成/截断/格式化逻辑
- **Acceptance Criteria Addressed**: AC-1, AC-2
- **Test Requirements**:
  - `programmatic` TR-1.1: 根目录.py文件列表仅包含app.py, api.py, converters.py, __init__.py
  - `programmatic` TR-1.2: _normalize_stock_code("fz000001")返回"fz000001"（8字符），_normalize_stock_code("fz1")正确补零为"fz000001"
  - `programmatic` TR-1.3: target_pool_100.json中100只股票代码全部为8字符格式
- **Notes**: 检查是否有lstrip('0')或zfill(5)等错误填充逻辑

## [ ] Task 2: 审查并修复SimTickSource仿真tick生成逻辑
- **Priority**: high
- **Depends On**: Task 1
- **Description**:
  - 检查core/domain.py或tick_bar_module.py中SimTickSource实现
  - 确保每只股票分配固定的1-9秒间隔（基于code hash取模，确定性）
  - tick数据包含完整字段：code, datetime, open, high, low, close, volume, amount
  - 价格在基准价±2%范围内随机波动，保持连续性
  - 仿真模式时间源使用虚拟时间，可加速运行
- **Acceptance Criteria Addressed**: AC-3
- **Test Requirements**:
  - `programmatic` TR-2.1: 同一只股票连续两次tick时间差固定（在1-9秒之间）
  - `programmatic` TR-2.2: 不同股票间隔可以不同
  - `programmatic` TR-2.3: tick数据OHLCV字段齐全
- **Notes**: 间隔用hash(code) % 9 + 1确定，保证确定性

## [ ] Task 3: 审查并修复RuntimeMode模式切换逻辑
- **Priority**: high
- **Depends On**: Task 2
- **Description**:
  - 检查core/runtime_mode_module.py中ModeChanged事件处理
  - 确保四模块（TickBar/Execution/Trade/Storage）正确订阅ModeChanged事件
  - 仿真/实盘/回放的分叉点仅限于：TickSource类型、DataProvider、副作用范围
  - 核心筛选/公式计算/转移/TTL/交易信号逻辑不得有mode==simulation分支
  - 验证EventBus上ModeChanged事件正确发布
- **Acceptance Criteria Addressed**: AC-4
- **Test Requirements**:
  - `programmatic` TR-3.1: grep核心处理代码无"simulation"、"sim_mode"等模式判断分支（除runtime_mode_module外）
  - `programmatic` TR-3.2: ModeChanged事件发布后四模块状态正确切换
  - `human-judgement` TR-3.3: 代码审查确认筛选逻辑不区分模式
- **Notes**: 使用表驱动(runtime_modes.json)配置模式差异，而非if/else

## [ ] Task 4: 审查并修复K线合成逻辑(1分钟/5分钟)
- **Priority**: high
- **Depends On**: Task 3
- **Description**:
  - 检查core/tick_bar_module.py中BarComposer实现
  - 确保tick数据正确聚合为1分钟K线：open=首tick价, high=最高, low=最低, close=末tick价, volume=累加
  - 确保1分钟K线正确合成为5分钟K线：5根1分钟K线合成1根5分钟K线
  - K线合成完成后发布BarComposed事件
  - K线数据缓存供公式模块使用
- **Acceptance Criteria Addressed**: AC-5
- **Test Requirements**:
  - `programmatic` TR-4.1: 生成5根1分钟tick后产生1根完整1分钟K线
  - `programmatic` TR-4.2: 5根完整1分钟K线后产生1根5分钟K线
  - `programmatic` TR-4.3: K线OHLCV计算正确
  - `human-judgement` TR-4.4: UI可显示K线图表
- **Notes**: 注意K线时间对齐（整点对齐）

## [ ] Task 5: 确保KDJ和MACD公式定义正确并可计算
- **Priority**: high
- **Depends On**: Task 4
- **Description**:
  - 检查config/data/builtin_formulas.json中KDJ和MACD公式定义
  - 定义KDJ_5MIN_CROSS：5分钟周期KDJ指标金叉（K上穿D）
  - 定义MACD_1MIN_CROSS：1分钟周期MACD指标金叉（DIF上穿DEA）
  - 确保FormulaModule能正确加载HQChartPy2或内置公式引擎计算这些指标
  - 公式计算结果写入latest_tick作为列，供筛选使用
  - 公式计算完成后发布FormulaEvaluated事件
- **Acceptance Criteria Addressed**: AC-6, AC-7
- **Test Requirements**:
  - `programmatic` TR-5.1: KDJ(5min)指标输出K、D、J三个值
  - `programmatic` TR-5.2: MACD(1min)指标输出DIF、DEA、MACD三个值
  - `programmatic` TR-5.3: 金叉条件(noperate=3)正确判断上穿
  - `programmatic` TR-5.4: 给定历史数据可触发金叉信号
- **Notes**: KDJ参数(9,3,3)，MACD参数(12,26,9)为默认值

## [ ] Task 6: 修复条件转移边触发和执行逻辑
- **Priority**: high
- **Depends On**: Task 5
- **Description**:
  - 检查core/execution_module.py中Compiler和EdgeExecutor
  - 确保编译期正确解析time_gate_interval参数
  - 确保EventDriver基于时间间隔触发边（不是轮询，用中断/call_later）
  - 边触发条件：gate通过 AND (源节点dirty OR 数据dirty)
  - e_source_KDJ_A: interval=60秒，条件=KDJ_5MIN_CROSS公式通过
  - e_source_MACD_B: interval=10秒，条件=MACD_1MIN_CROSS公式通过
  - e_A_INT_C/e_B_INT_C: interval=5秒，条件=INTERSECTION交集
  - 边执行后发布EdgeFired事件，筛选完成后发布StockFiltered事件
- **Acceptance Criteria Addressed**: AC-6, AC-7, AC-8
- **Test Requirements**:
  - `programmatic` TR-6.1: 边按配置的时间间隔准时触发
  - `programmatic` TR-6.2: 公式条件边正确筛选股票
  - `programmatic` TR-6.3: 条件边执行后发布正确事件
  - `programmatic` TR-6.4: 非到期边不执行
- **Notes**: 首次运行强制所有源节点dirty

## [ ] Task 7: 修复A/B池到C池的交集逻辑
- **Priority**: high
- **Depends On**: Task 6
- **Description**:
  - 检查INTERSECTION条件类型的处理逻辑（可能在builtins.py或screening_module.py）
  - 交集逻辑：股票必须同时存在于源池(A)和intersection_source指定池(B)中
  - 两个交集条件节点(cond_INT_A_C和cond_INT_B_C)都应输出相同交集结果到C池
  - 确保交集计算不会重复转移同一只股票（去重）
  - 转移执行后发布TransferExecuted事件
- **Acceptance Criteria Addressed**: AC-8
- **Test Requirements**:
  - `programmatic` TR-7.1: 仅在A池的股票不进入C池
  - `programmatic` TR-7.2: 仅在B池的股票不进入C池
  - `programmatic` TR-7.3: 同时在A和B池的股票进入C池
  - `programmatic` TR-7.4: 重复交集不会重复添加同一股票到C池
- **Notes**: 交集使用集合运算set(A) & set(B)

## [ ] Task 8: 修复TTL超时删除逻辑
- **Priority**: high
- **Depends On**: Task 7
- **Description**:
  - 检查TTLHelper和EdgeExecutor中TTL实现
  - 确保股票入池时记录entry_time(indate+intime)
  - A池TTL: ndelnum=100, ndeltype=2(分钟) → 100分钟
  - B池TTL: ndelnum=200, ndeltype=2 → 200分钟
  - C池TTL: ndelnum=20, ndeltype=2 → 20分钟
  - TTL到期后发布TTLExpired事件，从池中移除股票
  - TTL检查在每tick或通过定时中断执行
- **Acceptance Criteria Addressed**: AC-11, AC-10
- **Test Requirements**:
  - `programmatic` TR-8.1: 股票入池后entry_time正确记录
  - `programmatic` TR-8.2: 未到期股票不被删除
  - `programmatic` TR-8.3: 到期后股票从池中移除
  - `programmatic` TR-8.4: TTL时间单位(天/小时/分钟/秒)正确解析
- **Notes**: 仿真模式下使用虚拟时间判断TTL

## [ ] Task 9: 修复TradeModule自动买卖逻辑
- **Priority**: high
- **Depends On**: Task 8
- **Description**:
  - 检查core/trade_module.py实现
  - 订阅TransferExecuted事件：目标池为C池(target_pool)时执行买入
  - 买入逻辑：市价单，数量100股，价格为当前最新价
  - 订阅TTLExpired事件：源池为C池且股票被TTL删除时执行卖出
  - 卖出逻辑：市价单，数量为该股票全部持仓
  - 订单执行发布OrderPlaced→OrderFilled→PositionUpdated事件链
  - 仿真模式使用模拟成交（立即成交，无滑点）
- **Acceptance Criteria Addressed**: AC-9, AC-10
- **Test Requirements**:
  - `programmatic` TR-9.1: 股票入C池后生成买单，持仓+100股
  - `programmatic` TR-9.2: 买单成交后PositionUpdated事件发布
  - `programmatic` TR-9.3: C池TTL到期后生成卖单，持仓归零
  - `programmatic` TR-9.4: 卖单成交后PositionUpdated事件发布
  - `programmatic` TR-9.5: 非目标池的TransferExecuted不触发交易
- **Notes**: 使用baimpool=1标识目标池(trade pool)

## [ ] Task 10: 修复事件浮窗UI显示
- **Priority**: high
- **Depends On**: Task 9
- **Description**:
  - 检查web/js/ui.js中事件面板(event-panel)实现
  - 确保所有事件类型(TickReceived/DataChanged/BarComposed/FormulaEvaluated/StockFiltered/EdgeFired/TransferExecuted/Signal/OrderPlaced/OrderFilled/PositionUpdated/StatisticsUpdated/RankingChanged/AlertRaised/SnapshotUpdated/EventLogged/TTLExpired/ModeChanged)都被订阅和显示
  - 不同事件类型使用不同颜色/图标区分
  - 事件实时追加到浮窗列表，自动滚动到底部
  - 显示"未排队中"（待处理/已触发未执行）事件
  - 显示事件时间戳和详细信息（股票代码、池名、边ID等）
  - 可清空/暂停/过滤事件日志
- **Acceptance Criteria Addressed**: AC-12
- **Test Requirements**:
  - `human-judgement` TR-10.1: 事件浮窗在UI中可见
  - `human-judgement` TR-10.2: 事件实时滚动显示
  - `human-judgement` TR-10.3: 不同事件颜色不同
  - `human-judgement` TR-10.4: 事件详情(时间、股票、池)显示完整
  - `programmatic` TR-10.5: 所有30种事件类型都有处理逻辑
- **Notes**: 检查SSE或WebSocket事件流是否正确连接

## [ ] Task 11: 修复Web UI界面操作便捷性
- **Priority**: medium
- **Depends On**: Task 10
- **Description**:
  - 检查web/index.html, web/js/app.js, web/js/ui.js
  - 确保加载股票池有明确入口（按钮/菜单）
  - 确保模式切换（实盘/回放/仿真）有清晰的选择控件，三选项互斥
  - 确保开始/暂停/停止按钮状态正确
  - 确保画布上节点可正确显示股票数量
  - 点击节点可查看该池股票列表详情
  - 确保K线和公式值可以在界面上查看
  - 修复界面布局混乱问题
- **Acceptance Criteria Addressed**: AC-15
- **Test Requirements**:
  - `human-judgement` TR-11.1: 模式选择界面清晰，三个选项互不混淆
  - `human-judgement` TR-11.2: 加载target_pool_100流程顺畅
  - `human-judgement` TR-11.3: 开始/暂停/停止按钮工作正常
  - `human-judgement` TR-11.4: 节点显示股票数量，点击查看详情
- **Notes**: 用户特别强调界面不能混乱，操作要简便

## [ ] Task 12: 验证模块解耦和静态检查
- **Priority**: high
- **Depends On**: Task 11
- **Description**:
  - 运行scripts/check_module_imports.py静态检查
  - 修复所有跨模块直接import违规
  - 确保模块间仅通过EventBus通信
  - 白名单import仅限于：同包内模块、core/event_bus.py、core/schemas.py、native/、标准库、第三方库
  - core/模块不得import services/模块（通过Protocol接口注入）
- **Acceptance Criteria Addressed**: AC-13
- **Test Requirements**:
  - `programmatic` TR-12.1: check_module_imports.py输出0违规
  - `programmatic` TR-12.2: grep验证无services→core反向引用
- **Notes**: 如需要可更新白名单配置

## [ ] Task 13: 启动服务器并修复启动错误
- **Priority**: high
- **Depends On**: Task 12
- **Description**:
  - 启动FastAPI服务器（python app.py或uvicorn）
  - 修复任何启动时异常（import错误、配置加载错误、数据库初始化错误）
  - 确保API端点/pools, /pools/{id}/load, /modes/{mode}/start, /events/stream正常工作
  - 确保静态文件(web/)正确服务
- **Acceptance Criteria Addressed**: AC-14, AC-15
- **Test Requirements**:
  - `programmatic` TR-13.1: 服务器启动无异常，监听端口
  - `programmatic` TR-13.2: GET /返回index.html
  - `programmatic` TR-13.3: GET /api/pools返回池列表

## [ ] Task 14: MCP Playwright浏览器端到端验证
- **Priority**: high
- **Depends On**: Task 13
- **Description**:
  - 使用MCP Playwright打开浏览器访问服务器
  - 验证页面正确加载，画布显示
  - 加载target_pool_100.json股票池配置
  - 验证拓扑图正确显示：1个备选池+2个条件节点+2个状态池(A,B)+2个交集条件节点+1个目标池(C)，共8个节点+8条边
  - 选择仿真模式
  - 点击开始运行
  - 观察备选池100只fz开头8字符股票代码
  - 等待足够时间观察：A池有KDJ金叉股票进入、B池有MACD金叉股票进入、C池有交集股票进入
  - 验证C池股票触发买单（持仓100股）
  - 验证事件浮窗实时滚动所有事件
  - 验证TTL到期后股票被删除，C池触发卖单
  - 暂停/恢复/停止功能验证
- **Acceptance Criteria Addressed**: AC-1 through AC-15
- **Test Requirements**:
  - `human-judgement` TR-14.1: 页面正常加载无JS错误
  - `human-judgement` TR-14.2: 股票池拓扑正确渲染8节点
  - `human-judgement` TR-14.3: 备选池显示100只fz8字符代码股票
  - `human-judgement` TR-14.4: 仿真模式运行后A/B池有股票进入
  - `human-judgement` TR-14.5: C池有交集股票进入
  - `human-judgement` TR-14.6: C池股票触发买单，持仓更新
  - `human-judgement` TR-14.7: 事件浮窗实时显示各类事件
  - `human-judgement` TR-14.8: 暂停/恢复/停止工作正常
  - `human-judgement` TR-14.9: K线/公式值可查看
- **Notes**: 这是最终验收，必须手动验证每个节点和事件
