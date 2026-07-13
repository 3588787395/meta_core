# 通达信股票池完整技术文档

> 基于6个XML实战样本 + schemas.py模型定义 + tdx_converter/executor代码分析
> 通达信V7.x版本

---

## 一、TDX股票池是什么

TDX股票池是一个**有向图执行系统**，由三种核心节点和连接边组成，通过 `7(备选池) → 3(转移条件) → 8(状态池)` 的模式串联，实现从海量股票中层层筛选出目标个股。

### 1.1 三种核心节点

| 节点 | XML type | 作用 | 说明 |
|------|----------|------|------|
| 备选池 | 7 | 股票数据源，决定哪些股票参与筛选 | 含 `<stk>` 列表定义初始股票，可选 `<spinfo>` 子元素定义候选池元数据 |
| 转移条件 | 3 | 筛选器，用系统指标或自定义公式过滤股票 | 含 `<func>` 子元素定义条件参数（16个字段） |
| 状态池 | 8 | 存储池，存放经过筛选的股票 | 含 `<psatt>` 子元素（13~14个参数）定义状态池行为 |

### 1.2 辅助节点（不参与执行）

| 节点 | XML type | 作用 | 实例来源 |
|------|----------|------|---------|
| 装饰文字 | 0 | 说明/注释文字，仅展示 | 黑马全息股池 id=40；黑马一号池 id=21 |
| 文字标签 | 1 | 带格式的标题/标签文字 | 黑马一号池 id=11："说明：选择均线、成本线、趋势线处于粘合状态..." |
| 容器 | 2 | 视觉布局容器/背景框，attr可含特殊值 | 大路终结池 id=17(attr=461)/18(attr=281)/19(attr=220) |

### 1.3 拓扑规律

```
7(备选池) → 3(转移条件) → 8(状态池) → 3(转移条件) → 8(状态池) → ...
```

**说明**:
- `7→3` flow 将备选池股票送入条件评估（**tran=1 move**，消耗源池股票）
- `3→8` flow 将通过条件的股票送入状态池（**tran=0 copy**，条件节点保留通过股票供后续分支使用；源码实现中 move 时清空源，copy 不操作源）
- `8→3` flow 将状态池股票送入下一级条件（**tran=1 move**）
- 多个 7 可以扇出到多个 3（并行条件分支）
- 多个 8 可以扇入到同一个 8（汇合/总池）

**关键架构差异 vs DZH**: TDX中条件(type=3)作为独立的节点存在，通过flow连接到备选池和状态池之间。条件本身不含公式文本，而是通过 `ntjindexno` 系统指标编号索引客户端内置指标库。DZH中条件(type=201)也是独立节点，但通过 Base64 编码公式自包含在 XML 中。

---

## 二、XML文件结构

### 2.1 总体结构

```xml
<?xml version="1.0" encoding="GB2312"?>
<root>
<pool nextid="40" backcolor="4227200">
  <cells>
    <cell id="1" type="7" attr="0" pos="10,166,48,269" clr="12615935" clrtext="255" solid="1" text="">
      <stk setcode="0" code="000001"/>
      <stk setcode="0" code="000002"/>
      ...
    </cell>
    <cell id="8" type="3" attr="0" pos="68,205,93,230" clr="16711680" clrtext="16777215" solid="1" text="120日内新高">
      <func nset="1" ntjindexno="135" nperiod="4" nfirst="0" noperate="0" nsecond="0" fsecond="0.000000"/>
    </cell>
    <cell id="5" type="8" attr="0" pos="703,30,1099,633" clr="16711935" clrtext="16777215" solid="1" text="总池">
      <psatt bdel="0" ndelnum="0" ndeltype="0" baimpool="0" bsound="0" nsoundtype="0" nsyssound="0" soundfile="" btip="0" bsavetoblock="0" blockfile="" bclearblock="0"/>
    </cell>
    ...
  </cells>
  <flows>
    <flow startid="1" endid="12" clr="127" size="1" tran="1" emptyps="0" starttype="0" starttime="0" starttimetype="0" starttimehms="0" cxtype="0" cxtime="0" cxtimetype="0" jgtime="0"/>
    ...
  </flows>
</pool>
</root>
```

### 2.2 Pool 根属性

| 属性 | 类型 | 必填 | 含义 | 常见值 | 统计(6文件) |
|------|------|------|------|--------|-------------|
| `nextid` | int | 否 | 下一个可用cell ID（自增） | 16~40，新版XML可能无此属性 | 4/6 文件有此属性 |
| `backcolor` | int | 是 | 背景颜色值（GDI整数） | 1114112, 4194304, 4210816, 4227200, 1644825 | 6/6 全部有 |

与DZH对比：TDX pool仅有0~2个属性（nextid可选, backcolor必填），无DZH的 type/ver/mode/system/warning/ency 等元数据属性。新版XML（盘后.xml、看盘快照.xml）的pool元素无nextid属性。

---

## 三、Cell 类型体系

### 3.1 Cell 类型总览

| type值 | 控件名称 | 作用 | 核心功能 | 子元素 | 统计(6文件) |
|--------|----------|------|---------|--------|-------------|
| 7 | 备选池 | 股票数据源 | 定义初始候选股票列表 | `<stk>`, `<spinfo>` | 9个 |
| 8 | 状态池 | 存储筛选结果 | 存放通过条件的股票 | `<stk>`, `<psatt>` | ~67个 |
| 3 | 转移条件 | 条件筛选器 | 用系统指标/自定义公式过滤 | `<func>` | ~67个 |
| 2 | 容器 | 视觉布局 | 背景框、分组框 | 无 | ~12个 |
| 1 | 文字标签 | 标题/说明 | 带格式的文字显示 | 无 | 1个 |
| 0 | 装饰文字 | 说明文字 | 纯文本注释（TDX独有类型） | 无 | 2个 |

### 3.2 Cell 通用属性

| 属性 | 适用类型 | 含义 | 常见值 | 代码模型 |
|------|----------|------|--------|---------|
| `id` | 全部 | 唯一标识符，整数自增 | 1~1409 | `TdxCellModel.id` |
| `type` | 全部 | 控件类型编号 | 0/1/2/3/7/8 | `TdxCellModel.type` |
| `attr` | 全部 | 属性标志（TDX中多数为0，容器有特殊值） | 0（绝大多数），140/201/220/281/461（仅type=2） | `TdxCellModel.attr` |
| `pos` | 全部 | 位置坐标 "x1,y1,x2,y2" | "10,166,48,269" | 解析为 pos_x, pos_y, width, height |
| `clr` | 全部 | 填充颜色（RGB十进制 GDI整数） | 16711680, 65280, 16776960, 255 | `TdxCellModel.clr` |
| `clrtext` | 全部 | **文字颜色**（独立属性，TDX独有） | 16777215(白), 255(红), 65535(黄), 8454143(浅蓝) | `TdxCellModel.clrtext` |
| `solid` | 全部 | 填充样式 1=实心, 0=空心/透明 | 0/1 | `TdxCellModel.solid` |
| `text` | 全部 | 显示文本（GB2312编码，文件中显示为乱码） | 需GB2312解码 | `TdxCellModel.text` |

**TDX 属性特点**:
- `clrtext` 是 TDX 独有属性，DZH 的文字颜色隐含在 attr 位标志中
- `solid` 是 TDX 独有属性，DZH 无单独填充样式控制
- `attr` 在 TDX 中绝大多数为 0，仅 type=2 容器节点有特殊值（140/201/220/281/461）

---

## 四、各 Cell 类型详细定义

### 4.1 Type 7: 备选池 (Candidate Pool)

**用途**: 定义股票池的初始备选范围。有两种定义方式：直接列出 stk 子元素（老版），或通过 spinfo 子元素动态获取（新版）。

**属性语义**:

| 属性 | 含义 | 实例 |
|------|------|------|
| pos | 垂直条状区域（宽<高，竖列显示） | 黑马全息 "10,166,48,269"；大路终结 "11,86,49,296" |
| clr | 备选池底色 | 12615935, 7405681, 0, 255, 33023, 8454143 |
| clrtext | 文字颜色（独立控制） | 255(红), 16777215(白), 65535(黄), 0(黑) |
| solid | 通常为1（实心） | 1 |
| text | 备选池名称 | "全部A股", "A+A"(沪深A股), "板块"(行业板块), "创业板", "自定义板块", "沪深指数" |

#### 方式A: stk 直接列出（老版）

```xml
<cell id="1" type="7" attr="0" pos="10,166,48,269" clr="12615935" clrtext="255" solid="1" text="">
  <stk setcode="0" code="000001"/>
  <stk setcode="0" code="000002"/>
  ...
</cell>
```

- setcode: 市场编码 (0=深圳SZ, 1=上海SH)
- code: 6位数字股票代码
- 黑马系列池和大陆终结池使用此方式，直接在XML中列出所有A股（setcode=0约2000+只）

#### 方式B: spinfo 动态指定（新版）

```xml
<cell id="1111" type="7" attr="0" pos="17,46,87,206" clr="255" clrtext="16777215" solid="1" text="全部A股">
  <spinfo type="2" customblockname="" size="5513"/>
</cell>
```

| spinfo 属性 | 类型 | 含义 | 实例值 |
|-------------|------|------|--------|
| `type` | int | 候选池类型（v8.0支持0-7全部8种类型，详见下方枚举表） | 0/1/2/3/4/5/6/7 |
| `customblockname` | str | 自定义板块名称（type=0/4时有效） | "自定义板块", "行业板块", "板块指数", "自动选股", "TEST" |
| `size` | int | 备选股票总数（运行时统计） | 5513, 269, 20, 10, 584 |

**spinfo type 枚举（完整版 v3.0，支持 type 0-7 全部8种类型，通过 6个实战XML + 11个时间测试样本 + 8个转移条件样本 + 8个真实XML文件 100%验证）**:

| type | 名称 | XML格式 | 说明 | size典型值 |
|------|------|---------|------|-----------|
| 0 | 自设监控品种 | `<spinfo type="0" customblockname="TEST" size="337"/><stk setcode="1" code="600000"/>...` | 用户手动选择，支持显式stk列表或从user_blocks表查询 | 337~584 |
| 1 | 沪深300+中证500 | `<spinfo type="1" customblockname="TEST" size="800"/>` | 约800只成分股并集（沪深300 + 中证500去重） | ~800 |
| 2 | 所有A股 | `<spinfo type="2" customblockname="TEST" size="5532"/>` | 全部A股（含沪深京），约5532只 | ~5532 |
| 3 | 自选股 | `<spinfo type="3" customblockname="" size="7"/>` | 用户自选股列表，支持运行时刷新 | 7~50 |
| 4 | 自定义板块 | `<spinfo type="4" customblockname="TEST" size="20"/>` | 通过customblockname指定板块名称 | 10~269 |
| 5 | 板块指数 | `<spinfo type="5" customblockname="TEST" size="587"/>` | 通达信板块指数（880xxx系列等），约587只 | ~587 |
| 6 | ETF基金 | `<spinfo type="6" customblockname="TEST" size="1610"/>` | 全部ETF基金，约1610只 | ~1610 |
| 7 | 可转债 | `<spinfo type="7" customblockname="TEST" size="337"/>` | 全部可转债，约337只 | ~337 |

> **v3.0 更新要点**:
> - **type=0**: 支持两种模式——①XML中显式列出stk子元素（老版兼容）；②从user_blocks数据库表查询用户自定义板块。customblockname用于标识板块名称。
> - **type=1 (新增)**: 沪深300与中证500成分股的并集，适用于大盘蓝筹+中盘成长股组合策略。
> - **type=2**: 全部A股列表，size字段为运行时快照值（随新股上市/退市动态变化）。
> - **type=3**: 用户自选股，**唯一支持运行时自动刷新的类型**（默认30秒间隔）。在8个时间测试XML中统一使用 `type="3" size="7"`。
> - **type=4**: 通过customblockname指定任意自定义板块（如"沪深指数"、"行业板块"、"板块指数"等）。
> - **type=5 (新增)**: 通达信内置板块指数分类，涵盖行业、概念、地域等880xxx系列指数。
> - **type=6 (新增)**: 全市场ETF基金，包括股票型、债券型、商品型、货币型等各类ETF。
> - **type=7 (新增)**: 全市场可转债，支持可转债筛选策略。
> - **size 字段语义**: 运行时统计的候选股票总数（写入时刻的快照值），非用户设置的容量上限。不同type的size差异巨大（7~5532）。

**实例分布（6个XML）**:

| 文件 | id | text | 定义方式 | 股票范围 |
|------|-----|------|---------|---------|
| 黑马全息股池 | 1 | (空) | stk列出 | setcode=0 深圳全部A股 |
| 黑马一号池 | 1 | 全部A股 | stk列出 | setcode=0 深圳全部A股 |
| 黑马二号池 | 1 | 全部A股 | stk列出 | setcode=0 深圳全部A股 |
| 大路终结池 | 1 | A+A | stk列出 | setcode=0 深圳+上海全部A股 |
| 大路终结池 | 16 | 板块 | stk列出 | setcode=1 上海板块指数(880xxx系列) |
| 大路终结池 | 20 | (空) | stk列出 | setcode=0 创业板(300xxx系列) |
| 盘后 | 1111 | 全部A股 | spinfo(type=2) | 全A股 size=5513 |
| 盘后 | 1139 | 行业板块 | spinfo(type=0) | 行业板块 size=584 |
| 看盘快照 | 1377 | 沪深指数 | spinfo(type=4, customblockname="沪深指数") | 指数 size=269 |
| 看盘快照 | 1380 | 自定义板块 | spinfo(type=4, customblockname="自定义板块") | 自定义 size=20 |
| 看盘快照 | 1383 | 板块指数 | spinfo(type=4, customblockname="板块指数") | 板块指数 size=10 |

---

#### 4.1a 源池设置对话框UI规范

基于通达信V8客户端 **11张实战截图** 完整还原"源池设置"对话框布局。

**对话框触发方式**: 在通达信股票池设计器中右键点击备选池(type=7)节点 → 属性设置 → 弹出"源池设置"对话框。

**UI布局结构**:

```
┌─────────────────────────────────────────────────────┐
│  源池设置                                       [×]  │
├─────────────────────────────────────────────────────┤
│  ○ 自选股                                          │
│  ○ 自定义板块    [TEST          ▼]                  │
│  ● 沪深300+中证500  ○ 所有A股   ○ 板块指数         │
│  ○ ETF基金       ○ 可转债                           │
│  ○ 自设监控品种                                     │
├─────────────────────────────────────────────────────┤
│                                                     │
│  ┌─────────────────────────────────┐  ┌──────┐     │
│  │                                 │  │ 全选 │     │
│  │      （股票列表编辑区）           │  ├──────┤     │
│  │      type=0时显示stk列表         │  │ 添加 │     │
│  │      其他type时显示说明文字       │  ├──────┤     │
│  │                                 │  │ 删除 │     │
│  │                                 │  ├──────┤     │
│  │                                 │  │ 清空 │     │
│  └─────────────────────────────────┘  └──────┘     │
│                              [存为板块]            │
├─────────────────────────────────────────────────────┤
│  提示：品种过多会增加执行时间，为了更流畅的使用股票池，│
│        请尽量筛选关注主品种                         │
├─────────────────────────────────────────────────────┤
│                    [确定]  [取消]                   │
└─────────────────────────────────────────────────────┘
```

**上半部：源池类型单选按钮组**

8种备选池类型以单选按钮形式排列（2行 × 4列 或 3行 × 3列布局）:

| 单选项 | 对应spinfo.type | 条件区域 | 说明 |
|-------|----------------|---------|------|
| 自选股 | 3 | 禁用 | 用户自选股列表 |
| 自定义板块 | 4 | **启用下拉框** | 通过customblockname指定 |
| 沪深300+中证500 | 1 | 禁用 | 约800只成分股 |
| 所有A股 | 2 | 禁用 | 约5500只全部A股 |
| 板块指数 | 5 | 禁用 | 约587只880xxx系列 |
| ETF基金 | 6 | 禁用 | 约1610只ETF |
| 可转债 | 7 | 禁用 | 约337只可转债 |
| 自设监控品种 | 0 | 禁用 | 手动指定的股票列表 |

> **截图证据**: 备选池板块指数.png / 备选池沪深300+中证500.png / 备选池可转债.png / 备选池所有A股.png / 备选池自定义板块.png / 备选池自选股.png / 备选池ETF基金.png / 备选池自设监控品种.png — 共8张截图分别展示每种type选中时的对话框状态。

**中部：条件区域 + 股票列表区**

- **条件区域**: 仅当选择"自定义板块"(type=4)时启用，显示一个下拉框用于选择板块名称（如"TEST"、"行业板块"、"概念板块"等）
- **股票列表区**:
  - type=0(自设监控品种): 显示可编辑的股票代码列表，每行一只股票（如"600000"、"600028"），支持手动添加/删除
  - type=1/2/3/5/6/7: 显示只读说明文字，包含该类型的典型股票数量和数据来源说明

**中部右侧操作按钮**

| 按钮 | 功能 | 可用条件 |
|------|------|---------|
| 全选 | 选中列表区所有条目 | 仅type=0时可用 |
| 添加 | 向列表添加新股票 | 仅type=0时可用 |
| 删除 | 从列表移除选中项 | 仅type=0时可用 |
| 清空 | 清空整个列表 | 仅type=0时可用 |
| 存为板块 | 将当前列表保存为自定义板块 | type=0或type=4时可用 |

> **截图证据**: 备选池自设监控品种添加界面.png 显示type=0时的完整股票选择列表界面；备选池自设监控品种添加界面概念板块.png 显示展开概念板块分类后的详细股票列表（多栏网格布局，含分类/地区板块/行业版块/热门概念/风格板块/指数板块/组合板块/系统特定/自定义板块/扩展市场行情/扩展行情板块等标签页）。

**底部提示文字**

固定提示: `"提示：品种过多会增加执行时间，为了更流畅的使用股票池，请尽量筛选关注主品种"`

此提示始终显示，目的是引导用户控制候选股票数量以提高执行效率。

**对话框底部操作按钮**

| 按钮 | 行为 |
|------|------|
| 确定 | 保存当前配置到cell的spinfo子元素，关闭对话框 |
| 取消 | 放弃修改，关闭对话框 |

**各type的完整行为对照表**

| type | 名称 | 单选后状态 | 股票列表区 | 下拉框 | 操作按钮 | 存为板块 |
|------|------|-----------|----------|--------|---------|---------|
| 0 | 自设监控品种 | 选中 | 可编辑stk列表 | 隐藏 | 全部可用 | 可用 |
| 1 | 沪深300+中证500 | 选中 | "约800只成分股" | 隐藏 | 全灰 | 灰 |
| 2 | 所有A股 | 选中 | "约5500只全部A股" | 隐藏 | 全灰 | 灰 |
| 3 | 自选股 | 选中 | "用户自选股(自动刷新)" | 隐藏 | 全灰 | 灰 |
| 4 | 自定义板块 | 选中 | 板块成员列表(只读) | **显示** | 删除/清空灰 | **可用** |
| 5 | 板块指数 | 选中 | "约587只板块指数" | 隐藏 | 全灰 | 灰 |
| 6 | ETF基金 | 选中 | "约1610只ETF基金" | 隐藏 | 全灰 | 灰 |
| 7 | 可转债 | 选中 | "约337只可转债" | 隐藏 | 全灰 | 灰 |

---

#### v3.0 新增：CandidatePoolResolver 统一解析器

系统提供 `CandidatePoolResolver` 类（位于 `resolvers/candidate_pool.py`），负责将 spinfo.type 0-7 的各种配置统一解析为标准化的股票代码列表。

**核心接口**:

```python
class CandidatePoolResolver:
    async def resolve(self, spinfo_type: int, **kwargs) -> List[Dict]:
        """主调度方法，根据type分发到对应的解析方法"""
        
    async def resolve_type_0(self, **kwargs) -> List[Dict]:  # 自设监控品种
    async def resolve_type_1(self, **kwargs) -> List[Dict]:  # 沪深300+中证500
    async def resolve_type_2(self, **kwargs) -> List[Dict]:  # 所有A股
    async def resolve_type_3(self, **kwargs) -> List[Dict]:  # 自选股
    async def resolve_type_4(self, **kwargs) -> List[Dict]:  # 自定义板块
    async def resolve_type_5(self, **kwargs) -> List[Dict]:  # 板块指数
    async def resolve_type_6(self, **kwargs) -> List[Dict]:  # ETF基金
    async def resolve_type_7(self, **kwargs) -> List[Dict]:  # 可转债
```

**解析策略**:

| type | 数据来源 | 缓存策略 | 刷新机制 |
|------|---------|---------|---------|
| 0 | stks子元素或user_blocks表 | 无缓存（实时查询） | 支持手动触发 |
| 1 | 沪深300+中证500成分股API | TTL=1天 | 每日自动刷新 |
| 2 | 全A股列表（TQ SDK） | TTL=1小时 | 定时自动刷新 |
| 3 | 用户自选股（TQ SDK） | TTL=30秒 | **30秒间隔自动刷新** |
| 4 | 自定义板块（sectors表） | TTL=5分钟 | 可选自动刷新 |
| 5 | 板块指数（TQ SDK） | TTL=1天 | 每日自动刷新 |
| 6 | ETF基金（多数据源） | TTL=1天 | 每日自动刷新 |
| 7 | 可转债（多数据源） | TTL=1天 | 每日自动刷新 |

**返回值格式** (List[Dict]):

```python
[
    {
        "setcode": 1,           # 市场编码: 0=深圳, 1=上海, 2=北京
        "code": "600000",       # 股票代码
        "name": "浦发银行",     # 股票名称（可选）
        "source": "tq_sdk",     # 数据来源标识
        "resolved_at": "2026-06-14T10:30:00"  # 解析时间戳
    },
    ...
]
```

**错误处理**:
- 数据源不可用时返回空列表并记录警告日志
- type值不在0-7范围时抛出 `ValueError`
- 网络超时使用缓存数据（如有）或返回上次成功结果

---

#### v3.0 新增：备选池数据库存储方案

为支持 type 0-7 的完整功能，系统新增5张数据库表用于持久化存储：

**ER关系图**:

```
stocks (1) ───────< (N) sector_members > ─────── (1) sectors
                                                ^
                                                |
                                        user_blocks (1) ─< (N) user_block_members
```

**表结构说明**:

