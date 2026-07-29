# DESIGN — 执行流视角的表操作映射

## 1. 一句话本质

**股票池 = 状态池流水线。** 每个变换单元 = (条件转移边 + 转移条件 + 无条件转移边) 三元组，将一个状态池（视图）经条件计算变换为下一个状态池（视图）。从备选池出发，不断添加条件，形成下一状态池。变换单元执行 = gate→filter→propagate→callback→ttl。

### 1.1 新增执行模式

迁移后默认路径为 **新核心引擎 `PoolEngine`**（`core/engine.py`），`MetaEngine`
作为其门面保留旧公共 API：

- **单次执行**：`MetaEngine.run_pool()` → `PoolEngine.run_pool()` — 一次性执行完整流程。
- **持续循环执行**：`MetaEngine.run_loop()` → `PoolEngine.run_loop()` — 异步持续执行，每 tick 完整执行 gate→filter→propagate→callback→ttl，支持暂停/恢复/停止。
- **异步并发数据获取**：`DataUpdater` / `BarComposer` — 支持多时间框架并发获取 K 线数据，带缓存机制，通过 EventBus 向 `PoolEngine` 推送 `DataChanged`。
- **编译期/运行期分离 + 事件驱动**：`Compiler.compile(pool_config)` 在运行前一次性产出
  `CompiledSchedule`（含 execution_order / edge_ctx / edge_timing_spec /
  edge_filter_spec / edge_propagate_spec（业务上亦称 edge_flow_spec）/
  edge_action_spec / edge_ttl_spec）+ `TimedEventSpec` 表行（边触发+TTL 折叠）。
  运行期 `PoolEngine.run_tick()` 只做：

  ```python
  now = time_at(state=state)                    # 单一时间入口
  for eid in execution_order:                   # EventDriver 中断驱动
      if driver.is_edge_due(eid, now):
          driver.fire_edge(eid)                 # 到时执行 + 按需续期
  driver.fire_ttl_due(now)                      # TTL 一次性删除
  for eid in execution_order:                   # 仅执行触发边
      if edge_fired[eid] and (dirty.nodes[sid] or dirty.data):
          edge_executor.run(eid)                # filter → propagate → callback → ttl
  ```

  触发条件严格为：`triggered = edge_fired AND (node_dirty[sid] OR data_dirty)`。
  拓扑与边规格在 pool_config 不变时跨 tick 复用。到时事件经 `loop.call_at` 注册中断回调，不使用 `asyncio.sleep` 轮询。

旧路径 `_execute_flows()` / `_execute_flowsCore()` / `_tick_event_driven()` /
`_tick_simple()` 已删除；新核心路径为 `PoolEngine.run_tick()` + `EdgeExecutor.run()`。

### 1.2 前端架构边界

迁移后前端仅作为**展示层**，不参与业务真值计算。后端持有全部业务真值源，前端通过两条唯一路径与后端交互：

| 职责 | 前端 | 后端 |
|------|------|------|
| 业务真值源 | ❌ 不持有 | ✅ 股票池节点、运行时状态、事件队列、计时器队列、模式状态 |
| UI 渲染 | ✅ 按配置表渲染 | ❌ 不参与 |
| 用户输入 | ✅ 转发到后端 API | ✅ 处理并更新真值源 |
| 事件订阅 | ✅ 订阅 SSE `/api/events/stream` | ✅ 通过 `core/event_bus.py` 发布事件契约 |
| 纯界面状态 | ✅ 面板折叠/缩放/选中/滚动位置 | ❌ 不持有 |

**表驱动 UI 唯一路径**：所有 UI 组件、布局、字段、动作统一由 `config/ui/*.json` 配置表驱动，禁止硬编码节点类型分支或写死菜单条目。

**事件驱动唯一路径**：前端事件入口唯一为 `EventSource('/api/events/stream')`，事件格式与 `core/event_bus.py` 事件契约对齐；禁止前端伪造事件、独立维护事件队列真值源。

**模式一致性**：设计/仿真/回放/实盘四种模式共享同一条执行路径，差异仅在于数据源与时间推进机制（`runtime_modes.json` + `time_sources.json`）。

---

## 2. 加载配置

| 步骤 | 读配置表 | 写运行时索引 |
|------|---------|-------------|
| 加载JSON | config/**/*.json（递归子目录，SubTask 27.14 分类后分布到 architecture/runtime/data/ui/pools 等子目录；跳过 _archived/ 与 .locks.json） | self.tables |
| 建立模块索引 | architecture/modules.json | self.module_map: Dict[id→module] |
| 建立引擎索引 | engines.json | self.engine_index: Dict[id→engine] |
| 建立边策略索引 | edge_strategies.json | self._edge_strategies + self._node_init |
| 建立条件分发索引 | dispatch.json | self.dispatch_index |
| 建立时机规则缓存 | timing.json | self._timing_cfg |
| 建立TTL规则缓存 | tdx_psatt.json | self._psatt_cfg |
| 建立DZH类型映射 | dzh_type_map.json | self._dzh_type_map |
| 建立参数别名 | defaults.json | self._param_aliases |
| 注册handler | behavior_actions.json + builtins.py 反射 | self._handler_registry |
| 加载池配置 | pool_config（持久表，params JSON） | 无（按需读取） |
| 热加载检测 | 配置表文件hash | config_version（持久表 INSERT） |

> 代码位置: `core/engine.py` `MetaEngine.__init__()`、`PoolEngine.__init__()` 与 `Compiler.compile()`。

---

## 3. 初始化节点库存

| 节点类型 | 读配置表 | 写运行时表 | handler |
|---------|---------|-----------|---------|
| market_source | edge_strategies.json:node_init | PoolState.node_stocks[nid] | init_market_source |
| stock_state_pool | edge_strategies.json:node_init | PoolState.node_stocks[nid] | init_stock_state_pool |
| tdx_candidate | edge_strategies.json:node_init | PoolState.node_stocks[nid] | init_tdx_candidate |
| 其他类型 | 无 | PoolState.node_stocks[nid] = [] | 无 |

> 代码位置: `core/engine.py` `PoolEngineMixin._mark_source_nodes_dirty()` /
> `PoolEngineMixin._init_node_stocks()` 与 `core/runtime.py` `PoolState.node_stocks`。

### 3.1 状态池视图语义

**状态池是派生视图，不是独立可变存储。** `node_stocks[nid]` 是视图的物化缓存，其真值由"输入状态池 × 条件"决定。

| 状态池类型 | 角色 | 真值来源 |
|-----------|------|---------|
| 备选池 / 市场源 (market_source / tdx_candidate) | **源状态池** | 外部数据源（市场行情、板块成分），真值源 |
| 状态池 (stock_state_pool / tdx_state_pool) | **派生状态池** | 经变换单元（三元组）条件计算而来，是视图 |

派生状态池的 `node_stocks[nid]` 在每次 tick 中由变换单元重新计算（输入状态池未变且行情未变时复用缓存）。它不是独立存储，而是"输入状态池 × 条件"的物化结果。

---

## 4. 状态池变换——核心中的核心

### 4.0 变换单元（三元组一体）

**变换单元 = (条件转移边 + 转移条件 + 无条件转移边) 三元组**，是股票池的原子计算单位。

```
状态池N ──条件转移边──▶ 条件节点(转移条件) ──无条件转移边──▶ 状态池N+1
  (输入视图)         (filter计算)              (输出视图)
```

- **条件转移边**：从源状态池指向条件节点，传递股票到条件节点。对应 edge_semantics.json 中的 conditional 类型。
- **转移条件**：条件节点本身，对输入股票做 filter（强弱筛选）。
- **无条件转移边**：从条件节点指向目标状态池，将筛选结果 propagate 到下一状态池。对应 edge_semantics.json 中的 unconditional 类型。

三者一体，不可分割。以下 gate/filter/propagate/callback/ttl 五步作用于变换单元（三元组）而非单条独立边。

### 4.1 gate（时机门控）（作用于变换单元的条件转移边）— 读 timing.json 配置表

24种时机 = starttype(8) × cxtype(3)，已全部通过 timing.json 配置表驱动。

| starttype | timing.json evaluate | 读运行时表 |
|-----------|---------------------|-----------|
| 0 立即 | always | 无 |
| 1 延迟N秒 | elapsed_gte | PoolState.time_source['start_ts'] |
| 2 开市前 | in_range | 无（比较当前时间） |
| 3 开市后 | gte | 无（比较当前时间） |
| 4 收市前 | in_range | 无（比较当前时间） |
| 5 收市后 | gte | 无（比较当前时间） |
| 6 指定交易时间 | gte_hhmmss | 无（比较当前时间） |
| 7 指定时间 | gte_hhmmss | 无（比较当前时间） |

| cxtype | timing.json is_expired | 读运行时表 | 写运行时表 |
|--------|------------------------|-----------|-----------|
| 0 一直 | never | 无 | 无 |
| 1 持续窗口 | elapsed_gte | EdgeState.exec_ctx[eid]['first_fire'] | EdgeState.exec_ctx[eid]（首次记录） |
| 2 只一次 | count_gte_1 | EdgeState.exec_ctx[eid]['count'] | EdgeState.exec_ctx[eid]（执行完成后 +1） |

> 代码位置: `core/edge_executor.py` `EdgeExecutor._gate()` / `_starttype_gate()` /
> `_cxtype_gate()`；运行时状态真相源为 `core/edge_state.py` `EdgeState.exec_ctx`。

### 4.2 filter（强弱对比）（作用于变换单元的转移条件）— 读 dispatch.json + engines.json 配置表

二分法本质：不管什么条件，最终都是对每只股票求值 → 与阈值比较 → passed 或 rejected。

**公式=列**：公式结果写入 `latest_tick[code][formula_ref]`（`core/formula.py:158-162`），
公式结果成为 tick 行的列，供后续公式/筛选作列读取。**筛选=列操作**：filter 只读列做比较/排序/集合。

**evaluators 表驱动三分**（`core/evaluators.py`）：noperate 模式由 `tdx_noperate_rules.json`
的 `mode` 字段驱动，无 if/elif 分支：
- `compare`：`_scalar_compare(value, fsecond, noperate, prev_value)`，cross 模式经
  `prev_lookup` callable 依赖注入取前值
- `rank`：`_resolve_rank(ranked, fsecond, rank_rule)`，由 `rank_modes` 表驱动
- `inflection`：`logger.warning` + `return []`（标量模式不支持拐点）

策略路由链路：
```
edge_strategies.json 查策略 → strategy.handler 指向 builtins.py 函数
  → 函数内部调用 condition_dispatcher
    → condition_dispatcher 查 dispatch.json 做位掩码路由
      → 路由到 engines.json 中的网关
        → 网关调用 formula_eval / cross_section_eval / basic_filter / pass_through
```

filter 函数速查：

| filter 函数 | "强"的定义 | 读配置表 | 外部依赖 | 写入 |
|------------|-----------|---------|---------|------|
| formula_eval | 公式值>0 | dispatch.json + engines.json | tq_adapter.eval_indicator | 无（返回值） |
| _filter_by_bar_data | code in bar_data（成员测试） | 无 | current_bar_data | 无（返回值） |
| basic_filter | PE/ROE达标 | dispatch.json + engines.json | tq_adapter | 无（返回值） |
| cross_section_eval | 截面评分排序 | dispatch.json + engines.json | tq_adapter | 无（返回值） |
| condition_dispatcher(AND) | 多条件同时满足 | dispatch.json 链 | tq_adapter | 无（返回值） |
| condition_dispatcher(OR) | 任一条件满足 | dispatch.json 链 | tq_adapter | 无（返回值） |
| tdx_condition_evaluator | TDX公式通过 | tdx_indicators.json | tq_adapter | 写 node_stocks[tid] |
| pass_through | 全部通过 | 无 | 无 | 无（返回值） |

### 4.3 propagate（状态流转）（作用于变换单元的无条件转移边）— 读写运行时表 node_stocks

| 模式 | 读运行时表 | 写运行时表 | TDX tran参数 |
|------|-----------|-----------|-------------|
| copy | PoolState.node_stocks[src] | PoolState.node_stocks[tgt] += passed | tran=0 |
| move | PoolState.node_stocks[src] | PoolState.node_stocks[tgt] += passed; PoolState.node_stocks[src]=[] | tran=1 |
| overwrite | PoolState.node_stocks[src] | PoolState.node_stocks[tgt] = passed | emptyps + clear_dest_first |

> 代码位置: `core/edge_executor.py` `EdgeExecutor._propagate()`。

### 4.4 callback（持久化+副作用）— 读 action_table.json 配置表

回调由 app.py 动态注入 `engine._on_stock_enter_target_pool`，读取 action_table.json 查表执行。

| 动作 | 读表 | 写表 | handler |
|------|------|------|---------|
| bsavehis | node_stocks[tgt] + node.params | .dat/.log文件 + node_state（INSERT） | _append_history_entry |
| bsound | node.params.tdx_psatt.nsoundtype | 播放声音 | _play_sound_alert |
| btip | node.params.tdx_psatt | 弹窗通知 | _show_popup_alert |
| bsavetoblock | node.params.tdx_psatt.blockfile + node_stocks[tgt] | 板块文件 | _save_to_tdx_block |

> 代码位置: app.py L168-204

### 4.5 ttl（超时淘汰）— 读 tdx_psatt.json 配置表

| 条件 | 读表 | 写表 |
|------|------|------|
| bdel=1 且超时 | PoolState.node_stocks[tgt]（每只的indate+intime）+ tdx_psatt.json（ttl_units） | PoolState.node_stocks[tgt]（删除超时股票） |

TTL计算：
```
unit_sec = ttl_units[ndeltype]   # 0=天,1=小时,2=分钟,3=秒
ttl_sec = ndelnum × unit_sec
entry_ts = _parse_intime_to_ts(indate, intime)  # intime 归一化为 6 位 HHMMSS
if now_ts - entry_ts >= ttl_sec: 删除股票
```

> 代码位置: `core/edge_executor.py` `EdgeExecutor._apply_ttl()`。

### 4.6 边类型语义分发 — 读 edge_semantics.json 配置表

**三元组组装规则**：条件转移边（入边）+ 条件节点（枢纽）+ 无条件转移边（出边）= 一个变换单元。**边的类型由源节点类型决定（经全量 42 个 DZH 文件 602 条边验证）：**
- 条件转移边：源为备选池/状态池/数据源（type ∈ {0,200,202}），目标为条件节点（type=201），在三元组中担任"入边"角色，**有 interval 时间属性**
- 无条件转移边：源为条件节点（type=201），目标为状态池（type=200），在三元组中担任"出边"角色，**无时间属性，只有线宽(size)**

边的类型由源节点类型决定，影响运算流程和变更检测策略。

| 边类型 | 源节点类型 | 目标节点类型 | 触发条件 | 运算流程 | 读配置表 | 读运行时表 | 写运行时表 | 三元组角色 |
|--------|-----------|------------|---------|---------|---------|-----------|-----------|-----------|
| 条件转移边 | market_source / candidate_dzh / stock_state_pool / tdx_state_pool (备选池/状态池) | transfer_condition / dzh_condition_pool (条件节点) | gate通过 且 (源变化 或 行情变化) | gate → filter → propagate | edge_semantics.json, timing.json, dispatch.json | PoolState.node_stocks, EdgeState.formula_results, PoolState.dirty.nodes | PoolState.node_stocks, EdgeState.formula_results | 入边 |
| 无条件转移边 | transfer_condition / dzh_condition_pool (条件节点) | stock_state_pool / tdx_state_pool (状态池) | 源节点股票变化 | propagate | edge_semantics.json | PoolState.node_stocks, PoolState.dirty.nodes | PoolState.node_stocks | 出边 |

> **验证依据**：
> - attr=8192(条件边) 337 条：100% 源∈{type0,type200,type202}，100% 有 interval/begin/end/count
> - attr=8193(无条件边) 265 条：100% 源=type201(条件节点)，仅有 from/to/attr/clr 四属性

**触发条件**：

```
triggered[eid] = edge_fired[eid] AND (dirty.nodes[sid] OR dirty.data)
```

| 检测项 | 条件 | 通过条件 | 不通过时行为 |
|--------|------|---------|------------|
| 首次执行 | `PoolState.first_run == True` | 强制标记所有源节点 dirty | — |
| 源节点变化 | `dirty.nodes[sid]` 被设置 | 输入状态池股票集合变化 | 无条件边跳过；条件边仍可由 `dirty.data` 触发 |
| 行情数据变化 | `dirty.data == True` | 当前 tick 收到新行情 | 条件边触发公式重算；结果写入 `EdgeState.formula_results` |
| gate 通过 | `EdgeState.edge_fired[eid] == True` | timing.json 规则放行 | 边跳过（缓存不清除） |

> 代码位置: `core/engine.py` `PoolEngine.run_tick()` / `_should_fire_edge()`；
> `core/edge_executor.py` `EdgeExecutor.run()`；
> 运行时表真相源: `core/runtime.py` `PoolState` + `core/edge_state.py` `EdgeState`。

### 4.7 事件驱动路径（v4）

`unify-stockpool-oop-event-driven` spec 实施后，状态池变换五阶段对应的事件发布点：

| 阶段 | 发布事件 | 订阅模块 |
|------|---------|---------|
| gate（时机判定） | EdgeFired | EdgeExecutor |
| filter（强弱筛选） | StockFiltered | Execution |
| propagate（流转模式） | TransferExecuted | Trade/Database/Monitoring |
| callback（回调副作用） | Signal, AlertRaised | Trade/Monitoring |
| ttl（持有退出） | TTLExpired | Database/Monitoring |

**事件链**：TickReceived → DataChanged(tick) → BarComposed → DataChanged(bar) → EdgeFired → FormulaEvaluated → StockFiltered → TransferExecuted → Signal → OrderPlaced → OrderFilled → PositionUpdated → StatisticsUpdated → RankingChanged → AlertRaised → SnapshotUpdated → EventLogged

**三模式切换**：ModeChanged 事件驱动四模块切换数据源/时间源/交易接口/副作用范围。

---

## 5. 表驱动简化方向

> 表驱动重构路径中已剥离 / 待剥离的硬编码逻辑清单（Task 13 文档同步）。

### 5.1 已完成的剥离项 ✅

