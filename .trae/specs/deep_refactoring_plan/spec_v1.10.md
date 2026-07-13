# 股票池深度重构规划 v1.10

> 版本主题：并发深度 + 批次降维 + 三态传播
> 设计原则：表驱动、数据驱动、事件驱动、增量优先、全量保底、概念最简
> 目标：澄清并发模型的性能瓶颈和向量化应对方案；批次化从架构核心降为性能优化手段；澄清版本屏障的真正范围（每层有自己的一致性边界）；修复比较层 entered 处理 bug；明确三态（True/False/None）的完整传播路径和运算规则

---

## v1.9 → v1.10 变更摘要表

**变更日期：** 2026-07-01

| # | 变更项 | v1.9 | v1.10 | 本质变化 |
|---|--------|------|-------|---------|
| 1 | **并发模型深度** | 只讲单线程事件循环 + 批次化保一致，不谈性能代价 | **单线程事件循环 + 向量化计算 + 批次大小可调** | 从"只讲一致性"到"一致性与性能平衡"——明确 CPU 密集型计算的性能瓶颈，用向量化解决，批次大小是可调参数 |
| 2 | **批次化定位** | 批次化是架构核心，版本屏障靠批次实现 | **批次化是性能优化手段，架构核心是"事件驱动 + 状态机"** | 从"批次是一等公民"到"批次是实现细节"——概念上每条 tick 都是独立事件，只是为了性能攒一批处理 |
| 3 | **版本屏障范围** | 整个系统一个 batch_version，版本屏障是批次级的 | **每层有自己的版本号/一致性边界** | 从"全局一个版本号"到"分层一致性"——数据层、指标层、比较层、集合运算层各有各的版本，版本屏障是"计算开始时拍快照" |
| 4 | **比较层 entered 处理** | 新入池股票直接计算比较结果（True/False） | **先设为 None（数据不足），再根据当前数据计算** | 从"假设数据就绪"到"保守估计"——新入池的股票可能数据还没准备好，不能直接判 True/False |
| 5 | **三态传播路径** | 只提到比较层三态，没讲完整传播路径 | **三态完整传播：指标层 → 比较层 → 集合运算层 → 目标节点** | 从"只在比较层"到"全链路传播"——明确 AND/OR/NOT 的三态运算规则，排名型数据不足不参与排名 |
| 6 | **核心运行时表** | 9 张 | **9 张（语义澄清，数量不变）** | 表数量不变，但每层的版本/一致性边界更清晰 |
| 7 | **核心循环伪代码** | 比较层 entered 直接计算结果 | **比较层 entered 先设 None，再计算** | 修复 bug：新入池股票比较结果初始化为 None |

**一句话总结 v1.10 升级：** 澄清并发模型的性能代价与向量化应对方案（公式引擎 CPU 密集用 NumPy 向量化，股票池引擎 IO 密集用事件循环）；批次化从架构核心降为性能优化手段；版本屏障澄清为分层一致性（每层有自己的版本边界）；修复比较层 entered 处理 bug（先设 None 再计算）；明确三态（True/False/None）的完整传播路径和逻辑运算规则（类 SQL NULL 语义）。

---

## 一、核心升级一：并发模型深度——性能代价与向量化应对

### 1.1 问题：单线程事件循环的性能瓶颈

**v1.9 只讲了单线程事件循环的好处（一致性、无竞态），但没讲代价：Python 单线程 CPU 密集型计算会阻塞事件循环。**

```
单线程事件循环的性能瓶颈：

  事件循环（单线程）
    ┌───────────────────────────────────────────┐
    │  处理事件 → 计算指标 → 处理下一个事件       │
    │            ↑                              │
    │            └── 这里如果是 CPU 密集，       │
    │                会阻塞整个事件循环          │
    └───────────────────────────────────────────┘

问题：
  1. 股票池的指标计算是 CPU 密集的（TA-Lib、NumPy、pandas rolling）
  2. 如果在事件循环里直接算 5000 只股票 × 100 个指标，会阻塞很久
  3. 阻塞期间，新的 tick 事件、定时器事件都得不到及时处理
  4. 结果：延迟增加，定时器不准，系统看起来"卡"了
```

**股票池的计算特点：**
- 指标计算：CPU 密集型（MA、MACD、RSI 等，用 TA-Lib 或 NumPy/pandas 向量化）
- 比较判断：轻量（就是比较一下数值）
- 集合运算：轻量（集合交并差）
- 事件调度：IO 密集型（等待事件、分发事件）

**结论：CPU 密集的部分不能放在事件循环里硬扛，必须想办法优化。**

### 1.2 方案：向量化计算 + 批次大小可调

**核心思想：把 CPU 密集的计算放到公式引擎里用向量化，股票池引擎只做轻量的事件调度和状态管理；批次大小可调，平衡延迟和吞吐。**

```
优化后的并发模型：

  ┌───────────────────────────────────────────────────┐
  │              股票池引擎（IO 密集）                  │
  │  事件循环 + 状态管理 + 调度 + 轻量逻辑               │
  │  （单线程，低延迟，不做重计算）                      │
  └───────────────────┬───────────────────────────────┘
                      │ 调用 eval_indicators()
                      ▼
  ┌───────────────────────────────────────────────────┐
  │              公式引擎（CPU 密集）                   │
  │  向量化计算 + 批量处理 + 缓存                       │
  │  （NumPy/pandas 底层是 C，Python 层只是调度）        │
  └───────────────────────────────────────────────────┘

关键洞察：
  - 股票池引擎是 IO 密集的：事件调度、状态管理、集合运算，都是轻量的
  - 公式引擎是 CPU 密集的：指标计算，用向量化，批量算比逐条算快 10-100 倍
  - 两者职责分离，各自在自己的领域做到最优
```

**为什么向量化能解决问题？**

```
向量化 vs 逐条循环：

  逐条循环（Python 层循环，慢）：
    for code in codes:
        result[code] = calc_ma(prices[code], 5)  # Python 循环调用
    → 5000 只股票，循环 5000 次，每次都有 Python 解释器开销

  向量化（NumPy/pandas 底层 C 实现，快）：
    all_prices = np.array([prices[code] for code in codes])
    result = pd.rolling_mean(all_prices, window=5)  # 底层一次 C 运算
    → 一次向量化运算，Python 层开销只有一次
    → 比逐条循环快 10-100 倍
```

**现有代码验证（formula_engine.py）：**
- `window_op()` 用 `pd.Series.rolling()` 实现滚动窗口（pandas 底层 C 实现）
- `shift_op()` 用 `pd.Series.shift()` 实现偏移（向量化）
- `ema_op()` / `sma_op()` 用 `pd.Series.ewm()` 实现指数加权（向量化）
- `cross_op()` 用 `&` 运算符对两个 Series 做逐元素与（向量化）
- `_dispatch_func()` 表驱动分派到通用算子，无 Python 循环

**性能估算：**
- 5000 只股票，100 个指标，向量化计算应该在毫秒级（~10-100ms）
- 对比：逐条 Python 循环可能要几秒到几十秒
- 只要公式引擎的向量化计算足够快，单线程事件循环就不会被阻塞太久

### 1.3 批次大小可调：延迟与吞吐的平衡

**批次大小不是固定的，是可调参数——tick 少时小批次低延迟，tick 多时大批次高吞吐。**

```
批次大小的权衡：

  小批次（如 10ms 或 10 条 tick）：
    ✓ 延迟低（tick 来了很快就能处理）
    ✓ 实时性好
    ✗ 吞吐低（批次 overhead 占比高）
    ✗ 向量化优势不明显（批量太小）

  大批次（如 1s 或 1000 条 tick）：
    ✓ 吞吐高（批次 overhead 占比低）
    ✓ 向量化优势明显（批量大，一次算很多）
    ✗ 延迟高（tick 来了要等攒够一批）
    ✗ 实时性差

  动态调整：
    - tick 稀疏时（如盘前盘后）：小批次，低延迟
    - tick 密集时（如开盘尾盘）：大批次，高吞吐
    - 或者按时间窗口：最多等 N 毫秒，到点就处理
```

