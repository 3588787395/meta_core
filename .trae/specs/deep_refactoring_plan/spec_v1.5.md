# 股票池深度重构规划 v1.5

> 版本主题：指标缓存分级 + filter四层定型 + 概念瘦身
> 设计原则：表驱动、数据驱动、事件驱动、增量优先、全量保底、周期分级、概念最简
> 目标：engine.py 从 3504 行 → ≤ 800 行，配置表从 50+ 张 → ≤ 12 张核心表，概念数量压到最少

---

## v1.4 → v1.5 变更摘要：指标缓存分级 + filter四层定型 + 概念瘦身

**变更日期：** 2026-07-01

**核心升级（四大改进方向）：**

| # | 变更项 | v1.4 | v1.5 | 本质变化 |
|---|--------|------|------|---------|
| 1 | **指标缓存粒度（核心改进1）** | 股票级缓存（公式×股票→值） | **周期分级缓存**（公式×周期×股票→值），编译期已知指标周期 | 从"一股一缓存"到"一周期一缓存"，日线指标一天只算一次 |
| 2 | **filter 结构（核心改进2）** | 三层：指标计算→比较判断→结果汇总 | **四层定型**：指标层→比较层→组合层→传播层 | "结果汇总"改名为"组合层"，职责更清晰；传播层独立出来 |
| 3 | **概念瘦身（核心改进3）** | stock_dirty + stock_data_version 两套；edge_pending + dirty_nodes 两套 | **合并：stock_dirty 替代 stock_data_version；dirty_nodes 替代 edge_pending** | 概念从 4 个压到 2 个，能用一个表解决的就不用两个 |
| 4 | **编译期指标去重（核心改进4）** | 多条边各算各的指标 | **编译期合并相同指标，运行时只算一次** | 相同公式+参数+周期的指标，全系统只算一次，所有边共享 |
| 5 | **运行时核心表** | 9 张 | **8 张（-1）** | 合并冗余表，概念瘦身 |
| 6 | **编译期产物** | edge_indicator_spec（边级） | **新增全局 indicator_registry（指标级去重）** + edge_indicator_refs（边引用指标ID） | 指标从"边的属性"变为"全局资源"，边只是引用 |
| 7 | **数据周期感知** | 无（所有指标同频率检查） | **按周期分级：tick / 1min / 5min / 15min / 30min / 60min / day / week / month** | 不同周期指标脏标记频率不同，低频指标大幅减少计算 |
| 8 | **周期脏标记** | 股票级一个 dirty 标志 | **周期级 dirty 标志**（每只股票每个周期独立脏标记） | 日线数据不更新，日线指标永远不脏 |

**一句话总结 v1.5 升级：** 指标缓存按数据周期分级（编译期已知周期，运行期按需检查），filter 四层结构定型（指标→比较→组合→传播），消除概念冗余（stock_dirty/stock_data_version二选一，edge_pending/dirty_nodes统一），编译期指标去重（相同指标全系统只算一次）。

---

## 一、本质认知：股票池到底是什么（v1.5 深化版）

### 1.1 一句话本质（更新）

**股票池 = 一组节点 × 一组边 × 全局周期水位线 × 周期级脏标记 × 全局指标注册表 × 一个事件队列。**

- **节点**：装股票的容器（备选池/状态池/条件池/目标池）
- **边**：节点之间的连接，带触发条件（时间 + 过滤条件）
- **全局周期水位线**：`period_ts[period]` —— 每个数据周期自己的最新时间戳。**某周期水位线不涨，该周期的所有指标结果都不变。**
- **周期级脏标记**：`stock_dirty[code][period]` —— 每只股票每个周期独立的脏标志。日线数据没更新，日线指标就不脏。
- **全局指标注册表**：`indicator_registry` —— 编译期收集所有边用到的指标，去重后统一管理。相同公式+参数+周期的指标，全系统只有一份。
- **脏节点队列**：`dirty_nodes` —— 需要重新计算的节点（替代 v1.4 的 edge_pending）。节点脏了 = 该节点的所有出边都需要检查。

### 1.2 核心洞察1：指标缓存的粒度应该是"周期级"，不是"股票级"

**这是 v1.5 最重要的认知升级。**

之前（v1.4）的理解：
- 一只股票一个 dirty 标志
- 数据更新了 → 整只股票的所有指标都脏了
- 不管是 tick 级指标还是日线指标，每秒都要检查一遍

现在（v1.5）的深化理解：

```
不同周期的数据，变化频率天差地别：

┌──────────┬─────────────┬──────────────────────────┐
│  周期    │  变化频率   │  一天变化次数（估算）     │
├──────────┼─────────────┼──────────────────────────┤
│ tick     │ 每秒多次    │  ~14400 次（4小时×3600）  │
│ 1分钟    │ 每分钟一次  │  ~240 次                 │
│ 5分钟    │ 每5分钟一次 │  ~48 次                  │
│ 日线     │ 每天一次    │  1 次                    │
│ 周线     │ 每周一次    │  0.2 次                  │
│ 月线     │ 每月一次    │  0.03 次                 │
└──────────┴─────────────┴──────────────────────────┘
```

**关键问题：** 日线指标一天才变一次，为什么要每秒都检查它脏不脏？

**答案：不需要。** 日线数据不更新，日线指标就永远不脏。

**所以缓存粒度要按周期分级：**

```
指标缓存不是：
  indicator_cache[formula_key][code] = value

而是：
  indicator_cache[formula_key][period][code] = value

或者更准确地说（编译期已知周期）：
  indicator_cache[indicator_id][code] = value
  （indicator_id 已经包含了周期信息）
```

**脏标记也要按周期分级：**

```
不是：
  stock_dirty[code] = True/False

而是：
  stock_dirty[code][period] = True/False
```

**性能提升有多大？**

假设一个股票池有 1000 只股票，用到 10 个指标，其中：
- 2 个 tick 级指标（每秒变化）
- 3 个 1 分钟指标（每分钟变化）
- 3 个 5 分钟指标（每5分钟变化）
- 2 个日线指标（每天变化）

v1.4（股票级脏标记）：每秒检查 1000 × 10 = 10000 次缓存有效性
v1.5（周期级脏标记）：
- tick 级：每秒检查 1000 × 2 = 2000 次
- 1分钟：每分钟检查 1000 × 3 = 3000 次（折算每秒 50 次）
- 5分钟：每5分钟检查 1000 × 3 = 3000 次（折算每秒 10 次）
- 日线：每天检查 1000 × 2 = 2000 次（折算每秒 0.01 次）

**折算下来每秒约 2060 次检查，相比 v1.4 的 10000 次，减少约 80%。**

而且这还是保守估计——实际中低频指标通常更多（基本面、日线技术指标等）。

### 1.3 核心洞察2：filter 是四层，不是三层

**v1.4 的三层结构：** 指标计算 → 比较判断 → 结果汇总

**问题：** "结果汇总"这个名字太含糊了。汇总什么？怎么汇总？AND/OR 算汇总吗？排名算汇总吗？propagate 算汇总吗？

**v1.5 四层定型：**

