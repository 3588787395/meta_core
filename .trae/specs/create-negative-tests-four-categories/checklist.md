# Checklist

## 阶段 1：test_negative_invalid_config.py 补全（8 类 v3 边界用例）

- [x] C1.1: 读取现有 test_negative_invalid_config.py 与 conftest.py 确认已有用例
- [x] C1.2: 新增 empty_pool 用例（空备选池 stocks=[]）
- [x] C1.3: 新增 self_loop 用例（edge from==to）
- [x] C1.4: 新增 orphan 孤点用例（节点无连边）
- [x] C1.5: 新增 dup_edge 重复边用例（同 from/to/order）
- [x] C1.6: 新增 invalid_params 用例（_order 负数）
- [x] C1.7: 新增 cycle 循环引用用例（A→B→A）
- [x] C1.8: 新增 missing_node 缺失节点引用用例
- [x] C1.9: 新增 invalid_type 无效节点类型用例
- [x] C1.10: 所有新用例使用 try-except / pytest.raises 验证受控异常
- [x] C1.11: 运行 python -m pytest metatest/test_negative_invalid_config.py -v 无 collection 错误

## 阶段 2：test_negative_runtime_errors.py（8 用例）

- [x] C2.1: 读取 conftest.py 与 core/runtime_mode_module.py/execution_module.py/formula_module.py 实际 API
- [x] C2.2: 用例 1 — 重复入池（同股票二次入池不重复计数）
- [x] C2.3: 用例 2 — TTL 到期无持仓（TTL 触发但池中无该股票，优雅处理）
- [x] C2.4: 用例 3 — 公式错误（除零/未定义变量/语法错误，记录 WARNING）
- [x] C2.5: 用例 4 — 模块非法引用（跨模块直接 import 应被约束）
- [x] C2.6: 用例 5 — 状态损坏（PoolState.node_stocks=None 后 _populate_tables 恢复）
- [x] C2.7: 用例 6 — 并发访问（多线程操作 PoolState 不崩）
- [x] C2.8: 用例 7 — 无效股票代码（None/空串/整数/缺字段优雅归一化）
- [x] C2.9: 用例 8 — K 线历史溢出被 _BARS_HISTORY_MAXLEN 裁剪
- [x] C2.10: 运行 python -m pytest metatest/test_negative_runtime_errors.py -v 无 collection 错误

## 阶段 3：test_negative_api_frontend.py（8 用例）

- [x] C3.1: 读取 app.py / api.py 实际路由表与 Depends 配置
- [x] C3.2: 用例 1 — 404 不存在路由
- [x] C3.3: 用例 2 — 405 方法不允许
- [x] C3.4: 用例 3 — 500 服务端异常（不泄漏堆栈）
- [x] C3.5: 用例 4 — SSE 断连重连
- [x] C3.6: 用例 5 — WebSocket 消息格式错误
- [x] C3.7: 用例 6 — 配置缺失（ConfigStore.get_table 不存在表名）
- [x] C3.8: 用例 7 — 前端 XSS 防护（escapeHtml 转义）
- [x] C3.9: 用例 8 — 非法 JSON body（422/400）
- [x] C3.10: pytest.importorskip 兼容 FastAPI/httpx 缺失
- [x] C3.11: 运行 python -m pytest metatest/test_negative_api_frontend.py -v 无 collection 错误

## 阶段 4：test_negative_logic_errors.py（9 逻辑 + 15 同构复活 = 24 用例）

- [x] C4.1: 读取 execution_module.py/event_bus.py/runtime_mode_module.py 实际逻辑约束
- [x] C4.2: 逻辑 1 — 水位线哈希无碰撞（sha256 确定性 + 区分性）
- [x] C4.3: 逻辑 2 — 编译失败（compile 对 None/非 dict 抛受控异常）
- [x] C4.4: 逻辑 3 — 调用深度 ≤ 3 层（ast 检查 trigger_check/filter_eval/propagate_apply）
- [x] C4.5: 逻辑 4 — 未注册角色（_ROLE_ACTIONS.get 返回默认空动作）
- [x] C4.6: 逻辑 5 — 解耦失败恢复（SignalDeriver 异常不影响 ActionDispatcher）
- [x] C4.7: 逻辑 6 — 传播未知 mode（回退默认 copy 行为）
- [x] C4.8: 逻辑 7 — 畸形 filter_spec 默认全部通过
- [x] C4.9: 逻辑 8 — 规则 87 ConfigStore 绕过检测
- [x] C4.10: 逻辑 9 — 规则 59 表驱动绕过检测

