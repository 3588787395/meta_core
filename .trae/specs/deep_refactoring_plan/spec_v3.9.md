# v3.9 核心类精准定义（关系图精进 · 冲刺90分）

---

## 0. v3.8→v3.9 改进说明

### 评分目标

| 维度 | v3.8 | v3.9 目标 | 改进方向 |
|------|------|-----------|----------|
| 概括力 | 86 | 88+ | 定义更凝练，删废话 |
| 精准度 | 91 | 92+ | 属性/方法更准，代码引用更实 |
| 关系图 | 8/20 | 15/20 | 从1张混成3张分层图 |
| **综合** | **88** | **90+** | — |

### 主要改进点

| 改进项 | v3.8 问题 | v3.9 方案 |
|--------|-----------|-----------|
| 关系图 | 三种箭头混一张，辨识度低 | 拆成 3 张图：架构层次图 / 依赖关系图 / 数据流图，各有标题图例 |
| MetaEngine | 只列 3 方法，读者误以为类很简单 | 增加「核心内部机制」小节，介绍 6 大子系统 |
| FormulaRouter 属性 | `_simple_functions` 只是复杂度判定输入，不够核心 | 换成 `_engine_methods`（引擎方法映射表，表驱动分派核心） |
| 整体凝练 | 定义和描述还有废话 | 每类定义再减几个字，描述更精准 |

---

## 1. 核心类选型说明

**选型标准：** 缺了这 5 个类，股票池系统跑不起来。

| 序号 | 类名 | 核心职责 | 所在层 |
|------|------|---------|--------|
| 1 | MetaEngine | 运行时主引擎，tick 驱动拓扑执行与股票流转 | 引擎层 |
| 2 | FormulaRouter | 公式计算入口，双引擎路由+缓存 | 引擎层 |
| 3 | ConfigStore | 配置表存储中心，表驱动架构根基 | 配置层 |
| 4 | DynamicCellModel | 节点通用数据模型，图的基本构成单元 | 模型层 |
| 5 | DynamicFlowModel | 边通用数据模型，图的基本构成单元 | 模型层 |

**选型依据：** MetaEngine `__init__` 直接初始化 `formula_router`（`engine.py:400`），公式求值统一走 FormulaRouter，PythonFormulaEngine 只是其内部执行引擎之一。

---

## 2. 类1：MetaEngine —— 股票池运行时主引擎，tick 驱动拓扑执行与股票流转

**代码位置：** `core/engine.py:298`

### 核心属性（3个）

| 属性 | 公开/内部 | 含义 | 代码引用 |
|------|-----------|------|---------|
| `tables` | 公开 | 启动时加载的配置表快照（dict，{表名: 表数据}） | `engine.py:346` |
| `formula_router` | 公开 | 公式路由器实例，双引擎公式求值入口 | `engine.py:400` |
| `_rt` | 内部 | 运行时状态容器，node_stocks 等动态状态收敛于此 | `engine.py:349` |

> **说明：** 保留 1 个内部属性以体现"运行时状态收敛到 _rt"这一架构特点。去掉 `_compiled_cache`（编译缓存是实现细节）和 `highlight_events`（事件配置是次要功能）。

### 核心方法（3个）

| 方法 | 公开/内部 | 干什么 | 代码引用 |
|------|-----------|--------|---------|
| `run_pool(pool_config, ...)` | 公开 | 同步运行一次完整股票池计算（对外主入口） | `engine.py:3815` |
| `set_tq_adapter(a)` | 公开 | 注入行情适配器，懒加载 formula_router | `engine.py:576` |
| `_compile_pool(pool_config)` | 内部 | 编译股票池为静态调度表（拓扑排序+边预计算+缓存） | `engine.py:1354` |

> **说明：** 保留 1 个内部方法以体现"编译期 vs 运行期"的架构分层。去掉 `_run_tick_event_driven` 和 `_process_edge_pipeline`（两者是 `_compile_pool` 编译结果的执行者，属同一执行链路的细分步骤）。

### 发出事件

- **对外事件：** 通过 `highlight_events` 列表 + `_highlight_listeners` 回调列表对外通知高亮事件（`engine.py:371`）
- **内部事件机制：** `_event_queue` / `_signal_queue` / `_alert_queue` 三个 asyncio.Queue 传递内部事件（`engine.py:383`）

---

## 3. 类2：FormulaRouter —— 公式路由器，按复杂度与周期选择引擎，并管理结果缓存

**代码位置：** `core/formula_router.py:81`

### 核心属性（3个）

