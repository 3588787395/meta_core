# 股票池引擎代码理解笔记 v2.8 评审报告（第26轮）

> 评审日期：2026-07-01
> 评审对象：spec_v2.8.md（第四版，纯理解阶段最后一版）
> 评审性质：架构级代码理解正确性审核

---

## 一、总评分

| 版本 | 评分 | 说明 |
|------|------|------|
| v2.5（第一版） | 55分 | 入门级理解，框架正确但核心机制偏差较大 |
| v2.6（第二版） | 70分 | 模块级理解，修正了主要偏差但深度不足 |
| v2.7（第三版） | 82分 | 系统级理解，覆盖了主要机制但有5个关键偏差 |
| **v2.8（第四版）** | **91分** | **架构级理解，深度和广度均达到较高水平** |

**评分说明：** 91分意味着对代码的理解已经达到"可以支撑大规模重构"的水平，但仍有少量细节偏差和遗漏，需要在设计阶段逐步修正。

---

## 二、上一版5个偏差修正验证

### 偏差一：_data_cache 不是单一用途缓存 ✅ 已修正

**验证结论：完全正确。**

- `_data_cache` 确实是 4 类 TTL 缓存的统一容器：kline(300s) / snapshot(5s) / financial(86400s) / sector(3600s)
- 代码位置：`engine.py:406-410`，`ttl_map` 参数明确传入 4 个前缀
- TTL 匹配逻辑：`engine.py:112-115`，按 key 前缀匹配 `_ttl_map`

**补充：** 文档提到"kline 缓存的 key 格式略有不同"，这个观察非常准确。实际使用中 `_fetch_multi_timeframe_cached` 用的是 `kline:{tf}:{codes_hash}`（`engine.py:2335`），而 `data_config.json:93-98` 中声明的是 `kline:{market}:{code}:{timeframe}:{count}`——配置声明和实际使用不一致，这是一个值得注意的技术债务。

---

### 偏差二：pre_tick/post_tick 不是硬编码阶段 ✅ 已修正

**验证结论：完全正确。**

- pre_tick 有 2 个 stage：`stage_bar_data_inject` + `stage_minute_aggregator_feed`
- post_tick 有 4 个 stage：`pk_ranking` + `analysis_angles` + `dashboard` + `alerts`
- 配置文件：`pre_tick_pipeline.json`、`post_tick_pipeline.json`
- 执行逻辑：遍历 pipeline 列表 → 检查 enabled → 查 `stage_ops` 映射 → 反射调用 handler

代码验证：
- pre_tick: `engine.py:3547-3575`
- post_tick: `engine.py:3724-3743`

---

### 偏差三：_should_trigger_edge 不是单层 gate ✅ 已修正

**验证结论：完全正确。**

三层守卫结构准确：
1. replay_guard（回放模式）：`_should_fire_flow_replay` — begin/end/interval
2. tdx_guard（两种模式都有）：`_tdx_should_execute` — starttype 守门
3. duration_guard（两种模式都有）：`_tdx_check_duration` — cxtype 守门

模式分支正确：
- replay 模式：三层都走（`engine.py:1768-1777`）
- 非 replay 模式：跳过 replay_guard（`engine.py:1778-1781`）

**补充：** `_tdx_check_duration` 返回 True 表示"已过期/不通过"，所以 `_should_trigger_edge` 中用 `if self._tdx_check_duration(edge): return False`，这个反直觉的细节文档也抓住了，很好。

---

### 偏差四：transform unit 不是运行期执行的 ✅ 已修正

**验证结论：完全正确。**

- 编译期分组：`_group_transformation_units` (`engine.py:2217-2247`)
- 运行期：`compiled.units` 字段未被读取，边的处理统一走 `compiled.out_edges`
- 三元组结构：入边(conditional) + hub节点 + 出边(unconditional)
- 配对条件：恰好 1 条入边 + 1 条出边