```
┌─────────────────────────────────────────────────────────┐
│  第四层：传播层（propagate）                              │
│  copy / move / overwrite                                 │
│  输入：通过组合层的股票集合                               │
│  输出：目标节点的新股票列表                               │
├─────────────────────────────────────────────────────────┤
│  第三层：组合层（combine）                                │
│  AND / OR / NOT / 排名 / 截面运算                        │
│  输入：多个比较层的结果                                   │
│  输出：最终通过的股票集合                                 │
├─────────────────────────────────────────────────────────┤
│  第二层：比较层（compare）                                │
│  > / < / = / 金叉 / 死叉 / 上拐 / 下拐                   │
│  输入：单只股票的指标值                                   │
│  输出：单只股票的 bool 结果（独立型）                     │
├─────────────────────────────────────────────────────────┤
│  第一层：指标层（indicator）                              │
│  MA / MACD / RSI / 涨幅 / 成交量 / 财务指标              │
│  纯函数：输入=数据，输出=指标值                           │
│  可缓存 / 可共享 / 周期分级                               │
└─────────────────────────────────────────────────────────┘
```

**四层职责清晰划分：**

| 层级 | 名称 | 职责 | 可缓存性 | 可增量性 | 可共享性 |
|------|------|------|---------|---------|---------|
| **第一层** | 指标层（indicator） | 计算指标值（纯函数） | ✅ 完全可缓存 | ✅ 股票级独立 | ✅ 跨边/跨filter全局共享 |
| **第二层** | 比较层（compare） | 单股独立比较（>/</=/金叉/...） | ⚠️ 独立型可缓存 | ✅ 股票级增量 | ⚠️ 同规格可共享 |
| **第三层** | 组合层（combine） | 集合运算（AND/OR/NOT/排名/截面） | ❌ 动态的 | ⚠️ 排名需全量，逻辑运算可增量 | ❌ — |
| **第四层** | 传播层（propagate） | 状态传播（copy/move/overwrite） | ❌ — | ✅ 增量传播 | ❌ — |

**为什么传播层要独立出来？**
- 传播是"状态变化"，不是"计算"
- 传播有副作用（修改节点状态、触发事件、触发下游边）
- 传播模式（copy/move/overwrite）是独立的维度，和 filter 逻辑无关
- 单独拿出来，概念更清晰

### 1.4 核心洞察3：概念越少越好，能用一个表解决的就不用两个

**v1.4 存在的概念冗余：**

#### 冗余1：stock_dirty 和 stock_data_version

v1.4 里有两个东西：
- `stock_dirty[code]` —— 写时置脏的标志位
- `stock_data_version[code]` —— 时间戳对比的版本号

**这两个功能完全重复。** 它们回答的是同一个问题："这只股票的数据有没有变？"

v1.5 决定：**保留 stock_dirty，去掉 stock_data_version。**

理由：
1. 写时置脏更直观
2. 性能略好（省去时间戳比较）
3. 周期级脏标记用 stock_dirty[code][period] 更自然
4. 少一个概念，少一张表

#### 冗余2：edge_pending 和 dirty_nodes

v1.4 里有两个东西：
- `edge_pending` —— 待处理的边队列
- `dirty_nodes` —— 脏节点集合

**这两个功能重叠。** 节点脏了 = 该节点的所有出边都需要检查。

v1.5 决定：**保留 dirty_nodes，去掉 edge_pending。**

理由：
1. 节点数量远少于边数量（一个节点可能有多条出边）
2. 节点脏了，所有出边都要检查，用节点级更自然
3. 执行顺序还是按边的执行顺序来，但触发源是节点
4. 少一个概念，少一张表

**新的触发模型：**

```
数据更新 → 股票所在节点标记为 dirty → 加入 dirty_nodes 集合
         ↓
处理 dirty_nodes 时，按边的执行顺序遍历节点的出边
         ↓
每条边检查三要素，满足就执行
         ↓
边执行后，如果目标节点状态变化，目标节点标记为 dirty
```

### 1.5 核心洞察4：编译期就能知道每个指标用什么周期

**为什么重要？**

因为如果编译期就能知道指标周期，那么：
1. 缓存 key 可以预计算（不用运行时拼）
2. 脏标记可以按周期精确触发（不会错标）
3. 指标去重可以在编译期完成（相同周期+相同公式=同一个指标）

**怎么知道？**

从代码里就能看出来：
- `evaluators.py` 里的 `_nperiod_to_period()` 函数（第 385-394 行）
- `formula_engine.py` 里的 `eval_batch()` 的 `period` 参数（第 624 行）
- TDX 公式配置里的 `nperiod` 字段

**编译期做什么？**

1. 解析每条边的 filter 配置
2. 提取每个指标的公式、参数、周期
3. 全局去重：相同（公式 + 参数 + 周期）的指标合并为一个
4. 分配唯一的 indicator_id
5. 边只保存 indicator_id 引用，不保存完整指标定义

**运行时做什么？**

1. 数据更新时，只标记对应周期的 dirty
2. 计算指标时，只计算对应周期 dirty 的股票
3. 所有边共享同一份指标缓存

### 1.6 运行时只有五件事（v1.5 更新版）

| # | 事情 | 触发条件 | 做什么 |
|---|------|---------|-------|
| 1 | **数据更新（周期级）** | 外部行情推送 / K线到达 | 写 `latest_tick` 表，更新对应周期的 `period_ts`，**按周期标记 stock_dirty[code][period]**，股票所在节点加入 `dirty_nodes` |
| 2 | **TTL 过期处理** | tick 开始时检查过期队列 | 从 `ttl_expiry_queue` 弹出已过期股票，从节点移除，目标节点加入 `dirty_nodes` |
| 3 | **节点脏处理（事件驱动）** | 从 `dirty_nodes` 取节点 | 按执行顺序遍历节点出边 → 检查三要素 → 满足则执行 filter 四层 → propagate → 发事件 → 目标节点变化则加入 `dirty_nodes` |
| 4 | **无条件边立即传播**（同步） | 源节点股票状态变化时立即触发 | 直接 propagate，不走 gate，不走 filter，立即更新目标节点，立即发事件 |
| 5 | **指标缓存维护（周期级）** | 数据更新时 / 指标计算后 | 按周期标记脏 / 计算完后写入缓存并清脏 / 过期自动淘汰 |

**正确的因果链（v1.5 深化版）：**

```
数据更新（某周期的数据来了）
  ↓
该周期的水位线涨了（period_ts[period]++）
  ↓
更新了数据的股票，该周期标记为脏（stock_dirty[code][period] = True）
  ↓
股票所在节点标记为脏（加入 dirty_nodes）
  ↓
处理 dirty_nodes 时，遍历节点出边
  ↓
边执行：
  第一层（指标层）：按周期检查脏，只重算脏周期的指标
  第二层（比较层）：独立型增量，排名型全量
  第三层（组合层）：AND/OR/NOT/排名
  第四层（传播层）：copy/move/overwrite
  ↓
目标节点状态变化 → 目标节点加入 dirty_nodes → 触发下游
```

---

