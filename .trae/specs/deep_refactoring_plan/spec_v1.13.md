# 股票池深度重构规划 v1.13

> 版本主题：表驱动分层 + 时间模型 + 实盘边界
> 设计原则：诚实不吹牛、正交拆分、三态逻辑、保守策略
> 目标：诚实定义表驱动三层级（L1/L2/L3），补充时间模型设计（多周期同步/周期边界），拆分 node_type_table 恢复正交性，补充实盘边界场景处理（停牌/新股/容错）

---

## v1.12 → v1.13 变更摘要表

**变更日期：** 2026-07-01

| # | 变更项 | v1.12 | v1.13 | 本质变化 |
|---|--------|------|-------|---------|
| 1 | **表驱动层级诚实化** | "加新节点不改代码"（吹牛） | **L1 分发 + 部分 L2 组合（诚实）** | 明确定义 L1/L2/L3 三层级，承认当前只有 L1（表替代 if-else）+ 部分 L2（算子/组合/传播），不吹牛 |
| 2 | **时间模型** | 几乎空白（只提了 tick_interval） | **完整时间模型：多周期同步 + 周期边界 + 未完成K线 + 周期确认事件** | 补充股票计算的核心复杂度——时间模型，之前完全忽略了 |
| 3 | **node_type_table 拆分** | 1 张粗表（混了行为/UI/属性） | **拆成 4 张正交表：node_table + node_behavior_table + edge_behavior_table + ui_table** | 恢复正交性：每个表管一个维度，改一个维度不影响其他维度 |
| 4 | **实盘边界场景** | 几乎空白（默认数据都正常） | **停牌/新股/数据异常 完整处理方案** | 补充真实交易场景的边界处理：三态逻辑、股票状态独立、保守策略 |
| 5 | **运行时表新增** | 7 张核心运行时表 | **8 张（+1 stock_status_table）** | 新增股票状态表，每只股票独立状态（正常/停牌/数据不足/异常） |
| 6 | **配置表清单更新** | 4 张行为表 | **6 张（拆分 node_type_table）** | node_type_table → 拆为 node_table + node_behavior_table + edge_behavior_table + ui_table |
| 7 | **核心循环更新** | 无时间模型概念 | **加入周期确认事件、未完成K线处理** | 时间触发的边，触发时机和周期确认对齐 |

**一句话总结 v1.13 升级：** 诚实——表驱动分层（L1/L2/L3），不吹牛说"加新节点不改代码"；补全——时间模型（股票计算的核心复杂度）；正交——拆分 node_type_table，每个表管一个维度；落地——实盘边界场景（停牌/新股/数据异常），三态逻辑，保守策略。

---

## 一、表驱动三层级（诚实版，不吹牛）

### 1.1 之前的问题：吹牛了

**v1.12 的说法（不诚实/吹牛）：**
- "加新节点类型 = 加一行，不改引擎代码"
- "真正的表驱动：逻辑在表里，引擎只查表调用"

**实际情况（诚实版）：**
- node_type_table 只是把 if-else 换成了查表
- handler 还是要写代码的
- 加新类型还是要写新 handler 的代码
- 这不叫"改表不改代码"，这叫"结构清晰，容易扩展"

**为什么会吹牛？**
- 把"表驱动分发"和"数据驱动"搞混了
- 表驱动有不同层级，不是所有表驱动都能"改表不改代码"
- 诚实比吹牛重要——是什么级别就说什么级别

### 1.2 表驱动三层级定义

**L1：分发表驱动**
- 用表替代 if-else 做类型分发
- 比如 node_type_table 做节点类型分发
- handler 还是要写代码
- 加新类型还是要写新 handler 的代码
- 好处：结构清晰，容易扩展，容易注册
- **当前状态：已实现（node_type_table 就是这个级别）**

**L2：组合表驱动**
- 行为由多个表的组合决定，不需要写新代码
- 比如 filter = 指标 × 比较算子 × 集合运算，三者组合
- 加新 filter = 组合已有的指标、算子、运算，不需要新代码
- 这才是真正的"改表不改代码"
- **当前状态：部分实现（operator_table + combine_table + propagate_table 可以组合出 filter）**

**L3：数据驱动（DSL）**
- 连表结构都是数据定义的
- 用户可以自定义表结构、自定义行为
- 级别太高，股票池不一定需要
- **当前状态：不做，也不需要**

### 1.3 三层级对比

| 层级 | 名称 | 核心特征 | 加新功能 | 代码量 | 当前状态 |
|------|------|---------|---------|--------|---------|
| **L1** | **分发表驱动** | 用表替代 if-else 做类型分发 | 要写新 handler 代码 | 多 | ✅ 已实现 |
| **L2** | **组合表驱动** | 行为由多个表的组合决定 | 组合已有表，不改代码 | 中 | ⚠️ 部分实现 |
| **L3** | **数据驱动（DSL）** | 连表结构都是数据定义的 | 定义新表结构 | 少 | ❌ 不做 |

### 1.4 诚实说明：当前目标是 L1 + 部分 L2

**我们现在在哪里：**
- ✅ L1 分发表驱动：node_type_table 已经是了
- ⚠️ L2 组合表驱动：operator_table + combine_table + propagate_table 可以组合出 filter，但还不完整
- ❌ L3 数据驱动：不做，也不需要

**什么是真的"改表不改代码"：**
- ❌ 加新节点类型：要写新 handler 代码（L1，不是不改代码）
- ✅ 加新 filter 组合：组合已有的指标+算子+运算，不改代码（L2，真的不改代码）
- ✅ 加新传播方式组合：组合已有的传播属性，不改代码（L2）

**为什么不吹牛到 L2/L3：**
- 诚实：是什么就是什么，不包装
- 风险：吹出去的牛要兑现，兑现不了就打脸
- 价值：L1 已经有很大价值了（结构清晰、易扩展、易测试），不用靠吹牛撑场面

### 1.5 L2 组合表驱动的具体例子

**filter 的组合：**
```
filter = 指标 × 比较算子 × 集合运算

例子1：MA5 > 10 且 成交量 > 1000万
  = [MA5 指标, VOL 指标]
  × [gt 算子（>）]
  × [and 集合运算]

例子2：涨幅前 10
  = [涨跌幅 指标]
  × [无算子（直接用值）]
  × [top_n 集合运算]

加新 filter = 组合已有的三个表，不改代码 ✓
```

**加新指标 = 要写代码（L1）：**
- 新指标的计算逻辑要写代码
- 写完注册到 indicator_table 里
- 这是 L1，不是 L2

**加新算子 = 要写代码（L1）：**
- 新算子的比较逻辑要写代码
- 写完注册到 operator_table 里
- 这是 L1，不是 L2

**加新组合方式 = 不改代码（L2）：**
- 用已有的指标、算子、运算组合出新的 filter
- 不用写代码，改配置表就行
- 这才是真正的 L2

### 1.6 现有代码中的表驱动层级验证

**L1 分发表驱动（已实现）：**
- `timing.json:starttype_rules` → `_eval_timing_primitive`（engine.py:829）
  - 用表做 starttype 分发，每个 primitive 是一个 handler
  - 加新 primitive 要写新函数代码（L1）
- `edge_strategies.json:strategies` → 策略分发
- `modules.json` → 模块分发

**L2 组合表驱动（部分实现）：**
- `formula_funcs.json` → `_dispatch_func`（formula_engine.py:214）
  - 通用算子：window_op / shift_op / cross_op
  - 加新函数 = 加一行配置 + 可能用已有的通用算子
  - 如果用已有通用算子，不改代码（L2）
  - 如果需要新算子，要写代码（L1）

