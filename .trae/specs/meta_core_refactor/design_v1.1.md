# v1.1 真·事件驱动 + 执行顺序澄清 + 等价性框架

## 0. 设计原则与约束

**现状**：v1.0 设计评审 55.7 分，不及格。三大 P0 硬伤：伪事件驱动、执行顺序逻辑错误、等价性只有口号。

**v1.1 目标**：解决三大 P0 问题，建立可落地的、可验证的事件驱动架构。

**设计原则（修订版）**：
- 继承层次不超过 3 层
- 每个类职责单一
- **真·事件驱动**：事件队列是核心，不是边列表遍历；变化驱动计算，不是时钟驱动计算
- 数据不变则不计算
- 拓扑（连接关系）与执行顺序（行为次序）严格分离
- **等价性优先**：从设计阶段就考虑怎么验证与旧引擎等价
- **可观测性内置**：日志、指标、调试工具是一等公民

---

## 1. 核心类图（v1.1 更新）

### 1.1 Node 类体系（微调）

```
Node (基类)
├── SourceNode (源节点)
│   ├── MarketSourceNode (市场源/备选池)
│   └── CandidatePoolNode (候选池)
├── StatePoolNode (状态池)
└── ConditionNode (条件节点)
```

> **v1.1 变更**：移除 SinkNode 类，用 StatePoolNode + is_sink 标记替代。

#### Node 基类

**职责**：所有节点的抽象基类，封装节点身份、状态、事件发布、脏标记管理。

**属性**：
| 属性 | 类型 | 说明 |
|------|------|------|
| `node_id` | str | 节点唯一 ID |
| `node_type` | str | 节点类型（market_source / state_pool / condition / discard_pool） |
| `name` | str | 节点名称 |
| `params` | dict | 节点参数（从 pool_config 来） |
| `stocks` | list[dict] | 节点内股票列表 |
| `snapshot` | frozenset | 股票代码快照，用于变更检测 |
| `is_dirty` | bool | 脏标记，股票集是否变化 |
| `data_ts` | float | 数据时间戳 |
| `in_edges` | list[Edge] | 入边列表 |
| `out_edges` | list[Edge] | 出边列表 |
| `_event_queue` | EventQueue | 事件队列引用 |

**方法**：
| 方法 | 说明 |
|------|------|
| `update_stocks(new_stocks)` | 更新股票集，变化时置脏并产生 NodeStockChangedEvent 入队 |
| `mark_dirty()` | 标记节点为脏 |
| `clear_dirty()` | 清除脏标记 |
| `add_in_edge(edge)` / `add_out_edge(edge)` | 添加入/出边 |
| `get_stock_codes()` | 返回股票代码集合 |
| `snapshot_stocks()` | 生成股票快照并更新 snapshot |

**产生的事件**：
- `NodeStockChangedEvent` — 股票集变化时

---

### 1.2 Edge 类体系（v1.1 重大更新：TimingGate 拆分）

```
Edge (基类)
├── ConditionalEdge (条件转移边)
└── UnconditionalEdge (无条件转移边)

TimingGate (组合组件，被 Edge 持有)
```

> **v1.1 变更**：时机门控从 Edge 中拆分为独立的 TimingGate 类（组合优于继承）。

#### Edge 基类

**职责**：边的抽象基类，封装端点连接、触发判定、执行流转。

**属性**：
| 属性 | 类型 | 说明 |
|------|------|------|
| `edge_id` | str | 边唯一 ID |
| `source_node` | Node | 源节点引用 |
| `target_node` | Node | 目标节点引用 |
| `params` | dict | 边参数 |
| `execution_order` | int | 执行顺序优先级（编译期确定，数字越小越先执行） |
| `last_executed_ts` | float | 上次执行时间戳 |
| `exec_count` | int | 执行次数 |

**方法**：
| 方法 | 说明 |
|------|------|
| `should_execute(now_ts, data_dirty)` | 是否应该执行（由子类实现） |
| `execute(now_ts, data_dirty, formula_router, data_query)` | 执行边的流转逻辑，返回执行结果 |
| `reset()` | 重置执行状态 |

#### ConditionalEdge（条件转移边）

**职责**：条件转移边（源为备选池/状态池，目标为条件节点），有时机属性。
触发条件：timing_gate 通过 AND (源节点脏 OR 行情数据脏)。

**新增属性**：
| 属性 | 类型 | 说明 |
|------|------|------|
| `timing_gate` | TimingGate | 时机门控组件 |
| `filter_evaluator` | FilterEvaluator | 过滤器评估器组件 |
| `strategy_action` | str | 边策略动作 |

**新增方法**：
| 方法 | 说明 |
|------|------|
| `is_data_triggered(data_dirty)` | 判断数据变化是否触发 |
| `is_node_triggered()` | 判断源节点变化是否触发 |

#### UnconditionalEdge（无条件转移边）

**职责**：无条件转移边（源为条件节点，目标为状态池），无时机属性。
触发条件：源节点股票变化。

#### TimingGate（时机门控）

**职责**：专门负责时机门控逻辑（starttype × cxtype = 24 种组合）。从 Edge 中拆出，单一职责。

**属性**：
| 属性 | 类型 | 说明 |
|------|------|------|
| `start_type` | int | 开始类型（0-7） |
| `cx_type` | int | 持续类型（0-2） |
| `start_param` | any | 开始参数（延迟秒数/指定时间等） |
| `duration_param` | any | 持续参数（持续秒数等） |
| `last_fired_ts` | float | 上次触发时间戳 |
| `first_fired_ts` | float | 首次触发时间戳 |
| `is_fired` | bool | 当前是否处于触发窗口内 |

**方法**：
| 方法 | 说明 |
|------|------|
| `should_trigger(now_ts, bar_time, pool_start_ts)` | 判定当前是否在时机窗口内 |
| `reset()` | 重置状态 |

> **v1.1 说明**：24 种时机组合的判定逻辑直接编译为代码（硬编码），不需要运行时查表。因为这 24 种组合是固定的，不会变化。

---

### 1.3 Event 类体系（v1.1 精简 + 明确优先级）

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

> **v1.1 变更**：
> - 合并 PoolEnterEvent + PoolExitEvent → PoolChangedEvent（带 direction 字段）
> - 合并 EdgeFiredEvent + EdgeExecutedEvent → EdgeExecuteEvent（执行请求事件）
> - 移除 BarDataUpdatedEvent（合并入 DataUpdatedEvent，带 data_type 字段）
> - 事件总数从 12+ 精简到 9 种

#### Event 基类

| 属性 | 类型 | 说明 |
|------|------|------|
| `event_type` | str | 事件类型 |
| `timestamp` | float | 事件时间戳 |
| `source` | str | 事件源标识 |
| `priority` | int | 事件优先级（用于队列排序） |

#### 事件优先级定义（重要！）

| 优先级 | 事件类型 | 说明 |
|--------|---------|------|
| 0（最高） | `TimerTickEvent` | 时钟 tick 必须最先处理，推进时间 |
| 1 | `DataUpdatedEvent` | 数据更新次之，所有计算的基础 |
| 2 | `EdgeExecuteEvent` | 边执行请求，按 execution_order 排序 |
| 3 | `NodeStockChangedEvent` | 节点变化，会产生新的 EdgeExecuteEvent |
| 4（最低） | `PoolChangedEvent` / `AlertEvent` / `SignalEvent` | 输出类事件，最后处理 |

> **为什么 NodeStockChangedEvent 优先级比 EdgeExecuteEvent 低？**
> 因为 NodeStockChangedEvent 的处理是"把下游边加入执行队列"，而不是直接执行。
> 处理流程：EdgeExecuteEvent 执行 → 目标节点变化 → 产生 NodeStockChangedEvent →
> 处理 NodeStockChangedEvent → 把下游边加入队列 → 继续处理 EdgeExecuteEvent。
> 这样保证了"执行完一条边，立刻级联到下游"，而不是等所有 NodeStockChangedEvent 都处理完。

---

### 1.4 核心新增类（v1.1）

#### EventQueue（事件队列）

**职责**：事件驱动的核心。优先级队列，按优先级 + 执行顺序 + 入队时间排序。

**属性**：
| 属性 | 类型 | 说明 |
|------|------|------|
| `_queue` | heapq | 优先级队列（最小堆） |
| `_counter` | int | 同优先级的入队顺序计数器（保证 FIFO） |
| `_pending_edges` | set[str] | 待执行的边 ID 集合（去重，避免重复入队） |

**方法**：
| 方法 | 说明 |
|------|------|
| `enqueue(event)` | 事件入队 |
| `dequeue()` | 出队最高优先级事件 |
| `is_empty()` | 队列是否为空 |
| `size()` | 队列大小 |
| `enqueue_edge_execute(edge_id, reason)` | 边执行请求入队（自动去重） |
| `clear()` | 清空队列 |

**排序规则**：
1. 先按 `priority` 升序（数字越小优先级越高）
2. 同优先级的 `EdgeExecuteEvent` 按 `execution_order` 升序
3. 同优先级同 execution_order 按入队顺序（FIFO）

#### EventLoop（事件循环）

**职责**：驱动整个引擎的运行。从 EventQueue 取事件，分发给对应的处理器，直到队列为空。

**属性**：
| 属性 | 类型 | 说明 |
|------|------|------|
| `event_queue` | EventQueue | 事件队列 |
| `handlers` | dict[str, callable] | 事件类型 → 处理函数映射 |
| `engine_context` | EngineContext | 引擎上下文（数据、公式路由器等） |
| `max_iterations` | int | 单 tick 最大迭代次数（防死循环） |

**方法**：
| 方法 | 说明 |
|------|------|
| `register_handler(event_type, handler)` | 注册事件处理器 |
| `run_until_empty()` | 运行事件循环直到队列为空 |
| `run_one_tick(tick_event)` | 处理一个 tick 的所有事件 |

