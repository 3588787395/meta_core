# 股票池深度重构规划 v1.17

> 版本主题：纠正根本性错误——时间触发用系统时间，周期确认用数据时间
> 设计原则：诚实不吹牛、正交拆分、三态逻辑、保守策略、错误隔离
> 目标：彻底纠正"时间触发用数据时间"的根本性错误，重新整理双时间模型的使用场景，明确时间触发的三种模式

---

## v1.16 → v1.17 变更摘要表

**变更日期：** 2026-07-01

| # | 变更项 | v1.16 | v1.17 | 本质变化 |
|---|--------|------|-------|---------|
| 1 | **根本性纠错：时间触发的时间基准** | "时间触发用数据时间"（大错特错） | **时间触发（定时）用系统时间，周期确认用数据时间** | 纠正认知错误：定时任务按真实时间来，K线确认按数据时间来 |
| 2 | **双时间模型使用场景表** | 场景与时间基准对应关系混乱 | **完整的场景-时间基准对照表，每个场景都有明确的理由** | 从"凭感觉"到"有清晰判断标准"：什么时候用系统时间，什么时候用数据时间，一目了然 |
| 3 | **时间触发的三种模式** | 只有"时间驱动"一种模糊概念 | **绝对时间触发 / 相对时间触发 / 周期确认触发，三种模式泾渭分明** | 从"一种时间触发"到"三种触发模式"：定时触发≠周期确认，完全是两回事 |
| 4 | **核心循环伪代码更新** | 时间触发混在周期确认里 | **定时触发（系统时间）和周期确认（数据时间）分开处理** | 核心循环中明确区分两种时间驱动：定时任务到点就跑，周期确认等数据 |
| 5 | **功能-表操作对应表更新** | 时间触发和周期确认混在一起 | **定时触发和周期确认分开，各自读什么表、写什么表清清楚楚** | 功能表中两种时间驱动各归其位，不再混淆 |
| 6 | **表Schema时间字段说明更新** | 时间字段用途说明模糊 | **所有时间相关字段都明确标注是系统时间还是数据时间** | 每张表的每个时间字段，一看就知道是什么时间，不会用错 |
| 7 | **简单记忆法** | 没有 | **"什么时候做"=系统时间，"数据什么时候"=数据时间** | 一句话记住，再也不会搞混 |

**一句话总结 v1.17 升级：** 纠错——把搞反的时间基准纠正过来；澄清——三种时间触发模式泾渭分明；明确——每个场景用什么时间、为什么，清清楚楚。

---

## 一、根本性错误纠正：时间触发用系统时间

### 1.1 错误是什么

**之前的错误认知：** "时间触发用数据时间"

**为什么是错的：** 完全搞反了。定时任务就是按真实时间来的，跟数据没关系。

---

### 1.2 正确的认知

#### 1.2.1 时间触发（定时任务）：用系统时间

**定义：** 按系统时间（真实时间）触发的任务。

**例子：**
- "每天9:30触发" → 系统时间到了9:30就触发
- "每隔5分钟触发一次" → 系统时间每隔5分钟触发
- "开盘后30分钟触发" → 系统时间到了开盘+30分钟就触发

**为什么用系统时间？**
- 定时任务就是"什么时候做这件事"，跟数据没关系
- 数据可能延迟，但定时任务到点就执行（哪怕数据还没到）
- 定时任务的本质是闹钟，闹钟看的是墙上的钟，不是数据的时间

---

#### 1.2.2 周期确认事件：用数据时间

**定义：** 行情数据的时间跨过了周期边界，确认一根K线完成。

**例子：**
- 1分钟K线确认 → 数据时间跨过了分钟边界（比如从 9:30:59 到 9:31:00）
- 日线确认 → 数据时间跨过了日线边界（收盘）
- 5分钟K线确认 → 数据时间跨过了5分钟边界

**为什么用数据时间？**
- K线确认是看行情数据本身的时间，不是看系统时间
- 数据可能延迟，但K线的时间戳是市场时间
- 周期确认的本质是"数据走到哪了"，不是"现在几点了"

---

#### 1.2.3 指标计算的时间基准：用数据时间

**定义：** 技术指标基于行情数据计算，时间基准自然是数据时间。

**注意：** 触发计算的时机，可以是：
- 数据更新时触发（数据驱动）
- 定时触发（系统时间驱动，比如每5分钟重算一次）

但**指标值本身**的时间基准是数据时间。

---

#### 1.2.4 TTL（持仓时间、过期时间）：用系统时间

**定义：** 持仓时间、冷却时间、过期时间等"等了多久"的概念。

**例子：**
- "入池后持有5分钟" → 5分钟是系统时间
- "预警30秒内不重复" → 30秒是系统时间
- "缓存5分钟过期" → 5分钟是系统时间

**为什么用系统时间？**
- TTL是"我们的系统等了多久"，不是"市场过了多久"
- 哪怕市场停牌了，持仓时间照样算
- TTL的本质是"我们的耐心有多久"，跟市场没关系

---

### 1.3 简单记忆法

| 判断问题 | 答案 | 用什么时间 |
|---------|------|-----------|
| 和"**什么时候做这件事**"相关？ | 是 | **系统时间** |
| 和"**数据本身是什么时候的**"相关？ | 是 | **数据时间** |

**举例：**
- 定时触发 = 什么时候做 = 系统时间 ✅
- K线确认 = 数据什么时候 = 数据时间 ✅
- TTL过期 = 等了多久 = 系统时间 ✅
- 指标值 = 数据什么时候的值 = 数据时间 ✅

---

### 1.4 代码验证：现有实现是对的

**验证文件：** `meta_core/core/engine.py`

**验证结果：**

| 功能 | 代码实现 | 用什么时间 | 正确吗？ |
|------|---------|-----------|---------|
| 定时触发（starttype） | `_eval_timing_primitive` 中 `cur_ts = _safe_timestamp(self._now())` | 系统时间 | ✅ 正确 |
| 交易时段判断 | `_now_seconds_today()` 调用 `self._now()` | 系统时间 | ✅ 正确 |
| TTL计算 | LRUCache中用 `time.time()` | 系统时间 | ✅ 正确 |
| 周期确认 | `period_confirmed_events` 基于数据时间 | 数据时间 | ✅ 正确 |