---

## 二、时间模型设计（股票计算的核心复杂度）

### 2.1 之前的问题：完全忽略了

**v1.12 及之前的时间模型：**
- 只有一个 `tick_interval`（默认 1 秒）
- 只有一个周期（默认日线）
- 没有多周期概念
- 没有周期边界概念
- 没有未完成K线概念

**为什么这是大问题：**
- 股票计算的核心复杂度就是时间
- 多周期同步：tick、1分钟、5分钟、日线，数据到达时间不同步
- 周期边界：分钟K线什么时候确认？日线什么时候确认？
- 未完成K线：当前周期的K线是不完整的，指标怎么处理？
- 时间对齐：不同周期的指标计算，时间点怎么对齐？
- 这些都是实盘必须处理的，之前完全忽略了

### 2.2 多周期模型

**周期定义：**

| 周期 | 周期长度 | 数据来源 | 确认时机 | 未完成K线 |
|------|---------|---------|---------|----------|
| tick | 实时 | 逐笔推送 | 每笔都是完成的 | 无（tick 本身就是原子的） |
| 1分钟 | 60秒 | tick 聚合 | 每分钟的第 0 秒（如 10:30:00） | 当前分钟的K线是未完成的 |
| 5分钟 | 300秒 | 1分钟聚合 | 每5分钟的第 0 秒（如 10:30:00） | 当前5分钟的K线是未完成的 |
| 15分钟 | 900秒 | 5分钟聚合 | 每15分钟的第 0 秒 | 当前15分钟的K线是未完成的 |
| 30分钟 | 1800秒 | 15分钟聚合 | 每30分钟的第 0 秒 | 当前30分钟的K线是未完成的 |
| 60分钟 | 3600秒 | 30分钟聚合 | 每60分钟的第 0 秒 | 当前60分钟的K线是未完成的 |
| 日线 | 1天 | 60分钟聚合 | 收盘后（如 15:00:00） | 当天的K线是未完成的 |

**数据到达时间不同步：**
```
时间线（秒）：  0    1    2    3   ...   58   59   60   61   62
tick：          ✓    ✓    ✓    ✓   ...    ✓    ✓    ✓    ✓    ✓
1分钟K线：      未完成 ←─────────────────────────────→ 确认 ✓  未完成
5分钟K线：      未完成 ←───────────────────────────────────────────── ...
```

**关键结论：**
- 每个周期有自己的节奏
- 不是所有周期都在同一时间更新
- 短周期更新频繁，长周期更新少
- 长周期的K线由短周期聚合而来

### 2.3 已完成K线 vs 未完成K线

**每个周期有两种K线：**

| 类型 | 含义 | 特点 | 指标计算 |
|------|------|------|---------|
| **已完成K线** | 这个周期已经结束了，数据完整 | 不会再变了，确定的 | 可以放心用，结果是确定的 |
| **未完成K线** | 这个周期还在进行中，数据不完整 | 还在变化，不确定的 | 可以用，但结果会变，要小心 |

**指标计算的默认策略：**
```
指标默认用"已完成K线 + 当前未完成K线"计算最新值

例子：MA5（5日均线）
  已完成K线：前4天的K线（确定的，不会变）
  未完成K线：今天的K线（还在变，不确定）
  MA5 最新值 = (前4天收盘价 + 当前价) / 5

注意：最新值会随着当前价变化而变化
```

**为什么默认用未完成K线：**
- 实盘需要实时性，不能等收盘才算
- 未完成K线虽然不确定，但有参考价值
- 但要清楚告诉用户：这是实时值，会变的

### 2.4 周期确认事件

**定义：当一个周期的K线完成时，发一个周期确认事件。**

```
时间线：
  10:29:59  → 1分钟K线还在走（未完成）
  10:30:00  → 10:29的1分钟K线确认了！发 period_confirmed(period='1m', time='10:29')
  10:30:01  → 10:30的1分钟K线开始走（新的未完成K线）

  10:25:00  → 5分钟K线还在走（未完成）
  10:30:00  → 10:25的5分钟K线确认了！发 period_confirmed(period='5m', time='10:25')
  10:30:01  → 10:30的5分钟K线开始走（新的未完成K线）
```

**周期确认事件的用途：**
1. **时间触发的边**：触发时机和周期确认对齐
   - 比如"每天收盘后执行" = 日线确认事件触发
   - 比如"每小时开始执行" = 60分钟确认事件触发

2. **指标重算**：周期确认后，基于该周期的指标可以重算确认值
   - 比如日线确认后，MA5 的"确认值"就确定了，不会变了

3. **数据持久化**：周期确认后，K线数据可以持久化
   - 未完成K线存在内存里
   - 已完成K线可以存数据库/文件

### 2.5 时间对齐

**问题：不同周期的指标计算，时间点怎么对齐？**

**例子：用日线MA5 和 60分钟MA20 做比较**
- 日线MA5 的最新值：基于今天（未完成）+ 前4天（已完成）
- 60分钟MA20 的最新值：基于当前小时（未完成）+ 前19小时（已完成）
- 这两个"最新值"的时间点不一样，能直接比吗？

**对齐策略：**

| 策略 | 做法 | 适用场景 |
|------|------|---------|
| **最新值对齐（默认）** | 都用各自的最新值，不管时间点是否一致 | 实盘交易，需要最新数据 |
| **确认值对齐** | 都用上一个周期的确认值，时间点对齐 | 回测、严谨的比较 |
| **左对齐** | 以慢周期的时间点为准，快周期取对应时间点的值 | 跨周期比较 |

**默认策略：最新值对齐**
- 实盘场景下，大家都用最新值
- 虽然时间点不完全对齐，但够用
- 真的需要严谨对齐的，自己选择确认值对齐

### 2.6 时间驱动的边触发

**之前的边触发：数据变化就触发（脏驱动）**
- 只要数据变了，就触发边执行
- 没有时间概念

**v1.13 的边触发：两种触发模式**

| 触发模式 | 触发时机 | 适用场景 |
|---------|---------|---------|
| **数据驱动（默认）** | 源节点数据变化就触发 | 实时过滤、实时监控 |
| **时间驱动** | 周期确认事件触发 | 收盘选股、小时级调仓 |

**时间驱动边的例子：**
```
边：每日收盘后，从全市场选出 MA5 > MA10 的股票
  触发模式：时间驱动
  触发周期：日线
  触发时机：日线确认后（收盘后）
  效果：每天只算一次，不是每个 tick 都算
```

**为什么需要时间驱动：**
- 有些策略不需要实时，每天算一次就行
- 减少计算量，提高性能
- 避免未完成K线的波动干扰

### 2.7 现有代码中的时间模型验证

**验证 1：只有 tick_interval，没有多周期**
- `_tick_interval = self._timing_cfg.get("tick_interval", 1)`（engine.py:386）
- 只有一个 tick 间隔，没有多周期概念
- ✅ 符合：之前确实没有多周期模型

**验证 2：公式引擎有 period 参数，但只是透传**
- `eval_batch(..., period: str = "1d", ...)`（formula_engine.py:624）
- period 只是传给 data_fetcher，没有周期管理
- ✅ 符合：之前确实没有周期管理

**验证 3：没有周期确认事件**
- 搜遍代码，没有 period_confirmed 之类的概念
- ✅ 符合：之前确实没有周期确认事件

---

## 三、拆分 node_type_table，恢复正交性

