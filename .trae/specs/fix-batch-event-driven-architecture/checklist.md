# 股票池实时事件驱动架构修正 - Verification Checklist

## DirtyState（最简）
- [ ] CP-1: changed_codes: Set[str] 全局集合，记录本轮有新tick的股票
- [ ] CP-2: add_changed_codes(codes) 合并去重
- [ ] CP-3: node_entered_codes: Dict[str, Set[str)] 跟踪新入池股票
- [ ] CP-4: node_exited_codes: Dict[str, Set[str)] 跟踪TTL删除股票
- [ ] CP-5: add_node_entered_code/add_node_exited_code 正确记录
- [ ] CP-6: clear_dirty() 清空所有脏标记
- [ ] CP-7: 不存在period_codes字段

## Tick与K线（实时更新）
- [ ] CP-8: DataUpdater.apply_data 调用add_changed_codes(advanced_codes)
- [ ] CP-9: BarComposer.on_tick 不调用add_changed_codes
- [ ] CP-10: BarComposer.on_tick 同bucket时_merge_tick更新high/low/close为最新价
- [ ] CP-11: BarComposer.on_tick 新bucket时_append_closed_bar+_new_bar_from_tick
- [ ] CP-12: state.bars[period][code] 实时更新（含未闭合K线）
- [ ] CP-13: DataChanged(tick) codes为批量列表
- [ ] CP-14: DataChanged(bar) codes为批量列表

## K线数据获取（含未闭合K线）
- [ ] CP-15: _get_period_bars 返回 bars_history + 当前未闭合bar
- [ ] CP-16: live_context 获取的K线DataFrame末尾为当前未闭合K线
- [ ] CP-17: simulation mode通过bars_history_getter获取的K线同样含current bar
- [ ] CP-18: current bar的close字段等于latest_tick最新价
- [ ] CP-19: current bar的high/low随tick实时更新
- [ ] CP-20: live和simulation K线获取路径一致

## 公式引擎（无错误缓存）
- [ ] CP-21: _cached_eval缓存key包含codes frozenset，或公式eval直接计算不缓存
- [ ] CP-22: 同tick同公式不同codes调用返回各自正确结果
- [ ] CP-23: tick变化后重新计算公式，不返回旧缓存
- [ ] CP-24: CROSS(K,D)金叉判断正确（K从下穿越D）
- [ ] CP-25: CROSS(DIFF,DEA)金叉判断正确
- [ ] CP-26: 公式喂入完整历史K线+未闭合K线计算

## EdgeFired事件（一边一事件）
- [ ] CP-27: 每条边每次fire仅发布1个EdgeFired事件
- [ ] CP-28: EdgeFired携带eid、ts、changed_codes
- [ ] CP-29: 数据脏触发: changed_codes = changed_codes ∩ 源池股票
- [ ] CP-30: 节点脏触发: changed_codes包含node_entered_codes和node_exited_codes
- [ ] CP-31: changed_codes不含不在源池的股票
- [ ] CP-32: 数据脏+节点脏同时: changed_codes为并集

## 节点入/出跟踪
- [ ] CP-33: _propagate后调用add_node_entered_code(tid, code)
- [ ] CP-34: TTL删除前调用add_node_exited_code(nid, code)
- [ ] CP-35: mark_node_dirty在add_node_entered/exited_code之后调用
- [ ] CP-36: 新入池股票出现在下游边changed_codes中
- [ ] CP-37: TTL删除股票出现在下游边changed_codes中

## _filter增量筛选
- [ ] CP-38: changed_codes=None: handler接收全部codes
- [ ] CP-39: changed_codes=[]: 有缓存则不调handler直接返回
- [ ] CP-40: changed_codes非空: handler仅接收eval_codes=changed_set
- [ ] CP-41: new_passed=(old_passed-changed_set)∪newly_passed，∩codes_set
- [ ] CP-42: filter_inputs[eid]存储pass集合，增量结果与全量一致
- [ ] CP-43: 交集边对全量passed_codes取交集

## 模块解耦
- [ ] CP-44: check_module_imports.py通过
- [ ] CP-45: BarComposer不import EdgeExecutor/PoolEngine
- [ ] CP-46: EdgeExecutor不import BarComposer/DataUpdater
- [ ] CP-47: 跨模块仅通过EventBus通信

## 仿真端到端
- [ ] CP-48: 股票代码fz+6位数字(8字符如fz000001)
- [ ] CP-49: Tick间隔1-9秒，同股票固定，不同股票不同
- [ ] CP-50: 仿真/实盘共用_run_tick_body代码路径
- [ ] CP-51: A池(5m KDJ金叉,TTL100min)有股票进入
- [ ] CP-52: B池(1m MACD金叉,TTL200min)有股票进入
- [ ] CP-53: A∩B交集进入C池，触发BUY 100股
- [ ] CP-54: C池TTL 20min后触发SELL全部持仓
- [ ] CP-55: A池TTL100min、B池TTL200min到期删除
- [ ] CP-56: 未闭合K线价格波动时KDJ/MACD实时计算不等闭合

## UI与语法
- [ ] CP-57: 所有core/模块py_compile通过
- [ ] CP-58: from core.engine import PoolEngine 导入成功
- [ ] CP-59: 服务器uvicorn启动监听8000端口
- [ ] CP-60: 事件浮窗默认显示自动滚动
- [ ] CP-61: 不同事件类型颜色区分
- [ ] CP-62: Playwright可加载股票池/启动暂停仿真
