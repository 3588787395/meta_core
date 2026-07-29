# 股票池事件流程新执行规范 V2

## Why

既有 `specify-stockpool-event-flow` 规范已完成核心架构改造（G1-G6），但 Task 14-15 尚未闭环，且旧测试覆盖不足、断言偏弱，无法以量化结果证明事件链正确性。本规范作为**新执行规范**，要求重建 `eventtest` 目录下的严格正/反/合测试，以真实测试结果驱动代码修复；同时股票池可视化必须包含**转移条件节点**，其筛选条件从**计算参数**和**K 线配置**读取，禁止硬编码。

> **本规范采用双工程师协作（架构工程师 + 评审工程师）、98 分门槛、逐项执行、量化验证。**

## What Changes

- **重建 eventtest 目录**：删除旧测试，按正例/反例/合例三维度重写，总用例数 ≥ 170，exit code 0。
- **量化评审基线**：11 类事件计数 + 池状态快照 + A∩B 严格断言（`pool_C = pool_A ∩ pool_B`，不弱化）。
- **修复生产 bug 正向断言**：发现 bug 必须修复生产代码 + 增加正向断言验证 spec 被满足；禁止用 workaround 掩盖 bug，禁止测试反向断言。
- **股票池可视化包含转移条件节点**：前端画布正确渲染 transfer_condition / condition 节点，边上显示从计算参数和 K 线配置读取的筛选条件摘要。
- **更新设计文档**：`docs/DESIGN0.md` 与 `docs/DESIGN.md` 同步增补事件流程、验证合同、eventtest 量化基线、已修复 bug 清单。
- **禁止兼容旧接口**：继续清理 `at_fn` / `fire_ttl_due` / `TtlTracker` / `SimTickSource` / `execution_order` / 扁平 `get_node_stocks` 等残留。

## Impact

- `eventtest/` 目录全部测试用例重写。
- `core/engine.py`、`core/execution_module.py`、`core/domain.py`、`core/tick_bar_module.py`、`core/event_bus.py`、`core/runtime_mode_module.py` 按需修复。
- `web/js/canvas.js`、`web/js/ui.js`、`web/js/event-panel.js`、`web/js/app.js` 前端可视化完善。
- `docs/DESIGN0.md`、`docs/DESIGN.md` 文档更新。

---

## Requirements

### R1: eventtest 目录重建

系统 SHALL 在 `eventtest/` 目录下建立严格正/反/合测试，覆盖事件驱动核心、状态池视图、公式筛选、交易链路、仿真全流。

#### Scenario: 正例测试
- **WHEN** 配置合法、输入有效
- **THEN** 系统行为符合 spec（如单一定时器、EdgeFired 无 changed_codes、StatePoolView 视图接口、MockDataSource 注册 tick 到统一队列、A∩B 严格交集）

#### Scenario: 反例测试
- **WHEN** 配置非法、输入越界或违反约束
- **THEN** 系统明确失败或拒绝（如旧接口不存在、TTL 未持仓时不应卖出、重复转移被拒绝、空池无异常）

#### Scenario: 合例测试
- **WHEN** 仿真运行虚拟时间推进
- **THEN** 11 类事件按预期产生，池状态快照量化验证完整事件链（Tick→Bar→Formula→Edge→Transfer→Signal→Order→Position→TTL）

### R2: 量化评审基线

系统 SHALL 提供可量化的测试结果作为评审依据：

- 11 类事件计数：Tick、Bar、Formula、Edge、Transfer、Signal、Order、TTL、System、Trade、Error。
- 池状态快照：source/pool_A/pool_B/pool_C 在关键虚拟时刻的股票集合。
- 关键断言：`pool_C = pool_A ∩ pool_B`，不得以 `pool_C ⊆ source` 等弱断言替代。
- 性能基线：`/api/sim/start` < 1s，首次 `/api/sim/control step` < 500ms（不阻塞 Uvicorn）。

### R3: 股票池可视化包含转移条件节点

前端股票池设计器 SHALL：

- 渲染 `transfer_condition`（三角形）和 `condition`（紫色矩形）转移条件节点。
- 选中边时，属性面板从 `edge.params` 读取计算参数（公式引用、阈值、操作符）和 K 线配置（周期、长度）。
- 边标签显示触发频率 + 条件摘要（如 `60s / 5m KDJ 金叉`）。
- 条件摘要由配置表实时生成，禁止硬编码实例内容。

### R4: 双工程师协作与 98 分门槛

- 架构工程师负责代码实现；评审工程师负责阅读代码、运行测试、按 checklist 逐项打分。
- 每任务满分 100，≥ 98 进入下一任务；< 98 打回重做，携带扣分点。
- 调度方在 `tasks.md` 与 `checklist.md` 同步勾选进度。

### R5: 禁止兼容旧接口

- 删除或确认已删除：`EdgeFired.changed_codes`、`at_fn` 延迟求值、`fire_ttl_due`、`TtlTracker` 独立 heapq、`SimTickSource`、`execution_order`（运行时拓扑排序）、`get_node_stocks`/`set_node_stocks` 扁平接口。
- AST 静态分析覆盖 `FunctionDef`/`ClassDef` body，禁止函数级懒加载绕过模块零引用约束。

### R6: 设计文档更新

- `DESIGN0.md` 以架构合同风格增补：验证合同、验证范围、eventtest 量化基线、已修复 bug 清单、与设计原则一致性（关联 §7/§9/§13/§16）。
- `DESIGN.md` 增补：前端验证流程、Playwright 验证结果、eventtest 量化结果、已修复 bug 清单、验证方法说明。

---

## MODIFIED Requirements

### Requirement: 仿真模式

仿真模式除 tick 生成逻辑外，其他处理流程必须使用相同代码，禁止分别处理；所有股票代码用 `fz` 替代原市场代码；tick 间隔 1-9 秒随机，同股票固定，不同股票不同。

## REMOVED Requirements

### Requirement: 旧测试文件

**Reason**: 旧测试泛无意义，无法量化验证事件链正确性。
**Migration**: 删除 `eventtest/` 下旧用例，按本规范重建正/反/合测试。
