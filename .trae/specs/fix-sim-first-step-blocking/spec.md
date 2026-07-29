# Fix Simulation First Step Blocking Spec

## Why

当前 `/api/sim/start` 已可快速返回 session_id，但首次 `/api/sim/control step` 仍会阻塞 Uvicorn 约 6.4 秒。根本原因是 `RuntimeSimulator.astep()` 首次调用 `_aensure_mode_state()` 时，`PoolEngine.run_mode()` 虽是 `async def`，但内部无 `await` 点，实际同步执行重量级初始化，导致事件循环被长时间占用。本规格要求通过后台预热与线程池卸载，让 `/api/sim/start` 返回后即在后台完成初始化，首次正式 step 直接使用已初始化状态，不阻塞 Uvicorn。

## What Changes

- 在 `/api/sim/start` handler 中创建 simulator 后，启动后台任务 `asyncio.create_task(_warm_simulator(session_id))` 执行 `simulator.astep(0.0)`，完成重量级 `run_mode` 初始化。
- 在 `RuntimeSimulator._aensure_mode_state()` 中将 `run_mode` 的同步计算逻辑通过 `asyncio.to_thread()` 放到线程池执行，避免占用 Uvicorn 事件循环。
- 在 session 中维护 `init_event` / `init_task` / `init_error` 状态，支持查询初始化进度与异常。
- 在 `/api/sim/control step` handler 中检查初始化状态：未完成时返回 `code=102` "initializing"，完成失败时返回错误，已完成时直接执行步进。
- 若性能分析显示 `EventDriver.fire_due` 或 K 线合成仍是瓶颈，针对性优化事件处理与 bar 合成逻辑，确保单步响应 < 500ms。
- 保留 eventtest 既有行为，不破坏 G1-G6 事件驱动核心架构。

## Impact

- Affected specs: `specify-stockpool-event-flow` Task 14 Playwright 手动验证（依赖首次 step 可快速响应）
- Affected code:
  - `app.py`: `/api/sim/start`, `/api/sim/control`
  - `core/runtime_mode_module.py`: `RuntimeSimulator._aensure_mode_state()`, `astep()`
  - `core/engine.py`: `PoolEngine.__init__`, `_run_tick_body()`（必要时增加性能日志）
  - `core/execution_module.py`: `EventDriver.fire_due()`（必要时优化）
  - `core/tick_bar_module.py`: `BarComposer.on_tick()`（必要时优化）

## ADDED Requirements

### Requirement: Background Pre-initialization

The system SHALL complete simulator heavyweight initialization in the background after `/api/sim/start` returns.

#### Scenario: Start returns immediately
- **WHEN** the frontend calls `/api/sim/start`
- **THEN** the endpoint returns `session_id` within 100ms
- **AND** a background task begins executing `simulator.astep(0.0)`

#### Scenario: First step uses warmed state
- **GIVEN** the background pre-initialization has completed
- **WHEN** the frontend calls `/api/sim/control` with action `step`
- **THEN** the endpoint completes within 500ms using the already-initialized state

#### Scenario: Step during initialization
- **GIVEN** the background pre-initialization is still running
- **WHEN** the frontend calls `/api/sim/control` with action `step`
- **THEN** the endpoint returns code `102` with status `"initializing"` and a clear message

#### Scenario: Initialization failure reported
- **GIVEN** the background pre-initialization raised an exception
- **WHEN** the frontend calls `/api/sim/control` with action `step`
- **THEN** the endpoint returns code `1` with the initialization error message

### Requirement: Non-blocking run_mode Initialization

The system SHALL execute `PoolEngine.run_mode()` without blocking the Uvicorn event loop.

#### Scenario: run_mode in thread pool
- **WHEN** `RuntimeSimulator._aensure_mode_state()` needs to call `self._engine.run_mode()`
- **THEN** the actual computation runs in a worker thread via `asyncio.to_thread()`
- **AND** the event loop remains free to handle concurrent requests

### Requirement: Concurrent API Availability

The system SHALL keep other API endpoints responsive during simulation initialization.

#### Scenario: Other APIs during first step
- **WHEN** `/api/sim/control step` is blocked or still initializing
- **AND** a concurrent request to `/api/pools` or `/api/health` arrives
- **THEN** the concurrent request completes within normal response time without timeout

## MODIFIED Requirements

### Requirement: Simulation Session State

Existing simulation session map SHALL be extended with initialization tracking fields.

- `init_event`: `asyncio.Event()` set when warm-up finishes
- `init_task`: the `asyncio.Task` object for the background warm-up
- `init_error`: optional string captured if warm-up raises an exception

## REMOVED Requirements

None.
