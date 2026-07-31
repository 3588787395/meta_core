# Tasks

本规范按「架构工程师（实施）→ 评审工程师（验证）」迭代流程分 7 阶段实施，覆盖 v10 审计盲区闭合的全部变更。**第十一层洞察（审计盲区是收敛上限的最大敌人）**：v10 的 metatest 轮询零容忍检查只 grep `core/runtime_mode_module.py` / `core/table_engine.py` / `services/data.py` 三个文件，从未覆盖 `converters.py`，导致 `DZHPoolExecutor` 这个 ~400 行活体平行运行时（`threading.Thread + while + wait(1) + time.time()` 轮询）漏网。v11 闭合此盲区，消除平行运行时，删除死代码，统一同构函数，将 TDX 领域知识归入 `TdxPoolConverter` 子类。**净代码收益**：约 −500 ~ −560 行（DZHPoolExecutor 轮询基础设施 −100 + DzhXmlExporter 死代码 −328 + decode_tdx_action_hex 重复 −56 + 同构函数合并 −36）。

## 阶段 1：DZHPoolExecutor 平行运行时消除（核心，最大单项收益）

### 架构工程师任务

- [x] Task 1: 变更 D1 — 删除 DZHPoolExecutor 轮询基础设施
  - [x] SubTask 1.1: Read `converters.py:2910-3010` 确认 DZHPoolExecutor `__init__` 结构（_timers/_thread/_stop_event/_events/_edge_last_trigger/_start_time 六个轮询/线程/事件日志字段）
  - [x] SubTask 1.2: Read `converters.py:3180-3245` 确认 `_run_loop`（3183-3210）/ `start`（3212-3224）/ `stop`（3226-3240）三个轮询方法体
  - [x] SubTask 1.3: Read `converters.py:2951-2958` 确认 `_log_event` 方法（私有事件日志绕过 EventBus）
  - [x] SubTask 1.4: 删除 `_run_loop` / `start` / `stop` / `_log_event` 四个方法
  - [x] SubTask 1.5: 删除 `__init__` 中的 `_timers` / `_thread` / `_stop_event` / `_events` / `_edge_last_trigger` / `_start_time` / `running` 七个字段赋值
  - [x] SubTask 1.6: 保留 `execute_once()` / `_init_nodes_edges` / `_init_mock_stocks` / `NodeStateMachine`（execute_once 依赖）
  - [x] SubTask 1.7: Grep `execute_once` 在 converters.py 确认保留方法仍完整
  - [x] SubTask 1.8: Grep `_run_loop|_stop_event|_timers|_thread|_events|_log_event|_edge_last_trigger|_start_time|self.running` 在 converters.py DZHPoolExecutor 类内零匹配
  - [x] SubTask 1.9: `python -c "from converters import DZHPoolExecutor; e=DZHPoolExecutor({'nodes':[],'edges':[]}); print('execute_once OK' if hasattr(e,'execute_once') else 'MISSING')"` 验证 execute_once 仍可用

- [x] Task 2: 变更 D2 — /pool/start 端点改为委托 PoolEngine
  - [x] SubTask 2.1: Read `api.py:6377-6403` 确认 `start_pool` 端点当前实现（实例化 DZHPoolExecutor + executor.start() + 存入 _dzh_executors）
  - [x] SubTask 2.2: Read `api.py:6405-6430` 确认 `stop_pool` 端点当前实现（从 _dzh_executors 取 executor + executor.stop()）
  - [x] SubTask 2.3: Read `core/engine.py` 确认 PoolEngine 是否有 `run_loop()` / `stop_loop()` 方法（若无则需新增或调既有 start/stop 方法）
  - [x] SubTask 2.4: 修改 `start_pool`：删除 `from converters import DZHPoolExecutor` + `executor = DZHPoolExecutor(config)` + `executor._init_mock_stocks()` + `executor.start()`，改为 `engine = request.app.state.engine; engine.load_pool(config); engine.start_loop()`（或调既有等价方法）
  - [x] SubTask 2.5: 修改 `stop_pool`：删除从 `_dzh_executors` 取 executor 逻辑，改为 `engine = request.app.state.engine; engine.stop_loop()`
  - [x] SubTask 2.6: 删除 `request.app.state._dzh_executors` 字典初始化与访问
  - [x] SubTask 2.7: Grep `_dzh_executors` 在 api.py 零匹配
  - [x] SubTask 2.8: Grep `DZHPoolExecutor` 在 api.py /pool/start 与 /pool/stop 端点零匹配