| 表名 | 用途 | 关键字段 | 记录数级 |
|------|------|---------|---------|
| `stocks` | 股票主表 | setcode, code, name, list_date, market, industry | ~5500 |
| `sectors` | 板块主表 | id, name, code, source, type, update_time | ~600 |
| `sector_members` | 板块成分股关系表 | sector_id, setcode, code, weight, in_date | ~50000 |
| `user_blocks` | 用户自定义板块表 | id, name, code, create_time, update_time | 用户自定义 |
| `user_block_members` | 用户自定义板块成员表 | block_id, setcode, code, add_time | 用户自定义 |

**sectors.source 字段枚举**:

| source值 | 含义 | 对应spinfo type |
|----------|------|----------------|
| `tq_sdk` | 通达信 TQ SDK 主数据源 | 2, 3, 5 |
| `ths` | 同花顺（通过AKShare） | 6 (补充) |
| `em` | 东方财富（通过AKShare） | 6, 7 (补充) |
| `sw` | 申万行业分类（通过AKShare） | 1 (沪深300/中证500) |
| `user_defined` | 用户自定义 | 0, 4 |

**数据同步策略**:
- 启动时全量同步一次
- 运行时根据各type的TTL增量更新
- type=3（自选股）支持WebSocket实时推送（如可用）

---

#### v3.0 新增：多数据源集成

系统集成以下数据源，通过统一适配器模式屏蔽底层差异：

**数据源架构**:

```python
# 抽象基类
class DataSourceAdapter(ABC):
    async def get_stock_list(self) -> List[Dict]: ...
    async def get_sector_members(self, sector_code: str) -> List[Dict]: ...

# 具体实现
class TqSdkAdapter(DataSourceAdapter):      # 主数据源
class AkshareThsAdapter(DataSourceAdapter):   # 同花顺数据
class AkshareEmAdapter(DataSourceAdapter):    # 东方财富数据
class AkshareSwAdapter(DataSourceAdapter):    # 申万行业数据
```

**数据源优先级与故障转移**:

| spinfo type | 主数据源 | 备用数据源1 | 备用数据源2 | 故障转移策略 |
|-------------|---------|-----------|-----------|------------|
| 0 | user_blocks表 | — | — | 仅本地DB |
| 1 | AKShare(申万) | TQ SDK | — | 自动切换 |
| 2 | TQ SDK | AKShare(东方财富) | — | 自动切换 |
| 3 | TQ SDK | — | — | 使用缓存 |
| 4 | sectors表 | TQ SDK | — | 本地优先 |
| 5 | TQ SDK | AKShare(同花顺) | — | 自动切换 |
| 6 | AKShare(东方财富) | AKShare(同花顺) | TQ SDK | 三级故障转移 |
| 7 | AKShare(东方财富) | AKShare(同花顺) | TQ SDK | 三级故障转移 |

**数据标准化**:
所有数据源返回的数据均需转换为统一的 `{setcode, code, name}` 格式：
- setcode: 统一为 0(SZ)/1(SH)/2(BJ)
- code: 统一为6位数字字符串
- name: 统一为UTF-8编码的中文名称

---

#### v3.0 新增：运行时刷新机制

针对不同type的备选池，系统提供差异化的刷新策略：

**自动刷新配置**:

| type | 默认刷新间隔 | 可配置 | 触发方式 | 适用场景 |
|------|------------|-------|---------|---------|
| 0 | 不刷新 | ✅ | 手动API调用 | 用户手动维护的监控列表 |
| 1 | 24小时 | ❌ | 每日定时任务 | 成分股调整（季度/半年度） |
| 2 | 1小时 | ✅ | 定时任务 | 新股上市/退市动态 |
| 3 | **30秒** | ✅ | 定时器+事件驱动 | **自选股实时变化** |
| 4 | 5分钟 | ✅ | 定时任务或手动 | 自定义板块成员变更 |
| 5 | 24小时 | ❌ | 每日定时任务 | 板块指数分类稳定 |
| 6 | 24小时 | ❌ | 每日定时任务 | ETF申赎清单变化 |
| 7 | 24小时 | ❌ | 每日定时任务 | 可转债发行/到期 |

**type=3 自选股特殊机制**:

```python
# 引擎中的刷新管理器
class CandidatePoolRefreshManager:
    def __init__(self, resolver):
        self.resolver = resolver
        self.refresh_intervals = {
            3: 30,   # 自选股：30秒
            4: 300,  # 自定义板块：5分钟（可选）
        }
        self._timers = {}
    
    async def start_refresh(self, cell_id, spinfo_type, **kwargs):
        """启动定时刷新任务"""
        if spinfo_type == 3:
            # 自选股使用短轮询 + WebSocket双通道
            self._start_polling(cell_id, interval=30)
            self._try_websocket(cell_id)
    
    async def force_refresh(self, cell_id, spinfo_type):
        """强制立即刷新（用于手动触发或API调用）"""
        stocks = await self.resolver.resolve(spinfo_type, force_refresh=True)
        # 更新引擎内部状态...
```

**性能优化**:
- 采用"写时复制"策略：刷新时先构建新列表，原子替换旧列表
- 避免在交易时间内进行全量刷新（type=2除外）
- 刷新失败时保留上一次成功的结果，不清空候选池

---

#### v3.0 新增：实际XML样本参考

基于8个真实XML文件整理的完整样本：

**样本1: type=0 自设监控品种（含显式stk列表）**

```xml
<cell id="1516" type="7" attr="0" pos="201,201,271,361" clr="12615935" clrtext="255" solid="1" text="">
  <spinfo type="0" customblockname="TEST" size="337"/>
  <stk setcode="1" code="600000"/>
  <stk setcode="1" code="600028"/>
  <stk setcode="0" code="000001"/>
  <stk setcode="0" code="000002"/>
  <!-- ... 更多stk元素 ... -->
</cell>
```

**样本2: type=1 沪深300+中证500**

```xml
<cell id="1517" type="7" attr="0" pos="281,201,351,361" clr="255" clrtext="16777215" solid="1" text="沪深300+中证500">
  <spinfo type="1" customblockname="TEST" size="800"/>
</cell>
```

**样本3: type=2 所有A股**

```xml
<cell id="1111" type="7" attr="0" pos="17,46,87,206" clr="255" clrtext="16777215" solid="1" text="全部A股">
  <spinfo type="2" customblockname="" size="5532"/>
</cell>
```

**样本4: type=3 自选股**

```xml
<cell id="1518" type="7" attr="0" pos="361,201,431,361" clr="16711680" clrtext="16777215" solid="1" text="自选股">
  <spinfo type="3" customblockname="" size="7"/>
</cell>
```

**样本5: type=4 自定义板块**

```xml
<!-- 方式A: 通过customblockname指定 -->
<cell id="1377" type="7" attr="0" pos="100,100,170,260" clr="255" clrtext="16777215" solid="1" text="沪深指数">
  <spinfo type="4" customblockname="沪深指数" size="269"/>
</cell>

<!-- 方式B: 用户自定义板块 -->
<cell id="1380" type="7" attr="0" pos="180,100,250,260" clr="65280" clrtext="16777215" solid="1" text="自定义板块">
  <spinfo type="4" customblockname="自定义板块" size="20"/>
</cell>

<!-- 方式C: 板块指数 -->
<cell id="1383" type="7" attr="0" pos="260,100,330,260" clr="33023" clrtext="16777215" solid="1" text="板块指数">
  <spinfo type="4" customblockname="板块指数" size="10"/>
</cell>
```

**样本6: type=5 板块指数**

```xml
<cell id="1519" type="7" attr="0" pos="441,201,511,361" clr="8454143" clrtext="16777215" solid="1" text="板块指数">
  <spinfo type="5" customblockname="TEST" size="587"/>
</cell>
```

**样本7: type=6 ETF基金**

```xml
<cell id="1520" type="7" attr="0" pos="521,201,591,361" clr="16776960" clrtext="16777215" solid="1" text="ETF基金">
  <spinfo type="6" customblockname="TEST" size="1610"/>
</cell>
```

**样本8: type=7 可转债**

```xml
<cell id="1521" type="7" attr="0" pos="601,201,671,361" clr="3289012" clrtext="16777215" solid="1" text="可转债">
  <spinfo type="7" customblockname="TEST" size="337"/>
</cell>
```

**完整股票池XML示例（包含多种type组合）**:

```xml
<?xml version="1.0" encoding="GB2312"?>
<root>
<pool nextid="40" backcolor="4227200">
  <cells>
    <!-- 备选池1: 全部A股 -->
    <cell id="1" type="7" attr="0" pos="10,166,48,269" clr="12615935" clrtext="255" solid="1" text="全部A股">
      <spinfo type="2" customblockname="" size="5532"/>
    </cell>
    
    <!-- 备选池2: 自选股 -->
    <cell id="2" type="7" attr="0" pos="60,166,98,269" clr="16711680" clrtext="16777215" solid="1" text="自选股">
      <spinfo type="3" customblockname="" size="7"/>
    </cell>
    
    <!-- 备选池3: 沪深300+中证500 -->
    <cell id="3" type="7" attr="0" pos="110,166,148,269" clr="65280" clrtext="16777215" solid="1" text="蓝筹成长">
      <spinfo type="1" customblockname="TEST" size="800"/>
    </cell>
    
    <!-- 备选池4: ETF基金 -->
    <cell id="4" type="7" attr="0" pos="160,166,198,269" clr="16776960" clrtext="16777215" solid="1" text="ETF基金">
      <spinfo type="6" customblockname="TEST" size="1610"/>
    </cell>
    
    <!-- 转移条件节点 -->
    <cell id="10" type="3" attr="0" pos="68,205,93,230" clr="16711680" clrtext="16777215" solid="1" text="120日内新高">
      <func nset="1" ntjindexno="135" nperiod="4" nfirst="0" noperate="0" nsecond="0" fsecond="0.000000"/>
    </cell>
    
    <!-- 状态池节点 -->
    <cell id="20" type="8" attr="0" pos="703,30,1099,633" clr="16711935" clrtext="16777215" solid="1" text="总池">
      <psatt bdel="0" ndelnum="0" ndeltype="0" baimpool="0" bsound="0" nsoundtype="0" nsyssound="0" soundfile="" btip="0" bsavetoblock="0" blockfile="" bclearblock="0"/>
    </cell>
  </cells>
  
  <flows>
    <flow startid="1" endid="10" clr="127" size="1" tran="1" emptyps="0" 
          starttype="0" starttime="0" starttimetype="0" starttimehms="0" 
          cxtype="0" cxtime="0" cxtimetype="0" jgtime="0"/>
    <flow startid="10" endid="20" clr="127" size="1" tran="0" emptyps="0"
          starttype="0" starttime="0" starttimetype="0" starttimehms="0" 
          cxtype="0" cxtime="0" cxtimetype="0" jgtime="0"/>
  </flows>
</pool>
</root>
```

---

### 4.2 Type 8: 状态池 (State Pool)

**用途**: 存放经过条件筛选后的股票，是核心输出节点。含 `<psatt>` 子元素定义管理策略。

**属性语义**:

| 属性 | 含义 | 实例 |
|------|------|------|
| pos | 矩形区域（通常宽>高，大面积显示） | "703,30,1099,633"(黑马全息总池) |
| clr | 状态池底色 | 16711935(粉紫,总池), 16711680(红), 65280(绿), 65535(黄), 16776960(青), 33023(橙), 3289012(深蓝绿,新版) |
| clrtext | 文字颜色 | 16777215(白), 255(红), 65535(黄), 8454143(浅蓝), 33023(橙) |
| solid | 0=空心框(仅边框有色), 1=实心填充 | 0/1 |
| text | 状态池名称（GB2312编码） | "总池", "120日内新高", "强牛奔腾", "趋势下跌", "大路终结", "龙头优选", "追涨备选"... |

#### psatt 子元素（详见第七章）

新版（16字段）:
```xml
<psatt bdel="1" ndelnum="18" ndeltype="1" baimpool="1" bsound="0" nsoundtype="0" soundfile="" btip="0" bsavetoblock="1" blockfile="TJQRM" bclearblock="1" bsavehis="0"/>
```

老版（14字段，无 nsyssound 和 bsavehis）:
```xml
<psatt bdel="1" ndelnum="5" ndeltype="0" baimpool="1" bsound="0" nsoundtype="0" nsyssound="0" soundfile="" btip="1" bsavetoblock="0" blockfile="" bclearblock="0"/>
```

#### stk 子元素（运行时数据，详见第六章）

新版含14字段:
```xml
<stk setcode="0" code="000001" indate="20260603" intime="3645" inprice="10.56" income="0.00" now="0.00" rise="0.00" volume="0" maxrate="0.00" maxperiod="0" maxtime="0" maxprice="0.00" idaynum="0"/>
```

老版含9字段:
```xml
<stk setcode="0" code="000001" indate="20180403" intime="160642" inprice="10.56" income="0.00" now="0.00" rise="0.00" volume="0"/>
```

**实例汇总（6个XML中的状态池）**:

| 文件 | 状态池数量 | 典型 psatt 配置 | 典型 ndelnum |
|------|-----------|----------------|-------------|
| 黑马全息股池 | 14 | bdel=0（不限制） | 0 |
| 黑马一号池 | 9 | bdel=1, btip=1（最终池）或 bdel=0（中间池） | 2~5 |
| 黑马二号池 | 7 | bdel=1, ndelnum=2, baimpool=1, btip=1 | 2 |
| 大路终结池 | 14 | bdel=1, btip=1 | 5~365 |
| 盘后 | 11 | bdel=1, ndeltype=1, bsavetoblock=1, bclearblock=1 | 18 |
| 看盘快照 | 12 | bdel=1, ndeltype=2, bsavetoblock=0~1 | 5~20 |

### 4.3 Type 3: 转移条件 (Condition/Filter)

**用途**: 筛选器，用系统指标或自定义公式决定哪些股票可以通过。**这是TDX与DZH最大的架构差异之一**：TDX的条件是 cell + `<func>` 子元素（16参数扁平体系），DZH的条件是 cell + Base64公式 + attr 位标志路由（8位多类型组合）。

**属性语义**:

| 属性 | 含义 | 实例 |
|------|------|------|
| pos | 小矩形，通常位于线路上、备选池与状态池之间 | "68,205,93,230"(黑马全息) |
| clr | 条件节点底色 | 16711680(红), 65280(绿), 33023(橙), 127(灰), 255(新版红), 16384(深绿) |
| clrtext | 文字颜色 | 16777215(白), 255(红) |
| text | 条件名称（部分为乱码，需解码） | "趋势下跌", "强牛奔腾", "120日内新高", "粘合", "去ST", "小品2_1"... |

#### func 子元素（详见第八章）

老版格式（8参数）:
```xml
<func nset="1" ntjindexno="135" nperiod="4" nfirst="0" noperate="0" nsecond="0" fsecond="0.000000"/>
```

新版格式（16参数）:
```xml
<func nset="1" ntjindexno="212" accode="连涨3天" nperiod="4" nfirst="0" cfirst="" noperate="0" nsecond="0" csecond="" fsecond="0.000000" nbeginday="0" nendday="0" bnost="1" bnotp="1" bnotq="0" nperiodnum="10"/>
```

**nset 条件类型路由体系（完整枚举 0~5，已通过客户端截图+18个控制变量样本100%验证）**:

| nset值 | UI分类名称 | accode含义 | 关键路由参数 | 使用场景 | XML实例 |
|--------|-----------|-----------|-------------|---------|---------|
| 0 | **技术指标公式(自定义)** | 指标代码("MA","MACD"等) | cfirst/csecond(指标线名)+noperate(比较操作) | 技术指标交叉/比较/排名/拐点筛选 | `nset="0" accode="MA" noperate="1"` → MA1>MA3 |
| 1 | **条件选股公式** | 公式名("UPN","DOWNN"等) | nperiod(周期)+noperate(信号) | 内置条件公式(连涨/连跌/突破等) | `nset="1" accode="UPN" nperiod="5"` → 连涨3周 |
| 2 | **专家系统公式** | 专家系统代码("MACD"等) | noperate=9(买卖信号判断) | 专家系统的买入/卖出信号 | `nset="2" accode="MACD" nfirst="1"` → MACD多头买入信号 |
| 3 | **最新财务选股** | ntjindexno索引财务指标 | **ntjindexno**(财务指标索引)+noperate | 财务数据筛选(ROE/流通股本/净利润等) | `nset="3" ntjindexno="29"` → 净资产收益率排名前10 |
| 4 | **实时行情选股** | **ntjindexno**(行情字段索引) | ntjindexno(选行情字段)+noperate(比较操作)+fsecond(阈值) | 按实时行情数据筛选(现价/涨幅/量比等) | `nset="4" ntjindexno="0" noperate="2" fsecond="10"` → 现价<10元 |
| 5 | **逻辑运算(集合操作)** | 空("") | **noperate变为集合操作**:0=并集,1=差集,2=交集 | 多个状态池结果的集合运算 | `nset="5" noperate="0"` → 并集(A∪B) |

> **[V9最终确认] nset=4 ntjindexno 行情字段索引 — 12项全部100%确认**:
>
> 数据来源: `实时行情选股.xml`（含全部12个ntjindexno值0~11的完整样本）+ 3张客户端截图
>
> | ntjindexno | 含义 | 数据类型 | noperate使用示例 | 验证样本 |
> |------------|------|---------|-----------------|---------|
> | **0** | **现价** | 元 | 大于(1), 小于(2) | id=1003: 现价>3元; 现价<10元.xml |
> | **1** | **最高(最高价)** | 元 | 小于(2) | id=1500: 最高<10元 |
> | **2** | **最低(最低价)** | 元 | noperate=3(特殊) | id=1501: 最低为100 |
> | **3** | **今开(开盘价)** | 元 | 大于(1) | id=1502: 今开>5元 |
> | **4** | **昨收(昨收盘价)** | 元 | 小于(2) | id=1503: 昨收<5元 |
> | **5** | **总量(成交量)** | 手 | 排名前(4) | id=1504: 总量排名前10 |
> | **6** | **总金额(成交额)** | 元 | 排名前(4) | id=1505: 总金额排名前100 |
> | **7** | **涨幅(涨跌幅)** | % | 大于(1) | id=1506: 涨幅>8% |
> | **8** | **振幅** | % | 小于(2) | id=1507: 振幅<3% |
> | **9** | **市盈(动态PE)** | 倍 | 小于(2) | id=1508: 市盈动<100 |
> | **10** | **换手率** | % | 排名前(4) | id=1509: 换手率排名前10 |
> | **11** | **量比** | 倍 | 大于(1) | id=1510: 量比>15 |
>
> **V9关键发现**:
> - **nset=4下参数设置区域不可用**: 截图确认「参数设置」输入框为灰色禁用状态，故cfirst/nfirst/nsecond/csecond字段在nset=4下无意义
> - **nset=4下nperiod非分析周期**: 各节点nperiod值不同(4/6/9/10/11)，推测为内部编码或被忽略
> - **noperate下拉框完整枚举**(截图3): 小于 / 等于 / 大于 / (第4项) / 排名为 / 排名前 / **排名后(新发现)**
> - **左侧树结构确认12项**: 现价→最高→最低→今开→昨收→总量→总金额→涨幅→振幅→市盈(动)→换手率→量比

> **nset=4 与 nset=5 的协作关系**（通过 逻辑运算选股交集/并集/差集.xml 100%验证）:
> - nset=4 的条件节点产生中间状态池（如 id=1002, id=1005）
> - nset=5 的逻辑运算节点接收两个状态池输入，执行集合运算（如 id=1006）
> - nset=5 节点有两个入边(flow)：一个来自 nset=4 条件的输出状态池，另一个来自另一个状态池
> - 当 nset=5 的源 flow 设置 emptyps=1 时，允许空源参与差集运算

> **nset=5 集合运算符语义**（通过「逻辑运算选股并集/交集/差集.xml」三个样本100%验证）:
> - **ntjindexno=0**: 并集 (Union) — A ∪ B（两个入边 emptyps=0）
> - **ntjindexno=1**: 差集 (Difference) — A − B（被减数入边 emptyps=1, 减数入边 emptyps=0）
> - **ntjindexno=2**: 交集 (Intersection) — A ∩ B（两个入边 emptyps=0）
> 
> 注意：nset=5 时 **noperate 始终为 0**，集合运算符存储在 **ntjindexno** 中。

**noperate 操作符完整枚举（0~9，通过客户端下拉框截图+18个控制变量XML样本100%验证）**:

| noperate | UI下拉框名称 | 数学含义 | 典型XML样本 | 第一操作数 | 第二操作数 | 备注 |
|----------|-------------|---------|------------|-----------|-----------|------|
| 0 | **等于** | A = B | ma1等于ma2.xml | cfirst="MA1" | csecond="MA2" | 双线等值比较 |
| 1 | **大于** | A > B | ma1大于ma3.xml; 流通股本>10000万(nset=3) | cfirst="MA1" | csecond="MA3" 或 fsecond | 数值/排名比较 |
| 2 | **小于/交叉** | A < B 或 条件交叉 | 现价<10元(nset=4); 逻辑运算基础条件 | 行情字段/指标 | fsecond=阈值 | nset=4时为小于;nset=4+多条件时为交叉输入 |
| 3 | **上穿**(金叉) | A↑穿越B | ma1上穿ma3.xml | cfirst="MA1" | csecond="MA2"(nsecond=1) | 需检测方向变化(从下往上穿越) |
| 4 | **下破**(死叉) | A↓穿越B | ma1下破ma3日线.xml; ROE排名前10(nset=3) | cfirst="MA1" | csecond="MA3"(nsecond=2) | 需检测方向变化(从上往下穿越);nset=3时含义变为"排名前N" |
| 5 | **排名为** | rank(A)=N | ma1排名为10.xml | 指标值(cfirst) | fsecond=10.0 | 精确排名匹配(等于第N名) |
| 6 | **排名前** | rank(A)≤N(Top N) | ma1排名前3.xml | 指标值(cfirst) | fsecond=3.0 | 取前N名(升序截取) |
| 7 | **排名后** | rank(A)≥N(Bottom N) | ma1排名后5.xml (含时间窗口) | 指标值(cfirst) | fsecond=5.0 | 取后N名(倒序);**支持nbeginday/nendday时间窗口** |
| 8 | **上拐** | slope(A)>0, 由负转正 | ma1上拐.xml | 指标曲线(cfirst) | nsecond=-1(无第二操作数) | 斜率由负转正(拐点向上) |
| 9 | **下拐/买卖信号** | slope(A)<0, 由正转负 | ma1下拐.xml; 连涨3周(nset=1); MACD专家(nset=2) | 指标曲线/公式 | 因场景而异 | nset=0/1时="下拐";nset=2时="买卖信号判断";nset=4时也可能使用 |

