# Tasks

## 架构工程师任务（实施）

- [x] Task 1: 统一 DataChanged 发布器
  - [x] SubTask 1.1: 在 `core/tick_bar_module.py` 中创建模块级 `publish_data_changed(bus, state, source, codes, ts, data=None, period=None, bar_hash="")` 函数，合并 `_publish_tick_changed` 和 `_publish_bar_changed` 逻辑
  - [x] SubTask 1.2: `publish_data_changed` 内部处理 BarComposed 事件发布（当 source="bar" 时对每个 code 发布 BarComposed）
  - [x] SubTask 1.3: 将 `DataUpdater._publish_tick_changed` 方法删除，`apply_data` 中的调用改为 `publish_data_changed(self.bus, self.state, "tick", codes, ts, data=data_payload)`
  - [x] SubTask 1.4: 将 `_publish_bar_changed` 函数删除，`BarComposer.on_tick` 中的调用改为 `publish_data_changed(self.bus, self.state, "bar", codes, ts, period=period, bar_hash=period_hash)`
  - [x] SubTask 1.5: 验证 `is_event_bus` 检查在 `publish_data_changed` 内部统一处理，调用方不再需要重复检查

- [x] Task 2: EdgeExecutor 步骤表驱动化
  - [x] SubTask 2.1: 在 `core/schemas.py` 中定义 `EdgeStep` Protocol（`run(spec, ctx) -> StepResult`）和 `StepResult` dataclass（`should_continue: bool = True, data: dict = None`）
  - [x] SubTask 2.2: 在 `core/schemas.py` 中定义 `StepSpec`（`step_name: str, enabled: bool = True`）
  - [x] SubTask 2.3: 在 `CompiledSchedule` 中新增 `steps: List[StepSpec]` 字段
  - [x] SubTask 2.4: 在 `Compiler.compile` 中从 `edge_strategies.json:steps` 读取步骤序列填充 `schedule.steps`
  - [x] SubTask 2.5: 将 `_gate` 包装为 `GateStep` 类（实现 `EdgeStep` Protocol），内部委托现有 `_gate` 逻辑
  - [x] SubTask 2.6: 将 `_filter` 包装为 `FilterStep` 类，内部委托现有 `_filter` + FormulaEvaluated/StockFiltered 发布
  - [x] SubTask 2.7: 将 `_propagate` 包装为 `PropagateStep` 类，内部委托现有 `_propagate` + Executed/TransferExecuted 发布
  - [x] SubTask 2.8: 将 `_run_callback` 包装为 `CallbackStep` 类
  - [x] SubTask 2.9: 将 TTL 注册逻辑包装为 `TTLStep` 类（含 `_init_entry_trackers` + `register_ttl_spec` + `node_ttl_spec`）
  - [x] SubTask 2.10: 将 `EdgeExecutor.run` 改为 `for step_spec in schedule.steps: step = STEP_REGISTRY[step_spec.step_name]; result = step.run(ctx); if not result.should_continue: break`
  - [x] SubTask 2.11: 定义 `STEP_REGISTRY: Dict[str, EdgeStep]` 注册表，在模块级注册 5 个 Step
  - [x] SubTask 2.12: 在 `edge_strategies.json` 中添加 `steps` 数组：`[{step_name:"gate"},{step_name:"filter"},{step_name:"propagate"},{step_name:"ttl"},{step_name:"callback"}]`

- [x] Task 3: MonitoringModule 事件记录统一化
  - [x] SubTask 3.1: 在 `core/monitoring_module.py` 中定义 `EVENT_RECORD_ADAPTERS: Dict[str, Callable[[Any], dict]]` dict
  - [x] SubTask 3.2: 为现有 30+ 个事件类型编写 adapter 函数（从原 `_on_xxx` 方法体提取 details 构造逻辑），注册到 `EVENT_RECORD_ADAPTERS`
  - [x] SubTask 3.3: 编写 `event_to_record(event) -> dict` 函数：查 `EVENT_RECORD_ADAPTERS` 表，未命中用默认 adapter
  - [x] SubTask 3.4: 编写 `_on_any_event(self, event)` 方法：调用 `event_to_record` + `self._add_to_event_list`
  - [x] SubTask 3.5: 将 `MonitoringModule.subscribe` 方法改为 `self.bus.subscribe_any(self._on_any_event)`
  - [x] SubTask 3.6: 删除 30+ 个独立 `_on_xxx` 方法
  - [x] SubTask 3.7: 确保 `_add_to_event_list` 保留 `_events`/`_pending` 双列表写入逻辑不变

