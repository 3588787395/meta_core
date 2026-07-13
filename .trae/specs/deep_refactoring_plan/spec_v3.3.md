# v3.3 核心语义深挖：节点计算 vs 边配置

> 纯理解笔记。所有结论均从代码中提取，有文件+行号引用。不发明任何概念。
> 本轮目标：搞清楚"节点计算"到底是什么意思，以及用户的概念模型与代码实现的差异。

---

## 1. 问题提出：用户的概念模型 vs 代码实现的差异

### 用户的概念模型（原话）
- "所有边都只是参数和触发条件"
- "触发条件只是节点的事件配置，不是边的执行"
- "只有节点有计算执行功能"
- "无转移节点相当于空转移节点不需计算"

### 代码实现的表象
- MetaEngine 驱动一切，没有独立的 Node 类
- 边有 `filter` 方法（`_apply_edge_filter`、`_filter_conditional` 等）
- 主循环 `_run_tick_event_driven` 遍历的是**边**，不是节点
- 计算逻辑（过滤、公式求值）写在 MetaEngine 的方法里

### 核心矛盾
**表象上**：代码是"边驱动"的——主循环遍历边，边有 filter 方法。
**用户说**：只有节点有计算执行功能，边只是参数和触发条件。

哪个是对的？还是说两者都是对的，只是视角不同？

---

## 2. "节点计算"到底计算什么（代码验证）

### 2.1 节点里存了什么数据？

节点有两层数据：

**第一层：节点配置数据（静态）**
- 存在 `pool_config.nodes` 中，是一个 dict
- 包含：`id`, `type`, `params`, `pos`, `clr`, `text` 等
- 不同类型的节点，params 内容不同

代码证据：
- `_init_node_stocks` 从 `node.get('params', {}).get('stocks', [])` 读取初始股票（engine.py:778）
- `cell_type_registry.json` 定义了每种节点类型的 attrs 和 default_params（cell_type_registry.json:1-991）

**第二层：节点运行时数据（动态）**
- 存在 `node_stocks[nid]` 中，是一个股票列表
- 每个股票是 dict：`{code, label, _tracker, indate, intime, ...}`
- 这是节点的"状态"——节点当前持有哪些股票

代码证据：
- `node_stocks` 是运行时表，定义在 `_RUNTIME_TABLE_NAMES` 中（engine.py:132）
- `_snapshot_node_stocks` 对每个节点生成 `frozenset(codes)` 快照（engine.py:2031-2035）

### 2.2 节点需要做什么计算？

**结论：节点本身不做计算。计算发生在"边的筛选"过程中，但筛选的"对象"是节点的股票集合。**

让我们用三种 filter_type 来验证：

#### 筛选模式 1：unconditional（无条件）
```python
def _filter_unconditional(self, ctx):
    # 1. 源变更检测
    # 2. 流转属性解析（从 edge_flow_spec 读）
    # 3. 记录传播前目标池快照
    # 4. passed_codes = 全部源股票代码  <-- 没有计算，直接全通过
    # 5. 目标池更新
    # 6. 源池更新（mv=True 时移除）
```
代码位置：engine.py:2476-2526

**计算量：0**。只是把源节点的股票全部复制/移动到目标节点。

#### 筛选模式 2：conditional（条件过滤，nset_dispatch）
```python
def _filter_conditional(self, ctx):
    # 1. 流转属性解析
    # 2. 记录传播前目标池快照
    # 3. nset 变更检测 + 空源早退
    # 4. nset 缓存查找
    # 5. nset 策略解析 + 执行分派  <-- 核心计算在这里
    # 6. nset 缓存写入
```
代码位置：engine.py:2528-2627

**核心计算在第 5 步**：调用 `_HR.get(handler_name)` 对应的函数，比如 `edge_default_transfer`、`tdx_condition_eval` 等。

