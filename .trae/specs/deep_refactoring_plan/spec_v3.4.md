# v3.4 核心概念精准定义：节点 · 边 · 计算 · 触发 · 事件

> 纯理解笔记，不做设计建议。所有定义均从代码中提取，每个结论都有代码引用。

---

## 1. 节点（Node）

### 1.1 本质定义

**节点是股票池拓扑图中的顶点，承载股票集合（持仓状态），是数据流转的载体。**

节点是一个有类型、有配置、有运行时状态的实体，通过边与其他节点相连。节点的核心价值是持有一组股票（node_stocks），股票在节点之间通过边流动。

### 1.2 代码对应数据结构

节点在代码中是一个 **dict 字典**，不使用类封装。

- **配置层**：`pool_config.nodes` —— 节点配置列表/字典，来自股票池文件解析
  - `engine.py:1191-1225` —— `_prepare_topology` 中统一转换为 `{nid: node}` 字典
- **运行时**：`node_stocks[nid]` —— 节点持有的股票列表
  - `engine.py:758-780` —— `_init_node_stocks` 初始化节点股票
- **模型层**：`DynamicCellModel`（schemas.py:168）—— Pydantic 风格的通用节点模型，支持 dict 风格和属性风格访问

```python
# 节点字典的典型结构（来自代码推断）
node = {
    "id": "node_1",           # 节点唯一标识
    "type": 200,              # 节点类型（数字类型码）
    "params": {               # 节点参数（配置属性）
        "stocks": [...],      # 初始股票列表
        "tdx_psatt": {...},   # TDX 状态池属性
        "tdx_func": {...},    # TDX 条件公式
        "tdx_spinfo": {...},  # 备选池信息
        ...
    },
    "pos": "x1,y1,x2,y2",    # 画布位置
    "clr": -1,                # 颜色
    "text": "",               # 文字标签
    "attr": 0,                # 属性位标志整数
    ...
}
```

### 1.3 属性清单

#### 配置属性（来自 pool_config，静态不变）

| 属性 | 类型 | 说明 | 代码位置 |
|------|------|------|----------|
| `id` | str/int | 节点唯一标识 | `engine.py:1220-1223` |
| `type` / `cell_type` | int | 节点类型码（200=状态池, 201=条件, 202=备选池, 0=市场源, 等等） | `schemas.py:197` |
| `params` | dict | 节点参数字典（含所有业务配置） | `engine.py:1161` |
| `pos` / `position` | str/tuple | 画布坐标位置 | `schemas.py:224-235` |
| `clr` | int | 节点颜色 | `schemas.py:176` |
| `text` / `label` | str | 节点文字标签 | `schemas.py:176` |
| `attr` | int | 属性位标志整数（bit field） | `schemas.py:238-246` |

#### 运行时属性（运行中动态变化）

| 属性 | 存储位置 | 说明 | 代码位置 |
|------|----------|------|----------|
| 股票列表 | `node_stocks[nid]` | 节点当前持有的股票列表（list of dict） | `engine.py:758-780` |
| 脏标记 | `_dirty_nodes` set | 节点股票是否变化（dirty flag） | `engine.py:2062-2082` |
| 快照 | `_node_snapshots` dict | 节点股票代码集合快照（用于变更检测） | `engine.py:385` |
| 入边列表 | `in_edges[nid]` | 指向该节点的所有边 | `engine.py:1268` |
| 出边列表 | `out_edges[nid]` | 从该节点出发的所有边 | `engine.py:1267` |
| 拓扑深度 | `depths[nid]` | 节点在拓扑图中的深度（最长路径） | `engine.py:1235-1243` |

### 1.4 能做什么？不能做什么？

**能做：**
- 持有一组股票（核心职责）
- 作为边的起点或终点
- 提供股票给下游边进行筛选/流转
- 接收上游边流转过来的股票
- 有类型语义，不同类型决定边的行为（边类型由源节点类型决定）

**不能做：**
- 节点自身不执行计算（计算发生在边上）
- 节点不主动触发任何东西（触发由时间或数据变化驱动）
- 节点不直接和其他节点通信（必须通过边）

### 1.5 节点类型

节点类型由 `type` 字段（整数类型码）决定，**边的类型由源节点类型决定**（`edge_semantics.json:conclusion`）。

#### 按功能分类（来自 edge_semantics.json + pool_roles.json）

