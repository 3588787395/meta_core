# Checklist

## 架构工程师检查点（实施前自检）

- [x] 已阅读 spec.md「Why」章节并理解：v9 是「契约执行时序前移」——`raise NotImplementedError`（晚失败）→ `@abstractmethod`（早失败），非拆分、非重写钩子方法体
- [x] 已理解本次迭代核心是「消除 v8 引入的 30 行占位样板 + 提升运行时安全性」，非新增架构
- [x] 已阅读 `converters.py:23` 确认当前 `from abc import ABC` 导入（需扩展为 `ABC, abstractmethod`）
- [x] 已阅读 `converters.py:250-281` 确认 10 个 `raise NotImplementedError` 占位钩子（3 行/钩子 × 10 = 30 行）
- [x] 已阅读 `converters.py:675-676` 确认 DZH edge endpoint resolver（`raw.get("node_id", "") if isinstance(raw, dict) else str(raw)`）
- [x] 已阅读 `converters.py:1022-1023` 确认 TDX edge endpoint resolver（与 DZH 字符级相同）
- [x] 已阅读 `metatest/scoring.py:544-584` 确认 v8 `_score_oop_inheritance_depth` 6 条件评分逻辑
- [x] 已阅读 `metatest/runner.py:1391-1468` 确认 v8 `_collect_oop_inheritance` 6 字段采集
- [x] 已阅读 `metatest/test_positive_oop_inheritance.py` 确认 v8 断言结构
- [x] 已运行 `wc -l converters.py` 确认 v8 基线 5992 行
- [x] 已确认 `oop_inheritance_depth` 维度当前 = 100（6 条件全满足），v9 深化为 7 条件
- [x] 已确认调研结论：v8 已达 DZH/TDX 同构收敛上限，10 钩子方法体不可合并（抽象税 > 收益）
- [x] 已确认「合并非拆分」硬约束延续：@abstractmethod 仅改钩子签名装饰，不动方法体

## 评审工程师检查点（阶段 1：@abstractmethod 早失败钩子 + edge endpoint resolver）

### 变更 T1+T2+T3 — @abstractmethod 化 10 钩子 + edge endpoint resolver 上提

- [x] `from abc import ABC, abstractmethod` 导入在 converters.py 中（同行扩展）
- [x] 10 个差异钩子在 `BasePoolConverter` 中使用 `@abstractmethod` 装饰
- [x] 10 个钩子方法体为 `...`（非 `raise NotImplementedError`）
- [x] Grep `raise NotImplementedError` 在 `BasePoolConverter` 类内 = 0
- [x] Grep `@abstractmethod` 在 `BasePoolConverter` 类内 = 10
- [x] `_resolve_edge_endpoint` 静态方法在 `BasePoolConverter` 中定义
- [x] DZH `_serialize_flows` 中 edge endpoint 解析调用 `self._resolve_edge_endpoint`
- [x] TDX `_serialize_flows` 中 edge endpoint 解析调用 `self._resolve_edge_endpoint`
- [x] `python -m pytest metatest/test_positive_oop_inheritance.py -v` 退出码 0（v8 基线不回归）
- [x] `python -m pytest metatest/test_synthesis_import_export_roundtrip.py metatest/test_integration_roundtrip.py -v` 退出码 0（roundtrip 保真）
- [x] `wc -l converters.py` 净减 ≥ 10 行（v8 基线 5992，v9 目标 ≤ 5982）

## 评审工程师检查点（阶段 2：metatest v9 量化评审升级）

### 变更 M1 — scoring.py oop_inheritance_depth 维度深化
- [x] `_score_oop_inheritance_depth` 评分标准从 6 条件升级为 7 条件
- [x] 新增条件 7：`hooks_are_abstract`（10 个差异钩子使用 `@abstractmethod` 装饰）
- [x] 7 条件各占 100/7 ≈ 14.29%
- [x] 权重保持 6.144%（不变）
- [x] 21 维权重和 = 100%

### 变更 M2 — runner.py oop_inheritance 采集扩展
- [x] `_collect_oop_inheritance` 新增 `hooks_are_abstract` 字段采集
- [x] AST 检查 10 个钩子方法的 `decorator_list` 是否含 `abstractmethod`
- [x] `test_results["oop_inheritance"]` 字典新增 `hooks_are_abstract` 字段

