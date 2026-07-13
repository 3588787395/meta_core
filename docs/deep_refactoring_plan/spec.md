# 股票池深度重构规划 v1.0

> 核心洞察：最新 tick 时间不变 → 所有 K 线数据不变 → 不需重新计算
> 设计原则：表驱动、数据驱动、事件驱动
> 目标：engine.py 从 3504 行 → ≤ 800 行，配置表从 50+ 张 → ≤ 12 张核心表

---

## 一、本质认知：股票池到底是什么

### 1.1 一句话本质

**股票池 = 一组节点 × 一组边 × 一个时间水位线。**

- **节点**：装股票的容器（备选池/状态池/条件池/目标池）
- **边**：节点之间的连接，带触发条件（时间 + 过滤条件）
- **时间水位线**：`latest_tick_ts` —— 所有数据的最新时间戳。水位线不涨，所有计算结果不变。

### 1.2 运行时只有三件事

| # | 事情 | 触发条件 | 做什么 |
|---|------|---------|-------|
| 1 | **数据更新** | 外部行情推送 / K线到达 | 写 `latest_tick` 表，更新 `latest_tick_ts` |
| 2 | **边触发判定** | 时间水位线变化 或 源节点股票变化 | 查 `edge_triggers` 表，看哪些边该执行了 |
| 3 | **边执行** | 边触发条件满足 | filter → propagate → 目标节点更新 → 预警事件 |

**就这三件事。** 其他所有东西（拓扑、执行顺序、公式、K线合成、界面刷新）全都是运行前就确定的，运行时只读不写。

### 1.3 为什么之前代码又臭又长

因为把"运行前确定的东西"和"运行时变化的东西"搅在一起了：
- 每 tick 都重算拓扑序 → 错！拓扑运行前就定了
- 每 tick 都重新解析边参数 → 错！边参数运行前就编译好了
- 每 tick 都遍历所有节点所有边 → 错！只有脏节点的出边才需要检查
- 数据更新和过滤计算混在一起 → 错！数据层和计算层必须分离

---

## 二、核心设计：三张核心表 + 一张水位线

### 2.1 运行时内存表（一共 4 张核心，不是 30+ 张）

| 表名 | 类型 | 读时机 | 写时机 | 说明 |
|------|------|--------|--------|------|
| `latest_tick` | Dict[code → bar_dict] | filter 计算时读 | 行情推送时写 | **唯一真相源**。所有股票的最新tick数据 |
| `latest_tick_ts` | float | 边触发判定时读 | 行情推送时写 | **时间水位线**。只要这个值不变，所有K线计算结果都不变 |
| `node_stocks` | Dict[nid → List[stock]] | propagate 读写、filter 读 | 边执行后写 | 各节点当前股票列表 |
| `node_stocks_ts` | Dict[nid → float] | 边触发判定时读 | 节点股票变化时写 | 每个节点的股票最后变化时间戳 |

**就这四张核心运行时表。** 其他的都是运行前编译产物或配置表。

### 2.2 为什么 `latest_tick_ts` 是灵魂

用户的核心洞察：**最新 tick 时间不变，则所有 K 线数据不变，所以不需重新计算。**

展开说：

```
行情推送 → 写 latest_tick[code] = new_bar
         → 计算 new_hash = hash(all bars)
         → 如果 new_hash == old_hash：什么都不做，退出
         → 如果 new_hash != old_hash：
             latest_tick_ts = now()  # 水位线涨了
             标记所有源节点为"数据脏"
             然后才开始检查哪些边该执行
```

**水位线没涨 = 数据没变 = 所有公式结果不变 = 过滤结果不变 = 什么都不用算。**

这是整个系统最重要的性能优化和逻辑简化支点。

### 2.3 节点脏标记的本质

节点为什么会"脏"？两个原因：

| 脏的原因 | 标记时机 | 检查方式 |
|---------|---------|---------|
| **股票变化** | 股票入池/出池/TTL淘汰 | `node_stocks_ts[nid] > last_processed_ts` |
| **数据变化** | 水位线涨了（最新tick更新） | `latest_tick_ts > last_processed_ts` |

**一个节点只要不脏，它的所有出边都不用检查。**

---

## 三、运行前 vs 运行时：严格分离

### 3.1 运行前（设计时 + 加载时）做的事

全部一次性做完，运行时只读。

