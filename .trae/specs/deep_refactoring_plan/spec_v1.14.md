# 股票池深度重构规划 v1.14

> 版本主题：L2组合驱动落地 + 双时间模型 + 交易日历 + 正交边界
> 设计原则：诚实不吹牛、正交拆分、三态逻辑、保守策略
> 目标：落地 L2 组合表驱动（filter 三层组合 = 改表不改代码），澄清数据时间 vs 系统时间双时间模型，建立交易日历基础设施，明确每张表的正交职责边界

---

## v1.13 → v1.14 变更摘要表

**变更日期：** 2026-07-01

| # | 变更项 | v1.13 | v1.14 | 本质变化 |
|---|--------|------|-------|---------|
| 1 | **L2组合表驱动落地** | "部分实现"（概念层面） | **真正落地：filter 三层组合 = 改表不改代码** | 从概念到落地：formula_table + operator_table + combine_table 三张表组合出 filter，加新 filter 完全是配置，不需要写代码 |
| 2 | **双时间模型** | 只有"时间"一个模糊概念 | **数据时间 vs 系统时间，两个完全不同的概念** | 澄清核心混淆：数据时间（市场时间）vs 系统时间（处理时间），时间触发用数据时间，超时心跳用系统时间 |
| 3 | **交易日历** | 只有 market_calendar 简单配置 | **完整交易日历基础设施** | 从简单时段配置升级为完整交易日历：交易日判断、交易时段、午间休市、节假日、周末处理 |
| 4 | **正交边界澄清** | 拆分了 4 张表，但边界还有模糊 | **每张表的职责边界彻底澄清** | trigger_mode 归边行为、name 分显示名/唯一标识、position 归 UI，彻底正交不交叉 |
| 5 | **配置表清单更新** | 9 张配置表 | **11 张（+ formula_table + trade_calendar_table）** | 新增指标公式表（formula_table）和交易日历表（trade_calendar_table） |
| 6 | **核心循环更新** | 有时间模型但无组合逻辑 | **体现 filter 三层组合逻辑 + 双时间模型** | 核心循环伪代码中明确展示三层组合的查表调用流程，以及数据时间/系统时间的区分使用 |
| 7 | **功能-表操作对应表更新** | 部分功能表对应模糊 | **每张表的职责明确，功能-表对应更清晰** | 正交边界澄清后，每个功能读什么表写什么表更明确，不交叉不重叠 |

**一句话总结 v1.14 升级：** 落地——L2 组合表驱动真正落地（filter 三层组合 = 改表不改代码）；澄清——双时间模型（数据时间 vs 系统时间，不再混淆）；基础——交易日历（时间模型的基础设施，所有时间计算的依赖）；正交——边界彻底澄清（每张表管什么，不管什么，清清楚楚）。

---

## 一、L2 组合表驱动落地（filter 三层组合 = 真正的改表不改代码）

### 1.1 之前的问题：还在概念层面

**v1.13 的说法（概念层面）：**
- L2 组合表驱动：行为由多个表的组合决定
- filter = 指标 × 比较算子 × 集合运算
- 加新 filter = 组合已有的表，不改代码

**但 v1.13 的问题：**
- 只有概念，没有明确的表结构定义
- 没有明确的组合逻辑伪代码
- "指标"从哪里来？没有明确的 formula_table
- 组合逻辑是通用的吗？没有说清楚
- 还不够"落地"

**v1.14 的落地：**
- 明确三张表：formula_table（指标）+ operator_table（比较算子）+ combine_table（组合方式）
- 明确组合逻辑：filter_result = combine( compare( indicator(stock_data) ) )
- 明确组合逻辑是通用的：不需要为每种 filter 写代码
- 加新 filter = 选一个指标 + 选一个算子 + 选一个组合方式，完全是配置
- 这才是真正的 L2 组合表驱动，真正的"改表不改代码"

### 1.2 filter 三层组合模型

**核心公式：**
```
filter_result = combine( compare( indicator(stock_data) ) )
```

**三层拆解：**

| 层级 | 表名 | 职责 | 输入 | 输出 |
|------|------|------|------|------|
| **第一层：指标计算** | `formula_table` | 计算技术指标值 | 股票K线数据 | 指标值（数值） |
| **第二层：比较判断** | `operator_table` | 指标值和阈值比较 | 指标值 + 阈值 | 单条件布尔值（True/False/None） |
| **第三层：组合运算** | `combine_table` | 多个条件组合 | 多个布尔值 | 最终 filter 结果（True/False/None） |

**组合逻辑是通用的：**
- 先算指标（查 formula_table）
- 再比较（查 operator_table）
- 再组合（查 combine_table）
- 这个流程是固定的，不需要为每种 filter 写代码
- 不同的 filter 只是三张表的不同组合

### 1.3 表 1：formula_table（指标公式表）

**定义：所有可用的技术指标公式。**

| 字段 | 类型 | 说明 |
|------|------|------|
| formula_id | str | 指标公式 ID（唯一标识） |
| name | str | 指标名称（显示用） |
| formula | str | TDX 风格公式字符串 |
| params | List[dict] | 参数定义（参数名、默认值、最小值、最大值） |
| output_type | str | 输出类型（scalar / series / bool） |
| min_bars | int | 最小需要的K线数量 |
| category | str | 分类（趋势/震荡/成交量/情绪等） |

**示例数据：**
```json
{
  "formula_table": {
    "ma5": {
      "name": "5日均线",
      "formula": "MA(CLOSE, 5)",
      "params": [],
      "output_type": "scalar",
      "min_bars": 5,
      "category": "trend"
    },
    "ma10": {
      "name": "10日均线",
      "formula": "MA(CLOSE, 10)",
      "params": [],
      "output_type": "scalar",
      "min_bars": 10,
      "category": "trend"
    },
    "vol": {
      "name": "成交量",
      "formula": "VOL",
      "params": [],
      "output_type": "scalar",
      "min_bars": 1,
      "category": "volume"
    },
    "change_pct": {
      "name": "涨跌幅",
      "formula": "(CLOSE - REF(CLOSE, 1)) / REF(CLOSE, 1) * 100",
      "params": [],
      "output_type": "scalar",
      "min_bars": 2,
      "category": "trend"
    },
    "macd_diff": {
      "name": "MACD差",
      "formula": "EMA(CLOSE, 12) - EMA(CLOSE, 26)",
      "params": [],
      "output_type": "scalar",
      "min_bars": 26,
      "category": "trend"
    }
  }
}
```

**加新指标 = 要写代码吗？**
- 如果公式引擎能算：不用写代码，加一行配置就行（L2）
- 如果需要新函数：要写代码，写完注册到 formula_funcs.json（L1）
- 大部分常用指标，公式引擎都能算，不用写代码

### 1.4 表 2：operator_table（比较算子表）

**定义：所有可用的比较算子。**

| 字段 | 类型 | 说明 |
|------|------|------|
| operator_id | str | 算子 ID（唯一标识） |
| name | str | 算子名称（显示用） |
| symbol | str | 符号表示（如 >, <, =） |
| handler | str | 比较函数名 |
| param_count | int | 需要的参数数量（除了指标值本身） |
| param_names | List[str] | 参数名称列表 |
| accept_none | bool | 是否接受 None 输入（三态逻辑） |

**示例数据：**
```json
{
  "operator_table": {
    "gt": {
      "name": "大于",
      "symbol": ">",
      "handler": "op_gt",
      "param_count": 1,
      "param_names": ["threshold"],
      "accept_none": true
    },
    "gte": {
      "name": "大于等于",
      "symbol": ">=",
      "handler": "op_gte",
      "param_count": 1,
      "param_names": ["threshold"],
      "accept_none": true
    },
    "lt": {
      "name": "小于",
      "symbol": "<",
      "handler": "op_lt",
      "param_count": 1,
      "param_names": ["threshold"],
      "accept_none": true
    },
    "lte": {
      "name": "小于等于",
      "symbol": "<=",
      "handler": "op_lte",
      "param_count": 1,
      "param_names": ["threshold"],
      "accept_none": true
    },
    "eq": {
      "name": "等于",
      "symbol": "==",
      "handler": "op_eq",
      "param_count": 1,
      "param_names": ["threshold"],
      "accept_none": true
    },
    "between": {
      "name": "区间内",
      "symbol": "between",
      "handler": "op_between",
      "param_count": 2,
      "param_names": ["low", "high"],
      "accept_none": true
    },
    "cross_above": {
      "name": "上穿",
      "symbol": "cross_above",
      "handler": "op_cross_above",
      "param_count": 1,
      "param_names": ["threshold"],
      "accept_none": true
    },
    "cross_below": {
      "name": "下穿",
      "symbol": "cross_below",
      "handler": "op_cross_below",
      "param_count": 1,
      "param_names": ["threshold"],
      "accept_none": true
    }
  }
}
```

