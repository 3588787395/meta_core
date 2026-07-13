# v1.5 数据链路闭环 + Node/Edge/Topology详细设计 + 等价性测试

## 0. 设计原则与约束

**现状**：v1.4 设计评审通过，但 3 个 P1 问题需升级解决，3 个核心类（Node/Edge/Topology）缺失详细设计，等价性验证体系空白。

**v1.5 目标**：补全数据链路闭环，补全 Node/Edge/Topology 三大核心类详细设计，建立 TimingGate + FilterEvaluator 的等价性验证体系，达到可编码、可验证的标准。

**设计原则（继承 v1.4）**：
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
- **生命周期匹配**：对象的生命周期必须与其职责匹配，不同生命周期的对象不能混在一个容器里
- **消费即清空**：Pending 变更集被消费后必须立即清空，作为下一轮累计的起点
- **数据流完整**：从数据到达，到下游传播，每一环都必须有明确的触发者和接收者
- **先验证后消费**：任何资源消费前，必须先验证其有效性，验证失败则不消费
- **单线程事件循环**：事件循环本身是单线程的，所有状态变更都在事件循环线程中完成
- **查表驱动优先**：复杂的多分支逻辑优先用查表（表驱动）实现，减少 if/elif 嵌套

**v1.5 新增原则**：
- **链路闭环**：每条数据从进入到产生最终结果，必须有明确的、可追踪的完整路径
- **版本语义明确**：每个版本号（data_version / node.version）的递增者、递增时机、递增语义必须精确定义
- **契约先行**：类与类之间的交互契约（调用次数、输入输出、状态变化）必须在设计阶段明确
- **等价性可证**：每个核心组件的设计必须能被等价性测试验证，不能"只能靠人工判断"

---

## 1. 核心类图（v1.5 更新：补全 Node/Edge/Topology）

### 1.1 服务与上下文拆分（v1.5 不变）

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
  ├── data_version: int
  ├── bar_time: datetime
  ├── bar_data: dict
  ├── data_dirty: bool
  ├── events_fired: int
  ├── max_iterations: int
  └── formula_cache: FormulaCache

EngineContext（组合对象，方便传递）
  ├── services: EngineServices
  └── tick: TickContext
```

---

### 1.2 完整类层次图（v1.5 新增）

```
                    ┌──────────────────┐
                    │ EventDrivenEngine│
                    │  (协调者)        │
                    └────────┬─────────┘
                             │ 组合
           ┌─────────────────┼─────────────────┐
           ▼                 ▼                 ▼
     ┌──────────┐     ┌──────────┐     ┌──────────┐
     │ EventLoop│     │ Topology │     │  Metrics │
     │ (调度器) │     └────┬─────┘     └──────────┘
     └────┬─────┘          │ 引用
          │                ▼
          │           ┌──────────┐
          │           │   Node   │◄─────────────────┐
          │           │ (基类)   │                  │
          │           └────┬─────┘            ┌───┴────┐
          │                │ 继承             │ source │
          │          ┌─────┴──────┐           │ target │
          │          ▼            ▼           └───┬────┘
          │     ┌────────┐ ┌────────────┐          │
          │     │Source  │ │Condition   │    ┌─────┴──────┐
          │     │Node    │ │Node        │    │   Edge     │
          │     └────────┘ └────────────┘    │  (基类)     │
          │          │            │          └─────┬──────┘
          │          └──────┬─────┘                │ 组合
          │                 ▼                ┌─────┴──────┐
          │           ┌──────────┐           ▼            ▼
          │           │StatePool │     ┌──────────┐ ┌──────────┐
          │           │Node      │     │Conditional│ │Uncond-   │
          │           └────┬─────┘     │Edge      │ │itional   │
          │                │           └─────┬────┘ └──────────┘
          │                ▼                 │ 组合
          │           ┌──────────┐     ┌─────┴──────┐
          │           │TargetPool│     │TimingGate│ │FilterEval│
          │           │Node      │     │ (时机判断)│ │   uator  │
          │           └──────────┘     └──────────┘ └─────┬────┘
          │                                               │ 调用
          │                                               ▼
          │                                         ┌──────────┐
          │                                         │Formula   │
          │                                         │  Router  │
          │                                         └──────────┘
          │
          │ 事件出队 / 入队
          ▼
     ┌──────────┐
     │EventQueue│
     │ (优先级) │
     │ + 去重   │
     │ + 合并   │
     └──────────┘
```

---

## 2. 数据链路闭环（v1.5 P0-1）

### 2.1 问题回顾

**v1.4 的问题**：
1. `node.version` 谁递增？何时递增？语义是什么？——不明确
2. `data_version` 与 `node.version` 的关系？——说不清
3. `TickContext.current_data_ts` 与 `FormulaCache` 的关系？——只说了生命周期，没说数据关联
4. 一条数据从进入系统到产生最终结果，完整路径是什么？——缺失
5. 数据推送 → 版本号递增 → 事件入队 → 事件处理 → 状态更新 → 下游事件 的链路不闭合

**后果**：
- 开发时不知道版本号该怎么维护
- 出问题时难以追踪数据流向
- 等价性测试无法设计（不知道每一步的预期是什么）

---

### 2.2 核心概念澄清

#### 2.2.1 两种版本号的分工

| 版本号 | 谁维护 | 递增时机 | 语义 | 粒度 |
|--------|--------|---------|------|------|
| `TickContext.data_version` | EventDrivenEngine（数据推送入口） | 每次有新数据到达时 | 全局数据版本，标识当前数据的新鲜度 | 全局（所有股票共享） |
| `Node.version` | Node 自身（在事件循环线程中） | 节点的股票集合发生变化时 | 节点状态版本，标识节点股票集合的版本 | 节点级（每个节点独立） |

**关键区别**：
- `data_version` 是**数据版本**：回答"现在的数据是第几批的？"
- `node.version` 是**节点版本**：回答"这个节点的股票集合变了几次？"

**两者的关系**：
- data_version 变化 → 可能导致某些节点的股票集合变化 → 导致 node.version 递增
- 但 data_version 变化不一定导致所有 node.version 都递增（有些节点可能不受影响）
- node.version 递增一定是因为某种数据变化（直接或间接）

---

#### 2.2.2 TickContext.current_data_ts 与 FormulaCache 的关系

```
current_data_ts（当前数据时间戳）
    │
    ├── 用于判断数据的时间位置（开盘/收盘/指定时间点等）
    ├── 用于 TimingGate 的时间判断（周期触发、持续时长等）
    └── 隐含决定了 FormulaCache 的有效性
            │
            └── 同一个 current_data_ts 下，数据是不变的
                → FormulaCache 的结果是可信的
                → 所以 FormulaCache 放在 TickContext 里
