# 股票池深度重构规划 v1.9

> 版本主题：并发一致性 + 时间驱动独立化 + 真事件驱动
> 设计原则：表驱动、数据驱动、事件驱动、增量优先、全量保底、概念最简
> 目标：建立单线程事件循环 + 批次化版本屏障保证一致性；node_stock_change 结构化（entered/exited 集合）；公式引擎生命周期接口补全（register/invalidate/on_period_tick）；时间驱动独立化为定时器事件源；状态表三态化与有序化；真正的事件驱动架构

---

## v1.8 → v1.9 变更摘要表

**变更日期：** 2026-07-01

| # | 变更项 | v1.8 | v1.9 | 本质变化 |
|---|--------|------|------|---------|
| 1 | **并发模型（核心升级）** | 隐式循环，无批次概念，数据更新与计算可能交错 | **单线程事件循环 + 批次化 + 版本屏障** | 从"可能不一致"到"严格一致性"——每个批次计算基于同一版本的快照，不会读到一半数据变了 |
| 2 | **node_stock_change 结构化** | `node_stock_change[nid]: bool`（只知道变了，不知道哪些变了） | `node_stock_change[nid]: {entered: Set, exited: Set}` | 从"全量重算"到"真正增量处理"——入池初始化、出池清理，各做各的 |
| 3 | **公式引擎生命周期补全** | 6个接口（set_data_provider / eval_* / on_data_updated / get_cache_stats / invalidate_all） | **9个接口**：+register_formula + invalidate_formula + invalidate_formula_for_codes + on_period_tick | 从"求值工具"到"完整生命周期组件"——注册/失效/周期切换，全流程有接口 |
| 4 | **时间驱动独立化（核心升级）** | 数据更新时顺便检查时间触发（轮询换皮） | **独立定时器事件源**：每条时间触发的边注册定时器，时间到了发 edge_timer_event | 从"伪事件驱动"到"真事件驱动"——时间是独立事件源，不依赖数据更新 |
| 5 | **状态表完善** | edge_compare_results 二态（True/False），edge_filter_results 用 Set | **三态比较**（True/False/None）+ **排名型有序列表** | 从"二选一"到"三态+有序"——数据不足时不瞎判，排名结果有序可查 |
| 6 | **事件驱动架构** | 隐式循环，事件类型单一（只有数据事件） | **统一事件循环**：数据事件 + 定时器事件 + 控制事件（启动/暂停/停止） | 从"数据驱动循环"到"真正事件循环"——所有变化都是事件，统一调度 |
| 7 | **核心运行时表数** | 8 张 | **9 张**（+1 张 batch_state，node_stock_change 升级） | 增加批次状态表，保证一致性 |

**一句话总结 v1.9 升级：** 建立单线程事件循环 + 批次化版本屏障，从根本上解决并发一致性问题；node_stock_change 从 bool 升级为 entered/exited 双集合，实现真正的增量处理；公式引擎补全生命周期接口（register_formula / invalidate_formula / on_period_tick）；时间驱动独立化为定时器事件源，配合统一事件循环，实现真正的事件驱动架构；状态表三态化与有序化，语义更准确。

---

## 一、核心升级一：并发模型与一致性保证

### 1.1 问题：计算过程中数据更新了怎么办？

**v1.8 的问题：tick 数据随时可能来，计算过程中数据又更新了，读到一半数据变了，结果不一致。**

```
v1.8 的隐式问题（没明说，但实际存在）：

  线程 A：正在计算节点 N 的 filter
    - 读了股票 A 的数据（10:00:00 的）
    - 读了股票 B 的数据
    - ...
  线程 B：收到新 tick，更新了股票 A 的数据（10:00:01 的）
  
  结果：节点 N 的计算结果基于"混合版本"的数据
    - 股票 A 是新版本
    - 股票 B 是旧版本
    - 结果可能不正确，而且难以复现
  
  更糟糕的是：
    - 如果是排名型 filter，排名是相对的，混合版本导致排名完全错误
    - 如果是逻辑组合 AND，A 新 B 旧，可能漏掉本应通过的股票
```

**为什么会有这个问题？**
- 股票池是多线程环境吗？不一定，但即使是单线程的 async 环境，也有类似问题
- 数据更新可能在任何时刻发生（通过回调、队列等）
- 计算过程如果不是原子的，就可能读到不一致的数据

### 1.2 方案：批次化 + 版本屏障

**核心思想：数据更新是一批一批的，每个批次有一个版本号，计算基于批次快照，不会读到一半数据变了。**

```
v1.9 的正确做法：批次化 + 版本屏障

  ┌─────────────────────────────────────────────────────────┐
  │                    事件循环（单线程）                      │
  │                                                         │
  │  ┌──────────┐    ┌──────────┐    ┌──────────┐          │
  │  │ 批次 N    │ →  │ 批次 N+1  │ →  │ 批次 N+2  │ → ...   │
  │  │ 计算中    │    │ 计算中    │    │ 计算中    │          │
  │  └──────────┘    └──────────┘    └──────────┘          │
  │       ↑                ↑                ↑               │
  │       │                │                │               │
  │  数据队列：[tick1, tick2, ...]  攒够一批或到时间就处理    │
  └─────────────────────────────────────────────────────────┘

关键保证：
  1. 单线程事件循环：所有计算在一个线程，没有竞态条件
  2. 数据更新通过队列进来：新数据先缓存，不直接改运行时表
  3. 批次化处理：一次处理一批数据，更新一次版本号
  4. 版本屏障：计算过程中，所有读取都基于当前批次的快照
  5. 新数据来了先存着：等当前批次计算完，再处理下一批
```

**为什么不需要复杂的锁？**
- 因为是单线程事件循环，不存在多线程竞态
- 数据更新通过队列串行化，一次处理一批
- 计算过程中不会有数据更新（因为在同一个事件循环里）
- "批次"的概念就够了，不需要读写锁、MVCC 等复杂机制

**为什么批次化够用？**
- 股票池不需要微秒级实时，秒级批次完全够用
- 实际行情推送也是一批一批的（交易所是快照式推送）
- 攒个 100ms~1s 的批次，延迟可接受，性能大幅提升

### 1.3 批次状态表：batch_state

**新增一张核心运行时表：`batch_state`，记录当前批次的版本信息。**

| 属性 | 说明 |
|------|------|
| **结构** | `{batch_version: int, batch_ts: float, dirty_stocks: Set[code], pending_ticks: List[dict]}` |
| **归属** | 股票池引擎维护 |
| **内容** | 当前批次版本号、批次时间戳、本批次脏股票集合、待处理 tick 列表 |
| **生命周期** | 每个 tick 批次更新一次 |
| **更新时机** | 批次开始时：版本号+1，收集脏股票；批次结束时：清理 |
| **失效条件** | 下一个批次开始，当前批次就结束了 |

```python
# 结构示意
batch_state = {
    "batch_version": 12345,           # 单调递增的批次版本号
    "batch_ts": 1719820800.0,         # 本批次的时间戳
    "dirty_stocks": {"000001", "000002"},  # 本批次数据更新了的股票
    "pending_ticks": [...],            # 攒着的 tick，等下一批处理
}
```

### 1.4 单线程事件循环模型

**明确：股票池引擎运行在单线程事件循环中，所有计算在一个线程，数据更新通过队列进来。**

```
事件循环模型（单线程 + 多事件源）：

  ┌──────────────────────────────────────────────────────┐
  │                  事件循环 (Event Loop)                 │
  │                                                      │
  │  ┌────────────┐  ┌────────────┐  ┌────────────┐     │
  │  │  数据事件   │  │ 定时器事件  │  │  控制事件   │     │
  │  │ (tick来)   │  │(时间到了)   │  │(启动/暂停) │     │
  │  └─────┬──────┘  └─────┬──────┘  └─────┬──────┘     │
  │        │                │                │            │
  │        └────────────────┼────────────────┘            │
  │                         │                             │
  │                  ┌──────▼──────┐                      │
  │                  │  事件分发器  │                      │
  │                  └──────┬──────┘                      │
  │                         │                             │
  │                  ┌──────▼──────┐                      │
  │                  │  批次处理器  │                      │
  │                  │  (计算逻辑)  │                      │
  │                  └─────────────┘                      │
  └──────────────────────────────────────────────────────┘

事件源（3 类）：
  1. 数据事件：行情 tick 来了 → 放入数据队列
  2. 定时器事件：时间触发的边 → 到时发 timer_event
  3. 控制事件：启动/暂停/停止/配置变更 → 控制队列

处理规则：
  - 同一时刻只有一个事件在处理（单线程）
  - 数据事件攒批处理（不是来一个处理一个）
  - 定时器事件精确触发（到点就处理）
  - 控制事件高优先级（立即响应）
```

### 1.5 版本屏障的工作原理

