# 股票池引擎代码理解笔记 v2.7（第三版）

> 版本：v2.7
> 日期：2026-07-01
> 性质：纯理解笔记，不做设计建议
> 上一版：spec_v2.6.md

---

## 1. 上一版遗留偏差修正

### 1.1 CompiledSchedule 漏掉的字段

v2.6 对 CompiledSchedule 的描述不完整。实际 CompiledSchedule 有 **14 个字段**，分为 4 组：

| 分组 | 字段 | 说明 | 代码位置 |
|------|------|------|----------|
| 拓扑基础 | `nodes` | 节点字典 {nid: node} | engine.py:257 |
|  | `edges` | 边列表 | engine.py:258 |
|  | `edge_index` | 边索引 {eid: edge} | engine.py:259 |
|  | `depths` | 节点深度字典 {nid: depth}（longest-path） | engine.py:260 |
|  | `topo_order` | 节点 id 列表，按深度升序 | engine.py:261 |
|  | `processing_plan` | 处理计划 [(edge, filter_type), ...]，按源深度排序 | engine.py:262 |
|  | `out_edges` | 节点出边邻接表 {nid: [edge, ...]} | engine.py:263 |
|  | `in_edges` | 节点入边邻接表 {nid: [edge, ...]} | engine.py:264 |
|  | `units` | 变换单元三元组列表 | engine.py:265 |
|  | `standalone_edges` | 独立边列表 | engine.py:266 |
| 编译期预计算 | `edge_ctx` | {eid: {sid,tid,sn,tn,st,tt,ep,eid,edge,filter_type}} | engine.py:267 |
|  | `edge_timing` | {eid: {starttype,cxtype,...}} 时机门控规则 | engine.py:268 |
|  | `edge_filter_spec` | {eid: {nset,accode,...}} 筛选分派规则 | engine.py:269 |
|  | `edge_flow_spec` | {eid: {tran,emptyps,attr,...}} 状态流转规则 | engine.py:270 |
|  | `edge_action_spec` | {eid: {bsavehis,bsound,...}} callback 副作用规则 | engine.py:271 |
|  | `edge_ttl_spec` | {eid: {bdel,ndelnum,...}} TTL 超时淘汰规则 | engine.py:272 |

**关键认识**：CompiledSchedule 不仅仅是拓扑排序缓存，它是**编译期全量预计算**的产物——把运行期需要查表、解析、判断的逻辑全部在编译期算好，运行期只读。这是"一次编译，跨 tick 只读"设计的核心。（engine.py:229-232）

### 1.2 事件发射流程漏掉的中间层

v2.6 把事件发射描述为"直接遍历 transfer_events 发射"，实际中间有 **3 层间接**：

```
_emit_transfer_events
    ↓
遍历 event_domain_templates（3 个 domain: pool_enter/move_exit/ttl_expire）
    ↓
_resolve_codes → 从 transfer_events 或 diff 中提取代码列表
    ↓
_resolve_domain_ctx → 构建 domain 上下文（role/cond/tracker）
    ↓
_emit_domain_event → 通用领域事件发射器
    ├─ 遍历 event_rules.events 发射事件
    └─ 遍历 signal_rules.signals 发射信号
```

代码位置：engine.py:3492-3545（_emit_transfer_events）

---

## 2. 7个新方向的深入理解

### 2.1 _filter_cache（条件边筛选结果缓存）

#### 是什么

`_filter_cache` 是**条件边（conditional）筛选结果的 LRU+TTL 缓存**。缓存的是整条边的筛选输出（目标池的股票列表），用于在源池股票集和行情数据都没有变化时，直接复用上次筛选结果，跳过昂贵的条件计算。

- 类型：`LRUCache`（继承 dict，内部用 OrderedDict 实现）（engine.py:98-126）
- 初始化位置：engine.py:417
- 默认配置：
  - max_entries = 500（来自 defaults.json: filter_cache_maxsize）（engine.py:417）
  - default_ttl = 300 秒（来自 defaults.json: cache_ttl.default）（engine.py:417）

#### 键是什么

缓存键是一个元组：`(eid, frozenset(源池股票代码集合))`（engine.py:2293-2295）

```python
def _unit_cache_key(self, node_stocks, sid, eid):
    return (eid, frozenset(_stock_code(s) for s in node_stocks.get(sid, [])))
```

**设计意图**：键中包含源池股票集合的指纹（frozenset），这样当源池股票变化时，缓存键自动变化，旧缓存自然失效（不需要主动删除）。

#### 值是什么

缓存值是**目标池的完整股票列表的深拷贝**（list of stock dict）。（engine.py:2623）

```python
self._filter_cache.set(cache_key, copy.deepcopy(node_stocks.get(tid, [])))
```

