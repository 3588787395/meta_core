# 统一股票池 OOP / 事件驱动架构 Spec

## Why

当前 `meta_core` 项目虽然已采用表驱动 + 编译期/运行期分离，并存在 `EventBus`，但存在三类结构性问题：

1. **DZH/TZH 功能未完全统一**：大智慧（DZH）与通达信（TDX）股票池的能力各成体系，节点/边/条件/时机/TTL/回调/交易/UI/导入导出 等大量相似功能分散实现，存在重复；同时各自独有能力（如 DZH `tradeattr` 19 字段、`reload` 5 模式、`attrtext` 6 类型、`ana/histana` 分析体系；TDX `spinfo` 8 类型、`nset×noperate` 矩阵、24 时机组合、`stk` 14 字段、6 拓扑模式）未在同一对象模型中显式建模。
2. **模块间直接引用，未以事件引擎为唯一中介**：`core/` 内部 17 处直接 import（如 `engine.py` 直接 import `runtime/compiler/formula/edge_executor/...`），且 `core → services` 存在 4 处跨层违规引用（`replay→storage/tq_adapter`、`simulator→minute_aggregator`、`formula_router→data/formula_cache/hqchart`、`engine→pool_validator/data_query/formula_cache/market_data_port`）。违反"所有模块之间禁止相互引用，只准与事件引擎交互"的硬约束。
3. **非纯 OOP/事件驱动**：现有类承担多个职责，事件类型仅 4 种（`DataChanged/Executed/DomainEvent/Signal`），不足以覆盖"数据库、数据源、最新 tick、K 线合成、公式计算、股票筛选、交易执行、交易统计、监控记录、文件导入导出"等全部模块的事件契约。

本 spec 通过最大化合并 DZH/TDX 全部功能（约 18+18=36 类能力）、最小化重复（统一为单一对象模型），并把所有模块改造为"仅订阅/发布 EventBus 事件、不持有其他模块引用"的纯事件驱动 OOP 架构，从根本上解决以上问题。

## What Changes

### 一、建立统一对象模型（OOP，最大化覆盖 + 最小化重复）

* **BREAKING** 新建 `core/domain/` 包，定义全套领域对象类层次（属性 + 方法 + 事件），覆盖 DZH/TDX 全部能力

* 节点类层次：`Node(ABC)` → `CandidatePoolNode` / `StatePoolNode` / `ConditionNode` / `DiscardPoolNode` / `DecorativeNode` / `ExecutionOrderNode` / `ContainerNode` / `TextLabelNode` / `FlowArrowNode`

* 边类层次：`Edge(ABC)` → `ConditionalEdge` / `UnconditionalEdge`；统一 DZH `attr=8192/8193` 与 TDX 源类型决定论

* 时机控制类：`TimingSpec`（统一 DZH `begin(0-7)+end(0-1)+begint+endt+interval` 与 TDX `starttype(0-7)×cxtype(0-2)×starttime×cxtime×jgtime`，归一为 24 种组合矩阵）

* 强弱筛选类：`FilterSpec` + `Evaluator(ABC)` 层次（`IndicatorEvaluator`/`ConditionFormulaEvaluator`/`ExpertSystemEvaluator`/`FinancialScalarEvaluator`/`MarketScalarEvaluator`/`SetOperationEvaluator`），统一 DZH 5 公式类别与 TDX 6 种 `nset × noperate` 矩阵

* TTL 类：`TTLSpec`（统一 DZH `deltype(0-4)+hold+endtime+delstocktype` 与 TDX `bdel/ndelnum/ndeltype`）

* 回调类：`ActionSpec`（统一 DZH `enter/exit/tradeattr/alert` 位标志 与 TDX `psatt` 14 字段）

* 流转模式类：`PropagateSpec`（统一 DZH 5 模式 attr 位标志 与 TDX `tran` 2 种 + `emptyps`）

* 备选范围类：`CandidateRange`（统一 DZH `attrtext` 6 类型 与 TDX `spinfo` 8 种 type）

* 重载调度类：`ReloadSchedule`（统一 DZH `reload` 5 模式 与 TDX spinfo 各 type 的缓存 TTL/刷新机制）

### 二、建立事件引擎为唯一通信中介（事件驱动，模块零引用）

* **BREAKING** 扩展 `EventBus` 事件类型从 4 种扩展到 **28 种**，覆盖全部模块契约

* **BREAKING** 所有模块（`core/`、`services/`、`converters/`、`api/`、`app.py`）禁止直接 import 其他业务模块；只准：

  * 订阅 `EventBus` 事件

  * 发布 `EventBus` 事件

  * 通过构造函数注入接口（`Protocol`/`ABC`），不注入具体实现类

* 删除 `core → services` 全部 4 处跨层 import，改为通过事件订阅 + 接口注入

* 删除 `core/` 内部 17 处直接 import，改为：

  * 引擎类（`PoolEngine`）通过 `_components` 容器持有组件，组件间不互引

  * 各组件只通过 `EventBus` 协作，不持有其他组件引用

* `app.py` 的 `lifespan` 依赖注入改为"事件布线器"模式：只负责创建各模块实例并注册到 `EventBus`，模块间不传递引用

### 三、模块化重构（16 个独立模块，零相互引用）

将现有 27 个 `core/` 模块 + 11 个 `services/` 模块重组为 16 个独立模块，每个模块只与 `EventBus` 交互：

1. **EventBus 模块**（`core/event_bus.py`）：唯一通信枢纽，28 种事件类型
2. **Domain 模块**（`core/domain/`）：统一 OOP 领域对象（节点/边/条件/时机/筛选/TTL/回调/流转/备选/重载）
3. **Config 模块**（`core/table_engine.py` + `config/`）：JSON 配置表加载 + 热加载
4. **Database 模块**（`services/storage.py`）：SQLite 持久化（18 张表），事件订阅 `Persistable` / 发布 `Persisted`
5. **DataSource 模块**（`services/providers/` + `services/data.py`）：数据源适配器 + 契约探测 + 备选池解析
6. **TickBar 模块**（`core/tick_source.py` + `core/data_updater.py` + `core/bar_composer.py` + `services/minute_aggregator.py`）：最新 tick + K 线合成
7. **Formula 模块**（`core/formula.py` + `core/formula_engine.py` + `core/formula_router.py` + `services/formula_cache.py`）：公式计算
8. **Screening 模块**（`core/evaluators.py` + `core/edge_executor.py:_filter()`）：股票筛选（强弱对比）
9. **Execution 模块**（`core/compiler.py` + `core/engine.py` + `core/edge_executor.py` + `core/time_util.py:EventDriver`）：编译 + 核心循环 + 边执行 + 时序驱动
10. **Trade 模块**（`core/trade_executor.py` + `services/trading_service.py`）：交易执行 + 模拟交易 + 历史记录
11. **Statistics 模块**（`core/engine.py:_post_tick()` + PK/分析角度）：交易统计 + 收益分析 + PK 排名
12. **Monitoring 模块**（`core/event_panel.py` + `core/snapshot_builder.py` + dashboard/alerts）：监控记录 + 浮窗 + 看盘面板 + 告警
13. **ImportExport 模块**（`converters/dzh.py` + `converters/tdx.py` + `converters/json_xml.py`）：DZH/TDX/JSON 三格式导入导出
14. **RuntimeMode 模块**（`core/runtime.py` 模式相关 + `core/replay.py` + `core/simulator.py`）：实盘/回放/仿真三模式
15. **HotReload 模块**（`services/hot_reload.py`）：配置热加载
16. **API 模块**（`api/` + `app.py`）：HTTP 路由 + WebSocket 推送 + lifespan 事件布线