**计算过程中，所有读取都基于当前批次的快照，不会读到一半数据变了。**

```
批次处理流程：

  1. 批次开始
     ├─ batch_version += 1
     ├─ 从 pending_ticks 取出所有待处理 tick
     ├─ 更新 latest_tick（一次性更新所有）
     ├─ 收集 dirty_stocks（本批次所有数据更新的股票）
     └─ 更新 node_data_dirty（每个节点的脏股票）

  2. 批次计算（基于当前版本的快照）
     ├─ 处理 TTL 过期
     ├─ 按拓扑序处理脏节点
     │  ├─ 指标计算（公式引擎）
     │  ├─ 比较判断
     │  ├─ 集合运算
     │  └─ propagate
     └─ 发射事件

  3. 批次结束
     ├─ 清理脏标记
     └─ 等待下一批事件

关键保证：
  - 步骤 1 是"数据更新阶段"，集中更新所有数据
  - 步骤 2 是"计算阶段"，只读不写数据（除了状态表）
  - 计算阶段不会有新数据进来（因为在同一个事件循环）
  - 所以计算看到的是一致的快照
```

### 1.6 与现有代码的对比

**现有代码（engine.py）的情况：**

```
现有实现：
  - 有 _event_queue、_loop_running、_loop_task 等事件循环基础设施
  - 有 run_mode / run_loop / stop_loop / pause_loop / resume_loop
  - 但缺少明确的"批次"概念
  - 数据更新可能直接修改 latest_tick，没有版本屏障
  - 时间触发是在数据循环中顺便检查，不是独立事件源

v1.9 升级：
  - 保留单线程事件循环的基本架构
  - 增加批次化 + 版本屏障
  - 数据更新先入队列，批次开始时集中处理
  - 计算阶段基于一致快照
  - 时间触发独立为定时器事件源
```

---

## 二、核心升级二：node_stock_change 结构化

### 2.1 问题：只知道变了，不知道哪些变了

**v1.8 的问题：`node_stock_change[nid]: bool` 只能告诉你"这个节点的股票列表变了"，但不知道哪些新进来了、哪些出去了。**

```
v1.8 的问题：
  node_stock_change = {
      "node_001": True,   # 变了，但哪些变了？不知道
      "node_002": False,
  }

  处理时只能：
    - 要么全量重算所有股票（简单但慢）
    - 要么自己对比新旧 node_stocks 找差集（麻烦且重复）
  
  结果：
    - 入池的股票：不知道哪些是新的，无法做"入池初始化"
    - 出池的股票：不知道哪些走了，无法做"出池清理"
    - 实际上还是全量重算，增量处理是空谈
```

### 2.2 方案：entered / exited 双集合

**改为 `node_stock_change[nid] = {entered: Set[code], exited: Set[code]}`，明确记录哪些股票新进来了、哪些出去了。**

| 属性 | 说明 |
|------|------|
| **结构** | `Dict[nid → {entered: Set[code], exited: Set[code]}]` |
| **归属** | 股票池引擎维护 |
| **内容** | 每个节点本轮新增的股票集合 + 本轮移除的股票集合 |
| **生命周期** | 每个批次更新一次，批次结束后保留到下批次 |
| **更新时机** | 1. propagate 时计算差集并写入<br/>2. TTL 过期时加入 exited<br/>3. 备选池刷新时计算差集 |
| **失效条件** | 下一次该节点股票变化时，被新的 entered/exited 替换 |

```python
# 结构示意
node_stock_change = {
    "node_001": {
        "entered": {"000001", "000003"},   # 本轮新入池的股票
        "exited": {"000002", "000005"},     # 本轮出池的股票
    },
    "node_002": {
        "entered": set(),
        "exited": set(),
    },
    ...
}
```

### 2.3 为什么需要结构化？

**因为入池和出池的处理逻辑完全不同，必须分开。**

```
入池的股票（entered）需要做：
  1. 初始化指标缓存（虽然公式引擎有缓存，但可以预计算）
  2. 初始化比较结果（edge_compare_results 中新增条目）
  3. 初始化 TTL 计时器（如果该边有 TTL）
  4. 触接入池事件（emit stock_entered）
  5. 初始化 tracker 状态（如果有 tracker）

出池的股票（exited）需要做：
  1. 清理比较结果（edge_compare_results 中删除）
  2. 清理过滤结果（edge_filter_results 中删除）
  3. 取消 TTL 计时器（如果有）
  4. 触接出池事件（emit stock_exited）
  5. 清理 tracker 状态（如果有）
  6. 清理公式引擎缓存（可选，懒清理也行）

如果只有一个 bool：
  - 你不知道哪些是入池、哪些是出池
  - 只能全量重算，浪费性能
  - 无法做增量的入池初始化和出池清理
```

### 2.4 增量处理的完整逻辑

**有了 entered/exited，才能真正做到增量处理，而不是全量重算。**

```python
# v1.9 的增量处理逻辑（伪代码）

def process_edge(eid, nid):
    """处理一条边的 filter + propagate"""
    change = node_stock_change.get(nid, {"entered": set(), "exited": set()})
    entered = change["entered"]
    exited = change["exited"]
    data_dirty = node_data_dirty.get(nid, set())
    source_codes = node_stocks[nid]
    
    # === 第一层：指标计算 ===
    # 哪些股票需要重算指标？
    codes_to_eval = set()
    codes_to_eval |= entered           # 新入池的，必须算
    codes_to_eval |= data_dirty        # 数据变了的，需要重算
    # exited 的不用管，后面统一清理
    
    if codes_to_eval:
        indicator_values = formula_engine.eval_indicators(
            formula_ids=edge_indicator_refs[eid],
            codes=list(codes_to_eval),
            period=edge_period(eid),
        )
    # 注意：已经在池里且数据没变的股票，不用重算指标
    # 公式引擎的缓存里有它们的值
    
    # === 第二层：比较判断（独立型） ===
    if edge_filter_type[eid] == 'independent':
        # 1. 新入池的：计算比较结果
        for code in entered:
            result = do_compare(indicator_values, code, compare_spec)
            edge_compare_results[eid][code] = result
        
        # 2. 数据变了的：更新比较结果
        for code in data_dirty:
            if code in source_codes:  # 确保还在池里
                result = do_compare(indicator_values, code, compare_spec)
                edge_compare_results[eid][code] = result
        
        # 3. 出池的：清理比较结果
        for code in exited:
            edge_compare_results[eid].pop(code, None)
    
    # === 第三层：集合运算 ===
    if edge_filter_type[eid] == 'independent':
        # 独立型：增量更新集合
        # 其实不用每次全量算，只要处理变化的部分
        filter_set = edge_filter_results[eid]
        
        # 处理 entered 和 data_dirty 的：可能新通过
        for code in (entered | data_dirty):
            if code in source_codes:
                if edge_compare_results[eid].get(code, False):
                    filter_set.add(code)
                else:
                    filter_set.discard(code)
        
        # 处理 exited 的：从集合移除
        for code in exited:
            filter_set.discard(code)
    else:
        # 排名型：只要有变化就全量重排
        if entered or exited or data_dirty:
            # 注意：排名需要所有股票的指标值
            # 所以需要调用 eval_indicators 传全部 source_codes
            all_indicators = formula_engine.eval_indicators(
                formula_ids=edge_indicator_refs[eid],
                codes=list(source_codes),  # 全量
                period=edge_period(eid),
            )
            filter_set = do_rank_filter(all_indicators, source_codes, spec)
            edge_filter_results[eid] = filter_set  # 有序列表
    
    # === propagate ===
    old_target = set(node_stocks[tid])
    new_target = edge_filter_results[eid]
    
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
        
        # 发射事件
        for code in new_entered:
            emit_event("stock_entered", tid, code)
        for code in new_exited:
            emit_event("stock_exited", tid, code)
```

**关键点：**
- 入池的股票（entered）：初始化指标、比较结果、TTL 等
- 出池的股票（exited）：清理所有相关状态
- 数据变了的股票（data_dirty）：只重算它们的指标和比较
- 其他股票：完全不碰，沿用上次结果
- 这才是真正的增量处理！

---

## 三、核心升级三：公式引擎生命周期接口补全

### 3.1 v1.8 的缺口

**v1.8 的公式引擎有 6 个接口，但缺少生命周期管理接口。**

```
v1.8 的公式引擎接口（6个）：
  1. set_data_provider      — 设置数据提供者
  2. eval_indicator          — 单只单指标求值
  3. eval_indicators         — 批量多指标求值
  4. on_data_updated         — 数据更新通知
  5. get_cache_stats         — 缓存统计
  6. invalidate_all          — 清空所有缓存

缺少什么？
  1. 公式怎么注册进去的？—— 没有 register_formula
  2. 单个公式失效怎么办？—— 只有 invalidate_all，太粗暴
  3. 指定股票的某个公式失效？—— 没有 invalidate_formula_for_codes
  4. K线周期切换怎么通知？—— 没有 on_period_tick
  5. formula_registry 怎么注入？—— 没有明确接口
  6. 公式引擎的启动/关闭生命周期？—— 没有 start/stop
```

