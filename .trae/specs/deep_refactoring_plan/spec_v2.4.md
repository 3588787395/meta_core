# 股票池架构设计 v2.4

> 版本主题：缺陷修复 + 信号展开 + 契约补全，冲击 80 分
> 设计原则：从"伪代码可直接写代码"到"伪代码带着契约写，开发不用猜"
> 上一版 v2.3 问题：check_alerts 削足适履（统计级告警按股票循环）、propagate_stocks 三种模式混用、信号管理是黑盒、伪代码缺依赖契约
> 目标：修复 2 个核心伪代码设计缺陷、信号管理从黑盒变伪代码、所有伪代码补全依赖契约，冲击 80 分及格线

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
| 6 | **告警 (Alert)** | 满足特定条件时发出的通知，有作用域、有类型、有冷却机制 | `alert_rules + alert_state` | 行为 |
| 7 | **信号 (Signal)** | 满足特定条件时产生的交易指令（BUY/SELL），绑定到节点、有去重机制 | `signal_rules + signal_state` | 行为 |
| 8 | **跟踪 (Tracker)** | 股票入池后的持仓表现记录（入场价/时间/收益/TTL 等） | `tracker_schema + trackers` | 实体 |

**分类原则：实体 vs 行为**
- 实体（5个）：节点、边、股票、公式、跟踪 → 有状态、有生命周期
- 行为（3个）：触发、告警、信号 → 是动作、有判定逻辑

---

## 三、配置表清单（6 张）

### 3.1 总览

