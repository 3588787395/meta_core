# Tasks

> **新规范计划**：本 tasks.md 为前端股票池界面的逐项评审与完善计划，基于 `specify-frontend-improvement` 已实现的 18 个任务进行严格评审，并修复发现的问题。

## 执行机制（双工程师协作 + 98 分门槛）

本规格采用双工程师协作流程，由调度方（主 Agent）编排：

1. **架构工程师**（Architect Engineer，sub-agent）：负责修复评审中发现的前端问题，严格遵循 spec.md，禁止兼容旧接口，禁止另开炉灶。
2. **评审工程师**（Review Engineer，sub-agent）：负责逐项验证前端功能、运行 Playwright 浏览器测试、按 checklist.md 逐项打分，给出 0-100 分及扣分理由。
3. **门槛**：分数 ≥ 98 方可进入下一任务；分数 < 98 打回架构工程师重做（携带扣分点），直到 ≥ 98。
4. **任务依赖**：严格按"Task Dependencies"执行，前置任务未达 98 分不得开启后续任务。
5. 每个任务完成后，调度方在 tasks.md 勾选 `[x]`，并在 checklist.md 勾选通过项。

---

## 评审任务（评审工程师 → 架构工程师修复 → 评审工程师复验）

- [ ] Task 1: 画布引擎逐项评审
  - [ ] SubTask 1.1: 验证 `FlowCanvas` 核心方法（render/fitToContent/setEdgeLineType/zoomIn/selectNode 等）正确工作
  - [ ] SubTask 1.2: 验证节点渲染、拖拽、缩放、框选、迷你地图、边渲染符合表驱动配置
  - [ ] SubTask 1.3: 验证运行模式下编辑功能正确禁用
  - [ ] SubTask 1.4: 验证三种线形（贝兹曲线/横竖折线/直线）正确渲染
  - [ ] 评审：评审工程师打分，≥98 方可进入 Task 2

- [ ] Task 2: 节点类型与条件节点逐项评审
  - [ ] SubTask 2.1: 验证所有节点类型按 `cell_type_registry` 配置正确渲染（DZH 200/201/202/4、TDX 7/8/3、condition 紫色矩形等）
  - [ ] SubTask 2.2: 验证转移条件节点（cond1/cond2/cond3）显示为紫色圆角矩形（#8e44ad）
  - [ ] SubTask 2.3: 验证筛选条件从边的计算参数和 K 线配置中读取
  - [ ] SubTask 2.4: 验证条件节点配置面板可打开并显示 func/indi/indiparam/filter_spec
  - [ ] 评审：评审工程师打分，≥98 方可进入 Task 3

- [ ] Task 3: 属性面板与表驱动 UI 逐项评审
  - [ ] SubTask 3.1: 验证 `TableDrivenPanel` 根据选中节点/边动态生成表单
  - [ ] SubTask 3.2: 验证字段联动（depends_on/active_when）正常工作
  - [ ] SubTask 3.3: 验证实时校验并显示错误信息
  - [ ] SubTask 3.4: 验证位标志自动编解码（DataBinder.decodeAttrFlags/encodeAttrFlags）
  - [ ] SubTask 3.5: 验证 DZH 颜色值可视化显示
  - [ ] 评审：评审工程师打分，≥98 方可进入 Task 4

- [ ] Task 4: 四种模式切换逐项评审
  - [ ] SubTask 4.1: Playwright 验证设计模式界面正确加载
  - [ ] SubTask 4.2: 验证实盘模式切换路径正确，后端 `DataSourceContract` 探测逻辑正确
  - [ ] SubTask 4.3: 验证回放模式面板正确显示，`IStorageQuery` 注入 `kline_cache`
  - [ ] SubTask 4.4: Playwright 验证仿真模式切换 active，仿真面板正确显示
  - [ ] SubTask 4.5: 验证运行中切换模式被阻止并提示
  - [ ] SubTask 4.6: 验证模式切换不导致界面混乱
  - [ ] 评审：评审工程师打分，≥98 方可进入 Task 5

- [ ] Task 5: 仿真模式股票流转逐项评审（依赖 Task 4）
  - [ ] SubTask 5.1: Playwright 验证仿真模式启动后虚拟时钟和步数正确运行
  - [ ] SubTask 5.2: 验证备选池加载 100 只 `fz` 前缀股票
  - [ ] SubTask 5.3: 验证股票经 cond1 进入 pool_A、经 cond2 进入 pool_B
  - [ ] SubTask 5.4: 验证 A∩B 经 cond3 进入 pool_C 并触发买入信号
  - [ ] SubTask 5.5: eventtest 量化验证完整事件链（11 类事件计数 + 池状态快照）
  - [ ] 评审：评审工程师打分，≥98 方可进入 Task 6