## 二、核心设计：周期分级缓存 + filter四层定型 + 概念瘦身 + 编译期去重

### 2.1 运行时内存表（核心运行时表一共 8 张，v1.5 瘦身版）

| 表名 | 类型 | 读时机 | 写时机 | 说明 |
|------|------|--------|--------|------|
| `latest_tick` | Dict[code → bar_dict] | 指标计算时读 | 行情推送时写 | **唯一真相源**。所有股票的最新tick数据 |
| `period_ts` | Dict[period → float] | 周期水位线比较时读 | 对应周期数据推送时写 | **各周期独立水位线（秒级）**。某周期水位线不涨，该周期指标都不变 |
| `stock_dirty` | Dict[code → Dict[period → bool]] | 增量计算时读 | 该股票对应周期数据推送时写 | **周期级脏标记**。每只股票每个周期独立的脏标志 |
| `indicator_cache` | Dict[indicator_id → Dict[code → value]] | 指标计算前先查 | 指标计算完后写 | **全局指标结果缓存**。指标ID×股票代码 → 指标值。编译期去重，运行时共享 |
| `node_stocks` | Dict[nid → List[code]] | propagate 读写、filter 读 | 边执行/TTL过期后写 | 各节点当前股票列表 |
| `stock_state_version` | Dict[nid → Dict[code → float]] | 状态变化判断时读 | 股票入池/出池时写 | 每只股票在每个节点的状态版本 |
| `dirty_nodes` | Set[nid]（按执行顺序处理） | 执行循环读 | 节点股票状态变化时加入 / 数据更新时加入 | **脏节点集合**（替代 v1.4 的 edge_pending）。节点脏了=所有出边需要检查 |
| `edge_filter_cache` | Dict[eid → Dict[code → bool]] | 增量比较判断时读 | filter 比较后写 | 比较层结果缓存。每只股票在每条边的上次比较结果（独立型用） |
| `ttl_expiry_queue` | Heap[(expire_ts, nid, code)] | tick 开始时弹出 | 股票入池时插入 | TTL 过期队列。按过期时间排序的最小堆 |

**就这八张核心运行时表。** 其他的都是运行前编译产物或配置表。

**v1.5 相比 v1.4 的变化：**

| v1.4（9张） | v1.5（8张） | 变化 |
|-------------|-------------|------|
| `latest_tick` | `latest_tick` | 不变 |
| `latest_tick_ts` | `period_ts` | **升级为多周期**，从单一水位线变为各周期独立水位线 |
| `stock_data_version` | — | **删除**，功能由 `stock_dirty` 承接 |
| `stock_dirty` | `stock_dirty` | **升级为周期级**，从股票级变为周期级 |
| `indicator_cache` | `indicator_cache` | **key 变了**，从 formula_key 变为 indicator_id（编译期去重后的全局ID） |
| `node_stocks` | `node_stocks` | 不变 |
| `stock_state_version` | `stock_state_version` | 不变 |
| `edge_pending` | — | **删除**，功能由 `dirty_nodes` 承接 |
| `dirty_nodes` | `dirty_nodes` | **升级为主队列**，从辅助集合变为主触发源 |
| `edge_filter_cache` | `edge_filter_cache` | 不变 |
| `edge_last_data_ts` | `edge_last_data_ts` | 不变（注意：这张表存在，但不算"核心8张"里的独立表，属于边执行元数据） |
| `ttl_expiry_queue` | `ttl_expiry_queue` | 不变 |

**indicator_cache 的详细结构（v1.5 版）：**

```python
indicator_cache = {
    # key = 编译期分配的全局唯一 indicator_id（已去重）
    "ind_001": {
        # 这个指标是什么？编译期已知：
        #   formula: "MA"
        #   period: "1d"
        #   args: {"N": 5}
        "600519": 1800.5,           # 标量值（单输出）
        "000001": 12.5,
        # ...
    },
    "ind_002": {
        # formula: "MACD"
        # period: "1d"
        # args: {"SHORT": 12, "LONG": 26, "MID": 9}
        "600519": {                 # 多输出
            "DIF": 1.2,
            "DEA": 0.8,
            "MACD": 0.4,
        },
        # ...
    },
    # ...
}
```

**stock_dirty 的详细结构（v1.5 周期级）：**

```python
stock_dirty = {
    "600519": {
        "tick": True,    # tick 数据更新了，tick级指标脏了
        "1m": False,     # 1分钟数据没更新，1分钟指标不脏
        "5m": False,
        "15m": False,
        "30m": False,
        "60m": False,
        "1d": False,     # 日线数据没更新，日线指标不脏
        "1wk": False,
        "1mon": False,
    },
    "000001": {
        "tick": True,
        "1d": True,      # 这只股票日线数据也更新了（比如刚收盘）
        # ...
    },
    # ...
}
```

**period_ts 的详细结构：**

```python
period_ts = {
    "tick": 1234567890.0,   # tick 级最新时间戳
    "1m": 1234567800.0,     # 1分钟线最新时间戳
    "5m": 1234567500.0,     # 5分钟线最新时间戳
    "15m": 1234566000.0,
    "30m": 1234563000.0,
    "60m": 1234560000.0,
    "1d": 1234500000.0,     # 日线最新时间戳（今天开盘就没变过）
    "1wk": 1234000000.0,
    "1mon": 1230000000.0,
}
```

### 2.2 编译期：全局指标去重 + 边引用指标

**编译期做的事：**

1. 遍历所有边的 filter 配置
2. 提取每个指标的（公式 + 参数 + 周期）三元组
3. 全局去重：相同三元组合并为一个指标
4. 分配全局唯一的 indicator_id
5. 边只保存 indicator_id 列表（引用），不保存完整指标定义

```
编译前（边里嵌指标定义）：
  edge1: 指标=[MA(5, 1d), MACD(12,26,9, 1d)]
  edge2: 指标=[MA(5, 1d), RSI(14, 1d)]
  edge3: 指标=[MA(5, 1d), MACD(12,26,9, 1d)]

编译后（全局指标注册表 + 边引用）：
  indicator_registry:
    ind_001: MA(5, 1d)
    ind_002: MACD(12,26,9, 1d)
    ind_003: RSI(14, 1d)
  
  edge1: indicator_refs = [ind_001, ind_002]
  edge2: indicator_refs = [ind_001, ind_003]
  edge3: indicator_refs = [ind_001, ind_002]
```

**好处：**
- 相同指标全系统只算一次
- 缓存只有一份，不会重复存储
- 周期信息编译期已知，运行时直接用
- 指标管理更清晰

**编译期新增产物（v1.5）：**

| 编译产物 | 类型 | 说明 |
|----------|------|------|
| `indicator_registry` | Dict[indicator_id → indicator_spec] | **全局指标注册表**。所有去重后的指标，编译期一次性收集 |
| `edge_indicator_refs` | Dict[eid → List[indicator_id]] | 每条边引用的指标ID列表（替代 v1.4 的 edge_indicator_spec） |
| `edge_compare_spec` | Dict[eid → compare_spec] | 比较层规格（怎么比、和谁比、排名规则等） |
| `edge_combine_spec` | Dict[eid → combine_spec] | **v1.5 新增：组合层规格**（AND/OR/NOT/排名逻辑） |
| `edge_filter_type` | Dict[eid → 'independent' / 'global'] | filter 类型：单股独立型 / 全局依赖型 |

