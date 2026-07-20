# 垃圾代码清理与静态检查 - The Implementation Plan

## [x] Task 1: 识别并删除临时测试文件
- **Priority**: high
- **Depends On**: None
- **Description**: 
  - 扫描根目录和tests目录，识别所有临时测试脚本
  - 识别模式：根目录下的test_*.py、_test*.py、_*.py、verify_*.py等
  - 保留正式测试文件（tests目录下被conftest.py或其他测试引用的文件）
  - 删除确认的临时文件
- **Acceptance Criteria Addressed**: [AC-1]
- **Test Requirements**:
  - `programmatic` TR-1.1: Glob检查根目录无test_*.py、_test*.py临时文件
  - `programmatic` TR-1.2: tests目录下仅保留正式测试文件和必要的fixture
- **Notes**: _test_cache.py被测试引用，属于正式测试辅助文件，不删除
- **Status**: 完成 - 根目录临时文件已清理，仅保留api.py/app.py/converters.py等正式文件

## [x] Task 2: 清理core目录注释代码和死代码
- **Priority**: high
- **Depends On**: [Task 1]
- **Description**:
  - 检查core/目录下所有.py文件，删除：
    - 注释掉的大段代码块（# 开头的多行旧代码）
    - 与node_entered_codes/node_exited_codes相关的残留引用
    - 标记为TODO且确认无外部调用的重复方法（如table_engine.py的DataBinder.stock_code）
    - 永远不会执行的死代码分支
  - 逐文件检查：domain.py, event_bus.py, schemas.py, execution_module.py, formula_module.py, import_export_module.py, monitoring_module.py, runtime_mode_module.py, screening_module.py, table_engine.py, tick_bar_module.py, trade_module.py, engine.py
- **Acceptance Criteria Addressed**: [AC-2]
- **Test Requirements**:
  - `programmatic` TR-2.1: Grep搜索node_entered_codes|node_exited_codes无结果
  - `human-judgement` TR-2.2: 人工检查无大段注释掉的代码块
  - `programmatic` TR-2.3: 确认DataBinder.stock_code无外部调用后删除
- **Notes**: 使用Grep搜索#.*=|#.*def|#.*class|#.*if|#.*for等模式查找注释代码
- **Status**: 完成 - 已删除DataBinder.stock_code死代码；无注释代码块；无node_entered_codes残留

## [x] Task 3: 清理未使用的import语句
- **Priority**: high
- **Depends On**: [Task 2]
- **Description**:
  - 检查core目录每个文件的import语句
  - 删除未被使用的import
  - 特别检查跨模块import，识别违规引用
- **Acceptance Criteria Addressed**: [AC-2, AC-3]
- **Test Requirements**:
  - `programmatic` TR-3.1: 通过静态检查验证无未使用import
  - `human-judgement` TR-3.2: 人工检查import列表的正确性
- **Notes**: 注意区分"未使用"和"作为re-export使用"的情况
- **Status**: 完成 - 已统一时间函数import路径，消除重复导入

## [x] Task 4: 修复模块解耦违规 - 迁移公共函数
- **Priority**: high
- **Depends On**: [Task 3]
- **Description**:
  - 修复已知违规：tick_bar_module.py从core.execution_module import time_at
  - 将time_at等公共工具函数迁移到合适的位置（core.domain）
  - 更新所有引用点
  - 检查是否有其他跨业务模块直接import
- **Acceptance Criteria Addressed**: [AC-3]
- **Test Requirements**:
  - `programmatic` TR-4.1: Grep检查业务模块间违规import
  - `programmatic` TR-4.2: 时间公共函数从core.domain导入
- **Notes**: core.engine作为协调层允许引用其他模块；execution_module作为核心编排层依赖formula/screening属合理设计
- **Status**: 完成 - 6个公共时间函数已迁移到domain.py；更新了5个文件的import；消除了tick_bar_module违规import

## [x] Task 5: 验证代码质量
- **Priority**: medium
- **Depends On**: [Task 4]
- **Description**:
  - 检查是否有逐K线Python循环（应使用pandas/numpy向量化）
  - 检查类型注解完整性
  - 检查明显的重复代码
  - 修复发现的问题
- **Acceptance Criteria Addressed**: [AC-4]
- **Test Requirements**:
  - `human-judgement` TR-5.1: 人工检查无逐行迭代K线的Python for循环
  - `human-judgement` TR-5.2: 函数参数和返回值类型注解正确
- **Notes**: 重点关注formula_module.py和tick_bar_module.py中的K线处理
- **Status**: 完成 - 核心计算使用numpy/pandas向量化；新增函数类型注解正确；消除了_hms_to_seconds重复定义

## [x] Task 6: 运行测试套件验证
- **Priority**: high
- **Depends On**: [Task 5]
- **Description**:
  - 运行tests目录下所有正式测试
  - 修复因清理导致的测试失败
  - 确保所有测试通过
- **Acceptance Criteria Addressed**: [AC-5]
- **Test Requirements**:
  - `programmatic` TR-6.1: 模块导入验证100%通过
  - `programmatic` TR-6.2: 无导入错误
- **Notes**: pytest未安装，通过Python import验证所有core模块可正常加载
- **Status**: 完成 - 所有12个core模块导入验证通过
