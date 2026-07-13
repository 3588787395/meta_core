# Checklist — 全面系统测试集创建

## 测试条目文档

- [ ] 测试条目文档覆盖 12 大类 163 项测试
- [ ] 每项测试有明确的"正-反-合"策略
- [ ] 每项测试有明确的正确/错误边界，禁止模棱两可

## 一、XML 解析与导出

- [ ] DZH XML 解析：Pool 根属性、Cell 通用属性、6 种节点类型、stk/ana/tradeattr 子元素、Flow 属性、换行符/编码
- [ ] DZH XML 导出：条件节点/状态池/源节点字段导出、_orig_text/换行符、ana 子元素
- [ ] TDX XML 解析：Pool 根属性、Cell 通用属性、3 种核心节点、spinfo/stk/psatt/func
- [ ] TDX XML 导出：clr/clrtext 保留、flow.clr 保留、psatt/func None 处理、nextid 处理
- [ ] 283 个 XML 全量往返验证 0 差异

## 二、JSON 交叉格式

- [ ] DZH XML ↔ JSON ↔ DZH XML 逐字段一致
- [ ] TDX XML ↔ JSON ↔ TDX XML 逐字段一致
- [ ] DZH XML ↔ JSON ↔ TDX XML 交叉转换正确
- [ ] JSON schema 验证（必填字段、版本号、空内容）
- [ ] pool_meta extra 字段保留

## 三、节点类型系统

- [ ] DZH 7 种节点类型属性完整性
- [ ] TDX 6 种节点类型属性完整性
- [ ] attr 位标志解码正确
- [ ] clrtext/solid 原始值保留
- [ ] TDX↔DZH 类型映射双向正确

## 四、边类型系统

- [ ] starttype 0~7 全覆盖
- [ ] cxtype 0~2 全覆盖
- [ ] starttype × cxtype 24 种组合全覆盖
- [ ] 6 种流转模式（copy/move/overwrite/force_move/pass_through/output_components）
- [ ] TDX tran/emptyps 映射正确
- [ ] DZH attr 位标志映射正确
- [ ] 条件边/无条件边语义正确
- [ ] 变更检测（节点股票变化、行情数据变化、首次执行）

## 五、核心执行流

- [ ] gate→filter→propagate→callback→ttl 完整链路
- [ ] 串行/扇出/扇入/循环拓扑
- [ ] 空池/单节点池不崩溃

## 六、条件评估

- [ ] nset 0~5 六种评估器
- [ ] noperate 0~9 十种操作
- [ ] 条件分发路由（dispatch.json 位掩码）
- [ ] AND/OR 匹配模式

## 七、TTL 淘汰

- [ ] bdel 开关
- [ ] ndeltype 0~3 四种时间单位
- [ ] DZH hold+deltype+delstocktype+endtime 完整体系
- [ ] TTL 与 propagate/回放交互

## 八、事件与信号

- [ ] ENTER/EXIT/TIMEOUT/RANK_CHANGED 事件
- [ ] BUY/SELL 信号
- [ ] 事件/信号队列异步推送
- [ ] 持仓跟踪（StockTracker）
- [ ] 高亮事件

## 九、运行模式

- [ ] live/replay/simulation 三种模式
- [ ] run_loop 暂停/恢复/停止
- [ ] 时间源切换
- [ ] 交易接口

## 十、数据完整性

- [ ] 行情注入不覆盖 indate/intime/_tracker
- [ ] 降级链
- [ ] 数据缓存 TTL
- [ ] K 线合成
- [ ] 异步并发获取

## 十一、API 端点

- [ ] 池 CRUD + 运行/停止
- [ ] XML/JSON 导入导出
- [ ] 回放/仿真 API
- [ ] 配置表 CRUD + 热加载

## 十二、端到端真实 TQ 数据

- [ ] 简单串行池（全A股→MA金叉→目标池）
- [ ] 扇出池
- [ ] TTL 真实时间淘汰
- [ ] 回放历史数据
- [ ] 仿真模式
- [ ] 持仓跟踪真实盈亏
- [ ] 事件流真实推送
- [ ] 大规模股票池（5000+只）

## 边测试边完善

- [ ] 每个测试类别运行后收集所有失败项
- [ ] 修复发现的问题后再进入下一个类别
- [ ] 所有断言有明确的正确/错误边界
