# Meta Core 股票池平台 - 事件面板运行时 bug 修复 V6

## Why

V5 双工程师协作评审通过 7 任务（平均 98.57/100），但用户在仿真运行真实验证时发现多个运行时 bug，这些 bug 在静态代码审查 + Node.js 语法检查中无法被发现，必须通过实际启动仿真并观察事件面板才能暴露：

1. **事件 ts 坐标系混乱**：仿真模式下事件堆积在时间轴右边界并向下跑，原因是后端事件 ts 混合了真实 Unix 秒（1.78e9）与仿真相对秒（34501），导致前端 `normalizeToModeMs` 无法正确归一化
2. **分类图标语义错位**：原映射 tick=📊/edge=⚡/signal=💰/system=🔧 与语义不符，导致界面混乱
3. **散点视图事件错位**：按分类上下微调 6px，未在同一水平中线显示
4. **分类点击无响应**：`toggleSpan` 调用 `e.stopPropagation()` 阻止冒泡，导致行点击事件不触发
5. **定时器触发类型未识别**：`tick_timer` 正则 `/ticktimer|tick.*timer|tickdue/i` 无法匹配单独的 `tick`（来自 `details.kind='tick'`）
6. **后端事件 ts 真实时间戳泄漏**：`_publish_bar_changed` 内部调用 `time_at(state=composer.state)`，而 `composer.state` 是 `_InternalState`（`time_source` 为空 dict），导致 `time_at` 走 wall_clock 分支返回 `time.time()`，污染 `BarComposed` 和 `DataChanged(source="bar")` 事件 ts

## What Changes

### G2 硬约束强化：仿真/实盘同代码

本次修复彻底贯彻 G2 硬约束（仿真/实盘同代码，仅由 `state.time_source.driver_type` 在 `time_at` 内部决定时间源）：

- **删除仿真专用分支**：`time_at` 函数删除 `tsf < 1e9 else 0.0` hack 和 `if not ts_cfg: return time.time()` fallback，纯靠 `driver_type` 分派
- **删除 `MockDataSource._current_ts` 中 `time.time()` fallback**：统一走 `time_at(state)`
- **`_step_once` 不重复覆盖 `driver_type`**：由 `_post_init_mode_state` 一次性设置（virtual），此处仅推进虚拟时钟 `current_ts`
- **`_publish_bar_changed` ts 由事件流传递**：删除内部 `time_at(state=composer.state)` 调用，改为接收 `ts` 参数，来源于上游 `DataChanged(tick)` 事件的 `event.ts`
- **`BarComposer.on_tick` 接收 `event_ts` 参数**：`TickBarModule._on_data_changed` 调用时传入 `event.ts`

### 前端事件面板修复

- **`normalizeToModeMs` 时间归一化**：区分仿真相对秒（<1e9）与真实 Unix 秒（≥1e9），仿真模式下统一转为相对毫秒，避免坐标系不一致
- **分类图标标准化**：`CATEGORY_CONFIG` 更新为 tick=📡、edge=🔀、signal=🔔、system=⚙
- **散点视图中线**：`cy = plotH / 2`，所有事件在中线显示，删除按分类上下微调
- **分类点击响应**：移除 `toggleSpan` 的 `e.stopPropagation()`，点击时直接调用 `renderDetailForCategory`
- **定时器触发类型识别**：`tick_timer` 正则加入 `\btick\b`，匹配 `details.kind='tick'`
- **`TIMER_TRIGGER_TYPES` 表驱动**：边定时器/TTL超时/Tick定时器/一次性/循环/定时器六类，通过 `getTimerTriggerType` 函数识别

## Impact

- **Affected specs**: `refine-frontend-stockpool-v5`（前一版本，已结项但运行时验证暴露 bug）、`specify-frontend-improvement`（基础规范）
- **Affected code**:
  - `core/domain.py` - `time_at` 函数 G2 合规化
  - `core/engine.py` - `_run_tick_body` 删除 DIAG 日志、`_on_tick_received` ts 选择逻辑
  - `core/runtime_mode_module.py` - `_step_once`/`_astep_once` 删除冗余 `driver_type` 覆盖
  - `core/tick_bar_module.py` - `_publish_bar_changed` 接收 ts 参数、`BarComposer.on_tick` 接收 `event_ts`
  - `core/execution_module.py` - `fire_due` 删除 DIAG 日志
  - `web/js/event-panel.js` - `normalizeToModeMs`、`CATEGORY_CONFIG`、散点中线、分类点击、`TIMER_TRIGGER_TYPES`
  - `web/css/styles.css` - 事件面板样式
  - `web/index.html` - JS 版本号更新（v=17）
- **Affected configs**: 无（修复仅涉及代码逻辑，不修改配置表）

## ADDED Requirements

### Requirement: G2 仿真/实盘同代码强制验证

系统 SHALL 保证仿真模式与实盘模式除 tick 生成逻辑外，其他处理流程使用相同代码路径，禁止分别处理：

