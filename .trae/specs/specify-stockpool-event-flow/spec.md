# 股票池事件流程新执行规范 Spec

## Why

股票池程序需完整、清晰地落地验证"100只股票备选池→A池→B池→C池交易"实例，并以量化的真实测试结果进行评审。旧测试泛无意义，需创建 eventtest 目录重新建立严格正反合测试。仿真模式除 tick 请求方式外必须与实盘共用同一套代码。

> **本规范为新执行规范**：采用双工程师协作（架构工程师 + 评审工程师）、98 分门槛、逐项执行、量化验证。

## What Changes

- **只有一个定时器**：所有时间相关事件注册到同一个 heapq 优先队列，按最近到期时间中断触发。
- **定时器触发时立即注册下一次**：到时→发布事件+注册下次→结束。与模块计算无关。
- **引擎只发事件不执行计算**：引擎不参与任何业务逻辑。
- **事件没有顺序**：所有边、所有状态池，触发即开干，没有先后。不存在 execution_order。
- **EdgeFired 不携带 changed_codes**：只发事件信号(eid+ts)。
- **从状态池对象接口取脏股票**：EdgeExecutor 通过 `source_pool.get_dirty_codes()` 获取。
- **脏股票在 tick 表变更列**：`state.changed_codes` 是唯一真相源，状态池是视图。
- **状态池变更 = tick 变化 + 入池 + 出池**。
- **公式计算与筛选严格分离**：公式=添加列，筛选=列操作。
- **股票池与 K 线解耦**：BarComposer 仅发布事件。
- **仿真只发代码请求，tick 由 MockDataSource 产生**。
- **禁止兼容旧接口，必须唯一正确**。

## Impact

- `core/engine.py`、`core/execution_module.py`、`core/domain.py`、`core/tick_bar_module.py`、`core/event_bus.py`、`core/runtime_mode_module.py`
- `config/pools/sim_test_pool_100.json`

---

## Requirements

### R1: 单一定时器中断驱动

系统只有 **1 个定时器**——一个 heapq 优先队列。

- 优先队列：`heapq` 管理 `(next_fire_time, spec)` 元组。
- 中断触发：取堆顶 `heap[0][0] <= now` 的所有到时项，弹出执行。
- **触发时立即注册下一次**：定时器到时→引擎同时做①发布对应事件 ②计算 `next = last_fire + interval`，若 `next <= end_time` 则重新入队。**与模块计算完全无关**。
- 边触发：每条边注册到同一队列（kind="edge"），触发时发布 EdgeFired + 注册下次。
- TTL 到期：股票入池时注册到同一队列（kind="ttl"），触发时发布 TTLDue。一次性，不注册下次。
- tick 间隔：由 MockDataSource 注册到同一队列（kind="tick"），触发时发布 TickDue + 注册下次。
- **禁止**：遍历轮询、`asyncio.sleep`、线性扫描列表。

### R2: 事件没有顺序（运行时）

**运行时**，所有边、所有状态池，触发即开干，没有先后。

- 不存在 execution_order（运行时拓扑排序）：哪个定时器先到时先触发。
- 事件引擎职责简洁：到时→发布事件+注册下次→结束，不编排执行顺序。

**设计结构**上，边有顺序号，决定交集/差集的计算逻辑：

- 连接同一目标节点的多条边，按边的顺序号决定交集/差集运算的次序。
- 边的顺序号是配置时一次性确定的，影响初始化和筛选逻辑，不影响运行时事件触发顺序。
- 例：e3(顺序1)和e4(顺序2)都指向pool_C，交集运算按顺序号依次执行——先e3结果，再与e4结果取交集。

**两者不矛盾**：边顺序号是设计结构（一次性配置），事件无序是运行时循环（定时器独立触发）。

### R3: 引擎只发事件不执行计算

定时器到时→引擎发布事件+注册下次定时器→结束。引擎职责到此为止。

- 业务逻辑由订阅该事件的模块自己完成。
- 引擎不调用 edge_executor.run()、不调筛选、不调转移。

