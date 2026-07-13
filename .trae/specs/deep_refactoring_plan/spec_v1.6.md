# 股票池深度重构规划 v1.6

> 版本主题：版本号机制 + 真概念瘦身 + 公式注册表 + K线数据层独立
> 设计原则：表驱动、数据驱动、事件驱动、增量优先、全量保底、周期分级、概念最简
> 目标：engine.py 从 3504 行 → ≤ 800 行，配置表从 50+ 张 → ≤ 12 张核心表，概念数量压到最少

---

## v1.5 → v1.6 变更摘要：版本号机制 + 真概念瘦身 + 公式注册表 + K线独立

**变更日期：** 2026-07-01

**致命bug修复：** 周期级脏标记的清脏逻辑错误——算一个指标就清一次周期脏，导致同周期后续指标读旧缓存。

**核心升级（六大改进方向）：**

| # | 变更项 | v1.5 | v1.6 | 本质变化 |
|---|--------|------|------|---------|
| 1 | **缓存有效性判断（致命bug修复）** | 脏标记 + 清脏（容易搞错清脏时机） | **版本号对比**（period_version vs indicator_cache.version） | 从"写时置脏+算完清脏"到"版本号自然递增，旧的就是旧的"，不会搞错时机 |
| 2 | **filter 结构（真概念瘦身）** | 四层：指标层→比较层→组合层→传播层 | **三层：指标层→比较层→集合运算层** | 传播是边的步骤不是filter层；"组合层"改叫"集合运算层"，更准确 |
| 3 | **概念瘦身（真正的少）** | stock_dirty + dirty_nodes + 组合层 + 传播层 | **删掉 stock_dirty，用 period_version 代替；传播从filter层移出** | 概念更少，filter三层就够了，传播是边的动作 |
| 4 | **公式注册表 + 比较算子正交化** | 指标和比较混在一起 | **公式注册表（指标）独立 + 比较算子独立，指标×算子=filter** | 笛卡尔积正交设计，不是每个指标自己实现比较 |
| 5 | **K线数据层独立** | K线合成是股票池引擎的一部分 | **K线合成是独立数据层，股票池只读K线缓存** | 职责分离，引擎不关心数据怎么来的，只关心版本号 |
| 6 | **运行时核心表** | 8 张 | **7 张（-1）** | 删掉 stock_dirty，新增 period_version，净减1张 |

**一句话总结 v1.6 升级：** 用版本号替代脏标记（彻底解决清脏时机bug），filter真瘦身到三层（指标→比较→集合运算，传播是边的步骤），公式注册表与比较算子正交化（笛卡尔积设计），K线数据层独立（引擎只读缓存不合成）。

---

## 一、本质认知：股票池到底是什么（v1.6 深化版）

### 1.1 一句话本质（更新）

**股票池 = 一组节点 × 一组边 × 周期版本号水位线 × 公式注册表 × 比较算子集 × 一个事件队列。**

- **节点**：装股票的容器（备选池/状态池/条件池/目标池）
- **边**：节点之间的连接，带触发条件（时间 + 过滤条件 + 传播模式）
- **周期版本号水位线**：`period_version[period]` —— 每个数据周期自己的版本号。**某周期版本号不涨，该周期的所有指标结果都不变。**
- **公式注册表**：`formula_registry` —— 所有可用指标的元数据，包括名称、参数、周期、输出字段。编译期去重，运行时共享。
- **比较算子集**：`comparison_operators` —— 独立的比较操作符（>/</=/金叉/死叉/上拐/下拐/排名前N/排名后N），与指标正交。
- **脏节点队列**：`dirty_nodes` —— 需要重新计算的节点。节点脏了 = 该节点的所有出边都需要检查。

### 1.2 核心洞察1：版本号比脏标记更清晰——不会搞错清脏时机

**这是 v1.6 最重要的认知升级（致命bug修复）。**

v1.5 的问题（致命bug）：
```
脏标记模型的bug：
  数据更新 → stock_dirty[code][period] = True
  算指标A → 算完后清 stock_dirty[code][period] = False  ❌
  算指标B → 发现 stock_dirty = False，读缓存
  但指标A刚算完就清脏了，指标B读到的是旧缓存！
```

**根本原因：** 脏标记是"一次性"的——谁先算谁清脏，后面的就误读旧缓存。

**正确的模型：版本号**

```
版本号模型：
  数据更新 → period_version[period] += 1
  指标缓存带版本号：indicator_cache[ind_id][code] = {value, version}
  判断是否重算：cache.version < period_version[period]
  
  指标A：cache.version=5 < period_version=6 → 重算 → 写入 version=6
  指标B：cache.version=5 < period_version=6 → 重算 → 写入 version=6
  指标C：cache.version=6 == period_version=6 → 用缓存 ✅
```

**版本号的优势：**
1. **不会搞错清脏时机**——没有"清脏"这个动作，版本号自然递增
2. **同一轮tick内，所有同周期指标都看到"数据是新的"**——版本号是周期级的水位线
3. **更直观**——版本号低就是旧的，版本号高就是新的
4. **可以追溯**——知道缓存是哪一轮的数据

**版本号是单调递增的，只增不减。** 旧缓存的版本号永远 ≤ 当前版本号。

### 1.3 核心洞察2：filter 三层就够了，传播是边的步骤不是filter层

**v1.5 的四层结构问题：** 传播层算 filter 的一层吗？传播是"状态变化"，不是"过滤计算"。

**v1.6 真瘦身：filter 只有三层，传播是边的执行步骤之一。**

```
边执行的完整步骤：
  1. gate（时间条件检查）
  2. filter 三层计算（指标→比较→集合运算）
  3. propagate（传播：copy/move/overwrite）
  4. 发事件
  5. 目标节点脏标记
```

**filter 三层结构：**

```
┌─────────────────────────────────────────────────────────┐
│  第三层：集合运算层（set_op）                              │
│  AND / OR / NOT / 排名前N / 排名后N / 集合交并差          │
│  输入：多个比较层的结果集合                               │
│  输出：最终通过的股票集合                                 │
├─────────────────────────────────────────────────────────┤
│  第二层：比较层（compare）                                │
│  > / < / = / 金叉 / 死叉 / 上拐 / 下拐                   │
│  输入：单只股票的指标值（从指标层来）                     │
│  输出：单只股票的 bool 结果（独立型）                     │
├─────────────────────────────────────────────────────────┤
│  第一层：指标层（indicator）                              │
│  MA / MACD / RSI / 涨幅 / 成交量 / 财务指标              │
│  纯函数：输入=数据，输出=指标值                           │
│  可缓存 / 可共享 / 周期分级 / 版本号管理                  │
└─────────────────────────────────────────────────────────┘
```

**为什么传播不是filter的一层？**
- filter 是"计算"，传播是"动作"
- filter 的输出是"通过的股票集合"，传播的输入就是这个集合
- 传播有副作用（修改节点状态、发事件），filter 是纯计算
- 传播模式（copy/move/overwrite）是边的属性，不是filter的属性
- **概念更清晰：filter 负责"选谁"，传播负责"怎么送过去"**

**为什么"组合层"改叫"集合运算层"？**
- "组合"太模糊——组合什么？怎么组合？
- "集合运算"更准确——就是对股票集合做 AND/OR/NOT/排名 等运算
- 输入是集合，输出是集合，一目了然

### 1.4 核心洞察3：公式注册表 × 比较算子 = 正交笛卡尔积

**当前的问题：** 指标和比较混在一起，每个指标好像自己会比较似的。

**正确的正交设计：**

```
公式注册表（指标）：
  MA, MACD, RSI, KDJ, BOLL, CCI, 涨幅, 成交量, ...
  （纯函数，输入数据，输出指标值）

比较算子（独立的）：
  >, <, =, 金叉, 死叉, 上拐, 下拐, 排名前N, 排名后N
  （纯函数，输入指标值，输出bool或排名）

filter = 指标 × 比较算子
  例如：MA > 10元 = MA指标 × "大于"算子 × 阈值10
  例如：MACD金叉 = MACD指标 × "金叉"算子
  例如：涨幅排名前10 = 涨幅指标 × "排名前N"算子 × N=10
```