### 四、事件契约定义（28 种事件类型）

| #  | 事件类型                | 发布者             | 订阅者                             | 载荷                        |
| -- | ------------------- | --------------- | ------------------------------- | ------------------------- |
| 1  | `ConfigLoaded`      | Config 模块       | Execution/Domain/HotReload      | config\_tables dict       |
| 2  | `ConfigChanged`     | HotReload 模块    | Config/Execution 模块             | changed\_tables list      |
| 3  | `PoolLoaded`        | ImportExport 模块 | Execution/Database 模块           | pool\_config dict         |
| 4  | `TickReceived`      | DataSource 模块   | TickBar 模块                      | tick\_data dict           |
| 5  | `DataChanged`       | TickBar 模块      | Execution/Formula/Monitoring 模块 | tick/bar dict             |
| 6  | `BarComposed`       | TickBar 模块      | Formula/Statistics 模块           | bar dict                  |
| 7  | `FormulaEvaluated`  | Formula 模块      | Screening/Statistics 模块         | result + formula\_ref     |
| 8  | `CrossOverDetected` | Formula 模块      | Screening 模块                    | code + type(golden/death) |
| 9  | `StockFiltered`     | Screening 模块    | Execution 模块                    | passed + rejected lists   |
| 10 | `EdgeFired`         | Execution 模块    | Monitoring 模块                   | edge\_id + ts             |
| 11 | `TransferExecuted`  | Execution 模块    | Trade/Database/Monitoring       | src→tgt + codes + mode    |
| 12 | `TTLExpired`        | Execution 模块    | Database/Monitoring 模块          | node\_id + codes          |
| 13 | `Signal`            | Execution 模块    | Trade 模块                        | BUY/SELL + code + qty     |
| 14 | `OrderPlaced`       | Trade 模块        | Database/Monitoring 模块          | order dict                |
| 15 | `OrderFilled`       | Trade 模块        | Statistics/Database 模块          | fill dict                 |
| 16 | `PositionUpdated`   | Trade 模块        | Statistics/Monitoring 模块        | tracker dict              |
| 17 | `StatisticsUpdated` | Statistics 模块   | Monitoring/API 模块               | stats dict                |
| 18 | `RankingChanged`    | Statistics 模块   | Monitoring 模块                   | rankings dict             |
| 19 | `AlertRaised`       | Monitoring 模块   | API/Database 模块                 | alert dict                |
| 20 | `SnapshotUpdated`   | Monitoring 模块   | API 模块                          | snapshot dict             |
| 21 | `EventLogged`       | Monitoring 模块   | Database 模块                     | event dict                |
| 22 | `ModeChanged`       | RuntimeMode 模块  | 所有模块                            | mode\_id                  |
| 23 | `TimeAdvanced`      | RuntimeMode 模块  | Execution 模块                    | ts                        |
| 24 | `ReplayStarted`     | RuntimeMode 模块  | Database/Monitoring 模块          | session dict              |
| 25 | `ReplayStep`        | RuntimeMode 模块  | Execution 模块                    | step dict                 |
| 26 | `SimulationStep`    | RuntimeMode 模块  | Execution 模块                    | step dict                 |
| 27 | `ImportStarted`     | ImportExport 模块 | Monitoring 模块                   | format + path             |
| 28 | `ExportCompleted`   | ImportExport 模块 | Monitoring 模块                   | format + path + count     |

### 五、模块依赖契约（强制：只准与 EventBus 交互）

**禁止行为**（任何模块违反即视为 bug）：

* `from core.xxx import Yyy`（除 `core.event_bus` 外）

* `from services.xxx import Yyy`（除在 `app.py` lifespan 装配处外）

* `from converters.xxx import Yyy`（除在 `app.py` lifespan 装配处外）

* 模块构造函数接收具体实现类（必须接收 `Protocol`/`ABC` 接口）

**允许行为**：

* `from core.event_bus import EventBus, Event` —— 唯一允许的跨模块 import

* `from core.domain import ...` —— Domain 模块作为纯数据模型，可被任何模块 import（只读，不含业务逻辑）

* `from core.schemas import ...` —— Pydantic 数据模型，同上

* 模块构造函数接收 `EventBus` 实例 + 配置 dict + 可选 `Protocol` 接口

**`app.py`** **lifespan 装配规则**：

```python
async def lifespan(app):
    bus = EventBus()
    db = Database(bus, config)
    data_source = DataSource(bus, config)
    tick_bar = TickBarModule(bus, config)
    formula = FormulaModule(bus, config)
    screening = ScreeningModule(bus, config)
    execution = ExecutionModule(bus, config)
    trade = TradeModule(bus, config)
    statistics = StatisticsModule(bus, config)
    monitoring = MonitoringModule(bus, config)
    import_export = ImportExportModule(bus, config)
    runtime_mode = RuntimeModeModule(bus, config)
    hot_reload = HotReloadModule(bus, config)
    api = ApiModule(bus, app)
    # 各模块在 __init__ 中订阅事件、发布事件；不持有其他模块引用
```

## Impact

* **Affected specs**:

  * `DESIGN0.md`（架构合同，§1 核心原则、§2 三种表、§3 引擎核心循环 需补充"事件驱动唯一中介"约束）

  * `DESIGN.md`（执行流映射，§4 变换单元、§17 平台级功能映射 需标注事件契约）

  * `.trae/specs/improve-ui-convenience/`（UI 便利性，依赖本架构的事件类型扩展）

  * `.trae/specs/verify-simulation-pipeline/`（仿真管线，依赖本架构的 RuntimeMode 模块事件化）

