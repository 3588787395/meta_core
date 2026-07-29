# 改进计划

## 核心原则

1. **定时器触发时立即注册下一次**，与模块计算无关
2. **禁止兼容旧接口**，必须唯一正确
3. **运行时事件没有顺序**，所有边所有状态池触发即开干；但边有设计结构顺序号，决定交集/差集计算逻辑

---

## 代码与目标差距

### G1: EventDriver — 线性扫描改优先队列

| 当前代码 | 目标 |
|---------|------|
| `self._specs: List[TimedEventSpec]` 线性列表 | `heapq` 优先队列 `(next_fire_time, spec)` |
| `fire_due` 遍历所有 spec 检查 `at_fn() <= now` | 弹出堆顶到时项 |
| `at_fn` 延迟求值 | 入队时固定触发时间 |
| 触发后不自动注册下次 | 触发时立即注册下次（与模块无关） |
| TtlTracker 单独 heapq | TTL 也注册到同一优先队列 |
| fire_due / fire_ttl_due 两个方法 | 一个 fire_due 统一处理 |

**删除**：`fire_due` 线性扫描、`fire_ttl_due`、`at_fn` 延迟求值、`is_edge_due`、`self._specs` 列表

**新增**：
```python
class EventDriver:
    def __init__(self):
        self._heap: List[tuple[float, TimedEventSpec]] = []

    def add_spec(self, spec, first_fire_time: float):
        heapq.heappush(self._heap, (first_fire_time, spec))

    def fire_due(self, now: float) -> None:
        while self._heap and self._heap[0][0] <= now:
            fire_time, spec = heapq.heappop(self._heap)
            # ① 发布事件
            spec.action(spec.params)
            # ② 立即注册下次（与模块无关）
            if spec.interval is not None:
                next_time = fire_time + spec.interval
                if spec.end_fn is None or next_time <= spec.end_fn():
                    heapq.heappush(self._heap, (next_time, spec))
```

**难点**：`at_fn` 延迟求值与优先队列不兼容 → 入队时固定时间，删除 `at_fn`

---

### G2: 引擎只发事件不执行计算

| 当前代码 | 目标 |
|---------|------|
| `spec.action` 直接调用 `edge_executor.run()` | `spec.action` 只发布事件 |
| `_make_edge_action` 既发 EdgeFired 又调 run | 只发 EdgeFired |
| `_run_tick_body` 中 `driver.fire_due()` 同步执行 | 引擎发完事件即结束 |

**删除**：`_make_edge_action` 中对 `edge_executor.run()` 的直接调用、旧路径 `_run_tick`

**新增**：
- kind="edge" 的 `spec.action` 只发布 `EdgeFired(eid, ts)`
- kind="ttl" 的 `spec.action` 只发布 `TTLDue(nid, code, ts)`
- kind="tick" 的 `spec.action` 只发布 `TickDue(code, ts)`
- EdgeExecutor 订阅 EdgeFired 自行完成筛选/转移
- TradeModule 订阅 TTLDue 自行完成卖出/删除

---

### G3: EdgeFired 不携带 changed_codes

| 当前代码 | 目标 |
|---------|------|
| `EdgeFired(eid, ts, changed_codes=...)` | `EdgeFired(eid, ts)` |
| `_make_edge_action` 计算 `changed_codes` 传入 | 不计算不传入 |
| `_on_edge_fired` 从 `event.changed_codes` 取 | 从 `source_pool.get_dirty_codes()` 取 |
| `_filter(changed_codes=...)` 参数来自事件 | 参数来自 StatePool |

**删除**：`EdgeFired.changed_codes` 字段、`_make_edge_action` 中 `changed_codes` 计算逻辑

---

### G4: StatePool 视图对象

| 当前代码 | 目标 |
|---------|------|
| `state.node_stocks[nid]` 裸字典直接访问 | `StatePool` 对象接口 |
| `state.get_node_stocks(nid)` / `set_node_stocks(nid, stocks)` | `state.get_pool(nid).get_stocks()` |
| `state.dirty.changed_codes` 全局集合直接访问 | `pool.get_dirty_codes()` |
| 入池/出池不标脏 | `add_stocks()` / `remove_stocks()` 自动标脏 |

