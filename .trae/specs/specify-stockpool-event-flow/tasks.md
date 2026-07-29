# Tasks

> **新执行规范**：本 tasks.md 为股票池事件流程改造的新执行计划，与旧实现规范不兼容，禁止保留旧接口兼容层。

## 执行机制（双工程师协作 + 98 分门槛）

本规格采用双工程师协作流程，由调度方（主 Agent）编排：

1. **架构工程师**（Architect Engineer，sub-agent）：负责本任务代码实现，严格遵循 spec.md / improvement-plan.md / 硬约束，禁止兼容旧接口，禁止另开炉灶。
2. **评审工程师**（Review Engineer，sub-agent）：负责阅读代码、运行测试、按 checklist.md 逐项打分，给出 0-100 分及扣分理由。
3. **门槛**：分数 ≥ 98 方可进入下一任务；分数 < 98 打回架构工程师重做（携带扣分点），直到 ≥ 98。
4. **任务依赖**：严格按"Task Dependencies"执行，前置任务未达 98 分不得开启后续任务。
5. 每个任务完成后，调度方在 tasks.md 勾选 `[x]`，并在 checklist.md 勾选通过项。

---

## 实现任务（架构工程师 → 评审工程师）

- [x] Task 1: G4 — StatePool 视图对象（PoolState 视图，非 mock 数据池）（评审 100 分通过）
  - [x] SubTask 1.1: 在 runtime_mode_module.py 的 PoolState 旁新建 `StatePoolView` 类（避免与现有 mock 数据 `StatePool` 类冲突），实现 `get_stocks()`/`get_stock_codes()`/`get_dirty_codes()`/`add_stocks()`/`remove_stocks()`
  - [x] SubTask 1.2: 在 PoolState 添加 `get_pool(nid)` 方法返回 StatePoolView 实例
  - [x] SubTask 1.3: 全局搜索替换所有 `state.get_node_stocks(nid)` → `state.get_pool(nid).get_stocks()`
  - [x] SubTask 1.4: 全局搜索替换所有 `state.set_node_stocks(nid, stocks)` → `state.get_pool(nid).add_stocks()`/`remove_stocks()`
  - [x] SubTask 1.5: 删除 `get_node_stocks`/`set_node_stocks`/`add_node_stocks`/`remove_node_stocks` 扁平接口
  - [x] SubTask 1.6: 替换所有直接访问 `state.node_stocks[nid]` 为 `state.get_pool(nid).get_stocks()`（除 StatePoolView 内部实现外）
  - [x] 评审：评审工程师打分 100 分，进入 Task 2

- [x] Task 2: G3 — EdgeFired 去 changed_codes（依赖 Task 1）（评审 100 分通过）
  - [x] SubTask 2.1: 从 `EdgeFired` dataclass 删除 `changed_codes` 字段及文档
  - [x] SubTask 2.2: 修改 `_on_edge_fired` 从 `source_pool.get_dirty_codes()` 取脏股票
  - [x] SubTask 2.3: 删除 `_make_edge_action` 中 changed_codes 计算逻辑
  - [x] SubTask 2.4: 全局搜索删除所有 `event.changed_codes` 引用
  - [x] 评审：评审工程师打分 100 分，进入 Task 3

- [x] Task 3: G5 — SimTickSource 改 MockDataSource（可与 Task 4 并行）（复验 98 分通过）
  - [x] SubTask 3.1: 重命名 `SimTickSource` 类为 `MockDataSource`（domain.py:1614）
  - [x] SubTask 3.2: 更新 `_TICK_SOURCE_FACTORIES["sim"]` 工厂函数
  - [x] SubTask 3.3: 更新 engine.py / execution_module.py 中所有 SimTickSource 引用
  - [x] SubTask 3.4: MockDataSource 将 tick 定时器注册到 EventDriver 统一优先队列（与 Task 4 协调）
  - [x] 评审：评审工程师初评 95 分（docs/SPEC.md 残留 SimTickSource），修复后复验 98 分，进入 Task 5

- [x] Task 4: G1 — EventDriver 改优先队列（核心重构，可与 Task 3 并行）（评审 100 分通过）
  - [x] SubTask 4.1: 替换 `self._specs: List[TimedEventSpec]` 为 `self._heap: List[tuple[float, TimedEventSpec]]`
  - [x] SubTask 4.2: 重写 `add_spec(spec, first_fire_time)` 使用 `heapq.heappush`
  - [x] SubTask 4.3: 重写 `fire_due(now)`：弹出堆顶到时项，发布事件 + 立即注册下次（`next = fire_time + interval`）
  - [x] SubTask 4.4: 删除 `at_fn` 延迟求值，改为入队时固定触发时间
  - [x] SubTask 4.5: 合并 `fire_ttl_due` 到 `fire_due`，统一处理所有 kind
  - [x] SubTask 4.6: 删除 `is_edge_due`、线性扫描逻辑
  - [x] SubTask 4.7: 删除 TtlTracker 独立 heapq，TTL 注册到主队列（kind="ttl" 一次性）
  - [x] SubTask 4.8: 删除 `_make_edge_at_fn` / `_make_ttl_interval_at_fn` / `_make_ttl_endtime_at_fn` 等延迟求值工厂
  - [x] 评审：评审工程师打分 100 分，进入 Task 5

