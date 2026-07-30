# Tasks

本规范按「架构工程师（实施）→ 评审工程师（验证）」迭代流程分 6 阶段实施，覆盖 11 个 eventtest 失败修复 + 3 个运行时验证 harness + MetaDispatcher 统一 + 残余同构收敛 + metatest v5 20 维 + RULES 111-115。**第五层洞察（数据驱动分派 DDD 元统一）**：三核 Dispatcher 自身（EventBus / ConfigStore）也遵循 `register(key, value) → store → dispatch(key) → retrieve` 分派原语，统一为 `MetaDispatcher` 抽象基类，EventDriver 因 heapq 时序特化保持独立。本次迭代核心 = 闭合 v4 残留 4 类缺口（同构 Dispatcher / eventtest 失败 / 运行时验证沙箱 / 残余同构），非新建机制、非拆分。

## 阶段 1：修复 11 个 eventtest 真实失败（P0，阻塞 Task 34.3）

### 架构工程师任务

- [x] Task 1: 变更 F1 — 跨模块 import 纪律修复（5 个真实 bug）
  - [x] SubTask 1.1: 审查 `core/execution_module.py:84 from .table_engine import get_global_config_store, load_config_table` 与 `:87 from .screening_module import _apply_noperate_mode_series, _resolve_series_lookback`，定位调用站点
  - [x] SubTask 1.2: 审查 `core/screening_module.py:65 from .table_engine import load_config_table`，定位 `load_config_table` 实际调用站点
  - [x] SubTask 1.3: 审查 `core/formula_module.py:50 from .table_engine import load_config_table`，定位 `load_config_table` 实际调用站点
  - [x] SubTask 1.4: 在 `converters/_common.py` 或 `core/domain.py`（白名单基础模块）下沉 `load_config_table(name, default=None)` 函数（封装 `get_global_config_store().get_table(name, default)` 调用）
  - [x] SubTask 1.5: `core/execution_module.py` / `core/screening_module.py` / `core/formula_module.py` 的 `load_config_table` 调用改为下沉路径（`from converters._common import load_config_table` 或 `from .domain import load_config_table`）
  - [x] SubTask 1.6: `core/execution_module.py:87` 的 `_apply_noperate_mode_series` / `_resolve_series_lookback` 改为构造函数依赖注入（`__init__` 接收 `screening_helper` 参数）或下沉到 `converters/_common.py`
  - [x] SubTask 1.7: 同步处理 `core/runtime_mode_module.py:44` / `core/monitoring_module.py:47` / `core/trade_module.py:48` 的 `from core.table_engine import get_global_config_store`（改为 `from .domain import get_global_config_store` 下沉，或保留为允许的基础模块引用）
  - [x] SubTask 1.8: Grep `from\s+\.table_engine\s+import|from\s+core\.table_engine\s+import` 在 7 个业务模块（execution/screening/formula/runtime_mode/trade/tick_bar/monitoring）= 0（或仅 `get_global_config_store` 经白名单下沉）
  - [x] SubTask 1.9: Grep `from\s+\.screening_module\s+import` 在 `core/execution_module.py` = 0

- [x] Task 2: 变更 F2 — EventDriver action 签名同步（2 个测试侧 bug）
  - [x] SubTask 2.1: 审查 `eventtest/test_positive_eventdriver.py` 的 `action(params)` / `ttl_action(params)` 签名
  - [x] SubTask 2.2: 审查 `core/execution_module.py` 的 `EventDriver.fire_due` 调用 `spec.action(spec.params, fire_time)` 的实际签名
  - [x] SubTask 2.3: `eventtest/test_positive_eventdriver.py` 的所有 `def action(params)` 改为 `def action(params, fire_time=None)`
  - [x] SubTask 2.4: `eventtest/test_positive_eventdriver.py` 的所有 `def ttl_action(params)` 改为 `def ttl_action(params, fire_time=None)`
  - [x] SubTask 2.5: 运行 `python -m pytest eventtest/test_positive_eventdriver.py -v`，验证 0 失败