但注意：这些 handler 是从 `edge_strategies.json` 的 `strategies` 配置中查表得到的（engine.py:2601-2617）。策略的 key 是 `源节点类型:目标节点类型`，比如 `tdx_candidate:tdx_condition`。

#### 筛选模式 3：formula_eval（公式批量求值）
```python
def _filter_formula_eval(self, ctx):
    # 1. 流转属性解析
    # 2. 记录传播前目标池快照
    # 3. 读取编译期预解析的公式文本
    # 4. 读取编译期预解析的周期
    # 5. 收集源股票代码
    # 6. 调用 formula_router 批量求值  <-- 核心计算在这里
    # 7. 筛选满足条件的股票代码
    # 8. 目标池更新
    # 9. 源池更新
```
代码位置：engine.py:2629-2707

**核心计算在第 6 步**：`self._run_formula_eval_batch_sync(formula, symbols, period, current_bar_data)`

公式从哪里来？从 `_filter_spec.get('formula', '')` 读，而 `_filter_spec` 来自 `edge_filter_spec[eid]`（engine.py:2652, 2664）。

### 2.3 计算的输入是什么？输出是什么？

**输入**：
1. 源节点的股票列表（`node_stocks[sid]`）
2. 行情数据（`current_bar_data` / `latest_tick`）
3. 边的配置参数（`edge.params` → 编译为 `edge_filter_spec` / `edge_flow_spec`）
4. 节点的配置参数（`node.params`，公式可能从节点读）

**输出**：
1. 目标节点的股票列表更新（`node_stocks[tid]`）
2. 源节点的股票列表可能更新（move 模式）
3. 执行计数、时间戳等元数据更新

代码证据：
- 输入：`_filter_formula_eval` 从 ctx 读取 `sid, tid, node_stocks, current_bar_data`（engine.py:2638-2644）
- 输出：直接修改 `node_stocks[tid]` 和 `node_stocks[sid]`（engine.py:2694-2705）

### 2.4 计算逻辑写在哪里？

**计算逻辑全部写在 MetaEngine 的方法中**，不是节点的方法，也不是边的方法。

具体分布：
| 计算类型 | 所在方法 | 代码位置 |
|---------|---------|---------|
| 时机守门（gate） | `_should_trigger_edge` | engine.py:1757 |
| 时机原语求值 | `_eval_timing_primitive` | engine.py:829 |
| 无条件筛选 | `_filter_unconditional` | engine.py:2476 |
| 条件筛选（nset） | `_filter_conditional` | engine.py:2528 |
| 公式批量求值 | `_filter_formula_eval` | engine.py:2629 |
| 实际筛选执行 | HR 内置函数（`edge_default_transfer` 等） | native/builtins |
| 流转（copy/move/overwrite） | 各 filter 方法内部 | engine.py:2513-2524 等 |
| 后处理（tracker/events/TTL） | `_run_post_propagate_hooks` | engine.py:2729 |

> **关键发现**：节点没有 `compute()` 方法，边也没有 `execute()` 方法。所有计算都是 MetaEngine 的方法。节点和边都是数据（dict），不是行为主体。

---

## 3. "边只是参数和触发条件"的真实含义（代码验证）

### 3.1 边存了哪些参数？

边的配置数据：
- 基础：`id`, `from`, `to`, `attr`, `clr`
- params 中的参数（不同类型的边参数不同）

代码证据：
- `edge_semantics.json` 定义了两种边类型的 attributes：
  - conditional 边：`from, to, attr, clr, interval, begin, begint, end, endt, count`（edge_semantics.json:1）
  - unconditional 边：`from, to, attr, clr`（edge_semantics.json:1）

