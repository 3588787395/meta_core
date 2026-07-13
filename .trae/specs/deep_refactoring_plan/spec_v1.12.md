# 股票池深度重构规划 v1.12

> 版本主题：诚实架构 + 性能落地 + 表驱动升级到行为表
> 设计原则：诚实不包装、数据驱动、增量优先、概念最简、逻辑在表里
> 目标：诚实描述"轮询数据 + 脏驱动计算"模型，停止"事件驱动"包装；性能模型基于真实代码估算，不拍脑袋；表驱动从"状态表"升级到"行为表"——逻辑在表里，引擎只查表调用；脏标记体系收敛，消除冗余

---

## v1.11 → v1.12 变更摘要表

**变更日期：** 2026-07-01

| # | 变更项 | v1.11 | v1.12 | 本质变化 |
|---|--------|------|-------|---------|
| 1 | **架构命名诚实化** | "零延迟事件驱动"（包装） | **"轮询数据 + 脏驱动计算"（诚实）** | 停止包装，承认是 while 循环 + sleep 轮询，数据不是事件推送来的；但内部计算是脏驱动的——变了才算，不变跳过 |
| 2 | **延迟模型诚实化** | "零延迟"（误导） | **延迟 = tick_interval（默认 1 秒）** | 诚实：轮询模型天然有延迟，延迟就是轮询间隔；不是零延迟，也不是事件驱动 |
| 3 | **性能模型落地** | "毫秒级"（拍脑袋） | **分层估算，有依据，真实场景量化** | 基于代码实际结构，分 5 层估算：数据层 O(N)、指标层 O(M)、比较层 O(E×C)、集合运算层、传播层；给出 5000 只股票真实场景估算 |
| 4 | **表驱动升级：从状态表到行为表** | 8 张全是状态表（存数据） | **新增 4 张行为表（定义逻辑）** | 真正的表驱动：node_type_table / operator_table / combine_table / propagate_table；加新节点类型 = 加一行，不改引擎代码 |
| 5 | **脏标记体系收敛** | dirty_stocks + node_stock_change + node_data_dirty（3 套概念冗余） | **dirty_stocks（全局）+ node_changes[nid]（三集合）** | 统一：node_changes[nid] = {entered, exited, updated}；不再区分 stock_change 和 data_dirty |
| 6 | **核心循环伪代码** | if-else 区分节点类型（代码驱动） | **查表调用 handler（表驱动）** | 引擎里没有 if node_type == XX 就做 XX；所有差异查 node_type_table，找到 handler 调用 |
| 7 | **核心运行时表数** | 8 张 | **7 张（收敛脏标记）** | node_stock_change + node_data_dirty → 合并为 node_changes；表数 -1，概念更纯净 |

**一句话总结 v1.12 升级：** 诚实——停止"事件驱动""零延迟"的包装，承认是"轮询数据 + 脏驱动计算"；性能落地——基于真实代码分层估算，不拍脑袋；表驱动升级——从"状态表"到"行为表"，逻辑在表里，引擎只查表；脏标记收敛——统一 node_changes 三集合，消除冗余。

---

## 一、诚实架构：轮询数据 + 脏驱动计算

### 1.1 停止包装：实际是什么就叫什么

**v1.11 及之前的包装（不诚实）：**
- "零延迟事件驱动"
- "事件模型"
- "每个 tick 是一个独立事件"

**实际代码（engine.py:3711-3718 run_mode）：**
```python
while not self._stop_event.is_set():
    await self._pause_event.wait()
    if self._stop_event.is_set(): break
    if not self._is_trading_time():
        await asyncio.sleep(self._tick_interval)
        continue
    tick_bar_data = self._refresh_bar_data(mc, current_bar_data)
    await self._check_refreshed_pool_data(nodes)
    self._loop_node_stocks = await self._tick(pool_config, self._loop_node_stocks, tick_bar_data)
    await asyncio.sleep(self._tick_interval)
```

**诚实描述：这就是一个 while 循环 + sleep(tick_interval) 的轮询模型。**

### 1.2 准确的名字：轮询数据 + 脏驱动计算

```
架构模型：轮询数据 + 脏驱动计算
┌─────────────────────────────────────────────────┐
│  while True:                                    │
│    sleep(tick_interval)  ← 轮询间隔，默认 1 秒  │
│    获取最新数据      ← 轮询数据，不是事件推送    │
│    if 数据变了:      ← 脏驱动：变了才算          │
│      计算指标                                │
│      比较判断                                │
│      集合运算                                │
│      传播更新                                │
│    else:             ← 没变就跳过，省时间        │
│      跳过                                    │
└─────────────────────────────────────────────────┘
```

**两层含义：**

| 层面 | 模型 | 说明 |
|------|------|------|
| **数据获取层** | **轮询** | 主动去 pull 最新数据，不是被动等事件 push；tick_interval 决定轮询频率（默认 1 秒） |
| **计算执行层** | **脏驱动** | 数据变了才计算，没变就跳过；不是每个 tick 都全量重算 |

**为什么叫这个名字不叫"事件驱动"：**
- 事件驱动的核心是：**事件源主动推送，处理方被动响应**
- 这里的核心是：**主动轮询拉取，然后检查有没有变化**
- 虽然内部计算是"变化驱动"（脏驱动），但数据来源是轮询，不是事件
- 诚实比包装重要——叫什么不影响正确性，但误导会影响实现

### 1.3 延迟模型：tick_interval 决定，不是零延迟

**v1.11 的说法（不诚实）："零延迟"**

**诚实说法：延迟 = tick_interval（默认 1 秒），这是轮询模型的固有特性。**

```
时间线：
  t=0.0s  第 1 次轮询 → 获取 t=0.0s 的数据 → 处理
  t=1.0s  第 2 次轮询 → 获取 t=1.0s 的数据 → 处理
  t=2.0s  第 3 次轮询 → 获取 t=2.0s 的数据 → 处理

如果某股票在 t=0.3s 价格变化了：
  → 要等到 t=1.0s 轮询时才发现
  → 延迟 = 0.7s（最坏 1s，平均 0.5s）
  → 不是零延迟！
```

**延迟特点：**
- **最坏延迟** = tick_interval（默认 1 秒）
- **平均延迟** ≈ tick_interval / 2
- **延迟可控**：通过调整 tick_interval 可以控制延迟（但要权衡性能）
- **不是实时推送**：没有"数据一来就立刻处理"这回事

**为什么之前会包装成"零延迟"：**
- 可能是把"计算快"和"延迟低"搞混了
- 计算确实快（毫秒级），但那是计算时间，不是延迟
- 延迟 = 轮询间隔 + 计算时间，轮询间隔才是大头

### 1.4 代码验证：确实是轮询，确实是脏驱动

**验证 1：轮询模型（engine.py:3711-3718）**
- `while True` + `asyncio.sleep(self._tick_interval)`
- `_refresh_bar_data()` 主动获取数据
- 没有事件回调、没有消息队列、没有推送机制

**验证 2：脏驱动计算（engine.py:2066-2093）**
- `_dirty_nodes` 集合：标记哪些节点变了
- `_mark_dirty(nid)`：节点变了就标记为脏
- `_update_node_snapshot()`：对比快照，变了才标脏
- 没变的节点，出边跳过不处理