- [x] Task 3: 变更 F3 — 运行时集成失败定位与修复（4 个集成失败）
  - [x] SubTask 3.1: 运行 `python -m eventtest.run_eventtest 2>&1 | grep -E "FAILED|ERROR"`，定位 4 个集成失败的具体测试名
  - [x] SubTask 3.2: 对每个失败测试，审查 `eventtest/test_positive_condition_activation.py` 与 `eventtest/test_integration_sim_full_flow.py` 的失败堆栈
  - [x] SubTask 3.3: 定位 `FormulaEvaluated` 事件链断点（execution_module → formula_module → EventBus.publish(FormulaEvaluated)）
  - [x] SubTask 3.4: 定位 `StockFiltered` 事件链断点（screening_module → EventBus.publish(StockFiltered) → execution_module 订阅）
  - [x] SubTask 3.5: 修复 condition-activation 公式求值失败（确保 `FormulaEvaluated.error` 字段传播正确）
  - [x] SubTask 3.6: 修复 sim_full_flow 事件链断裂（确保 `StockFiltered` 订阅者正确触发 `TransferExecuted`）
  - [x] SubTask 3.7: 运行 `python -m eventtest.run_eventtest`，验证退出码 0

## 阶段 2：运行时验证 harness（P1，闭合 34.12/34.13/34.15 沙箱缺口）

### 架构工程师任务

- [x] Task 4: 变更 R1 — in-process replay 验证
  - [x] SubTask 4.1: 审查 `metatest/conftest.py` 的 `fastapi_client` fixture 与 `fire_due` 手动推进模式
  - [x] SubTask 4.2: 新建 `metatest/test_runtime_replay_heapq.py`，import `TestClient` from `fastapi.testclient` 与 `PoolEngine` 装配
  - [x] SubTask 4.3: 定义 `test_replay_step_driven_by_heapq`：装配 `PoolEngine` → 调 `engine.set_mode("replay")` → `engine.play()` → 断言 `EventDriver._heap` 含 step TimedEventSpec
  - [x] SubTask 4.4: 定义 `test_replay_fire_due_triggers_edge_fired`：调 `engine._components["event_driver"].fire_due(now + interval)` → 断言 `EdgeFired` 事件由 heapq 调度触发（无 `time.sleep`）
  - [x] SubTask 4.5: 定义 `test_replay_pause_cancels_heapq`：调 `engine.pause()` → 断言 `EventDriver._heap` 中 step spec 已 cancel
  - [x] SubTask 4.6: 运行 `python -m pytest metatest/test_runtime_replay_heapq.py -v`，验证 0 失败

- [x] Task 5: 变更 R2 — in-process simulation 验证
  - [x] SubTask 5.1: 新建 `metatest/test_runtime_simulation_heapq.py`
  - [x] SubTask 5.2: 定义 `test_simulation_start_auto_registers_heapq`：调 `engine.set_mode("simulation")` → `engine.start_auto()` → 断言 `EventDriver._heap` 含 sim_step TimedEventSpec
  - [x] SubTask 5.3: 定义 `test_simulation_fire_due_advances_auto_step`：调 `fire_due(now + 1.0/speed)` → 断言 `SimulationStep` 事件触发 + heapq 续程（无 `asyncio.sleep` 步进）
  - [x] SubTask 5.4: 定义 `test_simulation_stop_cancels_heapq`：调 `engine.stop_auto()` → 断言 sim_step spec 已 cancel
  - [x] SubTask 5.5: 运行 `python -m pytest metatest/test_runtime_simulation_heapq.py -v`，验证 0 失败

- [x] Task 6: 变更 R3 — in-process 三模式切换验证
  - [x] SubTask 6.1: 新建 `metatest/test_runtime_mode_switch.py`
  - [x] SubTask 6.2: 定义 `test_mode_switch_emits_mode_changed`：`set_mode("simulation")` → `set_mode("replay")` → 断言 `ModeChanged` 事件发布 2 次
  - [x] SubTask 6.3: 定义 `test_mode_switch_preserves_event_chain`：切换模式后注入 tick → 断言 `TickReceived → BarComposed → FormulaEvaluated → StockFiltered` 链路完整
  - [x] SubTask 6.4: 定义 `test_mode_switch_simulation_to_realtime`：切换到实盘模式 → 断言 `EventDriver` heapq 不再驱动步进（由实时 tick 推送）
  - [x] SubTask 6.5: 运行 `python -m pytest metatest/test_runtime_mode_switch.py -v`，验证 0 失败