| # | 表名 | 一句话说明 | 现有对应 |
|---|------|-----------|---------|
| 1 | **pool_config** | 池子定义：节点 + 边 + 执行顺序 + 绑定的告警/信号规则 | `pool_config = {nodes, edges}` |
| 2 | **formula_lib** | 公式库：所有可用的选股公式/指标 | `builtin_formulas.json + custom_formulas.json` |
| 3 | **alert_rules** | 告警规则：什么条件下发什么告警，有 scope 字段、有 alert_type 字段 | `alert_rules.json` |
| 4 | **signal_rules** | 交易信号规则：什么条件下产生什么信号，有 scope 字段 | `signal_rules.json` |
| 5 | **tracker_schema** | 持仓跟踪字段定义与计算公式 | `tracker_schema.json` |
| 6 | **system_config** | 系统参数：tick 间隔、交易时间、缓存策略等 | `timing.json + defaults.json + data_config.json` |

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
      },
      "alert_rule_ids": ["profit_threshold", "drawdown_threshold", "node_stock_count"],
      "signal_rule_ids": ["BUY_state_pool"]
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
      "exec_order": 1,
      "alert_rule_ids": ["edge_triggered", "edge_pass_rate"]
    }
  ]
}
```

**设计说明：**
- 节点和边放在同一张表里，因为它们是一个整体——池子的拓扑结构
- `exec_order` 是边的属性（不是独立概念），用户可以调整执行顺序
- 约束：执行顺序不能违背拓扑依赖（A→B，A 必须在 B 前面）
- 节点有自己的 TTL 配置（股票在节点里能待多久）
- **节点和边都绑定告警/信号规则**（通过 alert_rule_ids / signal_rule_ids），不是全局的

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

#### 表 3：alert_rules（告警规则）

**干什么的：** 定义什么条件下触发什么告警，包括作用域、告警类型、告警级别、冷却时间。

**关键字段（v2.4 更新：加 alert_type 字段）：**

```json
{
  "rules": {
    "profit_threshold": {
      "rule_id": "profit_threshold",
      "name": "止盈告警",
      "scope_type": "node",
      "alert_type": "stock_level",
      "scope_target": null,
      "condition": "tracker.profit_pct >= 10",
      "formula_id": null,
      "severity": "warning",
      "cooldown_sec": 300,
      "message_template": "{code} 达到止盈线，当前收益 {profit_pct}%"
    },
    "node_stock_count": {
      "rule_id": "node_stock_count",
      "name": "节点股票数超限",
      "scope_type": "node",
      "alert_type": "stats_level",
      "scope_target": null,
      "condition": "stock_count > 100",
      "formula_id": null,
      "severity": "info",
      "cooldown_sec": 600,
      "message_template": "节点 {node_id} 股票数达 {stock_count}，超过阈值 100"
    },
    "edge_triggered": {
      "rule_id": "edge_triggered",
      "name": "边触发告警",
      "scope_type": "edge",
      "alert_type": "stock_level",
      "scope_target": null,
      "condition": "transferred_count > 0",
      "severity": "info",
      "cooldown_sec": 60
    },
    "edge_pass_rate": {
      "rule_id": "edge_pass_rate",
      "name": "边通过率告警",
      "scope_type": "edge",
      "alert_type": "stats_level",
      "scope_target": null,
      "condition": "pass_rate < 0.1",
      "severity": "warning",
      "cooldown_sec": 300,
      "message_template": "边 {edge_id} 通过率仅 {pass_rate:.2%}，低于 10%"
    },
    "pool_empty": {
      "rule_id": "pool_empty",
      "name": "池子空了",
      "scope_type": "pool",
      "alert_type": "stats_level",
      "scope_target": null,
      "condition": "total_stocks == 0",
      "severity": "warning",
      "cooldown_sec": 600
    }
  }
}
```

**v2.4 重要更新 1：作用域 × 告警类型 矩阵**

告警规则不是全局的，是有作用域的，并且有两种类型：

| alert_type | 含义 | 检查方式 | 典型例子 |
|-----------|------|---------|---------|
| `stock_level` | 股票级告警 | 每只股票独立检查 | 某股票入池、某股票涨幅>5%、某股票止盈 |
| `stats_level` | 统计级告警 | 对节点/池子整体做统计后检查 | 节点股票数>100、池子整体回撤>10%、边通过率<10% |

**作用域 × 告警类型 矩阵：**

| 作用域 | 股票级告警（stock_level） | 统计级告警（stats_level） |
|-------|-------------------------|-------------------------|
| **node** | 入池/出池/持仓变化告警（如止盈、回撤） | 节点股票数/市值/收益率告警（如股票数>100、平均收益>5%） |
| **edge** | 边通过/拒绝告警（如某股票通过筛选） | 边通过率/过滤效率告警（如通过率<10%、流转数>50） |
| **pool** | 全池子股票告警（很少用） | 池子整体指标告警（如池子空了、全池亏损>20%） |

**编译期绑定：**
- 编译 `pool_config` 时，把每个节点/边绑定的 `alert_rule_ids` 解析出来
- 把告警规则的配置预编译（条件表达式编译）
- 运行时只检查绑定到当前节点/边/池的规则，不需要遍历全局

#### 表 4：signal_rules（交易信号规则）

**干什么的：** 定义什么条件下产生什么交易信号（BUY/SELL），包括触发时机、去重机制。

**关键字段（v2.3 已有 scope 字段，v2.4 保持）：**

```json
{
  "rules": {
    "BUY_state_pool": {
      "rule_id": "BUY_state_pool",
      "signal_type": "BUY",
      "scope_type": "node",
      "trigger_event": "enter",
      "condition": "target_node.type == 'state_pool' AND tracker == null",
      "formula_id": null,
      "dedup_window_sec": 3600,
      "dedup_key": "{code}_{signal_type}",
      "priority": 10,
      "stop_on_match": true,
      "field_mapping": {
        "price": "tracker.current_price",
        "profit_pct": "tracker.profit_pct"
      }
    },
    "SELL_state_pool": {
      "rule_id": "SELL_state_pool",
      "signal_type": "SELL",
      "scope_type": "node",
      "trigger_event": "exit",
      "exit_reasons": ["move", "timeout", "ttl"],
      "condition": "source_node.type == 'state_pool' AND tracker.status == 'holding'",
      "dedup_window_sec": 3600,
      "dedup_key": "{code}_{signal_type}"
    }
  }
}
```

**信号规则也有作用域：**

| scope_type | 含义 | 触发时机 | 绑定到哪里 |
|-----------|------|---------|-----------|
| `node` | 节点级信号 | 股票入池（enter）或出池（exit）时 | 节点的 signal_rule_ids |
| `edge` | 边级信号 | 边执行流转时 | 边的 signal_rule_ids |
| `pool` | 池子级信号 | 周期检查时 | pool_config.signal_rule_ids |

**编译期绑定：**
- 编译 `pool_config` 时，把每个节点绑定的 `signal_rule_ids` 解析出来
- 预编译信号规则的条件表达式
- 运行时只检查绑定到当前节点的信号规则

#### 表 5：tracker_schema（持仓跟踪定义）

同 v2.3，略。

#### 表 6：system_config（系统参数）

同 v2.3，略。

---

## 四、运行时表清单（10 张）

同 v2.3，略。10 张核心业务状态表：
node_stocks、market_data、edge_timing_state、node_ttl_state、trackers、signal_state、alert_state、pool_state、events、pool_errors。

---

## 五、统一时间源模型（v2.3 已有，v2.4 保持）

### 5.1 两个时间源，各司其职

| 时间源函数 | 含义 | 用途 | 底层实现 |
|-----------|------|------|---------|
| `pool_now()` | 池子当前时间（触发时间） | 触发判断、TTL 递减、冷却判断、事件时间戳 | 系统时间 / 虚拟时钟 / bar 时间（按模式切换） |
| `data_ts(code)` | 某只股票的数据时间戳 | 判断数据是否更新、计算指标用的时间 | market_data[code].timestamp |

**核心原则：触发用 pool_now()，数据用 data_ts()，两者绝不混用。**

---

## 六、核心函数伪代码（v2.4 全面更新：缺陷修复 + 信号展开 + 契约补全）

### 6.0 依赖契约说明（v2.4 新增）

**每个伪代码函数都包含以下四个契约：**

| 契约项 | 说明 | 例子 |
|-------|------|------|
| **输入参数** | 参数名、类型、含义 | `edge: dict` - 边配置（含 timing） |
| **返回值** | 类型、含义 | `bool` - True=应该触发，False=跳过 |
| **副作用** | 修改了哪些表/状态 | 更新 `timing_state.last_fire_ts` |
| **调用时机** | 什么时候调用 | 每条边每个 tick 开始时调用一次 |

### 6.1 核心循环（v2.4 更新：信号管理纳入主循环）

```python
async def run_pool(pool_config):
    """股票池主循环。
    
    【输入参数】
      pool_config: dict - 池子配置（含 nodes/edges/绑定的规则ID）
    
    【返回值】
      无（永不返回，直到 pool_state.running = False）
    
    【副作用】
      - 修改 node_stocks：股票流转
      - 修改 edge_timing_state：边时序状态推进
      - 修改 node_ttl_state：TTL 递减与淘汰
      - 修改 trackers：持仓跟踪更新
      - 修改 signal_state：信号生成与去重
      - 修改 alert_state：告警触发与冷却
      - 修改 events：事件入队
      - 修改 pool_state：全局状态推进
    
    【调用时机】
      池子启动后持续运行，每个 tick 执行一次
    """
    compiled = compile_pool(pool_config)
    pool_state = init_pool_state()
    node_stocks, node_ttl_state = init_nodes(compiled)
    market_data, trackers = {}, {}
    edge_timing_state, signal_state, alert_state = {}, {}, {}
    events, pool_errors = [], []

    while pool_state.running:
        await sleep(system_config.tick_interval)
        if not is_trading_time(pool_now()) or pool_state.paused:
            continue

        try:
            # 1. 刷新行情数据（唯一入口）
            new_market_data = refresh_market_data()
            data_changed = new_market_data.latest_ts > pool_state.last_data_ts

            # 2. 节点 TTL 淘汰（先淘汰，再执行边）
            expired = expire_node_ttl(node_ttl_state, pool_now_ts())
            for code, node_id in expired:
                remove_from_node(node_stocks, node_id, code)
                close_tracker(trackers, code, status='timeout')
                emit_event(events, 'transfer', {'code': code, 'action': 'timeout_exit'})
                # TTL 出池产生信号
                generate_signals(
                    scope_obj=compiled.nodes[node_id],
                    scope_type='node',
                    trigger_event='exit',
                    changes={'exited': [{'code': code}]},
                    trackers=trackers,
                    signal_rules=compiled.bound_signal_rules,
                    signal_state=signal_state,
                    events=events
                )

            # 3. 按执行顺序处理每条边
            for edge in compiled.exec_order_edges:
                ts = edge_timing_state[edge.id]
                if not should_trigger(edge, ts, pool_now_ts()):
                    continue
                src_changed = source_changed(edge.from_id, node_stocks, pool_state)
                if not (src_changed or data_changed):
                    continue

                try:
                    src_stocks = node_stocks[edge.from_id]
                    passed_codes = run_filter(edge, src_stocks, market_data)
                    changes = propagate_stocks(edge, passed_codes, node_stocks, node_ttl_state)

                    for s in changes.entered:
                        init_tracker_if_needed(trackers, s, edge, compiled)
                        # 入池产生信号
                        generate_signals(
                            scope_obj=compiled.nodes[edge.to_id],
                            scope_type='node',
                            trigger_event='enter',
                            changes={'entered': [s]},
                            trackers=trackers,
                            signal_rules=compiled.bound_signal_rules,
                            signal_state=signal_state,
                            events=events
                        )
                    for s in changes.exited:
                        # 出池产生信号（move 模式从源节点出池）
                        if edge.transfer_mode == 'move':
                            generate_signals(
                                scope_obj=compiled.nodes[edge.from_id],
                                scope_type='node',
                                trigger_event='exit',
                                changes={'exited': [s]},
                                trackers=trackers,
                                signal_rules=compiled.bound_signal_rules,
                                signal_state=signal_state,
                                events=events
                            )

                    # 边级告警（绑定到边上的告警规则）
                    check_alerts(
                        scope_obj=edge,
                        scope_type='edge',
                        changes=changes,
                        trackers=trackers,
                        alert_state=alert_state,
                        events=events,
                        compiled=compiled
                    )
                    # 目标节点级告警（入池）
                    check_alerts(
                        scope_obj=compiled.nodes[edge.to_id],
                        scope_type='node',
                        changes={'entered': changes.entered, 'all_stocks': node_stocks[edge.to_id]},
                        trackers=trackers,
                        alert_state=alert_state,
                        events=events,
                        compiled=compiled
                    )
                    # 源节点级告警（出池）
                    if changes.exited:
                        check_alerts(
                            scope_obj=compiled.nodes[edge.from_id],
                            scope_type='node',
                            changes={'exited': changes.exited, 'all_stocks': node_stocks[edge.from_id]},
                            trackers=trackers,
                            alert_state=alert_state,
                            events=events,
                            compiled=compiled
                        )

                    ts.last_fire_ts = pool_now_ts()
                    ts.exec_count += 1
                    if ts.first_fire_ts is None:
                        ts.first_fire_ts = ts.last_fire_ts

                    if changes:
                        emit_transfer_events(edge, changes, events)
                except Exception as e:
                    record_error(pool_errors, 'edge_error', edge.id, e)

            # 4. tick 末：更新全局状态
            update_trackers(trackers, node_stocks, market_data, pool_now_ts())

            # 5. 周期告警检查（所有节点的周期告警 + 统计级告警）
            for node_id, node in compiled.nodes.items():
                check_alerts(
                    scope_obj=node,
                    scope_type='node',
                    changes={'all_stocks': node_stocks[node_id]},
                    trackers=trackers,
                    alert_state=alert_state,
                    events=events,
                    compiled=compiled,
                    periodic=True
                )

            # 6. 池子级告警（统计级）
            check_alerts(
                scope_obj=pool_config,
                scope_type='pool',
                changes={'all_nodes': node_stocks},
                trackers=trackers,
                alert_state=alert_state,
                events=events,
                compiled=compiled,
                periodic=True
            )

            pool_state.last_data_ts = new_market_data.latest_ts
            pool_state.first_run = False
        except Exception as e:
            record_error(pool_errors, 'tick_error', 'main_loop', e)

        # 7. 推送事件给前端/消费者
        flush_events(events)
