# eventtest —— 条件节点拓扑重构后的严格正反合测试

## 目录用途

`eventtest/` 替代旧 `tests/` 目录，为条件节点拓扑重构（G1–G6 架构落地）后的真实行为
建立**量化回归基线**。旧 `tests/` 目录针对已删除接口编写，无法捕获运行时 bug，已冻结保留
（不删除），新评审一律以 `eventtest/` 输出的量化指标为唯一依据。

本目录不修改 `core/` 任何源文件，复用现有类：
`PoolEngine` / `EventBus` / `PoolState` / `StatePoolView` / `EventDriver` /
`EdgeExecutor` / `CompiledSchedule` / `MockDataSource` / `FormulaEngine` 等。

## 正反合测试方法论

| 层级 | 命名前缀 | 验证目标 |
| --- | --- | --- |
| 正测试 | `test_positive_*.py` | 验证正常路径——MockDataSource tick 生成、EventDriver 单 heapq 优先队列中断驱动、StatePoolView 视图脏股票、EdgeFired 无 changed_codes、条件节点激活与公式筛选、集合运算交集/差集/并集、交易事件链、TTL 一次性触发 |
| 反测试 | `test_negative_*.py` | 验证异常与边界——空备选池、无效条件节点配置、坏边拓扑（自环/孤点）、重复入池、TTL 到期无持仓、公式计算异常返回空、空 dirty_codes 兜底、跨模块非法 import |
| 合测试 | `test_integration_*.py` | 验证端到端集成——仿真模式全流程（source→cond1→pool_A、source→cond2→pool_B、pool_A+pool_B→cond3→pool_C→买入→TTL→卖出），断言事件计数、池状态快照、事件链顺序 |

- **正测试**验证正常路径，每条断言基于实际运行结果。
- **反测试**验证异常路径与边界条件，预期系统优雅降级而非崩溃。
- **合测试**验证仿真模式端到端集成，断言 11 类事件计数 ≥ 1 与事件链顺序。

## 运行方式

```bash
python -m eventtest.run_eventtest
```

运行后输出量化报告，包含：测试总数 / 通过数 / 失败数 / 通过率 / 总耗时 /
事件计数表（按 EventType 分组）/ 池状态快照表 / 退出码。

- 退出码 `0` = 全部通过（或无测试文件）
- 退出码 `1` = 有失败

## 评审标准

评审工程师**必须运行** `python -m eventtest.run_eventtest`，以输出报告中的量化指标打分：

- 正测试通过率 ≥ 98% 得满分，每低 1% 扣 5 分
- 反测试通过率 ≥ 98% 得满分，每低 1% 扣 5 分
- 合测试通过率 ≥ 98% 得满分，每低 1% 扣 5 分
- 事件链顺序错误直接扣 10 分
- 池状态断言错误直接扣 10 分
- **门槛：≥ 98 分**方可进入下一任务

## 禁止兼容旧接口

以下接口已删除，测试中**禁止**出现引用，每发现 1 处扣 5 分：

- `get_node_stocks` / `set_node_stocks` / `add_node_stocks` / `remove_node_stocks`（扁平接口）
- `execution_order`（运行时拓扑排序）
- `EdgeFired.changed_codes`（G3 后 EdgeFired 只含 `eid` + `ts`）
- `EventDriver.fire_due` 线性扫描、`at_fn` / `fire_ttl_due` / `is_edge_due` / `TtlTracker` 残留

## 共享 fixture（conftest.py）

| Fixture | 说明 |
| --- | --- |
| `virtual_clock` | 虚拟时钟对象，起点 `34500.0`（=09:30:00），提供 `advance(seconds)` |
| `fz_stocks` | 工厂：`fz_stocks(n=100)` 从 `config/pools/sim_test_pool_100.json` 读取 N 只 fz 股票代码 |
| `pool_engine` | 工厂：`pool_engine(pool_config_path=...)` 装配并返回 `PoolEngine` 实例 |
| `event_collector` | 工厂：`event_collector(bus)` 返回 `EventCollector`，订阅 EventBus 全部事件 |
| `pool_snapshot` | 工厂：`pool_snapshot(engine)` 返回 `Dict[str, List[str]]` 各池股票代码 |
| `report_state` | 暴露共享报告状态，供合测试填充事件计数与池快照 |
