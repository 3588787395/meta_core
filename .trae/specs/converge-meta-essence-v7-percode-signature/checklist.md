# Checklist

## 架构工程师检查点（实施前自检）

- [x] 已阅读 spec.md「Why」章节并理解：v7 是 v6 `_CACHED_TQ_CALLS` + `_call_cached` 模式的「收尾闭合」，非新建机制、非拆分
- [x] 已理解本次迭代核心是「闭合 v6 残留 2 类同构缺口」（per-code 三方法 + 双签名），预估净减 ~47 行
- [x] 已阅读 `services/providers.py:7558-7573` 确认 `get_snapshot` per-code 循环结构（cache_key=`snapshot_{code}` / sdk_method=`get_market_snapshot(stock_code=code)` / 缓存写入无条件）
- [x] 已阅读 `services/providers.py:7608-7623` 确认 `get_stock_info` per-code 循环结构（cache_key=`stock_info_{code}` / sdk_method=`get_stock_info(stock_code=code)` / 缓存写入无条件）
- [x] 已阅读 `services/providers.py:7625-7641` 确认 `get_report_data` per-code 循环结构（cache_key=`report_{code}` / sdk_method=`get_market_snapshot(stock_code=code)` / 缓存写入 `if data:` 真值判断）
- [x] 已阅读 `services/providers.py:7511-7535` 确认 `get_stock_list` 双签名分支结构（新签名 cache_key=`stock_list_by_type_{market_id}` / 旧签名 cache_key=`stock_list_{market_id}`）
- [x] 已阅读 `services/providers.py:7575-7593` 确认 v6 `_CACHED_TQ_CALLS` 表 + `_call_cached` 通用方法签名（`_call_cached(self, method_name, key_args=(), *sdk_args, **sdk_kwargs)`）
- [x] 已阅读 `services/providers.py:7649-7660` 确认 v6 `_SIMPLE_TQ_CALLS` 表 + `_call_simple` 通用方法
- [x] 已运行 `python -m metatest.runner` 确认 v6 总分 98.49 PASS（21 维全 ≥ 80）
- [x] 已运行 `wc -l services/providers.py` 确认 v6 基线 8406 行
- [x] 已确认「合并非拆分」硬约束延续：`_PER_CODE_TQ_CALLS` 表 + `_call_cached_per_code` 方法净增 ≤ 15 行，per-code 三方法 + 双签名收敛净减 ≥ 47 行
- [x] 已确认阶段 1 各 Task 顺序：Task 1（per-code 表驱动）→ Task 2（双签名收敛），同改 services/providers.py 需串行
- [x] 已确认 `cache_only_if_truthy` 标志设计：保留 get_report_data 的 `if data:` 真值判断行为（空快照不污染缓存、不出现在结果字典），是表驱动收敛的关键
- [x] 已阅读 RULES.md 第 116 条并理解 v6 已落地的外部 SDK 适配器转发同构表驱动约束

## 评审工程师检查点（阶段 1：TqSdkBridge per-code 循环三方法表驱动）

### 变更 P1 — `_call_cached_per_code` + `_PER_CODE_TQ_CALLS` 表

- [x] `_PER_CODE_TQ_CALLS: Dict[str, Tuple[str, str, bool]]` 表在 TqSdkBridge 类中定义
- [x] `_PER_CODE_TQ_CALLS` 含 3 条目：`get_snapshot` → (`snapshot`, `get_market_snapshot`, False) / `get_stock_info` → (`stock_info`, `get_stock_info`, False) / `get_report_data` → (`report`, `get_market_snapshot`, True)
- [x] `_call_cached_per_code(self, method_name, codes)` 通用方法定义
- [x] `_call_cached_per_code` 含 `if not codes: return {}` 短路
- [x] `_call_cached_per_code` 含 `for code in codes:` 循环
- [x] `_call_cached_per_code` 含 `cache_key = cache_prefix + f"_{code}"` 构建（非 f-string 完整模板）
- [x] `_call_cached_per_code` 含缓存命中检查 `if cache_key in self._cache: all_data[code] = self._cache[cache_key]; continue`
- [x] `_call_cached_per_code` 含 `try: data = getattr(self._tq, sdk_method)(stock_code=code)` 调用
- [x] `_call_cached_per_code` 含 `if not cache_only_if_truthy or data:` 条件缓存写入（保留 get_report_data 真值判断）
- [x] `_call_cached_per_code` 含 `except Exception: pass` 异常兜底（保留原 get_snapshot/get_stock_info/get_report_data 静默兜底行为）
- [x] `get_snapshot` 方法体改为 `return self._call_cached_per_code("get_snapshot", codes)` 薄包装
- [x] `get_stock_info` 方法体改为 `return self._call_cached_per_code("get_stock_info", codes)` 薄包装
- [x] `get_report_data` 方法体改为 `return self._call_cached_per_code("get_report_data", codes)` 薄包装
- [x] Grep `cache_key = f'snapshot_|cache_key = f'stock_info_|cache_key = f'report_` 在 TqSdkBridge 类内 = 0
- [x] Grep `if data:` 在 `get_report_data` 上下文 = 0（收敛到 `cache_only_if_truthy` 标志）
- [x] `python -m pytest metatest/ -x -q` 退出码 0
- [x] 净减行数 ≥ 25 行

