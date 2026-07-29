# 元模式深层收敛与严格正反合测试 v2 Spec

## Why

前一阶段 `perfect-meta-pattern-iteration` 完成 6 轮表层同构代码合并（同步/异步双路径、公式引擎协议化、HTTP Depends、配置加载统一、中等优先级批次、低优先级收尾），共 21 项实施任务。但用户明确指出这些重构「无关痛痒」，要的是**底层运行逻辑的洞察，真正同构代码的合并**。

`docs/deep_refactoring_plan/spec.md` 与 `docs/meta-core-essence-mapping/spec.md` 已沉淀出两个核心底层洞察：

1. **水位线洞察**：`latest_tick_ts` 是系统灵魂。水位线不变 → 所有 K 线数据不变 → 所有公式结果不变 → 所有过滤结果不变 → 零计算。当前代码每 tick 重算拓扑、重解析参数、全量遍历节点边，违反此洞察。
2. **编译-运行分离洞察**：股票池 = 有向图上的流计算。节点装股票，边定义流转。运行前编译为 `CompiledPool`，运行时只读不解析。当前代码把「运行前确定的东西」与「运行时变化的东西」搅在一起，导致 `engine.py` 3369 行（设计目标 420 行，超标 8x）。

同时，前一阶段 `create-metatest-comprehensive-validation` 虽得分 100/100，但存在三大缺陷：
- 前端 E2E 测试因环境缺失被跳过时给予 100% 信用分（非真实通过）
- 未覆盖水位线、编译-运行分离、边执行三要素等底层逻辑
- 量化评分维度不足以反映「真正同构代码合并」的深度

本次迭代将基于上述底层洞察，开展第 7-12 轮深层元模式收敛，并重建严格正反合测试 v2，以真实量化评分驱动迭代。

## What Changes

### 迭代 7：latest_tick 水位线表统一（高优先级，底层洞察 1）

- 在 `core/runtime_mode_module.py` 或新建 `core/runtime/tick_table.py` 中定义 `TickTable` 类，封装 `data: Dict[code→bar]`、`ts: float`（水位线）、`hash: int`（全量哈希）
- `update(tick_data) -> bool` 方法：比较新 hash 与旧 hash，水位线未涨返回 False，涨了更新 `ts` 并返回 True
- 将散落于 `runtime_mode_module.py`、`tick_bar_module.py`、`engine.py`、`screening_module.py` 中所有 `latest_tick[code] = ...` 直写改为 `tick_table.update(...)`
- **BREAKING**：所有读取 `state.latest_tick[code]` 的代码改为 `tick_table.get(code)` 或 `tick_table.snapshot()`
- 水位线不变时，引擎核心循环直接返回空事件列表，零计算

### 迭代 8：编译-运行分离统一（高优先级，底层洞察 2）

- 在 `core/execution_module.py` 中（或新建 `core/compiler.py`）定义 `compile(pool_config) -> CompiledPool` 函数
- `CompiledPool` 一次性产出：节点字典、边字典、端点解析、邻接表、源节点列表、边类型判定、边规格编译（timing_spec / filter_spec / propagate_spec）、节点角色映射
- **执行顺序从 `edge.params._order` 读取**，不是运行时拓扑排序（与硬约束「运行时事件无序，删除 execution_order」对齐：`_order` 是设计期结构，运行时仅按 `_order` 列表遍历，不做拓扑排序）
- 运行时核心循环仅读 `CompiledPool`，零解析
- **BREAKING**：`engine.py` 中所有运行时解析逻辑（节点类型判定、边参数解析、邻接表构建）迁移到编译期

### 迭代 9：边执行三要素表驱动（高优先级，违反 RULES.md 第 16 条）

