"""市场代码工具单一真相源：前缀/后缀配置加载、股票代码提取与归一化。

I36：消除 engine.py 与 _compat.py 的 5 项重复定义（_load_market_cfg、
_MARKET_PREFIXES、_MARKET_SUFFIXES、_stock_code、_normalize_stock_code），
统一 edge_executor.py 的 _stock_code 语义（label fallback + str() wrap）。

表驱动：_MARKET_PREFIXES/_MARKET_SUFFIXES 由 data_config.json 配置表决定，
归一化逻辑通过遍历前缀/后缀表实现，无硬编码 if/elif 分派。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CFG = Path(__file__).parent.parent / "config"


def _load_market_cfg():
    """从 data_config.json 加载市场代码前缀/后缀配置（fail-fast）。"""
    path = _CFG / "data_config.json"
    try:
        _dc = json.load(open(path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as ex:
        raise RuntimeError(
            f"无法加载配置表 {path}: {ex}（fail-fast：禁止静默回退硬编码值）"
        ) from ex
    return tuple(_dc.get("market_code_prefixes", ["SH", "SZ", "BJ"])), tuple(_dc.get("market_code_suffixes", [".SH", ".SZ", ".BJ"]))


_MARKET_PREFIXES, _MARKET_SUFFIXES = _load_market_cfg()


def _stock_code(s: Any) -> str:
    """从股票对象提取代码：dict 取 code（fallback label），其余 str()。

    I36 统一语义：合并 engine.py 的 label fallback 与 edge_executor.py 的
    str() wrap，消除两套语义分歧。dict 无 code/label 时返回 ''。
    """
    if isinstance(s, dict):
        return str(s.get('code', s.get('label', '')))
    return str(s)


def _normalize_stock_code(code: Any) -> str:
    """归一化股票代码：去除市场前缀(SH/SZ/BJ)和后缀(.SH/.SZ/.BJ)，返回纯数字代码。"""
    if not code or not isinstance(code, str):
        return str(code) if code is not None else ''
    c = code.strip()
    for prefix in _MARKET_PREFIXES:
        if c.upper().startswith(prefix) and len(c) > len(prefix) and c[len(prefix)].isdigit():
            c = c[len(prefix):]
            break
    for suffix in _MARKET_SUFFIXES:
        if c.upper().endswith(suffix) and len(c) > len(suffix):
            c = c[:-len(suffix)]
            break
    return c
