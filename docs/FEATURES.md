> **历史参考文档**：执行流以 `SIMPLIFIED_EXECUTION.md` 为准。本文档仅作历史参考。

# MetaCore 股票池平台 — 功能清单文档

> 版本: v39 | 更新日期: 2026-06-07

---

## 一、平台概述

MetaCore 是一个基于表驱动架构的股票池可视化编辑与执行平台，同时支持**大智慧(DZH)**和**通达信(TDX)**两种股票池 XML 格式。前端采用 xyflow 风格的单页应用架构，后端基于 FastAPI，所有业务逻辑由 JSON 配置表驱动。

### 核心设计原则

| 原则 | 说明 |
|------|------|
| 表驱动 | 节点类型、面板布局、边策略、属性所有权、行为规则均由 JSON 配置表驱动 |
| 配置驱动 UI | 属性面板零硬编码，所有字段布局从 API 动态获取 |
| 双格式兼容 | 同时支持 DZH 和 TDX 两种 XML 格式，通过 pool_type 路由到不同执行器 |
| 三模式一致 | 设计/运行/回放界面一致，一进入就是完整界面 |
| 响应式 | 同时兼容电脑和移动端 |

---

## 二、前端功能

### 2.1 模块体系

| 模块 | 文件 | 行数 | 职责 |
|------|------|------|------|
| 主控制器 | `js/main.js` | ~2067 | 应用入口，协调所有模块，绑定 UI 事件，管理应用状态 |
| 画布引擎 | `js/canvas.js` | ~2392 | 节点/边渲染、交互、缩放、平移、框选、连线拖拽、小地图 |
| 数据管理 | `js/pool-data.js` | ~1332 | 数据 CRUD、导入导出、撤销重做、校验、缓存、热重载 |
| 属性面板 | `js/table-driven-panel.js` | ~2055 | 配置驱动的属性面板引擎，22 种组件类型，字段联动，验证 |
| 高亮管理 | `js/highlight-manager.js` | ~244 | 运行/回放模式的实时高亮，WebSocket+轮询双通道 |
| 页面结构 | `index.html` | ~663 | HTML 骨架、所有 UI 组件、模态框 |
| 样式表 | `css/style.css` | ~4804 | 全部组件样式、暗色主题、3 个响应式断点 |

### 2.2 画布引擎 (FlowCanvas)

#### 渲染能力

| 功能 | 说明 |
|------|------|
| 节点渲染 | 13 种节点渲染器，支持 DZH 和 TDX 两种格式 |
| 边渲染 | SVG 路径 + 命中区域 + 策略标签 |
| 贝塞尔曲线 | 连线默认使用贝塞尔曲线 |
| SVG 网格背景 | 20px 小格 + 100px 大格 |
| 小地图 | Canvas 渲染(180x120px)，视口矩形，点击导航 |
| 股票数据表 | 支持 DZH 和 TDX 格式，>100 条虚拟滚动 |

#### 节点类型与形状

| 类型 | 形状 | 颜色 | 说明 |
|------|------|------|------|
| stock_state_pool (200) | 矩形 + 黄金分割渐变 | 蓝色系 | DZH 状态池 |
| market_source (202) | 圆柱形 + 38.2%光照渐变 | 绿色系 | DZH 备选池 |
| transfer_condition (201) | 朝右三角形 | 橙色系 | DZH 转移条件 |
| discard_pool (4) | 圆角矩形 | 红色系 | 丢弃池 |
| result_pool (203) | 矩形 | 紫色系 | 特殊池/结果池 |
| text_label (1) | 简单文本 | 灰色 | 文字标签 |
| container (2) | 虚线矩形 | 灰色 | 容器 |
| state_column (3) | 窄矩形 | 灰色 | 状态列 |
| drawing_tool (5) | — | — | 绘图工具 |
| flow_arrow (6) | — | — | 连接箭头 |
| tdx_candidate (tdx_7) | 圆柱形 + SVG 渐变 | 绿色系 | TDX 备选池 |
| tdx_state_pool (tdx_8) | 矩形 + 黄金分割渐变 | 蓝色系 | TDX 状态池 |
| tdx_condition (tdx_3) | 朝右三角形 | 橙色系 | TDX 转移条件 |

