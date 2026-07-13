# v3.1 变换单元执行语义与重构可能性

> **注意**：本文档是纯理解笔记，不是设计文档。
> 所有结论都有代码引用（文件+行号），不含主观臆断。
> 基于 v3.0 的认知颠覆，深入分析变换单元的运行时执行语义和重构可行性。

---

## 1. 变换单元的执行语义

### 1.1 一个变换单元完整执行一次，经历哪些步骤？

**变换单元 = conditional 入边 + 条件节点（枢纽） + unconditional 出边**

在一个 tick 内，一个变换单元的完整执行流程如下：

**步骤 1：入边（conditional）触发判定**
- 检查 gate 是否通过（starttype 时机、cxtype 持续期等）
- 检查源节点是否脏（上游变化）或数据是否脏（行情变化）
- 两者都满足才触发执行

**代码引用**：
- `core/engine.py:3621-3629` — `_run_tick_event_driven` 中的触发判定
- `core/engine.py:1757-1785` — `_should_trigger_edge` gate 评估

**步骤 2：入边（conditional）执行过滤计算**
- 流转属性解析（attr/tran → is_move/is_overwrite）
- nset 变更检测 + 空源早退
- 缓存查找（源和数据都没变时复用缓存结果）
- 策略解析 + 执行分派（调用实际的过滤函数，如 apply_filter / tdx_condition_eval / apply_dzh_filter）
- 缓存写入（将结果写入 _filter_cache）
- 结果写入条件节点（枢纽节点的股票列表被更新）

**代码引用**：
- `core/engine.py:2528-2627` — `_filter_conditional` 条件过滤完整流程

**步骤 3：条件节点标脏（级联传播的关键）**
- 入边执行完后，调用 `_mark_dirty(tid)` 将条件节点标记为脏
- 这是连接入边和出边的"隐式纽带"

**代码引用**：
- `core/engine.py:3638-3641` — 边执行后标记目标节点为脏

**步骤 4：出边（unconditional）触发判定**
- 遍历到条件节点的出边时
- gate 永远通过（starttype=0 → always）
- 源节点（条件节点）已经被入边标脏了，所以触发条件满足
- 出边被触发执行

**代码引用**：
- `core/engine.py:3608-3627` — 按拓扑序遍历节点出边 + 触发判定

**步骤 5：出边（unconditional）执行股票搬运**
- 源变更检测（虽然源肯定变了，但仍有这一步检查）
- 流转属性解析
- 恒等过滤（passed_codes = 全部源股票）
- 目标池更新（merge 或 replace）
- 源池更新（move 模式下移除）

**代码引用**：
- `core/engine.py:2476-2526` — `_filter_unconditional` 无条件边完整流程

**步骤 6：目标状态池标脏**
- 出边执行完后，调用 `_mark_dirty(tid)` 将目标状态池标记为脏
- 为更下游的变换单元级联传播做准备

### 1.2 conditional 入边触发了，unconditional 出边一定跟着执行吗？

**结论：在同一个 tick 内，几乎一定跟着执行，但有理论上的例外。**

**为什么几乎一定：**
1. 入边执行完后，条件节点被 `_mark_dirty(tid)` 标脏（`core/engine.py:3641`）
2. 主循环按拓扑序遍历，条件节点的深度 > 入边源节点的深度
3. 当遍历到条件节点的出边时，源节点（条件节点）已经是脏的
4. unconditional 边的 gate 永远通过（starttype=0 → always）
5. 所以 `triggered = fired AND (dirty OR data_dirty)` 一定为 True

**理论上的例外：**
- 如果主循环不是按拓扑序遍历，而是按边列表顺序，且出边排在入边前面——但代码是按拓扑序遍历的，所以不会发生
- 如果在入边和出边之间，条件节点的 dirty 标记被清除——但 `_clear_dirty()` 只在 tick 末尾调用（`core/engine.py:3643-3647`）

**代码引用**：
- `core/engine.py:3606-3641` — 按拓扑序遍历，边执行后标脏目标节点
- `core/engine.py:3643-3647` — tick 末才清脏

