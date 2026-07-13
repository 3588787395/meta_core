# 代码理解笔记 v2.6 评审报告（第24轮迭代）

> 评审人：资深架构审核工程师  
> 评审对象：spec_v2.6.md  
> 上一版得分：78分  
> 本版得分：**91分**  
> 提升幅度：+13分

---

## 一、总体评分与评价

### 评分：91/100

**相比上一版（78分）的进步：**
- 8条错误纠正全部正确，纠偏质量很高
- 7条遗漏补全基本正确，细节稍有偏差
- 核心机制理解深度显著提升
- 编译期/运行期边界讲得比较清楚

**主要扣分项：**
- edge_flow_spec 字段描述有遗漏（缺少 is_move、is_overwrite、fa 等关键字段）
- _process_edge_pipeline 内部流程理解有偏差
- 缺少对 _filter_cache（条件边缓存）的理解
- 缺少对 post_propagate_hooks 机制的理解
- event_domain_templates 机制没有讲透

---

## 二、8条错误纠正逐条审核

### ✅ 错误1：事件发射路径不是两条，只有一条 —— **正确**

**代码验证：**
- `_post_handle_new_entries` 确实只做 `tevs.append(...)`，不调用任何 emit/push 方法：`engine.py:2997-2999`
- tick 主流程顺序：`_tick()` → `_run_tick_event_driven` → `_emit_transfer_events`：`engine.py:3660-3661`
- `tevs` 贯穿整个 tick，作为 `_run_tick_event_driven` 的入参和返回值：`engine.py:3578-3589`

**结论：完全正确。**

---

### ✅ 错误2：gate 评估器不是完全表驱动，live 和 simulation 共用同一套硬编码逻辑 —— **正确**

**代码验证：**
- `_should_trigger_edge` 内部分派：replay 模式用 `_should_fire_flow_replay` + `_tdx_should_execute` + `_tdx_check_duration`，其他模式只有后两者：`engine.py:1767-1782`
- `_eval_gate` 标记 `[DEPRECATED]`，直接委托 `_should_trigger_edge`：`engine.py:1787-1795`
- `timing.json:gate_evaluator` 表（live_gate/replay_gate/virtual_gate）确实只是声明，没有被实际 dispatch 使用：`timing.json:134-149`

**结论：完全正确。**

---

### ✅ 错误3：cxtype 和 end_type 是两个完全不同的体系 —— **正确**

**代码验证：**
- cxtype 用于 `_tdx_check_duration`（实时模式持续时长守门）：`engine.py:1014-1030`
- end_type 用于 `_should_fire_flow_replay`（回放模式结束类型）：`engine.py:1033-1093`
- 配置表不同：`cxtype_rules` vs `simulator.end_type_handlers`
- 运行时状态不同：`_flow_duration_starts` / `_flow_exec_counts` vs `_flow_first_fire_ts` / `_flow_last_fire_ts`

**结论：完全正确。补充一点：starttype（实时）和 begin_type（回放）也是类似关系，文档里提到了，很好。**

---

### ✅ 错误4：无条件边也受 data_dirty 影响 —— **正确**

**代码验证：**
- 外层 triggered 判定（所有边统一）：`triggered = fired AND (node_dirty[sid] OR data_dirty)`：`engine.py:3625-3627`
- `_filter_unconditional` 内层有源变更检测：`engine.py:2490-2495`
- 两层过滤的语义描述准确：外层是通用门控，内层是 unconditional 特有的源变更检测

**结论：完全正确。理解到位。**

---

### ✅ 错误5：变换单元（三元组）不在核心执行路径中，只在编译期分组用 —— **正确**

**代码验证：**
- `_run_tick_event_driven` 按 `compiled.out_edges` 遍历，不是按三元组：`engine.py:3608-3613`
- `_group_transformation_units` 只在 `_prepare_topology` 中调用：`engine.py:1245-1246`
- `processing_plan` 存在于 `CompiledSchedule` 中，但运行期核心循环不消费：`engine.py:260-262`

**结论：完全正确。**

---

