# 元模式彻底完善迭代 Spec

## Why

前一阶段 `refactor-meta-pattern-unification` 已完成 3 组同构代码合并（DataChanged 发布器、EdgeExecutor 步骤表驱动、MonitoringModule 事件记录统一）。但底层逻辑洞察显示，代码库中仍残留 **14 类同构模式**，其中 4 类为高优先级，违反 RULES.md 第 2/16/24/69/81-83 条与项目硬约束「G2 硬约束同代码」「模块级配置加载必须通过 ConfigStore」「所有分派使用 dict，禁止 if/elif 链」「表驱动UI」。

本次迭代将这些残留同构彻底收敛为元模式，使程序底层运行逻辑全面符合 RULES.md 表驱动 + 事件驱动 + 协议化架构合同，并按「架构工程师 → 评审工程师」流程分轮次迭代完善。

## What Changes

### 迭代 1：同步/异步双路径统一（高优先级，硬约束 G2 延伸）

- 将 `runtime_mode_module.py` 的 `_step_once`（同步，行 1664-1838）与 `_astep_once`（异步，行 1838-2010）合并为单一 `_step_once_impl(d, *, async_mode: bool)` 骨架，11 个步骤逐一对应，差异点（事件过滤集、await、virtual_clock 同步）通过参数注入
- **BREAKING**：`_step_once` 与 `_astep_once` 内联调用全部改为委托 `_step_once_impl`
- 将 `tick_bar_module.py` 的 `_on_simulation_step`（行 1154-1175）与 `_on_replay_step`（行 1180-1202）合并为单一 `_on_step_event(event, *, driver_type)` 处理器
- 抽出 `_publish_tick_batch(bus, tick_data, ts)` 工具函数，统一 5 处 `TickReceived` 发布循环（runtime_mode_module.py:1708/1874、tick_bar_module.py:1042/1162/1188）

### 迭代 2：公式引擎协议化（高优先级，违反 RULES.md 第 16 条）

- 在 `core/formula_module.py` 中定义 `IFormulaEngine` Protocol，含 `eval`/`eval_outvars`/`eval_series`/`eval_batch` 标准签名
- `CompiledFormula`、`PythonFormulaEngine`、`FormulaEngine`、`FormulaRouter` 全部 `impl IFormulaEngine`
- 将 `FormulaRouter` 内部 `_eval_python`/`_eval_hqchart`、`_eval_python_batch`/`_eval_hqchart_batch` 6 个双路径方法用 `_ENGINE_DISPATCH = {"python": _eval_python, "hqchart": _eval_hqchart}` 表驱动收敛
- **BREAKING**：`FormulaRouter._eval_python` / `_eval_hqchart` 不再被外部直接调用

### 迭代 3：HTTP 路由 Depends 化（高优先级，违反 RULES.md 第 69 条）

- 在 `api.py` 中定义 `require_config_store() -> ConfigStore` FastAPI Dependency，统一处理「引擎未初始化」21+ 处样板
- 在 `api.py` 的 router 级别用 `dependencies=[Depends(require_config_store)]` 一次性挂载
- **BREAKING**：21+ 路由处理器内的 `if not _config_store: raise HTTPException(500, ...)` 全部删除
- 在 `app.py` 中定义 `get_simulator(name: str) -> Simulator` Depends，统一处理「仿真会话不存在」
- 用 `_SIM_ACTIONS = {"pause": "pause", "resume": "resume", "stop": "reset", "get_state": "get_state_snapshot", "set_speed": "set_speed"}` 表驱动，将 5 个独立 sim 路由收敛为单一 `@app.post("/api/pool/{name:path}/sim/{action}")`
- **BREAKING**：`/api/pool/{name}/sim/pause` 等 5 个独立路由合并为 `/api/pool/{name}/sim/{action}`

### 迭代 4：配置加载统一到 ConfigStore（高优先级，违反硬约束）