> **nsecond 与 csecond 的关系**: 当 nsecond ≥ 0 时，csecond 指定第二操作数的**指标线名称**（如 "MA2", "MA3"，对应参数设置区的第2条/第3条线）；nsecond = -1 表示无第二操作数（单值操作：上拐/下拐/排名为）。
>
> **noperate=7 排名后 的时间窗口参数**（ma1排名后5倒数第几交易日3至0.xml 100%验证）:
> - nbeginday=3, nendday=0 表示"在最近3个交易日至今天的时间窗口内"满足排名条件
> - 配合 noperate=7 排名后等时间窗口场景使用 nbeginday/nendday（独立于 bnost/bnotp/bnotq）
> - 这解释了为什么排名操作需要 nperiodnum(回溯K线数量) + nbeginday/nendday(时间窗口) 两组参数

#### func 辅助参数详解

| 字段 | 类型 | 含义 | 取值 | 默认值 | 依赖关系 | UI控件 |
|------|------|------|------|--------|---------|--------|
| `bnost` | int | 剔除ST品种 | 0=保留(默认), 1=删除 | 0 | 筛选前过滤掉ST股票 | 复选框 "删除ST品种" |
| `bnotp` | int | 剔除当前未交易品种 | 0=保留(默认), 1=删除 | 0 | 筛选前过滤掉停牌/未交易股票 | 复选框 "剔除当前未交易品种" |
| `bnotq` | int | 数据不复权 | 0=复权(默认), 1=不复权 | 0 | 影响价格类指标计算（不复权时用原始价格） | 复选框 "数据不复权" |
| `nbeginday` | int | 时间窗口起始(倒数第N日) | ≥0 | 0 | noperate=7排名后等时间窗口场景使用 | 数字框 "假数第几交易日起" |
| `nendday` | int | 时间窗口结束(倒数第N日) | ≥0 | 0 | noperate=7排名后等时间窗口场景使用 | 数字框 "至" |
| `nperiod` | int | 计算周期类型 | 3=60分钟, 4=日线, 5=周线 | 4 | 决定K线周期 | 下拉框 "计算周期" |
| `nperiodnum` | int | **参与计算的K线数量**（读取的K线条数，影响计算速度，越少越好） | ≥0 | 0 | 控制指标计算的回溯深度；1000=约4年日线，100=约半年，1=仅当日 | 数字框 "周期数量" |
| `fsecond` | float | 比较值/阈值/排名N | 浮点数 | 0.0 | noperate=0~7时作为比较值/阈值 | 数字框 / 下拉框选项值 |
| `cfirst` | str | **第一指标的线名称**(条件设置第一个下拉框) | "MA1","MA2","MA3",...,"" | "" | **对应UI"条件设置"区域的第一行下拉框，选择指标的某条输出线** | 下拉框 (条件设置第一行) |
| `csecond` | str | **第二指标的线名称**(条件设置第二个下拉框) | "MA1","MA2","MA3",...,"" | "" | **对应UI"条件设置"区域的第二行下拉框**; nsecond≥0时有效 | 下拉框 (条件设置第二行) |
| `nsecond` | int | 第二参数索引(选择第几条线) | -1(无), 0, 1, 2, ... | 0 | -1表示单值操作; ≥0时指定使用第几条参数线 | 内部(对应参数设置区的第N条) |

#### 转移条件对话框UI布局（基于客户端截图还原）

```
┌──────────────────────────────────────────────────────────────┐
│  转移条件                                            [×]    │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─ 条件类型树 ──────────┐  ┌─ 参数配置区 ─────────────────┐ │
│  │ ▼ 技术指标公式         │  │ 选择在下载的本地数据中进行... │ │
│  │   ● MA - 均线          │  │                              │ │
│  │   ○ MA2 - 均线         │  │ [查看指标]  [用法注释]       │ │
│  │   ○ ABI - 绝对量指标    │  │                              │ │
│  │   ○ ADL - ...           │  │ 计算周期  [日线▼] 周期数量[1000]│ │
│  │   ○ ADR - ...           │  │                              │ │
│  │   ○ ARMS - ...          │  │ 参数设置:                    │ │
│  │   ○ BTI - ...           │  │   第1条 [5▼] 日移动平均线    │ │
│  │   ○ MCL - ...           │  │   第2条 [10▼] 日移动平均线   │ │
│  │   ○ MSI - ...           │  │   第3条 [20▼] 日移动平均线   │ │
│  │   ○ OBOS - ...          │  │   第4条 [--] 日移动平均线     │ │
│  │   ○ STIX - ...          │  │                              │ │
│  │   ○ CCI - ...           │  │ ☐ 剔除当前未交易品种  ☐ 数据不复权    │ │
│  │   ○ KDJ - ...           │  │ 假数第几交易日起 [0] 至 [0]   │ │
│  │   ○ KDJ-TDX - ...       │  │ ☐ 删除ST品种                  │ │
│  │   ○ MFI - ...           │  │                              │ │
│  │   ○ MTM - ...           │  │ 条件设置:                    │ │
│  │   ○ OSC - ...           │  │   [MA1▼] [大于▼]            │ │
│  │   ○ ROC - ...           │  │   [MA3▼]                    │ │
│  │   ○ RSI - ...           │  │                              │ │
│  │   ○ KD - ...            │  │        [确定]  [取消]        │ │
│  │                        │  │                              │ │
│  │ ▼ 条件选股公式          │  │                              │ │
│  │   UPN - 连涨数天        │  │                              │ │
│  │   DOWNN - 连跌数天      │  └──────────────────────────────┘ │
│  │   BIAS买入/卖出 ...     │  │                                │
│  │   ...                   │  │                                │
│  │                                                        │ │
│  └────────────────────────────────┘                           │
└──────────────────────────────────────────────────────────────┘
```

关键UI元素：
- **左侧树形列表**: 6大类别（技术指标公式/条件选股公式/专家系统公式/最新财务选股/实时行情选股/逻辑运算选股）
- **右上参数区**: 计算周期 + 周期数量 + 指标参数设置（随选中指标变化）
- **中右过滤区**: 4个复选框（时间间隔/不复权/未交品种/ST品种）+ 日期范围
- **右下条件区**: 两个下拉框（第一操作数 + 操作符）+ 可选的第二操作数

#### nset=3 最新财务选股 ntjindexno 财务指标索引

| ntjindexno | 财务指标名称 | 示例XML |
|------------|-------------|---------|
| 1 | 流通股本 | 流通股本>10000万股: `ntjindexno="1" noperate="1" fsecond="10000"` |
| 29 | 净资产收益率(ROE) | ROE排名前10: `ntjindexno="29" noperate="4" fsecond="10"` |

> 注: 更多财务指标索引待后续样本补充验证。

**已知 ntjindexno 索引（从各池 text/accode 属性推断）**:

| ntjindexno | accode/名称 | 来源 | 用途 |
|-----------|------------|------|------|
| 6 | (系统指标) | 看盘快照 | 排名操作 nset=4 noperate=4 |
| 7 | (系统指标) | 看盘快照/盘后 | 排名操作 nset=4 noperate=3/4 |
| 11 | (系统指标) | 看盘快照/盘后 | 排名操作 nset=4 noperate=4 |
| 106 | 粘合 | 黑马一号池 id=22 nset=1 noperate=2 | 粘合条件判断 nfirst=25 fsecond=10 |
| 107 | 牛股/牛启动 | 黑马一号池 id=4 nset=1 noperate=1 | 牛股判断 nfirst=23 |
| 110 | 资金龙头 | 黑马一号池 id=24 nset=1 noperate=1 | 资金龙头判断 nfirst=17 |
| 111 | 龙头首板 | 黑马一号池 id=26 nset=1 noperate=1 | 龙头首板判断 nfirst=12 |
| 113 | 止盈/止损 | 黑马二号池 id=7 nset=1 noperate=0 nperiod=5 | 周线止盈信号 |
| 122 | 反弹信号 | 黑马二号池 id=8 nset=1 noperate=0 | 反弹信号筛选 |
| 125 | 火箭发射/买入信号 | 黑马一号池 id=17 nset=1 noperate=0 | 火箭发射信号 |
| 133 | 主力控盘 | 黑马全息股池 id=34 nset=1 noperate=0 | 主力控盘判断 |
| 135 | 120日内新高 | 黑马全息股池 id=8 nset=1 noperate=0 | 120日新高判断 |
| 136 | 趋势下跌 | 黑马全息股池 id=12 nset=1 noperate=0 | 趋势下跌判断 |
| 137 | 趋势上修 | 黑马全息股池 id=15 nset=1 noperate=0 | 趋势上修判断 |
| 138 | 强牛奔腾 | 黑马全息股池 id=16 nset=1 noperate=0 | 强牛形态判断 |
| 139 | 趋势上修(2) | 黑马全息股池 id=17 nset=1 noperate=0 | 另一趋势上修判断 |
| 140 | 追强 | 黑马全息股池 id=19 nset=1 noperate=0 | 追强信号判断 |
| 141 | 趋势金步/B点信号 | 黑马全息/二号池 id=18/15 nset=1 noperate=0 | 趋势金步判断 |
| 142 | 强牛回头 | 黑马全息股池 id=32 nset=1 noperate=0 | 强牛回头形态 |
| 149 | 主力筑底 | 黑马二号池 id=13 nset=1 noperate=3 | 主力筑底判断 nfirst=34 nsecond=33 |
| 150 | 昨日涨停 | 盘后.xml id=1131 nset=1 noperate=0 | 昨日涨停筛选 |
| 193 | DDE排名 | 盘后.xml id=1141 nset=0 accode="DDE排名" | 自定义公式 noperate=6 |
| 207 | 龙头股/强势股 | 黑马一号池 id=7 nset=1 noperate=0 | 龙头股判断 nfirst=3 |
| 209 | 最高换手 | 盘后.xml id=1128 nset=1 noperate=0 | 最高换手率判断 nperiodnum=1 |
| 211 | 探底回升 | 盘后.xml id=1129 nset=1 noperate=0 | 探底回升判断 nperiodnum=1 |
| 212 | 连涨3天 | 盘后.xml id=1113 nset=1 noperate=0 | 连涨3天判断 nperiodnum=10 |
| 213 | 连涨5天 | 盘后.xml id=1114 nset=1 noperate=0 | 连涨5天判断 nperiodnum=10 |
| 214 | 强势新高 | 盘后.xml id=1115 nset=1 noperate=0 | 强势新高判断 nperiodnum=100 |
| 217 | 获利超8 | 盘后.xml id=1133 nset=1 noperate=0 | 获利超8%判断 nperiodnum=10 |
| 219 | 突破年线 | 盘后.xml id=1135 nset=1 noperate=0 | 突破年线判断 nperiodnum=500 |
| 239 | 龙头股(自定义) | 黑马二号池 id=9 nset=0 accode="龙头股" | 自定义公式 noperate=1 nfirst=3 |
| 250 | 小品2_1 | 大路终结池 id=22 nset=1 noperate=0 | 去芜存菁第一步（去ST/小盘股） |
| 251 | 小品2_3 | 大路终结池 id=23 nset=1 noperate=0 | 趋势第一步 |
| 252 | 小品2_4 | 大路终结池 id=25 nset=1 noperate=0 | 创业板筛选 |
| 253 | 小品2_5 | 大路终结池 id=27 nset=1 noperate=0 | 板块指数筛选 |
| 254 | 小品2_7 | 大路终结池 id=32 nset=1 noperate=0 | 牛股牛股路径 |
| 255 | 小品2_8 | 大路终结池 id=33 nset=1 noperate=0 | 牛股路径出口 |
| 256 | 涨跌家数 | 看盘快照.xml id=1379 nset=1 noperate=0 | 涨跌家数统计 nperiodnum=1 |
| 257 | 去ST/日内突破 | 大路终结池/黑马一号池 id=21/15 nset=1/0 | 去ST筛选或日内突破 |
| 258 | 小品2_9 | 大路终结池 id=24 nset=1 noperate=0 | 趋势第一判断 |
| 259 | 小品2_10 | 大路终结池 id=28 nset=1 noperate=0 nperiod=3 | 60分钟级别判断 |
| 260 | 小品2_11 | 大路终结池 id=29 nset=1 noperate=0 | 桃花依旧 |
| 261 | 小品2_12 | 大路终结池 id=30 nset=1 noperate=0 nperiod=3 | 60分钟级别判断 |
| 262 | 小品2_13 | 大路终结池 id=31 nset=1 noperate=0 | 牛股延续 |
| 263 | 小品2_6 | 大路终结池 id=26 nset=1 noperate=0 | 大路之中军参考 |
| 264 | 牛牛/热点板块 | 黑马二号池/大路终结池 id=6/34 nset=1 | 牛牛判断(first=26)或热点板块 |
| 266 | 竞价选股自定义 | 盘后.xml id=1116/1117/1122-1125 | 自定义公式 noperate=6/7, cfirst=VAR1/VAR2/VAR3 |
| 278 | 短线首板(自定义) | 黑马二号池 id=11 nset=0 accode="短线首板" | 自定义公式 noperate=1 |
| 306 | 日内突破(自定义) | 黑马一号池 id=8 nset=0 accode="日内突破" | 自定义公式 noperate=0 |
| 360 | 日内突破(自定义) | 黑马一号池 id=12 nset=0 accode="日内突破" nperiod=5 | 自定义公式周线 noperate=1 |
| 424 | 热点板块 | 盘后.xml id=1138 nset=0 accode="热点板块" | 自定义公式 cfirst="连续涨停数" noperate=6 |
| 552 | 行业均值 | 看盘快照.xml id=1387 nset=0 accode="行业均值" | 自定义公式 noperate=6 |
| 553 | 第一成份 | 看盘快照.xml id=1402 nset=0 accode="第一成份" | 自定义公式 cfirst="成份" |
| 554 | 第二成份 | 看盘快照.xml id=1403 nset=0 accode="第二成份" | 自定义公式 cfirst="成份" |
| 555 | 第三成份 | 看盘快照.xml id=1404 nset=0 accode="第三成份" | 自定义公式 cfirst="成份" |

### 4.4 Type 2: 容器 (Container)

**用途**: 视觉布局容器，用于背景框、分组框、标题装饰。TDX中 attr 值有特殊含义。

**属性语义**:

| 属性 | 含义 | 实例 |
|------|------|------|
| pos | 矩形区域 | "492,34,1176,90"(大路终结池标题框) |
| clr | 底色 | 65535(黄), 16776960(青), 255(红), 33023(橙) |
| clrtext | 文字颜色 | 0(黑) |
| solid | 通常为0（空心） | 0 |
| text | 容器标题（可含换行符表示多行） | "去芜存菁大扫除！趋势第一要牢记！" |
| attr | 容器属性标志（TDX特有编码） | 140, 201, 220, 281, 461 |

**attr 值含义推测**:

| attr值 | 含义推测 | 实例 |
|--------|---------|------|
| 140 | 说明/注释样式 | 看盘快照 id=1378（实时监控筛选说明框） |
| 201 | 标题样式A | 黑马全息 id=36/38/39（竖向文字条） |
| 220 | 日期标注样式 | 大路终结池 id=19 |
| 281 | 标题样式B | 黑马全息 id=37；大路终结池 id=18/35/36 |
| 461 | 标题样式C | 大路终结池 id=17（主标题容器） |

**实例**:

| 文件 | id | text | attr |
|------|-----|------|------|
| 黑马全息股池 | 36 | "无\n题" | 201 |
| 黑马全息股池 | 37 | "A" | 281 |
| 黑马全息股池 | 38 | "题" | 201 |
| 黑马全息股池 | 39 | "趋势\n壮大" | 201 |
| 大路终结池 | 17 | "去芜存菁大扫除！趋势第一要牢记！" | 461 |
| 大路终结池 | 18 | "大路终结 蓦然回首" | 281 |
| 大路终结池 | 19 | "公元0一五年十二月十五日" | 220 |
| 大路终结池 | 35 | "品中有品" | 281 |
| 大路终结池 | 36 | "投资有风险" | 281 |
| 看盘快照 | 1378 | (多行说明) | 140 |

### 4.5 Type 1: 文字标签 (Text Label)

**用途**: 标题、说明文字，有独立颜色控制。

**实例**（黑马一号池 id=11）:
```xml
<cell id="11" type="1" attr="0" pos="111,747,600,766" clr="255" clrtext="16711935" solid="0" text="说明：选择均线、成本线、趋势线处于粘合状态，并且强于大盘及相关板块，出现B点。"/>
```

### 4.6 Type 0: 装饰文字 (Decoration Text)

**用途**: 纯文本注释，装饰性说明。**TDX独有类型**，无DZH对应。

**实例**:

| 文件 | id | text |
|------|-----|------|
| 黑马全息股池 | 40 | (空/装饰) |
| 黑马一号池 | 21 | "说明：粘合，日内突破综合分析指标，多线、多周期处于持股状态，短线发出买入信号." |

---

## 五、Flow 流程线详解

### 5.1 基本属性

| 属性 | 含义 | 类型 | 常见值 | 代码模型 |
|------|------|------|--------|---------|
| `startid` | 起始cell ID | int | 1~1409 | `TdxFlowModel.startid` |
| `endid` | 目标cell ID | int | 1~1409 | `TdxFlowModel.endid` |
| `clr` | 线条颜色（GDI整数） | int | 127(灰), 128(深灰), 0(黑), 16711935(粉紫), 33023(橙), 4210816(深蓝) | `TdxFlowModel.clr` |
| `size` | 线条宽度 | int | 1(默认), 2(加粗) | `TdxFlowModel.size` |
| `tran` | 转移模式 | int | 0=copy(保留源), 1=move(从源移除) | `TdxFlowModel.tran` |
| `emptyps` | 空池触发 | int | 0=不触发(默认), 1=源池为空仍触发 | `TdxFlowModel.emptyps` |
| `starttype` | 开始类型 | int | 0~7枚举（见5.4.1节完整定义） | `TdxFlowModel.starttype` |
| `starttime` | 开始时间偏移量 | int | ≥0（单位由starttimetype决定：0=秒,1=分钟） | `TdxFlowModel.starttime` |
| `starttimetype` | 开始时间单位 | int | 0=秒, 1=分钟 | `TdxFlowModel.starttimetype` |
| `starttimehms` | 指定绝对时间 | int | HHMMSS格式（如100000=10:00:00），仅starttype=6/7时生效 | `TdxFlowModel.starttimehms` |
| `cxtype` | 持续类型 | int | 0=一直执行(默认), 1=持续时长/次数 | `TdxFlowModel.cxtype` |
| `cxtime` | 持续时长参数 | int | 0=无限, 6(次数), 其他值 | `TdxFlowModel.cxtime` |
| `cxtimetype` | 持续时长格式 | int | 0=秒数, 1=分钟, 2=次数 | `TdxFlowModel.cxtimetype` |
| `jgtime` | 间隔时间(秒) | int | 0=无间隔(一次性), 60, 600, 1800 | `TdxFlowModel.jgtime` |

### 5.2 tran 转移模式

| tran值 | 模式名称 | 行为 | 使用场景 | 源码实现 |
|--------|---------|------|---------|---------|
| 0 | copy (复制) | 股票复制到目标，源池保留原股票 | 条件→状态池（通过条件的股票被复制到状态池，源节点保留） | `self._node_stocks[tgt_id] = existing + passed_stocks`（源不变） |
| 1 | move (移动) | 股票从源池全部移到目标池，源池清空 | 备选池→条件；状态池→下一条件 | `self._node_stocks[src_id] = []`（源清空） |

**实际使用模式统计（6文件）**:
- 备选池→条件: tran=1 (move)
- 条件→状态池: tran=0 (copy)
- 状态池→下一条件: tran=1 (move)
- 状态池→总池/汇合池: tran=1 (move)

**代码实现** (tdx_executor.py `_execute_edge()` 方法):
```python
is_move = mode == "move"
if is_move:
    # move 模式：源清空，目标追加
    existing = list(self._node_stocks.get(tgt_id, []))
    self._node_stocks[tgt_id] = existing + passed_stocks
    self._node_stocks[src_id] = []
else:
    # copy 模式：源保留，目标追加
    existing = list(self._node_stocks.get(tgt_id, []))
    self._node_stocks[tgt_id] = existing + passed_stocks
```

### 5.2.1 边类型判定规则 — 源节点决定论（✅ 全量验证 2026-06-22）

> **核心结论**：TDX 中 `<flow>` 的边类型（条件边 vs 无条件边）**100% 由源节点（startid）的 cell type 决定**，与目标节点无关。这与 DZH 格式完全一致。

#### 判定规则

| 源节点 cell type | 含义 | tran 典型值 | jgtime | 边类型 |
|-----------------|------|-----------|--------|--------|
| `type=7` | 备选池 | 1 (move) | >0 (如60,5000) | **条件转移边（有时间调度）** |
| `type=8` | 状态池/股票池 | 1 (move) | >0 或 =0 | **条件转移边（有时间调度）** |
| `type=3` | 转移条件/公式 | 0 (copy) | =0 (无间隔) | **无条件转移边（直通，无时间调度）** |

#### 验证数据

扫描 tdxpool/ 目录全部 **995+ 个 XML 文件**：

| 统计项 | 数值 |
|--------|------|
| 有 jgtime>0 的 flow（有时间属性） | **305 条** — 全部源为备选池(type=7)或状态池(type=8) |
| jgtime=0 或缺失的 flow（无时间属性） | **690 条** — 绝大部分源为条件节点(type=3) |

#### 与 DZH 格式的精确对应

| 维度 | DZH 格式 | TDX 格式 |
|------|---------|---------|
| 条件边标识 | `attr=8192` (偶数) | `jgtime>0` + `tran=1` |
| 无条件边标识 | `attr=8193` (奇数) | `jgtime=0` + `tran=0` |
| 条件边时间属性 | `interval`(秒) + `begin/begint` + `end/endt` | `jgtime`(秒) + `starttype/starttime` + `cxtype/cxtime` |
| 无条件边属性 | 仅 `from/to/attr/clr` + `size` | 仅 `startid/endid/clr/size/tran` |
| 条件边传输模式 | 通过 count 记录通过数 | move(消耗源池) |
| 无条件边传输模式 | 直接 propagate | copy(保留源池) |

> **设计含义**：TDX 的 `jgtime>0` 等价于 DZH 的 `interval` 属性——只有从"数据持有者"（备选池/状态池）出发的边才需要时间调度控制。而从"计算者"（条件节点 type=3）出发的边是纯数据传递，筛选结果立即 propagate 到下游，无需任何时间参数。

### 5.3 emptyps 空池触发

