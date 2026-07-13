# 股票池深度重构规划 v1.2

> 核心洞察：时间戳即水位线 + 股票级增量计算
> 设计原则：表驱动、数据驱动、事件驱动、增量计算
> 目标：engine.py 从 3504 行 → ≤ 800 行，配置表从 50+ 张 → ≤ 12 张核心表

---

## v1.1 → v1.2 变更摘要：股票级增量计算 + 时间戳即水位线

**变更日期：** 2026-07-01

**核心修正（基于第2轮评审的三大新洞察）：**

| # | 变更项 | v1.1 | v1.2 | 本质变化 |
|---|--------|------|------|---------|
| 1 | **水位线判定** | hash 比较 + `latest_tick_ts` | 纯 `latest_tick_ts`，无 hash | 去掉 hash 计算，时间戳本身就是版本标记 |
| 2 | **脏标记粒度** | 节点级（`stock_dirty_ts[nid]`） | 股票级（`stock_data_version[code]` / `stock_state_version[nid][code]`） | 从"整节点重算"到"单只股票增量" |
| 3 | **过滤计算方式** | 全量重算（源节点所有股票都过 filter） | 增量计算（只算数据变了的股票，没变的复用结果） | 性能从 O(n) → O(Δn)，Δn ≪ n |
| 4 | **数据脏标记** | `data_dirty` 全局 bool + 节点级 | 全局水位线 `latest_tick_ts` + 股票级 data_version | 去掉中间层标记，直接比时间戳 |
| 5 | **边触发依据** | `data_dirty OR stock_dirty_ts > last_processed_ts` | 源节点中存在 `stock_data_version[code] > edge_last_data_ts[eid]` 的股票 | 从"节点级二值判定"到"股票级精细筛选" |
| 6 | **filter 结果复用** | 无（每次全量重算） | 有（`edge_filter_cache[eid][code] = True/False`） | 没变化的股票直接读缓存，跳过计算 |
| 7 | **运行时核心表** | 7 张（节点级粒度） | 8 张（股票级粒度，更精细） | 增加股票版本表和过滤缓存表 |

**一句话总结 v1.2 升级：** 从"节点级全量重算"进化到"股票级增量计算"，时间戳直接当水位线，去掉多余的 hash 比较和节点级脏标记，性能提升一个数量级。

---

## 一、本质认知：股票池到底是什么

### 1.1 一句话本质

**股票池 = 一组节点 × 一组边 × 一个全局水位线 × 一堆股票版本号 × 一个事件队列。**

- **节点**：装股票的容器（备选池/状态池/条件池/目标池）
- **边**：节点之间的连接，带触发条件（时间 + 过滤条件）
- **全局水位线**：`latest_tick_ts` —— 所有数据的最新时间戳。水位线不涨，所有计算结果不变。
- **股票版本号**：每只股票自己的 `data_version`（数据版本）和每个节点里的 `state_version`（状态版本）
- **事件队列**：`edge_pending` —— 待处理的边，按执行顺序排列。事件驱动，不是轮询。

### 1.2 运行时只有四件事

| # | 事情 | 触发条件 | 做什么 |
|---|------|---------|-------|
| 1 | **数据更新** | 外部行情推送 / K线到达 | 写 `latest_tick` 表，更新 `latest_tick_ts`，更新该股票的 `stock_data_version[code]` |
| 2 | **TTL 过期处理** | tick 开始时检查过期队列 | 从 `ttl_expiry_queue` 弹出已过期股票，从节点移除，更新该股票在节点中的 `state_version` |
| 3 | **边触发 + 执行**（事件驱动） | 从 `edge_pending` 队列取边 | 检查时间条件 → 满足就增量 filter → propagate → 目标节点更新 → **立即发事件** → 标记目标节点中变化的股票 → 目标节点出边入队 |
| 4 | **无条件边立即传播**（同步） | 源节点股票状态变化时立即触发 | 直接 propagate，不走 gate，不走 filter，立即更新目标节点，立即发事件 |

**增量计算的核心特征：**
- 不是每 tick 把源节点所有股票都过一遍 filter
- 而是只挑出 `stock_data_version[code] > edge_last_data_ts[eid]` 的股票
- 这些股票才需要重新计算 filter
- 其他股票直接复用上次的 filter 结果（`edge_filter_cache[eid][code]`）
- 没变的股票 = 零开销

**就这四件事。** 其他所有东西（拓扑、执行顺序、公式、K线合成、界面刷新）全都是运行前就确定的，运行时只读不写。

