# 事件时间轴图形显示 + 代码清理优化 - Product Requirement Document

## Overview
- **Summary**: 在事件浮窗中添加横向时间轴图形可视化面板，支持缩放、拖拽、tooltip、过滤联动；同时对core/目录下超过500行的文件进行模块化拆分，确保每个文件≤500行，模块间仅通过EventBus通信解耦。
- **Purpose**: 提供更直观的事件时序可视化，便于调试和分析量化策略执行流程；改善代码可维护性，通过模块化拆分降低单文件复杂度。
- **Target Users**: 股票池平台开发者、量化交易策略调试人员、系统维护人员。

## Goals
- 事件面板添加可折叠的横向时间轴区域（位于事件列表上方）
- 时间轴深色背景，带时间刻度线
- 不同事件类型用对应颜色圆点/图标标记：
  * 📊 Tick = 蓝色 (#2196f3)
  * 📈 Bar = 绿色 (#4caf50)
  * 🧮 Formula = 青色 (#00bcd4)
  * ⚡ Edge = 橙色 (#ff9800)
  * 🔄 Transfer = 紫色 (#9c27b0)
  * 💰 Signal = 红色 (#f44336)
  * 📋 Order = 黄色 (#ffc107)
  * ⏰ TTL = 粉色 (#e91e63)
  * 🔧 System = 灰色 (#9e9e9e)
- 支持鼠标滚轮缩放时间范围
- 支持鼠标拖拽平移时间轴
- hover事件图标显示tooltip（时间、类型、代码、详情）
- 与现有分类过滤按钮联动（时间轴和列表同步过滤）
- 新事件到来时自动滚动到最新位置（可暂停）
- 保留原有事件列表功能
- 拆分core/中所有超过500行的文件，每个文件≤500行
- 模块解耦：模块间禁止直接import业务类，仅通过EventBus和Protocol接口通信
- 保持core/__init__.py导出兼容
- 所有拆分后import路径正确
- tests/verify_simulation_core.py全部12项PASS

## Non-Goals (Out of Scope)
- 不修改EventBus核心架构
- 不添加新的事件类型
- 不修改K线面板或其他非事件相关UI
- 不改变业务逻辑，仅做文件拆分
- 不修改仿真/实盘核心算法

## Background & Context
当前系统存在两个主要问题：
1. **UI层面**：事件面板只有列表形式，缺乏时序可视化，难以直观看到事件发生的时间分布和先后关系
2. **代码层面**：core/目录下多个文件超过500行（部分超过2000行），单文件复杂度高，维护困难，模块间耦合度较高

需要通过本次迭代同时解决UI可视化和代码架构问题。

## Functional Requirements

### UI: 时间轴可视化
- **FR-1**: 事件浮窗顶部添加可折叠的时间轴区域
- **FR-2**: 时间轴为深色背景，绘制垂直时间刻度线和时间标签
- **FR-3**: 事件类型对应颜色和图标（见Goals部分）
- **FR-4**: 事件在时间轴对应水平位置用圆点标记
- **FR-5**: 鼠标滚轮缩放：滚轮向上放大（时间范围变小），滚轮向下缩小（时间范围变大）
- **FR-6**: 鼠标拖拽平移：按住左键拖动可左右平移时间轴
- **FR-7**: hover事件标记点显示tooltip：显示格式化时间、事件类型、股票代码、详情摘要
- **FR-8**: 时间轴与事件列表过滤联动：点击分类过滤按钮时，时间轴和列表同步显示/隐藏对应类型
- **FR-9**: 自动滚动：新事件到来时自动滚动到最右侧（最新位置），提供"暂停自动滚动"开关
- **FR-10**: 折叠/展开：点击时间轴标题栏可折叠/展开时间轴区域

### 代码: 模块化拆分
- **FR-11**: domain.py(1403行) → 拆分为 domain_types.py（数据类/Protocol）、domain_utils.py（工具函数）
- **FR-12**: formula_module.py(2220行) → 拆分为 formula_operators.py（算子实现）、python_formula_engine.py（PythonFormulaEngine类）、formula_router.py（FormulaRouter类）
- **FR-13**: runtime_mode_module.py(2618行) → 拆分为 sim_tick_source.py（SimTickSource类）、sim_scheduler.py（SimulationScheduler类）、runtime_controller.py（RuntimeSimulator类）
- **FR-14**: execution_module.py(2594行) → 拆分为 edge_context.py（EdgeContext数据类）、filter_executor.py（FilterExecutor类）、ttl_manager.py（TTL管理）、edge_executor.py（EdgeExecutor精简版）
- **FR-15**: engine.py(1917行) → 拆分为 state_holder.py（GlobalState类）、pool_engine.py（PoolEngine精简版<500行）
- **FR-16**: table_engine.py(1455行) → 拆分为 table_loader.py（表加载）、table_registry.py（表注册查询）
- **FR-17**: monitoring_module.py(1345行) → 拆分为 event_recorder.py（事件记录）、sse_streamer.py（SSE推送）
- **FR-18**: 检查screening_module.py、tick_bar_module.py、trade_module.py、schemas.py，如接近或超过500行则拆分
- **FR-19**: 模块间禁止直接import业务类，仅允许from .event_bus import EventBus/Event 以及 Protocol接口
- **FR-20**: 保持所有功能不变，对外API兼容
- **FR-21**: 所有拆分后import路径正确更新

### 文档
- **FR-22**: 创建doc/ARCHITECTURE.md一页式架构文档：三层架构、模块职责表、事件流图、仿真/实盘统一说明

## Non-Functional Requirements
- **NFR-1**: 时间轴渲染性能：1000个事件标记点流畅显示
- **NFR-2**: 缩放/拖拽响应时间<50ms
- **NFR-3**: 代码拆分后不引入循环import
- **NFR-4**: 所有拆分文件≤500行
- **NFR-5**: 验证脚本12项全部PASS，无功能回退

## Constraints
- **Technical**: 前端使用原生Canvas或DOM绘制时间轴；后端Python保持现有架构
- **Business**: 必须保持事件格式和API完全兼容
- **Dependencies**: 依赖现有EventBus、SSE事件流、事件过滤机制

## Assumptions
- 现有事件面板CSS/JS基础结构可扩展
- 现有模块间主要通过EventBus通信，可进一步解耦
- 拆分文件后功能完全等价

## Acceptance Criteria

### AC-1: 时间轴显示正常
- **Given**: 事件面板已打开，SSE连接正常
- **When**: 收到事件流
- **Then**: 时间轴区域显示，事件按时间在对应位置用彩色圆点标记
- **Verification**: `human-judgment`

### AC-2: 缩放拖拽功能
- **Given**: 时间轴可见
- **When**: 滚轮缩放或拖拽平移
- **Then**: 时间范围正确缩放/平移，刻度标签更新
- **Verification**: `human-judgment`

### AC-3: Tooltip显示
- **Given**: 时间轴有事件标记
- **When**: hover在标记点上
- **Then**: 显示tooltip，包含时间、类型、代码、详情
- **Verification**: `human-judgment`

### AC-4: 过滤联动
- **Given**: 过滤按钮存在
- **When**: 点击某类型过滤按钮
- **Then**: 时间轴和列表同步隐藏/显示该类型事件
- **Verification**: `human-judgment`

### AC-5: 自动滚动暂停
- **Given**: 新事件持续到来
- **When**: 点击暂停/继续自动滚动
- **Then**: 暂停时停在当前位置，继续时自动跟随到最新
- **Verification**: `human-judgment`

### AC-6: 代码拆分完成
- **Given**: core/目录所有.py文件
- **When**: 统计行数
- **Then**: 每个文件≤500行，无循环import
- **Verification**: `programmatic`

### AC-7: 模块解耦
- **Given**: 拆分后的模块
- **When**: 检查import语句
- **Then**: 模块间仅通过event_bus和Protocol通信，无直接业务类import
- **Verification**: `programmatic`

### AC-8: 验证脚本全PASS
- **Given**: 运行tests/verify_simulation_core.py
- **When**: 仿真完成
- **Then**: 12项检查全部PASS
- **Verification**: `programmatic`

### AC-9: 架构文档完整
- **Given**: doc/ARCHITECTURE.md
- **When**: 阅读文档
- **Then**: 包含三层架构、模块职责、事件流图、仿真/实盘统一说明
- **Verification**: `human-judgment`
