# v3.6 核心类精准定义（更概括 · 更精准）

---

## 1. 核心类选型说明

**选型标准：** 没有这5个类，股票池系统就跑不起来。

| 序号 | 类名 | 为什么是核心 |
|------|------|-------------|
| 1 | MetaEngine | 运行时主引擎，tick 驱动拓扑执行与股票流转 |
| 2 | FormulaRouter | 公式计算入口，双引擎路由+缓存，MetaEngine 直接依赖 |
| 3 | ConfigStore | 配置表存储中心，表驱动架构根基 |
| 4 | DynamicCellModel | 节点通用数据模型，图的基本构成单元 |
| 5 | DynamicFlowModel | 边通用数据模型，图的基本构成单元 |

**选型依据：** MetaEngine `__init__` 中直接初始化 `formula_router`（engine.py:399-402），公式求值统一走 FormulaRouter，PythonFormulaEngine 只是其内部执行引擎之一。

---

## 2. 类1：MetaEngine —— 股票池运行时主引擎，tick 驱动拓扑执行与股票流转

**代码位置：** `core/engine.py:298`

### 核心属性（5个）

| 属性 | 含义 | 代码引用 |
|------|------|---------|
| `tables` | 启动时加载的配置表快照 | `engine.py:346` |
| `_rt` | 运行时表命名空间，node_stocks 等状态收敛于此 | `engine.py:349` |
| `formula_router` | 公式路由器，双引擎公式求值入口 | `engine.py:372` |
| `_compiled_cache` | 编译期调度表缓存，{pool_config哈希: CompiledSchedule} | `engine.py:460` |
| `_latest_tick` | 最新行情快照 | `engine.py:462` |

### 核心方法（5个）

| 方法 | 干什么 | 代码引用 |
|------|--------|---------|
| `run_pool(pool_config, ...)` | 同步运行一次完整股票池计算（对外入口） | `engine.py:3815` |
| `_compile_pool(pool_config)` | 编译股票池为静态调度表（拓扑排序+边预计算+缓存） | `engine.py:1354` |
| `_run_tick_event_driven(...)` | 单 tick 核心执行：按处理计划逐条边执行 | `engine.py:3578` |
| `_process_edge_pipeline(ctx)` | 边流水线：gate→守卫→流转解析→变换→后处理 | `engine.py:2713` |
| `set_tq_adapter(a)` | 注入行情适配器，懒加载 formula_router | `engine.py:576` |

### 发出事件

- **高亮事件回调：** 通过 `highlight_events` 列表 + `_highlight_listeners` 回调列表对外通知（engine.py:371）
- **内部队列：** `_event_queue` / `_signal_queue` / `_alert_queue` 三个 asyncio.Queue 传递内部事件（engine.py:383）

---

## 3. 类2：FormulaRouter —— 公式路由器，按复杂度与周期选择执行引擎，管理结果缓存

**代码位置：** `core/formula_router.py:81`

### 核心属性（4个）

| 属性 | 含义 | 代码引用 |
|------|------|---------|
| `_python_engine` | Python 公式引擎实例 | `formula_router.py:100` |
| `_cache` | 公式结果缓存实例 | `formula_router.py:101` |
| `_routing_rules` | 引擎路由规则表（表驱动） | `formula_router.py:106` |
| `_hqchart_available` | HQChart 引擎可用标记 | `formula_router.py:112` |

### 核心方法（4个）

| 方法 | 干什么 | 代码引用 |
|------|--------|---------|
| `eval(formula, symbol, period, ...)` | 单股公式求值（带缓存+路由） | `formula_router.py:309` |
| `eval_batch(formula, symbols, ...)` | 批量公式求值（带缓存+路由） | `formula_router.py:451` |
| `eval_outvars(formula, symbol, ...)` | 单股公式求值，返回全部输出变量末值 | `formula_router.py:348` |
| `_resolve_engine(ctx)` | 按规则表匹配执行引擎（python/hqchart/error） | `formula_router.py:167` |

### 发出事件

- **无**。纯计算路由层，无副作用、无事件。

---

## 4. 类3：ConfigStore —— 配置表存储中心，加载缓存校验热加载

**代码位置：** `core/table_engine.py:27`

### 核心属性（5个）

| 属性 | 含义 | 代码引用 |
|------|------|---------|
| `_tables` | 配置表内存缓存，{表名: 表数据dict} | `table_engine.py:32` |
| `_hashes` | 文件MD5哈希表，热加载变更检测 | `table_engine.py:33` |
| `_config_dir` | 配置表所在目录路径 | `table_engine.py:31` |
| `_validators` | 自定义校验器注册表 | `table_engine.py:35` |
| `_schema_validator` | 配置校验器引用 | `table_engine.py:38` |

