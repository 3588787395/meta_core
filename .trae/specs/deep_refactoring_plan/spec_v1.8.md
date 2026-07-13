# 股票池深度重构规划 v1.8

> 版本主题：状态表补全 + 公式引擎完整契约 + 脏来源拆分
> 设计原则：表驱动、数据驱动、事件驱动、增量优先、全量保底、概念最简
> 目标：补齐三层状态表（指标/比较/过滤），明确公式引擎完整接口契约（含 DataProvider + dirty_codes），拆分两种脏来源（stock_change vs data_change）

---

## v1.7 → v1.8 变更摘要表

**变更日期：** 2026-07-01

| # | 变更项 | v1.7 | v1.8 | 本质变化 |
|---|--------|------|------|---------|
| 1 | **三层状态表补齐（核心升级）** | 只提"指标层可缓存"，比较层、集合运算层中间结果无显式状态表 | **三张显式状态表**：`indicator_results` / `edge_compare_results` / `edge_filter_results` | 从"隐式缓存"到"显式状态表"——每层结果都有明确生命周期和失效条件 |
| 2 | **公式引擎接口契约（完整）** | 3个接口（eval_indicator / eval_batch / get_cache_stats），数据来源模糊 | **完整接口**：增加 `set_data_provider` / `on_data_updated(dirty_codes)` / `eval_indicators`，明确 DataProvider 接口 | 职责边界更清晰——公式引擎自己管数据，股票池引擎只管调用和通知 |
| 3 | **脏来源拆分（核心升级）** | `dirty_nodes: Set[nid]` 混成一团（股票变了还是数据变了分不清） | **拆成两张表**：`node_stock_change[nid]: bool` + `node_data_dirty[nid]: Set[code]` | 从"一种脏"到"两种脏"——stock_change 全量重算，data_change 只重算脏股票，处理逻辑完全不同 |
| 4 | **增量计算完整链条** | 只有概念，链条不完整 | **完整链条**：dirty_stocks → 指标重算 → 比较重算 → 集合运算 → propagate | 每一步读什么表、写什么表都明确，可追踪可验证 |
| 5 | **事件驱动完整时序图** | 有简单时序图，但不够细 | **完整时序图**：从数据更新到事件发出，每一步的读写操作都标注 | 可作为实现和调试的参考蓝图 |
| 6 | **核心运行时表数** | 5 张 | **8 张**（+3 张状态表，dirty_nodes 拆为 2 张） | 表数增加，但概念更清晰，每层状态显式化 |

**一句话总结 v1.8 升级：** 补齐三层状态表（指标结果/比较结果/过滤结果），每张表有明确的生命周期和失效条件；公式引擎接口契约完整化，明确 DataProvider 接口 + dirty_codes 通知机制；拆分两种脏来源（stock_change vs data_change），处理逻辑分开；增量计算链条完整闭环，每一步读写什么表都清清楚楚。

---

## 一、核心升级一：三层状态表补齐

### 1.1 为什么需要显式状态表？

**v1.7 的问题：只有"指标层可缓存"的概念，比较层、集合运算层的中间结果没有显式状态表。**

```
v1.7 的隐式状态（问题）：
  指标层：公式引擎内部有缓存（隐式）
  比较层：没有显式状态，每次都要重算（或隐式依赖 filter_cache）
  集合运算层：没有显式状态，每次都要重新组合
  
问题：
  1. 不清晰：每层的结果在哪、什么时候失效，全靠猜
  2. 难优化：想做增量优化，不知道哪些结果可以复用
  3. 难调试：出问题时，不知道是哪一层算错了
```

**v1.8 的显式状态表（正确做法）：**

```
三层显式状态表：
  第一层（指标层）：indicator_results[ind_id][code] = value
  第二层（比较层）：edge_compare_results[eid][code] = True/False
  第三层（集合运算层）：edge_filter_results[eid] = Set[code]

每一层：
  - 有明确的数据结构
  - 有明确的生命周期（什么时候创建、什么时候更新、什么时候失效）
  - 有明确的读写时机
  - 增量计算可以一层一层往下传
```

### 1.2 三层状态表详解

#### 第一层：indicator_results（指标计算结果）

| 属性 | 说明 |
|------|------|
| **结构** | `Dict[ind_id → Dict[code → value]]` |
| **归属** | 公式引擎内部维护（股票池引擎不直接访问） |
| **内容** | 每个指标在每只股票上的计算值（float / bool / None） |
| **生命周期** | 常驻内存，随数据更新而更新 |
| **更新时机** | 公式引擎 `on_data_updated(dirty_codes)` 被调用后，下次 `eval_indicator` 时重算 dirty_codes 的值 |
| **失效条件** | 1. 该股票数据更新了（dirty_codes 里有它）<br/>2. 公式定义变了（编译期事件）<br/>3. 参数变了（编译期事件） |
| **增量方式** | 只重算 dirty_codes 里的股票，其他读缓存 |

```python
# 结构示意（公式引擎内部）
indicator_results = {
    "MA5": {
        "000001": 10.5,
        "000002": 20.3,
        ...
    },
    "MACD": {
        "000001": 0.02,
        "000002": -0.01,
        ...
    },
    ...
}
```

#### 第二层：edge_compare_results（单股比较结果）

| 属性 | 说明 |
|------|------|
| **结构** | `Dict[eid → Dict[code → bool]]` |
| **归属** | 股票池引擎维护（比较层） |
| **内容** | 每条边的 filter 在每只股票上的比较结果（True=通过，False=不通过） |
| **生命周期** | 常驻内存，随指标结果更新而更新 |
| **更新时机** | 1. 指标结果更新了 → 重算对应股票的比较结果<br/>2. 节点股票列表变了 → 新入池股票要算，出池股票要删 |
| **失效条件** | 1. 该股票的指标值变了（indicator_results 更新了）<br/>2. 比较算子/参数变了（编译期事件）<br/>3. 股票从节点移除了 |
| **增量方式** | 只重算数据变了的股票（data_dirty），其他沿用上次结果 |

```python
# 结构示意
edge_compare_results = {
    "edge_001": {
        "000001": True,   # 这只股票通过了比较
        "000002": False,  # 这只股票没通过
        ...
    },
    "edge_002": {
        ...
    },
    ...
}
```

**注意：edge_compare_results 只针对"独立比较型" filter。排名型/截面型 filter 没有单股比较结果，直接跳到第三层。**

#### 第三层：edge_filter_results（最终通过的股票集合）

| 属性 | 说明 |
|------|------|
| **结构** | `Dict[eid → Set[code]]` |
| **归属** | 股票池引擎维护（集合运算层） |
| **内容** | 每条边的 filter 最终通过的股票代码集合 |
| **生命周期** | 常驻内存，随比较结果更新而更新 |
| **更新时机** | 1. 比较结果更新了 → 更新集合（独立型）<br/>2. 指标结果更新了 → 全量重排（排名型）<br/>3. 节点股票列表变了 → 更新集合 |
| **失效条件** | 1. 比较结果变了（edge_compare_results 更新了）<br/>2. 排名型：任何一只股票的指标值变了（排名是相对的）<br/>3. 节点股票列表变了 |
| **增量方式** | 独立型：增量更新集合；排名型：全量重排 |

