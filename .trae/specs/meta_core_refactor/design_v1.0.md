# v1.0 面向对象事件驱动架构设计

## 0. 设计原则与约束

**现状**：`engine.py` 3500+ 行过程式大函数，`MetaEngine` 上帝类，轮询全量计算，拓扑与执行顺序混淆，三套 filter 各搞一套。

**目标**：面向对象 + 事件驱动 + 数据驱动 + 表驱动，仅修改 `meta_core` 目录，向后兼容，功能等价。

**设计原则**：
- 继承层次不超过 3 层
- 每个类职责单一
- 事件驱动而非轮询包装
- 数据不变则不计算
- 拓扑（连接关系）与执行顺序（用户指定次序）分离

---

## 1. 核心类图

### 1.1 Node 类体系

```
Node (基类)
├── SourceNode (源节点)
│   ├── MarketSourceNode (市场源/备选池)
│   └── CandidatePoolNode (候选池)
├── StatePoolNode (状态池)
├── ConditionNode (条件节点)
└── SinkNode (废弃池)
```

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
| `_event_bus` | EventBus | 事件总线引用 |

**方法**：
| 方法 | 说明 |
|------|------|
| `update_stocks(new_stocks)` | 更新股票集，变化时置脏并发布 `NodeStockChangedEvent` |
| `mark_dirty()` | 标记节点为脏 |
| `clear_dirty()` | 清除脏标记 |
| `add_in_edge(edge)` / `add_out_edge(edge)` | 添加入/出边 |
| `get_stock_codes()` | 返回股票代码集合 |
| `snapshot_stocks()` | 生成股票快照并更新 snapshot |

**发布事件**：
- `NodeStockChangedEvent` — 股票集变化时

#### SourceNode（源节点）

**职责**：外部数据源节点（备选池/市场源），从外部获取股票列表。

**新增属性**：
| 属性 | 类型 | 说明 |
|------|------|------|
| `source_config` | dict | 数据源配置（markets / sector_codes / custom_stocks） |
| `refresh_interval` | float | 刷新间隔（秒） |
| `last_refresh_ts` | float | 上次刷新时间戳 |

**新增方法**：
| 方法 | 说明 |
|------|------|
| `refresh(data_provider)` | 从数据源刷新股票列表 |
| `should_refresh(now_ts)` | 判断是否需要刷新 |

**订阅事件**：
- `DataUpdatedEvent` — 行情数据更新时触发刷新检查

**发布事件**：
- `NodeStockChangedEvent` — 源股票变化时

#### StatePoolNode（状态池）

**职责**：状态池节点，持有经过滤的股票集合，支持 TTL 过期、目标池属性。

**新增属性**：
| 属性 | 类型 | 说明 |
|------|------|------|
| `is_target` | bool | 是否为目标池（baimpool=1） |
| `ttl_config` | dict | TTL 配置（bdel / ndelnum / ndeltype） |
| `alert_config` | dict | 告警配置（bsound / btip / bsavetoblock / bsavehis） |

**新增方法**：
| 方法 | 说明 |
|------|------|
| `check_ttl(now_ts)` | 检查并移除过期股票 |
| `is_alert_needed()` | 判断是否需要发出告警 |

**订阅事件**：
- `NodeStockChangedEvent`（来自上游）— 触发过滤重新计算

**发布事件**：
- `PoolEnterEvent` — 股票入池时
- `PoolExitEvent` — 股票出池时
- `AlertEvent` — 告警触发时
- `SignalEvent` — 目标池入/出产生 BUY/SELL 信号时

#### ConditionNode（条件节点）

**职责**：条件过滤节点，对输入股票执行公式/条件计算，输出通过的股票。

**新增属性**：
| 属性 | 类型 | 说明 |
|------|------|------|
| `filter_spec` | dict | 过滤规格（nset / accode / noperate / formula 等，编译期预计算） |
| `filter_type` | str | 过滤类型（conditional / formula_eval / unconditional） |
| `filter_cache` | LRUCache | 过滤结果缓存 |
| `last_filter_hash` | str | 上次过滤输入数据的哈希 |

**新增方法**：
| 方法 | 说明 |
|------|------|
| `evaluate(input_stocks, bar_data, formula_router)` | 执行过滤计算，返回通过的股票 |
| `invalidate_cache()` | 使过滤缓存失效 |

**订阅事件**：
- `NodeStockChangedEvent`（源节点）— 触发重新过滤
- `DataUpdatedEvent` — 行情数据变化触发重新过滤

**发布事件**：
- `NodeStockChangedEvent` — 过滤结果变化时

#### SinkNode（废弃池）

**职责**：废弃/丢弃池，接收被过滤掉的股票，不参与下游计算。

**新增属性**：无

**新增方法**：无

---

### 1.2 Edge 类体系

```
Edge (基类)
├── ConditionalEdge (条件转移边)
└── UnconditionalEdge (无条件转移边)
```

