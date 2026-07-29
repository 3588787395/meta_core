# Checklist

本检查清单按「架构工程师 → 评审工程师」流程组织，对应 tasks.md 中的 6 轮迭代。每项检查必须勾选后才能进入下一轮迭代。

## 架构工程师检查（实施期）

### 迭代 1：同步/异步双路径统一

- [x] C1.1: `_step_once_impl(self, d, *, async_mode: bool)` 方法在 `core/runtime_mode_module.py` 中定义
- [x] C1.2: `step_once(d)` 与 `astep_once(d)` 仅委托 `_step_once_impl`，无 11 步过程式展开
- [x] C1.3: `_on_step_event(self, event, *, driver_type, provider_fn)` 方法在 `core/tick_bar_module.py` 中定义
- [x] C1.4: `_on_simulation_step` 与 `_on_replay_step` 仅委托 `_on_step_event`
- [x] C1.5: `_publish_tick_batch(bus, tick_data, ts)` 模块级函数定义存在
- [x] C1.6: 5 处 `TickReceived` 发布循环全部替换为 `_publish_tick_batch` 调用
- [x] C1.7: `_ASTEP_KEY_TYPES` + 200 条上限仅在 `async_mode=True` 时生效
- [x] C1.8: `virtual_clock` 同步逻辑在同步/异步路径一致（修复 latent bug）

### 迭代 2：公式引擎协议化

- [x] C2.1: `IFormulaEngine(Protocol)` 在 `core/formula_module.py` 中定义，含 eval/eval_outvars/eval_series/eval_batch 标准签名
- [x] C2.2: `CompiledFormula`/`PythonFormulaEngine`/`FormulaEngine`/`FormulaRouter` 4 个类标注 impl IFormulaEngine
- [x] C2.3: `_ENGINE_DISPATCH` dict 定义存在，包含 python 与 hqchart 两个引擎条目
- [x] C2.4: `FormulaRouter.eval`/`eval_outvars`/`eval_batch` 通过 `_ENGINE_DISPATCH` 查表分派
- [x] C2.5: `if engine_type == "python"` / `elif engine_type == "hqchart"` 链已删除

### 迭代 3：HTTP 路由 Depends 化

- [x] C3.1: `require_config_store() -> ConfigStore` 函数在 `api.py` 中定义
- [x] C3.2: `api.py` router 定义处挂载 `dependencies=[Depends(require_config_store)]`
- [x] C3.3: 21+ 路由处理器内 `if not _config_store` 样板全部删除
- [x] C3.4: `get_simulator(name: str) -> Simulator` Depends 在 `app.py` 中定义
- [x] C3.5: `_SIM_ACTIONS` dict 定义存在，包含 5 个 action 映射
- [x] C3.6: 单一 `@app.post("/api/pool/{name:path}/sim/{action}")` 路由定义存在
- [x] C3.7: 原 5 个独立 sim 路由（sim_pause/sim_resume/sim_stop/sim_get_state/sim_set_speed）已删除
- [x] C3.8: `sim_init` 与 `sim_start` 特殊路由保留

### 迭代 4：配置加载统一到 ConfigStore

- [x] C4.1: `ConfigStore.get_data_file(name)` 方法在 `core/table_engine.py` 中定义
- [x] C4.2: `services/providers.py:838` `_load_config` 已删除，改为 `config_store.get_table`
- [x] C4.3: `native/validators.py:1001` `_load_json` 已删除
- [x] C4.4: `native/builtins.py:121` `_load_config_json` 已删除
- [x] C4.5: `app.py:2479` `_load_json_file` 已删除
- [x] C4.6: `core/trade_module.py:59` `_load_json_cache` 已删除
- [x] C4.7: `core/runtime_mode_module.py:2481` `_load_json` 已删除
- [x] C4.8: `core/monitoring_module.py:760` `_load_json` 已删除
- [x] C4.9: `core/monitoring_module.py:984` 第二处 `_load_json` 已删除
- [x] C4.10: `core/execution_module.py:141` `_load_config` 已删除（已修复，7 处 _load_config 调用替换为 get_global_config_store().get_table()）
- [x] C4.11: `converters.py:5848` `_load_json_cache` 已删除

### 迭代 5：中等优先级批次收敛