```python
# 结构示意
edge_filter_results = {
    "edge_001": {"000001", "000003", "000005", ...},  # 通过的股票集合
    "edge_002": {"000002", "000004", ...},
    ...
}
```

### 1.3 三层状态表的关系

```
数据流（从上往下）：

  indicator_results[ind_id][code] = value
         │
         │  指标值变了 → 比较结果要重算
         ▼
  edge_compare_results[eid][code] = bool
         │
         │  比较结果变了 → 过滤集合要更新
         ▼
  edge_filter_results[eid] = Set[code]
         │
         │  过滤集合变了 → propagate 到目标节点
         ▼
  node_stocks[tid] = List[code]
```

### 1.4 增量计算的完整链条

**从数据更新到 propagate 的完整增量链条：**

```
1. 数据更新
   dirty_stocks.add(code)
   ↓
2. 公式引擎收到通知
   formula_engine.on_data_updated(dirty_codes=dirty_stocks)
   → 公式引擎内部：indicator_results 中 dirty_codes 的值标记为失效
   ↓
3. 指标计算（需要时）
   formula_engine.eval_indicators(formula_ids, codes, period)
   → 只重算 dirty_codes 的指标，其他读缓存
   → 更新 indicator_results
   ↓
4. 比较层（独立型）
   对 edge_compare_results[eid]：
   → 只重算 data_dirty_codes ∩ source_codes 的比较结果
   → 其他股票的比较结果不变
   → 更新 edge_compare_results[eid]
   ↓
5. 集合运算层
   独立型（逻辑组合）：
   → 根据 edge_compare_results 的变化，增量更新 edge_filter_results[eid]
   排名型（全局依赖）：
   → 基于全量 indicator_results，全量重排
   → 更新 edge_filter_results[eid]
   ↓
6. propagate
   对比 edge_filter_results[eid] 和 node_stocks[tid]
   → 计算 entered_codes / exited_codes
   → 更新 node_stocks[tid]
   → 发事件
```

**每一步的读写表：**

| 步骤 | 读什么表 | 写什么表 | 说明 |
|------|---------|---------|------|
| 1. 数据更新 | — | `latest_tick[code]` + `dirty_stocks.add(code)` | 行情推送 |
| 2. 公式引擎通知 | `dirty_stocks` | 公式引擎内部标记失效 | `on_data_updated(dirty_codes)` |
| 3. 指标计算 | `indicator_results`（读缓存）+ 数据源 | `indicator_results`（更新脏股票） | 公式引擎内部 |
| 4. 比较层 | `indicator_results` + `edge_compare_results`（旧值） | `edge_compare_results[eid]`（更新脏股票） | 只重算 data_dirty 的 |
| 5. 集合运算层 | `edge_compare_results` 或 `indicator_results` | `edge_filter_results[eid]` | 独立型增量，排名型全量 |
| 6. propagate | `edge_filter_results[eid]` + `node_stocks[tid]` | `node_stocks[tid]` | 计算差集，发事件 |

---

## 二、核心升级二：公式引擎完整接口契约

### 2.1 数据怎么来？——明确 DataProvider 接口

**v1.7 遗留问题：公式引擎的数据从哪来？是股票池引擎喂数据，还是公式引擎自己拉？**

**答案：公式引擎有自己的数据层接口（DataProvider），股票池引擎不直接给数据。**

```
v1.7 的模糊边界：
  股票池引擎 → 调用 eval_indicator_batch(codes, formula, ...)
  公式引擎 → 数据从哪来？不知道，可能是传入的 data_fetcher，可能是 data_query
  
问题：
  1. 耦合：公式引擎依赖外部传入的数据获取方式
  2. 不清晰：谁负责数据缓存？谁负责数据更新通知？
```

```
v1.8 的清晰边界：
  ┌─────────────────┐        set_data_provider        ┌─────────────────┐
  │  股票池引擎      │ ──────────────────────────────→ │   公式引擎       │
  │  (Pool Engine)   │                                 │  (FormulaEngine) │
  │                  │        eval_indicator(s)        │                  │
  │                  │ ←────────────────────────────── │                  │
  │                  │                                 │                  │
  │                  │        on_data_updated          │                  │
  │                  │ ──────────────────────────────→ │                  │
  └─────────────────┘                                 └────────┬─────────┘
                                                                │
                                                                │ 内部调用
                                                                ▼
                                                  ┌─────────────────────────┐
                                                  │    DataProvider（接口） │
                                                  │  - fetch_bars(code, period) │
                                                  │  - get_snapshot(code)   │
                                                  └─────────────────────────┘
```

**设计原则：**
- 股票池引擎**不直接给**公式引擎喂数据
- 公式引擎**自己通过 DataProvider 接口拉取**数据
- 股票池引擎只负责：1）设置 DataProvider；2）通知数据更新；3）调用求值接口

### 2.2 DataProvider 接口定义

**公式引擎依赖的数据提供者接口：**

```python
class DataProvider:
    """
    数据提供者接口（公式引擎通过此接口获取行情数据）。
    由股票池引擎（或更底层）实现并注入。
    """
    
    def fetch_bars(self, code: str, period: str = "1d", 
                   count: int = None) -> Optional[pd.DataFrame]:
        """
        获取K线数据。
        
        Args:
            code: 股票代码
            period: 周期（1d/1m/5m/...）
            count: 获取的K线数量，None表示全部
            
        Returns:
            DataFrame（包含 open/high/low/close/vol 等列），数据不足时返回 None
        """
        ...
    
    def get_snapshot(self, code: str) -> Optional[dict]:
        """
        获取最新快照数据。
        
        Args:
            code: 股票代码
            
        Returns:
            快照字典（包含最新价、成交量等），无数据时返回 None
        """
        ...
```

### 2.3 公式引擎完整公开接口

**v1.8 的公式引擎完整接口（6个）：**

| 接口 | 签名 | 说明 | 谁调用 |
|------|------|------|--------|
| **set_data_provider** | `set_data_provider(provider: DataProvider) -> None` | 设置数据提供者 | 初始化时，股票池引擎调用 |
| **eval_indicator** | `eval_indicator(formula_id: str, code: str, period: str = "1d", args: dict = None) -> float \| bool \| None` | 单只股票单指标求值 | 股票池引擎（少用，主要用批量） |
| **eval_indicators** | `eval_indicators(formula_ids: List[str], codes: List[str], period: str = "1d", args_map: dict = None) -> Dict[str, Dict[str, Any]]` | 批量股票多指标批量求值（推荐） | 股票池引擎（主要接口） |
| **on_data_updated** | `on_data_updated(dirty_codes: Set[str]) -> None` | 通知数据更新，公式引擎内部失效相关缓存 | 数据更新时，股票池引擎调用 |
| **get_cache_stats** | `get_cache_stats() -> dict` | 获取缓存统计（命中率、缓存大小等） | 调试/监控时调用 |
| **invalidate_all** | `invalidate_all() -> None` | 清空所有缓存（公式/参数变更时用） | 编译期/配置变更时调用 |