```

---

### 6.2 函数 1：should_trigger(edge, timing_state, now_ts) → bool

**作用：** 判断一条边是否应该触发（时机门控）。

```python
def should_trigger(edge, timing_state, now_ts):
    """判断一条边是否应该触发（四道关：begin → end → interval → 首次）。
    
    【输入参数】
      edge: dict - 边配置（含 timing 字段：begin/end/interval_sec/count）
      timing_state: dict - 边的时序状态
          - first_fire_ts: float|None - 首次触发时间戳（None 表示还没触发过）
          - last_fire_ts: float|None - 上次触发时间戳
          - exec_count: int - 已执行次数
          - duration_start: float|None - 持续时间起始时间戳
      now_ts: float - 当前池子时间戳（pool_now_ts()）
    
    【返回值】
      bool - True=应该触发，False=跳过
    
    【副作用】
      无（纯函数，只读）
    
    【调用时机】
      每条边每个 tick 开始时调用一次，判断是否需要执行过滤
    """
    
    timing = edge.timing
    first_fire = timing_state.first_fire_ts  # None 表示还没触发过

    # 第 1 关：begin 条件（还没开始吗？）
    if first_fire is None:
        if not _check_begin(timing.begin, now_ts):
            return False

    # 第 2 关：end 条件（已经结束了吗？）
    if not _check_end(timing.end, first_fire, timing_state.exec_count, now_ts):
        return False

    # 第 3 关：interval 间隔（距离上次够不够？）
    if timing.interval_sec > 0 and timing_state.last_fire_ts is not None:
        if now_ts - timing_state.last_fire_ts < timing.interval_sec:
            return False

    # 第 4 关：持续时间型条件（duration guard）
    if timing.get('duration_mode') and timing_state.duration_start is None:
        pass  # 需要持续满足条件才触发，这里只做 gate，实际 duration 在 filter 后判断

    return True


def _check_begin(begin_config, now_ts):
    """检查 begin 条件：立即 / 开盘后 N 秒 / 指定时间 / 延迟 N 秒。"""
    if begin_config is None or begin_config.type == 'immediate':
        return True
    if begin_config.type == 'after_open':
        open_ts = today_market_open_ts(now_ts)
        return now_ts >= open_ts + begin_config.offset_sec
    if begin_config.type == 'at_time':
        target_ts = today_hms_ts(now_ts, begin_config.hms)
        return now_ts >= target_ts
    if begin_config.type == 'delay':
        return now_ts - pool_start_ts >= begin_config.delay_sec
    return True


def _check_end(end_config, first_fire_ts, exec_count, now_ts):
    """检查 end 条件：永不 / 指定时间 / 次数限制 / 持续时间。"""
    if end_config is None or end_config.type == 'never':
        return True
    if end_config.type == 'at_time':
        target_ts = today_hms_ts(now_ts, end_config.hms)
        return now_ts < target_ts
    if end_config.type == 'count':
        if end_config.count > 0 and exec_count >= end_config.count:
            return False
    if end_config.type == 'duration':
        if first_fire_ts is not None:
            if now_ts - first_fire_ts > end_config.duration_sec:
                return False
    return True
```

**代码验证：** 现有 `engine.py:1757 _should_trigger_edge` 做的是类似的事情——replay 模式下检查 begin/end/interval，live 模式下检查 starttype + duration。伪代码把它整理成清晰的四道关。

---

### 6.3 函数 2：run_filter(edge, source_stocks, market_data) → Set[code]

**作用：** 执行过滤，返回通过筛选的股票代码集合。

```python
def run_filter(edge, source_stocks, market_data):
    """执行过滤，返回通过的股票代码集合。
    
    【输入参数】
      edge: dict - 边配置（含 filter 字段：type/strategy/formula_id/params）
      source_stocks: list[dict] - 源节点的股票列表（每只股票是 dict，含 code 字段）
      market_data: dict - 行情数据（唯一真相源）
    
    【返回值】
      Set[str] - 通过过滤的股票代码集合
    
    【副作用】
      无（纯函数，只读；缓存由底层管理）
    
    【调用时机】
      边触发后，调用 propagate_stocks 之前调用
      调用前提：should_trigger() 返回 True，且 src_changed 或 data_changed 为 True
    
    三种过滤模式：
    1. unconditional：全部通过（无条件流转）
    2. conditional：条件过滤（nset_dispatch，策略函数）
    3. formula_eval：公式过滤（公式批量求值）
    """
    
    filter_type = edge.filter.type
    source_codes = {s['code'] for s in source_stocks}

    # 模式 1：无条件（全部通过）
    if filter_type == 'unconditional':
        return source_codes

    # 空源早退（emptyps=0 时空源不执行）
    if not source_codes and edge.filter.get('empty_pass', 0) != 1:
        return set()

    # 模式 2：条件过滤（策略函数）
    if filter_type == 'conditional':
        strategy = edge.filter.strategy
        params = edge.filter.params
        return _run_strategy_filter(strategy, params, source_stocks, market_data)

    # 模式 3：公式过滤（公式批量求值）
    if filter_type == 'formula_eval':
        formula_id = edge.filter.formula_id
        formula_params = edge.filter.params
        period = edge.filter.period or 'day'
        return _run_formula_filter(formula_id, formula_params, source_codes, period, market_data)

    # 未知类型，默认全部通过（安全兜底）
    logger.warning("未知 filter_type=%s，默认全部通过", filter_type)
    return source_codes


