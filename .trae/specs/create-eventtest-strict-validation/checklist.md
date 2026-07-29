# Checklist

## 评审打分规则（评审工程师使用）
- 评审工程师**必须运行** `python -m eventtest.run_eventtest`，以输出报告中的量化指标打分，不得仅靠代码审查。
- 每项检查点通过得满分，部分通过按比例扣分，未通过扣全部分。
- 任务总分 = 各检查点得分之和 / 检查点满分之和 × 100。
- **门槛：≥ 98 分方可进入下一任务；< 98 分打回架构工程师重做。**
- 评审工程师须给出：①总分 ②量化测试报告摘要（测试总数/通过数/通过率）③每项扣分理由 ④重做清单（若 < 98）。

## 量化测试通过率门槛
- [ ] 正测试通过率 ≥ 98%（每低 1% 扣 5 分）
- [ ] 反测试通过率 ≥ 98%（每低 1% 扣 5 分）
- [ ] 合测试通过率 ≥ 98%（每低 1% 扣 5 分）
- [ ] 事件链顺序错误直接扣 10 分
- [ ] 池状态断言错误直接扣 10 分

## Task 1: eventtest 目录骨架（评审 100 分通过）
- [x] `eventtest/` 目录存在于项目根目录
- [x] `eventtest/__init__.py` 存在
- [x] `eventtest/conftest.py` 提供 5 个 fixture：virtual_clock / fz_stocks / pool_engine / event_collector / pool_snapshot
- [x] `eventtest/run_eventtest.py` 运行后输出量化报告（测试总数/通过数/失败数/通过率/各测试耗时/事件计数表/池状态快照表/退出码）
- [x] `eventtest/README.md` 说明方法论与运行方式

## Task 2: 正测试 — MockDataSource + EventDriver（G1/G5）（评审 100 分通过）
- [x] `test_positive_mockdatasource.py` 验证 MD5 种子确定性
- [x] 同股票 tick 间隔固定（同种子下两次 tick 间隔相同）
- [x] 不同股票 tick 间隔不同
- [x] 所有股票代码以 `fz` 前缀
- [x] tick 数量 ≥ 备选池股票数 × 期望触发轮数
- [x] `test_positive_eventdriver.py` 验证内部仅 1 个 heapq（`_heap`）
- [x] fire_due 弹堆顶立即 heappush 下次（next = fire_time + interval）
- [x] TTL 一次性触发不注册下次
- [x] 不存在 `at_fn`/`fire_ttl_due`/`is_edge_due`/`TtlTracker`/`_make_edge_at_fn`/`_make_ttl_interval_at_fn`/`_make_ttl_endtime_at_fn` 残留

## Task 3: 正测试 — StatePoolView + EdgeFired（G3/G4）（评审 100 分通过）
- [x] `test_positive_statepoolview.py` 验证 get_pool 返回 StatePoolView 实例
- [x] get_dirty_codes() = state.changed_codes ∩ 本池股票
- [x] add_stocks() 入池并标脏
- [x] remove_stocks() 出池并标脏
- [x] 不存在 get_node_stocks/set_node_stocks/add_node_stocks/remove_node_stocks 扁平接口
- [x] `test_positive_edgefired.py` 验证 EdgeFired 只含 eid+ts
- [x] EdgeExecutor 从 source_pool.get_dirty_codes() 取脏股票
- [x] 不存在 event.changed_codes 字段引用
- [x] 不存在 _make_edge_action 中 changed_codes 计算逻辑

## Task 4: 正测试 — 条件节点激活与公式筛选（复评 100 分通过，修复 core/execution_module.py:1219 edge_index bug）
- [x] `test_positive_condition_activation.py` 验证 cond1 激活（KDJ 金叉，nperiod=3）
- [x] 验证 cond2 激活（MACD 金叉，nperiod=3）
- [x] 入边按 _order 排序
- [x] 公式 = 添加列（FormulaEngine.eval_series 写入 latest_tick[code][formula_ref]）
- [x] 筛选 = 列比较（_eval_op，只读列）
- [x] 发布 FormulaEvaluated + StockFiltered 事件
- [x] 公式计算与筛选严格分离
- [x] cond3 交集 = pool_A 全集 ∩ pool_B 全集（非脏股票交集）
- [x] 差集/并集按 _SET_OP_FUNCS 表驱动分派

## Task 5: 正测试 — 交易事件链 + TTL（评审 100 分通过）
- [x] `test_positive_trade_chain.py` 验证 pool_C 入池触发买入事件链
- [x] 事件链：TransferExecuted → Signal(buy,100) → OrderPlaced → OrderFilled → PositionUpdated
- [x] PositionUpdated 后持仓 = 100 股
- [x] `test_positive_ttl.py` 验证 TTL 到期触发卖出事件链
- [x] 事件链：TTLDue → Signal(sell_all) → OrderPlaced → OrderFilled → PositionUpdated
- [x] PositionUpdated 后持仓 = 0
- [x] TTL 一次性触发不注册下次（heap 长度不变）

## Task 6: 反测试 — 异常配置（复评 100 分通过，修复 FormulaEvaluated error 字段 gap）
- [x] `test_negative_empty_pool.py` 验证 source 0 只股票启动不抛异常
- [x] tick 数 = 0
- [x] passed 集合为空
- [x] pool_A/pool_B/pool_C 均为空
- [x] `test_negative_invalid_config.py` 验证 cond1.func 缺 accode
- [x] FormulaEngine 抛明确异常或返回空
- [x] EdgeExecutor 捕获不传播到 EventDriver
- [x] FormulaEvaluated 事件携带 error 字段