**接口详细说明：**

#### 1. set_data_provider

```python
def set_data_provider(self, provider: DataProvider) -> None:
    """
    设置数据提供者。公式引擎通过此提供者获取K线数据。
    
    必须在调用 eval_indicator(s) 之前设置。
    """
```

#### 2. eval_indicator（单只单指标）

```python
def eval_indicator(self, formula_id: str, code: str, 
                   period: str = "1d", args: dict = None) -> Any:
    """
    对单只股票的单个指标求值。
    
    Args:
        formula_id: 公式ID（对应 formula_registry 中的 id）
        code: 股票代码
        period: 周期（1d/1m/5m/...）
        args: 公式参数字典（如 {"SHORT": 5, "LONG": 10}）
        
    Returns:
        指标值（float）或条件结果（bool），数据不足时返回 None
    """
```

#### 3. eval_indicators（批量多指标，推荐）

```python
def eval_indicators(self, formula_ids: List[str], codes: List[str],
                    period: str = "1d", 
                    args_map: Dict[str, dict] = None) -> Dict[str, Dict[str, Any]]:
    """
    批量计算多只股票的多个指标（推荐接口，性能最优）。
    
    公式引擎内部优化：
    - 同一只股票的多个指标，可以复用同一份K线数据
    - 同一个指标的多只股票，可以向量化计算
    - 已经缓存的结果直接返回，只重算 dirty_codes 的
    
    Args:
        formula_ids: 公式ID列表
        codes: 股票代码列表
        period: 周期
        args_map: {formula_id: args_dict}，每个公式的参数
        
    Returns:
        {formula_id: {code: value}} 双层字典
    """
```

#### 4. on_data_updated（脏数据通知）

```python
def on_data_updated(self, dirty_codes: Set[str]) -> None:
    """
    通知数据更新。
    
    公式引擎内部操作：
    1. 将 dirty_codes 的指标值缓存标记为失效
    2. 将 dirty_codes 的K线数据缓存标记为失效
    3. 下次 eval_indicators 时，自动重算这些股票
    
    注意：这是一个"通知"，不是"命令"。公式引擎内部决定什么时候真正重算
         （懒加载：下次调用 eval_indicators 时才重算）。
    
    Args:
        dirty_codes: 数据更新了的股票代码集合
    """
```

#### 5. get_cache_stats

```python
def get_cache_stats(self) -> dict:
    """
    获取缓存统计信息（用于调试和监控）。
    
    Returns:
        {
            "formula_cache": {"size": 100, "max_size": 1000, "hit_rate": 0.95},
            "indicator_cache": {"size": 5000, "hit_rate": 0.8},
            "data_cache": {"size": 2000, "hit_rate": 0.9},
            ...
        }
    """
```

#### 6. invalidate_all

```python
def invalidate_all(self) -> None:
    """
    清空所有缓存（公式定义/参数变更时使用）。
    
    包括：公式编译缓存、指标值缓存、数据缓存等。
    """
```

### 2.4 公式引擎内部结构（股票池引擎不需要知道）

**再次强调：以下都是公式引擎内部的实现细节，股票池引擎完全不需要知道。** 列出来只是为了完整性。

```
公式引擎内部结构（黑盒内部）：
  ┌─────────────────────────────────────────────────┐
  │                 FormulaEngine                   │
  │                                                 │
  │  ┌───────────┐  ┌───────────┐  ┌───────────┐  │
  │  │  公式编译  │  │ 指标值缓存 │  │ 数据缓存   │  │
  │  │  缓存     │  │           │  │           │  │
  │  │ (formula  │  │ (indicator│  │ (data     │  │
  │  │  → compiled)│ │ _results) │  │ _cache)   │  │
  │  └───────────┘  └───────────┘  └───────────┘  │
  │        │              │              │          │
  │        └──────────────┼──────────────┘          │
  │                       │                         │
  │              ┌────────▼────────┐               │
  │              │  求值核心        │               │
  │              │  (eval_core)    │               │
  │              └────────┬────────┘               │
  │                       │                         │
  │              ┌────────▼────────┐               │
  │              │  DataProvider   │               │
  │              │  (接口)         │               │
  │              └─────────────────┘               │
  └─────────────────────────────────────────────────┘
```

**公式引擎内部的缓存策略（股票池引擎不关心）：**

| 缓存 | 结构 | 失效时机 |
|------|------|---------|
| 公式编译缓存 | `formula_id → CompiledFormula` | `invalidate_all()` 或公式定义变更 |
| 指标值缓存 | `(formula_id, period, args_key) → {code: value}` | `on_data_updated(dirty_codes)` 时，对应 code 的值失效 |
| K线数据缓存 | `(code, period) → DataFrame` | `on_data_updated(dirty_codes)` 时，对应 code 的数据失效 |

---

## 三、核心升级三：脏来源拆分

### 3.1 为什么要拆成两种脏？

**v1.7 的问题：`dirty_nodes` 混成一团，分不清是"股票列表变了"还是"股票数据变了"。**

```
v1.7 的 dirty_nodes（一锅粥）：
  dirty_nodes = {nid1, nid2, nid3, ...}
  
  一个节点是脏的，可能是因为：
  A. 股票列表变了（有新入池/出池的股票）
  B. 股票数据变了（节点里有 dirty_stocks 里的股票）
  
  但这两种情况的处理逻辑完全不同！
  
  A（stock_change）的处理：
    - 新入池的股票：要算它的所有指标、做比较、看是否通过
    - 出池的股票：要从所有结果里删掉
    - 相当于：对这部分股票做"全量初始化"
    
  B（data_change）的处理：
    - 只需要重算数据变了的股票的指标和比较
    - 其他股票的结果都可以复用
    - 相当于：对这部分股票做"增量更新"
  
  v1.7 混成一个 dirty_nodes，导致：
    - 要么两种情况都按全量处理（性能差）
    - 要么两种情况都按增量处理（可能漏算）
    - 代码里到处是 if/else 猜到底是哪种
```

**v1.8 的正确做法：拆成两张表，分开记录，分开处理。**

### 3.2 两种脏来源

#### 来源 A：stock_change（股票列表变化）

| 属性 | 说明 |
|------|------|
| **触发原因** | 节点股票列表变了（新股票入池、旧股票出池、TTL过期、备选池刷新等） |
| **表结构** | `node_stock_change[nid]: bool` —— 该节点的股票列表是否变了 |
| **处理逻辑** | 需要对**全部股票**重新计算吗？不，只需要对**变化的部分**（入池/出池）做处理 |
| **特点** | 变化的是"股票范围"，不是"数据值" |

```python
# 结构示意
node_stock_change = {
    "node_001": True,   # 这个节点的股票列表变了
    "node_002": False,  # 没变
    ...
}
```

#### 来源 B：data_change（股票数据变化）

| 属性 | 说明 |
|------|------|
| **触发原因** | 某些股票的数据更新了（行情 tick 来了），且这些股票在节点里 |
| **表结构** | `node_data_dirty[nid]: Set[code]` —— 该节点里哪些股票的数据变了 |
| **处理逻辑** | 只需要重算这些股票的指标和比较，其他股票的结果复用 |
| **特点** | 变化的是"数据值"，不是"股票范围" |

