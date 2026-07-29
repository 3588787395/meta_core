# 前端股票池平台 - 完整功能清单与处理流程规范

## Overview

### Summary
本规范基于对现有代码的全面分析，详细列出 Meta Core 股票池平台前端的所有功能模块、处理流程、状态管理和交互逻辑，作为前端界面完善和测试验证的完整依据。

### Purpose
提供完整的前端功能蓝图，确保所有功能点被正确识别、实现和验证，支持双工程师协作评审流程。

### Target Users
- 股票池设计者：配置股票池拓扑、转移条件、运行参数
- 策略开发者：编写和管理公式
- 系统管理员：配置和管理系统参数

## Goals
- 完整列出前端所有功能模块和处理流程
- 建立功能与代码的映射关系
- 提供清晰的功能验证标准
- 支持严格的双工程师评审流程

## Non-Goals (Out of Scope)
- 后端核心逻辑实现
- 数据库持久化逻辑
- 公式计算引擎实现

## 前端功能模块清单

### 一、股票池设计器

#### 1.1 画布引擎 (FlowCanvas)

**文件位置**: `web/js/canvas.js`

**功能清单**:
- 画布显示股票池拓扑图（节点和边）
- 支持拖拽移动节点（支持多选拖拽）
- 支持缩放（鼠标滚轮、缩放按钮）
- 支持适应画布（fitToContent）
- 支持三种线形：贝兹曲线、横竖折线、直线
- 支持连线模式创建边（通过 handle 拖拽）
- 支持框选多个节点
- 支持迷你地图导航
- 支持节点选中状态管理
- 支持节点大小调整（resize handles）
- 支持节点高亮闪烁
- 支持运行模式禁用编辑

**处理流程**:
```
用户操作 → FlowCanvas.render() → 更新内部状态 → 重绘节点和边
缩放/平移 → 更新 transform → 应用变换 → 更新迷你地图
连线模式 → 监听 handle 拖拽 → 创建临时边 → 释放时创建正式边
节点拖拽 → 计算偏移 → 更新节点 position → 重绘关联边
框选 → 计算矩形区域 → 匹配节点 → 更新选中状态
```

**关键方法**:
- `render(data)` - 渲染整个画布
- `fitToContent(padding)` - 适应画布
- `setEdgeLineType(type)` - 设置线形
- `selectNode(nodeId)` / `selectEdge(edgeId)` - 选中节点/边
- `_renderNode(node)` - 渲染单个节点
- `_renderEdge(edge)` - 渲染单个边
- `_attachNodeDrag(el, node)` - 绑定节点拖拽
- `_updateMinimap()` - 更新迷你地图

#### 1.2 节点类型渲染

**节点类型列表**:
| 类型 | 渲染方法 | 视觉特征 |
|------|---------|---------|
| `stock_state_pool` | `_renderStatePool()` | 圆角矩形，标题栏，股票列表 |
| `statepool` | `_renderStatePool()` | 同股票池 |
| `transfer_condition` | `_renderCondition()` | 三角形（指向右侧） |
| `market_source` | `_renderCandidate()` | 圆柱体 |
| `discard_pool` | `_renderDiscard()` | 小矩形，红色边框 |
| `text_label` | `_renderLabel()` | 文本标签 |
| `container` | `_renderContainer()` | 虚线边框容器 |
| `state_column` | `_renderColumn()` | 列节点 |
| `execution_order` | `_renderExecutionOrder()` | 执行顺序编号 |
| `flow_arrow` | `_renderArrowDeco()` | 箭头装饰 |
| `tdx_candidate` | `_renderTdxCandidate()` | TDX圆柱体 |
| `tdx_state_pool` | `_renderTdxStatePool()` | TDX状态池 |
| `tdx_condition` | `_renderTdxCondition()` | TDX条件三角形 |
| `condition` | `_renderConditionNode()` | 紫色矩形条件节点 |

**处理流程**:
```
节点数据 → _renderNode(node) → 根据 type 分发到对应渲染方法 → 创建 DOM 元素 → 添加交互事件 → 挂载到画布
```

#### 1.3 边渲染

**功能清单**:
- 边路径渲染（贝兹曲线/横竖折线/直线）
- 箭头标记（按策略类型显示不同颜色）
- 边标签（显示时间间隔、条件名称）
- 执行顺序编号徽标
- 多选入边顺序号显示
- 选中状态高亮

**处理流程**:
```
边数据 → _renderEdge(edge) → 计算起点/终点 → 构建路径 → 创建箭头标记 → 渲染标签 → 添加点击事件
```

#### 1.4 DZH颜色转换工具

**功能清单**:
- DZH调色板索引转CSS颜色
- BGR直接色解码
- 颜色元信息返回模式
- 颜色可视化徽章渲染

