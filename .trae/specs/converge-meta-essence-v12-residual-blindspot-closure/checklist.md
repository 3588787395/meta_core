# Checklist

## 架构工程师检查点（实施前自检）

- [ ] 已阅读 spec.md「Why」章节并理解：v12 是「v11 清理残留盲区闭合」——v11 删除 DZHPoolExecutor 轮询基础设施但保留 execute_once，同时把 /pool/run 改委托 PoolEngine.execute_pool，导致 execute_once 失去唯一调用方，DZHPoolExecutor 整个类变为死代码，但 metatest 字符串正则假阳性致漏判
- [ ] 已理解本次迭代核心是「清理自身残留 + 检测器硬化」——v11 的「全局上限」声明在 v12 闭合残留 + 检测器缺陷 + services 盲区后才真正成立
- [ ] 已完成 3 路并行架构审计（同构函数 / 轮询死代码 / OOP 继承纯度）确认 v11 漏判的 4 处高优先级盲区
- [x] 已阅读 `converters.py` DZHPoolExecutor 类完整定义（v11 后约 line 3173，含 execute_once / NodeStateMachine / _init_nodes_edges 等）
- [x] 已 Grep `\.execute_once\(` 全仓 .py 文件确认零调用方（PoolEngine.execute_pool 是唯一一次性执行入口）
- [x] 已 Grep `import DZHPoolExecutor` / `DZHPoolExecutor\(` 全仓 .py 文件确认零导入零实例化
- [x] 已阅读 `converters.py:76-130` 确认 `_StkIO` / `_StkWriter` 两个混入类的 `if fmt == "tdx"` 分支
- [x] 已阅读 `metatest/runner.py:150-163` 确认 `POLLING_PATTERNS` 12 项无 `threading.Timer` 检测
- [x] 已阅读 `metatest/runner.py:152` 确认 `asyncio\.sleep.*\n.*while` 正则方向反转（要求 sleep 在 while 之前）
- [x] 已阅读 `metatest/runner.py:2414-2521` 确认 `_collect_dead_code_violations` 用字符串正则 `\bClassName\b` 匹配 ref_blob（docstring 假阳性病根）
- [x] 已阅读 `metatest/runner.py:1057` 确认 check #23/#25 只扫 `_CORE_DIR` 不扫 services/*.py（_to_float 盲区病根）
- [x] 已阅读 `services/data.py:2733-2740` 确认 `_to_float` 本地重复定义（与 converters_common.safe_cast 同构）
- [x] 已 Grep `market_map\s*=\s*\{0:` 全仓确认 17 处内联字典
- [x] 已 Grep `services/data.py` 7 处 per-member 循环（1493/1626/1745/2014/2125/2231/2279）确认逐字同构
- [x] 已 Grep `code\.startswith\(\(['\"]0['\"]` 在 services/*.py / api.py 确认 4 处 if/elif 链
- [x] 已阅读 `api.py:1454` 确认 `ErrorResponse` 死类（BaseModel 派生，零实例化零导入）
- [x] 已确认「合并非拆分」硬约束延续：DZHPoolExecutor 整体删除而非保留 execute_once；_StkIO/_StkWriter 钩子化到 BasePoolConverter 而非新建文件

## 评审工程师检查点（阶段 1：DZHPoolExecutor 完全消除）

### 变更 R1 — 删除 DZHPoolExecutor 整个类
- [x] `DZHPoolExecutor` 类整体已删除（含 execute_once / NodeStateMachine / _init_nodes_edges / _init_mock_stocks / _build_mock_tick / _normalize_edge 等全部成员）
- [x] Grep `DZHPoolExecutor` 在 converters.py 零匹配
- [x] Grep `NodeStateMachine` 全仓 .py 文件零匹配（传递死代码已随 DZHPoolExecutor 删除）
- [x] Grep `\.execute_once\(` 全仓 .py 文件零匹配（PoolEngine.execute_pool 是唯一一次性执行入口）
- [x] `python -c "import converters; print('import OK')"` 验证

### 变更 R2 — 删除 converters.py 内 DZHPoolExecutor 的所有模块级引用
- [x] Grep `DZHPoolExecutor` 全仓 .py 文件零匹配（不含 metatest docstring，由 R3 处理）
- [x] Grep `_DZH_POOL_EXECUTOR|_dzh_pool_executor` 全仓零匹配

### 变更 R3 — 删除 metatest 内 DZHPoolExecutor 的 docstring 假阳性源
- [x] metatest/runner.py 中提及 DZHPoolExecutor 的 docstring 已改为不引用具体类名
- [x] metatest/test_negative_polling.py 中提及 DZHPoolExecutor 的 docstring / 注释已改
- [x] Grep `DZHPoolExecutor` 在 metatest/ 零匹配（或仅在 test_positive_no_dead_code.py 的负向测试 fixture 中保留）
- [x] `python -c "import metatest.runner; print('import OK')"` 验证

### 阶段 1 整体验证
- [x] `python -m pytest metatest/test_positive_no_dead_code.py -v` 退出码 0（DZHPoolExecutor 不再被误判为 alive）
- [x] `python -c "from converters import DzhPoolConverter, TdxPoolConverter; print('converters OK')"` 验证 DZH/TDX 子类未受影响

## 评审工程师检查点（阶段 2：_StkIO/_StkWriter if fmt 分支消除）

### 变更 S1 — 提取 `_parse_stks` / `_write_stks` 钩子到 BasePoolConverter
- [x] `BasePoolConverter` 新增 `_parse_stks(self, cell_elem) -> list` 钩子方法（默认 DZH 实现）
- [x] `BasePoolConverter` 新增 `_write_stks(self, cell_elem, source) -> None` 钩子方法（默认 DZH 实现）
- [x] Grep `def _parse_stks|def _write_stks` 在 BasePoolConverter 类内 = 2 匹配

### 变更 S2 — DzhPoolConverter 与 TdxPoolConverter 覆盖钩子
- [x] `DzhPoolConverter` 继承默认 DZH 实现（或覆盖为 DZH 分支逻辑）
- [x] `TdxPoolConverter` 覆盖 `_parse_stks` 为 TDX 分支逻辑
- [x] `TdxPoolConverter` 覆盖 `_write_stks` 为 TDX 分支逻辑
- [x] Grep `def _parse_stks|def _write_stks` 在 TdxPoolConverter 类内 = 2 匹配

### 变更 S3 — 删除 _StkIO / _StkWriter 两个混入类
- [x] `_StkIO` 类整体已删除（converters.py:76-101，约 26 行）
- [x] `_StkWriter` 类整体已删除（converters.py:104-130，约 27 行）
- [x] 调用点已改为 `converter._parse_stks(...)` / `converter._write_stks(...)`（通过 converter 单例分派）
- [x] 模块级 `_parse_stk_children` / `_parse_stk_elements` / `_export_field_stocks` 已删除或改为薄包装委托
- [x] Grep `class _StkIO|class _StkWriter` 在 converters.py 零匹配
- [x] `python -c "import converters; print('import OK')"` 验证

### 变更 S4 — Grep `if fmt == "tdx"` 全仓零匹配验证
- [x] Grep `if\s+fmt\s*==\s*["\']tdx["\']` 全仓 .py 文件零匹配（不含字符串字面量与注释）
- [x] Grep `if\s+fmt\s*==\s*["\']dzh["\']` 全仓 .py 文件零匹配（不含字符串字面量与注释）
- [x] Grep `fmt\s*==\s*["\']tdx["\']` 全仓 .py 文件零匹配（含 elif / 内联 if）
- [x] `python -m pytest metatest/ -x -q --ignore=metatest/test_positive_event_panel.py` 全量测试不回归

## 评审工程师检查点（阶段 3：metatest 检测器硬化）

### 变更 M1 — `_collect_dead_code_violations` 改 AST 引用检测
- [x] 新增 `_count_ast_references(class_name, py_files) -> int` 函数（AST `ast.Name` + `ast.Attribute` + `ast.ClassDef.bases`）
- [x] `_collect_dead_code_violations` 中的 `re.findall` 调用已替换为 `_count_ast_references`（批量版 `_build_ast_reference_map` 单遍扫描）
- [x] `ref_py_files` 排除 metatest/ 自身（避免自引用污染）
- [x] `python -c "from metatest.runner import _collect_dead_code_violations; r=_collect_dead_code_violations(); print(r); assert r['count'] == 0"` 验证零违规
- [x] 构造临时 .py 文件含仅在 docstring 中提及的死类，验证 AST 检测返回 count > 0（假阳性消除）

### 变更 M2 — `POLLING_PATTERNS` 新增 `threading.Timer` 检测
- [x] `POLLING_PATTERNS` 新增第 13 项 `r"threading\.Timer\s*\("`
- [x] `_file_uses_threading_thread` 扩展为 `_file_uses_threading_primitives`（同时检测 Thread 与 Timer）
- [x] `_collect_parallel_runtime_violations` 调用更新后的 `_file_uses_threading_primitives`
- [x] Grep `threading\.Timer` 全仓 .py 文件零匹配（生产代码）
- [x] `python -c "from metatest.runner import _collect_parallel_runtime_violations; r=_collect_parallel_runtime_violations(); print(r); assert r['violations'] == 0"` 验证零违规

### 变更 M3 — 修正 `asyncio\.sleep.*\n.*while` 正则方向
- [x] 正则已改为 `r"while\s+[^:]*:[^\n]*\n[^\n]*asyncio\.sleep\("`（while 在前，sleep 在循环体内）或由 M4 结构性 AST 检测覆盖
- [x] 构造临时 .py 文件含 `while True:\n    await asyncio.sleep(0.5)`，验证检测器捕获

### 变更 M4 — 新增结构性 AST 轮询检测
- [x] 新增 `_collect_structural_polling_violations() -> dict` 函数（`ast.While` + `time.sleep`/`asyncio.sleep`/sync `.wait`）
- [x] 支持 `# noqa: event-driver` 排除
- [x] 扫描范围 `core/` + `services/` + `app.py` + `converters.py`
- [x] `python -c "from metatest.runner import _collect_structural_polling_violations; r=_collect_structural_polling_violations(); print(r); assert r['violations'] == 0"` 验证零违规
- [x] 在 `_collect_isomorphism_violations` 新增检查项：结构性 AST 轮询零违规

### 变更 M5 — metatest check #23/#25 扫描范围扩展到 services/*.py
- [x] check #23/#25 扫描范围从 `_CORE_DIR` 扩展到 `_CORE_DIR + services/*.py`
- [x] Task 15 完成后验证 check #23/#25 在 services/*.py 扫描零匹配（_to_float 已删除）
- [x] `python -c "from metatest.runner import _collect_isomorphism_violations; r=_collect_isomorphism_violations(); print(r)"` 验证

### 变更 M6 — 删除 ErrorResponse 死类
- [x] `api.py:1454` 的 `ErrorResponse` 类已删除
- [x] `_PRE_EXISTING_DEAD_CLASSES` 豁免列表已删除或改为空 `frozenset()`
- [x] Grep `ErrorResponse` 全仓 .py 文件零匹配
- [x] `python -c "import api; print('import OK')"` 验证

### 变更 M7 — ISOMORPHISM_CHECKS_TOTAL 扩展 44 → 48
- [x] `ISOMORPHISM_CHECKS_TOTAL = 48`（从 44 扩展）
- [x] 新增第 45 项检查：`if fmt == "tdx"` 全仓零匹配
- [x] 新增第 46 项检查：`threading.Timer` 零匹配
- [x] 新增第 47 项检查：结构性 AST 轮询零违规
- [x] 新增第 48 项检查：AST 死代码检测零假阳性
- [x] test_results 新增 `if_fmt_tdx_violations` / `threading_timer_violations` / `structural_polling_violations` / `ast_dead_code_false_positives` 四个字段
- [x] `python -c "from metatest.scoring import ISOMORPHISM_CHECKS_TOTAL; print(ISOMORPHISM_CHECKS_TOTAL); assert ISOMORPHISM_CHECKS_TOTAL == 48"` 输出 48

## 评审工程师检查点（阶段 4：services 盲区闭合）

### 变更 V1 — 合并 `_to_float` 到 `converters_common.safe_cast`
- [x] `services/data.py:2733-2740` 的 `_to_float` 定义已删除
- [x] 调用点已改为 `safe_cast(value, float, None)`（保留 None 语义）
- [x] services/data.py 顶部已新增 `from converters_common import safe_cast`
- [x] Grep `^def _to_float` 在 services/data.py 零匹配
- [x] `python -c "import services.data; print('import OK')"` 验证

### 变更 V2 — `market_map` 内联字典 ×17 统一
- [x] `core/domain.py` 新增 `SETCODE_MARKET_MAP = {0:"SZ",1:"SH",2:"BJ"}` 常量
- [x] `converters.py:4229` 的 `SETCODE_MARKET_MAP` 改为从 `core.domain` 导入（或保留定义并删除 domain.py 的，但 domain.py 是更规范位置）
- [x] services/data.py 9 处内联字典已改为 `from core.domain import SETCODE_MARKET_MAP`
- [x] services/providers.py 4 处内联字典已改
- [x] api.py 4 处内联字典已改
- [x] Grep `market_map\s*=\s*\{0:` 全仓 .py 文件零匹配
- [x] `python -c "import services.data, services.providers, api; print('import OK')"` 验证

### 变更 V3 — 成分股构建 per-member 循环 ×7 合并
- [x] 新增 `_build_member_entries(members, default_market='SZ') -> list` 助手
- [x] 7 处调用点已改为委托 `_build_member_entries`
- [x] 1493 变体的 `if market_prefix else code` 差异已在助手里保留
- [x] Grep `member_entries\.append\(\{'stock_code'` 在 services/data.py 仅命中 `_build_member_entries` 内部 1 处
- [x] `python -c "import services.data; print('import OK')"` 验证

### 变更 V4 — 代码前缀→市场 setcode if/elif 链 ×4 表驱动
- [x] `core/domain.py` 新增 `_classify_market_by_code(code) -> int`（`0/3→0`、`6→1`、`4/8/9/920→2`，修复 4 前缀缺口）
- [x] `core/trade_module.py:72 _code_to_market` 改为委托 `_classify_market_by_code` 或直接修复 4 前缀
- [x] services/data.py:1498 if/elif 链已替换为查表调用
- [x] services/providers.py:7445,7943 if/elif 链已替换
- [x] api.py:3306 if/elif 链已替换
- [x] Grep `code\.startswith\(\(['\"]0['\"]` 在 services/*.py / api.py 零匹配
- [x] `python -c "from core.domain import _classify_market_by_code; assert _classify_market_by_code('000001')==0; assert _classify_market_by_code('600000')==1; assert _classify_market_by_code('430001')==2; assert _classify_market_by_code('830001')==2; print('OK')"` 验证 4 前缀修复

## 评审工程师检查点（阶段 5：RULES 修订 + 全量回归）

### RULES 120 再次修订 + 122/123 新增
- [ ] 第 120 条修订为「清理自身残留 + 检测器硬化后的全局收敛上限」
- [ ] 新增第 122 条「清理自身残留」纪律
- [ ] 新增第 123 条「检测器是约束的执行者」纪律
- [ ] Grep `^122\.` 在 RULES.md = 1
- [ ] Grep `^123\.` 在 RULES.md = 1

### 全量回归
- [ ] `python -m pytest metatest/ -x -q --ignore=metatest/test_positive_event_panel.py` 全量测试通过
- [ ] handler_exception_coverage = 100%（v10/v11 成果不回归）
- [ ] parallel_runtime_violations = 0（v11 保持 + v12 threading.Timer 检测扩展）
- [ ] dead_code_violations = 0（v12 AST 检测，含 DZHPoolExecutor + ErrorResponse 已删除）
- [ ] structural_polling_violations = 0（v12 新增，零违规）
- [ ] `python -m eventtest.run_eventtest` 退出码 0（全绿）
- [ ] Grep `DZHPoolExecutor` 全仓 .py 文件零匹配
- [ ] Grep `class _StkIO|class _StkWriter` 全仓零匹配
- [ ] Grep `if\s+fmt\s*==\s*["\']tdx["\']|if\s+fmt\s*==\s*["\']dzh["\']` 全仓 .py 文件零匹配
- [ ] Grep `threading\.Timer` 全仓 .py 文件零匹配（生产代码）
- [ ] Grep `market_map\s*=\s*\{0:` 全仓 .py 文件零匹配
- [ ] Grep `^def _to_float` 在 services/*.py 零匹配
- [ ] Grep `ErrorResponse` 全仓 .py 文件零匹配
- [ ] `python -c "from metatest.scoring import ISOMORPHISM_CHECKS_TOTAL; print(ISOMORPHISM_CHECKS_TOTAL); assert ISOMORPHISM_CHECKS_TOTAL == 48"` 输出 48
- [ ] oop_inheritance_depth 维度 = 100（v9-v11 保持 + v12 _StkIO/_StkWriter 钩子化后继承纯度提升）
- [ ] isomorphism_elimination 维度 = 100（48 项 0 违规，含新增 4 项）
- [ ] handler_exception_coverage 维度 = 100（v10/v11 保持）
- [ ] DZH↔TDX roundtrip 保真（19 roundtrip 测试全过）
- [ ] essence_ratio 维度提升（净 −320 ~ −400 行）
- [ ] adapter_isomorphism 维度 = 100（v7-v11 保持）
- [ ] dispatcher_isomorphism 维度 = 100（v5-v11 保持）
- [ ] runtime_verification 维度 = 100（v5-v11 保持）
- [ ] eventtest_regression 维度 = 100（v5-v11 保持）

## 第十二层洞察根因检查点（评审工程师最终验收）

- [ ] **清理自身的残留是收敛上限的下一个敌人**：v11 删除 DZHPoolExecutor 轮询基础设施但保留 execute_once，同时把 /pool/run 改委托 PoolEngine.execute_pool，导致 execute_once 失去唯一调用方——DZHPoolExecutor 整个类变为死代码。v12 闭合此残留，整个类删除（含 execute_once / NodeStateMachine / _init_nodes_edges 等传递死代码）
- [ ] **运行时单一真相源的彻底性**：v11 把 /pool/run 委托 PoolEngine.execute_pool 后，execute_once 与 execute_pool 已不再是「同构两个真相源」——而是「死代码 vs 唯一真相源」。v12 消除死代码，运行时只有一个真相源（PoolEngine）
- [ ] **OOP 继承纯度的彻底性**：v11 在 _call_converter 消除 if fmt == "tdx" 分支，但 _StkIO/_StkWriter 残留同一反模式。v12 提取 _parse_stks/_write_stks 钩子到 BasePoolConverter，子类覆盖，全仓 if fmt == "tdx" 零匹配——「大智慧和通达信只作为继承，所有基础功能用相同代码」彻底满足
- [ ] **检测器是约束的执行者**：v11 spec 点名 threading.Timer 但无检测器；asyncio.sleep 正则方向反转；NAME-BASED 检测换名漏检；字符串匹配 docstring 假阳性。v12 闭合 4 处缺陷（AST 死代码 + threading.Timer + 结构性 AST 轮询 + 正则修正），使「零容忍」声明变为「零执行」
- [ ] **审计盲区的同构性**：v11 闭合 converters.py 盲区，但 services/*.py 盲区同源（metatest check #23/#25 只扫 _CORE_DIR 不扫 services）。v12 扩展扫描范围到 services/*.py，闭合 _to_float + market_map ×17 + per-member 循环 ×7 + if/elif ×4 同构合并点
- [ ] **死代码零容忍的彻底性**：ErrorResponse 预存豁免与 spec SHALL NOT 矛盾。v12 删除 ErrorResponse，移除 _PRE_EXISTING_DEAD_CLASSES 豁免，死代码检测零豁免
- [ ] **非拆分非重写**：DZHPoolExecutor 整体删除（非保留 execute_once 孤儿）；_StkIO/_StkWriter 钩子化到 BasePoolConverter（非新建文件）；同构函数合并为单一规范版
- [ ] **量化评审驱动**：isomorphism_elimination 维度新增 4 项检查（if fmt 零匹配 / threading.Timer 零匹配 / 结构性轮询零违规 / AST 死代码零假阳性），44→48 项，使评分体系能驱动清理残留 + 检测器硬化
- [ ] **诚实声明确认**：v12 不是对 v11 的否定——v11 在 converters.py / core/ 内的收敛是真实的（_to_frontend 钩子 + TDX 函数归入子类 + 5 处同构合并）。v12 是对 v11「全局上限」声明的彻底性修正：v11 清理自身残留（DZHPoolExecutor 死代码）+ 检测器 4 处缺陷 + services 盲区在 v12 闭合后，全局收敛上限声明才真正成立