编译期预计算后，边的参数被提取到多个 spec 表中：
| spec 表 | 存什么 | 代码位置 |
|---------|-------|---------|
| `edge_ctx[eid]` | 端点信息（sid, tid, sn, tn, st, tt, ep, eid, edge, filter_type） | engine.py:245 |
| `edge_timing[eid]` | 时机门控规则（starttype, cxtype, starttime, cxtime...） | engine.py:246 |
| `edge_filter_spec[eid]` | 筛选分派规则（nset, accode, noperate, gateway, engine...） | engine.py:248 |
| `edge_flow_spec[eid]` | 状态流转规则（tran, emptyps, attr, propagate_mode...） | engine.py:250 |
| `edge_action_spec[eid]` | callback 副作用规则 | engine.py:271 |
| `edge_ttl_spec[eid]` | TTL 超时淘汰规则 | engine.py:272 |

### 3.2 边存了哪些触发条件配置？

触发条件配置存储在**边的 params** 中，包括：

**时机触发条件（gate）**：
- `starttype`：开始类型（0=始终，1=时间段，2=开盘后N分钟，3=收盘前N分钟...）
- `cxtype`：持续类型（0=无限期，1=持续N秒，2=到指定时间...）
- `begin` / `end` / `interval`：回放模式下的触发时间

代码证据：
- `_tdx_should_execute` 从 `edge.get('params', {})` 读 `starttype`（engine.py:787-788）
- `_tdx_check_duration` 从 `edge.get('params', {})` 读 `cxtype`（engine.py:1020）
- timing.json 的 `starttype_rules` 和 `cxtype_rules` 定义了每种类型对应的原语

**数据触发条件（dirty）**：
- 隐式的：源节点股票变化（`node_dirty[sid]`）或行情数据变化（`data_dirty`）
- 这不是边的配置，而是运行时状态

代码证据：
- `triggered = fired and (self._is_dirty(sid) or self._is_data_dirty())`（engine.py:3627）

### 3.3 边自己不执行，那谁来执行？

**MetaEngine 执行**。

边只是一个配置对象（dict），描述"从哪里到哪里，在什么条件下，用什么筛选规则"。
实际执行这些规则的是 MetaEngine 的方法。

类比：
- 边 = 一张菜谱（食材列表+步骤说明）
- MetaEngine = 厨师（按照菜谱做菜）
- 节点 = 碗（盛菜的容器）

代码证据：
- 边就是普通 dict，没有 class，没有方法
- 所有处理都在 `_process_edge_pipeline` → `_apply_edge_filter` → 具体 filter 方法（engine.py:2713-2735）

### 3.4 边和节点的关系是什么？

**边是节点之间的连接 + 连接上的配置**。

具体来说：
- 边有 `from`（源节点ID）和 `to`（目标节点ID）
- 边描述了"股票如何从源节点流向目标节点"
  - 什么时候允许流（时机触发条件）
  - 哪些股票能流过去（筛选规则）
  - 怎么流（copy / move / overwrite）

但用户说"边是节点的属性"——这个说法对吗？

**从代码结构看**：边不是节点的属性，边是独立的列表（`pool_config.edges`）。节点有出边邻接表（`out_edges[nid]`）和入边邻接表（`in_edges[nid]`），但这些是编译期生成的索引，不是节点自身的属性。

**从语义角度看**：边的"触发条件"确实可以理解为节点的"事件配置"——因为触发条件描述的是"在什么事件下，节点的股票会流出去"。但这个配置是**写在边上**的，不是写在节点上的。

代码证据：
- `out_edges` 和 `in_edges` 在 CompiledSchedule 中（engine.py:263-264）
- 节点自身（`pool_config.nodes`）中没有 edges 字段

---

## 4. "触发条件是节点的事件配置"的真实含义（代码验证）

### 4.1 节点有哪些事件？

代码中没有显式的"节点事件"定义。但从运行时行为看，节点的"事件"就是：

1. **股票集合变化事件**（node_dirty）
   - 当节点的股票列表发生变化时，`_mark_dirty(nid)` 被调用
   - 这会触发该节点所有出边的重新评估

2. **数据更新事件**（data_dirty）
   - 当行情数据更新时，`_data_dirty = True`
   - 这会触发所有条件边的重新评估（因为条件边的筛选依赖行情数据）