**加新算子 = 要写代码吗？**
- 要写代码：每个算子的比较逻辑是不同的
- 写完注册到 operator_table 里
- 这是 L1，不是 L2
- 但算子的数量是有限的，常用的也就 10 个左右

### 1.5 表 3：combine_table（组合运算表）

**定义：所有可用的多条件组合方式。**

| 字段 | 类型 | 说明 |
|------|------|------|
| combine_id | str | 组合方式 ID（唯一标识） |
| name | str | 组合方式名称（显示用） |
| handler | str | 组合函数名 |
| condition_count | str | 条件数量（fixed / variable） |
| fixed_count | int | 固定条件数（fixed 时有效） |
| support_ternary | bool | 是否支持三态逻辑（True/False/None） |
| is_rank | bool | 是否是排名类组合 |

**示例数据：**
```json
{
  "combine_table": {
    "and": {
      "name": "且（全部满足）",
      "handler": "combine_and",
      "condition_count": "variable",
      "fixed_count": 0,
      "support_ternary": true,
      "is_rank": false
    },
    "or": {
      "name": "或（任一满足）",
      "handler": "combine_or",
      "condition_count": "variable",
      "fixed_count": 0,
      "support_ternary": true,
      "is_rank": false
    },
    "not": {
      "name": "非（取反）",
      "handler": "combine_not",
      "condition_count": "fixed",
      "fixed_count": 1,
      "support_ternary": true,
      "is_rank": false
    },
    "top_n": {
      "name": "前N名",
      "handler": "combine_top_n",
      "condition_count": "fixed",
      "fixed_count": 1,
      "support_ternary": true,
      "is_rank": true
    },
    "bottom_n": {
      "name": "后N名",
      "handler": "combine_bottom_n",
      "condition_count": "fixed",
      "fixed_count": 1,
      "support_ternary": true,
      "is_rank": true
    },
    "top_pct": {
      "name": "前N%",
      "handler": "combine_top_pct",
      "condition_count": "fixed",
      "fixed_count": 1,
      "support_ternary": true,
      "is_rank": true
    }
  }
}
```

**加新组合方式 = 要写代码吗？**
- 要写代码：每种组合方式的逻辑是不同的
- 写完注册到 combine_table 里
- 这是 L1，不是 L2
- 但组合方式的数量也是有限的，常用的也就 10 个以内

### 1.6 真正的 L2：组合出新 filter 不改代码

**关键洞察：**
- 指标（formula_table）：可以有很多个，大部分不用写代码
- 算子（operator_table）：数量有限，约 10 个
- 组合方式（combine_table）：数量有限，约 10 个
- **三者的组合数量 = 指标数 × 算子数 × 组合数**
- 这个组合数量可以非常大，但都不需要写代码

**例子 1：MA5 > 10 且 成交量 > 1000万**
```
条件1：MA5 > 10
  - 指标：ma5（formula_table）
  - 算子：gt（operator_table）
  - 参数：threshold = 10

条件2：成交量 > 1000万
  - 指标：vol（formula_table）
  - 算子：gt（operator_table）
  - 参数：threshold = 10000000

组合：and（combine_table）
  - 两个条件都满足

加这个 filter = 配置三个条件 + 一个组合方式
不需要写代码 ✓
```

**例子 2：涨幅前 10**
```
指标：change_pct（formula_table）
算子：无（直接用指标值排名）
组合：top_n（combine_table）
  - 参数：n = 10

加这个 filter = 选一个指标 + 选一个排名组合方式
不需要写代码 ✓
```

**例子 3：MACD 金叉（DIFF 上穿 DEA）**
```
指标：macd_diff（formula_table）
算子：cross_above（operator_table）
  - 参数：threshold = DEA 值（另一个指标）
组合：单条件（不需要 combine）

加这个 filter = 选指标 + 选算子
不需要写代码 ✓
```

### 1.7 filter 配置结构

**一条边的 filter 配置：**
```json
{
  "edge_id": "edge_1",
  "filter_config": {
    "conditions": [
      {
        "condition_id": "cond_1",
        "formula_id": "ma5",
        "operator_id": "gt",
        "params": {
          "threshold": 10.0
        }
      },
      {
        "condition_id": "cond_2",
        "formula_id": "vol",
        "operator_id": "gt",
        "params": {
          "threshold": 10000000
        }
      }
    ],
    "combine": {
      "combine_id": "and",
      "params": {}
    }
  }
}
```

**这个配置完全是数据，没有代码。**
- 加新 filter = 改这个配置
- 不需要改引擎代码
- 真正的"改表不改代码" ✓

### 1.8 filter 三层组合执行伪代码

```python
def execute_filter(edge, stock_codes, stock_data):
    """执行 filter 三层组合
    
    filter_result = combine( compare( indicator(stock_data) ) )
    """
    filter_config = edge['filter_config']
    conditions = filter_config['conditions']
    combine_cfg = filter_config['combine']
    
    # 第一层：计算所有指标（向量化批量计算）
    indicator_results = {}
    for cond in conditions:
        formula_id = cond['formula_id']
        formula_def = formula_table[formula_id]
        
        # 公式引擎批量计算所有股票的指标值
        values = formula_engine.eval_batch(
            formula=formula_def['formula'],
            symbols=stock_codes,
            period=edge.get('period', '1d'),
        )
        indicator_results[cond['condition_id']] = values
    
    # 第二层：比较判断（每只股票，每个条件）
    compare_results = {}
    for cond in conditions:
        cond_id = cond['condition_id']
        operator_id = cond['operator_id']
        op_def = operator_table[operator_id]
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
            
            # 比较判断
            cond_results[code] = op_func(indicator_val, params)
        
        compare_results[cond_id] = cond_results
    
    # 第三层：组合运算
    combine_id = combine_cfg['combine_id']
    combine_def = combine_table[combine_id]
    combine_func = handler_registry[combine_def['handler']]
    combine_params = combine_cfg['params']
    
    filter_result = combine_func(compare_results, stock_codes, combine_params)
    
    return filter_result
```

**关键点：**
1. 三层结构清晰：指标 → 比较 → 组合
2. 每层都查表：formula_table / operator_table / combine_table
3. 组合逻辑是通用的：不需要为每种 filter 写代码
4. 三态逻辑贯穿：每层都处理 None（不确定）
5. 向量化计算：指标层批量计算，性能高

### 1.9 节点行为也是组合的

**节点行为也是三层组合：**
```
节点行为 = 输入处理 + 内部状态 + 输出处理
```

| 部分 | 表名 | 职责 |
|------|------|------|
| 输入处理 | in_edge_handler | 入边数据怎么处理 |
| 内部状态 | 节点内部状态表 | 节点自己的状态数据 |
| 输出处理 | out_edge_handler | 出边数据怎么处理 |

**不同的节点类型 = 三部分的不同组合：**
- 数据源节点：输入处理 = 无（自己产生数据），内部状态 = 数据源配置，输出处理 = 直接传递
- 过滤节点：输入处理 = 透传，内部状态 = 过滤条件配置，输出处理 = 应用过滤
- 股票池节点：输入处理 = 合并去重，内部状态 = 股票列表，输出处理 = 传递变化

**节点组合也是 L2 吗？**
- 部分是：handler 还是要写代码（L1）
- 但 handler 的数量是有限的
- 不同的节点类型可以复用同一个 handler
- 比如：所有过滤类节点可以复用同一个 filter handler
- 这也是一种组合，但组合的粒度更粗

### 1.10 现有代码中的 L2 验证

**验证 1：formula_funcs.json 是 L2 组合的雏形**
- `formula_funcs.json` → `_dispatch_func`（formula_engine.py:214）
- 通用算子：window_op / shift_op / cross_op
- 加新函数 = 加一行配置 + 可能用已有的通用算子
- ✅ 符合：这就是 L2 的雏形，但只在公式引擎内部

**验证 2：timing.json 是 L1 分发表驱动**
- `timing.json:starttype_rules` → 查表分发
- 每个 primitive 是一个 handler
- 加新 primitive 要写新函数代码
- ✅ 符合：这是 L1，不是 L2

**验证 3：filter 还没有完整的三层组合**
- 现有代码中 filter 逻辑还是耦合的
- 没有明确的 formula_table + operator_table + combine_table 三层结构
- ✅ 符合：v1.13 还在概念层面，v1.14 落地

---

## 二、数据时间 vs 系统时间（双时间模型）

### 2.1 之前的问题：混了

**v1.13 及之前的问题：**
- 只有一个"时间"的模糊概念
- 数据时间和系统时间混在一起用
- 时间触发用哪个时间？不清楚
- 周期确认用哪个时间？不清楚
- 超时心跳用哪个时间？不清楚

