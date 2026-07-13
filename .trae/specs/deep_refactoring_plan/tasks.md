# 股票池深度重构 - 实施任务清单

## 阶段一：核心重构（2周）

### [ ] Task 1: 提取 latest_tick 独立表
- **Priority**: high
- **Depends On**: None
- **Description**:
  - 新建 `core/runtime/tick_table.py`，封装最新tick表
  - 包含：`data` (Dict[code→bar])、`ts` (水位线时间戳)、`hash` (全量hash)
  - `update(tick_data)` 方法：比较hash，水位线没涨则返回False
  - 从 engine.py 中剥离所有 tick 数据相关逻辑
- **Acceptance Criteria Addressed**: AC-1, AC-2
- **Test Requirements**:
  - `programmatic` TR-1.1: 相同数据重复update返回False，水位线不变
  - `programmatic` TR-1.2: 不同数据update返回True，水位线递增
  - `programmatic` TR-1.3: hash计算正确（修改任意字段hash变化）
  - `human-judgement` TR-1.4: 代码结构清晰，单一职责

### [ ] Task 2: 编译器 compiler.py
- **Priority**: high
- **Depends On**: None
- **Description**:
  - 新建 `core/compiler.py`
  - `compile(pool_config) → CompiledPool` 方法
  - 编译内容：节点字典、边字典、端点解析、邻接表、源节点列表
  - **执行顺序从 edge.params._order 读取**，不是拓扑排序！
  - 边类型判定（条件/无条件）
  - 边规格编译：timing_spec / filter_spec / propagate_spec
  - 节点角色判定
- **Acceptance Criteria Addressed**: AC-3, AC-4
- **Test Requirements**:
  - `programmatic` TR-2.1: 执行顺序按 _order 排序，不是拓扑序
  - `programmatic` TR-2.2: 端点解析正确（sid/tid）
  - `programmatic` TR-2.3: 邻接表正确（out_edges/in_edges）
  - `programmatic` TR-2.4: 源节点判定正确（入度为0）
  - `human-judgement` TR-2.5: CompiledPool 结构清晰，字段命名准确

### [ ] Task 3: 重写核心循环
- **Priority**: high
- **Depends On**: Task 1, Task 2
- **Description**:
  - engine.py 核心函数：`run_once(compiled, now_ts)`
  - 按 `compiled.edge_order` 遍历边
  - 三要素触发判定：时间到了 AND (股票脏 OR 数据脏)
  - 脏标记传播：执行完边，目标节点标记为股票脏
  - tick 末清除所有脏标记
  - engine.py 总行数 ≤ 800
- **Acceptance Criteria Addressed**: AC-1, AC-2, AC-5, AC-7
- **Test Requirements**:
  - `programmatic` TR-3.1: 水位线不变时，零计算（直接返回空events）
  - `programmatic` TR-3.2: 源节点不脏时，不出边不执行
  - `programmatic` TR-3.3: 时间条件不满足时，边不执行
  - `programmatic` TR-3.4: 执行顺序与 edge_order 一致
  - `programmatic` TR-3.5: 行为等价性：新旧引擎同样输入同样输出
  - `human-judgement` TR-3.6: 核心循环 ≤ 150 行，逻辑清晰

### [ ] Task 4: 时间触发表驱动
- **Priority**: high
- **Depends On**: Task 2
- **Description**:
  - 新建 `core/timing.py`
  - 8 种 starttype 规则 + 3 种 cxtype 规则
  - `eval_gate(edge_spec, now_ts, flow_state) → bool`
  - 配置表：`config/timing.json`（8 start + 3 cx = 11 条规则）
  - 不是 24 条记录，是 8+3 条规则的笛卡尔积
- **Acceptance Criteria Addressed**: AC-6
- **Test Requirements**:
  - `programmatic` TR-4.1: 24 种组合全部正确
  - `programmatic` TR-4.2: flow_state（执行次数/持续时间）正确更新
  - `human-judgement` TR-4.3: 表驱动结构清晰，没有硬编码 if 链

### [ ] Task 5: 过滤条件表驱动
- **Priority**: high
- **Depends On**: Task 1, Task 2
- **Description**:
  - 新建 `core/filters.py`
  - 6 种 nset 求值器 + 10 种 noperate 比较器
  - `eval(codes, filter_spec, tick_data) → (passed, rejected)`
  - 配置表：`config/filter_specs.json`
- **Acceptance Criteria Addressed**: AC-6
- **Test Requirements**:
  - `programmatic` TR-5.1: 6 种求值器全部正确
  - `programmatic` TR-5.2: 10 种比较器全部正确
  - `programmatic` TR-5.3: 60 种组合抽样验证
  - `human-judgement` TR-5.4: 求值器和比较器正交，组合清晰

### [ ] Task 6: 传播模式表驱动
- **Priority**: high
- **Depends On**: Task 2
- **Description**:
  - 新建 `core/propagate.py`
  - 3 种模式：copy / move / overwrite
  - `apply(src_stocks, tgt_stocks, passed, mode) → new_tgt_stocks`
  - 配置表：`config/propagate_modes.json`
- **Acceptance Criteria Addressed**: AC-6
- **Test Requirements**:
  - `programmatic` TR-6.1: copy 模式正确（源不变，目标累加）
  - `programmatic` TR-6.2: move 模式正确（源清空，目标累加）
  - `programmatic` TR-6.3: overwrite 模式正确（目标替换）
  - `human-judgement` TR-6.4: 模式表驱动，没有硬编码 if

### [ ] Task 7: 节点角色表驱动
- **Priority**: high
- **Depends On**: Task 2
- **Description**:
  - 新建 `core/roles.py`
  - 5 种角色：candidate / state / condition / target / discard
  - `on_change(node_id, role, old_stocks, new_stocks) → events`
  - 配置表：`config/node_roles.json`
