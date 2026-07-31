# 元模式本质收敛 v11：审计盲区闭合 + 平行运行时消除 Spec

## Why

v10 文档化了「全局元模式收敛已达上限」(RULES 120「知止」纪律)，并据此停止进一步收敛。但 v11 架构工程师跨域深度审计发现 **v10 的「上限」是审计盲区制造的假象**——三项独立审计（同构函数 / 轮询模式 / OOP 继承纯度）均击穿了 v10 的结论，找到 5+ 处 v10 漏判的真同构合并点与 1 处活体轮询运行时。

**第十一层洞察（审计盲区是收敛上限的最大敌人）**：v10 的 metatest 轮询零容忍检查只 grep `core/runtime_mode_module.py` / `core/table_engine.py` / `services/data.py` 三个文件，**从未覆盖 `converters.py`**。而 `converters.py:2910 DZHPoolExecutor` 是一个 ~400 行的**活体平行运行时**——它用 `threading.Thread + while + _stop_event.wait(1) + time.time() - last >= interval_sec` 轮询调度边触发（converters.py:3183-3210），维护私有 `self._events` 列表绕过 EventBus（converters.py:2929/2951），用 `threading.Timer` 绕过 EventDriver（converters.py:2926），并被 `api.py:6386-6389 /pool/start` 端点活体启动。这个平行运行时**完整复制了 PoolEngine + execution_module + EventBus 的能力**，但用轮询而非事件驱动——直接违反用户硬约束「彻底事件驱动，禁止轮询」。

**真正的底层运行逻辑洞察**：`DZHPoolExecutor.execute_once()` 与 `PoolEngine.execute_pool()` 同构（一次性执行），`DZHPoolExecutor._run_loop()` 与 `PoolEngine.run_loop()` 同构（长时执行）——但前者用轮询，后者用 `asyncio.Event.wait()` + `loop.call_at` 事件驱动。**运行时应当只有一个真相源（PoolEngine），不应存在两个**。v11 消除 DZHPoolExecutor 平行运行时，将 `/pool/run` 与 `/pool/start` 端点统一委托到 PoolEngine，闭合「极致本质的运行时」的运行时单一真相源本质。

**诚实声明**：v11 不是对 v10 的否定——v10 在 `core/` 焦点目录内的收敛是真实的。v11 是对 v10「全局上限」声明的**范围修正**：上限仅适用于 `core/` 内部，`converters.py` 与跨域审计盲区仍有真同构合并机会。v11 闭合这些盲区后，全局收敛上限声明才真正成立。

## What Changes

### 阶段 1：DZHPoolExecutor 平行运行时消除（核心，最大单项收益）

- **变更 D1：删除 DZHPoolExecutor 轮询基础设施**。删除 `converters.py:2910-3317` 的 `DZHPoolExecutor` 类的以下成员：`_run_loop`（3183-3210）、`start`（3212-3224）、`stop`（3226-3240）、`_thread`（2927）、`_stop_event`（2928）、`_timers`（2926）、`_events`（2929）、`_log_event`（2951-2958）、`_edge_last_trigger`（2931）、`_start_time`（2930）。保留 `execute_once()`（一次性执行逻辑）与 `_init_nodes_edges` / `_init_mock_stocks` / `NodeStateMachine`（execute_once 依赖的状态机）。净 −80 ~ −100 行。
- **变更 D2：`/pool/start` 端点改为委托 PoolEngine**。`api.py:6377-6403` 的 `start_pool` 端点不再实例化 `DZHPoolExecutor` + 调 `executor.start()`，改为调 `request.app.state.engine.run_loop()`（事件驱动主循环，`asyncio.Event.wait()` + `loop.call_at`）。`/pool/stop` 端点改为调 `engine.stop_loop()`。`request.app.state._dzh_executors` 字典删除，统一用 `request.app.state.engine`。
- **变更 D3：`/pool/run` 端点保留 execute_once 委托**。`api.py:6355-6375` 的 `run_pool` 端点保留 `execute_once()` 一次性执行语义，但改为调 `engine.execute_pool(config)`（PoolEngine 已有此方法，api.py:5403 已在用）。删除 `from converters import DZHPoolExecutor` 死导入。
- **变更 D4：删除 api.py:5400 / 5470 死导入**。`api.py:5400` 和 `:5470` 的 `from converters import DZHPoolExecutor` 是死导入（只调 `engine.execute_pool`），直接删除。
- **变更 D5：`__init__.py` 导出清理**。`__init__.py:28/30/47` 的 `DZHPoolExecutor` 导出删除（若 D1 后 DZHPoolExecutor 类已不存在）或保留为 thin wrapper（若 execute_once 逻辑保留在 converters.py）。