**结论：** 代码实现是对的，之前文档写错了。v1.17 纠正文档。

---

## 二、双时间模型使用场景表（完整版）

### 2.1 系统时间 vs 数据时间 总览

```
┌─────────────────────────────────────────────────────────────────┐
│                        双时间模型（正确版）                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌───────────────────────────┐  ┌───────────────────────────┐  │
│  │      系统时间（sys）       │  │      数据时间（data）      │  │
│  │  = 真实时间 = 墙上的钟     │  │  = 市场时间 = 行情时间戳   │  │
│  │                           │  │                           │  │
│  │  用途：什么时候做这件事    │  │  用途：数据本身是什么时候  │  │
│  │  特点：全局唯一、单调递增  │  │  特点：股票级粒度、可能延迟│  │
│  └───────────────────────────┘  └───────────────────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

### 2.2 完整场景对照表

| # | 场景 | 用什么时间 | 为什么 | 反例（为什么不用另一个） |
|---|------|-----------|--------|------------------------|
| 1 | **每天9:30触发策略** | 系统时间 | 定时任务，到点就跑 | 不用数据时间：数据9:35才到，难道9:30的任务就不跑了？ |
| 2 | **每隔5分钟触发一次** | 系统时间 | 定时器，按真实时间间隔 | 不用数据时间：数据断断续续，5分钟间隔就不准了 |
| 3 | **开盘后30分钟触发** | 系统时间 | 定时任务，基于交易日历的偏移 | 不用数据时间：数据晚到，开盘后30分钟的任务还是要在9:60触发 |
| 4 | **1分钟K线确认** | 数据时间 | K线是行情数据，看数据本身的时间 | 不用系统时间：系统时间9:31了，但数据还停在9:30:59，不能确认 |
| 5 | **日线确认（收盘）** | 数据时间 | 日线是行情数据，看数据的时间 | 不用系统时间：系统时间15:00了，但数据还没收盘，不能确认 |
| 6 | **指标计算** | 数据时间（值的基准） | 指标基于行情数据算，时间序列是数据时间 | 触发时机可以是系统时间（定时重算），但值本身用数据时间 |
| 7 | **TTL持仓过期** | 系统时间 | "持有5分钟"是我们等了5分钟，不是市场过了5分钟 | 不用数据时间：停牌一小时，持仓时间照样算 |
| 8 | **预警冷却时间** | 系统时间 | "30秒内不重复预警"是我们的冷却，不是市场的 | 不用数据时间：没数据的时候，冷却时间照样走 |
| 9 | **缓存过期** | 系统时间 | 缓存5分钟过期是我们的缓存策略 | 不用数据时间：没数据的时候，缓存照样过期 |
| 10 | **超时检测（数据源）** | 系统时间 | "30秒没收到数据"是我们等了多久 | 不用数据时间：数据时间一直不变，怎么算超时？ |
| 11 | **日志时间戳** | 系统时间 | 记录"我们什么时候处理的" | 不用数据时间：日志是我们的记录，看我们的时间 |
| 12 | **性能统计** | 系统时间 | 计算处理耗时，用我们的时间 | 不用数据时间：性能是我们的性能，跟数据时间没关系 |
| 13 | **回测/回放** | 数据时间 | 模拟历史，用历史数据的时间推进 | 不用系统时间：回测要快进，不能等真实时间 |
| 14 | **交易时间判断** | 系统时间（实盘）/ 数据时间（回测） | 实盘看现在是不是交易时间，回测看数据时间 | 实盘用系统时间，因为"现在"是真实的现在 |
| 15 | **入池时间戳** | 系统时间 | 记录"什么时候入池的"，是我们的时间 | 不用数据时间：入池是我们系统的动作，看我们的时间 |

---

### 2.3 三种时间触发模式

之前把"时间触发"当成一个东西，这是错误的。实际上有三种完全不同的模式：

| 模式 | 触发源 | 时间基准 | 本质 | 例子 |
|------|--------|---------|------|------|
| **绝对时间触发** | 交易日历 + 系统时间 | 系统时间 | "每天几点几分触发" | 每天9:30触发、每天收盘前10分钟触发 |
| **相对时间触发** | 系统时间计时器 | 系统时间 | "每隔多久触发一次" | 每隔5分钟触发一次、入池后30分钟触发 |
| **周期确认触发** | 数据时间跨过周期边界 | 数据时间 | "每根K线确认后触发" | 每根1分钟K线确认后触发、每根日线确认后触发 |

---

#### 2.3.1 绝对时间触发

**定义：** 在指定的绝对时间点触发，基于交易日历 + 系统时间。

**配置示例：**
- 触发类型：`absolute_time`
- 时间：`09:30:00`（每天的9:30）
- 偏移：`-300`（开盘前5分钟）

**判断逻辑：**
```python
def check_absolute_time_trigger(sys_ts, calendar, target_hms, offset_sec=0):
    """绝对时间触发检查（用系统时间）"""
    target_ts = get_today_target_ts(sys_ts, target_hms) + offset_sec
    return sys_ts >= target_ts and sys_ts < target_ts + 60  # 1分钟窗口内
```

**特点：**
- 每天固定时间点触发
- 需要交易日历（只在交易日触发）
- 用系统时间，跟数据没关系
- 数据没到也触发（可能拿到的是旧数据）

---

#### 2.3.2 相对时间触发

**定义：** 每隔固定时间间隔触发，或从某个起点开始经过一段时间后触发。

**配置示例：**
- 触发类型：`relative_time`
- 间隔：`300`秒（5分钟）
- 起点：`pool_start`（股票池启动时）

**判断逻辑：**
```python
def check_relative_time_trigger(sys_ts, start_ts, interval_sec):
    """相对时间触发检查（用系统时间）"""
    if interval_sec <= 0:
        return True  # 总是触发
    elapsed = sys_ts - start_ts
    return elapsed >= interval_sec