| 属性 | 公开/内部 | 含义 | 代码引用 |
|------|-----------|------|---------|
| `_cache` | 内部 | 公式结果缓存（FormulaCache 实例） | `formula_router.py:101` |
| `_routing_rules` | 内部 | 引擎路由规则表（表驱动，按复杂度/周期决策） | `formula_router.py:106` |
| `_engine_methods` | 内部 | 引擎方法映射表（表驱动分派，消除 if engine 分支） | `formula_router.py:108` |

> **说明：** v3.8 的 `_simple_functions` 只是复杂度判定的输入之一，属次要。换成 `_engine_methods`——这是表驱动架构的核心体现：引擎调用不靠 `if engine == "python"` 分支，而靠查表反射调用，是 FormulaRouter 最精妙的设计之一。3 个全为内部属性，这是事实。

### 核心方法（3个）

| 方法 | 公开/内部 | 干什么 | 代码引用 |
|------|-----------|--------|---------|
| `eval(formula, symbol, period, ...)` | 公开 | 单股公式求值（带缓存+路由） | `formula_router.py:309` |
| `eval_batch(formula, symbols, ...)` | 公开 | 批量公式求值（带缓存+路由） | `formula_router.py:451` |
| `eval_outvars(formula, symbol, ...)` | 公开 | 单股公式求值，返回全部输出变量末值 | `formula_router.py:348` |

> **说明：** 3 个全为公开方法，覆盖主要使用场景：单股求值、批量求值、多输出变量求值。去掉 `_resolve_engine`（内部路由决策方法，使用者不需要知道）。

### 发出事件

- **无**。纯计算路由层，无副作用、无事件。

---

## 4. 类3：ConfigStore —— 配置表存储中心，加载、缓存、校验、热加载

**代码位置：** `core/table_engine.py:27`

### 核心属性（3个）

| 属性 | 公开/内部 | 含义 | 代码引用 |
|------|-----------|------|---------|
| `_tables` | 内部 | 配置表集合，{表名: 表数据 dict}（通过 `get()` 访问） | `table_engine.py:32` |
| `_hashes` | 内部 | 文件 MD5 哈希表，热加载变更检测用 | `table_engine.py:33` |
| `_validators` | 内部 | 自定义校验器注册表（通过 `register_validator()` 注册） | `table_engine.py:35` |

> **说明：** 3 个全为内部属性，这是事实——不为了"好看"而去下划线。去掉 `_config_dir`（数据来源，不是核心状态）和 `_schema_validator`（三级校验器是外部依赖）。

### 核心方法（3个）

| 方法 | 公开/内部 | 干什么 | 代码引用 |
|------|-----------|--------|---------|
| `load_all()` | 公开 | 加载目录下所有 JSON 配置表并校验 | `table_engine.py:49` |
| `get(name, default)` | 公开 | 按表名获取配置表数据（最常用对外方法） | `table_engine.py:248` |
| `check_hot_reload()` | 公开 | 检测配置文件变更，热加载并三级校验 | `table_engine.py:326` |

> **说明：** 3 个最能代表 ConfigStore 本质：加载、查询、热加载。去掉 `validate_table_with_report`（校验是热加载内部步骤）和 `get_layout_for_type`（布局查询是特定业务查询）。

### 发出事件

- **无异步事件推送。** 热加载变更通过返回变更表名列表同步通知调用方。

---

## 5. 类4：DynamicCellModel —— 通用节点数据模型，承载所有类型节点的结构化数据

**代码位置：** `core/schemas.py:168`

### 核心属性（3个）

| 属性 | 公开/内部 | 含义 | 代码引用 |
|------|-----------|------|---------|
| `id` | 公开（动态） | 节点唯一标识（通过 `__getattr__` 代理到 `_data['id']`） | `schemas.py:299` / `schemas.py:179` |
| `cell_type` | 公开（动态） | 节点类型 ID（整数，200=状态池/201=条件/202=备选池...） | `schemas.py:216` |
| `_data` | 内部 | 已知字段数据字典（动态属性的实际存储） | `schemas.py:179` |

> **说明：** 2 个业务属性 + 1 个内部存储属性——既说明"用起来像有这些属性"，也说明"实际上存在 _data 字典里"，两者都是事实。去掉 `position`（UI 坐标是次要属性）和 `_extra`（扩展存储是实现细节）。

### 核心方法（3个）

| 方法 | 公开/内部 | 干什么 | 代码引用 |
|------|-----------|--------|---------|
| `from_dict(data)` | 公开（类方法） | 从原始字典创建模型，自动解析 attr 位标志/位置 | `schemas.py:184` |
| `to_dict()` | 公开 | 序列化回字典，bit fields 重组为 attr 整数 | `schemas.py:316` |
| `get(key, default)` | 公开 | dict 风格取值，支持默认值 | `schemas.py:285` |