### 3.2 v1.9 的完整接口（9个核心 + 2个生命周期）

**补全公式引擎的完整生命周期：注册 → 求值 → 失效 → 周期切换 → 关闭。**

| 接口 | 签名 | 说明 | 谁调用 | 阶段 |
|------|------|------|--------|------|
| **register_formula** | `register_formula(formula_def: dict) -> str` | 注册一个公式，返回 formula_id | 编译期，股票池引擎调用 | 初始化 |
| **set_data_provider** | `set_data_provider(provider: DataProvider) -> None` | 设置数据提供者 | 初始化时 | 初始化 |
| **start** | `start() -> None` | 启动公式引擎（预热缓存等） | 初始化完成后 | 初始化 |
| **eval_indicator** | `eval_indicator(formula_id, code, period, args) -> Any` | 单只股票单指标求值 | 运行期 | 运行期 |
| **eval_indicators** | `eval_indicators(formula_ids, codes, period, args_map) -> dict` | 批量多指标求值 | 运行期 | 运行期 |
| **on_data_updated** | `on_data_updated(dirty_codes: Set[str]) -> None` | 通知数据更新 | 数据更新时 | 运行期 |
| **on_period_tick** | `on_period_tick(period: str, dirty_codes: Set[str]) -> None` | 通知某周期K线确认 | K线确认时 | 运行期 |
| **invalidate_formula** | `invalidate_formula(formula_id: str) -> None` | 使单个公式失效（所有股票） | 公式定义变更时 | 运行期 |
| **invalidate_formula_for_codes** | `invalidate_formula_for_codes(formula_id, codes) -> None` | 使指定股票的指定公式失效 | 精细失效时 | 运行期 |
| **get_cache_stats** | `get_cache_stats() -> dict` | 获取缓存统计 | 调试/监控 | 运行期 |
| **stop** | `stop() -> None` | 停止公式引擎（清理资源） | 关闭时 | 关闭 |

**总共 11 个接口，覆盖完整生命周期。**

### 3.3 新增接口详解

#### 1. register_formula（注册公式）

```python
def register_formula(self, formula_def: dict) -> str:
    """
    注册一个公式，编译并缓存，返回 formula_id。
    
    Args:
        formula_def: 公式定义字典
            {
                "id": "MA5",           # 可选，指定公式ID
                "formula": "MA(CLOSE,5)",  # 公式字符串
                "period": "1d",        # 默认周期
                "args": {},            # 默认参数
                "output_type": "float" # 输出类型: float/bool/...
            }
    
    Returns:
        formula_id: 公式的唯一标识
    """
```

**什么时候调用？**
- 编译期：收集所有边用到的公式，去重后批量注册
- 运行期：动态新增公式（热加载）

#### 2. invalidate_formula（单公式失效）

```python
def invalidate_formula(self, formula_id: str) -> None:
    """
    使单个公式失效（所有股票的该公式结果都失效）。
    
    场景：
      - 公式定义修改了
      - 公式参数修改了
      - 公式依赖的其他公式变了
    
    Args:
        formula_id: 要失效的公式ID
    """
```

#### 3. invalidate_formula_for_codes（指定股票失效）

```python
def invalidate_formula_for_codes(self, formula_id: str, codes: List[str]) -> None:
    """
    使指定股票的指定公式结果失效。
    
    场景：
      - 某只股票的数据有修正（不是新tick，是历史数据修正）
      - 精细控制缓存，避免全量失效
      - 某只股票除权除息，需要重算所有相关指标
    
    Args:
        formula_id: 公式ID
        codes: 股票代码列表
    """
```

#### 4. on_period_tick（周期K线确认）

```python
def on_period_tick(self, period: str, dirty_codes: Set[str]) -> None:
    """
    通知某周期的K线确认了（比如 1分钟K线走完了，5分钟K线走完了）。
    
    与 on_data_updated 的区别：
      - on_data_updated: 任何 tick 数据更新都调用（高频）
      - on_period_tick: 只有当某周期K线确认时才调用（低频，按周期）
    
    用途：
      - 有些指标只在K线确认后才计算（比如日线指标）
      - 避免每个tick都重算日线指标（没必要）
      - 提高性能，减少不必要的计算
    
    Args:
        period: 周期（"1m", "5m", "15m", "1d", ...）
        dirty_codes: 该周期K线确认了的股票集合
    """
```

**为什么需要这个？**
- 不同周期的K线更新频率不同
- 日线指标不需要每个 tick 都重算
- 只有当对应的K线周期确认了，才需要重算
- 这是性能优化的重要手段

#### 5. start / stop（生命周期）

```python
def start(self) -> None:
    """
    启动公式引擎。
    
    做什么：
      - 预热缓存（可选）
      - 启动后台线程（如果有的话）
      - 注册定时器（如果有周期任务）
    """

def stop(self) -> None:
    """
    停止公式引擎。
    
    做什么：
      - 清理所有缓存
      - 停止后台线程
      - 取消所有定时器
      - 释放资源
    """
```

### 3.4 formula_registry 怎么注入？

**公式注册表（formula_registry）是编译期产物，通过 register_formula 批量注入。**

```
注入流程：
  编译期：
    1. 股票池引擎遍历所有边，收集所有用到的公式
    2. 去重（相同公式+参数+周期 = 同一个 formula_id）
    3. 生成 formula_registry: {formula_id: formula_def}
    4. 批量调用 formula_engine.register_formula(def)
    
  运行期：
    1. 股票池引擎通过 formula_id 调用公式引擎
    2. 公式引擎通过 formula_id 查找编译结果
```

```python
# 编译期：注入 formula_registry（伪代码）
def compile_formulas(pool_config):
    """从股票池配置中收集所有公式，去重后注册到公式引擎"""
    # 1. 收集所有边的公式
    formula_defs = []
    for edge in pool_config.edges:
        for ind in edge.indicators:
            formula_defs.append({
                "formula": ind.formula,
                "period": ind.period,
                "args": ind.args,
            })
    
    # 2. 去重
    unique_formulas = deduplicate(formula_defs)
    
    # 3. 批量注册
    formula_ids = []
    for fdef in unique_formulas:
        fid = formula_engine.register_formula(fdef)
        formula_ids.append(fid)
    
    # 4. 建立边 → formula_ids 的映射
    edge_indicator_refs = build_edge_indicator_map(pool_config, formula_ids)
    
    return formula_ids, edge_indicator_refs
```

---

## 四、核心升级四：时间驱动独立化

### 4.1 问题：伪事件驱动，靠数据更新顺便检查时间

**v1.8 的问题：时间触发不是独立的事件源，而是"数据更新时顺便检查一下时间到了没"——这不是真正的事件驱动。**

```
v1.8 的时间触发（伪事件驱动）：

  数据来了 → 处理数据 → 顺便检查时间触发条件 → 时间到了就执行
  
  问题：
    1. 如果一直没有数据更新，时间触发永远不会触发
    2. 时间触发的精度取决于数据更新频率
    3. 逻辑耦合：数据处理和时间处理混在一起
    4. 不是真正的"事件驱动"，是"数据驱动+顺便检查"
  
  举个例子：
    - 某条边设置了"每天 14:30 触发"
    - 如果 14:30 正好没有数据更新
    - 那这条边就不会触发
    - 直到下一个数据来的时候才"迟到"地触发
```

### 4.2 方案：独立定时器事件源

**时间触发是独立的事件源：定时器。每个时间触发的边，注册一个定时器事件。时间到了，发 edge_timer_event。事件循环统一处理所有事件。**

```
v1.9 的时间触发（真事件驱动）：

  ┌─────────────────────────────────────────────────┐
  │              事件循环 (Event Loop)               │
  │                                                 │
  │  ┌───────────┐   ┌───────────┐   ┌───────────┐ │
  │  │ 数据事件   │   │ 定时器事件 │   │ 控制事件   │ │
  │  │  (tick)   │   │ (timer)   │   │ (control) │ │
  │  └─────┬─────┘   └─────┬─────┘   └─────┬─────┘ │
  │        │               │               │       │
  │        └───────────────┼───────────────┘       │
  │                        │                       │
  │               ┌────────▼────────┐              │
  │               │   事件分发器     │              │
  │               └────────┬────────┘              │
  │                        │                       │
  │               ┌────────▼────────┐              │
  │               │   事件处理器     │              │
  │               └─────────────────┘              │
  └─────────────────────────────────────────────────┘

时间触发的工作流程：
  1. 编译期：分析每条边的时间触发配置
  2. 为每条有时间触发的边注册一个定时器
  3. 时间到了，定时器发 edge_timer_event 到事件队列
  4. 事件循环收到 timer_event，处理该边的执行
  5. 执行完后，重新注册下一次定时器（如果是周期性的）
```