- **Acceptance Criteria Addressed**: AC-6, AC-8
- **Test Requirements**:
  - `programmatic` TR-7.1: target 池入池生成 ENTER 事件 + BUY 信号
  - `programmatic` TR-7.2: target 池出池生成 EXIT 事件 + SELL 信号
  - `programmatic` TR-7.3: discard 池出池生成 EXIT 事件
  - `human-judgement` TR-7.4: 角色行为由表驱动，没有硬编码角色判断

---

## 阶段二：外围剥离（1周）

### [ ] Task 8: 后处理独立模块
- **Priority**: medium
- **Depends On**: Task 3
- **Description**:
  - PK排名 → `post_processing/pk_ranking.py`
  - 分析角度 → `post_processing/analysis_angles.py`
  - 看盘面板 → `post_processing/dashboard.py`
  - 预警 → `post_processing/alerts.py`
  - 从 engine.py 完全剥离
- **Acceptance Criteria Addressed**: AC-9
- **Test Requirements**:
  - `programmatic` TR-8.1: 后处理不影响核心循环
  - `human-judgement` TR-8.2: 模块边界清晰

### [ ] Task 9: 事件/信号生成独立
- **Priority**: medium
- **Depends On**: Task 7
- **Description**:
  - 新建 `core/events.py`
  - 事件队列、信号队列、冷却机制
  - 从 engine.py 剥离
- **Acceptance Criteria Addressed**: AC-8
- **Test Requirements**:
  - `programmatic` TR-9.1: 事件去重/冷却正确
  - `human-judgement` TR-9.2: 事件系统独立可测试

### [ ] Task 10: K线合成器独立
- **Priority**: medium
- **Depends On**: Task 1
- **Description**:
  - K线合成从 engine.py 剥离
  - 订阅 latest_tick 变化，独立维护 kline_cache
  - 股票池引擎只读 kline_cache，不负责合成
- **Acceptance Criteria Addressed**: AC-10
- **Test Requirements**:
  - `programmatic` TR-10.1: 1分→5分→15分→日线 合成正确
  - `human-judgement` TR-10.2: 数据层与计算层分离

### [ ] Task 11: 数据源适配器统一接口
- **Priority**: medium
- **Depends On**: Task 10
- **Description**:
  - 统一各数据源接口规范
  - 实盘/回放/仿真 三种数据源
  - 都实现 `get_tick(ts) → tick_data` 接口
- **Acceptance Criteria Addressed**: AC-11
- **Test Requirements**:
  - `programmatic` TR-11.1: 三种模式切换正常
  - `human-judgement` TR-11.2: 接口一致，可替换

---

## 阶段三：前端对齐（1周）

### [ ] Task 12: 综合设置表格与后端对齐
- **Priority**: medium
- **Depends On**: Task 2
- **Description**:
  - 综合设置表格展示所有边的执行顺序
  - 支持拖拽调整顺序
  - 调整后写回 edge.params._order
- **Acceptance Criteria Addressed**: AC-4
- **Test Requirements**:
  - `human-judgement` TR-12.1: 综合设置与执行顺序一致
  - `human-judgement` TR-12.2: 交互直观

### [ ] Task 13: 执行顺序可视化
- **Priority**: medium
- **Depends On**: Task 12
- **Description**:
  - 画布上显示边的执行顺序编号
  - 编号模式：点击边分配/交换编号
  - 与综合设置表格联动
- **Acceptance Criteria Addressed**: AC-4
- **Test Requirements**:
  - `human-judgement` TR-13.1: 编号显示正确
  - `human-judgement` TR-13.2: 与综合设置同步

### [ ] Task 14: 三模式切换界面清理
- **Priority**: low
- **Depends On**: Task 11
- **Description**:
  - 实盘/回放/仿真 三种模式切换界面
  - 清晰、不混乱
  - 选中状态明确
- **Acceptance Criteria Addressed**: AC-11
- **Test Requirements**:
  - `human-judgement` TR-14.1: 三模式切换清晰无混乱

---

## 阶段四：优化验证（1周）

### [ ] Task 15: 性能基准测试
- **Priority**: medium
- **Depends On**: Task 3
- **Description**:
  - 对比重构前后性能
  - 测试场景：空转（水位线不变）、单条边触发、全量计算
  - 性能提升 ≥ 5x（全量场景）
- **Acceptance Criteria Addressed**: AC-7
- **Test Requirements**:
  - `programmatic` TR-15.1: 空转场景性能提升 ≥ 100x
  - `programmatic` TR-15.2: 单条边触发场景性能提升 ≥ 10x
  - `programmatic` TR-15.3: 全量场景性能提升 ≥ 5x

### [ ] Task 16: 全量回归测试
- **Priority**: high
- **Depends On**: Task 8, Task 9, Task 10
- **Description**:
  - 运行所有现有测试
  - 确保行为等价
  - 0 个测试失败
- **Acceptance Criteria Addressed**: AC-12
- **Test Requirements**:
  - `programmatic` TR-16.1: 所有单元测试通过
  - `programmatic` TR-16.2: 所有集成测试通过
  - `programmatic` TR-16.3: 行为等价性测试通过

### [ ] Task 17: 代码审查与文档
- **Priority**: low
- **Depends On**: Task 16
- **Description**:
  - 代码审查
  - 架构文档完善
  - API 文档
- **Acceptance Criteria Addressed**: AC-12
- **Test Requirements**:
  - `human-judgement` TR-17.1: 代码质量达标
  - `human-judgement` TR-17.2: 文档完整
