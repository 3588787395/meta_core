> **历史参考文档**：执行流以 `SIMPLIFIED_EXECUTION.md` 为准。本文档仅作历史参考。

# 功能-表操作映射清单 & 硬编码分支清单

> 审计日期: 2026-06-12
> 审计范围: engine.py, native/builtins*.py, app.py, tdx_evaluators.py, runtime_simulator.py, kline_replay_engine.py
>
> 更新日期: 2026-06-13（Task 13 文档同步：新增 0 节"功能-表-表 三列速查矩阵"）

---

## 0. 功能-表-表 三列速查矩阵

> 简洁版核心循环映射。功能模块 → 触发条件 → 读哪张表 → 写哪个运行时表。
> 优先级：① starttype/cxtype ② TTL 淘汰 ③ propagate 模式 ④ baimpool 角色 ⑤ 数据源契约 ⑥ post_tick 流水线。

| 功能名 | 触发条件 | 查的表 | 写入的运行时表 |
|--------|---------|--------|---------------|
| starttype 门控（立即/延迟/开市前/后/收市前/后/指定时间） | `_tdx_should_execute(edge)` 每边执行前 | `timing.json:starttype_rules` + `_dispatch_table` | 无（纯判断） |
| cxtype 过期（一直/持续窗口/只一次） | `_tdx_check_duration(edge)` 每边执行前 | `timing.json:cxtype_rules` + `cxtime_units` | `_flow_duration_starts`（首次）/ `_flow_exec_counts`（每次+1） |
| TTL 淘汰（bdel=1 且超时） | `_apply_tdx_psatt_ttl(node_id)` 每 tick | `tdx_psatt.json:ttl_units` + `time_formats` | `node_stocks[tgt]`（删除超时股票） |
| 6 种 psatt 副作用（bsavehis/bsound/btip/bsavetoblock/baimpool） | `_dispatch_pool_enter_actions` 入池后 | `action_table.json:pool_enter_actions` + `tdx_psatt.json:side_effects` | 调用回调（写历史文件/日志/板块文件） |
| 强弱筛选（nset×noperate 分发） | `condition_dispatcher` / `formula_eval` 每边 filter 段 | `dispatch.json`（位掩码路由 + `nset_dispatch` 子表） + `engines.json`（网关） | 无（返回值 passed/rejected） |
| TDX 公式评估（nset=0/1/2/3/4/5） | `tdx_evaluators.eval_nset*` | `tdx_indicators.json`（nset=0） + `tdx_ntjindexno_lookup.json`（nset=3） | 无（返回值） |
| 降级链（tq 不可用 → bar_data → pass_through） | `_resolve_fallback("chain_name")` TQ 调用失败时 | `fallback_chain.json:chains[chain_name]` | 无（链式选择 handler） |
| propagate 模式解析（move/overwrite/copy/force_move/output_components/pass_through） | `_propagate` 边执行后段 | `flow_mode_registry.json:resolve_rules`（按 priority 顺序匹配 attr_bits） | `node_stocks[tgt]`（按模式增减） + `node_stocks[src]`（move/force_move 时清空） |
| 池角色解析（target/candidate/sink/market_source/transfer_condition） | `_resolve_role(node)` 事件生成前 | `pool_roles.json:role_resolution.rules`（按 priority 顺序匹配） | 无（仅返回 role_id） |
| 事件生成（ENTER/EXIT/TIMEOUT/RANK_CHANGED） | `_emit_transfer_events(prev, curr, tevs)` tick 末尾 | `event_rules.json:event_types` + `signal_rules.json` | `_event_queue`（异步队列） |
| 信号生成（BUY/SELL） | 目标池入池/出池（role=target） | `signal_rules.json:signal_types` + `pool_roles.json:roles.target_pool` | `_signal_queue`（异步队列；_signal_events 为其派生 property，I33 收敛双写） |
| 持仓跟踪（盈亏/最大盈利/回撤/天数） | `_update_trackers` tick 末尾 | `tracker_schema.json:formulas` | `_tracker_snapshots` + `_exit_tracker_cache` |
| 数据源探测（tq_dll/tq_sdk/akshare/mock） | `/api/pool/{id}/run` 启动前 | `data_source_contract.json:sources[*]`（probe_expr + timeout_ms） | 无（探测失败返回 503） |
| post_tick stage 1: PK 排名 | `_post_tick()` 第 1 阶段 | `pk_config.json` | `_pk_rankings` |
| post_tick stage 2: 多分析角度 | `_post_tick()` 第 2 阶段 | `analysis_config.json` | `_angle_results` |
| post_tick stage 3: 看盘面板 | `_post_tick()` 第 3 阶段 | `dashboard_schema.json` | `_dashboard_data`（依赖 `_pk_rankings` + `_angle_results`） |
| post_tick stage 4: 监控告警 | `_post_tick()` 第 4 阶段 | `alert_rules.json` | `_alert_events` + `_alert_queue` + `_alert_cooldown` |
| 时间源（实盘/回放/仿真） | `_now()` 每次需当前时间 | `time_sources.json:sources[*].driver_type` | `_current_time_source`（设置） |
| 节点初始化（source 节点拉取市场股票） | `_init_node_stocks(nodes)` 池启动 | `edge_strategies.json:node_init` + `markets.json` + `tq_adapter` | `node_stocks[src]` |
| 运行时模式初始化（live/replay/simulation） | `run_mode(mode_id)` 池启动 | `runtime_modes.json:modes[mode_id]` + `time_sources.json` + `trade_interfaces.json` | `_current_time_source` + `_trade_interface` + 启动循环 |
| 决策路由（边策略：source→target → handler） | `_execute_flowsCore` 每边入口 | `edge_strategies.json:_edge_strategies`（key=`source_type:target_type`） | 无（仅获取 handler） |
| 节点类型注册（cell_type → handler） | `app.registry_generic` / engine init | `cell_type_registry.json:type_aliases` + `modules.json` + `behavior_actions.json` | `_handler_registry` |
| 类型属性位标志解码（attr_int → 布尔属性） | `_resolve_flow_attrs(attr_int)` | `field_definitions.json:flow_fields.attr.bit_fields` | 无（仅返回属性字典） |
| 行为动作注册（bsound/btip/bsavehis/bsavetoblock） | `_HANDLERS` 构建时 | `behavior_actions.json` | `_HANDLERS` + `_handler_registry` |
| 历史记录文件格式（dat/log 写入） | `bsavehis=1` 入池时 | `history_schema.json:dat_format` + `log_format` | 写 `.dat` / `.log` 文件 + `node_state`（持久表） |
| XML 导入导出（TDX/DZH 互转） | `tdx_import_file` / `tdx_export_xml` | `xml_mapping.json:pool/cell/flow` 元素映射 | 写 `.xml` 文件 + `pool_config`（持久表） |
| 分析结果字段定义（多分析角度输出） | `profit_analysis_calc` 等 | `analysis_results.json:field_definitions` | 无（仅返回字段列表） |
| 回放会话（每根 K 线状态快照） | `KLineReplayEngine.next_bar` | `kline_cache`（持久表） + 全部配置表 | `_state_pools` + `replay_snapshot` + `replay_session` + `stock_transfer_log` |
| 仿真运行（随机生成 bar 数据） | `RuntimeSimulator.step` | `mock_data.json:_gn` + `timing.json:simulator` | `_state_pools` + `_virtual_clock` + `event_log` |
| CRUD 持久化（创建/读取/更新/删除池） | `Storage.save_pool / get_pool / delete_pool` | `pool_config`（持久表） | `pool_config`（INSERT/UPSERT/DELETE） + `pool_node` + `pool_edge` + `config_version` |
| 域代码归一化（6 位 HHMMSS） | `_parse_intime_to_ts(indate, intime)` TTL 计算前 | `tdx_psatt.json:time_formats`（max_len → format 映射） | 无（仅返回 timestamp） |
| 公式模式应用（accumulate/judge/stateful） | `_apply_formula_mode(mode, formula)` formula_eval 入口 | `formula_modes.json:modes[mode_id]` | 无（仅返回转换后 formula） |
| 财务字段映射（PE/PB/ROE 字段名） | `eval_nset3_financial_scalar` | `tdx_ntjindexno_lookup.json:_financial_fields` + `data_source_mappings.json:stock_info_field_map` | 无（仅返回字段值） |
| 行业/板块候选股生成 | `_gen_sector_stocks(sector)` 市场源初始化 | `mock_data.json:sector_generation_rules` + `markets.json` | 无（仅返回股票代码列表） |
| 板块文件保存（bsavetoblock=1） | `_save_to_tdx_block` 入池回调 | `node.params.tdx_psatt.blockfile` + `node_stocks[tgt]` | 写板块 `.blk` 文件 |
| 声音预警（bsound=1） | `_play_sound_alert` 入池回调 | `node.params.tdx_psatt.nsoundtype` + `soundfile` | 无（仅播放声音/记录日志） |
| 弹窗提示（btip=1） | `_show_popup_alert` 入池回调 | `node.params.tdx_psatt` | 无（仅弹窗/记录日志） |
| 标记目标池（baimpool=1） | `_emit_transfer_events` 检测 role=target_pool | `pool_roles.json:roles.target_pool` + `node.params.tdx_psatt.baimpool` | 无（仅高亮） |
| 持仓退出 EXIT 事件（含 tracker 信息） | 股票 move 出池时 | `_exit_tracker_cache`（move 时缓存的旧 tracker） | `_event_queue`（EXIT 事件） + `_signal_queue`（SELL 信号） |

