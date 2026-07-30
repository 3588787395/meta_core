# Checklist

## 架构工程师检查点（实施前自检）

- [ ] 已阅读 spec.md「深层运行逻辑洞察：三大统一原语」章节并理解：极致本质运行时 = 全代码库无例外使用时间原语(EventDriver heapq) + 分派原语(TABLE+dispatch) + 继承原语(Base+Sub)
- [ ] 已阅读 spec.md「第四层洞察：三原语的元统一 — 数据驱动分派（DDD）」章节并理解：三原语同构于运行时三核 Dispatcher（EventBus / EventDriver / ConfigStore，均已存在），公理 `Code = Data + Dispatcher`，每行业务代码要么是 Data 声明要么是三核 Dispatcher 调用，禁止再造第四核
- [ ] 已理解本次迭代核心是「删除绕过三核 Dispatcher 的 67 处局部重写（重写 Data 副本），强制收敛到已存在的原语」，而非「创建新机制」
- [ ] 已理解「合并非拆分」硬约束：每处变更必须净减行数，essence_ratio ≥ 12%，净增 = 0 且 redo，meta_purity ≥ 90%
- [ ] 已阅读 RULES.md 第 16/24/30/39/41/59/69/74/87/91-100 条并理解 OOP 同源继承 + 事件驱动 + 表驱动 + ConfigStore 统一约束
- [ ] 已阅读 `core/execution_module.py:169 EventDriver` + `core/domain.py:1541 TimedEventSpec` 确认时间原语已存在（add_spec/fire_due/heapq 完整链路），12 处轮询违规是绕过此原语
- [ ] 已阅读 `converters.py:4019-4099` 确认 3 个 TDX 元素解析器同构（`_parse_func/psatt/spinfo_element` 仅元素名与 schema key 不同）
- [ ] 已阅读 `converters.py:4973-5012` 确认 3 个 TDX 元素序列化器同构（`_add_func/psatt/spinfo` 仅 attr 名与 ModelClass 不同）
- [ ] 已阅读 `converters.py:4450` 与 `:4882` 确认 `_DZH_TO_TDX_TYPE` 与 `_DZH_TO_TDX_TYPE_EXPORT` 在 DZH type 3 映射上矛盾（一处 → TDX 3、一处 → TDX 0）
- [ ] 已阅读 `services/providers.py:557-673` 与 `core/import_export_module.py:64-180` 确认 4 个公式解码器逐字节复制（约 120 行）
- [ ] 已阅读 `core/runtime_mode_module.py:726-748 _sync_play_loop` 确认 `while True + time.sleep(interval)` replay 步进轮询（违反时间原语）
- [ ] 已阅读 `core/runtime_mode_module.py:2122-2138 _sync_sim_loop` + `:2509-2525 auto_step_loop` 确认 simulation auto-step 轮询（违反时间原语）
- [ ] 已阅读 `core/table_engine.py:1700-1715 start_polling` 与 `services/data.py:5994-6005 _file_watcher_loop` 确认文件 mtime 轮询（`watchdog` 事件驱动实现已并存存在，违反时间原语）
- [ ] 已阅读 `app.py:1067-1200 events_stream` 确认 SSE `while True + run_in_executor(drain) + asyncio.sleep(0.05)` 50ms 队列轮询（违反时间原语）
- [ ] 已阅读 `web/js/app.js:66-87 RuntimeState._poll` 确认前端 `setInterval + fetch('/api/state/runtime')` 1s 轮询（违反时间原语）
- [ ] 已阅读 `core/domain.py:172-200 _NodeBase.to_dict/from_dict` 与 `:502-530 _EdgeBase.to_dict/from_dict` 确认 4 方法逐行复制仅返回类型注解不同（违反继承原语）
- [ ] 已阅读 `core/monitoring_module.py` 24 个 `_adapter_X` 函数确认共享 `extract fields → build dict` 骨架（违反分派原语）
- [ ] 已阅读 `core/table_engine.py:460-534 check_changes` 与 `:1594-1676 check_and_reload` 确认 70 行近乎全拷贝（违反继承原语）
- [ ] 已确认阶段 1 各 Task 顺序：Task 1（converters.py 25 组同构）→ Task 2（dzh_type_map 统一）+ Task 4（_common.py 下沉）→ Task 3（registry 强制）
- [ ] 已确认阶段 2 各 Task 顺序：Task 6（replay）→ Task 7（sim）共改 runtime_mode_module.py；Task 8（watchdog）→ Task 10（heapq 调度）共改 services/data.py
- [ ] 已确认三大原语与 16 维评分的对应：时间原语→polling_zero_tolerance+primitive_convergence，分派原语→primitive_convergence+isomorphism_elimination，继承原语→oop_inheritance_depth+primitive_convergence

