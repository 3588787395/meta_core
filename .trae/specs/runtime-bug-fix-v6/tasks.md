# Tasks — 事件面板运行时 bug 修复 V6

> **执行机制**：主 Agent 直接修复并验证，不再派发 sub-agent 评审工程师（V5 假评审流程已停止）。
>
> **源规范**：`refine-frontend-stockpool-v5`（已结项但运行时验证暴露 bug）
>
> **承接**：V5 评审通过 7 任务（平均 98.57/100），但用户在仿真运行真实验证时发现 6 个运行时 bug，本 V6 计划承接 bug 修复工作。

## 执行机制

1. **主 Agent** 直接修复并验证，禁止 workaround 掩盖 bug，禁止另开炉灶
2. **验证方式**：
   - 后端：`python _verify_event_ts.py` 验证事件 ts 全部为仿真相对秒
   - 前端：浏览器（Playwright 或手动 Ctrl+Shift+R）验证事件面板所有修复点
   - eventtest：171 项全部通过，退出码 0
3. **门槛**：所有验证项 PASS 方可结项，无扣分替代验证

---

## Task 1: 后端事件时间戳泄漏修复 ✅ PASS

> **根因**：`_publish_bar_changed` 内部调用 `time_at(state=composer.state)`，而 `composer.state` 是 `_InternalState`（`time_source` 为空 dict），导致 `time_at` 走 wall_clock 分支返回 `time.time()`，污染 `BarComposed` 和 `DataChanged(source="bar")` 事件 ts。

- [x] **SubTask 1.1**: 修复 `time_at` 函数（`core/domain.py`）：删除 `tsf < 1e9 else 0.0` hack 和 `if not ts_cfg: return time.time()` fallback，纯靠 `driver_type` 分派
- [x] **SubTask 1.2**: 修复 `MockDataSource._current_ts`（`core/domain.py`）：删除 `time.time()` fallback，统一走 `time_at(state)`
- [x] **SubTask 1.3**: 修复 `_step_once`/`_astep_once`（`core/runtime_mode_module.py`）：删除冗余 `driver_type` 覆盖和 DIAG 诊断日志，由 `_post_init_mode_state` 一次性设置
- [x] **SubTask 1.4**: 修复 `_publish_bar_changed`（`core/tick_bar_module.py`）：接收 `ts` 参数，删除内部 `time_at(state=composer.state)` 调用
- [x] **SubTask 1.5**: 修复 `BarComposer.on_tick`（`core/tick_bar_module.py`）：接收 `event_ts` 参数，传递给 `_publish_bar_changed`
- [x] **SubTask 1.6**: 修复 `TickBarModule._on_data_changed`（`core/tick_bar_module.py`）：调用 `on_tick` 时传入 `event.ts`
- [x] **SubTask 1.7**: 运行 `python _verify_event_ts.py` 验证：659 个事件全部为仿真相对秒（34501.000），真实 Unix 秒泄漏事件数 = 0 — PASS

## Task 2: 前端事件面板分类图标与布局修复 ✅ PASS

> **根因**：分类图标语义错位（tick=📊/edge=⚡/signal=💰/system=🔧）、散点视图按分类上下微调、分类点击 `stopPropagation` 阻止冒泡。

- [x] **SubTask 2.1**: 修复 `CATEGORY_CONFIG`（`web/js/event-panel.js`）：分类图标标准化 tick=📡、edge=🔀、signal=🔔、system=⚙
- [x] **SubTask 2.2**: 修复散点视图（`web/js/event-panel.js`）：`cy = plotH / 2`，所有事件在中线显示，删除按分类上下微调
- [x] **SubTask 2.3**: 修复分类点击响应（`web/js/event-panel.js`）：移除 `toggleSpan` 的 `e.stopPropagation()`，点击时直接调用 `renderDetailForCategory`
- [x] **SubTask 2.4**: 实现 `normalizeToModeMs`（`web/js/event-panel.js`）：区分仿真相对秒（<1e9）与真实 Unix 秒（≥1e9），仿真模式下统一转为相对毫秒
- [x] **SubTask 2.5**: 浏览器验证事件面板 7 项全部 PASS：仿真模式切换、事件面板右下角不遮挡、矩阵视图分类图标📡🔀🔔⚙、分类点击显示详情、散点视图中线、定时器队列触发类型列、无 JS 错误

## Task 3: 定时器触发类型识别修复 ✅ PASS

> **根因**：`tick_timer` 正则 `/ticktimer|tick.*timer|tickdue/i` 无法匹配单独的 `tick`（来自 `details.kind='tick'`），导致 tick 定时器回退显示"定时器"。