**验证 3：tick_interval 配置（timing.json）**
- `tick_interval` 默认 1 秒
- 这就是轮询间隔，也是延迟的上限

---

## 二、性能模型落地：分层估算，有依据

### 2.1 不再拍脑袋：基于真实代码结构估算

**v1.11 的说法（拍脑袋）："5000 只股票，100 个指标，向量化计算应该在毫秒级（~10-100ms）"**

**问题：**
- "应该"是多少？依据是什么？
- 10ms 和 100ms 差 10 倍，哪个对？
- 5000 只股票是 A 股全部，100 个指标是每个边的还是总共的？

**v1.12 的做法：分层估算，每层有依据，最后汇总。**

### 2.2 五层性能模型

基于实际代码结构，把一个 tick 的处理分成 5 层：

```
┌─────────────────────────────────────────┐
│  层 1：数据层（轮询 + 更新）            │
│  O(N)，N = 股票数                      │
├─────────────────────────────────────────┤
│  层 2：指标层（向量化计算）            │
│  O(M)，M = 指标数                      │
├─────────────────────────────────────────┤
│  层 3：比较层（边 × 条件）             │
│  O(E × C)，E = 边数，C = 每边条件数   │
├─────────────────────────────────────────┤
│  层 4：集合运算层（AND/OR/排名）      │
│  AND/OR: O(N)，排名: O(N log N)       │
├─────────────────────────────────────────┤
│  层 5：传播层（更新目标节点）          │
│  O(K)，K = 变化的股票数                │
└─────────────────────────────────────────┘
```

### 2.3 每层详细估算

#### 层 1：数据层（轮询 + 更新）

**操作：** 从数据源获取最新 tick，更新 `latest_tick`，标记 `dirty_stocks`

**时间复杂度：** O(N)，N = 股票总数

**估算依据：**
- 就是一次内存字典更新，非常快
- 每只股票大概几百字节数据
- 5000 只股票 ≈ 几 MB 数据

**估算时间：**
| 股票数 | 估算时间 | 说明 |
|--------|---------|------|
| 1000 | ~0.1ms | 纯内存操作，很快 |
| 5000 | ~0.5ms | 5 倍数据量，线性增长 |
| 10000 | ~1ms | 10 倍数据量 |

#### 层 2：指标层（向量化计算）

**操作：** 调用公式引擎计算指标，NumPy/pandas 向量化

**时间复杂度：** O(M × N)，但常数很小（底层 C 实现）
- M = 指标个数（去重后）
- N = 股票数

**估算依据：**
- 向量化计算的瓶颈是 Python 调用开销，不是计算量
- 一次 Python 调用 + 底层 C 计算
- 简单指标（MA、EMA、MACD 等）很快
- 复杂指标（自定义公式）可能慢一些

**估算时间（每个指标，5000 只股票）：**
| 指标类型 | 估算时间 | 说明 |
|---------|---------|------|
| 简单指标（MA、VOL 等） | ~0.5ms | rolling 运算，NumPy 底层 |
| 中等指标（MACD、KDJ 等） | ~1-2ms | 多步 rolling，还是向量化 |
| 复杂指标（自定义公式） | ~5-10ms | 可能有 Python 循环，或者运算多 |

**典型场景：20 个指标（5 简单 + 10 中等 + 5 复杂）**
- 5 × 0.5ms + 10 × 1.5ms + 5 × 7.5ms = 2.5 + 15 + 37.5 = **~55ms**

#### 层 3：比较层（边 × 条件）

**操作：** 每条边的每个条件，比较指标值和阈值

**时间复杂度：** O(E × C × K)
- E = 边数
- C = 每边条件数（平均）
- K = 需要比较的股票数（脏股票，不是全部）

**估算依据：**
- 就是简单的数值比较，非常快
- 但如果是 Python 层循环，每只股票每次比较都有开销
- 理想情况：向量化比较（一次运算算完所有股票）

**估算时间（向量化比较）：**
| 边数 × 条件数 | 股票数 | 估算时间 |
|-------------|--------|---------|
| 10 × 3 = 30 | 5000 | ~1ms |
| 20 × 5 = 100 | 5000 | ~3ms |
| 50 × 10 = 500 | 5000 | ~15ms |

**注意：** 如果是增量计算（只比脏股票），时间会少很多。
但向量化情况下，全量算和增量算时间差不多，所以不如全量算。

#### 层 4：集合运算层

**操作：** AND/OR/NOT 集合运算，排名前 N/后 N

**时间复杂度：**
- AND/OR/NOT: O(N)，集合操作，很快
- 排名: O(N log N)，排序

**估算依据：**
- Python 的 set 操作是优化过的，很快
- 排序是 O(N log N)，但 N 是股票数，5000 只排序很快

**估算时间：**
| 操作 | 股票数 | 估算时间 |
|------|--------|---------|
| AND/OR/NOT | 5000 | ~0.1ms | set 操作，很快 |
| 排名（排序） | 5000 | ~0.5ms | Python sorted 很快 |
| 排名（top N） | 5000 | ~0.2ms | heapq.nlargest，不用全排序 |

#### 层 5：传播层

**操作：** 更新目标节点股票列表，计算 entered/exited，发事件

**时间复杂度：** O(K)，K = 变化的股票数

**估算依据：**
- 就是集合差集运算 + 列表更新
- 变化的股票数通常远小于总数（比如 1%）

**估算时间：**
| 变化股票数 | 估算时间 |
|-----------|---------|
| 10 | ~0.01ms |
| 50 | ~0.05ms |
| 5000（全量） | ~0.5ms |

### 2.4 真实场景估算：5000 只股票的典型股票池

**场景设定（一个中等复杂度的股票池）：**
- 股票数：5000 只（A 股全部）
- 节点数：20 个（候选池 + 多个条件池 + 持仓池 + 观察池等）
- 边数：10 条（主要过滤链路）
- 每边条件数：平均 3 个
- 指标数：总共 20 个（去重后）
- 复杂指标比例：25%（5 个）

**分层估算：**

| 层级 | 操作 | 估算时间 | 占比 |
|------|------|---------|------|
| 层 1：数据层 | 轮询 + 更新 latest_tick | ~0.5ms | 0.8% |
| 层 2：指标层 | 20 个指标向量化计算 | ~55ms | 86% |
| 层 3：比较层 | 10 边 × 3 条件 = 30 次比较 | ~1ms | 1.6% |
| 层 4：集合运算层 | AND/OR + 排名 | ~0.5ms | 0.8% |
| 层 5：传播层 | 更新节点 + 发事件 | ~0.1ms | 0.2% |
| **合计** | | **~57ms** | **100%** |

**结论：**
- 一个 tick 的处理时间 ≈ **50-60ms**（典型场景）
- 瓶颈在**指标层**（占 85% 以上）
- 其他层加起来不到 10%
- tick_interval = 1s 完全够用，CPU 使用率 < 10%

### 2.5 极端场景估算

**场景：超复杂股票池**
- 股票数：5000 只
- 指标数：100 个（超多指标）
- 边数：50 条
- 每边条件数：平均 10 个