### R4: EdgeFired 不携带 changed_codes

EdgeFired 只携带 `eid` 和 `ts`，是定时器信号事件。EdgeExecutor 收到后从状态池对象接口取脏股票。

### R5: 脏股票在 tick 表变更列，通过状态池对象接口访问

- `state.changed_codes` 是全局脏股票集合（唯一真相源）。
- 状态池是视图，不独立维护脏股票集合。
- 状态池对象接口：
  - `get_dirty_codes()` → `state.changed_codes ∩ 本池股票`
  - `get_stocks()` → 本池当前股票列表
  - `add_stocks(codes)` → 入池 + 标脏
  - `remove_stocks(codes)` → 出池 + 标脏
- 状态池变更 = tick 变化 + 入池 + 出池。

### R6: 实例股票池配置

**节点：**

| 节点ID | 类型 | 名称 | 关键参数 |
|--------|------|------|---------|
| `source` | market_source | 备选池 | 100 只股票，仿真模式代码替换为 `fz` 前缀 |
| `pool_A` | statepool | A池 | TTL=100 分钟 |
| `pool_B` | statepool | B池 | TTL=200 分钟 |
| `pool_C` | statepool | C池 | TTL=20 分钟；入池=市价买入100股；出池=卖出所有持仓 |

**边（转移条件配置在连接上）：**

| 边ID | 源→目标 | 触发频率 | 转移条件 |
|------|---------|---------|---------|
| `e1` | source→pool_A | 60s | 5m KDJ 金叉 |
| `e2` | source→pool_B | 10s | 1m MACD 金叉 |
| `e3` | pool_A→pool_C | 5s | 交集(顺序1) |
| `e4` | pool_B→pool_C | 5s | 交集(顺序2) |

仿真约束：仿真只发代码请求，tick 由 MockDataSource 产生。fz 代码替代。1-9s 固定间隔。

### R7: 完整事件流程

#### 一、初始化

```
1. ConfigLoaded → PoolLoaded(100只fz股票入备选池) → ModeChanged(simulation)
2. MockDataSource 为每只股票分配固定间隔，注册 tick 定时器到优先队列
3. 注册边触发定时器到优先队列：e1(60s), e2(10s), e3(5s), e4(5s)
4. 所有定时器共用同一优先队列
```

#### 二、单定时器中断驱动

```
定时器中断触发（now >= heap[0][0]）
  → 弹出所有到时项
  → 对每个到时项：
     ① 引擎发布对应事件
     ② 引擎立即注册下次定时器（若 interval 存在且 next <= end_time）
  → 引擎职责结束

  kind="tick" → 发布 TickDue(code, ts) → TickBarModule 订阅
  kind="edge" → 发布 EdgeFired(eid, ts) → EdgeExecutor 订阅
  kind="ttl"  → 发布 TTLDue(nid, code, ts) → TradeModule 订阅
```

#### 三、边触发流程（EdgeExecutor 收到 EdgeFired 后）

```
EdgeExecutor._on_edge_fired(event):
  source_pool = get_pool(event.eid 的源节点)
  dirty_codes = source_pool.get_dirty_codes()
  source_codes = source_pool.get_stocks()

  if dirty_codes 为空且 first_run[eid]:
    dirty_codes = source_codes  # 兜底全量

  # 公式计算（为 tick 表添加列）
  FormulaEngine.eval(dirty_codes, formula_spec)
  → FormulaEvaluated(eid, results)

  # 筛选（列比较/排序/集合操作）
  # 若目标节点有多条入边，按边顺序号依次做交集/差集
  passed = 筛选逻辑(dirty_codes, formula_results, edge_params)
  → StockFiltered(eid, passed, rejected)

  # 转移
  target_pool.add_stocks(passed)  # 入池+标脏
  → TransferExecuted(eid, src, tgt, passed)

  # 注册 TTL 定时器
  for code in passed:
    heapq.heappush(heap, (now+ttl_sec, TTLSpec(nid=tgt, code=code)))

  # 交易回调
  if tgt == pool_C and passed:
    → Signal(buy, 100, market) → OrderPlaced → OrderFilled → PositionUpdated
```

