# 股票池仿真模式完整修复与验证 - Product Requirement Document

## Overview
- **Summary**: 修复并完整实现综合大智慧/通达信功能的股票池系统，确保仿真模式下100只股票备选池、正确的KDJ/MACD金叉转移条件、交集交易、自动买卖、事件监控浮窗等所有功能正确运行，并通过MCP Playwright浏览器手动验证每个节点和事件。
- **Purpose**: 用户反复强调仿真模式除tick生成外必须与实盘共用代码，条件边和转移条件必须正确工作，股票代码截断问题（fz00001而非fz000001）必须修复，系统必须能实际运行并产生正确的交易信号。
- **Target Users**: 量化交易开发者、股票池策略验证人员。

## Goals
- 修复股票代码截断bug：仿真模式代码必须为8字符（如fz000001、fz600000），不是7字符
- 清理根目录所有垃圾文件（diagnose_sim*.py、test_sim*.py、smoke_step.py等临时文件）
- 确保条件转移边正确工作：time_gate_interval触发频率、公式条件评估、状态流转
- 正确实现仿真模式tick生成：1-9秒随机间隔，同股票间隔固定（基于股票代码hash确定固定间隔）
- 正确实现K线合成：1分钟tick→5分钟K线、1分钟K线
- 正确实现KDJ(5分钟)金叉和MACD(1分钟)金叉公式计算
- 正确实现A/B池到C池的交集逻辑
- 正确实现C池自动交易：入池立即市价买100股，20分钟TTL后市价卖全部持仓
- 确保三模式（实盘/回放/仿真）除数据源/tick生成外共用同一套处理代码
- 事件浮窗完整显示所有事件（已记录+未排队中），图形化区分不同事件类型
- 通过MCP Playwright手动创建/加载股票池、切换仿真模式、运行验证每个节点
- 确保模块间零直接引用，仅通过EventBus通信

## Non-Goals (Out of Scope)
- 不新增大智慧/通达信之外的功能
- 不重构为微服务架构
- 不实现真实券商交易接口对接（仿真用模拟交易）
- 不重写HQChartPy2公式引擎（复用现有vendor）

## Background & Context
- 项目位于 `h:\new_tdx_mock\PYPlugins\meta_core`
- 已有架构：core/目录14个模块、services/、native/、web/前端、FastAPI后端
- 已有配置：config/pools/target_pool_100.json定义了S→A/B→C拓扑
- 已知bug：股票代码截断为7字符（fz00001），应为8字符（fz000001）
- 已知问题：条件边/转移条件可能未正确连接和触发，交集逻辑可能有问题
- 根目录有12个以上临时诊断/测试脚本需要清理
- EventBus已定义30种事件类型，但事件链路完整性待验证

