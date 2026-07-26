# DESIGN — 执行流视角的表操作映射

## 1. 一句话本质

**股票池 = 有向图上的逐边条件过滤。** 每条边 = gate→filter→propagate→callback→ttl。

### 1.1 前端架构边界

前端仅作为**展示层**，不参与业务真值计算。后端持有全部业务真值源，前端通过两条唯一路径与后端交互：

| 职责 | 前端 | 后端 |
|------|------|------|
| 业务真值源 | ❌ 不持有 | ✅ 股票池节点、运行时状态、事件队列、计时器队列、模式状态 |
| UI 渲染 | ✅ 按配置表渲染 | ❌ 不参与 |
| 用户输入 | ✅ 转发到后端 API | ✅ 处理并更新真值源 |
| 事件订阅 | ✅ 订阅 SSE `/api/events/stream` | ✅ 通过 `core/event_bus.py` 发布事件契约 |
| 纯界面状态 | ✅ 面板折叠/缩放/选中/滚动位置 | ❌ 不持有 |

**表驱动 UI 唯一路径**：所有 UI 组件、布局、字段、动作统一由 `config/ui/*.json` 配置表驱动，禁止硬编码节点类型分支或写死菜单条目。

**事件驱动唯一路径**：前端事件入口唯一为 `EventSource('/api/events/stream')`，事件格式与 `core/event_bus.py` 事件契约对齐；禁止前端伪造事件、独立维护事件队列真值源。

**模式一致性**：设计/仿真/回放/实盘四种模式共享同一条执行路径，差异仅在于数据源与时间推进机制（`runtime_modes.json` + `time_sources.json`）。

---

## 2. 加载配置

