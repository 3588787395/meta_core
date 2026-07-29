# Tasks

本规范按「架构工程师 → 评审工程师」流程分 4 阶段实施，覆盖迭代 13-19 真同构合并与 metatest v3 重建。每轮迭代独立可验证，评审工程师逐轮 Grep 验证同构消除。

## 阶段 1：CRITICAL 真同构合并（迭代 13-16，高优先级）

### 迭代 13：双 TickTable 同名异构合并

- [ ] Task 1: 合并双 TickTable 为单一真相源
  - [ ] SubTask 1.1: 在 `core/tick_table.py:TickTable` 新增 `bind_prev(prev_tick)` / `column(code, col) -> float|None` / `prev_column(code, col) -> float|None` / `bar_hash() -> str` 方法（吸收 execution_module.py:2630 视图版能力）
  - [ ] SubTask 1.2: `TickTable.__init__` 扩展可选 `latest_tick` / `prev_tick` 引用参数（向后兼容水位线版用法）
  - [ ] SubTask 1.3: 删除 `core/execution_module.py:2630` 的 `class TickTable` 定义（约 25 行）
  - [ ] SubTask 1.4: `execution_module.py` 顶部新增 `from core.tick_table import TickTable`，`EdgeExecutor._tick_table = TickTable(state.latest_tick, state.prev_tick)` 改为引用统一类
  - [ ] SubTask 1.5: Grep 验证 `class TickTable` 全局仅 1 处定义（`core/tick_table.py`）
  - [ ] SubTask 1.6: 行为等价性验证：`EdgeExecutor._tick_table.column(code, col)` 与 `bar_hash()` 输出与合并前一致

### 迭代 14：双 compile 系统合并

- [ ] Task 2: 合并 Compiler/CompiledSchedule 到 compile/CompiledPool
  - [ ] SubTask 2.1: 审计 `class CompiledSchedule` (425) 全部字段，逐字段迁移到 `class CompiledPool` (1364)
  - [ ] SubTask 2.2: `CompiledPool` 新增字段：`timing_specs: Dict[eid→dict]` / `filter_specs: Dict[eid→dict]` / `propagate_specs: Dict[eid→dict]` / `action_specs: Dict[tid→dict]` / `ttl_specs: Dict[tid→dict]`（吸收 CompiledSchedule）
  - [ ] SubTask 2.3: 将 `Compiler._build_timing_spec` / `_build_filter_spec` / `_build_propagate_spec` 方法的逻辑迁移到 `compile()` 函数内部（调用 `_compile_*_spec`），删除 `class Compiler` (859)
  - [ ] SubTask 2.4: 删除 `class CompiledSchedule` (425) 与 `class Compiler` (859)
  - [ ] SubTask 2.5: 全局搜索 `CompiledSchedule` 引用，全部改为 `CompiledPool`
  - [ ] SubTask 2.6: `EdgeExecutor.__init__` 参数 `schedule: CompiledSchedule` 改为 `compiled: CompiledPool`，内部 `self.schedule` 改为 `self.compiled`
  - [ ] SubTask 2.7: Grep 验证 `class Compiler` / `class CompiledSchedule` 在 `core/` 中零匹配
  - [ ] SubTask 2.8: 行为等价性验证：`compile(pool_config)` 产出含全部原字段，引擎运行结果不变

### 迭代 15：三对 spec 构建器合并为表驱动

- [ ] Task 3: 删除 5 个 Spec BaseModel，spec 统一为 dict
  - [ ] SubTask 3.1: 审计 `TimingSpec` (316) / `FilterSpec` (330) / `PropagateSpec` (389) / `ActionSpec` (397) / `TTLSpec` (405) 五个 BaseModel 的字段与使用点
  - [ ] SubTask 3.2: 确认 `_compile_timing_spec` (1330) / `_compile_filter_spec` (1342) / `_compile_propagate_spec` (1354) 已产出 dict 形态
  - [ ] SubTask 3.3: 将所有引用 `TimingSpec` / `FilterSpec` / `PropagateSpec` / `ActionSpec` / `TTLSpec` 类型的代码改为 dict 访问（`spec["field"]` 替代 `spec.field`）
  - [ ] SubTask 3.4: 删除 5 个 Spec BaseModel 类定义
  - [ ] SubTask 3.5: 删除 `Compiler._build_timing_spec` / `_build_filter_spec` / `_build_propagate_spec` 方法（已在 Task 2 迁移）
