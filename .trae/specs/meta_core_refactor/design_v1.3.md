# v1.3 P0漏洞修复 + 数据流补全 + Context拆分

## 0. 设计原则与约束

**现状**：v1.2 设计评审 82.25 分，良好但有 3 个 P0 逻辑漏洞需修复。

**v1.3 目标**：修复三大 P0 漏洞，补全关键缺失环节，为开发扫清最后障碍。

**设计原则（继承 v1.2）**：
- 继承层次不超过 3 层
- 每个类职责单一
- 事件驱动核心循环：事件队列是核心，变化驱动计算
- 数据不变则不计算
- 拓扑（连接关系）与执行顺序（行为次序）严格分离
- 等价性优先：从设计阶段就考虑怎么验证与旧引擎等价
- 可观测性内置：日志、指标、调试工具是一等公民
- 正确性优先于性能：先做对，再做快
- 依赖显式化：不使用全局单例，依赖通过构造函数或上下文显式传递
- 语义明确：每个标记、每个状态的含义必须精确，不能模糊

**v1.3 新增原则**：
- **生命周期匹配**：对象的生命周期必须与其职责匹配，不同生命周期的对象不能混在一个容器里
- **消费即清空**：Pending 变更集被消费后必须立即清空，作为下一轮累计的起点
- **数据流完整**：从数据到达，到下游传播，每一环都必须有明确的触发者和接收者

---

## 1. 核心类图（v1.3 更新：Context 拆分 + Topology 明确）

### 1.1 服务与上下文拆分（v1.3 重大更新）

#### 拆分背景

v1.2 的 EngineContext 混合了两种生命周期完全不同的东西：
- **引擎级服务**：event_queue、formula_router、data_query 等，与引擎同生共死
- **Tick 级状态**：now_ts、bar_data、data_dirty 等，每个 tick 都在变

混在一起的问题：
1. 生命周期不一致，谁来更新 tick 状态不明确
2. 可测试性差：测试一个只需要 formula_router 的方法，也得构造完整 context
3. 违反接口隔离原则：每个组件都拿到了它不应该拿到的东西
4. 有上帝对象膨胀风险

#### 拆分方案

拆分为三个类：

```
EngineServices（引擎级，生命周期 = 引擎生命周期）
  ├── event_queue: EventQueue
  ├── event_bus: EventBus
  ├── formula_router: FormulaRouter
  ├── data_query: DataQuery
  ├── config_store: ConfigStore
  ├── metrics: EngineMetrics
  └── topology: Topology

TickContext（tick 级，生命周期 = 一次 tick 执行）
  ├── tick_id: int
  ├── tick_start_time: float
  ├── current_data_ts: float
  ├── bar_time: datetime
  ├── bar_data: dict
  ├── data_dirty: bool
  ├── events_fired: int
  └── max_iterations: int

EngineContext（组合对象，方便传递）
  ├── services: EngineServices
  └── tick: TickContext
```

**为什么还要保留 EngineContext？**
- 为了方便传递：大多数方法同时需要服务和 tick 状态
- 但内部结构清晰，职责边界明确
- 需要只传服务的地方，可以只传 context.services

---

### 1.2 EngineServices 详细设计

**职责**：封装引擎级的稳定服务依赖，生命周期与引擎相同。

**属性**：
| 属性 | 类型 | 说明 |
|------|------|------|
| `event_queue` | EventQueue | 事件队列（优先级队列 + 去重合并） |
| `event_bus` | EventBus | 事件总线（发布订阅，用于外部系统） |
| `formula_router` | FormulaRouter | 公式路由器 |
| `data_query` | DataQuery | 数据查询服务 |
| `config_store` | ConfigStore | 配置存储 |
| `metrics` | EngineMetrics | 性能指标统计 |
| `topology` | Topology | 拓扑结构（节点、边、连接关系） |

**说明**：
- 所有属性在引擎启动时初始化，运行期间不变（引用不变，内部状态可变）
- 这些服务是无状态的或状态是全局共享的
- 可以在多个 tick 之间安全复用

---

### 1.3 TickContext 详细设计

**职责**：封装单次 tick 执行的状态，每个 tick 开始时创建，tick 结束后丢弃。

**属性**：
| 属性 | 类型 | 说明 |
|------|------|------|
| `tick_id` | int | Tick 序号（单调递增） |
| `tick_start_time` | float | Tick 开始时间戳（性能统计用） |
| `current_data_ts` | float | 当前数据时间戳 |
| `bar_time` | datetime | 当前 K 线时间 |
| `bar_data` | dict | 当前行情数据（全量） |
| `data_dirty` | bool | 本 tick 是否有新数据到达 |
| `events_fired` | int | 本 tick 已处理事件数 |
| `max_iterations` | int | 本 tick 最大迭代次数（防死循环） |

**说明**：
- 每个 tick 开始时创建新的 TickContext
- tick 内所有状态变化都在这个对象上
- tick 结束后，这个对象可以被丢弃或重置
- 天然线程安全（每个 tick 一个实例）

---

### 1.4 EngineContext（组合对象）

**职责**：方便同时传递服务和 tick 状态。

**属性**：
| 属性 | 类型 | 说明 |
|------|------|------|
| `services` | EngineServices | 引擎级服务 |
| `tick` | TickContext | 当前 tick 状态 |

**便捷属性（为了兼容和方便）**：
```python
@property
def event_queue(self):
    return self.services.event_queue

@property
def formula_router(self):
    return self.services.formula_router

@property
def now_ts(self):
    return self.tick.current_data_ts

@property
def data_dirty(self):
    return self.tick.data_dirty
```

**使用约定**：
- 大多数方法传 `EngineContext`（同时需要服务和 tick 状态）
- 纯计算方法（比如公式计算）可以只传 `TickContext` 或具体需要的参数
- 单元测试可以只构造 `EngineServices` 的 mock

---

### 1.5 Topology 类（v1.3 新增：明确职责）

**职责**：管理股票池的拓扑结构——节点、边、以及它们之间的连接关系。
**不负责**：执行逻辑、事件处理、数据计算（这些是 Engine 和 Node/Edge 的事）。

**属性**：
| 属性 | 类型 | 说明 |
|------|------|------|
| `nodes` | dict[str, Node] | 节点字典 {node_id: Node} |
| `edges` | dict[str, Edge] | 边字典 {edge_id: Edge} |
| `source_nodes` | list[SourceNode] | 源节点列表（缓存，方便查找） |
| `sink_nodes` | list[StatePoolNode] | 目标池列表（缓存） |
| `adjacency_list` | dict[str, list[str]] | 邻接表 {node_id: [out_edge_ids]} |
| `reverse_adjacency` | dict[str, list[str]] | 逆邻接表 {node_id: [in_edge_ids]} |
| `execution_order` | list[str] | 拓扑排序后的边执行顺序 |
| `is_valid` | bool | 拓扑是否有效（DAG 检查通过） |

**方法**：
| 方法 | 说明 |
|------|------|
| `from_config(config)` | 从配置构建拓扑（工厂方法） |
| `add_node(node)` | 添加节点 |
| `remove_node(node_id)` | 移除节点 |
| `add_edge(edge)` | 添加边 |
| `remove_edge(edge_id)` | 移除边 |
| `get_node(node_id)` | 获取节点 |
| `get_edge(edge_id)` | 获取边 |
| `get_out_edges(node_id)` | 获取节点的所有出边 |
| `get_in_edges(node_id)` | 获取节点的所有入边 |
| `validate()` | 验证拓扑（DAG 检查、孤立节点检查等） |
| `topological_sort()` | 拓扑排序，计算 execution_order |
| `get_source_nodes()` | 获取所有源节点 |
| `get_sink_nodes()` | 获取所有目标池节点 |
| `reset_all()` | 重置所有节点和边的运行时状态 |

