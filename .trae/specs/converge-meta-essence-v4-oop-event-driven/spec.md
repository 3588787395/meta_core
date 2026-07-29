# 元模式本质收敛 v4：OOP 同源继承 + 彻底事件驱动 + 第二轮深度同构合并 Spec

## Why

前三轮元模式收敛（`refactor-meta-pattern-unification` 规则 81-90、`deepen-meta-pattern-strict-metatest-v2` 迭代 7-12、`perfect-meta-essence-strict-metatest-v3` 15 组模式合并）累计净减约 440 行核心代码，metatest v3 报 96.94 PASS。但用户明确指出仍未达「极致本质的运行时」与「完善面向对象」，需在 v3 表层合并基础上推进**两个根因级洞察**：

1. **DZH/TDX 双实现并存违反 OOP**：经架构工程师对 `converters.py` / `api.py` / `app.py` / `services/*.py` / `core/import_export_module.py` / `native/*.py` 逐行洞察，确认存在 **25 组真正同构骨架**（同骨架、异数据），合计约 **560 行**可合并。典型如 `_parse_func_element` / `_parse_psatt_element` / `_parse_spinfo_element` 三函数仅元素名与 schema key 不同；`_add_func` / `_add_psatt` / `_add_spinfo` 三序列化器仅 attr 名与 ModelClass 不同；4-5 张并行的 DZH↔TDX 类型映射表（且 `converters.py:4450` 与 `:4882` 在 DZH type 3→TDX 3 vs 0 上互相矛盾）；`services/providers.py:557-673` 与 `core/import_export_module.py:64-180` 逐字节复制 `_extract_text_segments` / `_is_valid_formula` / `_extract_formula_from_binary` / `_decode_formula` 四个公式解码器。这违反用户硬约束「**完善面向对象，大智慧和通达信只作为继承，所有基础功能用相同代码。配置导入导出差异通过表驱动体现**」。

2. **12 处轮询违反事件驱动**：经架构工程师对 `core/runtime_mode_module.py` / `core/table_engine.py` / `app.py` / `services/data.py` / `web/js/*.js` 逐行洞察，确认存在 **12 处轮询违规**：(a) replay `_sync_play_loop` 与 simulation `_sync_sim_loop` / `auto_step_loop` 三处 `while True + time.sleep` 步进；(b) `ConfigStore.start_polling` 与 `services/data.py._file_watcher_loop` 两处文件 mtime 轮询（且 `watchdog` 事件驱动实现已并存存在）；(c) `services/data.py._refresh_with_backoff` / `_daily_loop` 两处 `asyncio.sleep` 周期刷新；(d) `app.py:1067-1200` SSE 流 `while True + run_in_executor(drain) + asyncio.sleep(0.05)` 50ms 队列轮询（且 `api.py:events_ws` 已用 `asyncio.Queue + await queue.get()` 实现正确路径）；(e) 前端 4 处 `setInterval` 轮询（`web/js/app.js:77` `/api/state/runtime` 1s、`web/js/ui.js:4249` `/reload` POST、`web/js/ui.js:4739` `/api/highlight-events` 降级轮询、`web/js/event-panel.js:2011` `/api/events/timer-queue` 1s）。这违反用户硬约束「**彻底事件驱动，禁止轮询**」与 RULES.md 第 30/39/41/74 条。

同时，经架构工程师对 `core/*.py` 8 个核心模块（共 24,187 行基线，v3 后 23,900 行）第二轮深度洞察，确认仍有 **30+ 组真正同构模式**（同骨架、异数据）尚未合并，合计约 **720 行**可合并，且其中多组跨模块重复违反 RULES.md 第 69 条「模块高内聚」与第 16 条「分派用 dict」。典型如 `_NodeBase.to_dict` / `_EdgeBase.to_dict` 逐行复制（仅返回类型注解不同）、`_hash_tick_data` 在 `runtime_mode_module.py:2791` 与 `tick_bar_module.py:869` 双实现（声称算法一致实则重写）、`monitoring_module.py` 24 个 `_adapter_X` 函数共享同一 `extract fields → build dict` 骨架、`table_engine.py` `ConfigStore.check_changes` 与 `ConfigStoreHotReloadManager.check_and_reload` 70 行近乎全拷贝。