**删除**：`get_node_stocks` / `set_node_stocks` / `add_node_stocks` / `remove_node_stocks` 扁平接口

**新增**：
```python
class StatePool:
    def __init__(self, state: 'PoolState', nid: str):
        self._state = state
        self._nid = nid

    def get_stocks(self) -> List[str]:
        return self._state.node_stocks[self._nid]

    def get_stock_codes(self) -> Set[str]:
        return {_stock_code(s) for s in self._state.node_stocks[self._nid]}

    def get_dirty_codes(self) -> Set[str]:
        return self._state.dirty.changed_codes & self.get_stock_codes()

    def add_stocks(self, codes: List[str]) -> None:
        self._state.node_stocks[self._nid].extend(codes)
        self._state.dirty.changed_codes.update(codes)  # 入池标脏

    def remove_stocks(self, codes: List[str]) -> None:
        code_set = set(codes)
        self._state.node_stocks[self._nid] = [
            s for s in self._state.node_stocks[self._nid]
            if _stock_code(s) not in code_set
        ]
        self._state.dirty.changed_codes.update(codes)  # 出池标脏
```

**难点**：约 100+ 处 `node_stocks` / `get_node_stocks` / `set_node_stocks` 引用需全部替换，无兼容

---

### G5: SimTickSource 改 MockDataSource

| 当前代码 | 目标 |
|---------|------|
| `SimTickSource(TickSource)` 类名 | `MockDataSource(TickSource)` 类名 |
| SimTickSource 内部 heapq 管理 tick | MockDataSource 注册 tick 到 EventDriver 统一优先队列 |

**删除**：`SimTickSource` 类名、`_TICK_SOURCE_FACTORIES["sim"]` 中对 SimTickSource 的引用

**新增**：重命名为 `MockDataSource`，tick 定时器注册到 EventDriver

---

### G6: 删除 execution_order（运行时拓扑排序），保留边顺序号

| 当前代码 | 目标 |
|---------|------|
| `CompiledSchedule.execution_order` 运行时拓扑排序 | 不存在运行时拓扑排序 |
| `_run_tick` 按拓扑序遍历边 | 各边独立触发 |
| 边有顺序号用于交集/差集逻辑 | 保留边顺序号（设计结构） |

**删除**：`execution_order` 运行时拓扑排序属性、`_run_tick` 中的遍历逻辑

**保留**：边的顺序号（edge_order），用于交集/差集运算次序

---

## 实施顺序

```
Step 1: G4 — StatePool 视图对象（其他改动依赖它）
Step 2: G3 — EdgeFired 去 changed_codes（依赖 StatePool）
Step 3: G5 — SimTickSource 改 MockDataSource（独立）
Step 4: G1 — EventDriver 改优先队列（核心重构）
Step 5: G2 — 引擎只发事件不执行（依赖 G1+G3）
Step 6: G6 — 删除 execution_order（依赖 G2）
Step 7: 整合验证
```

每步都是破坏性改动，不保留旧接口，一步到位。

---

## 每步要删除的代码清单

| Step | 删除项 | 文件 |
|------|--------|------|
| 1 | `get_node_stocks`/`set_node_stocks`/`add_node_stocks`/`remove_node_stocks` | runtime_mode_module.py, engine.py |
| 2 | `EdgeFired.changed_codes` 字段 | event_bus.py |
| 2 | `_make_edge_action` 中 changed_codes 计算 | execution_module.py |
| 3 | `SimTickSource` 类名 | domain.py, engine.py |
| 4 | `EventDriver.fire_due` 线性扫描、`fire_ttl_due`、`at_fn`、`is_edge_due` | execution_module.py |
| 4 | `self._specs: List[TimedEventSpec]` | execution_module.py |
| 5 | `_make_edge_action` 中 `edge_executor.run()` 调用 | execution_module.py |
| 5 | `_run_tick` 旧路径 | engine.py |
| 6 | `execution_order`（运行时拓扑排序） | execution_module.py, engine.py |
| 6 | 保留边顺序号（edge_order） | 不删除 |