**处理流程**:
```
DZH颜色值 → dzhColorToCss(value, fallback, returnMeta) → 判断类型（调色板/BGR/特殊值）→ 返回CSS颜色或元信息对象
```

### 二、数据管理 (PoolDataManager)

**文件位置**: `web/js/app.js`

**功能清单**:
- 股票池数据加载（API/本地存储）
- 数据保存（API/本地存储）
- 撤销/重做功能
- 剪贴板操作（复制/剪切/粘贴）
- 配置表缓存（cell_type_registry, modules, dzh_type_map等）
- LRU缓存（股票数据）
- 数据变更通知机制

**处理流程**:
```
加载股票池 → loadFromAPI()/loadFromLocal() → 设置数据 → _notify() → 画布重绘
保存 → saveToAPI()/saveToLocal() → 持久化 → 更新历史记录
撤销 → undo() → 恢复上一状态快照 → _notify() → 画布重绘
```

**关键方法**:
- `loadFromAPI(poolId)` - 从API加载
- `saveToAPI()` - 保存到API
- `saveToLocal()` - 本地存储
- `undo()` / `redo()` - 撤销/重做
- `initNew()` / `initDemo()` - 初始化
- `importXML()` / `exportXML()` - 导入导出
- `importTDXXML()` - TDX导入

### 三、属性面板 (TableDrivenPanel)

**文件位置**: `web/js/ui.js`

**功能清单**:
- 表驱动属性面板渲染
- 动态表单生成
- 字段联动（depends_on/active_when）
- 实时校验
- DZH颜色值可视化

**处理流程**:
```
选中节点/边 → 获取配置表字段定义 → TableDrivenPanel.render() → 动态生成表单
字段联动 → 监听字段变化 → 根据 depends_on/active_when 显示/隐藏相关字段
实时校验 → ValidationEngine.validate() → 显示错误信息
```

### 四、运行模式管理

**文件位置**: `web/js/app.js`, `web/js/canvas.js`

**功能清单**:
- 设计模式：股票池配置和编辑
- 实盘模式：实时行情数据运行
- 回放模式：历史数据回放
- 仿真模式：模拟数据运行（fz前缀股票）
- 模式切换控制
- 运行状态管理（开始/暂停/停止）
- 运行时禁用编辑操作

**处理流程**:
```
点击模式按钮 → 更新模式状态 → 更新模式指示器 → 显示/隐藏对应控制面板
运行中禁止切换模式 → 显示警告提示
点击开始 → 发送启动请求到后端 → 更新按钮状态 → 状态栏显示运行中
点击暂停 → 发送暂停请求 → 更新按钮状态 → 状态栏显示已暂停
点击停止 → 发送停止请求 → 更新按钮状态 → 状态栏显示已停止 → 恢复设计操作
```

### 五、仿真模式面板

**文件位置**: `web/index.html`, `web/js/app.js`

**功能清单**:
- 虚拟时钟显示
- 步数计数器
- 启动/暂停/步进/重置按钮
- 步长选择（1s/1min/5min/1h）
- 速度调节（0.5x - 20x）
- 仿真数据初始化：100只fz股票加载

**处理流程**:
```
点击启动 → 发送仿真启动请求 → 虚拟时钟开始运行 → 步数计数器递增
点击步进 → 发送单步请求 → 执行一个tick → 更新时钟和步数
点击重置 → 发送重置请求 → 重置时钟和步数
调整步长/速度 → 更新仿真参数 → 发送到后端
切换到仿真模式 → 自动加载100只fz股票 → 显示加载进度
```

### 六、回放模式面板

**文件位置**: `web/index.html`, `web/js/app.js`

**功能清单**:
- 当前时间显示
- 进度条（当前位置/总长度）
- 播放/暂停/步进按钮
- 速度选择（1x/2x/5x/10x/100x/MAX）
- 周期选择（1min/5min/15min/30min/60min/日线）
- 日期区间选择

**处理流程**:
```
选择日期区间 → 发送回放范围请求 → 初始化进度条
点击播放 → 发送回放启动请求 → 更新进度条和时间显示
调整速度/周期 → 更新回放参数 → 发送到后端
```

### 七、事件面板 (EventPanel)

**文件位置**: `web/js/ui.js`, `web/index.html`

**功能清单**:
- 可拖拽浮窗
- 折叠/展开功能
- 自动滚动到最新事件
- 暂停/继续接收事件
- 清空事件列表
- 事件分类筛选：Tick、Bar、Formula、Edge、Transfer、Signal、Order、TTL、System
- 事件图标和颜色展示
- 事件详情展示

**处理流程**:
```
拖拽浮窗 → 更新位置 → 存储到本地存储
折叠/展开 → 切换面板高度 → 保存状态
自动滚动 → 监听新事件 → 滚动到最新条目
暂停/继续 → 控制事件接收 → 更新按钮状态
WebSocket接收事件 → EventPanel.addEvent() → 根据类型分配颜色和图标 → 渲染事件条目
点击筛选 → 更新筛选状态 → 过滤事件列表 → 显示/隐藏对应类型事件
```