```

**特点：**
- 按固定间隔触发
- 用系统时间，跟数据没关系
- 可以从某个事件开始计时（如入池时间、启动时间）

---

#### 2.3.3 周期确认触发

**定义：** 当行情数据的时间跨过周期边界，确认一根K线完成时触发。

**配置示例：**
- 触发类型：`period_confirm`
- 周期：`1m`（1分钟K线）

**判断逻辑：**
```python
def check_period_confirm(bar_data_ts, period, last_confirm_ts):
    """周期确认触发检查（用数据时间）"""
    current_period_start = align_to_period(bar_data_ts, period)
    last_period_start = align_to_period(last_confirm_ts, period)
    return current_period_start > last_period_start
```

**特点：**
- 基于数据时间，不是系统时间
- 数据没更新就不触发
- 每只股票独立确认（数据时间是股票级的）
- 确认的是"数据走到哪了"，不是"现在几点了"

---

### 2.4 三种模式的对比

| 维度 | 绝对时间触发 | 相对时间触发 | 周期确认触发 |
|------|------------|------------|-------------|
| **时间基准** | 系统时间 | 系统时间 | 数据时间 |
| **触发源** | 时钟闹钟 | 计时器/倒计时 | K线数据更新 |
| **本质** | "几点了" | "过了多久" | "数据更新了吗" |
| **跟数据的关系** | 没关系，到点就跑 | 没关系，到点就跑 | 强相关，数据更新才触发 |
| **粒度** | 全局（所有股票同时） | 全局/个股都可以 | 股票级（每只独立） |
| **典型用途** | 每日开盘策略、收盘策略 | 定期巡检、定时重算 | K线级策略、指标更新 |
| **数据延迟时** | 照样触发（可能用旧数据） | 照样触发（可能用旧数据） | 不触发（等数据到了再说） |
| **停牌时** | 照样触发 | 照样触发 | 不触发（没数据） |

---

## 三、核心表字段定义（更新时间字段说明）

### 3.1 设计原则

所有表的Schema设计遵循以下原则（继承v1.16）：
1. **主键唯一**：每张表有明确的主键，全局唯一
2. **类型明确**：每个字段有明确的数据类型
3. **约束清晰**：必填/可选、默认值、取值范围、外键引用
4. **三态兼容**：支持 True/False/None 三态逻辑
5. **可追溯**：有创建时间、更新时间、版本号
6. **可扩展**：预留 ext 字段用于扩展

**v1.17新增原则：**
7. **时间明确**：所有时间字段都明确标注是系统时间还是数据时间

---

### 3.2 配置表：类型定义层（7张）

（继承v1.16，此处省略，无变化）

---

### 3.3 配置表：实例定义层（5张）

（继承v1.16，此处省略，无变化）

---

### 3.4 配置表：系统配置层（3张）

#### 3.4.1 period_table（周期定义表）

**更新说明：** 明确 `confirm_mode` 是基于数据时间的。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `period_id` | string | **主键**，必填，唯一 | 周期唯一标识 |
| `name` | string | 必填 | 周期名称（显示用） |
| `category` | string | 必填，枚举 | 分类：tick/intraday/daily/weekly/monthly |
| `seconds` | integer | 必填 | 周期长度（秒）。日K线及以上为交易日数量 |
| `is_tick` | bool | 必填，默认 false | 是否是tick级周期 |
| `confirm_mode` | string | 必填，默认 "time_boundary" | 确认方式：`time_boundary`（时间边界，**基于数据时间**）/ `next_bar`（下一根K线出现，**基于数据时间**）/ `volume`（成交量确认） |
| `align_to` | string | 可选，默认 null | 对齐方式，如 "market_open"（开盘对齐） |
| `data_source_period` | string | 可选，默认 null | 数据源中的周期标识 |
| `description` | string | 可选，默认 "" | 周期描述 |
| `created_at` | timestamp | 必填 | 创建时间（**系统时间**） |
| `updated_at` | timestamp | 必填 | 更新时间（**系统时间**） |
| `version` | string | 必填，默认 "1.0" | 版本号 |
| `ext` | object | 可选，默认 {} | 扩展字段 |

**重要说明：** 周期确认的 `confirm_mode` 全部基于**数据时间**，不是系统时间。

---

#### 3.4.2 trade_calendar_table（交易日历表）

（继承v1.16，无本质变化）

---

#### 3.4.3 system_config_table（系统配置表）

**更新说明：** 明确时间相关配置的时间基准。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `config_key` | string | **主键**，必填，唯一 | 配置项键名 |
| `config_value` | any | 必填 | 配置项值 |
| `value_type` | string | 必填，枚举 | 值类型：string/integer/float/bool/object/array |
| `category` | string | 必填，默认 "general" | 配置分类 |
| `description` | string | 可选，默认 "" | 配置描述 |
| `default_value` | any | 可选，默认 null | 默认值 |
| `is_readonly` | bool | 必填，默认 false | 是否只读 |
| `validation` | object | 可选，默认 null | 校验规则 |
| `created_at` | timestamp | 必填 | 创建时间（**系统时间**） |
| `updated_at` | timestamp | 必填 | 更新时间（**系统时间**） |
| `updated_by` | string | 可选，默认 null | 最后修改者 |
| `version` | string | 必填，默认 "1.0" | 版本号 |
| `ext` | object | 可选，默认 {} | 扩展字段 |

**常用时间相关配置项：**

| config_key | value_type | 默认值 | 时间基准 | 说明 |
|------------|-----------|--------|---------|------|
| `tick_interval_sec` | float | 1.0 | **系统时间** | tick轮询间隔（秒） |
| `data_timeout_sec` | integer | 30 | **系统时间** | 数据超时时间（秒），系统时间多久没收到数据算超时 |
| `max_calc_time_ms` | integer | 1000 | **系统时间** | 单次计算最大耗时（毫秒） |

---

### 3.5 运行时表（时间字段说明更新）

#### 3.5.1 latest_tick（最新Tick数据表）

**更新说明：** 明确 `timestamp` 是数据时间。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `code` | string | **主键**，唯一 | 股票代码 |
| `price` | float | 必填 | 最新价 |
| `open` | float | 可选 | 今日开盘价 |
| `high` | float | 可选 | 今日最高价 |
| `low` | float | 可选 | 今日最低价 |
| `volume` | integer | 可选 | 今日成交量（股） |
| `amount` | float | 可选 | 今日成交额（元） |
| `prev_close` | float | 可选 | 昨收价 |
| `timestamp` | timestamp | 必填 | 数据时间戳（**数据时间**，tick_data_ts） |
| `data_source` | string | 可选 | 数据源标识 |
| `raw_data` | object | 可选 | 原始数据（保留所有字段） |

**类型：** `Dict[code → tick_dict]`

---

#### 3.5.2 stock_status_table（股票状态表）

**更新说明：** 明确各时间字段的时间基准。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `code` | string | **主键**，唯一 | 股票代码 |
| `status` | string | 必填，枚举 | 状态：normal/suspended/insufficient_data/error |
| `status_reason` | string | 可选，默认 null | 状态原因描述 |
| `last_data_ts` | timestamp | 可选，默认 null | 最后一次收到数据的时间戳（**数据时间**） |
| `last_error` | string | 可选，默认 null | 最后一次错误信息 |
| `last_error_ts` | timestamp | 可选，默认 null | 最后一次错误时间（**系统时间**） |
| `error_count` | integer | 必填，默认 0 | 连续错误次数 |
| `suspended_reason` | string | 可选，默认 null | 停牌原因 |
| `data_lag_sec` | float | 可选，默认 null | 数据延迟（秒），**系统时间 - 数据时间** |
| `updated_at` | timestamp | 必填 | 更新时间（**系统时间**） |

---

#### 3.5.3 node_stocks（节点股票列表）

**更新说明：** 明确 `in_pool_ts` 是系统时间。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `node_id` | string | **主键**，唯一 | 节点ID |
| `stocks` | array[object] | 必填，默认 [] | 股票列表 |

**stock 元素结构：**
```json
{
  "code": "000001.SZ",
  "in_pool_ts": 1751356800.0,
  "source_edge_id": "edge_001",
  "entry_price": 12.34
}
```

**字段说明：**
- `in_pool_ts`：入池时间戳（**系统时间**）——记录"我们什么时候让它入池的"

---

#### 3.5.4 ttl_expiry_queue（TTL过期队列）

**更新说明：** 明确所有时间都是系统时间。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `expire_ts` | float | **主键**（堆排序键） | 过期时间戳（**系统时间**，Unix时间戳秒） |
| `node_id` | string | 必填 | 节点ID |
| `code` | string | 必填 | 股票代码 |
| `edge_id` | string | 必填 | 来源边ID（哪个边设置的TTL） |
| `entry_ts` | float | 必填 | 入池时间戳（**系统时间**） |
| `ttl_sec` | float | 必填 | TTL时长（秒，**系统时间**） |

**重要：** TTL全部用**系统时间**，因为TTL是"我们等了多久"。

---

#### 3.5.5 period_data（周期K线数据表）

**更新说明：** 明确K线的时间戳是数据时间。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `period` | string | **主键**，唯一 | 周期ID |
| `completed_bars` | Dict[code → List[bar_dict]] | 必填 | 每只股票的已完成K线列表（按时间倒序） |
| `current_bar` | Dict[code → bar_dict] | 必填 | 每只股票的当前未完成K线 |

**bar_dict 结构：**
```json
{
  "timestamp": 1751356800.0,
  "open": 12.34,
  "high": 12.50,
  "low": 12.20,
  "close": 12.45,
  "volume": 1000000,
  "amount": 12345000.0
}
```

**字段说明：**
- `timestamp`：K线时间戳（**数据时间**）——这根K线的开始时间

---

#### 3.5.6 period_confirmed_events（周期确认事件队列）

**更新说明：** 明确是数据时间驱动。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `period` | string | 必填 | 周期ID |
| `confirmed_stocks` | Set[string] | 必填 | 哪些股票的该周期确认了 |
| `confirmed_time` | timestamp | 必填 | 确认时间（**数据时间**）——刚确认的K线的时间戳 |

**重要：** 周期确认用**数据时间**，因为K线确认是看数据本身的时间。

---

#### 3.5.7 error_log_table（错误日志表）

**更新说明：** 明确发生时间是系统时间。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `error_id` | string | **主键**，唯一 | 错误唯一标识（UUID） |
| `error_type` | string | 必填，枚举 | 错误类型 |
| `severity` | string | 必填，枚举 | 严重程度：info/warning/error/critical |
| `message` | string | 必填 | 错误消息 |
| `stack_trace` | string | 可选，默认 null | 堆栈信息 |
| `context` | object | 可选，默认 {} | 上下文信息 |
| `occurred_at` | timestamp | 必填 | 发生时间（**系统时间**） |
| `recoverable` | bool | 必填，默认 true | 是否可恢复 |
| `recovered` | bool | 必填，默认 false | 是否已恢复 |
| `recovered_at` | timestamp | 可选，默认 null | 恢复时间（**系统时间**） |
| `retry_count` | integer | 必填，默认 0 | 重试次数 |

---

### 3.6 配置表汇总（15张）

（继承v1.16，无变化）

### 3.7 运行时表汇总（12张）

（继承v1.16，无变化）

---

## 四、核心循环伪代码（v1.17 更新版）

### 4.1 主循环：轮询 + 脏驱动 + 双时间模型 + 三种触发模式

```python
# ============================================================
#  v1.17 核心循环伪代码（纠正版：时间触发用系统时间，周期确认用数据时间）
# ============================================================

