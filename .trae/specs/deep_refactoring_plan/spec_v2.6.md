# 股票池系统代码理解笔记 v2.6（第二版）

> 纯理解笔记，只描述代码里是什么，不做设计建议。
> 所有结论均有代码引用（文件+行号）。

---

## 1. 上一轮错误纠正（8条）

### 错误1：事件发射路径不是两条，只有一条

**之前理解**：事件发射有两条路径——post hook 里直接发射 + tick 末批量发射。

**代码里的真相**：只有一条完整路径。post 阶段（`_post_handle_new_entries`）只是往 `tevs` 列表 append 一条转移事件记录，**不发射任何事件**。所有事件（ENTER/EXIT/TIMEOUT 等领域事件 + BUY/SELL 信号）统一在 tick 末通过 `_emit_transfer_events` 批量发射。

证据：
- `_post_handle_new_entries` 只做 `tevs.append(...)`，不调用任何 emit/push 方法：`engine.py:2974-3005`
- tick 主流程顺序：`_run_tick_event_driven` → `_emit_transfer_events(prev, node_stocks, tevs)`：`engine.py:3660-3661`
- `tevs` 是 `_run_tick_event_driven` 的入参和返回值，贯穿整个 tick：`engine.py:3578-3648`

---

### 错误2：gate 评估器不是完全表驱动，live 和 simulation 共用同一套硬编码逻辑

**之前理解**：gate 评估器已经完全表驱动了，live 和 simulation 用不同的表。

**代码里的真相**：
- live 和 simulation 模式共用同一套硬编码逻辑：`_tdx_should_execute`（starttype 守门）+ `_tdx_check_duration`（cxtype 守门）
- 只有 replay 模式额外叠加了 `_should_fire_flow_replay`（begin/end/interval 三重守门）
- `timing.json:gate_evaluator` 表（live_gate/replay_gate/virtual_gate）目前只是声明，没有被实际 dispatch 使用；`_eval_gate` 方法直接委托给 `_should_trigger_edge`，标记为 `[DEPRECATED]`

证据：
- `_should_trigger_edge` 内部分派：`engine.py:1757-1785`
  - replay 模式：`_should_fire_flow_replay` + `_tdx_should_execute` + `_tdx_check_duration`
  - 其他模式：`_tdx_should_execute` + `_tdx_check_duration`
- `_eval_gate` 标记 deprecated，直接委托：`engine.py:1787-1795`
- gate_evaluator 表定义但未消费：`timing.json:134-149`

---

### 错误3：cxtype 和 end_type 是两个完全不同的体系

**之前理解**：cxtype 和 end_type 是同一个东西的不同叫法，都是"结束类型"。

**代码里的真相**：是两个完全独立的体系，用于不同模式：

| 维度 | cxtype | end_type |
|------|--------|----------|
| 所属模式 | live / simulation（实时） | replay（回放） |
| 语义 | 边的**持续时长类型**：边触发后持续多久有效 | flow 的**结束类型**：回放时 flow 何时停止 |
| 取值 | 0=forever, 1=duration, 2=once | 0=无限, 1=首次触发后endt秒, 2=只执行一次, 等 |
| 配置表 | `timing.json:cxtype_rules` | `timing.json:simulator.end_type_handlers` |
| 求值方法 | `_tdx_check_duration` | `_should_fire_flow_replay` 内 end 守门 |
| 运行时状态表 | `_flow_duration_starts` / `_flow_exec_counts` | `_flow_first_fire_ts` / `_flow_last_fire_ts` |

证据：
- cxtype_rules 定义：`timing.json:13-28`
- end_type_handlers 定义：`timing.json:93-99`
- `_tdx_check_duration`（cxtype 守门）：`engine.py:1014-1030`
- `_should_fire_flow_replay`（begin/end/interval 三重守门）：`engine.py:1033-1093`

补充：starttype 和 begin_type 也是类似关系——starttype 用于实时模式的"开始时机"，begin_type 用于回放模式的"开始类型"。

---

### 错误4：无条件边也受 data_dirty 影响，语义是"外层触发门控 + 内层源变更检测"

