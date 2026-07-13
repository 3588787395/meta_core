# 股票池引擎代码理解笔记 v2.8（第四版）

> 版本：v2.8
> 日期：2026-07-01
> 性质：纯理解笔记，不做设计建议
> 上一版：spec_v2.7.md

---

## 1. 上一版5个偏差修正

### 1.1 偏差一：_data_cache 不是单一用途缓存

v2.7 只重点讲了 `_filter_cache`，对 `_data_cache` 一笔带过，误认为它只是普通数据缓存。实际 `_data_cache` 是**4类 TTL 缓存的统一容器**，用 key 前缀区分缓存类型，每类有独立的 TTL 策略。

- kline 缓存：TTL=300秒，缓存多时间框架K线数据
- snapshot 缓存：TTL=5秒，缓存实时行情快照
- financial 缓存：TTL=86400秒，缓存财务数据
- sector 缓存：TTL=3600秒，缓存板块成员列表

代码位置：engine.py:406-410

### 1.2 偏差二：pre_tick/post_tick 不是硬编码阶段

v2.7 认为 pre_tick 只是"数据注入"，post_tick 只是"事件发射"。实际两者都是**表驱动流水线**，各自由独立的 JSON 配置定义 stage 列表，每个 stage 可独立启用/禁用，handler 通过反射调用。

- pre_tick 有 2 个 stage：bar_data_inject + minute_aggregator_feed
- post_tick 有 4 个 stage：pk_ranking + analysis_angles + dashboard + alerts

代码位置：pre_tick_pipeline.json:4-23，post_tick_pipeline.json:4-52

### 1.3 偏差三：_should_trigger_edge 不是单层 gate

v2.7 对时机门控的理解不完整，只提到了"gate检查"。实际 `_should_trigger_edge` 是**三层守卫**：

1. replay_guard：回放模式下的 begin/end/interval 守门
2. tdx_guard：starttype 表驱动守门（实时模式也用）
3. duration_guard：cxtype 持续时长守门

而且三层守卫在**实时模式和回放模式的执行路径不同**：回放模式三层都走，实时模式跳过 replay_guard。

代码位置：engine.py:1757-1785

### 1.4 偏差四：transform unit 不是运行期执行的

v2.7 误认为变换单元在运行期有特殊的执行逻辑。实际**变换单元分组只发生在编译期**，运行期变换单元被拆解回独立边，和 standalone_edges 一起进入统一的 processing_plan 处理。运行期不存在"变换单元执行路径"。

编译期用途：
- 识别三元组结构（入边+枢纽+出边）
- 优化缓存键（以枢纽节点为粒度）
- 辅助拓扑模式识别

代码位置：engine.py:2217-2247（_group_transformation_units）

### 1.5 偏差五：nset_dispatch 不是只有 TDX 用

v2.7 认为 nset_dispatch 只是 TDX 条件的分派机制。实际 `dispatch.json` 覆盖了**所有条件类型**的分派，包括通用公式类型（INDICATOR、RANKING、SECTOR_MEMBERSHIP 等），nset=0~5 只是 TDX 特有的子分类。

完整的分派层级：
```
dispatch_rules（12种通用条件类型）
    ↓
nset_dispatch（6种TDX nset类型，0~5）
    ↓
evaluator_dispatch（评估器方法名映射）
```

代码位置：dispatch.json:3-170

---

## 2. 7个方向的深入理解

### 2.1 _data_cache 数据缓存层

#### 结构

`_data_cache` 是 `LRUCache` 类的实例，继承自 dict，内部用 OrderedDict 实现 LRU 策略。

代码位置：engine.py:98-126（LRUCache类定义），engine.py:406-410（初始化）

#### 4类 TTL 缓存

| 缓存类型 | key 前缀 | 默认 TTL | 用途 | 配置位置 |
|---------|----------|----------|------|----------|
| kline | `kline:` | 300秒 | 多时间框架K线数据 | data_config.json:86 |
| snapshot | `snap:` | 5秒 | 实时行情快照 | data_config.json:84 |
| financial | `fin:` | 86400秒 | 财务数据 | data_config.json:88 |
| sector | `sector:` | 3600秒 | 板块成员列表 | data_config.json:90 |

TTL 匹配逻辑：set 时如果未指定 ttl，按 key 前缀匹配 `_ttl_map`，找到第一个匹配的前缀就用对应的 TTL。

代码位置：engine.py:112-115（TTL前缀匹配）

#### key 格式

根据 data_config.json:93-98：
```
snapshot: snap:{market}:{code}:{timeframe}
kline:    kline:{market}:{code}:{timeframe}:{count}
financial: fin:{market}:{code}:{report_type}
sector:   sector:{sector_code}
```

但实际使用中，kline 缓存的 key 格式略有不同，用的是 `kline:{tf}:{codes_hash}`（按时间框架+股票集合哈希缓存）。

代码位置：engine.py:2334-2335（_fetch_multi_timeframe_cached 中的 key 构造）

#### LRU+TTL 双重策略

- **LRU 淘汰**：超过 max_entries（默认10000）时，淘汰最久未访问的条目
- **TTL 过期**：get 时检查时间戳，过期则删除并返回默认值
- **存储结构**：每个 entry = `{"data": ..., "ts": time.time(), "ttl": ...}`

代码位置：engine.py:103-115

#### 和 _latest_tick 的关系

`_data_cache` 和 `_latest_tick` 是**两个独立的数据层**：

- `_latest_tick`：行情唯一真相源，每次 tick 刷新，存储最新的逐笔/分钟行情数据（engine.py:462）
- `_data_cache`：通用数据缓存层，存储 K线/快照/财务/板块等可缓存数据，有 TTL

两者的关系：`_latest_tick` 是热路径数据（每 tick 必用），`_data_cache` 是按需查询的冷数据（公式求值时才查）。

#### 和 formula_engine 的关系