# --- 初始化 ---
# 加载基础设施层
trade_calendar = load_trade_calendar('trade_calendar_table')
data_provider = DataProvider()
formula_engine = FormulaEngine()

# 加载类型配置
node_type_table = load_table('node_type_table')
node_behavior_table = load_table('node_behavior_table')
edge_type_table = load_table('edge_type_table')
edge_behavior_table = load_table('edge_behavior_table')
operator_table = load_table('operator_table')
formula_table = load_table('formula_table')
period_table = load_table('period_table')

# 注册 handler
handler_registry = register_all_handlers()

# 加载实例配置
pool_instance = load_pool_instance('pool_1')
node_instances = load_node_instances('pool_1')
edge_instances = load_edge_instances('pool_1')

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
# 数据时间（股票级粒度）
tick_data_ts = {}    # Dict[code → ts]，数据时间
bar_data_ts = {}     # Dict[code → Dict[period → ts]]，数据时间
# 系统时间（全局）
last_poll_ts = 0     # 上次轮询时间（系统时间）
last_calc_ts = 0     # 上次计算时间（系统时间）

# 定时触发调度器（系统时间驱动）
scheduler = init_scheduler(edge_instances, edge_behavior_table)
# scheduler 管理所有定时触发的边（绝对时间触发、相对时间触发）

