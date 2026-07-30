# Tasks

本规范按「架构工程师（实施）→ 评审工程师（验证）」迭代流程分 4 阶段实施，覆盖 TqSdkBridge per-code 循环三方法表驱动 + get_stock_list 双签名收敛 + metatest v7 量化评审升级 + RULES 117。**第七层洞察（per-code 循环 + 双签名收尾闭合）**：v6 建立的 `_CACHED_TQ_CALLS` + `_call_cached`（带缓存 SDK 调用）模式在 v7 有两个自然延伸——per-code 循环变体（每个 code 独立 cache_key，循环内缓存检查/写入）与双签名变体（同一方法两分支均符合 `_call_cached` 骨架）。这证明 v6 表驱动模式具有可扩展性，v7 是该模式的「收尾闭合」，使 TqSdkBridge 所有缓存类调用统一到 `_call_cached` / `_call_cached_per_code` 两个通用方法。本次迭代核心 = 闭合 v6 残留 2 类同构缺口（per-code 三方法 + 双签名），非新建机制、非拆分，预估净减 ~47 行。

## 阶段 1：TqSdkBridge per-code 循环三方法表驱动（高优先级）

### 架构工程师任务

- [x] Task 1: 变更 P1 — 提取 `_call_cached_per_code` + `_PER_CODE_TQ_CALLS` 表
  - [x] SubTask 1.1: Read `services/providers.py:7558-7573` 确认 `get_snapshot` per-code 循环结构：cache_key=`snapshot_{code}` / sdk_method=`get_market_snapshot(stock_code=code)` / 缓存写入条件=无条件（try 块成功即写）
  - [x] SubTask 1.2: Read `services/providers.py:7608-7623` 确认 `get_stock_info` per-code 循环结构：cache_key=`stock_info_{code}` / sdk_method=`get_stock_info(stock_code=code)` / 缓存写入条件=无条件
  - [x] SubTask 1.3: Read `services/providers.py:7625-7641` 确认 `get_report_data` per-code 循环结构：cache_key=`report_{code}` / sdk_method=`get_market_snapshot(stock_code=code)` / 缓存写入条件=`if data:` 真值判断（空快照不污染缓存、不出现在结果字典）
  - [x] SubTask 1.4: 在 TqSdkBridge 类中定义 `_PER_CODE_TQ_CALLS: Dict[str, Tuple[str, str, bool]]` 表，映射方法名 → (cache_prefix, sdk_method, cache_only_if_truthy)，覆盖 3 个方法：`get_snapshot` → (`snapshot`, `get_market_snapshot`, False) / `get_stock_info` → (`stock_info`, `get_stock_info`, False) / `get_report_data` → (`report`, `get_market_snapshot`, True)
  - [x] SubTask 1.5: 定义通用 `_call_cached_per_code(self, method_name, codes)` 方法骨架：`if not codes: return {}` → `all_data = {}` → `for code in codes:` → `cache_key = cache_prefix + f"_{code}"` → `if cache_key in self._cache: all_data[code] = self._cache[cache_key]; continue` → `try: data = getattr(self._tq, sdk_method)(stock_code=code); if not cache_only_if_truthy or data: self._cache[cache_key] = data; all_data[code] = data except Exception: pass` → `return all_data`
  - [x] SubTask 1.6: `get_snapshot` 方法体改为 `return self._call_cached_per_code("get_snapshot", codes)` 薄包装
  - [x] SubTask 1.7: `get_stock_info` 方法体改为 `return self._call_cached_per_code("get_stock_info", codes)` 薄包装
  - [x] SubTask 1.8: `get_report_data` 方法体改为 `return self._call_cached_per_code("get_report_data", codes)` 薄包装
  - [x] SubTask 1.9: Grep `cache_key = f'snapshot_|cache_key = f'stock_info_|cache_key = f'report_` 在 TqSdkBridge 类内 = 0（仅 `_call_cached_per_code` 内用 `cache_prefix + f"_{code}"`）
  - [x] SubTask 1.10: Grep `if data:` 在 `get_report_data` 上下文 = 0（已收敛到 `_PER_CODE_TQ_CALLS` 的 `cache_only_if_truthy` 标志）
  - [x] SubTask 1.11: `python -m pytest metatest/ -x -q 2>&1 | tail -5` 验证无回归
  - [x] SubTask 1.12: 净减行数 ≥ 25 行（3 方法各 ~9 行 → 薄包装 1 行 + 表 1 行共享）

## 阶段 2：get_stock_list 双签名收敛（高优先级）

### 架构工程师任务

