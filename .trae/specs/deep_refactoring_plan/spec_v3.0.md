# v3.0 认知颠覆：unconditional 不是执行单元，只是拓扑连接

> **注意**：本文档是纯理解笔记，不是设计文档。
> 所有结论都有代码引用（文件+行号），不含主观臆断。
> 大版本升级原因：对执行模型的根本性认知纠正。

---

## 1. 认知颠覆：unconditional 的本质

### 用户指出的真相

> **unconditional 边根本就不是执行单元，它只是拓扑连接关系，指示转移节点的输出，无任何行为动作，不触发、不过滤、不执行。**

这句话需要从**概念模型**层面理解，而不是从代码实现的字面意思理解。

### 两层含义

**第一层（概念层）：unconditional 不是独立的计算单元**
- 它没有自己独立的触发条件（没有 starttype/interval/begin/end 等时间属性的"业务语义"）
- 它没有过滤逻辑（恒等过滤，所有源股票都通过）
- 它不是综合设置里的"一行"（综合设置的一行对应完整的变换单元）

**第二层（实现层）：unconditional 边在代码里确实有"执行动作"**
- 但那只是"把源节点的股票搬到目标节点"的机械操作
- 是变换单元执行的"收尾动作"，不是独立的计算步骤

### 一句话总结

> unconditional 边是条件节点的"输出尾巴"，不是独立的计算单元。
> 真正的计算单元是**变换单元**（conditional 入边 + 条件节点 + unconditional 出边）。

---

## 2. 代码验证：unconditional 边在运行时到底做了什么

### 2.1 unconditional 边的定义（配置层）

从 `edge_semantics.json` 看 unconditional 边的完整定义：

```json
{
  "name": "无条件转移边",
  "description": "源为条件/公式节点的边。无时间属性，只有 from/to/attr/clr 四个基本属性 + size(线宽)。源节点变化即反映到目标",
  "source_types": ["transfer_condition", "tdx_condition", "dzh_condition_pool"],
  "target_types": ["stock_state_pool", "tdx_state_pool", "stock_state_fallback", "discard_pool"],
  "trigger_rule": "source_changed",
  "flow": ["propagate"],
  "has_time_attributes": false,
  "attributes": ["from", "to", "attr", "clr"],
  "cache_filter_result": false,
  "role_in_unit": "out_edge"
}
```

**关键信息**：
- `has_time_attributes: false` — 没有时间属性（没有 interval/begin/end/count）
- `flow: ["propagate"]` — 只有传播阶段，没有 gate 和 filter 阶段
- `trigger_rule: "source_changed"` — 源节点变化时触发（被动触发）
- `role_in_unit: "out_edge"` — 在变换单元中扮演出边角色

**代码引用**：
- `config/edge_semantics.json:1` — 边类型语义配置表
- `config/edge_semantics.json:11-15` — unconditional 边定义

### 2.2 unconditional 边的触发判定（运行时）

在 `_run_tick_event_driven` 主循环中，所有边（包括 unconditional）都会经过触发判定：

```python
# Phase 2: edge_fired = 时间触发判定（复用 _should_trigger_edge，含 gate + cxtype）
fired = self._should_trigger_edge(edge, {'cur_ts': self._get_cur_ts()})

# Phase 2: triggered = edge_fired AND (node_dirty[sid] OR data_dirty)
sid = ec['sid']
triggered = fired and (self._is_dirty(sid) or self._is_data_dirty())
```

**对于 unconditional 边**：
- `_should_trigger_edge` 调用 `_tdx_should_execute` → starttype 默认是 0 → 对应 `primitive: "always"` → 永远返回 True
- `_tdx_check_duration` → cxtype 默认是 0 → 对应 `is_expired: "never"` → 永远不过期
- 所以 `fired` 永远是 True
- 最终 `triggered` 只取决于 `node_dirty[sid] OR data_dirty`

**代码引用**：
- `core/engine.py:3621-3627` — `_run_tick_event_driven` 中的触发判定
- `core/engine.py:1757-1785` — `_should_trigger_edge` 函数
- `core/engine.py:781-794` — `_tdx_should_execute` 函数
- `config/timing.json:4` — starttype=0 对应 `primitive: "always"`
- `config/timing.json:14-17` — cxtype=0 对应 `is_expired: "never"`

**结论**：unconditional 边没有自己的"触发时机"——它永远"可以触发"，实际是否触发只取决于源节点是否脏了。这就是 `trigger_rule: "source_changed"` 的真实含义。

### 2.3 unconditional 边的实际执行（filter 层）

`_filter_unconditional` 函数做了什么：

