# 股票池架构设计 v2.2

> 版本主题：补全版——告警+信号+TTL，冲击可重构及格线
> 设计原则：从"骨架搭对"到"肉长全"，冲击 80 分的"可指导重构"及格线
> 上一版 v2.1 问题：骨架基本正确（约 65 分），但告警规则、信号规则、节点 TTL、编译期职责等核心业务细节缺失
> 目标：配置表 6 张、运行时表 10 张、核心概念 8 个、核心循环约 35 行，达到可指导实际重构的程度

---

## 一、股票池的本质

**股票池 = 节点装股票，边连节点，触发条件到了且有新数据，就按过滤条件筛选，股票从源节点流到目标节点；同时跟踪持仓、做 TTL 淘汰、产生交易信号、发出告警。**

---

## 二、核心概念（8 个）

| # | 概念 | 一句话定义 | 代码对应 | 类型 |
|---|------|-----------|---------|------|
| 1 | **节点 (Node)** | 装股票的篮子，有类型（备选池/状态池/条件节点），有 TTL 配置 | `pool_config.nodes` | 实体 |
| 2 | **边 (Edge)** | 连接两个节点的管道，有触发条件、过滤条件、执行顺序等属性 | `pool_config.edges` | 实体 |
| 3 | **股票 (Stock)** | 在节点间流动的标的，有代码、行情数据、持仓跟踪 | `node_stocks[*].code` | 实体 |
| 4 | **公式 (Formula)** | 可复用的条件表达式/指标计算，被过滤条件、告警规则、信号规则引用 | `formula_lib` | 实体 |
| 5 | **触发 (Trigger)** | 边执行的时机判定（时间间隔 + 数据变化 + 源变化） | `edge.timing + edge_timing_state` | 行为 |
| 6 | **告警 (Alert)** | 满足特定条件时发出的通知，有规则定义、冷却机制 | `alert_rules + alert_state` | 行为 |
| 7 | **信号 (Signal)** | 满足特定条件时产生的交易指令（BUY/SELL），有去重机制 | `signal_rules + signal_state` | 行为 |
| 8 | **跟踪 (Tracker)** | 股票入池后的持仓表现记录（入场价/时间/收益/TTL 等） | `tracker_schema + trackers` | 实体 |

**为什么是 8 个？**

- v2.1 是 9 个，把"执行顺序"降为边的属性（不是一级概念）
- v2.1 把"行情数据"算一个概念，降为股票的属性（行情是股票的属性，不是独立实体）
- v2.1 把"事件"算一个概念，降为告警/信号的载体（事件是传输机制，不是业务概念）
- v2.1 把"池子"算一个概念，降为节点+边的组合（池子是容器，不是核心业务概念）
- 新增"告警"和"信号"作为核心业务概念——这是股票池的核心输出价值

**分类原则：实体 vs 行为**
- 实体（5个）：节点、边、股票、公式、跟踪 → 有状态、有生命周期
- 行为（3个）：触发、告警、信号 → 是动作、有判定逻辑

---

## 三、配置表清单（6 张）

### 3.1 总览

| # | 表名 | 一句话说明 | 现有对应 |
|---|------|-----------|---------|
| 1 | **pool_config** | 池子定义：节点 + 边 + 执行顺序 | `pool_config = {nodes, edges}` |
| 2 | **formula_lib** | 公式库：所有可用的选股公式/指标 | `builtin_formulas.json + custom_formulas.json` |
| 3 | **alert_rules** | 告警规则：什么条件下发什么告警 | `alert_rules.json` |
| 4 | **signal_rules** | 交易信号规则：什么条件下产生什么信号 | `signal_rules.json` |
| 5 | **tracker_schema** | 持仓跟踪字段定义与计算公式 | `tracker_schema.json` |
| 6 | **system_config** | 系统参数：tick 间隔、交易时间、缓存策略等 | `timing.json + defaults.json + data_config.json` |

