# Checklist

## 阶段 1：test_negative_invalid_config.py 补全

- [ ] C1.1: 读取现有 test_negative_invalid_config.py 与 conftest.py 确认已有用例
- [ ] C1.2: 新增 empty_pool 用例（空备选池 stocks=[]）
- [ ] C1.3: 新增 self_loop 用例（edge from==to）
- [ ] C1.4: 新增 orphan 孤点用例（节点无连边）
- [ ] C1.5: 新增 dup_edge 重复边用例（同 from/to/order）
- [ ] C1.6: 新增 invalid_params 用例（_order/starttype/cxtype 缺失或越界）
- [ ] C1.7: 新增 cycle 循环引用用例（A→B→A）
- [ ] C1.8: 所有新用例使用 pytest.raises 验证受控异常
- [ ] C1.9: 运行 python -m pytest metatest/test_negative_invalid_config.py -v 无 collection 错误

## 阶段 2：test_negative_runtime_errors.py

- [ ] C2.1: 读取 conftest.py 与 core/runtime_mode_module.py/execution_module.py/formula_module.py 实际 API
- [ ] C2.2: 用例 1 — 重复入池（同股票二次入池不重复计数）
- [ ] C2.3: 用例 2 — TTL 到期无持仓（TTL 触发但池中无该股票，优雅处理）
- [ ] C2.4: 用例 3 — 公式错误（除零/未定义变量/语法错误）
- [ ] C2.5: 用例 4 — 模块非法引用（跨模块直接 import 应被约束）
- [ ] C2.6: 用例 5 — 状态损坏（PoolState 内部 dict 被篡改后的校验）
- [ ] C2.7: 用例 6 — 并发访问（多线程操作 PoolState 不崩）
- [ ] C2.8: 异步用例使用 pytest.mark.asyncio
- [ ] C2.9: mock/patch 隔离依赖
- [ ] C2.10: 运行 python -m pytest metatest/test_negative_runtime_errors.py -v 无 collection 错误

## 阶段 3：test_negative_api_frontend.py

- [ ] C3.1: 读取 app.py / api.py 实际路由表与 Depends 配置
- [ ] C3.2: 用例 1 — 404 不存在路由
- [ ] C3.3: 用例 2 — 405 方法不允许
- [ ] C3.4: 用例 3 — 500 服务端异常（不泄漏堆栈）
- [ ] C3.5: 用例 4 — SSE 断连重连
- [ ] C3.6: 用例 5 — WebSocket 消息格式错误
- [ ] C3.7: 用例 6 — 配置缺失（ConfigStore.get_table 不存在表名）
- [ ] C3.8: 用例 7 — 前端 XSS 防护（escHtml 转义）
- [ ] C3.9: 用例 8 — 非法 JSON body（422/400）
- [ ] C3.10: 运行 python -m pytest metatest/test_negative_api_frontend.py -v 无 collection 错误

## 阶段 4：test_negative_logic_errors.py（新类别）

- [ ] C4.1: 读取 execution_module.py/event_bus.py/runtime_mode_module.py 实际逻辑约束
- [ ] C4.2: 用例 1 — 水位线哈希冲突（幂等性）
- [ ] C4.3: 用例 2 — 编译失败（CompiledPool 中途异常，部分结果不残留）
- [ ] C4.4: 用例 3 — 调用深度超三层（被拒或降级）
- [ ] C4.5: 用例 4 — 未注册角色（EventBus 订阅未知 event type 优雅返回）
- [ ] C4.6: 用例 5 — 解耦失败恢复（某模块订阅失败后其他模块仍正常）
- [ ] C4.7: 用例 6 — 传播未知模式（runtime_mode 非法 mode 字符串）
- [ ] C4.8: 用例 7 — 筛选 spec 畸形（filter spec 缺字段/类型错）
- [ ] C4.9: 运行 python -m pytest metatest/test_negative_logic_errors.py -v 无 collection 错误

## 阶段 5：运行验证与报告

- [ ] C5.1: 逐文件运行 pytest，收集 passed/failed/errors
- [ ] C5.2: 总通过率 >= 70%
- [ ] C5.3: 每个失败用例记录失败原因
- [ ] C5.4: 可快速修复的失败已修复（异常类型调整等）
- [ ] C5.5: 最终报告输出：文件路径 / 用例数 / 通过/失败 / 失败原因

## 实现规范

- [ ] N1: 使用 pytest 框架，pytest.raises 验证异常
- [ ] N2: 复用 conftest.py 中 fixture，不重复定义
- [ ] N3: 异步测试使用 pytest.mark.asyncio 装饰
- [ ] N4: 必要时使用 mock/patch 隔离依赖
- [ ] N5: 测试真实可运行（先 Read 实际代码 API 确保匹配）
- [ ] N6: 反测试验证优雅处理异常而非系统崩溃
- [ ] N7: 不删除旧 12 个分散文件，新 4 文件并行存在
- [ ] N8: 每文件至少 5-8 个测试用例

## 完成判定

- C1.1-C5.5 全部勾选
- 4 个文件均可独立运行（无 collection 错误）
- 总通过率 >= 70%
- 失败用例均有失败原因说明