* **Affected code**（关键文件/系统）:

  * `core/`: 27 个模块全部重组为 16 个独立模块；`engine.py` 从 2000 行降为 ≤400 行（仅保留核心循环 + 事件订阅）

  * `core/event_bus.py`: 事件类型从 4 种扩展到 28 种

  * `core/domain/`: 新建包，约 15 个领域对象类

  * `services/`: 11 个模块重组进 16 模块体系，删除 4 处跨层 import

  * `converters/`: 3 个转换器重组为 ImportExport 模块

  * `api/` + `app.py`: 改为事件布线器模式

  * `config/`: 80+ JSON 配置表保持不变（表驱动架构延续）

  * `data/meta.db`: 18 张表 schema 保持不变

* **Migration**: 渐进式迁移，每完成一个模块的事件化改造即回归测试；保留 `MetaEngine` 门面至所有调用方迁移完毕

## ADDED Requirements

### Requirement: 统一领域对象模型

系统 SHALL 提供一套完整的 OOP 领域对象类层次，覆盖 DZH 与 TDX 股票池的全部功能（最大化覆盖），并以单一真相源合并相似功能（最小化重复）。

#### Scenario: 节点类型完整覆盖

* **WHEN** 用户创建任意 DZH 节点（type=0/1/2/3/4/5/6/200/201/202/203）或 TDX 节点（type=0/1/2/3/7/8）

* **THEN** 系统 SHALL 实例化对应的 `Node` 子类（`CandidatePoolNode`/`StatePoolNode`/`ConditionNode`/`DiscardPoolNode`/`DecorativeNode`/`ExecutionOrderNode`/`ContainerNode`/`TextLabelNode`/`FlowArrowNode`），并保留原 type 数值作为 `legacy_type` 属性以兼容导入

#### Scenario: 边类型统一为两种

* **WHEN** 解析 DZH 边（attr=8192/8193/0/1/4096/20480）或 TDX 边（源 type=7/8/3）

* **THEN** 系统 SHALL 归一为 `ConditionalEdge`（源为备选池/状态池，有时间属性）或 `UnconditionalEdge`（源为条件节点，无时间属性）两种

#### Scenario: 时机控制矩阵统一

* **WHEN** 配置边时机（DZH `begin(0-7)+end(0-1)+begint+endt+interval` 或 TDX `starttype(0-7)×cxtype(0-2)×starttime×cxtime×jgtime`）

* **THEN** 系统 SHALL 归一为 `TimingSpec` 对象，支持 24 种 `starttype×cxtype` 组合，并自动换算 DZH/TDX 时间单位（DZH `begint` HHMMSS 与 TDX `starttimehms` HHMMSS 一致；DZH `interval` 秒 与 TDX `jgtime` 秒一致）

#### Scenario: 强弱筛选统一

* **WHEN** 配置转移条件（DZH 5 公式类别 或 TDX 6 种 `nset`）

* **THEN** 系统 SHALL 归一为 `FilterSpec + Evaluator` 子类组合：

  * DZH 技术指标 ↔ TDX nset=0 → `IndicatorEvaluator`

  * DZH 条件选股 ↔ TDX nset=1 → `ConditionFormulaEvaluator`

  * DZH 交易系统 ↔ TDX nset=2 → `ExpertSystemEvaluator`

  * DZH 基本面条件 ↔ TDX nset=3 → `FinancialScalarEvaluator`（30 项财务指标完整映射）

  * DZH 动态行情 ↔ TDX nset=4 → `MarketScalarEvaluator`（12 项行情字段完整映射）

  * DZH 板块成员 ↔ TDX nset=5 → `SetOperationEvaluator`（并/差/交）

#### Scenario: TTL 删除时间统一

* **WHEN** 配置状态池 TTL（DZH `deltype(0-4)+hold+endtime+delstocktype` 或 TDX `bdel+ndelnum+ndeltype(0-3)`）

* **THEN** 系统 SHALL 归一为 `TTLSpec` 对象，统一单位映射（天/小时/分钟/秒/交易日），保留 DZH 独有的 `delstocktype`（相对/指定交易时间）与 `endtime` 编码（`3600×HH − 900×MM + SS`）

#### Scenario: 回调副作用统一

* **WHEN** 股票入池触发回调（DZH `enter/exit/tradeattr 19字段/alert 位标志` 或 TDX `psatt 14字段`）

* **THEN** 系统 SHALL 归一为 `ActionSpec` 对象，统一 6 种副作用（`bsavehis`/`bsound`/`btip`/`bsavetoblock`/`baimpool`/`bhighlight`），保留 DZH 独有的 `tradeattr` 19 字段精细交易控制与 TDX 独有的 `soundfile/nsyssound/bclearblock`

#### Scenario: 备选范围统一

* **WHEN** 配置备选池来源（DZH `attrtext` 6 类型 Tab 分隔字符串 或 TDX `spinfo.type` 0-7 整数）

* **THEN** 系统 SHALL 归一为 `CandidateRange` 对象，统一 8 种来源类型：

  * DZH 个股 ↔ TDX type=0 自设监控品种

  * DZH 市场 ↔ TDX type=2 所有A股 / type=5 板块指数 / type=6 ETF / type=7 可转债

  * DZH 自选组 ↔ TDX type=3 自选股

  * DZH 行业板块 ↔ TDX type=4 自定义板块

  * DZH 概念板块 ↔ TDX type=1 沪深300+中证500（扩展）

  * DZH 行业经典 ↔ 兼容保留

#### Scenario: 重载调度统一

* **WHEN** 配置备选池重载（DZH `reload` 5 模式 或 TDX spinfo 各 type 的缓存 TTL/刷新机制）

* **THEN** 系统 SHALL 归一为 `ReloadSchedule` 对象，支持 5 种模式：`never`/`on_file_load`/`on_startup`/`interval`/`daily_time`，并支持 TDX 各 type 的差异化 TTL（如 type=3 自选股 30 秒、type=4 板块 5 分钟、type=2 A股 1 小时）

### Requirement: 事件引擎为唯一通信中介

系统 SHALL 以 `EventBus` 作为所有模块间通信的唯一中介，禁止任何业务模块直接 import 或持有其他业务模块的引用。

#### Scenario: 模块零引用

* **WHEN** 检查任意业务模块（`core/`、`services/`、`converters/`）的 import 语句

* **THEN** 系统 SHALL 只允许以下 import：

  * `from core.event_bus import EventBus, Event`（及具体事件类）

  * `from core.domain import ...`（纯数据模型，无业务逻辑）

  * `from core.schemas import ...`（Pydantic 模型）

  * 标准库 + 第三方库（fastapi/pydantic/numpy 等）

