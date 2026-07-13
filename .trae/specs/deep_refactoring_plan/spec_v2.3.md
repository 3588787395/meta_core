# 股票池架构设计 v2.3

> 版本主题：冲刺80分——黑盒展开 + 作用域澄清 + 时间统一 + 第一步细化
> 设计原则：从"骨架搭对、肉长全"到"伪代码可直接写代码"，冲击 80 分的"可指导重构"及格线
> 上一版 v2.2 问题：核心函数是黑盒（should_trigger、run_filter、propagate_stocks、check_alerts），告警规则作用域矛盾，now() 混用，重构第一步太虚
> 目标：4 个核心函数伪代码各 10-20 行、告警/信号有作用域模型、时间源统一为 pool_now() + data_ts()、重构第一步具体到文件级

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
| 6 | **告警 (Alert)** | 满足特定条件时发出的通知，有作用域、有冷却机制 | `alert_rules + alert_state` | 行为 |
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
| 3 | **alert_rules** | 告警规则：什么条件下发什么告警，有 scope 字段 | `alert_rules.json` |
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
      "alert_rule_ids": ["profit_threshold", "drawdown_threshold"],
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
      "alert_rule_ids": ["edge_triggered"]
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

**干什么的：** 定义什么条件下触发什么告警，包括作用域、告警级别、冷却时间。

**关键字段（v2.3 更新：加 scope 字段）：**

```json
{
  "rules": {
    "profit_threshold": {
      "rule_id": "profit_threshold",
      "name": "止盈告警",
      "scope_type": "node",
      "scope_target": null,
      "condition": "tracker.profit_pct >= 10",
      "formula_id": null,
      "severity": "warning",
      "cooldown_sec": 300,
      "message_template": "{code} 达到止盈线，当前收益 {profit_pct}%"
    },
    "drawdown_threshold": {
      "rule_id": "drawdown_threshold",
      "name": "回撤告警",
      "scope_type": "node",
      "scope_target": null,
      "condition": "tracker.max_drawdown <= -5",
      "severity": "critical",
      "cooldown_sec": 300
    },
    "edge_triggered": {
      "rule_id": "edge_triggered",
      "name": "边触发告警",
      "scope_type": "edge",
      "scope_target": null,
      "condition": "transferred_count > 0",
      "severity": "info",
      "cooldown_sec": 60
    },
    "pool_empty": {
      "rule_id": "pool_empty",
      "name": "池子空了",
      "scope_type": "pool",
      "scope_target": null,
      "condition": "total_stocks == 0",
      "severity": "warning",
      "cooldown_sec": 600
    }
  }
}
```

**v2.3 重要更新：作用域模型**

告警规则不是全局的，是有作用域的。`scope_type` 有三种：

| scope_type | 含义 | 什么时候检查 | 绑定到哪里 |
|-----------|------|-------------|-----------|
| `node` | 节点级告警 | 股票入池/出池/周期检查时 | 节点的 alert_rule_ids |
| `edge` | 边级告警 | 边执行完有流转时 | 边的 alert_rule_ids |
| `pool` | 池子级告警 | 每个 tick 末检查 | pool_config.alert_rule_ids |

**编译期绑定：**
- 编译 `pool_config` 时，把每个节点/边绑定的 `alert_rule_ids` 解析出来
- 把告警规则的配置预编译（条件表达式编译）
- 运行时只检查绑定到当前节点/边/池的规则，不需要遍历全局

#### 表 4：signal_rules（交易信号规则）

**干什么的：** 定义什么条件下产生什么交易信号（BUY/SELL），包括触发时机、去重机制。

