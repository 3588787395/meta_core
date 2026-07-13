# 股票池深度重构规划 v1.4

> 核心洞察：指标缓存分层 + 脏标记等价性 + 前后端一体设计
> 设计原则：表驱动、数据驱动、事件驱动、增量优先、全量保底、前后端同构
> 目标：engine.py 从 3504 行 → ≤ 800 行，配置表从 50+ 张 → ≤ 12 张核心表，前后端数据结构统一

---

## v1.3 → v1.4 变更摘要：指标缓存分层 + 前后端一体设计

**变更日期：** 2026-07-01

**核心升级（基于三大新洞察）：**

| # | 变更项 | v1.3 | v1.4 | 本质变化 |
|---|--------|------|------|---------|
| 1 | **缓存粒度（核心洞察1）** | 缓存整个 filter 结果（bool） | 分层缓存：① 指标值（可复用） ② filter比较判断（独立型可复用） | 从"缓存结论"到"缓存计算过程"，指标值可被多个filter共享 |
| 2 | **filter 三层结构** | 一层：filter = 黑盒计算 | 三层：指标计算 → 比较判断 → 结果汇总 | 解耦指标纯函数与比较逻辑，指标层可缓存可共享 |
| 3 | **指标缓存表** | 无（指标计算嵌在filter里） | 新增 `indicator_cache`：公式名×股票代码 → 指标值+版本号 | 指标值可跨filter、跨边复用 |
| 4 | **脏标记方式（核心洞察2）** | 仅时间戳对比方式 | 两种等价方式：A)时间戳对比 B)写时置脏；说明等价性，推荐写时置脏 | 从"只读一种方式"到"两种等价，选实现简单的" |
| 5 | **排名型 filter 优化** | 全量重算（指标+排名一起算） | 指标值读缓存，只重算排名比较 | 排名型也能复用指标计算，只重算相对比较部分 |
| 6 | **前后端一体（核心洞察3）** | 只讲后端简化 | 前后端统一数据结构：综合设置=执行顺序、属性面板=节点参数、公式配置=filter规格 | 从"只改后端"到"前后端同构，一套配置两端用" |
| 7 | **运行时核心表** | 8 张 | 9 张（+ indicator_cache） | 新增指标结果缓存表 |
| 8 | **编译期产物** | edge_filter_type | + edge_indicator_spec（指标规格） + edge_compare_spec（比较规格） | filter规格拆成指标+比较两部分 |
| 9 | **前端规划** | 无（只关注后端） | 新增前端简化规划：三端统一配置模型、组件注册表、数据双向绑定 | 前后端一体设计，同步简化 |

**一句话总结 v1.4 升级：** 把 filter 拆成"指标计算（纯函数，可缓存）+ 比较判断（独立型可增量，排名型全量）+ 结果汇总"三层，新增 indicator_cache 指标缓存表实现跨filter共享，明确脏标记两种等价方式（时间戳对比/写时置脏），并将前后端设计统一为一套数据结构。

---

## 一、本质认知：股票池到底是什么（v1.4 深化版）

### 1.1 一句话本质（更新）

**股票池 = 一组节点 × 一组边 × 一个全局水位线 × 一堆股票版本号 × 一个指标缓存层 × 一个事件队列。**

- **节点**：装股票的容器（备选池/状态池/条件池/目标池）
- **边**：节点之间的连接，带触发条件（时间 + 过滤条件）
- **全局水位线**：`latest_tick_ts` —— 所有数据的最新时间戳（秒级精度）。水位线不涨，所有指标计算结果不变。
- **股票版本号**：每只股票自己的 `data_version`（数据版本）和每个节点里的 `state_version`（状态版本）
- **指标缓存层**：`indicator_cache` —— 公式名×股票代码 → 指标值+版本号。**指标是纯函数，输入不变则输出不变，可缓存可共享。**
- **事件队列**：`edge_pending` —— 待处理的边，按执行顺序排列。事件驱动，不是轮询。

### 1.2 核心洞察1：复用的是指标结果，不是 filter 结果

**这是 v1.4 最重要的认知升级。**

之前（v1.3）的理解：
- filter 是一个黑盒，输入股票，输出 bool（过/不过）
- 缓存整个 filter 结果（edge_filter_cache）
- 独立型 filter 可以增量，全局型不行

现在（v1.4）的深化理解：

```
filter 不是一层，是三层：

┌─────────────────────────────────────────────────────┐
│  filter 结果（bool / 排名）                         │
│  ┌───────────────────────────────────────────────┐  │
│  │  比较判断（>/</=/金叉/死叉/排名/...）          │  │
│  │  ┌─────────────────────────────────────────┐  │  │
│  │  │  指标计算（MA/MACD/RSI/涨幅/成交量...） │  │  │
│  │  │  纯函数：输入=tick+K线，输出=指标值      │  │  │
│  │  └─────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

**关键分层：**

| 层级 | 性质 | 输入 | 输出 | 可缓存性 | 可增量性 | 可共享性 |
|------|------|------|------|---------|---------|---------|
| **指标计算层** | 纯函数 | tick数据 + K线数据 | 指标值（可能多个值、多个列表） | ✅ 完全可缓存 | ✅ 股票级独立 | ✅ 跨filter/跨边共享 |
| **比较判断层** | 逻辑运算 | 指标值 + 阈值/条件 | bool / 排名结果 | ⚠️ 独立型可缓存 | ⚠️ 独立型可增量，排名型需全量 | ⚠️ 同规格可共享 |
| **结果汇总层** | 集合运算 | 各比较结果 | 最终通过的股票列表 | ❌ 动态的 | ❌ — | ❌ — |

**为什么指标是纯函数？**
- 输入固定：某只股票的 tick 数据 + K线历史数据
- 只要输入不变，输出一定不变
- 时间戳不变 = 输入不变 = 指标值不变
- 这是数学上的确定性，不是经验假设

**指标缓存的威力：**
- 同一只股票的 MA(5) 值，在 10 条边的 filter 里都用到，只算 1 次
- 排名型 filter 虽然排名要全量重算，但指标值可以直接读缓存
- 指标计算通常是 filter 里最耗时的部分（尤其是复杂公式、K线运算）
- 缓存指标 = 抓住了性能瓶颈的牛鼻子

### 1.3 运行时只有五件事（v1.4 更新版）

| # | 事情 | 触发条件 | 做什么 |
|---|------|---------|-------|
| 1 | **数据更新** | 外部行情推送 / K线到达 | 写 `latest_tick` 表，更新 `latest_tick_ts`，标记该股票"数据脏"，**源节点出边入队** |
| 2 | **TTL 过期处理** | tick 开始时检查过期队列 | 从 `ttl_expiry_queue` 弹出已过期股票，从节点移除，更新该股票在节点中的 `state_version`，**触发下游边** |
| 3 | **边触发 + 执行**（事件驱动） | 从 `edge_pending` 队列取边 | 检查三要素→满足就选策略→**先看指标缓存，缺的补算**→比较判断→propagate→目标节点更新→**立即发事件**→标记目标节点中变化的股票→目标节点出边入队 |
| 4 | **无条件边立即传播**（同步） | 源节点股票状态变化时立即触发 | 直接 propagate，不走 gate，不走 filter，立即更新目标节点，立即发事件 |
| 5 | **指标缓存维护** | 数据更新时 / 指标计算后 | 数据更新时标记脏 / 计算完后写入缓存并清脏 / 过期自动淘汰 |

**正确的因果链（v1.4 深化版）：**

```
数据更新（tick_ts 涨了）
  ↓
指标输入变了 → 指标值可能变了 → 指标缓存标记为脏
  ↓
filter 的比较判断可能变了（因为指标值可能变了）
  ↓
需要重新计算 filter（但指标值能读缓存的先读，只算缺的）
  ↓
股票入池 / 出池
  ↓
节点状态变化
  ↓
触发下游边
```

**v1.3 的不足：** 缓存的是整个 filter 结果（bool），粒度太粗。
- 同一只股票的同一个指标，在不同 filter 里要重复计算
- 排名型 filter 完全不能缓存，每次全量重算指标

**v1.4 的改进：** 缓存粒度下沉到指标层。
- 指标值一次计算，多处复用
- 排名型 filter 也能复用指标计算，只重算排名
- 性能提升更显著

### 1.4 核心洞察2：脏标记的两种等价方式

脏标记的本质是回答一个问题：**这只股票的指标值需要重算吗？**

有两种完全等价的实现方式：

#### 方式 A：时间戳对比（读时判断）

```
每只股票的指标缓存记录：
  indicator_cache[formula][code] = {
    value: ...,      // 指标值
    data_ts: 12345,  // 计算时的数据版本号（即当时的 latest_tick_ts）
  }

需要重算吗？
  → 比较：latest_tick_ts > indicator_cache[formula][code].data_ts
  → 大于：数据更新过，需要重算
  → 等于：数据没变，直接用缓存
```

**特点：**
- 写数据时什么都不做（只更新 latest_tick_ts 和 stock_data_version）
- 读缓存时才判断是否过期
- 逻辑简单，但每次读都要比一次时间戳

#### 方式 B：写时置脏（写时标记）

```
每只股票有一个 dirty 标志集合：
  dirty_indicators[code] = set()  // 哪些公式可能脏了

或者更简单：
  stock_dirty[code] = True/False  // 这只股票数据脏不脏

