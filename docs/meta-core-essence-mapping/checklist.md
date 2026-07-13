# Meta Core 深度表驱动重构 — 验证检查清单

## 阶段一：清理死代码

### Task 1: 审计并删除死配置表
- [ ] 对94张配置表做了全代码库（.py + .js）的grep审计
- [ ] 确认删除的表确实0引用（含测试文件）
- [ ] 删除了所有确认的死配置表（约40张）
- [ ] 配置表数量从94张减少到约54张
- [ ] table_categories.json已更新，与实际文件一致
- [ ] 所有现有测试100%通过

### Task 2: 删除未使用的死代码
- [ ] 审计了engine.py中所有函数的调用情况
- [ ] 审计了table_engine.py中所有函数的调用情况
- [ ] 审计了builtins.py中所有函数的调用情况
- [ ] 删除了确认未使用的死代码（废弃函数、未使用导入、冗余兼容层）
- [ ] engine.py减少至少300行
- [ ] table_engine.py减少至少200行
- [ ] builtins.py减少至少200行
- [ ] 所有现有测试100%通过

---

## 阶段二：收敛配置表

### Task 3: 合并时机相关配置表
- [ ] 识别了所有时机相关的配置表（timing, gate_types, runtime_modes等）
- [ ] 合并为timing_operators.json（时机算子注册表）
- [ ] engine.py中gate/duration相关代码已更新为从新表读取
- [ ] 时机相关配置表从约5张合并到1张
- [ ] gate评估功能完全不变（行为一致）
- [ ] 所有时机相关测试通过
- [ ] 新表结构清晰，命名一致

### Task 4: 合并筛选相关配置表
- [ ] 识别了所有筛选相关的配置表（dispatch, modules, tdx_enums等）
- [ ] 合并为filter_operators.json（筛选算子注册表）
- [ ] engine.py中filter相关代码已更新为从新表读取
- [ ] 筛选相关配置表从约6张合并到1-2张
- [ ] filter功能完全不变（行为一致）
- [ ] 所有筛选相关测试通过
- [ ] 新表结构清晰，命名一致

### Task 5: 合并流转和副作用相关配置表
- [ ] 流转模式合并为flow_operators.json
- [ ] 副作用动作合并为action_operators.json
- [ ] 相关代码已更新为从新表读取
- [ ] 流转和动作相关表从约5张合并到2张
- [ ] 所有流转和动作测试通过
- [ ] 新表结构清晰

### Task 6: 收敛其他配置表
- [ ] enums相关表合并为enums.json
- [ ] defaults相关表合并为defaults.json
- [ ] market相关表合并为market_calendar.json
- [ ] 总配置表数量 <= 20张（阶段二结束时）
- [ ] 所有测试通过
- [ ] 表分类清晰，命名一致
- [ ] table_categories.json已更新

---

## 阶段三：简化引擎核心

### Task 7: 消除gate评估的过度抽象
- [ ] 删除了_dispatch_evaluator中间层
- [ ] 删除了_extract_prim_params_table等参数提取中间层
- [ ] gate评估简化为：timing_ops[op_type](edge, now) -> bool
- [ ] gate相关代码从约500行减少到约150行
- [ ] gate调用深度从5层降到2层
- [ ] 所有时机相关测试通过
- [ ] 代码逻辑清晰，一眼能看懂

### Task 8: 消除filter评估的过度抽象
- [ ] 删除了_eval_primitive中间层
- [ ] 删除了_extract_single_param、_extract_threshold等中间层
- [ ] filter评估简化为：filter_ops[op_type](edge, stocks) -> Set[Stock]
- [ ] filter相关代码从约800行减少到约250行
- [ ] filter调用深度从6层降到2层
- [ ] 所有筛选相关测试通过
- [ ] 代码逻辑清晰

### Task 9: 统一边执行流水线
- [ ] 删除了_process_edge_pipeline的phase表驱动模式
- [ ] 删除了_phase_dispatch和十几个_phase_xxx函数
- [ ] 边执行简化为5步：时机检查 -> 筛选 -> 流转 -> 副作用 -> TTL
- [ ] 5个步骤直接按顺序调用，不需要phase表和dispatch
- [ ] engine.py核心tick循环已重写
- [ ] engine.py <= 1500行（阶段三结束时）
- [ ] 边执行调用深度 <= 3层
- [ ] 所有测试通过
- [ ] 核心tick循环逻辑清晰，200行内能看懂

### Task 10: 简化flow和TTL逻辑
- [ ] flow：合并了propagate相关的多个函数
- [ ] TTL：合并了duration和ttl相关的多个函数
- [ ] 两者都走统一的算子注册模式
- [ ] flow代码减少50%以上
- [ ] TTL代码减少50%以上
- [ ] 所有流转和TTL测试通过
- [ ] 代码结构清晰

---

## 阶段四：精简表引擎

### Task 11: 精简table_engine.py
- [ ] 保留了核心功能：配置加载、缓存、校验、热加载
- [ ] 删除了过度设计的元数据层
- [ ] 简化了分类系统
- [ ] 删除了DataBinder等过度抽象
- [ ] table_engine.py <= 400行
- [ ] 所有配置加载和热加载测试通过
- [ ] 核心逻辑清晰易懂

### Task 12: 精简builtins.py
- [ ] 保留了真正的算子函数
- [ ] 删除了各种dispatch和wrapper函数
- [ ] 合并了重复的工具函数
- [ ] 与engine.py的职责清晰划分
- [ ] builtins.py <= 700行
- [ ] 所有测试通过
- [ ] 职责清晰，没有重复代码

---

## 阶段五：整理与验证

### Task 13: 最终代码整理
- [ ] 代码结构整理完成，一致性良好
- [ ] 注释和文档已更新
- [ ] 命名规范统一
- [ ] engine.py <= 1000行
- [ ] 核心代码(engine+table_engine+builtins) <= 1900行
- [ ] 配置表 <= 15张
- [ ] 代码结构清晰，易于理解

### Task 14: 完整测试与性能验证
- [ ] 完整测试套件运行通过（100% pass）
- [ ] 性能基准测试已完成
- [ ] 单次tick执行时间不增加（或有所减少）
- [ ] 内存占用不增加
- [ ] 人工走查关键路径，确认逻辑正确
- [ ] 发现的所有问题已修复

---

## 最终验收标准

### 量化指标
- [ ] 配置表数量 <= 15张（从94张减少84%）
- [ ] engine.py <= 1000行（从3369行减少70%）
- [ ] table_engine.py <= 300行（从1091行减少72%）
- [ ] builtins.py <= 600行（从1462行减少59%）
- [ ] 核心代码合计 <= 1900行（从~5900行减少68%）
- [ ] 边执行调用深度 <= 3层（从7-8层减少60%）
- [ ] 所有测试100%通过

### 质量指标
- [ ] 外部API完全不变（pool_api, system_api）
- [ ] 前端协议完全不变（WebSocket消息格式）
- [ ] DZH/TDX模板导入功能完全不变
- [ ] 三种运行模式（实盘/回放/仿真）完全不变
- [ ] 性能不下降（或有所提升）
- [ ] 代码结构清晰，新开发者30分钟内能理解核心模型
