# MetaCore 股票池平台系统参考文档

## 一、架构总览

```
┌───────────────────────────────────────────────────────────────────┐
│                        MetaEngine (顶层门面)                          │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  PoolEngine (单池执行引擎)                                      │  │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐  │  │
│  │  │ Compiler │ │ PoolState│ │EventBus  │ │  EventDriver     │  │  │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────────────┘  │  │
│  │  ┌──────────────┐ ┌───────────────┐ ┌───────────────────┐    │  │
│  │  │ EdgeExecutor │ │ FormulaEngine │ │ TtlTracker(s)      │    │  │
│  │  └──────────────┘ └───────────────┘ └────────────────────┘    │  │
│  │  ┌──────────────┐ ┌───────────────┐ ┌───────────────────┐    │  │
│  │  │ DataUpdater  │ │ BarComposer   │ │ SnapshotBuilder    │    │  │
│  │  └──────────────┘ └───────────────┘ └────────────────────┘    │  │
│  └──────────────────────────────────────────────────────────────┘  │
│  ┌──────────────────┐ ┌────────────────┐ ┌────────────────────┐   │
│  │ UIRenderer       │ │ WS Publisher   │ │ KLineReplayEngine   │   │
│  └──────────────────┘ └────────────────┘ └────────────────────┘   │
│  ┌──────────────────┐ ┌────────────────┐ ┌─────────────────────┐   │
│  │ RuntimeSimulator │ │ TqAdapter      │ │ Storage             │   │
│  └─────────────────┘ └────────────────┘ └─────────────────────┘   │
└───────────────────────────────────────────────────────────────────┘
         │                     │                      │
    ┌────┴─────┐        ┌─────┴──────┐         ┌─────┴──────┐
    │Providers │        │TableEngine │         │ Validators │
    │ TQ/Mock/ │        │ConfigStore │         │  Syntax/   │
    │ AKShare/ │        │RuleEngine  │         │  Logic/    │
    │ HQChart/ │        │DataBinder  │         │  Business  │
    │ DFCF/Local│       │PanelGen    │         │            │
    └──────────┘        └────────────┘         └────────────┘
```

## 二、核心层 (core/) — 类与方法

### 2.1 PoolEngine — 单池执行引擎

| 方法 | 参数 | 职责 |
|------|------|------|
| `__init__` | meta_engine, pool_config | 组装所有组件：state/bus/executor/composer/driver/schedule |
| `__getattr__` | name | 委托给 PoolStateMixin 的表访问 |
| `_build_topology` | | 构建节点/边拓扑映射 |
| `_init_node_stocks` | | 从 pool_config 初始化各节点股票，注册预填充股票到 TtlTracker |
| `_mark_source_nodes_dirty` | | 标记所有源节点为 dirty |
| `_run_tick_body` | | tick 主体：`driver.fire_due(now)` → 清 dirty → 快照 |
| `run_pool` | current_bar_data | 完整 run_pool 流程：初始化→数据刷新→tick→事件 |
| `run_tick` | | 单步 tick（兼容） |
| `get_events` | event_type | 获取指定类型事件 |

### 2.2 MetaEngine — 顶层门面

| 方法 | 参数 | 职责 |
|------|------|------|
| `__init__` | config_dir | 初始化存储/适配器/事件队列 |
| `_ensure_pool_engine` | pool_config | 按需创建/复用 PoolEngine |
| `_attach_ui_layer` | pe | 绑定 SnapshotBuilder/UIRenderer/WSPublisher |
| `run_pool` | pool_config, current_bar_data | 委托 PoolEngine.run_pool |
| `run_loop` | pool_config, current_bar_data | 异步循环运行 |
| `run_mode` | mode_id, pool_config, ... | 运行指定模式(replay/simulation) |
| `_tick` | pool_config, node_stocks, ... | 单步 tick |
| `_refresh_bar_data` | mode_cfg, current_bar_data | 按模式刷新行情 |
| `_post_tick` | node_stocks, current_bar_data | tick 后处理：RA排序/事件/回调 |
| `_emit_domain_event` | domain, domain_ctx, code | 发布 DomainEvent(ENTER/EXIT) |
| `_emit_transfer_events` | prev, updated, transfer_events | 批量发布转移事件 |
| `_push_event` | et, code, pool_id, detail | 底层 DomainEvent 发布 |
| `_on_signal_event` | signal | Signal → _signal_queue |
| `_on_domain_event` | event | DomainEvent → _event_queue |
| `_update_trackers` | node_stocks, current_bar_data | 更新持仓追踪器 |
| `_build_exit_tracker_info` | code, sid, prev_stock_index | 提取退出股票追踪信息 |
| `set_tq_adapter` | a | 设置 TqAdapter |
| `set_storage` | s | 设置 Storage |
| `set_table_engine` | config_store, rule_engine, ... | 设置表引擎组件 |
| `check_hot_reload` | | 检查配置热重载 |