**关键字段（v2.3 更新：加 scope 字段）：**

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
      "dedup_key": "{code}_{signal_type}_{entry_date}",
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
      "dedup_key": "{code}_{signal_type}_{exit_date}"
    }
  }
}
```

**v2.3 重要更新：信号规则也有作用域**

信号规则同样不是全局的，而是绑定到节点上。

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

同 v2.2，略。

#### 表 6：system_config（系统参数）

同 v2.2，略。

---

## 四、运行时表清单（10 张）

同 v2.2，略。10 张核心业务状态表：
node_stocks、market_data、edge_timing_state、node_ttl_state、trackers、signal_state、alert_state、pool_state、events、pool_errors。

---

## 五、统一时间源模型（v2.3 新增）

### 5.1 问题：now() 混用

现有代码中时间源混乱：
- 有时用 `time.time()`（系统时间戳）
- 有时用 `_dt.now()`（系统 datetime）
- 有时用 `_current_bar_time`（回放 bar 时间）
- 有时用 `_virtual_clock`（仿真虚拟时钟）
- 触发判断用系统时间，数据判断用数据时间，混在一起

### 5.2 解决方案：两个时间源，各司其职

| 时间源函数 | 含义 | 用途 | 底层实现 |
|-----------|------|------|---------|
| `pool_now()` | 池子当前时间（触发时间） | 触发判断、TTL 递减、冷却判断、事件时间戳 | 系统时间 / 虚拟时钟 / bar 时间（按模式切换） |
| `data_ts(code)` | 某只股票的数据时间戳 | 判断数据是否更新、计算指标用的时间 | market_data[code].timestamp |

**命名清晰，绝不混用：**
- 触发时间用 `pool_now()`（"现在该不该触发？"）
- 数据时间用 `data_ts(code)`（"这只股票的数据新不新？"）

### 5.3 pool_now() 的实现

```python
def pool_now(self) -> datetime:
    """返回池子当前时间（用于触发判断、TTL、冷却等）。
    
    按当前运行模式切换：
    - live 模式：系统时间
    - replay 模式：bar 时间
    - simulation 模式：虚拟时钟
    """
    mode_id = self._current_mode_id
    if mode_id == 'replay':
        return self._current_bar_time or _dt.now()
    if mode_id == 'simulation':
        if self._virtual_clock is not None:
            return _dt.fromtimestamp(self._virtual_clock)
        return _dt.now()
    # live 模式（默认）
    return _dt.now()

def pool_now_ts(self) -> float:
    """pool_now() 的时间戳版本（方便比较计算）。"""
    return _safe_timestamp(self.pool_now())
```

### 5.4 data_ts() 的实现

```python
def data_ts(self, code: str) -> float:
    """返回某只股票的最新数据时间戳。
    
    用于判断数据是否更新、计算指标用的数据时间。
    从 market_data 统一读取，不直接读底层存储。
    """
    tick = self._rt['market_data'].get(code)
    if tick is None:
        return 0.0
    return tick.get('timestamp', 0.0)

def is_data_updated(self, code: str, since_ts: float) -> bool:
    """判断某只股票的数据是否比 since_ts 更新。"""
    return self.data_ts(code) > since_ts
```

### 5.5 什么时候用哪个？

| 场景 | 用哪个时间源 | 原因 |
|------|------------|------|
| 边触发判断（begin/end/interval） | `pool_now()` | 触发是"时间到了没"，用池子时间 |
| 节点 TTL 递减 | `pool_now()` | TTL 是"待了多久"，用池子时间 |
| 告警冷却判断 | `pool_now()` | 冷却是"过了多久"，用池子时间 |
| 信号去重窗口 | `pool_now()` | 去重是"多久内重复"，用池子时间 |
| 事件时间戳 | `pool_now()` | 事件发生时间，用池子时间 |
| 判断行情数据是否更新 | `data_ts(code)` | 数据时间是"数据到什么时候"，用数据时间 |
| 计算技术指标 | `data_ts(code)` | 指标基于数据时间，不是系统时间 |
| 过滤条件中的时间函数 | `data_ts(code)` | 过滤用数据的时间，不是系统时间 |

**核心原则：触发用 pool_now()，数据用 data_ts()，两者绝不混用。**

---

## 六、4 个核心黑盒函数伪代码（v2.3 新增）

### 6.1 核心循环（用统一时间源更新）

```python
async def run_pool(pool_config):
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
                maybe_emit_signal(node_id, 'exit', code, trackers, signal_state, compiled, events)

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
                        maybe_emit_signal(edge.to_id, 'enter', s['code'], trackers, signal_state, compiled, events)
                    for s in changes.exited:
                        maybe_emit_signal(edge.from_id, 'exit', s['code'], trackers, signal_state, compiled, events)

                    # 边级告警（绑定到边上的告警规则）
                    check_alerts(edge, changes, trackers, alert_state, events, compiled)
                    # 目标节点级告警（入池）
                    check_alerts(compiled.nodes[edge.to_id], changes.entered, trackers, alert_state, events, compiled)
                    # 源节点级告警（出池）
                    check_alerts(compiled.nodes[edge.from_id], changes.exited, trackers, alert_state, events, compiled)

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

            # 5. 周期告警检查（所有节点的周期告警）
            for node_id, node in compiled.nodes.items():
                check_periodic_alerts(node, node_stocks[node_id], trackers, alert_state, events, compiled)

            pool_state.last_data_ts = new_market_data.latest_ts
            pool_state.first_run = False
        except Exception as e:
            record_error(pool_errors, 'tick_error', 'main_loop', e)

        # 6. 推送事件给前端/消费者
        flush_events(events)