| 层级 | 估算时间 | 说明 |
|------|---------|------|
| 数据层 | ~0.5ms | 不变 |
| 指标层 | ~200-300ms | 100 个指标，很多复杂的 |
| 比较层 | ~15ms | 50 × 10 = 500 次比较 |
| 集合运算层 | ~1ms | 多次排名 |
| 传播层 | ~0.5ms | 变化可能多一些 |
| **合计** | **~220-320ms** | |

**结论：** 即使超复杂，也在 300ms 以内，1 秒 tick 还是够用。

### 2.6 性能瓶颈与优化方向

**当前瓶颈：指标层（占 85%+）**

**优化方向（按性价比排序）：**
1. **指标缓存**：数据没变的指标，直接读缓存，不重算（已经有了）
2. **增量计算**：只重算脏股票的指标（但向量化下收益有限）
3. **指标去重**：多条边用同一个指标，只算一次（已经做了）
4. **C 扩展**：把最耗时的指标用 C 重写（工作量大）
5. **并行计算**：多进程/多线程计算指标（GIL 限制，收益有限）

**不需要优化的：**
- 数据层：已经很快了
- 比较层：已经很快了
- 集合运算层：已经很快了
- 传播层：已经很快了

---

## 三、表驱动升级：从状态表到行为表

### 3.1 之前的问题：全是状态表，没有行为表

**v1.11 的 8 张表，全是**状态表**（存数据的）：**

| 表名 | 类型 | 说明 |
|------|------|------|
| latest_tick | 状态表 | 存最新 tick 数据 |
| node_stocks | 状态表 | 存节点股票列表 |
| ttl_expiry_queue | 状态表 | 存 TTL 过期队列 |
| dirty_stocks | 状态表 | 存脏股票集合 |
| node_stock_change | 状态表 | 存节点变化 |
| node_data_dirty | 状态表 | 存节点数据脏标记 |
| edge_compare_results | 状态表 | 存比较结果 |
| edge_filter_results | 状态表 | 存过滤结果 |

**问题：这不是真正的表驱动。**

真正的表驱动是什么？
- 不是"数据存在表里"（那叫用表存数据）
- 而是"**逻辑定义在表里**"（加新功能 = 加一行，不改引擎代码）

### 3.2 真正的表驱动：行为表

**行为表：定义"做什么"和"怎么做"的表。**

之前的状态表定义的是"**有什么**"（数据）。
行为表定义的是"**做什么**"（逻辑）。

**4 张核心行为表：**

| 行为表 | 定义什么 | 加新东西 = 加一行 |
|--------|---------|------------------|
| node_type_table | 每种节点类型的行为 | 加新节点类型 = 加一行 |
| operator_table | 每种比较算子的行为 | 加新算子 = 加一行 |
| combine_table | 每种集合运算的行为 | 加新运算 = 加一行 |
| propagate_table | 每种传播方式的行为 | 加新传播方式 = 加一行 |

### 3.3 行为表详细设计

#### 表 1：node_type_table（节点类型行为表）

**定义：每种节点类型的完整行为。**

| 字段 | 类型 | 说明 |
|------|------|------|
| type_id | str | 节点类型 ID（如 'market_source', 'stock_state_pool'） |
| init_handler | str | 初始化函数名 |
| in_edge_handler | str | 入边处理函数名 |
| out_edge_handler | str | 出边处理函数名 |
| allowed_roles | List[str] | 允许的角色（如 'source', 'target', 'both'） |
| has_stocks | bool | 是否有股票列表（有的节点是纯逻辑节点，没有股票） |
| properties | dict | 其他属性 |

**示例数据：**

```json
{
  "node_type_table": {
    "market_source": {
      "init_handler": "init_market_source",
      "in_edge_handler": null,
      "out_edge_handler": "out_edge_resolve_and_pass",
      "allowed_roles": ["source"],
      "has_stocks": true,
      "properties": { "is_entry_point": true }
    },
    "stock_state_pool": {
      "init_handler": "init_stock_state_pool",
      "in_edge_handler": "in_edge_default",
      "out_edge_handler": "out_edge_default",
      "allowed_roles": ["source", "target", "both"],
      "has_stocks": true,
      "properties": {}
    },
    "transfer_condition": {
      "init_handler": "init_transfer_condition",
      "in_edge_handler": "in_edge_pass_through",
      "out_edge_handler": "out_edge_apply_filter",
      "allowed_roles": ["both"],
      "has_stocks": true,
      "properties": { "is_filter": true }
    },
    "discard_pool": {
      "init_handler": "init_discard_pool",
      "in_edge_handler": "in_edge_default",
      "out_edge_handler": null,
      "allowed_roles": ["target"],
      "has_stocks": true,
      "properties": { "is_terminal": true }
    }
  }
}
```

**加新节点类型的例子：**

想加一个"指标预警节点"（alert_node）：
1. 在 `node_type_table` 里加一行
2. 定义它的 init_handler、in_edge_handler、out_edge_handler
3. **不需要改引擎代码**

引擎代码里的处理（表驱动，没有 if-else）：
```python
def process_node(nid):
    node = nodes[nid]
    node_type = node['type']
    type_def = node_type_table[node_type]  # 查表
    
    # 调出边 handler（不是 if node_type == XX）
    handler_name = type_def['out_edge_handler']
    if handler_name:
        handler = getattr(self, handler_name)
        handler(nid, node)
```

#### 表 2：operator_table（比较算子行为表）

**定义：每种比较算子的行为。**

| 字段 | 类型 | 说明 |
|------|------|------|
| op_id | str | 算子 ID（如 'gt', 'lt', 'eq', 'between'） |
| func | str | 比较函数名 |
| param_count | int | 参数个数（除了左边的值） |
| param_names | List[str] | 参数名（用于配置 UI） |
| tri_state | bool | 是否支持三态（None） |
| vectorized | bool | 是否支持向量化 |

**示例数据：**

```json
{
  "operator_table": {
    "gt": {
      "func": "op_gt",
      "param_count": 1,
      "param_names": ["threshold"],
      "tri_state": true,
      "vectorized": true
    },
    "lt": {
      "func": "op_lt",
      "param_count": 1,
      "param_names": ["threshold"],
      "tri_state": true,
      "vectorized": true
    },
    "between": {
      "func": "op_between",
      "param_count": 2,
      "param_names": ["low", "high"],
      "tri_state": true,
      "vectorized": true
    },
    "cross_up": {
      "func": "op_cross_up",
      "param_count": 1,
      "param_names": ["threshold"],
      "tri_state": true,
      "vectorized": false
    }
  }
}
```

**加新算子的例子：**

想加一个"涨跌幅超过 N%"的算子（change_pct）：
1. 在 `operator_table` 里加一行
2. 实现 `op_change_pct` 函数
3. **不需要改比较层的引擎代码**

#### 表 3：combine_table（集合运算行为表）

**定义：每种集合运算的行为。**