### 3.1 之前的问题：一张大表混了多个维度

**v1.12 的 node_type_table（一张粗表）：**

| 字段 | 维度 | 说明 |
|------|------|------|
| type_id | 基本属性 | 类型ID |
| init_handler | 行为 | 初始化函数 |
| in_edge_handler | 行为 | 入边处理函数 |
| out_edge_handler | 行为 | 出边处理函数 |
| allowed_roles | 基本属性 | 允许的角色 |
| has_stocks | 基本属性 | 是否有股票列表 |
| properties | 混合 | 其他属性（可能混了 UI、行为、属性） |

**问题：**
1. **维度混杂**：基本属性、行为、UI 属性全混在一张表里
2. **改一个维度影响其他维度**：比如改 UI 颜色，要改 node_type_table，可能不小心改到行为
3. **不灵活**：如果两个节点类型行为一样，但 UI 不一样，要写两行重复的
4. **不正交**：正交的意思是"改一个维度不影响其他维度"

### 3.2 正交拆分：4 张表

**拆成正交的 4 张表：**

| 表名 | 管什么维度 | 改它影响什么 |
|------|-----------|------------|
| **node_table** | 节点基本属性 | 节点的身份、分类、结构属性 |
| **node_behavior_table** | 节点行为 | 节点怎么初始化、怎么处理入边、怎么处理出边 |
| **edge_behavior_table** | 边行为 | 边怎么检查门控、怎么过滤、怎么传播 |
| **ui_table** | UI 相关属性 | 节点显示成什么样（颜色、图标、大小） |

**正交的好处：**
- 改行为不影响 UI
- 改 UI 不影响行为
- 改基本属性不影响行为和 UI
- 每个表职责单一，容易理解和维护

### 3.3 表 1：node_table（节点基本属性表）

**定义：节点的基本属性，和行为、UI 无关。**

| 字段 | 类型 | 说明 |
|------|------|------|
| type_id | str | 节点类型 ID |
| name | str | 类型名称（显示用） |
| category | str | 分类（source/filter/pool/terminal 等） |
| allowed_roles | List[str] | 允许的角色（source/target/both） |
| has_stocks | bool | 是否有股票列表 |
| has_params | bool | 是否有参数配置 |
| max_in_edges | int | 最大入边数（-1 表示不限） |
| max_out_edges | int | 最大出边数（-1 表示不限） |

**示例数据：**
```json
{
  "node_table": {
    "market_source": {
      "name": "市场数据源",
      "category": "source",
      "allowed_roles": ["source"],
      "has_stocks": true,
      "has_params": true,
      "max_in_edges": 0,
      "max_out_edges": -1
    },
    "stock_state_pool": {
      "name": "股票状态池",
      "category": "pool",
      "allowed_roles": ["source", "target", "both"],
      "has_stocks": true,
      "has_params": true,
      "max_in_edges": -1,
      "max_out_edges": -1
    },
    "transfer_condition": {
      "name": "转移条件",
      "category": "filter",
      "allowed_roles": ["both"],
      "has_stocks": true,
      "has_params": true,
      "max_in_edges": -1,
      "max_out_edges": -1
    },
    "discard_pool": {
      "name": "弃池",
      "category": "terminal",
      "allowed_roles": ["target"],
      "has_stocks": true,
      "has_params": false,
      "max_in_edges": -1,
      "max_out_edges": 0
    }
  }
}
```

### 3.4 表 2：node_behavior_table（节点行为表）

**定义：节点的行为——怎么初始化、怎么处理入边、怎么处理出边。**

| 字段 | 类型 | 说明 |
|------|------|------|
| type_id | str | 节点类型 ID（和 node_table 关联） |
| init_handler | str | 初始化函数名 |
| in_edge_handler | str | 入边处理函数名 |
| out_edge_handler | str | 出边处理函数名 |
| tick_handler | str | 每 tick 处理函数名（可选） |
| alert_handler | str | 预警处理函数名（可选） |

**示例数据：**
```json
{
  "node_behavior_table": {
    "market_source": {
      "init_handler": "init_market_source",
      "in_edge_handler": null,
      "out_edge_handler": "out_edge_resolve_and_pass",
      "tick_handler": null,
      "alert_handler": null
    },
    "stock_state_pool": {
      "init_handler": "init_stock_state_pool",
      "in_edge_handler": "in_edge_default",
      "out_edge_handler": "out_edge_default",
      "tick_handler": null,
      "alert_handler": null
    },
    "transfer_condition": {
      "init_handler": "init_transfer_condition",
      "in_edge_handler": "in_edge_pass_through",
      "out_edge_handler": "out_edge_apply_filter",
      "tick_handler": null,
      "alert_handler": null
    },
    "discard_pool": {
      "init_handler": "init_discard_pool",
      "in_edge_handler": "in_edge_default",
      "out_edge_handler": null,
      "tick_handler": null,
      "alert_handler": null
    }
  }
}
```

### 3.5 表 3：edge_behavior_table（边行为表）

**定义：边的行为——怎么检查门控、怎么过滤、怎么传播。**

| 字段 | 类型 | 说明 |
|------|------|------|
| edge_type | str | 边类型 ID |
| gate_check_handler | str | 门控检查函数名 |
| filter_handler | str | 过滤处理函数名 |
| propagate_handler | str | 传播处理函数名 |
| trigger_mode | str | 触发模式（data_driven / time_driven） |
| default_trigger_period | str | 默认触发周期（时间驱动时用） |

**示例数据：**
```json
{
  "edge_behavior_table": {
    "default_edge": {
      "gate_check_handler": "gate_check_default",
      "filter_handler": "filter_three_layer",
      "propagate_handler": "propagate_default",
      "trigger_mode": "data_driven",
      "default_trigger_period": null
    },
    "daily_selection_edge": {
      "gate_check_handler": "gate_check_period_confirmed",
      "filter_handler": "filter_three_layer",
      "propagate_handler": "propagate_default",
      "trigger_mode": "time_driven",
      "default_trigger_period": "1d"
    }
  }
}
```

### 3.6 表 4：ui_table（UI 属性表）

**定义：UI 相关的属性——颜色、图标、可调整大小等。**

| 字段 | 类型 | 说明 |
|------|------|------|
| type_id | str | 节点类型 ID（和 node_table 关联） |
| color | str | 节点颜色（HEX） |
| icon | str | 图标名称 |
| resizable | bool | 是否可调整大小 |
| default_width | int | 默认宽度 |
| default_height | int | 默认高度 |
| show_label | bool | 是否显示标签 |

**示例数据：**
```json
{
  "ui_table": {
    "market_source": {
      "color": "#4CAF50",
      "icon": "database",
      "resizable": true,
      "default_width": 160,
      "default_height": 80,
      "show_label": true
    },
    "stock_state_pool": {
      "color": "#2196F3",
      "icon": "pool",
      "resizable": true,
      "default_width": 160,
      "default_height": 120,
      "show_label": true
    },
    "transfer_condition": {
      "color": "#FF9800",
      "icon": "filter",
      "resizable": true,
      "default_width": 160,
      "default_height": 100,
      "show_label": true
    },
    "discard_pool": {
      "color": "#9E9E9E",
      "icon": "trash",
      "resizable": true,
      "default_width": 120,
      "default_height": 80,
      "show_label": true
    }
  }
}
```

### 3.7 正交性验证

**改行为不改 UI：**
- 想改 market_source 的 out_edge_handler
- 只改 node_behavior_table，ui_table 完全不用动
- ✅ 正交

