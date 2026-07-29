# Checklist

## 评审打分规则（评审工程师使用）

- 每项检查点通过得满分，部分通过按比例扣分，未通过扣全部分。
- 任务总分 = 各检查点得分之和 / 检查点满分之和 × 100。
- **门槛：≥ 98 分方可进入下一任务；< 98 分打回架构工程师重做。**
- 评审工程师须给出：①总分 ②每项扣分理由 ③重做清单（若 < 98）。

---

## Task 1: 清理旧测试与重建 eventtest

- [ ] `eventtest/` 旧测试已删除或归档，新目录结构清晰
- [ ] 正例测试 ≥ 60 个，覆盖 EventDriver、StatePoolView、EdgeFired、MockDataSource、公式筛选、交易链、TTL
- [ ] 反例测试 ≥ 80 个，覆盖旧接口不存在、非法配置、空池、重复转移、公式错误、TTL 无持仓、模块非法引用
- [ ] 合例测试 ≥ 33 个，覆盖仿真全流、池状态快照、11 类事件计数、事件链顺序
- [ ] `python -m eventtest.run_eventtest` 全部通过，exit code 0
- [ ] 测试断言为正向 spec 满足，而非反向 bug 存在

## Task 2: 修复 eventtest 暴露的生产 bug

- [ ] 每个失败用例对应的生产 bug 已修复
- [ ] 未使用 workaround 掩盖 bug
- [ ] `pool_C = pool_A ∩ pool_B` 严格断言成立，未弱化为 `pool_C ⊆ source`
- [ ] AST 分析确认无函数级懒加载绕过模块零引用约束
- [ ] 旧接口已清理：`EdgeFired.changed_codes`、`at_fn`、`fire_ttl_due`、`TtlTracker`、`SimTickSource`、`execution_order`（运行时拓扑排序）、扁平 `get_node_stocks`

## Task 3: 股票池可视化——转移条件节点与边条件摘要

- [ ] 前端渲染 `transfer_condition` 节点（三角形）
- [ ] 前端渲染 `condition` 节点（紫色矩形）
- [ ] 选中边时属性面板从 `edge.params` 读取计算参数和 K 线配置
- [ ] 边标签显示触发频率 + 条件摘要，无硬编码实例内容
- [ ] 综合设置窗口三列布局正确展示流程
- [ ] Playwright 验证通过

## Task 4: 事件面板与仿真运行验证

- [ ] 事件面板分类矩阵/散点分布/定时器队列正常工作
- [ ] 仿真模式加载 100 只 fz 前缀股票
- [ ] A池/B池/C池股票流转正确，A∩B 交集进入 C 池
- [ ] `/api/sim/start` 响应 < 1s，不阻塞 Uvicorn
- [ ] 首次 `/api/sim/control step` 响应 < 500ms，不阻塞 Uvicorn
- [ ] Playwright 验证事件图标、颜色、点击详情、虚拟时钟推进

## Task 5: 设计文档更新

- [ ] `docs/DESIGN0.md` 包含验证合同（双工程师 + ≥98 分）
- [ ] `docs/DESIGN0.md` 包含验证范围（Task 1-4 评分表）
- [ ] `docs/DESIGN0.md` 包含 eventtest 量化基线（11 类事件 + 池快照 + A∩B 严格断言）
- [ ] `docs/DESIGN0.md` 包含已修复 bug 清单（反模式 + 修复说明）
- [ ] `docs/DESIGN0.md` 关联设计原则 §7/§9/§13/§16
- [ ] `docs/DESIGN.md` 包含前端验证流程、Playwright 结果、eventtest 量化结果
- [ ] `docs/DESIGN.md` 包含已修复 bug 清单和验证方法说明
- [ ] 两份文档风格一致、无矛盾
