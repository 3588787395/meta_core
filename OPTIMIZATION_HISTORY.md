# 股票池平台核心逻辑优化历史（I1–I100）

本文件以简洁语言记录 100 轮迭代的演进脉络，覆盖表驱动、事件驱动、数据驱动、时间驱动四条主线，以及类属性/方法/事件的持续精炼。

## 一、三阶段演进

### 阶段一（I1–I30）：表驱动基础设施搭建
确立"配置即真相"的运行期分派骨架，消除散布的条件分支与轮询。

- **I1**：`nset cross` + `prev_lookup` 表驱动三分，确立节点查找单一入口
- **I5**（98）：删除轮询循环，引入 `EventDriver` 边触发 + TTL 折叠——时间驱动从轮询转向中断
- **I10**（100）：`_safe_timestamp` 三副本统一为单一真相源
- **I15**：`apply_ttl` 发出 EXIT 事件，表驱动覆盖达上限场景
- **I20**（100）：`_ACTION_HANDLERS` 表驱动收敛，散落动作分派归一
- **I25**：`bar_hash()` 收敛 4 处重复访问
- **I30**：清理 `highlight_events` 死属性，首次系统性识别零消费字段

### 阶段二（I30–I70）：单一真相源深化
从"有表驱动"走向"每份语义只有一个权威定义"，消除双实现与硬编码副本。

- **I35**：消除 `_lookup_edge_cond` 双实现，边条件查找归一
- **I40**（100）：`run_loop` 回归测试 + 1e8 阈值收敛为 `is_offset_of_day`/`anchor_to_today`
- **I45**：`replay` elif 链 → dict 表驱动
- **I50**：三态审计，删零方差列，清理文档冗余
- **I55**：SELL 信号统一到 EventBus，修 UI 显示 bug
- **I60**（95）：消除 `_exit_tracker_cache` 死状态
- **I65**：删 `ttl_remaining` 死计算
- **I70**：TTL 事件命名 `EXIT` → `TIMEOUT` 统一

### 阶段三（I70–I100）：系统性死代码/死配置清理 + 生产 bug 修复
建立"死代码谱系"识别能力，从单点清理升级为家族式清理，并修复被死配置掩盖的生产 bug。

- **I75**：`per-occurrence pairs` 修键碰撞 bug
- **I76**（96/93）：`attr_bits` 单一真相源（`field_definitions.bit_fields.flow`），修前端 4/5 位掩码错误
- **I78**（98）：守卫参数化 + 修 SELL `price=0` bug
- **I80**（98）：Signal `asdict` 单一真相源，消除 API/WS 双路径字段漂移
- **I82**（98）：event dict 键名收敛 `event_type`
- **I85**（96）：`new_events` 加 `pool_id` fallback
- **I88**（98）：replay 暂停 → 中断驱动 + 删死代码
- **I90**（96）：`wall_clock` 双路径 → 单一执行路径
- **I93**（98）：识别 `capability_registry` 名义表（零消费），死配置谱系起点
- **I95**（97）：schemas fail-fast 统一
- **I97**（98）：三重 write-only 死属性清理
- **I98**（98）：`LRUCache` 死类迁移到 `tests/_test_cache.py` + 删级联死导入（`OrderedDict`/`time`）——二阶死代码模式
- **I99**（98）：删 `_compiled_timing` write-only 死缓存 + `_se` 死沙箱字典；记录 `starttype_rules` 7 死字段与表驱动设计退却
- **I100**（98）：修复 `simulator.py:128` 配置路径生产 bug（`_tm` → `_sc2`，被 fallback 值巧合掩盖）+ 删 2 死表（`param_extract_ops`/`fast_path_ops` 幽灵方法引用）+ 清理 17 死字段

## 二、四条主线收敛成果

### 表驱动（数据驱动）
- ConfigStore glob 加载 + 按文件名 stem 索引，30 张目标配置表收敛
- 运行期分派由 Python dict 常量驱动：`_STARTTYPE_GATE_HANDLERS` / `_ACTION_HANDLERS` / `_FILTER_EVALUATORS` / `_EDGE_TYPE_HANDLERS`
- JSON 配置从 aspirational（表达式/方法分派字段）演化为 realized（仅保留 `name` 标签 + Python 函数实现）

### 事件驱动
- EventBus 4 事件类型（`DataChanged`/`Executed`/`DomainEvent`/`Signal`）全部有订阅者
- Signal dataclass 为字段集唯一真相源，`asdict` 派生消除硬编码 8 字段列表
- API 与 WS 两路径共享同一 `asdict` 派生，字段集不可能漂移

### 时间驱动
- `time_at()` 三模式（实盘/回放/仿真）统一入口
- `_STARTTYPE_GATE_HANDLERS` 表驱动 gate 分派，4 个 handler 全部用 `_offset_seconds`
- 时间驱动从中断方法（直接/间接）实现，非轮询

### 类属性/方法/事件
- `PoolEngine` 仅保留构造函数与真相属性；辅助方法剥离到 `PoolEngineMixin`
- `MetaEngine` 作为兼容门面委托 `PoolEngine`
- 死类、死方法、死属性、死字段、死表、死缓存六层系统性清理

## 三、死代码谱系（I93 → I100）

| 迭代 | 类型 | 对象 | 规模 |
|------|------|------|------|
| I93 | 死表 | `capability_registry` 名义表 | 1 表 |
| I97 | write-only 死属性 | 三重死属性 | 3 属性 |
| I98 | 死类 + 级联死导入 | `LRUCache` + `OrderedDict`/`time` | 1 类 + 2 导入 |
| I99 | write-only 死缓存 + 死沙箱 | `_compiled_timing` + `_se` | 12 行 |
| I100 | 死表 + 死字段 + 生产 bug | `param_extract_ops`/`fast_path_ops` + 17 字段 + `_tm→_sc2` | 2 表 + 17 字段 + 1 bug |

## 四、生产 bug 修复记录

- **I55**：SELL 信号 UI 显示 bug
- **I75**：`per-occurrence pairs` 键碰撞
- **I76**：前端 `attr_bits` 4/5 位掩码错误
- **I78**：SELL `price=0`
- **I100**：`simulator.py:128` 配置路径 `_tm.get("default_hold_seconds")` → `_sc2.get(...)`，被 fallback 值 432000 与配置值 432000 巧合一致掩盖

## 五、评分曲线（关键节点）

```
I1(98) I5(98) I10(100) I20(100) I40(100) I60(95) I76(96/93) I78(98)
I80(99/98) I82(98) I83(98) I84(98) I86(98) I87(98) I88(98)
I91(98) I93(98) I97(98) I98(98) I99(98) I100(98)
```

后期（I77–I100）24 轮中 17 轮达 98 分阈值，7 轮 95–97 分（均为识别新死代码谱系或新 bug 的探索轮）。

## 六、最终基线

- **测试**：1493 passed + 2 xpassed（全量通过）
- **配置**：`timing.json` 精简为 `starttype_rules`（仅 `name`）+ `cxtype_rules`（仅 `name`）+ 活跃子表
- **死代码**：六层清理完成（死类/死方法/死属性/死字段/死表/死缓存）
- **真相源**：Signal `asdict`、`attr_bits` 单一真相源、`time_at` 单一入口、ConfigStore 单一加载点

---

*文档生成于 I100 完成后，覆盖 I1–I100 全部迭代。*