**为什么是 6 张？**
- v2.1 说 4 张，缺了 alert_rules 和 signal_rules
- 告警规则和信号规则是独立的业务概念，各自有自己的配置结构、生命周期、管理界面
- 它们都引用 formula_lib，但本身不是公式——公式是"条件表达式"，告警/信号是"业务规则"
- 6 张是合理的：2 张池子相关 + 1 张公式 + 2 张业务规则 + 1 张系统参数

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
      "type": "candidate",
      "name": "备选池",
      "params": { ... },
      "ttl": {
        "enabled": false,
        "ttl_sec": 0
      }
    }
  ],
  "edges": [
    {
      "id": "e1",
      "from": "n1",
      "to": "n2",
      "type": "conditional",
      "timing": {
        "interval_sec": 60,
        "begin": { ... },
        "end": { ... },
        "count": -1
      },
      "filter": {
        "formula_id": "f001",
        "params": { ... }
      },
      "transfer_mode": "move",
      "exec_order": 1
    }
  ]
}
```

**设计说明：**
- 节点和边放在同一张表里，因为它们是一个整体——池子的拓扑结构
- `exec_order` 是边的属性（不是独立概念），用户可以调整执行顺序
- 约束：执行顺序不能违背拓扑依赖（A→B，A 必须在 B 前面）
- 节点有自己的 TTL 配置（股票在节点里能待多久）

#### 表 2：formula_lib（公式库）

**干什么的：** 所有可用的选股公式、技术指标、条件表达式的定义。

**关键字段：**

```json
{
  "formulas": [
    {
      "id": "f001",
      "name": "MACD金叉",
      "type": "indicator",
      "script": "DIF:EMA(CLOSE,12)-EMA(CLOSE,26);...",
      "params": { ... },
      "cycle": "day"
    }
  ]
}
```

**设计说明：**
- 公式是独立于具体池子的资源，多个池子、多条告警/信号规则可以引用同一个公式
- 公式 = "表达式"，告警/信号 = "业务规则"，两者是引用关系

#### 表 3：alert_rules（告警规则）

**干什么的：** 定义什么条件下触发什么告警，包括告警级别、冷却时间。

**关键字段：**

```json
{
  "rules": {
    "profit_threshold": {
      "rule_id": "profit_threshold",
      "name": "止盈告警",
      "scope": "tracker",
      "condition": "profit_pct >= 10",
      "formula_id": null,
      "severity": "warning",
      "cooldown_sec": 300,
      "message_template": "{code} 达到止盈线，当前收益 {profit_pct}%"
    },
    "drawdown_threshold": {
      "rule_id": "drawdown_threshold",
      "name": "回撤告警",
      "scope": "tracker",
      "condition": "max_drawdown <= -5",
      "severity": "critical",
      "cooldown_sec": 300
    }
  }
}
```

**设计说明：**
- `scope`: 作用域——tracker（持仓跟踪）、node（节点）、pool（池子）
- `condition`: 条件表达式，可以直接写，也可以引用 formula_id
- `severity`: 严重级别——info / warning / critical
- `cooldown_sec`: 冷却时间，防止同一 (规则, 股票) 对反复触发
- 告警规则是全局的，不绑定具体池子（但 scope 可以限定范围）

#### 表 4：signal_rules（交易信号规则）

**干什么的：** 定义什么条件下产生什么交易信号（BUY/SELL），包括触发时机、去重机制。

**关键字段：**

```json
{
  "signals": {
    "BUY": {
      "signal_type": "BUY",
      "trigger": {
        "type": "pool_enter",
        "condition": "target_node.type == 'state_pool' AND tracker == null",
        "formula_id": null
      },
      "dedup_key": "{code}_{signal_type}_{entry_date}",
      "priority": 10,
      "stop_on_match": true,
      "field_mapping": {
        "price": "tracker.current_price",
        "profit_pct": "tracker.profit_pct"
      }
    },
    "SELL": {
      "signal_type": "SELL",
      "trigger": {
        "type": "pool_exit",
        "condition": "source_node.type == 'state_pool' AND tracker.status == 'holding'",
        "conditions": [
          { "type": "move_exit", "condition": "..." },
          { "type": "timeout", "condition": "tracker.ttl_remaining <= 0" }
        ],
        "match_policy": "any"
      },
      "dedup_key": "{code}_{signal_type}_{exit_date}"
    }
  }
}
```

**设计说明：**
- `trigger.type`: 触发时机——pool_enter（入池时）、pool_exit（出池时）、periodic（周期检查）
- `condition`: 附加条件（除了触发时机之外的过滤条件）
- `dedup_key`: 去重键，防止同一信号重复产生
- 信号规则是全局的，不绑定具体池子
- 信号 vs 告警的区别：信号是"交易指令"（有去重、有明确的 BUY/SELL 语义），告警是"通知"（有级别、有冷却）

#### 表 5：tracker_schema（持仓跟踪定义）

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
    "max_drawdown": {"type": "float", "default": 0},
    "hold_days": {"type": "float", "default": 0},
    "ttl": {"type": "int", "default": 0, "desc": "总TTL(秒),0表示无限制"},
    "ttl_remaining": {"type": "float", "default": 0, "desc": "剩余TTL(秒)"},
    "status": {"type": "string", "default": "holding", "desc": "holding/closed/timeout"},
    "pool_id": {"type": "string", "default": ""},
    "node_id": {"type": "string", "default": ""},
    "flow_id": {"type": "string", "default": ""}
  },
  "init_profile": {
    "entry_price": {"source": "current_price"},
    "entry_time": {"source": "now_ts"}
  },
  "update_formulas": {
    "profit_pct": "(current_price - entry_price) / entry_price * 100",
    "max_profit": "max(max_profit, profit_pct)",
    "ttl_remaining": "ttl - (now_ts - entry_time)"
  }
}
```

**为什么单独一张表？**
- tracker 是核心业务状态——股票入池后发生了什么、赚了多少、持仓多久——这些都是股票池的核心价值
- 单独一张表，职责更清晰

#### 表 6：system_config（系统参数）

**干什么的：** 全局系统参数，不随池子变化而变化。

**关键字段：**

```json
{
  "tick_interval": 1,
  "trading_calendar": {
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
  "alert_defaults": {
    "default_cooldown_sec": 60
  },
  "signal_defaults": {
    "default_dedup_window_sec": 86400
  }
}
```