**之前理解**：无条件边不受 data_dirty 影响，源节点变化就直接传播。

**代码里的真相**：无条件边同样经过外层统一的 `triggered` 判定，即 `triggered = fired AND (node_dirty[sid] OR data_dirty)`。但在 `_filter_unconditional` 内部还有一层源变更检测，如果源节点股票集合没变化就跳过。

两层过滤的语义：
1. **外层（通用）**：`triggered = edge_fired AND (node_dirty[sid] OR data_dirty)`——所有边统一的触发门控，`engine.py:3625-3629`
2. **内层（unconditional 特有）**：源节点股票集合是否变化——如果没变化就 return False 跳过，`engine.py:2490-2495`

所以 data_dirty 会让无条件边进入执行，但内层的源变更检测会把它拦下来。data_dirty 对无条件边的实际效果是：**即使源节点股票没变化，只要行情变了，也会进入 _filter_unconditional 函数（但很快被源变更检测拦下）**。

证据：
- 外层 triggered 判定（所有边统一）：`engine.py:3621-3629`
- `_filter_unconditional` 内层源变更检测：`engine.py:2490-2495`

---

### 错误5：变换单元（三元组）不在核心执行路径中，只在编译期分组用

**之前理解**：变换单元（条件边+枢纽节点+无条件边）是核心执行路径，股票池按三元组为单位执行。

**代码里的真相**：核心执行路径 `_run_tick_event_driven` 是**按边遍历**的，遍历顺序是拓扑序节点的出边（`compiled.out_edges`），不是按三元组执行。

三元组的用途：
- 编译期 `_prepare_topology` 中分组，生成 `units` 和 `standalone_edges`：`engine.py:1245-1246`
- 用于构建 `processing_plan`：`engine.py:1255-1257`
- 但 `_run_tick_event_driven` 不使用 `processing_plan`，用的是 `compiled.out_edges` + `compiled.topo_order`：`engine.py:3606-3613`

三元组目前更像是一个**编译期分析概念**和**拓扑验证辅助工具**，不是运行期执行单元。

证据：
- `_run_tick_event_driven` 按 out_edges 遍历：`engine.py:3608-3613`
- `_group_transformation_units` 只在 `_prepare_topology` 中调用：`engine.py:1245-1246`
- processing_plan 存在但运行期核心循环不消费：`engine.py:261-262`（CompiledSchedule 定义）

---

### 错误6：不存在叫 post_hook 的机制，post 处理是 pipeline 的一个阶段

**之前理解**：有 post_hook 钩子机制，边执行后自动调用。

**代码里的真相**：没有叫 post_hook 的机制。post 处理是 pipeline 的 `post_process` 阶段，由 `_process_edge_pipeline` 按 pipeline 相位顺序调用。具体的"post"函数有：
- `_post_handle_new_entries`：检测新入池股票，建 tracker，往 tevs append 事件记录
- `_post_apply_ttl`：TTL 过期检查

这些函数通过 `edge_strategies.json` 的策略配置调用，不是钩子机制。

证据：
- 搜索 `post_hook` 无结果（engine.py 内）
- pipeline 相位定义包含 `post_process`：`engine.py:295`
- `_post_handle_new_entries`：`engine.py:2974-3005`
- `_post_apply_ttl`：`engine.py:3007-3013`

---

### 错误7：_node_snapshots 不是 prev 快照，是增量更新用的脏检测快照

**之前理解**：`_node_snapshots` 和 `prev`（`_last_snapshot`）是一回事，都是上一 tick 的快照。

**代码里的真相**：是两套不同的快照，用途完全不同。详见"遗漏补全"第2条。

---

### 错误8：signal 和 event 不是一回事，是两个独立体系

**之前理解**：signal 和 event 是同一个东西的不同叫法。

**代码里的真相**：是两个完全独立的体系，有不同的配置表、不同的队列、不同的用途。详见"遗漏补全"第5条。

---

## 2. 上一轮遗漏补全（7条）

### 遗漏1：CompiledSchedule 编译期缓存——编译期到底算了什么、存在哪里

**编译期入口**：`_compile_pool(pool_config)` → `engine.py:1354`