#### Edge 基类

**职责**：边的抽象基类，封装端点连接、时机门控、触发判定。

**属性**：
| 属性 | 类型 | 说明 |
|------|------|------|
| `edge_id` | str | 边唯一 ID |
| `source_node` | Node | 源节点引用 |
| `target_node` | Node | 目标节点引用 |
| `params` | dict | 边参数 |
| `timing_spec` | dict | 时机门控规格（编译期预计算） |
| `last_fired_ts` | float | 上次触发时间戳 |
| `first_fired_ts` | float | 首次触发时间戳 |
| `exec_count` | int | 执行次数 |
| `is_fired` | bool | 当前 tick 是否触发（时机层面） |

**方法**：
| 方法 | 说明 |
|------|------|
| `should_trigger(now_ts)` | 时机门控判定（gate + duration） |
| `execute()` | 执行边的流转逻辑（由子类实现） |
| `reset()` | 重置执行状态 |

**订阅事件**：
- `TimerTickEvent` — 每 tick 检查时机门控

**发布事件**：
- `EdgeFiredEvent` — 边触发时机到达时
- `EdgeExecutedEvent` — 边执行完成时

#### ConditionalEdge（条件转移边）

**职责**：条件转移边（源为备选池/状态池，目标为条件节点），有时机属性。
触发条件：gate 通过 AND (源节点脏 OR 行情数据脏)。

**新增属性**：
| 属性 | 类型 | 说明 |
|------|------|------|
| `filter_spec` | dict | 过滤规格（编译期预计算） |
| `strategy_action` | str | 边策略动作（apply_filter / resolve_and_pass 等） |

**新增方法**：
| 方法 | 说明 |
|------|------|
| `is_data_triggered(data_dirty)` | 判断数据变化是否触发 |
| `is_node_triggered()` | 判断源节点变化是否触发 |

**订阅事件**：
- `NodeStockChangedEvent`（源节点）— 置触发条件
- `DataUpdatedEvent` — 行情数据变化置触发条件

#### UnconditionalEdge（无条件转移边）

**职责**：无条件转移边（源为条件节点，目标为状态池），无时机属性。
触发条件：源节点股票变化。

**新增属性**：无

**新增方法**：无

**订阅事件**：
- `NodeStockChangedEvent`（源节点）— 直接 propagate 到目标

---

### 1.3 Event 类体系

```
Event (基类)
├── DataEvent (数据类事件)
│   ├── DataUpdatedEvent (行情数据更新)
│   └── BarDataUpdatedEvent (K线数据更新)
├── NodeEvent (节点类事件)
│   ├── NodeStockChangedEvent (节点股票变化)
│   ├── PoolEnterEvent (股票入池)
│   └── PoolExitEvent (股票出池)
├── EdgeEvent (边类事件)
│   ├── EdgeFiredEvent (边时机触发)
│   └── EdgeExecutedEvent (边执行完成)
├── TimerEvent (时间类事件)
│   └── TimerTickEvent (时钟 tick)
├── AlertEvent (告警事件)
├── SignalEvent (信号事件)
└── SystemEvent (系统类事件)
    ├── PoolStartEvent (池启动)
    └── PoolStopEvent (池停止)
```

#### Event 基类

| 属性 | 类型 | 说明 |
|------|------|------|
| `event_type` | str | 事件类型 |
| `timestamp` | float | 事件时间戳 |
| `source` | str | 事件源标识 |

#### DataUpdatedEvent

**用途**：行情快照数据更新时发布，触发所有依赖行情的节点重新计算。

**携带数据**：
| 字段 | 类型 | 说明 |
|------|------|------|
| `bar_data` | dict | 最新行情数据 {code: {open, high, low, close, volume, amount}} |
| `data_hash` | str | 数据内容哈希 |
| `bar_time` | datetime | K线时间 |

#### BarDataUpdatedEvent

**用途**：K线数据（分钟/日线周期）更新时发布，触发公式重算。

**携带数据**：
| 字段 | 类型 | 说明 |
|------|------|------|
| `symbol` | str | 股票代码 |
| `period` | str | 周期（1m / 5m / 1d 等） |
| `bar_data` | list | K线数据序列 |

#### NodeStockChangedEvent

**用途**：节点股票集变化时发布，触发下游边/节点重新计算。

**携带数据**：
| 字段 | 类型 | 说明 |
|------|------|------|
| `node_id` | str | 节点 ID |
| `added` | list[str] | 新增股票代码列表 |
| `removed` | list[str] | 移除股票代码列表 |
| `current_codes` | frozenset | 当前股票代码集合 |

#### PoolEnterEvent

**用途**：股票进入状态池时发布（用于 UI 更新、历史记录、信号生成）。

**携带数据**：
| 字段 | 类型 | 说明 |
|------|------|------|
| `pool_id` | str | 池节点 ID |
| `code` | str | 股票代码 |
| `label` | str | 股票名称 |
| `entry_price` | float | 入池价格 |
| `entry_time` | float | 入池时间戳 |

