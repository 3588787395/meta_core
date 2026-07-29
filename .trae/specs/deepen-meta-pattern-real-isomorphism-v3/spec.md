# 元模式彻底完善 v3：真同构合并与严格正反合测试重建 Spec

## Why

前一阶段 `deepen-meta-pattern-strict-metatest-v2` 声称完成迭代 7-12 并重建 metatest v2，report.json 记录「796/796 通过 / 100.00 分」。但**底层运行逻辑核查揭露两大根本缺陷**：

1. **metatest/ 目录下 0 个 test_*.py 文件**。report.json 的「796/796 通过、54 文件、1788 断言」是陈旧/虚假数据——`runner.py` 在 `no_tests=True` 时直接返回 0（PASS），评分完全脱离真实测试结果。这正是用户所斥「随意评分」。
2. **声称已合并的同构代码实际仍并行存在**。架构洞察发现 7 类**真正同构**模式尚未合并（非皮毛、非拆分），违反 RULES.md「G2 硬约束同代码」「单一真相源」「编译-运行分离」「所有分派用 dict」。

本次迭代基于**底层运行逻辑洞察**，把这 7 类真同构代码合并为元模式（**合并而非拆分**），并从零重建严格正反合测试，使量化评分**完全由真实测试结果驱动**，按「架构工程师 → 评审工程师」流程分轮迭代。

## What Changes

### 迭代 13：双 TickTable 同名异构合并（CRITICAL，底层洞察：单一真相源）

- `core/tick_table.py:TickTable`（水位线版：`update/get/snapshot/hash`，57 行）与 `core/execution_module.py:2630:TickTable`（视图版：`column/prev_column/bar_hash`，25 行）**同名异构**，违反单一真相源
- 合并为单一 `TickTable` 类（位于 `core/tick_table.py`），同时承担：
  - 水位线检测（`update(tick_data) -> bool`，hash 比较）
  - 列视图读取（`column(code, col)` / `prev_column(code, col)`，绑定 `latest_tick`/`prev_tick` 引用）
- **BREAKING**：删除 `execution_module.py:2630` 的 `TickTable` 类，`EdgeExecutor._tick_table` 改为引用 `core/tick_table.py:TickTable`
- 水位线与视图统一后，引擎核心循环只持有一个 TickTable 实例，`update()` 返回 False 时短路，`column()` 读取零开销

### 迭代 14：双 compile 系统合并（CRITICAL，底层洞察：编译-运行分离一次性产出）

- `execution_module.py` 内存在**两套并行编译系统**：
  - 老路径：`class Compiler` (859) → `Compiler.compile()` (1182) → `CompiledSchedule` (425, BaseModel)
  - 新路径：`def compile(pool_config)` (1389) → `CompiledPool` (1364)
- 两套产出并存，违反「一次性产出」洞察。合并为**单一 `compile(pool_config) -> CompiledPool`**：
  - `CompiledPool` 吸收 `CompiledSchedule` 的全部字段（timing/filter/propagate/action/ttl specs + edge_order + node_role）
  - `Compiler` 类的 `_build_timing_spec` / `_build_filter_spec` / `_build_propagate_spec` 方法迁移为 `compile()` 内部步骤或合并到 `_compile_*_spec` 函数
- **BREAKING**：删除 `class Compiler` 与 `CompiledSchedule`；所有引用 `CompiledSchedule` 的代码改为引用 `CompiledPool`
- `EdgeExecutor.__init__` 参数 `schedule: CompiledSchedule` 改为 `compiled: CompiledPool`

### 迭代 15：三对 spec 构建器合并（CRITICAL，底层洞察：表驱动消除双路径）

- `execution_module.py` 内**三对并行 spec 构建器**：
  - `Compiler._build_timing_spec` (907, → `TimingSpec` BaseModel) vs `_compile_timing_spec` (1330, → dict)
  - `Compiler._build_filter_spec` (962, → `FilterSpec` BaseModel) vs `_compile_filter_spec` (1342, → dict)
  - `Compiler._build_propagate_spec` (1135, → `PropagateSpec` BaseModel) vs `_compile_propagate_spec` (1354, → dict)
- 合并为**单一 `_compile_spec(edge, kind) -> dict`** 表驱动函数：
  - `_SPEC_BUILDERS = {"timing": _compile_timing_spec, "filter": _compile_filter_spec, "propagate": _compile_propagate_spec}`
  - 三种 spec 统一为 `dict` 形态（删除 `TimingSpec`/`FilterSpec`/`PropagateSpec`/`ActionSpec`/`TTLSpec` BaseModel，运行时仅读 dict）
