# 条件节点拓扑重构 Spec

## Why

现有股票池实例把转移条件、计算参数、K线配置、筛选条件全部塞在边上（`sim_test_pool_100.json` 的 `edge.params`），可视化上看不到条件节点，用户无法在界面上直观配置和读取这些参数。同时原 `specify-stockpool-event-flow` 规划的 G1-G6 核心重构（单定时器中断、事件无序、引擎只发事件、EdgeFired 去 changed\_codes、StatePoolView 视图、MockDataSource、删除 execution\_order）尚未落地。

本规格将拓扑重构为**显式条件节点流**：`source→cond1→pool_A`、`source→cond2→pool_B`、`pool_A→cond3`、`pool_B→cond3`、`cond3→pool_C`，让计算参数/K线配置/筛选条件从条件节点读取；同时一次性落地 G1-G6 核心原则，禁止兼容旧接口。

## What Changes

* **新增条件节点拓扑**：实例配置引入 cond1/cond2/cond3 三个 `ConditionNode`，承载 func/indi/indiparam/filter\_spec（计算参数/K线配置/筛选条件/集合运算）。

* **BREAKING**：转移条件、计算参数、K线配置、筛选条件从边迁移到条件节点；边只承载触发频率（入边）+ 顺序号（多入边交集/差集）。

* **BREAKING**：边类型改为 `source/状态池→condition` 用 conditional 边（含 time\_gate\_interval），`condition→状态池` 用 unconditional 边（跟随入边）。

* **BREAKING**：单一定时器中断驱动（G1）——EventDriver 改 heapq 优先队列，触发即注册下次，与模块计算无关。

* **BREAKING**：引擎只发事件不执行计算（G2）——action 只发 EdgeFired/TTLDue/TickDue，业务由订阅模块完成。

* **BREAKING**：EdgeFired 去 changed\_codes（G3）——只含 eid+ts，脏股票从源池 StatePoolView 取。

* **BREAKING**：StatePoolView 视图对象（G4）——取代 get\_node\_stocks/set\_node\_stocks 扁平接口。

* **BREAKING**：SimTickSource 改 MockDataSource（G5）——tick 定时器注册到统一优先队列。

* **BREAKING**：删除 execution\_order 运行时拓扑排序（G6）——保留边顺序号用于交集/差集运算次序。

* **条件节点激活模型**：入边到时→条件节点激活→读取所有入边源池脏股票→按入边顺序号依次计算/筛选→集合运算→出边输出到目标池。

* **可视化**：股票池设计界面必须显示条件节点矩形，用户可点击配置计算参数/K线/筛选条件。

## Impact

* 实例配置：`config/pools/sim_test_pool_100.json`（重构节点/边拓扑）

* 核心代码：`core/execution_module.py`（EventDriver/EdgeExecutor/条件节点激活）、`core/event_bus.py`（EdgeFired 去 changed\_codes）、`core/domain.py`（MockDataSource 重命名、ConditionNode 激活接口）、`core/runtime_mode_module.py`（StatePoolView）、`core/engine.py`（删除 execution\_order 遍历）、`core/tick_bar_module.py`（BarComposer 仅发事件）

* 架构配置：`config/architecture/edge_strategies.json`（条件节点入/出边策略）

* 可视化：股票池设计界面（条件节点矩形显示与配置面板）

***

## ADDED Requirements

### Requirement: 条件节点拓扑

系统 SHALL 在实例股票池中显式建模条件节点，作为计算参数/K线配置/筛选条件/集合运算的载体。

#### Scenario: source→cond1→pool\_A 链路

* **WHEN** e1(source→cond1) 边定时器到时（60s）

* **THEN** cond1 激活，读取 source 池脏股票，按 cond1.func/indi/indiparam 计算 KDJ 金叉，按 cond1.filter\_spec 筛选，passed 通过 cond1→pool\_A 出边入 pool\_A

#### Scenario: 多入边交集 cond3

* **WHEN** ec3a(pool\_A→cond3) 或 ec3b(pool\_B→cond3) 边定时器到时（5s）

* **THEN** cond3 激活，读取 pool\_A 和 pool\_B 当前股票，按入边顺序号（ec3a=1, ec3b=2）做交集运算，结果通过 cond3→pool\_C 出边入 pool\_C

### Requirement: 条件节点承载配置

条件节点 SHALL 承载以下配置，边不再承载：

* `func`：公式函数（KDJ/MACD 等）

* `indi`：指标名

* `indiparam`：指标参数列表

* `filter_spec`：筛选规格（金叉/死叉/阈值/排序/集合运算）

* K线周期配置（在 indiparam 或 func 中）

### Requirement: 可视化条件节点

