# 股票池执行引擎 — 重构规划

## 1. 运行逻辑

```
┌─────────┐     ┌─────────────────────────┐     ┌──────────┐
│  编译    │────▶│  tick 循环               │────▶│ 事件发射  │
│ (一次性) │     │  first_run→标脏源         │     │          │
│         │     │  for eid in order:       │     │ DomainEv │
│ Schedule│     │    if due+dirty: execute  │     │ Signal   │
│         │     │  fire_ttl_due            │     │          │
│         │     │  clear_dirty+snapshot    │     │ _sync_   │
│         │     │  sync_events_to_meta     │     │ events   │
└─────────┘     └─────────────────────────┘     └──────────┘
```

三阶段：①**编译** `Compiler.compile(pool_config) → CompiledSchedule`（拓扑排序+分派索引）②**运行** `_run_tick_body()` ③**事件** `EventBus → DomainEvent/Signal → 外部消费者；_sync_events_to_meta()` 同步 Executed→transfer_events

## 2. 编译阶段

`Compiler.compile()` 产出 `CompiledSchedule`：

| 产出物 | 类型 | 用途 |
|--------|------|------|
| `execution_order` | `List[eid]` | 拓扑序边执行顺序 |
| `edge_ctx` | `Dict[eid, EdgeContext]` | 端点/类型/角色（只读） |
| `edge_timing_spec` | `Dict[eid, TimingSpec]` | starttype/cxtype/interval/duration |
| `edge_filter_spec` | `Dict[eid, FilterSpec]` | evaluator_type/formula_ref/params |
| `edge_propagate_spec` | `Dict[eid, PropagateSpec]` | copy/move/overwrite + 4策略组合 |
| `edge_action_spec` | `Dict[eid, ActionSpec]` | baimpool等目标池动作 |
| `edge_ttl_spec` | `Dict[eid, TTLSpec]` | bdel/check_type/ttl_sec/endtime_sec |
| `node_types` | `Dict[nid, str]` | 源/状态/条件/输出 |
| `source_node_ids` | `Set[nid]` | 数据入口节点集合 |

编译期映射：`_resolve_node_type` → dzh_type_map.json + tdx_type_map；`_resolve_edge_type` → edge_semantics.json；`_DISPATCH_KEY_TO_EVALUATOR_TYPE` → dispatch.json；`_build_ttl_spec` → tdx_psatt.json；`_build_propagate_spec` → attr位域4模式

## 3. 运行阶段

```python
# 0. 时间刷新
if wall_clock: state.time_source["current_ts"] = _safe_timestamp(_now())
# 1. 首次标脏
if state.first_run: mark_source_nodes_dirty()
# 2. 边扫描
for eid in schedule.execution_order:
    ec = schedule.edge_ctx[eid]
    fired = driver.is_edge_due(eid, now) if driver else True
    trigger = dirty.nodes.get(ec.sid) or (dirty.data and ec.sid in source_ids)
    if fired and trigger:
        if edge_executor.run(eid): mark_dirty(ec.tid)
# 3. TTL扫描
if driver: driver.fire_ttl_due(now)
# 4. 清脏+快照+同步
clear_dirty(); state.first_run = False; snapshot_nodes()
_sync_events_to_meta()
```

单条边执行5步流水线：①`gate(spec,eid)` TimingSpec→_STARTTYPE_GATE_HANDLERS + _CXTYPE_POST_GATES ②`filter(spec,codes,eid)` FilterSpec.evaluator_type→_FILTER_EVALUATORS ③`propagate(spec,sid,tid)` PropagateSpec.mode→_PROPAGATE_STRATEGIES ④`tracker_init(spec,tid)` 初始化目标节点tracker字段 ⑤`callback(ec,action_spec)` ActionSpec→_ACTION_HANDLERS。TTL由EventDriver.fire_ttl_due独立驱动，不入流水线。

## 4. 触发机制

**边触发 = 时间门控通过 ∧ 源节点脏**

| 条件 | 判定 | 数据源 |
|------|------|--------|
| 时间门控 | `EventDriver.is_edge_due(eid, now)` | `at_fn → _gate(TimingSpec)` |
| 源脏 | `dirty.nodes[sid]` | 上游propagate标脏 / 首次标脏 |
| 数据变更 | `dirty.data and sid in source_ids` | DataUpdater.apply_data标脏 |