### 八、K线与公式面板

**文件位置**: `web/js/app.js`, `web/index.html`

**功能清单**:
- 股票代码选择
- 周期选择（1分钟/5分钟）
- Canvas绘制K线图
- 显示价格信息
- 公式结果展示
- 实时更新

**处理流程**:
```
选择股票代码 → 请求K线数据 → KlineChart.render() → Canvas绘制
选择周期 → 请求对应周期数据 → 重新绘制图表
公式计算完成 → 接收 FormulaEvaluated 事件 → 更新公式结果面板
```

### 九、配置中心

**文件位置**: `web/index.html`, `web/js/app.js`

**功能清单**:
- 配置分类浏览（左侧分类列表）
- 配置表列表（右侧）
- 搜索功能
- 表格视图
- JSON视图
- 表单视图
- 校验视图
- 校验全部配置
- 热加载配置变更

**处理流程**:
```
加载配置分类 → 渲染左侧列表 → 点击分类 → 渲染右侧配置表列表
搜索 → 过滤配置表列表 → 显示匹配结果
选择配置表 → 加载配置数据 → 根据视图类型渲染 → 编辑后保存
点击校验 → 发送校验请求 → 显示校验结果
点击热加载 → 发送热加载请求 → 更新配置缓存 → 刷新界面
```

### 十、公式管理

**文件位置**: `web/index.html`, `web/js/app.js`

**功能清单**:
- 公式列表展示
- 搜索公式
- 按分类筛选（指标公式、选股公式、专家系统）
- 公式编辑器（名称、分类、类型、描述、脚本、参数）
- 公式测试功能
- 保存/删除公式

**处理流程**:
```
加载公式列表 → 渲染列表 → 搜索/筛选 → 更新列表显示
选择公式 → 加载公式数据 → 渲染编辑器 → 编辑后保存/测试/删除
点击测试 → 弹出测试对话框 → 选择股票代码和周期 → 执行测试 → 显示结果
```

### 十一、导入导出功能

**文件位置**: `web/js/app.js`, `web/index.html`

**功能清单**:
- DZH格式XML导入
- TDX格式XML导入
- JSON格式导入
- 文件拖拽上传
- 导入预览
- DZH格式XML导出
- TDX格式XML导出
- JSON格式导出

**处理流程**:
```
选择文件/拖拽上传 → 解析文件 → 显示导入预览 → 确认导入 → 更新股票池数据
选择导出格式 → 请求后端导出 → 下载文件
```

### 十二、股票池列表

**文件位置**: `web/index.html`, `web/js/app.js`

**功能清单**:
- 通达信池列表
- 大智慧池列表
- 示例池列表
- 已保存池列表
- 搜索功能
- 加载/删除/重命名操作

**处理流程**:
```
加载股票池列表 → 渲染四个标签页 → 搜索 → 过滤列表
点击加载 → PoolDataManager.loadFromAPI()/loadTDXPool() → 更新画布
点击删除 → 弹出确认对话框 → 发送删除请求 → 更新列表
点击重命名 → 弹出重命名对话框 → 发送重命名请求 → 更新列表
```

### 十三、上下文菜单

**文件位置**: `web/index.html`, `web/js/app.js`

**功能清单**:
- 画布右键菜单（添加节点、属性、综合设置、复制/剪切/粘贴、层级调整、删除）
- 对话框（线条宽度、说明文字、清除确认、选择品种、选择板块）

**处理流程**:
```
右键点击 → 显示上下文菜单 → 点击菜单项 → 执行对应操作
触发对话框 → 显示对话框 → 用户输入 → 确认/取消 → 执行操作
```

### 十四、状态栏

**文件位置**: `web/index.html`, `web/js/app.js`

**功能清单**:
- 就绪状态显示
- 节点数/连线数显示
- 鼠标坐标显示
- 当前时间显示
- 缩放比例显示

**处理流程**:
```
实时更新 → 监听画布事件 → 更新节点数/连线数/缩放比例
监听鼠标移动 → 更新鼠标坐标
定时器 → 更新当前时间
```

### 十五、响应式布局

**文件位置**: `web/css/styles.css`, `web/index.html`

**功能清单**:
- 桌面端布局（1200px+）
- 平板端布局（768px-1199px）
- 移动端布局（<768px）
- 移动端浮动按钮
- 移动端底部导航

**处理流程**:
```
窗口大小变化 → CSS媒体查询 → 调整布局 → 显示/隐藏元素
```

## Non-Functional Requirements