- [ ] Task 4: 定义 `_SPEC_BUILDERS` 表驱动分派
  - [ ] SubTask 4.1: 定义 `_SPEC_BUILDERS = {"timing": _compile_timing_spec, "filter": _compile_filter_spec, "propagate": _compile_propagate_spec}` dict
  - [ ] SubTask 4.2: `compile()` 函数内构建 spec 时改为 `_SPEC_BUILDERS[kind](edge)` 查表分派
  - [ ] SubTask 4.3: Grep 验证 `_build_timing_spec` / `_build_filter_spec` / `_build_propagate_spec` 方法在 `core/` 中零匹配
  - [ ] SubTask 4.4: Grep 验证 `class TimingSpec` / `class FilterSpec` / `class PropagateSpec` / `class ActionSpec` / `class TTLSpec` 在 `core/` 中零匹配

### 迭代 16：三套 propagate 派发合并

- [ ] Task 5: 合并 _PROPAGATE_STRATEGIES 到 _PROPAGATE_MODES
  - [ ] SubTask 5.1: 在 `config/architecture/propagate_modes.json` 新增 `overwrite_copy` 模式（src_action: keep, tgt_action: replace）
  - [ ] SubTask 5.2: 重构 `_PROPAGATE_MODES` (1547) 为由 `propagate_modes.json` 配置驱动初始化，每模式含 `(tgt_strategy_fn, src_strategy_fn)` 两策略元组
  - [ ] SubTask 5.3: 将 `_PROPAGATE_STRATEGIES` (2622) 的 4 模式策略合并到 `_PROPAGATE_MODES`（含 overwrite_copy）
  - [ ] SubTask 5.4: `EdgeExecutor._propagate()` (2924 附近) 改为查 `_PROPAGATE_MODES` 而非 `_PROPAGATE_STRATEGIES`
  - [ ] SubTask 5.5: 删除 `_PROPAGATE_STRATEGIES` dict 定义 (2622)
  - [ ] SubTask 5.6: `propagate_apply()` (1518) 与 `EdgeExecutor._propagate()` 共用 `_PROPAGATE_MODES`
  - [ ] SubTask 5.7: Grep 验证 `_PROPAGATE_STRATEGIES` 在 `core/` 中零匹配
  - [ ] SubTask 5.8: 行为等价性验证：4 种 propagate 模式（copy/move/overwrite/overwrite_copy）执行结果与合并前一致

## 阶段 2：HIGH/MEDIUM 真同构合并（迭代 17-19）

### 迭代 17：节点/边类型解析收敛到编译期

- [ ] Task 6: 删除 engine.py 运行时重复解析方法
  - [ ] SubTask 6.1: 确认 `CompiledPool` 已含 `node_role: Dict[nid→role]` / `edge_type: Dict[eid→str]` / 端点解析字段（如缺则补）
  - [ ] SubTask 6.2: `engine.py` 中所有调用 `self._resolve_node_type(n)` 改为 `self.compiled.node_role.get(nid)` 或预编译查表
  - [ ] SubTask 6.3: `engine.py` 中所有调用 `self._resolve_edge_type(source_type)` 改为 `self.compiled.edge_type.get(eid)`
  - [ ] SubTask 6.4: `engine.py` 中所有调用 `self._extract_edge_endpoint(edge, *keys)` 改为从 `CompiledPool` 预编译端点读取
  - [ ] SubTask 6.5: 删除 `engine.py:1560 _resolve_node_type` / `engine.py:1583 _extract_edge_endpoint` / `engine.py:1597 _resolve_edge_type` 三个方法
  - [ ] SubTask 6.6: Grep 验证 `engine.py` 中 `def _resolve_node_type` / `def _resolve_edge_type` / `def _extract_edge_endpoint` 零匹配
  - [ ] SubTask 6.7: 行为等价性验证：引擎运行时类型判定结果与合并前一致

### 迭代 18：角色解析三函数合并

- [ ] Task 7: 删除 engine.py 运行时角色解析方法
  - [ ] SubTask 7.1: 确认 `CompiledPool.node_role: Dict[nid→role]` 已由编译期 `_resolve_node_role(node)` (execution_module.py:1283) 产出
  - [ ] SubTask 7.2: `engine.py` 中所有调用 `self._resolve_pool_role(nid)` / `self._resolve_pool_role_compute(nid)` 改为 `self.compiled.node_role.get(nid)`
  - [ ] SubTask 7.3: 删除 `engine.py:1701 _resolve_pool_role` / `engine.py:1716 _resolve_pool_role_compute` 两个方法
  - [ ] SubTask 7.4: Grep 验证 `engine.py` 中 `def _resolve_pool_role` / `def _resolve_pool_role_compute` 零匹配
  - [ ] SubTask 7.5: 行为等价性验证：5 种角色（candidate/state/condition/target/discard）判定结果与合并前一致

