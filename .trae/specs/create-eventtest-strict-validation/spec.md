# eventtest 严格正反合测试规范 Spec

## Why
现有 `tests/` 目录下的测试已无法验证条件节点拓扑重构后的真实行为：旧测试针对已删除接口（`get_node_stocks`/`SimTickSource`/`execution_order`/`EdgeFired.changed_codes`）编写，且不产出量化测试结果。若仅靠代码审查评审，无法发现 G1-G6 架构落地后的运行时 bug（如 Task 16 修复的 5 个 bug 均未被旧测试捕获）。本规格要求新建独立 `eventtest/` 目录，以**正测试 / 反测试 / 合测试**三层方法论编写严格测试，**以量化真实测试结果**作为评审工程师打分的唯一依据。

## What Changes
- **新建** `eventtest/` 目录于项目根目录，与 `tests/` 并列；旧 `tests/` 目录保持冻结，不再作为评审依据。
- **新增** 正测试集（`test_positive_*.py`）：验证正常路径——MockDataSource tick 生成、EventDriver 单 heapq 优先队列中断驱动、StatePoolView 视图脏股票、EdgeFired 无 changed_codes、条件节点激活与公式筛选、集合运算交集/差集/并集、交易事件链（TransferExecuted→Signal→OrderPlaced→OrderFilled→PositionUpdated）、TTL 一次性触发。
- **新增** 反测试集（`test_negative_*.py`）：验证异常与边界——空备选池、无效条件节点配置、坏边拓扑（自环/孤点）、重复入池、TTL 到期无持仓、公式计算异常返回空、空 dirty_codes 兜底、跨模块非法 import。
- **新增** 合测试集（`test_integration_*.py`）：端到端集成——仿真模式全流程（source→cond1→pool_A、source→cond2→pool_B、pool_A+pool_B→cond3→pool_C→买入→TTL→卖出），断言事件计数、池状态快照、事件链顺序。
- **新增** `eventtest/conftest.py`：统一 fixture（虚拟时钟 34500.0、`fz` 股票代码生成器、`PoolEngine` 装配器、事件收集器 `EventCollector` 订阅 EventBus 所有事件、池状态快照工具）。
- **新增** `eventtest/run_eventtest.py`：测试运行器，输出量化报告（事件计数表、池状态表、断言通过率、耗时）。
- **新增** `eventtest/README.md`：方法论说明 + 运行方式。
- **修改** 评审流程：评审工程师**必须运行** `python -m eventtest.run_eventtest` 并以输出报告中的量化指标打分，不再仅靠代码审查。
- **修改** 修复流程：架构工程师在修复 bug 后**必须**先在本地运行测试集确认通过率 ≥ 98% 再提交评审。
- **修改** `docs/DESIGN.md` / `docs/DESIGN0.md`：补充测试架构章节、正反合测试方法论、量化评审标准。

## Impact
- Affected specs:
  - `restructure-stockpool-cond-node-flow`（已结项，本规格为其建立量化回归基线）
  - `specify-stockpool-event-flow`（同上）
- Affected code:
  - 新增 `eventtest/` 目录全部文件
  - `docs/DESIGN.md`（新增测试架构章节）
  - `docs/DESIGN0.md`（同步更新）
- 不修改 `core/` 任何源文件（除非测试发现真实 bug 并经架构工程师修复，修复须遵循零引用约束）。

## ADDED Requirements

### Requirement: eventtest 目录结构
系统 SHALL 在项目根目录下新建 `eventtest/` 目录，结构与 `tests/` 并列，包含：
- `__init__.py`（标记为包）
- `conftest.py`（共享 fixture）
- `test_positive_mockdatasource.py`
- `test_positive_eventdriver.py`
- `test_positive_statepoolview.py`
- `test_positive_edgefired.py`
- `test_positive_condition_activation.py`
- `test_positive_trade_chain.py`
- `test_positive_ttl.py`
- `test_negative_empty_pool.py`
- `test_negative_invalid_config.py`
- `test_negative_bad_topology.py`
- `test_negative_duplicate_transfer.py`
- `test_negative_ttl_no_position.py`
- `test_negative_formula_error.py`
- `test_integration_sim_full_flow.py`
- `test_integration_event_chain_order.py`
- `test_integration_pool_snapshot.py`
- `run_eventtest.py`
- `README.md`

