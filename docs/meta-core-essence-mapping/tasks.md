# Meta Core 深度表驱动重构 — 实施计划

## 阶段一：清理死代码

### [ ] Task 1: 审计并删除死配置表
- **Priority**: high
- **Depends On**: None
- **Description**:
  - 对94张配置表做全代码库grep审计，确认哪些表确实从未被引用
  - 删除所有0引用的死配置表（约40张）
  - 更新 table_categories.json
  - 运行测试确保删除后不影响功能
- **Acceptance Criteria Addressed**: AC-1
- **Test Requirements**:
  - `programmatic` TR-1.1: 配置表数量从94张减少到约54张（删除40张死表）
  - `programmatic` TR-1.2: 所有现有测试100%通过
  - `programmatic` TR-1.3: table_categories.json与实际文件一致
  - `human-judgement` TR-1.4: 审查删除的表列表，确认没有误删正在使用的表
- **Notes**: 删除前必须做全代码库（含测试）的grep确认，用表名（不含.json后缀）搜索所有.py和.js文件

### [ ] Task 2: 删除未使用的死代码
- **Priority**: high
- **Depends On**: Task 1
- **Description**:
  - 审计engine.py、table_engine.py、builtins.py中未被调用的函数
  - 删除明显的死代码（已废弃的函数、未使用的导入、冗余的兼容层）
  - 整理代码结构，合并重复逻辑
- **Acceptance Criteria Addressed**: AC-2, AC-3
- **Test Requirements**:
  - `programmatic` TR-2.1: engine.py减少至少300行
  - `programmatic` TR-2.2: table_engine.py减少至少200行
  - `programmatic` TR-2.3: builtins.py减少至少200行
  - `programmatic` TR-2.4: 所有现有测试100%通过
- **Notes**: 保守删除，不确定的先保留，标记为TODO后续确认

---

## 阶段二：收敛配置表

### [ ] Task 3: 合并时机相关配置表
- **Priority**: high
- **Depends On**: Task 2
- **Description**:
  - 当前分散在timing.json、edge_strategies.json、runtime_modes.json中的时机相关配置
  - 合并为timing_operators.json（时机算子注册表）
  - 更新engine.py中相关代码，从新表读取
  - 保持外部行为完全不变
- **Acceptance Criteria Addressed**: AC-1, AC-4
- **Test Requirements**:
  - `programmatic` TR-3.1: 时机相关配置表从约5张合并到1张
  - `programmatic` TR-3.2: 所有时机相关测试通过
  - `programmatic` TR-3.3: gate评估功能完全不变
  - `human-judgement` TR-3.4: 新表结构清晰，易于理解

### [ ] Task 4: 合并筛选相关配置表
- **Priority**: high
- **Depends On**: Task 3
- **Description**:
  - 当前分散在dispatch.json、modules.json、tdx_enums.json中的筛选相关配置
  - 合并为filter_operators.json（筛选算子注册表）
  - 更新engine.py中相关代码，从新表读取
  - 保持外部行为完全不变
- **Acceptance Criteria Addressed**: AC-1, AC-4
- **Test Requirements**:
  - `programmatic` TR-4.1: 筛选相关配置表从约6张合并到1-2张
  - `programmatic` TR-4.2: 所有筛选相关测试通过
  - `programmatic` TR-4.3: filter功能完全不变
  - `human-judgement` TR-4.4: 新表结构清晰，易于理解

### [ ] Task 5: 合并流转和副作用相关配置表
- **Priority**: medium
- **Depends On**: Task 4
- **Description**:
  - 流转模式（flow_mode_registry.json + edge_strategies.json中的流转部分）→ flow_operators.json
  - 副作用动作（action_table.json + behavior_actions.json）→ action_operators.json
  - 更新相关代码
- **Acceptance Criteria Addressed**: AC-1, AC-4
- **Test Requirements**:
  - `programmatic` TR-5.1: 流转和动作相关表从约5张合并到2张
  - `programmatic` TR-5.2: 所有流转和动作测试通过
  - `human-judgement` TR-5.3: 新表结构清晰

### [ ] Task 6: 收敛其他配置表
- **Priority**: medium
- **Depends On**: Task 5
- **Description**:
  - 合并enums相关表（tdx_enums.json + dzh_type_map.json等）→ enums.json
  - 合并defaults相关表（defaults.json +各种默认值散落的配置）→ defaults.json
  - 合并market相关表 → market_calendar.json
  - 总配置表数量控制在约15张
- **Acceptance Criteria Addressed**: AC-1
- **Test Requirements**:
  - `programmatic` TR-6.1: 总配置表数量 <= 20张（阶段二结束时）
  - `programmatic` TR-6.2: 所有测试通过
  - `human-judgement` TR-6.3: 表分类清晰，命名一致

---

## 阶段三：简化引擎核心

### [ ] Task 7: 消除gate评估的过度抽象
- **Priority**: high
- **Depends On**: Task 3
- **Description**:
  - 当前路径：_dispatch_gate -> _dispatch_evaluator -> _gate_eval_xxx -> _calc_offset -> ...
  - 简化为：timing_ops[op_type](edge, now) -> 直接返回布尔值
  - 删除中间层：_dispatch_evaluator、_extract_prim_params_table等
  - 保持功能完全一致
- **Acceptance Criteria Addressed**: AC-2, AC-5, AC-6
- **Test Requirements**:
  - `programmatic` TR-7.1: gate相关代码从约500行减少到约150行
  - `programmatic` TR-7.2: gate调用深度从5层降到2层
  - `programmatic` TR-7.3: 所有时机相关测试通过
  - `human-judgement` TR-7.4: 代码逻辑清晰，一眼能看懂