### 迭代 19：_eval_*_path 六路径表驱动收敛

- [ ] Task 8: 定义 _EVAL_PATHS 表驱动分派
  - [ ] SubTask 8.1: 审计 6 个求值路径函数的签名与调用点：`_eval_set_operation` (2199) / `_eval_pass_through` (2249) / `_eval_formula_path` (2263) / `_eval_scalar_path` (2423) / `_eval_set_op_path` (2480) / `_eval_intersection_path` (2501)
  - [ ] SubTask 8.2: 定义 `_EVAL_PATHS = {"set_op": _eval_set_operation, "pass_through": _eval_pass_through, "formula": _eval_formula_path, "scalar": _eval_scalar_path, "set_op_path": _eval_set_op_path, "intersection": _eval_intersection_path}` dict
  - [ ] SubTask 8.3: `filter_eval()` (1500) 内部改为通过 `path_kind = filter_spec["nset_type"]` 查 `_EVAL_PATHS` 分派
  - [ ] SubTask 8.4: 确认 6 个函数仅被 `_EVAL_PATHS` 表与 `filter_eval()` 引用，无外部直接调用
  - [ ] SubTask 8.5: Grep 验证 6 函数调用点仅出现在 `_EVAL_PATHS` 表定义与 `filter_eval()` 内部
  - [ ] SubTask 8.6: 行为等价性验证：6 种 nset 类型求值结果与合并前一致

## 阶段 3：metatest v3 严格正反合测试重建

### Task 9: 修复 runner.py 量化评分真实化

- [ ] SubTask 9.1: 删除 `metatest/runner.py` 中 `no_tests=True` 直接返回 0（PASS）的逻辑（main() 末尾 `if no_tests: return 0`）
- [ ] SubTask 9.2: 无测试文件时总分 = 0（FAIL），report.json 如实记录 `tests_total=0`
- [ ] SubTask 9.3: 删除虚假 `metatest/report.json`
- [ ] SubTask 9.4: 确保 `tests_passed` / `tests_total` 与 pytest 实际输出一致（已有逻辑，验证无误）

### Task 10: 正测试集 - 底层逻辑验证（5 项）

- [ ] SubTask 10.1: `metatest/test_positive_waterline.py` — 水位线不变零计算（同输入重复 update 返回 False、零事件；水位线变化触发计算）
- [ ] SubTask 10.2: `metatest/test_positive_compile_run_separation.py` — 编译-运行分离（compile 一次性产出 CompiledPool 含全部字段；运行时零解析）
- [ ] SubTask 10.3: `metatest/test_positive_edge_three_layers.py` — 边执行三要素（trigger_check → filter_eval → propagate_apply 3 层调用深度）
- [ ] SubTask 10.4: `metatest/test_positive_node_role_table.py` — 节点角色表驱动（5 种角色查 _ROLE_ACTIONS 分派、node_roles.json 配置正确）
- [ ] SubTask 10.5: `metatest/test_positive_event_signal_action.py` — 事件-信号-动作正交化（StockChanged → Signal → Action 三层解耦）

### Task 11: 正测试集 - 后端 17 模块功能回归