```

### 6.2 函数 1：should_trigger(edge, timing_state, now_ts) → bool

**作用：** 判断一条边是否应该触发（时机门控）。

**输入：**
- `edge`: 边配置（含 timing）
- `timing_state`: 边的时序状态（last_fire_ts, first_fire_ts, exec_count, duration_start）
- `now_ts`: 当前池子时间戳（pool_now_ts()）

**输出：** True = 应该触发，False = 跳过

```python
def should_trigger(edge, timing_state, now_ts):
    """判断一条边是否应该触发（四道关：begin → end → interval → 首次）。"""
    
    timing = edge.timing
    first_fire = timing_state.first_fire_ts  # None 表示还没触发过

    # 第 1 关：begin 条件（还没开始吗？）
    if first_fire is None:
        # 还没触发过，检查 begin 条件
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
        # 需要持续满足条件才触发，这里只做 gate，实际 duration 在 filter 后判断
        pass

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
        # delay 是相对池子启动时间，这里简化
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

**输入：**
- `edge`: 边配置（含 filter）
- `source_stocks`: 源节点的股票列表
- `market_data`: 行情数据（唯一真相源）

**输出：** 通过过滤的股票代码集合 Set[str]

```python
def run_filter(edge, source_stocks, market_data):
    """执行过滤，返回通过的股票代码集合。
    
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
    # 查策略函数，调用之
    strategy_fn = get_strategy_function(strategy)
    if strategy_fn is None:
        logger.warning("策略 %s 不存在，返回空", strategy)
        return set()
    
    # 构造上下文，调用策略函数
    ctx = {
        'source_stocks': source_stocks,
        'market_data': market_data,
        'params': params,
    }
    result = strategy_fn(ctx)
    
    # 策略返回的是通过的股票列表，提取 code
    return {s['code'] for s in result if isinstance(s, dict) and s.get('code')}


def _run_formula_filter(formula_id, formula_params, codes, period, market_data):
    """执行公式批量求值过滤。"""
    # 从公式库取公式
    formula = formula_lib.get(formula_id)
    if formula is None:
        logger.warning("公式 %s 不存在，返回空", formula_id)
        return set()
    
    # 批量求值（公式引擎）
    results = formula_engine.eval_batch(formula, codes, period, market_data, formula_params)
    
    # 结果为 True 的代码通过
    return {code for code, result in results.items() if bool(result)}
```

**代码验证：** 现有 `engine.py:2459 _apply_edge_filter` 做的是类似的事情——按 filter_type 分派到 `_filter_unconditional`、`_filter_conditional`、`_filter_formula_eval`。伪代码把它简化成清晰的三种模式，去掉缓存、池更新等副作用，只关注"过滤"这件事。

---

### 6.4 函数 3：propagate_stocks(edge, passed_codes, node_stocks, node_ttl_state) → {entered, exited}

**作用：** 把通过过滤的股票从源节点流转到目标节点，返回变更明细。

**输入：**
- `edge`: 边配置（含 transfer_mode）
- `passed_codes`: 通过过滤的股票代码集合
- `node_stocks`: 节点股票表（原地修改）
- `node_ttl_state`: 节点 TTL 状态表（原地修改）

**输出：** `{'entered': [stock_dict, ...], 'exited': [stock_dict, ...]}`

```python
def propagate_stocks(edge, passed_codes, node_stocks, node_ttl_state):
    """股票流转：从源节点到目标节点，返回变更明细。
    
    三种流转模式：
    1. copy：复制（源节点保留，目标节点新增）
    2. move：移动（源节点移除，目标节点新增）
    3. overwrite：覆盖（目标节点替换成源节点的过滤结果）
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

    # 计算入池的股票（目标节点原来没有的）
    entered = [s for s in passed_stocks if s['code'] not in tgt_old_codes]
    entered_codes = {s['code'] for s in entered}

    exited = []
    exited_codes = set()

    # 模式 1：copy（源不动，目标新增）
    if mode == 'copy':
        for s in entered:
            tgt_list.append(copy.deepcopy(s))
        # copy 模式没有 exited

    # 模式 2：move（源移除，目标新增）
    elif mode == 'move':
        for s in entered:
            tgt_list.append(s)  # 移动对象，不拷贝
        # 从源节点移除
        new_src = [s for s in src_list if s['code'] not in passed_codes_set]
        exited = [s for s in src_list if s['code'] in passed_codes_set]
        exited_codes = passed_codes_set
        node_stocks[from_id] = new_src

    # 模式 3：overwrite（目标替换成源的过滤结果）
    elif mode == 'overwrite':
        # 原来在目标里但不在新结果里的 = 出池
        exited = [s for s in tgt_list if s['code'] not in passed_codes_set]
        exited_codes = {s['code'] for s in exited}
        # 替换目标节点
        node_stocks[to_id] = passed_stocks.copy()
        # overwrite 模式源节点不动

    # 更新目标节点列表
    if mode != 'overwrite':
        node_stocks[to_id] = tgt_list

    # 更新节点 TTL 状态
    now_ts = pool_now_ts()
    ttl_config = get_node_ttl_config(to_id)
    
    # 入池的股票：初始化 TTL
    for code in entered_codes:
        if ttl_config and ttl_config.enabled:
            node_ttl_state[(to_id, code)] = {
                'entry_ts': now_ts,
                'ttl_sec': ttl_config.ttl_sec,
                'remaining_sec': ttl_config.ttl_sec,
            }

    # 出池的股票：清理 TTL
    for code in exited_codes:
        key = (from_id if mode == 'move' else to_id, code)
        if key in node_ttl_state:
            del node_ttl_state[key]

    return {
        'entered': entered,
        'exited': exited,
    }
```