| 类型分类 | 类型码 | 角色 | 说明 |
|----------|--------|------|------|
| 市场源节点 | 0 / market_source | `market_source` | 数据流起点，提供初始候选股票 |
| 备选池节点 | 202 / tdx_candidate | `candidate_pool` | 候选股票池，中转或观察 |
| 状态池节点 | 200 / stock_state_pool / tdx_state_pool | `candidate_pool` 或 `target_pool` | 核心持仓池，baimpool=1 时为目标池 |
| 条件节点 | 201 / transfer_condition / tdx_condition | `transfer_condition` | 三元组枢纽，入边筛选，出边直通 |
| 丢弃池节点 | 4 / discard_pool | `sink_pool` / `discard_pool` | 数据流终点，不满足条件的股票进入 |
| 辅助节点 | 1(标签) / 2(容器) / 3(状态列) | - | 纯展示，不参与数据流转 |

#### 目标池 vs 备选池（角色区分）

角色不是节点的固有属性，而是运行时解析的：
- `baimpool=1` → 目标池（target_pool），生成 BUY/SELL 信号
- 默认 → 备选池（candidate_pool），不生成交易信号
- 解析规则：`pool_roles.json:role_resolution.rules`

### 1.6 和边的关系

- 节点是边的端点：每条边有一个源节点（from/source）和一个目标节点（to/target）
- 节点的入边 = 所有指向它的边 → `in_edges[nid]`
- 节点的出边 = 所有从它出发的边 → `out_edges[nid]`
- 边的类型由**源节点**的类型决定 → `edge_semantics.json:edge_types`
- 节点股票变化（dirty）会触发其所有出边的重新评估 → `engine.py:3626-3629`

---

## 2. 边（Edge）

### 2.1 本质定义

**边是股票池拓扑图中的有向连接，定义股票从源节点到目标节点的流转规则（时机、筛选、流转模式）。**

边是股票流动的"管道+阀门"：管道决定方向，阀门（gate）决定何时开启，筛决定哪些股票能通过，流转模式决定通过后是复制还是移动。

### 2.2 代码对应数据结构

边在代码中是一个 **dict 字典**。

- **配置层**：`pool_config.edges` —— 边配置列表，来自股票池文件解析
  - `engine.py:1215` —— `_prepare_topology` 读取 edges
- **运行时**：边配置本身不变，但每条边有运行时状态
  - `edge_index[eid]` —— 边索引字典（O(1) 查找）
  - `_edge_fired[eid]` —— 本 tick 时间触发是否到达

```python
# 边字典的典型结构
edge = {
    "id": "flow_1",           # 边唯一标识（flow_id）
    "from": "node_1",         # 源节点 ID
    "to": "node_2",           # 目标节点 ID
    "params": {               # 边参数（配置属性）
        "starttype": 0,       # 开始类型（时机门控）
        "starttime": 0,       # 开始时间
        "starttimetype": 0,   # 开始时间单位
        "cxtype": 0,          # 持续类型
        "cxtime": 0,          # 持续时间
        "tran": 0,            # 流转模式（0=copy, 1=move）
        "emptyps": 0,         # 空源处理
        "interval": 0,        # 触发间隔
        "begin": 0,           # 开始类型（回放）
        "begint": 0,          # 开始参数
        "end": 0,             # 结束类型
        "endt": 0,            # 结束参数
        ...
    },
    "attr": 0,                # 属性位标志整数
    "clr": -1,                # 颜色
    ...
}
```

### 2.3 属性清单

#### 配置属性

| 属性 | 类型 | 说明 | 代码位置 |
|------|------|------|----------|
| `id` / `flow_id` | str | 边唯一标识 | `engine.py:1230` |
| `from` / `source` / `startid` | str | 源节点 ID | `engine.py:1159` |
| `to` / `target` / `endid` | str | 目标节点 ID | `engine.py:1160` |
| `params` | dict | 边参数字典 | `engine.py:1161` |
| `attr` | int | 属性位标志（bit field） | `schemas.py:514-522` |
| `clr` | int | 边颜色 | `edge_semantics.json` |

#### 运行时属性 / 编译期属性

