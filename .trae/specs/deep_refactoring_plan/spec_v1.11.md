# 股票池深度重构规划 v1.11

> 版本主题：纠正实盘认知——零延迟事件模型，删除批次化，空间批量≠时间批量
> 设计原则：表驱动、数据驱动、事件驱动、增量优先、全量保底、概念最简
> 目标：纠正"时间维度攒批=增加延迟=实盘不能接受"的根本性错误；明确空间批量（向量化）vs 时间批量（攒批）的本质区别；建立实盘零延迟事件模型；修正性能模型（向量化是计算层实现优化，不是架构层攒批）；删除所有批次相关的表和配置

---

## v1.10 → v1.11 变更摘要表

**变更日期：** 2026-07-01

| # | 变更项 | v1.10 | v1.11 | 本质变化 |
|---|--------|------|-------|---------|
| 1 | **根本性错误纠正** | 时间维度攒批（批次化）是性能优化手段 | **彻底删除时间批次化概念，实盘零延迟** | 从"攒批换吞吐"到"来一个处理一个"——时间批量=增加延迟=实盘不能接受，这是回测思维，大错特错 |
| 2 | **两种批量的本质区别** | 混淆空间批量与时间批量，统称为"批次化" | **明确区分：空间批量（向量化）✅ vs 时间批量（攒批）❌** | 空间批量=一个tick来了所有股票一起算（向量化，不增延迟）；时间批量=攒多个tick再处理（增延迟，实盘不行） |
| 3 | **实盘事件模型** | 数据事件攒批处理，批次大小可调 | **零延迟事件模型：每个tick立即处理，事件立即发出** | 每个tick是独立事件，立即处理，预警/通知/下单实时触发，延迟就是金钱 |
| 4 | **性能模型修正** | 批次化是性能优化，向量化是批次的手段 | **向量化是计算层实现优化，与批次化无关** | 向量化是"空间批量"——一个tick内所有股票一起算，不是"时间批量"——攒多个tick再算 |
| 5 | **批次相关表删除** | batch_state（运行时表）+ batch_config（编译期表） | **全部删除** | 彻底清除时间批次化概念，从架构核心里拿掉 |
| 6 | **核心循环伪代码** | 有攒批缓冲（pending_ticks），handle_tick_batch() | **简化：来一个处理一个，handle_single_tick()** | 没有攒批，每个tick立即走完完整流程：数据更新→指标计算→比较判断→集合运算→propagate→发事件 |
| 7 | **核心运行时表数** | 9 张（含 batch_state） | **8 张（删除 batch_state）** | 表数减1，概念更纯净，回归事件驱动本质 |

**一句话总结 v1.11 升级：** 纠正"时间维度攒批"的根本性错误（那是回测思维，实盘不能接受）；明确空间批量（向量化，不增延迟）与时间批量（攒批，增延迟）的本质区别；建立实盘零延迟事件模型（每个tick立即处理，事件立即发出）；修正性能模型（向量化是计算层实现优化，不是架构层攒批）；删除所有批次相关的表和配置；保留并强化正确的部分（事件驱动、三态、分层、脏标记、向量化计算）。

---

## 一、根本性错误纠正：时间维度攒批 = 增加延迟 = 实盘不能接受

### 1.1 错误的根源：把回测思维带到实盘

**v1.10 及之前的"批次化"、"攒一批tick再处理"——这是回测思维，大错特错！**

```
回测思维（错误的，被带到了实盘）：
  回测时有海量历史数据，为了效率，攒一批一起算
  → 批次化 = 提高吞吐量
  → 反正都是历史数据，晚一点没关系

实盘真相（正确的）：
  每个tick来了必须立即处理，不能等
  预警、通知、下单必须实时触发
  延迟就是金钱，晚一秒可能错过最佳买卖点
  不存在"攒一批提高效率"这种说法——那是用延迟换吞吐，实盘不接受
```

**为什么这是根本性错误？**
- 这不是"性能优化程度"的问题，而是"方向对错"的问题
- 时间维度攒批，本质上就是用延迟换吞吐
- 实盘场景下，延迟是不可接受的代价
- 把回测的优化手段当成实盘的架构核心，方向完全反了

### 1.2 代码验证：实盘代码里根本没有"攒批"

**验证结论：engine.py 的实盘循环里，没有任何"攒批"、"批次化"的逻辑。**

```python
# 实际代码（engine.py:3710-3718 run_mode）：
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

**实际情况：**
- 就是一个简单的循环，每次获取最新数据，调用 `_tick()` 处理
- 没有 pending_ticks 缓冲，没有 batch_config，没有 batch_state
- `_tick_interval` 是轮询间隔（默认1秒），不是"攒批等待时间"
- 每个循环周期，数据刷新一次，处理一次，事件立即发出

**运行时表验证（engine.py:131-144 _RUNTIME_TABLE_NAMES）：**
- 里面完全没有 batch_state、batch_config 等表
- 现有的表都是事件驱动、状态管理相关的

### 1.3 实盘的真相：零延迟，来一个处理一个

**实盘事件处理的正确模型：**

```
每个 tick 是一个独立事件，立即处理：

  tick 到达 → 数据更新 → 指标计算（向量化，全量算）
                    → 比较判断 → 集合运算 → propagate → 发事件
                    （所有步骤在一个 tick 内完成，不等待，不攒批）

  预警事件、节点更新事件立即发出，不缓冲
  下单信号实时触发，延迟就是金钱