```python
# 结构示意
node_data_dirty = {
    "node_001": {"000001", "000002"},  # 这两只股票的数据变了
    "node_002": set(),                  # 没有脏股票
    ...
}
```

### 3.3 两种脏的处理逻辑对比

| 维度 | stock_change（股票列表变了） | data_change（股票数据变了） |
|------|---------------------------|---------------------------|
| **表** | `node_stock_change[nid]: bool` | `node_data_dirty[nid]: Set[code]` |
| **影响范围** | 变化的那些股票（入池/出池） | 数据变了的那些股票 |
| **indicator_results** | 新入池的股票要算，出池的要删 | 只重算 dirty_codes 的 |
| **edge_compare_results** | 新入池的要算，出池的要删 | 只重算 dirty_codes 的 |
| **edge_filter_results** | 要重新计算（因为股票集合变了） | 独立型增量更新，排名型全量重排 |
| **处理复杂度** | 中等（要处理入池和出池） | 简单（只重算脏股票） |
| **性能影响** | 视变化数量而定 | 通常很小（tick 稀疏时） |

### 3.4 节点是否需要重新评估？

**节点需要重新评估 = 有 stock_change OR 有 data_dirty**

```python
def needs_re_evaluation(nid):
    """节点 nid 是否需要重新评估其出边？"""
    has_stock_change = node_stock_change.get(nid, False)
    has_data_dirty = len(node_data_dirty.get(nid, set())) > 0
    return has_stock_change or has_data_dirty
```

**但具体怎么评估，取决于是什么类型的脏——两种情况处理逻辑不同。**

### 3.5 删除旧的 dirty_nodes

**v1.8 删除旧的 `dirty_nodes: Set[nid]`，替换为：**

```
删除：
  dirty_nodes: Set[nid]  —— 一锅粥，分不清是哪种脏

新增：
  node_stock_change: Dict[nid → bool]  —— 股票列表是否变了
  node_data_dirty: Dict[nid → Set[code]]  —— 哪些股票数据变了
```

**为什么用两张表代替一张？**

- 一张表只能表达"是/否脏"，表达不了"哪些股票脏了"
- 两种脏的处理逻辑完全不同，必须分开
- 分开后，代码更清晰，不容易出错
- 性能更好：可以精准地只处理需要处理的部分

---

## 四、事件驱动的完整链条（时序图）

### 4.1 完整流程图：从数据更新到事件发出

```mermaid
flowchart TD
    A[行情数据推送] --> B[更新 latest_tick[code]]
    B --> C[dirty_stocks.add code]
    C --> D[更新 node_data_dirty: 对每个包含 code 的节点]
    D --> E[公式引擎 on_data_updated dirty_stocks]
    E --> F[公式引擎内部标记失效]
    
    F --> G[Tick 循环开始]
    G --> H[处理 TTL 过期]
    H --> I{过期了吗?}
    I -->|是| J[从 node_stocks 移除]
    J --> K[标记 node_stock_change = True]
    K --> L[处理 dirty nodes 循环]
    I -->|否| L
    
    L --> M[取出一个节点 nid]
    M --> N{需要评估吗? stock_change or data_dirty}
    N -->|否| O[跳过，下一个节点]
    N -->|是| P[遍历该节点的所有出边]
    
    P --> Q[检查时间触发条件]
    Q --> R{时间到了吗?}
    R -->|否| S[跳过这条边]
    R -->|是| T[执行边的 filter]
    
    T --> U["=== 第一层：指标计算 ==="]
    U --> V[调用 formula_engine.eval_indicators]
    V --> W[公式引擎内部: 只重算 dirty_codes]
    W --> X[更新 indicator_results 内部]
    
    X --> Y["=== 第二层：比较判断 ==="]
    Y --> Z{filter 类型?}
    Z -->|独立比较型| AA[增量比较: 只重算 data_dirty 的股票]
    Z -->|排名型| AB[跳过比较层，直接到集合运算]
    AA --> AC[更新 edge_compare_results[eid]]
    
    AC --> AD["=== 第三层：集合运算 ==="]
    AB --> AD
    AD --> AE{集合运算类型?}
    AE -->|逻辑组合 AND/OR/NOT| AF[基于比较结果增量更新]
    AE -->|排名/截面| AG[全量排名: 基于所有股票的指标值]
    AF --> AH[更新 edge_filter_results[eid]]
    AG --> AH
    
    AH --> AI["=== propagate ==="]
    AI --> AJ[对比 edge_filter_results 和 node_stocks[tid]]
    AJ --> AK[计算 entered_codes 和 exited_codes]
    AK --> AL[更新 node_stocks[tid]]
    AL --> AM[标记目标节点 node_stock_change = True]
    AM --> AN[发射入池/出池事件]
    
    AN --> AO[处理完这条边]
    AO --> AP{还有出边吗?}
    AP -->|有| Q
    AP -->|没有| AQ[清除该节点的脏标记]
    AQ --> AR{还有脏节点吗?}
    AR -->|有| M
    AR -->|没有| AS[Tick 结束]
    AS --> AT[清空 dirty_stocks]
    AT --> AU[等待下一次数据更新]
    
    O --> AR
    S --> AP
```

### 4.2 每一步的读写表对应

| 步骤 | 读什么表 | 写什么表 | 说明 |
|------|---------|---------|------|
| 行情推送 | — | `latest_tick[code] = new_bar` + `dirty_stocks.add(code)` | 数据层 |
| 更新节点脏 | `code_nodes[code]` | `node_data_dirty[nid].add(code)` | 节点粒度的数据脏 |
| 通知公式引擎 | `dirty_stocks` | 公式引擎内部失效标记 | `on_data_updated(dirty_codes)` |
| TTL 过期 | `ttl_expiry_queue` + `now` | `node_stocks[nid]` 移除 + `node_stock_change[nid] = True` | 股票列表变化 |
| 指标计算 | 公式引擎接口 `eval_indicators` | 公式引擎内部 `indicator_results` | 公式引擎黑盒 |
| 比较层（独立型） | `indicator_results`（通过接口）+ `edge_compare_results[eid]` | `edge_compare_results[eid]`（只更新 dirty 的） | 增量比较 |
| 集合运算-逻辑 | `edge_compare_results[eid]` | `edge_filter_results[eid]` | 增量更新集合 |
| 集合运算-排名 | `indicator_results`（通过接口） | `edge_filter_results[eid]` | 全量重排 |
| propagate | `edge_filter_results[eid]` + `node_stocks[tid]` | `node_stocks[tid]` + `node_stock_change[tid] = True` | 传播变化 |
| 事件发射 | `node_stocks[tid]` 新旧对比 | `event_queue` + `signal_queue` | 差集计算 |

---

## 五、运行时内存表（v1.8 更新版）

### 5.1 核心运行时表（8张）