| 步骤 | 读配置表 | 写运行时索引 |
|------|---------|-------------|
| 加载JSON | config/*.json（33张） | self.tables |
| 建立模块索引 | modules.json | self.module_map: Dict[id→module] |
| 建立引擎索引 | engines.json | self.engine_index: Dict[id→engine] |
| 建立边策略索引 | edge_strategies.json | self._edge_strategies + self._node_init |
| 建立条件分发索引 | dispatch.json | self.dispatch_index |
| 建立 nset 分发表 | dispatch.json（nset_dispatch 子表） | self._nset_dispatch |
| 建立时机规则缓存 | timing.json | self._timing_cfg |
| 建立TTL规则缓存 | tdx_psatt.json | self._psatt_cfg |
| 建立DZH类型映射 | dzh_type_map.json | self._dzh_type_map |
| 建立参数别名 | defaults.json | self._param_aliases |
| 注册handler | behavior_actions.json + builtins.py 反射 | self._handler_registry |
| 加载池配置 | pool_config（持久表，params JSON） | 无（按需读取） |
| 热加载检测 | 配置表文件hash | config_version（持久表 INSERT） |

> 代码位置: engine.py L25-59

---

## 3. 初始化节点库存

| 节点类型 | 读配置表 | 写运行时表 | handler |
|---------|---------|-----------|---------|
| market_source | edge_strategies.json:node_init | node_stocks[nid] | init_market_source |
| stock_state_pool | edge_strategies.json:node_init | node_stocks[nid] | init_stock_state_pool |
| tdx_candidate | edge_strategies.json:node_init | node_stocks[nid] | init_tdx_candidate |
| 其他类型 | 无 | node_stocks[nid] = [] | 无 |

> 代码位置: engine.py L86-92 `_init_node_stocks`

---

## 4. 逐边执行——核心中的核心

### 4.1 gate（时机门控）— 读 timing.json 配置表

24种时机 = starttype(8) × cxtype(3)，已全部通过 timing.json 配置表驱动。

| starttype | timing.json evaluate | 读运行时表 |
|-----------|---------------------|-----------|
| 0 立即 | always | 无 |
| 1 延迟N秒 | elapsed_gte | _pool_start_time |
| 2 开市前 | in_range | 无（比较当前时间） |
| 3 开市后 | gte | 无（比较当前时间） |
| 4 收市前 | in_range | 无（比较当前时间） |
| 5 收市后 | gte | 无（比较当前时间） |
| 6 指定交易时间 | gte_hhmmss | 无（比较当前时间） |
| 7 指定时间 | gte_hhmmss | 无（比较当前时间） |

| cxtype | timing.json is_expired | 读运行时表 | 写运行时表 |
|--------|------------------------|-----------|-----------|
| 0 一直 | never | 无 | 无 |
| 1 持续窗口 | elapsed_gte | _flow_duration_starts[eid] | _flow_duration_starts（首次记录） |
| 2 只一次 | count_gte_1 | _flow_exec_counts[eid] | 无（写在执行完成后） |

> 代码位置: engine.py L112-172 `_tdx_should_execute` + `_tdx_check_duration`

### 4.2 filter（强弱对比）— 读 dispatch.json + engines.json 配置表

二分法本质：不管什么条件，最终都是对每只股票求值 → 与阈值比较 → passed 或 rejected。

策略路由链路：
```
edge_strategies.json 查策略 → strategy.handler 指向 builtins.py 函数
  → 函数内部调用 condition_dispatcher
    → condition_dispatcher 查 dispatch.json 做位掩码路由
      → 路由到 engines.json 中的网关
        → 网关调用 formula_eval / cross_section_eval / basic_filter / pass_through

TDX 条件分发链路（_dispatch_tdx_condition）：
dispatch.json 的 nset_dispatch 子表 → 按 nset 查表获取 evaluator 名称
  → getattr(tdx_evaluators, evaluator_name) 动态调用对应评估器
  → 无硬编码字典，新增 nset 只改 JSON
```

filter 函数速查：

| filter 函数 | "强"的定义 | 读配置表 | 外部依赖 | 写入 |
|------------|-----------|---------|---------|------|
| formula_eval | 公式值>0 | dispatch.json + engines.json | tq_adapter.eval_indicator | 无（返回值） |
| _filter_by_bar_data | code in bar_data（成员测试） | 无 | current_bar_data | 无（返回值） |
| basic_filter | PE/ROE达标 | dispatch.json + engines.json | tq_adapter | 无（返回值） |
| cross_section_eval | 截面评分排序 | dispatch.json + engines.json | tq_adapter | 无（返回值） |
| condition_dispatcher(AND) | 多条件同时满足 | dispatch.json 链 | tq_adapter | 无（返回值） |
| condition_dispatcher(OR) | 任一条件满足 | dispatch.json 链 | tq_adapter | 无（返回值） |
| tdx_condition_evaluator | TDX公式通过 | tdx_indicators.json | tq_adapter | 写 node_stocks[tid] |
| pass_through | 全部通过 | 无 | 无 | 无（返回值） |

### 4.3 propagate（状态流转）— 读写运行时表 node_stocks

| 模式 | 读运行时表 | 写运行时表 | TDX tran参数 |
|------|-----------|-----------|-------------|
| copy | node_stocks[src] | node_stocks[tgt] += passed | tran=0 |
| move | node_stocks[src] | node_stocks[tgt] += passed; node_stocks[src]=[] | tran=1 |
| overwrite | node_stocks[src] | node_stocks[tgt] = passed | emptyps + clear_dest_first |

> 代码位置: builtins.py L1408 `_propagate`

### 4.4 callback（持久化+副作用）— 读 action_table.json 配置表

回调由 app.py 动态注入 `engine._on_stock_enter_target_pool`，读取 action_table.json 查表执行。
`_on_enter` 函数遍历 `pool_enter_actions`，按 `trigger` 条件匹配，通过 `args` 列表动态组装参数：
- `$xxx` → 上下文变量（如 `$pool_name`、`$node_id`、`$node`、`$new_stocks`）
- `key:type` → 从 psatt 提取并转换类型（如 `nsoundtype:int`、`blockfile:str`）
- `log_on_success` → 执行成功后的日志模板，支持 `{$xxx}` 变量替换

| 动作 | 读表 | 写表 | handler | args |
|------|------|------|---------|------|
| bsavehis | node_stocks[tgt] + node.params | .dat/.log文件 + node_state（INSERT） | _append_history_entry | $pool_name, $node_id, $node, $new_stocks |
| bsound | node.params.tdx_psatt.nsoundtype | 播放声音 | _play_sound_alert | nsoundtype:int, soundfile:str, $node_id, $new_stocks |
| btip | node.params.tdx_psatt | 弹窗通知 | _show_popup_alert | $node_id, $new_stocks |
| bsavetoblock | node.params.tdx_psatt.blockfile + node_stocks[tgt] | 板块文件 | _save_to_tdx_block | blockfile:str, $new_stocks, bclearblock:int |

> 代码位置: app.py L169-228

### 4.5 ttl（超时淘汰）— 读 tdx_psatt.json 配置表

| 条件 | 读表 | 写表 |
|------|------|------|
| bdel=1 且超时 | node_stocks[tgt]（每只的indate+intime）+ tdx_psatt.json（ttl_units） | node_stocks[tgt]（删除超时股票） |

TTL计算：
```
unit_sec = ttl_units[ndeltype]   # 0=天,1=小时,2=分钟,3=秒
ttl_sec = ndelnum × unit_sec
entry_ts = _parse_intime_to_ts(indate, intime)  # intime 归一化为 6 位 HHMMSS
if now_ts - entry_ts >= ttl_sec: 删除股票
```

> 代码位置: engine.py L174-235 `_apply_tdx_psatt_ttl`

---

## 5. 全功能表操作速查矩阵

二维矩阵：功能步骤 × 四类表（运行时读/运行时写/配置读/持久写）。

| 功能步骤 | 读运行时表 | 写运行时表 | 读配置表 | 写持久表 |
|---------|-----------|-----------|---------|---------|
| gate（时机门控） | _flow_duration_starts, _flow_exec_counts, _pool_start_time | _flow_duration_starts（首次）, _flow_exec_counts（每次+1） | timing.json | — |
| filter（强弱筛选） | node_stocks[src] | — | dispatch.json, engines.json, tdx_indicators.json | — |
| propagate（状态流转） | node_stocks[src], node_stocks[tgt] | node_stocks[tgt], node_stocks[src]（move时清空） | — | — |
| callback（持久化副作用） | node_stocks[tgt], node.params | — | action_table.json | node_state, stock_transfer_log, .dat/.log文件, 板块文件 |
| ttl（超时淘汰） | node_stocks[tgt] | node_stocks[tgt]（删除超时股票） | tdx_psatt.json | — |
| init（节点初始化） | — | node_stocks[nid] | edge_strategies.json:node_init | — |
| output（结果输出） | node_stocks | — | edge_strategies.json:output_types | — |
| replay（回放执行） | _state_pools, _timeline, _flow_fire_counts | _state_pools | 同逐边执行（全部配置表） | replay_snapshot, replay_session, stock_transfer_log |
| CRUD（配置管理） | — | — | — | pool_config, pool_node, pool_edge, config_version |

---

## 6. 时机控制

### 策略空间 = 时机轴 × 强弱轴

- **时机轴**: starttype(0~7) × cxtype(0~2) = 24 种时机组合
- **强弱轴**: nset × noperate × ntjindexno = 多种筛选组合
- 所有选股策略都是策略空间中的点

### starttype × cxtype 组合矩阵

| | cxtype=0 一直 | cxtype=1 持续窗口 | cxtype=2 只一次 |
|--|-------------|-----------------|-------------------|
| starttype=0 立即 | 每次执行都触发 | 窗口期内每次触发 | 只触发一次 |
| starttype=1 延迟 | 延迟后每次触发 | 延迟后窗口内触发 | 延迟后触发一次 |
| starttype=2 开市前 | 每天开市前触发 | 开市前窗口内触发 | 开市前触发一次 |
| starttype=3 开市后 | 开市后每次触发 | 开市后窗口内触发 | 开市后触发一次 |
| starttype=4 收市前 | 收市前每次触发 | 收市前窗口内触发 | 收市前触发一次 |
| starttype=5 收市后 | 收市后每次触发 | 收市后窗口内触发 | 收市后触发一次 |
| starttype=6 指定时间 | 到时间后每次触发 | 到时间后窗口内触发 | 到时间触发一次 |
| starttype=7 指定时间 | 到时间后每次触发 | 到时间后窗口内触发 | 到时间触发一次 |

### 策略示例

| 策略 | 时机参数 | 强弱参数 | 效果 |
|------|---------|---------|------|
| 开盘扫阳线 | starttype=3, starttime=30 | _filter_by_bar_data | 开市后30秒执行，bar_data 成员测试通过 |
| 尾盘选低PE | starttype=4, starttime=5 | basic_filter + sorttype=10 | 收市前5分钟，PE最低前10只 |
| 一次性公式选股 | starttype=0, cxtype=2 | formula_eval | 立即执行公式，只执行一次 |

### 运行时表读取

| 判断 | 读运行时表 | 计算公式 |
|------|-----------|---------|
| 延迟是否到期 | _pool_start_time | (now - _pool_start_time).total_seconds() >= delay |
| 持续窗口是否到期 | _flow_duration_starts[edge_id] | (now - _flow_duration_starts[edge_id]).total_seconds() >= window |
| 是否已执行过 | _flow_exec_counts[edge_id] | _flow_exec_counts[edge_id] >= 1 |

---

## 7. 强弱对比

filter 函数的二分法本质：输入一个股票列表，输出两个列表——passed（强者）和 rejected（弱者）。

| "强"的定义 | filter 函数 | 判定逻辑 | 外部依赖 |
|-----------|-----------|---------|---------|
| 公式值为正 | formula_eval() | eval_indicator(code, formula) > 0 | tq_adapter |
| code in bar_data | _filter_by_bar_data() | _stock_code(s) in bar_data | current_bar_data |
| PE低于阈值 | basic_filter() | pe <= pe_max | tq_adapter |
| 排名靠前 | sorttype 截断 | passed[:top_n] | 无 |
| 多条件同时满足 | condition_dispatcher() AND | 交集 | tq_adapter |
| 任一条件满足 | condition_dispatcher() OR | 并集 | tq_adapter |
| 截面评分高 | cross_section_eval() | 排序取前半 | tq_adapter |
| TDX公式通过 | tdx_condition_evaluator() | eval_indicator > 0 | tq_adapter |
| 全部通过 | pass_through() | 无筛选 | 无 |

### noperate 比较表

| noperate | 比较逻辑 | 适用场景 |
|----------|---------|---------|
| 0 等于 | val == nsecond | 固定值匹配 |
| 1 大于 | val > nsecond | 阈值以上 |
| 2 小于 | val < nsecond | 阈值以下 |
| 3 上穿 | prev <= nsecond AND curr > nsecond | 金叉信号 |
| 4 下穿 | prev >= nsecond AND curr < nsecond | 死叉信号 |
| 5 排序 | sort by val desc, take nsecond | 龙头股 |
| 6 排序前N | sort by val desc, take nsecond | 涨幅前N |
| 7 排序后N | sort by val asc, take nsecond | 跌幅前N |
| 8 拐点向上 | diff(val) > 0 | 趋势反转 |
| 9 拐点向下 | diff(val) < 0 | 趋势反转 |

---

## 8. 持有退出（TTL）

| 事件 | 触发条件 | 读运行时表 | 写运行时表 |
|------|---------|-----------|-----------|
| TTL淘汰 | psatt.bdel=1 且超时 | node_stocks | node_stocks（删除超时股票） |

TTL 参数来源：`node.params.tdx_psatt` 或 `node.params.psatt`

| 参数 | 含义 |
|------|------|
| bdel | 是否启用自动删除（1=启用） |
| ndelnum | 删除阈值数量 |
| ndeltype | 阈值单位（0=天, 1=小时, 2=分钟, 3=秒） |

6 种 psatt 副作用：

| 标志 | 含义 | 动作 |
|------|------|------|
| bdel=1 | 启用TTL | 定时删除超时股票 |
| bsound=1 | 声音预警 | 播放 nsoundtype 指定声音 |
| btip=1 | 弹窗提示 | 弹出股票入池通知 |
| bsavehis=1 | 保存历史 | 写入 .dat/.log 文件 |
| bsavetoblock=1 | 保存板块 | 保存到板块文件 |
| baimpool=1 | 标记目标池 | 高亮目标池 |

---

## 9. 回放执行

| 步骤 | 读表 | 写表 |
|------|------|------|
| 加载K线 | kline_cache（持久表）或 tq_adapter.get_kline_batch | 无 |
| 注入bar数据 | _timeline[current_index] | _state_pools（source节点） |
| 时间调度 | edge.params + _flow_fire_counts + _flow_last_fire | 无 |
| 执行flows | 同逐边执行 | 同逐边执行 |
| 记录转移 | _state_pools | stock_transfer_log（批量 INSERT） |
| 入池时间 | _state_pools | _stock_enter_times |
| 持有淘汰 | _stock_enter_times + params.hold_sec | _state_pools（删除过期） |
| 保存快照 | _state_pools | replay_snapshot（INSERT） |
| 更新会话 | 无 | replay_session（UPDATE kline_index, status） |

每根K线的表操作序列：写 replay_snapshot + 写 replay_session + 写 stock_transfer_log（N条）

> 代码位置: kline_replay_engine.py

---

## 10. 配置CRUD

| 功能 | 方法 | 读持久表 | 写持久表 | 本质 |
|------|------|---------|---------|------|
| 创建池 | storage.save_pool() | 无 | pool_config（INSERT）+ config_version（INSERT） | 写图拓扑到 params JSON |
| 读取池 | storage.get_pool() | pool_config | 无 | 加载图拓扑 |
| 更新池 | storage.save_pool() | pool_config（旧值） | pool_config（UPSERT）+ config_version（INSERT） | 覆盖图拓扑 |
| 删除池 | storage.delete_pool() | pool_config（旧值） | pool_config（DELETE CASCADE）+ config_version（INSERT） | 删图+级联 |
| 列表 | storage.list_pools() | pool_config | 无 | 列出所有图 |
| 保存节点 | storage.save_pool_node() | pool_node（旧值） | pool_node（INSERT OR REPLACE）+ config_version | 写节点定义 |
| 保存边 | storage.save_pool_edge() | pool_edge（旧值） | pool_edge（INSERT OR REPLACE）+ config_version | 写边定义 |

> **注意**: 运行时引擎不直接查 pool_node/pool_edge 表。它从 pool_config.params JSON 中提取 nodes 和 edges 列表。

---

## 11. 核心函数速查表

| 函数 | 输入 | 输出 | 读配置表 | 读运行时表 | 写运行时表 | 写持久表 | 代码位置 |
|------|------|------|---------|-----------|-----------|---------|---------|
| _execute_flowsCore | nodes, edges, node_stocks, bar_data | (node_stocks, transfer_events) | edge_strategies, timing.json | node_stocks, _flow_duration_starts, _flow_exec_counts, _pool_start_time | node_stocks, _flow_exec_counts, _flow_duration_starts | — | engine.py L285 |
| _tdx_should_execute | edge | bool | timing.json（starttype_rules） | _pool_start_time | — | — | engine.py L112 |
| _tdx_check_duration | edge | bool | timing.json（cxtype_rules） | _flow_duration_starts, _flow_exec_counts | _flow_duration_starts（首次） | — | engine.py L150 |
| _propagate | node_stocks, src, tgt, is_move, is_overwrite | None | — | node_stocks[src], node_stocks[tgt] | node_stocks[tgt], node_stocks[src]（move时） | — | builtins.py L1408 |
| _apply_tdx_psatt_ttl | node_id, node, node_stocks | None | tdx_psatt.json（ttl_units, time_formats） | node_stocks[node_id] | node_stocks[node_id]（删除超时） | — | engine.py L174 |
| condition_dispatcher | stock_list, conditions, dispatch_index, engine_index | {passed, failed} | dispatch.json, engines.json | node_stocks（间接） | — | — | builtins.py L935 |
| formula_eval | stock_list, formula, tq_adapter | {passed, rejected} | engines.json | node_stocks（间接） | — | — | builtins.py L786 |
| _filter_by_bar_data | stock_list, current_bar_data | (passed, rejected) | — | 无（参数传入） | — | — | builtins.py L193 |
| tdx_condition_evaluator | node_stocks, nodes, sid, tid, tq_adapter | node_stocks | — | node_stocks[src] | node_stocks[tid] | — | builtins.py L1506 |
| edge_default_transfer | action_inputs, strategy | action_inputs | edge_strategies.json（pre_inject） | node_stocks（间接） | — | — | builtins.py L1538 |
| init_market_source | node, tq_adapter | stock_list | — | — | — | — | builtins.py L1786 |
| _on_stock_enter_target_pool | node_id, node_info, new_stocks | None | action_table.json（含 args + log_on_success） | node_stocks[tgt] | — | node_state, stock_transfer_log | app.py L169 |
| KLineReplayEngine.next_bar | 无 | result | 全部配置表 | _timeline, _state_pools | _state_pools | replay_snapshot, replay_session, stock_transfer_log | kline_replay_engine.py |
| Storage.save_pool | pool_id, pool_data | None | — | — | — | pool_config, config_version | storage.py |
| Storage.get_pool | pool_id | pool_data | — | — | — | — | storage.py |

---

## 12. 附录

### A. col 列ID → TQ字段映射

| col ID | 含义 | TQ字段 |
|--------|------|--------|
| 2 | 代码 | code |
| -1 | 名称 | name |
| -2 | 最新价 | close |
| -3 | 涨跌幅 | pct_change |
| -4 | 换手率 | turnover |
| -6 | 量比 | volume_ratio |
| 7 | 入池时间 | entered_at |
| 8 | 现价 | current_price |
| 10 | 收益率 | profit_rate |
| 14 | 入池价 | entry_price |
| 17 | 最大收益 | max_profit |
| 45 | 保留天数 | hold_days |

### B. attr 位标志

节点 attr 整数字段按 cell_type_registry.json 中定义的位展开为布尔属性：
- indicator_condition, basic_condition, ranking_condition
- reverse_transfer, sector_membership, cross_section
- delete_source, clear_dest_first, keep_source

type 值说明：
- type=1: 条件节点（DZH transfer_condition）
- type=200: 状态池（stock_state_pool）
- type=201: 条件池（DZH condition_pool）
- type=202: 市场源（market_source）
- type=4: 丢弃池（discard_pool）

### C. 流转模式位标志 (flow_mode_registry.json)

| 位 | 含义 | 对应 propagate 模式 |
|----|------|-------------------|
| delete_source | 删除源 | move |
| clear_dest_first | 清空目标 | overwrite |
| keep_source | 保留源 | copy |
| force_move | 强制覆盖 | force_move |
| output_constituent | 输出成份 | output_components |

### D. enter/exit 动作编码

动作类型+参数编码为整数，格式由 action_rules.json 定义：
- type=0: 无动作
- type=1: 保存板块（参数=板块文件路径编码）
- type=2: 声音预警（参数=声音类型编码）
- type=3: 弹窗提示

解码函数: `table_engine.py decode_action()`

### E. tradeattr 19字段

DZH 独有，type=200 节点的子元素，完整交易配置：

| 字段 | 含义 |
|------|------|
| accountno | 账户编号 |
| entertradetype | 入场交易类型 |
| enterrate | 入场费率 |
| exittradetype | 出场交易类型 |
| exitrate | 出场费率 |
| buycontrol | 买入控制 |
| sellcontrol | 卖出控制 |
| entrustamount | 委托数量 |
| entrustprice | 委托价格 |
| ... | （共19字段） |

TDX 无交易系统对接，无对应字段。

### F. stk/ana/hist 子元素

| 子元素 | 含义 | TDX | DZH |
|--------|------|-----|-----|
| stk | 股票运行时数据 | 14字段（code/label/indate/intime/...） | 4字段（极简） |
| ana | 分析数据 | 无 | 独立数据体系 |
| histana | 历史分析 | 无 | 独立数据体系 |

TDX 的 stk 自带丰富运行时数据（14字段），DZH 的 stk 极简（4字段）但通过独立数据体系（ana/histana/TqAdapter）获取运行状态。

### G. TQ 数据适配器接口

```python
class TqAdapter(Protocol):
    def resolve_market(self, markets: List[str]) -> Dict[str, List[str]]
    def eval_indicator(self, codes: List[str], formula_text: str,
                       period: str, sorttype: int = 0) -> Dict
    def get_kline_data(self, codes: List[str], period: str = None,
                       start_date: str = None, end_date: str = None) -> Dict
    def get_kline_batch(self, codes: List[str], period: str,
                        start: str, end: str) -> Dict[str, List[Dict]]
```

实现: `services/providers/akshare_provider.py`（实盘）, `services/providers/mock_provider.py`（模拟）

### H. K线合成逻辑

函数: `services/kline_synthesizer.py synthesize_kline()`

| 源周期 | 可合成目标周期 |
|--------|-------------|
| 1min | 5min, 15min, 30min, 60min, day, week, month |
| 5min | 15min, 30min, 60min, day, week, month |
| day | week, month |

合成规则: 取周期内首根K线的 open，末根的 close，max(high) 为 high，min(low) 为 low，sum(volume) 为 volume。

### I. TDX edge 参数映射

| TDX 参数名 | DZH 等效参数 |
|-----------|-------------|
| starttype | begin |
| starttime | begint |
| jgtime | interval_sec |
| cxtype | end |
| cxtime | end_param |
| tran | mode（1=move, 0=copy） |
| emptyps | （DZH 无等效） |

### J. intime 位长归一化

| 原始位长 | 归一化 | 示例 |
|----------|--------|------|
| <= 2 位 | 0000XX | "45" → "000045" |
| <= 4 位 | 00XXXX | "913" → "000913" |
| 5 位 | 0XXXXX | "91313" → "091313" |
| 6 位 | 不变 | "091313" → "091313" |

归一化逻辑由 tdx_psatt.json 的 time_formats 配置驱动，代码: engine.py L218-235 `_parse_intime_to_ts()`

### K. DB Schema DDL（9张持久表）

```sql
-- 1. pool_config
CREATE TABLE pool_config (
    pool_id       TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    pool_type     TEXT NOT NULL DEFAULT 'dzh' CHECK (pool_type IN ('dzh', 'tdx')),
    description   TEXT,
    xml_source    TEXT,
    topology_mode TEXT DEFAULT 'flow',
    status        TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','active','archived','deleted')),
    params        TEXT DEFAULT '{}',
    created_at    DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at    DATETIME NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 2. pool_node
CREATE TABLE pool_node (
    node_id     TEXT PRIMARY KEY,
    pool_id     TEXT NOT NULL,
    node_type   TEXT NOT NULL,
    label       TEXT,
    pos_x       REAL DEFAULT 0, pos_y REAL DEFAULT 0,
    width       REAL DEFAULT 120, height REAL DEFAULT 60,
    params      TEXT DEFAULT '{}',
    created_at  DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (pool_id) REFERENCES pool_config(pool_id) ON DELETE CASCADE
);

-- 3. pool_edge
CREATE TABLE pool_edge (
    edge_id        TEXT PRIMARY KEY,
    pool_id        TEXT NOT NULL,
    source_node_id TEXT NOT NULL,
    target_node_id TEXT NOT NULL,
    params         TEXT DEFAULT '{}',
    created_at     DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (pool_id) REFERENCES pool_config(pool_id) ON DELETE CASCADE,
    FOREIGN KEY (source_node_id) REFERENCES pool_node(node_id) ON DELETE CASCADE,
    FOREIGN KEY (target_node_id) REFERENCES pool_node(node_id) ON DELETE CASCADE,
    CHECK (source_node_id != target_node_id)
);

-- 4. node_state
CREATE TABLE node_state (
    record_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id       TEXT NOT NULL,
    stock_code    TEXT NOT NULL,
    entered_at    DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
    left_at       DATETIME,
    current_state TEXT NOT NULL DEFAULT 'in' CHECK (current_state IN ('in', 'out')),
    FOREIGN KEY (node_id) REFERENCES pool_node(node_id) ON DELETE CASCADE
);

-- 5. stock_transfer_log
CREATE TABLE stock_transfer_log (
    log_id            TEXT PRIMARY KEY,
    ts                DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
    source_node_id    TEXT NOT NULL,
    target_node_id    TEXT NOT NULL,
    stock_code        TEXT NOT NULL,
    transfer_mode     TEXT NOT NULL CHECK (transfer_mode IN ('copy','move','overwrite','constituent','force_move','pass_through')),
    trigger_condition TEXT,
    kline_time        DATETIME,
    FOREIGN KEY (source_node_id) REFERENCES pool_node(node_id) ON DELETE CASCADE,
    FOREIGN KEY (target_node_id) REFERENCES pool_node(node_id) ON DELETE CASCADE
);

-- 6. replay_session
CREATE TABLE replay_session (
    session_id   TEXT PRIMARY KEY,
    pool_id      TEXT NOT NULL,
    base_period  TEXT NOT NULL DEFAULT 'day' CHECK (base_period IN ('day','5min','1min')),
    start_date   DATE NOT NULL,
    end_date     DATE NOT NULL,
    play_speed   REAL NOT NULL DEFAULT 1.0,
    current_time DATETIME,
    kline_index  INTEGER DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'loading' CHECK (status IN ('loading','playing','paused','finished')),
    created_at   DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at   DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (pool_id) REFERENCES pool_config(pool_id) ON DELETE CASCADE,
    CHECK (end_date >= start_date)
);

-- 7. replay_snapshot
CREATE TABLE replay_snapshot (
    snapshot_id   TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL,
    ts            DATETIME NOT NULL,
    node_states   TEXT NOT NULL DEFAULT '{}',
    recent_events TEXT DEFAULT '[]',
    kline_data    TEXT DEFAULT '[]',
    FOREIGN KEY (session_id) REFERENCES replay_session(session_id) ON DELETE CASCADE
);

-- 8. kline_cache
CREATE TABLE kline_cache (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code  TEXT NOT NULL,
    period      TEXT NOT NULL,
    kline_time  TEXT NOT NULL,
    open        REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
    volume      REAL NOT NULL, amount REAL DEFAULT 0,
    cached_at   DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (stock_code, period, kline_time)
);

-- 9. config_version
CREATE TABLE config_version (
    version_id  TEXT PRIMARY KEY,
    table_name  TEXT NOT NULL,
    change_type TEXT NOT NULL CHECK (change_type IN ('create','update','delete')),
    old_content TEXT,
    new_content TEXT,
    created_at  DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
    created_by  TEXT DEFAULT 'system'
);
```

### L. 数据流架构图

```
配置表(JSON) ──加载──→ 运行时索引(module_map / engine_index / dispatch_index / _nset_dispatch / _edge_strategies)
                              │
                              ▼
持久表(pool_config) ──读取──→ _init_node_stocks ──写入──→ 运行时表(node_stocks)
                              │
                              ▼
                    ┌─── _execute_flowsCore ───┐
                    │                          │
              gate(时机门控)              filter(强弱筛选)
              读 timing.json             读 node_stocks[src]
              读 _flow_duration_starts   读 dispatch.json（含 nset_dispatch）
              读 _flow_exec_counts       读 engines.json
              读 _pool_start_time              │
                    │                          ▼
                    ▼              callback(持久化+副作用)
              propagate(状态更新)   读 action_table.json（含 args + log_on_success）
              写 node_stocks[tgt]   读 node_stocks[tgt]
              写 node_stocks[src]   写 node_state
                    │              写 stock_transfer_log
                    ▼              写 .dat/.log 文件
              ttl(超时淘汰)         播放声音 / 弹窗
              读 tdx_psatt.json
              写 node_stocks[tgt]

降级链: TQ可用→真实评估 → bar_data可用→_filter_by_bar_data → always→pass_through（确定性透传）
```

---

## 13. 事件流（Event-Driven Flow）

### 13.1 完整事件链路

事件驱动架构下，完整的数据→筛选→交易→TTL 链路如下：

```
TickReceived
    │
    ▼
DataChanged (source="tick", codes=[...])
    │
    ├─► TickBarModule (BarComposer)
    │       │
    │       ├─► 1m K线闭合？
    │       │       └─► BarComposed (period="1m") ─┐
    │       │                                         │
    │       ├─► 5m K线闭合？                         │
    │       │       └─► BarComposed (period="5m") ─┤
    │       │                                         │
    │       └─► ... (15m/30m/60m/1d)                │
    │                                                 │
    ▼                                                 ▼
DataChanged (source="bar", codes=[...], period=xxx) ◄──┘
    │
    ├─► FormulaModule (PythonFormulaEngine)
    │       │
    │       ├─► 批量公式求值（按 changed_codes 增量）
    │       └─► FormulaEvaluated
    │               │
    │               └─► CROSS 检测 → CrossOverDetected
    │
    ▼
EdgeFired (eid, ts)
    │  （由 EventDriver.fire_due() 到时触发，G3 只携带 eid+ts）
    │  （脏股票由 EdgeExecutor 从 source_pool.get_dirty_codes() 取）
    │
    ▼
StockFiltered (eid, passed=[...], rejected=[...])
    │  （EdgeExecutor 订阅 EdgeFired，执行条件节点激活+筛选）
    │
    ▼
TransferExecuted (src, tgt, codes, mode, entered_codes, exited_codes)
    │  （ExecutionModule 执行 propagate：copy/move/overwrite）
    │
    ├─► 若目标池关联交易信号
    │       └─► Signal(BUY/SELL)
    │               │
    │               ├─► OrderPlaced
    │               │       │
    │               │       └─► OrderFilled（市价单立即成交）
    │               │               │
    │               │               └─► PositionUpdated
    │               │
    │               └─► （SELL信号从持仓中查找对应头寸）
    │
    └─► TTL 注册（G1：注册到 EventDriver 主队列，一次性不注册下次）
            │
            └─► EventDriver.heappush(heap, (now+ttl_sec, TTLSpec))
                    │
                    └─►（时间到期）TTLDue → Signal(SELL) → 股票移出目标池
```

### 13.2 事件时序与无序保证

- **G6 运行时事件无序**：不存在 execution_order 运行时拓扑排序，所有边触发是独立的定时器事件，哪个定时器先到时先触发。边顺序号 _order 仅用于多入边交集/差集运算次序（设计结构，非运行时排序）
- **G1 单一定时器中断驱动**：EventDriver 使用单一 heapq 优先队列，所有边触发/TTL/tick 间隔注册到同一队列。触发即注册下次（`next = fire_time + interval`，非 now + interval），与模块计算无关
- **单边激活内逻辑链**：EdgeFired →（EdgeExecutor 订阅）→ FormulaEvaluated → StockFiltered → TransferExecuted → (Signal→Order→PositionUpdated) → TTLExpired
- **跨 tick 顺序**：严格按时间戳递增，乱序 tick 由 DataUpdater 丢弃（new_ts < old_ts 直接忽略）
- **幂等性**：相同 hash 的 tick 幂等忽略（existing._hash == new_hash 不触发任何事件）

### 13.3 各模块事件订阅/发布矩阵

| 模块 | 订阅事件 | 发布事件 |
|------|---------|---------|
| TickBarModule | DataChanged(tick), ReplayStep, SimulationStep | DataChanged(bar), BarComposed |
| FormulaModule | DataChanged(bar), BarComposed, PoolLoaded | FormulaEvaluated, CrossOverDetected |
| ScreeningModule | FormulaEvaluated, PoolLoaded | StockFiltered |
| ExecutionModule（EdgeExecutor 订阅 EdgeFired 执行条件节点激活） | EdgeFired, DataChanged, TimeAdvanced, PoolLoaded | EdgeFired, StockFiltered, TransferExecuted, TTLExpired, Signal, Executed, DomainEvent |
| TradeModule | Signal(BUY/SELL) | OrderPlaced, OrderFilled, PositionUpdated |
| MonitoringModule | **所有事件类型** | StatisticsUpdated, AlertRaised, SnapshotUpdated, EventLogged |
| RuntimeModeModule | ModeChanged, ReplayStarted | DataChanged, TimeAdvanced, ReplayStep, SimulationStep |

---

## 14. filter 章节更新：通达信 func 16参数完整执行流

### 14.1 func 参数解析入口

TDX 转移条件完整执行流由 `screening_module.eval_tdx_condition()` 统一处理，func 对象16参数解析步骤：

```
1. 参数提取（从 action_inputs.src_params.tdx_func 获取）
   ├─ nset: int = func["nset"]                    # 参数1：评估器类型（0-5）
   ├─ noperate: int = func["noperate"]            # 参数2：操作符（0-9）
   ├─ accode: str = func["accode"]                # 参数3：公式/字段代码
   ├─ nfirst: float = func.get("nfirst", 0)       # 参数4：第一参数
   ├─ nsecond: float = func.get("nsecond", 0)     # 参数5：第二参数/阈值/排名N
   ├─ cfirst: str = func.get("cfirst", "")        # 参数6：第一比较指标
   ├─ csecond: str = func.get("csecond", "")      # 参数7：第二比较指标
   ├─ nperiod: int = func.get("nperiod", 0)       # 参数8：分析周期
   ├─ ntjindexno: int = func.get("ntjindexno", 0) # 参数9：字段索引（nset=3/4）
   ├─ formula_script: str = func.get("formula_script", "") # 参数10：完整公式
   ├─ formula_args: Dict = func.get("formula_args")        # 参数11：公式参数
   ├─ bfilterst: bool = func.get("bfilterst", False)       # 参数12：剔除ST
   ├─ bfiltercy: bool = func.get("bfiltercy", False)       # 参数13：剔除创业板
   ├─ bfilterkc: bool = func.get("bfilterkc", False)       # 参数14：剔除科创板
   ├─ bfilterbj: bool = func.get("bfilterbj", False)       # 参数15：剔除北交所
   └─ nperiodnum: int = func.get("nperiodnum", 0)          # 参数16：多日线天数

2. 股票池预处理
   ├─ stock_list = node_stocks[src]  # 源池股票
   ├─ 若 bfilterst → 剔除ST（名称含ST/*ST）
   ├─ 若 bfiltercy → 剔除创业板（300开头）
   ├─ 若 bfilterkc → 剔除科创板（688开头）
   └─ 若 bfilterbj → 剔除北交所（8/4开头）

3. nset 路由（dispatch.json:nset_dispatch 查表）
   └─ nset_cfg = _NSET_DISPATCH[nset]
       └─ evaluator_fn = nset_cfg["evaluator"]
           ├─ nset=0,1,2 → eval_formula_nset()
           ├─ nset=3,4   → eval_scalar_nset()
           └─ nset=5     → eval_nset5_set_operation()
```

### 14.2 nset=0/1/2 公式评估执行流（eval_formula_nset）

```
1. 公式脚本获取
   ├─ formula_script 非空？→ 直接使用
   └─ 否 → _lookup_builtin_script(accode) 查 builtin_formulas.json

2. 周期映射
   └─ period = _nperiod_to_period(nperiod)  # 0→1d, 4→5m, 8→1m等

3. eval_field 提取（CROSS 信号快捷路径）
   └─ builtin_info = _lookup_builtin_formula_info(accode)
       └─ eval_field = builtin_info.get("eval_field")  # CROSS_J_K / CROSS_DIF_DEA / XG

4. 批量公式求值
   └─ result = formula_router.eval_batch(formula_text, symbols, period=period, args=formula_args)
       → 返回 {symbol: value} 字典

5. eval_field 结果提取
   └─ 若 eval_field：从多输出 dict 中提取对应字段值

6. nset 分支处理
   ├─ nset=0（技术指标）→ _eval_nset0_result()
   │   └─ 提取标量值 → _apply_noperate_mode()
   │       ├─ mode=rank → _resolve_rank() 排名处理
   │       ├─ mode=inflection → 拐点（标量模式返回[]）
   │       └─ mode=compare → _scalar_compare() 逐只比较
   │
   └─ nset=1/2（条件选股/专家系统）
       └─ [s for s, v in result.items() if v > 0]  # 值>0即通过
```

### 14.3 nset=3/4 标量评估执行流（eval_scalar_nset）

```
1. 字段选择
   ├─ field_table = nset_cfg["field_table"]  # nset_3_financial / nset_4_market
   ├─ field_def = field_table[ntjindexno]
   └─ tq_field = field_def["tq_field"]

2. 派生字段处理
   └─ 若 tq_field 在 _DERIVED_COMPONENT_FIELDS 中
       ├─ 批量获取所有组件字段
       └─ _compute_derived_field() 计算派生值

3. 标量获取
   └─ MarketDataPort.get_financial_scalars_batch() / get_market_scalars_batch()
       （仿真模式回退到 current_bar_data 读取）

4. noperate 比较
   └─ _apply_noperate_mode()
       ├─ mode=rank → _resolve_rank()
       └─ mode=compare → _scalar_compare() （需要 prev_value 做 cross 检测）
```

### 14.4 nset=5 集合运算执行流

```
noperate 映射（_NSET5_OPS 表）：
├─ noperate=0（并集）→ passed = set_a | set_b
├─ noperate=1（差集）→ passed = set_a - set_b
└─ noperate=2（交集）→ passed = set_a & set_b
```

---

## 15. 公式计算章节更新：PythonFormulaEngine 表驱动算子

### 15.1 算子加载机制

`PythonFormulaEngine` 通过 `config/data/formula_funcs.json` 表驱动加载所有算子，`_dispatch_func()` 函数按表反射调用：

```python
def _dispatch_func(name: str, args: List[Any], ctx: Dict = None) -> Any:
    cfg = _FUNCS_CFG.get(name)
    if cfg is None: return None
    # 1. 按 arg_spec 提取并转换参数
    extracted_args = []
    for spec in cfg["arg_spec"]:
        val = args[spec["idx"]] if spec["idx"] < len(args) else spec.get("default")
        val = _CASTERS.get(spec.get("cast"), lambda x: x)(val)
        extracted_args.append(val)
    # 2. 注入上下文字段（如 SAR 需要 high/low）
    if cfg.get("context_fields") and ctx:
        extracted_args = [ctx.get(f) for f in cfg["context_fields"]] + extracted_args
    # 3. 反射调用 handler
    handler_func = globals().get(cfg["handler"])
    return handler_func(*extracted_args, **cfg.get("cfg_kwargs", {}))
```

### 15.2 算子完整清单

| 算子类别 | 算子名 | handler | 说明 |
|---------|-------|---------|------|
| **通用算子** | MA / HHV / LLV / SUM / COUNT / STD | window_op | 通用滚动窗口算子，agg_method 分别为 mean/max/min/sum/sum/std |
| **通用算子** | REF | shift_op | 序列偏移（shift N 周期） |
| **通用算子** | CROSS | cross_op | 穿越检测，direction_field 决定金叉/死叉 |
| **递推算子** | EMA | ema_op | 指数移动平均（alpha=2/(N+1)） |
| **递推算子** | SMA | sma_op | 加权移动平均（alpha=M/N） |
| **逐元素算子** | ABS | abs_op | 绝对值 |
| **逐元素算子** | MAX / MIN | max_op / min_op | 二元取大/取小 |
| **逐元素算子** | IF | if_op | 条件选择（np.where） |
| **复杂指标** | SAR | sar_op | 抛物线转向，需要 high/low 上下文 |

### 15.3 支持的 TDX 函数列表

通过 formula_funcs.json 表驱动，当前支持：
- **行情字段**：OPEN/O, HIGH/H, LOW/L, CLOSE/C, VOL/V, VOLUME
- **引用函数**：REF(X, N)
- **逻辑函数**：IF(cond, A, B), CROSS(A, B)（上穿）
- **数学函数**：ABS(X), MAX(A,B), MIN(A,B)
- **统计函数**：MA(X,N), EMA(X,N), SMA(X,N,M), HHV(X,N), LLV(X,N), SUM(X,N), COUNT(X,N), STD(X,N)
- **指标组合**：可组合计算 MACD（EMA组合）、KDJ（SMA+REF组合）、RSI 等

### 15.4 公式编译与缓存

```
公式文本 → _tokenize() 分词 → _ExprParser 递归下降解析 → Python 表达式字符串
    ↓
eval(expression, globals, local_ns) → pd.Series / pd.DataFrame 结果
    ↓
compiled_cache[(formula_text, period)] = 编译结果  (LRU, maxsize=1000)
```

- **n=0 语义**：窗口 n=0 使用 expanding 窗口（从首个数据点累计），符合通达信习惯
- **向量化**：所有计算使用 pandas/numpy 向量化，无 Python 循环
- **NaN 处理**：NaN 通过运算传播，pandas rolling/min_periods=1 保证前期有值

---

## 16. 事件面板（Event Panel）

### 16.1 功能概述

事件面板提供可视化的事件时间轴展示，支持监控全链路事件流。所有事件（含已发生与未处理的排队事件）沿时间轴分布，并以统一的分类作为 Y 轴垂直分轨。

### 16.2 视觉设计

- **Canvas 时间轴**：横向时间轴，从左到右时间递增，底部显示时间刻度
- **当前时间线**：矩阵视图、散点视图、定时器队列均绘制垂直虚线标识当前时刻
- **事件图标**：9 种事件分类使用不同颜色/图标编码
- **轨道（Lane）**：按 **事件分类** 垂直分轨；两种视图模式下 Y 轴语义相同
- **排队中事件**：未处理的 pending 事件以黄色虚线框标识
- **Tooltip**：鼠标悬停显示事件详情

### 16.3 视图模式

事件面板支持两种视图切换，两种视图使用同一组 `(ts, category)` 数据，且 **Y 轴均为 9 种事件分类**：

| 视图 | 名称 | 布局 | 用途 |
|------|------|------|------|
| 分类显示 | 矩阵视图 | 每类事件独占一行，图标沿时间轴分布 | 观察每类事件的密度与趋势 |
| 全部显示 | 散点视图 | 所有事件绘制在同一 Canvas，同分类在同一水平轨道 | 观察全量事件的时间关联与跨类关系 |

- 分类筛选：可显示/隐藏特定分类事件，筛选结果在两种视图间同步
- 点击分类行或单个图标 → 下方详情区显示事件文本记录

### 16.4 定时器队列

定时器队列独立展示未处理的 `TimerQueued` / `TTLDue` 等排队事件，帮助观察未来何时会发生什么。

- **时间分布图**：顶部 Canvas 以 `fire_at` 为 X 轴、单一固定轨道为 Y 轴绘制排队事件；底部同步显示当前时间线
- **fire_at 单位归一化**：后端可能以秒为单位下发，前端统一归一化为毫秒后再参与时间轴计算与格式化显示
- **列表视图**：按 `fire_at` 升序排列，显示预计触发时间、事件类型、股票代码、详情摘要、队列位置
- **过期标识**：已过期但未处理的排队事件用红色虚线框标出，并自动清理 60 秒前的过期项
- **可折叠/展开**：默认展开，确保队列状态一目了然

### 16.5 事件文本详情

下方详情区用于展示事件的文本记录，作为可视化图标的补充：

- 每条记录显示：时间戳、事件类型图标、股票代码、详情摘要
- 点击矩阵分类行 → 显示该分类下全部事件文本
- 点击散点/矩阵中的单个图标 → 显示该事件及上下文相关事件
- 排队中事件带 pending 样式（黄色虚线框背景）

### 16.6 交互与状态持久化

- **拖拽浮窗**：标题栏拖拽调整面板位置，释放后写入 `localStorage`
- **高度调整**：拖拽底边调整面板高度，写入 `localStorage`
- **折叠/展开**：点击标题栏折叠按钮切换面板高度，写入 `localStorage`
- **隐藏/显示**：点击关闭按钮隐藏面板，下次可通过菜单重新打开
- **暂停/继续接收事件**：暂停时事件不入可视化列表，仅保留已排队 timer；恢复后刷新显示
- **清空事件**：一键清空当前可视化事件与详情区
- **渲染节流**：`render()` 通过 `setTimeout` 实现 200ms 节流，避免高频事件下 DOM/Canvas 频繁重建

### 16.7 性能考虑

- Canvas 绘制代替大量 DOM 节点，单 tick 千级事件仍可保持流畅
- 200ms 渲染节流进一步降低重绘频率
- 事件列表按时间窗口裁剪，默认保留最近 N 条（可配置），防止内存无限增长
- 定时器队列自动清理过期项，避免长运行后列表膨胀

---

## 17. 条件节点拓扑与 G1-G6 核心架构

### 17.1 G3：EdgeFired 不携带 changed_codes

EdgeFired 只携带 eid 和 ts。脏股票由 EdgeExecutor 从源池 StatePoolView.get_dirty_codes() 取：

```python
def _on_edge_fired(self, event: EdgeFired) -> None:
    ec = self.schedule.edge_ctx.get(event.eid)
    source_pool = self.state.get_pool(ec.sid)
    dirty_codes = source_pool.get_dirty_codes()  # G3/G4
    # first_run 兜底：脏股票为空且首次运行时，用源池全量股票
    if not dirty_codes and self.state.first_run:
        dirty_codes = set(source_pool.get_stock_codes())
    self.run(event.eid, changed_codes=list(dirty_codes) if dirty_codes else None)
```

### 17.2 G4：StatePoolView 视图对象

StatePoolView 取代扁平的 get_node_stocks/set_node_stocks 接口，状态池是视图，不独立维护脏股票集合：

| 方法 | 行为 |
|------|------|
| get_stocks() | 返回本池股票列表（副本） |
| get_stock_codes() | 返回本池股票代码集合 |
| get_dirty_codes() | `state.changed_codes ∩ 本池股票`（脏股票在 tick 表变更列） |
| add_stocks(stocks) | 入池 + 标脏（写入 state.changed_codes） |
| remove_stocks(codes) | 出池 + 标脏 |

脏股票唯一真相源是 `state.changed_codes`（全局集合），StatePoolView.get_dirty_codes() 是其与本池股票的交集。

### 17.3 增量筛选执行流程

```
EdgeFired(eid, ts)  ← G3 只含 eid+ts
    │
    ▼
EdgeExecutor._on_edge_fired()
    │
    └─ source_pool.get_dirty_codes()  ← G4 从 StatePoolView 取脏股票
        │
        ├─ 脏股票为空且非首次 → 沿用上一次 StockFiltered 结果，跳过计算
        ├─ 脏股票为空且首次（first_run）→ 全量评估（changed_codes=None）
        └─ 脏股票非空 → 增量评估
            │
            ▼
        FormulaRouter.eval_batch(formula, dirty_codes, period)
            → 仅对 dirty_codes 中的股票求值
            │
            ▼
        合并缓存结果 + noperate 比较 → StockFiltered
            → passed = (cached_passed - dirty_codes) | newly_passed
```

### 17.4 缓存失效规则

| 缓存项 | 失效时机 |
|--------|---------|
| formula_results[(formula_ref, bar_hash)] | bar_hash 变化（K线闭合产生新bar） |
| filter_inputs[eid] | 源池股票集合变化（股票数量变化） |
| compiled_cache[formula_text] | LRU 容量淘汰（默认 1000） |

### 17.5 性能收益

增量筛选在实盘场景（每秒几只股票有Tick更新）下的性能收益：
- 若池中有 3000 只股票，每秒仅 50 只有更新 → 公式计算量减少 **98%**
- 脏股票为空时完全跳过计算（无行情变化的tick）
- nset=3/4 标量评估（财务/行情字段）同样享受增量机制

---

## 18. 条件节点拓扑与 G1-G6 核心架构

### 18.1 条件节点拓扑

条件节点（type=condition）承载 func/indi/indiparam/filter_spec，边只承载触发频率（入边）+ 顺序号（多入边交集/差集）。实例拓扑示例：

```
source(100只fz股票) → ec1(60s) → cond1(KDJ金叉) → ec1out → pool_A(TTL=6000s)
source → ec2(10s) → cond2(MACD金叉) → ec2out → pool_B(TTL=12000s)
pool_A → ec3a(5s,_order=1) → cond3(交集) ← ec3b(5s,_order=2) ← pool_B
cond3 → ec3out → pool_C(TTL=1200s,enter=buy 100,exit=sell_all)
```

- `source/状态池→condition` 用 conditional 边（含 time_gate_interval）
- `condition→状态池` 用 unconditional 边（跟随入边）
- 计算参数/K线配置/筛选条件从条件节点读取，非边

### 18.2 G1 单一定时器中断驱动

EventDriver 使用单一 heapq 优先队列，所有边触发/TTL/tick 间隔注册到同一队列：

- heapq 元素：`[fire_time, seq, spec]`，seq 用于同时间稳定排序
- 触发即注册下次：`next_time = fire_time + interval`（非 now + interval），保证固定间隔
- interval=None 或 <=0 表示一次性（如 TTL），不注册下次
- 禁止 asyncio.sleep、轮询、线性扫描、at_fn 延迟求值

### 18.3 G6 事件无序

- 删除 execution_order 运行时拓扑排序
- 运行时事件没有顺序，哪个定时器先到时先触发
- 边顺序号 `_order` 保留，用于多入边交集/差集运算次序（设计结构，非运行时排序）

### 18.4 G2 引擎只发事件不执行计算

定时器到时→action 发布事件 + 立即注册下次→结束。业务逻辑由订阅模块完成：

- kind="edge" 的 action 只发布 EdgeFired(eid, ts)
- kind="ttl" 的 action 只发布 TTLDue（一次性，不注册下次）
- kind="tick" 的 action 只发布 TickDue

### 18.5 G3 EdgeFired 不携带 changed_codes

EdgeFired 只携带 eid 和 ts。脏股票由 EdgeExecutor 从源池 StatePoolView.get_dirty_codes() 取（详见 §17）。

### 18.6 G4 StatePoolView 视图对象

StatePoolView 取代扁平的 get_node_stocks/set_node_stocks 接口（详见 §17.2）。状态池是视图，脏股票唯一真相源是 state.changed_codes（tick 表变更列）。

### 18.7 条件节点激活流程

EdgeExecutor 订阅 EdgeFired，收到后激活条件节点：

```
EdgeFired(eid) → 定位 eid 目标节点为条件节点
  → 收集所有入边（按 _order 排序）
  → 每条入边：
      source_pool = state.get_pool(in_edge.source)
      dirty_codes = source_pool.get_dirty_codes()  # G3/G4
      if 交集条件: port_results[order] = source_pool 全集（当前同时在池的股票）
      else: 公式计算（添加列）→ 筛选（列操作）→ port_results[order]
  → 集合运算：单入边直接输出；多入边按 filter_spec 做交集/差集/并集
  → 出边输出到目标池：add_stocks + 标脏 + 注册 TTL
  → C池入池触发买入事件链
```

### 18.8 公式计算与筛选分离

- 公式计算 = 添加列（FormulaEngine.eval_series 写入 series_results）
- 筛选 = 列操作（_eval_op 按 prev_expr/curr_expr 比较列值）
- 使用 HQChartPy2，Python 3.13，无 cross
- 金叉 noperate=3：`prev_expr="line1[-2] <= line2[-2]"`, `curr_expr="line1[-1] > line2[-1]"`
- 增量评估：仅对 dirty_codes 重新评估，合并 `passed = (cached_passed - dirty_codes) | newly_passed`

### 18.9 G5 MockDataSource

原仿真 tick 源重命名为 MockDataSource：

- tick 定时器注册到 EventDriver 统一优先队列（与边触发/TTL 同一队列）
- 所有股票代码用 fz 替代（如 fzSH600000），避免与实盘代码冲突
- 每只股票分配 1-9s 固定随机间隔（基于股票代码 hash 的确定性种子，同股票固定、不同股票不同）
- 仿真与实盘除 tick 请求方式外共用同一套代码

### 18.10 模块零引用

core/*.py 各模块除白名单外无跨模块引用：

- 白名单：`core.event_bus` / `core.domain` / `core.schemas` / 标准库 / 第三方库
- 模块构造函数仅接收 EventBus + 配置 dict + 可选 Protocol 接口（依赖注入）
- 业务模块间通信唯一合法通道：EventBus，禁止直接 import 其他业务模块

### 18.11 交易事件链

- C池入池买入：TransferExecuted → Signal(BUY,100) → OrderPlaced → OrderFilled → PositionUpdated
- C池出池卖出：TTLDue → Signal(SELL) → OrderPlaced → OrderFilled → PositionUpdated
- TTL 一次性（interval=None），不注册下次

---

## 19. 测试架构

### 19.1 eventtest 目录结构

项目根目录下新建 `eventtest/` 目录，与旧 `tests/` 目录并列。旧 `tests/` 目录冻结保留（不删除），新评审一律以 `eventtest/` 输出为准。

| 分类 | 文件 | 职责 |
|------|------|------|
| 基础设施 | `__init__.py` | 包标记 |
| 基础设施 | `conftest.py` | 共享 fixture（virtual_clock / fz_stocks / pool_engine / event_collector / pool_snapshot）|
| 基础设施 | `run_eventtest.py` | 量化测试运行器，输出测试总数/通过数/失败数/通过率/各测试耗时/事件计数表/池状态快照表/退出码 |
| 基础设施 | `README.md` | 方法论说明 + 运行方式 |
| 正测试 | `test_positive_mockdatasource.py` | MockDataSource 确定性 tick 生成（G5）|
| 正测试 | `test_positive_eventdriver.py` | EventDriver 单 heapq 优先队列中断驱动（G1）|
| 正测试 | `test_positive_statepoolview.py` | StatePoolView 视图脏股票（G4）|
| 正测试 | `test_positive_edgefired.py` | EdgeFired 无 changed_codes（G3）|
| 正测试 | `test_positive_condition_activation.py` | 条件节点激活与公式筛选 |
| 正测试 | `test_positive_trade_chain.py` | 交易事件链 + TTL |
| 正测试 | `test_positive_ttl.py` | TTL 一次性触发 |
| 反测试 | `test_negative_empty_pool.py` | 空备选池 |
| 反测试 | `test_negative_invalid_config.py` | 无效条件节点配置 |
| 反测试 | `test_negative_bad_topology.py` | 坏边拓扑（自环/孤点）|
| 反测试 | `test_negative_duplicate_transfer.py` | 重复入池 |
| 反测试 | `test_negative_ttl_no_position.py` | TTL 到期无持仓 |
| 反测试 | `test_negative_formula_error.py` | 公式计算异常返回空 |
| 反测试 | `test_negative_module_import.py` | 跨模块非法 import |
| 合测试 | `test_integration_sim_full_flow.py` | 仿真模式完整事件链端到端 |
| 合测试 | `test_integration_event_chain_order.py` | 事件链顺序验证 |
| 合测试 | `test_integration_pool_snapshot.py` | 池状态快照 + 量化评审报告 |

`conftest.py` 提供 5 个 fixture，全部复用 `core/` 现有类，禁止兼容已删除旧接口：

| fixture | 作用 |
|---------|------|
| `virtual_clock` | 虚拟时钟对象，起点 34500.0（=09:30:00），提供 `advance(seconds)` |
| `fz_stocks` | 工厂 fixture，从 `config/pools/sim_test_pool_100.json` 动态读取 N 只 `fz` 前缀股票代码 |
| `pool_engine` | 工厂 fixture，装配并返回 `PoolEngine` 实例（复用 `core.engine.PoolEngine`）|
| `event_collector` | 工厂 fixture，订阅 EventBus 全部事件，提供 `.events` / `.count_by_type()` / `.filter()` |
| `pool_snapshot` | 工厂 fixture，返回 `Dict[str, List[str]]`，通过 `engine.state.get_pool(nid).get_stock_codes()` 获取 |

### 19.2 正反合测试方法论

#### 正测试（test_positive_*.py）

验证正常路径，每条断言基于实际运行结果：

- **MockDataSource tick 生成（G5）**：同股票 tick 间隔固定（基于股票代码 hash 的确定性种子），不同股票间隔不同，所有代码以 `fz` 前缀
- **EventDriver 单 heapq 优先队列（G1）**：内部仅维护 1 个 `_heap`，`fire_due(now)` 按最近到期时间弹出堆顶，发布事件后立即 `heappush` 下次（next = fire_time + interval），TTL 一次性不注册下次
- **StatePoolView 视图脏股票（G4）**：`get_dirty_codes()` 返回 `state.changed_codes ∩ 本池股票`，`add_stocks()` 入池并标脏，`remove_stocks()` 出池并标脏
- **EdgeFired 无 changed_codes（G3）**：事件 payload 只含 `eid` 和 `ts`，脏股票由 EdgeExecutor 从 `source_pool.get_dirty_codes()` 取
- **条件节点激活与公式筛选**：EdgeFired 触发后收集入边按 `_order` 排序，从源池取脏股票，公式计算（添加列）与筛选（列操作）严格分离
- **集合运算**：多入边按 `filter_spec` 做交集/差集/并集（`_SET_OP_FUNCS` 表驱动分派）
- **交易事件链**：TransferExecuted → Signal(BUY) → OrderPlaced → OrderFilled → PositionUpdated
- **TTL 一次性触发**：TTL 到期触发 TTLDue → Signal(SELL) → OrderPlaced → OrderFilled → PositionUpdated，heap 长度不变

#### 反测试（test_negative_*.py）

验证异常与边界，预期系统优雅降级而非崩溃：

- **空备选池**：source 池 0 只股票，启动不抛异常，tick 数 = 0，条件节点激活后 passed 为空
- **无效条件节点配置**：cond1.func 缺失 accode 字段，FormulaEngine 返回空结果，异常被 EdgeExecutor 捕获，FormulaEvaluated 携带 error 字段
- **坏边拓扑（自环/孤点）**：边 source=target 时 CompiledSchedule 构建跳过该边，不引发无限循环
- **重复入池**：同一股票多次进入同一池仅出现一次，TTL 不重复注册
- **TTL 到期无持仓**：TTL 到期但股票无持仓，Signal 发出但 OrderPlaced 失败/为空，不抛异常
- **公式计算异常返回空**：FormulaEngine.eval_series 返回空 dict，StockFiltered passed 为空，不抛 KeyError
- **跨模块非法 import**：`core/execution_module.py` 仅允许 `core.event_bus`/`core.domain`/`core.schemas`/标准库/第三方库

#### 合测试（test_integration_*.py）

端到端集成验证仿真模式全流程：

- **仿真模式完整事件链**：加载 `config/pools/sim_test_pool_100.json`，启动 120 秒虚拟时钟，断言 11 类事件（TickReceived/DataChanged/BarComposed/EdgeFired/FormulaEvaluated/StockFiltered/TransferExecuted/Signal/OrderPlaced/OrderFilled/PositionUpdated）全部 ≥ 1
- **事件链顺序**：TickReceived → DataChanged → BarComposed → EdgeFired → FormulaEvaluated → StockFiltered → TransferExecuted → Signal → OrderPlaced → OrderFilled → PositionUpdated
- **池状态快照**：source = 100 只 fz 股票，pool_A ⊆ source，pool_B ⊆ source，pool_C = pool_A ∩ pool_B，pool_C 中每只股票持仓 = 100 股

### 19.3 量化评审标准

评审工程师必须运行 `python -m eventtest.run_eventtest`，以输出报告中的量化指标打分：

| 指标 | 评分规则 |
|------|---------|
| 正测试通过率 | ≥ 98% 得满分，每低 1% 扣 5 分 |
| 反测试通过率 | ≥ 98% 得满分，每低 1% 扣 5 分 |
| 合测试通过率 | ≥ 98% 得满分，每低 1% 扣 5 分 |
| 事件链顺序错误 | 直接扣 10 分 |
| 池状态断言错误 | 直接扣 10 分 |
| 旧接口残留 | 每处扣 5 分（搜索 `get_node_stocks`/`execution_order`/`changed_codes` 及原仿真 tick 源旧类名）|

**门槛：≥ 98 分**

### 19.4 运行方式

```bash
# 运行全部测试（pytest 原生输出）
python -m pytest eventtest/ -v

# 输出量化报告（测试总数/通过数/失败数/通过率/各测试耗时/事件计数表/池状态快照表/退出码）
python -m eventtest.run_eventtest
```

`run_eventtest.py` 通过 `_StatsPlugin` pytest 插件捕获精确的 passed/failed/collected 计数与各测试耗时，并从共享 `REPORT_STATE` 单例读取合测试填充的事件计数表与池状态快照表，生成完整量化报告。退出码 0 = 全部通过，1 = 有失败。

### 19.5 双工程师协作流程

```
架构工程师实现代码
    │
    ▼
本地运行 python -m eventtest.run_eventtest 确认通过率 ≥ 98%
    │
    ▼
提交评审
    │
    ▼
评审工程师运行 python -m eventtest.run_eventtest 打分
    │
    ├─ ≥ 98 分 → 进入下一任务
    └─ < 98 分 → 打回重做
```

**bug 修复原则**：发现 bug 必须修复生产代码 + 改正向断言验证 spec 被满足，禁止 workaround 掩盖 bug。

### 19.6 已修复的生产 bug（Task 1-10 期间）

| 位置 | bug 描述 | 修复 Task |
|------|---------|----------|
| `core/execution_module.py:1219` | edge_index dict 推导式 bug | Task 4 |
| `core/event_bus.py` | FormulaEvaluated 缺 error 字段 | Task 6 |
| `core/execution_module.py:877-883` | 自环边无限循环 bug | Task 7 |
| `core/execution_module.py:78-80` | TimedEventSpec 模块级 import（应函数级懒加载）| Task 8 |
| `core/trade_module.py` | `_on_ttl_expired` 移除 qty<=0 跳过 + `_on_signal` 发布 rejected OrderPlaced | Task 8 |
| `core/execution_module.py:2832-2842` | 交集路径退化 bug | Task 10 |

## 20. 前端验证

### 20.1 验证流程

前端验证采用双工程师协作 + Playwright 浏览器测试 + eventtest 量化验证的混合模式：

```
架构工程师修复前端/后端 bug
    │
    ▼
评审工程师 Playwright 浏览器验证（条件节点显示、配置面板、模式切换）
    │
    ├─ ≥ 98 分 → 进入下一任务
    └─ < 98 分 → 打回重做
    │
    ▼
eventtest 173 个正反合测试量化验证后端事件链（退出码 0 = 全部通过）
```

**验证策略**：Playwright 验证前端 UI 渲染与交互（条件节点矩形、配置面板、模式切换），eventtest 量化验证后端事件链正确性（11 类事件计数、池状态快照、A∩B 交集逻辑）。两者互补，Playwright 覆盖用户可见行为，eventtest 覆盖内部事件流正确性。

### 20.2 Playwright 验证结果

| Task | 验证内容 | 评分 | 证据 |
|------|---------|------|------|
| Task 1 | 加载示例池 + 条件节点显示 | 100 | 7 节点 source/cond1/cond2/cond3/pool_A/pool_B/pool_C；cond1-3 紫色圆角 #8e44ad；边标签 ec1=60s/ec2=10s/ec3a/ec3b=5s；多入边顺序号 #30/#31 |
| Task 2 | 条件节点配置面板 | 100 | cond1: func=KDJ/indi=KDJ/filter_spec.evaluator_type=indicator/noperate=3；cond2: func=MACD；cond3: evaluator_type=intersection |
| Task 3 | 仿真模式启动 | 98 | 仿真按钮 active；POST /api/sim/start 正确发出；simulationClock/simulationStepCount 可见；Bug3/Bug4/Bug5 修复后 mock provider 正常加载 |
| Task 4 | 完整事件链 | 98 | eventtest 量化：11 类事件全部产生（见 20.3） |
| Task 5 | 三种模式切换 | 98 | 设计/仿真模式 Playwright 验证；实盘/回放模式代码审查确认 |
| Task 6 | bug 修复 + 无回归 | 98 | 5 个 bug 全部修复；eventtest 173 测试退出码 0 |

### 20.3 eventtest 量化验证结果

```
事件计数表（按 EventType 分组）：
  TickReceived         10533
  DataChanged          73731
  BarComposed          63198
  EdgeFired            155
  FormulaEvaluated     3500
  StockFiltered        35
  TransferExecuted     29
  Signal               81
  OrderPlaced          81
  OrderFilled          81
  PositionUpdated      81

池状态快照表：
  source:  100 stocks
  pool_A:  81 stocks
  pool_B:  100 stocks
  pool_C:  81 stocks

总耗时: 385.78s
退出码: 0 (全部通过)
```

验证要点：
- source 池 100 只 fz 前缀股票（fz000001~fz000100）✓
- cond1（KDJ 5分钟金叉）筛选后 pool_A=81 stocks ✓
- cond2（MACD 1分钟金叉）筛选后 pool_B=100 stocks ✓
- cond3（A∩B 交集）后 pool_C=81 stocks ✓
- pool_C 入池触发买入：Signal=81 → OrderPlaced=81 → OrderFilled=81 → PositionUpdated=81 ✓

### 20.4 已修复的前端/后端 bug

| Bug | 位置 | 描述 | 修复 |
|-----|------|------|------|
| Bug1 | `web/js/app.js:11931` | UUID 验证过严，拒绝非 UUID 格式的 pool_id（如 sim_test_pool_100） | 删除 isValidPoolId 正则，改为 `var poolId = rawPoolId \|\| configId \|\| null;` |
| Bug2 | `config/ui/ui_layouts.json:1138` | func.cfirst/csecond 被渲染为 `<input type="color">`，指标线条名（如"K"/"D"）无法输入 | func_cfirst/csecond 从 number_input 改为 text_input |
| Bug3 | `app.py:277` | DataSourceContract 接收 defaults.json 配置（不含 sources 键），导致 `_sources` 为空，probe("mock") 返回 unknown_source | `DataSourceContract(config=config, bus=bus)` → `DataSourceContract(bus=bus)`，让其自行加载 data_source_contract.json |
| Bug4 | `config/data/data_source_contract.json` | module 路径 `meta_core.services.providers` 无法导入（No module named 'meta_core'），导致 MockProvider 加载失败 | module 路径改为 `services.providers`（5 处） |
| Bug5 | `config/data/data_providers.json` | 同 Bug4，ProviderRegistry 加载所有 provider 失败 | module 路径改为 `services.providers`（6 处） |

### 20.5 验证方法说明

**Playwright 浏览器验证**用于前端 UI 渲染与交互验证：
- 条件节点矩形渲染（颜色、圆角、class）
- 条件节点配置面板（func/indi/indiparam/filter_spec 字段）
- 模式切换按钮 active 状态
- 仿真面板元素可见性

**eventtest 量化验证**用于后端事件链正确性验证：
- 173 个正反合测试（60 正 + 80 反 + 33 合）
- 11 类事件计数量化断言
- 池状态快照断言（source/pool_A/pool_B/pool_C 股票数）
- A∩B 交集逻辑严格断言（pool_C = pool_A ∩ pool_B）

当 Playwright 浏览器测试因环境限制（sandbox 网络隔离）无法连接 localhost 时，以 eventtest 量化结果作为后端功能正确性的权威验证依据。

---

## 21. 前端最终完善

### 21.1 状态管理架构

采用 `window.AppState` 全局 Store 作为单一数据源，替代局部变量导致的状态不同步问题。

**全局 Store 结构**：

```javascript
window.AppState = {
  mode: 'design' | 'simulation' | 'replay' | 'live',
  simulationState: 'idle' | 'running' | 'paused' | 'stopped',
  simulationTime: 34500.0,  // 虚拟时钟（秒，09:30:00 = 34500）
  speed: 1,                 // 仿真速度倍率
  events: [],               // 事件历史
  timers: [],               // 定时器队列
  stockTables: {},          // 各节点股票数缓存
  _subscribers: {},         // 订阅者表：event_type → [callback]
  
  subscribe(eventType, callback) { /* 注册订阅 */ },
  notify(eventType, data)   { /* 通知所有订阅者 */ },
  setState(patch)           { /* 部分更新 + notify */ }
}
```

**subscribe/notify 机制**：
- 订阅：`AppState.subscribe('modeChanged', handler)`
- 通知：`AppState.notify('modeChanged', { mode: newMode })`
- 状态变更必须通过 `setState()`，禁止直接赋值
- 所有 UI 更新由订阅回调驱动，禁止各模块自行维护状态副本

**状态转换图**：

```mermaid
stateDiagram-v2
    [*] --> idle
    idle --> running: start()
    running --> paused: pause()
    paused --> running: resume()
    running --> stopped: stop()/reset()
    paused --> stopped: stop()/reset()
    stopped --> idle: reset()