读取时也是深拷贝返回：（engine.py:2585）
```python
cached_stocks = copy.deepcopy(cached)
```

#### LRU+TTL 具体怎么实现的

`LRUCache` 类（engine.py:98-126）：

- **存储结构**：内部用 `OrderedDict`（`self._store`），每个 entry 是 `{"data": ..., "ts": ..., "ttl": ...}`
- **LRU 策略**：
  - `get()` 时把访问的 key 移到末尾（`move_to_end`）（engine.py:108）
  - `set()` 时如果容量满了，从开头弹出最旧的（`popitem(last=False)`）（engine.py:111）
- **TTL 策略**：
  - `get()` 时检查 `time.time() - entry["ts"] > entry["ttl"]`，过期则删除并返回默认值（engine.py:106-107）
  - `set()` 时可指定 ttl，否则按 key 前缀匹配 `_ttl_map`，再否则用 `_default_ttl`（engine.py:112-115）
- **兼容 dict**：继承 dict，重写了 `__len__`、`__iter__`、`__contains__`、`__getitem__`、`__setitem__`、`keys()`、`values()`、`items()` 等方法，以兼容 `isinstance(x, dict)` 检查（engine.py:117-126）

#### 缓存什么时候失效

缓存失效有 4 种机制：

1. **源池变化失效**：源池股票集合变化 → cache_key 变化 → 旧缓存永远不会被命中（自然失效）（engine.py:2567-2568）
2. **行情变化失效**：bar_hash 变化 → 不查缓存，直接重算（engine.py:2582）
3. **TTL 过期失效**：超过 ttl 时间 → get 时自动删除（engine.py:106-107）
4. **LRU 淘汰失效**：超过 max_entries → 最久未用的被淘汰（engine.py:111）

注意：**行情数据变化时根本不查缓存**，不是让缓存失效，而是直接跳过缓存路径。（engine.py:2582: `if not nset_done and not src_changed and not bar_changed:`）

#### 对性能影响有多大

从代码路径看：

- 缓存命中时：直接 deepcopy 缓存结果 → 更新目标池 → 跳过所有条件计算（engine.py:2583-2597）
- 缓存未命中时：走完整的 nset 策略解析 + HR 分派 + 条件计算（engine.py:2600-2618）

对于行情比较平稳、源池变化不大的场景（大部分时间），条件边的筛选结果可以直接复用，避免了每次 tick 都重新计算条件公式。这是一个**典型的"数据不变就不重算"的增量优化**。

---

### 2.2 post_propagate_hooks（传播后副作用链）

#### 有哪些 hook？分别干什么？

当前配置了 3 个 hook（edge_strategies.json:271-274）：

| order | op | handler 方法 | 功能 |
|-------|----|-------------|------|
| 1 | record_execution | `_post_record_execution` | 记录边的执行次数和首/末次触发时间戳（engine.py:2950-2957） |
| 2 | handle_new_entries | `_post_handle_new_entries` | 检测新入池股票 → 创建 tracker → 追加 tev 事件 → 触发回调（engine.py:2974-3005） |
| 3 | apply_ttl | `_post_apply_ttl` | TTL 过期检查（仅对 auto_ttl_node_types 中的节点类型生效）（engine.py:3007-3013） |

#### hook 的执行顺序

按 `post_propagate_hooks` 列表中的 `order` 字段顺序执行。（engine.py:2903-2918）

执行流程：
1. 从 `_edge_cfg['post_propagate_hooks']` 取 hook 定义列表
2. 从 `_edge_cfg['hook_ops']` 查 op → method 映射
3. 反射获取 handler 方法
4. 评估 `when` 条件（如果有），不满足则跳过
5. 调用 handler(ctx)
6. 异常被捕获并 warning，不中断后续 hook

#### 是框架化的还是硬编码的？

**半框架化**：

- hook 的**列表、顺序、条件**是配置化的（edge_strategies.json 中定义）
- hook 的**具体实现**是硬编码在 engine.py 中的方法（`_post_record_execution`、`_post_handle_new_entries`、`_post_apply_ttl`）
- 通过 `hook_ops` 表做 op → method 名的映射（edge_strategies.json:276-279）

新增一个 hook 需要：在 engine.py 写一个方法 + 在 hook_ops 加一条映射 + 在 post_propagate_hooks 列表加一项。

#### 和 pipeline 是什么关系

pipeline 是设计上的 8 相位模型（见 2.5 节），而 post_propagate_hooks 是**扁平化后实际执行的第 2 步**。

在 `_process_edge_pipeline` 中（engine.py:2713-2735）：
```
步骤 1：_apply_edge_filter — 表驱动筛选（按 filter_type 分派）
步骤 2：_run_post_propagate_hooks — 后处理 hooks
```

所以 post_propagate_hooks 是 pipeline 扁平化后，"后处理"相位的具体实现形式。