数据更新时：
  → 写 latest_tick[code] = new_bar
  → stock_dirty[code] = True  // 直接置脏

计算指标时：
  → 如果 stock_dirty[code] == True：重算，算完设为 False
  → 如果 stock_dirty[code] == False：直接读缓存
```

**特点：**
- 写数据时多做一件事（置脏标记）
- 读缓存时不用比时间戳，直接看标志位
- 更直观，性能略好（省了时间戳比较）

#### 两种方式等价性证明

```
方式 A（时间戳对比）的"脏"定义：
  tick_ts > cache_ts → 脏

方式 B（写时置脏）的"脏"定义：
  数据更新后置脏，计算完清脏 → 脏

等价性：
  方式 A 中，cache_ts 是"上次计算时的 tick_ts"
  方式 B 中，清脏动作发生在"计算完时"
  
  数据更新 → 方式A: tick_ts > cache_ts（变脏）
           → 方式B: 置脏标记（变脏）
  
  计算完 → 方式A: cache_ts = tick_ts（变干净）
         → 方式B: 清脏标记（变干净）
  
  结论：两种方式在逻辑上完全等价，只是实现手段不同。
```

**推荐选择：写时置脏（方式 B）**
- 代码更直观，"脏"的概念更清晰
- 省掉每次读缓存时的时间戳比较
- 与现有代码中的 `_data_dirty` 概念一致（engine.py:466）
- 实现更简单

### 1.5 为什么之前代码又臭又长（补充前后端视角）

后端层面的问题（v1.3 已讲）：
- 每 tick 都重算拓扑序 → 错！拓扑运行前就定了
- 每 tick 都重新解析边参数 → 错！边参数运行前就编译好了
- 每 tick 都遍历所有节点所有边 → 错！只有 pending 队列里的边才需要检查
- 每 tick 都把所有股票过一遍 filter → 错！独立型只算数据变了的，全局型才全量
- 数据更新和过滤计算混在一起 → 错！数据层和计算层必须分离
- 指标计算嵌在 filter 里 → 错！指标是独立的纯函数层，可缓存可共享
- TTL 每 tick 检查所有股票 → 错！用过期队列，事件驱动
- 用 hash 比较判断数据变没变 → 错！时间戳本身就是版本号
- 所有 filter 都用同一种策略 → 错！先判断类型，再选增量/全量

**前端层面的问题（v1.4 新增）：**
- 前端一套数据结构，后端一套数据结构 → 错！应该共享同一份
- 综合设置表格的行顺序 ≠ 后端执行顺序 → 错！应该就是执行顺序
- 属性面板的字段 ≠ 后端节点参数 → 错！应该一一对应
- 公式配置界面的字段 ≠ 后端 filter 规格 → 错！应该就是同一份规格
- 前端硬编码很多业务逻辑 → 错！应该由配置表驱动
- 前后端各维护一份类型映射 → 错！应该共用一张注册表

---

## 二、核心设计：三层 filter + 指标缓存 + 双等价脏标记 + 前后端同构

### 2.1 运行时内存表（核心运行时表一共 9 张，+1 indicator_cache）

| 表名 | 类型 | 读时机 | 写时机 | 说明 |
|------|------|--------|--------|------|
| `latest_tick` | Dict[code → bar_dict] | 指标计算时读 | 行情推送时写 | **唯一真相源**。所有股票的最新tick数据 |
| `latest_tick_ts` | float | 水位线比较时读 | 行情推送时写 | **全局时间水位线（秒级）**。只要这个值不变，所有指标计算结果都不变 |
| `stock_data_version` | Dict[code → float] | 增量筛选时读 | 该股票行情推送时写 | **每只股票的数据版本** = 最后一次数据更新时的水位线（或用 dirty 标志替代，见 2.3 节） |
| `indicator_cache` | Dict[formula_key → Dict[code → indicator_result]] | 指标计算前先查 | 指标计算完后写 | **v1.4 新增：指标结果缓存**。公式名×股票代码 → 指标值+版本号/脏标记 |
| `node_stocks` | Dict[nid → List[code]] | propagate 读写、filter 读 | 边执行/TTL过期后写 | 各节点当前股票列表 |
| `stock_state_version` | Dict[nid → Dict[code → float]] | 状态变化判断时读 | 股票入池/出池时写 | **每只股票在每个节点的状态版本** = 最后一次状态变化时的水位线 |
| `edge_pending` | List[eid]（按执行顺序） | 执行循环读 | 节点股票状态变化时追加 / 数据更新时追加 | **边待处理队列**。需要检查的边，按执行顺序排列，去重 |
| `edge_filter_cache` | Dict[eid → Dict[code → bool]] | 增量 filter 比较判断时读 | filter 比较后写 | **过滤结果缓存（比较层）**。每只股票在每条边的上次比较结果（独立型用） |
| `edge_last_data_ts` | Dict[eid → float] | 增量筛选/全量判断时读 | 边执行完后写 | **每条边的数据处理水位线** = 最后一次处理时的全局水位线 |
| `ttl_expiry_queue` | Heap[(expire_ts, nid, code)] | tick 开始时弹出 | 股票入池时插入 | **TTL 过期队列**。按过期时间排序的最小堆 |

**就这九张核心运行时表。** 其他的都是运行前编译产物或配置表。

**v1.4 新增 indicator_cache 的详细结构：**

```python
indicator_cache = {
    # key = 公式唯一标识（公式内容hash + 参数 + 周期）
    "MA_5_1d": {
        "600519": {
            "value": 1800.5,           # 标量值（单输出）
            "data_ts": 1234567890.0,   # 计算时的数据版本
            # 或者用脏标记方式：
            # "dirty": False,
        },
        "000001": {
            "value": {                 # 多输出（如 MACD 的 DIF/DEA/MACD）
                "DIF": 1.2,
                "DEA": 0.8,
                "MACD": 0.4,
            },
            "data_ts": 1234567890.0,
        },
        # ...
    },
    "MACD_12_26_9_1d": { ... },
    # ...
}
```

**indicator_cache 的 key 怎么生成？**
- 公式文本的 hash（或公式ID） + 参数 + 周期
- 编译期预计算好，存入 edge_indicator_spec
- 运行时直接用，不用每次拼 key

**编译期新增（v1.4）：**

| 编译产物 | 类型 | 说明 |
|----------|------|------|
| `edge_filter_type[eid]` | `'independent'` / `'global'` | filter 类型：单股独立型 / 全局依赖型 |
| `edge_indicator_spec[eid]` | list[indicator_spec] | **v1.4 新增：指标计算规格列表**（一条边可能用到多个指标） |
| `edge_compare_spec[eid]` | compare_spec | **v1.4 新增：比较判断规格**（怎么比、和谁比、排名规则等） |

### 2.2 Filter 三层结构详解（v1.4 核心新增）

#### 第一层：指标计算（Indicator Layer）

**性质：纯函数，完全可缓存，股票级独立，可跨 filter 共享。**

**输入：**
- 某只股票的 tick 数据（latest_tick[code]）
- 某只股票的 K线历史数据（kline_cache[code][period]）
- 公式参数（如 MA 的周期 N，MACD 的 SHORT/LONG/MID）

**输出：**
- 标量值（单输出，如 MA、收盘价、涨幅）
- dict 多输出（如 MACD 的 DIF/DEA/MACD，KDJ 的 K/D/J）
- list/tuple 时间序列（如果需要历史值做比较判断）

**为什么是纯函数？**
- 输入完全决定输出
- 没有副作用
- 同一输入多次调用，结果一定相同
- 数据版本号不变 → 输入不变 → 输出不变

**代码中的对应实现：**
- `formula_engine.py` 的 `PythonFormulaEngine.eval()` —— 输入 bars，输出指标值
- `evaluators.py` 的 `eval_formula_nset()` 里调用 `formula_router.eval_batch()`

#### 第二层：比较判断（Comparison Layer）

**性质：逻辑运算，独立型可缓存可增量，排名型需全量。**

**输入：**
- 指标值（从 indicator_cache 读）
- 比较规则（>/</=/金叉/死叉/排名前N/...）
- 阈值/参数

**输出：**
- bool（通过/不通过，独立型）
- 排名结果（排名前N的股票列表，全局型）

**分类：**

| 比较类型 | 性质 | 可增量 | 例子 |
|---------|------|--------|------|
| **独立比较** | 单股独立，只和自己比 | ✅ 股票级增量 | 价格>10、涨幅>5%、MACD>0、成交量>1000万 |
| **穿越比较** | 需要历史值，但是单股独立 | ✅ 股票级增量（需要指标的时间序列） | 金叉、死叉、上拐、下拐 |
| **排名比较** | 截面比较，依赖所有股票 | ❌ 必须全量重算排名 | 涨幅前10、成交量前50、排名前N、排名后N |
| **集合运算** | 多个条件的组合 | ⚠️ 取决于子条件 | 交集、并集、差集（nset=5） |

**注意：排名比较也要读指标缓存！**
- 指标值还是从 indicator_cache 读（能省则省）
- 只有排名这一步需要全量做（因为排名是相对的）
- 指标计算通常是最耗时的，排名只是排序，很快

**代码中的对应实现：**
- `evaluators.py` 的 `_scalar_compare()` —— 标量比较
- `evaluators.py` 的 `_apply_noperate()` —— 带向量的比较（金叉/死叉等）
- `evaluators.py` 的 `_resolve_rank()` —— 排名处理

#### 第三层：结果汇总（Aggregation Layer）

**性质：集合运算，动态的，不缓存。**

**输入：**
- 各比较判断的结果
- 逻辑组合规则（AND/OR/NOT）
- propagate 模式（copy/move/overwrite）

**输出：**
- 最终通过 filter 的股票列表
- propagate 后的目标节点股票列表

**为什么不缓存？**
- 结果汇总依赖多个条件的组合
- 任何一个条件变了，结果就可能变
- 缓存价值不大，而且组合爆炸（所有可能的条件组合都缓存？不现实）
- 汇总本身很简单（集合运算，很快）

**代码中的对应实现：**
- `evaluators.py` 的 `eval_nset5_set_operation()` —— 集合运算
- `engine.py` 的 propagate 逻辑 —— 传播模式

### 2.3 脏标记的两种实现方式（等价，选简单的）

#### 方式 A：时间戳对比（读时判断）

```python
# 数据更新时
def on_tick(code, bar):
    ts = floor(bar['datetime'])
    latest_tick[code] = bar
    if ts > latest_tick_ts:
        latest_tick_ts = ts
    stock_data_version[code] = ts  # 更新这只股票的数据版本
    # ... 边入队等