**为什么这是大问题：**
- 两个时间是完全不同的概念
- 混着用会出各种诡异的 bug
- 比如：行情延迟了，"9:30触发"到底什么时候触发？
- 比如：K线时间戳乱序了，周期怎么确认？

### 2.2 两个完全不同的时间概念

| 维度 | 数据时间（市场时间） | 系统时间（处理时间） |
|------|-------------------|-------------------|
| **定义** | 行情数据的时间戳（tick 的 time、K线的 time） | 服务器的本地时间 |
| **来源** | 交易所、行情数据源 | 服务器操作系统时钟 |
| **单调性** | 不一定单调，可能乱序、可能缺失 | 单调递增，不会乱序 |
| **准确性** | 可能有延迟、可能有误差 | 相对准确（NTP 同步） |
| **代表什么** | "市场上发生了什么" | "我们什么时候处理的" |
| **另一个名字** | 事件时间（Event Time） | 处理时间（Processing Time） |

**通俗理解：**
- 数据时间 = 市场的时间（行情什么时候发生的）
- 系统时间 = 我们的时间（我们什么时候收到/处理的）
- 两者可能有偏差（行情延迟、网络延迟）

### 2.3 时间触发用哪个时间？

**答案：数据时间（市场时间）**

**为什么？**
- "9:30触发"指的是市场时间 9:30，不是系统时间 9:30
- 因为策略是基于市场时间的，不是基于我们服务器的时间
- 如果行情延迟了 5 秒，系统时间 9:30:05 才收到 9:30:00 的数据
- 这时候"9:30触发"应该在收到 9:30:00 的数据时触发，而不是在系统时间 9:30:00 触发

**例子：**
```
系统时间线：  9:29:58  9:29:59  9:30:00  9:30:01  9:30:02  9:30:03  9:30:04  9:30:05
数据时间线：  9:29:57  9:29:58  9:29:59  (延迟)  (延迟)  (延迟)  (延迟)  9:30:00

"9:30触发"应该在什么时候触发？
  ✅ 系统时间 9:30:05（收到数据时间 9:30:00 的数据时）
  ❌ 系统时间 9:30:00（那时候数据还没到，算不了）
```

### 2.4 周期确认用哪个时间？

**答案：数据时间（市场时间）**

**为什么？**
- 分钟K线的确认，是看数据时间跨过了分钟边界
- 不是看系统时间跨过了分钟边界
- 因为 K 线是基于市场时间的，不是基于我们服务器的时间

**例子：**
```
系统时间：  10:30:00  10:30:01  10:30:02
数据时间：  10:29:58  10:29:59  10:30:00

1分钟K线什么时候确认？
  ✅ 收到数据时间 10:30:00 的数据时（确认 10:29 的K线）
  ❌ 系统时间 10:30:00 的时候（那时候数据还没到）
```

### 2.5 超时、心跳用哪个时间？

**答案：系统时间（处理时间）**

**为什么？**
- "30秒没收到数据算断线"，这个 30 秒是用系统时间算的
- 因为超时是关于"我们多久没收到数据了"，不是"市场多久没动静了"
- 心跳也是一样，是我们的健康检查，用系统时间

**例子：**
```
系统时间：  10:00:00  10:00:10  10:00:20  10:00:30  10:00:40
数据时间：  10:00:00  (没收到)  (没收到)  (没收到)  (没收到)

"30秒没收到数据算断线"
  ✅ 系统时间 10:00:30 的时候，距离上次收到数据已经 30 秒了，算断线
  ❌ 用数据时间算不出来（因为根本没收到数据）
```

### 2.6 双时间模型对照表

| 场景 | 用哪个时间 | 原因 |
|------|-----------|------|
| **时间触发（9:30触发）** | 数据时间 | 策略基于市场时间，不是服务器时间 |
| **周期确认（分钟/日线）** | 数据时间 | K线基于市场时间，确认要看数据时间边界 |
| **指标计算（MA、MACD等）** | 数据时间 | 指标基于行情数据，用数据时间序列 |
| **回测/回放** | 数据时间 | 回测就是模拟历史，用历史数据的时间 |
| **超时检测（30秒没数据）** | 系统时间 | 超时是"我们多久没收到数据"，用系统时间 |
| **心跳/健康检查** | 系统时间 | 健康检查是我们的状态，用系统时间 |
| **日志时间戳** | 系统时间 | 日志记录什么时候发生的，用系统时间 |
| **性能统计（耗时）** | 系统时间 | 性能是我们处理的快慢，用系统时间 |
| **TTL 过期** | 系统时间 | TTL 是"入池多久了"，用系统时间算 |

**简单记忆：**
- 和"市场/行情/数据"相关的 → 用数据时间
- 和"我们/系统/处理"相关的 → 用系统时间

### 2.7 数据时间可能乱序怎么办？

**数据时间的特点：**
- 不一定单调递增
- 可能有乱序（后发的先到）
- 可能有缺失（某个时间点没数据）
- 可能有延迟（数据晚到）

**处理策略：**

| 问题 | 处理方式 | 说明 |
|------|---------|------|
| **乱序** | 水位线（Watermark） + 窗口 | 等一段时间，让迟到的数据到齐再处理 |
| **缺失** | 插值 / 跳过 | 看具体场景，K线缺失可以用上一根填充 |
| **延迟** | 容忍 + 告警 | 允许一定延迟，超过阈值告警 |

**股票池场景的简化策略：**
- 股票池对实时性要求不是特别高
- 可以用一个简单的"延迟窗口"：比如等 3 秒，3 秒内的数据都到齐了再处理
- 乱序的数据，只要在窗口内到了，都算有效
- 超过窗口的迟到数据，直接丢弃（或者特殊处理）

### 2.8 现有代码中的时间模型验证

**验证 1：timing.json 里有 market_calendar，但只有时段**
- `timing.json:market_calendar`（timing.json:29-44）
- 只有 open_sec / close_sec / sessions
- 没有交易日历的概念（不知道哪天是交易日）
- ✅ 符合：之前只有简单时段，没有完整交易日历

**验证 2：time_sources.json 区分了 wall_clock / sequence / virtual**
- `time_sources.json:time_sources`（time_sources.json:4-35）
- 三种时间源：wall_clock（系统时钟）、sequence（K线时间轴）、virtual（虚拟时钟）
- 这已经有"双时间"的雏形了
- ✅ 符合：有雏形，但还没有明确的数据时间 vs 系统时间的概念

**验证 3：公式引擎用 bars 的 index 作为时间**
- formula_engine 里用 `bars.index` 做时间序列
- 这是数据时间（K线的时间）
- ✅ 符合：指标计算用数据时间，是对的

---

## 三、交易日历（时间模型的基础设施）

### 3.1 之前的问题：只有简单时段

**v1.13 及之前的时间配置：**
- 只有 market_calendar.open_sec / close_sec
- 只有 sessions（上午/下午时段）
- 不知道哪天是交易日
- 不知道节假日
- 不知道午间休市的完整处理
- 周末处理也很简单

**为什么这是大问题：**
- 交易日历是所有时间计算的基础
- 判断"现在是不是交易时间"，首先要判断"今天是不是交易日"
- 周期确认、时间触发，都要先看是不是在交易时段内
- 没有交易日历，时间模型就是不完整的

### 3.2 交易日历的功能

**交易日历是时间模型的基础设施，所有时间相关的计算都依赖它。**

| 功能 | 说明 |
|------|------|
| **判断某一天是不是交易日** | 排除周末、节假日 |
| **获取交易日的开始/结束时间** | 开盘时间、收盘时间 |
| **判断当前是不是在交易时段内** | 包括午间休市处理 |
| **获取下一个交易日** | 今天不是交易日，找下一个 |
| **获取上一个交易日** | 往前找交易日 |
| **计算两个日期之间的交易日数** | 比如"5个交易日后" |
| **节假日管理** | 节假日列表、调休处理 |
| **交易时段管理** | 不同市场、不同品种的交易时段 |

### 3.3 trade_calendar_table（交易日历表）

**定义：交易日历配置表。**

| 字段 | 类型 | 说明 |
|------|------|------|
| market | str | 市场代码（SH/SZ/CFFEX等） |
| timezone | str | 时区（Asia/Shanghai） |
| trading_sessions | List[dict] | 交易时段列表（一天内的多个时段） |
| holidays | List[str] | 节假日列表（YYYY-MM-DD） |
| extra_trading_days | List[str] | 额外交易日（调休的周末） |
| half_days | List[str] | 半天交易日（如除夕） |
| half_day_close_time | str | 半天的收盘时间（如 11:30:00） |

