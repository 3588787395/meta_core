# 元模式本质收敛 v5：Dispatcher 同构统一 + 运行时验证闭环 + 残余同构收敛 Spec

## Why

v4 已达 98.02/100 PASS（16 维全 ≥ 80），核心代码 20,327 行，essence_ratio 15.30%，三原语覆盖率 100%。但经架构工程师第五层洞察，确认仍有 4 类根因级缺口未闭合，且其中包含 **11 个 eventtest 真实失败**（阻塞 Task 34.3）与 **3 个运行时验证沙箱缺口**（阻塞 34.12/34.13/34.15）：

1. **三核 Dispatcher 自身同构未统一（第五层洞察）**：v4 确立「Code = Data + Dispatcher」公理与运行时三核 Dispatcher（EventBus / EventDriver / ConfigStore），但经逐行对比，**EventBus 与 ConfigStore 共享「register(key, value) → store[key] = value → dispatch(key) → retrieve」骨架**（前者多订阅者扇出 + 副作用回调，后者纯查找无副作用），可统一为 `MetaDispatcher` 抽象基类。EventDriver 因 heapq 时序优先队列 + 自动续程语义特化，保持独立。这是「极致本质运行时」的最后一块拼图——Dispatcher 自身也遵循分派原语。

2. **11 个 eventtest 真实失败暴露跨模块 import 违规 + action 签名不一致**：
   - **5 个真实 bug**：`execution_module.py:84 from .table_engine import`、`:87 from .screening_module import`、`screening_module.py:65`、`formula_module.py:50` 同样违规 import `table_engine`——业务模块直接 import 业务模块违反零引用约束，需改为依赖注入或将 `load_config_table` 下沉到白名单模块（`domain` / `schemas` / `converters_common`）。
   - **2 个测试侧 bug**：`eventtest/test_positive_eventdriver.py` 的 `action(params)` 签名与生产代码 `fire_due` 调用 `action(params, fire_time)` 不一致，需同步为 `def action(params, fire_time=None)`。
   - **4 个运行时集成失败**：condition-activation 公式求值 / sim_full_flow 事件链，需运行时调试定位。

3. **3 个运行时验证沙箱缺口（34.12/34.13/34.15）**：v4 因沙箱无法启动服务保留未勾选。经调查，`metatest/conftest.py` 已提供 `fastapi_client`（TestClient in-process）+ `fire_due` 手动推进模式，可创建轻量 in-process 测试验证 replay/simulation heapq 调度，无需浏览器/uvicorn/asyncio loop。

4. **残余同构模式 v4 未覆盖**：
   - **55 个 `_on_*` handler 方法体仍各自独立**（注册已收敛到 `_SUBSCRIPTIONS` 表，但方法体未抽公共骨架）——`services/storage.py` 8 个 handler 均为「事件 → 持久化」模式可表驱动，`execution_module.py` 9 个 handler 部分可合并。
   - **202 个 try 块**（`runtime_mode_module.py` 34 个、`table_engine.py` 26 个、`formula_module.py` 25 个）中大量数值转换仍手写 `try/except`，可收敛到 `safe_cast`。
   - `services/providers.py`（8686 行，全库最大文件）未做任何收敛审查。

本次迭代（v5）将：(1) 统一 EventBus + ConfigStore 为 `MetaDispatcher` 基类（第五层洞察）；(2) 修复 11 个 eventtest 失败（跨模块 import 纪律 + action 签名 + 运行时集成）；(3) 创建 in-process 运行时验证 harness 闭合沙箱缺口；(4) 收敛 55 个 handler 方法体 + 202 个 try 块残余同构；(5) metatest v5 从 16 维扩展到 20 维（新增 `runtime_verification` / `dispatcher_isomorphism` / `eventtest_regression` / `cross_module_import_discipline`）；(6) RULES.md 111-115 + 全量回归。

**第五层洞察（本次迭代新增的根因收敛）**：v4 确立「三原语同构于运行时三核 Dispatcher」，v5 进一步洞察「**三核 Dispatcher 自身也遵循分派原语**」——EventBus 的 `subscribe/publish`、ConfigStore 的 `load_table/get_table` 都是 `register(key, value) → store → dispatch(key) → retrieve` 元模式的投影。统一为 `MetaDispatcher` 抽象基类后，极致本质运行时只有一条公理 `Code = Data + MetaDispatcher`，三核 Dispatcher 成为 `MetaDispatcher` 的三个特化子类。这闭合了「Dispatcher 自身也必须收敛到原语」的最后一环。

