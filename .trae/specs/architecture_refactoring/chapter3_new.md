
---

## 3. PoolVM：股票池领域专用虚拟机（DSVM）

> **评审批评**："'通用VM'名不副实——opcode通用了，但内存模型还是领域绑定的。增量计算和EdgeVM完全脱节，SET_FILTER增量算法是外挂的，不是VM原生支持的。"

### 3.1 定位决策：领域专用VM（DSVM），而非通用VM

#### 3.1.1 为什么放弃"通用VM"路线

v0.4尝试做"通用VM"，但实际上：
- 内存模型直接与EAV三元组硬绑定（`e:<entity>:<attr>`直接映射到`RuntimeState.get`）
- 12个通用opcode看似通用，但VM知道"实体-属性-值"这个特定存储模型
- 库函数直接访问整个state，没有权限隔离

**结论**：硬叫"通用VM"只会带来混淆。与其做一个"半吊子通用VM"，不如坦诚地做一个**领域专用VM（Domain-Specific VM, DSVM）**，在股票池领域内做到极致。

#### 3.1.2 PoolVM 的明确定位

| 维度 | PoolVM 定位 |
|------|------------|
| **名称** | **PoolVM**（股票池领域专用虚拟机） |
| **领域** | 集合操作 + 变更集传播 + 股票池流转 |
| **目标** | 在股票池领域内，性能最优、表达力最强、增量计算最自然 |
| **不追求** | 通用性（不指望用它跑排序算法或Web服务器） |
| **类比** | 像 SQL 引擎（专门做查询）、像正则表达式引擎（专门做匹配）——不是通用CPU，但在自己的领域比通用CPU高效得多 |

#### 3.1.3 领域专用 vs 通用的权衡

| 维度 | 通用VM（v0.4方案） | 领域专用VM（v0.5方案） |
|------|------------------|---------------------|
| **概念纯粹性** | 高（理论上可以跑任何程序） | 中（只针对股票池领域） |
| **领域性能** | 中（需要通过库函数间接操作） | **高（原生支持集合/变更集）** |
| **增量友好度** | 低（通用指令不知道什么是变更集） | **极高（变更集是一等公民）** |
| **实现复杂度** | 高（通用内存模型、沙箱、权限...） | **中（聚焦领域，不需要那么多通用机制）** |
| **可移植性** | 高（理论上） | 低（只能用于股票池领域） |
| **学习曲线** | 陡（需要理解通用VM模型） | **缓（概念都是股票池领域的）** |
| **新增领域操作** | 加库函数（不改VM） | 加opcode（改VM，但数量可控） |

**决策**：选择**领域专用VM（PoolVM）**。理由：
1. 我们的核心场景就是股票池，不需要通用VM
2. 领域专用VM可以原生支持集合和变更集，增量计算是天然的
3. 实现复杂度更低，性能更好
4. 概念更贴近领域，团队更容易理解

> **ADR-010**：新增架构决策记录——选择领域专用VM而非通用VM，详见第18章。

---

### 3.2 PoolVM 的核心设计思想：双类型栈 + 指令级增量

PoolVM 是一个**栈式虚拟机**，但操作数栈上的值只有两种类型：
1. **集合（Set）**：股票代码的集合（附带属性）
2. **变更集（ChangeSet）**：集合之间的差异（added/removed/modified）

**关键洞见**：既然整个系统的核心就是"集合变换"和"变更传播"，那VM的操作数就应该直接是这两种东西。不需要通用的int/float/string，也不需要通用的内存模型。

```
操作数栈上只有两种东西：
  ┌──────────┐
  │  Set     │  ← 股票集合（全量状态）
  ├──────────┤
  │  ChangeSet │  ← 变更集（增量状态）
  └──────────┘

指令的输入/输出也只有这两种类型
  Set → Set          （全量变换）
  ChangeSet → ChangeSet  （增量变换）
  Set + ChangeSet → Set  （应用变更）
```

#### 3.2.1 双模式执行：全量模式 + 增量模式

PoolVM 的每条指令都有**两种执行模式**：