### 2.3 Compiler — 编译器

| 方法/函数 | 参数 | 职责 |
|-----------|------|------|
| `Compiler.compile` | cls, pool_config | 主编译入口：config → CompiledSchedule |
| `_build_execution_order` | edges | 拓扑排序确定边执行顺序 |
| `_build_edge_ctx` | nodes, edges | 构建 EdgeContext(sid/tid) |
| `_build_timing_spec` | edge | 构建 TimingSpec(starttype/endtype/...) |
| `_build_filter_spec` | edge, nodes | 构建 FilterSpec(nset/formula/scalar) |
| `_build_propagate_spec` | edge | 构建 PropagateSpec(mode/strategy) |
| `_build_action_spec` | tid, nodes | 构建 ActionSpec(callback/baimpool) |
| `_build_ttl_spec` | tid, nodes | 构建 TTLSpec(check_type/ttl_sec/endtime_sec) |
| `_normalize_nodes` | pool_config | 规范化节点配置 |
| `build_timed_event_specs` | schedule, state, engine, ... | 编译所有 TimedEventSpec(边+TTL) |
| `_make_edge_at_fn` | state, eid, timing, executor | 边触发 at_fn：gate 通过返回 0.0 |
| `_make_edge_action` | bus, eid, sid, tid, executor, ... | 边 action：检查 dirty → run(eid) |
| `_make_ttl_interval_at_fn` | tracker | TTL interval at_fn：tracker.next_expire_at() |
| `_make_ttl_interval_action` | state, tracker, bus | TTL interval action：pop_expired → 删股 → 发布 TIMEOUT |
| `_make_ttl_endtime_at_fn` | state, endtime_sec | TTL endtime at_fn：当前秒≥endtime 返回 0.0 |
| `_make_ttl_endtime_action` | state, ttl_spec, tgt, bus, eid | TTL endtime action：扫描删股 → 发布 TIMEOUT |

### 2.4 CompiledSchedule — 编译产物

| 字段 | 类型 | 含义 |
|------|------|------|
| `execution_order` | List[str] | 边执行顺序(eid列表) |
| `edge_ctx` | Dict[str, EdgeContext] | eid → {sid, tid} |
| `edge_timing_spec` | Dict[str, TimingSpec] | eid → 时间触发规格 |
| `edge_filter_spec` | Dict[str, FilterSpec] | eid → 过滤规格 |
| `edge_propagate_spec` | Dict[str, PropagateSpec] | eid → 传播规格 |
| `edge_action_spec` | Dict[str, ActionSpec] | eid → 动作规格 |
| `edge_ttl_spec` | Dict[str, TTLSpec] | eid → TTL规格 |
| `node_ttl_spec` | Dict[str, TTLSpec] | nid → TTL规格(无入边节点) |

### 2.5 EdgeExecutor — 边执行器

| 方法 | 参数 | 职责 |
|------|------|------|
| `__init__` | state, schedule, formula_engine, event_bus, event_driver | 属性: state/schedule/formula_engine/bus/event_driver/_tick_table |
| `run` | eid | 完整管线: gate → filter → propagate → callback |
| `_gate` | spec, eid | 时间门控: always/never/elapsed/before_open/after_open/... |
| `_filter` | spec, codes, eid | 过滤: pass_through/formula/scalar/set_op |
| `_propagate` | spec, sid, tid, passed | 传播: copy/move/overwrite + 注册/注销 TTL |

**独立函数（gate 分发）:**

| 函数 | 职责 |
|------|------|
| `_gate_always` | 始终通过 |
| `_gate_never` | 始终拒绝 |
| `_gate_elapsed` | 经过 N 秒后触发 |
| `_gate_before_open` | 开盘前触发 |
| `_gate_after_open` | 开盘后触发 |
| `_gate_before_close` | 收盘前触发 |
| `_gate_after_close` | 收盘后触发 |
| `_gate_hhmmss` | 指定 HHMMSS 触发 |
| `_starttype_gate` | 门控分发入口 |

**独立函数（filter 分发）:**

