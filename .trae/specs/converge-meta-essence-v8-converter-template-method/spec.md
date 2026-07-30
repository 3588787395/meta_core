# 元模式本质收敛 v8：Converter 主流程模板方法上提 Spec

## Why

v7 元模式收尾闭合已完成（98.49 PASS，21 维全 ≥ 80，core 19928 行，providers.py 8366 行）。用户要求「完善面向对象，大智慧和通达信只作为继承，所有基础功能用相同代码」。经架构工程师第八层深度洞察调研，确认 `BasePoolConverter` 当前只收敛了 4 个原子操作（`_parse_element` / `_add_element` / `_decode_pos` / `_decode_xml_bytes`，~60 行），而两个真正同构的**主流程**仍是模块级并行函数：

1. **parse_dzh_xml（converters.py:1769，~1653 行）与 parse_tdx_xml（converters.py:4145，~1286 行）** 共享「解码 → ET.fromstring → 提取 pool 元数据 → 遍历 cells → 遍历 flows → 返回」6 步骨架，仅解码器选择 / 属性映射表 / 返回类型 3 维差异。当前模块级并行，未进入继承体系。
2. **export_dzh_xml（converters.py:3422，~723 行）与 _build_tdx_xml（converters.py:5686，~106 行）** 共享「取 pool_meta/nodes/edges → 构建 root Element → 遍历 nodes 建 cell → 遍历 edges 建 flow → 序列化」5 步骨架。DZH 导出还含 ency/warning/system 三段重复 if/elif（3441-3456）可表驱动收敛。

**第八层洞察（主流程模板方法上提）**：OOP 继承的真正价值不在原子操作层（v4 已做），而在**主流程编排层**。当前 `oop_inheritance_depth` 维度 = 100 但只检查原子操作 4 条件，**无法识别主流程未进入继承体系这一缺口**——这是评分体系的盲区。v8 将 `parse_pool` / `export_pool` 模板方法上提到 `BasePoolConverter`，子类仅覆盖差异钩子，闭合「大智慧和通达信只作为继承」的最后一层。

同时，调研显式确认 2 项已达标无需投入：
- **配置导入导出表驱动**（`_CONVERTER_REGISTRY` + `import_pool` / `export_pool` 已落地，RULES 89 生效，无过程式 if/elif 格式分支）
- **彻底事件驱动**（轮询模式 12/12 零匹配，`polling_zero_tolerance` = 100）

## What Changes

### 阶段 1：BasePoolConverter 主流程模板方法上提（高优先级，核心）

- **变更 T1：定义 `parse_pool` 模板方法**。在 `BasePoolConverter` 中新增 `parse_pool(self, source) -> Any` 模板方法，编排 6 步骨架：`text = self._decode_source(source)` → `root = ET.fromstring(text)` → `pool_meta = self._extract_pool_meta(root, source)` → `cells = self._parse_cells(root)` → `flows = self._parse_flows(root)` → `return self._build_result(pool_meta, cells, flows)`。**BREAKING**：无（模块级函数保留为薄包装委托）。净增 ~15 行（模板方法骨架）。
- **变更 T2：定义 `export_pool` 模板方法**。在 `BasePoolConverter` 中新增 `export_pool(self, config) -> bytes` 模板方法，编排 5 步骨架：`root = self._create_root(config)` → `self._serialize_pool_attrs(root, config)` → `self._serialize_cells(root, config)` → `self._serialize_flows(root, config)` → `return self._finalize_xml(root)`。**BREAKING**：无。净增 ~12 行。
- **变更 T3：定义抽象差异钩子**。在 `BasePoolConverter` 中定义 6+5=11 个抽象/空钩子方法（`_decode_source` / `_extract_pool_meta` / `_parse_cells` / `_parse_flows` / `_build_result` / `_create_root` / `_serialize_pool_attrs` / `_serialize_cells` / `_serialize_flows` / `_finalize_xml`），子类覆盖。**关键**：钩子默认实现为 `raise NotImplementedError` 或空操作，子类必须覆盖。

### 阶段 2：DzhPoolConverter / TdxPoolConverter 覆盖差异钩子（高优先级）

- **变更 D1：DzhPoolConverter 覆盖 parse 钩子**。将 `parse_dzh_xml` 的编排逻辑（1769-1793 pool_meta 提取 + cells 遍历入口 + flows 遍历入口 + 返回构建）拆为 5 个钩子方法覆盖到 `DzhPoolConverter`。**关键**：`_parse_cells` / `_parse_flows` 的方法体（~800 行 cell 解析 + ~200 行 flow 解析）原封不动移入子类方法，不拆分不重写。模块级 `parse_dzh_xml` 改为 `return _DZH_CONVERTER.parse_pool(source)` 薄包装。**BREAKING**：无。
- **变更 D2：TdxPoolConverter 覆盖 parse 钩子**。将 `parse_tdx_xml` 的编排逻辑（4145-4183 pool 元素定位 + cells 遍历 + flows 遍历 + 返回）拆为 5 个钩子方法覆盖到 `TdxPoolConverter`。`_parse_cells` / `_parse_flows` 方法体原封不动移入。模块级 `parse_tdx_xml` 改为 `return _TDX_CONVERTER.parse_pool(source)` 薄包装。**BREAKING**：无。
- **变更 D3：DzhPoolConverter 覆盖 export 钩子**。将 `export_dzh_xml` 的编排逻辑拆为 5 个钩子方法。模块级 `export_dzh_xml` 改为 `return _DZH_CONVERTER.export_pool(config)` 薄包装。**BREAKING**：无。
- **变更 D4：TdxPoolConverter 覆盖 export 钩子**。将 `_build_tdx_xml` 的编排逻辑拆为 5 个钩子方法。模块级 `_build_tdx_xml` 改为 `return _TDX_CONVERTER.export_pool(config)` 薄包装。**BREAKING**：无。