**代码验证：** 现有 `engine.py:2476 _filter_unconditional` 和 `_filter_conditional` 里内嵌了流转逻辑（更新 node_stocks、处理 move/copy/overwrite）。伪代码把流转逻辑抽出来变成独立函数，职责更清晰——filter 只管过滤，propagate 只管流转。

---

### 6.5 函数 4：check_alerts(scope_obj, changes, trackers, alert_state, events, compiled)

**作用：** 检查绑定到某个作用域对象（节点/边/池）的告警规则，满足条件就发告警。

**输入：**
- `scope_obj`: 作用域对象（node / edge / pool），有 alert_rule_ids 字段
- `changes`: 变更的股票列表（或流转统计）
- `trackers`: 持仓跟踪表
- `alert_state`: 告警状态（冷却记录等）
- `events`: 事件队列（输出告警事件）
- `compiled`: 编译后的池子（含预编译的告警规则）

**输出：** 无（副作用：往 events 里加告警事件，更新 alert_state）

```python
def check_alerts(scope_obj, changes, trackers, alert_state, events, compiled):
    """检查绑定到 scope_obj 的告警规则，满足条件就发告警。
    
    scope_obj 可以是：
    - 节点：检查节点绑定的告警规则（changes = 入池/出池的股票列表）
    - 边：检查边绑定的告警规则（changes = 流转结果，含 entered/exited 计数）
    - 池子：检查池子级告警规则（changes = 全量股票）
    """
    
    scope_id = scope_obj['id']
    scope_type = _get_scope_type(scope_obj)  # node / edge / pool
    alert_rule_ids = scope_obj.get('alert_rule_ids', [])
    
    now_ts = pool_now_ts()

    for rule_id in alert_rule_ids:
        # 从编译结果取预编译的告警规则
        rule = compiled.bound_alert_rules.get(rule_id)
        if rule is None:
            continue

        # 根据 scope_type 决定检查哪些股票
        stocks_to_check = _get_stocks_for_alert(scope_type, scope_id, changes, trackers)

        for stock in stocks_to_check:
            code = stock['code']
            
            # 冷却检查
            cooldown_key = (rule_id, code)
            if cooldown_key in alert_state.cooldowns:
                if now_ts < alert_state.cooldowns[cooldown_key]:
                    continue  # 还在冷却中，跳过
            
            # 构造告警上下文
            ctx = _build_alert_context(scope_type, scope_id, stock, trackers, changes)
            
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


def _get_scope_type(scope_obj):
    """判断对象是 node / edge / pool。"""
    if 'from' in scope_obj and 'to' in scope_obj:
        return 'edge'
    if 'type' in scope_obj and 'params' in scope_obj:
        return 'node'
    return 'pool'


def _get_stocks_for_alert(scope_type, scope_id, changes, trackers):
    """根据作用域类型获取要检查的股票列表。"""
    if scope_type == 'node':
        # 节点级：changes 就是入池/出池的股票列表
        return changes if isinstance(changes, list) else []
    if scope_type == 'edge':
        # 边级：changes 是流转结果，entered + exited 都要检查
        entered = changes.get('entered', [])
        exited = changes.get('exited', [])
        return entered + exited
    if scope_type == 'pool':
        # 池子级：全量持仓股票
        return list(trackers.values())
    return []


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

**代码验证：** 现有代码中告警逻辑是散落在各处的（`_push_alert`、`_alert_cooldown` 等），没有统一的 check_alerts 函数。伪代码把告警检查收拢成一个函数，并且明确了作用域模型——告警规则绑定到节点/边/池上，运行时只检查绑定的规则。

---

## 七、编译期更新（v2.3 更新）

### 7.1 编译期新增职责

| # | 工作项 | 说明 |
|---|--------|------|
| 1 | **拓扑排序验证** | 检查有没有环，计算每个节点的深度 |
| 2 | **执行顺序验证** | 用户指定的 exec_order 是否违背拓扑依赖 |
| 3 | **公式语法检查** | 所有引用的 formula_id 在 formula_lib 中存在吗？公式能正常编译吗？ |
| 4 | **触发器类型检查** | 边的 timing 配置合法吗？ |
| 5 | **节点类型验证** | 节点类型合法吗？参数完整吗？ |
| 6 | **边上下文预计算** | 预计算每条边的源节点、目标节点、filter_type 等 |
| 7 | **⚠️ 告警规则绑定与预编译** | 把节点/边/池绑定的 alert_rule_ids 解析出来，预编译条件表达式 |
| 8 | **⚠️ 信号规则绑定与预编译** | 把节点/边/池绑定的 signal_rule_ids 解析出来，预编译条件表达式 |
| 9 | **TTL 配置提取** | 每个节点的 TTL 配置提取出来 |

### 7.2 编译产物结构（更新）

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

    # ⚠️ v2.3 新增：绑定的告警规则（预编译）
    "bound_alert_rules": {
        rule_id: {
            ...rule_config,
            "_compiled_condition": compiled_expression,  # 预编译的条件
            "_scope_type": "node",  # 规则本身的 scope_type
        }
    },

    # ⚠️ v2.3 新增：绑定的信号规则（预编译）
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

### 7.3 绑定过程伪代码

```python
def _bind_alert_rules(pool_config, alert_rules_lib):
    """编译期：把告警规则绑定到节点/边/池上，并预编译条件表达式。"""
    
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
        
        bound[rule_id] = compiled_rule
    
    return bound