## 评审工程师检查点（阶段 2：get_stock_list 双签名收敛）

### 变更 P2 — get_stock_list 重构为双 `_call_cached` 调用

- [x] `_CACHED_TQ_CALLS` 表新增 2 条目：`get_stock_list_by_type` → (`stock_list_by_type`, `get_stock_list`, `[]`) / `get_stock_list` → (`stock_list`, `get_stock_list`, `[]`)
- [x] `get_stock_list` 方法体重构为 `if kwargs.get('list_type') is not None: return self._call_cached("get_stock_list_by_type", (market_id,), market_id, **kwargs)` + `return self._call_cached("get_stock_list", (market_id,), market=str(market_id))`
- [x] 旧签名 `market=str(market_id)` 关键字形式保留（通过 `**sdk_kwargs` 透传）
- [x] 新签名 `**kwargs`（含 `list_type`）透传保留
- [x] Grep `cache_key = f'stock_list` 在 TqSdkBridge 类内 = 0
- [x] Grep `if not self\._tq: return` 在 `get_stock_list` 上下文 = 0（收敛到 `_call_cached`）
- [x] `python -m pytest metatest/ -x -q` 退出码 0
- [x] 净减行数 ≥ 15 行

## 评审工程师检查点（阶段 3：metatest v7 量化评审升级）

### 变更 M1 — scoring.py adapter_isomorphism 维度升级
- [x] `adapter_isomorphism` 维度覆盖率阈值从 80% 提升至 90%
- [x] 权重保持 4%（不变）
- [x] 21 维权重和 = 100%
- [x] PASS 条件保持（总分 ≥ 95 且 21 维均 ≥ 80）

### 变更 M2 — runner.py adapter_isomorphism 采集扩展
- [x] `_collect_adapter_isomorphism` 新增 `_PER_CODE_TQ_CALLS` 表条目数采集
- [x] `_collect_adapter_isomorphism` 新增 `_call_cached_per_code` 方法定义检查
- [x] 覆盖率计算含 4 表（`_FORWARD_SPECS` + `_CACHED_TQ_CALLS` 含 2 新条目 + `_SIMPLE_TQ_CALLS` + `_PER_CODE_TQ_CALLS` 3 条目）
- [x] 覆盖方法数 = 4 通用转发器（`_forward` / `_call_cached` / `_call_simple` / `_call_cached_per_code`）对应条目和
- [x] `meta_unification` 字段新增 `per_code_table_entries` / `dual_signature_converged` 2 字段
- [x] `report.json` 含 v7 明细

### 变更 M3 — 正反合测试 v7 升级
- [x] `test_three_forward_tables_exist_with_entries` 升级含 `_PER_CODE_TQ_CALLS` 表存在 + 条目数 ≥ 3 断言（或重命名为 `test_four_forward_tables_exist_with_entries`）
- [x] `test_three_generic_forwarder_methods_defined` 升级含 `_call_cached_per_code` 方法定义断言（或重命名为 `test_four_generic_forwarder_methods_defined`）
- [x] `test_adapter_forward_coverage_above_threshold` 覆盖率阈值从 80% 提升至 90%
- [x] 覆盖率计算含 `_PER_CODE_TQ_CALLS` 3 条目
- [x] 新增 `test_get_stock_list_dual_signature_converged`：断言 `_CACHED_TQ_CALLS` 含 `get_stock_list_by_type` + `get_stock_list` 2 条目
- [x] 新增 `test_per_code_truthy_flag_preserved`：断言 `_PER_CODE_TQ_CALLS["get_report_data"]` 第三元素（cache_only_if_truthy）= True
- [x] `python -m pytest metatest/test_positive_adapter_isomorphism.py -v` 退出码 0

### 变更 M4 — metatest/README.md v7 文档更新
- [x] 新增 per-code 循环表驱动说明（`_PER_CODE_TQ_CALLS` + `_call_cached_per_code` + `cache_only_if_truthy` 标志）
- [x] 新增双签名收敛说明（get_stock_list 双 `_call_cached` 调用）
- [x] 更新覆盖率计算说明（4 表 + 4 通用转发器）