**正交化的好处：**
1. **概念清晰**——指标负责"算值"，比较负责"判断"
2. **复用性高**——一个指标可以配任意比较算子，一个比较算子可以用在任意指标上
3. **易于扩展**——加一个新指标不需要重写比较逻辑，加一个新比较算子不需要改指标
4. **配置简单**——filter 配置就是"选哪个指标 + 选哪个比较算子 + 参数"

### 1.5 核心洞察4：K线合成是独立的数据层，不是股票池引擎的一部分

**当前的问题：** 股票池引擎既要管K线合成，又要管筛选逻辑，职责不清。

**v1.6 正确的分层：**

```
┌─────────────────────────────────────────────────────────┐
│  股票池引擎层（只做筛选和传播）                           │
│  - 读 K线缓存（带版本号）                                 │
│  - 算指标（带版本号）                                     │
│  - filter 三层计算                                       │
│  - 传播 + 事件                                           │
├─────────────────────────────────────────────────────────┤
│  K线数据层（独立服务）                                   │
│  - latest_tick：原始tick数据                             │
│  - kline_cache[period]：合成后的K线数据                   │
│  - 每个周期有自己的版本号 period_version[period]          │
│  - 数据更新时版本号递增                                   │
└─────────────────────────────────────────────────────────┘
```

**K线数据层的职责：**
- 接收原始tick数据
- 合成各周期K线（1min/5min/15min/.../day/week/month）
- 维护 K 线缓存
- 维护每个周期的版本号（数据更新就递增）
- 提供数据查询接口（股票池引擎只读）

**股票池引擎的职责：**
- 只读 K 线缓存和版本号
- 根据版本号判断指标缓存是否过期
- 计算指标、执行 filter、传播状态
- 发事件

**好处：**
1. **职责分离**——数据层管数据，引擎层管逻辑
2. **可替换**——K线数据层可以换成任意数据源，引擎不用改
3. **可测试**——两层可以独立测试
4. **更清晰**——引擎不关心数据怎么来的，只关心版本号

### 1.6 运行时只有五件事（v1.6 更新版）

| # | 事情 | 触发条件 | 做什么 |
|---|------|---------|-------|
| 1 | **数据更新（K线数据层）** | 外部行情推送 | 写 `latest_tick`，合成K线，更新 `kline_cache[period]`，递增 `period_version[period]`，股票所在节点加入 `dirty_nodes` |
| 2 | **TTL 过期处理** | tick 开始时检查过期队列 | 从 `ttl_expiry_queue` 弹出已过期股票，从节点移除，目标节点加入 `dirty_nodes` |
| 3 | **节点脏处理（事件驱动）** | 从 `dirty_nodes` 取节点 | 按执行顺序遍历节点出边 → 检查三要素 → 满足则执行 filter 三层 → propagate → 发事件 → 目标节点变化则加入 `dirty_nodes` |
| 4 | **无条件边立即传播**（同步） | 源节点股票状态变化时立即触发 | 直接 propagate，不走 gate，不走 filter，立即更新目标节点，立即发事件 |
| 5 | **指标缓存维护（版本号机制）** | 指标计算时 | 对比 `indicator_cache.version` vs `period_version[period]`，版本低就重算，写入新版本号 |

**正确的因果链（v1.6 深化版）：**

```
数据更新（某周期的数据来了）
  ↓
K线数据层处理：
  latest_tick[code] = new_bar
  合成对应周期的K线
  kline_cache[period][code] = new_kline
  period_version[period] += 1  （版本号递增，没有"清脏"）
  ↓
股票所在节点标记为脏（加入 dirty_nodes）
  ↓
处理 dirty_nodes 时，遍历节点出边
  ↓
边执行：
  第一层（指标层）：对比版本号，cache.version < period_version 就重算
  第二层（比较层）：独立型增量，排名型全量
  第三层（集合运算层）：AND/OR/NOT/排名
  propagate（边的步骤，不是filter层）：copy/move/overwrite
  ↓
目标节点状态变化 → 目标节点加入 dirty_nodes → 触发下游
```

---

## 二、核心设计：版本号机制 + filter三层 + 公式注册表 + K线独立

### 2.1 运行时内存表（核心运行时表一共 7 张，v1.6 瘦身版）

| 表名 | 类型 | 读时机 | 写时机 | 说明 |
|------|------|--------|--------|------|
| `latest_tick` | Dict[code → bar_dict] | 指标计算时读 | 行情推送时写（K线数据层） | **唯一真相源**。所有股票的最新tick数据 |
| `kline_cache` | Dict[period → Dict[code → bar_list]] | 指标计算时读 | K线合成后写（K线数据层） | **各周期K线缓存**。股票池引擎只读，不负责合成 |
| `period_version` | Dict[period → int] | 版本号对比时读 | 对应周期数据更新时写（K线数据层） | **各周期独立版本号水位线**。单调递增，只增不减。版本号低=缓存旧 |
| `indicator_cache` | Dict[indicator_id → Dict[code → {value, version}]] | 指标计算前先查 | 指标计算完后写 | **全局指标结果缓存 + 版本号**。每个缓存项带自己的版本号，和周期版本号对比 |
| `node_stocks` | Dict[nid → List[code]] | propagate 读写、filter 读 | 边执行/TTL过期后写 | 各节点当前股票列表 |
| `dirty_nodes` | Set[nid]（按执行顺序处理） | 执行循环读 | 节点股票状态变化时加入 / 数据更新时加入 | **脏节点集合**。节点脏了=所有出边需要检查 |
| `ttl_expiry_queue` | Heap[(expire_ts, nid, code)] | tick 开始时弹出 | 股票入池时插入 | TTL 过期队列。按过期时间排序的最小堆 |

**就这七张核心运行时表。** 其他的都是运行前编译产物或配置表。

**v1.6 相比 v1.5 的变化：**

| v1.5（8张） | v1.6（7张） | 变化 |
|-------------|-------------|------|
| `latest_tick` | `latest_tick` | 不变（但归K线数据层管） |
| `period_ts` | `period_version` | **从时间戳改为版本号**（int单调递增，不是时间戳），更清晰 |
| `stock_dirty[code][period]` | — | **删除**，致命bug的根源。用版本号对比替代 |
| `indicator_cache[ind_id][code]` | `indicator_cache[ind_id][code] = {value, version}` | **升级**，每个缓存项带版本号，和周期版本号对比 |
| `node_stocks` | `node_stocks` | 不变 |
| `stock_state_version` | — | **删除**（如果之前有的话，v1.5里有但不算核心8张） |
| `dirty_nodes` | `dirty_nodes` | 不变 |
| `edge_filter_cache` | — | **移出核心表**（可选优化，不算核心） |
| `ttl_expiry_queue` | `ttl_expiry_queue` | 不变 |
| — | `kline_cache` | **新增核心表**（K线数据层独立） |

**净变化：8张 → 7张，减少1张。**

**indicator_cache 的详细结构（v1.6 版本号版）：**

```python
indicator_cache = {
    # key = 编译期分配的全局唯一 indicator_id（已去重）
    "ind_001": {
        # 这个指标是什么？编译期已知：
        #   formula: "MA"
        #   period: "1d"
        #   args: {"N": 5}
        "600519": {
            "value": 1800.5,      # 指标值（标量或dict多输出）
            "version": 42,        # 这个缓存的版本号
                                   # version < period_version["1d"] 就需要重算
        },
        "000001": {
            "value": 12.5,
            "version": 42,
        },
        # ...
    },
    "ind_002": {
        # formula: "MACD"
        # period: "1d"
        # args: {"SHORT": 12, "LONG": 26, "MID": 9}
        "600519": {
            "value": {              # 多输出
                "DIF": 1.2,
                "DEA": 0.8,
                "MACD": 0.4,
            },
            "version": 42,
        },
        # ...
    },
    # ...
}
```

