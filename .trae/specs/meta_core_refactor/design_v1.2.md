# v1.2 扇入去重 + 增量过滤决策 + 依赖注入

## 0. 设计原则与约束

**现状**：v1.1 设计评审 78.4 分，及格但有 3 个 P0 问题需解决。

**v1.2 目标**：解决三大 P0 问题，明确关键架构细节，为开发扫清障碍。

**设计原则（继承 v1.1）**：
- 继承层次不超过 3 层
- 每个类职责单一
- **真·事件驱动**：事件队列是核心，变化驱动计算
- 数据不变则不计算
- 拓扑（连接关系）与执行顺序（行为次序）严格分离
- **等价性优先**：从设计阶段就考虑怎么验证与旧引擎等价
- **可观测性内置**：日志、指标、调试工具是一等公民

**v1.2 新增原则**：
- **正确性优先于性能**：先做对，再做快
- **依赖显式化**：不使用全局单例，依赖通过构造函数或上下文显式传递
- **语义明确**：每个标记、每个状态的含义必须精确，不能模糊

---

## 1. 核心类图（v1.2 更新）

### 1.1 Node 类体系（v1.2 调整：移除 _event_queue）

```
Node (基类)
├── SourceNode (源节点)
│   ├── MarketSourceNode (市场源/备选池)
│   └── CandidatePoolNode (候选池)
├── StatePoolNode (状态池)
└── ConditionNode (条件节点)
```

#### Node 基类

**职责**：所有节点的抽象基类，封装节点身份、状态、脏标记管理。
**v1.2 变更**：移除 `_event_queue` 属性，事件发布通过 EngineContext 传递。

**属性**：
| 属性 | 类型 | 说明 |
|------|------|------|
| `node_id` | str | 节点唯一 ID |
| `node_type` | str | 节点类型 |
| `name` | str | 节点名称 |
| `params` | dict | 节点参数 |
| `stocks` | set[str] | 节点内股票代码集合（内部用 set，输出转 list） |
| `snapshot` | frozenset | 股票代码快照，用于变更检测 |
| `version` | int | 节点数据版本号（每次变化 +1） |
| `is_dirty` | bool | 脏标记：本 tick 内是否有变化待处理 |
| `data_ts` | float | 数据时间戳 |
| `in_edges` | list[Edge] | 入边列表 |
| `out_edges` | list[Edge] | 出边列表 |

**v1.2 新增：version 版本号**
- 每次节点股票集变化时 version + 1
- 用于事件去重和过时事件检测
- 单调递增，永不回退

**方法**：
| 方法 | 说明 |
|------|------|
| `update_stocks(new_stocks, context)` | 更新股票集，变化时返回变更集（added/removed），version+1，置脏 |
| `mark_dirty()` | 标记节点为脏 |
| `clear_dirty()` | 清除脏标记 |
| `add_in_edge(edge)` / `add_out_edge(edge)` | 添加入/出边 |
| `get_stock_codes()` | 返回股票代码集合（frozenset） |
| `snapshot_stocks()` | 生成股票快照并更新 snapshot |
| `apply_changes(added, removed)` | 应用变更集（增量更新） |

**产生的事件**：
- 不直接发布事件，由调用方（Edge.execute 或 EventHandler）根据变更集发布事件

---

### 1.2 Edge 类体系（v1.2 不变）

```
Edge (基类)
├── ConditionalEdge (条件转移边)
└── UnconditionalEdge (无条件转移边)

TimingGate (组合组件，被 Edge 持有)
FilterEvaluator (组合组件，被 ConditionalEdge 持有)
```

> **v1.2 说明**：Edge 类体系结构不变，但事件发布方式改变（通过 context 而非直接引用）。

---

### 1.3 Event 类体系（v1.2 增强：NodeStockChangedEvent 增加版本号）

```
Event (基类)
├── TimerEvent (时间类事件)
│   └── TimerTickEvent (时钟 tick)
├── DataEvent (数据类事件)
│   └── DataUpdatedEvent (行情数据更新)
├── NodeEvent (节点类事件)
│   └── NodeStockChangedEvent (节点股票变化)
├── EdgeEvent (边类事件)
│   └── EdgeExecuteEvent (边执行请求)
├── PoolEvent (池事件)
│   └── PoolChangedEvent (池变化，带 direction)
├── AlertEvent (告警事件)
├── SignalEvent (信号事件)
└── SystemEvent (系统类事件)
    ├── PoolStartEvent (池启动)
    └── PoolStopEvent (池停止)
```

#### NodeStockChangedEvent（v1.2 增强）

**新增字段**：
| 属性 | 类型 | 说明 |
|------|------|------|
| `node_id` | str | 节点 ID |
| `version` | int | 节点变化后的版本号 |
| `added` | frozenset | 新增的股票代码 |
| `removed` | frozenset | 移除的股票代码 |
| `timestamp` | float | 事件时间戳 |
| `source` | str | 事件源标识 |
| `priority` | int | 事件优先级 |

**v1.2 变化**：
- 事件携带具体的变更集（added/removed），而不仅是"变化了"的通知
- 携带版本号，用于去重和过时检测

---

### 1.4 EventQueue（v1.2 增强：Node 级去重）

**职责**：事件驱动的核心。优先级队列，按优先级 + 执行顺序 + 入队时间排序。
**v1.2 变更**：新增 `_pending_node_changes` 集合，用于 NodeStockChangedEvent 去重。

**属性**：
| 属性 | 类型 | 说明 |
|------|------|------|
| `_queue` | heapq | 优先级队列（最小堆） |
| `_counter` | int | 同优先级的入队顺序计数器（保证 FIFO） |
| `_pending_edges` | set[str] | 待执行的边 ID 集合（去重） |
| `_pending_node_changes` | dict[str, int] | 待处理的节点变化 {node_id: version}（去重） |

**v1.2 新增：_pending_node_changes**
- key: node_id
- value: 待处理的版本号
- 入队时检查：如果该节点已有更高或相等版本的事件待处理，则跳过
- 事件处理完后从 dict 中移除

**方法**：
| 方法 | 说明 |
|------|------|
| `enqueue(event)` | 事件入队（通用） |
| `dequeue()` | 出队最高优先级事件 |
| `is_empty()` | 队列是否为空 |
| `size()` | 队列大小 |
| `enqueue_edge_execute(edge_id, reason)` | 边执行请求入队（自动去重） |
| `enqueue_node_changed(node_id, version, added, removed, source)` | 节点变化事件入队（自动去重 + 版本合并） |
| `clear()` | 清空队列 |
| `mark_node_processed(node_id)` | 标记节点变化已处理（从 pending 中移除） |

**去重规则（v1.2 新增）**：

**EdgeExecuteEvent 去重**（同 v1.1）：
- 同一条边在队列中最多有一个待执行事件
- 已在队列中的边，重复入队请求直接忽略

**NodeStockChangedEvent 去重**（v1.2 新增）：
- 同一个节点在队列中最多有一个待处理事件
- 入队时检查：
  - 如果该节点没有 pending 事件 → 入队，记录 version
  - 如果该节点已有 pending 事件，且新版本 > 已记录版本 → **合并**：更新队列中事件的 added/removed/version（合并变更集）
  - 如果该节点已有 pending 事件，且新版本 <= 已记录版本 → 跳过（过时事件）

---

### 1.5 EngineContext（v1.2 增强：增加 EventQueue 引用）

**职责**：封装引擎运行时的全局依赖，避免到处传递参数。
**v1.2 变更**：新增 `event_queue` 引用，作为事件发布的统一入口。

