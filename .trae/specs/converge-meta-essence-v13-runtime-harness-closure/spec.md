# 元模式本质收敛 v13：运行时验证 harness 闭合 + 检测器 fail-loud 硬化 Spec

## Why

v12 闭合了 v11 清理残留 + 检测器 4 处缺陷 + services 盲区，声明全局收敛上限真正成立。但 v12 全量回归留下唯一未通过项：SubTask 21.22 `runtime_verification` 维度 = 0/100。v12 将此标记为「预存缺口，超出 v12 范围」，但 v13 架构工程师深度审计发现 **此缺口是 v5 引入的完整过程盲区，与 v12 主题同构**——

**第十三层洞察（检测器引用不存在目标是检测器自身缺陷，非「环境预存缺口」）**：v5 spec 阶段 2（变更 R1/R2/R3）声明创建 3 个 in-process 运行时验证测试文件（`test_runtime_replay_heapq.py` / `test_runtime_simulation_heapq.py` / `test_runtime_mode_switch.py`），v5 tasks.md 将 SubTask 4.2/5.1/6.1 全部标记为 `[x]` 已完成。但 **这 3 个文件从未被创建**——v5 checklist.md 的文件存在性检查项（`metatest/test_runtime_replay_heapq.py 文件存在` 等）始终未勾选（`[ ]`），从未执行验证。同时 `test_positive_runtime_verification.py`（v5 SubTask 17.2 声明新建）也不存在。`_collect_runtime_verification` 检测器（runner.py:2032-2049）从 `_StatsPlugin.file_stats` 查找这 3 个文件名，文件不存在时 `fstats` 为 `None`，`continue` 跳过，最终返回 `{"passed": 0, "total": 3, "files": [...]}`——**静默输出 0/3 而非 fail-loud 报警「目标文件缺失」**。这使得 `runtime_verification` 维度自 v5 起连续 8 个迭代（v5→v12）恒为 0/100，但因权重仅 4.8% 且总分仍 ≥95，从未触发评审工程师注意。

**真正的底层运行逻辑洞察**：
1. **检测器引用不存在目标是检测器自身缺陷**：v12 闭合了「检测器用字符串正则匹配 docstring 致假阳性」（M1）与「spec 点名 threading.Timer 但无检测器」（M2），但 `_collect_runtime_verification` 的反模式是 **检测器引用不存在的目标文件却静默输出 0/3 而非 fail-loud**——与 v12「检测器是约束的执行者」主题同构。一个指向不存在目标的检测器等于没有检测器，甚至更糟（制造「已检测」假象）。
2. **过程盲区的同构性**：v5 tasks.md 标记 `[x]` 但文件从未创建，与 v11「删除 DZHPoolExecutor 轮询基础设施但保留 execute_once 致死代码」同构——都是「声称完成但实际未完成」的过程残留。v5 checklist 的文件存在性检查项 `[ ]` 未勾选是直接证据：验证步骤被跳过。
3. **运行时验证是「彻底事件驱动，禁止轮询」的核心执行闭环**：3 个测试文件验证 `fire_due(now)` heapq 调度（replay/simulation）与 `ModeChanged` 事件驱动模式切换——正是用户硬约束「彻底事件驱动，禁止轮询」的 in-process 单元级验证。eventtest 是端到端 subprocess 验证（146s），这 3 个测试是单元级快速验证（<5s），二者互补。缺失这 3 个测试使「禁止轮询」在单元级无验证 harness。

**诚实声明**：v13 不是对 v12 的否定——v12 在 converters/services/core/ 内的收敛是真实的。v13 是对 v12 留下的唯一预存缺口的闭合：v5 过程盲区（3 个测试文件从未创建 + 检测器静默 0/3）+ 检测器 fail-loud 硬化（缺失目标文件应报警而非静默）。v13 闭合后，22 维量化评分体系的所有维度均可达 100%，全局收敛上限声明在 v13 闭合后真正完整。

## What Changes