#### EngineContext（引擎上下文）

**职责**：封装引擎运行时的全局依赖，避免到处传递参数。

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

#### FilterEvaluator（过滤器评估器）

**职责**：专门负责过滤计算，从 ConditionNode 中拆出。

**属性**：
| 属性 | 类型 | 说明 |
|------|------|------|
| `filter_spec` | dict | 过滤规格（编译期预计算） |
| `filter_cache` | FilterCache | 过滤结果缓存 |

**方法**：
| 方法 | 说明 |
|------|------|
| `evaluate(input_stocks, context)` | 执行过滤计算，返回通过的股票 |
| `evaluate_incremental(added, removed, current_stocks, context)` | 增量过滤（只计算变化的部分） |
| `invalidate_cache()` | 使缓存失效 |

---

### 1.5 Engine 主类（协调者）

#### EventDrivenEngine

**职责**：引擎协调者，负责拓扑构建、事件循环管理、组件装配、生命周期管理。
不是上帝类，只做协调，具体计算委托给 Node/Edge/EventLoop。

**属性**：
| 属性 | 类型 | 说明 |
|------|------|------|
| `event_loop` | EventLoop | 事件循环 |
| `nodes` | dict[str, Node] | 节点字典 {node_id: Node} |
| `edges` | dict[str, Edge] | 边字典 {edge_id: Edge} |
| `topology` | Topology | 拓扑关系（连接关系） |
| `formula_router` | FormulaRouter | 公式路由器（复用现有） |
| `data_query` | DataQuery | 数据查询（复用现有） |
| `compiled` | CompiledSchedule | 编译产物（复用现有结构） |
| `config_store` | ConfigStore | 配置表存储（复用现有） |
| `is_running` | bool | 是否运行中 |
| `is_paused` | bool | 是否暂停 |
| `metrics` | EngineMetrics | 性能指标收集 |

**方法**：
| 方法 | 说明 |
|------|------|
| `compile(pool_config)` | 编译池配置：构建 Node/Edge 对象、建立拓扑、计算执行顺序 |
| `start()` | 启动引擎 |
| `stop()` | 停止引擎 |
| `pause()` / `resume()` | 暂停/恢复 |
| `on_data(bar_data)` | 输入行情数据，产生 DataUpdatedEvent 入队 |
| `on_tick(tick_ts, bar_time)` | 时钟 tick，产生 TimerTickEvent 并入队，然后运行事件循环 |
| `get_node_stocks(node_id)` | 获取节点股票 |
| `get_metrics()` | 获取性能指标 |

---

### 1.6 辅助类（v1.1 更新）

#### Topology（拓扑）

**职责**：封装节点连接关系，与执行顺序分离。

**属性**：
- `adjacency_out` — 出边邻接表
- `adjacency_in` — 入边邻接表
- `depths` — 节点深度
- `has_cycle` — 是否有环
- `cycle_nodes` — 环中的节点列表

**方法**：
- `get_out_edges(node_id)` — 获取节点的出边
- `get_in_edges(node_id)` — 获取节点的入边
- `topological_sort()` — 拓扑排序
- `detect_cycles()` — 环检测
- `get_downstream(node_id)` — 获取所有下游节点（递归）
- `get_upstream(node_id)` — 获取所有上游节点（递归）

#### EngineMetrics（引擎指标）

**职责**：收集和管理引擎的性能指标。

**属性**：
| 属性 | 类型 | 说明 |
|------|------|------|
| `tick_count` | int | tick 总数 |
| `total_tick_time` | float | 总 tick 耗时（秒） |
| `events_processed` | int | 处理的事件总数 |
| `edges_executed` | int | 边执行总数 |
| `cache_hits` | int | 缓存命中次数 |
| `cache_misses` | int | 缓存未命中次数 |
| `max_queue_size` | int | 事件队列最大长度 |
| `last_tick_events` | int | 上一 tick 处理的事件数 |
| `last_tick_edges` | int | 上一 tick 执行的边数 |

**方法**：
| 方法 | 说明 |
|------|------|
| `record_tick_start()` | 记录 tick 开始 |
| `record_tick_end()` | 记录 tick 结束 |
| `record_event_processed()` | 记录事件处理 |
| `record_edge_executed()` | 记录边执行 |
| `record_cache_hit()` | 记录缓存命中 |
| `record_cache_miss()` | 记录缓存未命中 |
| `get_summary()` | 获取指标摘要字典 |
| `reset()` | 重置所有指标 |

---

### 1.7 类关系图（v1.1）

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
     └──────────┘    │ (基类)   │                  │
                       └────┬─────┘            ┌───┴────┐
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

     FormulaRouter ◄────── 依赖 ──────── FilterEvaluator
     DataQuery ◄────────── 依赖 ──────── SourceNode
```

---

## 2. 事件流设计（v1.1 重写：真·事件驱动）

### 2.1 核心思想：事件队列是心脏

**v1.0 的问题**：每 tick 遍历所有边检查条件 → 本质是轮询，事件只是"置标记"。

**v1.1 的设计**：事件队列是核心，所有计算都由事件驱动。

```
                    ┌─────────────┐
                    │  EventQueue │  ← 事件队列（优先级队列）
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
         TimerEvent   DataEvent    NodeEvent
              │            │            │
              └────────────┼────────────┘
                           ▼
                    ┌─────────────┐
                    │ EventLoop   │  ← 事件循环：出队 → 分发 → 处理
                    └──────┬──────┘
                           │
                           ▼
                    产生新事件 → 入队 → 继续循环
                           │
                           ▼
                    直到队列为空
```

**关键区别**：
| 维度 | v1.0（伪事件驱动） | v1.1（真·事件驱动） |
|------|-------------------|-------------------|
| 核心 | 边列表遍历 | 事件队列 |
| 驱动力 | 时钟 tick | 事件（数据变化、节点变化、时钟） |
| 计算触发 | 每 tick 检查所有边 | 只有变化的路径才计算 |
| 级联方式 | 一次遍历（错误假设） | 事件级联，直到队列为空 |
| 性能 | O(n) 每 tick（n=边数） | O(k) 每 tick（k=变化路径上的边数） |

---

### 2.2 事件类型与处理逻辑

#### 事件类型总览

| 事件类型 | 生产者 | 消费者 | 入队时机 | 处理动作 |
|---------|--------|--------|---------|---------|
| `TimerTickEvent` | Engine.on_tick() | EventLoop | 每个时钟 tick | 推进时间；检查所有 TimingGate 的状态变化；触发 TTL 检查；触发源节点刷新 |
| `DataUpdatedEvent` | Engine.on_data() | EventLoop | 行情数据到达 | 更新全局 bar_data；置 data_dirty=True；把所有依赖行情的条件边加入执行队列 |
| `EdgeExecuteEvent` | NodeStockChangedHandler / DataUpdatedHandler / TimerTickHandler | EventLoop | 边需要执行时 | 执行边的过滤/流转逻辑；目标节点变化则产生 NodeStockChangedEvent |
| `NodeStockChangedEvent` | Edge.execute() / Node.update_stocks() | EventLoop | 节点股票集变化时 | 把该节点的所有出边加入执行队列（按 execution_order 排序） |
| `PoolChangedEvent` | StatePoolNode | EventQueue 消费者 | 股票入池/出池时 | 外部消费（UI更新、信号生成等），不参与内部级联 |
| `AlertEvent` | StatePoolNode | EventQueue 消费者 | 告警触发时 | 外部消费（声音、弹窗、保存板块等） |
| `SignalEvent` | StatePoolNode（目标池） | EventQueue 消费者 | 目标池入/出时 | 外部消费（交易信号） |

---

### 2.3 核心事件流详解

#### 流 1：数据到达 → 条件边执行 → 级联传播

```
DataUpdatedEvent (priority=1)
    │
    ▼
Handler: on_data_updated(event)
    │
    ├─ 1. 更新 context.bar_data = event.bar_data
    ├─ 2. context.data_dirty = True
    │
    └─ 3. 找出所有 ConditionalEdge（依赖行情的边）
           按 execution_order 排序
           逐条调用 event_queue.enqueue_edge_execute(edge_id)
           （自动去重，已在队列中的不加）
    │
    ▼
[事件队列中现在有 N 个 EdgeExecuteEvent]
    │
    ▼
逐个处理 EdgeExecuteEvent (priority=2, 按 execution_order 排)
    │
    ▼
Handler: on_edge_execute(event)
    │
    ├─ 1. 从 edges 字典取出 edge 对象
    ├─ 2. 检查 should_execute(now_ts, data_dirty)
    │   ├─ 不满足：跳过（这可能发生，比如 timing gate 没到）
    │   └─ 满足：继续执行
    │
    ├─ 3. edge.execute(context) → 执行过滤/流转
    │   │
    │   └─ 目标节点股票变化了吗？
    │       ├─ 没变：什么都不做
    │       └─ 变了：产生 NodeStockChangedEvent 入队
    │
    └─ 4. 从 pending_edges 中移除该 edge_id
    │
    ▼
NodeStockChangedEvent (priority=3)
    │
    ▼
Handler: on_node_stock_changed(event)
    │
    ├─ 1. 找出该节点的所有出边
    │
    ├─ 2. 对每条出边：
    │   ├─ 检查 edge.should_execute(now_ts, data_dirty)
    │   │   └─ 注意：UnconditionalEdge 只要源节点变了就执行
    │   └─ 满足则调用 enqueue_edge_execute(edge_id)
    │       （自动去重，已在队列中的不加）
    │
    └─ 3. 清除该节点的脏标记
    │
    ▼
[新的 EdgeExecuteEvent 入队，继续循环]
    │
    ▼