```

**为什么 FormulaCache 不需要以 current_data_ts 为 key？**
- 因为 FormulaCache 已经是 tick 级的了
- 同一个 tick 内，current_data_ts 是不变的
- 不同 tick 的 FormulaCache 是不同的实例，天然隔离

---

### 2.3 node.version 的递增规则

#### 2.3.1 谁来递增？

**答案：Node 自身在 `_apply_stock_changes` 方法中递增**

```python
class Node:
    def _apply_stock_changes(self, added: set, removed: set) -> bool:
        """应用股票变更，返回是否真的有变化
        
        注意：本方法只能在事件循环线程中调用
        """
        actual_added = added - self._stocks
        actual_removed = removed & self._stocks
        
        if not actual_added and not actual_removed:
            return False  # 没有实际变化，不递增版本号
        
        self._stocks = (self._stocks - actual_removed) | actual_added
        self.version += 1  # ← 在这里递增！
        self._dirty = True
        
        return True
```

**为什么是 Node 自己递增？**
1. **封装性**：版本号是节点内部状态的一部分，应该由节点自己维护
2. **原子性**：股票集合变化 + 版本号递增是一个原子操作，放在同一个方法里
3. **防遗漏**：外部调用者不需要记得"改了股票集合还要改版本号"
4. **一致性**：所有导致股票集合变化的路径都走同一个方法，版本号不会乱

---

#### 2.3.2 何时递增？

**递增触发点（全部在事件循环线程中）**：

| 节点类型 | 递增触发场景 | 调用路径 |
|---------|-------------|---------|
| **SourceNode** | 数据推送导致源池股票变化 | `on_data_updated` → `SourceNode.update_stocks()` → `_apply_stock_changes()` |
| **ConditionNode** | 公式重算导致条件节点股票变化 | `Edge._do_execute()` → `ConditionNode.recompute()` → `_apply_stock_changes()` |
| **StatePoolNode** | 上游边传播导致状态池股票变化 | `Edge._do_execute()` → `StatePoolNode.add_stocks()` / `remove_stocks()` → `_apply_stock_changes()` |
| **TargetPoolNode** | 上游边传播导致目标池股票变化 | `Edge._do_execute()` → `TargetPoolNode.add_stocks()` / `remove_stocks()` → `_apply_stock_changes()` |

**不递增的场景**：
1. 只读操作（查询股票集合、查询版本号）
2. 没有实际变化的"假更新"（added 和 removed 都为空）
3. 非事件循环线程（不允许修改节点状态）

---

#### 2.3.3 递增的语义

**node.version 的精确语义**：
> 节点股票集合（stocks set）的变更次数计数器。
> 每次股票集合发生实际变化（有新增或移除）时，version 加 1。
> version 单调递增，不回退，不重置。

**用 version 可以做什么**：
1. **墓碑判断**：事件携带的 version < 当前 version → 事件是旧的，跳过
2. **变化检测**：前后两次 version 不同 → 中间状态变了
3. **调试追踪**：通过 version 可以知道节点经历了多少次变化

**用 version 不能做什么**：
1. **不能判断变化了什么**：只知道变了，不知道具体哪只股票变了（那是 pending 的事）
2. **不能跨节点比较**：不同节点的 version 之间没有关系
3. **不能判断时间先后**：同一节点内是单调的，但不同节点的 version 没有时序关系

---

### 2.4 完整数据链路时序图（文字版）

#### 2.4.1 总览：一条数据的完整旅程

```
阶段1：数据推送阶段（数据推送线程）
─────────────────────────────────────────────────────
  外部数据源(TQ/HQChart)
        │
        ▼
  [1] 数据推送到 DataQuery
        │
        ▼
  [2] 更新数据缓存（DataQuery 内部）
        │
        ▼
  [3] 触发 data_updated 回调
        │
        ▼
  [4] EventDrivenEngine.on_data_updated()
        │
        ├── [4a] data_version += 1（全局数据版本递增）
        ├── [4b] 创建新的 TickContext
        ├── [4c] 设置 current_data_ts / bar_time / bar_data
        ├── [4d] 设置 data_dirty = True
        └── [4e] enqueue_data_updated_event() 入队
        │
        ▼
  [5] DataUpdatedEvent 进入 EventQueue
        │
        └── 等待事件循环处理

阶段2：事件处理阶段（事件循环线程）
─────────────────────────────────────────────────────
  [6] EventLoop.run_tick() 开始
        │
        ▼
  [7] DataUpdatedEvent 出队
        │
        ▼
  [8] on_data_updated 处理器
        │
        ▼
  [9] 遍历所有 SourceNode，更新数据
        │
        ▼
 [10] SourceNode.update_stocks()
        │
        ├── [10a] 从 DataQuery 获取最新股票列表
        ├── [10b] 计算 added / removed 集合
        ├── [10c] _apply_stock_changes(added, removed)
        │         ├── 更新 self._stocks
        │         └── 【关键】self.version += 1（如果有变化）
        └── [10d] 如果有变化，enqueue_node_changed_event(node_id, version, added, removed)
        │
        ▼
 [11] NodeStockChangedEvent 进入 EventQueue
        │
        └── （多个 SourceNode 可能产生多个事件，按优先级排序）

