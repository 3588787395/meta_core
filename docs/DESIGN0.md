# DESIGN0 — 架构合同

## 1 核心原则

**程序 = MetaEngine（唯一解释器）+ 三种表**

- 表 = 可查询、可写入、固定 Schema 的数据结构。内存 Dict 也是表。不按介质区分，只按角色区分。
- 引擎不含领域知识。只做：读表 → 计算 → 写表。
- 新增功能 = 加 JSON 条目，零行 `engine.py` 改动。

---

## 2 三种表

### 2.1 持久表（SQLite）—— 审计影子

记录"发生过什么"，不影响当下决策。写多读少，跨进程。

| 表名 | 职责 |
|------|------|
| `pool_config` | 图拓扑元数据（名称、类型、状态） |
| `pool_node` | 节点定义（`pool_config` 的结构化镜像） |
| `pool_edge` | 边定义（流转参数的结构化镜像） |
| `node_state` | 节点运行时股票进出记录（运行时表的影子） |
| `stock_transfer_log` | 股票流转日志（只 INSERT，永不 UPDATE） |
| `config_version` | 配置变更版本（只 INSERT，审计追溯） |
| `kline_cache` | K线缓存（运行时只读，预填充） |
| `replay_session` | 回放会话元数据 |
| `replay_snapshot` | 回放快照（节点状态 + 事件 + K线数据） |

### 2.2 运行时表（内存 Dict）—— 真相源

**`node_stocks[nid]` 是核心核心。** 运行时表是此刻的真实状态，持久表只是它的影子。

| 表名 | 类型 | 职责 |
|------|------|------|
| `node_stocks[nid]` | `Dict[str, List]` | **最核心。** key=节点ID，value=股票列表。池中此刻真实存在的股票 |
| `_flow_duration_starts[eid]` | `Dict[str, datetime]` | 边首次执行时刻。cxtype=1 持续时长窗口的起始锚点 |
| `_flow_exec_counts[eid]` | `Dict[str, int]` | 边已执行次数。cxtype=2 只执行一次的计数依据 |
| `_pool_start_time` | `datetime` | 当前池执行起始时刻。starttype=1 延迟计算的零点 |
| `tevs` | `List[dict]` | 本次执行的流转事件列表。每边执行后追加。返回给调用方 |

### 2.3 配置表（JSON 文件）—— 规则即代码

定义"怎么执行"。启动时加载，运行时只读，跨版本存在。

**参与引擎核心循环的 7 张**：

| 文件 | 引擎读入点 | 职责 |
|------|-----------|------|
| `timing.json` | `_tdx_should_execute` / `_tdx_check_duration` | starttype × cxtype 的 24 种时间调度规则 |
| `edge_strategies.json` | `_execute_flowsCore` 策略路由 + `_init_node_stocks` 节点初始化 | source_type:target_type → handler 映射 + node_init 规则 |
| `dispatch.json` | `_rebuild_dispatch` 构建 `dispatch_index` | 位掩码 → 网关名称的条件分发路由 |
| `engines.json` | 引擎索引构建 | 网关 → 底层引擎映射（周期、数据源、兼容网关） |
| `modules.json` | 模块索引构建 | node_type → handler 的类型系统定义 |
| `tdx_psatt.json` | `_apply_tdx_psatt_ttl` | TTL 自动删除规则（bdel/ndelnum/ndeltype 驱动） |
| `fallback_chain.json` | builtins.py 各 filter 函数降级分支 | TQ 不可用时的降级链（bar_data → random） |

**引擎核心循环之外**（UI、校验、导出等）的配置表不在此列。但以下两张仍在引擎启动路径中读取：
- `action_table.json` — 目标池回调（bsavehis/bsound/btip/bsavetoblock），由 app.py 读取
- `cell_type_registry.json` — 节点类型注册表，含 `type_aliases`
- `property_ownership.json` — 属性归属配置，含 `type_name_mapping`
- `behavior_actions.json` — handler 注册表中的行为动作
- `pool_types.json` — 池类型映射
- `defaults.json` — 参数别名
- `dzh_type_map.json` — 大智慧类型映射
- `field_definitions.json` — 位标志编解码

共计 15 张引擎级和 27 张总配置表。

---

## 3 引擎核心循环

**核心函数：`MetaEngine._execute_flowsCore()`**

```
① 初始化运行时表
   读 node_init 配置（edge_strategies.json） → 写 node_stocks[nid]、_pool_start_time

② 逐边执行（核心六步）：
   ┌─ gate ────────────────────────────────────────────────────────────
   │  读 timing.json（starttype_rules + cxtype_rules）
   │  + 读 _flow_duration_starts[eid]
   │  + 读 _flow_exec_counts[eid]
   │  → bool（是否该执行这条边）
   │
   ├─ filter ──────────────────────────────────────────────────────────
   │  读 node_stocks[src]
   │  + 读 dispatch.json（位掩码 → 网关）
   │  + 读 engines.json（网关 → 底层引擎）
   │  + 调外部 API（TQ SDK 公式评估）
   │  → {passed, rejected}
   │
   ├─ propagate ───────────────────────────────────────────────────────
   │  读 node_stocks[src]
   │  + 读 node_stocks[tgt]
   │  → 写 node_stocks[tgt]（copy / move / overwrite）
   │  → 写 node_stocks[src]（若 move 则清空）
   │
   ├─ callback ────────────────────────────────────────────────────────
   │  读 node_stocks[tgt]
   │  + 读 action_table.json（pool_enter_actions）
   │  → 写 node_state（持久表影子上报）
   │  + 写 stock_transfer_log（流转日志）
   │  + 执行副作用（bsavehis→写文件, bsound→日志, btip→日志, bsavetoblock→写板块文件）
   │
   ├─ ttl ─────────────────────────────────────────────────────────────
   │  读 tdx_psatt.json（ttl_units / auto_ttl_node_types）
   │  + 读 node_stocks[tgt]（每只股票的 indate + intime）
   │  → 写 node_stocks[tgt]（删除超时股票）
   │
   └─ update ──────────────────────────────────────────────────────────
       写 _flow_exec_counts[eid] += 1

④ 输出结果
   读 node_stocks
   → 过滤 output_types（edge_strategies.json）
   → 输出 {node_id: {label, type, stock_count, stocks}}
```

**伪代码精要**（`engine.py` 实际结构）：

```python
def _execute_flowsCore(self, nodes, edges, node_stocks, current_bar_data):
    for edge in edges:
        if not gate(edge): continue          # 读 timing.json + 运行时表
        strat = edge_strategies[key]          # 读配置表
        handler = _handler_registry[strat.handler]
        result = handler(action_inputs)       # filter + propagate
        callback(tid, node_stocks, new)       # 读 action_table.json → 写持久表
        ttl(tid, node_stocks)                 # 读 tdx_psatt.json → 写运行时表
        _flow_exec_counts[eid] += 1           # 写运行时表
    return node_stocks, tevs
```

**这段代码不含任何股票代码、市场类型、指标名称等字面量。** 新增条件类型？改 `dispatch.json`。新增时机？改 `timing.json`。零行 Python。

---

## 4 策略空间

**策略空间 = 时机轴 × 强弱轴**

所有选股策略都是此空间中的一个点。两个轴已完全由配置表驱动。

### 4.1 时机轴（gate 函数）

`starttype(0~7) × cxtype(0~2) = 24 种组合`，全部通过 `timing.json` 驱动。

