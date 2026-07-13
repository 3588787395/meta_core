# v1.4 核心类详细设计 + 生命周期修复

## 0. 设计原则与约束

**现状**：v1.3 设计评审 78.5 分，有 3 个 P0 问题需修复，3 个核心类缺失详细设计。

**v1.4 目标**：修复三大 P0 问题，补全三大核心类（EventLoop / TimingGate / FilterEvaluator）的详细设计，明确并发模型，达到可开工标准。

**设计原则（继承 v1.3）**：
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

**v1.4 新增原则**：
- **先验证后消费**：任何资源消费前，必须先验证其有效性，验证失败则不消费
- **单线程事件循环**：事件循环本身是单线程的，所有状态变更都在事件循环线程中完成
- **查表驱动优先**：复杂的多分支逻辑优先用查表（表驱动）实现，减少 if/elif 嵌套

---

## 1. 核心类图（v1.4 更新：补全三大核心类）

### 1.1 服务与上下文拆分（v1.4 修正：FormulaCache 移到 TickContext）

#### 拆分背景

v1.3 已将 EngineContext 拆分为 EngineServices + TickContext，但 FormulaCache 生命周期错误地放在了引擎级的 FormulaRouter 中。

**v1.4 修正**：FormulaCache 移到 TickContext 中，与 tick 级生命周期匹配。

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
  └── formula_cache: FormulaCache   ← v1.4 新增，从 FormulaRouter 移来

EngineContext（组合对象，方便传递）
  ├── services: EngineServices
  └── tick: TickContext
```

**为什么 FormulaCache 放在 TickContext 而不是 FormulaRouter？**
- FormulaCache 是 tick 级的（数据版本变了就失效）
- TickContext 也是 tick 级的，生命周期匹配
- 每个 tick 创建新的 TickContext，缓存自然就是新的，不需要手动 clear
- 符合"生命周期匹配"原则

---

### 1.2 EngineServices 详细设计（v1.4 不变，移除 formula_cache）

**职责**：封装引擎级的稳定服务依赖，生命周期与引擎相同。

**属性**：
| 属性 | 类型 | 说明 |
|------|------|------|
| `event_queue` | EventQueue | 事件队列（优先级队列 + 去重合并） |
| `event_bus` | EventBus | 事件总线（发布订阅，用于外部系统） |
| `formula_router` | FormulaRouter | 公式路由器（无状态，只负责路由计算请求） |
| `data_query` | DataQuery | 数据查询服务 |
| `config_store` | ConfigStore | 配置存储 |
| `metrics` | EngineMetrics | 性能指标统计 |
| `topology` | Topology | 拓扑结构（节点、边、连接关系） |

**说明**：
- 所有属性在引擎启动时初始化，运行期间不变（引用不变，内部状态可变）
- 这些服务是无状态的或状态是全局共享的
- 可以在多个 tick 之间安全复用
- **v1.4 修正**：FormulaRouter 不再持有 FormulaCache，FormulaCache 移到 TickContext

---

### 1.3 TickContext 详细设计（v1.4 增强：补全 data_version + formula_cache）

**职责**：封装单次 tick 执行的状态，每个 tick 开始时创建，tick 结束后丢弃。

**属性**：
| 属性 | 类型 | 说明 |
|------|------|------|
| `tick_id` | int | Tick 序号（单调递增） |
| `tick_start_time` | float | Tick 开始时间戳（性能统计用） |
| `current_data_ts` | float | 当前数据时间戳 |
| `data_version` | int | 数据版本号（每次数据更新 +1）【v1.4 补全】 |
| `bar_time` | datetime | 当前 K 线时间 |
| `bar_data` | dict | 当前行情数据（全量） |
| `data_dirty` | bool | 本 tick 是否有新数据到达 |
| `events_fired` | int | 本 tick 已处理事件数 |
| `max_iterations` | int | 本 tick 最大迭代次数（防死循环） |
| `formula_cache` | FormulaCache | 公式结果缓存（tick 级）【v1.4 新增】 |

**说明**：
- 每个 tick 开始时创建新的 TickContext
- tick 内所有状态变化都在这个对象上
- tick 结束后，这个对象可以被丢弃或重置
- 天然线程安全（每个 tick 一个实例）
- **v1.4 新增**：`formula_cache` 和 `data_version` 字段

---

### 1.4 EngineContext（组合对象）（v1.4 不变）

**职责**：方便同时传递服务和 tick 状态。

**属性**：
| 属性 | 类型 | 说明 |
|------|------|------|
| `services` | EngineServices | 引擎级服务 |
| `tick` | TickContext | 当前 tick 状态 |

**便捷属性**：
```python
@property
def event_queue(self):
    return self.services.event_queue

@property
def formula_router(self):
    return self.services.formula_router

@property
def formula_cache(self):
    return self.tick.formula_cache

@property
def now_ts(self):
    return self.tick.current_data_ts

@property
def data_dirty(self):
    return self.tick.data_dirty

@property
def data_version(self):
    return self.tick.data_version
```

---

### 1.5 三大核心类关系图（v1.4 新增）

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
          │     │Source  │ │StatePool   │    ┌─────┴──────┐
          │     │Node    │ │Node        │    │   Edge     │
          │     └────────┘ └────────────┘    │  (基类)     │
          │                                   └─────┬──────┘
          │                                         │ 组合
          │                            ┌────────────┼────────────┐
          │                            ▼            ▼            ▼
          │                      ┌──────────┐ ┌──────────┐ ┌──────────┐
          │                      │TimingGate│ │FilterEval│ │Uncond-   │
          │                      │ (时机判断)│ │   uator  │ │itional   │
          │                      └──────────┘ │ (过滤评估)│ └──────────┘
          │                                   └─────┬────┘
          │                                         │ 调用
          │                                         ▼
          │                                   ┌──────────┐
          │                                   │Formula   │
          │                                   │  Router  │
          │                                   └──────────┘
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

## 2. Pending 版本号验证顺序修复（v1.4 P0-1）

### 2.1 问题回顾

**v1.3 的 bug**：先清空 pending 再验证版本号，如果版本号对不上（墓碑），变更集已经丢了。

```python
# v1.3 的错误顺序：
info.added.clear()          # 1. 先清空
info.removed.clear()
info.has_pending_event = False

# ... 获取节点 ...

if version != node.version: # 2. 后验证
    logger.warning(...)
    return  # ← 直接返回，本次变化被丢弃！