**脏标记传播链**：DataUpdater.apply_data → dirty.data=True → source_ids中sid脏 → 边触发 → propagate写目标 → mark_dirty(tid) → 下游dirty.nodes[tid]=True → clear_dirty()（tick结束）

**TTL触发**：`fire_ttl_due → on_timed_event_sync → _run_ttl → _TTL_CHECK_HANDLERS[check_type]`

**数据事件链路**：DataUpdater→EventBus(DataChanged) → BarComposer合成bars→EventBus(DataChanged) → SnapshotBuilder订阅维护增量视图 → _sync_events_to_meta()

## 5. 表驱动分派

核心分派点查表执行，新增分派零if-elif改动。

| 分派点 | 分派键 | 注册表 |
|--------|--------|--------|
| 时机门控(前) | `starttype` | `_STARTTYPE_GATE_HANDLERS` |
| 时机门控(后) | `cxtype` | `_CXTYPE_POST_GATES` |
| 筛选分派 | `evaluator_type` | `_FILTER_EVALUATORS` |
| 流转分派 | `mode` | `_PROPAGATE_STRATEGIES` |
| TTL检查 | `check_type` | `_TTL_CHECK_HANDLERS` |
| 动作回调 | `action` | `_ACTION_HANDLERS` |
| noperate比较 | `noperate` | `_NOPERATE_RULES` |
| 排名模式 | `noperate` | `_RANK_MODES` |
| 并列处理 | `tie_handling` | `_TIE_HANDLERS` |
| 集合运算 | `op_code` | `_NSET5_OPS` |
| nset分派 | `dispatch_key` | `_DISPATCH_TO_NSET_CFG` |
| 合并操作 | `op` | `_COMBINE_OPS` |

新增分派点：①加表行 ②加handler函数 ③注册到dict。

## 6. 重构清单

### P0：缓存统一

| # | 当前问题 | 目标状态 | 关键设计决策 |
|---|---------|---------|-------------|
| R7 | builtins._JSON_CACHE vs MetaEngine.tables/ConfigStore._tables | 统一到ConfigStore | builtins接受config_provider注入，不再自建缓存；对齐ConfigStore(table_engine.py:27) |
| R8 | compiler._CONFIG_CACHE 第三份缓存 | 统一到ConfigStore | Compiler.compile接受config_provider参数 |
| R9 | evaluators模块级3处独立加载：_noperate_data、_data_source_mappings_cache、_builtin_formulas_cache | 统一到ConfigStore懒加载 | 保留模块级引用，首次加载委托ConfigStore；注入方式：evaluators提供`init_caches(provider)`函数，将3个全局变量替换为provider.get()结果；builtins/edge_executor调用前统一初始化 |
| R17 | _NSET5_OPS重复定义于evaluators.py:65和edge_executor.py:495 | 统一到evaluators.py | edge_executor.py改为`from .evaluators import _NSET5_OPS` |

> 先统一缓存，后拆分MetaEngine：拆分后各模块需接受config_provider参数，若先拆分再统一则需二次重构接口。

### P1：MetaEngine God Object 拆分（1281行→5模块）

engine.py前645行：PoolEngineMixin(403行) + 工具函数(~240行) + PoolEngine(~78行) + 常量。MetaEngine从line 646开始，共1281行。

| # | 当前问题 | 目标状态 | 关键设计决策 |
|---|---------|---------|-------------|
| R1 | MetaEngine混合：领域事件+Tracker+配置+模块分派+UI | 拆为5独立模块 | 见§7 |
| R2 | _emit_domain_event 100行内嵌模板解析+信号路由+守卫 | DomainEventEmitter | 模板驱动，__init__预编译context_fields→resolver |
| R3 | _emit_transfer_events 70行混合日志+域模板+per-code解析 | 日志→TransferLogger，解析→DomainEventEmitter |
| R4 | _update_trackers 内嵌tracker公式拓扑排序+逐字段eval | TrackerUpdater，接收formula_order |
| R5 | _post_tick pipeline反射调用，参数10+ | PostTickPipeline，stage_handler(state,stocks,bar_data,now) |
| R6 | _ExecCtxView 68行view层混入engine | 移入runtime.py |