# 取指标时
def get_indicator(formula_key, code):
    cache = indicator_cache.get(formula_key, {})
    entry = cache.get(code)
    
    if entry and entry['data_ts'] >= latest_tick_ts:
        # 数据版本没变，缓存有效
        return entry['value']
    else:
        # 需要重算
        value = compute_indicator(formula_key, code)
        cache[code] = {
            'value': value,
            'data_ts': latest_tick_ts,
        }
        return value
```

#### 方式 B：写时置脏（写时标记）

```python
# 数据更新时
def on_tick(code, bar):
    ts = floor(bar['datetime'])
    latest_tick[code] = bar
    if ts > latest_tick_ts:
        latest_tick_ts = ts
    stock_dirty[code] = True  # 直接置脏
    # ... 边入队等

# 取指标时
def get_indicator(formula_key, code):
    cache = indicator_cache.get(formula_key, {})
    entry = cache.get(code)
    
    if entry and not stock_dirty[code]:
        # 数据不脏，缓存有效
        return entry['value']
    else:
        # 需要重算
        value = compute_indicator(formula_key, code)
        cache[code] = {
            'value': value,
            'dirty': False,
        }
        stock_dirty[code] = False  # 算完清脏
        return value
```

#### 等价性说明

两种方式在逻辑上完全等价：
- 方式 A 的 `data_ts >= latest_tick_ts` ≡ 方式 B 的 `not stock_dirty[code]`
- 数据更新时，A 更新 `data_ts`，B 置 `dirty=True`
- 计算完后，A 更新 `data_ts = latest_tick_ts`，B 设 `dirty=False`

**推荐方式 B（写时置脏），原因：**
1. 代码更直观，"脏"的概念更符合直觉
2. 省掉每次读缓存时的时间戳比较（虽然开销很小，但能省则省）
3. 与现有代码中的 `_data_dirty` 概念一致（engine.py:466）
4. 容易扩展：以后可以支持"部分脏"（某几个指标脏，其他不脏）
5. 调试更方便：直接看 dirty 标志就知道要不要重算

**注意：方式 B 中，stock_dirty[code] 是股票级的，不是指标级的。**
- 一只股票的数据更新了，它的所有指标都可能变（因为指标都依赖这只股票的数据）
- 所以股票级 dirty 就够了，不用每个指标单独记
- 简单，够用

### 2.4 边触发机制：三要素 AND（v1.3 基础上补充指标层）

**三要素不变（v1.3 已确定）：**

```
边执行 = 时间条件满足 
       AND 源节点有变化（股票增减 OR 数据版本更新）
       AND 数据版本 > 边上次处理版本
```

**v1.4 补充：边执行时的指标计算流程**

```
边执行开始：
  ↓
先收集这条边用到的所有指标（从 edge_indicator_spec 读）
  ↓
对源节点的每只股票：
  → 查 indicator_cache，哪些指标已经有缓存且不脏
  → 缺的/脏的，批量补算
  → 算完写入 indicator_cache，清脏
  ↓
所有指标值都准备好了（不管是缓存的还是新算的）
  ↓
进入比较判断层：
  → 独立型：只比较数据变了的股票，其他读 edge_filter_cache
  → 排名型：全量读指标缓存值，做排名
  ↓
