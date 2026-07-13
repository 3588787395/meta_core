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