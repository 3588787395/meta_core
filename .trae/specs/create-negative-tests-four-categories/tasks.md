# Tasks

## 阶段 1：补全 test_negative_invalid_config.py（8 类 v3 边界用例）

- [x] Task 1: 补齐异常配置反测试缺失用例（≥ 8 用例）
  - [x] SubTask 1.1: 读取现有 test_negative_invalid_config.py 与 conftest.py，确认已有用例
  - [x] SubTask 1.2: 新增 empty_pool 边界用例（空备选池 stocks=[]）
  - [x] SubTask 1.3: 新增 self_loop 边界用例（edge from==to）
  - [x] SubTask 1.4: 新增 orphan 孤点用例（节点无连边）
  - [x] SubTask 1.5: 新增 dup_edge 重复边用例（同 from/to/order）
  - [x] SubTask 1.6: 新增 invalid_params 非法边参数用例（_order 负数）
  - [x] SubTask 1.7: 新增 cycle 循环引用用例（A→B→A）
  - [x] SubTask 1.8: 新增 missing_node 缺失节点引用用例
  - [x] SubTask 1.9: 新增 invalid_type 无效节点类型用例
  - [x] SubTask 1.10: 用 pytest.raises / try-except 验证 Compiler.compile 抛受控异常；运行确认无 collection 错误

## 阶段 2：创建 test_negative_runtime_errors.py（8+ 用例）

- [x] Task 2: 运行时异常反测试整合文件（8 用例）
  - [x] SubTask 2.1: 读取 conftest.py 与 core/runtime_mode_module.py、execution_module.py、formula_module.py 实际 API
  - [x] SubTask 2.2: 用例 1 — 重复入池（同股票二次 add_to_pool 不重复计数）
  - [x] SubTask 2.3: 用例 2 — TTL 到期无持仓（TTL 触发但池中无该股票，优雅处理）
  - [x] SubTask 2.4: 用例 3 — 公式错误（除零/未定义变量/语法错误，PythonFormulaEngine 捕获并记录 WARNING）
  - [x] SubTask 2.5: 用例 4 — 模块非法引用（跨模块直接 import 应被约束/隔离）
  - [x] SubTask 2.6: 用例 5 — 状态损坏（PoolState.node_stocks=None 后 _populate_tables 恢复）
  - [x] SubTask 2.7: 用例 6 — 并发访问（多线程操作 PoolState 不崩）
  - [x] SubTask 2.8: 用例 7 — 无效股票代码（None/空串/整数/缺字段优雅归一化）
  - [x] SubTask 2.9: 用例 8 — K 线历史溢出被 _BARS_HISTORY_MAXLEN 裁剪
  - [x] SubTask 2.10: 运行确认无 collection 错误

## 阶段 3：创建 test_negative_api_frontend.py（8+ 用例）

- [x] Task 3: API/前端反测试整合文件（8 用例）
  - [x] SubTask 3.1: 读取 app.py / api.py 实际路由表与 Depends 配置
  - [x] SubTask 3.2: 用例 1 — 404 不存在路由（GET /api/nonexistent）
  - [x] SubTask 3.3: 用例 2 — 405 方法不允许（POST 访问 GET-only 路由）
  - [x] SubTask 3.4: 用例 3 — 500 服务端异常（不泄漏堆栈到响应体）
  - [x] SubTask 3.5: 用例 4 — SSE 断连重连（流式中断后重连状态正确）
  - [x] SubTask 3.6: 用例 5 — WebSocket 消息格式错误（发送非 JSON / 缺字段）
  - [x] SubTask 3.7: 用例 6 — 配置缺失（ConfigStore.get_table 不存在表名返回 None/默认）
  - [x] SubTask 3.8: 用例 7 — 前端 XSS 防护（escapeHtml 中和 <script> 标签）
  - [x] SubTask 3.9: 用例 8 — 非法 JSON body（POST body 非 JSON 触发 422/400）
  - [x] SubTask 3.10: pytest.importorskip 兼容 FastAPI/httpx 缺失；运行确认可执行

## 阶段 4：创建 test_negative_logic_errors.py（9 逻辑用例 + 15 同构复活检测）

