# BUG 跟踪文档 — simtests 执行套件

> 每发现一个 BUG 立即登记。状态：FIXED=已修复 / OPEN=待修复 / WONTFIX=不修复

| BUG-ID | 发现测试 | 描述 | 根因 | 修复文件 | 状态 |
|--------|----------|------|------|----------|------|
| BUG-001 | EV-005/006/009 | simulation/replay 模式下 BUY/SELL 信号永不发射 | `_setup_mode` 在 external_driver 模式下提前返回 mode_state，`run_mode` 不执行 `self._loop_pool_config = pool_config`，导致 `_emit_transfer_events` 读取 `self._loop_pool_config` 为 None | core/engine.py `_setup_mode` | FIXED |
| BUG-002 | EV-009 | BUY 信号 condition 字段期望 'pool_enter' 但实际为 flow_id | `condition` 字段是边/流条件标识（flow_id 或 edge label/accode），不是触发类型 `pool_enter` | simtests/test_08_events_signals.py | FIXED |
| BUG-003 | DI-003/DI-007 | TDX 候选池节点（type="7"）无法接收行情数据 | `_inject_bar_data` 只查 `dzh_type_map:type_map`（不含 TDX 类型 "7"），不查 `tdx_type_map`，导致 TDX 候选池节点无法匹配 `source_node_types` | core/engine.py `_inject_bar_data` + native/pipeline.py | FIXED |
| BUG-004 | EDGE-015/016/022, FLOW-008, TTL-011, E2E-007 | `_inject_bar_data` 用 bar_data codes 替换整个股票列表，导致不在 bar_data 中的股票丢失 | 原代码用 bar_data codes 替换整个列表，simulation 模式的 mock bar_data 只采样 60 个 codes，导致 5000 股票池只剩 60 | core/engine.py `_inject_bar_data` | FIXED |
| BUG-005 | FLOW-008 (全量运行时) | 多级池 state_B 为空，期望 5 只股票 | `_group_transformation_units` 使用 `set(...)` 迭代，顺序非确定性（依赖 PYTHONHASHSEED）。serial 策略下不排序，导致 state_A→condition_B→state_B 可能在 candidate_1→condition_A→state_A 之前执行 | core/engine.py `_execute_flowsCore` | FIXED |
| BUG-006 | COND-007 | nset=4 noperate=3 上穿静默退化为 >= | noperate=3 的 cross_above 逻辑在无历史数据时退化为简单比较 | core/evaluators.py | OPEN |
| BUG-007 | COND-008 | nset=4 noperate=4 下破被 rank_mode 劫持 | noperate=4 的 cross_below 逻辑被 rank_mode 分支错误匹配 | core/evaluators.py | OPEN |
| BUG-008 | COND-011 | nset=4 noperate=8 上拐标量模式无映射 | noperate=8 的 turn_up 在标量模式下无对应评估路径 | core/evaluators.py | OPEN |

## 修复摘要

### BUG-001: _loop_pool_config 未在 external_driver 模式下设置
- **影响**: simulation/replay 模式下所有 BUY/SELL 信号不发射
- **修复**: 在 `_setup_mode` 中统一设置 `self._loop_pool_config = pool_config`，确保所有模式都正确初始化

### BUG-003: _inject_bar_data TDX 类型不匹配
- **影响**: TDX 候选池节点（type="7"）无法接收行情数据，所有依赖行情的条件评估失败
- **修复**: 在 `_inject_bar_data` 和 `async_multi_tf_injector` 中添加 `tdx_type_map` 查询，与 `_init_node_stocks` 的类型解析保持一致

### BUG-004: _inject_bar_data 替换列表导致股票丢失
- **影响**: simulation 模式下 5000 股票池只剩 60 只（mock bar_data 采样数）
- **修复**: 改为只更新已有股票的 bar 字段，不添加新股票、不删除已有股票

### BUG-005: 变换单元顺序非确定性
- **影响**: 多级池在全量测试运行时随机失败（依赖 PYTHONHASHSEED）
- **修复**: 所有执行策略都按拓扑深度排序变换单元，确保上游单元先于下游单元执行

## 回退清理

| 清理项 | 原状态 | 清理后 | 验证 |
|--------|--------|--------|------|
| `_random_filter` | 已从代码删除，仅文档残留 | 代码中 0 处 | `grep -r "_random_filter" meta_core/ --include="*.py"` 返回 0 行 |
| `pass_through (silent)` | 代码中 0 处 | 代码中 0 处 | `assert_no_silent_pass_through` 通过 |
| `except.*: pass` (native/) | 6 处 | 0 处 | 全部升级为 `except Exception as e: logger.warning/debug(...)` |
| `except.*: pass` (core/) | 3 处 | 1 处（asyncio.CancelledError 正确模式） | 2 处升级为 debug 日志，1 处保留 |