### P2：transfer_condition_check 表驱动化（行为变更）

> **注意**：下述pipeline重排了原始执行顺序，属于行为变更而非等价重构。原始顺序：indicator→basic(不含indicator)→ranking→reverse→cross_section→basic(含indicator)。新顺序将cross_section从第5位提前至第2位，使截面过滤在ranking/reverse之前执行，语义更清晰但输出可能不同。需回归测试验证。

| # | 当前问题 | 目标状态 | 关键设计决策 |
|---|---------|---------|-------------|
| R10 | transfer_condition_check 6个if attr.get(flag)串行分支 | flag→handler注册表按priority执行 | 见§8 |
| R11 | flag间隐式依赖：basic+indicator时先公式后topn，basic only时仅topn | requires/excludes字段声明 | 运行期按表过滤活跃pipeline |

### P3：mock/规则表化

| # | 当前问题 | 目标状态 |
|---|---------|---------|
| R12 | _gen_stock_codes 7个hardcoded if "SH\d{6}" | mock_data.json stock_gen_rules表驱动 |
| R13 | _gen_sector_stocks内联random规则 | 补充fallback路由入表 |
| R14 | resolve_market tq/mock双路径hardcoded | markets.json mock_codes字段 |

### P4：eval消除

| # | 当前问题 | 目标状态 |
|---|---------|---------|
| R15 | _update_trackers用CompiledExpression（AST求值），已安全 | 保持现状 |
| R16 | _post_tick stage handler反射调用 | 保留反射，stage_ops表即为白名单 |

## 7. 模块拆分方案

### 当前结构

```
MetaEngine (1281行，line 646-1926)
├── __init__ (80行配置加载)
├── 领域事件发射 (200行: _emit_domain_event + _emit_transfer_events)
├── Tracker更新 (40行)
├── PostTick Pipeline (30行)
├── 事件桥接 (40行: _on_signal_event + _on_domain_event + _push_event)
├── 域解析原语 (200行: _resolve_codes/_resolve_domain_ctx/_resolve_domain_source + 5个handler方法)
├── 角色解析 (60行: _resolve_pool_role + _compute + _detail)
├── 价格/条件查询 (40行)
├── 兼容API (200行: _ExecCtxView + _prepare_topology + _build_processing_plan)
├── 外部API (100行: run_pool/run_loop/run_mode/_tick)
└── 配置/模块分派 (60行)
```

### 门面方法迁移清单

| 方法 | 行数 | 归属 |
|------|------|------|
| _resolve_context_field | 57 | →DomainEventEmitter |
| _resolve_pool_role + _compute | 40 | →DomainResolver |
| _build_exit_tracker_info + _tracker_detail | 25 | →TrackerUpdater |
| _should_emit_signal_for_domain | 16 | →DomainEventEmitter |
| _build_event_detail | 11 | →DomainEventEmitter |
| _resolve_role_from_node, _resolve_cond_* | 30 | →DomainResolver |
| _get_stock_price | 25 | 保留门面 |
| _log_transfer_batch | 20 | →TransferLogger |
| _read_config_row, _setup_mode | 40 | 保留门面 |
| set_*, check_hot_reload, get_* | 30 | 保留门面 |
| _run_module, _init_node_stocks | 63 | 保留门面 |
| _prepare_topology, _build_processing_plan等 | ~170 | 保留门面(兼容层) |
| _compute_formula_order | 35 | →Compiler |

### 目标结构

```
core/
├── engine.py              # PoolEngine(640行) + MetaEngine门面(450-500行)
├── domain_emitter.py      # DomainEventEmitter (150行)
├── tracker_updater.py     # TrackerUpdater (80行)
├── post_tick.py           # PostTickPipeline (60行)
├── event_bridge.py        # EventBridge (50行)
├── domain_resolver.py     # DomainResolver (180行)
├── compiler.py            # Compiler + CompiledSchedule (不变)
├── edge_executor.py       # EdgeExecutor (不变)
├── runtime.py             # PoolState (不变)
├── event_bus.py           # EventBus (不变)
├── time_util.py           # EventDriver + time_at (不变)
├── data_updater.py        # DataUpdater (不变)
├── evaluators.py          # 评估器 (不变)
└── _compat.py             # CompiledExpression (不变)
```