| starttype | 含义 | 计算方式 |
|-----------|------|---------|
| 0 | 立即 | `always` — 无条件放行 |
| 1 | 延迟 N 秒 | `elapsed_gte` — `_pool_start_time` 起算 |
| 2 | 开市前 | `in_range` — 开市前 N 秒窗口内 |
| 3 | 开市后 | `gte` — 开市时间 + N 秒后 |
| 4 | 收市前 | `in_range` — 收市前 N 分钟窗口内 |
| 5 | 收市后 | `gte` — 收市时间 + N 分钟后 |
| 6 | 交易时间 | `gte_hhmmss` — 到达指定 HHMMSS 后 |
| 7 | 指定时间 | `gte_hhmmss` — 到达指定 HHMMSS 后 |

| cxtype | 含义 | 过期判断 | 跟踪表 |
|--------|------|---------|--------|
| 0 | 一直执行 | `never` — 永不超时 | — |
| 1 | 执行 N 秒 | `elapsed_gte` — 首次执行后计时 | `_flow_duration_starts` |
| 2 | 只执行一次 | `count_gte_1` — 执行次数 ≥1 | `_flow_exec_counts` |

### 4.2 强弱轴（filter 函数）

`nset(0~5) × noperate(0~9) = 60 种组合`，通过以下链条驱动：

| 层级 | 驱动表 | 职责 |
|------|--------|------|
| 路由 | `dispatch.json` | 位掩码匹配 → 网关名称 |
| 网关 | `engines.json` | 网关 → 底层引擎（周期/数据源） |
| 评估 | `tdx_evaluators.py` | nset 对应的实际评估逻辑 |

| nset | 含义 | noperate 0~9 | 评估器 |
|------|------|-------------|--------|
| 5 | 直通（无筛选） | — | 直接透传全部股票 |
| 0 | 技术指标序列 | 等于/大于/小于/金叉/死叉/持股N周期/排名前N/排名后N/上拐/下拐 | `eval_nset0_indicator` |
| 1 | 条件选股公式 | 信号判断 | `eval_nset1_condition_formula` |
| 2 | 专家系统 | 任意信号/买入信号/卖出信号 | `eval_nset2_expert_system` |
| 3 | 最新财务标量 | 等于/大于/小于/排名前N | `eval_nset3_financial_scalar` |
| 4 | 实时行情标量 | 等于/大于/小于/排名前N | `eval_nset4_market_scalar` |
| — | 集合运算 | 并集/差集/交集 | `eval_nset5_set_operation` |

---

## 5 反模式清单

| # | 位置 | 反模式 | 正确做法 |
|---|------|--------|---------|
| 1 | `engine.py` L250-257 | `_dispatch_tdx_condition` 中 nset→evaluator 硬编码字典：`{0: tdx_evaluators.eval_nset0_indicator, 1: ..., 2: ...}` | 映射进 `dispatch.json` 配置表，engine 只做查表路由 |
| 2 | `tdx_evaluators.py` | `eval_nset3_financial_scalar` / `eval_nset4_market_scalar` / `eval_nset5_set_operation` 在 TQ 不可用时各有自己的 mock 降级分支（函数内部 if/else） | 统一走 `fallback_chain.json` 降级链，去除 evaluator 内的 mock 分支 |
| 3 | `builtins.py` L66-69 | `_random_filter` 函数独立实现随机过滤，与 `_resolve_fallback` 的 `always→_random_filter` 逻辑重复 | 所有调用方统一走 `_resolve_fallback("chain_name", ...) → fallback_chain.json` |
| 4 | `app.py` L616-625 | `_STOCK_NAMES` 从 `mock_data.json` 加载但硬编码在 app.py 全局作用域 | `_get_stock_name` 应查表而非依赖全局 Dict |
| 5 | `engine.py` | 任何 `if type == "xxx"` / `if nset == X` / `if starttype == Y` 类分支 | 所有映射进 JSON 配置表 |
| 6 | 任意文件 | `from xxx_native import yyy` 显式导入领域函数 | 通过 `_handler_registry` 字典动态查表调用 |

---

## 6 验收清单

- [x] `engine.py` 行数 ≤ 380（实际 365）
- [x] `timing.json` 驱动 gate — `_tdx_should_execute` / `_tdx_check_duration` 已配置表化
- [x] `tdx_psatt.json` 驱动 TTL — `_apply_tdx_psatt_ttl` 已配置表化
- [x] `fallback_chain.json` 驱动降级 — `_resolve_fallback` 已统一走配置表链
- [x] `action_table.json` 驱动回调 — `pool_enter_actions` 已配置表化
- [x] `edge_strategies.json` 驱动策略路由 — 全部 `source_type:target_type → handler` 已配置表化
- [x] `dispatch.json` 驱动条件分发 — 位掩码 → 网关映射已配置表化
- [x] `cell_type_registry.json` 包含 `type_aliases`
- [x] `property_ownership.json` 包含 `type_name_mapping`
- [ ] `engine.py` 中 `_dispatch_tdx_condition` 的 nset 硬编码字典应配置表化
- [ ] `tdx_evaluators.py` 各 nset 的 mock 降级逻辑应进 `fallback_chain.json`
- [x] 所有 filter 函数返回 `{passed, rejected}` 统一格式
- [ ] 新增功能对 `config/` 下有明确记录（JSON 条目级 diff 可追溯）

---

## 7 事件驱动架构合同

### 7.1 核心原则

**模块间通信唯一合法通道：EventBus**

- **禁止直接import**：除 `core.event_bus` 和 `core.domain` 白名单外，业务模块不得直接 import 其他业务模块
- **模块只知道两件事**：EventBus 实例 + 事件类型定义
- **发布/订阅解耦**：模块通过 `bus.publish(event)` 发布事件，通过 `bus.subscribe(EventType, handler)` 订阅事件
- **同步执行模型**：`publish()` 同步调用所有订阅者，订阅者异常由 EventBus 隔离（logger.warning），不中断主流程

### 7.2 事件类型清单

事件类型统一定义在 `core/event_bus.py`，按领域分类：

| 分类 | 事件类型 | 触发时机 | 关键字段 |
|------|---------|---------|---------|
| 生命周期 | ConfigLoaded / ConfigChanged | 配置加载/变更 | config_tables / changed_tables |
| 生命周期 | PoolLoaded | 股票池加载完成 | pool_config, source_format |
| 行情数据 | TickReceived | Tick 接收 | tick_data, code, ts |
| 行情数据 | DataChanged | 行情/K线数据变更 | ts, bar_hash, codes, source, period |
| 行情数据 | BarComposed | K线合成完成 | bar, period, code, ts |
| 公式计算 | FormulaEvaluated | 公式求值完成 | formula_ref, result, code, bar_hash |
| 公式计算 | CrossOverDetected | 交叉穿越检测 | code, cross_type(golden/death), formula_ref |
| 筛选执行 | StockFiltered | 股票过滤完成 | eid, passed, rejected, filter_ref |
| 边调度 | EdgeFired | 边触发 | eid, ts（G3：不携带 changed_codes） |
| 边调度 | TransferExecuted | 转移执行完成 | src, tgt, codes, mode, entered_codes, exited_codes |
| 边调度 | TTLExpired | TTL 过期 | node_id, codes, ts |
| 交易 | OrderPlaced / OrderFilled | 下单/成交 | order/fill: {code,side,qty,price,...} |
| 交易 | PositionUpdated | 持仓更新 | tracker: {node_id,code,entry_price,cur_price,qty,pnl} |
| 统计监控 | StatisticsUpdated / RankingChanged / AlertRaised | 统计/排名/告警 | stats/rankings/alert |
| 持久化 | SnapshotUpdated / EventLogged | 快照/日志 | snapshot/event |
| 模式切换 | ModeChanged / TimeAdvanced | 模式/时间推进 | mode_id/ts,source |
| 回放仿真 | ReplayStarted / ReplayStep / SimulationStep | 回放/仿真步骤 | session/step |
| 导入导出 | ImportStarted / ExportCompleted | 导入/导出 | format,path,count |
| 兼容层 | Executed / DomainEvent / Signal | 向后兼容事件 | 原有字段保持不变 |