- **BREAKING**：5 个 Spec BaseModel 类删除；`Compiler._build_*_spec` 方法删除；仅保留 `_compile_*_spec` 函数 + `_SPEC_BUILDERS` 表

### 迭代 16：三套 propagate 派发合并（CRITICAL，底层洞察：单一分派表）

- `execution_module.py` 内**三套并行 propagate 派发**：
  - `_PROPAGATE_MODES` dict (1547, 3 模式, `propagate_apply()` 函数用)
  - `_PROPAGATE_STRATEGIES` dict (2622, 4 模式含 `overwrite_copy`, `EdgeExecutor._propagate()` 用)
  - `config/architecture/propagate_modes.json` (3 模式, 配置描述)
- 三套并存，违反「所有分派用 dict」与配置驱动原则。合并为**单一 `_PROPAGATE_MODES` 表**：
  - `_PROPAGATE_MODES` 由 `propagate_modes.json` 配置驱动初始化（含 src_action / tgt_action 两策略元组）
  - 补齐 `overwrite_copy` 模式到 `propagate_modes.json`（4 模式对齐）
  - 删除 `_PROPAGATE_STRATEGIES`，`EdgeExecutor._propagate()` 改为查 `_PROPAGATE_MODES`
  - `propagate_apply()` 函数与 `EdgeExecutor._propagate()` 共用同一分派表
- **BREAKING**：`_PROPAGATE_STRATEGIES` 删除；`propagate_modes.json` 扩展为 4 模式

### 迭代 17：节点/边类型解析收敛到编译期（HIGH，底层洞察：运行时零解析）

- `engine.py` 运行时仍存在**与 `execution_module.py` 重复的解析方法**：
  - `engine.py:1560 _resolve_node_type(self, n)` vs `execution_module.py:465 _resolve_node_type(node)`
  - `engine.py:1597 _resolve_edge_type(self, source_type)` vs `execution_module.py:526 _resolve_edge_type(src_type)`
  - `engine.py:1583 _extract_edge_endpoint(self, edge, *keys)` vs `execution_module.py:450 _extract_edge_endpoint(edge, keys)`
- 收敛为**编译期单一实现**：
  - `execution_module.py` 的三个函数作为编译期唯一实现，产出存入 `CompiledPool.node_role` / `CompiledPool.edge_type` / 端点解析
  - `engine.py` 运行时仅从 `CompiledPool` 读取，删除三个 `_resolve_*` / `_extract_edge_endpoint` 方法
- **BREAKING**：`engine.py` 的 `_resolve_node_type` / `_resolve_edge_type` / `_extract_edge_endpoint` 方法删除

### 迭代 18：角色解析三函数合并（MEDIUM，底层洞察：单一角色映射）

- `engine.py` 内**三个角色解析函数同构**：
  - `_resolve_pool_role(self, nid)` (1701)
  - `_resolve_pool_role_compute(self, nid)` (1716)
  - `execution_module.py:1283 _resolve_node_role(node)`
- 合并为**编译期 `_resolve_node_role(node)` 单一实现**，产出存入 `CompiledPool.node_role: Dict[nid→role]`
- 运行时 `engine.py` 从 `compiled.node_role[nid]` 读取，删除 `_resolve_pool_role` / `_resolve_pool_role_compute`
- **BREAKING**：`engine.py` 的 `_resolve_pool_role` / `_resolve_pool_role_compute` 方法删除

### 迭代 19：_eval_*_path 六路径表驱动收敛（MEDIUM，底层洞察：求值路径同构）

- `execution_module.py` 内**六个求值路径函数同构**（2199-2562）：
  - `_eval_set_operation` / `_eval_pass_through` / `_eval_formula_path` / `_eval_scalar_path` / `_eval_set_op_path` / `_eval_intersection_path`
- 骨架同构：`codes → ctx → result: Dict[code→bool]`，仅求值核心差异
- 合并为**单一 `_eval_path(codes, ctx, path_kind) -> Dict` + `_EVAL_PATHS` 表**：
  - `_EVAL_PATHS = {"set_op": _eval_set_operation, "pass_through": _eval_pass_through, "formula": _eval_formula_path, "scalar": _eval_scalar_path, "set_op_path": _eval_set_op_path, "intersection": _eval_intersection_path}`
  - `filter_eval()` 通过 `path_kind = filter_spec["nset_type"]` 查表分派
- **BREAKING**：六个函数不再被外部直接调用，仅通过 `_EVAL_PATHS` 表分派

