# Checklist

## 架构工程师检查点（实施前自检）

- [ ] 已阅读 spec.md「Why」章节并理解：v11 是「审计盲区闭合」——v10 的「全局上限」是 metatest 未覆盖 converters.py 制造的假象，DZHPoolExecutor 这个 ~400 行活体平行运行时漏网
- [ ] 已理解本次迭代核心是「底层运行逻辑洞察 + 真正同构代码合并」——DZHPoolExecutor.execute_once 与 PoolEngine.execute_pool 同构、_run_loop 与 run_loop 同构，运行时只有一个真相源
- [ ] 已阅读 `converters.py:2910-3010` 确认 DZHPoolExecutor `__init__` 的 7 个轮询/线程/事件日志字段
- [ ] 已阅读 `converters.py:3183-3240` 确认 `_run_loop` / `start` / `stop` 三个轮询方法体
- [ ] 已阅读 `api.py:6355-6403` 确认 `/pool/run` 与 `/pool/start` 端点当前实例化 DZHPoolExecutor
- [ ] 已阅读 `api.py:5400 / 5470` 确认两处死导入（仅调 `engine.execute_pool`）
- [ ] 已 Grep `DzhXmlExporter` 全仓确认仅命中类定义本身（零实例化、零导入）
- [ ] 已阅读 `core/table_engine.py:842-898` 确认 `decode_tdx_action_hex` / `encode_tdx_action_hex` 硬编码 type_map/byte_type_map
- [ ] 已阅读 `converters.py:1157 / 1206` 确认 `decode_action` / `_encode_action_raw` 表驱动共享版
- [ ] 已阅读 `core/import_export_module.py:107-109` 确认 `_call_converter` 的 `if auto and fmt == "tdx"` 分支
- [ ] 已阅读 `converters.py:4484/4878/5908/5975` 确认 4 个 TDX 模块级自由函数
- [ ] 已 Grep `_scode` / `_extract_code` 在 core/ 确认 3 处本地重复定义
- [ ] 已 Grep `_ce_` 在 core/engine.py 确认 4 行死导入零使用点
- [ ] 已 Grep `elif mode == 'sdk'` 在 api.py 确认 2 处死等价分支
- [ ] 已阅读 `core/engine.py:301-316` 确认 TTL 注册双循环同构
- [ ] 已确认「合并非拆分」硬约束延续：DZHPoolExecutor 删除轮询基础设施但保留 execute_once，TDX 函数归入子类而非新建文件

## 评审工程师检查点（阶段 1：DZHPoolExecutor 平行运行时消除）

### 变更 D1 — 删除 DZHPoolExecutor 轮询基础设施
- [x] `_run_loop` / `start` / `stop` / `_log_event` 四个方法已删除  （Grep 零匹配验证通过）
- [x] `__init__` 中 `_timers` / `_thread` / `_stop_event` / `_events` / `_edge_last_trigger` / `_start_time` / `running` 七个字段已删除  （__init__ 仅保留 config/tq_adapter/formula_router/state/_nodes/_edges 六个字段）
- [x] `execute_once` / `_init_nodes_edges` / `_init_mock_stocks` / `NodeStateMachine` 保留完整  （line 2886/3192/3207/3414 均存在）
- [x] Grep `_run_loop|_stop_event|_timers|_thread|_events|_log_event|_edge_last_trigger|_start_time|self.running` 在 converters.py DZHPoolExecutor 类内零匹配
- [x] `python -c "from converters import DZHPoolExecutor; e=DZHPoolExecutor({'nodes':[],'edges':[]}); print(hasattr(e,'execute_once'), not hasattr(e,'_run_loop'), not hasattr(e,'start'))"` 输出 `True True True`  （运行验证输出 `True True True`）

> **范围声明**：用户硬约束「仅修改 converters.py 与 core/import_export_module.py 两个文件」将本规范原 7 阶段 21 任务收敛为阶段 1 的 D1、阶段 2 的 E1、阶段 4 的 G1+G2 四项。D2/D3/D4/D5（涉及 api.py / __init__.py）、阶段 3 / 5 / 6 / 7（涉及 table_engine.py / domain.py / engine.py / app.py / metatest / RULES.md 等其他文件）均不在本次收敛范围。

### 变更 D2 — /pool/start 端点委托 PoolEngine
- [ ] `start_pool` 端点不再实例化 DZHPoolExecutor，改为调 `engine.run_loop()` 或等价事件驱动方法
- [ ] `stop_pool` 端点不再调 `executor.stop()`，改为调 `engine.stop_loop()` 或等价方法
- [ ] `request.app.state._dzh_executors` 字典已删除
- [ ] Grep `_dzh_executors` 在 api.py 零匹配
- [ ] Grep `DZHPoolExecutor` 在 api.py /pool/start 与 /pool/stop 端点零匹配

