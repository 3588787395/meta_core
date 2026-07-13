# v2.5 代码理解笔记：股票池真实运行逻辑

> **注意**：本文档不是设计文档，是从代码中读到的真相。
> 所有结论都有代码引用，不含主观臆断。

---

## 1. 综合设置的真实结构

### 1.1 表格列定义

综合设置表格有 **3 列**，表头为：

| 列序号 | 列名 | 含义 |
|--------|------|------|
| 第1列 | 流程标识 | 左列彩色标签 + ▶ 箭头前缀，承担"选股流程结构图"视觉表达 |
| 第2列 | 条件/属性 | 中栏，上半显示条件描述 + 条件设置按钮，下半显示状态属性 + 状态属性设置按钮 |
| 第3列 | 时序/操作 | 右栏，显示上游边时序参数 + 时序设置按钮 |

**代码引用**：
- `editor.js:1355-1356` — 表头注释明确写"表头: 流程标识 | 条件/属性 | 时序/操作"
- `editor.js:2093-2098` — 渲染函数注释详细描述三列布局

### 1.2 行类型

综合设置表格的行有以下几种类型：

| 行类型 | 说明 | 对应后端对象 |
|--------|------|--------------|
| `source` | 备选池声明行（首行） | node（type=202 或 7） |
| `pool-decl` | 状态池声明行 | node（type=200 或 8） |
| `edge` | 复合边行（一条边对应一行） | edge + 可能的 condition node |

每行 = 一条复合边（或备选池首行），全部唯一，不存在"重复行"概念。

**代码引用**：
- `editor.js:1868-1876` — source 行定义
- `editor.js:1911-1919` — pool-decl 行定义
- `editor.js:1891-1899` — edge 行定义
- `editor.js:2098` — "每行 = 一条复合边"

### 1.3 行的顺序

行的顺序 **不是简单的执行顺序**，而是 **DZH深度优先（DZF）遍历顺序**：

1. 从每个备选池（source pool）独立开始 BFS/DFS
2. 先输出该父池的所有 EDGE 行（同级边批量输出）
3. 对每个新发现的目标池：输出声明行 + 立即递归其子树
4. 第一个池完全展开后才处理第二个池（深度优先）

边的顺序按 `edgeIndex` 排序（即 XML 中边的原始顺序）。

**代码引用**：
- `editor.js:1880-1928` — `processPoolRecursive` 函数实现 DZH 深度优先遍历
- `editor.js:1884-1885` — 边按 edgeIndex 排序
- `editor.js:1921-1923` — 立即递归子树（深度优先）

### 1.4 后端数据结构

每一行对应的数据结构不是单一的，而是复合的：

**edge 行**对应：
- `upstreamEdge` — 上游边（条件转移边，有 begin/begint/interval 等时序属性）
- `conditionNode` — 条件节点（type=201，可能为 null 表示无条件转移）
- `cell` — 目标池节点（type=200/8/202/7）

**source / pool-decl 行**对应：
- `cell` — 节点本身（备选池或状态池）

**代码引用**：
- `editor.js:1891-1899` — edge 行结构定义
- `editor.js:1826-1856` — candidates 构建逻辑，定义复合边候选

### 1.5 边类型（Edge Types）

边分两大类，类型由**源节点类型**决定（不是目标节点）：

| 边类型 | 源节点类型 | 有时序属性 | 触发规则 | 角色 |
|--------|-----------|-----------|----------|------|
| conditional（条件转移边） | 备选池/状态池/数据源 | 有 | gate通过且(源节点股票变化或行情数据变化) | 入边（in_edge） |
| unconditional（无条件转移边） | 条件节点 | 无 | 源节点变化即反映到目标 | 出边（out_edge） |

**代码引用**：
- `edge_semantics.json:1` — 边类型语义配置表
- `edge_semantics.json:4-10` — conditional 边定义
- `edge_semantics.json:11-15` — unconditional 边定义

### 1.6 变换单元（三元组）

一个完整的"条件转移"由三部分组成（变换单元 / transformation unit）：

```
条件转移边(入边) + 转移条件(枢纽节点) + 无条件转移边(出边)
```

将一个状态池经条件计算变换为下一个状态池。