> 状态图例：✅ 已剥离为配置表并被引擎查表调用 / ⏳ spec 已规划剥离，等待对应 Task 执行

---

## 一、配置表清单（57张）

| # | 表名 | 被引用位置 |
|---|------|-----------|
| 1 | action_rules.json | builtins_actions |
| 2 | action_table.json | engine.__init__ |
| 3 | actions.json | builtins_actions |
| 4 | alert_rules.json | builtins_post_tick |
| 5 | analysis_angles.json | builtins_post_tick |
| 6 | analysis_config.json | builtins_filters.profit_analysis_calc |
| 7 | analysis_results.json | builtins_post_tick |
| 8 | analysis_types.json | builtins_post_tick |
| 9 | api_routes.json | app.py (未直接读) |
| 10 | behavior_actions.json | builtins_actions |
| 11 | cell_type_registry.json | app.registry_generic |
| 12 | dashboard_schema.json | builtins_post_tick |
| 13 | data_config.json | engine.__init__ |
| 14 | data_providers.json | 未直接引用 |
| 15 | data_source_mappings.json | 未直接引用 |
| 16 | defaults.json | engine.__init__, app.registry_generic |
| 17 | dispatch.json | engine.__init__ |
| 18 | dzh_extra_fields.json | builtins_filters._build_dzh_extra, formula_eval |
| 19 | dzh_type_map.json | engine.__init__, app.registry_generic |
| 20 | edge_strategies.json | engine.__init__, app.registry_generic |
| 21 | engines.json | engine.__init__ |
| 22 | event_rules.json | engine.__init__ |
| 23 | fallback_chain.json | builtins._resolve_fallback, builtins_filters.formula_eval/sector_filter/cross_section_eval/basic_filter/condition_dispatcher |
| 24 | field_definitions.json | builtins_filters._decode_type201_attr, engine._resolve_flow_attrs(应查未查) |
| 25 | fields.json | 未直接引用 |
| 26 | flow_mode_registry.json | engine.__init__, runtime_simulator.__init__ |
| 27 | formula_modes.json | builtins_filters._apply_formula_mode, formula_eval |
| 28 | generate_configs.py | 生成脚本, 非配置表 |
| 29 | history_schema.json | 未直接引用 |
| 31 | markets.json | builtins_filters._gen_stock_codes, resolve_market |
| 32 | match_modes.json | 未直接引用 |
| 33 | mock_data.json | builtins._gen_sector_stocks, runtime_simulator.__init__ |
| 34 | modules.json | engine.__init__, app.registry_generic |
| 35 | operators.json | 未直接引用 |
| 36 | pk_config.json | builtins_post_tick |
| 37 | pk_rules.json | builtins_post_tick |
| 38 | pk_score_dimensions.json | builtins_post_tick |
| 39 | pool_roles.json | engine.__init__ (但_role_handlers仍硬编码) |
| 40 | pool_types.json | 未直接引用 |
| 41 | post_tick_pipeline.json | engine.__init__ |
| 42 | price_fields.json | engine.__init__ |
| 43 | property_ownership.json | 未直接引用 |
| 44 | runtime_modes.json | engine.__init__, runtime_simulator.__init__ |
| 45 | schedule_resolvers.json | 未直接引用 |
| 46 | signal_rules.json | engine.__init__ |
| 47 | table_schemas.json | 未直接引用 |
| 48 | tdx_enums.json | 未直接引用 |
| 49 | tdx_field_visibility.json | 未直接引用 |
| 50 | tdx_indicators.json | tdx_evaluators._build_formula_arg |
| 51 | tdx_ntjindexno_lookup.json | tdx_evaluators.eval_nset3 |
| 52 | tdx_psatt.json | engine.__init__, engine._apply_tdx_psatt_ttl |
| 53 | time_sources.json | engine.__init__, runtime_simulator.__init__ |
| 54 | timing.json | engine.__init__, runtime_simulator.__init__ |
| 55 | tracker_formulas.json | engine.__init__ |
| 56 | tracker_schema.json | engine.__init__ |
| 57 | trade_interfaces.json | engine.__init__, runtime_simulator.__init__ |
| 58 | ui_components.json | 未直接引用 |
| 59 | ui_layouts.json | 未直接引用 |
| 60 | validate_refs.py | 校验脚本, 非配置表 |
| 61 | xml_mapping.json | 未直接引用 |