结果汇总 → propagate → 发事件 → 触发下游
```

**性能提升路径：**

| 场景 | v1.3（缓存filter结果） | v1.4（缓存指标值） | 提升 |
|------|----------------------|-------------------|------|
| 单条独立型 filter | 增量重算 filter | 增量重算指标+增量比较 | 差不多 |
| 多条边共用同一指标 | 每条边各算一遍 | 算一次，多处复用 | 大幅提升（n 条边省 n-1 次） |
| 排名型 filter | 全量重算（指标+排名） | 指标读缓存，只重算排名 | 大幅提升（省了指标计算） |
| 穿越型 filter（金叉等） | 全量重算 | 指标读缓存（含历史值），只重算穿越判断 | 大幅提升 |

### 2.5 核心洞察3：前后端一体设计

**这是 v1.4 新增的重要维度。** 之前只考虑后端简化，现在要前后端一起考虑。

#### 为什么要前后端一体？

**现状的问题：**
- 前端一套数据模型，后端一套数据模型
- 两边各维护一份类型映射、字段定义、验证规则
- 修改配置要改两端，容易不一致
- 前端硬编码很多业务逻辑，配置表驱动不彻底

**一体设计的目标：**
- 前后端共享同一份配置数据结构
- 前端的界面 = 后端配置的可视化
- 前端的操作 = 后端配置的修改
- 一套配置，两端通用

#### 三个核心对应关系

```
┌─────────────────────┐          ┌─────────────────────┐
│     前端（UI）      │          │    后端（Engine）   │
├─────────────────────┤          ├─────────────────────┤
│ 综合设置表格的行    │    ≡     │ 边执行顺序          │
│ （行号=执行序号）   │          │ （edge_order）      │
├─────────────────────┤          ├─────────────────────┤
│ 节点属性面板的字段  │    ≡     │ 节点参数字典        │
│ （data_path 映射）  │          │ （node.params）     │
├─────────────────────┤          ├─────────────────────┤
│ 公式配置界面的控件  │    ≡     │ filter 规格         │
│ （公式+参数+比较）  │          │ （indicator_spec    │
│                     │          │  + compare_spec）   │
└─────────────────────┘          └─────────────────────┘
```

#### 1. 综合设置表格 ≡ 后端执行顺序

**前端（ComprehensiveSettings）：**
- 表格的每一行对应一条边
- 行顺序 = 执行顺序
- 用户拖拽调整行顺序 = 调整执行顺序
- 每行显示：条件名、属性、时序操作

**后端（CompiledPool.edge_order）：**
- `edge_order` 列表就是执行顺序
- 按这个顺序串行执行边
- 拓扑排序只是用来校验合法性和画界面的，不是执行顺序

**统一数据结构：**
```json
{
  "execution_order": [
    { "edge_id": "e1", "label": "涨幅>5%", "source": "备选池", "target": "状态池1" },
    { "edge_id": "e2", "label": "MACD金叉", "source": "状态池1", "target": "目标池" }
  ]
}
```

**前端怎么用？** → 直接渲染成表格，行顺序就是 execution_order 的顺序
**后端怎么用？** → 直接按 edge_order 顺序执行

#### 2. 节点属性面板 ≡ 后端节点参数

**前端（TableDrivenPanel）：**
- 属性面板的每个字段对应一个参数
- `data_path` 就是参数在 node.params 里的路径
- 字段类型（text_input/select/flag_group/...）由配置表决定
- 验证规则（required/min/max/pattern）由配置表决定

**后端（node.params）：**
- 节点参数就是一个 dict
- key 对应前端的 data_path
- 值就是用户配置的值

**统一数据结构：**
```json
{
  "node": {
    "id": "n1",
    "type": "stock_state_pool",
    "label": "状态池1",
    "params": {
      "hold": 60,
      "attr": 1234,
      "tdx_psatt": { ... }
    }
  }
}
```

**前端怎么用？** → PanelGenerator 根据 node_type 查布局配置，生成面板，data_path 绑定 params
**后端怎么用？** → 直接读 node.params[key]

#### 3. 公式配置界面 ≡ 后端 filter 规格

**前端（FormulaEditor + 条件配置）：**
- 公式选择 / 公式编辑
- 参数配置（如 MACD 的 SHORT/LONG/MID）
- 比较方式选择（>/</=/金叉/死叉/排名前N）
- 阈值设置

**后端（edge_indicator_spec + edge_compare_spec）：**
- indicator_spec：用什么公式、什么参数、什么周期
- compare_spec：怎么比较、和谁比、排名规则

**统一数据结构：**
```json
{
  "filter_spec": {
    "filter_type": "independent",
    "indicators": [
      {
        "formula": "MA",
        "period": "1d",
        "args": { "N": 5 }
      }
    ],
    "comparison": {
      "operator": "gt",
      "threshold": 10.0,
      "indicator_ref": 0
    }
  }
}
```

**前端怎么用？** → 渲染公式选择器 + 参数表单 + 比较方式选择器
**后端怎么用？** → 按 indicator_spec 算指标，按 compare_spec 做比较

#### 前后端同构的实现路径

**第 1 步：统一配置表来源**
- 后端的 `ui_layouts.json` / `cell_type_registry.json` / `field_definitions.json`
- 前端通过 API 获取，或直接打包静态资源
- 两端用同一份配置

**第 2 步：统一数据模型**
- pool_config 的结构前后端一致
- 节点/边的 params 结构前后端一致
- filter_spec 的结构前后端一致

**第 3 步：前端完全表驱动**
- 前端不硬编码业务逻辑
- 所有组件类型、字段、验证规则都从配置表来
- （现在 panel.js 已经是表驱动的了，很好！）

**第 4 步：双向绑定自动同步**
- 前端修改属性 → 自动更新 data_path 对应的值 → 自动同步到后端
- 后端配置变更 → 推送到前端 → 面板自动刷新
- （现在 panel.js 已有 DataBinder，基础很好）

---

## 三、运行前 vs 运行时：严格分离（v1.4 更新版）

### 3.1 运行前（设计时 + 加载时）做的事

全部一次性做完，运行时只读。

| 阶段 | 做什么 | 产出 |
|------|--------|------|
| **设计时** | 用户拖拽节点、连线、配置参数、设置执行顺序 | pool_config (JSON) |
| **加载时** | 解析 pool_config，编译成运行时可用的结构，**拆分 filter 为指标+比较两层**，判断 filter 类型 | CompiledPool |

**CompiledPool 是什么？** 就是把运行时需要的所有信息都预先算好，编好索引，排好顺序。运行时直接用，不再解析。

```python
CompiledPool = {
    # 节点
    'nodes': {nid: node_dict},           # 节点字典，按id查
    'node_type': {nid: type_name},       # 节点类型，快速判定
    
    # 边
    'edges': {eid: edge_dict},           # 边字典
    'edge_endpoints': {eid: (sid, tid)}, # 边的端点，预解析
    'edge_order': [eid1, eid2, ...],     # **综合设置行顺序 = 执行顺序**，不是拓扑序！
    'edge_type': {eid: 'conditional' | 'unconditional'},  # 边类型
    
    # v1.4 新增：filter 拆成三层后的规格
    'edge_indicator_spec': {eid: [spec, ...]},   # 指标计算规格列表
    'edge_compare_spec': {eid: spec},            # 比较判断规格
    'edge_filter_type': {eid: 'independent' | 'global'},  # filter 类型
    
    'edge_timing_spec': {eid: spec},     # 时间触发条件的编译结果
    'edge_propagate_spec': {eid: spec},  # 传播模式的编译结果
    'edge_ttl_spec': {eid: spec},        # TTL 淘汰规则的编译结果
    
    # 邻接表
    'out_edges': {nid: [eid, ...]},      # 节点的出边
    'in_edges': {nid: [eid, ...]},       # 节点的入边
    
    # 反向索引：股票在哪些节点里（数据更新时快速找源节点）
    # 注意：这个是运行时动态维护的，不是编译期的
    # 'code_to_nodes': {code: [nid, ...]}  # 运行时维护
    
    # 源节点列表（入度为0的节点）
    'source_nodes': [nid, ...],
    
    # 角色映射（哪些是目标池、哪些是备选池）
    'node_role': {nid: 'candidate' | 'state' | 'condition' | 'target' | 'discard'},
    
    # 条件边列表（数据更新时需要考虑的边）
    'conditional_edges': [eid, ...],
}
```

**关键：执行顺序 = 综合设置表格的行顺序，不是拓扑排序的结果。**

综合设置表格的每一行就是一个计算单元（一条边），行号就是执行序号。用户在综合设置里拖拽调整行顺序，就是调整执行顺序。每条边对应一行，每行对应一条边。

加载时按表格行顺序编译得到 `edge_order` 列表，运行时就按这个顺序串行执行。拓扑只是用来画界面和校验合法性的，不是执行顺序。

### 3.2 运行时做的事（事件驱动 + 三层 filter + 指标缓存 + 混合策略）

就一个循环（**事件驱动 + 流式串行执行 + 指标缓存 + 增量/全量混合策略**）：

```
1. 等数据更新（或时间步进）
2. 新 tick 来了吗？
   → 没来：去步骤 3
   → 来了：
       写 latest_tick[code] = new_bar
       ts = floor(new_bar['datetime'])  # 秒级精度
       
       如果 ts > latest_tick_ts:
           latest_tick_ts = ts              # 全局水位线涨了
       
       # v1.4：写时置脏（方式B）
       stock_dirty[code] = True             # 这只股票数据脏了
       
       # 关键：数据更新 → 源节点出边入队
       找出包含这只股票的所有节点 code_nodes[code]
       对于每个节点 nid in code_nodes:
           把 nid 的所有条件出边加入 edge_pending（去重）
           （无条件边不在这里处理，它们只在状态变化时立即执行）
3. 处理 TTL 过期：
   从 ttl_expiry_queue 弹出所有 expire_ts <= now 的股票
   → 从对应节点 node_stocks[nid] 移除
   → 更新 stock_state_version[nid][code] = latest_tick_ts
   → 触发 _on_node_stocks_changed(nid, changed_codes)
4. 处理 edge_pending 队列（按执行顺序）：
   while edge_pending 不为空：
      取出队首边 eid（按执行顺序最靠前的）
      sid, tid = edge_endpoints[eid]
      
      如果是无条件边：
         → 已经在 _on_node_stocks_changed 时立即处理过了，跳过
         （无条件边不进 pending 队列，这里是防御性检查）
      
      如果是条件边：
         a. 检查三要素：
            ① 时间触发条件满足吗？
               → 不满足：跳过（下次 tick 再说，或者等下一次入队）
            ② 数据版本更新了吗？
               latest_tick_ts > edge_last_data_ts[eid] 吗？
               → 不大于：数据没变，跳过
            ③ 源节点有变化吗？
               （股票增减 OR 数据更新，这个由入队机制保证，能进队列说明有变化）
               → 理论上能到这一步，说明有变化，继续
         
         b. === 第一层：指标计算（读缓存 + 补算） ===
            indicator_specs = edge_indicator_spec[eid]  # 这条边用到的所有指标
            source_codes = node_stocks[sid]             # 源节点所有股票
            
            对每个指标 spec in indicator_specs:
                formula_key = spec.cache_key  # 编译期预计算的缓存key
                
                # 找出需要重算的股票（脏的 + 还没算过的）
                need_compute = [code for code in source_codes
                                if stock_dirty.get(code, False) 
                                   or code not in indicator_cache[formula_key]]
                
                if need_compute:
                    # 批量计算这些股票的指标值
                    values = compute_indicator_batch(spec, need_compute, latest_tick)
                    
                    # 写入缓存
                    for code, val in zip(need_compute, values):
                        indicator_cache[formula_key][code] = {
                            'value': val,
                            'dirty': False,
                        }
                        stock_dirty[code] = False  # 算完清脏
            
            # 此时，所有股票的所有指标值都准备好了（缓存的 + 新算的）
            
         c. === 第二层：比较判断（独立型增量，排名型全量） ===
            filter_type = edge_filter_type[eid]
            compare_spec = edge_compare_spec[eid]
            
            如果 filter_type == 'independent'（独立型）：
               → 走增量比较
                  i. 增量筛选：找出源节点中需要重算比较的股票
                     dirty_codes = [code for code in source_codes
                                   if stock_data_version[code] > edge_last_data_ts[eid]]
                     加上新入池还没算过的：
                     new_codes = [code for code in source_codes
                                 if code not in edge_filter_cache[eid]]
                     need_compare = dirty_codes + new_codes
                     
                     → 如果 need_compare 为空：
                         真的什么都不用算，跳过（全用缓存）
                         但是等等——源节点股票可能变少了（出池了）
                         这种情况 passed_codes 会自然减少
                         所以还是要走一遍 propagate 和对比
                     
                  ii. 增量比较：
                      - 对 need_compare 中的股票，从 indicator_cache 读指标值
                      - 用 compare_spec 做比较判断
                      - 结果写入 edge_filter_cache[eid][code]
                      - 其他股票直接从 edge_filter_cache[eid][code] 读
                      - 汇总所有通过 filter 的股票 = passed_codes
            
            否则（global，全局依赖型 / 排名型）：
               → 走全量比较（但指标值读缓存！）
                  i. 把源节点所有股票都拉出来
                     all_codes = node_stocks[sid]
                  ii. 从 indicator_cache 批量读所有股票的指标值
                      （指标值都是最新的，上一步已经补算过了）
                  iii. 全量比较（排名/排序/截面比较等）
                      - 结果写入 edge_filter_cache[eid]（整个覆盖）
                      - 汇总通过的 = passed_codes
         
         d. === 第三层：结果汇总 + propagate ===
            propagate 传播 → 更新目标节点 node_stocks[tid]
            对比目标节点变化，得到 entered_codes 和 exited_codes
         
         e. 如果有变化：
              - 更新 stock_state_version[tid][code] = latest_tick_ts
                （对所有 entered_codes 和 exited_codes）
              - 把 tid 的所有出边加入 edge_pending（去重）
              - 处理无条件边：立即同步 propagate
              - **立即发射入池/出池事件**
         f. 更新 edge_last_data_ts[eid] = latest_tick_ts
