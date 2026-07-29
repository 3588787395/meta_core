# Four-Category Negative Tests v3 Rewrite Spec

## Why

metatest v3 反测试需重写为 4 类异常 + 15 组模式同构复活检测。原 14 个分散
反测试文件粒度过细，且缺少「底层逻辑」类别与「同构复活」防护。重写为 4 个
主文件统一管理，并通过 Grep 断言确保 Phase 1-3 合并的 15 组模式不会复活。

## What Changes

- AUGMENT test_negative_invalid_config.py：保留旧用例 + 新增 8 类 v3 边界用例
  （empty_pool / self_loop / orphan / dup_edge / invalid_params / cycle /
  missing_node / invalid_type）
- CREATE test_negative_runtime_errors.py：8+ 运行时异常用例（重复入池 / TTL 无持仓 /
  公式错误 / 跨模块非法引用 / 状态损坏恢复 / 并发访问 / 无效股票代码 / K 线溢出）
- CREATE test_negative_api_frontend.py：8+ API/前端异常用例（404 / 405 / 500 /
  SSE 断连 / WebSocket 错误 / 配置缺失 / XSS / 非法 JSON）
- CREATE test_negative_logic_errors.py（新类别）：9 底层逻辑用例 + 15 项同构复活检测
  （变更 A-O，Grep 断言旧同构代码零匹配）
- DO NOT delete existing 14 scattered files；4 主文件作为整合视图并行存在
- 使用 Python ``re`` 模块（而非 subprocess）实现 Grep 断言
- 每文件断言密度 ≥ 20

## Impact

- Affected specs: create-metatest-comprehensive-validation（并行，不动）
- Affected code: 仅 metatest/test_negative_*.py 测试文件，不动生产代码
- Dependencies: 复用 conftest.py fixtures（tick_table / event_collector /
  pool_snapshot / fastapi_client 等）
- Test target APIs: core/execution_module.py, core/event_bus.py,
  core/runtime_mode_module.py, core/formula_module.py, core/trade_module.py,
  core/screening_module.py, core/monitoring_module.py, app.py

## ADDED Requirements

### Requirement: 四类反测试每类 ≥ 8 用例

System SHALL provide 4 independently-runnable pytest files in metatest/ covering
invalid-config / runtime-errors / api-frontend / logic-errors categories, each
containing ≥ 8 test cases.

#### Scenario: Single file runs successfully
- WHEN executing python -m pytest metatest/test_negative_invalid_config.py -v
- THEN file recognized and cases execute without collection errors

#### Scenario: Exception handled gracefully
- WHEN system receives invalid config / runtime anomaly / malformed request / logic error
- THEN system handles via controlled exception or graceful degradation, no uncaught crash

### Requirement: 15 组同构复活检测（变更 A-O）

System SHALL provide 15 isomorphism revival detection tests in
test_negative_logic_errors.py using Python ``re`` module Grep assertions to
ensure zero matches of old isomorphic code patterns revived from Phase 1-3 merges:

- 变更 A: screening_module.py 不复活 _filter_condition_formula 等 nset 同构函数
- 变更 B: monitoring_module.py 不复活 5 个 PnL 计算同构函数
- 变更 C: import_export_module.py 不复活 _parse_dzh/_parse_tdx 等 同构函数
- 变更 D: formula_module.py _eval_formula/_eval_formula_series 为薄包装（≤8 行）
- 变更 E: trade_module.py _apply_tradeattr 不复活 if side=="BUY"/elif SELL 硬编码
- 变更 F: execution_module.py _build_filter_spec 表驱动分派
- 变更 G: core/*.py 不复活 json.load(open()) inline 模式
- 变更 H: execution_module.py 不复活 if mode=="inflection"/"rank" 硬编码
- 变更 I: runtime_mode_module.py 不复活 if self._base_period== 硬编码
- 变更 J: runtime_mode_module.py _run_coro_sync 仅模块级存在
- 变更 K: engine.py/runtime_mode_module.py _build_topology 委托 _build_adjacency
- 变更 L: monitoring_module.py 不复活 _momentum_key/_trend_key/_value_key
- 变更 M: execution_module.py _apply_stock_filters 仅在 _with_stock_filters 包装器内
- 变更 N: 5 核心模块 @_event_handler 装饰共 ≥ 28 次
- 变更 O: table_engine.py _validate_table 使用 _iter_entries 表驱动

#### Scenario: Isomorphism revival blocked
- WHEN old isomorphic code pattern (变更 A-O) reappears in core/*.py
- THEN Grep assertion fails with non-zero match count

### Requirement: 断言密度 ≥ 20/文件

System SHALL ensure each of the 4 main negative test files contains ≥ 20 assertions.

### Requirement: re 模块 Grep 断言

System SHALL implement Grep assertions using Python ``re`` module
(``re.findall`` / ``re.search``) rather than subprocess calls, for portability
and CI-friendliness.

## MODIFIED Requirements

### Requirement: 反测试整合组织

原 14 个分散文件 SHALL remain untouched；4 个新整合文件作为类别视图并行存在，
每文件聚焦一个异常维度，含 ≥ 8 用例与 ≥ 20 断言。

## REMOVED Requirements

### Requirement: 旧 5-8 用例下限
**Reason**: v3 提升为 ≥ 8 用例/文件
**Migration**: 4 主文件均已 ≥ 8 用例
