# Tasks

本规范按「架构工程师（实施）→ 评审工程师（验证）」迭代流程分 5 阶段实施，覆盖 BasePoolConverter 主流程模板方法上提 + Dzh/TdxPoolConverter 差异钩子覆盖 + export_dzh_xml if/elif 表驱动 + metatest v8 量化评审升级 + RULES 118。**第八层洞察（主流程模板方法上提）**：OOP 继承的真正价值不在原子操作层（v4 已做），而在主流程编排层。当前 `oop_inheritance_depth` = 100 但只检查原子操作 4 条件，无法识别主流程未进入继承体系这一缺口。v8 将 `parse_pool` / `export_pool` 模板方法上提到 `BasePoolConverter`，子类仅覆盖差异钩子，闭合「大智慧和通达信只作为继承」的最后一层。本次迭代核心 = 主流程编排骨架上提（非拆分、非重写 cell/flow 解析体），预估净减 ~30-50 行（编排去重 + if/elif 表驱动）。

## 阶段 1：BasePoolConverter 主流程模板方法上提（高优先级，核心）

### 架构工程师任务

- [x] Task 1: 变更 T1+T2+T3 — 定义 parse_pool / export_pool 模板方法 + 差异钩子
  - [x] SubTask 1.1: Read `converters.py:168-231` 确认 `BasePoolConverter` 当前 4 个原子方法 + 类结构
  - [x] SubTask 1.2: Read `converters.py:1769-1793` 确认 `parse_dzh_xml` 前 25 行编排骨架（decode → fromstring → pool_meta 提取）
  - [x] SubTask 1.3: Read `converters.py:4145-4183` 确认 `parse_tdx_xml` 前 40 行编排骨架（decode → fromstring → pool 元素定位 → pool_meta 提取）
  - [x] SubTask 1.4: Read `converters.py:3422-3460` 确认 `export_dzh_xml` 前 40 行编排骨架（取 config → root Element → pool attrs 设置）
  - [x] SubTask 1.5: Read `converters.py:5686-5740` 确认 `_build_tdx_xml` 前 55 行编排骨架（取 mapping → root Element → pool attrs 表驱动设置）
  - [x] SubTask 1.6: 在 `BasePoolConverter` 中新增 `parse_pool(self, source) -> Any` 模板方法，编排 6 步骨架：
    ```python
    def parse_pool(self, source):
        text = self._decode_source(source)
        root = ET.fromstring(text)
        pool_meta = self._extract_pool_meta(root, source)
        cells = self._parse_cells(root)
        flows = self._parse_flows(root)
        return self._build_result(pool_meta, cells, flows)
    ```
  - [x] SubTask 1.7: 在 `BasePoolConverter` 中新增 `export_pool(self, config) -> bytes` 模板方法，编排 5 步骨架：
    ```python
    def export_pool(self, config):
        root = self._create_root(config)
        self._serialize_pool_attrs(root, config)
        self._serialize_cells(root, config)
        self._serialize_flows(root, config)
        return self._finalize_xml(root)
    ```
  - [x] SubTask 1.8: 在 `BasePoolConverter` 中定义 11 个差异钩子方法（默认 `raise NotImplementedError`）：
    - parse 钩子：`_decode_source(self, source)` / `_extract_pool_meta(self, root, source)` / `_parse_cells(self, root)` / `_parse_flows(self, root)` / `_build_result(self, pool_meta, cells, flows)`
    - export 钩子：`_create_root(self, config)` / `_serialize_pool_attrs(self, root, config)` / `_serialize_cells(self, root, config)` / `_serialize_flows(self, root, config)` / `_finalize_xml(self, root)`
  - [x] SubTask 1.9: Grep `def parse_pool\b|def export_pool\b` 在 `BasePoolConverter` 类内 = 1 各
  - [x] SubTask 1.10: Grep `def _decode_source\b|def _extract_pool_meta\b|def _parse_cells\b|def _parse_flows\b|def _build_result\b` 在 `BasePoolConverter` 类内 = 1 各
  - [x] SubTask 1.11: `python -m pytest metatest/ -x -q 2>&1 | tail -5` 验证无回归（此阶段钩子未覆盖，子类仍用模块级函数，预期无影响）

## 阶段 2：DzhPoolConverter / TdxPoolConverter 覆盖差异钩子（高优先级）