def _run_strategy_filter(strategy, params, source_stocks, market_data):
    """执行策略函数过滤（nset_dispatch 模式）。"""
    strategy_fn = get_strategy_function(strategy)
    if strategy_fn is None:
        logger.warning("策略 %s 不存在，返回空", strategy)
        return set()
    
    ctx = {
        'source_stocks': source_stocks,
        'market_data': market_data,
        'params': params,
    }
    result = strategy_fn(ctx)
    
    return {s['code'] for s in result if isinstance(s, dict) and s.get('code')}


def _run_formula_filter(formula_id, formula_params, codes, period, market_data):
    """执行公式批量求值过滤。"""
    formula = formula_lib.get(formula_id)
    if formula is None:
        logger.warning("公式 %s 不存在，返回空", formula_id)
        return set()
    
    results = formula_engine.eval_batch(formula, codes, period, market_data, formula_params)
    
    return {code for code, result in results.items() if bool(result)}
```

**代码验证：** 现有 `engine.py:2459 _apply_edge_filter` 做的是类似的事情——按 filter_type 分派到 `_filter_unconditional`、`_filter_conditional`、`_filter_formula_eval`。伪代码把它简化成清晰的三种模式，去掉缓存、池更新等副作用，只关注"过滤"这件事。

---

### 6.4 函数 3：propagate_stocks(edge, passed_codes, node_stocks, node_ttl_state) → {entered, exited}

**作用：** 把通过过滤的股票从源节点流转到目标节点，返回变更明细。

**v2.4 修复：三种模式分开处理，每种模式的 entered/exited 计算逻辑不同。**

```python
def propagate_stocks(edge, passed_codes, node_stocks, node_ttl_state):
    """股票流转：从源节点到目标节点，返回变更明细。
    
    【输入参数】
      edge: dict - 边配置（含 from_id/to_id/transfer_mode 字段）
          - transfer_mode: str - 流转模式：'copy' / 'move' / 'overwrite'
      passed_codes: Set[str] - 通过过滤的股票代码集合（run_filter 的返回值）
      node_stocks: dict - 节点股票表（原地修改）
          键: node_id (str)
          值: list[dict] - 该节点的股票列表
      node_ttl_state: dict - 节点 TTL 状态表（原地修改）
          键: (node_id, code) 元组
          值: {entry_ts, ttl_sec, remaining_sec}
    
    【返回值】
      dict - 变更明细：
          - entered: list[dict] - 新进入目标节点的股票列表
          - exited: list[dict] - 离开源节点（或目标节点，overwrite 模式）的股票列表
    
    【副作用】
      - 修改 node_stocks[edge.from_id]：move 模式下移除通过的股票
      - 修改 node_stocks[edge.to_id]：新增或替换股票
      - 修改 node_ttl_state：入池股票初始化 TTL，出池股票清理 TTL
    
    【调用时机】
      run_filter 之后调用，用于实际执行股票流转
      调用前提：passed_codes 是 run_filter 的返回值
    
    三种流转模式：
    1. copy：目标 = 目标 ∪ 通过的股票。entered = 通过的 - 原来的目标，exited = ∅
    2. move：目标 = 目标 ∪ 通过的股票，源 = 源 - 通过的股票。
       entered = 通过的 - 原来的目标，exited = 通过的（从源出去）
    3. overwrite：目标 = 通过的股票（完全替换）。
       entered = 通过的 - 原来的目标，exited = 原来的目标 - 通过的
    """
    
    from_id = edge.from_id
    to_id = edge.to_id
    mode = edge.transfer_mode or 'copy'  # copy / move / overwrite

    src_list = node_stocks.get(from_id, [])
    tgt_list = node_stocks.get(to_id, [])
    tgt_old_codes = {s['code'] for s in tgt_list}

    # 从源节点取出通过过滤的股票对象
    passed_stocks = [s for s in src_list if s['code'] in passed_codes]
    passed_codes_set = {s['code'] for s in passed_stocks}

    # =====================================================
    # 模式 1：copy（源不动，目标新增）
    #   目标 = 目标 ∪ 通过的
    #   entered = 通过的 - 原来的目标
    #   exited = ∅
    # =====================================================
    if mode == 'copy':
        # 计算入池的股票（目标节点原来没有的）
        entered = [s for s in passed_stocks if s['code'] not in tgt_old_codes]
        entered_codes = {s['code'] for s in entered}
        exited = []
        exited_codes = set()

        # 目标节点新增
        for s in entered:
            tgt_list.append(copy.deepcopy(s))
        node_stocks[to_id] = tgt_list

        # 源节点不动

    # =====================================================
    # 模式 2：move（源移除，目标新增）
    #   目标 = 目标 ∪ 通过的
    #   源 = 源 - 通过的
    #   entered = 通过的 - 原来的目标
    #   exited = 通过的（从源出去）
    # =====================================================
    elif mode == 'move':
        # 计算入池的股票（目标节点原来没有的）
        entered = [s for s in passed_stocks if s['code'] not in tgt_old_codes]
        entered_codes = {s['code'] for s in entered}
        # 出池的股票 = 所有通过的（从源出去）
        exited = passed_stocks.copy()
        exited_codes = passed_codes_set

        # 目标节点新增
        for s in entered:
            tgt_list.append(s)  # 移动对象，不拷贝
        node_stocks[to_id] = tgt_list

        # 源节点移除
        new_src = [s for s in src_list if s['code'] not in passed_codes_set]
        node_stocks[from_id] = new_src

    # =====================================================
    # 模式 3：overwrite（目标替换成通过的）
    #   目标 = 通过的
    #   源不动
    #   entered = 通过的 - 原来的目标
    #   exited = 原来的目标 - 通过的
    # =====================================================
    elif mode == 'overwrite':
        # 入池 = 通过的 - 原来在目标里的
        entered = [s for s in passed_stocks if s['code'] not in tgt_old_codes]
        entered_codes = {s['code'] for s in entered}
        # 出池 = 原来在目标里的 - 通过的
        exited = [s for s in tgt_list if s['code'] not in passed_codes_set]
        exited_codes = {s['code'] for s in exited}

        # 替换目标节点
        node_stocks[to_id] = passed_stocks.copy()

        # 源节点不动

    else:
        logger.warning("未知 transfer_mode=%s，默认 copy", mode)
        return {'entered': [], 'exited': []}

    # =====================================================
    # 更新节点 TTL 状态
    # =====================================================
    now_ts = pool_now_ts()
    ttl_config = get_node_ttl_config(to_id)
    
    # 入池的股票：初始化 TTL（目标节点）
    for code in entered_codes:
        if ttl_config and ttl_config.enabled:
            node_ttl_state[(to_id, code)] = {
                'entry_ts': now_ts,
                'ttl_sec': ttl_config.ttl_sec,
                'remaining_sec': ttl_config.ttl_sec,
            }

    # 出池的股票：清理 TTL
    if mode == 'move':
        # move 模式：从源节点清理
        for code in exited_codes:
            key = (from_id, code)
            if key in node_ttl_state:
                del node_ttl_state[key]
    elif mode == 'overwrite':
        # overwrite 模式：从目标节点清理
        for code in exited_codes:
            key = (to_id, code)
            if key in node_ttl_state:
                del node_ttl_state[key]

    return {
        'entered': entered,
        'exited': exited,
    }