> **未直接引用** 的配置表（14张）: data_providers, data_source_mappings, fields, history_schema, match_modes, operators, pool_types, property_ownership, schedule_resolvers, table_schemas, tdx_enums, tdx_field_visibility, ui_components, xml_mapping

---

## 二、逐函数表操作映射

### 2.1 engine.py

| 函数 | 行号 | R:CONFIG | R:RUNTIME | W:RUNTIME | W:PERSIST |
|------|------|----------|-----------|-----------|-----------|
| `_stock_code(s)` | L11-13 | — | — | — | — |
| `LRUCache.__init__` | L17-18 | — | — | `_store,_max,_default_ttl,_ttl_map` | — |
| `LRUCache.get` | L19-24 | — | `_store` | — | — |
| `LRUCache.set` | L25-31 | — | `_ttl_map` | `_store` | — |
| `LRUCache.clear` | L32 | — | — | `_store` | — |
| **`MetaEngine.__init__`** | L36-90 | timing,tdx_psatt,dzh_type_map,modules,engines,edge_strategies,defaults,dispatch,tracker_schema,event_rules,signal_rules,pool_roles,data_config,price_fields,post_tick_pipeline,runtime_modes,time_sources,trade_interfaces | — | `tables,module_map,engine_index,dispatch_index,_flow_duration_starts,_flow_exec_counts,_pool_start_time,_event_queue,_signal_queue,_data_cache,_compiled_timing,_compiled_tracker,_pk_rankings,_angle_results,_dashboard_data,_alert_events,_alert_cooldown,_exit_tracker_cache` | — |
| `set_tq_adapter(a)` | L91 | — | — | `tq_adapter` | — |
| `_init_node_stocks(nodes)` | L92-107 | defaults,dzh_type_map,edge_strategies | `_HR`(handler注册表) | 返回`ns`(node_stocks) | — |
| `_tdx_should_execute(edge)` | L108-131 | timing(`starttype_rules,market_calendar,offset_rules`) | `tables[timing]`, `_compiled_timing` | — | — |
| `_resolve_flow_attrs(attr_int)` | L132-155 | **应查field_definitions但未查** | — | — | — |
| `_apply_tdx_psatt_ttl()` | L155-164 | tdx_psatt(`ttl_rules`) | `node_stocks`, `_flow_duration_starts` | `node_stocks`(删除过期股) | — |
| `_emit_transfer_events()` | L165-200 | **pool_roles(但_role_handlers硬编码)** | `node_stocks`, `_pool_state` | `_event_queue` | — |
| `_dispatch_pool_enter_actions()` | L201-219 | — | `_on_stock_enter_target_pool`(回调) | — | **history写入**(通过回调) |
| `_execute_flowsCore()` | L220-270 | edge_strategies(`_edge_strategies`) | `node_stocks`, `_flow_exec_counts`, `_pool_state` | `node_stocks` | — |
| `_inject_bar_data()` | L179-184 | — | `node_stocks`, bar_data参数 | `node_stocks`(更新行情) | — |
| `_tick()` | L400-406 | — | `node_stocks`, `_event_queue`, `_signal_queue` | `node_stocks`, `_pk_rankings`, `_dashboard_data`, `_alert_events` | — |
| `run_pool()` | L465-468 | — | — | `node_stocks`(初始化) | — |
| `run_mode(mode_id)` | L407-424 | runtime_modes,time_sources,trade_interfaces | `_current_time_source`, `_trade_interface` | `_current_time_source`, `_trade_interface` | — |
| `start_loop()` | L441-446 | — | — | — | — |
| `step_once()` | L447-464 | — | — | — | — |