### ✅ 错误6：不存在叫 post_hook 的机制，post 处理是 pipeline 的一个阶段 —— **基本正确，补充细节**

**代码验证：**
- 搜索 `post_hook` 确实无结果
- pipeline 相位定义包含 `post_process`：`engine.py:295`
- `_post_handle_new_entries` 和 `_post_apply_ttl` 是 post 阶段的 handler

**补充：** 文档说"通过 edge_strategies.json 的策略配置调用"，这个描述不够准确。实际上：
- 这些 post handler 是通过 `_run_post_propagate_hooks` 调用的：`engine.py:2896-2918`
- 配置在 `edge_strategies.json` 的 `post_propagate_hooks` 列表中
- 每个 hook 有 `op` 字段，通过 `hook_ops` 表映射到方法名
- 支持 `when` 条件表达式（用 `_eval_when` 求值）

**结论：核心判断正确，但 post 机制的具体调用方式描述不够精确。**

---

### ✅ 错误7：_node_snapshots 不是 prev 快照，是增量更新用的脏检测快照 —— **正确**

**代码验证：**
- `_last_snapshot` 是全量 tick 快照，在 tick 末更新：`engine.py:3644`
- `_node_snapshots` 是按节点单独更新的增量快照，配合 `_mark_dirty` 使用：`engine.py:2188-2207`
- 两者用途完全不同

**结论：完全正确。**

---

### ✅ 错误8：signal 和 event 不是一回事，是两个独立体系 —— **正确**

**代码验证：**
- 两个独立的配置表：`event_rules.json` vs `signal_rules.json`
- 两个独立的队列：`_event_queue` vs `_signal_queue` + `_signal_events`
- 两个独立的 push 方法：`_push_event` vs `_push_signal`
- 都在 `_emit_domain_event` 中统一发射：`engine.py:3231-3252`

**结论：完全正确。**

---

## 三、7条遗漏补全逐条审核

### ✅ 遗漏1：CompiledSchedule 编译期缓存 —— **基本正确，有小遗漏**

**正确的部分：**
- 缓存机制描述正确：md5 哈希、`_compiled_cache` 字典
- 大部分字段描述正确

**需要补充/修正的字段：**

| 字段 | 文档描述 | 实际代码 | 评价 |
|------|---------|---------|------|
| `edge_flow_spec` | `tran, emptyps, attr, propagate_mode, structural_mode` | 还包含 **`is_move`, `is_overwrite`, `fa`**：`engine.py:1615-1618` | ❌ 有遗漏 |
| `edge_timing` | 描述正确 | 实际代码中 `_compile_edge_spec` 的 timing 部分编译了 starttype/cxtype 规则 | ✅ 正确 |
| `edge_ttl_spec` | 描述正确 | 实际包含 `bdel, ndelnum, ndeltype, ttl_sec`，当 bdel=0 时存 `{bdel:0, ttl_sec:0}` | ✅ 基本正确 |

**edge_flow_spec 的实际完整字段**（`engine.py:1605-1623`）：
```python
{
    'tran': tran,              # 转移模式 0/1
    'emptyps': emptyps,        # 空源是否清空目标
    'attr': attr,              # 边属性位掩码
    'propagate_mode': ...,     # 'copy' / 'move' / 'overwrite'
    'structural_mode': ...,    # 结构性传播模式
    'is_move': bool,           # 是否移动（运行期直接读）
    'is_overwrite': bool,      # 是否覆盖（运行期直接读）
    'fa': {...},               # 流转属性字典
}
```

**结论：整体正确，但 edge_flow_spec 有重要字段遗漏。**

---

### ✅ 遗漏2：_update_node_snapshot 自动标脏机制 —— **正确**

**代码验证：**
- 函数逻辑完全正确：比较 frozenset → 不同则更新 + 自动 `_mark_dirty`：`engine.py:2188-2207`
- 调用位置正确：tick 开头逐节点更新、边执行后对目标节点更新
- 冗余标脏的观察正确：`_update_node_snapshot` 内部标一次，后面 `_mark_dirty(tid)` 又标一次