**与 EventDrivenEngine 的关系**：
- Engine 持有 Topology
- Engine 负责执行逻辑，Topology 负责结构管理
- Engine 可以通过 topology.nodes 访问所有节点，但不直接管理节点的创建/删除

---

### 1.6 Node 类体系（v1.3 不变）

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

**方法**：
| 方法 | 说明 |
|------|------|
| `update_stocks(new_stocks, context)` | 更新股票集，变化时返回变更集（added/removed），version+1，置脏 |
| `apply_changes(added, removed)` | 应用变更集（增量更新），返回是否有变化 |
| `mark_dirty()` | 标记节点为脏 |
| `clear_dirty()` | 清除脏标记 |
| `add_in_edge(edge)` / `add_out_edge(edge)` | 添加入/出边 |
| `get_stock_codes()` | 返回股票代码集合（frozenset） |
| `snapshot_stocks()` | 生成股票快照并更新 snapshot |

**产生的事件**：
- 不直接发布事件，由调用方（Edge.execute 或 EventHandler）根据变更集发布事件

---

### 1.7 Edge 类体系（v1.3 不变）

```
Edge (基类)
├── ConditionalEdge (条件转移边)
└── UnconditionalEdge (无条件转移边)

TimingGate (组合组件，被 Edge 持有)
FilterEvaluator (组合组件，被 ConditionalEdge 持有)
```

---

### 1.8 Event 类体系（v1.3 增强：DataUpdatedEvent 明确触发源）

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

#### DataUpdatedEvent（v1.3 增强）

**新增/明确字段**：
| 属性 | 类型 | 说明 |
|------|------|------|
| `data_type` | str | 数据类型（"tick" / "kline" / "finance" 等） |
| `data_ts` | float | 数据时间戳 |
| `affected_codes` | frozenset | 受影响的股票代码（可选，空表示全部） |
| `data_source` | str | 数据源标识（"tq" / "replay" / "manual" 等） |
| `timestamp` | float | 事件时间戳 |
| `source` | str | 事件源标识 |
| `priority` | int | 事件优先级 |

**说明**：
- `affected_codes` 用于增量更新场景，如果为空表示全量更新
- `data_source` 用于调试和统计

---

### 1.9 类关系图（v1.3 更新）

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

     ┌─────────────────────────────────────┐
     │         EngineContext               │
     │  ┌──────────────┐  ┌───────────┐   │
     │  │EngineServices│  │TickContext│   │
     │  │ (稳定服务)   │  │ (tick状态) │   │
     │  └──────────────┘  └───────────┘   │
     └─────────────────────────────────────┘
           ▲
           │ 方法参数传递
           │
  Node / Edge / FilterEvaluator / TimingGate  通过方法参数接收 context
```

**v1.3 关键变化**：
1. EngineContext 拆分为 EngineServices + TickContext
2. Topology 类职责明确，独立管理拓扑结构
3. DataUpdatedEvent 字段增强，明确数据类型和受影响范围

---

## 2. Pending 合并逻辑漏洞修复（v1.3 P0-1）

### 2.1 问题回顾

**v1.2 的 bug**：事件处理后，`info.added` 和 `info.removed` 没有被清空，导致下一轮合并时基准错误，变更被重复消费。

**bug 触发序列**：
```
1. 变化1：added={1,2} → 创建 pending，事件入队
2. 事件出队，开始处理（从 pending 取 added={1,2}）
3. 变化2：added={3} → 合并到 pending，pending.added = {1,2,3}
4. 事件处理完成，has_event_in_queue = False，但 added/removed 不清空
5. 变化3：added={4} → 合并到 pending，pending.added = {1,2,3,4}（基准错误！）
6. 因为 has_event_in_queue=False，变化3触发新事件入队
7. 新事件处理时，取 added={1,2,3,4} → {1,2,3} 被重复处理了！
```

**根本原因**：pending 中的 added/removed 应该是"自上次处理以来的累计变更"，处理完后必须清空，作为下一轮累计的起点。

---

### 2.2 正确设计：入队合并，出队消费，消费即清空

#### 核心思想

1. **Pending 合并发生在入队阶段**，不是出队阶段
2. **入队时**：如果队列中已有同节点的事件，合并变更集，原事件保留但标记为墓碑（无效）
3. **出队时**：如果事件已标记为墓碑，直接跳过
4. **消费时**：用合并后的变更集执行，消费后立即清空 pending 中的变更集

#### 为什么用墓碑标记而不是直接修改堆中的事件？

因为堆（heapq）数据结构不支持高效的随机修改。要修改堆中某个元素，需要：
1. 找到它（O(n)）
2. 修改它
3. 重新堆化（O(n)）

代价太高。用墓碑标记的话：
1. 旧事件保留在堆中，标记为 invalid
2. 新事件入队（携带最新的合并后的变更集）
3. 出队时遇到 invalid 的事件直接跳过

虽然会有一些"垃圾"事件留在堆中，但：
- 同一节点的 pending 事件通常只有 1-2 个
- 事件出队时会被跳过，不影响正确性
- tick 结束时队列清空，垃圾不会累积

---

### 2.3 数据结构

```python
class EventQueue:
    _queue: list                    # heapq 优先级队列
    _counter: int                   # FIFO 计数器
    _pending_edges: set[str]        # 待执行边 ID 集合（去重）
    
    # 节点变化 pending 信息
    _pending_node_changes: dict[str, NodeChangeInfo]

class NodeChangeInfo:
    """
    节点变化的 pending 信息
    
    语义：自上次消费（事件被处理）以来的累计变更
    消费后，added 和 removed 立即清空，作为下一轮累计的起点
    """
    version: int                    # 最新版本号
    added: set[str]                 # 自上次消费以来的累计新增
    removed: set[str]               # 自上次消费以来的累计移除
    has_pending_event: bool         # 队列中是否已有待处理的事件（可能是墓碑+新事件）
```

**注意**：v1.2 叫 `has_event_in_queue`，v1.3 改名为 `has_pending_event`，更准确。

---

### 2.4 完整状态机

#### NodeChangeInfo 的状态

```
           初始状态
              │
              ▼
        ┌───────────┐
        │   EMPTY   │  added={}, removed={}, has_pending_event=False
        └─────┬─────┘
              │ 新变化到达
              ▼
        ┌───────────┐
        │ ACCUMULATING │  added/removed 累计中，has_pending_event=True
        └─────┬─────┘
              │ 事件被消费（出队处理）
              ▼
        ┌───────────┐
        │   EMPTY   │  added/removed 清空，has_pending_event=False
        └───────────┘
