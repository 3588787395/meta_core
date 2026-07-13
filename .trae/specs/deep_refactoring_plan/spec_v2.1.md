# 股票池架构设计 v2.1

> 版本主题：平衡版——本质清晰 + 细节够用，既不过度设计也不过度简化
> 设计原则：最少的概念，最多的清晰度；表越少越好，但不能少到丢了业务状态
> 上一版 v2.0 问题：过度简化（58分），把边触发状态、持仓跟踪等核心业务状态都丢了
> 目标：找到 27 张表和 6 张表之间的平衡点

---

## 一、股票池的本质

**股票池 = 节点装股票，边连节点，触发条件到了且有新数据，就按过滤条件筛选，股票从源节点流到目标节点，同时跟踪持仓并发出预警。**

---

## 二、核心概念（9 个）

| # | 概念 | 一句话说明 | 代码对应 |
|---|------|-----------|---------|
| 1 | **节点 (Node)** | 装股票的篮子，有类型（备选池/状态池/条件节点） | `pool_config.nodes` |
| 2 | **边 (Edge)** | 连接两个节点的管道，有触发条件和过滤条件 | `pool_config.edges` |
| 3 | **触发条件 (Timing)** | 边什么时候能执行（间隔/开始/结束/次数） | `edge.interval/begin/end/count` |
| 4 | **过滤条件 (Filter)** | 边的筛子，哪些股票能流过去 | `edge.condition + formula` |
| 5 | **执行顺序 (ExecOrder)** | 边的处理顺序，可以不同于拓扑顺序 | `compiled.processing_plan` |
| 6 | **持仓跟踪 (Tracker)** | 股票入池后的跟踪信息（入场价/时间/收益等） | `_trackers` / `stock._tracker` |
| 7 | **行情数据 (MarketData)** | 每只股票的最新 tick/K 线，唯一真相源 | `_latest_tick` / `current_bar_data` |
| 8 | **事件 (Event)** | 入池/出池/预警等通知 | `_event_queue` / `_alert_queue` |
| 9 | **池子 (Pool)** | 完整的股票池定义 = 节点 + 边 + 执行顺序 | `pool_config` |

**为什么是 9 个？** 因为这 9 个概念缺一不可——少了任何一个，业务状态就不完整。多一个都是人为制造的复杂度。

---

## 三、配置表清单（4 张）

### 3.1 总览

| # | 表名 | 一句话说明 | 现有对应 |
|---|------|-----------|---------|
| 1 | **pool_config** | 池子定义：节点、边、执行顺序 | `pool_config = {nodes, edges}` |
| 2 | **formula_lib** | 公式库：所有可用的选股公式/指标 | `builtin_formulas.json` + `custom_formulas.json` |
| 3 | **tracker_schema** | 持仓跟踪字段定义与计算公式 | `tracker_schema.json` |
| 4 | **system_config** | 系统参数：tick 间隔、交易时间、缓存策略等 | `timing.json` + `defaults.json` + `data_config.json` |

**为什么是 4 张？**
- v2.0 说 3 张就够——把 tracker_schema 合并进了 system_config，但 tracker 是独立的业务概念，有自己的字段定义和计算公式，单独一张更清晰。
- 再多就过度设计了——现在 90+ 张配置表，大部分是"表驱动"走火入魔的产物。

### 3.2 各表详细说明

#### 表 1：pool_config（池子定义）

**干什么的：** 一个股票池的完整静态定义，运行前确定，运行时不变。

**关键字段：**

```json
{
  "pool_id": "pool_001",
  "name": "黑马一号池",
  "nodes": [
    {
      "id": "n1",
      "type": "candidate",      // candidate / condition / state_pool / discard
      "name": "备选池",
      "params": { ... }        // 各类型节点的参数
    }
  ],
  "edges": [
    {
      "id": "e1",
      "from": "n1",             // 源节点 ID
      "to": "n2",               // 目标节点 ID
      "type": "conditional",    // conditional / unconditional
      "timing": {               // 触发条件（仅 conditional 边有）
        "interval_sec": 60,     // 执行间隔（秒）
        "begin": {...},         // 开始条件
        "end": {...},           // 结束条件
        "count": -1             // 执行次数（-1 为无限）
      },
      "filter": {               // 过滤条件
        "formula_id": "f001",   // 引用 formula_lib
        "params": { ... }       // 公式参数
      },
      "transfer_mode": "move"   // move / copy
    }
  ],
  "exec_order": ["e1", "e2", "e3"]  // 执行顺序（边 ID 列表，可由用户调整）
}
```