| 函数 | 职责 |
|------|------|
| `_eval_pass_through` | 全通过 |
| `_eval_formula_path` | 公式求值过滤 |
| `_eval_scalar_path` | 标量比较过滤 |
| `_eval_set_op_path` | 集合运算过滤(差/交/并) |

**独立函数（propagate 策略）:**

| 函数 | 职责 |
|------|------|
| `_tgt_merge` | 目标池合并(保留已有+新增) |
| `_tgt_overwrite` | 目标池覆盖(清空+写入) |
| `_src_delete` | 源池删除(移动模式) |
| `_src_keep` | 源池保留(复制模式) |

**独立函数（TTL/Callback）:**

| 函数 | 职责 |
|------|------|
| `_init_entry_trackers` | 入池时注册 TTL 到 EventDriver |
| `_stock_entry_time` | 从 indate/intime 解析入池时间戳 |
| `_action_baimpool` | 发布 Signal(BUY) |
| `_run_callback` | 执行回调动作 |

### 2.6 PoolState — 池状态

| 方法 | 参数 | 职责 |
|------|------|------|
| `__init__` | pool_config | 初始化 14 张运行时表 |
| `get_node_stocks` | nid | 获取节点股票列表 |
| `set_node_stocks` | nid, stocks | 设置节点股票列表 |
| `mark_node_dirty` | nid | 标记节点脏 |
| `clear_dirty` | | 清除所有脏标记 |
| `get_exec_ctx` | eid | 获取边执行上下文 |
| `set_exec_ctx_fired` | eid, now | 标记边已触发 |
| `get_formula_result` | formula_ref, bar_hash | 获取公式缓存 |
| `set_formula_result` | formula_ref, bar_hash, result | 设置公式缓存 |
| `snapshot_nodes` | | 快照所有节点股票 |
| `restore_snapshots` | | 恢复快照 |
| `set_time_source` | ts_config | 设置时间源 |
| `bar_hash` | | 当前 bar 数据哈希 |
| `update_latest_tick` | tick_data | 更新 tick 数据 |

**14 张运行时表:**

| 表名 | 类型 | 含义 |
|------|------|------|
| `node_stocks` | Dict[str, List] | nid → 股票列表 |
| `latest_tick` | Dict[str, Dict] | code → 最新 tick |
| `prev_tick` | Dict[str, Dict] | code → 上一个 tick |
| `bars` | Dict | K线数据 |
| `exec_ctx` | Dict[str, Dict] | eid → 执行上下文(fired/count/ts) |
| `formula_results` | Dict[tuple, Dict] | (formula,mode,ref,hash) → 结果 |
| `filter_inputs` | Dict[str, frozenset] | eid → 输入代码集 |
| `node_dirty` | set | 脏节点集合 |
| `data_dirty` | bool | 数据脏标记 |
| `first_run` | bool | 首次运行标记 |
| `time_source` | Dict | 时间源配置 |
| `data_source` | Dict | 数据源配置 |
| `trade_interface` | Dict | 交易接口配置 |
| `side_effects_scope` | Dict | 副作用作用域 |

### 2.7 EventBus — 事件总线

| 方法 | 参数 | 职责 |
|------|------|------|
| `__init__` | | 初始化 _handlers: Dict[str, List[Callable]] |
| `subscribe` | event_type, handler | 订阅事件类型 |
| `publish` | event | 发布事件(按 type(event).__name__ 分发) |
| `get_events` | event_type | 获取已发布事件列表 |
| `clear` | | 清除所有已发布事件和处理器 |

### 2.8 EventDriver — 时间驱动器

| 方法 | 参数 | 职责 |
|------|------|------|
| `__init__` | state, bus | 初始化 _specs/_ttl_trackers |
| `add_spec` | spec | 注册 TimedEventSpec |
| `add_ttl_tracker` | eid, tracker | 注册 TtlTracker |
| `register_ttl` | eid, code, ttl_sec, entry_ts, now_unix | 运行时注册 TTL 条目 |
| `unregister_ttl` | eid, code | 运行时注销 TTL 条目 |
| `fire_due` | now | 扫描所有 spec，触发 at_fn()<=now 的 action |
| `is_edge_due` | eid, now | 检查特定边是否到期 |
| `fire_ttl_due` | now | 仅触发 TTL 类 spec |
| `clear_ttl` | | 清除所有 TtlTracker |

### 2.9 TtlTracker — TTL 追踪器

