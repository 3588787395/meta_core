"""HQChartPy2 C++ 公式引擎包（项目根目录绝对可见）。

放置位置：``<project_root>/HQChartPy2/``，与 ``vendor/`` 平级。
项目根目录在 Python 启动时（``python scripts/verify_tools.py``）位于 ``sys.path[0]``，
因此 ``from HQChartPy2 import ...`` 可被任何子模块直接导入，无需修改 ``sys.path``。

依赖文件（与本 ``__init__.py`` 同目录）：
- ``HQChartPy2.pyd`` —— C++ 扩展（CPython 3.13 ABI，对应 ``python313.dll``）
- ``libcrypto-1_1-x64.dll`` / ``libssl-1_1-x64.dll`` —— OpenSSL 1.1 运行时依赖

加载策略（Windows DLL 搜索路径限制）：
- Python 3.8+ 默认不再搜索 PATH 中的 DLL，必须显式注册 DLL 目录。
- 使用 ``os.add_dll_directory(_SELF_DIR)`` 将本包目录加入 DLL 搜索路径，
  使 ``HQChartPy2.pyd`` 能正确加载同目录的 OpenSSL DLL。
"""
import os
import sys
from os.path import dirname, abspath

_SELF_DIR = abspath(dirname(__file__))

# 显式注册 DLL 搜索目录（Python 3.8+ Windows 必需）
if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
    try:
        os.add_dll_directory(_SELF_DIR)
    except (OSError, FileNotFoundError):
        pass

# 兜底：将本目录加入 sys.path，确保 import 机制可定位子模块
if _SELF_DIR not in sys.path:
    sys.path.insert(0, _SELF_DIR)

try:
    from .HQChartPy2 import (
        GetAuthorizeInfo,
        GetVersion,
        LoadAuthorizeInfo,
        Run,
        SetLog,
    )
except ImportError:
    import importlib
    _mod = importlib.import_module("HQChartPy2")
    GetAuthorizeInfo = getattr(_mod, "GetAuthorizeInfo", None)
    GetVersion = getattr(_mod, "GetVersion", None)
    LoadAuthorizeInfo = getattr(_mod, "LoadAuthorizeInfo", None)
    Run = getattr(_mod, "Run", None)
    SetLog = getattr(_mod, "SetLog", None)

__all__ = ["GetAuthorizeInfo", "GetVersion", "LoadAuthorizeInfo", "Run", "SetLog"]