**补充：** `_unit_cache_key` (`engine.py:2293-2295`) 当前仍然以边为粒度（参数是 `eid`），没有体现"变换单元级缓存"的设计意图——这进一步佐证了"变换单元当前主要是编译期概念"的判断。

---

### 偏差五：nset_dispatch 不是只有 TDX 用 ⚠️ 基本修正，表述仍有小偏差

**验证结论：基本正确，但数量和层级表述有偏差。**

文档说"12 种通用条件类型"，实际 `dispatch.json` 中有 **14 种**：

| # | 类型 | gateway |
|---|------|---------|
| 1 | INDICATOR | formula_eval |
| 2 | RANKING | formula_eval |
| 3 | SECTOR_MEMBERSHIP | sector_filter |
| 4 | REVERSE_TRANSFER | formula_eval |
| 5 | CROSS_SECTION | cross_section_eval |
| 6 | BASIC_CONDITION | basic_filter |
| 7 | PASSTHROUGH | pass_through |
| 8 | FORMULA | formula_eval |
| 9 | TDX_INDICATOR | tdx_eval_nset0 |
| 10 | TDX_CONDITION_FORMULA | tdx_eval_nset1 |
| 11 | TDX_EXPERT_SYSTEM | tdx_eval_nset2 |
| 12 | TDX_FINANCIAL | tdx_eval_nset3 |
| 13 | TDX_MARKET | tdx_eval_nset4 |
| 14 | TDX_SETOP | tdx_eval_nset5 |

**核心判断正确：** dispatch.json 确实覆盖了所有条件类型，nset=0~5 只是 TDX 特有的子分类。

**层级表述偏差：** 文档画的是 `dispatch_rules → nset_dispatch → evaluator_dispatch` 三层，但实际更准确的层级是：
- 顶层：`dispatch_rules`（所有条件类型，含 TDX 和非 TDX）
- TDX 子层：`nset_dispatch`（TDX 的 6 种 nset 子类型）
- 扁平映射：`evaluator_dispatch`（TDX 条件类型 → 评估器方法名的扁平映射，不是第三层）

---

## 三、7个新方向理解验证

### 方向一：_data_cache 数据缓存层 ⭐ 92分

**正确点（10条）：**
1. ✅ LRUCache 继承自 dict，内部用 OrderedDict 实现 LRU
2. ✅ 4 类 TTL 缓存分类正确
3. ✅ TTL 前缀匹配逻辑正确（找到第一个匹配前缀即停止）
4. ✅ LRU+TTL 双重策略描述准确
5. ✅ 存储结构 `{"data": ..., "ts": ..., "ttl": ...}` 正确
6. ✅ `_latest_tick` 和 `_data_cache` 是两个独立数据层
7. ✅ `_latest_tick` 是热路径数据，`_data_cache` 是按需查询的冷数据
8. ✅ formula_router 不直接使用 `_data_cache`
9. ✅ `_fetch_multi_timeframe_cached` 使用 `_data_cache`
10. ✅ 缓存失效机制（TTL过期/LRU淘汰/自然失效）

**偏差点（2条）：**
1. ⚠️ key 格式描述不够精确：`data_config.json:93-98` 声明的格式是配置层面的"规范"，但实际代码中只有 `_fetch_multi_timeframe_cached` 用了 `kline:{tf}:{codes_hash}` 格式，其他缓存类型（snapshot/financial/sector）的实际使用场景在 v2.8 中并未找到——文档给人的感觉是 4 类缓存都有大量使用，但实际上当前代码主要用了 kline 缓存。
2. ⚠️ 缺少对 `_filter_cache` 和 `_data_cache` 关系的讨论：两者都是 LRUCache 实例，但用途、TTL 策略、生命周期完全不同。`_filter_cache` 是 per_tick 生命周期的筛选结果缓存，`_data_cache` 是 per_session 生命周期的通用数据缓存。

---

### 方向二：pre_tick/post_tick 流水线 ⭐ 95分