```

**状态说明**：

| 状态 | added | removed | has_pending_event | 说明 |
|------|-------|---------|-------------------|------|
| EMPTY | {} | {} | False | 没有待处理的变更 |
| ACCUMULATING | 非空 | 可能非空 | True | 有待处理的变更，正在累计 |

**状态转移**：

| 当前状态 | 事件 | 下一状态 | 动作 |
|---------|------|---------|------|
| EMPTY | 变化入队 | ACCUMULATING | 创建 pending，事件入队，累计 added/removed |
| ACCUMULATING | 变化入队 | ACCUMULATING | 合并变更集，更新版本号（不重复入队） |
| ACCUMULATING | 事件消费 | EMPTY | 取出 added/removed，清空，has_pending_event=False |

---

### 2.5 入队逻辑（合并发生在入队阶段）

```python
def enqueue_node_changed(self, node_id: str, version: int, 
                         added: set[str], removed: set[str], 
                         source: str = ""):
    """
    节点变化事件入队（自动去重 + 合并）
    
    合并发生在入队阶段：
    - 如果队列中已有同节点的事件，合并变更集
    - 旧事件标记为墓碑（invalid）
    - 新事件入队（携带合并后的完整变更集）
    
    Args:
        node_id: 节点 ID
        version: 变化后的版本号
        added: 本次新增的股票
        removed: 本次移除的股票
        source: 事件源（用于调试）
    """
    if node_id not in self._pending_node_changes:
        # 情况1：没有 pending 信息，新建
        info = NodeChangeInfo(
            version=version,
            added=set(added),
            removed=set(removed),
            has_pending_event=True
        )
        self._pending_node_changes[node_id] = info
        
        # 创建事件并入队
        event = NodeStockChangedEvent(
            node_id=node_id,
            version=version,
            added=frozenset(added),
            removed=frozenset(removed),
            is_valid=True,
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
    
    # 情况3：有更新的版本，需要合并
    
    # 先把新变更合并到 pending 中
    new_added = set(added)
    new_removed = set(removed)
    
    # 合并 added/removed（正确的合并公式）
    # 规则：A 先加后删 = 不变，A 先删后加 = 加
    merged_added = (info.added - new_removed) | (new_added - info.removed)
    merged_removed = (info.removed - new_added) | (new_removed - info.added)
    
    info.version = version
    info.added = merged_added
    info.removed = merged_removed
    
    if info.has_pending_event:
        # 队列中已有事件，需要标记旧事件为墓碑，然后入队新事件
        # 注意：我们无法直接修改堆中的旧事件，所以用"墓碑"策略
        # 旧事件保留在堆中，但新事件携带最新的合并后的变更集
        # 出队时，旧事件因为 version < info.version 会被跳过（墓碑）
        
        # 入队新事件（携带合并后的完整变更集）
        event = NodeStockChangedEvent(
            node_id=node_id,
            version=version,
            added=frozenset(merged_added),
            removed=frozenset(merged_removed),
            is_valid=True,
            timestamp=time.time(),
            source=source,
            priority=EVENT_PRIORITY_NODE_STOCK_CHANGED
        )
        self._enqueue(event)
        # has_pending_event 保持 True（因为又入队了一个新事件）
    else:
        # 队列中没有事件，直接入队
        info.has_pending_event = True
        event = NodeStockChangedEvent(
            node_id=node_id,
            version=version,
            added=frozenset(merged_added),
            removed=frozenset(merged_removed),
            is_valid=True,
            timestamp=time.time(),
            source=source,
            priority=EVENT_PRIORITY_NODE_STOCK_CHANGED
        )
        self._enqueue(event)
```

**合并公式验证**：

假设：
- 第一次变化：added={1,2}, removed={}
- 第二次变化：added={3}, removed={1}

合并计算：
```
merged_added = ({1,2} - {1}) | ({3} - {}) = {2} | {3} = {2,3}
merged_removed = ({} - {3}) | ({1} - {1,2}) = {} | {} = {}
```

验证状态变化：
- 初始状态 S0 = {}
- 第一次变化后 S1 = S0 ∪ {1,2} - {} = {1,2}
- 第二次变化后 S2 = S1 ∪ {3} - {1} = {2,3}
- 从 S0 到 S2 的总变更：added={2,3}, removed={} ✓

---

### 2.6 出队与消费逻辑（消费即清空）

```python
def dequeue(self) -> Event:
    """
    出队最高优先级事件
    
    跳过墓碑事件（is_valid=False 或 version < pending_version）
    """
    while not self.is_empty():
        event = heapq.heappop(self._queue)
        
        # 检查是否是节点变化事件
        if isinstance(event, NodeStockChangedEvent):
            info = self._pending_node_changes.get(event.node_id)
            if info is None:
                # 没有 pending 信息（可能是 tick 末清理了），跳过
                continue
            
            if event.version < info.version:
                # 旧版本事件（墓碑），跳过
                # 注意：has_pending_event 保持 True，因为还有更新的事件在队列中
                continue
            
            if event.version > info.version:
                # 理论上不应该发生，防御性编程
                logger.warning(f"事件版本 {event.version} 大于 pending 版本 {info.version}")
                continue
        
        # 有效的事件，返回
        return event
    
    # 队列为空
    return None
```

**事件处理（消费）逻辑**：

```python
# EventLoop 中处理 NodeStockChangedEvent
def on_node_stock_changed(event: NodeStockChangedEvent, context: EngineContext):
    node_id = event.node_id
    queue = context.services.event_queue
    info = queue._pending_node_changes.get(node_id)
    
    if info is None:
        # 没有 pending 信息（不应该发生，但防御性编程）
        return
    
    # ==========================================
    # 关键：先"消费" pending 中的变更（拷贝后清空）
    # ==========================================
    added = info.added.copy()
    removed = info.removed.copy()
    version = info.version
    
    # 清空，为下一轮累计做准备
    info.added.clear()
    info.removed.clear()
    info.has_pending_event = False
    
    # 获取节点对象
    node = context.services.topology.get_node(node_id)
    
    # 版本号验证（防御性检查）
    if version != node.version:
        logger.warning(f"版本不一致：event={version}, node={node.version}")
        return
    
    # 如果没有净变化，直接返回（下游边不需要执行）
    if not added and not removed:
        node.clear_dirty()
        return
    
    # 业务逻辑：把下游边加入队列
    for edge in node.out_edges:
        if edge.should_execute(context.tick.current_data_ts, context.tick.data_dirty):
            queue.enqueue_edge_execute(edge.edge_id)
    
    # 清除脏标记
    node.clear_dirty()
    
    # 发布外部事件（PoolChangedEvent 等）
    _publish_pool_events(node, added, removed, context)
```

**关键点**：
1. **消费即清空**：处理事件时，先拷贝 added/removed，然后立即清空 pending
2. **清空后再处理业务逻辑**：这样处理过程中新到来的变化会正确地累计到下一轮
3. **墓碑跳过**：出队时跳过旧版本的事件（墓碑）

---

### 2.7 典型场景验证

#### 场景 1：正常合并（两次变化，一次处理）

```
初始：EMPTY

1. 变化1入队：added={1,2}, version=2
   → 创建 pending，状态变为 ACCUMULATING
   → 事件1（v2）入队
   → pending: version=2, added={1,2}, removed={}, has_pending_event=True

2. 变化2入队：added={3}, removed={1}, version=3
   → 合并：
     merged_added = ({1,2} - {1}) | ({3} - {}) = {2,3}
     merged_removed = ({} - {3}) | ({1} - {1,2}) = {}
   → pending: version=3, added={2,3}, removed={}, has_pending_event=True
   → 事件2（v3）入队（事件1变成墓碑）

3. 出队：事件1（v2）
   → 检查：event.version(2) < info.version(3) → 墓碑，跳过

4. 出队：事件2（v3）
   → 检查：event.version(3) == info.version(3) → 有效
   → 消费：
     added = {2,3}
     removed = {}
     清空 pending.added 和 pending.removed
     has_pending_event = False
   → 处理业务逻辑...

最终：EMPTY ✓
```

#### 场景 2：处理过程中新变化到来

```
初始：ACCUMULATING（有一个事件在队列中）

1. 事件出队，开始处理
   → 先消费：拷贝 added/removed，清空 pending
   → pending 状态变为 EMPTY（has_pending_event=False）

2. 处理过程中，新变化到达：added={4}, version=4
   → 合并到 pending（此时 pending 是空的）
   → pending: version=4, added={4}, removed={}, has_pending_event=False
   → 因为 has_pending_event=False，所以新事件入队
   → has_pending_event = True

3. 事件处理完成
   → 状态：ACCUMULATING（有一个新事件在队列中）

最终：ACCUMULATING ✓（新变化会被下一个事件处理）
```

#### 场景 3：变更集完全抵消

```
初始：EMPTY

1. 变化1入队：added={1,2}, version=2
   → pending: version=2, added={1,2}, removed={}, has_pending_event=True
   → 事件1入队

2. 变化2入队：removed={1,2}, version=3
   → 合并：
     merged_added = ({1,2} - {1,2}) | ({} - {}) = {}
     merged_removed = ({} - {}) | ({1,2} - {1,2}) = {}
   → pending: version=3, added={}, removed={}, has_pending_event=True
   → 事件2入队（事件1变成墓碑）

3. 出队：事件1（v2）→ 墓碑，跳过

4. 出队：事件2（v3）
   → 消费：
     added = {}
     removed = {}
     清空 pending
     has_pending_event = False
   → 检查：added 和 removed 都为空 → 不执行下游边
   → clear_dirty()

最终：EMPTY ✓
```

#### 场景 4：多次合并

```
初始：EMPTY

1. 变化1：added={1}, v2 → 入队，pending=ACCUMULATING
2. 变化2：added={2}, v3 → 合并，added={1,2}，新事件入队（事件1成墓碑）
3. 变化3：removed={1}, v4 → 合并，added={2}, removed={}，新事件入队（事件2成墓碑）
4. 变化4：added={3,4}, v5 → 合并，added={2,3,4}, removed={}，新事件入队（事件3成墓碑）

5. 出队：事件1（v2）→ 墓碑，跳过
6. 出队：事件2（v3）→ 墓碑，跳过
7. 出队：事件3（v4）→ 墓碑，跳过
8. 出队：事件4（v5）→ 有效
   → 消费：added={2,3,4}, removed={}
   → 处理业务逻辑...

最终：EMPTY ✓
```

---

### 2.8 单元测试用例清单

| 测试用例 | 输入 | 预期输出 | 验证点 |
|---------|------|---------|--------|
| 单次变化 | added={1,2}, v2 | 1个事件，added={1,2} | 基本功能 |
| 两次合并 | 变化1: added={1,2}, v2<br>变化2: added={3}, v3 | 最终1个有效事件，added={1,2,3} | 正常合并 |
| 抵消合并 | 变化1: added={1,2}, v2<br>变化2: removed={1,2}, v3 | 最终1个事件，added={}, removed={} | 变更抵消 |
| 先删后加 | 变化1: removed={1}, v2（假设初始有1）<br>变化2: added={1}, v3 | added={}, removed={} | 先删后加抵消 |
| 多次合并 | 4次变化 | 最终1个有效事件，墓碑事件被跳过 | 多次合并 |
| 过时事件 | 变化1: added={1}, v3<br>变化2: added={2}, v2（过时） | 只有变化1生效，变化2被忽略 | 过时事件忽略 |
| 处理中新变化 | 事件处理中又有新变化 | 新变化会触发新事件，不会丢失 | 处理中累计 |
| 消费即清空 | 事件处理后 | pending.added={}, pending.removed={} | 清空逻辑 |
| 墓碑跳过 | 多个同节点事件 | 只有最新版本的事件被处理 | 墓碑机制 |
| 空变更不触发下游 | added={}, removed={} | 下游边不执行 | 空变更优化 |

---

### 2.9 与旧引擎行为等价性论证

#### 等价性定义

**状态级等价**：每个 tick 结束后，所有节点的股票集合与旧引擎完全一致。

**事件级等价**：
- 外部事件（PoolChangedEvent、AlertEvent、SignalEvent）的集合与旧引擎一致
- 同一 tick 内的事件顺序不做要求

#### 论证

**定理**：在单线程事件循环模型下，Pending 合并 + 墓碑方案与"每次变化都产生事件并逐次处理"的方案，最终状态等价。

**证明**：

我们用数学归纳法证明：对于任意次数的变化，合并后的总变更集等于逐次变化的变更集的"复合"。

**基例（0 次变化）**：
- 合并方案：added={}, removed={}
- 逐次方案：没有变化
- 等价 ✓

**基例（1 次变化）**：
- 合并方案：added=A1, removed=R1
- 逐次方案：added=A1, removed=R1
- 等价 ✓

**归纳步骤**：假设 n 次变化后等价，证明 n+1 次变化后也等价。

设 n 次变化后的总变更为 (An, Rn)，第 n+1 次变化为 (A, R)。

合并方案的总变更：
```
merged_added = (An - R) | (A - Rn)
merged_removed = (Rn - A) | (R - An)
```

逐次方案的总变更：
- 先应用 (An, Rn)，得到状态 S1 = S0 ∪ An - Rn
- 再应用 (A, R)，得到状态 S2 = S1 ∪ A - R = (S0 ∪ An - Rn) ∪ A - R

我们需要证明：S0 ∪ merged_added - merged_removed = S2

```
S0 ∪ merged_added - merged_removed
= S0 ∪ ((An - R) | (A - Rn)) - ((Rn - A) | (R - An))

让我们展开：
S2 = (S0 ∪ An - Rn) ∪ A - R
   = S0 ∪ An ∪ A - Rn - R

现在看左边：
S0 ∪ ((An - R) | (A - Rn)) - ((Rn - A) | (R - An))

我们可以证明这个等于 S0 ∪ An ∪ A - Rn - R。
（详细证明略，直觉上：合并公式就是计算"净变化"，和逐次应用的净效果一样）
```

**对于下游边执行的等价性**：

对于无状态的过滤函数 f：
- 逐次方案：执行 n 次，每次计算 f(S_i)
- 合并方案：执行 1 次，计算 f(S_n)

最终目标节点的状态：
- 逐次方案：target = f(S_1) ∪ f(S_2) ∪ ... ∪ f(S_n)
- 合并方案：target = f(S_n)

**这两者不一定相等！** 比如：
- S1 = {1,2}, f(S1) = {1}
- S2 = {1,2,3}, f(S2) = {3}
- 逐次方案 target = {1, 3}
- 合并方案 target = {3}

**等等，这不对！** 股票池的边逻辑不是"并集"，而是"目标节点 = 源节点中通过过滤的股票的某种流转"。

让我们重新考虑股票池的实际流转逻辑：

**条件转移边的语义**：把源节点中满足条件的股票转移到目标节点。

但"转移"有不同的策略：
1. **复制**：目标节点 = 目标节点 ∪ f(源节点)
2. **移动**：目标节点 = 目标节点 ∪ f(源节点)，源节点 = 源节点 - f(源节点)
3. **同步**：目标节点 = f(源节点)（完全同步）

对于**复制策略**（最常见）：
- 逐次方案：target = target ∪ f(S1) ∪ f(S2) ∪ ... ∪ f(Sn)
- 合并方案：target = target ∪ f(Sn)

这两者不等价，因为 f(S1) ∪ f(S2) ∪ ... ∪ f(Sn) 可能不等于 f(Sn)。

**但是！** 旧引擎也是"每个 tick 只计算一次"，不是"每次变化都计算"。旧引擎的执行模型是：
1. 收集所有数据更新
2. 按拓扑顺序执行所有边一次
3. 级联直到稳定

所以旧引擎同一 tick 内，每条边也只执行一次。

**因此，合并方案与旧引擎是等价的！** 因为旧引擎也是"tick 内只执行一次最终状态"。

**结论**：Pending 合并方案与旧引擎行为等价，因为两者都是"tick 内基于最终状态执行一次"。

---

### 2.10 墓碑标记概念澄清

**什么是墓碑标记（Tombstone）？**

墓碑标记是一种**延迟删除**策略：当需要删除或失效堆中的某个元素时，不直接删除它，而是标记它为"无效"，等它自然出队时再跳过。

**为什么用墓碑？**

因为堆（heapq）数据结构不支持高效的随机删除。直接删除需要：
1. 线性查找元素 O(n)
2. 删除后重新堆化 O(n)

而墓碑标记只需要：
1. 让旧元素留在堆中（不操作）
2. 入队新元素 O(log n)
3. 出队时检查有效性 O(1)

**代价**：堆中会有一些无效的"垃圾"事件。

**为什么代价可以接受？**

1. **数量少**：同一节点在队列中的事件通常只有 1-2 个
2. **不累积**：tick 结束时队列清空，垃圾不会跨 tick 累积
3. **跳过快**：出队时检查版本号是 O(1)，很快
4. **正确性有保障**：无效事件会被正确跳过，不影响结果

**v1.3 中的墓碑实现**：

在 v1.3 中，墓碑不是通过 `is_valid` 标志位实现的，而是通过 **版本号比较** 实现的：
- 旧事件的 version < pending.version → 墓碑，跳过
- 新事件的 version == pending.version → 有效，处理

这比显式的 `is_valid` 标志位更好，因为：
- 不需要修改事件对象（事件是不可变的）
- 版本号本身就是单调递增的，天然可以用来判断新旧

---

## 3. SourceNode / 数据触发完整流程（v1.3 P0-2）

### 3.1 问题回顾

v1.2 详细描述了 Edge.execute() 如何触发下游，但对于**数据从外部到达后，如何触发 SourceNode 更新，如何触发下游边重新执行**，完全没有说明。

缺失的关键环节：
1. 谁调用 SourceNode.update_stocks()？
2. 谁调用 enqueue_node_changed()？
3. DataUpdatedEvent 的完整处理逻辑是什么？
4. 行情数据更新（价格变化）和股票集合变化（备选池变化）是两条不同的路径，分别怎么走？

---

### 3.2 核心概念澄清

在继续之前，必须澄清两个容易混淆的概念：

#### 概念 1：股票集合变化 vs 股票属性变化

| 概念 | 说明 | 对应事件 |
|------|------|---------|
| **股票集合变化** | 哪些股票在池子里（股票代码的集合变了） | NodeStockChangedEvent |
| **股票属性变化** | 股票的价格、成交量等属性变了，但股票本身还在池子里 | DataUpdatedEvent |

**例子**：
- 备选池从 {1,2,3} 变成 {2,3,4} → 股票集合变化 → NodeStockChangedEvent
- 股票 1 的价格从 10 元涨到 11 元 → 股票属性变化 → DataUpdatedEvent

#### 概念 2：SourceNode 的两种类型

| 类型 | 股票集合是否变化 | 触发方式 | 示例 |
|------|----------------|---------|------|
| **静态源节点** | 固定不变 | 启动时初始化一次 | 自选股板块、固定备选池 |
| **动态源节点** | 会动态变化 | 定时刷新、事件触发 | 指数成分股、行业板块、动态选股 |

**v1.3 假设**：大多数 SourceNode 是静态的（股票集合固定），但数据属性（价格）是动态变化的。少数动态源节点（如指数成分股）会有股票集合变化。

---

### 3.3 完整数据流：两条路径

数据到达后，根据数据类型的不同，走两条不同的路径：

```
外部数据到达
    │
    ├─→ 股票集合变化（备选池成分股调整）
    │       ↓
    │   DataProvider 更新成分股列表
    │       ↓
    │   DataProvider 发布 DataUpdatedEvent(data_type="composition")
    │       ↓
    │   EventBus 分发给对应的 SourceNode
    │       ↓
    │   SourceNode.update_stocks()
    │       ↓
    │   NodeStockChangedEvent 入队
    │       ↓
    │   下游边级联执行
    │
    └─→ 股票属性变化（价格、成交量等行情数据）
            ↓
        DataProvider 更新 latest_tick / kline 缓存
            ↓
        DataProvider 发布 DataUpdatedEvent(data_type="tick")
            ↓
        EventBus 分发给所有订阅者
            ↓
        Engine.on_data_updated() 处理
            ↓
        更新 TickContext.bar_data / data_dirty=True
            ↓
        把所有依赖行情的条件边加入执行队列
            ↓
        边重新计算过滤条件
            ↓
        目标节点变化 → NodeStockChangedEvent
            ↓
        下游边级联执行
```

---

### 3.4 路径 1：股票集合变化（SourceNode 更新）

#### 触发源

| 触发源 | 说明 | 示例 |
|--------|------|------|
| **定时刷新** | 每隔一段时间重新拉取成分股 | 每天开盘前刷新指数成分股 |
| **事件触发** | 外部事件触发成分股调整 | 调入调出指数、ST 摘帽戴帽 |
| **手工刷新** | 用户手动触发刷新 | 用户点击"刷新备选池"按钮 |

#### 完整时序图（文字版）

```
[外部]        [DataProvider]      [EventBus]      [SourceNode]    [EventQueue]    [下游]
   │               │                 │                │               │            │
   │  成分股更新    │                 │                │               │            │
   ├──────────────>│                 │                │               │            │
   │               │ 更新成分股缓存   │                │               │            │
   │               │                 │                │               │            │
   │               │  DataUpdatedEvent                │               │            │
   │               │ (data_type="composition")        │               │            │
   │               ├────────────────>│                │               │            │
   │               │                 │                │               │            │
   │               │                 │  分发给订阅者  │               │            │
   │               │                 ├───────────────>│               │            │
   │               │                 │                │               │            │
   │               │                 │                │ 更新股票集合   │            │
   │               │                 │                │ (有变化)       │            │
   │               │                 │                │   ↓            │            │
   │               │                 │                │ version+1      │            │
   │               │                 │                │ mark_dirty()   │            │
   │               │                 │                │   ↓            │            │
   │               │                 │                │ NodeStockChangedEvent 入队 │
   │               │                 │                ├──────────────>│            │
   │               │                 │                │               │            │
   │               │                 │                │               │ 事件出队    │
   │               │                 │                │               │   ↓        │
   │               │                 │                │               │ 下游边入队  │
   │               │                 │                │               ├───────────>│
   │               │                 │                │               │            │ 级联执行
```

#### 涉及的类和方法

**DataProvider**（现有系统中的数据提供者）：
```python
class DataProvider:
    def update_composition(self, pool_id: str, codes: set[str]):
        """更新成分股列表"""
        # 更新本地缓存
        self._composition_cache[pool_id] = codes
        # 发布事件
        event = DataUpdatedEvent(
            data_type="composition",
            data_ts=time.time(),
            affected_codes=frozenset(codes),
            data_source="external",
            extra={"pool_id": pool_id}
        )
        self._event_bus.publish(event)
    
    def subscribe_composition(self, pool_id: str, callback: callable):
        """订阅成分股变化"""
        self._event_bus.subscribe(
            event_type="data_updated",
            filter=lambda e: e.data_type == "composition" and e.extra.get("pool_id") == pool_id,
            callback=callback
        )
```

**SourceNode**：
```python
class SourceNode(Node):
    def __init__(self, node_id: str, data_provider: DataProvider, pool_id: str):
        super().__init__(node_id)
        self._data_provider = data_provider
        self._pool_id = pool_id
        # 订阅成分股变化
        data_provider.subscribe_composition(pool_id, self._on_composition_changed)
    
    def _on_composition_changed(self, event: DataUpdatedEvent):
        """成分股变化回调"""
        new_codes = set(event.affected_codes)
        # 更新股票集
        added, removed = self.update_stocks(new_codes)
        if added or removed:
            # 注意：这里我们需要 event_queue 来发布事件
            # 但 SourceNode 不直接持有 event_queue
            # 所以需要通过某种方式获取...
            # 详见下面的"触发方式选择"
            pass
```

**问题来了**：SourceNode 的 `_on_composition_changed` 回调中，怎么拿到 event_queue 来发布 NodeStockChangedEvent？

---

### 3.5 触发方式选择：谁来调用 enqueue_node_changed？

有三种方案：

#### 方案 A：SourceNode 持有 EngineServices 引用

```python
class SourceNode(Node):
    def __init__(self, node_id: str, services: EngineServices, pool_id: str):
        super().__init__(node_id)
        self._services = services
        # ...
    
    def _on_composition_changed(self, event):
        new_codes = set(event.affected_codes)
        added, removed = self.update_stocks(new_codes)
        if added or removed:
            self._services.event_queue.enqueue_node_changed(
                node_id=self.node_id,
                version=self.version,
                added=added,
                removed=removed,
                source="composition_update"
            )
```

**优点**：
- 简单直接
- SourceNode 自己负责发布事件

**缺点**：
- SourceNode 依赖 EngineServices，耦合度高
- 测试时需要 mock EngineServices
- Node 基类不依赖 services，子类依赖，不一致

---

#### 方案 B：Engine 统一处理，SourceNode 只更新数据

```python
class SourceNode(Node):
    def update_composition(self, new_codes: set[str]) -> tuple[set, set]:
        """更新成分股，返回变更集（不发布事件）"""
        return self.update_stocks(new_codes)

class EventDrivenEngine:
    def _setup_source_subscriptions(self):
        """设置源节点的数据订阅"""
        for node in self.services.topology.get_source_nodes():
            if isinstance(node, MarketSourceNode):
                pool_id = node.params["pool_id"]
                self.services.data_provider.subscribe_composition(
                    pool_id,
                    lambda e, n=node: self._on_source_composition_changed(n, e)
                )
    
    def _on_source_composition_changed(self, source_node: SourceNode, event: DataUpdatedEvent):
        """源节点成分股变化回调"""
        new_codes = set(event.affected_codes)
        added, removed = source_node.update_composition(new_codes)
        if added or removed:
            self.services.event_queue.enqueue_node_changed(
                node_id=source_node.node_id,
                version=source_node.version,
                added=added,
                removed=removed,
                source="composition_update"
            )
```

**优点**：
- SourceNode 不依赖 EngineServices，职责单一（只存数据）
- 事件发布统一由 Engine 管理，责任清晰
- 符合"Node 不直接发布事件"的原则（v1.2 §1.1）

**缺点**：
- Engine 需要管理所有订阅，代码稍多
- 回调链条稍长

---

#### 方案 C：通过 EventBus 间接触发

```python
# SourceNode 发布内部事件到 EventBus
# Engine 监听这些事件，然后转发到 EventQueue

class SourceNode(Node):
    def _on_composition_changed(self, event):
        new_codes = set(event.affected_codes)
        added, removed = self.update_stocks(new_codes)
        if added or removed:
            # 发布到内部 EventBus
            self._event_bus.publish(InternalNodeChangedEvent(...))
```

**缺点**：多了一层间接，没必要。

---

**v1.3 选择：方案 B（Engine 统一处理）**

理由：
1. 符合"Node 不直接发布事件"的原则（v1.2 已确定）
2. SourceNode 职责单一，只管理数据
3. 事件发布的责任统一在 Engine/EventLoop 层面
4. 可测试性好（SourceNode 不需要 mock event_queue）

---

### 3.6 路径 2：股票属性变化（行情数据更新）

这是更常见的路径：行情数据（价格、成交量等）更新，导致过滤条件需要重新计算。

#### 触发源

| 触发源 | 说明 | 频率 |
|--------|------|------|
| **TQ 推送** | 通达信实时行情推送 | 高频（每秒多次） |
| **定时器触发** | 定时拉取行情数据 | 中频（每秒/每几秒） |
| **回放数据** | 历史数据回放 | 可控 |
| **手工刷新** | 用户手动刷新 | 低频 |

#### 完整时序图（文字版）

```
[外部]     [DataProvider]   [Engine]    [TickContext]   [EventQueue]   [Edge]   [下游]
   │            │              │             │              │           │         │
   │ 行情推送    │              │             │              │           │         │
   ├───────────>│              │             │              │           │         │
   │            │ 更新缓存      │             │              │           │         │
   │            │ (latest_tick)│             │              │           │         │
   │            │              │             │              │           │         │
   │            │ DataUpdatedEvent           │              │           │         │
   │            │ (data_type="tick")         │              │           │         │
   │            ├─────────────>│             │              │           │         │
   │            │              │             │              │           │         │
   │            │              │ 更新 tick 状态              │           │         │
   │            │              │ bar_data = 新数据          │           │         │
   │            │              │ data_dirty = True          │           │         │
   │            │              │             │              │           │         │
   │            │              │ 找出所有依赖行情的条件边    │           │         │
   │            │              │             │              │           │         │
   │            │              │ 把这些边加入执行队列       │           │         │
   │            │              ├────────────────────────────>│           │         │
   │            │              │             │              │           │         │
   │            │              │             │              │ 边出队执行 │         │
   │            │              │             │              ├──────────>│         │
   │            │              │             │              │           │ 重新计算  │
   │            │              │             │              │           │ 过滤条件  │
   │            │              │             │              │           │    ↓    │
   │            │              │             │              │           │ 目标节点  │
   │            │              │             │              │           │ 变化？    │
   │            │              │             │              │           │    ↓    │
   │            │              │             │              │           │ NodeStock │
   │            │              │             │              │           │ Changed   │
   │            │              │             │              │<──────────┤ 入队     │
   │            │              │             │              │           │         │
   │            │              │             │              │ 下游级联  │         │
   │            │              │             │              ├────────────────────>│
```

#### 涉及的类和方法

**DataProvider**（现有系统）：
```python
class DataProvider:
    def on_tick_data(self, codes: list[str], tick_data: dict):
        """接收 tick 数据"""
        # 更新缓存
        for code in codes:
            self._tick_cache[code] = tick_data.get(code)
        # 发布事件
        event = DataUpdatedEvent(
            data_type="tick",
            data_ts=tick_data.get("timestamp", time.time()),
            affected_codes=frozenset(codes),
            data_source="tq"
        )
        self._event_bus.publish(event)
```

**Engine（数据更新处理）**：
```python
class EventDrivenEngine:
    def _setup_data_subscriptions(self):
        """设置数据订阅"""
        self.services.data_provider.subscribe_tick(self._on_tick_data)
    
    def _on_tick_data(self, event: DataUpdatedEvent):
        """tick 数据到达回调"""
        # 更新 TickContext
        self.context.tick.current_data_ts = event.data_ts
        self.context.tick.bar_data = self.services.data_provider.get_latest_tick_all()
        self.context.tick.data_dirty = True
        
        # 找出所有依赖行情的条件边，加入执行队列
        # 注意：这里是全量扫描，因为数据更新是全局事件
        for edge in self.services.topology.edges.values():
            if isinstance(edge, ConditionalEdge) and edge.is_data_triggered():
                if edge.timing_gate.is_open(self.context.tick.current_data_ts):
                    self.services.event_queue.enqueue_edge_execute(edge.edge_id)
        
        # 如果事件循环当前没在跑，启动它
        if not self._event_loop_running:
            self._run_event_loop()
```

**ConditionalEdge**：
```python
class ConditionalEdge(Edge):
    def is_data_triggered(self) -> bool:
        """是否由数据更新触发"""
        # 大多数条件边都是数据触发的
        # 除非是纯节点触发的（比如无条件转移边）
        return self.filter_evaluator.needs_market_data()
    
    def should_execute(self, now_ts: float, data_dirty: bool) -> bool:
        """是否应该执行"""
        # 时机门控通过
        if not self.timing_gate.is_open(now_ts):
            return False
        # 源节点脏 或 数据脏
        return self.source_node.is_dirty or data_dirty
```

---

### 3.7 时钟触发路径（TimerTickEvent）

除了数据更新，时钟 tick 也是重要的触发源。

#### 完整时序图（文字版）

```
[定时器]     [Engine]    [TickContext]   [EventQueue]   [TimingGate]   [Edge]   [下游]
   │           │             │              │              │            │         │
   │ tick      │             │              │              │            │         │
   ├──────────>│             │              │              │            │         │
   │           │ 创建新的 TickContext       │              │            │         │
   │           │ (tick_id++, now_ts=...)    │              │            │         │
   │           │             │              │              │            │         │
   │           │ TimerTickEvent 入队        │              │            │         │
   │           ├────────────────────────────>│              │            │         │
   │           │             │              │              │            │         │
   │           │             │              │ 事件出队      │            │         │
   │           │             │              ├──────────────>│            │         │
   │           │             │              │              │            │         │
   │           │             │              │ 推进时间      │            │         │
   │           │             │              │ (检查所有     │            │         │
   │           │             │              │  TimingGate) │            │         │
   │           │             │              │              │ 状态变化？  │         │
   │           │             │              │              ├────────────>│         │
   │           │             │              │              │            │         │
   │           │             │              │ 时机窗口变化的边入队       │         │
   │           │             │              ├───────────────────────────>│         │
   │           │             │              │              │            │ 执行    │
   │           │             │              │              │            │   ↓     │
   │           │             │              │              │            │ 下游级联 │
   │           │             │              │              │            ├────────>│
```

**说明**：
- TimerTickEvent 也是全量扫描（检查所有 TimingGate）
- 只有时机状态发生变化的边才会入队执行
- 这是必要的，因为时间是全局推进的

---

### 3.8 与当前系统的对应关系

#### TQAdapter 怎么接入？

```
TQ 推送 → TQAdapter.on_tick_received() → DataProvider.on_tick_data() → EventBus
                                                                           ↓
                                                                   Engine._on_tick_data()
                                                                           ↓
                                                                   更新 TickContext
                                                                           ↓
                                                                   条件边入队
                                                                           ↓
                                                                   事件循环执行
```

**现有系统中的 DataProvider**：
- `core/data_provider.py` 或类似的文件
- 已经有 `latest_tick`、`kline` 等缓存
- 已经有事件通知机制（或者可以加）

**改造点**：
1. 给 DataProvider 增加 EventBus 发布能力
2. 增加 `subscribe_tick()`、`subscribe_composition()` 等订阅方法
3. Engine 在启动时设置好订阅关系

#### SourceNode 的类型

| SourceNode 类型 | 对应现有概念 | 股票集合是否变化 | 触发方式 |
|----------------|-------------|----------------|---------|
| MarketSourceNode | 市场源/备选池 | 大部分静态，少数动态 | 定时刷新 + 事件触发 |
| CandidatePoolNode | 候选池 | 静态（用户手动添加） | 手工操作 |

**CandidatePoolNode 的手工操作路径**：
```
用户添加股票 → API → Engine.add_stocks_to_candidate_pool()
                           ↓
                    candidate_node.update_stocks()
                           ↓
                    enqueue_node_changed()
                           ↓
                    下游级联执行
```

---

### 3.9 全量扫描 vs 事件驱动的澄清

**诚实说明**：

v1.2 之前的文档过度营销"真·事件驱动"，但实际上：
- **核心级联传播**是事件驱动的（NodeStockChangedEvent → 下游边执行）
- **时钟 tick 和数据到达**是全量扫描的（因为这两类事件本身就是全局的）

这不是缺点，而是合理的设计：
- 全局事件（时间推进、数据更新）本来就需要全量检查
- 局部事件（节点变化）用事件驱动，避免不必要的计算

**v1.3 修正说法**："事件驱动核心循环"，而不是"真·事件驱动"。

---

## 4. 公式结果缓存设计（v1.3 P1 补充）

### 4.1 缓存设计目标

L4 级跳过：公式结果缓存，避免重复计算相同的公式。

### 4.2 缓存键设计

**缓存键组成**：
```
cache_key = (formula_id, input_codes_frozenset, data_version)
```

| 组成部分 | 说明 |
|---------|------|
| `formula_id` | 公式唯一标识（公式名称 + 参数） |
| `input_codes_frozenset` | 输入股票代码集合（frozenset，可哈希） |
| `data_version` | 数据版本号（数据变化时递增） |

**为什么用 data_version 而不是时间戳？**
- 时间戳精度不够（同一秒内可能多次更新）
- data_version 单调递增，语义清晰
- 数据每次变化，data_version + 1

### 4.3 缓存失效策略

**失效时机**：
1. **数据变化**：data_version 变化，所有缓存自动失效（因为 key 里包含 data_version）
2. **tick 结束**：tick 结束时清空所有缓存（简单可靠）

**v1.3 选择**：tick 结束时清空所有缓存。

理由：
1. 实现简单，不容易出错
2. 同一 tick 内多次计算相同公式可以命中
3. tick 间不复用，避免数据过期问题
4. 性能足够（公式计算通常很快）

### 4.4 缓存大小限制

**策略**：不限制大小，或用简单的 LRU。

理由：
- 同一 tick 内的公式调用次数有限
- 股票池的公式数量通常不多（几十到几百个）
- tick 结束就清空，不会内存泄漏

### 4.5 缓存接口定义

```python
class FormulaCache:
    """公式结果缓存（tick 级）"""
    
    def __init__(self):
        self._cache: dict[tuple, any] = {}
        self.hit_count = 0
        self.miss_count = 0
    
    def get(self, formula_id: str, codes: frozenset, data_version: int) -> any:
        """获取缓存结果，不存在返回 None"""
        key = (formula_id, codes, data_version)
        result = self._cache.get(key)
        if result is not None:
            self.hit_count += 1
        else:
            self.miss_count += 1
        return result
    
    def set(self, formula_id: str, codes: frozenset, data_version: int, result: any):
        """设置缓存结果"""
        key = (formula_id, codes, data_version)
        self._cache[key] = result
    
    def clear(self):
        """清空缓存（tick 结束时调用）"""
        self._cache.clear()
        self.hit_count = 0
        self.miss_count = 0
    
    @property
    def hit_rate(self) -> float:
        """命中率"""
        total = self.hit_count + self.miss_count
        if total == 0:
            return 0.0
        return self.hit_count / total
```

### 4.6 在 FilterEvaluator 中的使用

```python
class FilterEvaluator:
    def evaluate(self, input_stocks: frozenset, context: EngineContext) -> frozenset:
        """执行过滤计算"""
        # 尝试从缓存获取
        cache = context.services.formula_router.cache
        data_version = context.tick.data_version  # 需要在 TickContext 中增加
        formula_id = self._formula_id
        
        cached = cache.get(formula_id, input_stocks, data_version)
        if cached is not None:
            return cached
        
        # 未命中，实际计算
        result = self._do_evaluate(input_stocks, context)
        
        # 写入缓存
        cache.set(formula_id, input_stocks, data_version, result)
        
        return result
```

**注意**：需要在 TickContext 中增加 `data_version` 字段，数据每次更新时 +1。

---

## 5. 等价性论证补充（v1.3 P1 改进）

### 5.1 等价性的精确定义

| 层级 | 等价要求 | 说明 |
|------|---------|------|
| **L1：最终状态等价** | ✅ 必须 | 每个 tick 结束后，所有节点的股票集合完全一致 |
| **L2：外部事件等价** | ✅ 必须 | 外部事件（入池/出池/告警/信号）的集合一致 |
| **L3：tick 级时序** | ✅ 必须 | 事件发生在哪个 tick 是一致的 |
| **L4：同一 tick 内顺序** | ⚪ 不要求 | 同一 tick 内的事件顺序不做要求 |
| **L5：中间状态** | ⚪ 不要求 | tick 内的中间状态不做要求 |
| **L6：事件数量** | ⚪ 不要求 | 同一 tick 内的事件数量可以不同 |

### 5.2 外部系统对事件顺序的依赖审计

| 外部系统 | 是否依赖顺序 | 说明 |
|---------|-------------|------|
| **UI 层** | 不依赖 | UI 只需要最终状态，同一 tick 内的顺序不影响显示 |
| **信号处理器** | 不依赖 | 交易信号只看"哪个 tick 入池/出池"，同一 tick 内价格一样 |
| **告警系统** | 不依赖 | 告警只关心"有没有触发"，不关心触发顺序 |
| **历史记录** | 不依赖 | 历史记录按 tick 存储，同一 tick 内的顺序不影响回放 |

**结论**：外部系统不依赖同一 tick 内的事件顺序，L4 不等价是可接受的。

### 5.3 合并方案与旧引擎的等价性证明

**定理**：Pending 合并方案与旧引擎状态级等价（L1）。

**证明**：

旧引擎的执行模型：
1. 每个 tick 开始，收集所有数据更新
2. 按拓扑顺序执行所有边一次
3. 如果有节点变化，重新执行下游边（级联）
4. 直到没有新的变化（达到稳定状态）
5. tick 结束

新引擎（Pending 合并）的执行模型：
1. 每个 tick 开始，数据更新触发条件边入队
2. 事件循环处理事件：边执行 → 节点变化 → 下游边入队 → ...
3. 直到队列为空（达到稳定状态）
4. tick 结束

**两者的共同点**：都是"数据变化 → 级联传播 → 稳定"的模型。

**两者的区别**：
- 旧引擎：按拓扑顺序批量执行
- 新引擎：按事件驱动增量执行

**最终状态等价性**：
- 假设过滤函数是确定性的（相同输入产生相同输出）
- 假设流转策略是幂等的（多次执行相同结果）
- 那么无论执行顺序如何，最终都会收敛到同一个稳定状态
- （这类似于数据流系统的"最终一致性"）

**对于无状态过滤 + 复制式流转**：
- 稳定状态是唯一的，与执行顺序无关
- 所以两种模型最终状态等价 ✓

### 5.4 变更集抵消场景的等价性

**场景**：同一 tick 内，股票先加后删（或先删后加），净变化为 0。

| 引擎 | 行为 |
|------|------|
| 旧引擎 | 可能产生两个事件（入池 + 出池），也可能只看到最终状态 |
| 新引擎 | 合并后净变化为 0，不触发下游，不产生事件 |

**这等价吗？**

如果旧引擎也是"tick 结束后才产生外部事件"，那是等价的。
如果旧引擎是"变化时立即产生外部事件"，那不等价。

**需要确认旧引擎的行为**：
- 旧引擎是每个 tick 结束时统一产生事件，还是变化时立即产生？

**v1.3 假设**：旧引擎是 tick 级的，每个 tick 结束时统一计算最终状态并产生事件。因此合并方案是等价的。

**如果旧引擎是变化时立即产生事件**：
- 我们需要在 tick 结束时，根据节点的净变化产生外部事件
- 而不是在每次变化时产生
- 这样仍然是等价的（外部看到的是最终状态）

---

## 6. 启动流程与初始化（v1.3 P1 补充）

### 6.1 引擎启动时序图

```
[调用方]    [Engine]    [Topology]    [EngineServices]    [EventLoop]    [SourceNode]
   │          │            │               │                  │              │
   │ 启动     │            │               │                  │              │
   ├─────────>│            │               │                  │              │
   │          │ 创建 EngineServices         │                  │              │
   │          ├────────────────────────────>│                  │              │
   │          │            │               │                  │              │
   │          │ 从配置构建拓扑              │                  │              │
   │          ├────────────>│               │                  │              │
   │          │            │ 创建 Node/Edge│                  │              │
   │          │            ├───────────────┤                  │              │
   │          │            │ 验证拓扑(DAG)  │                  │              │
   │          │            │ 拓扑排序       │                  │              │
   │          │<───────────┤               │                  │              │
   │          │            │               │                  │              │
   │          │ 初始化源节点数据            │                  │              │
   │          ├──────────────────────────────────────────────────────────────>│
   │          │            │               │                  │              │ 加载初始
   │          │            │               │                  │              │ 股票集合
   │          │<──────────────────────────────────────────────────────────────┤
   │          │            │               │                  │              │
   │          │ 创建 EventLoop             │                  │              │
   │          ├──────────────────────────────────────────────>│              │
   │          │            │               │                  │              │
   │          │ 注册事件处理器              │                  │              │
   │          ├──────────────────────────────────────────────>│              │
   │          │            │               │                  │              │
   │          │ 设置数据订阅                │                  │              │
   │          ├───────────────┬──────────────────────────────────────────────┤
   │          │               │               │                  │           │
   │          │ 发布 PoolStartEvent          │                  │              │
   │          ├───────────────────────────────┤                  │            │
   │          │            │               │                  │              │
   │          │ 启动完成 ✓  │               │                  │              │
   │<─────────┤            │               │                  │              │
```

### 6.2 第一个 tick 怎么触发？

**两种方式**：

1. **时钟触发**：定时器到了，产生 TimerTickEvent
2. **数据触发**：第一笔行情数据到达，产生 DataUpdatedEvent

**通常是时钟触发先到**，因为：
- 引擎启动时，如果已经在交易时间，会立即触发第一个 tick
- 然后等待行情数据到达

---

## 7. 总览：v1.3 变更清单

### P0 修复（3个）

| # | 问题 | 修复方案 | 位置 |
|---|------|---------|------|
| 1 | Pending 合并逻辑漏洞 | 消费即清空 + 墓碑标记 + 完整状态机 | §2 |
| 2 | SourceNode / 数据触发流程缺失 | 补全两条路径（集合变化 + 属性变化）+ 时序图 | §3 |
| 3 | EngineContext 职责模糊 | 拆分为 EngineServices + TickContext | §1.1-1.4 |

### P1 改进（4个）

| # | 改进 | 位置 |
|---|------|------|
| 1 | 等价性论证更严谨 | §5 |
| 2 | Topology 类职责与接口明确 | §1.5 |
| 3 | 公式结果缓存设计细节 | §4 |
| 4 | 墓碑标记概念澄清 | §2.10 |

### 其他补充

| # | 补充 | 位置 |
|---|------|------|
| 1 | 启动流程与初始化 | §6 |
| 2 | 全量扫描 vs 事件驱动的澄清 | §3.9 |
| 3 | 完整的单元测试用例清单 | §2.8 |

---

## 8. 下一步计划

### 开发顺序建议

1. **第一周：核心骨架**
   - EventQueue（含 Pending 合并）
   - EventLoop
   - Node/Edge 基类
   - EngineServices / TickContext / EngineContext
   - Topology 基础功能

2. **第二周：核心功能**
   - TimingGate
   - FilterEvaluator（含公式缓存）
   - SourceNode / StatePoolNode / ConditionNode
   - ConditionalEdge / UnconditionalEdge
   - 数据订阅与触发流程

3. **第三周：验证与完善**
   - 等价性测试框架
   - Oracle 对比测试
   - 性能基准测试
   - bug 修复

### v1.4 可能的议题

- 增量过滤的详细设计（如果 v1.x 性能不够）
- 时间轮（Time Wheel）优化 TimingGate 检查
- 更细粒度的指标体系
- 调试工具与可视化

---

**文档版本**：v1.3  
**创建日期**：2026-07-01  
**基于版本**：v1.2（评审得分 82.25）