#### SVG 形状细节

- **圆柱形**: SVG ellipse + rect + linearGradient，顶部椭圆实心时填充、空心时不填充
- **三角形**: SVG polygon，朝右方向 `0,0 0,h w,h/2`
- **矩形渐变**: SVG linearGradient，38.2% 位置高光，10 个色阶平滑过渡（光照效果）

#### 边策略颜色

| 策略 | 颜色 | 说明 |
|------|------|------|
| pass | #27ae60 绿 | 直通传递 |
| copy | #2980b9 蓝 | 复制传递 |
| overwrite | #e67e22 橙 | 覆盖写入 |
| move | #c0392b 红 | 移动 |
| force | #9b59b6 紫 | 强制转移 |

#### 交互操作

| 操作 | 行为 |
|------|------|
| 滚轮缩放 | 以光标为中心，0.1x-5x 范围 |
| Shift+左键拖拽 | 平移画布 |
| 中键拖拽 | 平移画布 |
| 左键空白拖拽 | 框选多个节点 |
| 节点拖拽 | 单选拖拽，选中节点批量拖动 |
| 8 方向缩放手柄 | nw/n/ne/w/e/sw/s/se |
| Source 手柄(蓝色右侧) | 拖拽发起连线 |
| Target 手柄(绿色左侧) | 接收连线 |
| 右键 | 上下文菜单 |
| 双击节点 | 显示属性面板 |
| 小地图点击 | 导航到对应位置 |

### 2.3 数据管理 (PoolDataManager)

#### 数据操作

| 功能 | 说明 |
|------|------|
| `setData()` | 设置数据(深拷贝+规范化) |
| `addNode(cellType, position)` | 创建节点(类型信息+默认参数) |
| `removeNode(id)` | 删除节点及其连接的边 |
| `addEdge(fromId, toId)` | 创建边(默认参数) |
| `removeEdge(id)` | 删除边 |
| `duplicateNode(id)` | 复制节点(偏移位置) |
| `updateNodeParams()` / `updateEdge()` | 更新(深合并) |
| `bringToFront()` / `sendToBack()` | Z 序管理 |

#### 导入导出

| 功能 | API 端点 | 说明 |
|------|----------|------|
| 导入大智慧 XML | `/api/dzh/import-and-save` | 文件上传，可选立即执行 |
| 导入通达信 XML | `/api/dzh/tdx/import` | 文件上传 |
| 导出大智慧 XML | `/api/dzh/export` | 下载 XML 文件 |
| 导出通达信 XML | `/api/tdx/export` | 下载 XML 文件 |
| 加载通达信池 | `/api/tdx/pools/{name}/load` | 从 tdxpool 目录加载 |
| 加载大智慧池 | `/api/files/dzhpool/{filename}/load` | 从 dzhpool 目录加载 |
| 加载示例 | `/api/files/examples/{filename}/load` | 从 examples 目录加载 |

#### 撤销重做

| 功能 | 说明 |
|------|------|
| `undo()` | 快照式撤销，最多 50 步历史 |
| `redo()` | 快照式重做 |
| `_pushHistory()` | 每次操作前自动推入快照 |

#### 剪贴板

| 功能 | 说明 |
|------|------|
| `copyToClipboard()` | 复制选中节点 |
| `cutToClipboard()` | 剪切选中节点 |
| `pasteFromClipboard()` | 粘贴节点(偏移位置) |

#### 校验

| 功能 | 说明 |
|------|------|
| `validateFlow(fromId, toId)` | 连接规则校验(source→condition→target) |
| `_normalizeData()` | 规范化节点位置、映射 cell_type、处理 TDX 字段 |