### 1.3 出边也有自己的触发条件吗？

**结论：出边没有独立的"业务触发条件"，只有"数据触发条件"。**

**详细分析：**

| 触发维度 | conditional 入边 | unconditional 出边 |
|---------|-----------------|-------------------|
| 时间触发（gate） | ✅ 有（starttype/begin/end/interval） | ❌ 无（starttype=0 即 always） |
| 持续期检查（cxtype） | ✅ 有 | ❌ 无（cxtype=0 即 never） |
| 源节点变化 | ✅ 是触发条件之一 | ✅ 是主要触发条件 |
| 行情数据变化 | ✅ 是触发条件之一 | ✅ 是触发条件之一 |
| 过滤计算 | ✅ 有（核心功能） | ❌ 无（恒等过滤） |

出边的"触发"本质上是**被动传播**——源节点变了就传过去，没有自己主动的"什么时候该算"的逻辑。

**代码引用**：
- `config/edge_semantics.json:11-15` — unconditional 边定义：`has_time_attributes: false`, `trigger_rule: "source_changed"`
- `config/edge_strategies.json:307-318` — pipeline_phases 中，replay_guard/tdx_guard/duration_guard 都只针对 conditional/formula_eval
- `core/engine.py:2490-2495` — `_filter_unconditional` 中的源变更检测

### 1.4 入边执行完，到出边执行，中间隔了多久？

**结论：在同一个 tick 内，几乎是"紧接着"执行的，中间只隔了同深度其他节点的边的处理。**

**详细分析：**

主循环的结构（`core/engine.py:3606-3641`）：
```python
sorted_nodes = compiled.topo_order  # 按深度升序
for nid in sorted_nodes:  # 遍历每个节点
    for edge in compiled.out_edges.get(nid, []):  # 遍历节点的每条出边
        # 触发判定 + 执行 + 标脏目标节点
```

假设有这样的拓扑：
```
节点A（深度0）→ 条件节点B（深度1）→ 节点C（深度2）
```

执行顺序：
1. 处理节点A的出边（入边）→ 执行 → 标脏节点B
2. 处理节点B的出边（出边）→ 执行 → 标脏节点C

中间隔了什么？
- 如果深度1只有节点B一个节点，那么就是紧接着的
- 如果深度1有多个节点，那么中间会处理其他同深度节点的出边

**时间尺度：**
- 同一个 tick 内（tick 间隔通常是 1 秒）
- 从代码执行时间看，可能是毫秒级甚至微秒级的间隔

**代码引用**：
- `core/engine.py:3606-3641` — 主循环结构
- `core/engine.py:386` — `_tick_interval` 默认 1 秒

---

## 2. 当前代码的执行路径

### 2.1 主循环按边遍历，一个变换单元的两条边怎么串起来的？

**答案：通过 dirty 标记 + 拓扑序 隐式串联。**

这是一个非常精妙的设计，但也非常不直观。

**串联机制详解：**

```
┌─────────────────────────────────────────────────────────────┐
│                      主循环（一个 tick）                      │
│                                                             │
│  按拓扑序遍历节点：                                           │
│    深度0节点 → 处理其出边（入边conditional）                  │
│                   ↓ 执行完后 _mark_dirty(tid)                │
│                 深度1节点（条件节点）变脏                     │
│                                                             │
│    深度1节点 → 处理其出边（出边unconditional）                │
│                   因为源节点（深度1节点）是脏的，所以触发      │
│                   ↓ 执行完后 _mark_dirty(tid)                │
│                 深度2节点（目标状态池）变脏                   │
│                                                             │
│    深度2节点 → 处理其出边（更下游的入边）...                  │
└─────────────────────────────────────────────────────────────┘
```

**关键机制：**
1. **拓扑序保证上游先处理**：深度小的节点先遍历，其出边先执行
2. **dirty 标记传递变化**：边执行完后标脏目标节点，下游边检查源节点是否脏
3. **隐式串联**：入边和出边之间没有显式的调用关系，完全靠 dirty 标记串起来