阶段3：节点变化传播阶段（事件循环线程）
─────────────────────────────────────────────────────
 [12] NodeStockChangedEvent 出队
        │
        ▼
 [13] on_node_stock_changed 处理器
        │
        ├── [13a] 验证事件 version == 节点 version（墓碑检查）
        ├── [13b] 验证失败 → 直接返回，pending 保留
        └── [13c] 验证通过 → 消费 pending，清空
        │
        ▼
 [14] 遍历节点的所有出边 (out_edges)
        │
        ▼
 [15] 对每条边，调用 edge.should_execute(ctx)
        │
        ├── [15a] TimingGate.should_trigger(ctx) → 判断时机
        └── [15b] 时机满足 → enqueue_edge_execute(edge_id)
        │
        ▼
 [16] EdgeExecuteEvent 进入 EventQueue
        │
        └── （多条边可能产生多个事件，自动去重）

阶段4：边执行阶段（事件循环线程）
─────────────────────────────────────────────────────
 [17] EdgeExecuteEvent 出队
        │
        ▼
 [18] on_edge_execute 处理器
        │
        ▼
 [19] edge.execute(ctx)
        │
        ├── [19a] 再次调用 TimingGate.should_trigger(ctx) 确认
        ├── [19b] 时机不满足 → 直接返回
        ├── [19c] 时机满足 → 计算上游节点股票集合
        │         └── upstream_node.stocks（取当前状态）
        ├── [19d] FilterEvaluator.evaluate(upstream_stocks, ctx)
        │         ├── 查 FormulaCache（命中则直接返回）
        │         └── 未命中 → FormulaRouter 计算 → 写入缓存
        ├── [19e] 得到 passed / rejected 集合
        └── [19f] 下游节点接收变更
                  └── downstream_node.add_stocks(passed) / remove_stocks(rejected)
        │
        ▼
 [20] 下游节点 _apply_stock_changes()
        │
        ├── 更新股票集合
        └── 【关键】下游节点 version += 1（如果有变化）
        │
        ▼
 [21] 如果下游节点有变化，enqueue_node_changed_event(...)
        │
        ▼
 [22] 回到阶段3，继续传播...
        │
        ▼
 [23] 直到没有新事件产生，tick 结束

阶段5：结果产出阶段（事件循环线程）
─────────────────────────────────────────────────────
 [24] TargetPoolNode 股票集合变化
        │
        ├── [24a] 产生信号（买入/卖出信号）
        ├── [24b] 触发告警通知
        └── [24c] 通过 EventBus 发布外部事件
        │
        ▼
 [25] 外部系统接收事件（GUI、监控、交易系统等）
```

---

#### 2.4.2 关键节点的输入输出

##### SourceNode 数据更新

```
输入：
  - 新数据（bar_data / tick_data）
  - 旧的股票集合（self._stocks）
处理：
  - 从 DataQuery 获取满足条件的股票列表
  - 计算 added = new_stocks - old_stocks
  - 计算 removed = old_stocks - new_stocks
  - _apply_stock_changes(added, removed)
输出：
  - 更新后的 self._stocks
  - self.version（如果有变化则 +1）
  - NodeStockChangedEvent（如果有变化则入队）
```

##### ConditionalEdge 执行

```
输入：
  - 上游节点股票集合（upstream_node.stocks）
  - TimingGate 状态（时机是否满足）
  - FilterEvaluator 配置（过滤条件）
  - FormulaCache（可能有缓存）
处理：
  - TimingGate.should_trigger() → 时机判断
  - FilterEvaluator.evaluate(upstream_stocks) → 过滤
    ├── 查 FormulaCache
    └── 未命中则调用 FormulaRouter 计算
  - 得到 passed 集合
输出：
  - 下游节点股票集合变化（add / remove）
  - 下游节点 version（如果有变化则 +1）
  - TimingGate 内部状态更新（record_trigger）