**缓存机制**：
- 缓存 key = `pool_config` 内容的 md5 哈希（json.dumps sort_keys + default=str）：`engine.py:1370-1371`
- 缓存存储：`self._compiled_cache: Dict[str, CompiledSchedule]`：`engine.py:460`
- 命中缓存直接返回，不重新编译：`engine.py:1372-1374`

**编译期预计算的内容**（CompiledSchedule 字段）：

| 字段 | 内容 | 作用 |
|------|------|------|
| `nodes` | 节点字典 {nid: node} | 运行期直接读取 |
| `edges` | 边列表 | 运行期直接读取 |
| `edge_index` | 边索引 {eid: edge} | O(1) 查找边 |
| `depths` | 节点深度字典 {nid: depth} | longest-path 算法结果 |
| `topo_order` | 节点 id 列表（按深度升序） | 预定执行顺序，运行期不重算 |
| `processing_plan` | [(edge, filter_type), ...] | 按源深度排序的处理计划 |
| `out_edges` | 节点出边邻接表 {nid: [edge, ...]} | 运行期遍历出边 |
| `in_edges` | 节点入边邻接表 {nid: [edge, ...]} | 入度分析 |
| `units` | 变换单元三元组列表 | 编译期分组结果 |
| `standalone_edges` | 独立边列表 | 无法配对的边 |
| `edge_ctx` | {eid: {sid,tid,sn,tn,st,tt,ep,eid,edge,filter_type}} | 预计算的边上下文 + filter_type，运行期不用再调 _resolve_edge_context |
| `edge_timing` | {eid: {starttype,cxtype,starttime,cxtime,starttype_rule,cxtype_rule,primitive}} | 编译期时机门控规则表 |
| `edge_filter_spec` | {eid: {nset,accode,noperate,dispatch_mask,gateway,engine}\|{filter_type}} | 编译期筛选分派规则 |
| `edge_flow_spec` | {eid: {tran,emptyps,attr,propagate_mode,structural_mode}} | 编译期状态流转规则 |
| `edge_action_spec` | {eid: {bsavehis,bsound,btip,bsavetoblock,blockfile,soundfile,nsoundtype}} | 编译期 callback 副作用规则 |
| `edge_ttl_spec` | {eid: {bdel,ndelnum,ndeltype,ttl_sec}\|{bdel:0,ttl_sec:0}} | 编译期 TTL 超时淘汰规则 |

证据：
- CompiledSchedule 类定义及字段注释：`engine.py:227-272`
- `_compile_pool` 编译流程：`engine.py:1354-1431`
- `_compile_edge_spec` 单条边 5 维 spec 编译：`engine.py:1433-1553`

**运行期使用方式**：`_run_tick_event_driven` 开头调用 `self._get_compiled(pool_config)` 获取缓存的 CompiledSchedule，后续全部从 compiled 对象读取预计算结果，不再重算拓扑/端点/filter_type。

---

### 遗漏2：_update_node_snapshot 自动标脏机制

**函数**：`_update_node_snapshot(nid, stocks)` → `engine.py:2188-2207`

**机制**：
1. 将传入的 stocks 转为 `frozenset(股票代码)` 作为新快照
2. 与 `self._node_snapshots[nid]` 中的旧快照比较
3. 如果不同（或旧快照不存在）：
   - 更新 `self._node_snapshots[nid] = new_snapshot`
   - 自动调用 `self._mark_dirty(nid)` 标记该节点为脏
   - 返回 True
4. 如果相同，返回 False

**调用位置**：
- `_tick()` 开头：对所有节点调用一次，检测外部注入导致的节点股票变化：`engine.py:3658-3659`
- `_run_tick_event_driven` 中边执行后：对目标节点调用，检测边执行导致的目标节点变化：`engine.py:3638-3639`

**注意**：边执行后还会**显式**调用 `self._mark_dirty(tid)` 标脏（`engine.py:3641`），即使 `_update_node_snapshot` 内部已经标过一次。这是冗余的但无害。

---

### 遗漏3：两套快照机制——prev vs _node_snapshots

代码里存在两套完全独立的快照，用途不同：

