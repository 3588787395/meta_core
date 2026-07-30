# 元模式本质收敛 v9：@abstractmethod 早失败运行时 + 收敛上限文档化 Spec

## Why

v8 完成了 Converter 主流程模板方法上提（`parse_pool` / `export_pool` 在 `BasePoolConverter`），闭合了 OOP 继承的「主流程编排层」。但 v8 引入 +122 行，其中约 50 行是模板方法骨架税（一次性基础设施），30 行是 10 个 `raise NotImplementedError` 占位钩子（lines 252-281）。架构工程师第九层深度调研确认：**v8 已达 DZH/TDX 同构收敛上限**——10 个钩子方法体是「结构同构但数据/派发异构」，强行合并会引入表条目爆炸 + roundtrip 回归风险，违背「精减代码」初衷。

**第九层洞察（契约执行时序是运行时本质维度）**：`raise NotImplementedError` 在**调用时**失败（晚失败——系统静默运行直到某个未覆盖钩子被 invoke），`@abstractmethod` 在**实例化时**失败（早失败——`DzhPoolConverter()` / `TdxPoolConverter()` 构造即报错）。将 10 个占位钩子改为 `@abstractmethod` 装饰，**消除 30 行样板代码** + **将契约执行从 invocation-time 前移到 construction-time**，实现 fail-fast 运行时本质。这是「极致本质的运行时」的第九层：类型系统级契约执行优于运行时异常契约执行。

同时，调研发现唯一的纯字符级重复代码（edge endpoint resolver，DZH 675-676 ≡ TDX 1022-1023，4 行）可上提 BasePoolConverter 消除。

**收敛上限文档化**：v9 明确记录 v8 已达 DZH/TDX 同构收敛上限，防止后续 v10/v11 反复尝试合并异构钩子而引入过度抽象（抽象税 > 收益）。这是元模式收敛的「知止」纪律。

## What Changes

### 阶段 1：@abstractmethod 早失败钩子（高优先级，核心）

- **变更 T1：导入 `abstractmethod`**。`from abc import ABC` → `from abc import ABC, abstractmethod`。净增 0 行（同行扩展）。
- **变更 T2：10 个占位钩子改为 `@abstractmethod` 装饰**。将 `BasePoolConverter` 中 10 个 `def _hook(...): raise NotImplementedError`（3 行/钩子 × 10 = 30 行）改为 `@abstractmethod` + `def _hook(...): ...`（2 行/钩子 × 10 = 20 行）。**净减 ~10 行**。**关键**：`@abstractmethod` 使 `BasePoolConverter` 不可实例化（已是 ABC），且子类若未覆盖任一钩子则在实例化时即报 `TypeError`（早失败），优于 `raise NotImplementedError` 的晚失败。
- **变更 T3：edge endpoint resolver 上提**。将 DZH 675-676 与 TDX 1022-1023 的纯字符级重复（`raw.get("node_id", "") if isinstance(raw, dict) else str(raw)`）提取为 `BasePoolConverter._resolve_edge_endpoint(raw) -> str` 静态方法。DZH/TDX 各 2 行 → 1 行调用，基类 +3 行助手。**净减 ~1 行**，消除唯一纯重复。

### 阶段 2：metatest v9 量化评审升级（量化闭环）

- **变更 M1：scoring.py oop_inheritance_depth 维度深化**。评分标准从 6 条件升级为 7 条件：原 6 条件 + 新增第 7 条件 `hooks_are_abstract`（10 个差异钩子使用 `@abstractmethod` 装饰，非 `raise NotImplementedError`）。7 条件各占 ~14.29%，全部满足满分 100。权重保持 6.144%。
- **变更 M2：runner.py oop_inheritance 采集扩展**。`_collect_oop_inheritance` 新增 `hooks_are_abstract` 字段采集（AST 检查 10 个钩子方法的 `decorator_list` 是否含 `abstractmethod`）。
- **变更 M3：test_positive_oop_inheritance.py v9 升级**。新增断言：10 个差异钩子在 `BasePoolConverter` 中使用 `@abstractmethod` 装饰（非 `raise NotImplementedError`）。
- **变更 M4：README.md v9 文档更新**。新增早失败运行时说明 + 收敛上限文档化。

### 阶段 3：RULES + 全量回归

- **变更 R1：RULES.md 新增第 119 条**。文档化「模板方法差异钩子必须使用 `@abstractmethod` 装饰（非 `raise NotImplementedError`），实现 construction-time 早失败契约执行。禁止 `raise NotImplementedError` 占位模式」。
- **变更 R2：全量回归**。metatest 总分 ≥ 95 且 21 维均 ≥ 80，eventtest 退出码 0，converters.py 净减 ≥ 10 行（v8 基线 5992，v9 目标 ≤ 5982），DZH↔TDX roundtrip 保真。

## Impact

- Affected specs: converge-meta-essence-v8-converter-template-method（v8 oop_inheritance_depth 维度 6→7 条件深化）
- Affected code: converters.py（BasePoolConverter 10 钩子 @abstractmethod 化 + edge endpoint resolver 上提）、metatest/scoring.py、metatest/runner.py、metatest/test_positive_oop_inheritance.py、metatest/README.md、RULES.md

## ADDED Requirements

### Requirement: @abstractmethod 早失败钩子
The system SHALL decorate all 10 differential hooks in `BasePoolConverter` with `@abstractmethod`, enforcing subclass override at construction-time (fail-fast) rather than invocation-time (late failure via `raise NotImplementedError`).

#### Scenario: 子类未覆盖钩子时实例化失败
- **WHEN** a subclass of `BasePoolConverter` is instantiated without overriding all 10 hooks
- **THEN** `TypeError` is raised at construction time (before any `parse_pool` / `export_pool` call)

#### Scenario: 钩子占位消除
- **WHEN** metatest runner collects oop_inheritance data
- **THEN** verifies 10 hooks use `@abstractmethod` decorator (not `raise NotImplementedError` body)

### Requirement: oop_inheritance_depth 维度深化为 7 条件
The system SHALL score `oop_inheritance_depth` dimension with 7 conditions (original 6 + `hooks_are_abstract`), each worth ~14.29%.

#### Scenario: @abstractmethod 驱动收敛
- **WHEN** metatest runner collects oop_inheritance data
- **THEN** checks 10 hooks decorated with `@abstractmethod` via AST decorator_list inspection