- 将当前 7-8 层调用深度（`_execute_flowsCore → _build_processing_plan → _process_edge_pipeline → _phase_dispatch → _phase_nset_filter → _dispatch_filter → _eval_primitive → _extract_prim_params_table → _extract_single_param`）收敛为 3 层：`trigger_check → filter_eval → propagate_apply`
- `trigger_check(edge_timing_spec, now_ts, flow_state, node_dirty) -> bool`：时间触发 AND 节点脏
- `filter_eval(codes, filter_spec, tick_table) -> (passed, rejected)`：nset 求值器 × noperate 比较器笛卡尔积
- `propagate_apply(src_stocks, tgt_stocks, passed, propagate_spec) -> new_tgt_stocks`：copy/move/overwrite 三模式
- **BREAKING**：`_phase_dispatch` / `_phase_nset_filter` / `_dispatch_filter` / `_eval_primitive` / `_extract_prim_params_table` / `_extract_single_param` 6 层中间函数删除

### 迭代 10：节点角色表驱动（中优先级，违反 RULES.md 第 16 条）

- 在 `config/architecture/node_roles.json` 中定义 5 种角色（candidate/state/condition/target/discard）的触发与动作
- 角色表结构：`{role: {on_enter: [actions], on_exit: [actions]}}`
- 当前 `if node.type == 'target': 发 ENTER 事件 + BUY 信号` 等散落判定收敛为 `_ROLE_ACTIONS[role][event_type]` 查表分派
- **BREAKING**：`engine.py` 中 `if node.type == ...` 链全部删除

### 迭代 11：事件-信号-动作正交化（中优先级，违反 RULES.md 第 24 条）

- 将三层概念正交化：
  - **事件**（Event）：状态变化的客观记录（入池/出池/TTL淘汰），由 `node_stocks` 新旧差集计算
  - **信号**（Signal）：基于角色规则从事件派生（ENTER→BUY、EXIT→SELL），由 `node_roles.json` 配置驱动
  - **动作**（Action）：信号触发的副作用（声音/弹窗/TDX板块/历史保存），由 `config/ui/action_table.json` 表驱动
- 当前 `transfer_executed → 直接调用 sound.play() / show_popup()` 的耦合拆分为 `event → signal → action` 三阶段
- **BREAKING**：`transfer_module` 中直接副作用调用全部改为发布信号事件，由 ActionDispatcher 表驱动执行

### 迭代 12：配置表收敛与死表清理（低优先级，违反 RULES.md 第 81 条）

- 审计 94 张配置表，识别 ~40 张死表（0 引用）
- 死表归档到 `config/_archive/` 目录，从运行时加载路径移除
- 核心运行时表收敛到 8 张：`timing.json` / `filter_specs.json` / `propagate_modes.json` / `node_roles.json` / `edge_semantics.json` / `runtime_modes.json` / `alert_rules.json` / `ttl_rules.json`
- 外围表（UI/导入导出/后处理）保留但标注用途
- **BREAKING**：`config/.locks.json` 中死表条目删除

### 重建 metatest v2（严格正反合测试 + 量化评分）

- 删除现有 `metatest/` 目录中给予「跳过即通过」信用分的逻辑
- 前端 E2E 测试改为：环境缺失时**真实失败**并计入扣分（强制环境就绪）
- 新增「底层逻辑验证」测试类别：
  - 水位线不变零计算验证（同输入重复 update 返回 False，零事件）
  - 编译-运行分离验证（CompiledPool 一次性产出，运行时零解析）
  - 边执行三要素验证（3 层调用深度，非 7-8 层）
  - 节点角色表驱动验证（5 种角色查表分派，无 if 链）
  - 事件-信号-动作正交化验证（三层解耦，信号由配置派生）
- 量化评分从 6 维扩展到 8 维：
  1. 模块覆盖率（权重 15%）
  2. 测试通过率（权重 20%）— 真实通过，跳过计为失败
  3. 断言密度（权重 10%）
  4. 事件链完整性（权重 15%）
  5. 性能基准（权重 10%）
  6. 前端 E2E 真实通过率（权重 10%）— 不再给信用分
  7. **底层逻辑覆盖度（权重 10%）**— 水位线/编译-运行分离/三要素/角色表/正交化 5 项
  8. **同构代码消除度（权重 10%）**— Grep 验证同构模式匹配数为 0
- 评分门槛保持 ≥ 95，但扣分项更严格

## Impact

