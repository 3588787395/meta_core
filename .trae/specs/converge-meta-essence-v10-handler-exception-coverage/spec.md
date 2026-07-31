# 元模式本质收敛 v10：handler 异常保护全覆盖 + 全局收敛上限文档化 Spec

## Why

v9 文档化了 DZH/TDX 同构收敛上限（converters.py 内）。v10 架构工程师跨模块深度调研运行时行为同构，确认**全局元模式收敛已达上限**：事件订阅（`_BaseModule` + `_SUBSCRIPTIONS` 覆盖 7 主模块）、adapter 转发（v6/v7 四表四通用器）、MetaDispatcher 统一（v5）、轮询消除（v4）均已达标。剩余 4 个辅助类（ImportExportModule/KLineReplayEngine/SignalDeriver/ActionDispatcher）各单订阅 1 事件，表驱动化抽象税 > 收益（每类 -1 手动 subscribe +1 register调用 +2 _SUBSCRIPTIONS 类属性 = 净 +2 行，负收益）。

但调研发现一个**真正的运行时安全缺口**：`_event_handler` 装饰器（event_bus.py:15-26）统一了事件 handler 异常处理（捕获→logger.warning→返回 None），防止 handler 异常中断 EventBus.publish 同步扇出链（一个 handler 抛异常会中断后续订阅者执行）。该装饰器已被 6 个模块 25+ handler 使用，但 4 个手动 subscribe 的 handler **未装饰**，存在未捕获异常中断事件链的运行时风险。

**第十层洞察（异常处理覆盖完整性是运行时安全本质）**：EventBus.publish 同步扇出（`_dispatch_impl` 遍历订阅者逐个调用），若某 handler 抛未捕获异常，后续订阅者不执行——事件链断裂。`_event_handler` 装饰器将异常处理从 handler 体内部上提到装饰器层（AOP 横切），统一了"handler 不应中断事件链"的运行时契约。v10 补齐 4 个未装饰 handler，实现 handler 异常保护 100% 覆盖，闭合"极致本质的运行时"的运行时安全本质。

**诚实声明**：v10 的 4 处装饰器补齐是 +4 行（非代码精减）。这是运行时安全性提升，响应"极致本质的运行时"诉求。全局元模式收敛已达上限，无更多精减代码机会——v10 文档化此结论（RULES 120「知止」纪律），防止后续过度抽象。

## What Changes

### 阶段 1：handler 异常保护全覆盖（高优先级，核心）

- **变更 T1：ImportExportModule._on_export_completed 添加 @_event_handler**。`core/import_export_module.py:141` 的 `_on_export_completed` 方法添加 `@_event_handler("_on_export_completed")` 装饰器。需从 `.event_bus` 导入 `_event_handler`。净 +1 行（装饰器）+ 1 行 import 调整（合并到现有 import）。
- **变更 T2：KLineReplayEngine._on_replay_started 添加 @_event_handler**。`core/runtime_mode_module.py:325` 的 `_on_replay_started` 方法添加装饰器。`_event_handler` 已在 import 中（runtime_mode_module.py:31）。净 +1 行。
- **变更 T3：SignalDeriver._on_stock_changed 添加 @_event_handler**。`core/trade_module.py:1041` 的 `_on_stock_changed` 方法添加装饰器。需确认 `_event_handler` 在 trade_module.py import 中（line 17 已 import）。净 +1 行。
- **变更 T4：ActionDispatcher._on_signal 添加 @_event_handler**。`core/trade_module.py:1073` 的 `_on_signal` 方法添加装饰器。同上 import 已就绪。净 +1 行。

### 阶段 2：metatest v10 量化评审升级（量化闭环）