### 架构工程师任务

- [x] Task 2: 变更 D1+D2 — Dzh/Tdx parse 钩子覆盖 + 模块级函数薄包装化
  - [x] SubTask 2.1: Read `converters.py:1769-3422` 完整审查 `parse_dzh_xml` 结构（pool_meta 提取段 / cells 遍历段 / flows 遍历段 / 返回构建段）
  - [x] SubTask 2.2: 在 `DzhPoolConverter` 中覆盖 5 个 parse 钩子：
    - `_decode_source(self, source)` → 调用 `self.decode_xml_content(source)` + filename 处理
    - `_extract_pool_meta(self, root, source)` → 提取 pool_attr_names / pool_present_attrs / pool_meta dict（原 1778-1793）
    - `_parse_cells(self, root)` → 原 1795-3200 的 cells 遍历逻辑（原封不动移入，不拆分不重写）
    - `_parse_flows(self, root)` → 原 3200-3420 的 flows 遍历逻辑（原封不动移入）
    - `_build_result(self, pool_meta, cells, flows)` → 返回最终 dict 结构
  - [x] SubTask 2.3: `parse_dzh_xml` 模块级函数改为 `return _DZH_CONVERTER.parse_pool(xml_content)` 薄包装（保留原签名 `parse_dzh_xml(xml_content, filename=None)`，filename 通过 source 传递）
  - [x] SubTask 2.4: Read `converters.py:4145-5686` 完整审查 `parse_tdx_xml` 结构
  - [x] SubTask 2.5: 在 `TdxPoolConverter` 中覆盖 5 个 parse 钩子：
    - `_decode_source(self, source)` → 读取文件 + `self.decode_tdx_xml(raw_bytes)`
    - `_extract_pool_meta(self, root, source)` → pool_elem 定位 + nextid/backcolor 提取 + 版本检测（原 4164-4178）
    - `_parse_cells(self, root)` → 原 4179-5400 的 cells 遍历逻辑（原封不动移入）
    - `_parse_flows(self, root)` → 原 flows 遍历逻辑（原封不动移入）
    - `_build_result(self, pool_meta, cells, flows)` → 返回 TdxPoolMetaModel
  - [x] SubTask 2.6: `parse_tdx_xml` 模块级函数改为 `return _TDX_CONVERTER.parse_pool(filepath)` 薄包装
  - [x] SubTask 2.7: Grep `def parse_dzh_xml\b|def parse_tdx_xml\b` 在 converters.py 模块级 = 1 各（薄包装保留）
  - [x] SubTask 2.8: `python -m pytest metatest/ -x -q 2>&1 | tail -5` 验证无回归
  - [x] SubTask 2.9: `python -m pytest eventtest/ -x -q 2>&1 | tail -5` 验证 roundtrip 保真（如有 eventtest 覆盖）

- [x] Task 3: 变更 D3+D4 — Dzh/Tdx export 钩子覆盖 + 模块级函数薄包装化
  - [x] SubTask 3.1: Read `converters.py:3422-4145` 完整审查 `export_dzh_xml` 结构
  - [x] SubTask 3.2: 在 `DzhPoolConverter` 中覆盖 5 个 export 钩子：
    - `_create_root(self, config)` → `ET.Element("pool")` + 基础 attrs 设置（type/ver/mode/backcolor）
    - `_serialize_pool_attrs(self, root, config)` → ency/warning/system 可选 attrs 设置（原 3440-3456，此阶段保留 if/elif，阶段 3 表驱动化）
    - `_serialize_cells(self, root, config)` → 原 3458-4100 的 cells 序列化逻辑（原封不动移入）
    - `_serialize_flows(self, root, config)` → 原 flows 序列化逻辑（原封不动移入）
    - `_finalize_xml(self, root)` → ET.tostring + 编码处理
  - [x] SubTask 3.3: `export_dzh_xml` 模块级函数改为 `return _DZH_CONVERTER.export_pool(config)` 薄包装
  - [x] SubTask 3.4: Read `converters.py:5686-5792` 完整审查 `_build_tdx_xml` 结构
  - [x] SubTask 3.5: 在 `TdxPoolConverter` 中覆盖 5 个 export 钩子：
    - `_create_root(self, config)` → `ET.Element(pool_cfg['root_element'])` + SubElement
    - `_serialize_pool_attrs(self, root, config)` → 表驱动 pool_attributes 设置（原 5699-5700）
    - `_serialize_cells(self, root, config)` → 原 5703-5750 的 cells 序列化逻辑（原封不动移入）
    - `_serialize_flows(self, root, config)` → 原 flows 序列化逻辑（原封不动移入）
    - `_finalize_xml(self, root)` → ET.tostring + 文件写入（原 _build_tdx_xml 的 filepath 参数处理）
  - [x] SubTask 3.6: `_build_tdx_xml` 模块级函数改为薄包装委托 `_TDX_CONVERTER.export_pool(config)`（保留 filepath 写入语义）
  - [x] SubTask 3.7: Grep `def export_dzh_xml\b|def _build_tdx_xml\b` 在 converters.py 模块级 = 1 各（薄包装保留）
  - [x] SubTask 3.8: `python -m pytest metatest/ -x -q 2>&1 | tail -5` 验证无回归
  - [x] SubTask 3.9: DZH↔TDX roundtrip 保真验证（导入 DZH → 导出 TDX → 导入 TDX → 导出 DZH，比对 pool_config 一致）