---

### 2.3 event_domain_templates（表驱动事件模板）

#### 事件模板长什么样

每个 domain 模板是一个字典，包含以下字段（以 pool_enter 为例，edge_strategies.json:367-388）：

```json
{
  "trigger_match": {"type": "pool_enter"},     // 匹配 event_rules 中 trigger.type
  "signal_trigger": "pool_enter",              // 匹配 signal_rules 中 trigger.type
  "role_ref": "target_role",                   // 角色引用字段名
  "codes_source": {"op": "transfer", "field": "transferred_codes"},  // 代码来源
  "role_source": {"type": "node", "nid_field": "target_id"},         // 角色来源
  "cond_source": {"type": "edge_condition", "fid_field": "flow_id"}, // 条件来源
  "tracker_source": null,                     // tracker 来源
  "mode_filter": null,                        // 模式过滤
  "skip_if_present_in": null,                 // 跳过条件
  "context_fields": {...},                    // 上下文字段映射
  "field_resolution": {...}                   // 字段解析链
}
```

#### 有哪些事件域（domain）

3 个 domain（edge_strategies.json:354-435）：

| domain | 触发场景 | codes 来源 | role_ref | signal_trigger |
|--------|----------|-----------|----------|----------------|
| pool_enter | 股票进入目标池 | transfer（从 transfer_events 提取） | target_role | pool_enter |
| move_exit | move 模式下离开源池 | transfer（从 transfer_events 提取，过滤 mode=move） | source_role | move_exit |
| ttl_expire | TTL 超时淘汰 | diff（prev_stock_index - stock_index 的差集） | resolved_role | timeout |

注意：还有一个特殊的 `resolvers` 子表（不是 domain），定义了 role/cond/tracker 三种 source type 的 handler 映射。（edge_strategies.json:355-366）

#### 模板怎么路由到具体的事件处理

路由流程（engine.py:3537-3545）：

```
遍历 _event_domain_templates 的每个 domain
    ↓
跳过非域模板（如 resolvers 子表，判断标志：没有 codes_source 字段）
    ↓
_resolve_codes(codes_source) → 得到代码列表
    ↓
对每个 code：
    ├─ _skip_domain_code → 应用 mode_filter 和 skip_if_present_in
    ├─ _resolve_domain_ctx → 构建 domain 上下文
    │   ├─ 从 code_ctx 提取 sid/tid/fid/mode
    │   ├─ _resolve_domain_source('role_source') → 解析角色
    │   ├─ _resolve_domain_source('cond_source') → 解析条件
    │   └─ _resolve_domain_source('tracker_source') → 解析 tracker
    └─ _emit_domain_event(domain, domain_ctx)
        ├─ 遍历 event_rules.events → 匹配 trigger_match.type → 发射事件
        └─ 遍历 signal_rules.signals → 匹配 signal_trigger + is_target → 发射信号
```

#### 这是表驱动的事件系统吗

**是的，是表驱动的**。理由：

1. **领域定义表驱动**：有哪些领域（pool_enter/move_exit/ttl_expire）由 `event_domain_templates` 表定义，不是硬编码分支
2. **代码来源表驱动**：每个领域的代码从哪来（transfer/diff/literal）由 `codes_source.op` + `codes_ops` 表决定
3. **角色/条件/tracker 来源表驱动**：由 `role_source.type` + `resolvers.role_source_types` 表决定
4. **事件/信号匹配表驱动**：发射哪些事件/信号由 `event_rules.json` + `signal_rules.json` 决定，通过 trigger.type 匹配

但有一个**硬编码的限制**：`_emit_domain_event` 中事件和信号的发射逻辑是写死的通用流程，不能通过配置改变发射的方式（只能改变发射什么）。

---

### 2.4 _latest_tick（行情唯一真相源中间层）

#### 结构是什么

`_latest_tick` 是一个字典：`{code: bar_dict}`（engine.py:462）

配套字段有 3 个（engine.py:462-464）：
- `_latest_tick: Dict[str, dict]` — 最新行情快照
- `_latest_tick_ts: float` — 时间戳（time.time()）
- `_latest_tick_hash: Optional[str]` — 内容哈希（用于变化检测）

#### 和 market_data / data_cache 是什么关系

代码中**没有** `market_data` 或 `data_cache` 作为独立运行时表。

- `_latest_tick` 就是当前 bar 的行情快照，是**唯一真相源**
- `_current_bar_data` 也存在（engine.py:415），但它是当前 tick 的参数传递，`_latest_tick` 是持久化存储
- 完整 K 线序列由 `DataQuery`（`formula_router` / `data_query`）负责，不在引擎内存中
- 公式求值时通过 `formula_router` 按需查询历史数据，不是直接读 `_latest_tick`