| 阶段 | 做什么 | 产出 |
|------|--------|------|
| **设计时** | 用户拖拽节点、连线、配置参数、设置执行顺序 | pool_config (JSON) |
| **加载时** | 解析 pool_config，编译成运行时可用的结构 | CompiledPool |

**CompiledPool 是什么？** 就是把运行时需要的所有信息都预先算好，编好索引，排好顺序。运行时直接用，不再解析。

```python
CompiledPool = {
    # 节点
    'nodes': {nid: node_dict},           # 节点字典，按id查
    'node_type': {nid: type_name},       # 节点类型，快速判定
    
    # 边
    'edges': {eid: edge_dict},           # 边字典
    'edge_endpoints': {eid: (sid, tid)}, # 边的端点，预解析
    'edge_order': [eid1, eid2, ...],     # **用户指定的执行顺序**，不是拓扑序！
    'edge_type': {eid: 'conditional' | 'unconditional'},  # 边类型
    'edge_filter_spec': {eid: spec},     # 过滤条件的编译结果
    'edge_timing_spec': {eid: spec},     # 时间触发条件的编译结果
    'edge_propagate_spec': {eid: spec},  # 传播模式的编译结果
    
    # 邻接表
    'out_edges': {nid: [eid, ...]},      # 节点的出边
    'in_edges': {nid: [eid, ...]},       # 节点的入边
    
    # 源节点列表（入度为0的节点）
    'source_nodes': [nid, ...],
    
    # 角色映射（哪些是目标池、哪些是备选池）
    'node_role': {nid: 'candidate' | 'state' | 'condition' | 'target' | 'discard'},
}
```

**关键：执行顺序是用户指定的，不是拓扑排序的结果。**

用户可以在综合设置里调整边的执行顺序，存在 `edge.params._order` 里。加载时按 `_order` 排序得到 `edge_order` 列表，运行时就按这个顺序来。拓扑只是用来画界面和校验合法性的，不是执行顺序。

### 3.2 运行时做的事

就一个循环：

```
1. 等数据更新（或时间步进）
2. 水位线涨了吗？
   → 没涨：回去等
   → 涨了：继续
3. 按执行顺序遍历每条边：
   a. 源节点脏吗？（股票变了 或 数据变了）
   b. 时间触发条件到了吗？
   c. a 和 b 都满足 → 执行这条边
   d. 执行完 → 目标节点标记为股票脏
4. 所有边处理完 → 发事件（入池/出池/预警）
5. 回去等下一次数据更新
```

**就这么简单。**

---

## 四、功能-表操作对应表

### 4.1 数据层（最新tick表）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| 行情推送 | — | latest_tick[code] = new_bar | 比较 hash，判断水位线要不要涨 |
| 水位线更新 | latest_tick 的 hash | latest_tick_ts | 计算全量 tick 的 hash |
| 源节点标记脏 | latest_tick_ts | 源节点的脏标记 | 水位线 > 上次处理时间 → 标记脏 |

### 4.2 边触发判定层

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| 边时间触发检查 | edge_timing_spec[eid] + latest_tick_ts + _flow_state[eid] | — | starttype × cxtype 的 24 种判定 |
| 边数据触发检查 | node_stocks_ts[sid] + latest_tick_ts | — | 源节点脏不脏 |
| 边是否该执行 | 上面两个结果的 AND | — | 时间到了 且 源节点脏 → 执行 |

### 4.3 边执行层（filter + propagate）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| 条件过滤 | latest_tick + node_stocks[sid] + edge_filter_spec[eid] | — | 公式计算 / 条件选股 / 财务筛选 / 行情筛选 |
| 状态传播 | node_stocks[sid] + node_stocks[tid] + edge_propagate_spec[eid] | node_stocks[tid] + node_stocks_ts[tid] | copy / move / overwrite |
| TTL 淘汰 | node_stocks[tid] + ttl_spec + latest_tick_ts | node_stocks[tid] + node_stocks_ts[tid] | 超时删除 |

### 4.4 事件层

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| 入池事件 | node_stocks 新旧对比 | event_queue | 差集计算（新 - 旧） |
| 出池事件 | node_stocks 新旧对比 | event_queue | 差集计算（旧 - 新） |
| 预警事件 | alert_rules + node_stocks 变化 | alert_queue | 规则匹配 |
| 交易信号 | node_role[tid] == 'target' + 入池/出池 | signal_queue | 角色判定 + 信号生成 |