### 2.2 native/builtins.py

| 函数 | 行号 | R:CONFIG | R:RUNTIME | W:RUNTIME | W:PERSIST |
|------|------|----------|-----------|-----------|-----------|
| `_decode_formula_base64` | L8-19 | — | — | — | — |
| `_load_builtin_json(filename)` | L23-30 | `config/{filename}` | `_BUILTIN_JSON_CACHE` | `_BUILTIN_JSON_CACHE` | — |
| `_resolve_fallback(chain_name)` | L33-64 | fallback_chain.json | `_HANDLERS` | — | — |
| `_stock_code(s)` | re-export from `_market_utils` | — | — | — | — |
| `_propagate(src,tgt,stocks,mode)` | L93-101 | — | `node_stocks[src/tgt]` | `node_stocks[tgt]`(及src, is_move时) | — |
| **`_gen_sector_stocks(sector)`** | L139-158 | **mock_data.json(但随机逻辑硬编码)** | — | — | — |
| `_handler_registry` | L175 | — | `globals()` | `_handler_registry` | — |
| `_HANDLERS` | L177-205 | — | `globals()` | `_HANDLERS` | — |

### 2.3 native/builtins_filters.py

| 函数 | 行号 | R:CONFIG | R:RUNTIME | W:RUNTIME | W:PERSIST |
|------|------|----------|-----------|-----------|-----------|
| `_load_json(filename)` | L5-10 | `config/{filename}` | `_json_cache` | `_json_cache` | — |
| `_gen_stock_codes(market)` | L11-25 | markets.json | — | — | — |
| `_filter_by_bar_data(stocks,bar)` | L26-30 | — | `bar_data` | — | — |
| `_decode_type201_attr(attr)` | L31-44 | field_definitions.json | — | — | — |
| `_decode_action(action)` | L45-66 | — | — | — | — |
| `_apply_formula_mode(mode,formula)` | L67-80 | formula_modes.json | — | — | — |
| `_build_dzh_extra(dzh_extra)` | L81-92 | dzh_extra_fields.json | — | — | — |
| `stock_pool_hold(stocks,tq)` | L101-118 | — | `tq_adapter.snapshots` | — | — |
| `_topn_filter(stocks,n,mode)` | L119-123 | — | — | — | — |
| `transfer_condition_check(...)` | L124-152 | field_definitions.json(间接) | `node_stocks`, bar_data, tq_adapter | — | — |
| `resolve_market(stocks,tq)` | L153-172 | markets.json(间接) | tq_adapter | — | — |
| `discard_sink_drop(...)` | L173-187 | — | — | — | — |
| `time_trigger_check(...)` | L188-191 | — | — | — | — |
| `profit_analysis_calc(...)` | L192-223 | analysis_config.json | tq_adapter.snapshots/kdata | — | — |
| `_calc_mock_field(snap,field)` | L224-231 | — | — | — | — |
| `_calc_field_from_formula(snap,f)` | L232-248 | — | `snap`(行情) | — | — |
| `_calc_aggregate_field_from_formula(...)` | L249-258 | — | `snapshots,kline_data` | — | — |
| `formula_eval(stocks,...)` | L262-311 | formula_modes,dzh_extra_fields,fallback_chain | tq_adapter, bar_data | — | — |
| `sector_filter(stocks,...)` | L312-321 | fallback_chain.json | tq_adapter | — | — |
| `cross_section_eval(stocks,...)` | L322-340 | fallback_chain.json | tq_adapter | — | — |
| `basic_filter(stocks,...)` | L341-358 | fallback_chain.json | tq_adapter, tq.financial_data | — | — |
| `condition_dispatcher(...)` | L361-399 | fallback_chain.json | dispatch_index, tq_adapter | — | — |