### 重建 metatest v3（严格正反合测试 + 真实量化评分）

- **删除虚假 report.json**，从零创建测试文件
- **正测试集**（验证功能正确性）：覆盖股票池平台**前端后端所有模块**：
  - 后端 17 个核心模块（core.* + app + api + converters）
  - 前端 4 个 JS 模块（app.js / canvas.js / event-panel.js / ui.js）
  - 5 项底层逻辑验证（水位线/编译-运行分离/三要素/角色表/正交化）
  - 17 个功能点正测试（三模式/池设计器/事件引擎/公式/K线/交易/导入导出/热加载/事件面板/HTTP/SSE/WS/数据源/校验器/原生动作/存储/备选池转移）
- **反测试集**（验证异常与边界）：异常配置/运行时异常/API前端异常/底层逻辑异常
- **合测试集**（验证端到端集成）：仿真全流程/三模式/导入导出roundtrip/热加载/元模式合并验证/前端E2E
- **量化评分 v3**：8 维评分**完全由真实测试结果计算**：
  - 删除 `no_tests=True` 直接返回 PASS 的逻辑
  - 无测试文件时总分 = 0（FAIL）
  - 测试运行失败/跳过均计入扣分
  - 评分门槛 ≥ 95 且 8 维均 ≥ 80
- **测试必须真实通过**（跳过计为失败，前端 E2E 环境缺失计为失败）

## Impact

- Affected specs:
  - `RULES.md` 第 91-96 条（迭代 7-12）→ 新增第 97-103 条（迭代 13-19）
  - `DESIGN.md` 元模式章节扩展（真同构合并记录）
  - `deepen-meta-pattern-strict-metatest-v2` 的 metatest v2 重建实际未落地，本次 v3 真正落地
- Affected code:
  - `core/tick_table.py` — 迭代 13（吸收视图版 TickTable 方法）
  - `core/execution_module.py` — 迭代 13（删 TickTable 类）/ 14（删 Compiler + CompiledSchedule）/ 15（删 5 Spec BaseModel + 3 _build_*_spec）/ 16（删 _PROPAGATE_STRATEGIES）/ 17（保留编译期解析）/ 18（保留 _resolve_node_role）/ 19（_EVAL_PATHS 表）
  - `core/engine.py` — 迭代 17（删 _resolve_node_type/_resolve_edge_type/_extract_edge_endpoint）/ 18（删 _resolve_pool_role/_resolve_pool_role_compute）
  - `config/architecture/propagate_modes.json` — 迭代 16（新增 overwrite_copy 模式）
  - `metatest/` — 全量重建（删除虚假 report.json，创建 test_*.py 文件）

## ADDED Requirements

### Requirement: 双 TickTable 合并为单一真相源

系统 SHALL 将 `core/tick_table.py:TickTable`（水位线版）与 `core/execution_module.py:TickTable`（视图版）合并为单一类，同时承担水位线检测与列视图读取。

#### Scenario: 水位线检测
- **WHEN** 行情推送调用 `tick_table.update(tick_data)`
- **THEN** 比较 hash，未涨返回 False，涨了更新 ts 并返回 True
- **AND** 引擎核心循环检测 False 后短路零计算

#### Scenario: 列视图读取
- **WHEN** 边执行调用 `tick_table.column(code, "close")`
- **THEN** 返回 latest_tick 中该 code 的 close 值
- **AND** 调用 `tick_table.prev_column(code, "close")` 返回 prev_tick 值

#### Scenario: 单一实例
- **WHEN** Grep 搜索 `class TickTable`
- **THEN** 仅 `core/tick_table.py` 一处定义
- **AND** `execution_module.py` 中无 `class TickTable` 定义

### Requirement: 双 compile 系统合并为单一 compile → CompiledPool

系统 SHALL 将 `class Compiler` + `CompiledSchedule` 老路径与 `def compile` + `CompiledPool` 新路径合并为单一编译入口。

#### Scenario: 单一编译入口
- **WHEN** 池配置加载
- **THEN** 调用 `compile(pool_config) -> CompiledPool` 唯一入口
- **AND** `CompiledPool` 含原 `CompiledSchedule` 的全部字段（timing/filter/propagate/action/ttl specs + edge_order + node_role）

#### Scenario: 老路径删除
- **WHEN** Grep 搜索 `class Compiler` / `class CompiledSchedule`
- **THEN** `execution_module.py` 中零匹配
- **AND** 所有引用 `CompiledSchedule` 的代码改为 `CompiledPool`

