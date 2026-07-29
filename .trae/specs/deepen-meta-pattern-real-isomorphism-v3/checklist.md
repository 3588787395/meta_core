# Checklist — 元模式彻底完善 v3 量化评审检查点

本检查点由评审工程师逐项验证，每项必须基于**真实代码核查**与**真实测试结果**，禁止信用分。

## 阶段 1：CRITICAL 真同构合并验证

### 迭代 13：双 TickTable 合并

- [ ] `core/tick_table.py:TickTable` 含 `update()` / `get()` / `snapshot()` / `column()` / `prev_column()` / `bar_hash()` / `bind_prev()` 全部方法
- [ ] `core/execution_module.py` 中无 `class TickTable` 定义（Grep `class TickTable` 全局仅 `core/tick_table.py` 1 处）
- [ ] `EdgeExecutor._tick_table` 引用 `core/tick_table.py:TickTable`（非本地定义）
- [ ] `tick_table.column(code, "close")` 与 `tick_table.prev_column(code, "close")` 行为等价于合并前视图版
- [ ] `tick_table.update(same_data)` 返回 False；`tick_table.update(diff_data)` 返回 True

### 迭代 14：双 compile 系统合并

- [ ] `core/execution_module.py` 中无 `class Compiler` 定义（Grep 零匹配）
- [ ] `core/execution_module.py` 中无 `class CompiledSchedule` 定义（Grep 零匹配）
- [ ] `core/` 中无 `CompiledSchedule` 引用（全局 Grep 零匹配，全部改为 `CompiledPool`）
- [ ] `CompiledPool` 含原 `CompiledSchedule` 全部字段（timing/filter/propagate/action/ttl specs + edge_order + node_role）
- [ ] `compile(pool_config) -> CompiledPool` 为唯一编译入口
- [ ] `EdgeExecutor.__init__` 参数为 `compiled: CompiledPool`（非 `schedule: CompiledSchedule`）
- [ ] 引擎运行结果与合并前行为等价

### 迭代 15：三对 spec 构建器合并

- [ ] `core/execution_module.py` 中无 `class TimingSpec` / `class FilterSpec` / `class PropagateSpec` / `class ActionSpec` / `class TTLSpec` 定义（Grep 零匹配）
- [ ] `core/execution_module.py` 中无 `def _build_timing_spec` / `def _build_filter_spec` / `def _build_propagate_spec` 方法（Grep 零匹配）
- [ ] `_SPEC_BUILDERS` dict 定义存在，含 `{"timing": ..., "filter": ..., "propagate": ...}` 三键
- [ ] `compile()` 函数内通过 `_SPEC_BUILDERS[kind](edge)` 查表分派构建 spec
- [ ] spec 统一为 dict 形态（`spec["field"]` 访问，无 `spec.field` 属性访问）

### 迭代 16：三套 propagate 派发合并

- [ ] `config/architecture/propagate_modes.json` 含 4 模式（copy/move/overwrite/overwrite_copy）
- [ ] `core/execution_module.py` 中无 `_PROPAGATE_STRATEGIES` 定义（Grep 零匹配）
- [ ] `_PROPAGATE_MODES` 由 `propagate_modes.json` 配置驱动初始化，含 4 模式
- [ ] `_PROPAGATE_MODES` 每模式含 `(tgt_strategy_fn, src_strategy_fn)` 两策略元组
- [ ] `propagate_apply()` 与 `EdgeExecutor._propagate()` 共用 `_PROPAGATE_MODES` 表
- [ ] 4 种 propagate 模式执行结果与合并前行为等价

## 阶段 2：HIGH/MEDIUM 真同构合并验证

### 迭代 17：节点/边类型解析收敛

- [ ] `core/engine.py` 中无 `def _resolve_node_type` 方法定义（Grep 零匹配）
- [ ] `core/engine.py` 中无 `def _resolve_edge_type` 方法定义（Grep 零匹配）
- [ ] `core/engine.py` 中无 `def _extract_edge_endpoint` 方法定义（Grep 零匹配）
- [ ] `core/execution_module.py` 保留编译期 `_resolve_node_type` / `_resolve_edge_type` / `_extract_edge_endpoint` 函数
- [ ] `CompiledPool` 含 `node_role` / `edge_type` / 端点解析字段
- [ ] `engine.py` 运行时从 `CompiledPool` 读取预编译结果（非运行时解析）