| 方法 | 参数 | 职责 |
|------|------|------|
| `__init__` | tgt, eid | 初始化 min-heap + entries dict |
| `register` | code, ttl_sec, entry_ts, now_unix | 注册到期条目(入堆) |
| `unregister` | code | 注销条目(lazy 标记删除) |
| `next_expire_at` | | 返回最近到期时间(inf=空) |
| `pop_expired` | now_unix | 弹出所有已到期条目 |
| `clear` | | 清空堆 |

### 2.10 TimedEventSpec — 定时事件规格

| 字段 | 类型 | 含义 |
|------|------|------|
| `at_fn` | Callable[[], float] | 返回下次触发时间(unix sec)；<=now 表示到期 |
| `interval` | Optional[float] | 触发间隔(秒)；None=一次性 |
| `end_fn` | Optional[Callable[[], float]] | 结束时间函数；None=永久 |
| `action` | Callable[[Any], None] | 到期回调：action(params) |
| `params` | dict | 传给 action 的参数 |

### 2.11 TtlEntry — TTL 条目(frozen dataclass)

| 字段 | 类型 | 含义 |
|------|------|------|
| `code` | str | 股票代码 |
| `expire_at` | float | 到期时间(unix sec) |
| `ttl_sec` | int | TTL 时长(秒) |
| `entry_ts` | float | 入池时间戳 |

### 2.12 FormulaEngine — 公式引擎

| 方法 | 参数 | 职责 |
|------|------|------|
| `__init__` | state | 绑定 PoolState |
| `eval` | spec, codes, ctx | 统一缓存求值入口 |
| `eval_scalar` | spec, codes, ctx, evaluator_fn | 标量求值 |
| `_cached_eval` | spec, codes, ctx, evaluator_fn, writeback | 缓存读/写集中于此 |
| `_eval_formula` | formula_ref, codes, ctx | 调用底层公式引擎 |

**工厂函数:**

| 函数 | 返回 | 职责 |
|------|------|------|
| `live_context` | EvalContext | 构造实盘模式上下文 |
| `replay_context` | EvalContext | 构造回放模式上下文 |
| `simulation_context` | EvalContext | 构造仿真模式上下文 |

### 2.13 TickTable — Tick 数据表

| 方法 | 参数 | 职责 |
|------|------|------|
| `__init__` | latest_tick, prev_tick | 绑定当前/上一 tick dict |
| `column` | code, col | 读取当前 tick 字段 |
| `prev_column` | code, col | 读取上一 tick 字段 |
| `bar_hash` | | 返回 tick 聚合哈希 |

### 2.14 DataUpdater — 数据更新器

| 方法 | 参数 | 职责 |
|------|------|------|
| `__init__` | state, bus | 绑定 PoolState + EventBus |
| `apply_data` | tick_data | 应用 tick 数据到 state |
| `_apply_code_tick` | code, tick | 单代码 tick 更新 |
| `_publish_tick_changed` | codes | 发布 DataChanged(source="tick") |

### 2.15 BarComposer — K线合成器

| 方法 | 参数 | 职责 |
|------|------|------|
| `__init__` | state, bus, periods | 绑定状态+事件总线+周期列表 |
| `subscribe` | | 订阅 DataChanged(tick) |
| `on_data_changed` | event | tick DataChanged → 合成 K线 → 发布 DataChanged(bar) |
| `on_tick` | codes | 处理 tick 更新 |
| `get_bar` | period, code | 获取指定周期K线 |
| `bar_hash` | field_refs | 计算引用字段哈希 |

### 2.16 SnapshotBuilder — 快照构建器

| 方法 | 参数 | 职责 |
|------|------|------|
| `__init__` | event_bus, nodes | 订阅 Executed/DataChanged/DomainEvent |
| `on_executed` | event | ADD entered, DISCARD exited/target_cleared |
| `on_data_changed` | event | 更新 _data_meta[code] |
| `on_domain_event` | event | 仅处理 TIMEOUT: DISCARD code from pool |
| `snapshot` | | 构建当前完整快照 |
| `get_node` | nid | 获取单节点快照 |

### 2.17 Spec 数据类

| 类 | 关键字段 |
|----|---------|
| `TimingSpec` | starttype, endtype, offset_sec, interval_sec, start_hhmm, end_hhmm |
| `FilterSpec` | nset, formula_ref, fsecond, noperate, formula, scalar_expr, set_op |
| `PropagateSpec` | mode(copy/move/overwrite), tgt_strategy, src_strategy |
| `ActionSpec` | callback, baimpool, target_pool_action |
| `TTLSpec` | check_type(interval/endtime), ttl_sec, endtime_sec, bdel |
| `EdgeContext` | sid, tid, eid |
| `EvalContext` | mode, bar_hash, bars, latest_tick |