公式求值时，`formula_router` 通过 `DataQuery` 获取 K线数据，`DataQuery` 从本地 parquet 文件 + minute_aggregator 内存数据读取。`_data_cache` 是 engine 层的缓存，主要用于 `_fetch_multi_timeframe_cached` 等 engine 内部方法，不直接被 formula_engine 使用。

代码位置：engine.py:2325-2346（_fetch_multi_timeframe_cached）

#### 缓存失效机制

1. **TTL 过期**：get 时自动检查，过期删除
2. **LRU 淘汰**：超过最大条目数时淘汰最久未用
3. **自然失效**：key 变化导致旧缓存永不被命中（如 codes_hash 变化）

---

### 2.2 pre_tick / post_tick 流水线

#### pre_tick 流水线

**职责定位**：数据层 — 负责外部数据注入，将最新行情数据准备好供计算层使用。

代码位置：engine.py:3547-3575

**配置文件**：config/pre_tick_pipeline.json

**2 个 stage**：

| stage | op | 功能 | handler 位置 |
|-------|----|------|-------------|
| stage_bar_data_inject | stage_bar_data_inject | 将当前 bar 数据注入到 node_stocks | _pipeline.stage_bar_data_inject |
| stage_minute_aggregator_feed | stage_minute_aggregator_feed | 将 bar 数据喂给 minute_aggregator | _pipeline.stage_minute_aggregator_feed |

**执行流程**：
1. 遍历 `self._pre_tick_pipeline` 列表
2. 检查 stage.enabled，跳过禁用的 stage
3. 从 `_edge_cfg.stage_ops` 查 op → method_name 映射
4. 从 `_pipeline` 模块反射获取 handler
5. 调用 handler，返回 coroutine 则 await

代码位置：engine.py:3563-3575

#### post_tick 流水线

**职责定位**：后处理层 — 股票流转完成后，计算 PK排名、分析角度、看板数据、告警事件。

代码位置：engine.py:3724-3743

**配置文件**：config/post_tick_pipeline.json

**4 个 stage**：

| stage | op | config_table | write_to | 功能 |
|-------|----|-------------|----------|------|
| pk_ranking | pk_ranking | pk_config | _pk_rankings | 多维度加权评分排名 |
| analysis_angles | analysis_angles | analysis_config | _angle_results | 多分析角度分组排序 |
| dashboard | dashboard | dashboard_schema | _dashboard_data | 看盘面板数据汇总 |
| alerts | alerts | alert_rules | _alert_events | 告警事件检测与收集 |

**执行流程**：
1. 遍历 `self._post_tick_pipeline` 列表
2. 检查 stage.enabled，跳过禁用的 stage
3. 从 `_edge_cfg.stage_ops` 查 op → method_name 映射
4. 从 `_builtins_post_tick` 模块反射获取 handler
5. 读取 config_table 配置作为 handler 输入
6. 调用 handler，传入当前数据和上下文
7. 如果有 write_to，则 setattr 写入运行时表

代码位置：engine.py:3729-3743

#### 数据注入、PK排名、分析角度、看板、告警——和 pipeline 的关系

- **数据注入**：在 pre_tick 流水线的 stage_bar_data_inject 中完成
- **PK排名**：在 post_tick 流水线的 pk_ranking stage 中完成
- **分析角度**：在 post_tick 流水线的 analysis_angles stage 中完成
- **看板**：在 post_tick 流水线的 dashboard stage 中完成
- **告警**：在 post_tick 流水线的 alerts stage 中完成

全部都是表驱动流水线的一部分，不是硬编码在核心循环里。

#### 和核心 tick 循环的关系

完整的 tick 执行顺序（engine.py:3650-3661）：

```
_tick()
  ↓
_pre_tick()  → 数据注入 + 分钟线聚合
  ↓
_run_tick_event_driven()  → 核心：时机门控 + 条件过滤 + 股票流转
  ↓
_update_trackers()  → 更新持仓跟踪
  ↓
_emit_transfer_events()  → 发射流转事件
  ↓
_post_tick()  → PK排名 + 分析角度 + 看板 + 告警
```

pre_tick 在核心计算之前，post_tick 在核心计算之后。核心计算层（_run_tick_event_driven）不关心数据怎么来的，也不关心结果怎么展示。

---

### 2.3 _should_trigger_edge 时机门控完整逻辑

#### 三层守卫总览

```
_should_trigger_edge(edge, ctx)
  ↓
【模式判断】mode_id == 'replay' ?
  │
  ├─ 是 → replay_guard（begin/end/interval）
  │        ↓
  │     tdx_guard（starttype 守门）
  │        ↓
  │     duration_guard（cxtype 守门）
  │
  └─ 否 → tdx_guard（starttype 守门）
           ↓
        duration_guard（cxtype 守门）
```

代码位置：engine.py:1757-1785

#### 第一层：replay_guard（回放守卫）

**仅回放模式执行**。检查三个维度：begin（开始条件）、end（结束条件）、interval（最小间隔）。

代码位置：engine.py:1033-1093（_should_fire_flow_replay）

**begin 守门**：
- 从 `timing.json:simulator.begin_type_handlers` 查 begin_type → handler 名
- 从 `evaluator_primitives` 查 handler 名 → primitive 定义
- 调用 `_eval_timing_primitive` 求值

begin_type 语义：
- 0/6: 总是触发（无 begin 限制）
- 3: 从开始经过 begint 秒后触发
- 5: begint 秒后触发（特定日期）
- 7: 每天 begint 时刻触发（hhmmss）

**end 守门**：
- 从 `timing.json:simulator.end_type_handlers` 查 end_type → handler 名
- 同样走 primitive 求值

end_type 语义：
- 0: 无限（2147483647）
- 1: 从首次触发起 endt 秒后停止
- 2: 只执行一次

**interval 守门**：
- interval_sec > 0 时检查上次触发时间
- `cur_ts - last_fire_ts < interval_sec` 则拒绝触发

涉及的运行时表：
- `_flow_first_fire_ts`：首次触发时间戳（end_type=1/2 用）
- `_flow_last_fire_ts`：上次触发时间戳（interval 用）