**改 UI 不改行为：**
- 想改 stock_state_pool 的颜色
- 只改 ui_table，node_behavior_table 完全不用动
- ✅ 正交

**改基本属性不改行为和 UI：**
- 想改 transfer_condition 的 max_in_edges
- 只改 node_table，其他两张表完全不用动
- ✅ 正交

**行为复用：**
- 两个节点类型行为一样，但 UI 不一样
- node_behavior_table 可以共用一行，ui_table 各写各的
- ✅ 正交，减少重复

---

## 四、实盘边界场景处理

### 4.1 之前的问题：默认数据都正常

**v1.12 及之前的假设：**
- 所有股票都有正常的数据
- 所有股票都在交易
- 数据都是准确的、完整的
- 没有停牌、没有新股、没有数据异常

**为什么这是大问题：**
- 实盘不是这样的
- A 股 5000 多只股票，每天都有停牌的
- 每天都有新股上市
- 数据偶尔会有异常值
- 这些边界情况不处理，实盘会出问题

### 4.2 股票状态独立

**设计原则：每只股票的状态独立。**

**股票状态表（stock_status_table）：**

| 状态 | 含义 | 原因 |
|------|------|------|
| `normal` | 正常交易 | 正常有数据，正常交易 |
| `suspended` | 停牌 | 股票停牌了，没有最新数据 |
| `insufficient_data` | 数据不足 | 新股、刚复牌，历史数据不够计算指标 |
| `abnormal` | 数据异常 | 数据有异常值，不可信 |

**状态独立的好处：**
- 一只股票状态异常，不影响其他股票
- 每个节点可以根据股票状态做不同处理
- 状态是显式的，不是隐式的（不是靠 None 猜）

### 4.3 三态逻辑处理不确定性

**设计原则：三态逻辑处理不确定性（True/False/None）。**

**三态：**

| 状态 | 含义 | 说明 |
|------|------|------|
| `True` | 条件满足 | 确定满足条件 |
| `False` | 条件不满足 | 确定不满足条件 |
| `None` | 不确定 | 数据不足/停牌/异常，无法判断 |

**三态逻辑的运算规则：**

```
AND 运算（一假即假，一未知即未知）：
  True AND True = True
  True AND False = False
  True AND None = None
  False AND False = False
  False AND None = False
  None AND None = None

OR 运算（一真即真，一未知即未知）：
  True OR True = True
  True OR False = True
  True OR None = True
  False OR False = False
  False OR None = None
  None OR None = None

NOT 运算：
  NOT True = False
  NOT False = True
  NOT None = None
```

**为什么用三态：**
- 实盘有很多不确定性，不能简单地用 True/False
- 停牌的股票，你说它满足条件还是不满足？都不对，应该是"不知道"
- 三态逻辑能正确处理这种不确定性

### 4.4 保守策略：不确定的不入池

**设计原则：保守策略——不确定的不入池，宁可漏掉不错入。**

**原因：**
- 股票池是用来选股的
- 漏选一只股票，最多是少赚点
- 错选一只股票，可能会亏
- 所以保守策略更安全：不确定的就不选

**具体规则：**

| 场景 | 状态 | 比较结果 | 入池吗？ | 理由 |
|------|------|---------|---------|------|
| 正常股票，满足条件 | normal | True | ✅ 入 | 确定满足 |
| 正常股票，不满足条件 | normal | False | ❌ 不入 | 确定不满足 |
| 停牌股票 | suspended | None | ❌ 不入 | 不确定，保守不入 |
| 新股，数据不足 | insufficient_data | None | ❌ 不入 | 不确定，保守不入 |
| 数据异常 | abnormal | None | ❌ 不入 | 不确定，保守不入 |

**例外情况：**
- 如果用户明确要求"停牌的也留着"，可以配置
- 但默认是保守策略

### 4.5 停牌处理

**问题：股票停牌了，没有数据，怎么处理？**

**处理方案：**

| 层面 | 处理方式 | 说明 |
|------|---------|------|
| **数据层** | 标记状态为 suspended | latest_tick 里还是存最后一个有效数据，但状态是 suspended |
| **指标计算** | 用最近的有效数据计算，结果标记为"基于历史数据" | MA5 用停牌前的5天算，不是用今天的 |
| **比较判断** | 结果为 None（不确定） | 因为数据不是最新的，不能确定现在是否满足 |
| **传播** | 默认不能传播（保守策略） | 不确定的不入池 |
| **入池** | 默认不能入池（保守策略） | 不确定的不入池 |

**停牌股票的传播策略（可配置）：**
- `conservative`（默认）：停牌的不传播，不入池
- `keep`：停牌的保留在池子里，不出去也不进来
- `allow`：停牌的也可以传播，也可以入池（不推荐）

### 4.6 新股处理

**问题：刚上市的股票，历史数据不足，怎么处理？**

**处理方案：**

| 层面 | 处理方式 | 说明 |
|------|---------|------|
| **数据层** | 标记状态为 insufficient_data | 当历史K线数量 < 指标需要的最小数量时 |
| **指标计算** | 能算多少算多少，不够的返回 None | MA5 只有3天数据，就返回 None |
| **比较判断** | 结果为 None（不确定） | 数据不足，无法判断 |
| **传播** | 默认不能传播（保守策略） | 不确定的不入池 |
| **入池** | 默认不能入池（保守策略） | 不确定的不入池 |

**新股"数据不足"的判定：**
- 每个指标有自己的最小数据要求
- 比如 MA5 需要至少 5 根K线
- 比如 MACD 需要至少 26 根K线
- 只要有一个指标数据不足，整体就是 insufficient_data

**新股什么时候变成 normal：**
- 当历史K线数量 >= 所有用到的指标的最小数据要求时
- 不是固定多少天，而是看用了什么指标

### 4.7 数据异常处理

**问题：数据有错误/异常值，怎么处理？**

**异常类型：**
- 价格跳空过大（比如涨跌幅 > 20%，但不是新股）
- 成交量为 0 但价格在变
- 价格为 0 或负数
- 高低价关系不对（最高价 < 最低价）

**处理方案：**

| 层面 | 处理方式 | 说明 |
|------|---------|------|
| **数据层** | 检测异常，标记状态为 abnormal | 异常检测规则可配置 |
| **指标计算** | 跳过异常值，用前一个有效值，或者返回 None | 看异常类型和严重程度 |
| **比较判断** | 结果为 None（不确定） | 数据异常，不可信 |
| **传播** | 默认不能传播（保守策略） | 不确定的不入池 |
| **入池** | 默认不能入池（保守策略） | 不确定的不入池 |

**异常值会不会影响其他股票的计算？**
- **排名类会影响**：比如涨幅前 10，如果有一只股票涨幅异常大，会把其他股票挤出去
- **独立判断不会影响**：比如 MA5 > MA10，每只股票自己算自己的，互不影响
- **处理方式**：排名类计算前，先过滤掉 abnormal 的股票

### 4.8 股票状态表（运行时表）

**新增运行时表：stock_status_table**

| 字段 | 类型 | 说明 |
|------|------|------|
| key | str | 股票代码 |
| value | dict | 状态详情 |

**value 结构：**
```python
{
    'status': 'normal',  # normal / suspended / insufficient_data / abnormal
    'status_since': timestamp,  # 状态开始时间
    'reason': '',  # 状态原因（可选，如 '2024-01-01 起停牌'）
    'min_data_available': 120,  # 可用的历史K线数量（日线）
    'last_valid_data_time': timestamp,  # 最后一个有效数据的时间
}
```