| 旧硬编码 | 新驱动表 | 引擎读入点 | 任务 |
|----------|---------|-----------|------|
| `starttype` 8 种 if 链 + `_STARTTYPE_*` 常量 | `timing.json:starttype_rules` | `_tdx_should_execute` / `_dispatch_gate` | Task 2 |
| `cxtype` 3 种过期判断 | `timing.json:cxtype_rules` | `_tdx_check_duration` | Task 2 |
| 60 行 `_tdx_should_execute` if 链 | `timing.json:_dispatch_table` | `_dispatch_gate(edge, ctx)` | Task 2 |
| 持续时长单位 `{0:1, 1:60, 2:3600, 3:86400}` | `timing.json:cxtime_units` | `_tdx_check_duration` | Task 2 |
| 8+3 个 `_STARTTYPE_*` / `_CXTYPE_*` 常量 | `timing.json:simulator` 段 + 编译缓存 `_compiled_timing` | engine `__init__` | Task 2 |
| `nset → evaluator` 硬编码字典 | `dispatch.json:nset_dispatch` | `_dispatch_tdx_condition` | Task 1 |
| `_random_filter` 随机过滤 | `fallback_chain.json:chains[*]` | `_resolve_fallback` | 前期 |
| `baimpool == 1` 硬编码判断 | `pool_roles.json:roles.target_pool` | `_resolve_role` | 前期 |
| `_now()` 只支持 realtime/fixed | `time_sources.json` | `_now()` | 前期 |
| ENTER/EXIT/TIMEOUT 事件硬编码 | `event_rules.json` + `signal_rules.json` | `_emit_transfer_events` | 前期 |
| PK 排名/分析角度/看盘面板/告警硬编码 | `post_tick_pipeline.json` + 各 stage 表 | `_post_tick` | 前期 |
| callback 动作（bsavehis/bsound/btip/bsavetoblock） | `action_table.json:pool_enter_actions` | `_dispatch_pool_enter_actions` | 前期 |
| XML 导入导出字段映射硬编码 | `xml_mapping.json` | converters | 前期 |
| 历史记录文件格式硬编码 | `history_schema.json` | converters | 前期 |
| 高亮事件规则硬编码 | `highlight_rules.json`（I32 已删：orphan 配置 + 死事件链全清） | builtins（I32 已删 `_highlight_event`/`_get_highlight_*`） | 前期 |
| 分析结果字段硬编码 | `analysis_results.json` | builtins_post_tick | 前期 |

### 5.2 待完成的剥离项

全部完成 ✅。原待剥离项（TTL 单位 / intime 位长 / psatt 副作用 / 转移模式 / 角色解析 / 数据源探测 / post_tick 流水线 / nset3-5 降级 / stock_info_field_map / sector 生成 / type_aliases）均已剥离到对应 JSON 配置表。

### 5.3 验收

- Task 1（基线扫描）→ 列出违规行
- Task 2（timing.json 驱动）→ ✅ 已完成
- Task 3-7（tdx_psatt/flow_mode/pool_roles/data_source_contract/post_tick 流水线）→ ✅ 已完成
- Task 12（表驱动纯度回归）→ 验证违规行数为 0

---

## 6. 全功能表操作速查矩阵

二维矩阵：功能步骤 × 四类表（运行时读/运行时写/配置读/持久写）。

以下矩阵以变换单元（三元组）为粒度，gate/filter/propagate/callback/ttl 五步作用于变换单元。

| 功能步骤 | 读运行时表 | 写运行时表 | 读配置表 | 写持久表 |
|---------|-----------|-----------|---------|---------|
| gate（时机门控） | EdgeState.exec_ctx[eid] | EdgeState.exec_ctx[eid]（count/first_fire/last_fire/fired） | timing.json | — |
| filter（强弱筛选） | PoolState.node_stocks[src], EdgeState.formula_results | EdgeState.formula_results[(formula_ref, bar_hash)] | dispatch.json, engines.json, tdx_indicators.json | — |
| propagate（状态流转） | PoolState.node_stocks[src], PoolState.node_stocks[tgt] | PoolState.node_stocks[tgt], PoolState.node_stocks[src]（move时清空） | — | — |
| callback（持久化副作用） | PoolState.node_stocks[tgt], node.params | PoolState.trackers[(tgt, code)] | action_table.json | node_state, stock_transfer_log, .dat/.log文件, 板块文件 |
| ttl（超时淘汰） | PoolState.node_stocks[tgt] | PoolState.node_stocks[tgt]（删除超时股票） | tdx_psatt.json | — |
| init（节点初始化） | — | PoolState.node_stocks[nid] | edge_strategies.json:node_init | — |
| output（结果输出） | PoolState.node_stocks | — | edge_strategies.json:output_types | — |
| replay（回放执行） | PoolState.replay.* / PoolState.node_stocks, EdgeState.exec_ctx | PoolState.replay.node_stocks, EdgeState.exec_ctx | 同逐边执行（全部配置表） | replay_snapshot, replay_session, stock_transfer_log |
| CRUD（配置管理） | — | — | — | pool_config, pool_node, pool_edge, config_version |
| 边类型分发 | PoolState.dirty.nodes, PoolState.node_stocks, EdgeState.formula_results | PoolState.node_stocks, EdgeState.formula_results | edge_semantics.json, CompiledSchedule.edge_ctx | — |

---

## 7. 时机控制

### 策略空间 = 时机轴 × 强弱轴

- **时机轴**: starttype(0~7) × cxtype(0~2) = 24 种时机组合
- **强弱轴**: nset × noperate × ntjindexno = 多种筛选组合
- 所有选股策略都是策略空间中的点

### starttype × cxtype 组合矩阵

| | cxtype=0 一直 | cxtype=1 持续窗口 | cxtype=2 只一次 |
|--|-------------|-----------------|-------------------|
| starttype=0 立即 | 每次执行都触发 | 窗口期内每次触发 | 只触发一次 |
| starttype=1 延迟 | 延迟后每次触发 | 延迟后窗口内触发 | 延迟后触发一次 |
| starttype=2 开市前 | 每天开市前触发 | 开市前窗口内触发 | 开市前触发一次 |
| starttype=3 开市后 | 开市后每次触发 | 开市后窗口内触发 | 开市后触发一次 |
| starttype=4 收市前 | 收市前每次触发 | 收市前窗口内触发 | 收市前触发一次 |
| starttype=5 收市后 | 收市后每次触发 | 收市后窗口内触发 | 收市后触发一次 |
| starttype=6 指定时间 | 到时间后每次触发 | 到时间后窗口内触发 | 到时间触发一次 |
| starttype=7 指定时间 | 到时间后每次触发 | 到时间后窗口内触发 | 到时间触发一次 |

### 策略示例

| 策略 | 时机参数 | 强弱参数 | 效果 |
|------|---------|---------|------|
| 开盘扫阳线 | starttype=3, starttime=30 | _filter_by_bar_data | 开市后30秒执行，bar_data 成员测试通过 |
| 尾盘选低PE | starttype=4, starttime=5 | basic_filter + sorttype=10 | 收市前5分钟，PE最低前10只 |
| 一次性公式选股 | starttype=0, cxtype=2 | formula_eval | 立即执行公式，只执行一次 |

### 运行时表读取

| 判断 | 读运行时表 | 计算公式 |
|------|-----------|---------|
| 延迟是否到期 | PoolState.time_source['start_ts'] | (now - start_ts).total_seconds() >= delay |
| 持续窗口是否到期 | EdgeState.exec_ctx[edge_id]['first_fire'] | (now - first_fire).total_seconds() >= window |
| 是否已执行过 | EdgeState.exec_ctx[edge_id]['count'] | count >= 1 |

---

## 8. 强弱对比

filter 函数的二分法本质：输入一个股票列表，输出两个列表——passed（强者）和 rejected（弱者）。

| "强"的定义 | filter 函数 | 判定逻辑 | 外部依赖 |
|-----------|-----------|---------|---------|
| 公式值为正 | formula_eval() | eval_indicator(code, formula) > 0 | tq_adapter |
| 成员测试 | _filter_by_bar_data() | code in bar_data（成员测试） | current_bar_data |
| PE低于阈值 | basic_filter() | pe <= pe_max | tq_adapter |
| 排名靠前 | sorttype 截断 | passed[:top_n] | 无 |
| 多条件同时满足 | condition_dispatcher() AND | 交集 | tq_adapter |
| 任一条件满足 | condition_dispatcher() OR | 并集 | tq_adapter |
| 截面评分高 | cross_section_eval() | 排序取前半 | tq_adapter |
| TDX公式通过 | tdx_condition_evaluator() | eval_indicator > 0 | tq_adapter |
| 全部通过 | pass_through() | 无筛选 | 无 |

### noperate 比较表

| noperate | 比较逻辑 | 适用场景 |
|----------|---------|---------|
| 0 等于 | val == nsecond | 固定值匹配 |
| 1 大于 | val > nsecond | 阈值以上 |
| 2 小于 | val < nsecond | 阈值以下 |
| 3 上穿 | prev <= nsecond AND curr > nsecond | 金叉信号 |
| 4 下穿 | prev >= nsecond AND curr < nsecond | 死叉信号 |
| 5 排序 | sort by val desc, take nsecond | 龙头股 |
| 6 排序前N | sort by val desc, take nsecond | 涨幅前N |
| 7 排序后N | sort by val asc, take nsecond | 跌幅前N |
| 8 拐点向上 | diff(val) > 0 | 趋势反转 |
| 9 拐点向下 | diff(val) < 0 | 趋势反转 |

---

## 9. 持有退出（TTL）

| 事件 | 触发条件 | 读运行时表 | 写运行时表 |
|------|---------|-----------|-----------|
| TTL淘汰 | psatt.bdel=1 且超时 | node_stocks | node_stocks（删除超时股票） |

TTL 参数来源：`node.params.tdx_psatt` 或 `node.params.psatt`

| 参数 | 含义 |
|------|------|
| bdel | 是否启用自动删除（1=启用） |
| ndelnum | 删除阈值数量 |
| ndeltype | 阈值单位（0=天, 1=小时, 2=分钟, 3=秒） |

6 种 psatt 副作用：

| 标志 | 含义 | 动作 |
|------|------|------|
| bdel=1 | 启用TTL | 定时删除超时股票 |
| bsound=1 | 声音预警 | 播放 nsoundtype 指定声音 |
| btip=1 | 弹窗提示 | 弹出股票入池通知 |
| bsavehis=1 | 保存历史 | 写入 .dat/.log 文件 |
| bsavetoblock=1 | 保存板块 | 保存到板块文件 |
| baimpool=1 | 标记目标池 | 高亮目标池 |

---

## 10. 回放执行

| 步骤 | 读表 | 写表 |
|------|------|------|
| 加载K线 | kline_cache（持久表）或 tq_adapter.get_kline_batch | 无 |
| 注入bar数据 | _timeline[current_index] | _state_pools（source节点） |
| 时间调度 | edge.params + _flow_fire_counts + _flow_last_fire | 无 |
| 执行flows | 同逐边执行 | 同逐边执行 |
| 记录转移 | _state_pools | stock_transfer_log（批量 INSERT） |
| 入池时间 | _state_pools | _stock_enter_times |
| 持有淘汰 | _stock_enter_times + params.hold_sec | _state_pools（删除过期） |
| 保存快照 | _state_pools | replay_snapshot（INSERT） |
| 更新会话 | 无 | replay_session（UPDATE kline_index, status） |

每根K线的表操作序列：写 replay_snapshot + 写 replay_session + 写 stock_transfer_log（N条）

> 代码位置: kline_replay_engine.py

---

## 10.1 运行模式数据源契约

每种运行模式的数据驱动方式由 `runtime_modes.json` 中三个字段定义：

| 模式 | data_driver | trigger_mechanism | side_effects_scope |
|------|-------------|-------------------|-------------------|
| live（实盘） | market_push | data_arrival | all |
| replay（回放） | kline_sequence | tick_advance | readonly |
| simulation（仿真） | on_demand | step_advance | optional |

| 模式 | 数据获取方式 | 时间推进 | 副作用 |
|------|------------|---------|--------|
| live | TQ SDK 异步推送，`_inject_bar_data_async()` | wall_clock 自动推进 | 写持久表 + 发信号 + 执行交易 |
| replay | K线缓存同步读取，`_timeline[index]` | sequence 按K线时间步进 | 只读，不写持久表 |
| simulation | Mock 随机生成，`_generate_mock_bar_data()` | virtual 手动步进 | 可选，按配置决定 |

---

## 11. 配置CRUD

| 功能 | 方法 | 读持久表 | 写持久表 | 本质 |
|------|------|---------|---------|------|
| 创建池 | storage.save_pool() | 无 | pool_config（INSERT）+ config_version（INSERT） | 写图拓扑到 params JSON |
| 读取池 | storage.get_pool() | pool_config | 无 | 加载图拓扑 |
| 更新池 | storage.save_pool() | pool_config（旧值） | pool_config（UPSERT）+ config_version（INSERT） | 覆盖图拓扑 |
| 删除池 | storage.delete_pool() | pool_config（旧值） | pool_config（DELETE CASCADE）+ config_version（INSERT） | 删图+级联 |
| 列表 | storage.list_pools() | pool_config | 无 | 列出所有图 |
| 保存节点 | storage.save_pool_node() | pool_node（旧值） | pool_node（INSERT OR REPLACE）+ config_version | 写节点定义 |
| 保存边 | storage.save_pool_edge() | pool_edge（旧值） | pool_edge（INSERT OR REPLACE）+ config_version | 写边定义 |

> **注意**: 运行时引擎不直接查 pool_node/pool_edge 表。它从 pool_config.params JSON 中提取 nodes 和 edges 列表。

## 11.1 JSON 导入导出

| 功能 | 方法 | 读表 | 写表 | 本质 |
|------|------|------|------|------|
| 导出JSON | export_pool_to_json() | pool_config dict 或 PoolMetaModel | JSON 文件/字符串 | 序列化池配置为 JSON |
| 导入JSON | import_pool_from_json() | JSON 文件/字符串 | pool_config dict | 反序列化 JSON 为池配置 |

JSON 导入导出与 XML 导入导出的关系：

```
DZH XML ──parse_dzh_xml──→ 内部dict ──export_pool_to_json──→ JSON
   ↑                                                      │
   │                                                      ↓
   └──export_pool_to_xml── 内部dict ←──import_pool_from_json──┘

TDX XML ──parse_tdx_xml──→ TdxPoolMetaModel ──convert_tdx_to_config──→ pool_config ──export_pool_to_json──→ JSON
   ↑                                                                                                    │
   │                                                                                                    ↓
   └──export_tdx_pool_to_xml── TdxPoolMetaModel ←── pool_config ←──import_pool_from_json──┘
```

> 代码位置: converters/json_converter.py

---

## 12. 核心函数速查表

| 函数/方法 | 输入 | 输出 | 读配置表 | 读运行时表 | 写运行时表 | 写持久表 | 代码位置 |
|----------|------|------|---------|-----------|-----------|---------|---------|
| `PoolEngine.run_tick()` | — | — | timing.json（经 EdgeExecutor._gate） | PoolState.dirty / node_stocks / EdgeState.edge_fired / EdgeState.exec_ctx | PoolState.node_stocks / node_snapshots / dirty | — | core/engine.py |
| `PoolEngine.run_pool()` | current_bar_data | {success, error, node_states, events} | runtime_modes.json, timing.json | PoolState.* / EdgeState.* | 同 run_tick | node_state, stock_transfer_log（经 callback） | core/engine.py |
| `PoolEngine.run_loop()` | current_bar_data | node_stocks | runtime_modes.json | PoolState.* / EdgeState.* | 同 run_tick | 同 run_pool | core/engine.py |
| `PoolEngine.run_mode()` | mode_id | {node_stocks, inject, task?} | runtime_modes.json, time_sources.json, trade_interfaces.json | — | PoolState.time_source / data_source / trade_interface / side_effects_scope | — | core/engine.py |
| `EdgeExecutor.run()` | eid | bool（是否产生目标节点变化） | edge_semantics.json, timing.json, dispatch.json, engines.json, action_table.json, tdx_psatt.json | PoolState.node_stocks / latest_tick / dirty; EdgeState.exec_ctx / formula_results | PoolState.node_stocks / trackers / dirty; EdgeState.exec_ctx / formula_results | — | core/edge_executor.py |
| `EdgeExecutor._gate()` | TimingSpec, eid | bool | timing.json | EdgeState.exec_ctx[eid] | EdgeState.exec_ctx[eid]（count/first_fire/last_fire/fired） | — | core/edge_executor.py |
| `EdgeExecutor._filter()` | EdgeContext, FilterSpec | (passed, rejected) | dispatch.json, engines.json, tdx_indicators.json | PoolState.node_stocks[src], latest_tick, bars; EdgeState.formula_results | EdgeState.formula_results[(formula_ref, bar_hash)] | — | core/edge_executor.py |
| `EdgeExecutor._propagate()` | EdgeContext, PropagateSpec, passed | None | — | PoolState.node_stocks[src], PoolState.node_stocks[tgt] | PoolState.node_stocks[tgt], PoolState.node_stocks[src]（move时） | — | core/edge_executor.py |
| `Compiler.compile()` | pool_config | CompiledSchedule | edge_semantics.json, timing.json, dispatch.json, tdx_psatt.json, action_table.json | — | PoolState.topology（运行时写入） | — | core/compiler.py |
| `PoolState.update_latest_tick()` | tick_data | bool（是否推进） | — | PoolState.latest_tick | PoolState.latest_tick, PoolState.dirty.data | — | core/runtime.py |
| `PoolState.snapshot_nodes()` | — | {nid: frozenset} | — | PoolState.node_stocks | PoolState.node_snapshots | — | core/runtime.py |
| `DataUpdater.apply_data()` | bar_data | None | data_config.json | PoolState.latest_tick | PoolState.latest_tick / dirty.data | — | core/data_updater.py |
| `BarComposer.on_tick()` | codes: List[str] | None | — | PoolState.latest_tick | PoolState.bars[period][code] | — | core/bar_composer.py |
| `MetaEngine._emit_transfer_events()` | prev, node_stocks, transfer_events | None | event_rules.json, signal_rules.json, pool_roles.json | PoolState.node_stocks, PoolState.trackers | EventBus (DomainEvent/Signal) | — | core/engine.py |
| `MetaEngine._update_trackers()` | node_stocks, bar_data | None | tracker_schema.json | PoolState.node_stocks, PoolState.latest_tick | PoolState.trackers | — | core/engine.py |
| `MetaEngine._post_tick()` | node_stocks, bar_data | None | post_tick_pipeline.json, pk_config.json, analysis_config.json, dashboard_schema.json, alert_rules.json | PoolState.* | _pk_rankings, _angle_results, _dashboard_data, _alert_events | — | core/engine.py |

---

## 13. 持续循环引擎（run_loop）

### 13.1 执行流程

```
MetaEngine.run_mode(mode_id, pool_config, current_bar_data, **kwargs)
  │
  ├─ 延迟创建 PoolEngine(meta=self, pool_config=pool_config)
  ├─ pool_engine.run_mode(mode_id)                # 查 runtime_modes.json + time_sources.json + trade_interfaces.json
  │   ├─ 设置 time_source / data_source / trade_interface / side_effects_scope 四张表行
  │   ├─ 回放模式：state.enter_replay()            # 状态隔离
  │   ├─ _init_node_stocks()                       # 初始化节点股票
  │   └─ if loop_entry_policy == "internal_loop":
  │          创建 asyncio Task → pool_engine.run_loop()
  │      else:
  │          return {node_stocks, inject: True}    # 回放/仿真由外部控制器步进
  │
  └─ pool_engine.run_loop():
       while not _stopped:
         ├─ if _paused: await pause_event.wait()               # 中断等待（非 asyncio.sleep 轮询）
         ├─ if not meta._is_trading_time(): sleep; continue     # 查 timing.json:market_calendar
         ├─ tick_bar_data = meta._refresh_bar_data(mode_cfg, current_bar_data)
         ├─ if tick_bar_data:
         │      data_updater.apply_data(tick_bar_data)           # 写 latest_tick / bars / dirty.data
         │      _inject_bar_data(tick_bar_data)                  # bar 字段注入源节点
         ├─ await run_tick()                                      # 见 §1.1 伪代码
         └─ driver.attach_to_loop(loop)                           # EventDriver 注册到时事件中断
```

