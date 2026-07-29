# Tasks

本规范按「架构工程师（实施）→ 评审工程师（验证）」迭代流程分 5 阶段实施，覆盖 25 组 DZH/TDX OOP 同源继承 + 12 处轮询消除 + 30+ 组 core 第二轮同构合并 + metatest v4 重建 + 16 维量化评分。**第四层洞察（数据驱动分派 DDD）**：三原语（时间/分派/继承）同构于运行时三核 Dispatcher（EventBus / EventDriver / ConfigStore），67 处违规 = 绕过三核重写 Data 副本，本次迭代核心 = 删除重写强制收敛到已存在原语（非新建机制、非拆分）。

## 阶段 1：DZH/TDX OOP 同源继承（高优先级，OOP 完善核心）

### 架构工程师任务

- [x] Task 1: 变更 P1 — 引入 `BasePoolConverter` 抽象基类 + DzhPoolConverter / TdxPoolConverter 子类
  - [x] SubTask 1.1: 在 `converters.py` 中定义 `BasePoolConverter` 抽象基类，含 `_parse_element(elem, schema_key, int_fields, post_hook=None)` 泛型解析器、`_add_element(cell_elem, attr_name, model_class, element_name)` 序列化器、`_decode_pos(pos_str, *, as_dict=True)` / `_decode_xml_bytes(raw, encoding_priority, post_process_fn=None)` 公共方法
  - [x] SubTask 1.2: 定义 `DzhPoolConverter(BasePoolConverter)` 与 `TdxPoolConverter(BasePoolConverter)` 子类，子类仅实现差异（int_fields 表、post_hook、encoding_priority、cell envelope params 构建）
  - [x] SubTask 1.3: 合并 `_parse_func_element` / `_parse_psatt_element` / `_parse_spinfo_element` 3 函数为基类 `_parse_element` + 3 个 `(schema_key, int_fields, post_hook)` 三元组（净减 ~30 行）
  - [x] SubTask 1.4: 合并 `_add_func` / `_add_psatt` / `_add_spinfo` 3 序列化器为基类 `_add_element` + 3 行表（净减 ~25 行）
  - [x] SubTask 1.5: 合并 DZH `_parse_cell_pool/market/condition` 与 TDX `_convert_candidate/state_pool/condition_cell` 共用 `_CellEnvelope` 基类（封装 `{id, type, label, params, position, ...}` 骨架）（净减 ~60 行）
  - [x] SubTask 1.6: 合并 `_build_cell_default/pool/market` 3 工厂共用 6 行开头骨架提取为 `_build_cell_base(node, model_class)`（净减 ~15 行）
  - [x] SubTask 1.7: 合并 `_parse_stk_children` / `_parse_stk_elements` 为 `_StkIO.parse_stks` mixin 方法 + 合并 `_export_field_stocks` / `_add_stks` 为 `_StkWriter.write_stks` mixin 方法（净减 ~25 行）
  - [x] SubTask 1.8: 合并 `_parse_pos` / `_parse_tdx_pos` 为基类 `_decode_pos(pos_str, *, as_dict=True)`（净减 ~10 行）
  - [x] SubTask 1.9: 合并 `_decode_xml_content` / `_decode_tdx_xml` 为基类 `_decode_xml_bytes(raw, encoding_priority, post_process_fn=None)`（净减 ~10 行）
  - [x] SubTask 1.10: TDX flow 11 字段 `getattr(...) if None: fallback` 改为查 `xml_mapping.json:flow.attributes` 表迭代（净减 ~40 行）
  - [x] SubTask 1.11: `_make_tdx_cell` 3 分支 8 kwargs 重复改为 `_make_tdx_cell(internal_type, tdx_cell, pos, **extra)` 单一入口（净减 ~20 行）
  - [x] SubTask 1.12: 合并 6 个 DZH `_export_field_*` 函数为 `_export_field_table` 表 + 单循环（净减 ~25 行）
  - [x] SubTask 1.13: 合并 `convert_tdx_to_config` 与 `parse_dzh_xml` 的 final-assembly 骨架为 `_assemble_pool_result(cells, flows, **extras)` helper（净减 ~15 行）
  - [x] SubTask 1.14: 合并 DZH `trades`/`opentrades` export 双块为单循环（净减 ~12 行）

- [x] Task 2: 变更 P2 — 统一 DZH↔TDX 类型映射为单一真相源
  - [x] SubTask 2.1: 在 `config/architecture/dzh_type_map.json` 中定义 `dzh_to_tdx` / `tdx_to_dzh` / `tdx_to_frontend` / `frontend_to_tdx` 四向映射表（合并 5 处并行表的并集，修复 DZH type 3→TDX 3 vs 0 矛盾为唯一值）
  - [x] SubTask 2.2: `core/schemas.py:954-996 TDX_TO_DZH_CELL_TYPE` + `TDX_CELL_TYPE_MAP` 改为从 `ConfigStore.get_table("dzh_type_map")` 派生（删除 `_init_tdx_maps` 函数）
  - [x] SubTask 2.3: `converters.py:4450 _DZH_TO_TDX_TYPE` + `:4882 _DZH_TO_TDX_TYPE_EXPORT` 改为从 ConfigStore 读取（删除两个并行 dict）
  - [x] SubTask 2.4: `config/xml_mapping.json:dzh_to_tdx_type` / `tdx_to_frontend_type` / `frontend_to_tdx_type` 改为引用 `dzh_type_map.json`（消除冗余）
  - [x] SubTask 2.5: 添加单元测试断言 `dzh_to_tdx` 与 `tdx_to_dzh` 互为逆映射（消除矛盾）
  - [x] SubTask 2.6: Grep `_DZH_TO_TDX_TYPE\b|_DZH_TO_TDX_TYPE_EXPORT\b|TDX_TO_DZH_CELL_TYPE\b|TDX_CELL_TYPE_MAP\b` 在 *.py = 0

- [x] Task 3: 变更 P3 — `_CONVERTER_REGISTRY` 完整 OOP 路由 + 强制所有调用站点走 registry
  - [x] SubTask 3.1: 扩展 `_CONVERTER_REGISTRY` 表，支持 `path_or_data` 参数（既可接受文件路径，也可接受 bytes/str 内容），fmt 由 `is_tdx_format` 自动探测或显式传入
  - [x] SubTask 3.2: `api.py:5396-5407 dzh_import` 的 `is_tdx_format` 分支删除，改为 `_call_converter(content, fmt=None, direction="import")`（fmt=None 时自动探测）
  - [x] SubTask 3.3: `api.py:5509-5520 dzh_import_and_save` 同上
  - [x] SubTask 3.4: `api.py:6828-6835 dzh_load_demo` 同上
  - [x] SubTask 3.5: 提取 `_load_xml_content_from_request(request)` helper，合并 `dzh_import` 与 `dzh_import_and_save` 共享的 58 行内容加载骨架（含 6 行多编码解码循环 3 处复制）
  - [x] SubTask 3.6: `app.py:698-802` 4 个 TDX CRUD 端点（`_tdx_load` / `_tdx_create` / `_tdx_save` / `tdx_export_xml`）改为 `_call_converter` 入口
  - [x] SubTask 3.7: `api.py:5573 dzh_export` 改为 `_call_converter(path, "dzh", "export", config=config)`
  - [x] SubTask 3.8: `app.py:2561 load_dzhpool_file` 改为 `_call_converter(path, "dzh", "import")`
  - [x] SubTask 3.9: Grep `parse_dzh_xml\(|parse_tdx_xml\(|_build_tdx_xml\(|export_meta_to_dzh_xml_bytes\(` 在 api.py / app.py = 0
  - [x] SubTask 3.10: Grep `is_tdx_format` 在 api.py 内 if 分支 = 0（仅作为 fmt 探测函数调用）

