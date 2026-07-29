# Checklist

本检查清单按「架构工程师 → 评审工程师」流程组织，对应 tasks.md 中的 5 个阶段。评审工程师必须运行 `python -m metatest.runner` 并以 6 维量化指标为唯一打分依据。

## 评审打分规则（评审工程师使用）

- 评审工程师**必须运行** `python -m metatest.runner`，以输出报告中的 6 维量化指标打分
- 每项检查点通过得满分，部分通过按比例扣分，未通过扣全部分
- 任务总分 = 6 维加权分数（模块覆盖率25% + 通过率25% + 断言密度15% + 事件链15% + 性能10% + 前端E2E10%）
- **门槛：≥ 95 分方可通过评审；< 95 分打回架构工程师重做**
- 评审工程师须给出：①总分 ②6 维分数明细 ③量化测试报告摘要 ④扣分理由 ⑤重做清单（若 < 95）

## 量化测试通过率门槛

- [x] 正测试通过率 ≥ 98%（每低 1% 扣 5 分）
- [x] 反测试通过率 ≥ 98%（每低 1% 扣 5 分）
- [x] 合测试通过率 ≥ 98%（每低 1% 扣 5 分）
- [x] 前端 E2E 通过率 ≥ 90%（每低 1% 扣 2 分）
- [x] 事件链顺序错误直接扣 10 分
- [x] 池状态断言错误直接扣 10 分
- [x] 元模式合并验证失败直接扣 15 分

## 阶段 1：metatest 基础设施

- [x] C1.1: `metatest/` 目录存在于项目根目录
- [x] C1.2: `metatest/__init__.py` 存在
- [x] C1.3: `metatest/conftest.py` 提供 8+ fixture：virtual_clock / fz_stocks / pool_engine / event_collector / pool_snapshot / fastapi_client / playwright_browser / config_store
- [x] C1.4: `metatest/fixtures/` 目录存在，含测试数据（池配置/公式/期望事件序列）
- [x] C1.5: `metatest/scoring.py` 实现 6 维评分引擎
- [x] C1.6: `metatest/runner.py` 运行后输出量化报告（6 维分数 + 总分 + 扣分项 + 重做清单）
- [x] C1.7: `metatest/README.md` 说明方法论 + 运行方式 + 评分规则

## 阶段 2：正测试集（17 个关键功能点）

### Task 2: 三模式切换

- [x] C2.1: `test_positive_three_modes.py` 验证仿真模式启动/暂停/步进/重置
- [x] C2.2: 验证回放模式 K线回放/步进/速度控制
- [x] C2.3: 验证实盘模式 tick 接入
- [x] C2.4: 验证模式切换时事件面板显隐（设计隐藏/仿真回放显示）
- [x] C2.5: 验证仿真与实盘同代码路径（G2 硬约束）
- [x] C2.6: 验证仿真模式 fz 前缀替换
- [x] C2.7: 验证 `normalizeToModeMs()` 时间戳归一化

### Task 3: 股票池设计器

- [x] C3.1: `test_positive_pool_designer.py` 验证 11 种节点类型创建/序列化
- [x] C3.2: 验证 2 种边类型创建/序列化
- [x] C3.3: 验证 7 种 Spec 创建/序列化
- [x] C3.4: 验证 `_FieldMeta` + `_FIELDS` 表驱动序列化往返
- [x] C3.5: 验证边条件配置在连接上（非节点内部）
- [x] C3.6: 验证边顺序号交集/差集运算次序
- [x] C3.7: 验证 `_EVALUATOR_REGISTRY` + `@register_evaluator` 6 个评估器分派

### Task 4: 事件引擎

- [x] C4.1: `test_positive_event_engine.py` 验证 EventBus publish/subscribe/subscribe_any
- [x] C4.2: 验证 `_events` 用 deque O(1) 丢弃
- [x] C4.3: 验证 EventDriver heapq 优先队列 + 中断暂停
- [x] C4.4: 验证 10 类事件按序（EdgeFired 先于 FormulaEvaluated）
- [x] C4.5: 验证运行时事件无序（execution_order 已删除）
- [x] C4.6: 验证 25+ 事件适配器表驱动记录