**读写时机：**
- 读时机：指标计算前、比较判断前、传播前
- 写时机：数据更新时检测状态变化，更新状态表

---

## 五、配置表清单（v1.13 更新版，拆分后）

### 5.1 节点配置表（4 张，正交拆分）

| # | 表名 | 维度 | 读时机 | 写时机 | 说明 |
|---|------|------|--------|--------|------|
| 1 | `node_table` | 基本属性 | 初始化、校验时 | 配置加载时 | 节点的身份、分类、结构属性 |
| 2 | `node_behavior_table` | 节点行为 | 处理节点时查表 | 配置加载时 | 节点的 init/in/out/tick/alert handler |
| 3 | `edge_behavior_table` | 边行为 | 处理边时查表 | 配置加载时 | 边的 gate/filter/propagate handler + 触发模式 |
| 4 | `ui_table` | UI 属性 | 渲染 UI 时 | 配置加载时 | 颜色、图标、大小等显示属性 |

**v1.13 相比 v1.12 的变化：**

| v1.12（1 张粗表） | v1.13（4 张正交表） | 变化原因 |
|-------------------|---------------------|---------|
| node_type_table | **node_table + node_behavior_table + edge_behavior_table + ui_table** | 恢复正交性：每个表管一个维度，改一个维度不影响其他维度 |

### 5.2 算子配置表（3 张，L2 组合表驱动）

| # | 表名 | 维度 | 读时机 | 写时机 | 说明 |
|---|------|------|--------|--------|------|
| 1 | `operator_table` | 比较算子 | 比较判断时查表 | 配置加载时 | 每种比较算子的函数、参数（L1） |
| 2 | `combine_table` | 集合运算 | 集合运算时查表 | 配置加载时 | 每种集合运算的函数、参数（L1） |
| 3 | `propagate_table` | 传播方式 | propagate 时查表 | 配置加载时 | 每种传播方式的函数、属性（L1） |

**L2 组合：三者组合出 filter（不改代码）**
- filter = 指标 × 比较算子 × 集合运算
- 加新 filter 组合 = 不改代码（L2）
- 加新算子/新运算 = 要写代码（L1）

### 5.3 时间配置表（新增）

| # | 表名 | 维度 | 读时机 | 写时机 | 说明 |
|---|------|------|--------|--------|------|
| 1 | `period_table` | 周期定义 | 周期管理、时间对齐时 | 配置加载时 | 各周期的长度、确认时机、聚合关系 |
| 2 | `time_trigger_table` | 时间触发规则 | 时间驱动边检查时 | 配置加载时 | 时间驱动的触发规则（周期、偏移等） |

### 5.4 配置表汇总

| 类别 | 表数 | 表名 |
|------|------|------|
| 节点配置（正交） | 4 张 | node_table, node_behavior_table, edge_behavior_table, ui_table |
| 算子配置 | 3 张 | operator_table, combine_table, propagate_table |
| 时间配置 | 2 张 | period_table, time_trigger_table |
| **合计** | **9 张** | |

**v1.12 → v1.13 配置表变化：**

| 版本 | 配置表数 | 变化 |
|------|---------|------|
| v1.12 | 4 张 | node_type_table, operator_table, combine_table, propagate_table |
| v1.13 | 9 张 | node_type_table 拆为 4 张 + 新增 2 张时间配置表 + 保留 3 张算子表 |

---

## 六、运行时表清单（v1.13 更新版）

### 6.1 核心运行时表（8 张，+1 股票状态表）

| # | 表名 | 类型 | 读时机 | 写时机 | 说明 |
|---|------|------|--------|--------|------|
| 1 | `latest_tick` | Dict[code → bar_dict] | 公式引擎计算时读 | tick 开始时更新 | **唯一真相源**。所有股票的最新 tick 数据 |
| 2 | `stock_status_table` | Dict[code → status_dict] | 指标计算/比较/传播前 | 数据更新时检测 | **新增**。每只股票的状态（正常/停牌/数据不足/异常） |
| 3 | `node_stocks` | Dict[nid → List[code]] | propagate 读写、filter 读 | 边执行/TTL过期后写 | 各节点当前股票列表 |
| 4 | `ttl_expiry_queue` | Heap[(expire_ts, nid, code)] | TTL检查时弹出 | 股票入池时插入 | TTL 过期队列。按过期时间排序的最小堆 |
| 5 | `dirty_stocks` | Set[code] | 计算 node_changes 时用 | tick 开始时收集 / tick 结束时清空 | **全局水位线**。本 tick 数据更新了的股票集合 |
| 6 | `node_changes` | Dict[nid → {entered, exited, updated}] | 执行循环读（增量处理） | propagate/TTL/数据更新时写入 | **节点变化三集合**。entered=新进入，exited=离开，updated=还在但数据更新了 |
| 7 | `edge_compare_results` | Dict[eid → Dict[code → True/False/None]] | 集合运算层读 | 比较层写（增量更新） | **三态比较结果**。None=数据不足/停牌/异常。新入池股票先设 None，再计算 |
| 8 | `edge_filter_results` | Dict[eid → Set[code] 或 List[code]] | propagate 读 | 集合运算层写 | 排名型用有序列表，独立型用 Set。只放通过的，None/False 都不在 |

**v1.13 相比 v1.12 的变化：**

| v1.12（7 张） | v1.13（8 张） | 变化原因 |
|--------------|--------------|---------|
| （无） | **+ stock_status_table** | 实盘边界场景：每只股票独立状态（正常/停牌/数据不足/异常） |

### 6.2 时间相关运行时表（新增）

| # | 表名 | 类型 | 读时机 | 写时机 | 说明 |
|---|------|------|--------|--------|------|
| 1 | `period_data` | Dict[period → {completed_bars, current_bar}] | 指标计算、周期确认时 | 数据更新、周期确认时 | 各周期的已完成K线和当前未完成K线 |
| 2 | `period_confirmed_events` | Queue[event] | 时间驱动边处理时 | 周期确认时 | 周期确认事件队列 |

### 6.3 运行时表汇总

| 类别 | 表数 | 表名 |
|------|------|------|
| 核心运行时表 | 8 张 | latest_tick, stock_status_table, node_stocks, ttl_expiry_queue, dirty_stocks, node_changes, edge_compare_results, edge_filter_results |
| 时间相关表 | 2 张 | period_data, period_confirmed_events |
| **合计** | **10 张** | |

**v1.12 → v1.13 运行时表变化：**

| 版本 | 核心运行时表数 | 变化 |
|------|---------------|------|
| v1.12 | 7 张 | latest_tick, node_stocks, ttl_expiry_queue, dirty_stocks, node_changes, edge_compare_results, edge_filter_results |
| v1.13 | 8 张 | + stock_status_table（股票状态表） |

---

## 七、核心循环伪代码（v1.13 更新版）

### 7.1 主循环：轮询 + 脏驱动 + 时间模型