* **AND** 禁止 `from core.xxx import Yyy`（除上述白名单）、`from services.xxx import Yyy`、`from converters.xxx import Yyy`

#### Scenario: 事件订阅与发布

* **WHEN** 模块需要在运行时获取其他模块的数据或触发其他模块的行为

* **THEN** 模块 SHALL 通过 `bus.subscribe(EventType, handler)` 订阅事件，通过 `bus.publish(Event(...))` 发布事件

* **AND** 模块构造函数 SHALL 仅接收 `EventBus` 实例 + 配置 dict + 可选 `Protocol` 接口（不接收具体实现类）

#### Scenario: 跨层数据流通过事件

* **WHEN** `core/execution` 需要写入数据库（`services/storage`）

* **THEN** Execution 模块 SHALL 发布 `TransferExecuted`/`TTLExpired`/`EventLogged` 事件，Database 模块 SHALL 订阅这些事件并写入持久表

* **AND** Execution 模块不持有 `Storage` 实例，不调用 `storage.insert_xxx()` 方法

#### Scenario: app.py 事件布线器

* **WHEN** 应用启动（`lifespan` 上下文管理器）

* **THEN** `app.py` SHALL 创建 `EventBus` 实例，依次实例化 16 个模块并注入 `EventBus`，各模块在 `__init__` 中完成事件订阅

* **AND** `app.py` 不向任何模块传递其他模块的引用

### Requirement: 28 种事件类型契约

系统 SHALL 定义并实现 28 种事件类型，覆盖配置/数据/公式/筛选/执行/交易/统计/监控/导入导出/运行模式 全部模块契约。

#### Scenario: 数据流事件链

* **WHEN** 一个 tick 到达并触发完整执行链

* **THEN** 系统 SHALL 按序发布以下事件：

  1. `TickReceived`（DataSource → TickBar）
  2. `DataChanged`（TickBar → Execution/Formula/Monitoring）
  3. `BarComposed`（TickBar → Formula/Statistics）
  4. `FormulaEvaluated`（Formula → Screening/Statistics）
  5. `StockFiltered`（Screening → Execution）
  6. `EdgeFired` + `TransferExecuted`（Execution → Trade/Database/Monitoring）
  7. `Signal`（Execution → Trade）
  8. `OrderPlaced` + `OrderFilled` + `PositionUpdated`（Trade → Statistics/Database/Monitoring）
  9. `StatisticsUpdated` + `RankingChanged`（Statistics → Monitoring/API）
  10. `AlertRaised` + `SnapshotUpdated` + `EventLogged`（Monitoring → API/Database）

#### Scenario: 运行模式切换事件

* **WHEN** 用户切换运行模式（实盘/回放/仿真）

* **THEN** RuntimeMode 模块 SHALL 发布 `ModeChanged` 事件，所有模块 SHALL 订阅并重置自身状态

* **AND** 回放模式 SHALL 额外发布 `ReplayStarted`/`ReplayStep` 事件，仿真模式 SHALL 额外发布 `SimulationStep` 事件

#### Scenario: 配置热加载事件

* **WHEN** `config/*.json` 文件被修改

* **THEN** HotReload 模块 SHALL 发布 `ConfigChanged` 事件，Config 模块 SHALL 订阅并重载配置表，Execution 模块 SHALL 订阅并重建 `CompiledSchedule`

### Requirement: 16 模块化重组

系统 SHALL 重组为 16 个独立模块，每个模块职责单一，只与 `EventBus` 交互。

#### Scenario: 模块职责单一

* **WHEN** 审计任意模块的代码

* **THEN** 该模块 SHALL 只承担一个职责（如 Database 只负责持久化、Formula 只负责公式计算、Trade 只负责交易执行）

* **AND** 模块内的类 SHALL 通过继承/组合实现多态，不通过 if/else 分支处理不同类型

#### Scenario: Execution 模块精简

* **WHEN** 重构 `core/engine.py`

* **THEN** `PoolEngine` 类 SHALL 精简为 ≤400 行，仅保留：核心循环（`run_tick()`）、事件订阅注册、`_components` 容器

* **AND** 原 `engine.py` 中的 tracker 更新、事件生成、post\_tick 流水线 SHALL 拆分到 Statistics/Monitoring 模块

#### Scenario: Domain 模块纯数据

* **WHEN** 审计 `core/domain/` 包

* **THEN** 领域对象类 SHALL 只包含数据属性 + 简单访问方法，不含业务逻辑、不发布事件、不订阅事件

* **AND** 领域对象 SHALL 可被任意模块 import（作为白名单例外）

### Requirement: 三格式导入导出统一

系统 SHALL 通过 ImportExport 模块统一 DZH XML / TDX XML / JSON 三种格式的导入导出，并发布事件通知其他模块。

#### Scenario: DZH XML 导入

* **WHEN** 用户上传 DZH XML 文件（GB2312 编码，`<pool type="ss-pool">` 根元素）

* **THEN** ImportExport 模块 SHALL 发布 `ImportStarted` 事件，解析为统一 `PoolConfig` 领域对象，发布 `PoolLoaded` 事件，最后发布 `ExportCompleted` 事件

* **AND** 解析 SHALL 覆盖 DZH 全部节点类型（type=0/1/2/3/4/5/6/200/201/202/203）、边类型（attr=0/1/4096/8192/8193/20480）、`tradeattr` 19 字段、`reload` 5 模式、`attrtext` 6 类型

#### Scenario: TDX XML 导入

* **WHEN** 用户上传 TDX XML 文件（GB2312 编码，`<pool>` 根元素）

* **THEN** ImportExport 模块 SHALL 解析为统一 `PoolConfig`，覆盖 TDX 全部节点类型（type=0/1/2/3/7/8）、边属性（14 字段）、`spinfo` 8 种 type、`psatt` 14 字段、`func` 16 字段、`stk` 14 字段

#### Scenario: 三格式交叉兼容

* **WHEN** 用户执行 DZH XML → JSON → DZH XML 往返

* **THEN** 系统 SHALL 保证节点数、边数、节点类型、边属性一致（roundtrip 100% 通过）

* **AND** 同理保证 TDX XML → JSON → TDX XML、JSON → DZH XML → JSON、JSON → TDX XML → JSON 往返一致

### Requirement: 模块高内聚与文件合并（Phase 7 新增）

系统 SHALL 保证每个聚合器模块是单一自包含文件，禁止子组件逻辑分散到多个文件。配置文件 SHALL 按模块分类到子目录，禁止平铺散乱。

#### Scenario: 聚合器模块单一入口

* **WHEN** 开发者需要修改 TickBar / Formula / Execution / Trade / Monitoring / RuntimeMode 模块的功能