**示例数据：**
```json
{
  "trade_calendar_table": {
    "A股": {
      "market": "A股",
      "timezone": "Asia/Shanghai",
      "trading_sessions": [
        {
          "name": "morning",
          "start": "09:30:00",
          "end": "11:30:00"
        },
        {
          "name": "afternoon",
          "start": "13:00:00",
          "end": "15:00:00"
        }
      ],
      "holidays": [
        "2026-01-01",
        "2026-01-28",
        "2026-01-29",
        "2026-01-30",
        "2026-02-02",
        "2026-04-06",
        "2026-05-01",
        "2026-05-02",
        "2026-05-03",
        "2026-05-04",
        "2026-05-05",
        "2026-06-19",
        "2026-10-01",
        "2026-10-02",
        "2026-10-05",
        "2026-10-06",
        "2026-10-07",
        "2026-10-08"
      ],
      "extra_trading_days": [
        "2026-02-01",
        "2026-05-09",
        "2026-10-11"
      ],
      "half_days": [
        "2026-02-17"
      ],
      "half_day_close_time": "11:30:00"
    }
  }
}
```

### 3.4 交易日历的核心接口

```python
class TradeCalendar:
    """交易日历"""
    
    def is_trading_day(self, date: datetime.date) -> bool:
        """判断某一天是不是交易日"""
        # 1. 先看是不是周末
        if date.weekday() >= 5:  # 周六周日
            if date in extra_trading_days:
                return True
            return False
        
        # 2. 再看是不是节假日
        if date in holidays:
            return False
        
        return True
    
    def is_trading_time(self, dt: datetime.datetime) -> bool:
        """判断某个时间点是不是在交易时段内"""
        # 1. 先看今天是不是交易日
        if not self.is_trading_day(dt.date()):
            return False
        
        # 2. 看当前时间在不在交易时段内
        current_seconds = dt.hour * 3600 + dt.minute * 60 + dt.second
        
        # 半天交易日特殊处理
        if dt.date() in half_days:
            half_end = time_to_seconds(half_day_close_time)
            for session in trading_sessions:
                start = time_to_seconds(session['start'])
                end = min(time_to_seconds(session['end']), half_end)
                if start <= current_seconds <= end:
                    return True
            return False
        
        # 正常交易日
        for session in trading_sessions:
            start = time_to_seconds(session['start'])
            end = time_to_seconds(session['end'])
            if start <= current_seconds <= end:
                return True
        
        return False
    
    def next_trading_day(self, date: datetime.date) -> datetime.date:
        """获取下一个交易日"""
        current = date + timedelta(days=1)
        while not self.is_trading_day(current):
            current += timedelta(days=1)
        return current
    
    def prev_trading_day(self, date: datetime.date) -> datetime.date:
        """获取上一个交易日"""
        current = date - timedelta(days=1)
        while not self.is_trading_day(current):
            current -= timedelta(days=1)
        return current
    
    def trading_days_between(self, start: datetime.date, end: datetime.date) -> int:
        """计算两个日期之间的交易日数"""
        count = 0
        current = start
        while current <= end:
            if self.is_trading_day(current):
                count += 1
            current += timedelta(days=1)
        return count
    
    def get_open_time(self, date: datetime.date) -> Optional[datetime.datetime]:
        """获取某天的开盘时间"""
        if not self.is_trading_day(date):
            return None
        first_session = trading_sessions[0]
        h, m, s = map(int, first_session['start'].split(':'))
        return datetime.datetime(date.year, date.month, date.day, h, m, s)
    
    def get_close_time(self, date: datetime.date) -> Optional[datetime.datetime]:
        """获取某天的收盘时间"""
        if not self.is_trading_day(date):
            return None
        
        # 半天交易日特殊处理
        if date in half_days:
            h, m, s = map(int, half_day_close_time.split(':'))
            return datetime.datetime(date.year, date.month, date.day, h, m, s)
        
        last_session = trading_sessions[-1]
        h, m, s = map(int, last_session['end'].split(':'))
        return datetime.datetime(date.year, date.month, date.day, h, m, s)
```

### 3.5 交易日历在时间模型中的位置

```
时间模型层级：
  ┌─────────────────────────────────────┐
  │         双时间模型（上层概念）        │
  │    数据时间 vs 系统时间              │
  └─────────────────────────────────────┘
                    │
                    ▼
  ┌─────────────────────────────────────┐
  │         交易日历（基础设施）          │
  │  交易日判断 / 交易时段 / 节假日       │
  └─────────────────────────────────────┘
                    │
                    ▼
  ┌─────────────────────────────────────┐
  │         周期管理（周期确认）          │
  │  多周期K线 / 周期确认事件 / 未完成K线 │
  └─────────────────────────────────────┘
                    │
                    ▼
  ┌─────────────────────────────────────┐
  │         时间触发（边触发）            │
  │  数据驱动 / 时间驱动                 │
  └─────────────────────────────────────┘
```

**依赖关系：**
- 时间触发 → 依赖周期管理 → 依赖交易日历
- 所有时间相关的计算，最终都要落到交易日历上
- 交易日历是最底层的基础设施

### 3.6 午间休市处理

**A股的午间休市：11:30 - 13:00**

**这会影响什么？**

| 场景 | 午间休市怎么处理 |
|------|----------------|
| **时间触发** | 休市期间不触发，等下午开盘再触发 |
| **周期确认** | 休市期间没有数据，周期不前进 |
| **指标计算** | 休市期间没有新数据，指标值不变 |
| **超时检测** | 休市期间不算超时（本来就没数据） |
| **TTL 过期** | 休市期间 TTL 继续算（系统时间） |

**超时检测的特殊处理：**
- "30秒没收到数据算断线"
- 午间休市本来就没数据，不能算断线
- 所以超时检测要先判断是不是在交易时段内
- 交易时段内超时才算断线
- 非交易时段（休市、非交易日）不算超时

### 3.7 现有代码中的交易日历验证

**验证 1：timing.json 里有 market_calendar.sessions**
- `timing.json:market_calendar.sessions`（timing.json:32-43）
- 上午 9:30-11:30，下午 13:00-15:00
- 有午间休市的时段划分
- ✅ 符合：有简单的时段配置，但不是完整的交易日历

**验证 2：有 is_trading_time 的表达式**
- `time_sources.json:is_trading_time_expr`（time_sources.json:36-48）
- 判断当前秒数落在任一 session 的 [open_sec, close_sec] 区间内
- 有交易时间判断
- ✅ 符合：但只判断时段，不判断是不是交易日

**验证 3：没有节假日、周末处理**
- 搜遍代码，没有 holiday / weekend 之类的交易日历概念
- ✅ 符合：之前确实没有完整的交易日历

---

## 四、正交边界澄清（每张表的职责边界）

### 4.1 之前的问题：边界还有模糊

**v1.13 拆分了 4 张表，但还有一些边界问题：**

1. **trigger_mode 归哪里？**
   - v1.13 放在 edge_behavior_table
   - 对吗？trigger_mode 是边的行为吗？
   - 还是节点的行为？还是 UI 的？

2. **name（节点名称）归哪里？**
   - v1.13 放在 node_table
   - 但 name 既是唯一标识，又是显示名
   - 显示名应该归 ui_table 吧？
   - 唯一标识应该归 node_table

3. **position（节点位置）归哪里？**
   - 节点在画布上的位置坐标
   - 是 node_table 的属性？还是 ui_table 的？

4. **还有其他模糊的吗？**
   - color / icon → 归 ui_table（没问题）
   - handler → 归 behavior_table（没问题）
   - type_id → 归 node_table（没问题）

**v1.14 彻底澄清这些边界。**

### 4.2 正交原则：一张表只管一个维度

**什么是正交？**
- 改一个维度，不影响其他维度
- 每张表有清晰的职责边界
- 不交叉，不重叠
- 一个属性只属于一张表

**判断一个属性归哪张表的方法：**
- 改这个属性，会不会影响其他维度？
- 如果改 UI 不影响行为，那归 UI 表
- 如果改行为不影响 UI，那归行为表
- 如果改身份不影响行为和 UI，那归基本属性表

### 4.3 trigger_mode 归 edge_behavior_table

**问题：trigger_mode（数据驱动/时间驱动）归哪里？**

**答案：edge_behavior_table（边行为表）**

**为什么？**
- trigger_mode 决定边什么时候触发
- 触发时机是边的行为的一部分
- 改 trigger_mode 会改变边的行为
- 和 UI 无关，和节点基本属性无关

**验证：改 trigger_mode 影响什么？**
- 影响：边的执行时机（数据变化时触发 vs 周期确认时触发）
- 不影响：节点显示、节点身份、股票列表
- ✅ 属于行为维度，归 edge_behavior_table

