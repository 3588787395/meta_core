# meta_core 面向对象事件驱动重构 — 实施计划

## [ ] Task 1: 建立基准测试框架
- **Priority**: high
- **Depends On**: None
- **Description**:
  - 搭建新旧引擎对拍测试框架
  - 收集性能基准数据（空转/全量/典型场景）
  - 准备 5 个典型测试池（简单/复杂/多节点/多条件/大池子）
- **Acceptance Criteria Addressed**: AC-5, AC-7
- **Test Requirements**:
  - `programmatic` TR-1.1: 对拍框架可运行，输入配置相同输出完全一致
  - `programmatic` TR-1.2: 性能基准脚本可输出 P50/P95/P99 数据
  - `programmatic` TR-1.3: 5 个测试池全部加载运行无错
- **Notes**: 基准是后续所有重构的验证基础，必须先做

## [ ] Task 2: 设计面向对象类体系
- **Priority**: high
- **Depends On**: Task 1
- **Description**:
  - 设计 Node 基类及子类（备选池/状态池/条件节点/数据源等）
  - 设计 Edge 基类及子类（条件边/无条件边/公式边等）
  - 设计 Event 基类及子类（数据事件/时间事件/状态事件等）
  - 设计 Engine 主类（协调者角色）
  - 明确类之间的关系、职责边界、接口契约
- **Acceptance Criteria Addressed**: AC-1, AC-2
- **Test Requirements**:
  - `human-judgement` TR-2.1: 类图清晰，职责单一，继承关系合理
  - `human-judgement` TR-2.2: 每个类有明确的接口和事件
  - `human-judgement` TR-2.3: 节点/边/事件三大体系正交分离
- **Notes**: 设计先行，编码在后

## [ ] Task 3: 实现事件系统
- **Priority**: high
- **Depends On**: Task 2
- **Description**:
  - 实现 Event 基类和各类型事件子类
  - 实现事件总线/事件队列
  - 实现事件订阅/发布机制
  - 实现事件处理调度
- **Acceptance Criteria Addressed**: AC-2
- **Test Requirements**:
  - `programmatic` TR-3.1: 事件可发布可订阅，回调正确触发
  - `programmatic` TR-3.2: 事件顺序可控制（优先级/时序）
  - `programmatic` TR-3.3: 事件携带正确的上下文数据
- **Notes**: 事件系统是整个架构的骨架

## [ ] Task 4: 实现节点类体系
- **Priority**: high
- **Depends On**: Task 3
- **Description**:
  - 实现 Node 基类（股票存储、出入池、属性管理）
  - 实现备选池节点（数据源接入）
  - 实现状态池节点（股票持有、TTL 淘汰）
  - 实现条件节点（公式计算）
  - 实现目标池节点（信号生成、告警）
- **Acceptance Criteria Addressed**: AC-1
- **Test Requirements**:
  - `programmatic` TR-4.1: 每个节点类型可独立实例化和测试
  - `programmatic` TR-4.2: 节点股票增删正确，状态变更触发事件
  - `programmatic` TR-4.3: TTL 淘汰逻辑正确
- **Notes**: 节点是状态的载体

## [ ] Task 5: 实现边类体系
- **Priority**: high
- **Depends On**: Task 4
- **Description**:
  - 实现 Edge 基类（时机判断、触发逻辑、执行入口）
  - 实现条件转移边（gate + filter + propagate）
  - 实现无条件转移边（直接 propagate）
  - 实现公式边（公式计算 + 结果分发）
  - 时机判断查表驱动（timing.json）
  - 过滤计算委托 FormulaRouter
- **Acceptance Criteria Addressed**: AC-1, AC-2, AC-6
- **Test Requirements**:
  - `programmatic` TR-5.1: 每种边类型可独立测试
  - `programmatic` TR-5.2: 时机判断与旧引擎行为一致
  - `programmatic` TR-5.3: 过滤结果与旧引擎一致
  - `programmatic` TR-5.4: 状态流转与旧引擎一致
- **Notes**: 边是计算的主体

## [ ] Task 6: 实现数据驱动增量计算
- **Priority**: high
- **Depends On**: Task 5
- **Description**:
  - 实现数据时间戳机制（每只股票/每个节点的最新数据时间）
  - 实现脏标记机制（数据变了才计算）
  - 实现增量计算（只计算变更的股票）
  - 实现公式结果缓存（数据不变则复用）
- **Acceptance Criteria Addressed**: AC-3, AC-4
- **Test Requirements**:
  - `programmatic` TR-6.1: 数据不变时零公式计算调用
  - `programmatic` TR-6.2: 部分数据变更时只计算变更股票
  - `programmatic` TR-6.3: 空转性能比旧引擎提升 10 倍以上
- **Notes**: 数据驱动是性能提升的核心

## [ ] Task 7: 实现引擎主类和编排
- **Priority**: high
- **Depends On**: Task 6
- **Description**:
  - 实现 StockPoolEngine 主类（协调者）
  - 实现配置加载和编译（一次性编译，运行时只读）
  - 实现执行顺序调度（按用户指定顺序，非拓扑序）
  - 实现事件循环和事件分发
  - 实现对外 API（run_pool / run_loop / run_mode 等）
- **Acceptance Criteria Addressed**: AC-4, AC-5, AC-7
- **Test Requirements**:
  - `programmatic` TR-7.1: 对外 API 与旧引擎完全兼容
  - `programmatic` TR-7.2: 执行顺序可配置，与拓扑无关
  - `programmatic` TR-7.3: 5 个测试池新旧对拍全部通过
- **Notes**: 引擎主类应该很薄，只做编排

## [ ] Task 8: 集成验证和性能测试
- **Priority**: high
- **Depends On**: Task 7
- **Description**:
  - 全量对拍测试（所有现有测试用例）
  - 性能基准测试（空转/全量/典型场景）
  - 内存占用测试
  - 兼容性测试（DZH/TDX 格式导入导出）
- **Acceptance Criteria Addressed**: AC-5, AC-6, AC-7
- **Test Requirements**:
  - `programmatic` TR-8.1: 新旧对拍通过率 100%
  - `programmatic` TR-8.2: 空转性能提升 ≥ 10 倍
  - `programmatic` TR-8.3: 全量性能不低于旧引擎
  - `programmatic` TR-8.4: engine.py ≤ 1000 行
- **Notes**: 验证重构是否成功的关键

## [ ] Task 9: 文档和示例
- **Priority**: medium
- **Depends On**: Task 8
- **Description**:
  - 架构设计文档
  - API 文档
  - 扩展开发指南（新增节点类型/边类型）
  - 迁移指南（从旧引擎到新引擎）
- **Acceptance Criteria Addressed**: AC-1
- **Test Requirements**:
  - `human-judgement` TR-9.1: 文档清晰，开发者可按文档扩展
  - `human-judgement` TR-9.2: 示例代码可运行