### 2.4 native/builtins_actions.py

| 函数 | 行号 | R:CONFIG | R:RUNTIME | W:RUNTIME | W:PERSIST |
|------|------|----------|-----------|-----------|-----------|
| `stage_tracker` | — | — | node_stocks, tq_adapter | `_tracker_snapshots` | — |
| `stage_signal` | — | signal_rules.json | — | `_signal_queue` | — |
| `stage_order` | — | — | — | — | — |
| `stage_alert` | — | alert_rules.json | — | `_alert_events` | — |
| `accumulate_state` | — | — | node_stocks | node_stocks | — |

### 2.5 native/builtins_post_tick.py

| 函数 | 行号 | R:CONFIG | R:RUNTIME | W:RUNTIME | W:PERSIST |
|------|------|----------|-----------|-----------|-----------|
| `stage_dashboard` | — | dashboard_schema.json | `_pk_rankings,_angle_results` | `_dashboard_data` | — |
| `stage_alerts` | — | alert_rules.json | `_alert_events` | `_alert_queue` | — |
| `stage_tracker_update` | — | tracker_schema.json | node_stocks | `_exit_tracker_cache` | — |

### 2.6 app.py

| 函数 | 行号 | R:CONFIG | R:RUNTIME | W:RUNTIME | W:PERSIST |
|------|------|----------|-----------|-----------|-----------|
| `lifespan(app)` | L29-36 | — | — | `app.state.engine/storage/tq` | SQLite DB(Storage构造) |
| `tdx_list_pools()` | L48-53 | — | — | — | — |
| `tdx_load_pool(name)` | L56-64 | — | — | — | — |
| `tdx_export_xml(request)` | L67-74 | — | — | — | tempfile .xml |
| **`tdx_create_pool(request)`** | L77-91 | — | — | — | tdxpool/{name}.xml, Storage.save_pool |
| `tdx_execute_pool(request)` | L94-113 | — | `engine._on_stock_enter_target_pool` | 同左(临时) | — |
| `tdx_delete_pool(name)` | L116-126 | — | — | — | 删除xml+png |
| **`tdx_save_pool(name,request)`** | L129-145 | — | — | — | tdxpool/{name}.xml, Storage.save_pool |
| `registry_generic(reg_name)` | L155-161 | cell_type_registry/modules/dzh_type_map/defaults/flow_mode_registry/edge_strategies | — | — | — |
| `tdx_import_file(request)` | L167-187 | — | — | — | tempfile(删) |
| `list_history_dates(...)` | L219-224 | — | — | — | — |
| `get_pk_rankings(request)` | L270-271 | — | `engine._pk_rankings` | — | — |
| `get_dashboard(request)` | L273-274 | — | `engine._dashboard_data` | — | — |
| `get_alerts(request)` | L276-277 | — | `engine._alert_queue._queue` | — | — |

### 2.7 tdx_evaluators.py