- 将 10 处散落的 `_load_json`/`_load_config` 帮助函数全部替换为 `config_store.get_table(name)` 或新增的 `config_store.get_data_file(name)` 统一入口
- 涉及文件：`services/providers.py:838`、`native/validators.py:1001`、`native/builtins.py:121`、`app.py:2479`、`core/trade_module.py:59`、`core/runtime_mode_module.py:2481`、`core/monitoring_module.py:760`、`core/monitoring_module.py:984`、`core/execution_module.py:141`、`converters.py:5848`
- **BREAKING**：10 处独立 `_load_json` / `_load_config` 函数删除
- 在 `core/table_engine.py` 的 `ConfigStore` 中新增 `get_data_file(name)` 方法处理非配置表 JSON（如 `mock_data.json`）

### 迭代 5：中等优先级批次收敛

- **5.1** `engine.py:807-810` 残留 `if mode_id == "replay"` 分支 → 在 `runtime_modes.json` 增加 `state_scope` 字段，由 `state.set_state_scope(cfg)` 表驱动
- **5.2** `domain.py` 10+ Node/Edge/Spec 类的 `to_dict`/`from_dict` 样板 → 用 `@dataclass` + 字段元数据自动生成
- **5.3** `domain.py` 3 个 Evaluator 子类 → 用 `_EVALUATOR_REGISTRY: Dict[str, Type[Evaluator]]` 注册表替换运行期 `if/elif`
- **5.4** `runtime_mode_module.py` K 线合成器 4 函数（`synthesize_from_1min`/`synthesize_from_5min`/`_synthesize_day_from_intraday`/`synthesize_from_daily`）→ 合并为单一 `synthesize(bars, source_period, target_period)` 函数 + `_SYNTHESIS_RULES` 表驱动
- **5.5** `app.py` 5 个 tdx CRUD 路由 → 抽出 `_tdx_path_guard(name)` + `_TDX_ACTIONS` 表驱动
- **5.6** `import_export_module.py` 9 个导入/导出方法 → `_IMPORT_RULES` + `_EXPORT_RULES` 表驱动收敛为 `import_pool(path, format)` / `export_pool(config, path, format)` 2 个方法
- **5.7** `runtime_mode_module.py:3050-3071` 4 对 `set_xxx`/`get_xxx` → `dataclass` + `field(default_factory=dict)`
- **5.8** `web/js/event-panel.js` 5 个 `draw_xxx` 函数 → `_DRAW_LAYERS` 表驱动 + `_STYLE` 配置对象
- **5.9** `web/js/event-panel.js` 矩阵视图 vs 散点视图渲染骨架 → `renderEventCanvas(ctx, state, layoutMode)` 单一函数参数化

### 迭代 6：低优先级收尾（可选）

- `table_engine.py:1432/1573` `ConfigChanged` 2 处发布 → `_notify_changed(changed_tables)` 单一函数
- `execution_module.py:3060/3601/3104/3151` `EdgeFired`/`TTLDue` 2 处发布 → 扩展 `_publish` 工厂
- `runtime_mode_module.py:2792-2824` `PoolStateView` 5 个查询方法 → 保持现状

## Impact

- Affected specs: RULES.md 第 2/16/24/69/81-83 条；硬约束「G2 硬约束同代码」「模块级配置加载 ConfigStore」「所有分派 dict 表驱动」「表驱动UI」
- Affected code:
  - `core/runtime_mode_module.py` — 迭代 1（_step_once/_astep_once 合并）+ 迭代 4（_load_json 删除）+ 迭代 5.4（K 线合成器）+ 迭代 5.7（set/get 对）
  - `core/formula_module.py` — 迭代 2（IFormulaEngine Protocol + _ENGINE_DISPATCH 表）
  - `api.py` — 迭代 3（require_config_store Depends + router 级挂载）
  - `app.py` — 迭代 3（get_simulator Depends + _SIM_ACTIONS 表 + tdx CRUD 表驱动）+ 迭代 4（_load_json_file 删除）
  - `core/table_engine.py` — 迭代 4（ConfigStore 新增 get_data_file）+ 迭代 6（_notify_changed）
  - `core/tick_bar_module.py` — 迭代 1（_on_simulation_step/_on_replay_step 合并 + _publish_tick_batch）
  - `core/domain.py` — 迭代 5.2（@dataclass + 字段元数据）+ 迭代 5.3（_EVALUATOR_REGISTRY）
  - `core/execution_module.py` — 迭代 4（_load_config 删除）+ 迭代 6（_publish 扩展）
  - `core/monitoring_module.py` — 迭代 4（2 处 _load_json 删除）
  - `core/trade_module.py` — 迭代 4（_load_json_cache 删除）
  - `core/import_export_module.py` — 迭代 5.6（_IMPORT_RULES + _EXPORT_RULES）
  - `core/engine.py` — 迭代 5.1（state_scope 表驱动）
  - `services/providers.py`、`native/validators.py`、`native/builtins.py`、`converters.py` — 迭代 4（_load_json/_load_config 删除）
  - `web/js/event-panel.js` — 迭代 5.8（_DRAW_LAYERS + _STYLE）+ 迭代 5.9（renderEventCanvas 单一函数）
  - `config/architecture/runtime_modes.json` — 迭代 5.1（新增 state_scope 字段）