- [ ] Task 6: 事件面板逐项评审（依赖 Task 5）
  - [ ] SubTask 6.1: 验证事件面板按 9 类分类显示（Tick/Bar/Formula/Edge/Transfer/Signal/Order/TTL/System）
  - [ ] SubTask 6.2: 验证已发生事件与排队中事件区分显示
  - [ ] SubTask 6.3: 验证定时器队列显示预计触发时间
  - [ ] SubTask 6.4: 验证事件用图标和颜色展示
  - [ ] SubTask 6.5: 验证点击分类行/单个事件图标在详情区显示事件文本
  - [ ] 评审：评审工程师打分，≥98 方可进入 Task 7

- [ ] Task 7: 事件驱动与表驱动架构一致性评审
  - [ ] SubTask 7.1: 验证 UI 组件类型、属性字段、校验规则由后端配置表动态决定
  - [ ] SubTask 7.2: 验证前端通过 WebSocket 接收后端事件并更新界面
  - [ ] SubTask 7.3: 验证前端不直接调用后端业务模块，仅通过 API 与事件总线交互
  - [ ] SubTask 7.4: 验证核心对象（FlowCanvas/PoolDataManager/TableDrivenPanel/EventPanel）的事件订阅/发布正确
  - [ ] 评审：评审工程师打分，≥98 方可进入 Task 8

- [ ] Task 8: 方法/属性/事件正确性逐项评审
  - [ ] SubTask 8.1: 列出 `FlowCanvas`、`PoolDataManager`、`TableDrivenPanel`、`EventPanel` 等核心对象的公开方法/属性/事件清单
  - [ ] SubTask 8.2: 逐项验证每个公开方法的输入输出正确
  - [ ] SubTask 8.3: 逐项验证关键属性保持单一真相源，无冗余状态
  - [ ] SubTask 8.4: 逐项验证事件回调正确订阅、触发和取消订阅
  - [ ] SubTask 8.5: 删除未使用的冗余方法/属性/事件（清理 junk code）
  - [ ] 评审：评审工程师打分，≥98 方可进入 Task 9

- [ ] Task 9: 禁止兼容旧接口检查（依赖 Task 1-8）
  - [ ] SubTask 9.1: 全局搜索确认前端代码无 `get_node_stocks` / `set_node_stocks` 引用
  - [ ] SubTask 9.2: 全局搜索确认前端代码无 `SimTickSource` 引用
  - [ ] SubTask 9.3: 全局搜索确认前端代码无 `execution_order`（运行时拓扑排序）引用
  - [ ] SubTask 9.4: 全局搜索确认前端代码无 `EdgeFired.changed_codes` 引用
  - [ ] SubTask 9.5: 验证前端 API 调用使用 `StatePoolView` 视图接口
  - [ ] 评审：评审工程师打分，≥98 方可进入 Task 10

- [ ] Task 10: 问题修复与无回归验证（依赖 Task 1-9）
  - [ ] SubTask 10.1: 架构工程师修复 Task 1-9 评审中发现的所有前端问题
  - [ ] SubTask 10.2: 运行 eventtest 173 个正反合测试，确认退出码 0
  - [ ] SubTask 10.3: Playwright 验证所有核心场景通过
  - [ ] SubTask 10.4: 确认无回归（事件计数、池状态快照与基线一致）
  - [ ] 评审：评审工程师打分，≥98 方可进入 Task 11

- [ ] Task 11: 更新 DESIGN.md 和 DESIGN0.md（依赖 Task 10）
  - [ ] SubTask 11.1: DESIGN.md 前端验证章节补充逐项评审结果、修复清单、量化数据
  - [ ] SubTask 11.2: DESIGN0.md 前端验证章节同步更新，确认表驱动/事件驱动一致性
  - [ ] 评审：评审工程师打分，≥98 方可结项

# Task Dependencies
- [Task 2] depends on [Task 1]
- [Task 3] depends on [Task 2]
- [Task 4] depends on [Task 1]
- [Task 5] depends on [Task 4]
- [Task 6] depends on [Task 5]
- [Task 7] depends on [Task 1-6]
- [Task 8] depends on [Task 1-7]
- [Task 9] depends on [Task 1-8]
- [Task 10] depends on [Task 1-9]
- [Task 11] depends on [Task 10]