### 4.4 name 分两个：显示名 vs 唯一标识

**问题：name（节点名称）归哪里？**

**答案：分两个：**
- **显示名（display_name）** → 归 ui_table
- **唯一标识（id / type_id）** → 归 node_table

**为什么要分？**
- 显示名是给人看的，可以改，可以多语言
- 唯一标识是给系统用的，不能改，必须唯一
- 两者的性质完全不同，不能混在一个字段里

**例子：**
```
node_table（基本属性）:
  type_id: "market_source"  ← 唯一标识，不能改，代码里用这个

ui_table（UI 属性）:
  display_name: "市场数据源"  ← 显示名，可以改，可以多语言
  color: "#4CAF50"
  icon: "database"
```

**验证：改显示名影响什么？**
- 影响：UI 显示
- 不影响：行为、逻辑、唯一标识
- ✅ 属于 UI 维度，归 ui_table

**验证：改唯一标识影响什么？**
- 影响：整个系统的引用关系
- 不影响：UI 显示、行为逻辑（只要 handler 不变）
- ✅ 属于基本属性维度，归 node_table

### 4.5 position 归 ui_table

**问题：position（节点在画布上的位置）归哪里？**

**答案：ui_table（UI 属性表）**

**为什么？**
- 位置是 UI 层面的概念
- 改位置不影响节点的行为
- 改位置不影响节点的基本属性
- 位置只是给用户看的布局

**验证：改 position 影响什么？**
- 影响：画布上节点的位置
- 不影响：节点行为、股票计算、逻辑
- ✅ 属于 UI 维度，归 ui_table

**注意：**
- position 是实例级的（每个节点实例有自己的位置）
- 不是类型级的（不是所有同类型节点位置都一样）
- 所以 position 应该在实例数据里，不是在类型配置表里
- 但它属于 UI 维度，不是行为维度，也不是基本属性维度

### 4.6 四张表的职责边界（彻底澄清版）

| 表名 | 管什么 | 不管什么 | 判断标准 |
|------|--------|----------|---------|
| **node_table** | 节点的身份、分类、结构属性 | 行为、UI 显示 | 改了会影响"这个节点是什么" |
| **node_behavior_table** | 节点的行为逻辑 | UI 显示、基本身份 | 改了会影响"节点怎么工作" |
| **edge_behavior_table** | 边的行为逻辑 | UI 显示、节点身份 | 改了会影响"边怎么工作" |
| **ui_table** | UI 显示相关 | 行为逻辑、基本身份 | 改了只影响"看起来什么样" |

**具体属性归属：**

| 属性 | 归哪张表 | 原因 |
|------|---------|------|
| type_id | node_table | 唯一标识，身份的一部分 |
| category | node_table | 分类，身份的一部分 |
| allowed_roles | node_table | 结构属性，决定能连什么边 |
| has_stocks | node_table | 结构属性，有没有股票列表 |
| max_in_edges | node_table | 结构属性，最多几条入边 |
| max_out_edges | node_table | 结构属性，最多几条出边 |
| init_handler | node_behavior_table | 初始化行为 |
| in_edge_handler | node_behavior_table | 入边处理行为 |
| out_edge_handler | node_behavior_table | 出边处理行为 |
| tick_handler | node_behavior_table | tick 处理行为 |
| gate_check_handler | edge_behavior_table | 门控检查行为 |
| filter_handler | edge_behavior_table | 过滤处理行为 |
| propagate_handler | edge_behavior_table | 传播处理行为 |
| trigger_mode | edge_behavior_table | 触发模式是边的行为 |
| default_trigger_period | edge_behavior_table | 触发周期是边的行为 |
| display_name | ui_table | 显示名，UI 的一部分 |
| color | ui_table | 颜色，UI 的一部分 |
| icon | ui_table | 图标，UI 的一部分 |
| position | ui_table（实例级） | 位置，UI 的一部分 |
| default_width | ui_table | 默认大小，UI 的一部分 |
| default_height | ui_table | 默认大小，UI 的一部分 |
| resizable | ui_table | 可调整大小，UI 的一部分 |
| show_label | ui_table | 显示标签，UI 的一部分 |

### 4.7 正交性验证（改一个维度不影响其他）

**验证 1：改 UI 不改行为**
- 改 display_name / color / icon / position
- 只改 ui_table
- node_behavior_table / edge_behavior_table 完全不用动
- ✅ 正交

**验证 2：改行为不改 UI**
- 改 handler / trigger_mode
- 只改 node_behavior_table / edge_behavior_table
- ui_table 完全不用动
- ✅ 正交

**验证 3：改基本属性不改行为和 UI**
- 改 max_in_edges / allowed_roles
- 只改 node_table
- node_behavior_table / ui_table 完全不用动
- ✅ 正交

**验证 4：行为复用**
- 两个节点类型行为一样，但 UI 不一样
- node_behavior_table 可以共用一行
- ui_table 各写各的
- ✅ 正交，减少重复

### 4.8 配置表的分类层级

```
配置表分类：
  ┌─────────────────────────────────────┐
  │         节点配置（4张，正交）         │
  │  node_table                        │
  │  node_behavior_table               │
  │  edge_behavior_table               │
  │  ui_table                          │
  └─────────────────────────────────────┘
  ┌─────────────────────────────────────┐
  │         算子配置（3张，L2组合）       │
  │  formula_table（指标公式）           │
  │  operator_table（比较算子）          │
  │  combine_table（组合运算）           │
  └─────────────────────────────────────┘
  ┌─────────────────────────────────────┐
  │         时间配置（2张，基础设施）     │
  │  period_table（周期定义）            │
  │  trade_calendar_table（交易日历）    │
  └─────────────────────────────────────┘
```

**三个维度，互不交叉：**
- 节点配置：管"有什么节点/边，它们怎么工作，长什么样"
- 算子配置：管"filter 怎么组合计算"
- 时间配置：管"时间相关的所有基础设施"

---

## 五、配置表清单（v1.14 更新版）

### 5.1 节点配置表（4 张，正交拆分，边界澄清）

| # | 表名 | 维度 | 读时机 | 写时机 | 说明 |
|---|------|------|--------|--------|------|
| 1 | `node_table` | 基本属性 | 初始化、校验时 | 配置加载时 | 节点的身份、分类、结构属性（type_id / category / allowed_roles / has_stocks / max_in_edges / max_out_edges） |
| 2 | `node_behavior_table` | 节点行为 | 处理节点时查表 | 配置加载时 | 节点的 init/in/out/tick/alert handler |
| 3 | `edge_behavior_table` | 边行为 | 处理边时查表 | 配置加载时 | 边的 gate/filter/propagate handler + trigger_mode + default_trigger_period |
| 4 | `ui_table` | UI 属性 | 渲染 UI 时 | 配置加载时 | display_name / color / icon / default_width / default_height / resizable / show_label |

### 5.2 算子配置表（3 张，L2 组合表驱动，真正落地）

| # | 表名 | 维度 | 读时机 | 写时机 | 说明 |
|---|------|------|--------|--------|------|
| 1 | `formula_table` | 指标公式 | 指标计算时查表 | 配置加载时 | **新增**。所有可用的技术指标公式（TDX 风格） |
| 2 | `operator_table` | 比较算子 | 比较判断时查表 | 配置加载时 | 每种比较算子的函数、参数（gt / lt / between / cross_above 等） |
| 3 | `combine_table` | 组合运算 | 集合运算时查表 | 配置加载时 | 每种组合方式的函数、参数（and / or / top_n / bottom_n 等） |

**L2 组合落地：三者组合出 filter（真正的改表不改代码）**
- filter_result = combine( compare( indicator(stock_data) ) )
- 加新 filter = 选指标 + 选算子 + 选组合方式，完全是配置
- 加新指标公式（公式引擎支持的）= 不改代码
- 加新算子/新组合方式 = 要写代码（L1）

### 5.3 时间配置表（2 张，交易日历基础设施）

| # | 表名 | 维度 | 读时机 | 写时机 | 说明 |
|---|------|------|--------|--------|------|
| 1 | `period_table` | 周期定义 | 周期管理、时间对齐时 | 配置加载时 | 各周期的长度、确认时机、聚合关系 |
| 2 | `trade_calendar_table` | 交易日历 | 所有时间计算前 | 配置加载时 | **新增**。交易日判断、交易时段、节假日、周末、午间休市 |

### 5.4 配置表汇总

| 类别 | 表数 | 表名 |
|------|------|------|
| 节点配置（正交） | 4 张 | node_table, node_behavior_table, edge_behavior_table, ui_table |
| 算子配置（L2组合） | 3 张 | formula_table, operator_table, combine_table |
| 时间配置（基础设施） | 2 张 | period_table, trade_calendar_table |
| **合计** | **11 张** | |