```

### 21.2 事件面板可视化架构

事件面板提供三种互补视图：矩阵视图（时间轴定位）、散点图（密度分布）、定时器队列（待触发任务）。

#### 21.2.1 矩阵视图

| 特性 | 实现 |
|------|------|
| 时间轴比例定位 | `left = (event.ts - windowStart) / (windowEnd - windowStart) * 100%` |
| 样式驱动 | `EVENT_STATE_STYLES` 表驱动，按 event_type 查表获取 color/bg/icon |
| NOW 线 | 红色竖线 `#e74c3c`，使用 `transform: translateX()` GPU 加速，避免重排 |
| 时间刻度 | 每 60 秒一个刻度线 + HH:MM:SS 标签 |
| 滚动 | 窗口固定显示最近 5 分钟事件，NOW 线固定在右侧 80% 位置 |

**EVENT_STATE_STYLES 样式表**：

| event_type | color | bg | icon |
|------------|-------|----|------|
| TickReceived | #95a5a6 | rgba(149,165,166,0.15) | ⏱ |
| DataChanged | #3498db | rgba(52,152,219,0.15) | 📊 |
| BarComposed | #2ecc71 | rgba(46,204,113,0.15) | 📶 |
| EdgeFired | #9b59b6 | rgba(155,89,182,0.15) | ⚡ |
| FormulaEvaluated | #1abc9c | rgba(26,188,156,0.15) | 🧮 |
| StockFiltered | #f39c12 | rgba(243,156,18,0.15) | 🔍 |
| TransferExecuted | #e67e22 | rgba(230,126,34,0.15) | 🔄 |
| Signal | #e74c3c | rgba(231,76,60,0.20) | 🚦 |
| OrderPlaced | #34495e | rgba(52,73,94,0.15) | 📋 |
| OrderFilled | #27ae60 | rgba(39,174,96,0.20) | ✅ |
| PositionUpdated | #16a085 | rgba(22,160,133,0.15) | 💼 |
| TTLDue | #c0392b | rgba(192,57,43,0.20) | ⏰ |