## What Changes

### 阶段 1：修复 11 个 eventtest 真实失败（P0，阻塞 Task 34.3）

- **变更 F1 — 跨模块 import 纪律**：`execution_module.py:84,87` / `screening_module.py:65` / `formula_module.py:50` 删除业务模块直接 import `table_engine` / `screening_module`，改为依赖注入（构造函数注入 `config_store`）或将 `load_config_table` 下沉到白名单模块 `converters_common` / `domain`。**BREAKING**：`execution_module` / `screening_module` / `formula_module` 的 `load_config_table` 调用站点需改为注入或下沉路径。
- **变更 F2 — EventDriver action 签名同步**：`eventtest/test_positive_eventdriver.py` 的 `action(params)` / `ttl_action(params)` 改为 `def action(params, fire_time=None)`，与生产代码 `fire_due` 调用 `spec.action(spec.params, fire_time)` 一致。
- **变更 F3 — 运行时集成失败定位与修复**：4 个 condition-activation / sim_full_flow 失败需运行时调试，定位 `FormulaEvaluated` / `StockFiltered` 事件链断点并修复。

### 阶段 2：运行时验证 harness（P1，闭合 34.12/34.13/34.15 沙箱缺口）

- **变更 R1 — in-process replay 验证**：新建 `metatest/test_runtime_replay_heapq.py`，用 `fastapi_client` fixture 装配 `PoolEngine`，直接调 `engine._components["event_driver"].fire_due(now)` 推进时间，断言 `EdgeFired` / `TTLDue` / `Signal` 事件链由 heapq 调度触发，无 `time.sleep`。
- **变更 R2 — in-process simulation 验证**：新建 `metatest/test_runtime_simulation_heapq.py`，验证 `start_auto()` 入 heapq + `fire_due` 推进 auto-step，无 `asyncio.sleep` 步进。
- **变更 R3 — in-process 三模式切换验证**：新建 `metatest/test_runtime_mode_switch.py`，验证 仿真↔回放↔实盘 切换后事件链路正常（`ModeChanged` 事件驱动）。

### 阶段 3：第五层洞察 — MetaDispatcher 统一（P2，极致本质运行时最后拼图）

- **变更 M1 — 引入 `MetaDispatcher` 抽象基类**：在 `core/event_bus.py` 定义 `MetaDispatcher` 基类，含 `register(key, value) → store` + `dispatch(key) → retrieve` 模板方法 + `_store` 抽象存储 + `_dispatch_impl` 抽象 hook。
- **变更 M2 — EventBus 继承 MetaDispatcher**：`EventBus(MetaDispatcher)` 覆盖 `_store = Dict[str, List[Callable]]` + `_dispatch_impl` 遍历调用 handlers（多订阅者扇出 + 副作用）。
- **变更 M3 — ConfigStore 继承 MetaDispatcher**：`ConfigStore(MetaDispatcher)` 覆盖 `_store = Dict[str, Dict]` + `_dispatch_impl` 直接返回 value（纯查找无副作用）。`ConfigStoreBase` 继承链调整。
- **变更 M4 — EventDriver 保持独立**：因 heapq 时序优先队列 + 自动续程语义特化，`EventDriver` 不继承 `MetaDispatcher`，但文档化其作为「时序特化 Dispatcher」的角色。

### 阶段 4：残余同构收敛（P3，代码质量提升）

- **变更 S1 — `services/storage.py` 8 个 handler 表驱动**：定义 `_PERSIST_HANDLERS: Dict[type, str]` 映射事件类型到字段提取器，8 个 `_on_*` handler 收敛为 1 个通用 `_persist_event(event)` + 表查找。
- **变更 S2 — `execution_module.py` 9 个 handler 部分合并**：识别同构 handler（如 `_on_tick_received` / `_on_bar_composed` 共享「更新状态 → 触发评估」骨架），合并为表驱动。
- **变更 S3 — 202 个 try 块收敛 safe_cast**：`runtime_mode_module.py`（34）、`table_engine.py`（26）、`formula_module.py`（25）中数值转换 `try/except` 改为 `safe_float` / `safe_int`，目标净减 ≥ 150 行。
- **变更 S4 — `services/providers.py` 收敛审查**：8686 行最大文件，识别死代码 / 同构模式 / 冗余注释，目标净减 ≥ 200 行。