### 1.3 为什么之前代码又臭又长

因为把"运行前确定的东西"和"运行时变化的东西"搅在一起了：
- 每 tick 都重算拓扑序 → 错！拓扑运行前就定了
- 每 tick 都重新解析边参数 → 错！边参数运行前就编译好了
- 每 tick 都遍历所有节点所有边 → 错！只有 pending 队列里的边才需要检查
- 每 tick 都把所有股票过一遍 filter → 错！只算数据变了的股票
- 数据更新和过滤计算混在一起 → 错！数据层和计算层必须分离
- TTL 每 tick 检查所有股票 → 错！用过期队列，事件驱动
- 用 hash 比较判断数据变没变 → 错！时间戳本身就是版本号

---

## 二、核心设计：股票级版本 + 增量计算 + 事件队列

### 2.1 运行时内存表（核心运行时表一共 8 张）

| 表名 | 类型 | 读时机 | 写时机 | 说明 |
|------|------|--------|--------|------|
| `latest_tick` | Dict[code → bar_dict] | filter 计算时读 | 行情推送时写 | **唯一真相源**。所有股票的最新tick数据 |
| `latest_tick_ts` | float | 水位线比较时读 | 行情推送时写 | **全局时间水位线**。只要这个值不变，所有K线计算结果都不变 |
| `stock_data_version` | Dict[code → float] | 增量筛选时读 | 该股票行情推送时写 | **每只股票的数据版本** = 最后一次数据更新时的水位线 |
| `node_stocks` | Dict[nid → List[code]] | propagate 读写、filter 读 | 边执行/TTL过期后写 | 各节点当前股票列表 |
| `stock_state_version` | Dict[nid → Dict[code → float]] | 状态变化判断时读 | 股票入池/出池时写 | **每只股票在每个节点的状态版本** = 最后一次状态变化时的水位线 |
| `edge_pending` | List[eid]（按执行顺序） | 执行循环读 | 节点股票状态变化时追加 | **边待处理队列**。需要检查的边，按执行顺序排列，去重 |
| `edge_filter_cache` | Dict[eid → Dict[code → bool]] | 增量 filter 时读 | filter 计算后写 | **过滤结果缓存**。每只股票在每条边的上次过滤结果 |
| `edge_last_data_ts` | Dict[eid → float] | 增量筛选时读 | 边执行完后写 | **每条边的数据处理水位线** = 最后一次处理时的全局水位线 |
| `ttl_expiry_queue` | Heap[(expire_ts, nid, code)] | tick 开始时弹出 | 股票入池时插入 | **TTL 过期队列**。按过期时间排序的最小堆 |

**就这八张核心运行时表。** 其他的都是运行前编译产物或配置表。

### 2.2 为什么 `latest_tick_ts` 是灵魂（去掉 hash 比较）

用户的核心洞察：**新 tick 的时间戳天然就比旧的大，时间戳本身就是最好的版本标记。根本不需要算什么 hash。**

展开说：

```
行情推送 → 写 latest_tick[code] = new_bar
         → 现在的时间戳 ts = new_bar['datetime']
         → ts > latest_tick_ts 吗？
             → 不大于：说明是历史数据或重复数据，忽略
             → 大于：
                 latest_tick_ts = ts                    # 全局水位线涨了
                 stock_data_version[code] = ts          # 这只股票的数据版本更新了
                 # 注意：不立即把所有边加入 pending 队列
                 # 也不置什么 data_dirty 标记
                 # 边什么时候处理？看 edge_pending 队列
                 # 哪些股票要重算？看 stock_data_version[code] > edge_last_data_ts[eid]
```

**水位线没涨 = 数据没变 = 所有公式结果不变 = 过滤结果不变 = 什么都不用算。**

**时间戳就是版本号。** 不需要 hash，不需要 checksum，不需要任何额外的计算。时间天然单调递增，新数据的时间戳一定比旧数据大。

这是整个系统最重要的性能优化和逻辑简化支点。

### 2.3 脏标记的本质：从节点级到股票级

v1.1 的问题：节点级脏标记太粗糙。一个节点里有 1000 只股票，可能只有 10 只的状态变了，其他 990 只都没变。但只要标记了"节点脏"，就要把 1000 只都拉出来重算一遍 filter。

v1.2 的解法：**股票级版本号。**

