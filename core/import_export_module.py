"""ImportExport 模块：DZH XML / TDX XML / JSON 三格式导入导出。

按 ``unify-stockpool-oop-event-driven`` spec Task 12 实现。
``ImportExportModule`` 聚合 ``converters/dzh.py`` / ``converters/tdx.py`` /
``converters/json_xml.py`` 三模块，通过 HTTP 端点接收导入/导出请求（非事件），
发布 ``ImportStarted`` / ``PoolLoaded`` / ``ExportCompleted`` 事件。

向后兼容：原 3 个 converters 文件保持不变，本模块内部调用它们。
导入/导出请求由 API 模块直接调用本模块的公共方法（非事件驱动，因为是同步
请求/响应）；``PoolLoaded`` 事件触发 Execution 模块编译 + Database 模块持久化。
"""
from __future__ import annotations

import base64
import importlib
import logging
import os
import re
import struct
from typing import Any, Callable, Dict, Optional, Tuple

from .event_bus import (
    EventBus,
    ExportCompleted,
    ImportStarted,
    PoolLoaded,
)
from .domain import _hms_to_seconds

logger = logging.getLogger(__name__)


# ============================================================
# ImportExport 内部辅助函数（SubTask 27.8: 从 converters/_common.py 合并至此）
# 供 converters/dzh.py 与 converters/tdx.py 通过 ``from ..core.import_export_module
# import ...`` 复用，消除 converters 包内 _common.py 单独文件。
# ============================================================

def _safe_int(val, default=0):
    """安全转换字符串为整数，失败时返回默认值。"""
    if val is None or val == "":
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _safe_float(val, default=0.0):
    """安全转换字符串为浮点数，失败时返回默认值。"""
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


# ============================================================
# DZH 公式解码（Task 23.1: 从 services.providers._common 迁移至 converters 包内；
# SubTask 27.8: 再从 converters/_common.py 合并至本模块）
# ============================================================

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


def _decode_formula(indi_b64: str, ency: int = 0) -> str:
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


# ============================================================
# 表驱动：导入/导出 converter 统一入口（Task 7 / 变更 C）
# 合并 6 个同构 _parse_xxx / _serialize_xxx 为 _call_converter +
# _CONVERTER_REGISTRY 表（骨架相同：延迟 import → 调用 → 返回/写文件）。
# ============================================================

_FORMAT_LOG_LABELS: Dict[str, str] = {"dzh": "DZH XML", "tdx": "TDX XML", "json": "JSON"}


def _adapter_export_dzh(path: str, config: Dict[str, Any], fn: Callable) -> None:
    """export_dzh_xml(config) → 写文件（先序列化，失败不截断目标文件）。"""
    xml_bytes = fn(config)
    with open(path, "wb") as f:
        f.write(xml_bytes)


# (fmt, direction) -> (module_path, func_name, call_adapter)
# func_name 为字符串或元组（TDX 导入需 2 函数）；adapter 签名 (path, config, converter)
_CONVERTER_REGISTRY: Dict[Tuple[str, str], Tuple[str, Any, Callable]] = {
    ("dzh", "import"): ("..converters", "parse_dzh_xml",
        lambda p, c, fn: fn(open(p, "rb").read(), filename=p)),
    ("tdx", "import"): ("..converters", ("parse_tdx_xml", "convert_tdx_to_config"),
        lambda p, c, fns: {**fns[1](fns[0](p)), "name": os.path.splitext(os.path.basename(p))[0]}),
    ("json", "import"): ("..converters", "import_pool_from_json",
        lambda p, c, fn: fn(file_path=p)),
    ("dzh", "export"): ("..converters", "export_dzh_xml", _adapter_export_dzh),
    ("tdx", "export"): ("..converters", "_build_tdx_xml",
        lambda p, c, fn: fn(c, p)),
    ("json", "export"): ("..converters", "export_pool_to_json",
        lambda p, c, fn: fn(c, file_path=p)),
}


def _call_converter(
    path: str, fmt: str, direction: str, config: Optional[Dict[str, Any]] = None
) -> Any:
    """统一 converter 入口：查表 → 延迟 import → 调 adapter → 统一异常+日志。

    import 失败返回 ``{}``，export 失败抛出（与原 6 函数语义一致）。
    """
    entry = _CONVERTER_REGISTRY.get((fmt, direction))
    if entry is None:
        return {} if direction == "import" else None
    module_path, func_name, adapter = entry
    label = _FORMAT_LOG_LABELS.get(fmt, fmt.upper())
    action = "解析" if direction == "import" else "导出"
    try:
        try:
            mod = importlib.import_module(module_path, __package__)
        except (ImportError, TypeError, ValueError):
            mod = importlib.import_module(module_path.lstrip("."))
        converters = (tuple(getattr(mod, n) for n in func_name)
                      if isinstance(func_name, tuple) else getattr(mod, func_name))
        return adapter(path, config, converters)
    except Exception as ex:
        logger.warning("%s %s失败 path=%s: %s", label, action, path, ex, exc_info=True)
        if direction == "import":
            return {}
        raise