5. 回去等下一次数据更新
```

**v1.4 核心循环的关键变化（相对于 v1.3）：**

1. **filter 三层化**：指标计算 → 比较判断 → 结果汇总，每层职责清晰
2. **指标缓存层**：新增 indicator_cache，指标值跨 filter 跨边复用
3. **写时置脏**：数据更新时直接设 stock_dirty[code] = True，算完清脏
4. **排名型也能读缓存**：指标值从缓存读，只重算排名比较
5. **脏标记等价性**：明确两种方式等价，推荐写时置脏

**指标缓存的共享性：**
- 假设有 5 条边都用到 MA(5)
- v1.3：每条边各算一遍，共算 5 次
- v1.4：第 1 条边算完写入缓存，后面 4 条边直接读缓存，共算 1 次
- 性能提升：5x（指标计算是瓶颈的话）

---

## 四、功能-表操作对应表（v1.4 更新版）

### 4.1 数据层（最新tick表 + 指标缓存层）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| 行情推送 | — | `latest_tick[code] = new_bar` + `stock_dirty[code] = True` | **v1.4：写时置脏**，无 hash 计算；秒级精度，同秒内视为同一批 |
| 水位线更新 | `new_bar['datetime']` | `latest_tick_ts = max(latest_tick_ts, ts)` | 比较时间戳大小，取最大值 |
| **数据更新触发边入队** | `code_nodes[code]` + `out_edges[nid]` + `edge_type[eid]` | `edge_pending` 追加（去重） | 股票有新数据 → 所在节点的条件出边入队 |
| **指标缓存查询** | `indicator_cache[formula_key][code]` + `stock_dirty[code]` | — | **v1.4 新增**：不脏就用缓存，脏了就重算 |
| **指标缓存写入** | 计算结果 | `indicator_cache[formula_key][code]` + `stock_dirty[code] = False` | **v1.4 新增**：算完写入缓存并清脏 |

**关键变化（v1.3 → v1.4）：**
- 新增 indicator_cache 表（指标结果缓存）
- 脏标记从"时间戳对比"改为"写时置脏"（两种等价，推荐后者）
- 指标计算从 filter 内部剥离，成为独立的一层

### 4.2 TTL 淘汰层（事件驱动，非轮询）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| 股票入池记录 TTL | `edge_ttl_spec[eid]` | `ttl_expiry_queue` 插入 `(expire_ts, nid, code)` | `expire_ts = now + ttl_sec` |
| TTL 过期检查 | `ttl_expiry_queue + now` | 弹出过期项 | 最小堆：堆顶过期就弹出，直到堆顶未过期 |
| 过期股票移除 | `node_stocks[nid]` | `node_stocks[nid]` + `stock_state_version[nid][code]` | 从节点移除过期股票，更新该股票的状态版本 |
| 过期触发级联 | `stock_state_version[nid][code]` | `edge_pending` + 无条件边立即执行 | 源节点股票状态变了 → 出边入队 + 无条件边立即 propagate |

**（这部分 v1.3 已经很好，v1.4 不变）**

### 4.3 边触发判定层（双触发源 + 三要素 AND）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **数据更新 → 边入队** | `code_nodes[code]` + `out_edges[nid]` + `edge_type[eid]` | `edge_pending` 追加（去重） | 股票有新数据 → 所在节点的条件出边入队 |
| 节点股票状态变化 → 边入队 | `out_edges[sid] + edge_type[eid]` | `edge_pending` 追加（去重） | 源节点出边中，条件边入队；无条件边立即执行 |
| 边时间触发检查 | `edge_timing_spec[eid] + latest_tick_ts + _flow_state[eid]` | — | `starttype × cxtype` 的 24 种判定 |
| **三要素检查** | `latest_tick_ts` + `edge_last_data_ts[eid]` | — | 时间条件 AND 数据版本更新 AND 源节点有变化 |
| **指标缓存有效性检查** | `indicator_cache[formula_key]` + `stock_dirty[code]` | — | **v1.4 新增**：stock_dirty[code] 为 False 则缓存有效 |
| 增量筛选需要重算的股票（独立型比较） | `stock_data_version[code] + edge_last_data_ts[eid] + node_stocks[sid] + edge_filter_cache[eid]` | — | `need_compare = (data_version > edge_last_data_ts) OR (code not in cache)` |
| 全量判断（全局依赖型比较） | `latest_tick_ts` + `edge_last_data_ts[eid]` | — | `latest_tick_ts > edge_last_data_ts` 就全量重算比较（但指标读缓存） |
| 边是否需要执行 | 三要素是否全部满足 | — | 三要素都满足就执行，否则跳过 |

**关键变化（v1.3 → v1.4）：**
- 新增"指标缓存有效性检查"（在 filter 计算之前）
- 排名型 filter 的指标值也能读缓存，只重算比较部分
- 脏标记方式：写时置脏（推荐）

### 4.4 边执行层（三层 filter + 混合策略 + propagate + 即时事件）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **filter 类型判断** | `edge_filter_type[eid]` | — | 先判断 independent / global，再选比较策略 |
| **第一层：指标计算（共享缓存）** | `indicator_cache[formula_key]` + `stock_dirty[code]` + `latest_tick` + `edge_indicator_spec[eid]` | `indicator_cache[formula_key][code]` + `stock_dirty[code] = False` | **v1.4 新增核心层**：先查缓存，缺的/脏的补算，算完写缓存清脏 |
| **第二层：增量比较判断（独立型）** | `indicator_cache` + `edge_compare_spec[eid]` + `edge_filter_cache[eid]` | `edge_filter_cache[eid][code]` | 只重算数据变了的股票的比较，其他读缓存；汇总所有通过的 |
| **第二层：全量比较判断（全局/排名型）** | `indicator_cache` + `edge_compare_spec[eid]` + `node_stocks[sid]` | `edge_filter_cache[eid]`（整体覆盖） | **v1.4 优化**：指标值读缓存，只全量重算比较/排名部分 |
| **第三层：结果汇总 + 传播** | `node_stocks[sid]` + `node_stocks[tid]` + `edge_propagate_spec[eid]` | `node_stocks[tid]` + `stock_state_version[tid][code]` | copy / move / overwrite；更新变化股票的状态版本 |
| **即时事件发射** | `node_stocks[tid]` 新旧对比 + `node_role[tid]` | `event_queue` + `signal_queue` | 差集计算（新 - 旧 / 旧 - 新） | **每条边执行后立即发射** |
| 无条件边立即传播 | `node_stocks[sid]` + `edge_propagate_spec[eid]` | `node_stocks[tid]` + `stock_state_version[tid][code]` | 直接 propagate，无 gate，无 filter |
| 边处理水位线更新 | `latest_tick_ts` | `edge_last_data_ts[eid]` | `edge_last_data_ts[eid] = latest_tick_ts` |

**关键变化（v1.3 → v1.4）：**
- filter 从一层变成三层：指标计算 → 比较判断 → 结果汇总
- 新增指标缓存层（indicator_cache），跨 filter 跨边共享
- 排名型 filter 也能复用指标计算，只重算排名
- 性能显著提升（指标计算是最大瓶颈）

### 4.5 事件层（流式逐条产生）

| 功能 | 读什么表 | 写什么表 | 计算 | 时机 |
|------|---------|---------|------|------|
| 入池事件 | `node_stocks[tid]` 执行前后对比 | `event_queue` | 差集计算（新 - 旧） | **每条边执行后立即发射** |
| 出池事件 | `node_stocks[sid]` 执行前后对比 | `event_queue` | 差集计算（旧 - 新） | **每条边执行后立即发射** |
| 预警事件 | `alert_rules + node_stocks` 变化 | `alert_queue` | 规则匹配 | 每条边执行后立即检查 |
| 交易信号 | `node_role[tid] == 'target' + 入池/出池` | `signal_queue` | 角色判定 + 信号生成 | 每条边执行后立即生成 |

**（这部分 v1.3 已经很好，v1.4 不变）**

### 4.6 后处理层（PK排名/分析角度/看盘面板）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| PK排名 | `node_stocks[target] + latest_tick + pk_config` | `_pk_rankings` | 按权重评分排序 |
| 分析角度 | `node_stocks[target] + latest_tick + analysis_config` | `_angle_results` | 多维度计算 |
| 看盘面板 | `node_stocks + latest_tick + dashboard_schema` | `_dashboard_data` | 组装显示数据 |

**注意：后处理也可以复用 indicator_cache！**
- PK 排名用到的指标，如果 indicator_cache 里已经有了，直接读
- 不用再算一遍
- 又是一次性能提升

---

## 五、表驱动：逻辑在表结构里，差异在表内容里（v1.4 更新版）

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

**（这部分 v1.3 已经很好，v1.4 不变）**

### 5.2 过滤条件（filter_specs.json）—— v1.4 三层化

**v1.4 更新：filter 规格拆成指标层 + 比较层。**

**结构：**

```json
{
  "filter_specs": {
    "price_gt": {
      "filter_type": "independent",
      "indicators": [
        {
          "id": "price",
          "type": "scalar_field",
          "field": "close"
        }
      ],
      "comparison": {
        "type": "scalar_compare",
        "operator": "gt",
        "indicator_ref": "price",
        "threshold_param": "fsecond"
      }
    },
    "ma_cross": {
      "filter_type": "independent",
      "indicators": [
        {
          "id": "ma_fast",
          "type": "formula",
          "formula": "MA",
          "period": "1d",
          "args_param": "nfirst"
        },
        {
          "id": "ma_slow",
          "type": "formula",
          "formula": "MA",
          "period": "1d",
          "args_param": "nsecond"
        }
      ],
      "comparison": {
        "type": "cross",
        "direction": "above",
        "line1_ref": "ma_fast",
        "line2_ref": "ma_slow"
      }
    },
    "rank_top_n": {
      "filter_type": "global",
      "indicators": [
        {
          "id": "pct_change",
          "type": "derived",
          "expr": "(close - pre_close) / pre_close * 100"
        }
      ],
      "comparison": {
        "type": "rank",
        "order": "desc",
        "indicator_ref": "pct_change",
        "n_param": "fsecond",
        "tie_handling": "exact_rank"
      }
    }
  }
}
```

**为什么拆成指标层和比较层？**
1. 指标层是纯函数，可缓存可共享
2. 比较层决定了 filter 是 independent 还是 global
3. 同一个指标可以被多种比较方式使用（如 MA 可以 > X，可以金叉，可以排名）
4. 编译期可以做优化：把相同指标的计算合并，只算一次

**v1.3 到 v1.4 的变化：**
- v1.3：`{nset: {evaluator, operators, filter_type}}` —— 一层
- v1.4：`{spec_name: {indicators: [...], comparison: {...}, filter_type}}` —— 三层

**6 种 nset × 10 种 noperate = 60 种组合**，映射到三层结构后：
- 指标层：几种类型（scalar_field / formula / derived / financial_field）
- 比较层：几种类型（scalar_compare / cross / rank / inflection / ...）
- 组合起来就是所有筛选方式

### 5.3 传播模式（propagate_modes.json）

**结构：** `{mode_name: {op: fn, affects_source: bool}}`

| 模式 | 操作 | 影响源节点 |
|------|------|-----------|
| copy | target += passed | 否 |
| move | target += passed; source = [] | 是 |
| overwrite | target = passed | 否 |

**3 种模式 = 3 条记录，不是 3 个 if。**

**（这部分 v1.3 已经很好，v1.4 不变）**

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

**（这部分 v1.3 已经很好，v1.4 不变）**

### 5.5 UI 层（前后端一体的表驱动）—— v1.4 新增

**前端组件注册表（ui_components.json）：**

```json
{
  "components": {
    "text_input": { "renderer_type": "text_input" },
    "number_input": { "renderer_type": "number_input" },
    "select": { "renderer_type": "select" },
    "formula_editor": { "renderer_type": "formula_editor" },
    "indicator_select": { "renderer_type": "indicator_select" },
    "flag_group": { "renderer_type": "flag_group" },
    ...
  }
}
```

**布局配置（ui_layouts.json）：**
- 每种节点/边类型对应一个布局
- 布局里的字段 = 后端 params 的 data_path
- 字段的验证规则前后端共用

**字段定义（field_definitions.json）：**
- 每种组件类型的解码/编码方式
- 验证规则
- 默认值规则

**前后端同构的关键：**
- 后端的 PanelGenerator 生成面板描述
- 前端的 TableDrivenPanel 渲染面板
- 用同一份配置表（ui_layouts.json / field_definitions.json / cell_type_registry.json）
- 数据结构一致（node.params / edge.params）
- 修改配置，两端同步生效

---

## 六、配置表：从 50+ 张收敛到 12 张核心（v1.4 不变）

### 6.1 核心配置表（运行时引擎直接读的）

| # | 表名 | 作用 | 运行时机 |
|---|------|------|---------|
| 1 | `timing.json` | 时间触发规则（starttype + cxtype） | gate 判定 |
| 2 | `filter_specs.json` | 过滤条件规格（**三层：指标+比较+汇总**，**filter_type**） | filter 计算（支持批量 + 单只，独立型/全局型分类） |
| 3 | `propagate_modes.json` | 传播模式（copy/move/overwrite） | propagate |
| 4 | `node_roles.json` | 节点角色行为定义 | 事件/信号生成 |
| 5 | `edge_semantics.json` | 边类型语义（条件/无条件） | 边类型判定 |
| 6 | `runtime_modes.json` | 运行模式（实盘/回放/仿真） | 模式切换 |
| 7 | `alert_rules.json` | 预警规则 | 预警检查 |
| 8 | `ttl_rules.json` | TTL 淘汰规则 | TTL 过期队列管理 |

**8 张核心配置表。** 引擎核心循环只直接读这 8 张。

**v1.4 变化：** `filter_specs.json` 从一层结构改为三层结构（指标层+比较层+汇总层）。

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
| 17 | `ui_layouts.json` | **v1.4 补充**：UI布局配置 | 前端面板 + 后端PanelGenerator |
| 18 | `field_definitions.json` | **v1.4 补充**：字段定义/组件规则 | 前端面板 + 后端PanelGenerator |
| 19 | `ui_components.json` | **v1.4 补充**：UI组件注册 | 前端组件注册表 |

**这些是外围的，不影响核心引擎的简洁性。** 核心引擎 8 张表就够了。

---

## 七、代码结构：核心极薄，外围分层（v1.4 更新版）

### 7.1 目录结构

```
core/
  engine.py           # 核心引擎 ≤ 800 行
                       # 只做：数据更新 → TTL过期 → pending队列 → 三层filter执行 → 事件
  runtime.py          # 运行时表定义（9张核心表）
                       # latest_tick / latest_tick_ts / stock_data_version / stock_dirty
                       # indicator_cache  ← v1.4 新增
                       # node_stocks / stock_state_version
                       # edge_pending / edge_filter_cache / edge_last_data_ts
                       # ttl_expiry_queue
  compiler.py         # 编译期：pool_config → CompiledPool
                       # v1.4：拆分 filter 为 indicator_spec + compare_spec
  indicators.py       # v1.4 新增：指标计算层（纯函数 + 缓存管理）
                       # 封装 FormulaRouter，统一指标缓存读写
  timing.py           # 时间触发规则（查表 + 组合）
  filters.py          # 过滤条件（比较判断层：独立型增量，全局型全量）
                       # v1.4：只管比较，不管指标计算
  propagate.py        # 传播模式
  roles.py            # 节点角色行为
  events.py           # 事件/信号生成
  ttl_queue.py        # TTL 过期队列管理（最小堆）