### 迭代 18：角色解析三函数合并

- [ ] `core/engine.py` 中无 `def _resolve_pool_role` 方法定义（Grep 零匹配）
- [ ] `core/engine.py` 中无 `def _resolve_pool_role_compute` 方法定义（Grep 零匹配）
- [ ] `core/execution_module.py` 保留编译期 `_resolve_node_role(node)` 函数
- [ ] `CompiledPool.node_role: Dict[nid→role]` 由编译期产出
- [ ] `engine.py` 运行时从 `compiled.node_role.get(nid)` 读取角色
- [ ] 5 种角色（candidate/state/condition/target/discard）判定结果与合并前一致

### 迭代 19：_eval_*_path 六路径表驱动

- [ ] `_EVAL_PATHS` dict 定义存在，含 6 键（set_op/pass_through/formula/scalar/set_op_path/intersection）
- [ ] `filter_eval()` 通过 `path_kind = filter_spec["nset_type"]` 查 `_EVAL_PATHS` 分派
- [ ] 6 个 `_eval_*_path` 函数调用点仅出现在 `_EVAL_PATHS` 表定义与 `filter_eval()` 内部（无外部直接调用）
- [ ] 6 种 nset 类型求值结果与合并前一致

## 阶段 3：metatest v3 真实落地验证

### runner.py 量化评分真实化

- [ ] `metatest/runner.py` 中无 `if no_tests: return 0` 直接返回 PASS 的逻辑
- [ ] 无测试文件时 `runner.py` 返回总分 = 0（FAIL）
- [ ] 旧虚假 `metatest/report.json` 已删除
- [ ] 新 `report.json` 的 `tests_passed` / `tests_total` 与 pytest 实际输出一致

### 测试文件真实存在

- [ ] `metatest/` 下 `test_*.py` 文件总数 ≥ 30
- [ ] 正测试集 `test_positive_*.py` ≥ 22 文件（5 底层逻辑 + 17 后端模块 + 4 前端，可合并）
- [ ] 反测试集 `test_negative_*.py` ≥ 4 文件
- [ ] 合测试集 `test_synthesis_*.py` ≥ 6 文件

### 测试覆盖全面性

- [ ] 后端 17 核心模块覆盖：core.domain / core.engine / core.event_bus / core.execution_module / core.formula_module / core.import_export_module / core.monitoring_module / core.runtime_mode_module / core.schemas / core.screening_module / core.table_engine / core.tick_bar_module / core.trade_module / core.web_state / app / api / converters
- [ ] 前端 4 JS 模块覆盖：web/js/app.js / canvas.js / event-panel.js / ui.js
- [ ] 5 项底层逻辑验证：水位线 / 编译-运行分离 / 三要素 / 角色表 / 正交化
- [ ] 17 功能点覆盖：三模式 / 池设计器 / 事件引擎 / 公式 / K线 / 交易 / 导入导出 / 热加载 / 事件面板 / HTTP / SSE / WS / 数据源 / 校验器 / 原生动作 / 存储 / 备选池转移

### 量化评分真实性

- [ ] 运行 `python -m metatest.runner` 总分 ≥ 95
- [ ] 8 维分数均 ≥ 80（模块覆盖率/测试通过率/断言密度/事件链完整性/性能基准/前端E2E/底层逻辑覆盖度/同构代码消除度）
- [ ] report.json 非虚假数据（tests_total > 0，与 pytest 输出一致）
- [ ] 跳过测试计为失败（skipped 计入扣分）
- [ ] 前端 E2E 环境缺失计为失败（不再给信用分）

## 阶段 4：文档更新验证

- [ ] `RULES.md` 含第 97-103 条（迭代 13-19 真同构合并约束）
- [ ] `DESIGN.md` 元模式章节记录 7 项真同构合并
- [ ] `metatest/README.md` 说明 v3 真实评分规则（无测试=FAIL）

## 最终回归验证

- [ ] `python -m pytest metatest/ -v` 全部通过（或失败项有明确扣分记录）
- [ ] `python -m metatest.runner` 退出码 0（PASS）且总分 ≥ 95
- [ ] 7 项真同构消除 Grep 验证全部 0 匹配（见阶段 1/2 各项）
- [ ] 引擎行为等价性：合并前后同输入同输出
