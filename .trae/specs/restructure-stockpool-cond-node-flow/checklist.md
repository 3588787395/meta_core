# Checklist

## 评审打分规则（评审工程师使用）
- 每项检查点通过得满分，部分通过按比例扣分，未通过扣全部分。
- 任务总分 = 各检查点得分之和 / 检查点满分之和 × 100。
- **门槛：≥ 98 分方可进入下一任务；< 98 分打回架构工程师重做。**
- 评审工程师须给出：①总分 ②每项扣分理由 ③重做清单（若 < 98）。

## 条件节点拓扑
- [ ] sim_test_pool_100.json 含 cond1/cond2/cond3 三个 condition 类型节点
- [ ] cond1 承载 func=KDJ(nperiod=2,noperate=3,nfirst=9,nsecond=3,cfirst=3)
- [ ] cond2 承载 func=MACD(nperiod=1,noperate=3,nfirst=12,nsecond=26,cfirst=9)
- [ ] cond3 承载 filter_spec=交集，入边顺序号 1+2
- [ ] 边拓扑：ec1(source→cond1,60s), ec1out(cond1→pool_A), ec2(source→cond2,10s), ec2out(cond2→pool_B), ec3a(pool_A→cond3,5s,order=1), ec3b(pool_B→cond3,5s,order=2), ec3out(cond3→pool_C)
- [ ] 旧边 e1/e2/e3/e4 及其 params.condition_type/tdx_func/intersection_source 已删除
- [ ] source/pool_A/pool_B/pool_C 节点配置保留（100只股票/TTL/买卖动作）
- [ ] 计算参数/K线配置/筛选条件从条件节点读取，非边

## 单一定时器中断驱动（G1）
- [ ] 程序只有1个定时器——heapq 优先队列
- [ ] 所有边触发、TTL到期、tick间隔注册到同一优先队列
- [ ] 定时器按最近到期时间中断触发，禁止遍历轮询
- [ ] 定时器触发时立即注册下次（next = last_fire + interval），与模块计算无关
- [ ] TTL 入池时注册到同一队列，到时一次性触发，不注册下次
- [ ] 禁止 asyncio.sleep 或轮询检查时机门控
- [ ] 不存在 at_fn 延迟求值
- [ ] 不存在 fire_ttl_due 独立方法
- [ ] 不存在 is_edge_due 线性扫描
- [ ] 不存在 _make_edge_at_fn/_make_ttl_interval_at_fn/_make_ttl_endtime_at_fn 工厂
- [ ] 不存在 TtlTracker 独立 heapq

## 事件没有顺序（运行时，G6）
- [ ] 不存在 execution_order（运行时拓扑排序）
- [ ] 所有边触发是独立的定时器事件，哪个先到时先触发
- [ ] 边顺序号（edge_order/_order）保留，用于交集/差集运算次序
- [ ] 边顺序号是设计结构（一次性配置），与运行时事件无序不矛盾

## 引擎只发事件不执行计算（G2）
- [ ] 定时器到时→引擎发布事件+注册下次→结束
- [ ] 引擎不调用 edge_executor.run()
- [ ] 引擎不调筛选、不调转移
- [ ] 业务逻辑由订阅该事件的模块自己完成
- [ ] kind="edge" 的 action 只发布 EdgeFired
- [ ] kind="ttl" 的 action 只发布 TTLDue
- [ ] kind="tick" 的 action 只发布 TickDue

## EdgeFired 不携带 changed_codes（G3）
- [ ] EdgeFired 事件只携带 eid 和 ts
- [ ] EdgeExecutor 从 source_pool.get_dirty_codes() 取脏股票
- [ ] 不存在 event.changed_codes 引用
- [ ] 不存在 _make_edge_action 中 changed_codes 计算逻辑