# 派生规则表（保留 (callable, fmt) 二元组结构供外部检视）
_IMPORT_RULES: Dict[str, Tuple[Callable, str]] = {
    fmt: ((lambda p, f=fmt: _call_converter(p, f, "import")), fmt)
    for (fmt, d) in _CONVERTER_REGISTRY if d == "import"
}
_EXPORT_RULES: Dict[str, Tuple[Callable, str]] = {
    fmt: ((lambda c, p, f=fmt: _call_converter(p, f, "export", config=c)), fmt)
    for (fmt, d) in _CONVERTER_REGISTRY if d == "export"
}


class ImportExportModule:
    """ImportExport 模块：DZH XML / TDX XML / JSON 三格式导入导出。仅与 EventBus 交互。

    通过 HTTP 端点接收导入/导出请求（非事件），
    发布 ImportStarted/PoolLoaded/ExportCompleted 事件。

    导入流程（以 DZH 为例）::
        ImportStarted → 调用 converters.dzh.parse_dzh_xml → PoolLoaded
    导出流程（以 DZH 为例）::
        调用 converters.dzh.export_dzh_xml → 写文件 → ExportCompleted
    """

    def __init__(self, bus: EventBus, config: Optional[Dict[str, Any]] = None) -> None:
        self._bus = bus
        self._config = config or {}
        # 内部状态：导入/导出计数（仅用于诊断/状态查询）
        self._import_count: int = 0
        self._export_count: int = 0
        # 注册事件订阅（仅订阅 ExportCompleted 用于内部状态同步，不订阅导入请求）
        self._bus.subscribe(ExportCompleted, self._on_export_completed)

    # ------------------------------------------------------------------
    # 内部事件 handler
    # ------------------------------------------------------------------
    def _on_export_completed(self, event: ExportCompleted) -> None:
        """订阅 ExportCompleted 用于内部状态同步。"""
        self._export_count += 1

    # ------------------------------------------------------------------
    # 导入/导出方法（表驱动，由 API 模块直接调用，非事件订阅）
    # ------------------------------------------------------------------
    def import_pool(self, path: str, format: str) -> bool:
        """导入股票池文件（表驱动，支持 dzh / tdx / json 三种格式）。

        流程：查 ``_CONVERTER_REGISTRY`` 表 → 发布 ``ImportStarted`` →
        调用 ``_call_converter`` → 成功时发布 ``PoolLoaded`` 并递增计数。

        Args:
            path: 文件路径。
            format: 格式名（``"dzh"`` / ``"tdx"`` / ``"json"``）。

        Returns:
            成功导入返回 True，格式不支持或解析失败（空结果）返回 False。
        """
        if (format, "import") not in _CONVERTER_REGISTRY:
            return False
        self._bus.publish(ImportStarted(format=format, path=path))
        pool_config = _call_converter(path, format, "import")
        if pool_config:
            self._bus.publish(PoolLoaded(pool_config=pool_config, source_format=format))
            self._import_count += 1
            return True
        return False

    def export_pool(self, config: Dict[str, Any], path: str, format: str) -> str:
        """导出股票池文件（表驱动，支持 dzh / tdx / json 三种格式）。

        流程：查 ``_CONVERTER_REGISTRY`` 表 → 调用 ``_call_converter`` 写文件 →
        发布 ``ExportCompleted``。

        Args:
            config: 统一 PoolConfig dict。
            path: 输出文件路径。
            format: 格式名（``"dzh"`` / ``"tdx"`` / ``"json"``）。

        Returns:
            输出文件路径；格式不支持时返回空字符串。

        Raises:
            Exception: 导出失败时由 ``_call_converter`` 透传抛出。
        """
        if (format, "export") not in _CONVERTER_REGISTRY:
            return ""
        _call_converter(path, format, "export", config=config)
        self._bus.publish(ExportCompleted(
            format=format, path=path,
            count=len(config.get("nodes", [])),
        ))
        return path


__all__ = ["ImportExportModule"]