### 阶段 2：DzhXmlExporter 死代码清除（最大行数收益）

- **变更 E1：删除 DzhXmlExporter 类**。`converters.py:3982-4310` 的 `DzhXmlExporter` 类（~328 行）是平行 DZH 导出器，不继承 `BasePoolConverter`，不复用 `DzhPoolConverter._serialize_cells`/`_serialize_flows`/`_finalize_xml`。全仓 grep `DzhXmlExporter` 仅命中类定义本身（1 处），零实例化、零导入。DZH 导出已由 `DzhPoolConverter.export_pool` → `export_dzh_xml` 唯一路径承担。整体删除。净 −328 行。

### 阶段 3：decode_tdx_action_hex 表驱动统一（继承纯度 + 表驱动）

- **变更 F1：删除 core/table_engine.py 重复实现**。`core/table_engine.py:842-898` 的 `DataBinder.decode_tdx_action_hex` / `encode_tdx_action_hex`（~56 行）与 `converters.py:1157 decode_action` + `converters.py:1206 _encode_action_raw` 算法完全相同（同 `>>28 &0xF` 高位 + `>>16 &0xFF` 字节位），但前者硬编码 `type_map={1:"buy_amount",2:"buy_shares",3:"sell_shares"}` / `byte_type_map={6:"buy",7:"sell"}`，后者表驱动读 `config/runtime/filter_action_rules.json`。删除 table_engine 重复实现。
- **变更 F2：配置表反射调用改指共享版**。`config/data/data_mappings.json:166` 和 `config/data/data_config.json:1728` 的 `transform_expr` 字符串反射调用从 `decode_tdx_action_hex` / `encode_tdx_action_hex` 改指 `decode_action` / `_encode_action_raw`（或将硬编码 map 合入 `filter_action_rules.json` 的 `tdx_high_type_map` / `tdx_byte_type_map` 子表，共享版按 pool_type 选子表）。

### 阶段 4：_call_converter tdx 分支消除 + TDX 自由函数归入 TdxPoolConverter（继承纯度）

- **变更 G1：消除 _call_converter tdx 分支**。`core/import_export_module.py:107-109` 的 `if auto and fmt == "tdx": result = mod._tdx_pool_to_frontend(result, pool_name)` 分支将 TDX 领域知识泄漏到通用调度层。修复：在 `BasePoolConverter` 新增 `_to_frontend(result, name)` 钩子（默认返回 result），`TdxPoolConverter` 覆盖为调 `_tdx_pool_to_frontend`。`_call_converter` 调 `converter.parse_pool()` + `converter.to_frontend()`，消除 fmt 分支。
- **变更 G2：TDX 自由函数归入 TdxPoolConverter**。`converters.py` 的 `tdx_to_internal`（4484-4640）、`convert_tdx_to_config`（4878-4906）、`_tdx_pool_to_frontend`（5908-5972）、`_load_tdx_pool_config`（5975-5982）四个模块级自由函数归入 `TdxPoolConverter` 作为方法或 `@staticmethod`。对齐 `DzhPoolConverter._build_result` 直接产出前端 dict 的契约。

### 阶段 5：同构函数合并（底层运行逻辑洞察）

- **变更 H1：_stock_code / _scode / _extract_code 四胞胎统一**。`core/domain.py:1103 _stock_code` 是规范版（全工程 27 处调用）。`core/runtime_mode_module.py:125 _scode`、`core/runtime_mode_module.py:2410 _extract_code`、`core/screening_module.py:153 _extract_code` 是本地重复定义。删除本地定义，全部改用 `domain._stock_code`；screening_module 的 `_extract_code` 加 `"Code"` 大写回退后委托。净 −10 ~ −15 行。
- **变更 H2：_normalize_period 双胞胎统一**。`services/data.py:56 _normalize_period`（表驱动版，用 `_PERIOD_ALIASES` dict）与 `app.py:250 _normalize_period`（内联 mapping dict 版）同构。app.py 改为 `from services.data import _normalize_period`，扩充 `_PERIOD_ALIASES` 补齐 app.py 缺失的 `d/day` 等别名。净 −8 行。
- **变更 H3：engine.py _ce_* 死导入删除**。`core/engine.py:39-42` 的 `_extract_edge_endpoint as _ce_extract_edge_endpoint` / `_ce_resolve_node_type` / `_ce_resolve_edge_type` / `_ce_normalize_nodes` 4 行死导入（全文件 grep `_ce_` 仅命中这 4 行 import，零使用点）。直接删除。净 −4 行。
- **变更 H4：api.py 死等价分支合并**。`api.py:6165-6168` 和 `api.py:6214-6217` 的 `if mode == 'real': ... elif mode == 'sdk': ...` 两段分支体逐字相同（`tq = TqAdapter(mock_mode=False)`）。合并为 `if mode in ('real', 'sdk'): tq = TqAdapter(mock_mode=False)`。净 −8 行。
- **变更 H5：engine.py TTL 注册双循环合并**。`core/engine.py:301-307`（edge TTL）和 `core/engine.py:310-316`（node TTL）两个 per-stock 循环控制流完全同构，仅 `register_ttl_spec` 的 3 个参数（owner_id / ttl_key / ttl_sec）不同。抽出 `_register_ttl_batch(driver, state, stocks, owner_id, ttl_key, ttl_sec, now_val, bus)` 助手。净 −6 ~ −8 行。