#### PoolExitEvent

**用途**：股票离开状态池时发布。

**携带数据**：
| 字段 | 类型 | 说明 |
|------|------|------|
| `pool_id` | str | 池节点 ID |
| `code` | str | 股票代码 |
| `exit_reason` | str | 离开原因（filtered / ttl_expired / moved） |
| `exit_time` | float | 离开时间戳 |

#### EdgeFiredEvent

**用途**：边的时机门控通过时发布（时机层面的触发）。

**携带数据**：
| 字段 | 类型 | 说明 |
|------|------|------|
| `edge_id` | str | 边 ID |
| `fire_reason` | str | 触发原因（timer / data / node_change） |

#### EdgeExecutedEvent

**用途**：边执行完成（过滤+流转）后发布。

**携带数据**：
| 字段 | 类型 | 说明 |
|------|------|------|
| `edge_id` | str | 边 ID |
| `passed_count` | int | 通过过滤的股票数 |
| `rejected_count` | int | 被过滤的股票数 |

#### TimerTickEvent

**用途**：时钟 tick 事件，驱动时机门控检查。

**携带数据**：
| 字段 | 类型 | 说明 |
|------|------|------|
| `tick_ts` | float | tick 时间戳 |
| `bar_time` | datetime | 当前K线时间 |

#### AlertEvent

**用途**：告警事件（声音/弹窗/保存板块等）。

**携带数据**：
| 字段 | 类型 | 说明 |
|------|------|------|
| `pool_id` | str | 目标池 ID |
| `alert_type` | str | 告警类型（sound / tip / save_block / save_history） |
| `stocks` | list[dict] | 触发告警的股票列表 |
| `config` | dict | 告警配置 |

#### SignalEvent

**用途**：交易信号事件（BUY / SELL）。

**携带数据**：
| 字段 | 类型 | 说明 |
|------|------|------|
| `signal_type` | str | 信号类型（BUY / SELL） |
| `code` | str | 股票代码 |
| `pool_id` | str | 来源池 ID |
| `price` | float | 信号价格 |
| `timestamp` | float | 信号时间戳 |

---

### 1.4 Engine 主类（协调者）

#### EventDrivenEngine

**职责**：引擎协调者，负责拓扑构建、事件总线管理、组件装配、生命周期管理。
不是上帝类，只做协调，具体计算委托给 Node/Edge/FormulaRouter。

**属性**：
| 属性 | 类型 | 说明 |
|------|------|------|
| `event_bus` | EventBus | 事件总线 |
| `nodes` | dict[str, Node] | 节点字典 {node_id: Node} |
| `edges` | dict[str, Edge] | 边字典 {edge_id: Edge} |
| `topology` | Topology | 拓扑关系（连接关系） |
| `execution_order` | list[str] | 执行顺序（用户指定的边执行次序） |
| `formula_router` | FormulaRouter | 公式路由器（复用现有） |
| `data_query` | DataQuery | 数据查询（复用现有） |
| `compiled` | CompiledSchedule | 编译产物（复用现有结构） |
| `config_store` | ConfigStore | 配置表存储（复用现有） |
| `is_running` | bool | 是否运行中 |
| `is_paused` | bool | 是否暂停 |

**方法**：
| 方法 | 说明 |
|------|------|
| `compile(pool_config)` | 编译池配置：构建 Node/Edge 对象、建立拓扑、计算执行顺序 |
| `start()` | 启动引擎 |
| `stop()` | 停止引擎 |
| `pause()` / `resume()` | 暂停/恢复 |
| `on_data(bar_data)` | 输入行情数据，发布 DataUpdatedEvent |
| `get_node_stocks(node_id)` | 获取节点股票 |
| `get_event_queue()` | 获取事件队列（外部消费） |

---

### 1.5 辅助类

#### EventBus（事件总线）

**职责**：事件的发布/订阅管理，支持同步发布。

**方法**：
- `subscribe(event_type, handler)` — 订阅事件
- `unsubscribe(event_type, handler)` — 取消订阅
- `publish(event)` — 发布事件（同步调用所有订阅者）

#### Topology（拓扑）

**职责**：封装节点连接关系，与执行顺序分离。

**属性**：
- `adjacency_out` — 出边邻接表
- `adjacency_in` — 入边邻接表
- `depths` — 节点深度

**方法**：
- `get_out_edges(node_id)` — 获取节点的出边
- `get_in_edges(node_id)` — 获取节点的入边
- `topological_sort()` — 拓扑排序

#### DirtyManager（脏标记管理器）

**职责**：统一管理节点脏标记、数据脏标记。

**属性**：
- `dirty_nodes` — 脏节点集合
- `data_dirty` — 数据是否脏

