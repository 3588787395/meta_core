# Tasks

本规范按「架构工程师（实施）→ 评审工程师（验证）」迭代流程分 3 阶段实施，闭合 v12 留下的唯一预存缺口（`runtime_verification` 0/100）。**第十三层洞察（检测器引用不存在目标是检测器自身缺陷）**：v5 声明创建 3 个 in-process 运行时验证测试文件但从未创建，`_collect_runtime_verification` 静默输出 0/3 达 8 个迭代（v5→v12）。v13 真正创建这 3 个文件 + 1 个正测试 + 检测器 fail-loud 硬化。**净代码收益**：约 +200 ~ +280 行（4 个新测试文件 +200~+260 + 检测器硬化 +20~+30），但闭合 8 个迭代的 0/100 维度缺口。

## 阶段 1：创建 3 个 in-process 运行时验证测试文件（v5 声明但从未创建）

### 架构工程师任务

- [ ] Task 1: 变更 R1 — 新建 `metatest/test_runtime_replay_heapq.py`
  - [ ] SubTask 1.1: Read `metatest/conftest.py` 或现有测试确认 `fastapi_client` / `engine` fixture 装配方式（PoolEngine 实例化 + EventBus + EventDriver 组件装配）
  - [ ] SubTask 1.2: Read `core/runtime_mode_module.py:1961` `start_auto` / `play` / `pause` / `set_mode` 方法确认 API 签名与 heapq 交互
  - [ ] SubTask 1.3: Read `core/execution_module.py:202` `fire_due` 方法确认 heapq 弹出 + 事件发布机制
  - [ ] SubTask 1.4: Read `core/event_bus.py` `EdgeFired` / `TTLDue` / `Signal` 事件类定义确认断言目标
  - [ ] SubTask 1.5: 新建 `metatest/test_runtime_replay_heapq.py`，实现：`set_mode("replay")` → `play()` → 断言 `event_driver._heap` 含 step `TimedEventSpec`；`fire_due(now+interval)` 触发 `EdgeFired`（用 EventBus 订阅计数或事件列表断言）；`pause()` 后 `_cancelled` 含 spec id
  - [ ] SubTask 1.6: 确保测试无 `time.sleep` / `asyncio.sleep` 步进 / 启动服务（用 `fire_due` 手动推进时间）
  - [ ] SubTask 1.7: `python -m pytest metatest/test_runtime_replay_heapq.py -v` 退出码 0

- [ ] Task 2: 变更 R2 — 新建 `metatest/test_runtime_simulation_heapq.py`
  - [ ] SubTask 2.1: Read `core/runtime_mode_module.py:1961-1986` `start_auto` / `_sim_step_action` / `stop_auto` 确认 sim_step TimedEventSpec 注册与 cancel 机制
  - [ ] SubTask 2.2: 新建 `metatest/test_runtime_simulation_heapq.py`，实现：`set_mode("simulation")` → `start_auto()` → 断言 `event_driver._heap` 含 sim_step `TimedEventSpec`；`fire_due(now+1.0/speed)` 推进 auto-step（`_sim_step_action` 回调执行，step 计数 +1）；`stop_auto()` 后 sim_step spec 被 cancel
  - [ ] SubTask 2.3: 确保测试无 `asyncio.sleep` 步进
  - [ ] SubTask 2.4: `python -m pytest metatest/test_runtime_simulation_heapq.py -v` 退出码 0

- [ ] Task 3: 变更 R3 — 新建 `metatest/test_runtime_mode_switch.py`
  - [ ] SubTask 3.1: Read `core/runtime_mode_module.py:2180-2198` `switch_mode` 确认 `ModeChanged` 事件发布机制
  - [ ] SubTask 3.2: Read `core/event_bus.py:309` `ModeChanged` 类定义 + `core/monitoring_module.py:434` 事件封装
  - [ ] SubTask 3.3: Read `core/tick_bar_module.py:746` / `core/execution_module.py:3333` / `core/trade_module.py:630` `_on_mode_changed` 确认三模块订阅 ModeChanged 后的行为
  - [ ] SubTask 3.4: 新建 `metatest/test_runtime_mode_switch.py`，实现：`set_mode` 切换发布 `ModeChanged` ×2（仿真↔回放 / 回放↔实盘），用 EventBus 订阅计数断言；切换后 `TickReceived → BarComposed → FormulaEvaluated → StockFiltered` 链路完整（注入 tick 事件触发链）；切到实盘后 `fire_due` 无 sim_step spec（heapq 不步进）
  - [ ] SubTask 3.5: `python -m pytest metatest/test_runtime_mode_switch.py -v` 退出码 0

