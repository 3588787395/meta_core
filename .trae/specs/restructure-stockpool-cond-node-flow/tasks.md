# Tasks

## 执行机制（双工程师协作 + 98 分门槛）

本规格采用双工程师协作流程，由调度方（主 Agent）编排：

1. **架构工程师**（Architect Engineer，sub-agent）：负责本任务代码实现，严格遵循 spec.md，禁止兼容旧接口，禁止另开炉灶，禁止硬编码实例内容。
2. **评审工程师**（Review Engineer，sub-agent）：负责阅读代码、运行测试、按 checklist.md 逐项打分，给出 0-100 分及扣分理由。
3. **门槛**：分数 ≥ 98 方可进入下一任务；分数 < 98 打回架构工程师重做（携带扣分点），直到 ≥ 98。
4. **任务依赖**：严格按"Task Dependencies"执行，前置任务未达 98 分不得开启后续任务。
5. 每个任务完成后，调度方在 tasks.md 勾选 `[x]`，并在 checklist.md 勾选通过项。

---

## 实现任务（架构工程师 → 评审工程师）

- [x] Task 1: 实例配置重构 — sim_test_pool_100.json 改条件节点拓扑（评审 100 分通过）
  - [ ] SubTask 1.1: 新增 cond1 节点（type=condition），承载 func=KDJ(nset=0,nperiod=2,accode=KDJ,noperate=3,nfirst=9,nsecond=3,cfirst=3,formula_args={N:9,M1:3,M2:3})，filter_spec=金叉
  - [ ] SubTask 1.2: 新增 cond2 节点（type=condition），承载 func=MACD(nset=0,nperiod=1,accode=MACD,noperate=3,nfirst=12,nsecond=26,cfirst=9,formula_args={SHORT:12,LONG:26,MID:9})，filter_spec=金叉
  - [ ] SubTask 1.3: 新增 cond3 节点（type=condition），filter_spec=交集，入边顺序号 1+2
  - [ ] SubTask 1.4: 重构边拓扑：ec1(source→cond1,60s), ec1out(cond1→pool_A), ec2(source→cond2,10s), ec2out(cond2→pool_B), ec3a(pool_A→cond3,5s,order=1), ec3b(pool_B→cond3,5s,order=2), ec3out(cond3→pool_C)
  - [ ] SubTask 1.5: 删除旧边 e1/e2/e3/e4 及其 params 中的 condition_type/tdx_func/intersection_source（迁移到条件节点）
  - [ ] SubTask 1.6: 保留 source(100只股票)/pool_A(TTL=6000s)/pool_B(TTL=12000s)/pool_C(TTL=1200s,enter_action=market_buy 100,exit_action=market_sell_all) 节点配置
  - [ ] 评审：评审工程师打分，≥98 方可进入 Task 2

- [x] Task 2: G4 — StatePoolView 视图对象（PoolState 视图，非 mock 数据 StatePool）（评审 100 分通过）
  - [ ] SubTask 2.1: 在 runtime_mode_module.py 的 PoolState 旁新建 `StatePoolView` 类（避免与现有 mock 数据 StatePool 冲突），实现 get_stocks()/get_stock_codes()/get_dirty_codes()/add_stocks()/remove_stocks()
  - [ ] SubTask 2.2: 在 PoolState 添加 get_pool(nid) 方法返回 StatePoolView 实例
  - [ ] SubTask 2.3: 全局替换 state.get_node_stocks(nid) → state.get_pool(nid).get_stocks()
  - [ ] SubTask 2.4: 全局替换 state.set_node_stocks(nid, stocks) → state.get_pool(nid).add_stocks()/remove_stocks()
  - [ ] SubTask 2.5: 删除 get_node_stocks/set_node_stocks/add_node_stocks/remove_node_stocks 扁平接口
  - [ ] SubTask 2.6: 替换所有直接访问 state.node_stocks[nid] 为 state.get_pool(nid).get_stocks()（StatePoolView 内部除外）
  - [ ] 评审：评审工程师打分，≥98 方可进入 Task 3