#### 缓存与热重载

| 功能 | 说明 |
|------|------|
| LRU 缓存 + TTL | 数据请求缓存，支持过期清理 |
| `checkCacheVersion()` | 通过 `/api/registry/cache-version` 检测热重载 |
| `clearAllCaches()` | 清除所有前端缓存 |

### 2.4 属性面板引擎 (TableDrivenPanel)

#### 22 种组件类型

| 组件 | 用途 |
|------|------|
| `text_input` | 文本输入 |
| `number_input` | 数字输入 |
| `select` | 下拉选择 |
| `color_picker` | 颜色选择器 |
| `flag_group` | 位标志组(复选框组) |
| `action_compound` | 动作复合编辑器(enter/exit) |
| `market_selector` | 市场选择器 |
| `base64_readonly` | Base64 只读显示 |
| `indicator_select` | 指标选择 |
| `begint_input` | 起始时间输入 |
| `readonly_datetime` | 只读日期时间 |
| `stock_list_editor` | 股票列表编辑器 |
| `indicator_browser` | 指标浏览器 |
| `readonly` | 只读字段 |
| `flow_info` | 流转信息 |
| `flow_mode_display` | 流转模式显示 |
| `transfer_mode` | 转移模式 |
| `sector_tree` | 板块树 |
| `stock_source_editor` | 股票源编辑器 |
| `reload_mode` | 重载模式 |
| `formula_editor` | 公式编辑器 |
| `stock_data_table` | 股票数据表 |

#### 面板显示

| 功能 | 说明 |
|------|------|
| `showForNode(nodeId)` | 显示节点属性(解析节点、确定池类型、从 API 获取面板配置) |
| `showForEdge(edgeId)` | 显示边属性(注入源/目标标签) |
| `showForPool(poolMeta)` | 显示池元数据 |
| `showPlaceholder()` | 空状态占位 |
| `setReadOnly(bool)` | 切换只读模式 |

#### 字段联动与验证

| 功能 | 说明 |
|------|------|
| `_handleLinkage()` | 基于 depends_on/active_when 的字段可见性联动 |
| `_validateField()` | 字段验证(required, min, max, pattern, custom) |
| `_handleChange()` | 处理所有字段变更(标志、市场、动作、颜色、转移模式、数字、选择、文本) |

#### 持久化

| 功能 | 说明 |
|------|------|
| `_notifyChange()` | 回写 poolData、触发 onPropertyChange |
| `_persistChange()` | 防抖 300ms 后自动保存到后端 API |
| `_startHotReload()` | 配置热重载轮询 |

### 2.5 高亮管理 (HighlightManager)

| 功能 | 说明 |
|------|------|
| WebSocket 连接 | `ws://{host}/ws/highlight`，3 秒超时后降级为轮询 |
| HTTP 轮询降级 | 500ms 间隔，`/api/highlight-events` |
| `startHighlight()` | 高亮节点/边(带自动隐藏计时器) |
| `stopHighlight()` | 移除高亮 |
| `pauseAutoHide()` / `resumeAutoHide()` | 回放暂停/播放时控制自动隐藏 |
| `setReplayMode()` | 回放模式下更长默认持续时间 |

### 2.6 侧边栏

| 功能 | 说明 |
|------|------|
| 3 个标签页 | 通达信股票池、大智慧股票池、实例 |
| 文件过滤 | 只显示 xml/json 配置文件，不显示 dll/png 等 |
| 文件搜索 | 按名称过滤当前标签页文件 |
| 目录对应 | tdxpool / dzhpool / examples 三个目录 |
| 点击加载 | 点击文件直接加载到画布 |

### 2.7 工具栏