**代码引用**：
- `edge_semantics.json:16-23` — transformation_unit 定义
- `engine.py:2217-2247` — `_group_transformation_units` 函数实现三元组分组

---

## 2. 触发机制的真实逻辑

### 2.1 触发条件的组合逻辑

一条边真正触发执行的条件是：

```
triggered = edge_fired AND (node_dirty[sid] OR data_dirty)
```

即：
- **edge_fired**：时间/时机触发判定通过（gate + tdx_should_execute + duration check）
- **node_dirty[sid]**：源节点股票列表发生变化
- **data_dirty**：行情数据发生变化

三者关系：时间条件是"门"，节点变化或数据变化是"燃料"，门开了且有燃料才会燃烧。

**代码引用**：
- `engine.py:3621-3627` — `_run_tick_event_driven` 中的触发判定
- `engine.py:3583-3584` — 函数注释明确写 "triggered = edge_fired AND (node_dirty[sid] OR data_dirty)"

### 2.2 触发时间没到的时候

触发时间没到的时候，**跳过不算**——既不计算也不更新，直接跳过整条边。

代码中 `_should_trigger_edge` 返回 False 时，直接 `continue` 跳过该边，不会进入 filter 计算，也不会更新节点股票。

**代码引用**：
- `engine.py:3622-3629` — fired 为 False 时直接 continue

### 2.3 数据更新了但触发时间没到

数据更新会置 `data_dirty = True`，但如果 `edge_fired = False`（时间没到），边仍然不会执行。
数据更新的影响会**累积**，等下一次时间条件满足时，因为 data_dirty 已经是 True，所以会立即触发。

**代码引用**：
- `engine.py:2095-2115` — `_refresh_latest_tick` 置 data_dirty
- `engine.py:3626-3627` — 触发判定用 data_dirty

### 2.4 触发时间到了但数据没更新

触发时间到了（edge_fired = True），但数据没更新（data_dirty = False）且源节点也没变化（node_dirty = False），边**不会执行**。

这意味着：即使到了执行间隔，如果数据没变，也不会做无用计算。

**代码引用**：
- `engine.py:3626-3629` — triggered 为 False 时 continue

### 2.5 触发类型（Gate 评估器）

有 3 种 gate 评估器，对应 3 种运行模式：

| 模式 | Gate 评估器 | 时间源 |
|------|------------|--------|
| live（实盘） | live_gate | wall_clock（系统时间） |
| replay（回放） | replay_gate | kline_timeline（K线时间轴） |
| simulation（仿真） | virtual_gate | virtual_clock（虚拟时钟） |

**代码引用**：
- `runtime_modes.json:17,35,52` — 三种模式的 gate_evaluator_id
- `timing.json:134-149` — gate_evaluator 配置表

### 2.6 开始时间（begin / starttype）

starttype 有 8 种模式：

| starttype | 名称 | 语义 |
|-----------|------|------|
| 0 | immediate / 立即 | 总是触发（无 begin 限制） |
| 1 | delay / 延迟 | 从股票池启动开始经过 begint 秒后触发 |
| 2 | before_open / 开市前 | 开市前 begint 秒开始（区间：open - offset 到 open） |
| 3 | after_open / 开市后 | 开市后 begint 秒开始 |
| 4 | before_close / 收市前 | 收市前 begint*60 秒开始（区间） |
| 5 | after_close / 收市后 | 收市后 begint*60 秒开始 |
| 6 | trading_day / 交易日 | 总是触发（交易日） |
| 7 | specific_time / 指定时间 | 每天 begint 时刻（HHMMSS）触发 |

**代码引用**：
- `timing.json:3-12` — starttype_rules 表
- `timing.json:63-72` — begin_type_labels（simulator 段）

### 2.7 结束时间（end / cxtype）

cxtype（持续期类型）有 3 种模式：

| cxtype | 名称 | 过期判定 |
|--------|------|----------|
| 0 | forever / 永久 | 永不过期（never） |
| 1 | duration / 持续期 | 从首次触发经过 cxtime 后过期 |
| 2 | once / 一次性 | 执行一次后过期（count >= 1） |

注意：cxtype 是"持续期"的概念，是对"条件生效多久"的描述，不是简单的"结束时间点"。