股票池设计界面 SHALL 显示条件节点为矩形，用户可点击打开配置面板，配置计算参数/K线/筛选条件。

### Requirement: 单一定时器中断驱动（G1）

系统只有 1 个 heapq 优先队列，所有边触发、TTL、tick 间隔注册到同一队列。定时器到时→发布事件+立即注册下次→结束，与模块计算无关。禁止轮询、asyncio.sleep、线性扫描。

### Requirement: 引擎只发事件不执行计算（G2）

定时器到时→引擎发布事件+注册下次→结束。业务逻辑由订阅模块完成。引擎不调用 edge\_executor.run()。

### Requirement: EdgeFired 不携带 changed\_codes（G3）

EdgeFired 只携带 eid 和 ts。EdgeExecutor 从 source\_pool.get\_dirty\_codes() 取脏股票。

### Requirement: StatePoolView 视图对象（G4）

StatePoolView 提供 get\_stocks()/get\_stock\_codes()/get\_dirty\_codes()/add\_stocks()/remove\_stocks()。状态池是视图，脏股票在 tick 表变更列（state.changed\_codes）。删除 get\_node\_stocks/set\_node\_stocks 扁平接口。

### Requirement: MockDataSource（G5）

SimTickSource 重命名为 MockDataSource，tick 定时器注册到 EventDriver 统一优先队列。仿真与实盘除 tick 请求方式外共用同一套代码。

### Requirement: 删除 execution\_order 运行时拓扑排序（G6）

删除 CompiledSchedule.execution\_order 运行时拓扑排序。保留边顺序号（edge\_order）用于交集/差集运算次序。运行时事件没有顺序，哪个定时器先到时先触发。

### Requirement: 公式计算与筛选分离

公式=添加列，筛选=列操作。HQChartPy2，Python 3.13，无 cross。增量评估：仅对 dirty\_codes 重新评估。合并规则：`passed = (cached_passed - dirty_codes) | newly_passed`。

### Requirement: 实例股票池配置（新拓扑）

**节点：**

| 节点ID     | 类型             | 名称       | 关键参数                                     |
| -------- | -------------- | -------- | ---------------------------------------- |
| `source` | market\_source | 备选池      | 100 只股票，仿真模式代码替换为 fz 前缀                  |
| `cond1`  | condition      | KDJ金叉条件  | func=KDJ, K线=5m, filter=金叉(noperate=3)   |
| `cond2`  | condition      | MACD金叉条件 | func=MACD, K线=1m, filter=金叉(noperate=3)  |
| `pool_A` | statepool      | A池       | TTL=100min(6000s)                        |
| `pool_B` | statepool      | B池       | TTL=200min(12000s)                       |
| `cond3`  | condition      | 交集条件     | filter=交集, 入边顺序1+2                       |
| `pool_C` | statepool      | C池       | TTL=20min(1200s); 入池=市价买入100股; 出池=卖出所有持仓 |

**边（触发频率在入边，顺序号在多入边上）：**

| 边ID      | 源→目标          | 类型            | 触发频率 | 顺序号 |
| -------- | ------------- | ------------- | ---- | --- |
| `ec1`    | source→cond1  | conditional   | 60s  | -   |
| `ec1out` | cond1→pool\_A | unconditional | 跟随   | -   |
| `ec2`    | source→cond2  | conditional   | 10s  | -   |
| `ec2out` | cond2→pool\_B | unconditional | 跟随   | -   |
| `ec3a`   | pool\_A→cond3 | conditional   | 5s   | 1   |
| `ec3b`   | pool\_B→cond3 | conditional   | 5s   | 2   |
| `ec3out` | cond3→pool\_C | unconditional | 跟随   | -   |

仿真约束：仿真只发代码请求，tick 由 MockDataSource 产生。fz 代码替代。1-9s 固定间隔。

### Requirement: 完整事件流程

#### 一、初始化

```
1. ConfigLoaded → PoolLoaded(100只fz股票入备选池) → ModeChanged(simulation)
2. MockDataSource 为每只股票分配固定间隔，注册 tick 定时器到优先队列
3. 注册入边触发定时器到优先队列：ec1(60s), ec2(10s), ec3a(5s), ec3b(5s)
4. 所有定时器共用同一优先队列
```

#### 二、条件节点激活流程（入边到时）