### 4.5 后处理层（PK排名/分析角度/看盘面板）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| PK排名 | node_stocks[target] + latest_tick + pk_config | _pk_rankings | 按权重评分排序 |
| 分析角度 | node_stocks[target] + latest_tick + analysis_config | _angle_results | 多维度计算 |
| 看盘面板 | node_stocks + latest_tick + dashboard_schema | _dashboard_data | 组装显示数据 |

**注意：后处理是"只读计算 + 写结果缓存"，不影响 node_stocks。** 是附属功能，不是核心流程。

---

## 五、表驱动：逻辑在表结构里，差异在表内容里

### 5.1 时间触发（timing.json）

**结构：** `{starttype: {rule, evaluator}, cxtype: {rule, evaluator}}`

**24 种组合 = 8 种 start × 3 种 cx**，不是 24 个 if，而是两张表的笛卡尔积：

```python
def eval_gate(edge, now_ts, flow_state):
    start_ok = _START_RULES[edge.starttype](edge, now_ts, flow_state)
    if not start_ok: return False
    cx_ok = _CX_RULES[edge.cxtype](edge, now_ts, flow_state)
    return cx_ok
```

**8 + 3 = 11 条规则**，覆盖 24 种组合。这才叫表驱动——不是把 24 个 if 塞进 JSON，而是找到两个正交维度，用组合来表达。

### 5.2 过滤条件（filter_specs.json）

**结构：** `{nset: {evaluator, operators: {noperate: op_fn}}}`

**6 种 nset × 10 种 noperate = 60 种组合**，同样是两张表的笛卡尔积：

```python
def eval_filter(codes, spec, tick_data):
    values = spec['evaluator'](codes, spec.formula, tick_data)  # nset 决定怎么求值
    passed = spec['operator'](values, spec.threshold)           # noperate 决定怎么比
    return passed
```

**6 个求值器 + 10 个比较器 = 60 种筛选方式。**

### 5.3 传播模式（propagate_modes.json）

**结构：** `{mode_name: {op: fn, affects_source: bool}}`

| 模式 | 操作 | 影响源节点 |
|------|------|-----------|
| copy | target += passed | 否 |
| move | target += passed; source = [] | 是 |
| overwrite | target = passed | 否 |

**3 种模式 = 3 条记录，不是 3 个 if。**

### 5.4 节点角色（node_roles.json）

**结构：** `{role_name: {triggers: [], actions: []}}`

角色决定了节点"发生变化时该做什么"：

| 角色 | 入池时做什么 | 出池时做什么 |
|------|------------|------------|
| candidate | 标记出边脏 | — |
| state | 标记出边脏 | — |
| condition | 标记出边脏（立即 propagate） | — |
| target | 发 ENTER 事件 + BUY 信号 + 写历史 | 发 EXIT 事件 + SELL 信号 |
| discard | 发 EXIT 事件 | — |

**角色表驱动，不是 if node.type == 'target'。**

---

## 六、配置表：从 50+ 张收敛到 12 张核心

### 6.1 核心配置表（运行时引擎直接读的）

| # | 表名 | 作用 | 运行时机 |
|---|------|------|---------|
| 1 | `timing.json` | 时间触发规则（starttype + cxtype） | gate 判定 |
| 2 | `filter_specs.json` | 过滤条件规格（nset + noperate） | filter 计算 |
| 3 | `propagate_modes.json` | 传播模式（copy/move/overwrite） | propagate |
| 4 | `node_roles.json` | 节点角色行为定义 | 事件/信号生成 |
| 5 | `edge_semantics.json` | 边类型语义（条件/无条件） | 边类型判定 |
| 6 | `runtime_modes.json` | 运行模式（实盘/回放/仿真） | 模式切换 |
| 7 | `alert_rules.json` | 预警规则 | 预警检查 |
| 8 | `ttl_rules.json` | TTL 淘汰规则 | TTL 计算 |

**8 张核心配置表。** 引擎核心循环只直接读这 8 张。

### 6.2 外围配置表（后处理/UI/导入导出用的）

| # | 表名 | 作用 | 谁读 |
|---|------|------|------|
| 9 | `post_tick_pipeline.json` | 后处理流水线顺序 | post_tick 模块 |
| 10 | `pk_config.json` | PK 排名配置 | pk_ranking 模块 |
| 11 | `analysis_config.json` | 分析角度配置 | analysis 模块 |
| 12 | `dashboard_schema.json` | 看盘面板配置 | dashboard 模块 |
| 13 | `xml_mapping.json` | XML 导入导出映射 | converters 模块 |
| 14 | `history_schema.json` | 历史记录格式 | history 模块 |
| 15 | `cell_type_registry.json` | 节点类型注册 | UI/table_engine |
| 16 | `data_config.json` | 数据源配置 | 数据层 |

