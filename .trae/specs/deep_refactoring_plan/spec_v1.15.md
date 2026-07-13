# 股票池深度重构规划 v1.15

> 版本主题：诚实化 + 类型实例分离 + 时间细化 + 整体架构
> 设计原则：诚实不吹牛、正交拆分、三态逻辑、保守策略
> 目标：诚实化表驱动层级（L1分发 + 配置化，不吹牛L2），澄清类型配置 vs 实例配置边界，细化双时间模型粒度（tick级 vs bar级），建立四层整体架构图

---

## v1.14 → v1.15 变更摘要表

**变更日期：** 2026-07-01

| # | 变更项 | v1.14 | v1.15 | 本质变化 |
|---|--------|------|-------|---------|
| 1 | **表驱动层级诚实化** | "L2组合表驱动落地"（夸大） | **L1分发表驱动 + 配置化应用，L2暂不做** | 实事求是：配置化不是L2，L2是组合引擎通用、加新行为不改代码，目前做不到也不需要 |
| 2 | **类型配置 vs 实例配置** | 混在一张表里（node_table 既存类型又存实例） | **彻底分离：类型配置表 + 实例配置表** | 正交拆分：类型定义行为（开发时改），实例定义参数（用户设计时改），两者完全不同 |
| 3 | **双时间模型细化** | 数据时间 vs 系统时间（两个模糊概念） | **数据时间2种 + 系统时间3种，共5个时间戳** | 粒度细化：tick_data_ts / bar_data_ts[period] / sys_ts / last_poll_ts / last_calc_ts，每个都有明确用途 |
| 4 | **整体架构图** | 没有整体分层 | **四层架构：基础设施层 → 配置层 → 运行时层 → 接口层** | 建立清晰的分层架构，每层职责、边界、依赖关系明确 |
| 5 | **配置表清单更新** | 11张配置表（类型实例混） | **13张（类型表6张 + 实例表5张 + 时间表2张）** | 类型/实例分离后，配置表按层级重新分类 |
| 6 | **核心循环更新** | 有双时间但粒度粗 | **体现5个时间戳 + 类型实例查表流程** | 核心循环中明确每个时间戳的更新时机和用途，明确类型表和实例表的查表顺序 |
| 7 | **功能-表操作对应表更新** | 类型实例混 | **按类型层/实例层/运行时层分类** | 每个功能明确读类型表还是实例表，边界清晰 |

**一句话总结 v1.15 升级：** 诚实——是什么就说什么，配置化就是配置化，不吹牛叫L2；分离——类型配置和实例配置是两个完全不同的概念，彻底分开；细化——时间模型从2个概念细化到5个时间戳，每个都清清楚楚；架构——建立四层整体架构，每层职责边界明确。

---

## 一、表驱动层级诚实化（L1分发 + 配置化，L2暂不做）

### 1.1 上一轮的问题：夸大了

**v1.14 的说法（夸大）：**
- "L2 组合表驱动落地"
- "filter 三层组合 = 真正的改表不改代码"
- "加新 filter 完全是配置，不需要写代码"

**评审指出的问题：**
- 这不是 L2，这只是"配置化"
- 加新算子还是要写代码（L1），加新组合方式还是要写代码（L1）
- 配置化是 L1 的应用，不是 L2
- L2 的定义是：行为由多个正交表的组合决定，组合引擎是通用的，加新行为不需要写代码

**v1.15 诚实的说法：**
- L1 分发表驱动：用表做类型分发，加新类型要写新 handler ✅ 已实现
- **"配置化"**：同类功能可以通过配置组合出新实例（比如选指标+选算子=新filter）
  - 这不是 L2，这是"配置化"，是 L1 的应用
  - 加新算子还是要写代码（L1），但加新filter配置不需要（配置化）
- L2 组合表驱动：行为由多个正交表的组合决定，组合引擎是通用的，加新行为不需要写代码
  - 这个级别股票池目前不需要，也做不到
- 诚实命名：就叫"配置化"，不吹牛叫"L2"
- 表驱动只有两层：L1分发 + 配置化应用。L2 暂不做。

### 1.2 表驱动的三个层级定义

**先明确定义，再说我们在哪一层：**

| 层级 | 定义 | 加新行为要不要写代码 | 例子 |
|------|------|---------------------|------|
| **L0：硬编码** | 逻辑直接写在代码里，if-else 分支 | 要写代码，改逻辑就要改代码 | if node.type == 'condition': ... elif node.type == 'alert': ... |
| **L1：分发表驱动** | 用表做类型分发，每种类型对应一个 handler | 加新类型要写新 handler，但分发逻辑不用改 | node_behavior_table[type_id] → handler，查表调用 |
| **配置化（L1.5）** | 同类功能可以通过配置组合出新实例 | 加新实例不用写代码，但加新算子/新类型还是要写 | 选指标+选算子+选组合方式=新filter（配置化），但加新算子要写代码 |
| **L2：组合表驱动** | 行为由多个正交表的组合决定，组合引擎是通用的 | 加新行为完全不用写代码，改表就行 | （股票池目前不需要，也做不到） |

**关键区别：配置化 vs L2**

| 维度 | 配置化（L1.5） | L2 组合表驱动 |
|------|---------------|--------------|
| 组合的是什么 | 同类型的不同参数 | 不同维度的正交组合 |
| 组合引擎 | 针对特定领域（filter） | 通用的，不关心领域 |
| 加新算子/新维度 | 要写代码 | 不用写代码（加表就行） |
| 适用范围 | 单一功能（filter） | 整个系统的行为 |
| 股票池现状 | ✅ 能做到 | ❌ 不需要，也做不到 |

### 1.3 股票池的真实层级：L1 + 配置化

**股票池目前做到的：**

1. **L1 分发表驱动** ✅ 已实现
   - node_behavior_table：节点类型 → handler
   - edge_behavior_table：边类型 → handler
   - operator_table：算子类型 → handler
   - combine_table：组合方式类型 → handler
   - 加新类型 = 写新 handler + 注册到表

2. **配置化应用（L1.5）** ✅ 部分实现
   - filter 配置化：选指标 + 选算子 + 选组合方式 = 新filter实例
   - 加新 filter 实例 = 改配置，不用写代码
   - 但加新算子 / 加新组合方式 = 要写代码（L1）
   - 这是 L1 的应用，不是 L2

3. **L2 组合表驱动** ❌ 不做
   - 股票池不需要这么高的抽象层级
   - 强行做 L2 会过度设计，复杂度爆炸
   - 诚实一点，就叫"配置化"

### 1.4 为什么之前会混淆？

**混淆的根源：**
- "加新 filter 不改代码" → 这句话只说对了一半
- 加新 filter **实例** 不改代码 ✓（配置化）
- 加新 filter **类型** 还是要改代码 ✗（L1）
- 之前把"加新实例不改代码"说成了"L2"，夸大了

**正确的表述：**
- ❌ 错误："L2 组合表驱动，加新 filter 不改代码"
- ✅ 正确："filter 配置化，加新 filter 实例不用写代码；但加新算子/新组合方式还是要写代码（L1）"

### 1.5 配置化的价值（虽然不是L2，但依然有用）

**不要因为不是L2就觉得配置化没用：**

1. **用户价值**：用户可以自己组合新 filter，不用找开发
   - "我要一个 MA5 > 10 且 成交量 > 1000万 的 filter" → 用户自己配
   - 不用写代码，不用发版

2. **开发价值**：算子和组合方式是有限的
   - 常用算子也就 10 个左右（gt/lt/between/cross 等）
   - 常用组合方式也就 10 个以内（and/or/top_n 等）
   - 写完这 20 个 handler，用户能组合出成百上千种 filter

3. **维护价值**：逻辑清晰，正交拆分
   - 指标归指标，算子归算子，组合归组合
   - 改一个不影响其他
   - 测试也方便

**所以：配置化是好东西，但它不是 L2。是什么就说什么。**

### 1.6 现有代码验证

**验证 1：node_behavior_table 是 L1 分发表驱动**
- 每种节点类型对应一个 handler
- 加新节点类型要写新 handler
- ✅ 符合：这是 L1，不是 L2

**验证 2：formula_funcs.json 是配置化的雏形**
- 公式引擎的函数是配置化的
- 加新函数（公式引擎支持的）= 加配置
- 但加新函数类型（比如新的窗口函数）= 要写代码
- ✅ 符合：这是配置化，不是 L2

**验证 3：filter 的三层组合是配置化**
- 选指标 + 选算子 + 选组合方式 = 新 filter
- 加新 filter 实例 = 改配置
- 但加新算子 = 写代码
- ✅ 符合：这是配置化，不是 L2

---

## 二、类型配置 vs 实例配置（两个完全不同的概念）

### 2.1 之前的问题：混了

**v1.14 及之前的问题：**
- node_table 里既存类型定义，又存实例配置
- 比如：node_table 里有 type_id（类型），又有 position（实例UI位置）
- 概念上混淆了"某类节点"和"某个具体节点"
- 修改频率也不一样：类型很少改，实例经常改

**为什么这是大问题：**
- 类型配置和实例配置是完全不同的两个维度
- 类型是"类"（class），实例是"对象"（instance）
- 混在一张表里，职责不清，正交性差
- 类型表改了影响所有实例，实例表改了只影响一个实例

### 2.2 两个完全不同的配置维度

