# 股票池实时事件驱动架构修正 - Product Requirement Document

## Overview
- **Summary**: 修正股票池系统为纯实时事件驱动架构：Tick到来→实时更新K线（含未闭合K线用最新价）→边时间到→对有新Tick的股票（与源池取交集）调用公式引擎（公式自己取最新K线数据计算）→通过的股票转移→触发交易信号。股票池层不感知K线闭合/bar_hash，只关心"哪些股票有新Tick"。公式引擎自己取数据计算，结果由filter_inputs做结果缓存。状态池只是股票代码集合（tick表的视图）。
- **Purpose**: 当前代码存在根本性设计错误：(1) 公式计算只用已闭合K线，违反实时计算要求；(2) 全局bar_hash缓存导致同tick内不同边调用返回错误结果；(3) changed_codes逻辑错误，不与源池取交集、不包含新入/出池股票；(4) EdgeFired事件逻辑有误，未正确传递变化股票。
- **Target Users**: 量化交易开发者、股票池策略仿真用户。

## Goals
- G1: **实时计算**：Tick到来即实时更新所有周期K线（未闭合K线的high/low/close用最新tick价格），公式一律用最新价计算，不管K线闭不闭合
- G2: **股票池不管K线**：股票池/边/筛选器不维护bar_hash、不判断K线闭合状态、不按周期分脏股票；只管"时间到了，changed_codes里这些股票有新tick，公式你去算"
- G3: **最简脏标记**：`changed_codes: Set[str]`为本轮有新Tick的股票集合。BarComposer是Tick消费者，不向changed_codes添加内容（有Bar变化必有Tick变化，已经在集合中）
- G4: **一个边一个EdgeFired事件**：携带changed_codes列表参数，非per-stock事件
- G5: **changed_codes精确计算**：数据脏→changed_codes ∩ 源池股票；节点脏→node_entered_codes ∪ node_exited_codes；兼有则取并集
- G6: **公式自己取数据**：FormulaEngine通过统一数据接口获取最新K线（bars_history闭合K线 + 当前未闭合K线），计算KDJ/MACD/CROSS，不依赖bar_hash缓存
- G7: **filter_inputs做结果缓存**：EdgeExecutor._filter的增量逻辑正确维护passed集合，公式引擎不做跨调用缓存（每次取最新数据计算）
- G8: **节点入/出跟踪**：股票入池时记录到node_entered_codes，TTL删除时记录到node_exited_codes，确保下游边在当前tick内评估
- G9: **仿真/实盘完全一致**：除TickSource不同，其余代码路径100%共用，K线获取方式一致（都含未闭合K线）
- G10: **状态池是视图**：状态池不存储独立行情数据，只维护股票代码集合，底层数据统一在state.latest_tick/state.bars/state.bars_history

## Non-Goals (Out of Scope)
- 不修改PythonFormulaEngine的KDJ/MACD/CROSS指标计算逻辑（但要确保喂入完整历史+当前K线）
- 不修改前端UI布局（保证事件浮窗正常显示即可）
- 不新增技术指标
- 不重构持久化/导入导出
- 不修改传播模式语义
- 不在股票池层引入bar_hash或周期分组脏标记

## Background & Context
正确的实盘运行流：
```
Tick到达（某只/多只股票最新价变化，间隔1-9s随机，同股固定）
  → DataUpdater.apply_data(tick_data)
      → 逐code更新state.latest_tick
      → state.add_changed_codes(advanced_codes)   // 本轮有新tick的股票
      → state.mark_data_dirty()
      → bus.publish(DataChanged(source="tick", codes=advanced_codes))

  → BarComposer.on_data_changed(event)  // 订阅tick事件
      → 逐周期更新K线：
          - 同bucket: _merge_tick（high=max, low=min, close=最新价, volume累加）
          - 新bucket: _append_closed_bar(闭合K线入history) + _new_bar_from_tick(新K线)
      → 更新state.bars[period][code]为最新K线（含未闭合）
      → bus.publish(DataChanged(source="bar", period=period, codes=advanced_codes))
      // 注意：BarComposer不调用add_changed_codes——有bar变化必有tick变化，已在集合中

  → EventDriver.fire_due(now)
      → for each due edge:
          src = edge.sid
          is_node_dirty = dirty.nodes.get(src, False)
          is_data_dirty = dirty.data and src in source_ids
          if not (is_node_dirty or is_data_dirty): continue

          changed = set()
          if is_data_dirty:
              source_codes = {code(s) for s in state.get_node_stocks(src) if isinstance(s, dict)}
              changed |= dirty.changed_codes & source_codes
          if is_node_dirty:
              changed |= dirty.node_entered_codes.get(src, set())
              changed |= dirty.node_exited_codes.get(src, set())

          bus.publish(EdgeFired(eid=eid, ts=now, changed_codes=list(changed)))

  → EdgeExecutor._on_edge_fired(event)
      → run(eid, changed_codes=event.changed_codes)
        → _gate()
        → _filter(spec, source_codes, eid, changed_codes)
            → changed_codes=None: 全量eval
            → changed_codes=[]: 用filter_inputs缓存，不算
            → changed_codes非空: 仅对changed_codes调用formula_engine.eval()
                formula_engine内部：
                  → 取bars_history[period][code]（闭合K线）
                  → 末尾追加bars[period][code]（当前未闭合K线，用最新价）
                  → 喂给PythonFormulaEngine计算KDJ/MACD
                  → 判断CROSS()
              其余股票从filter_inputs[eid]缓存取结果
              new_passed = (old_passed - changed_set) | newly_passed
        → _propagate(passed)
            → add_node_entered_code(tid, entered)
            → mark_node_dirty(tid)
        → _run_callback() → BUY/SELL Signal
        → _apply_ttl()
        → TTL到期:
            → add_node_exited_code(nid, exited)
            → 删除股票 → mark_node_dirty(nid)

  → state.clear_dirty()
```