## Functional Requirements
- **FR-1**: 股票代码规范化 - 仿真模式下所有股票代码以'fz'开头后接6位数字（共8字符），如fz000001、fz600000、fz300750
- **FR-2**: 垃圾代码清理 - 删除根目录所有diagnose_sim*.py、test_sim*.py、smoke_step.py、test_golden_cross_debug.py等临时文件
- **FR-3**: 仿真Tick源 - SimTickSource为每只股票分配1-9秒固定间隔（基于code hash确定性分配），按间隔生成模拟tick数据，价格在基准价±2%范围内随机波动
- **FR-4**: 模式切换 - RuntimeModeModule处理ModeChanged事件，TickBar/Trade/Storage/Execution四模块正确切换数据源，仿真模式tick生成逻辑独立，其余处理路径与实盘完全一致
- **FR-5**: K线合成 - TickBarModule正确将1分钟tick合成为1分钟K线和5分钟K线，K线包含open/high/low/close/volume
- **FR-6**: 公式计算 - FormulaModule正确计算KDJ(5分钟周期)和MACD(1分钟周期)指标，支持金叉判断（上穿noperate=3）
- **FR-7**: 条件转移边执行 - ExecutionModule按time_gate_interval触发边：e_source_KDJ_A每60秒、e_source_MACD_B每10秒、e_A_INT_C/e_B_INT_C每5秒
- **FR-8**: 转移条件评估 - KDJ金叉条件：5分钟K线J值上穿K值（或K上穿D）；MACD金叉条件：1分钟K线DIF上穿DEA
- **FR-9**: 交集逻辑 - A池和B池出边通过条件节点计算交集：股票必须同时在A池和B池中才能进入C池
- **FR-10**: TTL超时删除 - A池股票100分钟后删除(ndelnum=100, ndeltype=2)，B池200分钟后删除，C池20分钟后删除
- **FR-11**: 自动交易 - TradeModule订阅TransferExecuted事件：股票入C池立即发市价买单100股；C池TTL退出时发市价卖单平全部持仓
- **FR-12**: 事件日志浮窗 - 前端UI显示事件浮窗，列出所有已发布事件，不同事件类型用不同颜色/图标区分，显示未排队中事件
- **FR-13**: 三格式导入导出 - ImportExportModule支持JSON/DZH XML/TDX XML导入导出，往返测试通过
- **FR-14**: 模块解耦 - 静态检查验证模块间无跨模块直接import，仅通过EventBus发布/订阅通信
- **FR-15**: Web UI操作 - 前端支持加载target_pool_100.json、切换到仿真模式、开始/暂停/停止运行、实时查看各池股票列表、事件日志、K线和公式值

## Non-Functional Requirements
- **NFR-1**: 事件驱动 - 所有模块协作通过EventBus异步事件完成，禁止直接模块调用
- **NFR-2**: 代码整洁 - 根目录仅保留必要文件（app.py, api.py, converters.py, __init__.py, DESIGN.md, DESIGN0.md, DZH/TDX文档），所有临时脚本移入scripts/或删除
- **NFR-3**: 启动速度 - 服务器启动时间<5秒
- **NFR-4**: 事件延迟 - tick从生成到UI显示延迟<1秒
- **NFR-5**: 可验证性 - 所有功能可通过MCP Playwright在浏览器中手动操作验证

## Constraints
- **Technical**: Python 3.13, FastAPI后端, 原生HTML/CSS/JS前端, HQChartPy2公式引擎
- **Business**: 仿真模式必须与实盘共用除tick生成外的所有代码路径
- **Dependencies**: vendor/HQChartPy2公式引擎、parquet历史数据、sqlite数据库

## Assumptions
- target_pool_100.json中的公式引用KDJ_5MIN_CROSS和MACD_1MIN_CROSS需要在builtin_formulas.json或代码中正确定义
- 现有EventBus 30种事件类型已覆盖所需事件链
- vendor/HQChartPy2路径可正确访问
- Playwright MCP环境可用

## Acceptance Criteria

### AC-1: 股票代码8字符无截断
- **Given**: 仿真模式加载target_pool_100.json
- **When**: 备选池初始化显示股票列表
- **Then**: 所有股票代码为8字符格式（fz+6位数字），如fz000001而非fz00001
- **Verification**: `programmatic` + `human-judgment`
- **Notes**: 检查_normalize_stock_code函数和domain.py中股票代码处理逻辑

### AC-2: 根目录无垃圾临时文件
- **Given**: 项目根目录
- **When**: 列出所有.py文件
- **Then**: 仅保留app.py, api.py, converters.py, __init__.py，无diagnose_*.py/test_sim*.py/smoke_*.py等临时文件
- **Verification**: `programmatic`

### AC-3: 仿真Tick按固定间隔生成
- **Given**: 仿真模式启动
- **When**: 观察tick生成时间戳
- **Then**: 每只股票tick间隔为1-9秒之间的固定值，同股票间隔相同，不同股票间隔不同
- **Verification**: `programmatic` + `human-judgment`

### AC-4: 三模式切换共用代码路径
- **Given**: 代码静态分析
- **When**: 检查ModeChanged事件处理
- **Then**: 仿真/实盘/回放仅在TickSource/DataProvider层面不同，筛选/转移/交易/统计逻辑无if mode==simulation分支
- **Verification**: `programmatic`