| 属性 | 存储位置 | 说明 | 代码位置 |
|------|----------|------|----------|
| `filter_type` | `edge_ctx[eid].filter_type` | 筛选类型：unconditional / conditional / formula_eval | `engine.py:1384` |
| `edge_fired` | `_edge_fired[eid]` | 本 tick 时间触发是否到达 | `engine.py:2117-2119` |
| `flow_exec_counts` | `_flow_exec_counts[eid]` | 边累计触发次数 | `engine.py:2952-2957` |
| `flow_first_fire_ts` | `_flow_first_fire_ts[eid]` | 首次触发时间戳 | `engine.py:2956` |
| `flow_last_fire_ts` | `_flow_last_fire_ts[eid]` | 末次触发时间戳 | `engine.py:2957` |
| 5 维 spec | `edge_timing/filter_spec/flow_spec/action_spec/ttl_spec` | 编译期预计算的5维规则表 | `engine.py:1433-1671` |

### 2.4 能做什么？不能做什么？

**能做：**
- 时机门控（gate）：判断当前是否允许触发
- 筛选计算（filter）：从源节点股票中选出满足条件的
- 状态流转（propagate）：将选出的股票复制/移动到目标节点
- 副作用（action）：TTL 淘汰、事件发射、tracker 更新等

**不能做：**
- 边不持有股票（股票存在节点里）
- 边不主动触发（触发由时间+数据变化驱动）
- 边不能反向流动（有向边，from → to）

### 2.5 边的类型

边的类型由**源节点类型**决定（`edge_semantics.json:conclusion`），不是由边自身的某个字段决定。

#### 两种基本类型（edge_semantics.json:edge_types）

| 边类型 | 源节点类型 | 触发规则 | 筛选方式 | 有无时间属性 | 角色 |
|--------|-----------|----------|----------|-------------|------|
| **条件转移边**（conditional） | 备选池/状态池/市场源 | gate_and_change（时机门通过 且 源节点变化或数据变化） | 条件筛选 | 有（interval/begin/end/count 等） | 三元组入边 |
| **无条件转移边**（unconditional） | 条件节点 | source_changed（源节点变化即触发） | 全部通过（identity） | 无（只有 from/to/attr/clr） | 三元组出边 |

#### 三种筛选模式（filter_type）

筛选类型是边的运行时分类，由 `_build_processing_plan` 预计算：

| filter_type | 说明 | 对应方法 | 代码位置 |
|-------------|------|----------|----------|
| `unconditional` | 无条件直通，全部股票通过 | `_filter_unconditional` | `engine.py:2476-2526` |
| `conditional` | 条件筛选，按策略分派（nset_dispatch） | `_filter_conditional` | `engine.py:2528-2627` |
| `formula_eval` | 公式批量求值，按公式结果筛选 | `_filter_formula_eval` | `engine.py:2629-2707` |

### 2.6 和节点的关系

- 边连接两个节点：源节点（from）→ 目标节点（to）
- 边从源节点取股票，筛选后写入目标节点
- 边的类型由源节点类型决定
- 源节点 dirty 或行情数据 dirty 时，边可能被触发执行
- 边执行后，如果目标节点股票变化，目标节点被标记为 dirty，级联触发其出边

---

## 3. 计算（Computation）

### 3.1 本质定义

**股票池中的"计算"是指：根据边的筛选规则，从源节点股票集合中选出满足条件的子集，并按流转模式更新源/目标节点的股票列表。**

计算的本质是**集合变换**：输入是源节点的股票集合，输出是满足条件的股票子集，副作用是源/目标节点的股票列表被更新。

### 3.2 计算什么？—— 输入与输出

#### 输入

1. **源节点股票列表**：`node_stocks[sid]` —— 待筛选的股票集合
2. **边配置参数**：`edge.params` —— 筛选规则、时机参数、流转模式等
3. **行情数据**：`current_bar_data` —— 用于公式求值或指标计算（可选）
4. **节点配置**：源节点和目标节点的 params —— 用于公式解析等（可选）

#### 输出

1. **通过筛选的股票代码集合**：`passed_codes` —— 满足条件的股票子集
2. **副作用**：
   - 目标节点股票列表更新（合并或替换）
   - 源节点股票列表更新（move 模式下移除通过的股票）
   - tracker 创建/更新
   - 事件发射
   - TTL 检查
   - 缓存写入

### 3.3 计算发生在哪里？—— 代码位置

核心计算逻辑在 `MetaEngine` 类中，分三层：