3. **入池事件**（pool_enter）
   - 股票进入节点时触发
   - 在后处理阶段通过 `_emit_domain_event` 发射

4. **出池事件**（pool_exit / move_exit / ttl_expire）
   - 股票离开节点时触发
   - 在后处理阶段发射

代码证据：
- `_mark_dirty` 标记节点为脏（engine.py:2062-2071）
- `_refresh_latest_tick` 置 `_data_dirty = True`（engine.py:2114）
- `event_domain_templates` 定义了 `pool_enter`、`move_exit`、`ttl_expire` 三种领域事件（edge_strategies.json:354-435）

### 4.2 触发条件怎么绑定到节点上？

**通过边的源节点隐式绑定**。

触发条件写在边的 params 中（starttype, cxtype 等）。当遍历节点的出边时，自然会检查这条边的触发条件。

所以：
- 节点的"事件配置" = 该节点所有出边的触发条件的集合
- 每条出边有自己的触发条件
- 触发条件是边的属性，但效果是"源节点在什么条件下输出股票"

代码证据：
- 主循环按节点遍历出边：`for edge in compiled.out_edges.get(nid, [])`（engine.py:3608-3609）
- 每条边独立检查 `_should_trigger_edge(edge)`（engine.py:3622）

### 4.3 事件触发了，节点做什么？

**节点什么都不做。是 MetaEngine 在做事。**

当触发条件满足 + 数据脏时，MetaEngine 执行：
1. 对源节点的股票进行筛选
2. 将筛选通过的股票转移到目标节点
3. 更新节点状态（node_stocks）
4. 标记目标节点为脏（级联传播）

节点只是被动地被修改股票列表。

代码证据：
- 触发后的执行：`_process_edge_pipeline(ctx)`（engine.py:3636）
- 目标节点标脏：`_mark_dirty(tid)`（engine.py:3641）

### 4.4 代码里对应的实现是什么？

实现方式是**事件驱动的 tick 循环**，但不是"节点事件→节点响应"的模式，而是"引擎检测事件→引擎执行边逻辑"的模式。

```
每个 tick:
  1. 刷新行情数据 → 置 data_dirty
  2. 首次运行 → 标脏所有源节点
  3. 按拓扑序遍历每个节点:
       遍历该节点的每条出边:
         检查时机触发条件 (gate)
         检查数据触发条件 (node_dirty OR data_dirty)
         如果都满足:
           执行筛选逻辑
           更新目标节点股票
           标脏目标节点
  4. 清脏 (准备下一个 tick)
```

代码证据：engine.py:3578-3648（`_run_tick_event_driven` 完整实现）

---

## 5. "转移节点"与"条件节点"的本质区别

### 5.1 节点类型全景图

从 `dzh_type_map.json` 和 `cell_type_registry.json` 中提取的核心节点类型：

| 内部类型名 | DZH类型码 | TDX类型码 | 中文名 | 角色 |
|-----------|-----------|-----------|--------|------|
| `market_source` | 202 | 7 | 备选池 | 源节点：提供初始股票 |
| `stock_state_pool` | 200 | 8 | 股票状态池 | 容器节点：持有股票 |
| `transfer_condition` | 201 | 3 | 转移条件 | 枢纽节点：条件过滤 |
| `discard_pool` | 4 | - | 丢弃池 | 汇节点：接收被丢弃的股票 |
| `stock_state_fallback` | 203 | - | 特殊池 | 容器节点：特殊用途 |

代码证据：
- dzh_type_map.json:4-15（type_map）
- dzh_type_map.json:44-51（tdx_type_map）
- cell_type_registry.json:713-723（type_info）

### 5.2 转移节点（条件节点）是什么？

**转移条件节点**（transfer_condition / tdx_condition），俗称"条件节点"，是股票池的"过滤枢纽"。