**正确点（8条）：**
1. ✅ pre_tick 职责定位（数据层）准确
2. ✅ post_tick 职责定位（后处理层）准确
3. ✅ pre_tick 2 个 stage 正确
4. ✅ post_tick 4 个 stage 正确
5. ✅ 执行流程（enabled检查 → stage_ops映射 → 反射调用）正确
6. ✅ post_tick 的 config_table / write_to 机制描述正确
7. ✅ 和核心 tick 循环的顺序关系正确
8. ✅ "全部都是表驱动流水线的一部分，不是硬编码在核心循环里"这个判断准确

**补充/深化点（2条）：**
1. pre_tick 的 handler 在 `_pipeline` 模块（`native.pipeline`），post_tick 的 handler 在 `_builtins_post_tick` 模块（`native.builtins`）——这个模块分离的设计意图文档未提及。
2. post_tick 的 handler 调用签名比文档描述的更复杂：除了 cfg/stocks/current_bar_data/now，还传入了 `stage_cfg`、`push_event`、`prev_rankings` 等额外参数（`engine.py:3737-3739`）。

---

### 方向三：_should_trigger_edge 三层守卫 ⭐ 93分

**正确点（10条）：**
1. ✅ 三层守卫总览正确
2. ✅ replay_guard 仅回放模式执行
3. ✅ tdx_guard 和 duration_guard 两种模式都执行
4. ✅ begin 守门的 begin_type 语义（0/6/3/5/7）正确
5. ✅ end 守门的 end_type 语义（0/1/2）正确
6. ✅ interval 守门的逻辑正确
7. ✅ 涉及的运行时表（`_flow_first_fire_ts` / `_flow_last_fire_ts`）正确
8. ✅ tdx_guard 走 `_eval_timing_primitive` 统一入口
9. ✅ 时机原语系统（内联7种原语）的判断正确
10. ✅ 实时模式和回放模式的时间源不同

**偏差点（2条）：**
1. ⚠️ 时机原语不止 7 种，文档说"内联了 7 种原语"，但代码中实际有：always、elapsed、timestamp_ge、in_range、hhmmss、once、count_gte = **7 种**，这个是对的。但文档列举时只列了 5 种（always/elapsed/in_range/not_elapsed/等等...），"not_elapsed" 实际上不是独立原语，而是 elapsed + compare_op='<=' 的组合。
2. ⚠️ `_eval_timing_primitive` 的 namespace 有 4 种（gate/duration/begin/end），但文档对 begin/end namespace 的求值细节描述较少——特别是 begin/end 模式下 `cur_ts` 和 `threshold` 都从 ctx 传入，没有单位换算，这个关键差异点文档只是一笔带过。

---

### 方向四：变换单元（transform unit）⭐ 94分

**正确点（8条）：**
1. ✅ 三元组结构定义准确
2. ✅ hub 节点类型（tdx_condition/transfer_condition/dzh_condition_pool）正确
3. ✅ hub 节点特点（入边数=1、出边数=1、不存储股票）正确
4. ✅ 分组算法步骤正确
5. ✅ 配对条件严格（恰好1入+1出）正确
6. ✅ 编译期用途（拓扑模式识别、缓存粒度优化）正确
7. ✅ 运行期不使用 `compiled.units` 的判断正确
8. ✅ 变换单元最终被拆解回独立边进入 processing_plan

**偏差点（2条）：**
1. ⚠️ 文档说"_unit_cache_key 以边为粒度，但概念上可以扩展到以枢纽节点为粒度"——这个判断偏保守。实际上从方法签名 `_unit_cache_key(self, node_stocks, sid, eid)` 看，第三个参数就是 `eid`（边ID），缓存键是 `(eid, frozenset(...))`，完全是边级粒度，没有任何"枢纽节点级"的痕迹。设计上预留扩展空间的说法没有代码证据支持。
2. ⚠️ 变换单元的一个重要用途文档未提及：在 `edge_semantics.json` 中，`change_detection.unit_level = true` 和 `unit_cache_key` 的配置表明，变换单元的设计意图之一是**变更检测粒度优化**——以枢纽节点为单位做变更检测，而不是每条边独立检测。虽然当前代码未实现，但这是设计文档中明确的方向。