#### 第一层：tick 级总入口

- **`_run_tick_event_driven`** —— 事件驱动的主循环
  - 位置：`engine.py:3578-3648`
  - 职责：遍历所有边，判断是否触发，触发则调用 `_process_edge_pipeline`

#### 第二层：边处理流水线

- **`_process_edge_pipeline`** —— 单条边的处理流水线
  - 位置：`engine.py:2713-2735`
  - 职责：调用筛选 + 后处理 hooks
  - 步骤：
    1. `_apply_edge_filter` —— 按 filter_type 分派到 3 个核心筛选函数
    2. `_run_post_propagate_hooks` —— 后处理（tracker、TTL、事件等）

#### 第三层：三种核心筛选函数

| 筛选函数 | filter_type | 位置 | 核心逻辑 |
|----------|-------------|------|----------|
| `_filter_unconditional` | unconditional | `engine.py:2476-2526` | 全部通过，identity 变换 |
| `_filter_conditional` | conditional | `engine.py:2528-2627` | 按策略分派（nset_dispatch → 评估器） |
| `_filter_formula_eval` | formula_eval | `engine.py:2629-2707` | 公式批量求值（formula_router.eval_batch） |

#### 其他计算位置

- **`_update_trackers`** —— tracker 公式计算（每个股票的持仓跟踪指标）
  - 位置：`engine.py:3049-3077`
- **`_apply_tdx_psatt_ttl`** —— TTL 超时淘汰计算
  - 位置：`engine.py:1902-1942`
- **`_compile_pool`** —— 编译期计算（拓扑排序、预计算 spec）
  - 位置：`engine.py:1354-1431`

### 3.4 什么时候计算？—— 触发条件

计算不是持续进行的，而是由**触发条件**决定是否执行。详见第 4 章"触发"。

简要来说，一条边的计算被执行当且仅当：

```
triggered = edge_fired AND (node_dirty[sid] OR data_dirty)
```

即：**时机门通过** 且 **（源节点股票有变化 或 行情数据有变化）**

### 3.5 计算的种类

#### 按计算对象分类

| 种类 | 计算内容 | 发生位置 |
|------|----------|----------|
| 边筛选计算 | 从源节点选出满足条件的股票 | `_filter_unconditional` / `_filter_conditional` / `_filter_formula_eval` |
| 流转计算 | 按 copy/move/overwrite 模式更新节点股票 | 各 filter 函数内部 |
| tracker 计算 | 持仓跟踪指标（收益率、持仓天数等） | `_update_trackers` |
| TTL 计算 | 超时淘汰判断 | `_apply_tdx_psatt_ttl` |
| 时机门计算 | 判断当前是否允许触发 | `_should_trigger_edge` / `_eval_timing_primitive` |
| 编译期计算 | 拓扑排序、预计算 spec 表 | `_compile_pool` |

#### 按筛选机制分类

1. **直通型**：unconditional，全部股票通过
2. **策略分派型**：conditional，通过 nset_dispatch 分派到不同评估器（TDX 公式、DZH 条件等）
3. **公式批量型**：formula_eval，调用 formula_router 批量求值

### 3.6 "节点计算"和"边的筛选"是一回事吗？

**不是一回事。**

- **节点不计算**。节点只是持有股票的容器，是被动的。
- **计算发生在边上**。边是计算的主体，负责筛选和流转。
- 条件节点（type=201）也不计算，它只是三元组的"枢纽"——入边做筛选，出边做直通，条件节点本身只是一个暂存的容器。

> 关键证据：`edge_semantics.json:transformation_unit` 明确描述了变换单元是"条件转移边(入边) + 转移条件(枢纽节点) + 无条件转移边(出边)"，计算发生在入边上。

---

## 4. 触发（Trigger）

### 4.1 本质定义

**触发是指：判断一条边在当前 tick 是否应该执行其筛选和流转逻辑的判定过程。**

触发是"开关"——决定计算是否发生。触发本身不是计算，而是计算的前提条件。

### 4.2 触发什么？

触发的是**边的执行**（即边的筛选+流转计算）。

具体来说，当一条边被触发时，会执行：
1. `_process_edge_pipeline(ctx)` —— 边处理流水线
2. 包含：筛选计算 + 后处理 hooks（tracker、TTL、事件等）

### 4.3 谁来触发？

