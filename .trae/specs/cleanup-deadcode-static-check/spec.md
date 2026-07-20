# 垃圾代码清理与静态检查 - Product Requirement Document

## Overview
- **Summary**: 对股票池模拟系统进行垃圾代码清理和静态检查，包括删除临时测试文件、清理core目录下的注释代码/未使用import/死代码、验证模块解耦（禁止跨模块直接引用）、确保代码质量（向量化计算、类型注解），并运行测试套件验证功能未被破坏。
- **Purpose**: 提高代码库整洁度，消除技术债务，确保模块间严格通过EventBus解耦，保持代码可维护性。
- **Target Users**: 项目开发和维护人员。

## Goals
- 删除所有临时测试脚本，保留正式测试文件
- 清理core目录下所有注释掉的代码块、未使用import、未使用变量/函数、死代码
- 确保模块间仅通过EventBus交互，无违规跨模块import
- 验证代码质量：numpy/pandas向量化、无逐K线循环、类型注解完整
- 所有现有测试通过，功能未被破坏

## Non-Goals (Out of Scope)
- 不进行功能重构或架构变更
- 不添加新功能
- 不修改测试用例的测试逻辑（仅删除临时脚本）
- 不修复与清理任务无关的bug

## Background & Context
项目是基于事件驱动架构的股票池模拟系统，之前的多轮迭代留下了一些临时测试脚本和可能的死代码。根据项目记忆中的硬约束：
- 所有模块之间禁止相互引用，只准与事件引擎交互
- 核心循环需通过EventBus实现，避免直接模块调用
- 禁止跨层引用，仅允许包内白名单import
- 代码应保持高内聚低耦合

## Functional Requirements
- **FR-1**: 删除根目录和tests目录下所有临时测试脚本（如test_*.py、_test*.py、_*.py等临时验证脚本），保留正式测试文件
- **FR-2**: 清理core目录下所有文件中的：
  - 注释掉的大段旧代码（# 开头的废弃代码块）
  - 未使用的import语句
  - 未使用的变量和函数
  - 永远不会执行的死代码分支
  - 与node_entered_codes/node_exited_codes相关的残留引用
- **FR-3**: 模块解耦静态检查：
  - 允许的import：标准库、同模块内部类/函数、core.event_bus中的事件类、core.domain中的领域对象
  - 禁止的import：跨业务模块直接引用（如formula_module import execution_module的业务类，tick_bar_module import execution_module的函数）
  - 修复所有发现的违规import
- **FR-4**: 代码质量验证：
  - numpy/pandas向量化计算，无逐K线Python循环
  - 无明显重复代码
  - 修改后的函数有正确的类型注解
- **FR-5**: 运行tests目录下所有正式测试，确保全部通过

## Non-Functional Requirements
- **NFR-1**: 清理后代码行数应减少，无冗余
- **NFR-2**: 静态检查无违规import
- **NFR-3**: 测试通过率100%

## Constraints
- **Technical**: Python 3.x，使用现有项目依赖，不引入新依赖
- **Business**: 不破坏现有功能，不改变用户可见行为
- **Dependencies**: 依赖现有的EventBus架构和领域模型

## Assumptions
- tests目录下以test_开头且在测试套件中被引用的为正式测试文件
- 以_开头的测试文件、临时验证脚本可以安全删除
- core.domain和core.event_bus是允许被其他core模块import的公共基础模块
- 标记为TODO且确认无外部调用的重复代码可以删除

## Acceptance Criteria

### AC-1: 临时文件已删除
- **Given**: 项目根目录和tests目录
- **When**: 执行清理
- **Then**: 所有临时测试脚本（如之前提到的test_cross_op.py、test_dirty_state_refactor.py、_test_sse.py等）被删除，正式测试文件保留
- **Verification**: `programmatic`
- **Notes**: 通过Glob检查确认无遗留临时文件

### AC-2: core目录无垃圾代码
- **Given**: core目录下所有.py文件
- **When**: 执行清理
- **Then**: 无注释掉的大段旧代码、无未使用import、无未使用变量/函数、无死代码、无node_entered_codes/node_exited_codes残留
- **Verification**: `programmatic` + `human-judgment`
- **Notes**: 通过Grep搜索注释代码模式、pyflakes/静态检查验证

### AC-3: 模块解耦无违规
- **Given**: core目录所有模块
- **When**: 检查import语句
- **Then**: 除core.event_bus和core.domain外，业务模块间无直接import
- **Verification**: `programmatic`
- **Notes**: tick_bar_module从execution_module import time_at是已知违规，需要修复（将time_at移到domain或公共工具位置）

### AC-4: 代码质量达标
- **Given**: 所有core模块代码
- **When**: 检查代码
- **Then**: numpy/pandas向量化、类型注解正确
- **Verification**: `human-judgment`

### AC-5: 所有测试通过
- **Given**: 清理后的代码库
- **When**: 运行pytest测试套件
- **Then**: tests目录下所有正式测试100%通过
- **Verification**: `programmatic`

## Open Questions
- [ ] time_at函数应该移到哪个位置？core.domain还是单独的core._utils？