| 字段 | 类型 | 说明 |
|------|------|------|
| op_id | str | 运算 ID（如 'and', 'or', 'not', 'top_n', 'bottom_n'） |
| func | str | 运算函数名 |
| param_count | int | 参数个数 |
| param_names | List[str] | 参数名 |
| input_count | str | 输入数量（'single', 'multiple', 'variable'） |
| tri_state | bool | 是否支持三态 |

**示例数据：**

```json
{
  "combine_table": {
    "and": {
      "func": "combine_and",
      "param_count": 0,
      "param_names": [],
      "input_count": "multiple",
      "tri_state": true
    },
    "or": {
      "func": "combine_or",
      "param_count": 0,
      "param_names": [],
      "input_count": "multiple",
      "tri_state": true
    },
    "not": {
      "func": "combine_not",
      "param_count": 0,
      "param_names": [],
      "input_count": "single",
      "tri_state": true
    },
    "top_n": {
      "func": "combine_top_n",
      "param_count": 2,
      "param_names": ["n", "sort_by"],
      "input_count": "single",
      "tri_state": false
    },
    "bottom_n": {
      "func": "combine_bottom_n",
      "param_count": 2,
      "param_names": ["n", "sort_by"],
      "input_count": "single",
      "tri_state": false
    }
  }
}
```

#### 表 4：propagate_table（传播方式行为表）

**定义：每种传播方式的行为。**

| 字段 | 类型 | 说明 |
|------|------|------|
| mode_id | str | 传播模式 ID（如 'copy', 'move', 'overwrite', 'merge'） |
| func | str | 传播函数名 |
| affects_source | bool | 是否影响源节点 |
| affects_target | bool | 是否影响目标节点 |
| needs_tracker | bool | 是否需要 tracker 信息 |

**示例数据：**

```json
{
  "propagate_table": {
    "copy": {
      "func": "propagate_copy",
      "affects_source": false,
      "affects_target": true,
      "needs_tracker": false
    },
    "move": {
      "func": "propagate_move",
      "affects_source": true,
      "affects_target": true,
      "needs_tracker": true
    },
    "overwrite": {
      "func": "propagate_overwrite",
      "affects_source": false,
      "affects_target": true,
      "needs_tracker": false
    },
    "merge": {
      "func": "propagate_merge",
      "affects_source": false,
      "affects_target": true,
      "needs_tracker": true
    }
  }
}
```

### 3.4 真正的表驱动核心循环

**之前的伪代码（代码驱动，if-else 区分节点类型）：**

```python
def process_node(nid):
    node = nodes[nid]
    node_type = node['type']
    
    # 代码驱动：if-else 区分节点类型
    if node_type == 'market_source':
        process_market_source(nid, node)
    elif node_type == 'stock_state_pool':
        process_stock_state_pool(nid, node)
    elif node_type == 'transfer_condition':
        process_transfer_condition(nid, node)
    elif node_type == 'discard_pool':
        process_discard_pool(nid, node)
    # ... 加新类型就要加新的 elif
```

**v1.12 的伪代码（表驱动，查表调用）：**

```python
def process_node(nid):
    node = nodes[nid]
    node_type = node['type']
    
    # 表驱动：查 node_type_table，找到 handler，调用
    type_def = node_type_table.get(node_type)
    if not type_def:
        return  # 未知类型，跳过
    
    handler_name = type_def.get('out_edge_handler')
    if handler_name:
        handler = handler_registry.get(handler_name)
        if handler:
            handler(nid, node)
    # 加新节点类型？在 node_type_table 里加一行就行
    # 引擎代码完全不用改
```

**关键区别：**

| 维度 | 代码驱动（旧） | 表驱动（新） |
|------|--------------|------------|
| 加新节点类型 | 改引擎代码（加 elif） | 加表行（不改代码） |
| 逻辑位置 | 分散在 if-else 里 | 集中在行为表里 |
| 可扩展性 | 差，要改代码 | 好，只加配置 |
| 可读性 | 要读代码才知道有哪些类型 | 看表就知道所有类型 |
| 可测试性 | 每个分支单独测 | 每个 handler 单独测 + 表数据测 |

### 3.5 现有代码中的表驱动雏形

**好消息：现有代码里已经有一些表驱动的雏形了。**

**例 1：edge_strategies.json 中的 strategies（engine.py:359）**
- 已经是"源类型:目标类型 → 策略"的映射
- 但还不是完整的行为表，只是策略配置

**例 2：timing.json 中的 starttype_rules（engine.py:789）**
- 已经是"starttype → 规则 → primitive"的映射
- `_eval_timing_primitive` 里已经是查表调用

**例 3：modules.json（engine.py:358）**
- 已经是"模块类型 → handler"的映射
- `_run_module` 方法已经是查表调用

**方向是对的，只是还不彻底。**
v1.12 的目标是：把所有行为差异都收敛到行为表里，引擎代码里没有 if-else 区分类型。

---

## 四、脏标记体系收敛

### 4.1 当前问题：多套概念冗余

**v1.11 的脏标记有 3 套概念：**

| 概念 | 类型 | 说明 |
|------|------|------|
| dirty_stocks | Set[code] | 全局：本轮数据更新了的股票 |
| node_stock_change[nid] | {entered: Set, exited: Set} | 节点：股票进出变化 |
| node_data_dirty[nid] | Set[code] | 节点：数据更新的股票 |

**问题：**
1. **概念冗余**：node_stock_change 和 node_data_dirty 都是"节点变化"，为什么要分开？
2. **语义不清**：stock_change 是"股票变化"，data_dirty 也是"股票数据变化"，区别是什么？
3. **处理复杂**：处理一个节点的时候，要同时看 entered/exited/data_dirty 三个来源

**为什么会有三套？**
- 历史原因：逐步加上去的
- node_stock_change 是"传播带来的变化"（入边传播过来的）
- node_data_dirty 是"数据更新带来的变化"（行情数据变了）
- 但本质上，它们都是"节点里的股票发生了变化"，需要重新计算

### 4.2 收敛方案：统一为 node_changes 三集合

**v1.12 的方案：两套变一套，两集合变三集合。**

| v1.11（3 套概念） | v1.12（2 套概念，收敛） | 说明 |
|-------------------|----------------------|------|
| dirty_stocks | **dirty_stocks**（保留，全局） | 全局水位线：本轮数据更新了哪些股票 |
| node_stock_change[nid].entered | **node_changes[nid].entered** | 新进入节点的股票 |
| node_stock_change[nid].exited | **node_changes[nid].exited** | 离开节点的股票 |
| node_data_dirty[nid] | **node_changes[nid].updated** | 还在节点里，但数据更新了的股票 |

**新的 node_changes 结构：**

```python
node_changes = {
    nid: {
        'entered': set(),   # 新进入节点的股票
        'exited': set(),    # 离开节点的股票
        'updated': set(),   # 还在节点里，但数据更新了的股票
    }
}
```

**三个集合的含义：**

| 集合 | 含义 | 来源 |
|------|------|------|
| entered | 新进入节点的股票 | 入边 propagate 过来的 |
| exited | 离开节点的股票 | TTL 过期、出边 move 等 |
| updated | 还在节点里，但数据更新了 | 全局 dirty_stocks ∩ node_stocks[nid] |

### 4.3 一个节点的变化 = 入边传播 + 数据更新