... 直到队列为空 ...
```

**关键点**：
1. **级联是自动的**：一条边执行导致目标节点变化 → 目标节点的出边自动入队 → 继续执行
2. **去重是必须的**：同一条边可能被多个上游触发，但只需要执行一次
3. **执行顺序有保证**：同一优先级的 EdgeExecuteEvent 按 execution_order 排序

---

#### 流 2：时钟 tick → 时机门控变化 → 触发边执行

```
TimerTickEvent (priority=0)
    │
    ▼
Handler: on_timer_tick(event)
    │
    ├─ 1. 更新 context.now_ts = event.tick_ts
    ├─ 2. 更新 context.bar_time = event.bar_time
    │
    ├─ 3. 遍历所有 ConditionalEdge 的 TimingGate
    │   │
    │   └─ 对每个 gate：
    │       ├─ old_fired = gate.is_fired
    │       ├─ new_fired = gate.should_trigger(now_ts, bar_time, pool_start_ts)
    │       ├─ gate.is_fired = new_fired
    │       │
    │       └─ 如果 old_fired == False AND new_fired == True：
    │           （时机窗口刚打开）
    │           └─ enqueue_edge_execute(edge_id, reason="timing_start")
    │
    ├─ 4. 遍历所有 StatePoolNode，执行 check_ttl(now_ts)
    │   └─ 有过期股票 → update_stocks() → NodeStockChangedEvent 入队
    │
    └─ 5. 遍历所有 SourceNode，检查 should_refresh(now_ts)
        └─ 需要刷新 → refresh() → update_stocks() → NodeStockChangedEvent 入队
    │
    ▼
[新的事件入队，继续循环]
```

**关键点**：
1. **时钟 tick 只做"状态变化检测"**，不直接执行边
2. **只有时机状态变化的边才入队**（从 false 变 true），不是每 tick 都入队
3. **TTL 检查和源节点刷新**也是由时钟触发的，但产生的是 NodeStockChangedEvent

---

#### 流 3：源节点刷新 → 下游级联

```
SourceNode.refresh()
    │
    ├─ 从 data_query 获取新的股票列表
    ├─ 与 snapshot 比较
    ├─ 相同：什么都不做
    └─ 不同：update_stocks(new_stocks)
        │
        └─ NodeStockChangedEvent 入队
            │
            ▼
        下游 ConditionalEdge 入队执行
            │
            ▼
        条件节点变化 → UnconditionalEdge 入队执行
            │
            ▼
        状态池变化 → 更下游的边继续级联
            │
            ▼
        ... 直到队列为空 ...
```

---

### 2.4 事件顺序保证

#### 优先级保证

事件队列是优先级队列，高优先级事件先处理：
- TimerTickEvent（最高）→ 先推进时间
- DataUpdatedEvent → 再更新数据
- EdgeExecuteEvent → 再执行边（按 execution_order 排）
- NodeStockChangedEvent → 再处理节点变化（把下游边加入队列）
- PoolChangedEvent / AlertEvent / SignalEvent（最低）→ 最后处理输出事件

#### 同优先级的执行顺序保证

**EdgeExecuteEvent 的排序**：
1. 先按 execution_order 升序（数字越小越先执行）
2. 同 execution_order 按入队顺序（FIFO）

**为什么 NodeStockChangedEvent 优先级比 EdgeExecuteEvent 低？**

这是 v1.1 的关键设计决策。让我们通过一个例子来说明：

```
拓扑：A → edge1 → B → edge2 → C
execution_order: edge1=1, edge2=2

假设 A 节点变化了，会发生什么？
```

**如果 NodeStockChangedEvent 优先级更高**：
```
队列：[NodeChanged(A)]
  → 处理 NodeChanged(A)
    → 把 edge1 加入队列
队列：[EdgeExecute(edge1)]
  → 处理 EdgeExecute(edge1)
    → B 节点变化
    → 产生 NodeChanged(B) 入队
队列：[NodeChanged(B)]
  → 处理 NodeChanged(B)
    → 把 edge2 加入队列
队列：[EdgeExecute(edge2)]
  → 处理 EdgeExecute(edge2)
    → C 节点变化
    → 产生 NodeChanged(C) 入队
队列：[NodeChanged(C)]
  → 处理 NodeChanged(C)
    → 没有下游
队列为空
```

结果：需要 6 次事件处理才能完成级联。

**如果 EdgeExecuteEvent 优先级更高（v1.1 的选择）**：
```
初始队列：[NodeChanged(A)]
  → 处理 NodeChanged(A)
    → 把 edge1 加入队列
队列：[EdgeExecute(edge1)]
  → 处理 EdgeExecute(edge1)
    → B 节点变化
    → 产生 NodeChanged(B) 入队
队列：[EdgeExecute(edge2), NodeChanged(B)]  ← 等等，不对！
```

哦，这里有问题。让我重新想清楚...

实际上，正确的流程应该是：

```
初始队列：[NodeChanged(A, priority=3)]
  → 出队 NodeChanged(A) （因为队列里只有它）
  → 处理：把 edge1 加入队列
队列：[EdgeExecute(edge1, priority=2)]
  → 出队 EdgeExecute(edge1) （优先级2比3高）
  → 执行 edge1
  → B 节点变化，产生 NodeChanged(B, priority=3) 入队
队列：[NodeChanged(B, priority=3)]
  → 出队 NodeChanged(B)
  → 处理：把 edge2 加入队列
队列：[EdgeExecute(edge2, priority=2)]
  → 出队 EdgeExecute(edge2)
  → 执行 edge2
  → C 节点变化，产生 NodeChanged(C, priority=3) 入队
队列：[NodeChanged(C, priority=3)]
  → 出队 NodeChanged(C)
  → 处理：没有下游
队列为空
```

结果：和之前一样，也是 6 次事件处理。

**那优先级设计的意义在哪里？**

意义在于：**当队列中有多个事件时，保证执行顺序的正确性**。

考虑一个更复杂的场景：
```
拓扑：
  A → edge1 → B
  A → edge2 → C
  B → edge3 → D
  C → edge4 → D

execution_order: edge1=1, edge2=2, edge3=3, edge4=4
```

如果 A 节点变化，v1.1 的流程：
```
初始：[NodeChanged(A)]
  → 处理 NodeChanged(A)
    → enqueue edge1, edge2 （按 execution_order 排序）
队列：[EdgeExecute(edge1), EdgeExecute(edge2)]
  → 出队 edge1，执行
    → B 变化 → NodeChanged(B) 入队
队列：[EdgeExecute(edge2), NodeChanged(B)]
  → 出队 edge2，执行 （EdgeExecute 优先级更高）
    → C 变化 → NodeChanged(C) 入队
队列：[NodeChanged(B), NodeChanged(C)]
  → 出队 NodeChanged(B)
    → enqueue edge3
队列：[EdgeExecute(edge3), NodeChanged(C)]
  → 出队 edge3，执行 （EdgeExecute 优先级更高）
    → D 变化 → NodeChanged(D) 入队
队列：[NodeChanged(C), NodeChanged(D)]
  → 出队 NodeChanged(C)
    → enqueue edge4
队列：[EdgeExecute(edge4), NodeChanged(D)]
  → 出队 edge4，执行
    → D 可能再变化 → NodeChanged(D) 入队
队列：[NodeChanged(D), NodeChanged(D)]
  → ... 处理 D 的变化 ...
```

**EdgeExecute 优先级更高的好处**：
- 同一层的边先全部执行完，再处理下一层的节点变化
- 更符合"按执行顺序处理"的直觉
- 避免"处理一个节点变化 → 执行一条边 → 又处理节点变化 → 又执行一条边"的交错

**结论**：EdgeExecuteEvent 优先级高于 NodeStockChangedEvent，是为了保证"同层边按 execution_order 连续执行"。

---

### 2.5 单线程模型与并发安全

**整个引擎在单线程中运行**，事件循环是串行的。

这意味着：
- 不存在并发问题，无需锁
- 事件处理是原子的，不会被打断
- `on_data()` 和 `on_tick()` 必须在同一个线程中调用（或者通过队列串行化）

**外部调用的线程安全**：
- 如果 `on_data()` 从另一个线程调用，必须把 DataUpdatedEvent 放入线程安全的队列
- 事件循环线程从队列中取事件处理
- v1.1 假设外部调用都在同一个线程，或者调用方自己保证串行化

---

## 3. 核心流程（v1.1 重写：事件循环 + 队列调度）

### 3.1 完整事件循环伪代码

```python
class EventLoop:
    def __init__(self, engine_context):
        self.queue = EventQueue()
        self.context = engine_context
        self.handlers = {}
        self.max_iterations = 10000  # 防死循环
        self.iteration_count = 0

    def register_handler(self, event_type, handler):
        self.handlers[event_type] = handler

    def run_until_empty(self):
        """运行事件循环直到队列为空"""
        self.iteration_count = 0
        while not self.queue.is_empty():
            if self.iteration_count >= self.max_iterations:
                raise EventLoopError(
                    f"事件循环迭代次数超过上限 {self.max_iterations}，"
                    f"可能存在死循环。当前队列大小: {self.queue.size()}"
                )

            event = self.queue.dequeue()
            handler = self.handlers.get(event.event_type)
            if handler:
                try:
                    handler(event, self.context, self.queue)
                except Exception as e:
                    logger.error(f"事件处理异常: {event.event_type}, 错误: {e}")
                    # 继续处理下一个事件，不中断整个循环

            self.iteration_count += 1