---

### 方向五：nset_dispatch 策略分派体系 ⭐ 87分

**正确点（7条）：**
1. ✅ dispatch.json 四大部分（dispatch_rules/nset_dispatch/evaluator_dispatch/attr_dispatch）正确
2. ✅ nset_dispatch 6 种类型（0~5）正确
3. ✅ nset=0/1/2 走 eval_formula_nset，nset=3/4 走 eval_scalar_nset，nset=5 走 eval_nset5_set_operation
4. ✅ engines.json 9 个时间周期引擎正确
5. ✅ 编译期分派（`_compile_edge_spec`）的存在正确
6. ✅ 运行期分派走 `_HR` 注册表
7. ✅ TDX 条件走 `_dispatch_tdx_condition` → `tdx_evaluators`

**偏差点（4条）：**
1. ❌ dispatch_rules 数量错误：文档说 12 种，实际 14 种（少算了 PASSTHROUGH 和 FORMULA）
2. ⚠️ 分派层级描述不准确：`evaluator_dispatch` 不是第三层，而是 TDX 条件的扁平映射。准确的层级应该是：
   ```
   dispatch_rules（所有条件类型，顶层）
     ├─ 非TDX类型 → 直接走 gateway（formula_eval/sector_filter/...）
     └─ TDX类型 → nset_dispatch（6种nset子类型）
                     └─ evaluator_dispatch（扁平映射到评估器方法名）
   ```
3. ⚠️ 运行期分派的描述不够精确：`_filter_conditional` 中实际的执行路径是读编译期的 `edge_filter_spec.strategy` 和 `handler_name`，然后从 `_HR` 查 handler 直接调用。`_dispatch_tdx_condition` 是 `_HR` handler 内部调用的，不是在 `_filter_conditional` 层面直接调用的。
4. ⚠️ 缺少对 `_filter_strategies` 三种模式（unconditional/conditional/formula_eval）的清晰区分——这三种是顶层 filter_type 分派，nset_dispatch 是 conditional 模式下的子分派。

---

### 方向六：formula_router / DataQuery 完整架构 ⭐ 85分

**正确点（8条）：**
1. ✅ FormulaRouter 是公式求值的统一入口和路由分发器
2. ✅ 核心职责（复杂度分析/引擎路由/统一接口/结果缓存）正确
3. ✅ DataQuery 是统一 K 线数据查询入口
4. ✅ DataQuery 只读取本地 parquet + minute_aggregator，不提供下载
5. ✅ 双引擎架构（PythonFormulaEngine + HQChartProvider）正确
6. ✅ 公式求值调用链的大体结构正确
7. ✅ 复杂度分析（simple/complex）基于 simple_functions 配置
8. ✅ 路由决策预先做出，失败时不切换路径

**偏差/遗漏点（5条）：**
1. ❌ **DataQuery 有两个不同的导入路径**，这是重要遗漏：
   - `engine.py:394`: `from ..services.data_query import DataQuery`
   - `formula_router.py:32`: `from ..services.data import DataQuery`
   
   这意味着可能有两个不同的 DataQuery 实现，或者有重命名/迁移。这个差异点非常关键，文档完全未提及。

2. ⚠️ 文档说"formula_router 通过 DataQuery 获取 K 线数据"，但没有明确说明：**Python 引擎和 HQChart 引擎的数据获取路径是不同的**。Python 引擎可能直接使用 DataQuery，而 HQChart 引擎作为 C++ 封装，可能有自己独立的数据加载逻辑。

