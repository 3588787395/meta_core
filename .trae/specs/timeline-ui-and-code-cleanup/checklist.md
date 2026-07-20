# Checklist - 事件时间轴图形显示 + 代码清理优化

## 文档检查
- [ ] spec.md 已创建，包含完整需求
- [ ] tasks.md 已创建，任务分解明确
- [ ] checklist.md 已创建（本文件）
- [ ] doc/ARCHITECTURE.md 已创建，包含：
  - [ ] 三层架构说明（事件驱动/时间驱动/表驱动）
  - [ ] 模块职责表
  - [ ] 事件流图（Tick→Bar→Formula→Edge→Transfer→Signal→Order）
  - [ ] 仿真/实盘统一说明

## UI检查：时间轴功能
- [ ] 时间轴区域显示在事件列表上方
- [ ] 时间轴可折叠/展开（点击标题栏）
- [ ] 深色背景，垂直时间刻度线和时间标签
- [ ] 事件类型对应颜色正确：
  - [ ] 📊 Tick = 蓝色
  - [ ] 📈 Bar = 绿色
  - [ ] 🧮 Formula = 青色
  - [ ] ⚡ Edge = 橙色
  - [ ] 🔄 Transfer = 紫色
  - [ ] 💰 Signal = 红色
  - [ ] 📋 Order = 黄色
  - [ ] ⏰ TTL = 粉色
  - [ ] 🔧 System = 灰色
- [ ] 事件在时间轴对应水平位置显示为圆点
- [ ] 鼠标滚轮缩放功能正常
- [ ] 鼠标拖拽平移功能正常
- [ ] hover事件标记点显示Tooltip：
  - [ ] 显示格式化时间
  - [ ] 显示事件类型
  - [ ] 显示股票代码
  - [ ] 显示详情摘要
- [ ] 与分类过滤按钮联动：
  - [ ] 取消勾选某类型，时间轴对应点隐藏
  - [ ] 勾选某类型，时间轴对应点显示
  - [ ] 列表同步显示/隐藏
- [ ] 自动滚动功能：
  - [ ] 新事件到来时自动滚动到最右侧
  - [ ] 有"暂停/继续自动滚动"按钮
  - [ ] 暂停时停在当前位置
  - [ ] 继续时恢复自动跟随
- [ ] 原有事件列表功能保留正常

## 代码拆分检查
- [ ] domain.py 拆分完成：
  - [ ] domain_types.py（数据类/Protocol）
  - [ ] domain_utils.py（工具函数）
  - [ ] 行数均≤500
- [ ] formula_module.py 拆分完成：
  - [ ] formula_operators.py（算子实现）
  - [ ] python_formula_engine.py（PythonFormulaEngine）
  - [ ] formula_router.py（FormulaRouter）
  - [ ] 行数均≤500
- [ ] runtime_mode_module.py 拆分完成：
  - [ ] sim_tick_source.py（SimTickSource）
  - [ ] sim_scheduler.py（SimulationScheduler）
  - [ ] runtime_controller.py（RuntimeSimulator）
  - [ ] 行数均≤500
- [ ] execution_module.py 拆分完成：
  - [ ] edge_context.py（EdgeContext）
  - [ ] filter_executor.py（FilterExecutor）
  - [ ] ttl_manager.py（TTL管理）
  - [ ] edge_executor.py（EdgeExecutor精简）
  - [ ] 行数均≤500
- [ ] engine.py 拆分完成：
  - [ ] state_holder.py（GlobalState）
  - [ ] pool_engine.py（PoolEngine精简<500行）
  - [ ] 行数均≤500
- [ ] table_engine.py 拆分完成：
  - [ ] table_loader.py（表加载）
  - [ ] table_registry.py（表注册查询）
  - [ ] 行数均≤500
- [ ] monitoring_module.py 拆分完成：
  - [ ] event_recorder.py（事件记录）
  - [ ] sse_streamer.py（SSE推送）
  - [ ] 行数均≤500
- [ ] 其他文件检查：
  - [ ] screening_module.py ≤500行或已拆分
  - [ ] tick_bar_module.py ≤500行或已拆分
  - [ ] trade_module.py ≤500行或已拆分
  - [ ] schemas.py ≤500行或已拆分为api_schemas.py

## 代码质量检查
- [ ] 所有core/下.py文件行数≤500
- [ ] 模块间无循环import
- [ ] 模块间仅通过EventBus/Event和Protocol通信
- [ ] 无直接import其他模块的业务类
- [ ] 所有import路径正确更新
- [ ] core/__init__.py导出保持兼容
- [ ] 功能无回退，业务逻辑不变

## 验证测试检查
- [ ] tests/verify_simulation_core.py 运行成功
- [ ] a_source_pool_count: PASS
- [ ] b_tick_received: PASS
- [ ] c_bars_composed: PASS
- [ ] d_unclosed_bar: PASS
- [ ] e_formula_evaluated: PASS
- [ ] f_pool_a: PASS（或warn）
- [ ] g_pool_b: PASS（或warn）
- [ ] h_pool_c: PASS（或warn）
- [ ] i_buy_signal: PASS（或warn）
- [ ] j_event_chain: PASS
- [ ] k_code_format: PASS
- [ ] l_tick_interval: PASS
- [ ] 总计12项，核心项全部PASS
- [ ] web服务器启动成功
- [ ] 浏览器访问时间轴UI显示正常