#### 第一套：_last_snapshot（全量 tick 快照，prev）
- **类型**：`{nid: frozenset(codes)}` 全节点全量快照
- **更新时机**：每个 tick 末统一更新（`_run_tick_event_driven` Step 4）：`engine.py:3644`
- **清除时机**：无，被下一个 tick 覆盖
- **用途**：
  - 传给 pipeline ctx 作为 `prev_snapshot`，供 filter 阶段做源变更检测
  - `_filter_unconditional` 用它判断源节点股票集合是否变化：`engine.py:2491-2495`
  - `_filter_conditional` 用它 + prev_bar_hash 做缓存 key：`engine.py:2566-2569`
- **对应变量名**：`self._last_snapshot` / `prev`（tick 开头的局部变量）

#### 第二套：_node_snapshots（增量节点快照）
- **类型**：`{nid: frozenset(codes)}` 按节点单独更新
- **更新时机**：
  - tick 开头对每个节点调用 `_update_node_snapshot` 时更新
  - 边执行后对目标节点调用 `_update_node_snapshot` 时更新
- **清除时机**：tick 末 `_clear_dirty()` 清脏节点标记，但不清空 _node_snapshots 本身
- **用途**：
  - 增量脏检测：节点股票变化时自动标脏（`_mark_dirty`）
  - 触发级联：源节点脏 → 边触发 → 目标节点标脏 → 下一层边触发
- **对应变量名**：`self._node_snapshots` / `_dirty_nodes`

#### 两者关系
- 都是 `{nid: frozenset(codes)}` 结构
- 但更新时机和用途完全不同
- `_last_snapshot` 是"上一 tick 结束时"的状态，用于本 tick 内的变更检测
- `_node_snapshots` 是"本 tick 内最新"的状态，用于增量脏标记和级联传播

证据：
- `_snapshot_node_stocks`（生成 _last_snapshot）：`engine.py:2031-2035`
- `_update_node_snapshot`（更新 _node_snapshots）：`engine.py:2188-2207`
- `_last_snapshot` 在 tick 末赋值：`engine.py:3644`
- `_node_snapshots` 在 tick 开头逐节点更新：`engine.py:3658-3659`

---

### 遗漏4：变换单元（三元组）——编译期分组概念，运行期核心路径不直接使用

**定义**：变换单元 = 条件转移边（入边）+ 枢纽条件节点 + 无条件转移边（出边），将一个状态池经条件计算变换为下一个状态池。

**配置**：`edge_semantics.json:transformation_unit`

**编译期分组逻辑**（`_group_transformation_units`，`engine.py:2217-2247`）：
1. 从 `edge_semantics.json` 读取 `hub_node_types`（枢纽节点类型）
2. 遍历所有边，按源/目标节点类型分类：
   - 目标是枢纽节点 → 入边（in_edge）
   - 源是枢纽节点 → 出边（out_edge）
3. 对每个枢纽节点，如果恰好有 1 条入边 + 1 条出边，则组成一个三元组
4. 无法配对的边放入 `standalone_edges`

**当前实际用途**：
- 构建 `processing_plan` 时使用：`engine.py:1289-1331`
- 拓扑验证时使用：`_validate_pool_topology`
- **但核心执行循环 `_run_tick_event_driven` 不使用三元组，直接按边遍历**

**注意**：edge_semantics.json 中还声明了 `change_detection.unit_level = true`（单元级变更检测），但代码中目前没有看到实际的单元级缓存实现，属于配置声明超前于代码实现。

---

### 遗漏5：signal 与 event 的区别——两个独立体系

**两个独立的配置表**：
- event：`config/event_rules.json`
- signal：`config/signal_rules.json`

**两个独立的队列**：
- event：`self._event_queue`（asyncio.Queue） + `self._alert_events`
- signal：`self._signal_queue`（asyncio.Queue） + `self._signal_events`

**事件（Event）**：
- 类型：ENTER / EXIT / TIMEOUT
- 触发：股票进入/离开池、TTL 超时等池状态变更
- 配置表：`event_rules.json` → `events` 字段
- detail 映射：`event_rules.json` → `detail_mapping` 字段
- 发射方法：`_push_event(et, code, pool_id, detail)` → `engine.py:3078-3080`
- 用途：系统内部事件通知、UI 高亮、历史记录等