* **THEN** 系统 SHALL 保证该模块所有类与逻辑位于单一文件 `<module>_module.py` 中

* **AND** 该模块的子组件文件（如 `tick_source.py` / `data_updater.py` / `bar_composer.py` / `formula.py` / `formula_engine.py` / `compiler.py` / `edge_executor.py` / `trade_executor.py` / `event_panel.py` / `snapshot_builder.py` / `replay.py` / `simulator.py`）SHALL 已被删除，内容已内联到聚合器模块

#### Scenario: 死代码与 thin re-export 清理

* **WHEN** 静态扫描 `core/` 目录

* **THEN** 系统 SHALL 不存在以下文件：`core/tick_source.py`（thin re-export）/ `core/evaluators.py`（重复）/ `core/_compat.py`（死代码）/ `core/_market_utils.py`（小工具）/ `core/value_extractor.py`（已合并）/ `core/ttl_helper.py`（已合并）/ `core/runtime.py`（已合并）

* **AND** 每个被删除文件的内容 SHALL 已迁移到合适的聚合器模块

#### Scenario: 大文件子模块保留

* **WHEN** 子组件文件 > 700 行（如 `converters/dzh.py` 3377 行、`converters/tdx.py` 3284 行、`converters/json_xml.py` 762 行）

* **THEN** 系统 SHALL 保留为独立子模块，由聚合器模块通过 import 调用

* **AND** 聚合器模块 `import_export_module.py` SHALL 作为单一入口封装子模块调用

#### Scenario: 配置文件分类整合

* **WHEN** 开发者浏览 `config/` 目录

* **THEN** 配置文件 SHALL 按模块分类到子目录：`architecture/` / `data/` / `runtime/` / `ui/` / `pools/` / `_archived/`

* **AND** `core/table_engine.py` SHALL 递归扫描子目录加载所有配置表

* **AND** 所有配置表 SHALL 仍能被正确加载

#### Scenario: 文件数量减少

* **WHEN** 统计 `core/` + `services/` + `converters/` 目录的 .py 文件数量

* **THEN** 文件总数 SHALL 从 \~57 减至 \~27（减少 \~50%）

* **AND** core/ 目录 SHALL 从 \~40 文件减至 \~15 文件（含 `__init__.py` / `engine.py` / `event_bus.py` / `schemas.py` / `table_engine.py` + 9 个聚合器模块 + `domain/` 子目录）

#### Scenario: 测试与静态检查保持通过

* **WHEN** 运行 `python scripts/check_module_imports.py`

* **THEN** 输出 SHALL 为 0 违规（exit 0）

* **AND** 运行 v4 测试套件（test\_v4\_\*.py）SHALL 88/88 通过

* **AND** 全套测试 failed 数 SHALL 等于基线 43（不增加）

## MODIFIED Requirements

### Requirement: 引擎核心循环（原 DESIGN0 §3）

\[原规范] `PoolEngine.run_tick()` 通过 `_components` 容器持有组件并直接调用方法。

\[修改为] `PoolEngine.run_tick()` SHALL 通过 `EventBus` 协调各模块：

1. 发布 `TimeAdvanced` 事件（RuntimeMode 模块订阅并推进时间）
2. 订阅 `DataChanged` 事件（TickBar 模块发布，触发核心循环）
3. 执行触发边时，发布 `EdgeFired` 事件，由 `EdgeExecutor`（独立模块）订阅执行
4. `EdgeExecutor` 执行完成后发布 `TransferExecuted` 事件，由 Trade/Database/Monitoring 模块订阅
5. `PoolEngine` 不再直接调用 `_update_trackers()`/`_emit_transfer_events()`/`_post_tick()`，这些逻辑 SHALL 由 Statistics/Monitoring 模块通过事件订阅实现

### Requirement: 三种运行模式（原 DESIGN0 §4）

\[原规范] `run_mode(mode_id)` 查 `runtime_modes.json` 初始化，直接设置 `PoolState` 四张表行。

\[修改为] RuntimeMode 模块 SHALL 在模式切换时发布 `ModeChanged` 事件，各模块订阅并重置自身状态：

* TickBar 模块订阅 → 切换数据源（live: tq\_dll/sdk/akshare；replay: kline\_cache；simulation: mock）

* Execution 模块订阅 → 切换时间源（wall\_clock/sequence/virtual）

* Trade 模块订阅 → 切换交易接口（live\_order/noop/paper\_trade）

* Database 模块订阅 → 切换副作用范围（all/readonly/optional）

* RuntimeMode 模块内部 SHALL 不持有其他模块引用，仅通过事件通知

### Requirement: 表驱动架构（原 DESIGN0 §1）

\[原规范] 引擎不含领域知识，只做读表→计算→写表。

\[修改为] 表驱动原则保持不变，但"读表→计算→写表"的每一步 SHALL 通过事件触发：

* 读表：模块在 `__init__` 时加载配置表，订阅 `ConfigChanged` 事件重载

* 计算：模块订阅上游事件，执行计算，发布下游事件

* 写表：模块发布 `Persistable` 事件，Database 模块订阅并写入持久表

## REMOVED Requirements

### Requirement: MetaEngine 门面直接委托

**Reason**: `MetaEngine` 作为兼容门面，直接持有 `_pool_engine`、`_trackers`、`_signal_queue` 等属性并代理到 `PoolState`/`EdgeState`，违反"模块零引用"原则。

**Migration**:

* 渐进式迁移：第一阶段保留 `MetaEngine` 作为 API 层适配器，仅注入 `EventBus`，不再直接持有 `PoolEngine` 引用

* 第二阶段：所有 API 端点改为通过 `EventBus` 发布请求事件、订阅响应事件，`MetaEngine` 完全删除

* 迁移期间 `MetaEngine.__getattr__` SHALL 仅代理 `capability_registry`，不代理运行时表

### Requirement: core 模块直接 import services

**Reason**: `core/replay.py` import `services.storage`/`services.tq_adapter`、`core/simulator.py` import `services.minute_aggregator`、`core/formula_router.py` import `services.data`/`services.formula_cache`/`services.providers.hqchart_provider`、`core/engine.py` import `services.pool_validator`/`services.data`/`services.formula_cache`/`services.market_data_port`，共 4 处跨层违规。

**Migration**:

* `replay.py` 改为订阅 `ReplayStarted` 事件，发布 `DataChanged` 事件由 TickBar 模块处理

* `simulator.py` 改为订阅 `SimulationStep` 事件，发布 `TickReceived` 事件由 TickBar 模块处理

* `formula_router.py` 改为订阅 `FormulaEvaluated` 事件，发布 `FormulaEvaluated` 事件（路由内部逻辑）

