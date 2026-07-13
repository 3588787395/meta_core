> **历史参考文档**：执行流以 `SIMPLIFIED_EXECUTION.md` 为准。本文档仅作历史参考。

# 功能→表操作映射

## engine.py

| 函数 | 读表 | 写表 | 核心逻辑 | 可剥离 |
|------|------|------|---------|--------|
| `MetaEngine.__init__` | config/*.json (全部), timing.json, tdx_psatt.json, dispatch.json | self.tables, self.module_map, self.engine_index, self._edge_strategies, self._nset_dispatch, self._timing_cfg, self._psatt_cfg | 加载所有配置表到内存，构建模块映射/引擎索引/边策略/分发索引；初始化事件队列/信号队列/缓存/持仓跟踪 | 模块映射构建、参数别名加载可移至配置表 |
| `_register_rule_handlers` | ConfigStore (flow_mode_registry, edge_strategies) | RuleEngine (handler注册) | 将 builtins handler 注册到 RuleEngine，注册 toggle/encode 等规则 | toggle 规则可移至配置表 |
| `set_tq_adapter` | — | self.tq_adapter, self._async_tq_adapter | 设置 TQ 适配器并创建异步版本 | — |
| `set_storage` | — | self._storage | 设置存储实例 | — |
| `update_candidate_data` | — | self._current_kline_data | 更新当前K线数据缓存 | — |
| `get_current_kline_data` | self._current_kline_data | — | 读取K线数据缓存 | — |
| `get_modules` / `get_conditions` / `get_engines` | self.module_map / self.dispatch_index / self.engine_index | — | 读取模块/条件/引擎列表 | — |
| `_emit` | — | self.events | 追加事件到内存事件列表 | — |
| `_resolve_param` | self._param_aliases | — | 参数别名解析 | 别名映射可完全移至配置表 |
| `_resolve_node_type` | self._dzh_type_map | — | 将 dzh_cell_type 映射为节点类型字符串 | 类型映射可移至配置表 |
| `_init_node_stocks` | self._node_init, _HR (handler注册表) | node_stocks | 按节点类型查找初始化 handler 并执行，返回初始股票池 | — |
| `_execute_flows` | pool_config (nodes, edges), self._edge_cfg.output_types | — | 执行完整流程并输出结果节点 | — |
| `_execute_flows_with_state` | pool_config (nodes, edges) | — | 带状态的流程执行，返回更新后的股票池和转移事件 | — |
| `_tdx_should_execute` | timing.json (starttype_rules, market_calendar, offset_units) | — | 根据 starttype 判断 flow 是否满足时间调度条件 | 时间规则表达式可移至配置表 |
| `_tdx_check_duration` | timing.json (cxtype_rules) | self._flow_duration_starts, self._flow_exec_counts | 检查 cxtype 持续时长是否已到期 | 持续时长规则可移至配置表 |
| `_apply_tdx_psatt_ttl` | tdx_psatt.json (ttl_units, ttl_unit_labels, time_formats, auto_ttl_node_types) | node_stocks | 对状态池节点应用 psatt TTL 自动删除规则 | TTL单位映射、时间格式可移至配置表 |
| `_parse_intime_to_ts` | tdx_psatt.json (time_formats) | — | 将 indate+intime 解析为 Unix 时间戳 | 时间格式表可移至配置表 |
| `_dispatch_tdx_condition` | dispatch.json (nset_dispatch), tdx_evaluators 模块 | — | 根据 nset 分发到对应 TDX 条件评估器 | — |
| `_inject_bar_data` | self._edge_cfg (source_node_types, bar_data_injection.bar_fields) | node_stocks | 同步注入K线数据到 source 类型节点 | bar_fields 字段列表可移至配置表 |
| `_inject_bar_data_async` | self._edge_cfg (source_node_types, bar_data_injection.bar_fields), pool_config.timeframes | node_stocks | 异步注入K线数据，支持多时间框架并发获取 | — |
| `_cache_get` / `_cache_set` | self._data_cache | self._data_cache | 数据缓存读写（带 TTL 过期） | — |
| `_fetch_multi_timeframe_cached` | self._data_cache | self._data_cache | 带缓存的多时间框架并发数据获取 | — |
| `_execute_flowsCore` | pool_config (nodes, edges), timing.json, tdx_psatt.json, self._edge_strategies, self.dispatch_index, self.engine_index, _HR (handler注册表) | node_stocks, self._flow_exec_counts, self._exit_tracker_cache | **核心循环**：gate(starttype/cxtype)→filter→propagate→callback→ttl，创建 StockTracker，推送转移事件 | — |
| `_callback` | self._on_stock_enter_target_pool | — | 目标池回调（入池后触发 bsavehis/bsound/btip 等） | — |
| `run_pool` | — | self.events | 执行池并输出结果 | — |
| `run_tdx_pool` | — | self.events | 执行 TDX 池（从文件或模型转换后执行） | — |
| `run_tdx_pool_from_file` | XML文件 | — | 从文件执行 TDX 池 | — |
| `execute_pool` | — | self.events | 执行池并返回结果+事件 | — |
| `_lj` | config/*.json | — | 加载 JSON 配置文件 | — |
| `_on_config_reloaded` | self.tables | self.module_map, self.engine_index, self._edge_strategies, self._nset_dispatch | 热加载后重建内存索引 | — |
| `_rebuild_dispatch` | self.tables (dispatch) | self.dispatch_index | 重建分发索引（含 bit_mask 解析） | — |
| `_reload_config` / `check_reload` | — | — | 委托 table_loader 热加载 | — |
| `validate_all` | — | — | 委托 validator 校验 | — |
| `get_panel_config` | ConfigStore | — | 委托 panel_generator 生成面板配置 | — |
| `apply_field_change` | ConfigStore | — | 应用字段变更并触发规则联动 | — |
| `validate_field` | ConfigStore | — | 委托 panel_generator 校验字段 | — |
| `decode_attr_flags` / `encode_attr_flags` | — | — | 委托 DataBinder 编解码属性标志 | — |
| `resolve_flow_mode` | ConfigStore (flow_mode_registry) | — | 委托 DataBinder 解析流模式 | — |
| `fire_rules` | — | — | 委托 RuleEngine 触发规则 | — |
| `prefetch_klines_for_pool` | self._storage | — | 预取K线数据 | — |
| `_get_stock_price` | current_bar_data | — | 从行情数据获取股票当前价格 | 价格字段优先级列表可移至配置表 |
| `_tracker_detail` | stock._tracker | — | 从股票记录提取 StockTracker 盈亏信息 | — |
| `_find_stock_by_code` | stocks 列表 | — | 按代码查找股票记录 | — |
| `_update_trackers` | node_stocks, current_bar_data | node_stocks (tracker字段) | 更新所有持仓跟踪器的当前价和盈亏指标 | 盈亏/回撤计算公式可移至配置表 |
| `_is_trading_time` | timing.json (market_calendar.sessions) | — | 检查当前是否处于交易时间段 | 交易时段配置可移至配置表 |
| `_push_event` | — | self._event_queue | 向事件队列推送事件 | — |
| `_is_target_pool` | self._loop_pool_config, tdx_psatt.baimpool | — | 判断节点是否为目标池 | — |
| `_get_edge_condition` | self._loop_pool_config (edges) | — | 获取边的筛选条件描述 | — |
| `_push_signal` | — | self._signal_queue | 将信号写入信号队列（_signal_events 为 _signal_queue 派生 property，I33 收敛双写） | — |
| `get_signal_queue` | — | — | 获取信号队列 | — |
| `_emit_transfer_events` | node_stocks (prev/updated), transfer_events, self._exit_tracker_cache | self._event_queue, self._signal_queue | 对比执行前后股票池变化，推送 ENTER/EXIT/TIMEOUT 事件和 BUY/SELL 信号 | — |
| `run_loop` | pool_config (nodes, edges), timing.json (tick_interval) | self._event_queue, self._signal_queue | **持续循环引擎**：每 tick 执行 gate→filter→propagate→callback→ttl 完整流程 | — |
| `start_loop` | — | self._loop_task | 启动持续循环引擎（非阻塞） | — |
| `pause_loop` / `resume_loop` | — | self._pause_event | 暂停/恢复循环执行 | — |
| `stop_loop` | — | self._stop_event | 优雅停止循环执行 | — |
| `get_event_queue` | — | — | 获取事件队列 | — |

## native/builtins.py

| 函数 | 读表 | 写表 | 核心逻辑 | 可剥离 |
|------|------|------|---------|--------|
| `_load_json` | config/{filename} | — | 加载 JSON 配置文件 | — |
| `_gen_stock_codes` | _SCOPE_POOLS (内存Dict, 源自 mock_data.json) | — | 根据 scope 生成股票代码列表 | scope 范围定义可移至配置表 |
| `_decode_formula_base64` | — | — | Base64 解码公式文本 | — |
| `_resolve_fallback` | fallback_chain.json | — | 统一降级分发器：根据配置选择处理方式 | 降级链配置已在外部JSON，handler路由逻辑可移至配置表 |
| `_normalize_stock_code` | — | — | 股票代码格式归一化 | — |
| `_stock_code` | — | — | 从股票元素提取代码（dict 取 code/label fallback，其余 str()） | — |
| `_filter_by_bar_data` | current_bar_data | — | 根据 bar 数据筛选股票（code in bar_data 成员测试） | — |
| `_decode_type201_attr` | schemas._parse_attr_bits | — | 解码 type201 属性位 | — |
| `_decode_action` | DataBinder.decode_tdx_action_hex | — | 解码 action 编码 | — |
| `_apply_formula_mode` | — | — | 应用公式模式（reverse/rank） | 模式类型可移至配置表 |
| `_build_dzh_extra` | — | — | 构建 DZH 扩展字段字典 | 字段列表可移至配置表 |
| `_propagate` | node_stocks (src_id) | node_stocks (tgt_id, src_id) | 股票池间传播（追加/覆盖/移动） | — |
| `render_label` | — | — | 空操作，返回空字典 | — |
| `render_shape` | tq_adapter | — | 渲染形状（委托 TQ 或返回默认） | — |
| `stock_pool_hold` | tq_adapter (get_snapshot) | — | 股票池持仓：获取快照填充入场价/量 | — |
| `transfer_condition_check` | node.params (attr_int, indi, sorttype), tq_adapter (eval_indicator), current_bar_data | — | 转移条件检查：根据 attr 位标志分发到指标条件/基础条件/排名条件/反向转移/横截面等 | attr 位标志解析逻辑可移至配置表 |
| `resolve_market` | tq_adapter (resolve_market), _SCOPE_POOLS, _SECTOR_MOCK_DATA | — | 解析市场：生成股票代码列表，支持板块过滤和自定义股票 | — |
| `discard_sink_drop` | — | — | 丢弃股票：标记丢弃时间和来源 | — |
| `time_trigger_check` | — | — | 时间触发检查：当前时间是否在触发时间列表中 | — |
| `profit_analysis_calc` | analysis_results.json, tq_adapter (get_snapshot, get_kline_data) | — | 盈亏分析计算：根据分析类型生成报告 | 分析类型/字段/结构配置可移至配置表 |
| `_calc_mock_field` | — | — | Mock 数据生成（hash 伪随机） | mock 字段生成规则可移至配置表 |
| `_calc_field_from_formula` | — | — | 从公式计算字段值 | 公式映射可移至配置表 |
| `_calc_aggregate_field_from_formula` | — | — | 从公式计算聚合字段值 | — |
| `candidate_resolve` | — | — | 委托 resolve_market | — |
| `accumulate_state` | — | — | 委托 stock_pool_hold | — |
| `discard_stocks` | — | — | 委托 discard_sink_drop | — |
| `_gen_sector_stocks` | _SECTOR_MOCK_DATA, mock_data.json (sector_generation_rules) | — | 生成板块股票列表 | 板块生成规则可移至配置表 |
| `formula_eval` | tq_adapter (eval_indicator), fallback_chain.json, node.params (indi/formula/mode/sort_type等) | — | 公式评估：解码公式→TQ执行→结果分类(passed/rejected)→应用模式→排序截取 | DZH 扩展字段组装可移至配置表 |
| `sector_filter` | tq_adapter (get_block_members), fallback_chain.json | — | 板块过滤：保留属于指定板块的股票 | — |
| `cross_section_eval` | tq_adapter (eval_indicator), fallback_chain.json | — | 横截面评估：按指标值排序取前N | — |
| `basic_filter` | tq_adapter (get_financial_data), fallback_chain.json | — | 基本面过滤：PE/PB/ROE 条件筛选 | 筛选字段和阈值可移至配置表 |
| `pass_through` | — | — | 透传：所有股票通过 | — |
| `condition_dispatcher` | tq_adapter (eval_indicator), dispatch_index, fallback_chain.json | — | 条件分发器：按 attr 位掩码匹配规则，AND/OR 组合多条件 | 位掩码匹配规则可移至配置表 |
| `_action_resolve_and_pass` | node_stocks, nodes, _HR (handler注册表) | node_stocks | 解析源节点股票并传递到目标节点 | — |
| `_action_apply_filter` | node_stocks, nodes, dispatch_index, current_bar_data | node_stocks | 对源节点股票应用条件过滤后传播 | — |
| `_action_dzh_condition_filter` | node_stocks, nodes, current_bar_data | node_stocks | DZH 条件过滤：构建 formula_eval 输入并执行 | — |
| `_action_pass_pool_stocks` | node_stocks | node_stocks | 传递池股票（支持 move） | — |
| `_action_transfer_between_pools` | node_stocks | node_stocks | 池间转移 | — |
| `_action_remove_from_pool` | node_stocks | node_stocks | 从池中移除股票（move 模式清空源） | — |
| `tdx_condition_evaluator` | node_stocks, nodes, dispatch.json (nset_dispatch), tdx_evaluators.eval_tdx_condition, fallback_chain.json | node_stocks | TDX 条件评估器：按 nset 分发到对应评估器 | — |
| `edge_default_transfer` | strategy.pre_inject, node_stocks, src_params | action_inputs | 边默认转移：按策略注入参数（from_source_params/attrtext/stocks/node） | pre_inject 策略可移至配置表 |
| `transfer_with_market_data_handler` | tq_adapter (get_stock_table_data) | — | 带行情数据的转移处理 | col_def 默认值可移至配置表 |
| `log_transfer_handler` | storage (insert_transfer_log) | — | 转移日志记录 | — |
| `condition_dispatch_handler` | node.params, dispatch_index, tq_adapter | — | 条件分发处理：条件评估→返回结果 | — |
| `init_market_source` | tq_adapter, node.params | — | 初始化市场源节点 | — |
| `init_stock_state_pool` | node.params.stocks | — | 初始化股票状态池 | — |
| `init_tdx_candidate` | node.params.stocks | — | 初始化 TDX 候选节点 | — |
| `tdx_convert_from_file` | XML文件 | — | TDX XML→内部配置转换 | — |
| `tdx_convert_from_pool` | pool_model | — | TDX 池模型→内部配置转换 | — |

## app.py

| 函数 | 读表 | 写表 | 核心逻辑 | 可剥离 |
|------|------|------|---------|--------|
| `_load_json_cache` | config/{xml_mapping,history_schema,action_table}.json | globals() 缓存 | 带缓存的 JSON 配置加载 | — |
| `_resolve_field` | — | — | 按 dot-path 解析对象字段 | — |
| `_model_to_dict` | — | — | 模型转字典 | — |
| `_indent_xml` | — | — | XML 缩进格式化 | — |
| `_resolve_attr` | xml_mapping.json, node/edge 数据 | — | 按映射配置解析属性值 | — |
| `_apply_attr_defaults` | — | — | 应用属性默认值（含条件默认值） | 条件默认值逻辑可移至配置表 |
| `_code_to_market` | — | — | 股票代码→市场编号映射 | 市场映射规则可移至配置表 |
| `_normalize_code` | — | — | 股票代码归一化 | — |
| `_get_stock_name` | mock_data.json (stock_names) | — | 获取股票名称 | — |
| `_build_tdx_xml` | xml_mapping.json, pool_data | XML文件 (tdxpool/{name}.xml) | 将池数据构建为 TDX XML 文件 | XML 映射配置已外部化 |
| `_tdx_pool_to_frontend` | xml_mapping.json, tdx_pool 对象 | — | TDX 池对象→前端 JSON 格式转换 | 字段映射可移至配置表 |
| `_extract_stk_fields` | history_schema.json (write_defaults) | — | 从股票记录提取历史字段 | — |
| `_write_stk_xml` | history_schema.json | XML文件 (.dat/.log) | 写入股票历史 XML 文件 | — |
| `_read_history_log` | history_schema.json, tdxpool/{pool}/{node}/{date}.dat/.log | — | 读取历史日志 XML | — |
| `_write_history_for_node` | history_schema.json | tdxpool/{pool}/{node}/{date}.dat/.log | 为节点写入历史数据 | — |
| `_save_pool_history` | pool_config (nodes), execution_result (node_states) | tdxpool/{pool}/{node}/{date}.dat/.log | 保存池执行结果的历史数据 | — |
| `_append_history_entry` | node.params (tdx_psatt.bsavehis) | tdxpool/{pool}/{node}/{date}.dat/.log | 实时追加入池记录 | — |
| `_dispatch_pool_enter_actions` | action_table.json (pool_enter_actions), node.params (tdx_psatt) | — | 入池动作分发：bsavehis/bsound/btip/bclearblock 等 | 动作配置已外部化 |
| `_play_sound_alert` | — | — | 声音预警（仅日志） | — |
| `_show_popup_alert` | — | — | 弹窗提示（仅日志） | — |
| `_save_to_tdx_block` | — | data/tdx_blocks/{blockfile}.txt | 保存股票到 TDX 板块文件 | — |
| `_load_tdx_pool_config` | XML文件 | — | 加载 TDX 池配置（XML→前端格式） | — |
| `lifespan` | — | app.state (engine, storage, tq) | 应用生命周期：初始化引擎/存储/适配器 | — |
| `tdx_list_pools` | tdxpool/ 目录 | — | 列出 TDX 池文件 | — |
| `tdx_load_pool` | tdxpool/{name}.xml | — | 加载 TDX 池 | — |
| `tdx_export_xml` | — | 临时XML文件 | 导出 TDX XML | — |
| `tdx_create_pool` | — | tdxpool/{name}.xml, Storage (save_pool) | 创建 TDX 池 | — |
| `tdx_execute_pool` | pool_data 或 tdxpool/{filename}.xml, MetaEngine (run_pool/run_tdx_pool_from_file), action_table.json | — | 执行 TDX 池（含入池回调） | — |
| `tdx_delete_pool` | tdxpool/{name}.xml, .png | — | 删除 TDX 池文件 | — |
| `tdx_save_pool` | — | tdxpool/{name}.xml, Storage (save_pool) | 保存 TDX 池 | — |
| `registry_generic` | config/{cell_type_registry,modules,dzh_type_map,defaults,flow_mode_registry,edge_strategies}.json | — | 读取注册表配置 | — |
| `tdx_import_file` | 上传的XML文件 | — | 导入 TDX XML 文件 | — |
| `list_dir_files` | tdxpool/dzhpool/examples 目录 | — | 列出目录文件 | — |
| `load_dzhpool_file` | dzhpool/{filename} | — | 加载 DZH 池文件 | — |
| `load_example_file` | examples/{filename} | — | 加载示例文件 | — |
| `list_history_dates` | tdxpool/{pool}/{node}/ 目录 | — | 列出历史日期 | — |
| `get_full_entry_log` | tdxpool/{pool}/{node}/{date}.dat/.log, history_schema.json | — | 获取完整入池日志 | — |
| `get_history_data` | tdxpool/{pool}/{node}/{date}.dat/.log, history_schema.json | — | 获取指定日期历史数据 | — |
| `export_history_data` | tdxpool/{pool}/{node}/{date}.dat/.log, history_schema.json | — | 导出历史数据为文本 | — |
| `SPAMiddleware.__call__` | web/ 目录 | — | SPA 中间件：API 请求透传，其他请求返回前端 | — |

## tdx_evaluators.py

| 函数 | 读表 | 写表 | 核心逻辑 | 可剥离 |
|------|------|------|---------|--------|
| `_apply_noperate` | — | — | nset=0 的10种操作符比较逻辑（等于/大于/小于/上穿/下破/持股/排名前N/排名后N/上拐/下拐） | 操作符类型和比较逻辑可移至配置表 |
| `_build_formula_arg` | func (nfirst/nsecond/cfirst/csecond) | — | 从 func 参数构建 formula_arg 字符串 | 参数优先级可移至配置表 |
| `eval_nset0_indicator` | tdx_ntjindexno_lookup.json (间接), tq_adapter (formula_process_mul_zb) | — | nset=0 技术指标序列评估：批量获取指标线数据，按 noperate 执行时序比较 | — |
| `eval_nset1_condition_formula` | tq_adapter (formula_process_mul_xg) | — | nset=1 条件选股公式评估：批量执行选股公式，检查最新信号值 | 信号判断逻辑（values[-1]=="1"）可移至配置表 |
| `eval_nset2_expert_system` | tq_adapter (formula_exp), _EXPERT_SIGNAL_MAP | — | nset=2 专家系统评估：按 nfirst 选择信号通道（任意/买入/卖出） | 信号选择映射(_EXPERT_SIGNAL_MAP)可移至配置表 |
| `eval_tdx_condition` | dispatch.json (evaluator_dispatch) | — | 统一分发入口：根据 dispatch_key 路由到对应 nset 评估器 | — |
| `_scalar_compare` | — | — | 标量值比较（等于/大于/小于/排名前N） | 操作符类型可移至配置表 |
| `_extract_code` | — | — | 从股票元素提取标准代码 | — |
| `eval_nset3_financial_scalar` | tdx_ntjindexno_lookup.json (nset_3_financial.fields), _STOCK_INFO_FIELD_MAP, tq_adapter (get_stock_info/get_financial_data) | — | nset=3 最新财务标量评估：按 ntjindexno 查询财务数据并标量比较 | 字段映射表(_financial_fields, _STOCK_INFO_FIELD_MAP)已外部化 |
| `eval_nset4_market_scalar` | tdx_ntjindexno_lookup.json (nset_4_market.fields), tq_adapter (get_market_snapshot) | — | nset=4 实时行情标量评估：按 ntjindexno 查询行情快照并标量比较，含衍生字段计算 | 衍生字段计算逻辑（涨幅%/振幅%）可移至配置表 |
| `_snap_val` | — | — | 从行情快照安全提取字段数值 | — |
| `eval_nset5_set_operation` | node_stocks, edges, func.noperate | — | nset=5 集合运算评估：对多条入边源股票池执行并集/差集/交集 | — |

## kline_replay_engine.py

| 函数 | 读表 | 写表 | 核心逻辑 | 可剥离 |
|------|------|------|---------|--------|
| `KLineReplayEngine.__init__` | — | self._bars, self._timeline, self._state_pools, self._stock_enter_times, self._flow_fire_counts 等 | 初始化回放引擎状态 | — |
| `_is_trading_time` | — | — | 判断是否交易时间（硬编码 9:30-11:30, 13:00-15:00） | 交易时段可移至配置表 |
| `_get_market_open_time` / `_get_market_close_time` | — | — | 获取市场开/收市时间 | — |
| `load_kline_data` | tq_adapter (get_kline_batch), pool_model | self._bars, self._timeline, self._state_pools, self._synthesized_bars, Storage (create_replay_session) | 加载K线数据：批量获取→构建时间线→初始化状态池→合成多周期K线→创建DB会话 | — |
| `_create_db_session` | Storage | self._session_id | 创建回放数据库会话 | — |
| `_calc_synthesis_stats` | self._synthesized_bars | — | 计算合成K线统计 | — |
| `_extract_codes` | pool_model (nodes) | — | 从池模型提取股票代码列表 | — |
| `_init_state_pools` | pool_model (nodes) | self._state_pools, self._stock_enter_times | 初始化各类型节点的状态池 | 节点类型→初始化逻辑映射可移至配置表 |
| `_init_flow_counts` | pool_model (edges) | self._flow_fire_counts, self._flow_last_fire | 初始化边的触发计数 | — |
| `_normalize_edges` | pool_model (edges) | pool_model (edges) | 边格式归一化（startid→source.node_id, tran→mode, TDX参数名映射） | 参数名映射可移至配置表 |
| `set_pool_model` | — | self._pool_model | 设置池模型并归一化边 | — |
| `_get_edge_info` / `_get_node_info` | self._pool_model | — | 按ID查找边/节点信息 | — |
| `_get_node_hold_sec` | self._pool_model (nodes) | — | 获取节点持仓时长 | — |
| `_apply_hold_expiry` | self._state_pools, self._stock_enter_times | self._state_pools, self._stock_enter_times, self._event_log | 应用持仓超时淘汰 | — |
| `_build_synthesized_bars` | self._bars | self._synthesized_bars | 合成多周期K线数据 | 周期映射关系可移至配置表 |
| `_get_current_datetime` | self._timeline | — | 获取当前K线时间 | — |
| `_should_fire_flow` | native.timing.should_fire, self._flow_fire_counts, self._flow_last_fire | — | 判断 flow 是否应触发（委托 timing 模块） | — |
| `_inject_bar_data` | self._timeline | — | 注入当前K线数据 | — |
| `_is_tdx_pool` | self._pool_model | — | 判断是否为 TDX 类型池 | — |
| `_apply_flow_scheduling` | self._pool_model (edges), timing | self._flows_fired_this_bar | 应用 flow 时间调度 | — |
| `next_bar` | self._timeline, self._pool_model, self._state_pools, MetaEngine._execute_flows_with_state, Storage (batch_log_stock_transfers, save_replay_snapshot, update_replay_session) | self._state_pools, self._flow_fire_counts, self._flow_last_fire, self._stock_enter_times, self._snapshots, self._event_log, Storage | **核心回放逻辑**：推进一根K线→注入数据→调度flow→执行引擎→记录转移事件→更新入池时间→应用持仓超时→保存快照 | — |
| `_append_event` | — | self._event_log, self._last_bar_events | 追加事件日志（带大小限制） | — |
| `play` | — | self._playing, self._replay_thread | 启动回放线程 | — |
| `_sync_play_loop` | — | — | 同步回放循环：按速度推进K线 | — |
| `pause` | — | self._paused | 暂停回放 | — |
| `stop` | — | self._playing, self._paused, self._replay_thread | 停止回放 | — |
| `step` | — | self._paused | 单步推进一根K线 | — |
| `set_speed` | SPEED_MAP | self._speed | 设置回放速度 | 速度映射可移至配置表 |
| `get_current_snapshot` | self._timeline, self._state_pools, self._stock_enter_times, self._flow_fire_counts, self._flows_fired_this_bar, self._event_log | — | 获取当前快照（含状态池/触发计数/事件） | — |
| `get_progress` | self._timeline | — | 获取回放进度 | — |
| `seek` | — | self._current_index | 跳转到指定进度 | — |
| `get_stock_table_data` | self._state_pools, self._stock_enter_times, tq_adapter (get_stock_table_data) | — | 获取节点股票表格数据 | — |
