# 股票池深度重构规划 v1.2

> 核心洞察：最新 tick 时间不变 → 所有 K 线数据不变 → 不需重新计算
> 设计原则：表驱动、数据驱动、事件驱动
> 目标：engine.py 从 3504 行 → ≤ 800 行，配置表从 50+ 张 → ≤ 12 张核心表

---

## v1.2 变更摘要：脏标记分离 + 事件驱动 + TTL精确定义

**变更日期：** 2026-07-01

**核心修正（基于第1轮评审意见）：**

### 1. 两种脏标记彻底分离

**原问题：** 把"数据脏（水位线涨了）"和"股票脏（股票增减了）"混为一谈，统一用一个 `dirty_nodes` 集合。

**新设计：**
- **data_dirty（数据脏）**：全局一个布尔标记，不是每个节点一个。含义是"水位线涨了，所有条件边的 filter 结果可能变了"。
- **stock_dirty_ts（股票脏时间戳）**：每个节点一个时间戳。含义是"该节点的股票列表最近一次变化的时间"。
- **edge_pending（边待处理队列）**：一个按执行顺序排列的队列，存需要检查的边。不是每次遍历所有边。

### 2. 边触发：从轮询改事件驱动

**原问题：** 每 tick 都 for 循环遍历所有边，检查源节点脏不脏。本质是轮询。

**新设计：**
- 节点股票脏了 → 把该节点的所有出边加入 pending 队列
- 数据脏了 → 把所有条件边的源节点标记为"需重算"（但不是每条边都立即执行，要看时间条件）
- 执行循环：从 pending 队列按执行顺序取边，检查时间条件，满足就执行

### 3. TTL 触发时机精确定义

**原问题：** TTL 是每 tick 检查所有股票是否过期，O(n) 开销。

**新设计：**
- 股票入池时记录过期时间，维护一个**过期队列**（按过期时间排序的最小堆）
- 每次 tick 开始时，先从过期队列里把已过期的股票移除
- 移除股票 → 节点股票脏 → 出边进入 pending 队列
- TTL 是事件驱动的，不是轮询的

### 4. 无条件边的触发逻辑精确定义

**原问题：** 无条件边和条件边混在一起走同一套 gate 判定。

**新设计：**
- 无条件边 = 源节点股票脏了 → **立即 propagate**（不需要等时间条件，也不需要 filter）
- 本质上是"源节点的变化立即反映到目标节点"
- 是**同步的、立即的、无延迟的**
- 无条件边在 `stock_dirty` 事件触发时立即执行，不走 pending 队列

**主要更新章节：**
- §1.2 运行时只有三件事 → 改为四件事（增加 TTL 过期处理）
- §2.1 运行时内存表 → 增加 edge_pending、stock_dirty_ts、ttl_expiry_queue
- §2.3 节点脏标记的本质 → 彻底重写，三种脏标记分离
- §3.2 运行时核心循环伪代码 → 彻底重写为事件驱动模型
- §4 功能-表操作对应表 → 按新模型更新
- §7.2 engine.py 核心循环伪代码 → 彻底重写
- §9 事件驱动 → 彻底重写，阐述事件驱动机制
- §9.2 脏标记传播图 → 按新模型重绘
- §十四 关键洞见总结 → 更新

---

## 一、本质认知：股票池到底是什么

### 1.1 一句话本质

**股票池 = 一组节点 × 一组边 × 一个时间水位线 × 一个事件队列。**

- **节点**：装股票的容器（备选池/状态池/条件池/目标池）
- **边**：节点之间的连接，带触发条件（时间 + 过滤条件）
- **时间水位线**：`latest_tick_ts` —— 所有数据的最新时间戳。水位线不涨，所有计算结果不变。
- **事件队列**：`edge_pending` —— 待处理的边，按执行顺序排列。事件驱动，不是轮询。

### 1.2 运行时只有四件事