它的特点：
1. **形状**：三角形（triangle_right）——视觉上表示"过滤"
2. **入边**：conditional 边（有 interval 等时间属性）
3. **出边**：unconditional 边（无时间属性，源变即传）
4. **核心功能**：对入边的股票进行条件筛选，筛选结果通过出边传出

代码证据：
- `edge_semantics.json` 定义了变换单元：
  - "条件转移边(入边) + 转移条件(枢纽节点) + 无条件转移边(出边)"（edge_semantics.json:1）
  - hub_node_types: `["tdx_condition", "transfer_condition", "dzh_condition_pool"]`（edge_semantics.json:1）

### 5.3 空转移节点是什么？

**空转移节点 = 没有筛选条件的条件节点 = 直通的条件节点**。

在代码中对应的是：
- `_filter_unconditional` 的行为：全部股票通过（engine.py:2510-2511）
- 或者条件节点的 `pass_all = true`（cell_type_registry.json:316，attr_decoded.pass_all）

"空转移"的意思是：不做过滤计算，直接把源股票传到目标。

### 5.4 为什么需要转移节点？直接连不行吗？

这是个好问题。从代码看，技术上完全可以不用条件节点，直接在状态池之间连一条 conditional 边。

但为什么股票池的设计里要有条件节点？有几个原因：

**1. 三元组结构（变换单元）是股票池的标准模式**
- 入边（条件转移边）+ 条件节点 + 出边（无条件转移边）
- 这是 TDX/DZH 股票池的标准拓扑结构
- 条件节点是"过滤逻辑"的可视化载体

代码证据：
- `_group_transformation_units` 将边分组为三元组（engine.py:2217-2247）
- `transformation_unit_strategies` 定义了三种条件节点的变换单元策略（edge_strategies.json:197-226）

**2. 条件节点承载筛选公式/条件的配置**
- DZH 条件节点（type=201）：params 中有 `indi`, `inditype`, `indiparam`, `crc` 等公式相关字段
- TDX 条件节点（type=3）：params 中有 `tdx_func` 包含 `nset, ntjindexno, accode, noperate` 等条件字段

代码证据：
- cell_type_registry.json:275-328（201号节点，转移条件）
- cell_type_registry.json:650-704（tdx_3，TDX转移条件）

**3. 入边和出边的语义不同**
- 入边（conditional）：有时间属性（interval, begin, end），控制"什么时候评估条件"
- 出边（unconditional）：没有时间属性，"条件节点一变就传过去"
- 条件节点是这两种语义的分界点

代码证据：
- edge_semantics.json 中 conditional 边 `has_time_attributes: true`，unconditional 边 `has_time_attributes: false`（edge_semantics.json:1）

**4. 可视化需求**
- 用户需要在画布上看到"哪里是过滤点"
- 三角形的条件节点直观地表示"这里有筛选逻辑"

### 5.5 条件节点和转移节点的区别是什么？

用户问的是"条件节点和转移节点的区别"。在代码中：

**它们是同一个东西的不同叫法。**

- 代码里叫 `transfer_condition`（转移条件）
- 口语中常叫"条件节点"
- 都是指 type=201（DZH）或 type=3（TDX）的节点

从 edge_strategies.json 的 `transformation_unit_strategies` 可以看到三个名字指同一类东西：
- `tdx_condition`（TDX条件节点）
- `transfer_condition`（DZH转移条件）
- `dzh_condition_pool`（DZH条件池）

它们都是"变换单元的枢纽节点"，功能相同，只是来源不同。

代码证据：edge_strategies.json:197-221

---

## 6. 概念模型 vs 代码实现的映射关系

### 6.1 概念层 vs 实现层的对应