| 函数 | 行号 | R:CONFIG | R:RUNTIME | W:RUNTIME | W:PERSIST |
|------|------|----------|-----------|-----------|-----------|
| `_build_formula_arg(func)` | L51-53 | tdx_indicators.json(`_indicators_data`) | — | — | — |
| `eval_nset0_indicator(...)` | L61-98 | tdx_indicators.json(间接) | `tq_adapter.formula_process_mul_zb()` | — | — |
| `eval_nset1_condition_formula(...)` | L99-120 | — | `tq_adapter.formula_process_mul_xg()` | — | — |
| `eval_nset2_expert_system(...)` | L121-149 | — | `tq_adapter.formula_exp()` | — | — |
| **`eval_nset3_financial_scalar(...)`** | L150-178 | tdx_ntjindexno_lookup.json(`_financial_fields`), **`_STOCK_INFO_FIELD_MAP`硬编码** | `tq_adapter.get_stock_info/get_financial_data` | — | — |
| **`eval_nset4_rank_compare(...)`** | L179-215 | — | `tq_adapter` | — | — |
| **`eval_nset5_price_compare(...)`** | L216-247 | — | `tq_adapter.get_kline` | — | — |
| `eval_nset6_hold_check(...)` | L248-271 | — | node_stocks, tracker | — | — |

### 2.8 runtime_simulator.py

| 函数 | 行号 | R:CONFIG | R:RUNTIME | W:RUNTIME | W:PERSIST | 与engine重复 |
|------|------|----------|-----------|-----------|-----------|-------------|
| `_scode(s)` | L38-41 | — | — | — | — | **是** = engine._stock_code |
| `StatePool.__init__` | L68-76 | — | — | `stocks,stock_expiry` | — | **TTL逻辑重复engine** |
| `StatePool.add/remove/clear` | L78-92 | — | `stocks,stock_expiry` | 同左 | — | **= engine._execute_flowsCore** |
| `StatePool.get_expired/cleanup` | L94-99 | — | `stock_expiry` | `stocks` | — | **= engine._apply_tdx_psatt_ttl** |
| `RuntimeSimulator.__init__` | L146-169 | timing,flow_mode_registry,mock_data | — | `pools,event_log,_mode_state` | — | **重复加载engine已加载的配置** |
| `_build_pool_config()` | L174-199 | — | `self.pool` | — | — | 部分重复engine.run_pool |
| `_run_coro()` | L204-234 | — | — | — | — | **= kline_replay._run_coro_sync** |
| `_all_node_ids()` | L292-313 | — | `pool_config.nodes/edges` | — | — | **= engine._tick** |
| `_generate_mock_bar_data()` | L315-343 | mock_data.json(`_gn`) | `node_stocks` | — | — | 部分重复engine._inject_bar_data |
| `initialize()` | L348-357 | — | — | `_mode_state,_engine._current_time_source` | — | **绕过engine.run_mode直接设** |
| `step()` | L359-383 | — | `clock,node_stocks,event_queue` | 同左 | — | **调engine._tick(已委托)** |

### 2.9 kline_replay_engine.py

| 函数 | 行号 | R:CONFIG | R:RUNTIME | W:RUNTIME | W:PERSIST | 与engine重复 |
|------|------|----------|-----------|-----------|-----------|-------------|
| `_do_step()` | — | — | `_current_bar_time` | 同左 | — | 委托engine._tick(正确) |
| `_run_coro_sync()` | — | — | — | — | — | **= runtime_simulator._run_coro** |

---

## 三、硬编码分支清单（按严重度排序）

### 🔴 严重 — 应进配置表而未进（反模式）

| # | 位置 | 行号 | 硬编码内容 | 应进哪张表 | 说明 |
|---|------|------|-----------|-----------|------|
| **H1** | engine.py `_emit_transfer_events` | L165-200 | `_role_handlers = {"candidate":..., "accumulated":..., "alert":..., "target":...}` | **pool_roles.json** → `role_resolution.rules` | 已加载pool_roles.json但未使用其handler映射，是最严重的反模式 |
| **H2** | engine.py `_resolve_flow_attrs` | L132-155 | `DynamicFlowModel.from_int(attr_int)` 硬编码位标志解码 | **field_definitions.json** → `flow_fields.attr.bit_fields` | field_definitions.json已有bit_fields定义但此函数未查表 |
| **H3** | tdx_evaluators.py `eval_nset3` | L150-178 | `if nset==3` → mock降级分支 | **fallback_chain.json** | nset3/4/5各自有独立mock降级逻辑，未走统一降级链 |
| **H4** | tdx_evaluators.py `eval_nset4` | L179-215 | `if nset==4` → mock降级分支 | **fallback_chain.json** | 同上 |
| **H5** | tdx_evaluators.py `eval_nset5` | L216-247 | `if nset==5` → mock降级分支 | **fallback_chain.json** | 同上 |
| **H6** | tdx_evaluators.py 模块级 | L14-27 | `_STOCK_INFO_FIELD_MAP = {"name":..., "industry":..., ...}` | **field_definitions.json** 或 **data_source_mappings.json** | 财务字段映射硬编码在Python中 |
| **H7** | app.py 模块级 | — | `_STOCK_NAMES = {}` 全局缓存 | **应从data_config.json或storage查表** | 全局可变状态，非线程安全 |
| **H8** | runtime_simulator.py `StatePool` | L68-76 | `hold_seconds=432000` (5天) | **timing.json** → `simulator.default_hold_seconds` | 与field_definitions.json中type200.hold.default重复 |
| **H9** | builtins.py `_gen_sector_stocks` | L139-158 | 随机生成逻辑: `random.sample(range(600000,605000), count)` | **mock_data.json** → `generator.sector_stocks.rules` | 已读mock_data.json但随机范围硬编码 |