### 阶段 5：metatest v5 重建（20 维量化评分）

- **变更 T1 — scoring.py v5（20 维）**：v4 16 维基础上新增 `runtime_verification` 5% + `dispatcher_isomorphism` 5% + `eventtest_regression` 5% + `cross_module_import_discipline` 5%，v4 维度等比降权，权重和 = 100%。
- **变更 T2 — runner.py v5**：采集 in-process 运行时验证结果 + MetaDispatcher 继承结构 + eventtest 通过率 + 跨模块 import 违规 Grep，填入 test_results。
- **变更 T3 — 正反合测试 v5**：新增 `test_positive_dispatcher_isomorphism.py` + `test_positive_runtime_verification.py` + `test_negative_cross_module_import.py` + 升级原有测试。
- **变更 T4 — metatest/README.md v5**：文档化 20 维评分 + MetaDispatcher 统一 + 运行时验证 harness。

### 阶段 6：RULES.md 111-115 + 全量回归

- **变更 D1 — RULES.md 111-115**：第 111 条 MetaDispatcher 统一 / 112 条跨模块 import 纪律 / 113 条 EventDriver action 签名 / 114 条运行时验证 harness / 115 条 handler 表驱动。
- **变更 D2 — 全量回归**：metatest v5 ≥ 95 且 20 维均 ≥ 80 + eventtest 退出码 0 + 运行时验证全绿。

## Impact

- Affected specs: `converge-meta-essence-v4-oop-event-driven`（v4 基线，v5 继承其 16 维评分与三原语框架）
- Affected code:
  - `core/event_bus.py` — 新增 `MetaDispatcher` 基类，`EventBus` 继承
  - `core/table_engine.py` — `ConfigStore` / `ConfigStoreBase` 继承 `MetaDispatcher`
  - `core/execution_module.py` — 修复 import 违规 + handler 合并 + EventDriver 文档化
  - `core/screening_module.py` / `core/formula_module.py` — 修复 import 违规
  - `services/storage.py` — 8 handler 表驱动收敛
  - `services/providers.py` — 收敛审查
  - `core/runtime_mode_module.py` / `core/formula_module.py` — try 块收敛 safe_cast
  - `eventtest/test_positive_eventdriver.py` — action 签名修复
  - `metatest/scoring.py` / `metatest/runner.py` — v5 20 维
  - `metatest/test_runtime_*.py` — 新建运行时验证 harness
  - `metatest/test_positive_dispatcher_isomorphism.py` / `test_negative_cross_module_import.py` — 新建
  - `RULES.md` — 111-115 条

## ADDED Requirements

### Requirement: MetaDispatcher 抽象基类统一 EventBus 与 ConfigStore

The system SHALL provide a `MetaDispatcher` abstract base class in `core/event_bus.py` that captures the shared `register(key, value) → store → dispatch(key) → retrieve` skeleton. `EventBus` and `ConfigStore` SHALL inherit from `MetaDispatcher`, overriding only the storage structure and dispatch implementation. `EventDriver` SHALL remain independent due to its heapq time-sequencing specialization.

#### Scenario: EventBus inherits MetaDispatcher
- **WHEN** `EventBus` is defined
- **THEN** it inherits `MetaDispatcher` and overrides `_dispatch_impl` to fan out to multiple subscribers with side effects

#### Scenario: ConfigStore inherits MetaDispatcher
- **WHEN** `ConfigStore` is defined
- **THEN** it inherits `MetaDispatcher` and overrides `_dispatch_impl` to return the stored value directly (pure lookup, no side effects)

#### Scenario: EventDriver remains independent
- **WHEN** `EventDriver` is defined
- **THEN** it does NOT inherit `MetaDispatcher` (documented as time-specialized Dispatcher)

### Requirement: In-process runtime verification harness