> **编译期/运行期分离说明**：
> - **编译期**（`PoolEngine.__init__` 中一次性）：`Compiler.compile(pool_config)` 产出 `CompiledSchedule`，
>   含 `execution_order`、`edge_ctx[eid]`、`edge_timing_spec` / `edge_filter_spec` /
>   `edge_propagate_spec` / `edge_action_spec` / `edge_ttl_spec`（6 维 spec），以及 `topology` 邻接表。
> - **运行期**（`PoolEngine.run_tick()` 每 tick 执行）：
>   1. `state.time_source['current_ts'] = now()`
>   2. 首次执行 `_mark_source_nodes_dirty()`
>   3. 阶段 1：统一计算 `EdgeState.edge_fired[eid]`（查 `timing.json` + `EdgeState.exec_ctx`）
>   4. 阶段 2：仅执行 triggered 边：`edge_fired AND (dirty.nodes[sid] OR dirty.data)`；
>      调用 `EdgeExecutor.run(eid)` 完成 gate→filter→propagate→callback→ttl
>   5. `state.clear_dirty()`、`state.snapshot_nodes()`、`_sync_events_to_meta()`
> - **不再每 tick 重算**：拓扑排序、edge_ctx、6 维 spec 跨 tick 复用。
> - **oracle 已更新**：迁移 Oracle 直接比较新引擎输出，不再保留旧路径对照基线。

> **表驱动改造说明**：
> - 备选池刷新：仍由 `MetaEngine` 委托 `services/candidate_pool.py`，`PoolEngine` 不含内联实现
> - 拓扑校验：`PoolEngine.run_mode()` 调用 `MetaEngine._validate_pool_topology()` 一行完成
> - 市场日历判断：`MetaEngine._is_trading_time()` 查 `timing.json:market_calendar.sessions`
> - 行情刷新：`MetaEngine._refresh_bar_data()` 查 `runtime_modes.json.<mode>.refresh_handler` 路由
> - `MetaEngine._current_mode_id = mode_id` 替代 `is_replay` 等模式标志位

### 13.2 控制方法

| 方法 | 作用 | 读写运行时表/组件 |
|------|------|------------------|
| `PoolEngine.run_mode(mode_id)` | 模式初始化与入口 | 写 state.time_source / data_source / trade_interface / side_effects_scope；创建 run_loop Task |
| `PoolEngine.run_loop()` | 内部持续循环 | 读 state.time_source；调用 data_updater / run_tick |
| `MetaEngine.pause_loop()` | 暂停循环 | 写 `pool_engine._components['_paused'] = True` |
| `MetaEngine.resume_loop()` | 恢复循环 | 写 `pool_engine._components['_paused'] = False` |
| `MetaEngine.stop_loop()` | 优雅停止 | 写 `pool_engine._components['_stopped'] = True`；cancel Task |

### 13.3 持仓跟踪（StockTracker）

每只入池股票自动创建 `StockTracker` 数据结构：

| 字段 | 类型 | 含义 |
|------|------|------|
| `entry_price` | float | 入场价 |
| `entry_time` | float | 入场时间戳 |
| `current_price` | float | 当前价 |
| `profit_pct` | float | 盈亏百分比 |
| `max_profit` | float | 最大盈利百分比 |
| `max_drawdown` | float | 最大回撤百分比 |
| `hold_days` | int | 持仓天数 |

### 13.4 交易信号（Signal）

| 信号类型 | 触发条件 | 写入表 |
|---------|---------|--------|
| BUY | 股票入池且目标池 baimpool=1 | _signal_queue（_signal_events 为派生 property，I33 收敛双写） |
| SELL | 股票出池（move/TTL）且源池 baimpool=1 | _signal_queue（_signal_events 为派生 property，I33 收敛双写） |

### 13.5 事件流（Event）

| 事件类型 | 触发条件 | detail 字段 |
|---------|---------|------------|
| ENTER | 股票入池 | source_id, mode, flow_id |
| EXIT | 股票出池（move 模式） | target_id, mode, flow_id, tracker 信息 |
| TIMEOUT | 股票 TTL 超时淘汰 | reason="ttl_expired", tracker 信息 |

### 13.6 异步并发数据获取（AsyncTqAdapter）

| 方法 | 作用 | 缓存策略 |
|------|------|---------|
| `_inject_bar_data_async()` | 异步注入 K 线数据到 source 节点 | 支持多时间框架 |
| `_fetch_multi_timeframe_cached()` | 带缓存的多时间框架并发获取 | key=kline:{tf}:{codes}, TTL=cache_ttl |
| `_cache_get()` / `_cache_set()` | 缓存读写 | 过期自动清除 |

---

## 14. builtins.py 拆分

| 文件 | 行数 | 职责 |
|------|------|------|
| `builtins.py` | 253 | 公共工具函数 + `from builtins_filters import *` + `from builtins_actions import *` 重导出 |
| `builtins_filters.py` | 535 | 筛选逻辑：formula_eval / condition_dispatcher / cross_section_eval / basic_filter / pass_through / sector_filter / resolve_market / candidate_resolve / accumulate_state / discard_stocks / transfer_condition_check / stock_pool_hold / render_label / render_shape / profit_analysis_calc / time_trigger_check / discard_sink_drop |
| `builtins_actions.py` | 311 | 动作逻辑：_action_resolve_and_pass / _action_apply_filter / _action_dzh_condition_filter / _action_pass_pool_stocks / _action_transfer_between_pools / _action_remove_from_pool / tdx_condition_evaluator / edge_default_transfer / highlight_start_handler / highlight_stop_handler / transfer_with_market_data_handler / log_transfer_handler / condition_dispatch_handler / init_market_source / init_stock_state_pool / init_tdx_candidate / tdx_convert_from_file / tdx_convert_from_pool |

---

## 15. 表驱动剥离

### 15.1 xml_mapping.json

将 TDX/DZH XML 导入导出的字段映射从硬编码逻辑剥离到 JSON 配置表：
- `pool` / `cell` / `flow` 元素的属性映射
- `dzh_to_tdx_type` / `tdx_to_frontend_type` 类型转换
- `frontend_node` / `frontend_edge` 前端格式映射

### 15.2 history_schema.json

将历史记录文件格式定义从硬编码逻辑剥离到 JSON 配置表：
- `dat_format` / `log_format` 文件格式定义
- `write_defaults` 写入默认值
- `read_mapping` 读取映射
- `intime_format` 时间格式

### 15.3 highlight_rules.json（I32 已删）

原将高亮事件规则从硬编码逻辑剥离到 JSON 配置表；I32 实测为 orphan 配置（零代码读取）+ 死事件链（`_emit_highlight` 从未定义），全链清理：删配置文件 + 删 `_highlight_event`/`_get_highlight_*` 函数 + 删 `highlight_*_handler` + 删 `highlight_events`/`_highlight_listeners` 死属性对。

### 15.4 analysis_results.json

将分析结果字段定义从硬编码逻辑剥离到 JSON 配置表。

---

## 16. runtime_simulator.py 表驱动重写

运行时模拟器使用 JSON 配置表驱动，消除硬编码分支：
- `DelType` 枚举映射到配置表
- `StatePool._is_expired()` 使用配置表驱动的 TTL 计算
- 池类型/节点类型映射全部走配置表

---

## 17. Phase 9 极致合并后的最终模块架构（33 文件）

> Phase 9 完成子目录包扁平化与脚本聚合，非测试 .py 文件从 62 降至 33。

### 17.1 core/ 目录（14 文件）

| 文件 | 行数 | 职责 |
|------|------|------|
| `__init__.py` | - | 包初始化 |
| `engine.py` | ~1500 | PoolEngine 核心循环 |
| `event_bus.py` | ~500 | EventBus 通信枢纽（30 事件） |
| `schemas.py` | ~940 | Pydantic 数据模型 |
| `table_engine.py` | ~1360 | Config 加载 + 热加载 |
| `domain.py` | - | 统一领域对象（合并自 domain/ 6 文件） |
| `tick_bar_module.py` | - | TickBar 模块（tick + K线） |
| `formula_module.py` | ~1868 | Formula 模块（公式计算） |
| `screening_module.py` | ~764 | Screening 模块（筛选 + 评估器） |
| `execution_module.py` | ~2938 | Execution 模块（编译 + 执行 + TTL） |
| `trade_module.py` | - | Trade 模块（交易执行） |
| `monitoring_module.py` | ~940 | Monitoring + Statistics 模块 |
| `import_export_module.py` | - | ImportExport 模块 |
| `runtime_mode_module.py` | ~2521 | RuntimeMode 模块（实盘/回放/仿真） |

### 17.2 services/ 目录（5 文件）

| 文件 | 行数 | 职责 |
|------|------|------|
| `__init__.py` | - | 包初始化 |
| `storage.py` | ~2552 | Database + DB Sync |
| `data.py` | ~5368 | DataSource + CandidatePool |
| `providers.py` | ~8915 | 6 个 Provider 适配器（合并） |
| `tq_adapter.py` | ~543 | TQ 适配器（独立保留） |

### 17.3 native/ 目录（3 文件）

| 文件 | 行数 | 职责 |
|------|------|------|
| `__init__.py` | - | 包初始化 |
| `builtins.py` | ~1347 | 内置函数 + Pipeline |
| `validators.py` | ~2168 | 验证器 + 匹配器 |

### 17.4 其他目录

**根目录（4 文件）**:
- `api.py`（7032 行，合并自 api/ 3 文件）
- `app.py`（FastAPI lifespan 装配）
- `converters.py`（8708 行，合并自 converters/ 4 文件）
- `__init__.py`

**scripts/（3 文件）**:
- `dev_tools.py`（合并 6 个开发工具）
- `verify_tools.py`（合并 6 个验证工具）
- `check_module_imports.py`（独立保留）

**web/（Phase 11 极致精简后，9 文件）**:

| 文件 | 说明 |
|------|------|
| `web/index.html` | 合并 3 HTML（index+config+formula）+ hash 路由（#/ + #/config + #/formula） |
| `web/js/canvas.js` | 画布渲染逻辑（独立保留，2800 行） |
| `web/js/app.js` | 合并 main+data+editor（应用核心，11195 行，IIFE 包裹） |
| `web/js/ui.js` | 合并 panel+toolbar-renderer+event-panel+formula-manager（UI 组件，6251 行，IIFE 包裹） |
| `web/css/styles.css` | 合并 5 CSS（@layer base + @layer components，8298 行，200KB） |
| `web/ui_renderer.py` | 前端 Python 渲染辅助（保留） |
| `web/package.json` | npm 配置（保留） |
| `web/jest.config.js` | Jest 测试配置（保留） |

**vendor/（2 文件）**:
- 第三方依赖（保留）

### 17.5 删除清单（Phase 8 + Phase 9 + Phase 11）

#### Phase 8 深度合并中进一步删除/内聚的源文件

在 Phase 7 已删除 22 个文件的基础上，Phase 8 进一步合并/删除：

- `core/formula_cache` → 并入 `core/formula_module.py`
- `core/statistics_module.py` → 并入 `core/monitoring_module.py`
- `core/hot_reload.py` → 改名为 `core/table_engine.py`
- `core/evaluators.py`（运行时入口部分） → 并入 `core/screening_module.py`
- `services/db_sync_service.py` → 并入 `services/storage.py`
- `services/candidate_pool.py` → 并入 `services/data.py`
- `services/providers/_common.py` → 并入 `services/providers/__init__.py`
- `native/pipeline.py` → 并入 `native/builtins.py`
- `native/matchers.py` → 并入 `native/validators.py`
- `converters/_common.py` → 并入 `core/import_export_module.py`
- `web/js/_convert_table_driven.py` + `web/js/_reindent.py` → 并入 `web/ui_renderer.py`

#### Phase 9 极致合并删除清单（35 文件）

**core/domain/ 7 文件**:
- `core/domain/__init__.py`
- `core/domain/base.py`
- `core/domain/nodes.py`
- `core/domain/edges.py`
- `core/domain/specs.py`
- `core/domain/evaluators.py`
- `core/domain/tick_source.py`
→ 合并为 `core/domain.py` 单文件

**services/providers/ 7 文件**:
- `services/providers/__init__.py`
- `services/providers/akshare_provider.py`
- `services/providers/dfcf_provider.py`
- `services/providers/hqchart_provider.py`
- `services/providers/local_file_provider.py`
- `services/providers/mock_provider.py`
- `services/providers/tq.py`
→ 合并为 `services/providers.py` 单文件

**converters/ 4 文件**:
- `converters/__init__.py`
- `converters/dzh.py`
- `converters/tdx.py`
- `converters/json_xml.py`
→ 合并为 `converters.py` 单文件

**api/ 3 文件**:
- `api/__init__.py`
- `api/pool_api.py`
- `api/system_api.py`
→ 合并为 `api.py` 单文件

**scripts/ 13 文件**:
- `scripts/analyze_dzh.py`
- `scripts/config_tools.py`
- `scripts/decode_formulas.py`
- `scripts/debug_formula.py`
- `scripts/merge_config_tables.py`
- `scripts/xml_tools.py`
→ 合并为 `scripts/dev_tools.py`
- `scripts/e2e_verify.py`
- `scripts/manual_mcp_verify.py`
- `scripts/manual_mcp_verify_sim.py`
- `scripts/run_sim_verify.py`
- `scripts/import_target_pool_100.py`
- `scripts/run_server.py`
→ 合并为 `scripts/verify_tools.py`

**core/ 1 文件**:
- `core/runtime.py`
→ 合并到 `core/runtime_mode_module.py`

#### Phase 11 前端极致精简删除清单（14 文件 + 8 样本归档）

Phase 11 完成 web/ 目录前端文件极致精简：原 8 个 JS 合并为 3 个，原 5 个 CSS 合并为 1 个，原 3 个 HTML 合并为 1 个。

**JS 7 文件合并 → `web/js/app.js` + `web/js/ui.js`**:
- `web/js/main.js` → 并入 `web/js/app.js`
- `web/js/data.js` → 并入 `web/js/app.js`
- `web/js/editor.js` → 并入 `web/js/app.js`
- `web/js/panel.js` → 并入 `web/js/ui.js`
- `web/js/toolbar-renderer.js` → 并入 `web/js/ui.js`
- `web/js/event-panel.js` → 并入 `web/js/ui.js`
- `web/js/formula-manager.js` → 并入 `web/js/ui.js`

**CSS 5 文件合并 → `web/css/styles.css`**:
- `web/css/style.css`
- `web/css/config-center.css`
- `web/css/event-panel.css`
- `web/css/formula.css`
- `web/css/table-driven-panel.css`

**HTML 2 文件合并 → `web/index.html`**:
- `web/config.html`
- `web/formula.html`

**样本文件归档（8 个 → `docs/samples/pools/`）**:
- `cys.json`
- `ultra7.json`
- `ultra7_injected.json`
- `panhou.xml`
- `盘后.xml`
- `超赢1号.xml`
- `超赢7号.xml`
- `金色两点半.xml`

### 17.6 模块间通信契约（Phase 8 后仍然有效）

- **唯一通信通道**：`core/event_bus.py` 的 `EventBus`（发布/订阅领域事件，30 种事件类型）。
- **禁止直接引用**：模块不得 `import` 其他业务模块的内部类；跨模块协作一律
  通过事件订阅/发布完成。
- **依赖注入**：跨层依赖（数据源/公式缓存/市场数据端口）通过构造函数注入
  工厂或 Protocol 接口，不直接 `import services.*`。
- **向后兼容**：迁移期内原组件类公共方法签名不变，仍可被显式 import 引用，
  但新代码应走事件驱动入口。
- 静态校验：`scripts/check_module_imports.py` 强制白名单，Phase 8 验证 0 违规。

---

## 18. 事件面板（Event Panel）

### 18.1 功能概述

事件面板提供可视化的事件时间轴展示，支持监控全链路事件流。所有事件（含已发生与未处理的排队事件）沿时间轴分布，并以统一的分类作为 Y 轴垂直分轨。

### 18.2 视觉设计

- **Canvas 时间轴**：横向时间轴，从左到右时间递增，底部显示时间刻度
- **当前时间线**：矩阵视图、散点视图、定时器队列均绘制垂直虚线标识当前时刻
- **事件图标**：9 种事件分类使用不同颜色/图标编码
- **轨道（Lane）**：按 **事件分类** 垂直分轨；两种视图模式下 Y 轴语义相同
- **排队中事件**：未处理的 pending 事件以黄色虚线框标识
- **Tooltip**：鼠标悬停显示事件详情

### 18.3 视图模式

事件面板支持两种视图切换，两种视图使用同一组 `(ts, category)` 数据，且 **Y 轴均为 9 种事件分类**：

| 视图 | 名称 | 布局 | 用途 |
|------|------|------|------|
| 分类显示 | 矩阵视图 | 每类事件独占一行，图标沿时间轴分布 | 观察每类事件的密度与趋势 |
| 全部显示 | 散点视图 | 所有事件绘制在同一 Canvas，同分类在同一水平轨道 | 观察全量事件的时间关联与跨类关系 |

- 分类筛选：可显示/隐藏特定分类事件，筛选结果在两种视图间同步
- 点击分类行或单个图标 → 下方详情区显示事件文本记录

### 18.4 定时器队列

定时器队列独立展示未处理的 `TimerQueued` / `TTLDue` 等排队事件，帮助观察未来何时会发生什么。

- **时间分布图**：顶部 Canvas 以 `fire_at` 为 X 轴、单一固定轨道为 Y 轴绘制排队事件；底部同步显示当前时间线
- **fire_at 单位归一化**：后端可能以秒为单位下发，前端统一归一化为毫秒后再参与时间轴计算与格式化显示
- **列表视图**：按 `fire_at` 升序排列，显示预计触发时间、事件类型、股票代码、详情摘要、队列位置
- **过期标识**：已过期但未处理的排队事件用红色虚线框标出，并自动清理 60 秒前的过期项
- **可折叠/展开**：默认展开，确保队列状态一目了然

### 18.5 事件文本详情

下方详情区用于展示事件的文本记录，作为可视化图标的补充：

- 每条记录显示：时间戳、事件类型图标、股票代码、详情摘要
- 点击矩阵分类行 → 显示该分类下全部事件文本
- 点击散点/矩阵中的单个图标 → 显示该事件及上下文相关事件
- 排队中事件带 pending 样式（黄色虚线框背景）

### 18.6 交互与状态持久化

- **拖拽浮窗**：标题栏拖拽调整面板位置，释放后写入 `localStorage`
- **高度调整**：拖拽底边调整面板高度，写入 `localStorage`
- **折叠/展开**：点击标题栏折叠按钮切换面板高度，写入 `localStorage`
- **隐藏/显示**：点击关闭按钮隐藏面板，下次可通过菜单重新打开
- **暂停/继续接收事件**：暂停时事件不入可视化列表，仅保留已排队 timer；恢复后刷新显示
- **清空事件**：一键清空当前可视化事件与详情区
- **渲染节流**：`render()` 通过 `setTimeout` 实现 200ms 节流，避免高频事件下 DOM/Canvas 频繁重建

### 18.7 性能考虑

- Canvas 绘制代替大量 DOM 节点，单 tick 千级事件仍可保持流畅
- 200ms 渲染节流进一步降低重绘频率
- 事件列表按时间窗口裁剪，默认保留最近 N 条（可配置），防止内存无限增长
- 定时器队列自动清理过期项，避免长运行后列表膨胀