**设计说明：**
- 节点和边放在同一张表里，因为它们是一个整体——池子的拓扑结构。
- `exec_order` 独立出来，因为用户可以调整执行顺序，不必和拓扑顺序一致。
- 约束：执行顺序不能违背拓扑依赖（A→B，A 必须在 B 前面）。

#### 表 2：formula_lib（公式库）

**干什么的：** 所有可用的选股公式、技术指标、条件表达式的定义。

**关键字段：**

```json
{
  "formulas": [
    {
      "id": "f001",
      "name": "MACD金叉",
      "type": "indicator",      // indicator / condition / trade_sys / basic
      "script": "DIF:EMA(CLOSE,12)-EMA(CLOSE,26);...",
      "params": { ... },
      "cycle": "day"            // 应用周期
    }
  ]
}
```

**设计说明：**
- 公式是独立于具体池子的资源，多个池子可以引用同一个公式。
- 单独一张表，因为公式库是可复用的资产，不是某个池子的私有配置。

#### 表 3：tracker_schema（持仓跟踪定义）

**干什么的：** 定义持仓跟踪器有哪些字段、初始值是什么、每个字段怎么计算。

**关键字段：**

```json
{
  "fields": {
    "entry_price": {"type": "float", "default": 0, "desc": "入场价格"},
    "entry_time":  {"type": "int",   "default": 0, "desc": "入场时间戳"},
    "current_price": {"type": "float", "default": 0},
    "profit_pct": {"type": "float", "default": 0},
    "max_profit": {"type": "float", "default": 0},
    "hold_days": {"type": "float", "default": 0}
  },
  "init_profile": {
    "entry_price": {"source": "current_price"},
    "entry_time": {"source": "now_ts"}
  },
  "update_formulas": {
    "profit_pct": "(current_price - entry_price) / entry_price * 100",
    "max_profit": "max(max_profit, profit_pct)"
  }
}
```

**为什么单独一张表？**
- v2.0 把它合并进 system_config，但 tracker 是核心业务状态——股票入池后发生了什么、赚了多少、持仓多久——这些都是股票池的核心价值。
- 单独一张表，职责更清晰。

#### 表 4：system_config（系统参数）

**干什么的：** 全局系统参数，不随池子变化而变化。

**关键字段：**

```json
{
  "tick_interval": 1,              // tick 间隔（秒）
  "trading_calendar": {            // 交易时间
    "sessions": [
      {"open_sec": 9*3600+30*60, "close_sec": 11*3600+30*60},
      {"open_sec": 13*3600, "close_sec": 15*3600}
    ]
  },
  "cache": {
    "filter_cache_maxsize": 500,
    "cache_ttl_default": 300
  },
  "data_provider": { ... },
  "alert_config": { ... }
}
```

---

## 四、运行时表清单（7 张）

### 4.1 总览

| # | 表名 | 一句话说明 | 现有对应 | 类型 |
|---|------|-----------|---------|------|
| 1 | **node_stocks** | 每个节点当前有哪些股票 | `node_stocks` | 核心业务状态 |
| 2 | **market_data** | 每只股票的最新行情数据（唯一真相源） | `_latest_tick` | 核心业务状态 |
| 3 | **edge_timing_state** | 每条边的时序触发状态 | `_flow_last_fire_ts` + `_flow_first_fire_ts` + `_flow_exec_counts` + `_flow_duration_starts` | 核心业务状态 |
| 4 | **trackers** | 持仓跟踪：每只股票的入池跟踪信息 | `_trackers` | 核心业务状态 |
| 5 | **pool_state** | 池子运行状态 | `_pool_start_time` + `_first_run` + `_current_bar_time` | 核心业务状态 |
| 6 | **events** | 事件队列（入池/出池/预警/信号） | `_event_queue` + `_alert_queue` + `_signal_queue` | 核心业务状态 |
| 7 | **alert_cooldown** | 告警冷却状态 | `_alert_cooldown` | 核心业务状态 |