3. ⚠️ 缓存层级描述不完整：文档提到了 `FormulaCache`（formula_router 层）和 `_data_cache`（engine 层），但没有说清楚两者的关系和区别：
   - `_data_cache`：缓存原始 K 线数据，粒度是"时间框架+股票集合"
   - `FormulaCache`：缓存公式计算结果，粒度是"公式+股票+周期+参数"
   - 两者是不同层级的缓存，不存在重复缓存的问题

4. ⚠️ 文档说"简单公式 + 1m/tick 周期 → Python 引擎"，这个说法基本正确但不够精确。实际路由决策是查表（`formula_routing.json` 的 `engine_routing` 规则表），不是硬编码的 if-else。规则表中可能定义了更复杂的路由条件。

5. ⚠️ 缺少对 `_run_formula_eval_batch_sync` 的讨论：engine 层调用 formula_router 时，通过这个方法做 async → sync 转换（`engine.py:1797-1811`），处理了"已在事件循环中"和"无事件循环"两种情况。这个同步桥接层是 engine 和 formula_router 之间的重要适配。

---

### 方向七：runtime_tables 代理机制 ⭐ 90分

**正确点（9条）：**
1. ✅ `_rt` 是运行时表的统一存储命名空间
2. ✅ "运行时表一等公民化"的概念理解准确
3. ✅ 统一存储 / schema 声明 / 代理访问 / 可枚举 / 可序列化 五个特征正确
4. ✅ ConfigStore 和 runtime_tables 的关系正确（静态配置表 vs 动态运行时表）
5. ✅ `__setattr__` 和 `__getattr__` 的代理机制描述正确
6. ✅ `_RUNTIME_TABLE_NAMES` 集合的作用正确
7. ✅ 引导顺序正确（先设 _rt → 初始化属性 → _init_runtime_tables → 同步到 _rt）
8. ✅ 设计意图（便于管理、调试、持久化）的理解准确
9. ✅ 表的分类（per_tick / per_session）基本正确

**偏差点（3条）：**
1. ❌ **表数量错误**：文档说"共有 27 张运行时表"，但 `runtime_tables_schema.json` 中实际有 **29 张**（数 `table_name` 可得）。而 `_RUNTIME_TABLE_NAMES` 集合中有 **32 个**（多了 `_last_data_update_ts` 和 `_current_bar_data`，以及可能的其他差异）。

2. ⚠️ schema 声明和实际代理不完全一致：文档在"遗留问题"中提到了这个点，但没有深入分析。实际情况是：
   - `runtime_tables_schema.json` 声明了 29 张表
   - `_RUNTIME_TABLE_NAMES` 有 32 个表名
   - `_init_runtime_tables` 按 schema 初始化到 `_rt`
   - 但 `__setattr__` / `__getattr__` 代理基于 `_RUNTIME_TABLE_NAMES`
   
   这意味着 schema 中有声明但不在 `_RUNTIME_TABLE_NAMES` 中的表，不会走代理访问；反之，在 `_RUNTIME_TABLE_NAMES` 中但 schema 没声明的表，会走代理但不会被 `_init_runtime_tables` 初始化。

3. ⚠️ 缺少对 `_compiled_cache` 为什么不走 `_rt` 代理的讨论：`engine.py:459-460` 明确注释"_compiled_cache 不在 _RUNTIME_TABLE_NAMES 中，走正常属性路径（非 _rt 代理）"——这说明运行时表的设计是有选择性的，不是所有运行时状态都要进入 `_rt`。选择标准是什么？文档未探讨。

---

## 四、完整调用链验证 ⭐ 90分

**总体评价：** 调用链的主干正确，层次清晰，但有几处细节偏差。

**正确的主干：**
```
外部数据更新 → pre_tick 流水线 → _refresh_latest_tick → _run_tick_event_driven
→ _should_trigger_edge → _process_edge_pipeline → _apply_edge_filter
→ 股票流转 → _update_trackers → _emit_transfer_events → post_tick 流水线
```

**细节偏差：**

