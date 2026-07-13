from __future__ import annotations

import json
import os
from typing import Dict, Any, Optional


_DATASETS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'datasets',
)
_CACHE: Dict[str, dict] = {}


class DatasetMissingError(Exception):
    """Raised when a requested dataset file is not found or cannot be loaded."""


def _load(name: str) -> dict:
    if name in _CACHE:
        return _CACHE[name]
    path = os.path.join(_DATASETS_DIR, f'{name}.json')
    if not os.path.isfile(path):
        raise DatasetMissingError(f"Dataset not found: {path}")
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        raise DatasetMissingError(f"Failed to load dataset {name}: {e}") from e
    _CACHE[name] = data
    return data


def load_klines(name: str = 'market_klines_1min') -> dict:
    return _load(name)


def load_capital_flows(name: str = 'capital_flows') -> dict:
    return _load(name)


def load_financials(name: str = 'financial_snapshots') -> dict:
    return _load(name)


def load_sectors(name: str = 'sector_constituents') -> dict:
    return _load(name)


def load_abnormal(name: str = 'abnormal_klines') -> dict:
    return _load(name)


def load_replay_schedule(name: str = 'replay_schedules') -> dict:
    return _load(name)


def get_by_tick(dataset: dict, date: str, tick_idx: int) -> Any:
    if not isinstance(dataset, dict):
        raise DatasetMissingError(f"Dataset is not a dict: {type(dataset)}")
    if 'data' in dataset and isinstance(dataset['data'], dict):
        data = dataset['data']
    elif 'bars' in dataset and isinstance(dataset['bars'], dict):
        data = dataset['bars']
    else:
        data = dataset
    if date not in data:
        raise DatasetMissingError(f"Date {date} not found in dataset")
    bars = data[date]
    if not isinstance(bars, list):
        raise DatasetMissingError(f"Data for date {date} is not a list")
    if tick_idx < 0 or tick_idx >= len(bars):
        raise DatasetMissingError(
            f"Tick index {tick_idx} out of range for date {date} (len={len(bars)})"
        )
    return bars[tick_idx]