**period_version 的详细结构：**

```python
period_version = {
    "tick": 10000,      # tick 级当前版本号
    "1m": 240,          # 1分钟线当前版本号
    "5m": 48,           # 5分钟线当前版本号
    "15m": 16,
    "30m": 8,
    "60m": 4,
    "1d": 42,           # 日线当前版本号（今天第42次更新？不，日线一天只更一次）
    "1wk": 6,
    "1mon": 2,
}
```

**关键：版本号是单调递增的整数，不是时间戳。**
- 每更新一次数据，版本号 +1
- 不需要关心时间，只关心版本号高低
- 缓存版本号 < 周期版本号 = 缓存旧了，需要重算

**kline_cache 的详细结构：**

```python
kline_cache = {
    "1d": {                     # 周期
        "600519": [             # 股票代码
            {open: 1780, high: 1820, low: 1770, close: 1800.5, volume: ...},
            {open: 1750, high: 1790, low: 1740, close: 1780, volume: ...},
            # ... 历史K线
        ],
        # ...
    },
    "1m": {
        # ...
    },
    # ...
}
```

### 2.2 编译期：公式注册表 + 比较算子正交化 + 边引用

**编译期做的事：**

1. 加载 `formula_registry`（公式注册表，所有可用指标）
2. 加载 `comparison_operators`（比较算子集，所有可用比较算子）
3. 遍历所有边的 filter 配置
4. 提取每个 filter 用到的（公式 + 参数 + 周期）三元组
5. 全局去重：相同三元组合并为一个指标，分配 indicator_id
6. 提取每个 filter 用到的比较算子和参数
7. 边只保存引用：指标ID + 比较算子ID + 参数

```
编译前（边里嵌完整配置）：
  edge1: 指标=MA(5, 1d), 比较=大于(10元)
  edge2: 指标=MA(5, 1d), 比较=金叉(MA10)
  edge3: 指标=涨幅, 比较=排名前N(10)

编译后（公式注册表 + 比较算子 + 边引用）：
  formula_registry:
    ind_001: MA(N=5, period=1d)
    ind_002: MA(N=10, period=1d)
    ind_003: 涨幅(period=tick)
  
  comparison_operators:
    op_gt: 大于（阈值）
    op_golden_cross: 金叉（两条线）
    op_rank_top: 排名前N
  
  edge_filter_spec:
    edge1: {indicator: ind_001, operator: op_gt, params: {threshold: 10}}
    edge2: {indicators: [ind_001, ind_002], operator: op_golden_cross, params: {}}
    edge3: {indicator: ind_003, operator: op_rank_top, params: {n: 10}}
```

**编译期新增/修改产物（v1.6）：**

| 编译产物 | 类型 | 说明 |
|----------|------|------|
| `formula_registry` | Dict[indicator_id → formula_spec] | **公式注册表**。所有去重后的指标，编译期一次性收集 |
| `comparison_operators` | Dict[op_id → operator_spec] | **比较算子集**。所有可用的比较算子，独立于指标 |
| `edge_indicator_refs` | Dict[eid → List[indicator_id]] | 每条边引用的指标ID列表 |
| `edge_compare_spec` | Dict[eid → compare_spec] | 比较层规格（用哪个算子、参数是什么） |
| `edge_set_op_spec` | Dict[eid → set_op_spec] | **集合运算层规格**（AND/OR/NOT/排名逻辑，v1.5叫组合层） |
| `edge_filter_type` | Dict[eid → 'independent' / 'global'] | filter 类型：单股独立型 / 全局依赖型 |

**formula_registry 的详细结构：**

```python
formula_registry = {
    "ind_001": {
        "id": "ind_001",
        "name": "MA",                   # 公式名称
        "type": "formula",              # formula / scalar_field / derived / financial
        "period": "1d",                 # 数据周期（编译期已知！）
        "args": {"N": 5},               # 公式参数
        "output_type": "scalar",        # scalar / multi / series
        "output_fields": ["MA"],        # 输出字段名（多输出时有多个）
        "cache_key": "MA_5_1d",         # 缓存 key（编译期预计算）
    },
    "ind_002": {
        "id": "ind_002",
        "name": "MACD",
        "type": "formula",
        "period": "1d",
        "args": {"SHORT": 12, "LONG": 26, "MID": 9},
        "output_type": "multi",         # 多输出（DIF/DEA/MACD）
        "output_fields": ["DIF", "DEA", "MACD"],
        "cache_key": "MACD_12_26_9_1d",
    },
    "ind_003": {
        "id": "ind_003",
        "name": "close",
        "type": "scalar_field",
        "field": "close",
        "period": "tick",               # tick 级数据
        "output_type": "scalar",
        "output_fields": ["close"],
        "cache_key": "close_tick",
    },
    # ...
}
```

**comparison_operators 的详细结构：**

```python
comparison_operators = {
    "op_gt": {
        "id": "op_gt",
        "name": "大于",
        "type": "scalar_compare",       # scalar_compare / cross / inflection / rank
        "input_type": "single",         # single / dual（单指标/双指标）
        "params": ["threshold"],        # 需要的参数
        "description": "line1 当前值大于阈值",
    },
    "op_lt": {
        "id": "op_lt",
        "name": "小于",
        "type": "scalar_compare",
        "input_type": "single",
        "params": ["threshold"],
        "description": "line1 当前值小于阈值",
    },
    "op_eq": {
        "id": "op_eq",
        "name": "等于",
        "type": "scalar_compare",
        "input_type": "single",
        "params": ["threshold", "tolerance"],
        "description": "line1 当前值近似等于阈值",
    },
    "op_golden_cross": {
        "id": "op_golden_cross",
        "name": "金叉",
        "type": "cross",                # 穿越型
        "input_type": "dual",           # 需要两个指标
        "params": [],                   # 不需要额外参数
        "direction": "above",           # 上穿
        "window": 2,                    # 需要几期数据
        "description": "line1 上穿 line2（金叉）",
    },
    "op_dead_cross": {
        "id": "op_dead_cross",
        "name": "死叉",
        "type": "cross",
        "input_type": "dual",
        "params": [],
        "direction": "below",           # 下穿
        "window": 2,
        "description": "line1 下穿 line2（死叉）",
    },
    "op_turn_up": {
        "id": "op_turn_up",
        "name": "上拐",
        "type": "inflection",           # 拐点点
        "input_type": "single",
        "params": [],
        "direction": "up",
        "window": 3,
        "description": "曲线由降转升",
    },
    "op_turn_down": {
        "id": "op_turn_down",
        "name": "下拐",
        "type": "inflection",
        "input_type": "single",
        "params": [],
        "direction": "down",
        "window": 3,
        "description": "曲线由升转降",
    },
    "op_rank_top": {
        "id": "op_rank_top",
        "name": "排名前N",
        "type": "rank",                 # 排名型
        "input_type": "single",
        "params": ["n"],
        "order": "desc",                # 降序（值大的在前）
        "description": "按指标值降序取前N名",
    },
    "op_rank_bottom": {
        "id": "op_rank_bottom",
        "name": "排名后N",
        "type": "rank",
        "input_type": "single",
        "params": ["n"],
        "order": "asc",                 # 升序（值小的在前）
        "description": "按指标值升序取前N名（即倒数后N名）",
    },
    # ...
}
```

### 2.3 Filter 三层结构详解（v1.6 真瘦身版）

#### 第一层：指标层（Indicator Layer）

**性质：纯函数，完全可缓存，股票级独立，全局共享，周期分级，版本号管理。**