**indicator_registry 的详细结构：**

```python
indicator_registry = {
    "ind_001": {
        "id": "ind_001",
        "type": "formula",          # formula / scalar_field / derived / financial
        "formula": "MA",            # 公式名或公式文本
        "period": "1d",             # 数据周期（编译期已知！）
        "args": {"N": 5},           # 公式参数
        "cache_key": "MA_5_1d",     # 缓存 key（编译期预计算）
        "output_type": "scalar",    # scalar / multi / series
    },
    "ind_002": {
        "id": "ind_002",
        "type": "formula",
        "formula": "MACD",
        "period": "1d",
        "args": {"SHORT": 12, "LONG": 26, "MID": 9},
        "cache_key": "MACD_12_26_9_1d",
        "output_type": "multi",     # 多输出（DIF/DEA/MACD）
    },
    "ind_003": {
        "id": "ind_003",
        "type": "scalar_field",
        "field": "close",
        "period": "tick",           # tick 级数据
        "cache_key": "close_tick",
        "output_type": "scalar",
    },
    # ...
}
```

### 2.3 Filter 四层结构详解（v1.5 定型版）

#### 第一层：指标层（Indicator Layer）

**性质：纯函数，完全可缓存，股票级独立，全局共享，周期分级。**

**输入：**
- 某只股票的对应周期数据（从 latest_tick / kline_cache 读）
- 公式参数（编译期已知）

**输出：**
- 标量值（单输出，如 MA、收盘价、涨幅）
- dict 多输出（如 MACD 的 DIF/DEA/MACD，KDJ 的 K/D/J）
- list/tuple 时间序列（如果需要历史值做比较判断）

**周期分级：**
- 每个指标有明确的周期（编译期已知）
- 只在对应周期数据更新时才可能脏
- 低频指标（日线、周线）很少重算

**全局共享：**
- 相同（公式 + 参数 + 周期）的指标，全系统只有一份缓存
- 多条边用同一个指标，都读同一份缓存
- 编译期去重，运行时零成本共享

**代码中的对应实现：**
- `formula_engine.py` 的 `PythonFormulaEngine.eval()` —— 输入 bars，输出指标值
- `evaluators.py` 的 `eval_formula_nset()` 里调用 `formula_router.eval_batch()`

#### 第二层：比较层（Comparison Layer）

**性质：逻辑运算，独立型可缓存可增量，排名型需全量。**

**输入：**
- 指标值（从 indicator_cache 读）
- 比较规则（>/</=/金叉/死叉/...）
- 阈值/参数

**输出：**
- bool（通过/不通过，独立型）

**分类：**

| 比较类型 | 性质 | 可增量 | 例子 |
|---------|------|--------|------|
| **独立比较** | 单股独立，只和自己比 | ✅ 股票级增量 | 价格>10、涨幅>5%、MACD>0、成交量>1000万 |
| **穿越比较** | 需要历史值，但是单股独立 | ✅ 股票级增量（需要指标的时间序列） | 金叉、死叉、上拐、下拐 |

**注意：排名不在这一层，排名在第三层组合层。**

**代码中的对应实现：**
- `evaluators.py` 的 `_scalar_compare()` —— 标量比较
- `evaluators.py` 的 `_apply_noperate()` —— 带向量的比较（金叉/死叉等）

#### 第三层：组合层（Combine Layer）

**性质：集合运算，动态的，不缓存。**

**输入：**
- 多个比较层的结果（bool 集合）
- 组合规则（AND/OR/NOT/排名/截面）

**输出：**
- 最终通过 filter 的股票集合

**为什么排名在这一层？**
- 排名是截面运算，依赖所有股票的指标值
- 排名不是"比较"（单股独立），而是"组合"（多股相对）
- 放在组合层，概念更清晰

**组合层的子类型：**

| 子类型 | 说明 | 可增量 |
|--------|------|--------|
| 逻辑运算 | AND / OR / NOT | ⚠️ 部分可增量（短路优化） |
| 排名运算 | 排名前N / 排名后N | ❌ 需全量重排 |
| 集合运算 | 交集 / 并集 / 差集（nset=5） | ⚠️ 取决于子条件 |

**代码中的对应实现：**
- `evaluators.py` 的 `_resolve_rank()` —— 排名处理
- `evaluators.py` 的 `eval_nset5_set_operation()` —— 集合运算

#### 第四层：传播层（Propagate Layer）

**性质：状态变更，有副作用，增量传播。**

**输入：**
- 通过组合层的股票集合
- 传播模式（copy/move/overwrite）
- 源节点 + 目标节点

**输出：**
- 目标节点的新股票列表
- 入池/出池事件
- 目标节点脏标记（触发下游）

**传播模式：**

| 模式 | 操作 | 影响源节点 |
|------|------|-----------|
| copy | target += passed | 否 |
| move | target += passed; source -= passed | 是 |
| overwrite | target = passed | 否 |

**为什么传播层要独立？**
- 传播是"状态变化"，不是"计算"
- 传播有副作用（发事件、触发下游）
- 传播模式是独立的维度，和 filter 逻辑无关
- 四层结构比三层更清晰，每层职责单一

### 2.4 脏标记的周期分级实现

#### 数据更新时的脏标记（周期级）

```python
# 数据更新时
def on_tick(code, bar, period="tick"):
    ts = floor(bar['datetime'])
    latest_tick[code] = bar
    
    # 更新对应周期的水位线
    if ts > period_ts[period]:
        period_ts[period] = ts
    
    # 只标记对应周期为脏
    if code not in stock_dirty:
        stock_dirty[code] = {}
    stock_dirty[code][period] = True
    
    # 股票所在节点标记为脏
    for nid in code_nodes[code]:
        dirty_nodes.add(nid)
        # （无条件边立即处理，条件边等主循环处理）
```

**关键：只有对应周期的数据更新了，才标记该周期为脏。**
- tick 数据更新 → 只标记 tick 周期脏
- 1分钟线合成完成 → 只标记 1m 周期脏
- 日线收盘 → 只标记 1d 周期脏

#### 指标计算时的脏检查（周期级）

```python
# 取指标时
def get_indicator(indicator_id, code):
    spec = indicator_registry[indicator_id]
    period = spec["period"]  # 编译期已知周期
    
    cache = indicator_cache.get(indicator_id, {})
    entry = cache.get(code)
    
    # 只检查对应周期的脏标记
    is_dirty = stock_dirty.get(code, {}).get(period, False)
    
    if entry is not None and not is_dirty:
        # 缓存有效，直接返回
        return entry["value"]
    else:
        # 需要重算
        value = compute_indicator(indicator_id, code)
        cache[code] = {
            'value': value,
        }
        # 注意：不清 stock_dirty[code][period]
        # 因为清脏是批量的，不是逐个指标清的
        return value

# 批量计算完一批指标后，统一清脏
def clear_period_dirty(codes, period):
    for code in codes:
        if code in stock_dirty:
            stock_dirty[code][period] = False
```