**v1.13 → v1.14 配置表变化：**

| 版本 | 配置表数 | 变化 |
|------|---------|------|
| v1.13 | 9 张 | node_table + node_behavior_table + edge_behavior_table + ui_table + operator_table + combine_table + propagate_table + period_table + time_trigger_table |
| v1.14 | 11 张 | **+ formula_table（指标公式表，L2落地）**<br>**+ trade_calendar_table（交易日历，基础设施）**<br>（propagate_table 合并到 combine_table 概念里，time_trigger_table 合并到 edge_behavior_table.trigger_mode 里） |

---

## 六、运行时表清单（v1.14 更新版）

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

### 6.2 时间相关运行时表（3 张，+1 交易日历缓存）

| # | 表名 | 类型 | 读时机 | 写时机 | 说明 |
|---|------|------|--------|--------|------|
| 1 | `period_data` | Dict[period → {completed_bars, current_bar}] | 指标计算、周期确认时 | 数据更新、周期确认时 | 各周期的已完成K线和当前未完成K线（数据时间） |
| 2 | `period_confirmed_events` | Queue[event] | 时间驱动边处理时 | 周期确认时 | 周期确认事件队列（数据时间驱动） |
| 3 | `trade_calendar_cache` | dict | 所有时间判断前 | 启动时加载 | 交易日历缓存（提高判断性能） |

### 6.3 运行时表汇总

| 类别 | 表数 | 表名 |
|------|------|------|
| 核心运行时表 | 8 张 | latest_tick, stock_status_table, node_stocks, ttl_expiry_queue, dirty_stocks, node_changes, edge_compare_results, edge_filter_results |
| 时间相关表 | 3 张 | period_data, period_confirmed_events, trade_calendar_cache |
| **合计** | **11 张** | |

---

## 七、核心循环伪代码（v1.14 更新版）

### 7.1 主循环：轮询 + 脏驱动 + 双时间模型 + L2 组合

```python
# ============================================================
#  v1.14 核心循环伪代码（轮询数据 + 脏驱动计算 + 双时间模型 + L2组合）
# ============================================================

# --- 初始化 ---
# 加载正交配置表（节点配置）
node_table = load_table('node_table')
node_behavior_table = load_table('node_behavior_table')
edge_behavior_table = load_table('edge_behavior_table')
ui_table = load_table('ui_table')

# 加载算子表（L2 组合）
formula_table = load_table('formula_table')      # 指标公式表
operator_table = load_table('operator_table')    # 比较算子表
combine_table = load_table('combine_table')      # 组合运算表

# 加载时间配置（基础设施）
period_table = load_table('period_table')
trade_calendar = load_trade_calendar('trade_calendar_table')

# 注册 handler
handler_registry = register_all_handlers()

# 初始化时间模型
period_data = init_period_data(period_table)
period_confirmed_events = Queue()

# 编译期：拓扑排序
topo_order = build_topo_order(nodes, edges)

# --- 主循环（轮询模型）---
tick_interval = 1.0  # 秒，系统时间间隔

while running:
    # 1. 等待下一个 tick（系统时间）
    await asyncio.sleep(tick_interval)
    system_time = datetime.datetime.now()  # 系统时间（处理时间）
    
    if paused:
        continue
    
    # 2. 轮询获取最新数据
    tick_data = poll_latest_data()
    
    # 3. 检查是不是交易时间（用交易日历 + 数据时间）
    if tick_data:
        sample_time = get_data_time(next(iter(tick_data.values())))  # 数据时间
        is_trading = trade_calendar.is_trading_time(sample_time)
    else:
        is_trading = trade_calendar.is_trading_time(system_time)  # 没数据时用系统时间近似
    
    # 4. 更新数据层 + 标记脏股票 + 更新股票状态
    dirty_stocks.clear()
    for code, new_bar in tick_data.items():
        # 更新 latest_tick（数据时间）
        if latest_tick.get(code) != new_bar:
            latest_tick[code] = new_bar
            dirty_stocks.add(code)
        
        # 检测并更新股票状态（停牌/新股/异常）
        old_status = stock_status_table.get(code, {}).get('status', 'normal')
        new_status = detect_stock_status(code, new_bar)
        if old_status != new_status:
            update_stock_status(code, new_status)
    
    # 5. 超时检测（用系统时间）
    # 如果 30 秒没收到数据，标记为断线
    check_timeout(system_time, timeout=30)
    
    # 6. 更新多周期数据 + 检测周期确认事件（用数据时间）
    period_confirmed_events.clear()
    if is_trading and dirty_stocks:
        for period in period_table:
            # 更新该周期的未完成K线（数据时间驱动）
            update_current_bar(period, tick_data)
            
            # 检查该周期是否有K线确认了（数据时间跨过边界）
            if period_bar_confirmed(period):
                # 把已完成的K线移到 completed_bars
                confirm_current_bar(period)
                # 发周期确认事件
                period_confirmed_events.put({
                    'period': period,
                    'confirmed_time': get_confirmed_bar_time(period),  # 数据时间
                })
                # 开始新的未完成K线
                start_new_current_bar(period)
    
    # 7. 如果没有数据变化 且 没有周期确认事件，跳过计算（脏驱动）
    if not dirty_stocks and period_confirmed_events.empty():
        continue
    
    # 8. 计算每个节点的变化（node_changes）
    compute_all_node_changes(dirty_stocks)
    
    # 9. 按拓扑序处理脏节点（表驱动 + L2 组合）
    for nid in topo_order:
        if not is_node_dirty(nid):
            continue
        
        # 查 node_behavior_table，调出边 handler
        node = nodes[nid]
        type_def = node_behavior_table[node['type']]
        handler_name = type_def['out_edge_handler']
        
        if handler_name:
            handler = handler_registry[handler_name]
            handler(nid, node, node_changes[nid])
    
    # 10. 处理时间驱动的边（周期确认事件触发，数据时间）
    while not period_confirmed_events.empty():
        event = period_confirmed_events.get()
        process_time_driven_edges(event)
    
    # 11. 后处理（PK 排名 / 分析角度 / 预警）
    post_process()
    
    # 12. TTL 过期检查（用系统时间）
    process_ttl_expiry(system_time)
    
    # 13. 清脏，为下一轮做准备
    clear_all_dirty()
```

### 7.2 filter 三层组合执行（L2 落地）

```python
def execute_filter_three_layer(edge, stock_codes):
    """filter 三层组合执行（L2 组合表驱动）
    
    filter_result = combine( compare( indicator(stock_data) ) )
    
    第一层：formula_table → 计算指标值
    第二层：operator_table → 比较判断
    第三层：combine_table → 组合运算
    """
    filter_config = edge['filter_config']
    conditions = filter_config['conditions']
    combine_cfg = filter_config['combine']
    
    # ========== 第一层：指标计算（查 formula_table）==========
    indicator_results = {}
    for cond in conditions:
        formula_id = cond['formula_id']
        formula_def = formula_table[formula_id]
        
        # 公式引擎批量计算所有股票的指标值（向量化）
        values = formula_engine.eval_batch(
            formula=formula_def['formula'],
            symbols=stock_codes,
            period=edge.get('period', '1d'),
            params=cond.get('formula_params', {}),
        )
        indicator_results[cond['condition_id']] = values
    
    # ========== 第二层：比较判断（查 operator_table）==========
    compare_results = {}
    for cond in conditions:
        cond_id = cond['condition_id']
        operator_id = cond['operator_id']
        op_def = operator_table[operator_id]
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
    
    # ========== 第三层：组合运算（查 combine_table）==========
    combine_id = combine_cfg['combine_id']
    combine_def = combine_table[combine_id]
    combine_func = handler_registry[combine_def['handler']]
    combine_params = combine_cfg['params']
    
    filter_result = combine_func(compare_results, stock_codes, combine_params)
    
    return filter_result
```

### 7.3 时间驱动的边处理（数据时间）

```python
def process_time_driven_edges(period_event):
    """处理时间驱动的边（周期确认事件触发，用数据时间）
    
    时间触发用数据时间（市场时间），不是系统时间
    """
    period = period_event['period']
    confirmed_time = period_event['confirmed_time']  # 数据时间
    
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
        
        # 交易日历检查（确认时间是不是在交易时段内）
        if not trade_calendar.is_trading_time(confirmed_time):
            continue
        
        # 执行这条边（用确认后的K线计算）
        process_edge_time_driven(edge, confirmed_time)
```

### 7.4 双时间使用对比