---

## 三、事件体系

### 3.1 四大事件类型

| 事件常量 | 值 | 数据类 | 核心字段 |
|----------|----|----|---------|
| `EVENT_DATA_CHANGED` | `"DataChanged"` | DataChanged | ts, bar_hash, codes, source, period, data |
| `EVENT_EXECUTED` | `"Executed"` | Executed | eid, sid, tid, entered, exited, target_cleared, mode |
| `EVENT_DOMAIN` | `"DomainEvent"` | DomainEvent | event_type, code, pool_id, details |
| `EVENT_SIGNAL` | `"Signal"` | Signal | signal_type, code, pool_id, price, ts, condition, profit_pct, hold_days |

### 3.2 DomainEvent 子类型

| event_type | 触发时机 | details |
|------------|---------|---------|
| `"ENTER"` | 股票入池(pool_enter) | actions, prices, timestamp, mode, flow_id |
| `"EXIT"` | 股票移出(move_exit) | reason="move_exit", source_id, flow_id |
| `"TIMEOUT"` | TTL 到期 | reason="TTL_EXPIRED"/"TTL_ENDTIME", flow_id, ttl_sec, timestamp |
| `"RANK_CHANGED"` | 排名变化 | old_rank, new_rank, score |

### 3.3 Signal 子类型

| signal_type | 触发时机 | 关键字段 |
|-------------|---------|---------|
| `"BUY"` | 入池+百幕池动作 | code, pool_id=目标, price, condition |
| `"SELL"` | 移出/TTL到期+持仓追踪 | code, pool_id, profit_pct, hold_days |

### 3.4 DataChanged 子类型

| source | 触发者 | period | data |
|--------|-------|--------|------|
| `"tick"` | DataUpdater | None | None |
| `"bar"` | BarComposer | "1m"/"5m"/... | 单根K线dict |

### 3.5 完整发布者→订阅者映射

```
┌───────────────────────────────────────────────────────────────────────┐
│ 发布者                          │ 事件              │ 订阅者            │
├──────────────────────────────────────────────────────────────────────┤
│ DataUpdater._publish_tick_changed                                          │
│   ├─ DataChanged(tick) ──────── BarComposer.on_data_changed               │
│   ├─ DataChanged(tick) ──────── SnapshotBuilder.on_data_changed           │
│   └─ DataChanged(tick) ──────── UIRenderer.on_event("data_changed")      │
│                                                                             │
│ BarComposer → _publish_bar_changed                                         │
│   ├─ DataChanged(bar) ──────── SnapshotBuilder.on_data_changed           │
│   └─ DataChanged(bar) ──────── UIRenderer.on_event("data_changed")      │
│                                                                             │
│ EdgeExecutor.run → _publish                                                │
│   ├─ Executed ───────────────── SnapshotBuilder.on_executed              │
│   └─ Executed ───────────────── UIRenderer.on_event("executed")         │
│                                                                             │
│ _action_baimpool → _publish                                                │
│   ├─ Signal(BUY) ────────────── MetaEngine._on_signal_event → _signal_q │
│   └─ Signal(BUY) ────────────── UIRenderer.on_event("signal")           │
│                                                                             │
│ TTL interval action (compiler closure)                                     │
│   ├─ DomainEvent(TIMEOUT) ───── MetaEngine._on_domain_event → _event_q │
│   ├─ DomainEvent(TIMEOUT) ───── SnapshotBuilder.on_domain_event         │
│   └─ DomainEvent(TIMEOUT) ───── UIRenderer.on_event("domain")          │
│                                                                             │
│ TTL endtime action (compiler closure)                                      │
│   ├─ DomainEvent(TIMEOUT) ───── (同上)                                   │
│                                                                             │
│ MetaEngine._push_event                                                     │
│   ├─ DomainEvent(ENTER) ─────── MetaEngine._on_domain_event → _event_q │
│   ├─ DomainEvent(ENTER) ─────── UIRenderer.on_event("domain")          │
│   │                            (SnapshotBuilder 跳过 ENTER)              │
│   ├─ DomainEvent(EXIT) ──────── (同 ENTER)                               │
│   └─ DomainEvent(RANK_CHANGED)─ (同 ENTER)                               │
│                                                                             │
│ MetaEngine._emit_domain_event                                              │
│   └─ Signal(SELL) ───────────── MetaEngine._on_signal_event → _signal_q │
│                                 UIRenderer.on_event("signal")           │
└───────────────────────────────────────────────────────────────────────┘
```