- Affected specs:
  - `RULES.md` 第 84-90 条（前 6 轮）→ 新增第 91-96 条（第 7-12 轮）
  - `DESIGN.md` 元模式章节扩展
  - `docs/deep_refactoring_plan/spec.md` 与 `docs/meta-core-essence-mapping/spec.md` 从规划文档落地为实施规范
  - 硬约束「G2 硬约束同代码」「所有分派使用 dict」「表驱动UI」「运行时事件无序」「MetaEngine已删除」「PoolState是唯一真相源」「变换单元使用三元组」「CompiledSchedule一次性产出」「运行期不处理拓扑」对齐
- Affected code:
  - `core/runtime_mode_module.py` — 迭代 7（TickTable 引入）+ 迭代 8（CompiledPool 产出）
  - `core/execution_module.py` — 迭代 8（compile 函数）+ 迭代 9（三要素收敛）+ 迭代 11（事件-信号-动作拆分）
  - `core/engine.py` — 迭代 8（运行时零解析）+ 迭代 9（3 层调用）+ 迭代 10（角色表驱动）+ 迭代 11（信号派生）
  - `core/tick_bar_module.py` — 迭代 7（TickTable 适配）
  - `core/screening_module.py` — 迭代 7（TickTable 读取）+ 迭代 9（filter_eval 收敛）
  - `core/trade_module.py` — 迭代 11（信号驱动动作）
  - `core/monitoring_module.py` — 迭代 11（事件记录适配）
  - `config/architecture/node_roles.json` — 迭代 10（新增/扩展）
  - `config/architecture/timing.json` — 迭代 9（starttype × cxtype 笛卡尔积）
  - `config/architecture/filter_specs.json` — 迭代 9（nset × noperate 笛卡尔积）
  - `config/architecture/propagate_modes.json` — 迭代 9（3 模式）
  - `config/.locks.json` — 迭代 12（死表清理）
  - `config/_archive/` — 迭代 12（新建死表归档目录）
  - `metatest/` — 全量重建为 v2

## ADDED Requirements

### Requirement: latest_tick 水位线表统一

系统 SHALL 封装 `TickTable` 类作为最新 tick 数据的唯一真相源，通过 hash 比较实现水位线检测。

#### Scenario: 水位线不变时零计算
- **WHEN** 行情推送调用 `tick_table.update(tick_data)` 且新 hash 等于旧 hash
- **THEN** 返回 False，`tick_table.ts` 不更新
- **AND** 引擎核心循环检测到 False 后直接返回空事件列表，不执行任何边

#### Scenario: 水位线变化时触发计算
- **WHEN** 行情推送调用 `tick_table.update(tick_data)` 且新 hash 不等于旧 hash
- **THEN** 更新 `tick_table.ts` 为当前时间，返回 True
- **AND** 引擎核心循环检测到 True 后按 edge_order 遍历边执行

#### Scenario: 统一读取入口
- **WHEN** 任意模块需要读取最新 tick 数据
- **THEN** 调用 `tick_table.get(code)` 或 `tick_table.snapshot()` 获取
- **AND** 禁止直接访问 `state.latest_tick[code]` 内部字典

### Requirement: 编译-运行分离统一

系统 SHALL 在加载时一次性编译 `pool_config` 为 `CompiledPool`，运行时仅读不解析。

#### Scenario: 编译期产出
- **WHEN** 池配置加载时调用 `compile(pool_config)`
- **THEN** 产出 `CompiledPool` 含：节点字典、边字典、端点解析、邻接表、源节点列表、边类型判定、边规格编译、节点角色映射
- **AND** 执行顺序从 `edge.params._order` 读取排序，非拓扑排序

#### Scenario: 运行时零解析
- **WHEN** 引擎核心循环执行边
- **THEN** 仅从 `CompiledPool` 读取预编译结构
- **AND** 禁止运行时调用节点类型判定、边参数解析、邻接表构建

#### Scenario: 编译-运行分离与运行时事件无序对齐
- **WHEN** 编译期产出 `edge_order` 列表
- **THEN** 该列表是设计期结构（来自 `edge.params._order`），非运行时拓扑排序
- **AND** 运行时按 `edge_order` 列表遍历，定时器到时即触发，引擎不编排执行顺序（与硬约束「运行时事件无序」对齐）