```

**触发场景**：
1. 数据推送在另一个线程，on_node_stock_changed 执行过程中，数据线程推送了新数据
2. 重入调用：处理下游边时，某种回调导致源节点再次变化

**后果**：本次事件的 added/removed 已经从 pending 中清空了，但因为版本号验证失败，业务逻辑没执行，变化永久丢失。

---

### 2.2 修复方案：先验证，后消费

#### 核心思想

1. **先验证版本号**：确认事件对应的版本号与节点当前版本号一致
2. **验证通过后再消费**：拷贝变更集，清空 pending
3. **验证失败则跳过**：pending 不清空，让更新的事件去处理

#### 正确的伪代码

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
    # 第一步：先验证版本号（关键！v1.4 修复）
    # ==========================================
    node = context.services.topology.get_node(node_id)
    
    if event.version != node.version:
        # 版本不一致，说明有更新的事件在队列中
        # 本次事件是墓碑，跳过，让更新的事件处理
        # pending 不要清空！
        logger.debug(f"版本不一致，跳过：event={event.version}, node={node.version}")
        return
    
    # ==========================================
    # 第二步：验证通过后，再消费 pending 中的变更
    # ==========================================
    added = info.added.copy()
    removed = info.removed.copy()
    version = info.version
    
    # 清空，为下一轮累计做准备
    info.added.clear()
    info.removed.clear()
    info.has_pending_event = False
    
    # ==========================================
    # 第三步：执行业务逻辑
    # ==========================================
    
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

---

### 2.3 并发场景下的正确性论证

#### 并发模型前提（详见 §7）

事件循环是**单线程**的，所有状态变更（包括 node.version 更新和事件处理）都在事件循环线程中完成。

数据推送线程只做两件事：
1. 更新数据缓存
2. 往事件队列里放事件（enqueue）

因此，`on_node_stock_changed` 执行时，`node.version` 不会被其他线程修改。

#### 为什么还需要版本号验证？

虽然事件循环是单线程的，但版本号验证仍然有意义：

1. **墓碑机制**：dequeue 时已经通过 `event.version < info.version` 跳过了墓碑事件，但消费前再验证一次是双重保险
2. **防御性编程**：防止未来引入并发时出问题
3. **一致性检查**：确保 pending 的版本号和节点的版本号一致

#### 正确性论证

**定理**：在单线程事件循环模型下，"先验证后消费"的方案不会丢数据。

**证明**：

考虑以下不变式：
> 对于任意节点 N，如果 N 的当前版本号是 V，那么：
> - 如果 pending 中有事件，pending.version >= V（因为 enqueue 时 version 是单调递增的）
> - 队列中存在一个版本号为 pending.version 的事件（最新的那个）

当 `on_node_stock_changed` 被调用时：
1. `event.version == info.version`（dequeue 保证了这一点，否则会被跳过）
2. 验证 `event.version == node.version`
3. 如果相等 → 消费 pending，执行业务逻辑，正确 ✓
4. 如果不等 → 不消费 pending，返回，pending 中的变更集保留，等待更新的事件处理 ✓

**不会丢数据的原因**：
- 验证失败时，pending 不清空
- 队列中一定有一个更新版本的事件（因为 pending.version > node.version）
- 那个更新的事件出队时，版本号会匹配，然后正确消费

---

### 2.4 边界场景验证

#### 场景 1：正常消费

```
初始：node.version=2, pending.version=2, pending.added={1,2}

1. 事件出队（v2）
2. 验证：event.version(2) == node.version(2) ✓
3. 消费：added={1,2}, 清空 pending
4. 执行业务逻辑...

最终：node.version=2, pending=EMPTY ✓
```

#### 场景 2：墓碑事件（验证失败，pending 保留）

```
初始：node.version=3, pending.version=3, pending.added={1,2,3}
      队列中有事件1（v2，墓碑）和事件2（v3，有效）

1. 事件1出队（v2）
   → dequeue 检查：event.version(2) < info.version(3) → 墓碑，跳过
   
2. 事件2出队（v3）
   → dequeue 检查：event.version(3) == info.version(3) → 有效
   → 验证：event.version(3) == node.version(3) ✓
   → 消费：added={1,2,3}, 清空 pending
   → 执行业务逻辑...

最终：node.version=3, pending=EMPTY ✓
```

#### 场景 3：极端情况 — 节点版本比事件新

```
假设（理论上不应该发生，但防御性编程）：
node.version=4, event.version=3, pending.version=4

1. 事件出队（v3）
   → dequeue 检查：event.version(3) < info.version(4) → 墓碑，跳过

2. 后续事件出队（v4）
   → dequeue 检查：event.version(4) == info.version(4) → 有效
   → 验证：event.version(4) == node.version(4) ✓
   → 消费...

最终：正确 ✓
```

---

### 2.5 单元测试用例补充

在 v1.3 的测试用例基础上，新增以下测试：

| 测试用例 | 输入 | 预期输出 | 验证点 |
|---------|------|---------|--------|
| 先验证后消费 | 事件版本 = 节点版本 | pending 被清空，业务逻辑执行 | 正常流程 |
| 验证失败不清空 | 事件版本 < 节点版本 | pending 保留，业务逻辑不执行 | 墓碑场景 |
| 验证失败后更新事件到达 | 旧事件验证失败，新事件到达 | 新事件能正确消费 pending | 不丢数据 |
| dequeue 已跳过墓碑 | 多个同节点事件 | 只有最新版本的事件触发消费 | 双重验证一致性 |
| 并发模拟（单线程） | 处理中 enqueue 新变化 | 新变化累计到下一轮 | 消费即清空 + 累计 |

---

## 3. FormulaCache 生命周期修复（v1.4 P0-2）

### 3.1 问题回顾

**v1.3 的矛盾**：
- FormulaCache 放在 FormulaRouter 里（引擎级生命周期）
- 但 FormulaCache 是 tick 级的（数据版本变了就失效）
- 违反"生命周期匹配"原则

**具体矛盾点**：
1. EngineServices 是引擎级的，formula_router 是 EngineServices 的属性
2. FormulaCache 是 formula_router 的属性（通过 formula_router.cache 访问）
3. 但 FormulaCache 需要在 tick 结束时清空（tick 级生命周期）
4. 引擎级对象持有 tick 级状态 → 生命周期不匹配

---

### 3.2 三种方案对比

#### 方案 A：FormulaCache 移到 TickContext 里

**设计**：
- FormulaCache 作为 TickContext 的属性
- 每个 tick 创建新的 TickContext，缓存自然就是新的
- 不需要手动 clear

**优点**：
1. 生命周期完美匹配（都是 tick 级）
2. 实现简单，不需要额外管理
3. 职责清晰：TickContext 就是存 tick 级状态的
4. 天然线程安全（每个 tick 一个实例）

**缺点**：
1. FilterEvaluator 需要通过 context.tick.formula_cache 访问，路径稍长
2. 跨 tick 的缓存无法实现（但本来也不需要）

---

#### 方案 B：FormulaCache 留在 FormulaRouter，用 data_version 做 key

**设计**：
- FormulaCache 仍然在 FormulaRouter 里（引擎级）
- 缓存键包含 data_version，版本变了自动失效
- 不需要手动 clear，旧版本的缓存自然不会被命中

**优点**：
1. 接口简单：formula_router.cache.get(...)
2. 可以跨 tick 缓存相同 data_version 的结果（虽然 data_version 通常每个 tick 都变）
3. 实现简单

**缺点**：
1. 生命周期不匹配：引擎级对象持有 tick 级状态
2. 内存泄漏风险：旧版本的缓存不会自动清理，需要 LRU 或定期清理
3. 测试时构造 FormulaRouter 也得考虑缓存状态
4. 违反"生命周期匹配"原则

---

#### 方案 C：两级缓存 — tick 级 + 跨 tick 级

**设计**：
- 第一级（L1）：TickContext 里的 FormulaCache，tick 级，每个 tick 新建
- 第二级（L2）：FormulaRouter 里的 FormulaCache，用 data_version 做 key，跨 tick 复用
- 查找时先查 L1，再查 L2，都没有才计算
- 计算结果同时写入 L1 和 L2

**优点**：
1. 兼顾了性能和生命周期匹配
2. L1 是 tick 级的，符合生命周期匹配原则
3. L2 可以跨 tick 复用相同 data_version 的结果

**缺点**：
1. 复杂度高：两级缓存的一致性、失效策略都要考虑
2. 过度设计：data_version 每个 tick 都变，L2 命中率可能很低
3. 维护成本高：多了一层缓存，bug 风险增加

---

### 3.3 方案选择：方案 A（FormulaCache 移到 TickContext）

**选择理由**：
1. **生命周期匹配**：TickContext 就是 tick 级的，FormulaCache 也是 tick 级的，完美匹配
2. **简单可靠**：每个 tick 创建新的 TickContext，缓存自然就是新的，不需要手动 clear
3. **职责清晰**：tick 级的状态就应该放在 TickContext 里
4. **避免内存泄漏**：tick 结束后 TickContext 被丢弃，缓存自动回收
5. **符合现有代码风格**：v1.3 已经把 tick 级状态都放到 TickContext 了

**为什么不选方案 B？**
- 生命周期不匹配，违反设计原则
- 需要额外的清理机制（LRU、定期清理等）
- 内存泄漏风险

**为什么不选方案 C？**
- 过度设计，复杂度高
- data_version 每个 tick 都变，L2 命中率低
- 性价比低

---

### 3.4 缓存键设计

**缓存键组成**：
```
cache_key = (formula_id, input_codes_frozenset)
```

| 组成部分 | 说明 |
|---------|------|
| `formula_id` | 公式唯一标识（公式名称 + 参数哈希） |
| `input_codes_frozenset` | 输入股票代码集合（frozenset，可哈希） |

**为什么不用 data_version 了？**
- 因为 FormulaCache 已经是 tick 级的了，data_version 在同一个 tick 内是不变的
- 不需要用 data_version 做 key，缓存自然只在当前 tick 内有效

---

### 3.5 失效策略与内存控制

**失效时机**：
1. **tick 结束**：TickContext 被丢弃，FormulaCache 随之被 GC 回收
2. **不需要手动 clear**：因为每个 tick 都是新的 FormulaCache 实例

**内存占用控制**：
1. **自动回收**：tick 结束后自动回收，不会累积
2. **数量有限**：同一 tick 内的公式调用次数有限（通常几十到几百次）
3. **股票池公式数量有限**：通常几十个公式
4. **不需要 LRU**：因为 tick 内数量有限，且结束就清理

---

### 3.6 FormulaCache 接口定义

```python
class FormulaCache:
    """公式结果缓存（tick 级）
    
    生命周期：与 TickContext 相同，每个 tick 创建新实例
    失效策略：tick 结束后，TickContext 被丢弃，缓存自动回收
    """
    
    def __init__(self):
        self._cache: dict[tuple, any] = {}
        self.hit_count = 0
        self.miss_count = 0
    
    def get(self, formula_id: str, codes: frozenset) -> any | None:
        """获取缓存结果，不存在返回 None"""
        key = (formula_id, codes)
        result = self._cache.get(key)
        if result is not None:
            self.hit_count += 1
        else:
            self.miss_count += 1
        return result
    
    def set(self, formula_id: str, codes: frozenset, result: any):
        """设置缓存结果"""
        key = (formula_id, codes)
        self._cache[key] = result
    
    def clear(self):
        """清空缓存（一般不需要手动调用，tick 结束自动回收）"""
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
    
    def __len__(self) -> int:
        """缓存条目数"""
        return len(self._cache)
