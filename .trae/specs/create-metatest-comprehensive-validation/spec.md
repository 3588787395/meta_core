# metatest 全面正反合测试量化评审规范 Spec

## Why
现有测试体系存在三大缺陷：① `tests/` 目录 60+ 文件大量针对已删除接口（`MetaEngine`/`get_node_stocks`/`execution_order`/`EdgeFired.changed_codes`），353 个失败中 352 个为 pre-existing，无法作为评审依据；② `eventtest/` 仅 17 个文件覆盖后端核心运行时，前端模块（app.js/canvas.js/event-panel.js/ui.js）与 API 层（api.py/app.py 107+ 端点）覆盖严重不足；③ 评分靠人工代码审查，缺乏量化指标。本规格要求新建独立 `metatest/` 目录，以**正测试 / 反测试 / 合测试**三层方法论编写严格测试，**覆盖前端后端所有模块**（17 个关键功能点），**以量化测试结果（覆盖率+通过率+断言密度）作为评审工程师打分的唯一依据**。

## What Changes
- **新建** `metatest/` 目录于项目根目录，与 `tests/`/`eventtest/`/`simtests/` 并列；旧测试目录保持冻结。
- **新增** 正测试集（`test_positive_*.py`）：验证 17 个关键功能点的正常路径，覆盖后端 15 个核心模块 + 3 个 native 模块 + 5 个 services 模块 + API 层 + 前端 4 个 JS 模块。
- **新增** 反测试集（`test_negative_*.py`）：验证异常与边界场景，包括空池/无效配置/坏拓扑/重复入池/TTL 无持仓/公式异常/跨模块非法引用/前端 XSS/路由 404/SSE 断连。
- **新增** 合测试集（`test_integration_*.py`）：端到端集成，覆盖三模式全流程、事件链顺序、池状态快照、导入导出 roundtrip、配置热加载、前端 E2E（Playwright）。
- **新增** `metatest/conftest.py`：统一 fixture（虚拟时钟、fz 股票生成器、PoolEngine 装配器、事件收集器、池快照、FastAPI TestClient、Playwright browser）。
- **新增** `metatest/runner.py`：量化测试运行器，输出 6 维评分报告（模块覆盖率/测试通过率/断言密度/事件链完整性/性能基准/前端 E2E 通过率）。
- **新增** `metatest/scoring.py`：评分引擎，按模块加权计算总分（0-100），门槛 ≥ 95 分。
- **新增** `metatest/README.md`：方法论说明 + 运行方式 + 评分规则。
- **修改** 评审流程：评审工程师**必须运行** `python -m metatest.runner` 并以输出报告中的 6 维量化指标打分。
- **修改** 修复流程：架构工程师在修复 bug 后**必须**先在本地运行 metatest 确认总分 ≥ 95 再提交评审。
- **不修改** `core/`/`native/`/`services/`/`web/` 任何源文件（除非测试发现真实 bug 并经架构工程师修复）。

## Impact
- Affected specs:
  - `perfect-meta-pattern-iteration`（已结项，本规格为其建立量化回归基线）
  - `create-eventtest-strict-validation`（已结项，本规格扩展其覆盖范围至前端+API 层）
  - `refactor-meta-pattern-unification`（已结项，本规格验证三处同构合并的正确性）
- Affected code:
  - 新增 `metatest/` 目录全部文件（约 40+ 个测试文件 + conftest.py + runner.py + scoring.py + README.md）
  - 不修改任何源文件
- 不影响现有 `tests/`/`eventtest/`/`simtests/` 目录（保持冻结/只读）

## ADDED Requirements