## 评审工程师检查点（阶段 1：DZH/TDX OOP 同源继承）

### 变更 P1 — BasePoolConverter 抽象基类
- [x] `BasePoolConverter` 抽象基类在 converters.py 中定义，含 `_parse_element` / `_add_element` / `_decode_pos` / `_decode_xml_bytes` 公共方法
- [x] `DzhPoolConverter(BasePoolConverter)` 与 `TdxPoolConverter(BasePoolConverter)` 子类定义，仅实现差异
- [x] Grep `def _parse_func_element|def _parse_psatt_element|def _parse_spinfo_element` 在 converters.py = 0
- [x] Grep `def _add_func\b|def _add_psatt\b|def _add_spinfo\b` 在 converters.py = 0
- [x] Grep `def _parse_pos\b|def _parse_tdx_pos\b` 在 converters.py = 0
- [x] Grep `def _decode_xml_content\b|def _decode_tdx_xml\b` 在 converters.py = 0
- [x] DZH/TDX 双格式导入导出往返保真

### 变更 P2 — DZH↔TDX 类型映射单一真相源
- [x] `config/architecture/dzh_type_map.json` 含 `dzh_to_tdx` / `tdx_to_dzh` / `tdx_to_frontend` / `frontend_to_tdx` 四向映射
- [x] Grep `_DZH_TO_TDX_TYPE\b|_DZH_TO_TDX_TYPE_EXPORT\b|TDX_TO_DZH_CELL_TYPE\b|TDX_CELL_TYPE_MAP\b` 在 *.py = 0
- [x] DZH type 3 映射全代码库返回唯一值（矛盾消除）
- [x] 单元测试断言 `dzh_to_tdx` 与 `tdx_to_dzh` 互为逆映射

### 变更 P3 — _CONVERTER_REGISTRY 完整 OOP 路由
- [x] Grep `parse_dzh_xml\(|parse_tdx_xml\(|_build_tdx_xml\(|export_meta_to_dzh_xml_bytes\(` 在 api.py / app.py = 0
- [x] Grep `is_tdx_format` 在 api.py 内 if 分支 = 0（仅作为 fmt 探测函数调用）
- [x] `_load_xml_content_from_request` helper 在 api.py 中定义，`dzh_import` 与 `dzh_import_and_save` 共用
- [x] 6 行多编码解码循环仅在 `_load_xml_content_from_request` 出现一次