- **变更 M1：scoring.py 新增 handler_exception_coverage 检查逻辑**。在 `oop_inheritance_depth` 维度或新增子检查中，验证所有 `self._bus.subscribe(` 调用对应的 handler 方法均使用 `@_event_handler` 装饰（AST 检查：subscribe 调用的第二参数 handler_name 对应的方法 decorator_list 含 `_event_handler`）。**方案**：不新增第 22 维（避免权重重分配），而是在 `oop_inheritance_depth` 的 7 条件中深化第 7 条件为「hooks_are_abstract **且** handler_exception_coverage=100%」，或新增独立检查字段供 isomorphism_elimination 维度引用。**推荐**：新增 `handler_exception_coverage` 字段到 test_results，scoring 在 `isomorphism_elimination` 维度（40 项同构检查）中新增 1 项检查（41 项），0 违规满分。
- **变更 M2：runner.py 采集 handler_exception_coverage**。AST 解析 core/*.py，对所有 `self._bus.subscribe(EventType, self._handler_name)` 调用，检查 `_handler_name` 方法的 decorator_list 是否含 `_event_handler`。覆盖率 = 已装饰 handler 数 / 手动 subscribe handler 总数。
- **变更 M3：test_positive_oop_inheritance.py 或新建 test_positive_handler_exception.py**。断言：4 个手动 subscribe 的 handler 均使用 `@_event_handler` 装饰。
- **变更 M4：README.md v10 文档**。新增第十层洞察说明 + 全局收敛上限文档化段落。

### 阶段 3：RULES + 全量回归

- **变更 R1：RULES.md 新增第 120 条**。文档化「所有事件 handler 必须使用 `@_event_handler` 装饰器（禁止裸 handler），防止 handler 异常中断 EventBus.publish 同步扇出链。同时，元模式同构收敛已达全局上限——converters/订阅/adapter/MetaDispatcher/轮询五维均已达标，禁止强行合并结构同构但数据/派发异构的代码（全局知止纪律）。」
- **变更 R2：全量回归**。metatest 总分 ≥ 95 且 21 维均 ≥ 80，eventtest 退出码 0，handler_exception_coverage = 100%（4/4 手动 subscribe handler 全装饰），DZH↔TDX roundtrip 保真。

## Impact

- Affected specs: converge-meta-essence-v9-abstractmethod-failfast（v9 收敛上限从 DZH/TDX 扩展到全局）
- Affected code: core/import_export_module.py、core/runtime_mode_module.py、core/trade_module.py（4 处 handler 添加装饰器）、metatest/scoring.py、metatest/runner.py、metatest/test_positive_*.py、metatest/README.md、RULES.md

## ADDED Requirements

### Requirement: handler 异常保护全覆盖
The system SHALL decorate all event handlers registered via `self._bus.subscribe()` with `@_event_handler`, ensuring no handler exception can interrupt the EventBus.publish synchronous fan-out chain.

#### Scenario: handler 抛异常时不中断事件链
- **WHEN** a handler decorated with `@_event_handler` raises an exception during EventBus.publish fan-out
- **THEN** the exception is caught, logged via logger.warning(exc_info=True), and subsequent subscribers continue to receive the event

#### Scenario: 手动 subscribe 的 handler 全装饰
- **WHEN** metatest runner collects handler_exception_coverage data
- **THEN** verifies all 4 manually-subscribed handlers (ImportExportModule._on_export_completed / KLineReplayEngine._on_replay_started / SignalDeriver._on_stock_changed / ActionDispatcher._on_signal) are decorated with `@_event_handler`

### Requirement: 全局元模式收敛上限文档化
The system SHALL document that meta-pattern convergence has reached its global ceiling across five dimensions (converters/subscriptions/adapter/MetaDispatcher/polling), prohibiting forced merging of structurally-isomorphic but data/dispatch-heterogeneous code.

#### Scenario: 全局上限审计
- **WHEN** architecture review evaluates further convergence opportunities
- **THEN** confirms all five dimensions have reached ceiling and no code-reduction opportunity remains (v10 documents this as "global knowing-when-to-stop" discipline)