**代码引用**：
- `timing.json:13-28` — cxtype_rules 表
- `engine.py:1013-1030` — `_tdx_check_duration` 函数

### 2.8 间隔（interval）

interval_sec 是**两次触发的最小间隔**（秒）。

实现方式：记录 `_flow_last_fire_ts[eid]`，每次触发前检查 `cur_ts - last < interval_sec`，是则跳过。

**代码引用**：
- `engine.py:1087-1091` — interval 守门逻辑
- `timing.json:1051` — "interval_sec: 两次触发的最小间隔（秒）"

### 2.9 优先级和层次

触发判定的层次（从外到内）：

```
1. replay gate（回放模式才有） — begin/end/interval
2. tdx_should_execute — starttype（开始时机）
3. tdx_check_duration — cxtype（持续期/是否过期）
4. node_dirty OR data_dirty — 变化检测
```

**代码引用**：
- `engine.py:1757-1785` — `_should_trigger_edge` 统一入口
- `engine.py:1768-1777` — replay 模式 gate
- `engine.py:1778-1779` — tdx_should_execute
- `engine.py:1780-1781` — tdx_check_duration

---

## 3. 执行流程的真实顺序

### 3.1 一个 tick 内的完整流程

```
_tick()
├── _pre_tick()                    [数据注入层]
│   ├── stage_bar_data_inject      — 将 bar 数据注入到 node_stocks
│   └── stage_minute_aggregator_feed — 喂数据给分钟线合成器
│
├── snapshot (prev)                — 保存 tick 前的节点股票快照
├── _update_node_snapshot (所有节点) — 更新节点快照 + 变更检测 + 自动标脏
│
├── _run_tick_event_driven()       [计算层 - 核心]
│   ├── _refresh_latest_tick       — 刷新行情 + 置 data_dirty
│   ├── 首次运行时标脏所有源节点
│   ├── 按拓扑序遍历所有边
│   │   ├── _should_trigger_edge   — 时间 gate 判定
│   │   ├── 触发判定: edge_fired AND (node_dirty OR data_dirty)
│   │   ├── 若触发:
│   │   │   ├── _process_edge_pipeline
│   │   │   │   ├── _apply_edge_filter  — 筛选 + 流转 + 变换
│   │   │   │   └── _run_post_propagate_hooks — 后处理 hooks
│   │   │   └── _mark_dirty(tid)    — 标记目标节点为脏（级联传播）
│   │   └── 若未触发: continue
│   ├── 更新 _last_snapshot
│   └── _clear_dirty               — 清脏（含 data_dirty + edge_fired）
│
├── _update_trackers               — 更新 tracker（价格、收益等）
├── _emit_transfer_events          — 批量发射转移事件
└── _post_tick()                   [后处理阶段]
    ├── PK排名
    ├── 多分析角度
    ├── 看盘面板
    └── 监控告警
```

**代码引用**：
- `engine.py:3650-3661` — `_tick` 函数
- `engine.py:3578-3648` — `_run_tick_event_driven` 函数
- `engine.py:2713-2735` — `_process_edge_pipeline` 函数
- `engine.py:3492-3545` — `_emit_transfer_events` 函数

### 3.2 事件发送时机

**不是**每条边执行完就立即发事件，而是：

1. 边执行过程中，只记录 transfer_events（转移事件列表）
2. 等所有边都执行完（整个 tick 计算完成）
3. 在 `_emit_transfer_events` 中**批量**发射事件

事件分两类：
- **transfer_events**（流转事件）：边执行时累积，tick 末统一发射
- **领域事件**（pool_enter / move_exit / ttl_expire 等）：在 `_emit_transfer_events` 中通过 event_domain_templates 模板生成

**代码引用**：
- `engine.py:3660-3661` — `_run_tick_event_driven` 返回 tevs（transfer events），然后才 `_emit_transfer_events`
- `engine.py:3492-3545` — `_emit_transfer_events` 批量处理
- `engine.py:2974` — `_post_handle_new_entries` 也是在边执行过程中（post hook）

注意：`_post_handle_new_entries` 是在单条边的 post hook 里调用的，它会**立即**调用 `_push_event` 推送事件。所以有两种事件发射路径：
- 单条边的 post hook（如 handle_new_entries）— 边执行完立即发
- tick 末的 `_emit_transfer_events` — 所有边执行完统一发