```

---

### 3.2 一个 tick 的完整生命周期

```
调用 engine.on_tick(tick_ts, bar_time)
    │
    ├─ 1. 创建 TimerTickEvent，入队
    │
    ├─ 2. 如果有新的行情数据：
    │   └─ 创建 DataUpdatedEvent，入队
    │
    ├─ 3. 调用 event_loop.run_until_empty()
    │   │
    │   └─ 事件循环开始处理：
    │       │
    │       ├─ 阶段 A：TimerTickEvent（priority=0）
    │       │   ├─ 更新时间
    │       │   ├─ 检查 TimingGate 状态变化
    │       │   ├─ 检查 TTL 过期
    │       │   └─ 检查源节点刷新
    │       │
    │       ├─ 阶段 B：DataUpdatedEvent（priority=1）
    │       │   ├─ 更新行情数据
    │       │   ├─ 置 data_dirty=True
    │       │   └─ 所有条件边入队执行
    │       │
    │       ├─ 阶段 C：级联传播（EdgeExecute + NodeChanged 交替）
    │       │   └─ 事件循环自动处理，直到没有新的边需要执行
    │       │
    │       └─ 阶段 D：输出事件（PoolChanged / Alert / Signal）
    │           └─ 这些事件只是被"记录"下来，供外部消费
    │
    ├─ 4. tick 结束，重置状态：
    │   ├─ context.data_dirty = False
    │   ├─ 清除所有节点的脏标记
    │   └─ 更新 metrics
    │
    └─ 5. 返回输出事件列表（供外部消费）
```

---

### 3.3 级联传播的正确性论证

**问题**：事件队列 + 级联传播，能保证所有受影响的边都被执行吗？

**结论**：是的，只要拓扑是 DAG（有向无环图），就能保证。

**论证**：

1. **初始触发**：某个事件（数据更新、时钟、节点变化）导致第一批边入队。

2. **执行与传播**：每条边执行后，如果目标节点变化，产生 NodeStockChangedEvent。
   NodeStockChangedEvent 的处理会把目标节点的所有出边入队。

3. **终止条件**：
   - 如果拓扑是 DAG，级联最终会到达没有出边的节点（Sink）
   - 每条边最多执行一次（因为 pending_edges 去重）
   - 边的数量是有限的，所以事件循环必然终止

4. **完整性**：
   - 假设边 E 的源节点 S 在本 tick 内变化了
   - S 的变化会产生 NodeStockChangedEvent
   - NodeStockChangedEvent 的处理会把 E 加入队列（如果 E.should_execute() 为真）
   - 所以 E 一定会被执行
   - 由数学归纳法，所有受影响的边都会被执行

5. **执行顺序**：
   - 同优先级的 EdgeExecuteEvent 按 execution_order 排序
   - 这保证了"同时都可执行时，先执行 execution_order 小的边"

**环的情况**：
如果拓扑有环，级联可能会无限循环。v1.1 通过两种机制防止：
1. **编译期环检测**：编译时检测并报错（见第 5 章）
2. **运行期防死循环**：max_iterations 上限

---

### 3.4 时序图（v1.1 版）

```
行情源           Engine        EventQueue      EventLoop     Edge1     NodeB    Edge2     NodeC
  │              │                │              │          │         │        │         │
  │── bar_data ─→│                │              │          │         │        │         │
  │              │── enqueue ────→│              │          │         │        │         │
  │              │  DataUpdated   │              │          │         │        │         │
  │              │── run_until_empty ───────────→│          │         │        │         │
  │              │                │              │          │         │        │         │
  │              │                │── dequeue ──→│          │         │        │         │
  │              │                │  DataUpdated │          │         │        │         │
  │              │                │              │── handler→│         │        │         │
  │              │                │              │  把所有条件边入队    │        │         │
  │              │                │←── enqueue ──┤  (edge1, edge2)   │        │         │
  │              │                │              │          │         │        │         │
  │              │                │── dequeue ──→│          │         │        │         │
  │              │                │  EdgeExe(1)  │          │         │        │         │
  │              │                │              │── execute────────→│         │        │
  │              │                │              │          │ 过滤计算 │        │         │
  │              │                │              │←─ result ──────────│         │        │
  │              │                │              │          │         │        │         │
  │              │                │              │  B 变化了吗？       │        │         │
  │              │                │              │─── 是 ──→│         │        │         │
  │              │                │              │  update_stocks()   │        │         │
  │              │                │←─ enqueue ───┤         │         │        │         │
  │              │                │  NodeChanged(B)      │         │        │         │
  │              │                │              │          │         │        │         │
  │              │                │── dequeue ──→│          │         │        │         │
  │              │                │  EdgeExe(2)  │  ← 优先级更高，先处理这个
  │              │                │              │── execute──────────────────→│         │
  │              │                │              │          │         │        │ 过滤计算 │
  │              │                │              │←─ result ────────────────────│         │
  │              │                │              │          │         │        │         │
  │              │                │              │  C 变化了吗？                │         │
  │              │                │              │─── 是 ──→│         │        │         │
  │              │                │←─ enqueue ───┤         │         │        │         │
  │              │                │  NodeChanged(C)      │         │        │         │
  │              │                │              │          │         │        │         │
  │              │                │── dequeue ──→│          │         │        │         │
  │              │                │  NodeChanged(B)      │         │        │         │
  │              │                │              │  处理 B 的变化：把 B 的出边加入队列
  │              │                │              │  （edge2 已经在队列里了，去重）
  │              │                │              │          │         │        │         │
  │              │                │── dequeue ──→│          │         │        │         │
  │              │                │  NodeChanged(C)      │         │        │         │
  │              │                │              │  处理 C 的变化：没有下游
  │              │                │              │          │         │        │         │
  │              │                │  （空）      │          │         │        │         │
  │              │←─ 返回 ────────┤              │          │         │        │         │
  │              │   输出事件列表   │              │          │         │        │         │
```

---

### 3.5 与旧引擎行为一致性的论证

**旧引擎的行为**（根据现有代码推断）：
- 每 tick 按拓扑序遍历所有边
- 每条边检查是否满足条件（时机 + 数据/节点变化）
- 满足则执行，更新目标节点
- 一次遍历完成后，检查是否有节点变化了
- 如果有，再遍历一遍（多轮传播，直到稳定）
- 或者是固定传播 N 轮

**新引擎的行为**：
- 事件驱动，只有变化的路径才执行
- 级联传播自动进行，直到队列为空
- 同层边按 execution_order 排序

**等价性的关键**：
1. **最终状态一致**：只要所有受影响的边都被执行了，最终状态就一致
2. **执行顺序可能不同**：旧引擎是"按拓扑序多轮遍历"，新引擎是"事件级联"
3. **同一 tick 内的中间状态可能不同**，但最终状态相同

**为什么最终状态一定相同？**
因为：
- 两个引擎的"边执行逻辑"是一样的（同样的过滤条件、同样的流转规则）
- 两个引擎都会执行所有"应该执行"的边
- 只要执行的边集合相同，最终的节点状态就相同
- （假设边的执行是幂等的，且没有副作用依赖执行顺序）

**什么情况下执行顺序会影响结果？**
如果边的执行有副作用，且副作用依赖执行顺序，那么结果可能不同。
例如：
- 股票从 A 池转移到 B 池，同时从 A 池转移到 C 池
- 如果先执行 A→B，再执行 A→C，那么 B 和 C 都能拿到股票
- 如果边的逻辑是"移动"而不是"复制"，那么执行顺序会影响结果

但在股票池系统中，边的逻辑通常是"复制"（条件转移），而不是"移动"。
所以执行顺序不影响最终结果。

---

## 4. 数据驱动增量计算（v1.1 细化）

### 4.1 多层时间戳机制（不变）

| 层级 | 时间戳 | 存储位置 | 用途 |
|------|--------|---------|------|
| 全局行情 | `latest_tick_ts` | EngineContext | 最新行情数据的时间戳 |
| 全局K线 | `current_bar_time` | EngineContext | 当前K线的时间 |
| 节点级 | `node.data_ts` | Node | 节点股票集最后变化的时间戳 |
| 边级 | `edge.last_executed_ts` | Edge | 边最后执行的时间戳 |
| 公式缓存级 | `cache_entry.ts` | FilterCache | 公式结果缓存的时间戳 |

### 4.2 脏标记机制（v1.1 细化）

**两类脏标记**：

| 脏标记类型 | 存储 | 置位时机 | 清除时机 |
|-----------|------|---------|---------|
| 数据脏 `data_dirty` | EngineContext | 行情数据变化时 | tick 末 |
| 节点脏 `is_dirty` | Node | 节点股票变化时 | NodeStockChangedEvent 处理后 |

**v1.1 变化**：
- 脏标记不再由 DirtyManager 统一管理，而是分散在各自的对象中
- 节点脏标记在 NodeStockChangedEvent 处理时清除（而不是 tick 末）
- 因为 NodeStockChangedEvent 处理完后，该节点的所有出边都已经入队了

### 4.3 增量计算策略（v1.1 新增：增量过滤）

**四级跳过机制**：

| 级别 | 检查点 | 跳过条件 | 节省的计算 |
|------|--------|---------|-----------|
| L1 | 数据层 | `data_hash == last_data_hash` | 全部计算（零开销） |
| L2 | 节点层 | `not node.is_dirty` | 该节点的所有出边计算 |
| L3 | 边层 | `not edge.should_execute()` | 该边的过滤+传播 |
| L4 | 公式层 | `filter_cache` 命中 | 公式计算本身 |

**v1.1 新增：L5 增量过滤**：

| 级别 | 检查点 | 跳过条件 | 节省的计算 |
|------|--------|---------|-----------|
| L5 | 增量层 | 只有少数股票变化 | 只计算变化的股票，不重算全部 |

**增量过滤的实现思路**：

当源节点只有少量股票变化时（比如 1000 只股票中只有 2 只变化）：
- 对新增的股票：执行过滤计算，通过的加入目标节点
- 对移除的股票：直接从目标节点移除
- 对不变的股票：不重新计算

**适用条件**：
- 公式是逐股票计算的（大多数情况都是）
- 变化的股票数量远小于总数量

**不适用情况**：
- 排名类公式（"排名前 10"）—— 一只股票变化可能影响所有股票的排名
- 集合运算类公式（交集、并集等）—— 需要完整集合才能计算

**实现策略**：
- FilterEvaluator 有两个方法：`evaluate()`（全量）和 `evaluate_incremental()`（增量）
- 边执行时，根据变化股票占比决定用哪个方法
- 阈值：变化股票占比 < 10% 时用增量，否则用全量

### 4.4 公式结果缓存策略（v1.1 修正）

**缓存键构成（修正版）**：
```
cache_key = md5(
    formula_id +          # 公式标识
    frozenset(symbols) +  # 股票代码集合（frozenset，顺序无关）
    period +              # 周期
    bar_data_hash         # 行情数据哈希
)
```

**v1.1 修正点**：
- symbols 用 frozenset 而不是 list，保证顺序不影响哈希
- 避免了"同样的股票，顺序不同导致缓存不命中"的问题

**缓存 TTL 策略**（不变）：

| 周期 | TTL | 说明 |
|------|-----|------|
| tick | 0（不缓存） | 实时变化太快 |
| 1m | 60s | 分钟级数据 |
| 5m / 15m / 30m / 60m | 300s | 分钟级 |
| 1d / 周 / 月 | 86400s | 日线级 |

---

## 5. 执行顺序与拓扑分离（v1.1 重写：彻底讲清楚）

### 5.1 拓扑是什么

**拓扑（Topology）= 节点和边的连接关系（结构图）**

拓扑回答的问题：**谁能影响谁？**

- A 节点有一条边指向 B 节点 → A 能影响 B
- A 变化了，B 可能需要重新计算
- 拓扑是**结构属性**，是静态的（编译期确定，运行期不变）

**拓扑的表示**：
- 邻接表（adjacency list）
- 节点深度（从源节点到该节点的最长路径长度）
- 环检测结果（是否有环，环在哪里）

**拓扑的用途**：
1. 确定数据流向（从上游到下游）
2. 确定级联传播的路径
3. 检测循环依赖
4. 计算节点深度（用于可视化）

---

### 5.2 执行顺序是什么

**执行顺序（ExecutionOrder）= 边的处理次序（行为顺序）**

执行顺序回答的问题：**当多条边都可以执行时，先执行哪条？**

- 两条边都满足条件，都要执行 → 先执行 execution_order 小的
- 执行顺序是**行为属性**，是用户指定的（编译期确定，运行期不变）

**执行顺序的表示**：
- 每条边有一个 `execution_order` 整数属性
- 数字越小，优先级越高（越先执行）
- 事件队列中，同优先级的 EdgeExecuteEvent 按 execution_order 排序

**执行顺序的来源**：
1. **用户指定**：pool_config 中边的顺序（列表顺序即执行顺序）
2. **拓扑深度（保底）**：如果用户没指定，按源节点深度排序
   - 深度小的边先执行（上游先执行）
   - 同深度的边，按边 ID 排序（确定性）

---

### 5.3 两者的关系

**一句话总结**：
> **拓扑决定"谁能影响谁"，执行顺序决定"同时都满足时先算谁"。**

**更详细的对比**：

| 维度 | 拓扑（Topology） | 执行顺序（ExecutionOrder） |
|------|-----------------|---------------------------|
| 本质 | 连接关系（结构） | 处理次序（行为） |
| 回答的问题 | 谁能影响谁？ | 同时都满足时先算谁？ |
| 属性类型 | 结构属性 | 行为属性 |
| 变化频率 | 静态（编译期确定） | 静态（编译期确定） |
| 决定因素 | 节点和边的连接 | 用户指定 + 拓扑保底 |
| 运行期作用 | 决定级联传播路径 | 决定事件队列中的排序 |

**两者怎么配合？**

```
编译期：
  1. 从 pool_config 构建拓扑（Topology）
  2. 检测拓扑是否有环（有环则报错）
  3. 计算每个节点的深度
  4. 根据用户指定的边顺序 + 拓扑深度，计算每条边的 execution_order
     - 用户指定的顺序优先
     - 但必须满足：源节点深度 <= 目标节点深度（否则强制调整）
     - 调整时发出警告