**属性**：
| 属性 | 类型 | 说明 |
|------|------|------|
| `now_ts` | float | 当前时间戳 |
| `bar_time` | datetime | 当前 K 线时间 |
| `bar_data` | dict | 当前行情数据 |
| `data_dirty` | bool | 数据是否脏（本 tick 是否有新数据） |
| `formula_router` | FormulaRouter | 公式路由器 |
| `data_query` | DataQuery | 数据查询 |
| `config_store` | ConfigStore | 配置存储 |
| `event_queue` | EventQueue | 事件队列（v1.2 新增） |
| `metrics` | EngineMetrics | 性能指标（v1.2 新增） |

> **设计决策**：EngineContext 持有 EventQueue 引用，所有组件通过 context 发布事件。
> 详见第 10 章"事件发布的依赖传递方案"。

---

### 1.6 类关系图（v1.2 更新）

```
                    ┌──────────────────┐
                    │ EventDrivenEngine│
                    │  (协调者)        │
                    └────────┬─────────┘
                             │ 组合
           ┌─────────────────┼─────────────────┐
           ▼                 ▼                 ▼
     ┌──────────┐     ┌──────────┐     ┌──────────┐
     │EventLoop │     │ Topology │     │  Metrics │
     └────┬─────┘     └────┬─────┘     └──────────┘
          │                │ 引用
          ▼                ▼
     ┌──────────┐    ┌──────────┐
     │EventQueue│    │   Node   │◄─────────────────┐
     │ (去重增强)│    │ (基类)   │                  │
     └──────────┘    └────┬─────┘            ┌───┴────┐
                            │ 继承             │ source │
                      ┌─────┴──────┐           │ target │
                      ▼            ▼           └───┬────┘
                ┌────────┐ ┌────────────┐          │
                │Source  │ │StatePool   │    ┌─────┴──────┐
                │Node    │ │Node        │    │   Edge     │
                └────────┘ └────────────┘    │  (基类)     │
                                              └─────┬──────┘
                                                    │ 组合
                                       ┌────────────┼────────────┐
                                       ▼            ▼            ▼
                                 ┌──────────┐ ┌──────────┐ ┌──────────┐
                                 │TimingGate│ │FilterEval│ │Uncond-   │
                                 │          │ │   uator  │ │itional   │
                                 └──────────┘ └──────────┘ └──────────┘

     ┌──────────────┐
     │EngineContext │  ← 上下文对象，持有 event_queue / formula_router / ...
     └──────────────┘
           ▲
           │ 方法参数传递
           │
  Node / Edge / FilterEvaluator / TimingGate  通过方法参数接收 context
```

**v1.2 关键变化**：
1. Node 不再直接持有 EventQueue 引用
2. EngineContext 持有 EventQueue 引用
3. 所有组件通过方法参数接收 context，通过 context.event_queue 发布事件
4. EventQueue 增加 NodeStockChangedEvent 去重机制

---

## 2. 事件流设计（v1.2 更新：扇入去重 + 版本号）

### 2.1 核心思想：事件队列是心脏（同 v1.1）

> 详见 v1.1 §2.1，本节不重复。

---

### 2.2 事件类型与处理逻辑（v1.2 更新）

#### 事件类型总览

| 事件类型 | 生产者 | 消费者 | 入队时机 | 处理动作 |
|---------|--------|--------|---------|---------|
| `TimerTickEvent` | Engine.on_tick() | EventLoop | 每个时钟 tick | 推进时间；检查所有 TimingGate 的状态变化；触发 TTL 检查；触发源节点刷新 |
| `DataUpdatedEvent` | Engine.on_data() | EventLoop | 行情数据到达 | 更新全局 bar_data；置 data_dirty=True；把所有依赖行情的条件边加入执行队列 |
| `EdgeExecuteEvent` | NodeStockChangedHandler / DataUpdatedHandler / TimerTickHandler | EventLoop | 边需要执行时 | 执行边的过滤/流转逻辑；目标节点变化则调用 enqueue_node_changed |
| `NodeStockChangedEvent` | EventQueue.enqueue_node_changed() | EventLoop | 节点股票集变化时 | 把该节点的所有出边加入执行队列；清除脏标记；从 pending_node_changes 移除 |
| `PoolChangedEvent` | StatePoolNode（由 EventHandler 调用） | EventQueue 消费者 | 股票入池/出池时 | 外部消费（UI更新、信号生成等），不参与内部级联 |
| `AlertEvent` | StatePoolNode（由 EventHandler 调用） | EventQueue 消费者 | 告警触发时 | 外部消费（声音、弹窗、保存板块等） |
| `SignalEvent` | StatePoolNode（目标池，由 EventHandler 调用） | EventQueue 消费者 | 目标池入/出时 | 外部消费（交易信号） |

**v1.2 变化**：
- NodeStockChangedEvent 不再由 Node 直接发布，而是通过 EventQueue.enqueue_node_changed() 发布
- 事件携带 added/removed 变更集和 version 版本号
- 去重在 EventQueue 层面完成

---

### 2.3 核心事件流：扇入去重详解（v1.2 新增）

#### 问题场景：扇入（Fan-in）

```
拓扑：
  A → edge1 → B
  A → edge2 → B
  B → edge3 → C

A 节点变化，edge1 和 edge2 都会执行，都可能导致 B 变化。
```

**v1.1 的问题**：
- edge1 执行 → B 变化 → NodeChanged(B) 入队
- edge2 执行 → B 再变化 → 又一个 NodeChanged(B) 入队
- 队列中有两个 NodeChanged(B)，重复处理

**v1.2 的解决方案**：版本号 + 去重合并

#### 完整扇入事件流示例