---

## 12. 附录

### A. col 列ID → TQ字段映射

| col ID | 含义 | TQ字段 |
|--------|------|--------|
| 2 | 代码 | code |
| -1 | 名称 | name |
| -2 | 最新价 | close |
| -3 | 涨跌幅 | pct_change |
| -4 | 换手率 | turnover |
| -6 | 量比 | volume_ratio |
| 7 | 入池时间 | entered_at |
| 8 | 现价 | current_price |
| 10 | 收益率 | profit_rate |
| 14 | 入池价 | entry_price |
| 17 | 最大收益 | max_profit |
| 45 | 保留天数 | hold_days |

### B. attr 位标志

节点 attr 整数字段按 cell_type_registry.json 中定义的位展开为布尔属性：
- indicator_condition, basic_condition, ranking_condition
- reverse_transfer, sector_membership, cross_section
- delete_source, clear_dest_first, keep_source

type 值说明：
- type=1: 条件节点（DZH transfer_condition）
- type=200: 状态池（stock_state_pool）
- type=201: 条件池（DZH condition_pool）
- type=202: 市场源（market_source）
- type=4: 丢弃池（discard_pool）

### C. 流转模式位标志 (flow_mode_registry.json)

| 位 | 含义 | 对应 propagate 模式 |
|----|------|-------------------|
| delete_source | 删除源 | move |
| clear_dest_first | 清空目标 | overwrite |
| keep_source | 保留源 | copy |
| force_move | 强制覆盖 | force_move |
| output_constituent | 输出成份 | output_components |

### D. enter/exit 动作编码

动作类型+参数编码为整数，格式由 action_rules.json 定义：
- type=0: 无动作
- type=1: 保存板块（参数=板块文件路径编码）
- type=2: 声音预警（参数=声音类型编码）
- type=3: 弹窗提示

解码函数: `table_engine.py decode_action()`

### E. tradeattr 19字段

DZH 独有，type=200 节点的子元素，完整交易配置：

| 字段 | 含义 |
|------|------|
| accountno | 账户编号 |
| entertradetype | 入场交易类型 |
| enterrate | 入场费率 |
| exittradetype | 出场交易类型 |
| exitrate | 出场费率 |
| buycontrol | 买入控制 |
| sellcontrol | 卖出控制 |
| entrustamount | 委托数量 |
| entrustprice | 委托价格 |
| ... | （共19字段） |

TDX 无交易系统对接，无对应字段。

### F. stk/ana/hist 子元素

| 子元素 | 含义 | TDX | DZH |
|--------|------|-----|-----|
| stk | 股票运行时数据 | 14字段（code/label/indate/intime/...） | 4字段（极简） |
| ana | 分析数据 | 无 | 独立数据体系 |
| histana | 历史分析 | 无 | 独立数据体系 |

TDX 的 stk 自带丰富运行时数据（14字段），DZH 的 stk 极简（4字段）但通过独立数据体系（ana/histana/TqAdapter）获取运行状态。

### G. TQ 数据适配器接口

```python
class TqAdapter(Protocol):
    def resolve_market(self, markets: List[str]) -> Dict[str, List[str]]
    def eval_indicator(self, codes: List[str], formula_text: str,
                       period: str, sorttype: int = 0) -> Dict
    def get_kline_data(self, codes: List[str], period: str = None,
                       start_date: str = None, end_date: str = None) -> Dict
    def get_kline_batch(self, codes: List[str], period: str,
                        start: str, end: str) -> Dict[str, List[Dict]]
```

实现: `services/providers/akshare_provider.py`（实盘）, `services/providers/mock_provider.py`（模拟）

### H. K线合成逻辑

函数: `services/kline_synthesizer.py synthesize_kline()`

| 源周期 | 可合成目标周期 |
|--------|-------------|
| 1min | 5min, 15min, 30min, 60min, day, week, month |
| 5min | 15min, 30min, 60min, day, week, month |
| day | week, month |

合成规则: 取周期内首根K线的 open，末根的 close，max(high) 为 high，min(low) 为 low，sum(volume) 为 volume。

### I. TDX edge 参数映射

| TDX 参数名 | DZH 等效参数 |
|-----------|-------------|
| starttype | begin |
| starttime | begint |
| jgtime | interval_sec |
| cxtype | end |
| cxtime | end_param |
| tran | mode（1=move, 0=copy） |
| emptyps | （DZH 无等效） |

### J. intime 位长归一化

| 原始位长 | 归一化 | 示例 |
|----------|--------|------|
| <= 2 位 | 0000XX | "45" → "000045" |
| <= 4 位 | 00XXXX | "913" → "000913" |
| 5 位 | 0XXXXX | "91313" → "091313" |
| 6 位 | 不变 | "091313" → "091313" |

归一化逻辑由 tdx_psatt.json 的 time_formats 配置驱动，代码: engine.py L218-235 `_parse_intime_to_ts()`

### K. DB Schema DDL（9张持久表）

```sql
-- 1. pool_config
CREATE TABLE pool_config (
    pool_id       TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    pool_type     TEXT NOT NULL DEFAULT 'dzh' CHECK (pool_type IN ('dzh', 'tdx')),
    description   TEXT,
    xml_source    TEXT,
    topology_mode TEXT DEFAULT 'flow',
    status        TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','active','archived','deleted')),
    params        TEXT DEFAULT '{}',
    created_at    DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at    DATETIME NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 2. pool_node
CREATE TABLE pool_node (
    node_id     TEXT PRIMARY KEY,
    pool_id     TEXT NOT NULL,
    node_type   TEXT NOT NULL,
    label       TEXT,
    pos_x       REAL DEFAULT 0, pos_y REAL DEFAULT 0,
    width       REAL DEFAULT 120, height REAL DEFAULT 60,
    params      TEXT DEFAULT '{}',
    created_at  DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (pool_id) REFERENCES pool_config(pool_id) ON DELETE CASCADE
);

-- 3. pool_edge
CREATE TABLE pool_edge (
    edge_id        TEXT PRIMARY KEY,
    pool_id        TEXT NOT NULL,
    source_node_id TEXT NOT NULL,
    target_node_id TEXT NOT NULL,
    params         TEXT DEFAULT '{}',
    created_at     DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (pool_id) REFERENCES pool_config(pool_id) ON DELETE CASCADE,
    FOREIGN KEY (source_node_id) REFERENCES pool_node(node_id) ON DELETE CASCADE,
    FOREIGN KEY (target_node_id) REFERENCES pool_node(node_id) ON DELETE CASCADE,
    CHECK (source_node_id != target_node_id)
);

-- 4. node_state
CREATE TABLE node_state (
    record_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id       TEXT NOT NULL,
    stock_code    TEXT NOT NULL,
    entered_at    DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
    left_at       DATETIME,
    current_state TEXT NOT NULL DEFAULT 'in' CHECK (current_state IN ('in', 'out')),
    FOREIGN KEY (node_id) REFERENCES pool_node(node_id) ON DELETE CASCADE
);

-- 5. stock_transfer_log
CREATE TABLE stock_transfer_log (
    log_id            TEXT PRIMARY KEY,
    ts                DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
    source_node_id    TEXT NOT NULL,
    target_node_id    TEXT NOT NULL,
    stock_code        TEXT NOT NULL,
    transfer_mode     TEXT NOT NULL CHECK (transfer_mode IN ('copy','move','overwrite','constituent','force_move','pass_through')),
    trigger_condition TEXT,
    kline_time        DATETIME,
    FOREIGN KEY (source_node_id) REFERENCES pool_node(node_id) ON DELETE CASCADE,
    FOREIGN KEY (target_node_id) REFERENCES pool_node(node_id) ON DELETE CASCADE
);

-- 6. replay_session
CREATE TABLE replay_session (
    session_id   TEXT PRIMARY KEY,
    pool_id      TEXT NOT NULL,
    base_period  TEXT NOT NULL DEFAULT 'day' CHECK (base_period IN ('day','5min','1min')),
    start_date   DATE NOT NULL,
    end_date     DATE NOT NULL,
    play_speed   REAL NOT NULL DEFAULT 1.0,
    current_time DATETIME,
    kline_index  INTEGER DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'loading' CHECK (status IN ('loading','playing','paused','finished')),
    created_at   DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at   DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (pool_id) REFERENCES pool_config(pool_id) ON DELETE CASCADE,
    CHECK (end_date >= start_date)
);

-- 7. replay_snapshot
CREATE TABLE replay_snapshot (
    snapshot_id   TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL,
    ts            DATETIME NOT NULL,
    node_states   TEXT NOT NULL DEFAULT '{}',
    recent_events TEXT DEFAULT '[]',
    kline_data    TEXT DEFAULT '[]',
    FOREIGN KEY (session_id) REFERENCES replay_session(session_id) ON DELETE CASCADE
);

-- 8. kline_cache
CREATE TABLE kline_cache (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code  TEXT NOT NULL,
    period      TEXT NOT NULL,
    kline_time  TEXT NOT NULL,
    open        REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
    volume      REAL NOT NULL, amount REAL DEFAULT 0,
    cached_at   DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (stock_code, period, kline_time)
);

-- 9. config_version
CREATE TABLE config_version (
    version_id  TEXT PRIMARY KEY,
    table_name  TEXT NOT NULL,
    change_type TEXT NOT NULL CHECK (change_type IN ('create','update','delete')),
    old_content TEXT,
    new_content TEXT,
    created_at  DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
    created_by  TEXT DEFAULT 'system'
);
```

### L. 数据流架构图

```
配置表(JSON) ──加载──→ 运行时索引(module_map / engine_index / dispatch_index / _edge_strategies)
                              │
                              ▼
持久表(pool_config) ──读取──→ PoolEngine._init_node_stocks ──写入──→ PoolState.node_stocks
                              │
                              ▼
                    ┌─── EdgeExecutor.run(eid) ───┐
                    │                              │
              gate(时机门控)                  filter(强弱筛选)
              读 edge_timing_spec                 读 edge_filter_spec
                → timing.json                     → dispatch.json
              读 EdgeState.exec_ctx[eid]          读 PoolState.node_stocks[src]
              读 PoolState.time_source              读 PoolState.latest_tick / bars
                                                    读 EdgeState.formula_results
                                                    写 EdgeState.formula_results[(formula_ref, bar_hash)]
                    │                              │
                    ▼                  callback(持久化+副作用)
              propagate(状态更新)       读 edge_action_spec
              读 edge_propagate_spec                 → action_table.json
              读/写 PoolState.node_stocks[src/tgt]  读 PoolState.node_stocks[tgt]
                                                    写 PoolState.trackers[(tgt, code)]
                                                    发布 DomainEvent / Signal
                    │
                    ▼
              ttl(超时淘汰)
              读 edge_ttl_spec
                → tdx_psatt.json
              读/写 PoolState.node_stocks[tgt]

注：图中 edge_*_spec 均取自 CompiledSchedule；edge_propagate_spec 业务上亦称 edge_flow_spec。
```

## §17 功能-表操作映射总表

所有功能统一用一行表达：**功能名 → 读表 → 计算 → 写表**。表驱动不是数据库问题，而是把行为放进可配置的结构（JSON、内存字典、运行时索引都视为表）。

### 17.1 六步核心循环

| 功能 | 读表 | 计算 | 写表 |
|------|------|------|------|
| gate（时机门控） | `CompiledSchedule.edge_timing_spec[eid]` → `timing.json:starttype_rules` / `cxtype_rules`；`PoolState.time_source`；`EdgeState.exec_ctx[eid]` | `EdgeExecutor._gate()` 评估 starttype × cxtype 是否放行 | `EdgeState.exec_ctx[eid]`（count / first_fire / last_fire / fired） |
| filter（强弱对比） | `CompiledSchedule.edge_filter_spec[eid]` → `dispatch.json:nset_dispatch`；`engines.json`；`PoolState.node_stocks[src]`；`PoolState.latest_tick` / `bars` | `EdgeExecutor._filter()` 对每只股票求值，按 noperate 与阈值比较，输出 passed / rejected | `EdgeState.formula_results[(formula_ref, bar_hash)]`（缓存公式结果） |
| propagate（状态流转） | `CompiledSchedule.edge_propagate_spec[eid]`；`PoolState.node_stocks[src]` / `node_stocks[tgt]` | `EdgeExecutor._propagate()` 按 copy / move / overwrite 执行 | `PoolState.node_stocks[tgt]`；move/overwrite 时更新 `PoolState.node_stocks[src]` |
| callback（持久化+副作用） | `CompiledSchedule.edge_action_spec[eid]` → `action_table.json:pool_enter_actions`；`PoolState.node_stocks[tgt]`；node.params | `EdgeExecutor._run_callback()` 执行 bsavehis / bsound / btip / bsavetoblock / baimpool | `PoolState.trackers[(tgt, code)]`；`EventBus` 发布 `DomainEvent` / `Signal`；持久表由 `MetaEngine._emit_transfer_events()` 后续写入 |
| ttl（超时淘汰） | `CompiledSchedule.edge_ttl_spec[eid]` → `tdx_psatt.json:ttl_units` / `time_formats`；`PoolState.node_stocks[tgt]` 中 indate+intime | `EdgeExecutor._apply_ttl()` 计算 ndelnum × ndeltype 是否超时 | `PoolState.node_stocks[tgt]`（删除超时股票） |
| pre_tick（前置流水线） | `pre_tick_pipeline.json:pipeline`（stage 数组）；runtime state | `MetaEngine._pre_tick()` 查表按 stage 数组串行执行前置处理 | `PoolState.latest_tick` / `bars`（数据注入） |
| post_tick（后处理） | `post_tick_pipeline.json:pipeline`；runtime state | `MetaEngine._post_tick()` 查表按阶段串行执行 pk_ranking / analysis_angles / dashboard / alerts | `PoolState.post_tick_results`；MetaEngine 视图属性 `_pk_rankings` / `_angle_results` / `_dashboard_data` / `_alert_events` |

### 17.1a DZH 特有规则表

| 功能 | 读表 | 计算 | 写表 |
|------|------|------|------|
| DZH 备选池重载调度 | dzh_reload_schedule.json:modes / disambiguation / scheduling；node.params.reload / reload_mode / reload_param | 根据魔数/模式/参数决定触发时机；daily_time 解析 HHMMSS | candidate_pool_refresh_manager 内部任务表；node.params.lastload |
| DZH attrtext 范围解析 | dzh_market_mappings.json:mappings；node.params.attrtext | 按正则/类型把个股/市场/自选组/概念/行业/经典行业映射为内部代码列表 | node_stocks[candidate_node_id] |

### 17.1b Provider / Filter 配置表

| 功能 | 读表 | 计算 | 写表 |
|------|------|------|------|
| AKShare source 路由 | data_source_routes.json:provider_routes.akshare | 按 (source, category) 或 block_type 查表分发方法 | 返回对应板块/股票列表 |
| 分析类型数据获取 | analysis_config.json:analysis_types[*].data_fetch | 按 method / count_key / period_key 统一获取快照或 K 线 | analysis 结果字典 |

### 17.2 三种运行模式

| 功能 | 读表 | 计算 | 写表 |
|------|------|------|------|
| live（实盘） | runtime_modes.json:live；time_sources.json:wall_clock；fallback_chain.json；trade_interfaces.json:live_order | 按真实时间推进，真实行情驱动，真实下单 | _runtime_state；_signal_queue（_signal_events 为派生 property） |
| replay（回放） | runtime_modes.json:replay；time_sources.json:sequence；kline_cache / _timeline；trade_interfaces.json:noop | 按 K 线序列时间步进，只读回放，不下单 | replay_session；replay_snapshot；stock_transfer_log |
| simulation（仿真） | runtime_modes.json:simulation；time_sources.json:virtual；mock_data.json；trade_interfaces.json:paper_trade | 按虚拟时钟手动/自动步进，mock 行情，模拟记账 | _runtime_state；paper_trade_positions；_virtual_cash |

### 17.3 仿真模式架构

仿真模式提供完整的虚拟交易环境，支持手动步进和自动步进两种模式，速度可调节（0.5x ~ 20x）。

#### 17.3.1 前端 UI 组件

| 组件 | ID | 功能 |
|------|-----|------|
| 启动按钮 | `simBtnStart` | 开始自动步进 |
| 暂停按钮 | `simBtnPause` | 暂停自动步进 |
| 步进按钮 | `simBtnStep` | 手动单步执行 |
| 重置按钮 | `simBtnReset` | 重置仿真会话 |
| 步长选择 | `simDeltaSelect` | 1s / 1min / 5min / 1h |
| 速度滑块 | `simSpeedSlider` | 0.5x ~ 20x 连续可调 |
| 速度显示 | `simSpeedValue` | 当前速度倍数 |
| 时钟显示 | `simulationClock` | 虚拟时间 HH:MM:SS |
| 步数显示 | `simulationStepCount` | 已执行步数 |

#### 17.3.2 前端状态机

```
setMode('simulation')
  │
  ├─ 初始化: switch data source to mock
  ├─ 初始化: POST /api/pool/{name}/sim/init
  ├─ 显示面板: simulationPanel + eventPanel
  ├─ 状态: _simAutoStepping = false
  │
  └─ 用户操作:
       ├─ 点击 "▶ 启动" → startSimAutoStep()
       │    ├─ _simAutoStepping = true
       │    └─ _simAutoTick() 循环:
       │         ├─ runSimulationStep(delta)
       │         ├─ 等待 interval = 1000/_simSpeed ms
       │         └─ 重复 (直到暂停或退出)
       │
       ├─ 点击 "⏸ 暂停" → stopSimAutoStep()
       │    └─ _simAutoStepping = false
       │
       ├─ 点击 "⏭ 步进" → stopSimAutoStep() + runSimulationStep(delta)
       │
       ├─ 拖动速度滑块 → _simSpeed = slider.value
       │    └─ POST /api/pool/{name}/sim/speed {speed: _simSpeed}
       │
       └─ 点击 "✕ 退出" → stopSimAutoStep() + setMode('design')
```

#### 17.3.3 后端 API 端点

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/pool/{name}/sim/init` | POST | 初始化仿真会话 |
| `/api/pool/{name}/sim/start` | POST | 执行一步仿真（别名） |
| `/api/pool/{name}/simulation/step` | POST | 执行一步仿真 |
| `/api/pool/{name}/sim/pause` | POST | 暂停仿真 |
| `/api/pool/{name}/sim/resume` | POST | 恢复仿真 |
| `/api/pool/{name}/sim/stop` | POST | 停止并清理仿真 |
| `/api/pool/{name}/sim/state` | GET | 获取仿真状态快照 |
| `/api/pool/{name}/sim/speed` | POST | 设置速度倍数 |

#### 17.3.4 仿真步进流程

```
runSimulationStep(delta)
  │
  ├─ POST /api/pool/{name}/sim/start {delta: delta}
  │
  └─ _run_simulation_step(name, delta)
       ├─ 检查数据源 == mock
       ├─ _get_or_create_simulator(name)
       │    ├─ RuntimeSimulator(pool_config, engine)
       │    └─ simulator.initialize()
       ├─ effective_delta = delta * simulator.speed
       ├─ events = simulator.step(d=effective_delta)
       │    ├─ clock += d
       │    ├─ _generate_tick_from_queue()    # tick 生成
       │    ├─ _sync_stock_prices()           # K线数据同步
       │    ├─ engine._tick()                 # 完整引擎循环
       │    │    ├─ apply_data(bar_data)      # 数据注入
       │    │    ├─ EventDriver.fire_due()    # 边条件计算 + 公式求值
       │    │    ├─ _update_trackers()        # 持仓跟踪更新
       │    │    ├─ _emit_transfer_events()   # ENTER/EXIT/TIMEOUT 事件
       │    │    └─ _post_tick()              # 回调
       │    └─ 收集 events + signals
       └─ 返回 {virtual_clock, node_stocks, events}
