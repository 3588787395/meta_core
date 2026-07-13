"""运行时表真相源：PoolState 与 15 张核心运行时表。

本模块按 ``execute-architecture-migration`` 规格 Task 2 实现，
将原先散落在 ``MetaEngine`` 中的 29 张运行时表收敛为 15 张目标表，
并提供统一的读写接口。

Task 10 扩展：新增 ``data_source`` / ``trade_interface`` /
``side_effects_scope`` 三模式配置行，以及 ``replay`` / ``simulator``
子对象用于状态隔离。

收敛后 ``PoolState`` 仅保留 5 个核心属性：
  - pool_config
  - _tables（15 张运行时表容器）
  - dirty
  - edge_state
  - first_run

其余表级访问方法集中到 ``PoolStateMixin``，保持核心类简洁。
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from .edge_state import EdgeState
except ImportError:
    from edge_state import EdgeState

try:
    from .time_util import time_at
except ImportError:
    from time_util import time_at


def _hash_tick(tick: Dict[str, Any]) -> str:
    """对单只股票 tick 做确定性摘要（I26：与 data_updater 路径统一的 per-code hash）。

    排除 ``_ts`` / ``_hash`` 元数据字段，使 per-code _hash 仅依赖行情内容
    （open/high/low/close/volume/...），不受时间戳影响。这保证：
      - 相同行情内容在不同时间到达（replay vs live）产生相同 per-code _hash
      - ``update_latest_tick``（全量替换）与 ``apply_data``（增量更新）两条路径
        对相同内容产生相同 per-code _hash，进而相同聚合 hash 与缓存键
      - ``update_latest_tick`` 重复调用（无 ``_ts`` 输入）保持幂等
    """
    content = {k: v for k, v in tick.items() if k not in ("_ts", "_hash")}
    try:
        payload = json.dumps(content, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        payload = str(sorted(content.items()))
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


# 15 张运行时表名（按 ARCHITECTURE_FINAL.md 收敛；I13 新增 prev_tick 供 TickTable 双周期视图；
# I60 移除 exit_tracker_cache——该表从不写入，为 vestigial 死状态；
# I74 移除 trackers——仅 _init_entry_trackers 写入 1 次，_update_trackers 从不同步，
# 生产 0 读取（post_tick 读 stock._tracker，_build_exit_tracker_info 读 prev_stock_index），
# 为 vestigial 死状态。tracker 单一真相源 = stock._tracker）
_TABLE_NAMES: frozenset[str] = frozenset({
    "node_stocks",
    "latest_tick",
    "prev_tick",
    "bars",
    "node_snapshots",
    "topology",
    "post_tick_results",
    "alert_cooldown",
    "time_source",
    "data_source",
    "trade_interface",
    "side_effects_scope",
    "replay",
    "simulator",
    "bars_history",
})


@dataclass
class DirtyState:
    """脏标记对象：合并原 ``_dirty_nodes`` 与 ``_data_dirty`` 两张表。"""

    nodes: Dict[str, bool] = field(default_factory=dict)
    data: bool = False

    @property
    def node_dirty(self) -> Dict[str, bool]:
        return self.nodes

    @property
    def data_dirty(self) -> bool:
        return self.data


class PoolStateMixin:
    """PoolState 表级访问方法集合。

    将 15 张运行时表的读写、回放隔离、拓扑预建等职责从 ``PoolState``
    核心类中剥离，使其属性/方法数满足架构约束。
    """

    def _populate_tables(self) -> None:
        """初始化 15 张运行时表容器。"""
        self._tables = {name: {} for name in _TABLE_NAMES}

    def _build_topology(self) -> None:
        """根据 pool_config 的 nodes/edges 预建 topology 邻接表。"""
        cfg = self.pool_config
        edges = cfg.get("edges", [])
        nodes = cfg.get("nodes", [])
        node_ids = {n["id"] for n in nodes if isinstance(n, dict) and "id" in n}
        adj: Dict[str, List[str]] = {nid: [] for nid in node_ids}
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            src = edge.get("source") or edge.get("from") or edge.get("sid")
            eid = edge.get("id") or edge.get("flow_id")
            if src and eid:
                adj.setdefault(str(src), []).append(str(eid))
        self.topology = adj

    def __getattr__(self, name: str) -> Any:
        if name in _TABLE_NAMES:
            return self._tables[name]
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    def __setattr__(self, name: str, value: Any) -> None:
        if name in _TABLE_NAMES and hasattr(self, "_tables"):
            self._tables[name] = value
        else:
            object.__setattr__(self, name, value)

    # ------------------------------------------------------------------
    # 边级运行时表代理（由 EdgeState 持有）
    # ------------------------------------------------------------------
    @property
    def exec_ctx(self) -> Dict[str, Dict[str, Any]]:
        return self.edge_state.exec_ctx

    @property
    def formula_results(self) -> Dict[Tuple[Any, str], Any]:
        return self.edge_state.formula_results

    @property
    def filter_inputs(self) -> Dict[str, frozenset]:
        return self.edge_state.filter_inputs

    # ------------------------------------------------------------------
    # node_stocks
    # ------------------------------------------------------------------
    def get_node_stocks(self, nid: str) -> List[Any]:
        return list(self.node_stocks.get(nid, []))

    def set_node_stocks(self, nid: str, stocks: List[Any]) -> None:
        self.node_stocks[nid] = list(stocks)

    # ------------------------------------------------------------------
    # latest_tick（行情唯一真相源）
    # ------------------------------------------------------------------
    def get_latest_tick(self) -> Dict[str, Any]:
        return self.latest_tick

    def bar_hash(self) -> str:
        """返回 ``latest_tick`` 顶层 ``_hash``（缓存键 / 事件 payload）；缺失返回空串。

        I25：收敛 ``state.latest_tick.get("_hash","")`` 全系统 4 处重复访问
        （formula.py / engine.py / data_updater.py / runtime.py）到唯一访问器。
        与 ``TickTable.bar_hash()``（视图层）形成双层一致性，二者读取同一字段。
        """
        return self.latest_tick.get("_hash", "")

    def update_latest_tick(self, tick_data: Optional[Dict[str, Any]]) -> bool:
        """刷新 latest_tick，自动计算 hash 与水位线 _ts。

        Returns:
            True 表示 hash 变化（内容推进），False 表示无变化或空输入。

        I26：与 ``DataUpdater.apply_data`` 路径统一——规范化每个 tick（注入 ``code``、
        设置 per-code ``_hash``/``_ts``），并使用聚合 hash 算法。两条写入路径对
        相同行情内容现在产生相同的 ``latest_tick["_hash"]``，缓存键
        ``(formula, mode, ref, bar_hash)`` 不再因路径切换而失效。

        注意：``.clear()`` + ``.update()`` 而非 ``= dict(...)``，保留 dict 对象身份
        使 TickTable 等 view 持有者引用稳定（I13）。
        """
        if not tick_data:
            return False
        now = time_at(state=self)
        normalized: Dict[str, Any] = {}
        for code, raw in tick_data.items():
            if isinstance(code, str) and code.startswith("_"):
                # 顶层元数据键（如 _hash/_ts）跳过——由本方法重新计算
                continue
            if not isinstance(raw, dict):
                continue
            tick = dict(raw)
            tick["code"] = str(code)
            if "_ts" not in tick:
                tick["_ts"] = now
            tick["_hash"] = _hash_tick(tick)
            normalized[str(code)] = tick
        if not normalized:
            return False
        new_hash = self._hash_tick_data(normalized)
        if self.bar_hash() == new_hash:
            return False
        self.latest_tick.clear()
        self.latest_tick.update(normalized)
        self.latest_tick["_hash"] = new_hash
        self.latest_tick["_ts"] = now
        self.mark_data_dirty()
        return True

    @staticmethod
    def _hash_tick_data(tick_data: Dict[str, Any]) -> str:
        """对行情数据做聚合 hash，与 ``DataUpdater._hash_aggregate`` 算法一致。

        I26：统一双 hash 算法。原 ``md5(json(whole tick_data))`` 与 ``_hash_aggregate``
        （per-code ``_hash`` 聚合）对相同行情内容产生不同 hash，导致缓存键在
        ``update_latest_tick``（全量替换）与 ``apply_data``（增量更新）两条路径间
        不命中。现统一为聚合算法：对每个 code 取其 per-code ``_hash``（缺失则从
        tick 内容计算），按 code 排序后用 ``\\x00`` 连接做 md5。
        """
        payload_parts: List[str] = []
        for code in sorted(tick_data.keys()):
            if isinstance(code, str) and code.startswith("_"):
                continue
            tick = tick_data[code]
            if not isinstance(tick, dict):
                continue
            per_hash = tick.get("_hash")
            if not per_hash:
                # 与 _apply_code_tick 一致：注入 code 字段后计算 per-code hash
                tick_copy = dict(tick)
                tick_copy.setdefault("code", str(code))
                per_hash = _hash_tick(tick_copy)
            payload_parts.append(f"{code}:{per_hash}")
        payload = "\x00".join(payload_parts)
        return hashlib.md5(payload.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    # dirty 标记
    # ------------------------------------------------------------------
    def mark_node_dirty(self, nid: str) -> None:
        self.dirty.nodes[nid] = True

    def mark_data_dirty(self) -> None:
        self.dirty.data = True

    def is_node_dirty(self, nid: str) -> bool:
        return self.dirty.nodes.get(nid, False)

    def is_data_dirty(self) -> bool:
        return self.dirty.data

    def clear_dirty(self) -> None:
        self.dirty.nodes.clear()
        self.dirty.data = False

    # ------------------------------------------------------------------
    # exec_ctx —— 委托给 EdgeState
    # ------------------------------------------------------------------
    def get_exec_ctx(self, eid: str) -> Dict[str, Any]:
        return self.edge_state.get_exec_ctx(eid)

    def set_exec_ctx_fired(self, eid: str, now: Optional[float] = None) -> None:
        self.edge_state.set_exec_ctx_fired(eid, now=now)

    # ------------------------------------------------------------------
    # formula_results —— 委托给 EdgeState
    # ------------------------------------------------------------------
    def get_formula_result(self, formula_ref: Any, bar_hash: str) -> Any:
        return self.edge_state.get_formula_result(formula_ref, bar_hash)

    def set_formula_result(self, formula_ref: Any, bar_hash: str, result: Any) -> None:
        self.edge_state.set_formula_result(formula_ref, bar_hash, result)

    # ------------------------------------------------------------------
    # node_snapshots
    # ------------------------------------------------------------------
    def snapshot_nodes(self) -> Dict[str, frozenset]:
        """将当前 node_stocks 聚合为 ``{nid: frozenset(code)}`` 并保存。"""
        snapshots: Dict[str, frozenset] = {}
        for nid, stocks in self.node_stocks.items():
            snapshots[nid] = self._snapshot_stocks(stocks)
        self.node_snapshots.update(snapshots)
        return snapshots

    def restore_snapshots(self) -> Dict[str, List[Dict[str, Any]]]:
        """从 node_snapshots 还原 node_stocks（仅保留 code 字段）。"""
        restored: Dict[str, List[Dict[str, Any]]] = {}
        for nid, codes in self.node_snapshots.items():
            restored[nid] = [{"code": code} for code in codes]
        self.node_stocks = restored
        return restored

    @staticmethod
    def _snapshot_stocks(stocks: List[Any]) -> frozenset:
        codes = set()
        for s in stocks:
            if isinstance(s, dict):
                code = s.get("code")
                if code is not None:
                    codes.add(str(code))
            elif s is not None:
                codes.add(str(s))
        return frozenset(codes)

    # ------------------------------------------------------------------
    # time_source / 三模式配置行
    # ------------------------------------------------------------------
    def set_time_source(self, ts_config: Dict[str, Any]) -> None:
        self.time_source = dict(ts_config)

    def get_time_source(self) -> Dict[str, Any]:
        return self.time_source

    def set_data_source(self, ds_config: Dict[str, Any]) -> None:
        self.data_source = dict(ds_config)

    def get_data_source(self) -> Dict[str, Any]:
        return self.data_source

    def set_trade_interface(self, ti_config: Dict[str, Any]) -> None:
        self.trade_interface = dict(ti_config)

    def get_trade_interface(self) -> Dict[str, Any]:
        return self.trade_interface

    def set_side_effects_scope(self, se_config: Dict[str, Any]) -> None:
        self.side_effects_scope = dict(se_config)

    def get_side_effects_scope(self) -> Dict[str, Any]:
        return self.side_effects_scope

    # ------------------------------------------------------------------
    # 回放状态隔离
    # ------------------------------------------------------------------
    def _snapshot_edge_state(self) -> Dict[str, Any]:
        """快照当前边级状态。"""
        return self.edge_state.snapshot()

    def _fresh_edge_state(self) -> Dict[str, Any]:
        """创建全新的回放边级状态副本。"""
        return {
            "exec_ctx": {},
            "formula_results": {},
            "filter_inputs": {},
        }

    def enter_replay(self) -> None:
        """进入回放模式：快照实盘状态并切换到回放副本。

        回放期间 ``run_tick()`` 操作的是 ``replay.node_stocks`` 与
        ``replay.edge_state``，回放结束调用 ``exit_replay()`` 恢复实盘状态。
        """
        self.replay["live_node_stocks"] = copy.deepcopy(self.node_stocks)
        self.replay["live_edge_state"] = self._snapshot_edge_state()
        self.replay["live_node_snapshots"] = copy.deepcopy(self.node_snapshots)
        self.replay["live_dirty"] = copy.deepcopy(self.dirty)
        self.replay["live_first_run"] = self.first_run
        self.replay["node_stocks"] = copy.deepcopy(self.node_stocks)
        self.replay["edge_state"] = EdgeState()
        self.replay["node_snapshots"] = {}
        self.replay["dirty"] = DirtyState()
        self.replay["first_run"] = True
        self.replay["active"] = True
        self._swap_to_replay()

    def exit_replay(self) -> None:
        """退出回放模式：恢复实盘 ``node_stocks`` 与边级状态。"""
        live_node_stocks = self.replay.get("live_node_stocks", {})
        live_edge_state = self.replay.get("live_edge_state", {})
        self.node_stocks = live_node_stocks
        self.node_snapshots = self.replay.get("live_node_snapshots", {})
        self.dirty = self.replay.get("live_dirty", DirtyState())
        self.first_run = self.replay.get("live_first_run", self.first_run)
        self.edge_state.restore(live_edge_state)
        self.replay["active"] = False

    def _swap_to_replay(self) -> None:
        """将运行时装态切换到回放副本。"""
        self.node_stocks = self.replay["node_stocks"]
        self.node_snapshots = self.replay.get("node_snapshots", {})
        self.dirty = self.replay.get("dirty", DirtyState())
        self.first_run = self.replay.get("first_run", True)
        self.edge_state = self.replay.get("edge_state", EdgeState())

    def is_replay_active(self) -> bool:
        return bool(self.replay.get("active"))


class PoolState(PoolStateMixin):
    """池级运行时表真相源。

    按 ``ARCHITECTURE_FINAL.md`` 约束，核心类仅保留 5 个属性：
      - pool_config
      - _tables（15 张运行时表容器，含 latest_tick + prev_tick 双周期）
      - dirty
      - edge_state
      - first_run

    15 张运行时表通过 ``_tables`` 按名访问；``__getattr__`` / ``__setattr__``
    提供对旧代码 ``self.node_stocks`` 等写法的兼容。
    """

    def __init__(self, pool_config: Optional[Dict[str, Any]] = None) -> None:
        self.pool_config = pool_config or {}
        self._tables = {}
        self.dirty = DirtyState()
        self.edge_state = EdgeState()
        self.first_run = True
        self._populate_tables()
        self._build_topology()


__all__ = ["DirtyState", "PoolState"]
