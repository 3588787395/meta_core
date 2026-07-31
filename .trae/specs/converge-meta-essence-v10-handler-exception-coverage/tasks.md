# Tasks

本规范按「架构工程师（实施）→ 评审工程师（验证）」迭代流程分 3 阶段实施，覆盖 4 个手动 subscribe handler 的 @_event_handler 异常保护补齐 + metatest v10 量化评审升级 + RULES 120 全局知止纪律。**第十层洞察（异常处理覆盖完整性是运行时安全本质）**：EventBus.publish 同步扇出时，一个 handler 抛未捕获异常会中断后续订阅者执行——事件链断裂。`_event_handler` 装饰器（event_bus.py:15-26）将异常处理从 handler 体内部上提到装饰器层（AOP 横切），统一了"handler 不应中断事件链"的运行时契约。该装饰器已被 6 模块 25+ handler 使用，但 4 个手动 subscribe 的 handler 未装饰，存在运行时安全缺口。v10 补齐这 4 处，实现 handler 异常保护 100% 覆盖。同时深度调研确认全局元模式收敛已达上限（converters/订阅/adapter/MetaDispatcher/轮询五维），v10 文档化此结论。**诚实声明**：v10 的 4 处装饰器补齐是 +4 行（非代码精减），是运行时安全性提升响应"极致本质的运行时"诉求。

## 阶段 1：handler 异常保护全覆盖（高优先级，核心）

### 架构工程师任务

- [ ] Task 1: 变更 T1 — ImportExportModule._on_export_completed 添加 @_event_handler
  - [ ] SubTask 1.1: Read `core/import_export_module.py:1-20` 确认当前 import 结构（`from .event_bus import EventBus, ExportCompleted, ImportStarted, PoolLoaded`）
  - [ ] SubTask 1.2: Read `core/import_export_module.py:126-145` 确认 ImportExportModule 类结构 + `_on_export_completed` 方法（line 141）
  - [ ] SubTask 1.3: 修改 import：在 `from .event_bus import (...)` 中新增 `_event_handler`（合并到现有 import，不新增 import 行）
  - [ ] SubTask 1.4: 在 `_on_export_completed` 方法定义前添加 `@_event_handler("_on_export_completed")` 装饰器
  - [ ] SubTask 1.5: `python -c "from core.import_export_module import ImportExportModule; print('import OK')"` 验证模块导入无错

- [ ] Task 2: 变更 T2 — KLineReplayEngine._on_replay_started 添加 @_event_handler
  - [ ] SubTask 2.1: Read `core/runtime_mode_module.py:25-35` 确认 `_event_handler` 已在 import 中（line 31 `_BaseModule` 同 import 块）
  - [ ] SubTask 2.2: Read `core/runtime_mode_module.py:300-340` 确认 KLineReplayEngine 类结构 + `_on_replay_started` 方法（line 325）
  - [ ] SubTask 2.3: 在 `_on_replay_started` 方法定义前添加 `@_event_handler("_on_replay_started")` 装饰器
  - [ ] SubTask 2.4: `python -c "from core.runtime_mode_module import KLineReplayEngine; print('import OK')"` 验证

- [ ] Task 3: 变更 T3+T4 — SignalDeriver._on_stock_changed + ActionDispatcher._on_signal 添加 @_event_handler
  - [ ] SubTask 3.1: Read `core/trade_module.py:14-20` 确认 `_event_handler` 已在 import 中（line 17）
  - [ ] SubTask 3.2: Read `core/trade_module.py:1025-1080` 确认 SignalDeriver._on_stock_changed（line 1041）+ ActionDispatcher._on_signal（line 1073）
  - [ ] SubTask 3.3: 在 `_on_stock_changed` 方法定义前添加 `@_event_handler("_on_stock_changed")` 装饰器
  - [ ] SubTask 3.4: 在 `_on_signal` 方法定义前添加 `@_event_handler("_on_signal")` 装饰器
  - [ ] SubTask 3.5: `python -c "from core.trade_module import SignalDeriver, ActionDispatcher; print('import OK')"` 验证

- [ ] Task 4: 阶段 1 验证
  - [ ] SubTask 4.1: Grep `@_event_handler\("_on_export_completed"\)` 在 import_export_module.py = 1
  - [ ] SubTask 4.2: Grep `@_event_handler\("_on_replay_started"\)` 在 runtime_mode_module.py = 1
  - [ ] SubTask 4.3: Grep `@_event_handler\("_on_stock_changed"\)` 在 trade_module.py = 1
  - [ ] SubTask 4.4: Grep `@_event_handler\("_on_signal"\)` 在 trade_module.py = 1
  - [ ] SubTask 4.5: `python -m pytest metatest/ -x -q --ignore=metatest/test_positive_event_panel.py 2>&1 | tail -5` 全量测试不回归
  - [ ] SubTask 4.6: `python -m eventtest.run_eventtest 2>&1 | tail -3` 退出码 0

## 阶段 2：metatest v10 量化评审升级（量化闭环）

### 架构工程师任务

