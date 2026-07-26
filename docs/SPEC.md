# 股票池系统规格文档（归并版）

> 本文档归并自原 `specs/` 目录下 13 个规格文档（INDEX / 00-CONTRACT / 01-FORMAT-MAPPING /
> 02-EDGE-EXECUTOR / 03-EVENT-SYSTEM / 04-DIRTY-TRIGGER / 05-FILTER-EVALUATION /
> 06-PROPAGATION / 07-TTL-SYSTEM / 08-CONVERTERS / 09-POOLSTATE-RUNTIME /
> 10-ENGINE-ORCHESTRATION / 11-CALLBACKS-SIGNALS）。
>
> 已更新所有源文件路径引用为 Phase 7/8/9 极致合并后的真实路径。原 `specs/` 目录
> 已于 Phase 10 SubTask 30.2 删除。本文档保留原规格中的架构契约、事件流不变量、
> 节点/边/条件/时机/TTL/回调/传播规则等有效内容；如代码实现与本文档冲突，以代码为准。

---

## 1. 架构契约（原 SPEC-00）

### 1.1 一句话定义

**股票池 = 状态池流水线。** 引擎不含领域字面量，只做：读表 → 计算 → 写表。

### 1.2 run_tick 真实代码

来源：`core/engine.py:280-308`。DESIGN-3 §1.1 是概念层抽象，与实现层不完全一一对应
（非矛盾，是抽象层级差异）。

```python
# engine.py:280 — async 壳，不含 await
async def run_tick(self) -> None:
    self._run_tick_body()

# engine.py:290 — 唯一真相源
def _run_tick_body(self) -> None:
    # 1. 时间推进
    if self.state.time_source.get("driver_type") == "wall_clock":
        self.state.time_source["current_ts"] = _safe_timestamp(self._now())
    self._components["_tick_event_offset"] = len(
        self._components["event_bus"].get_events())

    # 2. 首次执行标脏源节点
    if self.state.first_run:
        self._mark_source_nodes_dirty()

    # 3. 事件驱动：fire_due 统一扫描所有 TimedEventSpec，
    #    at_fn()<=now 就调 action——边触发和 TTL 完全同一套机制
    driver = self._components.get("event_driver")
    now = time_at(state=self.state)
    if driver is not None:
        driver.fire_due(now)          # 原 time_util.py:247，现 core/execution_module.py

    # 4. 收尾
    self.state.clear_dirty()
    self.state.first_run = False
    self.state.snapshot_nodes()
    self._sync_events_to_meta()
```

**关键事实**：
- **不存在 Phase 1 / Phase 2 双循环**。时序判定、边触发、TTL 全在 `fire_due` 一次扫描中完成。
- **`edge_fired` 已在 I94 删除**（原 `edge_state.py:10-14`，现 `core/execution_module.py` EdgeState），不存在于任何运行时表。
- dirty 检查在 `EdgeExecutor.run()` 内部（gate→filter 之前），不是 `run_tick` 的职责。

### 1.3 真实执行链路

```
fire_due(now)                                    # 原 time_util.py:247，现 core/execution_module.py
  → for spec in _specs:
      if spec.at_fn() <= now:                    # 到时判定
        spec.action(spec.params)                # 发布事件

边触发 spec 的 action（原 compiler.py:840-854，现 core/execution_module.py）:
  → bus.publish(Executed(eid, ...))              # edge_executor 订阅
  → EdgeExecutor.run(eid)                        # 原 edge_executor.py，现 core/execution_module.py
    → _gate():    读 TimingSpec + exec_ctx       # 时序门控
    → _filter():  读 FilterSpec + node_stocks    # 强弱筛选
    → _propagate(): 读 PropagateSpec + node_stocks  # 状态流转
                → mark_node_dirty(tgt)          # 标脏下游
                → register_ttl(code, ...)       # 注册 TTL 到期
    → _run_callback(): 读 ActionSpec             # 持久化+副作用
    → _apply_ttl():  读 TTLSpec                  # 超时淘汰（interval 模式）

TTL spec 的 action（compiler 构造，kind="ttl"）:
  → tracker.pop_expired(now)                     # 弹出到期条目
  → 从 node_stocks[tgt] 删除到期股票
  → bus.publish(DomainEvent(TIMEOUT, ...))
```

### 1.4 触发不变式

DESIGN-3 §1.1 定义的概念不变式 `triggered[eid] = edge_fired[eid] AND (dirty.nodes[sid] OR dirty.data)`
在代码中的对应：

| 不变式维度 | 概念层 | 代码实现 | 来源 |
|----------|--------|---------|------|
| 时间门控 | `edge_fired[eid]` | `TimedEventSpec.at_fn() <= now` → `action` 发布 `Executed` | `core/execution_module.py`（原 time_util.py:247-258, compiler.py:840） |
| 数据变化 | `dirty.nodes[sid] OR dirty.data` | compiler 生成的 `action` 闭包：`trigger = dirty.nodes.get(src) or (dirty.data and src in source_ids)`；`trigger` 为 `True` 时才调用 `edge_executor.run(eid)` | `core/execution_module.py`（原 compiler.py:852） |
| 首次执行 | `first_run` 强制标脏 | `_mark_source_nodes_dirty()` | `core/engine.py:299-300` |

**级联规则**：`_propagate()` 中 `mark_node_dirty(tgt)` 标脏下游节点，但 **`fire_due` 已执行完毕**，
新脏节点仅在下一 tick 的 `fire_due` 中触发下游边。

### 1.5 变换单元

变换单元 = (条件转移边 + 转移条件 + 无条件转移边) 三元组。gate/filter/propagate/callback/ttl
五步作用于变换单元，不是作用于单条边。

```
状态池N ──条件转移边──▶ 条件节点(转移条件) ──无条件转移边──▶ 状态池N+1
  (输入视图)         (filter计算)              (输出视图)
```

**边类型由源节点类型决定**（经 602 条 DZH 边 + 995+ 条 TDX 边 100% 验证）：

| 源节点类型 | 边类型 | 在 EventDriver 中的表现 |
|-----------|--------|----------------------|
| 备选池/状态池/数据源 | 条件转移边 | 注册为 `TimedEventSpec`（有 interval/at_fn） |
| 条件节点 | 无条件转移边 | 注册 TimedEventSpec（同所有边，compiler 无 edge_type 过滤）；trigger 依赖上游 `_propagate` 标脏后 `dirty.nodes.get(src)` 为真 |

**nset=5 集合运算不违反此规则**：集合运算节点的入边源是状态池，每条入边注册为独立的
`TimedEventSpec`，各自按时序触发。集合运算的"绕过 filter"是
`FilterSpec.evaluator_type="set_operation"` 的内部分发，与边类型分类正交。

### 1.6 三种表

| 表类 | 角色 | 变更频率 | 审计 | 例子 |
|------|------|---------|------|------|
| 配置表 (JSON) | 规则即代码 | 跨版本持久，可热加载 | config_version | timing.json, dispatch.json |
| 运行时表 (内存 Dict) | 真相源 | per_tick / per_session | 无（影子在持久表） | PoolState._tables, EdgeState |
| 持久表 (SQLite) | 审计影子 | 跨进程持久 | INSERT-only (transfer_log, config_version) | pool_config, node_state |

### 1.7 编译期 / 运行期分离

| 阶段 | 时机 | 产出 | 复用 |
|------|------|------|------|
| 编译期 | `Compiler.compile(pool_config)` | `CompiledSchedule`（7 个 spec 字段 + execution_order） | pool_config 不变时跨 tick 复用 |
| 运行期 | `_run_tick_body()` 每 tick | EventDriver 扫描 + EdgeExecutor 执行 | 不重编译 |

**CompiledSchedule 7 个 spec 字段**（原 compiler.py:142-160，现 `core/execution_module.py`）：

| 字段 | 类型 | 来源 |
|------|------|------|
| `execution_order` | `List[str]` | 拓扑排序 |
| `edge_ctx` | `Dict[str, EdgeContext]` | 边端点/类型/角色 |
| `edge_timing_spec` | `Dict[str, TimingSpec]` | timing.json + edge params |
| `edge_filter_spec` | `Dict[str, FilterSpec]` | dispatch.json + func params |
| `edge_propagate_spec` | `Dict[str, PropagateSpec]` | edge params (tran/attr) |
| `edge_action_spec` | `Dict[str, ActionSpec]` | action_table.json + psatt |
| `edge_ttl_spec` | `Dict[str, TTLSpec]` | tdx_psatt.json + psatt |

**pool_config 变更时**：需重新 `compile()` 并重建 `PoolEngine`，不存在"部分更新 CompiledSchedule"。

### 1.8 三种运行模式

**同一套 `_run_tick_body()`，差异仅在四张表行 + EventDriver 注册的 spec 集合**：

| 模式 | time_source.driver_type | data_source | trade_interface | side_effects_scope |
|------|------------------------|-------------|-----------------|-------------|
| live | wall_clock | tq实时推送 | live_order | live |
| replay | sequence | kline_cache | noop | replay |
| simulation | virtual | mock生成 | paper_trade | simulation |

### 1.9 反模式禁令

- ❌ 引擎代码中出现股票代码、市场类型、指标名称等字面量
- ❌ `if type == "xxx"` / `if nset == X` / `if pool_type == "custom"` 硬编码分支
- ❌ `from xxx_native import yyy` 显式导入领域函数（走 `_handler_registry` 查表）
- ❌ `eval()` 无沙箱表达式求值（走 `CompiledExpression` ast 受控求值）
- ❌ `asyncio.sleep` 轮询等待（走 `loop.call_at` 中断回调）

---

## 2. 事件流契约（原 SPEC-03）

### 2.1 三类领域事件

事件类型定义在 `core/event_bus.py:39-101`：

| 类 | 常量 | 字段 | 语义 | 行号 |
|----|------|------|------|------|
| `Executed` | `EVENT_EXECUTED = "Executed"` | eid, sid, tid, entered, exited, target_cleared, mode, details | 单条边执行完成 | :39, :99 |
| `DomainEvent` | `EVENT_DOMAIN = "DomainEvent"` | domain, name, payload | 领域状态变更（入池/出池/TIMEOUT） | :60, :100 |
| `Signal` | `EVENT_SIGNAL = "Signal"` | signal_type, codes, details | 交易信号（BUY/SELL） | :79, :101 |

### 2.2 Executed.details 结构