| 模式 | 输入类型 | 输出类型 | 触发条件 |
|------|---------|---------|---------|
| **全量模式** | Set | Set | 第一次执行 / 变更集太大降级 / 全量重算 |
| **增量模式** | ChangeSet | ChangeSet | 输入是变更集，且指令支持增量 |

**传播机制**：
- 一条边的输入如果是变更集，整条边的指令序列尽量走增量模式
- 如果某条指令不支持增量，自动降级：先应用变更集得到全量集合，再走全量模式
- 输出如果是变更集，直接传递给下一条指令/下一条边

这就是**增量计算的VM原生支持**——变更集在指令之间流动，不需要外挂。

---

### 3.3 PoolVM 指令集（ISA v1.0）

PoolVM 有 **16个 opcode**，全部针对股票池领域优化。分为5类：

#### 3.3.1 指令分类总览

| 类别 | 数量 | 指令 | 说明 |
|------|------|------|------|
| **栈操作** | 4 | PUSH, POP, DUP, SWAP | 基本栈操作 |
| **集合操作** | 5 | SET_LOAD, SET_STORE, SET_FILTER, SET_UNION, SET_DIFF | 集合全量操作 |
| **变更集操作** | 4 | CS_APPLY, CS_UNION, CS_COMPOSE, CS_EMPTY? | 变更集操作 |
| **控制流** | 2 | JZ, JMP | 条件/无条件跳转 |
| **其他** | 1 | NOP | 空操作 |

> **为什么只有16个？**：因为是领域专用VM，不需要通用VM的算术、逻辑、比较等指令。所有复杂逻辑都通过FILTER的谓词表达式表达。

#### 3.3.2 完整指令列表

| Opcode | 操作数 | 栈变化（全量模式） | 栈变化（增量模式） | 功能描述 |
|--------|--------|-----------------|-----------------|---------|
| **PUSH** | literal | `→ Set` | `→ ChangeSet` | 压入字面量集合或空变更集 |
| **POP** | — | `value →` | `value →` | 弹出栈顶 |
| **DUP** | — | `a → a a` | `a → a a` | 复制栈顶 |
| **SWAP** | — | `a b → b a` | `a b → b a` | 交换栈顶两个元素 |
| **SET_LOAD** | addr | `→ Set` | — | 从状态加载集合（全量模式用） |
| **SET_STORE** | addr | `Set →` | `ChangeSet →` | 存储集合/变更集到状态 |
| **SET_FILTER** | pred_id | `Set → Set` | `ChangeSet → ChangeSet` | 按谓词过滤（支持增量） |
| **SET_UNION** | — | `a b → a∪b` | `csa csb → csa∪csb` | 集合并集（支持增量） |
| **SET_DIFF** | — | `a b → a-b` | `csa csb → csa-csb` | 集合差集（支持增量） |
| **CS_APPLY** | — | `Set CS → Set` | — | 把变更集应用到集合 |
| **CS_UNION** | — | — | `cs1 cs2 → cs1∪cs2` | 变更集合并（仅用于同层级并行变更） |
| **CS_COMPOSE** | — | — | `cs1 cs2 → cs1∘cs2` | 变更集顺序合成（先cs1后cs2） |
| **CS_EMPTY?** | — | `CS → bool` | `CS → bool` | 变更集是否为空 |
| **JMP** | label | `→` | `→` | 无条件跳转 |
| **JZ** | label | `cond →` | `cond →` | 为零（假）则跳转 |
| **NOP** | — | `→` | `→` | 空操作 |

#### 3.3.3 增量模式的自动切换机制

**规则**：
1. 指令执行前，检查栈顶元素的类型
2. 如果是 ChangeSet 且指令支持增量 → 走增量模式
3. 如果是 Set 或指令不支持增量 → 走全量模式
4. 如果混合类型（一个Set一个ChangeSet）→ 先应用变更集，再走全量

**示例（FILTER指令）**：
```
全量模式：
  栈顶是 Set → 计算所有元素的谓词 → 返回新的 Set

增量模式：
  栈顶是 ChangeSet → 只计算变化元素的谓词 → 返回新的 ChangeSet
  - added 中的元素：检查是否满足谓词 → 满足则加入输出的 added
  - removed 中的元素：如果之前在集合里 → 加入输出的 removed
  - modified 中的元素：重新评估谓词 → 状态变化则加入 added 或 removed
```