### Requirement: 三对 spec 构建器合并为表驱动

系统 SHALL 将三对并行 spec 构建器（`_build_*_spec` 方法 vs `_compile_*_spec` 函数）合并为单一 `_SPEC_BUILDERS` 表驱动。

#### Scenario: 表驱动分派
- **WHEN** 编译期需要构建某类 spec
- **THEN** 调用 `_SPEC_BUILDERS[kind](edge)` 查表分派
- **AND** `kind ∈ {"timing", "filter", "propagate"}`

#### Scenario: Spec BaseModel 删除
- **WHEN** Grep 搜索 `class TimingSpec` / `class FilterSpec` / `class PropagateSpec` / `class ActionSpec` / `class TTLSpec`
- **THEN** `execution_module.py` 中零匹配
- **AND** spec 统一为 dict 形态

### Requirement: 三套 propagate 派发合并为单一 _PROPAGATE_MODES 表

系统 SHALL 将 `_PROPAGATE_MODES` / `_PROPAGATE_STRATEGIES` / `propagate_modes.json` 三套派发合并为单一 `_PROPAGATE_MODES` 表，由 `propagate_modes.json` 配置驱动。

#### Scenario: 配置驱动初始化
- **WHEN** 模块加载
- **THEN** `_PROPAGATE_MODES` 由 `propagate_modes.json` 初始化（含 4 模式：copy/move/overwrite/overwrite_copy）
- **AND** 每模式含 `(tgt_strategy, src_strategy)` 两策略元组

#### Scenario: 单一分派表
- **WHEN** Grep 搜索 `_PROPAGATE_STRATEGIES`
- **THEN** `core/` 中零匹配
- **AND** `propagate_apply()` 与 `EdgeExecutor._propagate()` 共用 `_PROPAGATE_MODES`

### Requirement: 节点/边类型解析收敛到编译期

系统 SHALL 将 `engine.py` 运行时的 `_resolve_node_type` / `_resolve_edge_type` / `_extract_edge_endpoint` 方法删除，运行时仅从 `CompiledPool` 读取预编译结果。

#### Scenario: 运行时零解析
- **WHEN** Grep 搜索 `engine.py` 中 `def _resolve_node_type` / `def _resolve_edge_type` / `def _extract_edge_endpoint`
- **THEN** 零匹配
- **AND** `execution_module.py` 中保留编译期单一实现

### Requirement: 角色解析三函数合并为编译期单一实现

系统 SHALL 将 `engine.py` 的 `_resolve_pool_role` / `_resolve_pool_role_compute` 与 `execution_module.py` 的 `_resolve_node_role` 合并为编译期 `_resolve_node_role` 单一实现。

#### Scenario: 编译期产出角色映射
- **WHEN** `compile(pool_config)` 执行
- **THEN** 产出 `CompiledPool.node_role: Dict[nid→role]`
- **AND** 运行时 `engine.py` 从 `compiled.node_role[nid]` 读取

#### Scenario: 运行时角色解析方法删除
- **WHEN** Grep 搜索 `engine.py` 中 `def _resolve_pool_role` / `def _resolve_pool_role_compute`
- **THEN** 零匹配

### Requirement: _eval_*_path 六路径表驱动收敛

系统 SHALL 将六个求值路径函数收敛为 `_EVAL_PATHS` 表驱动，`filter_eval()` 通过 `path_kind` 查表分派。

#### Scenario: 表驱动求值
- **WHEN** `filter_eval()` 需要求值某 nset 类型
- **THEN** 查 `_EVAL_PATHS[path_kind]` 得到求值函数
- **AND** `path_kind ∈ {"set_op", "pass_through", "formula", "scalar", "set_op_path", "intersection"}`

#### Scenario: 六函数不再被外部直接调用
- **WHEN** Grep 搜索 `_eval_set_operation(` / `_eval_pass_through(` 等六函数调用
- **THEN** 仅 `filter_eval()` 与 `_EVAL_PATHS` 表内引用
- **AND** 无外部直接调用

### Requirement: 严格正反合测试 v3 真实落地

系统 SHALL 在 `metatest/` 目录下创建真实测试文件，量化评分完全由真实测试结果计算。

#### Scenario: 测试文件真实存在
- **WHEN** 列举 `metatest/test_*.py`
- **THEN** 含正测试集（test_positive_*.py）+ 反测试集（test_negative_*.py）+ 合测试集（test_synthesis_*.py）
- **AND** 总数 ≥ 30 个测试文件

