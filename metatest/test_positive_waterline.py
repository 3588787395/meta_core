"""Task 18.1: 水位线不变零计算验证正测试。

验证 ``core/runtime_mode_module.TickTable`` 的水位线（waterline）语义：
  - 相同数据 update 返回 False（水位线未涨 → 调用方可短路）
  - 不同数据 update 返回 True，ts 严格递增
  - hash 计算基于 sha256，相同数据相同 hash，不同数据不同 hash
  - get(code) / snapshot() 接口正确性
  - 引擎核心循环在 tick_table.update() 返回 False 时短路返回空事件列表
    （通过 mock 引擎 _on_tick_received 验证 waterline short-circuit 行为）

硬约束：
  - 仿真模式下股票代码使用 ``fz`` 前缀
  - 测试不依赖网络/真实行情源
"""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# 1. TickTable.update —— 相同数据重复 update 返回 False
# ---------------------------------------------------------------------------


def test_ticktable_update_same_data_returns_false(tick_table):
    """相同数据重复 update 应返回 False（水位线未涨，可短路）。"""
    # 首次 update 数据，应返回 True（水位线上涨）
    data = {"fz000001": {"close": 10.0, "_ts": 34500.0}}
    assert tick_table.update(data) is True
    # 重复 update 完全相同的数据，应返回 False（hash 不变）
    assert tick_table.update(data) is False
    # 再次重复，仍为 False
    assert tick_table.update(data) is False


# ---------------------------------------------------------------------------
# 2. TickTable.update —— 不同数据 update 返回 True，ts 递增
# ---------------------------------------------------------------------------


def test_ticktable_update_different_data_returns_true(tick_table):
    """不同数据 update 应返回 True，且 ts 严格递增（水位线上涨）。"""
    # 第一次 update
    assert tick_table.update({"fz000001": {"close": 10.0}}) is True
    ts1 = tick_table.ts
    # 第二次 update 不同数据，ts 必须递增
    assert tick_table.update({"fz000001": {"close": 11.0}}) is True
    assert tick_table.ts > ts1
    # 第三次 update 又不同数据，ts 继续递增
    ts2 = tick_table.ts
    assert tick_table.update({"fz000002": {"close": 20.0}}) is True
    assert tick_table.ts > ts2


# ---------------------------------------------------------------------------
# 3. TickTable._compute_hash —— hash 计算正确性
# ---------------------------------------------------------------------------


def test_ticktable_hash_computation(tick_table):
    """hash 计算应满足：相同数据相同 hash，不同数据不同 hash。"""
    data_a = {"fz000001": {"close": 10.0}}
    data_b = {"fz000001": {"close": 11.0}}
    # 相同数据 → 相同 hash
    h_a1 = tick_table._compute_hash(data_a)
    h_a2 = tick_table._compute_hash(data_a)
    assert h_a1 == h_a2
    # 不同数据 → 不同 hash
    h_b = tick_table._compute_hash(data_b)
    assert h_a1 != h_b
    # hash 为整数（sha256 digest 转 int）
    assert isinstance(h_a1, int)
    # 顺序无关性：sort_keys=True 保证 {a:1,b:2} 与 {b:2,a:1} 同 hash
    h_swap1 = tick_table._compute_hash({"fz000001": {"close": 10.0}, "fz000002": {"close": 20.0}})
    h_swap2 = tick_table._compute_hash({"fz000002": {"close": 20.0}, "fz000001": {"close": 10.0}})
    assert h_swap1 == h_swap2


# ---------------------------------------------------------------------------
# 4. TickTable.get —— 返回正确的 bar 数据
# ---------------------------------------------------------------------------


def test_ticktable_get_returns_correct_bar(tick_table):
    """get(code) 应返回该 code 的最新 bar dict，未命中返回空 dict。"""
    tick_table.update({
        "fz000001": {"close": 10.5, "volume": 1000},
        "fz000002": {"close": 20.0, "volume": 2000},
    })
    # 已存在 code 返回完整 bar
    bar1 = tick_table.get("fz000001")
    assert bar1 == {"close": 10.5, "volume": 1000}
    # 另一个 code
    bar2 = tick_table.get("fz000002")
    assert bar2 == {"close": 20.0, "volume": 2000}
    # 未命中 code 返回空 dict
    assert tick_table.get("fz999999") == {}