## ADDED Requirements

### Requirement: 同步/异步双路径统一

系统 SHALL 将 `RuntimeModeModule._step_once` 与 `_astep_once` 合并为单一 `_step_once_impl(d, *, async_mode: bool)` 骨架函数，11 个步骤逐一对应，差异点通过参数注入。

#### Scenario: 同步模式执行
- **WHEN** 调用方请求同步执行（`async_mode=False`）
- **THEN** `_step_once_impl` 用 `self._run_coro(self._engine._tick(...))` 调用引擎
- **AND** 末尾根据 `not self._astep_mode` 发布 `SimulationStep` 事件

#### Scenario: 异步模式执行
- **WHEN** 调用方请求异步执行（`async_mode=True`）
- **THEN** `_step_once_impl` 用 `await self._engine._tick(...)` 调用引擎
- **AND** 应用 `_ASTEP_KEY_TYPES` 事件过滤集 + 200 条上限
- **AND** 当 `virtual_clock` 非 None 时同步 `pe.state.time_source["current_ts"]`

#### Scenario: 新增执行步骤
- **WHEN** 需要在单步流程中新增一个步骤（如风控检查）
- **THEN** 仅在 `_step_once_impl` 单一函数中添加
- **AND** 同步/异步路径同时获得该步骤，避免双路径漂移

### Requirement: 公式引擎协议化

系统 SHALL 定义 `IFormulaEngine` Protocol，统一 `CompiledFormula`、`PythonFormulaEngine`、`FormulaEngine`、`FormulaRouter` 的 `eval`/`eval_outvars`/`eval_series`/`eval_batch` 方法签名。

#### Scenario: 公式路由分派
- **WHEN** `FormulaRouter` 收到 eval 请求
- **THEN** 通过 `_ENGINE_DISPATCH` dict 查表选择 `_eval_python` 或 `_eval_hqchart`
- **AND** 禁止使用 `if engine_type == "python": ... elif engine_type == "hqchart": ...` 链

#### Scenario: 新增公式引擎
- **WHEN** 需要新增一种公式引擎（如 `eval_js`）
- **THEN** 仅实现 `IFormulaEngine` Protocol + 在 `_ENGINE_DISPATCH` dict 中添加 `{"js": _eval_js}` 条目
- **AND** 零行 `FormulaRouter` 改动

### Requirement: HTTP 路由 Depends 化

系统 SHALL 通过 FastAPI `Depends` 机制统一处理「引擎未初始化」与「仿真会话不存在」样板。

#### Scenario: 引擎未初始化
- **WHEN** 任意路由被调用且 `_config_store` 为 None
- **THEN** `require_config_store` Dependency 抛出 `HTTPException(500, "引擎未初始化")`
- **AND** 路由处理器内不再有 `if not _config_store` 判断

#### Scenario: 仿真控制动作
- **WHEN** 调用 `POST /api/pool/{name}/sim/{action}`（action ∈ pause/resume/stop/get_state/set_speed）
- **THEN** `get_simulator` Dependency 统一处理会话查找
- **AND** `_SIM_ACTIONS[action]` 表驱动分派到 `simulator.<method>()`

### Requirement: 配置加载统一到 ConfigStore