| 按钮 | 功能 |
|------|------|
| 侧边栏切换 | 展开/折叠侧边栏 |
| 新建 | 创建新池(选择 DZH/TDX 格式) |
| 添加节点 | 下拉选择节点类型添加 |
| 导入 | 上传 XML 文件(支持 DZH/TDX) |
| 导出 | 导出为 XML 文件下载 |
| 保存 | 保存到后端 |
| 撤销 / 重做 | 撤销/重做操作 |
| 适配 | 画布适配内容 |
| 执行顺序 | 切换执行顺序显示 |
| 流转模式 | 切换流转模式(F 键) |
| 运行 | 执行股票池 |
| 回放 | 启动回放会话 |
| 通达信池 | TDX 池浏览器 |
| 规则编辑器 | 条件配置编辑器 |

### 2.8 上下文菜单

| 菜单项 | 功能 |
|------|------|
| 添加节点 | 子菜单：5 种节点类型 |
| 属性 | 显示属性面板 |
| 复制 / 剪切 / 粘贴 | 剪贴板操作 |
| 置于顶层 / 置于底层 | Z 序管理 |
| 删除 | 删除节点/边(红色危险项) |

### 2.9 模态框

| 模态框 | 功能 |
|------|------|
| 新建池 | 名称、格式选择(DZH/TDX) |
| 导入 | 文件上传、格式选择 |
| 股票列表 | 可排序表格、颜色编码、K 线查看 |
| K 线图 | K 线图表展示 |
| 列编辑器 | 列选择、拖拽排序 |

### 2.10 回放面板

| 控件 | 功能 |
|------|------|
| 播放/暂停/步进 | 回放控制 |
| 速度选择 | 1x/2x/5x/10x/100x/MAX |
| 周期选择 | 1min/5min/day |
| 进度条 | 可拖拽定位 |
| 关闭 | 关闭回放会话 |

### 2.11 三种运行模式

| 模式 | 说明 |
|------|------|
| 设计(design) | 完整编辑能力，属性可编辑 |
| 运行(run) | 执行股票池，实时高亮，属性只读 |
| 回放(replay) | K 线回放，播放/暂停/步进/变速，属性只读 |

### 2.12 键盘快捷键

| 快捷键 | 功能 |
|--------|------|
| `Ctrl+S` | 保存 |
| `Ctrl+Z` | 撤销 |
| `Ctrl+Y` / `Ctrl+Shift+Z` | 重做 |
| `Ctrl+C` | 复制节点 |
| `Ctrl+V` | 粘贴节点 |
| `Ctrl+X` | 剪切节点 |
| `Delete` / `Backspace` | 删除选中节点/边 |
| `Escape` | 清除选择，退出流转模式 |
| `F` | 切换流转模式 |
| `+` / `=` | 放大 |
| `-` | 缩小 |

### 2.13 响应式设计

| 断点 | 布局 | 说明 |
|------|------|------|
| 桌面(>1024px) | 三栏布局 | 侧边栏 240px + 画布 + 属性面板 320px |
| 平板(769-1024px) | 抽屉式 | 侧边栏左抽屉 + 属性面板右抽屉，工具栏仅图标 |
| 移动(<768px) | 单栏 | 侧边栏/属性面板全屏覆盖，底部标签栏，FAB 按钮 |

### 2.14 其他 UI 特性

| 特性 | 说明 |
|------|------|
| Toast 通知 | 成功(绿色)/错误(红色)动画通知 |
| 状态栏 | 节点/边计数、坐标、时间、缩放比例 |
| 模式指示器 | 运行=绿色，回放=橙色，固定顶部居中 |
| 下拉菜单 | position:fixed 不被工具栏裁剪 |
| 移动端溢出菜单 | 汉堡菜单 + 溢出菜单 |

---

## 三、后端功能

### 3.1 API 端点总览