**计算方式：**

```python
def compute_node_changes(nid):
    """计算一个节点的所有变化"""
    changes = {
        'entered': set(),
        'exited': set(),
        'updated': set(),
    }
    
    # 1. 入边传播过来的变化（entered/exited）
    for in_edge in in_edges[nid]:
        edge_changes = edge_propagate_changes[in_edge['id']]
        changes['entered'] |= edge_changes.get('entered', set())
        changes['exited'] |= edge_changes.get('exited', set())
    
    # 2. 数据更新的变化（updated）
    # = 全局 dirty_stocks ∩ 当前节点的股票
    current_stocks = set(node_stocks[nid])
    changes['updated'] = dirty_stocks & current_stocks
    
    # 注意：entered 和 updated 可能有重叠吗？
    # 不会，因为 entered 的股票是"刚进来的"，还不在 node_stocks 里
    # 等 propagate 完成后，才会加入 node_stocks
    
    return changes
```

### 4.4 处理节点时的判断逻辑

**v1.11（三个来源，判断复杂）：**

```python
def process_node(nid):
    entered = node_stock_change[nid]['entered']
    exited = node_stock_change[nid]['exited']
    data_dirty = node_data_dirty.get(nid, set())
    
    # 有变化吗？要同时看三个
    if not entered and not exited and not data_dirty:
        return  # 跳过
    
    # 需要重算指标的股票：entered + data_dirty
    codes_to_eval = entered | data_dirty
    # ...
```

**v1.12（一个对象，清晰明了）：**

```python
def process_node(nid):
    changes = node_changes.get(nid, {'entered': set(), 'exited': set(), 'updated': set()})
    entered = changes['entered']
    exited = changes['exited']
    updated = changes['updated']
    
    # 有变化吗？看三个集合
    if not entered and not exited and not updated:
        return  # 跳过
    
    # 需要重算指标的股票：entered + updated
    codes_to_eval = entered | updated
    # ...
```

**为什么更清晰：**
- 只有一个 `node_changes` 对象，不用记好几个变量名
- 三个集合的含义明确：进来的、出去的、还在但变了的
- 不再区分"stock_change"和"data_dirty"这种容易混淆的概念

### 4.5 核心运行时表变化

| v1.11（8 张） | v1.12（7 张） | 变化 |
|--------------|--------------|------|
| latest_tick | latest_tick | 保留，不变 |
| node_stocks | node_stocks | 保留，不变 |
| ttl_expiry_queue | ttl_expiry_queue | 保留，不变 |
| dirty_stocks | dirty_stocks | 保留，不变（全局水位线） |
| node_stock_change | **node_changes** | 合并 + 升级：从 2 集合到 3 集合 |
| node_data_dirty | **（删除）** | 合并进 node_changes.updated |
| edge_compare_results | edge_compare_results | 保留，不变 |
| edge_filter_results | edge_filter_results | 保留，不变 |

**净变化：8 张 → 7 张（合并 2 张为 1 张）**

---

## 五、核心循环伪代码（v1.12 表驱动版）

### 5.1 主循环：轮询 + 脏驱动

```python
# ============================================================
#  v1.12 核心循环伪代码（轮询数据 + 脏驱动计算 + 表驱动）
# ============================================================

# --- 初始化 ---
# 加载行为表
node_type_table = load_table('node_type_table')
operator_table = load_table('operator_table')
combine_table = load_table('combine_table')
propagate_table = load_table('propagate_table')

# 注册 handler（所有 handler 提前注册好）
handler_registry = register_all_handlers()

# 编译期：拓扑排序
topo_order = build_topo_order(nodes, edges)

# --- 主循环（轮询模型）---
tick_interval = 1.0  # 秒，默认 1 秒

while running:
    # 1. 等待下一个 tick（轮询间隔 = 延迟）
    await asyncio.sleep(tick_interval)
    
    if paused:
        continue
    
    # 2. 轮询获取最新数据
    tick_data = poll_latest_data()
    
    # 3. 更新数据层 + 标记脏股票
    dirty_stocks.clear()
    for code, new_bar in tick_data.items():
        if latest_tick.get(code) != new_bar:
            latest_tick[code] = new_bar
            dirty_stocks.add(code)
    
    # 4. 如果没有数据变化，跳过计算（脏驱动）
    if not dirty_stocks:
        continue  # 数据没变，不用算
    
    # 5. 计算每个节点的变化（node_changes）
    compute_all_node_changes(dirty_stocks)
    
    # 6. 按拓扑序处理脏节点（表驱动）
    for nid in topo_order:
        if not is_node_dirty(nid):
            continue  # 节点没变，跳过
        
        # 表驱动：查 node_type_table，调出边 handler
        node = nodes[nid]
        type_def = node_type_table[node['type']]
        handler_name = type_def['out_edge_handler']
        
        if handler_name:
            handler = handler_registry[handler_name]
            handler(nid, node, node_changes[nid])
    
    # 7. 后处理（PK 排名 / 分析角度 / 预警）
    post_process()
    
    # 8. 清脏，为下一轮做准备
    clear_all_dirty()
```

### 5.2 节点处理（表驱动）

```python
def process_node_out_edges(nid, node, changes):
    """处理一个节点的所有出边（表驱动版本）
    
    没有 if-else 区分节点类型，所有差异查 node_type_table
    """
    entered = changes['entered']
    exited = changes['exited']
    updated = changes['updated']
    
    source_codes = set(node_stocks[nid])
    
    # 遍历所有出边
    for edge in out_edges[nid]:
        eid = edge['id']
        
        # 检查时间条件（查表：edge_timer_specs）
        if not edge_should_fire_now(edge):
            continue
        
        # 处理这条边（三层 filter + propagate）
        process_edge(edge, source_codes, entered, exited, updated)
```

### 5.3 边处理（三层 filter + 表驱动算子）

```python
def process_edge(edge, source_codes, entered, exited, updated):
    """处理一条边：三层 filter + propagate（表驱动算子）
    
    比较算子查 operator_table
    集合运算查 combine_table
    传播方式查 propagate_table
    """
    eid = edge['id']
    tid = edge['target_id']
    
    # === 第一层：指标计算（向量化） ===
    indicator_ids = edge['indicators']
    codes_to_eval = entered | updated  # 新入的 + 数据更新的
    
    if codes_to_eval:
        indicator_values = formula_engine.eval_indicators(
            formula_ids=indicator_ids,
            codes=list(codes_to_eval),
        )
    else:
        indicator_values = {}
    
    # === 第二层：比较判断（表驱动算子） ===
    compare_spec = edge['compare_spec']
    
    # 查表：用什么算子
    op_id = compare_spec['operator']
    op_def = operator_table[op_id]
    op_func = handler_registry[op_def['func']]
    
    # 新入池的：先设 None（三态：数据不足）
    for code in entered:
        edge_compare_results[eid][code] = None
    
    # 有指标值的：计算比较结果
    codes_with_data = (entered | updated) & source_codes
    for code in codes_with_data:
        if code not in indicator_values:
            continue
        result = op_func(indicator_values[code], compare_spec['params'])
        edge_compare_results[eid][code] = result
    
    # 出池的：清理
    for code in exited:
        edge_compare_results[eid].pop(code, None)
    
    # === 第三层：集合运算（表驱动 combine） ===
    combine_op = edge['combine_op']
    combine_def = combine_table[combine_op]
    combine_func = handler_registry[combine_def['func']]
    
    filter_result = combine_func(edge_compare_results[eid], source_codes, edge['combine_params'])
    edge_filter_results[eid] = filter_result
    
    # === propagate（表驱动传播方式） ===
    propagate_mode = edge['propagate_mode']
    propagate_def = propagate_table[propagate_mode]
    propagate_func = handler_registry[propagate_def['func']]
    
    new_entered, new_exited = propagate_func(
        source_id=edge['source_id'],
        target_id=tid,
        filter_result=filter_result,
        edge=edge,
    )
    
    # 更新目标节点的 changes
    if tid not in node_changes:
        node_changes[tid] = {'entered': set(), 'exited': set(), 'updated': set()}
    node_changes[tid]['entered'] |= new_entered
    node_changes[tid]['exited'] |= new_exited
    
    # 标记目标节点为脏
    mark_node_dirty(tid)
    
    # 发事件
    emit_events(tid, new_entered, new_exited)
```