### 7.3 事件订阅约束

- **每个模块仅订阅自身职责相关的事件**，不得订阅无关事件
- **事件 handler 必须快速返回**，禁止阻塞操作（阻塞操作应异步或放入队列）
- **handler 内异常不得抛出**，由 EventBus 统一捕获并记录 warning 日志
- **禁止在 handler 中直接修改事件对象**，事件视为不可变

### 7.4 前端事件面板合同

事件面板是前端可视化组件，通过 SSE/WebSocket 订阅后端 `EventLogged` / `SnapshotUpdated` / `TimerQueued` 等事件，禁止直接访问后端运行时表。

#### 7.4.1 统一 Y 轴语义

- 事件面板支持 **分类显示（矩阵）** 与 **全部显示（散点）** 两种视图
- 两种视图的 **Y 轴语义完全相同**，均按 9 种事件分类作为垂直分轨：
  `Tick / Bar / Formula / Edge / Transfer / Signal / Order / TTL / System`
- 切换视图时仅改变 X-Y 布局，不改变分类编码、颜色、图标与筛选状态

#### 7.4.2 事件分类与来源

| 分类 | 事件来源（示例） | 图标颜色 |
|------|----------------|---------|
| Tick | `TickReceived`, `DataChanged(tick)` | 灰色 |
| Bar | `BarComposed`, `DataChanged(bar)` | 蓝色 |
| Formula | `FormulaEvaluated`, `StockFiltered` | 绿色 |
| Edge | `EdgeFired`, `CrossOverDetected` | 橙色 |
| Transfer | `TransferExecuted`, `Executed` | 紫色 |
| Signal | `Signal(BUY/SELL)` | 红色 |
| Order | `OrderPlaced`, `OrderFilled`, `PositionUpdated` | 黄色 |
| TTL | `TTLExpired`, `TimerQueued`, `Timeout` | 暗红色 |
| System | `ModeChanged`, `TimeAdvanced`, `PoolLoaded` | 青色 |

#### 7.4.3 定时器队列可视化

- `TimerQueued` 事件必须展示在独立定时器队列区域
- 时间分布图以 `fire_at` 为 X 轴、单一轨道为 Y 轴，所有排队事件沿时间轴分布
- `fire_at` 单位必须在前端归一化为毫秒后再参与时间轴计算
- 已过期但未处理的排队事件用红色虚线框标识

#### 7.4.4 状态持久化

- 面板位置、高度、折叠/展开状态、隐藏状态必须持久化到 `localStorage`
- 页面刷新后自动恢复，不依赖后端状态

#### 7.4.5 性能约束

- `render()` 必须通过 `setTimeout` 实现 200ms 渲染节流
- 高频事件下禁止每事件立即重建 DOM/Canvas
- 可视化绘制使用 Canvas，避免大量 DOM 节点

---

## 8 核心模块清单及职责

`core/` 目录下每个 `.py` 文件对应一个独立模块，模块间通过 EventBus 通信：

| 模块文件 | 核心类 | 职责 | 订阅事件 | 发布事件 |
|---------|-------|------|---------|---------|
| `domain.py` | Node/Edge/Spec/Evaluator/TickSource | 领域模型定义、配置加载、内置公式查找、nset/noperate 规则表、受控表达式求值器 | 无（纯数据/工具模块） | 无 |
| `event_bus.py` | EventBus + 30+ Event dataclass | 事件总线、事件类型定义、事件订阅/发布/查询日志 | 无（基础设施） | 无（被动调用） |
| `runtime_mode_module.py` | RuntimeModeModule / RuntimeSimulator / KLineReplayEngine / DirtyState / PoolState | 三模式（实盘/回放/仿真）统一入口、仿真核心逻辑、K线回放引擎、脏标记管理、运行时状态容器 | ModeChanged / ReplayStarted | DataChanged / TimeAdvanced / ReplayStep / SimulationStep |
| `tick_bar_module.py` | TickBarModule / DataUpdater / BarComposer | Tick 接收（实盘/仿真）、多周期K线合成（1m/5m/15m/30m/60m/1d）、行情数据更新 | DataChanged(tick) / ReplayStep / SimulationStep | DataChanged(bar) / BarComposed |
| `formula_module.py` | PythonFormulaEngine / FormulaRouter / FormulaModule | 纯Python公式引擎、公式路由、算子实现（cross_op/window_op/shift_op/ema_op/sma_op等）、LRU编译缓存 | DataChanged / BarComposed / PoolLoaded | FormulaEvaluated / CrossOverDetected |
| `screening_module.py` | ScreeningModule | 股票筛选器、通达信func 16参数完整解析、nset 0-5路由、noperate 0-9操作执行、交集/并集/差集集合运算 | FormulaEvaluated / PoolLoaded | StockFiltered |
| `execution_module.py` | ExecutionModule / Compiler / EdgeExecutor / EventDriver / EdgeState | 执行引擎、边调度（gate→filter→propagate→ttl）、集合运算（copy/move/overwrite）、G1 统一 heapq 到时事件驱动（TTL 注册到主队列，无独立 TtlTracker）、EdgeExecutor 订阅 EdgeFired 执行条件节点激活 | EdgeFired / DataChanged / TimeAdvanced / PoolLoaded | EdgeFired / StockFiltered / TransferExecuted / TTLExpired / Signal / Executed / DomainEvent |
| `trade_module.py` | TradeModule | 交易模块、买入/卖出市价单、持仓管理、盈亏跟踪 | Signal(BUY/SELL) | OrderPlaced / OrderFilled / PositionUpdated |
| `import_export_module.py` | ImportExportModule | 持久化/导入导出、DZH/TDX XML解析、JSON格式转换 | ImportStarted / PoolLoaded | ExportCompleted / ConfigChanged |
| `monitoring_module.py` | MonitoringModule | 监控/事件记录、性能统计、事件面板数据源 | 所有事件类型 | StatisticsUpdated / AlertRaised / SnapshotUpdated / EventLogged |
| `engine.py` | PoolEngine | 兼容层引擎（向后兼容旧API）、表驱动调度 | （兼容层，逐步迁移至上述模块） | （兼容层） |
| `table_engine.py` | TableEngine | UI表格引擎、数据展示格式化 | 无（UI工具） | 无 |
| `schemas.py` | Pydantic 模型 | Pydantic 数据模型、输入验证 | 无 | 无 |

> **注意**：`domain.py` 和 `event_bus.py` 是白名单基础模块，所有其他模块均可 import；其余模块间禁止直接 import，必须通过 EventBus 通信。

---

## 9 仿真模式与实盘模式统一

### 9.1 统一原则

**除 TickSource 不同外，所有代码路径完全一致**

| 维度 | 实盘模式 | 回放模式 | 仿真模式 |
|------|---------|---------|---------|
| TickSource | RealTickSource（外部行情推送） | KLineReplayEngine（历史K线驱动） | MockDataSource（随机生成，G5 重命名） |
| 时间源 | wall_clock（系统墙钟） | sequence（K线时间戳序列） | virtual（虚拟时钟，可倍速） |
| 股票代码前缀 | 无前缀（如 SH600000） | 无前缀 | fz前缀（如 fzSH600000，避免与实盘代码冲突） |
| 公式计算 | 完全相同 | 完全相同 | 完全相同 |
| 筛选逻辑 | 完全相同 | 完全相同 | 完全相同 |
| 边调度 | 完全相同 | 完全相同 | 完全相同 |
| TTL 淘汰 | 完全相同 | 完全相同 | 完全相同 |
| 事件流 | 完全相同 | 完全相同 | 完全相同 |