## 阶段 3：export_dzh_xml if/elif 表驱动收敛（中优先级）

### 架构工程师任务

- [x] Task 4: 变更 E1 — ency/warning/system 三段 if/elif 表驱动
  - [x] SubTask 4.1: Read `DzhPoolConverter._serialize_pool_attrs` 确认 ency/warning/system 三段重复 if/elif 结构（原 3440-3456）
  - [x] SubTask 4.2: 定义 `_OPTIONAL_POOL_ATTRS: List[str] = ["ency", "warning", "system"]` 类级常量
  - [x] SubTask 4.3: `_serialize_pool_attrs` 中三段 if/elif 改为单循环：
    ```python
    for attr_name in self._OPTIONAL_POOL_ATTRS:
        val = pool_meta.get(attr_name)
        if val is None:
            continue
        if (not pool_present or attr_name in pool_present) or (val != '' and val != 0):
            root.set(attr_name, str(val))
    ```
  - [x] SubTask 4.4: Grep `if.*ency.*warning|if.*warning.*system` 在 `DzhPoolConverter` 类内 = 0（if/elif 已表驱动）
  - [x] SubTask 4.5: `python -m pytest metatest/ -x -q 2>&1 | tail -5` 验证无回归
  - [x] SubTask 4.6: 净减行数 ≥ 7 行

## 阶段 4：metatest v8 量化评审升级（量化闭环）

### 架构工程师任务

- [x] Task 5: 变更 M1 — scoring.py oop_inheritance_depth 维度深化
  - [x] SubTask 5.1: Read `metatest/scoring.py:544-574` 确认 v7 `_score_oop_inheritance_depth` 4 条件评分逻辑
  - [x] SubTask 5.2: 评分标准升级：4 条件 → 6 条件（原 4 + `parse_pool_in_base` + `export_pool_in_base`），每条件占 100/6 ≈ 16.67%
  - [x] SubTask 5.3: 权重保持 6.144%（不变）
  - [x] SubTask 5.4: 21 维权重和 = 100%，PASS 条件保持（总分 ≥ 95 且 21 维均 ≥ 80）

- [x] Task 6: 变更 M2 — runner.py oop_inheritance 采集扩展
  - [x] SubTask 6.1: Read `metatest/runner.py:1391-1460` 确认 v7 `_collect_oop_inheritance` 4 字段采集
  - [x] SubTask 6.2: 新增 `parse_pool_in_base` 字段采集：AST 检查 `BasePoolConverter` 类体内是否定义 `parse_pool` 方法
  - [x] SubTask 6.3: 新增 `export_pool_in_base` 字段采集：AST 检查 `BasePoolConverter` 类体内是否定义 `export_pool` 方法
  - [x] SubTask 6.4: `test_results["oop_inheritance"]` 字典新增 2 字段