---

## 四、运行时表清单（10 张）

### 4.1 总览

| # | 表名 | 一句话说明 | 现有对应 | 类型 |
|---|------|-----------|---------|------|
| 1 | **node_stocks** | 每个节点当前有哪些股票 | `node_stocks` | 核心业务状态 |
| 2 | **market_data** | 每只股票的最新行情数据（唯一真相源） | `_latest_tick` | 核心业务状态 |
| 3 | **edge_timing_state** | 每条边的时序触发状态 | `_flow_last_fire_ts + _flow_first_fire_ts + _flow_exec_counts + _flow_duration_starts` | 核心业务状态 |
| 4 | **node_ttl_state** | 每个节点每只股票的 TTL 剩余时间 | `tracker.ttl_remaining`（但这是 per-node 的，不是 per-tracker 的） | 核心业务状态 |
| 5 | **trackers** | 持仓跟踪：每只股票的入池跟踪信息 | `_trackers` | 核心业务状态 |
| 6 | **signal_state** | 信号状态：已触发的信号、去重记录 | `_signal_events + _signal_queue` | 核心业务状态 |
| 7 | **alert_state** | 告警状态：冷却记录、已发送记录 | `_alert_cooldown + _alert_queue + _alert_events` | 核心业务状态 |
| 8 | **pool_state** | 池子运行状态 | `_pool_start_time + _first_run + _current_bar_time` | 核心业务状态 |
| 9 | **events** | 统一事件队列 | `_event_queue + _alert_queue + _signal_queue` | 核心业务状态 |
| 10 | **pool_errors** | 错误记录：运行期异常、公式计算失败等 | （目前散落各处，无统一表） | 核心业务状态 |

**为什么是 10 张？**
- v2.1 说 7 张，缺了 node_ttl_state、signal_state、pool_errors
- v2.1 把 alert_cooldown 算一张，但那只是告警状态的一部分——告警状态还包括已发送记录、告警队列
- 10 张是合理的：每一张都是独立的业务状态，少了就说不清楚运行时发生了什么

### 4.2 各表详细说明

#### 表 1：node_stocks（节点股票）

**干什么的：** 每个节点当前有哪些股票，这是股票池最核心的运行时状态。

**结构：** `{node_id: [stock_dict, ...]}`

**关键字段（每只股票）：**
- `code`: 股票代码
- `name`: 股票名称
- `tracker_id`: 持仓跟踪 ID（指向 trackers 表）

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
- 唯一真相源原则：所有过滤计算、tracker 更新、预警判断都从这里取数
- 数据更新只做一件事：写 market_data，标记时间戳

#### 表 3：edge_timing_state（边时序状态）

**干什么的：** 每条边的触发状态记录——上次什么时候触发的、第一次什么时候触发的、已经触发了多少次、持续窗口从什么时候开始的。

**结构：** `{edge_id: {last_fire_ts, first_fire_ts, exec_count, duration_start}}`

**关键字段：**
- `last_fire_ts`: 上次触发时间戳（用于 interval 守门）
- `first_fire_ts`: 首次触发时间戳（用于 end 条件守门）
- `exec_count`: 已执行次数（用于 count 限制）
- `duration_start`: 持续窗口起始锚点（用于持续时间型条件）

**生命周期：** per_session

#### 表 4：node_ttl_state（节点 TTL 状态）

**干什么的：** 每只股票在每个节点里还能待多久——TTL 到期就自动淘汰。

**结构：** `{(node_id, code): {entry_ts, ttl_sec, remaining_sec}}`

**关键字段：**
- `entry_ts`: 进入该节点的时间戳
- `ttl_sec`: 总 TTL 秒数（从节点配置来）
- `remaining_sec`: 剩余秒数

**生命周期：** per_session（股票在节点中期间持续更新，出节点后删除）

**设计说明：**
- 为什么不是 trackers 的字段？因为 TTL 是 per-node 的——同一只股票在不同节点可以有不同的 TTL
- 一只股票可以同时在多个节点里（copy 模式），每个节点有自己的 TTL 计时
- tracker 是 per-pool 或 per-stock 的，记录的是整体持仓表现
- 节点 TTL 淘汰 = 股票从节点移出 = 触发 SELL 信号（如果是目标节点）

**与 trackers.ttl 的关系：**
- `node_ttl_state`: 运行时状态，记录"这只股票在这个节点还剩多久"
- `tracker.ttl`: 配置/快照字段，记录"入场时设定的 TTL 是多少"
- 两者的值在入池时同步，但 node_ttl_state 是每个 tick 递减的运行时状态

#### 表 5：trackers（持仓跟踪）

**干什么的：** 跟踪每只股票入池后的表现——什么时候进的、什么价进的、现在赚了多少、最大回撤多少。

**主键设计：** `{pool_id}_{code}` —— 同一只股票在同一个池子里只有一条活跃跟踪记录。

**结构：** `{tracker_id: tracker_dict}`，tracker_id = `{pool_id}_{code}`

