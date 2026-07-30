# 元模式本质收敛 v6：TQ SDK 转发同构闭合 + 元模式深化精修 Spec

## Why

v5 元模式收敛已完成（98.42 PASS，core/*.py 19936 行，20 维全 ≥ 80，essence_ratio 16.93%）。用户要求「继续架构洞察迭代优化，完善元模式精减代码」。经架构工程师第六层深度洞察调研（覆盖 services/providers.py TQ SDK + DataSourceManager + core 残余 try + runtime_mode + execution_module + converters），确认 v4+v5 已收割 90%+ 同构红利，v6 收敛空间高度有限（预估 100-115 行），但存在一项 v5 保守保留的未竟目标：

**v5 SubTask 14.5「TQ SDK 调用合并为表驱动（`_TQ_API_SPECS` + 通用 `_call_tq_api`）」未真正落地**。v5 providers.py 收敛仅做死代码+注释清理（-204 行），TQ SDK 转发同构保守保留。经 v6 调研重新评估，确认该保守保留可安全解除：

1. **TqProvider 17 个转发方法**（`services/providers.py:8250-8404`）骨架完全同构：`if self._bridge is None: return DEFAULT → return self._bridge.METHOD(args)`，仅默认返回值（`{}` / `[]` / `False` / `{"success": False}` 等）与参数转发签名差异。预估净减 35-45 行。
2. **TqSdkBridge A 组 4 个缓存方法**（`get_block_list` / `get_block_members` / `get_sector_list` / `get_stock_list_in_sector`，`:7575-7655`）共享「cache_key → 缓存命中 → SDK 调用 → 缓存写入 → 异常兜底」骨架。预估净减 25 行。
3. **TqSdkBridge B 组 5 个简单方法**（`send_user_block` / `create_sector` / `clear_sector` / `formula_process_mul_xg` / `formula_process_mul_zb`，`:7669-7717`）共享「if not self._tq: return None → try: SDK 调用 → except: return None」骨架。预估净减 20 行。

**第六层洞察（本次迭代新增的元模式深化）**：经更深架构洞察，确认 TqProvider/TqSdkBridge 的转发同构本质是 **Dispatcher 之外的转发同构**——它们是「外部 SDK 边界适配器」，遵循 `adapter(method, args) → delegate(method, args) or default` 元模式，与 MetaDispatcher 的 `register-store-dispatch` 同构于「声明 Data（方法名→默认值映射）+ Dispatcher（通用转发器）」。v5 聚焦运行时三核 Dispatcher 内部统一，v6 闭合外部适配器的转发同构，是元模式收敛的「最后一公里」。

同时，调研确认 core/ 残余同构已饱和（runtime_mode_module 已完全表驱动化、execution_module 表驱动设施已饱和、converters BasePoolConverter 已彻底），v6 不在 core/ 做同构收敛，仅做必要精修。

## What Changes

### 阶段 1：TqProvider 17 转发方法表驱动闭合（高优先级，闭合 v5 SubTask 14.5）

- **变更 T1：TqProvider 转发表驱动**。定义 `_FORWARD_SPECS: Dict[str, Tuple[Any, Tuple[str, ...]]]` 映射方法名 → (default, kw 参数名元组) + 通用 `_forward(method_name, *args, **kwargs)` 方法。17 个转发方法改为表条目 + 薄包装（或通过 `__getattr__` 动态分派，但保守起见保留显式方法签名以维持 IDE 提示与类型检查）。**BREAKING**：无（行为完全不变）。净减约 35-45 行。

### 阶段 2：TqSdkBridge 双组表驱动闭合（高优先级）

- **变更 T2：TqSdkBridge A 组缓存方法表驱动**。定义 `_CACHED_TQ_CALLS: Dict[str, Tuple[str, str, Any]]` 映射方法名 → (cache_key 模板, sdk 方法名, 默认值) + 通用 `_call_cached(method_name, cache_prefix, sdk_method, *args, default)` 方法。4 个缓存方法改为表条目 + 薄包装。**BREAKING**：无。净减约 25 行。
- **变更 T3：TqSdkBridge B 组简单方法表驱动**。定义 `_SIMPLE_TQ_CALLS: Dict[str, str]` 映射方法名 → sdk 方法名 + 通用 `_call_simple(method_name, sdk_method, **kwargs)` 方法。5 个简单方法改为表条目 + 薄包装。**BREAKING**：无。净减约 20 行。

### 阶段 3：DataSourceManager 代理条件性收敛（中优先级，视阶段 1-2 收益）

- **变更 T4：DataSourceManager 代理表驱动（条件性）**。仅当阶段 1-2 净减不足 80 行时实施。定义 `_PROXY_SPECS: Dict[str, Tuple[Any, Tuple[str, ...]]]` 映射方法名 → (default, kw 参数名元组) + 通用 `_proxy(method_name, *args, **kwargs)` 方法。17 个代理方法改为表条目 + 薄包装。**BREAKING**：无。净减约 15-25 行。**风险**：参数转发签名差异（`eval_formula_zb` 含 8 个 kw）需细致设计，若抽象税过高则保留现状。

### 阶段 4：core 残余 safe_cast 精修（低优先级）

- **变更 C1：formula_module 残余 safe_cast**。`core/formula_module.py:847-850` 的 `try: float(tick_close) except: pass` 改为 `safe_float(tick_close, current.get("close"))`。净减 3 行。
- **变更 C2：runtime_mode_module 残余 helper 提取**。`:1196,1205` 的循环内 price 解析提取 `_safe_price(v, default=None)` helper；`:2021` 的 datetime 解析提取 `_safe_parse_dt(s)` helper。净减约 6 行。

### 阶段 5：metatest v6 量化评审升级（量化闭环）

- **变更 M1：scoring.py 新增第 21 维 `adapter_isomorphism`**（权重 4%，从 v5 20 维等比降权 4%）。评分标准：TqProvider/TqSdkBridge 转发方法表驱动覆盖率 ≥ 80% 满分，线性衰减。
- **变更 M2：runner.py 新增 `adapter_isomorphism` 采集**。Grep `def _forward\b|def _call_cached\b|def _call_simple\b` + 表驱动方法数 / 总转发方法数。
- **变更 M3：正反合测试 v6**。新建 `metatest/test_positive_adapter_isomorphism.py` 断言表驱动覆盖率。
- **变更 M4：metatest/README.md v6 文档更新**。新增第 21 维说明。

### 阶段 6：RULES + 全量回归

- **变更 D1：RULES.md 新增第 116 条**。文档化「外部 SDK 适配器转发同构必须表驱动（≥ 5 个同构转发方法收敛为 `_FORWARD_SPECS` 表 + 通用 `_forward` 方法）」。
- **变更 D2：全量回归**。metatest 总分 ≥ 95 且 21 维均 ≥ 80，eventtest 退出码 0，core/*.py ≤ 20000，essence_ratio ≥ 16%。

## Impact

- Affected specs: converge-meta-essence-v5-dispatcher-unification（v5 SubTask 14.5 闭合）
- Affected code: services/providers.py（TqProvider + TqSdkBridge + DataSourceManager）、core/formula_module.py、core/runtime_mode_module.py、metatest/scoring.py、metatest/runner.py、RULES.md、metatest/README.md

## ADDED Requirements

### Requirement: TqProvider 转发表驱动
The system SHALL provide a `_FORWARD_SPECS` table in TqProvider mapping method names to (default, kw_params) tuples, with a generic `_forward` method dispatching via the table.

#### Scenario: 表驱动转发
- **WHEN** TqProvider method called with bridge set
- **THEN** `_forward` dispatches to `self._bridge.METHOD(args)` and returns result
- **WHEN** TqProvider method called with bridge None
- **THEN** `_forward` returns the default from `_FORWARD_SPECS`

### Requirement: TqSdkBridge 缓存/简单方法表驱动
The system SHALL provide `_CACHED_TQ_CALLS` and `_SIMPLE_TQ_CALLS` tables in TqSdkBridge with generic `_call_cached` and `_call_simple` methods.

#### Scenario: 缓存方法表驱动
- **WHEN** cached TQ method called
- **THEN** `_call_cached` checks cache, calls SDK, writes cache, returns result (or default on exception)

### Requirement: metatest 第 21 维 adapter_isomorphism
The system SHALL score `adapter_isomorphism` dimension measuring TqProvider/TqSdkBridge forwarding method table-driven coverage.

#### Scenario: 表驱动覆盖率达标
- **WHEN** adapter_isomorphism computed
- **THEN** coverage = table-driven methods / total forwarding methods, ≥ 80% scores 100