#### 21.2.2 散点图视图

| 特性 | 实现 |
|------|------|
| 时间窗口 | 以 NOW 为中心 ±500ms 滑动窗口 |
| 聚合 | 同一事件类型在同一时间桶（50ms）聚合为单个点 |
| 计数徽标 | 聚合点右上角显示圆形徽标，数字为该桶事件计数（>1 时显示） |
| Tooltip 增强 | 悬停显示：事件类型、精确时间戳（HH:MM:SS.mmm）、聚合计数、最近 N 条摘要 |
| 点击详情 | 点击散点展开侧边面板，显示该时间窗口内所有事件的完整 payload |

**散点布局算法**：
- X 轴：时间（相对 NOW，-500ms ~ +500ms）
- Y 轴：事件类型分组（按类别颜色聚集）
- 半径：`r = 3 + Math.min(count, 10)`（事件越多点越大）

#### 21.2.3 定时器队列视图

| 特性 | 实现 |
|------|------|
| 状态样式 | `TIMER_STATE_STYLES` 表驱动 |
| NOW 线 | 绿色竖线 `#27ae60`，标记当前虚拟时钟位置 |
| 剩余时间 | 每个定时器卡片显示实时倒计时 `MM:SS`，每秒更新 |
| 排序 | 按 fire_time 升序，最近到期在最上 |
| 类型标签 | edge（紫色）/ ttl（红色）/ tick（灰色） |