## StatePoolView 视图对象（G4）
- [ ] state.changed_codes 是全局脏股票集合（唯一真相源）
- [ ] 状态池是视图，不独立维护脏股票集合
- [ ] StatePoolView.get_dirty_codes() = state.changed_codes ∩ 本池股票
- [ ] StatePoolView.add_stocks() 入池 + 标脏
- [ ] StatePoolView.remove_stocks() 出池 + 标脏
- [ ] 状态池变更 = tick 变化 + 入池 + 出池
- [ ] 不存在 get_node_stocks/set_node_stocks/add_node_stocks/remove_node_stocks 扁平接口
- [ ] 不存在直接访问 state.node_stocks[nid]（StatePoolView 内部除外）

## MockDataSource（G5）
- [ ] 仿真只发代码请求给 mock 数据源
- [ ] tick 由 MockDataSource 产生
- [ ] 所有股票代码用 fz 替代
- [ ] tick 间隔 1-9s 随机，同股票固定，不同股票不同
- [ ] 仿真与实盘除 tick 请求方式外共用同一套代码
- [ ] 不存在 SimTickSource 引用残留

## 条件节点激活模型
- [ ] EdgeExecutor 收到 EdgeFired 后定位条件节点
- [ ] 收集条件节点所有入边按 _order 排序
- [ ] 每条入边：从源池 StatePoolView 取脏股票，按条件节点 func/indi/indiparam 计算
- [ ] 按条件节点 filter_spec 筛选得 port_results[order]
- [ ] 单入边直接输出；多入边按 filter_spec 做交集/差集/并集
- [ ] 通过出边输出到目标池，add_stocks + 标脏 + 注册 TTL
- [ ] C池入池触发买入事件链
- [ ] 公式计算与筛选严格分离：公式=添加列，筛选=列操作

## 公式计算与筛选分离
- [ ] 公式=添加列（写入 latest_tick[code][formula_ref]）
- [ ] 筛选=列比较/排序/集合操作（只读列，不调公式引擎）
- [ ] 使用 HQChartPy2，Python 3.13，无 cross
- [ ] 增量评估：仅对 dirty_codes 重新评估
- [ ] 合并规则：passed = (cached_passed - dirty_codes) | newly_passed

## 股票池与 K 线解耦
- [ ] BarComposer 仅发布事件，不直接操作 node_stocks
- [ ] EdgeExecutor 订阅事件后执行条件节点激活+筛选+转移

## 交易事件链
- [ ] C池入池：TransferExecuted → Signal(buy,100) → OrderPlaced → OrderFilled → PositionUpdated
- [ ] C池出池：TTLDue → Signal(sell_all) → OrderPlaced → OrderFilled → PositionUpdated
- [ ] TTL 一次性，不注册下次

## 可视化条件节点
- [ ] 股票池设计界面显示条件节点矩形（cond1/cond2/cond3）
- [ ] 条件节点矩形可点击打开配置面板，配置 func/indi/indiparam/filter_spec
- [ ] 入边显示触发频率，多入边显示顺序号
- [ ] 事件面板用图标和颜色展示条件节点激活/筛选/转移事件

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

## Playwright 手动验证
- [ ] 仿真模式启动并加载实例股票池
- [ ] 备选池 100 只 fz 股票正确加载
- [ ] 条件节点 cond1/cond2/cond3 在界面显示
- [ ] 定时器中断驱动运行
- [ ] K线合成和公式计算筛选正确并可展示
- [ ] 股票经 cond1 进入 A池、经 cond2 进入 B池
- [ ] A∩B 交集股票经 cond3 进入 C池并触发买入
- [ ] 事件面板用图标和颜色展示

## 文档更新
- [ ] DESIGN.md 补充条件节点拓扑、单定时器中断驱动、事件无序、EdgeFired 无 changed_codes、StatePoolView 视图、条件节点激活流程
- [ ] DESIGN0.md 确认定时器中断驱动架构，删除 execution_order 相关描述，补充条件节点模型