运行期：
  1. 事件队列中的 EdgeExecuteEvent 按 execution_order 排序
  2. 级联传播由拓扑决定（源节点变化 → 目标节点的出边入队）
  3. 执行顺序只影响"同层边的相对顺序"，不影响传播路径
```

---

### 5.4 级联传播模型

**v1.0 的错误**："一次遍历即可完成所有级联更新"

**为什么错误？**
因为 execution_order 是边的顺序，不是节点的顺序。
一条边的目标节点变脏后，该节点的出边可能在 execution_order 中排在当前边之前。

举个例子：
```
拓扑：A → edge1 → B → edge2 → C
execution_order: edge2=1, edge1=2 （用户故意把下游边排前面）

如果 A 变化了，会发生什么？
```

**v1.0 的"一次遍历"模型**：
```
按 execution_order 遍历：
  1. edge2: B 还没变（因为 edge1 还没执行）→ 不执行
  2. edge1: A 变了 → 执行 → B 变化
遍历结束。

结果：edge2 没执行，C 没更新。错误！
```

**v1.1 的事件级联模型**：
```
初始：A 变化 → NodeStockChangedEvent 入队
  → 处理 NodeChanged(A)
    → 把 edge1 加入队列（edge2 是 B 的出边，A 变化不直接影响 edge2）

队列：[EdgeExecute(edge1)]
  → 执行 edge1
    → B 变化 → NodeChanged(B) 入队

队列：[NodeChanged(B)]
  → 处理 NodeChanged(B)
    → 把 edge2 加入队列

队列：[EdgeExecute(edge2)]
  → 执行 edge2
    → C 变化 → NodeChanged(C) 入队

队列：[NodeChanged(C)]
  → 处理 NodeChanged(C)
    → 没有下游

队列为空。

结果：edge1 和 edge2 都执行了，C 更新了。正确！
```

**结论**：
- 事件级联模型天然支持多轮传播
- 不需要"一次遍历"的错误假设
- 执行顺序只影响同层边的相对顺序，不影响传播的完整性

---

### 5.5 两种级联模型对比与选择

在设计过程中，我们考虑了两种级联模型：

#### 方案 A：事件队列 + 优先级排序（v1.1 选择的方案）

```
核心思想：
  - 所有待执行的边都在事件队列中
  - 按 execution_order 排序
  - 执行一条边 → 目标节点变化 → 把目标的出边加入队列
  - 循环直到队列为空
```

**优点**：
- 真正的事件驱动，只有变化的路径才执行
- 性能好（k 条边 vs n 条边）
- 级联传播自动完成，不需要额外机制
- execution_order 自然融入队列排序

**缺点**：
- 实现稍复杂（需要事件队列、去重等）
- 调试难度稍高（事件流不如过程式直观）

#### 方案 B：分轮次模型（每轮按 execution_order 跑全量）

```
核心思想：
  - 每 tick 分多轮（round）
  - 每轮按 execution_order 遍历所有边
  - 本轮产生的脏标记下一轮生效
  - 连续两轮没有节点变化，就停止
```

**优点**：
- 实现简单（两层循环）
- 调试容易（每轮状态清晰）
- 与旧引擎行为更接近

**缺点**：
- 每轮都要遍历所有边（O(n) 每轮）
- 不是真正的事件驱动（还是轮询）
- 边数多、轮数多时性能差

#### 为什么选方案 A？

1. **符合"真·事件驱动"的设计目标**：方案 B 本质还是轮询，只是多轮轮询。
2. **性能更好**：对于大多数情况，变化的边只是少数，事件队列模型的性能远好于多轮遍历。
3. **更可扩展**：未来如果要支持异步事件、并发执行，事件队列模型更容易扩展。
4. **更优雅**：级联传播是"自然发生"的，不需要额外的"轮次"概念。

---

### 5.6 编译期执行顺序验证算法

```
输入：
  - 拓扑（节点、边、邻接表）
  - 用户指定的边顺序（user_order: list[edge_id]）

输出：
  - execution_order: dict[edge_id, int]
  - warnings: list[str]（警告信息）

算法：
  1. 计算每个节点的拓扑深度（BFS 从源节点开始）

  2. 验证用户指定的顺序是否满足拓扑依赖：
     - 对每条边 e，检查：depth[e.source] <= depth[e.target]
     - 这总是满足的，因为边就是从浅到深的
     - 真正的约束是：如果边 e1 的目标是边 e2 的源，那么 e1 应该在 e2 前面
     - 也就是：如果 e1.target == e2.source，那么 order[e1] < order[e2]

  3. 检查用户顺序是否满足上述约束：
     - 构建边的依赖图：e1 → e2 表示 e1 必须在 e2 前面
     - 对用户顺序做拓扑排序验证
     - 如果不满足，发出警告，并强制调整为拓扑序

  4. 给每条边分配 execution_order：
     - 首先按用户指定的顺序（如果满足拓扑约束）
     - 不满足的部分，按拓扑深度排序插入
     - 最终保证：execution_order 是一个整数序列，从 0 开始递增
```

---

### 5.7 循环检测机制

**为什么需要循环检测？**
- 如果拓扑有环，事件级联可能会无限循环
- 编译期检测可以提前发现问题，避免运行期死循环

**编译期环检测**：
```
算法：DFS 检测环
  - 对每个未访问的节点，启动 DFS
  - 维护一个"当前递归栈"
  - 如果访问到一个已经在递归栈中的节点，说明有环
  - 记录环的路径

输出：
  - has_cycle: bool
  - cycle_nodes: list[str]（环中的节点）
  - cycle_edges: list[str]（环中的边）