### 5.4 表驱动的关键：handler_registry

**所有 handler 提前注册，运行时只查表调用。**

```python
# 注册 handler（启动时做一次）
handler_registry = {}

def register_handler(name, func):
    handler_registry[name] = func

# 节点相关
register_handler('init_market_source', init_market_source)
register_handler('init_stock_state_pool', init_stock_state_pool)
register_handler('out_edge_default', out_edge_default)
register_handler('out_edge_apply_filter', out_edge_apply_filter)

# 算子相关
register_handler('op_gt', op_gt)
register_handler('op_lt', op_lt)
register_handler('op_between', op_between)

# 集合运算相关
register_handler('combine_and', combine_and)
register_handler('combine_or', combine_or)
register_handler('combine_top_n', combine_top_n)

# 传播相关
register_handler('propagate_copy', propagate_copy)
register_handler('propagate_move', propagate_move)

# 运行时：查表调用
handler = handler_registry[handler_name]
result = handler(args)
```

**引擎代码里没有 if-else，只有查表调用。**

---

## 六、运行时表清单（v1.12 更新版）

### 6.1 核心运行时表（7 张）

| # | 表名 | 类型 | 读时机 | 写时机 | 说明 |
|---|------|------|--------|--------|------|
| 1 | `latest_tick` | Dict[code → bar_dict] | 公式引擎计算时读 | tick 开始时更新 | **唯一真相源**。所有股票的最新 tick 数据 |
| 2 | `node_stocks` | Dict[nid → List[code]] | propagate 读写、filter 读 | 边执行/TTL过期后写 | 各节点当前股票列表 |
| 3 | `ttl_expiry_queue` | Heap[(expire_ts, nid, code)] | TTL检查时弹出 | 股票入池时插入 | TTL 过期队列。按过期时间排序的最小堆 |
| 4 | `dirty_stocks` | Set[code] | 计算 node_changes 时用 | tick 开始时收集 / tick 结束时清空 | **全局水位线**。本 tick 数据更新了的股票集合 |
| 5 | `node_changes` | Dict[nid → {entered, exited, updated}] | 执行循环读（增量处理） | propagate/TTL/数据更新时写入 | **节点变化三集合**。entered=新进入，exited=离开，updated=还在但数据更新了 |
| 6 | `edge_compare_results` | Dict[eid → Dict[code → True/False/None]] | 集合运算层读 | 比较层写（增量更新） | **三态比较结果**。None=数据不足。新入池股票先设 None，再计算 |
| 7 | `edge_filter_results` | Dict[eid → Set[code] 或 List[code]] | propagate 读 | 集合运算层写 | 排名型用有序列表，独立型用 Set。只放通过的，None/False 都不在 |

**v1.12 相比 v1.11 的变化：**

| v1.11（8 张） | v1.12（7 张） | 变化原因 |
|--------------|--------------|---------|
| node_stock_change + node_data_dirty | **node_changes**（三集合） | 脏标记体系收敛：两套变一套，两集合变三集合 |

### 6.2 行为表（4 张，新增）

| # | 表名 | 类型 | 读时机 | 写时机 | 说明 |
|---|------|------|--------|--------|------|
| 1 | `node_type_table` | Dict[type_id → type_def] | 处理节点时查表 | 配置加载时写入 | **节点类型行为表**。每种节点类型的 init/in/out handler |
| 2 | `operator_table` | Dict[op_id → op_def] | 比较判断时查表 | 配置加载时写入 | **比较算子行为表**。每种比较算子的函数、参数 |
| 3 | `combine_table` | Dict[op_id → op_def] | 集合运算时查表 | 配置加载时写入 | **集合运算行为表**。每种集合运算的函数、参数 |
| 4 | `propagate_table` | Dict[mode_id → mode_def] | propagate 时查表 | 配置加载时写入 | **传播方式行为表**。每种传播方式的函数、属性 |

**行为表是编译期/启动时加载的，运行时只读，不写。**

### 6.3 公式引擎内部表（黑盒）

| 表名 | 类型 | 说明 |
|------|------|------|
| `formula_registry` | Dict[formula_id → CompiledFormula] | 公式注册表（通过 register_formula 注入） |
| `indicator_results` | Dict[(formula_id, period, args_key) → Dict[code → value 或 None]] | 指标值缓存。数据不足时 value 为 None |
| `data_cache` | Dict[(code, period) → DataFrame] | K线数据缓存 |

### 6.4 编译期表（不变）

| 编译产物 | 类型 | 说明 |
|----------|------|------|
| `formula_registry` | Dict[indicator_id → formula_spec] | 公式注册表。所有去重后的指标 |
| `comparison_operators` | Dict[op_id → operator_spec] | 比较算子集（逐步迁移到 operator_table） |
| `edge_indicator_refs` | Dict[eid → List[indicator_id]] | 每条边引用的指标ID列表 |
| `edge_compare_spec` | Dict[eid → compare_spec] | 比较层规格 |
| `edge_set_op_spec` | Dict[eid → set_op_spec] | 集合运算层规格（逐步迁移到 combine_table） |
| `edge_filter_type` | Dict[eid → 'independent' / 'global'] | filter 类型 |
| `edge_timer_specs` | Dict[eid → timer_spec] | 每条边的定时器配置 |

---

## 七、功能-表操作对应表（v1.12 更新版）

### 7.1 主循环层（轮询 + 脏驱动）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **轮询等待** | tick_interval 配置 | — | sleep(tick_interval)，延迟 = tick_interval |
| **数据轮询** | 数据源接口 | `latest_tick` + `dirty_stocks` | 主动 pull 最新数据，对比变化，标记脏股票 |
| **脏驱动跳过** | `dirty_stocks` | — | 如果 dirty_stocks 为空，直接跳过整个计算 |
| **节点变化计算** | `dirty_stocks` + `node_stocks` | `node_changes` | entered/exited 来自传播，updated = dirty_stocks ∩ node_stocks |
| **拓扑序处理** | `node_changes` + 拓扑序 | 各层状态表 | 按拓扑序处理脏节点，干净节点跳过 |