```

**为什么实盘可以零延迟？**
- A股一秒钟也就几笔到几十笔tick，不是每秒几万次
- 5000只股票，100个指标，向量化计算，现代CPU上是毫秒级
- 指标计算的瓶颈是Python调用开销，不是计算量
- 向量化把Python调用降到一次，计算在C层面完成
- 所以即使每个tick都全量算，也完全够快

---

## 二、两种"批量"的本质区别：空间批量 ≠ 时间批量

### 2.1 概念澄清：两种完全不同的"批量"

**有两种"批量"，完全不同，之前的架构师把这俩搞混了。**

| 维度 | 时间批量（攒批）❌ | 空间批量（向量化）✅ |
|------|-------------------|---------------------|
| **定义** | 攒多个 tick 再处理 | 一个 tick 来了，所有股票的指标一起用向量化算 |
| **对延迟的影响** | 增加延迟（tick来了要等） | 不增加延迟（反而更快） |
| **实盘可用性** | 绝对不行（延迟就是金钱） | 完全可以（越快越好） |
| **本质** | 用延迟换吞吐 | 用向量化换速度 |
| **所在层次** | 架构层（事件调度方式） | 计算层（实现优化手段） |
| **代码中是否存在** | 不存在（之前是文档虚构的） | 存在（公式引擎的 NumPy/pandas 向量化） |

### 2.2 时间批量（攒批）：实盘绝对不行

```
时间批量 = 攒多个 tick 再处理

  tick1 → 等...
  tick2 → 等...
  tick3 → 攒够了，一起处理
  ↑
  这期间 tick1 的预警、下单都被延迟了
  实盘：晚一秒，可能错过最佳买卖点，损失就是真金白银
```

**为什么时间批量在实盘不可接受：**
1. **预警延迟**：股票突破了，预警要等攒够一批才发，晚了
2. **下单延迟**：买卖信号要等攒够一批才触发，滑点变大
3. **通知延迟**：重要事件通知不及时，用户体验差
4. **违背实时性原则**：实盘的核心需求就是"快"，攒批正好反着来

### 2.3 空间批量（向量化）：不增延迟，反而更快

```
空间批量 = 一个 tick 来了，所有股票一起算（向量化）

  一个 tick 到达（比如股票A的价格变了）
    ↓
  更新 latest_tick（只更新A）
    ↓
  计算指标：所有股票的 MA5 一起算（向量化，NumPy 底层 C）
    ↓
  虽然只变了A，但全量算的时间和只算A差不多
  （因为瓶颈是 Python 调用开销，不是计算量）
    ↓
  所以不如直接全量算，反而简单
```

**为什么空间批量不增加延迟？**
- 来一个 tick，数据更新了一只股票
- 计算指标时，用向量化的方式：所有股票一起算
- 虽然只变了一只，但向量化计算所有股票的时间，和只算一只的时间差不多
- 因为底层是 C，Python 调用开销才是大头
- 所以不如直接全量算，反而简单
- **注意：这是"计算层的向量化实现"，不是"攒批"**

### 2.4 为什么之前会搞混？

**混淆的根源：都带"批量"二字，但维度完全不同。**

```
容易混淆的地方：
  - 时间批量：batch of ticks（多个时间点的数据）
  - 空间批量：batch of stocks（多个股票的计算）

  两者都叫"批量"，但是：
  - 一个是时间维度的批量 → 增加延迟 ❌
  - 一个是空间维度的批量 → 不增延迟，反而更快 ✅

  之前的架构师把"向量化计算（空间批量）"
  说成了"批次化（时间批量）"
  概念上就错了，导致整个架构认知偏差
```

**正确的表述：**
- ❌ 不要说："批次化处理"、"攒一批tick"
- ✅ 应该说："向量化计算"、"全股票并行计算"、"空间批量"

---

## 三、实盘零延迟事件模型

### 3.1 核心原则：每个 tick 立即处理，事件立即发出

**实盘事件处理的正确模型：**

```
零延迟事件模型：

  事件循环（单线程）
    ↑
    ├─ 数据事件：tick来了，立即处理，不攒批
    ├─ 定时器事件：时间到了，立即触发，精确到点
    └─ 控制事件：启动/暂停/停止，立即响应

  每个事件处理流程：
    数据更新 → 指标计算（向量化，全量算）
           → 比较判断 → 集合运算 → propagate → 发事件
    （所有步骤在一个事件内完成，不等待，不缓冲）
```

**关键特性：**
1. **零延迟**：tick来了就处理，不等待攒批
2. **实时事件**：预警、通知、下单信号立即发出，不缓冲
3. **单线程一致**：单线程事件循环天然提供一致性保证
4. **向量化计算**：计算层用空间批量（向量化）提速，不增延迟

### 3.2 事件处理的完整流程（单个 tick 内）

**一个 tick 到来后的完整处理流程：**

```
tick 到达
  │
  ▼
阶段1：数据更新
  ├─ 更新 latest_tick（唯一真相源）
  ├─ 标记 data_dirty（数据脏了）
  ├─ 标记相关节点为 dirty（节点脏驱动）
  └─ 通知公式引擎数据更新（缓存失效标记）
  │
  ▼
阶段2：TTL 过期检查
  └─ 检查 ttl_expiry_queue，移除过期股票
  │
  ▼
阶段3：按拓扑序处理脏节点（增量计算）
  └─ 对每个脏节点，处理其所有出边
       │
       ▼
     边处理（三层 filter + propagate）
       ├─ 第一层：指标计算（向量化，调用公式引擎）
       ├─ 第二层：比较判断（三态：True/False/None）
       ├─ 第三层：集合运算（独立型/排名型）
       └─ propagate → 更新目标节点 → 发事件
  │
  ▼
阶段4：后处理
  ├─ PK 排名
  ├─ 分析角度
  ├─ 看盘面板
  └─ 预警检查 & 预警事件发送
  │
  ▼
阶段5：清脏
  ├─ 清空 dirty_nodes
  ├─ 重置 data_dirty
  └─ 清空 edge_fired
```

**所有阶段在一个 tick 内完成，不等待，不攒批。**

### 3.3 事件立即发出，不缓冲

**事件发送原则：产生就发，不攒批，不缓冲。**

```
事件类型与发送时机：

  入池事件（stock_entered）
    产生时机：propagate 时，股票进入目标节点
    发送时机：立即发送，不等待

  出池事件（stock_exited）
    产生时机：propagate 时，股票离开目标节点
    发送时机：立即发送，不等待

  预警事件（alert）
    产生时机：post_tick 预警检查时触发
    发送时机：立即发送，不等待
    （但有 cooldown 机制，防止刷屏）

  交易信号（signal）
    产生时机：propagate 时，目标节点是交易角色
    发送时机：立即发送，不等待
    （延迟就是金钱，晚一秒滑点可能就大了）

  节点更新事件（node_updated）
    产生时机：节点股票列表变化
    发送时机：立即发送，不等待