关系图：
```
外部 bar_data 输入
    ↓
_refresh_latest_tick(bar_data)
    ↓
_latest_tick（持久化，跨 tick）
    ↓
    ├─ 条件计算读 _latest_tick（当前值）
    └─ 公式计算通过 formula_router 读 DataQuery（历史序列）
```

#### 数据怎么更新的

通过 `_refresh_latest_tick(bar_data)` 方法更新（engine.py:2095-2115）：

```python
def _refresh_latest_tick(self, bar_data):
    if not bar_data:
        return False
    new_hash = self._hash_bar_data(bar_data)
    if new_hash == self._latest_tick_hash:
        return False          # 内容没变，不更新
    self._latest_tick = dict(bar_data)
    self._latest_tick_hash = new_hash
    self._latest_tick_ts = time.time()
    self._data_dirty = True  # 置数据脏标记
    return True
```

调用位置：`_run_tick_event_driven` 的 Phase 2 Step 1（engine.py:3597）

#### 为什么需要这一层

设计意图（从代码推断）：

1. **解耦数据输入和计算**：数据更新是外部事件，计算层不直接操作输入参数，而是从 `_latest_tick` 读
2. **变化检测**：通过 hash 比较判断数据是否真的变了，没变就不置 `data_dirty`，从而跳过条件边重算
3. **统一访问点**：所有需要行情数据的地方都从 `_latest_tick` 读，而不是从各处传递的 `current_bar_data` 参数读
4. **Task 3 的产物**：从注释看（engine.py:461），这是 Task 3 的明确设计目标——"独立 latest_tick 运行时表（行情唯一真相源，与 node_stocks 解耦）"

---

### 2.5 pipeline 扁平化现状

#### 设计上的 8 相位是什么

设计上的 pipeline_phases 定义在 edge_strategies.json:304-337，共 8 个相位（含子步）：

| order | step | 适用 filter_type | 说明 |
|-------|------|-----------------|------|
| 1 | gate | 全部 | Gate 守门：eval_gate 查 runtime_modes 路由 |
| 2 | src_change_detect | unconditional | 源变更检测 |
| 3 | replay_guard | conditional, formula_eval (replay 模式) | Replay 守门：begin/end/interval |
| 4 | tdx_guard | conditional, formula_eval | TDX 守门：starttype 表驱动 |
| 5 | duration_guard | conditional, formula_eval | Duration 守门：cxtype 过期检查 |
| 6 | flow_resolve | 全部 | Flow 属性解析 |
| 6.1 | nset_change_detect | conditional | nset 变更检测：src/bar 变更标志 |
| 6.2 | nset_cache_lookup | conditional | nset 缓存查找：查 _filter_cache |
| 6.3 | nset_execute | conditional | nset 策略解析 + 执行分派 |
| 6.4 | nset_cache_write | conditional | nset 缓存写入 |
| 7 | transform | 全部 | 变换：读 transformations 表 |
| 8 | post_process | 全部 | 后处理链：count/ts→trackers→events→callback→TTL |

还有对应的 `phase_ops` 表，每个 step 有对应的 handler 方法名。（edge_strategies.json:338-352）

#### 实际扁平化后是哪两步

**实际运行时，pipeline 被扁平化为 2 步**，在 `_process_edge_pipeline` 中（engine.py:2713-2735）：

```
步骤 1：_apply_edge_filter — 表驱动筛选
    └─ 按 filter_type 分派到 3 个函数：
        ├─ _filter_unconditional（6 步内部实现）
        ├─ _filter_conditional（6 步内部实现 + 缓存）
        └─ _filter_formula_eval（9 步内部实现）

步骤 2：_run_post_propagate_hooks — 后处理 hooks
    └─ 按配置顺序执行 3 个 hook
```

注意：gate（时机判断）被提到了 `_run_tick_event_driven` 中的 `_should_trigger_edge`，在调用 `_process_edge_pipeline` 之前就完成了。（engine.py:3621-3627）

#### 为什么扁平化了

从代码注释和结构推断，原因是：

1. **消除过度抽象**：`_filter_unconditional`、`_filter_conditional`、`_filter_formula_eval` 三个方法的注释都写着"不走 pipeline 间接层"（engine.py:2479、2531、2633）
2. **性能优化**：每个相位都要查表、分派、传参，开销不小。直接写在一个函数里更快
3. **相位间耦合高**：flow_resolve、change_detect、cache_lookup、execute、cache_write 之间数据依赖强，拆开反而增加上下文传递成本
4. **filter_type 差异大**：三种 filter_type 的执行路径差异很大，用统一的 8 相位反而需要大量 condition 判断

#### 扁平化带来了什么问题

从代码中可以观察到的问题：