```

**代码验证：** 现有 `engine.py:2476 _filter_unconditional` 和 `_filter_conditional` 里内嵌了流转逻辑（更新 node_stocks、处理 move/copy/overwrite）。伪代码把流转逻辑抽出来变成独立函数，职责更清晰——filter 只管过滤，propagate 只管流转。

**v2.4 修复说明：**
- v2.3 问题：overwrite 模式的 entered 计算逻辑和 copy/move 共用一套，但 overwrite 语义完全不同
- v2.4 修复：三种模式完全分开，每个模式独立计算 entered/exited，逻辑更清晰
- 三种模式的 entered/exited 语义：
  - **copy**：entered=新增到目标的，exited=空
  - **move**：entered=新增到目标的，exited=从源出去的
  - **overwrite**：entered=新增到目标的，exited=从目标被替换掉的

---

### 6.5 函数 4：check_alerts(scope_obj, scope_type, changes, trackers, alert_state, events, compiled, periodic=False)

**作用：** 检查绑定到某个作用域对象（节点/边/池）的告警规则，满足条件就发告警。

**v2.4 修复：告警分股票级和统计级两类，分别处理。**

```python
def check_alerts(scope_obj, scope_type, changes, trackers, alert_state, events, compiled, periodic=False):
    """检查绑定到 scope_obj 的告警规则，满足条件就发告警。
    
    【输入参数】
      scope_obj: dict - 作用域对象（node / edge / pool），有 alert_rule_ids 字段
      scope_type: str - 作用域类型：'node' / 'edge' / 'pool'
      changes: dict - 变更信息（不同作用域、不同告警类型含义不同）
          - entered: list[dict] - 入池股票列表（股票级用）
          - exited: list[dict] - 出池股票列表（股票级用）
          - all_stocks: list[dict] - 当前所有股票（统计级用）
          - all_nodes: dict - 所有节点（池子级统计用）
          - transferred_count: int - 流转数量（边级统计用）
          - pass_rate: float - 通过率（边级统计用）
      trackers: dict - 持仓跟踪表
      alert_state: dict - 告警状态（原地修改）
          - cooldowns: dict - 冷却记录
              键: (rule_id, code) 或 (rule_id, scope_id)
              值: 冷却截止时间戳
          - sent_alerts: list - 历史告警列表
      events: list - 事件队列（原地追加告警事件）
      compiled: dict - 编译后的池子（含预编译的告警规则 bound_alert_rules）
      periodic: bool - 是否周期检查（默认 False）
          - True: 检查所有类型的告警（股票级遍历所有股票 + 统计级）
          - False: 只检查事件驱动的告警（股票级只检查变化的股票）
    
    【返回值】
      int - 本次触发的告警数量
    
    【副作用】
      - 修改 alert_state.cooldowns：更新冷却时间
      - 修改 alert_state.sent_alerts：追加历史告警
      - 修改 events：追加告警事件
    
    【调用时机】
      - 边执行后：检查边级告警、目标节点入池告警、源节点出池告警
      - TTL 淘汰后：检查节点出池告警
      - 每个 tick 末：周期检查所有节点和池子的告警（periodic=True）
    """
    
    scope_id = scope_obj['id']
    alert_rule_ids = scope_obj.get('alert_rule_ids', [])
    
    now_ts = pool_now_ts()
    triggered_count = 0

    for rule_id in alert_rule_ids:
        # 从编译结果取预编译的告警规则
        rule = compiled.bound_alert_rules.get(rule_id)
        if rule is None:
            continue

        alert_type = rule.get('alert_type', 'stock_level')

        # =====================================================
        # 类型 1：股票级告警（每只股票独立检查）
        # =====================================================
        if alert_type == 'stock_level':
            triggered_count += _check_stock_level_alerts(
                rule, scope_id, scope_type, changes, trackers,
                alert_state, events, now_ts, periodic
            )

        # =====================================================
        # 类型 2：统计级告警（基于整体统计检查）
        # =====================================================
        elif alert_type == 'stats_level':
            triggered_count += _check_stats_level_alerts(
                rule, scope_id, scope_type, changes, trackers,
                alert_state, events, now_ts
            )

    return triggered_count


def _check_stock_level_alerts(rule, scope_id, scope_type, changes, trackers,
                              alert_state, events, now_ts, periodic):
    """检查股票级告警：遍历股票，每只独立检查。
    
    【什么时候检查哪些股票】
      - 事件驱动（periodic=False）：只检查 changes.entered + changes.exited 中的股票
      - 周期检查（periodic=True）：检查 changes.all_stocks 中的所有股票
    """
    rule_id = rule['rule_id']
    triggered = 0

    # 确定要检查的股票列表
    if periodic:
        stocks_to_check = changes.get('all_stocks', [])
    else:
        entered = changes.get('entered', [])
        exited = changes.get('exited', [])
        stocks_to_check = entered + exited

    for stock in stocks_to_check:
        code = stock['code']
        
        # 冷却检查（按股票维度）
        cooldown_key = (rule_id, code)
        if cooldown_key in alert_state.cooldowns:
            if now_ts < alert_state.cooldowns[cooldown_key]:
                continue  # 还在冷却中，跳过
        
        # 构造告警上下文
        ctx = _build_stock_alert_context(scope_type, scope_id, stock, trackers, changes)
        
        # 执行条件判断
        condition_met = _eval_alert_condition(rule, ctx)
        
        if condition_met:
            # 发告警
            alert_event = {
                'type': 'alert',
                'rule_id': rule_id,
                'rule_name': rule['name'],
                'severity': rule['severity'],
                'code': code,
                'scope_type': scope_type,
                'scope_id': scope_id,
                'alert_type': 'stock_level',
                'timestamp': now_ts,
                'message': _render_alert_message(rule, ctx),
                'detail': ctx,
            }
            events.append(alert_event)
            
            # 更新冷却
            if rule.get('cooldown_sec', 0) > 0:
                alert_state.cooldowns[cooldown_key] = now_ts + rule['cooldown_sec']
            
            # 记录历史
            alert_state.sent_alerts.append(alert_event)
            triggered += 1

    return triggered


def _check_stats_level_alerts(rule, scope_id, scope_type, changes, trackers,
                              alert_state, events, now_ts):
    """检查统计级告警：计算整体统计量，检查一次。
    
    【统计量来源】
      - node 作用域：从 changes.all_stocks 计算（股票数、总市值、平均收益等）
      - edge 作用域：从 changes.transferred_count / changes.pass_rate 等获取
      - pool 作用域：从 changes.all_nodes 计算（总股票数、总市值等）
    """
    rule_id = rule['rule_id']
    triggered = 0

    # 冷却检查（按作用域维度，统计级不需要按股票去重）
    cooldown_key = (rule_id, scope_id)
    if cooldown_key in alert_state.cooldowns:
        if now_ts < alert_state.cooldowns[cooldown_key]:
            return 0  # 还在冷却中，跳过
    
    # 构造统计上下文
    ctx = _build_stats_alert_context(scope_type, scope_id, changes, trackers)
    
    # 执行条件判断
    condition_met = _eval_alert_condition(rule, ctx)
    
    if condition_met:
        # 发告警
        alert_event = {
            'type': 'alert',
            'rule_id': rule_id,
            'rule_name': rule['name'],
            'severity': rule['severity'],
            'code': None,  # 统计级告警没有具体股票
            'scope_type': scope_type,
            'scope_id': scope_id,
            'alert_type': 'stats_level',
            'timestamp': now_ts,
            'message': _render_alert_message(rule, ctx),
            'detail': ctx,
        }
        events.append(alert_event)
        
        # 更新冷却
        if rule.get('cooldown_sec', 0) > 0:
            alert_state.cooldowns[cooldown_key] = now_ts + rule['cooldown_sec']
        
        # 记录历史
        alert_state.sent_alerts.append(alert_event)
        triggered = 1

    return triggered