```

信号规则的绑定过程类似，略。

---

## 八、重构路径细化（v2.3 更新：第一步具体到文件级）

### 8.1 重构原则（同 v2.2）

1. **能合并的合并**：90 张配置表 → 6 张核心配置表
2. **能内联的内联**：简单逻辑不要拆成"表驱动"
3. **能删的删掉**：没用的概念、没用的中间表、没用的抽象层
4. **一个真相源**：数据只有一个入口，计算只有一个引擎，事件只有一个总线
5. **先补全再优化**：先把骨架和肉都长全，再考虑性能优化

### 8.2 分五步走

#### 第一步：统一数据层（解决"数据更新各搞一套"）

**目标：** `MarketData` 是唯一数据访问接口，所有计算从这里取数，没有直接读底层存储的代码。

**为什么先做这步？**
- 数据层是最底层的依赖，后面所有重构（公式引擎统一、tracker 统一、告警统一）都依赖统一的数据接口
- 现在数据访问散落在 engine.py、formula_engine.py、evaluators.py 里，各搞一套
- 先把数据层收拢，后面的重构才有稳定的基石

**具体做什么：**

| 任务 | 说明 | 影响文件 |
|------|------|---------|
| 1.1 定义 MarketData 接口 | 创建统一的行情数据访问接口类 | `core/market_data.py`（新建） |
| 1.2 实现默认 MarketData | 基于现有 tq_adapter 的实现 | `core/market_data.py` |
| 1.3 engine.py 改用 MarketData | 把 engine.py 里直接读 tick/bar 的地方，改成通过 MarketData 接口 | `core/engine.py` |
| 1.4 formula_engine.py 改用 MarketData | 公式引擎的数据获取走 MarketData 接口 | `core/formula_engine.py` |
| 1.5 evaluators.py 改用 MarketData | 评估器的数据获取走 MarketData 接口 | `core/evaluators.py` |
| 1.6 废弃 _data_cache / _current_bar_data | 统一由 MarketData 管理缓存 | `core/engine.py` |
| 1.7 验证：grep 不到直接读底层的代码 | 所有数据访问都走 MarketData 接口 | 全部文件 |

**MarketData 接口定义：**

```python
# core/market_data.py
class MarketData:
    """统一的行情数据访问接口（唯一真相源）。"""

    def get_tick(self, code: str) -> Optional[dict]:
        """获取某只股票的最新 tick 数据。"""
        ...

    def get_bar(self, code: str, period: str = 'day') -> Optional[list]:
        """获取某只股票的 K 线数据。
        
        period: day / 60min / 30min / 15min / 5min / 1min
        """
        ...

    def get_latest_ts(self, code: str) -> float:
        """获取某只股票的最新数据时间戳。"""
        ...

    def is_updated(self, code: str, since_ts: float) -> bool:
        """判断某只股票的数据是否比 since_ts 更新。"""
        ...

    def get_all_latest_ts(self) -> float:
        """获取全市场最新数据时间戳（用于判断整体数据是否更新）。"""
        ...

    def refresh(self) -> bool:
        """刷新数据（从底层数据源拉取最新数据）。
        
        返回 True 表示有数据更新。
        """
        ...