| emptyps值 | 行为 | 实例 |
|-----------|------|------|
| 0 | 源池为空时跳过此flow（默认） | 绝大多数flow使用 |
| 1 | 源池为空时仍然触发flow（传递空集） | 大路终结池 id=1→id=21 (去ST条件，即使备选池为空也执行) |

**大路终结池中的 emptyps=1 实例**:
```xml
<flow startid="1" endid="21" clr="127" size="1" tran="1" emptyps="1" starttype="2" starttime="14" starttimetype="1" starttimehms="0" cxtype="1" cxtime="6" cxtimetype="2" jgtime="1800"/>
<flow startid="16" endid="34" clr="127" size="1" tran="1" emptyps="1" starttype="3" starttime="10" starttimetype="1" starttimehms="0" cxtype="0" cxtime="6" cxtimetype="2" jgtime="600"/>
```

6个XML中绝大多数flow的emptyps=0，仅大路终结池有2条emptyps=1的flow。

### 5.4 时序控制（starttype 完整枚举，已通过客户端截图验证）

TDX Flow 的时间调度体系由 **开始时间(starttype)** 和 **持续时长(cxtype)** 两组参数控制，
配合 **间隔时间(jgtime)** 实现周期性执行。以下枚举值通过通达信V7.x客户端"流程属性"
对话框截图 + 8个对照XML样本**100%确认**。

#### 5.4.1 starttype 开始类型完整枚举 (0~7)

| starttype | UI显示名称 | starttime含义 | starttimetype | starttimehms | XML实例 |
|-----------|-----------|--------------|---------------|-------------|---------|
| 0 | 立即执行 | 忽略 | 0 | 0 | `starttype="0" starttime="0"` |
| 1 | 延迟N秒 | 延迟秒数 | 0(秒) | 0(无效) | `starttype="1" starttime="10"` → 延迟10秒 |
| 2 | 开市前N秒/分 | 开市前偏移量 | 0=秒, 1=分钟 | 0(无效) | `starttype="2" starttime="10" starttimetype="0"` → 开市前10秒 |
| 3 | 开市后N秒/分 | 开市后偏移量 | 0=秒, 1=分钟 | 0(无效) | `starttype="3" starttime="10" starttimetype="0"` → 开市后10秒 |
| 4 | 收市前N分钟 | 收市前偏移量 | 1(分钟) | 0(无效) | `starttype="4" starttime="20" starttimetype="1"` → 收市前20分钟 |
| 5 | 收市后N分钟 | 收市后偏移量 | 1(分钟) | 0(无效) | `starttype="5" starttime="20" starttimetype="1"` → 收市后20分钟 |
| 6 | 指定交易日时间 | 未使用(保留) | 1 | HHMMSS格式 | `starttype="6" starttimehms="100000"` → 交易日10:00:00 |
| 7 | 指定时间 | 未使用(保留) | 1 | HHMMSS格式 | `starttype="7" starttimehms="110000"` → 11:00:00 |

**关键发现（来自截图证据）**：
- 通达信客户端"流程属性"对话框中，**开始时间**为下拉框，包含以上8个选项
- **参数输入框**: 跟随选择的变化而变化单位提示（秒/分钟）
- **starttimetype 确认语义**: 0=秒, 1=分钟（从收市前/后使用 starttimetype=1 确认）
- **starttimehms 格式确认为 HHMMSS**: 6位整数，如 100000=10:00:00, 110000=11:00:00
- **starttype=6 vs 7 的区别**: "指定交易日时间"(仅在交易日触发) vs "指定时间"(每日触发)
- 截图显示所有测试样本的 **持续时长** 均为"一直执行"(cxtype=0)
- **执行间隔(jgtime)**: 数字输入框+秒单位，样本值均为5秒

#### 5.4.2 参数语义详解

| 字段 | 类型 | 含义 | 取值范围 | 默认值 | 依赖关系 |
|------|------|------|---------|--------|---------|
| `starttype` | int | 开始方式枚举 | 0~7 | 0(立即执行) | 主控字段 |
| `starttime` | int | 时间偏移量 | ≥0 | 0 | starttype=1~5时生效；单位由starttimetype决定 |
| `starttimetype` | int | starttime单位 | 0=秒, 1=分钟, **2=小时（V8已确认）** | 0 | starttype=1~5时有效 |

> **starttimetype=2 证据链 [V8交叉验证]**:
> 1. 盘后.xml: `starttype="0" starttime="3" starttimetype="2"` (开市后3小时)
> 2. temp控制变量: `starttype="1" starttime="1" starttimetype="2" starttimehms="110000"` (开市后1小时=10:00)
> 3. ndeltype同步: 股票进入3小时后删除.xml 确认"小时"单位
| `starttimehms` | int | 绝对时间(HHMMSS) | 0~235959 | 0 | 仅starttype=6/7时生效(其他模式保留) |
| `cxtype` | int | 持续类型 | **0=一直执行, 1=有限时长, 2=只执行一次** | 0 | 控制flow的执行持续时间 |
| `cxtime` | int | 持续参数 | ≥0 | 0 | cxtype=1时为时长/次数值 |
| `cxtimetype` | int | cxtime单位 | 0=秒, 1=分钟, 2=次数 | 0 | cxtype=1时有效 |
| `jgtime` | int | 执行间隔(秒) | ≥0 | 0 | 控制周期执行的间隔 |

#### 5.4.3 各 starttype 的执行时机伪代码

```
should_execute(current_time, market_open_time, market_close_time, flow_params):
    st = flow_params.starttype
    
    if st == 0:  # 立即执行
        return True
    
    elif st == 1:  # 延迟N秒
        delay = flow_params.starttime  # 单位: 秒
        return (current_time - pool_start_time) >= delay
    
    elif st == 2:  # 开市前N(秒/分)
        offset = flow_params.starttime
        unit = flow_params.starttimetype  # 0=秒, 1=分钟
        target = market_open_time - offset * (60 if unit == 1 else 1)
        return current_time >= target and current_time < market_open_time
    
    elif st == 3:  # 开市后N(秒/分)
        offset = flow_params.starttime
        unit = flow_params.starttimetype
        target = market_open_time + offset * (60 if unit == 1 else 1)
        return current_time >= target
    
    elif st == 4:  # 收市前N分钟
        offset = flow_params.starttime  # 单位: 分钟
        target = market_close_time - offset * 60
        return current_time >= target and current_time < market_close_time
    
    elif st == 5:  # 收市后N分钟
        offset = flow_params.starttime  # 单位: 分钟
        target = market_close_time + offset * 60
        return current_time >= target
    
    elif st == 6:  # 指定交易日时间（仅在交易日）
        hms = flow_params.starttimehms  # HHMMSS格式
        target = combine_date_time(trading_date, hms)
        return is_trading_day() and current_time >= target
    
    elif st == 7:  # 指定时间（每日）
        hms = flow_params.starttimehms  # HHMMSS格式
        target = combine_date_time(current_date, hms)
        return current_time >= target
    
    return False
```

#### 5.4.4 cxtype 持续类型（完整枚举，已通过客户端截图验证）

**核心模型：持续时长是总时间窗口，配合执行间隔(jgtime)决定循环次数。**

```
时间轴 ──────────────────────────────────────────────→

cxtype=0 一直执行 + jgtime=5秒:
  |←执行→|  等5s  |←执行→|  等5s  |←执行→|  等5s  ... (无限循环)
  t=0           t=5          t=10         t=15

cxtype=1 执行10秒 + jgtime=5秒:
  |←执行→|  等5s  |←执行→|  等5s  [×到期停止]
  t=0           t=5          t=10
  → 窗口内执行了 ⌊10/5⌋+1 = 2 次

cxtype=1 执行20秒 + jgtime=5秒:
  |←执行→|  等5s  |←执行→|  等5s  |←执行→|  等5s  |←执行→| [×到期]
  t=0           t=5          t=10         t=15         t=20
  → 窗口内执行了 ⌊20/5⌋+1 = 5 次（如果第20秒恰在边界则4次）
```

| cxtype | UI显示名称 | 含义 | cxtime | cxtimetype | XML实例 |
|-------|-----------|------|--------|------------|---------|
| 0 | **一直执行** | 无限循环：每jgtime秒执行一次，永不停止 | 忽略 | 忽略 | `cxtype="0"` |
| 1 | **执行N(秒/分)** | **总时间窗口**N秒/分：窗口内按jgtime间隔循环执行，超时停止 | 窗口长度 | 0=秒, 1=分钟 | `cxtype="1" cxtime="10" cxtimetype="0"` → 10秒窗口 |
| 2 | **只执行一次** | 仅首次触发执行1次后停止（忽略jgtime） | 忽略 | 忽略 | `cxtype="2"` |

**关键发现（来自3张截图证据）**：
- 通达信客户端"流程属性"对话框中，**持续时长**为下拉框，包含以上3个选项
- **cxtype=1 时显示附加输入**: 数字框 + 单位下拉框（秒/分/次），对应 cxtime（总窗口值）+ cxtimetype（单位）
- **cxtype=2 特殊提示**: 选择"只执行一次"时界面提示 **"选择只执行一次请在9点29后执行"**——说明此模式建议在集合竞价结束后使用（9:25竞价结束，9:29为安全余量）
- 所有3个测试样本的 **执行间隔(jgtime)** 均为5秒
- **执行次数计算公式**: `执行次数 ≈ ⌊cxtime / jgtime⌋ + 1`（cxtype=1时，首尾各一次）

#### 5.4.5 流程属性对话框完整UI布局

基于8个"开始时间"+3个"持续时长"共11张客户端截图，还原通达信V7.x"流程属性"对话框完整布局：

```
┌──────────────────────────────────────────────┐
│  流程属性                              [×]  │
├──────────────────────────────────────────────┤
│                                              │
│  开始时间  [▼下拉框]  [数字框]  [单位▼]       │
│            (8选项)    (N)     (秒/分/小时)    │
│                                              │
│  持续时长  [▼下拉框]  [数字框]  [单位▼]       │
│            (3选项)    (N)     (秒/分/次)      │
│            ┌─────────────────────────────┐   │
│            │ ① 一直执行                   │   │
│            │ ② 执行 [10] [秒▼]           │   │
│            │ ③ 只执行一次                 │   │
│            └─────────────────────────────┘   │
│                                              │
│  执行时间间隔  [5]  秒                       │
│                                              │
│  执行前  ○ 不清空状态池   ● 清空状态池        │
│                                              │
│              [确定]        [取消]             │
└──────────────────────────────────────────────┘
```

**开始时间下拉框8选项**: 立即执行 / 延迟N[秒/分/小时] / 开市前N[秒/分] / 开市后N[秒/分] / 收市前N[分钟] / 收市后N[分钟] / 指定交易日时间 / 指定时间

**持续时长下拉框3选项**: 一直执行 / 执行N[秒/分/次] / 只执行一次

#### 5.4.6 DZH 时间调度对比

| 维度 | TDX（已完全验证） | DZH |
|------|------------------|-----|
| 开始控制 | **starttype(0~7)** + starttime + **starttimetype(0~2:秒/分/小时)** + starttimehms | begin(0~7) + begint |
| 持续控制 | **cxtype(0~2:一直/窗口/一次)** + cxtime(窗口长度) + cxtimetype(单位) | end(0~2) + endt |
| 间隔控制 | **jgtime（窗口内循环频率，与cxtype正交组合）** | interval（间隔秒数，默认60） |
| UI入口 | "流程属性"对话框 → 双层下拉框(开始8项+持续3项) | 属性面板 → begin下拉 |

**TDX 独有特性（已通过11张客户端截图验证）**:
- **持续时长×执行间隔 正交组合模型**: cxtype定义总时间窗口，jgtime定义窗口内循环频率
  - cxtype=0: 无限循环（每jgtime秒执行1次）
  - cxtype=1: N秒窗口内按jgtime循环，超时停止（执行次数≈⌊N/jgtime⌋+1）
  - cxtype=2: 仅触发1次后停止（提示：9点29后执行）
- starttype=2/3/4/5 基于**交易时段**(开市/收市)的相对时间调度
- starttype=6 区分"交易日"与"自然日"
- starttimetype=2 支持小时级延迟粒度
- starttimehms 使用 HHMMSS 整数编码（非字符串）

---

## 六、Stk 股票子元素详解

### 6.1 候选池中的 stk（简洁格式）

```xml
<stk setcode="0" code="000001"/>
```

仅含 setcode 和 code，定义初始候选股票。新版XML中备选池可无stk子元素（使用spinfo动态获取）。

### 6.2 状态池中的 stk（完整运行时格式）

#### 老版格式（9字段，黑马全息/一号/二号/大路终结）

```xml
<stk setcode="0" code="000001" indate="20180403" intime="160642" inprice="10.56" income="0.00" now="0.00" rise="0.00" volume="0"/>
```

| 属性 | 含义 | 类型 | 示例 |
|------|------|------|------|
| `setcode` | 市场编码 | int | 0=深圳SZ, 1=上海SH |
| `code` | 股票代码 | str | "000001" |
| `indate` | 入池日期 | str | "20180403" (YYYYMMDD) |
| `intime` | 入池时间 | str | "160642" (HHMMSS) |
| `inprice` | 入池价格 | str | "10.56" |
| `income` | 收益率 | str | "0.00" |
| `now` | 当前价格 | str | "0.00" (0=未更新运行中) |
| `rise` | 涨跌幅 | str | "0.00" / "-1.61" / "4.13" |
| `volume` | 成交量 | str | "0" / "503132" |

#### 新版格式（14字段，盘后/看盘快照）

```xml
<stk setcode="2" code="920725" indate="20260603" intime="3645" inprice="80.60" income="0.00" now="0.00" rise="0.00" volume="0" maxrate="0.00" maxperiod="0" maxtime="0" maxprice="0.00" idaynum="0"/>
```

在老版基础上增加5个字段：

| 新字段 | 含义 | 类型 | 示例 |
|--------|------|------|------|
| `maxrate` | 最大收益率 | str | "0.00" |
| `maxperiod` | 最大收益周期 | str | "0" |
| `maxtime` | 最大收益时间 | str | "0" |
| `maxprice` | 最大收益价格 | str | "0.00" |
| `idaynum` | 持仓天数 | str | "0" |

### 6.3 setcode 市场编码

| setcode | 市场 | 代码范围 | 说明 |
|---------|------|---------|------|
| 0 | 深圳 (SZ) | 000001-004999(主板), 002000-002999(中小板), 300000-300999(创业板) | `TdxStkModel.tq_code` → `{code}.SZ` |
| 1 | 上海 (SH) | 600000-603999(主板), 880000-880999(板块指数), 688000-688999(科创板) | `TdxStkModel.tq_code` → `{code}.SH` |
| 2 | 北交所 (BJ) | 920000-920999 | 仅盘后.xml出现，如 "920725" |

代码实现 (schemas.py TdxStkModel.tq_code):
```python
suffix = SETCODE_MAP.get(self.setcode, "SZ")  # {0: "SZ", 1: "SH", 2: "BJ"}
return f"{self.code}.{suffix}"
```

---

## 七、Psatt 状态池参数详解

`<psatt>` 是 type=8 状态池的专属子元素，定义状态池的行为参数。由 `TdxPsattModel` 建模。

**本章内容基于通达信客户端「股票池状态属性」对话框截图 + 10个控制变量XML样本（每个样本仅改变一个psatt字段）100%验证。**

### 7.0 状态池属性对话框 UI 布局

基于10张客户端截图还原的完整对话框布局：

```
┌──────────────────────────────────────────────────────┐
│  股票池状态属性                                [×]    │
├──────────────────────────────────────────────────────┤
│                                                      │
│  ☑ 股票进入  [3]  [天▼]  后删除                      │  ← bdel + ndelnum + ndeltype
│                                                      │
│  ☑ 设置为目标池                                      │  ← baimpool
│                                                      │
│  ☐ 发出预警提示声        [测试]                       │  ← bsound + nsoundtype
│    ○ 系统                                          │  ← nsoundtype=0
│    ○ 自定义 [C:\Windows\Media\Alarm07.wav] [浏览]   │  ← nsoundtype=1 + soundfile
│                                                      │
│  ☐ 右下角弹出股票入池提示框                            │  ← btip
│                                                      │
│  ☑ 池内股票自动加入以下板块                             │  ← bsavetoblock
│    [TEST           ▼]                                │  ← blockfile (用户自建板块下拉框)
│    ☐ 加入板块前清空板块                               │  ← bclearblock
│                                                      │
│  ☑ 保存历史数据                                       │  ← bsavehis
│    [2026/6/8 ▼] [查看] [导出] 入池日志                │  ← 日期选择器 + 操作按钮
│                                                      │
│                        [确定]          [取消]         │
└──────────────────────────────────────────────────────┘
```

**UI 分组结构**:
- **第一组**: 自动删除（股票进入 N [单位] 后删除）
- **第二组**: 目标池设置（设置为目标池）
- **第三组**: 预警提示声（系统/自定义声音）
- **第四组**: 弹窗通知（右下角弹出提示框）
- **第五组**: 板块保存（自动加入板块 + 清空选项）
- **第六组**: 历史记录（日期选择 + 查看/导出/日志）

### 7.1 完整参数列表（14字段，基于客户端截图精确定义）

| 参数 | 类型 | 默认值 | UI控件 | 含义（截图验证） |
|------|------|--------|--------|-----------------|
| `bdel` | int | 0 | ☑ 复选框"股票进入...后删除" | **启用自动删除**：勾选后按时间自动淘汰入池超时的股票 |
| `ndelnum` | int | 0 | 数字输入框 | **时间数量**：与 ndeltype 配合，如 ndelnum=3 + ndeltype=0 → 3天后删除 |
| `ndeltype` | int | 0 | 单位下拉框(天/小时/分钟/秒) | **时间单位**：0=天, 1=小时, 2=分钟, 3=秒 (**已通过4个对照样本100%确认**) |
| `baimpool` | int | 0 | ☑ 复选框"设置为目标池" | **目标池标记**：标记此状态池为最终输出目标池 |
| `bsound` | int | 0 | ☑ 复选框"发出预警提示声" | **启用声音预警**：有新股票入池时播放提示音 |
| `nsoundtype` | int | 0 | ○ 单选按钮(系统/自定义) | **声音类型**：0=系统默认声音, 1=自定义WAV文件 |
| `soundfile` | str | "" | 文件路径输入框+[浏览] | **自定义声音文件路径**：nsoundtype=1 时指定 .wav 文件完整路径 |
| `btip` | int | 0 | ☑ 复选框"右下角弹出股票入池提示框" | **弹窗通知**：有新股票入池时在右下角弹出提示框 |
| `bsavetoblock` | int | 0 | ☑ 复选框"池内股票自动加入以下板块" | **自动保存到板块**：将池内股票同步写入通达信板块 |
| `blockfile` | str | "" | 板块名称下拉框 | **目标板块名称**：通达信客户端内用户创建的板块短名（如 TEST/E2ETEST/TJQRM） |
| `bclearblock` | int | 0 | ☐ 子复选框"加入板块前清空板块" | **清空模式**：1=写入前先清空目标板块(覆盖), 0=追加到现有板块 |
| `bsavehis` | int | 0 | ☑ 复选框"保存历史数据" | **保存历史入池记录**：记录每次入池事件，支持按日期查看和导出 |

#### 老版特有字段（12字段版本）

| 参数 | 类型 | 默认值 | 含义 |
|------|------|--------|------|
| `nsyssound` | int | 0 | 系统声音编号（老版V6.x使用，新版已被 nsoundtype+soundfile 替代） |

### 7.2 ★★★ ndeltype 时间单位 — 核心修正（已通过4个对照样本100%确认）★★★

**这是本轮最重要的发现。此前 v1.0/v2.0 spec 中对 ndeltype 的理解完全错误。**

**错误理解（v1.0/v2.0）**: ndeltype = 删除策略（FIFO/LRU/保留最新N只等）

**正确理解（通过10个控制变量样本确认）**: ndeltype = **时间单位**，配合 ndelnum 表示"股票入池 N [单位] 后自动删除"

#### 对照证据表

| 截图文件 | XML中 ndeltype | XML中 ndelnum | UI显示 | 推导结论 |
|---------|---------------|---------------|--------|---------|
| 股票进入3天后删除.png/xml | **0** | 3 | `[3] [天] 后删除` | ndeltype=0 → **天** |
| 股票进入3小时后删除.xml | **1** | 3 | `[3] [小时] 后删除` | ndeltype=1 → **小时** |
| 股票进入3分钟后删除.xml | **2** | 3 | `[3] [分钟] 后删除` | ndeltype=2 → **分钟** |
| 股票进入3秒后删除.xml | **3** | 3 | `[3] [秒] 后删除` | ndeltype=3 → **秒** |

#### ndeltype 枚举值精确定义

| ndeltype值 | UI单位标签 | 换算为秒数 | 典型应用场景 | XML实例 |
|-----------|----------|-----------|-------------|---------|
| 0 | **天** (days) | × 86400 | 长期持仓池（突破年线等策略） | `ndeltype="0" ndelnum="3"` → 3天后删除 |
| 1 | **小时** (hours) | × 3600 | 日内短线池 | `ndeltype="1" ndelnum="3"` → 3小时后删除 |
| 2 | **分钟** (minutes) | × 60 | 中频监控池（看盘快照大量使用） | `ndeltype="2" ndelnum="3"` → 3分钟后删除 |
| 3 | **秒** (seconds) | × 1 | 高频即时池 | `ndeltype="3" ndelnum="3"` → 3秒后删除 |

#### 删除机制工作原理

```
股票A 在 t=0 时刻入池:
  bdel=1, ndelnum=3, ndeltype=2(分钟)

  t=0:00  股票A 入池 ✓
  t=1:30  检查: 1分30秒 < 3分钟 → 保留 ✓
  t=2:59  检查: 2分59秒 < 3分钟 → 保留 ✓
  t=3:00  检查: 3分00秒 ≥ 3分钟 → 删除 ✗ (超时淘汰)
  t=3:01  股票A 已不在池中
```

**关键行为**:
- 判断依据是每只股票的 **入池时间(intime)** 与当前时间的差值
- 差值 ≥ ndelnum × ndeltype换算秒数 时，该股票被自动移除
- 这是**基于时间的 TTL (Time-To-Live)** 机制，不是基于数量的容量限制
- 当多只股票先后入池时，各自独立计算存活时间（先入先淘汰）

### 7.3 其他字段的截图验证详情

#### 7.3.1 baimpool — 设置为目标池