> **说明：** 2 个序列化方法 + 1 个访问方法，覆盖最常用操作。去掉 `__getitem__` 和 `__getattr__`（两者都是访问器魔法方法，`get()` 已能代表 dict 风格访问的本质）。

### 发出事件

- **无**。纯数据模型，无行为。

---

## 6. 类5：DynamicFlowModel —— 通用边数据模型，承载节点间股票流转规则

**代码位置：** `core/schemas.py:459`

### 核心属性（3个）

| 属性 | 公开/内部 | 含义 | 代码引用 |
|------|-----------|------|---------|
| `from_cell_id` | 公开（动态） | 源节点 ID（通过 `__getattr__` 代理到 `_data`） | `schemas.py:601` / `schemas.py:470` |
| `to_cell_id` | 公开（动态） | 目标节点 ID（通过 `__getattr__` 代理到 `_data`） | `schemas.py:601` / `schemas.py:470` |
| `mode_name` | 公开（property） | 流转模式名，由 attr 位标志动态解析 | `schemas.py:559` |

> **说明：** 2 个结构属性 + 1 个语义属性——既说明"边是什么"（连接哪两个节点），也说明"边干什么"（什么流转模式）。去掉 `_data` 和 `_extra`（边模型与节点模型同构，读者可类推）。

### 核心方法（3个）

| 方法 | 公开/内部 | 干什么 | 代码引用 |
|------|-----------|--------|---------|
| `from_dict(data)` | 公开（类方法） | 从原始字典创建模型，解析 attr 位标志+字段别名 | `schemas.py:475` |
| `to_dict()` | 公开 | 序列化回字典，bit fields 重组为 attr 整数 | `schemas.py:618` |
| `identify_mode()` | 公开 | 根据规则识别流转模式 | `schemas.py:555` |

> **说明：** 2 个序列化方法 + 1 个模式识别方法，覆盖边模型最本质的操作。去掉 `from_int` 和 `to_int`（attr 整数编解码是 `from_dict`/`to_dict` 的内部步骤）。

### 发出事件

- **无**。纯数据模型，无行为。

---

## 7. 架构层次图

### 图1：架构层次图（系统有几层，每层有什么）

**说明：** 从下到上，数据从配置层流向模型层，再由引擎层驱动执行。

```
┌─────────────────────────────────────────────────┐
│                   引擎层                         │
│  ┌──────────────┐      ┌──────────────────┐    │
│  │  MetaEngine  │◄────►│  FormulaRouter   │    │
│  │  (主引擎)     │      │  (双引擎路由)     │    │
│  └──────────────┘      └──────────────────┘    │
├─────────────────────────────────────────────────┤
│                   模型层                         │
│  ┌──────────────┐      ┌──────────────────┐    │
│  │DynamicCell   │◄────►│  DynamicFlow     │    │
│  │  (节点模型)   │      │  (边模型)        │    │
│  └──────────────┘      └──────────────────┘    │
├─────────────────────────────────────────────────┤
│                   配置层                         │
│  ┌──────────────────────────────────────────┐   │
│  │              ConfigStore                 │   │
│  │         (配置表存储中心)                  │   │
│  └──────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
```

**每层职责：**

| 层级 | 核心类 | 职责一句话 |
|------|--------|-----------|
| 配置层 | ConfigStore | 存所有 JSON 配置表，是表驱动架构的根基 |
| 模型层 | DynamicCellModel + DynamicFlowModel | 图的两个基本构成元素，节点+边 |
| 引擎层 | MetaEngine + FormulaRouter | 读配置、建模型、tick 驱动、公式求值 |

---

## 8. 依赖关系图

### 图2：依赖关系图（谁调用谁、谁注入谁）

**图例：**

| 箭头 | 含义 | 说明 |
|------|------|------|
| `──▶` | 调用/委托 | A 调用 B 的方法，B 是 A 的依赖 |
| `──◇` | 配置注入 | 配置数据从上游流入下游 |
| `───` | 结构组成 | 两者共同组成更大的结构 |

```
                ┌──────────────┐
                │  ConfigStore │
                └──────┬───────┘
                       │ 配置注入
         ┌─────────────┴──────────────┐
         ▼                            ▼
  ┌──────────┐    委托求值      ┌──────────────┐
  │ MetaEngine│ ──────────────▶ │ FormulaRouter │
  │  (主引擎) │                  │  (路由层)    │
  └─────┬────┘                  └──────────────┘
        │ 编译/驱动
        ▼
  ┌──────────┐                ┌───────────┐
  │DynamicCell│ ──────────── │DynamicFlow│
  │  (节点)   │   结构组成    │  (边)     │
  └──────────┘                └───────────┘
```