**方法**：
- `mark_node_dirty(node_id)` — 标记节点脏
- `mark_data_dirty()` — 标记数据脏
- `clear_all()` — 清除所有脏标记
- `is_node_dirty(node_id)` — 节点是否脏
- `is_data_dirty()` — 数据是否脏

---

### 1.6 类关系图

```
                    ┌──────────────────┐
                    │ EventDrivenEngine│
                    │  (协调者)        │
                    └────────┬─────────┘
                             │ 组合
           ┌─────────────────┼─────────────────┐
           ▼                 ▼                 ▼
     ┌──────────┐     ┌──────────┐     ┌──────────┐
     │ EventBus │     │ Topology │     │DirtyMgr  │
     └──────────┘     └────┬─────┘     └──────────┘
                           │ 引用
            ┌──────────────┴──────────────┐
            ▼                             ▼
      ┌──────────┐                  ┌──────────┐
      │   Node   │◄─────────────────│   Edge   │
      │ (基类)   │  source/target   │ (基类)   │
      └────┬─────┘                  └────┬─────┘
           │ 继承                         │ 继承
     ┌─────┴──────┐               ┌──────┴──────┐
     ▼            ▼               ▼             ▼
┌────────┐ ┌────────────┐ ┌──────────────┐ ┌───────────────┐
│Source  │ │StatePool   │ │Conditional   │ │Unconditional │
│Node    │ │Node        │ │Edge          │ │Edge           │
└────────┘ └────────────┘ └──────────────┘ └───────────────┘

     FormulaRouter ◄────── 依赖 ──────── ConditionNode
     (复用现有)

     DataQuery ◄────────── 依赖 ──────── SourceNode
     (复用现有)
```

---

## 2. 事件流设计

### 2.1 事件类型总览

| 事件类型 | 生产者 | 消费者 | 触发时机 |
|---------|--------|--------|---------|
| `TimerTickEvent` | Engine 主循环 | 所有 Edge（时机门控） | 每个 tick |
| `DataUpdatedEvent` | Engine.on_data() | ConditionNode / SourceNode / DirtyManager | 行情数据到达 |
| `BarDataUpdatedEvent` | 数据适配器 | FormulaRouter / ConditionNode | K线闭合 |
| `NodeStockChangedEvent` | Node.update_stocks() | 下游 Edge / 下游 Node | 节点股票集变化 |
| `EdgeFiredEvent` | Edge（时机满足） | Edge（自身执行） | 时机门控通过 |
| `EdgeExecutedEvent` | Edge（执行完） | Engine（统计/日志） | 边执行完成 |
| `PoolEnterEvent` | StatePoolNode | EventQueue / SignalGenerator | 股票入池 |
| `PoolExitEvent` | StatePoolNode | EventQueue / SignalGenerator | 股票出池 |
| `AlertEvent` | StatePoolNode | AlertHandler | 告警触发 |
| `SignalEvent` | StatePoolNode（目标池） | SignalQueue / 外部 | 目标池入/出 |

### 2.2 事件传递路径

**主路径（数据到达 → 节点更新 → 边触发 → 过滤 → 目标更新）**：

```
DataUpdatedEvent
    │
    ├─→ DirtyManager.mark_data_dirty()
    │
    └─→ 所有 ConditionNode 检查：数据脏 AND gate通过 → 重新过滤
            │
            └─→ ConditionNode 过滤结果变化 → NodeStockChangedEvent
                    │
                    └─→ 下游 UnconditionalEdge 触发 propagate
                            │
                            └─→ StatePoolNode 更新股票 → NodeStockChangedEvent
                                    │
                                    ├─→ PoolEnterEvent / PoolExitEvent
                                    ├─→ AlertEvent（如果是目标池且配置了告警）
                                    └─→ SignalEvent（如果是目标池）
```

**时间驱动路径**：

```
TimerTickEvent
    │
    ├─→ 所有 ConditionalEdge.should_trigger() 检查
    │       └─→ 时机满足 → EdgeFiredEvent
    │               └─→ 边检查：源脏 OR 数据脏 → 执行
    │
    ├─→ 所有 StatePoolNode.check_ttl() 检查
    │       └─→ 过期股票移除 → NodeStockChangedEvent
    │
    └─→ SourceNode.should_refresh() 检查
            └─→ 需要刷新 → 重新加载候选股 → NodeStockChangedEvent
```

### 2.3 事件顺序保证

**事件同步发布**：EventBus 采用同步发布模式，发布线程顺序调用所有订阅者。
这保证了：
1. 上游节点的 `NodeStockChangedEvent` 处理完成后，才会处理下游节点
2. 同一事件的多个订阅者按订阅顺序执行

**拓扑序保证**：
- 节点发布的 `NodeStockChangedEvent` 触发下游边时，按 `execution_order`（用户指定的执行顺序）处理
- 这保证了执行顺序与用户设计的股票池逻辑一致

**单线程模型**：
- 整个引擎在单线程中运行（事件循环）
- 不存在并发问题，无需锁
- 事件处理是原子的，不会被打断

