"""统一哈希函数模块——三族哈希函数的单一实现（继承原语收敛）。

合并 6 处 per-content MD5 + 3 处 aggregate tick hash + 3 处 bar_hash accessor，
消除跨 domain/tick_bar/formula/runtime_mode/execution 5 模块的哈希重复实现。
"""
import hashlib
import json
from typing import Any, Callable, Dict, Optional, Set


def hash_dict_content(content: Dict[str, Any], exclude: Optional[Set[str]] = None) -> str:
    """per-content MD5——对 dict 内容做稳定序列化后 MD5。

    合并 6 处重复：_hash_tick / _hash_bar / _hash_bars / _hash_object / _hash_code_bars
    （后两者的 None / DataFrame 特殊分支由调用方保留，dict 主路径收敛于此）。
    骨架：json.dumps(sort_keys=True, default=str, ensure_ascii=False) → except →
    str(sorted(items, key=key)) → md5 hexdigest。
    """
    exclude = exclude or set()
    filtered = {k: v for k, v in content.items() if k not in exclude}
    try:
        s = json.dumps(filtered, sort_keys=True, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        s = str(sorted(filtered.items(), key=lambda x: x[0]))
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def hash_tick_aggregate(
    tick_data: Dict[str, Any],
    per_code_hasher: Callable[[Any, Dict[str, Any]], str],
) -> str:
    """aggregate tick hash——按 code 排序聚合各 code 的 per-hash 后 MD5。

    合并 3 处重复：PoolStateMixin._hash_tick_data / _InternalState._hash_tick_data /
    _hash_period_bars。骨架：跳过 ``_`` 前缀键与非 dict 值，对每个 code 取其 per-code
    hash，按 code 排序后用 ``\\x00`` 连接做 md5。

    ``per_code_hasher(code, tick)`` 回调负责各处的 per-code 哈希差异：
      - PoolStateMixin：``tick.get("_hash") or hash_dict_content({**tick, "code": ...}, exclude={"_ts","_hash"})``
      - _InternalState：``tick.get("_hash") or hash_dict_content(tick, exclude={"_ts","_hash"})``
      - _hash_period_bars：``bar.get("_hash", "")``
    """
    parts = []
    for code in sorted(tick_data.keys()):
        if isinstance(code, str) and code.startswith("_"):
            continue
        tick = tick_data[code]
        if not isinstance(tick, dict):
            continue
        per_hash = per_code_hasher(code, tick)
        parts.append(f"{code}:{per_hash}")
    return hashlib.md5("\x00".join(parts).encode("utf-8")).hexdigest()


class BarHashMixin:
    """bar_hash accessor mixin——合并 3 处 ``return self.X.get("_hash", "")`` 重复。

    合并 PoolStateMixin.bar_hash / _InternalState.bar_hash / EdgeExecutor.bar_hash。
    子类覆盖 ``_get_bar_hash_container()`` 返回包含 ``_hash`` 字段的 dict
    （如 ``self.latest_tick`` / ``self._latest_tick``）。
    """

    def _get_bar_hash_container(self) -> Dict[str, Any]:
        """子类覆盖：返回包含 _hash 字段的 dict（如 self.latest_tick / self._latest_tick）。"""
        return getattr(self, "_latest_bar", None) or {}

    @property
    def bar_hash(self) -> str:
        """返回当前 bar 的 hash 字符串（无 bar 时空字符串）。"""
        container = self._get_bar_hash_container()
        return container.get("_hash", "")