### Task 5: 公式计算

- [x] C5.1: `test_positive_formula.py` 验证 Python 引擎 eval/eval_outvars/eval_series/eval_batch
- [x] C5.2: 验证 HQChart 引擎协议分派（`_ENGINE_DISPATCH` 表）
- [x] C5.3: 验证 `IFormulaEngine` Protocol 4 类结构化匹配
- [x] C5.4: 验证三模式上下文（live/replay/simulation）
- [x] C5.5: 验证 LRU 缓存命中/未命中
- [x] C5.6: 验证禁止 cross 函数
- [x] C5.7: 验证公式与筛选严格分离

### Task 6: K 线合成

- [x] C6.1: `test_positive_kline.py` 验证 1min→5min/15min/30min/60min 合成
- [x] C6.2: 验证 60min→day 合成（time 重写为 00:00:00）
- [x] C6.3: 验证 day→week/month 合成（`_PERIOD_KEY_FUNCS` 表）
- [x] C6.4: 验证 `_SYNTHESIS_RULES` 表 10 个映射
- [x] C6.5: 验证 `synthesize(bars, source, target)` 单一入口
- [x] C6.6: 验证 `BarComposer.on_tick` 接受 `event_ts` 参数
- [x] C6.7: 验证 `publish_data_changed` 统一发布器
- [x] C6.8: 验证 `_publish_tick_batch` 批量发布

### Task 7: 交易执行

- [x] C7.1: `test_positive_trade.py` 验证 C 池入池立即市价买入 100 股
- [x] C7.2: 验证停留 20 分钟出池卖出
- [x] C7.3: 验证入池动作分发（声音/弹窗/TDX 板块/历史保存）
- [x] C7.4: 验证持仓管理（_Position 增减）
- [x] C7.5: 验证交易记录（_TradeRecord）

### Task 8: 导入/导出

- [x] C8.1: `test_positive_import_export.py` 验证 `import_pool(path, format)` 三格式
- [x] C8.2: 验证 `export_pool(config, path, format)` 三格式
- [x] C8.3: 验证 `_IMPORT_RULES` / `_EXPORT_RULES` 表 3 个条目
- [x] C8.4: 验证 DZH XML 解析（attrtext 6 种条目）
- [x] C8.5: 验证 TDX XML 解析
- [x] C8.6: 验证 attr 位标志解码/编码

### Task 9: 配置热加载

- [x] C9.1: `test_positive_hot_reload.py` 验证 `ConfigStore.get_table(name)` 加载
- [x] C9.2: 验证 `ConfigStore.get_data_file(name)` 加载非配置表
- [x] C9.3: 验证热加载 watchdog 监听
- [x] C9.4: 验证 `ConfigChanged` 事件发布（`_notify_changed` 单一方法）
- [x] C9.5: 验证版本历史/回滚/diff
- [x] C9.6: 验证禁止 `_load_json`/`_load_config`（Grep 0 匹配）

### Task 10: 事件面板

- [x] C10.1: `test_positive_event_panel.py` 验证默认隐藏（display:none）
- [x] C10.2: 验证仿真/回放模式 .visible 类显示
- [x] C10.3: 验证右下角固定（right:16px;bottom:16px;560×400px）
- [x] C10.4: 验证矩阵视图时间轴水平+红色 NOW 垂直线
- [x] C10.5: 验证散点同行/分类切换（cy=plotH/2）
- [x] C10.6: 验证 `_STYLE` 配置对象 + `_DRAW_LAYERS` 表 5 层
- [x] C10.7: 验证 `renderEventCanvas(ctx, state, layoutMode)` 单一函数
- [x] C10.8: 验证 emoji 字体
- [x] C10.9: 验证事件分类图标（tick=📡, edge=🔀, signal=🔔, system=⚙）
- [x] C10.10: 验证 `getTimerTriggerType()` + `TIMER_TRIGGER_TYPES` 表

### Task 11: HTTP API