```
EdgeExecutor 收到 EdgeFired(eid):
  cond_node = get_condition_node(eid 的目标节点)
  入边列表 = cond_node 的所有入边（按 _order 排序）
  port_results = {}
  for in_edge in 入边列表:
    source_pool = state.get_pool(in_edge.source)
    dirty_codes = source_pool.get_dirty_codes()
    source_codes = source_pool.get_stocks()
    if dirty_codes 为空且 first_run:
      dirty_codes = source_codes  # 兜底全量
    # 公式计算（为 tick 表添加列）——从 cond_node 读取 func/indi/indiparam
    FormulaEngine.eval(dirty_codes, cond_node.func, cond_node.indiparam)
    # 筛选（列比较/排序/集合操作）——从 cond_node 读取 filter_spec
    passed = 筛选逻辑(dirty_codes, formula_results, cond_node.filter_spec)
    port_results[in_edge._order] = passed

  # 集合运算（按顺序号合并 port_results）
  if len(port_results) == 1:
    final_passed = port_results[唯一]
  else:
    final_passed = port_results[order1] ∩ port_results[order2] ∩ ...  # 交集（filter_spec 指定）

  # 出边输出
  out_edge = cond_node 的出边
  target_pool = state.get_pool(out_edge.target)
  target_pool.add_stocks(final_passed)  # 入池+标脏
  → TransferExecuted(out_edge.id, cond_node.id, out_edge.target, final_passed)

  # 注册 TTL 定时器（一次性）
  for code in final_passed:
    heapq.heappush(heap, (now+ttl_sec, TTLSpec(nid=out_edge.target, code=code)))

  # 交易回调（C池）
  if out_edge.target == pool_C and final_passed:
    → Signal(buy, 100, market) → OrderPlaced → OrderFilled → PositionUpdated
```

#### 三、时间线实例

```
t=0s   初始化。注册所有定时器到优先队列。

t=2s   fz000002 tick 定时器到时
       引擎：发布 TickDue(fz000002, 2s) + 注册下次
       TickBarModule：获取tick→changed_codes+={fz000002}

t=10s  ec2(source→cond2) 边定时器到时
       引擎：发布 EdgeFired(ec2, 10s) + 注册下次 at=20s
       EdgeExecutor：cond2 激活
         source_pool.get_dirty_codes()={fz000002,...}
         按 cond2.func=MACD 计算，筛选金叉
         → pool_B.add_stocks([fz000002])

t=60s  ec1(source→cond1) 边定时器到时
       引擎：发布 EdgeFired(ec1, 60s) + 注册下次 at=120s
       EdgeExecutor：cond1 激活
         source_pool.get_dirty_codes()={本周期tick股票}
         按 cond1.func=KDJ 计算，筛选金叉
         → pool_A.add_stocks([fz000001,fz000007])

t=65s  ec3a(pool_A→cond3) 边定时器到时
       引擎：发布 EdgeFired(ec3a, 65s) + 注册下次 at=70s
       EdgeExecutor：cond3 激活
         port[1] = pool_A.get_dirty_codes() → {fz000001,fz000007}
         port[2] = pool_B.get_dirty_codes() → {fz000002}
         交集 = {} （空）
         不输出

t=70s  ec3b(pool_B→cond3) 边定时器到时
       EdgeExecutor：cond3 激活
         port[1] = pool_A 股票 {fz000001,fz000007}
         port[2] = pool_B 股票 {fz000002}
         交集 = {} （仍空）

  (假设后续 fz000001 同时进入 pool_A 和 pool_B)
t=200s ec3a/ec3b 到时
       cond3 激活：port[1]∩port[2] = {fz000001}
         → pool_C.add_stocks([fz000001])
         → Signal(buy,100) → OrderPlaced → OrderFilled → PositionUpdated

t=1400s TTLDue(pool_C, fz000001)
         → Signal(sell_all) → OrderPlaced → OrderFilled → PositionUpdated
         → pool_C.remove_stocks([fz000001])
         【一次性，不注册下次】
```

## REMOVED Requirements

| 已删除需求                               | 删除原因                     | 迁移方式                                               |
| ----------------------------------- | ------------------------ | -------------------------------------------------- |
| 转移条件配置在边上                           | 条件节点承载配置                 | 迁移到 ConditionNode.func/indi/indiparam/filter\_spec |
| EdgeFired 携带 changed\_codes         | EdgeFired 只发信号           | 删除字段                                               |
| 每个状态池独立维护脏股票                        | 脏股票在 tick 表变更列           | StatePoolView\.get\_dirty\_codes()                 |
| SimTickSource                       | tick 由 MockDataSource 产生 | 重命名+重构                                             |
| 三段式核心循环                             | 轮询语义                     | 中断驱动                                               |
| execution\_order（运行时拓扑排序）           | 事件没有顺序                   | 删除运行时排序，保留边顺序号                                     |
| 兼容旧接口                               | 禁止兼容                     | 直接删除                                               |
| get\_node\_stocks/set\_node\_stocks | StatePoolView 取代         | state.get\_pool(nid).get\_stocks()                 |

