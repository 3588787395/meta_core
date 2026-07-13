"""公式结果 L1 进程内缓存。

缓存策略：
- Tick 级公式：不缓存（变化太快，缓存无意义）。
- 分钟级公式：缓存至当前分钟闭合，默认 TTL 60 秒（可通过 config/data_pipeline.json
  的 ``formula.cache_ttl_minute`` 覆盖）。
- 日线级公式：缓存至交易日结束，默认 TTL 86400 秒。
- 日终统一调用 ``invalidate_daily()``（即 ``clear_all()``），禁止跨交易日复用。

缓存条目格式：``{key: {'value': value, 'ts': timestamp, 'ttl': ttl}}``。
"""

import hashlib
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# config/data_pipeline.json 路径（services/ → 上两级 → config/）
_CONFIG_PATH = Path(__file__).resolve().parents[2] / 'config' / 'data_pipeline.json'

_DEFAULT_TTL_MINUTE = 60
_DEFAULT_TTL_DAY = 86400

# 分钟级周期集合（用于 TTL 与分钟闭合失效判断）
_MINUTE_PERIODS = frozenset({'1m', '5m', '15m', '30m', '60m'})
_ALL_PERIODS = _MINUTE_PERIODS | {'tick', '1d', '1w', '1mon'}


def _load_ttl_config() -> Dict[str, int]:
    """从 config/data_pipeline.json 加载公式缓存 TTL 配置。"""
    try:
        if _CONFIG_PATH.exists():
            with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            formula_cfg = cfg.get('formula', {}) if isinstance(cfg, dict) else {}
            return {
                'cache_ttl_minute': int(formula_cfg.get('cache_ttl_minute', _DEFAULT_TTL_MINUTE)),
                'cache_ttl_day': int(formula_cfg.get('cache_ttl_day', _DEFAULT_TTL_DAY)),
            }
    except Exception as e:
        logger.debug("加载公式缓存 TTL 配置失败，使用默认值: %s", e)
    return {
        'cache_ttl_minute': _DEFAULT_TTL_MINUTE,
        'cache_ttl_day': _DEFAULT_TTL_DAY,
    }


_TTL_CFG = _load_ttl_config()
_TTL_MINUTE: int = _TTL_CFG['cache_ttl_minute']
_TTL_DAY: int = _TTL_CFG['cache_ttl_day']


def _hash_object(obj: Any) -> str:
    """对任意对象做确定性 md5 哈希，生成 32 位十六进制字符串。"""
    if obj is None:
        return '0' * 32
    try:
        serialized = json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False)
    except Exception:
        try:
            serialized = repr(obj)
        except Exception:
            serialized = str(obj)
    return hashlib.md5(serialized.encode('utf-8', errors='replace')).hexdigest()


class FormulaCache:
    """公式结果 L1 进程内缓存。

    使用简单字典 + ``time.time()`` 实现，无外部依赖。
    """

    def __init__(self):
        self._cache: Dict[Any, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _period_from_key(key: str) -> Optional[str]:
        """从字符串缓存键中解析 period 字段。

        键格式：
        - 含分钟标签：``{symbol}:{minute}:{period}:{formula_hash}:{args_hash}``
        - 不含分钟标签：``{symbol}:{period}:{formula_hash}:{args_hash}``
        """
        if not isinstance(key, str):
            return None
        parts = key.split(':')
        if len(parts) < 3:
            return None
        # 第二段为数字说明存在 minute 标签，period 位于第三段
        if parts[1].isdigit():
            return parts[2] if parts[2] in _ALL_PERIODS else None
        return parts[1] if parts[1] in _ALL_PERIODS else None

    def make_key(
        self,
        symbol: str,
        period: str,
        formula_hash: str,
        args_hash: Optional[str] = None,
        minute: Optional[int] = None,
    ) -> str:
        """生成确定性缓存键。

        新键格式：
        - 含分钟标签：``{symbol}:{minute}:{period}:{formula_hash}:{args_hash}``
        - 不含分钟标签：``{symbol}:{period}:{formula_hash}:{args_hash}``

        为兼容历史调用，也支持旧签名 ``make_key(formula, symbol, period, context)``，
        此时会对 ``formula`` 与 ``context`` 分别哈希后生成新格式键。
        """
        # 兼容旧调用：make_key(formula, symbol, period, context)
        if isinstance(args_hash, dict) and formula_hash in _ALL_PERIODS:
            legacy_formula = symbol
            legacy_symbol = period
            legacy_period = formula_hash
            legacy_context = args_hash
            formula_hash = _hash_object(legacy_formula)
            args_hash = _hash_object(legacy_context)
            symbol = legacy_symbol
            period = legacy_period

        fh = formula_hash if formula_hash is not None else ''
        ah = args_hash if args_hash is not None else ''
        if minute is not None:
            return f"{symbol}:{minute}:{period}:{fh}:{ah}"
        return f"{symbol}:{period}:{fh}:{ah}"

    def get(self, key: Any) -> Any:
        """读取缓存值；不存在或已过期返回 ``None``，并自动驱逐过期条目。"""
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if time.time() > entry['ts'] + entry['ttl']:
                del self._cache[key]
                return None
            return entry['value']

    def set(self, key: Any, value: Any, ttl: Optional[int] = None) -> None:
        """写入缓存。

        - ``ttl=None`` 时根据键中的 period 自动选择 TTL。
        - ``period=='tick'`` 或 ``ttl <= 0`` 时不缓存。
        """
        if ttl is None:
            period = self._period_from_key(key)
            if period == 'tick':
                return
            ttl = _TTL_MINUTE if period in _MINUTE_PERIODS else _TTL_DAY
        if ttl <= 0:
            return
        with self._lock:
            self._cache[key] = {'value': value, 'ts': time.time(), 'ttl': ttl}

    def clear_all(self) -> None:
        """清空全部缓存。"""
        with self._lock:
            self._cache.clear()

    def invalidate_daily(self) -> None:
        """日终失效：清空全部缓存，禁止跨交易日复用。"""
        self.clear_all()

    def invalidate_on_minute_close(self, symbol: str, minute: int) -> int:
        """分钟闭合时，失效指定标的在该分钟下的分钟级缓存。

        会删除以下键：
        - 以 ``{symbol}:{minute}:`` 开头的键；
        - 包含 ``:{minute}:`` 分钟标签的键；
        - 旧格式键中 symbol 匹配且 period 属于分钟周期的键。

        Returns:
            被失效的条目数。
        """
        prefix = f"{symbol}:{minute}:"
        tag = f":{minute}:"
        removed = 0
        with self._lock:
            to_remove = []
            for key in list(self._cache.keys()):
                if isinstance(key, str):
                    if key.startswith(prefix) or tag in key:
                        to_remove.append(key)
                        continue
                    parts = key.split(':')
                    if parts and parts[0] == symbol:
                        period = parts[1] if len(parts) > 1 else None
                        if period in _MINUTE_PERIODS:
                            to_remove.append(key)
                elif isinstance(key, tuple) and len(key) >= 3:
                    if key[1] == symbol and key[2] in _MINUTE_PERIODS:
                        to_remove.append(key)
            for key in to_remove:
                if self._cache.pop(key, None) is not None:
                    removed += 1
        return removed

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)

    def __contains__(self, key: Any) -> bool:
        return self.get(key) is not None