data/
  tick_table.py       # latest_tick 表管理（读/写/时间戳水位线/脏标记，秒级精度）
  kline_cache.py      # K线缓存
  providers/          # 各数据源适配器

post_processing/
  pk_ranking.py       # PK 排名
  analysis_angles.py  # 分析角度
  dashboard.py        # 看盘面板
  alerts.py           # 预警

web/
  js/
    panel.js          # 表驱动属性面板（已实现，继续沿用）
    editor.js         # 编辑器（公式/规则/综合设置，已实现）
    # v1.4：前后端同构，数据结构统一

config/               # 19 张 JSON 配置表（8 核心 + 11 外围）
```

**v1.4 关键变化：**
- 新增 `indicators.py`：指标计算层，独立出来，纯函数 + 缓存管理
- `filters.py`：瘦身，只管比较判断，不管指标计算
- `tick_table.py`：写时置脏（stock_dirty）
- `compiler.py`：拆分 filter 规格为 indicator_spec + compare_spec
- 前端：明确前后端同构，共享数据结构

### 7.2 engine.py 核心循环伪代码（三层 filter + 指标缓存，约 260 行）

```python
class StockPoolEngine:
    
    def __init__(self):
        self._latest_tick = {}                # latest_tick[code] = bar_dict
        self._latest_tick_ts = 0.0            # 全局时间水位线（秒级）
        self._stock_dirty = {}                # v1.4：股票级脏标记（写时置脏）
        self._indicator_cache = {}            # v1.4：指标结果缓存
                                               # indicator_cache[formula_key][code] = {value, dirty}
        
        self._node_stocks = {}                # node_stocks[nid] = [code, ...]
        self._stock_state_version = {}        # stock_state_version[nid][code] = ts
        self._code_to_nodes = {}              # code_to_nodes[code] = {nid, ...}  # 反向索引
        
        self._edge_pending = []               # 待处理边队列（按执行顺序，去重）
        self._edge_filter_cache = {}          # edge_filter_cache[eid][code] = bool（比较层缓存）
        self._edge_last_data_ts = {}          # edge_last_data_ts[eid] = ts
        
        self._ttl_queue = TTLExpiryQueue()    # TTL 过期队列（最小堆）
        
        self._compiled = None                 # CompiledPool
    
    def on_tick(self, code, bar):
        """收到新 tick 数据"""
        import math
        ts = math.floor(bar['datetime'])      # 秒级精度
        
        # 1. 写数据
        self._latest_tick[code] = bar
        
        # 2. 更新全局水位线（如果新数据时间更新）
        if ts > self._latest_tick_ts:
            self._latest_tick_ts = ts
        
        # 3. v1.4：写时置脏（方式B）
        self._stock_dirty[code] = True
        
        # 4. 数据更新 → 源节点条件出边入队
        # 找出包含这只股票的所有节点
        nodes_with_code = self._code_to_nodes.get(code, set())
        compiled = self._compiled
        for nid in nodes_with_code:
            # 把该节点的所有条件出边加入 pending 队列
            for eid in compiled['out_edges'].get(nid, []):
                if compiled['edge_type'][eid] == 'conditional':
                    if eid not in self._edge_pending:
                        self._insert_in_order(eid)
    
    def _add_stock_to_node(self, nid, code):
        """把股票加入节点（维护反向索引）"""
        if code not in self._node_stocks.get(nid, []):
            self._node_stocks.setdefault(nid, []).append(code)
            self._code_to_nodes.setdefault(code, set()).add(nid)
    
    def _remove_stock_from_node(self, nid, code):
        """把股票从节点移除（维护反向索引）"""
        if code in self._node_stocks.get(nid, []):
            self._node_stocks[nid].remove(code)
            if code in self._code_to_nodes:
                self._code_to_nodes[code].discard(nid)
                if not self._code_to_nodes[code]:
                    del self._code_to_nodes[code]
    
    def _process_ttl_expiry(self):
        """处理 TTL 过期股票"""
        now_ts = self._latest_tick_ts
        changed_by_node = {}  # nid -> [code, ...]
        
        while not self._ttl_queue.empty():
            expire_ts, nid, code = self._ttl_queue.peek()
            if expire_ts > now_ts:
                break
            
            self._ttl_queue.pop()
            
            if code in self._node_stocks.get(nid, []):
                self._remove_stock_from_node(nid, code)
                self._stock_state_version.setdefault(nid, {})[code] = now_ts
                if nid not in changed_by_node:
                    changed_by_node[nid] = []
                changed_by_node[nid].append(code)
        
        # 触发状态变化
        for nid, codes in changed_by_node.items():
            self._on_node_stocks_changed(nid, codes)
    
    def _on_node_stocks_changed(self, nid, changed_codes):
        """节点股票状态变化了 → 触发下游边"""
        # 更新变化股票的状态版本
        now_ts = self._latest_tick_ts
        for code in changed_codes:
            self._stock_state_version.setdefault(nid, {})[code] = now_ts
        
        # 把所有出边加入 pending 队列（条件边）
        compiled = self._compiled
        for eid in compiled['out_edges'].get(nid, []):
            if compiled['edge_type'][eid] == 'conditional':
                if eid not in self._edge_pending:
                    # 按执行顺序插入，保持有序
                    self._insert_in_order(eid)
            else:
                # 无条件边：立即同步执行
                self._execute_unconditional_edge(eid)
    
    def _process_edge_pending(self):
        """处理 edge_pending 队列（按执行顺序）"""
        while self._edge_pending:
            eid = self._edge_pending.pop(0)
            self._execute_conditional_edge(eid)
    
    def _ensure_indicators(self, indicator_specs, codes):
        """v1.4 新增：确保指标值都是最新的（读缓存 + 补算 + 清脏）
        
        返回：indicator_values[spec_id][code] = value
        """
        result = {}
        compiled = self._compiled
        
        for spec in indicator_specs:
            spec_id = spec['id']
            formula_key = spec['cache_key']
            
            # 确保缓存字典存在
            if formula_key not in self._indicator_cache:
                self._indicator_cache[formula_key] = {}
            cache = self._indicator_cache[formula_key]
            
            # 找出需要重算的股票（脏的 + 还没算过的）
            need_compute = []
            for code in codes:
                if self._stock_dirty.get(code, False) or code not in cache:
                    need_compute.append(code)
            
            # 批量补算
            if need_compute:
                values = self._compute_indicator_batch(spec, need_compute)
                for code, val in zip(need_compute, values):
                    cache[code] = {'value': val, 'dirty': False}
                    self._stock_dirty[code] = False  # 算完清脏
            
            # 收集结果
            result[spec_id] = {code: cache[code]['value'] for code in codes if code in cache}
        
        return result
    
    def _execute_conditional_edge(self, eid):
        """执行一条条件边（三层 filter + 混合策略）"""
        compiled = self._compiled
        sid, tid = compiled['edge_endpoints'][eid]
        now_ts = self._latest_tick_ts
        
        # 1. 检查三要素
        # ① 时间触发条件
        if not self._check_timing(eid, now_ts):
            return  # 时间没到，跳过
        
        # ② 数据版本更新了吗？
        edge_data_ts = self._edge_last_data_ts.get(eid, 0.0)
        if now_ts <= edge_data_ts:
            return  # 数据没变，跳过
        
        # ③ 源节点有变化吗？（能进队列说明有变化，这里是防御性检查）
        source_codes = self._node_stocks.get(sid, [])
        if not source_codes:
            # 源节点空了，但可能还有出池需要传播
            # 继续执行，propagate 会处理
            pass
        
        # 2. === 第一层：指标计算（读缓存 + 补算） ===
        indicator_specs = compiled['edge_indicator_spec'].get(eid, [])
        indicator_values = self._ensure_indicators(indicator_specs, source_codes)
        
        # 3. === 第二层：比较判断（独立型增量，排名型全量） ===
        filter_type = compiled['edge_filter_type'].get(eid, 'independent')
        compare_spec = compiled['edge_compare_spec'].get(eid, {})
        filter_cache = self._edge_filter_cache.setdefault(eid, {})
        
        if filter_type == 'independent':
            # === 独立型：增量比较 ===
            need_compare = []
            for code in source_codes:
                data_version = self._stock_data_version.get(code, 0.0)
                if data_version > edge_data_ts or code not in filter_cache:
                    need_compare.append(code)
            
            if need_compare:
                # 增量比较：只算需要重算的
                for code in need_compare:
                    # 从 indicator_values 读指标值（上一步已确保最新）
                    passed = self._eval_compare_single(code, compare_spec, indicator_values)
                    filter_cache[code] = passed
            
            # 汇总所有通过的
            passed_codes = [code for code in source_codes 
                          if filter_cache.get(code, False)]
        
        else:
            # === global：全量比较（但指标值读缓存！） ===
            all_codes = source_codes
            if all_codes:
                # 全量比较（排名/排序/截面比较等）
                # 指标值从 indicator_values 读（都是最新的）
                passed_list = self._eval_compare_batch(all_codes, compare_spec, indicator_values)
                # 更新整个缓存
                filter_cache.clear()
                for code, passed in zip(all_codes, passed_list):
                    filter_cache[code] = passed
                passed_codes = [code for code, p in zip(all_codes, passed_list) if p]
            else:
                passed_codes = []
        
        # 4. === 第三层：结果汇总 + propagate ===
        old_target = set(self._node_stocks.get(tid, []))
        self._propagate(eid, passed_codes, sid, tid)
        new_target = set(self._node_stocks.get(tid, []))
        
        # 5. 对比变化
        entered = new_target - old_target
        exited = old_target - new_target
        
        if entered or exited:
            # 更新状态版本
            for code in entered:
                self._stock_state_version.setdefault(tid, {})[code] = now_ts
            for code in exited:
                self._stock_state_version.setdefault(tid, {})[code] = now_ts
            
            # 发射事件
            self._emit_events(eid, tid, entered, exited)
            
            # 触发下游
            changed = list(entered) + list(exited)
            self._on_node_stocks_changed(tid, changed)
        
        # 6. 更新边的处理水位线
        self._edge_last_data_ts[eid] = now_ts
    
    def _execute_unconditional_edge(self, eid):
        """执行无条件边（立即同步，无 gate，无 filter）"""
        compiled = self._compiled
        sid, tid = compiled['edge_endpoints'][eid]
        now_ts = self._latest_tick_ts
        
        source_codes = list(self._node_stocks.get(sid, []))
        
        old_target = set(self._node_stocks.get(tid, []))
        self._propagate(eid, source_codes, sid, tid)  # 直接传播，不过滤
        new_target = set(self._node_stocks.get(tid, []))
        
        entered = new_target - old_target
        exited = old_target - new_target
        
        if entered or exited:
            for code in entered:
                self._stock_state_version.setdefault(tid, {})[code] = now_ts
            for code in exited:
                self._stock_state_version.setdefault(tid, {})[code] = now_ts
            
            self._emit_events(eid, tid, entered, exited)
            
            changed = list(entered) + list(exited)
            self._on_node_stocks_changed(tid, changed)
    
    def tick_cycle(self):
        """一个 tick 的完整处理流程"""
        # 1. 等数据更新（外部调用 on_tick）
        # 2. 处理 TTL 过期
        self._process_ttl_expiry()
        # 3. 处理 pending 队列
        self._process_edge_pending()