| 维度 | 类型配置（Type-level） | 实例配置（Instance-level） |
|------|----------------------|--------------------------|
| **定义** | 某**类**节点/边的定义 | 某**一个具体**节点/边的配置 |
| **类比** | 类（class） | 对象（instance） |
| **例子** | "条件选股节点"这种类型的定义 | 股票池里"MA5金叉"这个具体的条件选股节点的配置 |
| **存在哪里** | node_type_table、edge_type_table | node_instance_table、edge_instance_table |
| **修改频率** | 低（开发时改） | 高（用户设计股票池时改） |
| **改了影响谁** | 所有该类型的实例 | 只影响这一个实例 |
| **谁来改** | 开发人员 | 最终用户（设计股票池时） |

**正交关系：**
- 类型定义行为（有什么 handler，支持什么参数）
- 实例定义参数（用哪个类型，参数值是什么）
- 同一类型可以有多个实例
- 一个实例只能属于一个类型

### 2.3 类型配置表有哪些？

**类型配置表（Type-level）：6 张**

| # | 表名 | 职责 | 修改频率 | 谁来改 |
|---|------|------|---------|-------|
| 1 | `node_type_table` | 节点类型定义：身份、分类、结构属性 | 低（开发时） | 开发人员 |
| 2 | `node_behavior_table` | 节点行为定义：init/in/out/tick handler | 低（开发时） | 开发人员 |
| 3 | `edge_type_table` | 边类型定义：身份、分类、结构属性 | 低（开发时） | 开发人员 |
| 4 | `edge_behavior_table` | 边行为定义：gate/filter/propagate handler + trigger_mode | 低（开发时） | 开发人员 |
| 5 | `ui_type_table` | UI 类型定义：默认显示名、颜色、图标、默认大小 | 低（开发时） | 开发人员 |
| 6 | `operator_type_table` | 算子/组合方式类型定义：handler、参数定义 | 低（开发时） | 开发人员 |

**注意：**
- operator_table / combine_table / formula_table 本质上也是类型配置
- 它们定义了"有哪些算子/组合方式/指标公式"
- 用户选的时候是从这些类型里选，配置到实例里
- 为了简化，我们还是叫 operator_table / combine_table / formula_table，但它们属于类型配置层

### 2.4 实例配置表有哪些？

**实例配置表（Instance-level）：5 张**

| # | 表名 | 职责 | 修改频率 | 谁来改 |
|---|------|------|---------|-------|
| 1 | `node_instance_table` | 具体节点实例：用哪个类型、参数值 | 高（设计时） | 用户 |
| 2 | `edge_instance_table` | 具体边实例：用哪个类型、参数值、filter配置 | 高（设计时） | 用户 |
| 3 | `ui_instance_table` | 具体实例的UI：位置、大小、显示名（可覆盖类型默认） | 高（设计时） | 用户 |
| 4 | `pool_instance_table` | 股票池实例：节点列表、边列表、拓扑结构 | 高（设计时） | 用户 |
| 5 | `formula_instance_table` | 自定义指标实例：用户自己写的公式 | 中（设计时） | 用户 |

**注意：**
- 实例配置是用户设计股票池时产生的
- 每个股票池有自己的一套实例配置
- 实例配置引用类型配置（通过 type_id）

### 2.5 类型和实例的关系

```
类型配置层（开发时定义，很少改）
  ┌─────────────────────────────────────┐
  │  node_type_table                    │
  │  node_behavior_table                │
  │  edge_type_table                    │
  │  edge_behavior_table                │
  │  ui_type_table                      │
  │  operator_table / combine_table     │
  │  formula_table                      │
  └─────────────────────────────────────┘
                    │
                    │ 引用（通过 type_id）
                    ▼
实例配置层（用户设计时，经常改）
  ┌─────────────────────────────────────┐
  │  pool_instance_table                │
  │  node_instance_table                │
  │  edge_instance_table                │
  │  ui_instance_table                  │
  │  formula_instance_table（自定义）    │
  └─────────────────────────────────────┘
```

**查表顺序：**
1. 先查实例表，得到 type_id 和实例参数
2. 再查类型表，得到行为定义（handler）
3. 合并类型默认值和实例参数
4. 执行 handler

**例子：**
```
类型层：
  node_type_table["condition_node"] = {
    category: "filter",
    has_stocks: true,
    ...
  }
  node_behavior_table["condition_node"] = {
    init_handler: "init_condition_node",
    in_edge_handler: "in_edge_condition",
    out_edge_handler: "out_edge_condition",
    ...
  }

实例层：
  node_instance_table["node_1"] = {
    type_id: "condition_node",  ← 引用类型
    name: "MA5金叉",
    params: {
      formula: "MA5上穿MA10",
      ...
    }
  }
```

### 2.6 之前的设计哪里混了？

**v1.14 的 node_table（混合了类型和实例）：**
```
node_table = {
  "market_source": {  ← 这是 type_id，应该在类型表里
    type_id: "market_source",
    category: "source",
    handler: "market_source_handler",  ← 行为，类型层面的
    position: {x: 100, y: 200},  ← 这是实例的UI位置，应该在实例表里
    display_name: "市场数据源",  ← 这是类型的默认显示名
    ...
  }
}
```

**问题：**
- position 是实例级的（每个节点实例位置不一样），但放在了类型表里
- 一张表既存类型定义，又存实例数据，职责不清
- 改 position 应该只影响一个实例，但现在放在类型表里会混淆

**v1.15 拆分后：**
```
类型层（node_type_table + node_behavior_table）：
  "market_source": {
    type_id: "market_source",
    category: "source",
    allowed_roles: [...],
    has_stocks: false,
    ...
  }

实例层（node_instance_table + ui_instance_table）：
  "node_1": {
    type_id: "market_source",  ← 引用类型
    pool_id: "pool_1",
    params: {...},
    position: {x: 100, y: 200},  ← 实例的UI位置
    custom_name: "我的数据源",  ← 实例自定义名称（覆盖类型默认）
    ...
  }
```

### 2.7 正交性验证

**验证 1：改类型不影响实例数量**
- 加一个新的节点类型 → 类型表加一行
- 实例表完全不用动
- ✅ 正交

**验证 2：改实例不影响类型定义**
- 用户改一个节点的位置 → 改 ui_instance_table
- 类型表完全不用动
- ✅ 正交

**验证 3：一个类型多个实例**
- 类型表只有一个"条件选股节点"类型
- 实例表可以有 100 个"条件选股节点"的实例
- 每个实例参数不同，但行为逻辑一样
- ✅ 正交，复用性好

**验证 4：修改频率不同**
- 类型表：几个月改一次（加新功能时）
- 实例表：用户每天都在改（设计股票池时）
- 混在一张表里 → 经常改的部分影响不常改的部分
- 分开后 → 各改各的，互不影响
- ✅ 正交

---

## 三、双时间模型细化（5个时间戳）

### 3.1 之前的问题：粒度太粗

**v1.14 的双时间模型：**
- 数据时间：行情数据的时间戳
- 系统时间：服务器的本地时间

**问题：粒度太粗，很多场景说不清：**
- 数据时间是 tick 的时间还是 K线的时间？
- 每只股票的数据时间可能不一样，怎么存？
- 系统时间有很多种：当前时间、上次轮询时间、上次计算时间，都不一样
- "时间触发用数据时间" → 用哪个数据时间？

**v1.15 细化为 5 个时间戳：**
- 数据时间（2种）：tick_data_ts、bar_data_ts[period]
- 系统时间（3种）：sys_ts、last_poll_ts、last_calc_ts

### 3.2 数据时间细化（2种）

**数据时间 = 市场时间 = 行情数据的时间戳**

再细分两种：

| 时间戳 | 全称 | 定义 | 粒度 | 存储方式 | 更新时机 |
|--------|------|------|------|---------|---------|
| `tick_data_ts[code]` | tick级数据时间 | 每只股票最新 tick 的时间戳 | 股票级（每只可能不一样） | Dict[code → timestamp] | 收到新 tick 时更新 |
| `bar_data_ts[code][period]` | bar级数据时间 | 每只股票、每个周期K线的最新时间戳 | 股票×周期级 | Dict[code → Dict[period → timestamp]] | K线更新/确认时更新 |

**详细说明：**

**1. tick_data_ts（tick级数据时间）**
- 定义：每只股票最新一笔 tick 的时间戳
- 粒度：股票级，每只股票可能不一样
  - 比如：平安银行最新 tick 是 9:30:05，贵州茅台最新 tick 是 9:30:03
- 存储：`Dict[code → timestamp]`
- 更新时机：每次收到新 tick 时，更新对应股票的 tick_data_ts
- 用途：
  - 判断某只股票有没有新数据
  - 计算数据延迟（系统时间 - tick_data_ts）
  - tick 级策略的时间触发

**2. bar_data_ts[period]（bar级数据时间）**
- 定义：每只股票、每个周期K线的最新时间戳
- 粒度：股票×周期级
  - 比如：平安银行的 1分钟K线最新是 9:30:00，日K线最新是 2026-06-30
- 存储：`Dict[code → Dict[period → timestamp]]`
- 更新时机：
  - 未完成K线更新时，更新 current_bar 的时间
  - 周期确认时，更新 completed_bar 的时间
- 用途：
  - 判断某只股票某周期的K线有没有更新
  - K线级策略的时间触发
  - 周期确认判断

**注意：**
- 之前的"数据时间"是一个模糊概念，现在细化为两种
- tick 级和 bar 级是完全不同的时间粒度
- 不同股票的数据时间可能不一样（有的股票活跃，有的不活跃）
- 不同周期的 bar_data_ts 也不一样（1分钟更新快，日线更新慢）