## Task 7: 反测试 — 坏拓扑 + 重复入池（复评 100 分通过，修复 core/execution_module.py 自环边无限循环 bug）
- [x] `test_negative_bad_topology.py` 验证自环边（source=cond1,target=cond1）
- [x] CompiledSchedule 构建跳过或抛明确异常
- [x] 不引发无限循环
- [x] 验证孤点（无入边无出边的节点）
- [x] `test_negative_duplicate_transfer.py` 验证同一股票多次经 cond1 进入 pool_A
- [x] pool_A 中该股票仅出现一次
- [x] TTL 不重复注册（heap 长度不增长）

## Task 8: 反测试 — TTL 无持仓 + 公式异常 + 模块零引用（复评 100 分通过，修复函数级懒加载 + _on_ttl_expired Signal 发布 spec 偏差）
- [x] `test_negative_ttl_no_position.py` 验证 TTL 到期但无持仓（11 测试）
- [x] Signal(sell_all) 发出（qty<=0 时仍发布 Signal(SELL, quantity=0)）
- [x] OrderPlaced 失败或为空（rejected OrderPlaced, status=rejected, reason=no_position）
- [x] 不抛异常
- [x] `test_negative_formula_error.py` 验证 FormulaEngine.eval_series 返回空 dict（10 测试）
- [x] StockFiltered passed 集合为空
- [x] 不抛 KeyError
- [x] `test_negative_module_import.py` 检查 core/*.py 各模块 import 语句（13 测试，含函数级懒加载 AST 分析）
- [x] 仅允许白名单：core.event_bus / core.domain / core.schemas / 标准库 / 第三方库
- [x] 不允许 core.screening_module / core.formula_module 直接 import
- [x] 生产代码修复：apply_ttl 函数级懒加载改为依赖注入；TimedEventSpec 下沉至 domain 模块级 import；_on_ttl_expired 移除 qty<=0 跳过；_on_signal 发布 rejected OrderPlaced

## Task 9: 合测试 — 仿真模式完整事件链端到端（评审 100 分通过，33/33 测试通过，11 类事件全部 ≥1）
- [x] `test_integration_sim_full_flow.py` 加载 sim_test_pool_100.json
- [x] 启动仿真 300 秒虚拟时钟（延长以触发 KDJ/MACD 金叉，120s 不足以计算 nperiod=3 金叉）
- [x] TickReceived ≥ 1
- [x] DataChanged ≥ 1
- [x] BarComposed ≥ 1
- [x] EdgeFired ≥ 1
- [x] FormulaEvaluated ≥ 1
- [x] StockFiltered ≥ 1
- [x] TransferExecuted ≥ 1
- [x] Signal ≥ 1
- [x] OrderPlaced ≥ 1
- [x] OrderFilled ≥ 1
- [x] PositionUpdated ≥ 1
- [x] `test_integration_event_chain_order.py` 验证事件链顺序：TickReceived→DataChanged→BarComposed→EdgeFired→FormulaEvaluated→StockFiltered→TransferExecuted→Signal→OrderPlaced→OrderFilled→PositionUpdated
- [x] conftest.py report_state fixture 改为 session-scoped（修复 ScopeMismatch，无回归）

## Task 10: 合测试 — 池状态快照 + 量化评审报告（复评 100 分通过，修复 core/execution_module.py 交集路径退化 bug）
- [x] `test_integration_pool_snapshot.py` 仿真后取池状态快照
- [x] source 池 = 100 只 fz 股票
- [x] pool_A ⊆ source
- [x] pool_B ⊆ source
- [x] pool_C = pool_A ∩ pool_B（严格断言通过，pool_C=81=pool_A(81)∩pool_B(100)）
- [x] pool_C 中每只股票持仓 = 100 股
- [x] `python -m eventtest.run_eventtest` 输出完整量化报告
- [x] 报告含测试总数/通过数/失败数/通过率
- [x] 报告含事件计数表（按 EventType 分组）
- [x] 报告含池状态快照表
- [x] 退出码 0 表示全部通过
- [x] 生产代码修复：_activate_condition 交集路径退化 bug（intersection 类型空源池记录 port_results[order]=[]，A∩∅=∅）

## Task 11: 文档更新（评审 100 分通过）
- [x] DESIGN.md 新增"测试架构"章节——eventtest 目录结构、正反合测试方法论、量化评审标准、运行方式
- [x] DESIGN0.md 同步更新，确认测试架构与设计原则一致
- [x] 文档语言简洁清晰，与既有章节风格一致

## 禁止兼容旧接口（贯穿全部任务）
- [ ] 不存在 EdgeFired.changed_codes 字段
- [ ] 不存在 SimTickSource 引用
- [ ] 不存在 execution_order（运行时拓扑排序）引用
- [ ] 不存在 get_node_stocks/set_node_stocks 引用
- [ ] 不存在兼容层或适配器
- [ ] 不存在 EventDriver.fire_due 线性扫描
- [ ] 不存在 at_fn / fire_ttl_due / TtlTracker 残留
- [ ] 每发现 1 处扣 5 分

## 旧 tests/ 目录冻结
- [ ] 旧 tests/ 目录不删除（冻结保留）
- [ ] 新评审一律以 eventtest/ 输出为准
- [ ] 旧 tests/ 不作为评审依据
