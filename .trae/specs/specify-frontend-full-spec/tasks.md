# Meta Core 股票池平台 - 前端实现任务列表

## [ ] Task 1: 画布引擎完善
- **Priority**: high
- **Depends On**: None
- **Description**:
  - 完善 FlowCanvas 画布引擎，支持节点渲染、拖拽移动、缩放适应画布、三种线形（贝兹曲线/横竖折线/直线）
  - 支持框选多个节点、迷你地图导航、节点选中状态管理
  - 支持运行模式禁用编辑功能
- **Acceptance Criteria Addressed**: AC-1
- **Test Requirements**:
  - `programmatic` TR-1.1: 画布渲染响应时间 < 100ms（通过 Performance API 测量）
  - `programmatic` TR-1.2: 支持至少 100 个节点的股票池渲染
  - `human-judgment` TR-1.3: 拖拽操作流畅无卡顿，缩放和平移响应及时

## [ ] Task 2: DZH颜色转换工具实现
- **Priority**: high
- **Depends On**: Task 1
- **Description**:
  - 实现 DZH_COLOR_UTILS 工具类，支持 DZH调色板索引转CSS颜色（20色标准调色板）
  - 支持 BGR直接色解码（高位=B, 中位=G, 低位=R）
  - 支持颜色元信息返回模式（含名称、类型、RGB等）
  - 实现颜色可视化徽章渲染组件
- **Acceptance Criteria Addressed**: AC-10
- **Test Requirements**:
  - `programmatic` TR-2.1: dzhColorToCss() 能正确转换所有 20 种调色板颜色
  - `programmatic` TR-2.2: BGR 颜色值能正确解码为 CSS 颜色
  - `human-judgment` TR-2.3: 属性面板中的颜色选择器能正确显示 DZH 颜色预览

## [ ] Task 3: 节点类型渲染完善
- **Priority**: high
- **Depends On**: Task 1
- **Description**:
  - 完善所有节点类型的渲染：股票池(200/8)、转移条件(201/3)、备选池(202/7)、丢弃池(4)、文本标签、容器等
  - 支持 DZH 和 TDX 两种格式的节点渲染差异
  - 实现显式条件节点（condition类型）的紫色矩形渲染
- **Acceptance Criteria Addressed**: AC-1, AC-10
- **Test Requirements**:
  - `programmatic` TR-3.1: DZH节点类型(200/201/202/4)正确渲染
  - `programmatic` TR-3.2: TDX节点类型(7/8/3)正确渲染
  - `human-judgment` TR-3.3: 节点视觉效果符合设计文档要求

## [ ] Task 4: 边管理功能实现
- **Priority**: high
- **Depends On**: Task 1, Task 3
- **Description**:
  - 实现边的创建（通过拖拽handle或工具栏）、删除、属性配置
  - 支持三种线形：贝兹曲线、横竖折线、直线
  - 支持执行顺序编号、箭头标记按策略类型显示不同颜色
  - 支持多选入边顺序号显示和选中状态高亮
- **Acceptance Criteria Addressed**: AC-1
- **Test Requirements**:
  - `programmatic` TR-4.1: 创建边后能正确保存并渲染
  - `programmatic` TR-4.2: 删除边后关联节点不受影响
  - `human-judgment` TR-4.3: 边的视觉效果清晰，执行顺序编号正确显示

## [ ] Task 5: 属性面板完善
- **Priority**: high
- **Depends On**: Task 1, Task 2, Task 3
- **Description**:
  - 完善 TableDrivenPanel 属性面板，支持选中节点/边时显示对应属性配置
  - 实现字段联动（depends_on/active_when）
  - 实现实时校验并显示错误信息
  - 实现位标志自动编解码（DataBinder.decodeAttrFlags/encodeAttrFlags）
  - 实现 DZH 颜色值可视化显示
- **Acceptance Criteria Addressed**: AC-1, AC-10
- **Test Requirements**:
  - `programmatic` TR-5.1: 属性面板能正确显示节点和边的配置项
  - `programmatic` TR-5.2: 字段联动功能正常（显示/隐藏相关字段）
  - `human-judgment` TR-5.3: 校验错误信息清晰明确，位标志编解码正确

