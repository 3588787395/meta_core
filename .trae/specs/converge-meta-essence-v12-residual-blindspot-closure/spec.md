# 元模式本质收敛 v12：v11 清理残留盲区闭合 + 检测器硬化 Spec

## Why

v11 文档化了「审计盲区闭合后的全局收敛上限」(RULES 120 修订 + 121 新增），并据此声明全局上限真正成立。但 v12 架构工程师跨域深度审计发现 **v11 的「上限」再次是审计盲区制造的假象**——v11 自身清理留下的残留 + 检测器缺陷共 4 处高优先级盲区，均击穿了 v11 的结论。

**第十二层洞察（清理自身的残留是收敛上限的下一个敌人）**：v11 变更 D1 删除了 `DZHPoolExecutor` 的轮询基础设施（`_run_loop`/`start`/`stop`/`_log_event` + 7 个字段），但**保留了 `execute_once()`**。同时 v11 变更 D3 把 `/pool/run` 端点改委托 `PoolEngine.execute_pool(config)`，导致 `execute_once()` 失去唯一调用方——`DZHPoolExecutor` 整个类变为死代码。但 v11 的 `_collect_dead_code_violations`（runner.py:2503-2515）用字符串正则 `\bClassName\b` 在 `ref_blob`（全部 .py 含 metatest/）中计数引用，而 metatest/runner.py 与 test_negative_polling.py 的 docstring 多次提及 `DZHPoolExecutor._run_loop`（描述 v11 清理历史），导致 `total_refs(8+) > def_refs(1)`，**假阳性误判为 alive**。这是 v11「审计盲区是收敛上限的最大敌人」第十一层洞察在 v12 的直接复现：清理自身的残留是下一个敌人。

**真正的底层运行逻辑洞察**：
1. **运行时单一真相源的彻底性**：v11 把 `/pool/run` 委托 `PoolEngine.execute_pool()` 后，`DZHPoolExecutor.execute_once()` 与 `PoolEngine.execute_pool()` 已不再是「同构两个真相源」——而是「死代码 vs 唯一真相源」。运行时单一真相源要求不仅消除平行运行时，还要消除失去调用方的残留一次性执行入口。`NodeStateMachine` / `_init_nodes_edges` / `_init_mock_stocks` 是 `DZHPoolExecutor` 的传递死代码（仅被死类的构造函数实例化）。
2. **OOP 继承纯度的彻底性**：v11 在 `_call_converter` 消除了 `if fmt == "tdx"` 分支（变更 G1），但 `_StkIO.parse_stks`（converters.py:80）/ `_StkWriter.write_stks`（converters.py:113）仍残留同一反模式——v4 创建 `_StkIO`/`_StkWriter` 时把两个函数塞进一个带 `fmt` 分支的混入类，与 OOP 继承纯度方向相反。用户硬约束「大智慧和通达信只作为继承，所有基础功能用相同代码」要求**所有** `if fmt == "tdx"` 分支彻底消除，不止 `_call_converter` 一处。
3. **检测器是约束的执行者，不是约束本身**：v11 spec 明确点名 `threading.Timer` 绕过 EventDriver（spec.md:7），但 `POLLING_PATTERNS`（runner.py:150-163）12 项中**无一项**检测 `threading.Timer`——v11 识别的违规模式无检测器 enforce。同理 `asyncio\.sleep.*\n.*while` 正则要求 sleep 在 while 之前（runner.py:152），但常见 polling 模式是 `while True:\n    await asyncio.sleep(0.5)`（while 在前），**正则方向反转致核心反轮询检测失效**。`_collect_dead_code_violations` 的字符串匹配把 docstring/注释/字符串字面量中的类名误计为 alive（盲区 1 的直接病根）。检测器缺陷使 v11 的「零容忍」声明变为「零执行」。
4. **审计盲区的同构性**：v11 闭合了 converters.py 轮询盲区（metatest 扫描扩展到 converters.py），但 `_to_float`（services/data.py:2733）盲区与 v11 的 DZHPoolExecutor 盲区**同源**——metatest check #23/#25（runner.py:1057）只扫 `_CORE_DIR`（core/*.py），不扫 services/*.py，使 `services/data.py:2733 _to_float` 漏网。RULES 104 明文禁止在 services/*.py 模块级重新定义 `_to_float`，但检测器未覆盖。

**诚实声明**：v12 不是对 v11 的否定——v11 在 converters.py / core/ 内的收敛是真实的，`_to_frontend` 钩子 + TDX 函数归入子类 + 同构函数合并 5 处都是真合并。v12 是对 v11「全局上限」声明的**彻底性修正**：v11 清理自身留下了 DZHPoolExecutor 残留死代码 + 检测器 4 处缺陷 + services 盲区，v12 闭合这些残留后全局收敛上限声明才真正成立。

## What Changes

### 阶段 1：DZHPoolExecutor 完全消除（v11 清理残留死代码，最大单项收益）

- **变更 R1：删除 DZHPoolExecutor 整个类**。`converters.py:3173` 的 `DZHPoolExecutor` 类在 v11 删除轮询基础设施 + api.py 改委托 PoolEngine 后已无任何调用方（全仓 `.execute_once(` 在 .py 文件零匹配，`import DZHPoolExecutor` 在 .py 文件零匹配）。删除整个类，含 `execute_once` / `NodeStateMachine`（传递死代码，仅被 DZHPoolExecutor.__init__ 实例化）/ `_init_nodes_edges` / `_init_mock_stocks` / `_build_mock_tick` / `_normalize_edge` 等全部成员。净 −200 ~ −250 行。
- **变更 R2：删除 converters.py 内 DZHPoolExecutor 的所有模块级引用**。Grep `DZHPoolExecutor` 在 converters.py 确认仅命中类定义本身（v11 已清除 api.py / __init__.py 引用）。若 converters.py 顶部有 `class DZHPoolExecutor` 之外的引用（如 `_DZH_POOL_EXECUTOR` 单例），一并删除。
- **变更 R3：删除 metatest 内 DZHPoolExecutor 的 docstring 假阳性源**。metatest/runner.py 与 metatest/test_negative_polling.py 的 docstring 多次提及 `DZHPoolExecutor._run_loop`（描述 v11 清理历史），是 _collect_dead_code_violations 假阳性的直接病根。将这些 docstring 改为不引用具体类名（如「v11 已删除的轮询执行器」），或保留但确保新检测器（变更 R5）用 AST 而非字符串匹配消除假阳性。

### 阶段 2：_StkIO/_StkWriter `if fmt == "tdx"` 分支消除（OOP 继承纯度彻底性）

- **变更 S1：提取 `_parse_stks` / `_write_stks` 钩子到 BasePoolConverter**。`converters.py:80 _StkIO.parse_stks` 内 `if fmt == "tdx": ... else: ...` 与 `converters.py:113 _StkWriter.write_stks` 内 `if fmt == "tdx": ... else: ...` 是 v11 在 `_call_converter` 已消除的同一反模式残留。修复：在 `BasePoolConverter` 新增 `_parse_stks(self, cell_elem) -> list` 与 `_write_stks(self, cell_elem, source) -> None` 钩子（默认实现为 DZH 分支逻辑，因 DZH 是默认格式），`TdxPoolConverter` 覆盖为 TDX 分支逻辑。
- **变更 S2：DzhPoolConverter 补齐 `_parse_stks` / `_write_stks` 方法**。当前 `DzhPoolConverter` 无 `add_stks` / `parse_stks` 方法，DZH 写入走模块级自由函数 `_export_field_stocks`（converters.py:3936-3938）。将 `_export_field_stocks` 收编为 `DzhPoolConverter._write_stks`，与 `TdxPoolConverter.add_stks`（converters.py:790）对称。
- **变更 S3：删除 _StkIO / _StkWriter 两个混入类**。钩子提取后，`_StkIO`（converters.py:76-101）/ `_StkWriter`（converters.py:104-130）两个混入类整体删除。模块级 `_parse_stk_children` / `_parse_stk_elements` / `_export_field_stocks` 若仅被混入类调用则一并删除，若被外部调用则保留为薄包装委托到 `BasePoolConverter._parse_stks` / `_write_stks`。净 −40 ~ −60 行。
- **变更 S4：Grep `if fmt == "tdx"` 全仓零匹配**。v11 在 `_call_converter` 消除后，`_StkIO`/`_StkWriter` 是最后两处残留。S1-S3 完成后，全仓 `if fmt == "tdx"` / `if fmt == "dzh"` 零匹配（不含字符串字面量与注释）。

### 阶段 3：metatest 检测器硬化（v11 声明约束的执行闭环）

- **变更 M1：`_collect_dead_code_violations` 改 AST 引用检测**。当前用 `re.findall(r"\bClassName\b", ref_blob)` 字符串匹配（runner.py:2503-2515），docstring/注释/字符串字面量中的类名被误计为 alive。改为 AST `ast.Name` 节点 + `ast.Attribute.attr` 字符串 + `ast.ClassDef.bases` 名称检查，排除 docstring/字符串字面量/注释。同时 `ref_py_files` 排除 metatest/ 自身（与 `def_py_files` 对齐），避免 metatest 描述被检测对象时产生自引用污染。
- **变更 M2：`POLLING_PATTERNS` 新增 `threading.Timer` 检测**。v11 spec.md:7 明确点名 `threading.Timer` 绕过 EventDriver，但 `POLLING_PATTERNS`（runner.py:150-163）12 项无一项检测。新增第 13 项 `r"threading\.Timer\s*\("`。同时 `_collect_parallel_runtime_violations` 的 `_file_uses_threading_thread` 扩展为 `_file_uses_threading_primitives`，同时检测 `threading.Thread` 与 `threading.Timer`。
- **变更 M3：修正 `asyncio\.sleep.*\n.*while` 正则方向**。当前正则要求 sleep 在 while 之前（runner.py:152），但常见 polling 模式是 `while True:\n    await asyncio.sleep(0.5)`（while 在前）。改为 `r"while\s+[^:]*:[^\n]*\n[^\n]*asyncio\.sleep\("` 或用 AST 检测 `ast.While` 循环体内含 `asyncio.sleep()` 调用（结构性而非文本性）。
- **变更 M4：新增结构性 AST 轮询检测**。当前 `POLLING_PATTERNS` 12 项中 7 项是 NAME-BASED（`start_polling` / `_file_watcher_loop` / `_sync_play_loop` / `_sync_sim_loop` / `auto_step_loop` / `while self._run\b` / `while self._sim_auto_step\b`），新轮询方法换名即漏检。新增 `_collect_structural_polling_violations()` 函数：扫描 `ast.While` 节点，循环体内含 `time.sleep()` / `asyncio.sleep(\d)` / sync `.wait(\d)` 任一即违规（含 `# noqa: event-driver` 排除）。扫描范围 core/ + services/ + app.py + converters.py（与 `_collect_polling_violations` 对齐）。
- **变更 M5：metatest check #23/#25 扫描范围扩展到 services/*.py**。当前 check #23/#25（runner.py:1057）只扫 `_CORE_DIR`（core/*.py），不扫 services/*.py，使 `services/data.py:2733 _to_float` 漏网（RULES 104 违规）。扩展扫描范围到 `services/*.py`，闭合 services 盲区。
- **变更 M6：删除 ErrorResponse 死类**。`api.py:1454` 的 `ErrorResponse`（BaseModel 派生类，~3 行）是 `_PRE_EXISTING_DEAD_CLASSES` 豁免的预存死类。v11 spec.md:80-85 明确「SHALL NOT contain any class that is never instantiated and never imported」，豁免与 spec 矛盾。删除 ErrorResponse 类，移除 `_PRE_EXISTING_DEAD_CLASSES` 豁免（runner.py:2395-2397）。
- **变更 M7：ISOMORPHISM_CHECKS_TOTAL 扩展 44 → 48**。新增 4 项检查：(1) `if fmt == "tdx"` 全仓零匹配（OOP 纯度彻底性）、(2) `threading.Timer` 零匹配（v11 spec 点名模式）、(3) 结构性 AST 轮询零违规（NAME-BASED 兜底）、(4) AST 死代码检测零假阳性（docstring 排除验证）。

### 阶段 4：services 盲区闭合（同构病根根除）

- **变更 V1：合并 `services/data.py:2733 _to_float` 到 `converters_common.safe_cast`**。`_to_float`（services/data.py:2733-2740）与 `converters_common.safe_float` / `safe_cast(v, float, default)` 算法同构（安全 float 转换），仅默认值语义差异（None vs 0.0）。删除 `_to_float` 定义，调用点改用 `from converters_common import safe_cast` + `safe_cast(value, float, None)` 保留 None 语义。净 −8 行。
- **变更 V2：`market_map = {0:'SZ',1:'SH',2:'BJ'}` 内联字典 ×17 统一**。17 处内联字典（services/data.py 9 处 + services/providers.py 4 处 + api.py 4 处）逐字重复，已有规范常量 `SETCODE_MARKET_MAP`（converters.py:4229）。将 `SETCODE_MARKET_MAP` 提升到 `core/domain.py`（与 `_code_to_market` 同处，作为市场映射单一真相源），17 处改为 `from core.domain import SETCODE_MARKET_MAP` 并删除内联字典。净 −17 行。
- **变更 V3：成分股构建 per-member 循环 ×7 合并**。`services/data.py` 7 处 per-member 循环（1493/1626/1745/2014/2125/2231/2279）逐字同构（含 `market_map` 内联 + `stock_code = f"{market_prefix}{code}"` + `member_entries.append({'stock_code': stock_code, 'weight': 1.0})`）。提取 `_build_member_entries(members, default_market='SZ')` 助手到 services/data.py 模块级或 Storage 类方法，7 处调用点改委托。净 −35 行。
- **变更 V4：代码前缀→市场 setcode if/elif 链 ×4 表驱动**。4 处 if/elif 链（services/data.py:1498 / services/providers.py:7445,7943 / api.py:3306）是同一前缀分类逻辑。`core/trade_module.py:72 _code_to_market(code)` 已实现但遗漏 `4` 前缀（北交所旧代码）。在 `core/domain.py` 新增规范 `_classify_market_by_code(code) -> int`（`0/3→0`、`6→1`、`4/8/9/920→2`），4 处 if/elif 链替换为查表调用。净 −20 行。

### 阶段 5：RULES 修订 + 全量回归

- **变更 L1：RULES 122 新增「清理自身残留」纪律**。新增第 122 条：「**清理自身残留**：删除平行运行时或重复实现时，必须同时删除失去调用方的残留入口与传递死代码（仅被死类实例化的 helper 类）。禁止保留 `execute_once` 等孤儿方法作为「未来可能用到的 thin wrapper」——若当前零调用方，即死代码。metatest 死代码检测必须用 AST 引用计数（排除 docstring/注释/字符串字面量），不得用字符串正则匹配。」
- **变更 L2：RULES 123 新增「检测器是约束的执行者」纪律**。新增第 123 条：「**检测器是约束的执行者**：spec 中点名的违规模式（如 `threading.Timer`）必须有对应检测器 enforce；轮询检测必须含结构性 AST 检测（`ast.While` + `time.sleep`/`asyncio.sleep`/sync `.wait`），不得仅靠 NAME-BASED 正则；检测器扫描范围必须覆盖所有声明约束的目录（如 RULES 104 覆盖 services/*.py，不得只扫 core/）。」
- **变更 L3：RULES 120 再次修订**。v11 的「审计盲区闭合后的全局收敛上限」再次修正为「**清理自身残留 + 检测器硬化后的**全局收敛上限」。v11 的上限仅适用于 v11 已闭合的盲区；v11 清理自身残留（DZHPoolExecutor 死代码）+ 检测器 4 处缺陷 + services 盲区在 v12 闭合后，全局收敛上限声明才真正成立。
- **变更 L4：全量回归**。metatest 总分 ≥ 95 且 22 维均 ≥ 80（含新增 4 项 isomorphism 检查），eventtest 退出码 0，`if fmt == "tdx"` 全仓零匹配，`threading.Timer` 零匹配，结构性 AST 轮询零违规，AST 死代码检测零假阳性，DZH↔TDX roundtrip 保真。

## Impact

- Affected specs: converge-meta-essence-v11-blindspot-closure（v11「全局上限」声明范围再次修正——v11 清理自身残留 + 检测器缺陷 + services 盲区在 v12 闭合后全局上限才真正成立）
- Affected code: `converters.py`（DZHPoolExecutor 整个类删除 + _StkIO/_StkWriter 混入类删除 + _parse_stks/_write_stks 钩子提取）、`core/import_export_module.py`（无变更，v11 已完成）、`core/domain.py`（新增 SETCODE_MARKET_MAP 常量 + _classify_market_by_code 函数）、`core/trade_module.py`（_code_to_market 修复 4 前缀缺口或委托新函数）、`services/data.py`（_to_float 合并 + market_map ×9 统一 + per-member 循环 ×7 合并 + if/elif ×1 表驱动）、`services/providers.py`（market_map ×4 统一 + if/elif ×2 表驱动）、`api.py`（market_map ×4 统一 + if/elif ×1 表驱动 + ErrorResponse 死类删除）、`converters_common.py`（无变更，已有 safe_cast）、`metatest/runner.py`（_collect_dead_code_violations 改 AST + POLLING_PATTERNS 新增 threading.Timer + asyncio.sleep 正则修正 + 新增 _collect_structural_polling_violations + check #23/#25 扫描扩展到 services + _PRE_EXISTING_DEAD_CLASSES 移除）、`metatest/scoring.py`（ISOMORPHISM_CHECKS_TOTAL 44→48）、`metatest/test_negative_polling.py`（新增 threading.Timer 断言 + 结构性轮询断言）、`metatest/test_positive_no_dead_code.py`（新增 AST 检测验证 + 负向回归测试）、`RULES.md`（120 再次修订 + 122/123 新增）

## ADDED Requirements

### Requirement: DZHPoolExecutor 完全消除
The system SHALL NOT contain any residual `DZHPoolExecutor` class, including its `execute_once` method, `NodeStateMachine` helper, `_init_nodes_edges`, `_init_mock_stocks`, or any other members. v11's retention of `execute_once` as a "potential thin wrapper" is revoked — zero callers means dead code.

#### Scenario: DZHPoolExecutor 类全仓零匹配
- **WHEN** metatest runner scans the entire codebase for `DZHPoolExecutor`
- **THEN** finds zero matches in .py files (excluding metatest docstrings describing v11/v12 cleanup history, which must not reference the class name directly)

#### Scenario: execute_once 无孤儿方法
- **WHEN** metatest runner scans for methods with zero callers
- **THEN** finds zero orphan methods (any method defined but never called is dead code)

### Requirement: _StkIO/_StkWriter if fmt 分支消除
The system SHALL NOT contain any `if fmt == "tdx"` or `if fmt == "dzh"` branch in `_StkIO` / `_StkWriter` or any other class. All format-specific stock parsing/writing logic MUST be encapsulated as `_parse_stks` / `_write_stks` hook overrides in `DzhPoolConverter` / `TdxPoolConverter` subclasses, with `BasePoolConverter` providing the default DZH implementation.

#### Scenario: _StkIO/_StkWriter 混入类删除
- **WHEN** metatest runner scans converters.py for `_StkIO` / `_StkWriter` class definitions
- **THEN** finds zero matches (hooks extracted to BasePoolConverter + subclass overrides)

#### Scenario: if fmt == "tdx" 全仓零匹配
- **WHEN** metatest runner scans all .py files for `if fmt == "tdx"` / `if fmt == "dzh"` patterns
- **THEN** finds zero matches in code (excluding string literals and comments)

### Requirement: metatest 检测器 AST 化
The system SHALL detect dead code using AST reference counting (`ast.Name` / `ast.Attribute` / `ast.ClassDef.bases`), NOT string regex matching. Docstrings, comments, and string literals SHALL NOT be counted as references. The `ref_py_files` set SHALL exclude metatest/ itself to avoid self-reference pollution.

#### Scenario: docstring 假阳性消除
- **WHEN** a class name appears only in docstrings/comments/string literals but not in actual code references
- **THEN** the class is correctly identified as dead code (count > 0)

#### Scenario: metatest 自引用污染消除
- **WHEN** metatest/runner.py docstring mentions a class name being detected
- **THEN** the mention is NOT counted as a reference (ref_py_files excludes metatest/)

### Requirement: threading.Timer 零容忍
The system SHALL NOT use `threading.Timer` for scheduling in `converters.py` / `services/*.py` / `app.py` / `api.py` / `core/*.py`. All timed scheduling MUST delegate to `EventDriver` (heapq + `loop.call_at`). `POLLING_PATTERNS` SHALL include `threading\.Timer\s*\("` as a detected pattern.

#### Scenario: threading.Timer 检测
- **WHEN** metatest runner scans for `threading.Timer` usage
- **THEN** finds zero matches in production code (excluding metatest/ test fixtures)

### Requirement: 结构性 AST 轮询检测
The system SHALL detect polling patterns using structural AST analysis (`ast.While` loop body containing `time.sleep()` / `asyncio.sleep(N)` / sync `.wait(N)`), NOT only NAME-BASED regex. The structural detector SHALL scan `core/` + `services/` + `app.py` + `converters.py` and support `# noqa: event-driver` exclusion.

#### Scenario: 新轮询方法换名仍被捕获
- **WHEN** a new polling method is introduced with a novel name (e.g., `_tick_loop`, `while self._running:`)
- **THEN** the structural AST detector catches it (NAME-BASED regex would miss it)

### Requirement: services 盲区闭合
The system SHALL scan `services/*.py` for RULES 104 violations (`_to_float` / `_cast_int` / `_cast_str` local redefinitions), NOT only `core/*.py`. The `market_map = {0:'SZ',1:'SH',2:'BJ'}` inline dict SHALL be replaced by a single `SETCODE_MARKET_MAP` constant in `core/domain.py`.

#### Scenario: _to_float services 零匹配
- **WHEN** metatest runner scans services/*.py for `def _to_float` / `def _cast_int` / `def _cast_str`
- **THEN** finds zero matches (only converters_common.safe_cast is the canonical implementation)

#### Scenario: market_map 内联字典零匹配
- **WHEN** metatest runner scans all .py files for `market_map = {0:` inline dict pattern
- **THEN** finds zero matches (SETCODE_MARKET_MAP in core/domain.py is the single source of truth)

## MODIFIED Requirements

### Requirement: 全局元模式收敛上限（v11 → v12 彻底性修正）
v11 的 RULES 120「审计盲区闭合后的全局收敛上限」声明再次修正为：**清理自身残留 + 检测器硬化后的**全局收敛上限。v11 的上限仅适用于 v11 已闭合的盲区（converters.py 轮询 + DZHPoolExecutor 平行运行时 + DzhXmlExporter 死代码 + TDX 函数归入子类 + 5 处同构合并）；v11 清理自身残留（DZHPoolExecutor 死代码）+ 检测器 4 处缺陷（threading.Timer / asyncio.sleep 正则 / NAME-BASED / 字符串匹配）+ services 盲区（_to_float + market_map ×17 + per-member 循环 ×7 + if/elif ×4）在 v12 闭合后，全局收敛上限声明才真正成立。metatest 的 isomorphism_elimination 维度从 44 项扩展到 48 项。

## REMOVED Requirements

### Requirement: DZHPoolExecutor execute_once 保留
**Reason**: v11 spec.md 变更 D1 保留 `execute_once()` 作为「一次性执行逻辑」， Migration 声明「execute_once() 一次性执行逻辑若与 PoolEngine.execute_pool() 有差异，吸收为 PoolEngine 方法或保留为 thin wrapper」。但 v11 变更 D3 把 /pool/run 端点改委托 `PoolEngine.execute_pool(config)` 后，`execute_once()` 失去唯一调用方，成为孤儿方法。v11 既未吸收为 PoolEngine 方法，也未保留为有调用方的 thin wrapper，而是悬空无调用方——这是死代码，违反 v11 spec.md:80-85「SHALL NOT contain any class that is never instantiated and never imported」。
**Migration**: 整个 DZHPoolExecutor 类删除（含 execute_once / NodeStateMachine / _init_nodes_edges / _init_mock_stocks / _build_mock_tick / _normalize_edge 等全部成员）。若 `execute_once` 的算法与 `PoolEngine.execute_pool` 有差异且未来需要，应直接在 `PoolEngine` 上实现，而非保留孤儿类。

### Requirement: _StkIO / _StkWriter 混入类
**Reason**: v4 创建 `_StkIO` / `_StkWriter` 时把两个函数塞进一个带 `fmt` 分支的混入类（`if fmt == "tdx": ... else: ...`），与 OOP 继承纯度方向相反。v11 在 `_call_converter` 消除了同一反模式，但 `_StkIO` / `_StkWriter` 残留是 v11 的漏判。
**Migration**: 提取 `_parse_stks` / `_write_stks` 钩子到 `BasePoolConverter`（默认 DZH 实现），`DzhPoolConverter` 与 `TdxPoolConverter` 覆盖为格式特异逻辑。删除 `_StkIO` / `_StkWriter` 两个混入类。

### Requirement: ErrorResponse 预存豁免
**Reason**: v11 `_PRE_EXISTING_DEAD_CLASSES` 豁免了 `ErrorResponse`（api.py:1454），但 v11 spec.md:80-85「SHALL NOT contain any class that is never instantiated and never imported」是硬性约束，无豁免例外。豁免与 spec 矛盾。
**Migration**: 删除 `ErrorResponse` 类（BaseModel 派生类，~3 行，零实例化零导入零引用）。移除 `_PRE_EXISTING_DEAD_CLASSES` 豁免列表。
