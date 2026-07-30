# metatest v7 严格正反合量化测试套件

## 概述

metatest v7 是股票池平台的严格正反合量化测试套件，覆盖前端与后端所有模块。
v6 在 v5 20 维基础上扩展为 **21 维**加权评分：v5 20 维等比降权 4%（每维 × 0.96），
新增第 21 维 adapter_isomorphism 占 4%（TqProvider/TqSdkBridge 转发方法表驱动覆盖率），
权重重新分配至总和 = 100%。v7 在 v6 21 维结构基础上，新增 per-code 循环表
（`_PER_CODE_TQ_CALLS`）+ 通用器（`_call_cached_per_code`），收敛 get_stock_list
双签名为双 `_call_cached` 调用，第 21 维覆盖率阈值从 80% 提升至 90%。

**第六层洞察（adapter 转发同构，外部 SDK 适配器元模式投影）**：TqProvider/TqSdkBridge
转发同构本质是 MetaDispatcher 之外的「声明 Data（方法名→默认值映射）+ Dispatcher
（通用转发器）」元模式投影。v6 将 20 个 TqProvider 转发方法收敛为 `_FORWARD_SPECS` 表 +
`_forward` 通用器，4 个 A 组缓存方法收敛为 `_CACHED_TQ_CALLS` 表 + `_call_cached` 通用器，
5 个 B 组简单方法收敛为 `_SIMPLE_TQ_CALLS` 表 + `_call_simple` 通用器（共 29 方法 / 3 通用器），
闭合 v5 SubTask 14.5 未竟目标，是元模式收敛的「最后一公里」。v7 新增 `_PER_CODE_TQ_CALLS`
表（3 条目）+ `_call_cached_per_code` 通用器，并将 get_stock_list 双签名收敛为双
`_call_cached` 调用（4 表 / 4 通用器 / 34 方法），完成 per-code 循环与双签名遗留收敛。

门槛：总分 ≥ 95 且 21 维均 ≥ 80 判定 PASS。

## 21 维评分规则与权重

权重总和 = 100%，总分 = Σ（维度得分 × 权重）。

### v4 16 维（v6 等比降权至 96%，每维 × 0.96）

| 序号 | 维度 | 权重 | 评分逻辑 |
|---|---|---|---|
| 1 | module_coverage | 5.376% | 覆盖模块数 / 17 × 100 |
| 2 | test_pass_rate | 9.984% | 通过数 / 总数 × 100（跳过计为失败） |
| 3 | assertion_density | 3.84% | 断言数 / (测试文件数 × 20) × 100 |
| 4 | event_chain_integrity | 6.144% | 出现事件类型数 / 10 × 100（链顺序错误扣 20%） |
| 5 | performance_benchmark | 3.84% | 1000 tick 耗时 ≤ 10s 满分，线性衰减 |
| 6 | frontend_e2e_pass_rate | 5.376% | 前端 E2E 真实通过数 / 总数 × 100（环境缺失给最低达标线 80） |
| 7 | logic_coverage | 3.84% | 5 项底层逻辑验证通过数 / 5 × 100 |
| 8 | isomorphism_elimination | 6.912% | 40 项同构代码 Grep 检查，0 违规满分 |
| 9 | line_convergence | 3.84% | 核心模块总行数 ≤ 22500 满分，线性衰减 |
| 10 | rule_compliance | 2.304% | RULES 91-100 Grep 违规数 / 10，0 违规满分 |
| 11 | negative_test_coverage | 1.536% | 4 类反测试用例数 / 目标数（每类 ≥ 8）均值 × 100 |
| 12 | synthesis_e2e | 2.304% | 合测试通过数 / 总数 × 100 |
| 13 | oop_inheritance_depth | 6.144% | BasePoolConverter + Dzh/TdxPoolConverter 继承 + 公共方法在基类 + 子类仅差异 |
| 14 | polling_zero_tolerance | 6.144% | 12 处轮询模式 Grep 零匹配 + EventDriver heapq 验证 + 前端 setInterval fetch 零匹配 |
| 15 | primitive_convergence | 6.144% | 三原语覆盖率（时间/分派/继承各 ≥ 95% 满分） |
| 16 | essence_ratio | 3.072% | 净减行数 / 变更前行数 × 100（目标 ≥ 12%，净增 = 0 触发 redo） |