## [ ] Task 6: 四种模式切换实现
- **Priority**: high
- **Depends On**: None
- **Description**:
  - 实现设计/实盘/回放/仿真四种模式的切换功能
  - 更新模式指示器（颜色和标签）：设计=蓝色, 实盘=绿色, 回放=橙色, 仿真=紫色
  - 显示/隐藏对应模式的控制面板
  - 禁止在运行中切换模式
- **Acceptance Criteria Addressed**: AC-2
- **Test Requirements**:
  - `programmatic` TR-6.1: 模式切换时模式指示器颜色和标签正确更新
  - `programmatic` TR-6.2: 运行中切换模式被正确阻止并显示提示
  - `human-judgment` TR-6.3: 模式切换操作直观，反馈及时

## [ ] Task 7: 仿真模式面板实现
- **Priority**: high
- **Depends On**: Task 6
- **Description**:
  - 实现仿真控制面板：虚拟时钟显示、步数计数器、启动/暂停/步进/重置按钮
  - 实现步长选择（1s/1min/5min/1h）和速度调节（0.5x - 20x）
  - 实现仿真数据初始化：加载 100 只 fz 前缀股票到备选池
  - 每只股票分配固定 tick 间隔（1-9秒随机值，同股票间隔固定）
- **Acceptance Criteria Addressed**: AC-3
- **Test Requirements**:
  - `programmatic` TR-7.1: 仿真启动后虚拟时钟正确运行，步数计数器递增
  - `programmatic` TR-7.2: 仿真运行 ≥300 秒后事件面板显示完整事件链
  - `human-judgment` TR-7.3: 股票正确流转（备选池→条件→状态池→C池）

## [ ] Task 8: 回放模式面板实现
- **Priority**: medium
- **Depends On**: Task 6
- **Description**:
  - 实现回放控制面板：当前时间显示、进度条、播放/暂停/步进按钮
  - 实现速度选择（1x/2x/5x/10x/100x/MAX）和周期选择（1min/5min/15min/30min/60min/日线）
  - 实现日期区间选择功能
- **Acceptance Criteria Addressed**: AC-2
- **Test Requirements**:
  - `programmatic` TR-8.1: 回放启动后进度条和时间显示正确更新
  - `human-judgment` TR-8.2: 速度和周期调整功能正常

## [ ] Task 9: 事件面板实现
- **Priority**: high
- **Depends On**: Task 6, Task 7
- **Description**:
  - 实现事件监控窗口：可拖拽浮窗、折叠/展开、自动滚动、暂停/继续接收
  - 实现事件分类筛选：Tick、Bar、Formula、Edge、Transfer、Signal、Order、TTL、System
  - 每种类型用不同颜色和图标标识
  - 实现事件展示：时间戳、事件类型图标、事件详情、事件状态标识
- **Acceptance Criteria Addressed**: AC-4, AC-11
- **Test Requirements**:
  - `programmatic` TR-9.1: WebSocket 事件能正确接收并显示
  - `programmatic` TR-9.2: 事件分类筛选功能正常（显示/隐藏对应类型）
  - `human-judgment` TR-9.3: 事件面板拖拽位置保存到本地存储，折叠/展开状态保存

## [ ] Task 10: 综合设置窗口实现
- **Priority**: medium
- **Depends On**: Task 4, Task 5
- **Description**:
  - 实现综合设置窗口：表格形式展示所有流程（源→条件→目标）
  - 三列布局：流程标识、条件/属性、时序/操作
  - 双击字段弹出字段编辑器
  - 支持批量编辑和快速配置
- **Acceptance Criteria Addressed**: AC-9
- **Test Requirements**:
  - `programmatic` TR-10.1: 综合设置窗口能正确加载和显示所有边信息
  - `human-judgment` TR-10.2: 双击字段能弹出编辑器，编辑后数据正确保存

## [ ] Task 11: 公式管理功能实现
- **Priority**: medium
- **Depends On**: None
- **Description**:
  - 实现公式列表：搜索、按分类筛选（指标公式、选股公式、专家系统）
  - 实现公式编辑器：名称、分类、类型、描述、脚本编辑器、参数列表管理
  - 实现公式测试：选择股票代码和周期，执行测试并显示结果
- **Acceptance Criteria Addressed**: AC-5
- **Test Requirements**:
  - `programmatic` TR-11.1: 公式列表能正确加载和搜索
  - `programmatic` TR-11.2: 公式保存后能正确存储到后端
  - `human-judgment` TR-11.3: 公式测试结果正确显示