### 🟡 中等 — 非核心业务逻辑散落在引擎代码中

| # | 位置 | 行号 | 硬编码内容 | 建议剥离到 |
|---|------|------|-----------|-----------|
| **H10** | engine.py `_tdx_should_execute` | L108-131 | `starttype_rules` 的eval编译和缓存 | timing.json编译逻辑应统一到__init__ |
| **H11** | builtins.py `_handler_registry` / `_HANDLERS` | L175, L177-205 | 从globals()构建的handler注册表 | **action_table.json** 或独立registry配置 |
| **H12** | builtins_filters.py `_decode_action` | L45-66 | action参数解码的位运算逻辑 | **field_definitions.json** → action_fields |
| **H13** | builtins_filters.py `_calc_mock_field` | L224-231 | mock字段计算: `if field.startswith("price") → random.uniform(...)` | **mock_data.json** → `generator.mock_field_rules` |
| **H14** | app.py `tdx_execute_pool` | L94-113 | `engine._on_stock_enter_target_pool = callback` 临时覆盖 | 应通过事件系统而非直接赋值 |
| **H15** | api/dzh_api.py | — | 全局单例 `_dzh_api` | 应改为 `app.state` 共享 |

### 🟢 轻微 — 代码风格/可维护性问题

| # | 位置 | 说明 |
|---|------|------|
| **H16** | runtime_simulator.py `_scode(s)` | 与engine.py `_stock_code(s)` 完全重复，应复用 |
| **H17** | runtime_simulator.py `_run_coro()` | 与kline_replay_engine.py `_run_coro_sync()` 重复 |
| **H18** | builtins.py + builtins_filters.py | 两个模块各自有 `_load_json` / `_load_builtin_json` + 缓存，应统一 |
| **H19** | runtime_simulator.py `StatePool` | 整个类与engine.py的流转逻辑重复，应删除 |
| **H20** | app.py `registry_generic` | `_REGISTRY_FILES` 字典硬编码reg_name→filename映射，应进配置 |

---

## 四、运行时表清单（内存Dict）

### 4.1 engine.py 管理的运行时表

| 表名 | 类型 | 初始化位置 | 生命周期 |
|------|------|-----------|---------|
| `node_stocks` | Dict[node_id→Set[stock]] | `_init_node_stocks` / `run_pool` | 池运行期间 |
| `_pool_state` | Dict | `run_pool` | 池运行期间 |
| `_flow_duration_starts` | Dict[flow_id→timestamp] | `__init__` | 引擎实例 |
| `_flow_exec_counts` | Dict[flow_id→int] | `__init__` | 引擎实例 |
| `_pool_start_time` | float | `run_pool` | 池运行期间 |
| `_event_queue` | asyncio.Queue | `__init__` | 引擎实例 |
| `_signal_queue` | asyncio.Queue | `__init__` | 引擎实例 |
| `_data_cache` | LRUCache | `__init__` | 引擎实例 |
| `_compiled_timing` | Dict[edge_id→compiled_eval] | `__init__` | 引擎实例 |
| `_compiled_tracker` | Dict | `__init__` | 引擎实例 |
| `_pk_rankings` | Dict | `__init__` / post_tick更新 | 引擎实例 |
| `_angle_results` | Dict | `__init__` / post_tick更新 | 引擎实例 |
| `_dashboard_data` | Dict | `__init__` / post_tick更新 | 引擎实例 |
| `_alert_events` | List | `__init__` / post_tick更新 | 引擎实例 |
| `_alert_cooldown` | Dict | `__init__` | 引擎实例 |
| `_exit_tracker_cache` | Dict | `__init__` | 引擎实例 |
| `_current_time_source` | str | `run_mode` | 引擎实例 |
| `_trade_interface` | str | `run_mode` | 引擎实例 |
| `tables` | Dict[filename→JSON] | `__init__` | 引擎实例(只读) |
| `module_map` | Dict | `__init__` | 引擎实例(只读) |
| `engine_index` | Dict | `__init__` | 引擎实例(只读) |
| `dispatch_index` | Dict | `__init__` | 引擎实例(只读) |
| `_nset_dispatch` | Dict | `__init__` | 引擎实例(只读) |
| `_tracker_schema` | Dict | `__init__` | 引擎实例(只读) |
| `_tracker_fields` | Dict | `__init__` | 引擎实例(只读) |
| `_tracker_formulas` | Dict | `__init__` | 引擎实例(只读) |

### 4.2 runtime_simulator.py 管理的运行时表（与engine重复）

