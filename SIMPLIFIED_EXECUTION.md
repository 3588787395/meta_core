# 股票池简化执行文档

## 1. 一句话本质
股票池 = 状态池流水线。每个变换单元 = (条件转移边 + 转移条件 + 无条件转移边) 三元组，将一个状态池（视图）经条件计算变换为下一个状态池（视图）。从备选池出发，不断添加条件，形成下一状态池。核心矛盾 = 时机 × 强弱，两者正交独立，缺一不可。

## 2. 三种表
- **配置表（JSON 文件）**：定义"怎么执行"。27 张引擎级配置表，启动加载，运行时只读。新增功能 = 加 JSON 条目，零行 engine.py 改动。
- **运行时表（内存 Dict）**：此刻的真实状态。`node_stocks[nid]` 是核心中的核心——key=节点ID，value=股票列表。运行时表是真相源，持久表只是影子。
- **持久表（SQLite）**：审计影子。记录"发生过什么"，不影响当下决策。写多读少，跨进程。

## 3. 核心循环：状态池变换单元（三元组一体）

### 变换单元定义

**变换单元 = (条件转移边 + 转移条件 + 无条件转移边) 三元组**，是股票池的原子计算单位。

```
状态池N ──条件转移边──▶ 条件节点(转移条件) ──无条件转移边──▶ 状态池N+1
  (输入视图)         (filter计算)              (输出视图)
```

- **条件转移边**：从源状态池指向条件节点，传递股票到条件节点进行计算
- **转移条件**：条件节点本身，对输入股票做 filter（强弱筛选）
- **无条件转移边**：从条件节点指向目标状态池，将筛选结果 propagate 到下一状态池

三者一体，不可分割。当前实现用 `for edge in edges:` 逐边处理并依赖边顺序隐式拼出三元组语义；优化方向是以变换单元为粒度显式分组执行。

以下五步作用于变换单元（三元组）而非单条独立边：

| 步骤 | 读哪张表 | 写哪张表 | 核心逻辑位置 |
|------|---------|---------|-------------|
| gate（时机门控） | timing.json + _flow_duration_starts + _flow_exec_counts + _pool_start_time → bool | 无 | engine.py `_dispatch_gate` |
| filter（强弱筛选） | node_stocks[src] + dispatch.json + engines.json → {passed, rejected} | 无（返回结果） | builtins_filters.py |
| propagate（状态流转） | node_stocks[src] + node_stocks[tgt] | node_stocks[tgt] + node_stocks[src]（move 时清空） | builtins.py `_propagate` |
| callback（持久化副作用） | node_stocks[tgt] + action_table.json | node_state + stock_transfer_log + .dat/.log 文件 | app.py `_dispatch_pool_enter_actions` |
| ttl（超时淘汰） | tdx_psatt.json + node_stocks[tgt] | node_stocks[tgt]（删除超时股票） | engine.py `_apply_tdx_psatt_ttl` |

## 4. 时机轴（24 种组合）
时机作用于变换单元（三元组）的 gate 步骤，而非单条边。
starttype(0~7) × cxtype(0~2) = 24 种时机组合，全部通过 timing.json 配置表驱动。

| starttype | 含义 |
|-----------|------|
| 0 | 立即 |
| 1 | 延迟 N 秒 |
| 2 | 开市前 |
| 3 | 开市后 |
| 4 | 收市前 |
| 5 | 收市后 |
| 6 | 指定交易日时间 |
| 7 | 指定时间 |

| cxtype | 含义 |
|--------|------|
| 0 | 一直执行 |
| 1 | 持续窗口 |
| 2 | 只执行一次 |

**时机的重要性**：竞价三步倒（starttype=2）、金色两点半（starttype=4）、尾盘抢筹（starttype=4+cxtype=2）——策略胜负不在筛选条件多精妙，而在什么时候执行。同一组条件，9:25 执行和 14:57 执行，结果天壤之别。

## 5. 强弱轴（60 种组合）
强弱作用于变换单元（三元组）的 filter 步骤，而非单条边。
nset(0~5) × noperate(0~9) = 60 种筛选组合，通过 dispatch.json:nset_dispatch 子表驱动。

| nset | 含义 |
|------|------|
| 0 | 技术指标 |
| 1 | 条件选股 |
| 2 | 专家系统 |
| 3 | 最新财务 |
| 4 | 实时行情 |
| 5 | 逻辑运算 |

| noperate | 含义 |
|----------|------|
| 0 | 等于 |
| 1 | 大于 |
| 2 | 小于 |
| 3 | 上穿 |
| 4 | 下穿 |
| 5 | 排名为 |
| 6 | 排名前 N |
| 7 | 排名后 N |
| 8 | 上拐 |
| 9 | 下拐 |

**强弱的重要性**：龙头股 vs 跟风股的区别不在概念，在强弱排序。同一时刻，涨幅前 10 和后 10 的股票，次日表现可能完全相反。sorttype 的取值决定了你是抓龙头还是捡垃圾。

## 6. 三种运行模式

| 模式 | 时间源 | 数据源 | 交易接口 | 副作用 | 循环控制 |
|------|--------|--------|----------|--------|----------|
| live（实盘） | wall_clock | TQ SDK 实时推送 | live_order | 允许全部 | 暂停/恢复/停止 |
| replay（回放） | sequence（K线时间轴） | 历史 K 线缓存 | noop | 只读 | 播放/暂停/步进/变速 |
| simulation（仿真） | virtual（虚拟时钟） | Mock 随机生成 | paper_trade | 可选 | 步进/运行到/暂停/重置 |

