# Tasks

本规范按「架构工程师（实施）→ 评审工程师（验证）」迭代流程分 3 阶段实施，覆盖 BasePoolConverter 10 钩子 @abstractmethod 早失败化 + edge endpoint resolver 上提 + metatest v9 量化评审升级 + RULES 119。**第九层洞察（契约执行时序是运行时本质维度）**：`raise NotImplementedError` 在调用时失败（晚失败），`@abstractmethod` 在实例化时失败（早失败）。v9 将 10 个占位钩子改为 `@abstractmethod` 装饰，消除 30 行样板代码 + 将契约执行从 invocation-time 前移到 construction-time，实现 fail-fast 运行时本质。同时调研确认 v8 已达 DZH/TDX 同构收敛上限，v9 文档化此上限防止过度抽象。预估净减 ~11 行（10 占位消除 + 1 edge resolver）。

## 阶段 1：@abstractmethod 早失败钩子 + edge endpoint resolver（高优先级，核心）

### 架构工程师任务

- [x] Task 1: 变更 T1+T2+T3 — @abstractmethod 化 10 钩子 + edge endpoint resolver 上提
  - [x] SubTask 1.1: Read `converters.py:23` 确认当前 `from abc import ABC` 导入
  - [x] SubTask 1.2: Read `converters.py:250-281` 确认 10 个 `raise NotImplementedError` 占位钩子结构
  - [x] SubTask 1.3: Read `converters.py:675-676` 确认 DZH edge endpoint resolver 代码
  - [x] SubTask 1.4: Read `converters.py:1022-1023` 确认 TDX edge endpoint resolver 代码（与 DZH 字符级相同）
  - [x] SubTask 1.5: 修改 `converters.py:23` 导入：`from abc import ABC` → `from abc import ABC, abstractmethod`
  - [x] SubTask 1.6: 修改 `converters.py:250-281` 的 10 个占位钩子，每个从：
    ```python
    def _decode_source(self, source):
        raise NotImplementedError
    ```
    改为：
    ```python
    @abstractmethod
    def _decode_source(self, source): ...
    ```
    10 个钩子：`_decode_source` / `_extract_pool_meta` / `_parse_cells` / `_parse_flows` / `_build_result` / `_create_root` / `_serialize_pool_attrs` / `_serialize_cells` / `_serialize_flows` / `_finalize_xml`
  - [x] SubTask 1.7: 在 `BasePoolConverter` 类体中新增 `_resolve_edge_endpoint` 静态方法：
    ```python
    @staticmethod
    def _resolve_edge_endpoint(raw):
        """解析 edge 端点为 node_id 字符串（dict 取 node_id，其他 str 化）。"""
        return raw.get("node_id", "") if isinstance(raw, dict) else str(raw)
    ```
  - [x] SubTask 1.8: 修改 DZH `converters.py:675-676` 的 2 行 edge endpoint 解析为：
    ```python
    source_node_id = self._resolve_edge_endpoint(src_raw)
    target_node_id = self._resolve_edge_endpoint(tgt_raw)
    ```
  - [x] SubTask 1.9: 修改 TDX `converters.py:1022-1023` 的 2 行 edge endpoint 解析为：
    ```python
    so_str = self._resolve_edge_endpoint(so_raw)
    to_str = self._resolve_edge_endpoint(to_raw)
    ```
  - [x] SubTask 1.10: Grep `raise NotImplementedError` 在 `BasePoolConverter` 类内 = 0（已全部 @abstractmethod 化）
  - [x] SubTask 1.11: Grep `@abstractmethod` 在 `BasePoolConverter` 类内 = 10
  - [x] SubTask 1.12: `python -m pytest metatest/test_positive_oop_inheritance.py -v` 退出码 0（v8 基线测试不回归，v9 断言在 Task 3 添加）
  - [x] SubTask 1.13: `python -m pytest metatest/test_synthesis_import_export_roundtrip.py metatest/test_integration_roundtrip.py -v` 退出码 0（roundtrip 保真）
  - [x] SubTask 1.14: `wc -l converters.py` 净减 ≥ 10 行（v8 基线 5992，v9 目标 ≤ 5982）

## 阶段 2：metatest v9 量化评审升级（量化闭环）

### 架构工程师任务

- [x] Task 2: 变更 M1+M2 — scoring.py 7 条件 + runner.py 采集扩展
  - [x] SubTask 2.1: Read `metatest/scoring.py:544-584` 确认 v8 `_score_oop_inheritance_depth` 6 条件评分逻辑
  - [x] SubTask 2.2: 评分标准升级：6 条件 → 7 条件（原 6 + `hooks_are_abstract`），每条件占 100/7 ≈ 14.29%
  - [x] SubTask 2.3: 新增 `hooks_are_abstract` 条件评分：`bool(oop.get("hooks_are_abstract", False))`
  - [x] SubTask 2.4: 权重保持 6.144%（不变），21 维权重和 = 100%
  - [x] SubTask 2.5: Read `metatest/runner.py:1391-1468` 确认 v8 `_collect_oop_inheritance` 6 字段采集
  - [x] SubTask 2.6: 新增 `hooks_are_abstract` 字段采集：AST 检查 `BasePoolConverter` 类体内 10 个钩子方法的 `decorator_list` 是否含 `abstractmethod`。具体逻辑：遍历 BasePoolConverter 类体的 FunctionDef 节点，对 10 个钩子名检查 `decorator_list` 中是否存在 `ast.Name(id='abstractmethod')` 或 `ast.Attribute(attr='abstractmethod')`。10 个钩子全部含 abstractmethod 装饰 → True
  - [x] SubTask 2.7: `test_results["oop_inheritance"]` 字典新增 `hooks_are_abstract` 字段