---

## 3. 核心流程（事件视角）

### 3.1 完整事件链：从数据到达目标更新

```
时间轴 →

[行情数据到达]
     │
     ▼
Engine.on_data(bar_data)
     │
     ├─ 1. 计算数据哈希，与上次比较
     │   ├─ 相同：跳过，什么都不做（数据驱动）
     │   └─ 不同：继续
     │
     ├─ 2. 更新 latest_tick 数据
     │
     └─ 3. 发布 DataUpdatedEvent
             │
             ├─→ DirtyManager: data_dirty = True
             │
             ├─→ SourceNode: 检查是否需要刷新候选股
             │   └─ 需要刷新 → update_stocks() → NodeStockChangedEvent
             │
             └─→ [按 execution_order 遍历条件边]
                      │
                      ▼
            ConditionalEdge:
              检查 is_fired (gate) AND (source_dirty OR data_dirty)
                      │
                      ├─ 不满足：跳过（零计算）
                      └─ 满足：执行过滤
                              │
                              ▼
                    ConditionNode.evaluate():
                      从 formula_router 计算过滤结果
                      命中缓存则直接返回
                      未命中则执行公式计算
                              │
                              ▼
                    比较过滤结果与上次：
                      相同 → 不发布事件（数据驱动）
                      不同 → update_stocks() → NodeStockChangedEvent
                                      │
                                      ▼
                          UnconditionalEdge:
                             propagate 到目标状态池
                                      │
                                      ▼
                          StatePoolNode.update_stocks():
                            计算 added / removed
                            发布 PoolEnterEvent / PoolExitEvent
                            检查告警 → AlertEvent
                            检查目标池信号 → SignalEvent
                            标记自己为脏（级联下游）
                                      │
                                      ▼
                          [继续处理该目标节点的下游边]
```

### 3.2 时序图（文字版）

```
行情源           Engine           DirtyMgr      ConditionEdge   ConditionNode   StatePoolNode
  │                │                 │              │               │               │
  │── bar_data ──→│                 │              │               │               │
  │                │                 │              │               │               │
  │                │── publish ─────→│              │               │               │
  │                │  DataUpdated    │=True         │               │               │
  │                │                 │              │               │               │
  │                │───────────────────────────────→│               │               │
  │                │  检查触发条件                 │               │               │
  │                │                 │              │=fired?        │               │
  │                │                 │              │+source_dirty? │               │
  │                │                 │              │+data_dirty?   │               │
  │                │                 │              │               │               │
  │                │                 │              │── evaluate ──→│               │
  │                │                 │              │               │ 公式计算       │
  │                │                 │              │←─ result ─────│               │
  │                │                 │              │               │               │
  │                │                 │              │── propagate ────────────────→│
  │                │                 │              │               │  更新股票集    │
  │                │                 │              │               │               │
  │                │←─ publish ────────────────────────────────────────────────────│
  │                │  PoolEnterEvent               │               │               │
  │                │  SignalEvent                  │               │               │
  │                │  AlertEvent                   │               │               │
  │                │                 │              │               │               │
```

### 3.3 TTL 过期检查流程

```
TimerTickEvent
     │
     ▼
StatePoolNode.check_ttl(now_ts):
     │
     ├─ 遍历 stocks，计算 entry_ts + ttl_sec <= now_ts
     │
     ├─ 收集过期股票列表
     │
     └─ 有过期股票 → update_stocks(移除过期) → NodeStockChangedEvent
                           │
                           ├─→ PoolExitEvent (exit_reason=ttl_expired)
                           └─→ 下游级联更新
```

---

## 4. 数据驱动增量计算

### 4.1 数据时间戳机制

**多层时间戳**：

| 层级 | 时间戳 | 存储位置 | 用途 |
|------|--------|---------|------|
| 全局行情 | `latest_tick_ts` | Engine | 最新行情数据的时间戳 |
| 全局K线 | `current_bar_time` | Engine | 当前K线的时间 |
| 节点级 | `node.data_ts` | Node | 节点股票集最后变化的时间戳 |
| 边级 | `edge.last_fired_ts` | Edge | 边最后触发的时间戳 |
| 公式缓存级 | `cache_entry.ts` | FormulaCache | 公式结果缓存的时间戳 |

**时间戳传播**：
- 数据到达 → 更新 `latest_tick_ts`
- 节点股票变化 → 更新 `node.data_ts`
- 边执行 → 更新 `edge.last_fired_ts`

### 4.2 脏标记机制

**两类脏标记**：

| 脏标记类型 | 存储 | 置位时机 | 清除时机 |
|-----------|------|---------|---------|
| 数据脏 `data_dirty` | DirtyManager | 行情数据变化时 | tick 末 |
| 节点脏 `node_dirty` | DirtyManager + Node.is_dirty | 节点股票变化时 | tick 末（或节点被处理后） |

