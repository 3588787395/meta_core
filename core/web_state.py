"""前端展示态格式化：把事件、计时器队列、运行时时间等复杂计算收敛到后端。

按 ``specify-frontend-improvement`` Task 1 要求：
- 事件分类（category）
- 时间戳归一化 / 仿真时间换算
- TTL / fire_at 计算
- 计时器状态、触发类型、剩余时间
- 运行时当前展示时间

全部由本模块输出为可直接渲染的字段，前端 ``event-panel.js`` 只负责展示。
"""
from __future__ import annotations

import math
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

# 分类配置：与前端 event-panel.js CATEGORY_CONFIG 保持一致（仅用于输出 category 字段）
CATEGORY_CONFIG: Dict[str, Dict[str, str]] = {
    "tick": {"color": "#9e9e9e", "icon": "📡", "label": "Tick"},
    "bar": {"color": "#2196f3", "icon": "📈", "label": "Bar"},
    "formula": {"color": "#4caf50", "icon": "🧮", "label": "Formula"},
    "edge": {"color": "#ff9800", "icon": "🔀", "label": "Edge"},
    "transfer": {"color": "#9c27b0", "icon": "🔄", "label": "Transfer"},
    "signal": {"color": "#f44336", "icon": "🔔", "label": "Signal"},
    "order": {"color": "#ffc107", "icon": "📋", "label": "Order"},
    "ttl": {"color": "#b71c1c", "icon": "⏰", "label": "TTL"},
    "system": {"color": "#00bcd4", "icon": "⚙", "label": "System"},
}

_CATEGORIES = list(CATEGORY_CONFIG.keys())

# 事件类型 -> 分类
_EVENT_TYPE_TO_CATEGORY: Dict[str, str] = {
    "tickreceived": "tick",
    "datachanged": "tick",
    "tick": "tick",
    "tick_update": "tick",
    "tickdue": "tick",
    "barcomposed": "bar",
    "bar": "bar",
    "kline": "bar",
    "formulaevaluated": "formula",
    "stockfiltered": "formula",
    "formula": "formula",
    "filter": "formula",
    "edgefired": "edge",
    "crossoverdetected": "edge",
    "edge": "edge",
    "cross": "edge",
    "crossover": "edge",
    "transferexecuted": "transfer",
    "executed": "transfer",
    "rankingchanged": "transfer",
    "transfer": "transfer",
    "enter": "transfer",
    "exit": "transfer",
    "rank_changed": "transfer",
    "signal": "signal",
    "buy": "signal",
    "sell": "signal",
    "orderplaced": "order",
    "orderfilled": "order",
    "positionupdated": "order",
    "order": "order",
    "ttlexpired": "ttl",
    "ttldue": "ttl",
    "timeout": "ttl",
    "ttl": "ttl",
    "timerqueued": "ttl",
    "timerfired": "ttl",
    "timerexpired": "ttl",
    "edgetimer": "ttl",
    "ticktimer": "ttl",
    "modechanged": "system",
    "timeadvanced": "system",
    "poolloaded": "system",
    "configloaded": "system",
    "configchanged": "system",
    "simulationstep": "system",
    "replaystep": "system",
    "replaystarted": "system",
    "importstarted": "system",
    "exportcompleted": "system",
    "statisticsupdated": "system",
    "snapshotupdated": "system",
    "eventlogged": "system",
    "alertraised": "system",
    "alert": "system",
    "loaded": "system",
    "saved": "system",
    "import": "system",
    "export": "system",
    "system": "system",
    "unknown": "system",
}

# 计时器触发类型识别规则
_TIMER_TRIGGER_TYPES: List[Dict[str, Any]] = [
    {"key": "edge_timer", "label": "边定时器", "match": "edge.*timer|edgetimer|edgefired|crossover"},
    {"key": "ttl", "label": "TTL超时", "match": "ttl|ttldue|ttlexpired|timeout"},
    {"key": "tick_timer", "label": "Tick定时器", "match": "ticktimer|tick.*timer|tickdue|\\btick\\b"},
    {"key": "one_shot", "label": "一次性", "match": "oneshot|one_shot|count_gte_1|single"},
    {"key": "recurring", "label": "循环", "match": "recurring|periodic|interval|cxtype.*0"},
    {"key": "timer", "label": "定时器", "match": "timer|fire|due"},
]