**批次策略参数：**

| 参数 | 说明 | 典型值 |
|------|------|--------|
| `batch_max_wait_ms` | 最大等待时间（到点就处理，不管攒了多少） | 100ms ~ 1000ms |
| `batch_max_size` | 最大批次大小（攒够就处理，不等时间到） | 100 ~ 10000 条 |
| `batch_min_size` | 最小批次大小（攒够才处理，除非超时） | 1 ~ 100 条 |

**为什么批次化是性能优化，不是架构核心？**
- 概念上，每条 tick 都是独立事件，理论上来一个就该处理一个
- 实际上，攒一批处理效率更高（向量化优势、减少 overhead）
- 但如果去掉批次化，来一个处理一个，架构还是成立的——只是性能差
- 所以批次化是"性能优化手段"，不是"架构核心"
- 架构核心是"事件驱动 + 状态机"，批次化是实现层面的优化

### 1.4 与多线程/多进程的对比

**为什么不直接用多线程/多进程？**

| 方案 | 优点 | 缺点 | 适用场景 |
|------|------|------|---------|
| **单线程 + 向量化** | 简单、无竞态、一致性好、调试容易 | 极致性能受限（但股票池够用） | ✅ 股票池首选 |
| 多线程 | 利用多核 | GIL 限制 CPU 密集型性能、竞态、锁、死锁 | IO 密集型场景 |
| 多进程 | 真正并行、利用多核 | 进程间通信复杂、状态同步难、内存开销大 | 纯 CPU 密集、无状态 |

**为什么股票池选单线程 + 向量化？**
1. **一致性简单**：单线程天然没有竞态，版本屏障容易实现
2. **向量化足够快**：NumPy/pandas 底层是 C，5000 只股票 100 个指标毫秒级
3. **调试容易**：单线程 bug 好复现，多线程 bug 难以捉摸
4. **内存开销小**：不需要多份状态副本
5. **股票池不是极致性能场景**：秒级延迟完全可接受，不需要微秒级

**什么时候才需要多进程？**
- 如果股票数量到了 10 万+，指标 1000+，向量化也扛不住了
- 那时候可以考虑把公式引擎放到独立进程，用消息队列通信
- 但股票池引擎还是单线程，只是把 CPU 密集的部分 offload 出去
- 目前阶段完全不需要，过早优化是万恶之源

---

## 二、核心升级二：批次化降维——从架构核心到性能优化

### 2.1 问题：批次化喧宾夺主了

**v1.9 把批次化放在很核心的位置，给人的感觉是"没有批次化，架构就不成立"。但实际上不是——批次化是性能优化，不是架构核心。**

```
v1.9 给人的错觉：

  批次化 → 版本屏障 → 一致性 → 架构成立
  （批次是前提）

  实际上应该是：

  事件驱动 + 状态机 → 架构成立
        ↑
    批次化是性能优化（可选的，只是默认开启）
```

**为什么这很重要？**
- 如果把批次化当作架构核心，很多设计会被带偏
- 比如：什么都想套个 batch_version，什么都想按批次来
- 但实际上，每层有自己的一致性边界，不需要全局一个版本号
- 又比如：定时器事件跟批次没关系，它是独立的事件源

### 2.2 架构核心：事件驱动 + 状态机

**架构核心是"事件驱动 + 状态机"，批次化是实现层面的性能优化。**

```
架构核心（一等公民）：

  事件 → 状态机 → 新状态 → 事件
   ↑                      ↓
   └──────────────────────┘

  事件类型（都是一等公民）：
    - 数据事件：tick 来了
    - 定时器事件：时间到了
    - 控制事件：启动/暂停/停止

  状态机：
    - 状态表：latest_tick、node_stocks、edge_compare_results...
    - 转换函数：process_edge()、propagate()...
    - 每个事件触发状态转换
```

**批次化在哪里？**

```
批次化的位置（实现细节，二等公民）：

  数据事件源 → [攒批缓冲] → 事件循环
                  ↑
             这就是批次化
             只是把多个数据事件合并成一个批次事件
             减少事件循环的调度次数
             提升性能

  注意：
    - 只有数据事件需要攒批（因为 tick 频率高）
    - 定时器事件不需要攒批（精确到点）
    - 控制事件不需要攒批（立即响应）
    - 批次化只影响数据事件的处理粒度，不影响架构本质
```

### 2.3 概念上：每条 tick 都是独立事件

**概念上，每条 tick 都是独立事件，理论上来一个处理一个。只是为了性能，我们攒一批处理。**

```
概念模型（正确的思维方式）：

  tick1 → 处理 → 更新状态 → tick2 → 处理 → 更新状态 → ...
  （每条 tick 都是独立事件，逐条处理）

  实现模型（性能优化）：

  tick1, tick2, tick3, ... → 攒一批 → 一次性处理 → 更新状态
  （攒一批处理，效率更高，但语义上和逐条处理等价）

  关键：批次化不改变语义，只改变性能
    - 批次处理的结果 = 逐条处理的结果（只要批次内没有依赖）
    - 如果批次化改变了语义，那就是 bug
```

**为什么语义等价？**
- 股票池的计算是"基于当前状态"的，状态是最新的就行
- 批次开始时，一次性把所有 tick 更新到 latest_tick
- 然后基于最新状态计算
- 这和逐条更新逐条计算的结果是一样的（因为计算只依赖最终状态）
- 当然，前提是计算是"无状态"的，或者状态只依赖最新数据

### 2.4 架构图里的位置

**架构图里，事件是一等公民，批次是实现细节，画在角落里就行。**

```
v1.9 的架构图（批次太显眼了，像核心）：

  事件循环
    ├── 批次 N
    ├── 批次 N+1
    └── 批次 N+2
  （批次占了主要位置）

v1.10 的架构图（事件是核心，批次是细节）：

                    ┌─────────────────────┐
                    │    事件循环（核心）   │
                    └──────────┬──────────┘
                               │
          ┌────────────────────┼────────────────────┐
          ▼                    ▼                    ▼
     数据事件               定时器事件            控制事件
  （攒批优化）            （精确触发）          （立即响应）
       ↑
    这是实现细节
    不影响架构本质
```

---

## 三、核心升级三：版本屏障澄清——分层一致性边界

### 3.1 问题：版本屏障不是"整个系统一个版本号"

**v1.9 给人的感觉是"整个系统一个 batch_version，所有层都用这个版本号"。但实际上不是——每层有自己的版本号/一致性边界。**

```
v1.9 给人的错觉：

  batch_version = 12345
    ↓
  数据层 v12345
  指标层 v12345
  比较层 v12345
  集合运算层 v12345
  （所有层同一个版本号）

  实际上：

  数据层：latest_tick_ts = 1719820800.123  （数据版本）
  指标层：每个指标有自己的缓存版本        （公式引擎内部维护）
  比较层：edge_compare_results 有自己的更新版本
  集合运算层：edge_filter_results 有自己的更新版本
  （每层有自己的一致性边界）
```

**为什么不能全局一个版本号？**
1. **粒度太粗**：全局版本号每次都 +1，但实际上可能只有少数股票的数据变了
2. **不必要的失效**：全局版本号一变，所有层的缓存都失效，但其实很多不需要重算
3. **不符合分层原则**：每层应该有自己的状态和版本，不应该依赖全局的东西

### 3.2 每层的一致性边界

**版本屏障的本质是：计算开始时拍一个快照，计算过程中数据不影响本次结果。每层有自己的快照。**