1. **代码重复**：三个 filter 函数都有类似的 flow_resolve、目标池更新、源池更新逻辑（engine.py:2497-2524 vs 2551-2623 vs 2651-2705）
2. **pipeline_phases 配置成了摆设**：定义了 8 相位和 phase_ops，但实际不按这个执行（至少 filter 部分不按）
3. **新增 filter_type 成本高**：需要写一个完整的新函数，而不是复用相位组合
4. **理解成本高**：需要同时理解"设计上的 8 相位"和"实际的 2 步扁平化"，两者不一致增加认知负担

---

### 2.6 三种 filter 类型完整体系

#### 三种类型概览

| 类型 | handler 方法 | 触发条件 | 适用场景 |
|------|-------------|----------|----------|
| unconditional | `_filter_unconditional` | 源池变化时 | 无条件直通传递（如变换单元出边） |
| conditional | `_filter_conditional` | 时间触发 + (源池脏 OR 数据脏) | 条件筛选边（nset_dispatch 策略分派） |
| formula_eval | `_filter_formula_eval` | 时间触发 + (源池脏 OR 数据脏) | 公式批量求值筛选 |

#### 每种的触发条件、执行逻辑

**1. unconditional（无条件）**（engine.py:2476-2526）

触发条件：
- gate 通过（时机触发）
- 源池股票集变化（src_changed）

执行逻辑（6 步）：
1. 源变更检测（源未变则跳过，返回 False）
2. 流转属性解析（读编译期 edge_flow_spec）
3. 记录传播前目标池快照
4. passed_codes = 全部源股票代码（identity）
5. 目标池更新（io=True 替换，否则合并）
6. 源池更新（mv=True 时移除）

**2. conditional（条件过滤）**（engine.py:2528-2627）

触发条件：
- gate 通过（时机触发）
- 源池脏 OR 数据脏（node_dirty[sid] OR data_dirty）

执行逻辑（6 步 + 缓存）：
1. 流转属性解析（读编译期 edge_flow_spec）
2. 记录传播前目标池快照
3. nset 变更检测 + 空源早退（计算 src_changed + bar_changed）
4. nset 缓存查找（无变更时查 _filter_cache 复用结果）
5. nset 策略解析 + 执行分派（读编译期 edge_filter_spec → HR 分派）
6. nset 缓存写入（未命中缓存时写回）

**3. formula_eval（公式批量求值）**（engine.py:2629-2707）

触发条件：
- gate 通过（时机触发）
- 源池脏 OR 数据脏

执行逻辑（9 步）：
1. 流转属性解析（读编译期 edge_flow_spec）
2. 记录传播前目标池快照
3. 读取编译期预解析的公式文本
4. 读取编译期预解析的周期
5. 收集源股票代码
6. 调用 formula_router 批量求值
7. 筛选满足条件的股票代码
8. 目标池更新（io=True 替换，否则合并）
9. 源池更新（mv=True 时移除）

#### 三种类型的代码路径是分开的还是共用的

**入口统一，内部分开**：

- 统一入口：`_apply_edge_filter(ctx)`（engine.py:2459-2474）
  - 查表 `_filter_strategies` 得到方法名
  - 反射调用对应方法

- 内部实现：三个函数完全独立，代码不共用
  - `_filter_unconditional`：1976-2526（约 50 行）
  - `_filter_conditional`：2528-2627（约 100 行）
  - `_filter_formula_eval`：2629-2707（约 80 行）

有部分逻辑是重复的（flow_resolve、目标池更新、源池更新），但各自独立实现。

#### 各有多少边在用

没有直接的统计数据，但从 `_resolve_filter_type` 的逻辑（engine.py:1290-1330）可以推断：

- **变换单元入边**：默认 conditional（unit_cfg.in_filter_type，默认 'conditional'）
- **变换单元出边**：默认 unconditional（unit_cfg.out_filter_type，默认 'unconditional'）
- **独立边**：由 `edge_semantics.json` 的 `edge_type_handlers` 决定
  - `propagate_directly` → unconditional
  - `apply_gate_filter` → conditional
  - `formula_eval` → formula_eval

从实际股票池配置看，conditional 应该是最多的（大部分边是条件筛选边），unconditional 次之（变换单元出边、直通边），formula_eval 最少（特殊公式边）。

---

### 2.7 pool_role 解析机制

#### pool_role 是什么？有哪些角色？

pool_role（池角色）是节点的**语义分类标签**，用于决定节点在事件/信号系统中的行为。

定义在 `pool_roles.json:roles` 中，共 6 种角色（pool_roles.json:4-70）：