### 3.3 系统时间细化（3种）

**系统时间 = 处理时间 = 服务器的本地时间**

再细分三种：

| 时间戳 | 全称 | 定义 | 单调性 | 更新时机 | 用途 |
|--------|------|------|--------|---------|------|
| `sys_ts` | 当前系统时间 | 当前时刻的系统时间 | 单调递增 | 每次需要时获取 | 超时检测、TTL、日志时间戳 |
| `last_poll_ts` | 上次轮询时间 | 上一次轮询数据的时间 | 单调递增 | 每次轮询数据后更新 | 计算轮询间隔、判断轮询是否正常 |
| `last_calc_ts` | 上次计算时间 | 上一次完成计算的时间 | 单调递增 | 每次计算完成后更新 | 判断计算是否卡住、性能统计 |

**详细说明：**

**1. sys_ts（当前系统时间）**
- 定义：当前时刻的系统时间（wall clock）
- 单调性：单调递增（假设NTP同步正常，不会跳变）
- 更新时机：每次需要时调用 `time.time()` 或 `datetime.now()`
- 用途：
  - 超时检测（sys_ts - last_poll_ts > 阈值 → 超时）
  - TTL 过期计算（expire_ts = in_pool_ts + ttl，比较 expire_ts 和 sys_ts）
  - 日志时间戳
  - 性能统计（耗时 = end_sys_ts - start_sys_ts）

**2. last_poll_ts（上次轮询时间）**
- 定义：上一次完成数据轮询的系统时间
- 单调性：单调递增
- 更新时机：每次轮询数据结束后更新
- 用途：
  - 计算轮询间隔（sys_ts - last_poll_ts）
  - 判断轮询是否正常（间隔太长说明有问题）
  - 超时检测的基准（不是用数据时间，是用"我们多久没轮询到数据了"）

**3. last_calc_ts（上次计算时间）**
- 定义：上一次完成计算循环的系统时间
- 单调性：单调递增
- 更新时机：每次计算循环完成后更新
- 用途：
  - 判断计算是否卡住（sys_ts - last_calc_ts > 阈值 → 卡住了）
  - 性能统计（计算耗时 = last_calc_ts - last_poll_ts）
  - 健康检查

### 3.4 五个时间戳的关系

```
数据时间（市场时间，可能乱序，每只股票可能不一样）
  ┌─────────────────────────────────────────────────┐
  │  tick_data_ts[code]                              │
  │    - 每只股票最新 tick 的时间戳                   │
  │    - 股票级粒度                                   │
  │    - 更新：收到新 tick 时                         │
  ├─────────────────────────────────────────────────┤
  │  bar_data_ts[code][period]                       │
  │    - 每只股票每个周期K线的最新时间戳              │
  │    - 股票×周期级粒度                              │
  │    - 更新：K线更新/确认时                         │
  └─────────────────────────────────────────────────┘

系统时间（处理时间，单调递增，全局唯一）
  ┌─────────────────────────────────────────────────┐
  │  sys_ts                                          │
  │    - 当前系统时间                                 │
  │    - 每次需要时获取                               │
  │    - 用途：超时、TTL、日志、性能统计              │
  ├─────────────────────────────────────────────────┤
  │  last_poll_ts                                    │
  │    - 上次轮询数据的时间                           │
  │    - 每次轮询后更新                               │
  │    - 用途：判断轮询间隔、超时检测基准             │
  ├─────────────────────────────────────────────────┤
  │  last_calc_ts                                    │
  │    - 上次完成计算的时间                           │
  │    - 每次计算完成后更新                           │
  │    - 用途：判断计算是否卡住、性能统计             │
  └─────────────────────────────────────────────────┘
```

### 3.5 每个时间戳的维护者

| 时间戳 | 谁来维护 | 在哪里维护 |
|--------|---------|-----------|
| tick_data_ts[code] | 数据层 | 轮询数据时，每收到一个 tick 就更新 |
| bar_data_ts[code][period] | 周期管理层 | 更新K线数据时更新 |
| sys_ts | 操作系统 | 直接调用系统API获取，不存 |
| last_poll_ts | 主循环 | 轮询数据结束后更新 |
| last_calc_ts | 主循环 | 计算循环完成后更新 |

### 3.6 常见场景用哪个时间戳？

| 场景 | 用哪个时间戳 | 为什么 |
|------|-------------|--------|
| "9:30触发"（tick级策略） | tick_data_ts | 看该股票的 tick 时间有没有到 9:30 |
| "9:30触发"（K线级策略） | bar_data_ts['1m'] | 看该股票的1分钟K线时间有没有到 9:30 |
| 周期确认（1分钟K线确认） | bar_data_ts['1m'] | 数据时间跨过分钟边界 |
| 超时检测（30秒没数据） | sys_ts - last_poll_ts | 用系统时间算"我们多久没收到数据了" |
| TTL 过期 | sys_ts | 入池时间 + TTL 和当前系统时间比 |
| 日志时间戳 | sys_ts | 记录"我们什么时候处理的" |
| 性能统计（计算耗时） | last_calc_ts - last_poll_ts | 用系统时间算处理快慢 |
| 判断计算是否卡住 | sys_ts - last_calc_ts | 系统时间过了很久还没完成计算 |
| 指标计算 | bar_data_ts / tick_data_ts | 用行情数据的时间序列 |
| 回测/回放 | bar_data_ts / tick_data_ts | 模拟历史，用历史数据的时间 |

**简单记忆：**
- 和"市场/行情/数据"相关 → 用数据时间（tick_data_ts 或 bar_data_ts）
- 和"我们/系统/处理"相关 → 用系统时间（sys_ts / last_poll_ts / last_calc_ts）
- 股票级的 → 用股票级的时间戳（每只股票可能不一样）
- 全局的 → 用全局系统时间

### 3.7 数据时间的股票级粒度

**重要：数据时间是股票级的，不是全局的。**

之前可能有个误解："数据时间就是最新行情的时间"
- 不对！每只股票的数据时间可能不一样
- 比如：
  - 平安银行：最后一笔 tick 是 9:30:05
  - 贵州茅台：最后一笔 tick 是 9:30:03
  - 某只停牌股票：最后一笔 tick 是昨天 15:00:00

**所以：**
- tick_data_ts 是 `Dict[code → timestamp]`，不是一个全局变量
- bar_data_ts 是 `Dict[code → Dict[period → timestamp]]`，也不是全局的
- 判断"某只股票有没有新数据"，要看这只股票的 tick_data_ts 有没有更新
- 不能用"全局最新数据时间"来代表所有股票

**对脏驱动的影响：**
- dirty_stocks 就是"tick_data_ts 有更新的股票集合"
- 每只股票独立判断是否变脏
- 这和我们之前的设计是一致的 ✅

### 3.8 现有代码验证

**验证 1：latest_tick 是股票级的**
- `latest_tick: Dict[code → bar_dict]`
- 每只股票有自己的最新 tick
- tick 里有时间戳
- ✅ 符合：tick_data_ts 可以从 latest_tick 里提取

**验证 2：period_data 是分周期的**
- `period_data[period] = {completed_bars, current_bar}`
- 每个周期有自己的K线数据
- 每根K线有自己的时间戳
- ✅ 符合：bar_data_ts 可以从 period_data 里提取

**验证 3：主循环里有系统时间**
- `system_time = datetime.datetime.now()`
- 每次循环都获取
- ✅ 符合：sys_ts 的雏形

**验证 4：之前没有 last_poll_ts / last_calc_ts**
- 代码里没有明确的这两个变量
- 但概念上是存在的（轮询前后、计算前后）
- ✅ 符合：v1.15 把它们明确出来

---

## 四、整体架构图（四层）

### 4.1 四层架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│                        接口层（Interface Layer）                  │
│  前端API  │  事件推送  │  WebSocket  │  REST API                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│                        运行时层（Runtime Layer）                  │
│  状态表  │  事件循环  │  脏驱动  │  计算引擎  │  TTL管理          │
│                                                                   │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│                        配置层（Config Layer）                     │
│  ┌───────────────┐     ┌───────────────┐                        │
│  │  类型配置      │     │  实例配置      │                        │
│  │  node_type    │     │  pool_instance │                        │
│  │  edge_type    │     │  node_instance │                        │
│  │  behavior     │     │  edge_instance │                        │
│  │  ui_type      │     │  ui_instance   │                        │
│  │  operator     │     │  formula_inst  │                        │
│  └───────────────┘     └───────────────┘                        │
│                                                                   │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│                      基础设施层（Infrastructure Layer）           │
│  交易日历  │  数据提供者  │  公式引擎  │  日志  │  配置存储       │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

**四层架构，从下到上：**
1. 基础设施层：最底层，提供基础能力
2. 配置层：类型配置 + 实例配置，定义系统行为
3. 运行时层：状态表 + 事件循环 + 脏驱动，实际运行
4. 接口层：最上层，和外部交互

### 4.2 第一层：基础设施层（Infrastructure Layer）

**定义：最底层的基础能力，所有上层都依赖它。**

**组成：**