### 模块接口

**DomainEventEmitter** — 与DomainResolver强耦合，先分设后视复杂度决定是否合并。
```python
class DomainEventEmitter:
    """领域事件发射器：event_domain_templates 表驱动发射 DomainEvent + Signal。"""
    def __init__(self, event_rules, signal_rules, pool_roles, event_domain_templates, edge_cfg, value_extractor): ...
    def emit_domain_event(self, domain, domain_ctx, code, bus): ...
    def emit_transfer_events(self, prev_stocks, updated_stocks, transfer_events, bus): ...
```

**DomainResolver**
```python
class DomainResolver:
    """域解析原语：codes提取、context构建、source分派。"""
    def __init__(self, event_domain_templates, edge_cfg): ...
    def resolve_codes(self, source_spec, ctx) -> List[Tuple[str, dict]]: ...
    def resolve_domain_ctx(self, tpl, base_ctx, code, code_ctx) -> dict: ...
    def resolve_domain_source(self, source_key, resolver_category, tpl, ctx, code_ctx, base_ctx, code): ...
    def skip_domain_code(self, tpl, code, base_ctx, code_ctx) -> bool: ...
```

**TrackerUpdater**
```python
class TrackerUpdater:
    """Tracker公式更新器：编译期formula_order + 运行期逐字段AST求值。"""
    def __init__(self, tracker_fields, tracker_formulas, formula_order, price_fields, market_cfg): ...
    def update(self, node_stocks, current_bar_data, now_ts, get_price_fn): ...
```

**PostTickPipeline**
```python
class PostTickPipeline:
    """PostTick流水线：post_tick_pipeline.json表驱动执行。"""
    def __init__(self, pipeline_config, edge_cfg, builtins_module): ...
    def run(self, state, node_stocks, current_bar_data, now, context): ...
```

**EventBridge**
```python
class EventBridge:
    """EventBus订阅者桥接：DomainEvent→event_queue, Signal→signal_queue。"""
    def __init__(self, event_queue, signal_queue, time_at_fn): ...
    def on_signal(self, signal): ...
    def on_domain_event(self, event): ...
```

### MetaEngine 门面（重构后）

```python
class MetaEngine:
    """门面：委托PoolEngine运行 + 持有各子模块。"""
    def __init__(self, config_dir=None):
        self._domain_emitter = DomainEventEmitter(...)
        self._tracker_updater = TrackerUpdater(...)
        self._post_tick = PostTickPipeline(...)
        self._event_bridge = EventBridge(...)
    # 保留：run_pool, run_loop, run_mode, _now, _is_trading_time,
    #   _refresh_bar_data, _get_stock_price, _read_config_row, _setup_mode,
    #   set_*, check_hot_reload, _run_module, _init_node_stocks,
    #   _prepare_topology, _build_processing_plan (兼容层)
    # 迁移：_emit_domain_event→._domain_emitter, _update_trackers→._tracker_updater,
    #   _post_tick→._post_tick, _on_signal/_on_domain→._event_bridge,
    #   _resolve_*→DomainResolver, _compute_formula_order→Compiler
    # 废弃：_ExecCtxView→runtime.py直接提供
```

### 缓存统一方案

```python
# builtins/builtins.py: 接受 config_provider 注入，复用已有ConfigStore
class BuiltinRegistry:
    def __init__(self, config_provider: ConfigStore = None):
        self._provider = config_provider
    def load_config(self, filename, raise_on_error=False):
        if self._provider: return self._provider.get(filename, {})
        return _load_config_json(filename, raise_on_error=raise_on_error)

# evaluators: 3处缓存统一注入
def init_caches(provider: ConfigStore):
    global _noperate_data, _data_source_mappings_cache, _builtin_formulas_cache
    _noperate_data = provider.get("tdx_noperate_rules", _noperate_data)
    _data_source_mappings_cache = provider.get("data_source_mappings", _data_source_mappings_cache)
    _builtin_formulas_cache = provider.get("builtin_formulas", _builtin_formulas_cache)

# Compiler: compile(pool_config, config_provider=store)
```