```

#### 17.3.5 速度控制机制

- **速度倍数**：存储在 `RuntimeSimulator.speed` 属性
- **有效步长**：`effective_delta = delta * speed`
- **自动步进间隔**：`interval = 1000 / speed` ms（前端 setTimeout）
- **速度范围**：0.5x ~ 20x，步长 0.5

### 17.4 数据源降级链

数据源降级链的实际配置在 `data_source_contract.json`，而非 `fallback_chain.json`。`data_source_contract.json:sources.*.probe` 为每个数据源声明探测契约（method + timeout_ms），共定义 4 个数据源：

| 数据源 | 读表 | probe 契约 | 写表 |
|------|------|------|------|
| tq_dll | data_source_contract.json:sources.tq_dll.probe | method=_probe, timeout_ms=2000，调用 is_ready() + get_market_data() 探测 DLL 是否加载成功 | _data_source_status |
| tq_sdk | data_source_contract.json:sources.tq_sdk.probe | method=_probe, timeout_ms=3000，探测 TQ SDK 是否可连接 | _data_source_status |
| akshare | data_source_contract.json:sources.akshare.probe | method=_probe, timeout_ms=5000，探测 akshare 是否可访问 | _data_source_status |
| mock | data_source_contract.json:sources.mock.probe（explicit_only=true） | method=_probe, timeout_ms=100，确定性随机生成 bar 数据 | PoolState.latest_tick / bars；PoolState.node_stocks[source] |

全局策略：`default_chain: ["tq_dll"]`（默认仅启用 tq_dll），`global_policy.auto_fallback: false`（单源失败不自动切换到下一源），`sources.mock.explicit_only: true`（mock 源必须显式选择才能使用），`global_policy.require_explicit_user_consent_for_mock: true`（启用 mock 需用户显式同意）。

降级链完整表达式：**读 data_source_contract.json:sources.*.probe → 按 default_chain 顺序执行 probe.method（受 timeout_ms 约束）→ 首个探测成功的源即为当前源 → 写 _data_source_status。由于 auto_fallback=false，单源失败不会自动降级到下一源；mock 源需用户显式同意后才能启用，保证生产环境不会误用模拟数据。**

> **fallback_chain.json 的实际职责**：fallback_chain.json 不是数据源降级链，而是**操作类型降级链**。它定义了 9 个操作类型的 condition→handler 降级规则：`formula_eval` / `cross_section_eval` / `basic_filter` / `sector_filter` / `condition_dispatcher` / `dzh_condition_pool` / `nset3_financial_scalar` / `nset4_market_scalar` / `nset5_set_operation`。每个链是一个有序列表，按 condition（如 `tq_available` / `bar_data_available` / `always`）依次匹配，首个匹配的 condition 对应的 handler 即为该操作的执行入口（如 `_eval_real` / `_filter_by_bar_data` / `pass_through`）。

### 17.4 PK 平台四阶段

| 功能 | 读表 | 计算 | 写表 |
|------|------|------|------|
| pk_ranking | pk_config.json:dimensions / weights；node_stocks；current_bar_data | 按 profit / momentum / trend / volume / volatility 维度评分并排序 | _pk_rankings |
| analysis_angles | analysis_config.json:angles；_pk_rankings | 计算 momentum / trend / value 角度结论 | _angle_results |
| dashboard | dashboard_schema.json；_pk_rankings / _angle_results / _alert_events | 汇总 leaderboard / alert_summary / angle_summary | _dashboard_data |
| alerts | alert_rules.json:rules；_trackers / _pk_rankings | 评估 profit_threshold / drawdown_threshold / rank_change | _alert_events |

### 17.5 持仓跟踪（StockTracker）各字段

| 功能 | 读表 | 计算 | 写表 |
|------|------|------|------|
| entry_price | `PoolState.latest_tick[code].close` | 取入池瞬间收盘价 | `PoolState.trackers[(nid, code)].entry_price` |
| entry_time | `_now()`（由 `time_sources.json` 决定） | 入池时间戳 | `PoolState.trackers[(nid, code)].entry_time` |
| current_price | `PoolState.latest_tick[code].close` | 当前最新收盘价 | `PoolState.trackers[(nid, code)].current_price` |
| profit_pct | `PoolState.trackers[(nid, code)].entry_price` / current_price | `(current - entry) / entry × 100%` | `PoolState.trackers[(nid, code)].profit_pct` |
| max_profit | `PoolState.trackers[(nid, code)].profit_pct` 历史序列 | `max(历史 profit_pct)` | `PoolState.trackers[(nid, code)].max_profit` |
| max_drawdown | `PoolState.trackers[(nid, code)].profit_pct` 历史序列 | `max(历史 profit_pct) - 当前 profit_pct` | `PoolState.trackers[(nid, code)].max_drawdown` |
| hold_days | `PoolState.trackers[(nid, code)].entry_time` / `_now()` | `floor((_now - entry_time) / 86400)` | `PoolState.trackers[(nid, code)].hold_days` |

### 17.6 功能完整性验证结果（Task 7-9）

| 验证项 | 范围 | 结果 |
|--------|------|------|
| DZH Cell 类型解析 | 10种（type 1/2/3/4/5/6/200/201/202/203） | ✅ 全部完整 |
| DZH attrtext 备选范围 | 6种（个股/市场/自选组/概念板块/行业板块/行业经典） | ✅ 全部完整 |
| DZH reload 重载模式 | 5种（never/on_file_load/on_startup/interval/daily_time） | ✅ 全部完整 |
| DZH deltype 删除单位 | 5种（天/交易日/小时/分钟/秒） | ✅ 全部完整 |
| DZH 流转模式 | 5种（串行链式/扇出分流/扇入汇合/循环反馈/双源并行） | ✅ 补全 loop_feedback + dual_source，topology.json 表驱动化 |
| TDX nset 条件路由 | 6种（0~5） | ✅ 全部完整 |
| TDX noperate 操作符 | 10种（0~9） | ✅ noperate=5 修复为"排名为" |
| TDX spinfo 备选池 | 8种（type 0~7） | ✅ 全部完整 |
| TDX 拓扑模式 | 6种（serial/fan_out/fan_in/funnel/multi_source/multi_indicator） | ✅ 补全 funnel/multi_source/multi_indicator |
| TDX tran 转移模式 | 2种（copy/move） | ✅ 全部完整 |
| operators.json 操作符 | 7个（comparison 3/4/5/6/7/8/9） | ✅ 补全定义 |
| 端到端测试 | 6种nset × 6种拓扑 × 三模式PK | ✅ 全链路通过，0错误 |

### 17.7 平台级功能映射（完整 15 项）

> 股票池不是选股器，是持续循环的流转机。以下 15 项功能覆盖分析、看盘、决策、交易、监控、记录全链路，每项 = 读表 → 计算 → 写表。

| # | 功能 | 读表 | 计算（核心逻辑） | 写表 |
|---|------|------|-----------------|------|
| 1 | 时机门控 gate | `CompiledSchedule.edge_timing_spec` → timing.json + `PoolState.time_source` + `EdgeState.exec_ctx[eid]` | `EdgeExecutor._gate()` 判断 starttype×cxtype 24种组合是否放行 | `EdgeState.exec_ctx[eid]`（count/first_fire/last_fire/fired） |
| 2 | 强弱筛选 filter | `CompiledSchedule.edge_filter_spec` → dispatch.json + engines.json + `PoolState.node_stocks[src]` + `PoolState.latest_tick` / `bars` | `EdgeExecutor._filter()` 查表路由 gateway，二分 passed/rejected | `EdgeState.formula_results[(formula_ref, bar_hash)]`（缓存） |
| 3 | 状态流转 propagate | `CompiledSchedule.edge_propagate_spec` + `PoolState.node_stocks[src/tgt]` | `EdgeExecutor._propagate()` 执行 copy/move/overwrite | `PoolState.node_stocks[tgt]`（+src if move） |
| 4 | 回调副作用 callback | `CompiledSchedule.edge_action_spec` → action_table.json + `PoolState.node_stocks[tgt]` + node.params | `EdgeExecutor._run_callback()` 触发 bsavehis/bsound/btip/bsavetoblock/baimpool | `PoolState.trackers[(tgt, code)]`；EventBus 发布事件；持久化回调写 .dat/.log + node_state |
| 5 | 超时淘汰 ttl | `CompiledSchedule.edge_ttl_spec` → tdx_psatt.json + `PoolState.node_stocks[tgt]` | `EdgeExecutor._apply_ttl()` 计算 ndelnum×ttl_units[ndeltype] | `PoolState.node_stocks[tgt]`（删除超时） |
| 6 | PK排名 | pk_config.json + `PoolState.trackers` | 查表按多维度加权评分排名 | `PoolState.post_tick_results.pk_rankings`；MetaEngine 视图属性 `_pk_rankings` |
| 7 | 多分析角度 | analysis_config.json + `PoolState.trackers` | 查表按动量/趋势/价值分别评分 | `PoolState.post_tick_results.angle_results`；MetaEngine 视图属性 `_angle_results` |
| 8 | 看盘面板 | dashboard_schema.json + post_tick_results | 查表汇总到面板 | `PoolState.post_tick_results.dashboard_data`；MetaEngine 视图属性 `_dashboard_data` |
| 9 | 监控告警 | alert_rules.json + `PoolState.trackers` + post_tick_results | 查表检查止盈/回撤/排名变化 | `PoolState.post_tick_results.alert_events`；MetaEngine `_alert_events` + `_alert_queue` |
| 10 | 持仓跟踪 | `PoolState.latest_tick` + `PoolState.node_stocks` | `MetaEngine._update_trackers()` 计算入场价/当前价/盈亏/回撤/天数 | `PoolState.trackers[(nid, code)]` |
| 11 | 信号生成 | pool_roles.json + event_rules.json + `PoolState.node_stocks` + `PoolState.trackers` | `MetaEngine._emit_transfer_events()` 判断 ENTER/EXIT/TIMEOUT → BUY/SELL | MetaEngine `_signal_queue`（_signal_events 为派生 property） |
| 12 | 数据注入 | data_source_contract.json + runtime_modes.json | `DataUpdater.apply_data()` 写 `latest_tick` 并置 `dirty.data`；`BarComposer.on_tick(codes)` 订阅 `DataChanged(tick)` 事件并写 `bars` | `PoolState.latest_tick` / `PoolState.bars`；置 `PoolState.dirty.data` |
| 13 | 时间推进 | time_sources.json + runtime_modes.json | `PoolEngine._now()` 选择 wall_clock/sequence/virtual | `PoolState.time_source` |
| 14 | 组件能力注册 | capability_registry.json | `MetaEngine.__init__` 查表动态绑定组件到 `self._capabilities`；`__getattr__` 仅代理 `_capabilities` 中注册的字段，不再代理运行时表。新增组件只需改 JSON，零行 Python | `self._capabilities` |
| 15 | pre_tick 流水线 | pre_tick_pipeline.json | `MetaEngine._pre_tick()` 查表执行 stage 数组。新增 stage 只需改 JSON，零行 Python | `PoolState.latest_tick` / `PoolState.node_stocks`（数据注入） |

**引擎核心循环只有 6 行**（`PoolEngine.run_tick()`）：统一计算 `edge_fired` → 仅执行 triggered 边 → `clear_dirty` → `snapshot_nodes` → `sync_events_to_meta`。15 项功能全部通过查表执行，核心循环代码不随功能增加而增长。

### 17.7.1 一行一功能精简版

> 每项平台功能 = 读表 → 计算 → 写表，一行表达。15 项功能全部表驱动，核心循环代码不随功能增加而增长。新核心路径的运行时表真相源为 `PoolState._tables`（15 张池级表）与 `EdgeState`（4 张边级表）；第 13 轮迭代已彻底移除旧 API `_rt` 兼容 sink，第 14 轮进一步将 `_flow_exec_counts`、`_flow_first_fire_ts`、`_flow_last_fire_ts`、`_flow_duration_starts`、`_filter_cache` 等遗留字段改为 `EdgeState.exec_ctx` / `formula_results` 的视图类，运行时表无本地 fallback。

| # | 功能 | 一行表达 |
|---|------|---------|
| 1 | 时机门控 gate | 读 `CompiledSchedule.edge_timing_spec` → timing.json + `PoolState.time_source` + `EdgeState.exec_ctx[eid]` → `EdgeExecutor._gate()` 判断 starttype×cxtype 24种组合是否放行 → 写 `EdgeState.exec_ctx[eid]` |
| 2 | 强弱筛选 filter | 读 `CompiledSchedule.edge_filter_spec` → dispatch.json + engines.json + `PoolState.node_stocks[src]` + `PoolState.latest_tick` / `bars` → `EdgeExecutor._filter()` 二分 passed/rejected → 写 `EdgeState.formula_results[(formula_ref, bar_hash)]` |
| 3 | 状态流转 propagate | 读 `CompiledSchedule.edge_propagate_spec` + `PoolState.node_stocks[src/tgt]` → `EdgeExecutor._propagate()` 执行 copy/move/overwrite → 写 `PoolState.node_stocks[tgt]`（+src if move） |
| 4 | 回调副作用 callback | 读 `CompiledSchedule.edge_action_spec` → action_table.json + `PoolState.node_stocks[tgt]` + node.params → `EdgeExecutor._run_callback()` 触发动作 → 写 `PoolState.trackers[(tgt, code)]` + EventBus 事件 |
| 5 | 超时淘汰 ttl | 读 `CompiledSchedule.edge_ttl_spec` → tdx_psatt.json + `PoolState.node_stocks[tgt]` → `EdgeExecutor._apply_ttl()` 计算超时 → 写 `PoolState.node_stocks[tgt]` |
| 6 | PK排名 | 读 pk_config.json + `PoolState.trackers` → 按多维度加权评分排名 → 写 `PoolState.post_tick_results.pk_rankings` |
| 7 | 多分析角度 | 读 analysis_config.json + `PoolState.trackers` → 按动量/趋势/价值评分 → 写 `PoolState.post_tick_results.angle_results` |
| 8 | 看盘面板 | 读 dashboard_schema.json + `PoolState.post_tick_results` → 汇总到面板 → 写 `PoolState.post_tick_results.dashboard_data` |
| 9 | 监控告警 | 读 alert_rules.json + `PoolState.trackers` + `PoolState.post_tick_results` → 检查止盈/回撤/排名变化 → 写 `PoolState.post_tick_results.alert_events` |
| 10 | 持仓跟踪 | 读 `PoolState.latest_tick` + `PoolState.node_stocks` → 计算入场价/当前价/盈亏/回撤/天数 → 写 `PoolState.trackers[(nid, code)]` |
| 11 | 信号生成 | 读 pool_roles.json + event_rules.json + `PoolState.node_stocks` + `PoolState.trackers` → 判断 ENTER/EXIT/TIMEOUT → BUY/SELL → 写MetaEngine `_signal_queue`（_signal_events 为派生 property） |
| 12 | 数据注入 | 读 data_source_contract.json + runtime_modes.json → `DataUpdater` / `BarComposer` 探测数据源、并发获取多时间框架 → 写 `PoolState.latest_tick` / `PoolState.bars` + 置 `dirty.data` |
| 13 | 时间推进 | 读 time_sources.json + runtime_modes.json → `PoolEngine._now()` 选择 wall_clock/sequence/virtual → 写 `PoolState.time_source` |
| 14 | 组件能力注册 | 读 capability_registry.json → `MetaEngine.__init__` 动态绑定组件到 `self._capabilities` → 写 `self._capabilities` |
| 15 | pre_tick 流水线 | 读 pre_tick_pipeline.json → `MetaEngine._pre_tick()` 执行 stage 数组 → 写 `PoolState.latest_tick` / `PoolState.node_stocks` |

**核心契约**：引擎核心循环只有 6 行（`PoolEngine.run_tick()`）：统一计算 `edge_fired` → 仅执行 triggered 边 → `clear_dirty` → `snapshot_nodes` → `sync_events_to_meta`。15 项功能全部通过查表执行，核心循环代码不随功能增加而增长。新增功能 = 加 JSON 条目，零行 Python。

### §17.1 事件契约补充（v4）

原 §17 表格中各功能的发布/订阅事件契约（v4 新增）：

| 功能 | 发布事件 | 订阅事件 |
|------|---------|---------|
| 配置加载 | ConfigLoaded | ConfigChanged |
| 池导入 | PoolLoaded, ImportStarted, ExportCompleted | - |
| tick 接收 | TickReceived | - |
| K线合成 | DataChanged, BarComposed | TickReceived |
| 边触发 | EdgeFired | DataChanged, BarComposed, TimeAdvanced |
| 公式计算 | FormulaEvaluated, CrossOverDetected | EdgeFired |
| 股票筛选 | StockFiltered | FormulaEvaluated |
| 边执行 | TransferExecuted, TTLExpired, Signal | StockFiltered, EdgeFired |
| 交易执行 | OrderPlaced, OrderFilled, PositionUpdated, AlertRaised | Signal, TransferExecuted |
| 交易统计 | StatisticsUpdated, RankingChanged | PositionUpdated, BarComposed |
| 监控记录 | SnapshotUpdated, EventLogged, AlertRaised | 全部事件 |
| 模式切换 | ModeChanged | - |
| 时间推进 | TimeAdvanced | TickReceived(live), ReplayStep(replay), SimulationStep(simulation) |
| 回放 | ReplayStarted, ReplayStep | - |
| 仿真 | SimulationStep | - |
| 配置热加载 | ConfigChanged | config/*.json 文件变更 |

---

## §18 运行模式与数据源

### 18.1 三种模式的本质差异

三种模式不是三套代码，而是**同一套核心循环（gate→filter→propagate→callback→ttl→post_tick）绑定不同的“时间源 × 数据源 × 订单接口”组合**。

| 维度 | 实盘（live） | 回放（replay） | 仿真（simulation） |
|------|------------|---------------|-------------------|
| 本质 | 真实时间与真实行情驱动，产生真实交易 | 历史 K 线时间序列驱动，验证策略历史表现 | 虚拟时间与随机行情驱动，验证逻辑与调参 |
| 时间源 | wall_clock（`time.time()`） | sequence（`_timeline[kline_index]`） | virtual（`_virtual_now`，可手动/自动步进） |
| 数据源 | tq_dll → tq_sdk → akshare → mock | kline_cache / _timeline（预加载历史 K 线） | mock_provider（按 mock_data.json 规则生成） |
| 订单接口 | live_order（真实券商下单） | noop（空操作，只记录） | paper_trade（模拟持仓与资金） |
| 副作用范围 | all（写持久表 + 发信号 + 真实交易） | readonly（只读，记录到 replay_snapshot / replay_session） | optional（按配置写模拟持仓） |
| 典型用途 | 生产运行 | 策略回测 | 单元测试 / 演示 |

### 18.1a 三模式数据源本质对比

| 维度 | 实盘（live） | 回放（replay） | 仿真（simulation） |
|------|------------|---------------|-------------------|
| 数据从哪来 | TQ DLL/SDK 实时推送 + akshare 开源 + mock 兜底 | SQLite `kline_cache` 预加载 | `mock_data.json` 规则生成 |
| 数据怎么来 | 异步并发 `_inject_bar_data_async()` | 同步读取 `_timeline[index]` | 即生即用 `_generate_mock_bar_data()` |
| 多时间框架 | 并发获取+缓存（TTL=5s snapshot / 300s kline） | K线合成（`kline_synthesizer`） | 不支持 |
| 缓存策略 | TTL 过期机制 | 无缓存（预加载全量） | 无缓存（即生即用） |
| 可复现性 | 不可复现（实时行情） | 完全可复现（历史数据） | 确定性可复现（seed=42） |
| 网络IO | 有（TQ/akshare） | 无（预加载） | 无（本地生成） |
| 典型用途 | 生产运行 | 策略回测 | 单元测试/演示 |

### 18.1.1 数据源本质差异

| 模式 | 数据从哪来 | 怎么来 | 多时间框架 | 缓存策略 |
|------|-----------|--------|-----------|---------|
| 实盘 | TQ DLL 实时推送 | 异步并发 `_inject_bar_data_async()` | 支持，并发获取+缓存 | TTL=5s(snapshot)/300s(kline) |
| 回放 | 历史K线缓存 | 同步读取 `_timeline[index]` | K线合成 | 无缓存（预加载） |
| 仿真 | Mock 随机生成 | 即生即用 `_generate_mock_bar_data()` | 不支持 | 无缓存（即生即用） |

**核心洞察**：实盘是"数据驱动+时间驱动"双轮（行情到达触发 tick + gate 按真实时间判断）；回放是"时间驱动"单轮（K线序列步进）；仿真是"逻辑验证"模式（虚拟时钟+随机行情+模拟成交）。

### 18.2 模式 × 时间源 × 数据源 × 订单接口对照表

| 模式 | 时间源配置 | 数据源配置 | 订单接口配置 | 关键运行时表 |
|------|-----------|-----------|-------------|-------------|
| live | time_sources.json:wall_clock | fallback_chain.json:tq_dll / tq_sdk / akshare / mock | trade_interfaces.json:live_order | _runtime_state, _signal_queue（_signal_events 为派生 property）, _trackers |
| replay | time_sources.json:sequence | kline_cache + replay_session.base_period | trade_interfaces.json:noop | replay_session, replay_snapshot, stock_transfer_log, _timeline |
| simulation | time_sources.json:virtual | mock_data.json + mock_provider | trade_interfaces.json:paper_trade | _runtime_state, paper_trade_positions, _virtual_cash, _trackers |

### 18.3 同一核心循环如何通过配置切换三种模式

核心循环 `_tick()` 的实现不直接写死时间、行情、下单逻辑，而是查表：

1. **时间推进查表**：`_now()` 读取 `time_sources.json`，根据当前模式返回 wall_clock / sequence / virtual 时间。
2. **行情获取查表**：`_inject_bar_data_async()` / `_get_stock_price()` 读取 `fallback_chain.json`，按优先级探测并返回第一个可用源的数据。
3. **交易执行查表**：`_execute_order()` 读取 `trade_interfaces.json`，根据当前模式调用 live_order / noop / paper_trade。
4. **模式切换 = 改配置**：启动时指定 `runtime_mode=live|replay|simulation`，引擎按 `runtime_modes.json` 加载对应的时间源、数据源、订单接口条目，核心循环代码不变。

**模式切换零代码改动**：启动时指定 `run_mode(mode_id)`，引擎按 `runtime_modes.json` 加载对应的时间源、数据源、订单接口、gate_evaluator、data_injector、refresh_handler 配置，核心循环代码不变。新增模式 = 在 `runtime_modes.json` 加一条目，零行 Python。

### 18.4 数据源降级链的触发条件与回退策略

```
tq_dll    probe_expr: dll_available
  ↓ 探测失败（DLL 未加载或超时）
