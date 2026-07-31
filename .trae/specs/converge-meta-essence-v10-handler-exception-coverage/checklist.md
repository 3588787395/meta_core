# Checklist

## 架构工程师检查点（实施前自检）

- [ ] 已阅读 spec.md「Why」章节并理解：v10 是「异常处理覆盖完整性」——4 个手动 subscribe 的 handler 未装饰 _event_handler，存在未捕获异常中断 EventBus.publish 同步扇出链的运行时风险
- [ ] 已理解本次迭代核心是「运行时安全性提升」（+4 行装饰器），非代码精减——全局元模式收敛已达上限，无更多精减机会
- [ ] 已阅读 `core/event_bus.py:15-26` 确认 `_event_handler` 装饰器定义（捕获→logger.warning→返回 None）
- [ ] 已阅读 `core/event_bus.py:536-545` 确认 `_BaseModule` + `_SUBSCRIPTIONS` 表驱动订阅机制
- [ ] 已 Grep `self._bus.subscribe(` 确认 4 处手动 subscribe（import_export_module:136 / runtime_mode_module:321 / trade_module:1039,1071）
- [ ] 已 Grep `_event_handler` 确认 6 模块 25+ handler 已使用，但 4 个手动 subscribe 的 handler 未装饰
- [ ] 已确认 4 个辅助类单订阅表驱动化抽象税 > 收益（每类净 +2 行，负收益）
- [ ] 已确认全局元模式收敛五维均达标：converters（v8/v9）/订阅（_BaseModule 7 主模块）/adapter（v6/v7）/MetaDispatcher（v5）/轮询（v4）
- [ ] 已确认「合并非拆分」硬约束延续：_event_handler 装饰器仅添加横切异常处理，不动 handler 方法体

## 评审工程师检查点（阶段 1：handler 异常保护全覆盖）

### 变更 T1 — ImportExportModule._on_export_completed
- [ ] `_event_handler` 已添加到 `core/import_export_module.py` 的 `.event_bus` import
- [ ] `@_event_handler("_on_export_completed")` 装饰器在 `_on_export_completed` 方法定义前
- [ ] `python -c "from core.import_export_module import ImportExportModule"` 导入无错

### 变更 T2 — KLineReplayEngine._on_replay_started
- [ ] `@_event_handler("_on_replay_started")` 装饰器在 `_on_replay_started` 方法定义前
- [ ] `_event_handler` 已在 `core/runtime_mode_module.py` import 中（line 31）
- [ ] `python -c "from core.runtime_mode_module import KLineReplayEngine"` 导入无错

### 变更 T3+T4 — SignalDeriver._on_stock_changed + ActionDispatcher._on_signal
- [ ] `@_event_handler("_on_stock_changed")` 装饰器在 `_on_stock_changed` 方法定义前
- [ ] `@_event_handler("_on_signal")` 装饰器在 `_on_signal` 方法定义前
- [ ] `_event_handler` 已在 `core/trade_module.py` import 中（line 17）
- [ ] `python -c "from core.trade_module import SignalDeriver, ActionDispatcher"` 导入无错

### 阶段 1 整体验证
- [ ] Grep `@_event_handler\("_on_export_completed"\)` 在 import_export_module.py = 1
- [ ] Grep `@_event_handler\("_on_replay_started"\)` 在 runtime_mode_module.py = 1
- [ ] Grep `@_event_handler\("_on_stock_changed"\)` 在 trade_module.py = 1
- [ ] Grep `@_event_handler\("_on_signal"\)` 在 trade_module.py = 1
- [ ] `python -m pytest metatest/ -x -q --ignore=metatest/test_positive_event_panel.py` 全量测试不回归
- [ ] `python -m eventtest.run_eventtest` 退出码 0（全绿）

## 评审工程师检查点（阶段 2：metatest v10 量化评审升级）