**信号（Signal）**：
- 类型：BUY / SELL
- 触发：股票进入目标池（BUY）、离开目标池或 TTL 超时（SELL）
- 配置表：`signal_rules.json` → `signals` 字段
- 字段映射：`signal_rules.json` → `field_mapping` 字段
- 发射方法：`_push_signal(sig_type, code, price, ts, pool_id, cond, ...)` → `engine.py:3081-3085`
- 用途：交易信号输出，供外部交易系统消费

**发射流程**：两者都在 `_emit_domain_event` 中统一发射，同一个领域事件（如 pool_enter）会同时触发 event 和 signal：
- 遍历 `event_defs` 发射事件：`engine.py:3231-3237`
- 遍历 `signal_defs` 发射信号：`engine.py:3244-3252`

证据：
- event_rules.json：`event_rules.json:1-150`
- signal_rules.json：`signal_rules.json:1-131`
- `_push_event`：`engine.py:3078-3080`
- `_push_signal`：`engine.py:3081-3085`
- `_emit_domain_event` 中同时发射 event 和 signal：`engine.py:3197-3252`

---

### 遗漏6：脏标记体系——node_dirty / data_dirty / edge_fired 三级脏标记

**三级脏标记**：

| 标记 | 类型 | 置位时机 | 清除时机 | 用途 |
|------|------|----------|----------|------|
| `_dirty_nodes` | set of nid | 节点股票变化时（`_mark_dirty`） | tick 末 `_clear_dirty()` | 标记哪些节点的股票集合变了 |
| `_data_dirty` | bool | 行情数据变化时（`_refresh_latest_tick` 中 hash 变化） | tick 末 `_clear_dirty()` | 标记行情数据是否更新 |
| `_edge_fired` | dict {eid: bool} | 每 tick 对每条边调用 `_mark_edge_fired(eid, fired)` | tick 末 `_clear_edge_fired()` | 记录本 tick 边的时间触发状态 |

**触发判定公式**（Phase 2）：
```
triggered = edge_fired[eid] AND (node_dirty[sid] OR data_dirty)
```
即：时间条件满足 **且**（源节点股票变了 **或** 行情数据变了）。

证据：
- `_dirty_nodes` + `_mark_dirty`：`engine.py:2062-2071`
- `_data_dirty` + `_refresh_latest_tick`：`engine.py:2095-2115`
- `_edge_fired` + `_mark_edge_fired`：`engine.py:2117-2123`
- 触发判定：`engine.py:3621-3627`
- 统一清脏：`engine.py:2084-2093`

---

### 遗漏7：首次执行（_first_run）的特殊处理

**`_first_run` 标志**：
- 初始值：True（`engine.py:418`）
- 置 False：tick 末（`engine.py:3646`）

**首次执行的特殊行为**：
1. **源节点标脏**：首次执行时对所有入度为 0 的源节点标脏，确保初始股票能传播下去：`engine.py:3600-3603`
2. **跳过源变更检测**：`_filter_unconditional` 和 `_filter_conditional` 中，如果 `_first_run` 为 True，跳过源节点变更检测（因为没有 prev 快照可比）：
   - unconditional：`engine.py:2491`
   - conditional：`engine.py:2566`
3. **prev_snapshot 为 None**：首次执行时 `_last_snapshot` 是 None，所有源变更检测都返回 True。

证据：
- 首次执行标脏源节点：`engine.py:3600-3603`
- _first_run 置 False：`engine.py:3646`
- _filter_unconditional 中跳过源变更检测：`engine.py:2491`
- _last_snapshot 初始为 None：`engine.py:412`

---

## 3. 编译期 vs 运行期的完整边界

### 编译期（一次性，结果缓存）

**入口**：`_compile_pool(pool_config)` → 结果存入 `self._compiled_cache`