| 表名 | 类型 | 读时机 | 写时机 | 说明 |
|------|------|--------|--------|------|
| `latest_tick` | Dict[code → bar_dict] | 公式引擎计算时读（通过 DataProvider） | 行情推送时写 | **唯一真相源**。所有股票的最新tick数据。 |
| `node_stocks` | Dict[nid → List[code]] | propagate 读写、filter 读 | 边执行/TTL过期后写 | 各节点当前股票列表 |
| `ttl_expiry_queue` | Heap[(expire_ts, nid, code)] | tick 开始时弹出 | 股票入池时插入 | TTL 过期队列。按过期时间排序的最小堆 |
| `dirty_stocks` | Set[code] | 通知公式引擎时传、比较层增量时用 | tick数据更新时加入 / 本轮处理完清空 | **股票级水位线**。本轮数据更新了的股票集合。 |
| `node_stock_change` | Dict[nid → bool] | 执行循环读 | 节点股票变化时设为 True / 处理完清 False | **v1.8 新增**。节点股票列表是否变了（入池/出池/TTL）。 |
| `node_data_dirty` | Dict[nid → Set[code]] | 执行循环读、比较层增量读 | 数据更新时加入 / 处理完清空 | **v1.8 新增**。节点里哪些股票的数据变了。 |
| `edge_compare_results` | Dict[eid → Dict[code → bool]] | 集合运算层读 | 比较层写（增量更新） | **v1.8 新增**。每条边的单股比较结果（独立型 filter）。 |
| `edge_filter_results` | Dict[eid → Set[code]] | propagate 读 | 集合运算层写 | **v1.8 新增**。每条边最终通过的股票集合。 |

**v1.8 相比 v1.7 的变化：**

| v1.7（5张） | v1.8（8张） | 变化原因 |
|-------------|-------------|---------|
| `latest_tick` | `latest_tick` | 不变 |
| `node_stocks` | `node_stocks` | 不变 |
| `dirty_nodes` | ~~`dirty_nodes`~~ **删除** | 拆成两张表，更清晰 |
| `ttl_expiry_queue` | `ttl_expiry_queue` | 不变 |
| `dirty_stocks` | `dirty_stocks` | 不变 |
| — | **`node_stock_change`** | **新增**：股票列表变化标记 |
| — | **`node_data_dirty`** | **新增**：节点内数据脏的股票集合 |
| — | **`edge_compare_results`** | **新增**：比较层状态表 |
| — | **`edge_filter_results`** | **新增**：集合运算层状态表 |

**净变化：5张 → 8张（删1张，加4张），状态显式化，概念更清晰。**

### 5.2 公式引擎内部表（黑盒，股票池引擎不直接访问）

| 表名 | 类型 | 说明 |
|------|------|------|
| `formula_cache` | Dict[formula_id → CompiledFormula] | 公式编译缓存 |
| `indicator_results` | Dict[(formula_id, period, args_key) → Dict[code → value]] | 指标值缓存（第一层状态表） |
| `data_cache` | Dict[(code, period) → DataFrame] | K线数据缓存 |

**这些都是公式引擎内部的事，股票池引擎完全不需要知道。** 股票池引擎只通过公开接口与公式引擎交互。

### 5.3 编译期表（不变）

编译期产物和 v1.7 一样，没有变化：

| 编译产物 | 类型 | 说明 |
|----------|------|------|
| `formula_registry` | Dict[indicator_id → formula_spec] | 公式注册表。所有去重后的指标，编译期一次性收集 |
| `comparison_operators` | Dict[op_id → operator_spec] | 比较算子集。所有可用的比较算子，独立于指标 |
| `edge_indicator_refs` | Dict[eid → List[indicator_id]] | 每条边引用的指标ID列表 |
| `edge_compare_spec` | Dict[eid → compare_spec] | 比较层规格（用哪个算子、参数是什么） |
| `edge_set_op_spec` | Dict[eid → set_op_spec] | 集合运算层规格（AND/OR/NOT/排名逻辑） |
| `edge_filter_type` | Dict[eid → 'independent' / 'global'] | filter 类型：单股独立型 / 全局依赖型 |

---

## 六、核心循环伪代码（v1.8 更新版）

### 6.1 完整的事件驱动循环

```python
# ============================================================
#  v1.8 核心循环伪代码
# ============================================================

# --- 初始化 ---
formula_engine.set_data_provider(data_provider)  # 设置数据提供者

while True:
    # 1. 等数据更新（或时间步进）
    wait_for_data_update()
    
    # 2. 处理新数据
    if new_data_arrived():
        for code, new_bar in new_tick_data:
            latest_tick[code] = new_bar
            dirty_stocks.add(code)
            
            # 更新 node_data_dirty（每个包含这只股票的节点）
            for nid in code_nodes[code]:
                if nid not in node_data_dirty:
                    node_data_dirty[nid] = set()
                node_data_dirty[nid].add(code)
        
        # 通知公式引擎：哪些股票的数据更新了
        formula_engine.on_data_updated(dirty_stocks)
    
    # 3. 处理 TTL 过期
    expired = pop_all_expired_from_ttl_queue(now)
    for expire_ts, nid, code in expired:
        # 从节点移除
        node_stocks[nid].remove(code)
        # 标记股票列表变了
        node_stock_change[nid] = True
        # 清理相关状态（可选，懒清理也行）
        # ...
    
    # 4. 处理脏节点（按拓扑序）
    while has_dirty_nodes():
        # 取出一个节点（按拓扑序，先深后浅或反过来，取决于方向）
        nid = get_next_dirty_node()
        
        has_stock_change = node_stock_change.get(nid, False)
        data_dirty_codes = node_data_dirty.get(nid, set())
        has_data_dirty = len(data_dirty_codes) > 0
        
        if not has_stock_change and not has_data_dirty:
            # 这个节点其实不脏，跳过
            clear_node_dirty(nid)
            continue
        
        # 遍历该节点的所有条件出边
        for eid in sorted(out_edges[nid], key=edge_order):
            edge = edges[eid]
            sid = edge.source_id
            tid = edge.target_id
            
            # a. 检查时间触发条件
            if not edge_timing_should_fire(eid):
                continue
            
            # b. 检查源节点是否需要重新计算
            if not has_stock_change and not has_data_dirty:
                continue
            
            source_codes = node_stocks[sid]
            
            # === 第一层：指标计算（调用公式引擎） ===
            indicator_ids = edge_indicator_refs[eid]
            
            # 计算需要重算的股票（并集）
            if has_stock_change:
                # 股票列表变了：所有源节点股票都要算（因为新入池的可能没算过）
                # 但公式引擎内部有缓存，所以传全部也没事，会自动增量
                codes_to_eval = source_codes
            else:
                # 只有数据变了：只传 dirty 的股票
                codes_to_eval = data_dirty_codes & source_codes
                if not codes_to_eval:
                    # 没有需要重算的，跳过
                    # 但等一下，edge_filter_results 可能还没初始化？
                    # 第一次运行时还是要全量算
                    pass
            
            # 调用公式引擎批量接口
            # 注意：传全部 source_codes，公式引擎内部决定哪些重算
            # 这样更简单，而且公式引擎可以复用缓存
            indicator_values = formula_engine.eval_indicators(
                formula_ids=indicator_ids,
                codes=source_codes,      # 传全部，公式引擎内部增量
                period=edge_period(eid),
                args_map=edge_args_map(eid),
            )
            # indicator_values = {ind_id: {code: value}}
            
            # === 第二层：比较判断 ===
            compare_spec = edge_compare_spec.get(eid)
            
            if compare_spec is not None:
                # 有比较层（独立比较型）
                if eid not in edge_compare_results:
                    edge_compare_results[eid] = {}
                
                if has_stock_change:
                    # 股票列表变了：全部重新比较（简单粗暴，正确第一）
                    # 优化方向：可以只比较新入池的，但要处理出池的清理
                    for code in source_codes:
                        result = do_compare(
                            indicator_values, code, compare_spec
                        )
                        edge_compare_results[eid][code] = result
                    # 清理出池股票的比较结果
                    stale_codes = set(edge_compare_results[eid].keys()) - set(source_codes)
                    for code in stale_codes:
                        del edge_compare_results[eid][code]
                else:
                    # 只有数据变了：增量比较，只重算 dirty 的
                    for code in (data_dirty_codes & source_codes):
                        result = do_compare(
                            indicator_values, code, compare_spec
                        )
                        edge_compare_results[eid][code] = result
            
            # === 第三层：集合运算 ===
            set_op_spec = edge_set_op_spec[eid]
            filter_type = edge_filter_type[eid]
            
            if filter_type == 'independent':
                # 独立型：基于比较结果
                if has_stock_change or has_data_dirty:
                    # 重新计算通过的集合
                    # 优化方向：可以增量更新集合
                    passed = set()
                    for code in source_codes:
                        if edge_compare_results[eid].get(code, False):
                            passed.add(code)
                    edge_filter_results[eid] = passed
            else:
                # 全局依赖型（排名/截面）：基于全量指标值
                # 只要有任何数据变化，都要全量重排
                if has_stock_change or has_data_dirty:
                    passed = do_rank_filter(
                        indicator_values, source_codes, set_op_spec
                    )
                    edge_filter_results[eid] = passed
            
            # === propagate（边的步骤） ===
            old_target = set(node_stocks[tid])
            new_target = edge_filter_results[eid]
            
            entered = new_target - old_target
            exited = old_target - new_target
            
            if entered or exited:
                # 更新目标节点股票列表
                node_stocks[tid] = list(new_target)
                # 标记目标节点股票列表变了
                node_stock_change[tid] = True
                
                # 发射事件
                for code in entered:
                    emit_event("stock_entered", tid, code)
                for code in exited:
                    emit_event("stock_exited", tid, code)
        
        # 处理完这个节点的所有边后，清除脏标记
        clear_node_dirty(nid)
    
    # 5. 清脏（本轮 tick 处理完了）
    dirty_stocks.clear()
    # node_stock_change 和 node_data_dirty 在处理过程中逐个清除了
    
    # 6. 回去等下一次数据更新
```