#### Scenario: 目录结构创建完成
- **WHEN** 架构工程师完成 Task 1
- **THEN** `eventtest/` 目录下存在上述全部 18 个文件
- **AND** `conftest.py` 提供 `virtual_clock`/`fz_stocks`/`pool_engine`/`event_collector`/`pool_snapshot` 五个 fixture

### Requirement: 正测试（正常路径验证）
系统 SHALL 提供正测试集，覆盖以下正常路径，每条断言基于实际运行结果：

#### Scenario: MockDataSource 生成确定性 tick
- **WHEN** 启用 MockDataSource 并配置 MD5 种子 + tick 间隔 1-9s
- **THEN** 同股票 tick 间隔固定（同种子下两次 tick 间隔相同）
- **AND** 不同股票 tick 间隔不同
- **AND** 所有股票代码以 `fz` 前缀
- **AND** tick 数量 ≥ 备选池股票数 × 期望触发轮数

#### Scenario: EventDriver 单 heapq 优先队列中断驱动（G1）
- **WHEN** 注册 edge/ttl/tick 三类定时器 spec 到 EventDriver
- **THEN** 内部仅维护 1 个 heapq（`_heap`），无 `_specs` 列表
- **AND** `fire_due(now)` 按最近到期时间弹出堆顶，发布事件后立即 `heappush` 下次（next = fire_time + interval）
- **AND** TTL spec 一次性触发不注册下次
- **AND** 不存在 `at_fn`/`fire_ttl_due`/`is_edge_due`/`TtlTracker` 残留

#### Scenario: StatePoolView 视图脏股票（G4）
- **WHEN** 调用 `state.get_pool(nid)`
- **THEN** 返回 `StatePoolView` 实例
- **AND** `get_dirty_codes()` 返回 `state.changed_codes ∩ 本池股票`
- **AND** `add_stocks()` 入池并标脏
- **AND** `remove_stocks()` 出池并标脏
- **AND** 不存在 `get_node_stocks`/`set_node_stocks` 扁平接口

#### Scenario: EdgeFired 不携带 changed_codes（G3）
- **WHEN** EdgeFired 事件发布
- **THEN** 事件 payload 只含 `eid` 和 `ts`
- **AND** EdgeExecutor 从 `source_pool.get_dirty_codes()` 取脏股票
- **AND** 不存在 `event.changed_codes` 字段引用

#### Scenario: 条件节点激活与公式筛选
- **WHEN** EdgeFired 触发条件节点 cond1（KDJ 金叉）
- **THEN** 收集 cond1 所有入边按 `_order` 排序
- **AND** 从源池 StatePoolView 取脏股票，按 `func` 调用 FormulaEngine.eval_series 添加列
- **AND** 按 `filter_spec` 筛选（列比较），passed 集合写入目标池
- **AND** 发布 FormulaEvaluated + StockFiltered 事件
- **AND** 公式计算与筛选严格分离

#### Scenario: 集合运算交集/差集/并集
- **WHEN** cond3 多入边触发，`filter_spec.evaluator_type=intersection`
- **THEN** 取 pool_A 当前股票全集 ∩ pool_B 当前股票全集
- **AND** 差集/并集按 `_SET_OP_FUNCS` 表驱动分派
- **AND** 输出到 pool_C 并触发买入事件链

#### Scenario: 交易事件链
- **WHEN** 股票入 pool_C
- **THEN** 事件序列：TransferExecuted → Signal(buy,100) → OrderPlaced → OrderFilled → PositionUpdated
- **AND** PositionUpdated 后持仓 = 100 股

#### Scenario: TTL 一次性触发
- **WHEN** 股票入 pool_C 注册 TTL
- **THEN** TTL 到期触发 TTLDue
- **AND** 触发卖出事件链：TTLDue → Signal(sell_all) → OrderPlaced → OrderFilled → PositionUpdated
- **AND** TTL 不注册下次（heap 长度不变）

### Requirement: 反测试（异常与边界验证）
系统 SHALL 提供反测试集，验证异常路径与边界条件，预期系统优雅降级而非崩溃：

#### Scenario: 空备选池
- **WHEN** source 池配置 0 只股票
- **THEN** 启动不抛异常
- **AND** tick 数 = 0
- **AND** 条件节点激活后 passed 集合为空
- **AND** pool_A/pool_B/pool_C 均为空