```python
def _filter_unconditional(self, ctx):
    # 1. 源变更检测（源未变则跳过）
    if not self._first_run and prev_snapshot is not None:
        src_changed = prev_snapshot.get(sid, frozenset()) != frozenset(...)
        if not src_changed:
            return False

    # 2. 流转属性解析（读编译期 edge_flow_spec）
    mv = _flow_spec.get('is_move', False)
    io = _flow_spec.get('is_overwrite', False)

    # 3. passed_codes = 全部源股票代码（恒等过滤）
    passed_codes = {_stock_code(s) for s in src_stocks if isinstance(s, dict)}

    # 4. 目标池更新（io=True 替换，否则合并）
    # 5. 源池更新（mv=True 时移除 passed_codes）
```

**关键观察**：
1. **没有过滤计算** — `passed_codes` 就是全部源股票，恒等过滤
2. **源变更检测** — 源没变就直接跳过，这印证了 `trigger_rule: "source_changed"`
3. **只有流转动作** — 把股票从源搬到目标，根据 attr 决定是 move/copy/overwrite

**代码引用**：
- `core/engine.py:2476-2526` — `_filter_unconditional` 函数

### 2.4 pipeline 阶段对比

从 `edge_strategies.json` 的 `pipeline_phases` 看两种边的差异：

| 阶段 | unconditional | conditional | 说明 |
|------|--------------|-------------|------|
| gate | ❌ 无 | ✅ 有 | 条件边有 gate，无条件边没有 |
| src_change_detect | ✅ 有 | ❌ 无 | 无条件边靠源变更检测触发 |
| replay_guard | ❌ 无 | ✅ 有 | 回放模式下的 begin/end/interval |
| tdx_guard | ❌ 无 | ✅ 有 | starttype 时间门控 |
| duration_guard | ❌ 无 | ✅ 有 | cxtype 持续期检查 |
| flow_resolve | ✅ 有 | ✅ 有 | 流转属性解析（attr/tran） |
| transform | ✅ 有 | ✅ 有 | 变换执行 |
| post_process | ✅ 有 | ✅ 有 | 后处理 |

**代码引用**：
- `config/edge_strategies.json:304-337` — pipeline_phases 定义

**结论**：unconditional 边没有 gate、没有 guard、没有触发时机——它只是"源变了就传过去"。

---

## 3. 重新理解执行模型

### 3.1 什么是真正的计算单元？

**真正的计算单元 = 变换单元（Transformation Unit）**

变换单元是一个三元组：
```
conditional 入边 + 条件节点（枢纽） + unconditional 出边
```

它做的事情：
1. **conditional 入边**：决定"什么时候算"（触发时机 gate）和"用什么条件算"（filter 条件）
2. **条件节点**：承载过滤条件的配置（公式、参数等）
3. **unconditional 出边**：把算完的结果搬到目标池（只是搬运，没有计算）

**代码引用**：
- `config/edge_semantics.json:16-23` — transformation_unit 定义
- `core/engine.py:2217-2247` — `_group_transformation_units` 函数实现三元组分组
- `config/edge_strategies.json:197-226` — transformation_unit_strategies 配置

### 3.2 执行顺序里有什么？

从 `_run_tick_event_driven` 看，运行时遍历的是**所有边**（conditional + unconditional），但两者的"执行"性质完全不同：

| 边类型 | 是否遍历 | 触发条件 | 执行内容 | 性质 |
|--------|---------|---------|---------|------|
| conditional | ✅ 是 | gate 通过 + (源变 or 数据变) | 触发时机判定 + 过滤计算 + 结果传播 | **主动计算** |
| unconditional | ✅ 是 | 源变了 | 恒等过滤 + 股票搬运 | **被动传播** |

但从**概念模型**上说：
- 执行顺序的基本单位应该是**变换单元**，不是边
- unconditional 边只是变换单元的"输出部分"
- 综合设置里的一行，对应的是一个完整的变换单元

### 3.3 unconditional 边什么时候处理？

**编译期**：
- 识别变换单元（三元组分组）
- 确定边类型（conditional vs unconditional）
- 预计算 edge_ctx、edge_flow_spec 等

**运行时**：
- unconditional 边确实会被遍历到（在 out_edges 里）
- 但它的"执行"只是被动传播——源节点脏了就把股票搬过去
- 没有独立的触发逻辑，没有过滤计算

**代码引用**：
- `core/engine.py:1354-1440` — `_compile_pool` 编译期入口
- `core/engine.py:1192-1287` — `_prepare_topology` 拓扑预计算
- `core/engine.py:3578-3648` — `_run_tick_event_driven` 运行时主循环

---

## 4. 节点 + 边的真实关系图（文字版）