**注意：脏标记是周期级的，不是指标级的。**
- 一个周期脏了，该周期的所有指标都可能脏
- 批量计算完该周期的所有指标后，统一清脏
- 简单，够用，性能好

### 2.5 边触发机制：节点脏驱动 + 三要素 AND

**v1.5 改为节点脏驱动（替代 v1.4 的边队列驱动）：**

```
节点脏 = 该节点的所有条件出边都需要检查

处理 dirty_nodes 时：
  按边的执行顺序遍历所有节点的所有出边
  每条边检查三要素
  满足就执行
  执行后目标节点变化则标记目标节点脏
```

**三要素不变（v1.3 已确定，v1.5 沿用）：**

```
边执行 = 时间条件满足 
       AND 源节点有变化（股票增减 OR 数据版本更新）
       AND 数据版本 > 边上次处理版本
```

**v1.5 补充：边执行时的四层 filter 流程**

```
边执行开始：
  ↓
收集这条边引用的所有指标ID（从 edge_indicator_refs 读）
  ↓
=== 第一层：指标计算（周期级脏检查 + 全局缓存共享） ===
对每个 indicator_id:
  spec = indicator_registry[indicator_id]
  period = spec.period  # 编译期已知
  source_codes = node_stocks[sid]
  
  # 找出需要重算的股票（该周期脏 + 还没算过的）
  need_compute = [code for code in source_codes
                  if stock_dirty.get(code, {}).get(period, False)
                     or code not in indicator_cache[indicator_id]]
  
  if need_compute:
    # 批量计算
    values = compute_indicator_batch(indicator_id, need_compute)
    # 写入缓存
    for code, val in zip(need_compute, values):
      indicator_cache[indicator_id][code] = {'value': val}
    # 清该周期的脏标记（批量清）
    clear_period_dirty(need_compute, period)

所有指标值都准备好了（缓存的 + 新算的）
  ↓
=== 第二层：比较判断（独立型增量，穿越型增量） ===
filter_type = edge_filter_type[eid]
compare_spec = edge_compare_spec[eid]

如果是独立比较型：
  → 增量比较：只重算数据变了的股票，其他读 edge_filter_cache
  → 汇总得到 per_stock_result[code] = bool
  ↓
=== 第三层：组合运算（AND/OR/NOT/排名） ===
combine_spec = edge_combine_spec[eid]

如果是排名型：
  → 全量读指标值（但指标值是缓存的，不用重算）
  → 全量排名
  → 得到 passed_codes
如果是逻辑组合型：
  → 组合多个比较结果
  → 得到 passed_codes
  ↓
=== 第四层：传播（copy/move/overwrite） ===
propagate_spec = edge_propagate_spec[eid]
→ 更新目标节点 node_stocks[tid]
→ 对比得到 entered_codes / exited_codes
  ↓
如果有变化：
  → 更新 stock_state_version[tid][code]
  → 目标节点 tid 加入 dirty_nodes
  → 处理无条件边：立即同步 propagate
  → 立即发射入池/出池事件
  ↓
更新 edge_last_data_ts[eid] = period_ts["tick"]  # 用tick周期作为全局时间基准
```

**性能提升路径（v1.4 → v1.5）：**

| 场景 | v1.4（股票级脏标记） | v1.5（周期级脏标记 + 全局去重） | 提升 |
|------|---------------------|-------------------------------|------|
| 单条独立型 filter | 增量重算指标+比较 | 按周期检查脏，只重算脏周期的指标 | 取决于指标周期分布，低频指标提升大 |
| 多条边共用同一指标 | 每条边各算一遍 | 编译期去重，全局只算一次 | 大幅提升（n 条边省 n-1 次） |
| 排名型 filter | 指标读缓存，只重算排名 | 同 v1.4，但指标是全局共享的 | 持平（但指标计算量更少了） |
| 日线指标为主的策略 | 每秒都检查一遍 | 一天只检查一次 | 巨大提升（几百到几千倍） |

---

## 三、运行前 vs 运行时：严格分离（v1.5 更新版）

### 3.1 运行前（设计时 + 加载时）做的事

全部一次性做完，运行时只读。

| 阶段 | 做什么 | 产出 |
|------|--------|------|
| **设计时** | 用户拖拽节点、连线、配置参数、设置执行顺序 | pool_config (JSON) |
| **加载时** | 解析 pool_config，**全局收集指标并去重**，拆分 filter 为四层，判断 filter 类型，编译所有规格 | CompiledPool |

**CompiledPool v1.5 更新版：**

```python
CompiledPool = {
    # 节点
    'nodes': {nid: node_dict},
    'node_type': {nid: type_name},
    
    # 边
    'edges': {eid: edge_dict},
    'edge_endpoints': {eid: (sid, tid)},
    'edge_order': [eid1, eid2, ...],     # 综合设置行顺序 = 执行顺序
    'edge_type': {eid: 'conditional' | 'unconditional'},
    
    # ===== v1.5 核心变更：全局指标注册表 + 边引用 =====
    'indicator_registry': {              # **全局指标注册表（编译期去重）**
        indicator_id: {
            'type': 'formula' / 'scalar_field' / 'derived' / 'financial',
            'formula': 'MA',             # 公式名或文本
            'period': '1d',              # 数据周期（编译期已知！）
            'args': {...},               # 参数
            'cache_key': 'MA_5_1d',      # 缓存 key（预计算）
            'output_type': 'scalar',     # scalar / multi / series
        },
        ...
    },
    
    # ===== filter 四层规格 =====
    'edge_indicator_refs': {             # 第一层：指标引用（ID列表，不是完整定义）
        eid: [ind_id1, ind_id2, ...]
    },
    'edge_compare_spec': {               # 第二层：比较规格
        eid: {type: 'scalar_compare' / 'cross' / ..., ...}
    },
    'edge_combine_spec': {               # 第三层：组合规格（v1.5 新增）
        eid: {type: 'logic' / 'rank' / 'set_op', ...}
    },
    'edge_filter_type': {                # filter 类型
        eid: 'independent' | 'global'
    },
    
    # 其他规格
    'edge_timing_spec': {eid: spec},
    'edge_propagate_spec': {eid: spec},  # 第四层：传播规格
    'edge_ttl_spec': {eid: spec},
    
    # 邻接表
    'out_edges': {nid: [eid, ...]},
    'in_edges': {nid: [eid, ...]},
    
    # 源节点列表
    'source_nodes': [nid, ...],
    
    # 角色映射
    'node_role': {nid: 'candidate' | 'state' | 'condition' | 'target' | 'discard'},
}
```

**v1.5 相比 v1.4 的编译期变化：**

| v1.4 | v1.5 | 变化原因 |
|------|------|---------|
| `edge_indicator_spec[eid]`（边里嵌完整指标定义） | `indicator_registry`（全局） + `edge_indicator_refs[eid]`（边引用ID） | 编译期去重，全局共享 |
| 无 `edge_combine_spec` | 新增 `edge_combine_spec[eid]` | filter 四层定型，组合层独立出来 |
| "结果汇总层"概念模糊 | 明确为"组合层" + "传播层" | 概念清晰，职责单一 |

