"""时间源统一入口 + 统一时间驱动基础设施。

三模式时间架构（state.time_source["driver_type"]）：
    - wall_clock：实盘模式，由 run_tick 写入 current_ts（= _now().timestamp()）
    - sequence：回放模式，由 ReplayRunner 写入 K 线时间戳
    - virtual：仿真模式，由 Simulator 写入虚拟时钟

统一时间驱动：
    所有到时事件统一为 ``TimedEventSpec``：
      - at_fn() 返回到期时间（Unix 秒），<= now 表示到期
      - action(params) 到期时调用，发布事件参数不同，引发的下个事件不同
      - 边触发：action 发布 Executed → 订阅者执行 filter→propagate→callback
      - TTL到期：action 发布 DomainEvent(TIMEOUT) → 订阅者执行批量删除

    ``TtlTracker`` 是单条边的 TTL 追踪器（面向对象），仅管理到期时间堆，
    不发布事件——发布是 action 的职责，不是 tracker 的职责。

    ``EventDriver.fire_due(now)`` 统一扫描所有 TimedEventSpec，
    at_fn() <= now 就调 action——边触发和 TTL 完全同一套机制。
"""

from __future__ import annotations

import heapq
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


def time_at(source: Optional[str] = None, state: Any = None) -> float:
    """统一时间入口。三模式差异仅在参数，不在代码分支。"""
    if source == "wall" or state is None:
        return time.time()
    ts_cfg = getattr(state, "time_source", None)
    if not ts_cfg:
        return time.time()
    cur_ts = ts_cfg.get("current_ts")
    if cur_ts is not None:
        try:
            return float(cur_ts)
        except (TypeError, ValueError):
            pass
    return time.time()


def _safe_timestamp(dt_obj) -> float:
    """安全获取 datetime 的 timestamp，捕获 Windows 上旧时间戳的 OSError。"""
    try:
        return dt_obj.timestamp()
    except (OSError, ValueError):
        return time.time()


_OFFSET_THRESHOLD = 1e8


def is_offset_of_day(sec: float) -> bool:
    """判断 sec 是当日秒数偏移（< 1e8）还是 Unix 时间戳。"""
    return abs(sec) < _OFFSET_THRESHOLD


def anchor_to_today(sec: float):
    """将当日秒数偏移锚定到本日 00:00，返回 datetime。"""
    from datetime import datetime, timedelta
    base = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return base + timedelta(seconds=sec)


def time_now_unix(state: Any) -> float:
    """返回当前时间的 Unix 时间戳，用于 TTL entry_time 比较。"""
    sec = time_at(state=state)
    if is_offset_of_day(sec):
        return anchor_to_today(sec).timestamp()
    return sec


# ---------------------------------------------------------------------------
# TtlEntry + TtlTracker（面向对象：仅管理到期时间堆，不发布事件）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TtlEntry:
    """TTL 到期条目（值对象）：一只股票在特定目标池中的到期记录。

    入池时创建，入堆排序；出池时惰性删除（从 _entries 移除，堆弹出时跳过）。
    pop_expired 返回到期条目列表，由 action 消费并发布 DomainEvent(TIMEOUT)。
    """
    code: str
    tgt: str
    eid: str
    ttl_sec: float
    entry_ts: float
    expire_at: float

    def __lt__(self, other: "TtlEntry") -> bool:
        return self.expire_at < other.expire_at


class TtlTracker:
    """单条边的 TTL 追踪器（面向对象，仅管理到期时间堆）。

    职责单一：register / unregister / next_expire_at / pop_expired / clear。
    不发布事件——发布是 TimedEventSpec.action 的职责。

    next_expire_at() 使 TTL 的 at_fn 与边触发共用 ``at_fn() <= now`` 语义。
    """

    def __init__(self, tgt: str, eid: str) -> None:
        self._tgt = tgt
        self._eid = eid
        self._heap: List[TtlEntry] = []
        self._entries: Dict[str, TtlEntry] = {}

    @property
    def tgt(self) -> str:
        return self._tgt

    @property
    def eid(self) -> str:
        return self._eid

    def register(self, code: str, ttl_sec: float, entry_ts: float, now_unix: float) -> None:
        """股票入池时注册到期条目。expire_at = entry_ts + ttl_sec。

        已过期（expire_at <= now_unix）仍入堆，fire_due 时立即弹出。
        """
        if ttl_sec <= 0:
            return
        expire_at = entry_ts + ttl_sec
        entry = TtlEntry(
            code=code, tgt=self._tgt, eid=self._eid,
            ttl_sec=ttl_sec, entry_ts=entry_ts, expire_at=expire_at,
        )
        self._entries[code] = entry
        heapq.heappush(self._heap, entry)

    def unregister(self, code: str) -> None:
        """股票出池时取消注册（惰性删除）。"""
        self._entries.pop(code, None)

    def next_expire_at(self) -> float:
        """返回堆顶到期时间。空堆返回 inf（永不到期）。"""
        while self._heap:
            top = self._heap[0]
            if top.code in self._entries:
                return top.expire_at
            heapq.heappop(self._heap)
        return float("inf")

    def pop_expired(self, now_unix: float) -> List[TtlEntry]:
        """弹出所有到期条目（expire_at <= now_unix），跳过已取消的。"""
        expired: List[TtlEntry] = []
        while self._heap and self._heap[0].expire_at <= now_unix:
            entry = heapq.heappop(self._heap)
            if entry.code in self._entries:
                del self._entries[entry.code]
                expired.append(entry)
        return expired

    def clear(self) -> None:
        """清空所有追踪。"""
        self._heap.clear()
        self._entries.clear()


