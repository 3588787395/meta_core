# Meta Core 股票池平台 - 前端完善规范

## Overview

### Summary
本规范基于对现有代码的全面分析，详细定义 Meta Core 股票池平台前端完善的所有功能模块、处理流程、状态管理和交互逻辑。前端采用单页应用架构，包含三个核心视图（主页、配置中心、公式管理），基于表驱动设计原则构建，所有组件类型和交互行为由后端配置表动态决定。

### Purpose
提供完整的前端功能蓝图，作为开发、测试和评审的依据，确保前端界面简洁清晰、操作简便、功能完整，符合股票池平台的运行逻辑。

### Target Users
- 股票池设计者：配置股票池拓扑、转移条件、运行参数
- 策略开发者：编写和管理公式
- 系统管理员：配置和管理系统参数

## Goals
- 完整实现股票池设计、运行、监控、执行的全流程 UI
- 支持设计/实盘/回放/仿真四种模式的清晰切换
- 事件面板用图标和颜色展示，可分类筛选
- 股票池可视化包含转移条件节点，筛选条件从计算参数和K线配置中读取
- 前端后端解耦，前端仅通过 API 和 WebSocket 与后端交互
- 支持 DZH/TDX 双格式兼容
- 界面简洁清晰地图形化显示不同事件
- 界面操作简便，解决所有使用问题

## Non-Goals (Out of Scope)
- 公式计算引擎后端实现（由 Python 公式插件负责）
- 数据库持久化逻辑（由后端 API 负责）
- 事件驱动引擎核心逻辑（由后端 PoolEngine 负责）
- 第三方框架引入（保持原生 JavaScript）

## Background & Context

### 架构设计原则
- **单一真相源**：`PoolDataManager` 管理所有股票池数据和配置表缓存
- **表驱动 UI**：所有组件类型、交互行为、样式及校验规则均由后端配置表决定
- **事件驱动**：通过 WebSocket 接收后端事件，实时更新界面
- **响应式设计**：支持桌面端、平板端、移动端
- **DZH/TDX 兼容**：支持两种格式的股票池导入导出和渲染

### 核心文件结构
```
web/
├── index.html          # 主界面（三视图：主页/配置中心/公式管理）
├── css/
│   └── styles.css      # 样式文件（基础 + 组件样式）
├── js/
│   ├── canvas.js       # 画布引擎（FlowCanvas + DZH颜色工具）
│   ├── ui.js           # UI组件（TableDrivenPanel + EventPanel + FormulaManager）
│   └── app.js          # 应用核心（PoolDataManager + Charts + main.js）
└── ui_renderer.py      # Python端UI渲染辅助
```

### 核心类/对象映射
| 功能模块 | 主要文件 | 核心类/对象 |
|---------|---------|------------|
| 画布引擎 | `web/js/canvas.js` | `FlowCanvas`, `DZH_COLOR_UTILS` |
| 数据管理 | `web/js/app.js` | `PoolDataManager`, `LRUCache` |
| 属性面板 | `web/js/ui.js` | `TableDrivenPanel`, `ComponentRegistry`, `ValidationEngine`, `DataBinder` |
| 事件面板 | `web/js/event-panel.js` | `EventPanel` (分类矩阵 / 散点分布 / 定时器队列) |
| K线图表 | `web/js/app.js` | `KlineChart`, `BaseChart` |
| 公式管理 | `web/js/app.js` | `FormulaEditor`, `ConfigManager`, `ComprehensiveSettings` |

## Functional Requirements

### FR-1: 股票池设计器

#### FR-1.1: 画布引擎 (FlowCanvas)

**功能清单：**
- 画布显示股票池拓扑图（节点和边）
- 支持拖拽移动节点（支持多选拖拽）
- 支持缩放（鼠标滚轮、缩放按钮，范围 0.1-5x）
- 支持适应画布（fitToContent）
- 支持三种线形：贝兹曲线、横竖折线、直线
- 支持连线模式创建边（通过 handle 拖拽）
- 支持框选多个节点
- 支持迷你地图导航
- 支持节点选中状态管理
- 支持节点大小调整（resize handles）
- 支持节点高亮闪烁
- 支持运行模式禁用编辑

**处理流程：**
```
用户操作 → FlowCanvas.render(data) → 更新内部状态(_nodes, _edges) → 重绘节点和边
缩放/平移 → 更新 transform(x,y,zoom) → _applyTransform() → 更新迷你地图
连线模式 → 监听 handle 拖拽(_startConnectionDrag) → 创建临时边(_tempEdge) → 释放时创建正式边(onConnect)
节点拖拽 → 计算偏移 → 更新节点 position → 重绘关联边
框选 → 计算矩形区域 → 匹配节点 → 更新选中状态(selectedNodeIds)
```

