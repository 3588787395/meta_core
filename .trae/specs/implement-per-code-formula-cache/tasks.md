# Per-code公式缓存与增量筛选 - The Implementation Plan (Decomposed and Prioritized Task List)

## [ ] Task 1: 验证并修复core/formula_module.py的per-code缓存实现
- **Priority**: high
- **Depends On**: None
- **Description**: 
  - 验证现有_cached_eval方法的per-code缓存逻辑正确性
  - 确认缓存key为("formula_per_code", ctx.mode, formula_ref, code, period)
  - 确认code_bar_hash使用_hash_code_bars对单只股票K线计算哈希
  - 确认缓存值为(code_bar_hash, value)元组
  - 确认某只股票K线变化仅失效该股票对应缓存
- **Acceptance Criteria Addressed**: [AC-1, AC-2]
- **Test Requirements**:
  - `programmatic` TR-1.1: 相同K线数据多次计算hash返回相同值
  - `programmatic` TR-1.2: 不同K线数据计算hash返回不同值
  - `programmatic` TR-1.3: 单只股票变化不影响其他股票缓存
- **Notes**: 根据代码探索，_hash_code_bars和_cached_eval已基本实现，需验证正确性

## [ ] Task 2: 验证并修复core/execution_module.py的_filter增量筛选逻辑
- **Priority**: high
- **Depends On**: [Task 1]
- **Description**: 
  - 验证现有_filter方法的三种模式逻辑正确性：
    - changed_codes=None：全量评估
    - changed_codes=[]：缓存命中直接返回
    - changed_codes非空：增量评估，仅对changed_set重新计算
  - 确认增量合并公式：passed_set = (prev_passed | set(newly_passed)) & codes_set
  - 确认filter_inputs存储为frozenset
  - 确认首次运行无缓存时正确全量计算
- **Acceptance Criteria Addressed**: [AC-3, AC-4, AC-5, AC-6, AC-7, AC-9]
- **Test Requirements**:
  - `programmatic` TR-2.1: changed_codes=None时对所有股票评估
  - `programmatic` TR-2.2: changed_codes=[]且有缓存时直接返回
  - `programmatic` TR-2.3: changed_codes非空时仅评估变化股票
  - `programmatic` TR-2.4: 增量合并结果正确（新增/移除股票正确反映）
  - `programmatic` TR-2.5: filter_inputs[eid]为frozenset类型
  - `programmatic` TR-2.6: 未变化股票结果与上次一致
- **Notes**: 根据代码探索，_filter方法已基本实现，需验证正确性

## [ ] Task 3: 修复并完善测试脚本test_incremental_filter.py
- **Priority**: high
- **Depends On**: [Task 2]
- **Description**: 
  - 修复测试中调用run_pool的方式：changed_codes不应作为run_pool参数传入
  - 正确的测试方式：通过apply_data传入变化数据，系统自动跟踪changed_codes
  - 或者直接调用EdgeExecutor.run(eid, changed_codes=...)进行单元测试
  - 修复股票代码格式问题（测试中使用6{i:05d}应为6{i:06d}生成6位代码）
  - 确保所有8个测试用例可独立运行
- **Acceptance Criteria Addressed**: [AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-7, AC-8, AC-9]
- **Test Requirements**:
  - `programmatic` TR-3.1: test_per_code_cache通过
  - `programmatic` TR-3.2: test_filter_inputs_storage通过
  - `programmatic` TR-3.3: test_first_run_no_cache通过
  - `programmatic` TR-3.4: test_empty_changed_codes通过
  - `programmatic` TR-3.5: test_full_vs_incremental通过（仅2只股票重新计算）
  - `programmatic` TR-3.6: test_cache_consistency通过（98只未变化股票结果一致）
  - `programmatic` TR-3.7: test_incremental_merge_correctness通过
  - `programmatic` TR-3.8: test_performance通过（平均耗时<50ms）
- **Notes**: 关键问题是测试API调用方式，changed_codes是state内部跟踪的，不是run_pool的参数

## [ ] Task 4: 运行完整测试套件验证所有功能
- **Priority**: high
- **Depends On**: [Task 3]
- **Description**: 
  - 运行test_incremental_filter.py所有测试用例
  - 修复测试中发现的任何bug
  - 确保增量筛选性能达标（<50ms）
  - 运行现有项目测试确保无回归问题
- **Acceptance Criteria Addressed**: [AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-7, AC-8, AC-9]
- **Test Requirements**:
  - `programmatic` TR-4.1: 所有8个增量筛选测试通过
  - `programmatic` TR-4.2: 性能测试平均耗时<50ms
  - `programmatic` TR-4.3: 现有功能无回归
- **Notes**: 如有失败，迭代修复直至所有测试通过