## 阶段 3：第五层洞察 — MetaDispatcher 统一（P2，极致本质运行时最后拼图）

### 架构工程师任务

- [x] Task 7: 变更 M1 — 引入 `MetaDispatcher` 抽象基类
  - [x] SubTask 7.1: 在 `core/event_bus.py` 顶部（`EventBus` 类定义前）定义 `MetaDispatcher` 抽象基类
  - [x] SubTask 7.2: `MetaDispatcher` 含抽象属性 `_store`（子类覆盖存储结构）+ 抽象方法 `_dispatch_impl(self, key, *args, **kwargs)`（子类覆盖派发语义）
  - [x] SubTask 7.3: `MetaDispatcher` 含模板方法 `register(self, key, value)` → `self._store[key] = value` + `dispatch(self, key, *args, **kwargs)` → `return self._dispatch_impl(key, *args, **kwargs)`
  - [x] SubTask 7.4: `MetaDispatcher` 文档化「register-store-dispatch 元模式投影」+「EventBus 扇出子类 / ConfigStore 查找子类 / EventDriver 时序特化（独立）」三态
  - [x] SubTask 7.5: 净增行数 ≤ 30 行（基类骨架，非拆分）

- [x] Task 8: 变更 M2 — EventBus 继承 MetaDispatcher
  - [x] SubTask 8.1: `class EventBus(MetaDispatcher):` 继承
  - [x] SubTask 8.2: `EventBus.__init__` 中 `self._subscribers: Dict[str, List[Callable]] = {}` 作为 `_store` 的特化形态（保留 `_subscribers` 名以向后兼容，或 alias `_store = _subscribers`）
  - [x] SubTask 8.3: `EventBus.subscribe(event_type, handler)` 委托 `self.register(self._event_type_name(event_type), handler)`（多订阅者 append 语义，覆盖 `_store` 默认赋值）
  - [x] SubTask 8.4: `EventBus._dispatch_impl(self, key, event)` 覆盖为遍历 `self._subscribers.get(key, [])` + `self._any_subscribers` 调用 handler（扇出 + 副作用）
  - [x] SubTask 8.5: `EventBus.publish(event)` 内部调用 `self._dispatch_impl(type(event).__name__, event)` + 事件日志写入（保留 `_events.append` + `_total_published` 逻辑）
  - [x] SubTask 8.6: 验证 `EventBus` 所有现有调用站点（`subscribe` / `publish` / `get_events` / `subscribe_any`）行为不变
  - [x] SubTask 8.7: 运行 `python -m pytest metatest/ -k event -x`，验证 EventBus 相关测试全绿

- [x] Task 9: 变更 M3 — ConfigStore 继承 MetaDispatcher
  - [x] SubTask 9.1: `core/table_engine.py` 中 `class ConfigStoreBase(MetaDispatcher):` 继承（ConfigStoreBase 已是 ConfigStore 的基类，调整继承链为 `ConfigStore → ConfigStoreBase → MetaDispatcher`）
  - [x] SubTask 9.2: `ConfigStore.__init__` 中 `self._tables: Dict[str, Dict] = {}` 作为 `_store` 的特化形态
  - [x] SubTask 9.3: `ConfigStore._load_table(name, path)` 委托 `self.register(name, data)`（覆盖 `_store` 默认赋值为 dict 合并语义）
  - [x] SubTask 9.4: `ConfigStore._dispatch_impl(self, key, *args, **kwargs)` 覆盖为 `return self._tables.get(key)`（纯查找无副作用）
  - [x] SubTask 9.5: `ConfigStore.get_table(name, default=None)` 内部调用 `self._dispatch_impl(name)` 或 `return self._tables.get(name, default)`（保留 default 参数语义）
  - [x] SubTask 9.6: 验证 `ConfigStore` 所有现有调用站点（`get_table` / `load_all` / `_load_table`）行为不变
  - [x] SubTask 9.7: 运行 `python -m pytest metatest/ -k config -x`，验证 ConfigStore 相关测试全绿

