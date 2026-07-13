# 第25轮迭代代码理解笔记审核报告（v2.7）

> 审核人：严厉资深架构审核工程师
> 审核日期：2026-07-01
> 审核对象：spec_v2.7.md（第三版代码理解笔记）
> 上一版得分：91分（v2.6）
> 本轮得分：**94分**

---

## 一、总体评分与对比

| 维度 | v2.6 得分 | v2.7 得分 | 提升 |
|------|-----------|-----------|------|
| 架构整体理解 | 90 | 93 | +3 |
| 核心机制深度 | 92 | 95 | +3 |
| 细节准确性 | 88 | 93 | +5 |
| 覆盖面广度 | 93 | 94 | +1 |
| **综合** | **91** | **94** | **+3** |

**评分结论**：相比 v2.6 的 91 分，本轮提升 3 分至 94 分。7个新方向的理解大部分准确，深度显著提升。但仍存在几处关键偏差和重要遗漏，距离"完全掌握"还有一步之遥。

---

## 二、理解正确的重点（10条）

### 1. CompiledSchedule 字段体系 ✅
准确识别出 CompiledSchedule 的 14 个字段及其 4 组分类（拓扑基础/编译期预计算），对"一次编译，跨 tick 只读"的设计本质把握精准。代码位置 engine.py:257-272 完全对应。

### 2. _filter_cache 的 LRU+TTL 双策略 ✅
对 LRUCache 实现、缓存键结构（eid+frozenset）、缓存值（深拷贝目标池列表）、4种失效机制（源池变化/行情变化/TTL/LRU）的理解**完全正确**。特别是"行情变化时根本不查缓存而是直接跳过的细节，把握精准（engine.py:2582）。

### 3. post_propagate_hooks 的半框架化设计 ✅
3个hook（record_execution/handle_new_entries/apply_ttl）、执行顺序、when条件求值、异常捕获不中断的机制，**完全正确。对"半框架化"（配置化列表+硬编码实现）的定性精准。

### 4. event_domain_templates 的表驱动本质 ✅
3个domain（pool_enter/move_exit/ttl_expire）、resolvers子表、codes_source/role_source/cond_source三层解析链、事件+信号双发射流程，**整体正确**。对"表驱动事件系统"的定性准确。

### 5. _latest_tick 作为行情唯一真相源 ✅
结构（_latest_tick/_latest_tick_ts/_latest_tick_hash）、更新机制（_refresh_latest_tick + hash比较）、data_dirty置位机制，**完全正确**。对"解耦数据输入和计算"的设计意图把握精准。

### 6. pipeline 扁平化的2步结构 ✅
设计上8相位 vs 实际2步（_apply_edge_filter + _run_post_propagate_hooks）、gate被提到_should_trigger_edge前置、扁平化的4个原因，**理解正确**。

### 7. 三种filter类型的划分与差异 ✅
unconditional/conditional/formula_eval 三种类型的 handler 方法、触发条件、执行步骤数，**基本正确**。对"入口统一，内部分开"的结构把握准确。

### 8. pool_role 的 6 种角色与 is_target 判断 ✅
6种角色（target_pool/candidate_pool/sink_pool/transfer_condition/market_source/discard_pool）、is_target 信号判断逻辑、角色与边类型的两个独立维度，**完全正确**。

### 9. 事件发射的 3 层间接 ✅
_emit_transfer_events → event_domain_templates → _resolve_codes/_resolve_domain_ctx → _emit_domain_event 的3层间接结构，**完全正确**。

### 10. 核心数据流动的脏标记驱动模型 ✅
"表驱动的增量数据流引擎"的本质定性、data_dirty + node_dirty + edge_fired 三维触发模型、级联传播机制，**把握精准**。

---

## 三、理解有偏差的点（5条）

### 偏差1：_HR 不是 HandlerRegistry，而是 builtins 函数字典 ⚠️

**笔记描述**：多处提到 "HR（HandlerRegistry）"，暗示 HR 是一个专门的 HandlerRegistry 类。

**实际代码**（engine.py:61）：
```python
_HR = {n: o for n, o in vars(_builtins).items() if callable(o) and not n.startswith("__")}
```

_HR 本质是：从 `_builtins` 模块（native/builtins）加载的所有可调用对象构成的函数字典，不是一个类，没有注册机制。它就是一个"全局函数表，用模块加载 + 字典查找实现"分派"。

**影响程度**：中。不影响对功能理解，但影响对架构风格的判断——这不是"注册模式"，而是"模块函数字典"。

### 偏差2：_data_cache 被遗漏，说"代码中没有 data_cache" ❌

**笔记描述**（2.4节）："代码中**没有** `market_data` 或 `data_cache` 作为独立运行时表。"

**实际代码**（engine.py:406-410, 139）：
```python
self._data_cache = LRUCache(
    max_entries=_cs.get("max_entries", self._defaults.get('cache_maxsize', 10000),
    default_ttl=self._timing_cfg.get("cache_ttl", 5.0),
    ttl_map={"kline": ..., "snapshot": ..., "financial": ..., "sector": ...}
)
```