代码位置：engine.py:1067-1091

#### 第二层：tdx_guard（starttype 守门）

**实时模式和回放模式都执行**。检查 starttype（开始类型）。

代码位置：engine.py:781-794（_tdx_should_execute）

**执行逻辑**：
1. 从 edge.params 读取 starttype
2. 查 `timing.json:starttype_rules` 得规则定义
3. 取 rule.primitive 作为时机原语名
4. 调用 `_eval_timing_primitive` 求值

**时机原语系统**：
`_eval_timing_primitive` 是统一的时机求值入口，内联了 7 种原语：
- always：总是返回 True/False
- elapsed：经过指定时间
- in_range：在指定时间范围内
- not_elapsed：未经过指定时间
- 等等...

原语求值时支持两种模式：
- namespace='gate'/'duration'：实时模式，cur_ts = self._now()
- namespace='begin'/'end'：回放模式，cur_ts 从 ctx 传入

代码位置：engine.py:829-1011（_eval_timing_primitive 及相关方法）

#### 第三层：duration_guard（持续时长守门）

**实时模式和回放模式都执行**。检查 cxtype（持续类型）。

代码位置：engine.py:1013-1030（_tdx_check_duration）

**执行逻辑**：
1. 从 edge.params 读取 cxtype
2. 查 `timing.json:cxtype_rules` 得规则定义
3. 取 rule.is_expired 作为评估器名
4. 查 `simulator.evaluator_primitives` 得 primitive 定义
5. 调用 `_eval_timing_primitive` 求值，namespace='duration'

**返回值注意**：
`_tdx_check_duration` 返回 True 表示"已过期/不通过"，所以 `_should_trigger_edge` 中是：
```python
if self._tdx_check_duration(edge):
    return False  # 过期了，不触发
```

代码位置：engine.py:1780-1781

#### begin / end / interval / duration 分别怎么算

| 概念 | 位置 | 计算方式 | 用途 |
|------|------|---------|------|
| begin | replay_guard | begin_type + begint，回放模式下算什么时候"开始可以触发" | 回放模式的时间窗起点 |
| end | replay_guard | end_type + endt，回放模式下算什么时候"停止触发" | 回放模式的时间窗终点 |
| interval | replay_guard | interval_sec，两次触发的最小间隔 | 回放模式的触发频率限制 |
| duration | duration_guard | cxtype + endt，持续时长守门 | 实时/回放都用，控制边的有效时长 |

#### 实时模式和回放模式的不同

| 维度 | 实时模式 | 回放模式 |
|------|---------|---------|
| replay_guard | 不执行 | 执行（begin+end+interval） |
| tdx_guard | 执行，cur_ts=实时时间 | 执行，cur_ts=回放bar时间 |
| duration_guard | 执行 | 执行 |
| 时间源 | 系统时间 / 虚拟时钟 | current_bar_time |
| first_fire_ts 记录 | 有 | 有 |
| last_fire_ts 记录 | 有 | 有（interval 用） |

代码位置：engine.py:1765-1781（模式分支）

---

### 2.4 变换单元（transform unit）完整机制

#### 三元组结构到底是什么

变换单元是**编译期对拓扑结构的识别单位**，由三个元素组成：

```
{
  "in_edge": 条件转移边（入边，conditional）,
  "hub_id":  枢纽条件节点ID,
  "out_edge": 无条件转移边（出边，unconditional）
}
```

也就是：**条件转移边 + 转移条件节点 + 无条件转移边**，形成一个"入边→枢纽→出边"的三元组结构。

代码位置：engine.py:2218-2220（注释说明），engine.py:2244（构造三元组）

#### hub 节点是什么

hub 节点（枢纽节点）是位于三元组中间的**条件节点**，它的类型必须属于 `hub_node_types` 列表。

根据 edge_semantics.json:
```json
"hub_node_types": ["tdx_condition", "transfer_condition", "dzh_condition_pool"]
```

也就是：通达信条件节点、转移条件节点、大智慧条件池节点。

这些节点的特点：
- 入边数 = 1（来自状态池的条件转移边）
- 出边数 = 1（去往目标状态池的无条件转移边）
- 本身不存储股票，只是一个条件计算节点

代码位置：edge_semantics.json:62-70

#### 分组算法是怎么分组的

`_group_transformation_units` 方法实现分组：

代码位置：engine.py:2217-2247

**算法步骤**：

1. 从 `edge_semantics_cfg.transformation_unit.hub_node_types` 读取枢纽节点类型集合
2. 遍历所有边，用 `_resolve_edge_context` 解析每条边的源/目标节点类型
3. 分类：
   - 如果目标节点类型 ∈ hub_types → 入边候选，加入 in_edges_by_hub[target_id]
   - 如果源节点类型 ∈ hub_types → 出边候选，加入 out_edges_by_hub[source_id]
4. 对每个 hub_id：
   - 如果入边数 == 1 且 出边数 == 1 → 成功配对为一个变换单元
   - 否则 → 无法配对，保留为独立边
5. 返回 (units, standalone_edges)

**配对条件很严格**：必须恰好 1 条入边 + 1 条出边。多入边、多出边的条件节点都不能形成变换单元。

#### 编译期怎么用

编译期（`_compile_topology_ctx` / `_get_compiled`）中：

1. 调用 `_group_transformation_units` 得到 units 和 standalone_edges
2. 按源节点深度对 units 排序
3. 调用 `_build_processing_plan` 将 units 和 standalone_edges 统一为 processing_plan
   - 变换单元的入边 → filter_type = unit_cfg.in_filter_type（默认 'conditional'）
   - 变换单元的出边 → filter_type = unit_cfg.out_filter_type（默认 'unconditional'）
   - 独立边 → filter_type 由 edge_semantics.json 决定
4. processing_plan 存入 CompiledSchedule.processing_plan

代码位置：engine.py:1245-1257（编译期分组），engine.py:1289-1305（_build_processing_plan）