### 9.2 fz 前缀股票代码

仿真模式下，所有股票代码自动添加 `fz` 前缀（如 `fzSH600000`、`fzSZ000001`），目的是：
- 避免仿真交易与实盘交易代码冲突
- 便于在监控面板区分实盘/仿真持仓
- 数据源层自动处理前缀映射（仿真数据源识别 fz 前缀，使用 MockStock 数据）

代码前缀归一化由 `core.domain._normalize_to_fz()` / `_strip_fz_prefix()` 统一处理。

---

## 10 公式插件系统合同

### 10.1 核心原则

**所有指标计算必须通过 formula_module，业务代码不得直接实现 CROSS/MA/EMA 等算子**

### 10.2 表驱动路由

算子通过 `config/data/formula_funcs.json` 表驱动加载：

```json
{
  "funcs": {
    "MA":  {"handler": "window_op", "arg_spec": [{"idx":0,"cast":"series"},{"idx":1,"cast":"int"}], "agg_method": "mean"},
    "EMA": {"handler": "ema_op",    "arg_spec": [{"idx":0,"cast":"series"},{"idx":1,"cast":"int"}]},
    "SMA": {"handler": "sma_op",    "arg_spec": [{"idx":0,"cast":"series"},{"idx":1,"cast":"int"},{"idx":2,"cast":"int"}]},
    "REF": {"handler": "shift_op",  "arg_spec": [{"idx":0,"cast":"series"},{"idx":1,"cast":"int"}]},
    "CROSS": {"handler": "cross_op", "arg_spec": [{"idx":0,"cast":"series"},{"idx":1,"cast":"series"}], "direction_field": "direction"},
    "HHV": {"handler": "window_op", "arg_spec": [{"idx":0,"cast":"series"},{"idx":1,"cast":"int"}], "agg_method": "max"},
    "LLV": {"handler": "window_op", "arg_spec": [{"idx":0,"cast":"series"},{"idx":1,"cast":"int"}], "agg_method": "min"},
    "SUM": {"handler": "window_op", "arg_spec": [{"idx":0,"cast":"series"},{"idx":1,"cast":"int"}], "agg_method": "sum"},
    "COUNT": {"handler": "window_op", "arg_spec": [{"idx":0,"cast":"series"},{"idx":1,"cast":"int"}], "agg_method": "sum", "agg_override": "bool_sum"},
    "STD": {"handler": "window_op", "arg_spec": [{"idx":0,"cast":"series"},{"idx":1,"cast":"int"}], "agg_method": "std", "agg_kwargs": {"ddof": 1}},
    "ABS": {"handler": "abs_op",    "arg_spec": [{"idx":0,"cast":"series"}]},
    "MAX": {"handler": "max_op",    "arg_spec": [{"idx":0,"cast":"series"},{"idx":1,"cast":"series"}]},
    "MIN": {"handler": "min_op",    "arg_spec": [{"idx":0,"cast":"series"},{"idx":1,"cast":"series"}]},
    "IF":  {"handler": "if_op",     "arg_spec": [{"idx":0},{"idx":1},{"idx":2}]},
    "SAR": {"handler": "sar_op",    "arg_spec": [{"idx":2,"cast":"int"},{"idx":3,"cast":"int"},{"idx":4,"cast":"int"}], "context_fields": ["high","low"]}
  }
}
```

### 10.3 算子实现合同

- **通用算子优先**：优先使用 `window_op` / `shift_op` / `cross_op` 三个通用算子，通过 `agg_method` 配置差异
- **向量化实现**：所有算子必须使用 numpy/pandas 向量化实现，禁止逐根K线 Python 循环
- **n=0 语义**：窗口大小 n=0 表示 expanding 窗口（从首个数据点到当前点的累计计算），与通达信语义一致
- **NaN 传播**：算子必须正确处理 NaN 值，不得抛出异常
- **LRU 缓存**：公式编译结果缓存于 `PythonFormulaEngine._compiled_cache`，默认容量 1000

### 10.4 业务代码禁令

业务代码（screening/execution/trade 等模块）严格禁止：
- ❌ 直接实现 MA/EMA/CROSS 等指标计算逻辑
- ❌ 硬编码指标名称或参数
- ❌ 绕过 FormulaRouter 直接调用 tq_adapter
- ✅ 必须通过 `FormulaRouter.eval_batch()` 批量求值
- ✅ 必须通过 `formula_funcs.json` 配置添加新算子
- ✅ 内置公式通过 `builtin_formulas.json` 查找，accode 为索引键

---

## 11 批量事件模型

### 11.1 核心原则

**批量事件携带 changed_codes 列表而非单只股票事件，避免 N 只股票触发 N 次事件的性能问题**

### 11.2 批量事件字段约定

| 事件类型 | 批量字段 | 含义 | 特殊值语义 |
|---------|---------|------|-----------|
| DataChanged | codes: List[str] | 本批次有数据更新的股票代码列表 | 空列表=无变化；非空=增量更新 |
| BarComposed | code: str（单条） | 单只股票单周期K线闭合 | 逐代码逐周期发布 |
| EdgeFired | （G3：无 changed_codes，只含 eid+ts） | 边触发信号 | 脏股票由 EdgeExecutor 从 source_pool.get_dirty_codes() 取（G4 StatePoolView） |
| StockFiltered | passed/rejected: List[str] | 通过/未通过筛选的股票列表 | 空列表=无通过/无拒绝 |
| TransferExecuted | codes/entered_codes/exited_codes: List[str] | 转移/新入池/离开的股票列表 | entered_codes=本次新入池；exited_codes=本次被覆盖/TTL删除 |
| TTLExpired | codes: List[str] | TTL过期被删除的股票列表 | 空列表=无过期 |

### 11.3 脏股票增量筛选机制（G3/G4）

EdgeFired 不携带 changed_codes（G3）。脏股票由 EdgeExecutor 从源池 StatePoolView.get_dirty_codes() 取（G4）：

1. **脏股票来源**：`source_pool.get_dirty_codes() = state.changed_codes ∩ 本池股票`
2. **first_run 兜底**：脏股票为空且首次运行时，用源池全量股票
3. **增量评估**：仅对脏股票重新评估公式，未变化股票复用上一次结果
4. **合并规则**：`passed = (cached_passed - dirty_codes) | newly_passed`

state.changed_codes 是全局脏股票唯一真相源（tick 表变更列），状态池是视图不独立维护脏股票集合。

---

## 12 脏标记机制（DirtyState）

### 12.1 DirtyState 字段定义

`DirtyState` 位于 `runtime_mode_module.py`，是增量计算的核心：

| 字段 | 类型 | 含义 |
|------|------|------|
| nodes | Dict[str, bool] | 节点脏标记：key=node_id，value=True表示该节点状态有变化（入池/出池） |
| data | bool | 数据脏标记：True表示本tick有行情数据推进（Tick/Bar变化） |
| changed_codes | Set[str] | 本tick内有数据更新的股票代码集合 |

> **注意**：根据 `cleanup-deadcode-static-check` spec，`node_entered_codes` 和 `node_exited_codes` 已被移除（死代码清理），节点脏通过 `dirty.nodes` 布尔标记触发全量评估。