### 6.2 核心循环的关键变化（v1.7 → v1.8）

| 步骤 | v1.7 | v1.8 | 变化 |
|------|------|------|------|
| 脏节点表示 | `dirty_nodes: Set[nid]`（混成一团） | `node_stock_change` + `node_data_dirty`（拆成两张） | 更清晰，处理逻辑分开 |
| 数据更新通知 | 隐式（公式引擎自己判断） | 显式 `formula_engine.on_data_updated(dirty_codes)` | 契约更明确 |
| 数据来源 | 模糊（data_fetcher / data_query） | 明确 `DataProvider` 接口 + `set_data_provider` | 边界清晰 |
| 比较层结果 | 隐式（每次重算或 filter_cache） | 显式 `edge_compare_results[eid][code]` | 状态显式化，可增量 |
| 过滤结果 | 隐式（直接 propagate） | 显式 `edge_filter_results[eid] = Set[code]` | 状态显式化，可追踪 |
| 指标计算接口 | `eval_indicator_batch`（单指标） | `eval_indicators`（多指标批量，推荐） | 性能更优，接口更完整 |

---

## 七、功能-表操作对应表（v1.8 更新版）

### 7.1 数据层（股票级水位线 + 公式引擎通知）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| 行情推送 | — | `latest_tick[code] = new_bar` + `dirty_stocks.add(code)` | 股票级水位线，哪只变了加哪只 |
| **更新节点数据脏** | `code_nodes[code]` | `node_data_dirty[nid].add(code)` | **v1.8 新增**：每只股票的数据脏，记录到对应节点 |
| **通知公式引擎** | `dirty_stocks` | 公式引擎内部失效标记 | **v1.8 新增**：`formula_engine.on_data_updated(dirty_codes)`，显式通知 |
| **指标计算** | 公式引擎接口 `eval_indicators` | 公式引擎内部 `indicator_results` | **v1.8 升级**：多指标批量接口，公式引擎内部增量 |
| 编译期指标去重 | 所有边的指标配置 | `formula_registry` + `edge_indicator_refs` | 不变，相同（公式+参数+周期）合并为一个 |

### 7.2 TTL 淘汰层（事件驱动，非轮询）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| 股票入池记录 TTL | `edge_ttl_spec[eid]` | `ttl_expiry_queue` 插入 `(expire_ts, nid, code)` | `expire_ts = now + ttl_sec` |
| TTL 过期检查 | `ttl_expiry_queue + now` | 弹出过期项 | 最小堆：堆顶过期就弹出，直到堆顶未过期 |
| 过期股票移除 | `node_stocks[nid]` | `node_stocks[nid]` | 从节点移除过期股票 |
| **过期触发级联** | — | `node_stock_change[nid] = True` | **v1.8 变化**：不是加 dirty_nodes，是设 node_stock_change |

### 7.3 边触发判定层（节点脏驱动 + 三要素 AND）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **数据更新 → 节点数据脏** | `code_nodes[code]` | `node_data_dirty[nid].add(code)` | **v1.8 变化**：不是加 dirty_nodes，是加到 node_data_dirty |
| 节点股票状态变化 → 节点股票变了 | `out_edges[sid]` | `node_stock_change[tid] = True` | **v1.8 变化**：标记 stock_change，不是加 dirty_nodes |
| 边时间触发检查 | `edge_timing_spec[eid] + _flow_state[eid]` | — | `starttype × cxtype` 的判定 |
| **三要素检查** | `node_stock_change[nid]` + `node_data_dirty[nid]` + 时间条件 | — | **v1.8 更新**：时间条件 AND (stock_change OR 有 data_dirty) |
| 边是否需要执行 | 三要素是否全部满足 | — | 三要素都满足就执行，否则跳过 |