## 7. 功能 → 表操作映射总表

以下映射以变换单元（三元组）为粒度，gate/filter/propagate/callback/ttl 五步作用于变换单元。

| 功能名 | 读表 | 写表 | 核心逻辑位置 |
|--------|------|------|-------------|
| starttype 门控 | timing.json:starttype_rules | 无（纯判断） | engine.py `_dispatch_gate` |
| cxtype 过期 | timing.json:cxtype_rules | _flow_duration_starts/_flow_exec_counts | engine.py `_dispatch_duration` |
| TTL 淘汰 | tdx_psatt.json:ttl_units | node_stocks[tgt] | engine.py `_apply_tdx_psatt_ttl` |
| 6 种 psatt 副作用 | action_table.json | 调用回调 | app.py `_dispatch_pool_enter_actions` |
| 强弱筛选 | dispatch.json + engines.json | 无 | builtins_filters.py |
| TDX 公式评估 | tdx_indicators.json | 无 | tdx_evaluators.py |
| 降级链 | fallback_chain.json | 无 | builtins.py `_resolve_fallback` |
| propagate 模式 | flow_mode_registry.json | node_stocks[tgt/src] | engine.py `_resolve_flow_attrs` |
| 池角色解析 | pool_roles.json | 无 | engine.py `_resolve_role` |
| 事件生成 | event_rules.json | _event_queue | engine.py `_emit_transfer_events` |
| 信号生成 | signal_rules.json | _signal_queue | engine.py `_emit_transfer_events` |
| 持仓跟踪 | tracker_schema.json:formulas | _tracker | engine.py `_update_trackers` |
| post_tick PK 排名 | pk_config.json | _pk_rankings | builtins_post_tick.py |
| post_tick 分析角度 | analysis_config.json | _angle_results | builtins_post_tick.py |
| post_tick 看盘面板 | dashboard_schema.json | _dashboard_data | builtins_post_tick.py |
| post_tick 监控告警 | alert_rules.json | _alert_events/_alert_queue | builtins_post_tick.py |
| 时间源 | time_sources.json | _current_time_source | engine.py `_now` |
| 节点初始化 | edge_strategies.json:node_init | node_stocks[src] | engine.py `_init_node_stocks` |
| 运行模式初始化 | runtime_modes.json | _current_time_source | engine.py `run_mode` |
| 边策略路由 | edge_strategies.json | 无 | engine.py `_execute_flowsCore` |
| 边类型语义 | edge_semantics.json | _filter_cache/_last_snapshot | engine.py `_resolve_edge_type` |
| CRUD 持久化 | pool_config（持久表） | pool_config/pool_node/pool_edge | storage.py |
| XML 导入导出 | xml_mapping.json | .xml 文件 + pool_config | converters/ |
| JSON 导入导出 | 无 | JSON 文件 | converters/json_converter.py |
| 回放执行 | kline_cache + 全部配置表 | replay_snapshot/replay_session | kline_replay_engine.py |
| 仿真运行 | mock_data.json + timing.json:simulator | _state_pools/_virtual_clock | runtime_simulator.py |
| 域代码归一化 | tdx_psatt.json:time_formats | 无 | engine.py `_parse_intime_to_ts` |
| 数据注入 | data_config.json:injection_rules | node_stocks[src] | engine.py `_inject_bar_data` |
| 备选池刷新 | data_config.json:refresh_rules | 无 | engine.py `_check_refreshed_pool_data` |
| 历史记录 | history_schema.json | .dat/.log 文件 | app.py `_append_history_entry` |

## 8. 核心循环伪代码

```python
def _execute_flowsCore(nodes, edges, node_stocks, bar_data):
    # 变换单元分组：以条件节点为枢纽，配对入边(条件转移边)与出边(无条件转移边)
    # 无法配对的边回退到逐边处理（向后兼容）
    units, standalone_edges = _group_transformation_units(edges, nodes)
    for unit in units:                # 三元组原子执行
        if not gate(unit.in_edge): continue      # 读 timing.json + 运行时表
        result = filter(unit.condition)           # 读 dispatch.json → 强弱筛选
        propagate(unit.out_edge, result)          # 写 node_stocks[tgt]
        callback(tid, node_stocks, new)           # 读 action_table.json → 写持久表
        ttl(tid, node_stocks)                     # 读 tdx_psatt.json → 写运行时表
    for edge in standalone_edges:     # 独立边回退逐边逻辑
        ...
    return node_stocks, tevs
```

### 逐边回退逻辑（参考）

```python
def _execute_flowsCore(nodes, edges, node_stocks, bar_data):
    for edge in edges:
        if not gate(edge): continue          # 读 timing.json + 运行时表
        strat = edge_strategies[key]          # 读配置表
        handler = _handler_registry[strat.handler]
        result = handler(action_inputs)       # filter + propagate
        callback(tid, node_stocks, new)       # 读 action_table.json → 写持久表
        ttl(tid, node_stocks)                 # 读 tdx_psatt.json → 写运行时表
        _flow_exec_counts[eid] += 1           # 写运行时表
    return node_stocks, tevs
```

这段代码不含任何股票代码、市场类型、指标名称等字面量。新增条件类型？改 dispatch.json。新增时机？改 timing.json。新增分析角度？改 analysis_config.json。零行 Python。