系统 SHALL 将所有散落的 `_load_json`/`_load_config` 帮助函数替换为 `ConfigStore.get_table(name)` 或 `ConfigStore.get_data_file(name)` 调用。

#### Scenario: 加载配置表
- **WHEN** 任意模块需要加载 `config/architecture/xxx.json`
- **THEN** 调用 `config_store.get_table("xxx")`
- **AND** 自动获得热加载能力

#### Scenario: 加载非配置表 JSON
- **WHEN** 任意模块需要加载 `data/xxx.json` 或 `mock_data.json`
- **THEN** 调用 `config_store.get_data_file("xxx")`
- **AND** 该方法在 `ConfigStore` 中新增

#### Scenario: 删除散落帮助函数
- **WHEN** Grep 搜索 `_load_json` / `_load_config` / `_load_json_file` / `_load_json_cache`
- **THEN** 代码库中零匹配（除 `ConfigStore` 内部实现）

### Requirement: K 线合成器表驱动

系统 SHALL 将 4 个 K 线合成函数合并为单一 `synthesize(bars, source_period, target_period)` 函数 + `_SYNTHESIS_RULES` 表驱动。

#### Scenario: 从 1 分钟合成 5 分钟
- **WHEN** 调用 `synthesize(bars, "1min", "5min")`
- **THEN** 查 `_SYNTHESIS_RULES[("1min", "5min")]` 得到 `_aggregate_bars(bars, n=5)`
- **AND** 返回合成后的 bars 列表

#### Scenario: 从分钟合成日/周/月
- **WHEN** 调用 `synthesize(bars, "1min", "day")` 或 `synthesize(bars, "1min", "week")`
- **THEN** 查 `_SYNTHESIS_RULES` 得到 `_group_and_synthesize(bars, key_func=_get_day_key)`
- **AND** `key_func` 通过 `_PERIOD_KEY_FUNCS = {"day": ..., "week": ..., "month": ...}` 表驱动

### Requirement: 导入/导出表驱动

系统 SHALL 将 9 个导入/导出方法收敛为 2 个方法 + `_IMPORT_RULES`/`_EXPORT_RULES` 表驱动。

#### Scenario: 导入 dzh 格式
- **WHEN** 调用 `import_pool(path, format="dzh")`
- **THEN** 查 `_IMPORT_RULES["dzh"]` 得到 `(parser_func, format_name)`
- **AND** 执行解析并发布 `PoolLoaded` 事件

#### Scenario: 导出 tdx 格式
- **WHEN** 调用 `export_pool(config, path, format="tdx")`
- **THEN** 查 `_EXPORT_RULES["tdx"]` 得到 `(serializer_func, format_name)`
- **AND** 执行序列化并发布 `ExportCompleted` 事件

### Requirement: 前端渲染表驱动

系统 SHALL 将 `web/js/event-panel.js` 的 5 个 `draw_xxx` 函数收敛为 `_DRAW_LAYERS` 表驱动 + `_STYLE` 配置对象。

#### Scenario: 渲染事件画布
- **WHEN** `renderEventCanvas(ctx, state, layoutMode)` 被调用
- **THEN** 按 `_DRAW_LAYERS` 表中的图层顺序依次调用 `drawLabelArea` → `drawHorizontalGrid` → `drawTimeAxis` → `drawEventIcon` → `drawNowLine`
- **AND** 样式（颜色/线宽/字体）从 `_STYLE` 配置对象读取

#### Scenario: 切换矩阵/散点布局
- **WHEN** `layoutMode = "matrix"` 时
- **THEN** `rowH = plotH / catCount`，事件 y 坐标按分类分行
- **WHEN** `layoutMode = "scatter"` 时
- **THEN** `rowH = plotH`，所有事件 y 坐标居中（`cy = plotH / 2`）

## MODIFIED Requirements

### Requirement: RULES.md 架构合同