- [ ] SubTask 11.1: `metatest/test_positive_domain.py` — core.domain（Node/Edge/Spec 数据模型）
- [ ] SubTask 11.2: `metatest/test_positive_engine.py` — core.engine（PoolEngine 运行时核心循环、水位线短路）
- [ ] SubTask 11.3: `metatest/test_positive_event_bus.py` — core.event_bus（事件发布订阅、Signal/StockChanged）
- [ ] SubTask 11.4: `metatest/test_positive_execution_module.py` — core.execution_module（compile/CompiledPool/三要素/propagate_apply）
- [ ] SubTask 11.5: `metatest/test_positive_formula_module.py` — core.formula_module（IFormulaEngine Protocol、_ENGINE_DISPATCH 表）
- [ ] SubTask 11.6: `metatest/test_positive_import_export.py` — core.import_export_module（import_pool/export_pool 表驱动）
- [ ] SubTask 11.7: `metatest/test_positive_monitoring.py` — core.monitoring_module（事件记录统一）
- [ ] SubTask 11.8: `metatest/test_positive_runtime_mode.py` — core.runtime_mode_module（_step_once_impl 统一、三模式）
- [ ] SubTask 11.9: `metatest/test_positive_schemas.py` — core.schemas（数据模型校验）
- [ ] SubTask 11.10: `metatest/test_positive_screening.py` — core.screening_module（filter_eval 表驱动、TickTable 读取）
- [ ] SubTask 11.11: `metatest/test_positive_table_engine.py` — core.table_engine（ConfigStore get_table/get_data_file、热加载）
- [ ] SubTask 11.12: `metatest/test_positive_tick_bar.py` — core.tick_bar_module（_on_step_event 统一、_publish_tick_batch、TickTable 适配）
- [ ] SubTask 11.13: `metatest/test_positive_trade.py` — core.trade_module（SignalDeriver/ActionDispatcher、信号驱动动作）
- [ ] SubTask 11.14: `metatest/test_positive_web_state.py` — core.web_state（Web 状态管理）
- [ ] SubTask 11.15: `metatest/test_positive_app.py` — app（FastAPI、get_simulator Depends、_SIM_ACTIONS 表、tdx CRUD 表驱动）
- [ ] SubTask 11.16: `metatest/test_positive_api.py` — api（require_config_store Depends、路由级挂载）
- [ ] SubTask 11.17: `metatest/test_positive_converters.py` — converters（dzh/tdx 转换、ConfigStore 加载）

### Task 12: 正测试集 - 前端 4 JS 模块

- [ ] SubTask 12.1: `metatest/test_positive_frontend_app.py` — web/js/app.js（核心应用逻辑、模块导出、关键函数存在性）
- [ ] SubTask 12.2: `metatest/test_positive_frontend_canvas.py` — web/js/canvas.js（画布渲染、节点/边绘制）
- [ ] SubTask 12.3: `metatest/test_positive_frontend_event_panel.py` — web/js/event-panel.js（renderEventCanvas 单一函数、_DRAW_LAYERS 表、_STYLE 配置、矩阵/散点布局）
- [ ] SubTask 12.4: `metatest/test_positive_frontend_ui.py` — web/js/ui.js（UI 组件、表驱动 action_table）
- [ ] SubTask 12.5: 前端测试采用静态分析（AST/正则验证 JS 文件结构）+ 可选 Playwright E2E（环境缺失计为失败）

### Task 13: 反测试集 - 异常与边界

- [ ] SubTask 13.1: `metatest/test_negative_config.py` — 异常配置（空备选池/缺字段/自环/孤点/重复边/无效 propagate_mode）
- [ ] SubTask 13.2: `metatest/test_negative_runtime.py` — 运行时异常（重复入池/TTL无持仓/公式错误/跨模块非法引用/水位线 hash 碰撞）
- [ ] SubTask 13.3: `metatest/test_negative_api_frontend.py` — API/前端异常（404/405/500/SSE断连/WebSocket错误/配置缺失/XSS）
- [ ] SubTask 13.4: `metatest/test_negative_logic.py` — 底层逻辑异常（编译失败/三要素调用深度超限/角色未注册/信号动作解耦失败/双 TickTable 残留/Compiler 残留）

### Task 14: 合测试集 - 端到端集成

- [ ] SubTask 14.1: `metatest/test_synthesis_simulation_full_flow.py` — 仿真全流程（备选池→A池→B池→C池→买入→TTL→卖出）
- [ ] SubTask 14.2: `metatest/test_synthesis_three_modes.py` — 三模式合测试（仿真/回放/实盘同代码路径）
- [ ] SubTask 14.3: `metatest/test_synthesis_import_export_roundtrip.py` — 导入导出 roundtrip（dzh/tdx/json 往返一致）
- [ ] SubTask 14.4: `metatest/test_synthesis_hot_reload.py` — 配置热加载合测试（修改配置表→引擎自动重载→行为变更）
- [ ] SubTask 14.5: `metatest/test_synthesis_meta_pattern_v3.py` — 元模式 v3 合并验证（迭代 13-19 共 7 项真同构合并正确性）
- [ ] SubTask 14.6: `metatest/test_synthesis_frontend_e2e.py` — 前端 E2E（Playwright，环境缺失计为失败）

## 阶段 4：量化评审与文档

### Task 15: 评审工程师 - 量化评分与 Grep 验证