| 版本标记 | 类型 | 粒度 | 含义 | 更新时机 |
|----------|------|------|------|---------|
| `latest_tick_ts` | float | 全局 | 全局时间水位线 = 最新数据的时间戳 | 任何股票有新数据时，取最大值 |
| `stock_data_version[code]` | float | 每只股票 | 这只股票最后一次数据更新时的水位线 | 这只股票有新 tick 时 |
| `stock_state_version[nid][code]` | float | 每只股票 × 每个节点 | 这只股票在这个节点里最后一次状态变化时的水位线 | 股票入池/出池/TTL淘汰时 |
| `edge_last_data_ts[eid]` | float | 每条边 | 这条边最后一次处理时的全局水位线 | 边执行完 filter 后 |

**怎么判断一只股票需不需要重算 filter？**

```
需要重算 = stock_data_version[code] > edge_last_data_ts[eid]
```

就这么简单。比一下时间戳，大于就是数据更新过，需要重算；小于等于就是数据没变，直接用缓存。

**怎么判断一个节点的股票状态有没有变化？**

不是看什么 `stock_dirty_ts[nid]`（节点级的粗标记），而是：
- 边执行完后，对比 propagate 前后目标节点的股票集合
- 哪些股票新进来了 / 哪些股票出去了
- 这些变化了的股票，更新它们的 `stock_state_version[nid][code] = latest_tick_ts`
- 只要有任何股票变化，就把目标节点的出边加入 `edge_pending`

**边什么时候被触发？**
- **无条件边**：源节点有股票状态变化 → **立即同步执行**（不进 pending 队列）
- **条件边**：源节点有股票状态变化 → 边加入 pending 队列 → 轮到它时检查：
  - 时间条件满足吗？
  - 源节点中有没有 `stock_data_version[code] > edge_last_data_ts[eid]` 的股票？
  - 有就执行（增量 filter），没有就跳过

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
    
    # 条件边列表（数据更新时需要考虑的边）
    'conditional_edges': [eid, ...],
}
```

**关键：执行顺序 = 综合设置表格的行顺序，不是拓扑排序的结果。**

综合设置表格的每一行就是一个计算单元（一条边），行号就是执行序号。用户在综合设置里拖拽调整行顺序，就是调整执行顺序。每条边对应一行，每行对应一条边。

加载时按表格行顺序编译得到 `edge_order` 列表，运行时就按这个顺序串行执行。拓扑只是用来画界面和校验合法性的，不是执行顺序。

### 3.2 运行时做的事（事件驱动 + 增量计算核心循环）

就一个循环（**事件驱动 + 流式串行执行 + 增量计算**）：

```
1. 等数据更新（或时间步进）
2. 新 tick 来了吗？
   → 没来：去步骤 3
   → 来了：
       写 latest_tick[code] = new_bar
       ts = new_bar['datetime']
       如果 ts > latest_tick_ts:
           latest_tick_ts = ts              # 全局水位线涨了
       stock_data_version[code] = ts        # 这只股票的数据版本更新了
3. 处理 TTL 过期：
   从 ttl_expiry_queue 弹出所有 expire_ts <= now 的股票
   → 从对应节点 node_stocks[nid] 移除
   → 更新 stock_state_version[nid][code] = latest_tick_ts
   → 触发 _on_node_stocks_changed(nid, changed_codes)
4. 处理 edge_pending 队列（按执行顺序）：
   while edge_pending 不为空：
      取出队首边 eid（按执行顺序最靠前的）
      sid, tid = edge_endpoints[eid]
      
      如果是无条件边：
         → 已经在 _on_node_stocks_changed 时立即处理过了，跳过
         （无条件边不进 pending 队列，这里是防御性检查）
      
      如果是条件边：
         a. 检查时间触发条件满足吗？
            → 不满足：跳过（下次 tick 再说，或者等下一次入队）
         b. 增量筛选：找出源节点中需要重算的股票
            dirty_codes = [code for code in node_stocks[sid]
                          if stock_data_version[code] > edge_last_data_ts[eid]]
            → 如果 dirty_codes 为空：
                没有股票需要重算，跳过（全用缓存）
                但是等等——如果有新入池的股票呢？
                新入池的股票，stock_state_version 是新的，
                但它可能不在 edge_filter_cache 里。
                所以还要加上：
                new_codes = [code for code in node_stocks[sid]
                            if code not in edge_filter_cache[eid]]
                need_compute = dirty_codes + new_codes
                → 如果 need_compute 为空：
                    真的什么都不用算，跳过
         c. 需要计算 → 增量执行这条边：
            i. 增量 filter：
               - 对 need_compute 中的股票，重新计算 filter 结果
               - 结果写入 edge_filter_cache[eid][code]
               - 其他股票直接从 edge_filter_cache[eid][code] 读
               - 汇总所有通过 filter 的股票 = passed_codes
            ii. propagate 传播 → 更新目标节点 node_stocks[tid]
            iii. 对比目标节点变化，得到 entered_codes 和 exited_codes
            iv. 如果有变化：
                 - 更新 stock_state_version[tid][code] = latest_tick_ts
                   （对所有 entered_codes 和 exited_codes）
                 - 把 tid 的所有出边加入 edge_pending（去重）
                 - 处理无条件边：立即同步 propagate
                 - **立即发射入池/出池事件**
            v. 更新 edge_last_data_ts[eid] = latest_tick_ts