```

**为什么事件不能攒批？**
- 预警晚了，用户可能错过操作时机
- 下单信号晚了，滑点变大，真金白银的损失
- 通知晚了，用户体验差
- 事件攒批和数据攒批一样，都是用延迟换吞吐，实盘不接受

---

## 四、性能模型修正：向量化是计算层实现优化

### 4.1 正确的性能模型

**性能模型的正确认知：**

```
架构层（事件驱动，零延迟）
  ↑
  │ 每个事件立即处理
  │
计算层（向量化，空间批量）
  ↑
  │ 一个事件内，所有股票一起算
  │ 用 NumPy/pandas 向量化提速
  │
底层（C 实现，高效计算）
```

**关键点：**
- **架构层**：事件驱动，零延迟，来一个处理一个（时间维度不攒批）
- **计算层**：向量化，空间批量，所有股票一起算（空间维度批量）
- **向量化是计算层的实现优化，不是架构层的攒批手段**

### 4.2 为什么向量化够快？

**向量化的性能优势：**

```
逐条循环（Python 层循环，慢）：
  for code in codes:
      result[code] = calc_ma(prices[code], 5)
  → 5000 只股票，循环 5000 次
  → 每次都有 Python 解释器开销
  → 瓶颈在 Python 调用，不在计算

向量化（NumPy/pandas 底层 C 实现，快）：
  all_prices = np.array([prices[code] for code in codes])
  result = pd.rolling_mean(all_prices, window=5)
  → 一次向量化运算，Python 层开销只有一次
  → 计算在 C 层面完成，效率高
  → 比逐条循环快 10-100 倍
```

**性能估算：**
- 5000 只股票，100 个指标，向量化计算应该在毫秒级（~10-100ms）
- 对比：逐条 Python 循环可能要几秒到几十秒
- A股一秒钟也就几笔到几十笔 tick
- 所以即使每个 tick 都全量算，也完全够快

### 4.3 为什么全量算反而简单？

**增量计算 vs 全量计算：**

```
增量计算（理论上更快，但复杂）：
  - 只重算数据变了的股票
  - 需要追踪哪些股票变了
  - 需要 dirty 标记、增量更新逻辑
  - 代码复杂，容易出 bug
  - 边界情况多（新股、停牌、复牌）

全量计算（向量化后，简单且够快）：
  - 每个 tick 都重算所有股票
  - 不需要追踪谁变了
  - 逻辑简单，不容易出 bug
  - 因为向量化，全量算和增量算时间差不多
  （瓶颈是 Python 调用开销，不是计算量）

结论：用向量化 + 全量算，简单且够快
```

**现有代码验证：**
- `_refresh_latest_tick()` 更新数据后标记 `_data_dirty = True`
- `_dirty_nodes` 标记哪些节点需要处理（节点级增量）
- 但具体到指标计算，用向量化全量算更简单
- 这是"节点级增量 + 计算级全量向量化"的组合

---

## 五、删除批次相关的表和配置

### 5.1 运行时表：删除 batch_state

**v1.10 的 9 张表 → v1.11 的 8 张表（删除 batch_state）：**

| # | v1.10 表名 | v1.11 状态 | 说明 |
|---|-----------|-----------|------|
| 1 | `latest_tick` | ✅ 保留 | 唯一真相源，每只股票自己的 tick 时间戳 |
| 2 | `node_stocks` | ✅ 保留 | 各节点当前股票列表 |
| 3 | `ttl_expiry_queue` | ✅ 保留 | TTL 过期队列 |
| 4 | `dirty_stocks` | ✅ 保留 | 股票级水位线（本 tick 数据更新的股票集合） |
| 5 | `node_stock_change` | ✅ 保留 | entered/exited 双集合 |
| 6 | `node_data_dirty` | ✅ 保留 | 节点里哪些股票的数据变了 |
| 7 | `edge_compare_results` | ✅ 保留 | 三态比较结果（True/False/None） |
| 8 | `edge_filter_results` | ✅ 保留 | 排名型用有序列表，独立型用 Set |
| 9 | `batch_state` | ❌ 删除 | 彻底清除时间批次化概念 |

**净变化：9 张 → 8 张（删除 batch_state）。**

### 5.2 编译期表：删除 batch_config

**编译期表变化：**

| 编译产物 | v1.10 | v1.11 | 说明 |
|---------|------|------|------|
| `formula_registry` | ✅ | ✅ | 公式注册表 |
| `comparison_operators` | ✅ | ✅ | 比较算子集 |
| `edge_indicator_refs` | ✅ | ✅ | 每条边引用的指标ID列表 |
| `edge_compare_spec` | ✅ | ✅ | 比较层规格 |
| `edge_set_op_spec` | ✅ | ✅ | 集合运算层规格 |
| `edge_filter_type` | ✅ | ✅ | filter 类型 |
| `edge_timer_specs` | ✅ | ✅ | 每条边的定时器配置 |
| `batch_config` | ✅ | ❌ | 彻底删除，批次化概念不存在 |

**净变化：8 个 → 7 个（删除 batch_config）。**

### 5.3 配置文件：删除批次相关配置

**需要删除的配置项：**
- `timing.json` 中的 `batch_max_wait_ms`、`batch_max_size`、`batch_min_size`
- `data_config.json` 中的批次相关配置
- 任何提到"攒批"、"批次大小"的配置

**实际验证：** 代码中目前没有这些配置，说明之前的"批次化"只存在于文档中，代码里根本没实现。这也从侧面印证了——实盘代码是对的，文档写错了。

---

## 六、核心循环伪代码（v1.11 简化版）

### 6.1 事件循环（零延迟，来一个处理一个）

```python
# ============================================================
#  v1.11 核心循环伪代码（零延迟事件驱动 + 向量化计算）
# ============================================================

# --- 初始化 ---
formula_engine = PythonFormulaEngine()
formula_engine.set_data_provider(data_provider)

# 编译期：注册所有公式
for fdef in formula_registry.values():
    formula_engine.register_formula(fdef)

formula_engine.start()

# 编译期：注册所有定时器
for eid, timer_spec in edge_timer_specs.items():
    register_timer(eid, timer_spec)

# --- 事件循环 ---
running = True
paused = False

