# 股票池实时事件驱动架构修正 - Implementation Plan

## [ ] Task 1: DirtyState精简 + add_node_exited_code
- **Priority**: high
- **Depends On**: None
- **Description**:
  - 确认DirtyState使用全局`changed_codes: Set[str]`（不按周期分）
  - 确认/新增`node_exited_codes: Dict[str, Set[str]]`字段和`add_node_exited_code(nid, code)`方法
  - 确认`add_changed_codes(codes)`、`add_node_entered_code(nid, code)`、`mark_node_dirty(nid)`、`mark_data_dirty()`、`clear_dirty()`正确
  - clear_dirty()清空nodes/data/changed_codes/node_entered_codes/node_exited_codes
  - 移除period_codes字段（如存在）
  - 涉及文件：[runtime_mode_module.py](file:///h:/new_tdx_mock/PYPlugins/meta_core/core/runtime_mode_module.py) DirtyState定义
- **Acceptance Criteria Addressed**: AC-3, FR-1
- **Test Requirements**:
  - `programmatic` TR-1.1: add_changed_codes合并去重
  - `programmatic` TR-1.2: add_node_entered_code/add_node_exited_code正确记录
  - `programmatic` TR-1.3: clear_dirty清空所有
  - `programmatic` TR-1.4: 不存在period_codes字段

## [ ] Task 2: DataUpdater正确记录changed_codes，BarComposer不添加
- **Priority**: high
- **Depends On**: Task 1
- **Description**:
  - DataUpdater.apply_data: tick推进后调用state.add_changed_codes(advanced_codes)
  - _publish_bar_changed: 移除state.add_changed_codes/state.add_period_codes调用（BarComposer不向脏集合添加东西）
  - DataChanged事件(tick和bar)的codes保持批量列表
  - 涉及文件：[tick_bar_module.py](file:///h:/new_tdx_mock/PYPlugins/meta_core/core/tick_bar_module.py)
- **Acceptance Criteria Addressed**: AC-3, FR-2
- **Test Requirements**:
  - `programmatic` TR-2.1: apply_data后changed_codes含advanced_codes
  - `programmatic` TR-2.2: BarComposer.on_tick后changed_codes不含额外股票（由tick已加入）

## [ ] Task 3: 公式K线获取包含未闭合K线（live和simulation一致）
- **Priority**: high
- **Depends On**: Task 1
- **Description**:
  - 修改`_get_period_bars`函数：使其返回bars_history（闭合K线）末尾追加当前bars[period][code]（未闭合K线）
  - 或者：统一使用bars_history_getter方式获取K线（该getter已包含current bar）
  - 确保live_context和simulation mode下，公式引擎通过data_fetcher获取的K线DataFrame都包含：闭合历史K线 + 当前未闭合K线（最新价）
  - 移除_get_period_bars中"仅返回已闭合K线"的注释和逻辑
  - 验证bars_history_getter正确将current bar追加到末尾，字段(time/open/high/low/close/volume)完整
  - 涉及文件：[formula_module.py](file:///h:/new_tdx_mock/PYPlugins/meta_core/core/formula_module.py) _get_period_bars/live_context, [tick_bar_module.py](file:///h:/new_tdx_mock/PYPlugins/meta_core/core/tick_bar_module.py) make_bars_history_getter
- **Acceptance Criteria Addressed**: AC-1, AC-2, FR-3, FR-9
- **Test Requirements**:
  - `programmatic` TR-3.1: _get_period_bars返回含current bar的数据
  - `programmatic` TR-3.2: live mode下公式引擎获取的DataFrame末尾是当前未闭合K线
  - `programmatic` TR-3.3: simulation mode下bars_history_getter同样包含current bar
  - `programmatic` TR-3.4: current bar的close等于latest_tick价格

## [ ] Task 4: 移除/修正FormulaEngine的bar_hash缓存
- **Priority**: high
- **Depends On**: Task 3
- **Description**:
  - 修改_cached_eval：缓存key不能只用全局bar_hash（否则同tick不同codes调用返回错误结果）
  - 最简方案：缓存key加入frozenset(codes)，即key=("formula", ctx.mode, spec.formula_ref, frozenset(codes))
  - 或更简方案：eval路径直接调用_eval_formula，跳过_cached_eval（filter_inputs已做结果缓存，公式层缓存不必要）
  - 确保每次公式调用都取最新K线数据计算，不返回过期结果
  - 保留writeback=True（写入tick[spec.formula_ref]=value）
  - 涉及文件：[formula_module.py](file:///h:/new_tdx_mock/PYPlugins/meta_core/core/formula_module.py) _cached_eval/eval
- **Acceptance Criteria Addressed**: AC-6, FR-4
- **Test Requirements**:
  - `programmatic` TR-4.1: 同tick内对不同codes调用eval返回各自正确结果
  - `programmatic` TR-4.2: tick变化后重新计算，不返回旧缓存
  - `programmatic` TR-4.3: KDJ CROSS(K,D)判断正确
  - `programmatic` TR-4.4: MACD CROSS(DIFF,DEA)判断正确

## [ ] Task 5: _make_edge_action精确计算changed_codes
- **Priority**: high
- **Depends On**: Task 1, Task 2
- **Description**:
  - 重写_make_edge_action的action闭包：
    ```python
    changed = set()
    if is_data_dirty:
        source_codes = {_stock_code(s) for s in state.get_node_stocks(src) if isinstance(s, dict)}
        changed |= dirty.changed_codes & source_codes
    if is_node_dirty:
        changed |= dirty.node_entered_codes.get(src, set())
        changed |= dirty.node_exited_codes.get(src, set())
    bus.publish(EdgeFired(eid=eid, ts=time_at(state=state), changed_codes=list(changed)))
    ```
  - 移除旧逻辑：`if is_node_dirty and not changed: changed=全源池`（错误，会导致全量计算）
  - 移除按period取changed_codes的逻辑
  - 确保每次fire只发布1个EdgeFired事件
  - 涉及文件：[execution_module.py](file:///h:/new_tdx_mock/PYPlugins/meta_core/core/execution_module.py) _make_edge_action
- **Acceptance Criteria Addressed**: AC-3, AC-4, AC-5, FR-5, FR-6
- **Test Requirements**:
  - `programmatic` TR-5.1: 数据脏→changed_codes=changed_codes∩源池
  - `programmatic` TR-5.2: 节点脏→changed_codes含entered+exited
  - `programmatic` TR-5.3: 每次fire仅1个EdgeFired
  - `programmatic` TR-5.4: changed_codes不含非源池股票

## [ ] Task 6: _propagate记录entered，TTL记录exited
- **Priority**: high
- **Depends On**: Task 1
- **Description**:
  - _propagate中：转移成功后，对每个entered code调用state.add_node_entered_code(tid, code)，再mark_node_dirty(tid)
  - TTL到期action中：删除股票前，对每个exited code调用state.add_node_exited_code(nid, code)，删除后mark_node_dirty(nid)
  - 确保EdgeExecutor._on_edge_fired/run中订阅EdgeFired事件后正确传递changed_codes给_filter
  - 涉及文件：[execution_module.py](file:///h:/new_tdx_mock/PYPlugins/meta_core/core/execution_module.py) _propagate, TTL action, _on_edge_fired/run
- **Acceptance Criteria Addressed**: AC-5, FR-8
- **Test Requirements**:
  - `programmatic` TR-6.1: propagate后node_entered_codes含新入池股票
  - `programmatic` TR-6.2: TTL删除后node_exited_codes含被删股票
  - `programmatic` TR-6.3: 下游边changed_codes含这些股票

## [ ] Task 7: _filter增量筛选验证
- **Priority**: high
- **Depends On**: Task 5
- **Description**:
  - 验证_filter的changed_codes增量逻辑：
    - None→全量eval
    - []→返回缓存
    - 非空→仅eval_codes=changed_set，prev_passed=old_passed-changed_set
    - new_passed=(old_passed-changed_set)∪newly_passed，再∩codes_set
  - 确保handler（_eval_formula_path）接收的ctx使用最新的live_context/simulation_context（含未闭合K线）
  - 交集路径_eval_intersection_path对全量codes取交集（交集本身无法增量，但changed_codes只影响公式边）
  - 涉及文件：[execution_module.py](file:///h:/new_tdx_mock/PYPlugins/meta_core/core/execution_module.py) _filter, _eval_formula_path
- **Acceptance Criteria Addressed**: AC-3, FR-7
- **Test Requirements**:
  - `programmatic` TR-7.1: changed_codes=None→handler收全部codes
  - `programmatic` TR-7.2: changed_codes=[]→不调handler，返回缓存
  - `programmatic` TR-7.3: changed_codes=["a"]→handler仅收"a"，其余从缓存合并
  - `programmatic` TR-7.4: 增量结果与全量计算一致

## [ ] Task 8: 模块解耦静态检查
- **Priority**: medium
- **Depends On**: Task 1-7
- **Description**:
  - 运行scripts/check_module_imports.py
  - 确保BarComposer不import EdgeExecutor/PoolEngine
  - EdgeExecutor不import BarComposer/DataUpdater
  - 仿真/实盘共用_run_tick_body
  - 涉及文件：所有core/模块
- **Acceptance Criteria Addressed**: AC-8
- **Test Requirements**:
  - `programmatic` TR-8.1: check_module_imports.py通过
  - `programmatic` TR-8.2: `from core.engine import PoolEngine`导入成功

## [ ] Task 9: 仿真模式端到端Playwright验证
- **Priority**: high
- **Depends On**: Task 1-8
- **Description**:
  - 启动uvicorn服务器
  - 加载sim_demo_pool_100.json（100只fz前缀股票，备选池→A(5m KDJ,1min,TTL100min)/B(1m MACD,10s,TTL200min)→交集→C(5s,BUY100股,TTL20min卖出)）
  - 启动仿真模式，运行足够长时间
  - 使用MCP integrated_browser打开http://localhost:8000验证UI
  - 验证：8位fz代码、Tick间隔1-9s同股固定、A/B池有股票、交集入C池BUY、TTL 20min SELL、事件浮窗
  - 涉及文件：服务器/Web UI/仿真配置
- **Acceptance Criteria Addressed**: AC-7, AC-9
- **Test Requirements**:
  - `programmatic` TR-9.1: 服务器启动监听8000
  - `programmatic` TR-9.2: 候选池100只fz000001格式8字符
  - `programmatic` TR-9.3: Tick间隔1-9s同股固定不同股不同
  - `programmatic` TR-9.4: EdgeFired为单事件/边
  - `programmatic` TR-9.5: A/B池有股票，交集入C池触发BUY
  - `programmatic` TR-9.6: C池TTL20min后SELL
  - `programmatic` TR-9.7: 仿真与实盘共用_run_tick_body
  - `human-judgment` TR-9.8: Playwright验证UI可操作、事件浮窗显示正确

## Task Dependencies (DAG)
```
Task 1 (DirtyState精简)
  ├─→ Task 2 (DataUpdater/BarComposer)
  ├─→ Task 3 (K线含未闭合)
  │    └─→ Task 4 (移除bar_hash缓存)
  └─→ Task 6 (propagate/TTL记录entered/exited)
       └─→ Task 5 (_make_edge_action精确changed_codes)
            └─→ Task 7 (_filter增量验证)
                 └─→ Task 8 (静态检查)
                      └─→ Task 9 (端到端Playwright)
```