仅 `entered` 非空时 `details` 非 None（原 edge_executor.py:800-804，现 `core/execution_module.py`）：

```python
details = {
    "actions": list(actions),      # ActionSpec.target_pool_actions
    "prices": dict(prices),        # _init_entry_trackers 返回的入池价格
    "timestamp": ts,               # 执行时刻 Unix 秒
}
```

`exited` 或 `target_cleared` 非空时 details 仍可为 None（仅 entered 控制是否有详情）。

### 2.3 DomainEvent 域分类

| domain 值 | name 值 | 触发时机 |
|-----------|---------|---------|
| `"ENTER"` | 代码值 | 股票入池（I23 将 per-code 价格合并入 Executed.details.prices，但 `_push_event` 仍发布 DomainEvent(ENTER)） |
| `"EXIT"` | 代码值 | 股票出池（TTL timeout / move 模式 / overwrite 清空） |
| `"TIMEOUT"` | 代码值 | TTL 到期删除 |

**I23 合并**：原 per-code `DomainEvent(ENTER)` 已收敛为 `Executed.details.prices[code]`。订阅者从
`Executed` 即可获得入池信息，无需再监听 `EVENT_DOMAIN` 获取 ENTER。

### 2.4 EventBus 接口

| 方法 | 行号 | 说明 |
|------|------|------|
| `subscribe(event_type, handler)` | :106+ | 注册处理函数 |
| `publish(event)` | :106+ | 发布事件到所有订阅者 |
| `get_events()` | — | 返回事件队列（供 `_sync_events_to_meta` 消费） |
| `clear()` | — | 清空事件队列 |

### 2.5 引擎内订阅关系

`core/engine.py:624-625`：

| 订阅者 | 事件 | 处理方法 |
|--------|------|---------|
| `meta_engine` | `EVENT_SIGNAL` | `_on_signal_event` |
| `meta_engine` | `EVENT_DOMAIN` | `_on_domain_event` |

`_push_event` 经 EventBus 发布 `DomainEvent(ENTER/EXIT/RANK_CHANGED)`（engine.py:955-965），
订阅 `EVENT_DOMAIN` 的组件自动获得推送。`TIMEOUT` 由 `_run_ttl` 直发，不经 `_push_event`。

### 2.6 事件流顺序（单 tick 内）

```
fire_due(now)
  → spec.action(params)               # 边触发 action 闭包
    → edge_executor.run(eid)
      → _publish(bus, Executed(...))   # 步骤 5
      → _run_callback(...)             # 步骤 6
  → ttl action                         # TTL 到期 action
    → DomainEvent(TIMEOUT, code)       # 由 TTL action 发布
    → state.remove_stock(tgt, code)    # 实际删除
```

所有事件在同一次 `fire_due` 调用中发布，在 `_run_tick_body` 末尾由 `_sync_events_to_meta`
批量同步到 MetaEngine 的事件队列。

### 2.7 DataChanged 事件

| 类 | 常量 | 触发时机 |
|----|------|---------|
| `DataChanged` | `EVENT_DATA_CHANGED` | `DataUpdater.apply_data` 中，任何 tick 写入 latest_tick 时发布 |

供 `BarComposer` 等订阅者同步更新。与引擎事件流正交——`DataChanged` 在 `apply_data` 阶段发布，
早于 `fire_due`。

---

## 3. 节点/边/条件/时机/TTL/回调/传播规则

### 3.1 EdgeExecutor 执行管线（原 SPEC-02）

`EdgeExecutor.run(eid)` 是单条边的完整执行（原 edge_executor.py:763-819，现 `core/execution_module.py`）：

```
gate → filter → propagate → callback → publish Executed
```

注意：TTL 不在 `run()` 内执行。TTL 由独立的 `TimedEventSpec(kind="ttl")` 驱动
（原 time_util.py:260-269，现 `core/execution_module.py`），`_propagate` 中仅注册 TTL 追踪条目
（`register_ttl`）。

#### 3.1.1 步骤时序与数据流

| 步骤 | 入参 | 出参 | 副作用 | 原代码行（现 `core/execution_module.py`）|
|------|------|------|--------|--------|
| gate | `TimingSpec` + `exec_ctx` + now | bool（放行/拦截） | 写 `exec_ctx["fired"]` | :821-854 |
| filter | `FilterSpec` + source_codes | (passed, rejected) | 写 `filter_inputs[eid]` | :856-878 |
| propagate | `PropagateSpec` + sid/tid + passed | (entered, exited, target_cleared) | 写 `node_stocks[tgt]` + `node_stocks[src]`；`mark_node_dirty(tid)`；`register_ttl` / `unregister_ttl` | :880-934 |
| callback | `ActionSpec` + entered + prices | None | 写文件/板块/声音/弹窗 | :248 |
| publish | Executed 事件 | None | EventBus 发布 | :798-814 |

#### 3.1.2 gate 返回 False 时的行为

`gate` 返回 `False` → `run()` 返回 `False`（:778），**不执行 filter/propagate/callback**。
`exec_ctx["fired"]` 置 `False`（:839/:845），但不写入 `last_fire`。

### 3.2 gate 双表驱动

gate 由两张表驱动，消除 if/elif 分支（I19）：

#### 3.2.1 starttype 前置门控（0-7）

`_STARTTYPE_GATE_HANDLERS`（:402）：starttype → handler 映射。

| starttype | 语义 | handler | 用途 |
|-----------|------|---------|------|
| 0 | 一直执行 | `_gate_always`（:353） | 无条件放行 |
| 1 | 开盘前 | `_gate_before_open`（:370） | now_sec < open_sec |
| 2 | 开盘后 | `_gate_after_open`（:376） | now_sec >= open_sec |
| 3 | 收盘前 | `_gate_before_close`（:382） | now_sec < close_sec |
| 4 | 收盘后 | `_gate_after_close`（:388） | now_sec >= close_sec |
| 5 | 指定时间 | `_gate_hhmmss`（:394） | now_sec == starttimehms 转秒 |
| 6/7 | [OPEN] | 未在代码中注册 | 待确认 |

**双时间值**（I42）：`now_unix`（Unix 秒，用于 elapsed/interval 算术）与 `now_sec`（当日秒数偏移，
用于 5 个市场时间 gate）一次性计算，避免 virtual 模式下的 offset→anchor→Unix→datetime 往返。

#### 3.2.2 cxtype 后置门控（0-2）

`_CXTYPE_POST_GATES`（:452）：cxtype → handler 映射。

| cxtype | 语义 | handler | 行为 |
|--------|------|---------|------|
| 0 | 一直 | `_cxtype_forever` | 始终放行 |
| 1 | 持续窗口 | `_cxtype_elapsed`（I19） | first_fire + cxtime 内放行，超出拦截 |
| 2 | 只执行一次 | `_cxtype_once` | count==0 放行，否则拦截 |

**cxtype=2 与 DZH 的对应**：TDX `cxtype=2` ↔ DZH `end=1, endt=1`（执行一次后永久拦截）。

#### 3.2.3 interval 触发间隔

与 starttype/cxtype 正交（:848-853）：

```python
if spec.interval_sec > 0:
    last_fire = exec_ctx.get("last_fire")
    if last_fire is not None and now_unix - last_fire < spec.interval_sec:
        return False
```

三重门控顺序：starttype → cxtype → interval。任一拦截即返回 `False`。

### 3.3 filter evaluator 分派

#### 3.3.1 唯一分派键

`FilterSpec.evaluator_type` 是唯一运行期分派键（I53）。`filter_type` 降级为审计追溯元数据，
不参与控制流。

`_FILTER_EVALUATORS`（:621-625）：

| evaluator_type | handler | 语义 |
|---------------|---------|------|
| `"pass_through"` | `_eval_pass_through` | 全部通过（无条件边） |
| `"formula"` | `_eval_formula_path` | 公式求值 |
| `"scalar"` | `_eval_scalar_path` | 标量比较 |
| `"set_operation"` | `_eval_set_op_path`（:614） | 集合运算 |

#### 3.3.2 nset=5 集合运算的正确理解

1. 集合运算节点的**入边源是状态池**，每条入边是条件转移边（注册为 `TimedEventSpec`，有 interval/at_fn），
   **完全符合"边类型由源节点决定"规则**。

2. "绕过 filter" 不是边类型分类的例外——它是 `evaluator_type="set_operation"` 在 `_FILTER_EVALUATORS`
   中的**一个分派分支**（:625）。集合运算边仍然经过 `run()` → `_filter()` 调用链，只是 `_filter`
   内部查表找到 `set_operation` handler，走 `_eval_set_op_path`（:614）→ `_eval_set_operation`（:467）。

3. `_eval_set_operation` 的输入不是单条边的 source_codes，而是**汇聚所有入边的源节点股票集合**，
   按 `ntjindexno`（操作码）做并集/差集/交集（:459-467）：
   - ntjindexno=0 → 并集（union）
   - ntjindexno=1 → 差集（difference）
   - ntjindexno=2 → 交集（intersect）

4. 集合运算边**仍有 gate 门控**（时序判定），只是 filter 语义从"强弱筛选"变为"集合合并"。

#### 3.3.3 noperate 语义随 nset 变化

| nset | noperate 范围 | 语义 | 依据 |
|------|--------------|------|------|
| 0 (系统指标) | 0-9 | 10项操作符（大于/小于/上穿/下穿等） | TDX §8.4 |
| 1 (条件组合) | 0 | [OPEN] 条件组合仅1种语义？ | 待确认 |
| 2 (信号选择) | nfirst 决定 | noperate 不参与 | TDX §8.5.3 |
| 3 (标量函数) | 0-9 子集 | 标量子集操作符 | TDX §8.4 |
| 4 (标量线) | 0-9 子集 | [OPEN] noperate=3 在 nset=4 下有歧义 | 1个样本 |
| 5 (集合运算) | ntjindexno | 0=并/1=差/2=交 | TDX §8.1 |

**[OPEN]** noperate=3 在 nset=4 下的语义仅有 1 个样本，存在歧义，待更多样本确认。

### 3.4 脏状态与触发机制（原 SPEC-04）

#### 3.4.1 DirtyState 数据类

原 `runtime.py:82-94`（现 `core/runtime_mode_module.py`）。两个脏维度：