#### Scenario: 无效条件节点配置
- **WHEN** cond1.func 缺失 accode 字段
- **THEN** FormulaEngine 抛出明确异常或返回空结果
- **AND** 异常被 EdgeExecutor 捕获，不传播到 EventDriver
- **AND** 发布 FormulaEvaluated 事件携带 error 字段

#### Scenario: 坏边拓扑（自环）
- **WHEN** 边 source=cond1, target=cond1
- **THEN** CompiledSchedule 构建抛出明确异常或跳过该边
- **AND** 不引发无限循环

#### Scenario: 重复入池
- **WHEN** 同一股票通过 cond1 多次进入 pool_A
- **THEN** pool_A 中该股票仅出现一次
- **AND** TTL 不重复注册（heap 长度不增长）

#### Scenario: TTL 到期无持仓
- **WHEN** TTL 到期但该股票无持仓（已被人工卖出）
- **THEN** Signal(sell_all) 发出但 OrderPlaced 失败或为空
- **AND** 不抛异常

#### Scenario: 公式计算异常返回空
- **WHEN** FormulaEngine.eval_series 返回空 dict
- **THEN** StockFiltered passed 集合为空
- **AND** 不抛 KeyError

#### Scenario: 跨模块非法 import
- **WHEN** 检查 `core/execution_module.py` 的 import 语句
- **THEN** 仅允许 `core.event_bus`/`core.domain`/`core.schemas`/标准库/第三方库
- **AND** 不允许 `core.screening_module`/`core.formula_module` 直接 import

### Requirement: 合测试（端到端集成验证）
系统 SHALL 提供合测试集，验证仿真模式全流程：

#### Scenario: 仿真模式完整事件链
- **WHEN** 加载 `config/pools/sim_test_pool_100.json` 并启动仿真 120 秒虚拟时钟
- **THEN** TickReceived ≥ 1
- **AND** DataChanged ≥ 1
- **AND** BarComposed ≥ 1
- **AND** EdgeFired ≥ 1
- **AND** FormulaEvaluated ≥ 1
- **AND** StockFiltered ≥ 1
- **AND** TransferExecuted ≥ 1
- **AND** Signal ≥ 1
- **AND** OrderPlaced ≥ 1
- **AND** OrderFilled ≥ 1
- **AND** PositionUpdated ≥ 1
- **AND** 事件链顺序：TickReceived → DataChanged → BarComposed → EdgeFired → FormulaEvaluated → StockFiltered → TransferExecuted → Signal → OrderPlaced → OrderFilled → PositionUpdated

#### Scenario: 池状态快照
- **WHEN** 仿真运行 120 秒后取池状态快照
- **THEN** source 池 = 100 只 fz 股票
- **AND** pool_A ⊆ source
- **AND** pool_B ⊆ source
- **AND** pool_C = pool_A ∩ pool_B
- **AND** pool_C 中每只股票持仓 = 100 股

### Requirement: 量化测试运行器
系统 SHALL 提供 `eventtest/run_eventtest.py`，运行后输出量化报告：

#### Scenario: 运行测试
- **WHEN** 执行 `python -m eventtest.run_eventtest`
- **THEN** 运行全部正/反/合测试
- **AND** 输出报告包含：测试总数 / 通过数 / 失败数 / 通过率 / 各测试耗时
- **AND** 输出事件计数表（按 EventType 分组）
- **AND** 输出池状态快照表
- **AND** 退出码 0 表示全部通过，非 0 表示有失败

## MODIFIED Requirements

### Requirement: 评审工程师打分标准
评审工程师 SHALL 必须运行 `python -m eventtest.run_eventtest`，以输出报告中的量化指标打分：
- 正测试通过率 ≥ 98% 得满分，每低 1% 扣 5 分
- 反测试通过率 ≥ 98% 得满分，每低 1% 扣 5 分
- 合测试通过率 ≥ 98% 得满分，每低 1% 扣 5 分
- 事件链顺序错误直接扣 10 分
- 池状态断言错误直接扣 10 分
- 禁止兼容旧接口检查（搜索 `get_node_stocks`/`SimTickSource`/`execution_order`/`changed_codes` 残留）每发现 1 处扣 5 分
- 门槛仍为 ≥ 98 分

## REMOVED Requirements

### Requirement: 旧 tests/ 目录作为评审依据
**Reason**: 旧测试针对已删除接口编写，不验证条件节点拓扑重构后的真实行为，无法作为评审依据。
**Migration**: 旧 `tests/` 目录冻结保留（不删除），新评审一律以 `eventtest/` 输出为准。