- [x] **SubTask 3.1**: 修复 `TIMER_TRIGGER_TYPES`（`web/js/event-panel.js`）：`tick_timer` 正则加入 `\btick\b` → `/ticktimer|tick.*timer|tickdue|\btick\b/i`
- [x] **SubTask 3.2**: 更新 `web/index.html` JS 版本号 v=16 → v=17 强制浏览器加载最新代码
- [x] **SubTask 3.3**: Python 模拟验证 107 个定时器全部识别成功：Tick定时器 103、边定时器 4，无回退到默认值"定时器" — PASS
- [x] **SubTask 3.4**: 服务器验证 `event-panel.js?v=17` 包含 `\btick\b` 修复 — PASS

## Task 4: 设计文档更新（依赖 Task 1-3）✅ PASS

> **由主 Agent 直接执行**。

- [x] **SubTask 4.1**: 在 `DESIGN.md` 追加 §23 "事件面板运行时 bug 修复 V6 结果"章节，包含：① 修复清单（文件:行号格式）② 量化数据（事件 ts 分布/定时器触发类型识别统计）③ G2 硬约束合规性说明
- [x] **SubTask 4.2**: 同步更新 `DESIGN0.md` 追加 §9 "运行时验证结论（V6）"章节
- [x] **SubTask 4.3**: 创建规范文件 `.trae/specs/runtime-bug-fix-v6/spec.md`、`tasks.md`、`checklist.md`
- [x] **SubTask 4.4**: 文档简洁清晰，无冗余文字，引用具体的验证脚本输出和代码位置证据

## Task 5: WebSocket APIKeyHeader 依赖冲突修复 ✅ PASS

> **根因**：`config_api_router`（`api.py:204`）同时包含 HTTP 路由与 WebSocket 路由（`/ws`、`/ws/events`）。`app.py:545` 通过 `app.include_router(config_api_router, dependencies=[Depends(verify_api_key)])` 挂载时，dependencies 递归应用到 WebSocket 路由，而 `verify_api_key` 依赖 `APIKeyHeader.__call__(request: Request)`——WebSocket ASGI scope 不注入 HTTP `Request`，导致 `TypeError: APIKeyHeader.__call__() missing 1 required positional argument: 'request'`。

- [x] **SubTask 5.1**: 在 `api.py:815-821` 新增 `config_ws_router = APIRouter(prefix="/api/config", tags=["config-ws"])`，添加根因注释
- [x] **SubTask 5.2**: 修改 `api.py:824`：`@router.websocket("/ws")` → `@config_ws_router.websocket("/ws")`
- [x] **SubTask 5.3**: 修改 `api.py:849`：`@router.websocket("/ws/events")` → `@config_ws_router.websocket("/ws/events")`
- [x] **SubTask 5.4**: 修改 `app.py:82`：`from .api import config_api_router, config_api_init, config_ws_router`
- [x] **SubTask 5.5**: 在 `app.py:547` 新增 `app.include_router(config_ws_router)`（不带 `dependencies`）
- [x] **SubTask 5.6**: 启动 `uvicorn app:app --port 8765` 验证 4 个 WebSocket 端点全部通过：
  - `/api/config/ws` ping→pong ✅
  - `/api/config/ws/events` 连接成功 ✅
  - `/ws/highlight` 收到 `subscribe_highlight_ack` ✅
  - `/ws/pool/nonexistent` 收到 1008 `Pool not found` ✅
  - 服务端启动日志无 `TypeError: APIKeyHeader.__call__` 报错 ✅
- [x] **SubTask 5.7**: 在 `DESIGN.md` 追加 §24 章节，记录问题现象/根因定位/修复方案/修复清单/量化验证/架构约束强化
- [x] **SubTask 5.8**: 在 `DESIGN0.md` 追加 §10 章节，记录修复结论与运行时验证结论

---

# Task Dependencies

- **Task 1** (后端 ts 修复) → 无依赖
- **Task 2** (前端面板修复) → 无依赖（可与 Task 1 并行）
- **Task 3** (定时器触发类型) → 依赖 Task 2
- **Task 4** (DESIGN.md §23 更新) → 依赖 Task 1-3
- **Task 5** (WebSocket 依赖修复) → 无依赖（独立 bug 修复）

---

# 最终结项门槛

- 所有 5 个任务验证项全部 PASS
- `tasks.md` 全部勾选 `[x]`
- `checklist.md` 全部勾选 `[x]`
- `DESIGN.md` §23、§24 章节已添加
- `DESIGN0.md` §9、§10 章节已同步更新

---

# 评分规则

- 本规范不再采用 V5 的双工程师打分流程
- 验证项全部 PASS 即结项，无扣分替代验证
- 禁止 workaround 掩盖 bug：发现生产 bug 必须修复生产代码 + 改正向断言验证 spec 被满足
- 禁止测试反向断言：测试通过条件必须是"spec 被满足"，而非"bug 存在"
