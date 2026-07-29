# Tasks

## 阶段 1：补全 test_negative_invalid_config.py（已存在）

- [ ] Task 1: 补齐异常配置反测试缺失用例
  - [ ] SubTask 1.1: 读取现有 test_negative_invalid_config.py 与 conftest.py，确认已有用例
  - [ ] SubTask 1.2: 新增 empty_pool 边界用例（空备选池 stocks=[]）
  - [ ] SubTask 1.3: 新增 self_loop 边界用例（edge from==to）
  - [ ] SubTask 1.4: 新增 orphan 孤点用例（节点无连边）
  - [ ] SubTask 1.5: 新增 dup_edge 重复边用例（同 from/to/order）
  - [ ] SubTask 1.6: 新增 invalid_params 非法边参数用例（_order/starttype/cxtype 缺失或越界）
  - [ ] SubTask 1.7: 新增 cycle 循环引用用例（A→B→A）
  - [ ] SubTask 1.8: 用 pytest.raises 验证 Compiler.compile 抛受控异常；运行确认无 collection 错误

## 阶段 2：创建 test_negative_runtime_errors.py

- [ ] Task 2: 运行时异常反测试整合文件
  - [ ] SubTask 2.1: 读取 conftest.py 与 core/runtime_mode_module.py、execution_module.py、formula_module.py 实际 API
  - [ ] SubTask 2.2: 用例 1 — 重复入池（同股票二次 add_to_pool 不重复计数）
  - [ ] SubTask 2.3: 用例 2 — TTL 到期无持仓（TTL 触发但池中无该股票，优雅处理）
  - [ ] SubTask 2.4: 用例 3 — 公式错误（除零/未定义变量/语法错误，PythonFormulaEngine 返回 None 或抛受控异常）
  - [ ] SubTask 2.5: 用例 4 — 模块非法引用（跨模块直接 import 应被约束/隔离）
  - [ ] SubTask 2.6: 用例 5 — 状态损坏（PoolState 内部 dict 被外部篡改后的恢复/校验）
  - [ ] SubTask 2.7: 用例 6 — 并发访问（多线程/协程同时操作 PoolState 不崩）
  - [ ] SubTask 2.8: 异步用例使用 pytest.mark.asyncio；mock/patch 隔离依赖；运行确认可执行

## 阶段 3：创建 test_negative_api_frontend.py

- [ ] Task 3: API/前端反测试整合文件
  - [ ] SubTask 3.1: 读取 app.py / api.py 实际路由表与 Depends 配置
  - [ ] SubTask 3.2: 用例 1 — 404 不存在路由（GET /api/nonexistent）
  - [ ] SubTask 3.3: 用例 2 — 405 方法不允许（POST 访问 GET-only 路由）
  - [ ] SubTask 3.4: 用例 3 — 500 服务端异常（patch 内部抛错，验证不泄漏堆栈到响应体）
  - [ ] SubTask 3.5: 用例 4 — SSE 断连重连（fastapi_client 流式中断后重连状态正确）
  - [ ] SubTask 3.6: 用例 5 — WebSocket 消息格式错误（发送非 JSON / 缺字段）
  - [ ] SubTask 3.7: 用例 6 — 配置缺失（ConfigStore.get_table 不存在表名，返回空/默认值不崩）
  - [ ] SubTask 3.8: 用例 7 — 前端 XSS 防护（池名/节点名含 <script>，escHtml 转义验证）
  - [ ] SubTask 3.9: 用例 8 — 非法 JSON body（POST body 非 JSON 触发 422/400）
  - [ ] SubTask 3.10: 运行确认可执行

## 阶段 4：创建 test_negative_logic_errors.py（新类别）

- [ ] Task 4: 底层逻辑反测试新类别文件
  - [ ] SubTask 4.1: 读取 execution_module.py（Compiler/CompiledPool）、event_bus.py、runtime_mode_module.py 实际逻辑约束
  - [ ] SubTask 4.2: 用例 1 — 水位线哈希冲突（同 hash 不同事件，幂等性验证）
  - [ ] SubTask 4.3: 用例 2 — 编译失败（CompiledPool 编译中途异常，部分结果不残留）
  - [ ] SubTask 4.4: 用例 3 — 调用深度超三层（模块链 A→B→C→D 被拒或降级）
  - [ ] SubTask 4.5: 用例 4 — 未注册角色（EventBus 订阅未知 event type 优雅返回）
  - [ ] SubTask 4.6: 用例 5 — 解耦失败恢复（某模块订阅失败后其他模块仍正常）
  - [ ] SubTask 4.7: 用例 6 — 传播未知模式（runtime_mode 传入非法 mode 字符串，拒绝或回退默认）
  - [ ] SubTask 4.8: 用例 7 — 筛选 spec 畸形（filter spec 缺字段/类型错，受控异常）
  - [ ] SubTask 4.9: 运行确认可执行

## 阶段 5：运行验证与报告

- [ ] Task 5: 运行 4 个文件并生成报告
  - [ ] SubTask 5.1: 逐文件运行 pytest，收集 passed/failed/errors
  - [ ] SubTask 5.2: 统计总通过率，确认 >= 70%
  - [ ] SubTask 5.3: 记录每个失败用例的失败原因（fixture 缺失/API 不匹配/期望异常类型不符）
  - [ ] SubTask 5.4: 修复可快速修复的失败（如异常类型从 ValueError 调整为 KeyError）
  - [ ] SubTask 5.5: 输出最终报告：文件路径 / 用例数 / 通过/失败 / 失败原因

# Task Dependencies

- Task 2/3/4 互相独立，可并行
- Task 1 与 Task 2/3/4 独立，可并行
- Task 5 依赖 Task 1-4 全部完成