#### 文件管理 API

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/api/files/tdxpool` | 列出 tdxpool 目录文件 |
| GET | `/api/files/dzhpool` | 列出 dzhpool 目录文件 |
| GET | `/api/files/examples` | 列出 examples 目录文件 |
| GET | `/api/files/dzhpool/{filename}/load` | 加载 dzhpool 中的 XML 文件 |
| GET | `/api/files/examples/{filename}/load` | 加载 examples 中的 JSON 文件 |

#### 股票池 CRUD API

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | `/api/pools` | 创建股票池 |
| GET | `/api/pools` | 列出所有股票池 |
| GET | `/api/pools/{pool_id}` | 获取股票池详情 |
| PUT | `/api/pools/{pool_id}` | 更新股票池 |
| DELETE | `/api/pools/{pool_id}` | 删除股票池 |

#### 执行 API

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | `/api/pools/{pool_id}/run` | 执行股票池(mock/real) |
| POST | `/api/pools/{pool_id}/test` | 测试单个节点 |
| GET | `/api/pools/{pool_id}/events` | 获取最近执行事件 |
| POST | `/api/dzh/execute-pool` | 执行 DZH 池 |
| POST | `/api/tdx/execute-pool` | 执行 TDX 池 |

#### DZH 大智慧 API

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | `/api/dzh/import` | 导入 DZH XML |
| POST | `/api/dzh/import-and-save` | 导入并持久化 |
| POST | `/api/dzh/export` | 导出 DZH XML |
| GET | `/api/dzh/cell-types` | 获取 cell 类型元数据 |
| GET | `/api/dzh/flow-schema` | 获取 Flow 字段定义 |
| GET | `/api/dzh/markets` | 获取市场定义 |
| GET | `/api/dzh/schedules` | 获取计划模板 |
| GET | `/api/dzh/col-definitions` | 获取列定义 |
| GET | `/api/dzh/modules` | 获取模块定义 |
| GET | `/api/dzh/formula-list` | 获取公式列表 |
| POST | `/api/dzh/validate-formula` | 校验公式 |
| POST | `/api/dzh/validate-roundtrip` | 往返校验 |
| GET/POST/PUT/DELETE | `/api/dzh/cells[/{cell_id}]` | Cell CRUD |
| GET/POST/PUT/DELETE | `/api/dzh/flows[/{flow_id}]` | Flow CRUD |
| GET | `/api/dzh/cells/{cell_id}/stocks` | 获取 cell 股票数据 |

#### TDX 通达信 API

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/api/tdx/pools` | 列出 TDX 池 |
| GET | `/api/tdx/pools/{name}/load` | 加载 TDX 池 |
| POST | `/api/tdx/pools` | 创建 TDX 池 |
| PUT | `/api/tdx/pools/{name}` | 保存 TDX 池 |
| DELETE | `/api/tdx/pools/{name}` | 删除 TDX 池 |
| POST | `/api/tdx/export` | 导出 TDX XML |
| POST | `/api/dzh/tdx/import` | 上传导入 TDX XML |

#### 回放 API

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | `/api/dzh/replay/load` | 加载 K 线数据 |
| POST | `/api/dzh/replay/start` | 启动回放 |
| POST | `/api/dzh/replay/pause` | 暂停回放 |
| POST | `/api/dzh/replay/step` | 步进一根 K 线 |
| POST | `/api/dzh/replay/speed` | 设置回放速度 |
| GET | `/api/dzh/replay/snapshot` | 获取回放快照 |
| GET | `/api/dzh/replay/progress` | 获取回放进度 |

#### 模拟 API

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | `/api/sim/start` | 启动模拟会话 |
| POST | `/api/sim/control` | 控制模拟(pause/resume/step/stop/jump/speed) |
| GET | `/api/sim/state` | 获取模拟状态 |
| GET | `/api/sim/events` | 获取模拟事件日志 |