### 变更 M3 — test_positive_oop_inheritance.py v9 升级
- [x] 新增 `TestV9AbstractMethodHooks` 测试类
- [x] 断言 10 个差异钩子在 `BasePoolConverter` 中使用 `@abstractmethod` 装饰
- [x] 断言 `BasePoolConverter` 不可实例化（`pytest.raises(TypeError)`）
- [x] 断言 `DzhPoolConverter` / `TdxPoolConverter` 可实例化（已覆盖全部 10 钩子）
- [x] 断言 `_resolve_edge_endpoint` 在 `BasePoolConverter` 中定义，DZH/TDX 调用
- [x] `python -m pytest metatest/test_positive_oop_inheritance.py -v` 退出码 0（含 v9 新断言）

### 变更 M4 — README.md v9 文档更新
- [x] 标题 v8 → v9，概述新增第九层洞察说明
- [x] oop_inheritance_depth 维度说明 6 条件 → 7 条件
- [x] 新增「收敛上限文档化」段落

## 评审工程师检查点（阶段 3：RULES + 全量回归）

### RULES.md 119
- [x] 第 119 条 — 模板方法差异钩子必须使用 `@abstractmethod` 装饰（非 `raise NotImplementedError`），实现 construction-time 早失败契约执行 + 收敛上限知止纪律
- [x] Grep `^119\.` 在 RULES.md = 1

### 全量回归
- [x] `python -m pytest metatest/ -x -q --ignore=metatest/test_positive_event_panel.py` 全量测试通过
- [x] oop_inheritance_depth 维度 = 100（7/7 条件全满足，独立验证）
- [x] `python -m eventtest.run_eventtest` 退出码 0（全绿）
- [x] `wc -l converters.py` 净减 ≥ 10 行（v8 基线 5992，v9 目标 ≤ 5982）
- [x] Grep `raise NotImplementedError` 在 `BasePoolConverter` 类内 = 0
- [x] Grep `@abstractmethod` 在 `BasePoolConverter` 类内 = 10
- [x] Grep `_resolve_edge_endpoint` 在 `BasePoolConverter` 类内 = 1（定义）
- [x] oop_inheritance_depth 维度 = 100（7 条件全满足）
- [x] DZH↔TDX roundtrip 保真（19 roundtrip 测试全过）
- [x] essence_ratio 维度不受影响（仅统计 core/*.py，converters.py 不计入）
- [x] adapter_isomorphism 维度 = 100（v7/v8 保持）
- [x] dispatcher_isomorphism 维度 = 100（v5/v6/v7/v8 保持）
- [x] runtime_verification 维度 = 100（v5/v6/v7/v8 保持）
- [x] eventtest_regression 维度 = 100（v5/v6/v7/v8 保持）

## 第九层洞察根因检查点（评审工程师最终验收）

- [x] **契约执行时序前移**：`raise NotImplementedError` 在调用时失败（晚失败），`@abstractmethod` 在实例化时失败（早失败）。v9 将 10 个占位钩子改为 `@abstractmethod` 装饰，将契约执行从 invocation-time 前移到 construction-time，实现 fail-fast 运行时本质
- [x] **占位样板消除**：10 个 `raise NotImplementedError`（3 行/钩子 × 10 = 30 行）改为 `@abstractmethod` + `...`（2 行/钩子 × 10 = 20 行），净减 10 行
- [x] **唯一纯重复消除**：edge endpoint resolver（DZH 675-676 ≡ TDX 1022-1023，4 行纯字符级重复）上提 BasePoolConverter，净减 1 行
- [x] **收敛上限文档化**：v8 已达 DZH/TDX 同构收敛上限，10 钩子方法体是结构同构但数据/派发异构，禁止强行合并（抽象税 > 收益）。v9 文档化此上限，防止后续过度抽象
- [x] **非拆分非重写**：@abstractmethod 仅改钩子签名装饰，不动方法体。edge resolver 仅提取已存在代码，不重写
- [x] **量化评审驱动**：oop_inheritance_depth 维度深化为 7 条件，新增 `hooks_are_abstract` 条件，使评分体系能驱动 @abstractmethod 收敛
