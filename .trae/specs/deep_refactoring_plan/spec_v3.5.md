# v3.5 核心类精准定义（5个核心类 · 属性 · 方法 · 事件）

---

## 1. 核心类选型说明

**选型标准：** 没有这5个类，股票池系统就跑不起来——缺了引擎没法执行，缺了公式没法选股，缺了配置表没有业务逻辑，缺了节点和边就没有股票池拓扑。

| 序号 | 类名 | 为什么是核心 |
|------|------|-------------|
| 1 | MetaEngine | 运行时主引擎，驱动整个股票池 tick 循环、边过滤、股票流转 |
| 2 | PythonFormulaEngine | 公式计算引擎，条件选股的核心能力，没有它条件节点无法工作 |
| 3 | ConfigStore | 配置表存储中心，表驱动架构的根基，所有业务规则从这里读取 |
| 4 | DynamicCellModel | 节点通用数据模型，承载状态池/条件/备选池等所有节点类型的数据 |
| 5 | DynamicFlowModel | 边通用数据模型，承载节点间股票流转的规则与属性 |

---

## 2. 类1：MetaEngine —— 股票池运行时主引擎，驱动行情tick下的节点拓扑执行与股票流转

**代码位置：** `core/engine.py:298`

### 核心属性（5个）

| 属性 | 含义 | 代码引用 |
|------|------|---------|
| `tables` | 配置表字典，所有业务规则配置的内存镜像 | `engine.py:346` |
| `_rt` | 运行时表命名空间，node_stocks等所有运行时状态收敛于此 | `engine.py:349` |
| `_compiled_cache` | 编译期调度表缓存，{pool_config哈希: CompiledSchedule} | `engine.py:460` |
| `formula_router` | 公式路由，双引擎公式求值入口 | `engine.py:372` |
| `_latest_tick` | 最新行情快照，行情数据唯一真相源 | `engine.py:462` |

### 核心方法（5个）

| 方法 | 干什么 | 代码引用 |
|------|--------|---------|
| `_compile_pool(pool_config)` | 编译股票池为静态调度表（拓扑排序+边预计算+缓存） | `engine.py:1354` |
| `_run_tick_event_driven(...)` | 单tick核心执行：按处理计划逐条边执行过滤与流转 | `engine.py:3578` |
| `_filter_conditional(ctx)` | 条件过滤：TDX公式/条件节点筛选股票 | `engine.py:2528` |
| `_process_edge_pipeline(ctx)` | 边流水线：gate→重放守卫→tdx守卫→时长守卫→流转解析→变换→后处理 | `engine.py:2713` |
| `run_pool(pool_config, ...)` | 同步运行一次完整股票池计算（对外入口） | `engine.py:3815` |

### 发出事件

- **无显式事件机制**。通过 `_event_queue` / `_signal_queue` / `_alert_queue` 三个 asyncio.Queue 传递内部事件（`engine.py:383`），但不属于类级别的事件系统。
- 通过 `highlight_events` 列表 + `_highlight_listeners` 回调列表对外通知高亮事件（`engine.py:371`）。

---

## 3. 类2：PythonFormulaEngine —— 纯Python公式引擎，向量化计算TDX风格技术指标与选股条件

**代码位置：** `core/formula_engine.py:581`

### 核心属性（3个）

| 属性 | 含义 | 代码引用 |
|------|------|---------|
| `data_query` | 可选数据查询对象，批量求值时的兜底数据源 | `formula_engine.py:591` |
| `_compiled_cache` | 公式编译缓存（LRU，默认1000条） | `formula_engine.py:592` |

### 核心方法（4个）

| 方法 | 干什么 | 代码引用 |
|------|--------|---------|
| `_compile(formula)` | 编译公式字符串为CompiledFormula，结果缓存 | `formula_engine.py:594` |
| `eval(formula, bars, args)` | 对单只股票K线求值，返回bool/标量/多输出字典 | `formula_engine.py:604` |
| `eval_batch(formula, symbols, ...)` | 批量对多只股票求值，返回{symbol: result} | `formula_engine.py:620` |

### 发出事件

- **无**。纯计算引擎，无副作用、无事件。

---

## 4. 类3：ConfigStore —— 配置表存储中心，加载、缓存、校验、热加载所有JSON配置表

**代码位置：** `core/table_engine.py:27`

### 核心属性（5个）

| 属性 | 含义 | 代码引用 |
|------|------|---------|
| `_tables` | 配置表内存缓存，{表名: 表数据dict} | `table_engine.py:32` |
| `_hashes` | 文件MD5哈希表，用于热加载变更检测 | `table_engine.py:33` |
| `_validators` | 自定义校验器注册表，{表名: 校验函数} | `table_engine.py:35` |
| `_config_dir` | 配置表所在目录路径 | `table_engine.py:31` |
| `_schema_validator` | 三级校验器（语法/逻辑/业务规则） | `table_engine.py:38` |

### 核心方法（5个）