```python
# ============================================================
#  v1.13 核心循环伪代码（轮询数据 + 脏驱动计算 + 表驱动 + 时间模型）
# ============================================================

# --- 初始化 ---
# 加载正交配置表
node_table = load_table('node_table')
node_behavior_table = load_table('node_behavior_table')
edge_behavior_table = load_table('edge_behavior_table')
ui_table = load_table('ui_table')

# 加载算子表
operator_table = load_table('operator_table')
combine_table = load_table('combine_table')
propagate_table = load_table('propagate_table')

# 加载时间配置
period_table = load_table('period_table')
time_trigger_table = load_table('time_trigger_table')

# 注册 handler
handler_registry = register_all_handlers()

# 初始化时间模型
period_data = init_period_data(period_table)  # 各周期的已完成K线 + 当前未完成K线
period_confirmed_events = Queue()

# 编译期：拓扑排序
topo_order = build_topo_order(nodes, edges)

# --- 主循环（轮询模型）---
tick_interval = 1.0  # 秒，默认 1 秒

while running:
    # 1. 等待下一个 tick（轮询间隔 = 延迟）
    await asyncio.sleep(tick_interval)
    
    if paused:
        continue
    
    # 2. 轮询获取最新数据
    tick_data = poll_latest_data()
    
    # 3. 更新数据层 + 标记脏股票 + 更新股票状态
    dirty_stocks.clear()
    for code, new_bar in tick_data.items():
        # 更新 latest_tick
        if latest_tick.get(code) != new_bar:
            latest_tick[code] = new_bar
            dirty_stocks.add(code)
        
        # 检测并更新股票状态（停牌/新股/异常）
        old_status = stock_status_table.get(code, {}).get('status', 'normal')
        new_status = detect_stock_status(code, new_bar)  # 检测状态
        if old_status != new_status:
            update_stock_status(code, new_status)  # 更新状态表
    
    # 4. 更新多周期数据 + 检测周期确认事件
    period_confirmed_events.clear()
    for period in period_table:
        # 更新该周期的未完成K线
        update_current_bar(period, tick_data)
        
        # 检查该周期是否有K线确认了
        if period_bar_confirmed(period):
            # 把已完成的K线移到 completed_bars
            confirm_current_bar(period)
            # 发周期确认事件
            period_confirmed_events.put({
                'period': period,
                'confirmed_time': get_confirmed_bar_time(period),
            })
            # 开始新的未完成K线
            start_new_current_bar(period)
    
    # 5. 如果没有数据变化 且 没有周期确认事件，跳过计算（脏驱动）
    if not dirty_stocks and period_confirmed_events.empty():
        continue  # 数据没变，周期也没确认，不用算
    
    # 6. 计算每个节点的变化（node_changes）
    compute_all_node_changes(dirty_stocks)
    
    # 7. 按拓扑序处理脏节点（表驱动）
    for nid in topo_order:
        if not is_node_dirty(nid):
            continue  # 节点没变，跳过
        
        # 表驱动：查 node_behavior_table，调出边 handler
        node = nodes[nid]
        type_def = node_behavior_table[node['type']]
        handler_name = type_def['out_edge_handler']
        
        if handler_name:
            handler = handler_registry[handler_name]
            handler(nid, node, node_changes[nid])
    
    # 8. 处理时间驱动的边（周期确认事件触发）
    while not period_confirmed_events.empty():
        event = period_confirmed_events.get()
        process_time_driven_edges(event)
    
    # 9. 后处理（PK 排名 / 分析角度 / 预警）
    post_process()
    
    # 10. 清脏，为下一轮做准备
    clear_all_dirty()
```

### 7.2 时间驱动的边处理

```python
def process_time_driven_edges(period_event):
    """处理时间驱动的边（周期确认事件触发）
    
    时间触发的边，触发时机和周期确认对齐
    """
    period = period_event['period']
    confirmed_time = period_event['confirmed_time']
    
    # 找出所有时间驱动、且周期匹配的边
    for edge in all_edges:
        # 查 edge_behavior_table
        edge_type = edge.get('edge_type', 'default_edge')
        behavior_def = edge_behavior_table.get(edge_type, {})
        
        # 不是时间驱动的，跳过
        if behavior_def.get('trigger_mode') != 'time_driven':
            continue
        
        # 周期不匹配的，跳过
        edge_period = edge.get('trigger_period', behavior_def.get('default_trigger_period'))
        if edge_period != period:
            continue
        
        # 时间偏移检查（比如收盘后 30 分钟）
        if not check_time_offset(edge, confirmed_time):
            continue
        
        # 执行这条边
        process_edge_time_driven(edge, confirmed_time)


def process_edge_time_driven(edge, confirmed_time):
    """时间驱动的边处理
    
    和数据驱动的边类似，但：
    1. 触发时机是周期确认事件，不是数据变化
    2. 用确认后的K线计算，不是用未完成K线
    """
    eid = edge['id']
    sid = edge['source_id']
    tid = edge['target_id']
    
    # 源节点的所有股票（时间驱动是全量算，不是增量）
    source_codes = set(node_stocks[sid])
    
    # 过滤掉状态不正常的（停牌/数据不足/异常）
    valid_codes = filter_by_status(source_codes, allowed=['normal'])
    
    if not valid_codes:
        return
    
    # 用确认后的K线计算指标
    indicator_values = formula_engine.eval_indicators_with_confirmed_bars(
        formula_ids=edge['indicators'],
        codes=list(valid_codes),
        period=edge.get('period', '1d'),
        confirmed_time=confirmed_time,
    )
    
    # 比较判断（三态逻辑）
    compare_results = {}
    for code in valid_codes:
        if code not in indicator_values:
            compare_results[code] = None  # 数据不足
        else:
            op_id = edge['compare_spec']['operator']
            op_def = operator_table[op_id]
            op_func = handler_registry[op_def['func']]
            compare_results[code] = op_func(indicator_values[code], edge['compare_spec']['params'])
    
    # 集合运算
    combine_op = edge['combine_op']
    combine_def = combine_table[combine_op]
    combine_func = handler_registry[combine_def['func']]
    filter_result = combine_func(compare_results, valid_codes, edge['combine_params'])
    
    # propagate（保守策略：None 的不入池）
    propagate_mode = edge['propagate_mode']
    propagate_def = propagate_table[propagate_mode]
    propagate_func = handler_registry[propagate_def['func']]
    
    new_entered, new_exited = propagate_func(
        source_id=sid,
        target_id=tid,
        filter_result=filter_result,
        edge=edge,
    )
    
    # 更新目标节点
    update_target_node(tid, new_entered, new_exited)
```

### 7.3 股票状态检测

```python
def detect_stock_status(code, new_bar):
    """检测股票状态
    
    返回：normal / suspended / insufficient_data / abnormal
    """
    # 1. 先检查是否数据异常
    if is_abnormal_data(new_bar):
        return 'abnormal'
    
    # 2. 再检查是否停牌（成交量为 0 且价格不变）
    if is_suspended(new_bar):
        return 'suspended'
    
    # 3. 再检查是否数据不足（历史K线不够）
    if has_insufficient_data(code):
        return 'insufficient_data'
    
    # 4. 都不是，就是正常的
    return 'normal'


def is_abnormal_data(bar):
    """检测数据是否异常"""
    if bar is None:
        return True
    
    # 价格为 0 或负数
    if bar.get('close', 0) <= 0:
        return True
    
    # 高低价关系不对
    if bar.get('high', 0) < bar.get('low', 0):
        return True
    
    # 涨跌幅过大（非新股）
    # ... 更多异常检测规则
    
    return False


def is_suspended(bar):
    """检测是否停牌"""
    # 成交量为 0 且价格不变 = 大概率停牌
    if bar.get('vol', 0) == 0 and bar.get('change_pct', 0) == 0:
        return True
    return False


def has_insufficient_data(code):
    """检测是否数据不足"""
    # 获取可用的历史K线数量
    available_bars = get_available_bars_count(code)
    
    # 计算所有用到的指标的最小数据要求
    min_required = get_min_required_bars(code)
    
    return available_bars < min_required
```