- [x] Task 10: 变更 M4 — EventDriver 保持独立 + 文档化
  - [x] SubTask 10.1: `core/execution_module.py` 中 `EventDriver` 类不继承 `MetaDispatcher`（保持独立）
  - [x] SubTask 10.2: `EventDriver` 类 docstring 新增「时序特化 Dispatcher：heapq 优先队列 + fire_time 排序 + 自动续程（periodic reschedule），与 MetaDispatcher 的 register-store-dispatch 元模式投影不同构，保持独立」说明
  - [x] SubTask 10.3: 验证 `EventDriver` 的 `add_spec` / `fire_due` / `cancel` / `schedule_periodic` 行为不变

## 阶段 4：残余同构收敛（P3，代码质量提升）

### 架构工程师任务

- [x] Task 11: 变更 S1 — `services/storage.py` 8 个 handler 表驱动
  - [x] SubTask 11.1: 审查 `services/storage.py` 中所有 `def _on_*` handler 方法（预期 8 个）
  - [x] SubTask 11.2: 识别同构骨架：8 个 handler 均为「事件 → 提取字段 → 持久化」模式
  - [x] SubTask 11.3: 定义 `_PERSIST_HANDLERS: Dict[type, Tuple[str, ...]]` 表，映射事件类型到字段提取器（如 `{EdgeFired: ("eid", "ts"), Signal: ("code", "action", "price"), ...}`）
  - [x] SubTask 11.4: 定义通用 `_persist_event(self, event)` 方法：查表 → 提取字段 → 持久化
  - [x] SubTask 11.5: 8 个 `_on_*` handler 改为 `lambda event: self._persist_event(event)` 或删除（由 `_SUBSCRIPTIONS` 表 + `_persist_event` 自动派发）
  - [x] SubTask 11.6: Grep `def _on_\w+\b` 在 `services/storage.py` ≤ 2（仅 `_persist_event` + 必要 override）
  - [x] SubTask 11.7: 净减行数 ≥ 40 行

- [x] Task 12: 变更 S2 — `execution_module.py` 9 个 handler 部分合并
  - [x] SubTask 12.1: 审查 `core/execution_module.py` 中所有 `def _on_*` handler 方法（预期 9 个）
  - [x] SubTask 12.2: 识别同构 handler 组：`_on_tick_received` / `_on_bar_composed` 共享「更新状态 → 触发评估」骨架
  - [x] SubTask 12.3: 识别 `_on_edge_fired` / `_on_ttl_due` 共享「执行副作用 → 发布 Signal」骨架
  - [x] SubTask 12.4: 合并同构组为表驱动（`_EVAL_HANDLERS` / `_SIGNAL_HANDLERS` 表 + 通用 `_eval_event` / `_signal_event` 方法）
  - [x] SubTask 12.5: 非同构 handler 保留（如 `_on_mode_changed` 含特殊模式切换逻辑）
  - [x] SubTask 12.6: Grep `def _on_\w+\b` 在 `core/execution_module.py` ≤ 5（合并后剩余非同构 handler）
  - [x] SubTask 12.7: 净减行数 ≥ 30 行

- [x] Task 13: 变更 S3 — 202 个 try 块收敛 safe_cast
  - [x] SubTask 13.1: Grep `try:\s*\n\s*float\(|try:\s*\n\s*int\(` 在 `core/runtime_mode_module.py`（预期 34 处）
  - [x] SubTask 13.2: Grep `try:\s*\n\s*float\(|try:\s*\n\s*int\(` 在 `core/table_engine.py`（预期 26 处）
  - [x] SubTask 13.3: Grep `try:\s*\n\s*float\(|try:\s*\n\s*int\(` 在 `core/formula_module.py`（预期 25 处）
  - [x] SubTask 13.4: 每处 `try: float(x) except (TypeError, ValueError): default` 改为 `safe_float(x, default)`（从 `converters/_common` import）
  - [x] SubTask 13.5: 每处 `try: int(x) except (TypeError, ValueError): default` 改为 `safe_int(x, default)`
  - [x] SubTask 13.6: 业务必要的 try 块保留（如 JSON 解析、文件 IO、网络调用）
  - [x] SubTask 13.7: Grep `try:\s*\n\s*float\(|try:\s*\n\s*int\(` 在 3 模块 ≤ 10（仅业务必要保留）
  - [x] SubTask 13.8: 净减行数 ≥ 150 行