#### 仿真模式 API（池级别）

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | `/api/pool/{name}/sim/init` | 初始化仿真会话 |
| POST | `/api/pool/{name}/sim/start` | 执行一步仿真（别名） |
| POST | `/api/pool/{name}/simulation/step` | 执行一步仿真 |
| POST | `/api/pool/{name}/sim/pause` | 暂停仿真 |
| POST | `/api/pool/{name}/sim/resume` | 恢复仿真 |
| POST | `/api/pool/{name}/sim/stop` | 停止并清理仿真 |
| GET | `/api/pool/{name}/sim/state` | 获取仿真状态快照 |
| POST | `/api/pool/{name}/sim/speed` | 设置速度倍数（0.5x ~ 20x） |

#### 表驱动 API

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/api/v1/table/layouts` | 列出所有 UI 布局 |
| GET | `/api/v1/table/layouts/{layout_id}` | 获取指定布局 |
| POST | `/api/v1/table/panel` | 生成面板配置 |
| POST | `/api/v1/table/panel/apply` | 应用字段变更 |
| POST | `/api/v1/table/panel/validate` | 校验字段值 |
| POST | `/api/v1/table/reload` | 触发配置热加载 |
| GET | `/api/v1/table/validate` | 校验所有配置表 |
| GET | `/api/v1/table/validate/integrity` | 完整配置完整性校验 |
| GET | `/api/v1/table/status` | 获取引擎状态 |
| GET | `/api/v1/table/enums/{pool_type}` | 获取枚举数据 |
| GET | `/api/v1/table/ownership/{pool_type}` | 获取属性所有权 |
| GET | `/api/v1/table/ownership/{pool_type}/{node_type}` | 检查节点类型属性所有权 |
| POST | `/api/v1/table/ownership/validate` | 校验数据属性所有权 |
| GET | `/api/v1/table/rules` | 列出行为规则 |
| POST | `/api/v1/table/rules` | 创建/更新规则 |
| DELETE | `/api/v1/table/rules/{rule_id}` | 删除规则 |
| POST | `/api/v1/table/rules/export` | 批量导出规则 |
| POST | `/api/v1/table/rules/reorder` | 重排规则优先级 |

#### 注册表 API

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/api/registry/cell-types` | 节点类型注册表 |
| GET | `/api/registry/modules` | 模块定义 |
| GET | `/api/registry/dzh-type-map` | DZH 类型映射 |
| GET | `/api/registry/defaults` | 默认值配置 |
| GET | `/api/registry/flow-modes` | 流转模式注册表 |
| GET | `/api/registry/edge-strategies` | 边策略配置 |
| GET | `/api/registry/cache-version` | 缓存版本号 |

