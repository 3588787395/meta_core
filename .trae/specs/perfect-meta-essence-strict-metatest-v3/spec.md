# 元模式极致本质收敛与严格正反合量化测试 v3 Spec

## Why

前两轮元模式收敛（`refactor-meta-pattern-unification` 规则 81-90，`deepen-meta-pattern-strict-metatest-v2` 迭代 7-12）完成了表层同构合并：DataChanged 发布器、EdgeExecutor 步骤表驱动、MonitoringModule 事件记录、同步/异步双路径、公式引擎协议、HTTP Depends、配置加载统一、K 线合成、导入导出、前端事件画布、水位线 TickTable、编译-运行分离、边执行三要素、节点角色表、事件-信号-动作正交化。

但用户明确指出这仍是「皮毛修改」，要的是**底层运行逻辑的洞察，真正同构代码的合并，极致本质的运行时**。经架构工程师对 8 个核心模块（共 24,187 行）逐行洞察，确认仍有 **15 组真正同构模式**（同骨架、异数据）尚未合并，净减约 440 行，且其中 4 组违反 RULES.md 第 59/87 条。同时 metatest v2 虽报 99.57 分 PASS，但评分仍存在「随意评分」问题：6 项同构检查远少于实际 15 组模式、断言密度目标过低（10/文件）、未按真实测试结果扣分、未覆盖本次新增的 15 组模式合并验收。

本次迭代（v3）将：(1) 合并 15 组真正同构代码至极致本质运行时；(2) 重新创建 metatest v3 严格正反合测试，全面覆盖股票池平台前端+后端所有模块；(3) 评分完全由真实测试结果量化驱动，杜绝随意评分；(4) 按架构工程师 → 评审工程师迭代流程推进。

## What Changes

### 第一批：规则违规 + 纯字节级重复（最高优先级）

- **变更 A**：合并 `screening_module.py` 4 个 nset 筛选函数。`_filter_condition_formula`(nset=1) 与 `_filter_expert_system`(nset=2) 除 docstring 外逐字节相同；`_filter_financial_scalar`(nset=3) 与 `_filter_market_scalar`(nset=4) 仅 `nset_label`("3" vs "4") 不同。合并为 `_filter_truthy`（1+2）+ `_filter_scalar`（3+4，nset_label 由 nset 派生），`_NSET_FILTER_HANDLERS` 表值驱动。净减约 30 行。
- **变更 G**：消除 4 处绕过 ConfigStore 的 `json.load(open(...))` 配置加载（违反规则 87）。`formula_module.py:1704-1731` 的 `_load_simple_functions`/`_load_routing_config`、`engine.py:2168-2174` 批量加载、`domain.py:1419-1431` 的 `_load_market_cfg` 全部改为 `get_global_config_store().get_table(...)`。净减约 30 行。
- **变更 H**：消除 `execution_module.py:2311-2420` 的 `if mode == "inflection"` / `if mode == "rank"` 硬编码分支（违反规则 59）。该分支与 `screening_module._apply_noperate_mode` 是同一逻辑的旧拷贝，合并为表驱动 `_MODE_HANDLERS` + 向量变体 `_apply_noperate_mode_series`。净减约 30 行。
- **变更 I**：消除 `runtime_mode_module.py:545-553` 的 `if/elif base_period` 硬编码（违反规则 59）。改为 `_BASE_PERIOD_TARGETS` 表驱动。净减约 6 行。
- **变更 E**：消除 `trade_module.py:1101-1156` 的 BUY/SELL 双分支（仅字段前缀 `enter*` vs `exit*` 不同）。改为 `_TRADEATTR_FIELD_MAP` 表驱动 + 单循环。净减约 20 行。

### 第二批：大块行数收益（次高优先级）