- [x] Task 2: 变更 P2 — get_stock_list 重构为双 `_call_cached` 调用
  - [x] SubTask 2.1: Read `services/providers.py:7511-7535` 确认 get_stock_list 双签名分支结构：新签名（`list_type` kwarg 存在）cache_key=`stock_list_by_type_{market_id}` / sdk 调用 `self._tq.get_stock_list(market_id, **kwargs)` / 旧签名 cache_key=`stock_list_{market_id}` / sdk 调用 `self._tq.get_stock_list(market=str(market_id))`
  - [x] SubTask 2.2: 向 `_CACHED_TQ_CALLS` 表追加 2 条目：`"get_stock_list_by_type"` → (`stock_list_by_type`, `get_stock_list`, `[]`) / `"get_stock_list"` → (`stock_list`, `get_stock_list`, `[]`)
  - [x] SubTask 2.3: get_stock_list 方法体重构为 `if kwargs.get('list_type') is not None: return self._call_cached("get_stock_list_by_type", (market_id,), market_id, **kwargs)` + `return self._call_cached("get_stock_list", (market_id,), market=str(market_id))`
  - [x] SubTask 2.4: 验证 `_call_cached` 签名兼容：`_call_cached(self, method_name, key_args=(), *sdk_args, **sdk_kwargs)` — `key_args=(market_id,)` 用于 cache_key 构建（`stock_list_by_type_{market_id}` / `stock_list_{market_id}`），`*sdk_args` 与 `**sdk_kwargs` 透传 SDK 调用
  - [x] SubTask 2.5: 验证旧签名 `market=str(market_id)` 关键字形式保留（kwargs 形式透传）
  - [x] SubTask 2.6: 验证新签名 `**kwargs`（含 `list_type`）透传保留
  - [x] SubTask 2.7: Grep `cache_key = f'stock_list` 在 TqSdkBridge 类内 = 0（已收敛到 `_call_cached`）
  - [x] SubTask 2.8: `python -m pytest metatest/ -x -q 2>&1 | tail -5` 验证无回归
  - [x] SubTask 2.9: 净减行数 ≥ 15 行（25 行 → ~5 行双调用 + 表 2 行共享）

## 阶段 3：metatest v7 量化评审升级（量化闭环）

### 架构工程师任务

- [x] Task 3: 变更 M1 — scoring.py adapter_isomorphism 维度升级
  - [x] SubTask 3.1: Read `metatest/scoring.py` 确认 v6 第 21 维 `adapter_isomorphism` 评分标准（覆盖率 ≥ 80% 满分）
  - [x] SubTask 3.2: 评分标准升级：覆盖率阈值从 80% 提升至 90%（v6 已 100%，v7 收尾后仍 100%，阈值提升确保未来回归被捕获）
  - [x] SubTask 3.3: 权重保持 4%（v6 已设置，不变）
  - [x] SubTask 3.4: 21 维权重和 = 100%，PASS 条件保持（总分 ≥ 95 且 21 维均 ≥ 80）

- [x] Task 4: 变更 M2 — runner.py adapter_isomorphism 采集扩展
  - [x] SubTask 4.1: Read `metatest/runner.py` 确认 v6 `_collect_adapter_isomorphism` 实现（AST 解析三表条目数 + 三通用转发器方法定义 → 覆盖率）
  - [x] SubTask 4.2: 扩展采集：新增 `_PER_CODE_TQ_CALLS` 表条目数采集 + `_call_cached_per_code` 方法定义检查
  - [x] SubTask 4.3: 覆盖率计算扩展：总表驱动方法数 = `_FORWARD_SPECS` + `_CACHED_TQ_CALLS`（含 2 新条目）+ `_SIMPLE_TQ_CALLS` + `_PER_CODE_TQ_CALLS`（3 条目）；覆盖方法数 = 4 通用转发器对应条目和
  - [x] SubTask 4.4: `meta_unification` 字段新增 `per_code_table_entries` / `dual_signature_converged` 2 字段
  - [x] SubTask 4.5: `report.json` 含 v7 明细

- [x] Task 5: 变更 M3 — 正反合测试 v7 升级
  - [x] SubTask 5.1: Read `metatest/test_positive_adapter_isomorphism.py` 确认 v6 三表 + 三通用转发器断言
  - [x] SubTask 5.2: 升级 `test_three_forward_tables_exist_with_entries`：新增 `_PER_CODE_TQ_CALLS` 表存在 + 条目数 ≥ 3 断言
  - [x] SubTask 5.3: 升级 `test_three_generic_forwarder_methods_defined`：新增 `_call_cached_per_code` 方法定义断言（重命名为 `test_four_generic_forwarder_methods_defined`）
  - [x] SubTask 5.4: 升级 `test_adapter_forward_coverage_above_threshold`：覆盖率阈值从 80% 提升至 90% + 覆盖率计算含 `_PER_CODE_TQ_CALLS` 3 条目
  - [x] SubTask 5.5: 新增 `test_get_stock_list_dual_signature_converged`：断言 `_CACHED_TQ_CALLS` 含 `get_stock_list_by_type` + `get_stock_list` 2 条目（双签名收敛）
  - [x] SubTask 5.6: 新增 `test_per_code_truthy_flag_preserved`：断言 `_PER_CODE_TQ_CALLS["get_report_data"]` 第三元素（cache_only_if_truthy）= True
  - [x] SubTask 5.7: `python -m pytest metatest/test_positive_adapter_isomorphism.py -v` 退出码 0