### 4.1 拓扑结构图

```
备选池/状态池 (type=200/202)
    │
    │  conditional 入边（有时间属性、有过滤条件）
    │  角色：决定什么时候算、用什么条件算
    ▼
转移条件/TDX条件 (type=201)  ← 枢纽节点，承载条件配置
    │
    │  unconditional 出边（无时间属性、无过滤条件）
    │  角色：把结果搬到目标池（只是搬运）
    ▼
目标状态池 (type=200)
```

### 4.2 变换单元视角

一个变换单元 = 一整段"从一个状态池到下一个状态池"的完整逻辑：

```
┌─────────────────────────────────────────────────┐
│               变换单元（1 个计算单元）            │
│                                                  │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐    │
│  │ 源状态池 │───▶│ 条件节点 │───▶│ 目标池  │    │
│  └─────────┘    └─────────┘    └─────────┘    │
│       ↑              ↑              ↑           │
│       │              │              │           │
│  conditional    枢纽节点       unconditional   │
│  入边（计算）   （承载条件）    出边（搬运）    │
└─────────────────────────────────────────────────┘
```

### 4.3 边类型判定规则

**边的类型由源节点类型决定**（不是目标节点）：

| 源节点类型 | 边类型 | 原因 |
|-----------|--------|------|
| 备选池/状态池/数据源 | conditional | 这些节点的出边需要触发时机和过滤条件 |
| 条件节点/公式节点 | unconditional | 这些节点的出边只是把结果传出去 |

**代码引用**：
- `config/edge_semantics.json:1` — description 明确说"边的类型由源节点类型决定"
- `core/engine.py:2209-2215` — `_resolve_edge_type` 函数
- `core/engine.py:361-365` — `_edge_type_lookup` 构建

---

## 5. 综合设置的每一行到底是什么（重新回答）

### 5.1 之前的错误理解

之前认为：**综合设置的每一行 = 一条边**

这是错误的。

### 5.2 正确理解

**综合设置的每一行 = 一个变换单元（三元组）**

具体来说：

| 行类型 | 对应什么 | 说明 |
|--------|---------|------|
| 备选池行（首行） | 源节点声明 | 整个流程的起点 |
| 状态池声明行 | 目标节点声明 | 每个池只声明一次 |
| 条件边行（带 └─▶ 箭头） | 一个完整的变换单元 | 左列的目标池是变换单元的输出，源池由缩进和层级隐含 |

**条件边行的完整对应关系**：
```
左列：└─▶ 目标状态池  →  unconditional 出边的目标
中列：条件描述 + 设置  →  条件节点（枢纽）的配置
右列：时序参数 + 设置  →  conditional 入边的时间属性
```

一行对应一整套"从源池经过条件计算到目标池"的完整逻辑。

### 5.3 为什么 unconditional 边不在综合设置里单独出现？

因为：
1. unconditional 边没有自己独立的配置（没有时间属性、没有过滤条件）
2. 它只是条件节点的"输出尾巴"，和条件节点是一体的
3. 用户不需要单独配置 unconditional 边——它的属性（attr 流转模式）从条件节点的配置里来

**代码引用**：
- `doc/综合设置表格.md:1-150` — 综合设置表格结构
- `config/edge_semantics.json:11-15` — unconditional 边只有 4 个基本属性

---

## 6. 对股票池本质的全新理解（一句话）

> **股票池是一组"状态池变换单元"的有向无环图，每个变换单元从上游状态池取股票，经过条件过滤后输出到下游状态池，unconditional 边只是变换单元内部的输出管道，不是独立的计算步骤。**

---

## 7. 之前理解错了的地方（至少 10 条）

### 错误 1：unconditional 边是执行单元

**之前理解**：unconditional 边和 conditional 边一样，都是独立的执行单元，都有自己的触发逻辑。

**代码真相**：unconditional 边没有独立的触发时机（starttype=0 即 always）、没有过滤计算（恒等过滤），它只是变换单元的输出管道。

**代码引用**：
- `config/edge_semantics.json:11-15` — unconditional 边定义
- `core/engine.py:2476-2526` — `_filter_unconditional` 函数

---

### 错误 2：综合设置的每一行对应一条边

**之前理解**：综合设置表格的每一行就是一条边，行号就是边的执行顺序。

**代码真相**：综合设置的每一行（条件边行）对应一个完整的变换单元（conditional 入边 + 条件节点 + unconditional 出边），不是一条边。

**代码引用**：
- `doc/综合设置表格.md:22-33` — 行类型定义
- `config/edge_semantics.json:16-23` — transformation_unit 定义

---

### 错误 3：unconditional 边有触发条件