The system SHALL provide in-process runtime verification tests that start the application via `TestClient`, trigger replay/simulation stepping via `EventDriver.fire_due(now)` manual advancement, and assert the event chain (EdgeFired / TTLDue / Signal / ModeChanged) is driven by heapq scheduling without `time.sleep` or `asyncio.sleep` stepping.

#### Scenario: Replay stepping verified via heapq
- **WHEN** `test_runtime_replay_heapq.py` runs
- **THEN** it assembles `PoolEngine`, calls `fire_due(now)` to advance time, and asserts `EdgeFired` events are triggered by heapq dispatch

#### Scenario: Simulation auto-step verified via heapq
- **WHEN** `test_runtime_simulation_heapq.py` runs
- **THEN** it verifies `start_auto()` registers a TimedEventSpec in heapq and `fire_due` advances auto-step

#### Scenario: Three-mode switch verified
- **WHEN** `test_runtime_mode_switch.py` runs
- **THEN** it verifies 仿真↔回放↔实盘 mode switch emits `ModeChanged` events and the event chain remains intact

### Requirement: Cross-module import discipline

The system SHALL enforce that business modules (`execution_module` / `screening_module` / `formula_module` / `runtime_mode_module` / `trade_module` / `tick_bar_module` / `monitoring_module`) do NOT directly import other business modules (`table_engine` / `screening_module`) at module level. Cross-module dependencies SHALL be resolved via dependency injection (constructor injection) or by sinking shared utilities (`load_config_table`) to whitelist modules (`converters_common` / `domain`).

#### Scenario: No business module imports table_engine
- **WHEN** Grep `from\s+\.table_engine\s+import|from\s+core\.table_engine\s+import` in `core/execution_module.py` / `core/screening_module.py` / `core/formula_module.py`
- **THEN** result is 0 matches (or only via dependency injection pattern)

### Requirement: EventDriver action signature consistency

The system SHALL enforce that all `TimedEventSpec.action` callables accept the signature `action(params, fire_time=None)` to match `EventDriver.fire_due`'s invocation `spec.action(spec.params, fire_time)`.

#### Scenario: Test action signatures match production
- **WHEN** `eventtest/test_positive_eventdriver.py` defines test actions
- **THEN** they use `def action(params, fire_time=None)` signature

### Requirement: Handler table-driven convergence

The system SHALL converge isomorphic `_on_*` event handler method bodies into table-driven dispatch where ≥ 3 handlers share the same skeleton. `services/storage.py` 8 persist handlers SHALL be unified into `_PERSIST_HANDLERS: Dict[type, str]` + 1 generic `_persist_event(event)`.

#### Scenario: storage.py handlers table-driven
- **WHEN** Grep `def _on_\w+\b` in `services/storage.py`
- **THEN** result is ≤ 2 (only the generic `_persist_event` + necessary overrides)

## MODIFIED Requirements

### Requirement: metatest scoring dimensions (v4 → v5)

The metatest scoring SHALL expand from 16 dimensions (v4) to 20 dimensions (v5). The 4 new dimensions are:
- `runtime_verification` (5%) — in-process replay/simulation/mode-switch heapq verification
- `dispatcher_isomorphism` (5%) — MetaDispatcher base class + EventBus/ConfigStore inheritance
- `eventtest_regression` (5%) — eventtest exit code 0 (all tests pass)
- `cross_module_import_discipline` (5%) — no business module direct import violations

The v4 16 dimensions SHALL be proportionally weight-reduced to sum to 80%, with the 4 new dimensions at 20% total. PASS condition remains: total ≥ 95 AND all 20 dimensions ≥ 80.

### Requirement: essence_ratio target (v5)

The `essence_ratio` target SHALL remain ≥ 12% (net line reduction / baseline × 100). The baseline SHALL be updated to v4's final state (20,327 lines for core/*.py + v4 services/*.py baseline). v5 SHALL achieve additional net reduction ≥ 350 lines (MetaDispatcher + handler table-driven + safe_cast convergence + providers.py audit).

## REMOVED Requirements

### Requirement: Sandbox-limited runtime verification (v4 34.12/34.13/34.15)
**Reason**: v5 introduces in-process runtime verification harness that closes the sandbox gap without requiring actual service startup or browser.
**Migration**: The 3 unchecked subtasks (34.12/34.13/34.15) are replaced by v5's `runtime_verification` dimension and `test_runtime_*.py` harness.