```
初始状态：
  A.stocks = {1, 2, 3}, version=1, is_dirty=False
  B.stocks = {}, version=1, is_dirty=False
  C.stocks = {}, version=1, is_dirty=False

事件 0：NodeStockChangedEvent(A, version=2, added={1,2,3}, removed={})
  ↓
Handler: on_node_stock_changed(event)
  ├─ 1. 找出 A 的所有出边：edge1, edge2
  ├─ 2. 按 execution_order 排序
  ├─ 3. enqueue_edge_execute(edge1)
  ├─ 4. enqueue_edge_execute(edge2)
  ├─ 5. A.clear_dirty()
  └─ 6. event_queue.mark_node_processed(A.node_id)

队列：[EdgeExecute(edge1), EdgeExecute(edge2)]
  ↓
事件 1：EdgeExecuteEvent(edge1)
  ↓
Handler: on_edge_execute(event)
  ├─ 1. edge1.execute(context)
  │   ├─ 过滤计算：{1, 2, 3} → 通过 {1, 2}
  │   ├─ B.apply_changes(added={1,2}, removed={})
  │   │   ├─ B.stocks = {1, 2}
  │   │   ├─ B.version += 1 → version=2
  │   │   └─ B.mark_dirty()
  │   └─ 返回变更集：added={1,2}, removed={}
  │
  └─ 2. 有变化 → event_queue.enqueue_node_changed(
              B.node_id, version=2,
              added={1,2}, removed={},
              source="edge1")
       ├─ 检查 _pending_node_changes：B 不在
       ├─ 加入 pending：B → version=2
       └─ NodeStockChangedEvent(B, v2) 入队

队列：[EdgeExecute(edge2), NodeChanged(B, v2)]
  ↓
事件 2：EdgeExecuteEvent(edge2)  ← EdgeExecute 优先级更高，先处理
  ↓
Handler: on_edge_execute(event)
  ├─ 1. edge2.execute(context)
  │   ├─ 过滤计算：{1, 2, 3} → 通过 {2, 3}
  │   ├─ B.apply_changes(added={3}, removed={})
  │   │   ├─ B.stocks = {1, 2, 3}
  │   │   ├─ B.version += 1 → version=3
  │   │   └─ B.is_dirty 已经是 True，不变
  │   └─ 返回变更集：added={3}, removed={}
  │
  └─ 2. 有变化 → event_queue.enqueue_node_changed(
              B.node_id, version=3,
              added={3}, removed={},
              source="edge2")
       ├─ 检查 _pending_node_changes：B → version=2
       ├─ 新版本 3 > 已记录版本 2 → 合并！
       ├─ 合并变更集：
       │   added = {1,2} ∪ {3} = {1,2,3}
       │   removed = {} ∪ {} = {}
       ├─ 更新 pending：B → version=3
       └─ 更新队列中的事件（或标记旧事件为无效）

队列：[NodeChanged(B, v3, added={1,2,3}, removed={})]
       （v2 的事件被合并/替换为 v3）
  ↓
事件 3：NodeStockChangedEvent(B, v3)
  ↓
Handler: on_node_stock_changed(event)
  ├─ 1. 验证版本：event.version == B.version? → 是（v3 == v3）
  ├─ 2. 找出 B 的所有出边：edge3
  ├─ 3. enqueue_edge_execute(edge3)
  ├─ 4. B.clear_dirty()
  └─ 5. event_queue.mark_node_processed(B.node_id)

队列：[EdgeExecute(edge3)]
  ↓
事件 4：EdgeExecuteEvent(edge3)
  ↓
Handler: on_edge_execute(event)
  ├─ 1. edge3.execute(context)
  │   ├─ 流转：{1, 2, 3} → C
  │   ├─ C.apply_changes(added={1,2,3}, removed={})
  │   │   └─ C.version = 2, C.is_dirty = True
  │   └─ 返回变更集：added={1,2,3}, removed={}
  │
  └─ 2. 有变化 → enqueue_node_changed(C.node_id, v2, ...)

队列：[NodeChanged(C, v2)]
  ↓
事件 5：NodeStockChangedEvent(C, v2)
  ↓
Handler: on_node_stock_changed(event)
  ├─ 1. 验证版本：v2 == C.version? → 是
  ├─ 2. C 没有出边
  ├─ 3. C.clear_dirty()
  └─ 4. mark_node_processed(C.node_id)

队列为空。
```

**关键点**：
1. **B 节点变化了两次，但只产生一个 NodeStockChangedEvent**（v3，合并了两次变更）
2. **版本号单调递增**：每次变化 version + 1，不会回退
3. **去重在入队时完成**：EventQueue.enqueue_node_changed() 负责去重和合并
4. **处理时验证版本**：防止处理过时事件（虽然单线程下不会发生，但作为防御性编程）

---

### 2.4 三种去重方案对比与选择（v1.2 新增）

#### 方案 A：事件合并（同 tick 内同一节点的多个 ChangedEvent 合并成一个）

**核心思想**：
- EventQueue 维护 `_pending_node_changes` 字典
- 入队时，如果该节点已有 pending 事件，合并变更集，更新版本号
- 队列中同一个节点最多只有一个待处理事件

**优点**：
- ✅ 减少事件数量，性能更好
- ✅ 变更集合并后，下游能看到完整变化
- ✅ 逻辑清晰，易于理解

**缺点**：
- ❌ 实现稍复杂（需要合并变更集）
- ❌ 如果队列中已有该事件，需要找到并更新它（堆结构不支持随机修改）

**复杂度**：中
**正确性保证**：高（只要合并逻辑正确，结果与多次处理等价）

---

#### 方案 B：脏标记去重（节点有一个 dirty flag，重复标记不重复执行）

**核心思想**：
- Node.is_dirty 标记节点是否有变化待处理
- 入队前检查：如果节点已经是 dirty，说明已有事件待处理，不再重复入队
- 事件处理完后清除 dirty 标记

**优点**：
- ✅ 实现简单（一个 bool 标记）
- ✅ 性能好（O(1) 检查）

**缺点**：
- ❌ 事件不携带具体变更集，下游需要自己 diff
- ❌ 中间状态丢失（多次变化合并成一次"变化了"的通知）
- ❌ 脏标记语义模糊（是"有变化待处理"还是"数据是脏的"？）

**复杂度**：低
**正确性保证**：中（能去重，但丢失了变更细节）

---

#### 方案 C：时间戳版本号（每个节点有版本号，处理过时事件直接跳过）

**核心思想**：
- 每个 Node 有 version，每次变化 +1
- NodeStockChangedEvent 携带 version
- 处理事件时，先检查 event.version == node.version
- 如果 event.version < node.version → 过时事件，跳过

**优点**：
- ✅ 能处理乱序事件（并发场景下有用）
- ✅ 版本号单调递增，语义清晰
- ✅ 可以作为防御性编程，防止处理过期数据

**缺点**：
- ❌ 不去重（事件还是会入队，只是处理时跳过）
- ❌ 队列中可能有大量过时事件，浪费空间和处理时间
- ❌ 单线程场景下意义不大（因为事件不会乱序）

**复杂度**：低
**正确性保证**：高（但不去重）

---

#### v1.2 的选择：方案 A + 方案 C 组合

**为什么组合？**
- **方案 A 解决"去重"问题**：减少事件数量，合并变更集
- **方案 C 解决"正确性"问题**：版本号作为防御性检查，防止处理过时数据
- 两者互补，组合后既高效又正确

**具体机制**：

**入队时（方案 A 的去重合并）**：
```
def enqueue_node_changed(node_id, version, added, removed, source):
    if node_id not in _pending_node_changes:
        # 没有 pending 事件，直接入队
        event = NodeStockChangedEvent(node_id, version, added, removed, ...)
        _heapq.heappush(_queue, event)
        _pending_node_changes[node_id] = version
    else:
        existing_version = _pending_node_changes[node_id]
        if version > existing_version:
            # 新版本更新，需要合并
            # 注意：堆结构不支持随机修改，所以用"墓碑标记"策略
            # 旧事件保留在堆中，但标记为 invalid
            # 新事件入队，pending 中记录最新版本
            event = NodeStockChangedEvent(node_id, version, added, removed, ...)
            event.is_valid = True
            _heapq.heappush(_queue, event)
            _pending_node_changes[node_id] = version
            # 旧事件的 added/removed 合并到新事件中？
            # 不，因为新事件的 added/removed 是相对于旧版本的增量
            # 等等，这里有问题...
```

**等等，这里需要更仔细地想清楚变更集合并的问题。**

如果 edge1 执行后 B 从 {} → {1,2}，变更集是 added={1,2}, removed={}
然后 edge2 执行后 B 从 {1,2} → {1,2,3}，变更集是 added={3}, removed={}

那么合并后的变更集应该是 added={1,2,3}, removed={}，对吗？

是的。但问题是：**EventQueue 怎么知道旧事件的变更集？**

如果旧事件已经在堆中，我们没法直接拿到它的 added/removed 来合并。

**修正方案：墓碑标记 + 版本号验证**

更简单也更正确的方案是：
1. 旧事件不修改，保留在堆中
2. 新事件直接入队（携带最新的版本号）
3. 出队时，检查事件的 version 是否等于 _pending_node_changes[node_id]
   - 如果相等 → 这是最新的事件，处理它
   - 如果不等 → 这是过时事件，跳过（墓碑）
4. 处理完后，从 _pending_node_changes 中移除该节点