**关键方法：**
- `render(data)` - 渲染整个画布
- `fitToContent(padding)` - 适应画布
- `setEdgeLineType(type)` - 设置线形
- `selectNode(nodeId)` / `selectEdge(edgeId)` - 选中节点/边
- `zoomIn()` / `zoomOut()` / `setZoom(zoom)` - 缩放控制
- `_renderNodes()` / `_renderEdges()` - 渲染节点和边
- `_attachNodeDrag(el, node)` - 绑定节点拖拽
- `_updateMinimap()` - 更新迷你地图

#### FR-1.2: 节点类型渲染

**节点类型列表：**
| 类型 | 渲染方法 | 视觉特征 | DZH类型 | TDX类型 |
|------|---------|---------|---------|---------|
| `stock_state_pool` | `_renderStatePool()` | 圆角矩形，标题栏，股票列表 | 200 | 8 |
| `statepool` | `_renderStatePool()` | 同股票池 | - | - |
| `transfer_condition` | `_renderCondition()` | 三角形（指向右侧） | 201 | 3 |
| `market_source` | `_renderCandidate()` | 圆柱体 | 202 | 7 |
| `discard_pool` | `_renderDiscard()` | 小矩形，红色边框 | 4 | - |
| `text_label` | `_renderLabel()` | 文本标签 | - | - |
| `container` | `_renderContainer()` | 虚线边框容器 | - | - |
| `state_column` | `_renderColumn()` | 列节点 | - | - |
| `execution_order` | `_renderExecutionOrder()` | 执行顺序编号 | - | - |
| `flow_arrow` | `_renderArrowDeco()` | 箭头装饰 | - | - |
| `tdx_candidate` | `_renderTdxCandidate()` | TDX圆柱体 | - | 7 |
| `tdx_state_pool` | `_renderTdxStatePool()` | TDX状态池 | - | 8 |
| `tdx_condition` | `_renderTdxCondition()` | TDX条件三角形 | - | 3 |
| `condition` | `_renderConditionNode()` | 紫色矩形条件节点（显式条件节点） | - | - |

**处理流程：**
```
节点数据 → _renderNode(node) → 根据 type 分发到对应渲染方法 → 创建 DOM 元素 → 添加交互事件 → 挂载到画布
```

#### FR-1.3: 边渲染

**功能清单：**
- 边路径渲染（贝兹曲线/横竖折线/直线）
- 箭头标记（按策略类型显示不同颜色）
- 边标签（显示时间间隔、条件名称）
- 执行顺序编号徽标
- 多选入边顺序号显示
- 选中状态高亮

**处理流程：**
```
边数据 → _renderEdge(edge) → 计算起点/终点 → 构建路径(_buildEdgePath) → 创建箭头标记 → 渲染标签 → 添加点击事件
```

#### FR-1.4: DZH颜色转换工具

**功能清单：**
- DZH调色板索引转CSS颜色（20色标准调色板）
- BGR直接色解码（高位=B, 中位=G, 低位=R）
- 颜色元信息返回模式（含名称、类型、RGB等）
- 颜色可视化徽章渲染

**处理流程：**
```
DZH颜色值 → dzhColorToCss(value, fallback, returnMeta) → 判断类型（调色板/BGR/特殊值）→ 返回CSS颜色或元信息对象
```

#### FR-1.5: 节点管理

**功能清单：**
- 添加节点：备选池、转移条件、状态池、丢弃池、文字标签
- 删除节点（含确认对话框）
- 复制/剪切/粘贴节点
- 节点层级调整（置于顶层/底层）
- 节点选中状态管理

**处理流程：**
```
添加节点 → PoolDataManager.addNode(cellType, position) → 生成新节点ID → _notify() → 画布重绘
删除节点 → 弹出确认对话框 → PoolDataManager.removeNode(id) → 同步删除关联边 → _notify() → 画布重绘
复制 → PoolDataManager.copyToClipboard(id) → 存储到剪贴板
剪切 → PoolDataManager.cutToClipboard(id) → 存储并删除原节点
粘贴 → PoolDataManager.pasteFromClipboard() → 创建副本节点（偏移位置）→ _notify() → 画布重绘
层级调整 → PoolDataManager.bringToFront()/sendToBack() → 调整数组顺序 → _notify() → 画布重绘
```

#### FR-1.6: 边管理

**功能清单：**
- 创建边：源节点 → 目标节点（通过拖拽或工具栏）
- 删除边
- 设置边属性：触发频率、转移条件、顺序号、转移模式
- 设置线条宽度和颜色
- 边选中状态管理
- 执行顺序编号

**处理流程：**
```
创建边 → FlowCanvas.onConnect() → PoolDataManager.addEdge(fromId, toId) → validateFlow()验证 → _notify() → 画布重绘
删除边 → PoolDataManager.removeEdge(id) → 更新边顺序号(_order) → _notify() → 画布重绘
设置边属性 → PoolDataManager.updateEdge(id, changes) → 更新 params → _notify() → 画布重绘
执行顺序 → PoolDataManager.reorderEdges(edgeIds)/reorderEdgesByCondition() → 更新_order → _notify() → 画布重绘
```