```

**影响文件清单：**

1. **`core/market_data.py`（新建）**
   - 定义 `MarketData` 抽象基类
   - 实现 `TqAdapterMarketData`（基于现有 tq_adapter）
   - 实现内存缓存（LRU + TTL）

2. **`core/engine.py`（修改）**
   - 删除 `_data_cache`、`_current_bar_data`、`_last_data_update_ts`
   - 新增 `self.market_data: MarketData` 属性
   - 所有直接读 tick/bar 的地方，改成 `self.market_data.get_tick(code)`
   - 所有 `time.time()` 用于数据时间判断的，改成 `self.market_data.get_latest_ts(code)`
   - `_latest_tick` 逐步迁移到 `MarketData` 内部管理

3. **`core/formula_engine.py`（修改）**
   - 公式引擎的构造函数接受 `MarketData` 参数
   - 所有数据获取通过 `MarketData` 接口
   - 不再直接访问 tq_adapter 或底层存储

4. **`core/evaluators.py`（修改）**
   - 评估器的数据获取通过 `MarketData` 接口
   - 不再直接读底层数据

**验收标准：**
```bash
# 检查是否还有直接读底层存储的代码
grep -rn "tq_adapter\." core/engine.py core/formula_engine.py core/evaluators.py
# 结果：0 行（全部通过 MarketData 间接访问）

grep -rn "_data_cache\|_current_bar_data" core/engine.py
# 结果：0 行（已废弃）

grep -rn "get_tick\|get_bar\|get_latest_ts" core/
# 结果：都走 MarketData 接口
```

**预计改动量：**
- 新建文件：~150 行（MarketData 接口 + 实现）
- 修改 engine.py：~50 处调用点修改
- 修改 formula_engine.py：~20 处调用点修改
- 修改 evaluators.py：~10 处调用点修改
- 总计：~300 行新增/修改

**风险：**
- 低风险：只是接口封装，业务逻辑不变
- 有现成的 tq_adapter 可以包一层
- 可以分阶段迁移，先包接口，再逐步替换调用点

---

#### 第二步：补全运行时表（解决"运行时表缺失"）

**目标：** 30+ 运行时表 → 10 张核心业务状态表。

**动作：**
- [ ] 合并边时序状态：`_flow_last_fire_ts + _flow_first_fire_ts + _flow_exec_counts + _flow_duration_starts` → `edge_timing_state`
- [ ] 新增 `node_ttl_state`：节点 TTL 状态
- [ ] 修正 `trackers` 主键：code → (pool_id, code)
- [ ] 新增 `signal_state`：信号状态（去重 + 历史 + 队列）
- [ ] 扩展 `alert_state`：从只有 cooldown → cooldown + 历史 + 队列
- [ ] 合并池子状态：`_pool_start_time + _first_run + _current_bar_time` → `pool_state`
- [ ] 统一事件队列：`_event_queue + _alert_queue + _signal_queue` → `events`
- [ ] 新增 `pool_errors`：统一错误记录表
- [ ] 明确哪些是中间缓存，从 `_RUNTIME_TABLE_NAMES` 中移除

**验收：** `_RUNTIME_TABLE_NAMES` 中只有 10 个核心业务状态表。

---

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

---

#### 第四步：完善编译期（解决"编译期职责不清"）

**目标：** compile_pool 做完整的编译期工作，运行期只读 compiled_pool。

**动作：**
- [ ] 编译期做公式语法检查
- [ ] 编译期做执行顺序验证
- [ ] 编译期做告警/信号规则绑定（按作用域）
- [ ] 编译期预提取节点 TTL 配置
- [ ] 运行期所有"查表"都改成读 compiled_pool 里的预计算结果
- [ ] 编译失败时池子不启动，给出明确的错误信息

**验收：** 运行期没有"配置查表"，所有静态数据都在 compiled_pool 里。

---

#### 第五步：精简配置层（解决"配置表 90 张"）

**目标：** 90+ 张配置表 → 6 张核心配置表 + 可选的扩展表。

**动作：**
- [ ] 把分散的节点/边/执行顺序合并进 `pool_config`
- [ ] 把分散的公式定义合并进 `formula_lib`
- [ ] 把告警规则定义独立为 `alert_rules`（加 scope 字段）
- [ ] 把信号规则定义独立为 `signal_rules`（加 scope 字段）
- [ ] 把 tracker 字段定义独立为 `tracker_schema`
- [ ] 把系统参数合并进 `system_config`
- [ ] 评估剩下的 80+ 张表，简单逻辑内联，UI 相关移到前端

**验收：** 后端核心配置表只有 6 张，新人能在 10 分钟内理解配置结构。

---

### 8.3 最终会变成什么样？

```
核心文件（7-8 个）：
├── engine.py           # 主循环 + 边执行调度（500 行以内）
├── market_data.py      # 数据接入（统一接口，唯一真相源）⭐ v2.3 明确
├── formula_engine.py   # 公式计算（统一引擎）
├── tracker.py          # 持仓跟踪（独立模块）
├── event_bus.py        # 事件总线（统一出口）
├── pool_compiler.py    # 编译期：pool_config → compiled_pool
├── alert_manager.py    # 告警管理（作用域 + 冷却）
└── signal_manager.py   # 信号管理（作用域 + 去重）