**关键字段：**（由 tracker_schema 定义）
- `entry_price`: 入场价格
- `entry_time`: 入场时间戳
- `current_price`: 当前价格
- `profit_pct`: 当前收益率
- `max_profit`: 最大收益率
- `max_drawdown`: 最大回撤
- `hold_days`: 持仓天数
- `ttl`: 总 TTL（秒），从入池节点配置拷贝
- `status`: holding / closed / timeout
- `pool_id`: 所属池
- `node_id`: 当前所在节点
- `flow_id`: 入池边

**生命周期：** per_session（股票在池中期间持续更新，出池后可保留用于历史查询）

**设计说明：**
- 主键是 (pool_id, code)，不是 code——因为一只股票可以在多个池子里
- 同一只股票在同一个池子里，状态是唯一的（要么持仓，要么已平仓）
- v2.1 没说清楚主键，现在明确了

#### 表 6：signal_state（信号状态）

**干什么的：** 记录已产生的信号、去重状态，防止同一信号重复产生。

**结构：**
```
{
  "signals": [signal_list],         // 已产生的信号列表（可持久化）
  "dedup_keys": {dedup_key: ts},    // 去重键 -> 上次产生时间
  "pending_queue": [signal_list]    // 待消费的信号队列
}
```

**关键字段：**
- `signals`: 历史信号列表（用于回溯）
- `dedup_keys`: 去重记录（signal_rules.dedup_key 渲染后的值 -> 时间戳）
- `pending_queue`: 待推送的信号队列

**生命周期：** per_session（dedup_keys 可跨 session 持久化）

**设计说明：**
- 去重是信号的核心机制——不能同一 (股票, 信号类型, 日期) 反复产生信号
- 信号 vs 事件的区别：信号是"业务对象"（有状态、有去重），事件是"传输机制"（队列、消费）

#### 表 7：alert_state（告警状态）

**干什么的：** 记录告警冷却状态、已发送告警，防止同一告警反复刷屏。

**结构：**
```
{
  "cooldowns": {(rule_id, code): cooldown_end_ts},  // 冷却状态
  "sent_alerts": [alert_list],                       // 已发送告警（历史）
  "pending_queue": [alert_list]                      // 待发送队列
}
```

**关键字段：**
- `cooldowns`: 冷却记录（规则ID + 股票代码 -> 冷却结束时间戳）
- `sent_alerts`: 历史告警列表
- `pending_queue`: 待推送的告警队列

**生命周期：** per_session（cooldowns 可跨 session 持久化）

**设计说明：**
- v2.1 只说了 alert_cooldown，但那只是告警状态的一部分
- 完整的告警状态包括：冷却、历史记录、待发送队列

#### 表 8：pool_state（池子运行状态）

**干什么的：** 池子整体的运行状态——什么时候开始的、是不是第一次跑、当前数据时间是什么。

**结构：** 单例（一个池子一个状态对象）

**关键字段：**
- `start_time`: 池子启动时间
- `first_run`: 是否首次运行（首次强制全量计算）
- `current_data_time`: 当前行情数据时间（用于判断数据是否更新）
- `last_data_time`: 上次行情数据时间（对比用）
- `paused`: 是否暂停
- `running`: 是否在运行

**生命周期：** per_session

#### 表 9：events（事件队列）

**干什么的：** 所有事件的统一出口——入池、出池、预警、交易信号、错误，都走这里。

**结构：** 队列（FIFO）

**事件类型：**
- `transfer`: 股票流转（入池/出池）
- `alert`: 预警事件
- `signal`: 交易信号
- `pool_start`: 池子启动
- `pool_stop`: 池子停止
- `error`: 错误事件

**生命周期：** per_session（消费后出队）

**设计说明：**
- 统一事件总线：前端刷新、告警通知、交易接口，都从这里消费
- 替代现在的 `_event_queue + _alert_queue + _signal_queue` 三套队列

#### 表 10：pool_errors（错误记录）

**干什么的：** 统一记录运行期的错误——公式计算失败、数据获取失败、边执行异常等。

**结构：** `[error_record, ...]`（环形缓冲区，保留最近 N 条）

**关键字段：**
- `timestamp`: 错误时间戳
- `type`: 错误类型（formula_error / data_error / edge_error / system_error）
- `source`: 错误来源（哪个公式、哪条边、哪个模块）
- `message`: 错误信息
- `stack`: 堆栈信息（可选）
- `context`: 错误上下文（股票代码、节点、边等）

**生命周期：** per_session（可配置保留条数，超出后覆盖最旧的）

**设计说明：**
- 为什么要有这张表？因为现在错误都是打日志，没有统一管理
- 重构后，前端错误面板、告警联动（错误次数太多自动暂停）都依赖这张表
- 这是"可观测性"的基础

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

## 五、核心循环伪代码（约 35 行）