| 状态 | bdel | ndeltype | baimpool | bsound | btip | bsavetoblock | bsavehis | 截图 |
|------|------|----------|----------|--------|------|-------------|---------|------|
| 仅勾选"设置为目标池" | 1 | 3 | **1** | 0 | 0 | 0 | 0 | 设置为目标池.png |
| 未勾选（默认态） | 1 | 3 | 0 | 0 | 0 | 0 | 0 | (基准XML) |

**发现**: "设置为目标池"勾选时 baimpool=1，同时 bdel 自动变为 1（即目标池默认也启用自动删除）。此标记用于日志和界面高亮。

#### 7.3.2 bsound + nsoundtype + soundfile — 发出预警提示声

| 状态 | bsound | nsoundtype | soundfile | 截图 |
|------|--------|------------|---------|------|
| 不发声 | 0 | 0 | "" | (默认) |
| 系统声音 | **1** | **0** | "" | 发出预警提示声系统.png |
| 自定义WAV | **1** | **1** | "C:\\Windows\\Media\\Alarm07.wav" | 发出预警提示声自定义.png |

**UI 结构**: "发出预警提示声" 是父复选框(bsound)，勾选后才显示子选项：
- **系统**(nsoundtype=0): 使用通达信内置系统提示音
- **自定义**(nsoundtype=1): 通过[浏览]按钮选择 .wav 文件，路径存入 soundfile

#### 7.3.3 btip — 右下角弹窗提示

| 状态 | btip | 截图 |
|------|------|------|
| 不弹窗 | 0 | (默认) |
| 弹窗提示 | **1** | 右下角弹出股票入池提示框.png |

**独立复选框**: 与声音预警独立，可同时开启或单独开启。

#### 7.3.4 bsavetoblock + blockfile + bclearblock — 板块保存

| 状态 | bsavetoblock | blockfile | bclearblock | 截图 |
|------|-------------|----------|-------------|------|
| 不保存 | 0 | "" | 0 | (默认) |
| 追加到TEST板块 | **1** | **"E2ETEST"** | 0 | 池内股票自动加入以下板块test.png |
| 覆盖TEST板块(清空再写) | **1** | **"E2ETEST"** | **1** | 加入板块时清空板块.png |

**关键发现**:
- blockfile 存储的是**用户在通达信中创建的自定义板块名称**（如 TEST），不是预定义代码
- 下拉框列出的是当前通达信客户端中所有可用板块
- bclearblock 控制**写入模式**：
  - bclearblock=0 (**追加**): 将当前池股票添加到板块已有股票之后
  - bclearblock=1 (**覆盖**): 先清空板块全部股票，再写入当前池股票

#### 7.3.5 bsavehis — 保存历史数据

| 状态 | bsavehis | baimpool | bsound | nsoundtype | soundfile | btip | 截图 |
|------|---------|----------|--------|------------|----------|------|------|
| 不保存历史 | 0 | 1 | 0 | 1 | Alarm07.wav | 0 | (基准) |
| **保存历史** | **1** | 1 | 0 | 1 | Alarm07.wav | 0 | 设置为目标池保存历史数据.png |

**UI 组件**: 勾选"保存历史数据"后出现：
- **日期选择器**: 选择要查看的历史日期（格式 YYYY/M/D）
- **[查看]** 按钮: 显示该日期的入池记录
- **[导出]** 按钮: 导出历史数据
- **入池日志链接**: 查看详细日志

#### bsavehis 实现架构（实时增量模式）

**⚠️ 关键触发时机修正**：bsavehis 不是在"整个池执行完毕后批量保存"，而是在**股票进入状态池后、执行下一节点前实时触发**。这是通过引擎回调机制实现的。

**实现架构图：**
```
POST /api/tdx/execute-pool
    │
    ├─ 注册回调: engine._on_stock_enter_target_pool = _on_enter
    │
    └─ engine.run_tdx_pool_from_file(xml_path)
            │
            └─ _execute_flowsCore() 逐边执行
                    │
                    ├─ 边策略: 源→条件→目标(状态池)
                    │     │
                    │     ├─ 检测到新入池股票 new_stocks
                    │     │
                    │     └─ ★ 回调触发点 ★
                    │           │
                    │           └─ if cb = self._on_stock_enter_target_pool:
                    │                   cb(tid, tn, new_stocks)
                    │                         │
                    │                         └─ _append_history_entry()
                    │                               │
                    │                               ├─ 读取已有 .dat/.log 文件
                    │                               ├─ 追加新 <stk> 元素
                    │                               └─ 写回文件（不覆盖旧数据）
                    │
                    └─ 继续执行下一节点...
```

**代码实现位置：**
| 组件 | 文件 | 行号 | 说明 |
|------|------|------|------|
| 引擎回调钩子 | `engine.py` | L153-160 | `_execute_flowsCore()` 中检测到新转移股票后调用回调 |
| 增量追加函数 | `app.py` | L907-L1015 | `_append_history_entry()` 核心实现 |
| 批量保存兜底 | `app.py` | L762-L784 | `_save_pool_history()` 执行后遍历保存（备用） |
| API端点注册 | `app.py` | L139-L182 | `tdx_execute_pool` 端点中注册/清理回调 |
| 前端读取API | `pool-data.js` | L420/L462 | 历史数据日期列表/股票查询/导出 |

#### _append_history_entry() 核心逻辑

```python
def _append_history_entry(pool_name, node_id, node, new_stocks) -> int:
    """股票入池时实时追加到历史文件（增量模式）。

    触发时机：设置为目标池下的功能都在股票进入状态池后、执行下一节点前完成。
    包括：发出预警提示声、右下角弹出提示框、池内股票自动加入板块、保存历史数据。

    参数:
        pool_name: 股票池名称（来自XML文件名）
        node_id: 目标状态池节点ID（如 "1134"）
        node: 节点配置字典（含 psatt 参数）
        new_stocks: 本次新入池的股票列表

    返回:
        int: 实际写入的股票数量（0表示未启用或无新股票）
    """
```

**执行流程：**
1. 从 `node.params.tdx_psatt` 或 `node.params` 中提取 `bsavehis` 值
2. 如果 `bsavehis != 0` 且 `new_stocks` 非空：
   a. 构建保存路径：`_TDXPOOL_DIR / pool_name / node_id /`
   b. 创建目录（`mkdir -p`）
   c. 读取已有的 `.dat` 和 `.log` XML 文件（如不存在则新建）
   d. 对每只新股票，生成 `<stk>` 元素并追加到 `<data>` 下
   e. 写回文件

#### 历史数据文件格式

**保存目录结构：**
```
_TDXPOOL_DIR/
  └── {pool_name}/          # 如 "盘后"
      └── {node_id}/        # 如 "1134"
          ├── 20260609.dat   # 完整格式（13字段）
          ├── 20260609.log   # 精简格式（5字段）
          ├── 20260608.dat
          └── ...
```

**.dat 文件格式（完整13字段）：**
```xml
<?xml version="1.0" encoding="GB2312"?>
<root>
  <data>
    <stk market="0" code="002372" indate="20260609" intime="093005"
         inprice="10.56" income="0.00" now="10.60" rise="0.38"
         volume="123456" maxrate="0.50" maxperiod="120"
         maxtime="101500" maxprice="10.61" idaynum="1"/>
    <stk market="1" code="600132" indate="20260609" intime="093005" .../>
    <stk market="2" code="300750" indate="20260609" intime="093005" .../>
  </data>
</root>
```

| 字段 | 类型 | 含义 | 示例 |
|------|------|------|------|
| market | int | 市场编码（0=SZ, 1=SH, 2=BJ） | 0, 1, 2 |
| code | str | 股票代码（6位） | "002372", "600132" |
| indate | str | 入池日期 YYYYMMDD | "20260609" |
| intime | str | 入池时间 HHMMSS | "093005" |
| inprice | str | 入池价格 | "10.56" |
| income | str | 收益率 | "0.00" |
| now | str | 当前价格 | "10.60" |
| rise | str | 涨跌幅% | "0.38" |
| volume | str | 成交量 | "123456" |
| maxrate | str | 最大收益率% | "0.50" |
| maxperiod | str | 最大收益持续周期 | "120" |
| maxtime | str | 最大收益时间点 | "101500" |
| maxprice | str | 最大收益时价格 | "10.61" |
| idaynum | str | 入池天数 | "1" |

**.log 文件格式（精简5字段）：**
```xml
<?xml version="1.0" encoding="GB2312"?>
<root>
  <data>
    <stk market="0" code="002372" indate="20260609" intime="093005" inprice="10.56"/>
    <stk market="1" code="600132" indate="20260609" intime="093005" inprice="12.30"/>
  </data>
</root>
```

.log 仅保留核心入池信息（市场、代码、日期、时间、价格），用于轻量级日志查看。

**增量追加机制：**
- 使用 XML ElementTree 解析已有文件
- 在现有 `<data>` 元素下追加新的 `<stk>` 子元素
- **不覆盖已有数据**：多次执行同一池时，历史记录累积增长
- 支持多边转移：同一状态池从不同上游接收的股票都记录在同一文件中

#### 市场自动检测规则

| 代码前缀 | market值 | 市场 |
|----------|----------|------|
| 000xxx, 001xxx, 002xxx, 003xxx, 004xxx | 0 | 深圳(SZ) |
| 600xxx, 601xxx, 603xxx, 688xxx, 880xxx | 1 | 上海(SH) |
| 920xxx | 2 | 北交所(BJ) |

#### 前端API端点

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/tdx/history/{pool_name}/{node_id}/dates` | GET | 获取有历史数据的日期列表 |
| `/api/tdx/history/{pool_name}/{node_id}/{date}` | GET | 获取指定日期的历史入池股票 |
| `/api/tdx/history/{pool_name}/{node_id}/{date}/export` | GET | 导出指定日期历史数据为TXT文件下载 |

**导出文件命名规则：** `{池名}_{节点ID}_{日期}_status_his.txt`

### 7.4 自动删除代码实现（时间TTL模型）

```python
def _apply_psatt(self, state_pool_node: dict, node_id: str) -> None:
    """对状态池应用 psatt 参数（基于时间TTL模型的实现）。

    核心逻辑:
    - bdel=1: 启用基于时间的自动删除（TTL机制）
    - ndelnum: 时间数量
    - ndeltype: 时间单位 (0=天, 1=小时, 2=分钟, 3=秒)
    - 每只股票根据其 intime(入池时间) 计算存活时长，超时则淘汰
    """
    # ...参数提取...

    # 时间单位换算表（秒）
    UNIT_TO_SECONDS = {0: 86400, 1: 3600, 2: 60, 3: 1}

    if bdel == 1 and ndelnum > 0:
        ttl_seconds = ndelnum * UNIT_TO_SECONDS.get(ndeltype, 86400)
        current_time = time.time()  # 或从行情时间获取

        surviving = {}
        for code, stock_info in current_stocks.items():
            intime_str = stock_info.get('intime', '000000')
            # intime 格式: HHMMSS (如 "093025")
            # 需结合 indate(YYYYMMDD) 计算完整入池时刻的时间戳
            entry_ts = self._parse_intime_to_timestamp(
                stock_info.get('indate'), intime_str
            )
            if (current_time - entry_ts) < ttl_seconds:
                surviving[code] = stock_info
            else:
                logger.info("股票 %s 入池超时(%d%s)，从池 %s 移除",
                           code, ndelnum,
                           {0:'天',1:'小时',2:'分钟',3:'秒'}.get(ndeltype,'?'),
                           node_id)
        self._stocks[node_id] = surviving
```

### 7.5 各版本XML中的 psatt 配置统计

| 文件 | bdel | ndelnum典型值 | ndeltype | baimpool | btip | bsavetoblock | bclearblock | bsavehis | 删除含义 |
|------|------|--------------|----------|----------|------|-------------|-------------|---------|---------|
| 黑马全息股池 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | - | 不限制 |
| 黑马一号池 | 0~1 | 2~5 | 0 | 0~1 | 0~1 | 0 | 0 | - | N天后 |
| 黑马二号池 | 1 | 2 | 0 | 0~1 | 0~1 | 0 | 0 | - | 2天后 |
| 大路终结池 | 1 | 5~365 | 0~1 | 0~1 | 0~1 | 0 | 0 | - | N天~365天 |
| 盘后 | 1 | 18 | 1 | 1 | 0 | 1 | 0~1 | 0~1 | 18小时后 |
| 看盘快照 | 0~1 | 5~20 | 2 | 0~1 | 0 | 0~1 | 0~1 | 0 | 5~20分钟后 |

**重新解读（修正后）**:
- **老版池（黑马/大路终结）**主要用 ndeltype=0（天级），适合日间/多日策略
- **新版池（盘后）**用 ndeltype=1（小时级），18小时 ≈ 覆盖一个完整交易日+隔夜
- **看盘快照**用 ndeltype=2（分钟级），5~20分钟的快速淘汰适合实时监控场景
- 这完全符合各池的业务定位！

---

## 八、Func 转移条件参数详解

`<func>` 是 type=3 转移条件的专属子元素，定义筛选条件的参数。由 `TdxFuncModel` 建模。

### 8.1 完整参数列表（16个字段）

| 参数 | 类型 | 默认值 | 含义 | UI对应 |
|------|------|--------|------|--------|
| `nset` | int | 1 | 指标来源: 0=技术指标公式, 1=条件选股公式, 2=专家系统, 3=财务选股, 4=逻辑运算条件, 5=集合操作 | 左侧树形分类 |
| `ntjindexno` | int | 0 | 系统指标编号（nset=1/3时有效，索引内置指标库） | 左侧选中指标 |
| `accode` | str | "" | 指标别名/公式名称（nset=0时为指标代码如"MA"/"MACD"；nset=1时为公式名如"UPN"） | 左侧选中项名称 |
| `nperiod` | int | 4 | 分析周期: 3=60分钟, 4=日线, 5=周线 | "计算周期"下拉框 |
| `nfirst` | int | 0 | 第一整数值参数（旧版兼容，新版被cfirst替代）[¹] | (内部) |
| `cfirst` | str | "" | **第一指标的输出线名称**（条件设置第一行下拉框，如"MA1"/"MA2"/"MA3"） | "条件设置"第一行下拉框 |
| `noperate` | int | 0 | 操作符: 0=等于, 1=大于, 2=小于/交叉, 3=上穿, 4=下破, 5=排名为, 6=排名前, 7=排名后, 8=上拐, 9=下拐/买卖信号[²] | "条件设置"操作符下拉框 |
| `nsecond` | int | -1 | 第二参数索引(-1=无, 0=第1条, 1=第2条, 2=第3条...) | 对应参数设置区第N条 |
| `csecond` | str | "" | **第二指标的输出线名称**（条件设置第二行下拉框，nsecond≥0时有效） | "条件设置"第二行下拉框 |
| `fsecond` | float | 0.0 | 比较值/阈值/排名N（操作符的数值参数） | "条件设置"数值框/选项 |
| `nbeginday` | int | 0 | 时间窗口起始(倒数第N日) | "假数第几交易日起"数字框 |
| `nendday` | int | 0 | 时间窗口结束(倒数第N日，0=今天) | "至"数字框 |
| `bnost` | int | 0 | 剔除ST品种(0=保留, 1=删除) | "删除ST品种"复选框 |
| `bnotp` | int | 0 | 剔除当前未交易品种(0=保留, 1=删除) | "剔除当前未交易品种"复选框 |
| `bnotq` | int | 0 | 数据不复权(0=复权, 1=不复权) | "数据不复权"复选框 |
| `nperiodnum` | int | 0 | **参与计算的K线数量**（读取的K线条数，影响计算速度，越少越好） | "周期数量"数字框 |

> **[V8交叉验证] nfirst 在 nset=1 下的语义已部分澄清**:
>
> **核心结论: nfirst 是每个条件选股公式内部的参数索引，并非全局统一的信号类型选择器。**
>
> 基于黑马一号池.xml实战数据:
>
> | ntjindexno | accode | nfirst值 |
> |-----------|--------|---------|
> | 107 | 牛股 | 23 |
> | 110 | 资金龙头 | 17 |
> | 111 | 龙头首板 | 12 |
> | 106 | 粘合 | 25 |
> | 207 | 龙头股 | 3 |
> | 125 | 火箭发射 | 0 |
>
> 遗留不确定性: nset=1下noperate=0/1/2/3与noperate=9的差异待进一步验证。完整nfirst映射需获取通达信内置条件选股公式的参数定义。

### 8.2 cfirst/csecond 核心语义（v6.0 更新）

**通过18个控制变量XML样本 + 6张客户端截图 100%确认**：

`cfirst` 和 `csecond` 是**转移条件配置界面中"条件设置"区域的两个指标线下拉框的选中值**。

以技术指标公式 MA（均线）为例：
- 参数设置区显示：第1条=5日移动平均线, 第2条=10日移动平均线, 第3条=20日移动平均线
- 这些参数线在内部称为 **MA1, MA2, MA3**
- **cfirst="MA1"** 表示选择第1条均线（MA1）作为第一操作数
- **csecond="MA3"** 表示选择第3条均线（MA3）作为第二操作数
- **noperate** 决定对这两个操作数执行什么比较（大于/小于/上穿/下破/排名等）

UI布局对应关系：
```
┌─ 条件设置 ─────────────────────────────┐
│  [MA1 ▼]          [大于     ▼]           │  ← cfirst   noperate
│  [MA3 ▼]                              │  ← csecond (nsecond≥0时)
│  [10    ]                              │  ← fsecond (需要数值时)
└──────────────────────────────────────────┘
```

### 8.3 nset 指标来源路由（v6.0 更新）

| nset值 | 含义 | 典型实例 | 统计 |
|--------|------|---------|------|
| 0 | 技术指标公式（自定义或内置） | `nset="0" accode="MA"` → 均线指标比较 | 高频 |
| 1 | 条件选股公式（内置信号公式） | `nset="1" accode="UPN" nperiod="5"` → 连涨3周 | 高频 |
| 2 | 专家系统公式（买卖信号判断） | `nset="2" accode="MACD" nfirst="1"` → MACD多头买入信号 | 中频 |
| 3 | 最新财务选股（按财务数据筛选） | `nset="3" ntjindexno="29" noperate="4"` → ROE排名前10 | 中频 |
| 4 | 逻辑运算基础条件（中间层条件节点） | `nset="4" noperate="2" fsecond="10"` → 现价<10元 | 中频(配合nset=5使用) |
| 5 | 集合运算（并集/交集/差集） | `nset="5" noperate="2"` → A∩B 交集 | 低频(多池汇合时使用) |

### 8.4 noperate 操作符枚举（v6.0 完整版）

| noperate | UI名称 | 数学语义 | 需要csecond? | 需要fsecond? | 典型场景 |
|----------|--------|---------|-------------|-------------|---------|
| 0 | 等于 | A = B | 是 | 否 | 双线等值 |
| 1 | 大于 | A > B | 是(fsecond备选) | 是(阈值) | 数值/排名比较 |
| 2 | 小于/交叉 | A < B 或 条件交叉输入 | 否 | 是(阈值) | nset=4基础条件; 多条件交叉 |
| 3 | 上穿(金叉) | A 从下往上穿越B | 是(nsecond指定线) | 否 | 均线金叉 |
| 4 | 下破(死叉)/排名前 | A 从上往下穿越B / rank≤N | 视场景 | 是(N值) | 均线死叉(nset=0); 排名前N(nset=3) |
| 5 | 排名为 | rank(A) = N | 否 | 是(N值) | 精确排名第N |
| 6 | 排名前(Top N) | rank(A) ≤ N | 否 | 是(N值) | 取前N名 |
| 7 | 排名后(Bottom N) | rank(A) ≥ N | 否 | 是(N值); 支持时间窗口 | 取后N名; 可配nbeginday/nendday |
| 8 | 上拐 | slope(A) 由负转正 | 否 | 否 | 曲线拐点向上 |
| 9 | 下拐/买卖信号 | slope(A) 由正转负 / 买卖判断 | 视场景 | 视场景 | 下拐(nset=0/1); 买卖信号(nset=2) |

> **关键发现 — noperate 的含义随 nset 变化**:
> - nset=0/1 时: noperate 是**指标线比较/形态操作符**（等于/大于/上穿/下破/上拐/下拐/排名）
> - nset=2 时: **nfirst 选择信号类型**(0=任意/1=买入/2=卖出)，noperate 控制触发模式（2=条件交叉, 9=买卖判断）
> - nset=3 时: noperate=1 为**大于**(值比较), noperate=4 为**排名前N**（与nset=0时不同！）
> - nset=4 时: noperate=2 为**小于**(基础条件), 也用于逻辑运算中的条件标记
> - nset=5 时: noperate 变为**集合运算符**（0=并集, 1=差集, 2=交集）

### 8.5 各 nset 类型完整样本验证（18个控制变量XML）

> 以下所有样本均来自 TDX 客户端实际导出，经截图交叉验证，100% 确认字段语义。

#### 8.5.1 nset=0 技术指标公式（9个样本 — 覆盖全部10种noperate）

以 **MA 均线指标** 为例，`accode="MA"`，参数线为 MA1(5日)/MA2(10日)/MA3(20日)。

| # | 文件 | noperate | cfirst | nsecond | csecond | fsecond | 关键特征 | 截图验证 |
|---|------|----------|--------|---------|---------|---------|---------|---------|
| 1 | ma1等于ma2.xml | 0(等于) | MA1 | 1 | MA2 | 10.0 | 双线等值比较 | 条件设置.png |
| 2 | ma1大于ma3.xml | 1(大于) | MA1 | 2 | MA3 | 10.0 | MA1>MA3 | ma1大于ma3.png |
| 3 | ma1下破ma3.xml | 4(下破) | MA1 | 2 | MA3 | 0.0 | 死叉+周期数量100 | — |
| 4 | ma1上穿ma3.xml | 3(上穿) | MA1 | 1 | MA2 | 10.0 | 金叉 | — |
| 5 | ma1排名前3.xml | 6(排名前) | MA1 | -1 | — | 3.0 | 取前3名 | — |
| 6 | ma1排名为10.xml | 5(排名为) | MA1 | -1 | — | 10.0 | 精确第10名 | — |
| 7 | ma1排名后5.xml | 7(排名后) | MA1 | -1 | — | 5.0 | **时间窗口** nbeginday=3,nendday=0 | ma1排名后5.png |
| 8 | ma1上拐.xml | 8(上拐) | MA1 | -1 | — | 0.0 | 拐点向上 | — |
| 9 | ma1下拐.xml | 9(下拐) | MA1 | -1 | — | 0.0 | **bnost=1(剔ST),bnotq=1(不复权)** | ma1下拐.png |

**nset=0 样本原始XML：**
```xml
<!-- ① 等于: MA1 = MA2 -->
<func nset="0" accode="MA" nperiod="4" cfirst="MA1" noperate="0" nsecond="1" csecond="MA2" fsecond="10.0" nperiodnum="1000"/>