# 编译期：拓扑排序
topo_order = build_topo_order(node_instances, edge_instances)

# --- 主循环（轮询模型）---
tick_interval = 1.0  # 秒，系统时间间隔

while running:
    # ============================================================
    # 第1步：等待下一个 tick（系统时间）
    # ============================================================
    await asyncio.sleep(tick_interval)
    sys_ts = time.time()  # 当前系统时间（处理时间）
    
    if paused:
        continue
    
    # ============================================================
    # 第2步：轮询获取最新数据
    # ============================================================
    tick_data = data_provider.poll_latest_data()
    last_poll_ts = sys_ts  # 更新上次轮询时间（系统时间）
    
    # ============================================================
    # 第3步：判断是不是交易时间（用系统时间，实盘模式）
    # ============================================================
    # 实盘用系统时间判断"现在是不是交易时间"
    is_trading = trade_calendar.is_trading_time(datetime.fromtimestamp(sys_ts))
    
    # ============================================================
    # 第4步：更新数据层 + 标记脏股票 + 更新数据时间戳
    # ============================================================
    dirty_stocks.clear()
    for code, new_bar in tick_data.items():
        # 更新 tick 数据时间（股票级，数据时间）
        new_ts = get_data_time(new_bar)  # 从tick数据里提取时间戳
        if tick_data_ts.get(code) != new_ts:
            tick_data_ts[code] = new_ts
        
        # 更新 latest_tick（唯一真相源）
        if latest_tick.get(code) != new_bar:
            latest_tick[code] = new_bar
            dirty_stocks.add(code)
        
        # 检测并更新股票状态
        old_status = stock_status_table.get(code, {}).get('status', 'normal')
        new_status = detect_stock_status(code, new_bar)
        if old_status != new_status:
            update_stock_status(code, new_status)
            dirty_stocks.add(code)
    
    # ============================================================
    # 第5步：更新 bar 数据 + 检测周期确认事件（数据时间驱动）
    # ============================================================
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
                # 发周期确认事件（数据时间驱动）
                period_confirmed_events.put({
                    'period': period,
                    'confirmed_stocks': confirmed_stocks,
                    'confirmed_time': get_confirmed_bar_time(period),  # 数据时间
                })
                # 开始新的未完成K线
                start_new_current_bar(period, confirmed_stocks)
    
    # ============================================================
    # 第6步：超时检测（用系统时间）
    # ============================================================
    # 如果 30 秒没轮询到数据，标记为断线
    check_timeout(sys_ts, last_poll_ts, timeout=30)
    
    # ============================================================
    # 第7步：定时触发检查（系统时间驱动）
    # ============================================================
    # 检查有没有定时任务到点了（绝对时间触发 + 相对时间触发）
    timed_trigger_edges = scheduler.check_due(sys_ts, is_trading)
    # timed_trigger_edges: List[edge_id]  哪些定时边到点了
    
    # ============================================================
    # 第8步：如果没有任何触发，跳过计算（脏驱动）
    # ============================================================
    has_data_trigger = len(dirty_stocks) > 0
    has_period_confirm = not period_confirmed_events.empty()
    has_timed_trigger = len(timed_trigger_edges) > 0
    
    if not has_data_trigger and not has_period_confirm and not has_timed_trigger:
        last_calc_ts = sys_ts
        continue
    
    # ============================================================
    # 第9步：计算每个节点的变化（node_changes）
    # ============================================================
    compute_all_node_changes(dirty_stocks, node_stocks, node_changes)
    
    # ============================================================
    # 第10步：按拓扑序处理脏节点（数据驱动的边）
    # ============================================================
    for nid in topo_order:
        if not is_node_dirty(nid, node_changes):
            continue
        
        # 查表调用 handler
        node_inst = node_instances[nid]
        type_id = node_inst['type_id']
        type_behavior = node_behavior_table[type_id]
        handler_name = type_behavior['out_edge_handler']
        
        if handler_name:
            handler = handler_registry[handler_name]
            merged_params = merge_params(type_behavior.get('default_params', {}), node_inst.get('params', {}))
            handler(nid, node_inst, merged_params, node_changes[nid])
    
    # ============================================================
    # 第11步：处理周期确认触发的边（数据时间驱动）
    # ============================================================
    while not period_confirmed_events.empty():
        event = period_confirmed_events.get()
        # 找出所有周期确认触发的边，执行它们
        process_period_confirm_edges(event, edge_instances, edge_behavior_table)
    
    # ============================================================
    # 第12步：处理定时触发的边（系统时间驱动）
    # ============================================================
    for eid in timed_trigger_edges:
        # 执行定时触发的边
        process_timed_edge(eid, edge_instances, edge_behavior_table, sys_ts)
        # 标记下一次触发时间
        scheduler.mark_fired(eid, sys_ts)
    
    # ============================================================
    # 第13步：后处理（PK 排名 / 分析角度 / 预警）
    # ============================================================
    post_process(node_stocks, node_changes, stock_status_table)
    
    # ============================================================
    # 第14步：TTL 过期检查（用系统时间）
    # ============================================================
    process_ttl_expiry(sys_ts, ttl_expiry_queue, node_stocks, node_changes)
    
    # ============================================================
    # 第15步：更新上次计算时间（系统时间）
    # ============================================================
    last_calc_ts = time.time()
    
    # ============================================================
    # 第16步：清脏，为下一轮做准备
    # ============================================================
    clear_all_dirty(dirty_stocks, node_changes)