#### 元数据 API

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/api/meta/modules` | 获取所有模块定义 |
| GET | `/api/meta/conditions` | 获取所有条件/调度规则 |
| GET | `/api/meta/engines` | 获取引擎索引 |

### 3.2 核心引擎

#### MetaEngine (engine.py)

| 方法 | 功能 |
|------|------|
| `run_pool()` | 根据 pool_type 自动路由到 DZH/TDX 执行器 |
| `execute_pool()` | 执行 DZH 格式股票池 |
| `run_tdx_pool()` | 执行 TDX 格式股票池 |
| `get_panel_config()` | 生成面板配置 |
| `apply_field_change()` | 应用字段变更+规则联动 |
| `validate_field()` | 校验字段值 |
| `decode_attr_flags()` | 解码位标志 |
| `encode_attr_flags()` | 编码位标志 |
| `fire_rules()` | 触发规则执行 |
| `prefetch_klines_for_pool()` | 预取 K 线数据 |

#### ConfigStore (table_engine.py)

| 方法 | 功能 |
|------|------|
| `load_all()` | 加载所有 JSON 配置表 |
| `get_layout_for_type()` | 按节点类型+池类型查找布局(支持别名映射、TDX 映射、target_type 数组匹配) |
| `get_rules()` | 获取行为规则(支持标签过滤+优先级排序) |
| `check_hot_reload()` | 三级校验热加载(语法/逻辑/业务规则，失败保留旧配置) |
| `rollback_config()` | 回滚配置到指定版本 |

#### PanelGenerator (table_engine.py)

| 方法 | 功能 |
|------|------|
| `generate_panel()` | 生成面板描述(含属性所有权 disabled 标记) |
| `apply_change()` | 应用字段变更(支持 flag_group/action_compound 特殊处理) |
| `compute_field_visibility()` | 计算字段可见性(depends_on/active_when 联动) |
| `validate_field()` | 校验字段值(min/max/required) |

#### PropertyOwnershipManager (table_engine.py)

| 方法 | 功能 |
|------|------|
| `get_blocked_attrs()` | 获取池类型封锁属性列表 |
| `is_attr_allowed()` | 检查属性是否允许编辑 |
| `get_allowed_attrs()` | 获取允许编辑的属性列表 |
| `get_disabled_fields()` | 获取应禁用的字段列表 |
| `filter_data()` | 过滤不属于当前池类型的属性 |

### 3.3 DZH XML 解析器 (dzh_xml_raw.py)

#### 解析的 Cell 类型

| DZH type | 节点类型名 | 分类 |
|----------|-----------|------|
| 1 | text_label | 装饰 |
| 2 | container | 布局 |
| 3 | state_column | 布局 |
| 4 | discard_pool | 终端 |
| 5 | drawing_tool | 装饰 |
| 6 | flow_arrow | 装饰 |
| 200 | stock_state_pool | 核心 |
| 201 | transfer_condition | 逻辑 |
| 202 | market_source | 源 |
| 203 | result_pool | 核心 |

#### 位标志解码

| 对象 | 标志位数 | 说明 |
|------|----------|------|
| Cell200 attr | 12 位 | show_overview, simple_intermediate, no_delete_source, clear_dest_first 等 |
| Cell201 attr | 7 位 | show_overview, basic_condition, reverse_transfer, sector_membership 等 |
| Flow attr | 5 位 | delete_source, force_move, keep_source, clear_dest_first, output_constituent |
| Enter/Exit Action | 高4位+低16位 | 动作类型 + 参数 |

#### 辅助功能

- 自动检测拓扑模式(serial/parallel/fan_out/fan_in/cyclic/mixed)
- 股票数据向下游池传播
- 时序属性解析(begin/begint/end/endt/interval)
- 市场代码映射(SH#/SZ#/B$# -> sh_a/sz_a/sector_index 等)
- attrtext 三分类解析(markets/sectors/stocks)
- 交易记录解析(trades/opentrades)

### 3.4 DZH XML 导出器 (dzh_xml_exporter.py)

| 方法 | 功能 |
|------|------|
| `export()` | 将图数据导出为 DZH XML 字符串 |
| `export_pool_to_xml()` | 便捷导出为 XML 字符串 |
| `export_pool_to_file()` | 便捷导出为 XML 文件 |
| `compare_xml_content()` | 比较两个 XML 内容 |
| `verify_roundtrip()` | 验证往返转换一致性 |

---

## 四、配置体系

### 4.1 配置文件清单

| 文件 | 用途 |
|------|------|
| `cell_type_registry.json` | 节点类型注册表(类型ID、名称、分类、形状、属性、位标志定义、默认参数) |
| `ui_layouts.json` | UI 布局配置(每种节点类型的属性面板布局，驱动前端动态生成) |
| `edge_strategies.json` | 边策略配置(节点间股票流转策略、初始化 handler、字段联动) |
| `pool_types.json` | 池类型定义(DZH/TDX 执行器映射) |
| `property_ownership.json` | 属性所有权配置(DZH/TDX 独有属性隔离) |
| `action_rules.json` | 行为规则配置(触发器、守卫条件、动作) |
| `field_definitions.json` | 字段定义(所有字段的类型、验证规则、描述) |
| `modules.json` | 模块定义(核心/逻辑/源/终端/辅助模块) |
| `dzh_type_map.json` | DZH 类型映射 |
| `api_routes.json` | API 路由表 |
| `table_schemas.json` | 表结构定义 |
| `defaults.json` | 默认值配置 |
| `flow_mode_registry.json` | 流转模式注册表 |

### 4.2 边策略配置

| 策略键 | Handler | 说明 |
|--------|---------|------|
| market_source:* | _action_resolve_and_pass | 市场源解析候选股传递 |
| market_source:transfer_condition | _action_pass_pool_stocks | 备选池直通传递 |
| market_source:stock_state_pool | _action_resolve_and_pass | 市场源直接传入状态池 |
| transfer_condition:stock_state_pool | _action_apply_filter | 转移条件过滤后传入 |
| stock_state_pool:transfer_condition | _action_pass_pool_stocks | 状态池传递到条件 |
| stock_state_pool:stock_state_pool | _action_transfer_between_pools | 状态池间转移 |
| stock_state_pool:discard_pool | _action_remove_from_pool | 移入丢弃池 |

---

## 五、已验证功能清单

### 5.1 已验证通过

- [x] Registry API 端点正常
- [x] DZH XML 导入/导出
- [x] TDX 池加载/渲染(圆柱形备选池、三角形条件、矩形状态池)
- [x] 节点尺寸严格按数据渲染(height 固定，无 overflow:hidden)
- [x] 三角形方向朝右
- [x] 圆柱形顶部椭圆：实心填充，空心不填充
- [x] 圆柱形光照渐变(38.2% 高光，10 色阶)
- [x] 矩形黄金分割渐变(38.2% 白色高光向两边渐变)
- [x] 贝塞尔曲线连线
- [x] 移动端溢出菜单
- [x] 下拉菜单 position:fixed 不被裁剪
- [x] 框选+批量拖动
- [x] 侧边栏三标签页(通达信/大智慧/实例)
- [x] 文件过滤(只显示 xml/json)
- [x] TDX/DZH/示例点击加载
- [x] 大智慧节点属性面板显示(已修复 poolData.setData 缺失问题)
- [x] 连线配置面板
- [x] 面板配置 pool_type=any 通配匹配
- [x] 属性所有权 disabled 字段标记
- [x] 字段联动(depends_on/active_when)
- [x] 位标志编解码
- [x] 撤销/重做
- [x] 剪贴板(复制/剪切/粘贴)
- [x] 键盘快捷键
- [x] 响应式三断点布局
- [x] 小地图导航
- [x] 8 方向节点缩放
- [x] 手柄式连线拖拽
- [x] 回放系统(播放/暂停/步进/变速)
- [x] Toast 通知
- [x] 上下文菜单
- [x] 自动保存(300ms 防抖)

### 5.2 待验证/待完善

- [ ] TDX 导出端点完整验证
- [ ] 导出 XML 格式往返校验
- [ ] 回放模式完整功能验证
- [ ] 新建空白池流程
- [ ] DZH 备选池 `_renderCandidate` 改为 SVG 圆柱形(当前仍用 CSS cyl-top/cyl-body/cyl-bottom)
- [ ] 运行模式完整功能验证
- [ ] K 线图集成验证
- [ ] 公式编辑器验证

---

## 六、架构特点

1. **SPA 单页应用** — 合并 index.html 和 pool_editor.html 为一页，无页面跳转
2. **xyflow 风格画布** — DOM 节点 + SVG 连线 + Handle 连接点 + 声明式状态管理
3. **表驱动架构** — 引擎仅含通用解析与执行逻辑，所有领域知识提取为配置表
4. **三级校验热加载** — 语法/逻辑/业务规则三级校验，失败保留旧配置
5. **属性所有权隔离** — DZH/TDX 独有属性在跨格式编辑时被正确禁用
6. **双通道实时通信** — WebSocket 优先，3 秒超时后降级为 HTTP 轮询
7. **虚拟滚动** — 股票列表 >100 条自动启用
8. **快照式撤销重做** — 深拷贝快照，最多 50 步历史