---

## 八、功能-表操作对应表（v1.13 更新版）

### 8.1 主循环层（轮询 + 脏驱动 + 时间模型）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **轮询等待** | tick_interval 配置 | — | sleep(tick_interval)，延迟 = tick_interval |
| **数据轮询** | 数据源接口 | `latest_tick` + `dirty_stocks` | 主动 pull 最新数据，对比变化，标记脏股票 |
| **股票状态检测** | 数据 + 状态检测规则 | `stock_status_table` | 检测每只股票的状态（正常/停牌/数据不足/异常） |
| **周期更新** | `period_table` + 最新数据 | `period_data` | 更新各周期的未完成K线，检测周期确认 |
| **周期确认事件** | `period_data` + 当前时间 | `period_confirmed_events` | 当周期K线完成时，发确认事件 |
| **脏驱动跳过** | `dirty_stocks` + `period_confirmed_events` | — | 如果没数据变化 且 没周期确认，跳过计算 |
| **节点变化计算** | `dirty_stocks` + `node_stocks` | `node_changes` | entered/exited 来自传播，updated = dirty_stocks ∩ node_stocks |
| **拓扑序处理** | `node_changes` + 拓扑序 | 各层状态表 | 按拓扑序处理脏节点，干净节点跳过 |

### 8.2 节点处理层（正交表驱动）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **节点基本信息** | `node_table` | — | 查表得到节点的分类、角色、结构属性 |
| **节点行为判断** | `node_behavior_table` | — | 查表得到 handler，不是 if-else 判断 |
| **初始化 handler** | `node_behavior_table.init_handler` | `node_stocks` | 查表调用对应的初始化函数 |
| **入边 handler** | `node_behavior_table.in_edge_handler` | `node_changes` | 查表调用对应的入边处理函数 |
| **出边 handler** | `node_behavior_table.out_edge_handler` | 各边状态表 | 查表调用对应的出边处理函数 |
| **UI 渲染** | `ui_table` + `node_table` | — | 查表得到颜色、图标、大小等显示属性 |
| **脏节点判断** | `node_changes[nid]` | — | entered/exited/updated 全空就是干净的 |

### 8.3 边执行层（三层 filter + 表驱动算子 + 时间驱动）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **边触发模式判断** | `edge_behavior_table.trigger_mode` | — | 数据驱动还是时间驱动 |
| **时间条件检查（数据驱动）** | `edge_timer_specs` | — | 时间到了才执行 |
| **时间条件检查（时间驱动）** | `time_trigger_table` + `period_confirmed_events` | — | 周期确认事件触发 |
| **股票状态过滤** | `stock_status_table` | — | 停牌/数据不足/异常的，按保守策略处理 |
| **第一层：指标计算** | 公式引擎接口 | 公式引擎内部表 | 数据驱动用未完成K线，时间驱动用确认K线 |
| **第二层：比较判断** | `operator_table` + 指标值 + 股票状态 | `edge_compare_results` | 查表调算子函数。三态：True/False/None |
| **第三层：集合运算** | `combine_table` + 比较结果 | `edge_filter_results` | 查表调集合运算函数。AND/OR/排名等（三态逻辑） |
| **propagate** | `propagate_table` + filter 结果 | `node_stocks` + `node_changes` | 查表调传播函数。保守策略：None 的不入池 |
| **事件发射** | `node_changes[tid]` | 事件队列 | entered/exited 直接发事件 |

### 8.4 时间模型层

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **多周期数据管理** | `period_table` + tick 数据 | `period_data` | 各周期的已完成K线 + 当前未完成K线 |
| **周期确认检测** | `period_data` + 当前时间 | `period_confirmed_events` | 当一个周期的K线完成时，发确认事件 |
| **时间对齐** | `period_data` + 对齐策略 | — | 最新值对齐 / 确认值对齐 / 左对齐 |
| **未完成K线处理** | `period_data.current_bar` | — | 指标默认用"已完成K线 + 当前未完成K线" |
| **确认值计算** | `period_data.completed_bars` | — | 用全部已完成K线计算，结果确定不变 |

### 8.5 数据层 + 股票状态

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **tick 数据更新** | 数据源 | `latest_tick` + `dirty_stocks` | 轮询拉取，对比变化，标记脏股票 |
| **股票状态检测** | `latest_tick` + 历史数据 | `stock_status_table` | 检测正常/停牌/数据不足/异常 |
| **通知公式引擎** | `dirty_stocks` | 公式引擎内部失效标记 | `formula_engine.on_data_updated(dirty_codes)` |
| **指标计算（向量化）** | 公式引擎接口 + `stock_status_table` | 公式引擎内部表 | 空间批量：一批股票一起算，跳过状态异常的 |

### 8.6 TTL 淘汰层

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| 股票入池记录 TTL | 边配置 | `ttl_expiry_queue` 插入 | `expire_ts = current_ts + ttl_sec` |
| TTL 过期检查 | `ttl_expiry_queue + current_ts` | 弹出过期项 | 最小堆：堆顶过期就弹出 |
| 过期股票移除 | `node_stocks[nid]` | `node_stocks[nid]` | 从节点移除 |
| **过期触发级联** | — | `node_changes[nid].exited.add(code)` | 加入 exited 集合 |

### 8.7 实盘边界场景

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **停牌处理** | `stock_status_table` | `edge_compare_results` | 停牌的比较结果为 None，默认不入池 |
| **新股处理** | `stock_status_table` | `edge_compare_results` | 数据不足的比较结果为 None，默认不入池 |
| **数据异常处理** | `stock_status_table` | `edge_compare_results` | 异常的比较结果为 None，默认不入池 |
| **三态逻辑运算** | `edge_compare_results`（三态） | `edge_filter_results` | AND/OR/NOT 的三态版本 |
| **保守策略** | `edge_filter_results` + 配置 | `node_stocks` | 不确定的不入池，宁可漏掉不错入 |
| **排名类异常过滤** | `stock_status_table` + 排名数据 | 排名结果 | 排名前先过滤掉 abnormal 的股票 |

### 8.8 后处理层

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| PK排名 | `node_stocks[target]` + `pk_config` + `stock_status_table` | `_pk_rankings` | 按权重评分排序，先过滤状态异常的 |
| 分析角度 | `node_stocks[target]` + `analysis_config` + `stock_status_table` | `_angle_results` | 多维度计算，先过滤状态异常的 |
| 看盘面板 | `node_stocks` + `dashboard_schema` + `stock_status_table` | `_dashboard_data` | 组装显示数据，显示股票状态 |
| 预警检查 | `alert_rules` + `node_changes` + `stock_status_table` | `_alert_queue` | 规则匹配 + cooldown 检查，状态异常的跳过 |

---

## 九、概念变化对照表（v1.12 → v1.13）

### 9.1 表驱动：诚实化分层

| v1.12（吹牛） | v1.13（诚实） | 理由 |
|--------------|--------------|------|
| "加新节点不改代码" | **L1 分发 + 部分 L2 组合** | 诚实：加新节点要写 handler 代码（L1），只有组合新 filter 不改代码（L2） |
| "真正的表驱动" | **表驱动有三层，我们在 L1+L2** | 不吹牛，是什么级别说什么级别 |
| node_type_table（1张粗表） | **node_table + node_behavior_table + edge_behavior_table + ui_table（4张正交表）** | 恢复正交性，每个表管一个维度 |
| 没有 L1/L2/L3 概念 | **明确定义 L1/L2/L3** | 清晰的层级，知道自己在哪里，要去哪里 |