- **变更 C**：合并 `import_export_module.py` 6 个 `_parse_xxx`/`_serialize_xxx` 函数（骨架完全相同：延迟 import converter → 调用 → 返回/写文件，6 处 try/except ImportError 兜底逐字重复）。扩展 `_CONVERTER_REGISTRY` 表 + 单一入口 `_call_converter`。**BREAKING**：删除 6 个独立函数。净减约 60 行。
- **变更 D**：合并 `formula_module.py` `_eval_formula`(1318-1388) 与 `_eval_formula_series`(1402-1457) 的 7 步同构骨架（提取 formula_ref → 查 builtin → 解析 period → 合并 args → 定义 fetcher → 调用 engine → 转换结果）。提取 `_eval_formula_core(series=False)`，两函数改为薄包装。净减约 60 行。
- **变更 F**：合并 `execution_module.py` 4 个 `_build_xxx_spec`（timing/filter/propagate/edge_ctx 共享「从 edge.params 提取字段 → 查 config 表 → 构造 Spec」骨架）+ `_build_filter_spec` 内部 4 个 FilterSpec 构造分支。提取 `_extract_edge_params` + `_FILTER_SPEC_BUILDERS` 表驱动。净减约 50 行。

### 第三批：跨文件与装饰器级同构（架构级）

- **变更 J**：合并 `runtime_mode_module.py` 两个 `_run_coro_sync`(575-600) / `_run_coro`(1223-1253) 同步执行器（骨架相同：复用持久 loop → run_until_complete → 运行中降级线程，仅 loop 属性名 `_replay_loop` vs `_sim_loop` 不同）。提取模块级 `_run_coro_sync(coro, loop_holder, loop_attr)`。净减约 40 行。
- **变更 K**：合并跨文件 `_build_topology` 双实现（`engine.py:393-397` 从 edge_ctx 读、`runtime_mode_module.py:2723-2737` 从原始 config edges 读）。提取 `_build_adjacency(node_ids, edges_iter, src_getter, eid_getter)` 纯函数。净减约 10 行。
- **变更 N**：用 `@_event_handler(name)` 装饰器统一包装 20+ 处 `_on_xxx` 事件 handler 的 try/except 样板（trade/execution/monitoring/tick_bar/screening 5 模块）。净减约 40 行 + 修复 `exc_info=True` 不一致。
- **变更 B**：合并 `monitoring_module.py` 5 个 `_compute_xxx_pnl` 方法（共享「filter trackers by qty>0 → 提取字段 → 聚合 → return dict」骨架）。改为 `_PNL_METRIC_SPECS` 表驱动 + 单一 `_compute_pnl_metric(spec)`。净减约 30 行。
- **变更 L**：合并 `monitoring_module.py` 3 个 `_xxx_key` 排序键方法为 `_ANGLE_SORT_KEYS` lambda 表。净减约 18 行。
- **变更 M**：在 `_FILTER_EVALUATORS` 分派点统一加 `_apply_stock_filters` 后过滤包装，消除 4 处重复调用。净减约 8 行。
- **变更 O**：归一化 `table_engine.py:226-239` 的 dict/list 集合校验双分支为 `_iter_entries(collection)` 迭代器。净减约 8 行。

### metatest v3 重建（严格正反合量化测试）

- **BREAKING**：重新创建 `metatest/` 为 v3，按真实测试结果量化评分，杜绝随意评分
- 正测试（`test_positive_*.py`）：覆盖 17 个后端模块 + 前端全部模块的功能正确性，每模块 ≥ 1 文件，含本次 15 组模式合并的回归断言
- 反测试（`test_negative_*.py`）：4 类异常（无效配置/运行时异常/API前端/底层逻辑违规），每类 ≥ 8 用例，本次新增 15 组模式的「同构复活」反测试（Grep 断言旧同构代码零匹配）
- 合测试（`test_synthesis_*.py`）：端到端集成（仿真全流程/三模式/导入导出/热加载/元模式合并/前端 E2E/事件链顺序/水位线短路/编译-运行分离）
- 评分引擎 v3：12 维加权评分，**完全由真实测试结果驱动**，跳过计失败、环境缺失计失败、断言密度目标提升至 20/文件、同构检查从 6 项扩展到 15 项（对应本次 15 组模式）、新增「行数收敛度」维度（核心模块行数 ≤ 目标值才满分）

### 文档同步

- 在 `RULES.md` 新增第 91-100 条，固化本次 15 组模式合并的元模式约束
- 更新 `metatest/README.md` 为 v3 评分规则说明

## Impact

- Affected specs: 
  - RULES.md（新增第 91-100 条）
  - `refactor-meta-pattern-unification`（规则 81-83 基础上深化）
  - `deepen-meta-pattern-strict-metatest-v2`（迭代 7-12 基础上深化）
  - `meta-core-essence-mapping`（行数收敛目标对齐）
