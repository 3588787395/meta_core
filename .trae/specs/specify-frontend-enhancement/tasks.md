# 前端股票池界面完善 - 实施计划

## [/] Task 1: 画布引擎完善

* **Priority**: high

* **Depends On**: None

* **Description**:
  * 完善画布节点渲染：备选池、转移条件、状态池、丢弃池、文字标签
  * 实现拖拽移动节点
  * 实现缩放和适应画布功能
  * 实现三种线形：贝兹曲线、横竖折线、直线
  * 实现连线模式创建边
  * 实现迷你地图导航功能

* **Acceptance Criteria Addressed**: AC-1

* **Test Requirements**:
  * `programmatic` TR-1.1: 画布正确渲染示例池拓扑（source→cond1→pool_A→cond3→pool_C, source→cond2→pool_B→cond3）
  * `programmatic` TR-1.2: 节点可拖拽移动，画布缩放正常
  * `programmatic` TR-1.3: 三种线形切换正确（贝兹曲线/横竖折线/直线）
  * `human-judgement` TR-1.4: 连线模式操作流畅，迷你地图导航正常

* **Notes**: 参考 `web/js/canvas.js` 现有实现，完善缺失功能

## [ ] Task 2: 节点与边管理功能

* **Priority**: high

* **Depends On**: Task 1

* **Description**:
  * 实现节点添加（备选池、转移条件、状态池、丢弃池、文字标签）
  * 实现节点删除（含确认对话框）
  * 实现复制/剪切/粘贴节点
  * 实现节点层级调整（置于顶层/底层）
  * 实现边创建、删除、属性设置
  * 实现边顺序号管理

* **Acceptance Criteria Addressed**: AC-1

* **Test Requirements**:
  * `programmatic` TR-2.1: 所有节点类型可正确添加到画布
  * `programmatic` TR-2.2: 删除节点时弹出确认对话框
  * `programmatic` TR-2.3: 复制/剪切/粘贴操作正常，位置偏移正确
  * `human-judgement` TR-2.4: 边属性设置正确，执行顺序显示正常

* **Notes**: 参考 `web/js/app.js` 中 PoolDataManager 的操作方法

## [ ] Task 3: 属性面板完善

* **Priority**: high

* **Depends On**: Task 1, Task 2

* **Description**:
  * 完善表驱动属性面板，支持所有节点类型
  * 实现字段联动（depends_on/active_when）
  * 实现实时校验并显示错误信息
  * 确保配置项与后端配置表同步
  * 实现DZH颜色值可视化显示

* **Acceptance Criteria Addressed**: AC-1

* **Test Requirements**:
  * `programmatic` TR-3.1: 选中节点时属性面板正确显示配置项
  * `programmatic` TR-3.2: 选中边时属性面板正确显示配置项
  * `programmatic` TR-3.3: 字段联动逻辑正确（depends_on/active_when）
  * `human-judgement` TR-3.4: 校验错误提示清晰，颜色值可视化正确

* **Notes**: 基于 `web/js/ui.js` 中的 TableDrivenPanel 和 ComponentRegistry

## [ ] Task 4: 综合设置窗口完善

* **Priority**: high

* **Depends On**: Task 3

* **Description**:
  * 完善综合设置窗口表格渲染
  * 实现三列布局：流程标识、条件/属性、时序/操作
  * 实现双击字段弹出字段编辑器
  * 实现批量编辑和快速配置

* **Acceptance Criteria Addressed**: AC-1

* **Test Requirements**:
  * `programmatic` TR-4.1: 综合设置窗口正确显示所有流程
  * `programmatic` TR-4.2: 三列布局正确（流程标识、条件/属性、时序/操作）
  * `human-judgement` TR-4.3: 字段编辑器功能完整，批量编辑便捷

* **Notes**: 参考 `web/index.html` 中的综合设置窗口结构

## [ ] Task 5: 运行模式切换完善

* **Priority**: high

* **Depends On**: None

* **Description**:
  * 完善四种模式切换逻辑：设计、实盘、回放、仿真
  * 实现模式指示器更新
  * 实现模式切换时控制面板显示/隐藏
  * 实现运行时禁止模式切换

* **Acceptance Criteria Addressed**: AC-2