**脏标记传播规则**：
1. `DataUpdatedEvent` → `data_dirty = True`
2. `NodeStockChangedEvent` → 该节点 `is_dirty = True`
3. 下游边触发条件：`edge_fired AND (source_node_dirty OR data_dirty)`
4. 边执行后，目标节点自动脏（因为股票变了）
5. 脏标记沿拓扑向下传播，直到没有下游

**Tick 末清除**：
```python
# 每个 tick 结束时
dirty_manager.clear_all()
node.clear_dirty() for all nodes
edge.clear_fired() for all edges
```

### 4.3 增量计算策略

**三级跳过机制**：

| 级别 | 检查点 | 跳过条件 | 节省的计算 |
|------|--------|---------|-----------|
| L1 | 数据层 | `data_hash == last_data_hash` | 全部计算（零开销） |
| L2 | 节点层 | `not node.is_dirty` | 该节点的所有出边计算 |
| L3 | 边层 | `not edge.is_fired` | 该边的过滤+传播 |
| L4 | 公式层 | `filter_cache` 命中 | 公式计算本身 |

**与现有系统的对应**：
- L1 对应 `_hash_bar_data()` + `_has_bar_data_changed()`
- L2 对应 `_mark_dirty()` + `_is_dirty()` + `_update_node_snapshot()`
- L4 对应 `_filter_cache`（LRUCache）

### 4.4 公式结果缓存策略

**缓存键构成**：
```
cache_key = md5(
    formula_id +          # 公式标识
    symbols +             # 股票列表
    period +              # 周期
    bar_data_hash         # 行情数据哈希
)
```

**缓存 TTL 策略**（复用现有 FormulaCache）：

| 周期 | TTL | 说明 |
|------|-----|------|
| tick | 0（不缓存） | 实时变化太快 |
| 1m | 60s | 分钟级数据 |
| 5m / 15m / 30m / 60m | 300s | 分钟级 |
| 1d / 周 / 月 | 86400s | 日线级 |

**缓存失效触发**：
- 手动失效：分钟闭合时调用 `invalidate_on_minute_close()`
- 自动失效：TTL 过期自动淘汰
- 容量淘汰：LRU 策略

---

## 5. 执行顺序与拓扑分离

### 5.1 拓扑是什么

**拓扑 = 节点之间的连接关系（谁连谁）**，是结构属性。

由 `Topology` 类管理：
- `adjacency_out[node_id] = [edge, ...]` — 节点的出边
- `adjacency_in[node_id] = [edge, ...]` — 节点的入边
- `depths[node_id] = depth` — 节点深度（拓扑排序结果）

**拓扑的作用**：
1. 确定数据流向（从上游到下游）
2. 确定脏标记传播路径
3. 计算节点深度（用于可视化）

**拓扑的来源**：pool_config 中的 nodes + edges 静态定义，编译期一次性构建。

### 5.2 执行顺序是什么

**执行顺序 = 边的处理次序（先处理哪条边，后处理哪条边）**，是行为属性。

由 `execution_order` 列表管理，元素是边 ID 的有序列表。

**执行顺序的来源**：
1. **用户指定**：pool_config 中边的顺序（列表顺序即执行顺序）
2. **拓扑深度**：作为默认/保底顺序（深度小的先执行）
3. **变换单元约束**：同一变换单元的入边必须在出边之前

### 5.3 两者怎么配合

**编译期**：
```
pool_config
    │
    ├─→ TopologyBuilder.build() → Topology 对象（连接关系）
    │
    └─→ ExecutionPlanner.plan() → execution_order 列表（执行顺序）
            │
            └─ 算法：
               1. 按用户给出的边列表顺序
               2. 同时满足拓扑依赖（源节点深度 <= 目标节点深度）
               3. 违反拓扑依赖时，按拓扑深度重排并告警
```

**运行期**：
```
每次 tick:
  按 execution_order 顺序遍历所有边
      对每条边：
          检查触发条件（edge_fired AND node_dirty AND data_dirty）
          满足则执行
          不满足则跳过
```

**为什么需要分离**：
- 拓扑是"能不能连"（结构合法性）
- 执行顺序是"先算哪个"（计算次序）
- 用户可能有意识地安排边的执行顺序（如先处理A边再处理B边，因为B依赖A的副作用）
- 拓扑只保证依赖关系，不保证用户期望的计算顺序

### 5.4 怎么保证结果正确

**正确性保证机制**：

1. **拓扑合法性检查**（编译期）：
   - 检测循环依赖（环）
   - 检测孤立节点
   - 验证边的端点存在

2. **执行顺序与拓扑一致性检查**（编译期）：
   - 如果用户指定的执行顺序违反拓扑依赖，发出警告
   - 自动调整为满足拓扑依赖的顺序（尽量贴近用户顺序）