```

**v1.4 核心循环的关键变化（相对于 v1.3）：**

1. **新增 `_ensure_indicators()` 方法**：统一管理指标缓存的读写和补算
2. **新增 `_stock_dirty` 字典**：写时置脏，算完清脏
3. **新增 `_indicator_cache` 字典**：指标结果缓存，跨 filter 跨边共享
4. **filter 三层化**：
   - 第一层：`_ensure_indicators()` → 指标计算（读缓存+补算）
   - 第二层：比较判断（独立型增量，全局型全量）
   - 第三层：propagate → 结果汇总
5. **排名型也读指标缓存**：全局型 filter 的指标值也从缓存读，只全量重算比较

---

## 八、事件驱动模型（v1.4 补充指标层）

### 8.1 事件类型与传播链

```
行情数据更新事件
    ↓
on_tick(code, bar)
    ↓
更新 latest_tick[code]
更新 latest_tick_ts（如果时间更新，秒级精度）
设置 stock_dirty[code] = True （v1.4：写时置脏）
    ↓
数据更新 → 源节点出边入队
找出包含这只股票的所有节点
把这些节点的条件出边加入 edge_pending（去重）
    ↓
（进入 pending 队列，等待执行时检查三要素）

TTL 过期事件
    ↓
从 ttl_expiry_queue 弹出过期股票
从 node_stocks[nid] 移除
更新 stock_state_version[nid][code]
    ↓