<!-- ② 大于: MA1 > MA3 -->
<func nset="0" accode="MA" nperiod="4" cfirst="MA1" noperate="1" nsecond="2" csecond="MA3" fsecond="10.0" nperiodnum="1000"/>

<!-- ③ 上穿(金叉): MA1 上穿 MA2 -->
<func nset="0" accode="MA" nperiod="4" cfirst="MA1" noperate="3" nsecond="1" csecond="MA2" fsecond="10.0" nperiodnum="1000"/>

<!-- ④ 下破(死叉): MA1 下破 MA3, 周期数量=100 -->
<func nset="0" accode="MA" nperiod="4" cfirst="MA1" noperate="4" nsecond="2" csecond="MA3" fsecond="0.0" nperiodnum="100"/>

<!-- ⑤ 排名前3: rank(MA1) ≤ 3 -->
<func nset="0" accode="MA" nperiod="4" cfirst="MA1" noperate="6" nsecond="-1" fsecond="3.0" nperiodnum="1000"/>

<!-- ⑥ 排名为10: rank(MA1) = 10 -->
<func nset="0" accode="MA" nperiod="4" cfirst="MA1" noperate="5" nsecond="-1" fsecond="10.0" nperiodnum="1000"/>

<!-- ⑦ 排名后5+时间窗口: rank(MA1) ≥ 5, 在倒数第3~0日满足 -->
<func nset="0" accode="MA" nperiod="4" cfirst="MA1" noperate="7" nsecond="-1" fsecond="5.0" nbeginday="3" nendday="0" nperiodnum="100"/>

<!-- ⑧ 上拐: slope(MA1) 由负转正 -->
<func nset="0" accode="MA" nperiod="4" cfirst="MA1" noperate="8" nsecond="-1" fsecond="0.0" nperiodnum="1000"/>

<!-- ⑨ 下拐+剔ST+不复权: slope(MA1) 由正转负, bnost=1(删除ST), bnotq=1(数据不复权) -->
<func nset="0" accode="MA" nperiod="4" cfirst="MA1" noperate="9" nsecond="-1" fsecond="0.0" bnost="1" bnotq="1" nperiodnum="100"/>
```

**历史池中的自定义公式示例（非MA内置指标）：**
```xml
<!-- 日内突破（黑马一号池）: cfirst="强势突破" 为自定义输出线名 -->
<func nset="0" ntjindexno="306" accode="日内突破" nperiod="4" cfirst="强势突破" noperate="0" fsecond="1.0"/>

<!-- 竞价选股（盘后）: cfirst="VAR1", 排名前5, 不复权 -->
<func nset="0" ntjindexno="266" accode="竞价选股自定义" nperiod="4" cfirst="VAR1" noperate="6" fsecond="5.0" bnost="1" bnotp="1" nperiodnum="100"/>

<!-- 热点板块（盘后）: cfirst="连续涨停数", 排名前2 -->
<func nset="0" ntjindexno="424" accode="热点板块" nperiod="4" cfirst="连续涨停数" noperate="6" fsecond="2.0" nperiodnum="1"/>
```

#### 8.5.2 nset=1 条件选股公式（1个样本）

```xml
<!-- 连涨3周: 使用UPN条件选股公式, 周线周期(nperiod=5), noperate=9(买卖/连涨判断) -->
<func nset="1" accode="UPN" nperiod="5" cfirst="MA1" noperate="9" nsecond="-1" csecond="MA3" fsecond="0.0" bnost="1" nperiodnum="1000"/>
```
- `accode="UPN"` = 连涨条件选股公式
- `nperiod="5"` = 周线分析
- `noperate="9"` = 连涨/连跌信号判断
- 截图验证: `条件选股公式连涨3周.png`

#### 8.5.3 nset=2 专家系统公式（4个样本 — 发现 nfirst 信号类型选择器）

> **v6.1 重大发现**: nset=2 专家系统中，**nfirst 是信号类型选择器**，"条件设置"下拉框显示的是信号类型名（非指标线名），cfirst 为残留值。

```xml
<!-- 任意交易信号: nfirst=0 → 接收MACD产生的任何买卖信号 -->
<func nset="2" accode="MACD" nperiod="4" nfirst="0" cfirst="MA1" noperate="2" fsecond="10.0" bnost="1" bnotq="1" nperiodnum="1000"/>

<!-- 多头买入信号: nfirst=1 → 仅接收MACD金叉买入信号 (截图验证) -->
<func nset="2" accode="MACD" nperiod="4" nfirst="1" cfirst="MA1" noperate="2" fsecond="10.0" bnost="1" bnotq="1" nperiodnum="1000"/>

<!-- 多头卖出信号: nfirst=2 → 仅接收MACD死叉卖出信号 -->
<func nset="2" accode="MACD" nperiod="4" nfirst="2" cfirst="MA1" noperate="2" fsecond="10.0" bnost="1" bnotq="1" nperiodnum="1000"/>
```

**nfirst 信号类型枚举（nset=2 专家系统专用）**:

| nfirst值 | 含义 | UI"条件设置"下拉框显示 | 说明 |
|----------|------|---------------------|------|
| 0 | 任意交易信号 | "任意交易信号" | 接收专家系统产生的所有买卖信号 |
| 1 | 多头买入信号 | "多头买入信号" | 仅接收买入/做多信号 | 
| 2 | 多头卖出信号 | "多头卖出信号" | 仅接收卖出/做空信号 |

> **注意**: 截图 `多头买入信号.png` 清晰显示：
> - 左侧树选中 **专家系统公式 → MACD - MACD专家系统**
> - 条件设置下拉框 = **"多头买入信号"** ← 对应 nfirst=1
> - 参数设置区显示 MACD 标准参数(12/26/9)
> - 复选框: 数据不复权☑ + 删除ST品种☑

**nset=2 的特殊行为总结**:

| 参数 | nset=0(技术指标)时含义 | nset=2(专家系统)时含义 | 原因 |
|------|---------------------|--------------------|------|
| **nfirst** | 遗留字段(通常=0) | **信号类型选择器**(0/1/2) | 专家系统需要指定关注哪种信号 |
| **cfirst** | 指标线名称(MA1等) | **残留占位符**(固定为"MA1") | 客户端未清空默认值，实际由nfirst控制 |
| **noperate** | 比较操作符(0~9) | 固定为 **2**(交叉/条件触发) | 专家系统的信号判断逻辑内置在nfirst中 |

> **⚠️ 不确定性标注**: 之前「专家系统公式macd.xml」样本使用了 noperate="9"，与本次4个控制变量样本的 noperate="2" 不同。二者差异原因待确认（可能是TDX版本差异导致同一功能使用不同noperate值，或noperate=9为旧版"买卖判断模式"、noperate=2为新版"条件交叉模式"）。当前文档以控制变量样本（nset=2 noperate=2）为准。

#### 8.5.4 nset=3 最新财务选股（V9-R2最终确认 — 7个XML样本 + 完整树形结构）

> **[V9-R2最终确认] 数据来源**: `最新财务选股.xml`(5节点) + `流通股本.xml`(1节点) + `ROE排名前10.xml`(1节点) + 5张UI截图(含完整树形结构)
> **确认方法**: XML提供5个绝对锚点(ntjindexno=0/1/9/19/29)，截图1+截图2展示完整树形列表按序排列，**5个锚点与树位置0-indexed完全吻合**

**7个XML样本汇总**:

```xml
<!-- 总股本大于10000万股: ntjindexno=0, noperate=1(大于), fsecond=10000, 单位=万股 -->
<func nset="3" ntjindexno="0" accode="MACD" nperiod="4" nfirst="1" cfirst="MA1" noperate="1" nsecond="-1" csecond="MA3" fsecond="10000.000000"/>

<!-- 流通A股(流通股本)大于10000万股: ntjindexno=1, fsecond=10000, 单位=万股 -->
<func nset="3" ntjindexno="1" accode="MACD" nperiod="4" nfirst="0" cfirst="MA1" noperate="1" nsecond="-1" csecond="MA3" fsecond="10000.000000"/>

<!-- 流动负债大于100万元: ntjindexno=9, noperate=1(大于), fsecond=100, 单位=万元 -->
<func nset="3" ntjindexno="9" accode="" nperiod="4" nfirst="0" cfirst="" noperate="1" nsecond="0" csecond="" fsecond="100.000000"/>

<!-- 净资产(股东权益)大于1000万元: ntjindexno=19, noperate=1(大于), fsecond=1000, 单位=万元 -->
<func nset="3" ntjindexno="19" accode="" nperiod="4" nfirst="0" cfirst="" noperate="1" nsecond="0" csecond="" fsecond="1000.000000"/>

<!-- 净资产收益率排名前10: ntjindexno=29, noperate=4(排名前), fsecond=10 -->
<func nset="3" ntjindexno="29" accode="" nperiod="4" nfirst="0" cfirst="" noperate="4" nsecond="0" csecond="" fsecond="10.000000"/>
```

> **[V9-R2最终确认] nset=3 ntjindexno 财务指标索引 — 30项完整映射表**:

| ntjindexno | 财务指标名称 | 数据类型 | 置信度 | 验证方式 |
|------------|-------------|---------|--------|---------|
| **0** | **总股本** | 万股 | ✅ **100% XML** | `最新财务选股.xml` id=1003 |
| **1** | **流通A股(流通股本)** | 万股 | ✅ **100% XML** | `流通股本.xml` id=1003 |
| 2 | B股/A股 | 万股 | 🔵 **95% 树序** | 截图1树第3项 |
| 3 | H股 | 万股 | 🔵 **95% 树序** | 截图1树第4项 |
| 4 | AB股总市值 | 元 | 🔵 **95% 树序** | 截图1树第5项 |
| 5 | 流通市值 | 元 | 🔵 **95% 树序** | 截图1树第6项 |
| 6 | 总资产 | 万元 | 🔵 **95% 树序** | 截图1树第7项 |
| 7 | 流动资产 | 万元 | 🔵 **95% 树序** | 截图1树第8项 |
| 8 | 无形资产 | 万元 | 🔵 **95% 树序** | 截图1树第9项 |
| **9** | **流动负债** | 万元 | ✅ **100% XML** | `最新财务选股.xml` id=1512 |
| 10 | 资本公积金 | 万元 | 🔵 **95% 树序** | 截图1树第11项 |
| 11 | 应收账款 | 万元 | 🔵 **95% 树序** | 截图1树第12项 |
| 12 | 营业利润 | 万元 | 🔵 **95% 树序** | 截图1树第13项 |
| 13 | 投资收益 | 万元 | 🔵 **95% 树序** | 截图1树第14项 |
| 14 | 经营现金流量 | 万元 | 🔵 **95% 树序** | 截图1树第15项 |
| 15 | 总现金流量 | 万元 | 🔵 **95% 树序** | 截图1树第16项 |
| 16 | 存货 | 万元 | 🔵 **95% 树序** | 截图1树第17项 |
| 17 | **净利润** | 万元 | 🔵 **95% 树序** | 截图1树第18项 |
| 18 | **未分配利润** | 万元 | 🔵 **95% 树序** | 截图1树第19项 |
| **19** | **净资产(股东权益)** | 万元 | ✅ **100% XML** | `最新财务选股.xml` id=1513 |
| 20 | 每股净资产(BPS) | 元/股 | 🔵 **95% 树序** | 截图2续第1项 |
| 21 | 每股公积金 | 元/股 | 🔵 **95% 树序** | 截图2续第2项 |
| 22 | 每股未分配利润 | 元/股 | 🔵 **95% 树序** | 截图2续第3项 |
| 23 | 每股收益(EPS) | 元/股 | 🔵 **95% 树序** | 截图2续第4项 |
| 24 | 股东权益比 | % | 🔵 **95% 树序** | 截图2续第5项 |
| 25 | 少数股权 | 万元 | 🔵 **95% 树序** | 截图2续第6项 |
| 26 | 营业收入 | 万元 | 🔵 **95% 树序** | 截图2续第7项 |
| 27 | 利润总额 | 万元 | 🔵 **95% 树序** | 截图2续第8项 |
| 28 | 税后利润(净利润) | 万元 | 🔵 **95% 树序** | 截图2续第9项 |
| **29** | **净资产收益率(ROE)** | % | ✅ **100% XML** | `ROE排名前10.xml` id=1003 |

> **推断依据**: 通达信「最新财务选股」对话框左侧树形列表使用标准0-based索引。5个XML锚点代入树位置完全吻合：#0→树第1项"总股本", #1→树第2项"流通A股", #9→树第10项"流动负债"(隔7项), #19→树第20项"净资产"(隔9项), #29→树第30项末项"ROE"。**经修正确认#17=净利润、#18=未分配利润，0~29连续30项无间断无跳号。**
>
> **nset=3 特殊行为**: noperate支持大于(1)/小于(2)/排名前(4)；单位体系—股本用**万股**、金额用**万元**、比率无单位(%)、每股用**元/股**；accode残留旧值但不参与逻辑；参数设置区域部分灰色禁用（同nset=4行为）

#### 8.5.5 nset=4 实时行情/逻辑运算基础条件（1个样本）

```xml
<!-- 现价小于10元: nset=4基础条件, noperate=2(小于), fsecond=10 -->
<func nset="4" nperiod="4" cfirst="MA1" noperate="2" nsecond="-1" csecond="MA3" fsecond="10.0" bnost="1" nperiodnum="0"/>
```
- nset=4 用于实时行情条件或逻辑运算的基础条件节点
- `noperate="2"` = 小于比较
- 截图验证: `实时行情选股现价小于10元.png`

##### nset=4 专用 noperate 枚举 [V9最终确认]

**数据来源**: `实时行情选股.xml`(12个节点) + 截图3(下拉框完整选项列表)

| noperate | 含义 | 下拉框选项 | XML验证样本数 | 典型用法 |
|----------|------|-----------|-------------|---------|
| **0** | **等于(==)** | 等于 | 推断(下拉有此选项) | — |
| **1** | **大于(>)** | 大于 | ✅ **4个样本** | 现价>3元 / 今开>5元 / 涨幅>8% / 量比>15 |
| **2** | **小于(<)** | 小于 | ✅ **4个样本** | 最高<10元 / 昨收<5元 / 振幅<3% / 市盈<100 |
| **3** | **穿越/等于变体?** | (第4项,待读清) | ⚠️ **1个样本** | id=1501: 最低(ntj=2) nop=3 f=100 |
| **4** | **排名前(N)** | 排名前 | ✅ **3个样本** | 总量排名前10 / 总金额排名前100 / 换手率排名前10 |
| **5** | **排名为(==第N)** | 排名为 | V8已确认(nset=0) | — |
| **6?** | **排名后(倒数第N)?** | **🆕 排名后** | 🆕 截图3下拉第7项(新发现) | 待XML验证 |

> **V9修正**: V8中noperate=3标注为"上穿"主要来自看盘快照.xml的nset=4节点，但在实时行情选股.xml的完整样本中noperate=3仅出现1次(id=1501, ntjindexno=2=最低)，语义可能不同于"上穿"。截图3下拉框第4项的具体文字待进一步辨认。
>
> **🆕 新发现: noperate含"排名后"选项**（截图3下拉框第7项），对应值推测为6或更高，当前XML样本中未出现。

#### 8.5.6 nset=5 集合操作（并集/交集/差集）— 多节点协作模式

集合操作需要 **多个 type=3 条件节点协作**，是唯一的多节点模式：

```
拓扑结构:
1001(候选池7) ──→ 1003(nset=4,条件A) ──→ 1002(状态池8)
     │
     ├──→ 1004(nset=4,条件B) ──→ 1005(状态池8)
     │                                    │
     └────────────────────────────────────┼──→ 1006(nset=5,集合操作) ──→ 1007(状态池8)
                                          │
1002 ────────────────────────────────────┘
1005 ────────────────────────────────────┘
```

**三个集合操作的差异仅在于 nset=5 节点的 `ntjindexno` 值：**

| 操作 | ntjindexno | 含义 | 样本文件 | 对应flow的emptyps |
|------|-----------|------|---------|-------------------|
| 并集 ∪ | 0 | A ∪ B (合并去重) | 逻辑运算选股并集.xml | flow(1002→1006)=0, flow(1005→1006)=0 |
| 差集 A-B | 1 | A \ B (A中有B中没有) | 逻辑运算选股差集.xml | flow(1002→1006)=**1**, flow(1005→1006)=0 |
| 交集 ∩ | 2 | A ∩ B (同时属于AB) | 逻辑运算选股交集.xml | flow(1002→1006)=0, flow(1005→1006)=0 |

```xml
<!-- 并集: ntjindexno=0 -->
<func nset="5" ntjindexno="0" nperiod="4" noperate="0"/>

<!-- 差集: ntjindexno=1, 注意 emptyps=1 表示从源池中移除 -->
<func nset="5" ntjindexno="1" nperiod="4" noperate="0"/>