| role_id | 名称 | is_target | is_sink | 说明 |
|---------|------|-----------|---------|------|
| target_pool | 目标池 | true | false | 核心目标池，股票进入时生成 BUY 信号，离开时生成 SELL 信号 |
| candidate_pool | 备选池 | false | false | 候选股票池，仅作为中转或观察，不生成交易信号 |
| sink_pool | 废弃池 | false | true | 废弃/丢弃池，进入此池的股票不再跟踪 |
| transfer_condition | 转移条件节点 | false | false | 流转条件判断节点，非实际持仓池 |
| market_source | 市场源 | false | false | 市场数据源节点，提供候选股票列表 |
| discard_pool | 丢弃池 | false | true | 不满足条件的股票移入此池，同 sink_pool |

每个角色对象包含：`role_id`、`name`、`baimpool_value`、`is_target`、`is_sink`、`generate_buy_signal`、`generate_sell_on_exit`、`desc`、`priority`。

#### is_target / is_source 怎么判断

**is_target**：
- 从角色对象的 `is_target` 字段读取（如 `target_pool.is_target = true`）
- 在信号判断中使用：`role_info and role_info.get("is_target", False)`（engine.py:3258、3265）

**is_source**：
- pool_roles.json 中**没有** `is_source` 字段
- "source" 是边的概念（源节点），不是池角色的属性
- 在事件模板中，`pool_enter` 用 `target_role`（目标节点的角色），`move_exit` 用 `source_role`（源节点的角色）
- 判断"是不是目标池"用 `is_target`，判断"是不是源"是看边的方向，不是角色属性

#### 信号发射和 pool_role 的关系

信号发射的核心判断是：**目标池（is_target=true）的股票进入/离开时才产生 BUY/SELL 信号**。

具体逻辑在 `_should_emit_signal_for_domain` 中（engine.py:3254-3269）：

```python
def _should_emit_signal_for_domain(self, sig_rule, trigger_type, role_info):
    trigger = sig_rule.get("trigger", {})
    if trigger.get("type") == trigger_type:
        return bool(role_info and role_info.get("is_target", False))
    # conditions 多条件分支...
    for cond in conditions:
        if cond.get("type") != trigger_type:
            continue
        if role_info and role_info.get("is_target", False):
            matched = True
        # ...
```

对应 signal_rules.json 中的配置：
- BUY 信号：trigger.type = pool_enter，条件隐含 target_pool_role.is_target == true（signal_rules.json:9）
- SELL 信号：trigger.conditions 中有 move_exit 和 timeout，都需要 source_role.is_target == true（signal_rules.json:24、29）

#### 节点角色和边类型的关系

- **节点角色**（pool_role）：描述节点的语义性质（目标池/备选池/源/...），用于事件信号系统
- **边类型**（edge_type / filter_type）：描述边的筛选方式（unconditional/conditional/formula_eval），用于数据流计算

两者是**独立的两个维度**：
- 目标池节点可以有入边（conditional 或 unconditional），也可以有出边
- 市场源节点（market_source 角色）的出边通常是 conditional（条件筛选边）
- 变换单元内部的边通常是 unconditional（直通）

但有一个**间接关联**：边的 filter_type 部分由源节点的类型决定（通过 edge_semantics.json 的 edge_type_handlers），而节点类型又和角色解析有关联（如 market_source 类型节点角色是 market_source）。

---

## 3. 股票池引擎的完整架构图（文字版）