- [x] Task 14: 变更 S4 — `services/providers.py` 收敛审查
  - [x] SubTask 14.1: 审查 `services/providers.py`（8686 行，全库最大文件）整体结构
  - [x] SubTask 14.2: 识别死代码（未被引用的函数 / 类 / 常量）
  - [x] SubTask 14.3: 识别同构模式（重复的 TQ SDK 调用包装 / 行情字段提取 / 订单状态映射）
  - [x] SubTask 14.4: 识别冗余注释（v1/v2 历史遗留、被注释掉的代码块、过度详细的日志描述）
  - [x] SubTask 14.5: 合并同构 TQ SDK 调用为表驱动（`_TQ_API_SPECS` 表 + 通用 `_call_tq_api(spec_key, ...)`）
  - [x] SubTask 14.6: 删除死代码 + 冗余注释（保留业务必要的注释）
  - [x] SubTask 14.7: 净减行数 ≥ 200 行

## 阶段 5：metatest v5 重建（20 维量化评分）

### 架构工程师任务

- [x] Task 15: 变更 T1 — scoring.py v5（20 维）
  - [x] SubTask 15.1: 审查 `metatest/scoring.py` 当前 v4 16 维权重分配
  - [x] SubTask 15.2: v4 16 维权重等比降权至 80%（每维 × 0.8）
  - [x] SubTask 15.3: 新增第 17 维 `dispatcher_isomorphism`（权重 5%）：MetaDispatcher 基类存在 + EventBus/ConfigStore 继承 + EventDriver 独立 + 公共骨架行数占比 ≥ 60%
  - [x] SubTask 15.4: 新增第 18 维 `runtime_verification`（权重 5%）：3 个 in-process 测试通过率
  - [x] SubTask 15.5: 新增第 19 维 `eventtest_regression`（权重 5%）：eventtest 退出码 0（全绿）
  - [x] SubTask 15.6: 新增第 20 维 `cross_module_import_discipline`（权重 5%）：Grep 8 处违规模式 0 匹配
  - [x] SubTask 15.7: 20 维权重和 = 100%，PASS 条件：总分 ≥ 95 且 20 维均 ≥ 80

- [x] Task 16: 变更 T2 — runner.py v5 采集
  - [x] SubTask 16.1: `metatest/runner.py` 新增 `dispatcher_isomorphism` 数据采集：检查 `MetaDispatcher` 基类 + `EventBus(MetaDispatcher)` + `ConfigStore(MetaDispatcher)` 继承
  - [x] SubTask 16.2: 新增 `runtime_verification` 数据采集：运行 `test_runtime_replay_heapq.py` / `test_runtime_simulation_heapq.py` / `test_runtime_mode_switch.py` 并采集通过率
  - [x] SubTask 16.3: 新增 `eventtest_regression` 数据采集：运行 `python -m eventtest.run_eventtest` 并采集退出码
  - [x] SubTask 16.4: 新增 `cross_module_import_discipline` 数据采集：Grep 8 处违规模式并计数
  - [x] SubTask 16.5: `test_results` 字典新增 4 维字段，填入 `report.json`
  - [x] SubTask 16.6: `meta_unification` 字段新增 `meta_dispatcher_exists` / `eventbus_inherits_meta` / `configstore_inherits_meta` / `eventdriver_independent` 4 字段

- [x] Task 17: 变更 T3 — 正反合测试 v5
  - [x] SubTask 17.1: 新建 `metatest/test_positive_dispatcher_isomorphism.py`：断言 `MetaDispatcher` 基类存在 + `EventBus` / `ConfigStore` 继承 + `EventDriver` 独立 + 公共骨架行数占比 ≥ 60%
  - [x] SubTask 17.2: 新建 `metatest/test_positive_runtime_verification.py`：调用 3 个 runtime 测试并断言全绿
  - [x] SubTask 17.3: 新建 `metatest/test_negative_cross_module_import.py`：Grep 8 处违规模式并断言 0 匹配
  - [x] SubTask 17.4: 升级 `metatest/test_positive_oop_inheritance.py`：新增 MetaDispatcher 继承断言
  - [x] SubTask 17.5: 升级 `metatest/test_negative_polling.py`：保留 v4 12 处轮询检查 + 新增 MetaDispatcher 违规检查（自造第四核 Dispatcher）
  - [x] SubTask 17.6: 运行 `python -m pytest metatest/ -x`，验证全绿