**这些是外围的，不影响核心引擎的简洁性。** 核心引擎 8 张表就够了。

---

## 七、代码结构：核心极薄，外围分层

### 7.1 目录结构

```
core/
  engine.py           # 核心引擎 ≤ 800 行
                       # 只做：数据更新 → 脏标记 → 边遍历 → 执行 → 事件
  runtime.py          # 运行时表定义（latest_tick / node_stocks / 脏标记）
  compiler.py         # 编译期：pool_config → CompiledPool
  timing.py           # 时间触发规则（查表 + 组合）
  filters.py          # 过滤条件（求值器 + 比较器）
  propagate.py        # 传播模式
  roles.py            # 节点角色行为
  events.py           # 事件/信号生成

data/
  tick_table.py       # latest_tick 表管理（读/写/hash比较）
  kline_cache.py      # K线缓存
  providers/          # 各数据源适配器

post_processing/
  pk_ranking.py       # PK 排名
  analysis_angles.py  # 分析角度
  dashboard.py        # 看盘面板
  alerts.py           # 预警

config/               # 16 张 JSON 配置表
```

### 7.2 engine.py 核心循环伪代码（约 100 行）

```python
class StockPoolEngine:
    
    def compile(self, pool_config):
        """运行前：一次性编译，返回 CompiledPool"""
        return self._compiler.compile(pool_config)
    
    def on_tick_update(self, compiled, tick_data):
        """数据更新入口：写 latest_tick，更新水位线"""
        changed = self._tick_table.update(tick_data)
        if not changed:
            return  # 水位线没涨，什么都不用做
        
        # 标记所有源节点为数据脏
        for nid in compiled.source_nodes:
            self._mark_data_dirty(nid)
    
    def run_once(self, compiled):
        """执行一次：按执行顺序遍历边，脏的才处理"""
        events = []
        
        for eid in compiled.edge_order:
            sid, tid = compiled.edge_endpoints[eid]
            
            # 检查：源节点脏不脏？
            if not self._is_node_dirty(sid):
                continue
            
            # 检查：时间触发条件满足吗？
            if not self._timing.eval_gate(eid, compiled.edge_timing_spec[eid]):
                continue
            
            # 执行边
            new_stocks = self._execute_edge(eid, compiled)
            
            # 目标节点股票变了吗？
            if self._node_stocks_changed(tid, new_stocks):
                self._node_stocks[tid] = new_stocks
                self._mark_stock_dirty(tid)
                events.extend(self._roles.on_change(tid, compiled.node_role[tid]))
        
        # 清脏标记
        self._clear_all_dirty()
        
        return events
    
    def _execute_edge(self, eid, compiled):
        """执行一条边：filter → propagate"""
        sid, tid = compiled.edge_endpoints[eid]
        spec = compiled.edge_filter_spec[eid]
        mode = compiled.edge_propagate_spec[eid]
        src_stocks = self._node_stocks[sid]
        
        # 条件边才需要 filter，无条件边直接 propagate
        if compiled.edge_type[eid] == 'conditional':
            passed, rejected = self._filters.eval(src_stocks, spec, self._tick_table.data)
        else:
            passed = src_stocks
        
        # propagate
        return self._propagate.apply(src_stocks, self._node_stocks[tid], passed, mode)
```

**核心循环就这么多。** 简洁、清晰、可验证。

---

## 八、执行顺序 vs 拓扑：两个完全不同的东西

### 8.1 拓扑是什么

拓扑 = 节点和边的连接关系图。

- 用来画界面（节点位置、连线走向）
- 用来校验合法性（有没有环、有没有孤立节点）
- 用来找源节点（入度为 0 的节点）

**拓扑不是执行顺序。**

### 8.2 执行顺序是什么

执行顺序 = 用户指定的边的处理先后次序。

- 用户在综合设置里调整（点边分配编号）
- 存在 `edge.params._order` 里
- 加载时按 `_order` 排序，得到 `edge_order` 列表
- 运行时就按这个顺序处理边