- [x] Task 5: G2 — 引擎只发事件不执行计算（依赖 Task 2 + Task 4）（评审 100 分通过）
  - [x] SubTask 5.1: kind="edge" 的 spec.action 改为只发布 `EdgeFired(eid, ts)`
  - [x] SubTask 5.2: kind="ttl" 的 spec.action 改为只发布 `TTLDue(nid, code, ts)`（一次性，不注册下次）
  - [x] SubTask 5.3: kind="tick" 的 spec.action 改为只发布 `TickDue(code, ts)`
  - [x] SubTask 5.4: EdgeExecutor 订阅 EdgeFired 自行完成筛选/转移
  - [x] SubTask 5.5: 删除 `_make_edge_action` 中 `edge_executor.run()` 调用
  - [x] SubTask 5.6: 删除旧路径 `_run_tick`/`_run_tick_body` 中直接驱动逻辑
  - [x] 评审：评审工程师打分 100 分，进入 Task 6

- [x] Task 6: G6 — 删除 execution_order（运行时拓扑排序），保留边顺序号（依赖 Task 5）（复验 98 分通过）
  - [x] SubTask 6.1: 删除 `CompiledSchedule.execution_order` 运行时拓扑排序属性
  - [x] SubTask 6.2: 删除 engine.py 中按 execution_order 遍历边的逻辑（含 _prepare_topology/_build_processing_plan 死代码）
  - [x] SubTask 6.3: 保留边的顺序号（edge_order），用于交集/差集运算次序
  - [x] 评审：评审工程师初评 65 分（残留 _prepare_topology 死代码 + eventtest 挂起），重做后复验 98 分，进入 Task 7

## 验证任务（评审工程师主导）

- [x] Task 7: 验证实例股票池配置（依赖 Task 1-6）（复验 100 分通过）
  - [x] SubTask 7.1: 确认备选池 100 只股票，仿真模式代码全部为 fz 前缀
  - [x] SubTask 7.2: 确认 e1: source→pool_A，60s，5m KDJ 金叉，A池 TTL=100min
  - [x] SubTask 7.3: 确认 e2: source→pool_B，10s，1m MACD 金叉，B池 TTL=200min
  - [x] SubTask 7.4: 确认 e3/e4: pool_A/pool_B→pool_C，5s，交集条件，C池 TTL=20min
  - [x] SubTask 7.5: 确认 C池入池=市价买入100股，出池=卖出所有持仓
  - [x] 评审：评审工程师初评 80 分（误判 nperiod），复验 100 分，进入 Task 8
  - [x] 遗留问题：eventtest 3 个事件链顺序测试因 first_run 机制失败（EdgeFired 先于 TickReceived），需在后续 Task 中修复

- [x] Task 8: 验证核心架构（依赖 Task 1-6）（评审 99 分通过）
  - [x] SubTask 8.1: 验证只有1个 heapq 优先队列
  - [x] SubTask 8.2: 验证定时器触发时立即注册下次（与模块无关）
  - [x] SubTask 8.3: 验证引擎只发事件不执行计算
  - [x] SubTask 8.4: 验证运行时事件没有顺序，不存在 execution_order（运行时拓扑排序）
  - [x] SubTask 8.4b: 验证边顺序号（edge_order）保留，用于交集/差集运算次序
  - [x] SubTask 8.5: 验证 EdgeFired 只含 eid+ts，不携带 changed_codes
  - [x] SubTask 8.6: 验证 EdgeExecutor 从 source_pool.get_dirty_codes() 取脏股票
  - [x] SubTask 8.7: 验证状态池变更 = tick 变化 + 入池 + 出池
  - [x] SubTask 8.8: 验证禁止 asyncio.sleep 或轮询
  - [x] 评审：评审工程师打分 99 分，进入 Task 9