5. 回去等下一次数据更新
```

**增量计算的关键含义：**
- 不是每条边每次都把源节点所有股票过一遍 filter
- 而是只算 `stock_data_version[code] > edge_last_data_ts[eid]` 的股票
- 加上新入池的、还没算过的股票
- 其他股票直接读缓存，零开销
- 实际行情中，每 tick 只有少数股票有数据更新，大部分股票不动
- 所以 Δn ≪ n，性能提升一个数量级

**事件驱动的关键含义：**
- 不是每 tick 遍历所有边，而是只处理 `edge_pending` 队列里的边
- 边入队的触发源只有一个：**源节点有股票状态变化**
- 数据更新本身不会让边入队，数据更新只更新股票的 data_version
- 边什么时候发现数据更新了？轮到它执行时，自己去比 `stock_data_version`
- 无条件边是**同步立即执行**的，不走 pending 队列
- 执行是**流式串行的**，按执行顺序一条一条来
- 后一条边能看到前一条边的执行结果（目标节点已更新、版本号已更新）

**就这么简单。**

---

## 四、功能-表操作对应表

### 4.1 数据层（最新tick表，时间戳即水位线）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| 行情推送 | — | `latest_tick[code] = new_bar` + `stock_data_version[code] = ts` | 无 hash 计算，时间戳直接当版本号 |
| 水位线更新 | `new_bar['datetime']` | `latest_tick_ts = max(latest_tick_ts, ts)` | 比较时间戳大小，取最大值 |

**关键变化（v1.1 → v1.2）：**
- 去掉了 hash 比较，不需要计算全量 tick 的 hash
- 时间戳本身就是版本标记，天然单调递增
- `stock_data_version[code]` 记录每只股票自己的数据版本

### 4.2 TTL 淘汰层（事件驱动，非轮询）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| 股票入池记录 TTL | `edge_ttl_spec[eid]` | `ttl_expiry_queue` 插入 `(expire_ts, nid, code)` | `expire_ts = now + ttl_sec` |
| TTL 过期检查 | `ttl_expiry_queue + now` | 弹出过期项 | 最小堆：堆顶过期就弹出，直到堆顶未过期 |
| 过期股票移除 | `node_stocks[nid]` | `node_stocks[nid]` + `stock_state_version[nid][code]` | 从节点移除过期股票，更新该股票的状态版本 |
| 过期触发级联 | `stock_state_version[nid][code]` | `edge_pending` + 无条件边立即执行 | 源节点股票状态变了 → 出边入队 + 无条件边立即 propagate |

**关键变化（v1.1 → v1.2）：**
- `stock_dirty_ts[nid]`（节点级时间戳）→ `stock_state_version[nid][code]`（股票级 × 节点级）
- 从"整个节点脏"变成"具体哪只股票状态变了"

### 4.3 边触发判定层（事件驱动 + 增量筛选）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| 节点股票状态变化 → 边入队 | `out_edges[sid] + edge_type[eid]` | `edge_pending` 追加（去重） | 源节点出边中，条件边入队；无条件边立即执行 |
| 边时间触发检查 | `edge_timing_spec[eid] + latest_tick_ts + _flow_state[eid]` | — | `starttype × cxtype` 的 24 种判定 |
| 增量筛选需要重算的股票 | `stock_data_version[code] + edge_last_data_ts[eid] + node_stocks[sid] + edge_filter_cache[eid]` | — | `need_compute = (data_version > edge_last_data_ts) OR (code not in cache)` |
| 边是否需要执行 | `need_compute` 是否为空 | — | 有需要计算的股票就执行，没有就跳过 |

**关键变化（v1.1 → v1.2）：**
- 去掉了 `data_dirty` 全局 bool 标记
- 去掉了 `stock_dirty_ts[nid]` 节点级时间戳
- 去掉了 `last_processed_ts[eid]` 边级时间戳
- 换成 `stock_data_version[code]`（股票数据版本）+ `edge_last_data_ts[eid]`（边处理水位线）
- 判定粒度从"整条边要不要执行"变成"哪些股票需要重算"

### 4.4 边执行层（增量 filter + propagate + 即时事件）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **增量条件过滤** | `latest_tick` + `node_stocks[sid]` + `edge_filter_spec[eid]` + `stock_data_version` + `edge_last_data_ts[eid]` + `edge_filter_cache[eid]` | `edge_filter_cache[eid][code]` | 只重算数据变了的股票，其他读缓存；汇总所有通过的 |
| 状态传播 | `node_stocks[sid]` + `node_stocks[tid]` + `edge_propagate_spec[eid]` | `node_stocks[tid]` + `stock_state_version[tid][code]` | copy / move / overwrite；更新变化股票的状态版本 |
| **即时事件发射** | `node_stocks[tid]` 新旧对比 + `node_role[tid]` | `event_queue` + `signal_queue` | 差集计算（新 - 旧 / 旧 - 新） | **每条边执行后立即发射** |
| 无条件边立即传播 | `node_stocks[sid]` + `edge_propagate_spec[eid]` | `node_stocks[tid]` + `stock_state_version[tid][code]` | 直接 propagate，无 gate，无 filter |
| 边处理水位线更新 | `latest_tick_ts` | `edge_last_data_ts[eid]` | `edge_last_data_ts[eid] = latest_tick_ts` |

**关键变化（v1.1 → v1.2）：**
- 全量 filter → 增量 filter
- 新增 `edge_filter_cache[eid][code]` 过滤结果缓存
- 新增 `edge_last_data_ts[eid]` 边处理水位线
- 性能从 O(n) → O(Δn)

### 4.5 事件层（流式逐条产生）

| 功能 | 读什么表 | 写什么表 | 计算 | 时机 |
|------|---------|---------|------|------|
| 入池事件 | `node_stocks[tid]` 执行前后对比 | `event_queue` | 差集计算（新 - 旧） | **每条边执行后立即发射** |
| 出池事件 | `node_stocks[sid]` 执行前后对比 | `event_queue` | 差集计算（旧 - 新） | **每条边执行后立即发射** |
| 预警事件 | `alert_rules + node_stocks` 变化 | `alert_queue` | 规则匹配 | 每条边执行后立即检查 |
| 交易信号 | `node_role[tid] == 'target' + 入池/出池` | `signal_queue` | 角色判定 + 信号生成 | 每条边执行后立即生成 |

**流式事件的关键：** 不是攒到 tick 末尾统一发，而是每条边执行完、目标节点更新后，立即计算该边导致的变化并发射事件。这样事件的顺序与边的执行顺序严格一致。

### 4.6 后处理层（PK排名/分析角度/看盘面板）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| PK排名 | `node_stocks[target] + latest_tick + pk_config` | `_pk_rankings` | 按权重评分排序 |
| 分析角度 | `node_stocks[target] + latest_tick + analysis_config` | `_angle_results` | 多维度计算 |
| 看盘面板 | `node_stocks + latest_tick + dashboard_schema` | `_dashboard_data` | 组装显示数据 |

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
def eval_filter_single(code, spec, tick_data):
    """单只股票的 filter 计算（增量计算时调用）"""
    value = spec['evaluator_single'](code, spec.formula, tick_data)
    passed = spec['operator_single'](value, spec.threshold)
    return passed

def eval_filter_batch(codes, spec, tick_data):
    """批量 filter 计算（全量计算时调用）"""
    values = spec['evaluator_batch'](codes, spec.formula, tick_data)
    passed = spec['operator_batch'](values, spec.threshold)
    return passed
```