**结论：完全正确。观察细致。**

---

### ✅ 遗漏3：两套快照机制 —— **正确**

**代码验证：**
- `_last_snapshot`（prev）：全量快照，tick 末更新，用于源变更检测、缓存 key：`engine.py:3644`
- `_node_snapshots`：增量快照，随时更新，用于脏标记级联：`engine.py:2188-2207`
- 两者关系描述准确

**结论：完全正确。两套快照讲得很清楚。**

---

### ✅ 遗漏4：变换单元（三元组）—— 编译期分组概念 —— **正确**

**代码验证：**
- 分组逻辑正确：枢纽节点 + 入边 + 出边 = 三元组：`engine.py:2217-2247`
- 实际用途正确：构建 `processing_plan`、拓扑验证
- 核心执行循环不使用三元组：正确
- `change_detection.unit_level = true` 配置声明超前于代码实现：这个观察**非常准确**

**补充：** `_unit_cache_key` 函数是存在的（`engine.py:2293`），但它是给 `_filter_conditional` 做 nset 缓存用的，不是真正的"单元级缓存"。文档的判断是对的。

**结论：完全正确。**

---

### ⚠️ 遗漏5：signal 与 event 的区别 —— **基本正确，但发射流程描述有偏差**

**正确的部分：**
- 两个独立体系的描述正确
- 配置表、队列、push 方法都正确
- 都在 `_emit_domain_event` 中发射：正确

**有偏差的部分：**

文档说"遍历 event_defs 发射事件"、"遍历 signal_defs 发射信号"，这个描述**过于简化**。

实际的发射流程（`engine.py:3197-3252`）：
1. 从 `_event_domain_templates`（edge_strategies.json 中）获取 domain 模板
2. 模板包含 `trigger_match`（事件触发类型匹配）、`signal_trigger`（信号触发类型）
3. 模板包含 `role_ref`（引用哪个角色判断是否目标池）
4. 模板包含 `context_fields`（上下文字段解析规则）
5. 模板包含 `field_resolution`（价格/收益等字段解析规则）
6. **不是所有 event_defs / signal_defs 都发射，而是通过 trigger 类型匹配过滤**

另外，`_emit_transfer_events` 的流程描述也不够完整：
- 它遍历的是 `_event_domain_templates`，不是直接遍历 tevs
- 通过 `_resolve_codes` 确定哪些股票触发（从 tevs 或 prev/current 差值等来源）
- 通过 `_resolve_domain_ctx` 构建领域上下文

**结论：核心判断正确，但发射流程的中间层（event_domain_templates）没有讲透。**

---

### ✅ 遗漏6：脏标记体系——三级脏标记 —— **正确**

**代码验证：**
- `_dirty_nodes`：节点脏标记，`_mark_dirty` 置位，`_clear_dirty` 清除：`engine.py:2062-2093`
- `_data_dirty`：数据脏标记，`_refresh_latest_tick` 置位：`engine.py:2095-2115`
- `_edge_fired`：边时间触发状态，`_mark_edge_fired` 置位：`engine.py:2117-2123`
- 触发判定公式正确：`triggered = edge_fired AND (node_dirty OR data_dirty)`：`engine.py:3625-3627`
- 统一清脏在 `_clear_dirty` 中：正确，包含清 `_dirty_nodes`、`_data_dirty`、`_clear_edge_fired()`

**结论：完全正确。三级脏标记理解到位。**

---

### ✅ 遗漏7：首次执行（_first_run）的特殊处理 —— **正确**

**代码验证：**
- 初始值 True：`engine.py:418`
- 首次执行标脏源节点（入度为0）：`engine.py:3600-3603`
- `_filter_unconditional` 中跳过源变更检测：`engine.py:2491`
- `_filter_conditional` 中也跳过：`engine.py:2566`
- tick 末置 False：`engine.py:3646`
- `_last_snapshot` 初始为 None：`engine.py:412`

**结论：完全正确。**

---

## 四、编译期 vs 运行期边界 —— 审核

**评分：88/100**