```

---

### 2.5 链路闭合验证点

| 验证点 | 验证方法 | 预期 |
|--------|---------|------|
| **数据推送 → data_version 递增** | 推送一批数据，检查 data_version | data_version = 旧值 + 1 |
| **data_version 递增 → TickContext 更新** | 检查新的 TickContext | tick.data_version == 全局 data_version |
| **SourceNode 数据更新 → node.version 递增** | 更新源池数据，检查 version | version = 旧值 + 1（如果有变化） |
| **node.version 递增 → NodeStockChangedEvent 入队** | 监控事件队列 | 队列中有对应节点的事件 |
| **NodeStockChangedEvent → 边触发判断** | 处理节点变化事件 | 边执行事件入队（如果时机满足） |
| **EdgeExecuteEvent → 下游节点变化** | 执行边，检查下游节点 | 下游节点股票集合变化，version 递增 |
| **下游节点变化 → 继续传播** | 多级拓扑 | 变化逐级传播到最下游 |
| **TargetPool 变化 → 外部事件** | 监控 EventBus | 有 PoolChangedEvent 等发布 |

**链路闭合的标志**：
从"数据推送"开始，到"TargetPoolNode 产生外部事件"结束，每一步都有明确的
- 触发者（谁发起的）
- 输入（什么数据进来）
- 输出（什么结果出去）
- 状态变化（哪些状态变了）

---

### 2.6 与旧引擎的链路对比

| 阶段 | 旧引擎 | 新引擎（v1.5） |
|------|--------|---------------|
| 数据接收 | `OnRecvNewData` 回调 | DataQuery + data_updated 事件 |
| 数据版本 | 隐式（靠时间戳判断） | 显式 data_version |
| 源池更新 | `_tdx_update_source_pool` | SourceNode.update_stocks() |
| 条件计算 | `_tdx_calc_condition` | ConditionNode.recompute() |
| 状态传播 | `_tdx_dispatch` + 递归 | 事件队列 + 边执行 |
| 版本管理 | 无（靠"标志位"判断） | node.version 单调递增 |
| 可观测性 | 只能打 log 调试 | 事件队列 + 版本号 + 指标 |

---

## 3. Node 类详细设计（v1.5 P1-1）

### 3.1 Node 基类

#### 3.1.1 职责

**Node（节点）** 是股票池拓扑图中的基本计算单元，代表一个"股票集合"。
每个节点持有一个股票代码集合，以及相关的元数据。

**核心职责**：
1. 持有并维护一个股票代码集合（`_stocks`）
2. 维护版本号（`version`），用于变化检测和墓碑判断
3. 管理脏标记（`_dirty`），标识状态是否需要处理
4. 提供统一的变更接口（`add_stocks` / `remove_stocks` / `_apply_stock_changes`）
5. 维护入边/出边引用（通过 Topology 管理）

**不做什么**：
- 不负责执行逻辑（那是 Edge 的事）
- 不负责拓扑结构（那是 Topology 的事）
- 不负责事件入队（由调用者负责）

---

#### 3.1.2 属性列表

| 属性 | 类型 | 可见性 | 说明 |
|------|------|--------|------|
| `node_id` | `str` | public | 节点唯一标识 |
| `name` | `str` | public | 节点名称（可读，用于调试） |
| `node_type` | `str` | public | 节点类型（"source"/"condition"/"state_pool"/"target_pool"） |
| `version` | `int` | public | 节点版本号（单调递增） |
| `config` | `dict` | public | 节点配置（从 pool.json 等解析） |
| `_stocks` | `frozenset[str]` | private | 股票代码集合（内部存储，不可变） |
| `_dirty` | `bool` | private | 脏标记：本 tick 内是否有未处理的变化 |
| `_metadata` | `dict` | private | 附加元数据（扩展用） |

**为什么 `_stocks` 用 frozenset？**
1. **不可变**：防止外部代码意外修改内部状态
2. **线程安全读**：虽然写只能在事件循环线程，但读可以多线程（引用是原子的）
3. **版本一致性**：每次变化都创建新的 frozenset，version 和 stocks 是一致的

---

#### 3.1.3 方法列表

| 方法 | 签名 | 可见性 | 说明 |
|------|------|--------|------|
| `__init__` | `(node_id: str, name: str, config: dict)` | public | 构造函数 |
| `stocks` | `@property -> frozenset[str]` | public | 获取当前股票集合（只读） |
| `count` | `@property -> int` | public | 获取股票数量 |
| `is_dirty` | `@property -> bool` | public | 是否有未处理的变化 |
| `contains` | `(code: str) -> bool` | public | 检查某只股票是否在节点中 |
| `add_stocks` | `(stocks: set[str]) -> bool` | public | 添加股票，返回是否有变化 |
| `remove_stocks` | `(stocks: set[str]) -> bool` | public | 移除股票，返回是否有变化 |
| `set_stocks` | `(stocks: set[str]) -> bool` | public | 设置整个股票集合，返回是否有变化 |
| `clear_dirty` | `() -> None` | public | 清除脏标记（事件处理完后调用） |
| `_apply_stock_changes` | `(added: set[str], removed: set[str]) -> bool` | protected | 核心：应用变更，递增 version |
| `reset` | `() -> None` | public | 重置状态（清空股票，version 归零） |
| `to_dict` | `() -> dict` | public | 序列化（调试/监控用） |

---

#### 3.1.4 核心方法伪代码

```python
class Node:
    """节点基类
    
    代表拓扑图中的一个节点，持有一个股票集合。
    所有状态变更都必须在事件循环线程中完成。
    """
    
    def __init__(self, node_id: str, name: str, config: dict | None = None):
        self.node_id = node_id
        self.name = name
        self.node_type = "base"  # 子类覆盖
        self.config = config or {}
        self.version = 0
        self._stocks = frozenset()
        self._dirty = False
        self._metadata = {}
    
    @property
    def stocks(self) -> frozenset[str]:
        """当前股票集合（只读）"""
        return self._stocks
    
    @property
    def count(self) -> int:
        """股票数量"""
        return len(self._stocks)
    
    @property
    def is_dirty(self) -> bool:
        """是否有未处理的变化"""
        return self._dirty
    
    def contains(self, code: str) -> bool:
        """检查某只股票是否在节点中"""
        return code in self._stocks
    
    def add_stocks(self, stocks: set[str]) -> bool:
        """添加股票
        
        Args:
            stocks: 要添加的股票代码集合
            
        Returns:
            True 表示有实际变化，False 表示没有变化
        """
        return self._apply_stock_changes(added=stocks, removed=set())
    
    def remove_stocks(self, stocks: set[str]) -> bool:
        """移除股票
        
        Args:
            stocks: 要移除的股票代码集合
            
        Returns:
            True 表示有实际变化，False 表示没有变化
        """
        return self._apply_stock_changes(added=set(), removed=stocks)
    
    def set_stocks(self, stocks: set[str]) -> bool:
        """设置整个股票集合
        
        用新集合替换旧集合，自动计算 added/removed。
        
        Args:
            stocks: 新的股票代码集合
            
        Returns:
            True 表示有实际变化，False 表示没有变化
        """
        new_stocks = set(stocks)
        old_stocks = set(self._stocks)
        added = new_stocks - old_stocks
        removed = old_stocks - new_stocks
        return self._apply_stock_changes(added, removed)
    
    def clear_dirty(self):
        """清除脏标记
        
        在节点变化事件处理完后调用，表示变化已经被下游感知。
        """
        self._dirty = False
    
    def _apply_stock_changes(self, added: set[str], removed: set[str]) -> bool:
        """应用股票变更（核心方法）
        
        计算实际变化，更新股票集合，递增版本号，设置脏标记。
        
        Args:
            added: 要添加的股票
            removed: 要移除的股票
            
        Returns:
            True 表示有实际变化，False 表示没有变化
            
        注意：本方法只能在事件循环线程中调用
        """
        current = set(self._stocks)
        
        actual_added = added - current
        actual_removed = removed & current
        
        if not actual_added and not actual_removed:
            return False  # 没有实际变化
        
        # 更新股票集合
        new_stocks = (current - actual_removed) | actual_added
        self._stocks = frozenset(new_stocks)
        
        # 递增版本号（关键！）
        self.version += 1
        
        # 设置脏标记
        self._dirty = True
        
        return True
    
    def reset(self):
        """重置状态"""
        self._stocks = frozenset()
        self.version = 0
        self._dirty = False
    
    def to_dict(self) -> dict:
        """序列化（调试用）"""
        return {
            "node_id": self.node_id,
            "name": self.name,
            "node_type": self.node_type,
            "version": self.version,
            "count": self.count,
            "is_dirty": self._dirty,
        }
