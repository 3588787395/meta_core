# Tasks - 事件时间轴图形显示 + 代码清理优化

## Phase 1: 规格文档和设计
- [x] Task 1.1: 创建 spec.md 需求文档
- [ ] Task 1.2: 创建 tasks.md 任务清单
- [ ] Task 1.3: 创建 checklist.md 验收清单
- [ ] Task 1.4: 创建 doc/ARCHITECTURE.md 架构设计文档

## Phase 2: 时间轴UI实现
- [ ] Task 2.1: 修改 web/index.html 添加时间轴容器HTML结构
  - 时间轴折叠/展开头部
  - Canvas画布容器
  - Tooltip元素
  - 自动滚动暂停按钮
- [ ] Task 2.2: 修改 web/css/styles.css 添加时间轴样式
  - 时间轴容器深色背景
  - 折叠/展开按钮样式
  - 时间轴刻度样式
  - Tooltip样式
  - 控制按钮样式
- [ ] Task 2.3: 修改 web/js/ui.js 实现时间轴逻辑
  - Timeline类封装（初始化、渲染、事件处理）
  - 时间刻度计算和绘制
  - 事件标记点绘制（按类型着色）
  - 鼠标滚轮缩放
  - 鼠标拖拽平移
  - Tooltip显示/隐藏
  - 与过滤按钮联动
  - 自动滚动到最新/暂停
  - 折叠/展开切换
  - 新事件到来时更新时间轴

## Phase 3: 后端文件拆分（按顺序避免循环依赖）
注意：拆分顺序从底层依赖到上层模块

### 3.1 基础层拆分
- [ ] Task 3.1.1: 拆分 domain.py
  - 创建 core/domain_types.py（数据类、Protocol定义）
  - 创建 core/domain_utils.py（工具函数 _normalize_to_fz, _hash_tick 等）
  - 更新 domain.py 为从新模块导入并重新导出
- [ ] Task 3.1.2: 检查并拆分 schemas.py(1029行 → api_schemas.py)

### 3.2 引擎层拆分
- [ ] Task 3.2.1: 拆分 table_engine.py
  - 创建 core/table_loader.py（表加载逻辑）
  - 创建 core/table_registry.py（表注册查询）
  - 更新 table_engine.py
- [ ] Task 3.2.2: 拆分 monitoring_module.py
  - 创建 core/event_recorder.py（事件记录）
  - 创建 core/sse_streamer.py（SSE推送）
  - 更新 monitoring_module.py
- [ ] Task 3.2.3: 拆分 formula_module.py
  - 创建 core/formula_operators.py（cross/ma/ema/macd/kdj等算子）
  - 创建 core/python_formula_engine.py（PythonFormulaEngine类）
  - 创建 core/formula_router.py（FormulaRouter类）
  - 更新 formula_module.py
- [ ] Task 3.2.4: 拆分 engine.py
  - 创建 core/state_holder.py（GlobalState类）
  - 创建 core/pool_engine.py（PoolEngine精简版<500行）
  - 更新 engine.py

### 3.3 执行层拆分
- [ ] Task 3.3.1: 拆分 execution_module.py
  - 创建 core/edge_context.py（EdgeContext数据类）
  - 创建 core/filter_executor.py（FilterExecutor类）
  - 创建 core/ttl_manager.py（TTL管理逻辑）
  - 创建 core/edge_executor.py（EdgeExecutor主类精简）
  - 更新 execution_module.py
- [ ] Task 3.3.2: 拆分 runtime_mode_module.py
  - 创建 core/sim_tick_source.py（SimTickSource类）
  - 创建 core/sim_scheduler.py（SimulationScheduler类）
  - 创建 core/runtime_controller.py（RuntimeSimulator类）
  - 更新 runtime_mode_module.py

### 3.4 检查其他文件
- [ ] Task 3.4.1: 检查 screening_module.py(46KB≈900行)，如需要则拆分
- [ ] Task 3.4.2: 检查 tick_bar_module.py(45KB≈900行)，如需要则拆分
- [ ] Task 3.4.3: 检查 trade_module.py(49KB≈1000行)，如需要则拆分
- [ ] Task 3.4.4: 更新所有模块内import路径
- [ ] Task 3.4.5: 确保 core/__init__.py 导出兼容（如需要）

## Phase 4: 验证
- [ ] Task 4.1: 运行 tests/verify_simulation_core.py
- [ ] Task 4.2: 修复所有验证失败项，确保12项PASS
- [ ] Task 4.3: 检查所有core/文件行数≤500行
- [ ] Task 4.4: 检查模块间import解耦（仅EventBus和Protocol）
- [ ] Task 4.5: 启动web服务器
- [ ] Task 4.6: 浏览器验证时间轴UI功能：
  - 时间轴显示正常
  - 缩放拖拽工作
  - Tooltip显示正确
  - 过滤联动正常
  - 自动滚动/暂停工作
  - 折叠/展开工作
