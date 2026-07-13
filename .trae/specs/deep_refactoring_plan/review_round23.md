# 第23轮迭代 spec_v2.5 代码理解笔记 审核报告

> **审核人**：资深架构审核工程师
> **审核对象**：spec_v2.5.md（v2.5 代码理解笔记）
> **审核日期**：2026-07-01
> **审核方法**：逐点对照真实代码验证
> **总体评分**：**78 / 100**

---

## 一、总体评价

这篇笔记的**核心骨架理解是正确的**，对股票池引擎的触发机制、拓扑执行、数据边界等核心概念把握得比较准，能够帮人快速建立对系统的整体认知。**但细节上有几处明显的理解偏差和遗漏**，特别是在事件发射路径、编译期缓存、无条件边触发逻辑等方面。

**适用性判断**：
- ✅ 作为新人入门的"全景图"——合格
- ⚠️ 作为深度开发的"精确手册"——不够，有几处会误导
- ❌ 作为重构依据——需要先修正错误理解

---

## 二、正确理解的点（12 条）

### 1. 综合设置表格是 3 列布局 ✅
**理解正确**。表头确为"流程标识 | 条件/属性 | 时序/操作"。
- 代码依据：`web/js/editor.js:1355-1356`

### 2. 行有 3 种类型：source / pool-decl / edge ✅
**理解正确**。`buildTableRows` 函数确实构建了这三种行类型。
- 代码依据：`web/js/editor.js:1868-1876`（source）、`1911-1919`（pool-decl）、`1891-1899`（edge）

### 3. 行顺序是 DZH 深度优先遍历顺序，不是执行顺序 ✅
**理解正确**。`processPoolRecursive` 函数实现了"边批量→逐池声明+立即递归"的深度优先模式。真实执行顺序是拓扑序。
- 代码依据：`web/js/editor.js:1880-1928`、`core/engine.py:3606`

### 4. 边类型由源节点类型决定，不是目标节点 ✅
**理解正确**。经 42 个 DZH 文件 602 条边全量验证，边类型 100% 由源节点类型决定。
- 代码依据：`config/edge_semantics.json:1`

### 5. 触发条件是 edge_fired AND (node_dirty OR data_dirty) ✅
**理解正确**。这是 Phase 2 事件驱动模型的核心公式。
- 代码依据：`core/engine.py:3625-3627`

### 6. "时间是门，变化是燃料"的比喻 ✅
**理解正确且精准**。时间条件（gate）决定"能不能执行"，变化（dirty）决定"要不要执行"，两者缺一不可。
- 代码依据：`core/engine.py:3622-3629`

### 7. 节点股票在 filter 执行过程中立即更新 ✅
**理解正确**。`_filter_unconditional` 和 `_filter_conditional` 中直接修改 `node_stocks[tid]` 和 `node_stocks[sid]`。
- 代码依据：`core/engine.py:2513-2524`、`2705`

### 8. 拓扑序 + dirty 级联，同 tick 内多级传播 ✅
**理解正确**。按 topo_order 遍历，边执行后立即 `_mark_dirty(tid)`，下游边在同一个 tick 内就能感知到。
- 代码依据：`core/engine.py:3606`、`3638-3641`

### 9. 引擎只持有当前 bar 快照，不持有完整K线 ✅
**理解正确**。引擎只有 `_latest_tick`、`current_bar_data` 等快照数据，完整K线由 `DataQuery` 负责。
- 代码依据：`core/engine.py:2095-2115`、`core/formula_router.py:18-21`

### 10. 分钟线合成在 Min1Aggregator，不在引擎内部 ✅
**理解正确**。`Min1Aggregator` 是独立服务，通过 `pre_tick` 的 `stage_minute_aggregator_feed` 喂数据。
- 代码依据：`services/minute_aggregator.py:33-42`、`config/pre_tick_pipeline.json:14-22`

### 11. pre_tick 是数据层，_run_tick_event_driven 是计算层 ✅
**理解正确**。代码中有非常明确的职责边界注释。
- 代码依据：`core/engine.py:3550-3555`

### 12. TTL 检查是单条边级的 post hook，不是全局统一 ✅
**理解正确**。`apply_ttl` 是 post_propagate_hooks 的第 3 个 hook，且有 when 条件限制节点类型。
- 代码依据：`config/edge_strategies.json:274`、`core/engine.py:3007-3013`

---

## 三、错误理解的点（8 条）

### 错误 1：事件有两条发射路径——post hook 立即发 + tick 末批量发 ❌
**实际情况**：只有一条发射路径——**全部在 tick 末通过 `_emit_transfer_events` 批量发射**。