```python
async def run_pool(pool_config):
    compiled = compile_pool(pool_config)       # 编译期：一次编译，运行期只读
    pool_state = init_pool_state()
    node_stocks, node_ttl_state = init_nodes(compiled)
    market_data, trackers = {}, {}
    edge_timing_state, signal_state, alert_state = {}, {}, {}
    events, pool_errors = [], []

    while pool_state.running:
        await sleep(system_config.tick_interval)
        if not is_trading_time() or pool_state.paused: continue

        try:
            # 1. 刷新行情数据（唯一入口）
            new_market_data = refresh_market_data()
            data_changed = new_market_data.timestamp != pool_state.last_data_time

            # 2. 节点 TTL 淘汰（先淘汰，再执行边）
            expired = expire_node_ttl(node_ttl_state, now())
            for code, node_id in expired:
                remove_from_node(node_stocks, node_id, code)
                close_tracker(trackers, code, status='timeout')
                emit_event(events, 'transfer', {'code': code, 'action': 'timeout_exit'})
                maybe_emit_signal('SELL', code, trackers, signal_state, compiled, events)

            # 3. 按执行顺序处理每条边
            for edge in compiled.exec_order_edges:
                ts = edge_timing_state[edge.id]
                if not should_trigger(edge, ts, pool_state): continue
                src_changed = source_changed(edge.from_id, node_stocks, pool_state)
                if not (src_changed or data_changed): continue

                try:
                    src_stocks = node_stocks[edge.from_id]
                    filtered = filter_stocks(edge.filter, src_stocks, new_market_data)
                    transferred = transfer_stocks(filtered, edge, node_stocks, node_ttl_state)

                    for s in transferred.entered:
                        init_tracker_if_needed(trackers, s, edge, compiled)
                        maybe_emit_signal('BUY', s['code'], trackers, signal_state, compiled, events)
                    for s in transferred.exited:
                        maybe_emit_signal('SELL', s['code'], trackers, signal_state, compiled, events)

                    # 告警检查（流转后检查新入池/出池的股票）
                    check_alerts(transferred.entered + transferred.exited, trackers,
                                 alert_rules, alert_state, events)

                    ts.last_fire_ts = now()
                    ts.exec_count += 1
                    if ts.first_fire_ts is None: ts.first_fire_ts = ts.last_fire_ts

                    if transferred:
                        emit_transfer_events(edge, transferred, events)
                except Exception as e:
                    record_error(pool_errors, 'edge_error', edge.id, e)

            # 4. tick 末：更新全局状态
            update_trackers(trackers, node_stocks, new_market_data)

            # 5. 周期告警检查（所有持仓股票）
            check_periodic_alerts(trackers, alert_rules, alert_state, events)

            pool_state.last_data_time = new_market_data.timestamp
            pool_state.first_run = False
        except Exception as e:
            record_error(pool_errors, 'tick_error', 'main_loop', e)

        # 6. 推送事件给前端/消费者
        flush_events(events)
```

### 人话解释（6 步）

1. **等一个 tick**，看看是不是交易时间、是不是暂停了，不是就继续睡
2. **拉最新行情**，对比时间戳——数据没变就跳过重算
3. **先做 TTL 淘汰**：检查每个节点每只股票的剩余时间，到期就踢出去，触发 SELL 信号
4. **按执行顺序检查每条边**：
   - 触发时间到了吗？（interval/begin/end/count）
   - 源节点股票变了吗？或者行情数据变了吗？
   - 都满足 → 执行过滤，股票流过去，更新 tracker，发信号，发告警
5. **tick 末**：更新所有 tracker、检查周期告警、保存快照、清理标记
6. **推送事件**：前端刷新、告警通知、交易接口，都从事件队列取

### 关键修正点（相比 v2.1）

1. **错误处理**：每个边的执行都有 try/except，错误记录到 pool_errors，不中断主循环
2. **节点 TTL 淘汰**：在边执行之前做，因为 TTL 到期的股票应该先被移除
3. **信号生成**：入池时可能产生 BUY，出池/超时时可能产生 SELL，有去重机制
4. **告警触发时机**：有两个时机——
   - 流转时：检查新入池/出池的股票
   - 周期检查：每个 tick 末检查所有持仓股票（止盈/止损/回撤等）
5. **source_changed 检测位置**：在 should_trigger 之后，因为如果时间条件都不满足，没必要检测源变化
6. **trackers 主键**：(pool_id, code)，不是 code

---

## 六、compile_pool 编译期

### 6.1 编译期的职责

**输入：** pool_config（用户配置的池子定义）

**输出：** compiled_pool（编译后的池子对象，运行期只读）

**编译期做什么：**

| # | 工作项 | 说明 |
|---|--------|------|
| 1 | **拓扑排序验证** | 检查有没有环，计算每个节点的深度 |
| 2 | **执行顺序验证** | 用户指定的 exec_order 是否违背拓扑依赖（A→B，A 必须在 B 前面） |
| 3 | **公式语法检查** | 所有引用的 formula_id 在 formula_lib 中存在吗？公式能正常编译吗？ |
| 4 | **触发器类型检查** | 边的 timing 配置合法吗？（比如 interval 不能是负数） |
| 5 | **节点类型验证** | 节点类型合法吗？参数完整吗？ |
| 6 | **边上下文预计算** | 预计算每条边的源节点、目标节点、filter_type 等，运行期直接用 |
| 7 | **告警规则绑定** | 哪些告警规则适用于这个池子？预绑定 |
| 8 | **信号规则绑定** | 哪些信号规则适用于这个池子？预绑定 |
| 9 | **TTL 配置提取** | 每个节点的 TTL 配置提取出来，运行期直接查 |