**为什么是 7 张？**
- v2.0 说 3 张就够——把边触发状态、tracker、告警冷却都丢了，这些都是真实的业务状态，不是缓存。
- v1.x 有 30+ 张——大量是中间缓存（`_filter_cache`、`_last_snapshot`、`_last_bar_hash`、`_dirty_nodes`、`_node_snapshots`...），这些不是业务状态，是实现细节。
- 7 张是平衡点：每一张都是核心业务状态，少了就说不清楚运行时发生了什么。

### 4.2 各表详细说明

#### 表 1：node_stocks（节点股票）

**干什么的：** 每个节点当前有哪些股票，这是股票池最核心的运行时状态。

**结构：** `{node_id: [stock_dict, ...]}`

**关键字段（每只股票）：**
- `code`: 股票代码
- `name`: 股票名称
- `_tracker`: 持仓跟踪引用（指向 trackers 表）

**生命周期：** per_session（池子运行期间持续存在，跨 tick 更新）

#### 表 2：market_data（行情数据）

**干什么的：** 每只股票的最新行情数据，是所有计算的唯一真相源。

**结构：** `{code: {tick_data, bar_data}}`

**关键字段：**
- `timestamp`: 最新数据时间戳
- `close/open/high/low/volume`: 最新价量
- 各周期 K 线数据

**生命周期：** per_tick（每个 tick 刷新一次）

**设计说明：**
- 唯一真相源原则：所有过滤计算、tracker 更新、预警判断都从这里取数。
- 数据更新只做一件事：写 market_data，标记时间戳。

#### 表 3：edge_timing_state（边时序状态）

**干什么的：** 每条边的触发状态记录——上次什么时候触发的、第一次什么时候触发的、已经触发了多少次、持续窗口从什么时候开始的。

**结构：** `{edge_id: {last_fire_ts, first_fire_ts, exec_count, duration_start}}`

**关键字段：**
- `last_fire_ts`: 上次触发时间戳（用于 interval 守门）
- `first_fire_ts`: 首次触发时间戳（用于 end 条件守门）
- `exec_count`: 已执行次数（用于 count 限制）
- `duration_start`: 持续窗口起始锚点（用于持续时间型条件）

**生命周期：** per_session

**设计说明：**
- 这是真实的业务状态，不是缓存——它记录了"这条边运行到什么程度了"。
- v2.0 把它丢了，结果就是"触发条件"这件事说不清楚。
- 4 个相关字段合并成一张表，比分散成 4 张表更清晰。

#### 表 4：trackers（持仓跟踪）

**干什么的：** 跟踪每只股票入池后的表现——什么时候进的、什么价进的、现在赚了多少、最大回撤多少。

**结构：** `{code: tracker_dict}`

**关键字段：**（由 tracker_schema 定义）
- `entry_price`: 入场价格
- `entry_time`: 入场时间戳
- `current_price`: 当前价格
- `profit_pct`: 当前收益率
- `max_profit`: 最大收益率
- `max_drawdown`: 最大回撤
- `hold_days`: 持仓天数
- `pool_id`: 所属池
- `flow_id`: 入池边

**生命周期：** per_session（股票在池中期间持续更新，出池后可保留用于历史查询）

**设计说明：**
- 这是股票池的核心价值之一——不只是筛选股票，还要跟踪持仓表现。
- v2.0 把它丢了，等于丢了一半的业务。

#### 表 5：pool_state（池子运行状态）

**干什么的：** 池子整体的运行状态——什么时候开始的、是不是第一次跑、当前数据时间是什么。

**结构：** 单例（一个池子一个状态对象）

**关键字段：**
- `start_time`: 池子启动时间
- `first_run`: 是否首次运行（首次强制全量计算）
- `current_data_time`: 当前行情数据时间（用于判断数据是否更新）
- `last_data_time`: 上次行情数据时间（对比用）

**生命周期：** per_session

#### 表 6：events（事件队列）

**干什么的：** 所有事件的统一出口——入池、出池、预警、交易信号，都走这里。

**结构：** 队列（FIFO）

**事件类型：**
- `transfer`: 股票流转（入池/出池）
- `alert`: 预警事件
- `signal`: 交易信号
- `pool_start`: 池子启动
- `pool_stop`: 池子停止

**生命周期：** per_session（消费后出队）

