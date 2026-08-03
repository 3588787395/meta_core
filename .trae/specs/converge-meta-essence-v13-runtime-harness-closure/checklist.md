# Checklist

## 架构工程师检查点（实施前自检）

- [ ] 已阅读 spec.md「Why」章节并理解：v13 是「v12 留下的唯一预存缺口闭合」——v5 声明创建 3 个 in-process 运行时验证测试文件但从未创建，`_collect_runtime_verification` 静默输出 0/3 达 8 个迭代（v5→v12）
- [ ] 已理解本次迭代核心是「检测器引用完整性 fail-loud + 运行时验证 harness 真正创建」——v13 闭合后 22 维全部可达 100%
- [ ] 已阅读 `metatest/runner.py:1953-1957` 确认 `_RUNTIME_TEST_FILES` 列表（3 个文件名）
- [ ] 已阅读 `metatest/runner.py:2032-2049` 确认 `_collect_runtime_verification` 当前实现（`fstats=None` → `continue` → 静默 0/3）
- [ ] 已阅读 `metatest/README.md:130-141` 确认 3 个测试文件的验证点规范
- [ ] 已阅读 `.trae/specs/converge-meta-essence-v5-dispatcher-unification/spec.md:35-37` 确认 v5 变更 R1/R2/R3 的原始要求
- [ ] 已阅读 `.trae/specs/converge-meta-essence-v5-dispatcher-unification/tasks.md:42/49/56` 确认 v5 SubTask 4.2/5.1/6.1 标记 [x] 但文件从未创建
- [ ] 已阅读 `.trae/specs/converge-meta-essence-v5-dispatcher-unification/checklist.md:45/52/59` 确认 v5 文件存在性检查项 [ ] 未勾选（过程盲区直接证据）
- [ ] 已 Glob `**/test_runtime_*.py` 全仓确认零匹配（3 文件确实从未创建）
- [ ] 已 Glob `**/test_positive_runtime*.py` 全仓确认零匹配（正测试也不存在）
- [ ] 已阅读 `core/execution_module.py:202` `fire_due` 确认 heapq 弹出 + 事件发布机制
- [ ] 已阅读 `core/runtime_mode_module.py:1961` `start_auto` / `2180` `switch_mode` 确认 API 签名
- [ ] 已阅读 `core/event_bus.py:309` `ModeChanged` 类定义确认断言目标

## 评审工程师检查点（阶段 1：3 个 in-process 运行时验证测试文件创建）

### 变更 R1 — 新建 `metatest/test_runtime_replay_heapq.py`
- [ ] `metatest/test_runtime_replay_heapq.py` 文件存在
- [ ] 测试覆盖：`set_mode("replay")` → `play()` → `_heap` 含 step TimedEventSpec
- [ ] 测试覆盖：`fire_due(now+interval)` 触发 `EdgeFired`（heapq 调度）
- [ ] 测试覆盖：`pause()` cancel heapq（`_cancelled` 含 spec id）
- [ ] Grep `time\.sleep|asyncio\.sleep` 在该文件零匹配（除 `# noqa: event-driver`）
- [ ] `python -m pytest metatest/test_runtime_replay_heapq.py -v` 退出码 0

### 变更 R2 — 新建 `metatest/test_runtime_simulation_heapq.py`
- [ ] `metatest/test_runtime_simulation_heapq.py` 文件存在
- [ ] 测试覆盖：`set_mode("simulation")` → `start_auto()` → `_heap` 含 sim_step TimedEventSpec
- [ ] 测试覆盖：`fire_due(now+1.0/speed)` 推进 auto-step
- [ ] 测试覆盖：`stop_auto()` cancel heapq
- [ ] Grep `asyncio\.sleep` 在该文件零匹配（步进禁用）
- [ ] `python -m pytest metatest/test_runtime_simulation_heapq.py -v` 退出码 0

### 变更 R3 — 新建 `metatest/test_runtime_mode_switch.py`
- [ ] `metatest/test_runtime_mode_switch.py` 文件存在
- [ ] 测试覆盖：`set_mode` 切换发布 `ModeChanged` ×2
- [ ] 测试覆盖：切换后 `TickReceived → BarComposed → FormulaEvaluated → StockFiltered` 链路完整
- [ ] 测试覆盖：切到实盘后 heapq 不再步进
- [ ] `python -m pytest metatest/test_runtime_mode_switch.py -v` 退出码 0

### 阶段 1 整体验证
- [ ] 3 文件全绿：`python -m pytest metatest/test_runtime_replay_heapq.py metatest/test_runtime_simulation_heapq.py metatest/test_runtime_mode_switch.py -v` 退出码 0
- [ ] Grep `time\.sleep|asyncio\.sleep` 在 3 个新文件零匹配（除 `# noqa: event-driver`）
- [ ] 检测器能采集：`_collect_runtime_verification` with 3 文件 passed → `passed==3`

## 评审工程师检查点（阶段 2：正测试包装 + 检测器 fail-loud 硬化）

### 变更 P1 — 新建 `metatest/test_positive_runtime_verification.py`
- [ ] `metatest/test_positive_runtime_verification.py` 文件存在
- [ ] 断言 3 个 runtime 测试文件 `passed > 0` 且 `failed == 0` 且 `errors == 0`
- [ ] `python -m pytest metatest/test_positive_runtime_verification.py -v` 退出码 0