### Requirement: metatest 目录结构
系统 SHALL 在项目根目录下新建 `metatest/` 目录，结构与现有测试目录并列，包含：
- `__init__.py`（标记为包）
- `conftest.py`（共享 fixture，提供 8+ 个 fixture）
- `runner.py`（量化测试运行器，输出 6 维评分报告）
- `scoring.py`（评分引擎，按模块加权计算总分）
- `README.md`（方法论说明 + 运行方式 + 评分规则）
- 正测试集（15+ 个 `test_positive_*.py`）
- 反测试集（10+ 个 `test_negative_*.py`）
- 合测试集（8+ 个 `test_integration_*.py`）
- 前端测试集（5+ 个 `test_frontend_*.py`，使用 Playwright）
- `fixtures/`（测试数据：池配置/公式/期望事件序列）

#### Scenario: 目录创建
- **WHEN** 架构工程师创建 `metatest/` 目录
- **THEN** 目录包含上述所有文件，且 `python -m metatest.runner` 可执行

### Requirement: 正测试覆盖 17 个关键功能点
正测试 SHALL 覆盖以下 17 个关键功能点的正常路径：

| # | 功能点 | 测试文件 | 覆盖模块 |
|---|---|---|---|
| 1 | 三模式切换 | test_positive_three_modes.py | runtime_mode_module/engine/app.py |
| 2 | 股票池设计器 | test_positive_pool_designer.py | domain/canvas.js |
| 3 | 事件引擎 | test_positive_event_engine.py | event_bus/execution_module |
| 4 | 公式计算 | test_positive_formula.py | formula_module/providers |
| 5 | K 线合成 | test_positive_kline.py | tick_bar_module/runtime_mode_module |
| 6 | 交易执行 | test_positive_trade.py | trade_module |
| 7 | 导入/导出 | test_positive_import_export.py | import_export_module/converters |
| 8 | 配置热加载 | test_positive_hot_reload.py | table_engine/api.py |
| 9 | 事件面板 | test_positive_event_panel.py | event-panel.js/web_state.py |
| 10 | HTTP API | test_positive_http_api.py | api.py/app.py |
| 11 | WebSocket/SSE | test_positive_websocket.py | api.py/app.js |
| 12 | 数据源 | test_positive_data_source.py | providers/data/tq_adapter |
| 13 | 校验器 | test_positive_validators.py | native/validators.py |
| 14 | 原生动作库 | test_positive_native_actions.py | native/builtins.py |
| 15 | 存储层 | test_positive_storage.py | services/storage.py |
| 16 | 备选池+池间转移 | test_positive_pool_transfer.py | runtime_mode_module/execution_module |
| 17 | 迁移 Oracle | test_positive_migration_oracle.py | tests/fixtures/migration_oracle/ |

#### Scenario: 正测试通过
- **WHEN** 运行正测试集
- **THEN** 所有测试通过，通过率 ≥ 98%

### Requirement: 反测试覆盖异常与边界
反测试 SHALL 覆盖以下异常与边界场景：
- 空备选池/空 tick 数据
- 无效条件节点配置（缺字段/类型错误）
- 坏边拓扑（自环/孤点/重复边）
- 重复入池/出池不存在股票
- TTL 到期无持仓
- 公式计算异常（除零/未定义变量/类型不匹配）
- 跨模块非法 import（违反零引用约束）
- 前端 XSS 注入（escHtml/escapeHtml）
- HTTP 路由 404/405/500
- SSE 断连/重连
- 配置文件缺失/格式错误
- WebSocket 消息格式错误

#### Scenario: 反测试通过
- **WHEN** 运行反测试集
- **THEN** 所有测试通过（即系统正确处理异常），通过率 ≥ 98%

### Requirement: 合测试覆盖端到端集成
合测试 SHALL 覆盖以下端到端场景：
- 仿真模式全流程（备选池→A池→B池→C池→买入→TTL→卖出）
- 回放模式全流程（K线回放→tick→bar→公式→筛选→转移）
- 实盘模式 tick 链路（tick_source→TickReceived→DataChanged→BarComposed→FormulaEvaluated→StockFiltered→EdgeFired→TransferExecuted）
- 事件链顺序断言（10 类事件按序，EdgeFired 先于 FormulaEvaluated）
- 池状态快照断言（备选池 100 只，A/B/C 池数量正确）
- 导入导出三向 roundtrip（DZH→JSON→TDX→JSON→DZH）
- 配置热加载端到端（修改 JSON→watchdog→ConfigChanged→模块重载）
- 前端 E2E（Playwright：模式切换/事件面板/池设计/导入导出）