**这才是真正的事件驱动：**
- 数据更新是事件
- 时间到了是事件
- 控制命令是事件
- 所有事件都通过事件循环统一调度

### 4.3 定时器事件类型

**定时器事件有多种类型，对应不同的时间触发场景。**

| 定时器类型 | 触发方式 | 对应场景 |
|-----------|---------|---------|
| **一次性定时器** | 指定时间点触发一次 | 每天固定时间触发（如 14:30） |
| **间隔定时器** | 每隔 N 秒触发一次 | 定时刷新（如每 30 秒检查一次） |
| **周期K线定时器** | 每根K线结束时触发 | 1分钟/5分钟/日线K线确认 |
| **日历定时器** | 按日历规则触发 | 每周一、每月第一天等 |

```python
# 定时器事件结构
edge_timer_event = {
    "type": "edge_timer",           # 事件类型
    "eid": "edge_001",              # 边ID
    "timer_type": "once",           # 定时器类型: once/interval/period/calendar
    "trigger_ts": 1719820800.0,     # 触发时间戳
    "scheduled_ts": 1719820800.0,   # 原定触发时间（用于判断是否迟到）
}
```

### 4.4 事件类型大全

**v1.9 的统一事件循环处理三类事件：数据事件、定时器事件、控制事件。**

| 大类 | 事件类型 | 触发源 | 说明 |
|------|---------|--------|------|
| **数据事件** | `tick_batch` | 行情推送 | 一批 tick 数据，批次化处理 |
| **数据事件** | `period_bar_confirm` | K线合成器 | 某周期K线确认了（如 1分钟K线走完） |
| **定时器事件** | `edge_timer` | 定时器 | 某条边的时间触发条件到了 |
| **定时器事件** | `ttl_check` | 定时器 | TTL 过期检查（定期扫描） |
| **控制事件** | `start` | 外部调用 | 启动股票池 |
| **控制事件** | `pause` | 外部调用 | 暂停 |
| **控制事件** | `resume` | 外部调用 | 恢复 |
| **控制事件** | `stop` | 外部调用 | 停止 |
| **控制事件** | `config_reload` | 外部调用 | 配置热重载 |
| **内部事件** | `stock_entered` | propagate | 股票入池（供外部消费） |
| **内部事件** | `stock_exited` | propagate | 股票出池（供外部消费） |

### 4.5 事件循环的处理优先级

**不同类型的事件有不同的处理优先级。**

```
优先级从高到低：
  1. 控制事件（stop/pause/resume）—— 立即响应
  2. 定时器事件 —— 精确到点，尽量不延迟
  3. 数据事件 —— 可以攒批处理
  4. 通知事件（stock_entered/exited）—— 不影响核心逻辑，可以延后

处理策略：
  - 控制事件：最高优先级，收到立即处理
  - 定时器事件：按时间排序，到点就处理
  - 数据事件：攒批处理（攒 100ms 或攒够 N 条）
  - 通知事件：放到事件队列尾部，闲了再处理
```

### 4.6 时间驱动与数据驱动的协作

**时间触发的边，执行时也需要数据——但数据是"当前状态"，不是"触发原因"。**

```
时间驱动的边执行流程：

  1. 定时器时间到 → 发 edge_timer_event
  2. 事件循环收到事件 → 找到对应的边
  3. 检查：源节点有数据吗？
     ├─ 有 → 正常执行 filter + propagate
     └─ 没有 → 跳过（或等数据来了补执行）
  4. 执行完 → 注册下一次定时器

关键点：
  - 时间是"触发源"，数据是"执行依据"
  - 时间到了就执行，不管数据有没有更新
  - 如果数据没更新，沿用上次的计算结果（有缓存）
  - 如果数据更新了，用最新的数据计算
```

**举个例子：**
- 某条边设置"每天 15:00 选股"
- 15:00 定时器触发，执行选股
- 选股用的是 15:00 时的最新数据
- 不管 15:00 有没有数据更新，都会执行

---

## 五、核心升级五：状态表完善

### 5.1 edge_compare_results 三态化

**从二态（True/False）改为三态（True/False/None），None 表示数据不足。**

| 属性 | 说明 |
|------|------|
| **结构** | `Dict[eid → Dict[code → True/False/None]]` |
| **含义** | `True`=通过，`False`=不通过，`None`=数据不足无法判断 |
| **更新时机** | 指标值更新后重算比较结果 |
| **失效条件** | 数据更新、股票入池/出池 |

```python
# 结构示意
edge_compare_results = {
    "edge_001": {
        "000001": True,    # 通过
        "000002": False,   # 不通过
        "000003": None,    # 数据不足（比如新股，K线不够）
        ...
    },
    ...
}
```

**为什么需要三态？**
- 数据不足时，不能简单地算 False
- False 表示"明确不满足条件"，None 表示"不知道满不满足"
- 排名型、截面型 filter 里，数据不足的股票应该排除，而不是当作最后一名
- 逻辑组合里，None 的传播规则不同（比如 AND 里有一个 None，结果可能是 None 或 False）

**三态传播规则（逻辑运算）：**

| 运算 | 规则 | 例子 |
|------|------|------|
| **AND** | 有 False 则 False，否则有 None 则 None，否则 True | True AND None = None<br/>False AND None = False |
| **OR** | 有 True 则 True，否则有 None 则 None，否则 False | True OR None = True<br/>False OR None = None |
| **NOT** | True→False, False→True, None→None | NOT None = None |

### 5.2 edge_filter_results 排名型有序化

**排名型 filter 的结果不用 Set，用有序列表（list），保留排名顺序。**

| 属性 | 说明 |
|------|------|
| **结构** | 独立型：`Set[code]`；排名型：`List[code]`（按排名从高到低） |
| **归属** | 股票池引擎维护 |
| **内容** | 通过 filter 的股票集合（独立型）或排名列表（排名型） |
| **更新时机** | 比较结果更新后（独立型）或指标更新后（排名型） |
| **失效条件** | 数据更新、股票入池/出池 |

```python
# 结构示意
edge_filter_results = {
    # 独立型 filter：用 Set（无序）
    "edge_001": {"000001", "000003", "000005"},
    
    # 排名型 filter：用 List（有序，按排名）
    "edge_002": ["000001", "000003", "000005", ...],  # 第1名、第2名、第3名...
}
```

**为什么排名型要用有序列表？**
- 排名是有顺序的，Set 丢了顺序信息
- 后处理（PK排名、显示等）需要排名顺序
- 如果用 Set，每次都要重新排序，浪费性能
- 有序列表可以直接取前 N 名、取排名区间

**排名型 filter 的操作：**

| 操作 | 方法 | 说明 |
|------|------|------|
| 判断是否通过 | `code in filter_set` | 独立型用 Set，排名型也可以转 Set 判断 |
| 取前 N 名 | `ranked_list[:N]` | 排名型直接切片 |
| 取排名 | `ranked_list.index(code)` | 排名型可查具体名次 |
| 取第 M~N 名 | `ranked_list[M:N]` | 排名型可查区间 |

### 5.3 每张状态表的失效条件

**明确每张状态表在什么情况下需要重算/失效。**

#### 表 1：indicator_results（公式引擎内部）

| 失效条件 | 影响范围 | 触发方式 |
|---------|---------|---------|
| 某只股票数据更新 | 该股票的所有指标 | `on_data_updated(dirty_codes)` |
| 某周期K线确认 | 该周期的所有指标所有股票 | `on_period_tick(period, dirty_codes)` |
| 公式定义变更 | 该公式的所有股票 | `invalidate_formula(formula_id)` |
| 某只股票历史数据修正 | 该股票的指定公式 | `invalidate_formula_for_codes(fid, codes)` |
| 公式参数变更 | 该公式的所有股票 | `invalidate_formula(formula_id)` |

#### 表 2：edge_compare_results

| 失效条件 | 影响范围 | 触发方式 |
|---------|---------|---------|
| 某只股票指标值变了 | 该股票的所有相关边的比较结果 | data_dirty 驱动，增量重算 |
| 股票入池 | 新入池股票需要计算比较结果 | entered 驱动，新增条目 |
| 股票出池 | 出池股票的比较结果需要清理 | exited 驱动，删除条目 |
| 比较算子/参数变更 | 该边所有股票的比较结果 | 编译期事件，全量重算 |
| 公式变更（间接） | 该边所有股票的比较结果 | 通过 indicator_results 传递 |

#### 表 3：edge_filter_results