- [x] Task 4: 变更 P4 — 公共工具函数下沉 `converters/_common.py`
  - [x] SubTask 4.1: 新建 `converters/_common.py` 模块，定义 `safe_int(val, default=0)` / `safe_float(val, default=0.0)` / `safe_cast(v, cast_fn, default, empty_check=True)` / `decode_formula` / `extract_formula_from_binary` / `is_valid_formula` / `extract_text_segments` / `decode_xml_bytes` / `decode_pos` / `hash_dict_content`
  - [x] SubTask 4.2: `core/import_export_module.py:39-56` `_safe_int` / `_safe_float` 改为 `from converters._common import safe_int, safe_float`（删除模块内定义）
  - [x] SubTask 4.3: `core/import_export_module.py:64-180` `_extract_text_segments` / `_is_valid_formula` / `_extract_formula_from_binary` / `_decode_formula` 改为从 `converters._common` 引用（删除 4 个函数定义，约 120 行）
  - [x] SubTask 4.4: `services/providers.py:557-673` 4 个公式解码器副本删除（与 SubTask 4.3 协同），改为 `from converters._common import decode_formula, ...`
  - [x] SubTask 4.5: `services/tq_adapter.py` 中 `decode_formula` re-export 改为从 `converters._common` 引用
  - [x] SubTask 4.6: `native/builtins.py:60-73 _decode_formula_base64` 改为 thin wrapper 调用 `converters._common.decode_formula`
  - [x] SubTask 4.7: `native/validators.py:2182-2188 _safe_int` 副本删除，改为从 `converters._common` 引用
  - [x] SubTask 4.8: `core/schemas.py:96-112 _load_dzh_type_map` 与 `converters.py:2641-2650 _load_dzh_type_map` 双实现删除，改为通过 `ConfigStore.get_table("dzh_type_map")`
  - [x] SubTask 4.9: Grep `def _safe_int\b|def _safe_float\b|def _to_float\b|def _cast_int\b|def _cast_str\b|def _decode_formula\b|def _extract_formula_from_binary\b|def _is_valid_formula\b|def _extract_text_segments\b|def _load_dzh_type_map\b` 在 core/*.py + native/*.py + services/*.py = 0（仅 converters/_common.py 内允许）

### 评审工程师任务（阶段 1 验证）

- [ ] Task 5: 阶段 1 Grep + 回归验证
  - [x] SubTask 5.1: Grep `def _parse_func_element|def _parse_psatt_element|def _parse_spinfo_element|def _add_func\b|def _add_psatt\b|def _add_spinfo\b` 在 converters.py = 0
  - [x] SubTask 5.2: Grep `class BasePoolConverter|class DzhPoolConverter|class TdxPoolConverter` 在 converters.py 存在且继承关系正确
  - [ ] SubTask 5.3: Grep `_DZH_TO_TDX_TYPE\b|_DZH_TO_TDX_TYPE_EXPORT\b|TDX_TO_DZH_CELL_TYPE\b|TDX_CELL_TYPE_MAP\b` 在 *.py = 0
  - [ ] SubTask 5.4: Grep `parse_dzh_xml\(|parse_tdx_xml\(|_build_tdx_xml\(|export_meta_to_dzh_xml_bytes\(` 在 api.py / app.py = 0
  - [ ] SubTask 5.5: Grep `def _safe_int\b|def _safe_float\b|def _decode_formula\b|def _extract_formula_from_binary\b` 在 services/providers.py + native/validators.py = 0
  - [x] SubTask 5.6: 运行 DZH↔TDX 双格式导入导出往返测试，确认保真
  - [x] SubTask 5.7: 运行 `python -m pytest metatest/test_positive_import_export.py -x` 确认无回归
  - [ ] SubTask 5.8: 核心模块 + converters.py 总行数较基线净减 ≥ 560 行（Task 1 完成：converters.py 净减 201 行；待 Tasks 2-4 完成达成 ≥560）

## 阶段 2：彻底事件驱动（高优先级，禁止轮询）

### 架构工程师任务

- [ ] Task 6: 变更 E1 — replay 步进改为 EventDriver heapq 调度
  - [ ] SubTask 6.1: 在 `core/execution_module.py` EventDriver 中确认 `schedule(event, fire_time)` + `cancel(event_id)` + `loop.call_at` 调度链可用（若不足则扩展）
  - [ ] SubTask 6.2: `core/runtime_mode_module.py:726-748 _sync_play_loop` 删除，改为 `play()` 调用 `EventDriver.schedule(step_event, fire_time=now + base_interval/speed)`，步进事件回调中调用 `_do_step()` 并重新调度
  - [ ] SubTask 6.3: `pause()` 改为 `EventDriver.cancel(step_event_id)` 取消调度，`resume()` 重新调度
  - [ ] SubTask 6.4: `stop()` 清空 heapq 中所有 step 事件
  - [ ] SubTask 6.5: Grep `def _sync_play_loop\b` 在 runtime_mode_module.py = 0
  - [ ] SubTask 6.6: Grep `time\.sleep\(interval\)` 在 runtime_mode_module.py replay 路径 = 0

- [ ] Task 7: 变更 E2 — simulation auto-step 改为 EventDriver heapq 调度
  - [ ] SubTask 7.1: `core/runtime_mode_module.py:2122-2138 _sync_sim_loop` 删除，改为 `start_auto()` 调用 `EventDriver.schedule(sim_step_event, fire_time=now + 1.0/speed)`
  - [ ] SubTask 7.2: 步进事件回调中调用 `step_simulation(step_idx)` 并重新调度
  - [ ] SubTask 7.3: `core/runtime_mode_module.py:2509-2525 auto_step_loop` 删除（疑似死代码，但违规模式存在）
  - [ ] SubTask 7.4: 停止通过 `ModeChanged` 事件订阅或 `EventDriver.cancel` 取消，禁止 `_run` / `_sim_auto_step` / `_current_mode` 标志轮询
  - [ ] SubTask 7.5: Grep `def _sync_sim_loop\b|async def auto_step_loop\b` 在 runtime_mode_module.py = 0
  - [ ] SubTask 7.6: Grep `while self\._run\b|while self\._sim_auto_step\b` 在 runtime_mode_module.py = 0

- [x] Task 8: 变更 E3 — 移除 `ConfigStore.start_polling` 与 `services/data.py._file_watcher_loop` 两处文件 mtime 轮询
  - [x] SubTask 8.1: `core/table_engine.py:1700-1715 start_polling` 方法删除（`start_watchdog` 已存在为正确路径）
  - [x] SubTask 8.2: `services/data.py:5994-6005 _file_watcher_loop` 函数删除
  - [x] SubTask 8.3: `services/data.py` 改为依赖 `watchdog.Observer` 发布 `FileModified` 事件到 EventBus，`DataManager` 订阅事件重新加载
  - [x] SubTask 8.4: 验证 `app.py:507` 已调用 `start_watchdog`，无需改动
  - [x] SubTask 8.5: Grep `def start_polling\b` 在 table_engine.py = 0
  - [x] SubTask 8.6: Grep `def _file_watcher_loop\b` 在 services/data.py = 0
  - [x] SubTask 8.7: Grep `asyncio\.sleep\(3\)` 在 services/data.py 文件监视路径 = 0

- [x] Task 9: 变更 E4 — SSE 流改为 `asyncio.Queue + await queue.get()`
  - [x] SubTask 9.1: `app.py:1067-1200 events_stream` 删除 `while True + run_in_executor(drain_sync_queue) + asyncio.sleep(0.05)` 50ms 队列轮询
  - [x] SubTask 9.2: 改为每会话创建 `asyncio.Queue(maxsize=10000)`，EventBus 订阅回调 `queue.put_nowait(event_data)`
  - [x] SubTask 9.3: 流循环 `asyncio.wait_for(queue.get(), timeout=15.0)` 阻塞等待事件，超时发心跳
  - [x] SubTask 9.4: `request.is_disconnected()` 检测保留作为退出条件
  - [x] SubTask 9.5: 镜像 `api.py:824-862 events_ws` 已验证的 `asyncio.Queue + await queue.get()` 模式
  - [x] SubTask 9.6: Grep `run_in_executor\(.*drain` 在 app.py = 0
  - [x] SubTask 9.7: Grep `asyncio\.sleep\(0\.05\)` 在 app.py SSE 路径 = 0

- [x] Task 10: 变更 E5 — `services/data.py` 三处 `asyncio.sleep` 周期刷新改为 EventDriver heapq 调度
  - [x] SubTask 10.1: `services/data.py:5785-5842 _refresh_with_backoff` 删除，改为入 heapq `TimedEventSpec(fire_time=now+interval, action=refresh_fn)`，触发后重新调度
  - [x] SubTask 10.2: `services/data.py:5740-5763 _daily_loop` 删除，改为入 heapq `TimedEventSpec(fire_time=next_target, action=daily_reload)`，触发后重新计算次日 fire_time
  - [x] SubTask 10.3: 文件变化驱动的刷新改为订阅 watchdog 事件（与 E3 协同）
  - [x] SubTask 10.4: Grep `def _refresh_with_backoff\b` 在 services/data.py = 0
  - [x] SubTask 10.5: Grep `asyncio\.sleep\(wait_seconds\)` 在 services/data.py 每日调度路径 = 0

- [x] Task 11: 变更 E6 — 前端 4 处 `setInterval` 轮询改为 SSE/WS 订阅
  - [x] SubTask 11.1: `web/js/app.js:66-87 RuntimeState._poll + setInterval(...,1000)` 删除，改为订阅 `EventSource('/api/events/stream')` 的 `ModeChanged` / `SnapshotUpdated` 事件更新 `mode` / `displayNowMs` / `activeSessionId`
  - [x] SubTask 11.2: `web/js/ui.js:4247-4256 _startHotReload + setInterval POST /reload` 删除，改为订阅 `/api/config/ws` WebSocket 的 `ConfigChanged` 推送
  - [x] SubTask 11.3: `web/js/ui.js:4736-4757 HighlightManager.startPolling + setInterval fetch /api/highlight-events` 删除，仅依赖 `/ws/highlight` WebSocket 推送，移除降级轮询
  - [x] SubTask 11.4: `app.py:2521-2524` `/api/highlight-events` GET 端点删除（已无前端调用）
  - [x] SubTask 11.5: `web/js/event-panel.js:2011 setInterval(syncTimerQueue,1000) + fetch /api/events/timer-queue` 删除，改为从 SSE 流的 `TimerQueued` / `TimerFired` 事件更新 `timerQueue`
  - [x] SubTask 11.6: Grep `setInterval.*_poll` 在 web/js/app.js = 0
  - [x] SubTask 11.7: Grep `setInterval.*\/reload` 在 web/js/ui.js = 0
  - [x] SubTask 11.8: Grep `setInterval.*\/api\/highlight-events` 在 web/js/ui.js = 0
  - [x] SubTask 11.9: Grep `setInterval.*syncTimerQueue` 在 web/js/event-panel.js = 0
  - [x] SubTask 11.10: 前端版本号 `?v=N` 更新（RULES.md 第 79 条）

### 评审工程师任务（阶段 2 验证）

- [ ] Task 12: 阶段 2 Grep + 回归验证
  - [ ] SubTask 12.1: Grep `def _sync_play_loop\b|def _sync_sim_loop\b|async def auto_step_loop\b` 在 runtime_mode_module.py = 0
  - [ ] SubTask 12.2: Grep `def start_polling\b|def _file_watcher_loop\b|def _refresh_with_backoff\b` 在 core/table_engine.py + services/data.py = 0
  - [ ] SubTask 12.3: Grep `run_in_executor\(.*drain` + `asyncio\.sleep\(0\.05\)` 在 app.py SSE 路径 = 0
  - [ ] SubTask 12.4: Grep `setInterval.*fetch|setInterval.*_poll|setInterval.*\/reload|setInterval.*syncTimerQueue` 在 web/js/*.js = 0
  - [ ] SubTask 12.5: 启动 replay 模式验证步进由 EventDriver heapq 调度，无 `time.sleep`
  - [ ] SubTask 12.6: 启动 simulation 模式验证 auto-step 由 EventDriver heapq 调度，无 `asyncio.sleep` 步进
  - [ ] SubTask 12.7: 修改配置文件验证 watchdog 事件驱动重载，无 mtime 轮询
  - [ ] SubTask 12.8: 连接 SSE 流验证事件推送由 `asyncio.Queue + await queue.get()` 驱动，无 50ms 轮询
  - [ ] SubTask 12.9: 浏览器验证前端 4 处状态更新由 SSE/WS 推送，无 setInterval 轮询

## 阶段 3：core/*.py 第二轮深度同构合并

### 架构工程师任务（阶段 3a：domain.py 与 schemas.py 的 OOP 完整收敛）

- [ ] Task 13: 变更 C1 — 引入 `_FieldedBase` mixin 合并 `_NodeBase` / `_EdgeBase` 的 `to_dict` / `from_dict`
  - [ ] SubTask 13.1: 在 `core/domain.py` 定义 `_FieldedBase` mixin，含 `_common_to_dict()` / `_common_from_dict(data)` 方法（合并 4 方法 ~52 行仅返回类型注解不同的逐行复制）
  - [ ] SubTask 13.2: `_NodeBase` / `_EdgeBase` 继承 `_FieldedBase`，`to_dict` / `from_dict` 改为 1 行薄包装委托
  - [ ] SubTask 13.3: 合并 `from_dzh_type` / `from_tdx_type` / `from_dzh_attr` / `from_tdx_source_type` 4 个 classmethod 为 `_lookup_in_registry(registry, t, label)` helper + 4 个 1 行薄包装
  - [ ] SubTask 13.4: Grep `def to_dict\b|def from_dict\b` 在 domain.py 仅匹配 `_FieldedBase._common_to_dict/_common_from_dict` + 4 个 1 行薄包装
  - [ ] SubTask 13.5: 验证 `_NodeBase` / `_EdgeBase` 子类序列化/反序列化结果与原逻辑一致

- [ ] Task 14: 变更 C2 — 合并 schemas.py 6 个 `from_dict` classmethod 为 `_DictConstructible` mixin
  - [ ] SubTask 14.1: 在 `core/schemas.py` 定义 `_DictConstructible` mixin，含 `@classmethod from_dict(cls, data) -> cls` 方法（合并 6 处逐字相同实现）
  - [ ] SubTask 14.2: `TdxFuncModel` / `TdxPsattModel` / `TdxSpinfoModel` / `TdxStkModel` / `TdxCellModel` / `TdxFlowModel` 6 个 Pydantic 模型继承 `_DictConstructible`
  - [ ] SubTask 14.3: 合并 `_validate_ndeltype` / `_validate_type` 等 field-validator 为 `_validate_in_set(v, allowed, message)` helper
  - [ ] SubTask 14.4: Grep `def from_dict\b` 在 schemas.py 仅匹配 `_DictConstructible.from_dict` 1 处

### 架构工程师任务（阶段 3b：哈希函数三族统一）

- [ ] Task 15: 变更 C3 — 引入 `core/_hashing.py` 模块统一三族哈希函数
  - [ ] SubTask 15.1: 新建 `core/_hashing.py`，定义 `hash_dict_content(content: dict, exclude: set = ()) -> str`（合并 per-content MD5 6 处：`_hash_tick` / `_hash_bar` / `_hash_bars` / `_hash_object` / `_hash_code_bars` / 内联 fallback）
  - [ ] SubTask 15.2: 定义 `hash_tick_aggregate(tick_data: dict, per_code_hasher) -> str`（合并 aggregate tick hash 3 处：`PoolStateMixin._hash_tick_data` / `_InternalState._hash_tick_data` / `_hash_period_bars`）
  - [ ] SubTask 15.3: 定义 `BarHashMixin` 含 `bar_hash` property（合并 3 处 `return self.X.get("_hash", "")` accessor）
  - [ ] SubTask 15.4: `core/domain.py:1906-1921 _hash_tick` 改为 thin wrapper 调用 `hash_dict_content(tick, exclude={"_ts","_hash"})`
  - [ ] SubTask 15.5: `core/tick_bar_module.py:322-328 _hash_bar` 改为 thin wrapper 调用 `hash_dict_content(bar, exclude={"_hash"})`
  - [ ] SubTask 15.6: `core/tick_bar_module.py:409-416 _hash_period_bars` + `:869-889 _InternalState._hash_tick_data` 改为 thin wrapper 调用 `hash_tick_aggregate`
  - [ ] SubTask 15.7: `core/runtime_mode_module.py:2791-2815 PoolStateMixin._hash_tick_data` 改为 thin wrapper 调用 `hash_tick_aggregate`
  - [ ] SubTask 15.8: `core/formula_module.py:1058-1081 _hash_code_bars` + `:1084-1090 _hash_bars` + `:1562-1573 _hash_object` 改为 thin wrapper 调用 `hash_dict_content`
  - [ ] SubTask 15.9: `runtime_mode_module.py:2737-2744 PoolStateMixin.bar_hash` + `execution_module.py:2559-2561 EdgeExecutor.bar_hash` + `tick_bar_module.py:865-866 _InternalState.bar_hash` 改为继承 `BarHashMixin`
  - [ ] SubTask 15.10: Grep `def _hash_tick\b|def _hash_bar\b|def _hash_bars\b|def _hash_object\b|def _hash_code_bars\b|def _hash_tick_data\b|def _hash_period_bars\b|def bar_hash\b` 在 core/*.py ≤ 8（仅 _hashing.py 内核心实现 + 必要 thin wrapper）

- [ ] Task 16: 变更 C4 — 合并 `safe_cast` 跨模块统一到 `converters/_common.py`（与 P4 协同）
  - [ ] SubTask 16.1: 在 `converters/_common.py` 定义 `safe_cast(v, cast_fn, default, empty_check=True)` + `safe_int` / `safe_float` / `safe_str` thin wrapper
  - [ ] SubTask 16.2: `core/web_state.py:135-142 _to_float` 改为 `from converters._common import safe_float`（或保留 `_to_float` 作为 thin wrapper 返回 Optional[float]）
  - [ ] SubTask 16.3: `core/execution_module.py:875-880 _cast_int` / `_cast_str` 改为 thin wrapper 调用 `safe_int` / `safe_str`
  - [ ] SubTask 16.4: 消除 ~12 处 `try: float(X) except (TypeError, ValueError): default` 内联样板（runtime_mode/execution/web_state/formula/screening/monitoring 6 模块）改为调用 `safe_float`
  - [ ] SubTask 16.5: Grep `def _safe_int\b|def _safe_float\b|def _to_float\b|def _cast_int\b|def _cast_str\b` 在 core/*.py = 0（仅 converters/_common.py 内允许）

### 架构工程师任务（阶段 3c：table_engine.py 热加载三件套统一）

- [ ] Task 17: 变更 C5 — 引入 `ConfigStoreBase` 基类合并热加载三件套
  - [ ] SubTask 17.1: 在 `core/table_engine.py` 定义 `ConfigStoreBase` 基类，含 `check_and_reload()` 模板方法（10 步骨架：iter files → md5 → skip → json.loads → 3 层校验 → swap → record_config_version → log）
  - [ ] SubTask 17.2: 定义抽象 hook `_validate(name, data) -> List[ValidationResult]` 与 `_commit(name, data) -> None`
  - [ ] SubTask 17.3: `ConfigStore.check_changes` 改为继承 `ConfigStoreBase`，override `_validate` 调用 `self._schema_validator.validate_X` × 3，`_commit` 更新 `self._tables`
  - [ ] SubTask 17.4: `ConfigStoreHotReloadManager.check_and_reload` 改为继承 `ConfigStoreBase`，override `_validate` 调用 `self.validate_and_swap`，`_commit` 更新 `self._config_store._tables`
  - [ ] SubTask 17.5: 合并 `rollback()` 双实现为 `ConfigStoreBase.rollback(version_id)` 模板方法，子类仅 override `_reload_all()` hook
  - [ ] SubTask 17.6: 合并 3 层校验调用骨架（479-500 与 1576-1590）为 `_validate_three_tiers(name, data) -> (passed, errors)` helper
  - [ ] SubTask 17.7: Grep `def check_changes\b|def check_and_reload\b|def rollback\b` 在 table_engine.py ≤ 3（仅基类 + 必要 thin override）
  - [ ] SubTask 17.8: 验证热加载与回滚功能与原逻辑一致

### 架构工程师任务（阶段 3d：monitoring_module.py 表驱动收敛）

- [ ] Task 18: 变更 C6 — 24 个 `_adapter_X` 改为 `_ADAPTER_SPECS` 声明式表 + 通用 builder
  - [ ] SubTask 18.1: 在 `core/monitoring_module.py` 定义 `_ADAPTER_SPECS: Dict[str, Dict[str, List[str]]]` 表，含 24 个 key（TickReceived / DataChanged / BarComposed / FormulaEvaluated / StockFiltered / TimeAdvanced / SnapshotUpdated / Executed / DomainEvent / PoolLoaded / ConfigLoaded / ConfigChanged / TransferExecuted / TTLExpired / OrderPlaced / OrderFilled / AlertRaised / PositionUpdated / StatisticsUpdated / RankingChanged / EdgeFired / Signal / CrossoverDetected / ModeChanged），每个 spec 含 `top_fields` 与 `details_fields` 列表
  - [ ] SubTask 18.2: 定义 `_build_adapter_record(spec_key: str, event) -> Dict[str, Any]` 通用 builder，按 spec 提取字段构建 `{"event_type", "details", ...}` dict
  - [ ] SubTask 18.3: `EVENT_RECORD_ADAPTERS` 表的 value 从函数引用改为 spec key 字符串，分派点调用 `_build_adapter_record(spec_key, event)`
  - [ ] SubTask 18.4: 删除 24 个 `_adapter_X` 函数定义
  - [ ] SubTask 18.5: Grep `def _adapter_\w+\b` 在 monitoring_module.py = 0
  - [ ] SubTask 18.6: 验证 24 个事件类型的 adapter record 与原逻辑一致

- [ ] Task 19: 变更 C7 — 合并 `compute_pk_ranking` / `compute_analysis_angles` 与 `publish_rankings` 表驱动
  - [ ] SubTask 19.1: 定义 `_RANKING_SPECS = [(cfg_attr, store_attr, sort_key, builder, dimension, label), ...]` 表
  - [ ] SubTask 19.2: `compute_pk_ranking` / `compute_analysis_angles` 改为 thin wrapper 调用 `_compute_ranking(spec)`
  - [ ] SubTask 19.3: `publish_rankings` 改为迭代 `_RANKING_SPECS` 表调用 `_compute_ranking` + `bus.publish(RankingChanged)`
  - [ ] SubTask 19.4: Grep `def compute_pk_ranking\b|def compute_analysis_angles\b` 在 monitoring_module.py = 0（或方法体 ≤ 3 行 thin wrapper）

### 架构工程师任务（阶段 3e：execution_module.py 表驱动收敛）

- [ ] Task 20: 变更 C8 — 合并 `_compile_X_spec` 3 函数 + `_make_X_action` 3 函数 + `XStep` 5 类 + `_gate_before/after_X` 4 函数
  - [ ] SubTask 20.1: 定义 `_compile_spec(params, fields_table)` helper + 3 个 `_X_SPEC_FIELDS` 表 decl，`_compile_timing_spec` / `_compile_filter_spec` / `_compile_propagate_spec` 改为 1 行委托
  - [ ] SubTask 20.2: 定义 `_make_publishing_action(state, bus, event_factory, pre_check=None)` helper，`_make_edge_action` / `_make_ttl_interval_action` / `_make_ttl_endtime_action` 改为 1 行委托
  - [ ] SubTask 20.3: 提取 `Step` 基类（含 `__init__(self, executor)` + abstract `run(self, ctx)`），`GateStep` / `FilterStep` / `PropagateStep` / `TTLStep` / `CallbackStep` 继承
  - [ ] SubTask 20.4: 定义 `_gate_window(anchor, offset, now_sec, before=True)` helper，`_gate_before_open` / `_gate_after_open` / `_gate_before_close` / `_gate_after_close` 改为 1 行委托
  - [ ] SubTask 20.5: 删除 `_publish_edge_fired` / `_publish_ttl_due` 1 行薄包装，调用方直接用 `_publish(bus, Event(...))`
  - [ ] SubTask 20.6: Grep `def _compile_timing_spec\b|def _compile_filter_spec\b|def _compile_propagate_spec\b` 在 execution_module.py 仅匹配 1 行委托
  - [ ] SubTask 20.7: Grep `def _make_edge_action\b|def _make_ttl_interval_action\b|def _make_ttl_endtime_action\b` 在 execution_module.py 仅匹配 1 行委托
  - [ ] SubTask 20.8: Grep `def _gate_before_open\b|def _gate_after_open\b|def _gate_before_close\b|def _gate_after_close\b` 在 execution_module.py 仅匹配 1 行委托

### 架构工程师任务（阶段 3f：trade_module.py 表驱动收敛）

- [ ] Task 21: 变更 C9 — 合并 `_execute_buy` / `_execute_sell` 与 `_apply_psatt_side_effects` 表驱动
  - [ ] SubTask 21.1: 定义 `_SIDE_SPECS = {"BUY": {position_update_fn, cash_update_fn, trade_action}, "SELL": {...}}` 表
  - [ ] SubTask 21.2: `_execute_buy` / `_execute_sell` 改为 `_execute_trade(side, signal, ...)` 单一方法查表分派
  - [ ] SubTask 21.3: `_PaperTradeEngine.buy` / `sell` 改为 `_paper_trade(side, signal, ...)` 单一方法查表分派
  - [ ] SubTask 21.4: 定义 `_PSATT_SIDE_EFFECTS = [(flag_attr, event_kind, payload_builder), ...]` 5 条表
  - [ ] SubTask 21.5: `_apply_psatt_side_effects` 5 if 分支改为迭代 `_PSATT_SIDE_EFFECTS` 表
  - [ ] SubTask 21.6: Grep `def _execute_buy\b|def _execute_sell\b` 在 trade_module.py = 0
  - [ ] SubTask 21.7: Grep `if action_spec\.bsavehis|if action_spec\.bsound|if action_spec\.btip|if action_spec\.bsavetoblock|if action_spec\.baimpool` 在 trade_module.py = 0

### 架构工程师任务（阶段 3g：runtime_mode_module.py 表驱动收敛）

- [ ] Task 22: 变更 C10 — 合并 OHLCV 字面量 + 日期函数 + replay 控制状态
  - [ ] SubTask 22.1: 提取 `_aggregate_ohlcv(group)` helper，`_aggregate_bars` / `_group_and_synthesize` 共用
  - [ ] SubTask 22.2: 定义 `_DATE_KEYS = {"day": "%Y-%m-%d", "month": "%Y-%m"}` + week lambda，`_get_week_key` / `_get_month_key` / `_day_key` 改为查表
  - [ ] SubTask 22.3: 定义 `_status(status, **extra)` helper，6 个 replay 控制方法改为 1 行委托
  - [ ] SubTask 22.4: Grep `"open": group\[0\]\["open"\]` 在 runtime_mode_module.py 仅匹配 `_aggregate_ohlcv` 1 处
  - [ ] SubTask 22.5: Grep `def _get_week_key\b|def _get_month_key\b|def _day_key\b` 在 runtime_mode_module.py = 0（或方法体 ≤ 2 行查表）

### 架构工程师任务（阶段 3h：跨模块 _register_subscribers 表驱动）

- [ ] Task 23: 变更 C11 — 引入 `_SUBSCRIPTIONS` 类属性表 + `_BaseModule.register_subscribers` 基类方法
  - [ ] SubTask 23.1: 在 `core/event_bus.py` 定义 `_BaseModule` 基类，含 `_SUBSCRIPTIONS: ClassVar[List[Tuple[Type[Event], str]]] = []` 类属性 + `register_subscribers(self)` 方法（单一循环 `for evt, handler_name in self._SUBSCRIPTIONS: self._bus.subscribe(evt, getattr(self, handler_name))`）
  - [ ] SubTask 23.2: runtime_mode_module / execution_module / formula_module / trade_module / tick_bar_module / screening_module / monitoring_module 7 模块的 `_register_subscribers` 改为类级 `_SUBSCRIPTIONS` 表 + 继承 `_BaseModule`
  - [ ] SubTask 23.3: 合并 `_on_pool_loaded` 3 模块共享骨架为 `_extract_from_pool_config(event, key, field_extractor)` helper
  - [ ] SubTask 23.4: 合并 `_on_mode_changed` 2 模块共享 `prev → new → log` 骨架为 `_log_mode_change(prev, new, label)` helper
  - [ ] SubTask 23.5: Grep `def _register_subscribers\b` 在 7 模块 = 0（仅 _BaseModule 内允许）
  - [ ] SubTask 23.6: Grep `self\._bus\.subscribe\(EventType, self\._on_` 在 7 模块 = 0（仅 _BaseModule.register_subscribers 内允许）

### 架构工程师任务（阶段 3i：get_table 防御性调用统一）

- [ ] Task 24: 变更 C12 — 消除 `get_global_config_store().get_table("X") if get_global_config_store() else {}` 14 处双调用
  - [ ] SubTask 24.1: 在 `core/_common.py` 或 `core/event_bus.py._BaseModule` 定义 `_get_table(name) -> dict` helper（统一 `cs = get_global_config_store(); return cs.get_table(name) if cs else {}`）
  - [ ] SubTask 24.2: `execution_module.py` 11 处改为 `self._get_table("X")` 或 `_get_table("X")`
  - [ ] SubTask 24.3: `trade_module.py` 2 处改为 `_get_table("X")`
  - [ ] SubTask 24.4: `monitoring_module.py` 内部调用改为统一 `_get_table("X")`
  - [ ] SubTask 24.5: Grep `get_global_config_store\(\)\.get_table.*\n.*if get_global_config_store` 在 core/*.py = 0（双调用 perf smell 消除）

### 评审工程师任务（阶段 3 验证）

- [ ] Task 25: 阶段 3 Grep + 回归验证
  - [ ] SubTask 25.1: Grep `def to_dict\b|def from_dict\b` 在 domain.py 仅匹配 `_FieldedBase._common_to_dict/_common_from_dict` + 4 个 1 行薄包装
  - [ ] SubTask 25.2: Grep `def from_dict\b` 在 schemas.py 仅匹配 `_DictConstructible.from_dict` 1 处
  - [ ] SubTask 25.3: Grep `def _hash_tick\b|def _hash_bar\b|def _hash_bars\b|def _hash_object\b|def _hash_code_bars\b|def _hash_tick_data\b|def _hash_period_bars\b` 在 core/*.py ≤ 8（仅 _hashing.py 内核心实现）
  - [ ] SubTask 25.4: Grep `def check_changes\b|def check_and_reload\b|def rollback\b` 在 table_engine.py ≤ 3
  - [ ] SubTask 25.5: Grep `def _adapter_\w+\b` 在 monitoring_module.py = 0
  - [ ] SubTask 25.6: Grep `def _execute_buy\b|def _execute_sell\b|def _paper_trade\b` 在 trade_module.py 仅匹配 `_execute_trade` + `_paper_trade` 单一实现
  - [ ] SubTask 25.7: Grep `def _register_subscribers\b` 在 7 模块 = 0
  - [ ] SubTask 25.8: 运行 `python -m pytest metatest/test_positive_*.py -x` 确认无回归
  - [ ] SubTask 25.9: 核心模块总行数较阶段 3 基线净减 ≥ 720 行

## 阶段 4：metatest v4 重建（严格正反合 + 16 维量化评分 + 三原语收敛度）

### 架构工程师任务

- [ ] Task 26: 升级 `scoring.py` v4（16 维量化评分引擎 + 三原语收敛度）
  - [ ] SubTask 26.1: `DIMENSIONS` 从 12 维升级为 16 维：v3 12 维（降权）+ `oop_inheritance_depth` 8% + `polling_zero_tolerance` 8% + `primitive_convergence` 8% + `essence_ratio` 4%（权重和 = 100%）
  - [ ] SubTask 26.2: 新增 `_score_oop_inheritance_depth`：(a) `BasePoolConverter` 存在 + (b) `DzhPoolConverter` / `TdxPoolConverter` 继承 + (c) 公共方法（`_parse_element` / `_add_element` / `_decode_pos` / `_decode_xml_bytes`）在基类 + (d) 子类仅含差异方法
  - [ ] SubTask 26.3: 新增 `_score_polling_zero_tolerance`：(a) 12 处轮询模式 Grep 零匹配 + (b) EventDriver heapq 调度验证 + (c) 前端 setInterval fetch 零匹配
  - [ ] SubTask 26.4: 新增 `_score_primitive_convergence`：(a) 时间原语覆盖率（EventDriver/Queue/watchdog 触发数 / 总时间触发数）+ (b) 分派原语覆盖率（表驱动分派数 / (表驱动 + if/elif + 同构函数)）+ (c) 继承原语覆盖率（基类公共方法数 / (基类 + 子类同构方法总数)），三原语均 ≥ 95% 满分
  - [ ] SubTask 26.5: 新增 `_score_essence_ratio`：净减行数 / 变更前行数 × 100，目标 ≥ 12%，净增 = 0 且触发 redo（强制「合并非拆分」硬约束）
  - [ ] SubTask 26.6: `ISOMORPHISM_CHECKS_TOTAL` 从 15 扩展到 40（v3 15 + 阶段 1 25 + 阶段 3 核心取 25）
  - [ ] SubTask 26.7: `line_convergence` 目标从 23,000 调整为 22,500
  - [ ] SubTask 26.8: 验证所有维度评分由 test_results 字段计算，无硬编码信用分

- [ ] Task 27: 升级 `runner.py` v4（真实测试结果采集 + 三原语覆盖率）
  - [ ] SubTask 27.1: 采集 `BasePoolConverter` 存在性 + 子类继承关系 + 公共方法位置，填入 test_results["oop_inheritance"]
  - [ ] SubTask 27.2: 采集 12 处轮询模式 Grep 结果 + EventDriver heapq 调度验证 + 前端 setInterval fetch 检查，填入 test_results["polling_violations"]
  - [ ] SubTask 27.3: 采集 40 项同构检查 Grep 结果，填入 test_results["isomorphism_violations"] / ["isomorphism_total_checks"]=40
  - [ ] SubTask 27.4: 采集核心模块总行数（`wc -l core/*.py`），目标 ≤ 22,500
  - [ ] SubTask 27.5: 采集三原语覆盖率：(a) 时间原语（grep EventDriver.add_spec / asyncio.Queue / watchdog 触发数 vs grep while+sleep 残留数）+ (b) 分派原语（grep _ADAPTER_SPECS / _SIDE_SPECS / _SUBSCRIPTIONS 等表数 vs grep def _adapter_X / def _execute_buy 等同构残留数）+ (c) 继承原语（基类公共方法数 vs 子类同构方法数），填入 test_results["primitive_convergence"]
  - [ ] SubTask 27.6: 采集 essence_ratio：净减行数（基线 - 当前） / 变更前行数（基线），填入 test_results["essence_ratio"]
  - [ ] SubTask 27.7: 采集运行时三核 Dispatcher 唯一性 + meta_purity（第四层洞察根因）：(a) Grep `while\s+True|while\s+self\._\w+\s*[:)]` 自造事件循环残留 + (b) Grep `time\.sleep|asyncio\.sleep\(\d` 自造时间调度残留 + (c) Grep `get_global_config_store\(\)\.get_table.*\n.*if get_global_config_store` 绕过 ConfigStore 残留 + (d) meta_purity = (Data 声明行数 + 三核 Dispatcher 调用行数) / 总业务行数，填入 test_results["meta_unification"]，目标 meta_purity ≥ 90%
  - [ ] SubTask 27.8: 输出 `metatest/report.json` 含 16 维明细 + 总分 + PASS/FAIL + redo_list + meta_unification 根因解释层

- [ ] Task 28: 重写正测试覆盖 OOP + 事件驱动 + 第二轮同构 + 三原语
  - [ ] SubTask 28.1: 新增 `test_positive_oop_inheritance.py` — 验证 `BasePoolConverter` + `DzhPoolConverter` / `TdxPoolConverter` 继承结构 + 公共方法在基类 + 子类仅含差异
  - [ ] SubTask 28.2: 新增 `test_positive_event_driven.py` — 验证 replay/simulation 步进由 EventDriver heapq 调度 + SSE 流 asyncio.Queue + watchdog 文件监视
  - [ ] SubTask 28.3: 新增 `test_positive_dzh_tdx_isomorphism.py` — 验证 25 组 DZH/TDX 同构合并后的回归（双格式导入导出保真 + 类型映射一致性 + converter registry 路由）
  - [ ] SubTask 28.4: 新增 `test_positive_core_isomorphism_v2.py` — 验证 30+ 组 core 第二轮同构合并后的回归（_FieldedBase / _hashing / _ADAPTER_SPECS / _SUBSCRIPTIONS / _execute_trade / _PSATT_SIDE_EFFECTS 等）
  - [ ] SubTask 28.5: 新增 `test_positive_primitive_convergence.py` — 验证三原语覆盖率（时间原语 / 分派原语 / 继承原语各 ≥ 95%）
  - [ ] SubTask 28.6: 升级原有 24 个 `test_positive_*.py` 含本次合并的回归断言
  - [ ] SubTask 28.7: 断言密度 ≥ 20/文件

- [ ] Task 29: 重写反测试覆盖轮询零容忍 + DZH/TDX 同构复活 + OOP 结构违规 + 拆分检测
  - [ ] SubTask 29.1: 新增 `test_negative_polling.py` — Grep `time\.sleep` / `asyncio\.sleep.*\n.*while` / `setInterval.*fetch` / `start_polling` / `_file_watcher_loop` / `_sync_play_loop` / `_sync_sim_loop` / `auto_step_loop` 在非测试代码零匹配（≥ 12 用例）
  - [ ] SubTask 29.2: 新增 `test_negative_dzh_tdx_revival.py` — Grep 25 组旧同构代码（`_parse_func_element` / `_add_func` / `_DZH_TO_TDX_TYPE` 等）零匹配
  - [ ] SubTask 29.3: 新增 `test_negative_oop_violation.py` — 验证 DZH/TDX 子类未重新引入同构方法 + 公共方法未在子类覆盖
  - [ ] SubTask 29.4: 新增 `test_negative_split_detection.py` — 验证每处变更净减行数 > 0（净增计 redo）+ 新建文件仅允许 converters/_common.py 与 core/_hashing.py + 抽象基类/mixin 伴随 ≥ 2 处子类收敛
  - [ ] SubTask 29.5: 升级原有 4 类反测试（无效配置/运行时异常/API前端/底层逻辑违规）含本次新增 30+ 组模式
  - [ ] SubTask 29.6: 每类 ≥ 8 用例

- [ ] Task 30: 重写合测试覆盖 DZH↔TDX 端到端 + 事件链路无 sleep + 表驱动 + 三原语
  - [ ] SubTask 30.1: 新增 `test_synthesis_dzh_tdx_roundtrip.py` — DZH↔TDX 双格式互转保真（DZH→TDX→DZH 与 TDX→DZH→TDX 往返一致）
  - [ ] SubTask 30.2: 新增 `test_synthesis_event_driven_no_sleep.py` — 验证事件链路无 `time.sleep` / `asyncio.sleep` 步进，全由 EventDriver heapq 调度
  - [ ] SubTask 30.3: 新增 `test_synthesis_table_driven_dispatch.py` — 验证所有分派表（`_CONVERTER_REGISTRY` / `_ADAPTER_SPECS` / `_SUBSCRIPTIONS` / `_SIDE_SPECS` / `_PSATT_SIDE_EFFECTS` / `_RANKING_SPECS` 等）存在且非空
  - [ ] SubTask 30.4: 新增 `test_synthesis_primitive_convergence.py` — 验证三原语覆盖率（时间原语 / 分派原语 / 继承原语各 ≥ 95%）+ 三原语不变量 Grep 零违规
  - [ ] SubTask 30.5: 升级原有 8 个 `test_synthesis_*.py` 含本次合并的端到端验证
  - [ ] SubTask 30.6: 前端 E2E 通过 Playwright 真实浏览器验证（环境缺失计失败）

- [ ] Task 31: 更新 `metatest/README.md` 为 v4
  - [ ] SubTask 31.1: 文档化 16 维评分规则与权重
  - [ ] SubTask 31.2: 文档化 40 项同构检查清单
  - [ ] SubTask 31.3: 文档化 OOP 同源继承验证规则
  - [ ] SubTask 31.4: 文档化轮询零容忍验证规则
  - [ ] SubTask 31.5: 文档化三原语收敛度验证规则（时间/分派/继承原语覆盖率）
  - [ ] SubTask 31.6: 文档化「合并非拆分」硬约束（essence_ratio + 拆分检测）
  - [ ] SubTask 31.7: 文档化正反合三层方法论

### 评审工程师任务（阶段 4 验证）

- [ ] Task 32: metatest v4 量化评分验证
  - [ ] SubTask 32.1: 运行 `python -m metatest.runner`，验证 16 维评分输出完整
  - [ ] SubTask 32.2: 验证 report.json 含 16 维明细 + 总分 + PASS/FAIL + redo_list + meta_unification 根因解释层
  - [ ] SubTask 32.3: 验证 `oop_inheritance_depth` 维度按真实 BasePoolConverter 存在性 + 继承关系计算
  - [ ] SubTask 32.4: 验证 `polling_zero_tolerance` 维度按真实 12 处轮询 Grep 计算
  - [ ] SubTask 32.5: 验证 `primitive_convergence` 维度按真实三原语覆盖率计算（时间/分派/继承各 ≥ 95%）
  - [ ] SubTask 32.6: 验证 `essence_ratio` 维度按真实净减行数计算（净增 = 0 且 redo）
  - [ ] SubTask 32.7: 验证同构检查 40 项（非 15 项）
  - [ ] SubTask 32.8: 验证 line_convergence 目标 22,500
  - [ ] SubTask 32.9: 验证无任何维度存在硬编码信用分
  - [ ] SubTask 32.10: 验证运行时三核 Dispatcher 唯一性（EventBus / EventDriver / ConfigStore）：Grep `while\s+True|while\s+self\._\w+\s*[:)]` + `time\.sleep|asyncio\.sleep\(\d` + `get_global_config_store\(\)\.get_table.*\n.*if get_global_config_store` + `def _safe_int\b|def _adapter_\w+\b|def _execute_buy\b|def _compile_\w+_spec\b` 在 core/*.py + services/*.py + app.py 非测试代码 = 0
  - [ ] SubTask 32.11: 验证 meta_purity ≥ 90%（(Data 声明行数 + 三核 Dispatcher 调用行数) / 总业务行数），且三原语覆盖率 ≥ 95% 与 meta_purity ≥ 90% 根因一致性成立
  - [ ] SubTask 32.12: 验证无再造第四核 Dispatcher（无自造事件循环 / 自造调度器 / 自造配置加载）

## 阶段 5：文档同步与全量回归

### 架构工程师任务

- [ ] Task 33: RULES.md 新增第 101-110 条
  - [ ] SubTask 33.1: 第 101 条 — DZH/TDX OOP 同源继承，禁止重新引入 25 组同构函数（`_parse_func_element` / `_add_func` / `_DZH_TO_TDX_TYPE` 等），所有基础功能用相同代码，差异通过子类与表驱动体现
  - [ ] SubTask 33.2: 第 102 条 — DZH↔TDX 类型映射单一真相源 `config/architecture/dzh_type_map.json`，禁止重新引入并行映射表
  - [ ] SubTask 33.3: 第 103 条 — `_CONVERTER_REGISTRY` 完整 OOP 路由，禁止 api.py / app.py 绕过 registry 直接调用 `parse_dzh_xml` / `parse_tdx_xml` / `_build_tdx_xml`
  - [ ] SubTask 33.4: 第 104 条 — 公共工具函数下沉 `converters/_common.py`，禁止模块级重新定义 `_safe_int` / `_safe_float` / `_decode_formula` 等
  - [ ] SubTask 33.5: 第 105 条 — replay/simulation 步进由 EventDriver heapq + `loop.call_at` 调度，禁止 `while True + time.sleep` / `asyncio.sleep` 步进循环与 `_run` / `_sim_auto_step` / `_current_mode` 标志轮询
  - [ ] SubTask 33.6: 第 106 条 — 文件监视由 `watchdog.Observer` 事件驱动，禁止 `while _running + asyncio.sleep + 比较 mtime` 轮询
  - [ ] SubTask 33.7: 第 107 条 — SSE 流由 `asyncio.Queue + await queue.get()` 阻塞等待，禁止 `run_in_executor(drain) + asyncio.sleep(0.05)` 50ms 队列轮询
  - [ ] SubTask 33.8: 第 108 条 — 前端 setInterval 轮询禁止，所有状态更新由 SSE/WS 订阅推送
  - [ ] SubTask 33.9: 第 109 条 — `_FieldedBase` / `_ADAPTER_SPECS` / `_SUBSCRIPTIONS` / `_SIDE_SPECS` / `_PSATT_SIDE_EFFECTS` / `_RANKING_SPECS` 表驱动，禁止重新引入同构函数与 if/elif 链
  - [ ] SubTask 33.10: 第 110 条 — 哈希函数三族统一到 `core/_hashing.py`，禁止跨模块重新定义 `_hash_tick` / `_hash_bar` / `_hash_tick_data` 等同构实现

### 评审工程师任务（全量回归）

- [ ] Task 34: 全量回归验证
  - [ ] SubTask 34.1: 运行 `python -m pytest metatest/ -x` 全量测试通过（含正反合）
  - [ ] SubTask 34.2: 运行 `python -m metatest.runner` 总分 ≥ 95 且 16 维均 ≥ 80 判定 PASS
  - [ ] SubTask 34.3: 运行 eventtest 全部通过（退出码 0）
  - [ ] SubTask 34.4: 核心模块总行数 ≤ 22,500，line_convergence 维度满分
  - [ ] SubTask 34.5: Grep RULES 101-110 对应 10 条违规模式，rule_compliance 维度满分
  - [ ] SubTask 34.6: Grep 40 项同构检查 0 违规，isomorphism_elimination 维度满分
  - [ ] SubTask 34.7: Grep 12 处轮询模式 0 匹配，polling_zero_tolerance 维度满分
  - [ ] SubTask 34.8: 验证 `BasePoolConverter` + `DzhPoolConverter` / `TdxPoolConverter` 继承结构正确，oop_inheritance_depth 维度满分
  - [ ] SubTask 34.9: 验证三原语覆盖率（时间/分派/继承各 ≥ 95%），primitive_convergence 维度满分
  - [ ] SubTask 34.10: 验证 essence_ratio ≥ 12%（净减行数 / 变更前行数），essence_ratio 维度满分
  - [ ] SubTask 34.11: 验证无任何变更净增行数（拆分检测反测试通过）
  - [ ] SubTask 34.12: 启动 replay 验证步进由 EventDriver heapq 调度，事件链完整
  - [ ] SubTask 34.13: 启动 simulation 验证 auto-step 由 EventDriver heapq 调度
  - [ ] SubTask 34.14: DZH↔TDX 双格式互转保真验证
  - [ ] SubTask 34.15: 三模式（仿真/回放/实盘）切换后事件链路正常

# Task Dependencies

- 阶段 1（Task 1-4）相互独立可并行：Task 1 改 converters.py（25 组同构合并）、Task 2 改 dzh_type_map.json + schemas.py + converters.py（类型映射统一，与 Task 1 共改 converters.py 但改不同位置，需顺序：Task 1 → Task 2）、Task 3 改 api.py + app.py + import_export_module.py（registry 强制，依赖 Task 1 完成）、Task 4 改 converters/_common.py + 多模块（公共工具下沉，与 Task 1 共改 converters.py，需顺序：Task 1 → Task 4）
- 阶段 2（Task 6-11）相互独立可并行：Task 6/7 改 runtime_mode_module.py（replay/sim 步进，需顺序：Task 6 → Task 7 共改同文件）、Task 8 改 table_engine.py + services/data.py（文件监视）、Task 9 改 app.py（SSE 流）、Task 10 改 services/data.py（周期刷新，与 Task 8 共改 services/data.py 需顺序：Task 8 → Task 10）、Task 11 改 web/js/*.js（前端订阅）
- 阶段 3（Task 13-24）依赖阶段 1-2 完成：Task 13/14 改 domain.py + schemas.py、Task 15 改 _hashing.py + 多模块、Task 16 改 _common.py + 多模块（与 Task 4 协同）、Task 17 改 table_engine.py、Task 18/19 改 monitoring_module.py、Task 20 改 execution_module.py、Task 21 改 trade_module.py、Task 22 改 runtime_mode_module.py、Task 23 改 event_bus.py + 7 模块（跨模块需最后统一）、Task 24 改多模块 get_table helper
- 阶段 4（Task 26-31）依赖阶段 1-3 全部完成（测试需验证已合并的代码）
- 阶段 5（Task 33-34）依赖阶段 4 完成
- 评审任务（Task 5/12/25/32/34）分别依赖对应阶段实施完成

## 并行化建议

- 第一波并行：Task 1（converters.py 25 组同构）→ Task 2（dzh_type_map）+ Task 4（_common.py）→ Task 3（registry 强制）；同时 Task 6/7（runtime_mode replay/sim）+ Task 8（table_engine/services watchdog）+ Task 9（app.py SSE）+ Task 11（前端 SSE/WS）
- 第二波并行：Task 8 → Task 10（services/data.py 周期刷新）；同时阶段 3a（Task 13/14 domain+schemas）+ 阶段 3b（Task 15/16 _hashing+safe_cast）+ 阶段 3c（Task 17 table_engine）+ 阶段 3d（Task 18/19 monitoring）+ 阶段 3e（Task 20 execution）+ 阶段 3f（Task 21 trade）+ 阶段 3g（Task 22 runtime_mode）+ 阶段 3i（Task 24 get_table），Task 23（_SUBSCRIPTIONS 跨 7 模块）最后统一
- 第三波并行：Task 26（scoring）+ Task 27（runner）先行，Task 28/29/30（测试文件）并行编写，Task 31（README）最后
