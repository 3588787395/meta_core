"""eventtest —— 条件节点拓扑重构后的严格正反合测试包。

替代旧 ``tests/`` 目录，以正测试 / 反测试 / 合测试三层方法论建立量化回归基线，
评审以 ``python -m eventtest.run_eventtest`` 输出的量化指标为唯一依据。

本包不修改 ``core/`` 任何源文件，复用 PoolEngine / EventBus / PoolState /
StatePoolView / EventDriver / MockDataSource 等现有类，禁止兼容已删除的旧接口
（``get_node_stocks`` / ``execution_order`` / ``EdgeFired.changed_codes``）。
"""