3. **数据驱动保证**（运行期）：
   - 只有源节点脏或数据脏时才执行
   - 执行后目标节点自动变脏
   - 下游边在后续的 execution_order 位置会被触发
   - 即使执行顺序与拓扑不同，脏标记机制保证最终所有受影响的节点都被更新

4. **单 tick 内多次遍历？不需要**：
   - 因为 execution_order 是按拓扑深度排序的（默认情况）
   - 上游先执行，下游后执行
   - 一次遍历即可完成所有级联更新

---

## 6. 表驱动配置

### 6.1 精简后的核心配置表

从现有 50+ 张配置表中，保留以下核心表（按重要性排序）：

| 序号 | 配置表 | 用途 | 被谁使用 |
|------|--------|------|---------|
| 1 | `edge_semantics.json` | 边类型语义（条件边/无条件边的定义、触发规则、三元组组装规则） | TopologyBuilder / EdgeFactory |
| 2 | `timing.json` | 时机门控规则（starttype × cxtype = 24种时机的表达式） | ConditionalEdge / TimingGate |
| 3 | `dispatch.json` | 条件分派规则（nset → gateway → engine） | ConditionNode / FilterDispatcher |
| 4 | `engines.json` | 公式引擎配置（网关兼容性、默认引擎） | FormulaRouter |
| 5 | `edge_strategies.json` | 边策略（不同节点类型组合的处理动作） | EdgeStrategy / EdgeExecutor |
| 6 | `pool_roles.json` | 池角色定义（目标池/备选池/废弃池） | StatePoolNode / SignalGenerator |
| 7 | `tdx_noperate_rules.json` | 比较操作符规则（大于/小于/上穿/下穿/排名等） | ConditionEvaluator |
| 8 | `tdx_psatt.json` | 状态池属性（TTL、告警、保存历史等） | StatePoolNode |
| 9 | `formula_routing.json` | 公式路由规则（简单/复杂公式的引擎选择） | FormulaRouter |
| 10 | `data_config.json` | 数据配置（市场代码、缓存策略） | Engine / DataAdapter |
| 11 | `runtime_tables_schema.json` | 运行时表定义 | RuntimeState |
| 12 | `defaults.json` | 默认值（兜底配置） | 所有组件 |

### 6.2 每张表干什么

#### 1. edge_semantics.json
- 定义两种边类型：conditional / unconditional
- 每种边的源节点类型、目标节点类型
- 触发规则（gate_and_change / source_changed）
- 运算流程（["gate", "filter", "propagate"] / ["propagate"]）
- 变换单元三元组的组装规则

#### 2. timing.json
- starttype 8种（立即/延迟/开市前/开市后/收市前/收市后/指定交易时间/指定时间）
- cxtype 3种（一直/持续窗口/只一次）
- 每种组合的判定表达式

#### 3. dispatch.json
- nset 分派（0/1/2 公式型，3/4 标量型，5 集合运算型）
- 每个 nset 对应的 gateway
- 位掩码路由规则

#### 4. engines.json
- 引擎定义（python_engine / hqchart_engine）
- 兼容的 gateway 列表
- 是否为默认引擎

#### 5. edge_strategies.json
- 节点类型组合 → 处理动作映射
- 如 market_source:* → resolve_and_pass
- 如 transfer_condition:stock_state_pool → apply_filter
- 动作的 handler 名称

#### 6. pool_roles.json
- 角色定义：target_pool / candidate_pool / sink_pool / transfer_condition / market_source
- 角色解析规则（优先级）
- 每个角色的属性（is_target / generate_buy_signal 等）

#### 7. tdx_noperate_rules.json
- 比较操作符定义（noperate ID → 规则）
- 规则字段：expr / prev_expr / curr_expr / combine
- 排名模式定义

#### 8. tdx_psatt.json
- 状态池属性字段定义
- TTL 单位映射（ndeltype → 秒数）
- 自动 TTL 节点类型列表

#### 9. formula_routing.json
- 简单公式函数列表
- 路由决策规则
- 引擎方法映射表

#### 10. data_config.json
- 市场代码前缀/后缀
- 缓存策略（max_entries / TTL）
- 数据注入规则

#### 11. runtime_tables_schema.json
- 运行时表的列表和类型
- 用于初始化 _rt 命名空间

#### 12. defaults.json
- 所有配置缺失时的兜底默认值
- 参数别名
- 安全求值函数列表

### 6.3 引擎怎么查表

**查表原则**：
1. 编译期能查的，编译期查（结果存入 compiled 对象）
2. 运行期只读编译结果，不直接读配置表
3. 配置表只在 Engine 初始化时加载一次