| # | 事情 | 触发条件 | 做什么 |
|---|------|---------|-------|
| 1 | **数据更新** | 外部行情推送 / K线到达 | 写 `latest_tick` 表，更新 `latest_tick_ts`，置 `data_dirty = True` |
| 2 | **TTL 过期处理** | tick 开始时检查过期队列 | 从 `ttl_expiry_queue` 弹出已过期股票，从节点移除，标记节点股票脏 |
| 3 | **边触发 + 执行**（事件驱动） | 从 `edge_pending` 队列取边 | 检查时间条件 → 满足就 filter → propagate → 目标节点更新 → **立即发事件** → 标记目标节点股票脏 → 目标节点出边入队 |
| 4 | **无条件边立即传播**（同步） | 源节点股票脏时立即触发 | 直接 propagate，不走 gate，不走 filter，立即更新目标节点，立即发事件 |

**事件驱动的核心特征：**
- 不是每 tick 遍历所有边，而是只处理 `edge_pending` 队列里的边
- 节点股票变化 → 出边入队 → 按执行顺序处理
- 数据变化 → 所有条件边的源节点标记"数据脏" → 下次时间条件满足时重算
- 无条件边同步立即执行，不进队列

**就这四件事。** 其他所有东西（拓扑、执行顺序、公式、K线合成、界面刷新）全都是运行前就确定的，运行时只读不写。

### 1.3 为什么之前代码又臭又长

因为把"运行前确定的东西"和"运行时变化的东西"搅在一起了：
- 每 tick 都重算拓扑序 → 错！拓扑运行前就定了
- 每 tick 都重新解析边参数 → 错！边参数运行前就编译好了
- 每 tick 都遍历所有节点所有边 → 错！只有 pending 队列里的边才需要检查
- 数据更新和过滤计算混在一起 → 错！数据层和计算层必须分离
- TTL 每 tick 检查所有股票 → 错！用过期队列，事件驱动

---

## 二、核心设计：三种类的脏标记 + 事件队列

### 2.1 运行时内存表（核心运行时表一共 7 张）

| 表名 | 类型 | 读时机 | 写时机 | 说明 |
|------|------|--------|--------|------|
| `latest_tick` | Dict[code → bar_dict] | filter 计算时读 | 行情推送时写 | **唯一真相源**。所有股票的最新tick数据 |
| `latest_tick_ts` | float | 边触发判定时读 | 行情推送时写 | **时间水位线**。只要这个值不变，所有K线计算结果都不变 |
| `data_dirty` | bool | 条件边判定时读 | 水位线涨了写 True，处理完写 False | **全局一个标记**。水位线涨了 → True，所有条件边需重算 filter |
| `node_stocks` | Dict[nid → List[stock]] | propagate 读写、filter 读 | 边执行/TTL过期后写 | 各节点当前股票列表 |
| `stock_dirty_ts` | Dict[nid → float] | 边触发判定时读 | 节点股票变化时写 | **每个节点一个时间戳**。该节点股票最后变化时间 |
| `edge_pending` | List[eid]（按执行顺序） | 执行循环读 | 节点股票脏时追加 | **边待处理队列**。需要检查的边，按执行顺序排列，去重 |
| `ttl_expiry_queue` | Heap[(expire_ts, nid, code)] | tick 开始时弹出 | 股票入池时插入 | **TTL 过期队列**。按过期时间排序的最小堆 |

**就这七张核心运行时表。** 其他的都是运行前编译产物或配置表。

### 2.2 为什么 `latest_tick_ts` 是灵魂

用户的核心洞察：**最新 tick 时间不变，则所有 K 线数据不变，所以不需重新计算。**

展开说：

```
行情推送 → 写 latest_tick[code] = new_bar
         → 计算 new_hash = hash(all bars)
         → 如果 new_hash == old_hash：什么都不做，退出
         → 如果 new_hash != old_hash：
             latest_tick_ts = now()  # 水位线涨了
             data_dirty = True       # 全局数据脏标记
             # 注意：不立即把所有边加入 pending 队列
             # 而是等时间条件满足时，由执行循环检查 data_dirty
```

**水位线没涨 = 数据没变 = 所有公式结果不变 = 过滤结果不变 = 什么都不用算。**

这是整个系统最重要的性能优化和逻辑简化支点。

### 2.3 三种脏标记的本质（彻底分离）