def _build_stock_alert_context(scope_type, scope_id, stock, trackers, changes):
    """构造股票级告警的求值上下文。"""
    code = stock['code']
    tracker = trackers.get(code, {})
    return {
        'code': code,
        'stock': stock,
        'tracker': tracker,
        'scope_type': scope_type,
        'scope_id': scope_id,
        'changes': changes,
    }


def _build_stats_alert_context(scope_type, scope_id, changes, trackers):
    """构造统计级告警的求值上下文。
    
    计算各种统计量：
    - stock_count: 股票数量
    - total_mv: 总市值
    - avg_profit_pct: 平均收益率
    - max_drawdown: 最大回撤
    - transferred_count: 流转数量（边级）
    - pass_rate: 通过率（边级）
    """
    ctx = {
        'scope_type': scope_type,
        'scope_id': scope_id,
    }

    if scope_type == 'node':
        all_stocks = changes.get('all_stocks', [])
        stock_count = len(all_stocks)
        total_mv = 0.0
        total_profit = 0.0
        profit_count = 0
        for s in all_stocks:
            code = s['code']
            tracker = trackers.get(code, {})
            total_mv += tracker.get('market_value', 0)
            if 'profit_pct' in tracker:
                total_profit += tracker['profit_pct']
                profit_count += 1
        ctx['stock_count'] = stock_count
        ctx['total_mv'] = total_mv
        ctx['avg_profit_pct'] = total_profit / profit_count if profit_count > 0 else 0.0

    elif scope_type == 'edge':
        ctx['transferred_count'] = changes.get('transferred_count', 0)
        ctx['pass_rate'] = changes.get('pass_rate', 0.0)
        ctx['entered_count'] = len(changes.get('entered', []))
        ctx['exited_count'] = len(changes.get('exited', []))

    elif scope_type == 'pool':
        all_nodes = changes.get('all_nodes', {})
        total_stocks = 0
        total_mv = 0.0
        for nid, stocks in all_nodes.items():
            total_stocks += len(stocks)
            for s in stocks:
                code = s['code']
                tracker = trackers.get(code, {})
                total_mv += tracker.get('market_value', 0)
        ctx['total_stocks'] = total_stocks
        ctx['total_mv'] = total_mv

    return ctx


def _eval_alert_condition(rule, ctx):
    """执行告警条件判断（用预编译的表达式）。"""
    compiled_expr = rule.get('_compiled_condition')
    if compiled_expr is None:
        # 没有条件，默认触发
        return True
    try:
        return bool(eval(compiled_expr, {'__builtins__': {}}, ctx))
    except Exception as e:
        logger.debug("告警条件计算失败 rule=%s: %s", rule['rule_id'], e)
        return False
```

**v2.4 修复说明：**
- v2.3 问题：边级/池级告警是统计性的（比如"节点股票数>100"），但函数内部按股票循环检查——削足适履
- v2.4 修复：告警分两类
  - **股票级告警**（stock_level）：每只股票独立触发，遍历股票检查
  - **统计级告警**（stats_level）：基于节点/池子整体统计，计算一次统计量检查一次
- 冷却机制也不同：
  - 股票级：按 (rule_id, code) 冷却
  - 统计级：按 (rule_id, scope_id) 冷却
- 作用域 × 告警类型 矩阵全部覆盖

---

### 6.6 函数 5：generate_signals(scope_obj, scope_type, trigger_event, changes, trackers, signal_rules, signal_state, events) → int

**作用：** 根据信号规则生成交易信号，带去重机制。

**v2.4 新增：信号管理从黑盒展开为伪代码。**

```python
def generate_signals(scope_obj, scope_type, trigger_event, changes, trackers,
                     signal_rules, signal_state, events):
    """根据信号规则生成交易信号，带去重机制。
    
    【输入参数】
      scope_obj: dict - 作用域对象（node / edge / pool），有 signal_rule_ids 字段
      scope_type: str - 作用域类型：'node' / 'edge' / 'pool'
      trigger_event: str - 触发事件类型：'enter' / 'exit' / 'periodic'
      changes: dict - 变更信息
          - entered: list[dict] - 入池股票列表（enter 事件）
          - exited: list[dict] - 出池股票列表（exit 事件）
          - all_stocks: list[dict] - 所有股票（periodic 事件）
      trackers: dict - 持仓跟踪表
      signal_rules: dict - 预编译的信号规则字典（compiled.bound_signal_rules）
      signal_state: dict - 信号状态（原地修改）
          - dedup_keys: dict - 去重记录
              键: dedup_key（字符串，如 "{code}_{signal_type}"）
              值: 最后一次触发的时间戳
          - signal_queue: list - 待消费的信号队列
          - signal_history: list - 历史信号列表
      events: list - 事件队列（原地追加信号事件）
    
    【返回值】
      int - 本次生成的信号数量
    
    【副作用】
      - 修改 signal_state.dedup_keys：更新去重记录
      - 修改 signal_state.signal_queue：追加待消费的信号
      - 修改 signal_state.signal_history：追加历史信号
      - 修改 events：追加信号事件
    
    【调用时机】
      - 股票入池后（trigger_event='enter'）
      - 股票出池后（trigger_event='exit'，包括 move/TTL/overwrite）
      - 周期检查时（trigger_event='periodic'）
    """
    
    scope_id = scope_obj['id']
    signal_rule_ids = scope_obj.get('signal_rule_ids', [])
    
    now_ts = pool_now_ts()
    generated_count = 0

    for rule_id in signal_rule_ids:
        # 从编译结果取预编译的信号规则
        rule = signal_rules.get(rule_id)
        if rule is None:
            continue

        # 触发事件不匹配，跳过
        if rule.get('trigger_event') != trigger_event:
            continue

        # 确定要检查的股票列表
        if trigger_event == 'enter':
            stocks_to_check = changes.get('entered', [])
        elif trigger_event == 'exit':
            stocks_to_check = changes.get('exited', [])
        elif trigger_event == 'periodic':
            stocks_to_check = changes.get('all_stocks', [])
        else:
            continue

        for stock in stocks_to_check:
            code = stock['code']
            
            # 构造信号上下文
            ctx = _build_signal_context(scope_type, scope_id, stock, trackers, trigger_event, changes)
            
            # 条件判断
            if not _eval_signal_condition(rule, ctx):
                continue
            
            # 去重检查
            dedup_key = _render_dedup_key(rule, ctx)
            dedup_window = rule.get('dedup_window_sec', 3600)
            
            if dedup_key in signal_state.dedup_keys:
                last_ts = signal_state.dedup_keys[dedup_key]
                if now_ts - last_ts < dedup_window:
                    continue  # 还在去重窗口内，跳过
            
            # 生成信号
            signal = {
                'signal_id': f"{rule_id}_{code}_{int(now_ts)}",
                'rule_id': rule_id,
                'signal_type': rule['signal_type'],  # BUY / SELL / CUSTOM
                'code': code,
                'scope_type': scope_type,
                'scope_id': scope_id,
                'trigger_event': trigger_event,
                'timestamp': now_ts,
                'price': _resolve_signal_price(rule, ctx),
                'priority': rule.get('priority', 10),
                'fields': _resolve_signal_fields(rule, ctx),
                'detail': ctx,
            }
            
            # 加入信号队列
            signal_state.signal_queue.append(signal)
            signal_state.signal_history.append(signal)
            
            # 更新去重记录
            signal_state.dedup_keys[dedup_key] = now_ts
            
            # 同时发事件
            events.append({
                'type': 'signal',
                'signal_type': signal['signal_type'],
                'code': code,
                'scope_id': scope_id,
                'timestamp': now_ts,
                'detail': signal,
            })
            
            generated_count += 1
            
            # stop_on_match：命中后不再检查后续规则
            if rule.get('stop_on_match', False):
                break

    return generated_count