| 模块 | 职责 | 关键数据/接口 |
|------|------|--------------|
| **交易日历** | 交易日判断、交易时段、节假日、午间休市 | TradeCalendar 类，is_trading_day / is_trading_time / next_trading_day |
| **数据提供者** | 行情数据获取、轮询、缓存 | DataProvider 接口，poll_latest_data / get_history_bars |
| **公式引擎** | 技术指标计算、TDX公式解析 | FormulaEngine 类，eval / eval_batch |
| **配置存储** | 配置表加载、缓存、热加载、校验 | ConfigStore 类，load_table / get_table / reload |
| **日志系统** | 日志记录、分级、输出 | Logger 接口 |
| **时间工具** | 时间转换、格式化、交易日计算 | 时间工具函数 |

**特点：**
- 最底层，被所有上层依赖
- 不依赖上层（没有循环依赖）
- 相对稳定，不经常改
- 可以独立测试

**依赖关系：**
- 基础设施层 → 无（最底层）
- 配置层 → 依赖基础设施层（配置存储是基础设施的一部分）
- 运行时层 → 依赖基础设施层（交易日历、数据提供者、公式引擎）
- 接口层 → 依赖基础设施层（日志、配置）

### 4.3 第二层：配置层（Config Layer）

**定义：定义系统的行为和参数，分类型配置和实例配置。**

**组成：**

| 分类 | 表名 | 职责 | 修改频率 |
|------|------|------|---------|
| **类型配置（6张）** | | | |
| | node_type_table | 节点类型定义：身份、分类、结构属性 | 低（开发时） |
| | node_behavior_table | 节点行为定义：handler | 低（开发时） |
| | edge_type_table | 边类型定义：身份、分类、结构属性 | 低（开发时） |
| | edge_behavior_table | 边行为定义：handler + trigger_mode | 低（开发时） |
| | ui_type_table | UI类型定义：默认显示名、颜色、图标 | 低（开发时） |
| | operator_table / combine_table / formula_table | 算子、组合方式、指标公式类型 | 中（开发时） |
| **实例配置（5张）** | | | |
| | pool_instance_table | 股票池实例：节点列表、边列表、拓扑 | 高（用户设计时） |
| | node_instance_table | 具体节点实例：类型引用、参数值 | 高（用户设计时） |
| | edge_instance_table | 具体边实例：类型引用、filter配置 | 高（用户设计时） |
| | ui_instance_table | 实例UI：位置、大小、自定义名称 | 高（用户设计时） |
| | formula_instance_table | 用户自定义指标公式 | 中（用户设计时） |
| **时间配置（2张）** | | | |
| | period_table | 周期定义 | 低（开发时） |
| | trade_calendar_table | 交易日历配置 | 低（运维时） |

**特点：**
- 静态的（运行时不经常改，改了也是配置变更）
- 类型配置：开发时定义，很少改
- 实例配置：用户设计股票池时改，经常改
- 运行时层只读配置层，不写（热加载除外）

**依赖关系：**
- 配置层 → 依赖基础设施层（配置存储）
- 运行时层 → 依赖配置层（读配置）
- 接口层 → 依赖配置层（读配置、改配置）

### 4.4 第三层：运行时层（Runtime Layer）

**定义：系统运行时的状态和逻辑，是核心计算层。**

**组成：**

| 模块 | 表/组件 | 职责 |
|------|--------|------|
| **状态表（11张）** | | |
| | latest_tick | 最新tick数据（唯一真相源） |
| | stock_status_table | 股票状态（正常/停牌/数据不足/异常） |
| | node_stocks | 各节点当前股票列表 |
| | ttl_expiry_queue | TTL过期队列（最小堆） |
| | dirty_stocks | 脏股票集合（全局水位线） |
| | node_changes | 节点变化三集合（entered/exited/updated） |
| | edge_compare_results | 三态比较结果（True/False/None） |
| | edge_filter_results | filter最终结果 |
| | period_data | 各周期K线数据 |
| | period_confirmed_events | 周期确认事件队列 |
| | trade_calendar_cache | 交易日历缓存 |
| **计算引擎** | | |
| | 主事件循环 | 轮询 → 更新 → 计算 → 后处理 |
| | 脏驱动计算 | 只计算变脏的部分，干净的跳过 |
| | 拓扑排序 | 按拓扑序处理节点 |
| | filter执行器 | 三层组合：指标→比较→组合 |
| | 公式引擎调用 | 向量化批量计算指标 |
| **时间管理** | | |
| | tick_data_ts | tick级数据时间（股票级） |
| | bar_data_ts | bar级数据时间（股票×周期级） |
| | sys_ts / last_poll_ts / last_calc_ts | 系统时间（3种） |
| | 周期确认检测 | 数据时间跨过边界时触发 |
| | TTL管理 | 系统时间驱动的过期淘汰 |

**特点：**
- 动态的，运行时不断变化
- 是系统的核心计算层
- 只读配置层，不写配置层（热加载除外）
- 状态表是运行时的，重启就没了（或从持久化恢复）

**依赖关系：**
- 运行时层 → 依赖配置层（读配置） + 基础设施层（数据、公式、日历）
- 接口层 → 依赖运行时层（读状态、发命令）

### 4.5 第四层：接口层（Interface Layer）

**定义：和外部系统交互的接口，是最上层。**

**组成：**

| 接口类型 | 职责 | 例子 |
|---------|------|------|
| **前端API** | 前端页面调用的接口 | 获取股票池列表、保存股票池、获取运行状态 |
| **事件推送** | 主动推送事件给前端 | 股票入池/出池事件、预警事件、状态变化事件 |
| **WebSocket** | 实时双向通信 | 实时推送股票池变化、实时行情 |
| **REST API** | 第三方系统调用 | 查询股票池状态、手动触发计算 |
| **配置接口** | 配置管理接口 | 读取/修改配置、热加载 |

**特点：**
- 最上层，直接和外部交互
- 不直接做计算，调用运行时层
- 负责数据序列化、权限校验、参数校验
- 可以有多种接口实现（Web、桌面、移动端）

**依赖关系：**
- 接口层 → 依赖运行时层（读状态、发命令） + 配置层（读/写配置） + 基础设施层（日志）
- 无（最上层，没有其他层依赖它）

### 4.6 层间依赖关系总结

```
接口层（Interface）
    │
    │ 调用 / 订阅
    ▼
运行时层（Runtime）
    │
    │ 读取配置
    ▼
配置层（Config）
    │
    │ 使用基础设施
    ▼
基础设施层（Infrastructure）
```

**依赖规则：**
1. **只能从上到下依赖**：上层可以依赖下层，下层不能依赖上层
2. **不能跨层依赖**：接口层不能直接依赖基础设施层（要通过运行时层或配置层）
3. **不能循环依赖**：A依赖B，B不能依赖A
4. **每层只和相邻层交互**：减少耦合

**好处：**
- 分层清晰，职责明确
- 可以独立替换某一层（比如换接口层，运行时层不用动）
- 可以独立测试（基础设施层可以单独测）
- 新人上手快（从下到上理解）

### 4.7 各层的修改频率

| 层级 | 修改频率 | 谁来改 | 改什么 |
|------|---------|-------|--------|
| 基础设施层 | 很低（几个月一次） | 开发/运维 | 加新基础设施、性能优化 |
| 配置层（类型） | 低（几周一次） | 开发 | 加新节点类型、加新算子 |
| 配置层（实例） | 高（每天都可能改） | 用户/运营 | 设计股票池、改参数 |
| 运行时层 | 中（几周一次） | 开发 | bug修复、性能优化、加新功能 |
| 接口层 | 中（几周一次） | 前端/开发 | 加新页面、加新接口 |

**符合：越底层越稳定，越上层越灵活。**

---

## 五、配置表清单（v1.15 更新版）

### 5.1 类型配置表（6 张，开发时定义，低修改频率）

| # | 表名 | 维度 | 读时机 | 写时机 | 说明 |
|---|------|------|--------|--------|------|
| 1 | `node_type_table` | 节点类型 | 初始化、校验时 | 开发时 | 节点类型的身份、分类、结构属性（type_id / category / allowed_roles / has_stocks / max_in_edges / max_out_edges） |
| 2 | `node_behavior_table` | 节点行为 | 处理节点时查表 | 开发时 | 节点类型的 init/in/out/tick/alert handler |
| 3 | `edge_type_table` | 边类型 | 初始化、校验时 | 开发时 | 边类型的身份、分类、结构属性 |
| 4 | `edge_behavior_table` | 边行为 | 处理边时查表 | 开发时 | 边类型的 gate/filter/propagate handler + trigger_mode + default_trigger_period |
| 5 | `ui_type_table` | UI类型 | 渲染UI、获取默认值时 | 开发时 | 类型的默认显示名、颜色、图标、默认大小 |
| 6 | `operator_type_table` | 算子类型 | 计算时查表 | 开发时 | operator_table + combine_table + formula_table（算子/组合方式/指标公式的类型定义） |

### 5.2 实例配置表（5 张，用户设计时，高修改频率）

| # | 表名 | 维度 | 读时机 | 写时机 | 说明 |
|---|------|------|--------|--------|------|
| 1 | `pool_instance_table` | 股票池实例 | 加载股票池时 | 用户保存时 | 股票池实例的基本信息、节点列表、边列表、拓扑结构 |
| 2 | `node_instance_table` | 节点实例 | 加载节点、计算时 | 用户编辑时 | 具体节点实例：type_id引用、参数值 |
| 3 | `edge_instance_table` | 边实例 | 加载边、计算时 | 用户编辑时 | 具体边实例：type_id引用、filter配置、参数值 |
| 4 | `ui_instance_table` | UI实例 | 渲染UI时 | 用户拖动/编辑时 | 实例的UI：position / size / custom_name（覆盖类型默认） |
| 5 | `formula_instance_table` | 自定义指标 | 指标计算时 | 用户编辑时 | 用户自定义的指标公式（扩展 formula_table 的类型） |