| 字段 | 类型 | 语义 |
|------|------|------|
| `nodes` | `Dict[str, bool]` | 哪些节点的股票集合发生了变化 |
| `data` | `bool` | 行情数据是否推进 |

#### 3.4.2 脏标记写入点

| 写入方法 | 调用位置 | 原行号 | 说明 |
|---------|---------|------|------|
| `mark_node_dirty(nid)` | `_propagate` → target | edge_executor.py:933（现 `core/execution_module.py`） | 传播后标脏目标节点 |
| `mark_node_dirty(nid)` | `_src_delete` → source | edge_executor.py:678（现 `core/execution_module.py`） | move/overwrite 模式标脏源节点 |
| `mark_node_dirty(nid)` | TTL interval/endtime action 闭包 | compiler.py:912, :995（现 `core/execution_module.py`） | TTL 到期清理后标脏目标节点 |
| `mark_node_dirty(nid)` | engine source init | engine.py:234, :572 | 首次/数据源标脏 |
| `mark_data_dirty()` | DataUpdater.apply_data | data_updater.py:104（现 `core/tick_bar_module.py`） | tick 推进时置脏 |
| `mark_data_dirty()` | runtime.py:250（现 `core/runtime_mode_module.py`） | — | `dirty.data = True` |

#### 3.4.3 脏标记读取点（触发判定）

原 compiler.py:839-854（现 `core/execution_module.py`）编译期生成的 action 闭包：

```python
def action(params: Any) -> None:
    ec = schedule.edge_ctx.get(eid)
    if ec is None:
        return
    src = ec.sid
    dirty = state.dirty
    trigger = dirty.nodes.get(src) or (dirty.data and src in source_ids)
    if trigger:
        edge_executor.run(eid)
```

| 条件 | 语义 | 适用边类型 |
|------|------|----------|
| `dirty.nodes.get(src)` | 源节点股票集合变化 | conditional + unconditional |
| `dirty.data and src in source_ids` | 行情推进且源为数据入口 | 仅 conditional（source 节点） |

**所有边**（conditional + unconditional）均在 `build_timed_event_specs` 中注册 TimedEventSpec
（compiler.py:1051-1067，现 `core/execution_module.py`，无 edge_type 过滤），共用同一个
`_make_edge_action` 闭包。无条件边的触发依赖其源节点被上游 `_propagate` 标脏——
`dirty.nodes.get(src)` 为真即触发。由于 `execution_order` 按拓扑序排列，同一次 `fire_due`
遍历中上游标脏后下游即可在同 tick 内看到并触发。

#### 3.4.4 脏标记清除时序

`core/engine.py` `_run_tick_body`（:305-315）：

```python
driver.fire_due(now)          # 遍历 spec，触发 action → edge_executor.run → mark_node_dirty
self.state.clear_dirty()      # 全量清零 nodes + data
self.state.first_run = False
self.state.snapshot_nodes()   # 冻结当前股票快照
```

**关键不变式**：`fire_due` 内产生的脏标记（propagate 级联）在**同一 tick 内不会被再次消费**——
新脏标记在 `clear_dirty()` 之前的 `fire_due` 遍历中不会被二次触发，因为 `fire_due` 遍历的是
**预注册的 spec 列表**，而非动态查询脏节点。

#### 3.4.5 首次执行标脏

`core/engine.py:299-300`：

```python
if self.state.first_run:
    self._mark_source_nodes_dirty()
```

`_mark_source_nodes_dirty` 遍历 `source_node_ids`，对每个源节点调用 `mark_node_dirty(nid)`
（engine.py:234）。这保证首次 tick 所有源节点的出边都会被触发。

#### 3.4.6 级联规则

1. conditional 边执行 → `_propagate` → `mark_node_dirty(tid)` 标脏条件节点
2. unconditional 出边同样注册 TimedEventSpec，经过 `_make_edge_action` 闭包。当上游 `_propagate`
   标脏条件节点后，`dirty.nodes[src]` 为真，unconditional 边的 action 闭包触发 `edge_executor.run(eid)`
3. 级联是否在同 tick 内完成取决于 `execution_order` 的排列——`_build_execution_order`
   （compiler.py:479-493，现 `core/execution_module.py`）按 `_order` 字段排序（设计者指定，非自动拓扑排序），
   若上游边 `_order` < 下游边 `_order` 则同 tick 级联

### 3.5 公式引擎与过滤器求值（原 SPEC-05）

#### 3.5.1 FormulaEngine 类

原 `formula.py:112+`（现 `core/formula_module.py`）。统一公式引擎（I54 缓存收敛）。

| 方法 | 原行号 | 语义 |
|------|------|------|
| `__init__(state, data_query=None)` | :120 | 接收 PoolState + 可选 data_query |
| `eval(spec, codes, ctx)` | :131 | 公式求值路径，委托 `_eval_formula` + `_cached_eval` |
| `eval_scalar(spec, codes, ctx, evaluator_fn)` | :145 | 标量求值路径，委托外部 evaluator_fn + `_cached_eval` |
| `_cached_eval(spec, codes, ctx, eval_fn, writeback)` | :155 | I54 缓存统一入口，读 formula_results + 命中则返回，未命中则调用 eval_fn 并写回 |
| `_eval_formula(formula_ref, codes, ctx)` | :182 | 从 formula_ref 加载公式体，委托 PythonFormulaEngine 执行 |
| `_cache_key(spec, codes, ctx)` | — | 生成缓存键 |

**属性**：`state`, `_data_query`, `_python_engine`(PythonFormulaEngine), `_logger`：≤5 个。

**缓存策略**（I54）：`_cached_eval` 统一管理 formula 与 scalar 两条路径的读/写。缓存键 =
`("formula", ctx.mode, spec.formula_ref, ctx.bar_hash)`。命中则返回缓存值；未命中则调用
`eval_fn` 并写回 `state.formula_results`。writeback=True 时同时写回 `state.latest_tick[code][formula_ref]`。

**_eval_formula 路径**（:182）：`formula_ref` → `_lookup_builtin_script` 内建查找 → 委托
`_python_engine`(PythonFormulaEngine) 逐只求值 → 返回 `{code: result}` 字典。

#### 3.5.2 EvalContext 三模式

原 `formula.py:52-97`（现 `core/formula_module.py`）。公式求值上下文，决定数据源和时效性：

| 工厂函数 | 原行号 | 用途 | 数据源 |
|---------|------|------|--------|
| `live_context(state, period)` | :66 | 实盘 | `state.latest_tick` + `state.bars` |
| `replay_context(state, period, ...)` | :82 | 回放 | `state.replay` 中的历史快照 |
| `simulation_context(state, period, ...)` | :97 | 模拟 | 模拟时钟 + 虚拟数据 |

#### 3.5.3 求值器实现

原 `evaluators.py`（现 `core/screening_module.py`）：

| 函数 | 原行号 | 对应 evaluator_type | 语义 |
|------|------|---------------------|------|
| `eval_formula_nset(action_inputs, nset_cfg)` | :478 | `formula` | 公式路径，委托 FormulaEngine |
| `eval_scalar_nset(action_inputs, nset_cfg, prev_lookup)` | :599 | `scalar` | 标量提取路径 |
| `eval_nset5_set_operation(action_inputs)` | :705 | `set_operation` | 集合运算（并集/交集/补集） |
| `eval_tdx_condition(dispatch_key, action_inputs)` | :738 | `basic_filter` / `sector_filter` | TDX 条件求值（根据 dispatch_key 区分） |

**分派桥接**：`basic_filter` 和 `sector_filter` 共用 `eval_tdx_condition`，通过 `dispatch_key`
参数区分行为。`cross_section_eval` 未在 evaluators.py 中找到独立函数，可能内联于 formula 路径或
edge_executor 内部。`pass_through` 无需求值器，所有源股票直接通过。

#### 3.5.4 edge_executor 中的 filter 步骤

`EdgeExecutor.run(eid)` 的 filter 步骤（§3.1.1）：

1. 从 `FilterSpec` 取 `evaluator_type` + `formula_ref` + 参数
2. 按 `evaluator_type` 分派到对应求值器
3. 求值器返回通过集合 `passed: Set[str]`
4. `passed` 传入 `_propagate` 决定哪些股票写入目标池

**无条件边**（unconditional）的 `FilterSpec` 无实质过滤——所有源节点股票直接通过
（propagate-only，无 gate/filter 步骤）。

### 3.6 传播模式与股票转移（原 SPEC-06）

#### 3.6.1 四种传播模式

原 compiler.py:692-725（现 `core/execution_module.py`）。由 `attr` 位域 + `tran` + `emptyps` +
`clear_dest_first` 联合决定：

| mode | delete_source | force_move | keep_source | clear_dest_first | tran | 语义 |
|------|:---:|:---:|:---:|:---:|:---:|------|
| `copy` | 0 | — | — | 0 | 0 | 源不变，追加到目标 |
| `overwrite_copy` | 0 | — | — | 1 | 0 | 先清目标，再追加（保留源） |
| `move` | 1 | 0 | 0 | 0 | 0 | 从源移除，追加到目标 |
| `overwrite` | 1 | 1 | — | 0/1 | 0/1 | 先清目标，再从源移除并追加 |

**决策树**（compiler.py:718-724，现 `core/execution_module.py`）：

```
if clear_dest_first:
    mode = "overwrite_copy" if not is_move else "overwrite"
elif is_move:
    mode = "move"
else:
    mode = "copy"
```

其中：
- `is_move = (tran==1) or (delete_source and not keep_source)`（:716）
- `delete_source = bool(attr_int & 0x1)`（:700）
- `force_move = bool(attr_int & 0x2)`（:701）
- `keep_source = bool(attr_int & 0x1000)`（:702）
- `clear_dest_first = params.clear_dest_first OR (emptyps==1) OR (attr_int & 0x2000) OR (delete_source AND force_move)`（:703-709）

#### 3.6.2 目标策略函数

返回 `(entered, target_cleared)` 二元组：

| 函数 | 原行号 | 行为 | target_cleared |
|------|------|------|----------------|
| `_tgt_merge(state, tid, transferred, tgt_stocks)` | :638 | 追加去重写入目标 | `[]`（恒空，不清空已有） |
| `_tgt_overwrite(state, tid, transferred, tgt_stocks)` | :649 | 清空目标后写入 transferred | 被覆盖出目标池的代码列表 |