1. ⚠️ `_refresh_latest_tick` 的位置：文档放在了 pre_tick 之后、_run_tick_event_driven 内部，这个是对的。但文档说"_refresh_latest_tick 更新 latest_tick + 置 data_dirty"，实际 `_refresh_latest_tick` 还会计算 `_current_bar_hash` 并与 `_last_bar_hash` 对比——置位 data_dirty 的条件是"hash 变化"，不是无条件置位。

2. ⚠️ `_process_edge_pipeline` 的内部结构：文档说包含 `_apply_edge_filter` + `_run_post_propagate_hooks`，但实际代码中 `_process_edge_pipeline` 的结构需要更仔细验证。从 `engine.py:2713` 的注释看，是 `_apply_edge_filter` + 后处理，但后处理的具体名称和组成文档描述不够精确。

3. ⚠️ 条件过滤路径描述有混淆：`_filter_conditional` 是 conditional filter_type 的处理函数，它内部走的是 `_HR` 策略分派（读编译期 edge_filter_spec.strategy），不是直接走 nset_dispatch。nset_dispatch 是 TDX 条件下 `_dispatch_tdx_condition` 内部的事情。

4. ⚠️ 缺少 `_build_processing_plan` 的讨论：编译期 `_group_transformation_units` 得到 units 和 standalone_edges 后，通过 `_build_processing_plan` 统一成 processing_plan，这个中间步骤在调用链中缺失了。

---

## 五、代码理解全景图验证 ⭐ 88分

**模块划分 ⭐ 85分：**
- 大体结构正确，但有几处不准确：
  - `evaluators.py` 在 `core/` 下，文档放对了
  - 但 `table_engine.py` 的 `ConfigStore` 类名正确，职责描述准确
  - `services/` 下的 `data.py` 和 `data_query.py` 的关系未理清（前面已提到）
  - 缺少 `native/` 目录的重要性说明——`_HR` 注册表、`_pipeline`、`_builtins_post_tick` 都来自 `native` 模块，这是核心业务逻辑的实际载体，文档在全景图中完全没有体现

**依赖关系 ⭐ 90分：**
- 核心依赖链基本正确
- 但缺少对 `native` 模块依赖的展示

**数据流向 ⭐ 92分：**
- 六层结构（数据输入→数据缓存→数据准备→核心计算→状态存储→事件发射→后处理→输出）的划分非常清晰
- 各层的职责描述准确
- 这是文档的亮点之一

**核心设计原则 ⭐ 93分：**
- 6 条设计原则总结得非常到位
- 表驱动、编译期/运行期分离、增量计算、统一抽象、运行时表一等公民化、分层架构
- 这 6 条精准地抓住了代码的设计哲学

---

## 六、本质理解（一句话）⭐ 95分

> "股票池本质上是一个'事件驱动的数据流拓扑计算引擎'——它以拓扑图定义股票流转路径，以配置表定义所有业务规则，以时机门控控制何时计算，以条件过滤决定哪些股票流动，以运行时表存储状态，最终通过事件和后处理流水线将结果呈现出来。"

**评价：** 这句话非常精准，抓住了本质。五个核心要素（拓扑图/配置表/时机门控/条件过滤/运行时表）都点到了，"事件驱动的数据流拓扑计算引擎"这个定性也很准确。

**可以更精炼的地方：** 如果要更本质一点，可以强调"股票是数据，节点是状态，边是变换，时间是触发"——但当前版本已经足够好了。

---

## 七、核心正确理解（15条）