## 评审工程师检查点（阶段 4：RULES + 全量回归）

### RULES.md 117
- [x] 第 117 条 — 外部 SDK 适配器 per-code 循环调用表驱动（`_PER_CODE_TQ_CALLS` + `_call_cached_per_code` + `cache_only_if_truthy` 标志）+ 双签名同构方法收敛（`_CACHED_TQ_CALLS` 多条目 + if/else 双调用）
- [x] Grep `^117\.` 在 RULES.md = 1

### 全量回归
- [x] `python -m pytest metatest/ -x -q` 全量测试通过 — 实测 1264 passed, 53 skipped，退出码 0
- [x] `python -m metatest.runner` 总分 ≥ 95 且 21 维均 ≥ 80 判定 PASS — 实测总分 98.49 PASS
- [x] `python -m eventtest.run_eventtest` 退出码 0（全绿）— 实测退出码 0
- [x] `wc -l core/*.py | tail -1` ≤ 20,000（v6 基线 19928，v7 不改 core）— 实测 19928 total
- [x] `wc -l services/providers.py` 净减 ≥ 40 行（v6 基线 8406，v7 目标 ≤ 8366）— 实测 8366 行，净减 40 行
- [x] Grep `cache_key = f'snapshot_|cache_key = f'stock_info_|cache_key = f'report_|cache_key = f'stock_list` 在 TqSdkBridge 类内 = 0（per-code + 双签名全部收敛）— 实测 TqSdkBridge 区域内 0 匹配
- [x] Grep `if data:` 在 `get_report_data` 上下文 = 0（收敛到 `cache_only_if_truthy` 标志）— 实测全文件 0 匹配
- [x] essence_ratio ≥ 16%（metatest 维度满分）— 实测 16.97%，维度得分 100
- [x] adapter_isomorphism 维度 = 100（覆盖率 ≥ 90%，v7 升级阈值后仍 100%）— 实测 100.0/100，覆盖率 100%（34/34 方法表驱动）
- [x] 无任何变更净增行数（essence_ratio 反测试通过）— core 不变 + providers.py 净减 40 行
- [x] DZH↔TDX 双格式互转保真（oop_inheritance_depth 维度满分，v6 保持）— 实测维度得分 100（4/4 条件满足）
- [x] MetaDispatcher 继承结构正确（dispatcher_isomorphism 维度满分，v5/v6 保持）— 实测维度得分 100
- [x] 3 个 in-process 运行时验证测试全绿（runtime_verification 维度满分，v5/v6 保持）— 实测维度得分 100（3/3 通过）
- [x] eventtest 全绿（eventtest_regression 维度满分，v5/v6 保持）— 实测维度得分 100（退出码 0）

## 第七层洞察根因检查点（评审工程师最终验收）

- [x] **per-code 循环变体元模式延伸**：v6 `_call_cached` 的「cache_key 构建 → 缓存检查 → SDK 调用 → 缓存写入 → 异常兜底」骨架在 per-code 循环场景有自然延伸，v7 通过 `_call_cached_per_code` 闭合该变体，证明 v6 表驱动模式具有可扩展性
- [x] **`cache_only_if_truthy` 标志保留行为差异**：get_report_data 的 `if data:` 真值判断（空快照不污染缓存、不出现在结果字典）通过表条目的布尔标志保留，非消除差异，是表驱动收敛「保留业务语义」的关键设计
- [x] **双签名变体元模式延伸**：get_stock_list 同一方法两分支均符合 `_call_cached` 骨架，通过向 `_CACHED_TQ_CALLS` 追加 2 条目 + if/else 双调用收敛，证明「同一方法多签名」也是 `_call_cached` 模式的自然延伸
- [x] **TqSdkBridge 缓存类调用全收敛**：v7 收尾后，TqSdkBridge 所有缓存类调用统一到 `_call_cached` / `_call_cached_per_code` 两个通用方法，无独立过程式 per-code 循环方法或双签名展开
- [x] **非新建机制非拆分**：本次迭代核心 = 闭合 v6 残留 2 类同构缺口，`_PER_CODE_TQ_CALLS` 表 + `_call_cached_per_code` 方法净增 ≤ 15 行，per-code 三方法 + 双签名收敛净减 ≥ 47 行，净减 ≥ 32 行
- [x] **DataSourceManager 封存确认**：v6 评估正确（抽象税过高），v7 不再评估，封存决策记录在 spec.md「Why」章节