#### 运行期为什么没用

运行期（`_run_tick_event_driven`）中：

- 边的处理是按拓扑序遍历 `compiled.out_edges`，每条边独立处理
- 不区分"变换单元的边"和"独立边"
- 都走统一的 `_should_trigger_edge` → `_process_edge_pipeline` 路径
- `compiled.units` 字段在运行期**没有被读取**

**那变换单元存在的意义是什么？**

主要是编译期优化和模式识别用途：
1. **拓扑模式识别**：辅助识别股票池的拓扑模式（如单条件链、多条件并联等）
2. **缓存粒度优化**：`_unit_cache_key` 以边为粒度，但概念上可以扩展到以枢纽节点为粒度
3. **未来优化点**：设计上预留了变换单元级执行优化的可能性，但当前版本还没用到

代码位置：engine.py:2293-2295（_unit_cache_key 方法，当前仍按边粒度）

---

### 2.5 nset_dispatch 策略分派体系

#### dispatch.json 里有什么

dispatch.json 包含 4 大部分：

代码位置：dispatch.json:1-171

**1. dispatch_rules（12种通用条件类型）**

每种条件类型定义：
- condition_type：类型名
- bit_mask / bit：位掩码（用于属性位运算）
- gateway：网关（路由到哪个处理路径）
- name：中文名称
- required_fields：必需字段
- extra：额外参数

12 种类型：
- INDICATOR（指标条件）→ gateway: formula_eval
- RANKING（排序条件）→ gateway: formula_eval
- SECTOR_MEMBERSHIP（板块成员）→ gateway: sector_filter
- REVERSE_TRANSFER（反向转移）→ gateway: formula_eval
- CROSS_SECTION（横向统计）→ gateway: cross_section_eval
- BASIC_CONDITION（基本条件）→ gateway: basic_filter
- PASSTHROUGH（无条件直通）→ gateway: pass_through
- FORMULA（公式类型条件）→ gateway: formula_eval
- TDX_INDICATOR（TDX技术指标，nset=0）→ gateway: tdx_eval_nset0
- TDX_CONDITION_FORMULA（TDX条件选股，nset=1）→ gateway: tdx_eval_nset1
- TDX_EXPERT_SYSTEM（TDX专家系统，nset=2）→ gateway: tdx_eval_nset2
- TDX_FINANCIAL（TDX财务选股，nset=3）→ gateway: tdx_eval_nset3
- TDX_MARKET（TDX实时行情，nset=4）→ gateway: tdx_eval_nset4
- TDX_SETOP（TDX集合运算，nset=5）→ gateway: tdx_eval_nset5

**2. nset_dispatch（6种TDX nset类型）**

nset=0~5 分别对应：
- 0: TDX技术指标 → evaluator: eval_formula_nset
- 1: TDX条件选股公式 → evaluator: eval_formula_nset
- 2: TDX专家系统公式 → evaluator: eval_formula_nset
- 3: TDX最新财务选股 → evaluator: eval_scalar_nset
- 4: TDX实时行情选股 → evaluator: eval_scalar_nset
- 5: TDX集合运算 → evaluator: eval_nset5_set_operation

**3. evaluator_dispatch（评估器方法名映射）**

条件类型 → 评估器方法名 的扁平映射。

**4. attr_dispatch（属性位分派）**

indicator_condition / basic_condition / ranking_condition 等的位掩码定义。

#### engines.json 里有什么

engines.json 定义了 9 个时间周期引擎：

代码位置：engines.json:1

| 引擎ID | 名称 | 粒度 | 兼容网关 |
|--------|------|------|---------|
| tick | 分笔成交 | 0ms | formula_eval, pass_through |
| 1min | 1分钟线 | 60s | formula_eval, cross_section_eval, pass_through |
| 5min | 5分钟线 | 300s | formula_eval, cross_section_eval, pass_through |
| 15min | 15分钟线 | 900s | formula_eval, cross_section_eval, pass_through |
| 30min | 30分钟线 | 1800s | formula_eval, cross_section_eval, pass_through |
| 60min | 60分钟线 | 3600s | formula_eval, cross_section_eval, pass_through |
| daily | 日线（默认） | 86400s | formula_eval, sector_filter, cross_section_eval, basic_filter, pass_through |
| weekly | 周线 | 604800s | formula_eval, pass_through |
| monthly | 月线 | 2592000s | formula_eval, pass_through |

每个引擎定义：fn（求值函数名）、data_source（数据源）、compatible_gateways（兼容的网关类型）、is_default（是否默认）。

#### 策略分派是怎么工作的

**编译期分派**（_compile_edge_spec）：

代码位置：engine.py:1478-1526

1. 从 edge.params 解析 tdx_func.nset
2. 查 `_nset_dispatch[nset_key]` 得 nset_entry
3. 取 nset_entry.dispatch_key
4. 查 `self.dispatch_index[dispatch_key]` 得 dispatch_rule
5. 把 nset、accode、dispatch_key、dispatch_mask、gateway、engine 等存入 edge_filter_spec
6. 预解析 strategy（策略参数）

**运行期分派**（_filter_conditional → HR 分派）：

代码位置：engine.py:2528-2618

1. 读取编译期 edge_filter_spec
2. 取 strategy 和 handler_name
3. 从 `_HR`（native.builtins 注册表）查 handler
4. 调用 handler 执行实际的条件计算

如果是 TDX 条件，还会经过 `_dispatch_tdx_condition`：
- 取 nset → 查 `_nset_dispatch[nset]` → 取 evaluator 名
- 从 `tdx_evaluators` 模块反射获取评估器
- 调用评估器执行 TDX 特有计算

代码位置：engine.py:1993-1998（_dispatch_tdx_condition）

#### 和 filter / edge / node 的关系

