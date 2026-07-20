# Per-code公式缓存与增量筛选 - Verification Checklist

## 公式缓存验证
- [ ] _hash_code_bars函数对相同K线数据返回相同哈希值
- [ ] _hash_code_bars函数对不同K线数据返回不同哈希值
- [ ] _cached_eval使用per-code缓存key: ("formula_per_code", ctx.mode, formula_ref, code, period)
- [ ] 缓存值存储为(code_bar_hash, value)元组
- [ ] 单只股票K线变化仅失效该股票对应公式缓存
- [ ] 其他未变化股票缓存保持有效，不重新计算

## 增量筛选逻辑验证
- [ ] changed_codes=None时，对所有源池股票进行全量评估
- [ ] changed_codes=[]且有缓存时，直接返回cached_passed，不调用公式求值
- [ ] changed_codes=[]但无缓存时，执行全量评估
- [ ] changed_codes非空时，仅对changed_codes ∩ 源池股票重新评估
- [ ] 增量合并公式正确：passed_set = (cached_passed - changed_set) | newly_passed
- [ ] 首次运行（无缓存）时正确执行全量计算
- [ ] state.filter_inputs[eid]存储为frozenset类型

## 功能正确性验证
- [ ] 100只股票中2只Tick变化，仅这2只重新计算公式
- [ ] 未变化的98只股票结果与上一次完全一致
- [ ] 新增通过阈值的股票正确进入passed_set
- [ ] 不再通过阈值的股票正确从passed_set移除
- [ ] 增量筛选结果与全量计算结果完全一致

## 性能验证
- [ ] 100只股票增量筛选（2只变化）平均耗时<50ms
- [ ] 50次增量筛选平均耗时达标

## 测试覆盖验证
- [ ] test_per_code_cache测试通过
- [ ] test_filter_inputs_storage测试通过
- [ ] test_first_run_no_cache测试通过
- [ ] test_empty_changed_codes测试通过
- [ ] test_full_vs_incremental测试通过
- [ ] test_cache_consistency测试通过
- [ ] test_incremental_merge_correctness测试通过
- [ ] test_performance测试通过