### 阶段 1：创建 3 个 in-process 运行时验证测试文件（v5 声明但从未创建）

- **变更 R1：新建 `metatest/test_runtime_replay_heapq.py`**。按 v5 spec.md:35 + README.md:137 实现：用 `fastapi_client` fixture 装配 `PoolEngine`，`set_mode("replay")` → `play()` → 断言 `_heap` 含 step `TimedEventSpec`；`fire_due(now+interval)` 触发 `EdgeFired` 事件（heapq 调度，无 `time.sleep`）；`pause()` cancel heapq（`_cancelled` 含 spec id）。禁止 `time.sleep` / `asyncio.sleep` 步进 / 启动服务。
- **变更 R2：新建 `metatest/test_runtime_simulation_heapq.py`**。按 v5 spec.md:36 + README.md:138 实现：`set_mode("simulation")` → `start_auto()` → 断言 `_heap` 含 sim_step `TimedEventSpec`；`fire_due(now+1.0/speed)` 推进 auto-step（`_sim_step_action` 回调执行）；`stop_auto()` cancel heapq。禁止 `asyncio.sleep` 步进。
- **变更 R3：新建 `metatest/test_runtime_mode_switch.py`**。按 v5 spec.md:37 + README.md:139 实现：`set_mode` 切换发布 `ModeChanged` ×2（仿真↔回放 / 回放↔实盘）；切换后 `TickReceived → BarComposed → FormulaEvaluated → StockFiltered` 事件链路完整（用 EventBus 订阅计数断言）；切到实盘后 heapq 不再步进（`fire_due` 无 sim_step spec）。

### 阶段 2：创建正测试包装 + 检测器 fail-loud 硬化

- **变更 P1：新建 `metatest/test_positive_runtime_verification.py`**。按 v5 spec.md:57 + README.md:141 实现：通过 subprocess 运行 3 个 runtime 测试文件，断言退出码 0（全绿）。或直接用 pytest import 机制在主运行中断言 3 文件 passed > 0。
- **变更 D1：`_collect_runtime_verification` fail-loud 硬化**。当前文件不存在时 `fstats=None` → `continue` → 静默 0/3。改为：检测 `_RUNTIME_TEST_FILES` 中任一文件不在 `stats.file_stats` 中时，在返回 dict 新增 `missing_files: List[str]` 字段，并在 `_collect_isomorphism_violations` 新增检查项「runtime_verification 目标文件缺失计 1 违规」（检测器引用不存在目标即检测器缺陷）。

### 阶段 3：ISOMORPHISM_CHECKS_TOTAL 扩展 + RULES 新增 + 全量回归

- **变更 M1：ISOMORPHISM_CHECKS_TOTAL 48 → 49**。新增第 49 项检查：`runtime_verification` 目标文件零缺失（`missing_files` 为空）。
- **变更 L1：RULES 124 新增「检测器引用完整性」纪律**。「检测器引用的目标（文件/类/函数/模式）必须实际存在；目标缺失时检测器必须 fail-loud（返回 `missing_files` / `missing_targets` 字段 + 计违规），禁止静默输出零分制造「已检测」假象。这是「极致本质的运行时」的第十三层洞察：检测器引用不存在目标是检测器自身缺陷——一个指向不存在目标的检测器等于没有检测器，甚至更糟（制造假象）。v5 声明创建 3 个 runtime 测试文件但从未创建，`_collect_runtime_verification` 静默输出 0/3 达 8 个迭代，是此反模式的直接实例。」
- **变更 L2：全量回归**。metatest 总分 ≥ 95 且 22 维均 ≥ 80（含 `runtime_verification` 达 100%），eventtest 退出码 0，3 个 runtime 测试文件全绿，`missing_files` 为空。

## Impact