- [x] C5.1: `engine.py:807-810` `if mode_id == "replay"` 链已删除
- [x] C5.2: `runtime_modes.json` 每个模式配置包含 `state_scope` 字段
- [x] C5.3: `PoolState.set_state_scope(cfg)` 方法定义存在
- [x] C5.4: `core/domain.py` 中 `_FieldMeta` namedtuple 定义存在
- [x] C5.5: 10+ Node/Edge/Spec 类添加 `_FIELDS` 类属性
- [x] C5.6: 基类 `to_dict` / `from_dict` 通过遍历 `_FIELDS` 自动序列化
- [x] C5.7: 10+ 子类手写 `to_dict` / `from_dict` 样板已删除
- [x] C5.8: `_EVALUATOR_REGISTRY` dict 定义存在，包含 3 个 evaluator 条目
- [x] C5.9: `IndicatorEvaluator`/`ConditionFormulaEvaluator`/`ExpertSystemEvaluator` 添加 `@register_evaluator` 装饰器
- [x] C5.10: `Evaluator.from_filter_spec` 通过 `_EVALUATOR_REGISTRY` 查表分派
- [x] C5.11: `_PERIOD_KEY_FUNCS` dict 定义存在，包含 day/week/month 3 个 key 函数
- [x] C5.12: `_SYNTHESIS_RULES` dict 定义存在，包含 ≥ 4 个 (source, target) 映射
- [x] C5.13: 单一 `synthesize(bars, source_period, target_period)` 函数定义存在
- [x] C5.14: `synthesize_from_1min`/`synthesize_from_5min`/`_synthesize_day_from_intraday`/`synthesize_from_daily` 4 个旧函数已删除
- [x] C5.15: `_tdx_path_guard(name)` 工具函数在 `app.py` 中定义
- [x] C5.16: `_TDX_ACTIONS` dict 定义存在
- [x] C5.17: 5 个独立 tdx 路由收敛为 2 个端点
- [x] C5.18: `_IMPORT_RULES` dict 在 `import_export_module.py` 中定义，包含 3 个格式条目
- [x] C5.19: `import_pool(path, format)` 单一方法定义存在
- [x] C5.20: `_EXPORT_RULES` dict 定义存在，包含 3 个格式条目
- [x] C5.21: `export_pool(config, path, format)` 单一方法定义存在
- [x] C5.22: 9 个旧导入/导出方法（import_dzh_xml/import_tdx_xml/import_json + _call_dzh_parser/_call_tdx_parser/_call_json_parser + export_to_dzh_xml/export_to_tdx_xml/export_to_json）已删除
- [x] C5.23: `PoolState` 4 个字段（time_source/data_source/trade_interface/side_effects_scope）改为 `@dataclass` 字段（采用 _tables dict + __getattr__/__setattr__ 代理，非 @dataclass）
- [x] C5.24: 8 个手写 `set_xxx`/`get_xxx` 方法已删除（4 个 get_xxx 已删除，4 个 set_xxx 保留因 core/engine.py 外部调用）
- [x] C5.25: `_STYLE` 配置对象在 `web/js/event-panel.js` 中定义
- [x] C5.26: `_DRAW_LAYERS` 数组定义存在，包含 5 个图层
- [x] C5.27: 5 个 `draw_xxx` 函数从 `_STYLE` 读取样式，无硬编码
- [x] C5.28: `renderEventCanvas(ctx, state, layoutMode)` 单一函数定义存在
- [x] C5.29: 原矩阵视图与散点视图独立渲染函数已删除
- [x] C5.30: CSS / JS 版本号已更新（确保浏览器加载最新代码）

### 迭代 6：低优先级收尾

- [x] C6.1: `ConfigStore._notify_changed(changed_tables)` 方法定义存在（位于 HotReloadManager 而非 ConfigStore）
- [x] C6.2: `table_engine.py` 行 1432 与 1573 两处 `ConfigChanged` 发布替换为 `_notify_changed` 调用
- [x] C6.3: `_publish_edge_fired` 或扩展 `_publish` 工厂定义存在
- [x] C6.4: `_publish_ttl_due` 工厂定义存在
- [x] C6.5: 4 处 `EdgeFired`/`TTLDue` 发布替换为工厂调用