**TIMER_STATE_STYLES 样式表**：

| timer 状态 | color | bg | border |
|------------|-------|----|--------|
| pending（未到期） | #2c3e50 | #ffffff | 1px solid #bdc3c7 |
| due（到期中） | #e67e22 | #fef5e7 | 2px solid #e67e22 |
| fired（已触发） | #95a5a6 | #ecf0f1 | 1px dashed #bdc3c7 |
| cancelled（已取消） | #e74c3c | #fdedec | 1px solid #e74c3c |

### 21.3 事件类型分类映射表

9 类事件按功能域分类，统一颜色与图标：

| 分类 | 包含事件 | 主色 | 图标 |
|------|---------|------|------|
| 时钟 Tick | TickReceived | 灰色 #95a5a6 | ⏱ |
| 数据更新 | DataChanged, BarComposed | 蓝色 #3498db | 📊 |
| 条件触发 | EdgeFired, FormulaEvaluated, StockFiltered | 紫色 #9b59b6 | ⚡ |
| 状态转移 | TransferExecuted, TTLDue | 橙色 #e67e22 | 🔄 |
| 交易信号 | Signal | 红色 #e74c3c | 🚦 |
| 订单生命周期 | OrderPlaced, OrderFilled | 深蓝 #34495e | 📋 |
| 持仓更新 | PositionUpdated | 青绿 #16a085 | 💼 |
| 系统事件 | SessionStarted, SessionStopped, SpeedChanged | 深灰 #7f8c8d | ⚙️ |
| 错误/警告 | FormulaError, OrderRejected | 深红 #c0392b | ⚠️ |