### v5 新增 4 维（v6 降权至 96%，各 4.8%）

| 序号 | 维度 | 权重 | 评分逻辑 |
|---|---|---|---|
| 17 | dispatcher_isomorphism | 4.8% | MetaDispatcher 基类存在（25%）+ EventBus 继承（25%）+ ConfigStore 继承（25%）+ EventDriver 独立 + 公共骨架行数占比 ≥ 60%（25%）；详见 §MetaDispatcher 统一 |
| 18 | runtime_verification | 4.8% | 3 个 in-process 运行时验证测试通过率（replay/simulation/mode-switch），全绿 = 100，否则 0；详见 §运行时验证 harness |
| 19 | eventtest_regression | 4.8% | `python -m eventtest.run_eventtest` 退出码 0（全绿）= 100，否则 0 |
| 20 | cross_module_import_discipline | 4.8% | 8 处跨模块 import 违规模式 Grep 零匹配 = 100，每违规扣 12.5；详见 §跨模块 import 纪律 |

### v6 新增 1 维（4%，v5 20 维降权 4% 释放）

| 序号 | 维度 | 权重 | 评分逻辑 |
|---|---|---|---|
| 21 | adapter_isomorphism | 4.0% | TqProvider/TqSdkBridge 转发方法表驱动覆盖率 ≥ 90% 满分，线性衰减；覆盖率 = 表驱动覆盖方法数 / 总转发方法数 × 100（_FORWARD_SPECS + _CACHED_TQ_CALLS + _SIMPLE_TQ_CALLS + _PER_CODE_TQ_CALLS 四表条目 / 总数 = 34，4 通用转发器 _forward/_call_cached/_call_simple/_call_cached_per_code 各覆盖其表）；详见 §adapter 转发同构 |

### PASS 条件

- 总分 ≥ 95
- 21 维均 ≥ 80（redo_list 为空）
- 严格规则：跳过测试计为失败；前端 E2E 环境缺失给最低达标线 80（环境问题非代码问题）；无任何硬编码信用分

## MetaDispatcher 统一（第五层洞察，`dispatcher_isomorphism` 维度）

公理 `Code = Data + MetaDispatcher`：三原语（时间 / 分派 / 继承）同构于运行时三核 Dispatcher。
v5 抽取 `MetaDispatcher` 抽象基类（`core/event_bus.py` 顶部，骨架 ≤ 30 行），统一 EventBus
与 ConfigStore 的 `register-store-dispatch` 元模式投影，EventDriver 因 heapq 时序特化保持独立。

| 子类 | 元模式投影 | `_store` 特化 | `_dispatch_impl` 覆盖 |
|---|---|---|---|
| **EventBus**（扇出子类） | `register(event_type, handler)` → `_subscribers[key].append(handler)` → `dispatch(key, event)` | `_subscribers: Dict[str, List[Callable]]` | 遍历订阅者 + `_any_subscribers` 扇出（副作用） |
| **ConfigStore**（查找子类） | `register(name, data)` → `_tables[name] = data` → `dispatch(name)` → 查找 | `_tables: Dict[str, Dict]` | `return self._tables.get(key)`（纯查找无副作用） |
| **EventDriver**（独立） | heapq 优先队列 + `fire_time` 排序 + 自动续程（periodic reschedule），与 MetaDispatcher 不同构，不继承 | `self._heap` | （不覆盖，保持独立） |

`MetaDispatcher` 模板方法：`register(self, key, value)` → `self._store[key] = value`；
`dispatch(self, key, *args, **kwargs)` → `return self._dispatch_impl(key, *args, **kwargs)`。

正测试 `test_positive_dispatcher_isomorphism.py` 断言：基类存在 + 3 子类继承/独立 + 公共骨架行数
占比 ≥ 60%（`MetaDispatcher` 行数 / (MetaDispatcher + 2 个 `_dispatch_impl`)）。

## 运行时验证 harness（`runtime_verification` 维度）

v5 阶段 2 闭合 v4 沙箱缺口 34.12（replay 步进）/ 34.13（simulation auto-step）/ 34.15（三模式切换），
全部用 `fire_due(now)` 手动推进时间，**禁止 `time.sleep` / `asyncio.sleep` 步进 / 启动服务或浏览器**。