- [x] Task 18: 变更 T4 — metatest/README.md v5 文档
  - [x] SubTask 18.1: 更新 `metatest/README.md` 文档化 20 维评分（v4 16 维 + v5 4 新维）
  - [x] SubTask 18.2: 文档化 MetaDispatcher 统一（第五层洞察）
  - [x] SubTask 18.3: 文档化运行时验证 harness（3 个 in-process 测试）
  - [x] SubTask 18.4: 文档化跨模块 import 纪律（8 处违规模式）

## 阶段 6：RULES.md 111-115 + 全量回归

### 架构工程师任务

- [x] Task 19: 变更 D1 — RULES.md 111-115
  - [x] SubTask 19.1: 第 111 条 — MetaDispatcher 统一：EventBus + ConfigStore 必须继承 `MetaDispatcher` 抽象基类，EventDriver 因 heapq 时序特化保持独立
  - [x] SubTask 19.2: 第 112 条 — 跨模块 import 纪律：7 个业务模块禁止直接 import `table_engine` / `screening_module`，必须经依赖注入或下沉到白名单模块（`converters/_common` / `domain`）
  - [x] SubTask 19.3: 第 113 条 — EventDriver action 签名：所有 `TimedEventSpec.action` 必须接受 `action(params, fire_time=None)` 签名
  - [x] SubTask 19.4: 第 114 条 — 运行时验证 harness：replay/simulation/mode-switch 必须有 in-process 测试，用 `fire_due(now)` 推进时间，禁止启动服务/浏览器
  - [x] SubTask 19.5: 第 115 条 — handler 表驱动：≥ 3 个同构 `_on_*` handler 必须收敛为 `_HANDLERS` 表 + 1 个通用方法

- [x] Task 20: 变更 D2 — 全量回归
  - [x] SubTask 20.1: 运行 `python -m pytest metatest/ -x` 全量测试通过
  - [x] SubTask 20.2: 运行 `python -m metatest.runner` 总分 ≥ 95 且 20 维均 ≥ 80
  - [x] SubTask 20.3: 运行 `python -m eventtest.run_eventtest` 退出码 0（全绿）
  - [x] SubTask 20.4: 核心模块总行数 ≤ 20,000（v4 基线 20,325 - v5 净减 ≥ 350）
  - [x] SubTask 20.5: Grep RULES 111-115 对应 5 条违规模式 0 匹配
  - [x] SubTask 20.6: Grep 8 处跨模块 import 违规模式 0 匹配
  - [x] SubTask 20.7: Grep 12 处轮询模式 0 匹配（v4 已达标，v5 保持）
  - [x] SubTask 20.8: MetaDispatcher 继承结构正确（EventBus + ConfigStore 继承，EventDriver 独立）
  - [x] SubTask 20.9: 3 个 in-process 运行时验证测试全绿
  - [x] SubTask 20.10: essence_ratio ≥ 16%（v4 基线 24,000 → v5 目标 ≤ 20,000）
  - [x] SubTask 20.11: 启动 replay 验证步进由 EventDriver heapq 调度（in-process 测试替代）
  - [x] SubTask 20.12: 启动 simulation 验证 auto-step 由 EventDriver heapq 调度（in-process 测试替代）
  - [x] SubTask 20.13: 三模式切换后事件链路正常（in-process 测试替代）
  - [x] SubTask 20.14: DZH↔TDX 双格式互转保真验证（v4 已达标，v5 保持）
  - [x] SubTask 20.15: 无任何变更净增行数（essence_ratio 维度反测试通过）

# Task Dependencies

- Task 2 / Task 3 依赖 Task 1（先修 import 违规，再修 action 签名，再修运行时集成）
- Task 4 / Task 5 / Task 6 依赖 Task 1 / Task 2 / Task 3（运行时验证 harness 需基于已修复的 EventBus / EventDriver）
- Task 7 / Task 8 / Task 9 / Task 10 可并行（MetaDispatcher 统一四步，但 M1 必须先于 M2/M3）
- Task 11 / Task 12 / Task 13 / Task 14 可并行（残余同构收敛四组独立）
- Task 15 / Task 16 / Task 17 / Task 18 依赖 Task 7-14（metatest v5 需基于已完成的代码变更）
- Task 19 / Task 20 依赖 Task 15-18（RULES + 全量回归需基于已完成的 metatest v5）
