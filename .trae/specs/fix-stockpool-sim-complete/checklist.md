# 股票池仿真模式完整修复与验证 - Verification Checklist

## 代码清理与基础修复
- [ ] 根目录diagnose_sim*.py(7个)、diagnose_py313_deps.py、smoke_step.py、test_golden_cross_debug.py、test_sim_*.py(7个)等临时文件已全部删除
- [ ] 根目录仅保留app.py, api.py, converters.py, __init__.py四个Python文件
- [ ] 股票代码_normalize_stock_code函数确保8字符输出（fz+6位数字）
- [ ] 所有股票代码格式化路径统一使用同一规范化函数
- [ ] target_pool_100.json中100只股票代码全部为fz+6位数字格式

## Tick生成与K线合成
- [ ] SimTickSource为每只股票分配1-9秒固定间隔（hash确定性）
- [ ] Tick数据OHLCV字段齐全，价格波动合理
- [ ] 1分钟K线正确合成（open/high/low/close/volume）
- [ ] 5分钟K线由5根1分钟K线正确合成
- [ ] BarComposed事件在K线合成后正确发布

## 模式切换与代码路径
- [ ] ModeChanged事件正确驱动四模块切换
- [ ] 核心筛选/转移/TTL/交易逻辑无mode==simulation分支判断
- [ ] 实盘与仿真共用同一套execution/trading/statistics代码
- [ ] runtime_modes.json表驱动模式差异配置

## 公式计算与金叉条件
- [ ] KDJ指标(9,3,3)在5分钟周期正确计算K/D/J值
- [ ] MACD指标(12,26,9)在1分钟周期正确计算DIF/DEA/MACD值
- [ ] KDJ金叉（K上穿D，noperate=3）正确判断
- [ ] MACD金叉（DIF上穿DEA，noperate=3）正确判断
- [ ] FormulaEvaluated事件在公式计算后正确发布
- [ ] 公式结果写入tick列可供筛选使用

## 条件边触发与状态转移
- [ ] e_source_KDJ_A边每60秒触发一次评估
- [ ] e_source_MACD_B边每10秒触发一次评估
- [ ] e_A_INT_C和e_B_INT_C边每5秒触发一次评估
- [ ] 条件边使用中断驱动（call_later），非轮询
- [ ] 首次执行所有源节点强制dirty
- [ ] gate通过AND(源dirty OR 数据dirty)条件正确实现
- [ ] EdgeFired事件在边触发后发布
- [ ] StockFiltered事件在筛选完成后发布
- [ ] KDJ金叉股票从S→A池转移正确
- [ ] MACD金叉股票从S→B池转移正确
- [ ] TransferExecuted事件在转移完成后发布

## 交集逻辑
- [ ] INTERSECTION条件类型正确实现集合交集
- [ ] cond_INT_A_C计算A∩B，结果进入C池
- [ ] cond_INT_B_C计算B∩A，结果进入C池
- [ ] 两个交集条件节点输出相同且不重复添加股票
- [ ] 仅在A池或仅在B池的股票不进入C池

## TTL超时删除
- [ ] 股票入池时entry_time正确记录（indate+intime）
- [ ] A池股票100分钟后正确删除（ndelnum=100, ndeltype=2）
- [ ] B池股票200分钟后正确删除
- [ ] C池股票20分钟后正确删除
- [ ] TTLExpired事件在超时删除时发布
- [ ] TTL时间单位(天/小时/分钟/秒)正确解析

## 自动交易
- [ ] TradeModule正确订阅TransferExecuted事件
- [ ] 股票入C池（目标池baimpool=1）立即触发市价买单100股
- [ ] 买单成交后发布OrderPlaced→OrderFilled→PositionUpdated事件链
- [ ] 持仓记录正确更新（+100股）
- [ ] C池TTL退出触发市价卖单平全部持仓
- [ ] 卖单成交后持仓归零
- [ ] 仿真模式市价单按当前最新价立即成交（无滑点）
- [ ] 非目标池转移不触发交易

## 事件系统
- [ ] 所有10+事件类型按正确顺序发布：TickReceived→DataChanged→BarComposed→FormulaEvaluated→StockFiltered→EdgeFired→TransferExecuted→Signal→OrderPlaced→OrderFilled→PositionUpdated→StatisticsUpdated→RankingChanged→AlertRaised→SnapshotUpdated→EventLogged
- [ ] TTLExpired、ModeChanged事件正确发布
- [ ] 事件payload包含必要信息（股票代码、池ID、边ID、时间戳等）

## Web UI界面
- [ ] 事件浮窗在运行界面可见
- [ ] 所有事件类型实时显示在浮窗中
- [ ] 不同事件类型用不同颜色/图标区分
- [ ] 事件自动滚动到底部
- [ ] 事件显示时间戳和详细信息
- [ ] "未排队中"（待处理）事件可见
- [ ] 模式选择界面清晰，三个模式（实盘/回放/仿真）+设计模式互不混淆
- [ ] 加载股票池按钮明确可见
- [ ] 开始/暂停/停止按钮状态正确
- [ ] 画布拓扑正确显示所有节点和边
- [ ] 节点显示当前股票数量
- [ ] 点击节点可查看该池股票列表
- [ ] K线图表可查看
- [ ] 公式指标值可查看
- [ ] 界面布局整洁不混乱

## 模块解耦
- [ ] check_module_imports.py静态检查0违规
- [ ] 模块间无直接import，仅通过EventBus通信
- [ ] core/不直接import services/，通过Protocol注入
- [ ] 所有模块职责单一、高内聚

## 服务器与API
- [ ] 服务器启动无异常
- [ ] 静态文件(web/)正确服务
- [ ] /api/pools端点返回池列表
- [ ] /api/pools/{id}/load端点可加载池配置
- [ ] /api/modes/{mode}/start端点可切换模式并启动
- [ ] /api/events/stream SSE端点正确推送事件
- [ ] 暂停/恢复/停止API端点工作正常

## MCP Playwright端到端验收
- [ ] 浏览器打开页面无JS错误
- [ ] 股票池画布正确渲染8个节点（S+2条件+A+B+2交集+C）
- [ ] 成功加载target_pool_100.json
- [ ] 成功切换到仿真模式
- [ ] 点击开始后系统正常运行
- [ ] 备选池显示100只fz开头8字符股票代码
- [ ] 等待后A池有KDJ金叉股票进入
- [ ] 等待后B池有MACD金叉股票进入
- [ ] 等待后C池有交集股票进入
- [ ] C池股票入池后持仓变为100股（买单执行）
- [ ] 事件浮窗实时滚动显示所有事件类型
- [ ] 不同事件颜色区分明显
- [ ] 暂停按钮可暂停运行
- [ ] 恢复按钮可继续运行
- [ ] 停止按钮可停止运行
- [ ] 可查看K线数据
- [ ] 可查看公式指标计算结果