```
分层一致性边界：

  ┌─────────────────────────────────────────────────┐
  │ 数据层（latest_tick）                            │
  │   版本：latest_tick_ts[code]                     │
  │   一致性边界：每只股票的最新 tick 时间             │
  │   屏障：批次开始时批量更新，计算阶段只读           │
  └───────────────────┬─────────────────────────────┘
                      │ 数据输入
                      ▼
  ┌─────────────────────────────────────────────────┐
  │ 指标层（公式引擎内部 indicator_results）          │
  │   版本：每个 (formula_id, code) 有自己的版本     │
  │   一致性边界：单个公式单只股票的指标值            │
  │   屏障：计算指标时，数据是当时的快照              │
  └───────────────────┬─────────────────────────────┘
                      │ 指标值输入
                      ▼
  ┌─────────────────────────────────────────────────┐
  │ 比较层（edge_compare_results）                   │
  │   版本：每条边每只股票的比较结果更新版本          │
  │   一致性边界：单条边单只股票的比较结果            │
  │   屏障：比较计算基于当时的指标值快照              │
  └───────────────────┬─────────────────────────────┘
                      │ 比较结果输入
                      ▼
  ┌─────────────────────────────────────────────────┐
  │ 集合运算层（edge_filter_results）                 │
  │   版本：每条边的过滤结果更新版本                  │
  │   一致性边界：单条边的过滤结果                    │
  │   屏障：集合运算基于当时的比较结果/指标值快照      │
  └─────────────────────────────────────────────────┘
```

**各层的版本/一致性边界详解：**

| 层级 | 版本标识 | 一致性边界 | 屏障机制 |
|------|---------|-----------|---------|
| **数据层** | `latest_tick_ts[code]` | 每只股票的最新 tick | 批次开始时批量更新，计算阶段只读不写 |
| **指标层** | 公式引擎内部缓存版本 | 每个 (formula_id, code, period) | 计算指标时取当时的数据，计算过程中数据不变 |
| **比较层** | 比较结果的隐式版本（靠 dirty 标记） | 每条边每只股票的比较结果 | 比较计算基于当时的指标值，计算过程中指标值不变 |
| **集合运算层** | 过滤结果的隐式版本（靠 dirty 标记） | 每条边的过滤结果 | 集合运算基于当时的比较结果/指标值，计算过程中不变 |

### 3.3 版本屏障的真正含义

**版本屏障不是"什么都套一个 batch_version"，而是"计算开始时拍一个快照，计算过程中数据不影响本次结果"。**

```
版本屏障的工作原理（通用模式）：

  1. 计算开始前
     └─ 确定输入数据的版本/快照（不是全局版本，是本次计算需要的数据）

  2. 计算过程中
     └─ 所有读取都基于这个快照
     └─ 即使底层数据更新了，本次计算也看不到
     └─ 保证结果的一致性

  3. 计算结束后
     └─ 输出结果 + 结果对应的版本
     └─ 下次计算时，检查输入数据版本有没有变
     └─ 没变就复用结果，变了就重算
```

**在单线程事件循环里，版本屏障天然成立：**
- 因为是单线程，计算过程中不会有其他事件处理
- 所以计算过程中数据不会变（数据更新也是事件，要等当前事件处理完才会处理下一个）
- 从这个角度看，单线程事件循环本身就是"天然的版本屏障"
- batch_state 里的 batch_version 更多是"批次标识"，不是"全局版本号"

### 3.4 为什么单线程事件循环是天然的版本屏障

**单线程事件循环 = 天然的版本屏障，因为计算过程中不会有其他事件插入。**

```
单线程事件循环的一致性保证：

  时间线 →

  事件A处理中
    读数据 → 计算 → 写结果
    ↑              ↑
    │              │
    └──────────────┘
     这期间没有其他事件
     数据不会变
     所以结果是一致的

  然后才处理事件B
  事件B可能更新数据
  但不影响事件A的结果
```

**这就是为什么我们选单线程事件循环：**
- 一致性是天然的，不需要复杂的锁、MVCC、事务
- 只要保证"一个事件处理完之前，不处理下一个事件"
- 数据一致性就有保证
- 批次化只是把多个数据事件合并成一个，不改变这个本质

---

## 四、核心升级四：修复比较层 entered 处理 bug

### 4.1 问题：新入池股票直接计算比较结果，可能数据不足

**v1.9 的伪代码里，新入池的股票（entered）直接计算比较结果。但新入池的股票可能数据还没准备好，直接计算可能得到错误的 False，而不是 None。**

```
v1.9 的问题（伪代码）：

  # 新入池的：计算比较结果
  for code in entered:
      if code in indicator_values.get(indicator_ids[0], {}):
          result = do_compare(indicator_values, code, compare_spec)
          edge_compare_results[eid][code] = result
      else:
          # 数据不足，标记为 None
          edge_compare_results[eid][code] = None

  问题：
    - 这里只检查了"本次有没有计算指标"
    - 但如果股票之前就在别的节点里，公式引擎缓存里可能有旧数据
    - 旧数据可能已经过期了，或者不完整
    - 用旧数据计算比较结果，可能是错的
    - 更保守的做法是：先设为 None，再根据当前数据计算
```

**更严重的问题：**
- 新入池的股票，可能是第一次进入股票池
- 它的 K 线数据可能还没加载完（比如只加载了一部分）
- 这时候计算指标，可能得到不完整的结果
- 用不完整的结果做比较，可能误判

**正确的做法：**
1. 新入池的股票，比较结果先设为 None（数据不足）
2. 然后再根据当前可用的数据计算比较结果
3. 如果数据足够，结果会是 True/False
4. 如果数据不足，结果保持 None
5. 这样至少不会"瞎判"

### 4.2 方案：先设 None，再计算

**新入池的股票，比较结果先初始化为 None，然后再根据当前数据计算。如果数据足够，计算结果会覆盖 None；如果数据不足，保持 None。**

```
v1.10 的正确做法：

  # 1. 新入池的：先初始化为 None（数据不足，保守估计）
  for code in entered:
      edge_compare_results[eid][code] = None

  # 2. 然后根据当前数据计算（数据足够的话会覆盖 None）
  codes_to_compare = entered & set(indicator_values.get(indicator_ids[0], {}).keys())
  for code in codes_to_compare:
      if code in source_codes:
          result = do_compare(indicator_values, code, compare_spec)
          edge_compare_results[eid][code] = result

  3. 出池的：清理比较结果
  for code in exited:
      edge_compare_results[eid].pop(code, None)
```

**为什么这样更安全？**
- 最坏情况：数据不足，结果是 None，不会误判
- 最好情况：数据足够，结果正确计算
- 符合"三态"的设计思想：数据不足就是 None，不瞎猜
- 和指标层的行为一致：指标层数据不足也返回 None

### 4.3 出池的股票：清理比较结果

**出池的股票（exited），比较结果要清理掉。**

```
出池清理：

  for code in exited:
      # 清理比较结果
      edge_compare_results[eid].pop(code, None)
      # 清理过滤结果（在集合运算层做）
      edge_filter_results[eid].discard(code)
```

**为什么要清理？**
- 股票已经不在池子里了，留着比较结果没用
- 浪费内存
- 下次再入池时，可能用旧数据误判
- 清理掉，下次入池重新初始化（None），更安全

---

## 五、核心升级五：三态的完整传播路径

### 5.1 三态是什么？

**三态：True（通过）/ False（不通过）/ None（数据不足/未知）。类似 SQL 的 NULL 语义。**

| 状态 | 含义 | 说明 |
|------|------|------|
| `True` | 通过 | 明确满足条件 |
| `False` | 不通过 | 明确不满足条件 |
| `None` | 数据不足/未知 | 不知道满不满足，可能是数据不够，可能是新股，可能是停牌 |