- Affected code:
  - `core/screening_module.py` — 变更 A（4 函数→2 函数）
  - `core/formula_module.py` — 变更 D（双 eval 合并）+ 变更 G（ConfigStore）
  - `core/execution_module.py` — 变更 F（build_spec 合并）+ 变更 H（mode 表驱动）+ 变更 M（后过滤统一）
  - `core/runtime_mode_module.py` — 变更 I（base_period 表）+ 变更 J（_run_coro 合并）+ 变更 K（topology 合并）
  - `core/engine.py` — 变更 G（ConfigStore）+ 变更 K（topology 合并）
  - `core/domain.py` — 变更 G（ConfigStore）+ 变更 K
  - `core/trade_module.py` — 变更 E（tradeattr 表驱动）+ 变更 N（装饰器）
  - `core/monitoring_module.py` — 变更 B（pnl 合并）+ 变更 L（排序键表）+ 变更 N（装饰器）
  - `core/import_export_module.py` — 变更 C（6 函数→1 函数+1 表）
  - `core/table_engine.py` — 变更 O（校验归一化）
  - `core/tick_bar_module.py` — 变更 N（装饰器）
  - `metatest/` — v3 重建（正反合测试 + scoring.py v3 + runner.py v3）
- 净减约 440 行核心代码 + 修复 4 处规则违规（规则 59×3、规则 87×1）

## ADDED Requirements

### Requirement: nset 筛选函数同构合并（变更 A）

系统 SHALL 将 `screening_module.py` 中 4 个 nset 筛选函数合并为 2 个值驱动函数，`_NSET_FILTER_HANDLERS` 表按 nset 路由到合并后的函数。

#### Scenario: nset=1 与 nset=2 共用真值筛选
- **WHEN** filter_spec.nset ∈ {1, 2}（条件选股公式 / 专家系统公式）
- **THEN** 路由到 `_filter_truthy(filter_spec, stock_results, current_code, evaluator)`，返回 `[c for c,v in stock_results.items() if isinstance(v,(int,float)) and v>0]`

#### Scenario: nset=3 与 nset=4 共用标量筛选
- **WHEN** filter_spec.nset ∈ {3, 4}（最新财务标量 / 实时行情标量）
- **THEN** 路由到 `_filter_scalar(filter_spec, stock_results, current_code, evaluator)`，nset_label 由 `str(filter_spec.nset)` 派生，委托 `_apply_noperate_mode(scalars, ..., nset_label=str(nset))`

#### Scenario: 旧 4 函数零匹配
- **WHEN** Grep `_filter_condition_formula|_filter_expert_system|_filter_financial_scalar|_filter_market_scalar` 在 screening_module.py
- **THEN** 匹配数 = 0（仅保留 `_filter_truthy` / `_filter_scalar` / `_filter_indicator` / `_filter_set_operation`）

### Requirement: ConfigStore 配置加载统一（变更 G）

系统 SHALL 消除所有绕过 ConfigStore 的 `json.load(open(...))` 配置加载，统一通过 `get_global_config_store().get_table(...)` 确保热加载能力。

#### Scenario: formula_module 配置加载
- **WHEN** FormulaModule 加载 simple_functions 或 routing_config
- **THEN** 调用 `get_global_config_store().get_table("data_pipeline")` / `get_table("formula_routing")`，无 `open()` / `json.load()` 直读

#### Scenario: engine/domain 配置加载
- **WHEN** engine.py 批量加载配置或 domain.py 加载 market_cfg
- **THEN** 通过 ConfigStore 统一入口，禁止 `json.load(open(path,...))` 样板