- [x] Task 3: G3 — EdgeFired 去 changed_codes（依赖 Task 2）（评审 100 分通过）
  - [ ] SubTask 3.1: 从 EdgeFired dataclass 删除 changed_codes 字段及文档
  - [ ] SubTask 3.2: 修改 _on_edge_fired 从 source_pool.get_dirty_codes() 取脏股票
  - [ ] SubTask 3.3: 删除 _make_edge_action 中 changed_codes 计算逻辑
  - [ ] SubTask 3.4: 全局删除所有 event.changed_codes 引用
  - [ ] 评审：评审工程师打分，≥98 方可进入 Task 4

- [x] Task 4: G5 — SimTickSource 改 MockDataSource（评审 100 分通过）
  - [ ] SubTask 4.1: 重命名 SimTickSource 类为 MockDataSource（domain.py:1614）
  - [ ] SubTask 4.2: 更新 _TICK_SOURCE_FACTORIES["sim"] 工厂函数
  - [ ] SubTask 4.3: 更新 engine.py/execution_module.py 中所有 SimTickSource 引用
  - [ ] SubTask 4.4: MockDataSource 将 tick 定时器注册到 EventDriver 统一优先队列（与 Task 5 协调）
  - [ ] 评审：评审工程师打分，≥98 方可进入 Task 6

- [x] Task 5: G1 — EventDriver 改优先队列（核心重构）（评审 100 分通过）
  - [ ] SubTask 5.1: 替换 self._specs: List 为 self._heap: List[tuple[float, spec]]
  - [ ] SubTask 5.2: 重写 add_spec(spec, first_fire_time) 使用 heapq.heappush
  - [ ] SubTask 5.3: 重写 fire_due(now)：弹出堆顶到时项，发布事件 + 立即注册下次（next = fire_time + interval）
  - [ ] SubTask 5.4: 删除 at_fn 延迟求值，改为入队时固定触发时间
  - [ ] SubTask 5.5: 合并 fire_ttl_due 到 fire_due，统一处理所有 kind
  - [ ] SubTask 5.6: 删除 is_edge_due、线性扫描逻辑
  - [ ] SubTask 5.7: 删除 TtlTracker 独立 heapq，TTL 注册到主队列（kind="ttl" 一次性）
  - [ ] SubTask 5.8: 删除 _make_edge_at_fn/_make_ttl_interval_at_fn/_make_ttl_endtime_at_fn 延迟求值工厂
  - [ ] 评审：评审工程师打分，≥98 方可进入 Task 6

- [x] Task 6: G2 — 引擎只发事件不执行计算（依赖 Task 3 + Task 5）（评审 100 分通过）
  - [ ] SubTask 6.1: kind="edge" 的 spec.action 改为只发布 EdgeFired(eid, ts)
  - [ ] SubTask 6.2: kind="ttl" 的 spec.action 改为只发布 TTLDue(nid, code, ts)（一次性，不注册下次）
  - [ ] SubTask 6.3: kind="tick" 的 spec.action 改为只发布 TickDue(code, ts)
  - [ ] SubTask 6.4: EdgeExecutor 订阅 EdgeFired 自行完成条件节点激活+筛选+转移
  - [ ] SubTask 6.5: 删除 _make_edge_action 中 edge_executor.run() 调用
  - [ ] SubTask 6.6: 删除旧路径 _run_tick/_run_tick_body 中直接驱动逻辑
  - [ ] 评审：评审工程师打分，≥98 方可进入 Task 7

- [x] Task 7: G6 — 删除 execution_order（运行时拓扑排序），保留边顺序号（依赖 Task 6）（评审 100 分通过）
  - [ ] SubTask 7.1: 删除 CompiledSchedule.execution_order 运行时拓扑排序属性
  - [ ] SubTask 7.2: 删除 engine.py 中按 execution_order 遍历边的逻辑
  - [ ] SubTask 7.3: 保留边的顺序号（edge_order/_order），用于交集/差集运算次序
  - [ ] 评审：评审工程师打分，≥98 方可进入 Task 8