`_data_cache` 是**独立的 LRUCache，有 kline/snapshot/financial/sector 4种 TTL 分类，在 runtime_tables_schema.json 中也有定义（engine.py:139）。这是公式引擎读历史K线的数据缓存层。

**影响程度**：中高。这是一个重要的运行时表，直接关系到公式求值的性能优化路径。

### 偏差3：move_exit 的 trigger_match.type 是 "pool_exit" 不是 "move_exit" ⚠️

**笔记描述**（2.3节表格）：move_exit 的 trigger_match 隐含 move_exit。

**实际代码**（edge_strategies.json:391）：
```json
"move_exit": {
  "trigger_match": {"type": "pool_exit"},
  "signal_trigger": "move_exit",
  ...
}
```

注意：trigger_match.type = "pool_exit"，signal_trigger = "move_exit"。两者不一样！事件匹配用的是 "pool_exit"，信号匹配用的是 "move_exit"。

**影响程度**：低。不影响整体架构理解，但事件匹配机制的细节有偏差。

### 偏差4：架构图是 7 层不是 6 层 ⚠️

**笔记描述**："完整架构图（6层）"

**实际计数**：配置层 → 编译层 → 数据层 → 状态层 → 计算层 → 副作用层 → 事件层 = **7层**。

笔记自己画的图里就是7层，但标题写"6层"。数错了。

**影响程度**：极低。纯计数错误，内容是对的。

### 偏差5：unconditional 的触发条件描述不准确 ⚠️

**笔记描述**（2.6节）：unconditional 触发条件是"源池变化时"。

**实际代码**（engine.py:3621-3627）：
```python
fired = self._should_trigger_edge(edge, ...)  # gate 判断（时机触发）
...
triggered = fired and (self._is_dirty(sid) or self._is_data_dirty())
```

unconditional 同样需要：
1. **gate 通过**（时机触发，_should_trigger_edge）
2. **源池脏 OR 数据脏**

不是只有"源池变化时"。conditional 和 unconditional 的触发条件**在顶层是一样的**——都是 fired AND (node_dirty OR data_dirty)。差异在 filter 内部：unconditional 内部如果 src 没变直接返回 False（engine.py:2493-2496？让我确认一下）。

实际上在 _filter_unconditional 内部第一步就是源变更检测，源未变则跳过。但顶层触发判断是一样的。

**影响程度**：低。顶层触发条件理解有细微偏差，但功能结果是对的。

---

## 四、仍然遗漏的重要点（7条）

### 遗漏1：_data_cache 的完整作用与4类TTL ❗

**重要性**：高

_data_cache 是公式引擎读历史数据的缓存层，有 kline/snapshot/financial/sector 4种不同 TTL 策略（engine.py:406-410）。这是性能优化的关键一层，直接影响公式求值的性能模型。

笔记完全没提。

### 遗漏2：pre_tick / post_tick 流水线的具体 stage ❗

**重要性**：高

笔记提到了 pre_tick 和 post_tick 是流水线，但不知道具体有哪些 stage。实际上：

- **pre_tick_pipeline**：定义在 pre_tick_pipeline.json，有 stage_bar_data_inject、stage_minute_aggregator_feed 等
- **post_tick_pipeline**：定义在 post_tick_pipeline.json，有 pk_ranking、analysis_angles、dashboard、alerts 等
- 都通过 stage_ops 表路由到 handler 方法

这是"数据注入 → 计算 → 后处理"三层架构的重要组成部分。

### 遗漏3：_should_trigger_edge 的完整判断逻辑 ❗

**重要性**：高

笔记说时机判断是黑盒。实际 _should_trigger_edge 包含（engine.py:1757-1785）：

1. **replay_guard**：回放模式下的 begin/end/interval 判断
2. **tdx_guard**：starttype 时机判断（立即/开盘前/开盘后/收市前/收市后/指定时间等）
3. **duration_guard**：cxtype 持续时长判断（只执行一次/一直执行/持续N秒等）

这是 pipeline 设计中的 gate 相位的实际实现，是"时机门控的核心逻辑。

### 遗漏4：变换单元（transformation unit）的完整机制 ❗

**重要性**：中高

笔记提到了变换单元三元组，但不知道：
- 变换单元的**结构**：in_edge + hub_node + out_edge 三元组
- **hub 节点**：transfer_condition 类型节点是 hub
- **分组算法**：_group_transformation_units 如何识别变换单元
- **为什么需要变换单元**：对应 TDX 的"转移条件 + 出边"模式

这是拓扑结构的重要概念。

### 遗漏5：nset_dispatch 的具体策略体系 ❗

**重要性**：中高

笔记自己也承认不懂。dispatch.json、engines.json、强弱对比引擎、nset_dispatch 策略分派机制，这是条件筛选的核心计算逻辑，目前还是黑盒。

### 遗漏6：formula_router / DataQuery 的完整架构 ❗

**重要性**：中

formula_router 是什么、DataQuery 怎么获取历史K线、批量求值的具体实现、_data_cache 如何配合使用，这些是公式系统的核心路径，目前理解很浅。