```

---

#### 3.1.5 发出的事件

Node 基类**不直接发出事件**。事件入队是由调用 Node 变更方法的代码负责的（通常是 Edge 或 EventDrivenEngine）。

**间接关联的事件**：
- `NodeStockChangedEvent`：节点股票集合变化后，由调用者入队

---

#### 3.1.6 接收的事件

Node 基类**不直接接收事件**。事件处理在 EventLoop 的处理器中完成，处理器调用 Node 的方法。

---

### 3.2 SourceNode（数据源节点）

#### 3.2.1 职责

**SourceNode（源节点）** 是股票池的数据源入口，代表"初始股票池"。

**核心职责**：
1. 从 DataQuery 获取满足条件的初始股票列表（如：沪深A股、创业板、某板块等）
2. 数据更新时，重新计算股票集合
3. 作为拓扑图的"源头"，没有入边

**与旧引擎的对应**：
- 旧引擎：`source_pool` / 初始股票池
- 新引擎：`SourceNode`

---

#### 3.2.2 属性列表（继承 + 新增）

| 属性 | 类型 | 继承/新增 | 说明 |
|------|------|----------|------|
| `node_id` | `str` | 继承 | 节点唯一标识 |
| `name` | `str` | 继承 | 节点名称 |
| `node_type` | `str` | 覆盖 | "source" |
| `version` | `int` | 继承 | 版本号 |
| `_stocks` | `frozenset[str]` | 继承 | 股票集合 |
| `_dirty` | `bool` | 继承 | 脏标记 |
| `source_type` | `str` | 新增 | 源类型（"market"/"sector"/"concept"/"custom"等） |
| `source_params` | `dict` | 新增 | 源参数（市场代码、板块代码等） |
| `_last_update_ts` | `float` | 新增 | 上次更新时间戳 |

---

#### 3.2.3 方法列表（继承 + 新增）

| 方法 | 签名 | 继承/新增 | 说明 |
|------|------|----------|------|
| `__init__` | `(node_id, name, config)` | 覆盖 | 构造函数，解析 source_type/source_params |
| `stocks` | `@property` | 继承 | 获取股票集合 |
| `count` | `@property` | 继承 | 股票数量 |
| `add_stocks` | `(stocks) -> bool` | 继承 | 添加股票 |
| `remove_stocks` | `(stocks) -> bool` | 继承 | 移除股票 |
| `_apply_stock_changes` | `(added, removed) -> bool` | 继承 | 核心变更方法 |
| `clear_dirty` | `()` | 继承 | 清除脏标记 |
| `update_stocks` | `(ctx: EngineContext) -> bool` | 新增 | 从数据源更新股票列表（数据推送时调用） |
| `_fetch_stocks_from_source` | `(ctx: EngineContext) -> set[str]` | private | 从 DataQuery 获取股票列表 |
| `reset` | `()` | 继承 | 重置 |
| `to_dict` | `() -> dict` | 覆盖 | 序列化（增加 source_type） |

---

#### 3.2.4 核心方法伪代码

```python
class SourceNode(Node):
    """数据源节点
    
    股票池的数据源入口，从 DataQuery 获取初始股票列表。
    没有入边，是拓扑图的源头。
    """
    
    def __init__(self, node_id: str, name: str, config: dict | None = None):
        super().__init__(node_id, name, config)
        self.node_type = "source"
        
        # 解析源配置
        self.source_type = config.get("source_type", "market")
        self.source_params = config.get("source_params", {})
        self._last_update_ts = 0.0
    
    def update_stocks(self, ctx: EngineContext) -> bool:
        """从数据源更新股票列表
        
        在数据推送时被调用，重新从 DataQuery 获取股票列表，
        并更新自身状态。
        
        Args:
            ctx: 引擎上下文
            
        Returns:
            True 表示股票集合有变化，False 表示没有变化
        """
        # 从数据源获取最新股票列表
        new_stocks = self._fetch_stocks_from_source(ctx)
        
        # 更新股票集合（自动计算 added/removed，递增 version）
        changed = self.set_stocks(new_stocks)
        
        if changed:
            self._last_update_ts = ctx.tick.current_data_ts
        
        return changed
    
    def _fetch_stocks_from_source(self, ctx: EngineContext) -> set[str]:
        """从 DataQuery 获取股票列表
        
        根据 source_type 和 source_params 调用不同的 DataQuery 接口。
        """
        data_query = ctx.services.data_query
        
        if self.source_type == "market":
            # 按市场获取（如：沪A、深A、创业板等）
            market = self.source_params.get("market", "all")
            return set(data_query.get_stocks_by_market(market))
        
        elif self.source_type == "sector":
            # 按行业板块获取
            sector_code = self.source_params.get("sector_code", "")
            return set(data_query.get_stocks_by_sector(sector_code))
        
        elif self.source_type == "concept":
            # 按概念板块获取
            concept_code = self.source_params.get("concept_code", "")
            return set(data_query.get_stocks_by_concept(concept_code))
        
        elif self.source_type == "custom":
            # 自定义股票列表
            return set(self.source_params.get("stocks", []))
        
        else:
            # 未知类型，返回空集合
            logger.warning(f"未知的源类型: {self.source_type}")
            return set()
    
    def to_dict(self) -> dict:
        d = super().to_dict()
        d["source_type"] = self.source_type
        d["source_params"] = self.source_params
        d["last_update_ts"] = self._last_update_ts
        return d
