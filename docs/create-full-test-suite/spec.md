# 全面系统测试集创建 Spec

## Why

现有测试虽多但验证深度不足——283 个 XML 往返验证全部通过，却仍存在 flow.clr=-1 被替换为 127、spinfo.market 被推断覆盖等字段级 BUG。测试用例缺少"正-反-合"策略，缺少边界值和异常路径覆盖，缺少基于真实 TQ 量化数据的端到端验证。需要创建一套能**真正发现问题**的测试集。

## What Changes

- 创建测试条目文档，列出所有分类测试表格，详尽无漏
- 创建基于真实 TQ 量化数据的测试集
- 对每项功能采用"正-反-合"测试策略
- 逐步创建简单完整股票池，边测试边完善
- 修复测试中发现的所有问题

## Impact

- Affected code: `engine.py`, `converters/*`, `native/builtins*.py`, `tdx_evaluators.py`, `schemas.py`, `services/tq_adapter.py`
- Affected tests: `tests/test_*.py` 全部测试文件

## ADDED Requirements

### Requirement: 测试条目文档

系统 SHALL 提供完整的测试条目分类文档，覆盖以下 12 大类：

1. **XML 解析与导出** — DZH/TDX XML 的解析正确性、导出正确性、往返一致性
2. **JSON 交叉格式** — DZH XML ↔ JSON ↔ TDX XML 逐字段精确一致
3. **节点类型系统** — 所有节点类型的属性、默认值、边界值
4. **边类型系统** — starttype×cxtype 24 种组合、流转模式、变更检测
5. **核心执行流** — gate→filter→propagate→callback→ttl 完整链路
6. **条件评估** — nset0~5 六种评估器、noperate 0~9 十种操作
7. **TTL 淘汰** — ndeltype 0~3 四种时间单位、bdel 开关、hold/deltype/delstocktype
8. **事件与信号** — ENTER/EXIT/TIMEOUT/BUY/SELL 生成规则
9. **运行模式** — live/replay/simulation 三种模式
10. **数据完整性** — 行情注入不覆盖元数据、降级链、缓存策略
11. **API 端点** — HTTP 层面的集成测试
12. **端到端真实数据** — 基于 TQ 量化数据的完整池运行验证

#### Scenario: 正向测试

- **WHEN** 输入合法配置和正常数据
- **THEN** 输出与设计预期完全一致（逐字段精确比较）

#### Scenario: 反向测试

- **WHEN** 输入异常值、边界值、缺失字段
- **THEN** 系统正确处理（拒绝/降级/保留原值），不静默替换或丢失

#### Scenario: 综合测试

- **WHEN** 多模块协作执行复杂业务场景
- **THEN** 各环节数据处理准确，模块间接口一致

### Requirement: 真实 TQ 数据测试

系统 SHALL 提供基于真实 TQ 量化数据的端到端测试：

- 使用 TQ SDK 获取真实 K 线和行情数据
- 构建简单完整股票池（备选池→条件→状态池），验证全链路
- 验证筛选结果与 TDX 客户端一致
- 验证持仓跟踪、事件生成、信号触发的正确性

### Requirement: 边测试边完善

- 每创建一个测试类别，立即运行并收集所有失败项
- 修复发现的问题后再进入下一个类别
- 禁止模棱两可通过——每个断言必须有明确的正确/错误边界