### 变更 D1 — `_collect_runtime_verification` fail-loud 硬化
- [ ] `_collect_runtime_verification` 返回 dict 新增 `missing_files: List[str]` 字段
- [ ] `fstats is None` 时文件名加入 `missing_files`（而非 `continue` 跳过）
- [ ] 空 stats 时 `missing_files` 含 3 个文件名（fail-loud）
- [ ] 齐备 stats 时 `missing_files == []` 且 `passed == 3`
- [ ] `_collect_isomorphism_violations` 新增第 49 项检查：`missing_files` 非空计 1 违规
- [ ] test_results 新增 `runtime_verification_missing_files` 字段
- [ ] `python -c "from metatest.runner import _collect_runtime_verification, _StatsPlugin; s=_StatsPlugin(); r=_collect_runtime_verification(s); print(r); assert len(r['missing_files'])==3"` 验证 fail-loud
- [ ] `python -c "from metatest.runner import _collect_runtime_verification, _StatsPlugin; s=_StatsPlugin(); s.file_stats={...3文件...}; r=_collect_runtime_verification(s); print(r); assert r['missing_files']==[] and r['passed']==3"` 验证齐备零缺失

## 评审工程师检查点（阶段 3：ISOMORPHISM_CHECKS_TOTAL 扩展 + RULES 新增 + 全量回归）

### 变更 M1 — ISOMORPHISM_CHECKS_TOTAL 48 → 49
- [ ] `ISOMORPHISM_CHECKS_TOTAL = 49`（从 48 扩展）
- [ ] 新增第 49 项检查：runtime_verification 目标文件零缺失
- [ ] `python -c "from metatest.scoring import ISOMORPHISM_CHECKS_TOTAL; print(ISOMORPHISM_CHECKS_TOTAL); assert ISOMORPHISM_CHECKS_TOTAL == 49"` 输出 49

### 变更 L1 — RULES 124 新增
- [ ] 新增第 124 条「检测器引用完整性」纪律
- [ ] Grep `^124\.` 在 RULES.md = 1

### 全量回归
- [ ] `python -m pytest metatest/ -x -q --ignore=metatest/test_positive_event_panel.py` 全量测试通过（v12 的 4 个 pre-existing 失败仍允许）
- [ ] 4 个新测试文件全绿：`python -m pytest metatest/test_runtime_replay_heapq.py metatest/test_runtime_simulation_heapq.py metatest/test_runtime_mode_switch.py metatest/test_positive_runtime_verification.py -v` 退出码 0
- [ ] `python -m eventtest.run_eventtest` 退出码 0（全绿）
- [ ] runtime_verification 维度 = 100%（v5→v12 的 0/100 缺口闭合）
- [ ] isomorphism_elimination 维度 = 100（49 项 0 违规，含新增第 49 项）
- [ ] Grep `time\.sleep|asyncio\.sleep` 在 3 个 runtime 测试文件零匹配（除 `# noqa: event-driver`）
- [ ] Grep `^124\.` 在 RULES.md = 1
- [ ] `python -c "from metatest.scoring import ISOMORPHISM_CHECKS_TOTAL; print(ISOMORPHISM_CHECKS_TOTAL); assert ISOMORPHISM_CHECKS_TOTAL == 49"` 输出 49
- [ ] oop_inheritance_depth 维度 = 100（v12 成果不回归）
- [ ] adapter_isomorphism 维度 = 100（v12 成果不回归）
- [ ] dispatcher_isomorphism 维度 = 100（v12 成果不回归）
- [ ] handler_exception_coverage 维度 = 100（v12 成果不回归）
- [ ] essence_ratio 维度不回归（v12 成果保持）
- [ ] eventtest_regression 维度 = 100（v12 成果不回归）
- [ ] DZH↔TDX roundtrip 保真（不回归）

## 第十三层洞察根因检查点（评审工程师最终验收）

- [ ] **检测器引用不存在目标是检测器自身缺陷**：v5 声明创建 3 个 runtime 测试文件但从未创建，`_collect_runtime_verification` 静默输出 0/3 达 8 个迭代。v13 真正创建 3 文件 + 检测器 fail-loud 硬化（`missing_files` 字段 + 第 49 项检查），使检测器引用完整性成为可执行约束
- [ ] **过程盲区的同构性**：v5 tasks.md 标记 [x] 但文件从未创建，与 v11「保留 execute_once 致死代码」同构——都是「声称完成但实际未完成」的过程残留。v5 checklist 文件存在性检查 [ ] 未勾选是直接证据。v13 真正执行文件存在性验证 + 全量回归
- [ ] **运行时验证是「彻底事件驱动，禁止轮询」的核心执行闭环**：3 个测试文件验证 `fire_due(now)` heapq 调度 + `ModeChanged` 事件驱动模式切换——用户硬约束的 in-process 单元级验证。v13 闭合后，「禁止轮询」在单元级有验证 harness
- [ ] **检测器 fail-loud 优于静默零分**：静默 0/3 制造「已检测」假象（8 个迭代未触发注意），fail-loud `missing_files` 使缺失立即暴露为违规。这是 v12「检测器是约束的执行者」第十二层洞察的延伸：检测器不仅要检测违规，还要检测自身引用完整性
- [ ] **非拆分非重写**：3 个测试文件是 v5 spec 已定义的实现（非新设计），检测器硬化是在现有 `_collect_runtime_verification` 上新增 `missing_files` 字段（非新建检测器）
- [ ] **量化评审驱动**：isomorphism_elimination 维度新增第 49 项检查（runtime_verification 目标文件零缺失），48→49 项，使评分体系能驱动检测器引用完整性
- [ ] **诚实声明确认**：v13 不是对 v12 的否定——v12 在 converters/services/core/ 内的收敛是真实的。v13 是对 v12 留下的唯一预存缺口的闭合：v5 过程盲区 + 检测器静默 0/3 + 检测器 fail-loud 硬化。v13 闭合后，22 维全部可达 100%，全局收敛上限声明真正完整
