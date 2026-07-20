# MetaCore 架构设计文档

## 一、三层架构

系统采用 **事件驱动 + 时间驱动 + 表驱动** 三层混合架构：

| 层级 | 驱动方式 | 核心模块 | 职责 |
|------|---------|---------|------|
| **事件驱动层** | EventBus 发布/订阅 | event_bus, execution_module, monitoring_module | 模块解耦通信、边触发、事件记录与推送 |
| **时间驱动层** | 仿真时钟/Tick时钟 | runtime_mode_module, tick_bar_module | 时间推进、Tick生成、K线合成、TTL管理 |
| **表驱动层** | 数据表查询 | table_engine, formula_module | 指标表加载、公式计算、数据查询路由 |

## 二、模块职责表

| 模块 | 文件 | 核心类 | 职责 |
|------|------|--------|------|
| 事件总线 | event_bus.py | EventBus, Event | 模块间解耦通信的唯一通道 |
| 领域模型 | domain.py | TickData, BarData, EdgeContext等 | 核心数据结构定义 |
| Tick/K线 | tick_bar_module.py | TickBarManager | Tick接收、K线合成、周期管理 |
| 公式引擎 | formula_module.py | FormulaRouter, PythonFormulaEngine | MA/EMA/MACD/KDJ等指标计算 |
| 表引擎 | table_engine.py | TableRegistry, TableLoader | 指标表加载、缓存、查询 |
| 边执行 | execution_module.py | EdgeExecutor, FilterExecutor, TTLManager | 条件边触发、股票池转移、TTL过期 |
| 池引擎 | engine.py | PoolEngine, GlobalState | 股票池管理、全局状态维护 |
| 运行控制 | runtime_mode_module.py | RuntimeSimulator, SimTickSource, SimScheduler | 仿真/实盘模式控制、Tick源 |
| 监控 | monitoring_module.py | EventRecorder, SSEStreamer | 事件记录、SSE实时推送 |
| 交易 | trade_module.py | OrderManager | 信号转订单、持仓管理 |
| 筛选 | screening_module.py | StockScreener | 初始股票池筛选 |
| API模式 | schemas.py | 各类Pydantic Schema | HTTP API请求/响应定义 |

## 三、事件流图

```
                          ┌───────────────────────────────────────────────────────────┐
                          │                    EventBus (事件总线)                     │
                          └───────────────────────────────────────────────────────────┘
                                       ▲         ▲         ▲         ▲         ▲
         TickReceived                  │         │         │         │         │ Signal(BUY/SELL)
    ┌──────────────────┐               │         │         │         │         │    ┌──────────────┐
    │  SimTickSource   │──────────────►│         │         │         │         ├────┤  OrderManager│
    │  / LiveTickSource│DataChanged    │         │         │         │         │    │  (trade)     │
    └──────────────────┘(tick)         │         │         │         │         │    └──────────────┘
                                       ▼         │         │         │         │
                              ┌──────────────┐   │         │         │         │
                              │ TickBarManager│   │         │         │         │
                              │  (tick_bar)  │───┘         │         │         │
                              └──────────────┘DataChanged  │         │         │
                                       │      (bar)        │         │         │
                                       ▼                   │         │         │
                              ┌──────────────┐               │         │         │
                              │FormulaRouter │───────────────┘         │         │
                              │(formula)     │  FilteredStocks/        │         │
                              └──────────────┘  FormulaResult          │         │
                                       │                               │         │
                                       ▼                               ▼         │
                              ┌──────────────┐               ┌──────────────┐    │
                              │FilterExecutor│               │ EdgeExecutor │    │
                              │ (execution)  │──────────────►│ (execution)  │────┘
                              └──────────────┘  EdgeContext  └──────────────┘  Executed/
                                       ▲                               │         TransferExecuted
                                       │                               │
                                       │TTL Expired                    ▼
                              ┌──────────────┐               ┌──────────────┐
                              │  TTLManager  │◄──────────────┤  PoolEngine  │
                              │ (execution)  │               │   (engine)   │
                              └──────────────┘               └──────────────┘
```

**事件顺序：**
1. **Tick** → `TickReceived` → `DataChanged(source=tick)`
2. **Bar** → Tick累积合成K线 → `DataChanged(source=bar)`
3. **Formula** → K线更新触发公式计算 → `FilteredStocks`/条件满足
4. **Edge** → 条件边触发 → `EdgeFired` → 股票池转移 `Executed`
5. **Transfer** → 池转移执行 → C池满足交集条件
6. **Signal** → BUY信号发布 → `Signal(signal_type=BUY, volume=100)`
7. **Order** → （实盘）订单下达 → `OrderPlaced`/`OrderFilled`

## 四、仿真/实盘统一

仿真和实盘模式**共享95%以上代码**，唯一区别是 **TickSource 实现不同**：

| 维度 | 仿真模式 (Simulation) | 实盘模式 (Live) |
|------|---------------------|----------------|
| Tick来源 | SimTickSource（内置生成器，按固定间隔生成fzxxxxxx格式模拟Tick） | LiveTickSource（对接真实行情源） |
| 时间推进 | SimulationScheduler按仿真时间快进 | 真实 wall-clock 时间 |
| 订单成交 | 立即成交（不模拟撮合） | 真实订单路由到券商 |
| 其他模块 | 完全相同 | 完全相同 |

**切换方式：** RuntimeSimulator 通过配置 `mode="simulation"` 或 `mode="live"` 选择TickSource实现，其余模块（公式、边、池、监控）完全复用，无需修改。

## 五、模块通信原则

1. **仅通过 EventBus 通信**：模块间禁止直接 import 业务类实例
2. **依赖倒置**：高层模块依赖 Protocol 接口，不依赖具体实现
3. **事件不可变**：Event 对象发布后不可修改
4. **单向数据流**：Tick→Bar→Formula→Edge→Transfer→Signal 顺序执行