- **`time_at` 单一入口**：三模式差异仅在参数（`driver_type`），不在代码分支
  - `source="wall"` 或 `state is None`：显式墙钟入口（`_now()` 无 state 上下文）
  - `driver_type in ("virtual", "sequence")`：返回 `current_ts`（虚拟秒），缺失返回 0.0
  - `driver_type in ("wall_clock", None)`：实盘模式，`current_ts` 优先，否则 `time.time()`
- **禁止 `time.time()` fallback**：`MockDataSource._current_ts` 统一委托 `time_at(state)`
- **禁止 `if mode == "simulation"` 分支**：所有时间获取通过 `time_at(state)` 单一入口
- **禁止 `current_ts >= 1e9 则返回 0` hack**：那会形成仿真专用分支，违反 G2
- **`current_ts` 正确性由设置方保证**：`_post_init_mode_state` 仿真启动时设虚拟时钟，`run_pool` 实盘启动时设墙钟

#### Scenario: 仿真模式事件 ts 全部为虚拟秒
- **WHEN** 启动仿真会话并运行 15 秒
- **AND** 拉取所有事件检查 ts 分布
- **THEN** 所有事件 ts < 1e9（仿真相对秒）
- **AND** 真实 Unix 秒泄漏事件数 = 0

#### Scenario: 实盘模式事件 ts 为墙钟
- **WHEN** 启动实盘模式
- **AND** 拉取事件检查 ts
- **THEN** 事件 ts 为真实 Unix 秒（≥ 1e9）
- **AND** 代码路径与仿真模式相同，仅 `driver_type` 参数不同

### Requirement: 事件 ts 事件流传递

系统 SHALL 保证事件 ts 通过事件流传递，不在每个订阅者中重复计算：

- **`_publish_bar_changed` 接收 `ts` 参数**：来源于上游 `DataChanged(tick)` 事件的 `event.ts`
- **`BarComposer.on_tick` 接收 `event_ts` 参数**：`TickBarModule._on_data_changed` 调用时传入 `event.ts`
- **禁止订阅者内部调用 `time_at(state)` 计算 ts**：那会与上游事件 ts 不一致，且在 `_InternalState.time_source` 未初始化时回落到 `time.time()` 污染仿真坐标系

#### Scenario: BarComposed 事件 ts 与 TickReceived 同源
- **WHEN** 仿真产生 TickReceived 事件（ts=34501）
- **AND** TickBarModule 处理后发布 DataChanged(tick)（ts=34501）
- **AND** BarComposer 合成 K 线后发布 BarComposed
- **THEN** BarComposed 事件 ts = 34501（与上游同源）
- **AND** 不调用 `time_at(state)` 重新计算

### Requirement: 前端事件时间坐标系归一化

系统 SHALL 在前端通过 `normalizeToModeMs` 函数归一化事件 ts 到当前模式坐标系：

- **仿真相对秒（< 1e9）**：直接 `*1000` 转毫秒
- **真实 Unix 秒（≥ 1e9 且 < 1e12）**：仿真模式下需减去 `simStartRealTime` 转为仿真相对毫秒
- **真实 Unix 毫秒（≥ 1e12）**：仿真模式下同样减 base

#### Scenario: 仿真模式下事件沿时间轴正确分布
- **WHEN** 仿真产生事件（ts=34501 仿真相对秒）
- **AND** 前端 `normalizeToModeMs(34501)` 转换
- **THEN** 返回 34501000 毫秒（仿真相对毫秒）
- **AND** 事件在时间轴上沿水平方向正确分布，不堆积在右边界

### Requirement: 事件分类图标语义标准化

系统 SHALL 使用以下事件分类图标映射，禁止使用语义不符的图标：

| 分类 | 图标 | 颜色 |
|------|------|------|
| tick | 📡 | 灰色 |
| edge | 🔀 | 橙色 |
| signal | 🔔 | 红色 |
| system | ⚙ | 青色 |

#### Scenario: 分类图标正确显示
- **WHEN** 仿真产生 TickReceived 事件
- **THEN** 矩阵视图中 tick 分类行显示 📡 图标
- **AND** 不显示 📊（原错位图标）

### Requirement: 散点视图事件中线显示

系统 SHALL 在散点视图中将所有事件图标显示在同一水平中线（`cy = plotH / 2`），禁止按分类上下错开：

#### Scenario: 散点视图所有事件在中线
- **WHEN** 切换到散点视图
- **THEN** 所有事件图标在 `cy = plotH / 2` 水平中线显示
- **AND** 不按分类上下微调

### Requirement: 分类点击显示事件记录

系统 SHALL 保证点击矩阵视图中的分类行后，下方详情区显示该分类的所有事件记录文本：

- **移除 `e.stopPropagation()`**：分类切换按钮不得阻止事件冒泡
- **点击时直接调用 `renderDetailForCategory`**：确保点击分类行后下方显示相关事件记录

#### Scenario: 点击分类显示记录
- **WHEN** 用户点击矩阵视图中 tick 分类行
- **THEN** 下方详情区显示 tick 分类所有事件记录文本
- **AND** 不被 `stopPropagation` 阻止

### Requirement: 定时器触发类型识别

