# 全面系统测试条目文档

> 覆盖股票池系统所有功能模块，每项采用"正-反-合"策略，禁止模棱两可通过

---

## 一、XML 解析与导出（DZH / TDX）

### 1.1 DZH XML 解析

| # | 测试项 | 正向 | 反向 | 综合 |
|---|--------|------|------|------|
| 1 | Pool 根属性解析 | type="ss-pool", ver="1.0", mode="1", nextid, backcolor, warning, ency, system 全部正确读取 | 缺失可选属性(warning/ency/system)不报错 | 206 个 XML 全部解析无异常 |
| 2 | Cell 通用属性 | id/type/attr/pos/clr/text 全部正确 | id 非数字、pos 格式错误、type 未知值 | 特殊 id 格式("115"/"cell_3") |
| 3 | type=202 备选池 | attrtext/reload/lastload 正确读取 | attrtext 为空、含 Tab/含中文市场名 | attrtext 含 8 种市场代码 |
| 4 | type=200 状态池 | hold/col/width/histana/wizd/deltype/delstocktype/endtime/enter/exit/stocknum/tmpl/sorttype 全部正确 | hold=0、col 为空、deltype 超范围 | hold_sec→hold 转换、col_list→col 转换 |
| 5 | type=201 转移条件 | inditype/crc/indi/sorttype/indiparam 正确读取 | indi 为空 Base64、crc=0 | indi 双重编码场景 |
| 6 | type=1/2/3/4/5/6 辅助节点 | 各类型属性正确读取 | type=203 未知类型不崩溃 | attr 位标志解码正确 |
| 7 | stk 子元素 | label/t/p/tid 全部读取 | stk 无 label、p 为非数字 | 大量 stk（2000+只） |
| 8 | ana 子元素 | label/t/p 全部读取 | ana 为空列表 | histana 与 ana 数量一致性 |
| 9 | tradeattr 子元素 | 19 字段全部读取 | 部分字段缺失 | enter/exit 动作编码与 tradeattr 一致性 |
| 10 | Flow 属性 | from/to/attr/clr/count/begin/begint/end/endt/interval_sec 全部正确 | from/to 不存在的节点 | attr 位标志解码 |
| 11 | 换行符和特殊字符 | text 含 \n 正确保留 | text 含 \t、含 &#10; | _orig_text 空字符串 vs None |
| 12 | 编码处理 | GB2312/UTF-8 编码自动检测 | BOM 头、乱码字节 | 中文文件名 XML |

### 1.2 DZH XML 导出

| # | 测试项 | 正向 | 反向 | 综合 |
|---|--------|------|------|------|
| 13 | 条件节点字段导出 | inditype/crc/indi/sorttype/indiparam 写为 cell 属性 | 字段为 None 时不输出 | 导出后重新解析结果一致 |
| 14 | 状态池字段导出 | hold/col/width/histana 等 15 个字段正确输出 | hold_sec→hold 转换、col_list→col 逗号分隔 | 导出后重新解析结果一致 |
| 15 | 源节点字段导出 | attrtext/reload/lastload 正确输出 | attrtext 含 \n 编码为 &#10; | 导出后重新解析结果一致 |
| 16 | _orig_text 导出 | 空字符串保留 | None 不输出 | \n 换行符编码 |
| 17 | ana 子元素导出 | label/t/p 全部输出 | 空列表不输出 ana | histana 与 ana 一致性 |
| 18 | 213 个 DZH XML 往返 | 全部字段级一致 | — | 0 差异 |

### 1.3 TDX XML 解析

| # | 测试项 | 正向 | 反向 | 综合 |
|---|--------|------|------|------|
| 19 | Pool 根属性 | nextid/backcolor 正确读取 | nextid 缺失、backcolor 缺失 | 新版 XML 无 nextid |
| 20 | Cell 通用属性 | id/type/attr/pos/clr/clrtext/solid/text 全部正确 | clr=-1、clrtext=0、solid=0 | clr=-1 保留原值 |
| 21 | type=7 备选池 | stk 列表 + spinfo 子元素 | spinfo 缺失、stk 为空 | spinfo.market 保留原始值不被推断覆盖 |
| 22 | type=8 状态池 | psatt 14 字段 + stk 列表 | psatt 缺失、bdel=0+ndelnum=0 | bsavehis=1 与历史数据目录 |
| 23 | type=3 转移条件 | func 16 字段全部读取 | func 缺失、nset 超范围 | nset=0~5 各有正确 evaluator |
| 24 | spinfo type 枚举 | type=0/2/3/4 正确解析 | type=1/5 预留值 | customblockname 与 type 对应关系 |
| 25 | stk setcode 映射 | setcode=0→SZ, 1→SH, 2→BJ | setcode 未知值 | 大量 stk（2000+只） |
| 26 | Flow 属性 | startid/endid/clr/size/tran/emptyps/starttype/starttime 等 12 字段 | clr=-1 保留原值 | tran=0→copy, tran=1→move |