**关键依赖路径：**

1. **MetaEngine → ConfigStore：** 启动时从 ConfigStore 读所有配置表（`engine.py:346`）
2. **MetaEngine → FormulaRouter：** 公式求值全部委托给 FormulaRouter（`engine.py:400`）
3. **MetaEngine → DynamicCell/DynamicFlow：** 编译池配置为节点+边模型，再驱动执行
4. **FormulaRouter → （内部）PythonFormulaEngine / HQChartProvider：** 路由到具体执行引擎

---

## 9. 数据流图

### 图3：数据流图（数据从哪来到哪去）

**图例：**

| 箭头 | 含义 |
|------|------|
| `══▶` | 主数据流向（股票数据/计算结果） |
| `──▶` | 控制/配置流向 |

```
  行情数据(tick)
       │
       ▼
┌──────────┐   股票池配置    ┌──────────────┐
│  行情适配 │ ─────────────▶ │  ConfigStore │
│  器(tq)  │                  └──────┬───────┘
└─────┬────┘                         │ 配置表
       │  bar_data                   ▼
       │  ──────────────▶   ┌──────────────────┐
       │                    │    MetaEngine    │
       │                    │  (编译+执行拓扑)  │
       │                    └────────┬─────────┘
       │                             │
       │  K线数据请求                │ 公式求值请求
       ▼                             ▼
┌──────────┐   K线数据返回    ┌──────────────┐
│ DataQuery│ ◀────────────── │ FormulaRouter│
│(数据查询) │ ──────────────▶ │  (路由+缓存)  │
└──────────┘   计算结果返回    └──────────────┘
                                     │
                                     ▼
                              ┌──────────────┐
                              │  Python引擎  │
                              │  / HQChart   │
                              └──────────────┘
```

**数据流向一句话：** 行情 tick 进 MetaEngine，触发拓扑执行；边公式需要计算时，MetaEngine 调 FormulaRouter，FormulaRouter 从 DataQuery 取 K 线、路由到具体引擎计算、缓存结果后返回。

---

## 10. MetaEngine 核心内部机制（补充说明）

> **为什么需要这一节？** v3.8 只展示了 3 个方法，读者可能误以为 MetaEngine 是个简单的类。实际上它是整个系统最复杂的类（`engine.py` 超过 3800 行），内部有多个子系统协同工作。以下是 6 大核心子系统的概览：

### 子系统1：时机守门系统（Timing Gate System）

**职责：** 判断一条边在当前时刻"该不该触发"。

**核心组件：**
- `_should_trigger_edge()` —— 统一 gate 评估入口（`engine.py:1757`）
- `_tdx_should_execute()` —— TDX 开始时间判定（`engine.py:781`）
- `_tdx_check_duration()` —— 持续时长判定（`engine.py:1013`）
- `_should_fire_flow_replay()` —— 回放模式 begin/end/interval 判定（`engine.py:1033`）
- `_compiled_timing` —— 预编译的 timing 表达式（`engine.py:433`）

**一句话：** 时机不到，再好的条件也不跑。

### 子系统2：过滤执行系统（Filter Pipeline System）

**职责：** 时机到了之后，具体执行股票筛选和流转。

**核心组件：**
- `_process_edge_pipeline()` —— 边处理流水线主入口（`engine.py:2713`）
- `_build_pipeline_ctx()` —— 构建流水线上下文（`engine.py:1174`）
- `_resolve_filter_type()` —— 判定过滤类型（条件/无条件/...）（`engine.py:1333`）
- `_run_formula_eval_batch_sync()` —— 批量公式求值（`engine.py:1797`）

**一句话：** 时机到了之后，按流水线一步步筛股票、转节点。

### 子系统3：编译调度系统（Compilation & Scheduling）

**职责：** 把股票池配置（原始 XML/JSON）编译成可高效执行的静态调度表。

**核心组件：**
- `_compile_pool()` —— 编译主入口，返回 CompiledSchedule（`engine.py:1354`）
- `_prepare_topology()` —— 拓扑预处理（`engine.py:1191`）
- `_build_processing_plan()` —— 构建执行计划（`engine.py:1289`）
- `_compile_edge_spec()` —— 编译单条边的规格（`engine.py:1433`）
- `_compiled_cache` —— 编译结果缓存，按 pool_config 内容哈希（`engine.py:460`）