| 脏标记 | 类型 | 范围 | 含义 | 置脏时机 | 清脏时机 |
|--------|------|------|------|---------|---------|
| **data_dirty** | bool | 全局一个 | 水位线涨了，所有条件边的 filter 结果可能变了 | `latest_tick_ts` 更新时 | 一次 tick 末尾统一清 |
| **stock_dirty_ts[nid]** | float | 每个节点一个 | 该节点的股票列表最近一次变化的时间戳 | 股票入池/出池/TTL淘汰时 | 不清，单调递增（只比较新旧） |
| **edge_pending** | List[eid] | 全局一个队列 | 待检查/待执行的边，按执行顺序排列 | 源节点股票脏时，把源节点的所有出边加入 | 边处理完从队列移除 |

**三者的关系：**
- `data_dirty` 影响的是**条件边的 filter 结果**——数据变了，同样的股票可能过滤结果不同
- `stock_dirty_ts[nid]` 影响的是**源节点的股票集合**——股票变了，需要重新检查下游边
- `edge_pending` 是**执行队列**——哪些边需要被检查，按顺序来

**边什么时候被触发？**
- **无条件边**：源节点 `stock_dirty_ts` 变化 → **立即同步执行**（不进 pending 队列）
- **条件边**：源节点 `stock_dirty_ts` 变化 → 边加入 pending 队列 → 轮到它时检查：(时间条件满足) AND (data_dirty 或 stock_dirty) → 满足就执行

---

## 三、运行前 vs 运行时：严格分离

### 3.1 运行前（设计时 + 加载时）做的事

全部一次性做完，运行时只读。

| 阶段 | 做什么 | 产出 |
|------|--------|------|
| **设计时** | 用户拖拽节点、连线、配置参数、设置执行顺序 | pool_config (JSON) |
| **加载时** | 解析 pool_config，编译成运行时可用的结构 | CompiledPool |

**CompiledPool 是什么？** 就是把运行时需要的所有信息都预先算好，编好索引，排好顺序。运行时直接用，不再解析。

```python
CompiledPool = {
    # 节点
    'nodes': {nid: node_dict},           # 节点字典，按id查
    'node_type': {nid: type_name},       # 节点类型，快速判定
    
    # 边
    'edges': {eid: edge_dict},           # 边字典
    'edge_endpoints': {eid: (sid, tid)}, # 边的端点，预解析
    'edge_order': [eid1, eid2, ...],     # **综合设置行顺序 = 执行顺序**，不是拓扑序！
    'edge_type': {eid: 'conditional' | 'unconditional'},  # 边类型
    'edge_filter_spec': {eid: spec},     # 过滤条件的编译结果
    'edge_timing_spec': {eid: spec},     # 时间触发条件的编译结果
    'edge_propagate_spec': {eid: spec},  # 传播模式的编译结果
    'edge_ttl_spec': {eid: spec},        # TTL 淘汰规则的编译结果
    
    # 邻接表
    'out_edges': {nid: [eid, ...]},      # 节点的出边
    'in_edges': {nid: [eid, ...]},       # 节点的入边
    
    # 源节点列表（入度为0的节点）
    'source_nodes': [nid, ...],
    
    # 角色映射（哪些是目标池、哪些是备选池）
    'node_role': {nid: 'candidate' | 'state' | 'condition' | 'target' | 'discard'},
    
    # 条件边列表（data_dirty 时需要考虑的边）
    'conditional_edges': [eid, ...],
}
```

**关键：执行顺序 = 综合设置表格的行顺序，不是拓扑排序的结果。**

综合设置表格的每一行就是一个计算单元（一条边），行号就是执行序号。用户在综合设置里拖拽调整行顺序，就是调整执行顺序。每条边对应一行，每行对应一条边。

加载时按表格行顺序编译得到 `edge_order` 列表，运行时就按这个顺序串行执行。拓扑只是用来画界面和校验合法性的，不是执行顺序。

### 3.2 运行时做的事（事件驱动核心循环）

就一个循环（**事件驱动 + 流式串行执行**）：

