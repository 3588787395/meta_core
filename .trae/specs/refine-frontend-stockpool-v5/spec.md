# Meta Core 股票池平台 - 前端股票池界面综合完善与评审 V5

## Why

`specify-frontend-improvement` 已交付 18 个功能模块（全部 `[x]`），但前一轮迭代评审 `refine-frontend-stockpool-v4` 仅完成 Task 1-3（画布引擎/节点边渲染/属性面板，均 98/100），Task 4-10（模式切换、仿真股票流转、事件面板、表/事件驱动一致性、方法属性事件清单、旧接口清理、设计文档更新）尚未启动。同时 V4 评审遗留若干已知缺陷（Playwright 替代验证扣分、模式指示器颜色值与 spec 描述存在差异、散点视图标签样式不一致等），需要在新一轮规范中系统化修复。

此外，用户明确要求："所有股票池功能正确并且简洁，符合表驱动事件驱动，验证所有方法属性事件都能正确工作"。这要求本规范不仅要完成功能评审，更要进行**架构级清理**：删除冗余方法/属性/事件、确保单一真相源、严格禁止兼容旧接口、保证模块间通过事件总线/API 解耦。

## What Changes

### 综合完善（承接 V4 Task 4-10）
- **模式切换评审与修复**：验证设计/实盘/回放/仿真四种模式的状态管理、控制面板显隐、事件面板显隐、运行中切换阻止逻辑
- **仿真股票流转评审与修复**：验证备选池 100 只 fz 股票加载、cond1→pool_A（1min/KDJ金叉/100分钟TTL）、cond2→pool_B（10s/MACD金叉/200分钟TTL）、cond3→pool_C（5s/交集/买入100股/20分钟卖出）完整事件链
- **事件面板评审与修复**：验证 9 类事件分类显示、矩阵/散点视图一致性、定时器队列、默认显隐控制、本地存储恢复
- **表驱动/事件驱动架构一致性评审**：验证 UI 组件/字段/校验由后端配置表驱动、事件流通过 SSE/WebSocket/pub-sub 传递、模块间无直接引用、destroy() 正确清理订阅
- **方法/属性/事件清单逐项验证与清理**：列出所有核心对象公开接口，Playwright 逐项调用验证，删除冗余代码
- **禁止兼容旧接口检查与回归验证**：扫描禁用 token（get_node_stocks/set_node_stocks/SimTickSource/EdgeFired.changed_codes/运行时 execution_order），运行 eventtest 173 个测试
- **设计文档更新**：在 DESIGN.md 追加 §22 章节，同步更新 DESIGN0.md

### 新增要求（V5 强化）
- **架构级清理**：junk code 零容忍，未使用的方法/属性/事件/变量/导入必须删除
- **单一真相源强制验证**：`_nodes`/`nodeElements`、`selectedNodeId`/`selectedNodeIds`、`simulationState`、`_data`、`transform` 等关键属性必须保持单一真相源，无冗余状态字段
- **方法签名一致性**：所有公开方法输入输出必须符合 spec，无未捕获异常，无 workaround
- **简洁性要求**：所有股票池功能必须正确并且简洁，禁止过度设计
- **颜色值统一**：模式指示器颜色必须严格匹配 spec（设计=蓝色、实盘=#27ae60、回放=#e67e22、仿真=#9b59b6），修复 V4 评审遗留的颜色值差异

## Impact

- **Affected specs**: `specify-frontend-improvement`（基础规范，已完成）、`refine-frontend-stockpool-v4`（前一版本，部分完成）、`frontend-iterative-polish-v3`（已整合到 V4）、`frontend-stockpool-review-plan`（已整合到 V4）
- **Affected code**:
  - `web/js/canvas.js` - FlowCanvas 画布引擎
  - `web/js/ui.js` - TableDrivenPanel、ComponentRegistry、ValidationEngine、DataBinder
  - `web/js/app.js` - AppState、PoolDataManager、Charts、main.js
  - `web/js/event-panel.js` - EventPanel 事件面板
  - `web/css/styles.css` - 样式定义
  - `web/index.html` - 主界面
  - `DESIGN.md` / `DESIGN0.md` - 设计文档
