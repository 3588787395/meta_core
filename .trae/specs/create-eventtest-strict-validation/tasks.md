# Tasks

## 执行机制（双工程师协作 + 98 分门槛）

本规格采用双工程师协作流程，由调度方（主 Agent）编排：

1. **架构工程师**（Architect Engineer，sub-agent）：负责本任务代码实现，严格遵循 spec.md，禁止兼容旧接口，禁止另开炉灶，禁止硬编码实例内容。
2. **评审工程师**（Review Engineer，sub-agent）：负责运行 `python -m eventtest.run_eventtest`，按 checklist.md 逐项打分，给出 0-100 分及扣分理由。**必须以量化测试结果作为打分依据，不得仅靠代码审查。**
3. **门槛**：分数 ≥ 98 方可进入下一任务；分数 < 98 打回架构工程师重做（携带扣分点 + 测试失败清单），直到 ≥ 98。
4. **任务依赖**：严格按"Task Dependencies"执行，前置任务未达 98 分不得开启后续任务。
5. 每个任务完成后，调度方在 tasks.md 勾选 `[x]`，并在 checklist.md 勾选通过项。

---

## 实现任务（架构工程师 → 评审工程师）

- [x] Task 1: 创建 eventtest 目录骨架 + conftest.py + run_eventtest.py（复评 100 分通过）
  - [x] SubTask 1.1: 在项目根目录创建 `eventtest/` 目录与 `__init__.py`
  - [x] SubTask 1.2: 编写 `conftest.py`，提供五个 fixture：`virtual_clock`（虚拟时钟 34500.0）、`fz_stocks`（生成 N 只 fz 股票代码）、`pool_engine`（装配 PoolEngine 实例）、`event_collector`（订阅 EventBus 全部事件并收集）、`pool_snapshot`（取各池状态快照）
  - [x] SubTask 1.3: 编写 `run_eventtest.py` 测试运行器，运行全部测试并输出量化报告（测试总数/通过数/失败数/通过率/各测试耗时/事件计数表/池状态快照表/退出码）
  - [x] SubTask 1.4: 编写 `README.md` 说明方法论与运行方式
  - [x] 评审：评审工程师打分 100 分，≥98 通过

- [x] Task 2: 正测试 — MockDataSource + EventDriver 单定时器中断（G1/G5）（评审 100 分通过）
  - [x] SubTask 2.1: 编写 `test_positive_mockdatasource.py`：MD5 种子确定性、同股票间隔固定、不同股票间隔不同、fz 前缀、tick 数量断言
  - [x] SubTask 2.2: 编写 `test_positive_eventdriver.py`：内部仅 1 个 heapq、fire_due 弹堆顶立即 heappush 下次、TTL 一次性不注册下次、无 at_fn/fire_ttl_due/is_edge_due/TtlTracker 残留
  - [x] 评审：评审工程师运行测试打分 100 分，≥98 通过

- [x] Task 3: 正测试 — StatePoolView + EdgeFired 去脏股票（G3/G4）（评审 100 分通过）
  - [x] SubTask 3.1: 编写 `test_positive_statepoolview.py`：get_pool 返回 StatePoolView、get_dirty_codes = state.changed_codes ∩ 本池股票、add_stocks 入池标脏、remove_stocks 出池标脏、无 get_node_stocks/set_node_stocks 扁平接口
  - [x] SubTask 3.2: 编写 `test_positive_edgefired.py`：EdgeFired 只含 eid+ts、EdgeExecutor 从 source_pool.get_dirty_codes() 取脏股票、无 event.changed_codes 字段引用
  - [x] 评审：评审工程师运行测试打分 100 分，≥98 通过

- [x] Task 4: 正测试 — 条件节点激活与公式筛选（复评 100 分通过，修复 core/execution_module.py:1219 edge_index bug）
  - [x] SubTask 4.1: 编写 `test_positive_condition_activation.py`：cond1 激活（KDJ 金叉，nperiod=3）、cond2 激活（MACD 金叉，nperiod=3）、入边按 _order 排序、公式=添加列、筛选=列比较、发布 FormulaEvaluated + StockFiltered 事件
  - [x] SubTask 4.2: 编写 `test_positive_set_operations.py`（合入 condition_activation 或独立）：cond3 交集（pool_A 全集 ∩ pool_B 全集）、差集、并集、表驱动 _SET_OP_FUNCS
  - [x] 评审：评审工程师运行测试打分 100 分，≥98 通过

- [x] Task 5: 正测试 — 交易事件链 + TTL（评审 100 分通过）
  - [x] SubTask 5.1: 编写 `test_positive_trade_chain.py`：pool_C 入池 → TransferExecuted → Signal(buy,100) → OrderPlaced → OrderFilled → PositionUpdated（持仓=100）
  - [x] SubTask 5.2: 编写 `test_positive_ttl.py`：TTL 到期 → TTLDue → Signal(sell_all) → OrderPlaced → OrderFilled → PositionUpdated（持仓=0）、TTL 一次性不注册下次
  - [x] 评审：评审工程师运行测试打分 100 分，≥98 通过

