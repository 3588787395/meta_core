# 第27轮迭代专题评审报告：unconditional 语义深挖

**评审对象**：spec_v2.9.md（v2.9版本，unconditional 语义专题深挖笔记）  
**评审人**：资深架构审核工程师  
**评审日期**：2026-07-01  
**专题评分**：**78分**

---

## 一、总体评分说明

| 评分维度 | 得分 | 满分 | 说明 |
|---------|------|------|------|
| 核心结论准确性 | 22 | 25 | 核心结论"无条件=没有过滤条件"正确，但表述不够精确 |
| 代码验证充分性 | 18 | 25 | 关键代码路径都覆盖了，但部分细节验证不深 |
| 对比分析全面性 | 16 | 20 | 12维度对比框架很好，但有遗漏维度 |
| 问题诊断深度 | 12 | 15 | 识别出的问题基本准确，但部分问题定性偏严重 |
| 建议合理性 | 10 | 15 | 建议方向正确，但缺乏可落地性和风险分析 |
| **合计** | **78** | **100** | **良好的专题深挖，抓住了主要矛盾，但深度和精度仍有提升空间** |

---

## 二、正确的理解点（✅ 验证通过）

### 1. 核心结论："无条件 = 没有过滤条件，不是没有触发条件" ✅

**验证结果：完全正确。**

代码证据：
- `_filter_unconditional`（engine.py:2509-2511）：`passed_codes = {_stock_code(s) for s in src_stocks if isinstance(s, dict)}` —— 全部源股票通过，无过滤逻辑
- `_filter_conditional`（engine.py:2528-2627）：有完整的策略分派、缓存、公式评估等过滤逻辑
- `edge_strategies.json:228-232`：明确标注 `"op": "identity"` —— 恒等 Filter

这是本专题最核心的洞察，准确抓住了 unconditional 的本质。

### 2. 顶层触发机制：所有边都经过相同的 gate 检查 ✅

**验证结果：完全正确。**

代码证据：
- `_run_tick_event_driven`（engine.py:3621-3627）：对每条边都调用 `_should_trigger_edge`，没有按 filter_type 分支
- `_should_trigger_edge`（engine.py:1757-1785）：内部执行 replay guard + tdx_should_execute + tdx_check_duration，没有任何 unconditional 特殊处理
- 最终触发条件统一为：`triggered = fired and (self._is_dirty(sid) or self._is_data_dirty())`

### 3. 配置设计与代码实现不一致 ✅

**验证结果：正确，但需要补充语境。**

- `edge_strategies.json:304-337` 的 `pipeline_phases` 设计中，unconditional 确实没有 replay_guard/tdx_guard/duration_guard
- 实际代码中 `_should_trigger_edge` 对所有边一视同仁
- 但需注意：`pipeline_phases` 更像是**设计蓝图/文档**，当前代码的 `_process_edge_pipeline` 并没有按这个阶段表逐阶段执行，而是直接调用 `_apply_edge_filter` 分派到三个核心函数

### 4. 双重变更检测问题 ✅

**验证结果：正确。**

确实存在两层变更检测：
1. **顶层 gate 层**（engine.py:3627）：`self._is_dirty(sid)` —— 基于 `_dirty_nodes` 集合
2. **filter 内部**（engine.py:2491-2495）：`prev_snapshot.get(sid) != 当前源股票集` —— 基于快照比较

两层检测的目的都是"源变了才执行"，但实现机制不同，确实存在冗余。

### 5. 变换单元三元组的结构理解 ✅

**验证结果：基本正确。**

`edge_semantics.json` 明确定义了：
- `transformation_unit.hub_node_types`: 条件节点是枢纽
- `in_edge_role`: conditional（入边）
- `out_edge_role`: unconditional（出边）

"筛选→计算→搬运"的三元组模型准确反映了设计意图。

---

## 三、有偏差的理解点（⚠️ 需要修正）

### 1. 偏差："配置和实现表里不一"——定性偏严重

**文档表述**："这是一个'表里不一'的问题——配置说一套，代码做一套。"

**实际情况**：
- `pipeline_phases` 配置更像是**架构设计文档**，而非运行时实际使用的配置表
- `_process_edge_pipeline`（engine.py:2713-2735）并没有按 `pipeline_phases` 逐阶段执行，而是直接调用 `_apply_edge_filter`
- `phase_ops` 中的 `_phase_gate`、`_phase_src_change_detect` 等方法甚至可能未被调用（需进一步验证）

**修正建议**：应表述为"**设计蓝图与当前实现存在差距**"，而非"表里不一"。后者暗示代码有 bug 或欺骗性，但实际是实现简化/演进不同步的问题。

