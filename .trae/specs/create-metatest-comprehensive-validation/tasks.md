# Tasks

本规范按「架构工程师 → 评审工程师」流程分 5 阶段实施，覆盖前端后端所有模块。

## 阶段 1：metatest 基础设施

- [x] Task 1: 创建 metatest 目录骨架与共享 fixture
  - [x] SubTask 1.1: 创建 `metatest/__init__.py`
  - [x] SubTask 1.2: 创建 `metatest/conftest.py`，提供 8+ fixture：`virtual_clock`(34500.0)、`fz_stocks(n=100)`、`pool_engine`、`event_collector`、`pool_snapshot`、`fastapi_client`(TestClient)、`playwright_browser`、`config_store`
  - [x] SubTask 1.3: 创建 `metatest/fixtures/` 目录，含测试数据（池配置/公式/期望事件序列）
  - [x] SubTask 1.4: 创建 `metatest/scoring.py`，实现 6 维评分引擎（模块覆盖率/通过率/断言密度/事件链/性能/前端E2E）
  - [x] SubTask 1.5: 创建 `metatest/runner.py`，运行全部测试并输出量化报告
  - [x] SubTask 1.6: 创建 `metatest/README.md`，说明方法论+运行方式+评分规则

## 阶段 2：正测试集（17 个关键功能点）

- [x] Task 2: 三模式切换正测试
  - [x] SubTask 2.1: `test_positive_three_modes.py` — 仿真模式启动/暂停/步进/重置
  - [x] SubTask 2.2: 回放模式 K线回放/步进/速度控制
  - [x] SubTask 2.3: 实盘模式 tick 接入
  - [x] SubTask 2.4: 模式切换时事件面板显隐（设计隐藏/仿真回放显示）
  - [x] SubTask 2.5: 仿真与实盘同代码路径验证（G2 硬约束）
  - [x] SubTask 2.6: 仿真模式 fz 前缀替换验证
  - [x] SubTask 2.7: `normalizeToModeMs()` 时间戳归一化验证

- [x] Task 3: 股票池设计器正测试
  - [x] SubTask 3.1: `test_positive_pool_designer.py` — 11 种节点类型创建/序列化
  - [x] SubTask 3.2: 2 种边类型（Conditional/Unconditional）创建/序列化
  - [x] SubTask 3.3: 7 种 Spec（Timing/Filter/Propagate/Action/TTL/CandidateRange/ReloadSchedule）
  - [x] SubTask 3.4: `_FieldMeta` + `_FIELDS` 表驱动序列化往返
  - [x] SubTask 3.5: 边条件配置在连接上（非节点内部）
  - [x] SubTask 3.6: 边顺序号交集/差集运算次序
  - [x] SubTask 3.7: `_EVALUATOR_REGISTRY` + `@register_evaluator` 6 个评估器分派

- [x] Task 4: 事件引擎正测试
  - [x] SubTask 4.1: `test_positive_event_engine.py` — EventBus publish/subscribe/subscribe_any
  - [x] SubTask 4.2: `_events` 用 deque O(1) 丢弃验证
  - [x] SubTask 4.3: EventDriver heapq 优先队列 + 中断暂停
  - [x] SubTask 4.4: 10 类事件按序（EdgeFired 先于 FormulaEvaluated）
  - [x] SubTask 4.5: 运行时事件无序（execution_order 已删除）
  - [x] SubTask 4.6: 25+ 事件适配器（EVENT_RECORD_ADAPTERS）表驱动记录

- [x] Task 5: 公式计算正测试
  - [x] SubTask 5.1: `test_positive_formula.py` — Python 引擎 eval/eval_outvars/eval_series/eval_batch
  - [x] SubTask 5.2: HQChart 引擎协议分派（`_ENGINE_DISPATCH` 表）
  - [x] SubTask 5.3: `IFormulaEngine` Protocol 4 类结构化匹配
  - [x] SubTask 5.4: 三模式上下文（live/replay/simulation）
  - [x] SubTask 5.5: LRU 缓存命中/未命中
  - [x] SubTask 5.6: 禁止 cross 函数验证
  - [x] SubTask 5.7: 公式与筛选严格分离