* `engine.py` 的 4 处 services import 改为通过事件订阅 + 接口注入

### Requirement: 极致文件合并（Phase 9 新增）

系统 SHALL 在 Phase 8 基础上进一步合并剩余的子目录包与脚本文件，将非测试 .py 文件数从 62 降至 ≤35，达成"高内聚、零分散"目标。

#### Scenario: core/domain/ 6 文件合并为单文件

* **WHEN** 统计 `core/domain/` 目录

* **THEN** 系统 SHALL 将 `base.py` / `nodes.py` / `edges.py` / `specs.py` / `evaluators.py` / `tick_source.py` 合并为单一文件 `core/domain.py`

* **AND** `core/domain/` 子目录 SHALL 完全删除（含 `__init__.py`）

* **AND** 所有 `from core.domain.base import` / `from core.domain.nodes import` / `from core.domain.edges import` / `from core.domain.specs import` / `from core.domain.evaluators import` / `from core.domain.tick_source import` / `from core.domain import` 引用 SHALL 更新为 `from core.domain import`（合并后单文件通过 `__all__` 导出全部符号）

#### Scenario: services/providers/ 6 文件合并为单文件

* **WHEN** 统计 `services/providers/` 目录

* **THEN** 系统 SHALL 将 `akshare_provider.py` / `dfcf_provider.py` / `hqchart_provider.py` / `local_file_provider.py` / `mock_provider.py` / `tq.py` 合并为单一文件 `services/providers.py`

* **AND** `services/providers/` 子目录 SHALL 完全删除（含 `__init__.py`，原 `_common.py` 已在 Phase 8 合并到 `__init__.py`）

* **AND** 所有 `from services.providers import` / `from services.providers.xxx import` / `from .providers import` / `from ..services.providers import` 引用 SHALL 更新为 `from services.providers import`（合并后单文件通过 `__all__` 导出全部 Provider 类）

#### Scenario: converters/ 3 文件合并为单文件

* **WHEN** 统计 `converters/` 目录

* **THEN** 系统 SHALL 将 `dzh.py` / `tdx.py` / `json_xml.py` 合并为单一文件 `converters.py`（位于 `meta_core/converters.py` 根，与 `core/` 同级）

* **AND** `converters/` 子目录 SHALL 完全删除（含 `__init__.py`，原 `_common.py` 已在 Phase 7 合并到 `core/import_export_module.py`）

* **AND** 所有 `from converters.dzh import` / `from converters.tdx import` / `from converters.json_xml import` / `from .converters import` 引用 SHALL 更新为 `from converters import`（合并后单文件通过 `__all__` 导出全部解析函数）

#### Scenario: api/ 2 文件合并为单文件

* **WHEN** 统计 `api/` 目录

* **THEN** 系统 SHALL 将 `pool_api.py` / `system_api.py` 合并为单一文件 `api.py`（位于 `meta_core/api.py` 根）

* **AND** `api/` 子目录 SHALL 完全删除（含 `__init__.py`）

* **AND** 所有 `from api.pool_api import` / `from api.system_api import` / `from .api import` 引用 SHALL 更新为 `from api import`（合并后单文件通过 `__all__` 导出全部路由注册函数）

#### Scenario: scripts/ 14 文件合并为 3 文件

* **WHEN** 统计 `scripts/` 目录

* **THEN** 系统 SHALL 将 14 个工具脚本合并为 3 个文件：

  * `scripts/dev_tools.py`：合并 `analyze_dzh.py` / `config_tools.py` / `decode_formulas.py` / `debug_formula.py` / `merge_config_tables.py` / `xml_tools.py` 6 个开发工具

  * `scripts/verify_tools.py`：合并 `e2e_verify.py` / `manual_mcp_verify.py` / `manual_mcp_verify_sim.py` / `run_sim_verify.py` / `import_target_pool_100.py` / `run_server.py` 6 个验证工具

  * `scripts/check_module_imports.py`：保留独立（静态检查工具，被多任务依赖）

* **AND** 所有命令行入口（如 `python scripts/run_server.py`）SHALL 改为 `python scripts/verify_tools.py run_server` 或类似子命令模式

#### Scenario: core/runtime.py 合并

* **WHEN** 检查 `core/runtime.py`（324 行）

* **THEN** 系统 SHALL 将其内容合并到 `core/runtime_mode_module.py`（如不产生循环依赖）或 `core/engine.py`

* **AND** `core/runtime.py` SHALL 被删除

#### Scenario: 空或仅含 docstring 的 __init__.py 清理

* **WHEN** 检查所有 `__init__.py` 文件

* **THEN** 若 `__init__.py` 为空或仅含 docstring/注释，且所在目录已合并为单文件，SHALL 删除该 `__init__.py` 与所在目录

* **AND** 含有实际 re-export 逻辑的 `__init__.py`（如 `services/providers/__init__.py` 已合并 `_common.py`）SHALL 在合并到单文件后删除

#### Scenario: 测试与静态检查保持通过

* **WHEN** 运行 `python scripts/check_module_imports.py`

* **THEN** 输出 SHALL 为 0 违规（exit 0）

* **AND** 运行 v4 测试套件 SHALL 17/17 通过

* **AND** 全套测试 failed 数 SHALL ≤ 158 + 13 errors 基线（不增加）

* **AND** 非测试 .py 文件数 SHALL ≤ 35（从 62 减少 ≥27 个）

### Requirement: 文档与代码极致精简（Phase 10 新增）

系统 SHALL 在 Phase 9 已将非测试 .py 文件降至 33 的基础上，进一步完成"必须完全清理垃圾代码、所有模块必须完备完整、禁止代码分散、无论代码还是配置、前端还是后端、必须解耦合高内聚"目标，**彻底清理冗余文档、过时规格、死代码、未引用配置、空目录**，达成"单一真相源 + 零垃圾代码"的最终状态。

#### Scenario: 根目录冗余文档归一

* **WHEN** 统计 `meta_core/` 根目录下的 `.md` 文件

* **THEN** 系统 SHALL 将以下历史归档文档**删除**（其内容已被 `DESIGN.md` 覆盖或已无参考价值）：

  * `ARCHITECTURE_REFACTOR.md`（重构历史）

  * `ARCHITECTURE_UNIFIED.md`（统一架构历史）

  * `OPTIMIZATION_HISTORY.md`（I1-I100 优化历史）

  * `SIMPLIFIED_EXECUTION.md`（执行简化历史）

  * `属性功能总表.md`（属性对照已并入 `DESIGN.md` 附录）

  * `TDX与DZH属性功能对照表.md`（同上）