### 阶段 6：metatest v11 量化评审升级（盲区闭合闭环）

- **变更 M1：轮询零容忍检查扩展到 converters.py**。`metatest/runner.py` 的 `_collect_polling_violations`（或同等函数）的扫描文件列表从 `core/runtime_mode_module.py` / `core/table_engine.py` / `services/data.py` 扩展到包含 `converters.py`。新增检查：`converters.py` 内不得出现 `while + wait(N) + time.time()` 轮询模式（DZHPoolExecutor._run_loop 已删除后应零匹配）。
- **变更 M2：平行运行时检测**。新增 `_collect_parallel_runtime_violations()` 函数：检测 `converters.py` / `services/*.py` 内不得出现 `threading.Thread` + `while` + `_stop_event.wait` 组合的平行运行时模式（PoolEngine 的 `asyncio.Event.wait()` + `loop.call_at` 不在此列）。
- **变更 M3：死代码检测**。新增 `_collect_dead_code_violations()` 函数：AST 解析所有 class 定义，检测零实例化 / 零导入的类（如 DzhXmlExporter）。返回 `{"dead_classes": List[str], "count": int}`。
- **变更 M4：isomorphism_elimination 检查扩展**。ISOMORPHISM_CHECKS_TOTAL 从 41 → 44（新增 3 项：converters.py 轮询零容忍 / 平行运行时零容忍 / 死代码零容忍）。
- **变更 M5：test_results 新增字段**。`parallel_runtime_violations` / `dead_code_violations` / `converters_polling_violations` 三个字段。

### 阶段 7：RULES 修订 + 全量回归

- **变更 R1：RULES 120 修订**。v10 的 RULES 120「全局收敛上限知止」声明被 v11 修正为「**审计盲区闭合后的**全局收敛上限」。新增第 121 条：「**禁止平行运行时**：不得在 `converters.py` / `services/*.py` 内实现 `threading.Thread + while + wait(N)` 轮询调度平行运行时；所有长时执行必须委托 `PoolEngine.run_loop()`（`asyncio.Event.wait()` + `loop.call_at` 事件驱动），所有一次性执行必须委托 `PoolEngine.execute_pool()`。运行时只有一个真相源。」
- **变更 R2：全量回归**。metatest 总分 ≥ 95 且 22 维均 ≥ 80，eventtest 退出码 0，converters.py 轮询零匹配，平行运行时零匹配，死代码零匹配，DZH↔TDX roundtrip 保真。

## Impact

- Affected specs: converge-meta-essence-v10-handler-exception-coverage（v10「全局上限」声明范围修正——仅适用于 core/ 内部，converters.py 跨域仍有盲区）
- Affected code: `converters.py`（DZHPoolExecutor 消除 + DzhXmlExporter 删除 + TDX 函数归入子类）、`api.py`（/pool/start + /pool/run 委托 PoolEngine + 死导入删除 + 死等价分支合并）、`core/import_export_module.py`（_call_converter tdx 分支消除）、`core/table_engine.py`（decode_tdx_action_hex 重复删除）、`core/engine.py`（_ce_* 死导入删除 + TTL 双循环合并）、`core/runtime_mode_module.py`（_scode/_extract_code 统一）、`core/screening_module.py`（_extract_code 统一）、`app.py`（_normalize_period 统一）、`__init__.py`（DZHPoolExecutor 导出清理）、`config/data/data_mappings.json` + `config/data/data_config.json`（transform_expr 改指）、`metatest/runner.py` + `metatest/scoring.py` + `metatest/test_negative_polling.py`（盲区闭合检测）、`RULES.md`（120 修订 + 121 新增）

## ADDED Requirements