- [x] Task 8: 条件节点激活模型 — EdgeExecutor 改造（依赖 Task 1-7）（评审 98 分通过 + 交集语义修复）
  - [ ] SubTask 8.1: EdgeExecutor 收到 EdgeFired(eid) 后，定位 eid 目标节点为条件节点
  - [ ] SubTask 8.2: 收集条件节点的所有入边（按 _order 排序），对每条入边：从源池 StatePoolView 取脏股票，按条件节点 func/indi/indiparam 计算，按 filter_spec 筛选，得 port_results[order]
  - [ ] SubTask 8.3: 集合运算：单入边直接输出；多入边按 filter_spec 做交集/差集/并集
  - [ ] SubTask 8.4: 通过条件节点出边输出到目标池，add_stocks + 标脏 + 注册 TTL
  - [ ] SubTask 8.5: C池入池触发买入：TransferExecuted → Signal(buy,100) → OrderPlaced → OrderFilled → PositionUpdated
  - [ ] SubTask 8.6: 公式计算与筛选严格分离：公式=添加列，筛选=列操作，HQChartPy2，无 cross
  - [ ] 评审：评审工程师打分，≥98 方可进入 Task 9

- [x] Task 9: 可视化条件节点（依赖 Task 8）（评审 100 分通过）
  - [x] SubTask 9.1: 股票池设计界面显示条件节点矩形（cond1/cond2/cond3）
  - [x] SubTask 9.2: 条件节点矩形可点击打开配置面板，配置 func/indi/indiparam/filter_spec
  - [x] SubTask 9.3: 入边显示触发频率，多入边显示顺序号
  - [x] SubTask 9.4: 事件面板用图标和颜色展示条件节点激活/筛选/转移事件
  - [ ] 评审：评审工程师打分，≥98 方可进入验证任务

## 验证任务（评审工程师主导）

- [x] Task 10: 验证实例配置与拓扑（依赖 Task 1-9）（评审 100 分通过）
  - [ ] SubTask 10.1: 确认 source 100只股票，仿真模式 fz 前缀
  - [ ] SubTask 10.2: 确认 cond1: KDJ金叉, 5m; cond2: MACD金叉, 1m; cond3: 交集
  - [ ] SubTask 10.3: 确认 pool_A TTL=6000s, pool_B TTL=12000s, pool_C TTL=1200s+买入100股+卖出
  - [ ] SubTask 10.4: 确认边拓扑：ec1(60s)/ec2(10s)/ec3a+ec3b(5s,order1+2), 出边跟随
  - [ ] 评审：评审工程师打分，≥98 方可进入 Task 11

- [x] Task 11: 验证核心架构（依赖 Task 1-9）（评审 100 分通过）
  - [ ] SubTask 11.1: 验证只有1个 heapq 优先队列
  - [ ] SubTask 11.2: 验证定时器触发时立即注册下次（与模块无关）
  - [ ] SubTask 11.3: 验证引擎只发事件不执行计算
  - [ ] SubTask 11.4: 验证运行时事件无序，不存在 execution_order（运行时拓扑排序）
  - [ ] SubTask 11.5: 验证边顺序号保留用于交集/差集运算
  - [ ] SubTask 11.6: 验证 EdgeFired 只含 eid+ts，不携带 changed_codes
  - [ ] SubTask 11.7: 验证 EdgeExecutor 从 source_pool.get_dirty_codes() 取脏股票
  - [ ] SubTask 11.8: 验证状态池变更 = tick 变化 + 入池 + 出池
  - [ ] SubTask 11.9: 验证禁止 asyncio.sleep 或轮询
  - [ ] 评审：评审工程师打分，≥98 方可进入 Task 12

- [x] Task 12: 验证条件节点激活与公式筛选（依赖 Task 8）（评审 100 分通过）
  - [ ] SubTask 12.1: 验证 cond1 激活：读取 source 脏股票→KDJ计算→金叉筛选→入 pool_A
  - [ ] SubTask 12.2: 验证 cond2 激活：读取 source 脏股票→MACD计算→金叉筛选→入 pool_B
  - [ ] SubTask 12.3: 验证 cond3 激活：读取 pool_A+pool_B→交集→入 pool_C
  - [ ] SubTask 12.4: 验证公式=添加列，筛选=列操作，HQChartPy2，无 cross
  - [ ] SubTask 12.5: 验证增量评估和合并规则 passed = (cached_passed - dirty_codes) | newly_passed
  - [ ] 评审：评审工程师打分，≥98 方可进入 Task 13