**代码引用**：
- `edge_strategies.json:271-274` — post_propagate_hooks 列表，第 2 个就是 handle_new_entries
- `engine.py:2896-2918` — `_run_post_propagate_hooks` 逐条执行 hook

### 3.3 节点股票列表的更新时机

节点股票列表（node_stocks）在 **filter 执行过程中立即更新**，不是等所有边都执行完。

具体在 `_apply_edge_filter` → `_filter_unconditional` 或 `_filter_conditional` 中：
- 目标池更新（合并或替换）— 立即写入 node_stocks[tid]
- 源池更新（move 模式时移除）— 立即写入 node_stocks[sid]

然后立即标记目标节点为 dirty，这样在拓扑序遍历时，下游边在同一个 tick 内就能感知到变化。

**代码引用**：
- `engine.py:2513-2524` — `_filter_unconditional` 中直接修改 node_stocks
- `engine.py:3637-3641` — 边执行完后 `_mark_dirty(tid)` 级联传播

### 3.4 拓扑排序与级联传播

边的执行顺序是**按拓扑序**（深度升序），但因为有 dirty 标记，级联传播可以在**同一个 tick 内**发生：

1. 边 A→B 执行，B 节点股票变化
2. B 被标记为 dirty
3. 继续遍历拓扑序，当处理 B 的出边 B→C 时
4. 因为 B 是 dirty 的，所以 B→C 也会触发（如果时间条件满足）

这样一条链路上的多级转移，可以在同一个 tick 内完成。

**代码引用**：
- `engine.py:3606` — sorted_nodes = compiled.topo_order（已按深度升序预排序）
- `engine.py:3638-3641` — 边执行后标记目标节点 dirty，级联传播

### 3.5 三种 Filter 类型

| filter_type | 调度方法 | 说明 |
|-------------|---------|------|
| unconditional | `_filter_unconditional` | 恒等过滤：全部源股票通过 |
| conditional | `_filter_conditional` | nset 分派过滤：按策略分派 |
| formula_eval | `_filter_formula_batch` | 公式批量求值过滤 |

**代码引用**：
- `engine.py:2459-2474` — `_apply_edge_filter` 统一入口
- `edge_strategies.json:227-243` — edge_filter_registry 表

### 3.6 后处理 Hooks（单条边级）

每条边执行完后，会按顺序执行 3 个 post hook：

| 顺序 | Hook | 作用 |
|------|------|------|
| 1 | record_execution | 记录执行次数和首/末次触发时间戳 |
| 2 | handle_new_entries | 检测新入池 + 创建 tracker + 发射事件 + 回调 |
| 3 | apply_ttl | TTL 过期检查（条件：节点类型在 auto_ttl_node_types 中） |

**代码引用**：
- `edge_strategies.json:271-274` — post_propagate_hooks 列表
- `engine.py:2950-2957` — `_post_record_execution`
- `engine.py:2974` — `_post_handle_new_entries`
- `engine.py:3007` — `_post_apply_ttl`

---

## 4. 数据层与引擎层的真实边界

### 4.1 K线合成在哪里做的

K线（分钟线）合成在 **services/minute_aggregator.py** 的 `Min1Aggregator` 类中：

- 输入：逐笔 Tick 数据（symbol, time, price, volume）
- 输出：1分钟K线（OHLCV）
- 实现：使用预分配 numpy 数组，热路径无磁盘 I/O，全内存处理

更高周期（5m/15m/30m/60m）的合成代码里没有看到专门的合成器，应该是在 DataQuery 中通过聚合 1 分钟线实现的。

**代码引用**：
- `minute_aggregator.py:33-42` — Min1Aggregator 类定义
- `minute_aggregator.py:63-86` — on_tick 热路径处理
- `data.py:85-130` — DataQuery.get_kline_series 读取不同周期

### 4.2 股票池引擎直接用K线数据，还是自己再加工

股票池引擎**不直接用完整K线序列**，而是通过 `current_bar_data` 使用**当前 bar 的快照数据**。

具体流程：
1. 数据层通过 `_refresh_bar_data` 获取最新行情快照
2. 通过 `_pre_tick` → `stage_bar_data_inject` 将 bar 数据注入到 node_stocks（每只股票附加当前bar信息）
3. 公式计算时，通过 formula_router → data_query 去查完整 K 线历史