这样：
- 不需要合并变更集（因为最新的事件携带的是完整的变更集吗？不，是增量的）
- 等等，还是有问题。如果每次事件只携带相对于上一个版本的增量，那么跳过旧事件会导致变更丢失。

**哦，我理解错了。变更集应该怎么设计？**

两种选择：
1. **增量变更集**：每个事件只携带本次变化的 added/removed
   - 优点：事件小
   - 缺点：不能跳过任何事件，否则丢失变更
2. **全量快照**：每个事件携带变化后的完整股票集
   - 优点：可以安全跳过旧事件
   - 缺点：事件大（股票多时）

对于股票池系统，股票代码集合通常不大（几十到几百只），全量快照是可以接受的。

**但更好的设计是：NodeStockChangedEvent 携带的是"本次变化的增量"，但去重合并是在 EventQueue 中做的，合并后的事件携带"从上次处理到现在的总增量"。**

让我重新设计：

**最终方案：墓碑标记 + pending 中维护总变更集**

```
_pending_node_changes: dict[str, PendingInfo]
where PendingInfo = {
    version: int,           # 最新版本号
    added: set[str],        # 从上次处理到现在的总新增
    removed: set[str],      # 从上次处理到现在的总移除
    has_event_in_queue: bool  # 是否已经有事件在队列中
}
```

**入队逻辑**：
```python
def enqueue_node_changed(node_id, version, added, removed, source):
    if node_id not in _pending_node_changes:
        # 没有 pending 信息，新建
        info = PendingInfo(
            version=version,
            added=set(added),
            removed=set(removed),
            has_event_in_queue=True
        )
        _pending_node_changes[node_id] = info
        event = NodeStockChangedEvent(node_id, version, added, removed, ...)
        _heapq.heappush(_queue, event)
    else:
        info = _pending_node_changes[node_id]
        if version <= info.version:
            # 过时事件，忽略
            return
        # 更新版本和变更集
        info.version = version
        # 合并 added/removed（注意 A 先删后加 = 加，先加后删 = 不变）
        new_added = set(added)
        new_removed = set(removed)
        # 合并：
        # 之前新增的 - 现在移除的 + 现在新增的 - 之前移除的
        info.added = (info.added - new_removed) | (new_added - info.removed)
        info.removed = (info.removed - new_added) | (new_removed - info.added)
        # 等等，这个合并逻辑不对，让我重新想...
```

**变更集合并的正确逻辑**：

假设：
- 第一次变化：added={1,2}, removed={} → 状态从 S0 → S1
- 第二次变化：added={3}, removed={1} → 状态从 S1 → S2

那么从 S0 → S2 的总变更是：
- added = {2, 3} （1 先加后删，抵消了）
- removed = {}

验证：
- S0 = {}
- S1 = S0 ∪ {1,2} - {} = {1,2}
- S2 = S1 ∪ {3} - {1} = {2,3}
- S0 → S2：added = {2,3}, removed = {} ✓

合并公式：
```
总 added = (added1 - removed2) ∪ (added2 - removed1)
总 removed = (removed1 - added2) ∪ (removed2 - added1)
```

对吗？让我验证一下：
- added1={1,2}, removed1={}
- added2={3}, removed2={1}

总 added = ({1,2} - {1}) ∪ ({3} - {}) = {2} ∪ {3} = {2,3} ✓
总 removed = ({} - {3}) ∪ ({1} - {1,2}) = {} ∪ {} = {} ✓

正确！

**但是，这个合并逻辑有一个前提：两次变化是连续的，中间没有被处理过。**
在我们的场景中，pending 中的变更就是"从上次处理到现在的总变更"，所以这个前提成立。

**好，现在方案完整了：**

1. EventQueue 维护 `_pending_node_changes: dict[str, PendingInfo]`
2. 每次调用 enqueue_node_changed() 时：
   - 如果节点不在 pending 中 → 创建 PendingInfo，事件入队
   - 如果节点已在 pending 中，且新版本 > 旧版本 → 合并变更集，更新版本号
     - 如果已经有事件在队列中 → 不重复入队（旧事件出队时会拿到最新的 pending 信息）
     - 如果没有事件在队列中 → 事件入队，标记 has_event_in_queue=True
3. 事件出队时：
   - 从 pending 中取出最新的 version 和变更集
   - 用最新的信息处理（而不是事件本身携带的信息）
   - 处理完后，标记 has_event_in_queue=False
   - （如果 pending 中 version > 事件 version，说明入队后又有新变化，但没关系，我们用最新的）

等等，这样事件本身携带的 added/removed 就没用了，因为处理时用的是 pending 中的最新信息。
那事件本身还需要携带 added/removed 吗？

不需要了。事件只需要携带 node_id 和 version（用于排序和调试）。
真正的变更集在 pending 中维护，处理时从 pending 取。

**这样设计更简洁也更正确。让我重新整理：**

---

### 2.5 最终去重方案：Pending 合并 + 墓碑标记（v1.2 确定方案）

#### 数据结构

```python
class EventQueue:
    _queue: heapq              # 优先级队列
    _counter: int              # FIFO 计数器
    _pending_edges: set[str]   # 待执行边 ID 集合（去重）
    
    # v1.2 新增：节点变化 pending 信息
    _pending_node_changes: dict[str, NodeChangeInfo]
    
class NodeChangeInfo:
    version: int           # 最新版本号
    added: set[str]        # 从上次处理到现在的总新增
    removed: set[str]      # 从上次处理到现在的总移除
    has_event_in_queue: bool  # 队列中是否已有该节点的事件
```

#### 入队逻辑

```python
def enqueue_node_changed(self, node_id: str, version: int, 
                         added: set[str], removed: set[str], 
                         source: str = ""):
    """
    节点变化事件入队（自动去重 + 合并）
    
    Args:
        node_id: 节点 ID
        version: 变化后的版本号
        added: 本次新增的股票
        removed: 本次移除的股票
        source: 事件源（用于调试）
    """
    if node_id not in self._pending_node_changes:
        # 情况1：没有 pending 信息，新建并入队
        info = NodeChangeInfo(
            version=version,
            added=set(added),
            removed=set(removed),
            has_event_in_queue=True
        )
        self._pending_node_changes[node_id] = info
        event = NodeStockChangedEvent(
            node_id=node_id,
            version=version,
            timestamp=time.time(),
            source=source,
            priority=EVENT_PRIORITY_NODE_STOCK_CHANGED
        )
        self._enqueue(event)
        return
    
    info = self._pending_node_changes[node_id]
    
    if version <= info.version:
        # 情况2：过时事件，忽略
        return
    
    # 情况3：有更新的版本，合并变更集
    new_added = set(added)
    new_removed = set(removed)
    
    # 合并 added/removed
    # 规则：A 先加后删 = 不变，A 先删后加 = 加
    merged_added = (info.added - new_removed) | (new_added - info.removed)
    merged_removed = (info.removed - new_added) | (new_removed - info.added)
    
    info.version = version
    info.added = merged_added
    info.removed = merged_removed
    
    if not info.has_event_in_queue:
        # 队列中没有事件，需要入队
        info.has_event_in_queue = True
        event = NodeStockChangedEvent(
            node_id=node_id,
            version=version,
            timestamp=time.time(),
            source=source,
            priority=EVENT_PRIORITY_NODE_STOCK_CHANGED
        )
        self._enqueue(event)
    # else: 队列中已有事件，不需要重复入队
    # 事件出队时会从 info 中取最新的 added/removed
```

#### 出队与处理逻辑