**之前理解**：所有边都有触发条件，unconditional 边也不例外。

**代码真相**：unconditional 边的 `trigger_rule` 是 `"source_changed"`——它只是被动地等源节点变了就传过去，没有自己主动的触发逻辑。

**代码引用**：
- `config/edge_semantics.json:13` — unconditional 的 trigger_rule
- `config/edge_strategies.json:307-309` — src_change_detect 阶段仅无条件边需要

---

### 错误 4：边的类型由目标节点决定

**之前理解**：边连到什么类型的节点，就是什么类型的边。

**代码真相**：边的类型由**源节点**类型决定。源是备选池/状态池 → conditional 边；源是条件节点 → unconditional 边。

**代码引用**：
- `config/edge_semantics.json:1` — description 明确说明
- `core/engine.py:2209-2215` — `_resolve_edge_type` 函数

---

### 错误 5：运行时只有 conditional 边被执行

**之前理解**：既然 unconditional 不是执行单元，那运行时就不会执行它。

**代码真相**：运行时所有边都会被遍历到（在 `_run_tick_event_driven` 的 out_edges 循环里），unconditional 边也会被"执行"——但执行的只是股票搬运，没有计算。

**代码引用**：
- `core/engine.py:3608-3641` — `_run_tick_event_driven` 主循环遍历所有出边

---

### 错误 6：条件节点是"主动计算"的节点

**之前理解**：条件节点自己会计算，会触发。

**代码真相**：条件节点（type=201）本身不主动计算。真正的计算发生在它的入边（conditional 边）上。条件节点只是承载条件配置的枢纽。

**代码引用**：
- `config/edge_strategies.json:72-80` — transfer_condition → stock_state_pool 边的策略是 apply_filter
- `config/edge_semantics.json:18-20` — transformation_unit 的 hub_node_types

---

### 错误 7：unconditional 边有时间属性

**之前理解**：所有边都有 starttype、cxtype 等时间属性。

**代码真相**：unconditional 边 `has_time_attributes: false`，只有 from/to/attr/clr 四个基本属性，没有 interval/begin/end/count。

**代码引用**：
- `config/edge_semantics.json:14` — unconditional 的 has_time_attributes
- `config/edge_semantics.json:15` — unconditional 只有 4 个 attributes

---

### 错误 8：执行顺序的基本单位是边

**之前理解**：执行顺序是按边排序的，每条边是一个执行步骤。

**代码真相**：从概念模型上说，执行顺序的基本单位应该是变换单元。每条 unconditional 边只是其上游变换单元的"尾巴"，不是独立的执行步骤。

**代码引用**：
- `core/engine.py:2217-2247` — `_group_transformation_units` 函数
- `config/edge_strategies.json:197-226` — transformation_unit_strategies

---

### 错误 9：unconditional 边需要 gate 守门

**之前理解**：所有边都要经过 gate 检查。

**代码真相**：从 pipeline_phases 看，gate、replay_guard、tdx_guard、duration_guard 这些阶段都只针对 conditional 和 formula_eval 边，unconditional 边没有这些阶段。

**代码引用**：
- `config/edge_strategies.json:304-337` — pipeline_phases 定义
- `config/edge_strategies.json:310-318` — replay_guard/tdx_guard/duration_guard 都只针对 conditional/formula_eval

---

### 错误 10：unconditional 边的结果需要缓存

**之前理解**：为了性能，所有边的过滤结果都可以缓存。

**代码真相**：unconditional 边 `cache_filter_result: false`——因为它根本没有过滤计算，只是恒等传递，缓存没有意义。

**代码引用**：
- `config/edge_semantics.json:15` — unconditional 的 cache_filter_result

---

### 错误 11：节点和边是对等的两种元素

**之前理解**：股票池由节点和边组成，两者是对等的。

**代码真相**：从变换单元的视角看，"条件节点 + 它的入边和出边"是一个整体。条件节点不能独立存在，它必须有一条入边和一条出边，三者共同构成一个计算单元。拓扑校验也要求条件节点入边数=1、出边数=1。

**代码引用**：
- `core/engine.py:2269-2273` — 拓扑校验要求条件节点入边数和出边数各为 1
- `config/edge_semantics.json:16-23` — transformation_unit 定义

---

### 错误 12：unconditional 边的配置很丰富

**之前理解**：unconditional 边和 conditional 边一样，有丰富的配置项。

**代码真相**：unconditional 边只有 from/to/attr/clr 四个基本属性，连时间属性都没有。它的"配置"其实都在条件节点上。

**代码引用**：
- `config/edge_semantics.json:15` — unconditional 只有 4 个 attributes

---

**文档结束**（v3.0 — 认知颠覆版）