## [ ] Task 12: 配置中心功能实现
- **Priority**: medium
- **Depends On**: None
- **Description**:
  - 实现配置分类浏览：左侧分类列表、右侧配置表列表、搜索功能
  - 实现配置表编辑：表格视图、JSON视图、表单视图、校验视图
  - 实现热加载：校验全部配置、热加载配置变更
- **Acceptance Criteria Addressed**: AC-6
- **Test Requirements**:
  - `programmatic` TR-12.1: 配置表能正确加载和保存
  - `programmatic` TR-12.2: 配置校验功能正常
  - `human-judgment` TR-12.3: 热加载后界面实时更新

## [ ] Task 13: 导入导出功能实现
- **Priority**: high
- **Depends On**: Task 3, Task 4
- **Description**:
  - 实现 DZH格式XML导入导出
  - 实现 TDX格式XML导入导出
  - 实现 JSON格式导入导出
  - 实现文件拖拽上传和导入预览
- **Acceptance Criteria Addressed**: AC-7
- **Test Requirements**:
  - `programmatic` TR-13.1: DZH XML 文件能正确导入导出
  - `programmatic` TR-13.2: TDX XML 文件能正确导入导出
  - `programmatic` TR-13.3: JSON 文件能正确导入导出

## [ ] Task 14: 股票池列表实现
- **Priority**: medium
- **Depends On**: Task 13
- **Description**:
  - 实现股票池列表展示：通达信池、大智慧池、示例池、已保存池四个标签页
  - 实现搜索功能
  - 实现加载、删除、重命名操作
- **Acceptance Criteria Addressed**: AC-1
- **Test Requirements**:
  - `programmatic` TR-14.1: 股票池列表能正确加载和搜索
  - `human-judgment` TR-14.2: 加载股票池后画布正确更新

## [ ] Task 15: 上下文菜单和对话框实现
- **Priority**: medium
- **Depends On**: Task 1, Task 3, Task 4
- **Description**:
  - 实现画布右键菜单：添加节点、属性、综合设置、复制/剪切/粘贴、层级调整、删除
  - 实现线条宽度设置、说明文字设置、清除确认、选择品种、选择板块对话框
- **Acceptance Criteria Addressed**: AC-1
- **Test Requirements**:
  - `human-judgment` TR-15.1: 右键菜单能正确显示和执行操作
  - `human-judgment` TR-15.2: 对话框功能完整，操作结果正确

## [ ] Task 16: K线与公式面板实现
- **Priority**: medium
- **Depends On**: Task 6
- **Description**:
  - 实现 K线图表：股票代码选择、周期选择（1分钟/5分钟）、Canvas绘制K线图
  - 实现公式结果面板：显示公式计算结果，实时更新
- **Acceptance Criteria Addressed**: AC-1
- **Test Requirements**:
  - `human-judgment` TR-16.1: K线图表能正确加载和显示
  - `human-judgment` TR-16.2: 公式结果面板实时更新

## [ ] Task 17: 响应式布局实现
- **Priority**: medium
- **Depends On**: All UI tasks
- **Description**:
  - 实现桌面端布局（1200px+）：三栏布局（左侧列表 + 中间画布 + 右侧属性面板）
  - 实现平板端布局（768px-1199px）：双栏布局或可折叠面板
  - 实现移动端布局（<768px）：底部导航 + 浮动按钮 + 汉堡菜单
- **Acceptance Criteria Addressed**: AC-8
- **Test Requirements**:
  - `human-judgment` TR-17.1: 桌面端三栏布局正确显示
  - `human-judgment` TR-17.2: 平板端布局自适应
  - `human-judgment` TR-17.3: 移动端浮动按钮和底部导航正确显示

## [ ] Task 18: Playwright浏览器验证脚本
- **Priority**: high
- **Depends On**: All tasks
- **Description**:
  - 编写 Playwright 验证脚本，覆盖模式切换、仿真运行、事件接收、导入导出等核心功能
  - 实现自动化测试，确保所有验证点通过
- **Acceptance Criteria Addressed**: AC-12
- **Test Requirements**:
  - `programmatic` TR-18.1: Playwright 脚本能正确启动浏览器并执行测试
  - `programmatic` TR-18.2: 所有验证点通过