**设计说明：**
- 统一事件总线：前端刷新、告警通知、交易接口，都从这里消费。
- 替代现在的 `_event_queue` + `_alert_queue` + `_signal_queue` 三套队列。

#### 表 7：alert_cooldown（告警冷却）

**干什么的：** 防止同一告警反复触发——每个 (规则, 股票) 对有冷却时间。

**结构：** `{(rule_id, code): cooldown_end_ts}`

**生命周期：** per_session

**设计说明：**
- 这是真实的业务状态，不是缓存——它记录了"这个告警什么时候才能再触发"。
- 单独一张表，因为它有独立的业务含义。

### 4.3 什么不是运行时表？

以下都是**中间缓存/实现细节**，不是核心业务状态，不应该算作"运行时表"：

| 现有字段 | 说明 | 归类 |
|---------|------|------|
| `_filter_cache` | 过滤结果缓存，避免相同数据重复计算 | 性能优化，可丢 |
| `_last_snapshot` | 节点股票快照，用于变更检测 | 实现细节 |
| `_last_bar_hash` / `_current_bar_hash` | 行情数据哈希，用于变更检测 | 实现细节 |
| `_dirty_nodes` | 脏节点标记 | 实现细节 |
| `_node_snapshots` | 节点快照 | 实现细节 |
| `_data_cache` | 通用数据缓存 | 性能优化 |
| `_exit_tracker_cache` | 出池时临时缓存 tracker | 实现细节 |
| `_pk_rankings` | PK 排名结果 | 衍生数据，可重算 |
| `_angle_results` | 多分析角度结果 | 衍生数据，可重算 |
| `_dashboard_data` | 看盘面板数据 | 衍生数据，可重算 |
| `_loop_node_stocks` | run_loop 模式的 node_stocks 副本 | 冗余，就是 node_stocks |

**原则：** 如果重启后能从其他表重新算出来，就不是核心业务状态。

---

## 五、核心循环（26 行伪代码）

```python
async def run_pool(pool_config):
    pool_state = init_pool_state()
    node_stocks = init_node_stocks(pool_config.nodes)
    compiled = compile_pool(pool_config)    # 一次编译，运行期只读

    while pool_state.running:
        await sleep(system_config.tick_interval)
        if not is_trading_time(): continue

        # 1. 刷新行情数据（唯一入口）
        new_market_data = refresh_market_data()
        data_changed = new_market_data.timestamp != pool_state.last_data_time

        # 2. 检测源节点股票变化（备选池刷新等）
        source_changed = detect_source_changes(node_stocks, pool_state.prev_snapshot)

        # 3. 按执行顺序处理每条边
        for edge_id in compiled.exec_order:
            edge = compiled.edges[edge_id]
            timing_state = edge_timing_state[edge_id]

            # 触发判定：时间条件 AND (源变了 OR 数据变了)
            if not should_trigger(edge, timing_state, pool_state): continue
            if not (source_changed[edge.from_id] or data_changed): continue

            # 执行过滤 + 流转
            source_stocks = node_stocks[edge.from_id]
            filtered = filter_stocks(edge.filter, source_stocks, new_market_data)

            # 更新目标节点 + tracker
            transferred = transfer_stocks(filtered, edge, node_stocks, trackers)

            # 更新边时序状态
            timing_state.last_fire_ts = now()
            timing_state.exec_count += 1
            if timing_state.first_fire_ts is None:
                timing_state.first_fire_ts = timing_state.last_fire_ts

            # 发出事件
            if transferred:
                emit_transfer_events(edge, transferred, events)
                emit_alert_events(edge, filtered, alert_cooldown, new_market_data)

        # 4. tick 末：更新全局状态
        update_trackers(trackers, node_stocks, new_market_data)
        pool_state.last_data_time = new_market_data.timestamp
        pool_state.prev_snapshot = snapshot(node_stocks)
        pool_state.first_run = False

        # 5. 推送事件给前端/消费者
        flush_events(events)
```

### 人话解释（5 步）

1. **等一个 tick**，看看是不是交易时间，不是就继续睡
2. **拉最新行情**，对比时间戳——数据没变就跳过重算
3. **按执行顺序检查每条边**：
   - 触发时间到了吗？（interval/begin/end/count）
   - 源节点股票变了吗？或者行情数据变了吗？
   - 都满足 → 执行过滤，股票流过去，更新 tracker，发事件