```

---

#### 3.2.5 触发场景

**什么时候调用 `update_stocks`？**

| 触发场景 | 调用者 | 调用路径 |
|---------|--------|---------|
| 数据推送 | EventDrivenEngine | `on_data_updated` → 遍历所有 SourceNode → `node.update_stocks(ctx)` |
| 初始化 | EventDrivenEngine | `start()` → 初始化所有 SourceNode → `node.update_stocks(ctx)` |
| 手动刷新 | 外部 API | `engine.refresh_source(node_id)` → `node.update_stocks(ctx)` |

---

### 3.3 ConditionNode（条件节点）

#### 3.3.1 职责

**ConditionNode（条件节点）** 代表一个"条件选股"的结果，持有满足某个公式条件的股票集合。

**核心职责**：
1. 持有一个 FilterEvaluator（定义了条件公式）
2. 当上游数据变化时，重新计算满足条件的股票
3. 作为"计算节点"，输出是公式计算的结果

**与旧引擎的对应**：
- 旧引擎：条件选股节点 / `_tdx_calc_condition`
- 新引擎：`ConditionNode` + 入边的 `FilterEvaluator`

**重要设计决策**：
> ConditionNode 本身不计算公式，公式计算由入边的 FilterEvaluator 完成。
> ConditionNode 只是"持有结果"的节点。
> 这样设计的原因是：同一个条件可以被多个上游触发，计算逻辑统一在 Edge 中。

---

#### 3.3.2 属性列表（继承 + 新增）

| 属性 | 类型 | 继承/新增 | 说明 |
|------|------|----------|------|
| `node_id` | `str` | 继承 | 节点唯一标识 |
| `name` | `str` | 继承 | 节点名称 |
| `node_type` | `str` | 覆盖 | "condition" |
| `version` | `int` | 继承 | 版本号 |
| `_stocks` | `frozenset[str]` | 继承 | 股票集合 |
| `_dirty` | `bool` | 继承 | 脏标记 |
| `formula_id` | `str` | 新增 | 公式 ID（标识用） |
| `_last_compute_ts` | `float` | 新增 | 上次计算时间戳 |
| `_compute_count` | `int` | 新增 | 累计计算次数（指标用） |

---

#### 3.3.3 方法列表（继承 + 新增）

| 方法 | 签名 | 继承/新增 | 说明 |
|------|------|----------|------|
| `__init__` | `(node_id, name, config)` | 覆盖 | 构造函数 |
| `stocks` | `@property` | 继承 | 获取股票集合 |
| `add_stocks` | `(stocks) -> bool` | 继承 | 添加股票 |
| `remove_stocks` | `(stocks) -> bool` | 继承 | 移除股票 |
| `set_stocks` | `(stocks) -> bool` | 继承 | 设置股票集合 |
| `_apply_stock_changes` | `(added, removed) -> bool` | 继承 | 核心变更方法 |
| `clear_dirty` | `()` | 继承 | 清除脏标记 |
| `recompute` | `(stocks: set[str], ctx: EngineContext) -> bool` | 新增 | 重新计算条件（由 Edge 调用） |
| `reset` | `()` | 继承 | 重置 |
| `to_dict` | `() -> dict` | 覆盖 | 序列化 |

**注意**：`recompute` 方法实际上通常不需要，因为 ConditionNode 的股票集合是由 Edge 的 FilterEvaluator 计算后，通过 `set_stocks` / `add_stocks` / `remove_stocks` 设置的。ConditionNode 本身不持有 FilterEvaluator。

---

#### 3.3.4 简化后的 ConditionNode

```python
class ConditionNode(Node):
    """条件节点
    
    代表一个条件选股的结果，持有满足某个公式条件的股票集合。
    公式计算由入边的 FilterEvaluator 完成，本节点只持有结果。
    """
    
    def __init__(self, node_id: str, name: str, config: dict | None = None):
        super().__init__(node_id, name, config)
        self.node_type = "condition"
        self.formula_id = config.get("formula_id", "")
        self._last_compute_ts = 0.0
        self._compute_count = 0
    
    def record_compute(self, ctx: EngineContext):
        """记录一次计算（由 Edge 在执行后调用）"""
        self._last_compute_ts = ctx.tick.current_data_ts
        self._compute_count += 1
    
    def to_dict(self) -> dict:
        d = super().to_dict()
        d["formula_id"] = self.formula_id
        d["last_compute_ts"] = self._last_compute_ts
        d["compute_count"] = self._compute_count
        return d
