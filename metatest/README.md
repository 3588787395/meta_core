# metatest v2 严格正反合测试

## 概述

metatest v2 是「元模式深层收敛与严格正反合测试 v2」spec 的量化测试运行器。
按 8 维加权评分，门槛：总分 ≥ 95 且 8 维均 ≥ 80 判定 PASS。

## v2 严格评分规则

- **跳过测试计为失败**（不再给予信用分）
- **前端 E2E 环境缺失计为失败**（不再给信用分）
- **8 维分数均需 ≥ 80** 才达标
- **总分 ≥ 95 且 8 维均 ≥ 80** 判定 PASS

## 8 维评分（权重总和 = 100%）

| 维度 | 权重 | 评分逻辑 |
|---|---|---|
| module_coverage | 15% | 覆盖模块数 / 17 * 100 |
| test_pass_rate | 20% | 通过数 / 总数 * 100（跳过计为失败） |
| assertion_density | 10% | 断言数 / (文件数 * 目标密度) * 100 |
| event_chain_integrity | 15% | 出现事件类型数 / 10 * 100（链顺序错误扣 20%） |
| performance_benchmark | 10% | 1000 tick 耗时 ≤10s 满分，线性衰减 |
| frontend_e2e_pass_rate | 10% | 前端 E2E 真实通过数 / 总数 * 100 |
| logic_coverage | 10% | 5 项底层逻辑验证通过数 / 5 * 100 |
| isomorphism_elimination | 10% | 6 项 Grep 检查，0 违规满分 |

## 运行方式

```bash
python -m metatest.runner
```

## 退出码

- 0 = 总分 ≥ 95 且 8 维均 ≥ 80（PASS）或无测试文件
- 1 = 总分 < 95 或有维度 < 80（FAIL）

## 正反合三层方法论

- **正测试**（`test_positive_*.py`）：验证功能正确性，覆盖 17 个关键功能点 + 5 项底层逻辑
- **反测试**（`test_negative_*.py`）：验证异常与边界处理（异常配置/运行时异常/API前端/底层逻辑）
- **合测试**（`test_synthesis_*.py`）：端到端集成验证（仿真全流程/三模式/导入导出/热加载/元模式合并/前端 E2E）

## 5 项底层逻辑验证

1. 水位线（waterline）— TickTable 类
2. 编译-运行分离 — compile 函数 / CompiledPool 类
3. 三要素 — trigger_check / filter_eval / propagate_apply
4. 角色表 — node_roles.json + _ROLE_ACTIONS
5. 正交化 — StockChanged / Signal / SignalDeriver / ActionDispatcher

## 6 项同构代码消除度检查

1. `state.latest_tick[` = 0（除 TickTable 内部）
2. 运行时 `json.loads` / `_parse_edge` / `_build_adjacency` = 0
3. `_phase_dispatch` / `_phase_nset_filter` / `_dispatch_filter` / `_eval_primitive` = 0
4. `if node.type ==` = 0
5. `transfer_module` 中 `sound.play` / `popup.show` = 0
6. 死表引用 = 0

## 前端 E2E 测试

使用 Playwright 驱动浏览器，验证前端界面：
- 主页加载（`#topbar` 可见）
- 顶部工具栏含模式切换按钮（`#btnDesign`/`#btnRun`/`#btnReplay`/`#btnSimulation`）
- 模式切换到仿真（按钮 `active` 状态切换）
- 事件面板可见性（仿真模式下 `.visible` 类）
- 股票池设计器（canvas 元素存在）

环境缺失时通过 `pytest.importorskip("playwright")` 自动 skip。

## 报告输出

- 控制台：8 维明细 + 总分 + 扣分项 + 重做清单
- `metatest/report.json`：结构化报告（含 8 维分数、总分、PASS/FAIL、测试统计）