### 21.4 快捷键系统

采用 `SHORTCUTS` 表驱动映射，集中管理所有键盘快捷键：

```javascript
const SHORTCUTS = {
  ' ':        { action: 'togglePlayPause', desc: '播放/暂停仿真' },
  'r':        { action: 'resetSimulation',  desc: '重置仿真' },
  'ArrowRight': { action: 'stepForward',    desc: '单步前进（1秒）' },
  'ArrowLeft':  { action: 'stepBackward',   desc: '单步后退（1秒）' },
  '+':        { action: 'speedUp',          desc: '加速（×2）' },
  '-':        { action: 'speedDown',        desc: '减速（÷2）' },
  '1':        { action: 'setSpeed1x',       desc: '1x 速度' },
  'm':        { action: 'toggleMode',       desc: '切换设计/仿真模式' },
  'e':        { action: 'toggleEventPanel', desc: '显示/隐藏事件面板' },
  'Escape':   { action: 'closeDialogs',     desc: '关闭所有弹窗' },
  '?':        { action: 'showHelp',         desc: '显示快捷键帮助' }
};
```

| 按键 | 功能 | 上下文 |
|------|------|--------|
| Space | 播放/暂停 | 仿真模式 |
| R | 重置仿真 | 仿真模式 |
| → / ← | 单步前进/后退 1 秒 | 仿真暂停时 |
| + / - | 加速/减速 | 仿真模式 |
| 1 | 恢复 1x 速度 | 仿真模式 |
| M | 切换设计/仿真模式 | 全局 |
| E | 切换事件面板 | 全局 |
| Esc | 关闭弹窗/详情面板 | 全局 |
| ? | 显示快捷键帮助弹窗 | 全局 |