### 5.3 时间配置表（2 张，基础设施）

| # | 表名 | 维度 | 读时机 | 写时机 | 说明 |
|---|------|------|--------|--------|------|
| 1 | `period_table` | 周期定义 | 周期管理、时间对齐时 | 开发时 | 各周期的长度、确认时机、聚合关系 |
| 2 | `trade_calendar_table` | 交易日历 | 所有时间计算前 | 运维时 | 交易日判断、交易时段、节假日、周末、午间休市 |

### 5.4 配置表汇总

| 层级 | 类别 | 表数 | 表名 |
|------|------|------|------|
| **配置层** | 类型配置 | 6 张 | node_type_table, node_behavior_table, edge_type_table, edge_behavior_table, ui_type_table, operator_type_table (operator+combine+formula) |
| | 实例配置 | 5 张 | pool_instance_table, node_instance_table, edge_instance_table, ui_instance_table, formula_instance_table |
| | 时间配置 | 2 张 | period_table, trade_calendar_table |
| **合计** | | **13 张** | |

**v1.14 → v1.15 配置表变化：**

| 版本 | 配置表数 | 变化 |
|------|---------|------|
| v1.14 | 11 张 | node_table + node_behavior_table + edge_behavior_table + ui_table + formula_table + operator_table + combine_table + period_table + trade_calendar_table + ...（类型实例混） |
| v1.15 | 13 张 | **类型/实例彻底分离**：类型表6张 + 实例表5张 + 时间表2张<br>新增：pool_instance_table, formula_instance_table<br>拆分：node_table → node_type_table + node_instance_table<br>拆分：ui_table → ui_type_table + ui_instance_table<br>重命名：更清晰的命名，明确是类型还是实例 |

---

## 六、运行时表清单（v1.15 更新版）

### 6.1 核心运行时表（8 张，不变）

| # | 表名 | 类型 | 读时机 | 写时机 | 说明 |
|---|------|------|--------|--------|------|
| 1 | `latest_tick` | Dict[code → bar_dict] | 公式引擎计算时读 | tick 开始时更新 | **唯一真相源**。所有股票的最新 tick 数据（数据时间） |
| 2 | `stock_status_table` | Dict[code → status_dict] | 指标计算/比较/传播前 | 数据更新时检测 | 每只股票的状态（正常/停牌/数据不足/异常） |
| 3 | `node_stocks` | Dict[nid → List[code]] | propagate 读写、filter 读 | 边执行/TTL过期后写 | 各节点当前股票列表 |
| 4 | `ttl_expiry_queue` | Heap[(expire_ts, nid, code)] | TTL检查时弹出 | 股票入池时插入 | TTL 过期队列。按过期时间排序的最小堆（系统时间） |
| 5 | `dirty_stocks` | Set[code] | 计算 node_changes 时用 | tick 开始时收集 / tick 结束时清空 | **全局水位线**。本 tick 数据更新了的股票集合 |
| 6 | `node_changes` | Dict[nid → {entered, exited, updated}] | 执行循环读（增量处理） | propagate/TTL/数据更新时写入 | **节点变化三集合**。entered=新进入，exited=离开，updated=还在但数据更新了 |
| 7 | `edge_compare_results` | Dict[eid → Dict[code → True/False/None]] | 集合运算层读 | 比较层写（增量更新） | **三态比较结果**。None=数据不足/停牌/异常。新入池股票先设 None，再计算 |
| 8 | `edge_filter_results` | Dict[eid → Set[code] 或 List[code]] | propagate 读 | 集合运算层写 | 排名型用有序列表，独立型用 Set。只放通过的，None/False 都不在 |

### 6.2 时间相关运行时表（5 个时间戳 + 2 张表）

**数据时间（2种，股票级）：**

| 时间戳 | 类型 | 读时机 | 写时机 | 说明 |
|--------|------|--------|--------|------|
| `tick_data_ts[code]` | Dict[code → timestamp] | 数据时间判断、超时检测 | 收到新tick时 | 每只股票最新 tick 的时间戳（股票级） |
| `bar_data_ts[code][period]` | Dict[code → Dict[period → ts]] | 周期确认、K线级时间触发 | K线更新/确认时 | 每只股票每个周期K线的最新时间戳（股票×周期级） |

**系统时间（3种，全局）：**

| 时间戳 | 类型 | 读时机 | 写时机 | 说明 |
|--------|------|--------|--------|------|
| `sys_ts` | timestamp（不存储，每次获取） | 超时、TTL、日志、性能 | — | 当前系统时间（单调递增） |
| `last_poll_ts` | timestamp | 轮询间隔判断、超时检测 | 每次轮询后 | 上次轮询数据的系统时间 |
| `last_calc_ts` | timestamp | 计算卡住判断、性能统计 | 每次计算后 | 上次完成计算的系统时间 |

**时间相关表：**

| # | 表名 | 类型 | 读时机 | 写时机 | 说明 |
|---|------|------|--------|--------|------|
| 1 | `period_data` | Dict[period → {completed_bars, current_bar}] | 指标计算、周期确认时 | 数据更新、周期确认时 | 各周期的已完成K线和当前未完成K线（数据时间） |
| 2 | `period_confirmed_events` | Queue[event] | 时间驱动边处理时 | 周期确认时 | 周期确认事件队列（数据时间驱动） |

### 6.3 运行时表汇总

| 类别 | 表数/数量 | 名称 |
|------|----------|------|
| 核心运行时表 | 8 张 | latest_tick, stock_status_table, node_stocks, ttl_expiry_queue, dirty_stocks, node_changes, edge_compare_results, edge_filter_results |
| 数据时间戳 | 2 种 | tick_data_ts, bar_data_ts[period] |
| 系统时间戳 | 3 种 | sys_ts, last_poll_ts, last_calc_ts |
| 时间相关表 | 2 张 | period_data, period_confirmed_events |
| **合计** | **10张表 + 5个时间戳** | |

---

## 七、核心循环伪代码（v1.15 更新版）

### 7.1 主循环：轮询 + 脏驱动 + 五时间戳 + 类型实例查表