#### 四、时间线实例

```
t=0s   初始化。注册所有定时器到优先队列。

t=2s   fz000002 tick 定时器到时
       引擎：发布 TickDue(fz000002, 2s) + 注册下次 at=4s
       TickBarModule：获取tick→changed_codes+={fz000002}
       → TickReceived → DataChanged → BarComposer合成

t=5s   e3/e4 边定时器到时 + fz000005 tick定时器到时
       引擎：发布 TickDue(fz000005,5s) + 注册下次 at=10s
             发布 EdgeFired(e3,5s) + 注册下次 at=10s
             发布 EdgeFired(e4,5s) + 注册下次 at=10s
       【没有顺序，三个事件独立触发】

t=10s  e2 + e3/e4 + 多个 tick 到时
       引擎：发布所有到时事件 + 注册所有下次定时器
       EdgeFired(e2): 备选池.get_dirty_codes()={fz000002,...}
         → MACD金叉 → fz000002 passed
         → pool_B.add_stocks([fz000002])

t=60s  e1 首次到时
       EdgeFired(e1): 备选池.get_dirty_codes()={本周期tick股票}
         → KDJ金叉 → fz000001,fz000007 passed
         → pool_A.add_stocks([fz000001,fz000007])

t=70s  e3/e4 到时
       EdgeFired(e3): pool_A.get_dirty_codes() → 按边顺序号1计算
       EdgeFired(e4): pool_B.get_dirty_codes() → 按边顺序号2与e3结果取交集
         → pool_C.add_stocks([fz000001])
         → Signal(buy) → OrderPlaced → OrderFilled → PositionUpdated

t=1270s  TTLDue(pool_C, fz000001)
         → Signal(sell_all) → OrderPlaced → OrderFilled → PositionUpdated
         → pool_C.remove_stocks([fz000001])
         【一次性，不注册下次】
```

### R8: 禁止兼容旧接口

- 删除 `EdgeFired.changed_codes` 字段。唯一正确的 EdgeFired 只含 `eid` + `ts`。
- 删除 `EventDriver.fire_due` 线性扫描。唯一正确是优先队列。
- 删除 `execution_order`。事件没有顺序。
- 删除 `SimTickSource`。唯一正确是 `MockDataSource`。
- 删除 `state.get_node_stocks(nid)` / `state.set_node_stocks(nid, stocks)`。唯一正确是 `StatePool` 对象接口。
- 不保留旧接口包装、不保留兼容层。

### R9: 公式计算与筛选分离

- 公式=添加列，筛选=列操作。
- HQChartPy2，Python 3.13，无 cross。
- 增量评估：仅对 `dirty_codes ∩ source_codes` 重新评估，其余沿用缓存。
- 合并规则：`passed = (cached_passed - dirty_codes) | newly_passed`

### R10: 仿真与实盘共用代码

仿真模式除 tick 请求方式外，其他处理流程必须使用相同代码，禁止分别处理。

---

## REMOVED Requirements

| 已删除需求 | 删除原因 | 迁移方式 |
|-----------|---------|---------|
| EdgeFired 携带 changed_codes | EdgeFired 只发信号 | 删除字段 |
| 每个状态池独立维护脏股票 | 脏股票在 tick 表变更列，状态池是视图 | StatePool.get_dirty_codes() |
| SimTickSource | tick 由 MockDataSource 产生 | 重命名+重构 |
| 三段式核心循环 | 轮询语义 | 中断驱动 |
| execution_order（运行时拓扑排序） | 事件没有顺序，但边有设计结构顺序号 | 删除运行时排序，保留边顺序号 |
| 10类事件按序发布 | 事件没有顺序 | 各事件独立触发 |
| 兼容旧接口 | 禁止兼容 | 直接删除 |