while running:
    # 1. 等待事件（控制事件 > 定时器事件 > 数据事件）
    event = wait_for_event(
        priority_order=["control", "timer", "data"],
        # 注意：没有 batch_config！数据事件来了就处理，不攒批
    )
    
    # 2. 控制事件（最高优先级）
    if event.type == "control":
        if event.action == "start":
            paused = False
            resume_all_timers()
        elif event.action == "pause":
            paused = True
            pause_all_timers()
        elif event.action == "resume":
            paused = False
            resume_all_timers()
        elif event.action == "stop":
            running = False
            cancel_all_timers()
            formula_engine.stop()
            break
        elif event.action == "config_reload":
            reload_config()
        continue
    
    if paused:
        continue  # 暂停状态下，不处理数据和定时器事件
    
    # 3. 定时器事件（次高优先级，精确到点）
    if event.type == "edge_timer":
        handle_edge_timer_event(event)
        continue
    
    # 4. 数据事件（最低优先级，立即处理，不攒批）
    if event.type == "tick":
        handle_single_tick(event.tick_data)
        continue
```

### 6.2 单个 tick 处理（零延迟，完整流程）

```python
def handle_single_tick(tick_data):
    """处理单个 tick 数据（零延迟，来一个处理一个）
    
    完整流程：数据更新 → 指标计算（向量化）→ 比较判断
              → 集合运算 → propagate → 发事件 → 后处理 → 清脏
    """
    
    # === 阶段 1：数据更新 ===
    # 单线程事件循环天然提供版本屏障，计算阶段数据不会变
    
    # 1.1 更新 latest_tick（唯一真相源）
    dirty_stocks.clear()
    for code, new_bar in tick_data.items():
        latest_tick[code] = new_bar
        dirty_stocks.add(code)
    
    # 1.2 更新 node_data_dirty（每个包含这些股票的节点）
    for code in dirty_stocks:
        for nid in code_nodes[code]:
            if nid not in node_data_dirty:
                node_data_dirty[nid] = set()
            node_data_dirty[nid].add(code)
            mark_node_dirty(nid)  # 标记节点为脏
    
    # 1.3 通知公式引擎（数据更新了，缓存可能失效）
    formula_engine.on_data_updated(dirty_stocks)
    
    # === 阶段 2：TTL 过期检查 ===
    handle_ttl_expiry(time.time())
    
    # === 阶段 3：按拓扑序处理脏节点（计算阶段） ===
    # 单线程事件循环保证：计算过程中不会有新数据进来
    # 所以所有读取都是一致的
    
    # 收集所有需要处理的脏节点
    dirty_nodes = collect_dirty_nodes()
    
    # 按拓扑序处理（从上到下）
    for nid in topological_sort(dirty_nodes):
        process_node(nid)
    
    # === 阶段 4：后处理（PK排名/分析角度/看盘面板/预警） ===
    post_process()
    
    # === 阶段 5：清脏 ===
    # 5.1 清理 dirty_stocks
    dirty_stocks.clear()
    
    # 5.2 清理各节点的 data_dirty
    for nid in node_data_dirty:
        node_data_dirty[nid].clear()
    
    # 5.3 清理脏节点标记
    clear_all_dirty()
    
    # 注意：node_stock_change 不清理
    # 因为下一轮可能还需要知道本轮的变化（比如级联传播）
    # 下一轮 propagate 时会自动叠加
```

### 6.3 节点处理（增量计算）

```python
def process_node(nid):
    """处理一个脏节点的所有出边（增量处理）"""
    # 读取该节点的变化情况
    if nid not in node_stock_change:
        node_stock_change[nid] = {"entered": set(), "exited": set()}
    change = node_stock_change[nid]
    entered = change["entered"]
    exited = change["exited"]
    data_dirty = node_data_dirty.get(nid, set())
    
    # 没有变化？跳过
    if not entered and not exited and not data_dirty:
        return
    
    source_codes = set(node_stocks[nid])
    
    # 遍历该节点的所有出边（按顺序）
    for eid in sorted(out_edges[nid], key=edge_order):
        process_edge(eid, nid, source_codes, entered, exited, data_dirty)
    
    # 处理完该节点，重置 stock_change（注意：目标节点的变化会在 propagate 中设置）
    node_stock_change[nid] = {"entered": set(), "exited": set()}
    if nid in node_data_dirty:
        node_data_dirty[nid].clear()