股票池核心流转逻辑只需要当前 bar 的 close/high/low/open/volume 等字段，不需要完整K线序列。
完整K线序列只在公式求值时才需要，由 formula_router 按需从 data_query 获取。

**代码引用**：
- `engine.py:3662-3671` — `_refresh_bar_data` 获取最新 bar 快照
- `pre_tick_pipeline.json:5-13` — stage_bar_data_inject 注入 bar 数据
- `formula_router.py:18-21` — 公式路由层不持有数据源，K线由 data_query 提供
- `engine.py:3547-3575` — `_pre_tick` 是数据层，`_run_tick_event_driven` 是计算层

### 4.3 数据更新怎么通知到股票池引擎

数据更新通知通过 **dirty 标记机制**实现，不是通过事件订阅/回调模式：

1. 数据更新后，调用 `_refresh_latest_tick(bar_data)`
2. 计算 bar_data 的 hash，如果 hash 变化，置 `_data_dirty = True`
3. 在下一轮边执行判定时，`data_dirty` 作为触发条件之一

另外，`_on_data_updated` 是另一个接口，可以显式标记指定节点为 dirty。

**代码引用**：
- `engine.py:2095-2115` — `_refresh_latest_tick` 置 data_dirty
- `engine.py:2129-2155` — `_on_data_updated` 显式标记节点 dirty
- `engine.py:3626-3627` — 触发判定用 data_dirty

### 4.4 数据层与计算层的职责分离

代码中有非常明确的职责边界注释：

```
pre_tick 是数据层：负责外部数据注入（bar_data_inject、分钟线聚合等），
  将最新行情数据准备好供计算层使用。
_run_tick_event_driven 是计算层：负责股票流转逻辑，
  根据数据层提供的行情数据进行条件判断和节点间股票转移。
两者职责分离：数据更新是外部事件，不是股票池核心循环的一部分。
```

**代码引用**：
- `engine.py:3550-3555` — `_pre_tick` 函数的职责边界说明注释

### 4.5 数据流向图

```
外部行情源 (TQ/akshare 等)
    │
    ▼
DataService / refresh_handler
    │
    ▼
current_bar_data (当前 bar 快照)
    │
    ├──► Min1Aggregator (分钟线合成) ──► closed_bars (内存) ──► DataQuery
    │                                       ▲
    │                                       │ 历史 parquet
    │                                       ▼
    └──► _pre_tick (数据注入)
            │
            ▼
    node_stocks + _latest_tick
            │
            ▼
    _run_tick_event_driven (计算层)
            │
            ├──► 公式求值 → formula_router → data_query → K线序列
            │
            ▼
    节点股票转移 + 事件发射
```

**代码引用**：
- `pre_tick_pipeline.json` — pre_tick 包含 bar_data_inject 和 minute_aggregator_feed
- `formula_router.py:81-99` — FormulaRouter 持有 data_query
- `data.py:39-130` — DataQuery 读取历史 + 今日分钟线

---

## 5. 我之前理解错了的地方

### 错误 1：综合设置的行对应"计算单元"

**之前理解**：每一行对应一个计算单元。
**代码真相**：每一行对应一条**复合边**（上游边 + 条件节点 + 目标池），不是一个独立的计算单元。真正的执行单位是**边（edge）**，不是行。

**引用**：`editor.js:2098` — "每行 = 一条复合边"

### 错误 2：行的顺序就是执行顺序

**之前理解**：表格从上到下的顺序就是执行顺序。
**代码真相**：行的顺序是 DZH 深度优先遍历的显示顺序，不是严格的执行顺序。真正的执行顺序是**拓扑序**（深度升序），且有 dirty 级联传播机制，同一条链路可以在一个 tick 内连续触发多级。

**引用**：`engine.py:3606` — "sorted_nodes = compiled.topo_order  # 已按深度升序预排序"

### 错误 3：触发时间到了就一定会执行

**之前理解**：到了执行间隔就会执行计算。
**代码真相**：触发需要两个条件同时满足：时间条件到达 **AND**（源节点变化 OR 数据变化）。时间到了但数据没变，不会执行。