#### FR-1.7: 属性面板 (TableDrivenPanel)

**功能清单：**
- 选中节点时显示节点属性配置
- 选中边时显示边属性配置
- 配置项由后端配置表动态生成
- 支持字段联动（depends_on/active_when）
- 实时校验并显示错误信息
- DZH颜色值可视化
- 位标志自动编解码

**处理流程：**
```
选中节点/边 → 获取配置表字段定义(cell_type_registry.field_definitions) → TableDrivenPanel.render() → 动态生成表单
字段联动 → 监听字段变化 → 根据 depends_on/active_when 显示/隐藏相关字段
实时校验 → ValidationEngine.validate(fieldConfig, value, allData) → 显示错误信息
位标志编解码 → DataBinder.decodeAttrFlags()/encodeAttrFlags() → 更新位标志整数值
```

**内置组件列表：**
| 组件类型 | 说明 |
|---------|------|
| `text_input` | 文本输入框 |
| `textarea` | 多行文本输入框 |
| `number_input` | 数字输入框 |
| `select` | 下拉选择框 |
| `tdx_enum_select` | TDX增强枚举选择器 |
| `color_picker` | 颜色选择器（DZH颜色可视化） |
| `flag_group` | 位标志组（复选框组） |
| `action_compound` | 动作复合组件（动作类型+参数） |
| `market_selector` | 市场选择器 |
| `formula_editor` | 公式编辑器（Base64自动编解码） |
| `stock_list_editor` | 股票列表管理 |
| `stock_source_editor` | 股票来源编辑器 |
| `transfer_mode` | 转移模式选择器 |
| `flow_mode_display` | 流转模式显示标签 |
| `condition_summary` | 条件公式摘要 |
| `readonly` | 只读文本显示 |
| `stock_list` | 股票列表（支持虚拟滚动） |
| `kline_chart` | K线图组件 |
| `indicator_chart` | 指标走势图组件 |
| `rule_editor` | 规则编辑器组件 |

#### FR-1.8: 综合设置窗口 (ComprehensiveSettings)

**功能清单：**
- 表格形式展示所有流程（源→条件→目标）
- 三列布局：流程标识、条件/属性、时序/操作
- 双击字段弹出字段编辑器
- 支持批量编辑和快速配置

**处理流程：**
```
打开窗口 → 遍历所有边 → 构建三列布局 → 渲染表格
双击字段 → 弹出字段编辑器 → 编辑完成 → 更新边属性 → 刷新表格
```

### FR-2: 运行模式管理

#### FR-2.1: 模式切换

**功能清单：**
- 设计模式：股票池配置和编辑
- 实盘模式：实时行情数据运行
- 回放模式：历史数据回放
- 仿真模式：模拟数据运行（fz前缀股票）

**处理流程：**
```
点击模式按钮 → 更新模式状态(_runMode) → 更新模式指示器(modeIndicator) → 显示/隐藏对应控制面板
运行中禁止切换模式 → 检查运行状态 → 阻止切换并显示提示
```

#### FR-2.2: 模式状态管理

**功能清单：**
- 模式切换时更新模式指示器（颜色和标签）
- 隐藏/显示对应模式的控制面板
- 禁止在运行中切换模式

**处理流程：**
```
模式切换 → 更新 modeIndicator → 显示对应模式标签和颜色（设计=蓝色, 实盘=绿色, 回放=橙色, 仿真=紫色）
显示/隐藏控制面板 → 控制 simulationPanel/replayPanel 显示状态
运行中切换 → 检查运行状态 → 阻止切换并显示提示
```

#### FR-2.3: 运行控制

**功能清单：**
- 开始/暂停/停止按钮
- 状态栏显示运行状态
- 运行时禁用设计操作

**处理流程：**
```
点击开始 → 发送启动请求到后端(/api/pools/{poolId}/start) → 更新按钮状态 → 状态栏显示运行中 → 禁用画布编辑
点击暂停 → 发送暂停请求(/api/pools/{poolId}/pause) → 更新按钮状态 → 状态栏显示已暂停
点击停止 → 发送停止请求(/api/pools/{poolId}/stop) → 更新按钮状态 → 状态栏显示已停止 → 恢复画布编辑
```

### FR-3: 仿真模式面板

#### FR-3.1: 仿真控制面板

**功能清单：**
- 虚拟时钟显示
- 步数计数器
- 启动/暂停/步进/重置按钮
- 步长选择（1s/1min/5min/1h）
- 速度调节（0.5x - 20x）

