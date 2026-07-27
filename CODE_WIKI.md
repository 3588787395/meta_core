# Code Wiki — MetaCore 股票池平台

> 本文档基于代码库实际结构、模块职责、关键类与函数说明、依赖关系以及运行方式等关键信息整理。  
> 一句话本质：**股票池 = 状态池流水线**。引擎不含领域字面量，只做 `读表 → 计算 → 写表`。

---

## 目录

1. [项目概览](#1-项目概览)
2. [整体架构](#2-整体架构)
3. [目录结构](#3-目录结构)
4. [核心模块职责](#4-核心模块职责)
   - 4.1 [应用入口层 (app.py)](#41-应用入口层-apppy)
   - 4.2 [API 路由层 (api.py)](#42-api-路由层-apipy)
   - 4.3 [核心层 (core/)](#43-核心层-core)
   - 4.4 [服务层 (services/)](#44-服务层-services)
   - 4.5 [原生命令层 (native/)](#45-原生命令层-native)
   - 4.6 [转换器层 (converters.py)](#46-转换器层-converterspy)
   - 4.7 [前端 (web/)](#47-前端-web)
5. [关键类与函数说明](#5-关键类与函数说明)
6. [事件总线与事件流](#6-事件总线与事件流)
7. [运行模式](#7-运行模式)
8. [配置系统](#8-配置系统)
9. [数据源与公式系统](#9-数据源与公式系统)
10. [依赖关系](#10-依赖关系)
11. [项目运行方式](#11-项目运行方式)

---

## 1. 项目概览

**MetaCore 股票池平台** 是一个基于表驱动架构 (table-driven) 的股票池可视化与执行平台，支持 **大智慧 (DZH)** 与 **通达信 (TDX)** 两种主流股票池 XML 格式的解析、编辑、执行与回放。系统采用三层混合架构（事件驱动 + 时间驱动 + 表驱动），将 13 个核心模块通过 EventBus 解耦，统一在 `live / replay / simulation` 三种运行模式下复用同一执行路径。

### 核心能力

- **双格式 XML 解析/导出**：DZH（10 种 cell 类型）+ TDX（6 种 cell 类型），bit-flag 属性编解码
- **图执行引擎**：`备选池 → 转移条件 → 状态池 → 转移条件 → 状态池 ...` 严格交替的有向图流水线
- **公式引擎**：纯 Python 实现 DZH/TDX 公式语法（ema/sma/cross/if/abs 等），HQChart C++ 引擎可选桥接
- **三模式一致性**：live / replay / simulation 仅 TickSource 与时间驱动不同，95%+ 代码共享
- **配置热重载**：所有业务规则集中在 `config/**/*.json`，文件 watchdog 监听 + 三层校验 + 原子替换
- **表驱动 UI**：22 种面板组件 + 13 种节点渲染器 + 5 种边策略，全部由 `config/ui/*.json` 驱动
- **持久化**：SQLite 存储 9 张统一表 + 5 张候选池表；INSERT-only 审计表

### 技术栈

| 层 | 技术 |
|---|---|
| 后端框架 | FastAPI + Starlette (ASGI) |
| 服务器 | Uvicorn（推断，未显式声明） |
| 数据校验 | Pydantic |
| 数据处理 | pandas + numpy + pyarrow (parquet) |
| 存储 | SQLite |
| 公式引擎 C++ 扩展 | HQChartPy2.pyd (CPython 3.13 ABI, Windows, 内嵌 OpenSSL 1.1) |
| 数据源 | TQ SDK / TDX DLL / HQChart / AKShare / DFCF / 本地文件 / Mock |
| 前端 | 原生 JS + 单 HTML (hash 路由)，Playwright 用于 E2E 测试 |

---

## 2. 整体架构

### 2.1 三层混合架构

```
┌─────────────────────────────────────────────────────────────┐
│                  FastAPI (app.py) — 装配层                  │
│  lifespan() 创建 EventBus，依次实例化 16 个模块             │
└─────────────────────────────┬───────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│  事件驱动层      │  │  时间驱动层      │  │  表驱动层       │
│  (EventBus)     │  │  (调度器)        │  │  (ConfigStore)  │
├─────────────────┤  ├─────────────────┤  ├─────────────────┤
│ execution_module│  │ runtime_mode    │  │ table_engine    │
│ monitoring_mod  │  │ tick_bar_module │  │ formula_module  │
│ event_bus       │  │                 │  │                 │
│ screening_mod   │  │                 │  │                 │
│ trade_module    │  │                 │  │                 │
└─────────────────┘  └─────────────────┘  └─────────────────┘
```

### 2.2 模块通信原则

1. **EventBus-only**：模块间不直接 import，仅通过 `core.event_bus` 通信
2. **依赖倒置**：通过 `typing.Protocol` 接口解耦（如 `IPoolValidator`、`IDataQuery`、`IFormulaCache`）
3. **不可变事件**：所有事件为 `@dataclass(frozen=False)` 但发布后视为不可变
4. **单向数据流**：`Tick → Bar → Formula → Edge → Transfer → Signal → Order`

### 2.3 关键不变式

- **变换单元 = (条件转移边 + 转移条件 + 无条件转移边) 三元组** —— 原子计算单元
- **触发不变式**：`triggered[eid] = edge_fired[eid] AND (dirty.nodes[sid] OR dirty.data)`
- **边类型由源节点类型决定**（已通过 602 DZH + 995+ TDX 边校验）
- **编译期 / 运行期分离**：`Compiler.compile()` 一次编译产生 `CompiledSchedule`（7 个 spec 字段），`PoolEngine.run_tick()` 每 tick 只做 `fire_due(now)` 扫描

### 2.4 反模式禁止清单

- 引擎中禁止出现股票代码 / 市场代码 / 指标字面量
- 禁止 `if type == "xxx"` 硬编码分支，必须改用 dispatch 表
- 禁止 `from xxx_native import yyy` 跨层导入
- 禁止 `eval()` 执行用户公式
- 禁止 `asyncio.sleep` 轮询

---

## 3. 目录结构

```
/workspace/
├── app.py                     # FastAPI 入口 + lifespan 装配层 (~130KB)
├── api.py                     # API 路由工厂 (7087 行)
├── converters.py              # DZH/TDX/JSON 格式转换
├── quick_test.py              # 仿真烟雾测试脚本
├── __init__.py                # 包初始化器 (懒加载 re-export)
│
├── core/                      # 核心层 — 14 个模块
│   ├── engine.py              # PoolEngine 统一引擎 (~2187 行)
│   ├── domain.py              # DZH/TDX 领域模型 (~2138 行)
│   ├── schemas.py             # Pydantic 模型 (~1275 行)
│   ├── event_bus.py           # EventBus + 30+ 事件类 (~605 行)
│   ├── execution_module.py    # Compiler + EdgeExecutor + EventDriver (~3663 行)
│   ├── formula_module.py      # 公式引擎 (~2868 行)
│   ├── tick_bar_module.py     # Tick/K线合成 (~1230 行)
│   ├── screening_module.py    # 选股筛选 (~1023 行)
│   ├── trade_module.py        # 交易执行 (~1225 行)
│   ├── monitoring_module.py   # 监控+统计 (~1489 行)
│   ├── import_export_module.py # 导入导出 (~500 行)
│   ├── runtime_mode_module.py # 三模式 + PoolState (~3280 行)
│   ├── table_engine.py        # ConfigStore + HotReload (~1714 行)
│   └── web_state.py           # 前端展示态格式化 (~392 行)
│
├── services/                  # 服务层
│   ├── data.py                # DataQuery + DataSourceContract (~6299 行)
│   ├── providers.py            # 8+ 数据源 provider (~8896 行)
│   ├── storage.py              # SQLite 持久化 (~2923 行)
│   └── tq_adapter.py           # TqAdapter 门面 (~701 行)
│
├── native/                    # 原生命令层
│   ├── builtins.py            # 命令式 pipeline 处理器 (~1583 行)
│   └── validators.py           # 三层校验 + 热重载 (~2588 行)
│
├── HQChartPy2/                # HQChart C++ 扩展 (Windows .pyd)
│   └── __init__.py            # 加载 .pyd + OpenSSL DLLs
│
├── config/                    # JSON 配置表 (表驱动核心)
│   ├── architecture/          # 架构类配置 (12 个表)
│   ├── data/                  # 数据源类配置 (24 个表)
│   ├── pools/                 # 池模板
│   ├── runtime/               # 运行时配置 (19 个表)
│   └── ui/                    # UI 配置 (16 个表)
│
├── web/                       # 前端单页应用
│   ├── index.html             # 入口 HTML
│   ├── js/                    # JavaScript 模块
│   ├── css/                   # 样式
│   ├── ui_renderer.py         # 服务端 UI 渲染辅助
│   ├── package.json           # 前端依赖 (jest + playwright)
│   └── jest.config.js
│
├── data/                      # 历史数据
│   ├── history/1d/*.parquet   # 日线 parquet
│   └── pools.db               # SQLite 数据库
│
├── dzhpool/                   # 大智慧股票池样例 (200+ XML)
├── tdxpool/                   # 通达信股票池样例 (60+ XML)
├── examples/                  # 示例池 JSON
├── doc/                       # 原始技术文档 + 截图
├── docs/                      # 设计文档
│   ├── DESIGN.md              # 设计文档 (旧版)
│   ├── SPEC.md                # 系统规范 (归并版)
│   ├── FEATURES.md            # 功能清单
│   ├── SYSTEM_REFERENCE.md   # 系统参考
│   └── reference/             # DZH/TDX 完整技术文档
├── tests/                     # 单元测试
├── simtests/                  # 仿真测试套件
└── scripts/                   # 开发工具
    ├── check_module_imports.py # 跨模块导入静态检查
    └── dev_tools.py            # CLI 开发工具集
```

---

## 4. 核心模块职责

### 4.1 应用入口层 (app.py)

**职责**：FastAPI 应用入口 + lifespan 装配层。

**关键内容**：
- `lifespan(app)` 异步上下文管理器：创建 `EventBus`，依次实例化 16 个模块，每个模块仅注入 `EventBus + 配置 dict`
- 启动 `HotReloadManager` watchdog 监听 `config/` 目录
- Legacy 保留：`PoolEngine` / `TqAdapter` / `Min1Aggregator` / `DataQueryService` / `DataSyncService` 供现有 API 路由调用
- 挂载所有路由器（`create_meta_router`、`create_execution_router` 等）
- CORS 中间件 + API Key 认证中间件（`X-API-Key` 头）
- 自动导入 `config/pools/*.json` 中的示例股票池到 Storage

**装配顺序**（lifespan 内）：

```
1. EventBus (模块间唯一通信中介)
2. ConfigStore (加载配置表)
3. Storage (SQLite 持久化)
4. CandidatePoolResolver + DataSourceContract
5. TickBarModule
6. FormulaModule (注入 _StateBackedDataQuery + HQChartProvider)
7. ScreeningModule
8. ExecutionModule (注入 PoolEngine 作为 meta_engine)
9. TradeModule
10. StatisticsModule
11. MonitoringModule
12. ImportExportModule
13. RuntimeModeModule
14. HotReloadManager (启动 watchdog)
15. Legacy: PoolEngine / TqAdapter / Min1Aggregator / DataQueryService / DataSyncService
16. API 装线 + 示例池导入
```

### 4.2 API 路由层 (api.py)

**职责**：FastAPI 路由工厂（7087 行），合并自原 `api/{__init__,pool_api,system_api}.py`。

**路由器清单**：

| 路由器工厂 / 实例 | 前缀 | 职责 |
|---|---|---|
| `create_meta_router()` | `/api/meta` | 元数据 + 备选池管理 (`/candidate-pool/*` 子路由) |
| `create_execution_router()` | `/api` | 池 CRUD + 执行 (`/pools`, `/pools/{id}/run`) |
| `create_dzh_router()` | `/api/dzh` | DZH XML 全生命周期（导入/导出/cells/flows/replay/formula） |
| `create_json_router()` | `/api/json` | JSON 池导入导出 |
| `create_replay_router()` | `/api/replay` | K 线回放会话 |
| `create_sim_router()` | `/api/sim` | 仿真会话 |
| `create_formula_router()` | `/api/formula` | 公式 CRUD + 测试 + 编解码 |
| `table_router` | `/api/v1/table` | 表驱动引擎（layouts/panel/rules/ownership） |
| `table_config_router` | `/api/config` | action_rules + handlers |
| `config_api_router` | `/api/config` | 配置 CRUD + 校验 + 热重载 + 历史 |
| `config_ws_router` | `/api/config` | 配置 WebSocket + 事件流 WebSocket |

**典型端点示例**：
- `POST /api/pools` 创建池
- `POST /api/pools/{pool_id}/run` 执行池
- `POST /api/dzh/import` 导入 DZH XML
- `POST /api/tdx/execute-pool` 执行 TDX 池
- `POST /api/replay/start` 启动回放
- `POST /api/sim/start` 启动仿真
- `WS /api/config/ws/events` 事件流订阅

### 4.3 核心层 (core/)

#### 4.3.1 `engine.py` — 统一引擎

**职责**：合并 `MetaEngine` + `PoolEngine` 的统一核心引擎。持有编译后的 schedule、`PoolState`、`EdgeExecutor`，运行事件驱动的 tick 循环。

**关键类**：
- `CompiledExpression` — 单表达式 AST 解析与缓存，安全求值（委托 `tdx_evaluators`，禁用 `eval`）
- `IPoolValidator` / `IDataQuery` / `IFormulaCache` / `IMarketDataPort` — `Protocol` 接口
- `PoolEngineMixin` — 引擎辅助方法集合（拓扑构建、节点股票初始化、tick 源构建）
- `PoolEngine(PoolEngineMixin)` — 统一引擎，持有 `_components` dict

**关键方法**：
- `PoolEngine.__init__()` — 懒加载配置，若传入 `pool_config` 立即调用 `_init_pool_runtime`
- `PoolEngine._init_pool_runtime(pool_config, subscribe_data_changed)` — 装配所有组件
- `PoolEngine._ensure_pool_engine(pool_config)` — create-or-reuse，pool_id 或边/节点签名变化时重建
- `PoolEngine._config_signature(pool_config)` (静态) — 边/节点签名 MD5
- `PoolEngine._inject_bars_history_getter()` — 注入仿真 bars-history 到 `FormulaEngine._data_query`
- `PoolEngine.__getattr__` — 代理 legacy `self.event_bus`、`self.schedule` 到 `_components`

#### 4.3.2 `domain.py` — DZH/TDX 统一领域模型

**职责**：DZH/TDX 统一 OOP 领域模型，合并自 6 个原文件。**仅标准库依赖**，可被任意模块安全导入。

**关键类**：
- `Node(ABC)` / `Edge(ABC)` — 抽象基类
- 11 个 Node 子类：`DecorativeNode`、`TextLabelNode`、`ContainerNode`、`StateColumnNode`、`DiscardPoolNode`、`ExecutionOrderNode`、`FlowArrowNode`、`StatePoolNode`、`ResultPoolNode`、`ConditionNode`、`CandidatePoolNode`
- `ConditionalEdge` / `UnconditionalEdge` — 边类型
- Specs：`TimingSpec`、`FilterSpec`、`PropagateSpec`、`ActionSpec`、`TTLSpec`、`CandidateRange`、`ReloadSchedule`
- Evaluators：`Evaluator(ABC)` + 6 个具体实现（对应 nset 0-5）
- `TimedEventSpec`、`TickSource(ABC)`、`RealTickSource`、`MockDataSource`
- `EdgeStateMixin` / `EdgeState` — 边级运行时表

**关键函数**：`all_dzh_types()`、`all_tdx_types()`、`evaluator_from_filter_spec()`、`time_at()`、`time_now_unix()`、`_stock_code()`、`_load_builtin_formulas()`、`_lookup_builtin_script()`

#### 4.3.3 `schemas.py` — Pydantic 模型

**职责**：DZH/TDX 整数属性 Pydantic 模型，动态生成自 `cell_type_registry.json`、`field_definitions.json` 等。失败时 `ConfigLoadError`。

**关键类**：`DynamicCellModel`（自动将 `attr` 整数展开为布尔子属性）、`DynamicFlowModel`、`Cell200AttrBitsModel` / `Cell201AttrBitsModel` / `Cell202AttrBitsModel`、TDX 系列 `TdxFuncModel` / `TdxPsattModel` / `TdxSpinfoModel` / `TdxStkModel` / `TdxCellModel` / `TdxFlowModel`

**关键函数**：`parse_cell(data)`（按 type 分派到 Cell 子类）、`_parse_attr_bits`、`_compose_attr_int`

#### 4.3.4 `event_bus.py` — EventBus

**职责**：统一事件总线，定义完整事件分类法 + `EventBus` 发布订阅器。

**事件分类**（~30 个 `@dataclass`）：
- **核心 4 类**：`DataChanged`、`Executed`、`DomainEvent`、`Signal`
- **扩展类**：`ConfigLoaded`、`ConfigChanged`、`PoolLoaded`、`TickReceived`、`BarComposed`、`FormulaEvaluated`、`CrossOverDetected`、`StockFiltered`、`EdgeFired`、`TransferExecuted`、`TTLExpired`、`TTLDue`、`TickDue`、`OrderPlaced`、`OrderFilled`、`PositionUpdated`、`StatisticsUpdated`、`RankingChanged`、`AlertRaised`、`SnapshotUpdated`、`EventLogged`、`ModeChanged`、`TimeAdvanced`、`ReplayStarted`、`ReplayStep`、`SimulationStep`、`SimulationStateChanged`、`ImportStarted`、`ExportCompleted`

**EventBus 方法**：
- `subscribe(event_type, handler)` — 按类或类型名字符串订阅
- `subscribe_any(handler)` — 订阅所有事件，返回 `unsubscribe` 回调
- `publish(event)` — 同步发布，追加到日志（`max_events` 上限），吞掉订阅者异常
- `get_events(event_type=None)` / `get_events_since(absolute_offset)` — 读取已发布事件
- `clear()` / `total_published` / `dropped_count` 属性

#### 4.3.5 `execution_module.py` — 编译器 + 边执行器 + 时间驱动

**职责**：核心执行模块，合并自 `compiler.py` + `edge_executor.py` + `time_util.py` + `edge_state.py`。订阅 `StockFiltered` / `DataChanged` / `TimeAdvanced` / `ConfigChanged` / `PoolLoaded`，运行 `gate → filter → propagate → callback → ttl` 五步管道，发布 `EdgeFired` / `TransferExecuted` / `TTLExpired` / `Signal`。

**关键类**：
- `EventDriver` — 统一时间驱动，单一 `heapq` 优先队列；`add_spec(spec, first_fire_time)`、`fire_due(now)`（同 fire_time 时 tick 优先于 edge）
- `EdgeContext`、`TimingSpec`、`FilterSpec`、`PropagateSpec`、`ActionSpec`、`TTLSpec`、`CompiledSchedule` — Pydantic spec 模型
- `Compiler` — 将 `pool_config` 编译为 `CompiledSchedule`（7 个 spec 字段）
- `EdgeExecutor` — 运行单条边：`run(eid, changed_codes=None)`、`_on_edge_fired(event)`
- `TickTable` — 绑定 `state.latest_tick` / `state.prev_tick`
- `TTLHelper` — TTL 记账

**模块函数**：`_now_ts(state)`、`_stock_entry_time(stock)`、`_lookup_edge_cond(pool_config, eid)`、`_action_baimpool(...)`、`_run_callback(...)`、`_publish(bus, event)`、`_gate_*` 系列、`_cxtype_*` 系列、`_eval_formula_path`、`_eval_scalar_path`、`_eval_set_op_path`、`_eval_intersection_path`、`_tgt_merge`、`_tgt_overwrite`、`_src_delete`、`_src_keep`

#### 4.3.6 `formula_module.py` — 公式引擎

**职责**：公式计算 + 金叉检测。合并自 `formula.py` + `formula_engine.py` + `formula_router.py` + `value_extractor.py`。

**关键类**：
- `_LRUCache` — 编译公式有界 LRU 缓存
- `_ExprParser` — DZH/TDX 风格公式 tokenizer/parser
- `CompiledFormula` — 单公式编译表示
- `PythonFormulaEngine` — 无状态纯 Python 求值器（`ema_op`、`sma_op`、`sar_op`、`cross_op`、`window_op`、`shift_op`、`if_op`、`abs_op`、`max_op`、`min_op`）
- `EvalContext` + 工厂 `live_context` / `replay_context` / `simulation_context`
- `FormulaEngine` — 有状态引擎，按 `(formula_ref, code, period, code_bar_hash)` 缓存结果
- `IDataQuery` / `IFormulaCache` / `IHQChartProvider` Protocol + `_InMemoryCache`
- `FormulaRouter` — 在 HQChart (C++) 与 Python 引擎间路由
- `ValueExtractor` — 从公式结果抽取标量
- `FormulaModule` — 公开 facade
- `FormulaCache` — 公式结果 TTL 缓存

**关键方法**：`FormulaEngine.eval(spec, codes, ctx)`、`eval_scalar(spec, codes, ctx, evaluator_fn)`、`_cached_eval(...)`；`PythonFormulaEngine.compile(formula)`、`.eval(...)`；`FormulaRouter.eval_batch(...)`；`ValueExtractor.extract(...)`

#### 4.3.7 `tick_bar_module.py` — Tick / K 线合成

**职责**：事件驱动 tick 接收 + K 线合成。合并 `data_updater.py` + `bar_composer.py` + `minute_aggregator.py`。

**关键类**：
- `DataUpdater` — 行情数据更新器；`bind(data_source)`、`apply_data(tick_data)` 返回是否有 code 推进
- `BarComposer` — 多周期 K 线合成器；订阅 `DataChanged(tick)`，发布 `DataChanged(bar)` 和 `BarComposed`
- `Tick` (NamedTuple)、`Min1Aggregator` — 1 分钟聚合器
- `_InternalState` — 内部状态持有者
- `TickBarModule` — 公开 facade，组合 TickSource + DataUpdater + BarComposer + Min1Aggregator

**关键函数**：`_bar_bucket_ts(ts, period)`、`_hash_bar(bar)`、`_new_bar_from_tick(tick, bucket_ts)`、`_merge_tick(bar, tick)`、`_compose_5m_from_1m(...)`、`make_bars_history_getter(state, periods=None)`（被 `PoolEngine._inject_bars_history_getter` 使用）

#### 4.3.8 `screening_module.py` — 选股筛选

**职责**：股票筛选（排名比较）+ TDX 条件求值器统一入口。订阅 `FormulaEvaluated`，运行 `nset × noperate` 矩阵，发布 `StockFiltered`。**表驱动**（`dispatch.json:nset_dispatch` + `tdx_noperate_rules.json`，无 if/elif 链）。

**关键类 `ScreeningModule`**：
- `_NSET_TO_EVALUATOR: Dict[int, Type[Evaluator]]` — 表驱动 nset → Evaluator 子类映射
- `_on_pool_loaded(event)` — 从每条边抽取 `formula_ref` 构建 `FilterSpec`
- `_on_formula_evaluated(event)`、`_evaluate_filter(...)`、`register_edge_filter(...)`、`_resolve_evaluator(...)`

**模块函数**：`eval_formula_nset(action_inputs, nset_cfg)` (nset 0/1/2)、`eval_scalar_nset(...)` (nset 3/4)、`eval_nset5_set_operation(...)` (nset 5)、`eval_tdx_condition(dispatch_key, action_inputs)`、`_filter_*` 系列、`_apply_noperate`、`_apply_noperate_mode`

#### 4.3.9 `trade_module.py` — 交易执行

**职责**：事件驱动交易执行 + 模拟交易 + 历史。合并 `trade_executor.py` + `trading_service.py`。订阅 `Signal`，执行 buy/sell（live_order/paper_trade/noop），发布 `OrderPlaced` / `OrderFilled` / `PositionUpdated`。支持 DZH `tradeattr` 19 字段控制 + TDX `psatt` 副作用。

**关键类**：
- `_TradeExecutor` — 向后兼容执行器
- `_Position` / `_TradeRecord` — dataclass
- `_PaperTradeEngine` — 模拟交易引擎，`freeze` / `unfreeze`
- `_HistoryManager` — 历史持久化
- `TradeModule` — 公开 facade
  - `_INTERFACE_HANDLERS = {"live_order": "_live_execute", "paper_trade": "_paper_execute", "noop": "_noop_execute"}` — 表驱动分派
  - 订阅：`Signal` / `OrderPlaced` / `OrderFilled` / `ModeChanged` / `TransferExecuted` / `DataChanged` / `TTLDue`

**模块函数**：`_load_json_cache`、`_get_stock_name`、`_code_to_market`、`_quote_filename`、`_read_history_log`、`_write_history_for_node`、`_save_pool_history`、`_dispatch_pool_enter_actions`、`_play_sound_alert`、`_show_popup_alert`、`_save_to_tdx_block`

#### 4.3.10 `monitoring_module.py` — 监控 + 统计

**职责**：事件驱动监控日志 + 浮动面板 + 看板 + 告警 + 交易统计。合并 `event_panel.py` + `snapshot_builder.py` + dashboard/alerts + `statistics_module.py`。

**关键类**：
- `_EventPanel` — 旧事件面板组件（私有，向后兼容）
- `_SnapshotBuilder` — 节点股票快照构建器
- `MonitoringModule` — 公开 facade，持有 `_event_panel` / `_snapshot_builder` / `_event_list` / `_pending_events` / `_node_snapshots` / `_alert_cooldown` / `_dashboard_data`
- `StatisticsModule` — 交易统计 + P&L + PK 排名 + 多角度分析；订阅 `PositionUpdated` / `BarComposed` / `StatisticsUpdated`，发布 `StatisticsUpdated` 和 `RankingChanged`

#### 4.3.11 `import_export_module.py` — 导入导出

**职责**：DZH XML / TDX XML / JSON 三格式导入导出。聚合原 `converters/{dzh,tdx,json_xml}.py`。HTTP 端点直接调用本模块（非事件），模块内发布 `ImportStarted` / `PoolLoaded` / `ExportCompleted`。

**关键类 `ImportExportModule`**：
- `import_dzh_xml(xml_path)` — DZH XML → 统一 `pool_config` dict
- `import_tdx_xml(xml_path)` — TDX XML → `pool_config`
- `import_json(json_path)` — JSON → `pool_config`

**模块函数**：`_safe_int`、`_safe_float`、`_extract_text_segments`、`_is_valid_formula`、`_extract_formula_from_binary`、`_decode_formula(indi_b64, ency=0)`

#### 4.3.12 `runtime_mode_module.py` — 运行模式 + PoolState

**职责**：live / replay / simulation 统一入口。合并 `replay.py` + `simulator.py`。发布 `ModeChanged` / `TimeAdvanced` / `ReplayStarted` / `ReplayStep` / `SimulationStep`。支持手动/自动步进 + 0.5x–20x 速度。

**关键类**：
- `MockStock`、`StatePool` — 仿真器辅助
- `KLineReplayEngine` — K 线回放引擎；持有 `_bars` / `_timeline` / `_current_index` / `_total_bars` / `_base_period` / `_playing` / `_paused` / `_speed` / `_pool_model` / `_pool_id` / `_snapshots` / `_synthesized_bars`
- `RuntimeSimulator` — 完整仿真器
- `RuntimeModeModule` — 模式切换 facade
- `DirtyState`、`StatePoolView`、`PoolStateMixin`、`PoolState(PoolStateMixin)` — 规范池运行时状态（15 张命名表，≤5 个公开属性强约束）

**关键方法**：
- `RuntimeModeModule.switch_mode(mode_id)` — 发布 `ModeChanged`
- `RuntimeModeModule.step_replay(...)` / `step_simulation(...)` — 发布 `ReplayStep` / `SimulationStep`
- `KLineReplayEngine.load_kline_data(...)`、`.step()`、`.play()`、`.pause()`、`.set_speed(...)`
- `RuntimeSimulator.step()`、`.run_auto(...)`、`.set_speed(...)`
- `PoolState.__init__(pool_config)` — 填充 15 张表 + 构建拓扑

#### 4.3.13 `table_engine.py` — 表驱动架构核心 + 热重载

**职责**：表驱动架构核心引擎 — 通用表解析/执行引擎，无领域知识。所有业务逻辑在配置表中，引擎只解析执行。同时承载热重载层（合并 `services/hot_reload.py`）。

**关键类**：
- `ConfigStore` — 配置表存储（加载/缓存/校验/热重载）；递归扫描 `config_dir` 的 `*.json`（跳过 `_archived/` 和 `.locks.json`）；`bus` 存在时订阅 `ConfigChanged` 重载
- `RuleEngine` — 从配置表执行规则
- `DataBinder` — 将数据绑定到规则上下文
- `PanelGenerator` — 从配置生成 UI 面板
- `PropertyOwnershipManager` — 属性归属元数据管理
- `HotReloadManager` — 文件监听 + 原子替换 + WebSocket 推送；快照初始 hash，文件变更时（防抖 1s）发布 `ConfigChanged`

#### 4.3.14 `web_state.py` — 前端展示态格式化

**职责**：将复杂计算收敛到后端，使 `event-panel.js` 只做渲染。覆盖：事件分类、时间戳归一化、仿真时间转换、TTL/fire_at 数学、计时器状态、触发类型、剩余时间、运行时显示时间。

**关键函数**（无类）：
- `classify_event_type(event_type)` — 事件 → 类别（`tick` / `bar` / `formula` / `edge` / `transfer` / `signal` / `order` / `ttl` / `system`）
- `format_event(ev)` — 面板用完整事件格式化
- `format_timer_queue(timers, now_ms)` — 计时器队列格式化
- `runtime_state(mode, now_ts, active_session_id)` — 生成 `/api/state/runtime` 响应
- `display_now_ms(mode, now_ts)` — 当前显示时间
- `normalize_display_ms(ts)`、`format_display_time(...)`、`format_sim_duration(...)`、`format_wall_time(...)`

### 4.4 服务层 (services/)

#### 4.4.1 `services/data.py` — 统一数据服务

**职责**：合并 `data_query.py` + `data_service.py` + `market_data_port.py`。

**关键类**：
- `DataQuery` — 本地 K 线只读查询（SQLite + parquet + Min1Aggregator 内存）
- `DataQueryService` — 服务层包装
- `KLineProvider` — K 线 provider 抽象
- `DataSourceContract` + 异常层级（`DataSourceContractError` / `DataSourceUnavailableErrorContract` / `DataSourceMockExplicitOnlyError`）— Task 11 契约强制
- `DataSyncService` — DB 同步服务
- `MarketDataPort(ABC)` + `TqAdapterMarketDataPort` — 公式引擎行情端口抽象
- `CandidatePoolResolver` — 候选池解析
- `CandidatePoolRefreshManager` — 刷新管理器

#### 4.4.2 `services/providers.py` — 数据源 provider

**职责**：合并 `services/providers/` 7 个文件，~8900 行，是项目最大文件。`DataSourceProvider` 抽象基类，`DataSourceManager` 动态从 `data_providers.json` 加载 provider 并维护 fallback 链。

**关键类**：
- `DataSourceProvider` — 抽象基类，默认空实现 + `_emit_tick` 发布 `TickReceived`
- `DataSourceManager` — 中心管理器，动态加载 + 降级链
- 8+ 具体 provider：
  - `MockProvider` — Mock 数据源
  - `DfcfProvider` — 东方财富
  - `HQChartProvider` + `PERIOD_ID` + `IHQDataImpl` + `FastHQChart` — HQChart DLL 桥接
  - `AkShareProvider` + `_RateLimiter` — akshare
  - `LocalFileProvider` — 本地文件
  - `TqDllProvider` / `TqSdkBridge` / `TqSdkProvider` / `TqProvider` — 通达信 TQ 系列
- `KLineDataCache` — K 线缓存
- `TqConnector` — 连接器

**共享工具**：`decode_formula`、`map_period`、`decode_sorttype`、`normalize_code`、`to_dzh_code`、`_format_timestamp`、`_format_hold_days`、`_norm_period`

#### 4.4.3 `services/storage.py` — SQLite 持久化

**职责**：SQLite 持久化层。仅做本地 SQLite CRUD，从不直接调用任何数据源。可选 `EventBus` 注入订阅 `TransferExecuted` / `TTLExpired` / `OrderPlaced` / `OrderFilled` / `EventLogged` / `ConfigChanged` / `ReplayStarted`，路由到 `insert_*` / `update_*` 方法。

**统一表**（9 张）：`pool_config` / `pool_node` / `pool_edge` / `node_state` / `stock_transfer_log` / `replay_session` / `replay_snapshot` / `kline_cache` / `config_version`

**候选池表**（5 张）：`stocks` / `sectors` / `sector_members` / `user_blocks` / `user_block_members`

**关键类**：
- `IStorageQuery(Protocol, @runtime_checkable)` — API 模块依赖注入用查询接口（~20 个查询方法）
- `safe_path_join(base, filename)` — 路径遍历安全 join
- `Storage` — 主存储类（所有表 CRUD；`bus` 时事件订阅）
- `DatabaseSyncService` — DB 同步服务（合并 `db_sync_service.py`）

**审计语义**：`stock_transfer_log` 和 `config_version` 表 INSERT-only

#### 4.4.4 `services/tq_adapter.py` — TqAdapter 门面

**职责**：薄门面，re-export `DataSourceManager` + `DataSourceProvider` + 契约类型，保留 legacy `TqAdapter` 接口。

**关键类 `TqAdapter(_TQFormatterMixin)`**：
- 构造 `(mock_mode=False, config=None, sdk_mode=None)`
- 持有 `_data_source_state` / `_mode_source` / `_explicit_source`
- **H2 修复**：永不静默回退到 mock；当无真实源时记录 `_data_source_state['status']='no_real_source'`
- 公开：`get_data_source_state()` / `get_available_sources()` / `set_active_source(name)` / `_probe(source_name, contract)`

**工厂**：`create_tq_adapter(mock_mode=False) -> TqAdapter`

### 4.5 原生命令层 (native/)

#### 4.5.1 `native/builtins.py` — 命令式 pipeline 处理器

**职责**：合并 `pipeline.py` + 原 `builtins.py`。提供历史命令式 pipeline 处理器（仍被新事件驱动模块调用）。

**处理器清单**：
- 初始化：`init_market_source`、`init_stock_state_pool`、`init_tdx_candidate`
- 池操作：`stock_pool_hold`、`transfer_condition_check`、`resolve_market`、`discard_sink_drop`、`time_trigger_check`、`profit_analysis_calc`、`formula_eval`、`sector_filter`、`cross_section_eval`、`basic_filter`、`pass_through`、`condition_dispatcher`、`candidate_resolve`、`accumulate_state`、`discard_stocks`
- 边处理：`edge_default_transfer`、`transfer_with_market_data_handler`、`log_transfer_handler`、`condition_dispatch_handler`
- 渲染：`render_label`、`render_shape`
- TDX：`tdx_condition_evaluator`、`tdx_convert_from_file`、`tdx_convert_from_pool`
- 报表构建：`_build_aggregate_report`、`_build_per_stock_report`、`_calc_mock_field`、`_calc_field_from_formula`、`_calc_aggregate_field_from_formula`
- Pipeline 步骤：`_step_resolve`、`_step_pass`、`_step_filter`、`_step_dzh_filter`、`_step_propagate`、`_step_transfer`、`_step_remove`、`_execute_action`、`_make_action`
- 刷新：`_filter_by_bar_data_handler`、`_apply_tq_snapshot`、`tq_snapshot_refresh`、`noop_refresh`、`mock_advance_refresh`

#### 4.5.2 `native/validators.py` — 三层校验 + 热重载

**职责**：合并 `schema_validator` / `config_validator` / `table_loader` / `matchers`。

**关键类**：
- `ValidationResult` — 单个校验结果
- `SyntaxValidator` (L1) — JSON 格式 / 必填字段 / 数据类型
- `LogicValidator` (L2) — 字段依赖 / 互斥 / 枚举合法性
- `BusinessValidator` (L3) — 归属一致性 / handler 引用 / type-map 完整性
- `SchemaValidator` — 编排三层校验
- `ConfigIntegrityValidator` — 全覆盖完整性校验（所有节点类型 × 属性）
- `TableLoader` — `config/` 热重载 watcher
- `TopologyPatternMatcher` — 拓扑模式匹配器

**关键函数**：`validate_configs(config_dir) -> Dict[str, Any]`（模块级便捷入口）、`should_fire(...)`（Flow/Edge 时机判断）

### 4.6 转换器层 (converters.py)

**职责**：DZH / TDX / Native 格式转换。

**关键 API**：

**DZH XML 解析 / 导出**：
- `parse_dzh_xml(xml_content, filename=None)` — DZH XML → 池配置
- `export_dzh_xml(config)`、`export_meta_to_dzh_xml_bytes(pool_config)`
- `DzhXmlExporter` 类 — 面向对象导出器
- `DZHPoolExecutor` 类 — DZH 池图执行器
- `NodeStateMachine` 类 — 节点状态机
- 运行时辅助：`execute_transfer`、`evaluate_condition`、`should_trigger`
- Cell 构建/解析家族：`_build_cell` / `_build_cell_*` / `_parse_cell_*`
- 字段导出家族：`_export_simple_field` / `_export_field_*`

**TDX XML 解析**：
- `is_tdx_format(xml_content)` — 格式嗅探
- `_decode_tdx_xml(raw_bytes)`、`detect_xml_version(root)`、`_parse_tdx_pos(pos_str)`
- `_load_tdx_element_schemas`、`_load_tdx_period_map`、`_parse_func_element`
- `_make_tdx_cell(cell_type, **data)`

**JSON 导入导出**（re-export 自 `core.import_export_module`）：`import_pool_from_json`、`export_pool_to_json`

**属性 / 标志 codec**：`decode_attr_flags`、`encode_attr_flags`、`decode_action`、`encode_action`、`decode_reload_mode`、`encode_reload_mode`、`_decode_type200_attr`、`_decode_type201_attr`、`_decode_flow_attr`、`_decode_enter_exit_action`

**映射 / 元数据**：`load_dzh_market_mappings`、`_lookup_market_mapping`、`get_cell_type_info`、`get_all_cell_types`、`parse_attrtext_triple`、`parse_attrtext_selections`、`build_attrtext_from_selections`

### 4.7 前端 (web/)

**职责**：单页应用，**仅做展示**，无业务真相。两条独特路径：
- `config/ui/*.json` 表驱动 UI 配置
- `EventSource('/api/events/stream')` 事件流订阅

**22 种面板组件**：text_input、number_input、select、color_picker、flag_group、action_compound、market_selector、base64_readonly、indicator_select、begint_input、readonly_datetime、stock_list_editor、indicator_browser、readonly、flow_info、flow_mode_display、transfer_mode、sector_tree、stock_source_editor、reload_mode、formula_editor、stock_data_table

**5 种边策略**：pass（绿）、copy（蓝）、overwrite（橙）、move（红）、force（紫）

**13 种节点渲染器**：DZH 200/201/202/203/1/2/3/4/5/6 + TDX tdx_7/tdx_8/tdx_3

**双通道实时**：WebSocket 主 + 3 秒超时回退 HTTP 轮询（500ms）

**快照撤销/重做**：深拷贝快照，最大 50 步

---

## 5. 关键类与函数说明

### 5.1 类索引表

| 类 | 所在文件 | 职责 |
|---|---|---|
| `PoolEngine` | `core/engine.py` | 统一引擎，持有编译 schedule + PoolState + EdgeExecutor |
| `Compiler` | `core/execution_module.py` | 编译 `pool_config` → `CompiledSchedule` |
| `CompiledSchedule` | `core/execution_module.py` | 编译产物（7 spec 字段） |
| `EdgeExecutor` | `core/execution_module.py` | 单条边执行（gate → filter → propagate → callback → ttl） |
| `EventDriver` | `core/execution_module.py` | 统一时间驱动，单一 heapq |
| `EventBus` | `core/event_bus.py` | 发布订阅总线 |
| `PoolState` | `core/runtime_mode_module.py` | 池运行时状态（15 张命名表） |
| `RuntimeSimulator` | `core/runtime_mode_module.py` | 完整仿真器 |
| `KLineReplayEngine` | `core/runtime_mode_module.py` | K 线回放引擎 |
| `RuntimeModeModule` | `core/runtime_mode_module.py` | 模式切换 facade |
| `FormulaEngine` | `core/formula_module.py` | 有状态公式引擎 |
| `PythonFormulaEngine` | `core/formula_module.py` | 无状态纯 Python 求值器 |
| `FormulaRouter` | `core/formula_module.py` | HQChart vs Python 路由 |
| `ValueExtractor` | `core/formula_module.py` | 公式结果标量抽取 |
| `FormulaModule` | `core/formula_module.py` | 公式 facade |
| `ScreeningModule` | `core/screening_module.py` | 选股筛选 facade |
| `TradeModule` | `core/trade_module.py` | 交易 facade |
| `MonitoringModule` | `core/monitoring_module.py` | 监控 facade |
| `StatisticsModule` | `core/monitoring_module.py` | 统计 facade |
| `ImportExportModule` | `core/import_export_module.py` | 导入导出 facade |
| `TickBarModule` | `core/tick_bar_module.py` | Tick/K线 facade |
| `DataUpdater` | `core/tick_bar_module.py` | 行情更新器 |
| `BarComposer` | `core/tick_bar_module.py` | K 线合成器 |
| `ConfigStore` | `core/table_engine.py` | 配置表存储 |
| `HotReloadManager` | `core/table_engine.py` | 文件监听 + 原子替换 |
| `PanelGenerator` | `core/table_engine.py` | UI 面板生成 |
| `PropertyOwnershipManager` | `core/table_engine.py` | 属性归属管理 |
| `Storage` | `services/storage.py` | SQLite CRUD |
| `DataQuery` | `services/data.py` | 本地 K 线查询 |
| `DataSourceContract` | `services/data.py` | 数据源契约 |
| `CandidatePoolResolver` | `services/data.py` | 候选池解析 |
| `DataSourceProvider` | `services/providers.py` | 抽象 provider 基类 |
| `DataSourceManager` | `services/providers.py` | provider 管理器 |
| `TqAdapter` | `services/tq_adapter.py` | legacy 门面 |
| `SchemaValidator` | `native/validators.py` | 三层校验编排 |
| `TopologyPatternMatcher` | `native/validators.py` | 拓扑模式匹配 |
| `DynamicCellModel` | `core/schemas.py` | 通用 Cell Pydantic 模型 |
| `parse_cell(data)` | `core/schemas.py` | 按 type 分派到 Cell 子类 |
| `DZHPoolExecutor` | `converters.py` | DZH 池图执行器 |
| `DzhXmlExporter` | `converters.py` | DZH XML 导出器 |

### 5.2 关键函数索引表

| 函数 | 所在文件 | 职责 |
|---|---|---|
| `lifespan(app)` | `app.py` | FastAPI lifespan，装配 16 个模块 |
| `load_global_config()` | `app.py` | 加载 `config/runtime/defaults.json` |
| `verify_api_key(api_key)` | `app.py` | API Key 认证依赖 |
| `_import_demo_pools(storage)` | `app.py` | 导入示例池到 storage |
| `_get_kline_bars(request, code, period, limit)` | `app.py` | 统一 K 线读取（TickBar → Engine → DataQuery → mock 兜底） |
| `_normalize_period(period)` | `app.py` | 周期标识归一化 |
| `parse_dzh_xml(xml_content, filename)` | `converters.py` | DZH XML → 池配置 |
| `parse_tdx_xml(xml_path)` | `converters.py` | TDX XML → 池配置 |
| `_build_tdx_xml(pool_data, path)` | `converters.py` | 池配置 → TDX XML |
| `_tdx_pool_to_frontend(pool, name)` | `converters.py` | TDX 池 → 前端格式 |
| `_load_tdx_pool_config(xml_path)` | `converters.py` | 加载 TDX 池配置 |
| `decode_attr_flags` / `encode_attr_flags` | `converters.py` | bit-flag codec |
| `_dispatch_pool_enter_actions(...)` | `core/trade_module.py` | 入池副作用分派 |
| `_read_history_log(...)` | `core/trade_module.py` | 读取历史日志 |
| `_stock_code(...)` | `core/domain.py` | 股票代码归一化 |
| `time_at(...)` / `time_now_unix()` | `core/domain.py` | 时间工具 |
| `_lookup_builtin_script(...)` | `core/domain.py` | 内置公式查找 |
| `make_bars_history_getter(state, periods)` | `core/tick_bar_module.py` | bars_history getter 工厂 |
| `validate_configs(config_dir)` | `native/validators.py` | 配置校验便捷入口 |
| `should_fire(...)` | `native/validators.py` | Flow/Edge 时机判断 |
| `eval_tdx_condition(dispatch_key, action_inputs)` | `core/screening_module.py` | TDX 条件统一求值 |
| `safe_path_join(base, filename)` | `services/storage.py` | 路径遍历安全 join |
| `format_event(ev)` | `core/web_state.py` | 事件面板格式化 |
| `runtime_state(mode, now_ts, session_id)` | `core/web_state.py` | `/api/state/runtime` 响应 |
| `main()` | `scripts/check_module_imports.py` | 跨模块导入静态检查入口 |
| `main()` | `scripts/dev_tools.py` | CLI 开发工具入口 |

### 5.3 Pydantic Spec 数据类

定义在 `core/execution_module.py`，构成 `CompiledSchedule` 的 7 个 spec 字段：

| Spec | 字段示例 | 职责 |
|---|---|---|
| `EdgeContext` | `eid, sid, tid, edge_type, source_node_type` | 边上下文 |
| `TimingSpec` | `starttype, cxtype, begint, endt, interval_sec` | 时机（24 组合：starttype 0-7 × cxtype 0-2） |
| `FilterSpec` | `nset, noperate, ntjindexno, nperiod, nfirst, nsecond, fsecond, evaluator_type` | 过滤（nset 0-5） |
| `PropagateSpec` | `mode (copy/move/overwrite)` | 传播 |
| `ActionSpec` | `bdel, bsound, btip, bsavehis, bsavetoblock, baimpool` | 6 种 psatt 副作用 |
| `TTLSpec` | `hold_sec, endtime, deltype` | TTL 持有与退出 |
| `CompiledSchedule` | `execution_order, edge_ctx, edge_timing_spec, edge_filter_spec, edge_propagate_spec, edge_action_spec, edge_ttl_spec` | 编译产物 |

---

## 6. 事件总线与事件流

### 6.1 端到端事件流

```
外部 tick
    │
    ▼
DataUpdater ──publish──> DataChanged(tick)
                              │
                ┌─────────────┼─────────────┐
                ▼             ▼             ▼
        BarComposer    SnapshotBuilder  UIRenderer
        (合成 K 线)     (节点股票快照)
                │
                ▼
        DataChanged(bar) + BarComposed
                │
                ▼
        FormulaEngine._on_bar_composed
                │
                ▼
        FormulaEvaluated
                │
                ▼
        ScreeningModule._on_formula_evaluated
                │
                ▼
        StockFiltered
                │
                ▼
        EdgeExecutor._filter → _propagate
                │
                ▼
        EdgeFired → TransferExecuted
                │
                ▼
        TTL actions → MetaEngine._emit_domain_event
                │
                ▼
        DomainEvent(ENTER/EXIT/TIMEOUT/RANK_CHANGED)
                │
                ▼
        TradeModule._on_signal → Signal(BUY/SELL)
                │
                ▼
        OrderPlaced → OrderFilled → PositionUpdated
```

### 6.2 四类核心事件

| 事件类型 | 子类型/字段 | 发布者 | 订阅者 |
|---|---|---|---|
| `DataChanged` | tick / bar | DataUpdater / BarComposer | BarComposer / SnapshotBuilder / UIRenderer / FormulaEngine |
| `Executed` | edge-level | EdgeExecutor | SnapshotBuilder / UIRenderer |
| `DomainEvent` | ENTER / EXIT / TIMEOUT / RANK_CHANGED | MetaEngine | SnapshotBuilder / UIRenderer / TradeModule |
| `Signal` | BUY / SELL | EdgeExecutor | TradeModule |

### 6.3 事件不变式

- 单 tick 内事件顺序：`TickReceived → DataChanged(tick) → BarComposed → FormulaEvaluated → StockFiltered → EdgeFired → TransferExecuted → Signal → OrderPlaced → OrderFilled → PositionUpdated`
- 同 fire_time 时 tick 优先于 edge（`EventDriver.fire_due` 实现）

---

## 7. 运行模式

| 模式 | TickSource | 时间驱动 | 交易接口 | 适用场景 |
|---|---|---|---|---|
| `live` | `RealTickSource` | 墙钟 | `live_order` | 实盘 |
| `replay` | `KLineReplayEngine` | 回放时间轴 | `noop` (冻结 paper_trade) | 历史 K 线复盘 |
| `simulation` | `MockDataSource` | `SimulationScheduler` | `paper_trade` (解冻) | 策略仿真 |

**模式切换**：`RuntimeModeModule.switch_mode(mode_id)` 发布 `ModeChanged`，各模块订阅后调整行为。

**速度范围**：0.5x – 20x（仅 replay / simulation）

**PoolState 15 张运行时表**：`node_stocks` / `latest_tick` / `prev_tick` / `bars` / `exec_ctx` / `formula_results` / `filter_inputs` / `edge_fired` / `triggered` / `ttl_entries` / `rankings` / `positions` / `orders` / `event_log` / `dirty`

---

## 8. 配置系统

### 8.1 配置目录组织

```
config/
├── architecture/    # 架构类（12 表）
│   ├── capability_registry.json
│   ├── cell_type_registry.json
│   ├── dispatch.json
│   ├── dzh_type_map.json
│   ├── edge_semantics.json
│   ├── edge_strategies.json
│   ├── engines.json
│   ├── flow_mode_registry.json
│   ├── modules.json
│   ├── pool_roles.json
│   ├── property_ownership.json
│   ├── table_categories.json
│   ├── table_schemas.json
│   └── timing.json
├── data/            # 数据源类（24 表）
│   ├── builtin_formulas.json
│   ├── custom_formulas.json
│   ├── data_config.json
│   ├── data_mappings.json
│   ├── data_pipeline.json
│   ├── data_providers.json
│   ├── data_source_contract.json
│   ├── data_source_mappings.json
│   ├── data_source_routes.json
│   ├── data_sources.json
│   ├── dzh_market_mappings.json
│   ├── formula_funcs.json
│   ├── formula_modes.json
│   ├── formula_routing.json
│   ├── local_file_paths.json
│   ├── market_classifications.json
│   ├── markets.json
│   ├── mock_data.json
│   ├── mock_field_ranges.json
│   ├── price_fields.json
│   ├── tdx_element_schemas.json
│   ├── tdx_enums.json
│   ├── tdx_field_visibility.json
│   ├── tdx_indicator_formula_map.json
│   ├── tdx_indicators.json
│   ├── tdx_noperate_rules.json
│   ├── tdx_ntjindexno_lookup.json
│   └── tdx_system_indicators.json
├── pools/           # 池模板
│   ├── pool_types.json
│   ├── sim_demo_pool.json
│   ├── sim_test_pool.json
│   └── target_pool_100.json
├── runtime/         # 运行时（19 表）
│   ├── attr_flag_map.json
│   ├── defaults.json              # 全局默认值
│   ├── dzh_condition_fallback.json
│   ├── dzh_extra_fields.json
│   ├── dzh_reload_schedule.json
│   ├── event_rules.json
│   ├── fallback_chain.json
│   ├── filter_action_rules.json
│   ├── flow_mode_rules.json
│   ├── highlight_rules.json
│   ├── history_schema.json
│   ├── match_modes.json
│   ├── post_tick_pipeline.json
│   ├── pre_tick_pipeline.json
│   ├── runtime_modes.json
│   ├── side_effect_scopes.json
│   ├── signal_rules.json
│   ├── time_sources.json
│   ├── tracker_schema.json
│   └── trade_interfaces.json
└── ui/              # UI（16 表）
    ├── action_pipeline.json
    ├── action_rules.json
    ├── action_table.json
    ├── actions.json
    ├── api_routes.json
    ├── behavior_actions.json
    ├── chart_config.json
    ├── column_definitions.json
    ├── context_menu_config.json
    ├── dashboard_schema.json
    ├── field_definitions.json
    ├── fields.json
    ├── keyboard_shortcuts.json
    ├── theme_config.json
    ├── toolbar_config.json
    ├── ui_components.json
    ├── ui_layouts.json
    └── ui_state.json
```

### 8.2 三层校验

| 层 | 类 | 检查内容 |
|---|---|---|
| L1 语法 | `SyntaxValidator` | JSON 格式 / 必填字段 / 数据类型 |
| L2 逻辑 | `LogicValidator` | 字段依赖 / 互斥 / 枚举合法性 |
| L3 业务 | `BusinessValidator` | 归属一致性 / handler 引用 / type-map 完整性 |

校验失败时回滚（`HotReloadManager` 原子替换保证）。

### 8.3 热重载流程

```
config/*.json 修改
    │
    ▼
HotReloadManager watchdog (1s 防抖)
    │
    ▼
ConfigStore.reload(table_name) + SchemaValidator 校验
    │
    ├─ 校验通过 → 原子替换内存表 + 发布 ConfigChanged
    │
    └─ 校验失败 → 回滚 + log warning
```

---

## 9. 数据源与公式系统

### 9.1 9 种数据源 Provider

| Provider | 文件位置 | 数据来源 | EventBus |
|---|---|---|---|
| `MockProvider` | `services/providers.py:1086` | 随机生成 | 可选 `TickReceived` |
| `DfcfProvider` | `services/providers.py:1443` | 东方财富 | 可选 |
| `HQChartProvider` | `services/providers.py:2052` | HQChart C++ DLL | 可选 |
| `AkShareProvider` | `services/providers.py:2572` | akshare | 可选 |
| `LocalFileProvider` | `services/providers.py:3081` | 本地文件 | 可选 |
| `TqDllProvider` | `services/providers.py:6907` | TDX DLL (ctypes) | 可选 |
| `TqSdkProvider` | `services/providers.py:8107` | 天勤 SDK | 可选 |
| `TqProvider` | `services/providers.py:8645` | TQ 通用 | 可选 |
| `_StubMockProvider` | `services/providers.py:217` | 测试用 stub | — |

`DataSourceManager` 从 `data_providers.json` 动态加载，维护降级链。**H2 修复**：`TqAdapter` 不再静默回退 mock，无真实源时状态为 `no_real_source`。

### 9.2 公式引擎路由

```
FormulaRouter
    │
    ├─ HQChartProvider 可用 + 公式属于 HQChart 支持范围
    │       │
    │       └─> HQChart C++ 引擎 (HQChartPy2.pyd)
    │
    └─ 否则
            │
            └─> PythonFormulaEngine (纯 Python 实现)
                    │
                    ├─ _ExprParser 解析
                    ├─ CompiledFormula 编译
                    └─ 算子: ema_op / sma_op / sar_op / cross_op / window_op /
                            shift_op / if_op / abs_op / max_op / min_op
```

### 9.3 内置公式与自定义公式

- `config/data/builtin_formulas.json` — 内置公式库
- `config/data/custom_formulas.json` — 用户自定义公式
- `core/domain._load_builtin_formulas()` 加载入口
- `core/domain._lookup_builtin_script()` / `_lookup_builtin_formula_info()` 查询

### 9.4 TDX 条件求值

TDX 条件（type=3）**不含公式文本**，使用 `ntjindexno` 系统指标号索引客户端内置指标库。求值入口：

```
eval_tdx_condition(dispatch_key, action_inputs)
    │
    ├─ nset=0 → IndicatorEvaluator       (技术指标)
    ├─ nset=1 → ConditionFormulaEvaluator (条件选股公式)
    ├─ nset=2 → ExpertSystemEvaluator     (专家系统公式)
    ├─ nset=3 → FinancialScalarEvaluator  (最新财务)
    ├─ nset=4 → MarketScalarEvaluator     (实时行情)
    └─ nset=5 → SetOperationEvaluator     (集合运算: 交/并/差)
```

`noperate` 0-9 表示比较运算符（`>` / `>=` / `<` / `<=` / `=` / `!=` / `cross_up` / `cross_down` / ...）。

---

## 10. 依赖关系

### 10.1 模块层间依赖白名单

由 `scripts/check_module_imports.py` 静态强制：

```
core/        → 仅依赖 core.event_bus / core.domain / core.schemas + 整个 native/ + 标准库
services/    → 依赖 core.* + services.* + 标准库
converters   → 依赖 core.import_export_module（白名单例外）
native/      → 依赖 services.tq_adapter / core.schemas / core.domain + 标准库
api.py       → 依赖所有上层
app.py       → 装配层，可 import 任意模块
```

**文件级白名单例外**（`MODULE_INTERNAL_WHITELIST`）：
- `core/import_export_module.py` 可 import `converters`
- `converters.py` 可 import `core.import_export_module`
- `core/runtime_mode_module.py` 可 import `core.engine`
- `core/execution_module.py` 可 import `core.engine`

### 10.2 Python 第三方依赖（推断）

项目无 `requirements.txt` / `pyproject.toml` / `setup.py` / `Dockerfile`。从代码 import 推断：

| 依赖 | 用途 | 必需性 |
|---|---|---|
| `fastapi` | Web 框架 | 必需 |
| `starlette` | ASGI 基础（FastAPI 依赖） | 必需 |
| `uvicorn` | ASGI 服务器（推断） | 必需 |
| `pydantic` | 数据校验 | 必需 |
| `pandas` | 数据处理 | 必需 |
| `numpy` | 数值计算 | 必需 |
| `pyarrow` | parquet 读写 | 必需 |
| `requests` | HTTP 客户端 | 必需（`quick_test.py`） |

**可选数据源依赖**：

| 依赖 | 用途 |
|---|---|
| `tqsdk` | 天勤 SDK |
| `akshare` | AKShare 数据源 |
| `ctypes` (TDX DLL) | TDX 原生 DLL |
| `HQChartPy2.pyd` | HQChart C++ 引擎 (Windows, CPython 3.13 ABI) |

### 10.3 前端依赖 (`web/package.json`)

| 依赖 | 用途 |
|---|---|
| `jest` (^29.0.0) | 单元测试 |
| `jest-environment-jsdom` (^29.0.0) | jsdom 环境 |
| `@playwright/test` (^1.61.1) | E2E 浏览器测试 |

### 10.4 外部 C++ 扩展

`HQChartPy2/__init__.py`：
- 加载 `HQChartPy2.pyd`（CPython 3.13 ABI，Windows）
- 内嵌 OpenSSL 1.1 DLLs (`libcrypto-1_1-x64.dll` / `libssl-1_1-x64.dll`)
- Windows 下调用 `os.add_dll_directory(_SELF_DIR)` 让 `.pyd` 找到 OpenSSL
- 导出：`GetAuthorizeInfo`、`GetVersion`、`LoadAuthorizeInfo`、`Run`、`SetLog`

---

## 11. 项目运行方式

### 11.1 后端启动

由于项目无显式 `requirements.txt` 或运行脚本，标准启动方式：

```bash
# 1. 安装 Python 依赖（手动）
pip install fastapi uvicorn pydantic pandas numpy pyarrow requests

# 2. 启动 ASGI 服务
cd /workspace
uvicorn app:app --host 0.0.0.0 --port 8000
```

启动后：
- FastAPI 应用创建时执行 `lifespan()`，依次实例化 16 个模块
- 自动从 `config/pools/*.json` 导入示例股票池到 SQLite
- 启动 `HotReloadManager` 监听 `config/` 目录变化
- 加载所有 `config/**/*.json` 配置表到 `ConfigStore`

### 11.2 前端访问

前端由 FastAPI 从 `web/index.html` 提供服务（Phase 11 合并的单 HTML，hash 路由）：

- `http://localhost:8000/` — 主界面（池编辑/执行）
- `http://localhost:8000/#/config` — 配置中心
- `http://localhost:8000/#/formula` — 公式管理

### 11.3 API 认证

API Key 认证通过 `X-API-Key` 请求头：

- 配置文件：`config/runtime/defaults.json` 的 `auth` 字段
- 环境变量：`META_CORE_API_KEY`
- 默认关闭（`auth.enabled=false` 时跳过校验，向后兼容）
- WebSocket 路由独立挂载，不带 API key 依赖（不支持 `APIKeyHeader`）

### 11.4 CORS

从 `config/runtime/defaults.json` 的 `cors` 字段加载：

```json
{
  "cors": {
    "allowed_origins": ["http://localhost:*", "http://127.0.0.1:*"]
  }
}
```

### 11.5 烟雾测试

`quick_test.py` 提供仿真烟雾测试：

```bash
# 前置：服务必须运行在 127.0.0.1:8000
python quick_test.py
```

脚本行为：
1. 加载 `config/pools/target_pool_100.json`
2. `POST /api/sim/start` 启动仿真（speed=1.0）
3. 10 次迭代 `POST /api/sim/control`（`action: step, params: {delta: 5.0}`）
4. 打印事件类型计数

### 11.6 单元测试与仿真测试

```bash
# 单元测试（pytest）
pytest tests/

# 仿真测试套件（自带 harness）
pytest simtests/

# 前端单元测试
cd web && npm test

# E2E 测试
cd web && npx playwright test
```

`simtests/harness/` 提供：
- `driver.py` — 仿真驱动器
- `clock.py` — 仿真时钟
- `assertions.py` / `bug_asserts.py` — 断言库
- `datasets.py` — 测试数据集
- `log_capture.py` — 日志捕获
- `perf.py` — 性能采集

### 11.7 开发工具

`scripts/dev_tools.py` 提供 CLI 子命令：

```bash
# DZH XML 结构分析
python scripts/dev_tools.py analyze_dzh xml <file>
python scripts/dev_tools.py analyze_dzh xml2 <file>
python scripts/dev_tools.py analyze_dzh tianji_bfs <file>

# 配置生成与校验
python scripts/dev_tools.py config_tools generate
python scripts/dev_tools.py config_tools validate

# DZH 公式解码 v3
python scripts/dev_tools.py decode_formulas <file>

# 公式调试
python scripts/dev_tools.py debug_formula --xml-path <path>

# 配置表合并（Task 11）
python scripts/dev_tools.py merge_config_tables

# XML 工具
python scripts/dev_tools.py xml_tools check <file>
python scripts/dev_tools.py xml_tools inject <file> [out]
```

### 11.8 跨模块导入检查

```bash
python scripts/check_module_imports.py
```

扫描 `core/` + `services/` + `converters.py`，强制层间白名单。返回 0 通过，1 失败。

### 11.9 数据准备

- **历史 K 线**：`data/history/1d/*.parquet`（已含 000001、600000.SH、600000 示例）
- **SQLite**：`data/pools.db`（首次启动自动创建，9 张统一表 + 5 张候选池表）
- **TDX 池样例**：`tdxpool/*.xml`（60+ 真实池）
- **DZH 池样例**：`dzhpool/*.xml`（200+ 真实池）
- **示例池 JSON**：`examples/*.json` + `config/pools/*.json`

### 11.10 HQChart C++ 扩展（可选）

仅在 Windows + Python 3.13 环境下加载：

- 文件：`HQChartPy2/HQChartPy2.pyd` + `libcrypto-1_1-x64.dll` + `libssl-1_1-x64.dll`
- 加载失败时 `HQChartProvider` 初始化失败仅 warning，自动回退 `PythonFormulaEngine`
- 不影响其他功能

---

## 附录 A：术语表

| 术语 | 含义 |
|---|---|
| **股票池** | 状态池流水线，有向图执行系统 |
| **变换单元** | (条件转移边 + 转移条件 + 无条件转移边) 三元组，原子计算单元 |
| **备选池** | 股票源（DZH type=202 / TDX type=7） |
| **转移条件** | 过滤节点（DZH type=201 / TDX type=3） |
| **状态池** | 股票存储节点（DZH type=200 / TDX type=8） |
| **nset** | 过滤类型（0=指标 / 1=条件选股 / 2=专家系统 / 3=财务 / 4=行情 / 5=集合运算） |
| **noperate** | 比较运算符（0-9） |
| **ntjindexno** | TDX 系统指标号 |
| **psatt** | TDX 状态池副作用（6 种：bdel/bsound/btip/bsavehis/bsavetoblock/baimpool） |
| **tran** | 边传播模式（0=copy / 1=move） |
| **starttype** | 开始时间类型（0-7） |
| **cxtype** | 持续类型（0-2） |
| **CompiledSchedule** | 编译产物（7 spec 字段） |
| **EventBus** | 模块间唯一通信中介 |
| **PoolState** | 池运行时状态（15 张命名表） |
| **三模式** | live / replay / simulation |
| **三层校验** | 语法 / 逻辑 / 业务 |
| **三层架构** | 事件驱动 + 时间驱动 + 表驱动 |

## 附录 B：DZH / TDX 格式对比

| 维度 | DZH | TDX |
|---|---|---|
| 根元素 | `<pool type="ss-pool" ver="1.0|1.1" mode="1">` | `<root><pool nextid="..." backcolor="...">` |
| Cell 类型数 | 10（1-6, 200-203） | 6（0, 1, 2, 3, 7, 8） |
| 备选池 type | 202 | 7 |
| 转移条件 type | 201 | 3 |
| 状态池 type | 200 | 8 |
| 条件公式 | Base64 自包含 (`indi` 字段) | `ntjindexno` 索引客户端内置指标库 |
| 文本颜色 | 隐藏在 attr bit flag | 独立 `clrtext` 属性 |
| 填充样式 | 无独立控制 | `solid` (1=实心 / 0=空心) |
| 编码 | GB2312 / GBK | GBK |
| 状态池副作用 | `tradeattr` 19 字段 | `psatt` 13-14 参数 |

## 附录 C：参考文档索引

| 文档 | 路径 | 抽象层级 | 关注点 |
|---|---|---|---|
| DESIGN.md | `/workspace/DESIGN.md` | 实现级 | 表操作映射，每步执行如何 |
| SPEC.md | `/workspace/docs/SPEC.md` | 契约级 | 架构不变式 / 事件流契约 / 反模式 |
| FEATURES.md | `/workspace/docs/FEATURES.md` | 功能级 | 已构建 / 已验证 / 待办清单 |
| SYSTEM_REFERENCE.md | `/workspace/docs/SYSTEM_REFERENCE.md` | 参考级 | 类/方法清单 / 数据流 / 发布订阅映射 |
| ARCHITECTURE.md | `/workspace/doc/ARCHITECTURE.md` | 概览级 | 三层架构 + 事件流图（最简） |
| DZH 技术文档 | `/workspace/docs/reference/DZH股票池完整技术文档.md` | 输入格式 | DZH XML 完整规范 |
| TDX 技术文档 | `/workspace/docs/reference/TDX股票池完整技术文档.md` | 输入格式 | TDX XML 完整规范 |
| 公式引擎指南 | `/workspace/docs/公式引擎使用指南.md` | 用户级 | 公式使用 |
| 函数表映射 | `/workspace/docs/FUNCTION_TABLE_MAP.md` | 实现级 | 函数 → 配置表映射 |

---

**文档版本**：基于代码库当前状态生成  
**核心模块数**：14（core/）+ 4（services/）+ 2（native/）+ 1（converters）+ 1（api）+ 1（app）= **23 个主要文件**  
**核心配置表**：71 个 JSON（architecture 12 + data 24 + runtime 19 + ui 16）  
**最大文件**：`services/providers.py`（~8900 行，8+ 数据源 provider）  
**入口**：`app.py` (FastAPI lifespan) → `uvicorn app:app`