### 3.6 定时事件体系 (TimedEventSpec 三种变体)

| params["kind"] | at_fn 逻辑 | action 效果 | 触发事件 |
|----------------|-----------|------------|---------|
| `"edge"` | `_gate(timing,eid)` → 0.0(通过)/now+1(未到) | `edge_executor.run(eid)` | Executed + Signal(BUY) |
| `"ttl"` (interval) | `tracker.next_expire_at()` → unix sec / inf | `pop_expired` → 删股 | DomainEvent(TIMEOUT) |
| `"ttl"` (endtime) | `now_sec_of_day >= endtime` → 0.0 / now+1 | 扫描删股 | DomainEvent(TIMEOUT) |

---

## 四、端到端数据流

```
外部 tick 数据
    │
    ▼
DataUpdater.apply_data()
    │ 发布 DataChanged(tick)
    ▼
EventBus ──┬── BarComposer.on_data_changed()  → 合成K线 → DataChanged(bar)
           ├── SnapshotBuilder.on_data_changed() → 更新 data_meta
           └── UIRenderer → WebSocket 推送

PoolEngine._run_tick_body()
    │
    ▼
EventDriver.fire_due(now)
    │
    ├──[边定时事件] ── action → edge_executor.run(eid)
    │       │
    │       ├── _gate() ──── 时间门控(starttype分发)
    │       ├── _filter() ── 过滤(pass_through/formula/scalar/set_op)
    │       ├── _propagate() ── 传播(copy/move/overwrite)
    │       │     └── register_ttl / unregister_ttl
    │       ├── _publish(Executed) ──→ SnapshotBuilder / UIRenderer
    │       └── _run_callback() ── _action_baimpool → Signal(BUY)
    │
    └──[TTL定时事件] ── action
            │
            ├── interval: tracker.pop_expired() → 删股 → DomainEvent(TIMEOUT)
            └── endtime: 扫描删股 → DomainEvent(TIMEOUT)
                    │
                    ▼
            EventBus ── SnapshotBuilder(TIMEOUT:DISCARD) / MetaEngine / UIRenderer

tick 后处理: MetaEngine._post_tick()
    ├── _ra_score_weighted_sort → RANK_CHANGED
    ├── _emit_transfer_events → ENTER/EXIT DomainEvent
    └── _emit_domain_event → Signal(SELL)
```

---

## 五、服务层 (services/) — 关键类

### 5.1 Storage — SQLite 持久化

| 方法 | 职责 |
|------|------|
| `save_pool` / `get_pool` / `list_pools` / `delete_pool` | 池配置 CRUD |
| `save_execution` / `get_executions` | 执行记录 |
| `save_node_state` / `get_node_stocks` / `remove_stock_from_node` | 节点股票状态 |
| `log_stock_transfer` / `batch_log_stock_transfers` | 转移日志 |
| `create_replay_session` / `save_replay_snapshot` | 回放会话 |
| `save_pool_node` / `save_pool_edge` | 节点/边配置 |
| `save_kline` / `get_klines` | K线缓存 |
| `upsert_stocks` / `upsert_sectors` / `upsert_sector_members` | 股票/板块同步 |
| `upsert_user_block` / `update_user_block_members` | 用户自选股块 |
| `get_sectors_catalog` / `get_sector_members` | 板块查询 |
| `record_config_version` / `rollback_config` | 配置版本管理 |

### 5.2 DataQuery — 统一K线查询

| 方法 | 职责 |
|------|------|
| `get_kline_series` | symbol+period+end_time+count → K线序列 |
| `_load_history` | 从 parquet 加载历史 |
| `_load_today` | 从 Min1Aggregator 加载当日 |
| `_resample_minute_bars` | 1分钟→多周期重采样 |

### 5.3 DataSourceContract — 数据源契约

| 方法 | 职责 |
|------|------|
| `get_source` / `list_sources` | 查询源配置 |
| `probe_source` / `probe_or_raise` | 探测源可用性 |
| `grant_explicit_consent` | 授权 mock 显式使用 |
| `get_active_source` | 解析当前活跃源 |

### 5.4 FormulaRouter — 公式路由