**处理流程：**
```
点击启动 → 发送仿真启动请求(/api/pools/{poolId}/simulate/start) → 虚拟时钟开始运行 → 步数计数器递增
点击步进 → 发送单步请求(/api/pools/{poolId}/simulate/step) → 执行一个tick → 更新时钟和步数
点击重置 → 发送重置请求(/api/pools/{poolId}/simulate/reset) → 重置时钟和步数
调整步长/速度 → 更新仿真参数 → 发送到后端(/api/pools/{poolId}/simulate/params)
```

#### FR-3.2: 仿真数据初始化

**功能清单：**
- 加载 100 只 fz 前缀股票到备选池
- 每只股票分配固定 tick 间隔（1-9秒随机值，同股票间隔固定）
- 显示初始化进度

**处理流程：**
```
切换到仿真模式 → 自动加载100只fz股票 → 显示加载进度 → 初始化完成后显示股票数量 → 通知后端初始化仿真数据
```

### FR-4: 回放模式面板

#### FR-4.1: 回放控制面板

**功能清单：**
- 当前时间显示
- 进度条（当前位置/总长度）
- 播放/暂停/步进按钮
- 速度选择（1x/2x/5x/10x/100x/MAX）
- 周期选择（1min/5min/15min/30min/60min/日线）
- 日期区间选择

**处理流程：**
```
选择日期区间 → 发送回放范围请求 → 初始化进度条
点击播放 → 发送回放启动请求 → 更新进度条和时间显示
调整速度/周期 → 更新回放参数 → 发送到后端
```

### FR-5: 事件面板 (EventPanel) - 可视化版

#### FR-5.1: 事件监控窗口

**功能清单：**
- 可拖拽浮窗
- 折叠/展开功能
- 暂停/继续接收事件
- 清空事件
- 两种视图切换：**分类显示** / **全部显示**
  - 两种视图的 **Y 轴语义相同**，均按 9 种事件分类作为垂直分轨
  - **分类显示（矩阵）**：每类事件独占一行，便于按类别查看密度与趋势
  - **全部显示（散点）**：所有事件绘制在同一 Canvas，按分类确定 Y 坐标，便于观察全量事件时间分布
- 分类筛选：点击分类标签可显示/隐藏对应分类事件，筛选后两种视图同步生效

**处理流程：**
```
拖拽浮窗 → 更新位置 → 存储到本地存储
折叠/展开 → 切换面板高度 → 保存状态
暂停/继续 → 控制事件接收 → 更新按钮状态
切换视图 → 使用同一组 (ts, category) 数据渲染分类矩阵或散点分布
分类筛选 → 更新 activeFilters → 重绘当前视图
```

#### FR-5.2: 分类显示视图（矩阵）

**功能清单：**
- 9 种事件分类每类一行：Tick、Bar、Formula、Edge、Transfer、Signal、Order、TTL、System
- 每行左侧显示分类图标、名称、事件计数
- 每行右侧按时间轴分布该分类下的事件图标
- 已发生事件图标为实心样式，排队中事件图标为黄色虚线框样式
- 点击分类行 → 下方详情区显示该分类所有事件文本记录
- 点击单个事件图标 → 下方详情区显示该事件及相关事件文本
- 绘制当前时间线（垂直虚线），标识当前时刻

**处理流程：**
```
接收事件 → 分类 → 按分类聚合 → 计算时间范围 → 在分类行内按 ts 分布图标 → 点击行/图标 → 在详情区渲染事件文本列表
```

**事件分类颜色映射：**
| 分类 | 图标 | 颜色 | 事件类型 |
|------|------|------|---------|
| Tick | 📊 | 灰色 | TickReceived, DataChanged |
| Bar | 📈 | 蓝色 | BarComposed |
| Formula | 🧮 | 绿色 | FormulaEvaluated, StockFiltered |
| Edge | ⚡ | 橙色 | EdgeFired, CrossOver |
| Transfer | 🔄 | 紫色 | TransferExecuted, Executed |
| Signal | 💰 | 红色 | BUY, SELL |
| Order | 📋 | 黄色 | OrderPlaced, OrderFilled, PositionUpdated |
| TTL | ⏰ | 暗红色 | TTLExpired, Timeout, TimerQueued |
| System | 🔧 | 青色 | ModeChanged, TimeAdvanced, PoolLoaded |

#### FR-5.3: 全部显示视图（散点）

**功能清单：**
- Canvas 绘制时间轴（X轴）和分类（Y轴）上的全部事件，Y 轴语义与分类显示视图完全一致
- 所有分类的事件绘制在同一画布，便于观察跨类别的时间关联
- 每个事件显示为对应分类颜色的图标，同分类事件在相同水平轨道上
- 已发生事件显示为实心图标；排队中事件显示为黄色虚线框图标
- 左侧显示分类图标、名称、事件计数（与矩阵视图一致）
- 图标可点击，点击后在下方详情区显示该事件详情
- 底部显示时间刻度，绘制当前时间线（垂直虚线）

**处理流程：**
```
接收事件 → 计算时间范围 → 按 (ts, category) 映射到 Canvas 坐标 → 绘制事件图标 → 点击图标 → 在详情区渲染事件文本
```