* **AND** 系统 SHALL 将以下外部参考资料**移至** `docs/reference/` 目录（保留但归档）：

  * `红宝书8-公式系统(初级).md`（外部公式参考）

  * `DZH股票池完整技术文档.md`（DZH 软件功能参考）

  * `TDX股票池完整技术文档.md`（TDX 软件功能参考）

* **AND** 系统 SHALL **保留** `DESIGN.md`（架构单一真相源）+ `DESIGN0.md`（架构合同基线）

* **AND** 根目录 `.md` 文件数 SHALL 从 11 降至 ≤ 4（`DESIGN.md` / `DESIGN0.md` + 移至 `docs/reference/` 后保留的 README 类）

#### Scenario: specs/ 过时规格文档归并

* **WHEN** 检查 `meta_core/specs/` 目录

* **THEN** 系统 SHALL 识别以下问题：13 个 `.md` 文件（`INDEX.md` + `00-CONTRACT.md` 到 `11-CALLBACKS-SIGNALS.md`）引用的源文件路径（`compiler.py` / `edge_executor.py` / `runtime.py` / `converters/dzh.py` / `converters/tdx.py` 等）大多已在 Phase 7/8/9 中合并或删除，文档**已过时失真**

* **AND** 系统 SHALL 将 `specs/` 目录下 13 个文件的**有效内容**（架构契约 + 事件流 + TTL 规则等不变量）**合并**为单一文件 `docs/SPEC.md`，并更新所有源文件路径引用为 Phase 9 后的真实路径（如 `core/execution_module.py` / `core/runtime_mode_module.py` / `converters.py` 等）

* **AND** 系统 SHALL **删除**原 `specs/` 目录（含 `INDEX.md` + 12 个编号文档）

#### Scenario: 死代码检测与清理

* **WHEN** 对 Phase 9 合并后的大文件执行死代码扫描

* **THEN** 系统 SHALL 检查以下文件的未引用符号（函数 / 类 / 常量）：

  * `converters.py`（8708 行）—— grep 全项目无引用的 `def` / `class`

  * `services/providers.py`（8915 行）—— grep 全项目无引用的 `Provider` 子类

  * `api.py`（7032 行）—— grep 全项目无引用的路由注册函数

  * `core/domain.py`（1638 行）—— grep 全项目无引用的 `Spec` / `Evaluator` 子类

  * `core/runtime_mode_module.py`（2455 行）—— grep 全项目无引用的辅助函数

* **AND** 系统 SHALL **删除**所有未引用的死代码（函数定义 + 关联常量 + 注释块）

* **AND** 系统 SHALL **保留**以下情形的代码：

  * 通过 `__all__` 导出的公共 API

  * 被 `tests/` 测试用例引用的符号

  * 通过反射 / `getattr` 动态调用的符号

  * 通过 `Protocol` 接口约束的实现

#### Scenario: runtime/ 空目录清理

* **WHEN** 检查 `meta_core/runtime/` 目录

* **THEN** 系统 SHALL 识别 `runtime/__init__.py` 是向后兼容 shim，仅被 `simtests/` 目录引用（`simtests/conftest.py` / `simtests/harness/driver.py` / `simtests/harness/log_capture.py`）

* **AND** 系统 SHALL **更新** `simtests/` 中所有 `from meta_core.runtime.* import` / `meta_core.runtime.*` 引用为直接 `from meta_core.core.runtime_mode_module import` / `from meta_core.core.screening_module import`

* **AND** 系统 SHALL **删除** `meta_core/runtime/` 目录（含 `__init__.py`）

#### Scenario: 配置文件清理

* **WHEN** 检查 `meta_core/config/` 目录

* **THEN** 系统 SHALL **删除** `config/_archived/` 死目录（含 36 个归档 JSON，无代码加载）

* **AND** 系统 SHALL **扫描** `config/architecture/` / `config/data/` / `config/runtime/` / `config/ui/` 中所有 JSON，识别未被代码 `load_json` / `open` 引用的文件

* **AND** 系统 SHALL **删除**未引用的配置 JSON

* **AND** 系统 SHALL **保留**被 `core/table_engine.py` / `core/schemas.py` / `services/storage.py` 等模块通过路径加载的配置

#### Scenario: DESIGN.md 第 17 章重写

* **WHEN** 检查 `DESIGN.md` 第 17 章标题

* **THEN** 系统 SHALL 将标题从 "Phase 8 深度合并后的最终模块架构" **重写**为 "Phase 9 极致合并后的最终模块架构（33 文件）"

* **AND** 系统 SHALL 更新 17.1-17.4 表格内容为 Phase 9 后的真实结构：

  * `core/` 14 文件（含 `domain.py` 单文件，删除 `runtime.py` / `domain/` 子目录）

  * `services/` 5 文件（含 `providers.py` 单文件，删除 `providers/` 子目录）

  * `native/` 3 文件

  * 根目录 4 文件（`api.py` / `app.py` / `converters.py` / `__init__.py`）

  * `scripts/` 3 文件（`dev_tools.py` / `verify_tools.py` / `check_module_imports.py`）

  * `web/` 1 文件（`ui_renderer.py`）

  * `runtime/` 0 文件（待 Phase 10 删除）

  * `vendor/` 2 文件

* **AND** 系统 SHALL 更新 17.5 删除清单，追加 Phase 9 删除的 35 个文件

* **AND** 系统 SHALL 保留 17.6 通信契约不变（Phase 9 后仍有效）

#### Scenario: Phase 9 文档同步修正

* **WHEN** 检查 `tasks.md` 中 Phase 9 Task 29 子任务勾选状态

* **THEN** 系统 SHALL 将所有 9 个子任务（29.1-29.9）从 `[ ]` 改为 `[x]`（实际已完成）

* **AND** 检查 `checklist.md` 第十四节第 260 行声明 "DESIGN.md 第 17 章已更新为 Phase 9 极致合并后的最终架构"

* **THEN** 系统 SHALL **修正**该行为真实状态："待 Phase 10 SubTask 30.6 完成后更新"，并在 Phase 10 SubTask 30.6 完成后改回 `[x]`

#### Scenario: 最终验证（Phase 10 完成）

* **WHEN** 运行 `python scripts/check_module_imports.py`

* **THEN** 输出 SHALL 为 0 违规（exit 0）

* **AND** 运行 `python -m pytest tests/test_v4_integration.py -x --tb=short -q` SHALL 17/17 通过

* **AND** 运行 `python -m pytest tests/test_roundtrip_dzh.py tests/test_roundtrip_tdx.py` SHALL 4/4 通过

* **AND** 运行 `python -m pytest tests/ --collect-only -q` SHALL 0 ImportError

