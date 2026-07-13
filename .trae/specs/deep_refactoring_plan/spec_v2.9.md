# v2.9 代码理解笔记（专题篇：unconditional 语义深挖）

## 1. 问题提出（用户指出的逻辑矛盾）

用户指出了一个根本性的逻辑问题：

> "unconditional 都叫无条件，还有什么触发条件？"——如果叫无条件，就应该源一变就传，不应该还有触发条件。

但之前的代码理解里说："unconditional 顶层触发条件与 conditional 相同（都是 fired AND 脏标记），差异在内部"。

这说明代码里的实现可能有概念混乱——为了"统一"把两种完全不同的边塞进了同一套机制里。

---

## 2. 代码里 unconditional 的真实实现

### 2.1 触发机制（顶层 gate 层）

**关键发现：所有边（包括 unconditional）都经过相同的顶层触发检查，没有任何区分！**

在 `_run_tick_event_driven` 中（engine.py:3621-3628）：

```python
# Phase 2: edge_fired = 时间触发判定（复用 _should_trigger_edge，含 gate + cxtype）
fired = self._should_trigger_edge(edge, {'cur_ts': self._get_cur_ts()})
self._mark_edge_fired(eid, fired)

# Phase 2: triggered = edge_fired AND (node_dirty[sid] OR data_dirty)
sid = ec['sid']
triggered = fired and (self._is_dirty(sid) or self._is_data_dirty())
if not triggered:
    continue
```

**`_should_trigger_edge` 对所有边一视同仁**，执行完全相同的检查（engine.py:1757-1785）：
1. replay 模式：`_should_fire_flow_replay`（begin/end/interval）
2. 所有模式：`_tdx_should_execute`（starttype 表驱动守门）
3. 所有模式：`_tdx_check_duration`（cxtype 持续期守门）

**没有任何 `if filter_type == 'unconditional'` 的特殊分支。**

### 2.2 筛选层（_filter_unconditional 内部）

进入筛选层后，`_filter_unconditional` 做了这些事（engine.py:2476-2526）：

1. **源变更检测**（engine.py:2490-2495）：源股票集未变则跳过，返回 False
2. **流转属性解析**（engine.py:2497-2504）：读编译期 edge_flow_spec
3. **记录目标池快照**（engine.py:2506-2507）：用于后处理检测新入池
4. **passed_codes = 全部源股票代码**（engine.py:2509-2511）：核心——不过滤
5. **目标池更新**（engine.py:2513-2520）：io=True 替换，否则合并
6. **源池更新**（engine.py:2522-2524）：mv=True 时移除已传递股票

**核心特征：没有过滤条件，全部源股票通过。**

### 2.3 配置中的定义

**edge_semantics.json 中的定义**：

```json
"unconditional": {
  "name": "无条件转移边",
  "description": "源为条件/公式节点的边。无时间属性，只有 from/to/attr/clr 四个基本属性 + size(线宽)。源节点变化即反映到目标",
  "source_types": ["transfer_condition", "tdx_condition", "dzh_condition_pool"],
  "trigger_rule": "source_changed",
  "flow": ["propagate"],
  "has_time_attributes": false,
  "cache_filter_result": false,
  "role_in_unit": "out_edge"
}
```

**edge_strategies.json 中的 pipeline_phases 设计**（edge_strategies.json:304-337）：

| 阶段 | unconditional | conditional/formula_eval |
|------|--------------|--------------------------|
| gate | ✅ 有 | ✅ 有 |
| src_change_detect | ✅ 有 | ❌ 无 |
| replay_guard | ❌ 无 | ✅ 有 |
| tdx_guard | ❌ 无 | ✅ 有 |
| duration_guard | ❌ 无 | ✅ 有 |
| flow_resolve | ✅ 有 | ✅ 有 |
| transform | ✅ 有 | ✅ 有 |
| post_process | ✅ 有 | ✅ 有 |

**注意：配置设计上，unconditional 没有 replay_guard/tdx_guard/duration_guard，但实际代码里 _should_trigger_edge 对所有边都执行这些检查。**

---

## 3. unconditional vs conditional 的全面对比（12个维度）