### 12.2 脏标记生命周期

```
① Tick/Bar 数据到达
   → DataUpdater.apply_data()
     → 检测到时间戳推进
       → dirty.data = True
       → dirty.changed_codes.add(code)  // 逐只添加

② 边触发（EdgeFired，G3 只含 eid+ts）
   → EdgeExecutor 从 source_pool.get_dirty_codes() 取脏股票（G4）
     → dirty_codes = state.changed_codes ∩ 源池股票
     → first_run 兜底：脏股票为空且首次 → 全量评估
     → 对 dirty_codes 中的股票增量筛选
     → 其余股票复用 formula_results 缓存

③ propagate 完成后
   → 若有股票入池/出池
     → dirty.nodes[tgt] = True  // 标记目标节点脏

④ 本tick所有边执行完毕
   → dirty.clear()
     → nodes = {}
     → data = False
     → changed_codes = set()
```

### 12.3 脏标记与缓存协作

| 缓存 | 键 | 失效条件 |
|------|-----|---------|
| formula_results | (formula_ref, bar_hash) | bar_hash 变化（K线闭合） |
| filter_inputs | eid | 源池股票集合变化 |
| compiled_cache | formula_text | LRU 容量淘汰（默认1000） |

---

## 13 通达信转移条件（func）完整16参数合同

通达信转移条件 `func` 对象包含16个参数，完整定义如下：

| 序号 | 参数名 | 类型 | 含义 | 适用nset |
|------|--------|------|------|---------|
| 1 | nset | int | 评估器类型选择（0-5），见第14节路由表 | 全部 |
| 2 | noperate | int | 操作符类型（0-9），见第15节操作符合同 | nset=0,3,4 |
| 3 | accode | str | 公式/指标/字段代码（如 "MA" / "MACD" / "PE"） | nset=0,1,2,3,4 |
| 4 | nfirst | float | 第一参数（如 MA 周期 N1） | nset=0 |
| 5 | nsecond | float | 第二参数（如 MA 周期 N2 / 比较阈值 / 排名N） | nset=0,3,4 |
| 6 | cfirst | str | 第一比较指标/字段（用于金叉/死叉双指标比较） | nset=0 (noperate=3,4) |
| 7 | csecond | str | 第二比较指标/字段（通常为常量或另一指标） | nset=0 (noperate=3,4) |
| 8 | nperiod | int | 分析周期（0=日线,1=周线,2=月线,3=多分钟,4=5分钟,5=15分钟,6=30分钟,7=60分钟,8=1分钟） | 全部 |
| 9 | ntjindexno | int | 字段索引（nset=3财务字段/nset=4行情字段的序号） | nset=3,4 |
| 10 | formula_script | str | 完整公式脚本（优先于accode查表） | nset=0,1,2 |
| 11 | formula_args | Dict | 公式命名参数（如 MACD 的 SHORT/LONG/MID） | nset=0,1,2 |
| 12 | bfilterst | bool | 是否剔除ST品种 | 全部 |
| 13 | bfiltercy | bool | 是否剔除创业板 | 全部 |
| 14 | bfilterkc | bool | 是否剔除科创板 | 全部 |
| 15 | bfilterbj | bool | 是否剔除北交所 | 全部 |
| 16 | nperiodnum | int | 多日线天数（nperiod=11时有效） | nset=0 |

### 13.1 参数优先级

- `formula_script` 非空 → 直接使用该脚本，忽略 accode
- `formula_script` 为空 → 通过 accode 查 `builtin_formulas.json` 获取脚本
- `nfirst/nsecond/cfirst/csecond` → 按 `formula_arg_priority` 配置顺序拼接为公式参数

---

## 14 nset 0-5 路由表

| nset | 名称 | Evaluator类 | noperate支持 | 数据来源 | 说明 |
|------|------|------------|-------------|---------|------|
| 0 | 技术指标序列 | IndicatorEvaluator | 0-9 全支持 | FormulaRouter.eval_batch() | MA/EMA/MACD/KDJ等技术指标，支持比较/金叉/拐点/排名 |
| 1 | 条件选股公式 | ConditionFormulaEvaluator | 无（公式自身返回0/1信号） | FormulaRouter.eval_batch() | 条件选股公式，值>0表示通过；排名模式(noperate=5-7)需线数据，eval_batch不支持 |
| 2 | 专家系统 | ExpertSystemEvaluator | 无（公式自身返回买/卖信号） | FormulaRouter.eval_batch() | 专家系统/交易系统公式，值>0表示有信号；排名模式不支持 |
| 3 | 最新财务标量 | FinancialScalarEvaluator | 0-7（等于/大于/小于/排名） | MarketDataPort（或current_bar_data回退） | PE/PB/ROE/EPS等30个财务指标，通过ntjindexno选择字段 |
| 4 | 实时行情标量 | MarketScalarEvaluator | 0-7（等于/大于/小于/排名） | MarketDataPort（或current_bar_data回退） | price/open/high/low/close/volume/pct_change等12个行情字段 |
| 5 | 集合运算 | SetOperationEvaluator | 0=并集,1=差集,2=交集 | 纯内存集合运算 | 对多个源池的股票集合做并/差/交，不依赖数据源 |

### 14.1 nset 路由实现

路由通过 `dispatch.json:nset_dispatch` 配置表驱动，`eval_nset_dispatch` 函数查表获取：
- `evaluator`：评估器函数（eval_formula_nset / eval_scalar_nset / eval_nset5_set_operation）
- `nset_cfg`：nset 特定配置（field_table / data_method / supports_derived 等）

---

## 15 noperate 0-9 操作符合同

noperate 逻辑由 `config/data/tdx_noperate_rules.json` 表驱动，通用比较器 `_eval_op` 按表字段执行：

| noperate | 名称 | mode | compare | 语义 | 公式表达（expr/prev_expr/curr_expr） |
|----------|------|------|---------|------|-----------------------------------|
| 0 | 等于 | compare | abs_lt | 当前值近似等于阈值（容差1e-8绝对/1e-4相对） | `abs_diff < tol` |
| 1 | 大于 | compare | gt | 当前值严格大于阈值 | `a > b` |
| 2 | 小于 | compare | lt | 当前值严格小于阈值 | `a < b` |
| 3 | 上穿（金叉） | compare | cross | 前一周期line1≤line2，当前周期line1>line2 | `line1[-2] <= line2[-2] AND line1[-1] > line2[-1]` |
| 4 | 下破（死叉） | compare | cross | 前一周期line1≥line2，当前周期line1<line2 | `line1[-2] >= line2[-2] AND line1[-1] < line2[-1]` |
| 5 | 排名为 | rank | rank | 精确第N名（处理并列，同值同名次） | tie_handling=exact_rank |
| 6 | 排名前N | rank | rank | 降序排列取前N名（不处理并列） | order=desc, tie_handling=none |
| 7 | 排名后N | rank | rank | 升序排列取前N名（即倒数后N名） | order=asc, tie_handling=none |
| 8 | 上拐（拐点向上） | inflection | inflection | 曲线由降转升：前一期斜率<0，当前斜率≥0 | `(line1[-2]-line1[-3])<0 AND (line1[-1]-line1[-2])>=0` |
| 9 | 下拐（拐点向下） | inflection | inflection | 曲线由升转降：前一期斜率>0，当前斜率≤0 | `(line1[-2]-line1[-3])>0 AND (line1[-1]-line1[-2])<=0` |

### 15.1 标量模式 vs 向量模式

