# 元模式统一重构 Spec

## Why

当前代码库中存在 3 组高度同构但未合并的底层运行逻辑：

1. **DataChanged 发布重复**：`_publish_tick_changed` 与 `_publish_bar_changed` 构造的 `DataChanged` 事件仅 `source`/`codes`/`payload`/`period` 字段不同，骨架完全相同，违反 RULES.md 第 16 条「所有分派用 dict 表驱动」。
2. **EdgeExecutor.run 过程式编排**：`_gate`/`_filter`/`_propagate` 三步共享「读 spec → 读 state → 查表分派 → 写 state」的完全相同骨架，但 `run` 方法仍以 9 步过程式 if/调用序列展开，未将"步骤序列"本身表驱动化，违反 RULES.md 第 2 条「新增功能=加JSON条目，零行引擎改动」。
3. **MonitoringModule 30+ `_on_` 方法同构**：30+ 个事件订阅方法方法体都是 `self._add_to_event_list({ts, event_type, code, details})`，仅 details 字段提取不同，违反 RULES.md 第 69 条「所有模块必须完整自包含」。

本次重构将这 3 组同构代码合并为元模式统一接口，使程序底层运行逻辑彻底符合 RULES.md 的表驱动 + 事件驱动架构合同。

## What Changes

### 变更 1：统一 DataChanged 发布器
- 将 `_publish_tick_changed`（tick_bar_module.py:181）与 `_publish_bar_changed`（tick_bar_module.py:358）合并为单一 `publish_data_changed(bus, state, source, codes, ts, data=None, period=None, bar_hash="")` 模块级函数
- **BREAKING**：`_publish_tick_changed` 和 `_publish_bar_changed` 内联调用点全部改为调用 `publish_data_changed`

### 变更 2：EdgeExecutor 步骤表驱动化
- 将 `_gate`/`_filter`/`_propagate` 三步抽象为统一的 `EdgeStep` 协议（Protocol），每步实现 `run(spec, ctx) -> StepResult` 接口
- 将 `run` 方法的 9 步过程式编排改为 `STEPS` 表驱动循环：`for step in STEPS: step.run(spec, ctx)`
- **BREAKING**：`run` 方法不再内联 `_callback`/`_ttl`，统一提升为 `CallbackStep`/`TTLStep`
- **BREAKING**：`CompiledSchedule` 新增 `steps: List[StepSpec]` 字段，编译期产出步骤序列

### 变更 3：MonitoringModule 事件记录统一化
- 将 30+ 个 `_on_xxx` 方法替换为 `subscribe_any` + 通用 `event_to_record(event) -> dict` 转换函数
- `event_to_record` 通过 `EVENT_RECORD_ADAPTERS` 配置表驱动，每种事件类型一个 adapter 函数提取 details
- **BREAKING**：删除 30+ 个独立 `_on_xxx` 方法，`MonitoringModule.subscribe` 改为 `subscribe_any(event_to_record_handler)`

## Impact

- Affected specs: RULES.md 第 2/16/24/69 条
- Affected code:
  - `core/tick_bar_module.py` — 合并两个 `_publish_*_changed` 为统一函数
  - `core/execution_module.py` — EdgeExecutor 步骤表驱动化，CompiledSchedule 新增 steps 字段
  - `core/monitoring_module.py` — 30+ `_on_` 方法替换为统一 `event_to_record` + `subscribe_any`
  - `core/event_bus.py` — 无变更（`subscribe_any` 已存在）
  - `app.py` — MonitoringModule 初始化方式调整
  - `core/schemas.py` — 新增 `StepSpec`/`EdgeStep` Protocol 定义

## ADDED Requirements

### Requirement: 统一 DataChanged 发布器

系统 SHALL 提供单一 `publish_data_changed` 函数替代 `_publish_tick_changed` 和 `_publish_bar_changed`。

#### Scenario: tick 数据变更发布
- **WHEN** DataUpdater.apply_data 写入 latest_tick 后调用 `publish_data_changed(bus, state, source="tick", codes=updated_codes, ts=event_ts, data=tick_payload)`
- **THEN** 发布 `DataChanged(source="tick", codes=updated_codes, ts=event_ts, data=tick_payload)` 事件

#### Scenario: bar 数据变更发布
- **WHEN** BarComposer.on_tick 合成 K 线后调用 `publish_data_changed(bus, state, source="bar", codes=bar_codes, ts=event_ts, period=period, bar_hash=period_hash)`
- **THEN** 发布 `DataChanged(source="bar", codes=bar_codes, ts=event_ts, period=period)` 事件
- **AND** 对每个 code 发布 `BarComposed(bar=bar, period=period, code=code, ts=event_ts)` 事件

#### Scenario: 空 codes 跳过
- **WHEN** codes 为空列表
- **THEN** 不发布任何事件，直接返回