### 7.4 边执行层（三层filter + 状态表 + propagate + 即时事件）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| **filter 类型判断** | `edge_filter_type[eid]` | — | 先判断 independent / global，再选策略 |
| **第一层：指标计算（公式引擎接口）** | `formula_registry` + 公式引擎接口 `eval_indicators` | 公式引擎内部 `indicator_results` | **v1.8 升级**：多指标批量接口，公式引擎内部增量，股票池引擎不关心缓存细节 |
| **第二层：比较判断（独立型）** | 指标值（来自公式引擎） + `comparison_operators` + `edge_compare_spec[eid]` + `node_data_dirty[sid]` | `edge_compare_results[eid]` | **v1.8 新增**：显式状态表，增量更新（只重算 data_dirty 的） |
| **第三层：集合运算** | `edge_compare_results[eid]`（独立型）或指标值（排名型） + `edge_set_op_spec[eid]` + `node_stocks[sid]` | `edge_filter_results[eid]` | **v1.8 新增**：显式状态表，独立型增量，排名型全量 |
| **propagate（边的步骤）** | `edge_filter_results[eid]` + `node_stocks[tid]` + `edge_propagate_spec[eid]` | `node_stocks[tid]` + `node_stock_change[tid] = True` | **v1.8 变化**：从 edge_filter_results 读，不是当场算 |
| **即时事件发射** | `node_stocks[tid]` 新旧对比 + `node_role[tid]` | `event_queue` + `signal_queue` | 差集计算，每条边执行后立即发射 |
| 无条件边立即传播 | `node_stocks[sid]` + `edge_propagate_spec[eid]` | `node_stocks[tid]` + `node_stock_change[tid] = True` | 直接 propagate，无 gate，无 filter |

### 7.5 事件层（流式逐条产生）

| 功能 | 读什么表 | 写什么表 | 计算 | 时机 |
|------|---------|---------|------|------|
| 入池事件 | `node_stocks[tid]` 执行前后对比 | `event_queue` | 差集计算（新 - 旧） | 每条边执行后立即发射 |
| 出池事件 | `node_stocks[sid]` 执行前后对比 | `event_queue` | 差集计算（旧 - 新） | 每条边执行后立即发射 |
| 预警事件 | `alert_rules + node_stocks` 变化 | `alert_queue` | 规则匹配 | 每条边执行后立即检查 |
| 交易信号 | `node_role[tid] == 'target' + 入池/出池` | `signal_queue` | 角色判定 + 信号生成 | 每条边执行后立即生成 |

**（这部分完全不变）**

### 7.6 后处理层（PK排名/分析角度/看盘面板）

| 功能 | 读什么表 | 写什么表 | 计算 |
|------|---------|---------|------|
| PK排名 | `node_stocks[target] + pk_config` | `_pk_rankings` | 按权重评分排序，指标值通过公式引擎 `eval_indicators` 接口获取 |
| 分析角度 | `node_stocks[target] + analysis_config` | `_angle_results` | 多维度计算，指标值通过公式引擎接口获取 |
| 看盘面板 | `node_stocks + dashboard_schema` | `_dashboard_data` | 组装显示数据，指标值通过公式引擎接口获取 |

**注意：后处理也通过公式引擎接口获取指标值！**
- PK 排名用到的指标，直接调用公式引擎 `eval_indicators`
- 公式引擎内部会做缓存，不用重复计算
- 股票池引擎不需要关心缓存细节

---

## 八、概念变化对照表（v1.7 → v1.8）

### 8.1 消除的概念/表

| v1.7 概念 | v1.8 处理 | 理由 |
|-----------|-----------|------|
| `dirty_nodes: Set[nid]` | **删除**，拆为两张表 | 混成一团，分不清是股票变了还是数据变了，处理逻辑不同 |

### 8.2 新增的概念/表

| v1.8 新概念 | 说明 | 为什么加 |
|-----------|------|-----------|
| `node_stock_change[nid]: bool` | 节点股票列表是否变了（入池/出池/TTL） | 与 data_change 区分，处理逻辑不同 |
| `node_data_dirty[nid]: Set[code]` | 节点里哪些股票的数据变了 | 精准增量，只重算变了的股票 |
| `edge_compare_results[eid][code]` | 每条边的单股比较结果（独立型 filter） | 状态显式化，可增量，可调试 |
| `edge_filter_results[eid]` | 每条边最终通过的股票集合 | 状态显式化，可追踪，可验证 |
| `DataProvider` 接口 | 公式引擎的数据提供者接口 | 边界清晰，公式引擎自己管数据 |
| `on_data_updated(dirty_codes)` | 公式引擎的脏数据通知接口 | 显式契约，股票池引擎通知，公式引擎内部失效 |
| `eval_indicators` | 多指标批量求值接口 | 性能更优，一次调用算多个指标 |

### 8.3 概念数量统计

| 版本 | 核心运行时表数 | 变化 |
|------|---------------|------|
| v1.5 | 8 张 | — |
| v1.6 | 7 张 | -1 |
| v1.6.1 | 5 张 | -2 |
| v1.7 | 5 张 | 0（数量不变，质量升级） |
| v1.8 | **8 张** | **+3（删1张，加4张，状态显式化）** |

v1.8 核心运行时表（8张）：
1. `latest_tick`（唯一真相源）
2. `node_stocks`（节点股票）
3. `ttl_expiry_queue`（TTL队列）
4. `dirty_stocks`（股票级水位线）
5. `node_stock_change`（股票列表变化标记）← 新增
6. `node_data_dirty`（节点内数据脏的股票）← 新增
7. `edge_compare_results`（比较层状态表）← 新增
8. `edge_filter_results`（集合运算层状态表）← 新增

**对，从 5 张变回 8 张。但这 8 张和 v1.5 的 8 张完全不同——v1.5 是冗余的、概念不清的 8 张；v1.8 是每层状态显式化、职责清晰的 8 张。先做对，再做好，再做简。当前阶段是"做好"。**

### 8.4 保留的正确设计（v1.7 做对的部分，v1.8 继续保留）

| 设计 | 说明 | 状态 |
|------|------|------|
| 指标是纯函数，数据不变则结果不变 | ✅ | 保留，这是最根本的洞察 |
| filter三层结构（指标→比较→集合运算） | ✅ | **强化**：每层都有显式状态表 |
| 传播是边的步骤，不是filter层 | ✅ | 保留，职责分离 |
| 公式注册表 + 编译期指标去重 | ✅ | 保留，避免重复计算 |
| 比较算子正交化（指标×算子=filter） | ✅ | 保留，笛卡尔积设计 |
| 无条件边立即传播 | ✅ | 保留，同步传播 |
| 公式引擎内部维护指标缓存 | ✅ | **强化**：明确接口契约 + DataProvider |
| 股票级水位线 dirty_stocks | ✅ | 保留，这是性能核心 |
| 节点脏驱动 | ✅ | **升级**：拆成 stock_change 和 data_dirty 两种 |

---

## 九、实现路线图（v1.8）

### 阶段一：公式引擎完整契约（P0）

1. **定义 DataProvider 接口**
   - 抽象接口：`fetch_bars(code, period, count)` + `get_snapshot(code)`
   - 现有 TqAdapter 适配为 DataProvider

2. **完善公式引擎公开接口**
   - 增加 `set_data_provider(provider)` 方法
   - 增加 `on_data_updated(dirty_codes)` 方法（内部标记失效）
   - 增加 `eval_indicators(formula_ids, codes, period, args_map)` 批量多指标接口
   - 保留 `eval_indicator` 作为便捷方法（单只单指标）
   - 增加 `invalidate_all()` 方法