### 正确的部分：
- 编译期入口 `_compile_pool` → 缓存：正确
- 编译期预计算的内容大部分正确
- 运行期只读/修改的分类基本正确
- "编译期不依赖任何运行时状态"的判断正确

### 需要补充/修正的：

**1. 运行期还修改了一些重要状态：**
- `_flow_exec_counts`（边执行计数）
- `_flow_duration_starts`（cxtype 持续时长起始时间）
- `_flow_first_fire_ts` / `_flow_last_fire_ts`（replay 模式计时）
- `_filter_cache`（LRU 缓存，条件边计算结果缓存）
- `_latest_tick` / `_latest_tick_hash` / `_latest_tick_ts`（最新行情快照）
- `_exit_tracker_cache`（退出 tracker 缓存）

文档提到了前4个，但漏掉了 `_filter_cache` 和 `_latest_tick` 系列。

**2. 编译期计算的 5 维 spec 表，目前大部分是"编译了但运行期还没完全用上"的状态**

从代码看：
- `edge_flow_spec` 的 `is_move` / `is_overwrite` / `fa` 已经被运行期使用（`_filter_unconditional` 和 `_filter_conditional` 都从 `_current_compiled.edge_flow_spec` 读）
- `edge_timing` / `edge_filter_spec` / `edge_action_spec` / `edge_ttl_spec` 编译了，但运行期核心路径还没消费它们（gate 评估还是走 `_should_trigger_edge` 硬编码，TTL 还是走 `_apply_tdx_psatt_ttl` 运行期查表）

这是一个重要的过渡状态——编译期已经预计算了，但运行期还在逐步迁移中。文档没有提到这个过渡状态。

**结论：整体讲清楚了，但缺少对"过渡状态"的描述，也漏掉了几个运行期状态。**

---

## 五、事件/信号机制的完整流程 —— 审核

**评分：85/100**

### 正确的部分：
- 整体大流程正确：pre_tick → _tick → _run_tick_event_driven → _emit_transfer_events → post_tick
- tevs 是中间记录的判断正确
- 统一发射点在 `_emit_transfer_events` → `_emit_domain_event`：正确

### 不够完整/有偏差的部分：

**1. 漏掉了 event_domain_templates 这个关键中间层**

`_emit_transfer_events` 不是直接遍历 tevs 发射事件，而是：
1. 遍历 `_event_domain_templates`（来自 edge_strategies.json）
2. 每个 domain 模板定义了：
   - `codes_source`：从哪里获取触发的股票代码（如 `transfer_entries`、`ttl_expirations` 等）
   - `trigger_match`：事件触发类型匹配
   - `signal_trigger`：信号触发类型
   - `role_ref`：角色引用
   - `context_fields`：上下文字段解析规则
   - `field_resolution`：价格等字段解析规则
3. 通过 `_resolve_codes` 解析出具体哪些股票触发
4. 对每只股票调用 `_emit_domain_event`

这个 event_domain_templates 是表驱动事件系统的核心，但文档完全没提到。

**2. TTL 超时事件的发射路径描述不够**

文档提到了"从 TTL 检查来的 ttl_expire 超时事件"，但没有说明：
- TTL 检查在哪里做？（`_post_apply_ttl` → `_apply_tdx_psatt_ttl`）
- TTL 淘汰后怎么触发事件？（直接修改 node_stocks，然后通过 `_emit_transfer_events` 的差值检测发现？还是有专门的发射路径？）

实际上，`_apply_tdx_psatt_ttl` 直接修改 `node_stocks` 删除超时股票，但**没有直接调用事件发射**。TTL 事件的产生路径需要再确认。

**3. _post_handle_new_entries 里有回调 _on_stock_enter_target_pool**

这个回调是直接调用的，不走 event/signal 队列：`engine.py:3001-3005`。文档没提到。

**结论：大流程正确，但中间层机制和边缘路径有遗漏。**

---

## 六、三级脏标记体系 —— 审核

**评分：95/100**

### 完全正确的部分：
- 三级标记的定义、置位时机、清除时机都正确
- 触发判定公式正确
- `_clear_dirty` 统一清脏正确

