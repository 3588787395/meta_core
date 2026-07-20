# 事件监控与浮窗显示修正 - Verification Checklist

## 代码检查
- [ ] core/monitoring_module.py的_EVENT_HANDLERS包含所有11类关键事件
- [ ] 每个事件类型都有对应的处理方法
- [ ] SSE端点/api/events/stream正确序列化事件
- [ ] SSE事件JSON包含event_type、time、code、node_id、edge_id、details、timestamp字段
- [ ] web/css/styles.css中#eventPanel默认display为flex（非none）
- [ ] web/js/ui.js中getEventColor颜色映射符合要求
- [ ] CSS中所有事件类型样式类完整且颜色正确
- [ ] addEvent函数在列表底部追加新事件
- [ ] 添加事件后自动滚动到底部
- [ ] SSE连接有错误处理和自动重连逻辑

## 功能验证
- [ ] 服务器启动无错误
- [ ] SSE端点能正常连接
- [ ] SSE能推送心跳包
- [ ] 浏览器打开页面后事件浮窗默认显示在右下角
- [ ] TickReceived事件显示为灰色
- [ ] DataChanged/BarComposed事件显示为蓝色
- [ ] FormulaEvaluated事件显示为绿色
- [ ] EdgeFired事件显示为橙色
- [ ] TransferExecuted事件显示为紫色
- [ ] Signal(BUY/SELL)事件显示为红色
- [ ] OrderPlaced/OrderFilled/PositionUpdated事件显示为黄色
- [ ] TTLExpired事件显示为深红色
- [ ] 新事件到达时列表自动滚动到底部
- [ ] 事件显示时间、类型、代码、详情信息
- [ ] 运行模拟时能看到完整的事件流
