# Tasks

> **新执行规范 V2**：本 tasks.md 为股票池事件流程改造的第二轮执行计划，目标是重建严格测试、闭环可视化、更新设计文档。与旧测试不兼容，禁止保留旧接口兼容层。

## 执行机制（双工程师协作 + 98 分门槛）

1. **架构工程师**（Architect Engineer，sub-agent）：负责代码实现，严格遵循 spec.md / 硬约束，禁止兼容旧接口，禁止另开炉灶。
2. **评审工程师**（Review Engineer，sub-agent）：负责阅读代码、运行测试、按 checklist.md 逐项打分，给出 0-100 分及扣分理由。
3. **门槛**：分数 ≥ 98 方可进入下一任务；分数 < 98 打回架构工程师重做（携带扣分点），直到 ≥ 98。
4. **任务依赖**：严格按"Task Dependencies"执行，前置任务未达 98 分不得开启后续任务。
5. 每个任务完成后，调度方在 tasks.md 勾选 `[x]`，并在 checklist.md 勾选通过项。

---

## 实现任务（架构工程师 → 评审工程师）

- [ ] Task 1: 清理旧测试与重建 eventtest 目录结构
  - [ ] SubTask 1.1: 删除 `eventtest/` 下旧测试文件（保留 `conftest.py` / `run_eventtest.py` / `README.md` 骨架）
  - [ ] SubTask 1.2: 新建 `eventtest/test_positive_*.py`（≥ 60 个用例）：EventDriver 优先队列、StatePoolView、EdgeFired 无 changed_codes、MockDataSource、公式筛选分离、交易链、TTL、A∩B 严格交集
  - [ ] SubTask 1.3: 新建 `eventtest/test_negative_*.py`（≥ 80 个用例）：旧接口不存在、非法配置、空池、重复转移、公式错误、TTL 无持仓、模块非法引用
  - [ ] SubTask 1.4: 新建 `eventtest/test_integration_*.py`（≥ 33 个用例）：仿真全流、池状态快照、11 类事件计数、事件链顺序
  - [ ] SubTask 1.5: 更新 `eventtest/run_eventtest.py`，统一入口，输出通过数/总数/exit code
  - [ ] 评审：评审工程师运行 `python -m eventtest.run_eventtest`，打分 ≥ 98 进入 Task 2

- [ ] Task 2: 修复 eventtest 暴露的生产 bug（依赖 Task 1）
  - [ ] SubTask 2.1: 修复 eventtest 失败的正向断言对应的生产代码问题
  - [ ] SubTask 2.2: 禁止用 workaround 掩盖 bug；每个 bug 必须加正向断言验证 spec 被满足
  - [ ] SubTask 2.3: 确认 `pool_C = pool_A ∩ pool_B` 严格断言成立
  - [ ] SubTask 2.4: 确认模块零引用约束通过 AST 分析
  - [ ] 评审：评审工程师重新运行全部测试，打分 ≥ 98 进入 Task 3

- [ ] Task 3: 股票池可视化——转移条件节点与边条件摘要（依赖 Task 2）
  - [ ] SubTask 3.1: 在 `web/js/canvas.js` 确保 `transfer_condition` / `condition` 节点正确渲染
  - [ ] SubTask 3.2: 在 `web/js/ui.js` 属性面板读取 `edge.params` 中计算参数（formula_ref、operator、threshold）和 K 线配置（period、length）
  - [ ] SubTask 3.3: 边标签显示触发频率 + 条件摘要，禁止硬编码
  - [ ] SubTask 3.4: 综合设置窗口三列布局展示源→条件→目标流程
  - [ ] 评审：评审工程师通过 Playwright 验证，打分 ≥ 98 进入 Task 4

- [ ] Task 4: 事件面板与仿真运行验证（依赖 Task 3）
  - [ ] SubTask 4.1: 事件面板分类矩阵/散点分布/定时器队列正常工作
  - [ ] SubTask 4.2: 仿真模式加载 100 只 fz 股票，虚拟时钟推进，A/B/C 池流转正确
  - [ ] SubTask 4.3: `/api/sim/start` < 1s，首次 `/api/sim/control step` < 500ms（不阻塞 Uvicorn）
  - [ ] SubTask 4.4: Playwright 验证事件产生、图标颜色、点击详情
  - [ ] 评审：评审工程师运行 Playwright + eventtest，打分 ≥ 98 进入 Task 5

- [ ] Task 5: 更新设计文档（依赖 Task 4）
  - [ ] SubTask 5.1: `docs/DESIGN0.md` 增补验证合同、验证范围、eventtest 量化基线、已修复 bug 清单、与设计原则一致性（§7/§9/§13/§16）
  - [ ] SubTask 5.2: `docs/DESIGN.md` 增补前端验证流程、Playwright 结果、eventtest 量化结果、已修复 bug 清单、验证方法说明
  - [ ] SubTask 5.3: 确保两份文档架构合同风格一致
  - [ ] 评审：评审工程师审阅文档，打分 ≥ 98 结项

# Task Dependencies

- [Task 2] depends on [Task 1]
- [Task 3] depends on [Task 2]
- [Task 4] depends on [Task 3]
- [Task 5] depends on [Task 4]