- [x] C11.1: `test_positive_http_api.py` 验证 api.py 47+ 端点
- [x] C11.2: 验证 app.py 60+ 端点
- [x] C11.3: 验证 `require_config_store` Depends
- [x] C11.4: 验证 `get_simulator` Depends
- [x] C11.5: 验证 `_SIM_ACTIONS` 表 5 个 action
- [x] C11.6: 验证 `_TDX_ACTIONS` 表 4 个 method
- [x] C11.7: 验证 API Key 鉴权
- [x] C11.8: 验证 HTTP/WS 路由分离

### Task 12: WebSocket/SSE

- [x] C12.1: `test_positive_websocket.py` 验证 SSE 事件流订阅
- [x] C12.2: 验证 WebSocket 配置变更推送
- [x] C12.3: 验证会话隔离
- [x] C12.4: 验证重连机制（RECONNECT_DELAY=3000）
- [x] C12.5: 验证 MAX_EVENTS=2000 限流
- [x] C12.6: 验证 RENDER_THROTTLE=200 节流

### Task 13: 数据源

- [x] C13.1: `test_positive_data_source.py` 验证 MockProvider 确定性
- [x] C13.2: 验证 HQChartProvider 协议
- [x] C13.3: 验证 DataSourceContract 显式 mock 模式
- [x] C13.4: 验证 CandidatePoolResolver 备选池解析
- [x] C13.5: 验证 CandidatePoolRefreshManager 定时刷新
- [x] C13.6: 验证板块指数映射

### Task 14: 校验器

- [x] C14.1: `test_positive_validators.py` 验证 5 层校验
- [x] C14.2: 验证 TopologyPatternMatcher 拓扑模式匹配
- [x] C14.3: 验证 `should_fire` 时间触发判定
- [x] C14.4: 验证 `_get_table` 模块级帮助函数

### Task 15: 原生动作库

- [x] C15.1: `test_positive_native_actions.py` 验证 7 步表驱动执行
- [x] C15.2: 验证动作分派 dict（禁止 if/elif 链）
- [x] C15.3: 验证 nset/starttype/TTL/回调全查表
- [x] C15.4: 验证 Profit Analysis 评分排序

### Task 16: 存储层

- [x] C16.1: `test_positive_storage.py` 验证 SQLite 批量 executemany
- [x] C16.2: 验证连接复用
- [x] C16.3: 验证 WAL 模式
- [x] C16.4: 验证安全路径拼接

### Task 17: 备选池+池间转移

- [x] C17.1: `test_positive_pool_transfer.py` 验证备选池 100 只 fz 股票
- [x] C17.2: 验证备选池→A池（1分钟触发、5min KDJ 金叉、停留100分钟）
- [x] C17.3: 验证备选池→B池（10秒触发、1min MACD 金叉、停留200分钟）
- [x] C17.4: 验证 A+B→C池（5秒触发、交集）
- [x] C17.5: 验证 C池买入100股停留20分钟卖出

### Task 18: 迁移 Oracle

- [x] C18.1: `test_positive_migration_oracle.py` 验证 5 个 Oracle 场景
- [x] C18.2: 验证 oracle_conditional_transfer
- [x] C18.3: 验证 oracle_multi_level_cascade
- [x] C18.4: 验证 oracle_replay_mode_tick
- [x] C18.5: 验证 oracle_simple_candidate_to_state
- [x] C18.6: 验证 oracle_target_pool_action

## 阶段 3：反测试集

- [x] C19.1: `test_negative_empty_pool.py` 验证空备选池/空 tick 数据
- [x] C19.2: `test_negative_invalid_config.py` 验证缺字段/类型错误
- [x] C19.3: `test_negative_bad_topology.py` 验证自环/孤点/重复边
- [x] C20.1: `test_negative_duplicate_transfer.py` 验证重复入池
- [x] C20.2: `test_negative_ttl_no_position.py` 验证 TTL 到期无持仓
- [x] C20.3: `test_negative_formula_error.py` 验证除零/未定义变量/类型不匹配
- [x] C20.4: `test_negative_module_import.py` 验证跨模块非法引用
- [x] C21.1: `test_negative_http_404_500.py` 验证路由 404/405/500
- [x] C21.2: `test_negative_sse_disconnect.py` 验证 SSE 断连/重连
- [x] C21.3: `test_negative_websocket_error.py` 验证 WebSocket 消息格式错误
- [x] C21.4: `test_negative_config_missing.py` 验证配置文件缺失/格式错误
- [x] C21.5: `test_negative_frontend_xss.py` 验证前端 XSS 注入防护

