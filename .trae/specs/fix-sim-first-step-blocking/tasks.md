# Tasks

## 修复任务（架构工程师 → 评审工程师）

- [ ] Task 1: 确认当前阻塞根因与已做改动
  - [ ] SubTask 1.1: 读取 `app.py`、`core/runtime_mode_module.py`、`core/engine.py` 当前实现，确认已有后台预热 / 线程池改动
  - [ ] SubTask 1.2: 运行 `profile_step.py` 或 `test_sim_perf.py`，确认首次 step 耗时分布与当前瓶颈
  - [ ] SubTask 1.3: 确认 `EventDriver.fire_due` 与 `BarComposer.on_tick` 是否为剩余瓶颈
  - 评审：确认根因分析正确后方可进入 Task 2

- [ ] Task 2: 实现 `/api/sim/start` 后台预热初始化
  - [ ] SubTask 2.1: 在 `app.py` 添加 `_warm_simulator(session_id)` 协程，捕获异常并设置 `init_event`
  - [ ] SubTask 2.2: 在 `sim_start_session` 创建 simulator 后调用 `asyncio.create_task(_warm_simulator(session_id))`，并将 task 存入 session
  - [ ] SubTask 2.3: 确保 `/api/sim/start` 立即返回 session_id（<100ms）
  - 评审：验证 start 响应时间 <100ms

- [ ] Task 3: 实现 `_aensure_mode_state` 非阻塞初始化
  - [ ] SubTask 3.1: 在 `core/engine.py` 的 `PoolEngine.__init__` 中注入 `asyncio.Lock()`（如尚未存在）
  - [ ] SubTask 3.2: 在 `core/runtime_mode_module.py` 的 `_aensure_mode_state` 中使用 `asyncio.to_thread()` 运行 `run_mode` 同步计算
  - [ ] SubTask 3.3: 确保首次 `astep(0.0)` 不推进虚拟时间，仅完成初始化
  - 评审：验证 Uvicorn 在初始化期间可响应其他请求

- [ ] Task 4: 实现 `/api/sim/control` 初始化状态处理
  - [ ] SubTask 4.1: 在 `sim_control_session` 的 `step` 分支检查 `init_event.is_set()`
  - [ ] SubTask 4.2: 未初始化完成时返回 `code=102, status="initializing"`
  - [ ] SubTask 4.3: 初始化失败时返回 `code=1` 与 `init_error` 信息
  - [ ] SubTask 4.4: 正常完成后执行 `simulator.astep(delta)` 并返回结果
  - 评审：验证三种状态响应均正确

- [ ] Task 5: 优化首次/单次 step 事件处理性能（如分析确认需要）
  - [ ] SubTask 5.1: 若 `EventDriver.fire_due` 处理大量事件耗时过高，优化事件批量处理与优先级调度
  - [ ] SubTask 5.2: 若 `BarComposer.on_tick` 遍历 100 只股票合成多周期 K 线耗时过高，优化缓存与增量更新
  - [ ] SubTask 5.3: 添加/保留关键路径性能日志，确保可量化验证
  - 评审：验证 step 响应时间 <500ms

- [ ] Task 6: 本地 API 性能验证
  - [ ] SubTask 6.1: 启动后端 `python -m uvicorn app:app --host 127.0.0.1 --port 5000`
  - [ ] SubTask 6.2: 调用 `/api/sim/start`，记录响应时间
  - [ ] SubTask 6.3: 立即调用 `/api/sim/control step`，记录响应时间；必要时轮询等待初始化完成后再测正式 step
  - [ ] SubTask 6.4: 在首次 step / 初始化期间调用 `/api/pools` 或其他 API，确认不超时
  - 评审：验证性能指标满足规格

- [ ] Task 7: eventtest 回归验证
  - [ ] SubTask 7.1: 运行 `python -m eventtest.run_eventtest`
  - [ ] SubTask 7.2: 确认全部通过（171/171 或当前基线），退出码 0
  - 评审：验证无回归

# Task Dependencies

- [Task 2] depends on [Task 1]
- [Task 3] depends on [Task 1]
- [Task 4] depends on [Task 2, Task 3]
- [Task 5] depends on [Task 1]（可选，视性能分析结果）
- [Task 6] depends on [Task 4]
- [Task 7] depends on [Task 4, Task 5]