```

---

### 3.7 在 FilterEvaluator 中的使用

```python
class FilterEvaluator:
    def evaluate(self, input_stocks: frozenset, context: EngineContext) -> frozenset:
        """执行过滤计算"""
        # 尝试从缓存获取（从 TickContext 中取，v1.4 修正）
        cache = context.tick.formula_cache
        formula_id = self._formula_id
        
        cached = cache.get(formula_id, input_stocks)
        if cached is not None:
            return cached
        
        # 未命中，实际计算
        result = self._do_evaluate(input_stocks, context)
        
        # 写入缓存
        cache.set(formula_id, input_stocks, result)
        
        return result
```

---

### 3.8 TickContext 的创建与销毁

**创建时机**：每个 tick 开始时

```python
# EventDrivenEngine 中
def _start_new_tick(self, now_ts: float):
    """开始一个新的 tick"""
    self._tick_id += 1
    self.context.tick = TickContext(
        tick_id=self._tick_id,
        tick_start_time=time.time(),
        current_data_ts=now_ts,
        data_version=self._data_version,
        bar_time=datetime.fromtimestamp(now_ts),
        bar_data=self._get_latest_bar_data(),
        data_dirty=True,
        events_fired=0,
        max_iterations=self._max_iterations_per_tick,
        formula_cache=FormulaCache()  # 每个 tick 新建
    )
```

**销毁时机**：tick 结束后，TickContext 被新的 TickContext 替换，旧的被 GC 回收。

**是"创建新的"还是"复用并重置"？**

v1.4 选择 **"每个 tick 创建新的 TickContext"**，理由：
1. 更安全：不会有状态残留
2. 更清晰：生命周期明确，创建即开始，丢弃即结束
3. 成本低：TickContext 是轻量级对象，创建成本可忽略

---

## 4. EventLoop 详细设计（v1.4 P0-3）

### 4.1 类概述

**类名**：EventLoop（事件循环）

**职责**：事件循环调度器，从事件队列取事件、分发到对应处理器、执行处理逻辑。

**设计原则**：
- 单线程运行，所有事件处理都在事件循环线程中
- 事件驱动：有事件才处理，没事件就等待
- 最大迭代数保护：防止死循环
- 异常隔离：单个事件处理失败不影响整个循环

---

### 4.2 属性列表

| 属性 | 类型 | 说明 |
|------|------|------|
| `event_queue` | EventQueue | 事件队列（优先级队列） |
| `_handlers` | dict[str, callable] | 事件处理器映射 {event_type: handler_func} |
| `_running` | bool | 循环是否在运行 |
| `_stop_requested` | bool | 是否请求停止 |
| `_max_iterations` | int | 单次 tick 最大迭代次数（防死循环） |
| `_iteration_count` | int | 当前 tick 已迭代次数 |
| `_context` | EngineContext | 引擎上下文 |
| `_error_count` | int | 错误计数（用于熔断） |
| `_max_errors` | int | 最大连续错误数（熔断阈值） |

---

### 4.3 方法列表

| 方法 | 说明 |
|------|------|
| `__init__(event_queue, context, max_iterations=1000)` | 构造函数 |
| `register_handler(event_type, handler)` | 注册事件处理器 |
| `unregister_handler(event_type)` | 注销事件处理器 |
| `run_tick()` | 执行一个 tick 的事件循环 |
| `process_event(event)` | 处理单个事件 |
| `dispatch(event)` | 事件分发（根据事件类型找处理器） |
| `stop()` | 请求停止循环 |
| `is_running()` | 检查是否在运行 |

---

### 4.4 主循环伪代码（run_tick）

```python
def run_tick(self) -> int:
    """执行一个 tick 的事件循环
    
    处理队列中的所有事件，直到队列为空或达到最大迭代数。
    
    Returns:
        本 tick 处理的事件数
    """
    self._running = True
    self._stop_requested = False
    self._iteration_count = 0
    self._error_count = 0
    events_processed = 0
    
    try:
        while not self._stop_requested:
            # 检查最大迭代数（防死循环）
            if self._iteration_count >= self._max_iterations:
                logger.warning(
                    f"达到最大迭代数 {self._max_iterations}，停止循环"
                )
                break
            
            # 从队列取事件
            event = self.event_queue.dequeue()
            if event is None:
                # 队列为空，tick 结束
                break
            
            # 处理事件
            try:
                self.process_event(event)
                events_processed += 1
                self._error_count = 0  # 重置错误计数
            except Exception as e:
                # 异常隔离：单个事件失败不影响整个循环
                self._error_count += 1
                logger.error(
                    f"处理事件失败: {event.event_type}, "
                    f"错误: {e}, "
                    f"错误计数: {self._error_count}",
                    exc_info=True
                )
                
                # 熔断：连续错误太多，停止循环
                if self._error_count >= self._max_errors:
                    logger.error(
                        f"连续错误 {self._error_count} 次，触发熔断，停止循环"
                    )
                    break
            
            self._iteration_count += 1
            
            # 更新 tick 上下文中的事件计数
            if hasattr(self._context, 'tick'):
                self._context.tick.events_fired = events_processed
    
    finally:
        self._running = False
    
    return events_processed
```

---

### 4.5 事件分发（dispatch）

```python
def dispatch(self, event: Event) -> callable | None:
    """根据事件类型找到对应的处理器
    
    Args:
        event: 事件对象
    
    Returns:
        处理器函数，如果没有注册则返回 None
    """
    event_type = event.event_type
    handler = self._handlers.get(event_type)
    
    if handler is None:
        # 尝试找基类的处理器（支持继承）
        for base in type(event).__mro__:
            if hasattr(base, 'event_type'):
                handler = self._handlers.get(base.event_type)
                if handler is not None:
                    break
    
    return handler