### Requirement: 边执行三要素表驱动

系统 SHALL 将边执行收敛为 3 层调用：`trigger_check → filter_eval → propagate_apply`。

#### Scenario: 触发判定
- **WHEN** 边执行循环调用 `trigger_check(edge_timing_spec, now_ts, flow_state, node_dirty)`
- **THEN** 返回 `time_ok AND node_dirty` 布尔值
- **AND** 时间判定通过 `timing.json` 表的 starttype × cxtype 笛卡尔积（8×3=24 组合，11 条规则）

#### Scenario: 过滤求值
- **WHEN** 触发判定通过后调用 `filter_eval(codes, filter_spec, tick_table)`
- **THEN** 返回 `(passed_codes, rejected_codes)` 元组
- **AND** 过滤通过 `filter_specs.json` 表的 nset × noperate 笛卡尔积（6×10=60 组合，16 条规则）

#### Scenario: 传播应用
- **WHEN** 过滤求值完成后调用 `propagate_apply(src_stocks, tgt_stocks, passed, propagate_spec)`
- **THEN** 按 `propagate_modes.json` 表的 3 种模式（copy/move/overwrite）更新目标节点股票
- **AND** 返回 `new_tgt_stocks`

#### Scenario: 调用深度收敛
- **WHEN** Grep 检查边执行调用链
- **THEN** 最大调用深度 ≤ 3 层（trigger_check → filter_eval → propagate_apply）
- **AND** 禁止 `_phase_dispatch` / `_phase_nset_filter` / `_dispatch_filter` / `_eval_primitive` / `_extract_prim_params_table` / `_extract_single_param` 6 层中间函数存在

### Requirement: 节点角色表驱动

系统 SHALL 通过 `config/architecture/node_roles.json` 表驱动节点角色行为，禁止 `if node.type == ...` 链。

#### Scenario: 角色配置表
- **WHEN** 编译期加载 `node_roles.json`
- **THEN** 表含 5 种角色：candidate / state / condition / target / discard
- **AND** 每种角色定义 `on_enter` 与 `on_exit` 动作列表

#### Scenario: 角色分派
- **WHEN** 节点股票入池
- **THEN** 查 `_ROLE_ACTIONS[role]["on_enter"]` 表得到动作列表
- **AND** 依次执行动作（如 target 角色触发 ENTER 事件 + BUY 信号）

### Requirement: 事件-信号-动作正交化

系统 SHALL 将状态变化处理拆分为事件、信号、动作三层正交架构。

#### Scenario: 事件层
- **WHEN** 节点股票列表变化（入池/出池/TTL淘汰）
- **THEN** 计算新旧差集，发布 `StockChanged` 事件
- **AND** 事件仅记录客观状态变化，不含任何副作用

#### Scenario: 信号层
- **WHEN** 信号派生器收到 `StockChanged` 事件
- **THEN** 查 `node_roles.json` 中节点角色的信号规则
- **AND** target 角色入池 → 发布 `Signal(kind="BUY")`，出池 → 发布 `Signal(kind="SELL")`

#### Scenario: 动作层
- **WHEN** ActionDispatcher 收到 `Signal` 事件
- **THEN** 查 `config/ui/action_table.json` 表得到动作列表
- **AND** 依次执行动作（声音/弹窗/TDX板块/历史保存）

### Requirement: 配置表收敛与死表清理

系统 SHALL 将 94 张配置表收敛为 8 张核心 + 外围保留，死表归档。

#### Scenario: 核心表识别
- **WHEN** 审计 `config/architecture/` 目录
- **THEN** 识别 8 张核心运行时表：timing.json / filter_specs.json / propagate_modes.json / node_roles.json / edge_semantics.json / runtime_modes.json / alert_rules.json / ttl_rules.json
- **AND** 引擎核心循环只直接读这 8 张

#### Scenario: 死表归档
- **WHEN** Grep 验证某配置表 0 引用
- **THEN** 移动到 `config/_archive/` 目录
- **AND** 从 `config/.locks.json` 中删除对应条目

### Requirement: 严格正反合测试 v2

系统 SHALL 重建 `metatest/` 目录，测试必须真实通过（跳过计为失败），覆盖底层逻辑验证。

