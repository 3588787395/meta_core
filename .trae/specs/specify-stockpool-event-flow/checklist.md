# Checklist

## 评审打分规则（评审工程师使用）
- 每项检查点通过得满分，部分通过按比例扣分，未通过扣全部分。
- 任务总分 = 各检查点得分之和 / 检查点满分之和 × 100。
- **门槛：≥ 98 分方可进入下一任务；< 98 分打回架构工程师重做。**
- 评审工程师须给出：①总分 ②每项扣分理由 ③重做清单（若 < 98）。

## 单一定时器中断驱动
- [ ] 程序只有1个定时器——heapq 优先队列
- [ ] 所有边触发、TTL到期、tick间隔注册到同一优先队列
- [ ] 定时器按最近到期时间中断触发，禁止遍历轮询
- [ ] 定时器触发时立即注册下次（next = last_fire + interval），与模块计算无关
- [ ] TTL 入池时注册到同一队列，到时一次性触发，不注册下次
- [ ] 禁止 asyncio.sleep 或轮询检查时机门控
- [ ] 不存在 `at_fn` 延迟求值
- [ ] 不存在 `fire_ttl_due` 独立方法
- [ ] 不存在 `is_edge_due` 线性扫描

## 事件没有顺序（运行时）
- [ ] 不存在 execution_order（运行时拓扑排序）
- [ ] 所有边触发是独立的定时器事件，哪个先到时先触发
- [ ] 边顺序号（edge_order）保留，用于交集/差集运算次序
- [ ] 边顺序号是设计结构（一次性配置），与运行时事件无序不矛盾

## 引擎只发事件不执行计算
- [ ] 定时器到时→引擎发布事件+注册下次→结束
- [ ] 引擎不调用 edge_executor.run()
- [ ] 引擎不调筛选、不调转移
- [ ] 业务逻辑由订阅该事件的模块自己完成
- [ ] kind="edge" 的 action 只发布 EdgeFired
- [ ] kind="ttl" 的 action 只发布 TTLDue
- [ ] kind="tick" 的 action 只发布 TickDue

## EdgeFired 不携带 changed_codes
- [ ] EdgeFired 事件只携带 eid 和 ts
- [ ] EdgeExecutor 从 source_pool.get_dirty_codes() 取脏股票
- [ ] 不存在 event.changed_codes 引用
- [ ] 不存在 _make_edge_action 中 changed_codes 计算逻辑

## 脏股票在 tick 表变更列
- [ ] state.changed_codes 是全局脏股票集合（唯一真相源）
- [ ] 状态池是视图，不独立维护脏股票集合
- [ ] StatePoolView.get_dirty_codes() = state.changed_codes ∩ 本池股票
- [ ] StatePoolView.add_stocks() 入池 + 标脏
- [ ] StatePoolView.remove_stocks() 出池 + 标脏
- [ ] 状态池变更 = tick 变化 + 入池 + 出池
- [ ] 不存在 get_node_stocks/set_node_stocks/add_node_stocks/remove_node_stocks 扁平接口
- [ ] 不存在直接访问 state.node_stocks[nid]（StatePoolView 内部除外）

## 实例股票池配置
- [ ] 备选池含 100 只股票，仿真模式代码全部为 fz 前缀
- [ ] e1: source→pool_A，60s，5m KDJ 金叉，A池 TTL=100min
- [ ] e2: source→pool_B，10s，1m MACD 金叉，B池 TTL=200min
- [ ] e3: pool_A→pool_C，5s，交集(顺序1)
- [ ] e4: pool_B→pool_C，5s，交集(顺序2)
- [ ] C池 TTL=20min，入池=市价买入100股，出池=卖出所有持仓
- [ ] 转移条件配置在连接上（edge.params），非节点内部

## MockDataSource
- [ ] 仿真只发代码请求给 mock 数据源
- [ ] tick 由 MockDataSource 产生
- [ ] 所有股票代码用 fz 替代
- [ ] tick 间隔 1-9s 随机，同股票固定，不同股票不同
- [ ] 仿真与实盘除 tick 请求方式外共用同一套代码
- [ ] 不存在 SimTickSource 引用残留

## 公式计算与筛选分离
- [ ] 公式=添加列（写入 latest_tick[code][formula_ref]）
- [ ] 筛选=列比较/排序/集合操作（只读列，不调公式引擎）
- [ ] 使用 HQChartPy2，Python 3.13，无 cross
- [ ] 增量评估：仅对 dirty_codes 重新评估
- [ ] 合并规则：passed = (cached_passed - dirty_codes) | newly_passed

## 股票池与 K 线解耦
- [ ] BarComposer 仅发布事件，不直接操作 node_stocks
- [ ] EdgeExecutor 订阅事件后执行筛选和转移

## 交易事件链
- [ ] C池入池：TransferExecuted → Signal(buy,100) → OrderPlaced → OrderFilled → PositionUpdated
- [ ] C池出池：TTLDue → Signal(sell_all) → OrderPlaced → OrderFilled → PositionUpdated
- [ ] TTL 一次性，不注册下次

## 模块零引用约束
- [ ] core/*.py 各模块 import 语句除白名单外无跨模块引用
- [ ] 白名单：core.event_bus / core.domain / core.schemas / 标准库 / 第三方库
- [ ] 模块构造函数仅接收 EventBus + 配置 dict + 可选 Protocol 接口

## 禁止兼容旧接口
- [ ] 不存在 EdgeFired.changed_codes 字段
- [ ] 不存在 SimTickSource 引用
- [ ] 不存在 execution_order（运行时拓扑排序）引用，但边顺序号保留
- [ ] 不存在 get_node_stocks/set_node_stocks 引用
- [ ] 不存在兼容层或适配器
- [ ] 不存在 EventDriver.fire_due 线性扫描
- [ ] 不存在 at_fn 延迟求值
- [ ] 不存在 _make_edge_at_fn / _make_ttl_interval_at_fn / _make_ttl_endtime_at_fn 工厂
- [ ] 不存在 TtlTracker 独立 heapq

## Playwright 手动验证
- [ ] 仿真模式启动并加载实例股票池
- [ ] 备选池 100 只 fz 股票正确加载
- [ ] 定时器中断驱动运行
- [ ] K线合成和公式计算筛选正确并可展示
- [ ] 股票经 e1 进入 A池、经 e2 进入 B池
- [ ] A∩B 交集股票进入 C池并触发买入
- [ ] 事件面板用图标和颜色展示

## 文档更新
- [ ] DESIGN.md 补充修正后的事件流程（单定时器中断驱动、事件无序、EdgeFired 无 changed_codes、StatePoolView 视图）
- [ ] DESIGN0.md 确认定时器中断驱动架构，删除 execution_order 相关描述