| 用户的概念模型 | 代码中的对应物 | 代码位置 |
|---------------|--------------|---------|
| 节点 | `node_stocks[nid]`（运行时股票列表） + `pool_config.nodes[nid]`（配置） | engine.py:132, engine.py:758 |
| 节点计算 | MetaEngine 的筛选方法（`_filter_conditional` / `_filter_formula_eval` 等） | engine.py:2528, 2629 |
| 边（参数+触发条件） | `edge.params`（配置） + 编译后的各种 spec 表 | engine.py:245-272 |
| 触发条件（节点的事件配置） | 边的 `starttype` / `cxtype` / `begin` / `end` / `interval` | engine.py:787-788, 1020 |
| 转移节点 | type=201/3 的节点（transfer_condition / tdx_condition） | dzh_type_map.json:12, 48 |
| 空转移节点 | unconditional filter（`_filter_unconditional`）或 pass_all=true | engine.py:2476, cell_type_registry.json:316 |
| 无转移节点 | 没有出边的节点（`out_edges[nid]` 为空） | engine.py:263 |

### 6.2 为什么看起来不一样？

**视角不同**：
- 用户视角（概念模型）：从"节点"看——节点持有股票，节点有计算能力，节点有事件配置
- 代码视角（实现模型）：从"引擎"看——引擎驱动一切，节点是数据容器，边是配置规则

**本质是同一个东西的两种表述**：
- "节点计算" = 引擎对节点的股票执行筛选逻辑
- "边是触发条件" = 边的配置决定了什么时候、怎样执行筛选
- "节点的事件配置" = 节点的出边配置

### 6.3 执行流的两种解读

**解读 1：边驱动（代码视角）**
```
遍历边 → 检查边的触发条件 → 执行边的筛选 → 更新目标节点
```

**解读 2：节点驱动（用户视角）**
```
节点收到事件（数据更新/股票变化） → 节点执行计算（筛选） → 结果输出到下游节点
```

这两种解读描述的是同一个过程，只是主语不同：
- 边驱动的主语是"边"：边被遍历，边有触发条件，边有筛选逻辑
- 节点驱动的主语是"节点"：节点响应事件，节点执行计算，节点输出结果

在代码中，因为所有逻辑都在 MetaEngine 里，所以两种解读都只是"说法"，真正的执行主体是引擎。

---

## 7. 哪个更本质？为什么代码是现在这个样子？

### 7.1 哪个更本质？

**节点更本质。**

理由：

1. **股票池的目的是管理股票在不同"池"之间的流转**
   - "池"就是节点（状态池、备选池、条件池...）
   - 股票池的核心状态是"每个池里有哪些股票"
   - 边只是描述"池之间怎么流转"

2. **边的存在依赖于节点**
   - 没有节点，边没有意义（边必须有 from 和 to）
   - 没有边，节点仍然可以存在（就是一个静态的股票集合）

3. **用户的心智模型是节点中心的**
   - 大家说"黑马池"、"强势股池"——这些都是节点
   - 不说"黑马边"、"强势股边"
   - 条件节点也是"节点"，是"做条件计算的节点"

4. **计算的对象是节点的股票**
   - 筛选逻辑的输入是"源节点的股票"
   - 筛选逻辑的输出是"目标节点的股票"
   - 计算的目的是更新节点状态

### 7.2 为什么代码是现在这个样子？

代码用"边驱动"的实现方式，有几个技术原因：

**1. 时机守门（gate）是边的属性，不是节点的属性**
- 每条边有独立的 starttype / cxtype 配置
- 同一个节点的不同出边，可以有不同的触发时机
- 所以按边遍历、每条边独立检查 gate 是自然的

代码证据：engine.py:3608-3627（遍历节点出边，每条独立检查）

**2. 流转模式（copy/move/overwrite）是边的属性**
- 不同的边可以有不同的流转模式
- 比如一条边是 copy（保留源），另一条边是 move（移动）
- 这些配置在边的 attr/params 中

**3. 筛选策略是边两端节点类型的函数**
- 策略 key 是 `源节点类型:目标节点类型`
- 比如 `tdx_candidate:tdx_condition` 用 `tdx_condition_eval`
- 不同的边（连接不同类型的节点）有不同的筛选逻辑

代码证据：edge_strategies.json:35-183（strategies 段）

