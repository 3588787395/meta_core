# 元模式本质收敛 v7：TqSdkBridge per-code 循环 + 双签名收尾闭合 Spec

## Why

v6 元模式收敛已完成（98.49 PASS，21 维全 ≥ 80，core 19928 行，essence_ratio 16.97%）。用户要求「继续架构洞察迭代优化，完善元模式精减代码」。经架构工程师第七层深度洞察调研（覆盖 TqSdkBridge per-code 循环方法 + get_stock_list 双签名 + DataSourceManager 重新评估 + 全库残余同构扫描），确认 v6 的 `_call_cached` / `_call_simple` 模式已成熟，存在 2 个该模式的自然延伸同构族未闭合：

1. **TqSdkBridge per-code 循环三方法**（`services/providers.py:7558-7641`）共享「per-code 循环 → cache_key 构建 → 缓存检查 → SDK 调用 → 缓存写入 → 异常兜底」8 步骨架，仅 cache_prefix / sdk_method / 缓存写入条件（get_report_data 的 `if data:` 真值判断）3 维差异。预估净减 ~28 行。
2. **get_stock_list 双签名分支**（`services/providers.py:7511-7535`）新/旧签名两分支均符合 `_call_cached` 骨架，可通过向 `_CACHED_TQ_CALLS` 追加 2 条目 + if/else 双调用收敛。预估净减 ~19 行。

**第七层洞察（本次迭代新增的元模式深化）**：v6 建立的 `_CACHED_TQ_CALLS` + `_call_cached`（带缓存 SDK 调用）模式在 v7 有两个自然延伸——per-code 循环变体（调研 1）与双签名变体（调研 2）。这证明 v6 的表驱动模式具有可扩展性，v7 是该模式的「收尾闭合」，使 TqSdkBridge 的所有缓存类调用统一到 `_call_cached` / `_call_cached_per_code` 两个通用方法。

同时，调研显式排除 3 项无 ROI 目标：
- **DataSourceManager 17 代理**（v6 评估正确，三方案均不符合「净减显著 + 抽象税低」双标准，封存不再评估）
- **跨类 cache_key 同构**（TqDll+TqSdk SDK 调用机制差异致命）
- **storage.py `_collect_*_sectors`**（字段提取差异过多，抽象税 > 节省）

## What Changes

### 阶段 1：TqSdkBridge per-code 循环三方法表驱动（高优先级）

- **变更 P1：提取 `_call_cached_per_code` + `_PER_CODE_TQ_CALLS` 表**。定义 `_PER_CODE_TQ_CALLS: Dict[str, Tuple[str, str, bool]]` 映射方法名 → (cache_prefix, sdk_method, cache_only_if_truthy) + 通用 `_call_cached_per_code(self, method_name, codes)` 方法。3 个方法（`get_snapshot` / `get_stock_info` / `get_report_data`）改为薄包装委托。**BREAKING**：无。净减约 28 行。**关键**：`cache_only_if_truthy` 标志保留 get_report_data 的 `if data:` 真值判断行为（空快照不污染缓存、不出现在结果字典）。

### 阶段 2：get_stock_list 双签名收敛（高优先级）

- **变更 P2：get_stock_list 重构为双 `_call_cached` 调用**。向 `_CACHED_TQ_CALLS` 追加 2 条目（`get_stock_list_by_type` + `get_stock_list`），get_stock_list 方法体改为 if/else 双 `_call_cached` 调用。**BREAKING**：无。净减约 19 行。**关键**：保留旧签名 `market=str(market_id)` 关键字形式与新签名 `**kwargs` 透传。

### 阶段 3：metatest v7 量化评审升级（量化闭环）

- **变更 M1：scoring.py adapter_isomorphism 维度升级**。评分标准从「表驱动覆盖率 ≥ 80%」升级为「per-code + 双签名覆盖率 ≥ 90%」（v6 已 100%，v7 收尾后仍 100%）。权重保持 4%。
- **变更 M2：runner.py adapter_isomorphism 采集扩展**。新增 `_PER_CODE_TQ_CALLS` 条目数采集 + get_stock_list 双条目计入。
- **变更 M3：正反合测试 v7**。升级 `metatest/test_positive_adapter_isomorphism.py` 断言 `_PER_CODE_TQ_CALLS` 表存在 + 3 方法覆盖 + get_stock_list 双条目。
- **变更 M4：metatest/README.md v7 文档更新**。新增 per-code + 双签名说明。

### 阶段 4：RULES + 全量回归

- **变更 D1：RULES.md 新增第 117 条**。文档化「per-code 循环 SDK 调用必须表驱动（≥ 3 个同构 per-code 方法收敛为 `_PER_CODE_TQ_CALLS` 表 + 通用 `_call_cached_per_code` 方法，含 `cache_only_if_truthy` 标志保留真值判断行为差异）」。
- **变更 D2：全量回归**。metatest 总分 ≥ 95 且 21 维均 ≥ 80，eventtest 退出码 0，core/*.py ≤ 20000，services/providers.py 净减 ≥ 40 行，essence_ratio ≥ 16%。

## Impact

- Affected specs: converge-meta-essence-v6-adapter-forwarding（v6 adapter_isomorphism 维度升级）
- Affected code: services/providers.py（TqSdkBridge per-code 三方法 + get_stock_list 双签名）、metatest/scoring.py、metatest/runner.py、metatest/test_positive_adapter_isomorphism.py、metatest/README.md、RULES.md

## ADDED Requirements

### Requirement: TqSdkBridge per-code 循环表驱动
The system SHALL provide a `_PER_CODE_TQ_CALLS` table in TqSdkBridge mapping method names to (cache_prefix, sdk_method, cache_only_if_truthy) tuples, with a generic `_call_cached_per_code` method dispatching via the table.

#### Scenario: per-code 循环表驱动
- **WHEN** per-code TQ method called with codes list
- **THEN** `_call_cached_per_code` iterates codes, builds cache_key, checks cache, calls SDK, writes cache (conditionally per cache_only_if_truthy), returns result dict

### Requirement: get_stock_list 双签名收敛
The system SHALL consolidate get_stock_list dual-signature branches into two `_CACHED_TQ_CALLS` entries with if/else dispatch.

#### Scenario: 双签名分派
- **WHEN** get_stock_list called with list_type kwarg
- **THEN** dispatches to `_call_cached("get_stock_list_by_type", ...)`
- **WHEN** get_stock_list called without list_type
- **THEN** dispatches to `_call_cached("get_stock_list", ...)` with market=str(market_id)
