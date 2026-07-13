"""Pipeline handlers: refresh handlers.

I12 大瘦身：gate/injector/pre_tick 9 个死 handler 已删（零 Python 调用方）。
仅保留 Refresh 三 handler（engine._refresh_bar_data 经 getattr(_pipeline, handler_name) 消费）。

I21：tq_snapshot_refresh / mock_advance_refresh 共享的预检 + tq.get_snapshot + 异常吞咽
提取为 ``_apply_tq_snapshot`` 高阶函数；语义差异（替换 vs 合并）收敛到 ``merge`` 参数。
noop_refresh 因无预检不参与提取，保持单行 return。
"""

import logging

logger = logging.getLogger(__name__)


# === Refresh ===

def _apply_tq_snapshot(ctx, current_bar_data, merge: bool, fail_msg: str):
    """共享预检 + tq.get_snapshot 调用 + 异常吞咽。

    Args:
        ctx: refresh 上下文，需含 engine 引用
        current_bar_data: 刷新前的 bar 数据
        merge: True 时合并到 current_bar_data（mock_advance 语义），
               False 时整表替换（tq_snapshot 语义）
        fail_msg: 异常日志模板

    Returns:
        dict: 刷新后的 bar_data（刷新失败或预检不过则返回原 current_bar_data）
    """
    engine = ctx.get('engine')
    if engine is None:
        return current_bar_data
    tq = getattr(engine, 'tq_adapter', None)
    if not tq or not current_bar_data:
        return current_bar_data
    try:
        snapshot = tq.get_snapshot(list(current_bar_data.keys()))
        if snapshot:
            return {**current_bar_data, **snapshot} if merge else snapshot
    except Exception as ex:
        logger.warning(fail_msg, ex)
    return current_bar_data


def tq_snapshot_refresh(cfg, current_bar_data, **ctx):
    """实盘模式行情刷新：调用 tq_adapter.get_snapshot(codes)，整表替换。

    Args:
        cfg: refresh 配置
        current_bar_data: 刷新前的 bar 数据
        **ctx: 上下文，需含 engine 引用

    Returns:
        dict: 刷新后的 bar_data（刷新失败则返回原 current_bar_data）
    """
    return _apply_tq_snapshot(ctx, current_bar_data, merge=False,
                              fail_msg="行情刷新失败: %s")


def noop_refresh(cfg, current_bar_data, **ctx):
    """空操作刷新：直接返回原 current_bar_data。

    回放模式无需刷新行情（数据由 kline_sequence 驱动）。
    """
    return current_bar_data


def mock_advance_refresh(cfg, current_bar_data, **ctx):
    """仿真模式推进刷新：调用 mock_provider 生成新 mock 数据，合并到 current_bar_data。

    Args:
        cfg: refresh 配置
        current_bar_data: 当前 bar 数据
        **ctx: 上下文，需含 engine 引用

    Returns:
        dict: 刷新后的 bar_data（生成失败则返回原 current_bar_data）
    """
    return _apply_tq_snapshot(ctx, current_bar_data, merge=True,
                              fail_msg="mock_advance_refresh 生成 mock 数据失败: %s")


__all__ = [
    "tq_snapshot_refresh",
    "noop_refresh",
    "mock_advance_refresh",
]