```
1. 等数据更新（或时间步进）
2. 水位线涨了吗？
   → 没涨：去步骤 3
   → 涨了：data_dirty = True
3. 处理 TTL 过期：
   从 ttl_expiry_queue 弹出所有 expire_ts <= now 的股票
   → 从对应节点移除
   → 节点股票脏 → 触发 _on_node_stock_dirty(nid)
4. 处理 edge_pending 队列（按执行顺序）：
   while edge_pending 不为空：
      取出队首边 eid（按执行顺序最靠前的）
      sid, tid = edge_endpoints[eid]
      
      如果是无条件边：
         → 已经在 _on_node_stock_dirty 时立即处理过了，跳过
         （无条件边不进 pending 队列，这里是防御性检查）
      
      如果是条件边：
         a. 检查时间触发条件满足吗？
            → 不满足：跳过（下次 tick 再说，或者等下一次入队）
         b. 检查"需要执行吗"？
            = data_dirty  OR  stock_dirty_ts[sid] > last_processed_ts[eid]
            → 不需要：跳过
         c. 需要执行 → 执行这条边：
            i. filter 筛选
            ii. propagate 传播 → 更新目标节点
            iii. 如果目标节点股票变了：
                 - 更新 stock_dirty_ts[tid] = now()
                 - 把 tid 的所有出边加入 edge_pending（去重）
                 - 处理无条件边：立即同步 propagate
                 - **立即发射入池/出池事件**
            iv. 更新 last_processed_ts[eid] = now()
5. 清 data_dirty = False
6. 回去等下一次数据更新
```

**事件驱动的关键含义：**
- 不是每 tick 遍历所有边，而是只处理 `edge_pending` 队列里的边
- 边入队的触发源只有一个：**源节点股票脏了**
- `data_dirty` 不是入队条件，而是执行时的一个"放大器"——即使股票没变，只要数据变了，filter 结果也可能变
- 无条件边是**同步立即执行**的，不走 pending 队列
- 执行是**流式串行的**，按执行顺序一条一条来
- 后一条边能看到前一条边的执行结果（目标节点已更新、脏标记已传播）

**就这么简单。**

---

## 四、功能-表操作对应表

### 4.1 数据层（最新tick表）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| 行情推送 | — | latest_tick[code] = new_bar | 比较 hash，判断水位线要不要涨 |
| 水位线更新 | latest_tick 的 hash | latest_tick_ts + data_dirty | 计算全量 tick 的 hash；hash 变了 → data_dirty = True |

### 4.2 TTL 淘汰层（事件驱动，非轮询）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| 股票入池记录 TTL | edge_ttl_spec[eid] | ttl_expiry_queue 插入 (expire_ts, nid, code) | expire_ts = now + ttl_sec |
| TTL 过期检查 | ttl_expiry_queue + now | 弹出过期项 | 最小堆：堆顶过期就弹出，直到堆顶未过期 |
| 过期股票移除 | node_stocks[nid] | node_stocks[nid] + stock_dirty_ts[nid] | 从节点移除过期股票，更新股票脏时间戳 |
| 过期触发级联 | stock_dirty_ts[nid] | edge_pending + 无条件边立即执行 | 源节点股票脏 → 出边入队 + 无条件边立即 propagate |

### 4.3 边触发判定层（事件驱动，非轮询）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| 节点股票脏 → 边入队 | out_edges[sid] + edge_type[eid] | edge_pending 追加（去重） | 源节点出边中，条件边入队；无条件边立即执行 |
| 边时间触发检查 | edge_timing_spec[eid] + latest_tick_ts + _flow_state[eid] | — | starttype × cxtype 的 24 种判定 |
| 边是否需要执行 | data_dirty + stock_dirty_ts[sid] + last_processed_ts[eid] | — | (时间到) AND (data_dirty OR stock_dirty_ts > last_processed_ts) → 执行 |