### 7.2 节点处理层（表驱动）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **节点类型判断** | `node_type_table` | — | 查表得到 type_def，不是 if-else 判断 |
| **初始化 handler** | `node_type_table.init_handler` | `node_stocks` | 查表调用对应的初始化函数 |
| **入边 handler** | `node_type_table.in_edge_handler` | `node_changes` | 查表调用对应的入边处理函数 |
| **出边 handler** | `node_type_table.out_edge_handler` | 各边状态表 | 查表调用对应的出边处理函数 |
| **脏节点判断** | `node_changes[nid]` | — | entered/exited/updated 全空就是干净的 |

### 7.3 边执行层（三层 filter + 表驱动算子）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **时间条件检查** | `edge_timer_specs` | — | 时间到了才执行 |
| **第一层：指标计算** | 公式引擎接口 | 公式引擎内部表 | 增量：只重算 entered ∪ updated 的。向量化计算 |
| **第二层：比较判断** | `operator_table` + 指标值 | `edge_compare_results` | 查表调算子函数。三态：True/False/None |
| **第三层：集合运算** | `combine_table` + 比较结果 | `edge_filter_results` | 查表调集合运算函数。AND/OR/排名等 |
| **propagate** | `propagate_table` + filter 结果 | `node_stocks` + `node_changes` | 查表调传播函数。copy/move/overwrite/merge |
| **事件发射** | `node_changes[tid]` | 事件队列 | entered/exited 直接发事件 |

### 7.4 数据层

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **tick 数据更新** | 数据源 | `latest_tick` + `dirty_stocks` | 轮询拉取，对比变化，标记脏股票 |
| **通知公式引擎** | `dirty_stocks` | 公式引擎内部失效标记 | `formula_engine.on_data_updated(dirty_codes)` |
| **指标计算（向量化）** | 公式引擎接口 | 公式引擎内部表 | 空间批量：一批股票一起算，NumPy 底层 C 实现 |

### 7.5 TTL 淘汰层

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| 股票入池记录 TTL | 边配置 | `ttl_expiry_queue` 插入 | `expire_ts = current_ts + ttl_sec` |
| TTL 过期检查 | `ttl_expiry_queue + current_ts` | 弹出过期项 | 最小堆：堆顶过期就弹出 |
| 过期股票移除 | `node_stocks[nid]` | `node_stocks[nid]` | 从节点移除 |
| **过期触发级联** | — | `node_changes[nid].exited.add(code)` | 加入 exited 集合 |

### 7.6 后处理层

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| PK排名 | `node_stocks[target] + pk_config` | `_pk_rankings` | 按权重评分排序，指标值通过公式引擎接口 |
| 分析角度 | `node_stocks[target] + analysis_config` | `_angle_results` | 多维度计算，指标值通过公式引擎接口 |
| 看盘面板 | `node_stocks + dashboard_schema` | `_dashboard_data` | 组装显示数据，指标值通过公式引擎接口 |
| 预警检查 | `alert_rules + node_changes` | `_alert_queue` | 规则匹配 + cooldown 检查 |

---

## 八、概念变化对照表（v1.11 → v1.12）

### 8.1 诚实化：停止包装

| v1.11（包装/不诚实） | v1.12（诚实） | 理由 |
|---------------------|--------------|------|
| "零延迟事件驱动" | **"轮询数据 + 脏驱动计算"** | 实际是 while 循环 + sleep 轮询，不是事件推送；但内部计算是脏驱动的 |
| "零延迟" | **延迟 = tick_interval（默认 1s）** | 轮询模型天然有延迟，延迟就是轮询间隔；诚实比包装重要 |
| "每个 tick 是一个独立事件" | **每个 tick 是一次轮询** | 事件是被动接收的，轮询是主动拉取的；概念上完全不同 |
| "事件驱动架构" | **轮询 + 脏驱动架构** | 架构的核心是轮询获取数据，不是事件驱动；脏驱动是计算层的优化 |

### 8.2 性能模型：从拍脑袋到落地

| v1.11（拍脑袋） | v1.12（落地） | 理由 |
|----------------|--------------|------|
| "毫秒级"（笼统） | **分层估算：数据层/指标层/比较层/集合运算层/传播层** | 每层有依据，不拍脑袋 |
| "5000只股票100个指标~10-100ms" | **典型场景~50-60ms，瓶颈在指标层（占85%+）** | 基于真实代码结构估算，有具体数字 |
| "向量化很快" | **向量化是快，但 Python 调用开销是瓶颈** | 计算在 C 层很快，但 Python 调用开销不可忽视 |
| 没有瓶颈分析 | **瓶颈在指标层，其他层加起来不到10%** | 明确优化方向，不瞎优化 |

### 8.3 表驱动：从状态表到行为表

| v1.11（状态表） | v1.12（状态表 + 行为表） | 理由 |
|----------------|------------------------|------|
| 8 张全是状态表（存数据） | **7 张状态表 + 4 张行为表（定义逻辑）** | 真正的表驱动是逻辑在表里，不是数据在表里 |
| 加新节点类型 = 改引擎代码（加 elif） | **加新节点类型 = 加表行（不改代码）** | 这才是表驱动的核心价值 |
| 引擎里 if-else 区分类型 | **引擎里查表调用 handler** | 代码驱动 → 表驱动 |
| 没有 node_type_table | **有 node_type_table** | 节点类型行为定义在表里 |
| 没有 operator_table | **有 operator_table** | 比较算子行为定义在表里 |
| 没有 combine_table | **有 combine_table** | 集合运算行为定义在表里 |
| 没有 propagate_table | **有 propagate_table** | 传播方式行为定义在表里 |

### 8.4 脏标记：从分散到收敛

| v1.11（3 套概念） | v1.12（2 套概念，收敛） | 理由 |
|------------------|----------------------|------|
| dirty_stocks（全局） | **dirty_stocks（全局）** | 保留，全局水位线 |
| node_stock_change[nid].entered/exited | **node_changes[nid].entered/exited** | 改名，更清晰 |
| node_data_dirty[nid] | **node_changes[nid].updated** | 合并进 node_changes，叫 updated |
| 3 套概念（dirty_stocks + stock_change + data_dirty） | **2 套概念（dirty_stocks + node_changes）** | 减少冗余，降低认知负担 |
| 2 集合（entered/exited） | **3 集合（entered/exited/updated）** | 语义更清晰：进来的、出去的、还在但变了的 |

### 8.5 概念数量统计

| 版本 | 核心运行时表数 | 行为表数 | 变化 |
|------|---------------|---------|------|
| v1.5 | 8 张 | 0 张 | — |
| v1.6 | 7 张 | 0 张 | -1 |
| v1.6.1 | 5 张 | 0 张 | -2 |
| v1.7 | 5 张 | 0 张 | 0（质量升级） |
| v1.8 | 8 张 | 0 张 | +3（状态显式化） |
| v1.9 | 9 张 | 0 张 | +1（+batch_state） |
| v1.10 | 9 张 | 0 张 | 0 |
| v1.11 | 8 张 | 0 张 | -1（删除 batch_state） |
| **v1.12** | **7 张** | **4 张** | **状态表 -1（收敛脏标记），行为表 +4（表驱动升级）** |