**为什么用户需要调整执行顺序？** 因为有些策略依赖处理顺序。比如：
- 先执行 A 边过滤，再执行 B 边过滤
- 或者先 move 再 copy，和先 copy 再 move，结果不一样

用户最懂自己的策略，所以执行顺序必须让用户自己定。

### 8.3 拓扑排序的作用

拓扑排序只在一个地方用：**首次执行时，给用户推荐一个默认的执行顺序。**

用户加载一个新模板时，系统自动按拓扑深度给边分配默认编号。用户可以改，改了就以用户的为准。

---

## 九、事件驱动：不是轮询，是响应

### 9.1 触发条件三要素

一条边什么时候执行？三个条件同时满足：

| 条件 | 检查什么 | 对应变量 |
|------|---------|---------|
| **时间到了** | starttype + cxtype 判定通过 | timing.eval_gate() |
| **源节点股票变了** | 节点股票有增减 | node_stocks_ts[sid] > last_processed_ts |
| **数据更新了** | 最新 tick 水位线涨了 | latest_tick_ts > last_processed_ts |

**三个条件的关系：**
- 无条件边：源节点股票变了 → 立即 propagate（不需要时间条件，也不需要数据更新）
- 条件边：时间到了 且 (股票变了 或 数据更新了) → 执行 filter + propagate

### 9.2 脏标记传播

```
源节点数据脏
  ↓
出边1时间到了吗？→ 到了 → 执行 → 目标节点股票脏
  ↓                        ↓
出边2时间到了吗？→ 没到    目标节点出边...
  ↓
...
```

**脏标记沿着边传播，但只有时间条件满足的边才会真的执行。**

### 9.3 为什么不是每 tick 都遍历所有边

因为大部分时候：
1. 水位线没涨 → 数据没变 → 所有条件边的 filter 结果都一样 → 不用算
2. 大部分节点不脏 → 它们的出边不用检查
3. 大部分边的时间条件没到 → 即使源节点脏也不执行

**理想情况下（比如行情平淡时），一次 tick 可能只触发 0 条或 1-2 条边。** 而不是每次都全量计算。

---

## 十、数据层独立：K 线更新是另一回事

### 10.1 K 线更新和股票池计算是两条线

```
┌─────────────────┐     ┌─────────────────┐
│   行情数据推送   │────▶│  latest_tick    │  ← 唯一真相源
└─────────────────┘     └─────────────────┘
                               │
                 ┌─────────────┴─────────────┐
                 ▼                           ▼
          ┌─────────────┐           ┌─────────────┐
          │ K线合成器    │           │ 股票池引擎   │
          │ (1分/5分/日) │           │ (过滤/传播)  │
          └─────────────┘           └─────────────┘
                 │                           │
                 ▼                           ▼
          ┌─────────────┐           ┌─────────────┐
          │ kline_cache │           │ node_stocks │
          └─────────────┘           └─────────────┘
```

- **latest_tick 是唯一真相源**，所有数据都从这里来
- **K 线合成器** 订阅 latest_tick 的变化，合成各周期 K 线
- **股票池引擎** 也订阅 latest_tick 的变化，执行过滤计算
- 两者是**同级别的消费者**，不是上下游关系

### 10.2 公式计算用什么数据

公式需要什么周期的 K 线，就从 kline_cache 里拿。kline_cache 由 K 线合成器独立维护。

**股票池引擎不负责 K 线合成。** 它只消费数据，不生产数据。

---

## 十一、前端三模式（实盘/回放/仿真）的本质统一

### 11.1 三个模式的差异只有三个维度

| 维度 | 实盘 | 回放 | 仿真 |
|------|------|------|------|
| **时间源** | 系统时间 | K线时间轴 | 虚拟时钟 |
| **数据源** | 实时行情推送 | 历史K线序列 | Mock生成 |
| **交易接口** | 真实下单 | 空操作 | 模拟记账 |

### 11.2 统一的核心循环

```python
def run_mode(mode_config):
    time_source = TIME_SOURCES[mode_config.time_source]
    data_source = DATA_SOURCES[mode_config.data_source]
    trade_iface = TRADE_INTERFACES[mode_config.trade_interface]
    
    while running:
        now = time_source.now()               # 不同模式，时间来源不同
        tick_data = data_source.get(now)      # 不同模式，数据来源不同
        events = engine.run_once(compiled, tick_data, now)
        trade_iface.process(events)           # 不同模式，交易行为不同
        time_source.wait_next()               # 不同模式，等待方式不同
```