- [x] Task 7: 变更 M3 — 正反合测试 v8 升级
  - [x] SubTask 7.1: Read `metatest/test_positive_oop_inheritance.py` 确认 v7 4 条件断言
  - [x] SubTask 7.2: 升级断言：新增 `parse_pool` 模板方法在 `BasePoolConverter` 中定义
  - [x] SubTask 7.3: 升级断言：新增 `export_pool` 模板方法在 `BasePoolConverter` 中定义
  - [x] SubTask 7.4: 新增断言：4 个模块级函数（`parse_dzh_xml` / `parse_tdx_xml` / `export_dzh_xml` / `_build_tdx_xml`）函数体 ≤ 3 行（薄包装委托）
  - [x] SubTask 7.5: `python -m pytest metatest/test_positive_oop_inheritance.py -v` 退出码 0

- [x] Task 8: 变更 M4 — metatest/README.md v8 文档更新
  - [x] SubTask 8.1: 新增主流程模板方法说明（`parse_pool` / `export_pool` 在 `BasePoolConverter`，子类覆盖 11 个差异钩子）
  - [x] SubTask 8.2: 更新 oop_inheritance_depth 维度说明（4 条件 → 6 条件）

## 阶段 5：RULES + 全量回归

### 架构工程师任务

- [x] Task 9: 变更 R1 — RULES.md 新增第 118 条
  - [x] SubTask 9.1: 在 RULES.md 末尾新增第 118 条：「Converter 主流程必须模板方法化（`parse_pool` / `export_pool` 在 `BasePoolConverter` 编排骨架，子类仅覆盖差异钩子 `_decode_source` / `_extract_pool_meta` / `_parse_cells` / `_parse_flows` / `_build_result` / `_create_root` / `_serialize_pool_attrs` / `_serialize_cells` / `_serialize_flows` / `_finalize_xml`），禁止重新引入模块级并行主流程函数（模块级函数仅作薄包装委托）」
  - [x] SubTask 9.2: Grep `^118\.` 在 RULES.md = 1

### 评审工程师任务

- [x] Task 10: 变更 R2 — 全量回归
  - [x] SubTask 10.1: `python -m pytest metatest/ -x -q` 退出码 0（全量测试通过）
  - [x] SubTask 10.2: `python -m metatest.runner` 总分 ≥ 95 且 21 维均 ≥ 80，状态 PASS
  - [x] SubTask 10.3: `python -m eventtest.run_eventtest` 退出码 0（全绿）
  - [x] SubTask 10.4: `wc -l converters.py` 净减 ≥ 30 行（v7 基线 5870，v8 目标 ≤ 5840）
  - [x] SubTask 10.5: Grep `def parse_pool\b|def export_pool\b` 在 `BasePoolConverter` 类内 = 1 各
  - [x] SubTask 10.6: Grep `def _decode_source\b|def _extract_pool_meta\b|def _parse_cells\b|def _parse_flows\b|def _build_result\b` 在 `BasePoolConverter` 类内 = 1 各
  - [x] SubTask 10.7: Grep `def _create_root\b|def _serialize_pool_attrs\b|def _serialize_cells\b|def _serialize_flows\b|def _finalize_xml\b` 在 `BasePoolConverter` 类内 = 1 各
  - [x] SubTask 10.8: oop_inheritance_depth 维度 = 100（6 条件全满足）
  - [x] SubTask 10.9: DZH↔TDX roundtrip 保真（导入导出互转 pool_config 一致）
  - [x] SubTask 10.10: essence_ratio ≥ 16%（metatest 维度满分）
  - [x] SubTask 10.11: 无任何变更净增行数（essence_ratio 反测试通过）
  - [x] SubTask 10.12: adapter_isomorphism 维度 = 100（v7 保持）
  - [x] SubTask 10.13: dispatcher_isomorphism 维度 = 100（v5/v6/v7 保持）
  - [x] SubTask 10.14: runtime_verification 维度 = 100（v5/v6/v7 保持）
  - [x] SubTask 10.15: eventtest_regression 维度 = 100（v5/v6/v7 保持）

# Task Dependencies
- Task 2/3 依赖 Task 1（子类钩子覆盖需基类模板方法已定义）
- Task 4 依赖 Task 3（if/elif 表驱动在 DzhPoolConverter._serialize_pool_attrs 内实施）
- Task 5-8 依赖 Task 1-4（采集与测试需模板方法已落地）
- Task 9 依赖 Task 1-4（RULES 文档化已落地成果）
- Task 10 依赖 Task 1-9（全量回归）
