# Checklist

- [ ] `/api/sim/start` 在创建 simulator 后启动后台预热任务，并在 100ms 内返回 session_id
- [ ] `_warm_simulator` 协程正确捕获异常，异常信息写入 session `init_error`，并在完成时设置 `init_event`
- [ ] session 结构包含 `simulator`、`speed`、`pool_id`、`init_event`、`init_task`、`init_error`
- [ ] `RuntimeSimulator._aensure_mode_state()` 使用 `asyncio.to_thread()` 将 `run_mode` 计算放到线程池
- [ ] `PoolEngine.__init__` 已注入 `asyncio.Lock()` 用于串行化仿真 heavyweight 初始化
- [ ] 首次 `astep(0.0)` 不推进虚拟时间，仅完成 mode_state 初始化
- [ ] `/api/sim/control` 的 `step` 分支在 `init_event` 未 set 时返回 `code=102, status="initializing"`
- [ ] `/api/sim/control` 的 `step` 分支在 `init_error` 非空时返回 `code=1` 与错误信息
- [ ] `/api/sim/control` 的 `step` 分支在初始化完成后调用 `simulator.astep(delta)` 并正常返回
- [ ] 首次正式 step 响应时间 < 500ms
- [ ] 在初始化或 step 执行期间，其他 API（如 `/api/pools`）可在正常时间内响应，不超时
- [ ] `EventDriver.fire_due` 与 `BarComposer.on_tick` 性能瓶颈已识别并在需要时优化
- [ ] 关键路径保留性能日志，可量化验证各阶段耗时
- [ ] `python -m eventtest.run_eventtest` 全部通过，退出码 0
- [ ] 未破坏 G1-G6 事件驱动核心架构，未引入 `asyncio.sleep` 或轮询