**输入：**
- 某只股票的对应周期数据（从 `kline_cache[period]` 读）
- 公式参数（编译期已知）

**输出：**
- 标量值（单输出，如 MA、收盘价、涨幅）
- dict 多输出（如 MACD 的 DIF/DEA/MACD，KDJ 的 K/D/J）
- list/tuple 时间序列（如果需要历史值做比较判断）

**缓存有效性判断（版本号机制）：**
```python
def need_recompute(indicator_id, code):
    spec = formula_registry[indicator_id]
    period = spec["period"]
    current_version = period_version[period]
    cache_entry = indicator_cache.get(indicator_id, {}).get(code)
    if cache_entry is None:
        return True  # 没有缓存，需要算
    return cache_entry["version"] < current_version  # 版本低就重算
```

**没有"清脏"动作。** 版本号自然递增，旧的就是旧的。

#### 第二层：比较层（Comparison Layer）

**性质：逻辑运算，独立型可缓存可增量，排名型需全量。**

**输入：**
- 指标值（从 `indicator_cache` 读）
- 比较算子（从 `comparison_operators` 读）
- 算子参数

**输出：**
- bool（通过/不通过，独立型）

**分类：**

| 比较类型 | 性质 | 可增量 | 例子 |
|---------|------|--------|------|
| **标量比较** | 单股独立，只和阈值比 | ✅ 股票级增量 | 价格>10、涨幅>5%、MACD>0、成交量>1000万 |
| **穿越比较** | 需要历史值，但是单股独立 | ✅ 股票级增量（需要指标的时间序列） | 金叉、死叉、上拐、下拐 |
| **排名比较** | 截面运算，依赖所有股票 | ❌ 需全量排名 | 涨幅前10、成交量后20 |

**注意：排名在第三层集合运算层，不在这一层。** 这一层只有"单股独立"的比较。

#### 第三层：集合运算层（Set Operation Layer）

**性质：集合运算，动态的，不缓存。**

**输入：**
- 多个比较层的结果（bool 集合）
- 集合运算规则（AND/OR/NOT/排名/交并差）

**输出：**
- 最终通过 filter 的股票集合

**为什么排名在这一层？**
- 排名是截面运算，依赖所有股票的指标值
- 排名不是"比较"（单股独立），而是"集合运算"（多股相对排序）
- 放在集合运算层，概念更清晰

**集合运算层的子类型：**

| 子类型 | 说明 | 可增量 |
|--------|------|--------|
| 逻辑运算 | AND / OR / NOT | ⚠️ 部分可增量（短路优化） |
| 排名运算 | 排名前N / 排名后N | ❌ 需全量重排 |
| 集合运算 | 交集 / 并集 / 差集 | ⚠️ 取决于子条件 |

**为什么叫"集合运算层"不叫"组合层"？**
- "组合"太模糊，不知道组合什么
- "集合运算"很明确：输入是集合，输出是集合，做的是集合的运算
- 股票筛选本质上就是集合运算：从源集合里选出满足条件的子集

### 2.4 版本号机制详解（替代脏标记的正确模型）

#### 数据更新时的版本号递增

```python
# K线数据层：数据更新时
def on_tick(code, bar, period="tick"):
    # 1. 写原始tick
    latest_tick[code] = bar
    
    # 2. 如果是更高周期，合成K线
    if period != "tick":
        synthesized = synthesize_kline(code, bar, period)
        if synthesized:
            kline_cache[period][code] = synthesized
    
    # 3. 版本号递增（关键：没有"清脏"，只有版本号上涨）
    period_version[period] += 1
    
    # 4. 股票所在节点标记为脏
    for nid in code_nodes[code]:
        dirty_nodes.add(nid)
```

**关键：版本号是周期级的，不是股票级的，也不是指标级的。**
- 一个周期的数据更新了，该周期版本号 +1
- 该周期的所有指标缓存，版本号都可能低于当前版本号
- 每个指标自己判断要不要重算（对比版本号）

#### 指标计算时的版本号对比

```python
# 取指标时
def get_indicator(indicator_id, code):
    spec = formula_registry[indicator_id]
    period = spec["period"]
    current_version = period_version[period]  # 当前周期版本号
    
    cache = indicator_cache.get(indicator_id, {})
    entry = cache.get(code)
    
    if entry is not None and entry["version"] >= current_version:
        # 缓存有效（版本号够新），直接返回
        return entry["value"]
    else:
        # 需要重算（版本号旧，或没有缓存）
        value = compute_indicator(indicator_id, code)
        cache[code] = {
            'value': value,
            'version': current_version,  # 写入新版本号
        }
        return value
```

**版本号机制的优势（对比脏标记）：**

| 对比项 | 脏标记模型（v1.5，有bug） | 版本号模型（v1.6，正确） |
|--------|--------------------------|------------------------|
| **清脏时机** | 需要"算完清脏"，容易搞错（先算的先清，后算的误读） | 不需要清脏，版本号自然递增 |
| **同轮tick多指标** | 有bug：A算完清脏，B读旧缓存 | 正确：A和B都看到 version < current_version，都重算 |
| **直观性** | 脏=需要算？不脏=不用算？清脏时机容易搞混 | 版本低=旧的，要重算；版本高=新的，用缓存。一目了然 |
| **可追溯性** | 不知道缓存是哪一轮的 | 知道缓存的版本号，可以对比 |
| **实现复杂度** | 需要维护脏标记 + 清脏逻辑 | 只需要递增版本号 + 对比 |

### 2.5 边触发机制：节点脏驱动 + 三要素 AND + 边执行步骤

**边执行的完整步骤（v1.6 更新版）：**

```
边执行步骤（注意：filter 只有三层，传播是边的步骤）：
  1. gate：时间条件检查
  2. filter 第一层：指标计算（版本号对比 + 全局缓存）
  3. filter 第二层：比较判断（独立型增量）
  4. filter 第三层：集合运算（AND/OR/NOT/排名）
  5. propagate：传播（copy/move/overwrite）← 这是边的步骤，不是filter层
  6. 发事件
  7. 目标节点脏标记
```

**三要素不变（v1.3 已确定，v1.6 沿用）：**

```
边执行 = 时间条件满足 
       AND 源节点有变化（股票增减 OR 数据版本更新）
       AND 数据版本 > 边上次处理版本
```

**v1.6 补充：边执行时的 filter 三层 + propagate 流程**

```
边执行开始：
  ↓
收集这条边引用的所有指标ID（从 edge_indicator_refs 读）
  ↓
=== 第一层：指标计算（版本号对比 + 全局缓存共享） ===
对每个 indicator_id:
  spec = formula_registry[indicator_id]
  period = spec.period  # 编译期已知
  current_version = period_version[period]
  source_codes = node_stocks[sid]
  
  # 找出需要重算的股票（版本号低 OR 还没算过）
  need_compute = [code for code in source_codes
                  if code not in indicator_cache.get(indicator_id, {})
                     or indicator_cache[indicator_id][code]["version"] < current_version]
  
  if need_compute:
    # 批量计算
    values = compute_indicator_batch(indicator_id, need_compute)
    # 写入全局缓存（带新版本号）
    for code, val in zip(need_compute, values):
      indicator_cache[indicator_id][code] = {
          'value': val,
          'version': current_version,  # ✅ 写入新版本号
      }
    # 注意：没有"清脏"动作！
    # 版本号已经是 current_version 了，下次对比就知道是新的

所有指标值都准备好了（缓存的 + 新算的）
  ↓
=== 第二层：比较判断（独立型增量） ===
compare_spec = edge_compare_spec[eid]
operator = comparison_operators[compare_spec["operator_id"]]

如果是独立比较型（scalar_compare / cross / inflection）：
  → 增量比较：只重算数据变了的股票，其他读 edge_filter_cache
  → 汇总得到 per_stock_result[code] = bool
  ↓
=== 第三层：集合运算（AND/OR/NOT/排名） ===
set_op_spec = edge_set_op_spec[eid]

如果是排名型（rank）：
  → 全量读指标值（从 indicator_cache，都是最新的）
  → 全量排名
  → passed_codes = 排名结果
如果是逻辑组合型（logic）：
  → 组合多个比较结果
  → passed_codes = 组合结果
如果是集合运算型（set_op）：
  → 多个源集合的交并差
  → passed_codes = 运算结果
  ↓
=== propagate（边的步骤，不是filter层） ===
propagate_spec = edge_propagate_spec[eid]
→ 应用传播模式到 node_stocks[tid]
→ 对比得到 entered_codes 和 exited_codes
  ↓
如果有变化：
  → tid 加入 dirty_nodes
  → 处理无条件边：立即同步 propagate
  → 立即发射入池/出池事件
  ↓
更新 edge_last_data_version[eid] = period_version["tick"]  # 用tick周期作为全局版本基准
```