### 3.2 运行时做的事（事件驱动 + 四层 filter + 周期级缓存 + 节点脏驱动）

就一个循环（**节点脏驱动 + 流式串行执行 + 周期级指标缓存 + 增量/全量混合策略**）：

```
1. 等数据更新（或时间步进）
2. 新数据来了吗？
   → 没来：去步骤 3
   → 来了：
       period = 数据的周期（tick / 1m / 5m / ... / 1d）
       latest_tick[code] = new_bar
       ts = floor(new_bar['datetime'])
       
       # 更新对应周期的水位线
       if ts > period_ts[period]:
           period_ts[period] = ts
       
       # 只标记对应周期为脏（v1.5 核心：周期级脏标记）
       if code not in stock_dirty:
           stock_dirty[code] = {}
       stock_dirty[code][period] = True
       
       # 股票所在节点标记为脏（v1.5：节点脏驱动，替代 edge_pending）
       for nid in code_nodes[code]:
           dirty_nodes.add(nid)
3. 处理 TTL 过期：
   从 ttl_expiry_queue 弹出所有 expire_ts <= now 的股票
   → 从对应节点 node_stocks[nid] 移除
   → 更新 stock_state_version[nid][code]
   → 节点 nid 加入 dirty_nodes
4. 处理 dirty_nodes（按边的执行顺序）：
   while dirty_nodes 不为空：
      取出一个节点 nid（按某种顺序，比如拓扑序或执行顺序）
      
      遍历 nid 的所有条件出边，按 edge_order 排序：
         eid = 当前边
         sid, tid = edge_endpoints[eid]
         
         a. 检查三要素：
            ① 时间触发条件满足吗？
               → 不满足：跳过
            ② 数据版本更新了吗？
               period_ts["tick"] > edge_last_data_ts[eid] 吗？
               → 不大于：数据没变，跳过
            ③ 源节点有变化吗？
               （能进 dirty_nodes 说明有变化，继续）
         
         b. === 第一层：指标计算（周期级脏检查 + 全局缓存） ===
            indicator_ids = edge_indicator_refs[eid]
            source_codes = node_stocks[sid]
            
            对每个 ind_id in indicator_ids:
                spec = indicator_registry[ind_id]
                period = spec["period"]
                
                # 找出需要重算的股票（该周期脏 OR 还没算过）
                need_compute = [code for code in source_codes
                                if stock_dirty.get(code, {}).get(period, False)
                                   or code not in indicator_cache[ind_id]]
                
                if need_compute:
                    # 批量计算
                    values = compute_indicator_batch(ind_id, need_compute)
                    # 写入全局缓存
                    for code, val in zip(need_compute, values):
                        indicator_cache[ind_id][code] = {'value': val}
                    # 清该周期的脏标记
                    for code in need_compute:
                        stock_dirty[code][period] = False
            
            # 此时，所有指标值都准备好了（全局缓存的 + 新算的）
            # 其他边如果也用这些指标，直接读缓存就行
         
         c. === 第二层：比较判断（独立型增量） ===
            compare_spec = edge_compare_spec[eid]
            
            如果是独立比较型（scalar_compare / cross）：
               → 走增量比较
                  dirty_codes = [code for code in source_codes
                                 if code not in edge_filter_cache.get(eid, {})
                                    or 数据有变化]  # 用节点状态变化判断
                  
                  need_compare = dirty_codes + new_codes
                  
                  if need_compare:
                      for code in need_compare:
                          从 indicator_cache 读指标值
                          用 compare_spec 比较
                          结果写入 edge_filter_cache[eid][code]
                  
                  # 汇总通过的
                  passed_codes = [code for code in source_codes
                                  if edge_filter_cache[eid].get(code, False)]
         
         d. === 第三层：组合运算（AND/OR/NOT/排名） ===
            combine_spec = edge_combine_spec[eid]
            
            如果是排名型（rank）：
               → 全量读指标值（从 indicator_cache，都是最新的）
               → 全量排名
               → passed_codes = 排名结果
            
            如果是逻辑组合型（logic）：
               → 组合多个比较结果
               → passed_codes = 组合结果
            
            如果是集合运算（set_op）：
               → 多个源集合的交并差
               → passed_codes = 运算结果
         
         e. === 第四层：传播（copy/move/overwrite） ===
            propagate_spec = edge_propagate_spec[eid]
            → 应用传播模式到 node_stocks[tid]
            → 对比得到 entered_codes 和 exited_codes
         
         f. 如果有变化：
              - 更新 stock_state_version[tid][code]
              - tid 加入 dirty_nodes
              - 处理无条件边：立即同步 propagate
              - 立即发射入池/出池事件
         
         g. 更新 edge_last_data_ts[eid] = period_ts["tick"]
      
      # 处理完这个节点的所有边后，从 dirty_nodes 移除
      dirty_nodes.discard(nid)
5. 回去等下一次数据更新
```

**v1.5 核心循环的关键变化（相对于 v1.4）：**

1. **周期级脏标记**：stock_dirty 从股票级变为周期级，低频指标大幅减少计算
2. **节点脏驱动**：用 dirty_nodes 替代 edge_pending，概念更少
3. **全局指标去重**：indicator_registry 全局管理，边只引用ID，相同指标只算一次
4. **filter 四层定型**：指标层→比较层→组合层→传播层，每层职责清晰
5. **组合层独立**：从"结果汇总"改为"组合层"，排名、集合运算都在这里
6. **传播层独立**：状态传播从计算中剥离，概念更清晰

---

## 四、功能-表操作对应表（v1.5 更新版）

### 4.1 数据层（最新tick表 + 周期级脏标记 + 全局指标缓存）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| 行情推送（周期级） | — | `latest_tick[code] = new_bar` + `stock_dirty[code][period] = True` + `period_ts[period]` 更新 | **v1.5 核心：周期级脏标记**，只标记对应周期；秒级精度 |
| 周期水位线更新 | `new_bar['datetime']` | `period_ts[period] = max(period_ts[period], ts)` | 每个周期独立水位线 |
| **数据更新触发节点脏** | `code_nodes[code]` | `dirty_nodes.add(nid)` | **v1.5：节点脏驱动**，替代 edge_pending |
| **全局指标缓存查询** | `indicator_cache[indicator_id][code]` + `stock_dirty[code][period]` | — | **v1.5 更新**：按周期检查脏，不脏就用缓存 |
| **全局指标缓存写入** | 计算结果 | `indicator_cache[indicator_id][code]` + `stock_dirty[code][period] = False` | **v1.5 更新**：全局共享缓存，算完清对应周期的脏 |
| **编译期指标去重** | 所有边的指标配置 | `indicator_registry` + `edge_indicator_refs` | **v1.5 新增**：相同（公式+参数+周期）合并为一个 |