**`_tgt_merge` 内部**（:638-648）：existing = 去重集合；new_stocks = transferred 中不在 existing
的；`set_node_stocks(tid, tgt_stocks + new_stocks)`；返回 `(new_codes, [])`。

**`_tgt_overwrite` 内部**（:649-677）：保全已持仓代码的 `_tracker`（I66 修复）；
`set_node_stocks(tid, transferred)` 替换全部；`entered` = transferred 中不在 existing 的新代码；
`target_cleared` = existing 中不在 transferred 的旧代码（I69 修复 view drift）。

#### 3.6.3 源策略函数

| 函数 | 原行号 | 行为 | 返回值 |
|------|------|------|--------|
| `_src_delete(state, sid, src_stocks, passed_set)` | :678 | 从源池删除 passed 股票，标脏源 | `exited: List[str]` |
| `_src_keep(state, sid, src_stocks, passed_set)` | :686 | 保留源池不变（no-op） | `[]` |

**`_src_delete` 内部**（:678-684）：
1. `deleted = [_stock_code(s) for s in src_stocks if _stock_code(s) in passed_set]`
2. `state.set_node_stocks(sid, [s for s in src_stocks if _stock_code(s) not in passed_set])`
3. `state.mark_node_dirty(sid)` ← move/overwrite 模式下源节点标脏
4. `return deleted`

**`_src_keep`** 不修改源池、不标脏，返回空列表。

**mode → (target_strategy, source_strategy) 映射**（`_PROPAGATE_STRATEGIES`，:696-701）：

| mode | target_strategy | source_strategy |
|------|----------------|----------------|
| copy | _tgt_merge | _src_keep |
| move | _tgt_merge | _src_delete |
| overwrite | _tgt_overwrite | _src_delete |
| overwrite_copy | _tgt_overwrite | _src_keep |

#### 3.6.4 propagate 执行流程

原 edge_executor.py `_propagate` 步骤（:880-935，现 `core/execution_module.py`）：

1. 从 `PropagateSpec` 取 `mode` → 查表得 (target_strategy, source_strategy)
2. 取源节点股票 `src_stocks = state.get_node_stocks(sid)`
3. 取 filter 通过集 `passed`（或无条件时 `passed = all src stocks`）
4. 调用 `target_strategy(state, tid, passed, ...)` → `(entered, target_cleared)`
5. 调用 `source_strategy(state, sid, src_stocks, passed)` → `exited`
6. TTL 注册：对 `entered` 中每只股票调用 `_init_entry_trackers`（:792）
7. TTL 注销：对 `target_cleared` 中每只股票调用 `event_driver.unregister_ttl(eid_key, code)`（:926-930）
8. `state.mark_node_dirty(tid)`（:933）

#### 3.6.5 TTL 注册与注销的传播交互

| 事件 | 注册/注销 | 触发时机 |
|------|----------|---------|
| 股票入池 | `_init_entry_trackers` → `event_driver.register_ttl(eid, code, ttl_sec, ts, ts)` | `_propagate` 步骤 6 |
| 股票出池（TTL timeout） | TtlTracker `pop_expired` → `DomainEvent(TIMEOUT)` → `state.remove_stock` | TTL action |
| 股票被覆盖（overwrite） | `event_driver.unregister_ttl(eid_key, code)` | `_propagate` 步骤 7 |
| 股票移出源池（move） | 不注销（源池无 TTL 追踪） | — |

**注意**：overwrite 清空目标时，被覆盖的股票的 TTL 追踪必须注销，否则 TtlTracker 中残留过期
条目导致下次 `pop_expired` 误删新入池的同代码股票。

### 3.7 TTL 系统超时管理（原 SPEC-07）

#### 3.7.1 EventDriver TTL 桥接方法

原 `time_util.py:228-245`（现 `core/execution_module.py`）：

| 方法 | 原行号 | 行为 |
|------|------|------|
| `register_ttl(eid, code, ttl_sec, entry_ts, now_unix)` | :228 | 委托 `tracker.register()` |
| `unregister_ttl(eid, code)` | :234 | 委托 `tracker.unregister()` |
| `add_ttl_tracker(eid, tracker)` | — | 编译期注册 TtlTracker 实例 |

`EventDriver._ttl_trackers: Dict[str, TtlTracker]` 维护 eid → tracker 映射。

#### 3.7.2 TTL 到期处理流程

```
fire_due(now)
  → ttl spec.at_fn() <= now
    → ttl spec.action(params)
      → tracker.pop_expired(now) → List[TtlEntry]
      → for entry in expired:
          state.remove_stock(entry.tgt, entry.code)
          bus.publish(DomainEvent(TIMEOUT, entry.code))
```

TIMEOUT 由 TTL action 直发，不经 `_push_event`（engine.py docstring I70 确认）。

---

## 4. 信号与回调（原 SPEC-11）

### 4.1 _run_callback：目标节点副作用

原 edge_executor.py:248（现 `core/execution_module.py`）。消费 `_propagate` 返回的
`(entered, exited, target_cleared)` 三元组，对每只入池股票执行已注册的目标池动作。

**流程**：
1. 从 `CallbackSpec` 取 `actions` 列表
2. 对每只 `entered` 中的股票：
   - 遍历 `actions`，查 `_ACTION_HANDLERS` 表
   - 命中则调用 handler(state, eid, code, ...)
   - 未命中则跳过（I23：bsound/btip/bsavetoblock/bsavehis 不再注册 handler）

### 4.2 _ACTION_HANDLERS 表

原 edge_executor.py:240-244（现 `core/execution_module.py`）。表驱动分派，无 if/elif：

| action | handler | 原行号 | 语义 |
|--------|---------|------|------|
| `baimpool` | `_action_baimpool` | :220/244 | 发布 BUY Signal（目标池入池信号） |
| `bsound` | — | 未注册 | I23 后不再产生独立事件 |
| `btip` | — | 未注册 | I23 后不再产生独立事件 |
| `bsavetoblock` | — | 未注册 | I23 后不再产生独立事件 |
| `bsavehis` | — | 未注册 | I23 后不再产生独立事件 |

**关键架构决策**（I23）：bsound/btip/bsavetoblock/bsavehis 的 action 信息合并入
`Executed.details`，不再通过 `_run_callback` 产生独立事件。`_ACTION_HANDLERS` 仅保留 `baimpool`。

### 4.3 _action_baimpool：BUY Signal 发布

原 edge_executor.py:220-244（现 `core/execution_module.py`）：

```python
def _action_baimpool(state, eid, code, ...):
    """baimpool 动作：发布 BUY 信号（目标池入池）。"""
```

**Signal 字段**（I34 扩展）：
- `action`: `"BUY"`
- `code`: 股票代码
- `condition`: 边条件描述（I34：解析边条件 cond 传入）
- `profit_pct`: 浮动盈亏百分比（从 `_tracker.entry_price` 计算）
- `hold_days`: 持仓天数（从 `_tracker.entry_time` 计算）
- `eid`: 触发边 ID

Signal 由 EdgeExecutor 经 EventBus 发布（`EVENT_SIGNAL`），**非** engine 的 `_emit_transfer_events`。
engine.py:779 注明此处跳过 BUY 发布；engine.py:622-624 订阅 `EVENT_SIGNAL` 消除双发 BUY。

### 4.4 _emit_transfer_events：DomainEvent 生成

`core/engine.py:835`。根据 `prev_stocks` vs `updated_stocks` 的差异生成 DomainEvent：

| 事件类型 | 触发条件 | 语义 |
|---------|---------|------|
| `ENTER` | code 在 updated 中但不在 prev 中 | 股票入池 |
| `EXIT` | code 在 prev 中但不在 updated 中 | 股票出池 |
| `RANK_CHANGED` | `builtins._ra_score_weighted_sort`（native/builtins.py:1331-1336）三重守卫 | 排名变动 |

**RANK_CHANGED 触发条件**（`native/builtins.py:1331-1336`）：

1. `sc.get("rank_change")` 为 truthy（配置开关）
2. `push_event` kwarg 非空（回调可用）
3. 对每只股票：`prev.get(code) is not None`（有历史排名）且 `abs(old_rank - new_rank) >= delta`

其中 `delta = cfg.get('rank_change_rules', {}).get('thresholds', [{}])[2].get('delta', 5)`。

**⚠️ 索引错位（latent fragility）**：`pk_config.json` 的 `thresholds` 数组实际为
`[RANK_DOWN, rank_delta/RANK_CHANGED, SCORE_BREAKTHROUGH, SCORE_BREAKDOWN]`。代码访问
`thresholds[2]` 实际命中 `SCORE_BREAKTHROUGH`（无 `delta` 字段），回退默认值 `5`；而真正的
RANK_CHANGED 规则位于 `thresholds[1]`（`delta:5`）。代码**碰巧正确**（默认 5 = 配置 5），但若
任一方变更将静默错用阈值。硬编码索引 `[2]` 还可能在列表长度 <3 时 IndexError。

**Payload**：`{"old_rank": p, "new_rank": i, "score": r['pk_score']}`

**发布路径**：engine.py:951 将 `self._push_event` 作为 `push_event` kwarg 注入 post-tick handler。
`_ra_score_weighted_sort` 调用 `push_event("RANK_CHANGED", c, "", {...})` → `engine._push_event`
→ `EventBus.publish(DomainEvent)`。与 ENTER/EXIT 共享同一 `_push_event` → EventBus 路径，但
**不经过** `_emit_transfer_events`，因此不写入 `transfer_events` 列表。调用时 `pool_id=""`（空字符串），
而 ENTER/EXIT 携带真实 pool_id。

`_emit_transfer_events` 写入 `transfer_events` 列表，供 `_push_event` 消费（仅 ENTER/EXIT）。

### 4.5 _push_event：EventBus 发布

`core/engine.py:956`。将转移事件经 EventBus 发布为 DomainEvent：

```python
def _push_event(self, et, code, pool_id, detail=None):
```

- `et`: 事件类型（"ENTER"/"EXIT"/"RANK_CHANGED"）
- 发布为 `DomainEvent(type=et, code=code, pool_id=pool_id, details=detail)`
- 订阅 `EVENT_DOMAIN` 的组件（如 UIRenderer、SnapshotBuilder）自动接收

### 4.6 TIMEOUT 事件

TTL 到期由 TTL action 直发 `DomainEvent(TIMEOUT)`，不经 `_push_event`（engine.py docstring I70
确认）。TIMEOUT 不写入 `transfer_events` 列表。