```python
# EventLoop 中处理 NodeStockChangedEvent
def on_node_stock_changed(event, context, queue):
    node_id = event.node_id
    info = queue._pending_node_changes.get(node_id)
    
    if info is None:
        # 没有 pending 信息（不应该发生，但防御性编程）
        return
    
    # 从 pending 中取最新的变更集（而不是事件本身携带的）
    added = info.added
    removed = info.removed
    version = info.version
    
    # 获取节点对象
    node = context.nodes[node_id]
    
    # 版本号验证（防御性检查）
    assert version == node.version, f"版本不一致：event={version}, node={node.version}"
    
    # 业务逻辑：把下游边加入队列
    for edge in node.out_edges:
        if edge.should_execute(context.now_ts, context.data_dirty):
            queue.enqueue_edge_execute(edge.edge_id)
    
    # 清除脏标记
    node.clear_dirty()
    
    # 标记已处理
    info.has_event_in_queue = False
    
    # 如果 added 和 removed 都为空，说明多次变化后抵消了，可以移除 pending
    # 但通常不会这样，因为 is_dirty 已经标记了
    # 为了简单，保留 pending 信息直到 tick 末统一清理
    # 或者：如果队列中没有更多事件了，可以移除
    # 等等，不对。has_event_in_queue=False 只是说队列中没有事件了，
    # 但之后可能还会有新的变化入队。
    # 所以 pending 信息应该保留到 tick 末统一清理。
    
    # 不对，让我再想想。
    # 如果节点在 tick 内变化了 N 次，每次变化都会调用 enqueue_node_changed
    # 第一次调用：创建 pending，事件入队，has_event_in_queue=True
    # 第二次调用：合并变更集，has_event_in_queue 已经是 True，不重复入队
    # ...
    # 事件出队被处理：has_event_in_queue = False
    # 第 N 次调用（在事件处理之后）：合并变更集，has_event_in_queue=False，所以重新入队
    # 
    # 这样是正确的。因为事件处理完后，如果又有新变化，应该重新触发下游。
    # 
    # 那 pending 信息什么时候清理？
    # tick 结束时，统一清理所有 pending 信息。
    # 因为一个 tick 结束后，所有变化都已经处理完了。
```

**tick 末清理**：
```python
# tick 结束时
def end_of_tick_cleanup(queue):
    queue._pending_node_changes.clear()
    queue._pending_edges.clear()
```

#### 正确性论证

**定理**：在单线程事件循环模型下，Pending 合并方案与"每次变化都产生事件并处理"的方案，最终结果等价。

**证明**：

1. **单次变化**：显然等价（只有一个事件，处理一次）

2. **多次变化，中间没有被处理**：
   - 方案1（每次都处理）：变化1 → 处理 → 下游执行 → 变化2 → 处理 → 下游再执行
   - 方案2（合并处理）：变化1 + 变化2 → 合并 → 处理一次 → 下游执行一次
   
   这两种方案的最终节点状态相同吗？相同。
   下游执行的结果相同吗？这取决于下游边的逻辑。
   
   **关键问题：下游边执行一次 vs 执行多次，结果相同吗？**
   
   对于股票池系统：
   - 边的执行是"过滤 + 流转"
   - 如果源节点从 S0 → S1 → S2，那么：
     - 执行两次：S0→S1 过滤一次，S1→S2 再过滤一次
     - 执行一次：S0→S2 过滤一次
   
   这两种方式的结果相同吗？
   
   **对于无状态的过滤（大多数情况）**：
   - 过滤函数 f(S) 只依赖当前的输入集合 S
   - 那么 f(S2) = f(S0) Δ f(S1) Δ f(S2)？不对...
   - 等等，不是的。让我想清楚。
   
   如果边的逻辑是"把源节点中满足条件的股票加入目标节点"：
   - 方案1（两次执行）：
     - 第一次：f(S1) - f(S0) 加入目标
     - 第二次：f(S2) - f(S1) 加入目标
     - 最终目标增加：f(S1) - f(S0) + f(S2) - f(S1) = f(S2) - f(S0)
   - 方案2（一次执行）：
     - f(S2) - f(S0) 加入目标
     - 最终目标增加：f(S2) - f(S0)
   
   **结果相同！** ✓
   
   对于更复杂的流转逻辑（比如"移动"而非"复制"），可能需要更仔细的论证。
   但在股票池系统中，主要是"条件转移（复制）"，所以合并是安全的。

3. **多次变化，中间被处理了一次**：
   - 变化1 → 处理 → 变化2 → 处理
   - 合并方案也会处理两次（因为第一次处理完后 has_event_in_queue=False，第二次变化会重新入队）
   - 结果相同 ✓

**结论**：Pending 合并方案在股票池系统中是正确的，与逐次处理等价。

---

### 2.6 极端场景处理（v1.2 新增）

#### 场景 1：并发 / 乱序事件

**v1.2 模型**：单线程事件循环，事件不会乱序。
但作为防御性编程，版本号机制仍然有用：
- 如果未来引入多线程，版本号能检测乱序
- 如果有 bug 导致事件顺序错误，版本号能帮助发现

**处理策略**：
- 入队时：version <= info.version 的事件直接忽略（过时）
- 处理时：验证 event.version == node.version（断言）
- 生产环境可以降级为日志警告而非断言

#### 场景 2：变更集抵消（added 和 removed 完全抵消）

极端情况下，多次变化后 added 和 removed 完全抵消（比如先加后删同一只股票）。

**处理策略**：
- 合并后如果 added 和 removed 都为空，且 has_event_in_queue=True：
  - 不做特殊处理，事件还是会出队
  - 处理时发现 added 和 removed 都为空，下游边不会执行（因为节点没有净变化）
  - 有点浪费，但影响不大（这种情况很少见）
- 优化：如果合并后 added 和 removed 都为空，可以标记事件为无效
  - 但实现复杂度增加，v1.2 不做这个优化

#### 场景 3：扇入很大（100 个上游 → 1 个下游）

**性能影响**：
- 100 个上游都变化，会调用 100 次 enqueue_node_changed
- 但只有第一次会入队，后面 99 次只是合并变更集
- 处理时只产生一个 NodeStockChangedEvent，下游边只入队一次

**性能收益**：
- 事件数：从 100 个减少到 1 个
- 下游边执行：从可能重复触发（如果没有去重）到只触发一次
- 对于扇入大的场景，性能提升显著

---

### 2.7 脏标记的精确语义（v1.2 澄清）

之前脏标记的语义比较模糊，现在明确：

**is_dirty 的精确定义**：
> **本 tick 内，节点的股票集是否发生过变化，且该变化尚未被 NodeStockChangedEvent 处理。**

| 操作 | is_dirty 变化 | 说明 |
|------|-------------|------|
| 节点股票变化（update_stocks / apply_changes） | False → True | 发生变化，标记为脏 |
| 节点再次变化（已经是脏的） | 保持 True | 已经是脏的，不重复标记 |
| NodeStockChangedEvent 处理开始 | 保持 True | 处理中还是脏的 |
| NodeStockChangedEvent 处理结束 | True → False | 处理完了，清除脏标记 |
| tick 结束 | 全部重置为 False | tick 末统一清理 |

**与 version 的关系**：
- version 是单调递增的计数器，每次变化 +1
- is_dirty 是"是否有未处理变化"的标记
- 两者是相关但不同的概念：
  - version 回答"数据是哪个版本的"
  - is_dirty 回答"这个版本的数据是否被处理过"

---

## 3. 核心流程（v1.2 更新：去重 + 上下文传递）