```python
# ============================================================
#  v1.15 核心循环伪代码（轮询数据 + 脏驱动计算 + 五时间戳 + 类型实例分离）
# ============================================================

# --- 初始化 ---
# 加载基础设施层
trade_calendar = load_trade_calendar('trade_calendar_table')
data_provider = DataProvider()
formula_engine = FormulaEngine()

# 加载类型配置（开发时定义，低修改频率）
node_type_table = load_table('node_type_table')
node_behavior_table = load_table('node_behavior_table')
edge_type_table = load_table('edge_type_table')
edge_behavior_table = load_table('edge_behavior_table')
ui_type_table = load_table('ui_type_table')
operator_table = load_table('operator_table')
combine_table = load_table('combine_table')
formula_table = load_table('formula_table')
period_table = load_table('period_table')

# 注册 handler（L1 分发表驱动）
handler_registry = register_all_handlers()

# 加载实例配置（用户设计时，高修改频率）
pool_instance = load_pool_instance('pool_1')
node_instances = load_node_instances('pool_1')
edge_instances = load_edge_instances('pool_1')
ui_instances = load_ui_instances('pool_1')

# 初始化运行时状态
latest_tick = {}
stock_status_table = {}
node_stocks = init_node_stocks(node_instances)
ttl_expiry_queue = []
dirty_stocks = set()
node_changes = init_node_changes(node_instances)
edge_compare_results = {}
edge_filter_results = {}
period_data = init_period_data(period_table)
period_confirmed_events = Queue()

# 初始化时间戳
tick_data_ts = {}  # Dict[code → ts]
bar_data_ts = {}   # Dict[code → Dict[period → ts]]
last_poll_ts = 0   # 上次轮询时间（系统时间）
last_calc_ts = 0   # 上次计算时间（系统时间）

# 编译期：拓扑排序
topo_order = build_topo_order(node_instances, edge_instances)

# --- 主循环（轮询模型）---
tick_interval = 1.0  # 秒，系统时间间隔

while running:
    # 1. 等待下一个 tick（系统时间）
    await asyncio.sleep(tick_interval)
    sys_ts = time.time()  # 当前系统时间（处理时间）
    
    if paused:
        continue
    
    # 2. 轮询获取最新数据
    tick_data = data_provider.poll_latest_data()
    last_poll_ts = sys_ts  # 更新上次轮询时间
    
    # 3. 检查是不是交易时间（用交易日历 + 数据时间）
    if tick_data:
        sample_code = next(iter(tick_data.keys()))
        sample_time = get_data_time(tick_data[sample_code])  # 数据时间
        is_trading = trade_calendar.is_trading_time(sample_time)
    else:
        is_trading = trade_calendar.is_trading_time(datetime.fromtimestamp(sys_ts))
    
    # 4. 更新数据层 + 标记脏股票 + 更新股票状态 + 更新数据时间戳
    dirty_stocks.clear()
    for code, new_bar in tick_data.items():
        # 更新 tick 数据时间（股票级）
        new_ts = get_data_time(new_bar)
        if tick_data_ts.get(code) != new_ts:
            tick_data_ts[code] = new_ts
        
        # 更新 latest_tick（唯一真相源）
        if latest_tick.get(code) != new_bar:
            latest_tick[code] = new_bar
            dirty_stocks.add(code)
        
        # 检测并更新股票状态（停牌/新股/异常）
        old_status = stock_status_table.get(code, {}).get('status', 'normal')
        new_status = detect_stock_status(code, new_bar)
        if old_status != new_status:
            update_stock_status(code, new_status)
            dirty_stocks.add(code)  # 状态变化也算变脏
    
    # 5. 更新 bar 数据时间 + 检测周期确认事件（数据时间驱动）
    period_confirmed_events.clear()
    if is_trading and dirty_stocks:
        for period in period_table:
            # 更新该周期的未完成K线 + bar_data_ts
            update_current_bar(period, tick_data, bar_data_ts)
            
            # 检查该周期是否有K线确认了（数据时间跨过边界）
            confirmed_stocks = period_bar_confirmed(period, bar_data_ts)
            if confirmed_stocks:
                # 把已完成的K线移到 completed_bars
                confirm_current_bar(period, confirmed_stocks)
                # 发周期确认事件
                period_confirmed_events.put({
                    'period': period,
                    'confirmed_stocks': confirmed_stocks,
                    'confirmed_time': get_confirmed_bar_time(period),  # 数据时间
                })
                # 开始新的未完成K线
                start_new_current_bar(period, confirmed_stocks)
    
    # 6. 超时检测（用系统时间）
    # 如果 30 秒没轮询到数据，标记为断线
    check_timeout(sys_ts, last_poll_ts, timeout=30)
    
    # 7. 如果没有数据变化 且 没有周期确认事件，跳过计算（脏驱动）
    if not dirty_stocks and period_confirmed_events.empty():
        last_calc_ts = sys_ts  # 就算没计算，也更新一下
        continue
    
    # 8. 计算每个节点的变化（node_changes）
    compute_all_node_changes(dirty_stocks, node_stocks, node_changes)
    
    # 9. 按拓扑序处理脏节点（L1表驱动 + 配置化）
    for nid in topo_order:
        if not is_node_dirty(nid, node_changes):
            continue
        
        # 第一步：查实例表，得到 type_id 和实例参数
        node_inst = node_instances[nid]
        type_id = node_inst['type_id']
        instance_params = node_inst.get('params', {})
        
        # 第二步：查类型表，得到行为定义（handler）
        type_behavior = node_behavior_table[type_id]
        handler_name = type_behavior['out_edge_handler']
        
        if handler_name:
            handler = handler_registry[handler_name]
            # 合并类型默认值和实例参数，调用 handler
            merged_params = merge_params(type_behavior.get('default_params', {}), instance_params)
            handler(nid, node_inst, merged_params, node_changes[nid])
    
    # 10. 处理时间驱动的边（周期确认事件触发，数据时间）
    while not period_confirmed_events.empty():
        event = period_confirmed_events.get()
        process_time_driven_edges(event, edge_instances, edge_behavior_table)
    
    # 11. 后处理（PK 排名 / 分析角度 / 预警）
    post_process(node_stocks, node_changes, stock_status_table)
    
    # 12. TTL 过期检查（用系统时间）
    process_ttl_expiry(sys_ts, ttl_expiry_queue, node_stocks, node_changes)
    
    # 13. 更新上次计算时间
    last_calc_ts = time.time()
    
    # 14. 清脏，为下一轮做准备
    clear_all_dirty(dirty_stocks, node_changes)
```

### 7.2 filter 配置化执行（L1 + 配置化，不是L2）

```python
def execute_filter_configurable(edge_inst, stock_codes):
    """filter 配置化执行（L1分发表驱动 + 配置化应用，不是L2）
    
    流程：
    1. 查边实例表，得到 filter_config（实例参数）
    2. 查类型表（operator_table / combine_table / formula_table），得到 handler
    3. 三层组合：指标 → 比较 → 组合
    
    注意：
    - 加新 filter 实例 = 改配置，不用写代码（配置化）
    - 加新算子 / 加新组合方式 = 要写代码（L1）
    - 这是配置化，不是 L2
    """
    # 第一步：从实例配置得到 filter_config
    filter_config = edge_inst['filter_config']
    conditions = filter_config['conditions']
    combine_cfg = filter_config['combine']
    
    # ========== 第一层：指标计算（查 formula_table 类型表）==========
    indicator_results = {}
    for cond in conditions:
        formula_id = cond['formula_id']
        # 查类型表（formula_table）
        formula_def = formula_table[formula_id]
        
        # 公式引擎批量计算所有股票的指标值（向量化）
        values = formula_engine.eval_batch(
            formula=formula_def['formula'],
            symbols=stock_codes,
            period=edge_inst.get('period', '1d'),
            params=cond.get('formula_params', {}),
        )
        indicator_results[cond['condition_id']] = values
    
    # ========== 第二层：比较判断（查 operator_table 类型表）==========
    compare_results = {}
    for cond in conditions:
        cond_id = cond['condition_id']
        operator_id = cond['operator_id']
        # 查类型表（operator_table）
        op_def = operator_table[operator_id]
        # 调 handler（L1 分发）
        op_func = handler_registry[op_def['handler']]
        params = cond['params']
        
        cond_results = {}
        for code in stock_codes:
            # 股票状态检查（三态逻辑）
            status = stock_status_table.get(code, {}).get('status', 'normal')
            if status != 'normal':
                cond_results[code] = None  # 不确定
                continue
            
            # 指标值检查
            indicator_val = indicator_results[cond_id].get(code)
            if indicator_val is None:
                cond_results[code] = None  # 不确定
                continue
            
            # 比较判断（查表调算子）
            cond_results[code] = op_func(indicator_val, params)
        
        compare_results[cond_id] = cond_results
    
    # ========== 第三层：组合运算（查 combine_table 类型表）==========
    combine_id = combine_cfg['combine_id']
    # 查类型表（combine_table）
    combine_def = combine_table[combine_id]
    # 调 handler（L1 分发）
    combine_func = handler_registry[combine_def['handler']]
    combine_params = combine_cfg['params']
    
    filter_result = combine_func(compare_results, stock_codes, combine_params)
    
    return filter_result
```

**关键点：**
1. 三层结构清晰：指标 → 比较 → 组合
2. 每层都查类型表：formula_table / operator_table / combine_table
3. 查表后调 handler：这是 L1 分发表驱动
4. 加新实例不用写代码：这是配置化
5. 加新算子/新组合方式要写代码：L1 的事
6. 三态逻辑贯穿：每层都处理 None（不确定）
7. 向量化计算：指标层批量计算，性能高

### 7.3 时间驱动的边处理（数据时间 + 股票级粒度）

```python
def process_time_driven_edges(period_event, edge_instances, edge_behavior_table):
    """处理时间驱动的边（周期确认事件触发，用数据时间）
    
    时间触发用数据时间（市场时间），不是系统时间
    数据时间是股票级的，每只股票可能不一样
    """
    period = period_event['period']
    confirmed_stocks = period_event['confirmed_stocks']  # 哪些股票的周期确认了
    confirmed_time = period_event['confirmed_time']  # 数据时间
    
    # 找出所有时间驱动、且周期匹配的边
    for eid, edge_inst in edge_instances.items():
        # 第一步：查实例表，得到 type_id
        edge_type_id = edge_inst.get('edge_type', 'default_edge')
        
        # 第二步：查类型表，得到行为定义
        behavior_def = edge_behavior_table.get(edge_type_id, {})
        
        # 不是时间驱动的，跳过
        if behavior_def.get('trigger_mode') != 'time_driven':
            continue
        
        # 周期不匹配的，跳过
        edge_period = edge_inst.get('trigger_period', behavior_def.get('default_trigger_period'))
        if edge_period != period:
            continue
        
        # 交易日历检查（确认时间是不是在交易时段内）
        if not trade_calendar.is_trading_time(confirmed_time):
            continue
        
        # 执行这条边（只处理周期确认了的股票，股票级粒度）
        process_edge_time_driven(eid, edge_inst, confirmed_stocks, confirmed_time)
```

### 7.4 五时间戳使用对照表

| 功能 | 用什么时间戳 | 代码位置 |
|------|-------------|---------|
| tick 等待间隔 | sys_ts | `await asyncio.sleep(tick_interval)` |
| 上次轮询时间 | last_poll_ts | 轮询结束后更新 |
| 上次计算时间 | last_calc_ts | 计算结束后更新 |
| 超时检测 | sys_ts - last_poll_ts | `check_timeout(sys_ts, last_poll_ts, timeout=30)` |
| TTL 过期 | sys_ts | `process_ttl_expiry(sys_ts, ...)` |
| tick 数据时间 | tick_data_ts[code] | 每只股票最新 tick 时间 |
| K线数据时间 | bar_data_ts[code][period] | 每只股票每周期K线时间 |
| 周期确认 | bar_data_ts[code][period] | `period_bar_confirmed(period, bar_data_ts)` |
| 时间驱动触发 | bar_data_ts（周期确认事件） | `process_time_driven_edges(event)` |
| 指标计算 | tick_data_ts / bar_data_ts | `formula_engine.eval_batch(...)` |
| 交易时间判断 | 数据时间（优先） | `trade_calendar.is_trading_time(...)` |
| 日志时间戳 | sys_ts | 日志记录时 |
| 性能统计 | last_calc_ts - last_poll_ts | 计算耗时 |
| 判断计算卡住 | sys_ts - last_calc_ts | 健康检查 |

---

## 八、功能-表操作对应表（v1.15 更新版）

### 8.1 基础设施层