### 4.7 SnapshotBuilder 视图层

原 `snapshot_builder.py:23+`（现 `core/monitoring_module.py` 内 `_SnapshotBuilder`）。

| 方法/类 | 原行号 | 语义 |
|---------|------|------|
| `SnapshotBuilder` | :23 | 视图构建器，消费 Executed 事件 |
| `snapshot()` | :139 | 返回当前快照字典 `{pool_id: {code: detail}}` |

**view drift 修复**（I69）：`target_cleared` 修复前，`node_stocks` 已 REPLACE 但 Executed 事件
不携带被覆盖代码，view 只 ADD 不 DISCARD → view drift。修复后 `Executed.target_cleared` 提供
被覆盖出目标池的代码，SnapshotBuilder 可同步 DISCARD 陈旧代码。

### 4.8 副作用范围门控

`state.side_effects_scope` 在运行模式设置时由 `run_mode()` 写入（engine.py:501-506）。任何副作用
执行前查本表 allowed/denied：

- allowed → 正常执行
- denied → 记录日志并跳过，**不抛异常**（避免中断回放/仿真流程）

**关键门控点**：
- `_action_baimpool`：`live_order`/`paper_order` 受限
- 持久化操作：`persist_tracker` 受限
- 通知操作：`send_notification` 受限

详细范围表见 §5.5 或 `config/side_effect_scopes.json`。

### 4.9 事件发布路径总结

| 事件 | 发布者 | 经 EventBus | 经 _push_event | 入 transfer_events |
|------|--------|:-----------:|:-------------:|:-----------------:|
| Executed | EdgeExecutor | ✓ | — | — |
| BUY Signal | EdgeExecutor._action_baimpool | ✓ (EVENT_SIGNAL) | — | — |
| ENTER/EXIT | engine._push_event | ✓ (EVENT_DOMAIN) | ✓ | ✓ |
| RANK_CHANGED | builtins._ra_score_weighted_sort → engine._push_event (kwarg pass-through) | ✓ (EVENT_DOMAIN) | ✓ | — |
| TIMEOUT | TTL action._run_ttl | ✓ (EVENT_DOMAIN) | — | — |
| DataChanged | DataUpdater | ✓ (EVENT_DATA_CHANGED) | — | — |

---

## 5. 引擎编排与状态层

### 5.1 核心循环：_run_tick_body（原 SPEC-10）

`core/engine.py:285-320`。每 tick 执行：

```
1. wall_clock 模式更新 current_ts
2. 记录 _tick_event_offset
3. first_run → _mark_source_nodes_dirty()
4. now = time_at(state)
5. driver.fire_due(now)
6. state.clear_dirty()
7. state.first_run = False
8. state.snapshot_nodes()
9. _sync_events_to_meta()
```

**关键架构约束**（side_effect_scopes.json notes）：
> "模式差异仅体现在四张配置表行（time_source / data_source / trade_interface / side_effects_scope），
> 核心循环 run_tick() 不再分支。"

即 `fire_due` 路径在 live/replay/simulation 三模式下完全相同，差异由配置表 + 副作用范围门控实现。

### 5.2 数据注入：DataUpdater

原 `data_updater.py`（现 `core/tick_bar_module.py`）：

| 方法 | 原行号 | 行为 |
|------|------|------|
| `apply_data(tick_data)` | :67 | 接收行情字典，更新 `latest_tick`，推进 `_ts` 时调用 `mark_data_dirty()` 并 `_publish_tick_changed` |
| `_publish_tick_changed(codes)` | :148 | 发布 `DataChanged(ts, bar_hash, codes, source="tick")` 到 EventBus |
| `on_data_changed(event)` | BarComposer | 订阅 `EVENT_DATA_CHANGED`；仅处理 `source="tick"` 的事件，跳过 bar 源（避免循环） |

**DataUpdater.apply_data 流程**（:67-104）：
1. `update_latest_tick(tick_data)` → 返回 `changed: bool`
2. 仅在 `_ts` 推进时：`mark_data_dirty()` + `_publish_tick_changed(updated_codes)`
3. `changed` 返回值供外部决定是否执行 tick

### 5.3 首次运行初始化

`_mark_source_nodes_dirty()`（engine.py:564-576）：
- 遍历所有节点，对 `nid in source_node_ids` 或 `has_initial_stocks=True` 的节点调用
  `mark_node_dirty(nid)`
- 使源节点和预填股票的状态池在首个 tick 被驱动执行

`run_pool()`（engine.py:324+）：
1. `first_run = True`
2. `clear_dirty()`
3. 清空 transfer_events / event_bus / meta.events
4. 循环执行直到 `_event_queue` 为空

### 5.4 三种运行模式

| 维度 | live | replay | simulation |
|------|------|--------|------------|
| `_mode` | — | `"replay"` | `"simulation"` |
| time_source.driver_type | `"wall_clock"` | `"sequence"` | `"virtual"` |
| data_source | 实时行情 | 历史回放 | 模拟数据 |
| trade_interface | 真实交易 | — | 模拟交易 |
| side_effects_scope | `"live"` | `"replay"` | `"simulation"` |

**模式切换**（engine.py:490-515）：`run_mode(mode_id)` 是模式（重新）启动入口，每次进入新模式重置
`first_run`。`time_source.driver_type == "virtual"` 时注入 `bars_history_getter`（:615-620）。

### 5.5 副作用范围（side_effect_scopes.json）

#### 5.5.1 live 范围

| 类别 | 动作 |
|------|------|
| allowed | read_state, compute, emit_event, emit_signal, **live_order**, write_external_state, persist_tracker |
| denied | — |

实盘模式允许所有副作用，包括真实下单与外部状态写入。

#### 5.5.2 replay 范围

| 类别 | 动作 |
|------|------|
| allowed | read_state, compute, emit_event, emit_signal, update_internal_replay_state |
| denied | **live_order**, **paper_order**, write_external_state, persist_tracker, send_notification |

回放模式禁止任何形式的下单与外部写入。

#### 5.5.3 simulation 范围

| 类别 | 动作 |
|------|------|
| allowed | read_state, compute, emit_event, emit_signal, **paper_order**, update_internal_paper_state |
| denied | **live_order**, write_external_state, persist_tracker, send_notification |

仿真模式允许模拟下单（维护虚拟持仓），禁止真实下单。

**deny 策略**（side_effect_scopes.json notes）：
> "任何副作用执行前应查本表；若 action 在 denied 列表中，则记录日志并跳过，不抛异常
> （避免中断回放/仿真流程）。"

### 5.6 EventDriver.fire_due 调度

`fire_due(now)` 遍历所有 TimedEventSpec（边触发 + TTL 统一列表），对每条 spec：
1. `at_fn() <= now` → 已到期
2. 调用 `action(params)` → 发布事件或执行逻辑
3. 继续下一条 spec

边触发 at_fn 委托 `edge_executor._gate(timing, eid)`；TTL at_fn 委托 `TtlTracker.next_expire_at()`。
两者共用 `at_fn() <= now` 语义。

### 5.7 BarComposer 订阅链

```
DataUpdater.apply_data
  → _publish_tick_changed(codes)
    → EventBus.publish(DataChanged)
      → BarComposer.on_data_changed(event)
        → on_tick(codes)  [仅 source="tick"]
          → 更新多周期 bars
          → bar 推进时 publish DataChanged(source="bar")
```

bar 推进事件供下游订阅者（公式引擎等）使用，但 BarComposer 自身跳过 bar 源事件避免循环。

### 5.8 PoolState 与持久化（原 SPEC-09）

#### 5.8.1 _TABLE_NAMES 与动态表机制

原 `runtime.py:62-80`（现 `core/runtime_mode_module.py`）。15 张逻辑表：

| 表名 | 语义 |
|------|------|
| `node_stocks` | 节点→股票列表（核心状态） |
| `latest_tick` | 最新行情 tick |
| `prev_tick` | 上一 tick |
| `bars` | 多周期 K 线 |
| `node_snapshots` | 节点快照（脏检测基准） |
| `topology` | 图拓扑数据 |
| `post_tick_results` | tick 后计算结果缓存 |
| `alert_cooldown` | 告警冷却计时 |
| `time_source` | 时间源配置 |
| `data_source` | 数据源配置 |
| `trade_interface` | 交易接口配置 |
| `side_effects_scope` | 副作用范围配置 |
| `replay` | 回放状态 |
| `simulator` | 仿真状态 |
| `bars_history` | 历史K线 |

**动态表访问**（PoolStateMixin:97-130）：
- `__getattr__(name)` → `_tables[name]`（:125），仅对 `_TABLE_NAMES` 成员生效
- `__setattr__(name, value)` → `_tables[name] = value`（:130），同上
- 外部代码 `state.node_stocks` 等价于 `state._tables["node_stocks"]`

#### 5.8.2 PoolState 核心方法

| 方法 | 原行号 | 语义 |
|------|------|------|
| `get_node_stocks(nid)` | :153 | 返回节点股票列表（浅拷贝或引用） |
| `set_node_stocks(nid, stocks)` | :156 | 替换节点股票列表 |
| `mark_node_dirty(nid)` | :246 | `dirty.nodes[nid] = True` |
| `mark_data_dirty()` | :249 | `dirty.data = True` |
| `is_node_dirty(nid)` | :252 | 查询节点脏标记 |
| `is_data_dirty()` | :255 | 查询数据脏标记 |
| `clear_dirty()` | :258 | 重置 DirtyState（tick 末调用） |
| `get_exec_ctx(eid)` | :265 | 获取边执行上下文 |
| `set_exec_ctx_fired(eid, now)` | :268 | 记录边触发时间 |
| `get_formula_result(ref, hash)` | :274 | 公式结果缓存读 |
| `set_formula_result(ref, hash, val)` | :277 | 公式结果缓存写 |
| `snapshot_nodes()` | :283 | 快照所有节点股票（frozenset），存入 `node_snapshots` |
| `restore_snapshots()` | :291 | 从 `node_snapshots` 还原 `node_stocks` 为 `[{'code': c}]` 列表，返回还原字典 |
| `set_time_source(config)` | :314 | 设置时间源 |
| `get_time_source()` | :317 | 获取时间源配置 |
| `set_data_source(config)` | :320 | 设置数据源 |
| `set_trade_interface(config)` | :326 | 设置交易接口 |
| `set_side_effects_scope(scope)` | :332 | `self.side_effects_scope = dict(se_config)`（浅拷贝写入） |
| `update_latest_tick(tick_data)` | :174 | 更新行情数据（含 hash 校验） |
| `bar_hash()` | :165 | K线聚合摘要 |