- [x] Task 3: 变更 D3+D4 — /pool/run 委托 PoolEngine + 删除死导入
  - [x] SubTask 3.1: Read `api.py:6355-6375` 确认 `run_pool` 端点当前实现（DZHPoolExecutor(config).execute_once()）
  - [x] SubTask 3.2: 修改 `run_pool`：删除 `from converters import DZHPoolExecutor` + `executor = DZHPoolExecutor(config)` + `result = executor.execute_once()`，改为 `engine = request.app.state.engine; result = engine.execute_pool(config)`（api.py:5403 已在用此方法）
  - [x] SubTask 3.3: 调整返回字段映射：`execute_pool` 返回值与 `execute_once` 返回值的字段对齐（output_count/output_stocks/events/node_states）
  - [x] SubTask 3.4: 删除 `api.py:5400` 的 `from converters import DZHPoolExecutor` 死导入（紧跟 `engine = request.app.state.engine` + `engine.execute_pool(parsed)`，DZHPoolExecutor 导入未使用）
  - [x] SubTask 3.5: 删除 `api.py:5470` 的 `from converters import DZHPoolExecutor` 死导入
  - [x] SubTask 3.6: Grep `from converters import DZHPoolExecutor` 在 api.py 零匹配
  - [x] SubTask 3.7: Grep `DZHPoolExecutor` 在 api.py 仅命中注释或零匹配

- [x] Task 4: 变更 D5 — __init__.py 导出清理
  - [x] SubTask 4.1: Read `__init__.py:25-50` 确认 DZHPoolExporter 导出结构（line 28 try import / line 30 except None / line 47 __all__）
  - [x] SubTask 4.2: 删除 `__init__.py:28` `from .converters import DZHPoolExecutor`
  - [x] SubTask 4.3: 删除 `__init__.py:30` `DZHPoolExecutor = None`
  - [x] SubTask 4.4: 删除 `__init__.py:47` `__all__` 中的 `"DZHPoolExecutor"`
  - [x] SubTask 4.5: Grep `DZHPoolExecutor` 在 __init__.py 零匹配
  - [x] SubTask 4.6: `python -c "import workspace; print('import OK')"` 验证包导入无错

- [x] Task 5: 阶段 1 验证
  - [x] SubTask 5.1: `python -c "from converters import DZHPoolExecutor; e=DZHPoolExecutor({'nodes':[],'edges':[]}); print(hasattr(e,'execute_once'), not hasattr(e,'_run_loop'), not hasattr(e,'start'))"` 输出 `True True True`
  - [x] SubTask 5.2: Grep `while.*wait\(1\)|time\.time\(\).*interval` 在 converters.py 零匹配
  - [x] SubTask 5.3: Grep `threading\.Thread|threading\.Timer|threading\.Event` 在 converters.py 零匹配（import 行除外）
  - [x] SubTask 5.4: `python -m pytest metatest/ -x -q --ignore=metatest/test_positive_event_panel.py 2>&1 | tail -5` 全量测试不回归
  - [x] SubTask 5.5: `python -m eventtest.run_eventtest 2>&1 | tail -3` 退出码 0

## 阶段 2：DzhXmlExporter 死代码清除（最大行数收益）

### 架构工程师任务

- [x] Task 6: 变更 E1 — 删除 DzhXmlExporter 类
  - [x] SubTask 6.1: Read `converters.py:3982-3990` 确认 DzhXmlExporter 类定义起点（class DzhXmlExporter:）
  - [x] SubTask 6.2: Read `converters.py:4300-4315` 确认 DzhXmlExporter 类定义终点（下一个顶层定义的起点）
  - [x] SubTask 6.3: Grep `DzhXmlExporter` 全仓确认仅命中 converters.py:3982 类定义本身（零实例化、零导入、零继承）
  - [x] SubTask 6.4: 删除 `converters.py:3982-4310` 整个 DzhXmlExporter 类（~328 行）
  - [x] SubTask 6.5: Grep `DzhXmlExporter` 全仓零匹配
  - [x] SubTask 6.6: `python -c "import converters; print('import OK')"` 验证模块导入无错

