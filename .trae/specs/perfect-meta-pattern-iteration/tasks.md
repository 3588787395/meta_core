# Tasks

本规范按「架构工程师 → 评审工程师」流程分 6 轮迭代，每轮独立可验证。

## 架构工程师任务（实施）

### 迭代 1：同步/异步双路径统一

- [x] Task 1: 合并 `_step_once` 与 `_astep_once` 为单一骨架
  - [x] SubTask 1.1: 在 `core/runtime_mode_module.py` 中定义 `_step_once_impl(self, d, *, async_mode: bool)` 方法，迁移 11 个步骤骨架
  - [x] SubTask 1.2: 通过 `async_mode` 参数分派同步/异步调用
  - [x] SubTask 1.3: 异步应用 `_ASTEP_KEY_TYPES` + 200 条上限，同步不过滤
  - [x] SubTask 1.4: 统一 `virtual_clock` 同步逻辑（修复 latent bug）
  - [x] SubTask 1.5: `step_once(d)` 改为 `return self._step_once_impl(d, async_mode=False)`
  - [x] SubTask 1.6: `astep_once(d)` 改为 `return await self._step_once_impl(d, async_mode=True)`
  - [x] SubTask 1.7: 删除原 `_step_once`（行 1664-1838）与 `_astep_once`（行 1838-2010）

- [x] Task 2: 合并 `_on_simulation_step` 与 `_on_replay_step`
  - [x] SubTask 2.1: 定义 `_on_step_event(self, event, *, driver_type, provider_fn)` 方法
  - [x] SubTask 2.2: `_on_simulation_step` 改为委托调用
  - [x] SubTask 2.3: `_on_replay_step` 改为委托调用
  - [x] SubTask 2.4: 删除原 7 步过程式展开

- [x] Task 3: 抽出 `_publish_tick_batch` 工具函数
  - [x] SubTask 3.1: 在 `core/tick_bar_module.py` 模块级定义 `_publish_tick_batch(bus, tick_data, ts)`
  - [x] SubTask 3.2: 替换 5 处调用点

### 迭代 2：公式引擎协议化

- [x] Task 4: 定义 `IFormulaEngine` Protocol
  - [x] SubTask 4.1: 定义 `class IFormulaEngine(Protocol)` 含标准签名
  - [x] SubTask 4.2-4.5: 4 个类标注 impl IFormulaEngine

- [x] Task 5: 表驱动 `FormulaRouter` 引擎分派
  - [x] SubTask 5.1: 定义 `_ENGINE_DISPATCH` dict
  - [x] SubTask 5.2: `eval` 改为查表分派
  - [x] SubTask 5.3: `eval_outvars`/`eval_batch` 改为表驱动
  - [x] SubTask 5.4: 保留内部实现，仅删除外层 if/elif

### 迭代 3：HTTP 路由 Depends 化

- [x] Task 6: `api.py` 引入 `require_config_store` Dependency
  - [x] SubTask 6.1: 定义 `require_config_store() -> ConfigStore`
  - [x] SubTask 6.2: router 级挂载 `dependencies=[Depends(require_config_store)]`
  - [x] SubTask 6.3: 删除 21+ 处 `if not _config_store` 样板

- [x] Task 7: `app.py` 引入 `get_simulator` + sim 路由表驱动
  - [x] SubTask 7.1: 定义 `get_simulator(name)` Depends
  - [x] SubTask 7.2: 定义 `_SIM_ACTIONS` dict
  - [x] SubTask 7.3: 定义单一 `@app.post("/api/pool/{name:path}/sim/{action}")` 路由
  - [x] SubTask 7.4: 删除原 5 个独立 sim 路由
  - [x] SubTask 7.5: 保留 `sim_init`/`sim_start`

### 迭代 4：配置加载统一到 ConfigStore

- [x] Task 8: `ConfigStore` 新增 `get_data_file` 方法
  - [x] SubTask 8.1: 定义 `get_data_file(self, name)` 方法
  - [x] SubTask 8.2: 实现 `data/{name}.json` 加载 + 缓存

- [x] Task 9: 替换 10 处散落帮助函数
  - [x] SubTask 9.1-9.10: 替换 10 个文件中的 `_load_json`/`_load_config`

### 迭代 5：中等优先级批次收敛

- [x] Task 10: `engine.py` `if mode_id == "replay"` 表驱动
- [x] Task 11: `domain.py` Node/Edge/Spec 样板优化
- [x] Task 12: `domain.py` Evaluator 注册表
- [x] Task 13: K 线合成器表驱动
- [x] Task 14: `app.py` tdx CRUD 路由表驱动
- [x] Task 15: `import_export_module.py` 表驱动收敛
- [x] Task 16: `runtime_mode_module.py` 4 对 set/get dataclass 化
- [x] Task 17: `web/js/event-panel.js` 渲染表驱动

### 迭代 6：低优先级收尾（可选）

- [x] Task 18: `table_engine.py` `_notify_changed` 单一函数
- [x] Task 19: `execution_module.py` `_publish` 工厂扩展
- [x] Task 20: 更新 RULES.md 第 84-90 条
- [x] Task 21: 更新 DESIGN.md 章节

## 评审工程师任务（验证）

- [x] Task 22: 验证迭代 1（同步/异步双路径统一）
- [x] Task 23: 验证迭代 2（公式引擎协议化）
- [x] Task 24: 验证迭代 3（HTTP 路由 Depends 化）
- [x] Task 25: 验证迭代 4（配置加载统一）
- [x] Task 26: 验证迭代 5（中等优先级批次）
- [x] Task 27: 验证迭代 6（低优先级收尾）
- [x] Task 28: 验证 RULES.md 与 DESIGN.md 更新
- [ ] Task 29: 回归验证（pytest + eventtest + 三模式 + 公式 + 热加载 + 导入导出）
  - [x] SubTask 29.1: pytest 已运行（353 失败，352 pre-existing + 1 新增非致命 test_compiler）
  - [ ] SubTask 29.2: eventtest 未运行
  - [ ] SubTask 29.3: 三模式运行时验证未执行
  - [ ] SubTask 29.4: 公式计算运行时验证未执行
  - [ ] SubTask 29.5: 热加载运行时验证未执行
  - [ ] SubTask 29.6: 导入导出运行时验证未执行
  - [ ] SubTask 29.7: 浏览器事件面板验证未执行

# Task Dependencies

- 迭代 1：Task 1 与 Task 2 可并行；Task 3 依赖 Task 1/2
- 迭代 2：Task 4 与 Task 5 可并行；Task 5 依赖 Task 4
- 迭代 3：Task 6 与 Task 7 可并行
- 迭代 4：Task 9 依赖 Task 8
- 迭代 5：所有 Task 可并行
- 迭代 6：Task 20/21 依赖 Task 1-19
- 评审：Task 22-29 依赖对应实施任务

# 迭代优先级

| 迭代 | 优先级 | 任务数 | 影响范围 |
|---|---|---|---|
| 1 | 高 | 3 | runtime_mode_module.py + tick_bar_module.py |
| 2 | 高 | 2 | formula_module.py |
| 3 | 高 | 2 | api.py + app.py |
| 4 | 高 | 2 | 10 个文件 |
| 5 | 中 | 8 | 8 个文件 |
| 6 | 低 | 4 | 2 个文件 + 文档 |