| 维度 | unconditional | conditional | 代码引用 |
|------|--------------|-------------|---------|
| **1. 命名含义** | "无条件"边 | "条件"边 | edge_semantics.json |
| **2. 语义定位** | 变换单元出边 | 变换单元入边 | edge_semantics.json:role_in_unit |
| **3. 源节点类型** | 条件节点（type201） | 备选池/状态池/数据源 | edge_semantics.json:source_types |
| **4. 配置设计的 trigger_rule** | source_changed | gate_and_change | edge_semantics.json:trigger_rule |
| **5. 配置设计的 flow 步骤** | [propagate] | [gate, filter, propagate] | edge_semantics.json:flow |
| **6. 配置设计的时间属性** | 无（has_time_attributes: false） | 有（interval/begin/end等） | edge_semantics.json:has_time_attributes |
| **7. 实际顶层触发（代码）** | fired AND (dirty OR data_dirty) | fired AND (dirty OR data_dirty) | engine.py:3622-3627 |
| **8. 实际 gate 检查（代码）** | 有（_should_trigger_edge 全执行） | 有（_should_trigger_edge 全执行） | engine.py:1757-1785 |
| **9. 筛选逻辑** | 全部通过（identity） | 按策略过滤（nset_dispatch） | engine.py:2476 vs 2528 |
| **10. 缓存策略** | 无缓存 | 有 nset 缓存 | engine.py:2582-2597 |
| **11. 内部变更检测** | 有（src_change_detect 阶段） | 有（nset_change_detect 阶段） | engine.py:2490-2495 vs 2562-2569 |
| **12. 配置中的 guard 阶段** | 无 replay/tdx/duration guard | 有 replay/tdx/duration guard | edge_strategies.json:307-318 |

---

## 4. "无条件"到底指什么？（结论 + 论证）

### 4.1 结论

**"无条件" = "没有过滤条件"，而不是"没有触发条件"。**

更准确地说：
- **过滤无条件**：筛选阶段恒等（identity），源股票全部通过，不做任何条件过滤
- **触发有条件**：顶层 gate 检查、脏标记检查依然存在

### 4.2 论证

**证据 1：筛选函数命名和实现**

`_filter_unconditional` 的核心逻辑（engine.py:2509-2511）：
```python
# 4. passed_codes = 全部源股票代码
src_stocks = node_stocks.get(sid, [])
passed_codes = {_stock_code(s) for s in src_stocks if isinstance(s, dict)}
```

没有任何 if/条件判断，全部源股票都在 passed_codes 里。这叫"无条件过滤"——过滤条件恒为真。

**证据 2：edge_filter_registry 配置**

edge_strategies.json:228-232：
```json
"unconditional": {
  "op": "identity",
  "propagate_mode": "builtin_propagate",
  "desc": "无条件边：全部源池股票通过（恒等 Filter），内置 propagate"
}
```

明确写了"恒等 Filter"——identity filter，就是过滤操作的恒等函数。

**证据 3：transformations 配置**

edge_strategies.json:250-253：
```json
"unconditional": {
  "passed": {"op": "all_source_codes"},
  "target_update": {"op": "merge_or_replace"},
  "source_update": {"op": "remove_passed_if_move"}
}
```

`passed.op = "all_source_codes"`——通过的就是全部源代码。

**证据 4：与 conditional 的对比**

`_filter_conditional` 里有完整的策略分派、缓存、公式评估等过滤逻辑（engine.py:2528-2627），而 `_filter_unconditional` 没有。两种边的核心差异就在**筛选层**。

---

## 5. 当前实现的问题（概念混乱在哪里？）

### 问题 1：命名误导——"无条件"被理解成了"无触发条件"

"unconditional" 这个名字太容易让人误解为"没有触发条件"。但实际上：
- 它有触发条件（时间 gate + 脏标记）
- 它没有的是**过滤条件**

**应该叫 "pass_through"（直通）或 "identity_filter"（恒等过滤）才准确。**

### 问题 2：配置设计与代码实现不一致

**配置设计（edge_strategies.json pipeline_phases）：**
- unconditional 只有 gate + src_change_detect
- 没有 replay_guard / tdx_guard / duration_guard

**代码实现（_run_tick_event_driven + _should_trigger_edge）：**
- 所有边都经过完全相同的 gate 检查
- unconditional 也跑了 _tdx_should_execute + _tdx_check_duration

**这是一个"表里不一"的问题——配置说一套，代码做一套。**

### 问题 3：双重变更检测——同一概念做了两次

unconditional 边有两次"源变了吗"的检查：

1. **顶层 dirty 检查**（engine.py:3627）：`self._is_dirty(sid)`
2. **_filter_unconditional 内部检查**（engine.py:2491-2495）：`prev_snapshot.get(sid) != 当前源股票集`

**两次检查做的是同一件事——源股票集是否变化。** 只是实现方式不同：
- 顶层用 `_dirty_nodes` 集合标记
- 内部用 `prev_snapshot` 快照比较