def consume_signals(signal_state, limit=None):
    """消费信号队列，取出待消费的信号。
    
    【输入参数】
      signal_state: dict - 信号状态（原地修改）
          - signal_queue: list - 待消费的信号队列
      limit: int|None - 最大取出数量，None 表示全部取出
    
    【返回值】
      list[dict] - 取出的信号列表（按优先级排序，高优先级在前）
    
    【副作用】
      - 修改 signal_state.signal_queue：移除已取出的信号
    
    【调用时机】
      外部消费者（交易系统、策略引擎）定期调用，取出待处理的信号
    """
    
    queue = signal_state.signal_queue
    
    if not queue:
        return []
    
    # 按优先级排序（高优先级在前）
    queue.sort(key=lambda s: s.get('priority', 10), reverse=True)
    
    if limit is not None and limit > 0:
        result = queue[:limit]
        signal_state.signal_queue = queue[limit:]
    else:
        result = queue.copy()
        signal_state.signal_queue = []
    
    return result


def _build_signal_context(scope_type, scope_id, stock, trackers, trigger_event, changes):
    """构造信号求值上下文。"""
    code = stock['code']
    tracker = trackers.get(code, {})
    return {
        'code': code,
        'stock': stock,
        'tracker': tracker,
        'scope_type': scope_type,
        'scope_id': scope_id,
        'trigger_event': trigger_event,
        'changes': changes,
    }


def _eval_signal_condition(rule, ctx):
    """执行信号条件判断（用预编译的表达式）。"""
    compiled_expr = rule.get('_compiled_condition')
    if compiled_expr is None:
        # 没有条件，默认触发
        return True
    try:
        return bool(eval(compiled_expr, {'__builtins__': {}}, ctx))
    except Exception as e:
        logger.debug("信号条件计算失败 rule=%s: %s", rule['rule_id'], e)
        return False


def _render_dedup_key(rule, ctx):
    """渲染去重键（用上下文变量替换模板）。"""
    template = rule.get('dedup_key', '{code}_{signal_type}')
    result = template
    # 简单的变量替换（支持 code / signal_type / scope_id 等）
    result = result.replace('{code}', ctx.get('code', ''))
    result = result.replace('{signal_type}', rule.get('signal_type', ''))
    result = result.replace('{scope_id}', ctx.get('scope_id', ''))
    return result


def _resolve_signal_price(rule, ctx):
    """解析信号价格（从 field_mapping 或 tracker 中取）。"""
    field_mapping = rule.get('field_mapping', {})
    price_path = field_mapping.get('price', 'tracker.current_price')
    return _resolve_path(ctx, price_path)


def _resolve_signal_fields(rule, ctx):
    """解析信号附加字段（从 field_mapping 中取）。"""
    field_mapping = rule.get('field_mapping', {})
    fields = {}
    for field_name, path in field_mapping.items():
        if field_name == 'price':
            continue  # price 单独处理
        fields[field_name] = _resolve_path(ctx, path)
    return fields


def _resolve_path(ctx, path):
    """点号路径取值：'tracker.current_price' → ctx['tracker']['current_price']"""
    keys = path.split('.')
    cur = ctx
    for k in keys:
        if isinstance(cur, dict):
            cur = cur.get(k)
        else:
            return None
        if cur is None:
            return None
    return cur