**编译期查表流程**：
```
Engine.compile(pool_config):
    │
    ├─ 加载所有配置表（如果未加载）
    │
    ├─ 对每条边：
    │   ├─ 查 edge_semantics → 确定边类型（conditional/unconditional）
    │   ├─ 查 timing.json → 预计算 timing_spec
    │   ├─ 查 dispatch.json + engines.json → 预计算 filter_spec
    │   └─ 查 edge_strategies.json → 预计算 strategy_action
    │
    ├─ 对每个节点：
    │   ├─ 查 pool_roles.json → 确定节点角色
    │   └─ 查 tdx_psatt.json → 预计算 ttl_config / alert_config
    │
    └─ 返回 CompiledSchedule（包含所有预计算结果）
```

**运行期查表**：
运行期不直接读配置表，所有需要的信息都已预计算到 Node/Edge 对象的属性中。

---

## 7. 与现有系统的关系

### 7.1 哪些复用

| 组件 | 复用方式 | 说明 |
|------|---------|------|
| `FormulaRouter` | 直接复用 | 公式路由、双引擎切换、缓存策略均不变 |
| `PythonFormulaEngine` | 直接复用 | Python 公式引擎 |
| `FormulaCache` | 直接复用 | 公式结果缓存 |
| `DataQuery` | 直接复用 | 数据查询接口 |
| `evaluators.py` | 直接复用 | TDX 条件评估器（nset 评估） |
| `CompiledSchedule` | 改造复用 | 保留结构，增加 OO 对象引用 |
| `LRUCache` | 直接复用 | LRU + TTL 缓存 |
| `配置表（所有 JSON）` | 直接复用 | 50+ 张配置表全部保留 |
| `数据适配器（tq_adapter 等）` | 直接复用 | 数据源接入层不变 |
| `候选池服务（candidate_pool.py）` | 直接复用 | 候选股解析逻辑 |

### 7.2 哪些重写

| 组件 | 重写方式 | 说明 |
|------|---------|------|
| `MetaEngine` | 重构为 `EventDrivenEngine` | 上帝类拆分为 Node/Edge/EventBus/Topology/DirtyManager 等 |
| `engine.py` 中的大函数 | 分散到各 Node/Edge 子类 | 如 `_filter_conditional` → `ConditionNode.evaluate()` |
| `_run_tick_event_driven` | 重写为事件驱动循环 | 从"按拓扑序遍历边"改为"事件触发 + 执行顺序" |
| `_prepare_topology` | 重写为 `TopologyBuilder` | 从返回 dict 改为返回 Topology 对象 |
| `_compile_pool` | 重写为 `Compiler` | 从构建 dict 改为构建 Node/Edge 对象图 |
| `_process_edge_pipeline` | 拆分为 Edge 子类的 execute 方法 | gate → filter → propagate 拆分 |
| 三套 filter | 统一为 ConditionNode.evaluate() | 消除 conditional / formula_eval / unconditional 的重复代码 |

### 7.3 怎么保证向后兼容

**三层兼容保证**：

#### 第一层：API 兼容
- `MetaEngine` 类名保留，作为 Facade
- 所有现有公共方法保留：`run_pool()` / `start_loop()` / `stop_loop()` / `get_event_queue()` 等
- 内部委托给 `EventDrivenEngine`
- 所有现有属性通过 `__getattr__` 代理到新对象

#### 第二层：数据结构兼容
- `pool_config` 输入格式完全不变（nodes + edges 的 dict 结构）
- `node_stocks` 输出格式完全不变（{node_id: [stock_dict, ...]}）
- 事件格式完全不变（ENTER / EXIT 等事件的字段）
- `CompiledSchedule` 结构保留，新增 OO 对象字段

#### 第三层：行为等价
- 编写行为等价性测试（已有 `test_event_driven_equivalence.py`）
- 同一 pool_config + 同一输入数据 → 同一输出结果
- 所有现有 simtests / tests 必须全部通过
- 性能不劣化（数据稀疏时更快，数据全量时相当）

**迁移路径**：
```
v1.0: 新架构并行运行（可通过开关切换），行为等价验证
v1.1: 新架构设为默认，旧架构保留作 fallback
v1.2: 移除旧架构代码
```

---

## 附录：文件结构规划

```
meta_core/
├── core/
│   ├── engine.py              # 保留 MetaEngine Facade（向后兼容）
│   ├── oo_engine.py           # 新增：EventDrivenEngine 主类
│   ├── nodes.py               # 新增：Node 类体系
│   ├── edges.py               # 新增：Edge 类体系
│   ├── events.py              # 新增：Event 类体系 + EventBus
│   ├── topology.py            # 新增：Topology 类
│   ├── dirty_manager.py       # 新增：DirtyManager 类
│   ├── compiler.py            # 新增：编译期逻辑
│   ├── formula_router.py      # 保留（复用）
│   ├── formula_engine.py      # 保留（复用）
│   ├── evaluators.py          # 保留（复用）
│   ├── table_engine.py        # 保留
│   ├── replay.py              # 保留
│   └── simulator.py           # 保留
├── config/                    # 保留所有配置表
├── services/                  # 保留所有服务
└── tests/
    └── test_oo_engine.py      # 新增：新架构测试
```