---

## 三、运行前 vs 运行时：严格分离（v1.6 更新版）

### 3.1 运行前（设计时 + 加载时）做的事

全部一次性做完，运行时只读。

| 阶段 | 做什么 | 产出 |
|------|--------|------|
| **设计时** | 用户拖拽节点、连线、配置参数、设置执行顺序 | pool_config (JSON) |
| **加载时** | 解析 pool_config，**全局收集指标并去重**，拆分 filter 为三层，判断 filter 类型，编译所有规格 | CompiledPool |

**CompiledPool v1.6 更新版：**

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
    
    # ===== v1.6 核心变更：公式注册表 + 比较算子正交化 =====
    'formula_registry': {                # **公式注册表（编译期去重）**
        indicator_id: {
            'name': 'MA',                # 公式名
            'type': 'formula' / 'scalar_field' / 'derived' / 'financial',
            'period': '1d',              # 数据周期（编译期已知！）
            'args': {...},               # 参数
            'output_type': 'scalar',     # scalar / multi / series
            'output_fields': [...],      # 输出字段名
            'cache_key': 'MA_5_1d',      # 缓存 key（预计算）
        },
        ...
    },
    
    'comparison_operators': {            # **比较算子集（独立于指标）**
        op_id: {
            'name': '大于',
            'type': 'scalar_compare' / 'cross' / 'inflection' / 'rank',
            'input_type': 'single' / 'dual',
            'params': [...],
            ...
        },
        ...
    },
    
    # ===== filter 三层规格 =====
    'edge_indicator_refs': {             # 第一层：指标引用（ID列表）
        eid: [ind_id1, ind_id2, ...]
    },
    'edge_compare_spec': {               # 第二层：比较规格
        eid: {operator_id, params, ...}
    },
    'edge_set_op_spec': {                # 第三层：集合运算规格
        eid: {type: 'logic' / 'rank' / 'set_op', ...}
    },
    'edge_filter_type': {                # filter 类型
        eid: 'independent' | 'global'
    },
    
    # 其他规格
    'edge_timing_spec': {eid: spec},
    'edge_propagate_spec': {eid: spec},  # 传播规格（边的属性，不是filter层）
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

**v1.6 相比 v1.5 的编译期变化：**

| v1.5 | v1.6 | 变化原因 |
|------|------|---------|
| `indicator_registry` | `formula_registry` | 改名，更准确——这是公式的注册表，不是指标的注册表 |
| 无 `comparison_operators` | 新增 `comparison_operators` | 比较算子独立，与指标正交 |
| `edge_combine_spec` | `edge_set_op_spec` | "组合层"改叫"集合运算层"，更准确 |
| 传播层是filter第四层 | 传播是边的步骤，不是filter层 | 概念瘦身，filter只有三层 |

### 3.2 运行时做的事（事件驱动 + 三层filter + 版本号机制 + 节点脏驱动）

就一个循环（**节点脏驱动 + 流式串行执行 + 版本号指标缓存 + 增量/全量混合策略**）：

```
1. 等数据更新（或时间步进）
2. 新数据来了吗？
   → 没来：去步骤 3
   → 来了：
       period = 数据的周期（tick / 1m / 5m / ... / 1d）
       latest_tick[code] = new_bar
       
       # K线数据层：合成K线（如果需要）
       if period != "tick":
           kline_cache[period][code] = synthesize_kline(code, new_bar, period)
       
       # 版本号递增（关键：没有"清脏"，只有版本号上涨）
       period_version[period] += 1
       
       # 股票所在节点标记为脏
       for nid in code_nodes[code]:
           dirty_nodes.add(nid)
3. 处理 TTL 过期：
   从 ttl_expiry_queue 弹出所有 expire_ts <= now 的股票
   → 从对应节点 node_stocks[nid] 移除
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
               period_version["tick"] > edge_last_data_version[eid] 吗？
               → 不大于：数据没变，跳过
            ③ 源节点有变化吗？
               （能进 dirty_nodes 说明有变化，继续）
         
         b. === 第一层：指标计算（版本号对比 + 全局缓存） ===
            indicator_ids = edge_indicator_refs[eid]
            source_codes = node_stocks[sid]
            
            对每个 ind_id in indicator_ids:
                spec = formula_registry[ind_id]
                period = spec["period"]
                current_version = period_version[period]
                
                # 找出需要重算的股票（版本号低 OR 还没算过）
                need_compute = [code for code in source_codes
                                if code not in indicator_cache.get(ind_id, {})
                                   or indicator_cache[ind_id][code]["version"] < current_version]
                
                if need_compute:
                    # 批量计算
                    values = compute_indicator_batch(ind_id, need_compute)
                    # 写入全局缓存（带新版本号）
                    for code, val in zip(need_compute, values):
                        indicator_cache[ind_id][code] = {
                            'value': val,
                            'version': current_version,  # 写入新版本号
                        }
                    # 注意：没有"清脏"动作！
                    # 版本号已经是 current_version 了，下次对比就知道是新的
            
            # 此时，所有指标值都准备好了（全局缓存的 + 新算的）
            # 其他边如果也用这些指标，直接读缓存就行
         
         c. === 第二层：比较判断（独立型增量） ===
            compare_spec = edge_compare_spec[eid]
            operator = comparison_operators[compare_spec["operator_id"]]
            
            如果是独立比较型（scalar_compare / cross / inflection）：
               → 走增量比较
                  dirty_codes = [code for code in source_codes
                                 if code not in edge_filter_cache.get(eid, {})
                                    or 数据有变化]  # 用节点状态变化判断
                  
                  need_compare = dirty_codes + new_codes
                  
                  if need_compare:
                      for code in need_compare:
                          从 indicator_cache 读指标值
                          用 operator 比较
                          结果写入 edge_filter_cache[eid][code]
                  
                  # 汇总通过的
                  passed_codes = [code for code in source_codes
                                  if edge_filter_cache[eid].get(code, False)]
         
         d. === 第三层：集合运算（AND/OR/NOT/排名） ===
            set_op_spec = edge_set_op_spec[eid]
            
            如果是排名型（rank）：
               → 全量读指标值（从 indicator_cache，都是最新的）
               → 全量排名
               → passed_codes = 排名结果
            
            如果是逻辑组合型（logic）：
               → 组合多个比较结果
               → passed_codes = 组合结果
            
            如果是集合运算型（set_op）：
               → 多个源集合的交并差
               → passed_codes = 运算结果
         
         e. === propagate（边的步骤，不是filter层） ===
            propagate_spec = edge_propagate_spec[eid]
            → 应用传播模式到 node_stocks[tid]
            → 对比得到 entered_codes 和 exited_codes
         
         f. 如果有变化：
              - tid 加入 dirty_nodes
              - 处理无条件边：立即同步 propagate
              - 立即发射入池/出池事件
         
         g. 更新 edge_last_data_version[eid] = period_version["tick"]
      
      # 处理完这个节点的所有边后，从 dirty_nodes 移除
      dirty_nodes.discard(nid)
5. 回去等下一次数据更新
```