```

---

### 3.4 StatePoolNode（状态池节点）

#### 3.4.1 职责

**StatePoolNode（状态池节点）** 代表一个"状态池"，股票进入后会停留一段时间，支持 TTL 淘汰。

**核心职责**：
1. 持有股票集合，并为每只股票记录进入时间
2. 支持 TTL（Time To Live）：股票在池中超过一定时间后自动移除
3. 作为"中间状态"节点，用于累积、缓存股票

**与旧引擎的对应**：
- 旧引擎：状态池 / 持股池 / `_tdx_state_pool`
- 新引擎：`StatePoolNode`

---

#### 3.4.2 属性列表（继承 + 新增）

| 属性 | 类型 | 继承/新增 | 说明 |
|------|------|----------|------|
| `node_id` | `str` | 继承 | 节点唯一标识 |
| `name` | `str` | 继承 | 节点名称 |
| `node_type` | `str` | 覆盖 | "state_pool" |
| `version` | `int` | 继承 | 版本号 |
| `_stocks` | `frozenset[str]` | 继承 | 股票集合 |
| `_dirty` | `bool` | 继承 | 脏标记 |
| `ttl_seconds` | `float | None` | 新增 | TTL 秒数，None 表示不自动淘汰 |
| `_entry_times` | `dict[str, float]` | private | 每只股票的进入时间戳 {code: entry_ts} |
| `_last_cleanup_ts` | `float` | private | 上次清理时间戳 |

---

#### 3.4.3 方法列表（继承 + 新增）

| 方法 | 签名 | 继承/新增 | 说明 |
|------|------|----------|------|
| `__init__` | `(node_id, name, config)` | 覆盖 | 构造函数，解析 ttl |
| `stocks` | `@property` | 继承 | 获取股票集合 |
| `add_stocks` | `(stocks) -> bool` | 覆盖 | 添加股票，同时记录进入时间 |
| `remove_stocks` | `(stocks) -> bool` | 覆盖 | 移除股票，同时清理 entry_times |
| `set_stocks` | `(stocks) -> bool` | 继承 | 设置股票集合 |
| `_apply_stock_changes` | `(added, removed) -> bool` | 继承 | 核心变更方法 |
| `clear_dirty` | `()` | 继承 | 清除脏标记 |
| `cleanup_expired` | `(ctx: EngineContext) -> bool` | 新增 | 清理过期股票（TTL 淘汰） |
| `get_entry_time` | `(code: str) -> float | None` | 新增 | 获取某只股票的进入时间 |
| `get_stay_duration` | `(code: str, now_ts: float) -> float | None` | 新增 | 获取某只股票的停留时长 |
| `reset` | `()` | 覆盖 | 重置（同时清空 entry_times） |
| `to_dict` | `() -> dict` | 覆盖 | 序列化（增加 TTL 信息） |

---

#### 3.4.4 核心方法伪代码

```python
class StatePoolNode(Node):
    """状态池节点
    
    支持 TTL 淘汰的股票池。
    股票进入时记录时间，超过 TTL 后自动移除。
    """
    
    def __init__(self, node_id: str, name: str, config: dict | None = None):
        super().__init__(node_id, name, config)
        self.node_type = "state_pool"
        
        # TTL 配置（秒），None 表示不自动淘汰
        self.ttl_seconds = config.get("ttl_seconds")
        if self.ttl_seconds is not None and self.ttl_seconds <= 0:
            self.ttl_seconds = None  # 无效值视为不启用
        
        self._entry_times: dict[str, float] = {}
        self._last_cleanup_ts = 0.0
    
    def add_stocks(self, stocks: set[str]) -> bool:
        """添加股票，同时记录进入时间"""
        now_ts = time.time()  # 或从 ctx 获取，这里简化
        for code in stocks:
            if code not in self._entry_times:
                self._entry_times[code] = now_ts
        return super().add_stocks(stocks)
    
    def remove_stocks(self, stocks: set[str]) -> bool:
        """移除股票，同时清理 entry_times"""
        for code in stocks:
            self._entry_times.pop(code, None)
        return super().remove_stocks(stocks)
    
    def cleanup_expired(self, ctx: EngineContext) -> bool:
        """清理过期股票（TTL 淘汰）
        
        遍历所有股票，移除停留时间超过 ttl_seconds 的。
        
        Args:
            ctx: 引擎上下文
            
        Returns:
            True 表示有股票被移除，False 表示没有变化
        """
        if self.ttl_seconds is None:
            return False  # 没有启用 TTL
        
        now_ts = ctx.tick.current_data_ts
        
        # 找出过期的股票
        expired = set()
        for code, entry_ts in self._entry_times.items():
            if now_ts - entry_ts > self.ttl_seconds:
                expired.add(code)
        
        if not expired:
            return False
        
        # 移除过期股票
        return self.remove_stocks(expired)
    
    def get_entry_time(self, code: str) -> float | None:
        """获取某只股票的进入时间戳"""
        return self._entry_times.get(code)
    
    def get_stay_duration(self, code: str, now_ts: float) -> float | None:
        """获取某只股票的停留时长（秒）"""
        entry_ts = self._entry_times.get(code)
        if entry_ts is None:
            return None
        return now_ts - entry_ts
    
    def reset(self):
        super().reset()
        self._entry_times.clear()
        self._last_cleanup_ts = 0.0
    
    def to_dict(self) -> dict:
        d = super().to_dict()
        d["ttl_seconds"] = self.ttl_seconds
        d["avg_stay_duration"] = self._avg_stay_duration()
        return d
    
    def _avg_stay_duration(self) -> float:
        """计算平均停留时长（调试用）"""
        if not self._entry_times:
            return 0.0
        now = time.time()
        durations = [now - ts for ts in self._entry_times.values()]
        return sum(durations) / len(durations)