#### FR-5.4: 定时器队列

**功能清单：**
- 独立区域显示排队中的 timer 事件（包括未处理的排队事件）
- 顶部 Canvas 绘制定时器事件的时间分布图：X 轴为 fire_at 时间，Y 轴固定为单一轨道
- 时间分布图绘制当前时间线，过期事件用红色虚线框标识
- 下方列表显示预计触发时间、事件类型、股票代码、详情、queue_position
- 可折叠/展开
- 自动清理 60 秒前的过期项

**处理流程：**
```
接收 TimerQueued 事件 → 加入优先队列 → 按 fire_at 排序 → 渲染时间分布图 + 队列列表
```

#### FR-5.5: 事件文本详情

**功能清单：**
- 显示时间戳、事件类型图标、股票代码、详情摘要
- 点击详情条目可高亮并显示该事件详情
- 支持显示排队中事件（带 pending 样式）

**处理流程：**
```
SSE/WebSocket接收事件 → EventPanel.addEvent(event) → 根据类型分配颜色和图标 → 渲染分类矩阵/散点图 → 点击后在详情区渲染事件文本
```

### FR-6: K线与公式面板

#### FR-6.1: K线图表

**功能清单：**
- 股票代码选择
- 周期选择（1分钟/5分钟）
- Canvas绘制K线图
- 显示价格信息

**处理流程：**
```
选择股票代码 → 请求K线数据(/api/stocks/{code}/kline?period={period}) → KlineChart.render() → Canvas绘制
选择周期 → 请求对应周期数据 → 重新绘制图表
```

#### FR-6.2: 公式结果

**功能清单：**
- 显示公式计算结果
- 实时更新

**处理流程：**
```
公式计算完成 → 接收 FormulaEvaluated 事件 → 更新公式结果面板(formulaResult)
```

### FR-7: 配置中心

#### FR-7.1: 配置分类浏览

**功能清单：**
- 左侧分类列表
- 右侧配置表列表
- 搜索功能

**处理流程：**
```
加载配置分类 → 渲染左侧列表(catList) → 点击分类 → 渲染右侧配置表列表(tList)
搜索 → 过滤配置表列表 → 显示匹配结果
```

#### FR-7.2: 配置表编辑

**功能清单：**
- 表格视图
- JSON视图
- 表单视图
- 校验视图

**处理流程：**
```
选择配置表 → 加载配置数据(/api/config/tables/{tableName}) → 根据视图类型渲染 → 编辑后保存(/api/config/tables/{tableName})
```

#### FR-7.3: 热加载

**功能清单：**
- 校验全部配置
- 热加载配置变更

**处理流程：**
```
点击校验 → 发送校验请求(/api/config/validate) → 显示校验结果
点击热加载 → 发送热加载请求(/api/config/reload) → 更新配置缓存 → 刷新界面
```

### FR-8: 公式管理

#### FR-8.1: 公式列表

**功能清单：**
- 搜索公式
- 按分类筛选（指标公式、选股公式、专家系统）
- 公式列表展示

**处理流程：**
```
加载公式列表(/api/formulas) → 渲染列表(formulaList) → 搜索/筛选 → 更新列表显示
```

#### FR-8.2: 公式编辑器

**功能清单：**
- 公式名称、分类、类型、描述
- 公式脚本编辑器
- 参数列表管理（添加/删除参数）
- 保存/测试/删除按钮

**处理流程：**
```
选择公式 → 加载公式数据(/api/formulas/{name}) → 渲染编辑器 → 编辑后保存/测试/删除
```

#### FR-8.3: 公式测试

**功能清单：**
- 选择股票代码和周期
- 执行测试并显示结果
- 支持选股公式批量测试

**处理流程：**
```
点击测试 → 弹出测试对话框 → 选择股票代码和周期 → 执行测试(/api/formulas/{name}/test) → 显示结果
```

### FR-9: 股票池导入导出

#### FR-9.1: 导入功能

**功能清单：**
- DZH格式XML导入
- TDX格式XML导入
- JSON格式导入
- 文件拖拽上传
- 导入预览

**处理流程：**
```
选择文件/拖拽上传 → 解析文件 → 显示导入预览 → 确认导入 → PoolDataManager.importXML()/importTDXXML()/importJSON() → 更新股票池数据
```

#### FR-9.2: 导出功能

**功能清单：**
- DZH格式XML导出
- TDX格式XML导出
- JSON格式导出

**处理流程：**
```
选择导出格式 → PoolDataManager.exportXML()/exportTDXXml()/exportJSON() → 请求后端导出(/api/dzh/export /api/tdx/export /api/json/export) → 下载文件
```

#### FR-9.3: 保存功能

**功能清单：**
- 保存到服务器（API）
- 本地存储（localStorage）
- 撤销/重做功能