### 遗漏7：runtime_tables 代理机制（_rt 命名空间）❗

**重要性**：中

代码中有一个重要的架构设计——运行时表收敛到 _rt 命名空间，通过属性代理实现兼容（engine.py:131-144, 470-479）。

_RUNTIME_TABLE_NAMES 定义了哪些属性会被代理到 self._rt 字典。这是"运行时表一等公民化"的设计，笔记完全没提。

---

## 五、架构图验证

### 5.1 6层（实际7层）架构图 ✅（基本正确，计数错）

**验证结果**：7层架构图的**内容基本正确**，层数计数错误（是7层不是6层）。

**需补充**：
- 数据层应加上 `_data_cache`（K线/快照/财务/板块数据缓存）
- 配置层漏掉了 `pre_tick_pipeline.json` 和 `post_tick_pipeline.json`
- 计算层应加上 `formula_router` / `DataQuery`

### 5.2 核心数据流动图 ✅（基本正确）

**验证结果**：从 bar_data 输入到事件发出的主流程**基本正确**。

**需修正/补充**：
1. `_pre_tick` 是 async 的，有多个 stage（不是一步）
2. `_post_tick` 也是多 stage（PK排名/分析角度/看板/告警）
3. 缺少 `_data_cache` 在公式求值路径中的作用

---

## 六、总体评价

### 理解深度评估

**当前深度："模块级深入理解**（4/5级）

| 级别 | 描述 | 当前状态 |
|------|------|----------|
| L1 表面 | 知道有什么 | ✅ 远超 |
| L2 接口级 | 知道怎么用 | ✅ 远超 |
| L3 模块级 | 知道内部怎么实现 | ✅ 已达到 |
| L4 系统级 | 知道为什么这么设计 | ⚠️ 部分达到 |
| L5 本质级 | 知道演化路径与权衡 | ❌ 未达到 |

**能支撑的重构规模：**

- ✅ **小型重构**（单模块内部重构）：完全可以
- ✅ **中型重构**（跨模块重构，如 filter 模块拆分）：基本可以，风险可控
- ⚠️ **大型重构**（全架构重构）：有风险，nset策略、时机判断、变换单元这几块还不深
- ❌ ** rewrite 级重写**：还不够，公式系统、数据层、HR体系理解不足

### 核心优势

1. **表驱动架构的本质把握精准——"表驱动的增量数据流引擎"这个定性非常到位
2. **编译期/运行期分离**的设计理解深刻
3. **脏标记驱动**的增量计算模型理解透彻
4. **7个新方向**覆盖面广，大部分理解准确

### 核心短板

1. **数据层**理解不足（_data_cache 完全漏掉）
2. **公式系统**理解很浅（formula_router / DataQuery）
3. **nset 策略分派**是黑盒
4. **时机门控**（starttype/cxtype 是黑盒

---

## 七、下一轮应该深入的方向（8个）

### 方向1：_data_cache 与公式数据缓存体系
- 读 _data_cache 的结构、4种 TTL、与 formula_router/DataQuery 的配合方式、缓存命中率影响

### 方向2：_should_trigger_edge 时机门控完整逻辑
- starttype 的 7/8种时机类型、cxtype 的持续时长类型、replay_guard、tdx_guard、duration_guard

### 方向3：nset_dispatch 策略分派体系
- dispatch.json、engines.json、强弱对比引擎、HR中注册的所有 handler、具体策略实现

### 方向4：变换单元（transformation unit）完整机制
- 三元组结构、hub节点、分组算法、为什么需要变换单元、与 TDX 原始设计的对应关系

### 方向5：formula_router / DataQuery 完整架构
- formula_router 的接口、DataQuery 的数据获取、批量求值实现、与 _data_cache 的关系

### 方向6：pre_tick / post_tick 流水线
- 具体 stage 列表、每个 stage 的功能、stage_ops 路由机制、数据注入完整路径

### 方向7：runtime_tables 代理机制
- _RUNTIME_TABLE_NAMES、_rt 命名空间、属性代理实现、"运行时表一等公民化"的设计意图

### 方向8：_HR（builtins 函数表）全貌
- _builtins 模块里到底有多少函数、分类（nset策略/条件判断/值提取/等等）、整体架构

---

## 八、总结

**v2.7 是一份高质量的代码理解笔记。**

- **正确率 94 分，比 v2.6 提升 3 分。7 个新方向大部分理解准确，深度显著提升。对表驱动架构、增量计算模型、编译期/运行期分离这些核心设计把握精准。

**主要问题：**
1. 几处细节偏差（_HR本质、_data_cache遗漏、move_exit trigger类型）
2. 数据层和公式系统理解不足
3. nset策略和时机判断还是黑盒

**距离"完全掌握"还差最后一公里。** 再深入 2-3 轮，把数据层、公式层、策略层这三块啃下来，就能支撑大规模重构了。

---

> 审核结论：**通过，质量优秀。建议下一轮重点突破数据层 + 时机门控 + nset策略。