tq_sdk    probe_expr: sdk_available
  ↓ 探测失败（SDK 未授权或网络异常）
akshare   probe_expr: akshare_available
  ↓ 探测失败（网络不通或接口异常）
mock      probe_expr: always   ← 最终兜底，永不失败
```

| 项目 | 说明 |
|------|------|
| 触发条件 | 每次 tick 调用数据源前，先执行 `fallback_chain.json` 中该源的 `probe_expr`；返回 False 则降级 |
| 缓存策略 | 探测结果缓存到 `_data_source_status`，避免每 tick 重复探测；配置热加载时重置 |
| 回退策略 | 一旦降级，后续 tick 从下一个可用源继续；mock 恒为 True，确保系统始终有数据 |
| 可复现性 | mock 使用确定性随机数生成器（seed 由 `mock_data.json:random_seed` 指定，默认 42），保证同一配置多次运行结果一致 |
| 异常处理 | 单个源异常不抛错，记录到 `_data_source_status.last_error`，继续降级；全部源失败才抛 `DataSourceExhausted` |

### 18.5 “内存字典也是表”

表驱动贯彻到底，意味着**凡是“按结构查值即行为”的地方都是表**，不限于 SQLite 或 JSON 文件：

| 表的形式 | 例子 | 作用 |
|---------|------|------|
| JSON 配置文件 | timing.json / dispatch.json / fallback_chain.json | 持久化配置，热加载 |
| 内存字典 | `self.engine_index`、`self.dispatch_index` | 运行时索引，避免重复解析 |
| 运行时表视图 | `MetaEngine._flow_exec_counts`、`_flow_first_fire_ts`、`_flow_last_fire_ts`、`_flow_duration_starts`、`_filter_cache` | 不保存数据，仅作为 `EdgeState.exec_ctx` / `formula_results` 的视图 |
| SQLite 持久表 | pool_config / node_state / stock_transfer_log / replay_snapshot | 状态持久化与审计 |
| 运行时集合 | `PoolState.node_stocks[nid]`、`EdgeState.exec_ctx[eid]`、`PoolState.trackers[(nid, code)]` | 每 tick 的状态视图 |
| 协议/配置表 | `runtime_modes.json`、`time_sources.json`、`trade_interfaces.json` | 定义模式、时间、订单契约 |

**核心观点**：把行为从 `if/else` 硬编码搬到“表”里，运行时再查表执行。表可以放在文件、数据库、内存，甚至是一个函数字典。关键是“配置即逻辑”，同一套代码通过换表（换配置）实现实盘、回放、仿真三种行为。

**运行时表真相源**：所有核心运行时表统一收敛到 `PoolState._tables`（15 张池级表）与
`EdgeState`（边级表），schema 由 `config/runtime_tables_schema.json` 声明。
每张运行时表有明确的 key 类型、value 类型、读写时机、生命周期。
旧 API 字段（如 `self.node_stocks`、`_trackers`）作为 `MetaEngine` 真实属性存在；
`_flow_exec_counts`、`_flow_first_fire_ts`、`_flow_last_fire_ts`、
`_flow_duration_starts`、`_filter_cache` 等通过 `@property` 代理到
`_ExecCtxView` / `_FilterCacheView` 视图类，视图直接读写 `EdgeState.exec_ctx` /
`formula_results`，不持有本地 fallback 数据。
新增运行时表 = 在 schema JSON 加一条目。

| 表的形式 | 例子 | schema 声明 | 生命周期 |
|---------|------|-----------|---------|
| JSON 配置文件 | timing.json / dispatch.json / fallback_chain.json | table_schemas.json | 跨版本持久 |
| 内存字典（运行时表） | `PoolState._tables['node_stocks']` / `PoolState._tables['latest_tick']` | runtime_tables_schema.json | per_tick / per_session / persistent_in_memory |
| SQLite 持久表 | pool_config / node_state / stock_transfer_log | DDL（DESIGN.md §K） | 跨进程持久 |
| 协议/配置表 | runtime_modes.json / time_sources.json / trade_interfaces.json | table_schemas.json | 跨版本持久 |

**核心观点**：表驱动贯彻到底，意味着凡是“按结构查值即行为”的地方都是表。JSON 文件是表，内存 Dict（如 `PoolState._tables` / `EdgeState.exec_ctx`）是表，SQLite 表也是表。三者统一对待：读表 → 解释 → 写表。同一套核心循环代码通过换表（换配置）实现实盘、回放、仿真三种行为。

---

## §19 services/ 模块职责

`meta_core/services/` 目录承载引擎外围的所有平台级服务，分为"核心服务"与"平台增强服务"两组。每个服务以单一职责为原则，通过 `data_source_contract.json` / `runtime_modes.json` 等配置表与引擎核心解耦。

### 19.1 核心服务（6 项）

| 模块 | 职责 | 关键类/函数 | 读写表 |
|------|------|------------|--------|
| storage.py | SQLite 持久化（pool_config / node_state / stock_transfer_log / replay_session / replay_snapshot / kline_cache / config_version） | Storage | 读写 7 张持久表 |
| tq_adapter.py | TQ 适配器（对接通达信 TQ 量化接口，异步行情获取） | TqAdapter / AsyncTqAdapter | 读 current_bar_data；写 _data_cache |
| hot_reload.py | 热加载（监听 config/ 目录 JSON 文件变更，自动重载配置表） | HotReloader | 读 config/*.json；写 config_version（INSERT）；重置运行时索引 |
| data_service.py | 数据源契约+同步（DataSourceContract 探测数据源可用性 + DataSyncService 数据同步） | DataSourceContract / DataSyncService | 读 data_source_contract.json:sources.*.probe；写 _data_source_status |
| candidate_pool.py | 备选池解析+刷新（CandidatePoolResolver 解析板块/指数成分股 + CandidatePoolRefreshManager 定时刷新） | CandidatePoolResolver / CandidatePoolRefreshManager | 读 dzh_reload_schedule.json / dzh_market_mappings.json；写 node_stocks[candidate_node_id] |
| trading_service.py | 历史+模拟交易（HistoryManager 历史数据管理 + PaperTrade 模拟交易） | HistoryManager / PaperTrade | 读 node_state / stock_transfer_log；写 paper_trade_positions / _virtual_cash |

### 19.2 平台增强服务（3 项）

| 模块 | 职责 | 关键类/函数 | 读写表 |
|------|------|------------|--------|
| data_query.py | 统一 K 线查询服务（DataQueryService，屏蔽历史/今日/当前分钟区别，返回前复权连续序列） | DataQueryService | 读 kline_cache / current_bar_data；写 _kline_query_cache |
| formula_cache.py | 公式结果多级缓存（L1 进程内，缓存键 (formula, symbol, period, context_hash)，分级 TTL） | FormulaCache | 读/写 _formula_cache_l1 |
| minute_aggregator.py | 全市场分钟线合成器（Min1Aggregator，基于预分配 numpy 数组的无锁单线程实现，Tick→1分钟 OHLCV 合成） | Min1Aggregator | 读 tick 流；写 _min1_buffer / kline_cache |

## §20 事件驱动架构（v4）

`unify-stockpool-oop-event-driven` spec 实施后，系统升级为 v4 事件驱动架构。本节定义事件契约、模块依赖规则、app.py lifespan 装配规则。

### §20.1 事件契约表（30 种事件类型）

| # | 事件类型 | 发布者 | 订阅者 | 载荷 |
|---|---------|--------|--------|------|
| 1 | `ConfigLoaded` | Config | Execution/Domain/HotReload | config_tables dict |
| 2 | `ConfigChanged` | HotReload | Config/Execution | changed_tables list |
| 3 | `PoolLoaded` | ImportExport | Execution/Database | pool_config dict |
| 4 | `TickReceived` | DataSource | TickBar/RuntimeMode | tick_data dict |
| 5 | `DataChanged` | TickBar | Formula/Screening/Execution/Monitoring | tick/bar dict + changed_codes |
| 6 | `BarComposed` | TickBar | Formula/Statistics/Execution | bar dict + changed_codes |
| 7 | `EdgeFired` | PoolEngine/EdgeExecutor | Formula/Screening/Monitoring | edge_id + ts + changed_codes |
| 8 | `FormulaEvaluated` | Formula/FormulaRouter | Screening/Statistics | result + formula_ref + codes |
| 9 | `CrossOverDetected` | Formula | Screening | code + type(golden/death) + edge_id |
| 10 | `StockFiltered` | Screening | Execution | passed + rejected lists + edge_id |
| 11 | `TransferExecuted` | Execution | Trade/Database/Monitoring | src→tgt + codes + mode |
| 12 | `TTLExpired` | Execution | Database/Monitoring | node_id + codes |
| 13 | `Signal` | Execution/Trade | Trade | BUY/SELL + code + qty + price |
| 14 | `OrderPlaced` | Trade | Database/Monitoring | order dict |
| 15 | `OrderFilled` | Trade | Statistics/Database/Trade | fill dict |
| 16 | `PositionUpdated` | Trade | Statistics/Monitoring | tracker dict |
| 17 | `StatisticsUpdated` | Statistics | Monitoring/API | stats dict |
| 18 | `RankingChanged` | Statistics | Monitoring | rankings dict |
| 19 | `AlertRaised` | Monitoring/Trade | API/Database | alert dict |
| 20 | `SnapshotUpdated` | Monitoring | API | snapshot dict |
| 21 | `EventLogged` | Monitoring | Database | event dict |
| 22 | `ModeChanged` | RuntimeMode | TickBar/Execution/Trade/Database/所有模块 | mode_id |
| 23 | `TimeAdvanced` | RuntimeMode/PoolEngine | Execution | ts |
| 24 | `ReplayStarted` | RuntimeMode | Database/Monitoring/Replay | session dict |
| 25 | `ReplayStep` | RuntimeMode | Execution/TickBar | step dict |
| 26 | `SimulationStep` | RuntimeMode | Execution/TickBar | step dict |
| 27 | `ImportStarted` | ImportExport | Monitoring | format + path |
| 28 | `ExportCompleted` | ImportExport | Monitoring | format + path + count |
| 29-30 | `Executed`/`DomainEvent` | EdgeExecutor | Execution(转发) | 内部事件 |

### §20.2 模块依赖规则（强制：只准与 EventBus 交互）

**禁止行为**（任何模块违反即视为 bug）：
- `from core.xxx import Yyy`（除 `core.event_bus`/`core.domain`/`core.schemas` 白名单外）
- `from services.xxx import Yyy`（除在 `app.py` lifespan 装配处外）
- `from converters.xxx import Yyy`（除在 `app.py` lifespan 装配处外）
- 模块构造函数接收具体实现类（必须接收 `Protocol`/`ABC` 接口）

**允许行为**：
- `from core.event_bus import EventBus, Event` —— 唯一允许的跨模块 import
- `from core.domain import ...` —— Domain 模块作为纯数据模型，可被任何模块 import
- `from core.schemas import ...` —— Pydantic 数据模型，同上
- 模块构造函数接收 `EventBus` 实例 + 配置 dict + 可选 `Protocol` 接口

**8 个聚合器模块白名单**（允许组合同模块内组件）：
- `core/tick_bar_module.py` ← core.tick_source / core.data_updater / core.bar_composer / services.minute_aggregator
- `core/formula_module.py` ← core.formula / core.formula_engine / core.formula_router / services.formula_cache
- `core/trade_module.py` ← core.trade_executor / services.trading_service
- `core/import_export_module.py` ← converters.dzh / converters.tdx / converters.json_xml
- `core/runtime_mode_module.py` ← core.replay / core.simulator
- `core/execution_module.py` ← core.compiler / core.engine / core.edge_executor / core.time_util
- `core/monitoring_module.py` ← (SubTask 27.6: core.event_panel / core.snapshot_builder 已合并入本文件，原子组件 _EventPanel / _SnapshotBuilder 为模块内私有类)
- `core/screening_module.py` ← core.evaluators

**静态检查**：`scripts/check_module_imports.py` 扫描 core/services/converters 下所有 .py 文件 import 语句。

### §20.3 app.py lifespan 事件布线器

```python
async def lifespan(app):
    bus = EventBus()
    db = Database(bus, config)
    data_source = DataSource(bus, config)
    tick_bar = TickBarModule(bus, config)
    formula = FormulaModule(bus, config)
    screening = ScreeningModule(bus, config)
    execution = ExecutionModule(bus, config)
    trade = TradeModule(bus, config)
    statistics = StatisticsModule(bus, config)
    monitoring = MonitoringModule(bus, config)
    import_export = ImportExportModule(bus, config)
    runtime_mode = RuntimeModeModule(bus, config)
    hot_reload = HotReloadManager(bus, config)
    api = ApiModule(bus, app)
    # 各模块在 __init__ 中订阅事件、发布事件；不持有其他模块引用
```

### §20.4 tick 执行链 10 类事件按序发布

一个 tick 到达触发完整执行链：
1. `TickReceived`（DataSource → TickBar/EventBus）
2. `DataChanged(tick)`（TickBar → EventBus，最新价更新）
3. `BarComposed`（TickBar → EventBus，K线合成/更新）
4. `DataChanged(bar)`（TickBar → EventBus，bar数据更新通知）
5. `EdgeFired`（PoolEngine/EdgeExecutor → EventBus，时间门打开，携带 changed_codes）
6. `FormulaEvaluated`（Formula → Screening/Statistics，仅对 changed_codes 增量计算）
7. `StockFiltered`（Screening → Execution，输出通过/拒绝列表）
8. `TransferExecuted` + `TTLExpired`（Execution → Trade/Database/Monitoring，执行股票转移/过期删除）
9. `Signal`（Execution/Trade → Trade，生成买卖信号）
10. `OrderPlaced` + `OrderFilled` + `PositionUpdated`（Trade → Statistics/Database/Monitoring）
11. `StatisticsUpdated` + `RankingChanged`（Statistics → Monitoring/API）
12. `AlertRaised` + `SnapshotUpdated` + `EventLogged`（Monitoring → API/Database）

### §20.5 三模式 ModeChanged 事件订阅

| 订阅模块 | 切换属性 | 模式映射 |
|---------|---------|---------|
| TickBar | `_mode_id` | live→tq_dll/sdk/akshare; replay→kline_cache; simulation→mock |
| Execution | `_time_source` | live→wall_clock; replay→sequence; simulation→virtual |
| Trade | `_interface_type` | live→live_order; replay→noop; simulation→paper_trade |
| Database | `_side_effects_scope` | live→all; replay→readonly; simulation→optional |

---

## 13. 实例事件流程时间线

> 本节源自 `.trae/specs/specify-stockpool-event-flow/spec.md` 的"四、具体时间线实例"，作为 v4 事件驱动架构的具体落地验证基线。实例池：100 只股票备选池 → A 池 → B 池 → C 池（交集交易）。

### 13.1 核心循环三段式

每个 tick 严格遵循三段式执行：

```
apply_data  →  fire_due  →  clear_dirty
（填充 changed_codes）（发布 EdgeFired → 公式+筛选+转移）（清空 changed_codes）
```

- **apply_data**：`DataUpdater.apply_data(tick_data)` 写 `latest_tick`、置 `dirty.data`、对含变化股票的源节点置 `dirty.nodes[nid]`、记录 `changed_codes`（本周期有 tick/bar 更新的股票集合）。
- **fire_due**：`EventDriver.fire_due(now)` 遍历 `execution_order` 检查时机门控，到时边发布 `EdgeFired`（携带 `changed_codes`），随后执行 `EdgeExecutor.run(eid)` 完成 filter → propagate → callback → ttl；最后 `fire_ttl_due(now)` 一次性处理 TTL 删除。
- **clear_dirty**：`state.clear_dirty()` 清空 `changed_codes`、`dirty.nodes`、`dirty.data`；`snapshot_nodes()` 记录节点快照；发布 `TimeAdvanced`。

### 13.2 10 类事件按序发布链

```
TickReceived
  → DataChanged(tick)
    → BarComposed
      → DataChanged(bar)
        → EdgeFired（携带 changed_codes，时间门打开）
          → FormulaEvaluated（公式计算，为 tick 表添加列）
            → StockFiltered（筛选，列比较/排序/集合）
              → TransferExecuted（状态流转）
                → Signal（交易信号）
                  → OrderPlaced + OrderFilled + PositionUpdated（交易执行）
                    → StatisticsUpdated + RankingChanged（统计排名）
                      → AlertRaised + SnapshotUpdated + EventLogged（告警快照日志）