```

---

### 4.6 事件处理（process_event）

```python
def process_event(self, event: Event):
    """处理单个事件
    
    Args:
        event: 事件对象
    
    Raises:
        ValueError: 如果没有注册对应的处理器
    """
    handler = self.dispatch(event)
    
    if handler is None:
        logger.warning(f"未注册事件处理器: {event.event_type}")
        return
    
    # 执行处理器
    handler(event, self._context)
```

---

### 4.7 事件处理器注册

```python
def register_handler(self, event_type: str, handler: callable):
    """注册事件处理器
    
    Args:
        event_type: 事件类型字符串
        handler: 处理器函数，签名为 handler(event, context)
    """
    if event_type in self._handlers:
        logger.warning(f"覆盖已有的事件处理器: {event_type}")
    self._handlers[event_type] = handler

def unregister_handler(self, event_type: str):
    """注销事件处理器"""
    if event_type in self._handlers:
        del self._handlers[event_type]
```

---

### 4.8 停止机制

```python
def stop(self):
    """请求停止事件循环
    
    注意：这是"优雅停止"，会处理完当前正在处理的事件，然后退出循环。
    """
    self._stop_requested = True

def is_running(self) -> bool:
    """检查事件循环是否在运行"""
    return self._running
```

---

### 4.9 异常处理策略

| 异常场景 | 处理方式 | 说明 |
|---------|---------|------|
| 单个事件处理失败 | 捕获异常，记录日志，继续处理下一个 | 异常隔离，不影响整体 |
| 连续多个事件失败 | 熔断，停止循环 | 防止错误雪崩 |
| 队列为空 | 正常退出循环 | tick 结束 |
| 达到最大迭代数 | 停止循环，记录警告 | 防死循环 |
| 未注册事件类型 | 记录警告，跳过 | 容错处理 |

---

### 4.10 最大迭代数保护

**为什么需要最大迭代数？**
- 防止级联传播陷入死循环（比如 A→B→A 的环）
- 虽然拓扑是 DAG 的，但 bug 可能导致环
- 防御性编程

**默认值**：1000 次

**怎么确定的？**
- 股票池的节点数通常 < 100
- 边数通常 < 200
- 级联深度通常 < 10 层
- 1000 次远大于正常情况，既防死循环又不会误触发

---

### 4.11 与 EventQueue 的关系

```
EventLoop (调度者)
    │
    │ 1. dequeue() ← 取事件
    ▼
EventQueue (存储者)
    │
    │ 2. enqueue() → 事件入队
    │    （由事件处理器在处理过程中调用）
    ▼
EventLoop
```

**职责划分**：
- EventQueue：负责事件的存储、优先级排序、去重、合并
- EventLoop：负责事件的调度、分发、处理

**EventLoop 不直接操作队列的内部结构**，只通过公开接口（enqueue/dequeue）交互。

---

### 4.12 典型事件处理器注册示例

```python
# 在 EventDrivenEngine 中注册处理器
def _setup_event_handlers(self):
    loop = self._event_loop
    
    # 节点变化事件
    loop.register_handler(
        'node_stock_changed',
        self._on_node_stock_changed
    )
    
    # 边执行事件
    loop.register_handler(
        'edge_execute',
        self._on_edge_execute
    )
    
    # 数据更新事件
    loop.register_handler(
        'data_updated',
        self._on_data_updated
    )
    
    # 定时器事件
    loop.register_handler(
        'timer_tick',
        self._on_timer_tick
    )
    
    # 系统事件
    loop.register_handler(
        'pool_start',
        self._on_pool_start
    )
    loop.register_handler(
        'pool_stop',
        self._on_pool_stop
    )
```

---

## 5. TimingGate 详细设计（v1.4 P0-4）

### 5.1 类概述

**类名**：TimingGate（时机门控）

**职责**：判断一条边该不该触发（时机判断），基于 timing.json 中的配置。

**设计依据**：timing.json 中的 starttype（8种）× cxtype（3种）= 24种组合

**设计原则**：查表驱动，减少 if/elif 嵌套

---

### 5.2 时机类型说明

#### starttype（8种触发时机）

| starttype | 名称 | 说明 |
|-----------|------|------|
| 0 | 即时触发 | 数据一更新就触发 |
| 1 | 周期触发 | 每隔一段时间触发一次 |
| 2 | 时间点触发 | 在指定时间点触发 |
| 3 | 持续触发 | 条件满足持续一段时间后触发 |
| 4 | 次数触发 | 条件满足 N 次后触发 |
| 5 | 开盘触发 | 开盘时触发一次 |
| 6 | 收盘触发 | 收盘时触发一次 |
| 7 | 定时触发 | 每天指定时间触发 |

#### cxtype（3种持续类型）

| cxtype | 名称 | 说明 |
|--------|------|------|
| 0 | 脉冲型 | 触发一次就完，下次需要重新满足条件 |
| 1 | 持续型 | 条件满足期间持续触发 |
| 2 | 状态型 | 条件状态变化时触发（进入/退出） |

---

### 5.3 属性列表

| 属性 | 类型 | 说明 |
|------|------|------|
| `edge_id` | str | 所属边的 ID |
| `config` | dict | 时机配置（从 timing.json 解析） |
| `starttype` | int | 触发时机类型（0-7） |
| `cxtype` | int | 持续类型（0-2） |
| `_is_open` | bool | 当前门是否打开（可触发） |
| `_prev_is_open` | bool | 上一状态（用于检测状态变化） |
| `_duration_start_ts` | float | 持续时长开始时间戳（starttype=3 用） |
| `_trigger_count` | int | 已触发次数（starttype=4 用） |
| `_last_trigger_ts` | float | 上次触发时间戳（starttype=1 周期触发用） |
| `_triggered_today` | bool | 今天是否已触发（starttype=5/6/7 用） |
| `_last_date` | str | 上次日期（用于判断是否新的一天） |

---

### 5.4 方法列表

| 方法 | 说明 |
|------|------|
| `__init__(edge_id, config)` | 构造函数 |
| `should_trigger(ctx) -> bool` | 判断当前是否应该触发 |
| `record_trigger(ctx)` | 记录一次触发（更新内部状态） |
| `is_open(ctx) -> bool` | 门是否打开（条件是否满足） |
| `reset()` | 重置状态 |
| `_check_duration(ctx) -> bool` | 检查持续时长（starttype=3） |
| `_check_count(ctx) -> bool` | 检查触发次数（starttype=4） |
| `_check_periodic(ctx) -> bool` | 检查周期触发（starttype=1） |
| `_check_scheduled(ctx) -> bool` | 检查定时触发（starttype=2/7） |
| `_check_market_time(ctx) -> bool` | 检查开盘/收盘触发（starttype=5/6） |

---

### 5.5 24 种组合的查表驱动设计

#### 查表驱动表

```python
# 24 种组合的处理逻辑表
# 键：(starttype, cxtype)
# 值：处理函数或配置
_TIMING_RULES = {
    # starttype=0: 即时触发
    (0, 0): {"check": "immediate", "trigger": "pulse"},       # 即时 + 脉冲
    (0, 1): {"check": "immediate", "trigger": "continuous"},  # 即时 + 持续
    (0, 2): {"check": "immediate", "trigger": "state"},       # 即时 + 状态
    
    # starttype=1: 周期触发
    (1, 0): {"check": "periodic", "trigger": "pulse"},        # 周期 + 脉冲
    (1, 1): {"check": "periodic", "trigger": "continuous"},   # 周期 + 持续
    (1, 2): {"check": "periodic", "trigger": "state"},        # 周期 + 状态
    
    # starttype=2: 时间点触发
    (2, 0): {"check": "timepoint", "trigger": "pulse"},       # 时间点 + 脉冲
    (2, 1): {"check": "timepoint", "trigger": "continuous"},  # 时间点 + 持续
    (2, 2): {"check": "timepoint", "trigger": "state"},       # 时间点 + 状态
    
    # starttype=3: 持续触发
    (3, 0): {"check": "duration", "trigger": "pulse"},        # 持续 + 脉冲
    (3, 1): {"check": "duration", "trigger": "continuous"},   # 持续 + 持续
    (3, 2): {"check": "duration", "trigger": "state"},        # 持续 + 状态
    
    # starttype=4: 次数触发
    (4, 0): {"check": "count", "trigger": "pulse"},           # 次数 + 脉冲
    (4, 1): {"check": "count", "trigger": "continuous"},      # 次数 + 持续
    (4, 2): {"check": "count", "trigger": "state"},           # 次数 + 状态
    
    # starttype=5: 开盘触发
    (5, 0): {"check": "market_open", "trigger": "pulse"},     # 开盘 + 脉冲
    (5, 1): {"check": "market_open", "trigger": "continuous"},# 开盘 + 持续
    (5, 2): {"check": "market_open", "trigger": "state"},     # 开盘 + 状态
    
    # starttype=6: 收盘触发
    (6, 0): {"check": "market_close", "trigger": "pulse"},    # 收盘 + 脉冲
    (6, 1): {"check": "market_close", "trigger": "continuous"},# 收盘 + 持续
    (6, 2): {"check": "market_close", "trigger": "state"},    # 收盘 + 状态
    
    # starttype=7: 定时触发
    (7, 0): {"check": "scheduled", "trigger": "pulse"},       # 定时 + 脉冲
    (7, 1): {"check": "scheduled", "trigger": "continuous"},  # 定时 + 持续
    (7, 2): {"check": "scheduled", "trigger": "state"},       # 定时 + 状态
}
```

#### 三种 trigger 模式

| trigger 模式 | 触发逻辑 | 说明 |
|-------------|---------|------|
| `pulse`（脉冲型） | 条件满足的**第一个 tick**触发，之后不触发 | 触发一次就完，下次需要条件不满足后重新满足 |
| `continuous`（持续型） | 条件满足期间**每个 tick 都触发** | 持续触发 |
| `state`（状态型） | 条件状态**变化时**触发（进入和退出各一次） | 边沿触发 |

---

### 5.6 should_trigger 核心逻辑

```python
def should_trigger(self, ctx: EngineContext) -> bool:
    """判断当前是否应该触发
    
    Args:
        ctx: 引擎上下文
    
    Returns:
        True 表示应该触发，False 表示不应该
    """
    now_ts = ctx.tick.current_data_ts
    now_date = ctx.tick.bar_time.date() if ctx.tick.bar_time else None
    
    # 日期变更检查：新的一天，重置每日触发标记
    if now_date and now_date != self._last_date:
        self._triggered_today = False
        self._last_date = now_date
    
    # 保存上一状态（用于状态变化检测）
    self._prev_is_open = self._is_open
    
    # 第一步：检查条件是否满足（门是否打开）
    self._is_open = self._check_condition(ctx)
    
    # 第二步：根据 cxtype 判断是否触发
    rule = _TIMING_RULES.get((self.starttype, self.cxtype))
    if rule is None:
        logger.warning(f"未知的时机组合: starttype={self.starttype}, cxtype={self.cxtype}")
        return False
    
    trigger_mode = rule["trigger"]
    
    if trigger_mode == "pulse":
        # 脉冲型：条件满足的第一个 tick 触发
        return self._is_open and not self._prev_is_open
    
    elif trigger_mode == "continuous":
        # 持续型：条件满足期间每个 tick 都触发
        return self._is_open
    
    elif trigger_mode == "state":
        # 状态型：状态变化时触发
        return self._is_open != self._prev_is_open
    
    else:
        logger.warning(f"未知的触发模式: {trigger_mode}")
        return False