### 4.4 边执行层（filter + propagate + 即时事件）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| 条件过滤 | latest_tick + node_stocks[sid] + edge_filter_spec[eid] | — | 公式计算 / 条件选股 / 财务筛选 / 行情筛选 |
| 状态传播 | node_stocks[sid] + node_stocks[tid] + edge_propagate_spec[eid] | node_stocks[tid] + stock_dirty_ts[tid] | copy / move / overwrite |
| **即时事件发射** | node_stocks[tid] 新旧对比 + node_role[tid] | event_queue + signal_queue | 每条边执行完立即发入池/出池事件（流式） |
| 无条件边立即传播 | node_stocks[sid] + edge_propagate_spec[eid] | node_stocks[tid] + stock_dirty_ts[tid] | 直接 propagate，无 gate，无 filter |

### 4.5 事件层（流式逐条产生）

| 功能 | 读什么表 | 写什么表 | 计算 | 时机 |
|------|---------|---------|------|------|
| 入池事件 | node_stocks[tid] 执行前后对比 | event_queue | 差集计算（新 - 旧） | **每条边执行后立即发射** |
| 出池事件 | node_stocks[sid] 执行前后对比 | event_queue | 差集计算（旧 - 新） | **每条边执行后立即发射** |
| 预警事件 | alert_rules + node_stocks 变化 | alert_queue | 规则匹配 | 每条边执行后立即检查 |
| 交易信号 | node_role[tid] == 'target' + 入池/出池 | signal_queue | 角色判定 + 信号生成 | 每条边执行后立即生成 |

**流式事件的关键：** 不是攒到 tick 末尾统一发，而是每条边执行完、目标节点更新后，立即计算该边导致的变化并发射事件。这样事件的顺序与边的执行顺序严格一致。

### 4.6 后处理层（PK排名/分析角度/看盘面板）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| PK排名 | node_stocks[target] + latest_tick + pk_config | _pk_rankings | 按权重评分排序 |
| 分析角度 | node_stocks[target] + latest_tick + analysis_config | _angle_results | 多维度计算 |
| 看盘面板 | node_stocks + latest_tick + dashboard_schema | _dashboard_data | 组装显示数据 |

**注意：后处理是"只读计算 + 写结果缓存"，不影响 node_stocks。** 是附属功能，不是核心流程。

---

## 五、表驱动：逻辑在表结构里，差异在表内容里

### 5.1 时间触发（timing.json）

**结构：** `{starttype: {rule, evaluator}, cxtype: {rule, evaluator}}`

**24 种组合 = 8 种 start × 3 种 cx**，不是 24 个 if，而是两张表的笛卡尔积：

```python
def eval_gate(edge, now_ts, flow_state):
    start_ok = _START_RULES[edge.starttype](edge, now_ts, flow_state)
    if not start_ok: return False
    cx_ok = _CX_RULES[edge.cxtype](edge, now_ts, flow_state)
    return cx_ok
```

**8 + 3 = 11 条规则**，覆盖 24 种组合。这才叫表驱动——不是把 24 个 if 塞进 JSON，而是找到两个正交维度，用组合来表达。

### 5.2 过滤条件（filter_specs.json）

**结构：** `{nset: {evaluator, operators: {noperate: op_fn}}}`

**6 种 nset × 10 种 noperate = 60 种组合**，同样是两张表的笛卡尔积：

```python
def eval_filter(codes, spec, tick_data):
    values = spec['evaluator'](codes, spec.formula, tick_data)  # nset 决定怎么求值
    passed = spec['operator'](values, spec.threshold)           # noperate 决定怎么比
    return passed
```

**6 个求值器 + 10 个比较器 = 60 种筛选方式。**

### 5.3 传播模式（propagate_modes.json）

**结构：** `{mode_name: {op: fn, affects_source: bool}}`

| 模式 | 操作 | 影响源节点 |
|------|------|-----------|
| copy | target += passed | 否 |
| move | target += passed; source = [] | 是 |
| overwrite | target = passed | 否 |

**3 种模式 = 3 条记录，不是 3 个 if。**

### 5.4 节点角色（node_roles.json）

**结构：** `{role_name: {triggers: [], actions: []}}`

角色决定了节点"发生变化时该做什么"：