| 功能 | 读什么表/模块 | 写什么表/模块 | 计算 |
|------|-------------|-------------|------|
| **交易日判断** | trade_calendar_table | — | 排除周末、节假日，加上额外交易日 |
| **交易时段判断** | trade_calendar_table（trading_sessions） | — | 当前时间在不在任一交易时段内 |
| **数据轮询** | data_provider | latest_tick + tick_data_ts | 主动 pull 最新数据，对比变化 |
| **指标计算** | formula_table + formula_engine | formula_engine内部表 | 公式引擎向量化批量计算 |
| **配置加载** | config_store | — | 加载、缓存、校验配置表 |

### 8.2 配置层-类型配置

| 功能 | 读什么表 | 写什么表 | 说明 |
|------|---------|---------|------|
| **节点类型定义** | node_type_table | node_type_table | 开发时定义节点类型的身份、结构属性 |
| **节点行为定义** | node_behavior_table | node_behavior_table | 开发时定义节点类型的 handler |
| **边类型定义** | edge_type_table | edge_type_table | 开发时定义边类型的身份、结构属性 |
| **边行为定义** | edge_behavior_table | edge_behavior_table | 开发时定义边类型的 handler + trigger_mode |
| **UI类型定义** | ui_type_table | ui_type_table | 开发时定义类型的默认UI（颜色、图标、显示名） |
| **算子类型定义** | operator_table / combine_table / formula_table | operator_table / combine_table / formula_table | 开发时定义算子/组合方式/指标公式 |
| **周期定义** | period_table | period_table | 开发时定义各周期的长度、确认时机 |

### 8.3 配置层-实例配置

| 功能 | 读什么表 | 写什么表 | 说明 |
|------|---------|---------|------|
| **股票池创建/保存** | pool_instance_table | pool_instance_table | 用户创建/保存股票池实例 |
| **节点实例编辑** | node_instance_table | node_instance_table | 用户编辑具体节点的参数 |
| **边实例编辑** | edge_instance_table | edge_instance_table | 用户编辑具体边的 filter 配置 |
| **UI实例编辑** | ui_instance_table | ui_instance_table | 用户拖动节点、改大小、改显示名 |
| **自定义指标** | formula_instance_table | formula_instance_table | 用户自定义指标公式 |
| **拓扑结构维护** | pool_instance_table | pool_instance_table | 节点列表、边列表、连接关系 |

### 8.4 运行时层-主循环

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **轮询等待** | tick_interval 配置 | — | sleep(tick_interval)，系统时间 |
| **数据轮询** | data_provider | latest_tick + tick_data_ts + dirty_stocks | 主动 pull 最新数据，对比变化，标记脏股票 |
| **交易时间判断** | trade_calendar_table | — | 先判断是不是交易日，再判断在不在交易时段 |
| **股票状态检测** | 数据 + 状态检测规则 | stock_status_table | 检测每只股票的状态（正常/停牌/数据不足/异常） |
| **超时检测** | sys_ts + last_poll_ts | — | sys_ts - last_poll_ts > 阈值，算超时 |
| **周期更新** | period_table + 最新数据 | period_data + bar_data_ts | 更新各周期的未完成K线，数据时间驱动 |
| **周期确认事件** | period_data + bar_data_ts | period_confirmed_events | 数据时间跨过周期边界，发确认事件 |
| **脏驱动跳过** | dirty_stocks + period_confirmed_events | — | 如果没数据变化 且 没周期确认，跳过计算 |
| **节点变化计算** | dirty_stocks + node_stocks | node_changes | entered/exited 来自传播，updated = dirty_stocks ∩ node_stocks |
| **拓扑序处理** | node_changes + 拓扑序 | 各层状态表 | 按拓扑序处理脏节点，干净节点跳过 |
| **更新last_poll_ts** | sys_ts | last_poll_ts | 轮询结束后更新 |
| **更新last_calc_ts** | sys_ts | last_calc_ts | 计算结束后更新 |

### 8.5 运行时层-节点处理（类型+实例查表）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **节点实例信息** | node_instance_table | — | 查表得到 type_id、实例参数 |
| **节点类型信息** | node_type_table + node_instance_table.type_id | — | 通过 type_id 查类型表，得到类型定义 |
| **节点行为判断** | node_behavior_table + type_id | — | 查表得到 handler，不是 if-else 判断（L1分发） |
| **初始化 handler** | node_behavior_table.init_handler | node_stocks | 查表调用对应的初始化函数 |
| **入边 handler** | node_behavior_table.in_edge_handler | node_changes | 查表调用对应的入边处理函数 |
| **出边 handler** | node_behavior_table.out_edge_handler | 各边状态表 | 查表调用对应的出边处理函数 |
| **UI渲染（类型默认值）** | ui_type_table + type_id | — | 查表得到默认显示名、颜色、图标 |
| **UI渲染（实例覆盖）** | ui_instance_table + nid | — | 实例的自定义名称、位置、大小 |
| **脏节点判断** | node_changes[nid] | — | entered/exited/updated 全空就是干净的 |

### 8.6 运行时层-边执行层（配置化filter）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **边实例信息** | edge_instance_table | — | 查表得到 type_id、filter_config、实例参数 |
| **边类型信息** | edge_type_table + edge_instance_table.type_id | — | 通过 type_id 查类型表 |
| **边触发模式判断** | edge_behavior_table.trigger_mode | — | 数据驱动还是时间驱动（查类型表） |
| **时间条件检查** | period_confirmed_events + trade_calendar_table | — | 周期确认事件触发 + 交易时段检查 |
| **股票状态过滤** | stock_status_table | — | 停牌/数据不足/异常的，按保守策略处理 |
| **第一层：指标计算** | formula_table（类型表） + 公式引擎 | 公式引擎内部表 | 查类型表得到公式，公式引擎批量计算 |
| **第二层：比较判断** | operator_table（类型表） + 指标值 + 股票状态 | edge_compare_results | 查类型表调算子函数。三态：True/False/None |
| **第三层：组合运算** | combine_table（类型表） + 比较结果 | edge_filter_results | 查类型表调组合运算函数。and/or/top_n 等（三态逻辑） |
| **propagate** | combine_table（propagate类） + filter 结果 | node_stocks + node_changes | 查类型表调传播函数。保守策略：None 的不入池 |
| **事件发射** | node_changes[tid] | 事件队列 | entered/exited 直接发事件 |

### 8.7 运行时层-时间模型（五时间戳）

| 功能 | 用什么时间戳 | 读什么表 | 写什么表 |
|------|-------------|---------|---------|
| **tick数据时间** | tick_data_ts[code] | — | 收到新tick时更新 |
| **K线数据时间** | bar_data_ts[code][period] | — | K线更新/确认时更新 |
| **当前系统时间** | sys_ts | — | 每次需要时获取 |
| **上次轮询时间** | last_poll_ts | — | 每次轮询后更新 |
| **上次计算时间** | last_calc_ts | — | 每次计算后更新 |
| **周期确认检测** | bar_data_ts | period_data | period_confirmed_events |
| **时间对齐** | bar_data_ts + 对齐策略 | — | — |
| **超时检测** | sys_ts - last_poll_ts | — | — |
| **TTL过期** | sys_ts | ttl_expiry_queue | node_stocks + node_changes |
| **性能统计** | last_calc_ts - last_poll_ts | — | — |
| **计算卡住判断** | sys_ts - last_calc_ts | — | — |

### 8.8 运行时层-TTL淘汰层

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| 股票入池记录 TTL | 边实例配置 | ttl_expiry_queue 插入 | expire_ts = sys_ts + ttl_sec（系统时间） |
| TTL 过期检查 | ttl_expiry_queue + sys_ts | 弹出过期项 | 最小堆：堆顶过期就弹出（系统时间） |
| 过期股票移除 | node_stocks[nid] | node_stocks[nid] | 从节点移除 |
| **过期触发级联** | — | node_changes[nid].exited.add(code) | 加入 exited 集合 |

### 8.9 接口层

| 功能 | 读什么层 | 写什么层 | 说明 |
|------|---------|---------|------|
| **获取股票池列表** | 配置层（实例配置） | — | 读 pool_instance_table |
| **保存股票池** | — | 配置层（实例配置） | 写 pool_instance_table + node_instance_table + edge_instance_table + ui_instance_table |
| **获取运行状态** | 运行时层 | — | 读 node_stocks + stock_status_table + 时间戳 |
| **手动触发计算** | — | 运行时层 | 触发一次计算循环 |
| **事件订阅** | 运行时层 | — | 订阅 node_changes 事件 |
| **读取配置** | 配置层 | — | 读类型配置或实例配置 |
| **修改配置** | — | 配置层 | 写配置 + 热加载 |

---

## 九、概念变化对照表（v1.14 → v1.15）

### 9.1 表驱动层级：从"L2"到诚实化

| v1.14（夸大） | v1.15（诚实） | 理由 |
|--------------|--------------|------|
| "L2组合表驱动落地" | **L1分发表驱动 + 配置化应用** | 实事求是：配置化不是L2，加新算子还是要写代码 |
| "加新filter不改代码"（口号） | **加新filter实例不改代码（配置化），加新算子要写代码（L1）** | 说清楚什么不改、什么要改，不模糊 |
| "真正的L2" | **L2暂不做，股票池不需要** | L2是组合引擎通用、加新行为不改代码，目前做不到也不需要 |
| 三层组合=L2 | **三层组合=配置化，是L1的应用** | 配置化是好东西，但它不是L2。是什么就说什么 |