**v1.12 核心运行时表（7 张）：**
1. `latest_tick`（唯一真相源）
2. `node_stocks`（节点股票）
3. `ttl_expiry_queue`（TTL队列）
4. `dirty_stocks`（全局水位线）
5. `node_changes`（三集合：entered/exited/updated）
6. `edge_compare_results`（三态比较结果）
7. `edge_filter_results`（集合/有序列表）

**v1.12 新增行为表（4 张）：**
1. `node_type_table`（节点类型行为表）
2. `operator_table`（比较算子行为表）
3. `combine_table`（集合运算行为表）
4. `propagate_table`（传播方式行为表）

---

## 九、实现路线图（v1.12）

### 阶段一：诚实化 + 概念对齐（P0）

1. **更新所有架构文档**
   - 把"事件驱动"改成"轮询数据 + 脏驱动计算"
   - 把"零延迟"改成"延迟 = tick_interval"
   - 所有文档使用 v1.12 的诚实表述

2. **代码验证与确认**
   - 确认确实是 while 循环 + sleep 轮询（已验证：是的）
   - 确认确实是脏驱动计算（已验证：是的，_dirty_nodes）
   - 确认延迟确实是 tick_interval（已验证：是的）

3. **团队培训与对齐**
   - 诚实：是什么就叫什么，不包装
   - 轮询不是坏事，只是名字叫错了
   - 脏驱动是好东西，变了才算，省时间

### 阶段二：脏标记体系收敛（P0）

1. **合并 node_stock_change 和 node_data_dirty**
   - 新建 node_changes 结构：{entered, exited, updated}
   - 把原来的 node_stock_change.entered → node_changes.entered
   - 把原来的 node_stock_change.exited → node_changes.exited
   - 把原来的 node_data_dirty → node_changes.updated
   - 删除 node_stock_change 和 node_data_dirty

2. **更新所有相关代码**
   - 所有读取 node_stock_change 的地方 → 读 node_changes
   - 所有读取 node_data_dirty 的地方 → 读 node_changes.updated
   - 所有写入的地方同步更新
   - 确保逻辑等价，不引入 bug

3. **测试验证**
   - 确保收敛后行为和之前完全一致
   - 单元测试 + 集成测试
   - 性能不下降（应该持平，甚至略快，因为少一层间接）

### 阶段三：行为表落地（P1）

1. **定义行为表结构**
   - node_type_table：节点类型行为表
   - operator_table：比较算子行为表
   - combine_table：集合运算行为表
   - propagate_table：传播方式行为表

2. **注册 handler 机制**
   - 建立 handler_registry
   - 把现有的处理函数注册进去
   - 启动时一次性注册

3. **逐步替换 if-else 为查表调用**
   - 先从节点类型开始：if node_type == XX → 查 node_type_table
   - 再到比较算子：if op == XX → 查 operator_table
   - 再到集合运算、传播方式
   - 逐步替换，不一次性全改（降低风险）

4. **配置表落地**
   - 把行为表做成 JSON 配置文件
   - 启动时加载，运行时只读
   - 支持热加载（改配置不重启）

### 阶段四：性能模型验证（P1）

1. **性能基准测试**
   - 写 benchmark 脚本
   - 测量每层的实际耗时
   - 5000 只股票，不同指标数，不同边数

2. **验证估算模型**
   - 实际测量值 vs 估算值
   - 修正估算参数
   - 确保估算在合理范围内（±30%）

3. **瓶颈确认**
   - 确认瓶颈确实在指标层
   - 找出最慢的几个指标
   - 针对性优化

### 阶段五：正确性验证（P0）

1. **脏标记收敛验证**
   - 合并后行为等价性测试
   - 边界情况测试（新股、停牌、复牌）
   - 并发/时序测试

2. **表驱动替换验证**
   - 替换 if-else 前后行为一致
   - 回归测试全覆盖
   - 性能不下降

3. **端到端验证**
   - 完整股票池运行测试
   - 和 v1.11 结果对比
   - 确保正确性不受影响

---

## 十、统计总结（v1.11 → v1.12）

### 10.1 概念数量变化

| 统计项 | v1.11 | v1.12 | 变化 |
|--------|------|-------|------|
| 核心运行时表 | 8 张 | **7 张** | **-1（收敛脏标记）** |
| 行为表 | 0 张 | **4 张** | **+4（表驱动升级）** |
| filter 层数 | 3 层 | 3 层 | 不变 |
| 脏来源种类 | 3 种（entered/exited/data_dirty） | 3 种（entered/exited/updated） | 不变，但统一在 node_changes 里 |
| 显式状态表层数 | 3 层 | 3 层 | 不变 |
| 架构模型 | "事件驱动"（包装） | **"轮询 + 脏驱动"（诚实）** | 根本性诚实化 |
| 延迟模型 | "零延迟"（误导） | **延迟 = tick_interval** | 诚实化 |
| 性能模型 | 拍脑袋"毫秒级" | **分层估算，有依据** | 落地化 |
| 表驱动程度 | 状态表（存数据） | **状态表 + 行为表（定义逻辑）** | 升级到真正的表驱动 |

### 10.2 为什么是 v1.12？

**v1.12 是"诚实 + 落地 + 升级"的版本：**

1. **诚实**：停止"事件驱动""零延迟"的包装，承认是"轮询数据 + 脏驱动计算"
2. **落地**：性能模型从拍脑袋到分层估算，基于真实代码，有具体数字
3. **升级**：表驱动从"状态表"升级到"行为表"，逻辑在表里，引擎只查表

```
演进路径：
  v1.5 ~ v1.6：概念精简阶段（从多到少，先做对）
  v1.7：性能优化阶段（股票级水位线，增量计算）
  v1.8：状态显式化阶段（三层状态表，每层结果都有表）
  v1.9：架构完善阶段（一致性 + 事件驱动 + 生命周期）
  v1.10：深度澄清阶段（并发性能 + 批次定位 + 三态传播）
  v1.11：根本性纠错（删除时间批次化，零延迟事件模型）
  v1.12：诚实 + 落地 + 升级（轮询+脏驱动/性能落地/行为表）
  v2.0：完整稳定版（所有功能完善，文档齐全）
```

**v1.12 解决的三个核心问题：**
1. **诚实问题**：停止包装，是什么就叫什么——轮询数据 + 脏驱动计算
2. **性能问题**：从拍脑袋到落地——分层估算，有依据，真实场景量化
3. **表驱动问题**：从状态表到行为表——逻辑在表里，引擎只查表调用

诚实比包装重要，落地比拍脑袋重要，真正的表驱动比"用表存数据"重要。

### 10.3 一句话总结

**v1.12：诚实架构（轮询数据 + 脏驱动计算，不再包装成事件驱动）+ 性能落地（分层估算，有依据，不拍脑袋）+ 表驱动升级（从状态表到行为表，逻辑在表里）+ 脏标记收敛（node_changes 三集合统一）。**