**代码引用**：
- `core/engine.py:3606-3641` — 主循环完整逻辑
- `core/engine.py:2062-2071` — `_mark_dirty` 标脏函数
- `core/engine.py:1234-1243` — 拓扑深度计算（Bellman-Ford 式）

### 2.2 一个变换单元在一个 tick 内会被执行几次？

**结论：标准情况下，一个变换单元在一个 tick 内被执行 1 次（表现为两条边各执行 1 次）。**

**详细分析：**

| 边 | 执行次数 | 原因 |
|----|---------|------|
| conditional 入边 | 1 次 | gate 通过 + (源脏 OR 数据脏) → 触发一次 |
| unconditional 出边 | 1 次 | 入边标脏了条件节点 → 源脏 → 触发一次 |
| **合计** | **2 次边执行** | 完成一个变换单元的功能 |

**有没有可能执行多次？**
- 在当前实现下，**不可能**。因为：
  1. 每条边在一个 tick 内只会被遍历一次（`processed_edges` 去重，`core/engine.py:3607-3612`）
  2. dirty 标记在 tick 末才清除（`core/engine.py:3647`）
  3. 没有循环（DAG 拓扑）

**代码引用**：
- `core/engine.py:3607-3612` — `processed_edges` 去重，每条边只处理一次
- `core/engine.py:3643-3647` — tick 末才清脏

---

## 3. 级联传播机制详解

### 3.1 dirty 级联传播机制怎么工作的？

**核心机制：边执行 → 标脏目标节点 → 下游边检查源节点是否脏 → 触发执行 → 继续标脏...**

这是一个经典的**增量更新**（incremental update）模式。

**完整流程：**

```
Tick 开始
  ↓
刷新 latest_tick → 置 data_dirty（如果行情变了）
  ↓
首次执行时标脏所有源节点（入度为0）
  ↓
按拓扑序遍历每个节点：
  遍历节点的每条出边：
    触发判定：fired AND (源节点脏 OR data_dirty)
      ↓ 触发
    执行边（过滤 + 传播）
      ↓
    标脏目标节点（_mark_dirty(tid)）
      ↓
    （下游节点的出边在遍历到该节点时，会因为源脏而触发）
  ↓
Tick 结束 → 清除所有 dirty 标记（_clear_dirty）
```

**三个脏源：**
1. **node_dirty（节点脏）**：节点的股票列表变化了（`_dirty_nodes` 集合）
2. **data_dirty（数据脏）**：行情数据变化了（`_data_dirty` 布尔值）
3. **edge_fired（边时间触发）**：边的时间 gate 通过了（`_edge_fired` 字典）

**触发公式**（`core/engine.py:3627`）：
```
triggered = edge_fired AND (node_dirty[sid] OR data_dirty)
```

**代码引用**：
- `core/engine.py:3596-3647` — `_run_tick_event_driven` 完整主循环
- `core/engine.py:2062-2093` — dirty 相关函数（_mark_dirty/_is_dirty/_clear_dirty）
- `core/engine.py:2095-2115` — `_refresh_latest_tick` 置 data_dirty

### 3.2 同一个 tick 内，入边执行完，源变更检测触发出边，出边又执行——这是级联吗？

**结论：是级联，而且是 dirty 驱动的级联传播。**

但需要区分两个层面的"级联"：

| 层面 | 含义 | 代码位置 |
|------|------|---------|
| **变换单元内的级联** | 入边 → 条件节点脏 → 出边 | `core/engine.py:3641` 标脏 + 下游边检查 |
| **变换单元间的级联** | 上游变换单元的出边 → 下游节点脏 → 下游变换单元的入边 | 同样的机制，跨单元 |

这两个层面的级联用的是**同一套机制**——dirty 标记 + 拓扑序遍历。

**变换单元视角下的级联链：**
```
变换单元1（入边+条件节点+出边）
    ↓ 出边执行完标脏目标节点
变换单元2（入边+条件节点+出边）
    ↓ 出边执行完标脏目标节点
变换单元3...
```

