"""正测试：StatePoolView 视图对象（G4）

验证：
- get_pool 返回 StatePoolView 实例
- get_dirty_codes() = state.changed_codes ∩ 本池股票
- add_stocks() 入池并标脏
- remove_stocks() 出池并标脏
- 不存在 get_node_stocks/set_node_stocks/add_node_stocks/remove_node_stocks 扁平接口

基于 spec.md "StatePoolView 视图脏股票（G4）" Scenario。
复用 ``core/`` 现有 ``StatePoolView`` / ``PoolState`` / ``PoolStateMixin``，
禁止兼容已删除旧接口。
"""
from __future__ import annotations

import inspect

from core.runtime_mode_module import PoolState, PoolStateMixin, StatePoolView


# ────────────────────────────────────────────────────────────────
# 结构：get_pool 返回 StatePoolView 实例
# ────────────────────────────────────────────────────────────────


def test_get_pool_returns_statepoolview(pool_engine):
    """``state.get_pool(nid)`` 返回 ``StatePoolView`` 实例（G4 视图对象）。"""
    engine = pool_engine()
    state = engine.state
    # 对配置中每个节点取池视图，均应返回 StatePoolView 实例
    for nid in engine.nodes:
        view = state.get_pool(nid)
        assert isinstance(view, StatePoolView), (
            f"get_pool({nid!r}) 未返回 StatePoolView 实例"
        )
        # 视图内部持有正确的 nid 引用
        assert view._nid == nid


# ────────────────────────────────────────────────────────────────
# 语义：get_dirty_codes = state.changed_codes ∩ 本池股票
# ────────────────────────────────────────────────────────────────


def test_get_dirty_codes_is_intersection(pool_engine, fz_stocks):
    """``get_dirty_codes()`` = ``state.get_changed_codes() ∩ 本池股票``（交集语义）。"""
    engine = pool_engine()
    state = engine.state
    # 从配置动态取若干 fz 股票代码（不硬编码）
    codes = fz_stocks(5)
    source_pool = state.get_pool("source")
    pool_a = state.get_pool("pool_A")

    # 前 3 只入 source 池，全部 5 只标脏
    source_pool.add_stocks([{"code": c} for c in codes[:3]])
    state.add_changed_codes(set(codes))

    # source 池脏股票 = changed_codes ∩ source 池股票 = 前 3 只
    assert source_pool.get_dirty_codes() == set(codes[:3])

    # pool_A 为空，脏股票 = changed_codes ∩ ∅ = ∅
    assert pool_a.get_dirty_codes() == set()

    # 将第 4 只放入 pool_A，pool_A 脏股票 = changed_codes ∩ {code4} = {code4}
    pool_a.add_stocks([{"code": codes[3]}])
    assert pool_a.get_dirty_codes() == {codes[3]}

    # source 池脏股票不受 pool_A 操作影响（交集语义天然隔离）
    assert source_pool.get_dirty_codes() == set(codes[:3])


# ────────────────────────────────────────────────────────────────
# 行为：add_stocks 入池并标脏
# ────────────────────────────────────────────────────────────────


def test_add_stocks_enters_and_marks_dirty(pool_engine, fz_stocks):
    """``add_stocks()`` 入池并标脏（``state.changed_codes`` 含入池代码）。"""
    engine = pool_engine()
    state = engine.state
    code = fz_stocks(1)[0]
    pool_a = state.get_pool("pool_A")

    # 入池前 pool_A 为空，code 未标脏
    assert pool_a.get_stock_codes() == set()
    assert code not in state.get_changed_codes()

    # 入池
    pool_a.add_stocks([{"code": code}])

    # 入池后 pool_A 含该代码
    assert code in pool_a.get_stock_codes()
    # state.changed_codes 含该代码（标脏）
    assert code in state.get_changed_codes()
    # get_dirty_codes 含该代码（交集：changed_codes ∩ pool_A 股票）
    assert code in pool_a.get_dirty_codes()