| 方法 | 职责 |
|------|------|
| `eval` / `eval_outvars` | 单股求值(路由到 Python/HQChart) |
| `eval_batch` | 批量求值 |
| `_analyze_complexity` | 公式复杂度分析(简单/复杂) |
| `_resolve_engine` | 按规则路由到引擎 |
| `invalidate_on_minute_close` / `clear_all_cache` | 缓存失效 |

### 5.5 FormulaCache — 公式结果缓存

| 方法 | 职责 |
|------|------|
| `make_key` | 生成确定性缓存键 |
| `get` / `set` | 读写(自动TTL淘汰) |
| `invalidate_on_minute_close` | 分钟级失效 |
| `invalidate_daily` | 日级失效 |

### 5.6 Min1Aggregator — 分钟聚合器

| 方法 | 职责 |
|------|------|
| `on_tick` | 单 tick 聚合(numpy 预分配) |
| `on_tick_batch` | 批量 tick |
| `get_today_series` | 当日分钟序列 |
| `tier_symbols` | 层级股票列表 |

### 5.7 CandidatePoolResolver — 候选池解析器

| 方法 | 职责 |
|------|------|
| `resolve` | 按 spinfo_type(0~7) 解析候选池 |
| `resolve_type_0` ~ `_do_resolve_type_7` | 7种候选池类型解析 |
| `get_category_tree` | 板块分类树 |
| `build_from_sector` | 从板块构建 |
| `_fetch_with_fallback` | 多源降级拉取 |

### 5.8 TqAdapter — TQ 适配器门面

| 方法 | 职责 |
|------|------|
| `get_kline_data` / `get_snapshot` | K线/快照 |
| `get_sector_list` / `get_sector_stocks` | 板块数据 |
| `send_user_block` / `create_sector` | 自选股块操作 |
| `probe_and_assert` | 探测数据源 |
| `register_tick_callback` | 注册 tick 推送回调 |

### 5.9 HotReloadManager — 热重载管理器

| 方法 | 职责 |
|------|------|
| `detect_changes` | 检测文件变更 |
| `validate_and_swap` | 校验+原子替换 |
| `check_and_reload` | 检测+重载 |
| `rollback` | 版本回滚 |
| `start_polling` / `start_watchdog` | 启动监控 |

---

## 六、数据源 Provider 体系

| Provider | 基类 | 数据源 |
|----------|------|--------|
| `TqDllProvider` | DataSourceProvider | 通达信 DLL(ctypes) |
| `TqSdkProvider` | DataSourceProvider | 天勤 SDK |
| `TqProvider` | DataSourceProvider | TQ 门面(委托 DLL/SDK) |
| `MockProvider` | DataSourceProvider | 确定性随机模拟 |
| `HQChartProvider` | DataSourceProvider | HQChart C++引擎 |
| `AkShareProvider` | DataSourceProvider | AKShare 开源数据 |
| `DfcfProvider` | DataSourceProvider | 东方财富(AKShare) |
| `LocalFileProvider` | DataSourceProvider | 本地文件(TDX/DZH/THS) |
| `DataSourceManager` | — | 动态加载+单源分发 |
| `DataSourceProvider` | ABC | 抽象基类(17个方法) |

**DataSourceProvider 抽象方法:**

| 方法 | 职责 |
|------|------|
| `is_ready` | 数据源是否就绪 |
| `get_mode_info` | 返回模式标识 |
| `resolve_market` | 市场代码→代码列表 |
| `get_kline_data` | K线数据 |
| `get_snapshot` | 实时快照 |
| `get_block_members` | 板块成员 |
| `get_stock_list_by_type` | 按类型获取股票列表 |
| `get_sector_list` | 板块列表 |
| `get_sector_stocks` | 板块成分股 |
| `eval_indicator` | 指标公式求值 |
| `eval_formula_xg` | 选股公式求值 |
| `eval_formula_zb` | 指标公式求值(扩展) |
| `send_user_block` | 保存自选股块 |
| `create_sector` / `clear_sector` | 创建/清空板块 |
| `get_financial_data` | 财务数据 |
| `get_replay_data` | 回放数据 |
| `resample_kline` | K线重采样 |

---

## 七、表驱动引擎 (core/table_engine.py)

| 类 | 职责 |
|----|------|
| `ConfigStore` | 30张JSON配置表: 加载/校验/热重载/锁/回滚 |
| `RuleEngine` | 规则触发: guard检查+handler分发 |
| `DataBinder` | 数据绑定: dot-path读写/位标志编解码/动作编解码 |
| `PanelGenerator` | 面板生成: layout→field→component 解析+校验 |
| `PropertyOwnershipManager` | 属性归属: 池类型→节点类型→属性白名单 |