- Affected specs: converge-meta-essence-v12-residual-blindspot-closure（v12 留下的 21.22 预存缺口在 v13 闭合）；converge-meta-essence-v5-dispatcher-unification（v5 声明创建但从未创建的 3 个测试文件 + 1 个正测试在 v13 真正创建）
- Affected code: `metatest/test_runtime_replay_heapq.py`（新建）、`metatest/test_runtime_simulation_heapq.py`（新建）、`metatest/test_runtime_mode_switch.py`（新建）、`metatest/test_positive_runtime_verification.py`（新建）、`metatest/runner.py`（`_collect_runtime_verification` fail-loud 硬化 + `_collect_isomorphism_violations` 新增第 49 项检查）、`metatest/scoring.py`（`ISOMORPHISM_CHECKS_TOTAL` 48→49）、`RULES.md`（124 新增）

## ADDED Requirements

### Requirement: 运行时验证 harness 文件存在性
The system SHALL provide 3 in-process runtime verification test files (`test_runtime_replay_heapq.py` / `test_runtime_simulation_heapq.py` / `test_runtime_mode_switch.py`) that verify event-driven heapq scheduling without polling (`time.sleep` / `asyncio.sleep` stepping prohibited). All 3 files SHALL pass with exit code 0.

#### Scenario: replay heapq 调度验证
- **WHEN** `test_runtime_replay_heapq.py` runs
- **THEN** `set_mode("replay")` → `play()` puts step TimedEventSpec into `_heap`; `fire_due(now+interval)` triggers `EdgeFired` via heapq; `pause()` cancels heapq; no `time.sleep` used

#### Scenario: simulation heapq 调度验证
- **WHEN** `test_runtime_simulation_heapq.py` runs
- **THEN** `set_mode("simulation")` → `start_auto()` puts sim_step TimedEventSpec into `_heap`; `fire_due(now+1.0/speed)` advances auto-step; `stop_auto()` cancels heapq; no `asyncio.sleep` stepping

#### Scenario: 三模式切换事件链验证
- **WHEN** `test_runtime_mode_switch.py` runs
- **THEN** `set_mode` switch publishes `ModeChanged` ×2; after switch `TickReceived → BarComposed → FormulaEvaluated → StockFiltered` chain complete; after switching to live mode heapq no longer steps

### Requirement: 检测器引用完整性 fail-loud
The system SHALL detect when a detector's referenced targets (files/classes/functions/patterns) do not exist. When targets are missing, the detector MUST fail-loud by returning a `missing_files` / `missing_targets` field and counting a violation, NOT silently returning zero score.

#### Scenario: runtime_verification 目标文件缺失 fail-loud
- **WHEN** `_collect_runtime_verification` finds any file in `_RUNTIME_TEST_FILES` absent from `stats.file_stats`
- **THEN** returns `missing_files: List[str]` non-empty + counts 1 isomorphism violation (detector referencing non-existent target)

#### Scenario: 目标文件齐备后零违规
- **WHEN** all 3 runtime test files exist and pass
- **THEN** `missing_files` is empty + `runtime_verification` dimension = 100% + isomorphism check #49 = 0 violations

## MODIFIED Requirements

### Requirement: 全局元模式收敛上限（v12 → v13 完整性修正）
v12 的「清理自身残留 + 检测器硬化后的全局收敛上限」声明再次修正为：**检测器引用完整性闭合后**的全局收敛上限。v12 的上限在 22 维中 21 维达标（仅 `runtime_verification` 0/100 为预存缺口）；v13 闭合此缺口后，22 维全部可达 100%，全局收敛上限声明才真正完整。metatest 的 `isomorphism_elimination` 维度从 48 项扩展到 49 项。

## REMOVED Requirements

### Requirement: runtime_verification 预存缺口豁免
**Reason**: v12 将 21.22 `runtime_verification = 0/100` 标记为「预存缺口，超出 v12 范围」，留待后续迭代闭合。此豁免使 v12 的「全局收敛上限」声明不完整（22 维中 1 维为 0）。
**Migration**: v13 创建 3 个缺失测试文件 + 检测器 fail-loud 硬化后，`runtime_verification` 达 100%，豁免移除。