### [ ] Task 8: 消除filter评估的过度抽象
- **Priority**: high
- **Depends On**: Task 4, Task 7
- **Description**:
  - 当前路径：_dispatch_filter -> _eval_primitive -> _extract_prim_params_table -> ...
  - 简化为：filter_ops[op_type](edge, stocks) -> 直接返回通过的股票集合
  - 删除中间层：_eval_primitive、_extract_single_param、_extract_threshold等
  - 保持功能完全一致
- **Acceptance Criteria Addressed**: AC-2, AC-5, AC-6
- **Test Requirements**:
  - `programmatic` TR-8.1: filter相关代码从约800行减少到约250行
  - `programmatic` TR-8.2: filter调用深度从6层降到2层
  - `programmatic` TR-8.3: 所有筛选相关测试通过
  - `human-judgement` TR-8.4: 代码逻辑清晰

### [ ] Task 9: 统一边执行流水线
- **Priority**: high
- **Depends On**: Task 7, Task 8
- **Description**:
  - 当前：_process_edge_pipeline -> _phase_dispatch -> _phase_xxx（十几个phase函数）
  - 简化为：一条边的执行 = 时机检查 -> 筛选 -> 流转 -> 副作用 -> TTL
  - 5个步骤直接按顺序调用，不需要phase表和dispatch
  - engine.py核心tick循环重写
- **Acceptance Criteria Addressed**: AC-2, AC-3, AC-5, AC-6
- **Test Requirements**:
  - `programmatic` TR-9.1: engine.py <= 1500行（阶段三结束时）
  - `programmatic` TR-9.2: 边执行调用深度 <= 3层
  - `programmatic` TR-9.3: 所有测试通过
  - `human-judgement` TR-9.4: 核心tick循环逻辑清晰，200行内能看懂

### [ ] Task 10: 简化flow和TTL逻辑
- **Priority**: medium
- **Depends On**: Task 9
- **Description**:
  - flow：合并propagate相关的多个函数，简化流转逻辑
  - TTL：合并duration和ttl相关的多个函数
  - 两者都走统一的算子注册模式
- **Acceptance Criteria Addressed**: AC-2, AC-3
- **Test Requirements**:
  - `programmatic` TR-10.1: flow和TTL代码各减少50%以上
  - `programmatic` TR-10.2: 所有流转和TTL测试通过
  - `human-judgement` TR-10.3: 代码结构清晰

---

## 阶段四：精简表引擎

### [ ] Task 11: 精简table_engine.py
- **Priority**: medium
- **Depends On**: Task 6
- **Description**:
  - 当前1091行，目标 <= 300行
  - 保留核心功能：配置加载、缓存、校验、热加载
  - 删除过度设计的元数据层、分类系统（保留但简化）
  - 删除DataBinder等过度抽象
- **Acceptance Criteria Addressed**: AC-2, AC-3
- **Test Requirements**:
  - `programmatic` TR-11.1: table_engine.py <= 400行
  - `programmatic` TR-11.2: 所有配置加载和热加载测试通过
  - `human-judgement` TR-11.3: 核心逻辑清晰易懂

### [ ] Task 12: 精简builtins.py
- **Priority**: medium
- **Depends On**: Task 10
- **Description**:
  - 当前1462行，目标 <= 600行
  - 把真正的算子函数保留，删除各种dispatch和wrapper
  - 合并重复的工具函数
  - 与engine.py的职责清晰划分
- **Acceptance Criteria Addressed**: AC-3
- **Test Requirements**:
  - `programmatic` TR-12.1: builtins.py <= 700行
  - `programmatic` TR-12.2: 所有测试通过
  - `human-judgement` TR-12.3: 职责清晰，没有重复代码

---

## 阶段五：整理与验证

### [ ] Task 13: 最终代码整理
- **Priority**: medium
- **Depends On**: Task 11, Task 12
- **Description**:
  - 整理代码结构，确保一致性
  - 更新注释和文档
  - 确保命名规范统一
  - 核心代码总量达到目标
- **Acceptance Criteria Addressed**: AC-2, AC-3, AC-6
- **Test Requirements**:
  - `programmatic` TR-13.1: engine.py <= 1000行
  - `programmatic` TR-13.2: 核心代码(engine+table_engine+builtins) <= 1900行
  - `programmatic` TR-13.3: 配置表 <= 15张
  - `human-judgement` TR-13.4: 代码结构清晰，易于理解

### [ ] Task 14: 完整测试与性能验证
- **Priority**: high
- **Depends On**: Task 13
- **Description**:
  - 运行完整测试套件
  - 性能基准测试（与重构前对比）
  - 修复发现的所有问题
- **Acceptance Criteria Addressed**: AC-4
- **Test Requirements**:
  - `programmatic` TR-14.1: 所有现有测试100%通过
  - `programmatic` TR-14.2: 单次tick执行时间不增加（或有所减少）
  - `programmatic` TR-14.3: 内存占用不增加
  - `human-judgement` TR-14.4: 人工走查关键路径，确认逻辑正确

---

## 任务依赖关系图

```
阶段一: Task 1 -> Task 2

阶段二: Task 2 -> Task 3 -> Task 4 -> Task 5 -> Task 6
                        \
阶段三:                Task 3 -> Task 7 -> Task 8 -> Task 9 -> Task 10
                                  Task 4 ----^

阶段四: Task 6 -> Task 11
         Task 10 -> Task 12

阶段五: Task 11 + Task 12 -> Task 13 -> Task 14
```

**关键路径**: Task 1 -> 2 -> 3 -> 7 -> 8 -> 9 -> 10 -> 12 -> 13 -> 14