核心表（16 张）：
┌─ 配置表（6 张）───────┐
│ pool_config          │  节点 + 边 + 执行顺序 + 绑定的规则ID
│ formula_lib          │  公式库
│ alert_rules          │  告警规则（含 scope_type 字段）⭐ v2.3 更新
│ signal_rules         │  交易信号规则（含 scope_type 字段）⭐ v2.3 更新
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

## 九、时间和触发的处理（v2.3 更新：统一时间源）

### 9.1 为什么时间是核心维度？

股票池的本质是"**在正确的时间做正确的筛选**"——时间不对，一切都白搭。

### 9.2 两个时间源（v2.3 明确）

| 时间源 | 函数 | 含义 | 用途 |
|-------|------|------|------|
| **触发时间** | `pool_now()` / `pool_now_ts()` | 池子当前时间 | 触发判断、TTL 递减、冷却判断、事件时间戳 |
| **数据时间** | `data_ts(code)` / `get_latest_ts(code)` | 某只股票的数据时间 | 判断数据是否更新、计算指标 |

**绝不混用：**
- 问"该不该触发" → 用 `pool_now()`
- 问"数据新不新" → 用 `data_ts(code)`

### 9.3 时间的四个层次

| 层次 | 概念 | 时间源 | 对应表 |
|------|------|--------|--------|
| **全局数据时间** | 最新行情时间 | `market_data.get_all_latest_ts()` | `market_data` |
| **池子触发时间** | 池子当前时间 | `pool_now()` | `pool_state` |
| **边时间** | 边的触发状态 | `pool_now()` 推进 | `edge_timing_state` |
| **节点时间** | 节点 TTL 状态 | `pool_now()` 推进 | `node_ttl_state` |

### 9.4 触发条件的完整判定逻辑

```
一条边要不要执行，要过四道关（都用 pool_now()）：

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
   - 源节点股票变了吗？或者行情数据变了吗？（用 data_ts 判断）
   - 都没变 → 算了也白算，跳过

四道关都过了 → 执行过滤计算
```

### 9.5 数据不变 = 不用算

**核心洞察：** 最新 tick 时间不变 → 所有 K 线数据不变 → 所有计算结果不变 → 不需重新计算。

**实现方式：**
- `market_data.get_all_latest_ts()` 记录当前全市场最新数据时间
- `pool_state.last_data_ts` 记录上次数据时间
- 每个 tick 开始时对比：时间没变 → 整条边的过滤都可以跳过（除非源节点股票变了）

### 9.6 节点 TTL 的递减逻辑

用 `pool_now_ts()` 计算剩余时间，不用系统时间，不用数据时间。

```
每个 tick 开始时：
  遍历 node_ttl_state 中的每只股票：
    remaining = ttl_sec - (pool_now_ts() - entry_ts)
    如果 remaining <= 0：
      从节点中移除该股票
      关闭 tracker（status = 'timeout'）
      触发 SELL 信号（如果是目标节点）
      发出 transfer 事件（timeout_exit）
```

---

## 十、告警与信号的作用域模型（v2.3 新增）

### 10.1 作用域模型总览

告警规则和信号规则都不是全局的，而是有作用域的。它们定义在 `alert_rules` / `signal_rules` 表里，然后在 `pool_config` 的节点/边上通过 `alert_rule_ids` / `signal_rule_ids` 绑定。

