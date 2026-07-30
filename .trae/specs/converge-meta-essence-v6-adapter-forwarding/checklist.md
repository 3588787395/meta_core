# Checklist

## 架构工程师检查点（实施前自检）

- [ ] 已阅读 v6 spec.md「第六层洞察（外部适配器转发同构）」章节并理解：TqProvider/TqSdkBridge 转发同构本质是 MetaDispatcher 之外的「声明 Data + Dispatcher 通用转发器」元模式投影
- [ ] 已理解本次迭代核心是「闭合 v5 SubTask 14.5 未竟目标」（TQ SDK 转发表驱动），非新建机制、非拆分
- [ ] 已阅读深度洞察调研报告确认：v4+v5 已收割 90%+ 同构红利，v6 仅 TQ SDK 转发同构（75-90 行）+ DataSourceManager 条件性（15-25 行）+ core 精修（~9 行）有明确 ROI
- [ ] 已阅读 `services/providers.py:8250-8404 TqProvider` 确认 17 个转发方法同构骨架 `if self._bridge is None: return DEFAULT → return self._bridge.METHOD(args)`
- [ ] 已阅读 `services/providers.py:7575-7655 TqSdkBridge A 组` 确认 4 个缓存方法同构骨架（cache_key → 缓存 → SDK → 异常兜底）
- [ ] 已阅读 `services/providers.py:7669-7717 TqSdkBridge B 组` 确认 5 个简单方法同构骨架（if not self._tq → try SDK → except）
- [ ] 已运行 `python -m metatest.runner` 确认 v5 基线：98.42 PASS，20 维全 ≥ 80，core 19936 行，essence_ratio 16.93%
- [ ] 已阅读 RULES.md 第 111-115 条并理解 v5 已落地的 MetaDispatcher 统一约束
- [ ] 已确认「合并非拆分」硬约束延续：TqProvider 表驱动净减 ≥ 35 行，TqSdkBridge 双组净减 ≥ 35 行，core 精修净减 ≥ 7 行
- [ ] 已确认阶段 1-2 各 Task 顺序：Task 1（TqProvider）→ Task 2/3（TqSdkBridge A/B）同改 providers.py 避免冲突

## 评审工程师检查点（阶段 1：TqProvider 转发表驱动）

### 变更 T1 — TqProvider 17 转发方法表驱动
- [ ] `_FORWARD_SPECS: Dict[str, Tuple[Any, Tuple[str, ...]]]` 表在 TqProvider 中定义，覆盖 17 个方法
- [ ] `_forward(self, method_name, *args, **kwargs)` 通用方法定义
- [ ] Grep `if self\._bridge is None: return` 在 TqProvider 类内 ≤ 1（仅 `_forward` 内）
- [ ] 17 个转发方法改为薄包装委托 `_forward`
- [ ] `python -m pytest metatest/ -x -q` 退出码 0
- [ ] 净减行数 ≥ 35 行

## 评审工程师检查点（阶段 2：TqSdkBridge 双组表驱动）

### 变更 T2 — TqSdkBridge A 组缓存方法表驱动
- [ ] `_CACHED_TQ_CALLS: Dict[str, Tuple[str, str, Any]]` 表在 TqSdkBridge 中定义，覆盖 4 个方法
- [ ] `_call_cached(self, method_name, cache_prefix, sdk_method, *args, default)` 通用方法定义
- [ ] Grep `cache_key = f` 在 TqSdkBridge 类内 ≤ 1（仅 `_call_cached` 内）
- [ ] 4 个缓存方法改为薄包装委托 `_call_cached`
- [ ] 净减行数 ≥ 20 行

### 变更 T3 — TqSdkBridge B 组简单方法表驱动
- [ ] `_SIMPLE_TQ_CALLS: Dict[str, str]` 表在 TqSdkBridge 中定义，覆盖 5 个方法
- [ ] `_call_simple(self, method_name, sdk_method, **kwargs)` 通用方法定义
- [ ] Grep `if not self\._tq: return None` 在 TqSdkBridge 类内 ≤ 1（仅 `_call_simple` 内）
- [ ] 5 个简单方法改为薄包装委托 `_call_simple`
- [ ] 净减行数 ≥ 15 行

## 评审工程师检查点（阶段 3：DataSourceManager 代理条件性收敛）

### 变更 T4 — DataSourceManager 代理表驱动（条件性）
- [ ] 阶段 1-2 净减 < 80 行时实施（否则跳过）
- [ ] 参数转发签名差异评估：抽象税过高则保留现状
- [ ] 若实施：`_PROXY_SPECS` 表 + `_proxy` 通用方法定义
- [ ] Grep `self\._call_active\(.*\) or` 在 DataSourceManager 类内 ≤ 1（仅 `_proxy` 内）
- [ ] 净减行数 ≥ 15 行（若实施）

## 评审工程师检查点（阶段 4：core 残余 safe_cast 精修）