| 失效条件 | 影响范围 | 触发方式 |
|---------|---------|---------|
| 比较结果变了（独立型） | 变化的股票 → 集合变化 | 增量更新集合 |
| 任何股票指标值变了（排名型） | 全量重排（排名是相对的） | data_dirty 驱动，全量重排 |
| 股票入池/出池 | 集合变化 | entered/exited 驱动，增量更新 |
| 集合运算规则变更 | 该边所有股票 | 编译期事件，全量重算 |
| 排名参数变更（N 改变） | 该边所有股票 | 编译期事件，全量重排 |

---

## 六、真正的事件驱动架构

### 6.1 事件循环总览

**v1.9 的核心是统一事件循环：所有变化都是事件，所有事件通过事件循环统一调度。**

```
事件驱动架构总览：

  ┌──────────────────────────────────────────────────────────────┐
  │                        事件循环 (Event Loop)                  │
  │                                                              │
  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐          │
  │  │  数据事件源  │  │ 定时器事件源 │  │  控制事件源  │          │
  │  │  (行情)     │  │  (时钟)     │  │  (外部调用)  │          │
  │  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘          │
  │         │                │                │                 │
  │         └────────────────┼────────────────┘                 │
  │                          │                                  │
  │                  ┌───────▼───────┐                          │
  │                  │   事件队列     │                          │
  │                  │  (优先级队列)  │                          │
  │                  └───────┬───────┘                          │
  │                          │                                  │
  │                  ┌───────▼───────┐                          │
  │                  │   事件分发器   │                          │
  │                  └───────┬───────┘                          │
  │                          │                                  │
  │          ┌───────────────┼───────────────┐                  │
  │          │               │               │                  │
  │  ┌───────▼──────┐ ┌─────▼──────┐ ┌──────▼───────┐          │
  │  │ 数据事件处理器 │ │定时器处理器 │ │ 控制事件处理器 │          │
  │  └───────┬──────┘ └─────┬──────┘ └──────┬───────┘          │
  │          │               │               │                  │
  │          └───────────────┼───────────────┘                  │
  │                          │                                  │
  │                  ┌───────▼───────┐                          │
  │                  │  批次计算引擎  │                          │
  │                  │ (状态表驱动)  │                          │
  │                  └───────────────┘                          │
  └──────────────────────────────────────────────────────────────┘
```

### 6.2 事件处理流程

**每个事件的处理流程：入队 → 分发 → 处理 → 副作用（事件发射）。**

```
事件处理流水线：

  1. 事件入队
     - 数据事件：tick 到达 → 放入数据队列（攒批）
     - 定时器事件：时间到 → 放入定时器队列
     - 控制事件：外部调用 → 立即放入控制队列
  
  2. 事件分发
     - 控制事件优先：有控制事件先处理控制事件
     - 定时器事件次之：到点的定时器事件处理
     - 数据事件最后：攒够一批后处理
  
  3. 事件处理
     - 数据事件：批次化处理 → 更新状态 → 计算
     - 定时器事件：找到对应边 → 执行 filter + propagate
     - 控制事件：修改运行状态（启动/暂停/停止/重载）
  
  4. 副作用（事件发射）
     - 计算过程中产生的入池/出池事件
     - 放入通知事件队列
     - 外部消费者可以订阅
```

### 6.3 批次计算引擎

**数据事件触发批次计算，这是股票池的核心计算逻辑。**

```
批次计算引擎（数据事件驱动）：

  1. 批次开始
     ├─ batch_version += 1
     ├─ 从数据队列取出所有待处理 tick
     ├─ 批量更新 latest_tick
     ├─ 收集 dirty_stocks（本批次所有数据更新的股票）
     ├─ 更新 node_data_dirty（每个节点的脏股票）
     └─ 通知公式引擎：on_data_updated(dirty_stocks)

  2. TTL 过期检查
     ├─ 扫描 ttl_expiry_queue
     ├─ 弹出所有已过期的 (expire_ts, nid, code)
     ├─ 从 node_stocks[nid] 移除 code
     ├─ 加入 node_stock_change[nid].exited
     └─ 清理相关状态

  3. 按拓扑序处理脏节点
     对每个脏节点 nid（按拓扑深度排序）：
     ├─ 读取 node_stock_change[nid]（entered/exited）
     ├─ 读取 node_data_dirty[nid]（data_dirty）
     ├─ 对该节点的每条出边：
     │   ├─ 检查时间条件（如果有）
     │   ├─ 执行 filter（三层：指标→比较→集合）
     │   ├─ propagate 到目标节点
     │   └─ 发射事件
     └─ 清除该节点的脏标记

  4. 批次结束
     ├─ 清理 dirty_stocks
     ├─ 清理各节点的 data_dirty
     └─ 准备下一批
```

### 6.4 定时器事件处理

**时间触发的边，由定时器事件驱动执行。**

```
定时器事件处理流程：

  1. 定时器触发
     └─ 发 edge_timer_event 到事件队列

  2. 事件循环收到 timer_event
     ├─ 找到对应的边 eid
     ├─ 找到源节点 sid
     ├─ 检查：源节点有股票吗？
     │   ├─ 没有 → 跳过（或等数据）
     │   └─ 有 → 继续执行
     ├─ 检查：需要重算吗？
     │   ├─ 数据没变 → 用缓存的 edge_filter_results
     │   └─ 数据变了 → 重新计算 filter
     ├─ 执行 propagate（如果结果变了）
     └─ 发射事件

  3. 重新注册下一次定时器
     ├─ 如果是周期性触发 → 计算下一次触发时间
     └─ 注册新的定时器
```

**注意：定时器事件执行时，也可以复用状态表中的缓存结果，不一定每次都重算。**

### 6.5 控制事件处理

**控制事件优先级最高，用于管理股票池的运行状态。**

```
控制事件类型及处理：

  1. start 事件
     ├─ 初始化所有运行时表
     ├─ 启动公式引擎（formula_engine.start()）
     ├─ 注册所有定时器
     └─ 进入运行状态

  2. pause 事件
     ├─ 设置暂停标志
     ├─ 暂停所有定时器（可选）
     └─ 不再处理数据和定时器事件

  3. resume 事件
     ├─ 清除暂停标志
     ├─ 恢复所有定时器
     └─ 继续处理事件

  4. stop 事件
     ├─ 设置停止标志
     ├─ 取消所有定时器
     ├─ 停止公式引擎（formula_engine.stop()）
     ├─ 清理所有运行时表
     └─ 退出事件循环

  5. config_reload 事件
     ├─ 重新加载配置
     ├─ 重新编译拓扑
     ├─ 更新公式注册（register/invalidate）
     ├─ 更新定时器注册
     └─ 热切换到新配置（不中断运行）
```

---

## 七、运行时内存表（v1.9 更新版）

### 7.1 核心运行时表（9张）

| 表名 | 类型 | 读时机 | 写时机 | 说明 |
|------|------|--------|--------|------|
| `batch_state` | dict | 批次处理时读版本号 | 批次开始时更新版本 | **v1.9 新增**。批次状态：版本号、批次时间、脏股票集合 |
| `latest_tick` | Dict[code → bar_dict] | 公式引擎计算时读（通过 DataProvider） | 批次开始时批量更新 | **唯一真相源**。所有股票的最新tick数据。 |
| `node_stocks` | Dict[nid → List[code]] | propagate 读写、filter 读 | 边执行/TTL过期后写 | 各节点当前股票列表 |
| `ttl_expiry_queue` | Heap[(expire_ts, nid, code)] | TTL检查时弹出 | 股票入池时插入 | TTL 过期队列。按过期时间排序的最小堆 |
| `dirty_stocks` | Set[code] | 通知公式引擎时传、比较层增量时用 | 批次开始时收集 / 批次结束时清空 | **股票级水位线**。本批次数据更新了的股票集合。 |
| `node_stock_change` | Dict[nid → {entered: Set, exited: Set}] | 执行循环读（增量处理） | propagate/TTL/备选池刷新时写入 | **v1.9 升级**。从 bool 升级为 entered/exited 双集合 |
| `node_data_dirty` | Dict[nid → Set[code]] | 执行循环读、比较层增量读 | 批次开始时加入 / 批次结束时清空 | 节点里哪些股票的数据变了。 |
| `edge_compare_results` | Dict[eid → Dict[code → True/False/None]] | 集合运算层读 | 比较层写（增量更新） | **v1.9 升级**。从二态改为三态，None=数据不足 |
| `edge_filter_results` | Dict[eid → Set[code] 或 List[code]] | propagate 读 | 集合运算层写 | **v1.9 升级**。排名型用有序列表，不是 Set |

**v1.9 相比 v1.8 的变化：**