- [ ] SubTask 15.1: 运行 `python -m metatest.runner`，确认总分 ≥ 95 且 8 维均 ≥ 80
- [ ] SubTask 15.2: 验证 report.json 的 `tests_passed` / `tests_total` 与 pytest 实际输出一致（非虚假数据）
- [ ] SubTask 15.3: Grep 验证 7 项真同构消除（每项 0 匹配）：
  - `class TickTable` 全局仅 1 处（core/tick_table.py）— 验证迭代 13
  - `class Compiler` / `class CompiledSchedule` 在 core/ 零匹配 — 验证迭代 14
  - `class TimingSpec` / `class FilterSpec` / `class PropagateSpec` / `class ActionSpec` / `class TTLSpec` 在 core/ 零匹配 — 验证迭代 15
  - `_PROPAGATE_STRATEGIES` 在 core/ 零匹配 — 验证迭代 16
  - `engine.py` 中 `def _resolve_node_type` / `def _resolve_edge_type` / `def _extract_edge_endpoint` 零匹配 — 验证迭代 17
  - `engine.py` 中 `def _resolve_pool_role` / `def _resolve_pool_role_compute` 零匹配 — 验证迭代 18
  - 6 个 `_eval_*_path` 函数仅被 `_EVAL_PATHS` 表与 `filter_eval()` 引用 — 验证迭代 19
- [ ] SubTask 15.4: 验证测试覆盖后端 17 模块 + 前端 4 JS 模块
- [ ] SubTask 15.5: 验证正反合三层方法论完整（正测试 ≥ 22 文件、反测试 ≥ 4 文件、合测试 ≥ 6 文件）
- [ ] SubTask 15.6: 验证 metatest/ 下 test_*.py 总数 ≥ 30

### Task 16: 文档更新

- [ ] SubTask 16.1: 更新 `RULES.md` 新增第 97-103 条（迭代 13-19 真同构合并约束）
- [ ] SubTask 16.2: 更新 `DESIGN.md` 元模式章节（记录 7 项真同构合并）
- [ ] SubTask 16.3: 更新 `metatest/README.md` 说明 v3 真实评分规则（无测试=FAIL）

# Task Dependencies

- 阶段 1：
  - Task 1（TickTable 合并）无依赖，先行
  - Task 2（compile 合并）依赖 Task 1（EdgeExecutor 引用 TickTable）
  - Task 3（Spec BaseModel 删除）依赖 Task 2（Compiler 已删）
  - Task 4（_SPEC_BUILDERS 表）依赖 Task 3
  - Task 5（propagate 合并）依赖 Task 4（spec 统一为 dict）
- 阶段 2：
  - Task 6（节点/边解析收敛）依赖 Task 2（CompiledPool 含字段）
  - Task 7（角色解析合并）依赖 Task 6
  - Task 8（_eval_*_path 表）依赖 Task 4（filter_eval 已用 dict spec）
- 阶段 3：
  - Task 9（runner 修复）无依赖，可与阶段 1/2 并行
  - Task 10（底层逻辑正测试）依赖 Task 9 + 阶段 1/2 完成
  - Task 11（后端正测试）依赖 Task 9
  - Task 12（前端正测试）依赖 Task 9
  - Task 13（反测试）依赖 Task 10/11/12
  - Task 14（合测试）依赖 Task 10/11/12/13
- 阶段 4：
  - Task 15（评审）依赖 Task 14
  - Task 16（文档）依赖 Task 15

# 并行度建议

| 阶段 | 可并行 Task | 说明 |
|---|---|---|
| 1 | Task 1 先行；Task 2 串行依赖 Task 1；Task 3/4 串行；Task 5 串行 | execution_module.py 单文件，避免冲突 |
| 2 | Task 6/7 串行（同 engine.py）；Task 8 可与 Task 6/7 并行（不同函数区） | 需谨慎合并 |
| 3 | Task 9 先行；Task 10/11/12 部分并行（不同测试文件） | 测试文件独立 |
| 4 | Task 15/16 串行 | Task 16 依赖 Task 15 验证通过 |

# 迭代优先级

| 迭代 | 优先级 | 任务数 | 影响范围 | 真同构洞察 |
|---|---|---|---|---|
| 13 | CRITICAL | 1 | tick_table.py + execution_module.py | 单一真相源 |
| 14 | CRITICAL | 1 | execution_module.py | 一次性产出 |
| 15 | CRITICAL | 2 | execution_module.py | 表驱动消除双路径 |
| 16 | CRITICAL | 1 | execution_module.py + propagate_modes.json | 单一分派表 |
| 17 | HIGH | 1 | engine.py + execution_module.py | 运行时零解析 |
| 18 | MEDIUM | 1 | engine.py + execution_module.py | 单一角色映射 |
| 19 | MEDIUM | 1 | execution_module.py | 求值路径同构 |
| metatest v3 | HIGH | 8 | metatest/ 全量重建 | 真实量化评分 |