```
alert_rules 表（定义规则，全局唯一ID）
    │
    │  编译期绑定
    ▼
pool_config.nodes[*].alert_rule_ids  →  节点级告警
pool_config.edges[*].alert_rule_ids  →  边级告警
pool_config.alert_rule_ids           →  池子级告警
```

### 10.2 三种作用域对比

| 作用域 | 触发时机 | 检查对象 | 绑定位置 | 典型例子 |
|-------|---------|---------|---------|---------|
| **node（节点级）** | 股票入池/出池时 + 周期检查 | 该节点的股票 | node.alert_rule_ids | 止盈告警、回撤告警、持仓超时 |
| **edge（边级）** | 边执行完有流转时 | 流转的股票 | edge.alert_rule_ids | 边触发通知、异常大量流转 |
| **pool（池子级）** | 每个 tick 末 | 全池股票 | pool_config.alert_rule_ids | 池子空了、全池亏损告警 |

### 10.3 编译期做什么？

1. **收集**：遍历 pool_config 的所有节点/边/池，收集所有引用的 `alert_rule_ids`
2. **校验**：检查这些 rule_id 在 alert_rules 表中存在
3. **编译**：预编译告警规则的条件表达式
4. **存储**：把预编译的规则存在 `compiled.bound_alert_rules` 里

运行时：
- 检查节点告警 → 只查该节点绑定的规则
- 检查边告警 → 只查该边绑定的规则
- 检查池子告警 → 只查池子绑定的规则

**不需要遍历全局规则表，性能更好，逻辑更清晰。**

### 10.4 信号规则的作用域

信号规则同理：

| 作用域 | 触发时机 | 绑定位置 | 典型例子 |
|-------|---------|---------|---------|
| **node（节点级）** | 股票入池（enter）/ 出池（exit） | node.signal_rule_ids | 入池 BUY、出池 SELL |
| **edge（边级）** | 边执行流转时 | edge.signal_rule_ids | 特殊边触发特殊信号 |
| **pool（池子级）** | 周期检查时 | pool_config.signal_rule_ids | 全池调仓信号 |

### 10.5 表结构变更

#### alert_rules 表加 scope 字段

```json
// 之前（v2.2）
{
  "rule_id": "profit_threshold",
  "name": "止盈告警",
  "scope": "tracker",  // 模糊，不知道是节点级还是全局
  ...
}

// 现在（v2.3）
{
  "rule_id": "profit_threshold",
  "name": "止盈告警",
  "scope_type": "node",  // 明确：node / edge / pool
  "scope_target": null,  // 可选：指定具体节点ID，null 表示绑定到哪里算哪里
  ...
}
```

#### signal_rules 表加 scope 字段

```json
// 之前（v2.2）
{
  "signal_type": "BUY",
  "trigger": { "type": "pool_enter", ... },
  ...
}

// 现在（v2.3）
{
  "rule_id": "BUY_state_pool",
  "signal_type": "BUY",
  "scope_type": "node",
  "trigger_event": "enter",  // enter / exit / periodic
  ...
}
```

#### pool_config 加绑定字段

```json
{
  "pool_id": "pool_001",
  "alert_rule_ids": ["pool_empty"],
  "signal_rule_ids": [],
  "nodes": [
    {
      "id": "n1",
      "alert_rule_ids": ["profit_threshold", "drawdown_threshold"],
      "signal_rule_ids": ["BUY_state_pool", "SELL_state_pool"],
      ...
    }
  ],
  "edges": [
    {
      "id": "e1",
      "alert_rule_ids": ["edge_triggered"],
      "signal_rule_ids": [],
      ...
    }
  ]
}
```

---

## 十一、一句话总结

> **股票池就是几个篮子用管子连起来，管子上有筛子和定时器，定时看看有没有新数据，有就筛一下，股票从上游流到下游；同时记一下每只股票什么时候进来的、赚了多少、还能待多久，筛出来了就通知一下，该买卖了就发个信号。**

**v2.3 进步在哪里？**
- ✅ 4 个核心函数从黑盒变伪代码（should_trigger / run_filter / propagate_stocks / check_alerts），开发拿着就能写
- ✅ 告警/信号规则有了明确的作用域模型（node / edge / pool），不再自相矛盾
- ✅ 时间源统一为 pool_now() + data_ts()，触发用触发时间，数据用数据时间，绝不混用
- ✅ 重构第一步具体到文件级（core/market_data.py + engine.py + formula_engine.py + evaluators.py），开发拿着就能干
- ✅ 表结构更新明确（alert_rules / signal_rules 加 scope_type 字段，pool_config 加 rule_ids 绑定）

从"骨架对、肉长全"到"伪代码可直接写代码"，冲击 80 分的"可指导重构"及格线。