- **向量模式**（nset=0 技术指标）：传入完整时间序列 line1/line2（list），支持金叉/拐点等需要历史数据的操作
- **标量模式**（nset=3/4 财务/行情）：仅传入当前值 value，prev_value 可选用（cross 模式），上拐/下拐(noperate=8,9) 标量模式不支持（返回空列表）

### 15.2 CROSS 信号快捷路径

若公式通过 `eval_field` 指定了 CROSS_* 或 XG 输出字段（如 CROSS_J_K / CROSS_DIF_DEA），则：
- 公式自身已计算穿越信号（值=0/1）
- 直接判断 v>0 即视为通过，忽略 noperate 比较逻辑
- 避免 noperate=0(等于) 误将 CROSS=0 判为通过、noperate=1(大于) 误将 CROSS=1 判为通过

---

## 16 条件节点模型与定时器中断驱动架构

### 16.1 G1 单一定时器中断驱动（确认）

系统只有 1 个 heapq 优先队列（`EventDriver._heap`），所有边触发、TTL 到期、tick 间隔注册到同一队列。

- heapq 元素：`[fire_time, seq, spec]`，seq 用于同时间稳定排序
- `fire_due(now)` 弹出堆顶到时事件，发布事件 + 立即注册下次
- `next_time = fire_time + interval`（非 now + interval），保证固定间隔
- interval=None 或 <=0 表示一次性（如 TTL），不注册下次
- 禁止 asyncio.sleep、轮询、线性扫描、at_fn 延迟求值
- 不存在 `TtlTracker` 独立 heapq，TTL 注册到主队列（kind="ttl" 一次性）
- 不存在 `is_edge_due` / `fire_ttl_due` 独立方法

### 16.2 G6 事件无序（确认）

- 删除 `execution_order` 运行时拓扑排序（不存在 `_build_execution_order`）
- 运行时事件没有顺序，哪个定时器先到时先触发
- 边顺序号 `_order` 保留，用于多入边交集/差集运算次序（设计结构，非运行时排序）

### 16.3 条件节点拓扑

条件节点（type=condition）承载 func/indi/indiparam/filter_spec，边只承载触发频率（入边）+ 顺序号（多入边交集/差集）。实例拓扑示例：

```
source(100只fz股票) → ec1(60s) → cond1(KDJ金叉) → ec1out → pool_A(TTL=6000s)
source → ec2(10s) → cond2(MACD金叉) → ec2out → pool_B(TTL=12000s)
pool_A → ec3a(5s,_order=1) → cond3(交集) ← ec3b(5s,_order=2) ← pool_B
cond3 → ec3out → pool_C(TTL=1200s,enter=buy 100,exit=sell_all)
```

- `source/状态池→condition` 用 conditional 边（含 time_gate_interval）
- `condition→状态池` 用 unconditional 边（跟随入边）
- 计算参数/K线配置/筛选条件从条件节点读取，非边

### 16.4 条件节点激活流程

EdgeExecutor 订阅 EdgeFired，收到后激活条件节点：

```
EdgeFired(eid) → 定位 eid 目标节点为条件节点
  → 收集所有入边（按 _order 排序）
  → 每条入边：
      source_pool = state.get_pool(in_edge.source)
      dirty_codes = source_pool.get_dirty_codes()  # G3/G4
      if 交集条件: port_results[order] = source_pool 全集（当前同时在池的股票）
      else: 公式计算（添加列）→ 筛选（列操作）→ port_results[order]
  → 集合运算：单入边直接输出；多入边按 filter_spec 做交集/差集/并集
  → 出边输出到目标池：add_stocks + 标脏 + 注册 TTL
  → C池入池触发买入事件链
```

### 16.5 公式计算与筛选分离

- 公式计算 = 添加列（FormulaEngine.eval_series 写入 series_results）
- 筛选 = 列操作（_eval_op 按 prev_expr/curr_expr 比较列值）
- 使用 HQChartPy2，Python 3.13，无 cross
- 金叉 noperate=3：`prev_expr="line1[-2] <= line2[-2]"`, `curr_expr="line1[-1] > line2[-1]"`
- 增量评估：仅对 dirty_codes 重新评估，合并 `passed = (cached_passed - dirty_codes) | newly_passed`

### 16.6 G2 引擎只发事件不执行计算（确认）

定时器到时→action 发布事件 + 立即注册下次→结束。业务逻辑由订阅模块完成：

- kind="edge" 的 action 只发布 EdgeFired(eid, ts)
- kind="ttl" 的 action 只发布 TTLDue（一次性，不注册下次）
- kind="tick" 的 action 只发布 TickDue
- 引擎不调用 edge_executor.run()，EdgeExecutor 订阅 EdgeFired 自行完成条件节点激活+筛选+转移

### 16.7 交易事件链

- C池入池买入：TransferExecuted → Signal(BUY,100) → OrderPlaced → OrderFilled → PositionUpdated
- C池出池卖出：TTLDue → Signal(SELL) → OrderPlaced → OrderFilled → PositionUpdated
- TTL 一次性（interval=None），不注册下次

---

## 17 测试架构

### 17.1 eventtest 目录

项目根目录下新建 `eventtest/` 目录（与旧 `tests/` 并列），以**正测试 / 反测试 / 合测试**三层方法论编写严格测试，作为评审工程师打分的唯一依据。旧 `tests/` 目录冻结保留，新评审一律以 `eventtest/` 输出为准。

详细测试架构（目录结构、方法论、量化评审标准、已修复 bug 清单）见 `docs/DESIGN.md` §19。

### 17.2 量化评审合同

- 评审工程师必须运行 `python -m eventtest.run_eventtest`，以量化指标打分
- 门槛 ≥ 98 分，正/反/合测试通过率均 ≥ 98%
- 事件链顺序错误扣 10 分，池状态断言错误扣 10 分，旧接口残留每处扣 5 分
- 发现 bug 必须修复生产代码，禁止 workaround 掩盖 bug

---

## 18 前端验证

### 18.1 验证合同

前端验证采用**双工程师协作 + Playwright 浏览器测试 + eventtest 量化验证**混合模式，与 §17 量化评审合同并列执行：

- **架构工程师**：修复前端/后端 bug，严格遵循 spec.md
- **评审工程师**：Playwright 浏览器验证 UI 渲染与交互 + eventtest 量化验证后端事件链
- **门槛**：分数 ≥ 98 方可进入下一任务；< 98 打回重做

| 验证手段 | 覆盖范围 | 权威性 |
|---------|---------|--------|
| Playwright 浏览器 | 前端 UI 渲染（条件节点矩形、配置面板、模式切换） | 用户可见行为 |
| eventtest 量化 | 后端事件链（11 类事件计数、池状态、A∩B 交集） | 内部事件流正确性 |

当 Playwright 因环境限制（sandbox 网络隔离）无法连接 localhost 时，以 eventtest 量化结果作为后端功能正确性的权威验证依据。

### 18.2 验证范围

| Task | 验证内容 | 评分 |
|------|---------|------|
| Task 1 | 加载示例池 + 条件节点显示（7 节点拓扑、紫色圆角 #8e44ad、边标签、多入边顺序号） | 100 |
| Task 2 | 条件节点配置面板（func/indi/indiparam/filter_spec 字段与 sim_test_pool_100.json 一致） | 100 |
| Task 3 | 仿真模式启动（按钮 active、POST /api/sim/start、虚拟时钟、100 只 fz 前缀股票） | 98 |
| Task 4 | 完整事件链（11 类事件全部产生，见 §18.3） | 98 |
| Task 5 | 三种模式切换（设计/仿真 Playwright 验证，实盘/回放代码审查） | 98 |
| Task 6 | bug 修复 + 无回归（5 个 bug 全部修复，eventtest 173 测试退出码 0） | 98 |