**为什么需要三态？**
- 二态（True/False）的问题：数据不足时，你不知道该算 True 还是 False
- 算 False？不对，万一数据够了其实是 True 呢？
- 算 True？也不对，万一数据够了其实是 False 呢？
- 所以需要第三态：None = 不知道

### 5.2 三态的完整传播路径

**三态从指标层开始，一直传播到目标节点。每一层都要正确处理 None。**

```
三态传播路径：

  指标层 → 比较层 → 集合运算层 → propagate → 目标节点
    None → None → ??? → ??? → ???

  每一层都要回答：None 怎么办？
```

**各层的三态处理：**

| 层级 | 输入 | 输出 | None 处理规则 |
|------|------|------|--------------|
| **指标层** | K线数据 | 指标值（float 或 None） | 数据不足 → None |
| **比较层** | 指标值 + 比较规则 | True/False/None | 指标值为 None → 比较结果 None |
| **集合运算层（独立型）** | 比较结果 + 集合运算规则 | 通过/不通过/None？ | 见下方三态逻辑运算规则 |
| **集合运算层（排名型）** | 指标值 + 排名规则 | 排名列表 | 数据不足的股票不参与排名 |
| **propagate** | 过滤结果 | 节点股票列表 | None 不算入池（保守策略） |

### 5.3 三态逻辑运算规则

**类似 SQL 的 NULL 逻辑：AND、OR、NOT 的三态运算规则。**

#### AND 运算规则

| A | B | A AND B | 说明 |
|---|---|---------|------|
| True | True | True | 都通过才通过 |
| True | False | False | 有一个不通过就不通过 |
| True | None | None | 一个通过一个不知道 = 不知道 |
| False | True | False | 有一个不通过就不通过 |
| False | False | False | 都不通过 |
| False | None | False | 有一个不通过就不通过（不用管另一个） |
| None | True | None | 一个不知道一个通过 = 不知道 |
| None | False | False | 有一个不通过就不通过 |
| None | None | None | 都不知道 = 不知道 |

**口诀：AND 有 False 则 False，否则有 None 则 None，否则 True。**

#### OR 运算规则

| A | B | A OR B | 说明 |
|---|---|--------|------|
| True | True | True | 有一个通过就通过 |
| True | False | True | 有一个通过就通过 |
| True | None | True | 有一个通过就通过（不用管另一个） |
| False | True | True | 有一个通过就通过 |
| False | False | False | 都不通过 |
| False | None | None | 一个不通过一个不知道 = 不知道 |
| None | True | True | 有一个通过就通过 |
| None | False | None | 一个不知道一个不通过 = 不知道 |
| None | None | None | 都不知道 = 不知道 |

**口诀：OR 有 True 则 True，否则有 None 则 None，否则 False。**

#### NOT 运算规则

| A | NOT A | 说明 |
|---|-------|------|
| True | False | 通过变不通过 |
| False | True | 不通过变通过 |
| None | None | 不知道还是不知道 |

**口诀：NOT True→False, False→True, None→None。**

### 5.4 集合运算层的三态处理

**集合运算层（独立型）：多个条件的 AND/OR/NOT 组合，每个条件可能是 True/False/None。**

```
例子：A AND B AND C

  A = True, B = True, C = True → True（通过）
  A = True, B = False, C = True → False（不通过）
  A = True, B = None, C = True → None（数据不足）
  A = False, B = None, C = None → False（有 False 就是 False，不用管 None）
```

**集合运算层的输出是什么？**
- 对于独立型 filter：输出是"通过/不通过"的集合，但有 None 怎么办？
- 答案：None 的股票不算通过，也不算不通过，就是"待定"
- 但 edge_filter_results 是 Set 或 List，怎么表示 None？
- 答案：edge_filter_results 里只放 True 的，None 和 False 都不放
- 但是 edge_compare_results 里保留三态，方便增量计算

```
数据结构设计：

  edge_compare_results[eid][code] = True/False/None
    （保留三态，用于增量计算和逻辑组合）

  edge_filter_results[eid] = Set[code] 或 List[code]
    （只放通过的，None 和 False 都不在里面）
    （想查某只股票为什么不在里面，去 edge_compare_results 查）
```

### 5.5 排名型的三态处理

**排名型 filter：数据不足的股票不参与排名。**

```
排名型的三态处理：

  1. 收集所有有有效指标值的股票（排除 None 的）
  2. 对这些股票按指标值排名
  3. 取前 N 名作为过滤结果
  4. 数据不足的股票：不参与排名，也不在结果里
```

**为什么数据不足的不参与排名？**
- 排名是相对的，你不知道数据不足的股票如果数据够了会排第几
- 把它们排除在外，至少不会因为数据不足而误排
- 这是保守策略：宁可不排，也不排错

### 5.6 propagate 层的三态处理

**propagate：把过滤结果传播到目标节点。None 的股票不算入池。**

```
propagate 的三态处理：

  输入：edge_filter_results（只含通过的股票）
  输出：node_stocks[tid]（目标节点的股票列表）

  规则：
    - 通过的股票：进入目标节点
    - 不通过的股票：不进入目标节点
    - 数据不足的股票：不进入目标节点（保守策略）

  为什么是保守策略？
    - 宁可漏掉，也不错入
    - 数据不足就入池，风险太大
    - 等数据够了，自然会通过计算入池
```

**有没有更激进的策略？**
- 有：数据不足的股票也入池（"先入池再说，万一后面通过了"）
- 但这样会导致池子里有很多"不确定"的股票，后续计算可能都不准
- 股票池一般用保守策略，所以默认是"数据不足不入池"
- 如果需要激进策略，可以配置，但默认保守

### 5.7 三态传播的完整例子

```
完整例子：条件 = MA5 > MA10 AND VOL > 1000000

  股票 000001：
    MA5 = 10.5, MA10 = 10.0 → MA5 > MA10 = True
    VOL = 2000000 → VOL > 1000000 = True
    True AND True = True → 通过 ✓

  股票 000002：
    MA5 = 9.5, MA10 = 10.0 → MA5 > MA10 = False
    VOL = 500000 → VOL > 1000000 = False
    False AND False = False → 不通过 ✗

  股票 000003（新股，K线不够）：
    MA5 = None（数据不足）
    VOL = 800000 → VOL > 1000000 = False
    None AND False = False → 不通过 ✗
    （因为有一个 False，所以结果是 False，不是 None）

  股票 000004（停牌，没成交量）：
    MA5 = 10.2, MA10 = 10.0 → MA5 > MA10 = True
    VOL = None（停牌，没成交量）
    True AND None = None → 数据不足，不入池
```

---

## 六、运行时内存表（v1.10 更新版）

### 6.1 核心运行时表（9张，数量不变，语义澄清）

| 表名 | 类型 | 读时机 | 写时机 | 说明 |
|------|------|--------|--------|------|
| `latest_tick` | Dict[code → bar_dict] | 公式引擎计算时读（通过 DataProvider） | 批次开始时批量更新 | **唯一真相源**。所有股票的最新tick数据。版本：每只股票自己的 tick 时间戳 |
| `node_stocks` | Dict[nid → List[code]] | propagate 读写、filter 读 | 边执行/TTL过期后写 | 各节点当前股票列表 |
| `ttl_expiry_queue` | Heap[(expire_ts, nid, code)] | TTL检查时弹出 | 股票入池时插入 | TTL 过期队列。按过期时间排序的最小堆 |
| `dirty_stocks` | Set[code] | 通知公式引擎时传、比较层增量时用 | 批次开始时收集 / 批次结束时清空 | **股票级水位线**。本批次数据更新了的股票集合。 |
| `node_stock_change` | Dict[nid → {entered: Set, exited: Set}] | 执行循环读（增量处理） | propagate/TTL/备选池刷新时写入 | 从 bool 升级为 entered/exited 双集合 |
| `node_data_dirty` | Dict[nid → Set[code]] | 执行循环读、比较层增量读 | 批次开始时加入 / 批次结束时清空 | 节点里哪些股票的数据变了。 |
| `edge_compare_results` | Dict[eid → Dict[code → True/False/None]] | 集合运算层读 | 比较层写（增量更新） | **三态比较结果**。None=数据不足。新入池股票先设 None，再计算 |
| `edge_filter_results` | Dict[eid → Set[code] 或 List[code]] | propagate 读 | 集合运算层写 | 排名型用有序列表，独立型用 Set。只放通过的，None/False 都不在 |
| `batch_state` | dict | 批次处理时读批次标识 | 批次开始时更新批次号 | **批次状态**：批次号、批次时间、脏股票集合。注意：这是批次标识，不是全局版本号 |