`_post_handle_new_entries` 做的事情是：
1. 检测新入池股票
2. 创建 tracker
3. **往 `tevs` 列表 append 一条 transfer_event 记录**（不是立即发事件）
4. 调用回调函数 `_on_stock_enter_target_pool`（这是回调，不是事件发射）

真正调用 `_push_event` 的地方只有两处：
- `_emit_domain_event` 中（在 `_emit_transfer_events` 内调用）
- `_post_tick` 的 alerts stage 中

**影响**：这个误解可能导致开发者认为事件是实时的，但实际上所有领域事件都是 tick 末批量发射的。
- 代码依据：`core/engine.py:2974-3005`（`_post_handle_new_entries` 只 append 到 tevs）、`3230-3237`（`_push_event` 只在 `_emit_domain_event` 中调用）、`3660-3661`（tick 末才调用 `_emit_transfer_events`）

### 错误 2：3 种 gate 评估器对应 3 种运行模式——理解偏了 ❌
**实际情况**：gate_evaluator_id 确实有 3 种（live_gate / replay_gate / virtual_gate），但 `_should_trigger_edge` 的实际实现是：

- **replay 模式**：`_should_fire_flow_replay`（begin/end/interval）+ `_tdx_should_execute` + `_tdx_check_duration`
- **live / simulation 模式**：`_tdx_should_execute` + `_tdx_check_duration`

也就是说，live 模式和 simulation 模式用的是同一套 `_tdx_should_execute` + `_tdx_check_duration` 逻辑，并没有独立的 live_gate 函数实现。`timing.json:gate_evaluator` 配置表目前更像是规划中的路由表，实际代码还是硬编码的 if/else。

**影响**：以为 gate 评估已经完全表驱动了，实际还没有。
- 代码依据：`core/engine.py:1757-1785`（`_should_trigger_edge` 是硬编码的 mode 判断）

### 错误 3：行类型是 3 种——漏了条件节点行 ❌
**实际情况**：`editor.js:1357` 的注释写的是"行类型: 备选池行 / 条件行 / 属性行"。

虽然 `buildTableRows` 构建出来的行类型确实是 `source` / `pool-decl` / `edge` 三种，但**edge 行的中列上半部分显示的是条件节点信息**，从用户视角看，条件节点是作为边的一部分展示的。

笔记中说"每行 = 一条复合边"是对的，但把行类型归纳为 3 种并对应 3 种后端对象，容易让人忽略"条件节点作为边的一部分存在"这个重要的 UI 设计决策。

**影响**：不算严重错误，但表述不够精确。
- 代码依据：`web/js/editor.js:1357`（注释写的是 3 种：备选池行 / 条件行 / 属性行）

### 错误 4：starttype=6 的名称是"trading_day / 交易日"——不准确 ❌
**实际情况**：
- `starttype_rules` 中叫 `"trading_time"`
- `simulator.begin_type_labels` 中叫 `"交易日"`
- `begin_mode_map` 中叫 `"trading_day"`

而且 `starttype_rules["6"]` 的 primitive 是 `"hhmmss"`，op 是 `">="`，语义是"从 starttimehms 时刻开始"，不是"整个交易日都触发"。和 starttype=0（immediate，always）是不一样的。

笔记中说 starttype=6 是"总是触发（交易日）"，这个描述容易让人以为和 starttype=0 一样，实际它是"指定 HHMMSS 时间后触发"。

- 代码依据：`config/timing.json:10`（`"6": {"name": "trading_time", ...}`）、`timing.json:70`（begin_type_labels: "交易日"）、`timing.json:174`（begin_mode_map: "trading_day"）

### 错误 5：cxtype 是"持续期"的概念，不是简单的"结束时间点"——半对半错 ❌
**实际情况**：cxtype 确实是持续期概念（forever / duration / once），但笔记说"end 也有多种模式"，然后只列了 cxtype 的 3 种，混淆了两个概念：

- **cxtype**（持续期类型）：3 种（0=forever, 1=duration, 2=once）
- **end_type**（回放模式的结束类型）：有 0,1,2,3,4,7 共 6 种（在 `simulator.end_type_handlers` 中）

笔记的"错误12"中说"end 也有多种模式（无限/从首次触发计时/一次/指定时间）"，但实际代码中：
- cxtype 是给 live/simulation 模式用的持续期概念
- end_type 是给 replay 模式用的结束时间概念

两者不是一回事，笔记把它们混在一起了。