4. **tick 末**：更新所有 tracker、保存快照、清理标记
5. **推送事件**：前端刷新、告警通知，都从事件队列取

---

## 六、前端后端对应关系

### 6.1 对应关系表

| 前端概念 | 后端概念 | 说明 |
|---------|---------|------|
| **综合设置表格** | pool_config（节点 + 边） | 表格每一行 = 一个节点或一条边，左列缩进 = 拓扑结构 |
| **拓扑画布** | pool_config（节点 + 边） | 同一份数据的可视化展示，圆圈=节点，连线=边 |
| **执行顺序功能** | pool_config.exec_order | 用户拖拽调整的执行顺序，保存在这里 |
| **公式编辑器** | formula_lib | 编辑公式库中的公式 |
| **状态属性面板** | tracker_schema + trackers | 左边是 tracker 字段定义，右边是当前值 |
| **时序设置面板** | edge.timing + edge_timing_state | 左边是配置（interval/begin/end），右边是运行状态 |
| **预警面板** | events（alert 类型） | 从事件队列消费告警事件展示 |

### 6.2 一句话总结

**综合设置 = 拓扑画布 = pool_config，它们是同一份数据的三种表现形式。**

- 综合设置：表格视图，方便编辑参数和调整顺序
- 拓扑画布：图形视图，方便看整体结构
- pool_config：数据结构，程序内部用

### 6.3 执行顺序 vs 拓扑顺序

| 维度 | 拓扑顺序 | 执行顺序 |
|------|---------|---------|
| **由什么决定** | 节点连接关系（A→B，A 在 B 前面） | 用户配置（可调整同层级节点的先后） |
| **运行时能变吗** | 不能（拓扑不变） | 能（用户可随时调整） |
| **约束** | 由连接关系自然决定 | 不能违背拓扑依赖（A 必须在 B 前面） |
| **代码对应** | `compiled.depths` 推导 | `pool_config.exec_order` 用户指定 |

**代码验证：** 当前 `compiled.topo_order` 是按深度排序（`engine.py:1379`），理论上可以换成用户指定的顺序，只要不违背拓扑依赖。

---

## 七、现有代码的问题诊断

### 7.1 核心问题：四个"各搞一套"

| 问题 | 表现 | 严重程度 |
|------|------|---------|
| **数据更新各搞一套** | K线更新、tick更新、备选池刷新——各有各的路径，各有各的缓存 | ⭐⭐⭐⭐⭐ |
| **公式计算各搞一套** | 条件过滤用一套、tracker 用一套、预警用一套——重复计算 | ⭐⭐⭐⭐ |
| **界面刷新各搞一套** | 表格刷新、画布刷新、预警面板刷新——各推各的事件 | ⭐⭐⭐⭐ |
| **配置加载各搞一套** | 引擎加载一套、表驱动引擎加载一套、前端又加载一套 | ⭐⭐⭐ |

### 7.2 配置表过度拆分（90+ → 4）

**问题：** 走了"表驱动"的极端，把一切都拆成表：

- 节点类型 → `cell_type_registry.json`
- 边类型 → `edge_semantics.json`
- 拓扑模式 → `topology.json`
- 值提取器 → `value_extractors.json`
- UI 组件 → `ui_components.json`
- UI 布局 → `ui_layouts.json`
- 字段定义 → `field_definitions.json`
- 数据映射 → `data_mappings.json`
- 事件规则 → `event_rules.json`
- 信号规则 → `signal_rules.json`
- 告警规则 → `alert_rules.json`
- ...还有几十张

**结果：** 简单的逻辑变成了"查表 → 查表 → 再查表"，反而更难理解。新人看代码，根本不知道核心逻辑在哪。

**原则：表驱动不是目的，简单才是。** 如果一张表只有几行，而且逻辑很简单，直接写代码更清楚。

### 7.3 运行时表过多（30+ → 7）

**问题：** 把中间缓存、实现细节、衍生数据都当成"运行时表"，导致核心业务状态被淹没。