```

**环的处理策略**：
- **严格模式（默认）**：检测到环直接报错，拒绝编译
- **宽容模式**：检测到环发出警告，但允许编译（运行期靠 max_iterations 兜底）

**运行期防死循环**：
- EventLoop.max_iterations = 10000（默认）
- 超过上限抛出异常，终止事件循环
- 同时记录日志，便于调试

---

## 6. 表驱动配置（v1.1 收敛）

### 6.1 配置表收敛策略

**v1.0 的问题**：从 50+ 砍到 12 张，但本质还是"查表驱动"而非"表驱动"。

**v1.1 的收敛原则**：
1. **固定不变的东西，直接写代码**（不需要配置表）
2. **经常变化的东西，用配置表**
3. **语义相关的表，合并为一张**

### 6.2 收敛后的核心配置表（5 张）

| 序号 | 配置表 | 用途 | 说明 |
|------|--------|------|------|
| 1 | `edge_types.json` | 边类型定义 | 合并了 edge_semantics + edge_strategies |
| 2 | `node_types.json` | 节点类型定义 | 合并了 pool_roles + tdx_psatt |
| 3 | `dispatch.json` | 条件分派规则 | 保留（公式分派逻辑复杂且经常调整） |
| 4 | `engines.json` | 公式引擎配置 | 保留（双引擎切换需要配置） |
| 5 | `data_config.json` | 数据配置 | 保留（市场代码、缓存策略等） |

> **总表数**：从 v1.0 的 12 张 → v1.1 的 5 张，减少 58%。

**被淘汰的表及原因**：

| 表名 | 淘汰原因 | 去向 |
|------|---------|------|
| `timing.json` | 24 种组合是固定的，不会变化 | 编译成代码（TimingGate 类硬编码） |
| `edge_semantics.json` | 语义相关，合并 | 并入 `edge_types.json` |
| `edge_strategies.json` | 语义相关，合并 | 并入 `edge_types.json` |
| `pool_roles.json` | 语义相关，合并 | 并入 `node_types.json` |
| `tdx_psatt.json` | 语义相关，合并 | 并入 `node_types.json` |
| `tdx_noperate_rules.json` | 固定的比较操作符 | 编译成代码（evaluators.py 中硬编码） |
| `formula_routing.json` | 与 engines.json 功能重叠 | 并入 `engines.json` |
| `runtime_tables_schema.json` | 运行时表是固定的 | 直接定义为代码常量 |
| `defaults.json` | 默认值应该在代码中 | 直接写在类的 __init__ 中 |
| `table_categories.json` 等其他表 | 非核心，按需加载 | 保留在 config 目录中，但引擎不直接依赖 |

---

### 6.3 每张表干什么

#### 1. edge_types.json

**边类型定义**，包含：
- 边类型（conditional / unconditional）
- 每种边的源节点类型、目标节点类型
- 触发规则（gate_and_change / source_changed）
- 运算流程（["gate", "filter", "propagate"] / ["propagate"]）
- 边策略动作（不同节点类型组合的处理动作）
- 变换单元三元组的组装规则

**示例结构**：
```json
{
  "conditional": {
    "source_types": ["market_source", "state_pool"],
    "target_types": ["condition"],
    "trigger_rule": "gate_and_change",
    "pipeline": ["gate", "filter", "propagate"],
    "strategies": {
      "market_source:condition": "resolve_and_pass",
      "state_pool:condition": "apply_filter"
    }
  },
  "unconditional": {
    ...
  }
}
```

#### 2. node_types.json

**节点类型定义**，包含：
- 节点类型（market_source / state_pool / condition / discard_pool）
- 节点角色（target_pool / candidate_pool / transfer_condition 等）
- 状态池属性（TTL、告警、保存历史等）
- 每个角色的属性（is_target / generate_buy_signal 等）

**示例结构**：
```json
{
  "state_pool": {
    "roles": ["target_pool", "normal_pool", "sink_pool"],
    "attributes": {
      "ttl": {
        "ndeltype_map": {"0": "seconds", "1": "days", "2": "trading_days"},
        "default_ttl": null
      },
      "alert": {
        "types": ["sound", "tip", "save_block", "save_history"]
      },
      "is_target": false
    }
  },
  ...
}
```

#### 3. dispatch.json

**条件分派规则**（保留不变）：
- nset 分派（0/1/2 公式型，3/4 标量型，5 集合运算型）
- 每个 nset 对应的 gateway
- 位掩码路由规则

#### 4. engines.json

**公式引擎配置**（合并了 formula_routing.json）：
- 引擎定义（python_engine / hqchart_engine）
- 兼容的 gateway 列表
- 是否为默认引擎
- 公式路由规则（简单/复杂公式的引擎选择）

#### 5. data_config.json

**数据配置**（保留不变）：
- 市场代码前缀/后缀
- 缓存策略（max_entries / TTL）
- 数据注入规则

---

### 6.4 表驱动的精髓

**什么是真正的表驱动？**
> 用数据（表）来表达"变化的部分"，用代码来表达"不变的部分"。

**反模式**：什么都塞到 JSON 里，美其名曰"表驱动"。
- 固定不变的东西用配置表 → 增加了一层间接性，没有收益
- 只有"会变化的东西"才值得用配置表

**v1.1 的判断标准**：
- 这个东西会经常变吗？ → 会 → 配置表
- 这个东西是业务逻辑还是配置？ → 业务逻辑 → 代码
- 这个东西有多少种可能性？ → 很少且固定 → 代码
- 这个东西需要用户自定义吗？ → 需要 → 配置表

---

## 7. 与现有系统的关系（v1.1 不变，略作调整）

### 7.1 哪些复用

| 组件 | 复用方式 | 说明 |
|------|---------|------|
| `FormulaRouter` | 直接复用 | 公式路由、双引擎切换、缓存策略均不变 |
| `PythonFormulaEngine` | 直接复用 | Python 公式引擎 |
| `FormulaCache` | 改造复用 | 修正缓存键（frozenset 替代 list） |
| `DataQuery` | 直接复用 | 数据查询接口 |
| `evaluators.py` | 直接复用 | TDX 条件评估器（nset 评估） |
| `CompiledSchedule` | 改造复用 | 保留结构，增加 OO 对象引用 |
| `LRUCache` | 直接复用 | LRU + TTL 缓存 |
| `配置表（5 张核心表）` | 直接复用 | 其余表按需保留 |
| `数据适配器（tq_adapter 等）` | 直接复用 | 数据源接入层不变 |
| `候选池服务（candidate_pool.py）` | 直接复用 | 候选股解析逻辑 |

### 7.2 哪些重写

| 组件 | 重写方式 | 说明 |
|------|---------|------|
| `MetaEngine` | 重构为 `EventDrivenEngine` | 上帝类拆分为 Node/Edge/EventQueue/EventLoop/Topology/Metrics 等 |
| `engine.py` 中的大函数 | 分散到各 Node/Edge 子类 + FilterEvaluator + TimingGate | 如 `_filter_conditional` → `FilterEvaluator.evaluate()` |
| `_run_tick_event_driven` | 重写为事件循环 | 从"按拓扑序遍历边"改为"事件队列 + 级联传播" |
| `_prepare_topology` | 重写为 `TopologyBuilder` | 从返回 dict 改为返回 Topology 对象（含环检测） |
| `_compile_pool` | 重写为 `Compiler` | 从构建 dict 改为构建 Node/Edge 对象图 |
| `_process_edge_pipeline` | 拆分为 Edge 子类的 execute 方法 | gate → filter → propagate 拆分 |
| 三套 filter | 统一为 FilterEvaluator.evaluate() | 消除 conditional / formula_eval / unconditional 的重复代码 |

### 7.3 怎么保证向后兼容

**三层兼容保证**（同 v1.0）：

#### 第一层：API 兼容
- `MetaEngine` 类名保留，作为 Facade
- 所有现有公共方法保留：`run_pool()` / `start_loop()` / `stop_loop()` / `get_event_queue()` 等
- 内部委托给 `EventDrivenEngine`
- **v1.1 修正**：用 `@property` 显式代理兼容属性，不用 `__getattr__` 黑魔法
- 提供兼容属性白清单（通过审计外部调用点确定）

#### 第二层：数据结构兼容
- `pool_config` 输入格式完全不变
- `node_stocks` 输出格式完全不变
- 事件格式完全不变
- `CompiledSchedule` 结构保留，新增 OO 对象字段

#### 第三层：行为等价
- 见第 8 章"等价性验证框架"

---

## 8. 等价性验证框架（v1.1 新增）

### 8.1 等价性定义

**我们说的"等价"是什么意思？**

v1.1 定义为 **"功能等价 + 关键时序等价"**：

| 等价维度 | 要求 | 说明 |
|---------|------|------|
| 最终状态一致 | ✅ 必须 | 每个 tick 结束时，所有节点的股票列表完全一致 |
| tick 级时序一致 | ✅ 必须 | 同一股票在同一个 tick 入池/出池（不要求同一 tick 内的顺序） |
| 事件集合一致 | ✅ 必须 | 每个 tick 产生的 PoolChangedEvent / AlertEvent / SignalEvent 的集合一致（不要求顺序） |
| 同一 tick 内的事件顺序 | ⚪ 不要求 | 可以不同（因为执行顺序模型不同） |
| 中间状态一致 | ⚪ 不要求 | 同一 tick 内的中间状态可以不同 |
| 性能一致 | ⚪ 不要求（但有底线） | 最坏情况性能 ≥ 旧引擎的 90% |

**为什么这样定义？**
- 最终状态一致是底线（功能正确）
- tick 级时序一致保证策略回测结果一致
- 同一 tick 内的顺序不影响策略结果（因为一个 tick 内的价格是一样的）
- 放宽"同一 tick 内的顺序"可以大大降低实现难度

---

### 8.2 Oracle 对比测试框架

**核心思想**：用旧引擎作为 Oracle（基准），新引擎作为待测，对同一输入逐 tick 对比输出。

```
┌─────────────┐     相同输入      ┌─────────────┐
│  旧引擎      │ ───────────────→ │  新引擎      │
│  (Oracle)   │                  │  (待测)      │
└──────┬──────┘                  └──────┬──────┘
       │                                │
       │  tick 输出                      │  tick 输出
       │                                │
       ▼                                ▼