# ────────────────────────────────────────────────────────────────
# 行为：remove_stocks 出池并标脏
# ────────────────────────────────────────────────────────────────


def test_remove_stocks_exits_and_marks_dirty(pool_engine, fz_stocks):
    """``remove_stocks()`` 出池并标脏。

    出池后 ``state.changed_codes`` 仍含该代码（出池也标脏），
    但 ``get_dirty_codes()`` 不再返回它（已不在池中，交集为空）。
    """
    engine = pool_engine()
    state = engine.state
    code = fz_stocks(1)[0]
    pool_a = state.get_pool("pool_A")

    # 先入池
    pool_a.add_stocks([{"code": code}])
    assert code in pool_a.get_stock_codes()

    # 清脏以隔离 remove 的标脏行为
    state.clear_dirty()
    assert code not in state.get_changed_codes()

    # 出池（传 code 字符串，_extract_code 自动提取）
    pool_a.remove_stocks([code])

    # 出池后 pool_A 不含该代码
    assert code not in pool_a.get_stock_codes()
    # state.changed_codes 仍含该代码（出池也标脏）
    assert code in state.get_changed_codes()
    # get_dirty_codes 不含该代码（已不在池中，交集为空）
    assert code not in pool_a.get_dirty_codes()


# ────────────────────────────────────────────────────────────────
# 跨池隔离：source 池标脏不影响 pool_A 的 get_dirty_codes
# ────────────────────────────────────────────────────────────────


def test_dirty_codes_cross_pool_isolation(pool_engine, fz_stocks):
    """source 池标脏不影响 pool_A 的 ``get_dirty_codes``（交集语义天然隔离）。"""
    engine = pool_engine()
    state = engine.state
    codes = fz_stocks(4)
    source_pool = state.get_pool("source")
    pool_a = state.get_pool("pool_A")

    # source 池放 codes[:2]，pool_A 放 codes[2:4]
    source_pool.add_stocks([{"code": c} for c in codes[:2]])
    pool_a.add_stocks([{"code": c} for c in codes[2:4]])

    # 全部标脏
    state.add_changed_codes(set(codes))

    # source 脏股票 = codes[:2]，pool_A 脏股票 = codes[2:4]，互不干扰
    assert source_pool.get_dirty_codes() == set(codes[:2])
    assert pool_a.get_dirty_codes() == set(codes[2:4])

    # 从 source 移除 codes[0]，pool_A 脏股票不受影响
    source_pool.remove_stocks([codes[0]])
    # remove 也标脏，codes[0] 仍在 changed_codes，但已不在 source 池
    assert codes[0] not in source_pool.get_dirty_codes()
    # pool_A 脏股票不受 source 操作影响
    assert pool_a.get_dirty_codes() == set(codes[2:4])


# ────────────────────────────────────────────────────────────────
# 源码层面：不存在扁平接口
# ────────────────────────────────────────────────────────────────


def test_no_forbidden_flat_interfaces_in_source():
    """源码层面检查 ``PoolState`` / ``PoolStateMixin`` 不存在
    ``get_node_stocks`` / ``set_node_stocks`` / ``add_node_stocks`` /
    ``remove_node_stocks`` 扁平接口定义（G4 已删除）。
    """
    forbidden = [
        "get_node_stocks",
        "set_node_stocks",
        "add_node_stocks",
        "remove_node_stocks",
    ]
    # 运行时检查：PoolState 类不存在这些属性
    for name in forbidden:
        assert not hasattr(PoolState, name), (
            f"PoolState 仍存在禁止扁平接口 {name!r}"
        )
    # 源码检查：PoolState 与 PoolStateMixin 源码中不存在 def 定义
    combined_src = inspect.getsource(PoolState) + "\n" + inspect.getsource(PoolStateMixin)
    for name in forbidden:
        assert f"def {name}" not in combined_src, (
            f"禁止扁平接口 def {name} 出现在 PoolState/PoolStateMixin 源码"
        )