**v1.10 相比 v1.9 的变化：**

| v1.9 | v1.10 | 变化原因 |
|------|-------|---------|
| `batch_state` 是版本屏障核心 | `batch_state` 是批次标识，版本屏障是单线程事件循环天然提供的 | 澄清版本屏障的真正含义 |
| 全局一个 batch_version | 每层有自己的一致性边界 | 分层一致性，粒度更细 |
| 比较层 entered 直接计算结果 | 比较层 entered 先设 None，再计算 | 修复 bug：保守估计，不瞎判 |
| 三态只在比较层 | 三态完整传播到各层 | 明确 AND/OR/NOT 三态运算规则，排名型数据不足不参与排名 |

**净变化：9张 → 9张（数量不变，语义澄清和 bug 修复）。**

### 6.2 公式引擎内部表（黑盒，股票池引擎不直接访问）

| 表名 | 类型 | 说明 |
|------|------|------|
| `formula_registry` | Dict[formula_id → CompiledFormula] | 公式注册表（通过 register_formula 注入） |
| `indicator_results` | Dict[(formula_id, period, args_key) → Dict[code → value 或 None]] | 指标值缓存（第一层状态表）。数据不足时 value 为 None |
| `data_cache` | Dict[(code, period) → DataFrame] | K线数据缓存 |

**这些都是公式引擎内部的事，股票池引擎完全不需要知道。**

### 6.3 编译期表（不变）

编译期产物和 v1.9 一样，没有变化：

| 编译产物 | 类型 | 说明 |
|----------|------|------|
| `formula_registry` | Dict[indicator_id → formula_spec] | 公式注册表。所有去重后的指标，编译期一次性收集 |
| `comparison_operators` | Dict[op_id → operator_spec] | 比较算子集。所有可用的比较算子，独立于指标 |
| `edge_indicator_refs` | Dict[eid → List[indicator_id]] | 每条边引用的指标ID列表 |
| `edge_compare_spec` | Dict[eid → compare_spec] | 比较层规格（用哪个算子、参数是什么） |
| `edge_set_op_spec` | Dict[eid → set_op_spec] | 集合运算层规格（AND/OR/NOT/排名逻辑，支持三态运算） |
| `edge_filter_type` | Dict[eid → 'independent' / 'global'] | filter 类型：单股独立型 / 全局依赖型 |
| `edge_timer_specs` | Dict[eid → timer_spec] | 每条边的定时器配置（编译期分析时间触发条件） |
| `batch_config` | dict | **v1.10 新增**。批次配置：max_wait_ms、max_size、min_size |

---

## 七、核心循环伪代码（v1.10 更新版）

### 7.1 事件循环（真正的事件驱动，批次是实现细节）

```python
# ============================================================
#  v1.10 核心循环伪代码（事件驱动 + 状态机 + 向量化优化）
# ============================================================

# --- 初始化 ---
formula_engine = PythonFormulaEngine()
formula_engine.set_data_provider(data_provider)

# 编译期：注册所有公式
for fdef in formula_registry.values():
    formula_engine.register_formula(fdef)

formula_engine.start()

# 编译期：注册所有定时器
for eid, timer_spec in edge_timer_specs.items():
    register_timer(eid, timer_spec)

# 批次配置（性能优化参数，不是架构核心）
batch_config = {
    "max_wait_ms": 100,   # 最多等 100ms
    "max_size": 1000,     # 最多攒 1000 条
    "min_size": 1,        # 最少 1 条就可以处理
}

# --- 事件循环 ---
running = True
paused = False
pending_ticks = []  # 攒批缓冲（数据事件专用，定时器/控制事件不攒）

while running:
    # 1. 等待事件（控制事件 > 定时器事件 > 数据事件）
    event = wait_for_event(
        priority_order=["control", "timer", "data"],
        batch_config=batch_config,  # 数据事件攒批配置
    )
    
    # 2. 控制事件（最高优先级）
    if event.type == "control":
        if event.action == "start":
            paused = False
            resume_all_timers()
        elif event.action == "pause":
            paused = True
            pause_all_timers()
        elif event.action == "resume":
            paused = False
            resume_all_timers()
        elif event.action == "stop":
            running = False
            cancel_all_timers()
            formula_engine.stop()
            break
        elif event.action == "config_reload":
            reload_config()
        continue
    
    if paused:
        continue  # 暂停状态下，不处理数据和定时器事件
    
    # 3. 定时器事件（次高优先级，精确到点，不攒批）
    if event.type == "edge_timer":
        handle_edge_timer_event(event)
        continue
    
    # 4. 数据事件（最低优先级，攒批处理）
    if event.type == "tick_batch":
        handle_tick_batch(event.ticks)
        continue
```

### 7.2 批次处理（数据事件，性能优化）

```python
def handle_tick_batch(ticks):
    """处理一批 tick 数据（批次化是性能优化，不是架构核心）"""
    
    # === 阶段 1：数据更新（集中完成） ===
    # 注意：单线程事件循环天然提供版本屏障，计算阶段数据不会变
    
    # 1.1 批量更新 latest_tick
    dirty_stocks.clear()
    for code, new_bar in ticks:
        latest_tick[code] = new_bar
        dirty_stocks.add(code)
    
    # 1.2 更新 node_data_dirty（每个包含这些股票的节点）
    for code in dirty_stocks:
        for nid in code_nodes[code]:
            if nid not in node_data_dirty:
                node_data_dirty[nid] = set()
            node_data_dirty[nid].add(code)
    
    # 1.3 通知公式引擎（数据更新了，缓存可能失效）
    formula_engine.on_data_updated(dirty_stocks)
    
    # === 阶段 2：TTL 过期检查 ===
    handle_ttl_expiry(time.time())
    
    # === 阶段 3：按拓扑序处理脏节点（计算阶段） ===
    # 单线程事件循环保证：计算过程中不会有新数据进来
    # 所以所有读取都是一致的
    
    # 收集所有需要处理的节点（有 stock_change 或 data_dirty 的）
    dirty_nodes = collect_dirty_nodes()
    
    # 按拓扑序处理（从上到下或从下到上，取决于传播方向）
    for nid in topological_sort(dirty_nodes):
        process_node(nid)
    
    # === 阶段 4：批次结束（清理） ===
    # 4.1 清理 dirty_stocks
    dirty_stocks.clear()
    
    # 4.2 清理各节点的 data_dirty
    for nid in node_data_dirty:
        node_data_dirty[nid].clear()
    
    # 4.3 注意：node_stock_change 不清理
    # 因为下一轮可能还需要知道本轮的变化（比如级联传播）
    # 下一轮 propagate 时会自动叠加
```

### 7.3 节点处理（增量计算）