- [x] Task 6: K 线合成正测试
  - [x] SubTask 6.1: `test_positive_kline.py` — 1min→5min/15min/30min/60min 合成
  - [x] SubTask 6.2: 60min→day 合成（time 重写为 00:00:00）
  - [x] SubTask 6.3: day→week/month 合成（`_PERIOD_KEY_FUNCS` 表）
  - [x] SubTask 6.4: `_SYNTHESIS_RULES` 表 10 个映射验证
  - [x] SubTask 6.5: `synthesize(bars, source, target)` 单一入口
  - [x] SubTask 6.6: `BarComposer.on_tick` 接受 `event_ts` 参数
  - [x] SubTask 6.7: `publish_data_changed` 统一发布器
  - [x] SubTask 6.8: `_publish_tick_batch` 批量发布

- [x] Task 7: 交易执行正测试
  - [x] SubTask 7.1: `test_positive_trade.py` — C 池入池立即市价买入 100 股
  - [x] SubTask 7.2: 停留 20 分钟出池卖出
  - [x] SubTask 7.3: 入池动作分发（声音/弹窗/TDX 板块/历史保存）
  - [x] SubTask 7.4: 持仓管理（_Position 增减）
  - [x] SubTask 7.5: 交易记录（_TradeRecord）

- [x] Task 8: 导入/导出正测试
  - [x] SubTask 8.1: `test_positive_import_export.py` — `import_pool(path, format)` 三格式
  - [x] SubTask 8.2: `export_pool(config, path, format)` 三格式
  - [x] SubTask 8.3: `_IMPORT_RULES` / `_EXPORT_RULES` 表 3 个条目
  - [x] SubTask 8.4: DZH XML 解析（attrtext 6 种条目）
  - [x] SubTask 8.5: TDX XML 解析
  - [x] SubTask 8.6: attr 位标志解码/编码

- [x] Task 9: 配置热加载正测试
  - [x] SubTask 9.1: `test_positive_hot_reload.py` — `ConfigStore.get_table(name)` 加载
  - [x] SubTask 9.2: `ConfigStore.get_data_file(name)` 加载非配置表
  - [x] SubTask 9.3: 热加载 watchdog 监听
  - [x] SubTask 9.4: `ConfigChanged` 事件发布（`_notify_changed` 单一方法）
  - [x] SubTask 9.5: 版本历史/回滚/diff
  - [x] SubTask 9.6: 禁止 `_load_json`/`_load_config` 验证（Grep 0 匹配）

- [x] Task 10: 事件面板正测试
  - [x] SubTask 10.1: `test_positive_event_panel.py` — 默认隐藏（display:none）
  - [x] SubTask 10.2: 仿真/回放模式 .visible 类显示
  - [x] SubTask 10.3: 右下角固定（right:16px;bottom:16px;560×400px）
  - [x] SubTask 10.4: 矩阵视图时间轴水平+红色 NOW 垂直线
  - [x] SubTask 10.5: 散点同行/分类切换（cy=plotH/2）
  - [x] SubTask 10.6: `_STYLE` 配置对象 + `_DRAW_LAYERS` 表 5 层
  - [x] SubTask 10.7: `renderEventCanvas(ctx, state, layoutMode)` 单一函数
  - [x] SubTask 10.8: emoji 字体（'Segoe UI Emoji, Apple Color Emoji, Microsoft YaHei'）
  - [x] SubTask 10.9: 事件分类图标（tick=📡, edge=🔀, signal=🔔, system=⚙）
  - [x] SubTask 10.10: `getTimerTriggerType()` + `TIMER_TRIGGER_TYPES` 表

- [x] Task 11: HTTP API 正测试
  - [x] SubTask 11.1: `test_positive_http_api.py` — api.py 47+ 端点
  - [x] SubTask 11.2: app.py 60+ 端点
  - [x] SubTask 11.3: `require_config_store` Depends 验证
  - [x] SubTask 11.4: `get_simulator` Depends 验证
  - [x] SubTask 11.5: `_SIM_ACTIONS` 表 5 个 action
  - [x] SubTask 11.6: `_TDX_ACTIONS` 表 4 个 method
  - [x] SubTask 11.7: API Key 鉴权
  - [x] SubTask 11.8: HTTP/WS 路由分离（不同 APIRouter）

- [x] Task 12: WebSocket/SSE 正测试
  - [x] SubTask 12.1: `test_positive_websocket.py` — SSE 事件流订阅
  - [x] SubTask 12.2: WebSocket 配置变更推送
  - [x] SubTask 12.3: 会话隔离
  - [x] SubTask 12.4: 重连机制（RECONNECT_DELAY=3000）
  - [x] SubTask 12.5: MAX_EVENTS=2000 限流
  - [x] SubTask 12.6: RENDER_THROTTLE=200 节流