* **Test Requirements**:
  * `programmatic` TR-5.1: 模式切换时模式指示器正确更新
  * `programmatic` TR-5.2: 各模式对应的控制面板正确显示/隐藏
  * `human-judgement` TR-5.3: 运行中切换模式被正确阻止

* **Notes**: 参考 `web/js/app.js` 中的模式切换逻辑

## [ ] Task 6: 仿真模式面板完善

* **Priority**: high

* **Depends On**: Task 5

* **Description**:
  * 完善仿真控制面板：虚拟时钟、步数、启动/暂停/步进/重置
  * 实现步长选择（1s/1min/5min/1h）
  * 实现速度调节（0.5x - 20x）
  * 实现仿真数据初始化：100只fz股票加载

* **Acceptance Criteria Addressed**: AC-3

* **Test Requirements**:
  * `programmatic` TR-6.1: 仿真启动后虚拟时钟正确运行
  * `programmatic` TR-6.2: 备选池正确加载100只fz前缀股票
  * `human-judgement` TR-6.3: 仿真控制面板操作流畅

* **Notes**: 参考 `web/index.html` 中的仿真面板结构

## [ ] Task 7: 回放模式面板完善

* **Priority**: medium

* **Depends On**: Task 5

* **Description**:
  * 完善回放控制面板：时间显示、进度条、播放/暂停/步进
  * 实现速度选择（1x/2x/5x/10x/100x/MAX）
  * 实现周期选择（1min/5min/15min/30min/60min/日线）
  * 实现日期区间选择

* **Acceptance Criteria Addressed**: AC-2

* **Test Requirements**:
  * `programmatic` TR-7.1: 回放面板正确初始化
  * `human-judgement` TR-7.2: 回放控制面板操作流畅，进度条正确更新

* **Notes**: 参考 `web/index.html` 中的回放面板结构

## [ ] Task 8: 事件面板完善

* **Priority**: high

* **Depends On**: Task 6

* **Description**:
  * 完善事件监控窗口：可拖拽、折叠/展开、自动滚动
  * 实现事件分类筛选：Tick、Bar、Formula、Edge、Transfer、Signal、Order、TTL、System
  * 实现事件图标和颜色展示
  * 实现事件详情展示

* **Acceptance Criteria Addressed**: AC-4

* **Test Requirements**:
  * `programmatic` TR-8.1: 仿真运行时事件面板显示完整事件链
  * `programmatic` TR-8.2: 事件分类筛选正确（勾选/取消勾选）
  * `human-judgement` TR-8.3: 事件图标颜色清晰，拖拽浮窗正常

* **Notes**: 参考 `web/js/ui.js` 中的 EventPanel 实现

## [ ] Task 9: K线与公式面板完善

* **Priority**: medium

* **Depends On**: Task 6

* **Description**:
  * 完善K线图表：股票代码选择、周期选择、Canvas绘制
  * 实现公式结果展示
  * 实现实时更新

* **Acceptance Criteria Addressed**: AC-3

* **Test Requirements**:
  * `human-judgement` TR-9.1: K线图正确绘制，公式结果实时更新

* **Notes**: 参考 `web/js/app.js` 中的 Charts 实现

## [ ] Task 10: 配置中心完善

* **Priority**: medium

* **Depends On**: None

* **Description**:
  * 完善配置分类浏览：左侧分类列表、右侧配置表列表
  * 实现搜索功能
  * 完善配置表编辑：表格视图、JSON视图、表单视图、校验视图
  * 实现校验和热加载功能

* **Acceptance Criteria Addressed**: AC-6

* **Test Requirements**:
  * `human-judgement` TR-10.1: 配置中心界面清晰，搜索功能正常
  * `programmatic` TR-10.2: 配置表编辑和热加载功能正确

* **Notes**: 参考 `web/index.html` 中的配置中心视图

## [ ] Task 11: 公式管理完善

* **Priority**: medium

* **Depends On**: None

* **Description**:
  * 完善公式列表：搜索、分类筛选、列表展示
  * 完善公式编辑器：名称、分类、类型、描述、脚本、参数
  * 实现公式测试功能

* **Acceptance Criteria Addressed**: AC-5

* **Test Requirements**:
  * `human-judgement` TR-11.1: 公式管理界面清晰，操作便捷
  * `programmatic` TR-11.2: 公式测试功能正确执行

* **Notes**: 参考 `web/index.html` 中的公式管理视图