在 RULES.md 第 81-83 条基础上，新增第 84-90 条：
- 84. **同步/异步双路径统一为 `_step_once_impl(async_mode)` 单一骨架**：禁止重新引入 `_step_once` / `_astep_once` 双路径
- 85. **公式引擎统一为 `IFormulaEngine` Protocol**：`CompiledFormula`/`PythonFormulaEngine`/`FormulaEngine`/`FormulaRouter` 全部实现该协议，禁止新增引擎类时绕过 Protocol
- 86. **HTTP 路由「引擎未初始化」检查统一为 FastAPI `Depends(require_config_store)`**：禁止在路由处理器内重新引入 `if not _config_store` 样板
- 87. **配置加载统一到 `ConfigStore.get_table` / `get_data_file`**：禁止在模块级重新定义 `_load_json` / `_load_config` 帮助函数
- 88. **K 线合成统一为 `synthesize(bars, source_period, target_period)` + `_SYNTHESIS_RULES` 表**：禁止重新引入 `synthesize_from_1min` / `synthesize_from_5min` 等同构函数
- 89. **导入/导出统一为 `import_pool(path, format)` / `export_pool(config, path, format)` + `_IMPORT_RULES` / `_EXPORT_RULES` 表**：禁止重新引入 `import_dzh_xml` / `import_tdx_xml` 等同构方法
- 90. **前端事件画布统一为 `renderEventCanvas(ctx, state, layoutMode)` + `_DRAW_LAYERS` 表 + `_STYLE` 配置**：禁止重新引入矩阵视图/散点视图的独立渲染函数

## REMOVED Requirements

### Requirement: `_step_once` 与 `_astep_once` 双路径方法
**Reason**: 11 个步骤逐一对应同构，违反 G2 硬约束「同代码」延伸
**Migration**: 替换为 `_step_once_impl(d, *, async_mode=False)` 单一骨架；同步入口 `step_once(d)` 调用 `_step_once_impl(d, async_mode=False)`，异步入口 `astep_once(d)` 调用 `_step_once_impl(d, async_mode=True)`

### Requirement: 21+ 路由处理器内 `if not _config_store` 样板
**Reason**: 违反 RULES.md 第 69 条「模块自包含」与第 16 条「分派用 dict」
**Migration**: 替换为 `dependencies=[Depends(require_config_store)]` router 级挂载

### Requirement: 10 处散落的 `_load_json` / `_load_config` / `_load_json_file` / `_load_json_cache` 帮助函数
**Reason**: 违反硬约束「模块级配置加载必须通过 ConfigStore.get_table(name)，禁止直接 json.loads 绕过热加载」
**Migration**: 全部替换为 `config_store.get_table(name)` 或 `config_store.get_data_file(name)`

### Requirement: `_on_simulation_step` 与 `_on_replay_step` 双路径方法
**Reason**: 7 步骨架逐一对应同构，仅 driver_type 与 provider 不同
**Migration**: 替换为 `_on_step_event(event, *, driver_type, provider_fn)` 单一处理器

### Requirement: 4 个独立 K 线合成函数
**Reason**: `synthesize_from_1min` / `synthesize_from_5min` / `_synthesize_day_from_intraday` / `synthesize_from_daily` 同构
**Migration**: 替换为 `synthesize(bars, source_period, target_period)` + `_SYNTHESIS_RULES` 表

### Requirement: 9 个独立导入/导出方法
**Reason**: `import_dzh_xml` / `import_tdx_xml` / `import_json` + `_call_dzh_parser` / `_call_tdx_parser` / `_call_json_parser` + `export_to_dzh_xml` / `export_to_tdx_xml` / `export_to_json` 同构
**Migration**: 替换为 `import_pool(path, format)` / `export_pool(config, path, format)` + `_IMPORT_RULES` / `_EXPORT_RULES` 表

### Requirement: 5 个 sim 控制独立路由
**Reason**: `sim_pause` / `sim_resume` / `sim_stop` / `sim_get_state` / `sim_set_speed` 骨架同构
**Migration**: 替换为单一 `@app.post("/api/pool/{name:path}/sim/{action}")` + `_SIM_ACTIONS` 表

### Requirement: 矩阵视图与散点视图独立渲染函数
**Reason**: 6 步骨架完全同构，仅 `rowH` 与 `cy` 计算差异
**Migration**: 替换为 `renderEventCanvas(ctx, state, layoutMode)` 单一函数 + `layoutMode` 参数控制差异