- [x] Task 3: 变更 M3+M4 — 测试升级 + README 文档
  - [x] SubTask 3.1: Read `metatest/test_positive_oop_inheritance.py` 确认 v8 断言结构
  - [x] SubTask 3.2: 新增测试类 `TestV9AbstractMethodHooks`，断言：10 个差异钩子在 `BasePoolConverter` 中使用 `@abstractmethod` 装饰（AST 检查 decorator_list）
  - [x] SubTask 3.3: 新增测试：`BasePoolConverter` 不可实例化（`pytest.raises(TypeError)` when `BasePoolConverter()`）
  - [x] SubTask 3.4: 新增测试：`DzhPoolConverter` / `TdxPoolConverter` 可实例化（已覆盖全部 10 钩子，不报 TypeError）
  - [x] SubTask 3.5: 新增测试：edge endpoint resolver `_resolve_edge_endpoint` 在 `BasePoolConverter` 中定义，DZH/TDX 调用 `self._resolve_edge_endpoint`
  - [x] SubTask 3.6: `python -m pytest metatest/test_positive_oop_inheritance.py -v` 退出码 0（含 v9 新断言）
  - [x] SubTask 3.7: Read `metatest/README.md` 确认 v8 文档结构
  - [x] SubTask 3.8: README.md 标题 v8 → v9，概述新增第九层洞察说明
  - [x] SubTask 3.9: README.md oop_inheritance_depth 维度说明 6 条件 → 7 条件
  - [x] SubTask 3.10: README.md 新增「收敛上限文档化」段落：v8 已达 DZH/TDX 同构收敛上限，10 钩子方法体是结构同构但数据/派发异构，禁止强行合并

## 阶段 3：RULES + 全量回归

### 架构工程师任务

- [x] Task 4: 变更 R1 — RULES.md 新增第 119 条
  - [x] SubTask 4.1: 在 RULES.md 第 118 条后新增第 119 条：「模板方法差异钩子必须使用 `@abstractmethod` 装饰（非 `raise NotImplementedError`），实现 construction-time 早失败契约执行——子类未覆盖任一钩子时实例化即报 `TypeError`，优于 invocation-time 晚失败。禁止 `raise NotImplementedError` 占位模式。这是「极致本质的运行时」的第九层洞察：类型系统级契约执行优于运行时异常契约执行。同时，元模式同构收敛已达上限时须文档化「知止」，禁止强行合并结构同构但数据/派发异构的钩子（抽象税 > 收益）。」
  - [x] SubTask 4.2: Grep `^119\.` 在 RULES.md = 1

### 评审工程师任务

- [x] Task 5: 变更 R2 — 全量回归
  - [x] SubTask 5.1: `python -m pytest metatest/ -x -q --ignore=metatest/test_positive_event_panel.py` 退出码 0（全量测试通过，忽略预存 collection error 文件）
  - [x] SubTask 5.2: `python -c "from metatest.runner import _collect_oop_inheritance; from metatest.scoring import ScoringEngine; oop=_collect_oop_inheritance(); print(oop); e=ScoringEngine(); print(e._score_oop_inheritance_depth({'oop_inheritance': oop}))"` 验证 oop_inheritance_depth = 100（7/7 条件全满足）
  - [x] SubTask 5.3: `python -m eventtest.run_eventtest` 退出码 0（全绿）
  - [x] SubTask 5.4: `wc -l converters.py` 净减 ≥ 10 行（v8 基线 5992，v9 目标 ≤ 5982）
  - [x] SubTask 5.5: Grep `raise NotImplementedError` 在 `BasePoolConverter` 类内 = 0
  - [x] SubTask 5.6: Grep `@abstractmethod` 在 `BasePoolConverter` 类内 = 10
  - [x] SubTask 5.7: Grep `_resolve_edge_endpoint` 在 `BasePoolConverter` 类内 = 1（定义），在 DzhPoolConverter / TdxPoolConverter 各 = 2（调用 source + target）
  - [x] SubTask 5.8: oop_inheritance_depth 维度 = 100（7 条件全满足）
  - [x] SubTask 5.9: DZH↔TDX roundtrip 保真（19 roundtrip 测试全过）
  - [x] SubTask 5.10: essence_ratio 维度不受影响（仅统计 core/*.py，converters.py 不计入）
  - [x] SubTask 5.11: adapter_isomorphism 维度 = 100（v7/v8 保持）
  - [x] SubTask 5.12: dispatcher_isomorphism 维度 = 100（v5/v6/v7/v8 保持）
  - [x] SubTask 5.13: runtime_verification 维度 = 100（v5/v6/v7/v8 保持）
  - [x] SubTask 5.14: eventtest_regression 维度 = 100（v5/v6/v7/v8 保持）

# Task Dependencies
- Task 2/3 依赖 Task 1（@abstractmethod 化后才能采集与断言）
- Task 4 依赖 Task 1-3（RULES 文档化已落地成果）
- Task 5 依赖 Task 1-4（全量回归）