## 8. transfer_condition_check 表驱动化

### 当前6分支flag链

```python
if attr.get("indicator_condition"): ...                                              # ①
if attr.get("basic_condition") and not attr.get("indicator_condition"): _topn_filter  # ②
if attr.get("ranking_condition"): _topn_filter(..., 10)                              # ③
if attr.get("reverse_transfer"): reverse                                              # ④
if attr.get("cross_section"): _filter_by_bar_data                                    # ⑤
if attr.get("basic_condition") and attr.get("indicator_condition"): _topn_filter      # ⑥
```

### 目标：flag pipeline表（行为变更）

```json
{
  "condition_pipeline": [
    {"flag": "indicator_condition", "handler": "eval_indicator", "priority": 1, "requires": [], "excludes": []},
    {"flag": "cross_section", "handler": "filter_by_bar_data", "priority": 2, "requires": [], "excludes": []},
    {"flag": "reverse_transfer", "handler": "reverse_filter", "priority": 3, "requires": [], "excludes": []},
    {"flag": "ranking_condition", "handler": "topn_filter_10", "priority": 4, "requires": [], "excludes": []},
    {"flag": "basic_condition", "handler": "topn_filter", "priority": 5, "requires": [], "excludes": ["indicator_condition"]},
    {"flag": "basic_condition", "handler": "topn_filter_post", "priority": 6, "requires": ["indicator_condition"], "excludes": []}
  ]
}
```

> 原始顺序①→②→③→④→⑤→⑥，新pipeline将cross_section从⑤提前至②。这改变了执行语义：截面过滤现在在ranking/reverse之前执行。标注为行为变更，需回归测试。

```python
_COND_PIPELINE_HANDLERS = {
    "eval_indicator": _eval_indicator_condition,
    "filter_by_bar_data": lambda r, inputs: _filter_by_bar_data(r, inputs.get("current_bar_data"))[0],
    "reverse_filter": _reverse_filter,
    "topn_filter_10": lambda r, inputs: _topn_filter(r, inputs.get("params", {}).get("sorttype", ""), 10),
    "topn_filter": lambda r, inputs: _topn_filter(r, inputs.get("params", {}).get("sorttype", "")),
    "topn_filter_post": lambda r, inputs: _topn_filter(r, inputs.get("params", {}).get("sorttype", "")),
}

def transfer_condition_check(inputs):
    result = list(stock_list)
    for step in sorted(pipeline, key=lambda s: s["priority"]):
        if not attr.get(step["flag"]): continue
        if step["requires"] and not all(attr.get(r) for r in step["requires"]): continue
        if step["excludes"] and any(attr.get(e) for e in step["excludes"]): continue
        result = _COND_PIPELINE_HANDLERS[step["handler"]](result, inputs)
    return {"passed": result, "rejected": [...]}
```

indicator_condition handler内部（算法逻辑保留在handler内）：FormulaRouter路由+fallback链→表驱动化(formula_routes.json/fallback_chain.json/period_map.json)；字符集校验/参数解析/结果有效性判断→保留handler内部。

## 9. 实施顺序

```
Phase 1 (P0): 缓存统一
  R7  builtins._JSON_CACHE → ConfigStore       [1天]
  R8  compiler._CONFIG_CACHE → ConfigStore      [0.5天]
  R9  evaluators 3处缓存 → ConfigStore          [0.5天]
  R17 _NSET5_OPS统一到evaluators.py              [0.5天]

Phase 2 (P1): MetaEngine拆分
  R6  _ExecCtxView → runtime.py                [1天]
  R5  _post_tick → PostTickPipeline             [1天]
  R4  _update_trackers → TrackerUpdater         [1天]
  R2+R3 领域事件 → DomainEventEmitter           [2天]
  R1  MetaEngine门面收敛                        [1天]

Phase 3 (P2): transfer_condition_check表驱动
  R10 flag→handler注册表                        [1天]
  R11 requires/excludes声明                     [0.5天]

Phase 4 (P3): mock规则表化
  R12-R14 _gen_stock_codes/sector/market        [1天]
```