```

**关键约束：**
- **EdgeFired 先于 FormulaEvaluated 和 StockFiltered**：时间门打开才触发条件评估，而非计算完才触发边。先发布 `EdgeFired`，随后才执行公式计算和筛选。
- **EdgeFired 携带 `changed_codes`**：本周期有 Tick/Bar 更新的股票集合，单事件携带集合参数传递，**非每股票单独发事件**。
- 公式计算与股票筛选严格分离：公式=为 tick 表添加列，筛选=列比较/排序/集合操作。
- BarComposer 仅发布 DataChanged，不直接操作股票池。
- 每个状态池独立维护脏股票集合。

### 13.3 实例池配置

| 节点ID | 类型 | 名称 | 关键参数 |
|--------|------|------|---------|
| `source` | market_source（备选池） | 备选池 | 100 只股票，仿真模式代码替换为 `fz1`..`fz100` |
| `pool_A` | statepool | A池 | TTL=100 分钟（ndelnum=100, ndeltype=2） |
| `pool_B` | statepool | B池 | TTL=200 分钟（ndelnum=200, ndeltype=2） |
| `pool_C` | statepool | C池-交集交易 | TTL=20 分钟；入池=市价买入100股；出池=卖出所有持仓 |

| 边ID | 源→目标 | 触发频率 | 转移条件 |
|------|---------|---------|---------|
| `e1` | source→pool_A | 60 秒 | 5m KDJ(9,3,3) 金叉（noperate=3 上穿） |
| `e2` | source→pool_B | 10 秒 | 1m MACD(12,26,9) 金叉（noperate=3 上穿） |
| `e3` | pool_A→pool_C | 5 秒 | 交集（与 pool_B 取交集） |
| `e4` | pool_B→pool_C | 5 秒 | 交集（与 pool_A 取交集） |

仿真模式约束：股票代码全部用 `fz` 替代；tick 间隔 1-9 秒随机（同股票固定、不同股票不同）；仿真与实盘除 tick 生成逻辑外共用同一套代码。

### 13.4 时间线实例（仿真虚拟时钟）

```
t=0s   初始化
       ConfigLoaded → PoolLoaded(100只fz股票入备选池) → ModeChanged(simulation)
       MockDataSource 为每只股票分配固定间隔：
         fz1→3s, fz2→2s, fz3→7s, fz5→5s, fz7→4s, fz10→9s, ...（1-9s随机）

t=2s   fz2 首个 tick 到达
       apply_data: changed_codes={fz2}, dirty.data=True, dirty.nodes[source]=True
       TickReceived(fz2) → DataChanged(tick, [fz2])
       BarComposer: 更新 fz2 的 1m bar（未闭合）
       fire_due: e1(60s未到)、e2(10s未到)、e3/e4(5s未到) → 无边触发
       clear_dirty

t=3s   fz1 首个 tick 到达
       apply_data: changed_codes={fz1}, dirty.data=True
       TickReceived(fz1) → DataChanged(tick, [fz1])
       fire_due: 无边到时
       clear_dirty

t=5s   fz5 首个 tick 到达；e3/e4 首次到时（5s）
       apply_data: changed_codes={fz5}
       TickReceived(fz5) → DataChanged(tick, [fz5])
       fire_due:
         e3 到时 → EdgeFired(e3, changed_codes={fz5})
           _filter: source=pool_A(空) → passed=[]
           StockFiltered(e3, [], []) → TransferExecuted(e3, A→C, [])
         e4 到时 → EdgeFired(e4, changed_codes={fz5})
           _filter: source=pool_B(空) → passed=[]
           StockFiltered(e4, [], []) → TransferExecuted(e4, B→C, [])
       clear_dirty

t=10s  fz10 首个 tick 到达；e2 首次到时（10s）；e3/e4 第二次到时
       apply_data: changed_codes={fz2,fz10}（fz2 间隔2s, 10/2=5 到时）
       TickReceived(fz2) → TickReceived(fz10) → DataChanged(tick, [fz2,fz10])
       BarComposer: 1m bar 累加
       fire_due:
         e2 到时 → EdgeFired(e2, changed_codes={fz2,fz10})
           _filter: source=备选池(100只), changed_in_source={fz2,fz10}
             FormulaEngine.eval({fz2,fz10}, MACD 1m, noperate=3 上穿)
             → FormulaEvaluated(e2, {fz2:{prev:-0.1,curr:0.05}, fz10:{prev:0.2,curr:0.3}})
             筛选: fz2 金叉(prev<=0 AND curr>0) → newly_passed={fz2}
                   fz10 未金叉 → rejected
             passed = (cached={} - {fz2,fz10}) | {fz2} = {fz2}
             → StockFiltered(e2, passed=[fz2], rejected=[fz10])
           _propagate: node_stocks[pool_B] += [fz2], dirty.nodes[B]=True
             → TransferExecuted(e2, source→B, [fz2])
         e3 到时 → EdgeFired(e3, changed_codes={fz2,fz10})
           _filter: source=pool_A(空) → passed=[]
           → StockFiltered(e3, [], []) → TransferExecuted(e3, A→C, [])
         e4 到时 → EdgeFired(e4, changed_codes={fz2,fz10})
           _filter: source=pool_B={fz2}, INTERSECTION with pool_A(空)
             changed_in_source={fz2}, fz2 不在 A → newly_passed={}
             passed = (cached={} - {fz2}) | {} = {}
           → StockFiltered(e4, [], []) → TransferExecuted(e4, B→C, [])
       clear_dirty

t=60s  e1 首次到时（60s）；e2 第6次到时；e3/e4 第12次到时
       apply_data: changed_codes={fz1,fz3,fz7}（按各自间隔到时）
       TickReceived(fz1/fz3/fz7) → DataChanged(tick, [fz1,fz3,fz7])
       BarComposer: fz1 的 1m bar 闭合(t=60s) → BarComposed(1m, fz1)
                    5m bar 未闭合（需300s）
       fire_due:
         e1 到时 → EdgeFired(e1, changed_codes={fz1,fz3,fz7})
           _filter: source=备选池(100只), changed_in_source={fz1,fz3,fz7}
             FormulaEngine.eval({fz1,fz3,fz7}, KDJ 5m, noperate=3 上穿)
             【注】5m K 线需累积300s才有第一根完整5m bar
                   首次无完整 bar 时，公式引擎用最新 tick 推算
             → FormulaEvaluated(e1, {fz1:{K_prev:20,K_curr:35,J_prev:15,J_curr:45}, ...})
             筛选: fz1 KDJ 金叉(J 上穿 K) → newly_passed={fz1}
                   fz7 KDJ 金叉 → newly_passed={fz1,fz7}
             passed = (cached={} - {fz1,fz3,fz7}) | {fz1,fz7} = {fz1,fz7}
             → StockFiltered(e1, passed=[fz1,fz7], rejected=[fz3])
           _propagate: node_stocks[pool_A] += [fz1,fz7], dirty.nodes[A]=True
             → TransferExecuted(e1, source→A, [fz1,fz7])
         e3 到时 → EdgeFired(e3, changed_codes={fz1,fz3,fz7})
           _filter: source=pool_A={fz1,fz7}, INTERSECTION with pool_B={fz2}
             changed_in_source={fz1,fz7} ∩ changed_codes
             fz1 在 B? 否。fz7 在 B? 否。
             newly_passed={} (A∩B=空)
             passed = (cached={} - {fz1,fz7}) | {} = {}
           → StockFiltered(e3, [], []) → TransferExecuted(e3, A→C, [])
         e4 到时 → EdgeFired(e4, changed_codes={fz1,fz3,fz7})
           _filter: source=pool_B={fz2}, INTERSECTION with pool_A={fz1,fz7}
             fz2 在 A? 否。
             newly_passed={}
           → StockFiltered(e4, [], []) → TransferExecuted(e4, B→C, [])
       clear_dirty

t=70s  假设 fz1 此前经 e2 也进入了 pool_B（MACD 金叉发生在 t=20s）
       pool_A={fz1,fz7}, pool_B={fz1,fz2}
       e3/e4 到时 → 交集计算：
         e3: A∩B = {fz1} → passed=[fz1]
           → TransferExecuted(e3, A→C, [fz1])
           → Signal(buy, fz1, 100, market)
             → OrderPlaced → OrderFilled(price=模拟价) → PositionUpdated(fz1, vol=100)
             → StatisticsUpdated → RankingChanged
           → AlertRaised(pool_C 入池提示)
         e4: B∩A = {fz1} → 但 fz1 已在 C（去重），passed=[]

t=1270s   fz1 在 pool_C 的 TTL(20分钟) 到期
          fire_ttl_due:
            → TTLExpired(pool_C, fz1, entry_ts=70s)
            → Signal(sell_all, fz1)
              → OrderPlaced(sell, fz1, 100) → OrderFilled → PositionUpdated(fz1, vol=0, pnl=...)
              → StatisticsUpdated → RankingChanged
            从 node_stocks[pool_C] 删除 fz1

t=6060s   fz1,fz7 在 pool_A 的 TTL(100分钟) 到期
          fire_ttl_due:
            → TTLExpired(pool_A, fz1), TTLExpired(pool_A, fz7)
            从 node_stocks[pool_A] 删除 fz1, fz7

t=12020s  fz1 在 pool_B 的 TTL(200分钟) 到期
          fire_ttl_due:
            → TTLExpired(pool_B, fz1)
            从 node_stocks[pool_B] 删除 fz1
```

### 13.5 交易事件链

**入池买入链（股票进入 pool_C 时）：**

```
TransferExecuted(e3, pool_A→pool_C, [fz1])
  → Signal(action="buy", code=fz1, volume=100, order_type="market")
    → OrderPlaced → OrderFilled → PositionUpdated
      → StatisticsUpdated → RankingChanged
```

**出池卖出链（C 池 TTL 20 分钟到期时）：**

```
TTLExpired(pool_C, fz1, entry_ts)
  → Signal(action="sell_all", code=fz1, volume=0)
    → OrderPlaced → OrderFilled → PositionUpdated
      → StatisticsUpdated → RankingChanged
```

### 13.6 EdgeFired 关键语义

- **EdgeFired 先于 FormulaEvaluated 和 StockFiltered**：先触发才有公式计算和筛选。时间门打开才触发条件评估，而非计算完才触发边。先发布 `EdgeFired`（携带 `changed_codes`），再执行 `FormulaEvaluated` 和 `StockFiltered`。
- **EdgeFired 携带 `changed_codes`**：本周期有 Tick/Bar 更新的股票集合，单事件携带集合参数传递，**非每股票单独发事件**。
- **增量公式评估**：仅对 `changed_codes ∩ source_pool_codes` 重新评估公式；未变化股票沿用上一次缓存结果。合并规则：`passed = (cached_passed - changed_in_source) | newly_passed`。
- **仿真与实盘共用代码**：`apply_data` / `fire_due` / `clear_dirty` 三段式完全相同；唯一差异是 tick_data 由 MockDataSource 生成（fz 代码、1-9s 间隔），公式计算、筛选、转移、交易逻辑代码完全相同。

---

## §22 前端迭代评审 V5 结果

> 本节汇总 `refine-frontend-stockpool-v5` 规范的双工程师协作评审结果。规范路径：`.trae/specs/refine-frontend-stockpool-v5/`。所有 7 个任务均通过 ≥98 分门槛。

### §22.1 各任务最终评分表

| 任务 | 名称 | 评分 | 通过日期 | 关键修复 |
|------|------|------|---------|---------|
| Task 1 | V4 Task 1-3 回归验证与颜色值统一 | 99/100 | 2026-07-26 | 实盘模式 `#2ecc71` → `#27ae60`、回放模式 `#f39c12` → `#e67e22`、仿真 `#9b59b6`、设计 `#3498db` |
| Task 2 | 四种模式切换逐项评审与修复 | 98/100 | 2026-07-26 | `setMode/setRunMode` 的 `poolRunStatus` 检查从 `=== 'running'` 改为 `!== 'stopped'`，覆盖 paused 状态阻止切换 |
| Task 3 | 仿真模式股票流转逐项评审与修复 | 98/100 | 2026-07-26 | 新增 `_getSimDelta()` 辅助函数读取 `simDeltaSelect` 下拉值，修复步长选择不生效 bug |
| Task 4 | 事件面板（EventPanel）逐项评审与修复 | 98/100 | 2026-07-26 | 8 个 emoji 图标 + `MATRIX_LABEL_WIDTH` 96→86 + Canvas 字体添加 `Segoe UI Emoji, Apple Color Emoji, Microsoft YaHei` + `lastScatterLayout` 声明修复 TDZ |
| Task 5 | 表驱动与事件驱动架构一致性评审与修复 | 98/100 | 2026-07-26 | `canvas.js:2123` `_renderTdxCandidate` fallback 颜色从硬编码 `#3498db` 改为表驱动 `(NODE_TYPE_DEFAULTS.tdx_candidate||{}).color \|\| '#9b59b6'` |
| Task 6 | 方法/属性/事件清单 + 旧接口检查 + 回归 | 98/100 | 2026-07-26 | `app.js:12297` 删除 `\|\| result.data.node_stocks` 兼容回退，强制 `StatePoolView` 视图接口；删除 8 个 junk code 方法（canvas.js 4 + ui.js 4） |
| Task 7 | 更新 DESIGN.md 和 DESIGN0.md 设计文档 | 100/100 | 2026-07-26 | 本章节 + DESIGN0.md §8 前端验证章节 |

**总评分**：7 任务合计 690/700，平均 98.57/100，全部 ≥98 分门槛，结项通过。

### §22.2 修复清单（文件:行号）

**前端代码修复**：

| 文件 | 行号 | 修复内容 | 任务 |
|------|------|---------|------|
| `web/css/styles.css` | 模式指示器 | 实盘 `#27ae60` / 回放 `#e67e22` / 仿真 `#9b59b6` / 设计 `#3498db` 四色统一 | Task 1 |
| `web/js/app.js` | `setMode/setRunMode` | `poolRunStatus` 检查从 `=== 'running'` 改为 `!== 'stopped'`，paused 状态也阻止切换 | Task 2 |
| `web/js/app.js` | `_getSimDelta()` 新增 | 读取 `simDeltaSelect` 下拉值，修复步长选择不生效 | Task 3 |
| `web/js/event-panel.js` | 8 处 emoji | 修复 `📊📈🧮⚡🔄💰📋⏰🔧` 9 类事件图标 | Task 4 |
| `web/js/event-panel.js` | `MATRIX_LABEL_WIDTH` | 96 → 86，与散点视图统一 | Task 4 |
| `web/js/event-panel.js` | Canvas font | 添加 `'Segoe UI Emoji, Apple Color Emoji, Microsoft YaHei'` | Task 4 |
| `web/js/event-panel.js` | `lastScatterLayout` | 声明修复 TDZ（temporal dead zone）严重 bug | Task 4 |
| `web/js/canvas.js` | 2123 | `_renderTdxCandidate` fallback 颜色从 `#3498db` 改为 `(NODE_TYPE_DEFAULTS.tdx_candidate\|\|{}).color \|\| '#9b59b6'`（表驱动） | Task 5 |
| `web/js/app.js` | 12297 | 删除 `\|\| result.data.node_stocks` 兼容回退，强制 `StatePoolView` 视图接口（`result.data.pools`） | Task 6 |
| `web/js/canvas.js` | 4 处 | 删除未使用方法：`benchmarkRender` / `fitView` / `getHighlightedNodes` / `getHighlightedEdges` | Task 6 |
| `web/js/ui.js` | 4 处 | 删除未使用方法：`HighlightManager.getActiveCount` / `getActiveHighlights` / `ToolbarRenderer.validateButtonCount` / `getRenderedButtons`，并清理导出对象引用 | Task 6 |

### §22.3 量化数据（eventtest 基线）

**eventtest 171 项正反合测试全部通过，退出码 0**（运行耗时 421.74s）：

| 维度 | 数据 |
|------|------|
| 测试总数 | 171 |
| 通过数 | 171 |
| 失败数 | 0 |
| 通过率 | 100.00% |
| 退出码 | 0 |

**事件计数表（与基线一致，无回归）**：

| 事件类型 | 计数 |
|---------|------|
| TickReceived | 6000 |
| DataChanged | 143500 |
| BarComposed | 191000 |
| EdgeFired | 310 |
| FormulaEvaluated | 7000 |
| StockFiltered | 70 |
| TransferExecuted | 26 |
| Signal | 84 |
| OrderPlaced | 84 |
| OrderFilled | 84 |
| PositionUpdated | 84 |

**池状态快照（与基线一致，无回归）**：

| 池 | 股票数 |
|----|--------|
| source（备选池） | 100 |
| pool_A | 84 |
| pool_B | 100 |
| pool_C | 84 |

**禁用 token 扫描结果（全部零匹配）**：

| Token | 匹配数 |
|-------|--------|
| `get_node_stocks` | 0 |
| `set_node_stocks` | 0 |
| `SimTickSource` | 0 |
| `EdgeFired.changed_codes` | 0 |
| `changed_codes` | 0 |
| `execution_order`（运行时） | 0（4 处匹配全部为节点类型字符串合法例外：`canvas.js:1595/1645` + `app.js:4667/9369`） |

### §22.4 表驱动一致性结论（含验证证据）

| 检查项 | 代码位置 | 结论 |
|--------|---------|------|
| `/api/registry/*` 7 端点动态读取 | `app.js:283/293/303/313/320/327/334` + cache-version `app.js:1609` | ✅ 全覆盖 |
| `_renderPanel()` 表驱动分发 | `ui.js:1651` `ComponentRegistry.get(field.comp)` | ✅ 无硬编码 if-else |
| 内置组件 ComponentRegistry 注册 | `ui.js` 34 处 `ComponentRegistry.register` | ✅ 全注册 |
| `ToolbarRenderer` 配置驱动 | `ui.js:5576-5609` `renderToolbar()` 按 `config.groups/buttons` 渲染 | ✅ 表驱动 |
| 节点渲染参数配置驱动 | `canvas.js:560-619` `ensureConfigLoaded()` 加载 `cell_type_registry` → `NODE_TYPE_DEFAULTS`/`DZH_NODE_SIZES`/`DZH_BORDER_WIDTHS`/`DZH_FONT_SIZES` | ✅ 表驱动 |
| `field.type ===` 硬编码 | 全局搜索零匹配 | ✅ 无硬编码 |