**编译期计算并缓存**：
- 拓扑结构：nodes, edges, edge_index, depths, topo_order, out_edges, in_edges
- 变换单元分组：units, standalone_edges
- 处理计划：processing_plan
- 边上下文：edge_ctx（sid/tid/sn/tn/st/tt/ep/eid + filter_type）
- 5 维 spec 表：
  - edge_timing（时机门控规则）
  - edge_filter_spec（筛选分派规则）
  - edge_flow_spec（状态流转规则）
  - edge_action_spec（callback 副作用规则）
  - edge_ttl_spec（TTL 淘汰规则）

**编译期只读**：
- pool_config（nodes + edges）
- 配置表：timing.json, dispatch.json, engines.json, edge_strategies.json, tdx_psatt.json, edge_semantics.json
- 不依赖任何运行时状态（时间、股票、行情等）

### 运行期（每 tick 执行）

**运行期只读（从 CompiledSchedule 读取）**：
- topo_order（执行顺序）
- out_edges（遍历出边）
- edge_ctx（边上下文 + filter_type）
- edge_flow_spec（流转属性：mv/io/fa）
- edge_filter_spec（filter 策略）
- 其他预计算 spec 表

**运行期修改**：
- node_stocks（节点股票列表）——核心状态
- _node_snapshots（节点快照，用于脏检测）
- _dirty_nodes（脏节点集合）
- _data_dirty（数据脏标记）
- _edge_fired（边触发状态）
- _last_snapshot（全量快照，tick 末更新）
- _last_bar_hash（行情 hash）
- tevs（转移事件列表）
- _trackers（持仓跟踪器）
- _flow_exec_counts / _flow_duration_starts / _flow_first_fire_ts / _flow_last_fire_ts（flow 计时状态）
- _signal_events / _event_queue / _signal_queue（事件队列）

**运行期计算**：
- gate 评估（_should_trigger_edge）——依赖当前时间
- filter 执行（_filter_conditional / _filter_unconditional / _filter_formula_eval）——依赖当前行情
- 事件发射（_emit_transfer_events）
- tracker 更新

---

## 4. 事件/信号机制的完整流程

### 从产生到发射的全路径

```
Tick 开始
  ↓
_pre_tick（数据注入）
  ↓
_tick()
  ├─ prev = _snapshot_node_stocks(node_stocks)  // 保存 tick 开始前的全量快照
  ├─ 对所有节点调用 _update_node_snapshot        // 检测外部注入导致的节点变化，自动标脏
  ↓
_run_tick_event_driven（核心循环）
  ├─ _refresh_latest_tick(current_bar_data)     // 刷新行情，置 data_dirty
  ├─ 首次执行：标脏所有源节点
  ├─ 按拓扑序遍历每条边：
  │   ├─ fired = _should_trigger_edge(edge)     // 时间门控（starttype + cxtype）
  │   ├─ _mark_edge_fired(eid, fired)           // 记录边触发状态
  │   ├─ triggered = fired AND (node_dirty[sid] OR data_dirty)
  │   ├─ 若 triggered：
  │   │   ├─ _build_pipeline_ctx(...)            // 构建 pipeline 上下文
  │   │   ├─ _process_edge_pipeline(ctx)         // 执行 pipeline
  │   │   │   └─ ...
  │   │   │   └─ post_process 阶段
  │   │   │       └─ _post_handle_new_entries    // 往 tevs append 事件记录
  │   │   ├─ _update_node_snapshot(tid, ...)     // 更新目标节点快照，自动标脏
  │   │   └─ _mark_dirty(tid)                    // 显式标脏目标节点（级联）
  ├─ _last_snapshot = 新全量快照                  // tick 末保存
  ├─ _last_bar_hash = 新 bar hash
  ├─ _first_run = False
  └─ _clear_dirty()                              // 清所有脏标记
  ↓
_update_trackers（更新持仓跟踪器）
  ↓
_emit_transfer_events(prev, node_stocks, tevs)   // ★ 统一批量发射
  ├─ 预建索引：stock_index, prev_stock_index
  ├─ 遍历 tevs：
  │   ├─ _log_transfer_batch（写日志）
  │   └─ move 模式：记录 move_exit_set
  ├─ 遍历 event_domain_templates（每个领域）：
  │   ├─ _resolve_codes（确定哪些股票触发）
  │   └─ 对每只股票：
  │       ├─ _emit_domain_event(domain, ctx)
  │       │   ├─ 遍历 event_defs → _push_event   // 发射事件
  │       │   └─ 遍历 signal_defs → _push_signal  // 发射信号
  ↓
_post_tick（后处理）
  ↓
Tick 结束
```