┌─────────────────────────────────────────────┐
│              对比器 (Comparator)            │
│  - 逐 tick 对比节点股票列表                 │
│  - 逐 tick 对比输出事件集合                 │
│  - 记录差异，生成报告                       │
└─────────────────────────────────────────────┘
```

#### 测试流程

```
输入：
  - pool_config: 股票池配置
  - market_data: 历史行情数据（多 tick）

步骤：
  1. 用旧引擎运行，收集每个 tick 的输出：
     - old_output[tick] = {
         node_stocks: {node_id: [code, ...]},
         events: [PoolChangedEvent, ...],
         signals: [SignalEvent, ...]
       }

  2. 用新引擎运行，收集每个 tick 的输出：
     - new_output[tick] = 同上

  3. 逐 tick 对比：
     for tick in all_ticks:
       compare(old_output[tick], new_output[tick])

  4. 生成对比报告：
     - 总 tick 数
     - 一致的 tick 数
     - 有差异的 tick 数
     - 前 10 个差异的详细信息（哪个节点、哪些股票不同）
     - 性能对比（总耗时、平均每 tick 耗时）
```

#### 对比维度

| 对比项 | 对比方法 | 通过标准 |
|--------|---------|---------|
| 节点股票列表 | 集合相等（frozenset 比较） | 所有节点的股票集合完全一致 |
| 入池事件 | 集合相等（按股票代码分组） | 同一 tick 入池的股票集合一致 |
| 出池事件 | 集合相等（按股票代码分组） | 同一 tick 出池的股票集合一致 |
| 告警事件 | 集合相等 | 同一 tick 的告警事件集合一致 |
| 信号事件 | 集合相等 | 同一 tick 的信号事件集合一致 |

---

### 8.3 测试覆盖矩阵

**测试策略**：金字塔形测试，从下到上数量递减。

```
                    /\
                   /  \      E2E 测试（完整池子）
                  /    \
                 /      \    集成测试（边类型、场景）
                /        \
               /          \  单元测试（每个类）
              /____________\
```

#### 单元测试（每个类）

| 测试对象 | 测试内容 | 用例数（目标） |
|---------|---------|---------------|
| EventQueue | 入队、出队、优先级排序、去重 | 10+ |
| EventLoop | 事件分发、异常处理、死循环检测 | 10+ |
| TimingGate | 24 种 starttype × cxtype 组合 | 24+ |
| FilterEvaluator | 各种过滤类型、增量过滤、缓存 | 20+ |
| Topology | 拓扑构建、环检测、拓扑排序 | 10+ |
| Node 子类 | update_stocks、脏标记、TTL | 15+ |
| Edge 子类 | should_execute、execute | 15+ |
| EngineMetrics | 指标收集、汇总 | 5+ |

**单元测试总计**：约 100+ 用例

#### 集成测试（每个边类型 + 场景）

| 测试场景 | 测试内容 | 用例数（目标） |
|---------|---------|---------------|
| 单条件边 | 条件边 + 条件节点 + 状态池 | 5+ |
| 无条件边 | 条件节点 → 状态池 | 3+ |
| 多级级联 | 3 层以上的级联传播 | 5+ |
| 多分支汇合 | 多个上游 → 同一个下游 | 5+ |
| 时机门控 | 各种时机类型的触发 | 10+ |
| TTL 过期 | 股票入池后过期 | 5+ |
| 源节点刷新 | 备选池刷新触发下游 | 5+ |
| 数据驱动 | 数据变化触发重新计算 | 5+ |
| 增量过滤 | 少量股票变化时的增量计算 | 5+ |
| 边界情况 | 空池、单股票、满池 | 5+ |

**集成测试总计**：约 50+ 用例

#### 端到端测试（完整池子）

| 测试类型 | 测试内容 | 用例数（目标） |
|---------|---------|---------------|
| 真实股票池 | 使用项目中的真实股票池 XML | 10+ |
| 模糊测试 | 随机生成 pool_config + 行情数据 | 1000+ |
| 性能基准 | 100 条边、1000 只股票的场景 | 3+ |

**E2E 测试总计**：约 1000+ 用例（大部分是模糊测试）

---

### 8.4 模糊测试（Fuzz Testing）

**为什么需要模糊测试？**
- 手工设计的测试用例覆盖有限
- 随机生成的用例可能发现意想不到的边界情况
- 等价性验证需要大量测试才能建立信心

**模糊测试方案**：

```
随机生成器：
  1. 随机生成 pool_config：
     - 节点数：2-20 个
     - 边数：2-50 条
     - 随机选择边类型、时机类型、过滤条件
     - 保证拓扑是 DAG（避免环）

  2. 随机生成行情数据：
     - 股票数：10-1000 只
     - tick 数：100-1000 个
     - 价格随机波动

  3. 同时用旧引擎和新引擎运行
  4. 对比输出，记录差异
  5. 如果发现差异，自动最小化复现用例
```

**目标**：
- 运行 10000+ 个随机用例
- 发现的所有 bug 都修复
- 连续运行 24 小时无差异

---

### 8.5 性能基准测试

**性能指标**：
- 每 tick 平均耗时
- 每 tick 最大耗时
- 事件队列平均长度
- 缓存命中率

**测试场景**：

| 场景 | 规模 | 说明 |
|------|------|------|
| 小池子 | 5 条边、100 只股票 | 验证基础性能 |
| 中等池子 | 20 条边、500 只股票 | 典型场景 |
| 大池子 | 100 条边、1000 只股票 | 压力测试 |
| 稀疏变化 | 大池子，但每 tick 只有 1% 股票变化 | 验证数据驱动的优势 |
| 全量变化 | 大池子，每 tick 所有股票都变化 | 最坏情况性能 |

**通过标准**：
- 最坏情况性能 ≥ 旧引擎的 90%
- 稀疏变化场景性能 ≥ 旧引擎的 200%（越快越好）

---

### 8.6 迁移策略

**四阶段迁移**，每个阶段都有明确的准入准出标准。

```
阶段 0：双引擎并行（影子模式）
  │
  ▼
阶段 1：灰度切换（小流量）
  │
  ▼
阶段 2：灰度切换（大流量）
  │
  ▼
阶段 3：完全替换，旧引擎降级为 fallback
  │
  ▼
阶段 4：移除旧引擎代码
```

#### 阶段 0：双引擎并行（影子模式）

**时间**：v1.1 发布后
**流量**：0%（新引擎只跑，结果不对外）
**做法**：
- 每个 tick 同时跑旧引擎和新引擎
- 对比输出，记录差异
- 新引擎的结果只用于日志，不影响实际运行
**准出标准**：
- 连续运行 7 天无差异
- 性能达标（最坏情况 ≥ 90%）
- 没有发现 P0/P1 级 bug

#### 阶段 1：灰度切换（1% 流量）

**时间**：阶段 0 通过后
**流量**：1% 的股票池用新引擎
**做法**：
- 按股票池 ID 哈希，1% 的池子走新引擎
- 其余池子走旧引擎
- 新引擎的池子也跑旧引擎做对比（双写）
**准出标准**：
- 连续运行 3 天无差异
- 没有用户投诉
- 性能达标

#### 阶段 2：灰度切换（50% 流量）

**时间**：阶段 1 通过后
**流量**：50% 的股票池用新引擎
**做法**：同阶段 1
**准出标准**：
- 连续运行 7 天无差异
- 没有用户投诉
- 性能达标

#### 阶段 3：完全替换

**时间**：阶段 2 通过后
**流量**：100% 的股票池用新引擎
**做法**：
- 默认用新引擎
- 旧引擎保留，作为 fallback
- 提供开关，可以随时切回旧引擎
**准出标准**：
- 连续运行 30 天无重大问题
- 回滚次数 = 0

#### 阶段 4：移除旧引擎

**时间**：阶段 3 通过后
**做法**：
- 移除旧引擎代码
- 只保留新引擎
**准出标准**：
- 确认没有回滚需求

---

### 8.7 回滚机制

**万一新引擎出问题怎么办？**

**回滚触发条件**：
- 发现行为不一致（与旧引擎输出不同）
- 性能严重劣化（< 旧引擎的 50%）
- 崩溃、死循环等严重问题
- 用户投诉策略结果异常

**回滚方式**：
- 配置开关：`use_new_engine = false`
- 热切换：不需要重启，下一个 tick 生效
- 回滚后自动记录日志，便于排查

---

## 9. 错误处理与可观测性（v1.1 新增）

### 9.1 错误处理策略

**设计原则**：
- 单条边失败不影响整个 tick
- 单个股票失败不影响其他股票
- 失败要记录日志，便于排查
- 要有 metrics 统计失败率

#### 错误分类与处理策略

| 错误类型 | 发生场景 | 处理策略 | 日志级别 |
|---------|---------|---------|---------|
| 单条边执行失败 | 边 execute() 抛出异常 | 跳过该边，记录错误，继续处理其他边 | ERROR |
| 单个股票过滤失败 | 公式计算某只股票时抛异常 | 跳过该股票，记录警告，其他股票继续 | WARNING |
| 事件处理异常 | 事件处理器抛异常 | 跳过该事件，记录错误，继续处理下一个 | ERROR |
| 公式引擎异常 | FormulaRouter 调用失败 | 降级到备用引擎，都失败则跳过 | WARNING / ERROR |
| 数据源异常 | DataQuery 调用失败 | 使用缓存数据，缓存也没有则跳过 | WARNING |
| 事件队列死循环 | 迭代次数超过 max_iterations | 终止事件循环，记录严重错误 | CRITICAL |
| 引擎启动失败 | 编译阶段出错 | 抛出异常，启动失败 | CRITICAL |

#### 错误上下文

**每个错误日志都要包含的信息**：
- tick 序号、时间戳
- 错误类型、错误信息
- 相关的节点 ID / 边 ID / 股票代码
- 堆栈跟踪
- （可选）当时的上下文数据快照

**示例日志**：
```
[2026-07-01 10:30:00] ERROR engine: 边执行失败
  tick=1234, ts=1719801000.0
  edge_id=edge_007, edge_type=conditional
  source_node=node_003, target_node=node_005
  error: ValueError: 公式参数错误
  traceback:
    File "edges.py", line 123, in execute
      result = self.filter_evaluator.evaluate(stocks, context)
    ...