**6 个求值器 + 10 个比较器 = 60 种筛选方式。**

v1.2 新增：支持单只股票的增量计算接口 `eval_filter_single`，和批量计算接口 `eval_filter_batch` 并存。

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
| 2 | `filter_specs.json` | 过滤条件规格（nset + noperate） | filter 计算（支持批量 + 单只） |
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
                       # 只做：数据更新 → TTL过期 → pending队列 → 增量执行 → 事件
  runtime.py          # 运行时表定义（8张核心表）
                       # latest_tick / latest_tick_ts / stock_data_version
                       # node_stocks / stock_state_version
                       # edge_pending / edge_filter_cache / edge_last_data_ts
                       # ttl_expiry_queue
  compiler.py         # 编译期：pool_config → CompiledPool
  timing.py           # 时间触发规则（查表 + 组合）
  filters.py          # 过滤条件（求值器 + 比较器，支持批量 + 单只）
  propagate.py        # 传播模式
  roles.py            # 节点角色行为
  events.py           # 事件/信号生成
  ttl_queue.py        # TTL 过期队列管理（最小堆）

data/
  tick_table.py       # latest_tick 表管理（读/写/时间戳水位线）
  kline_cache.py      # K线缓存
  providers/          # 各数据源适配器

post_processing/
  pk_ranking.py       # PK 排名
  analysis_angles.py  # 分析角度
  dashboard.py        # 看盘面板
  alerts.py           # 预警