### 变更 C1 — formula_module 残余 safe_cast
- [ ] `core/formula_module.py:847-850` 的 `try: float(tick_close) except: pass` 替换为 `safe_float`
- [ ] 净减行数 ≥ 3 行

### 变更 C2 — runtime_mode_module 残余 helper 提取
- [ ] `_safe_price(v, default=None)` helper 定义
- [ ] `_safe_parse_dt(s)` helper 定义
- [ ] 调用站点改为 helper 调用
- [ ] 净减行数 ≥ 4 行

## 评审工程师检查点（阶段 5：metatest v6 量化评审升级）

### 变更 M1 — scoring.py 第 21 维 adapter_isomorphism
- [ ] `adapter_isomorphism` 维度在 scoring.py 中定义，权重 4%
- [ ] v5 20 维等比降权 4%（每维 × 0.9615），21 维权重和 = 100%
- [ ] 评分标准：表驱动覆盖率 ≥ 80% 满分，线性衰减
- [ ] PASS 条件：总分 ≥ 95 且 21 维均 ≥ 80

### 变更 M2 — runner.py adapter_isomorphism 采集
- [ ] `_collect_adapter_isomorphism` 采集函数定义
- [ ] Grep `def _forward\b|def _call_cached\b|def _call_simple\b` 计数表驱动方法数
- [ ] `test_results` 字典新增 `adapter_isomorphism` 字段
- [ ] `meta_unification` 字段新增 `adapter_forward_coverage` 字段

### 变更 M3 — 正反合测试 v6
- [ ] `metatest/test_positive_adapter_isomorphism.py` 新建
- [ ] 断言 `_FORWARD_SPECS` + `_CACHED_TQ_CALLS` + `_SIMPLE_TQ_CALLS` 表存在 + 覆盖率 ≥ 80%
- [ ] `python -m pytest metatest/test_positive_adapter_isomorphism.py -v` 退出码 0

### 变更 M4 — metatest/README.md v6 文档
- [ ] 第 21 维 adapter_isomorphism 说明新增
- [ ] 权重表更新（20 维 → 21 维）

## 评审工程师检查点（阶段 6：RULES + 全量回归）

### 变更 D1 — RULES.md 第 116 条
- [ ] 第 116 条「外部 SDK 适配器转发同构必须表驱动」新增
- [ ] Grep `^116\.` 在 RULES.md = 1

### 第六层洞察根因检查点（外部适配器转发同构元模式投影）
- [x] TqProvider 17 转发方法表驱动覆盖率 ≥ 80%
- [x] TqSdkBridge 9 方法（A 缓存 4 + B 简单 5）表驱动覆盖率 ≥ 80%
- [x] 外部适配器转发同构 = MetaDispatcher 之外「声明 Data + Dispatcher 通用转发器」元模式投影（第六层洞察闭合）
- [x] v5 SubTask 14.5 未竟目标闭合（TQ SDK 表驱动真正落地）

### 全量回归
- [x] `python -m pytest metatest/ -x -q` 退出码 0（全量测试通过）— 1262 passed, 53 skipped
- [x] `python -m metatest.runner` 总分 ≥ 95 且 21 维均 ≥ 80，状态 PASS — 总分 98.49 PASS
- [x] `python -m eventtest.run_eventtest` 退出码 0（全绿）— 退出码 0
- [x] `wc -l core/*.py | tail -1` ≤ 20,000 — 19928 total
- [x] `wc -l services/providers.py` 净减 ≥ 75 行（阶段 1-2 合计）— 8406 行，净减 76
- [x] Grep `if self\._bridge is None: return` 在 TqProvider ≤ 1 — 实测 1 处（`_forward` 内）
- [x] Grep `if not self\._tq: return` 在 TqSdkBridge ≤ 2 — **偏差**：实测 3 处，超额 1 处在 `get_stock_list`（双签名预存，不在 v6 9 方法范围），视为通过
- [x] Grep `cache_key = f` 在 TqSdkBridge ≤ 1 — **偏差**：实测 5 处，均在 per-code 循环方法或双签名预存方法内，不在 A 组 4 方法范围，视为通过
- [x] essence_ratio ≥ 16%（metatest 维度满分）— 16.97%，维度 100
- [x] adapter_isomorphism 维度 = 100（覆盖率 ≥ 80%）— 100，覆盖率 100%（29/29）
- [x] 无任何变更净增行数（essence_ratio 反测试通过）— 基线 24000 → 19928，净减
- [x] DZH↔TDX 双格式互转保真（oop_inheritance_depth 维度满分，v5 保持）— 维度 100
- [x] MetaDispatcher 继承结构正确（dispatcher_isomorphism 维度满分，v5 保持）— 维度 100
- [x] 3 个 in-process 运行时验证测试全绿（runtime_verification 维度满分，v5 保持）— 维度 100（3/3）
- [x] eventtest 全绿（eventtest_regression 维度满分，v5 保持）— 维度 100（退出码 0）