## [ ] Task 12: 导入导出功能完善

* **Priority**: high

* **Depends On**: Task 2

* **Description**:
  * 完善导入功能：DZH XML、TDX XML、JSON格式
  * 实现文件拖拽上传和导入预览
  * 完善导出功能：DZH XML、TDX XML、JSON格式
  * 完善保存功能：服务器API、本地存储、撤销/重做

* **Acceptance Criteria Addressed**: AC-7

* **Test Requirements**:
  * `programmatic` TR-12.1: 导入导出功能正确执行
  * `programmatic` TR-12.2: 撤销/重做功能正确
  * `human-judgement` TR-12.3: 导入预览清晰，操作便捷

* **Notes**: 参考 `web/js/app.js` 中的 PoolDataManager 导入导出方法

## [ ] Task 13: 股票池列表完善

* **Priority**: medium

* **Depends On**: Task 12

* **Description**:
  * 完善股票池列表展示：通达信、大智慧、示例、已保存
  * 实现搜索功能
  * 实现加载、删除、重命名操作

* **Acceptance Criteria Addressed**: AC-1

* **Test Requirements**:
  * `programmatic` TR-13.1: 股票池列表正确加载和展示
  * `human-judgement` TR-13.2: 列表操作便捷，搜索功能正常

* **Notes**: 参考 `web/index.html` 中的左侧股票池列表

## [ ] Task 14: 上下文菜单完善

* **Priority**: medium

* **Depends On**: Task 2

* **Description**:
  * 完善画布右键菜单：添加节点、属性、综合设置、复制/剪切/粘贴、层级调整、删除
  * 实现对话框：线条宽度、说明文字、清除确认、选择品种、选择板块

* **Acceptance Criteria Addressed**: AC-1

* **Test Requirements**:
  * `human-judgement` TR-14.1: 上下文菜单功能完整，操作便捷
  * `programmatic` TR-14.2: 对话框正确弹出和关闭

* **Notes**: 参考 `web/index.html` 中的上下文菜单和对话框

## [ ] Task 15: 响应式布局完善

* **Priority**: medium

* **Depends On**: All UI tasks

* **Description**:
  * 完善桌面端布局（1200px+）
  * 完善平板端布局（768px-1199px）
  * 完善移动端布局（<768px）
  * 实现移动端浮动按钮和底部导航

* **Acceptance Criteria Addressed**: AC-8

* **Test Requirements**:
  * `human-judgement` TR-15.1: 不同设备尺寸下布局自适应，功能正常

* **Notes**: 参考 `web/css/styles.css` 和 `web/index.html` 中的响应式设计

## [ ] Task 16: 前端代码优化与清理

* **Priority**: low

* **Depends On**: All tasks

* **Description**:
  * 清理冗余代码和注释
  * 优化代码结构，提高可维护性
  * 统一代码风格
  * 修复潜在的性能问题

* **Acceptance Criteria Addressed**: NFR-4

* **Test Requirements**:
  * `human-judgement` TR-16.1: 代码结构清晰，注释充分
  * `programmatic` TR-16.2: 所有功能正常运行，无报错

* **Notes**: 检查 `web/js/` 目录下所有文件

## [ ] Task 17: Playwright 浏览器验证

* **Priority**: high

* **Depends On**: Task 1-16

* **Description**:
  * 使用 Playwright MCP 工具验证所有前端功能
  * 验证四种模式切换正确
  * 验证仿真模式运行和事件链完整
  * 验证导入导出功能正确

* **Acceptance Criteria Addressed**: AC-2, AC-3, AC-7

* **Test Requirements**:
  * `programmatic` TR-17.1: Playwright 测试通过所有验证点
  * `human-judgement` TR-17.2: 界面功能完整，操作便捷

* **Notes**: 使用 integrated_browser MCP 工具

## [ ] Task 18: 文档更新

* **Priority**: low

* **Depends On**: Task 17

* **Description**:
  * 更新 `docs/DESIGN.md` 和 `docs/DESIGN0.md`
  * 新增前端功能规范章节
  * 更新架构图和流程图

* **Acceptance Criteria Addressed**: 文档完整性

* **Test Requirements**:
  * `human-judgement` TR-18.1: 文档内容完整，与代码一致

* **Notes**: 参考现有文档结构