**关键变化（v1.4 → v1.5）：**
- 脏标记从股票级升级为周期级（stock_dirty[code][period]）
- 单一水位线升级为多周期水位线（period_ts[period]）
- edge_pending 改为 dirty_nodes（节点脏驱动）
- 指标从边的属性变为全局资源（indicator_registry）
- 编译期指标去重，运行时全局共享

### 4.2 TTL 淘汰层（事件驱动，非轮询）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| 股票入池记录 TTL | `edge_ttl_spec[eid]` | `ttl_expiry_queue` 插入 `(expire_ts, nid, code)` | `expire_ts = now + ttl_sec` |
| TTL 过期检查 | `ttl_expiry_queue + now` | 弹出过期项 | 最小堆：堆顶过期就弹出，直到堆顶未过期 |
| 过期股票移除 | `node_stocks[nid]` | `node_stocks[nid]` + `stock_state_version[nid][code]` | 从节点移除过期股票，更新该股票的状态版本 |
| 过期触发级联 | `stock_state_version[nid][code]` | `dirty_nodes.add(nid)` + 无条件边立即执行 | **v1.5：节点脏驱动**，源节点变了 → 节点脏 → 出边都要检查 |

**（这部分 v1.4 已经很好，v1.5 只是把触发从 edge_pending 改为 dirty_nodes）**

### 4.3 边触发判定层（节点脏驱动 + 三要素 AND）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **数据更新 → 节点脏** | `code_nodes[code]` | `dirty_nodes.add(nid)` | 股票有新数据 → 所在节点标记为脏 |
| 节点股票状态变化 → 节点脏 | `out_edges[sid]` | `dirty_nodes.add(nid)` | 源节点变了 → 节点脏 → 出边都要检查 |
| 边时间触发检查 | `edge_timing_spec[eid] + period_ts["tick"] + _flow_state[eid]` | — | `starttype × cxtype` 的判定 |
| **三要素检查** | `period_ts["tick"]` + `edge_last_data_ts[eid]` | — | 时间条件 AND 数据版本更新 AND 源节点有变化 |
| **周期级指标缓存检查** | `indicator_cache[indicator_id]` + `stock_dirty[code][period]` | — | **v1.5 核心**：只检查对应周期的脏标记 |
| 增量筛选需要重算的股票（独立型比较） | `node_stocks[sid] + edge_filter_cache[eid]` | — | `need_compare = (code not in cache) OR (状态变化)` |
| 全量判断（全局依赖型比较） | `period_ts["tick"]` + `edge_last_data_ts[eid]` | — | `period_ts["tick"] > edge_last_data_ts` 就全量重算比较（但指标读缓存） |
| 边是否需要执行 | 三要素是否全部满足 | — | 三要素都满足就执行，否则跳过 |

**关键变化（v1.4 → v1.5）：**
- 触发源从 edge_pending 改为 dirty_nodes
- 脏标记从股票级改为周期级
- 指标缓存从边级改为全局共享
- 指标去重在编译期完成

### 4.4 边执行层（四层 filter + 混合策略 + propagate + 即时事件）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **filter 类型判断** | `edge_filter_type[eid]` | — | 先判断 independent / global，再选策略 |
| **第一层：指标计算（全局缓存 + 周期级脏）** | `indicator_registry` + `indicator_cache[ind_id]` + `stock_dirty[code][period]` + `latest_tick` + `edge_indicator_refs[eid]` | `indicator_cache[ind_id][code]` + `stock_dirty[code][period] = False` | **v1.5 核心**：全局去重、周期级脏检查、批量计算、批量清脏 |
| **第二层：比较判断（独立型增量）** | `indicator_cache` + `edge_compare_spec[eid]` + `edge_filter_cache[eid]` | `edge_filter_cache[eid][code]` | 只重算数据变了的股票的比较，其他读缓存 |
| **第三层：组合运算（AND/OR/NOT/排名）** | `indicator_cache` + `edge_combine_spec[eid]` + `node_stocks[sid]` | — | **v1.5 新增独立层**：排名、逻辑组合、集合运算 |
| **第四层：传播（copy/move/overwrite）** | `node_stocks[sid]` + `node_stocks[tid]` + `edge_propagate_spec[eid]` | `node_stocks[tid]` + `stock_state_version[tid][code]` | **v1.5 独立成层**：状态传播从计算中剥离 |
| **即时事件发射** | `node_stocks[tid]` 新旧对比 + `node_role[tid]` | `event_queue` + `signal_queue` | 差集计算 | **每条边执行后立即发射** |
| 无条件边立即传播 | `node_stocks[sid]` + `edge_propagate_spec[eid]` | `node_stocks[tid]` + `stock_state_version[tid][code]` | 直接 propagate，无 gate，无 filter |
| 边处理水位线更新 | `period_ts["tick"]` | `edge_last_data_ts[eid]` | `edge_last_data_ts[eid] = period_ts["tick"]` |

**关键变化（v1.4 → v1.5）：**
- filter 从三层变成四层：指标层→比较层→组合层→传播层
- "结果汇总层"拆分为"组合层"（计算）+ "传播层"（状态变更）
- 指标层全局共享，编译期去重
- 脏标记按周期分级

### 4.5 事件层（流式逐条产生）

| 功能 | 读什么表 | 写什么表 | 计算 | 时机 |
|------|---------|---------|------|------|
| 入池事件 | `node_stocks[tid]` 执行前后对比 | `event_queue` | 差集计算（新 - 旧） | 每条边执行后立即发射 |
| 出池事件 | `node_stocks[sid]` 执行前后对比 | `event_queue` | 差集计算（旧 - 新） | 每条边执行后立即发射 |
| 预警事件 | `alert_rules + node_stocks` 变化 | `alert_queue` | 规则匹配 | 每条边执行后立即检查 |
| 交易信号 | `node_role[tid] == 'target' + 入池/出池` | `signal_queue` | 角色判定 + 信号生成 | 每条边执行后立即生成 |

**（这部分 v1.3 已经很好，v1.4/v1.5 不变）**

### 4.6 后处理层（PK排名/分析角度/看盘面板）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| PK排名 | `node_stocks[target] + latest_tick + pk_config` | `_pk_rankings` | 按权重评分排序 |
| 分析角度 | `node_stocks[target] + latest_tick + analysis_config` | `_angle_results` | 多维度计算 |
| 看盘面板 | `node_stocks + latest_tick + dashboard_schema` | `_dashboard_data` | 组装显示数据 |

**注意：后处理也可以复用全局 indicator_cache！**
- PK 排名用到的指标，如果 indicator_registry 里已经有了，直接读
- 不用再算一遍
- 而且是全局共享的，其他边用的时候也能复用

---

## 五、表驱动：逻辑在表结构里，差异在表内容里（v1.5 更新版）

### 5.1 时间触发（timing.json）

**结构：** `{starttype: {rule, evaluator}, cxtype: {rule, evaluator}}`

**（这部分 v1.3 已经很好，v1.4/v1.5 不变）**

### 5.2 过滤条件（filter_specs.json）—— v1.5 四层定型版

**v1.5 更新：filter 规格明确为四层结构，指标全局注册，边引用ID。**