---

### 3.4 内存模型：双存储区域（State + 栈）

PoolVM 的"内存"分为两部分：

| 存储区域 | 存取方式 | 存储内容 | 对应状态 |
|---------|---------|---------|---------|
| **操作数栈** | PUSH/POP | 临时的 Set / ChangeSet | VM 内部 |
| **全局状态区** | SET_LOAD / SET_STORE | 持久化的节点股票集、边状态等 | RuntimeState |

**地址格式（全局状态区）**：

| 地址格式 | 含义 | 示例 |
|---------|------|------|
| `node:<node_id>:stocks` | 节点的股票集 | `node:src_1:stocks` |
| `edge:<edge_id>:<field>` | 边的状态字段 | `edge:e1:last_fire_ts` |
| `global:<key>` | 全局变量 | `global:current_ts` |
| `cs:<id>` | 命名变更集 | `cs:edge:e1:input` |

> **为什么没有通用的"堆"？**：因为是领域专用VM，所有需要持久化的数据都有明确的领域含义（节点股票集、边状态、全局变量），不需要通用堆。

---

### 3.5 完整边执行示例（增量模式）

让我们用一个**条件过滤边**的完整执行流程，展示增量模式下变更集如何在指令间流动：

**场景**：源节点 src_1 有变更集（新增了股票A，删除了股票B，修改了股票C的属性），过滤边需要把这些变化传递下去。

```
# 初始状态
# 栈: []
# 输入变更集 cs_in = (added={A}, removed={B}, modified={C:close}, v=42)

# === 步骤1：加载源节点的变更集 ===
# （传播器已经把输入变更集放在栈上了）
# 栈: [cs_in]

# === 步骤2：过滤（增量模式）===
# 因为栈顶是 ChangeSet，SET_FILTER 自动走增量模式
SET_FILTER  "price_gt_10"    # 谓词：价格 > 10

# 增量过滤的计算：
#   A（新增）：价格12 > 10 → 加入 added
#   B（删除）：之前在集合里 → 加入 removed
#   C（修改）：之前价格9 ≤10（不在集合里），现在价格11 >10 → 加入 added
# 输出 cs_out = (added={A, C}, removed={B}, v=42)

# 栈: [cs_out]

# === 步骤3：检查变更集是否为空 ===
CS_EMPTY?
# 栈: [cs_out, false]

JZ  HAS_CHANGES   # 非空，继续
JMP END
HAS_CHANGES:

# === 步骤4：存储到目标节点（增量模式）===
# SET_STORE 接收 ChangeSet，自动应用到目标节点
SET_STORE  "node:dst_1:stocks"

# 效果：把 cs_out 应用到 dst_1 的股票集
# 等同于：dst_stocks = apply(dst_stocks, cs_out)

# 栈: [cs_out]

# === 步骤5：输出变更集（传递给下游边）===
# 栈顶的 cs_out 就是这条边的输出变更集
# 传播器会把它传递给下游边的输入

END:
NOP
```

**关键观察**：
1. 整个过程中，**变更集一直在指令之间流动**，没有跳出VM
2. 每条指令自动判断走全量还是增量模式，不需要显式标记
3. 增量计算是VM原生的，不是外挂的
4. 如果某一步不支持增量（比如未来加了个复杂操作），自动降级

---

### 3.6 执行器实现（核心代码结构）