1. **_data_cache 是 4 类 TTL 缓存的统一容器**，用 key 前缀区分，每类有独立 TTL 策略
2. **pre_tick/post_tick 是表驱动流水线**，不是硬编码阶段，可独立启用/禁用
3. **_should_trigger_edge 是三层守卫**（replay_guard + tdx_guard + duration_guard），实时和回放模式路径不同
4. **变换单元是编译期概念**，运行期被拆解回独立边，不影响执行路径
5. **nset_dispatch 是 TDX 条件的子分派**，dispatch.json 覆盖所有条件类型
6. **FormulaRouter 是双引擎路由器**，在 Python 引擎和 HQChart 引擎之间做显式路由
7. **DataQuery 是统一 K 线数据入口**，只读本地 parquet + minute_aggregator
8. **runtime_tables 是一等公民**，通过 `_rt` 命名空间 + `__getattr__/__setattr__` 代理实现
9. **编译期/运行期分离**是核心设计原则，CompiledSchedule 缓存所有静态计算
10. **增量计算**是核心性能策略（脏节点 + 变更检测 + 多级缓存）
11. **表驱动**贯穿整个架构，所有业务逻辑尽量提取为 JSON 配置表
12. **_latest_tick 是行情唯一真相源**，与 node_stocks 解耦
13. **时机门控统一走 _eval_timing_primitive**，内联 7 种时机原语
14. **filter 有三种模式**（unconditional / conditional / formula_eval），顶层分派后走不同路径
15. **事件和后处理分离**：流转事件在 `_emit_transfer_events` 中发射，PK/分析/看板/告警在 post_tick 流水线中处理

---

## 八、仍然有偏差的点（8条）

1. **dispatch_rules 数量错误**：文档说 12 种，实际 14 种（PASSTHROUGH 和 FORMULA 被遗漏）
2. **DataQuery 有两个导入路径**：`services.data_query` 和 `services.data`，文档未发现此差异
3. **runtime_tables 数量错误**：schema 有 29 张，`_RUNTIME_TABLE_NAMES` 有 32 个，文档说 27 张
4. **nset_dispatch 层级表述不准确**：evaluator_dispatch 不是第三层，是 TDX 的扁平映射
5. **_filter_conditional 的执行路径描述有混淆**：直接走 _HR 策略分派，nset_dispatch 是更内层的事情
6. **变换单元的设计意图描述偏保守**：edge_semantics.json 中明确有 unit_level 变更检测的设计，文档说"概念上可以扩展"不够准确
7. **_refresh_latest_tick 的置位条件描述不精确**：data_dirty 是 hash 变化时才置位，不是无条件置位
8. **公式路由决策机制描述不够精确**：是查表驱动（formula_routing.json），不是简单的"简单公式+1m/tick→Python"硬编码判断

---

## 九、仍然遗漏的点（8条）

1. **native 模块的核心地位**：`_HR` 注册表、`_pipeline`、`_builtins_post_tick` 都来自 `native` 模块，这是业务逻辑的实际载体，全景图中完全缺失
2. **_compiled_cache 为什么不走 _rt 代理**：这个设计选择背后的考量未被探讨
3. **_filter_cache 和 _data_cache 的关系与区别**：两个 LRUCache 实例，用途/TTL/生命周期完全不同
4. **_run_formula_eval_batch_sync 同步桥接层**：engine 层调用 formula_router 时的 async→sync 转换
5. **_build_processing_plan 编译步骤**：变换单元和独立边如何统一成 processing_plan
6. **schema 声明和 _RUNTIME_TABLE_NAMES 不一致的影响**：哪些表走代理、哪些不走，原因是什么
7. **HQChart 引擎的数据获取路径**：C++ 引擎是如何获取 K 线数据的，和 DataQuery 是什么关系
8. **_HR 注册表的完整构成**：native.builtins 中有哪些 handler，如何分类的

---

## 十、总体评价

### 理解深度：L3.5（系统级 → 架构级过渡）

- **L1 入门** ✅ 已跨越
- **L2 模块级** ✅ 已跨越
- **L3 系统级** ✅ 已达到
- **L4 架构级** ⚙️ 正在接近

**判断依据：**
- 对核心引擎的内部机制（时机门控、条件过滤、股票流转、缓存策略）有深入理解
- 对模块间的依赖关系和数据流向有清晰认识
- 能识别出设计原则（表驱动、编译期/运行期分离、增量计算等）
- 但对一些跨模块的设计决策（为什么 DataQuery 有两个路径、为什么 _compiled_cache 不走 _rt）还缺少深入思考
- 对 native 模块这个核心业务载体的认识还不够充分