_on_node_stocks_changed(nid, [code, ...])
    ↓
    ├─ 条件边 → 加入 edge_pending 队列
    └─ 无条件边 → 立即同步 propagate

边执行事件（从 pending 队列取出）
    ↓
检查三要素：
  ① 时间条件满足吗？
  ② 数据版本更新了吗？
  ③ 源节点有变化吗？
    ↓
都满足 → 第一层：指标计算（读缓存 + 补算）
            ↓
            先查 indicator_cache
            缺的/脏的批量补算
            算完写入缓存，清脏
            ↓
         第二层：比较判断（先判断类型）
            ├─ independent → 增量比较（只算数据变了的）
            └─ global → 全量比较（指标读缓存，只重算排名/比较）
            ↓
         第三层：结果汇总 + propagate
            ↓
propagate → 更新目标节点
    ↓
对比变化 → entered / exited
    ↓
更新 stock_state_version[tid][code]
发射入池/出池事件
    ↓
_on_node_stocks_changed(tid, changed_codes)  ← 递归触发下游
```

### 8.2 脏标记传播图（v1.4 更新版）

```
                     ┌─────────────────────┐
                     │   latest_tick_ts    │  全局水位线（float，秒级）
                     └─────────┬───────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
    stock_dirty[A]        dirty[B]       ...  dirty[N]
    （每只股票一个 bool 脏标记）
              │
              │ 数据更新时：
              │ 1. stock_dirty[code] = True  （置脏）
              │ 2. 找出包含这只股票的节点
              │ 3. 节点条件出边入队 edge_pending
              ▼
    ┌──────────────────────────┐
    │      edge_pending        │  待处理边队列
    └──────────────┬───────────┘
                   │
                   ▼
          边执行时检查三要素
                   │
         ┌─────────┴─────────┐
         ▼                   ▼
  independent          global
  （增量比较）        （全量比较）
         │                   │
         └─────────┬─────────┘
                   │
         ┌─────────▼─────────┐
         │  indicator_cache  │  ← v1.4 核心新增
         │  （指标结果缓存）   │
         │  先读缓存，缺的补算  │
         │  跨filter跨边共享   │
         └─────────┬─────────┘
                   │
         ┌─────────▼─────────┐
         │ edge_filter_cache │  比较结果缓存（独立型用）
         └───────────────────┘
```

---

## 九、前端简化规划（v1.4 新增：前后端一体设计）

### 9.1 现状盘点（从代码中看到的）

**已有的好基础：**
- `panel.js` 的 `TableDrivenPanel` 已经是表驱动的了
- `ComponentRegistry` 组件注册表，支持动态扩展
- `DataBinder` 数据绑定工具，支持按 path 读写
- `ValidationEngine` 验证引擎，支持配置化校验规则
- `editor.js` 的 `ComprehensiveSettings` 综合设置窗口
- `FormulaEditor` 公式编辑器
- `table_engine.py` 的 `PanelGenerator` 后端面板生成

**需要改进的地方：**
- 前后端数据结构需要更严格的一一对应
- 综合设置的行顺序应该就是后端的执行顺序
- 公式配置界面的数据结构应该和后端 filter_spec 一致
- 一些硬编码的业务逻辑需要抽到配置表里

### 9.2 前端三模块对应后端三结构

```
前端模块                 ≡          后端结构
─────────────────────────────────────────────────────
ComprehensiveSettings    ≡    CompiledPool.edge_order
（综合设置表格）         ≡    （执行顺序）
                         ≡
  行 = 一条边            ≡    edge_order 的一个元素
  行号 = 执行序号        ≡    列表索引 = 执行顺序
  拖拽调整顺序           ≡    调整 edge_order
─────────────────────────────────────────────────────
TableDrivenPanel        ≡    node.params / edge.params
（属性面板）            ≡    （节点/边参数）
                         ≡
  字段 = 参数            ≡    data_path = params.xxx
  组件类型 = 配置表驱动   ≡    ui_layouts.json
  验证规则 = 配置表驱动   ≡    field_definitions.json
─────────────────────────────────────────────────────
FormulaEditor +          ≡    filter_spec (indicator_spec + compare_spec)
条件配置界面             ≡    （过滤条件规格）
                         ≡
  公式选择/编辑          ≡    indicator_spec.formula
  参数配置              ≡    indicator_spec.args
  比较方式选择           ≡    compare_spec.operator
  阈值设置              ≡    compare_spec.threshold
─────────────────────────────────────────────────────
```

### 9.3 前端简化路线图

#### 第 1 步：统一配置来源（低风险，高收益）

- 后端的 `ui_layouts.json` / `field_definitions.json` / `cell_type_registry.json`
- 前端通过 API 拉取，或打包为静态资源
- 确保两端用同一份配置
- 现在已经基本做到了，需要确认和固化

#### 第 2 步：综合设置 = 执行顺序（中风险，架构意义大）

- 综合设置表格的行数据结构和后端 `edge_order` 对齐
- 行顺序直接就是执行顺序
- 用户拖拽调整行顺序 → 更新 `edge_order` → 同步到后端
- 消除"前端一套顺序，后端一套顺序"的不一致

#### 第 3 步：属性面板字段 = 节点参数（已基本实现，需巩固）

- 前端 `data_path` 直接映射后端 `node.params.xxx`
- 面板字段的增删改完全由配置表驱动
- 前端不硬编码任何节点类型的字段
- 现在 `panel.js` + `PanelGenerator` 已经做到了，保持

#### 第 4 步：公式配置界面 = filter 规格（中风险，核心对齐）

- 公式选择器、参数表单、比较方式选择器
- 数据结构和后端 `filter_spec` 完全一致
- 前端编辑的结果可以直接传给后端做 filter
- 后端的 filter_spec 也可以直接渲染成前端配置界面

#### 第 5 步：双向绑定自动同步（已有基础，完善）

- 前端修改 → DataBinder 更新本地数据 → 自动同步到后端
- 后端配置变更 → 推送到前端 → 面板自动刷新
- 现在 `DataBinder` 有了基础，需要完善同步机制

### 9.4 前端代码结构优化（建议）

```
web/js/
  core/
    data-binder.js       # 数据绑定（已在 panel.js 里，可抽离）
    component-registry.js # 组件注册（已在 panel.js 里，可抽离）
    validation.js        # 验证引擎（已在 panel.js 里，可抽离）
  panels/
    node-panel.js        # 节点属性面板
    edge-panel.js        # 边属性面板
    formula-panel.js     # 公式配置面板
  editors/
    formula-editor.js    # 公式编辑器（已在 editor.js 里）
    rule-editor.js       # 规则编辑器（已在 editor.js 里）
    comprehensive-settings.js  # 综合设置（已在 editor.js 里）
  app.js                 # 应用入口，组装各模块
```

**（这部分是建议，不是必须。现在的合并文件方式也能工作，只是模块边界不太清晰。）**

---

## 十、总结：v1.4 的三个核心升级

### 升级 1：指标缓存分层——抓住性能瓶颈的牛鼻子

**之前（v1.3）：**
- 缓存整个 filter 结果（bool）
- 粒度太粗，同指标在不同 filter 里重复计算
- 排名型完全不能缓存

**现在（v1.4）：**
- filter 拆成三层：指标计算（纯函数）→ 比较判断 → 结果汇总
- 新增 indicator_cache，指标值一次计算，多处复用
- 排名型也能读指标缓存，只重算排名
- 性能提升：假设指标计算占 filter 时间的 80%，5 条边共用一个指标，就是 4x 提升

### 升级 2：脏标记等价性——两种方式任选，推荐写时置脏

**之前（v1.3）：**
- 只提了时间戳对比一种方式
- 没有讨论其他可能性

**现在（v1.4）：**
- 明确两种等价方式：A)时间戳对比 B)写时置脏
- 证明了等价性
- 推荐写时置脏（方式 B）：更直观、更简单、和现有代码 `_data_dirty` 概念一致

### 升级 3：前后端一体设计——从只改后端到两端同构

**之前（v1.3）：**
- 只关注后端引擎简化
- 前端完全没提

**现在（v1.4）：**
- 明确三个核心对应关系：综合设置=执行顺序、属性面板=节点参数、公式配置=filter规格
- 前后端共享同一份配置数据结构
- 简化要前后端一起考虑，不能各搞一套
- 前端也有简化路线图，和后端同步推进

---

**v1.4 一句话总结：** 把 filter 拆成"指标计算（纯函数，可缓存可共享）+ 比较判断（独立型可增量，排名型全量）+ 结果汇总"三层，新增 indicator_cache 实现跨边跨filter复用，明确脏标记两种等价方式，前后端统一数据结构一体设计。