```

---

### 4.2 定时触发调度器（系统时间驱动）

```python
class TimedScheduler:
    """定时触发调度器（基于系统时间）
    
    管理两种定时触发模式：
    1. 绝对时间触发：每天几点几分触发
    2. 相对时间触发：每隔多久触发一次
    """
    
    def __init__(self, edge_instances, edge_behavior_table):
        self.absolute_timers = {}  # eid → { target_hms, offset_sec }
        self.relative_timers = {}  # eid → { interval_sec, last_fire_ts, start_ts }
        self._init_timers(edge_instances, edge_behavior_table)
    
    def _init_timers(self, edge_instances, edge_behavior_table):
        """从边配置中初始化定时器"""
        for eid, edge_inst in edge_instances.items():
            type_id = edge_inst.get('edge_type', 'default_edge')
            behavior_def = edge_behavior_table.get(type_id, {})
            
            # 只处理时间触发的边
            trigger_mode = behavior_def.get('trigger_mode')
            if trigger_mode != 'time_driven':
                continue
            
            # 进一步区分是定时触发还是周期确认触发
            trigger_subtype = edge_inst.get('params', {}).get('trigger_subtype', 'period_confirm')
            
            if trigger_subtype == 'absolute_time':
                # 绝对时间触发
                self.absolute_timers[eid] = {
                    'target_hms': edge_inst['params'].get('target_hms', 93000),
                    'offset_sec': edge_inst['params'].get('offset_sec', 0),
                }
            elif trigger_subtype == 'relative_time':
                # 相对时间触发
                self.relative_timers[eid] = {
                    'interval_sec': edge_inst['params'].get('interval_sec', 300),
                    'last_fire_ts': None,
                    'start_ts': time.time(),
                }
            # period_confirm 类型不归 scheduler 管，归周期确认事件管
    
    def check_due(self, sys_ts, is_trading):
        """检查哪些定时任务到点了（用系统时间）"""
        due_edges = []
        
        # 检查绝对时间触发
        for eid, timer in self.absolute_timers.items():
            if not is_trading:
                continue  # 非交易时间不触发
            target_ts = get_today_target_ts(sys_ts, timer['target_hms']) + timer['offset_sec']
            # 判断是否在触发窗口内（1分钟窗口，避免重复触发）
            if target_ts <= sys_ts < target_ts + 60:
                if timer.get('last_fire_day') != get_day_key(sys_ts):
                    due_edges.append(eid)
                    timer['last_fire_day'] = get_day_key(sys_ts)
        
        # 检查相对时间触发
        for eid, timer in self.relative_timers.items():
            interval = timer['interval_sec']
            last_fire = timer.get('last_fire_ts')
            start = timer['start_ts']
            
            if last_fire is None:
                # 第一次触发
                if sys_ts - start >= interval:
                    due_edges.append(eid)
            else:
                if sys_ts - last_fire >= interval:
                    due_edges.append(eid)
        
        return due_edges
    
    def mark_fired(self, eid, sys_ts):
        """标记某条边已触发"""
        if eid in self.relative_timers:
            self.relative_timers[eid]['last_fire_ts'] = sys_ts
```

---

### 4.3 周期确认触发的边处理（数据时间驱动）

```python
def process_period_confirm_edges(period_event, edge_instances, edge_behavior_table):
    """处理周期确认触发的边（用数据时间）
    
    周期确认触发 = 数据时间跨过周期边界
    每只股票独立确认（股票级粒度）
    """
    period = period_event['period']
    confirmed_stocks = period_event['confirmed_stocks']  # 哪些股票的周期确认了
    confirmed_time = period_event['confirmed_time']      # 数据时间
    
    for eid, edge_inst in edge_instances.items():
        # 查类型表
        type_id = edge_inst.get('edge_type', 'default_edge')
        behavior_def = edge_behavior_table.get(type_id, {})
        
        # 只处理时间驱动 + 周期确认子类型的边
        if behavior_def.get('trigger_mode') != 'time_driven':
            continue
        
        trigger_subtype = edge_inst.get('params', {}).get('trigger_subtype', 'period_confirm')
        if trigger_subtype != 'period_confirm':
            continue
        
        # 周期不匹配的跳过
        edge_period = edge_inst.get('trigger_period', behavior_def.get('default_trigger_period'))
        if edge_period != period:
            continue
        
        # 交易日历检查（确认时间是不是在交易时段内）
        if not trade_calendar.is_trading_time(confirmed_time):
            continue
        
        # 执行这条边（只处理周期确认了的股票，股票级粒度）
        process_edge_period_confirm(eid, edge_inst, confirmed_stocks, confirmed_time)