- [x] Task 4: 更新 RULES.md 与 DESIGN 文档
  - [x] SubTask 4.1: 在 `RULES.md` 中新增第 81 条「DataChanged 发布统一为 `publish_data_changed` 单一函数」
  - [x] SubTask 4.2: 在 `RULES.md` 中新增第 82 条「EdgeExecutor 步骤序列表驱动化，新增步骤=加 JSON 条目」
  - [x] SubTask 4.3: 在 `RULES.md` 中新增第 83 条「MonitoringModule 事件记录表驱动化，新增事件适配=加 dict 条目」
  - [x] SubTask 4.4: 在 `DESIGN.md` 中补充元模式统一章节

## 评审工程师任务（验证）

- [x] Task 5: 验证 DataChanged 发布器统一
  - [x] SubTask 5.1: Grep 确认 `_publish_tick_changed` 和 `_publish_bar_changed` 在代码库中零匹配
  - [x] SubTask 5.2: Grep 确认 `publish_data_changed` 调用点在 tick_bar_module.py 中 ≥ 2 处
  - [x] SubTask 5.3: 验证 `publish_data_changed` 在 codes 为空时跳过发布
  - [x] SubTask 5.4: 验证 `publish_data_changed` 在 bus 无效时跳过发布
  - [ ] SubTask 5.5: 启动仿真验证 TickReceived → DataChanged(tick) → BarComposed → DataChanged(bar) 事件链完整

- [x] Task 6: 验证 EdgeExecutor 步骤表驱动化
  - [x] SubTask 6.1: Grep 确认 `EdgeExecutor.run` 方法体中不再有 `_gate(`/`_filter(`/`_propagate(`/`_run_callback(`/`_init_entry_trackers(` 过程式调用
  - [x] SubTask 6.2: 验证 `STEP_REGISTRY` 包含 5 个 Step（gate/filter/propagate/ttl/callback）
  - [x] SubTask 6.3: 验证 `CompiledSchedule.steps` 字段由 `edge_strategies.json:steps` 填充
  - [x] SubTask 6.4: 验证 GateStep 返回 `should_continue=False` 时后续步骤不执行
  - [ ] SubTask 6.5: 启动仿真验证边执行完整链路：gate → filter → FormulaEvaluated → StockFiltered → propagate → Executed → TransferExecuted → ttl → callback

- [x] Task 7: 验证 MonitoringModule 统一化
  - [x] SubTask 7.1: Grep 确认 `MonitoringModule` 中 `_on_` 方法数 ≤ 2（仅 `_on_any_event` + 可能的 `_on_domain_event` 如果语义特殊）
  - [x] SubTask 7.2: 验证 `EVENT_RECORD_ADAPTERS` dict 包含 ≥ 15 个事件类型 adapter
  - [x] SubTask 7.3: 验证 `subscribe_any` 在 `MonitoringModule.subscribe` 中调用
  - [x] SubTask 7.4: 验证未注册事件类型使用默认 adapter 正确记录
  - [ ] SubTask 7.5: 启动仿真验证事件面板所有 9 分类事件正常显示

- [ ] Task 8: 回归验证
  - [ ] SubTask 8.1: 运行 `python -m pytest tests/ -x` 全量测试通过
  - [ ] SubTask 8.2: 运行 eventtest 171 项全部通过（退出码 0）
  - [ ] SubTask 8.3: 启动仿真浏览器验证事件面板矩阵/散点视图正常
  - [ ] SubTask 8.4: 验证三模式（仿真/回放/实盘）切换后事件链路正常

# Task Dependencies

- Task 2 依赖 Task 1（步骤表驱动化前需统一 DataChanged 发布器，因 FilterStep/PropagateStep 内部发布事件）
- Task 3 独立于 Task 1/2，可并行
- Task 4 依赖 Task 1/2/3 全部完成
- Task 5 依赖 Task 1
- Task 6 依赖 Task 2
- Task 7 依赖 Task 3
- Task 8 依赖 Task 5/6/7 全部完成
- Task 1 和 Task 2 和 Task 3 可并行实施（无交叉文件冲突：Task 1 改 tick_bar_module.py，Task 2 改 execution_module.py + schemas.py，Task 3 改 monitoring_module.py）