`_RUNTIME_TABLE_NAMES` 里有 30+ 个，但真正的核心业务状态只有 7 个，其他都是：
- 性能缓存（`_filter_cache`、`_data_cache`）
- 变更检测中间状态（`_last_snapshot`、`_last_bar_hash`、`_dirty_nodes`）
- 衍生数据（`_pk_rankings`、`_angle_results`、`_dashboard_data`）
- 冗余副本（`_loop_node_stocks` 就是 `node_stocks` 的副本）

### 7.4 engine.py 过于臃肿（5000+ 行）

**问题：** 什么都往里塞——数据更新、公式计算、拓扑编译、事件处理、UI 接口... 核心逻辑被淹没。

**应该拆成：**
- `engine.py`：主循环 + 边执行（核心调度，500 行以内）
- `data_provider.py`：数据接入（统一入口）
- `formula_engine.py`：公式计算（统一引擎）
- `tracker.py`：持仓跟踪（独立模块）
- `event_bus.py`：事件总线（统一出口）

### 7.5 时间维度的处理分散

**问题：** 时间相关的逻辑散落在各处：
- `_flow_last_fire_ts`：边上次触发时间
- `_flow_first_fire_ts`：边首次触发时间
- `_flow_exec_counts`：边执行次数
- `_flow_duration_starts`：持续窗口起始
- `_pool_start_time`：池子启动时间
- `_current_bar_time`：当前 K 线时间
- `_virtual_clock`：虚拟时钟
- `_current_time_source`：当前时间源

**应该：** 合并成 `edge_timing_state` + `pool_state` 两张表，时间相关的逻辑集中管理。

---

## 八、简化重构的步骤和路径

### 8.1 重构原则

1. **能合并的合并**：90 张配置表 → 4 张核心配置表
2. **能内联的内联**：简单逻辑不要拆成"表驱动"
3. **能删的删掉**：没用的概念、没用的中间表、没用的抽象层
4. **一个真相源**：数据只有一个入口，计算只有一个引擎，事件只有一个总线

### 8.2 分四步走

#### 第一步：统一数据层（解决"数据更新各搞一套"）

**目标：** `market_data` 是唯一真相源，所有计算从这里取数。

**动作：**
- [ ] 把 K 线更新、tick 更新、备选池刷新统一成一条数据通路
- [ ] 建立 `market_data` 运行时表，作为唯一的数据真相源
- [ ] 数据更新只做一件事：写 `market_data`，更新时间戳
- [ ] 废弃 `_data_cache`、`_current_bar_data` 等重复缓存

**验收：** 所有过滤计算、tracker 更新、预警判断都从 `market_data` 取数，没有第二条数据通路。

#### 第二步：统一运行时表（解决"运行时表过多"）

**目标：** 30+ 运行时表 → 7 张核心业务状态表。

**动作：**
- [ ] 合并边时序状态：`_flow_last_fire_ts` + `_flow_first_fire_ts` + `_flow_exec_counts` + `_flow_duration_starts` → `edge_timing_state`
- [ ] 合并池子状态：`_pool_start_time` + `_first_run` + `_current_bar_time` → `pool_state`
- [ ] 统一事件队列：`_event_queue` + `_alert_queue` + `_signal_queue` → `events`
- [ ] 明确哪些是中间缓存，从 `_RUNTIME_TABLE_NAMES` 中移除，改为局部变量或私有属性
- [ ] 移除 `_loop_node_stocks` 冗余副本，直接用 `node_stocks`

**验收：** `_RUNTIME_TABLE_NAMES` 中只有 7 个核心业务状态表，其他都是实现细节，不再作为"一等公民"。

#### 第三步：统一计算层（解决"公式计算各搞一套"）

**目标：** 一个公式引擎，所有过滤/指标/tracker/预警都走这里。

**动作：**
- [ ] 条件过滤、tracker 计算、预警判断——统一调用同一个公式引擎
- [ ] 相同股票相同周期的 K 线只算一次，结果复用
- [ ] 过滤逻辑回归本质：输入=股票列表+条件，输出=过滤后的列表
- [ ] tracker 更新公式从 `tracker_schema` 读取，统一计算

**验收：** 公式计算只有一个入口，没有重复计算。

#### 第四步：精简配置层（解决"配置表 90 张"）

**目标：** 90+ 张配置表 → 4 张核心配置表 + 可选的扩展表。