### 核心方法（5个）

| 方法 | 干什么 | 代码引用 |
|------|--------|---------|
| `load_all()` | 加载目录下所有JSON配置表并校验 | `table_engine.py:49` |
| `get(name, default)` | 按表名获取配置表数据 | `table_engine.py:248` |
| `check_hot_reload()` | 检测配置文件变更，热加载并校验 | `table_engine.py:326` |
| `validate_table_with_report(...)` | 校验配置表并返回含schema级别的富报告 | `table_engine.py:181` |
| `get_layout_for_type(...)` | 根据节点类型+池类型查找UI布局配置 | `table_engine.py:257` |

### 发出事件

- **无异步事件推送。** 热加载变更通过返回变更表名列表同步通知调用方。

---

## 5. 类4：DynamicCellModel —— 通用节点数据模型，承载所有类型节点的结构化数据

**代码位置：** `core/schemas.py:168`

### 核心属性（5个）

| 属性 | 含义 | 代码引用 |
|------|------|---------|
| `id` | 节点唯一标识 | `schemas.py:176` |
| `cell_type` | 节点类型ID（整数，200=状态池/201=条件/202=备选池...） | `schemas.py:216` |
| `position` | 节点坐标位置（PositionModel对象） | `schemas.py:226` |
| `_data` | 已知字段数据字典 | `schemas.py:179` |
| `_extra` | 未知字段扩展字典 | `schemas.py:180` |

### 核心方法（5个）

| 方法 | 干什么 | 代码引用 |
|------|--------|---------|
| `from_dict(data)` | 从原始字典创建模型，自动解析attr位标志/位置 | `schemas.py:184` |
| `to_dict()` | 序列化回字典，bit fields重组为attr整数 | `schemas.py:316` |
| `get(key, default)` | dict风格取值，支持默认值 | `schemas.py:285` |
| `__getitem__(key)` | dict风格下标访问 | `schemas.py:272` |
| `__getattr__(name)` | 属性风格访问，动态代理到_data/_extra | `schemas.py:299` |

### 发出事件

- **无**。纯数据模型，无行为。

---

## 6. 类5：DynamicFlowModel —— 通用边数据模型，承载节点间股票流转规则

**代码位置：** `core/schemas.py:459`

### 核心属性（5个）

| 属性 | 含义 | 代码引用 |
|------|------|---------|
| `from_cell_id` | 源节点ID | `schemas.py:449` |
| `to_cell_id` | 目标节点ID | `schemas.py:450` |
| `mode_name` | 流转模式名（@property，由attr位标志动态解析） | `schemas.py:559` |
| `_data` | 已知字段数据字典 | `schemas.py:470` |
| `_extra` | 未知字段扩展字典 | `schemas.py:471` |

### 核心方法（5个）

| 方法 | 干什么 | 代码引用 |
|------|--------|---------|
| `from_dict(data)` | 从原始字典创建模型，解析attr位标志+字段别名 | `schemas.py:475` |
| `to_dict()` | 序列化回字典，bit fields重组为attr整数 | `schemas.py:618` |
| `from_int(attr_int)` | 从attr整数创建模型（类方法） | `schemas.py:529` |
| `to_int()` | 将当前boolean属性重新组合为attr整数 | `schemas.py:549` |
| `identify_mode()` | 根据规则识别流转模式 | `schemas.py:555` |

### 发出事件

- **无**。纯数据模型，无行为。

---

## 7. 类关系图（文字版）

```
                ┌──────────────┐
                │  ConfigStore │  ←  配置来源
                └──────┬───────┘
                       │ 注入/读取
         ┌─────────────┴─────────────┐
         ▼                           ▼
  ┌──────────┐               ┌──────────────┐
  │ MetaEngine│ ──────────► │ FormulaRouter │  ←  公式路由层
  │  (主引擎) │   委托求值   │  (双引擎入口)  │
  └─────┬────┘               └──────┬───────┘
        │ 编译/执行                  │ 内部包含
        ▼                            ▼
  ┌──────────┐    连接     ┌───────────┐
  │DynamicCell│◄──────────►│DynamicFlow│
  │  (节点)   │   组成拓扑  │  (边)     │
  └──────────┘             └───────────┘
```

**关系一句话：** ConfigStore 提供配置，MetaEngine 读取配置并驱动由 DynamicCellModel + DynamicFlowModel 组成的拓扑执行，公式计算委托给 FormulaRouter。

---

## 8. 股票池本质一句话概括（v3.6版，30字）

**股票池是行情驱动的有向图过滤器，股票经边公式筛选后在节点间流转。**