- [x] Task 13: 数据源正测试
  - [x] SubTask 13.1: `test_positive_data_source.py` — MockProvider 确定性
  - [x] SubTask 13.2: HQChartProvider 协议
  - [x] SubTask 13.3: DataSourceContract 显式 mock 模式
  - [x] SubTask 13.4: CandidatePoolResolver 备选池解析
  - [x] SubTask 13.5: CandidatePoolRefreshManager 定时刷新
  - [x] SubTask 13.6: 板块指数映射

- [x] Task 14: 校验器正测试
  - [x] SubTask 14.1: `test_positive_validators.py` — 5 层校验（Syntax/Logic/Business/Schema/ConfigIntegrity）
  - [x] SubTask 14.2: TopologyPatternMatcher 拓扑模式匹配
  - [x] SubTask 14.3: `should_fire` 时间触发判定
  - [x] SubTask 14.4: `_get_table` 模块级帮助函数

- [x] Task 15: 原生动作库正测试
  - [x] SubTask 15.1: `test_positive_native_actions.py` — 7 步表驱动执行（resolve/pass/filter/dzh_filter/propagate/transfer/remove）
  - [x] SubTask 15.2: 动作分派 dict（禁止 if/elif 链）
  - [x] SubTask 15.3: nset/starttype/TTL/回调全查表
  - [x] SubTask 15.4: Profit Analysis 评分排序

- [x] Task 16: 存储层正测试
  - [x] SubTask 16.1: `test_positive_storage.py` — SQLite 批量 executemany
  - [x] SubTask 16.2: 连接复用（避免重复 connect+WAL）
  - [x] SubTask 16.3: WAL 模式
  - [x] SubTask 16.4: 安全路径拼接（safe_path_join）

- [x] Task 17: 备选池+池间转移正测试
  - [x] SubTask 17.1: `test_positive_pool_transfer.py` — 备选池 100 只 fz 股票
  - [x] SubTask 17.2: 备选池→A池（1分钟触发、5min KDJ 金叉、停留100分钟）
  - [x] SubTask 17.3: 备选池→B池（10秒触发、1min MACD 金叉、停留200分钟）
  - [x] SubTask 17.4: A+B→C池（5秒触发、交集）
  - [x] SubTask 17.5: C池买入100股停留20分钟卖出

- [x] Task 18: 迁移 Oracle 正测试
  - [x] SubTask 18.1: `test_positive_migration_oracle.py` — 5 个 Oracle 场景回归基线
  - [x] SubTask 18.2: oracle_conditional_transfer
  - [x] SubTask 18.3: oracle_multi_level_cascade
  - [x] SubTask 18.4: oracle_replay_mode_tick
  - [x] SubTask 18.5: oracle_simple_candidate_to_state
  - [x] SubTask 18.6: oracle_target_pool_action

## 阶段 3：反测试集（异常与边界）

- [x] Task 19: 异常配置反测试
  - [x] SubTask 19.1: `test_negative_empty_pool.py` — 空备选池/空 tick 数据
  - [x] SubTask 19.2: `test_negative_invalid_config.py` — 缺字段/类型错误
  - [x] SubTask 19.3: `test_negative_bad_topology.py` — 自环/孤点/重复边

- [x] Task 20: 运行时异常反测试
  - [x] SubTask 20.1: `test_negative_duplicate_transfer.py` — 重复入池
  - [x] SubTask 20.2: `test_negative_ttl_no_position.py` — TTL 到期无持仓
  - [x] SubTask 20.3: `test_negative_formula_error.py` — 除零/未定义变量/类型不匹配
  - [x] SubTask 20.4: `test_negative_module_import.py` — 跨模块非法引用（零引用约束）

- [x] Task 21: API/前端反测试
  - [x] SubTask 21.1: `test_negative_http_404_500.py` — 路由 404/405/500
  - [x] SubTask 21.2: `test_negative_sse_disconnect.py` — SSE 断连/重连
  - [x] SubTask 21.3: `test_negative_websocket_error.py` — WebSocket 消息格式错误
  - [x] SubTask 21.4: `test_negative_config_missing.py` — 配置文件缺失/格式错误
  - [x] SubTask 21.5: `test_negative_frontend_xss.py` — 前端 XSS 注入（escHtml/escapeHtml）

## 阶段 4：合测试集（端到端集成）