### 9.2 时间模型：从无到有

| v1.12（几乎空白） | v1.13（完整设计） | 理由 |
|-------------------|-------------------|------|
| 只有 tick_interval | **多周期模型（tick/1m/5m/15m/30m/60m/1d）** | 股票计算的核心复杂度就是时间，不能没有 |
| 没有周期边界概念 | **已完成K线 vs 未完成K线** | 实盘需要实时性，但也要知道什么是确定的什么是不确定的 |
| 没有周期确认事件 | **周期确认事件（period_confirmed）** | 时间驱动的边，触发时机和周期确认对齐 |
| 没有时间对齐概念 | **三种对齐策略（最新值/确认值/左对齐）** | 跨周期比较需要时间对齐 |
| 只有数据驱动触发 | **数据驱动 + 时间驱动 两种触发模式** | 有些策略每天算一次就行，不用每个 tick 都算 |

### 9.3 实盘边界：从假设正常到处理异常

| v1.12（默认正常） | v1.13（处理边界） | 理由 |
|-------------------|-------------------|------|
| 假设所有股票都正常 | **股票状态独立（正常/停牌/数据不足/异常）** | 实盘有各种边界情况，不能假设都正常 |
| 二态逻辑（True/False） | **三态逻辑（True/False/None）** | 不确定性需要用三态处理，不能简单地 True/False |
| 没有停牌处理 | **完整停牌处理方案** | A 股每天都有停牌的，必须处理 |
| 没有新股处理 | **完整新股处理方案** | 新股数据不足，不能直接用 |
| 没有数据异常处理 | **完整数据异常处理方案** | 数据偶尔会有异常，必须处理 |
| （隐式）激进策略 | **保守策略（不确定的不入池）** | 宁可漏掉不错入，更安全 |

### 9.4 表数量变化

| 版本 | 配置表数 | 核心运行时表数 | 变化 |
|------|---------|---------------|------|
| v1.11 | 0 张 | 8 张 | — |
| v1.12 | 4 张 | 7 张 | +4 行为表，-1 状态表（收敛脏标记） |
| **v1.13** | **9 张** | **8 张** | **node_type_table 拆为 4 张 + 新增 2 张时间配置表 + 1 张股票状态表** |

---

## 十、实现路线图（v1.13）

### 阶段一：表驱动分层诚实化 + 正交拆分（P0）

1. **更新所有架构文档**
   - 加上 L1/L2/L3 三层级定义
   - 诚实说明当前是 L1 + 部分 L2
   - 停止"加新节点不改代码"的吹牛

2. **拆分 node_type_table**
   - 拆成 node_table（基本属性）
   - 拆成 node_behavior_table（节点行为）
   - 拆成 edge_behavior_table（边行为）
   - 拆成 ui_table（UI 属性）
   - 确保正交：改一个维度不影响其他维度

3. **更新所有引用**
   - 所有读 node_type_table 的地方，改成读对应的正交表
   - 确保逻辑等价，不引入 bug

### 阶段二：时间模型设计落地（P0）

1. **定义周期表（period_table）**
   - 各周期的长度、确认时机
   - 周期之间的聚合关系
   - 支持 tick/1m/5m/15m/30m/60m/1d

2. **实现多周期数据管理**
   - period_data：各周期的已完成K线 + 当前未完成K线
   - K线聚合：短周期聚合成K线周期
   - 未完成K线更新

3. **实现周期确认事件**
   - 检测周期确认
   - 发 period_confirmed 事件
   - 事件队列管理

4. **实现时间驱动的边**
   - 触发模式：data_driven / time_driven
   - 时间驱动边的执行逻辑
   - 用确认K线计算，不是用未完成K线

### 阶段三：实盘边界场景处理（P0）

1. **实现股票状态表（stock_status_table）**
   - 四种状态：normal / suspended / insufficient_data / abnormal
   - 状态检测逻辑
   - 状态转换规则

2. **实现三态逻辑**
   - 比较结果支持 True/False/None
   - AND/OR/NOT 的三态版本
   - 三态逻辑的集合运算

3. **实现停牌/新股/数据异常处理**
   - 停牌：检测 + 处理 + 传播策略
   - 新股：数据不足检测 + 处理
   - 数据异常：异常检测 + 处理

4. **实现保守策略**
   - 默认：不确定的不入池
   - 可配置：用户可以选择不同的策略
   - 排名类先过滤异常股票

### 阶段四：测试验证（P0）

1. **表驱动拆分验证**
   - 拆分前后行为一致
   - 正交性验证：改一个维度不影响其他维度
   - 回归测试全覆盖

2. **时间模型验证**
   - 多周期数据正确性
   - 周期确认事件时机正确
   - 时间驱动边执行正确
   - 未完成K线 vs 确认K线的指标计算正确

3. **边界场景验证**
   - 停牌股票处理正确
   - 新股处理正确
   - 数据异常处理正确
   - 三态逻辑正确
   - 保守策略正确

4. **端到端验证**
   - 完整股票池运行测试
   - 和 v1.12 结果对比（正常场景下应该一致）
   - 边界场景下行为符合预期

---

## 十一、统计总结（v1.12 → v1.13）

### 11.1 概念数量变化

| 统计项 | v1.12 | v1.13 | 变化 |
|--------|------|-------|------|
| 配置表数 | 4 张 | **9 张** | **+5（1拆4 + 新增2张时间表）** |
| 核心运行时表 | 7 张 | **8 张** | **+1（股票状态表）** |
| 表驱动层级 | 1 层（模糊） | **3 层（L1/L2/L3，清晰定义）** | 诚实化分层 |
| 时间模型 | 几乎空白 | **完整设计** | 多周期 + 周期边界 + 未完成K线 + 周期确认 |
| 股票状态 | 隐式（假设正常） | **显式 4 种状态** | 正常/停牌/数据不足/异常 |
| 逻辑系统 | 二态（True/False） | **三态（True/False/None）** | 处理不确定性 |
| 边触发模式 | 1 种（数据驱动） | **2 种（数据驱动 + 时间驱动）** | 时间驱动的边和周期确认对齐 |

### 11.2 为什么是 v1.13？

**v1.13 是"诚实 + 补全 + 正交 + 落地"的版本：**

1. **诚实**：表驱动分层（L1/L2/L3），不吹牛说"加新节点不改代码"
2. **补全**：时间模型——股票计算的核心复杂度，之前完全忽略了
3. **正交**：拆分 node_type_table，每个表管一个维度，恢复正交性
4. **落地**：实盘边界场景——停牌/新股/数据异常，三态逻辑，保守策略

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
  v2.0：完整稳定版（所有功能完善，文档齐全）
```

**v1.13 解决的四个核心问题：**
1. **诚实问题**：表驱动分层，不吹牛——L1 就是 L1，L2 就是 L2
2. **时间问题**：补全时间模型——多周期、周期边界、未完成K线、周期确认
3. **正交问题**：拆分 node_type_table——每个表管一个维度，改一个不影响其他
4. **边界问题**：实盘边界场景——停牌/新股/数据异常，三态逻辑，保守策略

诚实比吹牛重要，补全比忽略重要，正交比混杂重要，落地比空想重要。

### 11.3 一句话总结

**v1.13：表驱动分层诚实化（L1+L2，不吹牛）+ 时间模型补全（多周期/周期边界/未完成K线/周期确认）+ node_type_table 正交拆分（4张表）+ 实盘边界场景落地（停牌/新股/数据异常，三态逻辑，保守策略）。**