<!-- 交集: ntjindexno=2 -->
<func nset="5" ntjindexno="2" nperiod="4" noperate="0"/>
```

> **关键发现 — 集合操作的 emptyps 语义**：
> - 并集/交集: 所有输入 flow 的 `emptyps=0`（正常转移）
> - 差集: 从被减集中移除的 flow 设 `emptyps=1`（标记为待排除）
> - 这意味着差集操作通过 flow 属性而非 func 参数来控制行为！

### 8.6 nset=5 直接转移（无条件透传）

```xml
<!-- 看盘快照：无条件直接转移 -->
<func nset="5" ntjindexno="0" accode="" nperiod="4" nfirst="0" cfirst="" noperate="0" nsecond="0" csecond="" fsecond="0.000000" nbeginday="0" nendday="0" bnost="0" bnotp="0" bnotq="0" nperiodnum="0"/>
```

nset=5 在单入边（仅1条flow进入）模式下表示完全无条件，所有股票直接透传到下一个节点。与8.5.6节多入边集合运算的区别：单入边=直接透传，双入边=集合运算，两者func参数完全一致（`nset="5" ntjindexno="0" noperate="0"`），仅靠拓扑结构（入边数量）区分行为。

### 8.8 nset类型「转移条件」对话框 UI控件可用性矩阵（V9-R3核心发现）

> **[V9-R3 核心架构发现]**: 6种nset类型并非只是"不同数据源"，它们代表**4种完全不同的数据类型范式**，每种范式决定了「转移条件」对话框中哪些UI控件**可见/可用/置灰/隐藏**。这是理解TDX股票池条件体系的关键钥匙。
>
> **数据来源**: 9张客户端配置界面截图 + 全部38个temp XML样本 + 用户领域知识确认

#### 8.8.1 四大数据类型范式

| 范式 | nset | 数据本质 | 类比 | 是否有时间维度 |
|------|------|---------|------|-------------|
| **时间序列 (Time Series)** | **0** | 指标线序列 (MA1, MA2, MACD.DIF...) | K线上的曲线 | ✅ 有 — 每个周期一个值 |
| **信号事件 (Signal)** | **1, 2** | 公式输出信号 (买/卖/连涨N天/突破) | 事件触发器 | ⚠️ 有周期参数但输出为布尔 |
| **标量值 (Scalar)** | **3, 4** | 单一数值 (ROE%, 现价, 总股本, 量比) | 快照数字 | ❌ 无 — 当前时刻单一值 |
| **集合运算 (Set Op)** | **5** | 状态池间运算 (∪ / \ / ∩) | 集合论操作 | N/A |

#### 8.8.2 UI控件可用性完整矩阵

> 截图对照: [技术指标ma.png](file:///h:/new_tdx_mock/PYPlugins/meta_core/temp/转移条件/技术指标ma.png) = nset=0 | [条件选股公式连涨3周.png](file:///h:/new_tdx_mock/PYPlugins/meta_core/temp/转移条件/条件选股公式连涨3周.png) = nset=1 | [专家系统公式多头买入信号.png](file:///h:/new_tdx_mock/PYPlugins/meta_core/temp/转移条件/专家系统公式多头买入信号.png) = nset=2 | [最新财务选股1.png](file:///h:/new_tdx_mock/PYPlugins/meta_core/temp/转移条件/最新财务选股1.png) = nset=3 | [实时行情选股不可设置属性.png](file:///h:/new_tdx_mock/PYPlugins/meta_core/temp/转移条件/实时行情选股不可设置属性（参数设置、周期数量、时间段内满足条件）.png) = nset=4 | [逻辑运算选股并集.png](file:///h:/new_tdx_mock/PYPlugins/meta_core/temp/转移条件/逻辑运算选股并集.png) = nset=5

| UI控件区域 | 对应func字段 | nset=0 序列 | nset=1 信号(条件) | nset=2 信号(专家) | nset=3 标量(财务) | nset=4 标量(行情) | nset=5 集合 |
|-----------|------------|------------|------------------|------------------|------------------|------------------|-----------|
| **计算周期** dropdown | `nperiod` | ✅ 可用 (日线/周线/月线...) | ✅ 可用 | ✅ 可用 | ⚠️ 存在但无意义 | ⚠️ 存在但无意义 | ⚠️ 存在但无意义 |
| **周期数量** input | `nperiodnum` | ✅ 可用 (1000等) | ✅ 可用 | ✅ 可用 | ❌ **禁用** | ❌ **禁用** | ⚠️ 无意义 |
| **参数设置** 区域 | `nfirst`+`cfirst` | ✅ 显示指标参数(MA的5/10/20) | ✅ 显示公式参数(连=[3~5]天) | ✅ 显示专家参数(12/26/9) | ❌ **空白/隐藏** | ❌ **空白/隐藏** | ❌ **空白** |
| **时间段内满足条件** checkbox | `nbeginday`+`nendday` | ✅ 可勾选 | ⚠️ 可见 | ⚠️ 可见 | ❌ **灰色禁用** | ❌ **灰色禁用** | ⚠️ 可见 |
| **条件设置-线条选择** | `cfirst`+`csecond` | ✅ **两行**: Line1[MA1▼]+Line2[MA2▼] | ❌ **不存在** | ❌ **不存在**(改为信号选择器) | ❌ **不存在** | ❌ **不存在** | ❌ **不存在** |
| **条件设置-操作符** dropdown | `noperate` | ✅ **10项全量** | ⚠️ 由公式内部定义 | ⚠️ 仅信号判断 | ✅ **标量子集**(4~5项) | ✅ **标量子集**(4~5项) | ✅ **集合操作**(3项) |
| **条件设置-阈值** input | `fsecond` | ✅ 可输入 | ⚠️ 可能不需要 | ⚠️ 可能不需要 | ✅ 唯一输入项 | ✅ 唯一输入项 | ❌ 无 |
| **条件设置-单位标签** | (UI only) | 无 | 无 | 无 | ✅ 万股/万元/%/元/股 | ✅ 元/手/%/倍 | 无 |

#### 8.8.3 noperate 操作符 — 上下文依赖枚举（关键修正）

> **⚠️ V9-R3重大修正**: noperate 的含义**不是全局统一的**，而是**依赖于 nset 数据类型**的！同一数字在不同 nset 下含义完全不同。

**nset=0 时间序列 — 完整10项操作符** (来自 [条件设置.png](file:///h:/new_tdx_mock/PYPlugins/meta_core/temp/转移条件/条件设置.png) 下拉框截图):

| noperate | 含义 | XML验证样本 | 说明 |
|----------|------|-----------|------|
| 0 | 等于 | `ma1等于ma2.xml` | A == B (逐点比较) |
| 1 | 大于 | `ma1大于ma3.xml` | A > B (逐点比较) |
| 2 | 小于 | 多个XML通用 | A < B (逐点比较) |
| **3** | **上穿** | `ma1上穿ma3.xml` | A从下方穿越B (需历史序列) |
| **4** | **下穿** | 推测(截图有此项) | A从上方穿越B (需历史序列) |
| 5 | 持股 | 待验证 | A持续>B N周期 |
| **6** | **排名前** | `ma1排名前3.xml` | A值排名前N (fsecond=N, nbeginday=3) |
| **7** | **排名后** | `ma1排名后5...xml` | A值排名倒数第N (fsecond=5) |
| **8** | **上拐** | 待XML验证 | A斜率由负转正 (需导数计算) |
| **9** | **下拐** | `ma1下拐...xml` | A斜率由正转负 (需导数计算) |

> **注意**: 上穿/下穿/上拐/下拐 这4项是**纯时间序列操作**，需要至少2个历史数据点才能计算。因此它们**只存在于 nset=0** 中。

**nset=3/4 标量值 — 仅标量比较子集 (~4~5项)**:

| noperate | 含义 | nset=3验证 | nset=4验证 |
|----------|------|-----------|-----------|
| 0 | 等于 | 推测可用 | 推测可用 |
| 1 | 大于 | ✅ id=1003 fsecond=10000 | ✅ id=1506 fsecond=8 |
| 2 | 小于 | 推测可用 | ✅ id=1003 fsecond=10 |
| 4 | 排名前 | ✅ id=1514 fsecond=10 | ✅ id=1504 fsecond=10 |
| 6? | 排名后? | 截图下拉框显示存在 | ✅ 截图3下拉框第7项 |

> **不存在于 nset=3/4 的操作符**: 上穿(3)/下穿(4)/持股(5)/上拐(8)/下拐(9) — 因为标量值没有时间序列维度，无法执行这些操作。

**nset=1 条件选股公式 — 公式内部定义**:

| noperate | 含义 | 验证 |
|----------|------|------|
| 9 | 连涨N周期 / 公式默认触发 | ✅ `连涨3周.xml` noperate=9 |

> nset=1 的 noperate 由**每个条件选股公式自身定义**，不是通用的比较操作符。UI上甚至不显示操作符选择（因为条件设置区域为空）。

**nset=2 专家系统 — 信号判断**:

| noperate | 含义 | 验证 |
|----------|------|------|
| 2 | 条件交叉触发 (多数样本) | ✅ 4个控制变量XML |
| 9 | 买卖信号判断 (旧版?) | ✅ 1个旧样本 |

**nset=5 集合运算 — 操作符即运算类型**:

| noperate | 含义 | XML验证 |
|----------|------|---------|
| 0 | 并集 (A ∪ B) | ✅ `并集.xml` |
| 1 | 差集 (A \ B) | ✅ `差集.xml` |
| 2 | 交集 (A ∩ B) | 推测(树中有此选项) |

#### 8.8.4 func字段在各nset类型中的有效性总结

> **[V9-R3 设计原则] 残留字段不用管**: TDX客户端对所有nset类型统一使用16-field func结构，但**每种类型只读取自己需要的子集**。未被读取的字段保留上一次操作的残留值（常见为"MA1"/"MA3"/-1/0/1000等），**这些残留值无任何语义意义，引擎实现时应直接忽略**。

| func字段 | nset=0 序列 | nset=1 信号(条件) | nset=2 信号(专家) | nset=3 标量(财务) | nset=4 标量(行情) | nset=5 集合 |
|----------|:----------:|:----------------:|:----------------:|:----------------:|:----------------:|:--------:|
| `accode` | ✅ 指标代码("MA") | ✅ 公式名("UPN") | ✅ **专家代码**("MACD") | ❌ 残留 | ❌ 残留 | ❌ 空 |
| `ntjindexno` | ⚠️ 通常=0 | ⚠️ 通常=0 | ⚠️ 通常=0 | ✅ **财务索引(0-29)** | ✅ **行情索引(0-11)** | ⚠️ 通常=0 |
| `nperiod` | ✅ 周期编码 | ✅ 周期编码 | ✅ 周期编码 | ❌ 残留(通常=4) | ❌ 残留(通常=4) | ❌ 残留 |
| `nfirst` | ✅ 参数索引 | ✅ **公式参数索引** | ✅ **信号选择器**(见下表) | ❌ 无效 | ❌ 无效 | ❌ 无效 |
| `cfirst` | ✅ **线条1名**("MA1") | ❌ 残留 | ❌ 残留 | ❌ 无效 | ❌ 无效 | ❌ 无效 |
| `noperate` | ✅ **10项全量** | ✅ **公式内部定义** | ✅ **信号判断模式** | ✅ **标量子集** | ✅ **标量子集** | ✅ **集合操作** |
| `nsecond` | ✅ 参数索引 | ❌ 残留(-1) | ❌ 残留(-1) | ❌ 无效 | ❌ 无效 | ❌ 无效 |
| `csecond` | ✅ **线条2名**("MA2/MA3") | ❌ 残留 | ❌ 残留(MA3) | ❌ 无效 | ❌ 无效 | ❌ 无效 |
| `fsecond` | ✅ 阈值/排名N | ⚠️ 可能不用 | ❌ 残留(0或10) | ✅ **唯一有效值** | ✅ **唯一有效值** | ❌ =0 |
| `nbeginday` | ✅ 时间段起始 | ⚠️ | ⚠️ | ❌ =0 | ❌ =0 | ❌ =0 |
| `nendday` | ✅ 时间段结束 | ⚠️ | ⚠️ | ❌ =0 | ❌ =0 | ❌ =0 |
| `nperiodnum` | ✅ 周期数 | ✅ 周期数 | ✅ 周期数 | ❌ =0 | ❌ =0 | ❌ =0 |

#### 8.8.5 nset=2 专家系统 — nfirst信号选择器完整映射（4样本确认）

> 数据来源: 4个专家系统控制变量XML + [专家系统公式macd.png](file:///h:/new_tdx_mock/PYPlugins/meta_core/temp/转移条件/专家系统公式macd.png) + [多头买入信号.png](file:///h:/new_tdx_mock/PYPlugins/meta_core/temp/转移条件/专家系统公式多头买入信号.png)

| nfirst | 信号名称 | noperate | XML样本 | 截图证据 |
|--------|---------|----------|---------|---------|
| **0** | **任意交易信号** | 2 (或9旧版) | `任意交易信号.xml`, `专家系统macd.xml`(noperate=9) | macd.png下拉框显示"任意交易信号"被选中 |
| **1** | **多头买入信号** | 2 | `多头买入信号.xml` | 多头买入信号.png 下拉框选中此项 |
| **2** | **多头卖出信号** | 2 | `多头卖出信号.xml` | 推测(与#1对称) |

> **nset=2 残留特征**: 所有4个样本中 `cfirst="MA1" csecond="MA3"` 完全一致 — 这是TDX客户端在切换到专家系统模式前最后一次使用技术指标(nset=0)时选择的MA线条名，**作为残留值写入但完全不参与专家系统逻辑**。同理 `fsecond="10"` 或 `"0"` 也是残留。
>
> **noperate分歧解释**: noperate=2(条件交叉触发)是新版主流模式(3/4样本)；noperate=9(买卖信号判断)出现在旧版样本(1/4样本)。两者可能对应不同版本的专家系统评估逻辑，或同一版本中两种可选的信号判断策略。

### 8.7 代码实现中的条件评估

tdx_executor.py `_evaluate_tdx_condition()`:
```python
func_params = condition_node.get("params", {})
nset = func_params.get("nset", 1)
noperate = func_params.get("noperate", 0)

# nset=1: 系统指标，模拟模式下全部通过
# nset=0: 自定义公式，模拟模式下全部通过
if noperate == 0:
    return list(stock_list)  # 存在即通过
elif noperate == 1:
    return list(stock_list)  # 比较操作，模拟通过
elif noperate == 3:
    return list(stock_list)  # 排名操作，模拟通过
else:
    return list(stock_list)  # 默认通过
```

---

## 九、执行模型

### 9.1 执行的最小单位

执行的最小单位是**Flow（边）**。每条flow：
1. 从 startid 源节点获取股票列表
2. 如果 target 是条件节点(type=3)，评估 func 条件
3. 按 tran 模式（0=copy/1=move）转移股票到目标节点
4. 如果 target 是状态池(type=8)，应用 psatt 设置
5. 处理 emptyps=1 的特殊情况

### 9.2 执行顺序

**执行顺序控制股票池中所有转移条件的执行先后。执行顺序是全局的，不是仅限于同一上游节点的扇出场景。**

**关键概念**：
- **条件边（参与编号）**：源端为池的边，无论目标是什么。包括两种情形：
  - pool → 条件节点（type=3）的边（`tran=1`）
  - TDX 特有：pool → pool 直连边（源=池、目标=池），也属于条件边，参与编号
- **无条件边（不参与编号）**：源端为条件节点（type=3）的边（`tran=0`），不显示徽标

**操作方式**（与 DZH §6.2 一致）：
1. 在设计状态下，点击工具栏的"执行顺序"按钮进入编号模式
2. 所有条件边的两端点附近会出现一个数字徽标，显示该边的当前执行顺序编号
3. 编号从 **1** 开始（1, 2, 3, ...），数字越小执行越早
4. 用鼠标点击每条条件边，第一次点击的边编号为 1，第二次为 2，直到所有条件边编号完成
5. 如果点击已有编号的边，该边的编号与当前编号交换（冲突时自动调整）
6. 点击画布空白处退出编号模式

**XML记录方式**：

Flow按照在 XML 中 `<flows>` 内的排列顺序依次执行。排在前面的 flow 先执行。flow 元素排列顺序 = 执行顺序。

**持久化规则**：连接到同一条件节点的条件边（源=池）与其对应的无条件边（源=条件节点）在`<flows>`中成对排列，保持拓扑一致性。

### 9.3 执行方法签名

tdx_executor.py `TdxPoolExecutor.execute_once()`:
```python
def execute_once(self) -> Dict[str, Any]:
    """执行一次完整的池转移流程。

    Returns:
        {
            "success": bool,
            "transferred": int,     # 本次转移的股票总数
            "passed": int,          # 通过条件的股票数
            "rejected": int,        # 被拒绝的股票数
            "node_states": Dict,    # 各节点最终股票状态
            "transfer_events": List[Dict],  # 每条边的转移明细
        }
    """
```

### 9.4 五种流转拓扑模式

基于6个XML样本的拓扑分析：

**模式A：串行链式**
```
7(备选) → 3(条件A) → 8(状态A) → 3(条件B) → 8(状态B) → ...
```
实例：黑马二号池
```
7(全部A股) → 3(牛牛264) → 8(初选池) → 3(止盈113) → 8(止盈池)
                                    → 3(反弹122) → 8(反弹池)
                                    → 3(龙头239) → 8(龙头池) → 3(短线首板278) → 8(短线首板池)
```

**模式B：扇出分流** (黑马全息股池)
```
                     → 3(120日内新高)    → 8(120日内新高池)
7(全部A股) → 扇出 → 3(趋势下跌)       → 8(趋势下跌池)
                     → 3(强牛奔腾)       → 8(强牛奔腾池)
                     → 3(趋势上修)       → 8(趋势上修池)
                     → 3(追强)           → 8(追强池)
                     → 3(趋势金步)       → 8(趋势金步池)
                     → 3(强牛回头)       → 8(强牛回头池)
                     → 3(主力控盘)       → 8(主力控盘池)
```
所有条件共享同一个备选池id=1，各自筛选后输出到独立状态池。

**模式C：扇入汇合** (黑马全息股池)
```
8(120日内新高池) ─┐
8(趋势下跌池)    ─┤
8(趋势上修池A)   ─┤
8(趋势金步池)    ─┼→ 8(总池 id=5)
8(趋势上修池B)   ─┤
8(强牛奔腾池/主力控盘/强牛回头) ─┘
```
多个状态池汇入同一个最终输出池。

**模式D：多层漏斗** (黑马一号池)
```
7(A股) → 3(粘合106) → 8(粘合<10) → 3(牛股107) → 8(牛股>1) → 3(龙头207) → 8(强势行业)
                                                                              → 3(日内突破306) → 8(最终) → 3(资金龙头110) → 8(资金龙头)
                                                                                                            → 3(龙头首板111) → 8(龙头首板)
```
每层条件进一步过滤上一层的输出。

**模式E：多源并行** (大路终结池 — 3个备选池并行)
```
7(沪深A股 id=1) ─→ 3(去ST 257) → 8(去芜存菁 id=14) → 3(小品2_1/3/9/4/6/5) → 8(去芜存菁/趋势/大路中各池)
                                                                              → ... → 8(大路终点各池)
7(板块指数 id=16) ─→ 3(热点板块 264) → 8(板块池 id=15) → 最终
7(创业板 id=20)   ─→ 独立路径
```

**模式F：多指标并行** (盘后.xml)
```
7(全部A股 spinfo) → 扇出 → 3(连涨3天212) → 8(3天内强池) ─┐
                          → 3(连涨5天213) → 8(5天内强池) ─┤
                          → 3(强势新高214) → 8(强势新高池) ─┤
                          → 3(竞价V1 266)  → 8(竞价选股)  ─┤
                          → 3(竞价V2 266)  → 8(竞价终点)  ─┤
                          → 3(最高换手209) → 8(追涨备选)  ─┤
                          → 3(探底回升211) → 8(振幅信号)  ─┤
                          → 3(昨日涨停150) → 8(昨日涨停)  ─┤
                          → 3(获利超8 217) → 8(获利超8)   ─┤
                          → ...                           → 8(各输出池)
```

### 9.5 emptyps 空池逻辑代码实现

```python
# 如果源为空且 emptyps != 1，跳过此flow
if not src_stocks and emptyps != 1:
    return self._build_event(edge, src_id, tgt_id, mode, [], 0, 0, 0, skipped=True)

# 否则即使源空也会尝试执行（用于大路终结池的板块路径）
```

---

## 十、数据流转与程序分层

### 10.1 完整数据管线

```
TDX XML ──[tdx_xml_raw.py 解析]──→ TdxPoolMetaModel (原始模型: cells含TdxCellModel, flows含TdxFlowModel)
    │                                          │
    │                              [tdx_to_internal() 转换]
    │                                          ↓
    │                              PoolMetaModel (统一内部模型: cell_type 202/200/201/...)
    │                                          │
    │                              [convert_tdx_to_config()]
    │                                          ↓
    │                              引擎配置 {"pool_meta":{...}, "nodes":[...], "edges":[...]}
    │                                          │
    │                              [TdxPoolExecutor 执行]
    │                                          ↓
    └──────────────────────← 执行结果 (success/transferred/passed/rejected/node_states/transfer_events)
```

### 10.2 程序分层

```
┌──────────────────────────────────┐
│  引擎层 (engine.py)               │  run_tdx_pool_from_file() → 统一入口
│    run_pool() 按 pool_type 路由   │
├──────────────────────────────────┤
│  转换层                           │
│  ├ tdx_xml_raw.py                │  XML → TdxPoolMetaModel (原始模型)
│  ├ tdx_internal_converter.py     │  TdxPoolMetaModel → PoolMetaModel
│  ├ tdx_converter.py              │  PoolMetaModel → 引擎配置 {nodes:[], edges:[]}
│  └ tdx_executor.py               │  引擎配置 → 执行结果 (execute_once)
├──────────────────────────────────┤
│  模型层 (schemas.py)              │  TdxFuncModel(16字段), TdxPsattModel(14字段),
│                                  │  TdxSpinfoModel(3字段), TdxStkModel(14字段),
│                                  │  TdxCellModel, TdxFlowModel,
│                                  │  TdxPoolMetaModel
└──────────────────────────────────┘
```

### 10.3 节点类型映射 (tdx_converter.py)

```python
# TDX cell_type → 引擎节点类型
cell_type=202 → "tdx_candidate"   (候选池/股票源)
cell_type=200 → "tdx_state_pool"  (输出/状态池)
cell_type=201 → "tdx_condition"   (转移条件)
cell_type=1,2,3,4,6,203 → "decoration" (装饰性元素)
```

### 10.4 Flow 转换 (tdx_converter.py _convert_flow)

```python
tdx_tran = getattr(flow, "tdx_tran", None) or 0
mode = "move" if tdx_tran == 1 else "copy"

return {
    "source": {"node_id": str(from_cell_id)},
    "target": {"node_id": str(to_cell_id)},
    "params": {
        "mode": mode,     # "move" or "copy"
        "tdx_clr": ...,
        "tdx_size": ...,
        "tdx_tran": ...,
        "emptyps": 0,
        "starttype": 0,
        "jgtime": 60,
    },
}
```

---

## 十一、6个实战样本分析统计

### 11.1 黑马全息股池.xml

| 指标 | 数值 |
|------|------|
| nextid | 40 |
| backcolor | 4227200 (深蓝) |
| Cell总数 | 28 |
| type=7 备选池 | 1 (id=1, setcode=0 全部深市A股) |
| type=8 状态池 | 14 (总池id=5 + 13个策略输出池) |
| type=3 条件 | 9 (8种选股策略 + 强牛回头) |
| type=2 容器 | 4 (4个装饰标题) |
| type=0 装饰文字 | 1 |
| Flow总数 | 28 |
| 拓扑特征 | 星型扇出(1→8个条件) + 扇入汇合(多状态池→总池) |
| psatt特点 | 全部 bdel=0（不限制容量），无 bsavetoblock |

**状态池列表**:
| id | text(解码后) | clr |
|----|------------|-----|
| 5 | 总池 | 16711935 |
| 9 | 120日内新高 | 16711680 |
| 10 | 趋势上修 | 32768 |
| 13 | 强牛奔腾 | 16776960 |
| 14 | 趋势下跌 | 65280 |
| 20 | 趋势上修(小) | 33023 |
| 21 | 追强池 | 65535 |
| 22 | 趋势上修(中) | 32768 |
| 23 | 趋势金步 | 16711808 |
| 27 | 趋势下跌(小) | 65280 |
| 28 | 趋势金步(小) | 16711808 |
| 29 | 120日内新高(小) | 16711680 |
| 30 | 总池(汇入) | 16711935 |
| 33 | 主力控盘 | 16744576 |
| 35 | 强牛回头 | 8388672 |

### 11.2 黑马一号池.xml

| 指标 | 数值 |
|------|------|
| nextid | 31 |
| backcolor | 1114112 (深绿) |
| Cell总数 | 17 |
| type=7 备选池 | 1 (id=1 "全部A股" clr=7405681) |
| type=8 状态池 | 9 |
| type=3 条件 | 9 |
| type=1 文字标签 | 1 (id=11 说明文字) |
| type=0 装饰文字 | 1 (id=21 说明文字) |
| Flow总数 | 17 |
| 拓扑特征 | 多层漏斗（串行+分支：粘合→牛股→龙头→各出口） |
| psatt特点 | 中间池 bdel=1 ndelnum=2；最终池 bdel=1 ndelnum=5 baimpool=1 btip=1 |
| 时间调度 | flow 16→17 starttimehms=140000 (14:00执行) |

**拓扑路径**:
```
7(A股 id=1)
  ├─→ 3(粘合 106) → 8(粘合<10 id=3)
  │       ├─→ 3(牛股 107) → 8(牛股>1 id=5) → 3(龙头 207) → 8(强势行业 id=23)
  │       │                                              ├─→ 3(日内突破 306) → 8(最终 id=9)
  │       │                                              ├─→ 3(资金龙头 110) → 8(资金龙头 id=25)
  │       │                                              └─→ 3(龙头首板 111) → 8(龙头首板 id=27)
  │       └─→ 3(日内突破 360周线) → 8(id=13) → 3(日内突破 257) → 8(id=16) → 3(火箭发射 125) → 8(火箭发射 id=18)
  │                                                                                       ├─→ 3(资金龙头 110周线) → 8(资金龙头 id=25)
  │                                                                                       └─→ 3(龙头首板 111) → 8(龙头首板 id=27)
```

### 11.3 黑马二号池.xml

| 指标 | 数值 |
|------|------|
| nextid | 16 |
| backcolor | 4194304 (深蓝) |
| Cell总数 | 13 |
| type=7 备选池 | 1 (id=1 "全部A股" clr=0) |
| type=8 状态池 | 7 |
| type=3 条件 | 7 |
| Flow总数 | 14 |
| 拓扑特征 | 多路径并行扇出（B点信号/反弹信号/龙头股 三路并行→独立输出+二次筛选） |
| psatt特点 | 全部 bdel=1 ndelnum=2；最终池 baimpool=1 btip=1 |
| 日期 | stk数据来自20180524 |

**拓扑路径**:
```
7(A股 id=1) → 3(牛牛 264 first=26) → 8(初选 id=2)
  ├─→ 3(止盈/止损 113 周线) → 8(止盈信号-B点信号 id=3) → 3(B点信号 141) → 8(短线买入B点信号 id=16)
  ├─→ 3(反弹信号 122) → 8(反弹信号 id=4) → 3(主力筑底 149 first=34 noperate=3) → 8(主力筑底+散户淡出 id=14)
  └─→ 3(龙头239 nset=0 accode="龙头股" noperate=1) → 8(龙头股-走势强于行业指数 id=5) → 3(短线首板 278 nset=0) → 8(短线首板 id=12)