**4. 数据驱动的实现方式**
- 整个代码base是"表驱动 + 数据驱动"的风格
- 节点和边都是数据（dict）
- 逻辑在引擎方法中，通过查表分派
- 这种架构下，"边驱动"实现起来更直接：遍历边→查表→执行

**5. 历史原因：从 TDX/DZH 的原始实现演化而来**
- 原始股票池文件（.xml / .pool）中，边（flow）有自己的属性和逻辑
- 代码可能最初是按边来组织的
- 现在的 MetaEngine 是多次重构后的结果，但保留了边驱动的结构

### 7.3 如果按"节点中心"重写会长什么样？

（纯思考，不是建议）

如果完全按用户的概念模型来设计，代码结构可能是：

```python
class Node:
    node_id: str
    node_type: str
    stocks: list  # 持有股票
    out_edges: list[EdgeConfig]  # 出边配置（触发条件+筛选规则）
    
    def compute(self, context):
        # 检查每条出边的触发条件
        # 对满足条件的出边，执行筛选
        # 将结果发送到目标节点
        for edge in self.out_edges:
            if edge.should_trigger(context):
                passed = edge.filter(self.stocks, context)
                context.get_node(edge.to).receive(passed)
```

但这样做的问题是：
- 节点之间的依赖顺序（拓扑序）仍然需要引擎管理
- 全局数据（行情数据、时间）仍然需要外部注入
- 级联传播仍然需要协调
- 最终还是需要一个"引擎"来 orchestrate

所以**本质上，节点计算和边配置不矛盾**：
- 节点是计算的"主体"（持有数据、定义角色）
- 边是计算的"规则"（什么时候算、怎么算、结果去哪）
- 引擎是计算的"执行者"（按规则执行、协调顺序）

---

## 8. 对股票池本质的最新理解（一句话）

**股票池是一个由股票容器节点组成的有向图，边是节点之间的流转配置（触发时机+筛选规则+流转模式），每个 tick 由引擎检测变更事件并按拓扑序执行筛选计算，将符合条件的股票从源节点转移到目标节点。**

---

## 附录：关键代码引用速查表

| 概念 | 代码位置 | 说明 |
|------|---------|------|
| node_stocks 运行时表 | engine.py:132 | 节点股票数据是一等公民 |
| _dirty_nodes | engine.py:385 | 脏节点集合 |
| _node_snapshots | engine.py:385 | 节点股票快照（变更检测用） |
| _mark_dirty | engine.py:2062 | 标记节点为脏 |
| _update_node_snapshot | engine.py:2188 | 更新快照，变化则标脏 |
| _run_tick_event_driven | engine.py:3578 | 事件驱动核心循环 |
| _should_trigger_edge | engine.py:1757 | 时机守门 |
| _apply_edge_filter | engine.py:2459 | 统一筛选入口 |
| _filter_unconditional | engine.py:2476 | 无条件直通 |
| _filter_conditional | engine.py:2528 | 条件过滤（nset_dispatch） |
| _filter_formula_eval | engine.py:2629 | 公式批量求值 |
| _process_edge_pipeline | engine.py:2713 | 统一边处理流水线 |
| CompiledSchedule.edge_ctx | engine.py:245 | 预计算的边上下文 |
| CompiledSchedule.edge_timing | engine.py:246 | 编译期时机门控规则 |
| CompiledSchedule.edge_filter_spec | engine.py:248 | 编译期筛选分派规则 |
| CompiledSchedule.edge_flow_spec | engine.py:250 | 编译期状态流转规则 |
| edge_semantics.json | config/edge_semantics.json | 边类型语义（条件边/无条件边） |
| edge_strategies.json | config/edge_strategies.json | 边策略表 |
| cell_type_registry.json | config/cell_type_registry.json | 节点类型注册表 |
| dzh_type_map.json | config/dzh_type_map.json | DZH 类型映射 |

---

> 本文档所有结论均从代码提取，未发明任何新概念。