```

### 6.4 边处理（三层 filter + propagate，三态完整传播）

```python
def process_edge(eid, sid, source_codes, entered, exited, data_dirty):
    """处理一条边：三层 filter + propagate（全增量）
    三态（True/False/None）完整传播
    """
    edge = edges[eid]
    tid = edge.target_id
    filter_type = edge_filter_type[eid]
    
    # 检查时间条件（如果该边有时间触发）
    if edge_has_timing(eid):
        if not edge_timing_should_fire_now(eid):
            return  # 时间没到，跳过
    
    # === 第一层：指标计算（调用公式引擎，向量化，CPU 密集） ===
    indicator_ids = edge_indicator_refs[eid]
    period = edge_period(eid)
    args_map = edge_args_map(eid)
    
    # 需要重算指标的股票：新入池的 + 数据变了的
    codes_to_eval = entered | data_dirty
    codes_to_eval &= source_codes  # 确保在源节点里
    
    if codes_to_eval:
        # 公式引擎向量化计算（CPU 密集，底层 C 实现，快）
        # 只重算需要的，其他读缓存
        # 注意：这是"空间批量"——一批股票一起算
        #      不是"时间批量"——攒多个 tick 再算
        indicator_values = formula_engine.eval_indicators(
            formula_ids=indicator_ids,
            codes=list(codes_to_eval),
            period=period,
            args_map=args_map,
        )
    else:
        indicator_values = {}
    
    # === 第二层：比较判断（独立型 filter 才有，轻量） ===
    compare_spec = edge_compare_spec.get(eid)
    
    if compare_spec is not None and filter_type == 'independent':
        if eid not in edge_compare_results:
            edge_compare_results[eid] = {}
        
        # 1. 新入池的：先初始化为 None（数据不足，保守估计）
        for code in entered:
            edge_compare_results[eid][code] = None
        
        # 2. 有指标值的：计算比较结果（覆盖 None）
        #    （entered 和 data_dirty 里有指标值的都算）
        codes_with_indicator = set()
        for ind_id in indicator_ids:
            codes_with_indicator |= set(indicator_values.get(ind_id, {}).keys())
        
        codes_to_compare = (entered | data_dirty) & codes_with_indicator & source_codes
        
        for code in codes_to_compare:
            result = do_compare(indicator_values, code, compare_spec)
            edge_compare_results[eid][code] = result
        
        # 3. 出池的：清理比较结果
        for code in exited:
            edge_compare_results[eid].pop(code, None)
    
    # === 第三层：集合运算（轻量） ===
    if filter_type == 'independent':
        # 独立型：基于比较结果，增量更新集合
        if eid not in edge_filter_results:
            edge_filter_results[eid] = set()
        
        filter_set = edge_filter_results[eid]
        
        # 处理变化的股票（entered + data_dirty + exited）
        changed_codes = entered | data_dirty | exited
        
        for code in changed_codes:
            if code in exited:
                # 出池的：从集合移除
                filter_set.discard(code)
            elif code in source_codes:
                # 在池里的：看比较结果
                # 三态处理：只有 True 的才加入，False 和 None 都不加入
                cmp_result = edge_compare_results[eid].get(code, None)
                if cmp_result is True:
                    filter_set.add(code)
                else:
                    filter_set.discard(code)
    
    else:
        # 排名型：只要有变化就全量重排
        if entered or exited or data_dirty:
            # 注意：排名需要所有股票的指标值
            # 所以调用 eval_indicators 传全部 source_codes
            # 这也是"空间批量"——所有股票一起算
            all_indicators = formula_engine.eval_indicators(
                formula_ids=indicator_ids,
                codes=list(source_codes),
                period=period,
                args_map=args_map,
            )
            # 三态处理：数据不足（None）的股票不参与排名
            # 返回有序列表（按排名从高到低），只包含有有效数据的股票
            ranked = do_rank_filter(all_indicators, source_codes, edge_set_op_spec[eid])
            edge_filter_results[eid] = ranked
    
    # === propagate（传播到目标节点） ===
    if filter_type == 'independent':
        new_target = edge_filter_results[eid]
    else:
        # 排名型：取前 N 名（如果有数量限制）
        new_target = set(edge_filter_results[eid][:rank_limit(eid)]) \
            if isinstance(edge_filter_results[eid], list) \
            else edge_filter_results[eid]
    
    old_target = set(node_stocks[tid]) if tid in node_stocks else set()
    
    new_entered = new_target - old_target
    new_exited = old_target - new_target
    
    if new_entered or new_exited:
        # 更新目标节点股票列表
        node_stocks[tid] = list(new_target)
        
        # 记录到目标节点的 stock_change
        if tid not in node_stock_change:
            node_stock_change[tid] = {"entered": set(), "exited": set()}
        node_stock_change[tid]["entered"] |= new_entered
        node_stock_change[tid]["exited"] |= new_exited
        
        # 标记目标节点为脏（会在后续拓扑序中处理）
        mark_node_dirty(tid)
        
        # 发射事件（立即发送，不攒批，不缓冲）
        for code in new_entered:
            emit_event("stock_entered", tid, code)
        for code in new_exited:
            emit_event("stock_exited", tid, code)
```

### 6.5 三态逻辑运算辅助函数（保留，正确的部分）

```python
def tri_state_and(a, b):
    """三态 AND 运算：有 False 则 False，否则有 None 则 None，否则 True"""
    if a is False or b is False:
        return False
    if a is None or b is None:
        return None
    return True

def tri_state_or(a, b):
    """三态 OR 运算：有 True 则 True，否则有 None 则 None，否则 False"""
    if a is True or b is True:
        return True
    if a is None or b is None:
        return None
    return False

def tri_state_not(a):
    """三态 NOT 运算：True→False, False→True, None→None"""
    if a is None:
        return None
    return not a

def tri_state_combine(results, op='AND'):
    """组合多个三态结果（AND/OR）"""
    if not results:
        return None
    result = results[0]
    for r in results[1:]:
        if op == 'AND':
            result = tri_state_and(result, r)
        else:  # OR
            result = tri_state_or(result, r)
        # 短路优化：AND 遇到 False 就不用算了，OR 遇到 True 就不用算了
        if op == 'AND' and result is False:
            break
        if op == 'OR' and result is True:
            break
    return result