```
edge（边）
  ↓ 1:1
edge_filter_spec（编译期预计算的筛选分派规则）
  ↓ 包含
filter_type（conditional / unconditional / formula_eval）
  ↓ conditional 时
nset → dispatch_key → gateway → evaluator
  ↓
filter（筛选执行）→ 决定哪些股票通过这条边转移到下一个节点
```

- **edge**：拓扑结构上的边，定义股票从哪来、到哪去
- **filter**：边的筛选逻辑，决定哪些股票能通过
- **nset_dispatch**：筛选逻辑的一种（TDX条件的分派机制）
- **node**：节点，存储股票的地方

边连接节点，筛选决定哪些股票能从源节点流向目标节点。

---

### 2.6 formula_router / DataQuery 完整架构

#### formula_router 是什么

`FormulaRouter` 是**公式求值的统一入口和路由分发器**。它根据公式复杂度与数据周期，在 Python 公式引擎与 HQChart C++ 引擎之间做显式路由，并集成公式结果缓存。

代码位置：formula_router.py:81-233

**核心职责**：
1. 公式复杂度分析（simple / complex）
2. 引擎路由决策（python / hqchart）
3. 统一调用接口（eval / eval_outvars / eval_batch）
4. 公式结果缓存

#### DataQuery 是什么

`DataQuery` 是**统一 K 线数据查询入口**。它只读取本地 parquet 文件 + MinuteAggregator 内存数据，不提供任何下载、网络回退或数据补齐能力。

代码位置：services/data.py:39-131

**核心职责**：
1. 加载历史 K 线数据（从 parquet 文件）
2. 加载今日分钟线数据（从 minute_aggregator）
3. 周期对齐与裁剪
4. 标准化列名和格式

支持的周期：1m / 5m / 15m / 30m / 60m / 1d / 1wk / 1mon

#### formula_engine 和它们是什么关系

```
formula_router（路由层）
  ├─ python_engine: PythonFormulaEngine（纯Python公式引擎）
  └─ hqchart_provider: HQChartProvider（C++引擎封装）
        ↓
      DataQuery（K线数据查询）
        ↓
      parquet文件 + minute_aggregator
```

- `PythonFormulaEngine`：纯 Python 实现的公式解释器
- `HQChartProvider`：HQChart C++ 引擎的 Python 封装
- `DataQuery`：为两个引擎提供 K 线数据
- `FormulaRouter`：在两个引擎之间做路由决策

代码位置：formula_router.py:31-33（import 语句）

#### 公式求值的完整调用链

**从 engine 层调用开始**：

```
_filter_formula_eval(ctx)  [engine.py:2629]
  ↓
formula = _filter_spec.formula  [编译期预解析的公式]
symbols = 源节点股票列表
period = 公式周期
  ↓
_run_formula_eval_batch_sync(formula, symbols, period, current_bar_data)  [engine.py:1797]
  ↓ （async → sync 转换）
formula_router.eval_batch(formula, symbols, period, context={'bars': current_bar_data})
  ↓
FormulaRouter.eval_batch()  [formula_router.py]
  ↓
1. 分析公式复杂度 _analyze_complexity(formula)
   └─ 分词 → 检查所有函数是否都在 simple_functions 中
  ↓
2. 路由决策 _resolve_engine(ctx)
   └─ 查 formula_routing.json 规则表 → 返回 "python" 或 "hqchart"
  ↓
3. 查缓存 FormulaCache
   ├─ 命中 → 直接返回
   └─ 未命中 → 继续
  ↓
4. _dispatch_engine_call(engine, "eval_batch", ...)
   └─ 查 engine_methods 表 → 反射调用对应方法
  ↓
5a. Python 引擎路径：
    _python_engine.eval_batch()
      ↓
    PythonFormulaEngine 执行公式计算
      ↓
    调用 DataQuery 获取 K线数据
      ↓
    返回结果

5b. HQChart 引擎路径：
    _hqchart_provider.eval_indicator_outvars_async()
      ↓
    HQChart C++ 引擎执行公式计算
      ↓
    返回结果
  ↓
6. 写入 FormulaCache（按周期 TTL）
  ↓
7. 返回结果
```

**关键点**：
- 路由决策是**预先做出**的，失败时不切换路径（除非 HQChart 不可用且公式是简单公式）
- 简单公式 + 1m/tick 周期 → Python 引擎
- 其他情况 → HQChart 引擎
- 分钟闭合时由调用方触发 `invalidate_on_minute_close`

代码位置：formula_router.py:1-248（类定义和核心方法）

---

### 2.7 runtime_tables 代理机制

#### _rt 命名空间是什么

`_rt` 是 MetaEngine 实例上的一个字典，作为**运行时表的统一存储命名空间**。所有运行时表（如 node_stocks、_filter_cache、_pk_rankings 等）都存储在 `self._rt` 中，而不是直接作为实例属性。

代码位置：engine.py:349（self._rt = {}），engine.py:500-532（_init_runtime_tables）

#### 运行时表一等公民化是什么意思

"运行时表一等公民化"是指：

1. **统一存储**：所有运行时状态都收敛到 `self._rt` 字典，不再散落在各个实例属性中
2. **schema 声明**：每张运行时表都在 `runtime_tables_schema.json` 中声明结构、读写时机、生命周期
3. **代理访问**：通过 `__getattr__` / `__setattr__` 实现透明代理，旧代码的 `self.node_stocks` 写法不变
4. **可枚举**：可以遍历 `self._rt` 列出所有运行时状态
5. **可序列化**：理论上可以整体导出/导入运行时状态

设计意图：把"运行时状态"从"对象属性"提升为"一等公民的数据表"，便于管理、调试、持久化。

代码位置：engine.py:347-349（注释说明）

#### table_engine 和 runtime_tables 的关系

`table_engine.py` 中的 `ConfigStore` 是**配置表的存储和加载器**，负责加载 config/ 目录下的所有 JSON 配置表。

`runtime_tables` 是**运行时状态表**，存储在 engine 实例的 `_rt` 字典中。