| 测试文件 | 验证点 |
|---|---|
| `test_runtime_replay_heapq.py` | `set_mode("replay")` → `play()` → `_heap` 含 step TimedEventSpec；`fire_due(now+interval)` 触发 `EdgeFired`；`pause()` cancel heapq |
| `test_runtime_simulation_heapq.py` | `set_mode("simulation")` → `start_auto()` → `_heap` 含 sim_step TimedEventSpec；`fire_due(now+1.0/speed)` 推进 auto-step；`stop_auto()` cancel heapq |
| `test_runtime_mode_switch.py` | `set_mode` 切换发布 `ModeChanged` ×2；切换后 `TickReceived → BarComposed → FormulaEvaluated → StockFiltered` 链路完整；切到实盘后 heapq 不再步进 |

正测试 `test_positive_runtime_verification.py` 通过 subprocess 运行 3 个文件，断言退出码 0（全绿）。

## 跨模块 import 纪律（`cross_module_import_discipline` 维度）

7 个业务模块（execution / screening / formula / runtime_mode / trade / tick_bar / monitoring）
禁止直接 `import table_engine` / `screening_module`，必须经依赖注入或下沉到白名单基础模块
（`core/domain.py` / `converters/_common.py`）。

8 处违规模式 Grep 零匹配：

| 序号 | 模式 | 范围 |
|---|---|---|
| 1-7 | `from\s+\.table_engine\s+import` 或 `from\s+core\.table_engine\s+import` | 7 个业务模块各 1 处 |
| 8 | `from\s+\.screening_module\s+import` | `execution_module.py` |

白名单：`core/domain.py`（基础模块）允许 import table_engine；`load_config_table` /
`get_global_config_store` 下沉至 `converters/_common.py` 或 `core/domain.py`，
`_apply_noperate_mode_series` / `_resolve_series_lookback` 改为构造函数依赖注入。

反测试 `test_negative_cross_module_import.py` 断言 8 处违规模式 0 匹配。

## adapter 转发同构（第六层洞察，`adapter_isomorphism` 维度）

TqProvider / TqSdkBridge 转发同构本质是 MetaDispatcher 之外的「声明 Data（方法名→默认值
映射）+ Dispatcher（通用转发器）」元模式投影。v6 将三组同构转发方法收敛为表 + 通用器，
v7 在 v6 基础上新增 per-code 循环表（`_PER_CODE_TQ_CALLS`）+ 通用器（`_call_cached_per_code`），
并将 get_stock_list 双签名收敛为双 `_call_cached` 调用，闭合 per-code 转发与双签名遗留缺口：

| 通用转发器 | 表 | 覆盖方法数 | 表条目（method→default/sdk 映射） |
|---|---|---|---|
| `_forward` | `_FORWARD_SPECS` | 20 | TqProvider 20 个转发方法 → (default, kw 参数元组) |
| `_call_cached` | `_CACHED_TQ_CALLS` | 6 | TqSdkBridge A 组缓存方法 → (cache_key 模板, sdk 方法, 默认值)；v7 追加 get_stock_list_by_type / get_stock_list 双签名条目（4 → 6） |
| `_call_simple` | `_SIMPLE_TQ_CALLS` | 5 | TqSdkBridge B 组简单方法 → sdk 方法名 |
| `_call_cached_per_code` | `_PER_CODE_TQ_CALLS` | 3 | TqSdkBridge per-code 缓存方法 → (cache_prefix, sdk_method, cache_only_if_truthy) |

覆盖率 = 表驱动覆盖方法数 / 总转发方法数 × 100 = 34 / 34 × 100 = 100%（4 通用转发器
均存在时四表条目计为已覆盖）。覆盖率 ≥ 90% 满分，线性衰减。

### per-code 循环表驱动（v7 新增）

`_PER_CODE_TQ_CALLS` 表（3 条目）将 per-code 循环转发方法收敛为表 + 通用器，
映射方法名 → `(cache_prefix, sdk_method, cache_only_if_truthy)`：

| 方法名 | cache_prefix | sdk_method | cache_only_if_truthy |
|---|---|---|---|
| get_snapshot | snapshot | get_snapshot | False |
| get_stock_info | stock_info | get_stock_info | False |
| get_report_data | report_data | get_report_data | True |

`_call_cached_per_code(self, method_name, codes)` 通用方法承担 per-code 循环转发：