```

---

### 5.7 条件检查（_check_condition）

```python
def _check_condition(self, ctx: EngineContext) -> bool:
    """检查时机条件是否满足（门是否打开）
    
    根据 starttype 调用不同的检查函数
    """
    rule = _TIMING_RULES.get((self.starttype, self.cxtype))
    if rule is None:
        return False
    
    check_type = rule["check"]
    
    if check_type == "immediate":
        # 即时触发：只要有数据更新就满足
        return ctx.tick.data_dirty
    
    elif check_type == "periodic":
        # 周期触发：每隔一段时间
        return self._check_periodic(ctx)
    
    elif check_type == "timepoint":
        # 时间点触发：到达指定时间点
        return self._check_timepoint(ctx)
    
    elif check_type == "duration":
        # 持续触发：条件满足持续一段时间
        return self._check_duration(ctx)
    
    elif check_type == "count":
        # 次数触发：条件满足 N 次
        return self._check_count(ctx)
    
    elif check_type == "market_open":
        # 开盘触发
        return self._check_market_open(ctx)
    
    elif check_type == "market_close":
        # 收盘触发
        return self._check_market_close(ctx)
    
    elif check_type == "scheduled":
        # 定时触发
        return self._check_scheduled(ctx)
    
    else:
        logger.warning(f"未知的检查类型: {check_type}")
        return False
```

---

### 5.8 record_trigger 方法

```python
def record_trigger(self, ctx: EngineContext):
    """记录一次触发（更新内部状态）
    
    在边实际执行后调用，更新计数器、时间戳等状态。
    """
    now_ts = ctx.tick.current_data_ts
    
    self._last_trigger_ts = now_ts
    self._trigger_count += 1
    
    # 对于每日触发类型，标记今日已触发
    if self.starttype in (5, 6, 7):  # 开盘、收盘、定时
        self._triggered_today = True
```

---

### 5.9 reset 方法

```python
def reset(self):
    """重置所有状态
    
    通常在股票池重启时调用
    """
    self._is_open = False
    self._prev_is_open = False
    self._duration_start_ts = 0.0
    self._trigger_count = 0
    self._last_trigger_ts = 0.0
    self._triggered_today = False
    self._last_date = None