```python
def process_node(nid):
    """处理一个脏节点的所有出边（增量处理）"""
    # 读取该节点的变化情况
    if nid not in node_stock_change:
        node_stock_change[nid] = {"entered": set(), "exited": set()}
    change = node_stock_change[nid]
    entered = change["entered"]
    exited = change["exited"]
    data_dirty = node_data_dirty.get(nid, set())
    
    # 没有变化？跳过
    if not entered and not exited and not data_dirty:
        return
    
    source_codes = set(node_stocks[nid])
    
    # 遍历该节点的所有出边（按顺序）
    for eid in sorted(out_edges[nid], key=edge_order):
        process_edge(eid, nid, source_codes, entered, exited, data_dirty)
    
    # 处理完该节点，重置 stock_change（注意：目标节点的变化会在 propagate 中设置）
    node_stock_change[nid] = {"entered": set(), "exited": set()}
    if nid in node_data_dirty:
        node_data_dirty[nid].clear()
```

### 7.4 边处理（三层 filter + propagate，修复 entered 处理 bug）

```python
def process_edge(eid, sid, source_codes, entered, exited, data_dirty):
    """处理一条边：三层 filter + propagate（全增量）
    v1.10 修复：比较层 entered 先设 None，再计算
    """
    edge = edges[eid]
    tid = edge.target_id
    filter_type = edge_filter_type[eid]
    
    # 检查时间条件（如果该边有时间触发）
    if edge_has_timing(eid):
        if not edge_timing_should_fire_now(eid):
            return  # 时间没到，跳过
    
    # === 第一层：指标计算（调用公式引擎，向量化，CPU 密集） ===
    indicator_ids = edge_indicator_refs[eid]
    period = edge_period(eid)
    args_map = edge_args_map(eid)
    
    # 需要重算指标的股票：新入池的 + 数据变了的
    codes_to_eval = entered | data_dirty
    codes_to_eval &= source_codes  # 确保在源节点里
    
    if codes_to_eval:
        # 公式引擎向量化计算（CPU 密集，底层 C 实现，快）
        # 只重算需要的，其他读缓存
        indicator_values = formula_engine.eval_indicators(
            formula_ids=indicator_ids,
            codes=list(codes_to_eval),
            period=period,
            args_map=args_map,
        )
    else:
        indicator_values = {}
    
    # === 第二层：比较判断（独立型 filter 才有，轻量） ===
    compare_spec = edge_compare_spec.get(eid)
    
    if compare_spec is not None and filter_type == 'independent':
        if eid not in edge_compare_results:
            edge_compare_results[eid] = {}
        
        # ---- v1.10 修复：entered 先设 None ----
        # 1. 新入池的：先初始化为 None（数据不足，保守估计）
        for code in entered:
            edge_compare_results[eid][code] = None
        
        # 2. 有指标值的：计算比较结果（覆盖 None）
        #    （entered 和 data_dirty 里有指标值的都算）
        codes_with_indicator = set()
        for ind_id in indicator_ids:
            codes_with_indicator |= set(indicator_values.get(ind_id, {}).keys())
        
        codes_to_compare = (entered | data_dirty) & codes_with_indicator & source_codes
        
        for code in codes_to_compare:
            result = do_compare(indicator_values, code, compare_spec)
            edge_compare_results[eid][code] = result
        
        # 3. 出池的：清理比较结果
        for code in exited:
            edge_compare_results[eid].pop(code, None)
        # ---- v1.10 修复结束 ----
    
    # === 第三层：集合运算（轻量） ===
    if filter_type == 'independent':
        # 独立型：基于比较结果，增量更新集合
        if eid not in edge_filter_results:
            edge_filter_results[eid] = set()
        
        filter_set = edge_filter_results[eid]
        
        # 处理变化的股票（entered + data_dirty + exited）
        changed_codes = entered | data_dirty | exited
        
        for code in changed_codes:
            if code in exited:
                # 出池的：从集合移除
                filter_set.discard(code)
            elif code in source_codes:
                # 在池里的：看比较结果
                # 三态处理：只有 True 的才加入，False 和 None 都不加入
                cmp_result = edge_compare_results[eid].get(code, None)
                if cmp_result is True:
                    filter_set.add(code)
                else:
                    filter_set.discard(code)
    
    else:
        # 排名型：只要有变化就全量重排
        if entered or exited or data_dirty:
            # 注意：排名需要所有股票的指标值
            # 所以调用 eval_indicators 传全部 source_codes
            all_indicators = formula_engine.eval_indicators(
                formula_ids=indicator_ids,
                codes=list(source_codes),
                period=period,
                args_map=args_map,
            )
            # 三态处理：数据不足（None）的股票不参与排名
            # 返回有序列表（按排名从高到低），只包含有有效数据的股票
            ranked = do_rank_filter(all_indicators, source_codes, edge_set_op_spec[eid])
            edge_filter_results[eid] = ranked
    
    # === propagate ===
    if filter_type == 'independent':
        new_target = edge_filter_results[eid]
    else:
        # 排名型：取前 N 名（如果有数量限制）
        new_target = set(edge_filter_results[eid][:rank_limit(eid)]) \
            if isinstance(edge_filter_results[eid], list) \
            else edge_filter_results[eid]
    
    old_target = set(node_stocks[tid]) if tid in node_stocks else set()
    
    new_entered = new_target - old_target
    new_exited = old_target - new_target
    
    if new_entered or new_exited:
        # 更新目标节点股票列表
        node_stocks[tid] = list(new_target)
        
        # 记录到目标节点的 stock_change
        if tid not in node_stock_change:
            node_stock_change[tid] = {"entered": set(), "exited": set()}
        node_stock_change[tid]["entered"] |= new_entered
        node_stock_change[tid]["exited"] |= new_exited
        
        # 标记目标节点为脏（会在后续拓扑序中处理）
        mark_node_dirty(tid)
        
        # 发射事件
        for code in new_entered:
            emit_event("stock_entered", tid, code)
        for code in new_exited:
            emit_event("stock_exited", tid, code)
```

### 7.5 三态逻辑运算辅助函数

```python
def tri_state_and(a, b):
    """三态 AND 运算：有 False 则 False，否则有 None 则 None，否则 True"""
    if a is False or b is False:
        return False
    if a is None or b is None:
        return None
    return True

def tri_state_or(a, b):
    """三态 OR 运算：有 True 则 True，否则有 None 则 None，否则 False"""
    if a is True or b is True:
        return True
    if a is None or b is None:
        return None
    return False

def tri_state_not(a):
    """三态 NOT 运算：True→False, False→True, None→None"""
    if a is None:
        return None
    return not a

def tri_state_combine(results, op='AND'):
    """组合多个三态结果（AND/OR）"""
    if not results:
        return None
    result = results[0]
    for r in results[1:]:
        if op == 'AND':
            result = tri_state_and(result, r)
        else:  # OR
            result = tri_state_or(result, r)
        # 短路优化：AND 遇到 False 就不用算了，OR 遇到 True 就不用算了
        if op == 'AND' and result is False:
            break
        if op == 'OR' and result is True:
            break
    return result
```

---

## 八、功能-表操作对应表（v1.10 更新版）

### 8.1 事件循环层

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **事件分发** | 事件队列 | — | 按优先级：控制 > 定时器 > 数据。数据事件攒批（性能优化） |
| **控制事件处理** | — | 运行状态 + 定时器状态 | start/pause/resume/stop/config_reload |
| **定时器事件处理** | `edge_filter_results` + `node_stocks` | `node_stocks` + `node_stock_change` | 时间到了就执行，复用缓存结果 |
| **数据事件攒批** | 数据队列 + `batch_config` | pending_ticks 缓冲 | **v1.10 澄清**：攒批是性能优化，不是架构核心 |