- 代码依据：`config/timing.json:13-28`（cxtype_rules 只有 3 种）、`timing.json:93-99`（end_type_handlers 有 6 种）

### 错误 6：触发判定的层次——replay gate 是最外层 ❌
笔记中列的层次是：
```
1. replay gate（回放模式才有） — begin/end/interval
2. tdx_should_execute — starttype（开始时机）
3. tdx_check_duration — cxtype（持续期/是否过期）
4. node_dirty OR data_dirty — 变化检测
```

**实际情况**：`_should_trigger_edge` 的顺序确实是 replay gate → tdx_should_execute → tdx_check_duration，但注意 `_tdx_check_duration` 返回 `True` 表示"已过期"（应该跳过），所以函数里是 `if self._tdx_check_duration(edge): return False`。

这个语义理解是对的，但笔记漏掉了一个重要细节：**interval 检查是在 `_should_fire_flow_replay` 内部做的**（仅 replay 模式），live 模式的 interval 检查在哪里？

实际上，在 `_post_record_execution` 中记录了 `_flow_last_fire_ts`，但我在 `_should_trigger_edge` 的 live 模式路径中没有看到 interval 检查。这意味着 **live 模式下 interval 可能没有生效**，或者在其他地方检查。

- 代码依据：`core/engine.py:1757-1785`、`1087-1091`（interval 检查只在 `_should_fire_flow_replay` 中）

### 错误 7：无条件边"源节点变化即反映到目标"——在事件驱动模型下不对 ❌
笔记中说无条件边的触发规则是"源节点变化即反映到目标"，这在语义层面是对的，但在事件驱动模型下，无条件边的触发也会经过 `_should_trigger_edge` 的 gate 判定。

而且 `_filter_unconditional` 内部有自己的源变更检测（`_has_source_changed` 逻辑），如果源节点没变化，直接返回 False 跳过。

更重要的是：**无条件边也受 data_dirty 影响吗？**

从 `_run_tick_event_driven` 的代码看，所有边（包括无条件边）都使用同一个触发公式：
```python
triggered = fired and (self._is_dirty(sid) or self._is_data_dirty())
```

这意味着无条件边也需要 `data_dirty` 或 `node_dirty` 才会触发。但无条件边的语义应该是"源节点变化就传播"，理论上不需要 data_dirty。这里可能有设计上的微妙之处，笔记没有讲清楚。

- 代码依据：`core/engine.py:3625-3627`（所有边用同一个触发公式）、`2490-2495`（`_filter_unconditional` 内部有源变更检测）

### 错误 8：更高周期 K 线"应该是在 DataQuery 中通过聚合 1 分钟线实现的"——猜测，不是事实 ❌
笔记说"更高周期（5m/15m/30m/60m）的合成代码里没有看到专门的合成器，应该是在 DataQuery 中通过聚合 1 分钟线实现的。"

这是**猜测**，不是从代码中读到的真相。作为"代码理解笔记"，应该明确区分"代码证实的"和"推测的"。

实际上 `DataQuery.get_kline_series` 是怎么实现更高周期的，应该去读代码确认，而不是用"应该"这种模糊表述。

- 代码依据：`services/data.py:85-130`（需要确认高周期实现方式）

---

## 四、遗漏的重要点（7 条）

### 遗漏 1：CompiledSchedule 编译期缓存（非常重要）⚠️
笔记完全没有提到 `CompiledSchedule` 类和 `_get_compiled` 机制。这是整个引擎性能优化的核心——拓扑序、edge_ctx、edge_flow_spec、edge_filter_spec 等都是**编译期一次性计算并缓存**的，不是每 tick 重算。

这个概念对于理解系统的性能特征和架构设计非常重要。漏掉了这个，读者会以为每 tick 都在做拓扑排序和边解析。

- 代码依据：`core/engine.py:228-256`（CompiledSchedule 类定义）、`1703-1708`（`_get_compiled`）

### 遗漏 2：_update_node_snapshot 自动标脏机制 ⚠️
笔记提到了 dirty 标记机制，但漏掉了 `_update_node_snapshot` 这个关键函数——它不仅更新快照，还**自动检测变化并标脏**。

在 `_tick` 函数中，pre_tick 之后会对**所有节点**调用 `_update_node_snapshot`，这样外部对 node_stocks 的修改（比如手工添加股票、pre_tick 注入等）会被自动检测并标记为 dirty。

这个机制是"数据层与计算层分离"的关键桥梁——数据层只管改数据，计算层通过快照对比自动发现变化。