为什么？因为 `_filter_unconditional` 是从旧代码里直接搬过来的，它自带变更检测；而新的事件驱动架构又加了一层 dirty 机制。两层叠在一起了。

### 问题 4：为了"统一"把两种不同的边硬塞进同一套机制

从 edge_semantics.json 可以看出，设计上两种边的 trigger_rule 不同：
- unconditional: `source_changed`（源变了就传）
- conditional: `gate_and_change`（gate + 变化）

但代码实现里，为了"统一架构"，把 unconditional 也套进了 `_should_trigger_edge` 的 gate 机制里。结果就是：
- 名字叫无条件
- 配置说 trigger_rule 是 source_changed
- 代码里却也跑了完整的时间门控

**逻辑上不清晰，认知负担重。**

### 问题 5：变换单元的存在让 unconditional 的"无触发"变成了伪命题

unconditional 是变换单元的出边，它的源是条件节点。条件节点的入边是 conditional 边，有完整的时间控制。所以：

```
状态池 → [conditional边，有时间控制] → 条件节点 → [unconditional边] → 下一个状态池
```

即使 unconditional 边本身没有时间控制，条件节点的变化频率也被入边的时间控制住了。所以 unconditional 边"有没有触发条件"在效果上区别不大——因为上游已经限流了。

但这是**拓扑位置导致的结果**，不是 unconditional 边本身的语义。不能因为"上游控制了"就说"这条边不需要控制"。

---

## 6. 从逻辑上讲，unconditional 应该是什么样的？（更清晰的设计）

### 6.1 正确的语义分层

应该把"边"的概念拆成两个独立维度：

| 维度 | 含义 | 取值 |
|------|------|------|
| **触发模式** | 什么时候执行这条边 | time_gated（时间门控） / source_change（源变即传） |
| **筛选模式** | 哪些股票能通过 | identity（全部通过） / conditional（条件过滤） / formula（公式过滤） |

当前的 unconditional/conditional 把两个维度混在一起了：
- "unconditional" = （应该是 source_change 触发） + identity 筛选
- "conditional" = time_gated 触发 + conditional 筛选

### 6.2 更清晰的命名

| 当前名字 | 应该叫 | 含义 |
|---------|--------|------|
| unconditional | pass_through 或 identity | 筛选层恒等，全部通过 |
| conditional | strategy_filter | 按策略筛选 |

触发模式是另一个独立配置项，不和筛选模式绑定。

### 6.3 更简单的实现

如果 unconditional 真的是"源变了就传"，那它的触发逻辑应该是：

```python
# 伪代码
if filter_type == 'pass_through':
    # 直通边：源节点脏了就执行，不需要时间门控
    triggered = self._is_dirty(sid)
else:
    # 非直通边：需要时间门控 + 脏标记
    fired = self._should_trigger_edge(edge, ...)
    triggered = fired and (self._is_dirty(sid) or self._is_data_dirty())
```

然后 `_filter_unconditional` 内部的源变更检测可以删掉，因为顶层 dirty 机制已经做了。

### 6.4 但要小心——unconditional 真的不需要时间控制吗？

这是一个需要业务确认的问题：

**场景 1：变换单元出边**
- 源是条件节点，入边已经控制了频率
- 出边确实不需要额外的时间控制
- 源变了就传是对的

**场景 2：独立 unconditional 边（非变换单元）**
- 源是普通节点，没有上游限流
- 这时候 unconditional 边要不要时间控制？
- 如果不要，那每次 tick 都传，和每 tick 合并节点有什么区别？

从 edge_semantics.json 的验证数据看（602条边），unconditional 边的源基本都是条件节点（231/265），所以大多数情况下"源变了就传"是合理的。

---

## 7. 对股票池本质理解的更新

**一句话：unconditional 边不是"没有触发条件的边"，而是"没有过滤条件的边"——它的"无条件"指的是筛选层的恒等操作，而不是触发层的无时序控制。**

更深层的本质：
- **conditional 边 = "筛选器"**：从源池里挑出满足条件的股票
- **unconditional 边 = "传声筒"**：把条件节点的计算结果原封不动搬到状态池

变换单元（三元组）才是完整的计算单元：
```
[conditional入边：筛选] → [条件节点：计算] → [unconditional出边：搬运]
```

unconditional 边的存在是为了架构上的对称性——让"条件节点"也符合"节点+边"的图模型，而不是让条件节点直接修改目标池。