#### 5.8.3 snapshot_nodes / restore_snapshots

原 runtime.py:283-290（现 `core/runtime_mode_module.py`）。`snapshot_nodes()` 返回
`{nid: frozenset(stocks)}`，冻结当前所有节点的股票集合。下一 tick 可通过 `snapshot` 与
`get_node_stocks` 的差异检测变化。

`clear_dirty()` + `snapshot_nodes()` 的顺序（engine.py:305-307）保证：
- snapshot 捕获的是 fire_due 修改后的最终状态
- 脏标记已清零，下一 tick 的 trigger 判定从干净状态开始

---

## 6. 格式映射与转换层

### 6.1 格式映射：DZH/TDX → 配置表 → 编译器 → Spec 字段（原 SPEC-01）

#### 6.1.1 映射链路总览

```
DZH XML / TDX 公式文本
  → converter 层（现 converters.py）→ pool_config JSON
    → Compiler.compile() 读 config/*.json
      → CompiledSchedule 7 spec 字段 + 6 facade 字段
        → EdgeExecutor / EventDriver 运行期只读消费
```

#### 6.1.2 pool_config 结构

| 顶层键 | 类型 | 来源 | 说明 |
|--------|------|------|------|
| `nodes` | `{nid: node}` 或 `[node]` | converter | `_normalize_nodes` 统一为 dict（compiler.py:457-468，现 `core/execution_module.py`） |
| `edges` | `[edge]` | converter | 每条边含 `id/from/to/params/attr` |
| `id` / `pool_id` | str | converter | 池标识 |
| `name` | str | converter | 池名称 |

#### 6.1.3 CompiledSchedule 字段清单

原 compiler.py:142-160（现 `core/execution_module.py`）。**13 个字段**（7 spec + 6 facade）：

| 字段 | 类型 | 编译函数 | 原行号 | 说明 |
|------|------|---------|------|------|
| `execution_order` | `List[str]` | `_build_execution_order` | :479 | 按 `_order` 字段排序 |
| `edge_ctx` | `Dict[str, EdgeContext]` | `_build_edge_ctx` | :496 | 端点/类型/角色 |
| `edge_timing_spec` | `Dict[str, TimingSpec]` | `_build_timing_spec` | :528 | starttype/cxtype/interval |
| `edge_filter_spec` | `Dict[str, FilterSpec]` | `_build_filter_spec` | :580 | evaluator_type/formula_ref |
| `edge_propagate_spec` | `Dict[str, PropagateSpec]` | `_build_propagate_spec` | :682 | mode/clear_dest_first |
| `edge_action_spec` | `Dict[str, ActionSpec]` | `_build_action_spec`（模块级） | :300 | target_pool_actions/callbacks |
| `edge_ttl_spec` | `Dict[str, TTLSpec]` | `_build_ttl_spec`（模块级） | :370 | check_type/ttl_sec/endtime_sec |
| `node_types` | `Dict[str, str]` | 内联 | :738 | nid → resolved type |
| `source_node_ids` | `Set[str]` | `_is_source_node` | :739 | 数据入口节点集合 |
| `node_ttl_spec` | `Dict[str, TTLSpec]` | `_build_ttl_spec` | :763-769 | I16：无入边节点的 TTL |
| `topo_order` | `List[str]` | 深度排序 | :780 | 兼容 facade |
| `depths` | `Dict[str, int]` | 深度计算 | :772-779 | 兼容 facade |
| `nodes` | `Dict[str, Any]` | `_normalize_nodes` | :796 | 原始节点快照 |
| `edge_index` | `Dict[str, Any]` | 内联 | :781 | eid → edge dict |

#### 6.1.4 TimingSpec 映射

| pool_config 字段 | config 表 | Spec 字段 | 类型 | 原行号 |
|-----------------|----------|----------|------|------|
| `params.starttype` | `timing.json:starttype_rules` | `starttype` | int(0-7) | :533 |
| `params.cxtype` | `timing.json:cxtype_rules` | `cxtype` | int(0-2) | :534 |
| `params.starttime` | — | `starttime` | int | :535 |
| `params.starttimetype` | — | `starttimetype` | int | :536 |
| `params.starttimehms` | — | `starttimehms` | int | :537 |
| `params.cxtime × cxtimetype` | `timing.json:cxtime_units` | `duration_sec` | float | :538-545 |
| `params.jgtime` | — | `interval_sec` | int | :558 |
| starttype_rules+cxtype_rules | `timing.json` | `gate_expr` | str | :549 |

#### 6.1.5 FilterSpec 映射

| pool_config 字段 | config 表 | Spec 字段 | 原行号 |
|-----------------|----------|----------|------|
| `params.tdx_func.nset` | `dispatch.json:nset_dispatch` → `dispatch_key` | `evaluator_type`（编译期解析） | :602-618 |
| `params.tdx_func.accode` | — | `formula_ref`（formula 路径） | :627 |
| `params.tdx_func.ntjindexno` | — | `formula_ref`（scalar/set_op 路径） | :629 |
| `params.tdx_func.nperiod` | — | `formula_period` | :639 |
| `params.tdx_func.fsecond` | — | `threshold` | :644 |
| `params.tdx_func.noperate` | — | `noperate` | :645 |
| `params.tdx_func.sorttype` | — | `sorttype` | :646 |
| `params.tdx_func.compare_mode` | — | `compare_mode` | :647 |
| nset_dispatch entry 子集 | — | `evaluator_params`（scalar 路径） | :632-637 |
| dispatch_key | — | `filter_type`（审计追溯） | :641 |

**dispatch_key → evaluator_type 编译期映射**（compiler.py:564-571，现 `core/execution_module.py`）：

| dispatch_key | evaluator_type | 语义 |
|-------------|---------------|------|
| `TDX_INDICATOR` | `formula` | 技术指标(nset=0) |
| `TDX_CONDITION_FORMULA` | `formula` | 条件选股(nset=1) |
| `TDX_EXPERT_SYSTEM` | `formula` | 专家系统(nset=1) |
| `TDX_FINANCIAL` | `scalar` | 财务(nset=3) |
| `TDX_MARKET` | `scalar` | 市场(nset=3/4) |
| `TDX_SETOP` | `set_operation` | 集合运算(nset=5) |

#### 6.1.6 PropagateSpec 映射

| pool_config 字段 | Spec 字段 | 原行号 | 说明 |
|-----------------|----------|------|------|
| `params.tran` | `mode`（间接） | :698 | tran=1 → move |
| `params.attr / edge.attr` 位域 | `mode`（间接） | :692-696 | bit0=delete_source, bit2=force_move, bit12=keep_source, bit13=clear_dest |
| `params.emptyps` | `clear_dest_first` | :699,706 | emptyps=1 → overwrite |
| `params.clear_dest_first` | `clear_dest_first` | :705 | 显式覆盖 |
| — | `preserve_source` | :725 | not is_move or keep_source |

**attr 位域 → mode 决策表**（compiler.py:701-720，现 `core/execution_module.py`）：

| bit0(del) | bit2(force) | bit12(keep) | bit13(clear) | tran | mode |
|-----------|------------|------------|-------------|------|------|
| 0 | — | — | 0 | 0 | copy |
| 0 | — | — | 1 | 0 | overwrite_copy |
| 1 | 0 | 0 | 0 | 0 | move |
| 1 | 1 | — | 0/1 | 0 | overwrite |
| — | — | — | — | 1 | move |

#### 6.1.7 ActionSpec 映射

| psatt 字段 | config 表 | Spec 字段 | 原行号 |
|-----------|----------|----------|------|
| `tdx_psatt.bsavehis` | `action_table.json:pool_enter_actions` | `target_pool_actions[].append("bsavehis")` | :314-316 |
| `tdx_psatt.bsavetoblock` | 同上 | `target_pool_actions[].append("bsavetoblock")` | 同上 |
| `tdx_psatt.bsound` | 同上 | `target_pool_actions[].append("bsound")` | 同上 |
| `tdx_psatt.btip` | 同上 | `target_pool_actions[].append("btip")` | 同上 |
| `tdx_psatt.baimpool` | 硬编码 | `target_pool_actions[].append("baimpool")` | :322-323 |
| cfg.op | `action_table.json:callback_ops` | `callbacks[].append(op)` | :317-319 |

#### 6.1.8 TTLSpec 映射（三模式优先级）

| 优先级 | 触发条件 | check_type | 关键字段 | 原行号 |
|--------|---------|-----------|---------|------|
| 1 | bdel=0 | `none` | — | :384 |
| 2 | delstocktype=1 + endtime | `endtime` | endtime_sec / hold_for_ttl | :390-395 |
| 3 | ndelnum × ttl_units[ndeltype]（TDX）/ hold × ttl_units[deltype_map[deltype]]（DZH） | `interval` | ttl_sec | :396-405 |

#### 6.1.9 DirtyState 接口映射

| 方法 | 行为 | 原行号 |
|------|------|------|
| `mark_node_dirty(nid)` | `dirty.nodes[nid] = True` | :246-247 |
| `mark_data_dirty()` | `dirty.data = True` | :250 |
| `is_node_dirty(nid)` | `dirty.nodes.get(nid, False)` | :252-253 |
| `is_data_dirty()` | `dirty.data` | :255-256 |
| `clear_dirty()` | nodes.clear() + data=False | :258-260 |

#### 6.1.10 事件类

`core/event_bus.py:39-101`：

| 类/常量 | 字段 | 行号 |
|---------|------|------|
| `Executed` | eid, sid, tid, entered, exited, target_cleared, mode, details | :39 |
| `DomainEvent` | domain, name, payload | :60 |
| `Signal` | signal_type, codes, details | :79 |
| `EVENT_EXECUTED` | `"Executed"` | :99 |
| `EVENT_DOMAIN` | `"DomainEvent"` | :100 |
| `EVENT_SIGNAL` | `"Signal"` | :101 |

#### 6.1.11 边触发 action 闭包

原 compiler.py:839-854（现 `core/execution_module.py`）。**这是引擎最关键的机制**——编译期为
每条边生成闭包，注册为 `TimedEventSpec.action`：

