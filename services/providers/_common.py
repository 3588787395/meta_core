"""
数据源提供者公共工具模块。

合并自 _mock_data.py、_utils.py、_cache.py。

注意：_MOCK_MARKET_STOCKS / _MOCK_STOCK_NAMES 及其构建辅助已移至 mock_provider.py，
仅允许 MockProvider 使用（Task 3: 移除 Provider 内部 mock 数据回退）。

为兼容历史调用方（如 tq_adapter.py 的未使用 import），本模块通过 __getattr__
惰性 re-export 这两个符号；新代码应直接从 mock_provider.py 导入。
"""

# ===========================================================================
# _utils.py
# ===========================================================================

import base64
import re
import struct
from typing import Dict

PERIOD_MAP = {
    '分笔': 0,
    '1分': 1,
    '5分': 2,
    '15分': 3,
    '30分': 4,
    '60分': 5,
    '日': 6,
    '周': 7,
    '月': 8,
    '1m': 1,
    '5m': 2,
    '15m': 3,
    '30m': 4,
    '60m': 5,
    '1d': 6,
    '1w': 7,
    '1mon': 8,
    'tick': 0,
}

SORTTYPE_MAP: Dict[str, int] = {}


def _extract_text_segments(raw_bytes):
    """提取二进制中所有 ASCII 文本段。"""
    segments = []
    i = 0
    while i < len(raw_bytes):
        if 0x20 <= raw_bytes[i] <= 0x7E:
            start = i
            while i < len(raw_bytes) and 0x20 <= raw_bytes[i] <= 0x7E:
                i += 1
            segments.append({
                'start': start, 'end': i, 'len': i - start,
                'text': raw_bytes[start:i].decode('ascii', errors='replace'),
            })
        else:
            i += 1
    return segments


def _is_valid_formula(text):
    """验证文本是否像有效的 DZH 公式。"""
    if not text or len(text) < 3:
        return False
    illegal_chars = set(r'\`~@#$%^&?{|}=')
    for c in text:
        if c in illegal_chars:
            return False
    alpha_count = sum(1 for c in text if c.isalpha())
    digit_count = sum(1 for c in text if c.isdigit())
    total_len = len(text)
    if alpha_count < 2 and alpha_count + digit_count < 3:
        return False
    alpha_digit_ratio = (alpha_count + digit_count) / total_len if total_len > 0 else 0
    if alpha_digit_ratio < 0.4:
        return False
    valid_starters = [
        r'^[A-Z][A-Za-z0-9]*\(',
        r'^[a-z]{2,3}[\(\)\[\]\s\d]',
        r'^and\s',
        r'^or\s',
        r'^not\(',
        r'^[A-Z][\s]*[><=]',
        r'^[A-Z][\s]*\(',
        r'^[a-z][\s]*[><=]',
        r'^[a-z][\s]*\(',
        r'^[A-Za-z]{2,3}\s+[A-Za-z]',
    ]
    if not any(re.match(p, text) for p in valid_starters):
        return False
    has_paren = '(' in text or ')' in text
    has_comparison = any(op in text for op in ('>', '<', '='))
    has_comma = ',' in text
    if len(text) > 5:
        if not (has_paren or has_comparison or has_comma):
            return False
    if len(text) <= 5:
        if not (has_paren or has_comparison):
            return False
    if re.match(r'^[a-z]{3,5}$', text):
        return False
    return True


def _extract_formula_from_binary(raw_bytes):
    """从 DZH 二进制 indi 数据中提取公式文本。

    策略：从末尾查找 ;\\0 模式定位公式起始点，扩展提取完整文本段。
    降级：传统 ASCII 文本段提取。
    """
    if not raw_bytes:
        return ""
    formula_text = ""
    tail = raw_bytes[-64:] if len(raw_bytes) > 64 else raw_bytes
    for end_pos in range(len(tail) - 1, -1, -1):
        if tail[end_pos] == 0x3B:  # ';'
            null_terminated = (end_pos + 1 < len(tail) and tail[end_pos + 1] == 0x00)
            crlf_terminated = (end_pos + 2 < len(tail) and tail[end_pos:end_pos+3] == b';\r\n')
            if null_terminated or crlf_terminated:
                seg_end = end_pos
                seg_start = seg_end
                while seg_start > 0 and (0x20 <= tail[seg_start - 1] <= 0x7E or 0x81 <= tail[seg_start - 1] <= 0xFE):
                    seg_start -= 1
                candidate = tail[seg_start:seg_end + 1].decode('gbk', errors='replace')
                candidate = re.sub(r'^[^a-zA-Z0-9_\(]+', '', candidate)
                if not _is_valid_formula(candidate):
                    for strip_n in range(1, min(4, len(candidate))):
                        test = candidate[strip_n:]
                        if test and _is_valid_formula(test):
                            removed = candidate[:strip_n]
                            if len(removed) <= 2 and (not removed.isalpha() or len(removed) == 1):
                                candidate = test
                                break
                if candidate and _is_valid_formula(candidate):
                    formula_text = candidate
                    break
    if not formula_text:
        segments = _extract_text_segments(raw_bytes)
        for seg in reversed(segments):
            if seg['len'] >= 4 and seg['text'].rstrip().endswith(';'):
                candidate = seg['text'].rstrip('; \r\n\t\0')
                if candidate and _is_valid_formula(candidate):
                    formula_text = candidate
                    break
    if not formula_text:
        segments = _extract_text_segments(raw_bytes)
        for seg in reversed(segments):
            if seg['len'] >= 5 and seg['end'] >= len(raw_bytes) * 0.7:
                candidate = seg['text'].rstrip('; \r\n\t\0')
                if candidate and _is_valid_formula(candidate):
                    formula_text = candidate
                    break
        if not formula_text and segments:
            longest = max(segments, key=lambda s: s['len'])
            if longest['len'] >= 3:
                candidate = longest['text'].rstrip('; \r\n\t\0')
                if candidate and _is_valid_formula(candidate):
                    formula_text = candidate
    return formula_text