### 6.2 编译产物结构

```python
CompiledPool = {
    # 基本结构
    "pool_id": "pool_001",
    "nodes": {nid: node_dict},          # 节点字典
    "edges": {eid: edge_dict},          # 边字典
    "node_ttl_config": {nid: ttl_config},  # 节点 TTL 配置（预提取）

    # 执行顺序
    "exec_order": [eid1, eid2, ...],    # 边的执行顺序（用户指定 + 拓扑验证通过）
    "topo_order": [nid1, nid2, ...],    # 节点的拓扑顺序（备用）
    "node_depths": {nid: depth},        # 节点深度

    # 预编译的公式
    "compiled_formulas": {
        formula_id: compiled_expression  # 预编译的公式对象，运行期直接 eval
    },

    # 预计算的边上下文（运行期不用再查表）
    "edge_ctx": {
        eid: {
            "from_node": node_dict,
            "to_node": node_dict,
            "filter_type": "conditional",  # 预判定的过滤类型
            "timing_spec": {...},          # 预编译的时机规则
            "ttl_spec": {...}              # 预编译的 TTL 规则
        }
    },

    # 绑定的告警/信号规则（哪些规则适用于本池子）
    "bound_alert_rules": [rule_id1, rule_id2, ...],
    "bound_signal_rules": [signal_id1, signal_id2, ...],

    # 邻接表（便于运行期查找）
    "out_edges": {nid: [eid, ...]},     # 节点的出边
    "in_edges": {nid: [eid, ...]}       # 节点的入边
}
```

### 6.3 编译期 vs 运行期的边界

| 维度 | 编译期 | 运行期 |
|------|--------|--------|
| **时间点** | 池子启动时（一次） | 每个 tick（反复） |
| **输入** | pool_config + formula_lib + alert_rules + signal_rules | market_data + 运行时状态 |
| **输出** | compiled_pool（静态对象） | 状态变更 + 事件 |
| **性能要求** | 不敏感（慢一点没关系） | 敏感（每个 tick 都要快） |
| **可以失败吗** | 可以（配置错误，编译失败，池子不启动） | 不可以（单个边失败不能崩主循环） |
| **代码对应** | `MetaEngine._compile_pool()` → `CompiledSchedule` | `MetaEngine.run_loop()` |

**代码验证：** 当前 `engine.py:1354` 的 `_compile_pool` 已经实现了编译期的基本框架，返回 `CompiledSchedule` 对象，包含 `nodes/edges/edge_index/depths/topo_order/processing_plan/out_edges/in_edges/edge_ctx/edge_timing/edge_filter_spec/edge_flow_spec/edge_action_spec/edge_ttl_spec` 等字段。

**差距：**
- 当前编译期主要编译"边的 5 维 spec"（timing/filter/flow/action/ttl）
- 还没有编译告警规则、信号规则的绑定
- 还没有公式语法检查（公式编译是在运行期做的）
- 还没有执行顺序验证（目前是按深度排序，不是用户指定的 exec_order）

---

## 七、前端后端对应关系

### 7.1 对应关系表

| 前端概念 | 后端概念 | 说明 |
|---------|---------|------|
| **综合设置表格** | pool_config（节点 + 边） | 表格每一行 = 一个节点或一条边，左列缩进 = 拓扑结构 |
| **拓扑画布** | pool_config（节点 + 边） | 同一份数据的可视化展示，圆圈=节点，连线=边 |
| **执行顺序功能** | pool_config.edges[*].exec_order | 用户拖拽调整的执行顺序，保存在边的属性里 |
| **公式编辑器** | formula_lib | 编辑公式库中的公式 |
| **状态属性面板** | tracker_schema + trackers | 左边是 tracker 字段定义，右边是当前值 |
| **时序设置面板** | edge.timing + edge_timing_state | 左边是配置（interval/begin/end），右边是运行状态 |
| **预警面板** | events（alert 类型） + alert_state | 从事件队列消费告警事件展示，冷却状态从 alert_state 读 |
| **交易信号面板** | events（signal 类型） + signal_state | 从事件队列消费信号事件展示，去重状态从 signal_state 读 |
| **错误面板** | pool_errors | 直接从 pool_errors 表读错误列表 |
| **节点 TTL 设置** | node.ttl + node_ttl_state | 节点属性面板里配置 TTL，运行时显示剩余时间 |

### 7.2 一句话总结

**综合设置 = 拓扑画布 = pool_config，它们是同一份数据的三种表现形式。**

- 综合设置：表格视图，方便编辑参数和调整顺序
- 拓扑画布：图形视图，方便看整体结构
- pool_config：数据结构，程序内部用

### 7.3 执行顺序 vs 拓扑顺序