两者的关系：
- ConfigStore 加载的是**静态配置表**（只读，来自 JSON 文件）
- runtime_tables 是**动态运行时表**（可读写，内存中）
- runtime_tables_schema.json 是配置表，声明运行时表的元信息
- MetaEngine 通过 `self.tables` 访问 ConfigStore 中的配置表
- MetaEngine 通过 `self._rt` 或代理属性访问运行时表

```
ConfigStore（配置表，只读，来自JSON）
  ├─ runtime_tables_schema.json（运行时表的元信息声明）
  ├─ dispatch.json
  ├─ engines.json
  └─ ... 其他配置表

MetaEngine._rt（运行时表，可读写，内存中）
  ├─ node_stocks
  ├─ _filter_cache
  ├─ _pk_rankings
  └─ ... 其他运行时表
```

#### 有哪些运行时表

根据 runtime_tables_schema.json，共有 27 张运行时表：

| 表名 | 类型 | 生命周期 | 说明 |
|------|------|---------|------|
| node_stocks | Dict[str, List] | per_session | 核心：节点股票列表 |
| _flow_duration_starts | Dict[str, datetime] | per_session | cxtype=1 持续窗口起始 |
| _flow_exec_counts | Dict[str, int] | per_session | cxtype=2 执行计数 |
| _flow_first_fire_ts | Dict[str, float] | per_session | flow 首次触发时间戳 |
| _flow_last_fire_ts | Dict[str, float] | per_session | flow 上次触发时间戳 |
| _pool_start_time | datetime | per_session | 池执行起始时刻 |
| _filter_cache | LRUCache | per_tick | 条件边筛选结果缓存 |
| _last_snapshot | Dict[str, frozenset] | per_tick | 节点股票快照（变更检测用） |
| _last_bar_hash | str | per_tick | 上次行情数据 hash |
| _current_bar_hash | str | per_tick | 当前行情数据 hash |
| _first_run | bool | per_session | 首次执行标记 |
| _pk_rankings | Dict | per_tick | PK 排名结果 |
| _angle_results | Dict[str, Dict] | per_tick | 多分析角度结果 |
| _dashboard_data | Dict[str, Dict] | per_tick | 看盘面板数据 |
| _alert_events | List[dict] | per_tick | 告警事件列表 |
| _alert_queue | asyncio.Queue | per_session | 告警异步队列 |
| _alert_cooldown | Dict[Tuple, float] | per_session | 告警冷却时间 |
| _trackers | Dict[str, Dict] | per_session | 持仓跟踪 |
| _signal_events | List | per_session | 交易信号记录 |
| _signal_queue | asyncio.Queue | per_session | 信号异步队列 |
| _event_queue | asyncio.Queue | per_session | 事件流队列 |
| _exit_tracker_cache | Dict[Tuple, dict] | per_tick | move 出池时缓存的旧 tracker |
| _data_cache | LRUCache | per_session | 数据缓存表（TTL） |
| _loop_node_stocks | Dict[str, List] | per_session | run_loop 模式节点股票状态 |
| _current_time_source | Optional[dict] | per_session | 当前时间源配置 |
| _current_bar_time | Optional[datetime] | per_tick | 回放当前K线时间 |
| _virtual_clock | Optional[float] | per_tick | 仿真虚拟时钟 |
| _dirty_nodes | set | per_tick | 脏节点集合 |
| _node_snapshots | Dict[str, frozenset] | per_session | 节点股票快照 |

代码位置：runtime_tables_schema.json:5-266

#### 代理机制怎么实现的

通过重写 `__setattr__` 和 `__getattr__` 实现透明代理：

**__setattr__**（engine.py:534-541）：
- 如果 name 在 `_RUNTIME_TABLE_NAMES` 中 → 写入 `self._rt[name]`
- 否则 → 走正常的 super().__setattr__

**__getattr__**（engine.py:543-562）：
- 仅在正常属性查找失败时调用
- 如果 name 在 `_RUNTIME_TABLE_NAMES` 中 → 从 `self._rt[name]` 读取
- 如果 name 在 `_capabilities` 中 → 返回能力实例
- 否则 → 抛 AttributeError

**引导顺序**（engine.py:347-481）：
1. `__init__` 中先设置 `self._rt = {}`（直接 object.__setattr__，避免递归）
2. 设置 `self._rt_initialized = False`
3. 初始化过程中，运行时表属性的写入会直接进 _rt
4. 调用 `_init_runtime_tables()` 按 schema 初始化默认值
5. 设置 `self._rt_initialized = True`
6. 将已初始化的运行时表同步到 _rt（引用同一对象，非拷贝）

代码位置：engine.py:534-562（代理实现），engine.py:470-481（初始化同步）

---

## 3. 完整调用链：从数据更新到公式求值到过滤到事件发射

### 3.1 全链路总览