本次迭代（v4）将：(1) 通过 OOP 抽象基类 + 表驱动彻底消除 25 组 DZH/TDX 同构，实现「大智慧和通达信只作为继承，所有基础功能用相同代码，配置导入导出差异通过表驱动体现」；(2) 通过 EventDriver heapq + asyncio.Queue + watchdog + SSE/WS 订阅彻底消除 12 处轮询，实现「彻底事件驱动，禁止轮询」；(3) 通过 mixin + 表驱动合并 core/*.py 30+ 组第二轮同构至极致本质运行时；(4) 重建 metatest v4 严格正反合测试，评分从 v3 的 12 维扩展到 16 维（新增 `oop_inheritance_depth` / `polling_zero_tolerance` / `primitive_convergence` / `essence_ratio` 四维，真实结果驱动）；(5) 按架构工程师 → 评审工程师迭代流程推进 5 阶段。

**第四层洞察（本次迭代新增的根因收敛）**：经更深架构洞察，确认上述三大原语并非三个独立机制，而是同一元模式「数据驱动分派（DDD）」的三个投影——极致本质运行时只有一条公理 `Code = Data + Dispatcher`，并由**运行时三核 Dispatcher**（`EventBus` + `EventDriver` + `ConfigStore`，均已存在）承载所有业务逻辑。67 处违规的本质是「为应该作为 Data 的东西重新实现 Dispatcher 副本」（详见后文「第四层洞察」段）。因此本次迭代的核心是**删除绕过三核 Dispatcher 的 67 处局部重写，强制收敛到已存在的原语**，而非创建新机制——这正契合用户硬约束「让你洞察运行逻辑精减代码，不是让你拆分代码」。

## 深层运行逻辑洞察：三大统一原语（极致本质运行时的根因）

经架构工程师对全代码库逐行洞察，确认所有 55+ 组同构模式与 12 处轮询违规**并非 67 个独立问题**，而是**违反了已存在的三大统一原语**。极致本质运行时 = 全代码库无例外地使用这三大原语。本次迭代的核心不是「创建新机制」，而是「删除绕过原语的 67 处局部重写，强制收敛到已存在的原语」。

### 原语一：统一时间原语 — EventDriver + TimedEventSpec + heapq（已存在于 execution_module.py:169 + domain.py:1541）

**洞察**：`EventDriver` 已实现 `add_spec(spec, first_fire_time) → heapq.heappush` + `fire_due(now) → heapq.heappop + action + reschedule` 完整链路。12 处轮询违规的本质是**绕过已存在的 EventDriver，用 `while condition + sleep + check` 重新发明时间调度**。

**统一替换映射**（12 处轮询 → 1 原语）：
- replay `_sync_play_loop` `while True + time.sleep(interval)` → `EventDriver.add_spec(step_spec, now + base_interval/speed)`，回调 `_do_step()` + reschedule
- simulation `_sync_sim_loop` `while self._run + time.sleep` → `EventDriver.add_spec(sim_spec, now + 1.0/speed)`，回调 `step_simulation()` + reschedule
- simulation `auto_step_loop` `while _sim_auto_step + asyncio.sleep` → 同上（死代码删除）
- `ConfigStore.start_polling` `while _running + asyncio.sleep + mtime` → `watchdog.Observer.on_modified → EventBus.publish(FileModified)`（文件变化即事件，无需轮询）
- `services/data.py._file_watcher_loop` 同上
- `_refresh_with_backoff` `while _running + asyncio.sleep` → `EventDriver.add_spec(refresh_spec, now + interval)` + reschedule
- `_daily_loop` `asyncio.sleep(wait_seconds)` → `EventDriver.add_spec(daily_spec, next_target)` + reschedule next day
- SSE `while True + run_in_executor(drain) + asyncio.sleep(0.05)` → `asyncio.Queue + await queue.get()`（阻塞即事件，无需轮询）
- 前端 4 处 `setInterval + fetch` → `EventSource/WebSocket subscription`（推送即事件，无需拉取）

**原语不变量**：`grep -r "while.*sleep" core/*.py services/*.py app.py web/js/*.js` 在非测试代码 = 0。所有时间触发经 EventDriver heapq 或 asyncio.Queue 阻塞等待或 watchdog 事件推送。

### 原语二：统一分派原语 — `TABLE: Dict[K, Spec] + dispatch(k, *args) → action(spec, *args)`

**洞察**：所有 if/elif 链与同构函数集合的本质是**同一分派模式的不同写法**。统一为声明式表 + 通用 builder/dispatcher 单循环。RULES.md 第 16 条「分派用 dict」的极致形态。

**统一替换映射**（30+ 组同构 → 1 原语）：
- 24 个 `_adapter_X` → `_ADAPTER_SPECS: Dict[event_type, {top_fields, details_fields}]` + `_build_adapter_record(spec, event)`
- `_execute_buy` / `_execute_sell` → `_SIDE_SPECS: Dict[side, spec]` + `_execute_trade(side, ...)`
- `_apply_psatt_side_effects` 5 if → `_PSATT_SIDE_EFFECTS: List[(flag, kind, builder)]` 单循环
- 7 模块 `_register_subscribers` → `_SUBSCRIPTIONS: ClassVar[List[(EventType, handler_name)]]` + `_BaseModule.register_subscribers` 单循环
- `compute_pk_ranking` / `compute_analysis_angles` → `_RANKING_SPECS` 表 + `_compute_ranking(spec)`
- `_compile_timing/filter/propagate_spec` → `_compile_spec(params, fields_table)` + 3 表
- `_make_edge/ttl_interval/ttl_endtime_action` → `_make_publishing_action(...)` + 3 表
- `_gate_before/after_open/close` → `_gate_window(anchor, offset, now, before)` + 4 委托
- 3 个 `_get_week/month/day_key` → `_DATE_KEYS` 表 + 查表
- TDX flow 11 字段 getattr → `xml_mapping.json:flow.attributes` 表迭代
- `_make_tdx_cell` 3 分支 → 单一入口 + `**extra`
- 6 个 `_export_field_*` → `_export_field_table` + 单循环

**原语不变量**：同构函数集合（≥3 个共享骨架的函数/if 分支）必须收敛为 1 表 + 1 builder/dispatcher。`grep "def _adapter_\w+|def _execute_buy\b|def _execute_sell\b|def _compile_timing_spec\b" core/*.py` = 0。

### 原语三：统一继承原语 — `Base.common_method() + Sub.difference_only()`

**洞察**：所有跨格式/跨模块同构函数的本质是**同一算法的不同数据**。OOP 的极致形态：基类承载算法骨架，子类仅声明数据差异（int_fields / encoding_priority / post_hook / spec 表）。RULES.md 第 91-100 条「同构合并」的 OOP 根因收敛。

**统一替换映射**（25 + 12 组同构 → 1 原语）：
- DZH/TDX 25 组 → `BasePoolConverter` + `DzhPoolConverter` / `TdxPoolConverter`（**阶段 1 已完成**）
- `_NodeBase` / `_EdgeBase` to_dict/from_dict → `_FieldedBase._common_to_dict/_common_from_dict` + 4 薄包装
- 6 个 Pydantic from_dict → `_DictConstructible.from_dict` mixin
- per-content MD5 6 处 → `hash_dict_content(content, exclude)`
- aggregate tick hash 3 处 → `hash_tick_aggregate(tick_data, per_code_hasher)`
- bar_hash accessor 3 处 → `BarHashMixin.bar_hash`
- `check_changes` / `check_and_reload` → `ConfigStoreBase.check_and_reload` 模板方法 + 2 hook
- `rollback` 双实现 → `ConfigStoreBase.rollback` 模板方法 + 1 hook
- 5 个 `XStep` → `Step` 基类 + 5 子类
- safe_cast 5 处 → `converters/_common.safe_cast` + thin wrapper

**原语不变量**：公共方法必须在基类/mixin，子类仅含差异声明（数据表 / hook / override）。`grep "def to_dict\b" core/domain.py` 仅匹配 mixin + 薄包装。继承深度 ≥ 2（Base → Sub）。

### 三原语的量化评审（metatest v4 新增量化指标）

**`primitive_convergence`（原语收敛度，新增第 15 维，权重 8%，从 v3 12 维降权重分配）**：
- 时间原语覆盖率 = (经 EventDriver/Queue/watchdog 的时间触发数) / (总时间触发数) × 100
- 分派原语覆盖率 = (表驱动分派数) / (表驱动 + if/elif + 同构函数) × 100
- 继承原语覆盖率 = (基类公共方法数) / (基类 + 子类同构方法总数) × 100
- 三原语均 ≥ 95% 满分，线性衰减

**`essence_ratio`（本质比，新增第 16 维，权重 4%）**：
- 净减行数 / 变更前行数 × 100（衡量「合并」而非「拆分」）
- 目标 ≥ 12%（1,280 / 10,500 估算）
- **反向约束**：若变更后行数增加（净增），essence_ratio = 0 且 redo（强制「合并非拆分」）

**「禁止拆分」硬约束**（量化）：
- 每处变更必须净减行数（净增行数 > 0 的变更计 redo）
- 新建文件仅允许 `converters/_common.py` / `core/_hashing.py`（公共下沉，非拆分）
- 抽象基类/mixin 必须伴随 ≥ 2 处子类收敛（否则计「无效抽象」redo）

### 第四层洞察：三原语的元统一 — 数据驱动分派（DDD），极致本质运行时的终极形态

经第四层架构洞察，确认上述三大原语**并非三个独立机制，而是同一元模式（Data-Driven Dispatch）的三个投影**。极致本质运行时的终极本质只有一条公理：

> **Code = Data + Dispatcher**。每行业务代码要么是 Data 声明（spec 表 / 子类差异字段 / 配置 JSON / TimedEventSpec），要么是 Dispatcher 调用（基类方法 / 通用 builder / `EventBus.publish` / `EventDriver.schedule` / `ConfigStore.get_table`）。既非 Data 又非 Dispatcher 的「过程式重写」即违规。

**三原语的元统一映射**（证明三者同构）：

| 原语 | Data（声明一次） | Dispatcher（实现一次） | 违规形态（67 处的本质） |
|------|------------------|----------------------|------------------------|
| 时间原语 | `TimedEventSpec(fire_time, action, interval)` | `EventDriver.fire_due()` heapq + `loop.call_at` | `while True + sleep + check` 重新发明时间调度 |
| 分派原语 | `TABLE: Dict[K, Spec]` | `dispatch(k, *args) → action(spec, *args)` | if/elif 链 + 同构函数集合重新发明分派 |
| 继承原语 | `Sub.difference_table`（int_fields / encoding_priority / post_hook / spec） | `Base.common_method(self)`（self 携带 Data） | 跨格式/跨模块复制算法骨架重新发明继承 |

三者共享同一骨架：**声明 Data → 单一 Dispatcher 按 Data 执行**。差异仅在 Data 的载体（spec 对象 / dict 表 / 子类字段）与 Dispatcher 的入口（heapq / dict 查表 / 方法分派）。这证明 67 处违规不是 67 个问题，而是**同一根因（绕过已有 Dispatcher 重写 Data 副本）的 67 次复现**。

**运行时三核 Dispatcher**（已存在，禁止再造第四个）：

极致本质运行时 = 以下三个 Dispatcher + N 份 Data 声明，再无其他：

1. **`EventBus`**（`core/event_bus.py`）— event-type → handler 分派器（订阅/发布）。所有跨模块通信经此。
2. **`EventDriver`**（`core/execution_module.py:169`）— fire-time → action 分派器（heapq 调度）。所有时间触发经此。
3. **`ConfigStore`**（`core/table_engine.py`）— table-name → table-data 分派器（热加载 + 单一真相源）。所有配置读取经此。

所有业务模块 = 声明 Data（`_SUBSCRIPTIONS` / `_ADAPTER_SPECS` / `_SIDE_SPECS` / `TimedEventSpec` / `dzh_type_map.json` / 子类差异字段）+ 调用三核 Dispatcher（`bus.publish` / `driver.schedule` / `store.get_table`）+ 必要胶水（import / 类声明 / Data 装载）。**禁止任何模块重新实现这三核的等价物**（如自造事件循环 / 自造调度器 / 自造配置加载）。

**元纯度量化**（`primitive_convergence` 维度的根因解释，不新增维度以避免指标膨胀）：

- `meta_purity` = (Data 声明行数 + Dispatcher 调用行数) / 总业务行数 × 100
- 目标 ≥ 90%（剩余 ≤ 10% 为必要胶水：import / 类声明 / Data 装载 / 顶层控制流）
- 此指标作为 `primitive_convergence` 三原语覆盖率的**根因解释层**：三原语覆盖率 ≥ 95% 当且仅当 meta_purity ≥ 90%（因为每处过程式重写既拉低原语覆盖率又拉低 meta_purity）

**元不变量**（评审工程师最终验收的根因检查）：
- Grep `while\s+True|while\s+self\._\w+\s*[:)]` 在 core/*.py + services/*.py 非测试代码 = 0（无自造事件循环）
- Grep `time\.sleep|asyncio\.sleep\(\d` 在 core/*.py + services/*.py + app.py 非测试代码 = 0（无自造时间调度，EventDriver 唯一）
- Grep `def _safe_int\b|def _safe_float\b|def _hash_\w+\b|def _adapter_\w+\b|def _execute_buy\b|def _execute_sell\b|def _compile_\w+_spec\b` 在 core/*.py + services/*.py = 0（无自造分派同构副本，表/基类唯一）
- Grep `get_global_config_store\(\)\.get_table.*\n.*if get_global_config_store` 在 core/*.py = 0（无绕过 ConfigStore 的配置读取，ConfigStore 唯一）
- `wc -l core/*.py` ≤ 22,500（Data + Dispatcher 净减后行数）

## What Changes

### 阶段 1：DZH/TDX OOP 同源继承（高优先级，OOP 完善核心）

- **变更 P1：引入 `BasePoolConverter` 抽象基类 + `DzhPoolConverter` / `TdxPoolConverter` 子类**。统一 25 组同构骨架：元素解析（`_parse_func/psatt/spinfo_element` 3 函数→1 泛型解析器 + 3 后处理 hook）、元素序列化（`_add_func/psatt/spinfo` 3 函数→1 工厂 + 3 行表）、cell 转换器封装（DZH `_parse_cell_pool/market/condition` 与 TDX `_convert_candidate/state_pool/condition_cell` 共用 `_CellEnvelope` 基类）、cell 工厂（`_build_cell_default/pool/market` 共用 6 行开头骨架）、`<stk>` 子元素解析与序列化（`_parse_stk_children` / `_parse_stk_elements` 共用 `_StkIO` mixin、`_export_field_stocks` / `_add_stks` 共用 `_StkWriter`）、pos 解码（`_parse_pos` / `_parse_tdx_pos` 共用 `_decode_pos(pos_str, *, as_dict)`）、XML 多编码解码（`_decode_xml_content` / `_decode_tdx_xml` 共用 `_decode_xml_bytes(raw, encoding_priority, post_process)`）、TDX flow `getattr(...) if None: fallback` 11 字段表驱动（读 `xml_mapping.json:flow.attributes`）、`_make_tdx_cell` 3 分支 8 kwargs 重复改为 `_make_tdx_cell(internal_type, tdx_cell, pos, **extra)` 单一入口。**BREAKING**：删除 25 组同构函数。净减约 350 行。
- **变更 P2：统一 DZH↔TDX 类型映射为单一真相源 `_DZH_TDX_TYPE_MAP`**。消除 5 处并行表：`converters.py:4450 _DZH_TO_TDX_TYPE`、`converters.py:4882 _DZH_TO_TDX_TYPE_EXPORT`、`core/schemas.py:954 TDX_TO_DZH_CELL_TYPE` + `TDX_CELL_TYPE_MAP`、`config/xml_mapping.json:dzh_to_tdx_type`、`config/architecture/dzh_type_map.json:tdx_type_map`。收敛为单一 `config/architecture/dzh_type_map.json` 表（含 `dzh_to_tdx` / `tdx_to_dzh` / `tdx_to_frontend` / `frontend_to_tdx` 四向映射），所有读取通过 `ConfigStore.get_table("dzh_type_map")`。**BREAKING**：修复 `converters.py:4450` 与 `:4882` 在 DZH type 3 映射上的矛盾（一处 → TDX 3、一处 → TDX 0）。净减约 25 行。
- **变更 P3：`_CONVERTER_REGISTRY` 扩展为完整 OOP 路由表 + 强制所有调用站点走 registry**。消除 6 处绕过 registry 的直接调用：`api.py:5396-5407 dzh_import` / `:5509-5520 dzh_import_and_save` / `:6828-6835 dzh_load_demo` 三处 `is_tdx_format` 分支硬编码（共享同一骨架）；`app.py:698-802` TDX 文件 CRUD 4 端点（`_tdx_load` / `_tdx_create` / `_tdx_save` / `tdx_export_xml`）直接调用 `parse_tdx_xml` / `_build_tdx_xml`；`api.py:5573 dzh_export` 直接调用 `export_meta_to_dzh_xml_bytes`；`app.py:2561 load_dzhpool_file` 直接调用 `parse_dzh_xml`。统一改为 `_call_converter(path_or_data, fmt, direction, config=None)` 入口，fmt 由 `is_tdx_format` 自动探测或显式传入。同时合并 `dzh_import` 与 `dzh_import_and_save` 共享的内容加载骨架（58 行近乎逐字重复，含 6 行多编码解码循环 3 处复制）为 `_load_xml_content_from_request(request)` 单一辅助。**BREAKING**：删除 6 处直接调用站点。净减约 110 行。
- **变更 P4：公共工具函数下沉到 `converters/_common.py`**。消除跨模块重复：`_safe_int` / `_safe_float`（`core/import_export_module.py:39-56` 与 `native/validators.py:2182-2188` 逐字节重复）、`_decode_formula` 系列（`services/providers.py:557-673` 与 `core/import_export_module.py:64-180` 逐字节复制，约 120 行）、`_load_dzh_type_map`（`core/schemas.py:96` 与 `converters.py:2641` 双实现，仅错误策略不同）。所有模块通过 `from converters._common import safe_int, safe_float, decode_formula, decode_xml_bytes, decode_pos, hash_dict_content` 统一引用。`native/builtins.py:60-73 _decode_formula_base64` 改为 thin wrapper。**BREAKING**：删除 `services/providers.py` 中 4 个公式解码器副本与 `native/validators.py` 中 `_safe_int` 副本。净减约 130 行。

### 阶段 2：彻底事件驱动（高优先级，禁止轮询）

- **变更 E1：replay 步进改为 EventDriver heapq 调度**。删除 `core/runtime_mode_module.py:726-748 _sync_play_loop` 的 `while True + time.sleep(interval)` 线程，改为：(a) `play()` 调用 `EventDriver.schedule(step_event, fire_time=now + base_interval/speed)` 入 heapq；(b) 步进事件到时由 `loop.call_at` 触发 `_do_step()`，并在回调中重新调度下一个事件（speed 变化时重新计算 fire_time）；(c) `pause()` 通过 `EventDriver.cancel(step_event_id)` 取消调度，`resume()` 重新调度；(d) `stop()` 清空 heapq 中所有 step 事件。**BREAKING**：删除 `_sync_play_loop` 与 `time.sleep` 调用。净减约 15 行 + 修复规则 30/74 违规。
- **变更 E2：simulation auto-step 改为 EventDriver heapq 调度**。删除 `core/runtime_mode_module.py:2122-2138 _sync_sim_loop`（`while self._run + time.sleep`）与 `:2509-2525 auto_step_loop`（`while _sim_auto_step and _current_mode + asyncio.sleep`，后者疑似死代码但仍存在违规模式）两处。改为：(a) `start_auto()` 调用 `EventDriver.schedule(sim_step_event, fire_time=now + 1.0/speed)`；(b) 步进事件到时由 `loop.call_at` 触发 `step_simulation(step_idx)`，并在回调中重新调度；(c) 停止通过 `ModeChanged` 事件订阅或 `EventDriver.cancel` 取消，禁止 `_run` / `_sim_auto_step` 标志轮询。**BREAKING**：删除两处步进循环与对应标志轮询。净减约 25 行 + 修复规则 30/74 违规。
- **变更 E3：移除 `ConfigStore.start_polling` 与 `services/data.py._file_watcher_loop` 两处文件 mtime 轮询**。`core/table_engine.py:1700-1715 start_polling` 与 `services/data.py:5994-6005 _file_watcher_loop` 共享同一反模式：`while _running + asyncio.sleep(N) + 比较文件 mtime`。`watchdog.Observer` 事件驱动实现已在 `table_engine.py:1722+ start_watchdog` 与 `_start_file_watcher` 调用链中并存。改为：(a) 删除 `start_polling` 方法与 `_file_watcher_loop` 函数；(b) `services/data.py` 改为依赖 `watchdog.Observer` 发布 `FileModified` 事件到 EventBus，`DataManager` 订阅该事件重新加载；(c) `app.py:507` 已调用 `start_watchdog`，无需改动。**BREAKING**：删除两处轮询实现。净减约 25 行 + 修复规则 30/74 违规。
- **变更 E4：SSE 流改为 `asyncio.Queue + await queue.get()`**。删除 `app.py:1067-1200 events_stream` 内的 `while True + run_in_executor(drain_sync_queue) + asyncio.sleep(0.05)` 50ms 队列轮询。改为：(a) 每会话创建 `asyncio.Queue(maxsize=10000)`；(b) EventBus 订阅回调 `queue.put_nowait(event_data)`；(c) 流循环 `asyncio.wait_for(queue.get(), timeout=15.0)` 阻塞等待事件，超时发心跳；(d) `request.is_disconnected()` 检测保留作为退出条件。镜像 `api.py:824-862 events_ws` 已验证的 `asyncio.Queue + await queue.get()` 模式。**BREAKING**：删除 `drain_sync_queue` 与 `time.sleep(0.05)`。净减约 20 行 + 修复规则 39 违规。
- **变更 E5：`services/data.py` 三处 `asyncio.sleep` 周期刷新改为 EventDriver heapq 调度**。删除 `_refresh_with_backoff`（5785-5842 `while _running + asyncio.sleep`）、`_daily_loop`（5740-5763 `asyncio.sleep(wait_seconds)` 调度次日）两处。改为：(a) 周期刷新入 heapq `TimedEventSpec(fire_time=now+interval, action=refresh_fn)`，触发后重新调度；(b) 每日定时入 heapq `TimedEventSpec(fire_time=next_target, action=daily_reload)`，触发后重新计算次日 fire_time；(c) 文件变化驱动的刷新改为订阅 watchdog 事件（与 E3 协同）。**BREAKING**：删除两处 sleep 调度。净减约 30 行 + 修复规则 30 违规。
- **变更 E6：前端 4 处 `setInterval` 轮询改为 SSE/WS 订阅**。删除：(a) `web/js/app.js:66-87 RuntimeState._poll + setInterval(...,1000)` 改为订阅 `ModeChanged` / `SnapshotUpdated` SSE 事件更新 `mode` / `displayNowMs` / `activeSessionId`；(b) `web/js/ui.js:4247-4256 _startHotReload + setInterval POST /reload` 改为订阅 `/api/config/ws` WebSocket 的 `ConfigChanged` 推送；(c) `web/js/ui.js:4736-4757 HighlightManager.startPolling + setInterval fetch /api/highlight-events` 改为仅依赖 `/ws/highlight` WebSocket 推送，移除降级轮询（同时移除 `app.py:2521-2524` 空响应端点）；(d) `web/js/event-panel.js:2011 setInterval(syncTimerQueue,1000) + fetch /api/events/timer-queue` 改为从 SSE 流的 `TimerQueued` / `TimerFired` 事件更新 `timerQueue`。**BREAKING**：删除 4 处前端轮询 + 1 处后端空端点。净减约 60 行（前端 40 + 后端 20）+ 修复规则 39/41 违规。

### 阶段 3：core/*.py 第二轮深度同构合并

#### 阶段 3a：domain.py 与 schemas.py 的 OOP 完整收敛

- **变更 C1：引入 `_FieldedBase` mixin，合并 `_NodeBase` / `_EdgeBase` 的 `to_dict` / `from_dict` 逐行复制**。`core/domain.py:172-200 _NodeBase.to_dict/from_dict` 与 `:502-530 _EdgeBase.to_dict/from_dict` 共 4 方法 ~52 行仅返回类型注解不同，合并为 `_FieldedBase._common_to_dict/_common_from_dict` ~16 行 + 4 个 1 行薄包装。同时合并 `from_dzh_type` / `from_tdx_type` / `from_dzh_attr` / `from_tdx_source_type` 4 个 classmethod（共享 `_lookup_in_registry(registry, t, label)` 骨架，仅 registry 名与错误消息不同）。净减约 54 行。
- **变更 C2：合并 schemas.py 6 个 `from_dict` classmethod 为 `_DictConstructible` mixin**。`core/schemas.py:1046/1085/1132/1174/1215/1250` 6 处 `from_dict` 逐字相同（仅返回类型注解不同），合并为 mixin `@classmethod from_dict(cls, data) -> cls`。同时合并 `_validate_ndeltype` / `_validate_type` 等 field-validator（`if v not in ALLOWED: raise ValueError`）。净减约 23 行。

#### 阶段 3b：哈希函数三族统一（跨模块）

- **变更 C3：引入 `_hashing.py` 模块统一三族哈希函数**。(a) **per-content MD5**：`core/domain.py:1906-1921 _hash_tick` / `core/tick_bar_module.py:322-328 _hash_bar` / `core/formula_module.py:1084-1090 _hash_bars` / `:1562-1573 _hash_object` / `:1058-1081 _hash_code_bars` 共 6 处共享 `json.dumps(content, sort_keys=True) → except → str(sorted()) → md5` 骨架，仅 EXCLUDE_SET 不同，合并为 `hash_dict_content(content, exclude=())`；(b) **aggregate tick hash**：`core/runtime_mode_module.py:2791-2815 PoolStateMixin._hash_tick_data` 与 `core/tick_bar_module.py:869-889 _InternalState._hash_tick_data` 与 `:409-416 _hash_period_bars` 共 3 处共享 `for code in sorted: parts.append(f"{code}:{per_hash}") → md5("\x00".join(parts))` 骨架，合并为 `hash_tick_aggregate(tick_data, per_code_hasher)`；(c) **bar_hash accessor**：3 处 `return self.X.get("_hash", "")` 共享 accessor，合并为 mixin `BarHashMixin.bar_hash`。**BREAKING**：删除 6+3+3 = 12 处重复实现。净减约 54 行。
- **变更 C4：合并 `safe_cast` 跨模块统一到 `converters/_common.py`（与 P4 协同）**。`core/import_export_module.py:39-56 _safe_int/_safe_float` / `core/web_state.py:135-142 _to_float` / `core/execution_module.py:875-880 _cast_int/_cast_str` 共 5 处 helper 定义共享 `if v is None or v == "": return default; try: return CAST(v); except: return default` 骨架，合并为 `safe_cast(v, cast_fn, default, empty_check=True)` + 4 个 thin wrapper。同时消除 ~12 处 `try: float(X) except (TypeError, ValueError): default` 内联样板（runtime_mode/execution/web_state/formula/screening/monitoring 6 模块）。净减约 47 行。

#### 阶段 3c：table_engine.py 热加载三件套统一

- **变更 C5：引入 `ConfigStoreBase` 基类，合并 `ConfigStore.check_changes` 与 `ConfigStoreHotReloadManager.check_and_reload`**。`core/table_engine.py:460-534` 与 `:1594-1676` 两函数 ~70 行近乎全拷贝，共享 `iter_config_files → md5 → skip → json.loads → 3 层校验 → swap → record_config_version → log` 10 步骨架，仅校验入口与 table-storage 属性不同。合并为基类 `check_and_reload()` 模板方法 + 2 个 thin override hook（`_validate(name, data)` / `_commit(name, data)`）。同时合并 `rollback()` 双实现（540-558 与 1678-1698）与 3 层校验调用骨架（479-500 与 1576-1590）。**BREAKING**：删除 2 个全拷贝函数 + 2 个 rollback 重复。净减约 92 行。

#### 阶段 3d：monitoring_module.py 表驱动收敛

- **变更 C6：24 个 `_adapter_X` 改为 `_ADAPTER_SPECS` 声明式表 + 通用 builder**。`core/monitoring_module.py` 24 个 adapter（`_adapter_tick_received` / `_adapter_data_changed` / ... / `_adapter_mode_changed`）共享 `extract fields → build {"event_type", "details"} dict` 骨架，仅字段提取与 details 形状不同。改为 `_ADAPTER_SPECS = {"TickReceived": {"top_fields": [...], "details_fields": [...]}}` 表 + `_build_adapter_record(spec, event)` 通用 builder。`EVENT_RECORD_ADAPTERS` 表的 value 从函数引用改为 spec key 字符串。净减约 140 行（v4 单点最大收益）。
- **变更 C7：合并 `compute_pk_ranking` / `compute_analysis_angles` 与 `publish_rankings` 表驱动**。`core/monitoring_module.py:1095-1124` 与 `:1126-1150` 共享 `if not cfg: return {} → try → candidates filter → sort → build dict → except log return {}` 骨架，合并为 `_compute_ranking(cfg_attr, store_attr, sort_key, builder, label)`。`publish_rankings`（1152-1167）2 处 try/except + publish 重复改为迭代 `[(compute_fn, dimension, label), ...]` 表。净减约 34 行。

#### 阶段 3e：execution_module.py 表驱动收敛

- **变更 C8：合并 `_compile_X_spec` 3 函数 + `_make_X_action` 3 函数 + `XStep` 5 类 + `_gate_before/after_X` 4 函数**。`core/execution_module.py:1280-1310` 3 个 `_compile_X_spec` 共享 `return {field: cast(params.get(field, DEFAULT) or DEFAULT) for ...}` 骨架，改为 `_compile_spec(params, fields_table)` + 3 表 decl。`:3296-3359` 3 个 `_make_X_action` 共享 `def action(params, fire_time=None): ts = ...; _publish(bus, XEvent(...))` 骨架，改为 `_make_publishing_action(state, bus, event_factory, pre_check=None)`。`:3155-3247` 5 个 `XStep` 类共享 `__init__(self, executor) + def run(self, ctx): ...; return StepResult(should_continue=True)` 骨架，提取 `Step` 基类。`:1917-1938` 4 个 `_gate_before/after_X` 共享 `anchor ± offset vs now_sec` 比较，改为 `_gate_window(anchor, offset, now_sec, before=True)`。同时合并 3 个 `_publish_X` 1 行薄包装（`_publish_edge_fired` / `_publish_ttl_due`，调用方直接用 `_publish(bus, Event(...))`）。净减约 63 行。

#### 阶段 3f：trade_module.py 表驱动收敛

- **变更 C9：合并 `_execute_buy` / `_execute_sell` 与 `_apply_psatt_side_effects` 表驱动**。`core/trade_module.py:322-377` 2 个 `_execute_X` 共享 8 步骨架（compute qty → compute price → update position → update cash → append trade → persist → log），改为 `_execute_trade(side, signal, ...)` + `_SIDE_SPECS = {"BUY": {...}, "SELL": {...}}` 表。同时 `_PaperTradeEngine.buy` / `sell`（491-565）同构 70 行合并为 `_paper_trade(side, signal, ...)`。`:1132-1200 _apply_psatt_side_effects` 5 标志 if 分支改为 `_PSATT_SIDE_EFFECTS = [(flag_attr, event_kind, payload_builder), ...]` 表迭代。净减约 70 行。

#### 阶段 3g：runtime_mode_module.py 表驱动收敛

- **变更 C10：合并 `_aggregate_bars` / `_group_and_synthesize` OHLCV 字面量 + 3 个 `_X_key` 日期函数 + 6 个 replay 控制方法**。`core/runtime_mode_module.py:1008-1062` 2 个聚合函数共享 9 行 OHLCV dict 字面量，提取 `_aggregate_ohlcv(group)` helper。`:1026-1036` 3 个 `_get_week/month/day_key` 共享 `dt.strftime(FMT)` 骨架，改为 `_DATE_KEYS = {"day": "%Y-%m-%d", "month": "%Y-%m"}` + week lambda。`:716-785` 6 个 replay 控制方法共享 `return {"success": True, "status": "X", **extra}`，改为 `_status(status, **extra)` helper。净减约 23 行。

#### 阶段 3h：跨模块 _register_subscribers 表驱动

- **变更 C11：引入 `_SUBSCRIPTIONS` 类属性表 + `_BaseModule.register_subscribers` 基类方法**。7 模块（runtime_mode/execution/formula/trade/tick_bar/screening/monitoring）的 `_register_subscribers` 共享 `self._bus.subscribe(EventType, self._on_X)` 链，改为类级 `_SUBSCRIPTIONS = [(EventType, "_on_X"), ...]` 表 + 基类 `register_subscribers` 单一循环。同时合并 `_on_pool_loaded`（3 模块共享 `pool_config → extract → rebuild state` 骨架，改为 `_extract_from_pool_config(event, key, field_extractor)` helper）与 `_on_mode_changed`（2 模块共享 `prev → new → log` 骨架）。净减约 70 行。

#### 阶段 3i：get_table 防御性调用统一

- **变更 C12：消除 `get_global_config_store().get_table("X") if get_global_config_store() else {}` 14 处双调用**。改为模块级 `_get_table(name) -> dict` helper（已存在于 `monitoring_module.py:51-57`），从 `execution_module.py`（11 处）/ `trade_module.py`（2 处）/ `monitoring_module.py` 内部调用统一引用。同时消除 `get_global_config_store()` 双调用 perf 问题。净减约 5 行 + 修复 perf smell。

### 阶段 4：metatest v4 重建（严格正反合 + 16 维量化评分 + 三原语收敛度）

- **BREAKING**：重新创建 `metatest/` 为 v4，新增「OOP 同源继承验证」「轮询零容忍验证」「三原语收敛度验证」「合并非拆分硬约束」四大测试类别
- **正测试**（`test_positive_*.py`）：覆盖 17 个后端模块 + 前端全部模块 + 本次 25 组 DZH/TDX OOP 合并 + 12 处轮询消除 + 30+ 组 core 第二轮合并的回归断言
- **反测试**（`test_negative_*.py`）：4 类异常（无效配置/运行时异常/API前端/底层逻辑违规）+ 本次新增：(a) DZH/TDX 同构复活检测（Grep `_parse_func_element` / `_add_func` / `_DZH_TO_TDX_TYPE` 等旧同构代码零匹配）；(b) 轮询零容忍检测（Grep `time.sleep` / `asyncio.sleep.*\n.*while` / `setInterval.*fetch` / `start_polling` 在非测试代码零匹配）；(c) OOP 继承结构验证（`BasePoolConverter` 存在 + `DzhPoolConverter` / `TdxPoolConverter` 继承 + 公共方法在基类）；(d) **拆分检测**（每处变更净减行数验证，净增计 redo）
- **合测试**（`test_synthesis_*.py`）：DZH↔TDX 端到端往返（双格式互转保真）+ 事件链路无 sleep（grep `_sync_play_loop` / `_sync_sim_loop` / `start_polling` / `_file_watcher_loop` 零匹配）+ 表驱动断言（所有分派表存在且非空）+ 事件驱动流验证（replay/simulation 步进由 EventDriver heapq 调度，无 time.sleep）+ **三原语覆盖率验证**（时间/分派/继承三原语各 ≥ 95%）
- **评分引擎 v4**：16 维加权评分，**完全由真实测试结果驱动**：
  - v3 12 维保留并降权：module_coverage 6% / test_pass_rate 12% / assertion_density 5% / event_chain_integrity 7% / performance_benchmark 5% / frontend_e2e_pass_rate 6% / logic_coverage 5% / isomorphism_elimination 8% / line_convergence 5% / rule_compliance 3% / negative_test_coverage 2% / synthesis_e2e 2%
  - 新增 4 维：**`oop_inheritance_depth` 8%**（DZH/TDX 同源继承深度 + 公共方法在基类比例 + 子类仅含差异）+ **`polling_zero_tolerance` 8%**（12 处轮询模式 Grep 零匹配 + EventDriver heapq 调度验证 + 前端 setInterval fetch 零匹配）+ **`primitive_convergence` 8%**（三原语覆盖率：时间原语 + 分派原语 + 继承原语，各 ≥ 95% 满分）+ **`essence_ratio` 4%**（净减行数 / 变更前行数，目标 ≥ 12%，净增 = 0 且 redo）
  - 同构检查从 15 项扩展到 40 项（v3 15 + 阶段 1 25 + 阶段 3 30+ = 70 项，取核心 40 项）
  - **行数收敛度目标**：核心模块总行数 ≤ 22,500（v3 基线 23,900 - 1,400 目标），线性衰减
  - **「禁止拆分」硬约束**：essence_ratio 维度对净增行数的变更直接判 0 并触发 redo，无信用分
  - PASS 门槛：总分 ≥ 95 且 16 维均 ≥ 80

### 阶段 5：RULES.md 第 101-110 条 + 文档同步

- 在 `RULES.md` 新增第 101-110 条，固化本次 OOP 同源继承 + 事件驱动 + 第二轮同构合并 + 三原语收敛约束
- 更新 `metatest/README.md` 为 v4 评分规则说明（16 维 + 40 项同构检查 + 三原语收敛度 + 合并非拆分硬约束）

## Impact

- Affected specs:
  - `RULES.md`（新增第 101-110 条）
  - `refactor-meta-pattern-unification`（规则 81-83 基础上深化）
  - `deepen-meta-pattern-strict-metatest-v2`（迭代 7-12 基础上深化）
  - `perfect-meta-essence-strict-metatest-v3`（15 组模式基础上深化，本次新增 55+ 组）
  - `meta-core-essence-mapping`（行数收敛目标对齐 22,500）
- Affected code:
  - `converters.py` — 变更 P1/P2/P3/P4（25 组 DZH/TDX 同构合并 + 类型映射统一 + registry 强制 + 公共工具下沉）
  - `converters/_common.py`（新建）— 变更 P4 公共工具模块
  - `core/import_export_module.py` — 变更 P4（删除公式解码器副本）
  - `core/schemas.py` — 变更 P2/C2（类型映射统一 + from_dict mixin）
  - `core/domain.py` — 变更 C1/C3（`_FieldedBase` mixin + 哈希函数统一）
  - `core/runtime_mode_module.py` — 变更 E1/E2/C3/C10（replay/sim 步进 heapq + 哈希统一 + OHLCV/日期/状态表驱动）
  - `core/execution_module.py` — 变更 C8/C12（spec/action/step/gate 表驱动 + get_table helper）
  - `core/table_engine.py` — 变更 E3/C5（删除 start_polling + ConfigStoreBase 基类）
  - `core/monitoring_module.py` — 变更 C6/C7（24 adapter 声明式表 + ranking 表驱动）
  - `core/trade_module.py` — 变更 C9（buy/sell/psatt 表驱动）
  - `core/tick_bar_module.py` — 变更 C3（哈希函数统一）
  - `core/formula_module.py` — 变更 C3（哈希函数统一）
  - `core/web_state.py` — 变更 C4（safe_cast 统一）
  - `core/event_bus.py` — 变更 C11（_BaseModule.register_subscribers 基类）
  - `core/_hashing.py`（新建）— 变更 C3 哈希函数模块
  - `services/providers.py` — 变更 P4（删除公式解码器副本）
  - `services/data.py` — 变更 E3/E5（watchdog + heapq 替代 mtime/sleep 轮询）
  - `native/builtins.py` — 变更 P4（thin wrapper）
  - `native/validators.py` — 变更 P4（删除 _safe_int 副本）
  - `api.py` — 变更 P3/E6（registry 强制 + 删除前端轮询端点）
  - `app.py` — 变更 P3/E4（registry 强制 + SSE asyncio.Queue）
  - `web/js/app.js` — 变更 E6（RuntimeState SSE 订阅）
  - `web/js/ui.js` — 变更 E6（hot reload WS + highlight WS）
  - `web/js/event-panel.js` — 变更 E6（timer queue SSE 订阅）
  - `config/architecture/dzh_type_map.json` — 变更 P2（四向映射单一真相源）
  - `metatest/` — v4 重建（正反合测试 + scoring.py v4 + runner.py v4）
- 净减约 1,280 行核心代码（720 core + 560 DZH/TDX）+ 修复 12 处轮询违规 + 修复 25 组 DZH/TDX OOP 违规 + 修复 30+ 组 core 第二轮同构违规
- 核心模块总行数从 23,900 降至 ≤ 22,500

## ADDED Requirements

### Requirement: 三大统一原语收敛（极致本质运行时根因）

系统 SHALL 强制全代码库无例外地使用三大统一原语（时间原语 / 分派原语 / 继承原语），删除所有绕过原语的局部重写。本次迭代的核心是「删除绕过原语的 67 处局部重写，强制收敛到已存在的原语」，而非「创建新机制」。

#### Scenario: 时间原语统一收敛
- **WHEN** 任意模块需要时间触发（步进 / 周期刷新 / 定时任务 / 文件监视 / 事件推送）
- **THEN** 经 `EventDriver.add_spec(spec, fire_time)` + heapq 调度，或 `asyncio.Queue + await queue.get()` 阻塞等待，或 `watchdog.Observer` 事件推送，或 SSE/WS 订阅推送
- **AND** Grep `while.*sleep|run_in_executor\(.*drain|setInterval.*fetch` 在 core/*.py + services/*.py + app.py + web/js/*.js 非测试代码 = 0
- **AND** 时间原语覆盖率 ≥ 95%

#### Scenario: 分派原语统一收敛
- **WHEN** 任意模块存在 ≥ 3 个共享骨架的同构函数或 if/elif 分支
- **THEN** 收敛为 1 张声明式表 `TABLE: Dict[K, Spec]` + 1 个通用 builder/dispatcher 单循环
- **AND** Grep `def _adapter_\w+|def _execute_buy\b|def _execute_sell\b|def _compile_timing_spec\b|def _compile_filter_spec\b|def _compile_propagate_spec\b` 在 core/*.py = 0
- **AND** 分派原语覆盖率 ≥ 95%

#### Scenario: 继承原语统一收敛
- **WHEN** 任意模块存在跨格式/跨模块同构函数（同骨架异数据）
- **THEN** 基类/mixin 承载算法骨架，子类仅声明数据差异（int_fields / encoding_priority / post_hook / spec 表）
- **AND** Grep `def to_dict\b` 在 domain.py 仅匹配 `_FieldedBase._common_to_dict` + 薄包装
- **AND** 继承深度 ≥ 2（Base → Sub）
- **AND** 继承原语覆盖率 ≥ 95%

#### Scenario: 合并非拆分硬约束
- **WHEN** 评审工程师检查每处变更
- **THEN** 每处变更必须净减行数（净增行数 > 0 计 redo）
- **AND** 新建文件仅允许 `converters/_common.py` / `core/_hashing.py`（公共下沉，非拆分）
- **AND** 抽象基类/mixin 必须伴随 ≥ 2 处子类收敛（否则计「无效抽象」redo）
- **AND** essence_ratio = 净减行数 / 变更前行数 ≥ 12%

### Requirement: 运行时三核 Dispatcher 元统一（数据驱动分派 DDD 根因）

系统 SHALL 强制全代码库只使用三个已存在的 Dispatcher（`EventBus` / `EventDriver` / `ConfigStore`）承载所有业务逻辑，禁止任何模块重新实现这三核的等价物。每行业务代码要么是 Data 声明，要么是三核 Dispatcher 调用，既非 Data 又非 Dispatcher 的「过程式重写」即违规。

#### Scenario: 运行时三核唯一性
- **WHEN** 任意模块需要跨模块通信 / 时间触发 / 配置读取
- **THEN** 分别经 `EventBus.publish/subscribe` / `EventDriver.schedule` / `ConfigStore.get_table` 三核 Dispatcher
- **AND** Grep `while\s+True|while\s+self\._\w+\s*[:)]` 在 core/*.py + services/*.py 非测试代码 = 0（无自造事件循环）
- **AND** Grep `time\.sleep|asyncio\.sleep\(\d` 在 core/*.py + services/*.py + app.py 非测试代码 = 0（无自造时间调度，EventDriver 唯一）
- **AND** Grep `def _safe_int\b|def _safe_float\b|def _hash_\w+\b|def _adapter_\w+\b|def _execute_buy\b|def _execute_sell\b|def _compile_\w+_spec\b` 在 core/*.py + services/*.py = 0（无自造分派同构副本，表/基类唯一）
- **AND** Grep `get_global_config_store\(\)\.get_table.*\n.*if get_global_config_store` 在 core/*.py = 0（无绕过 ConfigStore 的配置读取）

#### Scenario: 元纯度量化
- **WHEN** 评审工程师计算 `primitive_convergence` 维度
- **THEN** meta_purity = (Data 声明行数 + Dispatcher 调用行数) / 总业务行数 × 100 ≥ 90%
- **AND** 三原语覆盖率 ≥ 95% 当且仅当 meta_purity ≥ 90%（根因解释层）
- **AND** `wc -l core/*.py` ≤ 22,500（Data + Dispatcher 净减后行数）

#### Scenario: 禁止再造第四核 Dispatcher
- **WHEN** 评审工程师审查新增代码
- **THEN** 禁止引入新的「跨模块通信 / 时间触发 / 配置读取」Dispatcher 等价物
- **AND** 任何新模块 = 声明 Data（`_SUBSCRIPTIONS` / spec 表 / 子类差异字段）+ 调用三核 Dispatcher + 必要胶水
- **AND** 自造事件循环 / 自造调度器 / 自造配置加载计 redo

### Requirement: metatest v4 16 维量化评分 + 三原语收敛度

系统 SHALL 重建 `metatest/` 为 v4，评分从 v3 的 12 维扩展到 16 维，新增 `oop_inheritance_depth` / `polling_zero_tolerance` / `primitive_convergence` / `essence_ratio` 四维，评分完全由真实测试结果量化驱动，且强制「合并非拆分」硬约束。

#### Scenario: 16 维评分计算
- **WHEN** ScoringEngine.calculate(test_results) 调用
- **THEN** 计算 16 维：v3 12 维（降权）+ oop_inheritance_depth 8% + polling_zero_tolerance 8% + primitive_convergence 8% + essence_ratio 4%（权重和 = 100%）
- **AND** primitive_convergence = (时间原语覆盖率 + 分派原语覆盖率 + 继承原语覆盖率) / 3 × 100
- **AND** essence_ratio = 净减行数 / 变更前行数 × 100，净增 = 0 且触发 redo

#### Scenario: 三原语覆盖率验证
- **WHEN** 运行 `python -m pytest metatest/test_synthesis_primitive_convergence.py`
- **THEN** 时间原语覆盖率 = (经 EventDriver/Queue/watchdog 的时间触发数) / (总时间触发数) ≥ 95%
- **AND** 分派原语覆盖率 = (表驱动分派数) / (表驱动 + if/elif + 同构函数) ≥ 95%
- **AND** 继承原语覆盖率 = (基类公共方法数) / (基类 + 子类同构方法总数) ≥ 95%

#### Scenario: 合并非拆分反向约束
- **WHEN** 运行 `python -m pytest metatest/test_negative_split_detection.py`
- **THEN** 每处变更净减行数 > 0（净增计 redo）
- **AND** 新建文件仅允许 `converters/_common.py` / `core/_hashing.py`
- **AND** 抽象基类/mixin 伴随 ≥ 2 处子类收敛

#### Scenario: 同构检查扩展到 40 项
- **WHEN** 计算 isomorphism_elimination 维度
- **THEN** 检查 40 项（v3 15 + 阶段 1 25 + 阶段 3 核心取 25），每项违规扣 100/40 分

#### Scenario: 行数收敛度目标
- **WHEN** 计算 line_convergence 维度
- **THEN** 核心模块总行数 ≤ 22,500 满分，线性衰减

#### Scenario: 轮询零容忍反测试
- **WHEN** 运行 `python -m pytest metatest/test_negative_polling.py`
- **THEN** Grep `time\.sleep` / `asyncio\.sleep.*\n.*while` / `setInterval.*fetch` / `start_polling` / `_file_watcher_loop` / `_sync_play_loop` / `_sync_sim_loop` / `auto_step_loop` 在非测试代码零匹配
- **AND** 环境缺失计失败，不给信用分

#### Scenario: OOP 继承结构验证
- **WHEN** 运行 `python -m pytest metatest/test_positive_oop_inheritance.py`
- **THEN** 验证 `BasePoolConverter` 存在 + `DzhPoolConverter` / `TdxPoolConverter` 继承 + 公共方法（`_parse_element` / `_add_element` / `_decode_pos` / `_decode_xml_bytes`）在基类 + 子类仅含差异方法

### Requirement: DZH/TDX OOP 同源继承基类（变更 P1）

系统 SHALL 引入 `BasePoolConverter` 抽象基类作为 DZH/TDX 转换器的共同真相源，`DzhPoolConverter` / `TdxPoolConverter` 仅作为子类实现差异，所有基础功能（元素解析/序列化/cell 转换/stk IO/pos 解码/XML 多编码解码）使用相同代码。

#### Scenario: 3 个 TDX 元素解析器合并
- **WHEN** TDX XML 含 `<func>` / `<psatt>` / `<spinfo>` 元素
- **THEN** 通过基类 `_parse_element(elem, schema_key, int_fields, post_hook=None)` 统一解析，子类仅提供 `int_fields` 与 `post_hook`
- **AND** Grep `def _parse_func_element|def _parse_psatt_element|def _parse_spinfo_element` 在 converters.py = 0

#### Scenario: 3 个 TDX 元素序列化器合并
- **WHEN** 序列化 cell 为 TDX XML
- **THEN** 通过基类 `_add_element(cell_elem, attr_name, model_class, element_name)` 统一序列化，子类仅提供 `(attr_name, model_class, element_name)` 三元组
- **AND** Grep `def _add_func\b|def _add_psatt\b|def _add_spinfo\b` 在 converters.py = 0

#### Scenario: DZH/TDX cell 转换器共用 _CellEnvelope 基类
- **WHEN** 解析 DZH 或 TDX cell 元素
- **THEN** DZH `_parse_cell_pool/market/condition` 与 TDX `_convert_candidate/state_pool/condition_cell` 共用 `_CellEnvelope` 基类的 `return {id, type, label, params, position, ...}` 封装骨架
- **AND** 子类仅提供 `params` dict 构建逻辑与 type label

#### Scenario: pos 解码与 XML 多编码解码共用基类方法
- **WHEN** DZH `_parse_pos` 或 TDX `_parse_tdx_pos` 调用
- **THEN** 共用 `BasePoolConverter._decode_pos(pos_str, *, as_dict=True)` 方法
- **AND** DZH `_decode_xml_content` 与 TDX `_decode_tdx_xml` 共用 `BasePoolConverter._decode_xml_bytes(raw, encoding_priority, post_process_fn=None)` 方法

#### Scenario: TDX flow 11 字段 getattr 表驱动
- **WHEN** TDX flow 导入或导出
- **THEN** 11 个 `getattr(flow, 'tdx_X', getattr(flow, 'X', default))` 调用改为查 `xml_mapping.json:flow.attributes` 表迭代
- **AND** 禁止 11 处 getattr 样板代码

### Requirement: DZH↔TDX 类型映射单一真相源（变更 P2）

系统 SHALL 将 5 处并行的 DZH↔TDX 类型映射表收敛为单一真相源 `config/architecture/dzh_type_map.json`，含 `dzh_to_tdx` / `tdx_to_dzh` / `tdx_to_frontend` / `frontend_to_tdx` 四向映射，所有读取通过 `ConfigStore.get_table("dzh_type_map")`。

#### Scenario: 类型映射单一来源
- **WHEN** 任意模块需要 DZH↔TDX 类型映射
- **THEN** 调用 `get_global_config_store().get_table("dzh_type_map")` 获取
- **AND** Grep `_DZH_TO_TDX_TYPE\b|_DZH_TO_TDX_TYPE_EXPORT\b|TDX_TO_DZH_CELL_TYPE\b|TDX_CELL_TYPE_MAP\b` 在 *.py = 0（仅作为别名指向 ConfigStore 时除外）

#### Scenario: 类型映射矛盾消除
- **WHEN** DZH type 3 查询 TDX 映射
- **THEN** 全代码库返回唯一值（消除 `converters.py:4450` 返回 3 vs `:4882` 返回 0 的矛盾）
- **AND** 单元测试断言 `dzh_to_tdx_map[3] == tdx_to_dzh_map.inverse[3]` 一致

### Requirement: _CONVERTER_REGISTRY 完整 OOP 路由（变更 P3）

系统 SHALL 强制所有 DZH/TDX 转换调用通过 `_CONVERTER_REGISTRY` + `_call_converter` 统一入口，禁止绕过 registry 直接调用 `parse_dzh_xml` / `parse_tdx_xml` / `_build_tdx_xml` / `export_meta_to_dzh_xml_bytes`。

#### Scenario: API 调用强制走 registry
- **WHEN** `api.py:dzh_import` / `dzh_import_and_save` / `dzh_load_demo` / `dzh_export` 调用
- **THEN** 通过 `_call_converter(path_or_data, fmt, direction, config=None)` 入口，fmt 由 `is_tdx_format` 自动探测
- **AND** Grep `parse_dzh_xml\(|parse_tdx_xml\(|_build_tdx_xml\(|export_meta_to_dzh_xml_bytes\(` 在 api.py / app.py = 0（仅 converters.py 内部允许）

#### Scenario: app.py TDX CRUD 端点强制走 registry
- **WHEN** `app.py:_tdx_load` / `_tdx_create` / `_tdx_save` / `tdx_export_xml` 调用
- **THEN** 通过 `_call_converter` 入口
- **AND** Grep `is_tdx_format` 在 api.py 内 if 分支 = 0（仅作为 fmt 探测函数调用）

#### Scenario: dzh_import 与 dzh_import_and_save 内容加载合并
- **WHEN** 两端点接收上传文件
- **THEN** 共用 `_load_xml_content_from_request(request)` 辅助函数处理 form/file/json body/multi-encoding 解码
- **AND** 6 行多编码解码循环仅在 `_load_xml_content_from_request` 出现一次

### Requirement: 公共工具函数下沉 converters/_common.py（变更 P4）

系统 SHALL 将跨模块重复的工具函数下沉到 `converters/_common.py`，所有模块统一引用。

#### Scenario: safe_int / safe_float 单一定义
- **WHEN** 任意模块需要 safe cast
- **THEN** 从 `converters/_common import safe_int, safe_float`，禁止模块级重新定义
- **AND** Grep `def _safe_int\b|def _safe_float\b|def _to_float\b|def _cast_int\b|def _cast_str\b` 在 core/*.py + native/*.py + services/*.py = 0（仅 converters/_common.py 内允许）

#### Scenario: 公式解码器单一副本
- **WHEN** 任意模块需要 `_decode_formula` / `_extract_formula_from_binary` / `_is_valid_formula` / `_extract_text_segments`
- **THEN** 从 `converters/_common import decode_formula, extract_formula_from_binary, is_valid_formula, extract_text_segments`
- **AND** Grep `def _decode_formula\b|def _extract_formula_from_binary\b|def _is_valid_formula\b|def _extract_text_segments\b` 在 services/providers.py = 0
- **AND** `native/builtins.py:_decode_formula_base64` 改为 thin wrapper 调用 `decode_formula`

#### Scenario: _load_dzh_type_map 单一实现
- **WHEN** 加载 dzh_type_map.json
- **THEN** 通过 `ConfigStore.get_table("dzh_type_map")` 统一入口
- **AND** Grep `def _load_dzh_type_map\b` 在 *.py = 0

### Requirement: replay/simulation 步进改为 EventDriver heapq 调度（变更 E1+E2）

系统 SHALL 通过 EventDriver heapq + `loop.call_at` 调度 replay/simulation 步进事件，禁止 `while True + time.sleep` / `asyncio.sleep` 步进循环与 `_run` / `_sim_auto_step` / `_current_mode` 标志轮询。

#### Scenario: replay 步进 heapq 调度
- **WHEN** `KLineReplayEngine.play()` 调用
- **THEN** 调用 `EventDriver.schedule(step_event, fire_time=now + base_interval/speed)` 入 heapq，由 `loop.call_at` 触发 `_do_step()` 并重新调度
- **AND** `pause()` 通过 `EventDriver.cancel(step_event_id)` 取消，`resume()` 重新调度
- **AND** Grep `def _sync_play_loop\b` 在 runtime_mode_module.py = 0
- **AND** Grep `time\.sleep\(interval\)` 在 runtime_mode_module.py replay 路径 = 0

#### Scenario: simulation auto-step heapq 调度
- **WHEN** `RuntimeSimulator.start_auto()` 调用
- **THEN** 调用 `EventDriver.schedule(sim_step_event, fire_time=now + 1.0/speed)` 入 heapq
- **AND** 步进事件到时触发 `step_simulation(step_idx)` 并重新调度
- **AND** 停止通过 `ModeChanged` 事件订阅或 `EventDriver.cancel` 取消
- **AND** Grep `def _sync_sim_loop\b|async def auto_step_loop\b` 在 runtime_mode_module.py = 0
- **AND** Grep `while self\._run\b|while self\._sim_auto_step\b` 在 runtime_mode_module.py = 0

### Requirement: 文件监视改为 watchdog 事件驱动（变更 E3）

系统 SHALL 通过 `watchdog.Observer` 发布 `FileModified` 事件触发配置/数据重载，禁止 `while _running + asyncio.sleep + 比较 mtime` 轮询。

#### Scenario: ConfigStore 文件监视
- **WHEN** 配置文件变化
- **THEN** `watchdog.Observer` 触发 `on_modified` → `ConfigChanged` EventBus 事件 → ConfigStore 重载
- **AND** Grep `def start_polling\b` 在 table_engine.py = 0
- **AND** Grep `asyncio\.sleep\(interval\)` 在 table_engine.py 热加载路径 = 0

#### Scenario: services/data.py 文件监视
- **WHEN** 数据文件（板块/自选股）变化
- **THEN** `watchdog.Observer` 触发 `FileModified` → DataManager 订阅事件重载
- **AND** Grep `def _file_watcher_loop\b` 在 services/data.py = 0
- **AND** Grep `asyncio\.sleep\(3\)` 在 services/data.py 文件监视路径 = 0

### Requirement: SSE 流改为 asyncio.Queue 阻塞等待（变更 E4）

系统 SHALL 通过 `asyncio.Queue + await queue.get()` 阻塞等待事件推送，禁止 `run_in_executor(drain) + asyncio.sleep(0.05)` 50ms 队列轮询。

#### Scenario: SSE 流事件推送
- **WHEN** 客户端连接 `/api/events/stream`
- **THEN** 创建 `asyncio.Queue(maxsize=10000)`，EventBus 订阅回调 `queue.put_nowait(event_data)`
- **AND** 流循环 `asyncio.wait_for(queue.get(), timeout=15.0)` 阻塞等待，超时发心跳
- **AND** Grep `run_in_executor\(.*drain` 在 app.py = 0
- **AND** Grep `asyncio\.sleep\(0\.05\)` 在 app.py SSE 路径 = 0

### Requirement: services/data.py 周期刷新改为 heapq 调度（变更 E5）

系统 SHALL 通过 EventDriver heapq 调度周期刷新与每日定时任务，禁止 `while _running + asyncio.sleep` 与 `asyncio.sleep(wait_seconds)` 调度。

#### Scenario: 周期刷新 heapq 调度
- **WHEN** `refresh_favorites` / `refresh_custom_block` 启动
- **THEN** 入 heapq `TimedEventSpec(fire_time=now+interval, action=refresh_fn)`，触发后重新调度
- **AND** Grep `def _refresh_with_backoff\b` 在 services/data.py = 0

#### Scenario: 每日定时 heapq 调度
- **WHEN** 每日定时重载任务启动
- **THEN** 入 heapq `TimedEventSpec(fire_time=next_target, action=daily_reload)`，触发后重新计算次日 fire_time
- **AND** Grep `asyncio\.sleep\(wait_seconds\)` 在 services/data.py 每日调度路径 = 0

### Requirement: 前端 4 处 setInterval 改为 SSE/WS 订阅（变更 E6）

系统 SHALL 通过 SSE/WS 订阅推送更新前端状态，禁止 `setInterval + fetch` 轮询后端 API。

#### Scenario: RuntimeState SSE 订阅
- **WHEN** 前端 `RuntimeState` 初始化
- **THEN** 订阅 `EventSource('/api/events/stream')` 的 `ModeChanged` / `SnapshotUpdated` 事件更新 `mode` / `displayNowMs` / `activeSessionId`
- **AND** Grep `setInterval.*_poll` 在 web/js/app.js = 0
- **AND** Grep `fetch\('/api/state/runtime'\)` 在 web/js/app.js = 0

#### Scenario: 前端热加载 WS 订阅
- **WHEN** 前端 `_startHotReload` 调用
- **THEN** 订阅 `/api/config/ws` WebSocket 的 `ConfigChanged` 推送
- **AND** Grep `setInterval.*\/reload` 在 web/js/ui.js = 0

#### Scenario: 前端高亮 WS 订阅
- **WHEN** 前端 `HighlightManager` 初始化
- **THEN** 仅依赖 `/ws/highlight` WebSocket 推送，移除降级 setInterval 轮询
- **AND** Grep `setInterval.*\/api\/highlight-events` 在 web/js/ui.js = 0
- **AND** 后端 `/api/highlight-events` GET 端点删除

#### Scenario: 前端计时器队列 SSE 订阅
- **WHEN** 前端事件面板初始化
- **THEN** 从 SSE 流的 `TimerQueued` / `TimerFired` 事件更新 `timerQueue`
- **AND** Grep `setInterval.*syncTimerQueue` 在 web/js/event-panel.js = 0
- **AND** Grep `fetch\('\/api\/events\/timer-queue` 在 web/js/event-panel.js = 0

### Requirement: _FieldedBase mixin 合并 to_dict/from_dict（变更 C1）

系统 SHALL 引入 `_FieldedBase` mixin 合并 `_NodeBase` / `_EdgeBase` 的 `to_dict` / `from_dict` 逐行复制，仅返回类型注解不同的 4 方法合并为 mixin 实现 + 4 个 1 行薄包装。

#### Scenario: to_dict 单一实现
- **WHEN** `_NodeBase.to_dict` 或 `_EdgeBase.to_dict` 调用
- **THEN** 委托 `_FieldedBase._common_to_dict()`，方法体 ≤ 2 行
- **AND** Grep `def to_dict\b` 在 domain.py 仅匹配 `_FieldedBase._common_to_dict` + 2 个 1 行薄包装

#### Scenario: from_dict 单一实现
- **WHEN** `_NodeBase.from_dict` 或 `_EdgeBase.from_dict` 调用
- **THEN** 委托 `_FieldedBase._common_from_dict()`，方法体 ≤ 2 行

#### Scenario: from_X_factory 合并
- **WHEN** `_NodeBase.from_dzh_type` / `from_tdx_type` / `_EdgeBase.from_dzh_attr` / `from_tdx_source_type` 调用
- **THEN** 委托 `_lookup_in_registry(registry, t, label)`，方法体 ≤ 2 行

### Requirement: 哈希函数三族统一到 _hashing.py（变更 C3）

系统 SHALL 引入 `core/_hashing.py` 模块统一 per-content MD5 / aggregate tick hash / bar_hash accessor 三族哈希函数。

#### Scenario: per-content MD5 单一实现
- **WHEN** 任意模块需要 hash 一个 dict 内容
- **THEN** 调用 `from core._hashing import hash_dict_content; hash_dict_content(content, exclude=())`
- **AND** Grep `def _hash_tick\b|def _hash_bar\b|def _hash_bars\b|def _hash_object\b|def _hash_code_bars\b` 在 core/*.py = 0（仅 _hashing.py 内 `hash_dict_content` 允许）

#### Scenario: aggregate tick hash 单一实现
- **WHEN** `PoolStateMixin._hash_tick_data` 或 `_InternalState._hash_tick_data` 调用
- **THEN** 委托 `hash_tick_aggregate(tick_data, per_code_hasher=hash_dict_content)`，方法体 ≤ 3 行
- **AND** Grep `def _hash_tick_data\b` 在 core/*.py = 0（仅 _hashing.py 内 `hash_tick_aggregate` 允许）

#### Scenario: bar_hash accessor mixin
- **WHEN** 任意模块读取 `bar_hash`
- **THEN** 通过 `BarHashMixin.bar_hash` property 返回 `self.X.get("_hash", "")`
- **AND** Grep `def bar_hash\b` 在 core/*.py ≤ 1（仅 mixin 定义）

### Requirement: ConfigStoreBase 合并热加载三件套（变更 C5）

系统 SHALL 引入 `ConfigStoreBase` 基类合并 `ConfigStore.check_changes` 与 `ConfigStoreHotReloadManager.check_and_reload` 双实现，以及 `rollback` 双实现与 3 层校验调用骨架。

#### Scenario: check_and_reload 模板方法
- **WHEN** ConfigStore 或 HotReloadManager 检测配置变化
- **THEN** 调用 `ConfigStoreBase.check_and_reload()` 模板方法（10 步骨架），子类仅 override `_validate(name, data)` 与 `_commit(name, data)` hook
- **AND** Grep `def check_changes\b|def check_and_reload\b` 在 table_engine.py ≤ 1（仅基类）

#### Scenario: rollback 单一实现
- **WHEN** ConfigStore 或 HotReloadManager 回滚配置
- **THEN** 调用 `ConfigStoreBase.rollback(version_id)` 模板方法，子类仅 override `_reload_all()` hook
- **AND** Grep `def rollback\b` 在 table_engine.py ≤ 1（仅基类）

### Requirement: _ADAPTER_SPECS 声明式表驱动 24 adapter（变更 C6）

系统 SHALL 将 monitoring_module 24 个 `_adapter_X` 函数改为 `_ADAPTER_SPECS` 声明式表 + 通用 builder，`EVENT_RECORD_ADAPTERS` 的 value 从函数引用改为 spec key 字符串。

#### Scenario: adapter 声明式表
- **WHEN** `EVENT_RECORD_ADAPTERS["TickReceived"]` 查询
- **THEN** 返回 spec key `"TickReceived"`，由 `_build_adapter_record(spec, event)` 通用 builder 处理
- **AND** Grep `def _adapter_\w+\b` 在 monitoring_module.py = 0（仅 `_build_adapter_record` 允许）

#### Scenario: spec 表覆盖 24 事件类型
- **WHEN** 检查 `_ADAPTER_SPECS`
- **THEN** 含 24 个 key（TickReceived / DataChanged / BarComposed / FormulaEvaluated / StockFiltered / TimeAdvanced / SnapshotUpdated / Executed / DomainEvent / PoolLoaded / ConfigLoaded / ConfigChanged / TransferExecuted / TTLExpired / OrderPlaced / OrderFilled / AlertRaised / PositionUpdated / StatisticsUpdated / RankingChanged / EdgeFired / Signal / CrossoverDetected / ModeChanged）
- **AND** 每个 spec 含 `top_fields` 与 `details_fields` 列表

### Requirement: trade_module buy/sell/psatt 表驱动（变更 C9）

系统 SHALL 将 `_execute_buy` / `_execute_sell` 与 `_apply_psatt_side_effects` 改为表驱动单循环。

#### Scenario: _execute_trade 表驱动
- **WHEN** `_execute_buy` 或 `_execute_sell` 调用
- **THEN** 委托 `_execute_trade(side, signal, ...)` + `_SIDE_SPECS = {"BUY": {...}, "SELL": {...}}` 表
- **AND** Grep `def _execute_buy\b|def _execute_sell\b` 在 trade_module.py = 0

#### Scenario: psatt side effects 表迭代
- **WHEN** `_apply_psatt_side_effects` 调用
- **THEN** 迭代 `_PSATT_SIDE_EFFECTS = [(flag_attr, event_kind, payload_builder), ...]` 5 条
- **AND** Grep `if action_spec\.bsavehis|if action_spec\.bsound|if action_spec\.btip|if action_spec\.bsavetoblock|if action_spec\.baimpool` 在 trade_module.py = 0

### Requirement: _SUBSCRIPTIONS 类属性表驱动（变更 C11）

系统 SHALL 通过 `_SUBSCRIPTIONS = [(EventType, "_on_X"), ...]` 类属性表 + `_BaseModule.register_subscribers` 基类方法统一 7 模块的事件订阅。

#### Scenario: 7 模块 _SUBSCRIPTIONS 表
- **WHEN** 检查 runtime_mode/execution/formula/trade/tick_bar/screening/monitoring 7 模块
- **THEN** 每模块定义 `_SUBSCRIPTIONS: ClassVar[List[Tuple[Type[Event], str]]]` 类属性
- **AND** 基类 `_BaseModule.register_subscribers` 单一循环 `for evt, handler_name in self._SUBSCRIPTIONS: self._bus.subscribe(evt, getattr(self, handler_name))`
- **AND** Grep `def _register_subscribers\b` 在 7 模块 = 0（仅 _BaseModule 内允许）

#### Scenario: _on_pool_loaded 合并
- **WHEN** tick_bar/formula/screening 3 模块收到 `PoolLoaded` 事件
- **THEN** 共用 `_extract_from_pool_config(event, key, field_extractor)` helper
- **AND** 3 模块 `_on_pool_loaded` 方法体 ≤ 5 行

### Requirement: metatest v4 14 维量化评分

系统 SHALL 重建 `metatest/` 为 v4，评分从 v3 的 12 维扩展到 14 维，新增 `oop_inheritance_depth` 与 `polling_zero_tolerance` 两维，评分完全由真实测试结果量化驱动。

#### Scenario: 14 维评分计算
- **WHEN** ScoringEngine.calculate(test_results) 调用
- **THEN** 计算 14 维：v3 12 维（降权）+ oop_inheritance_depth 10% + polling_zero_tolerance 10%（权重和 = 100%）
- **AND** oop_inheritance_depth = (BasePoolConverter 存在 + DzhPoolConverter/TdxPoolConverter 继承 + 公共方法在基类比例 + 子类仅含差异) × 100
- **AND** polling_zero_tolerance = (12 处轮询模式 Grep 零匹配 + EventDriver heapq 调度验证 + 前端 setInterval fetch 零匹配) × 100

#### Scenario: 同构检查扩展到 40 项
- **WHEN** 计算 isomorphism_elimination 维度
- **THEN** 检查 40 项（v3 15 + 阶段 1 25 + 阶段 3 核心取 25），每项违规扣 100/40 分

#### Scenario: 行数收敛度目标
- **WHEN** 计算 line_convergence 维度
- **THEN** 核心模块总行数 ≤ 22,500 满分，线性衰减

#### Scenario: 轮询零容忍反测试
- **WHEN** 运行 `python -m pytest metatest/test_negative_polling.py`
- **THEN** Grep `time\.sleep` / `asyncio\.sleep.*\n.*while` / `setInterval.*fetch` / `start_polling` / `_file_watcher_loop` / `_sync_play_loop` / `_sync_sim_loop` / `auto_step_loop` 在非测试代码零匹配
- **AND** 环境缺失计失败，不给信用分

#### Scenario: OOP 继承结构验证
- **WHEN** 运行 `python -m pytest metatest/test_positive_oop_inheritance.py`
- **THEN** 验证 `BasePoolConverter` 存在 + `DzhPoolConverter` / `TdxPoolConverter` 继承 + 公共方法（`_parse_element` / `_add_element` / `_decode_pos` / `_decode_xml_bytes`）在基类 + 子类仅含差异方法

## MODIFIED Requirements

### Requirement: metatest 评分引擎版本

metatest 评分引擎从 v3 的 12 维升级为 v4 的 14 维。新增 2 维：`oop_inheritance_depth`（OOP 同源继承深度）与 `polling_zero_tolerance`（轮询零容忍）。原 12 维权重重新分配以容纳新增维度。同构检查项从 15 项扩展到 40 项，行数收敛度目标从 ≤ 23,000 调整为 ≤ 22,500。

### Requirement: RULES.md 元模式规则集

RULES.md 在第 100 条基础上新增第 101-110 条，固化本次 OOP 同源继承 + 事件驱动 + 第二轮同构合并约束：第 101 条 DZH/TDX OOP 同源继承、第 102 条 DZH↔TDX 类型映射单一真相源、第 103 条 _CONVERTER_REGISTRY 完整 OOP 路由、第 104 条 公共工具函数下沉 converters/_common.py、第 105 条 replay/simulation heapq 调度禁轮询、第 106 条 文件监视 watchdog 禁 mtime 轮询、第 107 条 SSE 流 asyncio.Queue 禁 50ms 轮询、第 108 条 前端 setInterval 禁轮询改 SSE/WS、第 109 条 _FieldedBase/_ADAPTER_SPECS/_SUBSCRIPTIONS 表驱动、第 110 条 哈希函数三族统一到 _hashing.py。

## REMOVED Requirements

### Requirement: 25 组 DZH/TDX 同构函数
**Reason**: 同骨架异数据的 25 组函数（_parse_func/psatt/spinfo_element / _add_func/psatt/spinfo / _parse_cell_pool/market/condition / _convert_candidate/state_pool/condition_cell / _build_cell_default/pool/market / _parse_stk_children/_parse_stk_elements / _export_field_stocks/_add_stks / _parse_pos/_parse_tdx_pos / _decode_xml_content/_decode_tdx_xml / _make_tdx_cell 3 分支 / _export_field_* 6 函数 / _DZH_TO_TDX_TYPE 5 表）违反 OOP，已合并到 `BasePoolConverter` 基类 + 子类差异
**Migration**: `DzhPoolConverter` / `TdxPoolConverter` 子类仅实现差异方法，公共方法在基类

### Requirement: services/providers.py 公式解码器副本
**Reason**: `_extract_text_segments` / `_is_valid_formula` / `_extract_formula_from_binary` / `decode_formula` 与 `core/import_export_module.py:64-180` 逐字节复制，约 120 行重复
**Migration**: 删除 services/providers.py 副本，统一从 `converters/_common.py` 引用

### Requirement: 12 处轮询循环
**Reason**: 违反「彻底事件驱动，禁止轮询」与 RULES.md 第 30/39/41/74 条，包括 `_sync_play_loop` / `_sync_sim_loop` / `auto_step_loop` / `start_polling` / `_file_watcher_loop` / `_refresh_with_backoff` / `_daily_loop` / SSE 50ms drain / 前端 4 处 setInterval
**Migration**: 替换为 EventDriver heapq + `loop.call_at` / `asyncio.Queue + await queue.get()` / `watchdog.Observer` / SSE/WS 订阅

### Requirement: _NodeBase/_EdgeBase to_dict/from_dict 双实现
**Reason**: 4 方法 ~52 行仅返回类型注解不同，逐行复制
**Migration**: 合并到 `_FieldedBase` mixin + 4 个 1 行薄包装

### Requirement: 跨模块哈希函数双实现
**Reason**: per-content MD5 6 处 + aggregate tick hash 3 处 + bar_hash accessor 3 处共享骨架，跨 domain/tick_bar/formula/runtime_mode/execution 5 模块重复
**Migration**: 合并到 `core/_hashing.py` 模块 `hash_dict_content` / `hash_tick_aggregate` + `BarHashMixin`

### Requirement: 24 个 _adapter_X 函数
**Reason**: 共享 `extract fields → build dict` 骨架，仅字段提取与 details 形状不同
**Migration**: 改为 `_ADAPTER_SPECS` 声明式表 + `_build_adapter_record(spec, event)` 通用 builder

### Requirement: ConfigStore 与 HotReloadManager 双实现
**Reason**: check_changes / check_and_reload / rollback / 3 层校验共 ~140 行近乎全拷贝
**Migration**: 合并到 `ConfigStoreBase` 模板方法 + 2 个 thin override hook

### Requirement: 7 模块 _register_subscribers 过程式链
**Reason**: 共享 `self._bus.subscribe(EventType, self._on_X)` 链，违反 RULES.md 第 16 条「分派用 dict」
**Migration**: 改为类级 `_SUBSCRIPTIONS` 表 + 基类 `register_subscribers` 单一循环

### Requirement: trade_module _execute_buy/_execute_sell 双实现与 psatt 5 if 分支
**Reason**: 8 步骨架共享 + 5 标志 if 分支违反表驱动原则
**Migration**: 合并为 `_execute_trade(side, ...)` + `_SIDE_SPECS` 表 + `_PSATT_SIDE_EFFECTS` 表迭代
