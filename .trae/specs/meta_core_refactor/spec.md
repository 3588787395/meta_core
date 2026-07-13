# meta_core 面向对象事件驱动重构 — 产品需求文档

## Overview
- **Summary**: 将 meta_core 股票池引擎从过程式表驱动重构为面向对象事件驱动架构。以节点和边为一等对象，事件驱动状态流转，数据驱动增量计算，保持现有功能等价，显著提升代码清晰度、可维护性和运行时性能。
- **Purpose**: 解决当前引擎代码又臭又长（engine.py 3500+行）、数据更新/K线合成/公式计算/界面刷新各搞一套、轮询式全量计算性能浪费、拓扑与执行顺序混淆等问题。
- **Target Users**: 股票池系统开发者和维护者。

## Goals
- 面向对象：节点/边/事件为一等对象，职责清晰，继承关系合理
- 事件驱动：数据变更/时间到达/状态变更均为事件，事件驱动状态流转
- 数据驱动：数据不变则不计算，增量计算，零空转开销
- 表驱动：逻辑隐含于表结构，差异显示于表内容，新增配置不改代码
- 代码精简：engine.py 从 3500+ 行降至 1000 行以内
- 功能等价：与现有引擎行为完全一致，新旧对拍通过率 100%

## Non-Goals (Out of Scope)
- 不修改前端界面（editor.js 等）
- 不新增选股策略或指标
- 不修改公式引擎内核（FormulaRouter 等）
- 不修改数据适配器（TQ/akshare 等）
- 不支持新的股票池文件格式
- 不做性能极致优化（架构清晰优先）

## Background & Context
当前 meta_core 引擎（engine.py）是过程式表驱动架构，3500+ 行代码揉合了时机判断、过滤执行、状态流转、事件生成、TTL 淘汰等所有逻辑。虽有 50+ 张配置表驱动，但核心循环仍是每 tick 全量遍历所有边，数据不变时也有大量空转。

存在的核心问题：
1. 拓扑与执行顺序混淆——拓扑是连接关系，执行顺序可用户调整
2. 轮询式全量计算——数据不变时也在跑，浪费性能
3. 过程式大函数——gate/filter/propagate/ttl 全揉在 _execute_flowsCore 里
4. 三套 filter 各搞一套——unconditional/conditional/formula_eval 重复逻辑多
5. 事件生成滞后——tick 末统一生成，不是实时触发

## Functional Requirements
- **FR-1**: 面向对象的节点类体系——每种节点类型一个类，统一基类，各自实现自己的行为
- **FR-2**: 面向对象的边类体系——条件边/无条件边/公式边等，各自实现触发和执行逻辑
- **FR-3**: 事件系统——数据更新事件、时间到达事件、状态变更事件，事件驱动流转
- **FR-4**: 数据驱动增量计算——数据时间戳不变则跳过计算，仅计算变更部分
- **FR-5**: 执行顺序表驱动——按用户指定顺序执行，与拓扑分离
- **FR-6**: 表驱动配置——所有策略差异在配置表中，引擎只做查表执行
- **FR-7**: 功能等价性——与旧引擎输出完全一致，新旧对拍零差异

## Non-Functional Requirements
- **NFR-1**: 空转性能提升 10 倍以上（数据不变时几乎零开销）
- **NFR-2**: engine.py 代码行数 ≤ 1000 行（不含子类实现）
- **NFR-3**: 全量计算性能不低于旧引擎
- **NFR-4**: 与现有 API 完全兼容，调用方零修改
- **NFR-5**: 可测试性——每个类可独立单元测试

## Constraints
- **Technical**: Python 3.11+，仅修改 meta_core 目录，不动其他目录
- **Business**: 向后兼容，所有现有股票池文件必须正常运行
- **Dependencies**: 依赖现有 FormulaRouter、ConfigStore、数据适配器等

## Assumptions
- 旧引擎行为正确，作为对拍基准
- 现有配置表结构合理，可复用
- 用户可接受渐进式重构（先核心后外围）

## Acceptance Criteria

### AC-1: 面向对象类体系
- **Given**: 重构后的引擎代码
- **When**: 审查类结构
- **Then**: 存在 Node 基类及各类型子类、Edge 基类及各类型子类、Event 基类及各类型子类；每个类职责单一，继承关系清晰
- **Verification**: `human-judgment`

### AC-2: 事件驱动流转
- **Given**: 运行中的股票池
- **When**: 数据更新或时间到达
- **Then**: 通过事件触发计算和流转，不是轮询遍历
- **Verification**: `human-judgment`

### AC-3: 数据驱动增量计算
- **Given**: 数据未变化的连续 tick
- **When**: 执行引擎
- **Then**: 不执行任何公式计算，空转开销可忽略
- **Verification**: `programmatic`

### AC-4: 执行顺序与拓扑分离
- **Given**: 一个股票池配置
- **When**: 调整执行顺序（与拓扑不同）
- **Then**: 引擎按指定顺序执行，结果正确
- **Verification**: `programmatic`

### AC-5: 功能等价性
- **Given**: 任意股票池配置和输入数据
- **When**: 新旧引擎分别执行
- **Then**: 输出完全一致（节点股票列表、事件、信号等）
- **Verification**: `programmatic`

### AC-6: 代码精简
- **Given**: 重构后的代码
- **When**: 统计 engine.py 行数
- **Then**: engine.py ≤ 1000 行
- **Verification**: `programmatic`

### AC-7: API 兼容性
- **Given**: 现有调用方代码
- **When**: 切换到新引擎
- **Then**: 调用方无需修改，正常运行
- **Verification**: `programmatic`

## Open Questions
- [ ] 重构是单一大版本还是渐进式替换？
- [ ] 旧引擎保留多久用于对拍？
- [ ] 事件队列用 asyncio.Queue 还是自实现？