- [ ] Task 4: 阶段 1 验证
  - [ ] SubTask 4.1: `python -m pytest metatest/test_runtime_replay_heapq.py metatest/test_runtime_simulation_heapq.py metatest/test_runtime_mode_switch.py -v` 3 文件全绿
  - [ ] SubTask 4.2: Grep `time\.sleep|asyncio\.sleep` 在 3 个新测试文件零匹配（除 `# noqa: event-driver` 标注）
  - [ ] SubTask 4.3: `python -c "from metatest.runner import _collect_runtime_verification; from metatest.runner import _StatsPlugin; s=_StatsPlugin(); s.file_stats={'test_runtime_replay_heapq.py':{'passed':1,'failed':0,'errors':0},'test_runtime_simulation_heapq.py':{'passed':1,'failed':0,'errors':0},'test_runtime_mode_switch.py':{'passed':1,'failed':0,'errors':0}}; r=_collect_runtime_verification(s); print(r); assert r['passed']==3"` 验证检测器能采集到 3 文件通过

## 阶段 2：创建正测试包装 + 检测器 fail-loud 硬化

### 架构工程师任务

- [ ] Task 5: 变更 P1 — 新建 `metatest/test_positive_runtime_verification.py`
  - [ ] SubTask 5.1: Read 现有 `metatest/test_positive_*.py` 确认正测试模式（subprocess 或 import 断言）
  - [ ] SubTask 5.2: 新建 `metatest/test_positive_runtime_verification.py`，实现：断言 3 个 runtime 测试文件在主 pytest 运行中 `passed > 0` 且 `failed == 0` 且 `errors == 0`（通过 `_StatsPlugin.file_stats` 或直接 import 测试模块断言）
  - [ ] SubTask 5.3: `python -m pytest metatest/test_positive_runtime_verification.py -v` 退出码 0

- [ ] Task 6: 变更 D1 — `_collect_runtime_verification` fail-loud 硬化
  - [ ] SubTask 6.1: Read `metatest/runner.py:2032-2049` 确认 `_collect_runtime_verification` 当前实现（`fstats=None` → `continue` → 静默 0/3）
  - [ ] SubTask 6.2: 修改 `_collect_runtime_verification`：新增 `missing_files: List[str]` 字段，当 `fstats is None` 时将文件名加入 `missing_files`（而非 `continue` 跳过）
  - [ ] SubTask 6.3: 返回 dict 新增 `missing_files` 字段：`{"passed": passed, "total": total, "files": [...], "missing_files": [...]}`
  - [ ] SubTask 6.4: Read `metatest/runner.py` `_collect_isomorphism_violations` 函数确认检查项添加位置
  - [ ] SubTask 6.5: 在 `_collect_isomorphism_violations` 新增第 49 项检查：`runtime_verification_missing_files` 非空时计 1 违规（检测器引用不存在目标）
  - [ ] SubTask 6.6: test_results 新增 `runtime_verification_missing_files` 字段
  - [ ] SubTask 6.7: `python -c "from metatest.runner import _collect_runtime_verification, _StatsPlugin; s=_StatsPlugin(); r=_collect_runtime_verification(s); print(r); assert len(r['missing_files'])==3"` 验证 fail-loud（空 stats 时 3 文件缺失）
  - [ ] SubTask 6.8: `python -c "from metatest.runner import _collect_runtime_verification, _StatsPlugin; s=_StatsPlugin(); s.file_stats={'test_runtime_replay_heapq.py':{'passed':1,'failed':0,'errors':0},'test_runtime_simulation_heapq.py':{'passed':1,'failed':0,'errors':0},'test_runtime_mode_switch.py':{'passed':1,'failed':0,'errors':0}}; r=_collect_runtime_verification(s); print(r); assert r['missing_files']==[] and r['passed']==3"` 验证齐备后零缺失

## 阶段 3：ISOMORPHISM_CHECKS_TOTAL 扩展 + RULES 新增 + 全量回归

### 架构工程师任务