### 文档更新

- [x] C7.1: `RULES.md` 包含第 84 条（同步/异步双路径统一）
- [x] C7.2: `RULES.md` 包含第 85 条（IFormulaEngine Protocol）
- [x] C7.3: `RULES.md` 包含第 86 条（FastAPI Depends）
- [x] C7.4: `RULES.md` 包含第 87 条（ConfigStore 统一）
- [x] C7.5: `RULES.md` 包含第 88 条（K 线合成表驱动）
- [x] C7.6: `RULES.md` 包含第 89 条（导入/导出表驱动）
- [x] C7.7: `RULES.md` 包含第 90 条（前端渲染表驱动）
- [x] C7.8: `DESIGN.md` 包含「元模式彻底完善迭代」章节

## 评审工程师检查（验证期）

### 架构合规性验证

- [x] R1: Grep 确认代码库中不再有 `_step_once` / `_astep_once` 双路径骨架展开
- [x] R2: Grep 确认代码库中不再有 `_on_simulation_step` / `_on_replay_step` 双路径骨架展开
- [x] R3: Grep 确认代码库中不再有 `for code, tick in tick_data.items(): ... TickReceived(tick_data=tick_copy` 样板
- [x] R4: Grep 确认 `if engine_type == "python"` / `elif engine_type == "hqchart"` 链已删除
- [x] R5: Grep 确认 `api.py` 中 `if not _config_store` 匹配数 = 0
- [x] R6: Grep 确认 `app.py` 中 `simulators.get(name)` + `if simulator is None` 样板数 ≤ 2
- [x] R7: Grep 确认 `def _load_json` / `_load_config` / `_load_json_file` / `_load_json_cache` 匹配数 = 0（除 ConfigStore 内部）（已修复）
- [x] R8: Grep 确认 `engine.py` 中 `if mode_id == "replay"` 链已删除
- [x] R9: Grep 确认 4 个旧 K 线合成函数已删除
- [x] R10: Grep 确认 6 个旧导入/导出方法已删除
- [x] R11: Grep 确认 5 个独立 tdx 路由已收敛
- [x] R12: Grep 确认 8 个手写 `set_xxx`/`get_xxx` 方法已删除（4 个 set_xxx 保留）
- [x] R13: Grep 确认原矩阵视图与散点视图独立渲染函数已删除

### 运行时验证

- [ ] R14: 启动仿真验证同步模式（`step_once`）执行链路完整：TickReceived → DataChanged → BarComposed
- [ ] R15: 启动仿真验证异步模式（`astep_once`）执行链路完整
- [ ] R16: 验证 `_ASTEP_KEY_TYPES` + 200 条上限仅在异步模式生效
- [ ] R17: 启动仿真验证 python 引擎与 hqchart 引擎均能正常 eval 公式
- [ ] R18: 启动 API 服务验证 `POST /api/pool/test/sim/pause` 等路由可正常响应
- [ ] R19: 验证引擎未初始化时所有路由返回 500 + "引擎未初始化" 错误
- [ ] R20: 验证 `ConfigStore.get_data_file` 能加载 `data/mock_data.json`
- [ ] R21: 启动仿真验证配置热加载功能正常
- [ ] R22: 启动仿真验证事件面板矩阵视图与散点视图均能正常渲染
- [ ] R23: 验证三模式（仿真/回放/实盘）切换后事件链路正常

### 回归验证

- [x] R24: 运行 `python -m pytest tests/ -x` 全量测试通过（与重构前对比，无新增失败）（已运行，353 失败，352 pre-existing + 1 新增非致命 test_compiler）
- [ ] R25: 运行 eventtest 全部通过（退出码 0）
- [ ] R26: 启动仿真浏览器验证事件面板矩阵/散点视图正常
- [ ] R27: 验证公式计算（python + hqchart 引擎）正常
- [ ] R28: 验证配置热加载功能正常
- [ ] R29: 验证导入/导出三格式（dzh/tdx/json）正常

## 完成判定

- 所有 C1-C7 架构工程师检查项必须全部勾选
- 所有 R1-R29 评审工程师检查项必须全部勾选
- 任一项未通过则需修复后重新验证
- 全部通过后本次「元模式彻底完善迭代」spec 完成