```

---

### 5.10 与旧引擎的对应关系

| 旧引擎函数 | TimingGate 对应方法 | 说明 |
|-----------|-------------------|------|
| `_tdx_should_execute` | `should_trigger()` | 判断是否应该执行 |
| `_tdx_check_duration` | `_check_duration()` | 检查持续时长 |
| `_tdx_check_period` | `_check_periodic()` | 检查周期触发 |
| `_tdx_check_time` | `_check_scheduled()` | 检查定时触发 |

**设计差异**：
- 旧引擎：多个分散的函数，逻辑耦合
- 新设计：统一的 TimingGate 类，查表驱动，职责清晰

---

### 5.11 状态持久化

**是否需要持久化？**

不需要。理由：
1. TimingGate 的状态都是运行时状态（持续时长、触发次数等）
2. 股票池重启后，状态重新计算是合理的
3. 持久化会增加复杂度，且收益不大

**例外**：
如果有"今日已触发"的标记需要跨重启保留，可以持久化 `_triggered_today` 和 `_last_date`。但 v1.4 暂不考虑。

---

## 6. FilterEvaluator 详细设计（v1.4 P0-5）

### 6.1 类概述

**类名**：FilterEvaluator（筛选评估器）

**职责**：评估一条边的过滤条件，返回通过/被拒绝的股票集合。

**设计依据**：dispatch.json + engines.json + FormulaRouter

**设计原则**：
- 单一职责：只负责评估过滤条件，不负责时机判断（那是 TimingGate 的事）
- 缓存友好：能缓存的尽量缓存，不能缓存的明确说明
- 错误隔离：单只股票计算失败不影响其他股票

---

### 6.2 过滤类型

| 过滤类型 | nset 值 | 说明 | 示例 |
|---------|---------|------|------|
| **公式指标型** | 0, 1, 2 | 技术指标、条件选股、专家系统公式 | MACD 金叉、KDJ 超买 |
| **财务/行情标量型** | 3, 4 | 财务指标、行情数据的标量比较 |市盈率 < 30、涨幅 > 5% |
| **集合运算型** | 5 | AND/OR/差集等集合运算 | 两个条件的交集、并集 |
| **直通型** | - | 无条件，全部通过 | 无条件转移边 |

---

### 6.3 属性列表

| 属性 | 类型 | 说明 |
|------|------|------|
| `edge_id` | str | 所属边的 ID |
| `filter_config` | dict | 过滤配置（从 dispatch.json 解析） |
| `nset_type` | int | 过滤类型（0-5） |
| `formula_id` | str | 公式 ID（公式指标型用） |
| `formula_params` | dict | 公式参数 |
| `_left_evaluator` | FilterEvaluator | 左运算子（集合运算型用） |
| `_right_evaluator` | FilterEvaluator | 右运算子（集合运算型用） |
| `_set_operator` | str | 集合运算符（"and"/"or"/"diff"） |
| `_scalar_field` | str | 标量字段名（标量型用） |
| `_scalar_operator` | str | 比较运算符（">"/"<"/"==" 等） |
| `_scalar_value` | float | 比较值 |

---

### 6.4 方法列表

| 方法 | 说明 |
|------|------|
| `__init__(edge_id, filter_config, formula_router)` | 构造函数 |
| `evaluate(stocks, ctx) -> (passed, rejected)` | 评估过滤，返回通过和被拒绝的 |
| `evaluate_single(stock, ctx) -> bool` | 评估单只股票 |
| `_evaluate_formula(stocks, ctx) -> frozenset` | 公式指标型评估 |
| `_evaluate_scalar(stocks, ctx) -> frozenset` | 标量型评估 |
| `_evaluate_set(stocks, ctx) -> frozenset` | 集合运算型评估 |
| `needs_market_data() -> bool` | 是否依赖行情数据 |
| `from_config(edge_id, config, formula_router)` | 从配置构建（工厂方法） |

---

### 6.5 evaluate 核心逻辑

```python
def evaluate(self, stocks: frozenset, ctx: EngineContext) -> tuple[frozenset, frozenset]:
    """评估过滤条件
    
    Args:
        stocks: 输入股票代码集合
        ctx: 引擎上下文
    
    Returns:
        (passed, rejected): 通过的股票集合，被拒绝的股票集合
    """
    if not stocks:
        return frozenset(), frozenset()
    
    # 尝试从缓存获取（公式指标型才缓存）
    if self.nset_type in (0, 1, 2):
        cache = ctx.tick.formula_cache
        cache_key = self._get_cache_key()
        cached = cache.get(cache_key, stocks)
        if cached is not None:
            passed = cached
            rejected = stocks - passed
            return passed, rejected
    
    # 根据类型调用不同的评估方法
    if self.nset_type in (0, 1, 2):
        # 公式指标型
        passed = self._evaluate_formula(stocks, ctx)
    elif self.nset_type in (3, 4):
        # 财务/行情标量型
        passed = self._evaluate_scalar(stocks, ctx)
    elif self.nset_type == 5:
        # 集合运算型
        passed = self._evaluate_set(stocks, ctx)
    else:
        # 未知类型，默认全部通过（容错）
        logger.warning(f"未知的过滤类型: {self.nset_type}，全部通过")
        passed = stocks
    
    # 计算被拒绝的
    rejected = stocks - passed
    
    # 写入缓存（公式指标型才缓存）
    if self.nset_type in (0, 1, 2):
        cache = ctx.tick.formula_cache
        cache_key = self._get_cache_key()
        cache.set(cache_key, stocks, passed)
    
    return passed, rejected
```

---

### 6.6 evaluate_single 方法

```python
def evaluate_single(self, stock: str, ctx: EngineContext) -> bool:
    """评估单只股票是否通过过滤
    
    Args:
        stock: 股票代码
        ctx: 引擎上下文
    
    Returns:
        True 表示通过，False 表示被拒绝
    """
    passed, _ = self.evaluate(frozenset([stock]), ctx)
    return stock in passed
```

**注意**：单只股票评估的效率可能比批量评估低，因为公式计算通常是批量的。优先使用 `evaluate()` 批量评估。

---

### 6.7 公式指标型评估

```python
def _evaluate_formula(self, stocks: frozenset, ctx: EngineContext) -> frozenset:
    """公式指标型评估
    
    通过 FormulaRouter 调用公式引擎计算
    """
    formula_router = ctx.services.formula_router
    
    # 调用公式路由器批量计算
    # FormulaRouter 负责：路由到正确的公式引擎、处理参数、返回结果
    result = formula_router.eval_batch(
        formula_id=self.formula_id,
        codes=list(stocks),
        params=self.formula_params,
        context=ctx
    )
    
    # result 是一个字典 {code: bool/float}，表示每只股票是否满足条件
    # 对于条件选股公式，返回的是 bool（True 表示满足条件）
    # 对于技术指标公式，需要结合 noperate 进行比较
    
    if self.nset_type == 0:
        # 条件选股：直接取 True 的股票
        passed = frozenset(code for code, val in result.items() if val)
    
    elif self.nset_type == 1:
        # 技术指标 + 比较：需要结合 noperate
        passed = self._apply_noperate(result, ctx)
    
    elif self.nset_type == 2:
        # 专家系统：类似条件选股
        passed = frozenset(code for code, val in result.items() if val)
    
    else:
        passed = frozenset()
    
    return passed
```

---

### 6.8 标量型评估

```python
def _evaluate_scalar(self, stocks: frozenset, ctx: EngineContext) -> frozenset:
    """财务/行情标量型评估
    
    从 DataQuery 获取标量数据，然后比较
    """
    data_query = ctx.services.data_query
    
    # 批量获取标量数据
    values = data_query.get_scalar_values(
        codes=list(stocks),
        field=self._scalar_field,
        context=ctx
    )
    
    # 根据比较运算符过滤
    op = self._scalar_operator
    target = self._scalar_value
    
    def compare(val):
        if val is None:
            return False
        if op == ">":
            return val > target
        elif op == ">=":
            return val >= target
        elif op == "<":
            return val < target
        elif op == "<=":
            return val <= target
        elif op == "==":
            return val == target
        elif op == "!=":
            return val != target
        else:
            return False
    
    passed = frozenset(code for code, val in values.items() if compare(val))
    return passed
```

---

### 6.9 集合运算型评估

```python
def _evaluate_set(self, stocks: frozenset, ctx: EngineContext) -> frozenset:
    """集合运算型评估
    
    递归计算左右子表达式，然后进行集合运算
    """
    # 递归计算左右子表达式
    left_passed, _ = self._left_evaluator.evaluate(stocks, ctx)
    right_passed, _ = self._right_evaluator.evaluate(stocks, ctx)
    
    # 执行集合运算
    if self._set_operator == "and":
        # 交集
        return left_passed & right_passed
    elif self._set_operator == "or":
        # 并集
        return left_passed | right_passed
    elif self._set_operator == "diff":
        # 差集
        return left_passed - right_passed
    else:
        logger.warning(f"未知的集合运算符: {self._set_operator}")
        return frozenset()
```

---

### 6.10 与 FormulaRouter 的接口

#### FormulaRouter 职责

FormulaRouter 负责：
1. 公式路由：根据 formula_id 路由到正确的公式引擎（TQ、HQChart、本地计算等）
2. 参数处理：处理公式参数的转换和验证
3. 批量计算：支持批量股票的公式计算
4. 错误处理：公式计算失败时的降级和容错

**FormulaRouter 是无状态的**（v1.4 修正），不持有 FormulaCache。

#### 接口约定

```python
class FormulaRouter:
    """公式路由器（无状态，引擎级生命周期）"""
    
    def eval_batch(
        self,
        formula_id: str,
        codes: list[str],
        params: dict | None = None,
        context: EngineContext | None = None
    ) -> dict[str, any]:
        """批量计算公式
        
        Args:
            formula_id: 公式 ID
            codes: 股票代码列表
            params: 公式参数
            context: 引擎上下文
        
        Returns:
            字典 {code: result}，每只股票的计算结果
        """
        ...
    
    def eval_single(
        self,
        formula_id: str,
        code: str,
        params: dict | None = None,
        context: EngineContext | None = None
    ) -> any:
        """计算单只股票的公式"""
        ...
    
    def validate_formula(self, formula_id: str, params: dict | None = None) -> bool:
        """验证公式是否有效"""
        ...