| 功能 | 用什么时间 | 代码位置 |
|------|-----------|---------|
| tick 等待间隔 | 系统时间 | `await asyncio.sleep(tick_interval)` |
| 超时检测 | 系统时间 | `check_timeout(system_time, timeout=30)` |
| TTL 过期 | 系统时间 | `process_ttl_expiry(system_time)` |
| 周期确认 | 数据时间 | `period_bar_confirmed(period)` |
| 时间驱动触发 | 数据时间 | `process_time_driven_edges(event)` |
| 指标计算 | 数据时间 | `formula_engine.eval_batch(...)` |
| 交易时间判断 | 数据时间（优先） | `trade_calendar.is_trading_time(...)` |

---

## 八、功能-表操作对应表（v1.14 更新版）

### 8.1 主循环层（轮询 + 脏驱动 + 双时间模型）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **轮询等待** | tick_interval 配置 | — | sleep(tick_interval)，系统时间 |
| **数据轮询** | 数据源接口 | `latest_tick` + `dirty_stocks` | 主动 pull 最新数据，对比变化，标记脏股票 |
| **交易时间判断** | `trade_calendar_table` | — | 先判断是不是交易日，再判断在不在交易时段 |
| **股票状态检测** | 数据 + 状态检测规则 | `stock_status_table` | 检测每只股票的状态（正常/停牌/数据不足/异常） |
| **超时检测** | 系统时间 + 最后数据时间 | — | 系统时间 - 最后数据时间 > 阈值，算超时 |
| **周期更新** | `period_table` + 最新数据 | `period_data` | 更新各周期的未完成K线，数据时间驱动 |
| **周期确认事件** | `period_data` + 数据时间 | `period_confirmed_events` | 数据时间跨过周期边界，发确认事件 |
| **脏驱动跳过** | `dirty_stocks` + `period_confirmed_events` | — | 如果没数据变化 且 没周期确认，跳过计算 |
| **节点变化计算** | `dirty_stocks` + `node_stocks` | `node_changes` | entered/exited 来自传播，updated = dirty_stocks ∩ node_stocks |
| **拓扑序处理** | `node_changes` + 拓扑序 | 各层状态表 | 按拓扑序处理脏节点，干净节点跳过 |

### 8.2 节点处理层（正交表驱动）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **节点基本信息** | `node_table` | — | 查表得到 type_id / category / allowed_roles / has_stocks 等 |
| **节点行为判断** | `node_behavior_table` | — | 查表得到 handler，不是 if-else 判断 |
| **初始化 handler** | `node_behavior_table.init_handler` | `node_stocks` | 查表调用对应的初始化函数 |
| **入边 handler** | `node_behavior_table.in_edge_handler` | `node_changes` | 查表调用对应的入边处理函数 |
| **出边 handler** | `node_behavior_table.out_edge_handler` | 各边状态表 | 查表调用对应的出边处理函数 |
| **UI 渲染（显示名/颜色/图标）** | `ui_table` + `node_table` | — | 查表得到 display_name / color / icon / size 等 |
| **脏节点判断** | `node_changes[nid]` | — | entered/exited/updated 全空就是干净的 |

### 8.3 边执行层（三层 filter + L2 组合表驱动）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **边触发模式判断** | `edge_behavior_table.trigger_mode` | — | 数据驱动还是时间驱动 |
| **时间条件检查（时间驱动）** | `period_confirmed_events` + `trade_calendar_table` | — | 周期确认事件触发 + 交易时段检查 |
| **股票状态过滤** | `stock_status_table` | — | 停牌/数据不足/异常的，按保守策略处理 |
| **第一层：指标计算** | `formula_table` + 公式引擎接口 | 公式引擎内部表 | 查表得到公式，公式引擎批量计算 |
| **第二层：比较判断** | `operator_table` + 指标值 + 股票状态 | `edge_compare_results` | 查表调算子函数。三态：True/False/None |
| **第三层：组合运算** | `combine_table` + 比较结果 | `edge_filter_results` | 查表调组合运算函数。and/or/top_n 等（三态逻辑） |
| **propagate** | `combine_table`（propagate 类） + filter 结果 | `node_stocks` + `node_changes` | 查表调传播函数。保守策略：None 的不入池 |
| **事件发射** | `node_changes[tid]` | 事件队列 | entered/exited 直接发事件 |

### 8.4 时间模型层（双时间 + 交易日历）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **交易日判断** | `trade_calendar_table` | — | 排除周末、节假日，加上额外交易日 |
| **交易时段判断** | `trade_calendar_table`（trading_sessions） | — | 当前时间在不在任一交易时段内 |
| **午间休市处理** | `trade_calendar_table`（sessions） | — | 多个时段中间就是休市 |
| **节假日处理** | `trade_calendar_table`（holidays） | — | 节假日不算交易日 |
| **多周期数据管理** | `period_table` + tick 数据 | `period_data` | 各周期的已完成K线 + 当前未完成K线 |
| **周期确认检测** | `period_data` + 数据时间 | `period_confirmed_events` | 数据时间跨过周期边界，发确认事件 |
| **时间对齐** | `period_data` + 对齐策略 | — | 最新值对齐 / 确认值对齐 / 左对齐 |
| **未完成K线处理** | `period_data.current_bar` | — | 指标默认用"已完成K线 + 当前未完成K线" |
| **确认值计算** | `period_data.completed_bars` | — | 用全部已完成K线计算，结果确定不变 |
| **超时检测** | 系统时间 + 最后数据时间 | — | 系统时间差 > 阈值，算超时 |
| **TTL 过期** | 系统时间 + 入池时间 | `ttl_expiry_queue` | 系统时间 > 过期时间，算过期 |

### 8.5 数据层 + 股票状态

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **tick 数据更新** | 数据源 | `latest_tick` + `dirty_stocks` | 轮询拉取，对比变化，标记脏股票 |
| **股票状态检测** | `latest_tick` + 历史数据 | `stock_status_table` | 检测正常/停牌/数据不足/异常 |
| **通知公式引擎** | `dirty_stocks` | 公式引擎内部失效标记 | `formula_engine.on_data_updated(dirty_codes)` |
| **指标计算（向量化）** | `formula_table` + 公式引擎接口 + `stock_status_table` | 公式引擎内部表 | 空间批量：一批股票一起算，跳过状态异常的 |

### 8.6 TTL 淘汰层

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| 股票入池记录 TTL | 边配置 | `ttl_expiry_queue` 插入 | `expire_ts = system_time + ttl_sec`（系统时间） |
| TTL 过期检查 | `ttl_expiry_queue + system_time` | 弹出过期项 | 最小堆：堆顶过期就弹出（系统时间） |
| 过期股票移除 | `node_stocks[nid]` | `node_stocks[nid]` | 从节点移除 |
| **过期触发级联** | — | `node_changes[nid].exited.add(code)` | 加入 exited 集合 |

### 8.7 实盘边界场景

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **停牌处理** | `stock_status_table` | `edge_compare_results` | 停牌的比较结果为 None，默认不入池 |
| **新股处理** | `stock_status_table` | `edge_compare_results` | 数据不足的比较结果为 None，默认不入池 |
| **数据异常处理** | `stock_status_table` | `edge_compare_results` | 异常的比较结果为 None，默认不入池 |
| **三态逻辑运算** | `edge_compare_results`（三态） + `combine_table` | `edge_filter_results` | AND/OR/NOT 的三态版本 |
| **保守策略** | `edge_filter_results` + 配置 | `node_stocks` | 不确定的不入池，宁可漏掉不错入 |
| **排名类异常过滤** | `stock_status_table` + 排名数据 | 排名结果 | 排名前先过滤掉 abnormal 的股票 |

### 8.8 后处理层

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| PK排名 | `node_stocks[target]` + `pk_config` + `stock_status_table` | `_pk_rankings` | 按权重评分排序，先过滤状态异常的 |
| 分析角度 | `node_stocks[target]` + `analysis_config` + `stock_status_table` | `_angle_results` | 多维度计算，先过滤状态异常的 |
| 看盘面板 | `node_stocks` + `dashboard_schema` + `stock_status_table` + `ui_table` | `_dashboard_data` | 组装显示数据，显示股票状态 |
| 预警检查 | `alert_rules` + `node_changes` + `stock_status_table` | `_alert_queue` | 规则匹配 + cooldown 检查，状态异常的跳过 |

---

## 九、概念变化对照表（v1.13 → v1.14）

### 9.1 L2 组合表驱动：从概念到落地

| v1.13（概念层面） | v1.14（真正落地） | 理由 |
|------------------|------------------|------|
| "部分实现"（模糊） | **filter 三层组合 = 改表不改代码** | 从概念到落地：明确三张表 + 组合逻辑 + 执行伪代码 |
| 没有 formula_table | **+ formula_table（指标公式表）** | 指标是 filter 的第一层，必须有明确的表 |
| operator + combine + propagate | **operator + combine（propagate 合并到 combine）** | propagate 也是一种组合方式，概念上统一 |
| "加新 filter 不改代码"（口号） | **加新 filter = 选指标 + 选算子 + 选组合方式（可操作）** | 从口号到可操作的步骤，真正落地 |

