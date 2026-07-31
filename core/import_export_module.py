"""ImportExport 模块：DZH XML / TDX XML / JSON 三格式导入导出。"""
from __future__ import annotations

import importlib
import logging
import os
import tempfile
from typing import Any, Callable, Dict, Optional, Tuple

from .event_bus import (
    _event_handler,
    EventBus,
    ExportCompleted,
    ImportStarted,
    PoolLoaded,
)
from .domain import _hms_to_seconds

logger = logging.getLogger(__name__)


# 表驱动：导入/导出 converter 统一入口（Task 7 / 变更 C + Task 3 / 变更 P3）

_FORMAT_LOG_LABELS: Dict[str, str] = {"dzh": "DZH XML", "tdx": "TDX XML", "json": "JSON"}


def _as_xml_path(path_or_data):
    """path_or_data 为 bytes/str 内容时落临时文件，返回 (路径, 临时路径或 None)。"""
    if isinstance(path_or_data, str) and os.path.isfile(path_or_data):
        return path_or_data, None
    if isinstance(path_or_data, (bytes, bytearray, str)):
        data = path_or_data.encode("utf-8") if isinstance(path_or_data, str) else bytes(path_or_data)
        fd, tmp_path = tempfile.mkstemp(suffix=".xml")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return tmp_path, tmp_path
    return path_or_data, None


def _adapter_import_dzh(path_or_data, config, fn, name=None):
    """parse_dzh_xml 接受 bytes/str 内容；为文件路径时先读取。"""
    if isinstance(path_or_data, str) and os.path.isfile(path_or_data):
        content = open(path_or_data, "rb").read()
        fname = name or path_or_data
    else:
        content = path_or_data
        fname = name or "upload.xml"
    return fn(content, filename=fname)


def _adapter_import_tdx(path_or_data, config, fn, name=None):
    """parse_tdx_xml 接受文件路径；path_or_data 为内容时落临时文件。返回 TdxPoolMetaModel。"""
    path, tmp_path = _as_xml_path(path_or_data)
    try:
        return fn(path)
    finally:
        if tmp_path:
            os.unlink(tmp_path)


def _adapter_export_dzh(path, config, fn, name=None):
    """export_dzh_xml(config) → bytes；path 非空时同时写文件。"""
    xml_bytes = fn(config)
    if path:
        with open(path, "wb") as f:
            f.write(xml_bytes)
    return xml_bytes


# (fmt, direction) -> (module_path, func_name, call_adapter)
# adapter 签名: (path_or_data, config, converter, name=None)
_CONVERTER_REGISTRY: Dict[Tuple[str, str], Tuple[str, Any, Callable]] = {
    ("dzh", "import"): ("..converters", "parse_dzh_xml", _adapter_import_dzh),
    ("tdx", "import"): ("..converters", "parse_tdx_xml", _adapter_import_tdx),
    ("json", "import"): ("..converters", "import_pool_from_json",
        lambda p, c, fn, name=None: fn(file_path=p)),
    ("dzh", "export"): ("..converters", "export_dzh_xml", _adapter_export_dzh),
    ("tdx", "export"): ("..converters", "_build_tdx_xml",
        lambda p, c, fn, name=None: fn(c, p)),
    ("json", "export"): ("..converters", "export_pool_to_json",
        lambda p, c, fn, name=None: fn(c, file_path=p)),
}

# G1: fmt → 转换器单例属性名（_to_frontend 钩子分派；None 表示该格式无转换器实例）。
# 与 _CONVERTER_REGISTRY 解耦，保持 registry 条目为 (module_path, func_name, adapter) 三元组。
_FORMAT_CONVERTER_ATTR: Dict[str, Optional[str]] = {
    "dzh": "_DZH_CONVERTER",
    "tdx": "_TDX_CONVERTER",
    "json": None,
}


def _call_converter(
    path_or_data, fmt=None, direction="import", config=None, name=None
) -> Any:
    """统一 converter 入口：查表 → 延迟 import → 调 adapter → 统一异常+日志。"""
    try:
        mod = importlib.import_module("..converters", __package__)
    except (ImportError, TypeError, ValueError):
        mod = importlib.import_module("converters")
    auto = False
    if direction == "import" and fmt is None:
        auto = True
        fmt = "tdx" if mod.is_tdx_format(path_or_data) else "dzh"
    entry = _CONVERTER_REGISTRY.get((fmt, direction))
    if entry is None:
        return {} if direction == "import" else None
    module_path, func_name, adapter = entry
    label = _FORMAT_LOG_LABELS.get(fmt, fmt.upper())
    action = "解析" if direction == "import" else "导出"
    try:
        converters = (tuple(getattr(mod, n) for n in func_name)
                      if isinstance(func_name, tuple) else getattr(mod, func_name))
        result = adapter(path_or_data, config, converters, name=name)
        # G1: 统一 _to_frontend 钩子（默认透传；TdxPoolConverter 覆盖为前端 dict），
        # 消除 TDX 专属格式分支。仅在自动检测导入时应用，保留显式 import 返回原始模型的契约。
        conv_attr = _FORMAT_CONVERTER_ATTR.get(fmt)
        if auto and conv_attr:
            converter = getattr(mod, conv_attr, None)
            if converter is not None:
                pool_name = (name.rsplit(".", 1)[0] if name and "." in name else (name or "tdx_pool"))
                result = converter._to_frontend(result, pool_name)
        return result
    except Exception as ex:
        logger.warning("%s %s失败 path=%s: %s", label, action, path_or_data, ex, exc_info=True)
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
    """ImportExport 模块：DZH XML / TDX XML / JSON 三格式导入导出。仅与 EventBus 交互。"""

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
    @_event_handler("_on_export_completed")
    def _on_export_completed(self, event: ExportCompleted) -> None:
        """订阅 ExportCompleted 用于内部状态同步。"""
        self._export_count += 1

    # ------------------------------------------------------------------
    # 导入/导出方法（表驱动，由 API 模块直接调用，非事件订阅）
    # ------------------------------------------------------------------
    def import_pool(self, path: str, format: str) -> bool:
        """导入股票池文件（表驱动，支持 dzh / tdx / json 三种格式）。"""
        if (format, "import") not in _CONVERTER_REGISTRY:
            return False
        self._bus.publish(ImportStarted(format=format, path=path))
        try:
            pool_config = _call_converter(path, format, "import")
        except Exception:
            return False
        if pool_config:
            self._bus.publish(PoolLoaded(pool_config=pool_config, source_format=format))
            self._import_count += 1
            return True
        return False

    def export_pool(self, config: Dict[str, Any], path: str, format: str) -> str:
        """导出股票池文件（表驱动，支持 dzh / tdx / json 三种格式）。"""
        if (format, "export") not in _CONVERTER_REGISTRY:
            return ""
        _call_converter(path, format, "export", config=config)
        self._bus.publish(ExportCompleted(
            format=format, path=path,
            count=len(config.get("nodes", [])),
        ))
        return path


__all__ = ["ImportExportModule"]