- **Affected configs**: `config/pools/sim_test_pool_100.json`、`config/registry/*`、`config/toolbar_config.json`

## ADDED Requirements

### Requirement: 双工程师迭代评审流程

系统 SHALL 采用"架构工程师修复 + 评审工程师打分"的双轨流程，由调度方（主 Agent）编排：

1. **架构工程师**（sub-agent_type=general_purpose_task）：依据 spec.md 修复评审发现的问题，禁止兼容旧接口，禁止 workaround 掩盖 bug，禁止另开炉灶，禁止过度设计
2. **评审工程师**（sub-agent_type=general_purpose_task）：依据 checklist.md 逐项验证并打分，必须基于 Playwright 截图 + 浏览器控制台输出 + eventtest 量化结果，不得仅凭代码审查
3. **门槛**：≥ 98 分方可进入下一任务；< 98 分打回架构工程师重做（携带扣分点清单），直至 ≥ 98
4. **依赖**：严格按 Task Dependencies 执行，前置任务未达 98 分不得开启后续任务
5. **并行策略**：调度方可并行派发无依赖的任务对，但每个任务内部"架构工程师修复 → 评审工程师打分"必须串行。同一时刻最多并行 2 个任务对

#### Scenario: 任务通过评审
- **WHEN** 架构工程师完成修复并提交评审
- **AND** 评审工程师依据 checklist.md 逐项打分
- **AND** 总分 ≥ 98 分
- **THEN** 调度方在 tasks.md 勾选 `[x]`，在 checklist.md 勾选通过项
- **AND** 进入下一任务

#### Scenario: 任务未通过评审
- **WHEN** 评审工程师总分 < 98 分
- **THEN** 调度方将扣分点清单发回架构工程师
- **AND** 架构工程师修复后重新提交评审
- **AND** 循环直至 ≥ 98 分

### Requirement: 所有方法属性事件可验证

系统 SHALL 保证所有核心对象的公开方法/属性/事件可被逐项验证：

- **核心对象清单**：FlowCanvas、PoolDataManager、TableDrivenPanel、EventPanel、AppState、LRUCache、BaseChart/KlineChart/IndicatorChart、RuleEditor、ConfigSync、HighlightManager、ToolbarRenderer、ComponentRegistry、ValidationEngine、DataBinder、FormulaEditor、ConfigManager、ComprehensiveSettings
- **验证方式**：Playwright 在浏览器中逐项调用每个公开方法，验证输入输出符合 spec，无未捕获异常
- **清理要求**：junk code 零容忍，未使用的方法/属性/事件/变量/导入必须删除（ESLint 静态分析 + Playwright 运行时验证确认无回归）

#### Scenario: 方法验证通过
- **WHEN** Playwright 调用 `FlowCanvas.fitToContent(padding)` 方法
- **THEN** 画布正确适应内容，所有节点可见
- **AND** 浏览器控制台无 JavaScript 错误

#### Scenario: 冗余代码清理
- **WHEN** ESLint 静态分析发现未使用的方法
- **THEN** 架构工程师删除该方法
- **AND** Playwright 运行时验证确认无回归
- **AND** 所有现有功能测试仍然通过

### Requirement: 表驱动与事件驱动架构一致性

系统 SHALL 严格遵守表驱动与事件驱动架构：

- **表驱动**：UI 组件类型、属性字段、校验规则、节点渲染参数、工具栏按钮显隐均由后端配置表决定（cell_type_registry + dzh_type_map + field_definitions + toolbar_config.json），前端无硬编码字段类型分支
- **事件驱动**：模块间通过 API/SSE/WebSocket/pub-sub/CustomEvent 交互，禁止直接引用：
  - `FlowCanvas` 通过 `onChange()` 回调通知外部，不直接调用 `PoolDataManager`
  - `TableDrivenPanel` 通过 `onChange()` 回调通知，不直接调用 `FlowCanvas`
  - `event-panel.js` 通过 `window.xxx` 桥接函数与 `app.js` 交互，使用 `typeof window.xxx === 'function'` 守卫
  - `HighlightManager` 通过 `canvas.highlightNode()` 接口调用，不直接操作 SVG
  - `ConfigManager` 通过 API 与后端交互，不直接操作 `PoolDataManager._data`