**全局指标注册表（indicator_registry）：**

```json
{
  "indicator_registry": {
    "ind_close_tick": {
      "type": "scalar_field",
      "field": "close",
      "period": "tick",
      "output_type": "scalar"
    },
    "ind_ma5_1d": {
      "type": "formula",
      "formula": "MA",
      "period": "1d",
      "args": { "N": 5 },
      "output_type": "scalar"
    },
    "ind_ma10_1d": {
      "type": "formula",
      "formula": "MA",
      "period": "1d",
      "args": { "N": 10 },
      "output_type": "scalar"
    },
    "ind_macd_1d": {
      "type": "formula",
      "formula": "MACD",
      "period": "1d",
      "args": { "SHORT": 12, "LONG": 26, "MID": 9 },
      "output_type": "multi"
    },
    "ind_pct_change_tick": {
      "type": "derived",
      "expr": "(close - pre_close) / pre_close * 100",
      "period": "tick",
      "output_type": "scalar"
    }
  }
}
```

**边的 filter 规格（四层）：**

```json
{
  "filter_specs": {
    "price_gt_10": {
      "filter_type": "independent",
      "indicators": ["ind_close_tick"],
      "compare": {
        "type": "scalar_compare",
        "operator": "gt",
        "indicator_ref": "ind_close_tick",
        "threshold": 10.0
      },
      "combine": {
        "type": "single"
      }
    },
    "ma_golden_cross": {
      "filter_type": "independent",
      "indicators": ["ind_ma5_1d", "ind_ma10_1d"],
      "compare": {
        "type": "cross",
        "direction": "above",
        "line1_ref": "ind_ma5_1d",
        "line2_ref": "ind_ma10_1d"
      },
      "combine": {
        "type": "single"
      }
    },
    "rank_top_10_pct": {
      "filter_type": "global",
      "indicators": ["ind_pct_change_tick"],
      "compare": null,
      "combine": {
        "type": "rank",
        "order": "desc",
        "indicator_ref": "ind_pct_change_tick",
        "n": 10,
        "tie_handling": "exact_rank"
      }
    },
    "multi_condition_and": {
      "filter_type": "independent",
      "indicators": ["ind_close_tick", "ind_ma5_1d"],
      "compare": {
        "type": "multi",
        "conditions": [
          { "type": "scalar_compare", "operator": "gt", "indicator_ref": "ind_close_tick", "threshold": 10.0 },
          { "type": "scalar_compare", "operator": "gt", "indicator_ref": "ind_ma5_1d", "threshold_ref": "ind_close_tick" }
        ]
      },
      "combine": {
        "type": "logic",
        "op": "and",
        "conditions": [0, 1]
      }
    }
  }
}
```

**四层结构清晰对应：**
- 第一层（指标层）：`indicators` 字段 → 引用全局 indicator_registry 的 ID
- 第二层（比较层）：`compare` 字段 → 单股独立比较
- 第三层（组合层）：`combine` 字段 → 排名、逻辑组合、集合运算
- 第四层（传播层）：在 edge_propagate_spec 里，不属于 filter 本身

### 5.3 传播模式（propagate_modes.json）

**结构：** `{mode_name: {op: fn, affects_source: bool}}`

| 模式 | 操作 | 影响源节点 |
|------|------|-----------|
| copy | target += passed | 否 |
| move | target += passed; source = [] | 是 |
| overwrite | target = passed | 否 |

**（这部分 v1.3 已经很好，v1.4/v1.5 不变）**

### 5.4 节点角色（node_roles.json）

**结构：** `{role_name: {triggers: [], actions: []}}`

**（这部分 v1.3 已经很好，v1.4/v1.5 不变）**

---

## 六、概念瘦身对照表（v1.4 → v1.5）

### 6.1 消除的概念冗余

| v1.4 概念 | v1.5 处理 | 理由 |
|-----------|-----------|------|
| `stock_data_version[code]` | **删除**，功能由 `stock_dirty[code][period]` 承接 | 都是回答"数据变了吗"，一个够了；周期级更精确 |
| `edge_pending` 队列 | **删除**，功能由 `dirty_nodes` 集合承接 | 节点脏了=所有出边要检查，用节点级更自然；概念更少 |
| "结果汇总层" | **拆分**为"组合层"+"传播层" | "结果汇总"概念太模糊；拆分后每层职责单一清晰 |

### 6.2 保留并升级的概念

| v1.4 概念 | v1.5 升级 | 说明 |
|-----------|-----------|------|
| `stock_dirty[code]` | 升级为 `stock_dirty[code][period]` | 从股票级变为周期级，更精确 |
| `latest_tick_ts` | 升级为 `period_ts[period]` | 从单一水位线变为各周期独立水位线 |
| `indicator_cache[formula_key]` | 升级为 `indicator_cache[indicator_id]` | 从边级公式键变为全局指标ID，编译期去重 |
| `dirty_nodes` | 升级为主触发源 | 从辅助集合变为主队列，替代 edge_pending |
| `edge_indicator_spec[eid]` | 改为 `edge_indicator_refs[eid]` | 从完整定义变为ID引用，指标全局管理 |

### 6.3 新增的概念

| 概念 | 说明 | 为什么需要 |
|------|------|-----------|
| `indicator_registry` | 全局指标注册表 | 编译期去重，运行时共享 |
| 组合层（combine） | filter 第三层 | 排名、集合运算、逻辑组合的统一归属 |
| 传播层（propagate） | filter 第四层 | 状态变更从计算中剥离，概念清晰 |
| 周期级脏标记 | stock_dirty[code][period] | 不同周期不同频率，低频指标少算 |

**净变化：** 删除 3 个概念，升级 5 个概念，新增 4 个概念 → **概念总数减少，清晰度提升**。

---

## 七、实现路线图（v1.5）

### 阶段一：编译期指标去重 + 全局注册表

1. 新增 `indicator_registry` 编译产物
2. 修改 `_compile_pool`，收集所有边的指标并去重
3. 边改为引用 indicator_id，不保存完整指标定义
4. 新增 `edge_indicator_refs` 编译产物

### 阶段二：周期级脏标记 + 多周期水位线

1. `stock_dirty` 从股票级改为周期级（`stock_dirty[code][period]`）
2. `latest_tick_ts` 改为 `period_ts[period]`
3. 数据更新时只标记对应周期为脏
4. 指标计算时只检查对应周期的脏标记

### 阶段三：节点脏驱动 + 消除 edge_pending

1. 用 `dirty_nodes` 替代 `edge_pending` 作为主队列
2. 数据更新时标记节点脏，不是边入队
3. 处理循环改为遍历 dirty_nodes，再遍历节点出边
4. 删除 `edge_pending` 相关代码

### 阶段四：filter 四层定型

1. 新增 `edge_combine_spec` 编译产物
2. 明确比较层和组合层的边界
3. 排名从比较层移到组合层
4. 传播层独立为第四步

### 阶段五：优化与验证

1. 性能测试：对比 v1.4 和 v1.5 的性能
2. 正确性验证：确保结果和之前一致
3. 代码清理：删除所有冗余概念的代码