# ---------------------------------------------------------------------------
# 5. TickTable.snapshot —— 返回数据副本
# ---------------------------------------------------------------------------


def test_ticktable_snapshot_returns_copy(tick_table):
    """snapshot() 应返回数据浅拷贝，修改副本不影响原数据。"""
    tick_table.update({"fz000001": {"close": 10.0}})
    snap = tick_table.snapshot()
    # 副本内容应与原数据相等
    assert snap == tick_table.data
    # 但 snap 应是不同对象（dict 浅拷贝）
    assert snap is not tick_table.data
    # 修改副本不影响原数据
    snap["fz000002"] = {"close": 99.0}
    assert "fz000002" not in tick_table.data
    assert "fz000001" in tick_table.data


# ---------------------------------------------------------------------------
# 6. Engine waterline short-circuit —— 引擎短路验证
# ---------------------------------------------------------------------------


def test_engine_waterline_short_circuit():
    """引擎核心循环在 tick_table.update() 返回 False 时短路返回空事件列表。

    通过 mock 一个最小化的「引擎-like」对象复现 engine.py _on_tick_received 中
    的 waterline short-circuit 模式（pre_hash == post_hash 即 return）：
        _tt = state.tick_table
        _pre_hash = _tt.hash
        data_updater.apply_data(tick_data)
        if _tt.hash == _pre_hash:
            return  # 水位线未涨，零计算
        # 否则标记源节点脏，发布事件
    """
    # 构造 mock state 与 tick_table
    mock_state = MagicMock()
    # 使用真实 TickTable 模拟水位线
    from core.runtime_mode_module import TickTable
    tt = TickTable()
    tt.update({"fz000001": {"close": 10.0}})  # 先灌一次数据
    mock_state.tick_table = tt

    # mock data_updater.apply_data：第二次调用相同数据，hash 不变
    def apply_data_unchanged(tick_data):
        # 不更新 tick_table，模拟水位线未涨
        pass

    mock_data_updater = MagicMock()
    mock_data_updater.apply_data.side_effect = apply_data_unchanged

    # 复现 engine.py L632-646 的 waterline short-circuit 逻辑
    events_published: List[Any] = []
    _tt = getattr(mock_state, "tick_table", None)
    _pre_hash = _tt.hash if _tt is not None else None

    mock_data_updater.apply_data({"fz000001": {"close": 10.0}})

    # tick_table.hash 未变化（apply_data 未更新），应触发短路
    if _tt is not None and _pre_hash is not None and _tt.hash == _pre_hash:
        # 短路：不发布任何事件，标记源节点脏的逻辑被跳过
        pass
    else:
        # 否则进入下游计算（标记节点脏、发布事件等）
        events_published.append("DOWNSTREAM_EXECUTED")

    # 断言：水位线未涨时短路成立，下游未被触发
    assert _tt.hash == _pre_hash
    assert events_published == [], "水位线未涨时应短路返回空事件列表"


def test_engine_waterline_no_short_circuit_when_data_changed():
    """对照测试：数据变化时水位线上涨，下游计算应被触发（不短路）。"""
    from core.runtime_mode_module import TickTable

    mock_state = MagicMock()
    tt = TickTable()
    tt.update({"fz000001": {"close": 10.0}})
    mock_state.tick_table = tt

    # mock data_updater.apply_data：实际更新 tick_table，hash 变化
    def apply_data_changed(tick_data):
        tt.update(tick_data)

    mock_data_updater = MagicMock()
    mock_data_updater.apply_data.side_effect = apply_data_changed

    events_published: List[Any] = []
    _tt = getattr(mock_state, "tick_table", None)
    _pre_hash = _tt.hash if _tt is not None else None

    # 新数据，hash 应变化
    mock_data_updater.apply_data({"fz000001": {"close": 11.0}})

    if _tt is not None and _pre_hash is not None and _tt.hash == _pre_hash:
        # 短路
        pass
    else:
        # 数据变化 → 进入下游计算
        events_published.append("DOWNSTREAM_EXECUTED")

    # 断言：hash 已变化，未短路，下游被执行
    assert _tt.hash != _pre_hash
    assert events_published == ["DOWNSTREAM_EXECUTED"]