### 2. 偏差：双重变更检测——忽略了 conditional 也有同样问题

**文档表述**：只强调 unconditional 有双重变更检测。

**实际情况**：
- conditional 边同样存在：顶层 `_is_dirty(sid)` 检查 + 内部 `nset_change_detect`（engine.py:2562-2569）
- 而且 conditional 的内部变更检测更复杂，还包含 `bar_changed` 检测（行情数据变化）
- 所以"双重变更检测"是**普遍性问题**，不是 unconditional 特有的

### 3. 偏差：unconditional 内部变更检测的触发条件描述不完整

**文档表述**："源变更检测（源未变则跳过）"

**实际情况**（engine.py:2491）：
```python
if not self._first_run and prev_snapshot is not None:
    src_changed = prev_snapshot.get(sid, frozenset()) != frozenset(...)
    if not src_changed:
        return False
```

有两个前提条件：
1. 不是首次运行（`not self._first_run`）
2. 存在上一次快照（`prev_snapshot is not None`）

首次运行时**跳过变更检测**，直接执行。这个细节很重要，因为它解释了系统启动时的行为。

### 4. 偏差："变换单元"在当前实现中的地位被高估

**文档表述**：变换单元似乎是核心执行单元。

**实际情况**：
- `_group_transformation_units` 函数存在（engine.py:2217），用于将边分组为三元组
- 但在 `_run_tick_event_driven` 的主循环中（engine.py:3605-3641），**并未调用此函数**，而是按边逐条处理
- 变换单元更像是**概念模型/优化方向**，而非当前运行时的实际执行单元

这个偏差会导致对架构现状的误判——以为系统已经按单元执行了，实际还是逐边执行。

### 5. 偏差："_filter_unconditional 是从旧代码里直接搬过来的"——缺乏证据

**文档表述**："为什么？因为 `_filter_unconditional` 是从旧代码里直接搬过来的，它自带变更检测；而新的事件驱动架构又加了一层 dirty 机制。"

**问题**：这是一个**推测性结论**，没有代码历史或注释证据支持。虽然逻辑上合理，但作为"问题诊断"应该标注为推测，而非事实陈述。

---

## 四、遗漏的点（❌ 未覆盖）

### 1. 遗漏：_first_run 机制对 unconditional 的特殊意义

首次运行时：
- `_run_tick_event_driven` 对所有源节点标脏（engine.py:3600-3603）
- `_filter_unconditional` 跳过内部变更检测（engine.py:2491）

这意味着**系统启动时 unconditional 边会无条件执行一次**，用于初始化目标池。这个行为与"源变了就传"的语义在首次运行时有差异。

### 2. 遗漏：data_dirty 对 unconditional 的影响

触发条件是 `fired and (self._is_dirty(sid) or self._is_data_dirty())`（engine.py:3627）。

这意味着即使源节点股票集没变（`_is_dirty(sid)` 为 False），只要行情数据变了（`_is_data_dirty()` 为 True），unconditional 边也会被触发。

但进入 `_filter_unconditional` 后，内部的 `src_changed` 检测会发现源股票集没变，然后返回 False 跳过。

**结果**：data_dirty 会导致 unconditional 边"被触发但实际不执行"——空转一次。这个浪费在 conditional 边那里可能有意义（因为公式需要重新计算），但对 unconditional 边完全是浪费。

### 3. 遗漏：unconditional 边的 mv/io 属性与 conditional 完全相同

虽然筛选逻辑不同，但流转属性（move/overwrite）的处理机制完全一致：
- 都从 `edge_flow_spec` 读取 `is_move` 和 `is_overwrite`
- 都支持合并模式和替换模式
- mv 模式下都从源池移除股票

说明"流转模式"是独立于"筛选模式"的第三个维度——文档第6章提出的二维拆分（触发模式+筛选模式）还不够，应该是三维。

### 4. 遗漏：emptyps（空源处理）只存在于 conditional

`_filter_conditional` 中有 `emptyps` 检查（engine.py:2577）：
```python
if ep.get('emptyps', 0) != 1 and not node_stocks.get(sid, []):
    nset_done = True
    nset_passed = set()
```

unconditional 边没有这个配置和检查——源池为空时，它会把空集"传播"过去（如果是 io 模式，目标池也会被清空）。

这个差异有实际业务意义。

### 5. 遗漏：unconditional 边没有缓存，但 conditional 的缓存机制更复杂

文档只说了"unconditional 无缓存，conditional 有 nset 缓存"，但没说清楚：
- conditional 的缓存键是 `_unit_cache_key`，包含源节点+边ID
- 缓存命中条件是 `not src_changed and not bar_changed`
- 缓存的是**完整的目标池股票列表**（包括所有附加属性）
- unconditional 不需要缓存是合理的——因为它只是恒等变换，计算成本极低