### 关键结论

1. **tevs 只是中间记录**：不是事件，是转移事件的"原材料"，包含 flow_id、source_id、target_id、transferred_codes、mode 等。
2. **统一发射点**：所有 event 和 signal 都在 `_emit_transfer_events` → `_emit_domain_event` 中发射。
3. **两种触发源**：
   - 从 tevs 来的：pool_enter / move_exit 等转移事件
   - 从 TTL 检查来的：ttl_expire 超时事件
4. **一个领域事件同时产出 event 和 signal**：例如 pool_enter 会同时产生 ENTER 事件和 BUY 信号（如果目标池是目标角色）。

证据：
- tick 主流程：`engine.py:3650-3661`
- _run_tick_event_driven：`engine.py:3578-3648`
- _emit_transfer_events：`engine.py:3492-3545`
- _emit_domain_event：`engine.py:3197-3252`
- _post_handle_new_entries（往 tevs append）：`engine.py:2974-3005`

---

## 5. 对股票池运行本质的更新理解（一句话）

股票池是一个**事件驱动的有向图状态机**：以编译期预计算的拓扑结构为骨架，每 tick 由"时间门控 + 节点脏 + 数据脏"三因子判定边是否触发，股票沿边在节点间流转，所有事件/信号在 tick 末统一批量发射。

---

## 6. 还没搞懂、需要继续深入的问题（至少5个）

### 问题1：_process_edge_pipeline 内部完整流程是什么？

目前只知道 pipeline 有 8 个相位（gate, src_change_detect, replay_guard, tdx_guard, duration_guard, flow_resolve, transform, post_process），但每个相位具体做什么、filter_type 不同时跳过哪些相位、transform 阶段到底变换什么，还需要深入读 `_process_edge_pipeline` 及其调用的各个 handler。

相关代码位置：`engine.py` 中 `_DEFAULT_PIPELINE_PHASES` 定义（`engine.py:283-296`）和 `_process_edge_pipeline` 函数体。

---

### 问题2：公式求值（formula_eval）路径完整流程是什么？

条件边如何调用 formula_router 进行公式计算、缓存策略是什么、批量求值还是逐只求值，还不清楚。需要读 `_filter_formula_eval` 和 formula_router 相关代码。

---

### 问题3：pool_role（池角色）解析机制是什么？

`_emit_domain_event` 中用 `role_ref` 和 `resolved_role` 判断是否是目标池，但池角色是怎么定义的、怎么解析的（`_resolve_pool_role`）、和 `pool_roles.json` 的关系，还需要深入。

相关代码：`engine.py:3283-` 开始的 `_resolve_pool_role`，以及 `config/pool_roles.json`。

---

### 问题4：_edge_flow_spec 中各字段的具体语义是什么？

CompiledSchedule 里有 `edge_flow_spec`，包含 `tran, emptyps, attr, propagate_mode, structural_mode` 等字段，但这些字段具体什么意思、怎么影响流转行为，还不清楚。需要读 flow_resolve 相位的代码。

---

### 问题5：TTL 淘汰的完整流程是什么？

知道有 `_post_apply_ttl` 和 `_apply_tdx_psatt_ttl`，也知道有 ttl_spec，但 TTL 是怎么计算的、在哪里检查、淘汰时怎么发射事件/信号，还需要完整梳理。

---

### 问题6：变换单元（三元组）的单元级缓存是否已经实现？

`edge_semantics.json:change_detection` 声明了 `unit_level: true` 和 `unit_cache_key`，但代码中没看到实际的单元级缓存实现。是配置声明了但代码没做，还是藏在别的地方？需要确认。

---

### 问题7：replay 模式和 live 模式的完整差异清单是什么？

目前只知道 gate 评估不同（replay 多了 begin/end/interval），但数据刷新、时间源、tracker 行为等方面还有什么差异，需要完整对比 runtime_modes.json 和相关代码。