### 小补充：
`_edge_fired` 不只是"记录本 tick 边的时间触发状态"，它还有一个作用：**让下游边能够知道上游边的时间触发状态**。不过在当前的 Phase 2 架构下，`_edge_fired` 主要就是记录本 tick 状态，文档描述是对的。

**结论：理解非常准确。**

---

## 七、两套快照机制 —— 审核

**评分：92/100**

### 正确的部分：
- 两套快照的区别、用途、更新时机都讲得很清楚
- prev 快照用于源变更检测和缓存 key：正确
- _node_snapshots 用于增量脏标记：正确

### 小补充：
`_last_snapshot` 除了文档提到的用途外，还给 `_filter_conditional` 做 `_has_bar_data_changed(prev_bar_hash)` 提供了 `prev_bar_hash`。不过 `prev_bar_hash` 是单独维护的，不是 prev 快照的一部分，所以不算遗漏。

另外，文档没有提到 **_filter_cache（条件边筛选结果缓存）** 这一套缓存机制。这是第三套"类快照"机制——它缓存的是条件边筛选的结果，key 是源节点股票集合 + 行情 hash。这套缓存对性能很重要，但文档完全没提到。

**结论：两套快照的理解正确，但漏掉了 _filter_cache 这个第三套缓存机制。**

---

## 八、新发现的重要遗漏点

### 遗漏点1：_filter_cache —— 条件边筛选结果缓存

**重要程度：高**

位置：`engine.py:417`, `engine.py:2582-2588`, `engine.py:2620-2623`

- 类型：`LRUCache`（带 TTL 的 LRU 缓存）
- 用途：缓存 `_filter_conditional` 的筛选结果
- cache_key 构造：`_unit_cache_key(node_stocks, sid, eid)`
- 命中条件：源节点股票没变化 **且** 行情数据没变化
- 命中时直接返回缓存结果，跳过公式计算
- 这是性能优化的关键机制

文档完全没提到这套缓存。

---

### 遗漏点2：post_propagate_hooks —— 传播后副作用链

**重要程度：中高**

位置：`engine.py:2896-2948`

- 配置来源：`edge_strategies.json:post_propagate_hooks`
- 执行时机：`_process_edge_pipeline` 的步骤2（在 filter 之后）
- 每个 hook 有：`op`（操作类型）、`when`（条件表达式）
- 通过 `hook_ops` 表映射到具体方法
- 已知的 hook：
  - `_post_handle_new_entries`：检测新入池、建 tracker、往 tevs append
  - `_post_apply_ttl`：TTL 过期检查
  - `_post_record_execution`：记录执行次数和首/末次触发时间

文档提到了 `_post_handle_new_entries` 和 `_post_apply_ttl`，但没有把它们放在 `post_propagate_hooks` 这个统一框架下理解。

---

### 遗漏点3：event_domain_templates —— 表驱动事件模板

**重要程度：高**

位置：`engine.py:359`（加载）, `engine.py:3537-3545`（遍历）, `engine.py:3197-3252`（发射）

- 配置来源：`edge_strategies.json:event_domain_templates`
- 定义了各个领域事件（pool_enter, move_exit, ttl_expire 等）的：
  - `codes_source`：触发代码来源
  - `trigger_match`：事件触发类型匹配
  - `signal_trigger`：信号触发类型
  - `role_ref`：角色引用
  - `context_fields`：上下文字段解析
  - `field_resolution`：价格/收益字段解析
- 这是事件系统的"路由表"，决定了什么情况发射什么事件/信号

文档完全没提到这套模板机制。

---

### 遗漏点4：_latest_tick —— 行情唯一真相源

**重要程度：中**

位置：`engine.py:2095-2115`

- `_refresh_latest_tick` 写入 `_latest_tick` 字典
- 这是"行情唯一真相源"
- `_data_dirty` 的置位是通过比较 `_latest_tick_hash` 来判断的
- 公式计算应该从 `_latest_tick` 读数据，而不是从 current_bar_data

文档提到了 `_data_dirty`，但没有提到 `_latest_tick` 这个中间存储层。