```python
def action(params: Any) -> None:
    ec = schedule.edge_ctx.get(eid)
    if ec is None:
        return
    src = ec.sid
    dirty = state.dirty
    trigger = dirty.nodes.get(src) or (dirty.data and src in source_ids)
    if trigger:
        edge_executor.run(eid)
```

**所有边**（conditional + unconditional）均在 `build_timed_event_specs` 中注册 TimedEventSpec
（compiler.py:1051-1067，现 `core/execution_module.py`，无 edge_type 过滤）。无条件边的触发依赖其
源节点被上游 `_propagate` 标脏——当 `dirty.nodes.get(src)` 为真时，action 闭包调用
`edge_executor.run(eid)`。由于 `execution_order` 按拓扑序排列，同一 tick 的 `fire_due` 遍历中，
上游边的 `_propagate` 标脏下游节点后，下游边的 action 在同一次遍历中即可看到脏标记并触发。

### 6.2 转换层：DZH/TDX XML → pool_config（原 SPEC-08）

#### 6.2.1 双转换器架构

转换器现合并为单一 `converters.py`（项目根目录）：

| 转换器 | 入口函数 | 输入 | 输出 | 来源 |
|--------|---------|------|------|------|
| DZH | `dzh_to_internal(xml_content)` | DZH XML 字符串 | `PoolMetaModel` | 原 converters/dzh.py（入口已验证） |
| TDX | `tdx_to_internal(tdx_pool)` | `TdxPoolMetaModel` | `PoolMetaModel` | 原 converters/tdx.py:494 |

公共后续步骤：`PoolMetaModel` → `convert_to_config(pool_meta)` → `pool_config JSON`

#### 6.2.2 DZH 转换器关键函数

| 函数 | 原行号 | 语义 |
|------|------|------|
| `identify_flow_mode(attr_int)` | :102 | 从 attr 位域识别流的传播模式（copy/move/overwrite） |
| `_decode_xml_content(xml_content)` | :384 | 解码 XML 内容（处理编码/压缩） |
| `decode_attr_flags(attr_int, type_key)` | :473 | 解码 attr 位域为语义标志 |
| `parse_attrtext_triple(attrtext_str)` | :602 | 解析 attrtext 三元组 |
| `parse_attrtext_selections(attrtext_str)` | :746 | 解析 attrtext 选股条件序列 |
| `build_attrtext_from_selections(selections)` | :846 | 从选股条件重建 attrtext |

#### 6.2.3 TDX 转换器关键函数

| 函数 | 原行号 | 语义 |
|------|------|------|
| `detect_xml_version(root)` | :143 | 检测 TDX XML 版本 |
| `_parse_func_element(func_elem)` | :267 | 解析公式元素 |
| `_parse_psatt_element(psatt_elem)` | :282 | 解析持仓属性（TTL 配置来源） |
| `_parse_spinfo_element(spinfo_elem)` | :308 | 解析状态池信息 |
| `_parse_stk_elements(cell_elem)` | :350 | 解析股票元素列表 |
| `parse_tdx_xml(filepath)` | :364 | 主入口：XML → TdxPoolMetaModel |
| `tdx_to_internal(tdx_pool)` | :494 | TdxPoolMetaModel → PoolMetaModel |
| `_convert_candidate_cell(cell)` | :815 | 候选池转换 |
| `_convert_state_pool_cell(cell)` | :878 | 状态池转换 |
| `_convert_condition_cell(cell)` | :942 | 条件节点转换 |
| `_convert_decoration_cell(cell)` | :978 | 装饰节点转换 |
| `_convert_flow(flow)` | :1019 | 流（边）转换 |
| `convert_tdx_to_config(pool_meta)` | :1109 | PoolMetaModel → pool_config JSON |
| `_get_system_indicator(ntjindexno)` | :1182 | 系统指标查询 |
| `_get_formula_config(ntjindexno)` | :1191 | 公式配置查询 |

#### 6.2.4 _order 字段生成

`_order` 由设计者在转换期指定，编码拓扑执行意图。`_build_execution_order`
（compiler.py:479-493，现 `core/execution_module.py`）按此字段排序。

| 转换器 | _order 赋值位置 | 语义 |
|--------|----------------|------|
| 原 dzh.py | :1667, :1734, :1893 | 递增序号（按出现顺序） |
| 原 tdx.py | :2145, :3676, :3857 | `exec_order` 参数或递增序号 |

#### 6.2.5 dispatch.json 分派规则

dispatch.json 定义条件类型 → gateway 映射：

| condition_type | bit | gateway | 语义 |
|---------------|-----|---------|------|
| INDICATOR | 20 | `formula_eval` | 指标条件 |
| RANKING | 21 | `formula_eval` | 排序条件 |
| SECTOR_MEMBERSHIP | 19 | `sector_filter` | 板块成员 |
| REVERSE_TRANSFER | 18 | `formula_eval` | 反向转移 |
| CROSS_SECTION | 22 | `cross_section_eval` | 横向统计 |
| BASIC_CONDITION | 11 | `basic_filter` | 基本条件 |
| PASSTHROUGH | — | `pass_through` | 无条件直通 |

`gateway` → `FilterSpec.evaluator_type` 映射在 compiler.py:564-571（现 `core/execution_module.py`）编译期完成。

#### 6.2.6 辅助工具

| 函数/表 | 原位置 | 语义 |
|---------|------|------|
| `_get_compare_type(noperate)` | tdx.py:83 | 运算符编号→比较类型字符串 |
| `_make_tdx_cell(cell_type, **data)` | tdx.py:101 | 构造 TDX 单元格模型 |
| `_load_tdx_element_schemas(filename)` | tdx.py:219 | 加载元素模式定义 |
| `_load_tdx_period_map(filename)` | tdx.py:230 | 加载周期映射表 |
| `_get_element_int_fields(element)` | tdx.py:255 | 获取整型字段集合 |
| `_get_element_sector_type_map(element)` | tdx.py:261 | 获取板块类型映射 |
| `_get_tdx_type(cell)` | tdx.py:715 | 从单元格获取 TDX 类型 |
| `_get_dzh_cell_type(cell)` | tdx.py:742 | 从单元格获取 DZH 类型 |
| `_build_position(cell)` | tdx.py:756 | 构建位置信息 |
| `_safe_get(obj, attr, default)` | tdx.py:769 | 安全属性访问 |
| `_code_to_setcode(code)` | tdx.py:776 | 代码→板块代码转换 |

> 上述辅助函数现均位于 `converters.py`（原 `converters/_common.py` 的 helper 已合并至
> `core/import_export_module.py`，供 `converters.py` 反向引用）。

---

## 7. Executed 事件结构（原 SPEC-02 §6）

`_publish(bus, Executed(...))`（原 edge_executor.py:805-814，现 `core/execution_module.py`）：

```python
Executed(
    eid=ec.eid,
    sid=ec.sid,
    tid=ec.tid,
    entered=list(entered),
    exited=exited,
    target_cleared=target_cleared,
    mode=propagate_mode,
    details={                      # 仅 entered 非空时
        "actions": list(actions),
        "prices": dict(prices),     # _init_entry_trackers 返回的入池价格
        "timestamp": ts,
    },
)
```

I23：`DomainEvent(ENTER)` 已合并入 `Executed.details`，不再 per-code 发布独立事件。

### 7.1 callback 副作用表

原 edge_executor.py:248（现 `core/execution_module.py`）。`_run_callback` 按
`ActionSpec.target_pool_actions` 列表顺序执行副作用：

| action | 行为 | 写入目标 | TDX psatt 对应 |
|--------|------|---------|---------------|
| `bsavehis` | 实时增量写入历史文件 | 磁盘文件 | psatt.bsavehis |
| `bsavetoblock` | 写入用户自定义板块 | 板块文件 | psatt.bsavetoblock + blockfile |
| `bsound` | 播放声音 | 音频设备 | psatt.bsound + nsoundtype + soundfile |
| `btip` | 弹出提示 | UI | psatt.btip |

**DZH alert 位映射**：cell.attr bit24/27/28/29（popup/history/sound/flash）→ 转换为
`ActionSpec.callbacks` 列表。映射路径为 converter 职责，不在引擎内特判。

---

## 8. 路径映射表（Phase 7/8/9 后）

> 下表列出原 `specs/` 文档引用的所有源文件路径在 Phase 7/8/9 极致合并后的真实位置。
> 本文档正文中已将所有引用更新为现路径；下表保留旧→新对照，便于历史追溯。

### 8.1 core/ 目录合并

| 旧路径 | 新路径 | 合并来源/说明 |
|--------|--------|---------------|
| `core/compiler.py` | `core/execution_module.py` | SubTask 27.4：4 个 Execution 模块文件高内聚合并 |
| `core/edge_executor.py` | `core/execution_module.py` | 同上 |
| `core/time_util.py` | `core/execution_module.py` | 同上 |
| `core/edge_state.py` | `core/execution_module.py` | 同上 |
| `core/ttl_helper.py` | `core/execution_module.py` | SubTask 27.1：迁移至 ExecutionModule |
| `core/runtime.py` | `core/runtime_mode_module.py` | SubTask 27.7：runtime + replay + simulator 合并 |
| `core/replay.py` | `core/runtime_mode_module.py` | 同上 |
| `core/simulator.py` | `core/runtime_mode_module.py` | 同上 |
| `core/evaluators.py` | `core/screening_module.py` | Phase 8 SubTask 28.1：评估器层合并入 Screening |
| `core/statistics_module.py` | `core/monitoring_module.py` | SubTask 28.2：统计模块合并入 Monitoring |
| `core/event_panel.py` | `core/monitoring_module.py` | Monitoring 模块合并 |
| `core/snapshot_builder.py` | `core/monitoring_module.py` | 内联为 `_SnapshotBuilder` 私有类 |
| `core/value_extractor.py` | `core/formula_module.py` | 公式相关 4 文件合并 |
| `core/formula.py` | `core/formula_module.py` | 同上 |
| `core/formula_engine.py` | `core/formula_module.py` | 同上 |
| `core/formula_router.py` | `core/formula_module.py` | 同上 |
| `core/tick_source.py` | `core/tick_bar_module.py` | SubTask 27.2：tick/bar 3 文件合并；TickSource/MockDataSource 由 core/domain.py 提供 |
| `core/data_updater.py` | `core/tick_bar_module.py` | 同上 |
| `core/bar_composer.py` | `core/tick_bar_module.py` | 同上 |
| `core/trade_executor.py` | `core/trade_module.py` | SubTask 27.5：交易相关 2 文件合并 |
| `core/_compat.py` | `core/engine.py` | SubTask 27.1：CompiledExpression 迁移至 engine.py 顶部 |
| `core/_market_utils.py` | `core/domain.py` | SubTask 27.1：市场代码工具迁移至 domain.py 的 tick_source section |
| `core/domain/base.py` | `core/domain.py` | SubTask 29.1：domain 包 6 文件合并为单一模块 |
| `core/domain/nodes.py` | `core/domain.py` | 同上 |
| `core/domain/edges.py` | `core/domain.py` | 同上 |
| `core/domain/specs.py` | `core/domain.py` | 同上 |
| `core/domain/evaluators.py` | `core/domain.py` | 同上（Evaluator 接口层；评估器实现仍在 screening_module.py） |
| `core/domain/tick_source.py` | `core/domain.py` | 同上 |