| v1.8（8张） | v1.9（9张） | 变化原因 |
|-------------|-------------|---------|
| — | **`batch_state`** | **新增**：批次状态表，保证一致性 |
| `latest_tick` | `latest_tick` | 不变，但更新时机改为批次开始时批量更新 |
| `node_stocks` | `node_stocks` | 不变 |
| `ttl_expiry_queue` | `ttl_expiry_queue` | 不变 |
| `dirty_stocks` | `dirty_stocks` | 不变，但是批次级的，不是全局的 |
| `node_stock_change[nid]: bool` | `node_stock_change[nid]: {entered, exited}` | **升级**：从 bool 升级为双集合，支持真正增量处理 |
| `node_data_dirty` | `node_data_dirty` | 不变 |
| `edge_compare_results[eid][code]: bool` | `edge_compare_results[eid][code]: True/False/None` | **升级**：二态 → 三态，数据不足用 None |
| `edge_filter_results[eid]: Set[code]` | `edge_filter_results[eid]: Set 或 List` | **升级**：排名型用有序列表 |

**净变化：8张 → 9张（+1张 batch_state，升级3张表的语义）。**

### 7.2 公式引擎内部表（黑盒，股票池引擎不直接访问）

| 表名 | 类型 | 说明 |
|------|------|------|
| `formula_registry` | Dict[formula_id → CompiledFormula] | 公式注册表（通过 register_formula 注入） |
| `indicator_results` | Dict[(formula_id, period, args_key) → Dict[code → value]] | 指标值缓存（第一层状态表） |
| `data_cache` | Dict[(code, period) → DataFrame] | K线数据缓存 |

**这些都是公式引擎内部的事，股票池引擎完全不需要知道。**

### 7.3 编译期表（不变）

编译期产物和 v1.8 一样，没有变化：

| 编译产物 | 类型 | 说明 |
|----------|------|------|
| `formula_registry` | Dict[indicator_id → formula_spec] | 公式注册表。所有去重后的指标，编译期一次性收集 |
| `comparison_operators` | Dict[op_id → operator_spec] | 比较算子集。所有可用的比较算子，独立于指标 |
| `edge_indicator_refs` | Dict[eid → List[indicator_id]] | 每条边引用的指标ID列表 |
| `edge_compare_spec` | Dict[eid → compare_spec] | 比较层规格（用哪个算子、参数是什么） |
| `edge_set_op_spec` | Dict[eid → set_op_spec] | 集合运算层规格（AND/OR/NOT/排名逻辑） |
| `edge_filter_type` | Dict[eid → 'independent' / 'global'] | filter 类型：单股独立型 / 全局依赖型 |
| `edge_timer_specs` | Dict[eid → timer_spec] | **v1.9 新增**。每条边的定时器配置（编译期分析时间触发条件） |

---

## 八、核心循环伪代码（v1.9 更新版）

### 8.1 事件循环（真正的事件驱动）

```python
# ============================================================
#  v1.9 核心循环伪代码（事件驱动 + 批次化 + 版本屏障）
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
batch_version = 0
running = True
paused = False

while running:
    # 1. 等待事件（控制事件 > 定时器事件 > 数据事件）
    event = wait_for_event(priority_order=["control", "timer", "data"])
    
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
    
    # 4. 数据事件（最低优先级，攒批处理）
    if event.type == "tick_batch":
        handle_tick_batch(event.ticks)
        continue
```

### 8.2 批次处理（数据事件）

```python
def handle_tick_batch(ticks):
    """处理一批 tick 数据（批次化 + 版本屏障）"""
    global batch_version
    
    # === 阶段 1：批次开始（数据更新集中完成） ===
    batch_version += 1
    batch_ts = time.time()
    
    batch_state["batch_version"] = batch_version
    batch_state["batch_ts"] = batch_ts
    
    # 1.1 批量更新 latest_tick
    dirty_stocks.clear()
    for code, new_bar in ticks:
        latest_tick[code] = new_bar
        dirty_stocks.add(code)
    
    # 1.2 更新 node_data_dirty（每个包含这些股票的节点）
    for code in dirty_stocks:
        for nid in code_nodes[code]:
            if nid not in node_data_dirty:
                node_data_dirty[nid] = set()
            node_data_dirty[nid].add(code)
    
    # 1.3 通知公式引擎
    formula_engine.on_data_updated(dirty_stocks)
    
    # === 阶段 2：TTL 过期检查 ===
    handle_ttl_expiry(batch_ts)
    
    # === 阶段 3：按拓扑序处理脏节点（计算阶段） ===
    # 注意：计算阶段不会有新数据进来（单线程事件循环）
    # 所以所有读取都是基于当前批次的一致快照
    
    # 收集所有需要处理的节点（有 stock_change 或 data_dirty 的）
    dirty_nodes = collect_dirty_nodes()
    
    # 按拓扑序处理（从上到下或从下到上，取决于传播方向）
    for nid in topological_sort(dirty_nodes):
        process_node(nid)
    
    # === 阶段 4：批次结束（清理） ===
    # 4.1 清理 dirty_stocks
    dirty_stocks.clear()
    
    # 4.2 清理各节点的 data_dirty
    for nid in node_data_dirty:
        node_data_dirty[nid].clear()
    
    # 4.3 注意：node_stock_change 不清理
    # 因为下一轮可能还需要知道本轮的变化（比如级联传播）
    # 下一轮 propagate 时会自动叠加
```

### 8.3 节点处理（增量计算）

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

### 8.4 边处理（三层 filter + propagate）

```python
def process_edge(eid, sid, source_codes, entered, exited, data_dirty):
    """处理一条边：三层 filter + propagate（全增量）"""
    edge = edges[eid]
    tid = edge.target_id
    filter_type = edge_filter_type[eid]
    
    # 检查时间条件（如果该边有时间触发）
    if edge_has_timing(eid):
        if not edge_timing_should_fire_now(eid):
            return  # 时间没到，跳过
    
    # === 第一层：指标计算（调用公式引擎） ===
    indicator_ids = edge_indicator_refs[eid]
    period = edge_period(eid)
    args_map = edge_args_map(eid)
    
    # 需要重算指标的股票：新入池的 + 数据变了的
    codes_to_eval = entered | data_dirty
    codes_to_eval &= source_codes  # 确保在源节点里
    
    if codes_to_eval:
        # 只重算需要的，其他读缓存
        indicator_values = formula_engine.eval_indicators(
            formula_ids=indicator_ids,
            codes=list(codes_to_eval),
            period=period,
            args_map=args_map,
        )
    else:
        indicator_values = {}
    
    # === 第二层：比较判断（独立型 filter 才有） ===
    compare_spec = edge_compare_spec.get(eid)
    
    if compare_spec is not None and filter_type == 'independent':
        if eid not in edge_compare_results:
            edge_compare_results[eid] = {}
        
        # 1. 新入池的：计算比较结果
        for code in entered:
            if code in indicator_values.get(indicator_ids[0], {}):
                result = do_compare(indicator_values, code, compare_spec)
                edge_compare_results[eid][code] = result
            else:
                # 数据不足，标记为 None
                edge_compare_results[eid][code] = None
        
        # 2. 数据变了的：更新比较结果
        for code in data_dirty:
            if code in source_codes:
                result = do_compare(indicator_values, code, compare_spec)
                edge_compare_results[eid][code] = result
        
        # 3. 出池的：清理比较结果
        for code in exited:
            edge_compare_results[eid].pop(code, None)
    
    # === 第三层：集合运算 ===
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
            all_indicators = formula_engine.eval_indicators(
                formula_ids=indicator_ids,
                codes=list(source_codes),
                period=period,
                args_map=args_map,
            )
            # 返回有序列表（按排名从高到低）
            ranked = do_rank_filter(all_indicators, source_codes, edge_set_op_spec[eid])
            edge_filter_results[eid] = ranked
    
    # === propagate ===
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
        
        # 发射事件
        for code in new_entered:
            emit_event("stock_entered", tid, code)
        for code in new_exited:
            emit_event("stock_exited", tid, code)
```

### 8.5 定时器事件处理

