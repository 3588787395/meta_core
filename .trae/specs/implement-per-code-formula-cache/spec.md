# Per-code公式缓存与增量筛选 - Product Requirement Document

## Overview
- **Summary**: 实现per-code粒度的公式计算缓存与增量筛选机制，大幅提升股票池运行时的公式计算性能。当只有少数股票有Tick/Bar变化时，仅对变化的股票重新计算公式，其余股票沿用缓存结果，实现100只股票增量筛选耗时<50ms的性能目标。
- **Purpose**: 解决当前公式缓存使用全局bar_hash导致单只股票变化时所有股票缓存失效的问题；优化筛选逻辑，实现增量计算，减少不必要的重复公式求值。
- **Target Users**: 股票池平台开发者、量化交易用户，需要高性能实时股票筛选。

## Goals
- 将公式缓存从全局粒度改为per-code粒度，单只股票K线变化仅失效该股票对应公式缓存
- 实现增量筛选逻辑，根据changed_codes集合决定全量评估或增量评估
- 确保缓存结果一致性，增量合并后筛选结果与全量计算结果相同
- 性能目标：100只股票中2只Tick变化时，增量筛选耗时<50ms

## Non-Goals (Out of Scope)
- 不修改公式计算核心逻辑，仅优化缓存和筛选调度
- 不引入新的公式类型或指标
- 不修改事件驱动架构的核心流程
- 不涉及UI界面修改

## Background & Context
- 当前代码中formula_module.py已有_hash_code_bars函数和_cached_eval的per-code缓存雏形
- execution_module.py的_filter方法已有增量筛选框架
- 测试文件test_incremental_filter.py已创建但需要修复API调用方式
- changed_codes通过TickBarModule.apply_data()自动记录到state.dirty.changed_codes
- EdgeFired事件携带changed_codes参数，通过_on_edge_fired传递给EdgeExecutor.run()

## Functional Requirements
- **FR-1**: per-code公式缓存：缓存key为(formula_ref, code, period)，存储(code_bar_hash, value)
- **FR-2**: code_bar_hash计算：对单只股票该周期的K线数据计算md5哈希，而非全局所有股票
- **FR-3**: 增量筛选三种模式：
  - changed_codes=None：全量评估所有源池股票
  - changed_codes=[]：缓存命中则直接返回cached_passed，否则全量
  - changed_codes非空：仅对changed_codes ∩ 源池股票重新评估
- **FR-4**: 增量结果合并：passed_set = (cached_passed - changed_set) | newly_passed
- **FR-5**: 筛选结果缓存：state.filter_inputs[eid]存储为frozenset
- **FR-6**: 首次运行无缓存时正确执行全量计算

## Non-Functional Requirements
- **NFR-1**: 性能：100只股票中2只变化时，增量筛选平均耗时<50ms
- **NFR-2**: 正确性：增量筛选结果必须与全量计算结果一致
- **NFR-3**: 缓存一致性：股票数据未变化时必须命中缓存，不重复计算
- **NFR-4**: 向后兼容：不破坏现有API和功能

## Constraints
- **Technical**: Python 3.x，使用现有pandas/numpy技术栈
- **Architecture**: 事件驱动架构，模块间仅通过EventBus交互
- **Code style**: 遵循现有表驱动设计，最小化修改，不另开炉灶

## Assumptions
- _hash_code_bars函数已正确实现
- _cached_eval方法已基本实现per-code缓存
- _filter方法已基本实现增量筛选逻辑
- DirtyState.changed_codes已正确跟踪变化股票

## Acceptance Criteria

### AC-1: per-code公式缓存粒度正确
- **Given**: 两只股票600000和600001，相同公式
- **When**: 仅600000的K线数据变化
- **Then**: 600001的公式缓存保持有效，不重新计算；仅600000重新计算
- **Verification**: `programmatic`

### AC-2: code_bar_hash计算正确性
- **Given**: 相同的K线数据
- **When**: 多次计算_hash_code_bars
- **Then**: 返回相同的哈希值；不同K线数据返回不同哈希值
- **Verification**: `programmatic`

### AC-3: 全量评估模式（changed_codes=None）
- **Given**: 首次运行或changed_codes=None
- **When**: 执行_filter
- **Then**: 对所有源池股票进行公式评估，不使用缓存
- **Verification**: `programmatic`

### AC-4: 空changed_codes缓存命中模式
- **Given**: 已有缓存结果，changed_codes=[]
- **When**: 执行_filter
- **Then**: 直接返回cached_passed，不调用公式求值
- **Verification**: `programmatic`

### AC-5: 增量评估模式正确性
- **Given**: 100只股票，2只Tick变化
- **When**: 执行增量筛选
- **Then**: 仅对这2只股票重新计算公式，其余98只沿用缓存
- **Verification**: `programmatic`

### AC-6: 增量合并结果正确性
- **Given**: TEST01从9元涨到12元（新增通过），TEST02从11元跌到8元（不再通过），TEST03保持9.5元
- **When**: 执行增量筛选
- **Then**: passed_set包含TEST01，不包含TEST02，TEST03结果保持不变
- **Verification**: `programmatic`

### AC-7: filter_inputs存储为frozenset
- **Given**: 任意筛选执行后
- **When**: 检查state.filter_inputs[eid]
- **Then**: 所有值均为frozenset类型
- **Verification**: `programmatic`

### AC-8: 性能达标
- **Given**: 100只股票，2只变化
- **When**: 执行50次增量筛选
- **Then**: 平均耗时<50ms
- **Verification**: `programmatic`

### AC-9: 缓存一致性
- **Given**: 首次全量计算通过集合S1，仅2只股票变化
- **When**: 增量计算得到通过集合S2
- **Then**: 未变化的98只股票在S1和S2中的成员关系完全一致
- **Verification**: `programmatic`

## Open Questions
- [ ] 当前_cached_eval实现是否完全正确？需要验证
- [ ] 测试文件需要如何调整以正确调用API（changed_codes通过apply_data传入而非run_pool参数）？