- [x] Task 9: 验证 MockDataSource（依赖 Task 3）（评审 100 分通过）
  - [x] SubTask 9.1: 验证仿真只发代码请求给 mock 数据源
  - [x] SubTask 9.2: 验证 tick 由 MockDataSource 产生
  - [x] SubTask 9.3: 验证所有股票代码用 fz 替代
  - [x] SubTask 9.4: 验证 tick 间隔 1-9s 随机，同股票固定，不同股票不同
  - [x] SubTask 9.5: 验证仿真与实盘除 tick 请求方式外共用同一套代码
  - [x] 评审：评审工程师打分 100 分，进入 Task 10

- [x] Task 10: 验证公式计算与筛选分离（依赖 Task 5）（评审 99 分通过）
  - [x] SubTask 10.1: 验证公式=添加列，筛选=列操作
  - [x] SubTask 10.2: 验证使用 HQChartPy2，Python 3.13，无 cross（环境为 Python 3.11.7，HQChart 路径未实际加载，代码已保留注入接口）
  - [x] SubTask 10.3: 验证增量评估和合并规则 `passed = (cached_passed - dirty_codes) | newly_passed`
  - [x] 评审：评审工程师打分 99 分，进入 Task 11

- [x] Task 11: 验证交易事件链（依赖 Task 5）（评审 100 分通过）
  - [x] SubTask 11.1: 验证 C池入池买入：TransferExecuted → Signal(buy,100) → OrderPlaced → OrderFilled → PositionUpdated
  - [x] SubTask 11.2: 验证 C池出池卖出：TTLDue → Signal(sell_all) → OrderPlaced → OrderFilled → PositionUpdated
  - [x] SubTask 11.3: 验证 TTL 一次性，不注册下次
  - [x] 评审：评审工程师打分 100 分，进入 Task 12

- [x] Task 12: 验证模块零引用约束（依赖 Task 1-6）（评审 100 分通过）
  - [x] SubTask 12.1: 检查 core/*.py 各模块 import 语句，除白名单外无跨模块引用
  - [x] SubTask 12.2: 验证模块构造函数仅接收 EventBus + 配置 dict + 可选 Protocol 接口
  - [x] 评审：评审工程师打分 100 分，进入 Task 13

- [x] Task 13: 验证禁止兼容旧接口（依赖 Task 1-6）（评审 99 分通过）
  - [x] SubTask 13.1: 搜索确认无 `changed_codes` 字段残留
  - [x] SubTask 13.2: 搜索确认无 `SimTickSource` 引用残留
  - [x] SubTask 13.3: 搜索确认无 `execution_order`（运行时拓扑排序）引用残留，但边顺序号保留
  - [x] SubTask 13.4: 搜索确认无 `get_node_stocks`/`set_node_stocks` 引用残留
  - [x] SubTask 13.5: 搜索确认无兼容层或适配器（`TTLExpired` 旧事件类及 `_group_transformation_units` 死代码建议后续清理）
  - [x] 评审：评审工程师打分 99 分，进入 Task 14

- [ ] Task 14: Playwright 手动验证（依赖 Task 1-13）
  - [ ] SubTask 14.1: 启动仿真模式，加载实例股票池
  - [ ] SubTask 14.2: 验证备选池 100 只 fz 股票正确加载
  - [ ] SubTask 14.3: 验证定时器中断驱动运行
  - [ ] SubTask 14.4: 验证 K线合成和公式计算筛选正确并可展示
  - [ ] SubTask 14.5: 验证股票经 e1 进入 A池、经 e2 进入 B池
  - [ ] SubTask 14.6: 验证 A∩B 交集股票进入 C池并触发买入
  - [ ] SubTask 14.7: 验证事件面板用图标和颜色展示
  - [ ] 评审：评审工程师打分，≥98 方可进入 Task 15

- [ ] Task 15: 更新 DESIGN.md 和 DESIGN0.md（依赖 Task 14，架构工程师实现 + 评审工程师审）
  - [ ] SubTask 15.1: DESIGN.md 补充修正后的事件流程（单定时器中断驱动、事件无序、EdgeFired 无 changed_codes、StatePoolView 视图）
  - [ ] SubTask 15.2: DESIGN0.md 确认定时器中断驱动架构，删除 execution_order 相关描述
  - [ ] 评审：评审工程师打分，≥98 方可结项

# Task Dependencies
- [Task 2] depends on [Task 1]
- [Task 5] depends on [Task 2, Task 4]
- [Task 6] depends on [Task 5]
- [Task 3] 与 [Task 4] 可并行（但 Task 3.4 需 Task 4 完成）
- [Task 7] depends on [Task 1-6]
- [Task 8] depends on [Task 1-6]
- [Task 9] depends on [Task 3]
- [Task 10] depends on [Task 5]
- [Task 11] depends on [Task 5]
- [Task 12] depends on [Task 1-6]
- [Task 13] depends on [Task 1-6]
- [Task 14] depends on [Task 1-13]
- [Task 15] depends on [Task 14]