```python
class PoolVM:
    """股票池领域专用虚拟机（DSVM）。
    
    设计原则：
    - 操作数只有两种类型：Set 和 ChangeSet
    - 每条指令都支持全量和增量两种模式
    - 增量模式是原生的，不是外挂的
    - 只做股票池领域的事，不追求通用
    """
    
    def __init__(self, state: RuntimeState):
        self.state = state
        self.stack = []           # 操作数栈：元素是 Set 或 ChangeSet
        self.predicates = {}      # 谓词函数表：pred_id → function(code, attrs)
        self.labels = {}
    
    def execute_program(self, program: PoolProgram):
        pc = 0
        instructions = program.instructions
        self.labels = program.labels
        self.stack = []
        
        while pc < len(instructions):
            instr = instructions[pc]
            opcode = instr.opcode
            operands = instr.operands
            
            # 根据栈顶类型自动判断模式
            top_is_cs = self.stack and isinstance(self.stack[-1], ChangeSet)
            
            if opcode == 'PUSH':
                self.stack.append(operands[0])
                
            elif opcode == 'POP':
                self.stack.pop()
                
            elif opcode == 'DUP':
                self.stack.append(self.stack[-1])
                
            elif opcode == 'SWAP':
                a, b = self.stack.pop(), self.stack.pop()
                self.stack.append(a)
                self.stack.append(b)
                
            elif opcode == 'SET_LOAD':
                addr = operands[0]
                value = self.state.load_set(addr)
                self.stack.append(value)
                
            elif opcode == 'SET_STORE':
                addr = operands[0]
                value = self.stack.pop()
                if isinstance(value, ChangeSet):
                    # 增量模式：应用变更集
                    self.state.apply_change_set(addr, value)
                else:
                    # 全量模式：直接存储
                    self.state.store_set(addr, value)
                    
            elif opcode == 'SET_FILTER':
                pred_id = operands[0]
                pred = self.predicates[pred_id]
                value = self.stack.pop()
                
                if isinstance(value, ChangeSet):
                    # === 增量模式 ===
                    cs_in = value
                    cs_out = self._incremental_filter(cs_in, pred)
                    self.stack.append(cs_out)
                else:
                    # === 全量模式 ===
                    s = value
                    result = self._full_filter(s, pred)
                    self.stack.append(result)
                    
            elif opcode == 'SET_UNION':
                b = self.stack.pop()
                a = self.stack.pop()
                if isinstance(a, ChangeSet) and isinstance(b, ChangeSet):
                    # 增量模式：变更集合并（仅用于同层级并行）
                    self.stack.append(a.parallel_union(b))
                else:
                    # 全量模式
                    a_set = a if isinstance(a, set) else set(a)
                    b_set = b if isinstance(b, set) else set(b)
                    self.stack.append(a_set | b_set)
                    
            elif opcode == 'SET_DIFF':
                b = self.stack.pop()
                a = self.stack.pop()
                if isinstance(a, ChangeSet) and isinstance(b, ChangeSet):
                    self.stack.append(a.diff(b))
                else:
                    a_set = a if isinstance(a, set) else set(a)
                    b_set = b if isinstance(b, set) else set(b)
                    self.stack.append(a_set - b_set)
                    
            elif opcode == 'CS_APPLY':
                cs = self.stack.pop()
                s = self.stack.pop()
                result = apply_change_set(s, cs)
                self.stack.append(result)
                
            elif opcode == 'CS_EMPTY?':
                cs = self.stack[-1]  # 不弹出，只检查
                self.stack.append(cs.is_empty())
                
            elif opcode == 'JMP':
                pc = self.labels[operands[0]]
                continue
                
            elif opcode == 'JZ':
                cond = self.stack.pop()
                if not cond:
                    pc = self.labels[operands[0]]
                    continue
                    
            elif opcode == 'NOP':
                pass
            
            pc += 1
```

---

### 3.7 表驱动深度验证（PoolVM 版）

| 验证维度 | v0.4 通用VM（名不副实） | v0.5 PoolVM（领域专用） |
|---------|------------------------|----------------------|
| VM知道"股票池"吗？ | 声称不知道，但知道EAV | **知道（设计目标就是股票池）** |
| 增量计算是原生的吗？ | 不是（外挂库函数） | **是（变更集是一等公民，指令原生支持）** |
| 新增领域操作需要改VM吗？ | 不需要（加库函数） | 需要（加opcode） |
| 领域操作的数量 | 受库函数数量限制 | **opcode数量可控（~16个核心操作）** |
| 性能（领域内） | 中（库函数调用开销） | **高（原生指令，直接操作）** |
| 概念数量（认知负荷） | 多（通用VM+领域概念） | **少（都是领域概念）** |
| 能不能用于其他领域？ | 理论上能 | 不能 |
| **定位是否清晰？** | 含糊（硬撑通用） | **清晰（领域专用，做到极致）** |

---