系统 SHALL 通过 `TIMER_TRIGGER_TYPES` 表驱动识别定时器触发类型，在定时器队列显示触发类型列：

| 触发类型 | 匹配规则 | 颜色 |
|---------|---------|------|
| 边定时器 | `edge.*timer\|edgetimer\|edgefired\|crossover` | #ff9800 |
| TTL超时 | `ttl\|ttldue\|ttlexpired\|timeout` | #b71c1c |
| Tick定时器 | `ticktimer\|tick.*timer\|tickdue\|\btick\b` | #9e9e9e |
| 一次性 | `oneshot\|one_shot\|count_gte_1\|single` | #9c27b0 |
| 循环 | `recurring\|periodic\|interval\|cxtype.*0` | #4caf50 |
| 定时器（默认） | `timer\|fire\|due` | #2196f3 |

#### Scenario: Tick定时器正确识别
- **WHEN** 后端 timer-queue 返回 `spec.kind='tick'` 的定时器
- **AND** 前端构造 pseudoEvent `details.kind='tick'`
- **THEN** `getTimerTriggerType` 返回 "Tick定时器"
- **AND** 不回退到默认值"定时器"

## MODIFIED Requirements

### Requirement: V5 评审结论补充运行时验证

V5 评审基于"代码审查 + Node.js 语法检查"替代验证，未发现实质性 bug。但 V6 运行时验证暴露 6 个 bug，说明替代验证不足以发现运行时坐标系问题。本规范要求：

- **V5 §22 评审结论保留**：V5 评审的静态检查部分仍然有效
- **V6 §23 运行时修复追加**：在 DESIGN.md 追加 §23 章节，记录运行时 bug 修复与量化验证证据
- **未来评审必须包含运行时验证**：禁止仅凭代码审查 + 语法检查结项

## REMOVED Requirements

### Requirement: V5 假评审流程

**Reason**: V5 双工程师协作流程中评审工程师仅做静态检查，未实际启动仿真验证，导致 6 个运行时 bug 未被发现。用户明确要求停止假评审流程，转为真实问题定位和修复。
**Migration**: V6 直接由主 Agent 修复并验证，不再派发 sub-agent 评审工程师。

## 执行环境说明

- 必须通过 `python _verify_event_ts.py` 验证后端事件 ts 全部为仿真相对秒（< 1e9），真实 Unix 秒泄漏事件数 = 0
- 必须通过 `python _verify_trigger.py`（已删除，逻辑内联到验证脚本）或 Python 模拟验证定时器触发类型识别覆盖率 100%
- 必须通过浏览器（Playwright 或手动 Ctrl+Shift+R）真实验证事件面板所有修复点
- eventtest 171 项必须全部通过，退出码 0

## ADDED Requirements (Task 5)

### Requirement: WebSocket 路由与 HTTP 路由职责分离

系统 SHALL 将 WebSocket 路由与 HTTP 路由挂载到不同的 `APIRouter` 实例，禁止在同一个 router 中混合挂载：

- **`config_ws_router` 独立承载 WebSocket 路由**：`/api/config/ws` 与 `/api/config/ws/events` 装饰器从 `@router.websocket` 迁移到 `@config_ws_router.websocket`
- **`config_api_router` 仅承载 HTTP 路由**：保留 `dependencies=[Depends(verify_api_key)]` 强制校验
- **`config_ws_router` 不带 `dependencies` 挂载**：`app.include_router(config_ws_router)` 不传 `dependencies` 参数

#### Scenario: WebSocket 端点不被 APIKeyHeader 阻塞

- **WHEN** 启动 `uvicorn app:app`
- **AND** 客户端连接 `ws://127.0.0.1:8000/api/config/ws`
- **THEN** WebSocket 握手成功
- **AND** 服务端日志不输出 `TypeError: APIKeyHeader.__call__() missing 1 required positional argument: 'request'`
- **AND** 客户端发送 `ping` 后收到 `pong` 响应

### Requirement: include_router dependencies 边界约束

系统 SHALL 在使用 `app.include_router(router, dependencies=[...])` 时遵守以下约束：

- **dependencies 递归应用**：`dependencies` 会应用到 router 中所有路由，包括 WebSocket
- **禁止在含 WebSocket 的 router 上挂载 HTTP-only dependencies**：`APIKeyHeader`、`HTTPBasic`、`OAuth2` 等 HTTP 鉴权依赖不得作为 dependencies 应用到含 WebSocket 的 router
- **WebSocket 鉴权替代方案**：如需对 WebSocket 鉴权，应在路由函数体内通过 `websocket.headers.get(...)` 或 `websocket.query_params.get(...)` 主动校验

#### Scenario: HTTP 路由继续受 API Key 保护

- **WHEN** 客户端访问 `/api/config/tables`（HTTP 路由）不带 `X-API-Key` 头
- **AND** `auth.enabled = true` 且 `auth.api_key` 已配置
- **THEN** 服务端返回 401 Unauthorized
- **AND** `verify_api_key` 依赖正常工作（HTTP Request 可注入）