### 8.2 数据层（分层一致性，每层有自己的边界）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **批次开始（数据更新）** | 数据队列 | `latest_tick` + `dirty_stocks` + `node_data_dirty` | 批量更新数据，收集脏股票 |
| **通知公式引擎** | `dirty_stocks` | 公式引擎内部失效标记 | `formula_engine.on_data_updated(dirty_codes)` |
| **周期K线确认** | K线合成器 | `formula_engine.on_period_tick(period, codes)` | 不同周期的K线确认通知 |
| **指标计算（向量化）** | 公式引擎接口 `eval_indicators` | 公式引擎内部 `indicator_results` | **v1.10 澄清**：CPU 密集，向量化计算，批量算比逐条快 10-100 倍 |
| **数据层版本** | `latest_tick[code].ts` | — | **v1.10 澄清**：每只股票有自己的 tick 时间戳，不是全局 batch_version |

### 8.3 TTL 淘汰层

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| 股票入池记录 TTL | `edge_ttl_spec[eid]` | `ttl_expiry_queue` 插入 | `expire_ts = current_ts + ttl_sec` |
| TTL 过期检查 | `ttl_expiry_queue + current_ts` | 弹出过期项 | 最小堆：堆顶过期就弹出 |
| 过期股票移除 | `node_stocks[nid]` | `node_stocks[nid]` | 从节点移除 |
| **过期触发级联** | — | `node_stock_change[nid].exited.add(code)` | 加入 exited 集合，不是设 bool |

### 8.4 边触发判定层（节点脏驱动 + 时间触发）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **数据更新 → 节点数据脏** | `code_nodes[code]` | `node_data_dirty[nid].add(code)` | 批次开始时批量更新 |
| **节点股票变化 → entered/exited** | `node_stocks` 新旧对比 | `node_stock_change[nid].entered/exited` | 双集合，不是 bool |
| **边时间触发检查** | `edge_timer_specs[eid]` + 定时器 | `edge_timer_event` | 独立定时器事件源，不是顺便检查 |
| **三要素检查** | `node_stock_change[nid]` + `node_data_dirty[nid]` + 时间条件 | — | 时间条件 AND (有entered/exited OR 有data_dirty) |

### 8.5 边执行层（三层 filter + 增量处理 + propagate + 三态）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **filter 类型判断** | `edge_filter_type[eid]` | — | independent / global |
| **第一层：指标计算** | `formula_registry` + 公式引擎接口 | 公式引擎内部 `indicator_results` | 增量：只重算 entered ∪ data_dirty 的。**v1.10 澄清**：向量化，CPU 密集在公式引擎 |
| **第二层：比较判断** | 指标值 + `comparison_operators` + `edge_compare_spec[eid]` | `edge_compare_results[eid]` | **v1.10 修复**：entered 先设 None，再计算。三态：True/False/None |
| **第三层：集合运算（独立型）** | `edge_compare_results[eid]` + `edge_set_op_spec[eid]` | `edge_filter_results[eid]` | **v1.10 新增**：三态逻辑运算（AND/OR/NOT）。只有 True 的入集合，None/False 不入 |
| **第三层：集合运算（排名型）** | 指标值 + `edge_set_op_spec[eid]` | `edge_filter_results[eid]`（有序列表） | **v1.10 澄清**：数据不足的股票不参与排名 |
| **propagate** | `edge_filter_results[eid]` + `node_stocks[tid]` | `node_stocks[tid]` + `node_stock_change[tid]` | 计算 entered/exited，写入双集合。**v1.10 澄清**：None 的股票不入池（保守策略） |
| **入池初始化** | `node_stock_change[nid].entered` | 各状态表新增条目 | **v1.10 修复**：比较结果先设 None，再根据数据计算 |
| **出池清理** | `node_stock_change[nid].exited` | 各状态表删除条目 | 出池股票的所有状态清理 |

### 8.6 事件层（流式逐条产生）

| 功能 | 读什么表 | 写什么表 | 计算 | 时机 |
|------|---------|---------|------|------|
| 入池事件 | `node_stock_change[tid].entered` | `event_queue` | 直接读 entered 集合 | propagate 时立即发射 |
| 出池事件 | `node_stock_change[tid].exited` | `event_queue` | 直接读 exited 集合 | propagate 时立即发射 |
| 预警事件 | `alert_rules + node_stock_change` | `alert_queue` | 规则匹配 | propagate 后检查 |
| 交易信号 | `node_role[tid] == 'target' + entered/exited` | `signal_queue` | 角色判定 + 信号生成 | propagate 时生成 |

### 8.7 后处理层（PK排名/分析角度/看盘面板）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| PK排名 | `node_stocks[target] + pk_config` | `_pk_rankings` | 按权重评分排序，指标值通过公式引擎接口 |
| 分析角度 | `node_stocks[target] + analysis_config` | `_angle_results` | 多维度计算，指标值通过公式引擎接口 |
| 看盘面板 | `node_stocks + dashboard_schema` | `_dashboard_data` | 组装显示数据，指标值通过公式引擎接口 |

---

## 九、概念变化对照表（v1.9 → v1.10）

### 9.1 修正的概念

| v1.9 概念 | v1.10 修正 | 理由 |
|-----------|-----------|------|
| 批次化是架构核心 | **批次化是性能优化手段** | 架构核心是"事件驱动 + 状态机"，批次化只是攒批提升性能 |
| 全局一个 batch_version 做版本屏障 | **每层有自己的一致性边界** | 粒度太粗，不符合分层原则；单线程事件循环天然提供版本屏障 |
| 比较层 entered 直接计算结果 | **entered 先设 None，再计算** | 修复 bug：新入池股票可能数据不足，不能直接判 True/False |
| 三态只在比较层 | **三态完整传播到各层** | 明确 AND/OR/NOT 三态运算规则，排名型数据不足不参与排名 |
| 股票池引擎负责计算 | **股票池引擎 IO 密集，公式引擎 CPU 密集** | 职责分离：股票池引擎调度和状态管理，公式引擎向量化计算 |

### 9.2 新增的概念

| v1.10 新概念 | 说明 | 为什么加 |
|-------------|------|-----------|
| **向量化计算** | 公式引擎用 NumPy/pandas 向量化，底层 C 实现 | 解决单线程 CPU 密集的性能瓶颈 |
| **批次大小可调** | batch_config：max_wait_ms、max_size、min_size | 平衡延迟和吞吐，tick 少小批次低延迟，tick 多大批次高吞吐 |
| **三态逻辑运算** | tri_state_and / tri_state_or / tri_state_not | 类似 SQL NULL 语义，明确 None 的传播规则 |
| **分层一致性边界** | 数据层/指标层/比较层/集合运算层各有各的版本 | 澄清版本屏障的真正范围，不是全局一个版本号 |
| **保守入池策略** | 数据不足（None）的股票不入池 | 宁可漏掉不错入，股票池默认保守 |

### 9.3 概念数量统计

| 版本 | 核心运行时表数 | 变化 |
|------|---------------|------|
| v1.5 | 8 张 | — |
| v1.6 | 7 张 | -1 |
| v1.6.1 | 5 张 | -2 |
| v1.7 | 5 张 | 0（质量升级） |
| v1.8 | 8 张 | +3（状态显式化） |
| v1.9 | 9 张 | +1（+batch_state，升级3张表语义） |
| v1.10 | **9 张** | **0（数量不变，语义澄清 + bug 修复）** |

v1.10 核心运行时表（9张，数量不变，语义澄清）：
1. `latest_tick`（唯一真相源，每只股票自己的 tick 时间戳）
2. `node_stocks`（节点股票）
3. `ttl_expiry_queue`（TTL队列）
4. `dirty_stocks`（股票级水位线）
5. `node_stock_change`（entered/exited 双集合）
6. `node_data_dirty`（节点内数据脏的股票）
7. `edge_compare_results`（三态比较结果，entered 先设 None）← 修复 bug
8. `edge_filter_results`（集合/有序列表，只放通过的）
9. `batch_state`（批次标识，不是全局版本号）← 语义澄清

### 9.4 保留的正确设计（v1.9 做对的部分，v1.10 继续保留）