**代码引用**：
- `core/engine.py:3637-3641` — 边执行后标脏目标节点，这是级联的驱动力
- `core/engine.py:3626-3627` — 触发判定检查源节点是否脏，这是级联的接收端

### 3.3 为什么用 dirty 级联而不是直接调用？

**设计意图推测**（基于代码分析）：

1. **通用性**：同一套机制既支持变换单元内的级联，也支持变换单元间的级联
2. **解耦**：边之间不需要知道彼此的存在，只需要知道"源节点是否变了"
3. **增量更新**：只有变化的节点才会触发下游重算，性能更好
4. **拓扑灵活性**：支持任意 DAG 拓扑，不局限于标准变换单元结构

**代价**：
- 概念不直观：入边和出边的关系是隐式的，不是显式的
- 调试困难：级联传播路径不明显，需要追踪 dirty 标记

---

## 4. 按变换单元执行 vs 按边执行

### 4.1 概念对比

| 维度 | 按边执行（当前） | 按变换单元执行（假设） |
|------|----------------|---------------------|
| **基本执行单元** | 边（edge） | 变换单元（transformation unit） |
| **入边和出边的关系** | 隐式（通过 dirty 标记串联） | 显式（同一个单元内的两个步骤） |
| **概念模型** | 图计算（节点+边） | 变换链（单元+单元） |
| **灵活性** | 高（支持任意拓扑） | 中（主要支持标准三元组，需要 fallback） |
| **直观性** | 低（需要理解 dirty 级联） | 高（一个单元就是一次完整变换） |

### 4.2 代码对比

**当前按边执行的代码结构**（`core/engine.py:3578-3648`）：
```python
def _run_tick_event_driven(self, pool_config, node_stocks, ...):
    compiled = self._get_compiled(pool_config)
    self._refresh_latest_tick(current_bar_data)
    
    # 首次执行标脏源节点
    if self._first_run:
        for nid in compiled.topo_order:
            if not compiled.in_edges.get(nid):
                self._mark_dirty(nid)
    
    # 按拓扑序遍历所有边
    sorted_nodes = compiled.topo_order
    processed_edges = set()
    for nid in sorted_nodes:
        for edge in compiled.out_edges.get(nid, []):
            eid = edge.get('id')
            if eid in processed_edges:
                continue
            processed_edges.add(eid)
            
            # 触发判定
            fired = self._should_trigger_edge(edge, ...)
            triggered = fired and (self._is_dirty(sid) or self._is_data_dirty())
            if not triggered:
                continue
            
            # 执行边
            ctx = self._build_pipeline_ctx(...)
            self._process_edge_pipeline(ctx)
            
            # 标脏目标节点（级联传播）
            self._mark_dirty(tid)
    
    # tick 末清脏
    self._clear_dirty()
```

**假设按变换单元执行的代码结构**（伪代码）：
```python
def _run_tick_event_driven(self, pool_config, node_stocks, ...):
    compiled = self._get_compiled(pool_config)
    self._refresh_latest_tick(current_bar_data)
    
    # 按拓扑序遍历所有变换单元
    for unit in compiled.units:  # 按单元源深度排序
        in_edge = unit['in_edge']
        hub_id = unit['hub_id']
        out_edge = unit['out_edge']
        
        # 入边触发判定
        fired = self._should_trigger_edge(in_edge, ...)
        in_sid = in_edge_ctx['sid']
        triggered = fired and (self._is_dirty(in_sid) or self._is_data_dirty())
        if not triggered:
            continue
        
        # 步骤1：入边执行（条件过滤）
        in_ctx = self._build_pipeline_ctx(in_edge, ...)
        self._filter_conditional(in_ctx)  # 结果写入条件节点
        
        # 步骤2：出边执行（股票搬运）—— 不需要触发判定，直接执行
        out_ctx = self._build_pipeline_ctx(out_edge, ...)
        self._filter_unconditional(out_ctx)  # 从条件节点搬到目标池
        
        # 标脏目标节点（为下游单元级联）
        out_tid = out_edge_ctx['tid']
        self._mark_dirty(out_tid)
    
    # 处理 standalone_edges（非标准三元组的边）
    for edge in compiled.standalone_edges:
        ...  # 按原来的边方式处理
    
    # tick 末清脏
    self._clear_dirty()
```