**处理流程：**
```
点击保存 → PoolDataManager.saveToAPI() → 发送保存请求(/api/pools) → 同时保存到本地存储(localStorage)
撤销 → PoolDataManager.undo() → 恢复上一状态快照 → _notify() → 画布重绘
重做 → PoolDataManager.redo() → 恢复下一状态 → _notify() → 画布重绘
```

### FR-10: 股票池列表

#### FR-10.1: 列表展示

**功能清单：**
- 通达信池列表
- 大智慧池列表
- 示例池列表
- 已保存池列表
- 搜索功能

**处理流程：**
```
加载股票池列表 → 渲染四个标签页(tdxpool/dzhpool/examples/saved) → 搜索 → 过滤列表
```

#### FR-10.2: 列表操作

**功能清单：**
- 加载股票池
- 删除股票池
- 重命名股票池

**处理流程：**
```
点击加载 → PoolDataManager.loadFromAPI()/loadTDXPool() → 更新画布
点击删除 → 弹出确认对话框 → 发送删除请求(/api/tdx/pools/{name}) → 更新列表
点击重命名 → 弹出重命名对话框 → 发送重命名请求 → 更新列表
```

### FR-11: 上下文菜单

#### FR-11.1: 画布右键菜单

**功能清单：**
- 添加节点（子菜单：备选池、转移条件、状态池、丢弃池、文字标签）
- 属性
- 综合设置
- 复制/剪切/粘贴
- 层级调整（置于顶层/底层）
- 删除

**处理流程：**
```
右键点击 → 显示上下文菜单(contextMenu) → 点击菜单项 → 执行对应操作
```

#### FR-11.2: 对话框

**功能清单：**
- 线条宽度设置(lineWidthDialog)
- 说明文字设置(descTextDialog)
- 清除确认(clearConfirmDialog)
- 选择品种(selectStockDialog)
- 选择板块(selectBlockDialog)

**处理流程：**
```
触发对话框 → 显示对话框 → 用户输入 → 确认/取消 → 执行操作
```

### FR-12: 状态栏

#### FR-12.1: 状态显示

**功能清单：**
- 就绪状态显示(statusText)
- 节点数/连线数显示(statusNodes/statusEdges)
- 鼠标坐标显示(statusCoords)
- 当前时间显示(statusTime)
- 缩放比例显示(statusZoom)

**处理流程：**
```
实时更新 → 监听画布事件 → 更新节点数/连线数/缩放比例
监听鼠标移动 → 更新鼠标坐标
定时器 → 更新当前时间
```

### FR-13: 响应式布局

#### FR-13.1: 多设备支持

**功能清单：**
- 桌面端布局（1200px+）：三栏布局（左侧列表 + 中间画布 + 右侧属性面板）
- 平板端布局（768px-1199px）：双栏布局或可折叠面板
- 移动端布局（<768px）：底部导航 + 浮动按钮

**处理流程：**
```
窗口大小变化 → CSS媒体查询 → 调整布局 → 显示/隐藏元素
```

#### FR-13.2: 移动端优化

**功能清单：**
- 移动端浮动按钮（添加备选池、条件、状态池）
- 移动端底部导航（画布/属性切换）
- 汉堡菜单（溢出按钮）