#### Scenario: 无测试文件时 FAIL
- **WHEN** `metatest/` 下无 test_*.py 文件
- **THEN** `runner.py` 返回总分 = 0（FAIL）
- **AND** 不再直接返回 PASS

#### Scenario: 量化评分真实计算
- **WHEN** 运行 `python -m metatest.runner`
- **THEN** 8 维分数由 pytest 真实结果 + Grep 真实验证计算
- **AND** report.json 的 `tests_passed` / `tests_total` 与 pytest 实际输出一致

#### Scenario: 全模块覆盖
- **WHEN** 测试运行完成
- **THEN** 覆盖后端 17 个核心模块（core.* + app + api + converters）
- **AND** 覆盖前端 4 个 JS 模块（app.js / canvas.js / event-panel.js / ui.js）

## MODIFIED Requirements

### Requirement: RULES.md 架构合同扩展（第 97-103 条）

在 RULES.md 第 91-96 条（迭代 7-12）基础上，新增第 97-103 条：

- 97. **TickTable 合并为单一类**：禁止 `execution_module.py` 重新定义 `class TickTable`；水位线检测与列视图读取统一到 `core/tick_table.py:TickTable`
- 98. **compile 合并为单一 `compile(pool_config) -> CompiledPool`**：禁止重新引入 `class Compiler` / `class CompiledSchedule` 双路径
- 99. **spec 构建器合并为 `_SPEC_BUILDERS` 表**：禁止重新引入 `_build_*_spec` 方法与 `_compile_*_spec` 函数双路径；spec 统一为 dict
- 100. **propagate 派发合并为单一 `_PROPAGATE_MODES` 表**：禁止重新引入 `_PROPAGATE_STRATEGIES`；`propagate_modes.json` 为唯一配置源
- 101. **节点/边类型解析仅编译期执行**：禁止 `engine.py` 运行时重新定义 `_resolve_node_type` / `_resolve_edge_type` / `_extract_edge_endpoint`
- 102. **角色解析仅编译期 `_resolve_node_role`**：禁止 `engine.py` 运行时重新定义 `_resolve_pool_role` / `_resolve_pool_role_compute`
- 103. **求值路径表驱动 `_EVAL_PATHS`**：禁止 `_eval_*_path` 六函数被外部直接调用；统一通过 `_EVAL_PATHS[path_kind]` 分派

### Requirement: metatest 量化评分真实化

修改 `metatest/runner.py`：
- 删除 `no_tests=True` 直接返回 0（PASS）的逻辑（行 728-729）
- 无测试文件时总分 = 0（FAIL）
- report.json 的测试统计必须与 pytest 实际输出一致
- 删除虚假 report.json，由真实运行重新生成

## REMOVED Requirements

### Requirement: `execution_module.py:TickTable` 视图版类
**Reason**: 与 `core/tick_table.py:TickTable` 同名异构，违反单一真相源
**Migration**: 合并到 `core/tick_table.py:TickTable`，新增 `column()` / `prev_column()` / `bind_prev()` 方法

### Requirement: `class Compiler` 与 `class CompiledSchedule`
**Reason**: 与 `def compile` + `CompiledPool` 双路径并行，违反一次性产出洞察
**Migration**: `CompiledPool` 吸收 `CompiledSchedule` 字段；`Compiler._build_*_spec` 迁移为 `_compile_*_spec` 函数

### Requirement: `TimingSpec` / `FilterSpec` / `PropagateSpec` / `ActionSpec` / `TTLSpec` 五个 BaseModel
**Reason**: 与 `_compile_*_spec` 函数产出 dict 双形态并行，违反表驱动统一
**Migration**: spec 统一为 dict；`_SPEC_BUILDERS` 表驱动分派

### Requirement: `_PROPAGATE_STRATEGIES` dict
**Reason**: 与 `_PROPAGATE_MODES` + `propagate_modes.json` 三套派发并存
**Migration**: 合并到 `_PROPAGATE_MODES`（由 `propagate_modes.json` 驱动，补齐 `overwrite_copy`）

### Requirement: `engine.py` 的 `_resolve_node_type` / `_resolve_edge_type` / `_extract_edge_endpoint` / `_resolve_pool_role` / `_resolve_pool_role_compute` 五个运行时方法
**Reason**: 与 `execution_module.py` 编译期实现重复，违反运行时零解析
**Migration**: 运行时从 `CompiledPool` 读取预编译结果

### Requirement: metatest 虚假 report.json
**Reason**: 声称 796/796 通过但实际 0 个测试文件，评分不真实
**Migration**: 删除后由真实测试运行重新生成