> 大部分流程同 v1.1，本节只列出有变化的部分。

### 3.1 完整事件循环伪代码（v1.2 更新）

```python
class EventLoop:
    def __init__(self, engine_context):
        self.queue = EventQueue()
        self.context = engine_context
        self.context.event_queue = self.queue  # 把队列放入上下文
        self.handlers = {}
        self.max_iterations = 10000
        self.iteration_count = 0

    def register_handler(self, event_type, handler):
        self.handlers[event_type] = handler

    def run_until_empty(self):
        self.iteration_count = 0
        while not self.queue.is_empty():
            if self.iteration_count >= self.max_iterations:
                raise EventLoopError(...)

            event = self.queue.dequeue()
            handler = self.handlers.get(event.event_type)
            if handler:
                try:
                    handler(event, self.context)  # context 传给 handler
                except Exception as e:
                    logger.error(...)

            self.iteration_count += 1
    
    def end_of_tick(self):
        """tick 结束时的清理工作"""
        self.queue.clear_pending()
        self.context.data_dirty = False
        # 其他 tick 末清理...
```

**v1.2 变化**：
- 把 event_queue 放入 engine_context
- handler 只接收 event 和 context（queue 通过 context 获取）

---

### 3.2 边执行伪代码（v1.2 更新：通过 context 发布事件）

```python
class ConditionalEdge(Edge):
    def execute(self, context):
        """
        执行条件边的过滤和流转
        
        Args:
            context: EngineContext（包含 event_queue, formula_router 等）
            
        Returns:
            tuple: (changed, added, removed)  是否变化及变更集
        """
        # 1. 时机检查（should_execute 已经检查过了，这里可以跳过）
        
        # 2. 过滤计算
        source_stocks = self.source_node.get_stock_codes()
        passed_stocks = self.filter_evaluator.evaluate(source_stocks, context)
        
        # 3. 计算变更集
        current_target = self.target_node.get_stock_codes()
        added = passed_stocks - current_target
        removed = current_target & (source_stocks - passed_stocks)
        # 等等，流转逻辑可能更复杂（策略动作不同）
        # 这里简化为"目标节点 = 源节点中通过过滤的"
        
        if not added and not removed:
            return False, set(), set()
        
        # 4. 应用变更到目标节点
        self.target_node.apply_changes(added, removed)
        
        # 5. 通过 context 发布节点变化事件
        context.event_queue.enqueue_node_changed(
            node_id=self.target_node.node_id,
            version=self.target_node.version,
            added=added,
            removed=removed,
            source=f"edge:{self.edge_id}"
        )
        
        return True, added, removed
```

**v1.2 变化**：
- 事件通过 context.event_queue 发布，而不是 Node 自己发布
- Node.update_stocks 不再发布事件，只更新数据和版本号

---

## 4. 数据驱动增量计算（v1.2 重大更新：增量过滤 go/no-go 决策）

### 4.1 决策结论：v1.x 全量计算，v2.0 再优化增量

**Go/No-Go 决策：No-Go for v1.x**

v1.x 版本**不做增量过滤**，只保留 L1~L4 四级跳过机制。
增量过滤推迟到 v2.0，在功能正确、等价性验证充分的基础上再做优化。

**原因**：
1. **正确性边界复杂**：增量过滤的适用条件判断复杂，容易在边界情况下出错
2. **等价性验证难度高**：如果做增量过滤，等价性测试需要验证"增量结果 = 全量结果"，增加测试复杂度
3. **v1.x 目标是正确性**：先把功能做对，性能优化是下一步的事
4. **L1~L4 已经够用**：数据层、节点层、边层、公式层四级跳过，已经能避免大部分不必要的计算
5. **风险收益比低**：增量过滤能带来性能提升，但引入的正确性风险更大

---

### 4.2 增量过滤的深入分析（为 v2.0 准备）

虽然 v1.x 不做，但我们先把问题想清楚，为 v2.0 打好基础。

#### 增量过滤的适用边界

**按公式类型分类**：

| 公式类型 | nset | 可以增量吗？ | 原因 | 示例 |
|---------|------|-------------|------|------|
| 公式型 | 0/1/2 | ⚠️ 部分可以 | 取决于公式是否逐股票独立 | CLOSE > 10（可以）；涨幅前10（不可以） |
| 标量型 | 3/4 | ⚠️ 部分可以 | 取决于是否引用聚合属性 | 收盘价（可以）；池内平均价（不可以） |
| 集合运算型 | 5 | ❌ 大部分不行 | 集合运算通常需要完整集合 | 交集、并集、差集 |

**更细粒度的判断标准**：

**可以增量的条件（必须全部满足）**：
1. **逐股票独立性**：每只股票是否通过过滤，只依赖该股票自身的数据，不依赖其他股票
2. **无状态性**：过滤函数没有内部状态（不依赖历史值）
3. **无聚合引用**：不引用"池内股票数"、"平均价"等聚合属性
4. **单调可加性**：新增股票只可能增加结果，移除股票只可能减少结果（或反之）

**不可以增量的情况**：
1. **排名型**："涨幅前 10"、"成交量前 20" — 一只股票变化可能影响所有股票的排名
2. **聚合引用型**："价格高于池内平均价" — 池内股票变化会影响平均值
3. **状态依赖型**：公式有内部状态（比如历史值、累计值）
4. **集合运算型**："两个池的交集" — 需要两个完整的集合才能计算

#### 正确性论证（在适用条件下）

**定理**：如果过滤函数 f 满足逐股票独立性和无状态性，那么：
```
f(S1 ∪ S2) - f(S1) = f(S2) - f(S1 ∩ S2)
```
即：增量计算的结果与全量计算的结果一致。

**证明**：
- 因为 f 是逐股票独立的，可以定义 f(s) 为单只股票 s 是否通过过滤
- 那么 f(S) = {s ∈ S | f(s) 为真}
- f(S1 ∪ S2) = {s ∈ S1 ∪ S2 | f(s)} = {s ∈ S1 | f(s)} ∪ {s ∈ S2 | f(s)} = f(S1) ∪ f(S2)
- f(S1 ∪ S2) - f(S1) = (f(S1) ∪ f(S2)) - f(S1) = f(S2) - f(S1) = f(S2) - f(S1 ∩ S2)
- （因为 f(S1 ∩ S2) = f(S1) ∩ f(S2)，所以 f(S2) - f(S1) = f(S2) - f(S1 ∩ S2)）

**证毕**。✓

**增量更新规则**：
- 新增股票集合 A：对 A 中的每只股票计算 f(s)，通过的加入结果
- 移除股票集合 R：从结果中移除 R 中的股票
- 不变的股票：不需要重新计算

**结果**：
```
f(S Δ A Δ R) = (f(S) ∪ f(A)) - f(R)
```
其中 Δ 是对称差，S Δ A Δ R = (S - R) ∪ A

---

### 4.3 四级跳过机制（v1.2 确认：保留 L1~L4，移除 L5）

| 级别 | 检查点 | 跳过条件 | 节省的计算 |
|------|--------|---------|-----------|
| L1 | 数据层 | `data_hash == last_data_hash` | 全部计算（零开销） |
| L2 | 节点层 | `not node.is_dirty` | 该节点的所有出边计算 |
| L3 | 边层 | `not edge.should_execute()` | 该边的过滤+传播 |
| L4 | 公式层 | `filter_cache` 命中 | 公式计算本身 |

**v1.2 变更**：移除 L5 增量过滤，推迟到 v2.0。

---

### 4.4 公式结果缓存策略（v1.2 不变）