def classify_event_type(event_type: Any) -> str:
    """根据事件类型名返回分类 key。"""
    t = str(event_type or "UNKNOWN").lower().strip()
    if t in _EVENT_TYPE_TO_CATEGORY:
        return _EVENT_TYPE_TO_CATEGORY[t]
    # 兜底：按关键字前缀/包含匹配
    if "buy" in t or "sell" in t:
        return "signal"
    if "tick" in t or "data" in t:
        return "tick"
    if "bar" in t or "kline" in t:
        return "bar"
    if "formula" in t or "filter" in t:
        return "formula"
    if "edge" in t or "cross" in t:
        return "edge"
    if "transfer" in t or "executed" in t or "rank" in t or "enter" in t or "exit" in t:
        return "transfer"
    if "order" in t or "position" in t:
        return "order"
    if "ttl" in t or "timeout" in t or "expire" in t or "timer" in t:
        return "ttl"
    return "system"


def _to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(n) else n


def normalize_display_ms(ts: Any) -> Optional[float]:
    """将任意时间戳归一化为展示毫秒。

    规则：
    - 相对秒（仿真 clock，< 1e9） -> *1000
    - Unix 秒（>=1e9 且 <1e12） -> *1000
    - Unix 毫秒（>=1e12） -> 保持
    """
    n = _to_float(ts)
    if n is None:
        return None
    if n < 1e9:
        return n * 1000.0
    if n < 1e12:
        return n * 1000.0
    return n


def _is_relative_ts(ts: Any) -> bool:
    """判断时间戳是否为相对秒（仿真/回放坐标系）。"""
    n = _to_float(ts)
    return n is not None and n < 1e9