### 变更 D3+D4 — /pool/run 委托 PoolEngine + 死导入删除
- [ ] `run_pool` 端点改为调 `engine.execute_pool(config)`
- [ ] 返回字段映射对齐（output_count/output_stocks/events/node_states）
- [ ] `api.py:5400` 死导入已删除
- [ ] `api.py:5470` 死导入已删除
- [ ] Grep `from converters import DZHPoolExecutor` 在 api.py 零匹配

### 变更 D5 — __init__.py 导出清理
- [ ] `__init__.py:28` `from .converters import DZHPoolExecutor` 已删除
- [ ] `__init__.py:30` `DZHPoolExecutor = None` 已删除
- [ ] `__init__.py:47` `__all__` 中的 `"DZHPoolExecutor"` 已删除
- [ ] Grep `DZHPoolExecutor` 在 __init__.py 零匹配
- [ ] `python -c "import workspace; print('import OK')"` 验证

### 阶段 1 整体验证
- [ ] Grep `while.*wait\(1\)|time\.time\(\).*interval` 在 converters.py 零匹配
- [ ] Grep `threading\.Thread|threading\.Timer|threading\.Event` 在 converters.py 零匹配（import 行除外）
- [ ] `python -m pytest metatest/ -x -q --ignore=metatest/test_positive_event_panel.py` 全量测试不回归
- [ ] `python -m eventtest.run_eventtest` 退出码 0

## 评审工程师检查点（阶段 2：DzhXmlExporter 死代码清除）

### 变更 E1 — 删除 DzhXmlExporter 类
- [x] `converters.py:3982-4310` 整个 DzhXmlExporter 类已删除（~328 行）  （Grep `DzhXmlExporter` 在 converters.py 零匹配）
- [x] Grep `DzhXmlExporter` 全仓零匹配  （仅命中 spec/tasks/checklist 文档自身引用，源码零匹配）
- [x] `python -c "import converters; print('import OK')"` 验证  （import OK，923 测试通过）

## 评审工程师检查点（阶段 3：decode_tdx_action_hex 表驱动统一）

### 变更 F1 — 删除 core/table_engine.py 重复实现
- [ ] `core/table_engine.py:842-898` 的 `decode_tdx_action_hex` / `encode_tdx_action_hex` 已删除
- [ ] Grep `decode_tdx_action_hex|encode_tdx_action_hex` 在 core/table_engine.py 零匹配

