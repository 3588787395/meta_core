"""metatest：全面正反合测试量化评审框架。

基于「正反合」三层方法论对 meta_core 项目进行量化测试评审：
  - 正测试：验证功能正确性（happy path）
  - 反测试：验证边界与异常处理（sad path）
  - 合测试：端到端事件链完整性集成测试

6 维加权评分（门槛 95）：
  - module_coverage    25% 模块覆盖率
  - pass_rate          25% 测试通过率
  - assertion_density 15% 断言密度
  - event_chain        15% 事件链完整性
  - performance        10% 性能基准
  - frontend_e2e       10% 前端 E2E 通过率

运行方式：
    python -m metatest.runner
"""
