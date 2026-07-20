# 事件面板增强与Tick代码修复 - Verification Checklist

## 前端事件面板
- [ ] 事件面板HTML中添加了过滤按钮组（9个分类按钮）
- [ ] 事件面板HTML中添加了"暂停滚动"按钮
- [ ] 每个事件类型前显示对应的emoji图标
  - Tick/DataChanged: 📊
  - Bar/BarComposed: 📈
  - Formula/FormulaEvaluated/StockFiltered: 🧮
  - Edge/EdgeFired: ⚡
  - Transfer/TransferExecuted: 🔄
  - Signal/BUY/SELL: 💰
  - Order/OrderPlaced/OrderFilled/PositionUpdated: 📋
  - TTL/TTLExpired/Timeout: ⏰
  - ModeChanged/TimeAdvanced/PoolLoaded: 🔧
- [ ] 事件边框颜色严格符合规范
  - Tick/DataChanged: 灰色 #9e9e9e
  - Bar/BarComposed: 蓝色 #2196f3
  - Formula/FormulaEvaluated/StockFiltered: 绿色 #4caf50
  - Edge/EdgeFired: 橙色 #ff9800
  - Transfer/TransferExecuted: 紫色 #9c27b0
  - Signal/BUY/SELL: 红色 #f44336
  - Order/OrderPlaced/OrderFilled/PositionUpdated: 黄色 #ffc107
  - TTL/TTLExpired/Timeout: 深红色 #b71c1c
  - ModeChanged/TimeAdvanced: 青色 #00bcd4
- [ ] 点击分类过滤按钮可隐藏/显示对应类别的事件
- [ ] "全选"按钮可一次性显示所有事件
- [ ] 事件显示格式为 [HH:MM:SS] [emoji] [EventType] [code] [details]
- [ ] 点击"暂停滚动"后新事件到达不自动滚动
- [ ] "暂停滚动"按钮切换为"继续滚动"，点击恢复自动滚动
- [ ] 事件浮窗默认显示（非hidden状态）
- [ ] 事件浮窗位于页面右下角/右侧，z-index正确不被遮挡

## Tick代码修复
- [ ] 仿真模式步进时日志中不再出现"diag _on_tick_received skip: code empty"警告
- [ ] TickReceived事件始终携带非空code字段
- [ ] tick_data字典中始终包含code字段
- [ ] _on_tick_received能成功处理tick并发布DataChanged事件
- [ ] SimTickSource初始codes为空时能正确通过PoolLoaded事件重建

## 事件流验证
- [ ] TickReceived事件正确发布（ts, code, tick_data非空）
- [ ] DataChanged事件正确发布（ts, codes列表, source="tick"）
- [ ] BarComposed事件正确发布（ts, code, period, bar）
- [ ] FormulaEvaluated事件正确发布（formula_ref, result, code）
- [ ] EdgeFired事件正确发布（eid, ts, changed_codes列表）
- [ ] TransferExecuted事件正确发布（src, tgt, codes列表）
- [ ] 没有code为空或codes为空列表的无效事件被发布
- [ ] 每个事件都有ts时间戳字段
- [ ] 事件面板中能看到完整事件链从Tick到Order