### 21.5 本次修复的 Bug 清单

| Bug 编号 | 问题描述 | 根因 | 修复方案 |
|---------|---------|------|---------|
| Bug 1 | 仿真启动后不能自动步进，点击单步才动 | `currentMode` 是局部变量，模式切换后各模块持有的副本不一致，自动步进循环读到旧值 | 引入 `window.AppState` 全局 Store，所有模块通过 `AppState.mode` / `AppState.simulationState` 读取，变更通过 `setState()` + subscribe/notify 广播 |
| Bug 2 | 矩阵视图只堆事件块，没有时间轴，看不出事件先后顺序 | 事件块直接 `appendChild`，无 X 坐标计算 | 实现时间比例定位：`left = (ts - windowStart) / windowDuration * 100%`；添加 60 秒刻度线 + HH:MM:SS 标签；红色 NOW 线用 `transform: translateX()` GPU 加速 |
| Bug 3 | 散点图只有点，看不出密度，无悬停提示，点了没反应 | 无聚合、无交互绑定 | ±500ms 滑动窗口 + 50ms 时间桶聚合；聚合点右上角圆形计数徽标；Tooltip 显示类型/时间/计数/摘要；点击展开详情侧边面板 |
| Bug 4 | 定时器队列只列文字，没有时间线，看不到还有多久触发 | 纯 `<li>` 列表渲染 | 绿色 NOW 线 `#27ae60` 标记当前时间；`TIMER_STATE_STYLES` 表驱动 4 种状态样式；每个定时器卡片显示 MM:SS 实时倒计时 |
| Bug 5 | Reset 按钮调用了不存在的 API，重置后事件/定时器没清干净 | 误调用 `POST /api/sim/clear`（后端无此路由），未清理前端状态 | 改为调用正确的 `POST /api/sim/stop`，然后前端执行：清空 `AppState.events`/`AppState.timers`，`setState({ simulationState: 'idle', simulationTime: 34500 })`，重绘所有视图 |
| Bug 6 | Speed slider 拖动时发无效 API 请求，速度没变化 | 调用 `POST /api/sim/speed`（后端无此路由），速度控制在后端但未实现 | 移除无效 API 调用；仿真速度由客户端控制：`setInterval` 间隔 = 1000ms / speed；speed 存入 `AppState.speed` 并持久化到 localStorage |
| Bug 7 | 点击单步后事件延迟半秒才显示，有时不显示 | 步进后等待轮询才拉取事件，轮询间隔 500ms | 步进 API 返回新事件立即 `AppState.events.push()` + `notify('eventsUpdated')`；轮询作为兜底机制（1秒间隔），正常路径不依赖轮询 |
| Bug 8 | 股票进出备选池后，节点上方数字不刷新 | `refreshStockTables()` 只处理了 `stock_state_pool`，未处理 `market_source` 类型节点 | 在 `refreshStockTables()` 中增加 `market_source` 分支：初始股票数从 pool 配置读取，运行时通过 `GET /api/pool/{nid}/stocks` 获取实时数量并更新徽标 |