| 设计 | 说明 | 状态 |
|------|------|------|
| 指标是纯函数，数据不变则结果不变 | ✅ | 保留，最根本的洞察 |
| filter三层结构（指标→比较→集合运算） | ✅ | 保留，三态在各层传播 |
| 传播是边的步骤，不是filter层 | ✅ | 保留，职责分离 |
| 公式注册表 + 编译期指标去重 | ✅ | 保留 |
| 比较算子正交化（指标×算子=filter） | ✅ | 保留 |
| 无条件边立即传播 | ✅ | 保留 |
| 公式引擎内部维护指标缓存 | ✅ | **强化**：明确向量化计算，CPU 密集在公式引擎 |
| 股票级水位线 dirty_stocks | ✅ | 保留，批次级 |
| 节点脏驱动 | ✅ | 保留，entered/exited/data_dirty 三种 |
| 三层状态表 | ✅ | **强化**：三态完整传播，每层有自己的一致性边界 |
| 单线程事件循环 | ✅ | **强化**：明确是天然的版本屏障，不需要全局版本号 |
| 时间驱动独立化 | ✅ | 保留，定时器是独立事件源，不攒批 |

---

## 十、实现路线图（v1.10）

### 阶段一：并发模型澄清 + 向量化验证（P0）

1. **验证公式引擎的向量化实现**
   - 确认 formula_engine.py 中所有序列函数都是向量化的
   - 确认没有逐根 K 线的 Python 循环
   - 性能基准测试：5000 只股票 × 100 个指标，耗时多少

2. **明确股票池引擎与公式引擎的职责边界**
   - 股票池引擎：IO 密集，事件调度，状态管理，轻量逻辑
   - 公式引擎：CPU 密集，向量化计算，批量处理，缓存
   - 文档化：哪些计算在股票池引擎，哪些在公式引擎

3. **批次大小可调**
   - 新增 batch_config：max_wait_ms、max_size、min_size
   - 数据事件攒批逻辑使用此配置
   - 支持动态调整（运行时可以改）

### 阶段二：批次化降维 + 版本屏障澄清（P0）

1. **架构文档更新**
   - 明确架构核心是"事件驱动 + 状态机"
   - 批次化是性能优化手段，不是架构核心
   - 概念上每条 tick 都是独立事件，只是攒批处理

2. **版本屏障澄清**
   - 文档化每层的一致性边界
   - 数据层：latest_tick_ts（每只股票自己的版本）
   - 指标层：公式引擎内部缓存版本
   - 比较层：edge_compare_results 的隐式版本（dirty 标记）
   - 集合运算层：edge_filter_results 的隐式版本（dirty 标记）
   - 明确：单线程事件循环是天然的版本屏障

3. **batch_state 语义修正**
   - batch_state 是批次标识，不是全局版本号
   - 用于追踪批次，不用于版本控制
   - 版本控制是每层自己的事

### 阶段三：比较层 entered bug 修复（P0）

1. **修复 entered 处理逻辑**
   - 新入池股票：比较结果先设为 None
   - 然后根据当前指标值计算比较结果
   - 数据不足的保持 None

2. **出池清理逻辑确认**
   - 出池股票：清理比较结果
   - 清理过滤结果
   - 确保不会残留旧数据

3. **增量处理验证**
   - 验证 entered/exited/data_dirty 的增量处理正确
   - 增量结果与全量结果一致
   - 三态传播正确

### 阶段四：三态完整传播（P0）

1. **三态逻辑运算实现**
   - 实现 tri_state_and / tri_state_or / tri_state_not
   - 实现多条件组合（AND/OR 树）
   - 短路优化

2. **集合运算层三态处理**
   - 独立型：多个比较条件的 AND/OR/NOT 组合，支持三态
   - 只有 True 的才加入 edge_filter_results
   - False 和 None 都不加入，但 edge_compare_results 保留三态

3. **排名型三态处理**
   - 数据不足的股票不参与排名
   - 排名结果只包含有有效数据的股票
   - 有序列表

4. **propagate 层三态处理**
   - 确认保守策略：None 的股票不入池
   - 文档化为什么是保守策略
   - （可选）支持配置激进策略

### 阶段五：文档完善与验证（P1）

1. **更新所有相关文档**
   - 确保所有文档使用 v1.10 的概念
   - 不再使用"全局版本号"等错误概念
   - 三态传播规则明确

2. **正确性验证**
   - 三态逻辑运算的单元测试（覆盖所有组合）
   - entered 处理 bug 的回归测试
   - 增量计算与全量计算结果一致
   - 批次化结果与逐条处理结果一致

3. **性能验证**
   - 向量化计算的性能基准测试
   - 不同批次大小的延迟/吞吐对比
   - 确认单线程事件循环不会被阻塞太久

---

## 十一、统计总结（v1.9 → v1.10）

### 11.1 概念数量变化

| 统计项 | v1.9 | v1.10 | 变化 |
|--------|------|-------|------|
| 核心运行时表 | 9 张 | **9 张** | 0（数量不变，语义澄清 + bug 修复） |
| filter 层数 | 3 层 | 3 层 | 不变，但每层明确三态处理规则 |
| 公式引擎接口数 | 11 个 | **11 个** | 不变，但明确向量化计算的职责 |
| 脏来源种类 | 2 种（stock_change + data_change） | 2 种 | 不变，stock_change 是 entered/exited 双集合 |
| 显式状态表层数 | 3 层 | 3 层 | 不变，但三态完整传播 |
| 事件源数量 | 3 种（数据 + 定时器 + 控制） | 3 种 | 不变，但明确只有数据事件需要攒批 |
| 一致性保证 | 批次化 + 版本屏障 | **单线程事件循环天然一致** | 澄清：批次化是性能优化，一致性来自单线程 |
| 三态传播范围 | 比较层 | **全链路（指标→比较→集合→目标节点）** | 明确 AND/OR/NOT 运算规则，排名型数据不足不参与 |

### 11.2 为什么是 v1.10？

**v1.10 是 v1.9 的深度澄清和 bug 修复版本，主要解决"概念混淆"和"边界模糊"的问题。**

```
演进路径：
  v1.5 ~ v1.6：概念精简阶段（从多到少，先做对）
  v1.7：性能优化阶段（股票级水位线，增量计算）
  v1.8：状态显式化阶段（三层状态表，每层结果都有表）
  v1.9：架构完善阶段（一致性 + 真事件驱动 + 生命周期）
  v1.10：深度澄清阶段（并发性能 + 批次定位 + 三态传播）
  v2.0：完整稳定版（所有功能完善，文档齐全）
```

**v1.10 解决的是"认知层面"的问题：**
1. **并发深度**：明确单线程的性能瓶颈在哪里，用向量化怎么解决
2. **批次定位**：从"架构核心"降为"性能优化"，回归事件驱动本质
3. **版本屏障**：从"全局一个版本号"到"分层一致性边界"
4. **比较层 bug**：entered 先设 None，保守估计，不瞎判
5. **三态传播**：从"只在比较层"到"全链路传播"，明确运算规则

这些都是"认知级"的改进，不是加功能，但比加功能更重要——概念清晰了，实现才不会走偏。

### 11.3 一句话总结

**v1.10 澄清并发模型的性能代价与向量化应对方案（公式引擎 CPU 密集用 NumPy 向量化，股票池引擎 IO 密集用事件循环）；批次化从架构核心降为性能优化手段（事件驱动 + 状态机才是核心）；版本屏障澄清为分层一致性（每层有自己的边界，单线程事件循环是天然屏障）；修复比较层 entered 处理 bug（先设 None 再计算，保守估计）；明确三态（True/False/None）的完整传播路径和逻辑运算规则（类 SQL NULL 语义，AND/OR/NOT 各有规则，排名型数据不足不参与）。**