- [ ] Task 5: 变更 M1+M2 — scoring.py + runner.py handler_exception_coverage 采集与评分
  - [ ] SubTask 5.1: Read `metatest/scoring.py` 确认 `isomorphism_elimination` 维度评分逻辑（40 项同构检查，0 违规满分）
  - [ ] SubTask 5.2: Read `metatest/runner.py` 确认同构检查采集逻辑（ISOMORPHISM_CHECKS_TOTAL_V4 = 40）
  - [ ] SubTask 5.3: 在 runner.py 新增 `_collect_handler_exception_coverage()` 函数：AST 解析 core/*.py，对所有 `self._bus.subscribe(EventType, self._handler_name)` 调用，检查 `_handler_name` 方法的 decorator_list 是否含 `_event_handler`。返回 `{"covered": int, "total": int, "coverage": float}`
  - [ ] SubTask 5.4: 在 runner.py 的 `_collect_isomorphism_violations`（或同等函数）中新增 1 项检查：handler_exception_coverage < 100% 计为 1 违规。ISOMORPHISM_CHECKS_TOTAL 从 40 → 41
  - [ ] SubTask 5.5: 在 scoring.py 更新 ISOMORPHISM_CHECKS_TOTAL = 41，`_score_isomorphism_elimination` 使用 41 作分母
  - [ ] SubTask 5.6: test_results 新增 `handler_exception_coverage` 字段
  - [ ] SubTask 5.7: `python -c "from metatest.runner import _collect_handler_exception_coverage; r=_collect_handler_exception_coverage(); print(r)"` 验证 coverage = 100%（4/4）

- [ ] Task 6: 变更 M3+M4 — 测试 + README 文档
  - [ ] SubTask 6.1: 在 `metatest/test_positive_oop_inheritance.py` 或新建 `metatest/test_positive_handler_exception.py` 新增断言：4 个手动 subscribe 的 handler 均使用 `@_event_handler` 装饰（AST 检查 decorator_list）
  - [ ] SubTask 6.2: 新增测试：模拟 handler 抛异常，验证后续订阅者仍收到事件（_event_handler 装饰器的异常隔离行为）
  - [ ] SubTask 6.3: `python -m pytest metatest/test_positive_handler_exception.py -v`（或追加到 oop_inheritance 测试）退出码 0
  - [ ] SubTask 6.4: Read `metatest/README.md` 确认 v9 文档结构
  - [ ] SubTask 6.5: README.md 标题 v9 → v10，概述新增第十层洞察说明
  - [ ] SubTask 6.6: README.md 新增「全局收敛上限文档化」段落：五维（converters/订阅/adapter/MetaDispatcher/轮询）均已达标，禁止强行合并
  - [ ] SubTask 6.7: README.md isomorphism_elimination 维度说明 40 项 → 41 项

## 阶段 3：RULES + 全量回归

### 架构工程师任务

- [ ] Task 7: 变更 R1 — RULES.md 新增第 120 条
  - [ ] SubTask 7.1: 在 RULES.md 第 119 条后新增第 120 条：「所有事件 handler 必须使用 `@_event_handler` 装饰器（禁止裸 handler），防止 handler 异常中断 EventBus.publish 同步扇出链——一个 handler 抛未捕获异常会导致后续订阅者不执行，事件链断裂。`@_event_handler` 将异常处理从 handler 体内部上提到装饰器层（AOP 横切），统一"handler 不应中断事件链"的运行时契约。这是「极致本质的运行时」的第十层洞察：异常处理覆盖完整性是运行时安全本质。同时，元模式同构收敛已达全局上限——converters（v8/v9）/订阅（_BaseModule+_SUBSCRIPTIONS 覆盖 7 主模块）/adapter（v6/v7 四表四通用器）/MetaDispatcher（v5）/轮询（v4）五维均已达标，禁止强行合并结构同构但数据/派发异构的代码（全局知止纪律）。」
  - [ ] SubTask 7.2: Grep `^120\.` 在 RULES.md = 1

### 评审工程师任务

- [ ] Task 8: 变更 R2 — 全量回归
  - [ ] SubTask 8.1: `python -m pytest metatest/ -x -q --ignore=metatest/test_positive_event_panel.py` 退出码 0（全量测试通过）
  - [ ] SubTask 8.2: `python -c "from metatest.runner import _collect_handler_exception_coverage; r=_collect_handler_exception_coverage(); print(r); assert r['coverage'] == 100.0"` 验证 handler_exception_coverage = 100%
  - [ ] SubTask 8.3: `python -m eventtest.run_eventtest` 退出码 0（全绿）
  - [ ] SubTask 8.4: Grep `@_event_handler` 在 core/ 手动 subscribe 的 4 个 handler 各 = 1
  - [ ] SubTask 8.5: oop_inheritance_depth 维度 = 100（7 条件保持，v9 不回归）
  - [ ] SubTask 8.6: isomorphism_elimination 维度 = 100（41 项 0 违规，含新增 handler_exception_coverage 项）
  - [ ] SubTask 8.7: DZH↔TDX roundtrip 保真（19 roundtrip 测试全过）
  - [ ] SubTask 8.8: essence_ratio 维度不受影响（仅统计 core/*.py 行数，+4 装饰器行影响可忽略）
  - [ ] SubTask 8.9: adapter_isomorphism 维度 = 100（v7/v8/v9 保持）
  - [ ] SubTask 8.10: dispatcher_isomorphism 维度 = 100（v5/v6/v7/v8/v9 保持）
  - [ ] SubTask 8.11: runtime_verification 维度 = 100（v5/v6/v7/v8/v9 保持）
  - [ ] SubTask 8.12: eventtest_regression 维度 = 100（v5/v6/v7/v8/v9 保持）

# Task Dependencies
- Task 5/6 依赖 Task 1-3（装饰器补齐后才能采集与断言）
- Task 7 依赖 Task 1-6（RULES 文档化已落地成果）
- Task 8 依赖 Task 1-7（全量回归）