* **AND** 全套测试 failed 数 SHALL ≤ 158 + 13 errors 基线（不增加）

* **AND** 非测试 `.py` 文件数 SHALL ≤ 33（Phase 9 已达成，Phase 10 不增加）

* **AND** 根目录 `.md` 文件数 SHALL ≤ 4

* **AND** `config/` 目录无 `_archived/` 子目录

* **AND** `runtime/` 目录已删除

* **AND** `specs/` 目录已删除（内容合并到 `docs/SPEC.md`）

* **AND** `DESIGN.md` 第 17 章标题为 "Phase 9 极致合并后的最终模块架构（33 文件）"

### Requirement: 前端文件极致精简（Phase 11 新增）

系统 SHALL 在 Phase 10 后端清理完成的基础上，进一步将前端 `web/` 目录的分散文件（8 个 JS + 5 个 CSS + 3 个 HTML + 散乱样本）合并为高内聚的少量文件，达成"前端后端同等精简、零分散"目标。

#### Scenario: web/js/ 8 文件合并为 2-3 文件

* **WHEN** 统计 `web/js/` 目录

* **THEN** 系统 SHALL 将 8 个分散 JS 文件按职责合并为 2-3 个高内聚文件：

  * `web/js/app.js`：合并 `main.js`（应用入口）+ `data.js`（数据交互）+ `editor.js`（编辑器）—— 应用核心三件套

  * `web/js/ui.js`：合并 `panel.js`（面板）+ `toolbar-renderer.js`（工具栏）+ `event-panel.js`（事件浮窗）+ `formula-manager.js`（公式管理器）—— UI 组件四件套

  * `web/js/canvas.js`：保留独立（画布渲染逻辑独立，与 DOM 操作分离）

* **AND** 原 8 个 JS 文件 SHALL 全部删除

* **AND** `index.html` 中所有 `<script src="js/xxx.js">` 引用 SHALL 更新为新文件路径

* **AND** 合并后 JS 文件 SHALL 通过语法检查（`node --check` 或浏览器加载无错）

#### Scenario: web/css/ 5 文件合并为 1 文件

* **WHEN** 统计 `web/css/` 目录

* **THEN** 系统 SHALL 将 5 个分散 CSS 文件合并为单一 `web/css/styles.css`，按 `@layer` 分组：

  * `@layer base`：来自 `style.css`（基础样式）

  * `@layer components`：来自 `panel.css` / `event-panel.css` / `formula.css` / `config-center.css` / `table-driven-panel.css`

* **AND** 原 5 个 CSS 文件 SHALL 全部删除

* **AND** `index.html` / `config.html` / `formula.html` 中所有 `<link rel="stylesheet" href="css/xxx.css">` 引用 SHALL 更新为 `css/styles.css`

#### Scenario: web/ 根目录 3 HTML 合并为 1 个 index.html

* **WHEN** 统计 `web/` 根目录 HTML 文件

* **THEN** 系统 SHALL 将 `index.html` / `config.html` / `formula.html` 合并为单一 `web/index.html`，通过 hash 路由切换视图：

  * `#/` 或空 hash → 主页（原 `index.html` 内容）

  * `#/config` → 配置中心（原 `config.html` 内容）

  * `#/formula` → 公式管理（原 `formula.html` 内容）

* **AND** 合并后的 `index.html` SHALL 包含 hash 路由监听器与视图切换逻辑

* **AND** 原 `config.html` / `formula.html` SHALL 删除

#### Scenario: web/ 根目录样本文件归档

* **WHEN** 检查 `web/` 根目录下的样本文件

* **THEN** 系统 SHALL 识别以下样本文件（非前端运行所需）：

  * `cys.json` / `ultra7.json` / `ultra7_injected.json`（JSON 样本）

  * `panhou.xml` / `盘后.xml` / `超赢1号.xml` / `超赢7号.xml` / `金色两点半.xml`（XML 样本）

* **AND** 系统 SHALL 将这些样本文件**移至** `docs/samples/pools/` 目录（保留作为测试参考）

* **AND** 若代码或文档中有路径引用，SHALL 更新为新路径

#### Scenario: web/ 配置文件保留

* **WHEN** 检查 `web/` 根目录的配置文件

* **THEN** 系统 SHALL **保留**以下文件：

  * `package.json`（npm 配置）

  * `jest.config.js`（测试配置）

  * `ui_renderer.py`（前端渲染辅助 .py，已在 Phase 8 合并）

#### Scenario: 浏览器手动验证

* **WHEN** 用 MCP Playwright 打开 `http://localhost:<port>/` 验证前端

* **THEN** 系统 SHALL 验证以下功能正常：

  * 主页加载无 JS 错误（控制台 0 error）

  * Hash 路由 `#/config` / `#/formula` 切换正常

  * 节点/边/事件浮窗/公式管理器/工具栏等 UI 组件渲染正常

  * 画布交互（拖拽/缩放/选中等）正常

#### Scenario: 文件数量减少

* **WHEN** 统计 `web/` 目录文件总数

* **THEN** 文件数 SHALL 从 \~19（8 JS + 5 CSS + 3 HTML + 3 配置/Py）降至 ≤ 10（2-3 JS + 1 CSS + 1 HTML + 3 配置/Py）

* **AND** 样本文件 SHALL 移至 `docs/samples/pools/`

#### Scenario: 测试与静态检查保持通过

* **WHEN** 运行 `python scripts/check_module_imports.py`

* **THEN** 输出 SHALL 为 0 违规（exit 0）

* **AND** 运行 v4 测试套件 SHALL 17/17 通过

* **AND** 运行往返测试 SHALL 4/4 通过

* **AND** 非测试 `.py` 文件数 SHALL ≤ 33（不增加，前端合并不影响后端文件数）

#### Scenario: DESIGN.md 第 17 章更新

* **WHEN** Phase 11 完成后

* **THEN** 系统 SHALL 在 DESIGN.md 第 17 章 `web/` 表格更新为：

  * `web/index.html`（合并 3 HTML + hash 路由）

  * `web/js/app.js`（合并 main+data+editor）

  * `web/js/ui.js`（合并 panel+toolbar+event-panel+formula-manager）

  * `web/js/canvas.js`（保留）

  * `web/css/styles.css`（合并 5 CSS）

  * `web/ui_renderer.py`（保留）

  * `web/package.json` + `web/jest.config.js`（配置保留）

* **AND** 17.5 删除清单 SHALL 追加 Phase 11 删除的 12 个文件（8 JS + 5 CSS + 2 HTML = 15，扣除保留 1 个 canvas.js 后实际删除 14 个原文件，新文件 4 个）