触发由**两个条件的与（AND）**共同决定：

```
triggered = edge_fired AND (node_dirty[sid] OR data_dirty)
```

> 位置：`engine.py:3621-3629`

#### 条件一：edge_fired（时机门）

由**时间**驱动，判断当前时刻是否在允许触发的时间窗口内。

- 计算函数：`_should_trigger_edge`（`engine.py:1757-1785`）
- 包含三层判断：
  1. **回放模式特有**：begin/end/interval 守门（`_should_fire_flow_replay`）
  2. **starttype 守门**：开始类型判断（`_tdx_should_execute`）
  3. **cxtype 守门**：持续时间/过期判断（`_tdx_check_duration`）

时机原语（`_eval_timing_primitive`，`engine.py:829-985`）：
- `always` —— 总是允许
- `elapsed` —— 经过指定时长
- `timestamp_ge` —— 到达指定时间戳
- `in_range` —— 在时间区间内
- `hhmmss` —— 到达指定时分秒
- `once` —— 只执行一次
- `count_gte` —— 执行次数达到阈值

#### 条件二：node_dirty 或 data_dirty（变化检测）

由**数据变化**驱动，判断源节点股票或行情数据是否有变化。

- **node_dirty（节点脏）**：源节点的股票列表发生了变化
  - 标记函数：`_mark_dirty`（`engine.py:2062-2071`）
  - 检查函数：`_is_dirty`（`engine.py:2073-2082`）
  - 何时被标记：边执行后目标节点股票变化（`engine.py:3638-3641`）

- **data_dirty（行情脏）**：行情数据（bar_data）发生了变化
  - 标记函数：`_refresh_latest_tick`（`engine.py:2095-2115`）
  - 检查函数：`_is_data_dirty`（`engine.py:2125-2127`）
  - 何时被标记：新 bar 数据到达且 hash 变化（`engine.py:2108-2114`）

### 4.4 触发条件的种类

#### 按时机维度（starttype / cxtype）

| 触发时机类型 | 说明 | 对应原语 |
|-------------|------|----------|
| 总是触发 | 无时间限制 | always |
| 开盘后 N 分钟 | 从开盘时间偏移 | timestamp_ge |
| 指定时分秒 | 到达 HH:MM:SS | hhmmss |
| 时间区间内 | 在 [start, end] 区间内 | in_range |
| 开盘后经过 N 秒 | 从开盘计时 | elapsed |
| 只执行一次 | 首次触发后不再触发 | once |
| 执行 N 次后停止 | 次数限制 | count_gte |

#### 按变化维度

| 变化类型 | 说明 |
|----------|------|
| 源节点股票变化 | 节点股票列表的代码集合变化（code set 比较） |
| 行情数据变化 | bar_data 的 hash 变化（价格/成交量等） |

#### 按触发模式（整体）

- **gate_and_change**：时机门 通过 且 （节点变化 或 数据变化）—— 条件边的触发规则
- **source_changed**：源节点变化即触发——无条件边的触发规则

> 位置：`edge_semantics.json:edge_types`

### 4.5 触发和计算的关系

```
触发（Trigger） → 决定是否计算
计算（Computation） → 实际执行筛选和流转
```

- 触发是计算的**前置条件**
- 触发不消耗多少计算资源（只是布尔判断）
- 计算是触发为 True 后的**实际工作**
- 没有触发，就不会有计算
- 有触发，才有计算

### 4.6 触发逻辑写在哪里？

| 层级 | 函数/方法 | 位置 | 职责 |
|------|-----------|------|------|
| 主入口 | `_run_tick_event_driven` | `engine.py:3621-3629` | 触发判定 + 执行 |
| 时机门主入口 | `_should_trigger_edge` | `engine.py:1757-1785` | 统一时机门评估 |
| starttype 守门 | `_tdx_should_execute` | `engine.py:781-794` | 开始类型判断 |
| cxtype 守门 | `_tdx_check_duration` | `engine.py:1013-1030` | 持续时间判断 |
| 回放时机守门 | `_should_fire_flow_replay` | `engine.py:1033-1093` | begin/end/interval |
| 时机原语求值 | `_eval_timing_primitive` | `engine.py:829-985` | 7 个时机原语 |
| 脏标记管理 | `_mark_dirty` / `_is_dirty` / `_is_data_dirty` | `engine.py:2062-2127` | 变化检测基础设施 |