#### Scenario: 合测试通过
- **WHEN** 运行合测试集
- **THEN** 所有测试通过，通过率 ≥ 98%

### Requirement: 量化评分系统
评分引擎 SHALL 按 6 维指标加权计算总分（0-100）：

| 维度 | 权重 | 评分规则 |
|---|---|---|
| 模块覆盖率 | 25% | 17 个模块中覆盖的模块数 / 17 × 100 |
| 测试通过率 | 25% | 通过测试数 / 总测试数 × 100 |
| 断言密度 | 15% | 断言数 / 测试文件数 ≥ 20 为满分，线性递减 |
| 事件链完整性 | 15% | 10 类事件均出现且顺序正确得满分，缺 1 类扣 10%，顺序错扣 10% |
| 性能基准 | 10% | 仿真 1000 tick 耗时 < 5s 为满分，> 30s 为 0 分，线性递减 |
| 前端 E2E 通过率 | 10% | Playwright 通过数 / 总数 × 100 |

#### Scenario: 评分达标
- **WHEN** 运行 `python -m metatest.runner`
- **THEN** 输出 6 维评分报告，总分 ≥ 95 方可通过评审

#### Scenario: 评分不达标
- **WHEN** 总分 < 95
- **THEN** 报告列出扣分项与重做清单，打回架构工程师修复

### Requirement: 测试必须验证元模式彻底完善迭代的正确性
测试 SHALL 验证 `perfect-meta-pattern-iteration` spec 中的 7 项元模式合并：
1. `_step_once_impl(async_mode)` 单一骨架（同步/异步同代码路径）
2. `IFormulaEngine` Protocol + `_ENGINE_DISPATCH` 表驱动
3. `require_config_store` + `get_simulator` + `_SIM_ACTIONS` Depends 化
4. `ConfigStore.get_table` / `get_data_file` 统一配置加载（禁止 `_load_json`）
5. `synthesize(bars, source, target)` + `_SYNTHESIS_RULES` 表驱动 K 线合成
6. `import_pool` / `export_pool` + `_IMPORT_RULES` / `_EXPORT_RULES` 表驱动
7. `renderEventCanvas(ctx, state, layoutMode)` + `_DRAW_LAYERS` + `_STYLE` 前端渲染

#### Scenario: 元模式合并验证
- **WHEN** 运行元模式验证测试
- **THEN** 7 项合并均验证通过，无同构代码残留

## MODIFIED Requirements

### Requirement: 评审工程师打分依据
评审工程师 SHALL 运行 `python -m metatest.runner` 并以输出的 6 维量化指标为唯一打分依据，不再仅靠代码审查。每项检查点通过得满分，部分通过按比例扣分，未通过扣全部分。门槛 ≥ 95 分方可通过评审。

### Requirement: 架构工程师修复流程
架构工程师在修复 bug 后 SHALL 先在本地运行 `python -m metatest.runner` 确认总分 ≥ 95 再提交评审。若 < 95，需根据扣分项修复后重新运行。

## REMOVED Requirements

### Requirement: 旧 tests/ 目录作为评审依据
**Reason**: `tests/` 目录 60+ 文件大量针对已删除接口（`MetaEngine`/`get_node_stocks`/`execution_order`/`EdgeFired.changed_codes`），353 个失败中 352 个为 pre-existing，无法作为评审依据。
**Migration**: `tests/` 目录保持冻结只读，仅作为历史参考。评审以 `metatest/` 为唯一依据。