config/               # 16 张 JSON 配置表
```

**v1.2 关键变化：**
- `tick_table.py`：去掉 hash 计算，直接用时间戳当版本号
- `filters.py`：新增单只股票计算接口，支持增量计算
- `runtime.py`：从 7 张表变成 8 张表，粒度从节点级细化到股票级

### 7.2 engine.py 核心循环伪代码（事件驱动 + 增量计算，约 180 行）

```python
class StockPoolEngine:
    
    def __init__(self):
        self._latest_tick = {}                # latest_tick[code] = bar_dict
        self._latest_tick_ts = 0.0            # 全局时间水位线
        self._stock_data_version = {}         # stock_data_version[code] = ts
        
        self._node_stocks = {}                # node_stocks[nid] = [code, ...]
        self._stock_state_version = {}        # stock_state_version[nid][code] = ts
        
        self._edge_pending = []               # 待处理边队列（按执行顺序，去重）
        self._edge_filter_cache = {}          # edge_filter_cache[eid][code] = bool
        self._edge_last_data_ts = {}          # edge_last_data_ts[eid] = ts
        
        self._ttl_queue = TTLExpiryQueue()    # TTL 过期队列（最小堆）
        
        self._compiled = None                 # CompiledPool
    
    def on_tick(self, code, bar):
        """收到新 tick 数据"""
        ts = bar['datetime']
        
        # 1. 写数据
        self._latest_tick[code] = bar
        
        # 2. 更新全局水位线（如果新数据时间更新）
        if ts > self._latest_tick_ts:
            self._latest_tick_ts = ts
        
        # 3. 更新这只股票的数据版本
        self._stock_data_version[code] = ts
        
        # 注意：不立即触发任何边
        # 边什么时候处理？看 edge_pending 队列
        # 哪些股票要重算？边执行时自己去比 stock_data_version
    
    def _process_ttl_expiry(self):
        """处理 TTL 过期股票"""
        now_ts = self._latest_tick_ts
        changed_by_node = {}  # nid -> [code, ...]
        
        while not self._ttl_queue.empty():
            expire_ts, nid, code = self._ttl_queue.peek()
            if expire_ts > now_ts:
                break
            
            self._ttl_queue.pop()
            
            if code in self._node_stocks.get(nid, []):
                self._node_stocks[nid].remove(code)
                self._stock_state_version[nid][code] = now_ts
                if nid not in changed_by_node:
                    changed_by_node[nid] = []
                changed_by_node[nid].append(code)
        
        # 触发状态变化
        for nid, codes in changed_by_node.items():
            self._on_node_stocks_changed(nid, codes)
    
    def _on_node_stocks_changed(self, nid, changed_codes):
        """节点股票状态变化了 → 触发下游边"""
        # 更新变化股票的状态版本
        now_ts = self._latest_tick_ts
        for code in changed_codes:
            self._stock_state_version[nid][code] = now_ts
        
        # 把所有出边加入 pending 队列（条件边）
        compiled = self._compiled
        for eid in compiled['out_edges'].get(nid, []):
            if compiled['edge_type'][eid] == 'conditional':
                if eid not in self._edge_pending:
                    # 按执行顺序插入，保持有序
                    self._insert_in_order(eid)
            else:
                # 无条件边：立即同步执行
                self._execute_unconditional_edge(eid)
    
    def _process_edge_pending(self):
        """处理 edge_pending 队列（按执行顺序）"""
        while self._edge_pending:
            eid = self._edge_pending.pop(0)
            self._execute_conditional_edge(eid)
    
    def _execute_conditional_edge(self, eid):
        """执行一条条件边（增量计算）"""
        compiled = self._compiled
        sid, tid = compiled['edge_endpoints'][eid]
        now_ts = self._latest_tick_ts
        
        # 1. 检查时间触发条件
        if not self._check_timing(eid, now_ts):
            return  # 时间没到，跳过
        
        # 2. 增量筛选：找出需要重算的股票
        source_codes = self._node_stocks.get(sid, [])
        edge_data_ts = self._edge_last_data_ts.get(eid, 0.0)
        filter_cache = self._edge_filter_cache.setdefault(eid, {})
        
        need_compute = []
        for code in source_codes:
            data_version = self._stock_data_version.get(code, 0.0)
            if data_version > edge_data_ts or code not in filter_cache:
                need_compute.append(code)
        
        # 3. 如果没有需要计算的，直接用缓存
        if not need_compute:
            # 但是要确认一下——源节点股票可能有变化（新入池的已经包含在上面了）
            # 还有一种情况：源节点股票变少了（出池了）
            # 这种情况下，passed_codes 会自然减少
            passed_codes = [code for code in source_codes 
                          if filter_cache.get(code, False)]
            # 注意：即使不需要重算，也可能因为源节点股票变化而导致结果变化
            # 所以还是要走一遍 propagate 和对比
        else:
            # 4. 增量 filter
            spec = compiled['edge_filter_spec'][eid]
            for code in need_compute:
                # 单只股票计算
                passed = self._eval_filter_single(code, spec, self._latest_tick)
                filter_cache[code] = passed
            
            # 汇总所有通过的股票
            passed_codes = [code for code in source_codes 
                          if filter_cache.get(code, False)]
        
        # 5. propagate
        old_target = set(self._node_stocks.get(tid, []))
        self._propagate(eid, passed_codes)
        new_target = set(self._node_stocks.get(tid, []))
        
        # 6. 对比变化
        entered = new_target - old_target
        exited = old_target - new_target
        
        if entered or exited:
            # 更新状态版本
            for code in entered:
                self._stock_state_version[tid][code] = now_ts
            for code in exited:
                self._stock_state_version[tid][code] = now_ts
            
            # 发射事件
            self._emit_events(eid, tid, entered, exited)
            
            # 触发下游
            changed = list(entered) + list(exited)
            self._on_node_stocks_changed(tid, changed)
        
        # 7. 更新边的处理水位线
        self._edge_last_data_ts[eid] = now_ts
    
    def _execute_unconditional_edge(self, eid):
        """执行无条件边（立即同步，无 gate，无 filter）"""
        compiled = self._compiled
        sid, tid = compiled['edge_endpoints'][eid]
        now_ts = self._latest_tick_ts
        
        source_codes = list(self._node_stocks.get(sid, []))
        
        old_target = set(self._node_stocks.get(tid, []))
        self._propagate(eid, source_codes)  # 直接传播，不过滤
        new_target = set(self._node_stocks.get(tid, []))
        
        entered = new_target - old_target
        exited = old_target - new_target
        
        if entered or exited:
            for code in entered:
                self._stock_state_version[tid][code] = now_ts
            for code in exited:
                self._stock_state_version[tid][code] = now_ts
            
            self._emit_events(eid, tid, entered, exited)
            
            changed = list(entered) + list(exited)
            self._on_node_stocks_changed(tid, changed)
    
    def tick_cycle(self):
        """一个 tick 的完整处理流程"""
        # 1. 等数据更新（外部调用 on_tick）
        # 2. 处理 TTL 过期
        self._process_ttl_expiry()
        # 3. 处理 pending 队列
        self._process_edge_pending()