### 6. 遗漏：_update_node_snapshot 与 dirty 机制的关系

文档提到了顶层 dirty 检查和 filter 内部快照比较，但没讲清楚 `_update_node_snapshot` 的作用：
- `_tick` 函数中（engine.py:3658-3659），在调用 `_run_tick_event_driven` 之前，会对所有节点调用 `_update_node_snapshot`
- `_update_node_snapshot`（engine.py:2188-2207）会比较当前股票集与上一次快照，如果变化了就 `_mark_dirty(nid)`
- 这是 dirty 标记的主要来源——不是边执行时标脏，而是 tick 开始时检测节点变化标脏

这个机制解释了 dirty 标记的生命周期。

---

## 五、对七个审核要点的逐条回答

### 要点1："无条件 = 没有过滤条件，不是没有触发条件"——对吗？

**答：核心正确，但需要更精确的表述。**

精确表述应该是：
> "unconditional" 中的 "无条件" 指的是**筛选层的过滤条件恒为真**（identity filter），而非触发层没有条件。实际上它有两层触发条件：时间门控（gate）+ 变更检测（dirty/data_dirty）。

评分：9/10 —— 抓对了本质，但表述可以更严谨。

### 要点2：代码里的真实实现，描述对吗？

**答：大体正确，但有5处偏差/遗漏。**

具体偏差见第三节，主要是：
- 首次运行机制未提及
- data_dirty 的影响未分析
- 变更检测的前提条件未说明

评分：7/10 —— 主路径正确，细节有缺失。

### 要点3：unconditional vs conditional 的对比，全面吗？准确吗？

**答：框架很好（12个维度），但遗漏了几个重要维度。**

遗漏的维度：
- 空源处理（emptyps）
- 行情数据变更检测（bar_changed）
- 流转属性（mv/io）——两者相同，但也是一个对比维度
- 首次运行行为

评分：8/10 —— 覆盖面广，深度足够，但仍有补充空间。

### 要点4：配置和实现"表里不一"的问题，真的存在吗？严重吗？

**答：存在，但严重程度被高估了。**

- 存在性：✅ 确认存在——pipeline_phases 设计与实际代码不符
- 严重性：⚠️ 中等偏低——因为 pipeline_phases 更像是设计文档，不是运行时配置
- 真正的问题：**设计与实现的不同步会导致认知混乱**，后来者可能以为系统是按阶段执行的，实际不是

评分：6/10 —— 问题定性不准确，严重程度偏高。

### 要点5："双重变更检测"的问题，真的是问题吗？

**答：是问题，但不是 unconditional 特有的问题。**

- unconditional：顶层 dirty + 内部 src 快照比较
- conditional：顶层 dirty + 内部 src+bar 变更检测

两种边都有双重检测。这个问题的本质是：**架构演进过程中，旧的检测机制（快照比较）没有被新的机制（dirty 标记）完全替代**，导致冗余。

但需要注意：冗余不等于错误。在某些边界情况下（比如 dirty 标记漏标），快照比较可以作为兜底。只是性能上有浪费。

评分：7/10 —— 问题识别正确，但范围判断有误。

### 要点6："变换单元三元组"的本质理解，对吗？

**答：概念理解正确，但对当前实现的地位判断有误。**

- 概念层面：✅ 完全正确——入边筛选+节点计算+出边搬运，三元组成立
- 实现层面：⚠️ 有偏差——当前代码是**逐边执行**，不是按单元执行
- 变换单元更像是**架构理想模型**，用于理解和推理，而非实际执行单元

评分：7/10 —— 本质抓对了，但与现状的关系没说清。

### 要点7：从逻辑上讲，unconditional 边应该是什么样的？——建议合理吗？

**答：方向正确，但缺乏可落地性和风险分析。**

优点：
- 二维拆分（触发模式+筛选模式）的思路很好
- 命名建议（pass_through/identity）准确
- 伪代码示例清晰

不足：
- 没有分析"去掉时间门控"的风险——上游异常时会不会导致下游风暴？
- 没有考虑 data_dirty 的影响——行情数据变了要不要触发 unconditional？
- 没有迁移路径——怎么从当前实现过渡到理想设计？
- 实际上还应该有第三维：流转模式（copy/move/overwrite）

评分：6/10 —— 方向对，但太理想化，不够落地。

---

## 六、总体评价

### 这个专题搞懂了吗？

**答：基本搞懂了，达到了"理解本质"的层次。**

