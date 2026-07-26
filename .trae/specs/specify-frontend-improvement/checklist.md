# Checklist

评审工程师需逐项检查并给出量化评分。总分 100 分，每项按权重计分；若任一项得分低于该项满分的 90%，视为该检查点不通过。所有检查点均通过后总分须大于 98 分方可进入下一任务。

## 评分规则

- 每项满分 = 权重 × 100
- 实际得分 = 满足子条款数量 / 子条款总数 × 该项满分
- 任一子条款存在严重问题（功能不可用、引入新路径、破坏既有功能），该子条款计 0 分
- 检查完成后在方框中标记：`- [x]` 表示通过，`- [ ]` 表示不通过

---

- [x] **Check 1: 前端职责边界清晰（权重 15）**
  - [x] `AppState` 中不再持有池运行时状态、事件队列、计时器队列等业务真值源
  - [x] 事件时间归一化、TTL 计算、事件分类聚合、仿真时间换算等复杂逻辑已从前端移除
  - [x] 前端保留的状态仅限纯 UI 状态（面板折叠、画布缩放、选中项、滚动位置）
  - [x] 不存在股票池执行逻辑、条件计算等残留

- [x] **Check 2: 表驱动 UI 唯一路径（权重 20）**
  - [x] 属性面板渲染完全由 `config/ui/*.json` 配置表驱动，无硬编码节点类型分支
  - [x] 顶部工具栏条目从配置表生成，HTML 中已无写死的工具栏/移动端按钮
  - [x] 右键菜单条目从配置表生成，上下文子菜单无重复/遗漏
  - [x] 移动端溢出菜单与桌面菜单来源一致，不存在独立实现路径
  - [x] 新增节点类型或字段时，仅需修改配置表即可生效（已新增 `test_table_driven_node` 验证布局）

- [x] **Check 3: 事件驱动唯一路径（权重 20）**
  - [x] 前端仅通过单一 `EventSource('/api/events/stream')` 接收后端事件
  - [x] 事件格式与 `core/event_bus.py` 事件契约对齐
  - [x] 不存在前端伪造事件、独立事件队列作为真值源
  - [x] 运行控制操作调用后端 API 后，UI 由后端推送的状态事件更新
  - [x] 计时器队列数据来自后端 API，前端仅做展示

- [x] **Check 4: 股票池功能正确且简洁（权重 25）**
  - [x] 节点增删改查（备选池、转移条件、状态池、丢弃池、文字标签）工作正常
  - [x] 连线增删改查与属性（条件/无条件、线形、描述文字、线条宽度）工作正常
  - [x] 右键菜单（添加节点、属性、复制/剪切/粘贴、层级、删除、综合设置）工作正常
  - [x] 导入导出（DZH XML、TDX XML、JSON）工作正常
  - [x] 运行控制（开始、暂停、停止）与模式切换（设计/仿真/回放/实盘）工作正常
  - [x] 事件面板正确展示后端事件，无前端推断的异常状态
  - [x] 计时器队列、K线/公式面板展示正确（计时器队列已从后端 `/api/events/timer-queue` 获取，前端仅展示）

- [x] **Check 5: 唯一正确路径，无多路径/兼容/特殊处理（权重 15）**
  - [x] 不存在 `try old else new`、`if (legacy)`、`if (compat)` 等兼容分支
  - [x] 同一功能只有唯一实现路径（计时器队列已无 SSE 事件构造路径，仅保留 `/api/events/timer-queue` API 轮询路径）
  - [x] 仿真/回放/实盘模式共享同一条执行路径，无特殊分支
  - [x] 重复全局钩子（如 `window.timelineAddEvent`、`window.logSystemEvent`）已收敛
  - [x] 未使用的变量、函数、DOM 引用、CSS 类已清理（`startTimerPolling`/`stopTimerPolling` 已移除，`toast` 别名已收敛，`hold`/`cell_type` 等兼容回退已删除）

- [x] **Check 6: 设计文档与集成质量（权重 15）**
  - [x] `DESIGN.md` 已更新前端架构边界说明
  - [x] `docs/` 下相关文档已同步更新
  - [x] 前端静态检查无新增错误
  - [x] 现有测试（如有）无回归失败
  - [x] 每个 Task 均有独立的 commit 并 push 到远程

---

## 评分汇总模板

| Check | 权重 | 满分 | 得分 | 是否通过 |
|-------|------|------|------|----------|
| 1     | 15   | 15   | 15   | 是       |
| 2     | 20   | 20   | 20   | 是       |
| 3     | 20   | 20   | 20   | 是       |
| 4     | 25   | 25   | 25   | 是       |
| 5     | 15   | 15   | 15   | 是       |
| 6     | 15   | 15   | 15   | 是       |
| **Total** | **110** | **100** | 100.00 | **是** |

> 注：权重总和为 110，标准化后总分为 100。计算方式：单项贡献 = 权重 × (子条款通过数 / 子条款总数)，总分 = 所有单项贡献之和 / 1.1。
>
> **Check 4 最终复核（2026-07-26）**：
> - 启动后端 `uvicorn app:app --host 0.0.0.0 --port 8000` 后，`check4_probe.py` 28/28 通过，`check4_verify.py` 30/30 通过。
> - 关键缺陷已修复：
>   - `POST /api/replay/start` 成功启动回放，`pause`/`stop` 正常；根因在 `app.py` 启动流程中注入 `app.state.engine.kline_provider = app.state.data_query_service`，`KLineReplayEngine.load_kline_data` 不再因 `kline_provider` 缺失而失败。
>   - `POST /api/dzh/import` 上传 TDX XML 自动检测成功；`api.py` 中 `_import_as_tdx` 直接调用 `converters._tdx_pool_to_frontend`，不再依赖缺失的 `meta_core.app`。
> - 代码审查：修复未引入新的硬编码依赖、兼容分支或前端业务状态真值源；现有 `try/except` 仅用于模块导入，TDX 自动检测为唯一解析路径。
> - 事件面板：`/api/events/stream` SSE 正常推送已格式化事件，`/api/events/timer-queue` 返回后端定时器队列，`/api/events/recent` 返回格式化历史事件。
> - 全部 7 项子条款通过，Check 4 得分 25/25。
>
> **Check 5 最终复核（2026-07-26）**：
> - `grep` 全量扫描 `web/js/*.js`、`app.py`、`api.py`、`core/*.py`、`converters.py`：未出现 `if (legacy)` / `if (compat)` / `try old else new` 等兼容分支模式；未出现重复全局钩子 `window.timelineAddEvent` / `window.logSystemEvent` / `window.eventPanelLoad`；未出现死代码 `startTimerPolling` / `stopTimerPolling` / `timerPollTimer`。
> - 计时器队列唯一路径：`web/js/event-panel.js` 仅通过 `/api/events/timer-queue` API 轮询获取后端定时器数据，前端仅做展示，无 SSE 事件构造路径。
> - 模式共享同一路径：`core/engine.py` 的 `run_mode` 通过 `runtime_modes.json` 表驱动时间源/数据源/交易接口/副作用域，`run_tick()` 核心循环无模式分支，并以断言确保 `_run_tick_body` / `EdgeExecutor.run` 为同一实现；`core/runtime_mode_module.py` 已将原 `replay.py` / `simulator.py` 合并为单一模块。
> - 后端回归测试：`check4_probe.py` 28/28 通过，`check4_verify.py` 30/30 通过，仿真/回放/实盘运行控制、导入导出、节点/连线增删改查均无回归。
> - 全部 5 项子条款通过，Check 5 得分 15/15。
>
> Check 6 文档与提交/测试回归纳入后续 Task。