#### Scenario: bus 无效跳过
- **WHEN** bus 为 None 或非 EventBus 实例
- **THEN** 不发布任何事件，直接返回

### Requirement: EdgeExecutor 步骤表驱动化

系统 SHALL 将 EdgeExecutor.run 的 9 步过程式编排改为 `STEPS` 表驱动循环。

#### Scenario: 正常边执行
- **WHEN** EdgeExecutor.run(eid, changed_codes) 被调用
- **THEN** 按 `CompiledSchedule.steps` 中的步骤序列依次执行：GateStep → FilterStep → PropagateStep → TTLStep → CallbackStep
- **AND** 每步通过 `EdgeStep.run(spec, ctx) -> StepResult` 统一接口调用
- **AND** GateStep 返回 `StepResult(should_continue=False)` 时中断后续步骤

#### Scenario: 新增执行步骤
- **WHEN** 需要新增一个执行步骤（如风控检查）
- **THEN** 仅需在 `config/architecture/edge_strategies.json` 的 `steps` 数组中添加 `StepSpec` 条目
- **AND** 实现 `EdgeStep` Protocol 的 `run` 方法
- **AND** 零行 `EdgeExecutor.run` 改动

#### Scenario: 步骤顺序配置化
- **WHEN** 编译期 `Compiler.compile` 产出 `CompiledSchedule`
- **THEN** `steps` 字段包含从 `edge_strategies.json:steps` 读取的步骤序列
- **AND** 运行期按此序列循环执行

### Requirement: MonitoringModule 事件记录统一化

系统 SHALL 将 30+ 个独立 `_on_xxx` 方法替换为 `subscribe_any` + `event_to_record` 转换函数。

#### Scenario: 任意事件到达
- **WHEN** EventBus 发布任意事件
- **THEN** `MonitoringModule` 通过 `subscribe_any` 接收事件
- **AND** 调用 `event_to_record(event)` 将事件转换为记录 dict
- **AND** 调用 `self._add_to_event_list(record)` 加入事件列表

#### Scenario: 新增事件类型适配
- **WHEN** 需要为新事件类型添加监控记录
- **THEN** 仅需在 `EVENT_RECORD_ADAPTERS` dict 中添加 `{event_type_name: adapter_func}` 条目
- **AND** 零行 `MonitoringModule` 代码改动

#### Scenario: 未注册事件类型
- **WHEN** 收到未在 `EVENT_RECORD_ADAPTERS` 中注册的事件类型
- **THEN** 使用默认 adapter 提取 `{ts, event_type, code, details=event.details}` 加入事件列表

## MODIFIED Requirements

### Requirement: CompiledSchedule 编译产物

`CompiledSchedule` 在原有 6 个 spec dict + `nodes` + `edge_index` 基础上，新增 `steps: List[StepSpec]` 字段，由 `Compiler.compile` 从 `edge_strategies.json:steps` 读取并填充。运行期 `EdgeExecutor.run` 按 `steps` 序列循环执行。

### Requirement: EdgeExecutor.run 方法

`EdgeExecutor.run` 从 9 步过程式展开改为 `for step in schedule.steps: step.run(spec, ctx)` 表驱动循环。`_gate`/`_filter`/`_propagate` 方法保留为 `EdgeStep` 实现的内部委托，不删除（保持已有表驱动分派不变）。

### Requirement: MonitoringModule.subscribe 方法

`MonitoringModule.subscribe` 从逐个 `bus.subscribe(EventType, self._on_xxx)` 改为 `bus.subscribe_any(self._on_any_event)`。`_on_any_event` 调用 `event_to_record(event)` 查 `EVENT_RECORD_ADAPTERS` 表获取 adapter，转换后加入事件列表。

## REMOVED Requirements

### Requirement: `_publish_tick_changed` 方法
**Reason**: 与 `_publish_bar_changed` 同构，已合并为 `publish_data_changed`
**Migration**: 所有 `self._publish_tick_changed(codes, data)` 调用改为 `publish_data_changed(self.bus, self.state, "tick", codes, ts, data=data)`

### Requirement: `_publish_bar_changed` 函数
**Reason**: 与 `_publish_tick_changed` 同构，已合并为 `publish_data_changed`
**Migration**: 所有 `_publish_bar_changed(composer, period, codes, ts)` 调用改为 `publish_data_changed(composer.bus, composer.state, "bar", codes, ts, period=period, bar_hash=composer._bar_hashes.get(period, ""))`，BarComposed 事件在 `publish_data_changed` 内部对每个 code 发布

### Requirement: MonitoringModule 30+ 个 `_on_xxx` 方法
**Reason**: 方法体完全同构（`self._add_to_event_list({ts, event_type, code, details})`），仅 details 提取不同
**Migration**: 替换为 `EVENT_RECORD_ADAPTERS` dict + `event_to_record(event)` 函数 + `subscribe_any` 单一订阅