```

---

### 6.11 缓存策略

#### 什么能缓存

| 过滤类型 | 能否缓存 | 原因 |
|---------|---------|------|
| 公式指标型（0, 1, 2） | ✅ 能 | 相同输入 + 相同数据 → 相同输出 |
| 财务标量型（3） | ⚠️ 有限 | 财务数据变化慢，可以缓存更久，但 v1.4 先按 tick 级 |
| 行情标量型（4） | ✅ 能 | 同一 tick 内行情数据不变 |
| 集合运算型（5） | ⚠️ 视情况 | 如果子表达式都能缓存，整体也能缓存；但递归缓存复杂，v1.4 不缓存 |
| 直通型 | - | 不需要缓存 |

#### v1.4 缓存策略

- **公式指标型**：缓存，key = (formula_id, input_codes_frozenset)
- **标量型**：暂不缓存（因为 DataQuery 可能有自己的缓存）
- **集合运算型**：暂不缓存（复杂度高，收益待评估）
- **缓存范围**：tick 级（FormulaCache 在 TickContext 里）

#### 为什么集合运算型不缓存？

1. 实现复杂：需要递归检查子表达式是否有缓存
2. 命中率低：集合运算通常只做一次
3. 可以后续优化：如果性能不够再加

---

### 6.12 错误处理策略

| 错误场景 | 处理方式 | 说明 |
|---------|---------|------|
| 单只股票公式计算失败 | 该股票标记为不通过，记录日志 | 错误隔离，不影响其他股票 |
| 全部股票计算失败 | 返回空集合，记录错误 | 容错处理 |
| 公式不存在 | 初始化时验证，运行时返回空 | 提前发现问题 |
| 参数错误 | 初始化时验证，运行时返回空 | 提前发现问题 |
| 数据源不可用 | 降级处理（返回空或上一次结果） | 可用性优先 |

---

### 6.13 工厂方法：from_config

```python
@classmethod
def from_config(
    cls,
    edge_id: str,
    config: dict,
    formula_router: FormulaRouter
) -> "FilterEvaluator":
    """从配置构建 FilterEvaluator
    
    Args:
        edge_id: 边 ID
        config: 过滤配置（从 dispatch.json 解析）
        formula_router: 公式路由器
    
    Returns:
        FilterEvaluator 实例
    """
    nset_type = config.get("nset", 0)
    
    if nset_type == 5:
        # 集合运算型，递归构建左右子表达式
        left_config = config.get("left", {})
        right_config = config.get("right", {})
        left_eval = cls.from_config(edge_id + ".left", left_config, formula_router)
        right_eval = cls.from_config(edge_id + ".right", right_config, formula_router)
        
        evaluator = cls(edge_id, config, formula_router)
        evaluator.nset_type = 5
        evaluator._left_evaluator = left_eval
        evaluator._right_evaluator = right_eval
        evaluator._set_operator = config.get("operator", "and")
        return evaluator
    
    elif nset_type in (0, 1, 2):
        # 公式指标型
        evaluator = cls(edge_id, config, formula_router)
        evaluator.nset_type = nset_type
        evaluator.formula_id = config.get("formula_id", "")
        evaluator.formula_params = config.get("params", {})
        return evaluator
    
    elif nset_type in (3, 4):
        # 标量型
        evaluator = cls(edge_id, config, formula_router)
        evaluator.nset_type = nset_type
        evaluator._scalar_field = config.get("field", "")
        evaluator._scalar_operator = config.get("operator", ">")
        evaluator._scalar_value = config.get("value", 0)
        return evaluator
    
    else:
        # 未知类型，默认直通
        logger.warning(f"未知的过滤类型: {nset_type}，使用直通型")
        evaluator = cls(edge_id, config, formula_router)
        evaluator.nset_type = -1  # 直通型
        return evaluator
```

---

## 7. 并发模型（v1.4 P0-6）

### 7.1 结论：单线程事件循环

**事件循环是单线程的**。所有的状态变更（节点股票集合、版本号、TimingGate 状态等）都在事件循环线程中完成。

**为什么是单线程？**

1. **Python GIL**：CPython 的全局解释器锁导致多线程 CPU 密集型任务没有性能提升
2. **事件驱动天然单线程**：事件循环的设计模式本身就是单线程的（参考 Node.js、asyncio）
3. **避免并发问题**：单线程下不需要考虑锁、竞态条件、死锁等问题
4. **股票池逻辑是 IO 密集型**：主要耗时在数据 IO 和公式计算，公式计算可以批量处理
5. **正确性优先**：单线程模型更容易保证正确性

---

### 7.2 线程模型

```
┌─────────────────────────────────────────────────────────┐
│ 主线程（事件循环线程）                                    │
│  ┌───────────────────────────────────────────────────┐  │
│  │  EventLoop.run_tick()                             │  │
│  │  - 处理事件                                        │  │
│  │  - 变更节点状态                                    │  │
│  │  - 调用 FilterEvaluator                           │  │
│  │  - 更新 TimingGate 状态                           │  │
│  │  - 所有状态变更都在这里完成                         │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
           ▲               ▲
           │ enqueue       │ enqueue
           │               │
┌──────────┴──────┐ ┌──────┴──────────┐
│ 数据推送线程     │ │ 定时器线程       │
│ - 接收 TQ 推送  │ │ - 定时触发 tick  │
│ - 更新数据缓存   │ │ - 定时刷新备选池 │
│ - 往队列放事件   │ │ - 往队列放事件   │
└─────────────────┘ └─────────────────┘
```

---

### 7.3 各组件的线程安全性

| 组件 | 线程安全？ | 说明 |
|------|-----------|------|
| **EventLoop** | 否 | 只能在事件循环线程中运行 |
| **EventQueue** | ✅ 是 | enqueue/dequeue 有锁保护 |
| **Node（状态变更）** | 否 | 只能在事件循环线程中变更状态 |
| **Node（只读访问）** | ✅ 是 | 读操作不需要锁（假设引用是原子的） |
| **TimingGate** | 否 | 只能在事件循环线程中调用 |
| **FilterEvaluator** | ✅ 是 | 无状态，可重入 |
| **FormulaRouter** | ✅ 是 | 无状态，可重入 |
| **FormulaCache** | 否 | 每个 tick 一个实例，只在事件循环线程中访问 |
| **TickContext** | 否 | 每个 tick 一个实例，只在事件循环线程中访问 |
| **EngineServices** | ✅ 是 | 引用不变，内部服务都是线程安全的 |

---

### 7.4 EventQueue 的线程安全

**EventQueue 是唯一需要线程安全的组件**，因为：
- 数据推送线程会调用 enqueue
- 定时器线程会调用 enqueue
- 事件循环线程会调用 dequeue

**实现方式**：用一把锁保护队列的所有操作。

```python
class EventQueue:
    def __init__(self):
        self._queue: list = []
        self._lock = threading.Lock()
        self._pending_node_changes: dict = {}
        self._pending_edges: set = set()
    
    def enqueue(self, event: Event):
        """入队（线程安全）"""
        with self._lock:
            heapq.heappush(self._queue, event)
    
    def dequeue(self) -> Event | None:
        """出队（线程安全）"""
        with self._lock:
            if not self._queue:
                return None
            return heapq.heappop(self._queue)
    
    def enqueue_node_changed(self, node_id, version, added, removed, source=""):
        """节点变化事件入队（线程安全）"""
        with self._lock:
            # ... 合并逻辑 ...
            # ... 入队 ...
    
    def enqueue_edge_execute(self, edge_id):
        """边执行事件入队（线程安全，去重）"""
        with self._lock:
            if edge_id in self._pending_edges:
                return  # 去重：已经在队列中了
            self._pending_edges.add(edge_id)
            # ... 创建事件并入队 ...