---

### 遗漏点5：_process_edge_pipeline 的实际内部流程

**重要程度：中高**

文档说"目前只知道 pipeline 有 8 个相位"，但实际上：

`_process_edge_pipeline` 的实际代码（`engine.py:2713-2735`）非常简单：
```
步骤1：_apply_edge_filter(ctx) —— 按 filter_type 分派到 3 个核心函数
步骤2：_run_post_propagate_hooks(ctx, ...) —— 后处理 hooks
```

也就是说，**原来设计的 8 相位 pipeline，实际上并没有完全按相位执行**。`_DEFAULT_PIPELINE_PHASES` 定义了 8 个相位，但 `_process_edge_pipeline` 内部直接调用 `_apply_edge_filter`，而 `_apply_edge_filter` 又直接调用 `_filter_unconditional` / `_filter_conditional` / `_filter_formula_eval`，这些函数内部自己完成了 flow_resolve、transform 等工作。

换句话说：**8 相位 pipeline 是设计目标，当前代码是"扁平化"的实现，三个 filter 函数各自包含了多个相位的逻辑。**

这是一个重要的架构现状——设计是分阶段的，实现是揉在一起的。

---

### 遗漏点6：三种 filter 类型的完整分派

**重要程度：中**

文档提到了 unconditional 和 conditional，但没有系统梳理三种 filter 类型：

1. `unconditional`：无条件转移，源股票全部通过
2. `conditional`：条件过滤，按 nset_dispatch 策略分派（内部有缓存）
3. `formula_eval`：公式求值，走 formula_router 批量计算

分派入口：`_apply_edge_filter` → `_filter_strategies` 表 → 具体函数

三种 filter 类型是边执行的核心分派逻辑。

---

### 遗漏点7：pool_role 解析机制

**重要程度：中**

文档把这个列为"还没搞懂"的问题，确实还没搞懂。但它很重要，因为：
- 信号发射依赖 `is_target` 判断（`_should_emit_signal_for_domain`）
- `is_target` 来自 `resolved_role`
- 角色解析由 `_resolve_pool_role` 完成
- 配置在 `pool_roles.json` 中

这是信号系统的关键判断条件。

---

## 九、新纠正正确的点 vs 仍然理解错的点

### ✅ 新纠正正确的点（相比上一版）：

1. **事件发射路径**：从"两条路径"纠正为"一条路径+tevs中间记录" —— 完全正确
2. **gate 评估器**：从"完全表驱动"纠正为"硬编码+表声明未使用" —— 完全正确
3. **cxtype vs end_type**：从"同一个东西"纠正为"两个独立体系" —— 完全正确
4. **无条件边与 data_dirty**：从"不受影响"纠正为"外层受影响、内层拦截" —— 完全正确
5. **变换单元**：从"核心执行单元"纠正为"编译期分析概念" —— 完全正确
6. **post_hook 机制**：从"有钩子机制"纠正为"pipeline 阶段" —— 基本正确
7. **两套快照**：从"混淆 prev 和 _node_snapshots"纠正为"两套独立快照" —— 完全正确
8. **signal vs event**：从"同一个东西"纠正为"两个独立体系" —— 完全正确

### ⚠️ 仍然理解不够准确的点：

1. **post 机制的具体调用方式**：不是"通过 edge_strategies 策略配置调用"，而是通过 `post_propagate_hooks` 列表 + `hook_ops` 映射调用
2. **event 发射的中间层**：不是直接遍历 tevs 发射，而是通过 `event_domain_templates` 模板路由
3. **edge_flow_spec 字段**：漏掉了 `is_move`、`is_overwrite`、`fa` 这三个运行期直接使用的关键字段
4. **_process_edge_pipeline 内部流程**：实际是扁平化的两步（filter + post_hooks），不是 8 相位逐个执行

---

## 十、总体评价

### 现在对代码的理解到什么程度了？

**理解程度：良好偏上（91分水平）**