def decode_formula(indi_b64: str, ency: int = 0) -> str:
    """解码 DZH base64 编码的公式文本。

    支持 ency XOR 解密与 GBK 编码：先 XOR 解密（若 ency != 0），
    再从二进制中提取公式文本，最后 GBK 解码。
    降级：_extract_formula_from_binary / UTF-8 文本解码。
    """
    if not indi_b64 or indi_b64 == "0;":
        return ''
    try:
        raw = base64.b64decode(indi_b64)
    except Exception:
        return ''
    if not raw:
        return ''

    # XOR 解密
    if ency != 0:
        ency_bytes = struct.pack('<q', ency)
        raw = bytes(raw[i] ^ ency_bytes[i % 8] for i in range(len(raw)))

    # 查找终止符
    term_pos = -1
    target = b';\x00'
    pos = raw.rfind(target)
    if pos >= 0:
        term_pos = pos
    else:
        for i in range(len(raw) - 1, -1, -1):
            if raw[i] == 0x3B:
                if i + 2 < len(raw) and raw[i + 1:i + 3] == b'\r\n':
                    term_pos = i
                    break
                if i + 1 < len(raw) and raw[i + 1] == 0x0A:
                    term_pos = i
                    break
        if term_pos < 0:
            formula = _extract_formula_from_binary(raw)
            if formula:
                return formula
            try:
                return raw.decode('gbk', errors='replace')
            except Exception:
                try:
                    return raw.decode('utf-8', errors='replace')
                except Exception:
                    return ''

    # 从终止符向前搜索连续文本字节
    text_start = term_pos
    while text_start > 0:
        b = raw[text_start - 1]
        if 0x20 <= b <= 0x7E:
            text_start -= 1
        elif 0x81 <= b <= 0xFE:
            text_start -= 1
        else:
            break

    if term_pos - text_start < 2:
        formula = _extract_formula_from_binary(raw)
        if formula:
            return formula
        return ''

    formula_bytes = raw[text_start:term_pos + 1]
    try:
        formula = formula_bytes.decode('gbk')
    except Exception:
        formula = formula_bytes.decode('gbk', errors='replace')

    clean = re.sub(r'^[^a-zA-Z0-9_\(\u4e00-\u9fff]+', '', formula)
    return clean


def map_period(cycle: str) -> int:
    return PERIOD_MAP.get(cycle, 6)


def decode_sorttype(sorttype: str) -> int:
    return SORTTYPE_MAP.get(sorttype, 0)


def normalize_code(code: str) -> str:
    if not code:
        return code
    code = code.strip()
    if '.' in code:
        return code.upper()
    if code[:2].upper() in ('SH', 'SZ', 'BJ'):
        return code[2:] + '.' + code[:2].upper()
    return code


def to_dzh_code(code: str) -> str:
    if not code:
        return code
    code = code.strip()
    if '.' in code:
        parts = code.split('.')
        return parts[1].upper() + parts[0]
    return code.upper()


def _format_timestamp(ts):
    if not ts or ts <= 0:
        return '-'
    try:
        from datetime import datetime
        return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')
    except Exception:
        return '-'


def _format_hold_days(hold_sec):
    if not hold_sec or hold_sec <= 0:
        return '-'
    days = hold_sec / 86400
    if days >= 1:
        return f'{int(days)}天'
    hours = hold_sec / 3600
    if hours >= 1:
        return f'{int(hours)}时'
    return f'{int(hold_sec / 60)}分'


def _norm_period(period: str) -> str:
    _map = {'1min': '1m', '5min': '5m', '15min': '15m', '30min': '30m', '60min': '60m', 'day': '1d'}
    return _map.get(period, period)


# ===========================================================================
# _cache.py
# ===========================================================================

from typing import Dict, List, Optional


class KLineDataCache:
    def __init__(self, max_size: int = 500):
        self._cache: Dict[str, List[Dict]] = {}
        self._max_size = max_size

    def _make_key(self, code: str, period: str) -> str:
        return f"{code}|{period}"

    def get(self, code: str, period: str) -> Optional[List[Dict]]:
        key = self._make_key(code, period)
        return self._cache.get(key)

    def put(self, code: str, period: str, bars: List[Dict]):
        if len(self._cache) >= self._max_size:
            self._cache.pop(next(iter(self._cache)))
        key = self._make_key(code, period)
        self._cache[key] = bars

    def has(self, code: str, period: str) -> bool:
        return self._make_key(code, period) in self._cache

    def clear(self):
        self._cache.clear()


# ===========================================================================
# 向后兼容：惰性 re-export 已移至 mock_provider.py 的 mock 数据符号
# ===========================================================================

# 历史调用方（如 tq_adapter.py）可能仍 `from ._common import _MOCK_*`。
# 为避免破坏这些调用方（且避免与 mock_provider.py 形成循环导入），
# 使用模块级 __getattr__ (PEP 562) 在首次访问时从 mock_provider 惰性加载。
# 新代码应直接从 mock_provider.py 导入。

_MOCK_REEXPORTS = ('_MOCK_MARKET_STOCKS', '_MOCK_STOCK_NAMES')


def __getattr__(name):
    if name in _MOCK_REEXPORTS:
        from . import mock_provider as _mp
        return getattr(_mp, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(globals().keys() + list(_MOCK_REEXPORTS))