### 变更 M1+M2 — scoring.py + runner.py handler_exception_coverage
- [ ] runner.py 新增 `_collect_handler_exception_coverage()` 函数
- [ ] AST 解析 core/*.py 所有 `self._bus.subscribe(EventType, self._handler_name)` 调用
- [ ] 检查 `_handler_name` 方法的 decorator_list 是否含 `_event_handler`
- [ ] 返回 `{"covered": int, "total": int, "coverage": float}`
- [ ] ISOMORPHISM_CHECKS_TOTAL 从 40 → 41（新增 handler_exception_coverage 项）
- [ ] scoring.py ISOMORPHISM_CHECKS_TOTAL = 41
- [ ] `_score_isomorphism_elimination` 使用 41 作分母
- [ ] test_results 新增 `handler_exception_coverage` 字段
- [ ] `python -c "from metatest.runner import _collect_handler_exception_coverage; r=_collect_handler_exception_coverage(); print(r)"` 验证 coverage = 100%（4/4）

### 变更 M3+M4 — 测试 + README 文档
- [ ] 新增 handler_exception 断言测试（4 个手动 subscribe handler 全装饰）
- [ ] 新增异常隔离行为测试（handler 抛异常时后续订阅者仍收到事件）
- [ ] `python -m pytest metatest/test_positive_handler_exception.py -v`（或追加到 oop_inheritance）退出码 0
- [ ] README.md 标题 v9 → v10
- [ ] README.md 新增第十层洞察说明
- [ ] README.md 新增「全局收敛上限文档化」段落
- [ ] README.md isomorphism_elimination 维度说明 40 → 41 项

## 评审工程师检查点（阶段 3：RULES + 全量回归）

### RULES.md 120
- [ ] 第 120 条 — 所有事件 handler 必须 @_event_handler 装饰 + 全局收敛上限知止纪律
- [ ] Grep `^120\.` 在 RULES.md = 1

### 全量回归
- [ ] `python -m pytest metatest/ -x -q --ignore=metatest/test_positive_event_panel.py` 全量测试通过
- [ ] handler_exception_coverage = 100%（4/4 手动 subscribe handler 全装饰）
- [ ] `python -m eventtest.run_eventtest` 退出码 0（全绿）
- [ ] oop_inheritance_depth 维度 = 100（7 条件保持，v9 不回归）
- [ ] isomorphism_elimination 维度 = 100（41 项 0 违规）
- [ ] DZH↔TDX roundtrip 保真（19 roundtrip 测试全过）
- [ ] essence_ratio 维度不受影响（+4 装饰器行影响可忽略）
- [ ] adapter_isomorphism 维度 = 100（v7/v8/v9 保持）
- [ ] dispatcher_isomorphism 维度 = 100（v5/v6/v7/v8/v9 保持）
- [ ] runtime_verification 维度 = 100（v5/v6/v7/v8/v9 保持）
- [ ] eventtest_regression 维度 = 100（v5/v6/v7/v8/v9 保持）

## 第十层洞察根因检查点（评审工程师最终验收）

- [ ] **异常处理覆盖完整性**：EventBus.publish 同步扇出时，一个 handler 抛未捕获异常会中断后续订阅者执行——事件链断裂。_event_handler 装饰器将异常处理从 handler 体内部上提到装饰器层（AOP 横切），统一"handler 不应中断事件链"的运行时契约
- [ ] **4 处缺口补齐**：ImportExportModule._on_export_completed / KLineReplayEngine._on_replay_started / SignalDeriver._on_stock_changed / ActionDispatcher._on_signal 四个手动 subscribe 的 handler 添加 @_event_handler 装饰，实现 100% 覆盖
- [ ] **全局收敛上限文档化**：五维（converters/订阅/adapter/MetaDispatcher/轮询）均已达标，禁止强行合并结构同构但数据/派发异构的代码（全局知止纪律）
- [ ] **非拆分非重写**：_event_handler 装饰器仅添加横切异常处理，不动 handler 方法体
- [ ] **量化评审驱动**：isomorphism_elimination 维度新增 handler_exception_coverage 检查项（40→41 项），使评分体系能驱动异常保护收敛
- [ ] **诚实声明确认**：v10 的 +4 行装饰器是运行时安全性提升，非代码精减。全局元模式收敛已达上限，无更多精减机会