```

**v1.2 核心循环的关键变化：**
1. 去掉了 `data_dirty` 全局标记
2. 去掉了 `stock_dirty_ts[nid]` 节点级脏标记
3. 去掉了 `last_processed_ts[eid]` 边处理时间戳
4. 新增 `stock_data_version[code]` 股票数据版本
5. 新增 `stock_state_version[nid][code]` 股票×节点状态版本
6. 新增 `edge_filter_cache[eid][code]` 过滤结果缓存
7. 新增 `edge_last_data_ts[eid]` 边处理水位线
8. filter 从全量变成增量——只算数据变了的股票
9. 数据更新（on_tick）不再立即触发边，只更新版本号
10. 边什么时候执行，由 `edge_pending` 队列和源节点状态变化决定

---

## 八、事件驱动模型

### 8.1 事件类型与传播链

```
行情数据更新事件
    ↓
on_tick(code, bar)
    ↓
更新 latest_tick[code]
更新 latest_tick_ts（如果时间更新）
更新 stock_data_version[code]
    ↓
（不立即触发边，只更新版本号）

TTL 过期事件
    ↓
从 ttl_expiry_queue 弹出过期股票
从 node_stocks[nid] 移除
更新 stock_state_version[nid][code]
    ↓
_on_node_stocks_changed(nid, [code, ...])
    ↓
    ├─ 条件边 → 加入 edge_pending 队列
    └─ 无条件边 → 立即同步 propagate