| 方法 | 干什么 | 代码引用 |
|------|--------|---------|
| `load_all()` | 加载目录下所有JSON配置表并校验 | `table_engine.py:49` |
| `get(name, default)` | 按表名获取配置表数据 | `table_engine.py:248` |
| `check_hot_reload()` | 检测配置文件变更，热加载并三级校验 | `table_engine.py:326` |
| `get_layout_for_type(...)` | 根据节点类型+池类型查找UI布局配置 | `table_engine.py:257` |
| `validate_table_with_report(...)` | 校验配置表并返回含schema级别的富报告 | `table_engine.py:181` |

### 发出事件

- **无**。纯存储/查询，不主动推送事件。热加载通过返回变更表名列表通知调用方。

---

## 5. 类4：DynamicCellModel —— 通用节点数据模型，承载所有类型节点（状态池/条件/备选池等）的结构化数据

**代码位置：** `core/schemas.py:168`

### 核心属性（5个）

| 属性 | 含义 | 代码引用 |
|------|------|---------|
| `_data` | 已知字段数据字典（id/type/attr/pos等） | `schemas.py:179` |
| `_extra` | 未知字段扩展字典，存储未在field_definitions中声明的字段 | `schemas.py:180` |
| `_present_attrs` | 原始输入中存在的字段名集合（用于分辨缺省值与原值） | `schemas.py:181` |
| `cell_type` | 节点类型ID（整数，200=状态池/201=条件/202=备选池...） | `schemas.py:216` |
| `position` | 节点坐标位置（PositionModel对象） | `schemas.py:226` |

### 核心方法（5个）

| 方法 | 干什么 | 代码引用 |
|------|--------|---------|
| `from_dict(data)` | 从原始字典创建模型，自动解析attr位标志/位置/嵌套对象 | `schemas.py:184` |
| `to_dict()` | 序列化回字典，bit fields重新组合为attr整数 | `schemas.py:316` |
| `get(key, default)` | dict风格取值，支持默认值 | `schemas.py:285` |
| `__getitem__(key)` | dict风格下标访问 | `schemas.py:272` |
| `__getattr__(name)` | 属性风格访问，动态代理到_data/_extra | `schemas.py:299` |

### 发出事件

- **无**。纯数据模型，无行为。

---

## 6. 类5：DynamicFlowModel —— 通用边数据模型，承载节点间股票流转的规则、时机与位标志属性

**代码位置：** `core/schemas.py:459`

### 核心属性（5个）

| 属性 | 含义 | 代码引用 |
|------|------|---------|
| `_data` | 已知字段数据字典（from/to/attr/begin/end等） | `schemas.py:470` |
| `_extra` | 未知字段扩展字典 | `schemas.py:471` |
| `from_cell_id` | 源节点ID | `schemas.py:449` |
| `to_cell_id` | 目标节点ID | `schemas.py:450` |
| `mode_name` | 流转模式名（pass_through/move/copy等），由attr位标志动态解析 | `schemas.py:559` |

### 核心方法（5个）

| 方法 | 干什么 | 代码引用 |
|------|--------|---------|
| `from_dict(data)` | 从原始字典创建模型，自动解析attr位标志+字段别名归一化 | `schemas.py:475` |
| `to_dict()` | 序列化回字典，bit fields重新组合为attr整数 | `schemas.py:618` |
| `identify_mode()` | 根据flow_mode_registry的规则识别流转模式 | `schemas.py:555` |
| `to_int()` | 将当前boolean属性重新组合为attr整数 | `schemas.py:549` |
| `get(key, default)` | dict风格取值 | `schemas.py:589` |

### 发出事件

- **无**。纯数据模型，无行为。

---

## 7. 类关系图（文字版，极简）

```
                ┌──────────────┐
                │  ConfigStore │  ←  配置表（所有业务规则来源）
                └──────┬───────┘
                       │ 注入/读取
         ┌─────────────┼─────────────┐
         ▼             ▼             ▼
  ┌──────────┐  ┌─────────────┐  ┌───────────┐
  │ MetaEngine│→│PythonFormula│  │RuleEngine │ （表驱动引擎族）
  │  (主引擎) │  │   Engine    │  │  等辅助   │
  └─────┬────┘  └─────────────┘  └───────────┘
        │ 编译/执行
        ▼
  ┌──────────┐    连接     ┌───────────┐
  │DynamicCell│◄──────────►│DynamicFlow│
  │  (节点)   │   组成拓扑  │  (边)     │
  └──────────┘             └───────────┘
        ▲                        ▲
        │                        │
        └───── PoolMetaModel ────┘
            （股票池整体封装，非核心类）
```

**关系一句话：** ConfigStore 提供配置，MetaEngine 读取配置并驱动 DynamicCellModel + DynamicFlowModel 组成的拓扑执行，公式计算委托给 PythonFormulaEngine。

---

## 8. 股票池本质一句话概括（v3.5版）

**股票池是一个由节点（存股票）和边（带条件转股票）组成的有向图，在行情tick驱动下，按拓扑序逐条边执行公式过滤与状态流转，最终实现条件选股。**