| 表名 | 说明 | 重复对象 |
|------|------|---------|
| `StatePool.stocks` | 节点股票集合 | engine.node_stocks |
| `StatePool.stock_expiry` | 股票TTL过期时间 | engine._apply_tdx_psatt_ttl逻辑 |
| `_mode_state["node_stocks"]` | 另一份节点股票 | engine.node_stocks |
| `event_log` | 事件日志 | engine._event_queue |
| `clock` | 虚拟时钟 | engine._current_time_source |

---

## 五、关键发现与建议

### 5.1 最大的反模式: `_role_handlers` 硬编码字典

```python
# engine.py _emit_transfer_events() 中
_role_handlers = {
    "candidate":  self._handle_candidate_enter,
    "accumulated": self._handle_accumulated_enter,
    "alert":      self._handle_alert_enter,
    "target":     self._handle_target_enter,
}
```

**问题**: pool_roles.json 已被加载到 `self._pool_roles`，但此函数未使用它来解析handler。每次新增角色都需改engine.py。

**建议**: pool_roles.json 增加 `role_resolution.rules` 结构:
```json
{
  "role_resolution": {
    "rules": {
      "candidate":  {"handler_module": "builtins_actions", "handler_func": "stage_tracker"},
      "accumulated": {"handler_module": "builtins_actions", "handler_func": "accumulate_state"},
      "alert":      {"handler_module": "builtins_actions", "handler_func": "stage_alert"},
      "target":     {"handler_module": "builtins_actions", "handler_func": "stage_signal"}
    }
  }
}
```

### 5.2 runtime_simulator.py 大面积重复

RuntimeSimulator 中 `StatePool` 类 (L68-114) 完整重复了 engine.py 的以下逻辑:
- 股票添加/移除/清空 = `_execute_flowsCore` 中的 `_propagate` 调用
- TTL过期检查/清理 = `_apply_tdx_psatt_ttl`
- 节点ID提取 = `_tick` 中的 nodes/edges 解析

**建议**: 删除 `StatePool`，step() 直接委托 `engine._tick()`，仅保留 MockStock 生成和 virtual_clock 管理。

### 5.3 nset3/4/5 mock降级未走fallback_chain

tdx_evaluators.py 中 nset3/4/5 各自包含独立的mock降级逻辑，而 builtins.py 已有 `_resolve_fallback()` 机制读取 fallback_chain.json。

**建议**: 在 fallback_chain.json 增加 nset3/4/5 降级链条目，eval_nset3/4/5 统一调用 `_resolve_fallback("nset3")` 等。

### 5.4 _STOCK_NAMES 全局变量

app.py 中 `_STOCK_NAMES = {}` 作为全局缓存，非线程安全且不随引擎实例生命周期。

**建议**: 改为从 `engine._data_cache` 或 Storage 查表获取。

### 5.5 未被引用的14张配置表

以下配置表存在但未被任何核心代码直接引用，可能是遗留或计划中的：
data_providers, data_source_mappings, fields, history_schema, match_modes, operators, pool_types, property_ownership, schedule_resolvers, table_schemas, tdx_enums, tdx_field_visibility, ui_components, xml_mapping

---

## 六、精炼优先级

| 优先级 | 编号 | 操作 | 影响范围 | 状态 |
|--------|------|------|---------|------|
| P0 | H1 | `_role_handlers` → pool_roles.json | engine.py | ✅ 已使用pool_roles.json的role_resolution.rules，handler为规则匹配器非业务逻辑 |
| P0 | H2 | `_resolve_flow_attrs` → 查field_definitions.json | engine.py | ✅ 已通过schemas.py的_parse_attr_bits("flow")查field_definitions.json |
| P0 | H19 | 删除StatePool，RuntimeSimulator委托engine._tick | runtime_simulator.py | 🔲 待执行(阶段二) |
| P1 | H3-H5 | nset3/4/5降级 → fallback_chain.json | tdx_evaluators.py | ✅ 已增加nset3/4/5降级链+eval函数fallback逻辑 |
| P1 | H6 | `_STOCK_INFO_FIELD_MAP` → data_source_mappings.json | tdx_evaluators.py | ✅ 已移入data_source_mappings.json的stock_info_field_map |
| P1 | H7 | `_STOCK_NAMES` → 查表获取 | app.py | ✅ 确认app.py中不存在此变量，已被移除 |
| P1 | H9 | `_gen_sector_stocks`随机范围 → mock_data.json | builtins.py | ✅ 已使用mock_data.json的sector_generation_rules |
| P2 | H8 | `hold_seconds=432000` → timing.json | runtime_simulator.py | 🔲 待执行(阶段二) |
| P2 | H13 | `_calc_mock_field` → mock_data.json | builtins_filters.py | 🔲 待执行 |
| P2 | H15 | dzh_api全局单例 → app.state | api/dzh_api.py | 🔲 待执行(阶段二) |
| P2 | H14 | `engine._on_stock_enter_target_pool` → 事件系统 | app.py | 🔲 待执行(阶段二) |
| P3 | H16-H18, H20 | 代码重复消除 | 多文件 | 🔲 待执行 |