### AC-5: K线正确合成
- **Given**: 仿真模式运行5分钟以上
- **When**: 查看1分钟和5分钟K线数据
- **Then**: K线OHLCV正确，5分钟K线由5根1分钟K线合成
- **Verification**: `programmatic` + `human-judgment`

### AC-6: KDJ金叉条件正确触发A池转移
- **Given**: 仿真模式运行，5分钟K线形成KDJ金叉
- **When**: e_source_KDJ_A边每60秒触发评估
- **Then**: 满足KDJ金叉的股票从备选池进入A池
- **Verification**: `programmatic` + `human-judgment`

### AC-7: MACD金叉条件正确触发B池转移
- **Given**: 仿真模式运行，1分钟K线形成MACD金叉
- **When**: e_source_MACD_B边每10秒触发评估
- **Then**: 满足MACD金叉的股票从备选池进入B池
- **Verification**: `programmatic` + `human-judgment`

### AC-8: 交集逻辑正确工作
- **Given**: A池和B池中各有若干股票
- **When**: e_A_INT_C/e_B_INT_C边每5秒触发
- **Then**: 同时在A池和B池中的股票进入C池，仅在一池的股票不进入C池
- **Verification**: `programmatic` + `human-judgment`

### AC-9: C池自动买入正确执行
- **Given**: 股票进入C池
- **When**: TransferExecuted事件触发
- **Then**: TradeModule立即生成市价买单，买入100股，持仓记录更新
- **Verification**: `programmatic` + `human-judgment`

### AC-10: C池TTL卖出正确执行
- **Given**: 股票在C池停留满20分钟
- **When**: TTL超时触发
- **Then**: 股票从C池删除，TradeModule生成市价卖单卖出全部持仓
- **Verification**: `programmatic` + `human-judgment`

### AC-11: A/B池TTL正确删除
- **Given**: 股票在A池停留100分钟/B池停留200分钟
- **When**: TTL检查执行
- **Then**: 超时股票从对应池中删除
- **Verification**: `programmatic`

### AC-12: 事件浮窗完整显示
- **Given**: 仿真模式运行中
- **When**: 查看Web UI事件浮窗
- **Then**: 所有事件(TickReceived/DataChanged/BarComposed/FormulaEvaluated/StockFiltered/EdgeFired/TransferExecuted/Signal/OrderPlaced/OrderFilled/PositionUpdated/StatisticsUpdated/AlertRaised/SnapshotUpdated/EventLogged/TTLExpired)按时间顺序显示，不同事件类型颜色区分，未排队事件也可见
- **Verification**: `human-judgment`

### AC-13: 模块零直接引用
- **Given**: 运行静态检查脚本
- **When**: 执行check_module_imports.py
- **Then**: 0个跨模块违规import
- **Verification**: `programmatic`

### AC-14: MCP Playwright端到端验证通过
- **Given**: 服务器启动
- **When**: 使用Playwright打开浏览器，加载target_pool_100，切换仿真模式，运行
- **Then**: 可以在UI中看到备选池100只股票、A/B池有股票进入、C池有交集股票、交易订单生成、事件日志滚动、K线和公式值显示
- **Verification**: `human-judgment`

### AC-15: 股票池可通过UI加载并运行
- **Given**: Web UI已打开
- **When**: 点击加载target_pool_100.json，选择仿真模式，点击运行
- **Then**: 股票池拓扑正确显示在画布上，节点位置正确，开始运行后各池实时更新
- **Verification**: `human-judgment`

## Open Questions
- [ ] KDJ金叉精确定义：是J上穿K还是K上穿D？（按通达信标准，KDJ金叉指K上穿D）
- [ ] MACD金叉定义：DIF上穿DEA，是否需要DEA<0（底部金叉）还是任意位置金叉？（按用户需求，应是任意位置金叉）
- [ ] 市价单模拟：仿真模式下市价单成交价格如何确定？（按当前tick最新价成交）