### 18.3 eventtest 量化基线

```
事件计数（按 EventType 分组）：
  TickReceived         10533
  DataChanged          73731
  BarComposed          63198
  EdgeFired            155
  FormulaEvaluated     3500
  StockFiltered        35
  TransferExecuted     29
  Signal               81
  OrderPlaced          81
  OrderFilled          81
  PositionUpdated      81

池状态快照：
  source:  100 stocks
  pool_A:  81 stocks
  pool_B:  100 stocks
  pool_C:  81 stocks

总耗时: 385.78s | 退出码: 0 (全部通过)
```

**关键断言**：
- source 池 100 只 fz 前缀股票（fz000001~fz000100）
- cond1（KDJ 5分钟金叉）→ pool_A=81 stocks
- cond2（MACD 1分钟金叉）→ pool_B=100 stocks
- cond3（A∩B 交集）→ pool_C=81 stocks（严格断言 pool_C = pool_A ∩ pool_B，禁止弱化为 ⊆）
- pool_C 入池触发买入链：Signal=81 → OrderPlaced=81 → OrderFilled=81 → PositionUpdated=81

### 18.4 已修复 bug 清单

| Bug | 位置 | 反模式 | 修复 |
|-----|------|--------|------|
| Bug1 | `web/js/app.js:11931` | UUID 正则验证过严，拒绝非 UUID 格式 pool_id | 删除 isValidPoolId，改为 `var poolId = rawPoolId \|\| configId \|\| null;` |
| Bug2 | `config/ui/ui_layouts.json:1138` | func.cfirst/csecond 渲染为 `<input type="color">`，指标线条名无法输入 | func_cfirst/csecond 从 number_input 改为 text_input |
| Bug3 | `app.py:277` | DataSourceContract 接收 defaults.json（不含 sources 键），导致 `_sources` 为空 | `DataSourceContract(config=config, bus=bus)` → `DataSourceContract(bus=bus)` |
| Bug4 | `config/data/data_source_contract.json` | module 路径 `meta_core.services.providers` 无法导入 | 改为 `services.providers`（5 处） |
| Bug5 | `config/data/data_providers.json` | 同 Bug4，ProviderRegistry 加载失败 | 改为 `services.providers`（6 处） |

### 18.5 与设计原则的一致性

- **§7 事件驱动架构合同**：eventtest 量化验证 11 类事件通过 EventBus 正确流转，模块间零直接引用
- **§9 仿真模式与实盘模式统一**：仿真模式除 TickSource（MockDataSource）外，所有代码路径与实盘一致，eventtest 验证 fz 前缀股票代码正确生成
- **§13 通达信转移条件 func 16 参数**：Playwright 验证条件节点配置面板正确显示 func.accode/indi/indiparam/filter_spec 字段
- **§16 条件节点模型**：Playwright 验证 cond1/cond2/cond3 紫色圆角矩形渲染，多入边顺序号徽标正确显示

---

## 19 Frontend Final Polish

### 19.1 Validation Contract

**Dual-engineer review with ≥98 score threshold.**

```
Architect implements frontend fixes
    │
    ▼
Reviewer validates:
    ├─ Global AppState: single source of truth, no local mode copies
    ├─ Event Panel: 3 views (matrix/scatter/timer) render correctly
    ├─ Shortcuts: all SHORTCUTS table entries functional
    ├─ Bug fixes: 8 bugs verified fixed, no regressions
    └─ Score ≥ 98 → pass; < 98 → reject
```

**Scoring Rubric**:

| Criterion | Weight | Pass Condition |
|-----------|--------|----------------|
| AppState global store | 20 | All modules read from `window.AppState`, zero `currentMode` local copies |
| Matrix view timeline | 20 | Proportional positioning, tick marks, red NOW line (GPU-accelerated) |
| Scatter plot interactivity | 20 | ±500ms window aggregation, count badges, tooltip, click detail panel |
| Timer queue visualization | 15 | Green NOW line, state styles, real-time MM:SS countdown |
| Shortcuts | 10 | All 11 shortcuts from SHORTCUTS table functional |
| Bug fixes verification | 15 | All 8 bugs from §19.6 verified fixed |

**Hard Fail Conditions**: Any local state copy of `mode`/`simulationState`; invalid API calls to non-existent endpoints; event panel missing timeline.

### 19.2 Global State Store Contract

TypeScript interface for `window.AppState` — the single source of truth for all frontend state:

```typescript
type AppMode = 'design' | 'simulation' | 'replay' | 'live';
type SimulationState = 'idle' | 'running' | 'paused' | 'stopped';

interface AppState {
  mode: AppMode;
  simulationState: SimulationState;
  simulationTime: number;      // seconds since midnight, 34500 = 09:30:00
  speed: number;               // multiplier, default 1
  events: TimelineEvent[];     // append-only event log
  timers: TimerEntry[];        // pending timer queue
  stockTables: Record<string, StockTable>;  // per-node stock counts
  
  // Subscriber mechanism (frontend EventBus)
  subscribe(eventType: string, callback: (data: any) => void): () => void;  // returns unsubscribe
  notify(eventType: string, data: any): void;
  setState(patch: Partial<AppState>): void;  // merges patch + notifies subscribers
}

interface TimelineEvent {
  id: string;
  type: EventType;
  ts: number;                  // virtual clock seconds
  payload: Record<string, any>;
}

interface TimerEntry {
  id: string;
  kind: 'edge' | 'ttl' | 'tick';
  fireTime: number;            // virtual clock seconds
  edgeId?: string;
  nodeId?: string;
  status: 'pending' | 'due' | 'fired' | 'cancelled';
}
```

**Contract Rules**:
- **R1**: All state reads MUST go through `window.AppState`. No local copies of `mode`, `simulationState`, or `simulationTime`.
- **R2**: All state mutations MUST go through `setState()`. Direct property assignment is prohibited.
- **R3**: UI updates MUST be triggered by `subscribe()` callbacks. No polling for state changes.
- **R4**: `events` array is append-only. Events are never mutated after insertion.

### 19.3 Event Panel View Style Tables

#### EVENT_STATE_STYLES — Table-Driven Event Styling

```javascript
const EVENT_STATE_STYLES = Object.freeze({
  TickReceived:      { color: '#95a5a6', bg: 'rgba(149,165,166,0.15)', icon: '⏱' },
  DataChanged:       { color: '#3498db', bg: 'rgba(52,152,219,0.15)', icon: '📊' },
  BarComposed:       { color: '#2ecc71', bg: 'rgba(46,204,113,0.15)', icon: '📶' },
  EdgeFired:         { color: '#9b59b6', bg: 'rgba(155,89,182,0.15)', icon: '⚡' },
  FormulaEvaluated:  { color: '#1abc9c', bg: 'rgba(26,188,156,0.15)', icon: '🧮' },
  StockFiltered:     { color: '#f39c12', bg: 'rgba(243,156,18,0.15)', icon: '🔍' },
  TransferExecuted:  { color: '#e67e22', bg: 'rgba(230,126,34,0.15)', icon: '🔄' },
  Signal:            { color: '#e74c3c', bg: 'rgba(231,76,60,0.20)', icon: '🚦' },
  OrderPlaced:       { color: '#34495e', bg: 'rgba(52,73,94,0.15)', icon: '📋' },
  OrderFilled:       { color: '#27ae60', bg: 'rgba(39,174,96,0.20)', icon: '✅' },
  PositionUpdated:   { color: '#16a085', bg: 'rgba(22,160,133,0.15)', icon: '💼' },
  TTLDue:            { color: '#c0392b', bg: 'rgba(192,57,43,0.20)', icon: '⏰' }
});
```