```

---

### 9.2 可观测性设计

**三个支柱**：日志、指标、追踪。

#### 日志策略

**日志级别定义**：

| 级别 | 使用场景 | 示例 |
|------|---------|------|
| DEBUG | 详细的调试信息 | 每条边执行的输入输出、每个事件的入队出队 |
| INFO | 正常运行的关键事件 | 引擎启动/停止、每个 tick 的汇总信息 |
| WARNING | 异常但可恢复的情况 | 单只股票过滤失败、缓存未命中 |
| ERROR | 错误但不影响整体运行 | 单条边执行失败、数据源异常 |
| CRITICAL | 严重错误，可能影响整体运行 | 死循环、引擎启动失败 |

**日志内容建议**：

| 模块 | 日志内容 | 级别 |
|------|---------|------|
| EventLoop | tick 开始/结束、事件数、耗时 | INFO |
| EventLoop | 每个事件的处理 | DEBUG |
| EventQueue | 事件入队/出队 | DEBUG |
| Edge | 边执行开始/结束、结果 | DEBUG |
| FilterEvaluator | 过滤计算详情、缓存命中情况 | DEBUG |
| TimingGate | 时机状态变化 | DEBUG |
| Node | 节点股票变化 | DEBUG |
| Engine | 启动/停止、编译信息 | INFO |
| Engine | 错误、异常 | ERROR / CRITICAL |

**生产环境默认**：INFO 级别
**调试时**：可以动态调整到 DEBUG 级别

---

#### 指标（Metrics）

**EngineMetrics 收集的指标**：

| 指标名称 | 类型 | 说明 |
|---------|------|------|
| `tick_count` | Counter | tick 总数 |
| `total_tick_time` | Gauge | 总 tick 耗时（秒） |
| `avg_tick_time` | Gauge | 平均每 tick 耗时（秒） |
| `max_tick_time` | Gauge | 最大 tick 耗时（秒） |
| `events_processed_total` | Counter | 处理的事件总数 |
| `events_per_tick` | Gauge | 每 tick 平均事件数 |
| `edges_executed_total` | Counter | 边执行总数 |
| `edges_per_tick` | Gauge | 每 tick 平均执行边数 |
| `cache_hits` | Counter | 缓存命中次数 |
| `cache_misses` | Counter | 缓存未命中次数 |
| `cache_hit_rate` | Gauge | 缓存命中率 |
| `max_queue_size` | Gauge | 事件队列最大长度 |
| `error_count` | Counter | 错误总数 |
| `error_rate` | Gauge | 错误率（错误数 / 边执行数） |

**指标输出方式**：
- `get_metrics()` → 返回字典
- 日志输出（每 N 个 tick 输出一次汇总）
- （可选）Prometheus 格式导出

---

#### 调试工具

**内置调试工具集**：

1. **状态快照**：`engine.dump_state()`
   - 输出所有节点的股票列表
   - 输出所有边的状态
   - 输出事件队列内容

2. **单步执行**：`engine.step()`
   - 只处理一个事件
   - 返回处理的事件和结果
   - 用于逐步追踪事件流

3. **股票追踪**：`engine.track_stock(code)`
   - 开启某只股票的追踪
   - 记录这只股票经过的所有节点、所有边
   - 输出流动路径

4. **事件溯源**：`engine.get_event_history()`
   - 返回本 tick 处理的所有事件列表
   - 按时间顺序排列
   - 用于复现问题

5. **性能分析**：`engine.get_profile()`
   - 按边统计执行次数、总耗时、平均耗时
   - 找出最慢的边
   - 用于性能优化

---

### 9.3 边界情况清单

**必须测试的边界情况**：

| 分类 | 边界情况 | 预期行为 |
|------|---------|---------|
| 空池 | 所有节点都是空的 | 正常运行，不报错 |
| 单节点 | 只有一个源节点，没有边 | 正常运行，什么都不做 |
| 单条边 | 只有一条边，两个节点 | 正常工作 |
| 孤点 | 有节点没有任何边连接 | 正常运行，该节点不变化 |
| 多源节点 | 多个源节点 → 同一个条件节点 | 两个源的股票都参与过滤 |
| 多目标节点 | 一个条件节点 → 多个状态池 | 两个目标池都能收到股票 |
| 环 | 拓扑有环 | 编译期报错（严格模式）或运行期终止（宽容模式） |
| 自环 | 节点自己连自己 | 编译期报错 |
| 深链 | 10 层以上的级联 | 正常工作，级联正确 |
| 扇入 | 10 个上游 → 1 个下游 | 所有上游的股票都汇合到下游 |
| 扇出 | 1 个上游 → 10 个下游 | 所有下游都收到股票 |
| TTL 边界 | 股票刚好在 tick 边界过期 | 在正确的 tick 过期 |
| 时机边界 | 时机窗口刚好在 tick 边界打开/关闭 | 在正确的 tick 触发 |
| 大量股票 | 10000 只股票的备选池 | 性能可接受，不崩溃 |
| 大量边 | 100 条边的复杂池子 | 性能可接受，不崩溃 |
| 空过滤结果 | 没有股票通过过滤 | 目标节点变为空，正常 |
| 全通过过滤 | 所有股票都通过过滤 | 目标节点 = 源节点，正常 |
| 公式异常 | 公式计算抛异常 | 跳过该股票/该边，记录日志 |
| 数据源超时 | DataQuery 调用超时 | 使用缓存，记录警告 |

---

## 附录 A：文件结构规划（v1.1 更新）

```
meta_core/
├── core/
│   ├── engine.py              # 保留 MetaEngine Facade（向后兼容）
│   ├── event_driven_engine.py # 新增：EventDrivenEngine 主类
│   ├── event_queue.py         # 新增：EventQueue 优先级队列
│   ├── event_loop.py          # 新增：EventLoop 事件循环
│   ├── nodes.py               # 新增：Node 类体系
│   ├── edges.py               # 新增：Edge 类体系
│   ├── timing_gate.py         # 新增：TimingGate 时机门控
│   ├── filter_evaluator.py    # 新增：FilterEvaluator 过滤器评估器
│   ├── events.py              # 新增：Event 类定义
│   ├── topology.py            # 新增：Topology 类（含环检测）
│   ├── metrics.py             # 新增：EngineMetrics 指标收集
│   ├── compiler.py            # 新增：编译期逻辑
│   ├── engine_context.py      # 新增：引擎上下文
│   ├── formula_router.py      # 保留（复用）
│   ├── formula_engine.py      # 保留（复用）
│   ├── evaluators.py          # 保留（复用）
│   ├── table_engine.py        # 保留
│   ├── replay.py              # 保留
│   └── simulator.py           # 保留
├── config/                    # 保留所有配置表（核心 5 张 + 其他）
├── services/                  # 保留所有服务
└── tests/
    ├── unit/                  # 新增：单元测试
    │   ├── test_event_queue.py
    │   ├── test_event_loop.py
    │   ├── test_timing_gate.py
    │   ├── test_filter_evaluator.py
    │   ├── test_topology.py
    │   ├── test_nodes.py
    │   ├── test_edges.py
    │   └── test_metrics.py
    ├── integration/           # 新增：集成测试
    │   ├── test_single_edge.py
    │   ├── test_cascade.py
    │   ├── test_timing.py
    │   └── test_ttl.py
    ├── e2e/                   # 新增：端到端测试
    │   ├── test_equivalence.py    # Oracle 对比测试
    │   ├── test_fuzz.py           # 模糊测试
    │   └── test_performance.py    # 性能基准测试
    └── fixtures/              # 新增：测试夹具
        ├── sample_pools.py
        └── sample_data.py
```

---

## 附录 B：v1.0 → v1.1 变更摘要

| 章节 | v1.0 | v1.1 | 变化原因 |
|------|------|------|---------|
| 事件驱动 | 伪事件驱动（每 tick 遍历所有边） | 真·事件驱动（事件队列 + 级联传播） | P0 问题 1 |
| 执行顺序 | "一次遍历即可"（错误） | 事件级联，多轮传播，execution_order 只影响同层顺序 | P0 问题 2 |
| 等价性 | 只有口号 | 完整的等价性验证框架（Oracle对比、测试矩阵、迁移策略） | P0 问题 3 |
| Edge 类 | 职责过重（时机+执行+触发） | 拆分出 TimingGate、FilterEvaluator | P1 改进 |
| SinkNode | 独立子类 | 用 StatePoolNode + is_sink 标记替代 | P2 改进 |
| 事件类型 | 12+ 种 | 9 种（合并了相似事件） | P2 改进 |
| 配置表 | 12 张核心表 | 5 张核心表（合并语义相关表，固定逻辑编译成代码） | P1 改进 |
| 错误处理 | 无 | 完整的错误处理策略 | P1 改进 |
| 可观测性 | 无 | 日志、指标、调试工具 | P1 改进 |
| 循环检测 | 无 | 编译期 + 运行期双重防护 | P1 改进 |
| 增量过滤 | 只有布尔脏标记 | 支持增量过滤（只计算变化的股票） | P1 改进 |
| 缓存键 | list 进哈希（顺序敏感） | frozenset 进哈希（顺序不敏感） | P2 改进 |
| 向后兼容 | `__getattr__` 黑魔法 | `@property` 显式代理 + 白名单 | P2 改进 |