**核心引擎完全一样。** 差异只在三个"插件"上：时间源、数据源、交易接口。

---

## 十二、实施计划

### 阶段一：核心重构（2周）

目标：engine.py 从 3504 行 → ≤ 800 行，核心循环清晰可验证

| 任务 | 内容 | 产出 |
|------|------|------|
| T1 | 提取 latest_tick 独立表 | tick_table.py，含 hash 比较、水位线 |
| T2 | 提取编译器 compiler.py | pool_config → CompiledPool |
| T3 | 重写核心循环 | 按执行顺序 + 脏标记 + 三要素触发 |
| T4 | 时间触发表驱动化 | timing.py，8+3 规则覆盖 24 组合 |
| T5 | 过滤条件表驱动化 | filters.py，6 求值器 + 10 比较器 |
| T6 | 传播模式表驱动化 | propagate.py，3 种模式 |
| T7 | 节点角色表驱动化 | roles.py，5 种角色行为 |

**验收标准：**
- engine.py ≤ 800 行
- 核心循环函数 ≤ 150 行
- 所有现有测试全部通过（行为等价）
- 新增测试：水位线不变时零计算
- 新增测试：执行顺序用户可配置

### 阶段二：外围剥离（1周）

| 任务 | 内容 |
|------|------|
| T8 | 后处理独立成模块（pk/analysis/dashboard/alerts） |
| T9 | 事件/信号生成独立成模块（events.py） |
| T10 | K线合成器独立（kline_synthesizer.py） |
| T11 | 数据源适配器统一接口 |

### 阶段三：前端对齐（1周）

| 任务 | 内容 |
|------|------|
| T12 | 综合设置表格与 CompiledPool 对齐 |
| T13 | 执行顺序可视化与交互优化 |
| T14 | 三模式切换界面清理 |

### 阶段四：优化验证（1周）

| 任务 | 内容 |
|------|------|
| T15 | 性能基准测试（对比重构前后） |
| T16 | 全量回归测试 |
| T17 | 代码审查 + 文档完善 |

---

## 十三、验收标准

### 13.1 代码质量

- [ ] engine.py ≤ 800 行（当前 3504 行，减少 75%+）
- [ ] 核心循环函数 ≤ 150 行，逻辑清晰可读
- [ ] 核心配置表 ≤ 8 张（运行时引擎直接读的）
- [ ] 没有硬编码的节点类型/边类型/模式判断
- [ ] 所有分支逻辑都由配置表驱动

### 13.2 功能正确性

- [ ] 所有现有单元测试全部通过
- [ ] 所有现有集成测试全部通过
- [ ] 行为等价性测试：新引擎 vs 旧引擎，同样输入输出一致
- [ ] 实盘/回放/仿真三模式全部正常工作

### 13.3 性能提升

- [ ] 水位线不变时，计算开销为 0（不进入核心循环）
- [ ] 单条边触发时，只计算该边及其下游，不是全量
- [ ] 典型场景下性能提升 ≥ 5x（全量计算的场景）

### 13.4 架构清晰度

- [ ] 运行前/运行时严格分离
- [ ] 数据层/计算层/事件层严格分离
- [ ] 执行顺序与拓扑明确区分
- [ ] 每个功能都能找到"读什么表 → 算什么 → 写什么表"的对应

---

## 十四、关键洞见总结

1. **最新 tick 时间是水位线**：水位线不涨，所有计算结果都不变。这是最核心的性能优化支点。

2. **拓扑是拓扑，执行顺序是执行顺序**：拓扑是连接关系，执行顺序是用户指定的处理次序。两个东西不能混为一谈。

3. **运行前能算完的，运行时就别算了**：拓扑序、边端点、过滤条件、时间规则——全部在加载时编译好，运行时只读。

4. **脏标记传播是事件驱动的本质**：不是每 tick 都遍历所有边，而是从脏节点出发，沿边传播，只处理该处理的。

5. **表驱动不是把 if 搬进 JSON**：是找到正交维度，用组合来表达多样性。8 + 3 = 24，6 + 10 = 60——这才是表驱动的威力。

6. **K线更新和股票池计算是两条独立的线**：都消费 latest_tick，但互不依赖。分开了，各自都简单。

7. **三模式本质统一**：差异只在时间源、数据源、交易接口三个插件。核心引擎一套代码通吃。