```

---

### 4.4 双时间模型使用对照表

| 功能 | 用什么时间 | 代码位置 | 为什么 |
|------|-----------|---------|--------|
| tick 等待间隔 | 系统时间 | `await asyncio.sleep(tick_interval)` | 定时等待，看系统时间 |
| 上次轮询时间 | 系统时间 | `last_poll_ts = sys_ts` | 记录"我们什么时候轮询的" |
| 上次计算时间 | 系统时间 | `last_calc_ts = time.time()` | 记录"我们什么时候算完的" |
| 超时检测 | 系统时间 | `sys_ts - last_poll_ts > timeout` | "我们多久没收到数据了" |
| TTL 过期 | 系统时间 | `process_ttl_expiry(sys_ts, ...)` | "我们等了多久" |
| 交易时间判断（实盘） | 系统时间 | `trade_calendar.is_trading_time(sys_ts)` | "现在是不是交易时间" |
| tick 数据时间 | 数据时间 | `tick_data_ts[code]` | 每只股票最新 tick 的时间戳 |
| K线数据时间 | 数据时间 | `bar_data_ts[code][period]` | 每只股票每周期K线的时间戳 |
| 周期确认 | 数据时间 | `period_bar_confirmed(period, bar_data_ts)` | K线确认看数据时间 |
| 绝对时间触发 | 系统时间 | `scheduler.check_due(sys_ts)` | 定时任务看系统时间 |
| 相对时间触发 | 系统时间 | `sys_ts - last_fire >= interval` | 间隔计时看系统时间 |
| 周期确认触发 | 数据时间 | `period_confirmed_events` | K线确认看数据时间 |
| 指标计算（值的基准） | 数据时间 | `formula_engine.eval_batch(...)` | 指标基于行情数据 |
| 入池时间戳 | 系统时间 | `in_pool_ts` | 记录"我们什么时候让它入池的" |
| 日志时间戳 | 系统时间 | 日志记录时 | 记录"我们什么时候处理的" |
| 性能统计 | 系统时间 | `last_calc_ts - last_poll_ts` | 算我们的处理耗时 |
| 判断计算卡住 | 系统时间 | `sys_ts - last_calc_ts` | 系统时间过了很久还没算完 |

---

## 五、功能-表操作对应表（v1.17 更新版）

### 5.1 基础设施层

| 功能 | 读什么表/模块 | 写什么表/模块 | 计算 | 时间基准 | 错误场景 | 处理策略 |
|------|-------------|-------------|------|---------|---------|---------|
| **交易日判断** | trade_calendar_table | — | 排除周末、节假日，加上额外交易日 | 系统时间（实盘）/ 数据时间（回测） | 日历数据缺失 | 使用默认日历，记录warning |
| **交易时段判断** | trade_calendar_table | — | 当前时间在不在任一交易时段内 | 系统时间（实盘） | 时段配置错误 | 保守判断：不确认就不算交易时间 |
| **数据轮询** | data_provider | latest_tick + tick_data_ts + stock_status_table | 主动 pull 最新数据，对比变化 | 数据时间（数据本身） | 数据源断开/超时 | 指数退避重试，降级到备用数据源 |
| **指标计算** | formula_table + formula_engine | formula_engine内部表 | 公式引擎向量化批量计算 | 数据时间（值的基准） | 公式错误/数据不足 | 结果设为None，股票级隔离 |
| **配置加载** | config_store | — | 加载、缓存、校验配置表 | 系统时间（加载时间） | 配置格式错误/校验失败 | 保留旧配置，记录error，告警 |

---

### 5.2 配置层-类型定义

（继承v1.16，无变化）

---

### 5.3 配置层-实例定义

（继承v1.16，无变化）

---

### 5.4 配置层-系统配置

（继承v1.16，无变化）

---

### 5.5 运行时层-主循环

| 功能 | 读什么表 | 写什么表 | 计算 | 时间基准 | 错误场景 | 处理策略 |
|------|---------|---------|------|---------|---------|---------|
| **轮询等待** | system_config_table.tick_interval | — | sleep(tick_interval) | **系统时间** | — | — |
| **数据轮询** | data_provider | latest_tick + tick_data_ts + dirty_stocks + stock_status_table | 主动 pull，对比变化，标记脏股票 | 数据时间（数据本身） | 数据源超时/断开 | 重试→降级→告警，股票状态设为异常 |
| **交易时间判断** | trade_calendar_table | — | 先判断交易日，再判断交易时段 | **系统时间**（实盘） | 日历数据缺失 | 保守判断：不确认就不算交易时间 |
| **股票状态检测** | 最新数据 + 状态检测规则 | stock_status_table | 检测停牌/数据不足/异常 | 数据时间（判断依据） | 检测逻辑出错 | 设为normal（保守），记录error |
| **超时检测** | sys_ts + last_poll_ts + system_config_table | — | sys_ts - last_poll_ts > 阈值 | **系统时间** | — | — |
| **周期更新** | period_table + 最新数据 | period_data + bar_data_ts | 更新未完成K线 | **数据时间** | K线计算出错 | 股票级隔离，该股票周期数据不更新 |
| **周期确认事件** | period_data + bar_data_ts | period_confirmed_events | 数据时间跨过边界，发确认事件 | **数据时间** | — | — |
| **定时触发检查** | scheduler + trade_calendar | timed_trigger_edges | 检查绝对时间和相对时间是否到点 | **系统时间** | 调度器出错 | 降级为每tick都触发，记录error |
| **脏驱动跳过** | dirty_stocks + period_confirmed_events + timed_trigger_edges | — | 没变化就跳过计算 | — | — | — |
| **节点变化计算** | dirty_stocks + node_stocks | node_changes | entered/exited/updated三集合 | — | 计算出错 | 节点级隔离，该节点变化清空 |
| **拓扑序处理** | node_changes + pool_table.topology | 各层状态表 | 按拓扑序处理脏节点 | — | 拓扑排序失败 | 退化为按ID顺序，记录error |
| **更新last_poll_ts** | sys_ts | last_poll_ts | 轮询结束后更新 | **系统时间** | — | — |
| **更新last_calc_ts** | sys_ts | last_calc_ts | 计算结束后更新 | **系统时间** | — | — |

---

### 5.6 运行时层-节点处理

（继承v1.16，无变化）

---

### 5.7 运行时层-边执行层

| 功能 | 读什么表 | 写什么表 | 计算 | 时间基准 | 错误场景 | 处理策略 |
|------|---------|---------|------|---------|---------|---------|
| **边实例信息** | edge_instance_table | — | 查表得到type_id、filter_config | — | 实例不存在 | 跳过该边，记录error |
| **边类型信息** | edge_type_table + type_id | — | 通过type_id查类型定义 | — | 类型不存在 | 边级隔离，该边暂停，告警 |
| **触发模式判断** | edge_behavior_table.trigger_mode | — | 数据驱动 / 时间驱动（定时+周期确认） | — | — | — |
| **绝对时间触发检查** | trade_calendar_table + scheduler | — | 系统时间是否到达目标时间 | **系统时间** | — | — |
| **相对时间触发检查** | scheduler | — | 系统时间间隔是否达到 | **系统时间** | — | — |
| **周期确认触发检查** | period_confirmed_events + trade_calendar_table | — | 周期确认事件 + 交易时段检查 | **数据时间** | — | — |
| **股票状态过滤** | stock_status_table | — | 停牌/异常的按保守策略处理 | — | — | 结果设为None，不传播 |
| **第一层：指标计算** | formula_table + 公式引擎 | 公式引擎内部表 | 查类型表得到公式，批量计算 | 数据时间（值的基准） | 公式计算错误 | 股票级隔离，该股票结果设为None |
| **第二层：比较判断** | operator_table + 指标值 + 股票状态 | edge_compare_results | 查类型表调算子，三态逻辑 | — | 算子执行错误 | 股票级隔离，结果设为None |
| **第三层：组合运算** | operator_table（combine类） + 比较结果 | edge_filter_results | 查类型表调组合函数，三态逻辑 | — | 组合运算错误 | 边级隔离，结果设为空集 |
| **propagate** | propagate_handler + filter结果 | node_stocks + node_changes | 股票传播，保守策略 | — | 传播失败 | 边级隔离，不传播，记录error |
| **事件发射** | node_changes[tid] | 事件队列 | entered/exited发事件 | — | 事件发送失败 | 不影响主流程，记录warning |

---

### 5.8 运行时层-TTL淘汰层

| 功能 | 读什么表 | 写什么表 | 计算 | 时间基准 | 错误场景 | 处理策略 |
|------|---------|---------|------|---------|---------|---------|
| **股票入池记录TTL** | edge_instance_table.ttl_config | ttl_expiry_queue 插入 | expire_ts = in_pool_ts + ttl_sec | **系统时间** | TTL配置错误 | 不设置TTL（永不过期），记录warning |
| **TTL过期检查** | ttl_expiry_queue + sys_ts | 弹出过期项 | 最小堆：堆顶过期就弹出 | **系统时间** | 堆操作出错 | 全量扫描（降级），记录error |
| **过期股票移除** | node_stocks[nid] | node_stocks[nid] | 从节点移除 | — | 移除失败 | 跳过该股票，继续处理其他，记录error |
| **过期触发级联** | — | node_changes[nid].exited | 加入exited集合 | — | — | — |

**重要：** TTL全部用**系统时间**，因为TTL是"我们等了多久"。

---

### 5.9 运行时层-错误处理

（继承v1.16，无变化）

---

### 5.10 接口层

（继承v1.16，无变化）

---

## 六、统计总结（v1.16 → v1.17）

### 6.1 概念数量变化

| 统计项 | v1.16 | v1.17 | 变化 |
|--------|------|-------|------|
| 配置表数 | 15 张 | 15 张 | 0（结构不变，时间说明更新） |
| 运行时表数 | 12 张 | 12 张 | 0（结构不变，时间说明更新） |
| 时间触发模式 | 1种（模糊的"时间驱动"） | **3种（绝对时间/相对时间/周期确认）** | **+2种，澄清概念** |
| 双时间模型场景 | 模糊不清 | **15+场景对照表，每个都有理由** | 从"凭感觉"到"有清晰标准" |
| 核心循环步骤 | 12步 | 16步 | **+4步（定时触发独立出来）** |
| 根本性错误 | 1个（时间触发用数据时间） | **0个（已纠正）** | 纠正一个大错 |

### 6.2 为什么是 v1.17？

**v1.17 是"纠错 + 澄清"的版本：**

1. **纠错**：纠正了"时间触发用数据时间"这个根本性错误
2. **澄清**：把模糊的"时间驱动"拆成三种模式（绝对时间/相对时间/周期确认），每种模式的时间基准、触发源、用途都清清楚楚
3. **明确**：每个场景用什么时间、为什么用，都有明确的对照表，再也不会搞混

```
演进路径：
  v1.5 ~ v1.6：概念精简阶段（从多到少，先做对）
  v1.7：性能优化阶段（股票级水位线，增量计算）
  v1.8：状态显式化阶段（三层状态表，每层结果都有表）
  v1.9：架构完善阶段（一致性 + 事件驱动 + 生命周期）
  v1.10：深度澄清阶段（并发性能 + 批次定位 + 三态传播）
  v1.11：根本性纠错（删除时间批次化，零延迟事件模型）
  v1.12：三态逻辑完善（三态贯穿全链路 + 保守策略）
  v1.13：表驱动架构升级（节点/边/算子三层表驱动）
  v1.14：L2组合表驱动落地（+公式引擎 + filter三层组合）
  v1.15：诚实化 + 类型实例分离 + 五时间戳 + 四层架构
  v1.16：详细设计 + 表Schema + 错误处理 + 层间依赖
  v1.17：根本性纠错 + 双时间模型澄清 + 三种触发模式 ◀ 当前
```

---

## 附录：一句话记忆法

**"什么时候做" = 系统时间**
**"数据什么时候" = 数据时间**

| 你想问的问题 | 答案就是 | 用什么时间 |
|------------|---------|-----------|
| "几点了？" | 系统时间 | 系统时间 ✅ |
| "过了多久？" | 系统时间 | 系统时间 ✅ |
| "数据是什么时候的？" | 数据时间 | 数据时间 ✅ |
| "K线走完了吗？" | 看数据时间 | 数据时间 ✅ |
| "到点了吗？" | 系统时间 | 系统时间 ✅ |
| "等了多久？" | 系统时间 | 系统时间 ✅ |