### 4.3 性能对比

| 维度 | 按边执行（当前） | 按变换单元执行（假设） |
|------|----------------|---------------------|
| **触发判定次数** | 每条边都判定一次 | 每个单元只判定一次（入边） |
| **dirty 检查次数** | 每条边都检查 | 每个单元只检查一次 |
| **pipeline 构建次数** | 每条边都构建一次 ctx | 每个单元构建两次 ctx（但可以优化） |
| **总体性能差异** | 基准 | 理论上略好（减少一次 gate 判定和 dirty 检查） |

**性能差异估算**：
- 假设一个变换单元 = 2 条边
- 按边执行：2 次触发判定（gate + dirty 检查）
- 按变换单元执行：1 次触发判定 + 1 次直接执行
- 节省约 50% 的触发判定开销
- 但触发判定本身开销不大，实际性能提升可能有限

### 4.4 等价性分析

**对于标准变换单元（1入边+1条件节点+1出边）：**

**结论：两种执行方式在功能上完全等价。**

为什么等价？
1. 入边的触发条件完全一样（gate + 源脏/数据脏）
2. 入边的执行逻辑完全一样（条件过滤）
3. 出边的执行逻辑完全一样（股票搬运）
4. 出边在两种方式下都会被执行（当前是通过 dirty 级联，重构后是直接执行）
5. 最终目标节点的状态完全一样

**Edge case 分析：**

| Edge case | 按边执行 | 按变换单元执行 | 是否等价 |
|-----------|---------|--------------|---------|
| 条件节点有多个入边 | 每个入边独立触发，分别写入条件节点 | 需要定义"多入边"的语义 | ⚠️ 待确认 |
| 条件节点有多个出边 | 每个出边独立触发，分别从条件节点读出 | 需要定义"多出边"的语义 | ⚠️ 待确认 |
| 条件节点直连条件节点 | 拓扑校验会告警（`engine.py:2267-2268`），但仍可执行 | 不构成标准变换单元，走 standalone | ✅ 等价（都走 fallback） |
| 数据源节点直接连状态池 | 不构成标准变换单元，走 standalone | 不构成标准变换单元，走 standalone | ✅ 等价 |
| 公式边（formula_eval） | 按 conditional 类似的方式处理 | 需要单独归类或走 standalone | ✅ 等价 |

**代码引用**：
- `core/engine.py:2267-2273` — 拓扑校验：条件节点直连告警，入边/出边数各1条才正常
- `core/engine.py:2240-2246` — 变换单元分组：只有入边数=1且出边数=1时才配对

### 4.5 现有代码为什么按边执行？

**推测原因**（基于代码考古和分析）：

1. **历史原因**：最初的模型就是"节点+边"的图计算模型，变换单元是后来才抽象出来的概念
   - 证据：`_group_transformation_units` 函数（`engine.py:2217-2247`）是后加的，它的输出 `units` 和 `standalone_edges` 目前只用于构建 `processing_plan`，运行时仍按边遍历

2. **灵活性**：按边执行天然支持任意 DAG 拓扑，不局限于标准变换单元
   - 证据：代码中有 `standalone_edges` 回退路径（`engine.py:2246`）

3. **dirty 级联是通用机制**：dirty 标记 + 拓扑序遍历是一种通用的增量更新机制，不仅适用于变换单元
   - 证据：node_dirty/data_dirty/edge_fired 三个脏源（`engine.py:3627`）是通用设计

4. **实现简单**：按边执行的逻辑非常统一——所有边都走同一套流程
   - 证据：主循环代码很简洁，没有特殊 casing

**代码引用**：
- `core/engine.py:1245-1257` — 编译期分组了变换单元，但处理计划仍是按边展开的
- `core/engine.py:3606-3641` — 运行时主循环完全按边遍历，没有变换单元的概念