#### Scenario: 规则 87 零违规
- **WHEN** Grep `json\.load\(open\(` 在 core/*.py
- **THEN** 匹配数 = 0（ConfigStore 内部除外）

### Requirement: noperate mode 表驱动（变更 H）

系统 SHALL 将 `execution_module.py` 中 `if mode == "inflection"` / `if mode == "rank"` 硬编码分支替换为 `_MODE_HANDLERS` 表驱动，与 `screening_module._apply_noperate_mode` 共享同一逻辑真相源。

#### Scenario: inflection 模式
- **WHEN** noperate rule mode == "inflection"
- **THEN** 查 `_MODE_HANDLERS["inflection"]` 调用统一处理函数，无 if 分支

#### Scenario: rank 模式
- **WHEN** noperate rule mode == "rank"
- **THEN** 查 `_MODE_HANDLERS["rank"]` 调用统一处理函数，无 if 分支

#### Scenario: 向量变体复用
- **WHEN** execution_module 处理 series_results（向量数据）
- **THEN** 调用 `_apply_noperate_mode_series`（screening_module._apply_noperate_mode 的向量变体），共享同一 mode 分派表

### Requirement: base_period 目标周期表驱动（变更 I）

系统 SHALL 将 `runtime_mode_module.py` 中 `if/elif base_period` 替换为 `_BASE_PERIOD_TARGETS` 表驱动。

#### Scenario: 1min 基周期
- **WHEN** _base_period == "1min"
- **THEN** `_BASE_PERIOD_TARGETS["1min"]` 返回 `["5min","15min","30min","60min","day","week","month"]`

#### Scenario: day 基周期短路
- **WHEN** _base_period == "day"
- **THEN** `_BASE_PERIOD_TARGETS.get("day", [])` 返回空列表，`_build_synthesized_bars` 直接 return

### Requirement: tradeattr BUY/SELL 表驱动（变更 E）

系统 SHALL 将 `trade_module.py` `_apply_tradeattr` 的 BUY/SELL 双分支替换为 `_TRADEATTR_FIELD_MAP` 表驱动 + 单循环。

#### Scenario: BUY 单提取
- **WHEN** order.side == "BUY"
- **THEN** 按 `_TRADEATTR_FIELD_MAP["BUY"]` 的 9 个 enter* 字段提取，无 if side 分支

#### Scenario: SELL 单提取
- **WHEN** order.side == "SELL"
- **THEN** 按 `_TRADEATTR_FIELD_MAP["SELL"]` 的 9 个 exit* 字段提取，与 BUY 共用同一循环

### Requirement: 导入导出 converter 统一入口（变更 C）

系统 SHALL 将 6 个 `_parse_xxx`/`_serialize_xxx` 函数合并为单一 `_call_converter(path, fmt, direction, config=None)` + `_CONVERTER_REGISTRY` 表。

#### Scenario: 任意格式导入
- **WHEN** import_pool(path, format="dzh"|"tdx"|"json")
- **THEN** 查 `_CONVERTER_REGISTRY[(fmt, "import")]` 获取 converter 模块/函数/调用适配器，统一延迟 import + 统一 try/except + 统一日志

#### Scenario: 任意格式导出
- **WHEN** export_pool(config, path, format="dzh"|"tdx"|"json")
- **THEN** 查 `_CONVERTER_REGISTRY[(fmt, "export")]`，与导入共用同一 `_call_converter` 骨架

#### Scenario: 旧 6 函数零匹配
- **WHEN** Grep `def _parse_dzh|def _parse_tdx|def _parse_json|def _serialize_dzh|def _serialize_tdx|def _serialize_json` 在 import_export_module.py
- **THEN** 匹配数 = 0

### Requirement: 公式 eval 核心合并（变更 D）

系统 SHALL 将 `_eval_formula` 与 `_eval_formula_series` 的 7 步同构骨架合并为 `_eval_formula_core(formula_ref, codes, ctx, spec, lookback, series)`，两原函数改为薄包装委托。

#### Scenario: 标量公式求值
- **WHEN** _eval_formula(formula_ref, codes, ctx) 调用
- **THEN** 委托 `_eval_formula_core(..., series=False)`，提取 eval_field 返回 `{code: scalar}`

#### Scenario: 序列公式求值
- **WHEN** _eval_formula_series(formula_ref, codes, ctx, lookback) 调用
- **THEN** 委托 `_eval_formula_core(..., series=True, lookback=lookback)`，直接返回 batch

### Requirement: build_spec 提取器统一（变更 F）

系统 SHALL 将 4 个 `_build_xxx_spec` 的字段提取骨架统一为 `_extract_edge_params(edge)` + 各 Spec 的字段映射表，`_build_filter_spec` 的 4 个构造分支改为 `_FILTER_SPEC_BUILDERS` 表驱动。

#### Scenario: timing_spec 构建
- **WHEN** _build_timing_spec(edge)
- **THEN** 调用 `_extract_edge_params(edge)` 一次性提取，按 `_TIMING_SPEC_FIELDS` 映射构造 TimingSpec

#### Scenario: filter_spec 分支路由
- **WHEN** _build_filter_spec(edge) 评估 (has_tdx_func, has_formula_ref, condition_type)
- **THEN** 查 `_FILTER_SPEC_BUILDERS` 表路由到对应构造器，无 if/elif 分支

### Requirement: 同步协程执行器统一（变更 J）

系统 SHALL 将 `_run_coro_sync` 与 `_run_coro` 合并为模块级 `_run_coro_sync(coro, loop_holder, loop_attr="_sim_loop")`。

#### Scenario: replay 模式调用
- **WHEN** KLineReplayEngine 需同步执行协程
- **THEN** 调用 `_run_coro_sync(coro, self, "_replay_loop")`

#### Scenario: simulation 模式调用
- **WHEN** RuntimeSimulator 需同步执行协程
- **THEN** 调用 `_run_coro_sync(coro, self, "_sim_loop")`

### Requirement: 拓扑邻接构建统一（变更 K）

系统 SHALL 将跨文件 `_build_topology` 双实现合并为纯函数 `_build_adjacency(node_ids, edges_iter, src_getter, eid_getter)`。

#### Scenario: 编译期从 edge_ctx 构建
- **WHEN** PoolEngine._build_topology 从 schedule.edge_ctx.values() 构建
- **THEN** 调用 `_build_adjacency(node_ids, edge_ctx.values(), lambda ec: ec.sid, lambda ec: ec.eid)`

#### Scenario: 运行期从原始 edges 构建
- **WHEN** PoolState._build_topology 从 pool_config["edges"] 构建
- **THEN** 调用 `_build_adjacency(node_ids, edges, lambda e: e.get("source") or e.get("from") or e.get("sid"), lambda e: e.get("id"))`

### Requirement: 事件 handler 装饰器统一（变更 N）

系统 SHALL 用 `@_event_handler(name)` 装饰器统一包装 20+ 处 `_on_xxx` 的 try/except 样板，消除重复且统一 `exc_info=True`。

#### Scenario: 任意 handler 异常
- **WHEN** 任意被 `@_event_handler` 装饰的 _on_xxx 抛出异常
- **THEN** 装饰器捕获并 `logger.warning("<ClassName> <name> 异常: %s", ex, exc_info=True)`，返回 None，不向上传播

#### Scenario: handler 体内无 try/except
- **WHEN** 检查被装饰的 handler 函数体
- **THEN** 函数体不含 `try:` / `except Exception` 样板（由装饰器统一处理）

### Requirement: pnl 计算表驱动（变更 B）

系统 SHALL 将 5 个 `_compute_xxx_pnl` 方法合并为 `_PNL_METRIC_SPECS` 表 + 单一 `_compute_pnl_metric(spec)`。

#### Scenario: intraday 指标
- **WHEN** _ANALYSIS_HANDLERS["intraday"] 调用
- **THEN** 查 `_PNL_METRIC_SPECS["intraday"]`（filter=qty>0, extract=pnl, agg=sum, key=unrealized_pnl）执行单一 `_compute_pnl_metric`

#### Scenario: 新增 pnl 指标
- **WHEN** 需新增 pnl 分析角度
- **THEN** 仅在 `_PNL_METRIC_SPECS` 加条目，零行 `_compute_pnl_metric` 改动

### Requirement: 排序键 lambda 表（变更 L）

系统 SHALL 将 3 个 `_xxx_key` 方法替换为 `_ANGLE_SORT_KEYS` lambda dict。

#### Scenario: momentum 排序
- **WHEN** compute_analysis_angles 按 "momentum" 排序
- **THEN** `key_fn = _ANGLE_SORT_KEYS["momentum"]`（lambda 计算 pnl/entry_price）

### Requirement: 后过滤统一包装（变更 M）

系统 SHALL 在 `_FILTER_EVALUATORS` 分派点统一加 `_apply_stock_filters` 后过滤包装，消除 4 处重复调用。

#### Scenario: 公式/标量/集合路径后过滤
- **WHEN** _FILTER_EVALUATORS["formula"|"scalar"|"set_op"] 执行后
- **THEN** 包装器自动调用 `_apply_stock_filters(result, spec, state)`，evaluator 内部不再调用

#### Scenario: 透传/交集路径无后过滤
- **WHEN** _FILTER_EVALUATORS["pass_through"|"intersection"] 执行
- **THEN** 不加后过滤（保持原语义）

### Requirement: 集合校验归一化（变更 O）

系统 SHALL 将 `table_engine.py` `_validate_table` 的 dict/list 双分支归一化为 `_iter_entries(collection)` 迭代器。

#### Scenario: dict 集合校验
- **WHEN** collection 为 dict
- **THEN** `_iter_entries` yield `(f".{eid}", entry)`，错误定位为 `{collection_key}.{eid}`

#### Scenario: list 集合校验
- **WHEN** collection 为 list
- **THEN** `_iter_entries` yield `(f"[{idx}]", entry)`，错误定位为 `{collection_key}[{idx}]`

### Requirement: metatest v3 严格正反合测试套件

系统 SHALL 重新创建 `metatest/` 为 v3，按正反合三层方法论全面覆盖股票池平台前端+后端所有模块，评分完全由真实测试结果量化驱动。

#### Scenario: 正测试覆盖所有模块
- **WHEN** 运行 `python -m pytest metatest/test_positive_*.py`
- **THEN** 覆盖 17 个后端模块（engine/execution_module/runtime_mode_module/formula_module/domain/table_engine/schemas/trade_module/tick_bar_module/monitoring_module/screening_module/event_bus/import_export_module/web_state/tick_table/native/services）+ 前端全部模块（事件面板/工具栏/池设计器/公式编辑器/导入导出/模式切换）
- **AND** 每个后端模块 ≥ 1 测试文件，含本次 15 组模式合并的回归断言

#### Scenario: 反测试覆盖 4 类异常 + 同构复活检测
- **WHEN** 运行 `python -m pytest metatest/test_negative_*.py`
- **THEN** 覆盖 4 类（无效配置/运行时异常/API前端/底层逻辑违规），每类 ≥ 8 用例
- **AND** 含 15 组模式的「同构复活」反测试：Grep 断言旧同构代码（如 `_filter_condition_formula`、`json.load(open(`、`if mode == "inflection"`、BUY/SELL 双分支等）零匹配

#### Scenario: 合测试端到端集成
- **WHEN** 运行 `python -m pytest metatest/test_synthesis_*.py`
- **THEN** 覆盖仿真全流程/三模式/导入导出/热加载/元模式合并/前端 E2E/事件链顺序/水位线短路/编译-运行分离
- **AND** 前端 E2E 通过 Playwright 真实浏览器验证，环境缺失计失败（不给信用分）

### Requirement: metatest v3 量化评分引擎（12 维，真实测试结果驱动）

系统 SHALL 实现 12 维加权评分引擎，评分完全由真实测试结果计算，禁止随意评分或信用分。

#### Scenario: 12 维评分计算
- **WHEN** ScoringEngine.calculate(test_results) 调用
- **THEN** 计算 12 维：(1)module_coverage 10% (2)test_pass_rate 18% (3)assertion_density 8% (4)event_chain_integrity 10% (5)performance_benchmark 8% (6)frontend_e2e_pass_rate 10% (7)logic_coverage 8% (8)isomorphism_elimination 12% (9)line_convergence 8%（核心模块行数 ≤ 目标值满分） (10)rule_compliance 4%（RULES 91-100 Grep 零违规） (11)negative_test_coverage 2%（4 类反测试覆盖率） (12)synthesis_e2e 2%（合测试通过率）

#### Scenario: 跳过/环境缺失计失败
- **WHEN** 测试 skipped 或前端 E2E 环境缺失
- **THEN** 计为失败，分子不计入，不给信用分

#### Scenario: 同构检查扩展到 15 项
- **WHEN** 计算 isomorphism_elimination 维度
- **THEN** 检查 15 项（对应本次 15 组模式 + 原 6 项），每项违规扣 100/15 分

#### Scenario: 行数收敛度量化
- **WHEN** 计算 line_convergence 维度
- **THEN** 核心模块总行数 ≤ 23,000（当前 24,187 减 440 目标 23,747，再扣冗余至 23,000）满分，线性衰减

#### Scenario: PASS 门槛
- **WHEN** 总分计算完成
- **THEN** 总分 ≥ 95 且 12 维均 ≥ 80 判定 PASS，否则 FAIL 并输出 redo_list

## MODIFIED Requirements

### Requirement: metatest 评分引擎版本

metatest 评分引擎从 v2 的 8 维（权重和 100%）升级为 v3 的 12 维（权重和 100%）。新增 4 维：line_convergence（行数收敛度）、rule_compliance（规则合规度）、negative_test_coverage（反测试覆盖率）、synthesis_e2e（合测试 E2E 通过率）。原 8 维权重重新分配以容纳新增维度。同构检查项从 6 项扩展到 15 项，断言密度目标从 10/文件提升到 20/文件。

### Requirement: RULES.md 元模式规则集

RULES.md 在第 90 条基础上新增第 91-100 条，固化本次 15 组模式合并约束：第 91 条 nset 筛选函数值驱动、第 92 条 ConfigStore 配置加载统一、第 93 条 noperate mode 表驱动、第 94 条 base_period 目标表驱动、第 95 条 tradeattr BUY/SELL 表驱动、第 96 条导入导出 converter 统一入口、第 97 条公式 eval 核心合并、第 98 条同步协程执行器统一、第 99 条事件 handler 装饰器统一、第 100 条 pnl 计算表驱动。

## REMOVED Requirements

### Requirement: screening_module 4 个独立 nset 筛选函数
**Reason**: `_filter_condition_formula` 与 `_filter_expert_system` 逐字节相同；`_filter_financial_scalar` 与 `_filter_market_scalar` 仅 nset_label 不同，已合并为 `_filter_truthy` + `_filter_scalar`
**Migration**: `_NSET_FILTER_HANDLERS` 表路由：{1:_filter_truthy, 2:_filter_truthy, 3:_filter_scalar, 4:_filter_scalar}

### Requirement: import_export_module 6 个独立 _parse/_serialize 函数
**Reason**: 6 函数骨架完全相同（延迟 import + try/except ImportError 兜底逐字重复），仅 converter 模块/函数/调用签名不同
**Migration**: 合并为 `_call_converter(path, fmt, direction, config=None)` + `_CONVERTER_REGISTRY` 表

### Requirement: formula_module _eval_formula 与 _eval_formula_series 双实现
**Reason**: 7 步骨架完全同构，仅步骤 6 调用方法与步骤 7 结果转换不同
**Migration**: 合并为 `_eval_formula_core(series=False)`，两原函数改为薄包装

### Requirement: monitoring_module 5 个 _compute_xxx_pnl 方法
**Reason**: 共享「filter trackers → 提取字段 → 聚合 → return dict」骨架，仅 filter/extract/agg/key 不同
**Migration**: 合并为 `_PNL_METRIC_SPECS` 表 + 单一 `_compute_pnl_metric(spec)`

### Requirement: monitoring_module 3 个 _xxx_key 排序键方法
**Reason**: 3 个 @staticmethod 签名相同，仅字段提取与变换不同
**Migration**: 替换为 `_ANGLE_SORT_KEYS` lambda dict

### Requirement: runtime_mode_module 双 _run_coro 同步执行器
**Reason**: 骨架相同（复用持久 loop → run_until_complete → 降级线程），仅 loop 属性名不同
**Migration**: 合并为模块级 `_run_coro_sync(coro, loop_holder, loop_attr)`

### Requirement: 跨文件双 _build_topology
**Reason**: engine.py 与 runtime_mode_module.py 各维护一份拓扑构建，仅 edges 来源与字段 getter 不同
**Migration**: 合并为纯函数 `_build_adjacency(node_ids, edges_iter, src_getter, eid_getter)`

### Requirement: 20+ 处 _on_xxx try/except 样板
**Reason**: 5 模块 20+ 处逐字重复 `try: <body> except Exception as ex: logger.warning(...)`，且 exc_info=True 不一致
**Migration**: 统一为 `@_event_handler(name)` 装饰器