- 代码依据：`core/engine.py:2188-2207`（`_update_node_snapshot` 自动标脏）、`3658-3659`（tick 开始时对所有节点调用）

### 遗漏 3：两种快照机制（prev vs _node_snapshots）⚠️
代码中有两套快照：
1. `prev = self._snapshot_node_stocks(node_stocks)` —— 在 `_tick` 开始时手动保存，用于 `_emit_transfer_events` 对比前后变化
2. `self._node_snapshots` —— 由 `_update_node_snapshot` 维护，用于 dirty 标记

两者用途不同：
- prev 是"tick 前的完整快照"，用于事件发射时的全量对比
- _node_snapshots 是"增量式的节点快照"，用于变化检测和脏标记

笔记只提到了一个 snapshot，容易混淆。

- 代码依据：`core/engine.py:3655`（prev 快照）、`2031-2035`（`_snapshot_node_stocks`）、`2188-2207`（`_update_node_snapshot`）

### 遗漏 4：变换单元（三元组）在实际执行中并未使用 ⚠️
笔记花了不少篇幅讲变换单元（三元组），但没有说明一个重要事实：**在 `_run_tick_event_driven` 中，实际是按边（edge）遍历执行的，不是按变换单元（unit）遍历**。

`_group_transformation_units` 函数确实存在，`CompiledSchedule` 里也有 `units` 和 `standalone_edges` 字段，但 `_run_tick_event_driven` 用的是 `compiled.out_edges` 邻接表按边遍历。

变换单元目前更像是一个"规划中的优化"或"策略层概念"，在核心执行路径中并没有实际使用。这个状态应该说清楚。

- 代码依据：`core/engine.py:3608-3613`（按 out_edges 遍历，不是按 units）

### 遗漏 5：3 种 filter_type 的分派逻辑 ⚠️
笔记列出了 3 种 filter_type（unconditional / conditional / formula_eval），但没有说明**怎么决定一条边用哪种 filter_type**。

实际上 filter_type 是在编译期（`_compile_pool` 中）根据边的类型和配置决定的，存储在 `compiled.edge_ctx[eid]['filter_type']` 中。这个分派逻辑对于理解不同边的执行路径差异很重要。

- 代码依据：`core/engine.py:245-246`（CompiledSchedule.edge_ctx 包含 filter_type）

### 遗漏 6：tracker 更新是独立阶段，不在边执行流程内 ⚠️
笔记提到了 `_update_trackers`，但没有说明它的位置和作用。

`_update_trackers` 在 `_run_tick_event_driven` 之后、`_emit_transfer_events` 之前调用，它做两件事：
1. 更新每只股票的 current_price
2. 按拓扑序计算 tracker 公式（如收益、持仓天数等）

这是一个独立的计算阶段，不依赖于边的执行，而是对所有有 tracker 的股票统一更新。

- 代码依据：`core/engine.py:3049-3077`、`3660-3661`

### 遗漏 7：信号（signal）与事件（event）的区别 ⚠️
笔记通篇讲"事件发射"，但没有区分 event 和 signal 两个概念：

- **event**：通过 `_push_event` 放入 `_event_queue`，是领域事件（入池、出池等）
- **signal**：通过 `_push_signal` 放入 `_signal_queue`，是交易信号（BUY/SELL 等）

两者在 `_emit_domain_event` 中同时发射，但用途和消费者不同。笔记只讲了 event，没提 signal。

- 代码依据：`core/engine.py:3078-3085`（`_push_event` 和 `_push_signal` 是两个独立方法）、`3230-3252`（`_emit_domain_event` 中同时发射 event 和 signal）

---

## 五、12 个"纠正的错误"逐一复核