### 21.7 端到端联调修复清单（v1.22）

真实浏览器端到端验证中发现并修复的前后端集成 Bug：

| Bug 编号 | 问题描述 | 根因 | 修复方案 |
|---------|---------|------|---------|
| Bug 9 | 仿真事件 ts 字段为秒数，但前端 `getEventTs` 将其当作 HH:MM:SS 字符串解析，导致时间戳错误 | 后端仿真事件的 `time` 字段为 Unix 时间戳秒数（纯数字），前端先做字符串分割解析，失败后才回退数字处理 | 重构 `getEventTs`：仿真模式下优先检测数字类型字段（time/timestamp/ts），秒→毫秒自动转换（`t < 1e12 ? t*1000 : t`），最后才尝试字符串时间解析 |
| Bug 10 | 仿真刚开始时矩阵/散点视图时间窗口包含负数，事件挤在右侧 | `minTs = now - DEFAULT_TIME_WINDOW`，仿真刚开始 now 很小导致 minTs < 0 | 在 `renderMatrix`/`renderScatter` 中判断仿真模式下 `minTs < 0` 时设为 0，重新计算时间跨度 |
| Bug 11 | 定时器队列始终显示 0，API 返回空 | 后端 `timer-queue` 端点未接收 `session_id`，`_EventPanel` 创建时未传入 `event_driver` | 修改 `engine.py`：`_EventPanel(event_bus, event_driver)` 传入驱动实例；`app.py` 的 `/api/events/timer-queue` 端点支持 `session_id` 参数，从 `EventDriver._heap` 获取待触发定时器，返回 `now/count/timers`；前端新增 `syncTimerQueue()` 每秒轮询 |
| Bug 12 | 事件 API 返回 61075 条历史事件，总数异常庞大 | 从 EventBus 读取时未按会话隔离，包含上次会话的所有历史事件 | 实现基于会话的 EventBus 偏移量跟踪：会话创建时记录 `bus_offset = pe_bus.total_published`，每次只读取偏移量之后的新事件，追加到 `session["events"]` 并更新偏移量；限制最大 50000 条环形缓冲 |
| Bug 13 | 从 EventBus 收集的事件 `type` 字段为 None，前端无法分类 | dataclass 实例直接序列化后缺少 event_type 信息，白名单过滤缺失 | 在 `sim_get_events` 中收集阶段即应用 `ALLOWED_EVENTS` 白名单过滤，通过 `_normalize_event()` 将 dataclass 实例转为字典并设置 `type = 类名`、`time = clock.now_sec` |
| Bug 14 | 前端调用 `/api/sim/start` 返回"池不存在: demo" | 演示池 ID `'demo'` 非后端有效 UUID，但前端直接传 `pool_id='demo'` | `startSimulationSession` 中增加 `isBackendPoolId` 判断（正则 `/^[0-9a-f]{8,}-/`），非 UUID 时传递完整 `config` 参数（含 stocks/edges 等）而非 `pool_id` |
| Bug 15 | 定时器时间线渲染报 `pad is not defined` JS 错误 | `renderTimerQueueTimeline` 使用了 `pad()` 函数但未定义 | 在函数内定义局部 `pad2 = n => String(n).padStart(2, '0')`，替换所有 `pad()` 调用 |
| Bug 16 | EdgeTimer 事件未出现在定时器队列，因 type 含 'edge' 被误分类为 'edge' | `EVENT_TYPE_TO_CATEGORY` 映射缺少 `edgetimer` 条目 | 添加 `'edgetimer': 'ttl'` 映射，所有 TTL/Edge 定时器统一归入 ttl 分类 |
| Bug 17 | 散点视图渲染报 `Cannot access 'rowH' before initialization` | `lastScatterLayout` 赋值引用了 `rowH`，但 `const rowH` 在后续行声明（TDZ 错误） | 将 `const catCount` 和 `const rowH` 声明提前到 `lastScatterLayout` 赋值之前 |
| Bug 18 | 定时器队列时间线使用 `Date.now()` 墙钟时间，污染仿真时间轴 | `syncTimerQueue` 计算 x 坐标时用 `Date.now()` 作为当前时间，与仿真时间坐标系不一致 | 全部统一使用仿真时间：API 返回的 `now` 字段（仿真虚拟时钟毫秒），前端 `getCurrentTime()` 获取仿真时间 |
| Bug 19 | 仿真步进 1-2 步即卡死，单步耗时 5-17 秒 | 默认仿真 100 只股票产生大量 Tick/DataChanged/BarComposed 事件，`fire_due()` 处理压力过大 | 将 `core/runtime_mode_module.py` 中 `_SIM_FZ_CODE_COUNT` 从 100 降至 20，显著降低事件频率，使步进响应时间可接受 |

### 21.8 前后端事件数据流（端到端验证通过）

```
┌─────────────────────────────────────────────────────────────────┐
│ 后端 (app.py + core/)                                           │
│                                                                 │
│  RuntimeSimulator.step()                                        │
│    └─ EventDriver.fire_due()                                    │
│         ├─ 触发 TickTimer → TickReceived                        │
│         ├─ 处理行情 → DataChanged                               │
│         ├─ 合成K线 → BarComposed                                │
│         ├─ 评估条件 → EdgeFired/FormulaEvaluated                │
│         └─ 状态转移 → TransferExecuted/TTLDue                   │
│              │                                                  │
│              ▼                                                  │
│         EventBus.publish(ev)  ──────►  pe_bus._events[]         │
│                                                                 │
│  /api/sim/events?session_id=X&since=N:                          │
│    1. 读取 session["events"]（前端 step 直返 + 轮询累积）        │
│    2. 从 pe_bus.get_events_since(bus_offset) 追加新事件          │
│    3. _normalize_event() 规范化：dataclass→dict, type/time字段   │
│    4. ALLOWED_EVENTS 白名单过滤                                  │
│    5. 返回 { events, total, now: simulation_time_ms }           │
│                                                                 │
│  /api/events/timer-queue?session_id=X:                          │
│    从 event_driver._heap 获取待触发定时器                         │
│    返回 { now, count, timers:[{code, fire_at, kind, ...}] }     │
└─────────────────────────────────────────────────────────────────┘
         │ fetch (1s polling)                │ fetch (1s polling)
         ▼                                    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 前端 (web/js/)                                                  │
│                                                                 │
│  AppState (全局 Store)                                          │
│    ├─ mode/simulationState/simulationTime                       │
│    ├─ events[] / timers[]                                       │
│    └─ subscribe/notify 状态广播                                  │
│                                                                 │
│  event-panel.js:                                                │
│    ├─ syncEvents(): 轮询 /api/sim/events, 追加到 events[]       │
│    ├─ syncTimerQueue(): 轮询 /api/events/timer-queue            │
│    ├─ renderMatrix(): 矩阵视图 Canvas                           │
│    │   ├─ 时间轴刻度（mm:ss，相对0点）                           │
│    │   ├─ 红色 NOW 线（transform GPU加速）                       │
│    │   └─ 分类行 × 事件方块（EVENT_STATE_STYLES 表驱动）         │
│    ├─ renderScatter(): 散点视图 Canvas                          │
│    │   ├─ ±500ms 滑动窗口                                       │
│    │   ├─ 50ms 时间桶聚合 + 计数徽标                             │
│    │   └─ Tooltip 悬停详情                                      │
│    └─ renderTimerQueueTimeline(): 定时器队列 Canvas+DOM         │
│        ├─ 绿色 NOW 线                                           │
│        ├─ ◆ 菱形定时器标记                                      │
│        └─ 倒计时文本（Xs后触发）                                  │
│                                                                 │
│  时间坐标系统一：仿真模式下全部使用 simulationTime（相对毫秒）    │
│  getCurrentTime() = AppState.simulationTime                      │
│  getEventTs(ev): 秒→毫秒自动转换，统一坐标系                     │
└─────────────────────────────────────────────────────────────────┘
```

### 21.9 与设计原则一致性

| 原则 | 关联章节 | 体现 |
|------|---------|------|
| 事件驱动架构 | §7 | AppState 的 subscribe/notify 机制是前端版 EventBus；状态变更通过事件广播，模块间零直接调用 |
| 表驱动架构 | §9 | EVENT_STATE_STYLES、TIMER_STATE_STYLES、SHORTCUTS、CATEGORY_CONFIG 四张表驱动样式与行为；新增事件类型/快捷键只加表项，不改逻辑 |
| 单一数据源 | §13 | window.AppState 是前端唯一真相源；禁止各模块维护局部状态副本；所有 UI 更新由 AppState 订阅驱动 |
| 性能优化（GPU 加速） | §16 | 矩阵 NOW 线、散点图用 `transform` 而非 `left/top` 定位，触发 GPU 合成层，避免重排；定时器倒计时用 `requestAnimationFrame` 批量更新 |

---

## Changelog

| 日期 | 版本 | 变更内容 |
|------|------|---------|
| 2026-03-25 | v1.22 | 端到端联调修复：11个前后端集成Bug修复（事件时间坐标系统一、EventBus会话偏移隔离、timer-queue API、仿真启动pool_id兼容、性能优化等）；散点图rowH TDZ修复、定时器pad2修复、EdgeTimer分类修复；事件数据流图（§21.8）；浏览器真实验证通过 |
| 2026-07-25 | v1.21 | §21 前端最终完善：全局状态管理 AppState、事件面板三视图（矩阵/散点图/定时器队列）、表驱动样式与快捷键、8 个前端 Bug 修复 |