#### TIMER_STATE_STYLES — Table-Driven Timer Styling

```javascript
const TIMER_STATE_STYLES = Object.freeze({
  pending:   { color: '#2c3e50', bg: '#ffffff', border: '1px solid #bdc3c7' },
  due:       { color: '#e67e22', bg: '#fef5e7', border: '2px solid #e67e22' },
  fired:     { color: '#95a5a6', bg: '#ecf0f1', border: '1px dashed #bdc3c7' },
  cancelled: { color: '#e74c3c', bg: '#fdedec', border: '1px solid #e74c3c' }
});
```

### 19.4 Event Category Mapping (CATEGORY_CONFIG)

```javascript
const CATEGORY_CONFIG = Object.freeze({
  clock:     { events: ['TickReceived'],                    color: '#95a5a6', icon: '⏱' },
  data:      { events: ['DataChanged', 'BarComposed'],       color: '#3498db', icon: '📊' },
  condition: { events: ['EdgeFired', 'FormulaEvaluated', 'StockFiltered'], color: '#9b59b6', icon: '⚡' },
  transfer:  { events: ['TransferExecuted', 'TTLDue'],       color: '#e67e22', icon: '🔄' },
  signal:    { events: ['Signal'],                           color: '#e74c3c', icon: '🚦' },
  order:     { events: ['OrderPlaced', 'OrderFilled'],       color: '#34495e', icon: '📋' },
  position:  { events: ['PositionUpdated'],                  color: '#16a085', icon: '💼' },
  system:    { events: ['SessionStarted', 'SessionStopped', 'SpeedChanged'], color: '#7f8c8d', icon: '⚙️' },
  error:     { events: ['FormulaError', 'OrderRejected'],    color: '#c0392b', icon: '⚠️' }
});
```

**Invariant**: Every `EventType` value MUST appear in exactly one category's `events` array.

### 19.5 Keyboard Shortcut Mapping

```javascript
const SHORTCUTS = Object.freeze({
  ' ':         { action: 'togglePlayPause', desc: 'Play/Pause simulation', context: 'simulation' },
  'r':         { action: 'resetSimulation',  desc: 'Reset simulation',      context: 'simulation' },
  'ArrowRight':{ action: 'stepForward',      desc: 'Step forward 1s',       context: 'paused' },
  'ArrowLeft': { action: 'stepBackward',     desc: 'Step backward 1s',      context: 'paused' },
  '+':         { action: 'speedUp',          desc: 'Speed up ×2',           context: 'simulation' },
  '-':         { action: 'speedDown',        desc: 'Speed down ÷2',         context: 'simulation' },
  '1':         { action: 'setSpeed1x',       desc: 'Set speed 1x',          context: 'simulation' },
  'm':         { action: 'toggleMode',       desc: 'Toggle Design/Sim',     context: 'global' },
  'e':         { action: 'toggleEventPanel', desc: 'Toggle Event Panel',    context: 'global' },
  'Escape':    { action: 'closeDialogs',     desc: 'Close all dialogs',     context: 'global' },
  '?':         { action: 'showHelp',         desc: 'Show shortcut help',    context: 'global' }
});
```

**Contract**: Shortcut handler dispatch MUST use table lookup: `SHORTCUTS[key].action`. No `if/else` or `switch` chains for key handling.

### 19.6 Fixed Bugs and Anti-Patterns

| Bug | Symptom | Root Cause | Fix | Anti-Pattern Prohibited |
|-----|---------|------------|-----|------------------------|
| 1 | Auto-step fails after mode switch; only manual step works | `currentMode` local variable; modules hold stale copies after mode change | Migrate to `window.AppState` global store; `subscribe('modeChanged')` for all consumers | **No local state copies** of mode/simulationState/simulationTime. All reads from AppState. |
| 2 | Matrix view: events stacked without timeline; no temporal ordering visible | Events appended directly via `appendChild`; no X-coordinate calculation | Proportional positioning `left% = (ts - winStart)/winDur*100`; 60s tick marks; red NOW line via `transform: translateX()` (GPU) | **No non-proportional event placement** in time-based views. |
| 3 | Scatter plot: no density indication, no tooltip, no click interaction | No aggregation; no event listeners bound | ±500ms sliding window, 50ms buckets; count badges; rich tooltip; click opens detail panel | **No dead UI elements** — visual elements must have tooltips or interaction. |
| 4 | Timer queue: plain text list; no timeline, no time-to-fire indication | Rendered as `<li>` without temporal visualization | Green NOW line `#27ae60`; `TIMER_STATE_STYLES` table; MM:SS real-time countdown | **No unstructured lists** for temporal data — must show position relative to NOW. |
| 5 | Reset button: calls non-existent API, doesn't clear state | Called `POST /api/sim/clear` (no such route); frontend state not reset | Call `POST /api/sim/stop`; then clear events/timers, `setState({simulationState:'idle', simulationTime:34500})`, redraw all views | **No calls to non-existent API endpoints**. Verify endpoint exists before integration. |
| 6 | Speed slider: sends invalid API request, speed doesn't change | Called `POST /api/sim/speed` (no such route); server-side speed control not implemented | Remove invalid API call; client-side speed control via `setInterval(1000/speed)`; persist speed to localStorage | **No dead API calls**. If backend doesn't implement it, control it client-side. |
| 7 | Step events delayed ~500ms; sometimes don't appear | Waited for polling (500ms interval) to fetch new events after step | Step API response events immediately pushed to `AppState.events` + `notify('eventsUpdated')`; polling is fallback only (1s interval) | **No polling-dependent UI updates**. Prefer immediate event-driven updates; polling as safety net only. |
| 8 | Candidate pool node stock counts don't refresh after transfer | `refreshStockTables()` only handled `stock_state_pool`; `market_source` type not covered | Add `market_source` branch: initial count from pool config, runtime via `GET /api/pool/{nid}/stocks` | **No type-specific gaps in refresh logic**. All node types must be handled. |

### 19.7 localStorage Persistence Keys

| Key | Type | Purpose |
|-----|------|---------|
| `metacore.speed` | `number` | Last used simulation speed multiplier |
| `metacore.eventPanelVisible` | `boolean` | Event panel open/closed state |
| `metacore.lastMode` | `AppMode` | Last active mode before page unload |
| `metacore.shortcutHelpDismissed` | `boolean` | Whether user dismissed the shortcut help dialog |

**Contract**: All persistent keys MUST use `metacore.` namespace prefix. Sensitive state (session tokens, positions) is NOT persisted client-side.

### 19.8 Architecture Principle Alignment

| Principle | Section | Alignment |
|-----------|---------|-----------|
| Single Source of Truth | §I3 | `window.AppState` is the exclusive frontend state container. Zero local copies. `setState()` + `subscribe()` enforces unidirectional data flow. |
| Event-Driven | §I4 | AppState `subscribe/notify` is the frontend EventBus pattern. State changes broadcast events; UI reacts. No direct cross-module calls. |
| Table-Driven | §I5 | `EVENT_STATE_STYLES`, `TIMER_STATE_STYLES`, `CATEGORY_CONFIG`, `SHORTCUTS` — all four tables define behavior without code changes. New event types = new table row. |

---

## Changelog

| Date | Version | Changes |
|------|---------|---------|
| 2026-07-25 | v1.19 | §19 Frontend Final Polish: AppState contract, event panel 3-view architecture, table-driven styles/shortcuts, 8 bug fixes with anti-pattern prohibitions |