```python
def handle_edge_timer_event(event):
    """处理边的定时器事件（时间驱动）"""
    eid = event.eid
    edge = edges[eid]
    sid = edge.source_id
    tid = edge.target_id
    
    # 源节点有股票吗？
    if sid not in node_stocks or not node_stocks[sid]:
        # 没有数据，跳过（或等数据来了补执行）
        reschedule_timer(eid)
        return
    
    source_codes = set(node_stocks[sid])
    filter_type = edge_filter_type[eid]
    
    # 检查：有数据变化吗？
    # 没有的话可以直接用缓存的 edge_filter_results
    # 有的话需要重新计算（但 timer 事件触发时，数据应该已经更新过了）
    
    # 注意：定时器触发时，数据可能是最新的（数据事件先处理了）
    # 也可能数据没变（定时器周期比数据更新周期短）
    
    # 直接用当前状态计算（或复用缓存）
    # 这里简化为：调用 process_edge，但 entered/exited/data_dirty 都空也能处理吗？
    # 其实 timer 触发时，可能数据没变但时间条件满足了，需要执行
    
    # 对于定时器驱动的边：
    # - 如果数据没变，edge_filter_results 是最新的，可以直接用
    # - 但是否需要重新 propagate？不一定，要看目标节点是不是最新的
    
    # 简化：定时器触发的边，每次都"全量检查"
    # （其实有缓存，性能不会太差）
    
    # 标记源节点为"时间触发脏"，然后走正常处理流程
    # 或者直接调用 process_edge，传入"空变化"
    # 但 process_edge 有 entered/exited/data_dirty 都空就跳过的优化
    
    # 所以定时器驱动的边，处理逻辑略有不同：
    # 即使数据没变，只要时间到了，也要执行
    
    # === 简化版：定时器触发 → 直接用缓存的 filter 结果做 propagate ===
    
    if filter_type == 'independent':
        new_target = edge_filter_results.get(eid, set())
    else:
        new_target = set(edge_filter_results.get(eid, [])[:rank_limit(eid)]) \
            if isinstance(edge_filter_results.get(eid), list) \
            else edge_filter_results.get(eid, set())
    
    old_target = set(node_stocks[tid]) if tid in node_stocks else set()
    
    new_entered = new_target - old_target
    new_exited = old_target - new_target
    
    if new_entered or new_exited:
        node_stocks[tid] = list(new_target)
        if tid not in node_stock_change:
            node_stock_change[tid] = {"entered": set(), "exited": set()}
        node_stock_change[tid]["entered"] |= new_entered
        node_stock_change[tid]["exited"] |= new_exited
        mark_node_dirty(tid)
        
        for code in new_entered:
            emit_event("stock_entered", tid, code)
        for code in new_exited:
            emit_event("stock_exited", tid, code)
    
    # 注册下一次定时器
    reschedule_timer(eid)
```

---

## 九、功能-表操作对应表（v1.9 更新版）

### 9.1 事件循环层（新）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **事件分发** | 事件队列 | — | 按优先级：控制 > 定时器 > 数据 |
| **控制事件处理** | — | 运行状态 + 定时器状态 | start/pause/resume/stop/config_reload |
| **定时器事件处理** | `edge_filter_results` + `node_stocks` | `node_stocks` + `node_stock_change` | 时间到了就执行，复用缓存结果 |
| **数据事件处理** | 数据队列 + `latest_tick` | `batch_state` + `dirty_stocks` + `node_data_dirty` | 批次化处理，版本屏障 |

### 9.2 数据层（批次化 + 版本屏障）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **批次开始** | 数据队列 | `batch_state.batch_version++` + `latest_tick` + `dirty_stocks` | 批量更新数据，收集脏股票 |
| **更新节点数据脏** | `code_nodes[code]` + `dirty_stocks` | `node_data_dirty[nid].add(code)` | 每只股票的数据脏，记录到对应节点 |
| **通知公式引擎** | `dirty_stocks` | 公式引擎内部失效标记 | `formula_engine.on_data_updated(dirty_codes)` |
| **周期K线确认** | K线合成器 | `formula_engine.on_period_tick(period, codes)` | 不同周期的K线确认通知 |
| **指标计算** | 公式引擎接口 `eval_indicators` | 公式引擎内部 `indicator_results` | 只重算 dirty_codes，其他读缓存 |

### 9.3 TTL 淘汰层

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| 股票入池记录 TTL | `edge_ttl_spec[eid]` | `ttl_expiry_queue` 插入 | `expire_ts = batch_ts + ttl_sec` |
| TTL 过期检查 | `ttl_expiry_queue + batch_ts` | 弹出过期项 | 最小堆：堆顶过期就弹出 |
| 过期股票移除 | `node_stocks[nid]` | `node_stocks[nid]` | 从节点移除 |
| **过期触发级联** | — | `node_stock_change[nid].exited.add(code)` | **v1.9 变化**：加入 exited 集合，不是设 bool |

### 9.4 边触发判定层（节点脏驱动 + 时间触发）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **数据更新 → 节点数据脏** | `code_nodes[code]` | `node_data_dirty[nid].add(code)` | 批次开始时批量更新 |
| **节点股票变化 → entered/exited** | `node_stocks` 新旧对比 | `node_stock_change[nid].entered/exited` | **v1.9 变化**：双集合，不是 bool |
| **边时间触发检查** | `edge_timer_specs[eid]` + 定时器 | `edge_timer_event` | **v1.9 变化**：独立定时器事件源，不是顺便检查 |
| **三要素检查** | `node_stock_change[nid]` + `node_data_dirty[nid]` + 时间条件 | — | 时间条件 AND (有entered/exited OR 有data_dirty) |

### 9.5 边执行层（三层 filter + 增量处理 + propagate）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **filter 类型判断** | `edge_filter_type[eid]` | — | independent / global |
| **第一层：指标计算** | `formula_registry` + 公式引擎接口 | 公式引擎内部 `indicator_results` | **增量**：只重算 entered ∪ data_dirty 的 |
| **第二层：比较判断** | 指标值 + `comparison_operators` + `edge_compare_spec[eid]` | `edge_compare_results[eid]` | **v1.9 升级**：三态（True/False/None），增量更新 |
| **第三层：集合运算** | `edge_compare_results[eid]` 或指标值 + `edge_set_op_spec[eid]` | `edge_filter_results[eid]` | **v1.9 升级**：排名型用有序列表，独立型增量更新 |
| **propagate** | `edge_filter_results[eid]` + `node_stocks[tid]` | `node_stocks[tid]` + `node_stock_change[tid]` | **v1.9 升级**：计算 entered/exited，写入双集合 |
| **入池初始化** | `node_stock_change[nid].entered` | 各状态表新增条目 | 新入池股票的指标、比较、TTL初始化 |
| **出池清理** | `node_stock_change[nid].exited` | 各状态表删除条目 | 出池股票的所有状态清理 |

### 9.6 事件层（流式逐条产生）

| 功能 | 读什么表 | 写什么表 | 计算 | 时机 |
|------|---------|---------|------|------|
| 入池事件 | `node_stock_change[tid].entered` | `event_queue` | 直接读 entered 集合 | propagate 时立即发射 |
| 出池事件 | `node_stock_change[tid].exited` | `event_queue` | 直接读 exited 集合 | propagate 时立即发射 |
| 预警事件 | `alert_rules + node_stock_change` | `alert_queue` | 规则匹配 | propagate 后检查 |
| 交易信号 | `node_role[tid] == 'target' + entered/exited` | `signal_queue` | 角色判定 + 信号生成 | propagate 时生成 |

### 9.7 后处理层（PK排名/分析角度/看盘面板）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| PK排名 | `node_stocks[target] + pk_config` | `_pk_rankings` | 按权重评分排序，指标值通过公式引擎接口 |
| 分析角度 | `node_stocks[target] + analysis_config` | `_angle_results` | 多维度计算，指标值通过公式引擎接口 |
| 看盘面板 | `node_stocks + dashboard_schema` | `_dashboard_data` | 组装显示数据，指标值通过公式引擎接口 |

---

## 十、概念变化对照表（v1.8 → v1.9）

### 10.1 消除的概念/表

| v1.8 概念 | v1.9 处理 | 理由 |
|-----------|-----------|------|
| `node_stock_change[nid]: bool` | **升级**为 entered/exited 双集合 | 只知道变了没用，要知道哪些变了才能增量处理 |
| `edge_compare_results[eid][code]: bool` | **升级**为三态（True/False/None） | 数据不足时不能瞎判，None 表示"不知道" |
| `edge_filter_results[eid]: Set[code]` | **升级**：排名型用有序 List | 排名是有序的，Set 丢了顺序信息 |

### 10.2 新增的概念/表

| v1.9 新概念 | 说明 | 为什么加 |
|-----------|------|-----------|
| `batch_state` | 批次状态表（版本号、批次时间、脏股票） | 批次化 + 版本屏障，保证一致性 |
| `batch_version` | 单调递增的批次版本号 | 每个批次一个版本，计算基于快照 |
| `node_stock_change[nid].entered` | 本轮新入池的股票集合 | 入池初始化，真正增量处理 |
| `node_stock_change[nid].exited` | 本轮出池的股票集合 | 出池清理，真正增量处理 |
| `edge_compare_results` 三态 | True/False/None | 数据不足时不瞎判，语义更准确 |
| `edge_filter_results` 有序列表 | 排名型结果保留排名顺序 | 排名是有序的，后处理需要顺序 |
| **事件循环** | 统一事件分发 + 多事件源 | 真正的事件驱动架构 |
| **定时器事件源** | 时间触发是独立事件源 | 不依赖数据更新，时间到了就触发 |
| `register_formula` | 公式注册接口 | 公式引擎生命周期管理 |
| `invalidate_formula` | 单公式失效接口 | 精细控制缓存，不用 invalidate_all |
| `invalidate_formula_for_codes` | 指定股票的公式失效 | 更精细的缓存控制 |
| `on_period_tick` | 周期K线确认通知 | 不同周期的K线更新频率不同，性能优化 |