**动作：**
- [ ] 把分散的节点/边/执行顺序合并进 `pool_config`
- [ ] 把分散的公式定义合并进 `formula_lib`
- [ ] 把 tracker 字段定义独立为 `tracker_schema`
- [ ] 把系统参数合并进 `system_config`
- [ ] 评估剩下的 80+ 张表：
  - 只有几行的简单逻辑 → 直接内联到代码里
  - 纯 UI 相关的 → 移到前端，不放在后端配置里
  - 真正需要配置的 → 合并进上述 4 张表

**验收：** 后端核心配置表只有 4 张，新人能在 10 分钟内理解配置结构。

### 8.3 最终会变成什么样？

```
核心文件（5-6 个）：
├── engine.py           # 主循环 + 边执行调度（500 行以内）
├── data_provider.py    # 数据接入（统一入口）
├── formula_engine.py   # 公式计算（统一引擎）
├── tracker.py          # 持仓跟踪（独立模块）
├── event_bus.py        # 事件总线（统一出口）
└── pool_config.py      # 配置加载 + 校验

核心表（11 张）：
┌─ 配置表（4 张）─┐
│ pool_config     │  节点 + 边 + 执行顺序
│ formula_lib     │  公式库
│ tracker_schema  │  持仓跟踪定义
│ system_config   │  系统参数
└─────────────────┘
┌─ 运行时表（7 张）─┐
│ node_stocks       │  节点股票
│ market_data       │  行情数据（唯一真相源）
│ edge_timing_state │  边时序状态
│ trackers          │  持仓跟踪
│ pool_state        │  池子运行状态
│ events            │  事件队列
│ alert_cooldown    │  告警冷却
└───────────────────┘
```

---

## 九、时间和触发的处理

### 9.1 为什么时间是核心维度？

股票池的本质是"**在正确的时间做正确的筛选**"——时间不对，一切都白搭。

### 9.2 时间的三个层次

| 层次 | 概念 | 说明 | 对应表 |
|------|------|------|--------|
| **全局时间** | 当前行情时间 | 数据最新到什么时候了 | `pool_state.current_data_time` |
| **池子时间** | 池子启动时间 | 池子从什么时候开始跑的 | `pool_state.start_time` |
| **边时间** | 边的触发状态 | 这条边上次/首次什么时候触发的、触发了几次 | `edge_timing_state` |

### 9.3 触发条件的完整判定逻辑

```
一条边要不要执行，要过四道关：

1. 开始关（begin）：
   - 立即执行？开盘后？开市前？指定时间？延迟 N 秒？
   - 没过 → 这条边还没开始，跳过

2. 结束关（end）：
   - 到结束时间了吗？执行次数用完了吗？
   - 过了 → 这条边已经结束了，跳过

3. 间隔关（interval）：
   - 距离上次触发够 interval 秒了吗？
   - 不够 → 还没到时间，跳过

4. 变化关（change）：
   - 源节点股票变了吗？或者行情数据变了吗？
   - 都没变 → 算了也白算，跳过

四道关都过了 → 执行过滤计算
```

### 9.4 数据不变 = 不用算

**核心洞察：** 最新 tick 时间不变 → 所有 K 线数据不变 → 所有计算结果不变 → 不需重新计算。

这是性能优化的基石——大部分 tick 数据是不变的（比如 1 秒一次 tick，但 1 分钟 K 线 1 分钟才变一次），如果每次都重算，浪费 90%+ 的算力。

**实现方式：**
- `pool_state.current_data_time` 记录当前数据时间
- `pool_state.last_data_time` 记录上次数据时间
- 每个 tick 开始时对比：时间没变 → 整条边的过滤都可以跳过（除非源节点股票变了）

### 9.5 时间源的统一

现在有三种时间源：
- 实时模式：系统时间
- 回放模式：K 线时间推进
- 仿真模式：虚拟时钟推进

**统一到 `pool_state.current_data_time`：** 不管什么模式，"当前时间"就是这个值，所有触发判定都用它。不同模式只是推进这个值的方式不同。

---

## 十、一句话总结

> **股票池就是几个篮子用管子连起来，管子上有筛子，定时看看有没有新数据，有就筛一下，股票从上游流到下游，同时记一下每只股票什么时候进来的、赚了多少，筛出来了就通知一下。**

理解了这句话，再加上 4 张配置表、7 张运行时表、26 行核心循环，就完全理解了股票池的全部。