# ---------------------------------------------------------------------------
# TimedEventSpec（统一到时事件规格）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TimedEventSpec:
    """到时事件规格表行——边触发与 TTL 共用。

    到时触发是到时触发，执行事件是执行事件。
    所有到时事件统一为 TimedEventSpec，区别仅在 params 不同、引发的下个事件不同：
      - 边触发：action 发布 Executed → 订阅者执行 filter→propagate→callback
      - TTL到期：action 发布 DomainEvent(TIMEOUT) → 订阅者执行批量删除

    Attributes:
        at_fn:   计算下次触发时间（Unix 秒）。<= now 表示到期。
        interval: 触发间隔（秒）。None=一次性事件。
        end_fn:  计算结束时间（Unix 秒）。None=永久。
        action:  事件回调，签名为 ``action(params)``。
        params:  事件参数字典。
    """

    at_fn: Callable[[], float]
    interval: Optional[float]
    end_fn: Optional[Callable[[], float]]
    action: Callable[[Any], None]
    params: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# EventDriver — 统一时间驱动器
# ---------------------------------------------------------------------------


class EventDriver:
    """统一时间驱动器：所有 TimedEventSpec 共用 at_fn() <= now 到期判定。

    fire_due(now) 统一扫描所有 spec，at_fn() <= now 就调 action。
    边触发和 TTL 完全同一套机制——区别仅在 action 发布的事件不同。

    TtlTracker 仅供 TTL 类型的 at_fn 委托 next_expire_at()，
    以及运行期 register/unregister 到期条目。
    """

    def __init__(self, state: Any = None, bus: Any = None) -> None:
        self._state = state
        self._bus = bus
        self._specs: List[TimedEventSpec] = []
        self._ttl_trackers: Dict[str, TtlTracker] = {}

    def add_spec(self, spec: TimedEventSpec) -> None:
        """注册到时事件规格（边触发和 TTL 统一入口）。"""
        self._specs.append(spec)

    def add_ttl_tracker(self, eid: str, tracker: TtlTracker) -> None:
        """注册 TTL 追踪器（interval 类型，运行期 register/unregister）。"""
        self._ttl_trackers[eid] = tracker

    def register_ttl(self, eid: str, code: str, ttl_sec: float, entry_ts: float, now_unix: float) -> None:
        """运行期：股票入池时注册 TTL 到期。"""
        tracker = self._ttl_trackers.get(eid)
        if tracker is not None:
            tracker.register(code, ttl_sec, entry_ts, now_unix)

    def unregister_ttl(self, eid: str, code: str) -> None:
        """运行期：股票出池时取消 TTL 到期。"""
        tracker = self._ttl_trackers.get(eid)
        if tracker is not None:
            tracker.unregister(code)

    def is_edge_due(self, eid: str, now: float) -> bool:
        """边触发到期判定（兼容旧接口，tick body 中使用）。"""
        for spec in self._specs:
            if spec.params.get("eid") == eid:
                return spec.at_fn() <= now
        return True

    def fire_due(self, now: float) -> None:
        """统一到期触发：遍历所有 spec，at_fn() <= now 就调 action。

        边触发和 TTL 完全同一套机制——at_fn 判定到期，action 发布事件，
        订阅者执行具体逻辑。区别仅在 params 不同、引发的下个事件不同。
        """
        for spec in self._specs:
            try:
                if spec.at_fn() <= now:
                    spec.action(spec.params)
            except Exception:
                logger.warning("TimedEventSpec action 异常", exc_info=True)

    def fire_ttl_due(self, now: float) -> None:
        """TTL 到期触发（兼容旧接口，仅处理 TTL 类型 spec）。"""
        for spec in self._specs:
            if spec.params.get("kind") != "ttl":
                continue
            try:
                if spec.at_fn() <= now:
                    spec.action(spec.params)
            except Exception:
                logger.warning("TTL spec action 异常", exc_info=True)

    def clear_ttl(self) -> None:
        """清空所有 TTL 追踪器。"""
        for tracker in self._ttl_trackers.values():
            tracker.clear()