```
外部数据更新（K线/行情/财务）
  ↓
pre_tick 流水线
  ├─ stage_bar_data_inject（数据注入 node_stocks）
  └─ stage_minute_aggregator_feed（喂给分钟线聚合器）
  ↓
_refresh_latest_tick（更新 latest_tick + 置 data_dirty）
  ↓
_run_tick_event_driven（核心计算层）
  ├─ Phase 1: 编译期缓存 CompiledSchedule
  └─ Phase 2: 按拓扑序遍历边
        ↓
        每条边的处理：
        ├─ _should_trigger_edge（时机门控）
        │   ├─ replay_guard（回放：begin/end/interval）
        │   ├─ tdx_guard（starttype）
        │   └─ duration_guard（cxtype）
        ↓
        triggered = edge_fired AND (node_dirty OR data_dirty)
        ↓
        _process_edge_pipeline（边处理流水线）
          ├─ _apply_edge_filter（筛选 + 流转）
          │   ├─ filter_type == unconditional → _filter_unconditional
          │   ├─ filter_type == conditional → _filter_conditional
          │   │   ├─ 变更检测（src_changed + bar_changed）
          │   │   ├─ 缓存查找（_filter_cache）
          │   │   ├─ nset 策略分派（dispatch.json）
          │   │   │   └─ TDX条件 → _dispatch_tdx_condition
          │   │   │       └─ tdx_evaluators
          │   │   └─ formula_eval → formula_router.eval_batch
          │   │       ├─ 复杂度分析
          │   │       ├─ 引擎路由（python/hqchart）
          │   │       ├─ DataQuery 取数
          │   │       └─ 公式计算
          │   └─ 股票流转（复制/移动到目标节点）
          └─ _run_post_propagate_hooks（后处理）
              ├─ record_execution（记录执行次数/时间）
              ├─ handle_new_entries（新入池检测 + tracker）
              └─ apply_ttl（TTL 过期检查）
  ↓
_update_trackers（更新持仓跟踪器）
  ↓
_emit_transfer_events（发射流转事件）
  ├─ 遍历 event_domain_templates（pool_enter/move_exit/ttl_expire）
  ├─ _resolve_codes（提取代码列表）
  ├─ _resolve_domain_ctx（构建 domain 上下文）
  └─ _emit_domain_event（通用领域事件发射器）
      ├─ event_rules 事件
      └─ signal_rules 信号
  ↓
post_tick 流水线
  ├─ pk_ranking（PK排名）
  ├─ analysis_angles（多分析角度）
  ├─ dashboard（看盘面板）
  └─ alerts（告警事件）
```

### 3.2 关键路径详解

#### 路径一：数据更新 → latest_tick

```
外部 bar_data 到达
  ↓
_tick(pool_config, node_stocks, current_bar_data)
  ↓
_pre_tick(nodes, node_stocks, current_bar_data)
  ↓
stage_bar_data_inject（将 bar 字段合并到 node_stocks 中的股票对象）
  ↓
_run_tick_event_driven(...)
  ↓
_refresh_latest_tick(current_bar_data)
  ├─ 计算 current_bar_hash
  ├─ 如果 hash 变化 → 更新 _latest_tick + _latest_tick_ts
  └─ 置 _data_dirty = True
```

代码位置：engine.py:3650-3597（_tick → _run_tick_event_driven → _refresh_latest_tick）

#### 路径二：时机门控 → 边触发

```
按拓扑序遍历边
  ↓
_should_trigger_edge(edge, ctx)
  ├─ replay 模式 → _should_fire_flow_replay（begin+end+interval）
  ├─ _tdx_should_execute（starttype 守门）
  └─ _tdx_check_duration（cxtype 守门）
  ↓
edge_fired = True/False
  ↓
triggered = edge_fired AND (node_dirty[sid] OR data_dirty)
  ↓
True → 执行边的筛选和流转
False → 跳过这条边
```

代码位置：engine.py:3621-3628

#### 路径三：条件过滤 → 公式求值

```
_process_edge_pipeline(ctx)
  ↓
_apply_edge_filter(ctx)
  ↓
filter_type == 'conditional' → _filter_conditional(ctx)
  ↓
1. 变更检测
   ├─ src_changed = 源节点股票集变化？
   └─ bar_changed = 行情数据变化？
  ↓
2. 缓存查找（都没变才查）
   ├─ 命中 → 直接用缓存结果，跳过计算
   └─ 未命中 → 继续
  ↓
3. 策略执行
   ├─ 读 edge_filter_spec.strategy
   ├─ HR 分派到具体 handler
   └─ 执行条件计算
   ├─ 如果是 formula_eval 类型 →
   │   _filter_formula_eval
   │     ↓
   │   formula_router.eval_batch
   │     ├─ 复杂度分析
   │     ├─ 引擎路由决策
   │     ├─ 公式缓存查找
   │     ├─ 调用 Python/HQChart 引擎
   │     └─ 写入公式缓存
   └─ 如果是 TDX nset 类型 →
       _dispatch_tdx_condition
         ↓
       tdx_evaluators 模块
  ↓
4. 股票流转（将通过的股票加到目标节点）
  ↓
5. 缓存写入（如果适用）
```

代码位置：engine.py:2713-2735（_process_edge_pipeline），engine.py:2528-2625（_filter_conditional）

#### 路径四：流转完成 → 事件发射

```
_run_tick_event_driven 完成所有边的处理
  ↓
_update_trackers(node_stocks, current_bar_data)
  ↓
_emit_transfer_events(prev_snapshot, node_stocks, tevs)
  ↓
遍历 event_domain_templates（3个 domain）
  ├─ pool_enter：股票进入节点
  ├─ move_exit：股票从节点移出（move 模式）
  └─ ttl_expire：股票 TTL 过期被删除
  ↓
每个 domain：
  ├─ _resolve_codes → 从 diff/tevs 提取代码
  ├─ _resolve_domain_ctx → 构建上下文（role/cond/tracker）
  └─ _emit_domain_event → 发射事件/信号
      ├─ 遍历 event_rules.events
      └─ 遍历 signal_rules.signals
  ↓
写入 _event_queue / _signal_queue
```

代码位置：engine.py:3660-3661（_tick 中调用顺序），engine.py:3492-3545（_emit_transfer_events）

---

## 4. 代码理解全景图

### 4.1 模块划分

```
meta_core/
  ├── core/                     核心引擎层
  │   ├── engine.py            MetaEngine：股票池核心引擎
  │   ├── formula_engine.py    PythonFormulaEngine：纯Python公式引擎
  │   ├── formula_router.py    FormulaRouter：公式路由 + 缓存
  │   ├── table_engine.py      ConfigStore：配置表存储/加载
  │   ├── evaluators.py        评估器集合（TDX条件等）
  │   ├── replay.py            回放器
  │   ├── simulator.py         模拟器
  │   └── schemas.py           数据结构定义
  ├── services/                 服务层
  │   ├── data.py              DataQuery：K线数据查询
  │   ├── providers/           数据提供者（tq/akshare/mock等）
  │   └── formula_cache.py     公式结果缓存
  ├── config/                   配置表层（100+ JSON）
  │   ├── dispatch.json        策略分派规则
  │   ├── engines.json         时间周期引擎定义
  │   ├── pre_tick_pipeline.json   pre_tick 流水线
  │   ├── post_tick_pipeline.json  post_tick 流水线
  │   ├── runtime_tables_schema.json 运行时表元信息
  │   ├── data_config.json     数据配置（缓存策略等）
  │   ├── edge_semantics.json  边语义（变换单元等）
  │   ├── formula_routing.json 公式路由规则
  │   └── ... 其他 90+ 配置表
  ├── api/                      API 层
  └── app.py                    应用入口
```