```
┌─────────────────────────────────────────────────────────────────┐
│                        配置层（编译期只读）                       │
├─────────────────────────────────────────────────────────────────┤
│  pool_config.json   池配置（节点/边/变换单元）                    │
│  edge_semantics.json 边语义（edge_type_handlers）               │
│  edge_strategies.json 边策略（strategies + pipeline_phases +    │
│                         post_propagate_hooks +                   │
│                         event_domain_templates + hook_ops + ...）│
│  pool_roles.json     池角色定义 + 解析规则                       │
│  event_rules.json    事件规则（ENTER/EXIT/TIMEOUT）             │
│  signal_rules.json   信号规则（BUY/SELL）                       │
│  timing.json         时机规则（starttype/cxtype）               │
│  dispatch.json       分派表（nset_dispatch）                    │
│  engines.json        引擎表（强弱对比等）                        │
│  defaults.json       默认值                                     │
│  runtime_tables_schema.json 运行时表定义                        │
└─────────────────────────────────────────────────────────────────┘
                              ↓ 编译（_compile_pool）
┌─────────────────────────────────────────────────────────────────┐
│                     编译层（CompiledSchedule）                   │
├─────────────────────────────────────────────────────────────────┤
│  拓扑：topo_order / depths / out_edges / in_edges               │
│  计划：processing_plan / units / standalone_edges               │
│  预计算：edge_ctx / edge_timing / edge_filter_spec /            │
│          edge_flow_spec / edge_action_spec / edge_ttl_spec      │
└─────────────────────────────────────────────────────────────────┘
                              ↓ 每 tick 执行
┌─────────────────────────────────────────────────────────────────┐
│                      数据层（运行时状态）                         │
├─────────────────────────────────────────────────────────────────┤
│  _latest_tick       最新行情快照（唯一真相源）                   │
│  _latest_tick_hash  行情内容哈希                                 │
│  _latest_tick_ts    行情时间戳                                   │
│  _current_bar_data  当前 bar 数据（参数传递）                    │
│  _data_dirty        数据脏标记（行情变化时置 True）              │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                      状态层（股票池状态）                         │
├─────────────────────────────────────────────────────────────────┤
│  node_stocks        节点股票字典（核心业务状态）                 │
│  _last_snapshot     上 tick 节点股票快照（用于变更检测）         │
│  _last_bar_hash     上 tick bar 哈希                             │
│  _dirty_nodes       脏节点集合（级联传播）                       │
│  _edge_fired        边时间触发标记                               │
│  _first_run         首次运行标记                                 │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                      计算层（筛选/流转）                          │
├─────────────────────────────────────────────────────────────────┤
│  _apply_edge_filter 统一筛选入口                                 │
│  ├─ _filter_unconditional  无条件直通                            │
│  ├─ _filter_conditional   条件筛选（nset_dispatch）             │
│  └─ _filter_formula_eval  公式批量求值                           │
│  _filter_cache       条件边筛选结果缓存（LRU+TTL）               │
│  _current_compiled   当前 tick 的 CompiledSchedule 引用         │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                      副作用层（传播后）                           │
├─────────────────────────────────────────────────────────────────┤
│  post_propagate_hooks  后处理 hook 链                            │
│  ├─ _post_record_execution  记录执行次数/时间戳                  │
│  ├─ _post_handle_new_entries 新入池处理(tracker+tev+回调)       │
│  └─ _post_apply_ttl         TTL 过期检查                        │
│  _trackers           持仓跟踪器（股票级）                        │
│  _exit_tracker_cache 离池 tracker 缓存                           │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                      事件层（领域事件）                           │
├─────────────────────────────────────────────────────────────────┤
│  event_domain_templates  领域模板（3 个 domain）                 │
│  ├─ pool_enter    入池事件/信号                                  │
│  ├─ move_exit     move 出池事件/信号                             │
│  └─ ttl_expire    TTL 超时事件/信号                              │
│  _event_queue        事件队列（异步）                            │
│  _signal_queue       信号队列（异步）                            │
│  _event_rules        事件规则表                                  │
│  _signal_rules       信号规则表                                  │
│  _pool_roles         池角色表                                    │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. 核心数据流动图（从 tick 进来，到事件发出去）

```
外部 bar_data 输入
    │
    ▼
_pre_tick（数据注入流水线，可选）
    │
    ▼
_tick() 入口
    │
    ├─ prev = _snapshot_node_stocks(node_stocks)  // 保存传播前快照
    ├─ _exit_tracker_cache.clear()
    ├─ _update_node_snapshot(nid, stocks)          // 检测源节点变化，标脏
    │
    ▼
_run_tick_event_driven()
    │
    ├─ Phase 1: _get_compiled(pool_config)         // 获取编译期调度表
    │   （首次或配置变化时编译，缓存复用）
    │
    ├─ Phase 2 Step 1: _refresh_latest_tick(bar_data)
    │   └─ 哈希变化 → _data_dirty = True
    │
    ├─ Phase 2 Step 2: 首次执行 → 源节点标脏
    │
    └─ Phase 2 Step 3: 按拓扑序遍历所有边
            │
            ├─ _should_trigger_edge(edge)          // 时机判断（gate）
            ├─ _mark_edge_fired(eid, fired)
            │
            ├─ triggered = fired AND (node_dirty[sid] OR data_dirty)
            │
            └─ 若 triggered:
                    │
                    ├─ _build_pipeline_ctx()       // 构建上下文
                    │
                    ▼
                _process_edge_pipeline(ctx)
                    │
                    ├─ 步骤 1: _apply_edge_filter(ctx)
                    │   └─ 按 filter_type 分派:
                    │       ├─ unconditional → _filter_unconditional
                    │       │   └─ 源变更检测 → flow 解析 → 全量通过 → 更新池
                    │       ├─ conditional → _filter_conditional
                    │       │   ├─ flow 解析 → 变更检测
                    │       │   ├─ 缓存命中? → 是: 复用结果
                    │       │   ├─ 否: 策略解析 → HR 分派 → 计算
                    │       │   └─ 写缓存
                    │       └─ formula_eval → _filter_formula_eval
                    │           └─ flow 解析 → 公式求值 → 筛选 → 更新池
                    │
                    └─ 步骤 2: _run_post_propagate_hooks(ctx)
                        ├─ record_execution（计数+时间戳）
                        ├─ handle_new_entries（新入池: tracker + tev + 回调）
                        └─ apply_ttl（TTL 检查，条件执行）
    │
    └─ Phase 2 Step 4: 更新快照 + 清脏
            ├─ _last_snapshot = snapshot
            ├─ _last_bar_hash = current_bar_hash
            ├─ _first_run = False
            └─ _clear_dirty()  // 清 dirty_nodes + data_dirty + edge_fired
    │
    ▼