| 序号 | 纠正内容 | 真的错了吗？ | 复核结论 |
|------|---------|-------------|----------|
| 错误1 | 行对应"计算单元" → 行对应"复合边" | ✅ 原理解确实错了 | 纠正正确 |
| 错误2 | 行顺序=执行顺序 → 行顺序=显示顺序，执行按拓扑序 | ✅ 原理解确实错了 | 纠正正确 |
| 错误3 | 触发时间到了就执行 → 需要时间+变化双条件 | ✅ 原理解确实错了 | 纠正正确 |
| 错误4 | 数据更新但时间没到会缓存计算 → 时间没到直接跳过，用dirty累积 | ✅ 原理解确实错了 | 纠正正确 |
| 错误5 | 每条边执行完立即发事件 → 有两条发射路径 | ⚠️ 纠偏了，但又偏了 | 实际只有一条发射路径（tick末批量），post hook 只是累积事件不是立即发 |
| 错误6 | 引擎持有完整K线 → 只持有当前bar快照 | ✅ 原理解确实错了 | 纠正正确 |
| 错误7 | K线合成在引擎内部 → 在独立的 Min1Aggregator | ✅ 原理解确实错了 | 纠正正确 |
| 错误8 | 边类型由目标节点决定 → 由源节点决定 | ✅ 原理解确实错了 | 纠正正确 |
| 错误9 | 综合设置是节点列表 → 是边的列表 | ✅ 原理解确实错了 | 纠正正确 |
| 错误10 | TTL是全局统一检查 → 是边级post hook | ✅ 原理解确实错了 | 纠正正确 |
| 错误11 | 数据更新通过事件订阅 → 通过dirty标记 | ✅ 原理解确实错了 | 纠正正确 |
| 错误12 | begin/end是简单时间点 → 有多种模式 | ⚠️ 部分正确但混淆概念 | begin 确实有 8 种，但 end 分 cxtype 和 end_type 两个体系，笔记混在一起了 |

**小结**：12 个纠正中，10 个完全正确，2 个（错误5、错误12）纠偏后又产生了新的偏差。

---

## 六、评分明细

| 维度 | 得分 | 权重 | 加权分 | 说明 |
|------|------|------|--------|------|
| 综合设置表格理解 | 90 | 15% | 13.5 | 3列、3行类型、DZ序都对，只是行类型表述略粗 |
| 触发机制理解 | 80 | 20% | 16.0 | 核心公式对了，但 gate 评估器、interval、无条件边触发有偏差 |
| 执行流程理解 | 75 | 25% | 18.75 | 拓扑序、dirty级联、节点立即更新都对，但事件发射路径理解错了 |
| 数据边界理解 | 90 | 15% | 13.5 | 引擎快照、DataQuery、Min1Aggregator 都对，高周期是猜测 |
| 错误纠偏质量 | 80 | 15% | 12.0 | 12个纠正中10个正确，2个有新偏差 |
| 完整性（遗漏点） | 60 | 10% | 6.0 | 漏掉了编译期缓存、自动标脏、信号系统等重要概念 |
| **总分** | - | 100% | **78.75** | 约 78 分 |

---

## 七、下一轮应该深入搞清楚的问题

### 优先级 1（必须搞清楚）
1. **无条件边在事件驱动模型下的触发语义**：无条件边也需要 data_dirty 吗？还是设计上只需要 node_dirty？当前代码是所有边共用同一个触发公式，这是正确的还是临时实现？
2. **live 模式下的 interval 检查**：在 `_should_trigger_edge` 的 live 模式路径中，interval 检查在哪里？还是说 live 模式下 interval 还没实现？
3. **变换单元（三元组）的实际使用场景**：编译期有 units，为什么执行期按边遍历？变换单元是给谁用的？策略层？还是未来优化？

### 优先级 2（重要但不紧急）
4. **CompiledSchedule 编译的完整流程**：哪些东西是编译期计算的？哪些是运行期计算的？编译缓存的 key 是什么？
5. **filter_type 的分派逻辑**：什么边用 unconditional？什么边用 conditional？什么边用 formula_eval？
6. **信号系统（signal）的完整链路**：BUY/SELL 信号是怎么产生的？和 pool_enter/move_exit 事件是什么关系？

### 优先级 3（深化理解）
7. **高周期 K 线的实现方式**：DataQuery 是怎么生成 5m/15m/30m/60m 的？真的是聚合 1 分钟线吗？还是有单独的 parquet 文件？
8. **_on_stock_enter_target_pool 回调的用途**：这个回调是给谁用的？和事件系统的关系是什么？
9. **post_tick 流水线的具体作用**：PK排名、多分析角度、看盘面板、监控告警各自的输入输出是什么？

---

## 八、总结

这篇笔记的**主干理解是扎实的**（约 78% 正确），对股票池引擎的核心机制——事件驱动、拓扑执行、dirty 级联、数据分层——都有准确的把握。对于想要快速了解系统全貌的人来说，是一份不错的入门材料。

但**细节上有几处硬伤**，特别是：
1. **事件发射路径理解错误**（以为 post hook 立即发，实际都是 tick 末批量发）
2. **漏掉了编译期缓存这个重要的架构设计**
3. **无条件边的触发语义没有讲清楚**

如果要基于这篇笔记做深度开发或重构，建议先把上述 3 个问题搞清楚，否则可能会走弯路。

**一句话评价**：七分准确三分偏，入门够用深用险。