def format_sim_duration(ms: float) -> str:
    """将毫秒格式化为仿真时长 HH:MM:SS。"""
    if ms < 0:
        ms = 0
    total_sec = int(ms // 1000)
    h = total_sec // 3600
    m = (total_sec % 3600) // 60
    s = total_sec % 60
    return f"{h}:{m:02d}:{s:02d}"


def format_sim_duration_ms(ms: float) -> str:
    """将毫秒格式化为仿真时长 HH:MM:SS.mmm。"""
    if ms < 0:
        ms = 0
    total_sec = int(ms // 1000)
    h = total_sec // 3600
    m = (total_sec % 3600) // 60
    s = total_sec % 60
    milli = int(ms % 1000)
    return f"{h}:{m:02d}:{s:02d}.{milli:03d}"


def format_wall_time(ms: float) -> str:
    """将 Unix 毫秒格式化为 HH:MM:SS。"""
    try:
        return datetime.fromtimestamp(ms / 1000.0).strftime("%H:%M:%S")
    except Exception:
        return str(ms)


def format_wall_time_ms(ms: float) -> str:
    """将 Unix 毫秒格式化为 HH:MM:SS.mmm。"""
    try:
        return datetime.fromtimestamp(ms / 1000.0).strftime("%H:%M:%S.%f")[:-3]
    except Exception:
        return str(ms)


def format_display_time(ms: float, relative: bool = False) -> str:
    if relative:
        return format_sim_duration(ms)
    return format_wall_time(ms)


def format_display_time_ms(ms: float, relative: bool = False) -> str:
    if relative:
        return format_sim_duration_ms(ms)
    return format_wall_time_ms(ms)


def get_fire_at_ms(ev: Dict[str, Any]) -> Optional[float]:
    """从事件/定时器规格中提取触发时刻的展示毫秒。"""
    d = ev.get("details") or {}
    for key in ("fire_at", "fire_time", "next_fire_time"):
        v = d.get(key)
        if v is not None:
            return normalize_display_ms(v)
    if "ttl" in d:
        ttl = _to_float(d.get("ttl"))
        if ttl is not None:
            ttl_ms = ttl * 1000.0 if ttl < 1e6 else ttl
            base = normalize_display_ms(ev.get("ts") or ev.get("timestamp") or ev.get("time"))
            if base is not None:
                return base + ttl_ms
    return None


def get_timer_state(fire_at_ms: Optional[float], now_ms: float, event_type: str = "") -> str:
    """返回计时器状态：waiting / fired / expired。"""
    t = str(event_type).lower()
    if "fired" in t or "expired" in t or "triggered" in t:
        return "fired"
    if fire_at_ms is None:
        return "waiting"
    if fire_at_ms < now_ms - 100:
        return "expired"
    return "waiting"


def get_timer_trigger_type(spec: Dict[str, Any]) -> str:
    """根据定时器规格识别触发类型标签。"""
    d = spec.get("details") or {}
    hints = " ".join([
        str(spec.get("event_type") or ""),
        str(spec.get("kind") or ""),
        str(d.get("trigger_type") or d.get("timer_kind") or d.get("kind") or ""),
        str(d.get("fire_reason") or d.get("reason") or ""),
        "edge" if d.get("edge_id") or spec.get("edge_id") else "",
        "ttl" if d.get("ttl") else "",
        "recurring" if d.get("interval") else "",
        "oneshot" if d.get("one_shot") else "",
    ]).lower()
    for rule in _TIMER_TRIGGER_TYPES:
        import re
        if re.search(rule["match"], hints):
            return rule["label"]
    return "定时器"


def format_remaining_time(fire_at_ms: float, now_ms: float) -> str:
    diff = fire_at_ms - now_ms
    if diff <= 0:
        return "已触发"
    if diff < 1000:
        return f"{int(diff)}ms后触发"
    if diff < 60000:
        return f"{diff / 1000.0:.1f}s后触发"
    if diff < 3600000:
        minutes = int(diff // 60000)
        seconds = int((diff % 60000) // 1000)
        return f"{minutes}m{seconds}s后触发"
    return f"{int(diff // 3600000)}h后触发"


def format_event(ev: Dict[str, Any]) -> Dict[str, Any]:
    """为前端事件面板生成可直接展示的字段。

    输入为字典形式事件（来自 EventBus 或 API 归一化后），输出在保留原字段基础上追加：
    - category
    - display_ts / display_time / display_time_ms
    - fire_at_ms / fire_at_time / fire_at_time_ms（仅限含 fire_at/ttl 的事件）
    - time_mode: 'relative' | 'wall'
    """
    result = dict(ev)
    ev_type = str(ev.get("event_type") or ev.get("type") or "UNKNOWN")
    result["category"] = classify_event_type(ev_type)

    # 取原始时间戳：优先 timestamp，其次 ts / time
    raw_ts = ev.get("timestamp")
    if raw_ts is None:
        raw_ts = ev.get("ts")
    if raw_ts is None:
        raw_ts = ev.get("time")

    relative = _is_relative_ts(raw_ts)
    result["time_mode"] = "relative" if relative else "wall"

    display_ms = normalize_display_ms(raw_ts)
    if display_ms is None:
        # 没有时间戳时按当前模式兜底
        display_ms = time.time() * 1000.0
        result["time_mode"] = "wall"

    result["display_ts"] = display_ms
    result["display_time"] = format_display_time(display_ms, relative=relative)
    result["display_time_ms"] = format_display_time_ms(display_ms, relative=relative)

    fire_at_ms = get_fire_at_ms(ev)
    if fire_at_ms is not None:
        fire_relative = _is_relative_ts(raw_ts) or fire_at_ms < 86400000.0
        result["fire_at_ms"] = fire_at_ms
        result["fire_at_time"] = format_display_time(fire_at_ms, relative=fire_relative)
        result["fire_at_time_ms"] = format_display_time_ms(fire_at_ms, relative=fire_relative)

    return result


def format_timer_item(spec: Dict[str, Any], now_ms: float) -> Dict[str, Any]:
    """格式化单个计时器队列为前端可直接展示的字典。"""
    item = dict(spec)
    fire_at_ms = normalize_display_ms(spec.get("fire_at"))
    if fire_at_ms is None:
        fire_at_ms = now_ms
    ev_type = str(spec.get("event_type") or spec.get("kind") or "TimerQueued")
    state = get_timer_state(fire_at_ms, now_ms, ev_type)
    item["category"] = "ttl"
    item["display_fire_ms"] = fire_at_ms
    item["display_fire_time"] = format_display_time(fire_at_ms, relative=fire_at_ms < 86400000.0)
    item["display_fire_time_ms"] = format_display_time_ms(fire_at_ms, relative=fire_at_ms < 86400000.0)
    item["state"] = state
    item["trigger_type"] = get_timer_trigger_type(spec)
    item["remaining_text"] = format_remaining_time(fire_at_ms, now_ms)
    return item


def format_timer_queue(timers: List[Dict[str, Any]], now_ms: Optional[float] = None) -> Dict[str, Any]:
    """格式化整个计时器队列响应。"""
    if now_ms is None:
        now_ms = time.time() * 1000.0
    relative = now_ms < 86400000.0
    return {
        "success": True,
        "now_ms": now_ms,
        "now_time": format_display_time(now_ms, relative=relative),
        "now_time_ms": format_display_time_ms(now_ms, relative=relative),
        "count": len(timers),
        "timers": [format_timer_item(t, now_ms) for t in timers],
    }


def display_now_ms(mode: str = "live", now_ts: Optional[float] = None) -> float:
    """根据运行时模式返回当前展示毫秒。"""
    if now_ts is not None:
        ms = normalize_display_ms(now_ts)
        if ms is not None:
            return ms
    return time.time() * 1000.0


def runtime_state(
    mode: str = "live",
    now_ts: Optional[float] = None,
    active_session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """生成 /api/state/runtime 响应。"""
    now_ms = display_now_ms(mode, now_ts)
    relative = mode in ("simulation", "replay") or now_ms < 86400000.0
    return {
        "mode": mode,
        "display_now_ms": now_ms,
        "display_now_time": format_display_time(now_ms, relative=relative),
        "display_now_time_ms": format_display_time_ms(now_ms, relative=relative),
        "active_session_id": active_session_id,
    }