```

---

#### 3.4.5 TTL 淘汰触发时机

| 触发时机 | 说明 |
|---------|------|
| 每次 tick 开始 | 先清理过期，再处理新变化 |
| 手动调用 | 外部 API 主动触发清理 |
| 定时器 | 定时清理（如果 tick 间隔太长） |

**注意**：TTL 清理也会导致 node.version 递增（因为股票集合变化了）。

---

### 3.5 TargetPoolNode（目标池节点）

#### 3.5.1 职责

**TargetPoolNode（目标池节点）** 是股票池的"最终输出"，代表选股结果。

**核心职责**：
1. 持有最终的选股结果股票集合
2. 产生信号（买入/卖出信号）
3. 触发告警和外部事件
4. 作为拓扑图的"终点"，没有出边（或出边是告警等）

**与旧引擎的对应**：
- 旧引擎：目标池 / 输出池 / `_tdx_target_pool`
- 新引擎：`TargetPoolNode`

---

#### 3.5.2 属性列表（继承 + 新增）

| 属性 | 类型 | 继承/新增 | 说明 |
|------|------|----------|------|
| `node_id` | `str` | 继承 | 节点唯一标识 |
| `name` | `str` | 继承 | 节点名称 |
| `node_type` | `str` | 覆盖 | "target_pool" |
| `version` | `int` | 继承 | 版本号 |
| `_stocks` | `frozenset[str]` | 继承 | 股票集合 |
| `_dirty` | `bool` | 继承 | 脏标记 |
| `signal_type` | `str` | 新增 | 信号类型（"buy"/"sell"/"alert"等） |
| `_signals` | `list[Signal]` | private | 历史信号列表 |
| `_last_signal_ts` | `float` | private | 上次信号时间戳 |

---

#### 3.5.3 方法列表（继承 + 新增）

| 方法 | 签名 | 继承/新增 | 说明 |
|------|------|----------|------|
| `__init__` | `(node_id, name, config)` | 覆盖 | 构造函数 |
| `stocks` | `@property` | 继承 | 获取股票集合 |
| `add_stocks` | `(stocks) -> bool` | 覆盖 | 添加股票，同时产生买入信号 |
| `remove_stocks` | `(stocks) -> bool` | 覆盖 | 移除股票，同时产生卖出信号 |
| `set_stocks` | `(stocks) -> bool` | 继承 | 设置股票集合 |
| `_apply_stock_changes` | `(added, removed) -> bool` | 继承 | 核心变更方法 |
| `clear_dirty` | `()` | 继承 | 清除脏标记 |
| `generate_signals` | `(added: set, removed: set, ctx: EngineContext) -> list` | 新增 | 根据变化生成信号 |
| `get_recent_signals` | `(limit: int) -> list` | 新增 | 获取最近的信号 |
| `reset` | `()` | 覆盖 | 重置（清空信号历史） |
| `to_dict` | `() -> dict` | 覆盖 | 序列化（增加信号统计） |

---

#### 3.5.4 核心方法伪代码

```python
class TargetPoolNode(Node):
    """目标池节点
    
    股票池的最终输出节点，持有选股结果。
    股票变化时产生信号，并通过 EventBus 发布外部事件。
    """
    
    def __init__(self, node_id: str, name: str, config: dict | None = None):
        super().__init__(node_id, name, config)
        self.node_type = "target_pool"
        self.signal_type = config.get("signal_type", "buy")
        self._signals: list[dict] = []
        self._last_signal_ts = 0.0
        self._max_signal_history = config.get("max_signal_history", 1000)
    
    def add_stocks(self, stocks: set[str]) -> bool:
        """添加股票（由 Edge 调用）
        
        注意：信号生成不在 add_stocks 里做，
        而是在事件处理器中统一处理，这样更灵活。
        """
        return super().add_stocks(stocks)
    
    def remove_stocks(self, stocks: set[str]) -> bool:
        """移除股票（由 Edge 调用）"""
        return super().remove_stocks(stocks)
    
    def generate_signals(
        self,
        added: set[str],
        removed: set[str],
        ctx: EngineContext
    ) -> list[dict]:
        """根据股票变化生成信号
        
        在节点变化事件处理器中调用，生成信号并发布外部事件。
        
        Args:
            added: 新增的股票
            removed: 移除的股票
            ctx: 引擎上下文
            
        Returns:
            生成的信号列表
        """
        signals = []
        now_ts = ctx.tick.current_data_ts
        
        for code in added:
            signal = {
                "signal_type": self.signal_type,
                "action": "enter",  # 进入
                "code": code,
                "timestamp": now_ts,
                "node_id": self.node_id,
                "version": self.version,
            }
            signals.append(signal)
        
        for code in removed:
            signal = {
                "signal_type": self.signal_type,
                "action": "exit",  # 退出
                "code": code,
                "timestamp": now_ts,
                "node_id": self.node_id,
                "version": self.version,
            }
            signals.append(signal)
        
        if signals:
            # 记录历史
            self._signals.extend(signals)
            # 限制历史长度
            if len(self._signals) > self._max_signal_history:
                self._signals = self._signals[-self._max_signal_history:]
            self._last_signal_ts = now_ts
            
            # 通过 EventBus 发布外部事件
            event_bus = ctx.services.event_bus
            event_bus.publish("target_pool_signals", {
                "node_id": self.node_id,
                "signals": signals,
                "timestamp": now_ts,
            })
        
        return signals
    
    def get_recent_signals(self, limit: int = 10) -> list[dict]:
        """获取最近的信号"""
        return self._signals[-limit:]
    
    def reset(self):
        super().reset()
        self._signals.clear()
        self._last_signal_ts = 0.0
    
    def to_dict(self) -> dict:
        d = super().to_dict()
        d["signal_type"] = self.signal_type
        d["signal_count"] = len(self._signals)
        d["last_signal_ts"] = self._last_signal_ts
        return d
```

---

### 3.6 继承层次总结

```
Node（基类）
  ├── SourceNode      数据源节点（拓扑源头）
  ├── ConditionNode   条件节点（公式计算结果）
  ├── StatePoolNode   状态池节点（带 TTL）
  └── TargetPoolNode  目标池节点（拓扑终点，产信号）
```

| 节点类型 | 入边 | 出边 | 股票来源 | 主要操作 |
|---------|------|------|---------|---------|
| SourceNode | ❌ 无 | ✅ 有 | DataQuery | update_stocks |
| ConditionNode | ✅ 有 | ✅ 有 | Edge 的 FilterEvaluator | set_stocks（被 Edge 调用） |
| StatePoolNode | ✅ 有 | ✅ 有 | 上游边传播 + TTL | add_stocks / remove_stocks / cleanup_expired |
| TargetPoolNode | ✅ 有 | ❌ 无 | 上游边传播 | add_stocks / remove_stocks / generate_signals |

---

### 3.7 与旧引擎节点类型的对应

| 旧引擎概念 | 新引擎对应 | 说明 |
|-----------|-----------|------|
| 初始股票池 | SourceNode | 数据源入口 |
| 条件选股节点 | ConditionNode + FilterEvaluator | 公式计算结果 |
| 状态池/持股池 | StatePoolNode | 带 TTL 的中间池 |
| 目标池/输出池 | TargetPoolNode | 最终结果，产信号 |
| 板块池 | SourceNode(source_type="sector") | 源节点的一种配置 |
| 自选股池 | SourceNode(source_type="custom") | 源节点的一种配置 |

---