### Requirement: 平行运行时消除
The system SHALL NOT maintain any parallel runtime in `converters.py` or `services/*.py` that uses `threading.Thread + while + wait(N)` polling scheduling. All long-running execution MUST delegate to `PoolEngine.run_loop()` (event-driven via `asyncio.Event.wait()` + `loop.call_at`), and all one-shot execution MUST delegate to `PoolEngine.execute_pool()`.

#### Scenario: /pool/start 端点事件驱动
- **WHEN** client calls `POST /pool/start` with a pool config
- **THEN** the endpoint delegates to `request.app.state.engine.run_loop()` (event-driven), NOT to `DZHPoolExecutor.start()` (polling)

#### Scenario: /pool/run 端点单一真相源
- **WHEN** client calls `POST /pool/run` with a pool config
- **THEN** the endpoint delegates to `request.app.state.engine.execute_pool(config)`, NOT to `DZHPoolExecutor.execute_once()`

#### Scenario: converters.py 轮询零匹配
- **WHEN** metatest runner scans `converters.py` for polling patterns
- **THEN** finds zero matches of `while + wait(N) + time.time()` polling pattern

### Requirement: 死代码零容忍
The system SHALL NOT contain any class that is never instantiated and never imported (dead code). All classes must have at least one instantiation site or one import site.

#### Scenario: DzhXmlExporter 删除后零匹配
- **WHEN** metatest runner scans for dead classes
- **THEN** finds zero classes with zero instantiation and zero import

### Requirement: TDX 编解码表驱动统一
The system SHALL provide TDX action hex encode/decode through a single table-driven implementation (`decode_action` / `_encode_action_raw` reading `filter_action_rules.json`), prohibiting hardcoded `type_map` / `byte_type_map` duplication.

#### Scenario: table_engine.py 无重复编解码
- **WHEN** metatest runner scans `core/table_engine.py`
- **THEN** finds zero `decode_tdx_action_hex` / `encode_tdx_action_hex` definitions (deleted, replaced by shared table-driven version)

### Requirement: TDX 领域知识归入 TdxPoolConverter
The system SHALL encapsulate all TDX-specific conversion logic (tdx_to_internal / convert_tdx_to_config / _tdx_pool_to_frontend / _load_tdx_pool_config) inside `TdxPoolConverter` subclass, not as module-level free functions. The `_call_converter` dispatcher SHALL NOT contain `if fmt == "tdx"` branches.

#### Scenario: _call_converter 无 fmt 分支
- **WHEN** metatest runner scans `core/import_export_module.py:_call_converter`
- **THEN** finds zero `if fmt == "tdx"` / `if auto and fmt ==` branches (TDX post-processing moved to `TdxPoolConverter._to_frontend` override)

## MODIFIED Requirements

### Requirement: 全局元模式收敛上限（v10 → v11 范围修正）
v10 的 RULES 120「全局收敛上限知止」声明修正为：**审计盲区闭合后**的全局收敛上限。v10 的上限仅适用于 `core/` 内部；`converters.py` 与跨域审计盲区在 v11 闭合后，全局收敛上限声明才真正成立。metatest 的轮询零容忍检查必须覆盖 `converters.py`（v10 漏覆盖），平行运行时检测与死代码检测是 v11 新增的收敛保障维度。

## REMOVED Requirements

### Requirement: DZHPoolExecutor 平行运行时
**Reason**: DZHPoolExecutor（converters.py:2910-3317）是 ~400 行平行运行时，用 `threading.Thread + while + wait(1) + time.time()` 轮询调度边触发，维护私有 `self._events` 列表绕过 EventBus，完整复制 PoolEngine + execution_module + EventBus 的能力但用轮询而非事件驱动。违反用户硬约束「彻底事件驱动，禁止轮询」与「极致本质的运行时」。
**Migration**: `/pool/run` 端点改委托 `PoolEngine.execute_pool()`；`/pool/start` 端点改委托 `PoolEngine.run_loop()`；`/pool/stop` 端点改委托 `PoolEngine.stop_loop()`。`execute_once()` 一次性执行逻辑若与 `PoolEngine.execute_pool()` 有差异，吸收为 `PoolEngine` 方法或保留为 thin wrapper。

### Requirement: DzhXmlExporter 平行 DZH 导出器
**Reason**: DzhXmlExporter（converters.py:3982-4310，~328 行）是平行 DZH 导出器，不继承 BasePoolConverter，零实例化、零导入。DZH 导出已由 DzhPoolConverter.export_pool 唯一路径承担。
**Migration**: 无（死代码直接删除）。