### 变更 F2 — 配置表反射调用改指共享版
- [ ] `config/data/data_mappings.json:166` transform_expr 改为 `decode_action`
- [ ] `config/data/data_config.json:1728` transform_expr 改为 `_encode_action_raw`
- [ ] Grep `decode_tdx_action_hex|encode_tdx_action_hex` 在 config/data/*.json 零匹配
- [ ] 若需扩充 `filter_action_rules.json` 加 tdx 子表，已添加 `tdx_high_type_map` / `tdx_byte_type_map`
- [ ] `python -c "import json; json.load(open('config/data/data_mappings.json')); json.load(open('config/data/data_config.json')); print('JSON OK')"` 验证

## 评审工程师检查点（阶段 4：_call_converter tdx 分支消除 + TDX 函数归入 TdxPoolConverter）

### 变更 G1 — 消除 _call_converter tdx 分支
- [x] `BasePoolConverter` 新增 `_to_frontend(self, result, name)` 钩子方法（默认 `return result`）  （converters.py:278-280，默认透传）
- [x] `TdxPoolConverter` 覆盖 `_to_frontend` 调 `_tdx_pool_to_frontend`  （converters.py:1048，覆盖为前端 dict 转换）
- [x] `_call_converter` 删除 `if auto and fmt == "tdx"` 分支，改为统一调 `converter._to_frontend(result, name)`  （import_export_module.py:117-122，经 `_FORMAT_CONVERTER_ATTR` 表分派）
- [x] Grep `if.*fmt.*==.*\"tdx\"|if auto and fmt` 在 core/import_export_module.py 零匹配

### 变更 G2 — TDX 自由函数归入 TdxPoolConverter
- [x] `tdx_to_internal` / `convert_tdx_to_config` / `_tdx_pool_to_frontend` / `_load_tdx_pool_config` 已归入 TdxPoolConverter  （真实实现位于 TdxPoolConverter 内：tdx_to_internal@1115 / convert_tdx_to_config@1262 / load_tdx_pool_config@1293 / _to_frontend 覆盖@1048）
- [x] `python -c "from converters import TdxPoolConverter; print('import OK')"` 验证  （import OK，hasattr 全 True）

> **G2 实现说明（向后兼容包装）**：因用户硬约束「仅修改 converters.py 与 core/import_export_module.py」，app.py / native/builtins.py 等外部调用方不在可修改范围。故保留 4 个模块级 thin wrapper（converters.py:4357/4609/5613/5618）单行委托到 `TdxPoolConverter` 方法 / `_TDX_CONVERTER` 单例，确保外部调用方零改动。原 checklist「顶层零匹配」断言在 2 文件约束下不可达成——TDX 领域逻辑已实质归入子类（满足 G2 本质要求），模块级 wrapper 仅为兼容 shim。

## 评审工程师检查点（阶段 5：同构函数合并）

### 变更 H1 — _stock_code / _scode / _extract_code 四胞胎统一
- [ ] `core/runtime_mode_module.py:125` `_scode` 已删除，调用点改用 `_stock_code`
- [ ] `core/runtime_mode_module.py:2410` `_extract_code` 已删除，调用点改用 `_stock_code`
- [ ] `core/screening_module.py:153` `_extract_code` 已删除，调用点改用 `_stock_code`（含 `"Code"` 大写回退处理）
- [ ] Grep `^def _scode|^def _extract_code` 在 core/ 零匹配

### 变更 H2 — _normalize_period 双胞胎统一
- [ ] `services/data.py` 的 `_PERIOD_ALIASES` 已扩充补齐 app.py 缺失别名
- [ ] `app.py:250` `_normalize_period` 已删除，改为 `from services.data import _normalize_period`
- [ ] Grep `^def _normalize_period` 在 app.py 零匹配

### 变更 H3 — engine.py _ce_* 死导入删除
- [ ] `core/engine.py:39-42` 4 行死导入已删除
- [ ] Grep `_ce_` 在 core/engine.py 零匹配

### 变更 H4 — api.py 死等价分支合并
- [ ] `api.py:6165-6168` 第一处分支合并为 `if mode in ('real', 'sdk')`
- [ ] `api.py:6214-6217` 第二处分支合并为 `if mode in ('real', 'sdk')`
- [ ] Grep `elif mode == 'sdk'` 在 api.py 零匹配

### 变更 H5 — engine.py TTL 注册双循环合并
- [ ] 新增 `_register_ttl_batch` 助手函数
- [ ] edge TTL 循环改为调 `_register_ttl_batch`
- [ ] node TTL 循环改为调 `_register_ttl_batch`
- [ ] Grep `for stock in stocks:` 在 core/engine.py TTL 注册区仅命中 `_register_ttl_batch` 内部 1 处

## 评审工程师检查点（阶段 6：metatest v11 量化评审升级）

### 变更 M1+M2+M3 — runner.py 新增三个采集函数
- [ ] `_collect_polling_violations` 扫描文件列表已新增 `converters.py`
- [ ] 新增 `_collect_parallel_runtime_violations()` 函数（检测 threading.Thread + while + _stop_event.wait 组合）
- [ ] 新增 `_collect_dead_code_violations()` 函数（AST 检测零实例化/零导入类）
- [ ] `_collect_isomorphism_violations` 新增 3 项检查
- [ ] test_results 新增 `converters_polling_violations` / `parallel_runtime_violations` / `dead_code_violations` 字段
- [ ] `python -c "from metatest.runner import _collect_parallel_runtime_violations, _collect_dead_code_violations; print(_collect_parallel_runtime_violations()); print(_collect_dead_code_violations())"` 两者均返回零违规

### 变更 M4+M5 — scoring.py ISOMORPHISM_CHECKS_TOTAL 扩展
- [ ] `ISOMORPHISM_CHECKS_TOTAL = 44`（从 41 扩展）
- [ ] `_score_isomorphism_elimination` 使用 44 作分母
- [ ] `python -c "from metatest.scoring import ISOMORPHISM_CHECKS_TOTAL; print(ISOMORPHISM_CHECKS_TOTAL)"` 输出 44

### 变更 M6 — 测试断言
- [ ] `metatest/test_negative_polling.py` 新增 converters.py 轮询零匹配断言
- [ ] 新增平行运行时零容忍断言测试
- [ ] 新增死代码零容忍断言测试
- [ ] `python -m pytest metatest/test_negative_polling.py metatest/test_positive_no_parallel_runtime.py metatest/test_positive_no_dead_code.py -v` 退出码 0

### 变更 M7 — README.md v11 文档
- [ ] README.md 标题 v10 → v11
- [ ] 新增第十一层洞察说明（审计盲区是收敛上限的最大敌人）
- [ ] 新增「v10 上限范围修正」段落
- [ ] isomorphism_elimination 维度说明 41 → 44 项

## 评审工程师检查点（阶段 7：RULES 修订 + 全量回归）

### RULES 120 修订 + 121 新增
- [ ] 第 120 条修订为「审计盲区闭合后的全局收敛上限」
- [ ] 新增第 121 条「禁止平行运行时」
- [ ] Grep `^121\.` 在 RULES.md = 1

### 全量回归
- [ ] `python -m pytest metatest/ -x -q --ignore=metatest/test_positive_event_panel.py` 全量测试通过
- [ ] handler_exception_coverage = 100%（v10 成果不回归）
- [ ] parallel_runtime_violations = 0（v11 新增，零违规）
- [ ] dead_code_violations = 0（v11 新增，零违规）
- [ ] converters_polling_violations = 0（v11 新增，零违规）
- [ ] `python -m eventtest.run_eventtest` 退出码 0（全绿）
- [ ] Grep `DZHPoolExecutor` 在 api.py /pool/start + /pool/stop 端点零匹配
- [ ] Grep `DzhXmlExporter` 全仓零匹配
- [ ] Grep `decode_tdx_action_hex|encode_tdx_action_hex` 在 core/table_engine.py 零匹配
- [ ] Grep `if.*fmt.*==.*\"tdx\"` 在 core/import_export_module.py 零匹配
- [ ] Grep `^def _scode|^def _extract_code` 在 core/ 零匹配
- [ ] Grep `_ce_` 在 core/engine.py 零匹配
- [ ] Grep `elif mode == 'sdk'` 在 api.py 零匹配
- [ ] oop_inheritance_depth 维度 = 100（v9/v10 保持，TDX 函数归入子类后继承纯度提升）
- [ ] isomorphism_elimination 维度 = 100（44 项 0 违规）
- [ ] handler_exception_coverage 维度 = 100（v10 保持）
- [ ] DZH↔TDX roundtrip 保真（19 roundtrip 测试全过）
- [ ] essence_ratio 维度提升（净 −500 ~ −560 行）
- [ ] adapter_isomorphism 维度 = 100（v7/v8/v9/v10 保持）
- [ ] dispatcher_isomorphism 维度 = 100（v5/v6/v7/v8/v9/v10 保持）
- [ ] runtime_verification 维度 = 100（v5/v6/v7/v8/v9/v10 保持）
- [ ] eventtest_regression 维度 = 100（v5/v6/v7/v8/v9/v10 保持）

## 第十一层洞察根因检查点（评审工程师最终验收）

- [ ] **审计盲区是收敛上限的最大敌人**：v10 的 metatest 轮询零容忍检查只 grep `core/runtime_mode_module.py` / `core/table_engine.py` / `services/data.py` 三个文件，从未覆盖 `converters.py`，导致 DZHPoolExecutor 平行运行时漏网。v11 闭合此盲区，metatest 扫描文件列表扩展到 converters.py
- [ ] **运行时单一真相源**：DZHPoolExecutor.execute_once 与 PoolEngine.execute_pool 同构、_run_loop 与 run_loop 同构——但前者轮询后者事件驱动。v11 消除 DZHPoolExecutor 平行运行时，/pool/run 与 /pool/start 统一委托 PoolEngine，运行时只有一个真相源
- [ ] **死代码零容忍**：DzhXmlExporter（328 行平行 DZH 导出器，零实例化零导入）已删除。metatest 新增死代码检测维度，防止未来死代码积累
- [ ] **TDX 领域知识归入子类**：decode_tdx_action_hex 重复实现已删除（表驱动统一），TDX 4 个自由函数已归入 TdxPoolConverter，_call_converter 的 `if fmt == "tdx"` 分支已消除——「大智慧和通达信只作为继承，所有基础功能用相同代码」
- [ ] **同构函数合并**：_stock_code 四胞胎 / _normalize_period 双胞胎 / _ce_* 死导入 / 死等价分支 / TTL 双循环 5 处真同构已合并——「真正同构代码的合并」
- [ ] **非拆分非重写**：DZHPoolExecutor 保留 execute_once 一次性执行逻辑，仅删除轮询基础设施；TDX 函数归入子类而非新建文件；同构函数合并为单一规范版
- [ ] **量化评审驱动**：isomorphism_elimination 维度新增 3 项检查（converters 轮询 / 平行运行时 / 死代码），41→44 项，使评分体系能驱动审计盲区闭合
- [ ] **诚实声明确认**：v11 不是对 v10 的否定——v10 在 core/ 焦点目录内的收敛是真实的。v11 是对 v10「全局上限」声明的范围修正：上限仅适用于 core/ 内部，converters.py 跨域审计盲区在 v11 闭合后全局上限才真正成立