### 阶段 3：export_dzh_xml if/elif 表驱动收敛（中优先级）

- **变更 E1：ency/warning/system 三段 if/elif 表驱动**。将 `export_dzh_xml` 中 3441-3456 的三段重复 `if (not pool_present or X in pool_present) and _val is not None: ... elif _val is not None and _val != '': ...` 收敛为 `_OPTIONAL_POOL_ATTRS: List[str] = ["ency", "warning", "system"]` 表 + 单循环。净减 ~7 行。

### 阶段 4：metatest v8 量化评审升级（量化闭环）

- **变更 M1：scoring.py oop_inheritance_depth 维度深化**。评分标准从 4 条件升级为 6 条件：原 4 条件（基类存在 / 子类继承 / 原子方法在基类 / 子类仅差异）+ 新增 2 条件（`parse_pool` 模板方法在基类 / `export_pool` 模板方法在基类）。6 条件各占 ~16.67%，全部满足满分 100。权重保持 6.144%。
- **变更 M2：runner.py oop_inheritance 采集扩展**。`_collect_oop_inheritance` 新增 `parse_pool_in_base` / `export_pool_in_base` 2 字段采集（AST 检查 `BasePoolConverter` 类体内是否定义 `parse_pool` / `export_pool` 方法）。
- **变更 M3：正反合测试 v8 升级**。升级 `metatest/test_positive_oop_inheritance.py` 断言 `parse_pool` / `export_pool` 模板方法在 `BasePoolConverter` 中定义 + 4 个模块级函数（`parse_dzh_xml` / `parse_tdx_xml` / `export_dzh_xml` / `_build_tdx_xml`）为薄包装委托。
- **变更 M4：metatest/README.md v8 文档更新**。新增主流程模板方法说明。

### 阶段 5：RULES + 全量回归

- **变更 R1：RULES.md 新增第 118 条**。文档化「Converter 主流程必须模板方法化（`parse_pool` / `export_pool` 在 `BasePoolConverter`，子类仅覆盖差异钩子 `_decode_source` / `_extract_pool_meta` / `_parse_cells` / `_parse_flows` / `_build_result` 等），禁止重新引入模块级并行主流程函数」。
- **变更 R2：全量回归**。metatest 总分 ≥ 95 且 21 维均 ≥ 80，eventtest 退出码 0，converters.py 净减 ≥ 30 行（编排去重 + if/elif 表驱动），DZH↔TDX roundtrip 保真。

## Impact

- Affected specs: converge-meta-essence-v4-oop-event-driven（v4 oop_inheritance_depth 维度深化）
- Affected code: converters.py（BasePoolConverter 主流程模板方法 + DzhPoolConverter/TdxPoolConverter 钩子覆盖 + export_dzh_xml if/elif 表驱动）、metatest/scoring.py、metatest/runner.py、metatest/test_positive_oop_inheritance.py、metatest/README.md、RULES.md

## ADDED Requirements

### Requirement: BasePoolConverter 主流程模板方法
The system SHALL provide `parse_pool(self, source)` and `export_pool(self, config)` template methods in `BasePoolConverter`, orchestrating the 6-step parse skeleton and 5-step export skeleton respectively, with subclass-overridable difference hooks.

#### Scenario: parse_pool 模板方法编排
- **WHEN** `parse_pool(source)` called on DzhPoolConverter or TdxPoolConverter instance
- **THEN** orchestrates: `_decode_source` → `ET.fromstring` → `_extract_pool_meta` → `_parse_cells` → `_parse_flows` → `_build_result`

#### Scenario: export_pool 模板方法编排
- **WHEN** `export_pool(config)` called on DzhPoolConverter or TdxPoolConverter instance
- **THEN** orchestrates: `_create_root` → `_serialize_pool_attrs` → `_serialize_cells` → `_serialize_flows` → `_finalize_xml`

### Requirement: oop_inheritance_depth 维度深化
The system SHALL score `oop_inheritance_depth` dimension with 6 conditions (original 4 + `parse_pool_in_base` + `export_pool_in_base`), each worth ~16.67%.

#### Scenario: 主流程模板方法在基类
- **WHEN** metatest runner collects oop_inheritance data
- **THEN** checks `parse_pool` and `export_pool` methods defined in `BasePoolConverter` class body via AST