### 9.2 配置分类：从混合到类型/实例分离

| v1.14（类型实例混） | v1.15（彻底分离） | 理由 |
|-------------------|------------------|------|
| node_table（既存类型又存实例） | **node_type_table + node_instance_table** | 类型是"类"，实例是"对象"，完全不同的概念 |
| ui_table（类型和实例UI混） | **ui_type_table + ui_instance_table** | 类型的默认UI vs 实例的自定义UI，不一样 |
| 配置表11张（混） | **配置表13张（类型6 + 实例5 + 时间2）** | 按层级分类，更清晰 |
| 修改频率模糊 | **类型低（开发时）、实例高（用户设计时）** | 两者修改频率差很多，混在一起不好维护 |

### 9.3 时间模型：从双时间到五时间戳

| v1.14（粗粒度） | v1.15（细粒度） | 理由 |
|----------------|----------------|------|
| 数据时间（一个模糊概念） | **tick_data_ts[code] + bar_data_ts[code][period]** | 数据时间分tick级和bar级，且是股票级粒度（每只不一样） |
| 系统时间（一个模糊概念） | **sys_ts + last_poll_ts + last_calc_ts** | 系统时间也分好几种，用途不一样 |
| 2个时间概念 | **5个时间戳** | 每个时间戳都有明确的用途、更新时机、维护者 |
| "数据时间是全局的" | **数据时间是股票级的，每只股票可能不一样** | 之前的误解，现在纠正 |

### 9.4 整体架构：从无到四层

| v1.14（没有整体分层） | v1.15（四层架构） | 理由 |
|---------------------|------------------|------|
| 没有整体架构图 | **基础设施层 → 配置层 → 运行时层 → 接口层** | 建立清晰的分层架构，每层职责边界明确 |
| 各模块关系混乱 | **从上到下依赖，不能反向，不能跨层** | 分层清晰，耦合度低，可维护性好 |
| 修改频率不明确 | **越底层越稳定，越上层越灵活** | 符合架构设计的一般原则 |

### 9.5 表数量变化

| 版本 | 配置表数 | 核心运行时表数 | 变化 |
|------|---------|---------------|------|
| v1.13 | 9 张 | 8 张 | — |
| v1.14 | 11 张 | 8 张 | +formula_table + trade_calendar_table |
| **v1.15** | **13 张** | **8 张** | **类型/实例分离：类型6 + 实例5 + 时间2 = 13张**<br>**+五时间戳（不是表，是变量）** |

---

## 十、实现路线图（v1.15）

### 阶段一：表驱动层级诚实化（P0）

1. **文档和命名更新**
   - 所有文档里的"L2"改成"配置化"
   - 明确说明：L1分发表驱动 + 配置化应用，L2暂不做
   - 代码里的命名也要对应调整（如果有的话）

2. **明确配置化的边界**
   - 什么是配置化：加新实例不用写代码
   - 什么是L1：加新类型/新算子要写代码
   - 写清楚，不混淆

### 阶段二：类型配置 vs 实例配置分离（P0）

1. **拆分 node_table → node_type_table + node_instance_table**
   - node_type_table：类型定义（开发时）
   - node_instance_table：实例配置（用户设计时）
   - 实例通过 type_id 引用类型

2. **拆分 ui_table → ui_type_table + ui_instance_table**
   - ui_type_table：类型的默认UI（颜色、图标、默认显示名）
   - ui_instance_table：实例的UI（位置、大小、自定义显示名）

3. **新增 pool_instance_table**
   - 股票池实例的基本信息
   - 节点列表、边列表、拓扑结构

4. **新增 formula_instance_table（可选）**
   - 用户自定义的指标公式
   - 扩展 formula_table 的类型

5. **更新查表流程**
   - 先查实例表，得到 type_id
   - 再查类型表，得到行为定义
   - 合并类型默认值和实例参数

### 阶段三：双时间模型细化（P0）

1. **明确五个时间戳的定义**
   - tick_data_ts[code]：tick级数据时间（股票级）
   - bar_data_ts[code][period]：bar级数据时间（股票×周期级）
   - sys_ts：当前系统时间
   - last_poll_ts：上次轮询时间
   - last_calc_ts：上次计算时间

2. **在代码中明确命名**
   - 所有时间相关变量，明确是数据时间还是系统时间
   - 数据时间要明确是 tick 还是 bar，是哪只股票的
   - 系统时间要明确是当前、上次轮询、还是上次计算

3. **更新时间相关逻辑**
   - 超时检测用 sys_ts - last_poll_ts
   - TTL 用 sys_ts
   - 周期确认用 bar_data_ts
   - 性能统计用 last_calc_ts - last_poll_ts

4. **纠正数据时间是全局的误解**
   - 明确数据时间是股票级的
   - 每只股票的 tick_data_ts / bar_data_ts 可能不一样

### 阶段四：整体架构梳理（P1）

1. **按四层架构整理代码结构**
   - 基础设施层：交易日历、数据提供者、公式引擎、配置存储
   - 配置层：类型配置 + 实例配置
   - 运行时层：状态表、事件循环、脏驱动、计算引擎
   - 接口层：前端API、事件推送

2. **明确层间依赖关系**
   - 从上到下依赖，不能反向
   - 不跨层依赖
   - 每层只和相邻层交互

3. **文档更新**
   - 架构图更新
   - 每层职责说明
   - 依赖关系图

### 阶段五：测试验证（P0）

1. **诚实化验证**
   - 文档里没有夸大的"L2"说法
   - 明确说明什么是配置化、什么是L1
   - 加新算子确实要写代码（L1）
   - 加新filter实例确实不用写代码（配置化）

2. **类型实例分离验证**
   - 类型表和实例表彻底分开
   - 实例通过 type_id 引用类型
   - 改类型不影响实例数量
   - 改实例不影响类型定义
   - 一个类型可以有多个实例

3. **五时间戳验证**
   - 每个时间戳都有明确的用途
   - 每个时间戳的更新时机正确
   - 数据时间是股票级的（不是全局的）
   - 超时/TTL用系统时间，时间触发用数据时间

4. **架构分层验证**
   - 四层架构清晰
   - 依赖关系正确（从上到下）
   - 没有循环依赖
   - 没有跨层依赖

---

## 十一、统计总结（v1.14 → v1.15）

### 11.1 概念数量变化

| 统计项 | v1.14 | v1.15 | 变化 |
|--------|------|-------|------|
| 配置表数 | 11 张 | **13 张** | **+2（类型/实例分离后重新分类）** |
| 核心运行时表 | 8 张 | 8 张 | 不变 |
| 时间戳数量 | 2个（数据/系统，模糊） | **5个（2数据+3系统，明确）** | **+3，粒度细化** |
| 表驱动层级 | "L2组合"（夸大） | **L1分发 + 配置化，L2暂不做** | 诚实化，是什么说什么 |
| 配置分类 | 类型实例混 | **类型配置 + 实例配置，彻底分离** | 正交拆分，职责清晰 |
| 整体架构 | 没有分层 | **四层架构** | 建立清晰的分层架构 |
| 数据时间粒度 | 全局一个 | **股票级（每只可能不一样）** | 纠正误解，更准确 |

### 11.2 为什么是 v1.15？

**v1.15 是"诚实 + 分离 + 细化 + 架构"的版本：**

1. **诚实**：表驱动层级诚实化——是什么就说什么，配置化就是配置化，不吹牛叫L2
2. **分离**：类型配置和实例配置彻底分离——两个完全不同的概念，各归各的
3. **细化**：双时间模型细化——从2个模糊概念到5个明确的时间戳，每个都清清楚楚
4. **架构**：建立四层整体架构——每层职责、边界、依赖关系都明确

```
演进路径：
  v1.5 ~ v1.6：概念精简阶段（从多到少，先做对）
  v1.7：性能优化阶段（股票级水位线，增量计算）
  v1.8：状态显式化阶段（三层状态表，每层结果都有表）
  v1.9：架构完善阶段（一致性 + 事件驱动 + 生命周期）
  v1.10：深度澄清阶段（并发性能 + 批次定位 + 三态传播）
  v1.11：根本性纠错（删除时间批次化，零延迟事件模型）
  v1.12：诚实 + 落地 + 升级（轮询+脏驱动/性能落地/行为表）
  v1.13：表驱动分层 + 时间模型 + 实盘边界（诚实化/补全/正交）
  v1.14：L2组合驱动落地 + 双时间模型 + 交易日历 + 正交边界
  v1.15：诚实化 + 类型实例分离 + 时间细化 + 整体架构
  v2.0：完整稳定版（所有功能完善，文档齐全）
```

**v1.15 解决的四个核心问题：**
1. **诚实问题**：不吹牛，是什么就说什么——配置化就是配置化，不是L2
2. **混淆问题**：类型配置 vs 实例配置——两个完全不同的概念，彻底分开
3. **模糊问题**：时间模型细化——从2个模糊概念到5个明确时间戳
4. **架构问题**：四层整体架构——每层职责边界清晰，依赖关系明确

诚实比吹牛重要，分离比混杂重要，细化比模糊重要，清晰比混乱重要。

### 11.3 一句话总结

**v1.15：诚实化表驱动层级（L1+配置化，不吹牛L2）+ 类型实例彻底分离（两个完全不同的概念）+ 双时间模型细化（5个时间戳，每个都清清楚楚）+ 四层整体架构（基础设施→配置→运行时→接口）。**