- [ ] Task 7: 变更 M1 — ISOMORPHISM_CHECKS_TOTAL 48 → 49
  - [ ] SubTask 7.1: Read `metatest/scoring.py:114` 确认 `ISOMORPHISM_CHECKS_TOTAL = 48`（v12 值）
  - [ ] SubTask 7.2: 更新 `ISOMORPHISM_CHECKS_TOTAL = 49`（48 + 1 新增检查）
  - [ ] SubTask 7.3: Read `metatest/runner.py` 第 48 项检查位置确认新增点
  - [ ] SubTask 7.4: 新增第 49 项检查 docstring：runtime_verification 目标文件零缺失（检测器引用完整性）
  - [ ] SubTask 7.5: `python -c "from metatest.scoring import ISOMORPHISM_CHECKS_TOTAL; print(ISOMORPHISM_CHECKS_TOTAL); assert ISOMORPHISM_CHECKS_TOTAL == 49"` 输出 49

- [ ] Task 8: 变更 L1 — RULES 124 新增「检测器引用完整性」纪律
  - [ ] SubTask 8.1: Read `RULES.md` 第 123 条确认插入位置（line 306 之后）
  - [ ] SubTask 8.2: 新增第 124 条「检测器引用完整性」纪律（见 spec.md 变更 L1）
  - [ ] SubTask 8.3: Grep `^124\.` 在 RULES.md = 1

### 评审工程师任务

- [ ] Task 9: 变更 L2 — 全量回归
  - [ ] SubTask 9.1: `python -m pytest metatest/ -x -q --ignore=metatest/test_positive_event_panel.py` 全量测试通过（v12 的 4 个 pre-existing 失败仍允许）
  - [ ] SubTask 9.2: `python -m pytest metatest/test_runtime_replay_heapq.py metatest/test_runtime_simulation_heapq.py metatest/test_runtime_mode_switch.py metatest/test_positive_runtime_verification.py -v` 4 文件全绿
  - [ ] SubTask 9.3: `python -c "from metatest.runner import _collect_runtime_verification, _StatsPlugin; s=_StatsPlugin(); s.file_stats={'test_runtime_replay_heapq.py':{'passed':1,'failed':0,'errors':0},'test_runtime_simulation_heapq.py':{'passed':1,'failed':0,'errors':0},'test_runtime_mode_switch.py':{'passed':1,'failed':0,'errors':0}}; r=_collect_runtime_verification(s); print(r); assert r['passed']==3 and r['missing_files']==[]"` runtime_verification passed=3 + missing_files=[]
  - [ ] SubTask 9.4: `python -m eventtest.run_eventtest` 退出码 0（全绿）
  - [ ] SubTask 9.5: runtime_verification 维度 = 100%（v5→v12 的 0/100 缺口闭合）
  - [ ] SubTask 9.6: isomorphism_elimination 维度 = 100（49 项 0 违规，含新增第 49 项）
  - [ ] SubTask 9.7: Grep `time\.sleep|asyncio\.sleep` 在 3 个 runtime 测试文件零匹配（除 `# noqa: event-driver`）
  - [ ] SubTask 9.8: Grep `^124\.` 在 RULES.md = 1
  - [ ] SubTask 9.9: `python -c "from metatest.scoring import ISOMORPHISM_CHECKS_TOTAL; print(ISOMORPHISM_CHECKS_TOTAL); assert ISOMORPHISM_CHECKS_TOTAL == 49"` 输出 49
  - [ ] SubTask 9.10: oop_inheritance_depth / adapter_isomorphism / dispatcher_isomorphism / handler_exception_coverage / essence_ratio / eventtest_regression 维度 = 100（v12 成果不回归）
  - [ ] SubTask 9.11: DZH↔TDX roundtrip 保真（不回归）

# Task Dependencies
- Task 1/2/3 互相独立（3 个测试文件验证不同场景，可并行实现）
- Task 4 依赖 Task 1-3（3 文件创建后才能整体验证）
- Task 5 依赖 Task 1-3（正测试包装依赖 3 文件存在）
- Task 6 依赖 Task 1-3（检测器硬化后需 3 文件存在才能验证齐备场景）
- Task 7 依赖 Task 6（ISOMORPHISM_CHECKS_TOTAL 扩展依赖第 49 项检查就位）
- Task 8 依赖 Task 1-7（RULES 文档化已落地成果）
- Task 9 依赖 Task 1-8（全量回归）