- [x] Task 6: 反测试 — 异常配置（复评 100 分通过，修复 FormulaEvaluated error 字段 gap）
  - [x] SubTask 6.1: 编写 `test_negative_empty_pool.py`：source 0 只股票，启动不抛异常、tick=0、passed 集合空、所有池空
  - [x] SubTask 6.2: 编写 `test_negative_invalid_config.py`：cond1.func 缺 accode、FormulaEngine 抛异常或返回空、EdgeExecutor 捕获不传播、FormulaEvaluated 携带 error
  - [x] 评审：评审工程师运行测试打分 100 分，≥98 通过

- [x] Task 7: 反测试 — 坏拓扑 + 重复入池（复评 100 分通过，修复 core/execution_module.py 自环边无限循环 bug）
  - [x] SubTask 7.1: 编写 `test_negative_bad_topology.py`：自环边（source=cond1,target=cond1）、孤点、CompiledSchedule 构建跳过或抛明确异常、不引发无限循环
  - [x] SubTask 7.2: 编写 `test_negative_duplicate_transfer.py`：同一股票多次经 cond1 进入 pool_A、pool_A 仅出现一次、TTL 不重复注册
  - [x] 评审：评审工程师运行测试打分 100 分，≥98 通过

- [x] Task 8: 反测试 — TTL 无持仓 + 公式异常 + 模块零引用（复评 100 分通过，修复函数级懒加载 + _on_ttl_expired Signal 发布 spec 偏差）
  - [x] SubTask 8.1: 编写 `test_negative_ttl_no_position.py`：TTL 到期但无持仓、Signal(sell_all) 发出、OrderPlaced 失败或为空、不抛异常
  - [x] SubTask 8.2: 编写 `test_negative_formula_error.py`：FormulaEngine.eval_series 返回空 dict、StockFiltered passed 集合空、不抛 KeyError
  - [x] SubTask 8.3: 编写 `test_negative_module_import.py`：检查 core/*.py 各模块 import 语句、仅允许白名单（event_bus/domain/schemas/标准库/第三方）、不允许 screening_module/formula_module 直接 import
  - [x] 评审：评审工程师复评打分 100 分，≥98 通过

- [x] Task 9: 合测试 — 仿真模式完整事件链端到端（评审 100 分通过，33/33 测试通过，11 类事件全部 ≥1）
  - [x] SubTask 9.1: 编写 `test_integration_sim_full_flow.py`：加载 sim_test_pool_100.json、启动仿真 300 秒虚拟时钟（延长以触发 KDJ/MACD 金叉）、断言 11 类事件计数 ≥1、断言事件链顺序
  - [x] SubTask 9.2: 编写 `test_integration_event_chain_order.py`：从 EventCollector 取事件序列、按时间戳排序、断言每只股票的事件链顺序正确
  - [x] 评审：评审工程师运行测试打分 100 分，≥98 通过（附带 Task 10 黄旗：需调查 pool_C(94)>pool_A(81) 根因）

- [x] Task 10: 合测试 — 池状态快照 + 量化评审报告（复评 100 分通过，修复 core/execution_module.py 交集路径退化 bug）
  - [x] SubTask 10.1: 编写 `test_integration_pool_snapshot.py`：仿真后取池状态快照、source=100 只 fz 股票、pool_A ⊆ source、pool_B ⊆ source、pool_C = pool_A ∩ pool_B、pool_C 每只股票持仓=100
  - [x] SubTask 10.2: 运行 `python -m eventtest.run_eventtest` 输出完整量化报告（测试总数/通过数/失败数/通过率/事件计数表/池状态快照表）
  - [x] 评审：评审工程师复评打分 100 分，≥98 通过（pool_C=81=pool_A∩pool_B，退出码 0）

- [x] Task 11: 更新 DESIGN.md 和 DESIGN0.md（评审 100 分通过）
  - [x] SubTask 11.1: DESIGN.md 新增章节"测试架构"——eventtest 目录结构、正反合测试方法论、量化评审标准、运行方式
  - [x] SubTask 11.2: DESIGN0.md 同步更新，确认测试架构与设计原则一致
  - [x] 评审：评审工程师审阅文档打分 100 分，≥98 结项

# Task Dependencies
- [Task 2] depends on [Task 1]
- [Task 3] depends on [Task 1]
- [Task 4] depends on [Task 1]
- [Task 5] depends on [Task 1]
- [Task 6] depends on [Task 1]
- [Task 7] depends on [Task 1]
- [Task 8] depends on [Task 1]
- [Task 9] depends on [Task 2, Task 3, Task 4, Task 5]
- [Task 10] depends on [Task 9]
- [Task 11] depends on [Task 10]