| 维度 | 拓扑顺序 | 执行顺序 |
|------|---------|---------|
| **由什么决定** | 节点连接关系（A→B，A 在 B 前面） | 用户配置（可调整同层级节点的先后） |
| **运行时能变吗** | 不能（拓扑不变） | 能（用户可随时调整） |
| **约束** | 由连接关系自然决定 | 不能违背拓扑依赖（A 必须在 B 前面） |
| **代码对应** | `compiled.depths` 推导 | `pool_config.edges[*].exec_order` 用户指定 |
| **编译期验证** | 自动计算 | 验证不违背拓扑依赖 |

**代码验证：** 当前 `compiled.topo_order` 是按深度排序（`engine.py:1379`），理论上可以换成用户指定的顺序，只要不违背拓扑依赖。

---

## 八、重构路径细化（五步走）

### 8.1 重构原则

1. **能合并的合并**：90 张配置表 → 6 张核心配置表
2. **能内联的内联**：简单逻辑不要拆成"表驱动"
3. **能删的删掉**：没用的概念、没用的中间表、没用的抽象层
4. **一个真相源**：数据只有一个入口，计算只有一个引擎，事件只有一个总线
5. **先补全再优化**：先把骨架和肉都长全，再考虑性能优化

### 8.2 分五步走

#### 第一步：统一数据层（解决"数据更新各搞一套"）

**目标：** `market_data` 是唯一真相源，所有计算从这里取数。

**动作：**
- [ ] 把 K 线更新、tick 更新、备选池刷新统一成一条数据通路
- [ ] 建立 `market_data` 运行时表，作为唯一的数据真相源
- [ ] 数据更新只做一件事：写 `market_data`，更新时间戳
- [ ] 废弃 `_data_cache`、`_current_bar_data` 等重复缓存

**验收：** 所有过滤计算、tracker 更新、预警判断都从 `market_data` 取数，没有第二条数据通路。

#### 第二步：补全运行时表（解决"运行时表缺失"）

**目标：** 30+ 运行时表 → 10 张核心业务状态表。

**动作：**
- [ ] 合并边时序状态：`_flow_last_fire_ts + _flow_first_fire_ts + _flow_exec_counts + _flow_duration_starts` → `edge_timing_state`
- [ ] 新增 `node_ttl_state`：节点 TTL 状态（从节点配置 + 入池时间计算）
- [ ] 修正 `trackers` 主键：code → (pool_id, code)
- [ ] 新增 `signal_state`：信号状态（去重 + 历史 + 队列）
- [ ] 扩展 `alert_state`：从只有 cooldown → cooldown + 历史 + 队列
- [ ] 合并池子状态：`_pool_start_time + _first_run + _current_bar_time` → `pool_state`
- [ ] 统一事件队列：`_event_queue + _alert_queue + _signal_queue` → `events`
- [ ] 新增 `pool_errors`：统一错误记录表
- [ ] 明确哪些是中间缓存，从 `_RUNTIME_TABLE_NAMES` 中移除，改为局部变量或私有属性
- [ ] 移除 `_loop_node_stocks` 冗余副本，直接用 `node_stocks`

**验收：** `_RUNTIME_TABLE_NAMES` 中只有 10 个核心业务状态表，其他都是实现细节，不再作为"一等公民"。

#### 第三步：统一计算层（解决"公式计算各搞一套"）

**目标：** 一个公式引擎，所有过滤/指标/tracker/预警/信号都走这里。

**动作：**
- [ ] 条件过滤、tracker 计算、预警判断、信号条件——统一调用同一个公式引擎
- [ ] 相同股票相同周期的 K 线只算一次，结果复用
- [ ] 过滤逻辑回归本质：输入=股票列表+条件，输出=过滤后的列表
- [ ] tracker 更新公式从 `tracker_schema` 读取，统一计算
- [ ] 告警条件从 `alert_rules` 读取，统一计算
- [ ] 信号条件从 `signal_rules` 读取，统一计算

**验收：** 公式计算只有一个入口，没有重复计算。

#### 第四步：完善编译期（解决"编译期职责不清"）

**目标：** compile_pool 做完整的编译期工作，运行期只读 compiled_pool。

**动作：**
- [ ] 编译期做公式语法检查（提前发现公式错误，不要等到运行期）
- [ ] 编译期做执行顺序验证（用户指定的 exec_order 不违背拓扑依赖）
- [ ] 编译期做告警/信号规则绑定（哪些规则适用于本池子）
- [ ] 编译期预提取节点 TTL 配置
- [ ] 运行期所有"查表"都改成读 compiled_pool 里的预计算结果
- [ ] 编译失败时池子不启动，给出明确的错误信息

**验收：** 运行期没有"配置查表"，所有静态数据都在 compiled_pool 里；编译期能提前发现配置错误。

#### 第五步：精简配置层（解决"配置表 90 张"）

**目标：** 90+ 张配置表 → 6 张核心配置表 + 可选的扩展表。

**动作：**
- [ ] 把分散的节点/边/执行顺序合并进 `pool_config`
- [ ] 把分散的公式定义合并进 `formula_lib`
- [ ] 把告警规则定义独立为 `alert_rules`
- [ ] 把信号规则定义独立为 `signal_rules`
- [ ] 把 tracker 字段定义独立为 `tracker_schema`
- [ ] 把系统参数合并进 `system_config`
- [ ] 评估剩下的 80+ 张表：
  - 只有几行的简单逻辑 → 直接内联到代码里
  - 纯 UI 相关的 → 移到前端，不放在后端配置里
  - 真正需要配置的 → 合并进上述 6 张表