### 1.4 TDX XML 导出

| # | 测试项 | 正向 | 反向 | 综合 |
|---|--------|------|------|------|
| 27 | cell.clr 保留 | clr=-1 输出为 -1 | clr=None 使用默认值 | 条件节点 clr=16711680 vs -1 |
| 28 | flow.clr 保留 | clr=-1 输出为 -1 | — | 3 个文件 91 处差异已修复 |
| 29 | psatt/func None 处理 | None 时不输出子元素 | — | 导出后重新解析一致 |
| 30 | nextid 处理 | recalc_nextid=True 自动重算 | recalc_nextid=False 保留原值 | 两种模式均正确 |
| 31 | 70 个 TDX XML 往返 | 全部字段级一致 | — | 0 差异 |

---

## 二、JSON 交叉格式

| # | 测试项 | 正向 | 反向 | 综合 |
|---|--------|------|------|------|
| 32 | DZH XML → JSON → DZH XML | 逐字段精确一致 | — | 3 个测试文件 |
| 33 | TDX XML → JSON → TDX XML | 逐字段精确一致 | — | 3 个测试文件 |
| 34 | JSON → DZH XML → JSON | 往返一致 | — | — |
| 35 | JSON → TDX XML → JSON | 往返一致 | — | — |
| 36 | DZH XML → JSON → TDX XML | 交叉格式转换正确 | — | 类型映射正确 |
| 37 | TDX XML → JSON → DZH XML | 交叉格式转换正确 | — | 类型映射正确 |
| 38 | JSON schema 验证 | version/pool_meta/nodes/edges 必填 | 缺 version、缺 nodes、空内容 | version 不支持 |
| 39 | pool_meta extra 字段 | nextid/backcolor/ver/mode/ency/warning/system 保留 | — | — |
| 40 | edge from/to 规范化 | "from"/"to" → "source"/"target" | — | DZH edge 格式 |
| 41 | 283 个 XML 全量交叉验证 | DZH 213 + TDX 70 全部通过 | — | 0 差异 |

---

## 三、节点类型系统

### 3.1 DZH 节点类型

| # | 测试项 | 正向 | 反向 | 综合 |
|---|--------|------|------|------|
| 42 | type=202 备选池属性完整性 | attrtext/reload/lastload/markets/sectors | attrtext 为空 | 市场解析→股票列表 |
| 43 | type=200 状态池属性完整性 | hold/col/width/histana/wizd 等 15 字段 | hold=0 默认值 432000 | hold_sec→hold 转换 |
| 44 | type=201 转移条件属性完整性 | inditype/crc/indi/sorttype/indiparam | indi 空 Base64 | indi 双重编码 |
| 45 | type=4 丢弃池 | 正确创建和接收股票 | — | move 模式下股票进入丢弃池 |
| 46 | type=1/2/3/5/6 辅助节点 | 属性正确 | — | 不参与执行 |
| 47 | type=203 未知类型 | 不崩溃 | — | 与 type=200 共享 handler |
| 48 | attr 位标志解码 | type=200 的 8 个位标志正确 | attr=0 全部 False | 位标志影响执行行为 |
| 49 | 节点 ID 类型 | DZH id 为字符串 | id 冲突 | "115"/"cell_3" 格式 |

### 3.2 TDX 节点类型

| # | 测试项 | 正向 | 反向 | 综合 |
|---|--------|------|------|------|
| 50 | type=7 备选池 | spinfo+stk 完整 | spinfo 缺失时从 stk 推断 | market 不被推断覆盖 |
| 51 | type=8 状态池 | psatt 14 字段+stk | psatt 缺失时使用默认值 | bsavehis=1 历史记录 |
| 52 | type=3 转移条件 | func 16 字段 | func 缺失 | nset=0~5 各 evaluator |
| 53 | type=0/1/2 辅助节点 | 属性正确 | — | 不参与执行 |
| 54 | clrtext/solid 保留 | 原始值不被默认值覆盖 | clrtext=0 保留 | None 时才用默认值 |
| 55 | TDX→DZH 类型映射 | 7→202, 8→200, 3→201 | — | 反向映射也正确 |

---

## 四、边类型系统

### 4.1 时序参数（starttype × cxtype = 24 种）

