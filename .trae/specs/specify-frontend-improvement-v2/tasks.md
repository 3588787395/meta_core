# Tasks

> **前端完善新执行规范 V2**：本 tasks.md 针对 `specify-frontend-improvement` 第一版未闭环项重建，采用双工程师协作 + 98 分门槛。

## 执行机制（双工程师协作 + 98 分门槛）

1. **架构工程师**：负责前端代码实现，严格遵循 spec.md，禁止另开炉灶。
2. **评审工程师**：负责阅读代码、运行 Playwright / 手动验证、按 checklist 逐项打分。
3. **门槛**：每任务 ≥ 98 分进入下一任务；< 98 打回重做。
4. 调度方同步勾选 tasks.md 与 checklist.md。

---

## 实现任务（架构工程师 → 评审工程师）

- [ ] Task 1: 转移条件节点可视化
  - [ ] SubTask 1.1: 在 `web/js/canvas.js` 完善 `transfer_condition` 节点渲染（三角形，指向右侧）
  - [ ] SubTask 1.2: 在 `web/js/canvas.js` 完善 `condition` 节点渲染（紫色矩形）
  - [ ] SubTask 1.3: 确保节点选中、拖拽、缩放、适应画布与转移条件节点兼容
  - [ ] SubTask 1.4: 在 `web/css/styles.css` 调整样式，确保转移条件节点视觉清晰
  - [ ] 评审：评审工程师通过浏览器检查 + Playwright 验证，打分 ≥ 98 进入 Task 2

- [ ] Task 2: 边条件摘要与属性面板（依赖 Task 1）
  - [ ] SubTask 2.1: 在 `web/js/ui.js` 的 TableDrivenPanel 中，选中边时读取 `edge.params.formula_ref`、`operator`、`threshold`、`nset`、`noperate`
  - [ ] SubTask 2.2: 读取 `edge.params` 中 K 线配置 `period`、`length`、`bar_type`
  - [ ] SubTask 2.3: 边标签渲染触发频率 + 条件摘要（如 `60s / 5m KDJ 金叉`），由配置表生成
  - [ ] SubTask 2.4: 综合设置窗口三列布局展示源→条件→目标流程
  - [ ] SubTask 2.5: 禁止硬编码实例内容到程序
  - [ ] 评审：评审工程师验证属性面板和边标签，打分 ≥ 98 进入 Task 3

- [ ] Task 3: 事件面板完善（依赖 Task 1）
  - [ ] SubTask 3.1: 分类矩阵与散点分布使用同一时间轴语义，矩阵每行显示图标/计数/事件流
  - [ ] SubTask 3.2: 散点图包含统计数量并支持点击交互
  - [ ] SubTask 3.3: 定时器队列绘制 fire_at 时间分布图，显示所有排队事件和当前时间线
  - [ ] SubTask 3.4: 点击分类行/图标在详情区显示事件文本记录
  - [ ] SubTask 3.5: 事件面板状态（位置、高度、折叠、关闭）保存到 localStorage
  - [ ] 评审：评审工程师通过 Playwright 验证事件面板，打分 ≥ 98 进入 Task 4

- [ ] Task 4: 仿真模式与模式切换（依赖 Task 2、Task 3）
  - [ ] SubTask 4.1: 修复 `currentMode` 作用域问题，确保仿真运行按钮和自动步进可用
  - [ ] SubTask 4.2: 四种模式切换正确，指示器颜色和标签正确
  - [ ] SubTask 4.3: 仿真面板虚拟时钟、步数、启动/暂停/步进/重置、步长、速度调节正确
  - [ ] SubTask 4.4: 仿真运行 ≥300 秒虚拟时间后事件面板显示完整事件链
  - [ ] SubTask 4.5: 股票正确流转：备选池→A池/B池→C池（A∩B 交集）
  - [ ] 评审：评审工程师运行仿真并验证，打分 ≥ 98 进入 Task 5

- [ ] Task 5: 公式管理、配置中心、导入导出、响应式布局（依赖 Task 4）
  - [ ] SubTask 5.1: 公式管理列表/编辑器/测试功能完整
  - [ ] SubTask 5.2: 配置中心浏览/编辑/校验/热加载功能完整
  - [ ] SubTask 5.3: DZH/TDX/JSON 导入导出功能完整
  - [ ] SubTask 5.4: 股票池列表加载/删除/重命名功能完整
  - [ ] SubTask 5.5: 桌面/平板/移动端响应式布局正确
  - [ ] 评审：评审工程师逐项验证，打分 ≥ 98 进入 Task 6

- [ ] Task 6: Playwright 自动化验证与文档更新（依赖 Task 5）
  - [ ] SubTask 6.1: 编写/更新 Playwright 脚本，覆盖模式切换、仿真运行、事件接收、转移条件节点、导入导出
  - [ ] SubTask 6.2: 确保 Playwright 脚本全部通过
  - [ ] SubTask 6.3: 更新 `docs/DESIGN.md` 前端验证章节（流程、结果、bug 清单、验证方法）
  - [ ] SubTask 6.4: 更新 `docs/DESIGN0.md` 前端验证相关条目
  - [ ] 评审：评审工程师运行 Playwright 并审阅文档，打分 ≥ 98 结项

# Task Dependencies

- [Task 2] depends on [Task 1]
- [Task 3] depends on [Task 1]
- [Task 4] depends on [Task 2, Task 3]
- [Task 5] depends on [Task 4]
- [Task 6] depends on [Task 5]