**引用**：`engine.py:3626-3627` — "triggered = fired and (self._is_dirty(sid) or self._is_data_dirty())"

### 错误 4：触发时间没到但数据更新了，会缓存计算结果

**之前理解**：数据更新了但时间没到，会先算好存着，等时间到了直接用。
**代码真相**：时间没到就直接跳过，不算也不存。数据更新的影响通过 data_dirty 标记累积，等时间到了一起算。

**引用**：`engine.py:3628-3629` — "if not triggered: continue"

### 错误 5：每条边执行完立即发事件

**之前理解**：边执行完就立即向外发射事件。
**代码真相**：有两种事件发射路径：
- 单条边的 post hook（如 handle_new_entries）— 边执行完立即发
- tick 末的 `_emit_transfer_events` — 所有边执行完统一批量发

**引用**：`engine.py:3660-3661` — tick 末才调用 `_emit_transfer_events`

### 错误 6：股票池引擎持有完整K线数据

**之前理解**：股票池引擎自己维护K线数据。
**代码真相**：股票池引擎只持有**当前 bar 快照**（_latest_tick / current_bar_data）。完整K线序列由 DataQuery 负责，公式求值时通过 formula_router 按需查询。

**引用**：`formula_router.py:18-21` — "公式路由层不通过 PythonFormulaEngine 间接持有数据源。K 线数据由注入的 data_query 提供"

### 错误 7：K线合成在引擎内部

**之前理解**：K线合成是股票池引擎的一部分。
**代码真相**：K线（分钟线）合成在 services/minute_aggregator.py，是独立的服务。引擎通过 pre_tick 喂数据给 aggregator，但合成逻辑不在引擎里。

**引用**：`pre_tick_pipeline.json:14-22` — minute_aggregator_feed 是 pre_tick 的一个 stage

### 错误 8：边的类型由目标节点决定

**之前理解**：指向条件节点的边是条件边。
**代码真相**：边的类型**由源节点类型决定**。源是备选池/状态池 → 条件边（有 interval）；源是条件节点 → 无条件边（无时间属性）。

**引用**：`edge_semantics.json:1` — "边类型由源节点类型决定"

### 错误 9：综合设置表格是节点列表

**之前理解**：综合设置表格列的是所有节点。
**代码真相**：综合设置表格列的是**边**（复合边），不是节点。节点只是作为边的端点出现。备选池和状态池有单独的"声明行"，但主体是边。

**引用**：`editor.js:2093-2098` — 行类型说明

### 错误 10：TTL 检查是全局统一的

**之前理解**：每个 tick 结束时统一检查所有节点的 TTL。
**代码真相**：TTL 检查是**单条边级**的 post hook（第 3 个 hook），在每条边传播后对目标节点执行。且只对特定节点类型（auto_ttl_node_types）生效。

**引用**：`edge_strategies.json:274` — "apply_ttl hook 的 when 条件：ctx.get('tt') in config('tdx_psatt.auto_ttl_node_types')"

### 错误 11：数据更新通过事件订阅通知

**之前理解**：数据层通过事件/回调通知引擎数据更新了。
**代码真相**：数据更新通过 **dirty 标记**机制传递，不是事件订阅。数据层更新 data_dirty 标志，计算层在下一轮检查这个标志。

**引用**：`engine.py:2095-2115` — `_refresh_latest_tick` 置 data_dirty

### 错误 12：begin/end 是简单的时间点

**之前理解**：begin 是开始时间点，end 是结束时间点。
**代码真相**：begin 有 8 种模式（立即/延迟/开市前/开市后/收市前/收市后/交易日/指定时间），end 也有多种模式（无限/从首次触发计时/一次/指定时间）。不是简单的两个时间点。

**引用**：`timing.json:3-12` — starttype_rules 有 8 种
**引用**：`timing.json:93-100` — end_type_handlers 有多种

---

## 6. 对股票池运行本质的新理解

**一句话总结**：股票池引擎本质上是一个**事件驱动的有向图状态机**——节点是股票集合状态，边是带时间门控的状态转移函数，数据变化和时间流逝是两类输入事件，dirty 标记是增量计算的驱动机制，所有转移在 tick 内按拓扑序级联传播，tick 末批量发射领域事件。