### 能支撑的重构规模：中大型重构

**可以支撑：**
- ✅ 单个模块的重构（如 formula_router 优化、缓存策略调整）
- ✅ 多个模块的协同重构（如时机门控体系优化、运行时表治理）
- ✅ 配置表体系的整理和规范化
- ✅ 性能优化类重构（增量计算、缓存策略）

**需要谨慎：**
- ⚠️ 涉及 native 模块的重构（对其内部结构理解不足）
- ⚠️ DataQuery 双路径问题（需要先理清两个 DataQuery 的关系）
- ⚠️ 跨越多层的架构级重构（如整体分层调整）

### 理解阶段可以毕业了吗？✅ 可以毕业

**理由：**
1. 核心机制的理解正确率达到 91%，足够支撑设计工作
2. 剩余的偏差和遗漏主要是边缘细节，不影响架构决策
3. 对设计原则的把握准确，能在设计阶段做出正确的技术选择
4. "纯理解阶段"的目标已经达成——再继续抠细节会边际效益递减

**毕业条件达成情况：**
- ✅ 能画出准确的系统架构图
- ✅ 能讲清楚核心数据流和调用链
- ✅ 能识别出主要的设计模式和原则
- ✅ 能评估变更的影响范围
- ⚠️ 对部分边缘模块（native/）的理解还不够深入

---

## 十一、进入设计阶段的第一步建议

**第一步：做一次"设计假设清单"梳理，而不是直接开始画架构图。**

具体来说：

### 1. 先把 v2.8 中的 5 个遗留问题和本次评审发现的 8 个偏差 + 8 个遗漏分类整理

分为三类：
- **P0（必须先搞清楚才能做设计）**：DataQuery 双路径问题、native 模块核心地位
- **P1（设计过程中逐步验证）**：runtime_tables 不一致问题、变换单元的设计意图
- **P2（不影响架构决策，可以后补）**：表数量统计错误、原语数量描述等细节

### 2. 针对 P0 问题做一次"快速定向侦察"

用 1-2 天时间：
- 搞清楚 `services/data.py` 和 `services/data_query.py` 中两个 DataQuery 的关系（是重复？是迁移中？是不同职责？）
- 搞清楚 `native` 模块的目录结构和核心导出物（_HR 里有什么？pipeline 里有什么？）

### 3. 明确重构的"不变量"和"可变量"

在开始设计之前，先回答：
- 哪些东西是必须保留的？（如 TDX/DZH 兼容性、表驱动架构）
- 哪些东西是可以改的？（如模块划分、缓存策略、命名）
- 重构的目标是什么？（可维护性？性能？新功能？）

### 为什么这是第一步？

因为 v2.8 的理解已经达到了"**知其然**"的水平，但要进入设计阶段，还需要在几个关键决策点上达到"**知其所以然**"的水平。直接开始设计，可能会在这些未知点上做出错误假设，导致设计返工。

先花少量时间把 P0 问题搞清楚，相当于给设计"打地基"——地基不牢，地动山摇。

---

## 十二、总结

v2.8 是一份高质量的代码理解笔记。从 v2.5 到 v2.8，理解深度实现了从"入门"到"架构级"的跨越，正确率从 55 分提升到 91 分。

**最值得肯定的三点：**
1. **自我纠错能力强**：每版都能精准定位上一版的偏差并修正
2. **结构化思考好**：7 个方向的选择和组织非常合理，覆盖了核心机制
3. **本质把握准**：一句话总结抓住了股票池引擎的核心

**下一阶段的重点：**
- 从"理解代码"转向"设计未来"
- 先扫清 P0 级别的未知点
- 明确重构目标和约束
- 然后再开始正式的架构设计

---

**评审结论：理解阶段通过，准予毕业。建议按上述第一步建议执行后，进入设计阶段。**