### 9.2 时间模型：从单时间到双时间

| v1.13（单时间概念） | v1.14（双时间模型） | 理由 |
|-------------------|-------------------|------|
| 只有"时间"一个模糊概念 | **数据时间 vs 系统时间，两个完全不同的概念** | 澄清核心混淆，避免诡异 bug |
| 时间触发用哪个时间？不清楚 | **时间触发用数据时间（市场时间）** | 策略基于市场时间，不是服务器时间 |
| 周期确认用哪个时间？不清楚 | **周期确认用数据时间** | K线基于市场时间，确认看数据时间边界 |
| 超时时用哪个时间？不清楚 | **超时/心跳/TTL 用系统时间** | 超时是"我们多久没收到数据"，用系统时间 |
| 没有乱序处理概念 | **水位线 + 延迟窗口（简化版）** | 数据时间可能乱序，需要处理策略 |

### 9.3 交易日历：从简单时段到完整基础设施

| v1.13（简单时段配置） | v1.14（完整交易日历） | 理由 |
|---------------------|---------------------|------|
| 只有 market_calendar.sessions | **完整 trade_calendar_table** | 时段只是交易日历的一部分 |
| 没有交易日判断 | **is_trading_day（周末/节假日/调休）** | 首先要知道今天是不是交易日 |
| 没有节假日处理 | **holidays 列表** | 节假日不算交易日 |
| 没有周末处理 | **周末排除 + 额外交易日** | 周末排除，调休的周末算交易日 |
| 午间休市只有时段划分 | **完整的交易时段 + 半天交易日** | 半天交易日特殊处理 |
| 没有 next_trading_day 等接口 | **完整的交易日历接口** | 所有时间计算的基础设施 |

### 9.4 正交边界：从模糊到彻底澄清

| v1.13（边界有模糊） | v1.14（彻底澄清） | 理由 |
|-------------------|------------------|------|
| trigger_mode 在哪？没说清 | **edge_behavior_table（边行为）** | 触发模式是边的行为的一部分 |
| name 在哪？node_table | **分两个：唯一标识归 node_table，显示名归 ui_table** | 两者性质不同，不能混 |
| position 在哪？没说清 | **ui_table（实例级）** | 位置是 UI 布局，和行为无关 |
| 每张表管什么？大致知道 | **彻底澄清：一张表只管一个维度** | 正交原则：改一个维度不影响其他 |

### 9.5 表数量变化

| 版本 | 配置表数 | 核心运行时表数 | 变化 |
|------|---------|---------------|------|
| v1.12 | 4 张 | 7 张 | — |
| v1.13 | 9 张 | 8 张 | node_type_table 拆为 4 张 + 新增 2 张时间配置表 + 1 张股票状态表 |
| **v1.14** | **11 张** | **8 张** | **+ formula_table（L2 落地） + trade_calendar_table（交易日历）** |

---

## 十、实现路线图（v1.14）

### 阶段一：L2 组合表驱动落地（P0）

1. **创建 formula_table（指标公式表）**
   - 整理常用技术指标（MA、MACD、KDJ、RSI、BOLL 等）
   - 每个指标对应一个 TDX 风格公式
   - 参数定义、最小K线数、分类

2. **完善 operator_table（比较算子表）**
   - 整理常用比较算子（gt/lt/gte/lte/eq/between/cross_above/cross_below）
   - 每个算子对应一个 handler 函数
   - 三态逻辑支持（None 输入的处理）

3. **完善 combine_table（组合运算表）**
   - 整理常用组合方式（and/or/not/top_n/bottom_n/top_pct）
   - 每个组合方式对应一个 handler 函数
   - 三态逻辑支持
   - propagate 概念合并到 combine

4. **实现 filter 三层组合执行器**
   - 第一层：查 formula_table，公式引擎批量计算
   - 第二层：查 operator_table，逐个条件比较
   - 第三层：查 combine_table，多条件组合
   - 三态逻辑贯穿始终
   - 向量化计算，性能优先

### 阶段二：双时间模型澄清 + 交易日历（P0）

1. **澄清数据时间 vs 系统时间**
   - 代码中明确区分两种时间
   - 每个时间相关的变量，明确是数据时间还是系统时间
   - 命名规范：data_time / system_time

2. **创建 trade_calendar_table（交易日历表）**
   - 交易时段配置（上午/下午）
   - 节假日列表
   - 额外交易日（调休）
   - 半天交易日

3. **实现 TradeCalendar 类**
   - is_trading_day（交易日判断）
   - is_trading_time（交易时段判断）
   - next_trading_day / prev_trading_day
   - trading_days_between
   - get_open_time / get_close_time

4. **更新时间相关逻辑**
   - 时间触发用数据时间
   - 周期确认用数据时间
   - 超时/心跳/TTL 用系统时间
   - 所有时间判断先过交易日历

### 阶段三：正交边界澄清（P0）

1. **name 拆分：唯一标识 vs 显示名**
   - node_table 保留 type_id（唯一标识）
   - ui_table 增加 display_name（显示名）
   - 所有显示名称的地方，从 ui_table 取

2. **position 归 UI**
   - 节点实例的 position 明确归 UI 维度
   - 不影响行为和逻辑

3. **trigger_mode 归 edge_behavior_table**
   - 明确 trigger_mode 是边的行为
   - 从其他表中移除

4. **全面审查所有表的边界**
   - 每个属性归哪张表，清清楚楚
   - 确保正交：改一个维度不影响其他

### 阶段四：测试验证（P0）

1. **L2 组合验证**
   - 加新 filter 确实不用写代码
   - 三层组合逻辑正确
   - 三态逻辑正确
   - 性能达标（向量化）

2. **双时间模型验证**
   - 数据时间和系统时间正确区分
   - 时间触发用数据时间
   - 超时时用系统时间
   - 行情延迟场景下行为正确

3. **交易日历验证**
   - 交易日判断正确（周末/节假日/调休）
   - 交易时段判断正确（午间休市）
   - 半天交易日处理正确
   - next/prev_trading_day 正确

4. **正交边界验证**
   - 改 UI 不影响行为
   - 改行为不影响 UI
   - 改基本属性不影响行为和 UI
   - 行为复用正确

---

## 十一、统计总结（v1.13 → v1.14）

### 11.1 概念数量变化

| 统计项 | v1.13 | v1.14 | 变化 |
|--------|------|-------|------|
| 配置表数 | 9 张 | **11 张** | **+2（formula_table + trade_calendar_table）** |
| 核心运行时表 | 8 张 | 8 张 | 不变 |
| 表驱动层级 | L1 + 部分 L2 | **L1 + 真正的 L2 组合** | L2 真正落地：filter 三层组合 = 改表不改代码 |
| 时间模型 | 单时间（模糊） | **双时间（数据时间 vs 系统时间）** | 澄清核心混淆 |
| 交易日历 | 简单时段配置 | **完整交易日历基础设施** | 从时段到完整日历 |
| 正交边界 | 大致拆分 | **彻底澄清** | 每张表管什么，清清楚楚 |
| 边触发模式 | 2 种（数据/时间） | 2 种（数据/时间） | 不变，但时间触发明确用数据时间 |

### 11.2 为什么是 v1.14？

**v1.14 是"落地 + 澄清 + 基础 + 正交"的版本：**

1. **落地**：L2 组合表驱动真正落地——filter 三层组合 = 改表不改代码
2. **澄清**：双时间模型——数据时间 vs 系统时间，不再混淆
3. **基础**：交易日历——时间模型的基础设施，所有时间计算的依赖
4. **正交**：边界彻底澄清——每张表管什么，不管什么，清清楚楚

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
  v2.0：完整稳定版（所有功能完善，文档齐全）
```

**v1.14 解决的四个核心问题：**
1. **落地问题**：L2 组合表驱动从概念到落地——filter 三层组合，真正的改表不改代码
2. **混淆问题**：双时间模型澄清——数据时间 vs 系统时间，各用各的地方
3. **基础问题**：交易日历基础设施——所有时间计算的依赖，不再是简单时段
4. **边界问题**：正交边界彻底澄清——每张表的职责清清楚楚，不交叉不重叠

落地比概念重要，澄清比混淆重要，基础比上层重要，正交比混杂重要。

### 11.3 一句话总结

**v1.14：L2 组合表驱动真正落地（filter 三层组合 = 改表不改代码）+ 双时间模型澄清（数据时间 vs 系统时间）+ 交易日历基础设施（所有时间计算的依赖）+ 正交边界彻底澄清（每张表的职责清清楚楚）。**