---

## 5. 事件（Event）

### 5.1 本质定义

**事件是股票池运行过程中发生的有意义的状态变化的记录，是异步通知的载体。**

事件是"发生了什么"的事实陈述，不包含指令（不是命令）。事件被放入队列，由消费者异步处理。

### 5.2 事件类型

#### 核心业务事件（event_rules.json）

| 事件类型 | 触发时机 | 说明 |
|----------|----------|------|
| `ENTER` | 股票进入目标池时 | 股票首次进入（tracker 为空或状态非 holding） |
| `EXIT` | 股票离开源池时 | move 模式下持仓股票离开源池 |
| `TIMEOUT` | 股票 TTL 超时时 | 持仓股票 TTL 到期被淘汰 |

> 位置：`event_rules.json:events`

#### 系统生命周期事件

| 事件类型 | 触发时机 | 代码位置 |
|----------|----------|----------|
| `pool_start` | 股票池开始运行时 | `engine.py:3827-3833` |
| `pool_end` | 股票池结束运行时 | `engine.py:3867-3873` |
| `flow_fired` | 边触发执行时 | `engine.py:3855-3856` |

#### 其他事件

| 事件类型 | 说明 | 代码位置 |
|----------|------|----------|
| 高亮事件（highlight） | UI 高亮通知 | `engine.py:3016-3020` |
| 告警事件（alert） | 告警通知 | `engine.py:411` |
| 信号事件（signal） | 交易信号（BUY/SELL） | 见下文 |

### 5.3 谁产生事件？谁消费事件？

#### 生产者

1. **`_emit_transfer_events`** —— 转移事件发射器（ENTER/EXIT）
   - 位置：`engine.py:3492`（定义）
   - 在每个 tick 结束时调用，对比前后状态差异生成事件

2. **`_emit_domain_event`** —— 通用领域事件发射器
   - 位置：`engine.py:3197-3252`
   - 根据 `event_domain_templates` 配置表发射事件+信号

3. **`_push_event`** —— 底层事件入队
   - 位置：`engine.py:3078-3080`
   - 直接将事件放入 `_event_queue`

#### 消费者

事件通过 **asyncio.Queue** 传递，消费者从队列中取事件处理：

- `_event_queue` —— 通用事件队列（`engine.py:383`）
- `get_event_queue()` —— 获取事件队列的公共接口（`engine.py:3771`）
- 前端/其他模块通过监听事件队列获取通知

### 5.4 事件和触发的区别

| 维度 | 触发（Trigger） | 事件（Event） |
|------|----------------|---------------|
| 本质 | 判定过程（是否执行） | 事实记录（发生了什么） |
| 时序 | 计算之前 | 计算之后 |
| 作用对象 | 边（edge） | 股票/池（业务实体） |
| 是否持久化 | 不持久化，瞬时判定 | 入队列，可被消费/记录 |
| 方向性 | 向内（控制计算是否发生） | 向外（通知外界发生了什么） |
| 代码位置 | `_should_trigger_edge` 等 | `_emit_*` 系列方法 |

**简单说：触发是"要不要算"，事件是"算完后发生了什么"。**

### 5.5 事件和信号（Signal）的区别

| 维度 | 事件（Event） | 信号（Signal） |
|------|--------------|----------------|
| 本质 | 状态变化的事实记录 | 可执行的交易指令建议 |
| 类型 | ENTER / EXIT / TIMEOUT / pool_start / pool_end / flow_fired | BUY / SELL |
| 生成条件 | 任何池的进出都生成事件 | 只有目标池（baimpool=1）的进出才生成信号 |
| 队列 | `_event_queue` | `_signal_queue` |
| 列表 | `self.events` | `_signal_events` |
| 关系 | 事件是信号的前提——信号由事件派生 | 信号是事件的子集+升级——满足角色条件的事件才生成信号 |

> 关键证据：`signal_rules.json:signals.BUY.trigger.condition = "target_pool_role.is_target == true"`
> 只有目标池的 ENTER 事件才会升级为 BUY 信号。

代码中的信号生成路径：
1. `_emit_domain_event` 中遍历信号规则（`engine.py:3244-3252`）
2. 调用 `_should_emit_signal_for_domain` 判断是否满足信号条件（`engine.py:3254-3269`）
3. 满足则调用 `_push_signal` 入队（`engine.py:3081-3085`）