1. **per-code 循环**：遍历 `codes` 列表，逐 code 调用底层 SDK
2. **cache_key 构建**：`f"{cache_prefix}:{code}"`（per-code 维度隔离缓存）
3. **缓存检查**：命中则直接返回，跳过 SDK 调用
4. **SDK 调用**：未命中时 `getattr(self._sdk, sdk_method)(code)` 调用底层 SDK
5. **条件缓存写入**：`cache_only_if_truthy=True` 时仅当 `if data:` 真值判断通过才写入缓存（保留 get_report_data 原有 `if data:` 行为，空快照不污染缓存）
6. **异常兜底**：SDK 抛异常时返回 None / 空结果，不向上传播

`cache_only_if_truthy` 标志语义：get_report_data 的空快照（None / 空对象）既不写入缓存，
也不出现在结果字典中，避免空数据污染下游；get_snapshot / get_stock_info 始终缓存（False）。

### get_stock_list 双签名收敛（v7 新增）

get_stock_list 方法存在新旧两种调用签名，v7 将方法体重构为双 `_call_cached` 调用，
按签名分流到 `_CACHED_TQ_CALLS` 不同条目：

| 签名 | 触发条件 | 路由条目 | kwargs 透传 |
|---|---|---|---|
| 新签名 | `list_type` kwarg 存在 | `get_stock_list_by_type` 条目 | `**kwargs` 透传（含 list_type） |
| 旧签名 | `list_type` kwarg 缺失 | `get_stock_list` 条目 | `market=str(market_id)` 关键字形式 |

旧签名 `market=str(market_id)` 关键字形式与新签名 `**kwargs` 透传保留，确保调用方兼容。
两条目均纳入 `_CACHED_TQ_CALLS` 表（v7 追加，使该表从 4 条目扩至 6 条目）。

正测试 `test_positive_adapter_isomorphism.py` 断言：四表存在且条目数达标 +
四通用转发器方法定义 + 覆盖率 ≥ 90%。

## 运行方式与退出码

```bash
python -m metatest.runner
```

- 退出码 0 = 总分 ≥ 95 且 21 维均 ≥ 80（PASS）或无测试文件
- 退出码 1 = 总分 < 95 或有维度 < 80（FAIL）或有测试失败

## 目录结构

```
metatest/
├── conftest.py                              # 共享 pytest 夹具
├── scoring.py                               # 21 维量化评分引擎（v7）
├── runner.py                                # 测试运行器 + 21 维数据采集（含 adapter_forward_coverage 等字段）
├── test_positive_dispatcher_isomorphism.py  # v5 MetaDispatcher 继承断言
├── test_positive_runtime_verification.py     # v5 3 个 in-process 测试全绿
├── test_negative_cross_module_import.py     # v5 8 处违规模式零匹配
├── test_positive_adapter_isomorphism.py     # v6 adapter 转发表驱动覆盖率断言
├── test_runtime_replay_heapq.py             # v5 harness（replay）
├── test_runtime_simulation_heapq.py         # v5 harness（simulation）
├── test_runtime_mode_switch.py              # v5 harness（mode-switch）
├── test_positive_*.py / test_negative_*.py / test_synthesis_*.py  # v3/v4 正反合
└── report.json                              # 21 维 + meta_unification 结构化报告
```

## v7 严格规则总结

- 跳过测试计为失败（不在 passed 分子）
- 前端 E2E 环境缺失计 `frontend_e2e_passed=0`，给最低达标线 80（非信用分）
- 21 维分数均需 ≥ 80 才达标（redo_list 为空）
- 总分 ≥ 95 且 21 维均 ≥ 80 判定 PASS
- 所有评分由真实测试结果 / Grep / AST / 行数统计计算，无硬编码信用分
- essence_ratio 净增 = 0 触发 redo（强制「合并非拆分」硬约束）
- 任一轮询模式 > 5 匹配直接判 0 分（polling 零容忍硬约束）
- 三原语覆盖率 ≥ 95% 当且仅当 meta_purity ≥ 90%（根因一致性）
- EventDriver 因 heapq 时序特化保持独立，不继承 MetaDispatcher（dispatcher 同构硬约束）
- 运行时验证 harness 禁用 `time.sleep` / `asyncio.sleep` 步进（fire_due 推进硬约束）
- adapter 转发同构覆盖率 ≥ 90% 才达标（v7 提升至 90%，外部 SDK 适配器表驱动硬约束；4 表 / 4 通用转发器 / 34 方法）
