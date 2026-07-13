# DESIGN — 执行流视角的表操作映射

## 1. 一句话本质

**股票池 = 有向图上的逐边条件过滤。** 每条边 = gate→filter→propagate→callback→ttl。

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