**v1.6 核心循环的关键变化（相对于 v1.5）：**

1. **版本号替代脏标记**：没有 stock_dirty，没有清脏动作，只有 period_version 单调递增
2. **指标缓存带版本号**：每个缓存项有自己的 version，和周期版本号对比
3. **filter 三层定型**：指标层→比较层→集合运算层，传播是边的步骤不是filter层
4. **"组合层"改叫"集合运算层"**：更准确，输入输出都是集合
5. **公式注册表 + 比较算子正交化**：指标和比较是独立的两个维度，笛卡尔积
6. **K线数据层独立**：kline_cache 是独立的数据层，股票池引擎只读

---

## 四、功能-表操作对应表（v1.6 更新版）

### 4.1 数据层（K线数据层独立 + 版本号机制）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| 行情推送（K线数据层） | — | `latest_tick[code] = new_bar` + `kline_cache[period][code]` 更新 + `period_version[period] += 1` | **v1.6 核心：版本号递增**，没有清脏动作；K线合成是独立数据层 |
| 周期版本号更新 | — | `period_version[period] += 1` | 每个周期独立版本号，单调递增 |
| **数据更新触发节点脏** | `code_nodes[code]` | `dirty_nodes.add(nid)` | 不变，节点脏驱动 |
| **全局指标缓存查询** | `indicator_cache[indicator_id][code]` + `period_version[period]` | — | **v1.6 核心**：对比版本号，cache.version < period_version 就重算 |
| **全局指标缓存写入** | 计算结果 | `indicator_cache[indicator_id][code] = {value, version}` | **v1.6 核心**：写入新版本号，没有清脏 |
| **编译期指标去重** | 所有边的指标配置 | `formula_registry` + `edge_indicator_refs` | 不变，相同（公式+参数+周期）合并为一个 |
| **K线合成** | `latest_tick` | `kline_cache[period][code]` | **v1.6 新增**：K线数据层独立，引擎不负责合成 |

**关键变化（v1.5 → v1.6）：**
- 脏标记 → 版本号（stock_dirty 删除，period_version 替代）
- 指标缓存不带版本号 → 带版本号
- 清脏动作 → 无（版本号自然递增）
- K线合成是引擎的一部分 → K线数据层独立
- "组合层" → "集合运算层"（改名，更准确）

### 4.2 TTL 淘汰层（事件驱动，非轮询）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| 股票入池记录 TTL | `edge_ttl_spec[eid]` | `ttl_expiry_queue` 插入 `(expire_ts, nid, code)` | `expire_ts = now + ttl_sec` |
| TTL 过期检查 | `ttl_expiry_queue + now` | 弹出过期项 | 最小堆：堆顶过期就弹出，直到堆顶未过期 |
| 过期股票移除 | `node_stocks[nid]` | `node_stocks[nid]` | 从节点移除过期股票 |
| 过期触发级联 | — | `dirty_nodes.add(nid)` + 无条件边立即执行 | 节点脏驱动，源节点变了 → 节点脏 → 出边都要检查 |

**（这部分基本不变，只是触发从 stock_dirty 改为版本号机制）**

### 4.3 边触发判定层（节点脏驱动 + 三要素 AND）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **数据更新 → 节点脏** | `code_nodes[code]` | `dirty_nodes.add(nid)` | 股票有新数据 → 所在节点标记为脏 |
| 节点股票状态变化 → 节点脏 | `out_edges[sid]` | `dirty_nodes.add(nid)` | 源节点变了 → 节点脏 → 出边都要检查 |
| 边时间触发检查 | `edge_timing_spec[eid] + period_version["tick"] + _flow_state[eid]` | — | `starttype × cxtype` 的判定 |
| **三要素检查** | `period_version["tick"]` + `edge_last_data_version[eid]` | — | 时间条件 AND 数据版本更新 AND 源节点有变化 |
| **周期级指标缓存检查** | `indicator_cache[indicator_id]` + `period_version[period]` | — | **v1.6 核心**：对比版本号，cache.version < period_version 就重算 |
| 增量筛选需要重算的股票（独立型比较） | `node_stocks[sid] + edge_filter_cache[eid]` | — | `need_compare = (code not in cache) OR (状态变化)` |
| 全量判断（全局依赖型比较） | `period_version["tick"]` + `edge_last_data_version[eid]` | — | `period_version["tick"] > edge_last_data_version` 就全量重算比较（但指标读缓存） |
| 边是否需要执行 | 三要素是否全部满足 | — | 三要素都满足就执行，否则跳过 |

**关键变化（v1.5 → v1.6）：**
- 脏标记检查 → 版本号对比
- 没有清脏动作
- edge_last_data_ts → edge_last_data_version（版本号，不是时间戳）

### 4.4 边执行层（三层filter + 混合策略 + propagate + 即时事件）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **filter 类型判断** | `edge_filter_type[eid]` | — | 先判断 independent / global，再选策略 |
| **第一层：指标计算（全局缓存 + 版本号）** | `formula_registry` + `indicator_cache[ind_id]` + `period_version[period]` + `kline_cache` + `edge_indicator_refs[eid]` | `indicator_cache[ind_id][code] = {value, version}` | **v1.6 核心**：版本号对比、全局去重、批量计算、写入新版本号 |
| **第二层：比较判断（独立型增量）** | `indicator_cache` + `comparison_operators` + `edge_compare_spec[eid]` + `edge_filter_cache[eid]` | `edge_filter_cache[eid][code]` | 不变，只重算数据变了的股票的比较 |
| **第三层：集合运算（AND/OR/NOT/排名）** | `indicator_cache` + `edge_set_op_spec[eid]` + `node_stocks[sid]` | — | **v1.6 改名**：从"组合层"改为"集合运算层"，更准确 |
| **propagate（边的步骤）** | `node_stocks[sid]` + `node_stocks[tid]` + `edge_propagate_spec[eid]` | `node_stocks[tid]` | **v1.6 移出filter**：传播是边的步骤，不是filter的一层 |
| **即时事件发射** | `node_stocks[tid]` 新旧对比 + `node_role[tid]` | `event_queue` + `signal_queue` | 差集计算 | 每条边执行后立即发射 |
| 无条件边立即传播 | `node_stocks[sid]` + `edge_propagate_spec[eid]` | `node_stocks[tid]` | 直接 propagate，无 gate，无 filter |
| 边处理版本号更新 | `period_version["tick"]` | `edge_last_data_version[eid]` | `edge_last_data_version[eid] = period_version["tick"]` |

**关键变化（v1.5 → v1.6）：**
- filter 从四层变成三层：指标层→比较层→集合运算层
- 传播从 filter 第四层 → 边的独立步骤
- "组合层" → "集合运算层"（改名）
- 指标缓存不带版本号 → 带版本号
- 清脏动作 → 无（版本号自然递增）

### 4.5 事件层（流式逐条产生）

| 功能 | 读什么表 | 写什么表 | 计算 | 时机 |
|------|---------|---------|------|------|
| 入池事件 | `node_stocks[tid]` 执行前后对比 | `event_queue` | 差集计算（新 - 旧） | 每条边执行后立即发射 |
| 出池事件 | `node_stocks[sid]` 执行前后对比 | `event_queue` | 差集计算（旧 - 新） | 每条边执行后立即发射 |
| 预警事件 | `alert_rules + node_stocks` 变化 | `alert_queue` | 规则匹配 | 每条边执行后立即检查 |
| 交易信号 | `node_role[tid] == 'target' + 入池/出池` | `signal_queue` | 角色判定 + 信号生成 | 每条边执行后立即生成 |

**（这部分不变）**