> 同 v1.1 §4.4，本节不重复。

---

## 5. 执行顺序与拓扑分离（v1.2 不变）

> 同 v1.1 第 5 章，本节不重复。

---

## 6. 表驱动配置（v1.2 不变）

> 同 v1.1 第 6 章，本节不重复。

---

## 7. 与现有系统的关系（v1.2 不变）

> 同 v1.1 第 7 章，本节不重复。

---

## 8. 等价性验证框架（v1.2 更新：外部事件顺序依赖审计）

### 8.1 等价性定义（v1.2 补充：外部事件顺序依赖）

**v1.1 的定义**：
- 最终状态一致 ✅ 必须
- tick 级时序一致 ✅ 必须
- 事件集合一致 ✅ 必须
- 同一 tick 内的事件顺序 ⚪ 不要求
- 中间状态一致 ⚪ 不要求
- 性能一致 ⚪ 不要求（但有底线）

**v1.2 补充：外部系统是否依赖事件顺序？**

**审计结论**：
- **UI 层**：不依赖顺序。UI 只需要最终状态，同一 tick 内的顺序不影响显示
- **信号处理器**：不依赖顺序。交易信号只看"哪个 tick 入池/出池"，同一 tick 内的顺序不影响交易决策（因为一个 tick 内的价格是一样的）
- **告警系统**：不依赖顺序。告警只关心"有没有触发"，不关心触发顺序
- **历史记录**：不依赖顺序。历史记录按 tick 存储，同一 tick 内的顺序不影响回放

**结论**：外部系统不依赖同一 tick 内的事件顺序。
因此，v1.1 的等价性定义是成立的，不需要调整。

**风险应对**：
- 虽然不依赖顺序，但为了减少调试难度，可以在输出事件时按某种确定性顺序排序（比如按 node_id 排序）
- 等价性测试中，事件对比用"集合相等"而不是"序列相等"

---

### 8.2 ~ 8.6 同 v1.1

> Oracle 对比测试框架、测试覆盖矩阵、模糊测试、性能基准测试、迁移策略均同 v1.1，本节不重复。

---

## 9. 可观测性与错误处理（v1.2 不变）

> 同 v1.1 第 9 章（如果有的话），本节不重复。

---

## 10. 事件发布的依赖传递方案（v1.2 新增章节）

### 10.1 问题背景

**问题**：Node、Edge、FilterEvaluator 等组件需要发布事件，怎么拿到 EventQueue 引用？

**v1.1 的问题**：
- Node 类有 `_event_queue` 属性，但 Edge 类没有
- 责任边界不清晰
- 容易导致"每个对象都传一份 event_queue"的混乱局面

---

### 10.2 四种候选方案对比

#### 方案 A：构造函数注入

**核心思想**：每个 Node/Edge 构造时传入 event_bus 引用。

```python
class Node:
    def __init__(self, node_id, event_queue):
        self.node_id = node_id
        self._event_queue = event_queue
```

| 维度 | 评价 | 说明 |
|------|------|------|
| 耦合度 | 中 | Node/Edge 直接依赖 EventQueue |
| 可测试性 | 好 | 可以传入 mock 的 EventQueue 进行单元测试 |
| 性能 | 好 | 直接引用，没有间接层 |
| 复杂度 | 中 | 每个类的构造函数都要加 event_queue 参数 |
| 灵活性 | 中 | 运行时不能换 EventQueue（但通常不需要） |

**优点**：
- ✅ 依赖显式（构造函数里就能看到依赖什么）
- ✅ 可测试性好（容易 mock）
- ✅ 性能好（直接调用）

**缺点**：
- ❌ 构造函数参数膨胀（每个类都要加 event_queue）
- ❌ 依赖传递链条长（Engine → Node → Edge → FilterEvaluator，每层都要传）
- ❌ 责任边界模糊（谁都可以发事件，缺乏统一管控）

---

#### 方案 B：上下文对象（EngineContext）

**核心思想**：EngineContext 传给每个方法，context.event_queue.publish()。

```python
class Node:
    def update_stocks(self, new_stocks, context):
        # ...
        context.event_queue.enqueue_node_changed(...)
```

| 维度 | 评价 | 说明 |
|------|------|------|
| 耦合度 | 低 | 只依赖 EngineContext 接口，不直接依赖 EventQueue |
| 可测试性 | 好 | 可以传入 mock 的 EngineContext |
| 性能 | 中 | 多一层间接访问（context.event_queue），但可以忽略 |
| 复杂度 | 低 | 不需要每个类的构造函数都加参数 |
| 灵活性 | 高 | 运行时可以切换 context（虽然通常不用） |

**优点**：
- ✅ 构造函数简洁（不需要每个类都加 event_queue 参数）
- ✅ 依赖集中（所有全局依赖都在 context 里）
- ✅ 易于扩展（新增全局依赖只需加在 context 里，不用改每个类的构造函数）
- ✅ 符合"上下文传递"的常见模式

**缺点**：
- ❌ 方法签名都要加 context 参数
- ❌ 有"上帝对象"的风险（context 里东西越来越多）
- ❌ 依赖不那么显式（要看方法体才知道用了 context 的什么）

---

#### 方案 C：全局单例

**核心思想**：EventBus.instance()，全局唯一实例。

```python
class EventBus:
    _instance = None
    
    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

class Node:
    def update_stocks(self, new_stocks):
        EventBus.instance().enqueue(...)
```

| 维度 | 评价 | 说明 |
|------|------|------|
| 耦合度 | 高 | 所有类都依赖全局单例 |
| 可测试性 | 差 | 很难 mock 全局单例，测试之间相互影响 |
| 性能 | 好 | 直接访问 |
| 复杂度 | 低 | 不用传参数，最简单 |
| 灵活性 | 差 | 不能有多个实例（比如测试时需要多个独立引擎） |

**优点**：
- ✅ 实现最简单
- ✅ 不用传参数

**缺点**：
- ❌ **可测试性差**（最大的问题）
- ❌ 隐式依赖（代码里看不到依赖关系）
- ❌ 不能有多个独立实例
- ❌ 全局状态，测试之间容易互相污染
- ❌ 违反单一职责（EventBus 既要管业务逻辑，又要管自己的生命周期）

**结论**：不推荐。除非是非常简单的脚本，否则不要用全局单例。

---

#### 方案 D：回调注册

**核心思想**：Node.on_change(callback)，引擎注册回调。

```python
class Node:
    def __init__(self, node_id):
        self.node_id = node_id
        self._on_change_callbacks = []
    
    def on_change(self, callback):
        self._on_change_callbacks.append(callback)
    
    def update_stocks(self, new_stocks):
        # ...
        for callback in self._on_change_callbacks:
            callback(self, added, removed)
```

| 维度 | 评价 | 说明 |
|------|------|------|
| 耦合度 | 低 | Node 不依赖 EventQueue，只依赖回调接口 |
| 可测试性 | 好 | 可以注册测试回调 |
| 性能 | 中 | 回调调用有一点开销 |
| 复杂度 | 高 | 每个可观察对象都要维护回调列表 |
| 灵活性 | 高 | 可以注册多个回调，运行时可增删 |

**优点**：
- ✅ 完全解耦（Node 不知道 EventQueue 的存在）
- ✅ 符合观察者模式
- ✅ 可测试性好

**缺点**：
- ❌ 实现复杂（每个类都要维护回调列表）
- ❌ 回调地狱（注册/注销、生命周期管理麻烦）
- ❌ 调试困难（事件流不直观）
- ❌ 对于我们的场景，有点过度设计（不需要多个观察者）