### 10.3 概念数量统计

| 版本 | 核心运行时表数 | 变化 |
|------|---------------|------|
| v1.5 | 8 张 | — |
| v1.6 | 7 张 | -1 |
| v1.6.1 | 5 张 | -2 |
| v1.7 | 5 张 | 0（质量升级） |
| v1.8 | 8 张 | +3（状态显式化） |
| v1.9 | **9 张** | **+1（+batch_state，升级3张表语义）** |

v1.9 核心运行时表（9张）：
1. `batch_state`（批次状态，版本屏障）← 新增
2. `latest_tick`（唯一真相源）
3. `node_stocks`（节点股票）
4. `ttl_expiry_queue`（TTL队列）
5. `dirty_stocks`（股票级水位线）
6. `node_stock_change`（entered/exited 双集合）← 升级
7. `node_data_dirty`（节点内数据脏的股票）
8. `edge_compare_results`（三态比较结果）← 升级
9. `edge_filter_results`（集合/有序列表）← 升级

### 10.4 保留的正确设计（v1.8 做对的部分，v1.9 继续保留）

| 设计 | 说明 | 状态 |
|------|------|------|
| 指标是纯函数，数据不变则结果不变 | ✅ | 保留，最根本的洞察 |
| filter三层结构（指标→比较→集合运算） | ✅ | **强化**：每层状态表完善 |
| 传播是边的步骤，不是filter层 | ✅ | 保留，职责分离 |
| 公式注册表 + 编译期指标去重 | ✅ | **强化**：明确 register_formula 接口 |
| 比较算子正交化（指标×算子=filter） | ✅ | 保留 |
| 无条件边立即传播 | ✅ | 保留 |
| 公式引擎内部维护指标缓存 | ✅ | **强化**：补全生命周期接口 |
| 股票级水位线 dirty_stocks | ✅ | 保留，批次级 |
| 节点脏驱动 | ✅ | **升级**：entered/exited/data_dirty 三种 |
| 三层状态表 | ✅ | **强化**：三态化 + 有序化 |

---

## 十一、实现路线图（v1.9）

### 阶段一：事件循环 + 批次化（P0）

1. **建立统一事件循环**
   - 事件队列（优先级队列）
   - 事件分发器（控制 > 定时器 > 数据）
   - 控制事件处理（start/pause/resume/stop）

2. **实现批次化 + 版本屏障**
   - 新增 `batch_state` 表
   - 数据更新攒批处理（不是来一个处理一个）
   - 批次开始时批量更新 latest_tick
   - 计算阶段只读，保证一致性

3. **数据事件接入**
   - tick 数据先入数据队列
   - 批次开始时集中处理
   - 收集 dirty_stocks，更新 node_data_dirty
   - 通知公式引擎 on_data_updated

### 阶段二：时间驱动独立化（P0）

1. **定时器事件源**
   - 定时器管理（注册/取消/重调度）
   - 定时器事件生成（时间到了发事件）
   - 接入事件循环

2. **边时间触发配置编译**
   - 编译期分析每条边的时间触发条件
   - 生成 edge_timer_specs
   - 启动时注册所有定时器

3. **定时器事件处理**
   - 收到 edge_timer_event → 处理对应边
   - 复用状态表缓存（不一定每次重算）
   - 处理完后重新注册下一次定时器

### 阶段三：node_stock_change 结构化 + 增量处理（P0）

1. **升级 node_stock_change**
   - 从 bool 改为 {entered: Set, exited: Set}
   - 所有设置 stock_change 的地方都要改
   - propagate 时计算差集并写入

2. **入池初始化逻辑**
   - 新入池股票的指标计算
   - 新入池股票的比较结果初始化
   - 新入池股票的 TTL 注册

3. **出池清理逻辑**
   - 出池股票的比较结果清理
   - 出池股票的过滤结果清理
   - 出池股票的 TTL 取消
   - （公式引擎缓存懒清理，可选）

4. **完整增量处理链条验证**
   - 验证：entered/exited/data_dirty → 增量指标 → 增量比较 → 增量集合 → propagate
   - 每一步都是增量的，不碰没变的股票
   - 增量结果与全量结果一致

### 阶段四：公式引擎生命周期补全（P0）

1. **新增 register_formula 接口**
   - 公式注册 + 编译 + 缓存
   - 返回 formula_id
   - formula_registry 通过此接口注入

2. **新增 invalidate_formula 接口**
   - 单公式失效（所有股票）
   - 公式定义/参数变更时调用

3. **新增 invalidate_formula_for_codes 接口**
   - 指定股票的指定公式失效
   - 精细控制缓存

4. **新增 on_period_tick 接口**
   - 周期K线确认通知
   - 不同周期的K线更新频率不同
   - 性能优化：不用每个tick都重算日线指标

5. **新增 start/stop 生命周期**
   - start：预热缓存、启动定时器
   - stop：清理资源、取消定时器

### 阶段五：状态表完善（P1）

1. **edge_compare_results 三态化**
   - 从 bool 改为 True/False/None
   - 数据不足时返回 None
   - 三态逻辑运算规则（AND/OR/NOT）

2. **edge_filter_results 排名型有序化**
   - 排名型 filter 返回有序列表（List[code]）
   - 保留排名顺序
   - 支持取前 N 名、查名次等操作

3. **每张状态表的失效条件文档化**
   - indicator_results 的失效条件
   - edge_compare_results 的失效条件
   - edge_filter_results 的失效条件

### 阶段六：文档完善与验证（P1）

1. **更新所有相关文档**
   - 确保所有文档使用 v1.9 的概念
   - 不再使用旧概念（如 node_stock_change: bool）

2. **正确性验证**
   - 增量计算结果 = 全量计算结果
   - 批次一致性验证：计算过程中数据不会变
   - 时间触发精度验证：到点就触发，不依赖数据更新

3. **性能验证**
   - 批次化处理的吞吐提升
   - 增量计算的性能提升（tick 稀疏时）
   - 定时器事件的精度和开销

---

## 十二、统计总结（v1.8 → v1.9）

### 12.1 概念数量变化

| 统计项 | v1.8 | v1.9 | 变化 |
|--------|------|------|------|
| 核心运行时表 | 8 张 | **9 张** | +1（batch_state），升级 3 张表语义 |
| filter 层数 | 3 层 | 3 层 | 不变（但每层状态表更完善） |
| 公式引擎接口数 | 6 个 | **11 个** | +5（register + invalidate_* + on_period_tick + start/stop） |
| 脏来源种类 | 2 种（stock_change + data_change） | **2 种，但 stock_change 结构化** | stock_change 从 bool 升级为 entered/exited |
| 显式状态表层数 | 3 层 | 3 层 | 不变，但比较层三态化，过滤层有序化 |
| 事件源数量 | 1 种（只有数据） | **3 种**（数据 + 定时器 + 控制） | 真正的事件驱动架构 |
| 一致性保证 | 隐式（可能有问题） | **显式**（批次化 + 版本屏障） | 从"可能不一致"到"严格一致" |

### 12.2 为什么是 v1.9？

**v1.9 是 v2.0 之前的最后一个大版本，主要完善架构层面的基础问题：**

```
演进路径：
  v1.5 ~ v1.6：概念精简阶段（从多到少，先做对）
  v1.7：性能优化阶段（股票级水位线，增量计算）
  v1.8：状态显式化阶段（三层状态表，每层结果都有表）
  v1.9：架构完善阶段（一致性 + 真事件驱动 + 生命周期）
  v2.0：完整稳定版（所有功能完善，文档齐全）
```

**v1.9 解决的是"架构层面"的基础问题：**
1. **一致性**：批次化 + 版本屏障，从根本上解决并发一致性问题
2. **真事件驱动**：时间驱动独立化，不再是"数据驱动+顺便检查"
3. **生命周期完整**：公式引擎从"求值工具"升级为"完整组件"
4. **真正增量**：node_stock_change 结构化，入池出池各有各的处理逻辑

这些都是"架构级"的改进，不是简单加功能。搞定这些，v2.0 就水到渠成了。

### 12.3 一句话总结

**v1.9 建立单线程事件循环 + 批次化版本屏障，从根本上解决并发一致性问题；node_stock_change 从 bool 升级为 entered/exited 双集合，实现真正的增量处理；公式引擎补全生命周期接口（register_formula / invalidate_formula / on_period_tick / start/stop）；时间驱动独立化为定时器事件源，配合统一事件循环，实现真正的事件驱动架构；状态表三态化与有序化，语义更准确。**