边执行事件（从 pending 队列取出）
    ↓
检查时间条件
    ↓
增量筛选需要重算的股票
    ↓
增量 filter（算变动的，读缓存的）
    ↓
propagate → 更新目标节点
    ↓
对比变化 → entered / exited
    ↓
更新 stock_state_version[tid][code]
发射入池/出池事件
    ↓
_on_node_stocks_changed(tid, changed_codes)  ← 递归触发下游
```

### 8.2 脏标记传播图（股票级粒度）

```
                     ┌─────────────────────┐
                     │   latest_tick_ts    │  全局水位线（float）
                     └─────────┬───────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
    stock_data_version[A]  data_v[B]  ...  data_v[N]
    （每只股票一个）
              │
              │ 边执行时比大小：
              │ stock_data_version[code] > edge_last_data_ts[eid]
              ▼
    ┌──────────────────────────┐
    │ edge_filter_cache[eid]   │  过滤结果缓存
    │ {code: bool, ...}        │  （每条边一个）
    └──────────────┬───────────┘
                   │
                   ▼
          ┌─────────────────┐
          │  node_stocks    │  节点股票列表
          └───────┬─────────┘
                  │
                  ▼
    ┌──────────────────────────┐
    │ stock_state_version[nid] │  股票×节点状态版本
    │ {code: ts, ...}          │  （每个节点一个）
    └──────────────┬───────────┘
                   │ 状态变化时触发
                   ▼
          ┌─────────────────┐
          │  edge_pending   │  待处理边队列
          └─────────────────┘
```

**三层版本号：**
1. **全局层**：`latest_tick_ts` —— 全局水位线
2. **股票数据层**：`stock_data_version[code]` —— 每只股票的数据版本
3. **股票状态层**：`stock_state_version[nid][code]` —— 每只股票在每个节点的状态版本

**两种缓存：**
1. `edge_filter_cache[eid][code]` —— 过滤结果缓存，数据没变就直接读
2. `edge_last_data_ts[eid]` —— 边处理水位线，判断哪些股票数据更新了

---

## 九、关键洞见总结（v1.2 版）

### 洞见 1：时间戳即水位线，不需要 hash 比较

新 tick 的时间戳天然就比旧的大。时间戳本身就是最好的版本标记。
- 不需要计算 hash
- 不需要 checksum
- 不需要任何额外的版本计算
- `latest_tick_ts` 涨了 = 数据更新了 = 可能需要重算

### 洞见 2：脏标记应该是股票级的，不是节点级的

节点级脏标记太粗糙。一个节点 1000 只股票，可能只有 10 只变了，却要把 1000 只都拉出来重算。
- 每只股票有自己的 `data_version`（数据版本）
- 每只股票在每个节点有自己的 `state_version`（状态版本）
- 过滤计算时，只处理 `data_version > edge_last_data_ts` 的股票
- 没变化的股票，直接复用上次的计算结果
- 性能从 O(n) → O(Δn)，Δn ≪ n

### 洞见 3：所有节点读同一张 tick 表

数据更新是全局的，不是每个节点一份。
- 所有节点的公式计算都读同一张 `latest_tick` 表
- 不需要给每个节点单独标记"数据脏"
- 数据新不新，看全局的 `latest_tick_ts` 和每只股票的 `stock_data_version`
- 数据层和计算层彻底分离

### 洞见 4：事件驱动，不是轮询

不是每 tick 遍历所有边，而是只处理 `edge_pending` 队列里的边。
- 边入队的触发源只有一个：源节点股票状态变化
- 数据更新本身不会让边入队，只更新版本号
- 边什么时候发现数据更新了？轮到它执行时自己去比
- 无条件边同步立即执行，有条件边走队列

### 洞见 5：增量计算是性能的关键

实际行情中，每 tick 只有少数股票有数据更新，大部分股票不动。
- 全量重算是浪费
- 增量计算才是正道
- filter 结果能缓存就缓存
- 时间戳比大小就能判断要不要重算
- 简单、高效、正确

---

**v1.2 一句话总结：** 时间戳当水位线，股票级做增量，缓存 filter 结果，事件驱动执行。