| # | 测试项 | 正向 | 反向 | 综合 |
|---|--------|------|------|------|
| 56 | starttype=0 立即 | 始终执行 | — | 与所有 cxtype 组合 |
| 57 | starttype=1 延迟N秒 | 延迟后执行 | offset=0 立即执行 | 与 cxtype=1 持续窗口 |
| 58 | starttype=2 开市前 | 开市前时间窗口内执行 | 非交易时间 | 与 cxtype=2 只一次 |
| 59 | starttype=3 开市后 | 开市后执行 | 开市前不执行 | — |
| 60 | starttype=4 收市前 | 收市前时间窗口内执行 | 非交易时间 | — |
| 61 | starttype=5 收市后 | 收市后执行 | 交易时间不执行 | — |
| 62 | starttype=6 指定交易时间 | HHMMSS 时间到达后执行 | 时间未到不执行 | — |
| 63 | starttype=7 指定时间 | HHMMSS 时间到达后执行 | 时间未到不执行 | — |
| 64 | cxtype=0 一直执行 | 每次 tick 都执行 | — | 与所有 starttype 组合 |
| 65 | cxtype=1 持续窗口 | 首次触发后 N 秒内继续执行 | 窗口到期后停止 | _flow_duration_starts 记录 |
| 66 | cxtype=2 只一次 | 只执行一次 | 第二次不执行 | _flow_exec_counts 记录 |
| 67 | 24 种组合全覆盖 | timing.json 驱动全部正确 | — | 边界值：offset=0, cxtime=0 |

### 4.2 流转模式

| # | 测试项 | 正向 | 反向 | 综合 |
|---|--------|------|------|------|
| 68 | copy (keep_source) | 源保留，目标追加 | — | 源股票列表不变 |
| 69 | move (delete_source) | 源清空，目标=源 | — | 源股票列表变空 |
| 70 | overwrite (delete_source+clear_dest_first) | 源清空，目标=源（先清目标） | — | 目标原有股票被清除 |
| 71 | force_move | 强制覆盖 | — | — |
| 72 | pass_through | 全部通过 | — | 无条件转移 |
| 73 | TDX tran/emptyps 映射 | tran=0→copy, tran=1→move, emptyps=1→overwrite | — | — |
| 74 | DZH attr 位标志映射 | bit12=不删源, bit13=先清目标, bit19=输出成分股 | — | — |

### 4.3 边语义与变更检测

| # | 测试项 | 正向 | 反向 | 综合 |
|---|--------|------|------|------|
| 75 | 条件边语义 | gate→变更检测→filter→propagate | 源无变化时跳过 filter | 缓存 _filter_cache |
| 76 | 无条件边语义 | 变更检测→propagate（跳过 gate 和 filter） | 源无变化时跳过 | — |
| 77 | 首次执行 | _first_run=True 时无条件执行 | — | — |
| 78 | 行情数据变更检测 | _last_bar_hash ≠ _current_bar_hash 时触发 | hash 相同时跳过 | — |
| 79 | 节点股票变更检测 | _last_snapshot ≠ 当前 node_stocks 时触发 | 快照相同时跳过 | — |

---

## 五、核心执行流（gate→filter→propagate→callback→ttl）

| # | 测试项 | 正向 | 反向 | 综合 |
|---|--------|------|------|------|
| 80 | run_pool 完整链路 | 备选池→条件→状态池 全链路执行 | — | 输出结果与预期一致 |
| 81 | gate 门控 | starttype=0 立即放行 | starttype=1 延迟未到不放行 | 延迟到达后放行 |
| 82 | filter 筛选 | 公式通过的股票进入目标 | 公式不通过的股票被拒绝 | 部分通过部分拒绝 |
| 83 | propagate 传播 | copy/move/overwrite 三种模式正确 | — | 源和目标股票列表正确 |
| 84 | callback 回调 | psatt 副作用触发（声音/弹窗/保存板块） | bdel=0 时不触发 TTL | baimpool=1 标记目标池 |
| 85 | ttl 淘汰 | bdel=1+ndelnum=3+ndeltype=0 → 3天后删除 | bdel=0 不删除 | 持仓时间计算正确 |
| 86 | 串行池 | 7→3→8→3→8 串联执行 | — | 每级筛选结果正确 |
| 87 | 扇出池 | 7→3→8(多个) 并行分支 | — | 各分支独立执行 |
| 88 | 扇入池 | 8(多个)→8 汇合 | — | 股票合并正确 |
| 89 | 循环池 | 8→3→8 回环 | — | 不死循环 |
| 90 | 空池执行 | nodes=[], edges=[] | — | 不崩溃 |
| 91 | 单节点池 | 只有备选池无边 | — | 初始化正确 |