---

## 5. 变换单元类型体系

### 5.1 有多少种变换单元？

**从 `transformation_unit_strategies` 配置看，有 3 种已定义的变换单元 + 1 个 fallback：**

| 变换单元类型 | 枢纽节点类型 | 入边 action | 条件 action | 出边 action | 入边 filter_type | 出边 filter_type |
|-------------|-------------|------------|------------|------------|-----------------|-----------------|
| **tdx_condition** | tdx_condition | pass_through | tdx_condition_eval | transfer_between_pools | conditional | unconditional |
| **transfer_condition** | transfer_condition | pass_through | apply_filter | transfer_between_pools | conditional | unconditional |
| **dzh_condition_pool** | dzh_condition_pool | pass_through | apply_dzh_filter | transfer_between_pools | conditional | unconditional |
| **_fallback** | 其他 | - | - | - | - | - |

**代码引用**：
- `config/edge_strategies.json:197-226` — `transformation_unit_strategies` 配置

### 5.2 每种变换单元的执行流程有什么不同？

**共同点（结构相同）：**
1. 入边都是 conditional 类型
2. 出边都是 unconditional 类型
3. 都遵循"入边透传 → 条件计算 → 出边转移"的三段式结构

**不同点（条件计算不同）：**

| 变换单元类型 | 条件计算方式 | 说明 |
|-------------|-------------|------|
| tdx_condition | tdx_condition_eval | TDX 公式评估（通达信公式） |
| transfer_condition | apply_filter | nset 分派过滤（DZH 条件） |
| dzh_condition_pool | apply_dzh_filter | DZH 条件池过滤（公式批量求值） |

**入边的"pass_through"是什么意思？**

从配置看，入边的 action 是 `pass_through`，但运行时入边实际走的是 `_filter_conditional` 逻辑（nset 策略解析 + 执行分派）。

推测 `transformation_unit_strategies` 配置是**概念层的描述**，不是运行时实际调用的路径。运行时实际走的还是边级的策略。

**代码引用**：
- `config/edge_strategies.json:184-196` — `action_ops` 实际的 action 映射
- `config/edge_strategies.json:227-248` — `edge_filter_registry` filter 类型注册表

### 5.3 能不能用表驱动的方式统一所有变换单元？

**结论：完全可以，而且现有代码已经朝这个方向走了。**

**现有表驱动基础：**
1. `edge_filter_registry` — filter_type → op 映射（`edge_strategies.json:227-243`）
2. `filter_ops` — op → method 映射（`edge_strategies.json:244-248`）
3. `transformations` — 每种 filter_type 的 transform 配置（`edge_strategies.json:249-270`）
4. `pipeline_phases` — 流水线阶段定义（`edge_strategies.json:304-337`）
5. `phase_ops` — 阶段 → method 映射（`edge_strategies.json:338-353`）

**变换单元的表驱动设计（假设）：**

```json
{
  "transformation_units": {
    "tdx_condition": {
      "hub_type": "tdx_condition",
      "phases": [
        {"step": "in_edge_gate", "op": "conditional_gate"},
        {"step": "in_edge_filter", "op": "tdx_condition_eval"},
        {"step": "out_edge_propagate", "op": "unconditional_transfer"}
      ]
    },
    "transfer_condition": {
      "hub_type": "transfer_condition",
      "phases": [
        {"step": "in_edge_gate", "op": "conditional_gate"},
        {"step": "in_edge_filter", "op": "apply_filter"},
        {"step": "out_edge_propagate", "op": "unconditional_transfer"}
      ]
    }
  }
}
```

**技术可行性**：
- ✅ 三种变换单元的结构完全相同（三段式）
- ✅ 只有中间的"条件计算"不同，可以通过表驱动分派
- ✅ 现有代码已经有大量表驱动基础设施

---

## 6. 重构可能性分析

### 6.1 收益是什么？

