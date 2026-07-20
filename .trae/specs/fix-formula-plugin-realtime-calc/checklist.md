# 公式插件化与实时计算架构修正 - Verification Checklist

## 公式算子与插件化
- [ ] formula_funcs.json中CROSS条目配置正确，handler=cross_op
- [ ] 公式`CROSS(K,D)`通过_ExprParser解析，_dispatch_func正确分派到cross_op算子
- [ ] cross_op返回最后一根K线的布尔值（金叉True/死叉False）
- [ ] 业务代码（execution_module.py/screening_module.py）中无cross_op直接import或硬编码金叉/死叉检测逻辑
- [ ] builtin_formulas.json中KDJ_5MIN_CROSS使用`CROSS(KDJ.K, KDJ.D)`，MACD_1MIN_CROSS使用`CROSS(MACD.DIF, MACD.DEA)`

## K线数据获取（含未闭合K线）
- [ ] _get_period_bars函数返回bars_history（已闭合）+ [current_bar]（未闭合）的完整序列
- [ ] current_bar从state.bars[period][code]获取，close等于latest_tick最新价
- [ ] 1分钟和5分钟周期均正确合并不闭合K线
- [ ] 返回的DataFrame行数=已闭合K线数+1（有未闭合K线时）
- [ ] KDJ/MACD公式使用含未闭合K线的数据计算，结果实时反映最新价变化
- [ ] 已移除"仅使用已闭合K线避免信号跳变"的错误注释和逻辑

## Tick实时计算链路
- [ ] Tick到达后立即更新latest_tick[code]
- [ ] 同一周期bucket_ts内的Tick更新未闭合K线的high/max、low/min、close/最新、vol累加
- [ ] bucket_ts变化（新周期开始）时，旧K线正确归档到bars_history
- [ ] DataChanged事件携带本轮有更新的codes列表
- [ ] EdgeFired事件携带changed_codes参数
- [ ] 公式引擎对changed_codes中的股票重新计算，全链路不等待K线闭合

## 状态池视图模型
- [ ] DirtyState中已移除node_entered_codes/node_exited_codes字段
- [ ] changed_codes全局维护本轮有Tick变化的股票集合
- [ ] 状态池不维护独立的node_stocks列表副本
- [ ] 池成员集合通过"源池股票 ∩ 筛选通过 ∩ TTL未过期"动态计算
- [ ] entry_trackers仅维护code→{entry_ts, entry_price}元数据
- [ ] TTL到期时从entry_trackers移除code，下次筛选自动排除
- [ ] 新入池股票自动加入下一轮changed_codes（通过首次全量或changed_codes包含源池交集）

## 增量筛选与per-code缓存
- [ ] 公式缓存key为(formula_ref, code, period, code_bar_hash)，per-code粒度
- [ ] code_bar_hash是单只股票该周期K线的哈希，非全局
- [ ] 某只股票K线变化仅失效该股票的公式缓存
- [ ] _filter方法：changed_codes非空时仅对changed_codes ∩ 源池股票重新评估
- [ ] 未变化股票沿用cached_passed结果
- [ ] passed_set = (cached_passed - changed_set) | newly_passed合并逻辑正确
- [ ] changed_codes为空时（首次运行）全量评估源池股票
- [ ] 100只股票增量筛选耗时<50ms

## 仿真模式与fz前缀
- [ ] _normalize_to_fz确保numeric.zfill(6)，代码格式为fz+6位数字（fz000001而非fz00001）
- [ ] 所有仿真代码匹配正则`^fz\d{6}$`
- [ ] SimTickSource为每只股票分配1-9秒内的固定Tick间隔
- [ ] 同一只股票Tick间隔固定，不同股票间隔不同
- [ ] 仿真模式时钟step()正确推进，大delta自动分小步（1秒）处理
- [ ] 从BarComposer开始的核心处理代码无`if mode=="sim"`或`if mode=="live"`分支
- [ ] 仿真/实盘差异仅在time_source/data_source/trade_interface/side_effects_scope配置表

## 股票池配置
- [ ] target_pool_100.json配置合法，Compiler能正确解析编译
- [ ] 备选池包含100只fz前缀股票
- [ ] 边e1：备选池→A池，interval=60秒，5分钟KDJ金叉，TTL=6000秒
- [ ] 边e2：备选池→B池，interval=10秒，1分钟MACD金叉，TTL=12000秒
- [ ] 边e3/e4：A池→C池、B池→C池，interval=5秒，交集条件，C池TTL=1200秒
- [ ] C池入池动作：市价买入100股（baimpool）
- [ ] C池出池动作：市价卖出所有持仓

## 自动交易
- [ ] 股票入C池时发布Signal(action="BUY", volume=100)
- [ ] C池TTL到期（20分钟）时发布Signal(action="SELL", volume=全部持仓)
- [ ] Signal事件通过EventBus发布，TradeModule订阅执行
- [ ] 仿真模式使用paper_trade，不触发真实交易
- [ ] 持仓记录正确维护（买入增加、卖出减少）
- [ ] OrderFilled事件正确发布并记录

## 事件监控与浮窗
- [ ] MonitoringModule订阅所有关键事件：TickReceived/DataChanged/BarComposed/FormulaEvaluated/EdgeFired/TransferExecuted/Signal(BUY/SELL)/TTLExpired/OrderFilled
- [ ] SSE事件流实时推送所有事件到前端
- [ ] 前端事件浮窗默认显示（不隐藏）
- [ ] 不同事件类型用不同颜色区分（Tick-灰/Bar-蓝/Formula-绿/Edge-橙/Transfer-紫/Signal-红/Order-黄）
- [ ] 事件按时间顺序显示，最新在底部，自动滚动
- [ ] 事件显示时间、类型、代码、详情信息

## 代码质量与解耦
- [ ] 模块间无直接业务引用，仅通过EventBus通信
- [ ] 静态检查无违规import
- [ ] 无注释掉的代码块、无死代码、无未使用import
- [ ] 每个模块职责单一，高内聚低耦合
- [ ] numpy/pandas向量化计算，无逐K线Python循环

## Playwright端到端验证
- [ ] 服务器成功启动在localhost:8000
- [ ] 浏览器访问页面正常加载
- [ ] 切换仿真模式成功
- [ ] 加载target_pool_100.json配置成功
- [ ] 启动仿真运行，事件浮窗开始滚动
- [ ] 备选池显示100只fz前缀股票
- [ ] Tick间隔1-9秒随机更新，K线实时变化
- [ ] 1分钟K线随Tick实时更新未闭合K线
- [ ] 5分钟K线正确合成
- [ ] KDJ/MACD指标实时计算（可用调试面板查看）
- [ ] 有股票满足KDJ金叉时转移到A池
- [ ] 有股票满足MACD金叉时转移到B池
- [ ] 同时在A和B的股票转移到C池，触发买单
- [ ] C池股票20分钟后触发卖单
- [ ] 事件浮窗完整记录所有事件，颜色正确，自动滚动
- [ ] 界面操作流畅无报错