---

## 六、条件评估（nset 0~5）

| # | 测试项 | 正向 | 反向 | 综合 |
|---|--------|------|------|------|
| 92 | nset=0 技术指标 | formula_process_mul_zb → noperate 比较 | 指标计算失败时拒绝 | noperate 0~9 全覆盖 |
| 93 | nset=1 条件选股 | formula_process_mul_xg → 信号判断 | 公式语法错误 | — |
| 94 | nset=2 专家系统 | formula_exp → 买入/卖出信号 | 无信号时拒绝 | 任意/买入/卖出三种模式 |
| 95 | nset=3 财务标量 | get_financial_data → 标量比较 | 财务数据缺失 | PE/PB/ROE 阈值 |
| 96 | nset=4 行情标量 | get_market_snapshot → 标量比较 | 行情数据缺失 | 涨跌幅/振幅衍生指标 |
| 97 | nset=5 集合运算 | 并集/差集/交集 | 空集运算 | 多条件组合 |
| 98 | noperate=0 等于 | 值==阈值 | — | — |
| 99 | noperate=1 大于 | 值>阈值 | — | — |
| 100 | noperate=2 小于 | 值<阈值 | — | — |
| 101 | noperate=3 上穿 | 短期上穿长期 | 未上穿 | — |
| 102 | noperate=4 下穿 | 短期下穿长期 | 未下穿 | — |
| 103 | noperate=5 持股N周期 | 持仓满足N周期 | 持仓不足 | — |
| 104 | noperate=6 排名前N | 截面排名前N名 | 排名超出 | — |
| 105 | noperate=7 排名后N | 截面排名后N名 | 排名超出 | — |
| 106 | noperate=8 上拐 | 指标上拐点 | 非拐点 | — |
| 107 | noperate=9 下拐 | 指标下拐点 | 非拐点 | — |
| 108 | 条件分发路由 | dispatch.json 位掩码路由正确 | 未知 nset | nset_dispatch 子表 |
| 109 | AND/OR 匹配模式 | AND 全部满足、OR 任一满足 | 空条件列表 | 多条件组合 |

---

## 七、TTL 淘汰

| # | 测试项 | 正向 | 反向 | 综合 |
|---|--------|------|------|------|
| 110 | bdel=0 不删除 | 股票永不过期 | — | — |
| 111 | bdel=1 启用删除 | 超时股票被移除 | — | — |
| 112 | ndeltype=0 天 | ndelnum=3 → 3天后删除 | ndelnum=0 不删除 | — |
| 113 | ndeltype=1 小时 | ndelnum=2 → 2小时后删除 | — | — |
| 114 | ndeltype=2 分钟 | ndelnum=30 → 30分钟后删除 | — | — |
| 115 | ndeltype=3 秒 | ndelnum=60 → 60秒后删除 | — | — |
| 116 | DZH hold+deltype | hold=432000, deltype=0 → 5天后删除 | hold=0 不删除 | deltype=0~4 全覆盖 |
| 117 | DZH delstocktype=0 | 相对时间模式（入池后N单位时间） | — | — |
| 118 | DZH delstocktype=1 | 指定交易时间删除 | endtime 编码正确 | — |
| 119 | TTL 与 propagate 交互 | 入池时间记录正确 | — | 多次入池不重置计时 |
| 120 | TTL 与回放交互 | 回放时间轴正确驱动 TTL | — | — |

---

## 八、事件与信号

| # | 测试项 | 正向 | 反向 | 综合 |
|---|--------|------|------|------|
| 121 | ENTER 事件 | 股票入池时生成 | 重复入池不重复生成 | 事件字段完整 |
| 122 | EXIT 事件 | 股票出池时生成 | 未入池不出池事件 | move 模式触发 EXIT |
| 123 | TIMEOUT 事件 | TTL 过期时生成 | bdel=0 不生成 | — |
| 124 | RANK_CHANGED 事件 | 排名变化时生成 | 排名不变不生成 | — |
| 125 | BUY 信号 | 目标池入池时生成 | 非目标池不生成 | baimpool=1 标记 |
| 126 | SELL 信号 | 目标池出池时生成 | 非目标池不生成 | — |
| 127 | 事件队列 | _event_queue 异步推送 | — | 消费者正确读取 |
| 128 | 信号队列 | _signal_queue 异步推送 | — | 消费者正确读取 |
| 129 | 持仓跟踪 | StockTracker 入场价/当前价/盈亏 | — | 最大盈利/最大回撤 |
| 130 | 高亮事件 | highlight_start/stop 正确触发 | — | — |

---

## 九、运行模式