**处理流程：**
```
小屏幕设备 → 显示移动端布局 → 点击浮动按钮 → 执行对应操作
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
- 操作有反馈（toast提示、状态变化）

### NFR-3: 响应式
- 支持桌面端（1200px+）
- 支持平板端（768px-1199px）
- 支持移动端（<768px）

### NFR-4: 可维护性
- 代码结构清晰，模块化设计
- 遵循表驱动设计原则
- 配置表与代码分离
- 消除重复代码（已合并为三大JS文件）

### NFR-5: 兼容性
- DZH格式完整兼容（cell_type: 200/201/202/4）
- TDX格式完整兼容（cell_type: 7/8/3）
- 浏览器兼容：Chrome ≥ 80, Firefox ≥ 75

## Constraints

### Technical
- 前端框架：原生 JavaScript（无第三方框架）
- 构建工具：无（直接引用 JS/CSS 文件）
- 浏览器兼容：Chrome ≥ 80, Firefox ≥ 75
- ES Module：IIFE封装，全局变量导出

### Dependencies
- 后端 API：`/api/*` 端点
- WebSocket：`/ws/events` 事件推送
- 公式插件：HQChartPy2（Python后端）

### Business
- 仿真模式下所有股票代码必须用'fz'替代原市场代码
- 数据tick更新间隔为1-9秒随机值，同股票间隔固定，不同股票间隔不同
- 程序必须记录所有事件并在运行界面浮窗显示
- 界面需简洁清晰地图形化显示不同事件
- 界面操作需简便，解决所有使用问题

## Assumptions
- 后端 API 已实现并正常运行
- WebSocket 连接稳定可靠
- 用户具有基本的股票池操作知识
- 配置表（cell_type_registry、field_definitions、edge_strategies等）已从后端加载

## Acceptance Criteria

### AC-1: 股票池设计功能完整
- **Given**：用户进入主页并加载示例池
- **When**：用户创建节点、连接边、配置属性
- **Then**：所有操作正确执行，画布实时更新，属性面板显示正确
- **Verification**：`human-judgment`

### AC-2: 四种模式切换正确
- **Given**：股票池已加载
- **When**：用户在设计/实盘/回放/仿真模式间切换
- **Then**：模式指示器更新（颜色和标签），对应控制面板显示/隐藏正确
- **Verification**：`programmatic`

### AC-3: 仿真模式运行正确
- **Given**：切换到仿真模式并点击开始
- **When**：仿真运行 ≥300 秒虚拟时钟
- **Then**：事件面板显示完整事件链（Tick→Bar→Formula→Edge→Transfer→Signal→Order），股票正确流转（备选池→条件→状态池→C池）
- **Verification**：`programmatic`

### AC-4: 事件面板可视化展示
- **Given**：仿真运行中
- **When**：事件到达并显示在事件面板
- **Then**：分类矩阵中每类事件以图标流展示，已发生和排队中事件区分显示；点击分类或图标在下方显示事件文本记录；定时器队列显示排队中的 timer 事件
- **Verification**：`programmatic`

### AC-5: 公式管理功能完整
- **Given**：用户进入公式管理页面
- **When**：用户创建、编辑、测试公式
- **Then**：所有操作正确执行，测试结果正确显示
- **Verification**：`human-judgment`

### AC-6: 配置中心功能完整
- **Given**：用户进入配置中心
- **When**：用户查看、编辑、校验配置表
- **Then**：所有操作正确执行，热加载生效，界面实时更新
- **Verification**：`human-judgment`

### AC-7: 导入导出功能完整
- **Given**：股票池已设计完成
- **When**：用户导入/导出不同格式文件（DZH XML、TDX XML、JSON）
- **Then**：导入导出成功，数据一致性保证
- **Verification**：`programmatic`

### AC-8: 响应式布局正确
- **Given**：页面在不同设备上打开
- **When**：调整浏览器窗口大小（1200px+、768px-1199px、<768px）
- **Then**：布局自适应，功能正常使用，移动端浮动按钮和底部导航显示正确
- **Verification**：`human-judgment`

### AC-9: 综合设置窗口功能完整
- **Given**：股票池包含多个节点和边
- **When**：用户打开综合设置窗口并编辑字段
- **Then**：三列布局正确显示（流程标识、条件/属性、时序/操作），字段编辑器功能完整
- **Verification**：`human-judgment`

### AC-10: DZH/TDX格式兼容
- **Given**：用户导入DZH和TDX格式的股票池
- **When**：查看画布渲染和属性配置
- **Then**：DZH节点（200/201/202/4）和TDX节点（7/8/3）正确渲染，属性面板显示对应配置项
- **Verification**：`programmatic`

### AC-11: 事件面板浮动窗口功能
- **Given**：事件面板已打开
- **When**：用户拖拽、折叠、切换视图、清空事件
- **Then**：拖拽位置和折叠/展开状态保存到本地存储，分类矩阵/散点分布视图可切换，清空功能正常
- **Verification**：`human-judgment`

### AC-12: Playwright浏览器验证
- **Given**：前端服务正常运行
- **When**：使用Playwright执行验证脚本
- **Then**：所有验证点通过，包括模式切换、仿真运行、事件接收、导入导出等
- **Verification**：`programmatic`

## Open Questions
- [ ] 是否需要添加更多的快捷键支持？
- [ ] 是否需要支持多语言国际化？
- [ ] 是否需要添加股票池模板功能？
- [ ] 是否需要添加用户权限管理？
- [ ] 是否需要添加股票池运行历史记录功能？

## 功能与代码映射表

| 功能模块 | 主要文件 | 核心类/对象 | 关键方法/属性 |
|---------|---------|------------|-------------|
| 画布引擎 | `web/js/canvas.js` | `FlowCanvas` | `render()`, `fitToContent()`, `setEdgeLineType()`, `zoomIn()`, `_renderNodes()`, `_renderEdges()` |
| DZH颜色工具 | `web/js/canvas.js` | `DZH_COLOR_UTILS` | `dzhColorToCss()`, `dzhIntToCssHex()` |
| 数据管理 | `web/js/app.js` | `PoolDataManager` | `addNode()`, `removeNode()`, `addEdge()`, `removeEdge()`, `updateNode()`, `updateEdge()`, `undo()`, `redo()`, `importXML()`, `exportXML()` |
| LRU缓存 | `web/js/app.js` | `LRUCache` | `get()`, `set()`, `has()`, `delete()`, `clear()` |
| K线图表 | `web/js/app.js` | `KlineChart`, `BaseChart` | `render()`, `update()` |
| 属性面板 | `web/js/ui.js` | `TableDrivenPanel` | `render()`, `update()` |
| 组件注册表 | `web/js/ui.js` | `ComponentRegistry` | `register()`, `get()`, `registerFromConfig()` |
| 校验引擎 | `web/js/ui.js` | `ValidationEngine` | `validate()`, `registerValidator()` |
| 数据绑定 | `web/js/ui.js` | `DataBinder` | `get()`, `set()`, `decodeAttrFlags()`, `encodeAttrFlags()` |
| 事件面板 | `web/js/event-panel.js` | `EventPanel` | `addEvent()`, `clear()`, `renderMatrix()`, `renderScatter()`, `renderTimerQueue()`, `renderDetailForCategory()`, `renderDetailForEvent()` |
| 公式管理 | `web/js/app.js` | `FormulaEditor` | `load()`, `save()`, `test()` |
| 配置管理 | `web/js/app.js` | `ConfigManager` | `loadCategory()`, `loadTable()`, `saveTable()`, `validate()`, `reload()` |
| 综合设置 | `web/js/app.js` | `ComprehensiveSettings` | `render()`, `open()`, `close()`, `updateField()` |
| 主界面 | `web/index.html` | DOM元素 | 导航、画布、属性面板、事件面板、模式面板 |
| 样式 | `web/css/styles.css` | CSS规则 | 布局、画布、工具栏、模态框、响应式 |

## API端点清单

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/pools` | GET/POST | 股票池列表/创建 |
| `/api/pools/{poolId}` | GET/PUT/DELETE | 股票池详情/更新/删除 |
| `/api/pools/{poolId}/start` | POST | 启动运行 |
| `/api/pools/{poolId}/pause` | POST | 暂停运行 |
| `/api/pools/{poolId}/stop` | POST | 停止运行 |
| `/api/pools/{poolId}/simulate/start` | POST | 启动仿真 |
| `/api/pools/{poolId}/simulate/step` | POST | 仿真步进 |
| `/api/pools/{poolId}/simulate/reset` | POST | 重置仿真 |
| `/api/pools/{poolId}/simulate/params` | POST | 更新仿真参数 |
| `/api/dzh/import-and-save` | POST | DZH XML导入 |
| `/api/dzh/export` | POST | DZH XML导出 |
| `/api/tdx/import` | POST | TDX XML导入 |
| `/api/tdx/export` | POST | TDX XML导出 |
| `/api/tdx/pools` | GET/POST | TDX股票池列表/创建 |
| `/api/tdx/pools/{name}` | GET/PUT/DELETE | TDX股票池详情/更新/删除 |
| `/api/json/import` | POST | JSON导入 |
| `/api/json/export` | POST | JSON导出 |
| `/api/registry/cell-types` | GET | cell_type_registry |
| `/api/registry/modules` | GET | modules |
| `/api/registry/dzh-type-map` | GET | dzh_type_map |
| `/api/registry/defaults` | GET | defaults |
| `/api/registry/flow-modes` | GET | flow_mode_registry |
| `/api/registry/edge-strategies` | GET | edge_strategies |
| `/api/registry/field-definitions` | GET | field_definitions |
| `/api/config/tables/{tableName}` | GET/PUT | 配置表读取/更新 |
| `/api/config/validate` | POST | 校验全部配置 |
| `/api/config/reload` | POST | 热加载配置 |
| `/api/formulas` | GET/POST | 公式列表/创建 |
| `/api/formulas/{name}` | GET/PUT/DELETE | 公式详情/更新/删除 |
| `/api/formulas/{name}/test` | POST | 公式测试 |
| `/api/stocks/{code}/kline` | GET | K线数据 |
| `/ws/events` | WebSocket | 事件推送 |

## WebSocket事件类型清单

| 事件类型 | 分类 | 说明 |
|---------|------|------|
| `DataChanged` | Tick | 数据变化（tick） |
| `BarComposed` | Bar | K线合成 |
| `FormulaEvaluated` | Formula | 公式计算完成 |
| `StockFiltered` | Formula | 股票筛选完成 |
| `EdgeFired` | Edge | 边触发 |
| `CrossOver` | Edge | 跨边转移 |
| `TransferExecuted` | Transfer | 转移执行 |
| `Executed` | Transfer | 执行完成 |
| `BUY` | Signal | 买入信号 |
| `SELL` | Signal | 卖出信号 |
| `OrderPlaced` | Order | 订单提交 |
| `OrderFilled` | Order | 订单成交 |
| `PositionUpdated` | Order | 持仓更新 |
| `TTLExpired` | TTL | TTL过期 |
| `Timeout` | TTL | 超时 |
| `ModeChanged` | System | 模式变化 |
| `TimeAdvanced` | System | 时间推进 |
| `PoolLoaded` | System | 股票池加载 |
| `EventLogged` | System | 事件记录 |