- [x] Task 13: 验证交易事件链与 TTL（依赖 Task 8）（评审 100 分通过）
  - [ ] SubTask 13.1: 验证 C池入池买入：TransferExecuted → Signal(buy,100) → OrderPlaced → OrderFilled → PositionUpdated
  - [ ] SubTask 13.2: 验证 C池出池卖出：TTLDue → Signal(sell_all) → OrderPlaced → OrderFilled → PositionUpdated
  - [ ] SubTask 13.3: 验证 TTL 一次性，不注册下次
  - [ ] 评审：评审工程师打分，≥98 方可进入 Task 14

- [x] Task 14: 验证 MockDataSource 与仿真（依赖 Task 4）（评审 100 分通过）
  - [ ] SubTask 14.1: 验证仿真只发代码请求给 mock 数据源
  - [ ] SubTask 14.2: 验证 tick 由 MockDataSource 产生
  - [ ] SubTask 14.3: 验证所有股票代码用 fz 替代
  - [ ] SubTask 14.4: 验证 tick 间隔 1-9s 随机，同股票固定，不同股票不同
  - [ ] SubTask 14.5: 验证仿真与实盘除 tick 请求方式外共用同一套代码
  - [ ] 评审：评审工程师打分，≥98 方可进入 Task 15

- [x] Task 15: 验证模块零引用与禁止兼容旧接口（依赖 Task 1-9）（重做后评审 99.5 分通过）
  - [ ] SubTask 15.1: 检查 core/*.py 各模块 import 语句，除白名单外无跨模块引用
  - [ ] SubTask 15.2: 搜索确认无 changed_codes 字段残留
  - [ ] SubTask 15.3: 搜索确认无 SimTickSource 引用残留
  - [ ] SubTask 15.4: 搜索确认无 execution_order（运行时拓扑排序）引用残留，但边顺序号保留
  - [ ] SubTask 15.5: 搜索确认无 get_node_stocks/set_node_stocks 引用残留
  - [ ] SubTask 15.6: 搜索确认无兼容层或适配器
  - [ ] 评审：评审工程师打分，≥98 方可进入 Task 16

- [x] Task 16: Playwright 手动验证（依赖 Task 1-15）（评审 100 分通过，修复 5 个运行时 bug）
  - [ ] SubTask 16.1: 启动仿真模式，加载实例股票池
  - [ ] SubTask 16.2: 验证备选池 100 只 fz 股票正确加载
  - [ ] SubTask 16.3: 验证条件节点 cond1/cond2/cond3 在界面显示
  - [ ] SubTask 16.4: 验证定时器中断驱动运行
  - [ ] SubTask 16.5: 验证 K线合成和公式计算筛选正确并可展示
  - [ ] SubTask 16.6: 验证股票经 cond1 进入 A池、经 cond2 进入 B池
  - [ ] SubTask 16.7: 验证 A∩B 交集股票经 cond3 进入 C池并触发买入
  - [ ] SubTask 16.8: 验证事件面板用图标和颜色展示
  - [ ] 评审：评审工程师打分，≥98 方可进入 Task 17

- [x] Task 17: 更新 DESIGN.md 和 DESIGN0.md（依赖 Task 16，架构工程师实现 + 评审工程师审）（评审 100 分通过）
  - [ ] SubTask 17.1: DESIGN.md 补充条件节点拓扑、单定时器中断驱动、事件无序、EdgeFired 无 changed_codes、StatePoolView 视图、条件节点激活流程
  - [ ] SubTask 17.2: DESIGN0.md 确认定时器中断驱动架构，删除 execution_order 相关描述，补充条件节点模型
  - [ ] 评审：评审工程师打分，≥98 方可结项

# Task Dependencies
- [Task 3] depends on [Task 2]
- [Task 6] depends on [Task 3, Task 5]
- [Task 7] depends on [Task 6]
- [Task 8] depends on [Task 1, Task 2, Task 3, Task 5, Task 6, Task 7]
- [Task 9] depends on [Task 8]
- [Task 4] 与 [Task 5] 可并行（但 Task 4.4 需 Task 5 完成）
- [Task 10] depends on [Task 1-9]
- [Task 11] depends on [Task 1-9]
- [Task 12] depends on [Task 8]
- [Task 13] depends on [Task 8]
- [Task 14] depends on [Task 4]
- [Task 15] depends on [Task 1-9]
- [Task 16] depends on [Task 1-15]
- [Task 17] depends on [Task 16]