| 角色 | 入池时做什么 | 出池时做什么 |
|------|------------|------------|
| candidate | 标记出边脏 | — |
| state | 标记出边脏 | — |
| condition | 标记出边脏（立即 propagate） | — |
| target | 发 ENTER 事件 + BUY 信号 + 写历史 | 发 EXIT 事件 + SELL 信号 |
| discard | 发 EXIT 事件 | — |

**角色表驱动，不是 if node.type == 'target'。**

---

## 六、配置表：从 50+ 张收敛到 12 张核心

### 6.1 核心配置表（运行时引擎直接读的）

| # | 表名 | 作用 | 运行时机 |
|---|------|------|---------|
| 1 | `timing.json` | 时间触发规则（starttype + cxtype） | gate 判定 |
| 2 | `filter_specs.json` | 过滤条件规格（nset + noperate） | filter 计算 |
| 3 | `propagate_modes.json` | 传播模式（copy/move/overwrite） | propagate |
| 4 | `node_roles.json` | 节点角色行为定义 | 事件/信号生成 |
| 5 | `edge_semantics.json` | 边类型语义（条件/无条件） | 边类型判定 |
| 6 | `runtime_modes.json` | 运行模式（实盘/回放/仿真） | 模式切换 |
| 7 | `alert_rules.json` | 预警规则 | 预警检查 |
| 8 | `ttl_rules.json` | TTL 淘汰规则 | TTL 过期队列管理 |

**8 张核心配置表。** 引擎核心循环只直接读这 8 张。

### 6.2 外围配置表（后处理/UI/导入导出用的）

| # | 表名 | 作用 | 谁读 |
|---|------|------|------|
| 9 | `post_tick_pipeline.json` | 后处理流水线顺序 | post_tick 模块 |
| 10 | `pk_config.json` | PK 排名配置 | pk_ranking 模块 |
| 11 | `analysis_config.json` | 分析角度配置 | analysis 模块 |
| 12 | `dashboard_schema.json` | 看盘面板配置 | dashboard 模块 |
| 13 | `xml_mapping.json` | XML 导入导出映射 | converters 模块 |
| 14 | `history_schema.json` | 历史记录格式 | history 模块 |
| 15 | `cell_type_registry.json` | 节点类型注册 | UI/table_engine |
| 16 | `data_config.json` | 数据源配置 | 数据层 |

**这些是外围的，不影响核心引擎的简洁性。** 核心引擎 8 张表就够了。

---

## 七、代码结构：核心极薄，外围分层

### 7.1 目录结构

```
core/
  engine.py           # 核心引擎 ≤ 800 行
                       # 只做：数据更新 → TTL过期 → pending队列 → 执行 → 事件
  runtime.py          # 运行时表定义（latest_tick / node_stocks / 脏标记 / 过期队列）
  compiler.py         # 编译期：pool_config → CompiledPool
  timing.py           # 时间触发规则（查表 + 组合）
  filters.py          # 过滤条件（求值器 + 比较器）
  propagate.py        # 传播模式
  roles.py            # 节点角色行为
  events.py           # 事件/信号生成
  ttl_queue.py        # TTL 过期队列管理（最小堆）

data/
  tick_table.py       # latest_tick 表管理（读/写/hash比较）
  kline_cache.py      # K线缓存
  providers/          # 各数据源适配器

post_processing/
  pk_ranking.py       # PK 排名
  analysis_angles.py  # 分析角度
  dashboard.py        # 看盘面板
  alerts.py           # 预警

config/               # 16 张 JSON 配置表
```

### 7.2 engine.py 核心循环伪代码（事件驱动，约 150 行）

```python
class StockPoolEngine:
    
    def __init__(self):
        self._tick_table = TickTable()          # latest_tick + latest_tick_ts
        self._node_stocks = {}                  # node_stocks[nid]
        self._stock_dirty_ts = {}               # stock_dirty_ts[nid]
        self._data_dirty = False                # 全局数据脏标记
        self._edge_pending = []                 # 待处理边队列（按执行顺序，去重）
        self._ttl_queue = TTLExpiryQueue()      # TTL 过期队列（最小堆）
        self._last_processed_ts = {}            # last_processed_ts[eid]
        self._compiled