- **订阅清理**：事件订阅在 `destroy()` 中正确取消订阅，无内存泄漏

#### Scenario: 表驱动配置变更生效
- **WHEN** 修改后端 `field_definitions` 配置
- **THEN** 前端属性面板自动反映变化
- **AND** 无需修改前端代码

#### Scenario: 模块间无直接引用
- **WHEN** 检查 `FlowCanvas` 源代码
- **THEN** 不存在对 `PoolDataManager` 的直接调用
- **AND** 所有变更通过 `onChange()` 回调通知外部

### Requirement: 禁止兼容旧接口

系统 SHALL 严格禁止兼容旧接口，以下 token 在前端代码中必须零匹配（除明确的豁免情况）：

- `get_node_stocks` / `set_node_stocks`（节点类型字符串除外）
- `SimTickSource`
- `EdgeFired.changed_codes`
- 运行时 `execution_order`（节点类型字符串 `execution_order` + 编译期 `CompiledSchedule.execution_order` 字段除外）
- 前端 API 调用必须使用 `StatePoolView` 视图接口，不直接操作 `node_stocks`
- `PoolDataManager` 不暴露 `_data.node_stocks` 直接访问接口

#### Scenario: 旧接口扫描通过
- **WHEN** 全局搜索 `get_node_stocks`
- **THEN** 零匹配（节点类型字符串除外）
- **AND** eventtest 173 个正反合测试退出码 0

### Requirement: 设计文档同步更新

系统 SHALL 在所有任务完成后更新设计文档：

- `DESIGN.md` 追加 §22 "前端迭代评审 V5 结果"章节，包含：
  1. 各任务最终评分表（Task 1-7 分数 + 通过日期）
  2. 修复清单（文件:行号格式）
  3. 量化数据（事件计数/池状态快照/Playwright 截图引用）
- `DESIGN0.md` 同步更新前端验证章节，确认表驱动/事件驱动一致性结论（含验证证据）
- 文档简洁清晰，无冗余文字，无重复表述

#### Scenario: 文档更新完成
- **WHEN** 所有 Task 1-6 通过评审（≥98 分）
- **THEN** 调度方汇总评分、修复清单、量化数据
- **AND** 在 `DESIGN.md` 追加 §22 章节
- **AND** 同步更新 `DESIGN0.md`
- **AND** 文档评审 ≥ 98 分

## MODIFIED Requirements

### Requirement: 模式指示器颜色统一

V4 评审发现模式指示器颜色值与 spec 描述存在差异（实盘 #2ecc71 vs #27ae60、回放 #f39c12 vs #e67e22）。本规范要求统一为：

- 设计模式：蓝色（#3498db）
- 实盘模式：#27ae60（绿色）
- 回放模式：#e67e22（橙色）
- 仿真模式：#9b59b6（紫色）

#### Scenario: 颜色值统一
- **WHEN** 切换到实盘模式
- **THEN** 模式指示器颜色为 #27ae60
- **AND** CSS 样式表中无 #2ecc71 残留

## REMOVED Requirements

### Requirement: V4 Task 1-3 重复评审

**Reason**: V4 Task 1-3（画布引擎/节点边渲染/属性面板）已完成 98/100，无需重复评审
**Migration**: V5 直接复用 V4 评审结果，仅做快速回归验证

## 执行环境说明

- 若 Playwright 环境不可用，可采用"代码审查 + 语法检查 + HTTP 加载验证 + Node.js 测试脚本"作为替代验证手段，但每项替代验证扣 1 分（最高扣 5 分）
- eventtest 必须真实运行（不可替代），退出码 0 方可通过