#### Scenario: 真实通过判定
- **WHEN** 前端 E2E 测试因环境缺失被跳过
- **THEN** 计为失败（非信用分通过）
- **AND** 扣分项记录「前端 E2E 环境未就绪」

#### Scenario: 底层逻辑覆盖
- **WHEN** 运行 metatest v2
- **THEN** 必须包含 5 项底层逻辑验证测试：
  1. 水位线不变零计算验证
  2. 编译-运行分离验证
  3. 边执行三要素调用深度验证
  4. 节点角色表驱动验证
  5. 事件-信号-动作正交化验证

#### Scenario: 8 维量化评分
- **WHEN** 测试运行完成
- **THEN** 计算 8 维加权总分：
  - 模块覆盖率 15% / 测试通过率 20% / 断言密度 10% / 事件链完整性 15%
  - 性能基准 10% / 前端 E2E 真实通过率 10% / 底层逻辑覆盖度 10% / 同构代码消除度 10%
- **AND** 总分 ≥ 95 判定 PASS，否则 FAIL

## MODIFIED Requirements

### Requirement: RULES.md 架构合同扩展

在 RULES.md 第 84-90 条（前 6 轮）基础上，新增第 91-96 条：

- 91. **latest_tick 封装为 `TickTable` 类，hash 比较实现水位线检测**：禁止散落直写 `state.latest_tick[code] = ...`，禁止水位线不变时执行任何计算
- 92. **编译-运行分离为 `compile(pool_config) → CompiledPool` 一次性产出**：禁止运行时解析节点类型、边参数、邻接表；执行顺序从 `edge.params._order` 读取，非拓扑排序
- 93. **边执行收敛为 `trigger_check → filter_eval → propagate_apply` 三层调用**：禁止 7-8 层中间函数链；时间触发用 starttype × cxtype 笛卡尔积，过滤用 nset × noperate 笛卡尔积
- 94. **节点角色表驱动为 `node_roles.json` + `_ROLE_ACTIONS` 查表分派**：禁止 `if node.type == ...` 链
- 95. **事件-信号-动作三层正交化**：事件仅记录状态变化，信号由角色配置派生，动作由 `action_table.json` 表驱动；禁止 transfer_executed 直接调用副作用
- 96. **配置表收敛为 8 张核心 + 死表归档到 `config/_archive/`**：禁止运行时加载死表

### Requirement: 量化评分从 6 维扩展到 8 维

修改 `metatest/scoring.py` 的 `ScoringEngine`：
- 维度从 6 个扩展到 8 个（新增「底层逻辑覆盖度」「同构代码消除度」）
- 权重重新分配（见上述 8 维列表）
- 跳过测试计为失败，不再给予信用分
- 评分门槛保持 ≥ 95

## REMOVED Requirements

### Requirement: 散落的 latest_tick 直写
**Reason**: 违反水位线洞察，导致水位线不变时仍执行计算
**Migration**: 替换为 `TickTable.update(tick_data) -> bool` 统一入口

### Requirement: 运行时解析节点类型/边参数/邻接表
**Reason**: 违反编译-运行分离洞察，导致 engine.py 3369 行
**Migration**: 替换为编译期 `compile(pool_config) -> CompiledPool` 一次性产出

### Requirement: 7-8 层边执行调用链
**Reason**: 肤浅表驱动，每层都做同样的事（查表→调用→查表→调用），逻辑未简化仅被拆散
**Migration**: 替换为 `trigger_check → filter_eval → propagate_apply` 3 层

### Requirement: `if node.type == 'target'` 等散落判定
**Reason**: 违反 RULES.md 第 16 条「分派用 dict」
**Migration**: 替换为 `node_roles.json` + `_ROLE_ACTIONS` 查表分派

### Requirement: transfer_executed 直接调用副作用
**Reason**: 事件与动作耦合，违反正交化原则
**Migration**: 替换为 `event → signal → action` 三阶段

### Requirement: metatest 跳过即信用分通过
**Reason**: 评分不真实，掩盖测试覆盖缺口
**Migration**: 跳过计为失败，强制环境就绪
