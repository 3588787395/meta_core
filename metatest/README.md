# metatest v3 严格正反合量化测试套件

## 概述

metatest v3 是股票池平台的严格正反合量化测试套件，覆盖前端与后端所有模块。
评分完全由真实测试结果量化驱动，按 12 维加权评分，杜绝随意评分与硬编码信用分。
门槛：总分 ≥ 95 且 12 维均 ≥ 80 判定 PASS。

## 12 维评分规则与权重

权重总和 = 100%，总分 = Σ（维度得分 × 权重）。

| 序号 | 维度 | 权重 | 评分逻辑 |
|---|---|---|---|
| 1 | module_coverage | 10% | 覆盖模块数 / 17 × 100 |
| 2 | test_pass_rate | 18% | 通过测试数 / 总测试数 × 100（跳过计为失败） |
| 3 | assertion_density | 8% | 断言数 / (测试文件数 × 20) × 100 |
| 4 | event_chain_integrity | 10% | 出现事件类型数 / 10 × 100（链顺序错误扣 20%） |
| 5 | performance_benchmark | 8% | 1000 tick 耗时基准（≤10s 满分，线性衰减） |
| 6 | frontend_e2e_pass_rate | 10% | 前端 E2E 真实通过数 / 总数 × 100 |
| 7 | logic_coverage | 8% | 5 项底层逻辑验证通过数 / 5 × 100 |
| 8 | isomorphism_elimination | 12% | 15 项同构代码 Grep 检查，0 违规满分 |
| 9 | line_convergence | 8% | 核心模块总行数 ≤ 23000 满分，线性衰减 |
| 10 | rule_compliance | 4% | RULES 91-100 Grep 零违规 |
| 11 | negative_test_coverage | 2% | 4 类反测试覆盖率（每类 ≥ 8 用例） |
| 12 | synthesis_e2e | 2% | 合测试通过率 |

## 15 项同构检查清单

对应 15 组模式合并，每项 Grep 检查 0 违规满分：

1. nset 筛选函数合并（变更A）
2. ConfigStore 配置加载统一（变更G）
3. noperate mode 表驱动（变更H）
4. base_period 目标表驱动（变更I）
5. tradeattr BUY/SELL 表驱动（变更E）
6. 导入导出 converter 统一入口（变更C）
7. 公式 eval 核心合并（变更D）
8. build_spec 提取器统一（变更F）
9. 同步协程执行器统一（变更J）
10. 拓扑邻接构建统一（变更K）
11. 事件 handler 装饰器统一（变更N）
12. pnl 计算表驱动（变更B）
13. 排序键 lambda 表（变更L）
14. 后过滤统一包装（变更M）
15. 集合校验归一化（变更O）

## 正反合三层方法论

- **正测试**（`test_positive_*.py`）：验证功能正确性，覆盖 17 个后端模块 + 前端全部模块，含 15 组模式合并的回归断言。
- **反测试**（`test_negative_*.py`）：4 类异常（无效配置 / 运行时异常 / API 前端 / 底层逻辑违规），每类 ≥ 8 用例，含 15 组模式「同构复活」检测。
- **合测试**（`test_synthesis_*.py`）：端到端集成验证（仿真全流程 / 三模式 / 导入导出 / 热加载 / 元模式合并 / 前端 E2E / 水位线短路 / 编译-运行分离）。

## 运行方式与退出码

```bash
python -m metatest.runner
```

- 退出码 0 = 总分 ≥ 95 且 12 维均 ≥ 80（PASS）或无测试文件
- 退出码 1 = 总分 < 95 或有维度 < 80（FAIL）或有测试失败

## v3 严格规则

- 跳过测试计为失败（不在 passed 分子）
- 前端 E2E 环境缺失计 frontend_e2e_passed=0
- 12 维分数均需 ≥ 80 才达标
- 总分 ≥ 95 且 12 维均 ≥ 80 判定 PASS
- 所有评分由真实测试结果计算，无硬编码信用分

## 目录结构

```
metatest/
├── conftest.py                 # 共享 pytest 夹具与 REPORT_STATE 单例
├── scoring.py                  # 12 维量化评分引擎
├── runner.py                   # 测试运行器与数据采集
├── test_positive_*.py          # 正测试（功能正确性 + 模式合并回归）
├── test_negative_*.py          # 反测试（4 类异常 + 同构复活检测）
├── test_synthesis_*.py         # 合测试（端到端集成）
├── fixtures/                   # 测试夹具
└── report.json                 # 运行后生成的结构化报告
```

## 报告输出

- 控制台：12 维明细 + 总分 + 扣分项 + 重做清单
- `metatest/report.json`：结构化报告（含 12 维分数、权重、总分、PASS/FAIL、测试统计、redo_list）