- ✅ 核心执行流程（tick → 边遍历 → 股票流转）理解准确
- ✅ 三级脏标记机制理解透彻
- ✅ 两套快照机制理解准确
- ✅ 编译期缓存架构理解正确
- ✅ 事件与信号的分离理解正确
- ✅ 变换单元的定位理解正确
- ⚠️ 事件发射的中间层机制（event_domain_templates）还没掌握
- ⚠️ 条件边缓存（_filter_cache）完全没提到
- ⚠️ post_propagate_hooks 框架没理解透
- ⚠️ 三种 filter 类型的完整体系没有系统梳理

### 能指导重构了吗？

**结论：基本可以指导重构，但还有风险点**

**可以指导的部分：**
- 拓扑结构相关的重构（安全）
- 脏标记体系相关的优化（安全）
- 快照机制的整理（安全）
- 编译期缓存的完善（安全）

**有风险的部分：**
- 事件系统重构（风险：不了解 event_domain_templates 可能改坏）
- 条件边性能优化（风险：不知道 _filter_cache 可能重复造轮子）
- pipeline 相位重构（风险：以为是 8 相位，实际是扁平化的）

**建议：** 再深入一轮，把事件系统、filter 缓存、pipeline 实际执行路径搞清楚，再开始大规模重构。

---

## 十一、下一轮应该深入搞清楚的 7 个方向

### 方向1：event_domain_templates 完整机制
- 模板有哪些字段？每个字段什么意思？
- codes_source 有哪些类型？怎么解析？
- resolvers 子表是做什么的？
- 与 event_rules.json / signal_rules.json 的关系是什么？

### 方向2：_filter_cache（条件边缓存）完整机制
- `_unit_cache_key` 怎么构造的？
- 缓存命中条件和失效时机是什么？
- 缓存的是什么数据（目标节点股票列表？）
- LRU + TTL 的参数配置在哪里？

### 方向3：_process_edge_pipeline 实际执行路径 vs 设计的 8 相位
- 为什么设计了 8 相位但实际没按相位执行？
- 三个 filter 函数内部各自做了哪些相位的工作？
- 是过渡状态还是最终就这样了？
- edge_strategies.json 里的 pipeline_phases 配置有没有被使用？

### 方向4：三种 filter 类型（unconditional / conditional / formula_eval）的完整对比
- 各自的适用场景？
- 内部流程有什么异同？
- filter_type 是怎么决定的（`_resolve_filter_type`）？
- formula_eval 路径的完整流程（批量求值？逐只求值？）

### 方向5：pool_role（池角色）解析机制
- pool_roles.json 里定义了什么？
- `_resolve_pool_role` 怎么工作的？
- role_resolution 规则怎么匹配？
- is_target / is_source / is_alert 等角色标签的用途？

### 方向6：TTL 淘汰的完整路径
- TTL 检查在哪里触发（post_apply_ttl → apply_tdx_psatt_ttl）
- TTL 淘汰后，股票从节点移除，怎么触发 EXIT 事件和 SELL 信号？
- 是通过 _emit_transfer_events 里的差值检测，还是有专门路径？
- ttl_spec 编译了，但运行期实际用了吗？

### 方向7：edge_flow_spec 各字段的语义和运行期使用方式
- tran / emptyps / attr 三个原始参数的关系
- is_move / is_overwrite 是怎么派生的？
- fa 字典里有什么？
- propagate_mode 和 structural_mode 的区别？
- 运行期哪些地方读这些字段？

---

## 十二、总结

v2.6 相比上一版进步很大，核心机制理解准确率从 78 分提升到了 **91 分**。8 条错误纠正全部正确，7 条遗漏补全基本正确。

**当前理解的盲区主要集中在：**
1. 事件系统的中间层（event_domain_templates）
2. 条件边缓存机制（_filter_cache）
3. pipeline 的实际执行路径 vs 设计
4. post_propagate_hooks 框架

**建议：** 再深入一轮（第25轮），把上述 7 个方向搞清楚，然后就可以开始指导重构了。现在的理解程度做中小规模重构是安全的，但做大的架构调整（特别是事件系统和 pipeline 重构）还有风险。

---

*评审完毕*