## Functional Requirements
- **FR-1**: DirtyState维护changed_codes/nodes/data/node_entered_codes/node_exited_codes，提供对应add/clear方法；不存在period_codes字段
- **FR-2**: DataUpdater.apply_data tick推进后调用add_changed_codes(advanced_codes)，BarComposer不调用add_changed_codes
- **FR-3**: 公式K线获取必须包含当前未闭合K线（bars_history + bars[period][code]），live和simulation模式一致
- **FR-4**: FormulaEngine.eval不使用全局bar_hash缓存（或缓存key包含codes frozenset），确保每次取最新数据计算
- **FR-5**: _make_edge_action中changed_codes = (changed_codes ∩ 源池) ∪ node_entered_codes[src] ∪ node_exited_codes[src]
- **FR-6**: 每条边每次fire发布一个EdgeFired事件，携带changed_codes列表
- **FR-7**: _filter增量逻辑正确：changed_codes中股票重新计算，其余从filter_inputs缓存
- **FR-8**: _propagate调用add_node_entered_code；TTL到期调用add_node_exited_code
- **FR-9**: live和simulation K线获取路径一致，都通过bars_history_getter（含未闭合K线）
- **FR-10**: clear_dirty在每个tick fire_due后调用

## Non-Functional Requirements
- **NFR-1**: 正确性：实时K线+实时计算，不等闭合
- **NFR-2**: 性能：每tick仅对有新tick的股票(1-5只)调公式
- **NFR-3**: 解耦：模块仅通过EventBus通信
- **NFR-4**: 简洁：无bar_hash/period_codes过度设计
- **NFR-5**: 仿真=实盘：仅TickSource不同

## Constraints
- Python 3.10+, FastAPI/uvicorn, 现有架构不推翻
- 兼容sim_demo_pool配置
- HQChartPy2 Python公式引擎、pandas

## Assumptions
- PythonFormulaEngine.eval_batch对每只股票取含history+current的DataFrame计算
- CROSS(A,B)判断A从下方穿越B
- TTL删除后出边在当前tick评估

## Acceptance Criteria

### AC-1: K线实时更新含未闭合K线
- **Given**: 某股票当前K线(open=10.0,high=10.2,low=9.9,close=10.1)，最新tick价10.3
- **When**: BarComposer.on_tick处理该tick
- **Then**: state.bars中该K线high=10.3, close=10.3；公式引擎取到的DataFrame末尾包含此未闭合K线
- **Verification**: `programmatic`

### AC-2: 公式实时计算不等闭合
- **Given**: E1(5m KDJ,1min周期)
- **When**: tick波动导致未闭合5m K线K上穿D
- **Then**: 下一次E1 fire时该股票判定为金叉进入A池，不等5m闭合
- **Verification**: `programmatic`

### AC-3: changed_codes精确
- **Given**: changed_codes={fz000001,fz000002,fz999999}，源池含前两者不含fz999999
- **When**: E1数据脏触发
- **Then**: EdgeFired.changed_codes=[fz000001,fz000002]
- **Verification**: `programmatic`

### AC-4: 一边一事件
- **Given**: E1触发，3只股票需评估
- **When**: 发布事件
- **Then**: 仅1个EdgeFired，changed_codes长度3
- **Verification**: `programmatic`

### AC-5: 新入池股票被评估
- **Given**: fz000001刚入A池
- **When**: A池出边当前tick触发
- **Then**: changed_codes含fz000001
- **Verification**: `programmatic`

### AC-6: 无错误bar_hash缓存
- **Given**: 同tick内同公式被不同边/不同codes调用
- **When**: 两次调用
- **Then**: 不返回错误codes的缓存结果
- **Verification**: `programmatic`

### AC-7: 仿真端到端
- **Given**: 加载100只fz股票sim_demo_pool
- **When**: 运行仿真
- **Then**: 代码8字符(fz000001)；Tick间隔1-9s同股固定；A池/B池有股票；A∩B→C池BUY100股；C池TTL20min后SELL
- **Verification**: `programmatic` + `human-judgment`

### AC-8: 模块解耦
- **Given**: core/模块
- **When**: 静态检查import
- **Then**: BarComposer不import EdgeExecutor；跨模块仅经EventBus
- **Verification**: `programmatic`

### AC-9: UI验证
- **Given**: 服务器启动
- **When**: Playwright操作
- **Then**: 可加载/启动仿真；事件浮窗显示事件链
- **Verification**: `human-judgment`

## Open Questions
- 无