- [x] Task 22: 仿真全流程合测试
  - [x] SubTask 22.1: `test_integration_sim_full_flow.py` — 备选池→A池→B池→C池→买入→TTL→卖出
  - [x] SubTask 22.2: 事件计数断言
  - [x] SubTask 22.3: 池状态快照断言
  - [x] SubTask 22.4: 事件链顺序断言（10 类事件按序）

- [x] Task 23: 三模式合测试
  - [x] SubTask 23.1: `test_integration_three_modes.py` — 仿真/回放/实盘同代码路径
  - [x] SubTask 23.2: 回放模式 K线回放全流程
  - [x] SubTask 23.3: 实盘模式 tick 链路全流程

- [x] Task 24: 导入导出 roundtrip 合测试
  - [x] SubTask 24.1: `test_integration_roundtrip.py` — DZH→JSON→TDX→JSON→DZH
  - [x] SubTask 24.2: 配置一致性断言

- [x] Task 25: 配置热加载合测试
  - [x] SubTask 25.1: `test_integration_hot_reload.py` — 修改 JSON→watchdog→ConfigChanged→模块重载
  - [x] SubTask 25.2: 热加载后行为一致性

- [x] Task 26: 元模式合并验证合测试
  - [x] SubTask 26.1: `test_integration_meta_pattern.py` — 7 项元模式合并验证
  - [x] SubTask 26.2: `_step_once_impl(async_mode)` 同步/异步同代码路径
  - [x] SubTask 26.3: `IFormulaEngine` Protocol + `_ENGINE_DISPATCH` 表
  - [x] SubTask 26.4: `require_config_store` + `get_simulator` + `_SIM_ACTIONS` Depends
  - [x] SubTask 26.5: `ConfigStore.get_table` / `get_data_file` 统一加载（禁止 `_load_json`）
  - [x] SubTask 26.6: `synthesize` + `_SYNTHESIS_RULES` 表
  - [x] SubTask 26.7: `import_pool` / `export_pool` + `_IMPORT_RULES` / `_EXPORT_RULES` 表
  - [x] SubTask 26.8: `renderEventCanvas` + `_DRAW_LAYERS` + `_STYLE`

- [x] Task 27: 前端 E2E 合测试（Playwright）
  - [x] SubTask 27.1: `test_frontend_mode_switch.py` — 三模式切换 UI
  - [x] SubTask 27.2: `test_frontend_event_panel.py` — 事件面板矩阵/散点视图
  - [x] SubTask 27.3: `test_frontend_pool_designer.py` — 池设计器节点/边操作
  - [x] SubTask 27.4: `test_frontend_import_export.py` — 导入导出 UI
  - [x] SubTask 27.5: `test_frontend_formula.py` — 公式管理 UI

## 阶段 5：量化评分与评审

- [x] Task 28: 量化评分引擎实现
  - [x] SubTask 28.1: `scoring.py` 实现 6 维评分（模块覆盖率/通过率/断言密度/事件链/性能/前端E2E）
  - [x] SubTask 28.2: `runner.py` 运行全部测试并输出量化报告
  - [x] SubTask 28.3: 报告格式：6 维分数 + 总分 + 扣分项 + 重做清单

- [ ] Task 29: 评审工程师验证
  - [ ] SubTask 29.1: 运行 `python -m metatest.runner` 确认总分 ≥ 95
  - [ ] SubTask 29.2: 验证 6 维分数均达标
  - [ ] SubTask 29.3: 验证测试覆盖 17 个关键功能点
  - [ ] SubTask 29.4: 验证正反合三层方法论完整
  - [ ] SubTask 29.5: 验证元模式合并 7 项正确性

# Task Dependencies

- 阶段 1：Task 1 无依赖，基础设施先行
- 阶段 2：Task 2-18 依赖 Task 1（conftest.py fixture）；Task 之间可并行（不同模块）
- 阶段 3：Task 19-21 依赖 Task 1；可与阶段 2 并行
- 阶段 4：Task 22-27 依赖阶段 2+3（正反测试通过后才能集成）；Task 26 依赖 `perfect-meta-pattern-iteration` 完成
- 阶段 5：Task 28 依赖阶段 1-4；Task 29 依赖 Task 28

# 并行度建议

| 阶段 | 可并行 Task | 说明 |
|---|---|---|
| 2 | Task 2-18 全部并行 | 不同模块/文件，无冲突 |
| 3 | Task 19-21 全部并行 | 异常场景独立 |
| 4 | Task 22-27 部分并行 | Task 22-25 并行；Task 26 依赖元模式；Task 27 依赖 Playwright |