### NFR-1: 性能
- 画布渲染响应时间 < 100ms
- 事件面板更新响应时间 < 50ms
- 支持 100+ 节点的股票池
- 虚拟滚动支持 1000+ 股票列表

### NFR-2: 可用性
- 操作流程直观，符合用户习惯
- 错误信息清晰明确
- 支持键盘快捷键（撤销 Ctrl+Z、重做 Ctrl+Y）
- 拖拽操作流畅无卡顿

### NFR-3: 响应式
- 支持桌面端（1200px+）
- 支持平板端（768px-1199px）
- 支持移动端（<768px）

### NFR-4: 可维护性
- 代码结构清晰，模块化设计
- 遵循表驱动设计原则
- 配置表与代码分离
- 消除重复代码

## Constraints

### Technical
- 前端框架：原生 JavaScript（无第三方框架）
- 构建工具：无（直接引用 JS/CSS 文件）
- 浏览器兼容：Chrome ≥ 80, Firefox ≥ 75

### Dependencies
- 后端 API：`/api/*` 端点
- WebSocket：`/ws/events` 事件推送
- 公式插件：HQChartPy2（Python 后端）

## Assumptions
- 后端 API 已实现并正常运行
- WebSocket 连接稳定可靠
- 用户具有基本的股票池操作知识

## Acceptance Criteria

### AC-1: 股票池设计功能完整
- **Given**：用户进入主页并加载示例池
- **When**：用户创建节点、连接边、配置属性
- **Then**：所有操作正确执行，画布实时更新
- **Verification**：`human-judgment`

### AC-2: 四种模式切换正确
- **Given**：股票池已加载
- **When**：用户在设计/实盘/回放/仿真模式间切换
- **Then**：模式指示器更新，对应控制面板显示/隐藏正确
- **Verification**：`programmatic`

### AC-3: 仿真模式运行正确
- **Given**：切换到仿真模式并点击开始
- **When**：仿真运行 ≥300 秒虚拟时钟
- **Then**：事件面板显示完整事件链，股票正确流转
- **Verification**：`programmatic`

### AC-4: 事件面板分类展示
- **Given**：仿真运行中
- **When**：用户切换不同事件分类筛选
- **Then**：事件按分类正确显示/隐藏，颜色和图标正确
- **Verification**：`human-judgment`

### AC-5: 公式管理功能完整
- **Given**：用户进入公式管理页面
- **When**：用户创建、编辑、测试公式
- **Then**：所有操作正确执行，测试结果正确显示
- **Verification**：`human-judgment`

### AC-6: 配置中心功能完整
- **Given**：用户进入配置中心
- **When**：用户查看、编辑、校验配置表
- **Then**：所有操作正确执行，热加载生效
- **Verification**：`human-judgment`

### AC-7: 导入导出功能完整
- **Given**：股票池已设计完成
- **When**：用户导入/导出不同格式文件
- **Then**：导入导出成功，数据一致性保证
- **Verification**：`programmatic`

### AC-8: 响应式布局正确
- **Given**：页面在不同设备上打开
- **When**：调整浏览器窗口大小
- **Then**：布局自适应，功能正常使用
- **Verification**：`human-judgment`

### AC-9: 综合设置窗口功能完整
- **Given**：股票池包含多个节点和边
- **When**：用户打开综合设置窗口并编辑字段
- **Then**：三列布局正确显示，字段编辑器功能完整
- **Verification**：`human-judgment`

### AC-10: Playwright浏览器验证
- **Given**：前端服务正常运行
- **When**：使用Playwright执行验证脚本
- **Then**：所有验证点通过
- **Verification**：`programmatic`

## Open Questions
- [ ] 是否需要添加更多的快捷键支持？
- [ ] 是否需要支持多语言国际化？
- [ ] 是否需要添加股票池模板功能？
- [ ] 是否需要添加用户权限管理？

## 功能与代码映射表

| 功能模块 | 主要文件 | 核心类/对象 |
|---------|---------|------------|
| 画布引擎 | `web/js/canvas.js` | `FlowCanvas` |
| DZH颜色工具 | `web/js/canvas.js` | `DZH_COLOR_UTILS` |
| 数据管理 | `web/js/app.js` | `PoolDataManager`, `LRUCache` |
| 属性面板 | `web/js/ui.js` | `TableDrivenPanel`, `ComponentRegistry`, `ValidationEngine` |
| 事件面板 | `web/js/ui.js` | `EventPanel` |
| K线图表 | `web/js/app.js` | `KlineChart`, `BaseChart` |
| 公式编辑器 | `web/js/app.js` | `FormulaEditor` |
| 配置管理 | `web/js/app.js` | `ConfigManager` |
| 综合设置 | `web/js/app.js` | `ComprehensiveSettings` |
| 主界面 | `web/index.html` | DOM元素 |
| 样式 | `web/css/styles.css` | CSS规则 |