```

---

## 七、运行时内存表（v1.11 更新版）

### 7.1 核心运行时表（8张，删除 batch_state）

| 表名 | 类型 | 读时机 | 写时机 | 说明 |
|------|------|--------|--------|------|
| `latest_tick` | Dict[code → bar_dict] | 公式引擎计算时读（通过 DataProvider） | tick 到来时更新 | **唯一真相源**。所有股票的最新tick数据。版本：每只股票自己的 tick 时间戳 |
| `node_stocks` | Dict[nid → List[code]] | propagate 读写、filter 读 | 边执行/TTL过期后写 | 各节点当前股票列表 |
| `ttl_expiry_queue` | Heap[(expire_ts, nid, code)] | TTL检查时弹出 | 股票入池时插入 | TTL 过期队列。按过期时间排序的最小堆 |
| `dirty_stocks` | Set[code] | 通知公式引擎时传、比较层增量时用 | tick 开始时收集 / tick 结束时清空 | **股票级水位线**。本 tick 数据更新了的股票集合。 |
| `node_stock_change` | Dict[nid → {entered: Set, exited: Set}] | 执行循环读（增量处理） | propagate/TTL/备选池刷新时写入 | 从 bool 升级为 entered/exited 双集合 |
| `node_data_dirty` | Dict[nid → Set[code]] | 执行循环读、比较层增量读 | tick 开始时加入 / tick 结束时清空 | 节点里哪些股票的数据变了。 |
| `edge_compare_results` | Dict[eid → Dict[code → True/False/None]] | 集合运算层读 | 比较层写（增量更新） | **三态比较结果**。None=数据不足。新入池股票先设 None，再计算 |
| `edge_filter_results` | Dict[eid → Set[code] 或 List[code]] | propagate 读 | 集合运算层写 | 排名型用有序列表，独立型用 Set。只放通过的，None/False 都不在 |

**v1.11 相比 v1.10 的变化：**

| v1.10 | v1.11 | 变化原因 |
|------|-------|---------|
| 9 张核心运行时表 | **8 张** | 删除 batch_state，彻底清除时间批次化概念 |
| batch_state 是批次标识 | **无** | 时间批次化是根本性错误，实盘不能接受 |
| 数据事件攒批处理 | **每个 tick 立即处理** | 零延迟事件模型，预警/下单实时触发 |
| 向量化是批次化的手段 | **向量化是空间批量，与时间批次无关** | 澄清两种批量的本质区别 |

**净变化：9张 → 8张（删除 batch_state）。**

### 7.2 公式引擎内部表（黑盒，股票池引擎不直接访问）

| 表名 | 类型 | 说明 |
|------|------|------|
| `formula_registry` | Dict[formula_id → CompiledFormula] | 公式注册表（通过 register_formula 注入） |
| `indicator_results` | Dict[(formula_id, period, args_key) → Dict[code → value 或 None]] | 指标值缓存（第一层状态表）。数据不足时 value 为 None |
| `data_cache` | Dict[(code, period) → DataFrame] | K线数据缓存 |

**这些都是公式引擎内部的事，股票池引擎完全不需要知道。**

**关于向量化：**
- 公式引擎内部用 NumPy/pandas 向量化计算
- 这是"空间批量"——一批股票一起算
- 不是"时间批量"——攒多个 tick 再算
- 向量化是计算层的实现优化，不增加延迟

### 7.3 编译期表（不变，但删除 batch_config）

编译期产物相比 v1.10 少了 batch_config：

| 编译产物 | 类型 | 说明 |
|----------|------|------|
| `formula_registry` | Dict[indicator_id → formula_spec] | 公式注册表。所有去重后的指标，编译期一次性收集 |
| `comparison_operators` | Dict[op_id → operator_spec] | 比较算子集。所有可用的比较算子，独立于指标 |
| `edge_indicator_refs` | Dict[eid → List[indicator_id]] | 每条边引用的指标ID列表 |
| `edge_compare_spec` | Dict[eid → compare_spec] | 比较层规格（用哪个算子、参数是什么） |
| `edge_set_op_spec` | Dict[eid → set_op_spec] | 集合运算层规格（AND/OR/NOT/排名逻辑，支持三态运算） |
| `edge_filter_type` | Dict[eid → 'independent' / 'global'] | filter 类型：单股独立型 / 全局依赖型 |
| `edge_timer_specs` | Dict[eid → timer_spec] | 每条边的定时器配置（编译期分析时间触发条件） |

**注意：没有 batch_config！时间批次化概念彻底删除。**

---

## 八、功能-表操作对应表（v1.11 更新版）

### 8.1 事件循环层（零延迟，来一个处理一个）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **事件分发** | 事件队列 | — | 按优先级：控制 > 定时器 > 数据。**数据事件立即处理，不攒批** |
| **控制事件处理** | — | 运行状态 + 定时器状态 | start/pause/resume/stop/config_reload |
| **定时器事件处理** | `edge_filter_results` + `node_stocks` | `node_stocks` + `node_stock_change` | 时间到了就执行，复用缓存结果 |
| **数据事件处理** | `latest_tick` + 脏标记 | 各层状态表 + 事件队列 | **零延迟**：tick来了立即处理，完整流程一次走完 |

### 8.2 数据层（分层一致性，每层有自己的边界）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **tick 数据更新** | 数据队列 | `latest_tick` + `dirty_stocks` + `node_data_dirty` | tick 到来时立即更新，收集脏股票 |
| **通知公式引擎** | `dirty_stocks` | 公式引擎内部失效标记 | `formula_engine.on_data_updated(dirty_codes)` |
| **周期K线确认** | K线合成器 | `formula_engine.on_period_tick(period, codes)` | 不同周期的K线确认通知 |
| **指标计算（向量化）** | 公式引擎接口 `eval_indicators` | 公式引擎内部 `indicator_results` | **空间批量**：一批股票一起算，NumPy/pandas 底层 C 实现，比循环快 10-100 倍 |
| **数据层版本** | `latest_tick[code].ts` | — | 每只股票有自己的 tick 时间戳，不是全局版本号 |

### 8.3 TTL 淘汰层

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| 股票入池记录 TTL | `edge_ttl_spec[eid]` | `ttl_expiry_queue` 插入 | `expire_ts = current_ts + ttl_sec` |
| TTL 过期检查 | `ttl_expiry_queue + current_ts` | 弹出过期项 | 最小堆：堆顶过期就弹出 |
| 过期股票移除 | `node_stocks[nid]` | `node_stocks[nid]` | 从节点移除 |
| **过期触发级联** | — | `node_stock_change[nid].exited.add(code)` | 加入 exited 集合，不是设 bool |

### 8.4 边触发判定层（节点脏驱动 + 时间触发）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **数据更新 → 节点数据脏** | `code_nodes[code]` | `node_data_dirty[nid].add(code)` + `mark_node_dirty(nid)` | tick 开始时立即更新 |
| **节点股票变化 → entered/exited** | `node_stocks` 新旧对比 | `node_stock_change[nid].entered/exited` | 双集合，不是 bool |
| **边时间触发检查** | `edge_timer_specs[eid]` + 定时器 | `edge_timer_event` | 独立定时器事件源，精确到点 |
| **三要素检查** | `node_stock_change[nid]` + `node_data_dirty[nid]` + 时间条件 | — | 时间条件 AND (有entered/exited OR 有data_dirty) |

### 8.5 边执行层（三层 filter + 增量处理 + propagate + 三态）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **filter 类型判断** | `edge_filter_type[eid]` | — | independent / global |
| **第一层：指标计算** | `formula_registry` + 公式引擎接口 | 公式引擎内部 `indicator_results` | 增量：只重算 entered ∪ data_dirty 的。**空间批量**：向量化计算，一批股票一起算 |
| **第二层：比较判断** | 指标值 + `comparison_operators` + `edge_compare_spec[eid]` | `edge_compare_results[eid]` | entered 先设 None，再计算。三态：True/False/None |
| **第三层：集合运算（独立型）** | `edge_compare_results[eid]` + `edge_set_op_spec[eid]` | `edge_filter_results[eid]` | 三态逻辑运算（AND/OR/NOT）。只有 True 的入集合，None/False 不入 |
| **第三层：集合运算（排名型）** | 指标值 + `edge_set_op_spec[eid]` | `edge_filter_results[eid]`（有序列表） | 数据不足的股票不参与排名 |
| **propagate** | `edge_filter_results[eid]` + `node_stocks[tid]` | `node_stocks[tid]` + `node_stock_change[tid]` | 计算 entered/exited，写入双集合。None 的股票不入池（保守策略） |
| **入池初始化** | `node_stock_change[nid].entered` | 各状态表新增条目 | 比较结果先设 None，再根据数据计算 |
| **出池清理** | `node_stock_change[nid].exited` | 各状态表删除条目 | 出池股票的所有状态清理 |

### 8.6 事件层（立即发送，不缓冲）

| 功能 | 读什么表 | 写什么表 | 计算 | 时机 |
|------|---------|---------|------|------|
| 入池事件 | `node_stock_change[tid].entered` | `event_queue` | 直接读 entered 集合 | **propagate 时立即发射，不攒批** |
| 出池事件 | `node_stock_change[tid].exited` | `event_queue` | 直接读 exited 集合 | **propagate 时立即发射，不攒批** |
| 预警事件 | `alert_rules + node_stock_change` | `alert_queue` | 规则匹配 + cooldown 检查 | **post_tick 时立即发送，不缓冲** |
| 交易信号 | `node_role[tid] == 'target' + entered/exited` | `signal_queue` | 角色判定 + 信号生成 | **propagate 时立即生成，延迟就是金钱** |

### 8.7 后处理层（PK排名/分析角度/看盘面板）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| PK排名 | `node_stocks[target] + pk_config` | `_pk_rankings` | 按权重评分排序，指标值通过公式引擎接口 |
| 分析角度 | `node_stocks[target] + analysis_config` | `_angle_results` | 多维度计算，指标值通过公式引擎接口 |
| 看盘面板 | `node_stocks + dashboard_schema` | `_dashboard_data` | 组装显示数据，指标值通过公式引擎接口 |

---

## 九、概念变化对照表（v1.10 → v1.11）

### 9.1 纠正的根本性错误

| v1.10 概念 | v1.11 纠正 | 理由 |
|-----------|-----------|------|
| 时间维度攒批（批次化）是性能优化 | **彻底删除时间批次化概念，实盘零延迟** | 时间批量=增加延迟=实盘不能接受，这是回测思维，大错特错 |
| 批次化是性能优化手段 | **向量化才是性能优化手段，而且是空间批量** | 向量化（空间批量）不增延迟反而更快；攒批（时间批量）增加延迟实盘不行 |
| 数据事件攒批处理 | **每个 tick 立即处理** | 预警、通知、下单必须实时触发，延迟就是金钱 |
| batch_state 运行时表 | **删除** | 时间批次化概念不存在了 |
| batch_config 编译期配置 | **删除** | 时间批次化概念不存在了 |
| 向量化是批次化的一部分 | **向量化是独立的计算层优化，空间批量** | 澄清两种批量的本质区别，维度完全不同 |

### 9.2 保留并强化的正确概念

| v1.10 保留的概念 | v1.11 强化说明 |
|----------------|---------------|
| 事件驱动 + 状态机 | ✅ **强化**：这才是架构核心，零延迟，每个事件立即处理 |
| 单线程事件循环 | ✅ **强化**：天然的版本屏障，一致性保证 |
| 三态（True/False/None） | ✅ 保留：完整传播路径，AND/OR/NOT 运算规则 |
| 分层一致性边界 | ✅ 保留：每层有自己的版本/一致性边界 |
| 节点脏驱动 | ✅ 保留：entered/exited/data_dirty 三种脏来源 |
| 三层 filter 结构 | ✅ 保留：指标→比较→集合运算 |
| 公式引擎向量化计算 | ✅ **强化**：明确这是"空间批量"，不是"时间批量" |
| 保守入池策略 | ✅ 保留：数据不足（None）的股票不入池 |
| 比较层 entered 先设 None | ✅ 保留：修复的 bug，保守估计不瞎判 |

### 9.3 新概念：空间批量 vs 时间批量

| v1.11 新概念 | 说明 | 为什么加 |
|-------------|------|-----------|
| **空间批量（向量化）** | 一个 tick 内，所有股票的指标一起用向量化算 | 澄清：这才是正确的性能优化方式，不增加延迟 |
| **时间批量（攒批）** | 攒多个 tick 再处理 | 澄清：这是错误的，实盘不能接受，增加延迟 |
| **零延迟事件模型** | 每个 tick 立即处理，事件立即发出 | 实盘的正确模型，延迟就是金钱 |
| **计算层优化 vs 架构层设计** | 向量化是计算层实现，事件模型是架构层设计 | 之前把两者混为一谈，现在明确分层 |

### 9.4 概念数量统计

| 版本 | 核心运行时表数 | 变化 |
|------|---------------|------|
| v1.5 | 8 张 | — |
| v1.6 | 7 张 | -1 |
| v1.6.1 | 5 张 | -2 |
| v1.7 | 5 张 | 0（质量升级） |
| v1.8 | 8 张 | +3（状态显式化） |
| v1.9 | 9 张 | +1（+batch_state） |
| v1.10 | 9 张 | 0（语义澄清 + bug 修复） |
| **v1.11** | **8 张** | **-1（删除 batch_state，纠正根本性错误）** |

**v1.11 核心运行时表（8张，删除 batch_state）：**
1. `latest_tick`（唯一真相源，每只股票自己的 tick 时间戳）
2. `node_stocks`（节点股票）
3. `ttl_expiry_queue`（TTL队列）
4. `dirty_stocks`（股票级水位线）
5. `node_stock_change`（entered/exited 双集合）
6. `node_data_dirty`（节点内数据脏的股票）
7. `edge_compare_results`（三态比较结果，entered 先设 None）
8. `edge_filter_results`（集合/有序列表，只放通过的）

**少了 batch_state——时间批次化概念彻底删除。**

---

## 十、实现路线图（v1.11）

### 阶段一：文档纠正 + 概念澄清（P0）

1. **更新所有架构文档**
   - 彻底删除"批次化"、"攒批"等时间批量概念
   - 明确区分空间批量（向量化）vs 时间批量（攒批）
   - 建立零延迟事件模型的表述
   - 所有文档使用 v1.11 的正确概念

2. **代码验证与确认**
   - 确认实盘代码中确实没有时间批次化逻辑（已验证：确实没有）
   - 确认向量化计算确实存在（公式引擎的 NumPy/pandas）
   - 确认事件确实是立即发送的，没有缓冲

3. **概念培训与对齐**
   - 团队内对齐：空间批量 ≠ 时间批量
   - 明确：实盘零延迟，预警/下单实时触发
   - 避免以后再出现"攒批优化"之类的错误想法

### 阶段二：清理残留概念（P1）

1. **删除代码中残留的批次化相关代码**
   - 搜索所有 "batch"、"批次" 相关的代码
   - 确认哪些是空间批量（保留），哪些是时间批量（删除）
   - 清理无用的变量、函数、配置

2. **重命名容易混淆的变量**
   - 如果有变量名容易让人误解为"时间批次"，重命名
   - 比如：`batch_calc` → `vectorized_calc`（向量化计算）
   - 比如：`batch_update` → `bulk_update`（批量更新，但不是时间批量）

3. **注释与文档字符串更新**
   - 更新所有注释，使用正确的概念
   - 避免使用"批次"等容易混淆的词
   - 用"向量化"、"空间批量"、"全量计算"等准确表述

### 阶段三：性能优化（空间批量方向）（P1）

1. **强化向量化计算**
   - 检查公式引擎中是否还有 Python 循环可以向量化
   - 确保所有序列运算都是向量化的（rolling、shift、ema 等）
   - 性能基准测试，确认毫秒级

2. **增量 vs 全量的权衡**
   - 评估：节点级增量 + 计算级全量向量化，是否最优
   - 如果某些场景增量计算更优，可以保留增量
   - 但要明确：增量是"计算优化"，不是"时间批次"

3. **缓存策略优化**
   - 公式引擎的指标缓存策略
   - 数据缓存（K线数据）的 TTL 策略
   - 确保缓存命中率高，同时不脏

### 阶段四：正确性验证（P0）

1. **三态传播验证**
   - 三态逻辑运算的单元测试（覆盖所有组合）
   - 比较层 entered 先设 None 的回归测试
   - 排名型数据不足不参与排名的测试

2. **事件实时性验证**
   - 确认事件立即发送，没有攒批
   - 确认预警、下单信号的延迟在可接受范围内
   - 压力测试：高频 tick 下，延迟是否稳定

3. **一致性验证**
   - 单线程事件循环的一致性保证
   - 分层一致性边界的正确性
   - 脏标记、增量计算的正确性

---

## 十一、统计总结（v1.10 → v1.11）

### 11.1 概念数量变化

| 统计项 | v1.10 | v1.11 | 变化 |
|--------|------|-------|------|
| 核心运行时表 | 9 张 | **8 张** | **-1（删除 batch_state）** |
| filter 层数 | 3 层 | 3 层 | 不变 |
| 公式引擎接口数 | 11 个 | 11 个 | 不变，但明确是空间批量（向量化） |
| 脏来源种类 | 2 种（stock_change + data_change） | 2 种 | 不变，stock_change 是 entered/exited 双集合 |
| 显式状态表层数 | 3 层 | 3 层 | 不变，三态完整传播 |
| 事件源数量 | 3 种（数据 + 定时器 + 控制） | 3 种 | 不变，但**数据事件不攒批，零延迟** |
| 一致性保证 | 单线程事件循环天然一致 | 单线程事件循环天然一致 | 不变，之前的批次化是多余的概念 |
| 三态传播范围 | 全链路 | 全链路 | 不变 |
| **事件延迟模型** | 数据事件攒批，有延迟 | **零延迟，立即处理** | **根本性纠正** |
| **性能优化方式** | 批次化（时间批量） | **向量化（空间批量）** | **根本性纠正** |

### 11.2 为什么是 v1.11？

**v1.11 是纠正根本性错误的版本——把"时间维度攒批"这个回测思维从实盘架构里彻底拿掉。**

```
演进路径：
  v1.5 ~ v1.6：概念精简阶段（从多到少，先做对）
  v1.7：性能优化阶段（股票级水位线，增量计算）
  v1.8：状态显式化阶段（三层状态表，每层结果都有表）
  v1.9：架构完善阶段（一致性 + 真事件驱动 + 生命周期）
  v1.10：深度澄清阶段（并发性能 + 批次定位 + 三态传播）
  v1.11：根本性纠错（删除时间批次化，零延迟事件模型，空间批量≠时间批量）
  v2.0：完整稳定版（所有功能完善，文档齐全）
```

**v1.11 解决的是"方向对错"的问题：**
1. **纠正根本性错误**：时间维度攒批=增加延迟=实盘不能接受，这是回测思维
2. **澄清概念混淆**：空间批量（向量化，不增延迟）vs 时间批量（攒批，增延迟）
3. **建立正确模型**：零延迟事件模型，每个tick立即处理，事件立即发出
4. **修正性能认知**：向量化是计算层实现优化，不是架构层攒批
5. **净化架构概念**：删除所有批次相关的表和配置，回归事件驱动本质

这些都是"方向级"的纠正，比加功能更重要——方向错了，越努力越偏。

### 11.3 一句话总结

**v1.11 纠正"时间维度攒批"的根本性错误（那是回测思维，实盘不能接受）；明确空间批量（向量化，不增延迟反而更快）与时间批量（攒批，增加延迟实盘不行）的本质区别；建立实盘零延迟事件模型（每个tick立即处理，预警/通知/下单实时触发，延迟就是金钱）；修正性能模型（向量化是计算层实现优化，与时间批次化无关）；删除所有批次相关的表和配置（batch_state、batch_config 全删）；保留并强化正确的部分（事件驱动、三态、分层、脏标记、向量化计算）。**