```

**信号类型说明：**

| 信号类型 | 触发时机 | 典型用途 |
|---------|---------|---------|
| **BUY** | 股票入池时（enter 事件） | 买入信号 |
| **SELL** | 股票出池时（exit 事件，move/TTL/overwrite） | 卖出信号 |
| **CUSTOM** | 用户配置的特殊触发条件 | 自定义信号 |

**信号去重机制：**
- 同一股票同一类型信号，在去重窗口内不重复发
- 去重键由 dedup_key 模板渲染（如 `{code}_{signal_type}`）
- 去重窗口由 dedup_window_sec 配置（默认 3600 秒）

**代码验证：** 现有 `engine.py:3081 _push_signal` 和 `engine.py:3254 _should_emit_signal_for_domain` 做的是类似的事情——信号生成和触发判断。伪代码把它整理成清晰的 generate_signals + consume_signals 两个函数，职责更清晰。

---

## 七、编译期更新（v2.4 更新：alert_type 纳入编译期）

### 7.1 编译期新增职责

| # | 工作项 | 说明 |
|---|--------|------|
| 1 | **拓扑排序验证** | 检查有没有环，计算每个节点的深度 |
| 2 | **执行顺序验证** | 用户指定的 exec_order 是否违背拓扑依赖 |
| 3 | **公式语法检查** | 所有引用的 formula_id 在 formula_lib 中存在吗？公式能正常编译吗？ |
| 4 | **触发器类型检查** | 边的 timing 配置合法吗？ |
| 5 | **节点类型验证** | 节点类型合法吗？参数完整吗？ |
| 6 | **边上下文预计算** | 预计算每条边的源节点、目标节点、filter_type 等 |
| 7 | **⚠️ 告警规则绑定与预编译** | 把节点/边/池绑定的 alert_rule_ids 解析出来，预编译条件表达式，**验证 alert_type 与 scope_type 匹配** |
| 8 | **⚠️ 信号规则绑定与预编译** | 把节点/边/池绑定的 signal_rule_ids 解析出来，预编译条件表达式 |
| 9 | **TTL 配置提取** | 每个节点的 TTL 配置提取出来 |

### 7.2 编译产物结构（v2.4 更新：alert_type 预计算）

```python
CompiledPool = {
    # 基本结构
    "pool_id": "pool_001",
    "nodes": {nid: node_dict},
    "edges": {eid: edge_dict},
    "node_ttl_config": {nid: ttl_config},

    # 执行顺序
    "exec_order": [eid1, eid2, ...],
    "topo_order": [nid1, nid2, ...],
    "node_depths": {nid: depth},

    # 预编译的公式
    "compiled_formulas": {
        formula_id: compiled_expression
    },

    # 预计算的边上下文
    "edge_ctx": {
        eid: {
            "from_node": node_dict,
            "to_node": node_dict,
            "filter_type": "conditional",
            "timing_spec": {...},
            "ttl_spec": {...}
        }
    },

    # ⚠️ v2.4 更新：绑定的告警规则（预编译，含 alert_type）
    "bound_alert_rules": {
        rule_id: {
            ...rule_config,
            "_compiled_condition": compiled_expression,  # 预编译的条件
            "_scope_type": "node",  # 规则本身的 scope_type
            "_alert_type": "stock_level",  # v2.4 新增：告警类型
        }
    },

    # 绑定的信号规则（预编译）
    "bound_signal_rules": {
        rule_id: {
            ...rule_config,
            "_compiled_condition": compiled_expression,
            "_scope_type": "node",
        }
    },

    # 邻接表
    "out_edges": {nid: [eid, ...]},
    "in_edges": {nid: [eid, ...]}
}
```

### 7.3 绑定过程伪代码（v2.4 更新：alert_type 校验）

```python
def _bind_alert_rules(pool_config, alert_rules_lib):
    """编译期：把告警规则绑定到节点/边/池上，并预编译条件表达式。
    
    v2.4 更新：校验 alert_type 与 scope_type 的组合是否合法。
    """
    
    bound = {}
    
    # 收集所有引用的 rule_id
    all_rule_ids = set()
    
    # 池子级
    for rid in pool_config.get('alert_rule_ids', []):
        all_rule_ids.add(rid)
    
    # 节点级
    for node in pool_config.get('nodes', []):
        for rid in node.get('alert_rule_ids', []):
            all_rule_ids.add(rid)
    
    # 边级
    for edge in pool_config.get('edges', []):
        for rid in edge.get('alert_rule_ids', []):
            all_rule_ids.add(rid)
    
    # 预编译每个引用的规则
    for rule_id in all_rule_ids:
        rule = alert_rules_lib.get(rule_id)
        if rule is None:
            logger.warning("告警规则 %s 不存在", rule_id)
            continue
        
        # v2.4 新增：校验 alert_type 与 scope_type 的组合
        scope_type = rule.get('scope_type', 'node')
        alert_type = rule.get('alert_type', 'stock_level')
        if not _is_valid_alert_combination(scope_type, alert_type):
            logger.warning("告警规则 %s 类型组合非法: scope=%s, alert_type=%s",
                         rule_id, scope_type, alert_type)
            continue
        
        # 预编译条件表达式
        compiled_rule = dict(rule)
        condition = rule.get('condition')
        if condition:
            try:
                compiled_rule['_compiled_condition'] = compile(condition, f'<alert_{rule_id}>', 'eval')
            except SyntaxError as e:
                logger.warning("告警规则 %s 条件编译失败: %s", rule_id, e)
                compiled_rule['_compiled_condition'] = None
        else:
            compiled_rule['_compiled_condition'] = None
        
        compiled_rule['_scope_type'] = scope_type
        compiled_rule['_alert_type'] = alert_type
        
        bound[rule_id] = compiled_rule
    
    return bound


def _is_valid_alert_combination(scope_type, alert_type):
    """校验作用域 × 告警类型的组合是否合法。
    
    合法组合：
    - node × stock_level ✓
    - node × stats_level ✓
    - edge × stock_level ✓
    - edge × stats_level ✓
    - pool × stock_level ✓（很少用）
    - pool × stats_level ✓
    """
    valid_scopes = {'node', 'edge', 'pool'}
    valid_types = {'stock_level', 'stats_level'}
    return scope_type in valid_scopes and alert_type in valid_types
```

信号规则的绑定过程类似，略。

---

## 八、重构路径细化（同 v2.3，略）

---

## 九、时间和触发的处理（同 v2.3，略）

---

## 十、告警与信号的作用域模型（v2.4 更新：alert_type 纳入）

### 10.1 作用域模型总览

告警规则和信号规则都不是全局的，而是有作用域的。它们定义在 `alert_rules` / `signal_rules` 表里，然后在 `pool_config` 的节点/边上通过 `alert_rule_ids` / `signal_rule_ids` 绑定。

**告警规则还有 alert_type 维度，构成作用域 × 类型 矩阵。**

### 10.2 告警：作用域 × 类型 矩阵（v2.4 新增）

| 作用域 | 股票级告警（stock_level） | 统计级告警（stats_level） |
|-------|-------------------------|-------------------------|
| **node** | 入池/出池/持仓变化告警<br>例：止盈告警、回撤告警 | 节点股票数/市值/收益率告警<br>例：股票数>100、平均收益>5% |
| **edge** | 边通过/拒绝告警<br>例：某股票通过筛选 | 边通过率/过滤效率告警<br>例：通过率<10%、流转数>50 |
| **pool** | 全池子股票告警（很少用） | 池子整体指标告警<br>例：池子空了、全池亏损>20% |

### 10.3 信号规则的作用域

| 作用域 | 触发时机 | 绑定位置 | 典型例子 |
|-------|---------|---------|---------|
| **node（节点级）** | 股票入池（enter）/ 出池（exit） | node.signal_rule_ids | 入池 BUY、出池 SELL |
| **edge（边级）** | 边执行流转时 | edge.signal_rule_ids | 特殊边触发特殊信号 |
| **pool（池子级）** | 周期检查时 | pool_config.signal_rule_ids | 全池调仓信号 |

### 10.4 表结构变更（v2.4 更新）

#### alert_rules 表加 alert_type 字段

```json
// 之前（v2.3）
{
  "rule_id": "profit_threshold",
  "name": "止盈告警",
  "scope_type": "node",
  ...
}

// 现在（v2.4）
{
  "rule_id": "profit_threshold",
  "name": "止盈告警",
  "scope_type": "node",
  "alert_type": "stock_level",  // v2.4 新增：stock_level / stats_level
  ...
}
```

#### signal_rules 表保持 scope_type 字段（同 v2.3）

略。

#### pool_config 加绑定字段（同 v2.3）

略。

---

## 十一、一句话总结

> **股票池就是几个篮子用管子连起来，管子上有筛子和定时器，定时看看有没有新数据，有就筛一下，股票从上游流到下游；同时记一下每只股票什么时候进来的、赚了多少、还能待多久，筛出来了就通知一下（告警分股票级和统计级两类），该买卖了就发个信号（带去重和队列）。**

**v2.4 进步在哪里？**
- ✅ **修复 check_alerts 设计缺陷**：告警分股票级和统计级两类，分别处理，不再削足适履
- ✅ **修复 propagate_stocks 设计缺陷**：三种模式（copy/move/overwrite）完全分开，每种模式独立计算 entered/exited
- ✅ **信号管理从黑盒变伪代码**：generate_signals + consume_signals，带信号类型、去重机制、信号队列
- ✅ **所有伪代码补全依赖契约**：每个函数都有输入参数、返回值、副作用、调用时机，开发拿着就能写
- ✅ **alert_rules 表加 alert_type 字段**：stock_level / stats_level，作用域 × 类型矩阵全覆盖
- ✅ **核心循环伪代码更新**：信号管理纳入主循环，告警检查调用方式更新

从"伪代码可直接写代码"到"伪代码带着契约写，开发不用猜"，冲击 80 分的"可指导重构"及格线。