### 15 项同构复活检测（变更 A-O，Grep 断言零匹配）

- [x] C4.11: 变更 A — screening_module.py 无 _filter_condition_formula 等 nset 同构函数（_NSET_FILTER_HANDLERS 表存在）
- [x] C4.12: 变更 B — monitoring_module.py 无 5 个 PnL 同构函数（_compute_intraday_pnl 等）
- [x] C4.13: 变更 C — import_export_module.py 无 _parse_dzh/_parse_tdx/_serialize_* 同构函数
- [x] C4.14: 变更 D — formula_module.py _eval_formula/_eval_formula_series 薄包装 ≤ 8 行（_eval_formula_core 存在）
- [x] C4.15: 变更 E — trade_module.py _apply_tradeattr 无 if side=="BUY"/elif SELL 硬编码（_TRADEATTR_FIELD_MAP 表存在）
- [x] C4.16: 变更 F — execution_module.py _build_filter_spec 使用 _FILTER_SPEC_BUILDERS 表驱动（无 4 路 elif）
- [x] C4.17: 变更 G — core/*.py 无 json.load(open()) inline（ConfigStore/table_engine 内部除外）
- [x] C4.18: 变更 H — execution_module.py 无 if mode=="inflection"/"rank" 硬编码
- [x] C4.19: 变更 I — runtime_mode_module.py 无 if self._base_period== 硬编码
- [x] C4.20: 变更 J — runtime_mode_module.py _run_coro_sync 仅模块级存在（类内零定义）
- [x] C4.21: 变更 K — engine.py/runtime_mode_module.py _build_topology 委托 _build_adjacency（方法体薄）
- [x] C4.22: 变更 L — monitoring_module.py 无 _momentum_key/_trend_key/_value_key 同构函数
- [x] C4.23: 变更 M — execution_module.py _apply_stock_filters 仅在 _with_stock_filters 包装器内（evaluator 体内零调用）
- [x] C4.24: 变更 N — 5 核心模块 @_event_handler 装饰共 ≥ 28 次（每模块 ≥ 1）
- [x] C4.25: 变更 O — table_engine.py _validate_table 使用 _iter_entries（表驱动按 type 分派）

- [x] C4.26: Grep 断言使用 re 模块（re.findall/re.search），无 subprocess
- [x] C4.27: 运行 python -m pytest metatest/test_negative_logic_errors.py -v 无 collection 错误

## 阶段 5：运行验证与报告

- [x] C5.1: 逐文件运行 pytest，收集 passed/failed/skipped/errors
- [x] C5.2: 断言密度每文件 ≥ 20
- [x] C5.3: 每个失败用例记录失败原因
- [x] C5.4: 可快速修复的失败已修复
- [x] C5.5: 最终报告输出：文件路径 / 用例数 / 断言数 / 通过/失败/skip / 15 同构复活结果

## 实现规范

- [x] N1: 使用 pytest 框架，pytest.raises / try-except 验证异常
- [x] N2: 复用 conftest.py 中 fixture，不重复定义
- [x] N3: 必要时使用 mock/patch 隔离依赖
- [x] N4: 测试真实可运行（先 Read 实际代码 API 确保匹配）
- [x] N5: 反测试验证优雅处理异常而非系统崩溃
- [x] N6: 不删除旧 14 个分散文件，新 4 文件并行存在
- [x] N7: 每文件至少 8 个测试用例
- [x] N8: 每文件断言密度 ≥ 20
- [x] N9: Grep 断言使用 re 模块（re.findall/re.search），不使用 subprocess

## 完成判定

- [x] C1.1-C5.5 全部勾选
- [x] 4 个文件均可独立运行（无 collection 错误）
- [x] 每文件用例数 ≥ 8
- [x] 每文件断言密度 ≥ 20
- [x] 15 项同构复活检测全部通过（零匹配）