## 深层运行逻辑洞察：第五层 — Dispatcher 自身的元统一

### v4 回顾：三原语同构于运行时三核 Dispatcher

v4 确立公理 `Code = Data + Dispatcher`，三原语（时间/分派/继承）同构于运行时三核 Dispatcher：
- 时间原语 → `EventDriver`（heapq + fire_due）
- 分派原语 → `EventBus`（subscribe + publish 扇出）+ `ConfigStore`（load_table + get_table 查找）
- 继承原语 → `BasePoolConverter` / `_FieldedBase` / `ConfigStoreBase` / `_BaseModule` / `BarHashMixin` / `Step`

### v5 第五层洞察：三核 Dispatcher 自身也遵循分派原语

经逐行对比三核 Dispatcher 的注册-存储-派发骨架：

| Dispatcher | 注册方法 | 存储结构 | 派发方法 | 派发语义 |
|-----------|---------|---------|---------|---------|
| `EventBus` | `subscribe(type, handler)` | `Dict[str, List[Callable]]` | `publish(event)` | 1-to-N 扇出 + 副作用 |
| `ConfigStore` | `_load_table(name, path)` | `Dict[str, Dict]` | `get_table(name)` | 1-to-1 查找 + 无副作用 |
| `EventDriver` | `add_spec(spec, time)` | `List[tuple[float, int, spec]]` heapq | `fire_due(now)` | 1-to-1 时序弹出 + 续程 |

**洞察**：EventBus 与 ConfigStore 共享 `register(key, value) → store[key] = value → dispatch(key) → retrieve` 元模式：
- EventBus 的 `subscribe` = register，`_subscribers` = store，`publish` = dispatch（遍历调用）
- ConfigStore 的 `_load_table` = register，`_tables` = store，`get_table` = dispatch（直接返回）

两者的差异仅在 dispatch 语义（扇出 vs 查找）与副作用（有 vs 无），可统一为 `MetaDispatcher` 抽象基类 + 2 个 hook。

EventDriver 因 heapq 时序优先队列 + `fire_time` 排序 + 自动续程（periodic reschedule）语义高度特化，强行塞入 `MetaDispatcher` 会增加抽象税而非降低复杂度，保持独立。

**统一后的公理升级**：`Code = Data + MetaDispatcher`，三核 Dispatcher 成为 `MetaDispatcher` 的两个特化子类（EventBus 扇出子类 + ConfigStore 查找子类）+ 一个独立时序特化（EventDriver）。这闭合了「Dispatcher 自身也必须收敛到原语」的最后一环——极致本质运行时只有 `MetaDispatcher` 一个元模式。

### v5 量化评审（metatest v5 新增 4 维）

**`dispatcher_isomorphism`（Dispatcher 同构度，新增第 17 维，权重 5%）**：
- `MetaDispatcher` 基类存在
- `EventBus(MetaDispatcher)` 继承 + 覆盖 `_dispatch_impl`
- `ConfigStore(MetaDispatcher)` 继承 + 覆盖 `_dispatch_impl`
- `EventDriver` 独立（文档化时序特化）
- 公共骨架行数 / (公共 + 子类差异) ≥ 60%

**`runtime_verification`（运行时验证度，新增第 18 维，权重 5%）**：
- `test_runtime_replay_heapq.py` 通过（replay 步进由 heapq 调度）
- `test_runtime_simulation_heapq.py` 通过（simulation auto-step 由 heapq 调度）
- `test_runtime_mode_switch.py` 通过（三模式切换事件链正常）

**`eventtest_regression`（eventtest 回归度，新增第 19 维，权重 5%）**：
- `python -m eventtest.run_eventtest` 退出码 0（全绿）
- 失败数 = 0

**`cross_module_import_discipline`（跨模块 import 纪律，新增第 20 维，权重 5%）**：
- Grep `from\s+\.table_engine\s+import|from\s+core\.table_engine\s+import` 在 `core/execution_module.py` / `core/screening_module.py` / `core/formula_module.py` = 0（或仅依赖注入模式）
- Grep `from\s+\.screening_module\s+import` 在 `core/execution_module.py` = 0