### 4.6 后处理层（PK排名/分析角度/看盘面板）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| PK排名 | `node_stocks[target] + latest_tick + pk_config` | `_pk_rankings` | 按权重评分排序 |
| 分析角度 | `node_stocks[target] + latest_tick + analysis_config` | `_angle_results` | 多维度计算 |
| 看盘面板 | `node_stocks + latest_tick + dashboard_schema` | `_dashboard_data` | 组装显示数据 |

**注意：后处理也可以复用全局 indicator_cache！**
- PK 排名用到的指标，如果 formula_registry 里已经有了，直接读
- 不用再算一遍
- 而且是全局共享的，其他边用的时候也能复用

---

## 五、表驱动：逻辑在表结构里，差异在表内容里（v1.6 更新版）

### 5.1 时间触发（timing.json）

**结构：** `{starttype: {rule, evaluator}, cxtype: {rule, evaluator}}`

**（不变）**

### 5.2 公式注册表（formula_registry.json）—— v1.6 新增核心表

**v1.6 新增：所有可用指标的统一注册表。**

```json
{
  "version": "1.0",
  "description": "公式注册表：所有可用指标的元数据，包括名称、参数、周期、输出字段",
  "formulas": [
    {
      "id": "ma",
      "name": "MA",
      "description": "移动平均线",
      "category": "trend",
      "type": "formula",
      "default_period": "1d",
      "args": [
        { "name": "N", "default": 5, "description": "周期" }
      ],
      "output_type": "scalar",
      "output_fields": ["MA"],
      "script": "MA:CLOSE,M1;"
    },
    {
      "id": "macd",
      "name": "MACD",
      "description": "指数平滑异同移动平均线",
      "category": "trend",
      "type": "formula",
      "default_period": "1d",
      "args": [
        { "name": "SHORT", "default": 12, "description": "短期EMA周期" },
        { "name": "LONG", "default": 26, "description": "长期EMA周期" },
        { "name": "MID", "default": 9, "description": "DEA的EMA周期" }
      ],
      "output_type": "multi",
      "output_fields": ["DIF", "DEA", "MACD"],
      "script": "DIF:EMA(CLOSE,SHORT)-EMA(CLOSE,LONG);\nDEA:EMA(DIF,MID);\nMACD:(DIF-DEA)*2;"
    },
    {
      "id": "close_price",
      "name": "收盘价",
      "description": "最新收盘价",
      "category": "price",
      "type": "scalar_field",
      "field": "close",
      "default_period": "tick",
      "output_type": "scalar",
      "output_fields": ["close"]
    },
    {
      "id": "pct_change",
      "name": "涨跌幅",
      "description": "涨跌幅百分比",
      "category": "price",
      "type": "derived",
      "expr": "(close - pre_close) / pre_close * 100",
      "default_period": "tick",
      "output_type": "scalar",
      "output_fields": ["pct_change"]
    }
  ]
}
```

### 5.3 比较算子集（comparison_operators.json）—— v1.6 新增核心表

**v1.6 新增：独立的比较算子，与指标正交。**

```json
{
  "version": "1.0",
  "description": "比较算子集：所有可用的比较操作符，独立于指标",
  "operators": [
    {
      "id": "gt",
      "name": "大于",
      "type": "scalar_compare",
      "input_type": "single",
      "params": ["threshold"],
      "description": "指标值大于阈值",
      "expr": "value > threshold"
    },
    {
      "id": "lt",
      "name": "小于",
      "type": "scalar_compare",
      "input_type": "single",
      "params": ["threshold"],
      "description": "指标值小于阈值",
      "expr": "value < threshold"
    },
    {
      "id": "eq",
      "name": "等于",
      "type": "scalar_compare",
      "input_type": "single",
      "params": ["threshold", "tolerance"],
      "description": "指标值近似等于阈值",
      "expr": "abs(value - threshold) < tolerance"
    },
    {
      "id": "golden_cross",
      "name": "金叉",
      "type": "cross",
      "input_type": "dual",
      "params": [],
      "direction": "above",
      "window": 2,
      "description": "指标1上穿指标2（金叉）",
      "prev_expr": "line1[-2] < line2[-2]",
      "curr_expr": "line1[-1] >= line2[-1]",
      "combine": "and"
    },
    {
      "id": "dead_cross",
      "name": "死叉",
      "type": "cross",
      "input_type": "dual",
      "params": [],
      "direction": "below",
      "window": 2,
      "description": "指标1下穿指标2（死叉）",
      "prev_expr": "line1[-2] > line2[-2]",
      "curr_expr": "line1[-1] <= line2[-1]",
      "combine": "and"
    },
    {
      "id": "turn_up",
      "name": "上拐",
      "type": "inflection",
      "input_type": "single",
      "params": [],
      "direction": "up",
      "window": 3,
      "description": "曲线由降转升",
      "expr": "line[-2] - line[-3] < 0 and line[-1] - line[-2] >= 0"
    },
    {
      "id": "turn_down",
      "name": "下拐",
      "type": "inflection",
      "input_type": "single",
      "params": [],
      "direction": "down",
      "window": 3,
      "description": "曲线由升转降",
      "expr": "line[-2] - line[-3] > 0 and line[-1] - line[-2] <= 0"
    },
    {
      "id": "rank_top",
      "name": "排名前N",
      "type": "rank",
      "input_type": "single",
      "params": ["n"],
      "order": "desc",
      "description": "按指标值降序取前N名"
    },
    {
      "id": "rank_bottom",
      "name": "排名后N",
      "type": "rank",
      "input_type": "single",
      "params": ["n"],
      "order": "asc",
      "description": "按指标值升序取前N名（即倒数后N名）"
    }
  ]
}
```

### 5.4 Filter 规格（filter_specs.json）—— v1.6 正交化版

**v1.6 更新：指标和比较正交，filter = 指标 × 比较算子。**

```json
{
  "version": "1.0",
  "description": "Filter 规格：指标 × 比较算子 = filter",
  "filters": {
    "price_gt_10": {
      "filter_type": "independent",
      "indicators": ["close_price"],
      "compare": {
        "operator": "gt",
        "indicator_ref": "close_price",
        "params": { "threshold": 10.0 }
      },
      "set_op": {
        "type": "single"
      }
    },
    "ma_golden_cross": {
      "filter_type": "independent",
      "indicators": ["ma_5", "ma_10"],
      "compare": {
        "operator": "golden_cross",
        "line1_ref": "ma_5",
        "line2_ref": "ma_10",
        "params": {}
      },
      "set_op": {
        "type": "single"
      }
    },
    "rank_top_10_pct": {
      "filter_type": "global",
      "indicators": ["pct_change"],
      "compare": null,
      "set_op": {
        "type": "rank",
        "operator": "rank_top",
        "indicator_ref": "pct_change",
        "params": { "n": 10 }
      }
    },
    "multi_condition_and": {
      "filter_type": "independent",
      "indicators": ["close_price", "ma_5"],
      "compare": {
        "type": "multi",
        "conditions": [
          { "operator": "gt", "indicator_ref": "close_price", "params": { "threshold": 10.0 } },
          { "operator": "gt", "indicator_ref": "ma_5", "indicator_ref2": "close_price", "params": {} }
        ]
      },
      "set_op": {
        "type": "logic",
        "op": "and",
        "conditions": [0, 1]
      }
    }
  }
}
```

**三层结构清晰对应：**
- 第一层（指标层）：`indicators` 字段 → 引用 formula_registry 的 ID
- 第二层（比较层）：`compare` 字段 → 引用 comparison_operators 的 ID
- 第三层（集合运算层）：`set_op` 字段 → 排名、逻辑组合、集合运算

### 5.5 传播模式（propagate_modes.json）

**结构：** `{mode_name: {op: fn, affects_source: bool}}`

| 模式 | 操作 | 影响源节点 |
|------|------|-----------|
| copy | target += passed | 否 |
| move | target += passed; source = [] | 是 |
| overwrite | target = passed | 否 |

**（不变，传播是边的属性，不是filter的属性）**

---