### 变更 P4 — 公共工具函数下沉 converters/_common.py
- [x] `converters/_common.py` 模块定义 `safe_int` / `safe_float` / `safe_cast` / `decode_formula` / `extract_formula_from_binary` / `is_valid_formula` / `extract_text_segments` / `decode_xml_bytes` / `decode_pos` / `hash_dict_content`
- [x] Grep `def _safe_int\b|def _safe_float\b` 在 core/*.py + native/*.py + services/*.py = 0（仅 converters/_common.py 内允许）
- [x] Grep `def _decode_formula\b|def _extract_formula_from_binary\b|def _is_valid_formula\b|def _extract_text_segments\b` 在 services/providers.py = 0
- [x] `native/builtins.py:_decode_formula_base64` 改为 thin wrapper 调用 `converters._common.decode_formula`
- [x] Grep `def _load_dzh_type_map\b` 在 *.py = 0

## 评审工程师检查点（阶段 2：彻底事件驱动）

### 变更 E1 — replay 步进 heapq 调度
- [x] Grep `def _sync_play_loop\b` 在 runtime_mode_module.py = 0
- [x] Grep `time\.sleep\(interval\)` 在 runtime_mode_module.py replay 路径 = 0
- [x] `play()` 调用 `EventDriver.schedule(step_event, fire_time=...)` 入 heapq
- [x] `pause()` 通过 `EventDriver.cancel(step_event_id)` 取消调度

### 变更 E2 — simulation auto-step heapq 调度
- [x] Grep `def _sync_sim_loop\b|async def auto_step_loop\b` 在 runtime_mode_module.py = 0
- [x] Grep `while self\._run\b|while self\._sim_auto_step\b` 在 runtime_mode_module.py = 0
- [x] `start_auto()` 调用 `EventDriver.schedule(sim_step_event, fire_time=...)` 入 heapq
- [x] 停止通过 `ModeChanged` 事件订阅或 `EventDriver.cancel` 取消

### 变更 E3 — 文件监视 watchdog 事件驱动
- [ ] Grep `def start_polling\b` 在 table_engine.py = 0
- [ ] Grep `def _file_watcher_loop\b` 在 services/data.py = 0
- [ ] Grep `asyncio\.sleep\(3\)` 在 services/data.py 文件监视路径 = 0
- [ ] `services/data.py` 改为依赖 `watchdog.Observer` 发布 `FileModified` 事件

### 变更 E4 — SSE 流 asyncio.Queue 阻塞等待
- [ ] Grep `run_in_executor\(.*drain` 在 app.py = 0
- [ ] Grep `asyncio\.sleep\(0\.05\)` 在 app.py SSE 路径 = 0
- [ ] SSE 流使用 `asyncio.Queue(maxsize=10000)` + `asyncio.wait_for(queue.get(), timeout=15.0)`
- [ ] EventBus 订阅回调 `queue.put_nowait(event_data)`

### 变更 E5 — services/data.py 周期刷新 heapq 调度
- [ ] Grep `def _refresh_with_backoff\b` 在 services/data.py = 0
- [ ] Grep `asyncio\.sleep\(wait_seconds\)` 在 services/data.py 每日调度路径 = 0
- [ ] 周期刷新入 heapq `TimedEventSpec(fire_time=now+interval, action=refresh_fn)`
- [ ] 每日定时入 heapq `TimedEventSpec(fire_time=next_target, action=daily_reload)`

### 变更 E6 — 前端 4 处 setInterval 改为 SSE/WS 订阅
- [ ] Grep `setInterval.*_poll` 在 web/js/app.js = 0
- [ ] Grep `fetch\('/api/state/runtime'\)` 在 web/js/app.js = 0
- [ ] Grep `setInterval.*\/reload` 在 web/js/ui.js = 0
- [ ] Grep `setInterval.*\/api\/highlight-events` 在 web/js/ui.js = 0
- [ ] Grep `setInterval.*syncTimerQueue` 在 web/js/event-panel.js = 0
- [ ] Grep `fetch\('\/api\/events\/timer-queue` 在 web/js/event-panel.js = 0
- [ ] `/api/highlight-events` GET 端点在 app.py 删除
- [ ] 前端版本号 `?v=N` 更新（RULES.md 第 79 条）

## 评审工程师检查点（阶段 3：core/*.py 第二轮深度同构合并）

### 变更 C1 — _FieldedBase mixin
- [x] `_FieldedBase` mixin 在 core/domain.py 中定义，含 `_common_to_dict` / `_common_from_dict` 方法
- [x] `_NodeBase` / `_EdgeBase` 继承 `_FieldedBase`，`to_dict` / `from_dict` 方法体 ≤ 2 行
- [x] `from_dzh_type` / `from_tdx_type` / `from_dzh_attr` / `from_tdx_source_type` 4 个 classmethod 委托 `_lookup_in_registry`
- [x] Grep `def to_dict\b|def from_dict\b` 在 domain.py 仅匹配 `_FieldedBase._common_to_dict/_common_from_dict` + 4 个 1 行薄包装

### 变更 C2 — schemas.py _DictConstructible mixin
- [x] `_DictConstructible` mixin 在 core/schemas.py 中定义，含 `from_dict` classmethod
- [x] 6 个 Pydantic 模型继承 `_DictConstructible`
- [x] Grep `def from_dict\b` 在 schemas.py 仅匹配 `_DictConstructible.from_dict` 1 处（注：6 个 TDX 模型范围内唯一 from_dict 真相源；`DynamicCellModel`/`DynamicFlowModel` 为 dict-wrapper 范式保留自身 from_dict，非本变更范围）

### 变更 C3 — _hashing.py 模块
- [ ] `core/_hashing.py` 模块定义 `hash_dict_content` / `hash_tick_aggregate` / `BarHashMixin`
- [ ] Grep `def _hash_tick\b|def _hash_bar\b|def _hash_bars\b|def _hash_object\b|def _hash_code_bars\b` 在 core/*.py = 0（仅 _hashing.py 内 `hash_dict_content` 允许）
- [ ] Grep `def _hash_tick_data\b|def _hash_period_bars\b` 在 core/*.py = 0（仅 _hashing.py 内 `hash_tick_aggregate` 允许）
- [ ] Grep `def bar_hash\b` 在 core/*.py ≤ 1（仅 BarHashMixin 定义）
- [ ] `_NodeBase` / `_EdgeBase` / `PoolStateMixin` / `EdgeExecutor` / `_InternalState` / `BarComposer` 序列化/哈希结果与原逻辑一致

### 变更 C4 — safe_cast 跨模块统一
- [ ] Grep `def _safe_int\b|def _safe_float\b|def _to_float\b|def _cast_int\b|def _cast_str\b` 在 core/*.py = 0（仅 converters/_common.py 内允许）
- [ ] Grep `try:\s+float\(.*\)\s+except\s+\(TypeError,\s*ValueError\)` 在 core/*.py ≤ 3（仅业务必要的保留）

### 变更 C5 — ConfigStoreBase 基类
- [x] `ConfigStoreBase` 基类在 core/table_engine.py 中定义，含 `check_and_reload` / `rollback` 模板方法
- [x] Grep `def check_changes\b|def check_and_reload\b|def rollback\b` 在 table_engine.py ≤ 3
- [x] 热加载与回滚功能与原逻辑一致

### 变更 C6 — _ADAPTER_SPECS 声明式表
- [x] `_ADAPTER_SPECS` 表在 core/monitoring_module.py 中定义，含 24 个 key
- [x] `_build_adapter_record(spec_key, event)` 通用 builder 定义
- [x] Grep `def _adapter_\w+\b` 在 monitoring_module.py = 0
- [x] 24 个事件类型的 adapter record 与原逻辑一致

### 变更 C7 — ranking 表驱动
- [x] `_RANKING_SPECS` 表定义，含 pk_ranking / analysis_angles 2 条
- [x] Grep `def compute_pk_ranking\b|def compute_analysis_angles\b` 在 monitoring_module.py = 0（或方法体 ≤ 3 行 thin wrapper）

### 变更 C8 — execution_module 表驱动收敛
- [x] Grep `def _compile_timing_spec\b|def _compile_filter_spec\b|def _compile_propagate_spec\b` 在 execution_module.py 仅匹配 1 行委托
- [x] Grep `def _make_edge_action\b|def _make_ttl_interval_action\b|def _make_ttl_endtime_action\b` 在 execution_module.py 仅匹配 1 行委托
- [x] `Step` 基类定义，5 个 `XStep` 类继承
- [x] Grep `def _gate_before_open\b|def _gate_after_open\b|def _gate_before_close\b|def _gate_after_close\b` 在 execution_module.py 仅匹配 1 行委托
- [x] Grep `def _publish_edge_fired\b|def _publish_ttl_due\b` 在 execution_module.py = 0

### 变更 C9 — trade_module 表驱动
- [x] `_SIDE_SPECS` 表定义，含 BUY / SELL 2 条
- [x] Grep `def _execute_buy\b|def _execute_sell\b` 在 trade_module.py = 0
- [x] `_PSATT_SIDE_EFFECTS` 表定义，含 5 条
- [x] Grep `if action_spec\.bsavehis|if action_spec\.bsound|if action_spec\.btip|if action_spec\.bsavetoblock|if action_spec\.baimpool` 在 trade_module.py = 0

### 变更 C10 — runtime_mode_module 表驱动收敛
- [ ] `_aggregate_ohlcv(group)` helper 定义
- [ ] Grep `"open": group\[0\]\["open"\]` 在 runtime_mode_module.py 仅匹配 `_aggregate_ohlcv` 1 处
- [ ] `_DATE_KEYS` 表定义
- [ ] Grep `def _get_week_key\b|def _get_month_key\b|def _day_key\b` 在 runtime_mode_module.py = 0（或方法体 ≤ 2 行查表）
- [ ] `_status(status, **extra)` helper 定义

### 变更 C11 — _SUBSCRIPTIONS 类属性表
- [ ] `_BaseModule` 基类在 core/event_bus.py 中定义，含 `_SUBSCRIPTIONS` 类属性 + `register_subscribers` 方法
- [ ] 7 模块继承 `_BaseModule`，定义 `_SUBSCRIPTIONS` 类属性表
- [ ] Grep `def _register_subscribers\b` 在 7 模块 = 0（仅 _BaseModule 内允许）
- [ ] Grep `self\._bus\.subscribe\(EventType, self\._on_` 在 7 模块 = 0（仅 _BaseModule.register_subscribers 内允许）

### 变更 C12 — get_table 防御性调用统一
- [x] `_get_table(name)` helper 定义
- [ ] Grep `get_global_config_store\(\)\.get_table.*\n.*if get_global_config_store` 在 core/*.py = 0（双调用 perf smell 消除）

## 评审工程师检查点（阶段 4：metatest v4 重建）

### 三大统一原语收敛检查点（新增，极致本质运行时根因）
- [ ] **时间原语**：Grep `while.*sleep|run_in_executor\(.*drain|setInterval.*fetch` 在 core/*.py + services/*.py + app.py + web/js/*.js 非测试代码 = 0
- [ ] **时间原语覆盖率** ≥ 95%（EventDriver.add_spec / asyncio.Queue / watchdog 触发数 / 总时间触发数）
- [ ] **分派原语**：Grep `def _adapter_\w+|def _execute_buy\b|def _execute_sell\b|def _compile_timing_spec\b|def _compile_filter_spec\b|def _compile_propagate_spec\b` 在 core/*.py = 0
- [ ] **分派原语覆盖率** ≥ 95%（表驱动分派数 / (表驱动 + if/elif + 同构函数)）
- [ ] **继承原语**：Grep `def to_dict\b` 在 domain.py 仅匹配 `_FieldedBase._common_to_dict` + 薄包装
- [ ] **继承原语覆盖率** ≥ 95%（基类公共方法数 / (基类 + 子类同构方法总数)）
- [ ] **合并非拆分硬约束**：每处变更净减行数 > 0（净增计 redo）
- [ ] **essence_ratio** ≥ 12%（净减行数 / 变更前行数）
- [ ] 新建文件仅允许 `converters/_common.py` / `core/_hashing.py`
- [ ] 抽象基类/mixin 伴随 ≥ 2 处子类收敛（无「无效抽象」）

### 运行时三核 Dispatcher 元统一检查点（第四层洞察，DDD 根因）
- [ ] **EventBus 唯一**：跨模块通信全经 `bus.publish/subscribe`，无自造事件循环（Grep `while\s+True|while\s+self\._\w+\s*[:)]` 在 core/*.py + services/*.py 非测试代码 = 0）
- [ ] **EventDriver 唯一**：时间触发全经 `driver.schedule`，无自造时间调度（Grep `time\.sleep|asyncio\.sleep\(\d` 在 core/*.py + services/*.py + app.py 非测试代码 = 0）
- [ ] **ConfigStore 唯一**：配置读取全经 `store.get_table`，无绕过（Grep `get_global_config_store\(\)\.get_table.*\n.*if get_global_config_store` 在 core/*.py = 0）
- [ ] **无自造分派同构副本**：Grep `def _safe_int\b|def _safe_float\b|def _hash_\w+\b|def _adapter_\w+\b|def _execute_buy\b|def _execute_sell\b|def _compile_\w+_spec\b` 在 core/*.py + services/*.py = 0
- [ ] **meta_purity** ≥ 90%（(Data 声明行数 + Dispatcher 调用行数) / 总业务行数）
- [ ] **三原语覆盖率 ≥ 95% 当且仅当 meta_purity ≥ 90%**（根因解释层一致性）
- [ ] **禁止再造第四核 Dispatcher**：新模块 = Data 声明 + 三核调用 + 胶水，自造事件循环/调度器/配置加载计 redo
- [ ] **核心模块总行数** `wc -l core/*.py` ≤ 22,500（Data + Dispatcher 净减后行数）

### scoring.py v4（16 维量化评分 + 三原语收敛度）
- [ ] `DIMENSIONS` 升级为 16 维，权重和 = 100%（v3 12 维降权 + oop_inheritance_depth 8% + polling_zero_tolerance 8% + primitive_convergence 8% + essence_ratio 4%）
- [ ] `_score_oop_inheritance_depth` 方法实现
- [ ] `_score_polling_zero_tolerance` 方法实现
- [ ] `_score_primitive_convergence` 方法实现（时间/分派/继承三原语覆盖率）
- [ ] `_score_essence_ratio` 方法实现（净减行数 / 变更前行数，净增 = 0 且 redo）
- [ ] `ISOMORPHISM_CHECKS_TOTAL` = 40
- [ ] `line_convergence` 目标 = 22,500
- [ ] 所有维度评分由 test_results 字段计算，无硬编码信用分

### runner.py v4（真实测试结果采集 + 三原语覆盖率）
- [ ] 采集 `BasePoolConverter` 存在性 + 子类继承关系，填入 test_results["oop_inheritance"]
- [ ] 采集 12 处轮询模式 Grep 结果，填入 test_results["polling_violations"]
- [ ] 采集 40 项同构检查 Grep 结果
- [ ] 采集三原语覆盖率，填入 test_results["primitive_convergence"]
- [ ] 采集 essence_ratio，填入 test_results["essence_ratio"]
- [ ] report.json 含 16 维明细 + 总分 + PASS/FAIL + redo_list

### 正测试（test_positive_*.py）
- [ ] `test_positive_oop_inheritance.py` — 验证 BasePoolConverter + DzhPoolConverter / TdxPoolConverter 继承结构
- [ ] `test_positive_event_driven.py` — 验证 EventDriver heapq 调度 + asyncio.Queue + watchdog
- [ ] `test_positive_dzh_tdx_isomorphism.py` — 验证 25 组 DZH/TDX 同构合并回归
- [ ] `test_positive_core_isomorphism_v2.py` — 验证 30+ 组 core 第二轮同构合并回归
- [ ] `test_positive_primitive_convergence.py` — 验证三原语覆盖率各 ≥ 95%
- [ ] 升级原有 24 个 test_positive_*.py 含本次合并回归断言
- [ ] 断言密度 ≥ 20/文件

### 反测试（test_negative_*.py）
- [ ] `test_negative_polling.py` — 12 处轮询模式 Grep 零匹配（≥ 12 用例）
- [ ] `test_negative_dzh_tdx_revival.py` — 25 组旧同构代码 Grep 零匹配
- [ ] `test_negative_oop_violation.py` — DZH/TDX 子类未重新引入同构方法
- [ ] `test_negative_split_detection.py` — 每处变更净减行数 > 0 + 新建文件白名单 + 抽象基类伴随 ≥ 2 子类
- [ ] 升级原有 4 类反测试含本次新增 30+ 组模式
- [ ] 每类 ≥ 8 用例

### 合测试（test_synthesis_*.py）
- [ ] `test_synthesis_dzh_tdx_roundtrip.py` — DZH↔TDX 双格式互转保真
- [ ] `test_synthesis_event_driven_no_sleep.py` — 事件链路无 sleep
- [ ] `test_synthesis_table_driven_dispatch.py` — 所有分派表存在且非空
- [ ] `test_synthesis_primitive_convergence.py` — 三原语覆盖率各 ≥ 95% + 三原语不变量 Grep 零违规
- [ ] 升级原有 8 个 test_synthesis_*.py 含本次端到端验证
- [ ] 前端 E2E 通过 Playwright 真实浏览器验证

### metatest/README.md v4
- [ ] 文档化 16 维评分规则与权重
- [ ] 文档化 40 项同构检查清单
- [ ] 文档化 OOP 同源继承验证规则
- [ ] 文档化轮询零容忍验证规则
- [ ] 文档化三原语收敛度验证规则（时间/分派/继承原语覆盖率）
- [ ] 文档化「合并非拆分」硬约束（essence_ratio + 拆分检测）

## 评审工程师检查点（阶段 5：文档同步与全量回归）

### RULES.md 新增第 101-110 条
- [ ] 第 101 条 — DZH/TDX OOP 同源继承
- [ ] 第 102 条 — DZH↔TDX 类型映射单一真相源
- [ ] 第 103 条 — _CONVERTER_REGISTRY 完整 OOP 路由
- [ ] 第 104 条 — 公共工具函数下沉 converters/_common.py
- [ ] 第 105 条 — replay/simulation heapq 调度禁轮询
- [ ] 第 106 条 — 文件监视 watchdog 禁 mtime 轮询
- [ ] 第 107 条 — SSE 流 asyncio.Queue 禁 50ms 轮询
- [ ] 第 108 条 — 前端 setInterval 禁轮询改 SSE/WS
- [ ] 第 109 条 — _FieldedBase / _ADAPTER_SPECS / _SUBSCRIPTIONS 表驱动
- [ ] 第 110 条 — 哈希函数三族统一到 _hashing.py

### 全量回归
- [ ] `python -m pytest metatest/ -x` 全量测试通过（含正反合）
- [ ] `python -m metatest.runner` 总分 ≥ 95 且 16 维均 ≥ 80 判定 PASS
- [ ] eventtest 全部通过（退出码 0）
- [ ] 核心模块总行数 ≤ 22,500，line_convergence 维度满分
- [ ] Grep RULES 101-110 对应 10 条违规模式，rule_compliance 维度满分
- [ ] Grep 40 项同构检查 0 违规，isomorphism_elimination 维度满分
- [ ] Grep 12 处轮询模式 0 匹配，polling_zero_tolerance 维度满分
- [ ] BasePoolConverter + DzhPoolConverter / TdxPoolConverter 继承结构正确，oop_inheritance_depth 维度满分
- [ ] 三原语覆盖率（时间/分派/继承各 ≥ 95%），primitive_convergence 维度满分
- [ ] essence_ratio ≥ 12%（净减行数 / 变更前行数），essence_ratio 维度满分
- [ ] 无任何变更净增行数（拆分检测反测试通过）
- [ ] 启动 replay 验证步进由 EventDriver heapq 调度，事件链完整
- [ ] 启动 simulation 验证 auto-step 由 EventDriver heapq 调度
- [ ] DZH↔TDX 双格式互转保真验证
- [ ] 三模式（仿真/回放/实盘）切换后事件链路正常

## 禁止项检查

- [ ] 禁止拆分代码（本次是合并同构代码，不是拆分。每处变更必须净减行数，essence_ratio 维度强制）
- [ ] 禁止皮毛修改（每处变更必须是底层运行逻辑洞察，真正同构代码合并，收敛到三大统一原语）
- [ ] **禁止绕过三大统一原语**（时间原语 EventDriver heapq / 分派原语 TABLE+dispatch / 继承原语 Base+Sub，所有时间触发/分派/同构必须经原语）
- [ ] 禁止重新引入已合并的 DZH/TDX 同构函数（25 组，RULES 101 约束）
- [ ] 禁止重新引入已合并的 core 第二轮同构函数（30+ 组，RULES 109 约束）
- [ ] 禁止重新引入并行 DZH↔TDX 类型映射表（RULES 102 约束）
- [ ] 禁止 api.py / app.py 绕过 _CONVERTER_REGISTRY 直接调用 parse_dzh_xml / parse_tdx_xml / _build_tdx_xml（RULES 103 约束）
- [ ] 禁止模块级重新定义 _safe_int / _safe_float / _decode_formula 等公共工具（RULES 104 约束）
- [ ] 禁止重新引入 while True + time.sleep / asyncio.sleep 步进循环（RULES 105 约束，违反时间原语）
- [ ] 禁止重新引入 while _running + asyncio.sleep + 比较 mtime 文件监视轮询（RULES 106 约束，违反时间原语）
- [ ] 禁止重新引入 run_in_executor(drain) + asyncio.sleep(0.05) SSE 队列轮询（RULES 107 约束，违反时间原语）
- [ ] 禁止前端重新引入 setInterval + fetch 轮询（RULES 108 约束，违反时间原语）
- [ ] 禁止重新引入 _adapter_X 同构函数 / if node.type == 链 / _register_subscribers 过程式链（RULES 109 约束，违反分派原语）
- [ ] 禁止跨模块重新定义 _hash_tick / _hash_bar / _hash_tick_data 等哈希函数（RULES 110 约束，违反继承原语）
- [ ] 禁止随意评分（所有评分维度必须由真实测试结果计算，无硬编码信用分）
- [ ] 禁止跳过测试给信用分（跳过计失败，分子不计入）
- [ ] 禁止前端 E2E 环境缺失给信用分（环境缺失计 frontend_e2e_passed=0）
- [ ] 禁止减少测试覆盖（metatest v4 必须 ≥ v3 的覆盖范围，且新增 OOP + 轮询 + 第二轮同构 + 三原语收敛 + 拆分检测验收）
- [ ] 禁止删除表驱动分派表（_CONVERTER_REGISTRY / _ADAPTER_SPECS / _SUBSCRIPTIONS / _SIDE_SPECS / _PSATT_SIDE_EFFECTS / _RANKING_SPECS / _FIELDedBase / _DZH_TDX_TYPE_MAP 等保持表驱动）
- [ ] 禁止在合并后的统一函数中重新引入 if/elif 分支（必须表驱动或参数化，违反分派原语）
- [ ] 禁止净增行数的变更（essence_ratio = 0 且触发 redo，强制「合并非拆分」）
- [ ] 禁止「无效抽象」（抽象基类/mixin 未伴随 ≥ 2 处子类收敛，计 redo）