```

---

### 7.5 哪些部分可以并发？

#### 可以并发的部分

1. **数据接收**：数据推送线程和事件循环线程并行（已经是这样了）
2. **公式计算**：如果公式计算是 CPU 密集型的，可以用进程池并行计算
   - 注意：是多进程，不是多线程（因为 GIL）
   - FormulaRouter 可以内部用进程池实现并行
   - 但 FilterEvaluator 的接口仍然是同步的

#### 不能并发的部分

1. **状态变更**：所有状态变更（节点股票集合、版本号等）必须在事件循环线程中完成
2. **事件处理**：事件必须一个一个处理，不能并行处理
3. **TimingGate 状态更新**：时机门控的状态必须串行更新

---

### 7.6 为什么事件循环是单线程的？

**事件循环的本质是状态机**：
- 每个事件都会改变系统的状态
- 如果并行处理多个事件，会有竞态条件
- 需要加锁，而加锁会导致死锁、性能下降等问题

**事件驱动模型的优势**：
- 单线程 + 异步 IO，也能达到很高的吞吐量
- 因为大部分时间是在等 IO（数据推送、公式计算）
- 股票池的计算是批量的，单次计算可以处理很多股票

**类比**：
- Node.js 是单线程的，但能处理高并发
- Python 的 asyncio 也是单线程的
- 股票池的事件循环也是同样的设计思想

---

### 7.7 P0-1 问题的并发验证

**回到 P0-1（Pending 版本号验证顺序）的问题**：

在单线程事件循环模型下，`on_node_stock_changed` 执行时，`node.version` 不会被其他线程修改。那为什么还要先验证后消费？

**答案**：
1. **防御性编程**：虽然当前是单线程，但未来可能引入并发，提前做好准备
2. **dequeue 时的验证是基于 pending.version 的，消费前验证是基于 node.version 的，两者是双重保险**
   - dequeue 时验证：`event.version == info.version`
   - 消费前验证：`event.version == node.version`
   - 正常情况下两者应该一致，但如果有 bug 导致不一致，第二次验证能发现
3. **文档完整性**：明确说明并发模型，让开发人员清楚哪些是线程安全的，哪些不是

---

### 7.8 与旧引擎的并发模型对比

| 维度 | 旧引擎 | 新引擎（v1.4） |
|------|--------|---------------|
| 事件循环 | 单线程 | 单线程 |
| 数据推送 | 可能在其他线程 | 数据推送线程 + 事件队列 |
| 状态变更 | 单线程 | 单线程（事件循环线程） |
| 并发安全 | 靠"小心使用"保证 | 明确的线程模型 + 锁保护的队列 |

**结论**：两者都是单线程事件循环模型，是兼容的。

---

## 8. EdgeExecuteEvent 去重逻辑（v1.4 P1 补充）

### 8.1 去重规则

**规则**：同一条边在队列中最多有一个待执行事件。

**为什么需要去重？**
- 同一条边可能被多次触发（比如多个上游节点同时变化）
- 但只需要执行一次（因为执行时会处理所有最新状态）
- 避免重复计算，提高性能

---

### 8.2 实现方式

```python
class EventQueue:
    def __init__(self):
        self._queue: list = []
        self._pending_edges: set[str] = set()  # 待执行的边 ID 集合（去重用）
    
    def enqueue_edge_execute(self, edge_id: str, source: str = ""):
        """边执行事件入队（自动去重）
        
        如果边已经在队列中了，就不重复入队。
        """
        if edge_id in self._pending_edges:
            # 已经在队列中了，跳过
            return
        
        self._pending_edges.add(edge_id)
        
        event = EdgeExecuteEvent(
            edge_id=edge_id,
            timestamp=time.time(),
            source=source,
            priority=EVENT_PRIORITY_EDGE_EXECUTE
        )
        self._enqueue(event)
```

**出队时移除标记**：
```python
def dequeue(self) -> Event | None:
    while not self.is_empty():
        event = heapq.heappop(self._queue)
        
        # 如果是边执行事件，从 pending_edges 中移除
        if isinstance(event, EdgeExecuteEvent):
            self._pending_edges.discard(event.edge_id)
        
        # ... 墓碑检查等 ...
        
        return event
    
    return None
```

---

### 8.3 典型场景

#### 场景 1：多个上游节点变化，同一条边被多次触发

```
1. 节点 A 变化 → 触发边 E → enqueue_edge_execute("E")
   → pending_edges = {"E"}，事件入队 ✓

2. 节点 B 变化 → 触发边 E → enqueue_edge_execute("E")
   → E 已在 pending_edges 中，跳过 ✓

3. 边 E 出队执行
   → pending_edges 移除 "E"
   → 边 E 执行一次，处理所有最新状态 ✓
```

#### 场景 2：边正在执行时，又有触发

```
1. 边 E 出队执行
   → pending_edges 移除 "E"

2. 执行过程中，节点 A 变化 → 触发边 E
   → enqueue_edge_execute("E")
   → pending_edges = {"E"}，事件入队 ✓

3. 边 E 执行完成
   → 队列中还有一个边 E 的事件
   → 会被再次执行（处理执行期间的新变化）✓
```

---

## 9. 总览：v1.4 变更清单

### P0 修复（6个）

| # | 问题 | 修复方案 | 位置 |
|---|------|---------|------|
| 1 | Pending 消费时版本号验证顺序错误 | 先验证版本号，验证通过再消费 | §2 |
| 2 | FormulaCache 生命周期矛盾 | 移到 TickContext 里，与 tick 级生命周期匹配 | §3 |
| 3 | EventLoop 缺失详细设计 | 补全主循环、事件分发、异常处理、停止机制 | §4 |
| 4 | TimingGate 缺失详细设计 | 补全 24 种组合查表驱动、状态管理、与旧引擎对应关系 | §5 |
| 5 | FilterEvaluator 缺失详细设计 | 补全 4 种过滤类型、FormulaRouter 接口、缓存策略 | §6 |
| 6 | 并发模型未明确 | 明确单线程事件循环 + 线程安全边界 | §7 |

### P1 补充（1个）

| # | 改进 | 位置 |
|---|------|------|
| 1 | EdgeExecuteEvent 去重逻辑 | §8 |

### 其他改进

| # | 改进 | 位置 |
|---|------|------|
| 1 | TickContext 补全 data_version 字段 | §1.3 |
| 2 | TickContext 创建策略明确（每个 tick 新建） | §3.8 |
| 3 | FormulaCache 接口定义补全 | §3.6 |
| 4 | 三大核心类关系图 | §1.5 |

---

## 10. 下一步计划

### 开发顺序建议

1. **第一周：核心骨架**
   - EventQueue（含 Pending 合并 + 边去重 + 线程安全）
   - EventLoop（主循环 + 事件分发 + 异常处理）
   - Node/Edge 基类
   - EngineServices / TickContext / EngineContext
   - Topology 基础功能

2. **第二周：核心功能**
   - TimingGate（24 种组合查表驱动）
   - FilterEvaluator（4 种过滤类型 + FormulaCache）
   - FormulaRouter（无状态路由）
   - SourceNode / StatePoolNode / ConditionNode
   - ConditionalEdge / UnconditionalEdge
   - 数据订阅与触发流程

3. **第三周：验证与完善**
   - 等价性测试框架
   - Oracle 对比测试
   - 性能基准测试
   - bug 修复

### v1.5 可能的议题

- 增量过滤的详细设计（如果 v1.x 性能不够）
- 时间轮（Time Wheel）优化 TimingGate 检查
- 更细粒度的指标体系
- 调试工具与可视化
- 公式计算的多进程并行

---

**文档版本**：v1.4  
**创建日期**：2026-07-01  
**基于版本**：v1.3（评审得分 78.5）