- ✅ 抓住了最核心的混淆点："无条件"指的是筛选层，不是触发层
- ✅ 建立了清晰的对比框架（12个维度）
- ✅ 识别出了几个真实存在的问题（双重检测、配置与实现不同步）
- ⚠️ 对部分问题的定性和范围判断有偏差
- ⚠️ 一些重要细节被遗漏（首次运行、data_dirty 空转、emptyps 等）

**理解层次评级**：**L3 - 深度理解**  
（L1=知其然，L2=知其所以然，L3=能发现问题，L4=能设计改进方案）

### 对理解整体架构有帮助吗？

**答：非常有帮助。**

unconditional 边是理解股票池架构的关键枢纽：
1. 它连接了"条件节点"和"状态池"，是变换单元的出边
2. 它的存在揭示了"节点-边"图模型的完整性——连条件节点的输出也要用边来表示
3. 它与 conditional 的对比，帮助理解"筛选"和"搬运"是两个不同的关注点
4. 它的问题（双重检测、配置不一致）反映了架构演进的轨迹

这个专题选得很好，挖得也比较深，对建立整体架构认知有显著价值。

---

## 七、下一个应该深挖的专题（候选3+）

### 候选1：dirty 标记与变更检测机制的完整梳理

**理由**：
- 本次专题发现了"双重变更检测"问题，但只是冰山一角
- dirty 标记的来源、传播路径、生命周期还不清晰
- 涉及 `_mark_dirty`、`_update_node_snapshot`、`_clear_dirty`、`data_dirty` 等多个机制
- 搞懂这个可以回答"一条边到底什么时候会被触发"这个核心问题

**预期产出**：
- dirty 标记的完整状态机
- 变更检测的三层模型（节点层→边层→filter层）
- 冗余检测的量化分析

---

### 候选2：变换单元（三元组）的真实地位与演进路径

**理由**：
- 本次专题发现变换单元在概念上很重要，但在实现中似乎未被实际使用
- `_group_transformation_units` 函数存在但主循环不用，这是一个很奇怪的架构状态
- 需要搞清楚：是未来得及重构？还是实验性功能？还是有其他调用路径？
- 这个问题关系到对"架构方向"的判断——系统是在向单元化演进，还是保持逐边执行？

**预期产出**：
- 变换单元代码的完整调用链分析
- 单元化执行 vs 逐边执行的优劣对比
- 架构演进路线图建议

---

### 候选3：时间门控（gate）机制的完整解析

**理由**：
- 本次专题发现 unconditional 也经过了完整的时间门控，但为什么？
- `_tdx_should_execute`、`_tdx_check_duration`、`_should_fire_flow_replay` 这三个 gate 的具体逻辑还不清楚
- starttype、cxtype、begin/end/interval 这些时间属性到底怎么工作？
- 搞懂这个才能真正回答"unconditional 到底需不需要时间门控"

**预期产出**：
- 三层 gate 的详细语义和优先级
- starttype 所有模式的行为表
- cxtype 持续期的状态机
- unconditional 去 gate 的风险评估

---

### 候选4（ bonus ）：pipeline_phases 配置的真相

**理由**：
- 本次专题发现 `pipeline_phases` 与实际实现不符
- 但 `phase_ops` 里定义了那么多方法（`_phase_gate`、`_phase_src_change_detect` 等），它们真的没被调用吗？
- 是"架构蓝图"还是"死代码"？需要验证
- 这个问题关系到对代码库健康度的判断

**预期产出**：
- phase_ops 所有方法的调用关系图
- 死代码识别
- 表驱动架构的真实覆盖度评估

---

## 八、给下一轮深挖的建议

1. **深挖前先画调用图**：不要上来就读细节，先用 grep/callgraph 把关键函数的调用关系理清楚，避免"以为是这样，实际是那样"的偏差
2. **区分"设计是什么"和"实现是什么"**：代码库中有不少"设计文档式的配置"（如 pipeline_phases），不要把它们等同于运行时行为
3. **关注边界情况**：首次运行、空源、异常回退——这些地方往往藏着最深刻的设计考量
4. **问题诊断要区分"事实"和"推测"**：事实有代码证据，推测要明确标注，避免把合理推测当成既成事实
5. **建议要考虑风险和迁移**：只说"应该怎样"不够，还要说"为什么现在不是这样"以及"怎么过去"

---

**评审结论**：  
本专题质量良好，核心结论准确，对架构理解有显著帮助。存在的主要问题是：部分细节验证不深、对问题严重性的判断略有偏差、一些重要边界情况被遗漏。建议沿着"变更检测机制"或"时间门控机制"继续深挖，这两个方向与本次专题衔接最紧密，也最有可能产出有价值的架构洞察。