---

### 10.3 v1.2 的选择：方案 B（上下文对象）

**选择方案 B 的理由**：

1. **依赖集中管理**：所有全局依赖（event_queue、formula_router、data_query、metrics）都在 EngineContext 里，一目了然。

2. **构造函数简洁**：Node/Edge 的构造函数不需要加一堆依赖参数，只需要自己的身份参数（node_id、edge_id 等）。

3. **易于扩展**：未来新增全局依赖（比如配置存储、日志器），只需要加在 EngineContext 里，不用改每个类的构造函数。

4. **可测试性好**：单元测试可以传入 mock 的 EngineContext。

5. **符合行业惯例**：很多框架（如 Spring、Django）都有"上下文"或"请求作用域"的概念，开发者熟悉这种模式。

6. **避免全局单例的问题**：每个引擎实例有自己的 context，测试之间互不影响。

**与方案 A 的对比**：
- 方案 A 构造函数注入更显式，但参数传递链条太长
- 方案 B 上下文传递更简洁，虽然依赖没那么显式，但通过 context 也能看到所有依赖
- 对于我们的场景，方法数量不多，context 参数的成本可接受

**与方案 D 的对比**：
- 方案 D 完全解耦，但实现复杂度高
- 我们的场景不需要多个观察者（只有 EventQueue 一个观察者）
- 方案 D 有点过度设计

---

### 10.4 依赖注入图（v1.2）

```
┌─────────────────────────────────────────────────────────────┐
│                    EventDrivenEngine                        │
│                    (组装者 / 协调者)                         │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           │ 创建并持有
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                      EngineContext                          │
│  ┌─────────────┐  ┌──────────────────┐  ┌───────────────┐  │
│  │ event_queue │  │ formula_router   │  │  data_query   │  │
│  └─────────────┘  └──────────────────┘  └───────────────┘  │
│  ┌─────────────┐  ┌──────────────────┐  ┌───────────────┐  │
│  │  metrics    │  │  config_store    │  │   bar_data    │  │
│  └─────────────┘  └──────────────────┘  └───────────────┘  │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           │ 作为方法参数传递
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
    ┌────────┐        ┌────────┐        ┌──────────────┐
    │  Node  │        │  Edge  │        │FilterEvaluator│
    └────────┘        └────────┘        └──────────────┘
         │                 │
         ▼                 ▼
    ┌────────┐        ┌──────────────┐
    │Source  │        │  TimingGate  │
    │ Node   │        └──────────────┘
    └────────┘
    ┌────────┐
    │StatePool│
    │ Node   │
    └────────┘
    ┌────────┐
    │Condition│
    │ Node   │
    └────────┘
```

**依赖流向**：
- Engine 创建并持有 EngineContext
- Engine 创建 Node/Edge/FilterEvaluator 等组件（构造时不传入 context）
- 运行时，Engine 调用组件的方法时，把 context 作为参数传入
- 组件通过 context 访问 event_queue、formula_router 等全局依赖
- 组件不知道 EventQueue 的具体实现，只知道 context 的接口

---

### 10.5 责任边界

#### 谁可以发布事件？

| 组件 | 可以发布哪些事件 | 说明 |
|------|----------------|------|
| Engine | TimerTickEvent、DataUpdatedEvent、PoolStartEvent、PoolStopEvent | 引擎级事件 |
| Edge | NodeStockChangedEvent（间接，通过调用 enqueue_node_changed） | 边执行导致目标节点变化 |
| Node | 不直接发布事件 | Node 只更新数据，事件由调用方发布 |
| TimingGate | 不发布事件 | TimingGate 只做时机判断，状态变化由 TimerTickHandler 检测 |
| FilterEvaluator | 不发布事件 | 纯计算，无副作用 |
| EventLoop | 不发布事件（除了内部调度） | 只消费事件，不生产业务事件 |

#### 事件发布的统一入口

所有事件都通过 `EngineContext.event_queue` 发布：
- `event_queue.enqueue(event)` — 通用入队
- `event_queue.enqueue_edge_execute(edge_id, reason)` — 边执行请求（去重）
- `event_queue.enqueue_node_changed(node_id, version, added, removed, source)` — 节点变化（去重+合并）

---

### 10.6 方法签名约定

所有需要访问全局依赖的方法，都把 context 作为最后一个参数：

```python
# Node 类
def update_stocks(self, new_stocks: set[str], context: EngineContext) -> tuple[bool, set[str], set[str]]:
    ...

def apply_changes(self, added: set[str], removed: set[str]) -> bool:
    # 这个方法不需要 context，因为它只更新内部状态，不发布事件
    ...

# Edge 类
def execute(self, context: EngineContext) -> tuple[bool, set[str], set[str]]:
    ...

def should_execute(self, now_ts: float, data_dirty: bool) -> bool:
    # 这个方法不需要 context，因为它只读自己的状态
    ...

# FilterEvaluator 类
def evaluate(self, stocks: frozenset, context: EngineContext) -> frozenset:
    ...
```

**约定**：
- 纯计算、纯内部状态变更的方法 → 不需要 context
- 需要发布事件、需要公式路由器、需要数据查询的方法 → 需要 context
- context 统一作为最后一个参数

---

## 11. 总结与变更清单

### 11.1 v1.2 相对于 v1.1 的主要变更

| 变更项 | 位置 | 说明 |
|--------|------|------|
| NodeStockChangedEvent 去重 | §2.3 ~ §2.5 | 新增 Pending 合并 + 墓碑标记方案 |
| 脏标记语义澄清 | §2.7 | 明确定义 is_dirty 的含义 |
| 版本号机制 | §1.1、§2.5 | Node 新增 version 属性，单调递增 |
| 增量过滤 No-Go | §4.1 ~ §4.2 | v1.x 不做增量过滤，v2.0 再做 |
| 四级跳过机制 | §4.3 | 移除 L5，保留 L1~L4 |
| 事件发布依赖传递 | §10 | 选择方案 B（EngineContext 上下文传递） |
| Node 移除 _event_queue | §1.1 | Node 不再直接持有 EventQueue |
| EngineContext 增强 | §1.5 | 新增 event_queue、metrics 属性 |
| 外部事件顺序依赖审计 | §8.1 | 确认外部系统不依赖同一 tick 内的事件顺序 |
| 类图更新 | §1.6 | 更新类关系图 |
| 依赖注入图 | §10.4 | 新增完整的依赖注入图 |

---

### 11.2 三个 P0 问题的解决状态

| P0 问题 | 解决方案 | 所在章节 |
|---------|---------|---------|
| NodeStockChangedEvent 缺少去重机制 | Pending 合并 + 版本号 + 墓碑标记 | §2.3 ~ §2.7 |
| 增量过滤的正确性没有严格论证 | v1.x 不做增量，v2.0 再优化；同时给出了深入分析 | §4.1 ~ §4.2 |
| 事件发布的依赖传递不清晰 | 选择方案 B：EngineContext 上下文传递 | §10 |

---

### 11.3 下一轮迭代方向（v1.3 或开发中完善）

1. **时钟/数据触发的全量扫描优化**：时间轮（Time Wheel）替代全量遍历 TimingGate
2. **执行顺序违反拓扑约束的处理策略**：直接报错 vs 强制调整
3. **调试工具接口定义**：状态快照、单步执行、股票追踪等
4. **部分失败的状态一致性保证**：边执行的原子性
5. **迁移策略时间安排调整**：拉长各阶段时间

---

**v1.2 设计完成。可以进入开发阶段，边开发边验证。**