3. **公式引擎内部状态表**
   - 明确 `indicator_results` 作为内部第一层状态表
   - `on_data_updated` 时标记对应 code 的指标值失效
   - `eval_indicators` 时懒加载重算（只重算失效的）

### 阶段二：脏来源拆分（P0）

1. **删除 `dirty_nodes: Set[nid]`**
   - 替换为 `node_stock_change: Dict[nid → bool]`
   - 替换为 `node_data_dirty: Dict[nid → Set[code]]`

2. **更新数据更新路径**
   - 行情推送时：更新 `node_data_dirty[nid].add(code)`
   - 不再加 `dirty_nodes`

3. **更新 TTL 过期路径**
   - 过期时：设置 `node_stock_change[nid] = True`
   - 不再加 `dirty_nodes`

4. **更新 propagate 路径**
   - 目标节点变化时：设置 `node_stock_change[tid] = True`
   - 不再加 `dirty_nodes`

5. **更新核心循环**
   - 节点脏判定：`node_stock_change[nid] or len(node_data_dirty[nid]) > 0`
   - 处理时：区分 stock_change 和 data_dirty，分别处理
   - 处理完：清除对应节点的脏标记

### 阶段三：补齐三层状态表（P0）

1. **第二层：edge_compare_results**
   - 数据结构：`Dict[eid → Dict[code → bool]]`
   - 独立比较型 filter 才有
   - stock_change 时：全量重算（或增量处理入池/出池）
   - data_change 时：增量重算 data_dirty 的股票

2. **第三层：edge_filter_results**
   - 数据结构：`Dict[eid → Set[code]]`
   - 所有 filter 类型都有
   - 独立型：基于 edge_compare_results 更新
   - 排名型：基于全量指标值全量重排
   - propagate 从这里读，不是当场算

3. **增量计算完整链条验证**
   - 验证：dirty_stocks → 指标重算 → 比较重算 → 集合运算 → propagate
   - 每一步的输入输出都正确
   - 增量计算结果与全量计算结果一致

### 阶段四：时序图与文档完善（P1）

1. **完善时序图**
   - 从数据更新到事件发出的完整流程
   - 每一步读什么表、写什么表都标注

2. **更新所有相关文档**
   - 确保所有文档使用 v1.8 的概念
   - 不再出现 dirty_nodes（旧概念）

3. **验证与测试**
   - 正确性验证：增量计算结果 = 全量计算结果
   - 性能验证：tick 稀疏时性能数量级提升
   - 调试便利性：每层状态可观察、可验证

---

## 十、统计总结（v1.7 → v1.8）

### 10.1 概念数量变化

| 统计项 | v1.7 | v1.8 | 变化 |
|--------|------|------|------|
| 核心运行时表 | 5 张 | **8 张** | +3（删1加4，状态显式化） |
| filter 层数 | 3 层 | 3 层 | 不变（但每层都有显式状态表） |
| 公式引擎接口数 | 3 个 | **6 个** | +3（set_data_provider / on_data_updated / eval_indicators / invalidate_all） |
| 脏来源种类 | 1 种（混成一团） | **2 种**（stock_change + data_change） | 拆分为两种，处理逻辑分开 |
| 显式状态表层数 | 1 层（只有指标层隐式） | **3 层**（指标/比较/过滤） | +2 层，每层状态显式化 |

### 10.2 为什么表数又增加了？

**v1.5 是 8 张，v1.6.1 砍到 5 张，v1.8 又回到 8 张——这不是倒退，而是螺旋上升。**

```
演进路径：
  v1.5：8 张（冗余、概念不清、很多表不知道干嘛的）
   ↓  砍掉冗余，回归简单
  v1.6.1：5 张（先做对——概念最少，逻辑正确）
   ↓  在正确的基础上精细化
  v1.7：5 张（质量升级——股票级水位线，性能提升）
   ↓  进一步精细化，状态显式化
  v1.8：8 张（做好——每层状态明确，增量计算完整）
```

**关键区别：**

| 维度 | v1.5 的 8 张 | v1.8 的 8 张 |
|------|-------------|-------------|
| 概念清晰度 | 模糊，很多表不知道干嘛的 | 清晰，每层状态对应一层计算 |
| 增量计算 | 没有，全量重算 | 完整链条，一层一层往下传 |
| 可调试性 | 差，不知道哪层算错了 | 好，每层状态可观察 |
| 设计原则 | 想到什么加什么 | 三层 filter 结构的自然映射 |

**先做对，再做好，再做简。v1.8 是"做好"的阶段。** 等实现完、验证完，也许还能进一步简化（比如某些表可以合并），但那是后面的事。现在先把结构想清楚、做正确。

### 10.3 一句话总结

**v1.8 补齐三层状态表（指标结果/比较结果/过滤结果），每张表有明确的生命周期和失效条件；公式引擎接口契约完整化，明确 DataProvider 接口 + dirty_codes 通知机制；拆分两种脏来源（stock_change vs data_change），处理逻辑分开；增量计算链条完整闭环，每一步读写什么表都清清楚楚。**

---

## 附录：为什么要把状态表显式化？

### A.1 隐式状态 vs 显式状态

| 维度 | 隐式状态（v1.7 之前） | 显式状态（v1.8） |
|------|---------------------|-----------------|
| 存在形式 | 散布在各个变量、缓存里 | 集中在命名清晰的表中 |
| 可读性 | 差，要猜哪存了什么 | 好，看表名就知道干嘛的 |
| 可调试性 | 差，不知道哪层错了 | 好，逐层检查状态表 |
| 增量优化 | 难，不知道哪些可复用 | 易，每层都可以增量更新 |
| 心智负担 | 高，要记住所有隐式状态 | 低，表就是文档 |

### A.2 状态表是"文档"也是"蓝图"

**显式状态表 = 可执行的文档。**

```
你想知道比较层的结果是什么样的？
  → 看 edge_compare_results 的结构
  → 看它的更新时机、失效条件
  → 直接打印出来验证

你想优化性能？
  → 看哪层可以增量更新
  → 看哪些结果可以复用
  → 一层一层往下优化

你想调试 bug？
  → 先看 indicator_results 对不对
  → 再看 edge_compare_results 对不对
  → 再看 edge_filter_results 对不对
  → 哪层出问题一眼就知道
```

### A.3 为什么不直接用公式引擎的缓存？

**公式引擎的缓存是公式引擎内部的事，股票池引擎的状态表是股票池引擎的事。**

```
两个层面的状态：
  公式引擎内部状态：
    - indicator_results：指标值缓存
    - 目的：避免重复计算，提升性能
    - 归属：公式引擎管
    
  股票池引擎状态：
    - edge_compare_results：比较结果
    - edge_filter_results：过滤结果
    - 目的：增量计算、调试、可观察性
    - 归属：股票池引擎管

两者的关系：
  股票池引擎调用公式引擎接口 → 拿到指标值 → 计算比较结果 → 计算过滤结果
  ↑ 这是股票池引擎的计算流程，每层的中间结果就是状态表
```

**公式引擎的缓存是"黑盒内部的优化"，股票池引擎的状态表是"白盒的计算状态"——两者不冲突，各有各的用途。**
