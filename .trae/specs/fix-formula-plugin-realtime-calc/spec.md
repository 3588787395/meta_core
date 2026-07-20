# 公式插件化与实时计算架构修正 - Product Requirement Document

## Overview
- **Summary**: 修正股票池核心计算架构：①CROSS作为公式语言内置算子由PythonFormulaEngine通过表驱动实现（已正确实现，非硬编码）；②公式引擎**必须**使用包含未闭合K线的完整数据（闭合历史+当前未闭合K线实时价），Tick到达立即以最新价参与计算，不等待K线闭合；③股票池节点完全不感知K线合成，公式引擎自行从PoolState获取K线数据并计算；④状态池作为Tick表的过滤视图存在，不维护独立股票列表副本，仅维护入池追踪器（entry_ts/entry_price）用于TTL和交易；⑤EdgeFired事件携带changed_codes批量触发增量筛选；⑥仿真模式与实盘模式共享除TickSource外的全部代码路径。
- **Purpose**: 解决当前架构中"公式计算只用已闭合K线（违反实时计算要求）、股票池与K线耦合、状态池维护独立脏标记、增量筛选缓存粒度错误"等核心问题，实现真正的实时事件驱动、公式插件化、视图化架构。
- **Target Users**: 股票池系统开发者、量化交易用户。

## Goals
- CROSS/MA/EMA/MACD/KDJ等所有指标计算通过公式引擎插件（PythonFormulaEngine表驱动算子）执行，公式中写`CROSS(K,D)`即调用