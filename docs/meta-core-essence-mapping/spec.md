# Meta Core 深度表驱动重构 — 本质计算模型与实现路径

## 一、诊断：为什么当前代码是"一坨屎"

### 1.1 现状数据

| 指标 | 数值 | 设计目标 | 超标倍数 |
|------|------|---------|---------|
| engine.py | 3,369 行 | ~420 行 | **8x** |
| table_engine.py | 1,091 行 | ~200 行 | 5.5x |
| builtins.py | 1,462 行 | ~500 行 | 3x |
| 配置表数量 | 94 张 | ~15 张 | 6x |
| 死配置表(0引用) | ~40 张 | 0 | - |
| 边执行调用深度 | 7-8 层 | 2-3 层 | 3x |

### 1.2 根本病因：肤浅的表驱动

当前的"表驱动"是**反向表驱动**——不是简化，而是复杂化：

```
肤浅表驱动 = 把Python字典搬进JSON + 加一层查表分派
```

**病征一：间接层爆炸**

一条条件边的执行路径（7-8层）：
```
_execute_flowsCore
  -> _build_processing_plan
    -> _process_edge_pipeline
      -> _phase_dispatch
        -> _phase_nset_filter
          -> _dispatch_filter
            -> _eval_primitive
              -> _extract_prim_params_table
                -> _extract_single_param
```

每一层都在做同样的事：查表->调用->查表->调用。逻辑一点没少，只是被拆散了。

**病征二：配置表通货膨胀**

94 张配置表，约 40 张从未被引用（死表）。这些表是"为了表驱动而表驱动"造出来的，没有实际用途。

**病征三：概念碎片化**

同一个东西有多个名字：
- 条件边/filter/nset/conditional/formula_eval -> 都是"筛选"
- gate/starttype/timing/duration/cxtype -> 都是"时机"
- propagate/flow/transfer/move/copy -> 都是"流转"
- callback/action/psatt/side_effect -> 都是"副作用" string="true">念不是被澄清了，而是被乘了。

---

## 二、本质：股票池是什么？

### 2.1 一句话定义

> **股票池 = 有向图上的流计算。**

节点存股票，边定义股票怎么从一个节点流到另一个节点。

### 2.2 核心数据模型（3个实体）

#### 实体一：节点（Node）

```
Node:
  id: str
  type: str              # candidate/condition/state/terminal/decorator
  label: str
  position: {x, y}
  params: dict           # 类型特定参数
  stocks: Set[StockEntry]  # 运行时状态
```

所有节点结构相同，差异只在 type 和 params。

#### 实体二：边（Edge）

```
Edge:
  id: str
  from: str              # 源节点id
  to: str                # 目标节点id
  params: EdgeParams     # 所有属性都在这里
```

**EdgeParams 的5个维度：**

| 维度 | 含义 | 当前对应物 |
|------|------|-----------|
| timing | 什么时候触发 | gate + starttype + cxtype + duration |
| filter | 哪些股票通过 | nset + noperate + formula + condition |
| flow | 怎么流过去 | propagate + attr + tran + move/overwrite |
| actions | 流完做什么 | callback + psatt + bsound/btip/bsavehis |
| ttl | 在目标池呆多久 | bdel + ndelnum + ndeltype + hold |

**所有属性都是边的参数，不是5个独立阶段。**

#### 实体三：引擎（Engine）

```python
def tick(engine, now):
    