- [x] Task 4: 底层逻辑反测试新类别文件（9 逻辑 + 15 同构复活 = 24 用例）
  - [x] SubTask 4.1: 读取 execution_module.py / event_bus.py / runtime_mode_module.py 实际逻辑约束
  - [x] SubTask 4.2: 逻辑 1 — 水位线哈希无碰撞（sha256 不同数据不同 hash）
  - [x] SubTask 4.3: 逻辑 2 — 编译失败（compile 对 None/非 dict 抛受控异常）
  - [x] SubTask 4.4: 逻辑 3 — 调用深度 ≤ 3 层（ast 检查 trigger_check/filter_eval/propagate_apply）
  - [x] SubTask 4.5: 逻辑 4 — 未注册角色（_ROLE_ACTIONS.get 返回默认空动作）
  - [x] SubTask 4.6: 逻辑 5 — 解耦失败恢复（SignalDeriver 异常不影响 ActionDispatcher）
  - [x] SubTask 4.7: 逻辑 6 — 传播未知 mode（回退默认 copy 行为）
  - [x] SubTask 4.8: 逻辑 7 — 畸形 filter_spec 默认全部通过
  - [x] SubTask 4.9: 逻辑 8 — 规则 87 ConfigStore 绕过检测
  - [x] SubTask 4.10: 逻辑 9 — 规则 59 表驱动绕过检测
  - [x] SubTask 4.11: 同构复活 A — screening_module.py nset 筛选函数零匹配
  - [x] SubTask 4.12: 同构复活 B — monitoring_module.py 5 个 PnL 同构函数零匹配
  - [x] SubTask 4.13: 同构复活 C — import_export_module.py parse/serialize 同构零匹配
  - [x] SubTask 4.14: 同构复活 D — formula_module.py _eval_formula 薄包装 ≤ 8 行
  - [x] SubTask 4.15: 同构复活 E — trade_module.py _apply_tradeattr 无 BUY/SELL 硬编码
  - [x] SubTask 4.16: 同构复活 F — execution_module.py _build_filter_spec 表驱动
  - [x] SubTask 4.17: 同构复活 G — core/*.py 无 json.load(open()) inline
  - [x] SubTask 4.18: 同构复活 H — execution_module.py 无 if mode==inflection/rank
  - [x] SubTask 4.19: 同构复活 I — runtime_mode_module.py 无 if self._base_period==
  - [x] SubTask 4.20: 同构复活 J — runtime_mode_module.py _run_coro_sync 仅模块级
  - [x] SubTask 4.21: 同构复活 K — engine/runtime_mode _build_topology 委托 _build_adjacency
  - [x] SubTask 4.22: 同构复活 L — monitoring_module.py 无 _momentum/_trend/_value_key
  - [x] SubTask 4.23: 同构复活 M — execution_module.py _apply_stock_filters 仅在包装器内
  - [x] SubTask 4.24: 同构复活 N — 5 模块 @_event_handler 共 ≥ 28 次
  - [x] SubTask 4.25: 同构复活 O — table_engine.py _validate_table 使用 _iter_entries
  - [x] SubTask 4.26: 使用 re 模块（re.findall/re.search）实现 Grep 断言，无 subprocess
  - [x] SubTask 4.27: 运行确认无 collection 错误

## 阶段 5：运行验证与报告

- [x] Task 5: 运行 4 个文件并生成报告
  - [x] SubTask 5.1: 逐文件运行 pytest，收集 passed/failed/skipped/errors
  - [x] SubTask 5.2: 统计断言密度，确认每文件 ≥ 20 断言
  - [x] SubTask 5.3: 记录每个失败用例的失败原因
  - [x] SubTask 5.4: 修复可快速修复的失败（异常类型调整等）
  - [x] SubTask 5.5: 输出最终报告：文件路径 / 用例数 / 断言数 / 通过/失败/skip / 15 同构复活结果

# Task Dependencies

- Task 2/3/4 互相独立，可并行
- Task 1 与 Task 2/3/4 独立，可并行
- Task 5 依赖 Task 1-4 全部完成