```

### 11.4 大路终结池.xml

| 指标 | 数值 |
|------|------|
| nextid | 36 |
| backcolor | 4210816 (深蓝) |
| Cell总数 | 36 |
| type=7 备选池 | 3 (沪深A股 id=1, 创业板 id=20, 板块指数 id=16) |
| type=8 状态池 | 14 |
| type=3 条件 | 14 |
| type=2 容器 | 5 (标题/子标题/日期) |
| Flow总数 | 36 (与 nextid 相同) |
| 拓扑特征 | 三源并行（A股+创业板+板块）+ 多层漏斗 + 扇入汇合 |
| 时间调度 | 大量使用 starttype=2/3, cxtype=1, cxtime=6, cxtimetype=2, jgtime=60/600/1800 |
| psatt特点 | bdel=1, ndelnum 范围 5~365；最终池(牛股质变池 ndelnum=365) |
| emptyps | 2条 emptyps=1 flow（备选池→条件 和 板块→条件） |

**拓扑路径**:
```
7(沪深A股 id=1, emptyps=1) → 3(去ST 257) → 8(去芜存菁 id=14, ndelnum=18 ndeltype=1)
  ├─→ 3(小品2_1 250) → 8(去芜存菁大扫除 id=2, ndelnum=60)
  ├─→ 3(小品2_3 251) → 8(趋势第一步要牢记 id=3, ndelnum=60)
  ├─→ 3(小品2_9 258) → 8(趋势思涨长线求 id=4, ndelnum=60)
  ├─→ 3(小品2_4 252) → 8(趋势思涨短线极 id=5, ndelnum=60)
  ├─→ 3(小品2_6 263) → 8(大路之中军参考 id=6, ndelnum=34)
  └─→ 3(小品2_5 253) → 8(大路之中军参考+ id=7, ndelnum=34)

状态池2/3/4/5 → 3(小品2_10 259, 60分钟) → 8(落叶不知何处去 id=8, ndelnum=5)
状态池8 → 3(小品2_11 260) → 8(桃花依旧笑春风 id=9, ndelnum=120)
状态池6 → 3(小品2_12 261, 60分钟) → 8(关键 id=10, ndelnum=5)
状态池10 → 3(小品2_13 262) → 8(关键底座 id=11, ndelnum=60)
状态池2/3/4/5/6/7 → 3(小品2_7 254) → 8(牛股牛股 id=12, ndelnum=220)
状态池12 → 3(小品2_8 255) → 8(牛股质变 id=13, ndelnum=365)

7(板块指数 id=16, emptyps=1) → 3(热点板块 264) → 8(板块池 id=15)
7(创业板 id=20) → (独立路径，待分析)
```

### 11.5 盘后.xml

| 指标 | 数值 |
|------|------|
| nextid | 无（新版XML） |
| backcolor | 1644825 |
| Cell总数 | ~32 |
| type=7 备选池 | 2 (id=1111 spinfo type=2 "全部A股" size=5513, id=1139 spinfo type=0 "行业板块" size=584) |
| type=8 状态池 | 11 |
| type=3 条件 | 17 |
| Flow总数 | ~30+ |
| 拓扑特征 | 多指标并行扇出（连涨/新高/竞价/换手/探底/涨停/获利）→ 各独立输出池 |
| psatt特点 | bdel=1 ndelnum=18 ndeltype=1 bsavetoblock=1 bclearblock=1 |
| 数据特点 | stk含14字段（新版），setcode含2(北交所)，indate=20260603 |
| blockfile列表 | TJQRM(竞价热门), TJQZD(竞价终点), 3RQS(3天内强), 5RQS(5天内强), GGQS(强势新高), ZZZBT(追涨备选), ZDXHL(振幅信号), ZZT(昨日涨停), GLC8(获利超8), TPNX(突破年线), RDBK(热点板块), QSQD(强势启动) |

**关键func差异**:
- nset=0 自定义公式较多（竞价选股自定义 ntjindexno=266，cfirst=VAR1/VAR2/VAR3）
- noperate=6（特定操作）和 noperate=7（特殊比较）
- bnost=1 bnotp=1（排除ST和停牌）广泛使用
- nperiodnum=10/100/500（周期数量参数活跃使用）

### 11.6 看盘快照.xml

| 指标 | 数值 |
|------|------|
| nextid | 无（新版XML） |
| backcolor | 1644825 |
| Cell总数 | ~33 |
| type=7 备选池 | 3 (id=1377 "沪深指数" spinfo type=4 size=269, id=1380 "自定义板块" size=20, id=1383 "板块指数" size=10) |
| type=8 状态池 | 12 |
| type=3 条件 | 15 |
| type=2 容器 | 1 (id=1378 attr=140 多行说明文字) |
| Flow总数 | ~25+ |
| 拓扑特征 | 三源并行（指数+自定义+板块）→ 条件过滤 → 行业均值过滤 → 成份股拆解 → 板块保存 |
| psatt特点 | bdel=1 ndeltype=2；ndelnum 5~20；最终池 baimpool=1 bsavetoblock=1 bclearblock=1 |
| blockfile列表 | BYGN, BEGN, BSGN, GZBK |
| 数据特点 | stk含14字段新版，indate=20260303 |
| nset=5特色 | 大量使用 nset=5（直接转移/无条件透传）和 nset=4（系统指标+排名操作） |
| 自定义公式 | 行业均值(552)、第一成份(553)、第二成份(554)、第三成份(555)，cfirst="成份" |

---

## 十二、颜色值与编码参考

### 12.1 常用颜色值（GDI 十进制整数）

| 十进制值 | 近似颜色 | 常用场景 |
|---------|---------|---------|
| 16711680 | 红色 | 条件节点/状态池 ("120日内新高", "关键") |
| 65280 | 绿色 | 条件节点/状态池 ("趋势下跌") |
| 65535 | 黄色 | 状态池 ("追强池"), 容器底色 |
| 16776960 | 青色 | 状态池 ("强牛奔腾"), 容器 |
| 33023 | 橙色 | 备选池/状态池/条件 (大路终结池 main) |
| 16711935 | 粉紫 | 状态池 ("总池"), 容器 |
| 16777215 | 白色 | 文字颜色/状态池底色 |
| 255 | 红色 | 文字颜色/备选池底色 |
| 0 | 黑色 | 文字颜色/备选池底色 |
| 127 | 灰色 | 条件节点/flow线条/状态池 |
| 128 | 深灰 | flow线条 |
| 16384 | 深绿 | 条件节点 |
| 32768 | 亮绿 | 状态池 ("趋势上修") |
| 8388672 | 灰蓝 | 状态池 ("强牛回头") |
| 16744576 | 蓝灰 | 状态池 ("主力控盘") |
| 16711808 | 暗紫 | 状态池 ("趋势金步", "大路之中军参考") |
| 16744703 | 粉色 | 状态池 ("大路之中军参考+") |
| 8454143 | 浅蓝 | 文字颜色/备选池底色 |
| 8421376 | 绿色(偏暗) | 状态池 (黑马二号 "止盈信号") |
| 4194432 | 深紫蓝 | 状态池 (黑马二号 "龙头股") |
| 4227327 | 橙色(偏亮) | 状态池 (大路终结 "落叶不知何处去") |
| 10374619 | 土黄 | 状态池 (大路终结 "趋势第一步要牢记") |
| 8388736 | 深绿 | 状态池 (大路终结 "趋势思涨长线求") |
| 8388863 | 浅紫蓝 | 容器/装饰文字底色 |
| 16744448 | 紫红 | 状态池 (大路终结 "关键底座") |
| 7405681 | 深蓝 | 备选池 (黑马一号) |
| 12615935 | 浅粉 | 备选池 (黑马全息) |
| 3289012 | 深蓝绿 | 新版状态池底色 (盘后/看盘快照) |
| 4227200 | 深蓝 | backcolor 背景 (黑马全息) |
| 1114112 | 深绿 | backcolor 背景 (黑马一号) |
| 4194304 | 深蓝 | backcolor 背景 (黑马二号) |
| 4210816 | 深蓝 | backcolor 背景 (大路终结) |
| 1644825 | 深蓝(新版) | backcolor 背景 (盘后/看盘快照) |

### 12.2 setcode 市场编码全表

| setcode | 市场 | 简称 | 代码范围 | 实例 |
|---------|------|------|---------|------|
| 0 | 深圳 | SZ | 000001-004999(主板), 002000-002999(中小板), 300000-300999(创业板), 001xxx(2021后新主板) | 000001, 300750, 002415 |
| 1 | 上海 | SH | 600000-603999(主板), 688000-688999(科创板), 880000-880999(板块指数), 605xxx(2021后新主板) | 600519, 688981, 880501 |
| 2 | 北交所 | BJ | 920000-920999 | 920725, 920190 |

### 12.3 nperiod 分析周期

**已验证值**:
| nperiod | 周期 | 出现频率 | 实例 |
|---------|------|---------|------|
| 3 | 60分钟 | 低 | 大路终结池 id=28/30 (小品2_10/12) |
| 4 | 日线 | 最高 | 绝大多数条件 |
| 5 | 周线 | 中 | 黑马一号池 id=12 (日内突破周线); 黑马二号池 id=7 (止盈周线) |

**推测值（未经验证，基于通达信客户端分析周期下拉框推测）**:
| nperiod | 推测含义 | 验证状态 |
|---------|---------|---------|
| 0 | 分时/实时 | ⚠️ 推测 |
| 1 | 1分钟 | ⚠️ 推测 |
| 2 | 5分钟 | ⚠️ 推测 |
| 6 | 月线 | ⚠️ 推测 |
| 7 | 季线 | ⚠️ 推测 |
| 8 | 年线 | ⚠️ 推测 |
| 9 | 多分钟(自定义) | ⚠️ 推测 |
| 10 | 多日线(自定义) | ⚠️ 推测 |

---

## 十三、与DZH的关键差异总结

| 维度 | TDX | DZH |
|------|-----|-----|
| **XML根结构** | `<root><pool>`（双层嵌套） | `<pool>`（单层） |
| **Pool属性** | 0~2个（nextid可选, backcolor必填） | 8个（type/ver/mode/nextid/backcolor/system/warning/ency） |
| **备选池** | type=7，stk/spinfo 子元素定义股票 | type=202, attrtext 市场选择文本 |
| **状态池** | type=8, psatt 子元素（13~14参数统一管理） | type=200, hold/col/attr位标志分散管理 |
| **条件** | type=3, func 子元素（16参数扁平体系） | type=201, Base64公式+attr位标志（8位路由多类型组合） |
| **条件路由** | nset 区分系统/自定义/直接转移 | attr 位标志(8位)路由到不同处理器 |
| **公式存储** | 系统指标编号(ntjindexno) + 自定义公式名(accode) | Base64编码公式自包含(indi) |
| **丢弃池** | 无（move模式隐式丢弃） | type=4（显式丢弃池） |
| **转移模式** | tran=0(copy)/1(move) 2种 | 6种(attr位标志组合: pass_through/move/copy/overwrite/force_move/output_components) |
| **时间调度** | starttype(0/2/3)+starttime+starttimetype+starttimehms + cxtype+cxtime+cxtimetype + jgtime | begin(0~7)+begint + end(0~2)+endt + interval |
| **交易动作** | 无 | enter/exit 编码（4种动作类型） |
| **股票数据** | 9~14个运行时字段（丰富），now/rise/volume/maxrate等 | 4个字段（label/t/p/tid），精简 |
| **Cell独有属性** | clrtext(文字颜色), solid(填充样式) | url(超链接) |
| **独有类型** | type=0 装饰文字 | type=3 状态列, type=4 丢弃池, type=5 执行顺序, type=203 未知类型 |
| **板块保存** | psatt 级别 bsavetoblock+blockfile+bclearblock | 无 |

---

## 十四、右键交互体系

### 14.1 概述

TDX V8 客户端的右键菜单是一个**三维差异化体系**，菜单项的组成随以下三个维度动态变化：

| 维度 | 取值 | 影响 |
|------|------|------|
| **维度1：元素类型** | 候选池(type=7) / 条件节点(type=3) / 状态池(type=8) / 有条件边(转移连线) / 无条件边(直接连线) | 决定基础菜单项集合 |
| **维度2：运行模式** | 设计模式 vs 运行时 | 运行时锁定属性编辑、说明修改等设计操作 |
| **维度3：选中状态** | 状态池中是否选中了某只股票行 | 仅对状态池有效，选中后额外暴露行级操作（加自选、查看走势、删除该行） |

以下矩阵基于 TDX V8 客户端 14 张右键截图分析得出。

### 14.2 设计模式右键菜单矩阵

**表 14-1：设计模式右键菜单完整矩阵**

| 右键对象 | 菜单项数 | 菜单项列表 | 数据来源字段 |
|---------|---------|-----------|------------|
| 候选池 type=7 | **2** | 属性, 修改说明文字和样式 | `cell.text`, `cell.clrtext` |
| 条件 type=3 | **3** | 属性, 修改说明文字和样式, 停止运算 | `func.*`, `disabled` |
| 状态池 type=8 (无选中) | **6** | 属性, 修改说明文字和样式, 手工添加股票到状态池, 清除状态池中所有股票, 所有股票加入到板块股, 显示行号 | `psatt.*`, `show_row_num` |
| 状态池 type=8 (有选中) | **9** | ↑6项 + 将当前银行加入到自选股, 查看\|删除发行走势, 从本次池中删除该笔(行) | `stk.*`, 行号 |
| 有条件边 (type=3转移边) | **2** | 属性, 线条宽度 | `flow.size`(1-16), `func.*` |
| 无条件边 (直接连线) | **1** | 线条宽度 | `flow.size`(1-16) |

> **注**："将当前银行加入到自选股"为客户端原始文案，"银行"疑为"行情"或"品种"之误写。

### 14.3 运行时右键菜单矩阵

**表 14-2：运行时右键菜单矩阵**

运行模式下，画布进入**锁定状态**——属性编辑、说明修改、停止运算等设计操作均被禁用，仅保留部分查看与导出功能。

| 右键对象 | 菜单项数 | 菜单项列表 | 备注 |
|---------|---------|-----------|------|
| 状态池 type=8 (无选中) | **5** | 将当前银行加入到自选股, 查看\|删除发行走势, 所有股票加入到板块股, 显示行号 | 无属性/说明编辑（运行锁定） |
| 状态池 type=8 (有选中) | **3+** | 所有股票加入到板块股, 显示行号 (+ 可能被截断的更多项) | 运行时锁定大部分操作 |

> **关键差异总结**：
> - 运行时**移除**了"属性"、"修改说明文字和样式"、"停止运算"、"手工添加股票"、"清除状态池"等全部设计类入口
> - "将当前银行加入到自选股"和"查看/删除发行走势"在无选中状态下仍可调用（作用于整个状态池）
> - 有条件边和无条件边在运行时**无右键菜单**（或菜单被完全截断）

### 14.4 子对话框规范

**表 14-3：右键触发的子对话框 UI 规范**

| 对话框 | 标题 | UI 元素 | 触发来源 | 数据读写 |
|--------|------|---------|---------|---------|
| 线条宽度 | "线条宽度" | 输入域标签"线条宽度(1-16)"，数字输入框，确定/取消 | 边右键→线条宽度 | 读/写 `flow.size` |
| 说明文字 | "说明文字" | 输入域标签"请输入说明文字"，文本框(预填`cell.text`值)，颜色选择器("颜色")，确定/取消 | 节点右键→修改说明文字和样式 | 读/写 `cell.text` + `cell.clrtext` |
| 清除确认 | "提示" | 蓝色问号图标，文字"清除状态池中所有股票?"，是(Y)/否(N) | 状态池→清除全部 | — |
| 选择品种 | "选择品种" | 12个tab(分类/地区/行业/概念/风格/指数/组合/系统特定/自定义/扩展市场/扩展行情)，左侧树形品种列表，底部提示"按住Ctrl或Shift..."，全选/确定/取消 | 状态池→手工添加股票 | 写入 `cell.stks` |
| 请选择板块 | "请选择板块" | 板块列表(自选股/临时条件股/测试板块重命名/TEST/ALERT_TEST等)，右侧操作区(新建板块/删除板块/板块改名/清空品种)，"放在前面"复选框，确定/取消 | 状态池→所有股票加到板块股 | `psatt.bsavetoblock` + `blockfile` |

### 14.5 新发现字段映射

基于右键交互分析，在 Cell 通用属性表中补充以下字段：

| 字段名 | 类型 | 默认值 | 适用节点类型 | 说明 | XML来源 |
|--------|------|--------|------------|------|---------|
| `disabled` | bool | False | type=3 条件节点 | 停止运算标志。True 时条件节点不参与评估，画布显示灰度+斜线纹理 | cell属性或func子元素 |
| `show_row_num` | bool | False | type=8 状态池 | 显示行号标志。True 时状态池表格最左侧增加 "#" 行号列 | cell属性或psatt子元素 |

> **注意**：`flow.size` 字段已在 Flow 模型中定义（int, 默认值 1, 有效范围 1–16），此处仅确认其通过右键菜单"线条宽度"对话框进行 UI 暴露。

## 附录A：XML版本差异

6个TDX XML样本跨越了多年的版本演进，存在明显的格式差异：

### 版本一：老版（V6.x，黑马系列 + 大路终结）

- Pool 有 nextid 属性
- stk 有 9 个字段（无 maxrate/maxperiod/maxtime/maxprice/idaynum）
- psatt 有 12~13 个字段（老版含 nsyssound）
- func 有 8 个字段（无 accode/cfirst/csecond/nbeginday/nendday/bnost/bnotp/bnotq/nperiodnum）
- type=7 备选池通过 stk 子元素直接列出股票
- setcode 只有 0 和 1
- 中文 text 使用 GB2312 编码存储

### 版本二：新版（V7.x，盘后 + 看盘快照）

- Pool 无 nextid 属性
- stk 有 14 个字段（新增加 maxrate/maxperiod/maxtime/maxprice/idaynum）
- psatt 有 14 个字段（bsavehis 替代 nsyssound，新增 soundfile 独立管理）
- func 有 16 个字段（增加 accode/cfirst/csecond/nbeginday/nendday/bnost/bnotp/bnotq/nperiodnum）
- type=7 备选池使用 spinfo 子元素 + 动态加载（spinfo type=2/0/4）
- setcode 扩展到 2（北交所）
- bsavetoblock + blockfile + bclearblock 板块保存功能全面启用
- 中文 text 使用 GB2312 编码存储

### 版本二独有特性（V8交叉验证补充） [V8更新]
>
> #### 多输出自定义公式模式
>
> V7.x版本支持单个自定义公式输出多条指标线，每条线可在股票池中被独立引用和配置条件。
>
> | ntjindexno | accode | 输出线条数 | 输出线名称列表 |
> |------------|--------|-----------|--------------|
> | 425 | 强势启动 | 8条 | VAR5, VAR10, VAR20, VAR60, VAR5X, VAR10X, VAR20X, VAR60X |
> | 492 | 综合回撤 | 6条 | YHS, BHS, EHS, FYHS, FBHS, FEHS |
>
> #### attr=141 条件单元格变体
>
> 看盘快照.xml 中发现了 type="1" attr="141" 的cell节点。attr样式不仅适用于type=2容器，也可应用于type=1条件节点。

### 版本字段数量演变

| 元素 | 老版字段数 | 新版字段数 | 变化 |
|------|----------|----------|------|
| stk | 9 | 14 | +5 (maxrate/maxperiod/maxtime/maxprice/idaynum) |
| psatt | 12~13 | 14 | +soundfile 独立, +bsavehis, -nsyssound |
| func | 8 | 16 | +8 (accode/cfirst/csecond/nbeginday/nendday/bnost/bnotp/bnotq/nperiodnum) |
| Pool | 2 | 1 | -nextid |

---

## 附录B：Schemas.py 模型定义总表

以下为 `meta_core/schemas.py` 中所有 TDX 相关模型的定义参数总数：

| 模型 | 字段数 | 关键字段 |
|------|--------|---------|
| `TdxCellModel` | 8 | id, type, attr, pos_x, pos_y, width, height, clr, clrtext, solid, text |
| `TdxFlowModel` | 14 | startid, endid, clr, size, tran, emptyps, starttype, starttime, starttimetype, starttimehms, cxtype, cxtime, cxtimetype, jgtime |
| `TdxFuncModel` | 16 | nset, ntjindexno, accode, nperiod, nfirst, cfirst, noperate, nsecond, csecond, fsecond, nbeginday, nendday, bnost, bnotp, bnotq, nperiodnum |
| `TdxPsattModel` | 14 | bdel, ndelnum, ndeltype, baimpool, bsound, nsoundtype, soundfile, btip, bsavetoblock, blockfile, bclearblock, bsavehis |
| `TdxSpinfoModel` | 3 | type, customblockname, size |
| `TdxStkModel` | 14 | setcode, code, indate, intime, inprice, income, now, rise, volume, maxrate, maxperiod, maxtime, maxprice, idaynum |
| `TdxPoolMetaModel` | 2~3 | nextid(可选), backcolor(必填), cells, flows |

`SETCODE_MAP = {0: "SZ", 1: "SH", 2: "BJ"}`

---

## 版本更新记录

| 版本 | 日期 | 更新内容 |
|------|------|---------|
| **v8.0** | 2026-06-14 | **★ 完善spinfo.type 0-7全部枚举支持**<br/>• 扩展spinfo type从0-5（含2个预留值）到0-7完整8种类型<br/>• 新增type=1(沪深300+中证500)、type=5(板块指数)、type=6(ETF基金)、type=7(可转债)<br/>• 新增CandidatePoolResolver统一解析器架构说明<br/>• 新增备选池数据库存储方案（5张表ER关系）<br/>• 新增多数据源集成方案（TQ SDK/AKShare/同花顺/东方财富/申万）<br/>• 新增运行时刷新机制（type=3自选股30秒自动刷新）<br/>• 新增8个真实XML样本参考（type 0-7完整示例）<br/>• 添加完整股票池XML示例（多type组合） |
| v7.0 | 2026-06-09 | 基于42个XML实战样本 + bsavehis实时增量保存实现 |
| v6.0 | - | ndeltype时间单位核心修正、cfirst/csecond语义更新 |
| v5.0 | - | nset指标来源路由、noperate操作符完整枚举 |
| v4.0 | - | 16参数新版func格式、psatt扩展字段 |
| v3.0 | - | 初始版本，基础XML结构定义 |

---

> 文档版本：**v8.0** | 基于42个XML实战样本 + 50+个控制变量样本 + 7个形状图元控制变量样本 + 18个转移条件样本 + 10个状态池属性控制变量样本(含客户端截图100%验证) + bsavehis实时增量保存实现(引擎回调架构) + **spinfo.type 0-7完整枚举支持(8个真实XML样本验证)** | 通达信V7.x | 2026-06-14