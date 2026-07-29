# Checklist

## 架构工程师检查点（实施前自检）

- [ ] 已阅读 RULES.md 第 2/16/24/69 条并理解表驱动 + 事件驱动约束
- [ ] 已阅读 `core/tick_bar_module.py:181-196`（_publish_tick_changed）和 `:358-388`（_publish_bar_changed）确认两者同构
- [ ] 已阅读 `core/execution_module.py:2369-2468`（EdgeExecutor.run）确认 9 步过程式编排
- [x] 已阅读 `core/monitoring_module.py:130-640` 确认 30+ 个 `_on_` 方法同构
- [ ] 已确认 Task 1/2/3 无交叉文件冲突，可并行实施

## 评审工程师检查点（DataChanged 发布器统一）

- [ ] `publish_data_changed` 函数在 `core/tick_bar_module.py` 中定义为模块级函数
- [ ] 函数签名包含 `bus, state, source, codes, ts, data=None, period=None, bar_hash=""` 参数
- [ ] 函数在 `codes` 为空时 `return`，不发布事件
- [ ] 函数在 `bus` 无效（`not is_event_bus(bus)`）时 `return`
- [ ] `source="tick"` 时发布 `DataChanged(source="tick", codes, ts, data=data_payload)`
- [ ] `source="bar"` 时发布 `DataChanged(source="bar", codes, ts, period=period)` + 对每个 code 发布 `BarComposed`
- [ ] `DataUpdater.apply_data` 中调用 `publish_data_changed` 替代原 `self._publish_tick_changed`
- [ ] `BarComposer.on_tick` 中调用 `publish_data_changed` 替代原 `_publish_bar_changed`
- [ ] Grep `_publish_tick_changed` 在代码库中零匹配（注释除外）
- [ ] Grep `_publish_bar_changed` 在代码库中零匹配（注释除外）
- [ ] Grep `publish_data_changed` 在 `tick_bar_module.py` 中 ≥ 2 处调用

## 评审工程师检查点（EdgeExecutor 步骤表驱动化）

- [ ] `EdgeStep` Protocol 在 `core/schemas.py` 中定义，含 `run(spec, ctx) -> StepResult` 方法
- [ ] `StepResult` dataclass 在 `core/schemas.py` 中定义，含 `should_continue: bool` 和 `data: Optional[dict]`
- [ ] `StepSpec` 在 `core/schemas.py` 中定义，含 `step_name: str` 和 `enabled: bool = True`
- [ ] `CompiledSchedule` 新增 `steps: List[StepSpec]` 字段
- [ ] `Compiler.compile` 从 `edge_strategies.json:steps` 读取步骤序列
- [ ] `GateStep` 类实现 `EdgeStep`，内部委托现有 `_gate` 逻辑
- [ ] `FilterStep` 类实现 `EdgeStep`，内部委托 `_filter` + FormulaEvaluated/StockFiltered 发布
- [ ] `PropagateStep` 类实现 `EdgeStep`，内部委托 `_propagate` + Executed/TransferExecuted 发布
- [ ] `TTLStep` 类实现 `EdgeStep`，含 `_init_entry_trackers` + `register_ttl_spec` + `node_ttl_spec`
- [ ] `CallbackStep` 类实现 `EdgeStep`，内部委托 `_run_callback`
- [ ] `STEP_REGISTRY` dict 注册 5 个 Step 实例
- [ ] `EdgeExecutor.run` 方法体改为 `for step_spec in schedule.steps: step = STEP_REGISTRY[step_spec.step_name]; result = step.run(ctx); if not result.should_continue: break`
- [ ] `edge_strategies.json` 中 `steps` 数组包含 5 个步骤
- [ ] Grep `EdgeExecutor.run` 方法体中不再有过程式 `_gate(`/`_filter(`/`_propagate(`/`_run_callback(`/`_init_entry_trackers(` 调用
- [ ] Grep `STEP_REGISTRY` 包含 5 个 key（gate/filter/propagate/ttl/callback）

## 评审工程师检查点（MonitoringModule 统一化）

- [x] `EVENT_RECORD_ADAPTERS` dict 在 `core/monitoring_module.py` 中定义
- [x] dict 包含 ≥ 15 个事件类型 adapter（TickReceived/DataChanged/BarComposed/FormulaEvaluated/StockFiltered/EdgeFired/Executed/TransferExecuted/Signal/OrderPlaced/OrderFilled/PositionUpdated/TTLDue/TickDue/ModeChanged 等）
- [x] 每个 adapter 为 `Callable[[Any], dict]`，返回 `{ts, event_type, code, details}` 格式
- [x] `event_to_record(event)` 函数查 `EVENT_RECORD_ADAPTERS` 表，未命中用默认 adapter
- [x] 默认 adapter 提取 `{ts: event.ts, event_type: type(event).__name__, code: getattr(event, 'code', ''), details: getattr(event, 'details', {})}`
- [x] `_on_any_event(self, event)` 方法调用 `event_to_record` + `self._add_to_event_list`
- [x] `MonitoringModule.subscribe` 方法改为 `self.bus.subscribe_any(self._on_any_event)`
- [x] Grep `def _on_` 在 `monitoring_module.py` 中匹配数 ≤ 2（`_on_any_event` + 可能的保留方法）
- [x] `_add_to_event_list` 保留 `_events`/`_pending` 双列表写入逻辑

## 回归验证检查点

- [ ] `python -m pytest tests/ -x` 全量测试通过
- [ ] eventtest 171 项全部通过（退出码 0）
- [ ] 仿真模式启动后 TickReceived → DataChanged(tick) → BarComposed → DataChanged(bar) → EdgeFired → FormulaEvaluated → StockFiltered → TransferExecuted → Signal → OrderPlaced → OrderFilled → PositionUpdated 事件链完整
- [ ] 事件面板矩阵视图 9 分类正常显示
- [ ] 事件面板散点视图分类/同行布局正常
- [ ] 三模式（仿真/回放/实盘）切换后事件链路正常
- [ ] RULES.md 新增第 81/82/83 条
- [ ] DESIGN.md 补充元模式统一章节

## 禁止项检查

- [ ] 禁止在 `publish_data_changed` 中调用 `time_at(state)` 重新计算 ts（ts 必须由调用方传入）
- [ ] 禁止在 `EdgeStep.run` 中直接访问 `EdgeExecutor` 的非 spec/state/bus 属性（必须通过 ctx 传入）
- [x] 禁止在 `event_to_record` 中硬编码事件类型 if/elif 链（必须查 `EVENT_RECORD_ADAPTERS` dict）
- [ ] 禁止删除 `_gate`/`_filter`/`_propagate` 的表驱动分派表（`_CXTYPE_POST_GATES`/`_FILTER_EVALUATORS`/`_PROPAGATE_STRATEGIES` 保持不变）
- [x] 禁止在 `EVENT_RECORD_ADAPTERS` 中使用 if/elif 分支（每个 adapter 是独立函数）