## 阶段 3：decode_tdx_action_hex 表驱动统一（继承纯度 + 表驱动）

### 架构工程师任务

- [x] Task 7: 变更 F1 — 删除 core/table_engine.py 重复实现
  - [x] SubTask 7.1: Read `core/table_engine.py:842-898` 确认 `decode_tdx_action_hex` / `encode_tdx_action_hex` 两个 staticmethod（~56 行，硬编码 type_map/byte_type_map）
  - [x] SubTask 7.2: Read `converters.py:1157` 确认 `decode_action` 共享版（表驱动读 filter_action_rules.json）
  - [x] SubTask 7.3: Read `converters.py:1206` 确认 `_encode_action_raw` 共享版
  - [x] SubTask 7.4: Grep `decode_tdx_action_hex|encode_tdx_action_hex` 全仓确认调用点（应在 config/data/*.json transform_expr 字符串反射调用 + table_engine.py 定义本身）
  - [x] SubTask 7.5: 删除 `core/table_engine.py:842-898` 的 `decode_tdx_action_hex` / `encode_tdx_action_hex` 两个 staticmethod
  - [x] SubTask 7.6: Grep `decode_tdx_action_hex|encode_tdx_action_hex` 在 core/table_engine.py 零匹配

- [x] Task 8: 变更 F2 — 配置表反射调用改指共享版
  - [x] SubTask 8.1: Read `config/data/data_mappings.json:160-170` 确认 transform_expr 字段反射调用 `decode_tdx_action_hex`
  - [x] SubTask 8.2: Read `config/data/data_config.json:1725-1732` 确认 transform_expr 字段反射调用 `encode_tdx_action_hex`
  - [x] SubTask 8.3: 修改 `data_mappings.json:166` transform_expr 从 `decode_tdx_action_hex` 改为 `decode_action`
  - [x] SubTask 8.4: 修改 `data_config.json:1728` transform_expr 从 `encode_tdx_action_hex` 改为 `_encode_action_raw`
  - [x] SubTask 8.5: 若反射调用方按 pool_type 选函数（dzh vs tdx），确认 `decode_action` / `_encode_action_raw` 能正确处理 TDX 输入（或扩充 `filter_action_rules.json` 加 `tdx_high_type_map` / `tdx_byte_type_map` 子表，共享版按 pool_type 选子表）
  - [x] SubTask 8.6: Grep `decode_tdx_action_hex|encode_tdx_action_hex` 在 config/data/*.json 零匹配
  - [x] SubTask 8.7: `python -c "import json; json.load(open('config/data/data_mappings.json')); json.load(open('config/data/data_config.json')); print('JSON OK')"` 验证配置表合法

## 阶段 4：_call_converter tdx 分支消除 + TDX 自由函数归入 TdxPoolConverter（继承纯度）

### 架构工程师任务

- [x] Task 9: 变更 G1 — 消除 _call_converter tdx 分支
  - [x] SubTask 9.1: Read `core/import_export_module.py:95-115` 确认 `_call_converter` 函数完整结构（含 line 107-109 `if auto and fmt == "tdx": result = mod._tdx_pool_to_frontend(result, pool_name)` 分支）
  - [x] SubTask 9.2: Read `converters.py:168-275` 确认 `BasePoolConverter` 类结构（5 concrete + 2 template + 10 abstract hooks）
  - [x] SubTask 9.3: 在 `BasePoolConverter` 新增 `_to_frontend(self, result, name: str)` 钩子方法（默认 `return result`，非 abstract 因有默认实现）
  - [x] SubTask 9.4: 在 `TdxPoolConverter` 覆盖 `_to_frontend`：调 `_tdx_pool_to_frontend(result, name)`（吸收 line 107-109 的 TDX 后处理逻辑）
  - [x] SubTask 9.5: 修改 `_call_converter`：删除 `if auto and fmt == "tdx": ...` 分支，改为 `result = converter.parse_pool(...)` + `result = converter._to_frontend(result, name)` 统一调用
  - [x] SubTask 9.6: Grep `if.*fmt.*==.*\"tdx\"|if auto and fmt` 在 core/import_export_module.py 零匹配
  - [x] SubTask 9.7: `python -c "from core.import_export_module import _call_converter; print('import OK')"` 验证

- [x] Task 10: 变更 G2 — TDX 自由函数归入 TdxPoolConverter
  - [x] SubTask 10.1: Read `converters.py:4484-4640` 确认 `tdx_to_internal` 函数（~156 行）
  - [x] SubTask 10.2: Read `converters.py:4878-4906` 确认 `convert_tdx_to_config` 函数（~28 行）
  - [x] SubTask 10.3: Read `converters.py:5908-5972` 确认 `_tdx_pool_to_frontend` 函数（~65 行）
  - [x] SubTask 10.4: Read `converters.py:5975-5982` 确认 `_load_tdx_pool_config` 函数（~8 行）
  - [x] SubTask 10.5: Grep 这 4 个函数的调用点，确认调用方能否改为 `TdxPoolConverter.method()` 形态
  - [x] SubTask 10.6: 将 `_tdx_pool_to_frontend` 改为 `TdxPoolConverter._to_frontend` 方法体（SubTask 9.4 已用）
  - [x] SubTask 10.7: 将 `tdx_to_internal` / `convert_tdx_to_config` / `_load_tdx_pool_config` 改为 `TdxPoolConverter` 的 `@staticmethod` 或实例方法（若仅 TdxPoolConverter 内部调用），或保留为模块级但加 `_TDX_INTERNAL_` 前缀标识归属
  - [x] SubTask 10.8: Grep `^def tdx_to_internal|^def convert_tdx_to_config|^def _tdx_pool_to_frontend|^def _load_tdx_pool_config` 在 converters.py 顶层零匹配（已归入类内）
  - [x] SubTask 10.9: `python -c "from converters import TdxPoolConverter; print('import OK')"` 验证

## 阶段 5：同构函数合并（底层运行逻辑洞察）

### 架构工程师任务

- [x] Task 11: 变更 H1 — _stock_code / _scode / _extract_code 四胞胎统一
  - [x] SubTask 11.1: Read `core/domain.py:1103-1115` 确认 `_stock_code` 规范版实现
  - [x] SubTask 11.2: Read `core/runtime_mode_module.py:125-130` 确认 `_scode` 本地重复定义
  - [x] SubTask 11.3: Read `core/runtime_mode_module.py:2410-2415` 确认 `_extract_code` 本地重复定义
  - [x] SubTask 11.4: Read `core/screening_module.py:153-165` 确认 `_extract_code` 本地定义（含 `"Code"` 大写回退差异）
  - [x] SubTask 11.5: Grep `_scode\(|_extract_code\(` 在 core/runtime_mode_module.py 确认所有调用点
  - [x] SubTask 11.6: Grep `_extract_code\(` 在 core/screening_module.py 确认所有调用点
  - [x] SubTask 11.7: 删除 `core/runtime_mode_module.py:125` `_scode` 定义，调用点改用 `_stock_code`（已 import）
  - [x] SubTask 11.8: 删除 `core/runtime_mode_module.py:2410` `_extract_code` 定义，调用点改用 `_stock_code`
  - [x] SubTask 11.9: 删除 `core/screening_module.py:153` `_extract_code` 定义，调用点改用 `_stock_code`（若需 `"Code"` 大写回退，在 `_stock_code` 内补 `s.get("Code", s.get("code", ...))` 或在调用点处理）
  - [x] SubTask 11.10: Grep `^def _scode|^def _extract_code` 在 core/ 零匹配
  - [x] SubTask 11.11: `python -c "from core.runtime_mode_module import KLineReplayEngine; from core.screening_module import *; print('import OK')"` 验证

- [x] Task 12: 变更 H2 — _normalize_period 双胞胎统一
  - [x] SubTask 12.1: Read `services/data.py:50-60` 确认 `_normalize_period` 表驱动版（用 `_PERIOD_ALIASES` dict）
  - [x] SubTask 12.2: Read `app.py:248-260` 确认 `_normalize_period` 内联 mapping dict 版
  - [x] SubTask 12.3: 对比两个 mapping dict，确认 `_PERIOD_ALIASES` 需补齐的别名（app.py 多出的 `d/day` 等）
  - [x] SubTask 12.4: 扩充 `services/data.py` 的 `_PERIOD_ALIASES` 补齐 app.py 缺失的别名
  - [x] SubTask 12.5: 删除 `app.py:250` `_normalize_period` 定义，改为 `from services.data import _normalize_period`
  - [x] SubTask 12.6: Grep `^def _normalize_period` 在 app.py 零匹配
  - [x] SubTask 12.7: `python -c "from app import app; print('import OK')"` 验证

- [x] Task 13: 变更 H3 — engine.py _ce_* 死导入删除
  - [x] SubTask 13.1: Read `core/engine.py:35-50` 确认 `_ce_*` 4 行死导入（line 39-42）
  - [x] SubTask 13.2: Grep `_ce_extract_edge_endpoint|_ce_resolve_node_type|_ce_resolve_edge_type|_ce_normalize_nodes` 在 core/engine.py 确认仅命中 import 行（零使用点）
  - [x] SubTask 13.3: 删除 `core/engine.py:39-42` 4 行死导入
  - [x] SubTask 13.4: Grep `_ce_` 在 core/engine.py 零匹配
  - [x] SubTask 13.5: `python -c "from core.engine import PoolEngine; print('import OK')"` 验证

- [x] Task 14: 变更 H4 — api.py 死等价分支合并
  - [x] SubTask 14.1: Read `api.py:6160-6170` 确认第一处 `if mode == 'real': ... elif mode == 'sdk': ...`（分支体逐字相同 `tq = TqAdapter(mock_mode=False)`）
  - [x] SubTask 14.2: Read `api.py:6210-6220` 确认第二处同构分支
  - [x] SubTask 14.3: 合并第一处为 `if mode in ('real', 'sdk'): tq = TqAdapter(mock_mode=False)`
  - [x] SubTask 14.4: 合并第二处为同上
  - [x] SubTask 14.5: Grep `elif mode == 'sdk'` 在 api.py 零匹配
  - [x] SubTask 14.6: `python -c "import api; print('import OK')"` 验证

- [x] Task 15: 变更 H5 — engine.py TTL 注册双循环合并
  - [x] SubTask 15.1: Read `core/engine.py:295-320` 确认 edge TTL 循环（301-307）与 node TTL 循环（310-316）结构
  - [x] SubTask 15.2: 确认两循环仅 `register_ttl_spec` 的 owner_id / ttl_key / ttl_sec 三个参数不同，控制流完全同构
  - [x] SubTask 15.3: 在 core/engine.py 模块级或 PoolEngine 方法新增 `_register_ttl_batch(driver, state, stocks, owner_id, ttl_key, ttl_sec, now_val, bus)` 助手
  - [x] SubTask 15.4: 将 edge TTL 循环改为 `self._register_ttl_batch(driver, self.state, stocks, ec.tid, eid, edge_ttl.ttl_sec, now_val, bus)`
  - [x] SubTask 15.5: 将 node TTL 循环改为 `self._register_ttl_batch(driver, self.state, stocks, nid, f"node_ttl:{nid}", node_ttl.ttl_sec, now_val, bus)`
  - [x] SubTask 15.6: Grep `for stock in stocks:` 在 core/engine.py TTL 注册区仅命中 `_register_ttl_batch` 内部 1 处
  - [x] SubTask 15.7: `python -c "from core.engine import PoolEngine; print('import OK')"` 验证

## 阶段 6：metatest v11 量化评审升级（盲区闭合闭环）

### 架构工程师任务

- [x] Task 16: 变更 M1+M2+M3 — runner.py 新增三个采集函数
  - [x] SubTask 16.1: Read `metatest/runner.py` 确认 `_collect_polling_violations`（或同等函数）当前扫描文件列表
  - [x] SubTask 16.2: 修改 `_collect_polling_violations` 扫描文件列表，新增 `converters.py`（从 `core/runtime_mode_module.py` / `core/table_engine.py` / `services/data.py` 扩展为 4 文件）
  - [x] SubTask 16.3: 新增 `_collect_parallel_runtime_violations()` 函数：检测 `converters.py` / `services/*.py` 内 `threading.Thread` + `while` + `_stop_event.wait` 组合的平行运行时模式（AST 或 regex 检测）。返回 `{"violations": int, "files": List[str]}`
  - [x] SubTask 16.4: 新增 `_collect_dead_code_violations()` 函数：AST 解析所有 .py 文件的 class 定义，检测零实例化 / 零导入的类。返回 `{"dead_classes": List[str], "count": int}`
  - [x] SubTask 16.5: 在 `_collect_isomorphism_violations` 新增 3 项检查：converters.py 轮询非零 / 平行运行时非零 / 死代码非零 各计 1 违规
  - [x] SubTask 16.6: test_results 新增 `converters_polling_violations` / `parallel_runtime_violations` / `dead_code_violations` 三个字段
  - [x] SubTask 16.7: `python -c "from metatest.runner import _collect_parallel_runtime_violations, _collect_dead_code_violations; print(_collect_parallel_runtime_violations()); print(_collect_dead_code_violations())"` 验证两者均返回零违规

- [x] Task 17: 变更 M4+M5 — scoring.py ISOMORPHISM_CHECKS_TOTAL 扩展
  - [x] SubTask 17.1: Read `metatest/scoring.py` 确认 `ISOMORPHISM_CHECKS_TOTAL` 当前值（41）
  - [x] SubTask 17.2: 更新 `ISOMORPHISM_CHECKS_TOTAL = 44`（41 + 3 新增检查）
  - [x] SubTask 17.3: 确认 `_score_isomorphism_elimination` 使用 44 作分母
  - [x] SubTask 17.4: `python -c "from metatest.scoring import ISOMORPHISM_CHECKS_TOTAL; print(ISOMORPHISM_CHECKS_TOTAL)"` 输出 44

- [x] Task 18: 变更 M6 — 测试断言
  - [x] SubTask 18.1: 在 `metatest/test_negative_polling.py` 新增断言：`converters.py` 内零 `while + wait(N) + time.time()` 轮询模式
  - [x] SubTask 18.2: 新增 `metatest/test_positive_no_parallel_runtime.py` 或追加到既有测试：断言 `converters.py` / `services/*.py` 内零 `threading.Thread + while + _stop_event.wait` 平行运行时
  - [x] SubTask 18.3: 新增 `metatest/test_positive_no_dead_code.py` 或追加：断言全仓零死代码类（零实例化 + 零导入）
  - [x] SubTask 18.4: `python -m pytest metatest/test_negative_polling.py metatest/test_positive_no_parallel_runtime.py metatest/test_positive_no_dead_code.py -v` 退出码 0

- [x] Task 19: 变更 M7 — README.md v11 文档
  - [x] SubTask 19.1: Read `metatest/README.md` 确认 v10 文档结构
  - [x] SubTask 19.2: README.md 标题 v10 → v11，概述新增第十一层洞察说明（审计盲区是收敛上限的最大敌人）
  - [x] SubTask 19.3: README.md 新增「v10 上限范围修正」段落：v10 上限仅适用于 core/ 内部，converters.py 跨域盲区在 v11 闭合
  - [x] SubTask 19.4: README.md isomorphism_elimination 维度说明 41 项 → 44 项（新增 converters 轮询 / 平行运行时 / 死代码 3 项）

## 阶段 7：RULES 修订 + 全量回归

### 架构工程师任务

- [x] Task 20: 变更 R1 — RULES 120 修订 + 121 新增
  - [x] SubTask 20.1: Read `RULES.md:296-300` 确认 v10 第 120 条当前文本
  - [x] SubTask 20.2: 修订第 120 条：将「全局收敛上限知止」声明修正为「**审计盲区闭合后的**全局收敛上限」——v10 上限仅适用于 core/ 内部，converters.py 跨域盲区在 v11 闭合后全局上限才真正成立
  - [x] SubTask 20.3: 在第 120 条后新增第 121 条：「**禁止平行运行时**：不得在 `converters.py` / `services/*.py` 内实现 `threading.Thread + while + wait(N)` 轮询调度平行运行时；所有长时执行必须委托 `PoolEngine.run_loop()`（`asyncio.Event.wait()` + `loop.call_at` 事件驱动），所有一次性执行必须委托 `PoolEngine.execute_pool()`。运行时只有一个真相源。这是「极致本质的运行时」的第十一层洞察：审计盲区是收敛上限的最大敌人——v10 因 metatest 未覆盖 converters.py 而漏判 DZHPoolExecutor 平行运行时，v11 闭合此盲区并新增平行运行时零容忍检测。」
  - [x] SubTask 20.4: Grep `^121\.` 在 RULES.md = 1

### 评审工程师任务

- [x] Task 21: 变更 R2 — 全量回归
  - [x] SubTask 21.1: `python -m pytest metatest/ -x -q --ignore=metatest/test_positive_event_panel.py` 退出码 0（全量测试通过）
  - [x] SubTask 21.2: `python -c "from metatest.runner import _collect_handler_exception_coverage; r=_collect_handler_exception_coverage(); print(r); assert r['coverage'] == 100.0"` v10 成果不回归
  - [x] SubTask 21.3: `python -c "from metatest.runner import _collect_parallel_runtime_violations; r=_collect_parallel_runtime_violations(); print(r); assert r['violations'] == 0"` 平行运行时零违规
  - [x] SubTask 21.4: `python -c "from metatest.runner import _collect_dead_code_violations; r=_collect_dead_code_violations(); print(r); assert r['count'] == 0"` 死代码零违规
  - [x] SubTask 21.5: `python -m eventtest.run_eventtest` 退出码 0（全绿）
  - [x] SubTask 21.6: Grep `DZHPoolExecutor` 在 api.py /pool/start + /pool/stop 端点零匹配
  - [x] SubTask 21.7: Grep `DzhXmlExporter` 全仓零匹配
  - [x] SubTask 21.8: Grep `decode_tdx_action_hex|encode_tdx_action_hex` 在 core/table_engine.py 零匹配
  - [x] SubTask 21.9: Grep `if.*fmt.*==.*\"tdx\"` 在 core/import_export_module.py 零匹配
  - [x] SubTask 21.10: Grep `^def _scode|^def _extract_code` 在 core/ 零匹配
  - [x] SubTask 21.11: Grep `_ce_` 在 core/engine.py 零匹配
  - [x] SubTask 21.12: Grep `elif mode == 'sdk'` 在 api.py 零匹配
  - [x] SubTask 21.13: oop_inheritance_depth 维度 = 100（v9/v10 保持，TDX 函数归入子类后继承纯度提升）
  - [x] SubTask 21.14: isomorphism_elimination 维度 = 100（44 项 0 违规，含新增 3 项）
  - [x] SubTask 21.15: handler_exception_coverage 维度 = 100（v10 保持）
  - [x] SubTask 21.16: DZH↔TDX roundtrip 保真（19 roundtrip 测试全过）
  - [x] SubTask 21.17: essence_ratio 维度提升（净 −500 ~ −560 行）
  - [x] SubTask 21.18: adapter_isomorphism 维度 = 100（v7/v8/v9/v10 保持）
  - [x] SubTask 21.19: dispatcher_isomorphism 维度 = 100（v5/v6/v7/v8/v9/v10 保持）
  - [x] SubTask 21.20: runtime_verification 维度 = 100（v5/v6/v7/v8/v9/v10 保持）
  - [x] SubTask 21.21: eventtest_regression 维度 = 100（v5/v6/v7/v8/v9/v10 保持）

# Task Dependencies
- Task 2/3 依赖 Task 1（DZHPoolExecutor 轮询基础设施删除后才能改端点）
- Task 4 依赖 Task 1/2/3（api.py 所有 DZHPoolExecutor 引用清除后才能清 __init__.py）
- Task 5 依赖 Task 1-4（阶段 1 整体验证）
- Task 9 依赖 Task 10（TDX 函数归入 TdxPoolConverter 后才能消除 _call_converter tdx 分支）—— 或并行实施，Task 9 用 _to_frontend 钩子先行
- Task 16/17/18 依赖 Task 1-15（代码变更完成后才能采集与断言）
- Task 19 依赖 Task 16-18（文档化已落地成果）
- Task 20 依赖 Task 1-19（RULES 文档化已落地成果）
- Task 21 依赖 Task 1-20（全量回归）