### 5.6 事件怎么表示、怎么传递？

#### 事件的数据结构

```python
# 通用事件结构（_push_event 入队的格式）
event = {
    "type": "ENTER",           # 事件类型
    "code": "000001",          # 股票代码
    "pool_id": "target_pool",  # 关联的池 ID
    "time": 1719897600.0,      # 事件时间戳
    "detail": {                # 事件详情（各类型不同）
        "source_id": "node_1",
        "mode": "copy",
        "flow_id": "flow_1",
        ...
    }
}
```

> 位置：`engine.py:3078-3080`

#### 传递机制

- **传递方式**：`asyncio.Queue`（异步队列）
- **入队**：`_push_event` → `self._event_queue.put_nowait(event)`
- **出队**：消费者通过 `get_event_queue()` 获取队列，自行消费
- **存储**：`self.events` 列表也会记录部分事件（pool_start/pool_end/flow_fired）

---

## 6. 概念关系图（文字版）

```
┌─────────────────────────────────────────────────────────────────────┐
│                        股票池运行时                                  │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────┐    from     ┌────────────┐     to    ┌──────────┐   │
│  │  源节点   │────────────▶│     边     │──────────▶│ 目标节点  │   │
│  │ (Source) │◀────────────│   (Edge)   │◀──────────│ (Target) │   │
│  └────┬─────┘   股票流出   └─────┬──────┘  股票流入  └────┬─────┘   │
│       │                         │                         │         │
│       │ 持有股票                 │ 筛选+流转               │ 持有股票  │
│       │ node_stocks[sid]         │ _filter_*              │ node_stocks[tid] │
│       │                         │                         │         │
│       ├─────────────────────────┼─────────────────────────┤         │
│       │                         ▼                         │         │
│       │                  ┌───────────┐                    │         │
│       │                  │  计算     │                    │         │
│       │                  │ (Computation)                  │         │
│       │                  └─────┬─────┘                    │         │
│       │                        │                          │         │
│       │                        ▲                          │         │
│       │                        │                          │         │
│       ├─────────────────────────┼─────────────────────────┤         │
│       │                        │                          │         │
│       │                  ┌───────────┐                    │         │
│       │                  │  触发     │                    │         │
│       │                  │ (Trigger) │                    │         │
│       │                  └─────┬─────┘                    │         │
│       │                        │                          │         │
│       │            ┌───────────┴───────────┐              │         │
│       │            ▼                       ▼              │         │
│       │     edge_fired           node_dirty / data_dirty   │         │
│       │     (时机门)              (变化检测)               │         │
│       └────────────────────────────────────────────────────┘         │
│                                                                     │
│                              │                                      │
│                              ▼                                      │
│                        ┌───────────┐                                │
│                        │   事件    │                                │
│                        │  (Event)  │                                │
│                        └─────┬─────┘                                │
│                              │                                      │
│                    ┌─────────┴─────────┐                            │
│                    ▼                   ▼                            │
│              ENTER/EXIT/          BUY/SELL                          │
│            TIMEOUT 等             信号                             │
│            (事件队列)           (信号队列)                          │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 关系总结

1. **节点 ↔ 边**：节点是顶点，边是有向连接；边连接两个节点，边类型由源节点类型决定。
2. **边 ↔ 计算**：计算发生在边上，边是计算的主体；节点不计算，只持有数据。
3. **触发 → 计算**：触发是计算的开关，触发为真时才执行计算；触发是前置条件。
4. **计算 → 事件**：计算导致状态变化，状态变化产生事件；事件是计算的副作用。
5. **事件 → 信号**：信号是事件的子集，满足特定条件（如目标池）的事件才升级为信号。
6. **节点 ↔ 触发**：节点 dirty 是触发条件之一（变化检测维度）。

---

## 7. 股票池本质一句话概括（v3.4 版）

**股票池是一个由节点（持有股票的容器）和边（筛选+流转规则）组成的有向图，在时间和数据变化的驱动下，通过触发机制控制边的计算执行，使股票在节点间按规则流动，并以事件和信号的形式对外输出状态变化通知的系统。**

---

**版本**：v3.4
**日期**：2026-07-01
**性质**：纯理解笔记，所有定义均从代码中提取