### 8.2 services/ 目录合并

| 旧路径 | 新路径 | 合并来源/说明 |
|--------|--------|---------------|
| `services/storage.py` | `services/storage.py` | （保留；DB 同步服务层合并入此） |
| `services/db_sync_service.py` | `services/storage.py` | DB 同步服务合并入 storage |
| `services/data.py` | `services/data.py` | （保留；candidate_pool 合并入此） |
| `services/candidate_pool.py` | `services/data.py` | 候选池解析合并入 data |
| `services/pool_validator.py` | （已删除） | Phase 9 中删除 |
| `services/formula_cache.py` | `core/formula_module.py` | SubTask 28.4：FormulaCache 合并入 formula_module |
| `services/hot_reload.py` | `core/table_engine.py` | SubTask 28.6：HotReloadManager 合并入 table_engine |
| `services/minute_aggregator.py` | `core/tick_bar_module.py` | 分钟聚合器合并入 tick_bar_module |
| `services/trading_service.py` | `core/trade_module.py` | 交易服务合并入 trade_module |
| `services/providers/akshare.py` | `services/providers.py` | providers 包 7 文件合并为单一模块 |
| `services/providers/dfcf.py` | `services/providers.py` | 同上 |
| `services/providers/hqchart.py` | `services/providers.py` | 同上 |
| `services/providers/local_file.py` | `services/providers.py` | 同上 |
| `services/providers/mock.py` | `services/providers.py` | 同上 |
| `services/providers/tq.py` | `services/providers.py` | 同上 |
| `services/providers/__init__.py` | `services/providers.py` | DataSourceProvider 基类 + DataSourceManager + 公共工具层 |
| `services/providers/_common.py` | `services/providers.py` | 二进制公式解码 / 周期代码映射 / 缓存等公共工具 |

### 8.3 converters/ 目录合并

| 旧路径 | 新路径 | 合并来源/说明 |
|--------|--------|---------------|
| `converters/dzh.py` | `converters.py` | SubTask 29.3：converters 包合并为单文件（项目根） |
| `converters/tdx.py` | `converters.py` | 同上 |
| `converters/json_xml.py` | `converters.py` | 同上 |
| `converters/_common.py` | `core/import_export_module.py` | SubTask 27.8：helper 合并至 import_export_module，converters.py 反向引用 |

### 8.4 api/ 目录合并

| 旧路径 | 新路径 | 合并来源/说明 |
|--------|--------|---------------|
| `api/pool_api.py` | `api.py` | API 模块合并为单文件 |
| `api/system_api.py` | `api.py` | 同上 |

### 8.5 native/ 目录合并

| 旧路径 | 新路径 | 合并来源/说明 |
|--------|--------|---------------|
| `native/pipeline.py` | `native/builtins.py` | pipeline 合并入 builtins |
| `native/matchers.py` | `native/validators.py` | matchers 合并入 validators |

### 8.6 web/ 目录合并

| 旧路径 | 新路径 | 合并来源/说明 |
|--------|--------|---------------|
| `web/js/_convert_table_driven.py` | `web/ui_renderer.py` | UI 渲染辅助合并 |
| `web/js/_reindent.py` | `web/ui_renderer.py` | 同上 |

### 8.7 scripts/ 目录合并

| 旧路径 | 新路径 | 合并来源/说明 |
|--------|--------|---------------|
| `scripts/analyze_dzh.py` | `scripts/dev_tools.py` | 6 个开发工具脚本合并 |
| `scripts/config_tools.py` | `scripts/dev_tools.py` | 同上 |
| `scripts/decode_formulas.py` | `scripts/dev_tools.py` | 同上 |
| `scripts/debug_formula.py` | `scripts/dev_tools.py` | 同上 |
| `scripts/merge_config_tables.py` | `scripts/dev_tools.py` | 同上 |
| `scripts/xml_tools.py` | `scripts/dev_tools.py` | 同上 |
| `scripts/e2e_verify.py` | `scripts/verify_tools.py` | 6 个验证/运行脚本合并 |
| `scripts/manual_mcp_verify.py` | `scripts/verify_tools.py` | 同上 |
| `scripts/manual_mcp_verify_sim.py` | `scripts/verify_tools.py` | 同上 |
| `scripts/run_sim_verify.py` | `scripts/verify_tools.py` | 同上 |
| `scripts/import_target_pool_100.py` | `scripts/verify_tools.py` | 同上 |
| `scripts/run_server.py` | `scripts/verify_tools.py` | 同上 |

### 8.8 路径映射统计

- core/ 目录：28 条映射（含 domain 包 6 文件 + 22 个独立文件）
- services/ 目录：16 条映射（含 providers 包 8 文件）
- converters/ 目录：4 条映射
- api/ 目录：2 条映射
- native/ 目录：2 条映射
- web/ 目录：2 条映射
- scripts/ 目录：12 条映射
- **合计：66 条路径映射**

---

## 9. 阅读顺序与依赖图（原 INDEX.md）

### 9.1 阅读顺序

```
§1 架构契约（全局契约，先读）
  ├→ §6.1 格式映射（数据如何流入系统）
  ├→ §6.2 转换层（转换层细节）
  └→ §3.1 EdgeExecutor 执行管线（执行管线核心）
       ├→ §3.4 脏状态与触发（何时触发）
       ├→ §3.5 公式引擎与过滤器求值（过滤求值）
       ├→ §3.6 传播模式与股票转移（股票如何转移）
       ├→ §3.7 TTL 系统超时管理（超时管理）
       ├→ §2 事件流契约（事件流）
       └→ §4 信号与回调（副作用与输出）
            ├→ §5.8 PoolState 与持久化（状态层）
            └→ §5.1-5.7 引擎编排（编排层）
```

### 9.2 依赖图

```
                    §1 架构契约
                   /    |       \
              §6.1 FMT  §3.1 EDGE  §5.8 POOL
              /   \     |   \      |
          §6.2 CNV  §3.4 DIRTY §3.5 FLT  |
                    |   \     |    |
                §3.6 PROP §3.7 TTL  §5.1-5.7 ENG
                    |              |
                §2 EVENT ←→ §4 CALLBACK
```

### 9.3 已验证关键行号交叉索引

| 机制 | 首次定义 | 引用点 | 现位置 |
|------|---------|--------|------|
| `_make_edge_action` | §6.1.11 | §3.4.3 | `core/execution_module.py`（原 compiler.py:839） |
| `build_timed_event_specs` | §6.1.11 | §3.4.3 | `core/execution_module.py`（原 compiler.py:1051-1067） |
| `_build_execution_order` | §6.1.3 | §3.4.6 | `core/execution_module.py`（原 compiler.py:479-493） |
| `_PROPAGATE_STRATEGIES` | §3.6.3 | §3.1.1 | `core/execution_module.py`（原 edge_executor.py:696-701） |
| `mark_node_dirty` | §3.4.1 | §3.1.1/§3.6 | `core/runtime_mode_module.py`（原 runtime.py:246） |
| `clear_dirty` | §3.4.1 | §5.1 | `core/runtime_mode_module.py`（原 runtime.py:258） |
| `_init_entry_trackers` | §3.7.1 | §3.1.1 | `core/execution_module.py`（原 edge_executor.py:146） |
| `unregister_ttl` | §3.7.1 | §3.6.5 | `core/execution_module.py`（原 edge_executor.py:930） |
| `EVENT_SIGNAL` 订阅 | §4.3 | §5.6 | `core/engine.py:624` |

### 9.4 修正历史

| 日期 | 修正内容 | 影响章节 |
|------|---------|---------|
| 2026-07-14 | 无条件边**注册** TimedEventSpec（非"不注册"），compiler 无 edge_type 过滤 | §1, §6.1, §3.4 |
| 2026-07-14 | `_make_edge_action` 行号 839（非 845） | §6.1, §3.4 |
| 2026-07-14 | 源策略函数 `_src_delete`（非 `_src_remove`），目标策略 `_tgt_merge`（非 `_tgt_append`） | §3.6 |
| 2026-07-14 | overwrite 模式下源节点也标脏（`_src_delete` 调用 `mark_node_dirty`） | §3.1, §3.4 |
| 2026-07-14 | `_build_execution_order` 按 `_order` 字段排序（设计者指定，非自动拓扑排序） | §3.1, §3.4 |
| 2026-07-14 | I23 合并：bsound/btip 等不再产生独立事件，信息合并入 Executed.details | §2, §4 |
| 2026-07-14 | ENTER 仍经 `_push_event` 发布为 DomainEvent（与 Executed 并存） | §2 |
| 2026-07-14 | `_build_execution_order` 行号修正 479-497 → 479-493（实际止于 493） | §3.1, §3.4, §6.2 |
| 2026-07-14 | §3.4:20 "compiler facade" → "TTL interval/endtime action 闭包"（行号 912/:995 正确） | §3.4 |
| 2026-07-14 | §4.4 RANK_CHANGED 完整验证：三重守卫 + thresholds[2] 索引错位 latent fragility | §4 |
| 2026-07-18 | Phase 10 SubTask 30.2：13 个 specs/*.md 归并为本文档，所有路径更新为 Phase 9 后真实路径 | 全文 |

---

> **文档结束**。本归并文档作为 `specs/` 目录的历史快照与架构契约参考，后续架构变更应直接
> 更新本文档或在其基础上派生新文档。代码实现若与本文档冲突，以代码为准。