**1. 概念更清晰**
- 执行单元从"边"变成"变换单元"，更符合业务认知
- 入边和出边的关系从"隐式 dirty 级联"变成"显式同单元"
- 降低理解成本，新手上手更快

**2. 代码更简洁**
- 减少一次触发判定（出边不需要 gate 检查）
- 减少一次 dirty 检查（出边不需要检查源节点是否脏）
- 变换单元的执行逻辑内聚，不再分散在两条边里

**3. 调试更容易**
- 可以在变换单元级别打断点、加日志
- 级联路径更清晰（单元→单元，而不是边→边）
- 更容易追踪"哪个变换单元导致了变化"

**4. 优化空间更大**
- 变换单元级别的缓存（输入→输出的映射）
- 变换单元级别的并行（独立单元可以并行执行）
- 变换单元级别的增量更新（更细粒度的脏标记）

### 6.2 风险是什么？

**1. 改动量大**
- 主循环需要重写（从按边遍历改成按单元遍历）
- 触发逻辑需要调整（出边不再需要触发判定）
- 缓存逻辑可能需要调整（从边级缓存改成单元级缓存）
- 测试用例需要大量回归

**2. 破坏现有功能的风险**
- standalone_edges 的处理需要保证正确
- 非标准拓扑（多入边、多出边、条件节点直连）需要正确回退
- 回放模式、实时模式等不同模式下的行为需要保持一致

**3. 性能回退风险**
- 虽然理论上性能应该提升，但重构过程中可能引入低效代码
- dirty 级联机制经过了大量优化，重构后可能失去这些优化

**4. 调试成本**
- 重构初期 bug 会比较多
- 需要大量时间验证等价性
- 现有开发者需要适应新的执行模型

### 6.3 改动有多大？

**按改动范围估算：**

| 模块 | 改动量 | 说明 |
|------|--------|------|
| 主循环（_run_tick_event_driven） | 大 | 从按边遍历改成按单元遍历 |
| 触发判定逻辑 | 中 | 出边不再需要触发判定 |
| 编译期（_compile_pool） | 小 | 已经有 units 数据了，可能需要补充一些预计算 |
| 缓存逻辑 | 中 | 需要确认单元级缓存和边级缓存的关系 |
| 测试 | 大 | 需要大量回归测试验证等价性 |
| 文档 | 中 | 更新架构文档和开发指南 |

**总改动量估算：约 2-3 周的工作量**（假设 1 人，含测试）

### 6.4 第一步怎么改？

**推荐策略：渐进式重构，先验证等价性，再逐步切换**

**第一步：增加变换单元执行模式（不删除原有逻辑）**
1. 新增 `_run_tick_unit_driven` 方法，实现按变换单元执行
2. 保留 `_run_tick_event_driven` 作为后备
3. 增加一个开关（配置项或环境变量）控制使用哪种模式

**第二步：建立等价性测试**
1. 准备一组测试用例（覆盖各种拓扑和场景）
2. 两种模式都跑一遍，比较输出是否完全一致
3. 记录所有不一致的 case，分析原因并修复

**第三步：逐步切换默认模式**
1. 等价性测试通过后，把默认模式改成按变换单元执行
2. 保留按边执行模式作为 debug 选项
3. 观察线上运行情况，收集性能数据

**第四步：清理旧代码**
1. 确认按变换单元执行稳定后，删除按边执行的代码
2. 清理相关的 dead code
3. 更新文档

**为什么这样做？**
- 风险可控：任何时候都可以回退到按边执行
- 可验证：等价性测试保证功能不变
- 渐进式：不需要一次性大改，可以分步骤推进

---

## 7. 对股票池本质理解的更新

> **股票池是一组"状态池变换单元"按拓扑序排列的有向无环图，每个变换单元在一个 tick 内通过"入边条件过滤 + 出边结果搬运"的两段式操作完成一次状态变换，变换单元之间通过 dirty 标记实现级联传播，当前的按边执行模型是通过 dirty 标记隐式串联起变换单元的两条边，概念上等价于按变换单元执行。**

---

**文档结束**（v3.1 — 执行语义与重构可能性分析版）