## 六、概念瘦身对照表（v1.5 → v1.6）

### 6.1 消除的概念/表

| v1.5 概念 | v1.6 处理 | 理由 |
|-----------|-----------|------|
| `stock_dirty[code][period]` | **删除**，功能由 `period_version[period]` + 缓存版本号 承接 | 脏标记有清脏时机bug；版本号更清晰，不会搞错 |
| "传播层"（filter第四层） | **删除**，传播是边的步骤不是filter层 | 传播是"动作"不是"计算"，filter只有三层就够了 |
| "组合层" | **改名**为"集合运算层" | "组合"太模糊，"集合运算"更准确 |
| `stock_state_version` | **删除**（如果之前算的话） | 用节点脏 + 状态变化检测就够了 |
| `edge_filter_cache` | **移出核心表**（可选优化） | 不算核心7张表，是性能优化项 |

### 6.2 保留并升级的概念

| v1.5 概念 | v1.6 升级 | 说明 |
|-----------|-----------|------|
| `period_ts[period]`（时间戳） | 升级为 `period_version[period]`（版本号） | 从时间戳变为单调递增整数，更清晰，和缓存版本号对比 |
| `indicator_cache[indicator_id][code]`（只有值） | 升级为 `{value, version}` | 每个缓存项带自己的版本号，和周期版本号对比 |
| `indicator_registry` | 改名为 `formula_registry` | 更准确——这是公式的注册表 |
| `edge_combine_spec` | 改名为 `edge_set_op_spec` | "组合层"→"集合运算层" |
| `dirty_nodes` | 保留 | 不变，节点脏驱动 |

### 6.3 新增的概念

| 概念 | 说明 | 为什么需要 |
|------|------|-----------|
| `period_version[period]` | 周期版本号水位线 | 替代脏标记，更清晰，不会搞错清脏时机 |
| `comparison_operators` | 比较算子集 | 比较算子独立，与指标正交，笛卡尔积设计 |
| `kline_cache` | K线数据层缓存 | K线合成独立，股票池引擎只读 |
| 集合运算层（set_op） | filter 第三层（原组合层改名） | 更准确，输入输出都是集合 |

### 6.4 概念数量统计

| 版本 | 核心概念数 | 变化 |
|------|-----------|------|
| v1.4 | ~15 个 | — |
| v1.5 | ~12 个 | -3 |
| v1.6 | **~10 个** | **-2（净减少）** |

**详细统计：**
- 删除：stock_dirty、传播层（作为filter层）、stock_state_version（3个）
- 新增：period_version、comparison_operators、kline_cache、集合运算层（改名不算新增）（3个）
- 改名：indicator_registry→formula_registry、组合层→集合运算层（2个，不影响数量）
- **净变化：0 个？不对，让我再数一下**

重新数：
- v1.5 的核心概念：latest_tick, period_ts, stock_dirty, indicator_cache, node_stocks, stock_state_version, dirty_nodes, edge_filter_cache, ttl_expiry_queue, indicator_registry, 比较层, 组合层, 传播层 → 13个
- v1.6 的核心概念：latest_tick, kline_cache, period_version, indicator_cache, node_stocks, dirty_nodes, ttl_expiry_queue, formula_registry, comparison_operators, 指标层, 比较层, 集合运算层, propagate（边的步骤）→ 13个？

不对，让我按"运行时核心表"来数更准确：

| 版本 | 核心运行时表数 | 变化 |
|------|---------------|------|
| v1.4 | 9 张 | — |
| v1.5 | 8 张 | -1 |
| v1.6 | **7 张** | **-1** |

v1.6 核心运行时表（7张）：
1. latest_tick
2. kline_cache
3. period_version
4. indicator_cache
5. node_stocks
6. dirty_nodes
7. ttl_expiry_queue

对，7张，比 v1.5 的 8 张少 1 张（删除了 stock_dirty，新增了 kline_cache，但是 v1.5 的 edge_filter_cache 移出核心表，所以净减 1）。

### 6.5 配置表数量统计

| 版本 | 核心配置表数 | 变化 | 说明 |
|------|-------------|------|------|
| v1.5 | ~10 张 | — | timing, filter_specs, edge_strategies, indicator_registry, 等 |
| v1.6 | **~10 张** | **±0** | 新增 formula_registry、comparison_operators，但合并了一些冗余表 |

---

## 七、实现路线图（v1.6）

### 阶段一：版本号机制替换脏标记（致命bug修复）

1. 新增 `period_version[period]` 表（替代 `stock_dirty` 和 `period_ts`）
2. 修改 `indicator_cache` 结构，每个缓存项带 `version` 字段
3. 数据更新时递增 `period_version[period]`（替代置脏）
4. 指标计算前对比版本号：`cache.version < period_version[period]` 就重算
5. 计算完后写入新版本号（没有"清脏"动作）
6. 删除 `stock_dirty` 相关代码

### 阶段二：K线数据层独立

1. 新增 `kline_cache[period][code]` 表
2. 新增 K线合成服务（独立于股票池引擎）
3. 股票池引擎只读 K线缓存，不负责合成
4. 数据更新走 K线数据层，版本号由 K线数据层维护

### 阶段三：公式注册表 + 比较算子正交化

1. 新增 `formula_registry.json` 配置表
2. 新增 `comparison_operators.json` 配置表
3. 修改编译期逻辑：指标从 formula_registry 来，比较从 comparison_operators 来
4. filter 规格改为"指标引用 + 算子引用 + 参数"的正交形式
5. 修改比较层实现：通用比较器，根据算子ID选择逻辑

### 阶段四：filter真瘦身（三层 + 传播是边的步骤）

1. "组合层"改名为"集合运算层"（`edge_combine_spec` → `edge_set_op_spec`）
2. 传播从 filter 第四层移出，改为边的独立步骤
3. 明确边执行步骤：gate → filter三层 → propagate → 发事件 → 目标节点脏
4. 更新所有文档和注释，概念统一

### 阶段五：优化与验证

1. 性能测试：对比 v1.5 和 v1.6 的性能（版本号机制应该更快，因为没有清脏逻辑）
2. 正确性验证：确保版本号机制正确，没有清脏时机bug
3. 代码清理：删除所有脏标记相关的冗余代码
4. 文档更新：所有文档统一使用 v1.6 的概念

---

## 八、统计总结（v1.5 → v1.6）

### 8.1 概念数量变化

| 统计项 | v1.5 | v1.6 | 变化 |
|--------|------|------|------|
| 核心运行时表 | 8 张 | **7 张** | **-1** |
| filter 层数 | 4 层 | **3 层** | **-1** |
| 核心概念（估算） | ~12 个 | ~10 个 | -2 |
| 致命bug | 1 个（清脏时机错误） | **0 个** | **修复** |

### 8.2 配置表数量变化

| 统计项 | v1.5 | v1.6 | 变化 |
|--------|------|------|------|
| 核心配置表（估算） | ~10 张 | ~10 张 | ±0 |
| 新增表 | — | formula_registry, comparison_operators | +2 |
| 合并/删除表 | — | （合并部分冗余表） | -2 |
| 净变化 | — | — | ±0 |

### 8.3 运行时表数量变化

| 统计项 | v1.5 | v1.6 | 变化 |
|--------|------|------|------|
| 核心运行时表 | 8 张 | **7 张** | **-1** |
| 删除表 | — | stock_dirty, stock_state_version, edge_filter_cache（移出核心） | -3 |
| 新增表 | — | kline_cache, period_version（替代period_ts） | +1（净） |

### 8.4 一句话总结

**v1.6 用版本号替代脏标记（彻底解决清脏时机致命bug），filter真瘦身到三层（指标→比较→集合运算，传播是边的步骤），公式注册表与比较算子正交化（笛卡尔积设计），K线数据层独立（引擎只读缓存不合成），概念更少更清晰，核心运行时表从8张减到7张。**