| # | 测试项 | 正向 | 反向 | 综合 |
|---|--------|------|------|------|
| 131 | live 实盘模式 | wall_clock 时间源 + TQ SDK 数据 | — | 全副作用 |
| 132 | replay 回放模式 | sequence 时间源 + 历史K线 | K 线用完停止 | 只读副作用 |
| 133 | simulation 仿真模式 | virtual 时间源 + Mock 数据 | — | 可选副作用 |
| 134 | run_loop 持续循环 | 暂停/恢复/停止 | — | tick 间隔正确 |
| 135 | run_mode 入口 | 查 runtime_modes.json 初始化 | 未知 mode_id | — |
| 136 | 时间源切换 | wall_clock/sequence/virtual | — | _now() 返回正确 |
| 137 | 交易接口 | live_order/noop/paper_trade | — | 信号处理正确 |

---

## 十、数据完整性

| # | 测试项 | 正向 | 反向 | 综合 |
|---|--------|------|------|------|
| 138 | 行情注入不覆盖 indate | _inject_bar_data 保留原始 indate | — | existing_map 合并 |
| 139 | 行情注入不覆盖 intime | _inject_bar_data 保留原始 intime | — | — |
| 140 | 行情注入不覆盖 _tracker | _inject_bar_data 保留持仓跟踪数据 | — | — |
| 141 | 降级链 | TQ 不可用→fallback_chain.json 降级 | 降级链断裂 | pass_through 降级 |
| 142 | 数据缓存 | _data_cache TTL 过期机制 | 缓存未过期命中 | — |
| 143 | K 线合成 | 1min→5min/15min/30min/60min/day | — | OHLCV 正确 |
| 144 | 异步并发获取 | _inject_bar_data_async 多时间框架 | — | 缓存命中 |

---

## 十一、API 端点

| # | 测试项 | 正向 | 反向 | 综合 |
|---|--------|------|------|------|
| 145 | 创建池 | POST /api/pool | 缺必填字段 | 返回 pool_id |
| 146 | 运行池 | POST /api/pool/{id}/run | pool_id 不存在 | — |
| 147 | 停止池 | POST /api/pool/{id}/stop | 未运行的池 | — |
| 148 | 获取池状态 | GET /api/pool/{id}/status | pool_id 不存在 | — |
| 149 | TDX XML 导入 | POST /api/dzh/import | 非 XML 文件 | — |
| 150 | DZH XML 导入 | POST /api/dzh/import | 非 XML 文件 | — |
| 151 | JSON 导入导出 | POST/GET /api/pool/json | 格式错误 JSON | — |
| 152 | 回放 API | 创建会话/步进/暂停/恢复 | — | — |
| 153 | 仿真 API | 创建会话/步进/暂停/恢复 | — | — |
| 154 | 配置表 CRUD | GET/PUT /api/table/{name} | 表名不存在 | — |
| 155 | 热加载 | POST /api/config/reload | — | 运行中配置变更 |

---

## 十二、端到端真实 TQ 数据测试

| # | 测试项 | 正向 | 反向 | 综合 |
|---|--------|------|------|------|
| 156 | 简单串行池（全A股→MA金叉→目标池） | TQ 数据获取成功，筛选结果正确 | TQ 断连降级 | 持仓跟踪正确 |
| 157 | 扇出池（全A股→条件1→池A, 条件2→池B） | 两个分支独立执行 | — | 结果不互相干扰 |
| 158 | TTL 真实时间淘汰 | 入池后等待 TTL 过期 | — | 过期股票被移除 |
| 159 | 回放历史数据 | 指定日期范围回放 | 日期超出范围 | 快照正确 |
| 160 | 仿真模式 | 虚拟时钟步进 | — | 信号生成正确 |
| 161 | 持仓跟踪真实盈亏 | 入场价/当前价/盈亏计算 | — | 最大盈利/最大回撤 |
| 162 | 事件流真实推送 | ENTER/EXIT/BUY/SELL 事件 | — | 异步消费者读取 |
| 163 | 大规模股票池（5000+只） | 执行时间合理 | — | 内存不溢出 |

---

## 统计

| 类别 | 测试项数 |
|------|---------|
| XML 解析与导出 | 31 |
| JSON 交叉格式 | 10 |
| 节点类型系统 | 14 |
| 边类型系统 | 24 |
| 核心执行流 | 12 |
| 条件评估 | 18 |
| TTL 淘汰 | 11 |
| 事件与信号 | 10 |
| 运行模式 | 7 |
| 数据完整性 | 7 |
| API 端点 | 11 |
| 端到端真实数据 | 8 |
| **总计** | **163** |