---

## 八、验证层 (native/validators.py)

| 类 | 层级 | 职责 |
|----|------|------|
| `SyntaxValidator` | L1 | JSON格式/必填字段/数据类型 |
| `LogicValidator` | L2 | 字段依赖/互斥/枚举/位标志 |
| `BusinessValidator` | L3 | 归属一致性/handler引用/类型映射 |
| `SchemaValidator` | 编排器 | 三级校验统一入口 |
| `ConfigIntegrityValidator` | 全覆盖 | 跨表引用+完整覆盖校验 |
| `TableLoader` | 热重载 | 文件监控+自动重载+校验 |

---

## 九、UI/Web 层

前端（`web/` 目录）仅作为**展示层**，不参与业务真值计算。浏览器端负责：

1. 按 `config/ui/*.json` 配置表渲染节点、边、属性面板、工具栏、右键菜单；
2. 将用户操作转发到后端 API；
3. 通过 `EventSource('/api/events/stream')` 订阅后端 SSE 事件流；
4. 维护少量纯界面状态（面板折叠、画布缩放、选中项、滚动位置）。

后端持有全部业务真值源：股票池节点、运行时状态、事件队列、计时器队列、模式状态。

| 类 | 职责 |
|----|------|
| `UIRenderer` | 订阅EventBus→格式化→推送WebSocketPublisher |
| `WebSocketPublisher` | 管理WS客户端+广播消息 |
| `SnapshotBuilder` | (core层) 增量维护视图快照 |

**UI 事件→消息映射:**

| EventBus 事件 | UI 消息类型 |
|---------------|-----------|
| Executed | `"executed"` |
| DomainEvent | `"domain"` |
| Signal | `"signal"` |
| DataChanged | `"data_changed"` |

**唯一路径约束**：
- 表驱动 UI 唯一路径：`config/ui/*.json`；
- 事件驱动唯一路径：`EventSource('/api/events/stream')` + `core/event_bus.py` 事件契约；
- 设计/仿真/回放/实盘四种模式共享同一条执行路径，仅数据源与时间推进机制不同（`runtime_modes.json` + `time_sources.json`）。

---

## 十、模拟/回放层

| 类 | 职责 |
|----|------|
| `RuntimeSimulator` | 仿真运行：mock股票+tick生成+状态管理 |
| `KLineReplayEngine` | K线回放：历史数据+步进+速度控制+快照 |
| `PaperTradeEngine` | 模拟交易：买卖/持仓/盈亏 |
| `MockStock` | mock 股票数据 |

---

## 十一、Schema/配置模型层 (core/schemas.py)

| 模型 | 职责 |
|------|------|
| `DynamicCellModel` | DZH/TDX 节点动态属性模型 |
| `DynamicFlowModel` | 边属性位标志模型(流向/模式/动作) |
| `Cell200AttrBitsModel` | 状态池位标志 |
| `Cell201AttrBitsModel` | 条件节点位标志 |
| `Cell202AttrBitsModel` | 候选池位标志 |
| `PositionModel` | 位置模型 |
| `TradeAttrModel` | 交易属性 |
| `ActionModel` | 动作模型 |
| `StockSnapshotModel` | 股票快照 |
| `PoolMetaModel` | 池元信息 |
| `TdxFuncModel`/`TdxPsattModel`/`TdxSpinfoModel`/`TdxStkModel`/`TdxCellModel`/`TdxFlowModel` | TDX格式模型 |

---

## 十二、API 端点摘要

| 路径前缀 | 标签 | 职责 |
|----------|------|------|
| `/api/meta` | 元数据 | 池列表/数据源/模块/条件 |
| `/api` | 执行 | 池执行/运行控制 |
| `/api/dzh` | DZH | DZH格式导入导出 |
| `/api/json` | JSON导入导出 | JSON格式池操作 |
| `/api/sim` | 仿真 | 仿真会话控制 |
| `/api/replay` | 回放 | 回放会话控制 |
| `/api/table` | 配置表 | 配置CRUD |
| `/api/formula` | 公式 | 公式求值/测试 |
| `/api/tdx` | TDX | TDX格式池操作 |
| `/api/pool/{name}` | 池运行时 | node_stocks/events/signals/replay/simulation |
| `/api/data_source` | 数据源 | 状态查询/切换 |
| `/api/registry` | 注册表 | cell-types/modules/defaults等 |