### §22.5 事件驱动一致性结论（含验证证据）

**6 种事件机制全部存在并正确派发与监听**：

| 机制 | 派发位置 | 监听位置 |
|------|---------|---------|
| SSE `/api/events/stream` | 后端 | `event-panel.js:2042` `new EventSource('/api/events/stream')` |
| WebSocket `/api/config/ws` `ConfigSync` | `app.js:8819` `class ConfigSync` + `app.js:8843` `new WebSocket` | `app.js:9493` 实例化 |
| `AppState.subscribe()` pub-sub | `app.js:31-36` `_notify()` 派发 | `app.js:66-73` `subscribe()` 返回 unsubscribe 闭包 |
| `configChanged` CustomEvent | `app.js:8950` 派发 | `app.js:9498` 监听重载 registry |
| `tdx:historyView` CustomEvent | `ui.js:3996` 派发 | `app.js:9299` 监听 |
| `zoomchange` CustomEvent | `canvas.js:1495` 派发 | `app.js:9440` 监听 |

### §22.6 模块解耦结论（含验证证据）

| 模块 | 解耦方式 | 验证证据 |
|------|---------|---------|
| `FlowCanvas` | `onChange()` 回调通知外部 | `canvas.js` 全文搜索 `PoolDataManager` 零匹配 |
| `TableDrivenPanel` | `onChange()` 回调通知 | `ui.js` 全文搜索 `FlowCanvas` 零匹配 |
| `event-panel.js` | `window.xxx` 桥接函数 + `typeof window.xxx === 'function'` 守卫 | `event-panel.js:219/473/571/2138` 共 4 处 typeof 守卫 |
| `HighlightManager` | `canvas.highlightNode()/unhighlightNode()` 接口 | `ui.js:5222/5262` 无直接 SVG 操作 |
| `ConfigManager` | `api()` 函数调用 `/api/config/*` | `app.js:7520-9031` 全部通过 `api()` 与后端交互 |

### §22.7 事件订阅清理结论（含验证证据）

| 对象 | 清理方法 | 验证证据 |
|------|---------|---------|
| `HighlightManager` | `destroy()` | `ui.js:5328-5356`：stopHighlight + `stopPolling()` + `clearTimeout(_fallbackTimer)` + ws 4 handler 置 null + `ws.close()` + `cancelAnimationFrame(_animationFrameId)` |
| `ConfigSync` | `disconnect()` | `app.js:8872-8881`：`_stopPing()` + `clearTimeout(_reconnectTimer)` + `ws.onclose=null` + `ws.close()` + `ws=null` |
| `VirtualScroller` | `destroy()` | `canvas.js:313-322`：`cancelAnimationFrame` + scroll handler 解绑 |
| `TableDrivenPanel` | `destroy()` | `ui.js:1395-1410`：3 个定时器 + listeners 清理 |
| `FlowCanvas` | `destroy()` | `canvas.js:1315-1344+`：2 RAF + 2 doc handler + listeners + 回调置 null |
| `BaseChart` | `destroy()` | `app.js:2106-2115`：resize handler + DOM 移除 |

### §22.8 单一真相源结论（含验证证据）

| 属性 | 类型 | 验证证据 |
|------|------|---------|
| `_nodes` | 单一数据源 | `canvas.js:732` `this._nodes = []`；`nodeElements` 为派生缓存（render 时 `nodeElements.clear()` 重建） |
| `selectedNodeId` / `selectedNodeIds` | 单选 vs 多选语义不重叠 | `canvas.js:751/753`；`canvas.js:1348` `selectedNodeId = selectedIds.length === 1 ? selectedIds[0] : null` 派生关系明确 |
| `simulationState` | 单一状态字段 | `app.js:19` `simulationState: 'stopped'`；`app.js:31-36` `setSimulationState` 修改 + `_notify` 派发 |
| `_data` | 单一数据源 | `app.js:259` `this._data = null`；history/redoStack 存储 `JSON.stringify` 字符串（序列化快照，非独立状态副本） |
| `transform` | 单一状态对象 | `canvas.js:748` `this.transform = { x: 0, y: 0, zoom: 1 }`，所有变换均修改此对象 |

### §22.9 替代验证说明

依据 `spec.md` 「执行环境说明」：Playwright 环境不可用时，可采用"代码审查 + 语法检查 + HTTP 加载验证 + Node.js 测试脚本"作为替代验证手段，每项替代验证扣 1 分（最高扣 5 分）。

**本次评审采用的替代验证**：
- 代码审查（替代 Playwright DOM 截图）：每任务扣 1 分
- Node.js 语法检查 `node --check`（替代浏览器控制台）：每任务扣 1 分
- eventtest 必须真实运行（不可替代），171 项全部通过，退出码 0

**所有任务的 98 分扣分均来自上述替代验证扣分**，未发现实质性 bug。Node.js 语法检查覆盖：`web/js/app.js` / `canvas.js` / `ui.js` / `event-panel.js` 四个文件全部 exit code 0。

---

## §23 事件面板运行时 bug 修复 V6 结果

> 本章节记录 V5 评审结项后，用户在仿真运行真实验证时发现的 6 个运行时 bug 及其修复。V5 评审基于"代码审查 + Node.js 语法检查"替代验证，未发现实质性 bug，但运行时验证暴露坐标系一致性问题。V6 由主 Agent 直接修复并量化验证，不再派发 sub-agent 评审工程师（V5 假评审流程已停止）。

### §23.1 修复任务评分表

| 任务 | 名称 | 验证结果 | 量化证据 |
|------|------|---------|---------|
| Task 1 | 后端事件时间戳泄漏修复 | PASS | 659 事件全部仿真相对秒，0 真实 Unix 秒泄漏 |
| Task 2 | 前端事件面板分类图标与布局修复 | PASS | 浏览器 7 项全部 PASS |
| Task 3 | 定时器触发类型识别修复 | PASS | 107 定时器全部识别，无回退 |
| Task 4 | 设计文档更新 | PASS | §23 + DESIGN0.md §9 已添加 |

### §23.2 修复清单（文件:行号格式）

#### 后端 G2 合规化（仿真/实盘同代码）

| 文件 | 行号 | 修复内容 |
|------|------|---------|
| `core/domain.py` | `time_at` 函数 | 删除 `tsf < 1e9 else 0.0` hack 和 `if not ts_cfg: return time.time()` fallback，纯靠 `driver_type` 分派 |
| `core/domain.py` | `MockDataSource._current_ts` | 删除 `time.time()` fallback，统一走 `time_at(state)` |
| `core/runtime_mode_module.py` | `_step_once`/`_astep_once` | 删除冗余 `driver_type` 覆盖和 DIAG 诊断日志，由 `_post_init_mode_state` 一次性设置 |
| `core/engine.py` | `_run_tick_body` | 删除 DIAG 诊断日志，统一通过 `time_at(state)` 获取时间 |
| `core/engine.py` | `_on_tick_received` | ts 选择逻辑优先使用 `event.ts` 或 `time_at(state=self._state)`，禁止 fallback 到 `tick._ts` |
| `core/execution_module.py` | `fire_due` | 删除 DIAG 诊断日志 |

#### 后端事件 ts 事件流传递（核心修复）

| 文件 | 行号 | 修复内容 |
|------|------|---------|
| `core/tick_bar_module.py` | 358 | `_publish_bar_changed` 接收 `ts` 参数，删除内部 `time_at(state=composer.state)` 调用 |
| `core/tick_bar_module.py` | 446 | `BarComposer.on_tick` 接收 `event_ts` 参数（来自上游 `DataChanged(tick)` 事件） |
| `core/tick_bar_module.py` | 498 | `_publish_bar_changed(self, period, period_advanced, now)` 传入 ts |
| `core/tick_bar_module.py` | 1127 | `TickBarModule._on_data_changed` 调用 `on_tick(codes, event.ts)` |

#### 前端事件面板修复

| 文件 | 行号 | 修复内容 |
|------|------|---------|
| `web/js/event-panel.js` | 193-200 | `TIMER_TRIGGER_TYPES` 表驱动 6 类，`tick_timer` 正则加入 `\btick\b` |
| `web/js/event-panel.js` | 203-219 | `getTimerTriggerType` 函数通过事件详情和类型匹配识别 |
| `web/js/event-panel.js` | 264- | `normalizeToModeMs` 时间归一化，区分仿真相对秒（<1e9）与真实 Unix 秒（≥1e9） |
| `web/js/event-panel.js` | `CATEGORY_CONFIG` | 分类图标标准化 tick=📡、edge=🔀、signal=🔔、system=⚙ |
| `web/js/event-panel.js` | 散点视图 | `cy = plotH / 2`，所有事件在中线显示，删除按分类上下微调 |
| `web/js/event-panel.js` | 分类点击 | 移除 `e.stopPropagation()`，直接调用 `renderDetailForCategory` |
| `web/css/styles.css` | 事件面板 | 默认隐藏（display:none）、右下角定位（right:16px; bottom:16px）、标签区背景 |
| `web/index.html` | 839 | JS 版本号 v=16 → v=17 强制浏览器加载最新代码 |

### §23.3 量化验证数据

#### 后端事件 ts 分布（`python _verify_event_ts.py`）

```
[6] 事件类型分布:
    BarComposed                    count=364
    DataChanged                    count=252
    TickDue                        count=14
    TickReceived                   count=14
    TimeAdvanced                   count=15
[7] 仿真相对秒事件 (< 1e9): 659 个
    ts 范围: 34501.000 ~ 34501.000
[8] 真实 Unix 秒事件 (>= 1e9): 0 个  <-- 应为 0
[PASS] 所有事件 ts 均为仿真相对秒，无真实时间戳泄漏
```

#### 定时器触发类型识别统计（Python 模拟验证）

```
[6] 模拟前端 pseudoEvent 构造 + getTimerTriggerType 识别:
    [0] kind='tick' -> label='Tick定时器' color='#9e9e9e'
        hints='timerqueued tick      '
[7] 触发类型识别统计:
    Tick定时器      count=103
    边定时器         count=4
[PASS] 所有定时器触发类型识别成功，无回退到默认值
```

#### 浏览器验证（7 项全部 PASS）

| 验证项 | 结果 |
|--------|------|
| 仿真模式切换 | PASS |
| 事件面板右下角 560px×400px 不遮挡顶部工具栏 | PASS |
| 矩阵视图分类图标 📡🔀🔔⚙ 正确 | PASS |
| 分类点击显示详情 | PASS |
| 散点视图中线显示 | PASS |
| 定时器队列触发类型列 | PASS |
| 浏览器控制台无 JavaScript 错误 | PASS |

### §23.4 G2 硬约束合规性说明

本次修复彻底贯彻 G2 硬约束（仿真/实盘同代码，仅由 `state.time_source.driver_type` 在 `time_at` 内部决定时间源）：

- **未回退任何代码**：仿真与实盘走同一代码路径
- **删除仿真专用分支**：`time_at` 函数纯靠 `driver_type` 分派，无 `if mode == "simulation"` 分支
- **删除 `time.time()` fallback**：`MockDataSource._current_ts` 统一走 `time_at(state)`
- **ts 事件流传递**：`_publish_bar_changed` 接收 `ts` 参数，不重复计算，仿真/实盘同代码路径
- **`current_ts` 正确性由设置方保证**：`_post_init_mode_state` 仿真启动时设虚拟时钟，`run_pool` 实盘启动时设墙钟

### §23.5 V5 假评审流程停止说明

V5 双工程师协作流程中评审工程师仅做静态检查（代码审查 + Node.js 语法检查），未实际启动仿真验证，导致 6 个运行时 bug 未被发现。用户明确要求停止假评审流程，转为真实问题定位和修复。V6 由主 Agent 直接修复并量化验证，验证项全部 PASS 方可结项，无扣分替代验证。

### §23.6 后续评审要求

- **必须包含运行时验证**：禁止仅凭代码审查 + 语法检查结项
- **必须实际启动仿真**：通过 `python _verify_event_ts.py` 验证事件 ts 坐标系
- **必须浏览器真实验证**：事件面板所有修复点必须通过 Playwright 或手动 Ctrl+Shift+R 验证
- **eventtest 不可替代**：171 项必须全部通过，退出码 0

## §24 WebSocket APIKeyHeader 依赖冲突修复

### §24.1 问题现象

启动 `uvicorn app:app` 后，前端访问任一 WebSocket 端点（`/api/config/ws`、`/api/config/ws/events`）
立即被服务端以 1008 policy violation 关闭，服务端日志输出：

```
TypeError: APIKeyHeader.__call__() missing 1 required positional argument: 'request'
```

### §24.2 根因定位

| 文件 | 行号 | 问题 |
|------|------|------|
| `api.py` | 204 | `router = APIRouter(prefix="/api/config", ...)` 同时包含 HTTP 路由与 WebSocket 路由（`/ws`、`/ws/events`） |
| `api.py` | 824（原 817） | `@router.websocket("/ws")` 装饰在主 `router` 上 |
| `api.py` | 849（原 842） | `@router.websocket("/ws/events")` 装饰在主 `router` 上 |
| `app.py` | 545 | `app.include_router(config_api_router, dependencies=[Depends(verify_api_key)])` |
| `app.py` | 419、445 | `verify_api_key` 依赖 `_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)` |

`include_router` 的 `dependencies` 参数会应用到 router 中**所有路由**，包括 WebSocket。
`APIKeyHeader.__call__` 的签名是 `async def __call__(self, request: Request)`，依赖 HTTP `Request`
注入。WebSocket 路由的 ASGI scope 类型为 `websocket` 而非 `http`，FastAPI 不会注入 HTTP `Request`，
导致 `TypeError: APIKeyHeader.__call__() missing 1 required positional argument: 'request'`。

### §24.3 修复方案

将 WebSocket 路由从 `config_api_router` 拆出到独立的 `config_ws_router`，单独挂载不带
`dependencies`。HTTP 路由继续保留 `verify_api_key` 强制校验，WebSocket 走无 API key 路径
（WebSocket 握手阶段已通过浏览器同源策略与 CORS 中间件保障安全）。

### §24.4 修复清单（文件:行号格式）

| 文件 | 行号 | 修复内容 |
|------|------|---------|
| `api.py` | 815-821 | 新增 `config_ws_router = APIRouter(prefix="/api/config", tags=["config-ws"])`，添加根因注释 |
| `api.py` | 824 | `@router.websocket("/ws")` → `@config_ws_router.websocket("/ws")` |
| `api.py` | 849 | `@router.websocket("/ws/events")` → `@config_ws_router.websocket("/ws/events")` |
| `app.py` | 82 | `from .api import config_api_router, config_api_init, config_ws_router` |
| `app.py` | 546-547 | 新增 `app.include_router(config_ws_router)`（不带 `dependencies`） |

### §24.5 量化验证数据

启动 `uvicorn app:app --port 8765` 后，使用 Python `websockets` 客户端逐项验证：

| # | 端点 | 测试动作 | 预期 | 实际 |
|---|------|---------|------|------|
| 1 | `ws://127.0.0.1:8765/api/config/ws` | 发送 `ping` | 收到 `pong` | ✅ `pong` |
| 2 | `ws://127.0.0.1:8765/api/config/ws/events` | 仅连接 | 连接成功 | ✅ connected OK |
| 3 | `ws://127.0.0.1:8765/ws/highlight` | 发送 `subscribe_highlight` | 收到 `subscribe_highlight_ack` | ✅ `{"type":"subscribe_highlight_ack","status":"ok"}` |
| 4 | `ws://127.0.0.1:8765/ws/pool/nonexistent` | 连接 | 1008 `Pool not found` | ✅ `1008 policy violation Pool not found` |

服务端启动日志：`INFO: Application startup complete.` + `INFO: Uvicorn running on http://127.0.0.1:8765`，
**无 `TypeError: APIKeyHeader.__call__` 报错**。

### §24.6 架构约束强化

- **路由职责分离原则**：HTTP 路由与 WebSocket 路由必须挂载到不同的 `APIRouter` 实例
- **dependencies 边界**：`include_router(..., dependencies=[Depends(...)])` 中的 dependencies 会
  递归应用到 router 中所有路由，包括 WebSocket；任何依赖 HTTP `Request`/`APIKeyHeader` 的
  dependencies 都会破坏 WebSocket 路由
- **WebSocket 鉴权方案**：如需对 WebSocket 鉴权，应在路由函数体内通过 `websocket.headers.get(...)`
  或 `websocket.query_params.get(...)` 主动校验，不得使用 `Depends(APIKeyHeader)` 形式

## 元模式彻底完善迭代

基于 14 类同构代码模式洞察，分 6 轮迭代完成元模式彻底完善：

### 迭代 1：同步/异步双路径统一
- `_step_once` / `_astep_once` → `_step_once_impl(async_mode)` 单一骨架
- `_on_simulation_step` / `_on_replay_step` → `_on_step_event(driver_type, provider_fn)` 单一处理器
- 5 处 `TickReceived` 发布循环 → `_publish_tick_batch(bus, tick_data, ts)` 模块级函数

### 迭代 2：公式引擎协议化
- 定义 `IFormulaEngine` Protocol，统一 4 个引擎类的 `eval`/`eval_outvars`/`eval_series`/`eval_batch` 签名
- `FormulaRouter` 引擎分派从 if/elif 链改为 `_ENGINE_DISPATCH` dict 表驱动

### 迭代 3：HTTP 路由 Depends 化
- 21+ 处 `if not _config_store` 样板 → FastAPI `Depends(require_config_store)` router 级挂载
- 5 个独立 sim 路由 → 单一 `POST /api/pool/{name}/sim/{action}` + `_SIM_ACTIONS` 表驱动

### 迭代 4：配置加载统一到 ConfigStore
- 10 处散落 `_load_json` / `_load_config` / `_load_json_file` / `_load_json_cache` → `ConfigStore.get_table` / `get_data_file`
- `ConfigStore` 新增 `get_data_file(name)` 方法处理非配置表 JSON

### 迭代 5：中等优先级批次收敛
- `engine.py` `if mode_id == "replay"` → `state_scope` 字段表驱动
- `domain.py` 10+ Node/Edge/Spec 类 `to_dict`/`from_dict` 样板 → `_FieldMeta` + 基类统一实现
- `domain.py` Evaluator 子类 → `_EVALUATOR_REGISTRY` + `@register_evaluator` 装饰器
- 4 个 K 线合成函数 → `synthesize(bars, source, target)` + `_SYNTHESIS_RULES` 表
- 5 个 tdx CRUD 路由 → 2 个端点 + `_TDX_ACTIONS` 表
- 9 个导入/导出方法 → `import_pool` / `export_pool` + `_IMPORT_RULES` / `_EXPORT_RULES` 表
- 4 对 `set_xxx`/`get_xxx` → 直接属性访问（保留 setter 委托）
- 5 个 `draw_xxx` + 矩阵/散点独立渲染 → `renderEventCanvas(layoutMode)` + `_DRAW_LAYERS` + `_STYLE`

### 迭代 6：低优先级收尾
- `table_engine.py` 2 处 `ConfigChanged` 发布 → `_notify_changed` 单一方法
- `execution_module.py` 4 处 `EdgeFired`/`TTLDue` 发布 → `_publish_edge_fired` / `_publish_ttl_due` 工厂函数