**验收：** 后端核心配置表只有 6 张，新人能在 10 分钟内理解配置结构。

### 8.3 最终会变成什么样？

```
核心文件（6-7 个）：
├── engine.py           # 主循环 + 边执行调度（500 行以内）
├── data_provider.py    # 数据接入（统一入口）
├── formula_engine.py   # 公式计算（统一引擎）
├── tracker.py          # 持仓跟踪（独立模块）
├── event_bus.py        # 事件总线（统一出口）
├── pool_compiler.py    # 编译期：pool_config → compiled_pool
└── pool_config.py      # 配置加载 + 校验

核心表（16 张）：
┌─ 配置表（6 张）───────┐
│ pool_config          │  节点 + 边 + 执行顺序
│ formula_lib          │  公式库
│ alert_rules          │  告警规则
│ signal_rules         │  交易信号规则
│ tracker_schema       │  持仓跟踪定义
│ system_config        │  系统参数
└──────────────────────┘
┌─ 运行时表（10 张）────┐
│ node_stocks          │  节点股票
│ market_data          │  行情数据（唯一真相源）
│ edge_timing_state    │  边时序状态
│ node_ttl_state       │  节点 TTL 状态
│ trackers             │  持仓跟踪
│ signal_state         │  信号状态（去重+历史+队列）
│ alert_state          │  告警状态（冷却+历史+队列）
│ pool_state           │  池子运行状态
│ events               │  事件队列
│ pool_errors          │  错误记录
└──────────────────────┘
```

---

## 九、时间和触发的处理

### 9.1 为什么时间是核心维度？

股票池的本质是"**在正确的时间做正确的筛选**"——时间不对，一切都白搭。

### 9.2 时间的四个层次

| 层次 | 概念 | 说明 | 对应表 |
|------|------|------|--------|
| **全局时间** | 当前行情时间 | 数据最新到什么时候了 | `pool_state.current_data_time` |
| **池子时间** | 池子启动时间 | 池子从什么时候开始跑的 | `pool_state.start_time` |
| **边时间** | 边的触发状态 | 这条边上次/首次什么时候触发的、触发了几次 | `edge_timing_state` |
| **节点时间** | 节点 TTL 状态 | 这只股票在这个节点还剩多久 | `node_ttl_state` |

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

### 9.5 节点 TTL 的递减逻辑

```
每个 tick 开始时：
  遍历 node_ttl_state 中的每只股票：
    remaining = ttl_sec - (now - entry_ts)
    如果 remaining <= 0：
      从节点中移除该股票
      关闭 tracker（status = 'timeout'）
      触发 SELL 信号（如果是目标节点）
      发出 transfer 事件（timeout_exit）
```

**为什么在边执行之前做 TTL 淘汰？**
- TTL 到期的股票不应该再参与本 tick 的边计算
- 如果先执行边再淘汰，TTL 到期的股票可能被错误地流转到下一个节点

### 9.6 时间源的统一

现在有三种时间源：
- 实时模式：系统时间
- 回放模式：K 线时间推进
- 仿真模式：虚拟时钟推进

**统一到 `pool_state.current_data_time`：** 不管什么模式，"当前时间"就是这个值，所有触发判定都用它。不同模式只是推进这个值的方式不同。

---

## 十、告警与信号的区别

| 维度 | 告警 (Alert) | 信号 (Signal) |
|------|-------------|--------------|
| **目的** | 通知用户"发生了什么" | 产生交易指令"该买/卖了" |
| **类型** | info / warning / critical | BUY / SELL |
| **去重机制** | 冷却时间（cooldown_sec） | 去重键（dedup_key） |
| **触发时机** | 流转时 + 周期检查 | 入池时 + 出池时 + 超时 |
| **业务语义** | "你看看这个情况" | "执行这个操作" |
| **消费方** | 前端告警面板、通知渠道（飞书/短信/邮件） | 交易接口、策略引擎 |
| **历史记录** | 有（已发送告警） | 有（已产生信号） |
| **配置表** | alert_rules | signal_rules |
| **运行时表** | alert_state | signal_state |

**两者的关系：**
- 信号产生时，可以同时触发一个告警（比如"产生 BUY 信号"本身也是一种告警）
- 但告警不等于信号——告警是通知，信号是指令
- 告警可以有很多级别，信号只有 BUY/SELL 两种（加上方向的话还有更多，但本质是买卖）

---

## 十一、一句话总结

> **股票池就是几个篮子用管子连起来，管子上有筛子和定时器，定时看看有没有新数据，有就筛一下，股票从上游流到下游；同时记一下每只股票什么时候进来的、赚了多少、还能待多久，筛出来了就通知一下，该买卖了就发个信号。**

理解了这句话，再加上 6 张配置表、10 张运行时表、8 个核心概念、约 35 行核心循环，就完全理解了股票池的全部。
