"""正合测试：web_state 后端格式化 + monitoring_module 事件记录。

覆盖场景（测试 Python 后端，不测试 JS）：
1. normalize_display_ms() 返回归一化时间戳
2. get_timer_trigger_type() 识别边定时器
3. get_timer_trigger_type() 识别 TTL 超时
4. get_timer_trigger_type() 识别 Tick 定时器（regex 含 \\btick\\b）
5. _TIMER_TRIGGER_TYPES 条目数 ≥ 4
6. classify_event_type() 返回正确分类
7. format_event() 返回含预期字段的 dict
8. event_to_record() 处理 TickReceived
9. event_to_record() 处理 EdgeFired
10. EVENT_RECORD_ADAPTERS 或 adapter 函数存在（25+）
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from core.web_state import (
    CATEGORY_CONFIG,
    _TIMER_TRIGGER_TYPES,
    classify_event_type,
    format_event,
    get_timer_trigger_type,
    normalize_display_ms,
)
from core.monitoring_module import (
    EVENT_RECORD_ADAPTERS,
    event_to_record,
)
from core.event_bus import EdgeFired, TickReceived


# ------------------------------------------------------------------
# 测试 1：normalize_display_ms 归一化时间戳
# ------------------------------------------------------------------
def test_normalize_display_ms_relative_seconds():
    """相对秒（< 1e9）× 1000 转为毫秒。"""
    assert normalize_display_ms(34500.0) == 34500.0 * 1000.0
    assert normalize_display_ms(0) == 0.0


def test_normalize_display_ms_unix_seconds():
    """Unix 秒（>=1e9 且 <1e12）× 1000 转为毫秒。"""
    result = normalize_display_ms(1_000_000_000.0)
    assert result == 1_000_000_000_000.0


def test_normalize_display_ms_unix_ms():
    """Unix 毫秒（>=1e12）保持不变。"""
    val = 1_500_000_000_000.0
    assert normalize_display_ms(val) == val


def test_normalize_display_ms_invalid():
    """None / 非数值返回 None。"""
    assert normalize_display_ms(None) is None
    assert normalize_display_ms("not_a_number") is None


# ------------------------------------------------------------------
# 测试 5：_TIMER_TRIGGER_TYPES 条目数 ≥ 4
# ------------------------------------------------------------------
def test_timer_trigger_types_has_at_least_four():
    """_TIMER_TRIGGER_TYPES 表至少含 4 个触发类型规则。"""
    assert isinstance(_TIMER_TRIGGER_TYPES, list)
    assert len(_TIMER_TRIGGER_TYPES) >= 4
    # 每个条目含 key / label / match 三字段
    for rule in _TIMER_TRIGGER_TYPES:
        assert "key" in rule
        assert "label" in rule
        assert "match" in rule


# ------------------------------------------------------------------
# 测试 2：get_timer_trigger_type 识别边定时器
# ------------------------------------------------------------------
def test_get_timer_trigger_type_edge():
    """event_type 含 edgefired → 返回 '边定时器'。"""
    spec = {"event_type": "EdgeFired", "details": {"edge_id": "e1"}}
    assert get_timer_trigger_type(spec) == "边定时器"


def test_get_timer_trigger_type_edge_timer():
    """kind 含 edge.*timer → 返回 '边定时器'。"""
    spec = {"kind": "edge_timer", "details": {}}
    assert get_timer_trigger_type(spec) == "边定时器"


# ------------------------------------------------------------------
# 测试 3：get_timer_trigger_type 识别 TTL 超时
# ------------------------------------------------------------------
def test_get_timer_trigger_type_ttl():
    """details.ttl 存在 → 返回 'TTL超时'。"""
    spec = {"event_type": "TTLExpired", "details": {"ttl": 30}}
    assert get_timer_trigger_type(spec) == "TTL超时"


def test_get_timer_trigger_type_timeout():
    """fire_reason 含 timeout → 返回 'TTL超时'。"""
    spec = {"event_type": "", "details": {"reason": "timeout"}}
    assert get_timer_trigger_type(spec) == "TTL超时"


# ------------------------------------------------------------------
# 测试 4：get_timer_trigger_type 识别 Tick 定时器（regex 含 \btick\b）
# ------------------------------------------------------------------
def test_get_timer_trigger_type_tick_word_boundary():
    """event_type 为独立单词 'tick' → \\btick\\b 匹配 → 'Tick定时器'。"""
    spec = {"event_type": "tick", "details": {}}
    assert get_timer_trigger_type(spec) == "Tick定时器"


def test_get_timer_trigger_type_tickdue():
    """event_type 为 tickdue → 'Tick定时器'。"""
    spec = {"event_type": "TickDue", "details": {}}
    assert get_timer_trigger_type(spec) == "Tick定时器"


def test_get_timer_trigger_type_tick_timer():
    """kind 含 ticktimer → 'Tick定时器'。"""
    spec = {"kind": "ticktimer", "details": {}}
    assert get_timer_trigger_type(spec) == "Tick定时器"


def test_get_timer_trigger_type_tickreceived_not_tick_timer():
    """event_type 为 TickReceived 不应匹配 \\btick\\b（无词边界）。"""
    # TickReceived 中 tick 与 received 间无词边界，不应匹配 tick_timer 规则
    spec = {"event_type": "TickReceived", "details": {}}
    result = get_timer_trigger_type(spec)
    # 应落到更靠后的规则（如 'timer' / 'fire' / 'due' 或兜底 '定时器'）
    assert result != "Tick定时器"


# ------------------------------------------------------------------
# 测试 6：classify_event_type 返回正确分类
# ------------------------------------------------------------------
def test_classify_event_type_categories():
    """classify_event_type 将事件类型名映射到 category。"""
    assert classify_event_type("TickReceived") == "tick"
    assert classify_event_type("DataChanged") == "tick"
    assert classify_event_type("BarComposed") == "bar"
    assert classify_event_type("FormulaEvaluated") == "formula"
    assert classify_event_type("EdgeFired") == "edge"
    assert classify_event_type("TransferExecuted") == "transfer"
    assert classify_event_type("Signal") == "signal"
    assert classify_event_type("OrderPlaced") == "order"
    assert classify_event_type("TTLExpired") == "ttl"
    assert classify_event_type("ConfigChanged") == "system"
    assert classify_event_type("PoolLoaded") == "system"


def test_classify_event_type_fallback():
    """未知事件类型兜底返回 system。"""
    assert classify_event_type("SomethingNew") == "system"
    assert classify_event_type("") == "system"
    assert classify_event_type(None) == "system"


def test_category_config_consistent():
    """CATEGORY_CONFIG 的 key 与 classify_event_type 输出集合一致。"""
    assert isinstance(CATEGORY_CONFIG, dict)
    # 至少含 tick / edge / signal / system 四类
    for key in ("tick", "edge", "signal", "system"):
        assert key in CATEGORY_CONFIG
        assert "icon" in CATEGORY_CONFIG[key]


# ------------------------------------------------------------------
# 测试 7：format_event 返回含预期字段的 dict
# ------------------------------------------------------------------
def test_format_event_returns_expected_fields():
    """format_event 输出含 category / display_ts / display_time / time_mode。"""
    ev = {"event_type": "TickReceived", "ts": 34500.0, "code": "600000"}
    result = format_event(ev)
    assert isinstance(result, dict)
    assert result["category"] == "tick"
    assert "display_ts" in result
    assert "display_time" in result
    assert "display_time_ms" in result
    assert result["time_mode"] == "relative"
    assert result["display_ts"] == 34500.0 * 1000.0
    # 原字段保留
    assert result["code"] == "600000"
    assert result["event_type"] == "TickReceived"


def test_format_event_with_ttl_fire_at():
    """format_event 对含 ttl 的事件追加 fire_at_ms 字段。"""
    ev = {
        "event_type": "TTLExpired",
        "ts": 34500.0,
        "details": {"ttl": 30},
    }
    result = format_event(ev)
    assert result["category"] == "ttl"
    assert "fire_at_ms" in result
    assert "fire_at_time" in result
    assert "fire_at_time_ms" in result
    assert result["fire_at_ms"] == 34500.0 * 1000.0 + 30 * 1000.0


# ------------------------------------------------------------------
# 测试 8：event_to_record 处理 TickReceived
# ------------------------------------------------------------------
def test_event_to_record_tick_received():
    """event_to_record 将 TickReceived 转为含 price/volume 的记录。"""
    ev = TickReceived(
        tick_data={"price": 10.5, "volume": 100},
        code="600000",
        ts=34500.0,
    )
    record = event_to_record(ev)
    assert isinstance(record, dict)
    assert record["event_type"] == "TickReceived"
    assert record["code"] == "600000"
    assert record["ts"] == 34500.0
    details = record["details"]
    assert details["price"] == 10.5
    assert details["volume"] == 100


def test_event_to_record_tick_received_extracts_code():
    """tick_data.code 在 event.code 为空时作为兜底。"""
    ev = TickReceived(
        tick_data={"code": "000001", "price": 5.0, "volume": 50},
        code="",
        ts=100.0,
    )
    record = event_to_record(ev)
    assert record["code"] == "000001"


# ------------------------------------------------------------------
# 测试 9：event_to_record 处理 EdgeFired
# ------------------------------------------------------------------
def test_event_to_record_edge_fired():
    """event_to_record 将 EdgeFired 转为含 eid 的记录。"""
    ev = EdgeFired(eid="edge_42", ts=34600.0)
    record = event_to_record(ev)
    assert isinstance(record, dict)
    assert record["event_type"] == "EdgeFired"
    assert record["eid"] == "edge_42"
    assert record["edge_id"] == "edge_42"
    assert record["ts"] == 34600.0
    assert record["code"] == ""
    assert isinstance(record["details"], dict)


# ------------------------------------------------------------------
# 测试 10：EVENT_RECORD_ADAPTERS 或 adapter 函数存在（25+）
# ------------------------------------------------------------------
def test_event_record_adapters_count():
    """EVENT_RECORD_ADAPTERS 至少含 25 个 adapter 条目。"""
    assert isinstance(EVENT_RECORD_ADAPTERS, dict)
    assert len(EVENT_RECORD_ADAPTERS) >= 25
    # 每个值是 callable
    for name, adapter in EVENT_RECORD_ADAPTERS.items():
        assert callable(adapter), f"{name} adapter 不可调用"


def test_adapter_functions_exist():
    """monitoring_module 含 25+ 个 _adapter_* 函数（表驱动）。"""
    from core import monitoring_module as mm

    adapter_names = [
        name for name in dir(mm) if name.startswith("_adapter_")
    ]
    assert len(adapter_names) >= 25
    # 关键 adapter 存在
    for required in (
        "_adapter_tick_received",
        "_adapter_edge_fired",
        "_adapter_data_changed",
        "_adapter_config_changed",
    ):
        assert required in adapter_names


def test_event_to_record_unknown_uses_default_adapter():
    """event_to_record 对未知事件类型走 _default_adapter。"""
    from dataclasses import dataclass

    @dataclass
    class FakeEvent:
        ts: float = 99.0
        code: str = "FAKE"
        details: dict = None

        def __post_init__(self):
            if self.details is None:
                self.details = {"k": "v"}

    ev = FakeEvent()
    record = event_to_record(ev)
    assert record["event_type"] == "FakeEvent"
    assert record["ts"] == 99.0
    assert record["code"] == "FAKE"
    assert record["details"] == {"k": "v"}


# === Task 28.6 回归断言：converge-meta-essence-v4 阶段 3 C6 收敛状态 ===


class TestConvergenceRegressionV4:
    """SubTask 28.6：converge-meta-essence-v4 C6 _ADAPTER_SPECS 表驱动收敛回归。"""

    def test_adapter_specs_table_present(self):
        """monitoring_module 含 _ADAPTER_SPECS 表（C6 替代 24 个 _adapter_X）。"""
        import re
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "core" / "monitoring_module.py").read_text(encoding="utf-8")
        assert re.search(r"_ADAPTER_SPECS\s*[:=]", src), \
            "monitoring_module 应含 _ADAPTER_SPECS 表（C6 表驱动）"

    def test_no_adapter_functions_residue(self):
        """monitoring_module 不含 def _adapter_X 同构函数（C6 已表驱动化）。"""
        import re
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "core" / "monitoring_module.py").read_text(encoding="utf-8")
        count = len(re.findall(r"def _adapter_\w+\b", src))
        assert count == 0, \
            f"monitoring_module 不应含 def _adapter_X（C6 已表驱动），实际 {count} 处"

    def test_build_adapter_record_present(self):
        """monitoring_module 含 _build_adapter_record 通用 builder（C6 表驱动分派）。"""
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "core" / "monitoring_module.py").read_text(encoding="utf-8")
        assert "def _build_adapter_record" in src, \
            "monitoring_module 应含 _build_adapter_record 通用 builder（C6 表驱动分派）"