### 4.2 依赖关系

```
MetaEngine (engine.py)
  ├── 依赖 → ConfigStore（table_engine.py）
  │     └─ 加载所有 config/*.json
  ├── 依赖 → FormulaRouter（formula_router.py）
  │     ├── PythonFormulaEngine（formula_engine.py）
  │     ├── HQChartProvider（services.providers.hqchart）
  │     └── DataQuery（services.data.py）
  │           └─ parquet 文件 + minute_aggregator
  ├── 依赖 → LRUCache（engine.py 内部类）
  │     └─ _data_cache / _filter_cache
  ├── 依赖 → tdx_evaluators（evaluators.py）
  ├── 依赖 → _pipeline（native.pipeline）
  │     └─ pre_tick stage handlers
  ├── 依赖 → _builtins_post_tick（native.builtins）
  │     └─ post_tick stage handlers
  └── 依赖 → _HR（native.builtins 注册表）
        └─ 各种 action handler
```

### 4.3 数据流向

```
[数据输入层]
  外部行情数据（K线/快照/财务/板块）
       ↓
[数据缓存层]
  _data_cache（4类TTL缓存）+ _latest_tick（行情真相源）
       ↓
[数据准备层]
  pre_tick 流水线（数据注入 + 分钟线聚合）
       ↓
[核心计算层]
  _run_tick_event_driven
    ├─ 时机门控（3层守卫）
    ├─ 条件过滤（nset_dispatch + formula_router）
    └─ 股票流转（节点间转移）
       ↓
[状态存储层]
  _rt 运行时表（node_stocks / _trackers / _dirty_nodes 等）
       ↓
[事件发射层]
  _emit_transfer_events（domain 事件 + 信号）
       ↓
[后处理层]
  post_tick 流水线（PK排名 + 分析角度 + 看板 + 告警）
       ↓
[输出层]
  _event_queue / _signal_queue / _alert_queue / _dashboard_data
```

### 4.4 核心设计原则

1. **表驱动**：所有业务逻辑尽量提取为配置表，引擎只包含通用的表解析与执行逻辑
2. **编译期/运行期分离**：能在编译期算的就不在运行期算（CompiledSchedule）
3. **增量计算**：数据不变就不重算（脏节点 + 变更检测 + 缓存）
4. **统一抽象**：时机门控统一为 primitive，值提取统一为 extractor，条件统一为 filter
5. **运行时表一等公民化**：所有运行时状态收敛到 _rt 命名空间，schema 声明
6. **分层架构**：数据层 → 计算层 → 事件层 → 展示层，职责清晰

---

## 5. 对股票池运行本质的最终理解（一句话）

**股票池本质上是一个"事件驱动的数据流拓扑计算引擎"——它以拓扑图定义股票流转路径，以配置表定义所有业务规则，以时机门控控制何时计算，以条件过滤决定哪些股票流动，以运行时表存储状态，最终通过事件和后处理流水线将结果呈现出来。**

---

## 6. 遗留问题

### 6.1 变换单元在运行期真的没用吗？

当前代码中，`compiled.units` 在运行期没有被读取，`_unit_cache_key` 仍然以边为粒度而不是以枢纽节点为粒度。设计上预留的变换单元级执行优化（如"一次计算、两边共享结果"）是否真的会实现？还是说变换单元最终只是编译期的拓扑识别工具？

相关代码：engine.py:2293-2295（_unit_cache_key），engine.py:1290-1305（_build_processing_plan）

### 6.2 formula_router 和 tdx_evaluators 的关系

TDX 条件（nset=0/1/2）最终是走 formula_router 还是走 tdx_evaluators？从代码看，`_dispatch_tdx_condition` 直接调用 tdx_evaluators 模块，而 formula_eval 类型走 formula_router。这两条路径是完全独立的还是有重叠？TDX 公式求值和通用公式求值是否会统一到 formula_router？

相关代码：engine.py:1993-1998（_dispatch_tdx_condition），engine.py:2629-2680（_filter_formula_eval）

### 6.3 _data_cache 和 FormulaCache 的关系

`_data_cache`（engine 层）和 `FormulaCache`（formula_router 层）是两个独立的缓存层。K线数据会被缓存两次吗？还是说它们缓存的是不同粒度的数据（原始K线 vs 公式结果）？

相关代码：engine.py:406-410（_data_cache），formula_router.py:33（FormulaCache import）

### 6.4 runtime_tables 的 27 张表真的都在 _rt 里吗？

`_RUNTIME_TABLE_NAMES` 集合（engine.py:131-144）中列出的表名和 `runtime_tables_schema.json` 中声明的表名不完全一致。有些 schema 中有声明的表不在 `_RUNTIME_TABLE_NAMES` 中（如 _current_bar_data、_last_data_update_ts），反之亦然。实际哪些表走 _rt 代理、哪些表不走？

相关代码：engine.py:129-144（_RUNTIME_TABLE_NAMES），runtime_tables_schema.json:5-266（schema 声明）

### 6.5 post_tick 的 4 个 stage 和 event_rules 的关系

post_tick 流水线的 alerts stage 产生告警事件，而 `_emit_transfer_events` 也产生事件。这两类事件是什么关系？告警事件是否也走 event_queue？还是说告警是独立的事件体系？

相关代码：post_tick_pipeline.json:41-51（alerts stage），engine.py:3492-3545（_emit_transfer_events）