**一句话：** 编译一次，执行多次；避免每个 tick 都重新算拓扑。

### 子系统4：运行时状态系统（Runtime State System）

**职责：** 管理所有动态运行时状态，收敛到 `_rt` 命名空间。

**核心组件：**
- `_rt` —— 运行时状态容器（dict）（`engine.py:349`）
- `_init_runtime_tables()` —— 初始化运行时表（`engine.py:500`）
- `__getattr__` / `__setattr__` —— 属性代理，兼容旧访问方式（`engine.py:543` / `engine.py:534`）
- `_dirty_nodes` / `_node_snapshots` —— 脏节点标记与快照（`engine.py:385`）
- `_latest_tick` —— 最新行情快照，行情唯一真相源（`engine.py:462`）

**一句话：** 所有动态状态收敛到 _rt，通过属性代理对外提供兼容访问。

### 子系统5：事件系统（Event System）

**职责：** 内部事件传递与对外通知。

**核心组件：**
- `_event_queue` —— 通用事件队列（asyncio.Queue）（`engine.py:383`）
- `_signal_queue` —— 信号事件队列（`engine.py:383`）
- `_alert_queue` —— 预警事件队列（`engine.py:411`）
- `_highlight_listeners` —— 高亮事件监听器列表（对外回调）（`engine.py:371`）
- `_event_rules` / `_signal_rules` —— 事件/信号规则表（`engine.py:388`）

**一句话：** 三个异步队列传内部事件，高亮回调通知外部 UI。

### 子系统6：多级缓存系统（Multi-Level Cache System）

**职责：** 各级缓存避免重复计算，提升性能。

**核心组件：**
- `_compiled_cache` —— 编译结果缓存（pool_config 哈希 → CompiledSchedule）（`engine.py:460`）
- `_data_cache` —— 数据缓存（K线/快照/财务/板块，LRU + TTL）（`engine.py:406`）
- `_filter_cache` —— 过滤结果缓存（节点过滤结果，LRU + TTL）（`engine.py:417`）
- `_exit_tracker_cache` —— 退出追踪缓存（`engine.py:386`）

**一句话：** 编译缓存、数据缓存、过滤缓存——三级缓存支撑高性能运行。

> **注：** 以上 6 个子系统只是 MetaEngine 内部的主要部分。实际代码中还有：能力注册系统（capability_registry）、运行模式系统（runtime_modes）、Tracker 公式系统、备选池刷新管理器、拓扑模式识别器等。完整理解 MetaEngine 需要阅读 3800+ 行的 engine.py。

---

## 11. 股票池本质一句话（v3.9 版，30 字）

**股票池是行情驱动的有向图过滤器，股票经边公式筛选后在节点间流转。**

> **说明：** 与 v3.8 相同。这 30 字已经很准了——"行情驱动"说明输入源，"有向图过滤器"说明计算模型，"股票经边公式筛选后在节点间流转"说明执行过程。再减字会损伤精度。

---

## 附录 A：为什么 FormulaRouter 换 `_engine_methods` 不换别的？

### 候选属性对比

| 属性 | 核心性 | 为什么不选 |
|------|--------|-----------|
| `_simple_functions` | ★★☆ | 只是复杂度判定的一个输入，v3.8 已用 |
| `_hqchart_available` | ★★★ | 引擎可用状态，确实重要，但只是一个 bool 标志 |
| `_python_engine` | ★★☆ | 具体引擎实例，属"执行端"，不是"路由"的核心 |
| `_hqchart_provider` | ★★☆ | 同上，具体引擎实例 |
| `_data_query` | ★★☆ | 数据查询依赖，是基础设施，不是路由核心 |
| **`_engine_methods`** | **★★★★★** | **表驱动分派的核心体现——路由决策后不靠 if-else，靠查表反射调用。这是 FormulaRouter 最精妙的架构设计。** |

### 为什么 `_engine_methods` 最核心？

FormulaRouter 的核心职责是"路由"。路由分两步：
1. **决策：** 用 `_routing_rules` 决定用哪个引擎
2. **分派：** 用 `_engine_methods` 决定调哪个方法

`_routing_rules` 是"决策表"，`_engine_methods` 是"分派表"——两者配合，才实现了完整的表驱动路由。缺了 `_engine_methods`，路由决策完了还得写 `if engine == "python": return _eval_python(...)` 这种硬编码分支，那就不是"表驱动路由"了。

所以三个核心属性是：
- `_cache` —— 性能核心
- `_routing_rules` —— 决策核心
- `_engine_methods` —— 分派核心

完美对应"缓存 + 路由决策 + 引擎分派"三大职责。