- [x] Task 6: 变更 M4 — metatest/README.md v7 文档更新
  - [x] SubTask 6.1: Read `metatest/README.md` 确认 v6 第 21 维说明
  - [x] SubTask 6.2: 新增 per-code 循环表驱动说明（`_PER_CODE_TQ_CALLS` + `_call_cached_per_code` + `cache_only_if_truthy` 标志）
  - [x] SubTask 6.3: 新增双签名收敛说明（get_stock_list 双 `_call_cached` 调用）
  - [x] SubTask 6.4: 更新覆盖率计算说明（4 表 + 4 通用转发器）

## 阶段 4：RULES + 全量回归

### 架构工程师任务

- [x] Task 7: 变更 D1 — RULES.md 新增第 117 条
  - [x] SubTask 7.1: 在 RULES.md 末尾新增第 117 条：「外部 SDK 适配器 per-code 循环调用必须表驱动（≥ 3 个同构 per-code 方法收敛为 `_PER_CODE_TQ_CALLS` 表 + 通用 `_call_cached_per_code` 方法，含 `cache_only_if_truthy` 标志保留真值判断行为差异）；双签名同构方法必须收敛为 `_CACHED_TQ_CALLS` 多条目 + if/else 双调用，禁止重新引入独立 per-code 循环方法或双签名过程式展开」
  - [x] SubTask 7.2: Grep `^117\.` 在 RULES.md = 1

### 评审工程师任务

- [x] Task 8: 变更 D2 — 全量回归
  - [x] SubTask 8.1: `python -m pytest metatest/ -x -q` 退出码 0（全量测试通过）— 实测 1264 passed, 53 skipped，退出码 0
  - [x] SubTask 8.2: `python -m metatest.runner` 总分 ≥ 95 且 21 维均 ≥ 80，状态 PASS — 实测总分 98.49 PASS，21 维均 ≥ 80
  - [x] SubTask 8.3: `python -m eventtest.run_eventtest` 退出码 0（全绿）— 实测退出码 0（全部通过）
  - [x] SubTask 8.4: `wc -l core/*.py | tail -1` ≤ 20,000（v6 基线 19928，v7 不改 core）— 实测 19928 total ≤ 20000
  - [x] SubTask 8.5: `wc -l services/providers.py` 净减 ≥ 40 行（v6 基线 8406，v7 目标 ≤ 8366）— 实测 8366 行，净减 40 行
  - [x] SubTask 8.6: Grep `cache_key = f'snapshot_|cache_key = f'stock_info_|cache_key = f'report_|cache_key = f'stock_list` 在 TqSdkBridge 类内 = 0（per-code + 双签名全部收敛）— 实测 TqSdkBridge 区域（7481-7645）内 0 匹配，5 处匹配均在兄弟类（TqConnector/TqDllProvider）
  - [x] SubTask 8.7: Grep `if data:` 在 `get_report_data` 上下文 = 0（收敛到 `cache_only_if_truthy` 标志）— 实测全文件 0 匹配
  - [x] SubTask 8.8: essence_ratio ≥ 16%（metatest 维度满分）— 实测 16.97%，维度得分 100
  - [x] SubTask 8.9: adapter_isomorphism 维度 = 100（覆盖率 ≥ 90%，v7 升级阈值后仍 100%）— 实测 100.0/100，覆盖率 100%（34/34 方法表驱动）
  - [x] SubTask 8.10: 无任何变更净增行数（essence_ratio 反测试通过）— core 不变 + providers.py 净减 40 行
  - [x] SubTask 8.11: DZH↔TDX 双格式互转保真（oop_inheritance_depth 维度满分，v6 保持）— 实测维度得分 100（4/4 条件满足）
  - [x] SubTask 8.12: MetaDispatcher 继承结构正确（dispatcher_isomorphism 维度满分，v5/v6 保持）— 实测维度得分 100（基类存在/EventBus 继承/ConfigStore 继承/EventDriver 独立/骨架占比 60%）
  - [x] SubTask 8.13: 3 个 in-process 运行时验证测试全绿（runtime_verification 维度满分，v5/v6 保持）— 实测维度得分 100（3/3 replay/simulation/mode-switch 通过）
  - [x] SubTask 8.14: eventtest 全绿（eventtest_regression 维度满分，v5/v6 保持）— 实测维度得分 100（退出码 0）

# Task Dependencies
- Task 2 依赖 Task 1（同改 services/providers.py 的 TqSdkBridge 类，避免冲突；Task 1 先定义 `_call_cached_per_code` 与 `_PER_CODE_TQ_CALLS`，Task 2 后追加 `_CACHED_TQ_CALLS` 条目与重构 get_stock_list）
- Task 3-6 依赖 Task 1-2（采集与测试需表驱动已落地）
- Task 7 依赖 Task 1-2（RULES 文档化已落地成果）
- Task 8 依赖 Task 1-7（全量回归）