## 阶段 4：合测试集

- [x] C22.1: `test_integration_sim_full_flow.py` 验证仿真全流程
- [x] C22.2: 验证事件计数断言
- [x] C22.3: 验证池状态快照断言
- [x] C22.4: 验证事件链顺序断言（10 类事件按序）
- [x] C23.1: `test_integration_three_modes.py` 验证三模式同代码路径
- [x] C23.2: 验证回放模式全流程
- [x] C23.3: 验证实盘模式全流程
- [x] C24.1: `test_integration_roundtrip.py` 验证 DZH→JSON→TDX→JSON→DZH
- [x] C24.2: 验证配置一致性断言
- [x] C25.1: `test_integration_hot_reload.py` 验证热加载端到端
- [x] C25.2: 验证热加载后行为一致性
- [x] C26.1: `test_integration_meta_pattern.py` 验证 7 项元模式合并
- [x] C26.2: 验证 `_step_once_impl(async_mode)` 同步/异步同代码路径
- [x] C26.3: 验证 `IFormulaEngine` Protocol + `_ENGINE_DISPATCH` 表
- [x] C26.4: 验证 `require_config_store` + `get_simulator` + `_SIM_ACTIONS` Depends
- [x] C26.5: 验证 `ConfigStore.get_table` / `get_data_file` 统一加载
- [x] C26.6: 验证 `synthesize` + `_SYNTHESIS_RULES` 表
- [x] C26.7: 验证 `import_pool` / `export_pool` + `_IMPORT_RULES` / `_EXPORT_RULES` 表
- [x] C26.8: 验证 `renderEventCanvas` + `_DRAW_LAYERS` + `_STYLE`
- [x] C27.1: `test_frontend_mode_switch.py` 验证三模式切换 UI
- [x] C27.2: `test_frontend_event_panel.py` 验证事件面板矩阵/散点视图
- [x] C27.3: `test_frontend_pool_designer.py` 验证池设计器节点/边操作
- [x] C27.4: `test_frontend_import_export.py` 验证导入导出 UI
- [x] C27.5: `test_frontend_formula.py` 验证公式管理 UI

## 阶段 5：量化评分与评审

- [x] C28.1: `scoring.py` 实现 6 维评分（模块覆盖率/通过率/断言密度/事件链/性能/前端E2E）
- [x] C28.2: `runner.py` 运行全部测试并输出量化报告
- [x] C28.3: 报告格式包含 6 维分数 + 总分 + 扣分项 + 重做清单
- [ ] C29.1: 运行 `python -m metatest.runner` 确认总分 ≥ 95
- [ ] C29.2: 验证 6 维分数均达标
- [ ] C29.3: 验证测试覆盖 17 个关键功能点
- [ ] C29.4: 验证正反合三层方法论完整
- [ ] C29.5: 验证元模式合并 7 项正确性

## 6 维评分标准

| 维度 | 权重 | 满分标准 | 0 分标准 |
|---|---|---|---|
| 模块覆盖率 | 25% | 17 个模块全覆盖 | 0 个模块覆盖 |
| 测试通过率 | 25% | 通过率 100% | 通过率 < 80% |
| 断言密度 | 15% | 断言数/文件数 ≥ 20 | 断言数/文件数 < 5 |
| 事件链完整性 | 15% | 10 类事件全出现且顺序正确 | 事件链缺失或顺序错 |
| 性能基准 | 10% | 1000 tick < 5s | 1000 tick > 30s |
| 前端 E2E 通过率 | 10% | 通过率 100% | 通过率 < 80% |

## 完成判定

- 所有 C1-C29 检查项必须全部勾选
- 6 维总分 ≥ 95 方可通过评审
- 任一项未通过则需修复后重新验证
- 全部通过后本次「metatest 全面正反合测试量化评审」spec 完成