回到 _tick():
    │
    ├─ _update_trackers(node_stocks, current_bar_data)  // 更新 tracker 公式
    │
    ├─ _emit_transfer_events(prev, node_stocks, tevs)
    │   │
    │   ├─ 预建索引 + 预计算 move_exit_set
    │   │
    │   └─ 遍历 event_domain_templates（3 个 domain）
    │           │
    │           ├─ pool_enter:
    │           │   ├─ codes = transfer_events.transferred_codes
    │           │   ├─ 对每个 code:
    │           │   │   ├─ _resolve_domain_ctx → target_role / cond
    │           │   │   └─ _emit_domain_event:
    │           │   │       ├─ 遍历 event_rules → 匹配 trigger → push_event
    │           │   │       └─ 遍历 signal_rules → is_target? → push_signal
    │           │   └─ ...
    │           │
    │           ├─ move_exit:
    │           │   ├─ codes = transfer_events.transferred_codes (mode=move)
    │           │   └─ 类似 pool_enter，但 role 是 source_role
    │           │
    │           └─ ttl_expire:
    │               ├─ codes = prev_stock_index - stock_index
    │               ├─ skip_if_present_in = move_exit_set
    │               └─ 类似 pool_enter，但 trigger 是 timeout
    │
    ├─ _post_tick(...)  // 后处理（PK 排名/分析角度/看板/告警）
    │
    └─ 返回 node_stocks
```

---

## 5. 对股票池运行本质的再次更新理解

**股票池引擎本质上是一个"表驱动的增量数据流引擎"——以编译期预计算的拓扑调度表为骨架，以数据脏标记为触发源，以条件筛选为核心计算，以领域事件模板为输出接口，通过多层缓存（编译缓存、筛选缓存、变更检测）实现"不变就不算"的增量计算。**

---

## 6. 仍然不懂、需要更深入的问题

### 问题 1：nset_dispatch 的具体策略有哪些？分别怎么实现的？

`_filter_conditional` 中通过 HR（HandlerRegistry）分派执行具体策略，但 HR 里注册了哪些 strategy handler、每个 handler 具体做什么、dispatch.json 和 engines.json 怎么配合使用，还没有深入理解。

相关代码：engine.py:2600-2618（策略解析 + HR 分派）

### 问题 2：_should_trigger_edge 的完整判断逻辑是什么？

时机判断（gate）是触发的前置条件，但 `_should_trigger_edge` 的完整实现（starttype/cxtype/ replay_guard/tdx_guard/duration_guard 等）还没有逐行读。

相关代码：grep `_should_trigger_edge`

### 问题 3：变换单元（transform unit）的完整机制是什么？

processing_plan 中有变换单元三元组，入边/出边的 filter_type 不同，但变换单元具体是什么、内部怎么工作、transform 相位做什么，还不清楚。

相关代码：engine.py:1290-1313（变换单元处理计划）

### 问题 4：formula_router / DataQuery 的完整架构是什么？

公式求值路径只看到了 `_run_formula_eval_batch_sync` 调用，但 formula_router 是什么、DataQuery 怎么获取历史K线、批量求值的具体实现，还没有深入。

相关代码：grep `formula_router`、`DataQuery`

### 问题 5：pre_tick / post_tick 流水线的完整 stage 有哪些？

只知道 pre_tick 和 post_tick 是流水线，但具体有哪些 stage、每个 stage 做什么、数据注入的完整流程，还不清楚。

相关代码：grep `_pre_tick`、`_post_tick`、`pre_tick_pipeline`

### 问题 6：_emit_domain_event 中事件 detail 和信号字段的完整构建流程？

`context_fields` 和 `field_resolution` 的具体工作方式、`_resolve_context_field` 和 `_resolve_field` 的实现，还没有读。

相关代码：engine.py:3197-3252（_emit_domain_event）

### 问题 7：HR（HandlerRegistry）里到底注册了多少个 handler？

代码中大量使用 `_HR.get(handler_name)` 来分派，但 HR 是什么、在哪里初始化、注册了哪些 handler，还没有完整梳理。

相关代码：grep `_HR`、`HandlerRegistry`

---

> 本笔记为纯理解记录，所有结论均有代码引用支撑。
> 下一版待解决的核心问题：nset 策略体系、时机判断完整逻辑、变换单元机制。
