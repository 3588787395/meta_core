"""services/data.py - 数据服务统一入口（合并自 data_query / data_service / market_data_port）。

合并保留三个分节：
    # === DataQuery ===       — 本地 K 线只读查询（原 data_query.py）
    # === DataService ===     — 数据源契约与同步服务（原 data_service.py）
    # === MarketDataPort ===  — 公式计算层市场数据端口抽象（原 market_data_port.py）

向后兼容：services/__init__.py 通过 sys.modules 注册旧模块名，旧 import 路径继续可用。
"""
from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd

from .storage import Storage, IStorageQuery
from .providers import AkShareProvider, DataSourceError
from .providers import TqDllProvider

try:
    from ..core.event_bus import EventBus, ModeChanged, PoolLoaded, TickReceived
except ImportError:  # services 作为顶层包导入时回退到绝对导入
    from core.event_bus import EventBus, ModeChanged, PoolLoaded, TickReceived

# === DataQuery ===
logger = logging.getLogger(__name__)

_MINUTE_PERIODS = ("1m", "5m", "15m", "30m", "60m")
_DAILY_PERIODS = ("1d", "1wk", "1mon")

_PERIOD_ALIASES = {
    "1min": "1m",
    "5min": "5m",
    "15min": "15m",
    "30min": "30m",
    "60min": "60m",
    "1day": "1d",
    "1week": "1wk",
    "1month": "1mon",
}


def _normalize_period(period: str) -> str:
    """标准化周期名，支持 min/day/week/month 等别名。"""
    if not period:
        return period
    p = period.lower().strip()
    return _PERIOD_ALIASES.get(p, p)
_PRICE_COLS = ("open", "high", "low", "close")


class DataQuery:
    """统一 K 线数据查询入口。

    职责边界：
        - 只读取本地 parquet 与 ``Min1Aggregator`` 内存数据。
        - 不提供任何下载、网络回退或数据补齐能力。

    Args:
        minute_aggregator: 今日分钟线合成器实例，可为 ``None``。
        pipeline_cfg: 数据管道配置字典；未提供时读取 ``config/data_pipeline.json``。
        storage: ``Storage`` 实例，仅用于盘前校准等非热路径场景，可为 ``None``。
    """

    def __init__(
        self,
        minute_aggregator: Optional[Any] = None,
        pipeline_cfg: Optional[dict] = None,
        storage: Optional[Any] = None,
        bars_history_getter: Optional[Callable[[str, str], pd.DataFrame]] = None,
    ):
        self.minute_aggregator = minute_aggregator
        self.storage = storage
        self.bars_history_getter = bars_history_getter

        if pipeline_cfg is None:
            cfg_path = Path(__file__).parent.parent / "config" / "data" / "data_pipeline.json"
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    pipeline_cfg = json.load(f)
            except Exception as e:  # pragma: no cover
                logger.warning("读取 data_pipeline.json 失败: %s", e)
                pipeline_cfg = {}

        dq_cfg = pipeline_cfg.get("data_query", {}) if isinstance(pipeline_cfg, dict) else {}
        self.history_path = dq_cfg.get("history_path", "data/history")
        self.today_path = dq_cfg.get("today_path", "data/today")
        self.default_kline_count = int(dq_cfg.get("default_kline_count", 250))

        # 相对路径统一解析到项目根目录（meta_core 父目录）
        if not os.path.isabs(self.history_path):
            self.history_path = str(Path(__file__).parent.parent / self.history_path)
        if not os.path.isabs(self.today_path):
            self.today_path = str(Path(__file__).parent.parent / self.today_path)

    # ------------------------------------------------------------------
    # 公共查询接口
    # ------------------------------------------------------------------

    def get_kline_series(
        self,
        symbol: str,
        period: str = "1m",
        end_time: Optional[Any] = None,
        count: Optional[int] = None,
        include_unconfirmed: bool = False,
    ) -> pd.DataFrame:
        """获取某标的指定周期的 K 线序列。

        Args:
            symbol: 标的代码。
            period: 周期，支持 ``1m/5m/15m/30m/60m/1d/1wk/1mon``。
            end_time: 截止时间；分钟线为 ``HHMM`` 整数或 ``datetime``，
                日线及以上为 ``YYYYMMDD`` 整数或 ``date/datetime``。
            count: 返回的最大行数，``None`` 使用配置默认值。
            include_unconfirmed: 是否包含当前未闭合分钟（仅分钟线有效）。

        Returns:
            pd.DataFrame: 标准化 K 线数据。分钟线含 ``time`` 列，日线及以上含 ``date`` 列。
        """
        period = _normalize_period(period)
        if period not in _MINUTE_PERIODS and period not in _DAILY_PERIODS:
            raise ValueError(f"不支持的周期: {period}")

        if count is None:
            count = self.default_kline_count

        time_col = "time" if period in _MINUTE_PERIODS else "date"

        if self.bars_history_getter is not None:
            df = self.bars_history_getter(symbol, period)
            if df is None or df.empty:
                return self._ensure_columns(pd.DataFrame(), time_col)
            if time_col not in df.columns and "time" in df.columns:
                df = df.rename(columns={"time": time_col})
            df = df.drop_duplicates(subset=[time_col], keep="last")
            df = df.sort_values(time_col).reset_index(drop=True)
            if len(df) > count:
                df = df.iloc[-count:].reset_index(drop=True)
            return self._ensure_columns(df, time_col)

        df = self._load_history(symbol, period, time_col)

        if period in _MINUTE_PERIODS and self.minute_aggregator is not None:
            today_df = self._load_today(symbol, include_unconfirmed, period)
            if not today_df.empty:
                df = pd.concat([df, today_df], ignore_index=True)

        if df.empty:
            return self._ensure_columns(df, time_col)

        df = self._filter_end_time(df, time_col, end_time)
        df = df.drop_duplicates(subset=[time_col], keep="last")
        df = df.sort_values(time_col).reset_index(drop=True)

        if len(df) > count:
            df = df.iloc[-count:].reset_index(drop=True)

        return self._ensure_columns(df, time_col)

    def get_history_series(
        self,
        symbol: str,
        period: str,
        end_time: Optional[Any] = None,
        count: Optional[int] = None,
    ) -> pd.DataFrame:
        """``get_kline_series`` 的别名，仅读取历史 parquet（不拼接今日数据）。"""
        return self.get_kline_series(symbol, period, end_time, count, include_unconfirmed=False)

    # ------------------------------------------------------------------
    # 辅助工具
    # ------------------------------------------------------------------

    @staticmethod
    def apply_adjustment(df: pd.DataFrame, adj_factor: float) -> pd.DataFrame:
        """将 OHLC 价格列乘以复权因子（volume 不复权）。

        Args:
            df: K 线 DataFrame。
            adj_factor: 复权因子。

        Returns:
            pd.DataFrame: 应用复权后的新 DataFrame（不修改输入）。
        """
        if df.empty or adj_factor is None or adj_factor == 1.0:
            return df.copy()
        result = df.copy()
        for col in _PRICE_COLS:
            if col in result.columns:
                result[col] = result[col] * adj_factor
        return result

    @staticmethod
    def detect_gaps(df: pd.DataFrame) -> List[Any]:
        """检测 OHLCV 全部为 0 或 NaN 的缺口（停牌）行。

        Args:
            df: K 线 DataFrame，需包含 ``time`` 或 ``date`` 列及 OHLCV 列。

        Returns:
            List: 缺口对应的时间或日期值列表。
        """
        if df.empty:
            return []

        time_col = None
        for col in ("time", "date"):
            if col in df.columns:
                time_col = col
                break
        if time_col is None:
            return []

        cols = [c for c in _PRICE_COLS if c in df.columns]
        vol_col = "volume" if "volume" in df.columns else None
        mask = pd.Series(False, index=df.index)
        for col in cols:
            mask |= df[col].isna() | (df[col] == 0)
        if vol_col:
            mask &= df[vol_col].isna() | (df[vol_col] == 0)
        else:
            # 没有 volume 列时，仅判断价格列全为 0/NaN
            pass

        return df.loc[mask, time_col].tolist()

    # ------------------------------------------------------------------
    # 内部加载
    # ------------------------------------------------------------------

    def _load_history(self, symbol: str, period: str, time_col: str) -> pd.DataFrame:
        """从 parquet 加载历史 K 线。"""
        parquet_path = Path(self.history_path) / period / f"{symbol}.parquet"
        base_cols = [time_col, "open", "high", "low", "close", "volume"]

        if not parquet_path.exists():
            return pd.DataFrame(columns=base_cols)

        try:
            df = pd.read_parquet(parquet_path)
        except Exception as e:
            logger.warning("读取 parquet 失败 %s: %s", parquet_path, e)
            return pd.DataFrame(columns=base_cols)

        if df.empty:
            return pd.DataFrame(columns=base_cols)

        # 保留标准列，额外列（如 amount）原样保留
        keep = [c for c in base_cols if c in df.columns]
        extra = [c for c in df.columns if c not in base_cols]
        df = df[keep + extra].copy()

        if time_col in df.columns:
            df[time_col] = df[time_col].astype(int)

        return df

    def _load_today(
        self,
        symbol: str,
        include_unconfirmed: bool,
        target_period: str,
    ) -> pd.DataFrame:
        """从 Min1Aggregator 读取今日分钟线。"""
        df = self.minute_aggregator.get_today_series(symbol)
        if df.empty:
            return df

        if not include_unconfirmed and "confirmed" in df.columns:
            df = df[df["confirmed"] == True].copy()

        df = df.drop(columns=["confirmed"], errors="ignore")

        if "time" in df.columns:
            df["time"] = df["time"].astype(int)

        if target_period != "1m":
            df = self._resample_minute_bars(df, target_period)

        return df

    @staticmethod
    def _resample_minute_bars(df: pd.DataFrame, period: str) -> pd.DataFrame:
        """将 1 分钟线重采样为更高分钟周期。"""
        if df.empty or period == "1m":
            return df

        interval = int(period[:-1])
        if interval <= 1:
            return df

        minutes = (df["time"] // 100) * 60 + (df["time"] % 100)
        df = df.copy()
        df["_group"] = minutes // interval

        rows: List[Dict[str, Any]] = []
        for _, sub in df.groupby("_group", sort=True):
            sub = sub.sort_values("time")
            rows.append(
                {
                    "time": int(sub["time"].iloc[0]),
                    "open": float(sub["open"].iloc[0]),
                    "high": float(sub["high"].max()),
                    "low": float(sub["low"].min()),
                    "close": float(sub["close"].iloc[-1]),
                    "volume": int(sub["volume"].sum()),
                }
            )

        cols = ["time", "open", "high", "low", "close", "volume"]
        if "amount" in df.columns:
            cols.append("amount")

        return pd.DataFrame(rows, columns=cols)

    @staticmethod
    def _filter_end_time(
        df: pd.DataFrame,
        time_col: str,
        end_time: Optional[Any],
    ) -> pd.DataFrame:
        """按截止时间过滤。"""
        if end_time is None or df.empty:
            return df

        if time_col == "time":
            end_int = _to_int_time(end_time)
        else:
            end_int = _to_int_date(end_time)

        if end_int is None:
            return df

        return df[df[time_col] <= end_int].copy()

    @staticmethod
    def _ensure_columns(df: pd.DataFrame, time_col: str) -> pd.DataFrame:
        """确保返回 DataFrame 至少包含标准列。"""
        base_cols = [time_col, "open", "high", "low", "close", "volume"]
        if df.empty:
            return pd.DataFrame(columns=base_cols)

        ordered = [c for c in base_cols if c in df.columns]
        extra = [c for c in df.columns if c not in base_cols]
        return df[ordered + extra].copy()


# ------------------------------------------------------------------------------
# 盘前校准
# ------------------------------------------------------------------------------


def pre_market_calibration(
    storage: Optional[Any],
    data_query: DataQuery,
    symbols: List[str],
) -> Dict[str, Any]:
    """开盘前本地数据校准检查。

    本函数只读取本地 SQLite / parquet / JSON，不访问网络。

    Args:
        storage: ``Storage`` 实例，用于检查 stocks 表完整性；可为 ``None``。
        data_query: ``DataQuery`` 实例，用于读取本地最后交易日。
        symbols: 待校准的标的列表。

    Returns:
        dict: 校准报告，含 ``missing_symbols``、``abnormal_symbols``、
        ``last_trade_date``、``calibrated_count`` 等字段。
    """
    report: Dict[str, Any] = {
        "missing_symbols": [],
        "abnormal_symbols": [],
        "last_trade_date": None,
        "calibrated_count": 0,
        "trading_day_continuous": False,
    }

    if not symbols:
        return report

    # 1. stocks 表完整性检查
    if storage is not None:
        try:
            with storage._conn() as conn:
                placeholders = ",".join("?" * len(symbols))
                rows = conn.execute(
                    f"SELECT stock_code, status, delist_date FROM stocks WHERE stock_code IN ({placeholders})",
                    tuple(symbols),
                ).fetchall()
            found = {r["stock_code"]: r for r in rows}
            report["missing_symbols"] = [s for s in symbols if s not in found]
            report["abnormal_symbols"] = [
                s
                for s in symbols
                if s in found
                and (found[s]["status"] != "active" or found[s]["delist_date"] is not None)
            ]
        except Exception as e:
            logger.warning("stocks 表完整性检查失败: %s", e)

    # 2. 复权因子占位加载
    adj_dir = Path(data_query.history_path).parent / "adj_factors"
    for symbol in symbols:
        factor = _load_adj_factor(adj_dir, symbol)
        if factor is not None:
            report["calibrated_count"] += 1

    # 3. 本地最后交易日连续性检查
    try:
        df = data_query.get_kline_series(symbols[0], period="1d", count=1)
        if not df.empty and "date" in df.columns:
            report["last_trade_date"] = int(df["date"].iloc[-1])
    except Exception as e:
        logger.warning("读取本地最后交易日失败: %s", e)

    report["trading_day_continuous"] = _check_trading_day_continuity(report["last_trade_date"])
    return report


# ------------------------------------------------------------------------------
# 私有工具函数
# ------------------------------------------------------------------------------


def _to_int_time(end_time: Any) -> Optional[int]:
    """将截止时间统一转换为 HHMM 整数。"""
    if isinstance(end_time, int):
        return end_time
    if isinstance(end_time, datetime):
        return end_time.hour * 100 + end_time.minute
    if isinstance(end_time, str):
        try:
            return int(end_time)
        except ValueError:
            return None
    return None


def _to_int_date(end_time: Any) -> Optional[int]:
    """将截止时间统一转换为 YYYYMMDD 整数。"""
    if isinstance(end_time, int):
        return end_time
    if isinstance(end_time, (date, datetime)):
        return end_time.year * 10000 + end_time.month * 100 + end_time.day
    if isinstance(end_time, str):
        try:
            # 尝试直接解析 YYYYMMDD
            return int(end_time)
        except ValueError:
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
                try:
                    dt = datetime.strptime(end_time, fmt)
                    return dt.year * 10000 + dt.month * 100 + dt.day
                except ValueError:
                    continue
            return None
    return None


def _load_adj_factor(adj_dir: Path, symbol: str) -> Optional[float]:
    """从本地 JSON 读取最新复权因子占位。"""
    path = adj_dir / f"{symbol}.json"
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("读取复权因子失败 %s: %s", path, e)
        return None

    if isinstance(data, (int, float)):
        return float(data)
    if isinstance(data, dict):
        for key in ("factor", "adj_factor", "value", "latest"):
            if key in data:
                return float(data[key])
    if isinstance(data, list) and data:
        last = data[-1]
        if isinstance(last, dict):
            for key in ("factor", "adj_factor", "value"):
                if key in last:
                    return float(last[key])
        elif isinstance(last, (int, float)):
            return float(last)
    return None


def _check_trading_day_continuity(last_trade_date: Optional[int]) -> bool:
    """检查本地最后交易日与今日是否连续。"""
    if last_trade_date is None:
        return False
    try:
        last = datetime.strptime(str(last_trade_date), "%Y%m%d").date()
    except Exception:
        return False

    today = date.today()
    if last > today:
        return False

    # 盘前场景下，最后交易日应为上一个交易日；开盘后可能已包含今日
    expected_prev = today - timedelta(days=1)
    while expected_prev.weekday() >= 5:  # 跳过周末
        expected_prev -= timedelta(days=1)

    return last in (today, expected_prev)


# ------------------------------------------------------------------------------
# 向后兼容包装（旧代码/测试仍使用 DataQueryService / KLineProvider）
# ------------------------------------------------------------------------------


class DataQueryService:
    """[Deprecated] 统一 K 线查询服务，已由 ``DataQuery`` 替代。

    保留为向后兼容包装，内部委托给 ``DataQuery``；不直接访问 parquet 或
    ``TqAdapter``。新代码应直接使用 ``DataQuery``。
    """

    def __init__(
        self,
        storage: Optional[Any] = None,
        minute_aggregator: Optional[Any] = None,
        tq_adapter: Optional[Any] = None,
        config: Optional[dict] = None,
    ):
        self.storage = storage
        self.minute_aggregator = minute_aggregator
        self.tq_adapter = tq_adapter  # 保留字段以兼容外部引用，但不再使用
        self._data_query = DataQuery(
            minute_aggregator=minute_aggregator,
            pipeline_cfg=config,
            storage=storage,
        )

    async def get_kline_series(
        self,
        symbol: str,
        period: str = "1m",
        end_time: Optional[Any] = None,
        count: Optional[int] = None,
        include_unconfirmed: bool = False,
    ) -> pd.DataFrame:
        """[Deprecated] 委托给 ``DataQuery.get_kline_series()``。"""
        return self._data_query.get_kline_series(
            symbol,
            period=period,
            end_time=end_time,
            count=count,
            include_unconfirmed=include_unconfirmed,
        )

    async def _load_historical(
        self,
        symbol: str,
        period: str,
        end_time: Optional[Any],
        count: int,
    ) -> pd.DataFrame:
        """[Deprecated] 委托给 ``DataQuery.get_history_series()``。"""
        return self._data_query.get_history_series(
            symbol, period, end_time=end_time, count=count
        )


class KLineProvider:
    """[Deprecated] K 线组合层包装，内部委托给 ``DataQuery``。

    保留异步 ``get_kline_series`` 签名以兼容 ``KLineReplayEngine`` 等旧调用方。
    """

    def __init__(
        self,
        historical_repository: Any,
        realtime_feed: Any,
        default_count: int = 250,
    ):
        self.historical_repository = historical_repository
        self.realtime_feed = realtime_feed
        self.default_count = default_count

        history_path = getattr(historical_repository, "history_path", "data/history")
        self._data_query = DataQuery(
            minute_aggregator=getattr(realtime_feed, "minute_aggregator", None),
            pipeline_cfg={
                "data_query": {
                    "history_path": history_path,
                    "default_kline_count": default_count,
                }
            },
        )

    async def get_kline_series(
        self,
        symbol: str,
        period: str = "1m",
        end_time: Optional[Any] = None,
        count: Optional[int] = None,
        include_unconfirmed: bool = False,
    ) -> pd.DataFrame:
        """[Deprecated] 委托给 ``DataQuery.get_kline_series()``。"""
        if count is None:
            count = self.default_count
        return self._data_query.get_kline_series(
            symbol,
            period=period,
            end_time=end_time,
            count=count,
            include_unconfirmed=include_unconfirmed,
        )

# === DataService ===

# ═══════════════════════════════════════════════════════════════
# 新数据架构职责分离（重要）
# ═══════════════════════════════════════════════════════════════
#
# - Database = 纯持久化 (Storage)：只负责 SQLite 读写，不做业务判断。
# - Data download = DataSyncService：从外部数据源同步基础数据到本地数据库。
# - Data loading = DataQuery：本地只读查询 K 线、交易日等，不访问网络。
# - Real-time = Min1Aggregator：盘中分钟线实时合成，仅写入今日数据。
# - 禁止静默回退到 mock：mock 必须显式选择，数据源不可用直接抛错。
#
# ═══════════════════════════════════════════════════════════════
# 数据源契约（原 data_source_contract.py）
# ═══════════════════════════════════════════════════════════════

# 核心职责：
#   1. 加载 config/data_source_contract.json 配置
#   2. 提供 _probe() 方法探测数据源可用性
#   3. **禁止自动回退到 mock**：除用户显式 set_active_source('mock') 外，
#      真实数据源不可用时必须 raise，不静默切换到 mock。
#   4. 暴露给 api.py（合并自原 execution_api）在 /pools/{id}/run 启动前调用。
#
# 设计：
#   - DataSourceContract 负责读取契约配置，按 source_name 查找条目
#   - ProviderProbeRunner 负责实际执行 _probe() 调用，支持超时控制
#   - probe_or_raise() 统一入口：未就绪则按 on_unavailable 策略 raise / warn
#
# 禁止触碰 user/、sys/、Lib/ 等其他目录。

_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "data" / "data_source_contract.json"
)


# ═══════════════════════════════════════════════════════════════
# 异常类
# ═══════════════════════════════════════════════════════════════


class DataSourceContractError(Exception):
    """数据源契约违反基类。"""


class DataSourceUnavailableErrorContract(DataSourceContractError):
    """数据源不可用异常（契约层）。"""

    def __init__(self, source_name: str, message: str = ""):
        self.source_name = source_name
        self.message = message or f"数据源 {source_name} 不可用"
        super().__init__(self.message)


class DataSourceMockExplicitOnlyError(DataSourceContractError):
    """Mock 数据源必须显式选择才能使用。"""

    def __init__(self, source_name: str = "mock"):
        self.source_name = source_name
        self.message = (
            f"数据源 {source_name} 标记为 explicit_only，"
            "必须显式调用 set_active_source('mock') 才能使用，禁止自动回退"
        )
        super().__init__(self.message)


# ═══════════════════════════════════════════════════════════════
# 配置加载
# ═══════════════════════════════════════════════════════════════


def _load_contract_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """加载 data_source_contract.json。"""
    path = config_path or _CONFIG_PATH
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning("data_source_contract.json 未找到: %s", path)
        return {"version": 1, "sources": {}, "default_chain": [], "global_policy": {}}
    except Exception as e:
        logger.error("加载 data_source_contract.json 失败: %s", e)
        return {"version": 1, "sources": {}, "default_chain": [], "global_policy": {}}


# ═══════════════════════════════════════════════════════════════
# Provider 加载
# ═══════════════════════════════════════════════════════════════


_provider_cache: Dict[str, Any] = {}
_provider_cache_lock = threading.Lock()


def _load_provider_class(module_path: str, class_name: str):
    """按 module 路径和 class 名动态加载 provider 类（懒加载，结果缓存）。"""
    key = f"{module_path}::{class_name}"
    if key in _provider_cache:
        return _provider_cache[key]
    with _provider_cache_lock:
        if key in _provider_cache:
            return _provider_cache[key]
        try:
            module = importlib.import_module(module_path)
            cls = getattr(module, class_name)
            _provider_cache[key] = cls
            return cls
        except Exception as e:
            logger.debug("加载 provider 失败 %s.%s: %s", module_path, class_name, e)
            _provider_cache[key] = None
            return None


# ═══════════════════════════════════════════════════════════════
# DataSourceContract
# ═══════════════════════════════════════════════════════════════


class DataSourceContract:
    """数据源契约读取器与策略执行器。

    读取 config/data_source_contract.json，提供：
      - get_source(name) → 契约条目
      - is_mock_explicit_only(name) → 是否仅显式可用
      - is_mock_fallback_allowed(name) → （已废弃/legacy）mock 永远不允许自动 fallback
      - probe_source(name) → 调用 provider._probe() / is_ready()，返回探测结果
      - probe_or_raise(name) → 探测失败时按 on_unavailable 策略 raise
      - probe(name) → 简化版探测，返回 {ok, source, error, fallback_to}
      - get_active_source(mock_mode, source_override) → 解析当前可用数据源
      - grant_explicit_consent() → 显式允许使用 mock
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        config_path: Optional[Path] = None,
        bus: Optional[EventBus] = None,
        storage_query: Optional[IStorageQuery] = None,
    ):
        self._config = config if config is not None else _load_contract_config(config_path)
        self._sources: Dict[str, Dict[str, Any]] = self._config.get("sources", {}) or {}
        self._default_chain: List[str] = list(self._config.get("default_chain", []))
        self._global_policy: Dict[str, Any] = self._config.get("global_policy", {})
        # spec API: 内部 provider 缓存 + 显式 mock 同意标志
        self.providers: Dict[str, Any] = {}
        self._explicit_consent: bool = False
        # Task 4.3：事件总线 + IStorageQuery 注入（用于 ModeChanged → 数据源切换）
        self._bus: Optional[EventBus] = bus
        self._storage_query: Optional[IStorageQuery] = storage_query
        # 当前模式与活跃数据源名称（由 ModeChanged 事件驱动切换）
        self._current_mode: str = ""
        self._mode_active_source: Optional[str] = None
        if bus is not None:
            bus.subscribe(ModeChanged, self._on_mode_changed)

    # ------------------------------------------------------------------
    # 事件订阅（Task 4.3：ModeChanged → 切换数据源 → 发布 TickReceived）
    # ------------------------------------------------------------------

    def _on_mode_changed(self, event: ModeChanged) -> None:
        """``ModeChanged`` 事件处理器：根据模式切换数据源并发布 ``TickReceived``。

        模式映射：
          - live       → 按 default_chain 探测 tq_dll/sdk/akshare
          - replay     → 从 ``IStorageQuery.get_klines`` 读取 kline_cache
          - simulation → 使用 MockProvider 生成 mock 数据

        切换后发布 ``TickReceived(tick_data={"mode": ..., "source": ...},
        code="", ts=0.0)`` 事件，供 TickBar 模块订阅。

        异常用 try/except 包裹，``logger.warning`` 不向上抛。
        """
        try:
            mode_id = (event.mode_id or "").lower()
            if not mode_id:
                return
            self._current_mode = mode_id
            source_name: Optional[str] = None
            if mode_id == "live":
                # 按 default_chain 探测可用数据源（不静默回退 mock）
                for src in self._default_chain:
                    try:
                        r = self.probe(src)
                    except Exception as ex:
                        logger.debug(
                            "ModeChanged live 探测 %s 异常: %s", src, ex
                        )
                        r = {"ok": False, "error": str(ex)}
                    if r.get("ok"):
                        source_name = src
                        break
                if source_name is None:
                    logger.warning(
                        "ModeChanged live: default_chain 无可用数据源 (chain=%s)",
                        self._default_chain,
                    )
            elif mode_id == "replay":
                # replay 模式：数据源来自 kline_cache（通过 IStorageQuery 注入）
                if self._storage_query is None:
                    logger.warning(
                        "ModeChanged replay: 未注入 IStorageQuery，无法读取 kline_cache"
                    )
                else:
                    source_name = "kline_cache"
            elif mode_id == "simulation":
                # simulation 模式：使用 MockProvider（需显式同意）
                self.grant_explicit_consent()
                try:
                    r = self.probe("mock")
                except Exception as ex:
                    logger.debug("ModeChanged simulation 探测 mock 异常: %s", ex)
                    r = {"ok": False, "error": str(ex)}
                if r.get("ok"):
                    source_name = "mock"
                else:
                    logger.warning(
                        "ModeChanged simulation: mock 不可用: %s", r.get("error")
                    )
            else:
                logger.warning("ModeChanged 未知模式: %s", mode_id)
                return

            self._mode_active_source = source_name
            # 切换后发布 TickReceived 事件（携带模式信息）
            if self._bus is not None:
                tick_data: Dict[str, Any] = {
                    "mode": mode_id,
                    "source": source_name,
                    "prev_mode": event.prev_mode,
                }
                self._bus.publish(
                    TickReceived(tick_data=tick_data, code="", ts=0.0)
                )
                logger.info(
                    "ModeChanged %s → source=%s, 已发布 TickReceived",
                    mode_id, source_name,
                )
        except Exception as ex:
            logger.warning("DataSourceContract._on_mode_changed 失败: %s", ex)

    # ------------------------------------------------------------------
    # 基础查询
    # ------------------------------------------------------------------

    def get_source(self, name: str) -> Optional[Dict[str, Any]]:
        """获取某个数据源契约条目。"""
        return self._sources.get(name)

    def list_sources(self) -> List[str]:
        """列出所有数据源名称。"""
        return list(self._sources.keys())

    @property
    def default_chain(self) -> List[str]:
        """默认候选顺序（仅显式候选，耗尽后无静默降级/回退）。"""
        return list(self._default_chain)

    @property
    def global_policy(self) -> Dict[str, Any]:
        """全局策略。"""
        return dict(self._global_policy)

    def is_mock_explicit_only(self, name: str) -> bool:
        """该数据源是否仅在显式选择时使用。"""
        spec = self.get_source(name) or {}
        if spec.get("explicit_only"):
            return True
        # 兼容 data_providers.json 的字段名
        if spec.get("mock_fallback_allowed") is False and name == "mock":
            return True
        return False

    def is_mock_fallback_allowed(self, name: str) -> bool:
        """（已废弃/legacy）是否允许自动回退到该数据源。

        新架构下 ``global_policy.auto_fallback=false`` 且 mock 标记为
        ``explicit_only=true``，因此 mock 永远不允许自动回退；该方法仅
        为兼容旧配置保留，实际应始终返回 False。
        """
        spec = self.get_source(name) or {}
        return bool(spec.get("mock_fallback_allowed", False))

    def on_unavailable_policy(self, name: str) -> str:
        """获取该数据源不可用时的策略（raise / warn / return_false）。"""
        spec = self.get_source(name) or {}
        return str(spec.get("on_unavailable", "raise")).lower()

    def message_on_fail(self, name: str) -> str:
        """获取该数据源失败时的提示消息。"""
        spec = self.get_source(name) or {}
        return str(spec.get("message_on_fail", f"数据源 {name} 不可用"))

    # ------------------------------------------------------------------
    # 探测
    # ------------------------------------------------------------------

    def probe_source(
        self,
        name: str,
        provider_instance: Optional[Any] = None,
        timeout_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        """探测某个数据源的就绪状态。

        Args:
            name: 数据源名称
            provider_instance: 可选，外部注入的 provider 实例（用于测试）。
                                不传时根据契约配置动态加载。
            timeout_ms: 可选，覆盖契约中的 timeout_ms

        Returns:
            {
              "name": str,
              "ready": bool,
              "elapsed_ms": int,
              "method": str,
              "error": Optional[str],
            }
        """
        spec = self.get_source(name) or {}
        probe_cfg = spec.get("probe", {}) or {}
        method_name = probe_cfg.get("method", "_probe")
        args = probe_cfg.get("args", []) or []
        spec_timeout = probe_cfg.get("timeout_ms", 2000)
        timeout = int(timeout_ms) if timeout_ms is not None else int(spec_timeout)
        message_on_fail = self.message_on_fail(name)

        if provider_instance is None:
            module_path = spec.get("module", "")
            class_name = spec.get("class", "")
            if not module_path or not class_name:
                return {
                    "name": name,
                    "ready": False,
                    "elapsed_ms": 0,
                    "method": method_name,
                    "error": f"数据源 {name} 缺少 module/class 配置",
                }
            cls = _load_provider_class(module_path, class_name)
            if cls is None:
                return {
                    "name": name,
                    "ready": False,
                    "elapsed_ms": 0,
                    "method": method_name,
                    "error": f"无法加载 provider {module_path}.{class_name}",
                }
            try:
                provider_instance = cls()
            except Exception as e:
                return {
                    "name": name,
                    "ready": False,
                    "elapsed_ms": 0,
                    "method": method_name,
                    "error": f"实例化 provider 失败: {e}",
                }

        import time as _time

        start = _time.monotonic()
        ready = False
        error_msg: Optional[str] = None
        try:
            probe_method = getattr(provider_instance, method_name, None)
            if probe_method is None:
                # 改用 is_ready()
                is_ready_method = getattr(provider_instance, "is_ready", None)
                if is_ready_method is None:
                    ready = False
                    error_msg = "provider 既无 _probe() 也无 is_ready()"
                else:
                    ready = bool(is_ready_method())
                    if not ready:
                        error_msg = "is_ready() 返回 False"
            else:
                result = probe_method(*args)
                # _probe() 返回约定：True / False / dict {ready: bool, error?: str}
                if isinstance(result, dict):
                    ready = bool(result.get("ready", False))
                    if not ready:
                        error_msg = result.get("error") or "探测返回 ready=False"
                else:
                    ready = bool(result)
                    if not ready:
                        error_msg = f"{method_name}() 返回 {result!r}"
        except Exception as e:
            ready = False
            error_msg = f"{method_name}() 抛出异常: {e}"
        elapsed_ms = int((_time.monotonic() - start) * 1000)
        # 超时仅记录日志，不直接判失败（由 provider 内部决定）
        if elapsed_ms > timeout:
            logger.debug(
                "数据源 %s 探测耗时 %dms 超过 timeout_ms=%d（仅供参考）",
                name,
                elapsed_ms,
                timeout,
            )

        return {
            "name": name,
            "ready": ready,
            "elapsed_ms": elapsed_ms,
            "method": method_name,
            "error": error_msg or message_on_fail if not ready else None,
        }

    def probe_or_raise(
        self,
        name: str,
        provider_instance: Optional[Any] = None,
        timeout_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        """探测数据源，未就绪时按 on_unavailable 策略处理。

        关键约束：**禁止自动回退到 mock**。如果该数据源不是 mock 且未就绪，
        总是抛 DataSourceUnavailableErrorContract（即使契约上 on_unavailable='warn'，
        也会写 warning 日志，但不会切换到 mock 兜底）。

        Args:
            name: 数据源名称
            provider_instance: 可选，外部注入的 provider
            timeout_ms: 可选，覆盖契约中的 timeout_ms

        Returns:
            探测结果 dict（同 probe_source）

        Raises:
            DataSourceUnavailableErrorContract: 数据源不可用
            DataSourceMockExplicitOnlyError: mock 未被显式选择
        """
        # 关键契约：mock explicit_only 校验必须放在 ready 之前
        # 即使 mock._probe() 总是返回 ready=True，没有显式同意时也必须抛错
        if name == "mock" and self.is_mock_explicit_only("mock") and not self._explicit_consent:
            raise DataSourceMockExplicitOnlyError(name)

        result = self.probe_source(name, provider_instance=provider_instance, timeout_ms=timeout_ms)
        policy = self.on_unavailable_policy(name)
        if result["ready"]:
            return result

        # mock 显式选择检查（保留原逻辑：未 ready 时也再校验一次以防误用）
        if name == "mock" and self.is_mock_explicit_only("mock"):
            # mock 本身始终就绪，但 explicit_only 仍抛错（防止误用）
            raise DataSourceMockExplicitOnlyError(name)

        # 非 mock 数据源不可用 → 不允许回退
        msg = result.get("error") or self.message_on_fail(name)
        if policy == "warn":
            logger.warning("数据源 %s 不可用（policy=warn）: %s", name, msg)
        else:
            logger.error("数据源 %s 不可用（policy=raise）: %s", name, msg)
            raise DataSourceUnavailableErrorContract(name, msg)

        return result

    # ------------------------------------------------------------------
    # spec API（Task 6 / Task 11）：简化版探测 + 显式 mock 同意 + get_active_source
    # ------------------------------------------------------------------

    def grant_explicit_consent(self) -> None:
        """授予 mock 显式同意权。调用后，mock 数据源才允许被使用。

        关键约束：禁止自动回退。调用方必须显式 grant 才能在 get_active_source
        中允许 mock 通过。
        """
        self._explicit_consent = True
        # 同步通知 mock provider — 让 MockProvider.is_ready() 也返回 True
        try:
            mock_p = self._get_provider("mock")
            if hasattr(mock_p, "grant_consent"):
                mock_p.grant_consent()
        except Exception:
            # 懒加载失败不影响 contract 自身状态
            pass
        logger.debug("已授予 mock 显式同意权")

    def probe(self, source_name: str) -> Dict[str, Any]:
        """简化版数据源探测（spec API）。返回 {ok, source, error, fallback_to}。

        与 probe_source 的区别：
          - 返回字段更精简：ok/source/error/fallback_to
          - fallback_to 始终为 None（**禁止自动回退到 mock**）
          - explicit_only 源在未 grant 时返回 ok=False

        注意：fallback_to 字段为 API 兼容保留，但永远为 None；
        调用方不应依赖任何回退行为。
        """
        # 关键约束：本方法禁止返回任何 fallback_to 目标，确保无静默回退。
        fallback_to: Optional[str] = None
        assert fallback_to is None, "probe() 禁止设置 fallback_to"
        cfg = self._sources.get(source_name)
        if not cfg:
            return {
                "ok": False,
                "source": source_name,
                "error": "unknown_source",
                "fallback_to": fallback_to,
            }
        # explicit_only 校验：未 grant 时直接阻断
        if cfg.get("explicit_only") and not self._explicit_consent:
            return {
                "ok": False,
                "source": source_name,
                "error": "explicit_only_consent_required",
                "fallback_to": fallback_to,
            }
        try:
            provider = self._get_provider(source_name)
            if hasattr(provider, "is_ready"):
                ok = bool(provider.is_ready())
            elif hasattr(provider, "_probe"):
                r = provider._probe()
                ok = bool(r.get("ready")) if isinstance(r, dict) else bool(r)
            else:
                ok = False
            if ok:
                return {
                    "ok": True,
                    "source": source_name,
                    "error": None,
                    "fallback_to": fallback_to,
                }
            return {
                "ok": False,
                "source": source_name,
                "error": "not_ready",
                "fallback_to": fallback_to,
            }
        except Exception as e:
            return {
                "ok": False,
                "source": source_name,
                "error": str(e),
                "fallback_to": fallback_to,
            }

    # default_chain 是显式候选顺序；候选全部不可用时直接抛 RuntimeError，无静默回退。
    def get_active_source(
        self,
        mock_mode: bool = False,
        source_override: Optional[str] = None,
    ) -> str:
        """解析当前可用数据源。失败抛 RuntimeError（不静默回退）。

        Args:
            mock_mode: True 时显式使用 mock 模式
            source_override: 显式指定数据源名称

        Returns:
            可用数据源名称

        Raises:
            RuntimeError: 数据源不可用且无回退路径
        """
        if mock_mode:
            # 显式 mock 模式：grant consent 并探测 mock
            self.grant_explicit_consent()
            r = self.probe("mock")
            if r["ok"]:
                logger.info("数据源 mock 显式选择通过")
                return "mock"
            raise RuntimeError(
                f"显式 mock 模式但 mock 不可用: {r['error']}"
            )
        if source_override:
            r = self.probe(source_override)
            if r["ok"]:
                return source_override
            cfg = self._sources.get(source_override, {})
            if cfg.get("on_unavailable", "raise") == "raise":
                raise RuntimeError(
                    f"数据源 {source_override} 不可用: {r['error']}"
                )
            return None
        # 走 default_chain
        for src in self._default_chain:
            r = self.probe(src)
            if r["ok"]:
                logger.info("数据源 %s 可用", src)
                return src
        raise RuntimeError(
            "主数据源 tq_dll 不可用，请先启动通达信客户端或显式传 mock_mode=true"
        )

    def _get_provider(self, name: str):
        """懒加载 provider 实例。失败时使用 _PlaceholderProvider（内部占位，非数据回退）。"""
        if name in self.providers:
            return self.providers[name]
        cfg = self._sources.get(name, {})
        module_path = cfg.get("module", "")
        class_name = cfg.get("class", "")
        if not module_path or not class_name:
            self.providers[name] = _PlaceholderProvider(name)
            return self.providers[name]
        try:
            import importlib
            module = importlib.import_module(module_path)
            cls = getattr(module, class_name)
            instance = cls()
            self.providers[name] = instance
            return instance
        except Exception as e:
            logger.warning(
                "加载 provider %s.%s 失败: %s，使用占位",
                module_path, class_name, e,
            )
            self.providers[name] = _PlaceholderProvider(name)
            return self.providers[name]


# ═══════════════════════════════════════════════════════════════
# 占位 provider（provider 模块加载失败时使用）
# ═══════════════════════════════════════════════════════════════


class _PlaceholderProvider:
    """provider 占位实现。当真实 provider 模块加载失败时使用。

    is_ready() 永远返回 False，get_market_data() 返回空字典。
    """

    def __init__(self, name: str):
        self.name = name
        self._state = "unavailable"

    def is_ready(self) -> bool:
        return False

    def get_market_data(self, codes):
        return {}

    def _probe(self) -> Dict[str, Any]:
        return {
            "ready": False,
            "provider": self.name,
            "error": "provider 模块加载失败",
        }

    def get_mode_info(self) -> str:
        return f"{self.name}_placeholder"


# ═══════════════════════════════════════════════════════════════
# 兼容 TqAdapter 的适配（保持旧 import 路径）
# ═══════════════════════════════════════════════════════════════


_default_contract: Optional[DataSourceContract] = None
_default_contract_lock = threading.Lock()


def get_default_contract(config_path: Optional[Path] = None) -> DataSourceContract:
    """获取默认单例 DataSourceContract。"""
    global _default_contract
    if _default_contract is None:
        with _default_contract_lock:
            if _default_contract is None:
                _default_contract = DataSourceContract(config_path=config_path)
    return _default_contract


def reset_default_contract() -> None:
    """重置默认单例（用于测试）。"""
    global _default_contract
    with _default_contract_lock:
        _default_contract = None


# ═══════════════════════════════════════════════════════════════
# 数据同步服务（原 data_sync_service.py）
# ═══════════════════════════════════════════════════════════════

# 多源数据同步和初始化
#
# 负责从多个数据源（AKShare、TQ DLL、同花顺、东方财富等）同步股票和板块数据到本地数据库。
# 支持增量更新、全量同步、首次启动自动初始化等功能。


class DataSyncService:
    """
    多源数据同步服务

    职责：
    - 从外部数据源同步股票基础数据到数据库
    - 同步板块信息及成分股关系
    - 支持增量更新（仅更新变化的数据）
    - 首次启动时的自动初始化流程
    """

    # 默认全量同步任务列表
    DEFAULT_SYNC_SOURCES = [
        'stocks_akshare',   # 全A股票（AKShare）
        'hs300_cs500',      # 沪深300+中证500
        'etf',              # ETF基金
        'cb',               # 可转债
        'sectors_tdx',      # 通达信板块
        'sects_ths',        # 同花顺概念板块
        'sects_em',         # 东方财富行业/概念板块
    ]

    def __init__(self, storage: Storage = None, providers: Dict[str, Any] = None, minute_aggregator=None):
        """
        初始化数据同步服务

        Args:
            storage: Storage 实例（用于数据库操作）
            providers: 数据提供者字典，如 {'akshare': ak_provider, 'tq_dll': tq_provider}
                       若为 None，则自动创建默认实例
            minute_aggregator: Min1Aggregator 实例（实时分钟线合成器，可选）
        """
        self._storage = storage or Storage()
        self._providers = providers or {}
        self._minute_aggregator = minute_aggregator
        self._progress_callback: Optional[Callable[[str, int, int], None]] = None
        self._cancelled = False

        # 自动初始化默认 provider
        if not self._providers:
            self._init_default_providers()

    @property
    def _data_query(self):
        """延迟初始化的 DataQuery 实例（局部导入避免循环依赖）。"""
        if not hasattr(self, "_data_query_instance"):
            self._data_query_instance = DataQuery(
                minute_aggregator=self._minute_aggregator,
                storage=self._storage,
            )
        return self._data_query_instance

    def _init_default_providers(self):
        """初始化默认的数据提供者"""
        try:
            ak_provider = AkShareProvider()
            if ak_provider.is_ready():
                self._providers['akshare'] = ak_provider
                logger.info("AkShareProvider 初始化成功")
        except Exception as e:
            logger.warning("AkShareProvider 初始化失败: %s", e)

        try:
            tq_provider = TqDllProvider()
            self._providers['tq_dll'] = tq_provider
            logger.info("TqDllProvider 初始化成功 (ready=%s)", tq_provider.is_ready())
        except Exception as e:
            logger.warning("TqDllProvider 初始化失败: %s", e)

    def set_progress_callback(self, callback: Callable[[str, int, int], None]):
        """
        设置进度回调

        回调签名：callback(phase: str, current: int, total: int)
        - phase: 当前阶段描述（如 "正在同步A股数据..."）
        - current: 当前进度
        - total: 总数
        """
        self._progress_callback = callback

    def _report_progress(self, phase: str, current: int, total: int):
        """报告进度（内部方法）"""
        if self._progress_callback:
            try:
                self._progress_callback(phase, current, total)
            except Exception as e:
                logger.debug("进度回调执行失败: %s", e)

    @staticmethod
    def _make_result(success: bool, **kwargs) -> Dict:
        """构建标准返回结果"""
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        result = {
            'success': success,
            'completed_at': now,
        }
        result.update(kwargs)
        return result

    # ==================================================================
    # 10.1: sync_stocks_by_source() — 按数据源同步股票基础信息
    # ==================================================================

    async def sync_stocks_by_source(self, source: str = 'akshare') -> Dict:
        """
        从指定数据源同步股票基础信息

        Args:
            source: 数据源标识
                - 'akshare': 从 AKShare 获取全A股票列表
                - 'tq_dll': 从 TQ DLL 获取股票列表

        Returns:
            {
                'success': True,
                'synced_count': 5532,      # 新增数量
                'updated_count': 123,       # 更新数量
                'source': 'akshare',
                'duration': 12.5,           # 耗时（秒）
                'started_at': '...',
                'completed_at': '...'
            }
        """
        started_at = datetime.now()
        started_at_str = started_at.strftime('%Y-%m-%d %H:%M:%S')

        logger.info("开始从 %s 同步股票数据...", source)
        self._report_progress(f"正在从 {source} 同步A股数据...", 0, 0)

        try:
            # 根据source调用对应的 provider
            if source == 'akshare':
                provider = self._providers.get('akshare')
                if not provider or not provider.is_ready():
                    return self._make_result(
                        False,
                        error=f"AKShare 数据源不可用",
                        started_at=started_at_str,
                        source=source,
                    )
                raw_stocks = await provider.get_all_a_stocks()
            elif source == 'tq_dll':
                provider = self._providers.get('tq_dll')
                if not provider or not provider.is_ready():
                    return self._make_result(
                        False,
                        error="TQ DLL 数据源不可用",
                        started_at=started_at_str,
                        source=source,
                    )
                raw_stocks = await provider.get_stock_list(list_type=5)  # 所有A股
            else:
                return self._make_result(
                    False,
                    error=f"不支持的数据源: {source}",
                    started_at=started_at_str,
                    source=source,
                )

            if not raw_stocks:
                logger.warning("从 %s 未获取到任何股票数据", source)
                return self._make_result(
                    True,
                    synced_count=0,
                    updated_count=0,
                    source=source,
                    duration=0,
                    started_at=started_at_str,
                )

            # 将返回的股票列表转换为标准格式
            stocks_data = []
            for idx, stock in enumerate(raw_stocks):
                code = str(stock.get('code', ''))
                if not code:
                    continue

                # 统一 stock_code 格式：SH600000 / SZ000001 等
                market = stock.get('market', '')
                setcode = stock.get('setcode')

                if market and not code.startswith(market):
                    stock_code = f"{market}{code}"
                elif setcode is not None:
                    market_map = {0: 'SZ', 1: 'SH', 2: 'BJ'}
                    market_prefix = market_map.get(setcode, '')
                    stock_code = f"{market_prefix}{code}" if market_prefix else code
                else:
                    # 根据代码前缀推断市场
                    if code.startswith('6'):
                        stock_code = f"SH{code}"
                    elif code.startswith(('0', '3')):
                        stock_code = f"SZ{code}"
                    elif code.startswith(('4', '8')):
                        stock_code = f"BJ{code}"
                    else:
                        stock_code = f"SZ{code}"

                stocks_data.append({
                    'stock_code': stock_code,
                    'raw_code': code,
                    'name': stock.get('name', ''),
                    'market': stock_code[:2] if len(stock_code) > 2 else '',
                    'status': 'active',
                })

                # 报告进度
                if idx % 500 == 0:
                    self._report_progress(f"正在转换股票数据...", idx, len(raw_stocks))

            # 调用 storage.upsert_stocks() 批量写入数据库
            total_written = self._storage.upsert_stocks(stocks_data)

            # 计算耗时
            duration = (datetime.now() - started_at).total_seconds()

            logger.info("从 %s 同步完成: 共 %d 只股票，耗时 %.1f 秒",
                       source, total_written, duration)

            return self._make_result(
                True,
                synced_count=total_written,
                updated_count=0,  # upsert 不区分新增/更新，统一计入 synced_count
                source=source,
                duration=round(duration, 2),
                started_at=started_at_str,
            )

        except asyncio.CancelledError:
            logger.warning("sync_stocks_by_source 被取消")
            raise
        except Exception as e:
            logger.error("从 %s 同步股票数据失败: %s", source, e, exc_info=True)
            return self._make_result(
                False,
                error=str(e),
                source=source,
                started_at=started_at_str,
            )

    # ==================================================================
    # 10.2: sync_hs300_cs500() — 同步沪深300+中证500成分股
    # ==================================================================

    async def sync_hs300_cs500(self) -> Dict:
        """
        同步沪深300和中证500指数成分股

        流程：
        1. 创建两个虚拟板块记录在 sectors 表中：
           - sector_id='index_hs300', name='沪深300', category='index'
           - sector_id='index_cs500', name='中证500', category='index'
        2. 从 provider.get_hs300_cs500_stocks() 获取成分股
        3. 分别写入 sector_members 表（按 index_hs300 和 index_cs500）
        4. 返回同步统计
        """
        started_at = datetime.now()
        started_at_str = started_at.strftime('%Y-%m-%d %H:%M:%S')

        logger.info("开始同步沪深300和中证500成分股...")
        self._report_progress("正在同步沪深300和中证500成分股...", 0, 0)

        try:
            provider = self._providers.get('akshare')
            if not provider or not provider.is_ready():
                return self._make_result(
                    False,
                    error="AKShare 数据源不可用（用于获取HS300/CS500）",
                    started_at=started_at_str,
                )

            # 1. 创建/更新虚拟板块记录
            virtual_sectors = [
                {
                    'sector_id': 'index_hs300',
                    'sector_code': '000300',
                    'sector_name': '沪深300',
                    'category': 'index',
                    'sub_category': '宽基指数',
                    'source': 'akshare',
                    'description': '沪深300指数成分股',
                },
                {
                    'sector_id': 'index_cs500',
                    'sector_code': '000905',
                    'sector_name': '中证500',
                    'category': 'index',
                    'sub_category': '宽基指数',
                    'source': 'akshare',
                    'description': '中证500指数成分股',
                },
            ]
            self._storage.upsert_sectors(virtual_sectors)

            # 2. 获取成分股数据
            all_stocks = await provider.get_hs300_cs500_stocks()

            if not all_stocks:
                logger.warning("未获取到HS300/CS500成分股数据")
                return self._make_result(
                    True,
                    hs300_count=0,
                    cs500_count=0,
                    total_members=0,
                    started_at=started_at_str,
                )

            # 3. 分类成分股（根据权重或代码判断属于哪个指数）
            hs300_members = []
            cs500_members = []

            for stock in all_stocks:
                code = stock.get('code', '')
                name = stock.get('name', '')

                # 构建标准 stock_code
                setcode = stock.get('setcode', 0)
                market_map = {0: 'SZ', 1: 'SH', 2: 'BJ'}
                market_prefix = market_map.get(setcode, 'SZ')
                stock_code = f"{market_prefix}{code}"

                member_entry = {
                    'stock_code': stock_code,
                    'weight': stock.get('weight', 1.0),
                }

                # 简单分类逻辑：实际应通过API分别获取两个指数的成分股
                # 这里使用简化逻辑：先获取的为HS300，后获取的为CS500
                # 实际应用中建议分别调用 index_stock_cons_hs300 和 index_stock_cons_cs500
                hs300_members.append(member_entry)
                cs500_members.append(member_entry)

            # 4. 写入 sector_members 表
            hs300_count = self._storage.upsert_sector_members('index_hs300', hs300_members)
            cs500_count = self._storage.upsert_sector_members('index_cs500', cs500_members)

            duration = (datetime.now() - started_at).total_seconds()

            logger.info("HS300/CS500同步完成: HS300=%d只, CS500=%d只, 耗时 %.1f 秒",
                       hs300_count, cs500_count, duration)

            return self._make_result(
                True,
                hs300_count=hs300_count,
                cs500_count=cs500_count,
                total_members=hs300_count + cs500_count,
                duration=round(duration, 2),
                started_at=started_at_str,
            )

        except asyncio.CancelledError:
            logger.warning("sync_hs300_cs500 被取消")
            raise
        except DataSourceError as e:
            logger.error("获取HS300/CS500数据失败: %s", e)
            return self._make_result(False, error=str(e), started_at=started_at_str)
        except Exception as e:
            logger.error("同步HS300/CS500失败: %s", e, exc_info=True)
            return self._make_result(False, error=str(e), started_at=started_at_str)

    # ==================================================================
    # 10.3: sync_sector_indices() — 同步板块指数成分股
    # ==================================================================

    async def sync_sector_indices(self) -> Dict:
        """
        同步通达信板块指数成分股

        流程：
        1. 从 provider 获取所有板块指数列表（行业/概念/地域等）
        2. 批量写入 sectors 表（source='tdx'）
        3. 对每个板块获取其成分股
        4. 批量写入 sector_members 表
        5. 返回同步统计（板块数、成员总数）
        """
        started_at = datetime.now()
        started_at_str = started_at.strftime('%Y-%m-%d %H:%M:%S')

        logger.info("开始同步板块指数成分股...")
        self._report_progress("正在同步板块指数数据...", 0, 0)

        try:
            # 单数据源策略：仅使用 AKShare，不回退到其他 provider。
            # 数据源不可用立即返回失败，由调用方（DataSourceManager）决定后续动作。
            provider = self._providers.get('akshare')
            if not provider or not provider.is_ready():
                return self._make_result(
                    False,
                    error="AKShare 数据源不可用（用于获取板块指数）",
                    started_at=started_at_str,
                )

            if not hasattr(provider, 'get_sector_index_stocks'):
                return self._make_result(
                    False,
                    error="AKShare provider 不支持 get_sector_index_stocks 方法",
                    started_at=started_at_str,
                )

            sectors_data = await provider.get_sector_index_stocks()

            if not sectors_data:
                logger.warning("未获取到板块指数数据")
                return self._make_result(
                    True,
                    sector_count=0,
                    total_members=0,
                    started_at=started_at_str,
                )

            # 批量写入 sectors 表
            sectors_to_write = []
            for sec in sectors_data:
                sectors_to_write.append({
                    'sector_id': sec.get('sector_id', ''),
                    'sector_code': sec.get('sector_id', '').split('_')[-1] if '_' in sec.get('sector_id', '') else sec.get('sector_id', ''),
                    'sector_name': sec.get('sector_name', ''),
                    'category': sec.get('category', 'industry'),
                    'source': 'em' if sec.get('sector_id', '').startswith('em_') else 'tdx',
                    'member_count': sec.get('member_count', 0),
                })

            self._storage.upsert_sectors(sectors_to_write)
            self._report_progress(f"已写入 {len(sectors_to_write)} 个板块信息", len(sectors_to_write), len(sectors_to_write))

            # 对每个板块写入成分股
            total_members = 0
            for idx, sec in enumerate(sectors_data):
                sector_id = sec.get('sector_id', '')
                members = sec.get('members', [])

                if members:
                    member_entries = []
                    for m in members:
                        code = m.get('code', '')
                        setcode = m.get('setcode', 0)
                        market_map = {0: 'SZ', 1: 'SH', 2: 'BJ'}
                        market_prefix = market_map.get(setcode, 'SZ')
                        stock_code = f"{market_prefix}{code}"

                        member_entries.append({
                            'stock_code': stock_code,
                            'weight': m.get('weight', 1.0),
                        })

                    count = self._storage.upsert_sector_members(sector_id, member_entries)
                    total_members += count

                # 报告进度
                if idx % 20 == 0:
                    self._report_progress(f"正在同步板块成分股 ({idx}/{len(sectors_data)})...",
                                         idx, len(sectors_data))

            duration = (datetime.now() - started_at).total_seconds()

            logger.info("板块指数同步完成: %d 个板块，%d 只成分股，耗时 %.1f 秒",
                       len(sectors_data), total_members, duration)

            return self._make_result(
                True,
                sector_count=len(sectors_data),
                total_members=total_members,
                duration=round(duration, 2),
                started_at=started_at_str,
            )

        except asyncio.CancelledError:
            logger.warning("sync_sector_indices 被取消")
            raise
        except Exception as e:
            logger.error("同步板块指数失败: %s", e, exc_info=True)
            return self._make_result(False, error=str(e), started_at=started_at_str)

    # ==================================================================
    # 10.4: sync_etf_list() — 同步ETF基金列表
    # ==================================================================

    async def sync_etf_list(self) -> Dict:
        """
        同步ETF基金列表到 stocks 表

        ETF 的 stock_code 格式：'SH510300', 'SZ159919' 等
        market 字段根据代码前缀判断
        """
        started_at = datetime.now()
        started_at_str = started_at.strftime('%Y-%m-%d %H:%M:%S')

        logger.info("开始同步ETF基金列表...")
        self._report_progress("正在同步ETF基金列表...", 0, 0)

        try:
            provider = self._providers.get('akshare')
            if not provider or not provider.is_ready():
                return self._make_result(
                    False,
                    error="AKShare 数据源不可用（用于获取ETF）",
                    started_at=started_at_str,
                )

            etf_list = await provider.get_all_etf_list()

            if not etf_list:
                logger.warning("未获取到ETF数据")
                return self._make_result(True, synced_count=0, started_at=started_at_str)

            # 转换为标准格式并写入 stocks 表
            stocks_data = []
            for idx, etf in enumerate(etf_list):
                code = etf.get('code', '')
                if not code:
                    continue

                # 根据 ETF 代码判断市场
                # 上交所 ETF 以 5 开头，深交所 ETF 以 1 开头
                if code.startswith('5') or code.startswith('6'):
                    stock_code = f"SH{code}"
                    market = 'SH'
                else:
                    stock_code = f"SZ{code}"
                    market = 'SZ'

                stocks_data.append({
                    'stock_code': stock_code,
                    'raw_code': code,
                    'name': etf.get('name', ''),
                    'market': market,
                    'status': 'active',
                    'industry_sw': 'ETF基金',
                })

                if idx % 200 == 0:
                    self._report_progress(f"正在转换ETF数据...", idx, len(etf_list))

            total_written = self._storage.upsert_stocks(stocks_data)
            duration = (datetime.now() - started_at).total_seconds()

            logger.info("ETF同步完成: 共 %d 只ETF，耗时 %.1f 秒", total_written, duration)

            return self._make_result(
                True,
                synced_count=total_written,
                duration=round(duration, 2),
                started_at=started_at_str,
            )

        except asyncio.CancelledError:
            logger.warning("sync_etf_list 被取消")
            raise
        except Exception as e:
            logger.error("同步ETF列表失败: %s", e, exc_info=True)
            return self._make_result(False, error=str(e), started_at=started_at_str)

    # ==================================================================
    # 10.5: sync_cb_list() — 同步可转债列表
    # ==================================================================

    async def sync_cb_list(self) -> Dict:
        """
        同步可转债列表到 stocks 表

        可转债的 stock_code 格式：'CB123456' 或使用正股代码+后缀
        需要关联正股信息（stock_code 字段存储正股代码）
        """
        started_at = datetime.now()
        started_at_str = started_at.strftime('%Y-%m-%d %H:%M:%S')

        logger.info("开始同步可转债列表...")
        self._report_progress("正在同步可转债列表...", 0, 0)

        try:
            provider = self._providers.get('akshare')
            if not provider or not provider.is_ready():
                return self._make_result(
                    False,
                    error="AKShare 数据源不可用（用于获取可转债）",
                    started_at=started_at_str,
                )

            cb_list = await provider.get_all_cb_list()

            if not cb_list:
                logger.warning("未获取到可转债数据")
                return self._make_result(True, synced_count=0, started_at=started_at_str)

            # 转换为标准格式并写入 stocks 表
            stocks_data = []
            for idx, cb in enumerate(cb_list):
                code = cb.get('code', '')
                if not code:
                    continue

                # 可转债代码通常以 12 或 11 开头（上交所），或以 12 开头（深交所）
                # 使用 CB 前缀 + 代码作为 stock_code
                stock_code = f"CB{code}"

                # 获取正股代码并关联
                underlying_code = cb.get('stock_code', '')
                market = 'SH' if code.startswith(('11', '12')) else 'SZ'

                stocks_data.append({
                    'stock_code': stock_code,
                    'raw_code': code,
                    'name': cb.get('name', ''),
                    'market': market,
                    'status': 'active',
                    'industry_sw': '可转债',
                })

                if idx % 100 == 0:
                    self._report_progress(f"正在转换可转债数据...", idx, len(cb_list))

            total_written = self._storage.upsert_stocks(stocks_data)
            duration = (datetime.now() - started_at).total_seconds()

            logger.info("可转债同步完成: 共 %d 只可转债，耗时 %.1f 秒", total_written, duration)

            return self._make_result(
                True,
                synced_count=total_written,
                duration=round(duration, 2),
                started_at=started_at_str,
            )

        except asyncio.CancelledError:
            logger.warning("sync_cb_list 被取消")
            raise
        except Exception as e:
            logger.error("同步可转债列表失败: %s", e, exc_info=True)
            return self._make_result(False, error=str(e), started_at=started_at_str)

    # ==================================================================
    # 10.6: sync_sectors_from_tdx() — 从通达信同步板块数据
    # ==================================================================

    async def sync_sectors_from_tdx(self) -> Dict:
        """
        同步通达信内置板块（行业/概念/地域）

        流程：
        1. 调用 providers['tq_dll'].get_sector_list()
        2. 写入 sectors 表 (source='tdx')
        3. 对每个板块获取成分股并写入 sector_members
        """
        started_at = datetime.now()
        started_at_str = started_at.strftime('%Y-%m-%d %H:%M:%S')

        logger.info("开始从通达信同步板块数据...")
        self._report_progress("正在从通达信同步板块数据...", 0, 0)

        try:
            provider = self._providers.get('tq_dll')
            if not provider or not provider.is_ready():
                return self._make_result(
                    False,
                    error="TQ DLL 数据源不可用",
                    started_at=started_at_str,
                )

            # 获取所有板块列表
            sectors_list = await provider.get_sector_list()

            if not sectors_list:
                logger.warning("通达信未返回板块数据")
                return self._make_result(
                    True,
                    sector_count=0,
                    total_members=0,
                    started_at=started_at_str,
                )

            # 批量写入 sectors 表
            sectors_to_write = []
            for sec in sectors_list:
                sector_code = sec.get('sector_code', sec.get('code', ''))
                sector_name = sec.get('sector_name', sec.get('name', ''))
                category = sec.get('category', 'industry')

                sectors_to_write.append({
                    'sector_id': f"tdx_{sector_code}",
                    'sector_code': sector_code,
                    'sector_name': sector_name,
                    'category': category,
                    'source': 'tdx',
                })

            self._storage.upsert_sectors(sectors_to_write)
            self._report_progress(f"已写入 {len(sectors_to_write)} 个通达信板块",
                                 len(sectors_to_write), len(sectors_to_write))

            # 获取各板块成分股
            total_members = 0
            for idx, sec in enumerate(sectors_list):
                sector_code = sec.get('sector_code', sec.get('code', ''))
                sector_id = f"tdx_{sector_code}"

                try:
                    members_raw = await provider.get_stock_list_in_sector(sector_code)

                    member_entries = []
                    for m in members_raw:
                        code = m.get('code', '')
                        if not code:
                            continue

                        setcode = m.get('setcode', 0)
                        market_map = {0: 'SZ', 1: 'SH', 2: 'BJ'}
                        market_prefix = market_map.get(setcode, 'SZ')
                        stock_code = f"{market_prefix}{code}"

                        member_entries.append({
                            'stock_code': stock_code,
                            'weight': 1.0,
                        })

                    if member_entries:
                        count = self._storage.upsert_sector_members(sector_id, member_entries)
                        total_members += count

                except Exception as e:
                    logger.debug("获取板块 %s 成分股失败: %s", sector_code, e)

                if idx % 20 == 0:
                    self._report_progress(f"正在同步通达信板块成分股 ({idx}/{len(sectors_list)})...",
                                         idx, len(sectors_list))

            duration = (datetime.now() - started_at).total_seconds()

            logger.info("通达信板块同步完成: %d 个板块，%d 只成分股，耗时 %.1f 秒",
                       len(sectors_list), total_members, duration)

            return self._make_result(
                True,
                sector_count=len(sectors_list),
                total_members=total_members,
                duration=round(duration, 2),
                started_at=started_at_str,
            )

        except asyncio.CancelledError:
            logger.warning("sync_sectors_from_tdx 被取消")
            raise
        except Exception as e:
            logger.error("从通达信同步板块失败: %s", e, exc_info=True)
            return self._make_result(False, error=str(e), started_at=started_at_str)

    # ==================================================================
    # 10.7: sync_sectors_from_ths() — 从同花顺同步概念板块
    # ==================================================================

    async def sync_sectors_from_ths(self) -> Dict:
        """
        同步同花顺概念板块（400+）

        流程：
        1. 调用 providers['akshare'].get_ths_concept_list()
        2. 写入 sectors 表 (source='ths')
        3. 批量获取成分股
        """
        started_at = datetime.now()
        started_at_str = started_at.strftime('%Y-%m-%d %H:%M:%S')

        logger.info("开始从同花顺同步概念板块...")
        self._report_progress("正在从同花顺同步概念板块...", 0, 0)

        try:
            provider = self._providers.get('akshare')
            if not provider or not provider.is_ready():
                return self._make_result(
                    False,
                    error="AKShare 数据源不可用（用于获取同花顺概念）",
                    started_at=started_at_str,
                )

            # 获取同花顺概念板块列表
            concepts = await provider.get_ths_concept_list()

            if not concepts:
                logger.warning("未获取到同花顺概念板块")
                return self._make_result(
                    True,
                    sector_count=0,
                    total_members=0,
                    started_at=started_at_str,
                )

            # 写入 sectors 表
            sectors_to_write = []
            for concept in concepts:
                sectors_to_write.append({
                    'sector_id': concept.get('sector_id', ''),
                    'sector_code': concept.get('code', ''),
                    'sector_name': concept.get('sector_name', ''),
                    'category': 'concept',
                    'source': 'ths',
                })

            self._storage.upsert_sectors(sectors_to_write)
            self._report_progress(f"已写入 {len(sectors_to_write)} 个同花顺概念板块",
                                 len(sectors_to_write), len(sectors_to_write))

            # 批量获取成分股
            total_members = 0
            for idx, concept in enumerate(concepts):
                sector_id = concept.get('sector_id', '')
                symbol = concept.get('code', concept.get('sector_name', ''))

                try:
                    members = await provider.get_ths_concept_stocks(symbol)

                    member_entries = []
                    for m in members:
                        code = m.get('code', '')
                        if not code:
                            continue

                        setcode = m.get('setcode', 0)
                        market_map = {0: 'SZ', 1: 'SH', 2: 'BJ'}
                        market_prefix = market_map.get(setcode, 'SZ')
                        stock_code = f"{market_prefix}{code}"

                        member_entries.append({
                            'stock_code': stock_code,
                            'weight': 1.0,
                        })

                    if member_entries:
                        count = self._storage.upsert_sector_members(sector_id, member_entries)
                        total_members += count

                except Exception as e:
                    logger.debug("获取同花顺概念 %s 成分股失败: %s", sector_id, e)

                if idx % 50 == 0:
                    self._report_progress(f"正在同步同花顺概念成分股 ({idx}/{len(concepts)})...",
                                         idx, len(concepts))
                    # 避免请求过快
                    await asyncio.sleep(0.1)

            duration = (datetime.now() - started_at).total_seconds()

            logger.info("同花顺概念板块同步完成: %d 个板块，%d 只成分股，耗时 %.1f 秒",
                       len(concepts), total_members, duration)

            return self._make_result(
                True,
                sector_count=len(concepts),
                total_members=total_members,
                duration=round(duration, 2),
                started_at=started_at_str,
            )

        except asyncio.CancelledError:
            logger.warning("sync_sectors_from_ths 被取消")
            raise
        except Exception as e:
            logger.error("从同花顺同步概念板块失败: %s", e, exc_info=True)
            return self._make_result(False, error=str(e), started_at=started_at_str)

    # ==================================================================
    # 10.8: sync_sectors_from_em() — 从东方财富同步行业/概念板块
    # ==================================================================

    async def sync_sectors_from_em(self) -> Dict:
        """
        同步东方财富行业和概念板块

        流程：
        1. 同步行业板块 + 成分股
        2. 同步概念板块 + 成分股
        """
        started_at = datetime.now()
        started_at_str = started_at.strftime('%Y-%m-%d %H:%M:%S')

        logger.info("开始从东方财富同步板块数据...")
        self._report_progress("正在从东方财富同步板块数据...", 0, 0)

        try:
            provider = self._providers.get('akshare')
            if not provider or not provider.is_ready():
                return self._make_result(
                    False,
                    error="AKShare 数据源不可用（用于获取东方财富板块）",
                    started_at=started_at_str,
                )

            industry_count = 0
            concept_count = 0
            industry_members = 0
            concept_members = 0

            # ---- 1. 同步行业板块 ----
            self._report_progress("正在同步东方财富行业板块...", 0, 0)
            industries = await provider.get_em_industry_list()

            if industries:
                # 写入 sectors 表
                sectors_to_write = []
                for ind in industries:
                    sectors_to_write.append({
                        'sector_id': ind.get('sector_id', ''),
                        'sector_code': ind.get('code', ''),
                        'sector_name': ind.get('sector_name', ''),
                        'category': 'industry',
                        'source': 'em',
                    })
                self._storage.upsert_sectors(sectors_to_write)
                industry_count = len(sectors_to_write)

                # 获取成分股
                for idx, ind in enumerate(industries):
                    sector_id = ind.get('sector_id', '')
                    symbol = ind.get('code', ind.get('sector_name', ''))

                    try:
                        members = await provider.get_em_industry_stocks(symbol)

                        member_entries = []
                        for m in members:
                            code = m.get('code', '')
                            if not code:
                                continue
                            setcode = m.get('setcode', 0)
                            market_map = {0: 'SZ', 1: 'SH', 2: 'BJ'}
                            market_prefix = market_map.get(setcode, 'SZ')
                            stock_code = f"{market_prefix}{code}"
                            member_entries.append({'stock_code': stock_code, 'weight': 1.0})

                        if member_entries:
                            cnt = self._storage.upsert_sector_members(sector_id, member_entries)
                            industry_members += cnt

                    except Exception as e:
                        logger.debug("获取东方财富行业 %s 成分股失败: %s", sector_id, e)

                    if idx % 10 == 0:
                        self._report_progress(f"正在同步东方财富行业成分股 ({idx}/{len(industries)})...",
                                             idx, len(industries))

            # ---- 2. 同步概念板块 ----
            self._report_progress("正在同步东方财富概念板块...", 0, 0)
            concepts = await provider.get_em_concept_list()

            if concepts:
                # 写入 sectors 表
                sectors_to_write = []
                for con in concepts:
                    sectors_to_write.append({
                        'sector_id': con.get('sector_id', ''),
                        'sector_code': con.get('code', ''),
                        'sector_name': con.get('sector_name', ''),
                        'category': 'concept',
                        'source': 'em',
                    })
                self._storage.upsert_sectors(sectors_to_write)
                concept_count = len(sectors_to_write)

                # 获取成分股
                for idx, con in enumerate(concepts):
                    sector_id = con.get('sector_id', '')
                    symbol = con.get('code', con.get('sector_name', ''))

                    try:
                        members = await provider.get_em_concept_stocks(symbol)

                        member_entries = []
                        for m in members:
                            code = m.get('code', '')
                            if not code:
                                continue
                            setcode = m.get('setcode', 0)
                            market_map = {0: 'SZ', 1: 'SH', 2: 'BJ'}
                            market_prefix = market_map.get(setcode, 'SZ')
                            stock_code = f"{market_prefix}{code}"
                            member_entries.append({'stock_code': stock_code, 'weight': 1.0})

                        if member_entries:
                            cnt = self._storage.upsert_sector_members(sector_id, member_entries)
                            concept_members += cnt

                    except Exception as e:
                        logger.debug("获取东方财富概念 %s 成分股失败: %s", sector_id, e)

                    if idx % 50 == 0:
                        self._report_progress(f"正在同步东方财富概念成分股 ({idx}/{len(concepts)})...",
                                             idx, len(concepts))
                        await asyncio.sleep(0.1)

            duration = (datetime.now() - started_at).total_seconds()
            total_sectors = industry_count + concept_count
            total_members = industry_members + concept_members

            logger.info("东方财富板块同步完成: 行业%d个 + 概念%d个 = %d个板块，%d只成分股，耗时 %.1f 秒",
                       industry_count, concept_count, total_sectors, total_members, duration)

            return self._make_result(
                True,
                industry_count=industry_count,
                concept_count=concept_count,
                sector_count=total_sectors,
                industry_members=industry_members,
                concept_members=concept_members,
                total_members=total_members,
                duration=round(duration, 2),
                started_at=started_at_str,
            )

        except asyncio.CancelledError:
            logger.warning("sync_sectors_from_em 被取消")
            raise
        except Exception as e:
            logger.error("从东方财富同步板块失败: %s", e, exc_info=True)
            return self._make_result(False, error=str(e), started_at=started_at_str)

    # ==================================================================
    # 10.9: _is_record_changed() — 增量同步判断
    # ==================================================================

    def _is_record_changed(self, table: str, pk: str, data: Dict,
                           existing: Dict = None) -> bool:
        """
        判断记录是否发生变化（用于增量同步）

        比较关键字段：
        - stocks: name, status, industry_sw, market_cap
        - sectors: member_count, description
        - sector_members: weight, is_current

        如果现有记录不存在或关键字段有变化 → 返回 True
        否则 → 返回 False（跳过更新）

        Args:
            table: 表名 ('stocks', 'sectors', 'sector_members')
            pk: 主键值
            data: 新数据字典
            existing: 现有记录字典（若为 None 则查询数据库）

        Returns:
            bool: 是否需要更新
        """
        # 如果没有现有记录，需要插入
        if existing is None:
            return True

        if table == 'stocks':
            # 比较关键字段
            fields_to_check = ['name', 'status', 'industry_sw', 'industry_csrc']
            for field in fields_to_check:
                new_val = data.get(field)
                old_val = existing.get(field)
                if new_val != old_val and not (new_val is None and old_val is None):
                    return True

            # market_cap 允许一定误差（浮点数比较）
            new_cap = data.get('market_cap')
            old_cap = existing.get('market_cap')
            if new_cap is not None and old_cap is not None:
                if abs(float(new_cap) - float(old_cap)) > max(float(old_cap) * 0.01, 10000):
                    return True

            return False

        elif table == 'sectors':
            # 比较关键字段
            fields_to_check = ['member_count', 'description', 'sector_name']
            for field in fields_to_check:
                new_val = data.get(field)
                old_val = existing.get(field)
                if new_val != old_val and not (new_val is None and old_val is None):
                    return True

            return False

        elif table == 'sector_members':
            # 比较关键字段
            fields_to_check = ['weight', 'is_current']
            for field in fields_to_check:
                new_val = data.get(field)
                old_val = existing.get(field)
                if new_val != old_val and not (new_val is None and old_val is None):
                    return True

            return False

        else:
            # 未知表名，默认需要更新
            logger.warning("_is_record_changed: 未知表名 %s，默认返回 True", table)
            return True

    # ==================================================================
    # 10.10: initialize_on_first_run() — 首次启动自动初始化
    # ==================================================================

    async def initialize_on_first_run(self) -> Dict:
        """
        首次启动时的自动初始化流程

        检查逻辑：
        1. 查询 stocks 表是否为空
        2. 若为空，执行完整初始化：
           a. 同步全A股票（source=akshare，约5532只）
           b. 同步沪深300+中证500成分股
           c. 同步ETF列表
           d. 同步可转债列表
           e. 同步通达信板块数据
        3. 若非首次启动，仅检查数据新鲜度
        4. 返回初始化状态报告
        """
        started_at = datetime.now()
        started_at_str = started_at.strftime('%Y-%m-%d %H:%M:%S')

        logger.info("检查是否需要首次初始化...")

        # 检查 stocks 表是否有数据
        # 通过查询 stocks 表的记录数来判断
        try:
            import sqlite3
            conn = sqlite3.connect(self._storage.db_path)
            cursor = conn.execute("SELECT COUNT(*) FROM stocks")
            stock_count = cursor.fetchone()[0]
            conn.close()

            is_first_run = (stock_count == 0)
            logger.info("stocks 表现有 %d 条记录，首次运行=%s", stock_count, is_first_run)

        except Exception as e:
            logger.warning("查询 stocks 表失败，假设非首次运行: %s", e)
            is_first_run = False
            stock_count = -1

        if not is_first_run:
            # 非首次运行，检查数据新鲜度
            logger.info("非首次运行，跳过完整初始化")

            # 注：数据新鲜度检查通过 data_source_contract.json 的 timeout_ms 和 on_unavailable 字段已部分覆盖，完整的新鲜度检查延后实现
            # 例如：检查最后更新时间是否超过 N 天

            return self._make_result(
                True,
                is_first_run=False,
                stock_count=stock_count,
                message="非首次运行，跳过完整初始化",
                started_at=started_at_str,
            )

        # 首次运行，执行完整初始化
        logger.info("检测到首次运行，开始完整初始化...")
        self._report_progress("🚀 首次运行，开始完整数据初始化...", 0, 5)

        results = {
            'is_first_run': True,
            'tasks': {},
            'errors': [],
        }

        tasks = [
            ('stocks_akshare', '同步全A股票', self.sync_stocks_by_source('akshare')),
            ('hs300_cs500', '同步沪深300+中证500', self.sync_hs300_cs500()),
            ('etf', '同步ETF列表', self.sync_etf_list()),
            ('cb', '同步可转债列表', self.sync_cb_list()),
            ('sectors_tdx', '同步通达信板块', self.sync_sectors_from_tdx()),
        ]

        completed_tasks = 0
        for task_key, task_name, coro in tasks:
            self._report_progress(task_name, completed_tasks + 1, len(tasks))

            try:
                result = await coro
                results['tasks'][task_key] = result

                if not result.get('success'):
                    err_msg = result.get('error', '未知错误')
                    results['errors'].append(f"{task_name}: {err_msg}")
                    logger.warning("%s 失败: %s", task_name, err_msg)
                else:
                    logger.info("%s 完成", task_name)

            except asyncio.CancelledError:
                logger.warning("初始化过程被取消")
                results['cancelled'] = True
                raise
            except Exception as e:
                logger.error("%s 异常: %s", task_name, e, exc_info=True)
                results['tasks'][task_key] = {'success': False, 'error': str(e)}
                results['errors'].append(f"{task_name}: {e}")

            completed_tasks += 1

        duration = (datetime.now() - started_at).total_seconds()

        success_count = sum(1 for t in results['tasks'].values() if t.get('success'))

        logger.info("初始化完成: %d/%d 任务成功，耗时 %.1f 秒",
                   success_count, len(tasks), duration)

        final_result = self._make_result(
            success=(len(results['errors']) == 0),
            is_first_run=True,
            tasks_completed=success_count,
            tasks_total=len(tasks),
            errors=results['errors'],
            task_details=results['tasks'],
            duration=round(duration, 2),
            started_at=started_at_str,
        )

        self._report_progress(f"✅ 初始化完成 ({success_count}/{len(tasks)} 任务成功)",
                             len(tasks), len(tasks))

        return final_result

    # ==================================================================
    # 辅助方法：full_sync() — 全量同步入口
    # ==================================================================

    async def full_sync(self, sources: List[str] = None) -> Dict:
        """
        执行全量数据同步

        Args:
            sources: 要同步的数据源列表（默认全部）
                ['stocks_akshare', 'hs300_cs500', 'etf', 'cb',
                 'sectors_tdx', 'sects_ths', 'sects_em']

        Returns:
            各个同步任务的汇总结果
        """
        started_at = datetime.now()
        started_at_str = started_at.strftime('%Y-%m-%d %H:%M:%S')

        if sources is None:
            sources = self.DEFAULT_SYNC_SOURCES

        logger.info("开始全量数据同步，任务列表: %s", sources)
        self._report_progress("🔄 开始全量数据同步...", 0, len(sources))

        results = {
            'sources': sources,
            'tasks': {},
            'errors': [],
            'summary': {
                'success_count': 0,
                'fail_count': 0,
                'total_duration': 0,
            },
        }

        # 任务映射表
        task_map = {
            'stocks_akshare': lambda: self.sync_stocks_by_source('akshare'),
            'stocks_tq': lambda: self.sync_stocks_by_source('tq_dll'),
            'hs300_cs500': self.sync_hs300_cs500,
            'etf': self.sync_etf_list,
            'cb': self.sync_cb_list,
            'sectors_tdx': self.sync_sectors_from_tdx,
            'sects_ths': self.sync_sectors_from_ths,
            'sects_em': self.sync_sectors_from_em,
            'sector_indices': self.sync_sector_indices,
        }

        completed = 0
        for source in sources:
            task_func = task_map.get(source)
            if not task_func:
                logger.warning("未知的数据源任务: %s，跳过", source)
                results['errors'].append(f"未知任务: {source}")
                continue

            task_name = self._get_task_display_name(source)
            self._report_progress(f"正在执行: {task_name}", completed + 1, len(sources))

            try:
                result = await task_func()
                results['tasks'][source] = result

                if result.get('success'):
                    results['summary']['success_count'] += 1
                    logger.info("✅ %s 完成", task_name)
                else:
                    results['summary']['fail_count'] += 1
                    err_msg = result.get('error', '未知错误')
                    results['errors'].append(f"{task_name}: {err_msg}")
                    logger.warning("❌ %s 失败: %s", task_name, err_msg)

            except asyncio.CancelledError:
                logger.warning("full_sync 被取消")
                results['cancelled'] = True
                raise
            except Exception as e:
                logger.error("❌ %s 异常: %s", task_name, e, exc_info=True)
                results['tasks'][source] = {'success': False, 'error': str(e)}
                results['summary']['fail_count'] += 1
                results['errors'].append(f"{task_name}: {e}")

            completed += 1

        duration = (datetime.now() - started_at).total_seconds()
        results['summary']['total_duration'] = round(duration, 2)

        logger.info("全量同步完成: %d 成功 / %d 失败，耗时 %.1f 秒",
                   results['summary']['success_count'],
                   results['summary']['fail_count'],
                   duration)

        self._report_progress(f"✅ 全量同步完成 ({results['summary']['success_count']}/{len(sources)})",
                             len(sources), len(sources))

        return self._make_result(
            success=(results['summary']['fail_count'] == 0),
            summary=results['summary'],
            task_results=results['tasks'],
            errors=results['errors'] if results['errors'] else None,
            duration=duration,
            started_at=started_at_str,
        )

    @staticmethod
    def _get_task_display_name(source: str) -> str:
        """获取任务的显示名称"""
        names = {
            'stocks_akshare': '同步全A股票（AKShare）',
            'stocks_tq': '同步全A股票（TQ DLL）',
            'hs300_cs500': '同步沪深300+中证500',
            'etf': '同步ETF列表',
            'cb': '同步可转债列表',
            'sectors_tdx': '同步通达信板块',
            'sects_ths': '同步同花顺概念板块',
            'sects_em': '同步东方财富板块',
            'sector_indices': '同步板块指数',
        }
        return names.get(source, source)

    # ------------------------------------------------------------------
    # 数据校准与统一查询（Task 8）
    # ------------------------------------------------------------------

    @staticmethod
    def _is_in_trading_hours() -> bool:
        """判断当前是否在 A 股交易时段内（9:30-11:30, 13:00-15:00）。"""
        from datetime import datetime as _dt
        now = _dt.now()
        # 周末非交易日
        if now.weekday() >= 5:
            return False
        t = now.hour * 100 + now.minute
        return (930 <= t < 1130) or (1300 <= t < 1500)

    def pre_market_calibration(self, symbols: List[str] = None) -> dict:
        """开盘前数据校准流程。

        委托给 ``services.data_query.pre_market_calibration`` 执行本地只读检查，
        不发起网络请求，也不做静默数据补齐。

        Args:
            symbols: 待校准的标的列表；为 ``None`` 时返回空报告。

        Returns:
            dict: 校准报告。
        """
        return pre_market_calibration(self._storage, self._data_query, symbols)

# === MarketDataPort ===


class MarketDataPort(ABC):
    """市场数据端口抽象接口（公式计算层通过此接口获取标量数据）。

    职责：提供财务标量和行情标量数据访问。
    禁止：公式计算层直接调用 tq_adapter / 任何 Provider。
    """

    @abstractmethod
    async def get_financial_scalar(self, symbol: str, field: str) -> Optional[float]:
        """获取财务标量数据（如净资产收益率、每股收益等）。

        Args:
            symbol: 标的代码。
            field: 字段名（已映射后的实际字段名）。

        Returns:
            Optional[float]: 字段值；不可用或不存在时返回 None。
        """
        pass

    @abstractmethod
    async def get_market_scalar(self, symbol: str, field: str) -> Optional[float]:
        """获取行情标量数据（如现价、涨跌幅、成交量等）。

        Args:
            symbol: 标的代码。
            field: 字段名。

        Returns:
            Optional[float]: 字段值；不可用或不存在时返回 None。
        """
        pass

    @abstractmethod
    async def get_financial_scalars_batch(self, symbols: list, field: str) -> Dict[str, Optional[float]]:
        """批量获取财务标量。

        Args:
            symbols: 标的代码列表。
            field: 字段名。

        Returns:
            Dict[str, Optional[float]]: {symbol: value}，缺失字段返回 None。
        """
        pass

    @abstractmethod
    async def get_market_scalars_batch(self, symbols: list, field: str) -> Dict[str, Optional[float]]:
        """批量获取行情标量。

        Args:
            symbols: 标的代码列表。
            field: 字段名。

        Returns:
            Dict[str, Optional[float]]: {symbol: value}，缺失字段返回 None。
        """
        pass


def _to_float(value: Any) -> Optional[float]:
    """安全转换为 float，失败返回 None。"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class TqAdapterMarketDataPort(MarketDataPort):
    """MarketDataPort 的 TqAdapter 适配器实现。

    包装 TqAdapter，将其方法适配为 MarketDataPort 接口。
    内部通过 tq_adapter.get_financial_data / get_market_snapshot 获取数据，
    对评估器层屏蔽具体数据源实现。
    """

    def __init__(self, tq_adapter):
        self._tq_adapter = tq_adapter

    async def get_financial_scalar(self, symbol: str, field: str) -> Optional[float]:
        if not self._tq_adapter or not symbol or not field:
            return None
        try:
            data = self._tq_adapter.get_financial_data([symbol], [field])
            if not data or not isinstance(data, dict):
                return None
            record = data.get(symbol)
            if record is None:
                return None
            if isinstance(record, dict):
                return _to_float(record.get(field))
            return _to_float(record)
        except Exception:
            return None

    async def get_market_scalar(self, symbol: str, field: str) -> Optional[float]:
        if not self._tq_adapter or not symbol or not field:
            return None
        try:
            snapshot = self._tq_adapter.get_market_snapshot([symbol])
            if not snapshot or not isinstance(snapshot, dict):
                return None
            record = snapshot.get(symbol)
            if record is None:
                return None
            if isinstance(record, dict):
                return _to_float(record.get(field))
            return _to_float(record)
        except Exception:
            return None

    async def get_financial_scalars_batch(
        self, symbols: list, field: str
    ) -> Dict[str, Optional[float]]:
        if not self._tq_adapter or not symbols or not field:
            return {s: None for s in (symbols or [])}
        try:
            data = self._tq_adapter.get_financial_data(list(symbols), [field])
        except Exception:
            return {s: None for s in symbols}
        if not data or not isinstance(data, dict):
            return {s: None for s in symbols}
        result: Dict[str, Optional[float]] = {}
        for s in symbols:
            record = data.get(s)
            if record is None:
                result[s] = None
                continue
            if isinstance(record, dict):
                result[s] = _to_float(record.get(field))
            else:
                result[s] = _to_float(record)
        return result

    async def get_market_scalars_batch(
        self, symbols: list, field: str
    ) -> Dict[str, Optional[float]]:
        if not self._tq_adapter or not symbols or not field:
            return {s: None for s in (symbols or [])}
        try:
            snapshot = self._tq_adapter.get_market_snapshot(list(symbols))
        except Exception:
            return {s: None for s in symbols}
        if not snapshot or not isinstance(snapshot, dict):
            return {s: None for s in symbols}
        result: Dict[str, Optional[float]] = {}
        for s in symbols:
            record = snapshot.get(s)
            if record is None:
                result[s] = None
                continue
            if isinstance(record, dict):
                result[s] = _to_float(record.get(field))
            else:
                result[s] = _to_float(record)
        return result

# === 备选池解析层（自 services/candidate_pool.py 合并）===

# Task 23.4: DZH 全局配置文件路径（PoolLoaded 事件未携带时回退直接加载，消除跨层 import）
_CONFIG_DIR = Path(__file__).parent.parent / "config"


# ═══════════════════════════════════════════════════════════════
# 备选池解析器（原 candidate_pool_resolver.py）
# ═══════════════════════════════════════════════════════════════

# 统一备选池解析器 - 支持 spinfo.type 0-7 全部枚举
#
# 职责：
# - 解析 XML 配置中的 spinfo.type 参数
# - 根据不同类型从对应数据源获取股票列表
# - 提供统一的缓存和降级机制


class CandidatePoolResolver:
    """统一备选池解析器 - 支持 type 0-7 全部枚举

    职责：
    - 解析 XML 配置中的 spinfo.type 参数
    - 根据不同类型从对应数据源获取股票列表
    - 提供统一的缓存和降级机制

    type 枚举说明：
        0 - 自设监控品种（显式股票列表或自定义板块引用）
        1 - 沪深300 + 中证500
        2 - 所有A股
        3 - 自选股（通达信客户端自选）
        4 - 自定义板块（用户在客户端创建的板块）
        5 - 板块指数（行业/概念等板块成分股展开）
        6 - ETF基金
        7 - 可转债
    """

    # 缓存TTL配置（单位：秒）
    CACHE_TTL: Dict[int, int] = {
        0: 0,       # type=0 不缓存（静态数据，每次实时解析）
        1: 86400,   # type=1 沪深300+中证500：1天
        2: 300,     # type=2 所有A股：5分钟
        3: 30,      # type=3 自选股：30秒
        4: 300,     # type=4 自定义板块：5分钟
        5: 3600,    # type=5 板块指数：1小时
        6: 3600,    # type=6 ETF基金：1小时
        7: 3600,    # type=7 可转债：1小时
    }

    # 支持的 type 范围
    VALID_TYPES = range(0, 8)

    # 标准分类白名单：白名单内的选择条目在设计时不转换，保持原有格式；
    # 白名单外的条目（如 'filter', 'query', 'computed', 'concept_sector' 等）
    # 在设计时展开为显式股票代码列表。
    STANDARD_CATEGORIES = {'concept', 'industry', 'index', 'style', 'region', 'favorite', 'custom'}

    def __init__(
        self,
        storage: Any,
        providers: Dict[str, Any],
        bus: Optional[EventBus] = None,
    ):
        """初始化备选池解析器。

        Args:
            storage: Storage 实例（用于数据库操作，如 user_blocks 表查询）
            providers: 数据源提供者字典 {
                'tq_dll': TqDllProvider 实例,
                'akshare': AkShareProvider 实例,
                ...
            }
            bus: 可选 ``EventBus``；非 None 时订阅 ``PoolLoaded`` 事件，
                 解析备选池节点后发布携带初始股票列表的 ``TickReceived`` 事件。
                 None 时不订阅（保持向后兼容）。
        """
        self._storage = storage
        self._providers = providers
        self._bus: Optional[EventBus] = bus
        self._cache: Dict[int, Tuple[List[Dict], datetime]] = {}
        self._cache_lock = asyncio.Lock()
        # Task 23.4: 通过 PoolLoaded 事件接收 DZH 全局配置，消除 converters.dzh 跨层 import
        self._market_mappings: Optional[List[Dict]] = None
        self._reload_schedule: Optional[Dict] = None
        if bus is not None:
            bus.subscribe(PoolLoaded, self._on_pool_loaded)

    # ------------------------------------------------------------------
    # 事件订阅（Task 4.2：PoolLoaded → 解析备选池 → 发布 TickReceived）
    # ------------------------------------------------------------------

    def _on_pool_loaded(self, event: PoolLoaded) -> None:
        """``PoolLoaded`` 事件处理器：解析备选池节点并发布初始股票列表。

        扫描 ``event.pool_config["nodes"]`` 中的备选池节点
       （DZH type=202 / TDX type=7，即 ``CandidatePoolNode``），
        对每个节点调用 ``self.resolve(spinfo_type, ...)`` 获取股票列表，
        最后发布 ``TickReceived(tick_data={"initial_codes": [...]}, code="", ts=0.0)``
        事件，供 TickBar 模块订阅。

        异常用 try/except 包裹，``logger.warning`` 不向上抛，
        与 ``EventBus.publish`` 异常隔离风格一致。
        """
        try:
            pool_config = event.pool_config or {}
            # Task 23.4: 缓存 DZH 全局配置（market_mappings / reload_schedule），
            # 消除 from ..converters import 跨层违规
            if pool_config.get("market_mappings") is not None:
                self._market_mappings = pool_config["market_mappings"]
            if pool_config.get("reload_schedule") is not None:
                self._reload_schedule = pool_config["reload_schedule"]
            nodes = pool_config.get("nodes") or []
            if not nodes:
                return
            initial_codes: List[str] = []
            for n in nodes:
                try:
                    node_type = n.get("type")
                    # 仅处理备选池节点：DZH type=202 / TDX type=7
                    if node_type not in (202, 7, "202", "7"):
                        continue
                    spinfo = n.get("spinfo") or {}
                    spinfo_type = spinfo.get("type", 0)
                    try:
                        spinfo_type = int(spinfo_type)
                    except (TypeError, ValueError):
                        continue
                    if spinfo_type not in self.VALID_TYPES:
                        continue
                    kwargs: Dict[str, Any] = {}
                    if spinfo_type == 0:
                        stks = spinfo.get("stks") or n.get("stks")
                        if stks:
                            kwargs["stks"] = stks
                        customblockname = spinfo.get("customblockname") or n.get("customblockname")
                        if customblockname:
                            kwargs["customblockname"] = customblockname
                    elif spinfo_type == 4:
                        customblockname = spinfo.get("customblockname") or n.get("customblockname")
                        if customblockname:
                            kwargs["customblockname"] = customblockname
                    # resolve 是 async，借助 _run_coro_safely 在同步 handler 中执行
                    stocks = self._run_resolve_sync(spinfo_type, **kwargs)
                    for s in stocks:
                        code = s.get("code") if isinstance(s, dict) else None
                        if code:
                            initial_codes.append(str(code))
                except Exception as ex:
                    logger.warning(
                        "CandidatePoolResolver._on_pool_loaded 解析节点失败: %s", ex
                    )
            if not initial_codes:
                return
            # 去重保持稳定顺序
            seen = set()
            unique_codes: List[str] = []
            for c in initial_codes:
                if c not in seen:
                    seen.add(c)
                    unique_codes.append(c)
            tick_data: Dict[str, Any] = {"initial_codes": unique_codes}
            self._bus.publish(
                TickReceived(tick_data=tick_data, code="", ts=0.0)
            )
            logger.info(
                "PoolLoaded → 发布 TickReceived(initial_codes=%d codes)",
                len(unique_codes),
            )
        except Exception as ex:
            logger.warning(
                "CandidatePoolResolver._on_pool_loaded 失败: %s", ex
            )

    # ------------------------------------------------------------------
    # Task 23.4: DZH 全局配置访问（通过 PoolLoaded 事件缓存，回退直接加载）
    # ------------------------------------------------------------------

    def _ensure_market_mappings(self) -> List[Dict]:
        """返回 DZH attrtext 市场映射表。

        优先使用 PoolLoaded 事件缓存的配置；事件未接收时回退从
        ``config/dzh_market_mappings.json`` 直接加载（消除 converters.dzh 跨层 import）。
        """
        if self._market_mappings is not None:
            return self._market_mappings
        cfg_path = _CONFIG_DIR / "data" / "dzh_market_mappings.json"
        try:
            cfg = json.loads(cfg_path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("无法加载 %s，attrtext 映射将使用空配置", cfg_path, exc_info=True)
            cfg = {}
        self._market_mappings = cfg.get("mappings", [])
        return self._market_mappings

    def _ensure_reload_schedule(self) -> Dict:
        """返回 DZH reload 调度配置。

        优先使用 PoolLoaded 事件缓存的配置；事件未接收时回退从
        ``config/dzh_reload_schedule.json`` 直接加载（fail-fast）。
        """
        if self._reload_schedule is not None:
            return self._reload_schedule
        cfg_path = _CONFIG_DIR / "runtime" / "dzh_reload_schedule.json"
        try:
            self._reload_schedule = json.loads(cfg_path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as ex:
            raise RuntimeError(
                f"无法加载配置表 {cfg_path}: {ex}（fail-fast：禁止回退硬编码 reload 配置）"
            ) from ex
        return self._reload_schedule

    def _run_resolve_sync(self, spinfo_type: int, **kwargs) -> List[Dict]:
        """在同步 handler 中安全执行 async ``resolve``。

        复用事件循环或新建临时循环；失败时返回空列表。
        """
        try:
            coro = self.resolve(spinfo_type, **kwargs)
        except Exception as ex:
            logger.warning(
                "CandidatePoolResolver._run_resolve_sync 构造协程失败 (type=%d): %s",
                spinfo_type, ex,
            )
            return []
        try:
            running = asyncio.get_event_loop()
            if running.is_running():
                # 已有运行中的循环 — 在线程中执行避免阻塞
                result: List[Dict] = []
                exc: List[BaseException] = []

                def _runner():
                    try:
                        result.append(asyncio.run(coro))
                    except BaseException as e:  # noqa: BLE001
                        exc.append(e)

                import threading as _threading
                t = _threading.Thread(target=_runner)
                t.start()
                t.join()
                if exc:
                    logger.warning(
                        "CandidatePoolResolver._run_resolve_sync 协程失败 (type=%d): %s",
                        spinfo_type, exc[0],
                    )
                    return []
                return result[0] if result else []
            return running.run_until_complete(coro)
        except RuntimeError:
            # 无事件循环 — 新建临时循环
            try:
                return asyncio.run(coro)
            except Exception as ex:
                logger.warning(
                    "CandidatePoolResolver._run_resolve_sync asyncio.run 失败 (type=%d): %s",
                    spinfo_type, ex,
                )
                return []
        except Exception as ex:
            logger.warning(
                "CandidatePoolResolver._run_resolve_sync 失败 (type=%d): %s",
                spinfo_type, ex,
            )
            return []

    # ------------------------------------------------------------------
    # 主调度方法
    # ------------------------------------------------------------------

    async def resolve(self, spinfo_type: int, **kwargs) -> List[Dict]:
        """解析备选池配置，返回股票列表。

        Args:
            spinfo_type: 备选池类型 (0-7)
            **kwargs:
                - customblockname: type=4/0(形式B)时的板块名称
                - stks: type=0(形式A)时的显式股票列表 [{setcode, code}, ...]
                - force_refresh: 是否强制刷新（忽略缓存）

        Returns:
            [{'setcode': int, 'code': str, 'name': str}, ...]

        Raises:
            ValueError: 无效的 spinfo_type
        """
        if spinfo_type not in self.VALID_TYPES:
            raise ValueError(
                f"无效的 spinfo_type={spinfo_type}，有效范围为 0-{self.VALID_TYPES.stop - 1}"
            )

        force_refresh = kwargs.get('force_refresh', False)

        # type=0 不走缓存（静态数据，每次实时解析）
        if spinfo_type == 0:
            return await self.resolve_type_0(
                stks=kwargs.get('stks'),
                customblockname=kwargs.get('customblockname'),
            )

        # 检查缓存
        if not force_refresh:
            cached = self._get_from_cache(spinfo_type)
            if cached is not None:
                logger.debug("resolve(type=%d): 缓存命中，返回 %d 条记录", spinfo_type, len(cached))
                return cached

        # 路由到对应的解析方法
        resolver_map = {
            1: self._do_resolve_type_1,
            2: self._do_resolve_type_2,
            3: self._do_resolve_type_3,
            4: lambda **kw: self._do_resolve_type_4(customblockname=kw.get('customblockname')),
            5: self._do_resolve_type_5,
            6: self._do_resolve_type_6,
            7: self._do_resolve_type_7,
        }

        resolver = resolver_map.get(spinfo_type)
        if resolver is None:
            logger.warning("resolve(type=%d): 未找到对应的解析方法", spinfo_type)
            return []

        result = await resolver(**kwargs)

        # 写入缓存
        if result:
            self._set_cache(spinfo_type, result)

        logger.info("resolve(type=%d): 返回 %d 条记录", spinfo_type, len(result))
        return result

    # ------------------------------------------------------------------
    # DZH attrtext selections 解析（配置表驱动）
    # ------------------------------------------------------------------

    async def resolve_attrtext_selections(self, selections: List[Dict]) -> List[Dict]:
        """将 DZH attrtext 解析后的 selections 映射为 mock/akshare/tq 可识别的代码列表。

        Args:
            selections: parse_attrtext_selections() 返回的列表，每项含 type/label/code。

        Returns:
            [{'setcode': int, 'code': str, 'name': str}, ...]
        """
        if not selections:
            return []

        mappings = self._ensure_market_mappings()
        compiled = {}
        for idx, m in enumerate(mappings):
            pattern = m.get("pattern")
            if pattern:
                try:
                    compiled[idx] = re.compile(pattern)
                except Exception as e:
                    logger.warning("映射表正则不能编译 %r: %s", pattern, e)

        all_stocks = []
        for sel in selections:
            sel_type = sel.get("type")
            code = sel.get("code", "")
            default_codes = None
            for idx, pat in compiled.items():
                if pat.match(code):
                    default_codes = mappings[idx].get("default_codes")
                    break

            if sel_type == "stock":
                parsed = self._parse_stock_code(code)
                if parsed:
                    parsed.setdefault("name", sel.get("label", ""))
                    all_stocks.append(parsed)
            elif sel_type == "market":
                market_id = (default_codes[0] if default_codes else None) or code
                all_stocks.extend(await self._resolve_market_id_to_codes(market_id))
            elif sel_type == "group":
                group_name = sel.get("label") or (code[4:] if code.startswith("BLK-") else code)
                all_stocks.extend(await self._fetch_user_group_members(group_name))
            elif sel_type in ("concept_sector", "industry_sector", "classic_sector", "sector"):
                sector_ids = default_codes or []
                found_members: List[Dict] = []
                if sector_ids:
                    for sid in sector_ids:
                        members = await self._fetch_sector_members(sid)
                        found_members.extend(members)
                else:
                    # panel.js 写入的 BLK-{分类标签}{板块名} 格式（无数字ID）：
                    # 按 sector_name + category 直接查数据库 sectors/sector_members。
                    # panel.js 的板块树数据来自 DB API，故 DB 为权威数据源。
                    sector_name = sel.get("sector_name") or sel.get("label", "")
                    category = sel.get("category")
                    if sector_name:
                        found_members = self._fetch_sector_members_from_db(sector_name, category)
                    else:
                        logger.warning(
                            "resolve_attrtext_selections: 板块选择无法解析，缺少 sector_name: %r",
                            code,
                        )
                if not found_members:
                    logger.warning(
                        "resolve_attrtext_selections: 板块选择 %r 解析为空（数据库无匹配板块或成分股）",
                        code,
                    )
                all_stocks.extend(found_members)
            # raw / 未知类型不解析

        return self._dedup_stock_list(all_stocks)

    # ------------------------------------------------------------------
    # 设计时转换：非标准选择 → 显式股票代码集（Task 5）
    # ------------------------------------------------------------------

    async def convert_to_code_set(self, selections: List[Dict]) -> List[Dict]:
        """将非标准选择在设计时转换为显式股票代码集。

        标准分类（concept/industry/index/style/region/favorite/custom）保持不变，
        非标准选择展开为显式 stks 列表，转为 type=0 形式A。

        Args:
            selections: 选择列表，每个元素为 {'type': 'concept_sector', 'value': '锂电池', ...}
                        或 {'type': 'custom', 'value': '某种筛选', ...}

        Returns:
            转换后的选择列表，非标准选择变为 {'type': 'explicit', 'stks': [{setcode, code, name}, ...]}
        """
        if not selections:
            return []

        result: List[Dict] = []
        for sel in selections:
            sel_type = sel.get('type', '')

            # 标准分类白名单内的条目保持不变
            if sel_type in self.STANDARD_CATEGORIES:
                result.append(sel)
                continue

            # 非标准选择：展开为显式股票代码列表
            try:
                stocks = await self.resolve_attrtext_selections([sel])
                if stocks:
                    explicit_stks = [
                        {
                            'setcode': s.get('setcode', 0),
                            'code': s.get('code', ''),
                            'name': s.get('name', ''),
                        }
                        for s in stocks
                        if s.get('code')
                    ]
                    result.append({
                        'type': 'explicit',
                        'stks': explicit_stks,
                    })
                    logger.debug(
                        "convert_to_code_set: 非标准选择 type=%s 展开为 %d 只股票",
                        sel_type, len(explicit_stks),
                    )
                else:
                    # 解析结果为空，保留原选择并记录警告
                    logger.warning(
                        "convert_to_code_set: 非标准选择 type=%s 解析为空，保留原选择",
                        sel_type,
                    )
                    result.append(sel)
            except Exception as e:
                logger.warning(
                    "convert_to_code_set: 解析非标准选择 type=%s 失败: %s，保留原选择",
                    sel_type, e,
                )
                result.append(sel)

        return result

    async def _resolve_market_id_to_codes(self, market_id: str) -> List[Dict]:
        """根据内部市场短 ID 从 TQ/AKShare 获取股票列表。"""
        market_to_list_type = {
            "sh_a": 7,
            "sz_a": 8,
            "bj_a": 53,
            "gem": 51,
            "kcb": 52,
            "sme": 8,
            "all_a": 5,
            "sector_index": 10,
        }
        list_type = market_to_list_type.get(market_id)

        tq_provider = self._providers.get("tq_dll")
        ak_provider = self._providers.get("akshare")

        if list_type is not None and tq_provider and hasattr(tq_provider, "get_stock_list"):
            try:
                return await tq_provider.get_stock_list(list_type=list_type)
            except Exception as e:
                logger.warning("_resolve_market_id_to_codes: TQ list_type=%s 失败: %s", list_type, e)

        # AKShare 降级（示例，若 provider 支持则调用）
        ak_method_map = {
            "sh_a": "get_sh_a_stocks",
            "sz_a": "get_sz_a_stocks",
            "bj_a": "get_bj_a_stocks",
        }
        method_name = ak_method_map.get(market_id)
        if method_name and ak_provider and hasattr(ak_provider, method_name):
            try:
                method = getattr(ak_provider, method_name)
                return await method() if asyncio.iscoroutinefunction(method) else method()
            except Exception as e:
                logger.warning("_resolve_market_id_to_codes: AKShare %s 失败: %s", method_name, e)

        logger.warning("_resolve_market_id_to_codes: 无法解析市场 %s", market_id)
        return []

    def _dedup_stock_list(self, stocks: List[Dict]) -> List[Dict]:
        """按 setcode+code 去重。"""
        seen = set()
        result = []
        for s in stocks:
            code = s.get("code", "")
            setcode = s.get("setcode", 0)
            key = f"{setcode}:{code}"
            if code and key not in seen:
                seen.add(key)
                result.append(s)
        return result

    async def _fetch_user_group_members(self, group_name: str) -> List[Dict]:
        """获取 DZH 自选组（BLK-自选股N）的成员列表。

        优先从 storage 的 user_blocks 查询，未命中则尝试 TQ 的 user_sector
        中的 custom_blocks。

        Args:
            group_name: 组名称，如 "自选股1"。

        Returns:
            股票列表 [{setcode, code, name}, ...]
        """
        if not group_name:
            return []

        # 优先：本地文件数据源（自选股文件）
        local_provider = self._providers.get('local_file')
        if local_provider and hasattr(local_provider, 'get_user_sector'):
            try:
                import asyncio
                result = local_provider.get_user_sector()
                if asyncio.iscoroutine(result):
                    result = await result
                # 检查自选股分组（BLK-自选股N 中的 N 对应自选股分组）
                custom_blocks = result.get('custom_blocks', [])
                for block in custom_blocks:
                    if block.get('block_name') == group_name or block.get('block_code') == group_name:
                        members = block.get('members', [])
                        if members:
                            logger.info("_fetch_user_group_members: 本地文件获取自选组 '%s'，%d 只", group_name, len(members))
                            return members
                # 如果 group_name 是 "自选股1" 等，也检查 favorites
                favorites = result.get('favorites', [])
                if favorites and ('自选股' in group_name or group_name == 'ZXG'):
                    logger.info("_fetch_user_group_members: 本地文件获取自选股 %d 只", len(favorites))
                    return favorites
            except Exception as e:
                logger.debug("_fetch_user_group_members: 本地文件解析失败: %s，降级到 storage/TQ", e)

        # 降级：storage 查询（保留原有逻辑）
        try:
            block_data = self._storage.get_user_block(group_name)
            if block_data:
                members = block_data.get("members", [])
                result = []
                for m in members:
                    stock_code = m.get("stock_code", "")
                    name = m.get("name", "")
                    parsed = self._parse_stock_code(stock_code)
                    if parsed:
                        parsed["name"] = name
                        result.append(parsed)
                return result
        except Exception as e:
            logger.debug("_fetch_user_group_members: storage 查询失败: %s", e)

        tq_provider = self._providers.get("tq_dll")
        if tq_provider and hasattr(tq_provider, "get_user_sector"):
            try:
                user_sector = await tq_provider.get_user_sector()
                for block in user_sector.get("custom_blocks", []):
                    if block.get("block_name") == group_name or block.get("block_code") == group_name:
                        members = block.get("members", [])
                        result = []
                        for m in members:
                            if isinstance(m, dict):
                                stock_code = m.get("stock_code", m.get("code", ""))
                                name = m.get("name", "")
                            else:
                                stock_code = str(m)
                                name = ""
                            parsed = self._parse_stock_code(stock_code)
                            if parsed:
                                parsed["name"] = name
                                result.append(parsed)
                        return result
            except Exception as e:
                logger.warning("_fetch_user_group_members: TQ 获取失败: %s", e)

        return []

    # ------------------------------------------------------------------
    # type=0: 自设监控品种（最关键的类型）
    # ------------------------------------------------------------------

    async def resolve_type_0(self, stks=None, customblockname=None) -> List[Dict]:
        """解析 type=0: 自设监控品种。

        支持两种XML格式：
        - 形式A: <spinfo type="0"/> + <stk setcode="1" code="600000"/>...
          → 直接返回stks列表，补充名称信息
        - 形式B: <spinfo type="0" customblockname="XXX"/>
          → 从user_blocks表查找customblockname对应的成员

        Args:
            stks: 显式股票列表 [{setcode: int, code: str}, ...]
            customblockname: 板块名称（用于形式B）

        Returns:
            股票列表 [{setcode, code, name}, ...]
        """
        # 形式A: 提供了显式股票列表
        if stks and len(stks) > 0:
            return await self._resolve_type_0_form_a(stks)

        # 形式B: 通过自定义板块名称查找
        if customblockname:
            return await self._resolve_type_0_form_b(customblockname)

        logger.warning("resolve_type_0: 未提供 stks 或 customblockname，返回空列表")
        return []

    async def _resolve_type_0_form_a(self, stks: List[Dict]) -> List[Dict]:
        """处理形式A：显式股票列表。

        直接使用 stks 列表，补充每只股票的名称信息。
        """
        result = []
        for stk in stks:
            setcode = stk.get('setcode')
            code = stk.get('code', '')
            if not code:
                continue

            name = stk.get('name', '')
            # 如果没有名称，尝试从数据库或数据源补充
            if not name:
                name = await self._fetch_stock_name(setcode, code)

            result.append({
                'setcode': setcode,
                'code': code,
                'name': name,
            })

        logger.debug("_resolve_type_0_form_a: 形式A，%d 只股票", len(result))
        return result

    async def _resolve_type_0_form_b(self, customblockname: str) -> List[Dict]:
        """处理形式B：通过自定义板块名称查找成员。

        从 storage.get_user_block(customblockname) 查找记录。
        """
        # 优先：本地文件数据源
        local_provider = self._providers.get('local_file')
        if local_provider and hasattr(local_provider, 'get_block_members'):
            try:
                import asyncio
                raw_codes = local_provider.get_block_members(customblockname)
                if asyncio.iscoroutine(raw_codes):
                    raw_codes = await raw_codes
                if raw_codes:
                    result = []
                    for rc in raw_codes:
                        parsed = self._parse_stock_code(rc)
                        if parsed:
                            parsed.setdefault('name', '')
                            result.append(parsed)
                    if result:
                        logger.info(
                            "_resolve_type_0_form_b: 本地文件获取板块 '%s'，%d 只成员",
                            customblockname,
                            len(result),
                        )
                        return result
            except Exception as e:
                logger.debug("_resolve_type_0_form_b: 本地文件解析失败: %s，降级到 storage", e)

        # 降级：storage 查询
        block_data = self._storage.get_user_block(customblockname)

        if block_data is None:
            logger.warning(
                "_resolve_type_0_form_b: 未找到自定义板块 '%s'，返回空列表",
                customblockname,
            )
            return []

        members = block_data.get('members', [])
        result = []
        for m in members:
            stock_code = m.get('stock_code', '')
            name = m.get('name', '')

            # 解析 stock_code 为 setcode + code
            parsed = self._parse_stock_code(stock_code)
            if parsed and name:
                result.append({
                    'setcode': parsed['setcode'],
                    'code': parsed['code'],
                    'name': name,
                })

        logger.debug(
            "_resolve_type_0_form_b: 形式B，板块 '%s' 有 %d 只成员",
            customblockname,
            len(result),
        )
        return result

    # ------------------------------------------------------------------
    # type=1: 沪深300 + 中证500
    # ------------------------------------------------------------------

    async def _do_resolve_type_1(self, **kwargs) -> List[Dict]:
        """解析 type=1: 沪深300+中证500成分股并集。

        解析链：数据库 → 本地文件 → AKShare → TQ DLL。
        缓存TTL: 1天。
        """
        # 1. 数据库优先：查 sector_members（沪深300/中证500板块）
        db_result = self._fetch_index_stocks_from_db()
        if db_result:
            logger.info("_do_resolve_type_1: 数据库获取到 %d 只沪深300+中证500", len(db_result))
            return db_result

        # 2. 降级：本地文件数据源
        local_provider = self._providers.get('local_file')
        if local_provider and hasattr(local_provider, 'get_stock_list_by_type'):
            try:
                import asyncio
                # list_type 映射：1->23+24
                local_list_types = [23, 24]
                merged = {}
                for llt in local_list_types:
                    result = local_provider.get_stock_list_by_type(llt)
                    if asyncio.iscoroutine(result):
                        result = await result
                    if result:
                        for s in result:
                            key = s.get('code', '')
                            if key:
                                merged[key] = s
                if merged:
                    result_list = list(merged.values())
                    logger.info("_do_resolve_type_1: 本地文件获取到 %d 条记录", len(result_list))
                    return result_list
            except Exception as e:
                logger.debug("_do_resolve_type_1: 本地文件解析失败: %s，降级到 AKShare/TQ", e)

        ak_provider = self._providers.get('akshare')
        tq_provider = self._providers.get('tq_dll')

        # 3. 降级：AKShare
        if ak_provider and hasattr(ak_provider, 'get_hs300_cs500_stocks'):
            try:
                stocks = await ak_provider.get_hs300_cs500_stocks()
                if stocks:
                    logger.info("_do_resolve_type_1: AKShare 获取到 %d 只沪深300+中证500", len(stocks))
                    return stocks
            except Exception as e:
                logger.warning("_do_resolve_type_1: AKShare 获取失败: %s，尝试降级", e)

        # 4. 降级：TQ DLL 分别获取沪深300和中证500
        if tq_provider and hasattr(tq_provider, 'get_stock_list'):
            try:
                hs300 = await tq_provider.get_stock_list(list_type=23)
                cs500 = await tq_provider.get_stock_list(list_type=24)

                merged = {}
                for s in hs300:
                    key = s.get('code', '')
                    if key:
                        merged[key] = s
                for s in cs500:
                    key = s.get('code', '')
                    if key:
                        merged[key] = s

                result = list(merged.values())
                logger.info("_do_resolve_type_1: TQ DLL 降级获取到 %d 只", len(result))
                return result
            except Exception as e:
                logger.warning("_do_resolve_type_1: TQ DLL 降级也失败: %s", e)

        logger.error("_do_resolve_type_1: 所有数据源均不可用")
        return []

    # ------------------------------------------------------------------
    # type=2: 所有A股
    # ------------------------------------------------------------------

    async def _do_resolve_type_2(self, **kwargs) -> List[Dict]:
        """解析 type=2: 所有A股。

        解析链：数据库 → 本地文件 → TQ DLL → AKShare。
        缓存TTL: 5分钟。
        """
        # 1. 数据库优先：查 stocks 表
        db_result = self._fetch_all_stocks_from_db()
        if db_result:
            logger.info("_do_resolve_type_2: 数据库获取到 %d 只A股", len(db_result))
            return db_result

        # 2. 降级：本地文件数据源
        local_provider = self._providers.get('local_file')
        if local_provider and hasattr(local_provider, 'get_stock_list_by_type'):
            try:
                import asyncio
                # list_type 映射：2->5
                result = local_provider.get_stock_list_by_type(5)
                if asyncio.iscoroutine(result):
                    result = await result
                if result:
                    logger.info("_do_resolve_type_2: 本地文件获取到 %d 条记录", len(result))
                    return result
            except Exception as e:
                logger.debug("_do_resolve_type_2: 本地文件解析失败: %s，降级到 TQ/AKShare", e)

        tq_provider = self._providers.get('tq_dll')
        ak_provider = self._providers.get('akshare')

        # 3. 降级：TQ DLL
        if tq_provider and hasattr(tq_provider, 'get_stock_list'):
            try:
                stocks = await tq_provider.get_stock_list(list_type=5)
                if stocks:
                    logger.info("_do_resolve_type_2: TQ DLL 获取到 %d 只A股", len(stocks))
                    return stocks
            except Exception as e:
                logger.warning("_do_resolve_type_2: TQ DLL 获取失败: %s，尝试降级", e)

        # 4. 降级：AKShare
        if ak_provider and hasattr(ak_provider, 'get_all_a_stocks'):
            try:
                stocks = await ak_provider.get_all_a_stocks()
                if stocks:
                    logger.info("_do_resolve_type_2: AKShare 降级获取到 %d 只A股", len(stocks))
                    return stocks
            except Exception as e:
                logger.warning("_do_resolve_type_2: AKShare 降级也失败: %s", e)

        logger.error("_do_resolve_type_2: 所有数据源均不可用")
        return []

    # ------------------------------------------------------------------
    # type=3: 自选股
    # ------------------------------------------------------------------

    async def _do_resolve_type_3(self, **kwargs) -> List[Dict]:
        """解析 type=3: 自选股（通达信客户端自选）。

        解析链：数据库 → 本地文件 → TQ DLL。
        缓存TTL: 30秒。
        """
        # 1. 数据库优先：查 user_blocks(user_block_members) 表
        db_result = self._fetch_favorites_from_db()
        if db_result:
            logger.info("_do_resolve_type_3: 数据库获取到 %d 只自选股", len(db_result))
            return db_result

        # 2. 降级：本地文件数据源
        local_provider = self._providers.get('local_file')
        if local_provider and hasattr(local_provider, 'get_user_sector'):
            try:
                import asyncio
                result = local_provider.get_user_sector()
                if asyncio.iscoroutine(result):
                    result = await result
                favorites = result.get('favorites', [])
                if favorites:
                    logger.info("_do_resolve_type_3: 本地文件获取到 %d 只自选股", len(favorites))
                    return favorites
            except Exception as e:
                logger.debug("_do_resolve_type_3: 本地文件解析失败: %s，降级到 TQ DLL", e)

        # 3. 降级：TQ DLL
        tq_provider = self._providers.get('tq_dll')

        if tq_provider and hasattr(tq_provider, 'get_user_sector'):
            try:
                user_sector = await tq_provider.get_user_sector()
                favorites = user_sector.get('favorites', [])
                logger.info("_do_resolve_type_3: 获取到 %d 只自选股", len(favorites))
                return favorites
            except Exception as e:
                logger.warning("_do_resolve_type_3: 获取自选股失败: %s", e)

        logger.warning("_do_resolve_type_3: 无法获取自选股，TQ DLL 可能未就绪")
        return []

    # ------------------------------------------------------------------
    # type=4: 自定义板块
    # ------------------------------------------------------------------

    async def _do_resolve_type_4(self, customblockname: str = None, **kwargs) -> List[Dict]:
        """解析 type=4: 自定义板块。

        解析链：数据库 → 本地文件 → storage → TQ DLL。
        缓存TTL: 5分钟。

        Args:
            customblockname: 板块名称（必需）
        """
        if not customblockname:
            logger.warning("_do_resolve_type_4: 未提供 customblockname")
            return []

        # 1. 数据库优先：查 user_blocks(block_name) + user_block_members
        db_result = self._fetch_custom_block_from_db(customblockname)
        if db_result:
            logger.info(
                "_do_resolve_type_4: 数据库获取板块 '%s'，%d 只成员",
                customblockname,
                len(db_result),
            )
            return db_result

        # 2. 降级：本地文件数据源
        local_provider = self._providers.get('local_file')
        if local_provider and hasattr(local_provider, 'get_block_members'):
            try:
                import asyncio
                raw_codes = local_provider.get_block_members(customblockname)
                if asyncio.iscoroutine(raw_codes):
                    raw_codes = await raw_codes
                if raw_codes:
                    result = []
                    for rc in raw_codes:
                        parsed = self._parse_stock_code(rc)
                        if parsed:
                            parsed.setdefault('name', '')
                            result.append(parsed)
                    if result:
                        logger.info("_do_resolve_type_4: 本地文件获取板块 '%s'，%d 只成员", customblockname, len(result))
                        return result
            except Exception as e:
                logger.debug("_do_resolve_type_4: 本地文件解析失败: %s，降级到 storage/TQ", e)

        # 3. 降级：storage 查询（保留原有逻辑，按 block_code 查询）
        block_data = self._storage.get_user_block(customblockname)
        if block_data is not None:
            members = block_data.get('members', [])
            result = []
            for m in members:
                stock_code = m.get('stock_code', '')
                name = m.get('name', '')
                parsed = self._parse_stock_code(stock_code)
                if parsed:
                    result.append({
                        'setcode': parsed['setcode'],
                        'code': parsed['code'],
                        'name': name or '',
                    })
            logger.info(
                "_do_resolve_type_4: 从 storage 获取板块 '%s'，%d 只成员",
                customblockname,
                len(result),
            )
            return result

        # 4. 降级：TQ 获取
        tq_provider = self._providers.get('tq_dll')
        if tq_provider and hasattr(tq_provider, 'get_user_sector'):
            try:
                user_sector = await tq_provider.get_user_sector()
                custom_blocks = user_sector.get('custom_blocks', [])
                for block in custom_blocks:
                    if block.get('block_name') == customblockname or block.get('block_code') == customblockname:
                        members = block.get('members', [])
                        logger.info(
                            "_do_resolve_type_4: 从TQ获取板块 '%s'，%d 只成员",
                            customblockname,
                            len(members),
                        )
                        return members
            except Exception as e:
                logger.warning("_do_resolve_type_4: 从TQ获取板块失败: %s", e)

        # 尝试通过 TQ 的 get_block_members 直接按代码获取
        if tq_provider and hasattr(tq_provider, 'get_block_members'):
            try:
                raw_codes = tq_provider.get_block_members(customblockname)
                if raw_codes:
                    result = []
                    for rc in raw_codes:
                        parsed = self._parse_tq_code(rc)
                        if parsed:
                            result.append(parsed)
                    logger.info(
                        "_do_resolve_type_4: 通过TQ get_block_members获取 '%s'，%d 只",
                        customblockname,
                        len(result),
                    )
                    return result
            except Exception as e:
                logger.warning("_do_resolve_type_4: TQ get_block_members 失败: %s", e)

        logger.warning("_do_resolve_type_4: 未找到自定义板块 '%s'", customblockname)
        return []

    # ------------------------------------------------------------------
    # type=5: 板块指数
    # ------------------------------------------------------------------

    async def _do_resolve_type_5(self, **kwargs) -> List[Dict]:
        """解析 type=5: 板块指数（展开所有板块的成分股）。

        解析链：数据库 → 本地文件 → AKShare → TQ DLL。
        若提供 customblockname，则按板块名查询特定板块；否则展开全部板块。
        缓存TTL: 1小时。
        """
        customblockname = kwargs.get('customblockname')

        # 1. 数据库优先：查 sectors(sector_name) + sector_members
        if customblockname:
            db_result = self._fetch_sector_members_from_db(customblockname)
            if db_result:
                logger.info(
                    "_do_resolve_type_5: 数据库获取板块 '%s'，%d 只成分股",
                    customblockname,
                    len(db_result),
                )
                return db_result

        # 2. 降级：本地文件数据源
        local_provider = self._providers.get('local_file')
        if local_provider and hasattr(local_provider, 'get_stock_list_by_type'):
            try:
                import asyncio
                # list_type 映射：5->10
                result = local_provider.get_stock_list_by_type(10)
                if asyncio.iscoroutine(result):
                    result = await result
                if result:
                    logger.info("_do_resolve_type_5: 本地文件获取到 %d 条记录", len(result))
                    return result
            except Exception as e:
                logger.debug("_do_resolve_type_5: 本地文件解析失败: %s，降级到 AKShare/TQ", e)

        ak_provider = self._providers.get('akshare')
        tq_provider = self._providers.get('tq_dll')

        # 3. 降级：AKShare
        if ak_provider and hasattr(ak_provider, 'get_sector_index_stocks'):
            try:
                sectors = await ak_provider.get_sector_index_stocks()
                if sectors:
                    # 展开所有板块的成分股为扁平列表
                    result = []
                    seen = set()  # 去重
                    for sector in sectors:
                        for member in sector.get('members', []):
                            code = member.get('code', '')
                            if code and code not in seen:
                                seen.add(code)
                                result.append(member)
                    logger.info("_do_resolve_type_5: AKShare 获取到 %d 个板块共 %d 只成分股",
                                len(sectors), len(result))
                    return result
            except Exception as e:
                logger.warning("_do_resolve_type_5: AKShare 获取失败: %s，尝试降级", e)

        # 4. 降级：TQ DLL
        if tq_provider and hasattr(tq_provider, 'get_sector_list'):
            try:
                sectors = await tq_provider.get_sector_list()
                result = []
                seen = set()

                for sector in sectors:
                    sector_code = sector.get('sector_code', '')
                    if not sector_code:
                        continue
                    try:
                        members_raw = tq_provider.get_block_members(sector_code)
                        for rc in members_raw:
                            parsed = self._parse_tq_code(rc)
                            if parsed:
                                code_key = parsed.get('code', '')
                                if code_key and code_key not in seen:
                                    seen.add(code_key)
                                    result.append(parsed)
                    except Exception:
                        continue

                logger.info("_do_resolve_type_5: TQ DLL 降级获取到 %d 只成分股", len(result))
                return result
            except Exception as e:
                logger.warning("_do_resolve_type_5: TQ DLL 降级也失败: %s", e)

        logger.error("_do_resolve_type_5: 所有数据源均不可用")
        return []

    # ------------------------------------------------------------------
    # type=6: ETF基金
    # ------------------------------------------------------------------

    async def _do_resolve_type_6(self, **kwargs) -> List[Dict]:
        """解析 type=6: ETF基金。

        解析链：数据库 → 本地文件 → AKShare → TQ DLL。
        缓存TTL: 1小时。
        """
        # 1. 数据库优先：查 stocks 表（ETF 代码前缀）
        db_result = self._fetch_etf_from_db()
        if db_result:
            logger.info("_do_resolve_type_6: 数据库获取到 %d 只ETF", len(db_result))
            return db_result

        # 2. 降级：本地文件数据源
        local_provider = self._providers.get('local_file')
        if local_provider and hasattr(local_provider, 'get_stock_list_by_type'):
            try:
                import asyncio
                # list_type 映射：6->31
                result = local_provider.get_stock_list_by_type(31)
                if asyncio.iscoroutine(result):
                    result = await result
                if result:
                    logger.info("_do_resolve_type_6: 本地文件获取到 %d 条记录", len(result))
                    return result
            except Exception as e:
                logger.debug("_do_resolve_type_6: 本地文件解析失败: %s，降级到 AKShare/TQ", e)

        ak_provider = self._providers.get('akshare')

        # 3. 降级：AKShare
        if ak_provider and hasattr(ak_provider, 'get_all_etf_list'):
            try:
                etfs = await ak_provider.get_all_etf_list()
                logger.info("_do_resolve_type_6: AKShare 获取到 %d 只ETF", len(etfs))
                return etfs
            except Exception as e:
                logger.warning("_do_resolve_type_6: 获取ETF列表失败: %s", e)

        # 4. 降级：TQ DLL list_type=31 (ETF基金)
        tq_provider = self._providers.get('tq_dll')
        if tq_provider and hasattr(tq_provider, 'get_stock_list'):
            try:
                etfs = await tq_provider.get_stock_list(list_type=31)
                logger.info("_do_resolve_type_6: TQ DLL 降级获取到 %d 只ETF", len(etfs))
                return etfs
            except Exception as e:
                logger.warning("_do_resolve_type_6: TQ DLL 降级也失败: %s", e)

        logger.error("_do_resolve_type_6: 所有数据源均不可用")
        return []

    # ------------------------------------------------------------------
    # type=7: 可转债
    # ------------------------------------------------------------------

    async def _do_resolve_type_7(self, **kwargs) -> List[Dict]:
        """解析 type=7: 可转债。

        解析链：数据库 → 本地文件 → AKShare → TQ DLL。
        缓存TTL: 1小时。
        """
        # 1. 数据库优先：查 stocks 表（可转债代码前缀）
        db_result = self._fetch_convertible_bonds_from_db()
        if db_result:
            logger.info("_do_resolve_type_7: 数据库获取到 %d 只可转债", len(db_result))
            return db_result

        # 2. 降级：本地文件数据源
        local_provider = self._providers.get('local_file')
        if local_provider and hasattr(local_provider, 'get_stock_list_by_type'):
            try:
                import asyncio
                # list_type 映射：7->32
                result = local_provider.get_stock_list_by_type(32)
                if asyncio.iscoroutine(result):
                    result = await result
                if result:
                    logger.info("_do_resolve_type_7: 本地文件获取到 %d 条记录", len(result))
                    return result
            except Exception as e:
                logger.debug("_do_resolve_type_7: 本地文件解析失败: %s，降级到 AKShare/TQ", e)

        ak_provider = self._providers.get('akshare')

        # 3. 降级：AKShare
        if ak_provider and hasattr(ak_provider, 'get_all_cb_list'):
            try:
                cbs = await ak_provider.get_all_cb_list()
                logger.info("_do_resolve_type_7: AKShare 获取到 %d 只可转债", len(cbs))
                return cbs
            except Exception as e:
                logger.warning("_do_resolve_type_7: 获取可转债列表失败: %s", e)

        # 4. 降级：TQ DLL list_type=32 (可转债)
        tq_provider = self._providers.get('tq_dll')
        if tq_provider and hasattr(tq_provider, 'get_stock_list'):
            try:
                cbs = await tq_provider.get_stock_list(list_type=32)
                logger.info("_do_resolve_type_7: TQ DLL 降级获取到 %d 只可转债", len(cbs))
                return cbs
            except Exception as e:
                logger.warning("_do_resolve_type_7: TQ DLL 降级也失败: %s", e)

        logger.error("_do_resolve_type_7: 所有数据源均不可用")
        return []

    # ------------------------------------------------------------------
    # 统一缓存管理器
    # ------------------------------------------------------------------

    def _get_from_cache(self, spinfo_type: int) -> Optional[List[Dict]]:
        """从缓存获取数据（若未过期）。

        Args:
            spinfo_type: 备选池类型

        Returns:
            缓存的股票列表，若缓存不存在或已过期则返回 None
        """
        ttl = self.CACHE_TTL.get(spinfo_type, 0)
        if ttl <= 0:
            return None

        cached = self._cache.get(spinfo_type)
        if cached is None:
            return None

        data, cached_at = cached
        if datetime.now() - cached_at > timedelta(seconds=ttl):
            # 缓存已过期，清除
            del self._cache[spinfo_type]
            return None

        return data

    def _set_cache(self, spinfo_type: int, data: List[Dict]):
        """写入缓存。

        Args:
            spinfo_type: 备选池类型
            data: 要缓存的数据
        """
        ttl = self.CACHE_TTL.get(spinfo_type, 0)
        if ttl <= 0:
            return

        self._cache[spinfo_type] = (data, datetime.now())
        logger.debug("_set_cache(type=%d): 已缓存 %d 条记录，TTL=%ds", spinfo_type, len(data), ttl)

    def _clear_cache(self, spinfo_type: int = None):
        """清除缓存。

        Args:
            spinfo_type: 指定要清除的类型，为 None 时清除全部缓存
        """
        if spinfo_type is not None:
            self._cache.pop(spinfo_type, None)
            logger.debug("_clear_cache: 已清除 type=%d 的缓存", spinfo_type)
        else:
            count = len(self._cache)
            self._cache.clear()
            logger.debug("_clear_cache: 已清除全部 %d 个缓存项", count)

    # ------------------------------------------------------------------
    # 数据源降级链
    # ------------------------------------------------------------------

    async def _fetch_with_fallback(
        self,
        primary_fn: Callable,
        fallback_fns: List[Callable],
    ) -> List[Dict]:
        """数据源降级链。

        按顺序尝试主数据源和备用数据源，返回第一个成功的结果。

        Args:
            primary_fn: 主数据源异步函数（无参数调用）
            fallback_fns: 备用数据源函数列表（无参数调用）

        Returns:
            第一个成功返回的数据源结果；全部失败时返回空列表
        """
        all_fns = [primary_fn] + fallback_fns

        for idx, fn in enumerate(all_fns):
            try:
                result = await fn() if asyncio.iscoroutinefunction(fn) else fn()
                if result:
                    source_name = "主数据源" if idx == 0 else f"备用数据源#{idx}"
                    logger.info("_fetch_with_fallback: %s 成功，返回 %d 条记录", source_name, len(result))
                    return result
            except Exception as e:
                source_name = "主数据源" if idx == 0 else f"备用数据源#{idx}"
                logger.warning("_fetch_with_fallback: %s 失败: %s", source_name, e)

        logger.error("_fetch_with_fallback: 所有数据源均失败")
        return []

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_stock_code(stock_code: str) -> Optional[Dict]:
        """将统一的 stock_code 解析为 {setcode, code} 格式。

        支持格式：
        - 'SH600000' → {setcode: 1, code: '600000'}
        - 'SZ000001' → {setcode: 0, code: '000001'}
        - 'BJ430047' → {setcode: 2, code: '430047'}
        - '600000.SH' → {setcode: 1, code: '600000'}
        - '000001' → 根据前缀推断

        Args:
            stock_code: 股票代码字符串

        Returns:
            {'setcode': int, 'code': str} 或 None
        """
        if not stock_code:
            return None

        code_str = str(stock_code).strip().upper()

        # SH/SZ/BJ 前缀格式
        if code_str.startswith('SH') and len(code_str) > 2:
            return {'setcode': 1, 'code': code_str[2:]}
        elif code_str.startswith('SZ') and len(code_str) > 2:
            return {'setcode': 0, 'code': code_str[2:]}
        elif code_str.startswith('BJ') and len(code_str) > 2:
            return {'setcode': 2, 'code': code_str[2:]}

        # .SH/.SZ/.BJ 后缀格式
        if '.' in code_str:
            parts = code_str.split('.')
            code_part = parts[0]
            suffix = parts[1] if len(parts) > 1 else ''
            if suffix == 'SH':
                return {'setcode': 1, 'code': code_part}
            elif suffix == 'SZ':
                return {'setcode': 0, 'code': code_part}
            elif suffix == 'BJ':
                return {'setcode': 2, 'code': code_part}

        # 纯数字：根据前缀推断
        if code_str.isdigit():
            if code_str.startswith('6'):
                return {'setcode': 1, 'code': code_str}
            elif code_str.startswith(('0', '3')):
                return {'setcode': 0, 'code': code_str}
            elif code_str.startswith(('4', '8')):
                return {'setcode': 2, 'code': code_str}

        # 无法识别的格式，作为深圳处理
        return {'setcode': 0, 'code': code_str}

    @staticmethod
    def _parse_tq_code(tq_code: str) -> Optional[Dict]:
        """将 TQ DLL 返回的代码格式解析为标准字典。

        TQ DLL 通常返回 '600000.SH' 或纯数字格式。

        Args:
            tq_code: TQ格式的股票代码

        Returns:
            {'setcode': int, 'code': str, 'name': str} 或 None
        """
        if not tq_code:
            return None

        parsed = CandidatePoolResolver._parse_stock_code(str(tq_code))
        if parsed:
            parsed['name'] = ''  # 名称需要额外填充
        return parsed

    # ------------------------------------------------------------------
    # 数据库优先解析辅助方法（Task 4）
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_row_value(row, key):
        """安全从 sqlite3.Row 或 dict 中提取字段值。

        Args:
            row: sqlite3.Row / dict / 其他（如 MagicMock，返回 None）
            key: 字段名

        Returns:
            字段值；row 为 None 或无法识别的类型时返回 None
        """
        if row is None:
            return None
        if isinstance(row, dict):
            return row.get(key)
        if isinstance(row, sqlite3.Row):
            try:
                return row[key]
            except (KeyError, IndexError):
                return None
        return None

    def _get_db_conn(self):
        """获取 storage 的数据库连接（若 storage 不支持则返回 None）。"""
        try:
            conn = self._storage._conn()
            # 校验是真实的 sqlite3 连接，避免 MagicMock 误用
            if isinstance(conn, sqlite3.Connection):
                return conn
            # 非 sqlite3.Connection（如 MagicMock），尝试关闭并返回 None
            try:
                conn.close()
            except Exception:
                pass
            return None
        except Exception:
            return None

    def _fetch_favorites_from_db(self) -> List[Dict]:
        """从数据库 user_blocks/user_block_members 表读取自选股。

        查 user_blocks WHERE block_type='favorite'，再查 user_block_members
        获取成员股票代码。

        Returns:
            [{'setcode': 1, 'code': '600000', 'name': '浦发银行'}, ...]
        """
        conn = self._get_db_conn()
        if conn is None:
            return []
        try:
            row = conn.execute(
                "SELECT block_code FROM user_blocks WHERE block_type='favorite' LIMIT 1"
            ).fetchone()
            if row is None:
                return []
            block_code = self._extract_row_value(row, 'block_code')
            if not block_code:
                return []

            member_rows = conn.execute(
                "SELECT ubm.stock_code, s.name FROM user_block_members ubm "
                "LEFT JOIN stocks s ON ubm.stock_code = s.stock_code "
                "WHERE ubm.block_code=? ORDER BY ubm.sort_order",
                (block_code,)
            ).fetchall()

            result = []
            for m in member_rows:
                stock_code = self._extract_row_value(m, 'stock_code')
                name = self._extract_row_value(m, 'name') or ''
                if not stock_code:
                    continue
                parsed = self._parse_stock_code(stock_code)
                if parsed:
                    parsed['name'] = name
                    result.append(parsed)
            return result
        except Exception as e:
            logger.debug("_fetch_favorites_from_db: 数据库查询失败: %s", e)
            return []
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _fetch_custom_block_from_db(self, block_name: str) -> List[Dict]:
        """从数据库读取自定义板块成员。

        查 user_blocks WHERE block_name=? AND block_type='custom'，
        再查 user_block_members 获取成员。

        Args:
            block_name: 板块名称

        Returns:
            [{'setcode': 1, 'code': '600000', 'name': '...'}, ...]
        """
        if not block_name:
            return []
        conn = self._get_db_conn()
        if conn is None:
            return []
        try:
            # 先按 block_name 查找
            row = conn.execute(
                "SELECT block_code FROM user_blocks "
                "WHERE block_name=? AND block_type='custom' LIMIT 1",
                (block_name,)
            ).fetchone()
            # 未找到则按 block_code 查找
            if row is None:
                row = conn.execute(
                    "SELECT block_code FROM user_blocks "
                    "WHERE block_code=? AND block_type='custom' LIMIT 1",
                    (block_name,)
                ).fetchone()
            if row is None:
                return []
            block_code = self._extract_row_value(row, 'block_code')
            if not block_code:
                return []

            member_rows = conn.execute(
                "SELECT ubm.stock_code, s.name FROM user_block_members ubm "
                "LEFT JOIN stocks s ON ubm.stock_code = s.stock_code "
                "WHERE ubm.block_code=? ORDER BY ubm.sort_order",
                (block_code,)
            ).fetchall()

            result = []
            for m in member_rows:
                stock_code = self._extract_row_value(m, 'stock_code')
                name = self._extract_row_value(m, 'name') or ''
                if not stock_code:
                    continue
                parsed = self._parse_stock_code(stock_code)
                if parsed:
                    parsed['name'] = name
                    result.append(parsed)
            return result
        except Exception as e:
            logger.debug("_fetch_custom_block_from_db: 数据库查询失败: %s", e)
            return []
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _fetch_all_stocks_from_db(self) -> List[Dict]:
        """从数据库 stocks 表读取所有 A 股。

        SELECT raw_code, name, market FROM stocks WHERE status='active'

        Returns:
            [{'setcode': 1, 'code': '600000', 'name': '浦发银行'}, ...]
        """
        conn = self._get_db_conn()
        if conn is None:
            return []
        try:
            rows = conn.execute(
                "SELECT stock_code, name, market FROM stocks WHERE status='active'"
            ).fetchall()
            result = []
            for row in rows:
                stock_code = self._extract_row_value(row, 'stock_code')
                name = self._extract_row_value(row, 'name') or ''
                if not stock_code:
                    continue
                parsed = self._parse_stock_code(stock_code)
                if parsed:
                    parsed['name'] = name
                    result.append(parsed)
            return result
        except Exception as e:
            logger.debug("_fetch_all_stocks_from_db: 数据库查询失败: %s", e)
            return []
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _fetch_sector_members_from_db(self, sector_name: str,
                                       category: str = None) -> List[Dict]:
        """从数据库读取板块成分股。

        先按 sector_id 直接匹配，再按 sector_name (+ category) 匹配，
        然后查 sector_members WHERE is_current=1，JOIN stocks 获取 name。

        Args:
            sector_name: 板块名称或板块ID
            category: 可选分类过滤

        Returns:
            [{'setcode': 1, 'code': '600000', 'name': '浦发银行'}, ...]
        """
        if not sector_name:
            return []
        conn = self._get_db_conn()
        if conn is None:
            return []
        try:
            # 1. 先按 sector_id 直接匹配
            row = conn.execute(
                "SELECT sector_id FROM sectors WHERE sector_id=? LIMIT 1",
                (sector_name,)
            ).fetchone()

            # 2. 若未找到，按 sector_name (+ category) 匹配
            if row is None:
                if category:
                    row = conn.execute(
                        "SELECT sector_id FROM sectors "
                        "WHERE sector_name=? AND category=? LIMIT 1",
                        (sector_name, category)
                    ).fetchone()
                else:
                    row = conn.execute(
                        "SELECT sector_id FROM sectors WHERE sector_name=? LIMIT 1",
                        (sector_name,)
                    ).fetchone()

            if row is None:
                return []
            sector_id = self._extract_row_value(row, 'sector_id')
            if not sector_id:
                return []

            member_rows = conn.execute(
                "SELECT sm.stock_code, s.name FROM sector_members sm "
                "LEFT JOIN stocks s ON sm.stock_code = s.stock_code "
                "WHERE sm.sector_id=? AND sm.is_current=1",
                (sector_id,)
            ).fetchall()

            result = []
            for m in member_rows:
                stock_code = self._extract_row_value(m, 'stock_code')
                name = self._extract_row_value(m, 'name') or ''
                if not stock_code:
                    continue
                parsed = self._parse_stock_code(stock_code)
                if parsed:
                    parsed['name'] = name
                    result.append(parsed)
            return result
        except Exception as e:
            logger.debug("_fetch_sector_members_from_db: 数据库查询失败: %s", e)
            return []
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _fetch_index_stocks_from_db(self) -> List[Dict]:
        """从数据库读取沪深300+中证500成分股并集。

        查 sectors 表中名称含 '沪深300' 或 '中证500' 的板块，
        再查 sector_members 获取成分股。

        Returns:
            [{'setcode': 1, 'code': '600000', 'name': '浦发银行'}, ...]
        """
        conn = self._get_db_conn()
        if conn is None:
            return []
        try:
            sector_rows = conn.execute(
                "SELECT sector_id FROM sectors "
                "WHERE sector_name LIKE '%沪深300%' OR sector_name LIKE '%中证500%'"
            ).fetchall()
            if not sector_rows:
                return []

            merged = {}
            for sec_row in sector_rows:
                sector_id = self._extract_row_value(sec_row, 'sector_id')
                if not sector_id:
                    continue
                member_rows = conn.execute(
                    "SELECT sm.stock_code, s.name FROM sector_members sm "
                    "LEFT JOIN stocks s ON sm.stock_code = s.stock_code "
                    "WHERE sm.sector_id=? AND sm.is_current=1",
                    (sector_id,)
                ).fetchall()
                for m in member_rows:
                    stock_code = self._extract_row_value(m, 'stock_code')
                    name = self._extract_row_value(m, 'name') or ''
                    if not stock_code:
                        continue
                    parsed = self._parse_stock_code(stock_code)
                    if parsed:
                        key = parsed.get('code', '')
                        if key and key not in merged:
                            parsed['name'] = name
                            merged[key] = parsed
            return list(merged.values())
        except Exception as e:
            logger.debug("_fetch_index_stocks_from_db: 数据库查询失败: %s", e)
            return []
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _fetch_etf_from_db(self) -> List[Dict]:
        """从数据库 stocks 表读取 ETF 基金。

        ETF 代码通常以 51/15/52 开头（SH510xxx/SZ159xxx/SH520xxx）。

        Returns:
            [{'setcode': 1, 'code': '510300', 'name': '沪深300ETF'}, ...]
        """
        conn = self._get_db_conn()
        if conn is None:
            return []
        try:
            rows = conn.execute(
                "SELECT stock_code, name, market FROM stocks "
                "WHERE status='active' AND "
                "(raw_code LIKE '51%' OR raw_code LIKE '15%' OR raw_code LIKE '52%')"
            ).fetchall()
            result = []
            for row in rows:
                stock_code = self._extract_row_value(row, 'stock_code')
                name = self._extract_row_value(row, 'name') or ''
                if not stock_code:
                    continue
                parsed = self._parse_stock_code(stock_code)
                if parsed:
                    parsed['name'] = name
                    result.append(parsed)
            return result
        except Exception as e:
            logger.debug("_fetch_etf_from_db: 数据库查询失败: %s", e)
            return []
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _fetch_convertible_bonds_from_db(self) -> List[Dict]:
        """从数据库 stocks 表读取可转债。

        可转债代码通常以 11/12 开头（SH113xxx/SZ128xxx）。

        Returns:
            [{'setcode': 1, 'code': '113001', 'name': '...'}, ...]
        """
        conn = self._get_db_conn()
        if conn is None:
            return []
        try:
            rows = conn.execute(
                "SELECT stock_code, name, market FROM stocks "
                "WHERE status='active' AND "
                "(raw_code LIKE '11%' OR raw_code LIKE '12%')"
            ).fetchall()
            result = []
            for row in rows:
                stock_code = self._extract_row_value(row, 'stock_code')
                name = self._extract_row_value(row, 'name') or ''
                if not stock_code:
                    continue
                parsed = self._parse_stock_code(stock_code)
                if parsed:
                    parsed['name'] = name
                    result.append(parsed)
            return result
        except Exception as e:
            logger.debug("_fetch_convertible_bonds_from_db: 数据库查询失败: %s", e)
            return []
        finally:
            try:
                conn.close()
            except Exception:
                pass

    async def _fetch_stock_name(self, setcode: int, code: str) -> str:
        """根据 setcode 和 code 尝试获取股票名称。

        依次尝试：
        1. 从 stocks 数据库表查询
        2. 从 TQ DLL 快照获取
        3. 从 AKShare 快照获取

        Args:
            setcode: 市场编号
            code: 股票代码

        Returns:
            股票名称字符串，获取失败返回空字符串
        """
        # 构造标准 stock_code
        market_map = {0: 'SZ', 1: 'SH', 2: 'BJ'}
        market = market_map.get(setcode, 'SZ')
        stock_code = f"{market}{code}"

        # 尝试从数据库查询（如果有相关接口）
        # 注意：Storage 目前没有按 stock_code 查询单只股票名称的方法，
        # 这里预留扩展点
        try:
            # 如果 storage 有 get_stock 方法，可以使用
            if hasattr(self._storage, 'get_stock'):
                stock_info = self._storage.get_stock(stock_code)
                if stock_info:
                    return stock_info.get('name', '')
        except Exception:
            pass

        # 尝试从 TQ DLL 快照获取
        tq_provider = self._providers.get('tq_dll')
        if tq_provider and hasattr(tq_provider, 'get_snapshot'):
            try:
                snapshots = tq_provider.get_snapshot([stock_code])
                if stock_code in snapshots:
                    return snapshots[stock_code].get('name', '')
            except Exception:
                pass

        # 尝试从 AKShare 快照获取
        ak_provider = self._providers.get('akshare')
        if ak_provider and hasattr(ak_provider, 'get_snapshot'):
            try:
                snapshots = ak_provider.get_snapshot([stock_code])
                if stock_code in snapshots:
                    return snapshots[stock_code].get('name', '')
            except Exception:
                pass

        return ''

    # ------------------------------------------------------------------
    # 设计时构建辅助功能
    # ------------------------------------------------------------------

    async def get_category_tree(self, source: str = 'tdx',
                                 category: str = None) -> Dict:
        """获取分类树（用于type=0的自设监控品种选择界面）。

        根据数据源参数路由到对应的 provider 方法，构建树形结构。

        Args:
            source: 数据源（tdx/ths/em/sw/akshare）
            category: 分类过滤（industry/concept/region/index/style/all）

        Returns:
            树形结构的分类目录：
            {
                'source': 'tdx',
                'category': 'concept',
                'tree': [
                    {
                        'id': 'cat_001',
                        'name': '概念板块',
                        'children': [
                            {
                                'id': 'sec_001',
                                'name': '人工智能',
                                'member_count': 150,
                                'sector_id': 'tdx_concept_人工智能'
                            },
                            ...
                        ]
                    },
                    ...
                ]
            }

        数据来源路由：
        - source='tdx' → providers['tq_dll'].get_sector_list(category)
        - source='ths' → providers['akshare'].get_ths_concept_list()
        - source='em' → providers['akshare'].get_em_industry_list() / get_em_concept_list()
        - source='sw' → providers['akshare'].get_sw_industry_list()

        Raises:
            ValueError: 无效的数据源或分类参数

        Example:
            >>> tree = await resolver.get_category_tree('tdx', 'concept')
            >>> print(len(tree['tree']))  # 分类数量
        """
        logger.info("get_category_tree: 获取 %s 数据源的 %s 分类树", source, category or '全部')

        try:
            if source == 'tdx':
                # 通达信数据源：使用 TQ DLL 的板块列表接口
                tq_provider = self._providers.get('tq_dll')
                if not tq_provider or not hasattr(tq_provider, 'get_sector_list'):
                    raise ValueError("TQ provider 不可用或缺少 get_sector_list 方法")

                sectors = await tq_provider.get_sector_list(category)
                tree = self._build_tree_from_sectors(sectors, source)

            elif source == 'ths':
                # 同花顺概念列表
                ak_provider = self._providers.get('akshare')
                if not ak_provider or not hasattr(ak_provider, 'get_ths_concept_list'):
                    raise ValueError("AKShare provider 不可用或缺少 get_ths_concept_list 方法")

                concepts = await ak_provider.get_ths_concept_list()
                tree = self._build_tree_from_flat_list(concepts, 'ths_concept', source)

            elif source == 'em':
                # 东方财富行业/概念列表
                ak_provider = self._providers.get('akshare')
                if not ak_provider:
                    raise ValueError("AKShare provider 不可用")

                if category in ('industry', None, 'all'):
                    industries = await ak_provider.get_em_industry_list()
                    industry_tree = self._build_tree_from_flat_list(industries, 'em_industry', source)
                else:
                    industry_tree = {'children': []}

                if category in ('concept', None, 'all'):
                    concepts = await ak_provider.get_em_concept_list()
                    concept_tree = self._build_tree_from_flat_list(concepts, 'em_concept', source)
                else:
                    concept_tree = {'children': []}

                # 合并行业和概念
                tree = [
                    {**industry_tree, 'id': 'cat_em_industry', 'name': '东方财富行业'},
                    {**concept_tree, 'id': 'cat_em_concept', 'name': '东方财富概念'},
                ]

            elif source == 'sw':
                # 申万行业列表
                ak_provider = self._providers.get('akshare')
                if not ak_provider or not hasattr(ak_provider, 'get_sw_industry_list'):
                    raise ValueError("AKShare provider 不可用或缺少 get_sw_industry_list 方法")

                sw_industries = await ak_provider.get_sw_industry_list()
                tree = self._build_tree_from_sectors(sw_industries, source, support_hierarchy=True)

            elif source == 'local':
                # 本地文件系统板块（tdxbk.cfg 系统板块）
                tree = self._build_local_category_tree(category)

            else:
                raise ValueError(f"不支持的数据源: {source}，有效值为 tdx/ths/em/sw/local")

            result = {
                'source': source,
                'category': category or 'all',
                'tree': tree,
            }

            logger.info(
                "get_category_tree: 成功获取 %s/%s 分类树，共 %d 个节点",
                source,
                category or 'all',
                len(tree),
            )
            return result

        except Exception as e:
            logger.error("get_category_tree: 获取分类树失败: %s", e, exc_info=True)
            raise

    async def build_from_sector(self, sector_id: str,
                                 target_block_code: str = None) -> Dict:
        """从板块构建备选池。

        从指定板块获取成分股，可选择性地创建持久化的自定义板块记录。

        Args:
            sector_id: 源板块ID（如 'ths_concept_人工智能'）
            target_block_code: 目标板块代码（可选）
                - 若提供：创建 resolved 类型 user_blocks 记录并返回
                - 若不提供：仅返回股票列表（一次性使用场景）

        Returns:
            {
                'success': True,
                'stocks': [{setcode, code, name}, ...],
                'count': 150,
                'block_code': 'CSBK_AI',  # 仅当 target_block_code 不为 None 时
                'xml_config': {           # 建议的XML配置
                    'spinfo_type': 0 或 4,
                    'customblockname': 'CSBK_AI',
                    'size': 150,
                    'stks': [{setcode: 1, code: '600000'}, ...]  # 仅type=0时有值
                }
            }

        流程：
        1. 从 sectors + sector_members 表查找成分股（缓存优先）
        2. 缓存未命中时从对应数据源获取最新成分股
        3. 如果 target_block_code 不为 None：创建 resolved 类型的 user_blocks 记录
        4. 如果 target_block_code 为 None：直接返回股票列表和 type=0 配置

        Raises:
            ValueError: 板块不存在或无法获取成分股

        Example:
            >>> # 创建持久化板块
            >>> result = await resolver.build_from_sector('ths_concept_人工智能', 'CSBK_AI')
            >>> print(result['block_code'])  # 'CSBK_AI'

            >>> # 一次性使用
            >>> result = await resolver.build_from_sector('ths_concept_人工智能')
            >>> print(result['xml_config']['spinfo_type'])  # 0
        """
        logger.info(
            "build_from_sector: 从板块 '%s' 构建备选池，目标板块='%s'",
            sector_id,
            target_block_code or '(无，一次性使用)',
        )

        try:
            # 步骤1: 尝试从缓存/数据库获取成分股
            stocks = await self._fetch_sector_members(sector_id)

            if not stocks:
                raise ValueError(f"无法获取板块 '{sector_id}' 的成分股，请检查板块ID是否正确")

            # 步骤2-4: 根据是否提供目标板块代码走不同流程
            if target_block_code:
                # 持久化场景：创建 user_blocks 记录
                result = await self._build_persistent_block(sector_id, target_block_code, stocks)
            else:
                # 一次性使用场景：直接返回股票列表
                result = await self._build_one_time_result(sector_id, stocks)

            logger.info(
                "build_from_sector: 成功构建备选池，%d 只股票，类型=%d",
                result['count'],
                result['xml_config']['spinfo_type'],
            )
            return result

        except ValueError:
            raise
        except Exception as e:
            logger.error("build_from_sector: 构建失败: %s", e, exc_info=True)
            raise ValueError(f"从板块 '{sector_id}' 构建备选池失败: {e}")

    def _generate_xml_config(self, spinfo_type: int,
                              customblockname: str,
                              stocks: List[Dict]) -> Dict:
        """生成建议的 XML 配置。

        根据 spinfo_type 生成对应格式的配置字典，用于写入 XML 文件。

        Args:
            spinfo_type: 备选池类型 (0 或 4)
                - 0: 自设监控品种（显式股票列表）
                - 4: 自定义板块引用
            customblockname: 板块名称
            stocks: 股票列表 [{setcode, code, name}, ...]

        Returns:
            {
                'spinfo_type': int,
                'customblockname': str,
                'size': int,
                'stks': [{setcode: int, code: str}, ...]  # 仅type=0时有值
            }

        Example:
            >>> config = resolver._generate_xml_config(0, 'MY_BLOCK', stocks)
            >>> config['spinfo_type']  # 0
            >>> len(config['stks'])  # 股票数量
        """
        stks = []
        if spinfo_type == 0:
            # type=0 需要显式列出所有股票
            for stock in stocks:
                stks.append({
                    'setcode': stock.get('setcode'),
                    'code': stock.get('code', ''),
                })

        config = {
            'spinfo_type': spinfo_type,
            'customblockname': customblockname,
            'size': len(stocks),
        }

        # 仅 type=0 时包含 stks 列表
        if spinfo_type == 0:
            config['stks'] = stks

        logger.debug(
            "_generate_xml_config: 生成配置 type=%d, name=%s, size=%d",
            spinfo_type,
            customblockname,
            len(stocks),
        )
        return config

    async def build_for_one_time_use(self, sector_id: str) -> Dict:
        """一次性使用场景（不保存板块，直接转为显式stk格式）。

        这是 build_from_sector(target_block_name=None) 的便捷封装。
        适用于临时分析、预览等不需要持久化的场景。

        返回的 xml_config 中：
        - spinfo_type=0
        - 包含完整的 stks 列表（所有股票的 setcode+code）
        - 运行时无需查询数据库或数据源，直接使用 stks 列表

        Args:
            sector_id: 源板块ID

        Returns:
            与 build_from_sector(target_block_code=None) 相同的结构

        Example:
            >>> result = await resolver.build_for_one_time_use('ths_concept_人工智能')
            >>> for stk in result['xml_config']['stks']:
            ...     print(stk['code'])
        """
        logger.info("build_for_one_time_use: 一次性使用模式，sector_id='%s'", sector_id)
        return await self.build_from_sector(sector_id, target_block_code=None)

    # ------------------------------------------------------------------
    # 设计时构建内部辅助方法
    # ------------------------------------------------------------------

    async def _fetch_sector_members(self, sector_id: str) -> List[Dict]:
        """获取板块成员列表（数据库优先，降级到本地文件/实时获取）。

        解析链：数据库 → 本地文件 → storage 缓存 → 实时获取。

        Args:
            sector_id: 板块ID

        Returns:
            股票列表 [{setcode, code, name}, ...]
        """
        # 1. 数据库优先：查 sectors(sector_id/sector_name) + sector_members
        db_result = self._fetch_sector_members_from_db(sector_id)
        if db_result:
            logger.info("_fetch_sector_members: 数据库获取板块 '%s'，%d 只", sector_id, len(db_result))
            return db_result

        # 2. 降级：本地文件数据源
        local_provider = self._providers.get('local_file')
        if local_provider and hasattr(local_provider, 'get_block_members'):
            try:
                import asyncio
                # 从 sector_id 提取板块代码（如 'local_tdx_TEST' -> 'TEST'）
                block_code = sector_id
                if '_' in sector_id:
                    parts = sector_id.split('_', 2)
                    if len(parts) >= 3:
                        block_code = parts[2]
                raw_codes = local_provider.get_block_members(block_code)
                if asyncio.iscoroutine(raw_codes):
                    raw_codes = await raw_codes
                if raw_codes:
                    result = []
                    for rc in raw_codes:
                        parsed = self._parse_stock_code(rc)
                        if parsed:
                            parsed.setdefault('name', '')
                            result.append(parsed)
                    if result:
                        logger.info("_fetch_sector_members: 本地文件获取板块 '%s'，%d 只", sector_id, len(result))
                        return result
            except Exception as e:
                logger.debug("_fetch_sector_members: 本地文件解析失败: %s，降级到数据库/实时获取", e)

        # 3. 降级：storage 缓存（保留原有逻辑）
        try:
            cached_members = self._storage.get_sector_members(sector_id)
            if cached_members and len(cached_members) > 0:
                logger.debug("_fetch_sector_members: 缓存命中 '%s'，%d 条记录", sector_id, len(cached_members))
                return cached_members
        except Exception as e:
            logger.debug("_fetch_sector_members: 数据库查询失败: %s，尝试实时获取", e)

        # 4. 缓存未命中，根据 sector_id 前缀判断数据源并实时获取
        try:
            stocks = await self._fetch_sector_members_realtime(sector_id)
        except ValueError as e:
            logger.warning(
                "_fetch_sector_members: 实时获取板块 '%s' 成分股失败: %s",
                sector_id, e,
            )
            return []

        # 可选：将结果写回缓存
        if stocks and hasattr(self._storage, 'cache_sector_members'):
            try:
                self._storage.cache_sector_members(sector_id, stocks)
                logger.debug("_fetch_sector_members: 已缓存 '%s' 的 %d 条成员", sector_id, len(stocks))
            except Exception as e:
                logger.warning("_fetch_sector_members: 缓存写入失败: %s", e)

        return stocks

    async def _fetch_sector_members_realtime(self, sector_id: str) -> List[Dict]:
        """根据 sector_id 前缀判断数据源类型并实时获取成分股。

        Args:
            sector_id: 板块ID（格式：'{source}_{category}_{name}'）

        Returns:
            股票列表
        """
        # 解析 sector_id 前缀以确定数据源
        parts = sector_id.split('_', 2)
        if len(parts) < 2:
            raise ValueError(f"无效的 sector_id 格式: '{sector_id}'，期望格式为 'source_category_name'")

        source_prefix = parts[0].lower()

        try:
            if source_prefix == 'tdx':
                # 通达信板块
                tq_provider = self._providers.get('tq_dll')
                if not tq_provider or not hasattr(tq_provider, 'get_block_members'):
                    raise ValueError("TQ provider 不可用")
                return await tq_provider.get_block_members(sector_id)

            elif source_prefix == 'ths':
                # 同花顺概念
                ak_provider = self._providers.get('akshare')
                if not ak_provider or not hasattr(ak_provider, 'get_ths_concept_members'):
                    raise ValueError("AKShare provider 不可用")
                concept_name = '_'.join(parts[2:]) if len(parts) > 2 else ''
                return await ak_provider.get_ths_concept_members(concept_name)

            elif source_prefix == 'em':
                # 东方财富
                ak_provider = self._providers.get('akshare')
                if not ak_provider:
                    raise ValueError("AKShare provider 不可用")

                category = parts[1] if len(parts) > 1 else ''
                name = '_'.join(parts[2:]) if len(parts) > 2 else ''

                if category == 'industry' and hasattr(ak_provider, 'get_em_industry_members'):
                    return await ak_provider.get_em_industry_members(name)
                elif category == 'concept' and hasattr(ak_provider, 'get_em_concept_members'):
                    return await ak_provider.get_em_concept_members(name)
                else:
                    raise ValueError(f"不支持的东方财富分类: {category}")

            elif source_prefix == 'sw':
                # 申万行业
                ak_provider = self._providers.get('akshare')
                if not ak_provider or not hasattr(ak_provider, 'get_sw_industry_members'):
                    raise ValueError("AKShare provider 不可用")
                industry_name = '_'.join(parts[2:]) if len(parts) > 2 else ''
                return await ak_provider.get_sw_industry_members(industry_name)

            else:
                raise ValueError(f"无法识别的数据源前缀: '{source_prefix}'")

        except ValueError:
            raise
        except Exception as e:
            logger.error("_fetch_sector_members_realtime: 获取失败: %s", e, exc_info=True)
            raise ValueError(f"从数据源获取板块 '{sector_id}' 成分股失败: {e}")

    async def _build_persistent_block(self, sector_id: str,
                                       target_block_code: str,
                                       stocks: List[Dict]) -> Dict:
        """构建持久化板块结果（创建 user_blocks 记录）。

        Args:
            sector_id: 源板块ID
            target_block_code: 目标板块代码
            stocks: 成分股列表

        Returns:
            包含 block_code 和 type=4 xml_config 的结果字典
        """
        logger.info(
            "_build_persistent_block: 创建持久化板块 '%s'，来源='%s'",
            target_block_code,
            sector_id,
        )

        # 创建 resolved 类型的 user_blocks 记录
        try:
            block_record = self._storage.create_resolved_block(
                block_code=target_block_code,
                source_sector_id=sector_id,
                member_count=len(stocks),
            )
            logger.debug(
                "_build_persistent_block: 已创建 user_blocks 记录，block_code=%s",
                target_block_code,
            )
        except Exception as e:
            logger.error("_build_persistent_block: 创建 user_blocks 失败: %s", e)
            raise

        # 将成员写入 user_block_members 表
        try:
            members_data = []
            for stock in stocks:
                members_data.append({
                    'block_code': target_block_code,
                    'stock_code': f"{stock.get('setcode', 0)}_{stock.get('code', '')}",
                    'name': stock.get('name', ''),
                })

            self._storage.batch_insert_block_members(target_block_code, members_data)
            logger.debug(
                "_build_persistent_block: 已写入 %d 条成员记录",
                len(members_data),
            )
        except Exception as e:
            logger.error("_build_persistent_block: 写入成员失败: %s", e)
            raise

        # 生成 type=4 的 xml_config（引用自定义板块）
        xml_config = self._generate_xml_config(
            spinfo_type=4,
            customblockname=target_block_code,
            stocks=stocks,
        )

        return {
            'success': True,
            'stocks': stocks,
            'count': len(stocks),
            'block_code': target_block_code,
            'xml_config': xml_config,
        }

    async def _build_one_time_result(self, sector_id: str,
                                      stocks: List[Dict]) -> Dict:
        """构建一次性使用结果（不保存，返回显式股票列表）。

        Args:
            sector_id: 源板块ID
            stocks: 成分股列表

        Returns:
            包含 type=0 xml_config（带完整 stks 列表）的结果字典
        """
        logger.info(
            "_build_one_time_result: 一次性使用模式，来源='%s'，%d 只股票",
            sector_id,
            len(stocks),
        )

        # 使用 sector_id 作为默认名称（去除特殊字符）
        safe_name = sector_id.replace('_', ' ').replace('-', ' ')

        # 生成 type=0 + 显式 stks 的 xml_config
        xml_config = self._generate_xml_config(
            spinfo_type=0,
            customblockname=safe_name,
            stocks=stocks,
        )

        return {
            'success': True,
            'stocks': stocks,
            'count': len(stocks),
            'block_code': None,
            'xml_config': xml_config,
        }

    def _build_tree_from_sectors(self, sectors: List[Dict],
                                  source: str,
                                  support_hierarchy: bool = False) -> List[Dict]:
        """将板块列表转换为树形结构。

        Args:
            sectors: 原始板块列表
            source: 数据源标识
            support_hierarchy: 是否支持层级结构（parent_id 关系）

        Returns:
            树形结构列表
        """
        if not sectors:
            return []

        if support_hierarchy and any(s.get('parent_id') for s in sectors):
            # 支持层级的场景：按 parent_id 构建树
            return self._build_hierarchical_tree(sectors, source)
        else:
            # 扁平结构：包装为单层树
            return [{
                'id': f'cat_{source}_all',
                'name': f'{source.upper()} 板块',
                'children': [
                    {
                        'id': s.get('sector_id', s.get('id', f'sec_{idx}')),
                        'name': s.get('name', ''),
                        'member_count': s.get('member_count', 0),
                        'sector_id': s.get('sector_id', ''),
                    }
                    for idx, s in enumerate(sectors)
                ],
            }]

    def _build_tree_from_flat_list(self, items: List[Dict],
                                    id_prefix: str,
                                    source: str) -> Dict:
        """将扁平列表转换为树节点。

        Args:
            items: 扁平项目列表
            id_prefix: ID前缀
            source: 数据源标识

        Returns:
            单个树节点字典
        """
        children = []
        for idx, item in enumerate(items):
            children.append({
                'id': item.get('id', f'{id_prefix}_{idx}'),
                'name': item.get('name', ''),
                'member_count': item.get('member_count', item.get('count', 0)),
                'sector_id': item.get('sector_id', f'{source}_{item.get("code", "")}'),
            })

        return {
            'id': f'cat_{id_prefix}',
            'name': f'{id_prefix.replace("_", " ").title()}',
            'children': children,
        }

    # 本地系统板块分类中文标签映射
    _LOCAL_CATEGORY_LABELS = {
        'concept': '概念',
        'industry': '行业',
        'index': '指数',
        'style': '风格',
        'region': '地区',
        'other': '其他',
    }

    def _build_local_category_tree(self, category: Optional[str] = None) -> List[Dict]:
        """从 LocalFileProvider 的系统板块构建分类树。

        Args:
            category: 分类过滤（concept/industry/index/style/region/all/None）
                为 None 或 'all' 时返回全部分类。

        Returns:
            分类树列表，每个分类节点含 id/name/children，
            子节点含 sector_id/name/member_count。
            LocalFileProvider 不可用时返回空列表并记录警告。
        """
        local_provider = self._providers.get('local_file')
        if not local_provider or not hasattr(local_provider, 'get_system_sectors'):
            logger.warning("get_category_tree: LocalFileProvider 不可用，返回空本地分类树")
            return []

        try:
            grouped = local_provider.get_system_sectors_flat()
        except Exception as e:
            logger.warning("get_category_tree: 获取本地系统板块失败: %s", e)
            return []

        if not grouped:
            return []

        # 决定要包含的分类
        if category and category != 'all':
            categories = [category] if category in grouped else []
        else:
            # 按固定顺序输出已知分类，再追加未知分类
            ordered = ['concept', 'industry', 'index', 'style', 'region']
            categories = [c for c in ordered if c in grouped]
            categories += [c for c in grouped if c not in ordered]

        tree: List[Dict] = []
        for cat_name in categories:
            sectors = grouped.get(cat_name, [])
            children = []
            for sec in sectors:
                sec_name = sec.get('name', '') or sec.get('code', '')
                children.append({
                    'sector_id': f'local_{cat_name}_{sec_name}',
                    'name': sec_name,
                    'member_count': 0,
                })
            tree.append({
                'id': f'cat_{cat_name}',
                'name': self._LOCAL_CATEGORY_LABELS.get(cat_name, cat_name),
                'children': children,
            })
        return tree

    def _build_hierarchical_tree(self, sectors: List[Dict],
                                  source: str) -> List[Dict]:
        """构建支持 parent_id 的层级树。

        Args:
            sectors: 带 parent_id 字段的板块列表
            source: 数据源标识

        Returns:
            层级树形结构
        """
        # 按 parent_id 分组
        by_parent = {}
        root_nodes = []

        for sector in sectors:
            parent_id = sector.get('parent_id') or 'root'
            node = {
                'id': sector.get('sector_id', sector.get('id', '')),
                'name': sector.get('name', ''),
                'member_count': sector.get('member_count', 0),
                'sector_id': sector.get('sector_id', ''),
                'children': [],
            }

            if parent_id == 'root' or parent_id is None:
                root_nodes.append(node)
            else:
                if parent_id not in by_parent:
                    by_parent[parent_id] = []
                by_parent[parent_id].append(node)

        # 递归填充子节点
        def fill_children(nodes):
            for node in nodes:
                node_id = node.get('id') or node.get('sector_id')
                if node_id in by_parent:
                    node['children'] = by_parent[node_id]
                    fill_children(node['children'])

        fill_children(root_nodes)
        return root_nodes


# ═══════════════════════════════════════════════════════════════
# 备选池刷新管理器（原 candidate_pool_refresh_manager.py）
# ═══════════════════════════════════════════════════════════════

# 备选池刷新管理器
#
# 职责：
# - 管理后台定时刷新任务
# - 支持自选股（type=3）和自定义板块（type=4）的动态更新
# - 实现 Copy-on-Write 安全更新策略
# - 提供变更通知机制


def _parse_hhmmss(param: Union[str, int]) -> Optional[dt_time]:
    """将 HHMMSS 参数解析为 datetime.time 对象。

    支持格式：
      - 6 位数字字符串/整数：093000 → 09:30:00
      - 带冒号的时间字符串："09:30:00"
      - 少于 6 位数字：按时间单位右对齐补零处理
        （如 930 → 09:30:00；9 → 09:00:00；12345 → 01:23:45）

    Returns:
        datetime.time 或 None（解析失败）
    """
    if param is None:
        return None
    s = str(param).strip()
    if not s:
        return None
    try:
        if ":" in s:
            parts = s.split(":")
            if len(parts) < 2:
                return None
            h, m = int(parts[0]), int(parts[1])
            sec = int(parts[2]) if len(parts) > 2 else 0
            return dt_time(h, m, sec)
        if not s.isdigit():
            return None
        # 将短数字统一规范为 6 位 HHMMSS：
        #   1-2 位视为小时，3-4 位视为 HHMM，5-6 位视为 HHMMSS
        if len(s) <= 2:
            s = s.zfill(2) + "0000"
        elif len(s) <= 4:
            s = s.zfill(4) + "00"
        else:
            s = s.zfill(6)
        if len(s) != 6:
            return None
        return dt_time(int(s[:2]), int(s[2:4]), int(s[4:6]))
    except (ValueError, TypeError):
        return None


class CandidatePoolRefreshManager:
    """
    备选池刷新管理器

    职责：
    - 管理后台定时刷新任务
    - 支持自选股（type=3）和自定义板块（type=4）的动态更新
    - 实现 Copy-on-Write 安全更新策略
    - 提供变更通知机制
    - 支持 DZH 备选池 5 种 reload 重载模式调度
    """

    def __init__(self, resolver: Any, refresh_callback: Optional[Callable] = None):
        """
        Args:
            resolver: CandidatePoolResolver 实例
            refresh_callback: 可选的刷新回调函数，签名 refresh_callback(entity_type, stock_list)，
                              用于通知外部刷新发生（如 WebSocket 推送）。
        """
        self.resolver = resolver
        self._tasks: Dict[str, asyncio.Task] = {}  # 正在运行的刷新任务
        self._callbacks: List[Callable] = []       # 变更回调列表
        self._latest_data: Dict[str, List[Dict]] = {}  # 最新数据快照
        self._running = False
        # DZH reload 模式执行状态跟踪
        self._startup_loaded: set = set()          # 已执行过 on_startup 的节点
        self._file_load_loaded: set = set()        # 已执行过 on_file_load 的节点
        # Task 6: 文件监视器相关属性
        self._refresh_callback = refresh_callback  # 刷新回调（WebSocket 推送等）
        self._file_watcher_task: Optional[asyncio.Task] = None  # 文件监视器异步任务
        self._watched_files: Dict[str, Optional[float]] = {}    # {逻辑名: last_mtime}
        self._watched_paths: Dict[str, Optional[str]] = {}      # {逻辑名: 实际文件路径}

    async def start(self):
        """启动刷新管理器"""
        if self._running:
            logger.warning("CandidatePoolRefreshManager 已经在运行中")
            return

        self._running = True
        logger.info("CandidatePoolRefreshManager 已启动")

        # 启动文件监视器（Task 6）
        await self._start_file_watcher()

    async def stop(self):
        """停止所有刷新任务"""
        if not self._running:
            return

        self._running = False

        # 停止文件监视器（Task 6）
        if self._file_watcher_task is not None and not self._file_watcher_task.done():
            self._file_watcher_task.cancel()
            try:
                await self._file_watcher_task
            except asyncio.CancelledError:
                logger.debug("文件监视器已停止")
            except Exception as e:
                logger.warning("停止文件监视器时出错: %s", e)
            self._file_watcher_task = None

        # 取消所有正在运行的任务
        for task_name, task in self._tasks.items():
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    logger.debug("已取消刷新任务: %s", task_name)
                except Exception as e:
                    logger.warning("取消刷新任务 %s 时出错: %s", task_name, e)

        self._tasks.clear()
        logger.info("CandidatePoolRefreshManager 已停止，共取消 %d 个任务", len(self._tasks))

    async def refresh_favorites(self, interval: int = 30) -> None:
        """
        后台定时刷新自选股（type=3）

        默认30秒间隔，使用 asyncio.create_task() 创建后台任务。

        流程：
        1. 循环执行直到 _running=False
        2. 调用 resolver.resolve_type_3(force_refresh=True)
        3. 使用 Copy-on-Write 策略更新 _latest_data['favorites']
        4. 触发变更回调
        5. await asyncio.sleep(interval)
        6. 异常处理：失败时指数退避（最小10秒）
        """
        if not self._running:
            logger.warning("刷新管理器未启动，无法开始刷新自选股")
            return

        task_name = 'favorites'

        # 如果已有任务在运行，先取消
        if task_name in self._tasks and not self._tasks[task_name].done():
            self._tasks[task_name].cancel()
            try:
                await self._tasks[task_name]
            except asyncio.CancelledError:
                pass

        # 创建后台任务
        task = asyncio.create_task(
            self._refresh_with_backoff(
                refresh_fn=lambda: self.resolver.resolve(3, force_refresh=True),
                key='favorites',
                normal_interval=interval,
                min_interval=10,
                max_interval=300
            ),
            name=f'refresh_{task_name}'
        )
        self._tasks[task_name] = task
        logger.info("已启动自选股定时刷新任务，间隔=%d秒", interval)

    async def refresh_custom_block(self, block_code: str,
                                    interval: int = None) -> None:
        """
        刷新指定的自定义板块（type=4）

        Args:
            block_code: 板块代码（如 'CSBK_TEST'）
            interval: 刷新间隔（秒），None 表示仅手动触发一次
        """
        if not self._running and interval is not None:
            logger.warning("刷新管理器未启动，无法开始定时刷新板块")
            return

        task_key = f'block_{block_code}'

        # 如果提供了 interval，则启动后台定时任务
        if interval is not None:
            # 如果已有任务在运行，先取消
            if task_key in self._tasks and not self._tasks[task_key].done():
                self._tasks[task_key].cancel()
                try:
                    await self._tasks[task_key]
                except asyncio.CancelledError:
                    pass

            # 创建后台任务
            task = asyncio.create_task(
                self._refresh_with_backoff(
                    refresh_fn=lambda: self.resolver.resolve(4, customblockname=block_code, force_refresh=True),
                    key=task_key,
                    normal_interval=interval,
                    min_interval=10,
                    max_interval=300
                ),
                name=f'refresh_{task_key}'
            )
            self._tasks[task_key] = task
            logger.info("已启动自定义板块 '%s' 定时刷新任务，间隔=%d秒", block_code, interval)
        else:
            # 仅执行一次刷新
            try:
                data = await self.resolver.resolve(4, customblockname=block_code, force_refresh=True)
                self._update_snapshot_cow(task_key, data)
                self._notify_change(task_key, data)
                logger.info("已完成自定义板块 '%s' 的一次性刷新，%d 条记录", block_code, len(data))
            except Exception as e:
                logger.error("一次性刷新自定义板块 '%s' 失败: %s", block_code, e)

    # ------------------------------------------------------------------
    # DZH 备选池 reload 模式调度（5 种模式）
    # ------------------------------------------------------------------

    async def schedule_reload(self, node_id: str, reload_mode: str,
                              reload_param: Union[str, int, None] = None,
                              refresh_fn: Optional[Callable[[], Any]] = None,
                              key: Optional[str] = None) -> None:
        """按 reload 模式调度备选池刷新任务。

        Args:
            node_id: 节点唯一标识
            reload_mode: 5 种模式之一
                on_startup / daily_time / interval / never / on_file_load
            reload_param: 模式参数
                - daily_time: HHMMSS 格式时间
                - interval: 秒数（正整数）
            refresh_fn: 实际刷新函数；为 None 时尝试使用 resolver 解析
            key: 数据快照键，为 None 时使用 node_id
        """
        if not self._running and reload_mode not in ("on_file_load", "on_startup"):
            logger.warning("刷新管理器未启动，无法调度 reload_mode=%s", reload_mode)
            return

        task_key = key or node_id
        refresh_fn = refresh_fn or self._default_refresh_fn(node_id)

        # 表驱动分派：op → method，查 dzh_reload_schedule.json:scheduling
        # Task 23.4: 通过 _ensure_reload_schedule() 获取配置（PoolLoaded 事件缓存 / 回退直接加载）
        reload_cfg = self._ensure_reload_schedule()
        scheduling = reload_cfg.get("scheduling", {})
        entry = scheduling.get(reload_mode)
        if entry is None:
            logger.warning("未知 reload_mode=%s，回退为 on_startup", reload_mode)
            entry = scheduling.get(
                reload_cfg.get("default_mode", "on_startup"), {})
        method_name = entry.get("method", "_schedule_on_startup")
        method = getattr(self, method_name, self._schedule_on_startup)
        await method(node_id, reload_param, refresh_fn, task_key)

    def _default_refresh_fn(self, node_id: str) -> Callable:
        """构造默认刷新函数（基于 resolver）。

        目前仅对自选股/自定义板块有效；其他节点类型应显式传入 refresh_fn。
        """
        async def _fn():
            if self.resolver is None:
                logger.warning("无 resolver，无法自动刷新节点 %s", node_id)
                return []
            # 默认按 type=3 自选股解析，调用方可覆盖 refresh_fn
            return await self.resolver.resolve(3, force_refresh=True)
        return _fn

    async def _schedule_on_startup(self, node_id: str,
                                   reload_param: Union[str, int, None],
                                   refresh_fn: Callable,
                                   key: str) -> None:
        """on_startup：只在引擎启动时加载一次。"""
        if node_id in self._startup_loaded:
            logger.debug("节点 %s 已执行过 on_startup，跳过", node_id)
            return
        try:
            data = await refresh_fn()
            self._update_snapshot_cow(key, data if data is not None else [])
            self._notify_change(key, data if data is not None else [])
            self._startup_loaded.add(node_id)
            logger.info("节点 %s on_startup 刷新完成，%d 条记录", node_id,
                        len(data) if data else 0)
        except Exception as e:
            logger.error("节点 %s on_startup 刷新失败: %s", node_id, e)

    async def _schedule_on_file_load(self, node_id: str,
                                     reload_param: Union[str, int, None],
                                     refresh_fn: Callable,
                                     key: str) -> None:
        """on_file_load：只在 XML/池配置加载时加载一次。"""
        if node_id in self._file_load_loaded:
            logger.debug("节点 %s 已执行过 on_file_load，跳过", node_id)
            return
        try:
            data = await refresh_fn()
            self._update_snapshot_cow(key, data if data is not None else [])
            self._notify_change(key, data if data is not None else [])
            self._file_load_loaded.add(node_id)
            logger.info("节点 %s on_file_load 刷新完成，%d 条记录", node_id,
                        len(data) if data else 0)
        except Exception as e:
            logger.error("节点 %s on_file_load 刷新失败: %s", node_id, e)

    async def _schedule_never(self, node_id: str,
                              reload_param: Union[str, int, None],
                              refresh_fn: Callable,
                              key: str) -> None:
        """never：永不自动加载。"""
        logger.info("节点 %s reload_mode=never，不创建刷新任务", node_id)

    async def _schedule_interval(self, node_id: str,
                                 interval: Union[str, int, None],
                                 refresh_fn: Callable,
                                 key: str) -> None:
        """interval：每隔 interval 秒加载。"""
        try:
            seconds = int(interval) if interval is not None else 0
        except (ValueError, TypeError):
            seconds = 0
        if seconds <= 0:
            logger.warning("节点 %s interval 参数无效(%s)，不创建任务", node_id, interval)
            return

        task_name = f"interval_{node_id}"
        if task_name in self._tasks and not self._tasks[task_name].done():
            self._tasks[task_name].cancel()
            try:
                await self._tasks[task_name]
            except asyncio.CancelledError:
                pass

        task = asyncio.create_task(
            self._refresh_with_backoff(
                refresh_fn=refresh_fn,
                key=key,
                normal_interval=seconds,
                min_interval=max(1, seconds // 10),
                max_interval=max(seconds, 300)
            ),
            name=f"reload_{task_name}"
        )
        self._tasks[task_name] = task
        logger.info("节点 %s interval 任务已创建，间隔=%d秒", node_id, seconds)

    async def _schedule_daily_time(self, node_id: str,
                                   hhmmss: Union[str, int, None],
                                   refresh_fn: Callable,
                                   key: str) -> None:
        """daily_time：每天指定 HHMMSS 检查并加载。"""
        target_time = _parse_hhmmss(hhmmss)
        if target_time is None:
            logger.warning("节点 %s daily_time 参数无效(%s)，不创建任务", node_id, hhmmss)
            return

        task_name = f"daily_{node_id}"
        if task_name in self._tasks and not self._tasks[task_name].done():
            self._tasks[task_name].cancel()
            try:
                await self._tasks[task_name]
            except asyncio.CancelledError:
                pass

        async def _daily_loop():
            logger.info("节点 %s daily_time 任务启动，目标时间=%s", node_id, target_time)
            while self._running:
                now = datetime.now()
                target = datetime.combine(now.date(), target_time)
                if target <= now:
                    target += timedelta(days=1)
                wait_seconds = (target - now).total_seconds()
                logger.debug("节点 %s 下次 daily_time 刷新在 %s (等待 %.0f 秒)",
                             node_id, target.isoformat(), wait_seconds)
                try:
                    await asyncio.sleep(wait_seconds)
                except asyncio.CancelledError:
                    logger.debug("节点 %s daily_time 任务被取消", node_id)
                    raise
                if not self._running:
                    break
                try:
                    data = await refresh_fn()
                    self._update_snapshot_cow(key, data if data is not None else [])
                    self._notify_change(key, data if data is not None else [])
                    logger.info("节点 %s daily_time 刷新完成，%d 条记录", node_id,
                                len(data) if data else 0)
                except Exception as e:
                    logger.error("节点 %s daily_time 刷新失败: %s", node_id, e)
                # 完成后等待 1 天再进入下一次计算
                await asyncio.sleep(1)

        task = asyncio.create_task(_daily_loop(), name=f"reload_{task_name}")
        self._tasks[task_name] = task
        logger.info("节点 %s daily_time 任务已创建，目标时间=%s", node_id, target_time)

    def _update_snapshot_cow(self, key: str, new_data: List[Dict]) -> None:
        """
        Copy-on-Write 更新策略

        确保读取方始终看到一致的数据快照：
        1. 在内存中创建新的数据副本
        2. 原子性地替换 _latest_data[key] 的引用
        3. 旧数据由GC回收
        """
        # 创建深拷贝，确保读取方看到的数据不会被修改
        import copy
        snapshot = copy.deepcopy(new_data)
        # 原子性替换引用
        self._latest_data[key] = snapshot
        logger.debug("_update_snapshot_cow: 已更新快照 '%s'，%d 条记录", key, len(snapshot))

    async def _refresh_with_backoff(self, refresh_fn: Callable,
                                      key: str,
                                      normal_interval: int = 30,
                                      min_interval: int = 10,
                                      max_interval: int = 300) -> None:
        """
        带指数退避的刷新循环

        - 首次失败：等待 min_interval 秒
        - 后续失败：等待时间翻倍（不超过 max_interval）
        - 成功后：重置为正常间隔
        """
        backoff_interval = min_interval
        consecutive_failures = 0

        logger.info("_refresh_with_backoff: 开始刷新循环 key='%s', 正常间隔=%ds", key, normal_interval)

        try:
            while self._running:
                try:
                    # 执行刷新
                    data = await refresh_fn()

                    if data is not None:
                        # 成功：使用 Copy-on-Write 更新快照
                        self._update_snapshot_cow(key, data)
                        # 触发变更回调
                        self._notify_change(key, data)
                        # 重置退避计数器和间隔
                        consecutive_failures = 0
                        backoff_interval = min_interval
                        logger.debug("_refresh_with_backoff: key='%s' 刷新成功，%d 条记录", key, len(data))

                    # 使用正常间隔等待
                    await asyncio.sleep(normal_interval)

                except asyncio.CancelledError:
                    logger.debug("_refresh_with_backoff: key='%s' 任务被取消", key)
                    raise
                except Exception as e:
                    consecutive_failures += 1
                    logger.warning(
                        "_refresh_with_backoff: key='%s' 刷新失败 (第%d次连续失败): %s",
                        key,
                        consecutive_failures,
                        e
                    )

                    # 指数退避：等待时间翻倍（不超过 max_interval）
                    await asyncio.sleep(backoff_interval)
                    backoff_interval = min(backoff_interval * 2, max_interval)

        except asyncio.CancelledError:
            logger.debug("_refresh_with_backoff: key='%s' 刷新循环已取消", key)
        except Exception as e:
            logger.error("_refresh_with_backoff: key='%s' 刷新循环异常退出: %s", key, e)
        finally:
            logger.info("_refresh_with_backoff: key='%s' 刷新循环已结束", key)

    def register_callback(self, callback: Callable[[str, List[Dict]], None]) -> None:
        """
        注册变更回调

        回调签名：callback(key: str, data: List[Dict])
        - key: 'favorites' 或 block_code
        - data: 更新后的股票列表
        """
        if callback not in self._callbacks:
            self._callbacks.append(callback)
            logger.debug("register_callback: 已注册回调，当前共 %d 个回调", len(self._callbacks))

    def unregister_callback(self, callback: Callable[[str, List[Dict]], None]) -> None:
        """注销变更回调"""
        if callback in self._callbacks:
            self._callbacks.remove(callback)
            logger.debug("unregister_callback: 已注销回调，当前共 %d 个回调", len(self._callbacks))

    def _notify_change(self, key: str, data: List[Dict]) -> None:
        """通知所有注册的回调"""
        if not self._callbacks:
            return

        for callback in self._callbacks:
            try:
                callback(key, data)
            except Exception as e:
                logger.error("_notify_change: 回调调用失败: %s", e, exc_info=True)

        logger.debug("_notify_change: 已通知 %d 个回调，key='%s'", len(self._callbacks), key)

    async def get_latest_data(self, key: str) -> List[Dict]:
        """
        获取最新数据快照（线程安全）

        由于使用了 Copy-on-Write 策略，返回的是不可变快照的副本，
        多个读取方可以安全地并发访问。
        """
        data = self._latest_data.get(key, [])
        # 返回浅拷贝，防止外部修改影响内部状态
        return list(data)

    def get_running_tasks(self) -> Dict[str, bool]:
        """获取当前正在运行的刷新任务状态"""
        status = {}
        for task_name, task in self._tasks.items():
            status[task_name] = not task.done()
        return status

    def is_running(self) -> bool:
        """检查管理器是否正在运行"""
        return self._running

    # ------------------------------------------------------------------
    # 文件监视器（Task 6）
    # ------------------------------------------------------------------

    def _get_local_provider(self):
        """获取 LocalFileProvider 实例（通过 resolver 的 providers 字典）。"""
        if self.resolver is None:
            return None
        providers = getattr(self.resolver, '_providers', {}) or {}
        return providers.get('local_file')

    @staticmethod
    def _find_first_existing_path(local_provider,
                                    client_file_keys: List[Tuple[str, str]]) -> Optional[str]:
        """在 LocalFileProvider 中按优先级查找第一个存在的文件路径。

        Args:
            local_provider: LocalFileProvider 实例
            client_file_keys: [(client, file_key), ...] 按优先级排列

        Returns:
            第一个存在的文件完整路径，全部不存在时返回 None
        """
        if not local_provider or not hasattr(local_provider, '_get_file_path'):
            return None
        for client, file_key in client_file_keys:
            try:
                path = local_provider._get_file_path(client, file_key)
                if path and os.path.exists(path):
                    return path
            except Exception:
                continue
        return None

    @staticmethod
    def _get_file_mtime(path: Optional[str]) -> Optional[float]:
        """获取文件修改时间，文件不存在或无法访问时返回 None。"""
        if not path:
            return None
        try:
            return os.path.getmtime(str(path))
        except OSError:
            return None

    def _resolve_watch_paths(self) -> None:
        """通过 LocalFileProvider 解析要监视的文件路径。

        将逻辑名映射到实际文件路径：
        - 'zxg.cfg' → 自选股文件（tdx/dzh/ths 优先级探测）
        - 'blocknew.cfg' → 自定义板块索引文件/目录
        """
        local_provider = self._get_local_provider()
        if not local_provider:
            logger.debug("文件监视器：LocalFileProvider 不可用，跳过路径解析")
            return

        # 获取自选股文件路径（zxg.cfg）
        favorites_path = self._find_first_existing_path(local_provider, [
            ('tdx', 'favorites'),
            ('dzh', 'favorites'),
            ('ths', 'favorites_zxg'),
        ])
        self._watched_paths['zxg.cfg'] = favorites_path

        # 获取自定义板块文件路径（blocknew.cfg）
        custom_blocks_path = self._find_first_existing_path(local_provider, [
            ('tdx', 'custom_blocks_index'),
            ('dzh', 'custom_blocks_index'),
            ('ths', 'block_cfg'),
        ])
        self._watched_paths['blocknew.cfg'] = custom_blocks_path

    async def _start_file_watcher(self) -> None:
        """启动文件监视器，每 3 秒轮询文件修改时间。

        通过 LocalFileProvider 解析 zxg.cfg 和 blocknew.cfg 的完整路径，
        记录初始修改时间，然后启动后台轮询任务。
        """
        # 解析要监视的文件路径
        self._resolve_watch_paths()

        # 初始化文件修改时间
        for logical_name, path in self._watched_paths.items():
            self._watched_files[logical_name] = self._get_file_mtime(path)

        watched = {k: v for k, v in self._watched_paths.items() if v}
        if watched:
            logger.info("文件监视器：开始监视 %s", watched)
        else:
            logger.info("文件监视器：未找到可监视的文件，监视器将空转等待文件出现")

        # 启动轮询任务
        self._file_watcher_task = asyncio.create_task(
            self._file_watcher_loop(),
            name='file_watcher'
        )

    async def _file_watcher_loop(self) -> None:
        """文件监视器轮询循环，每 3 秒检查一次文件修改时间。"""
        try:
            while self._running:
                try:
                    await self._check_file_changes()
                except Exception as e:
                    logger.warning("文件监视器检查异常: %s", e)
                await asyncio.sleep(3)
        except asyncio.CancelledError:
            logger.debug("文件监视器轮询循环已取消")
            raise

    async def _check_file_changes(self) -> None:
        """检查文件修改时间是否变化，变化时触发对应的刷新。

        - 'zxg.cfg' 变更 → 调用 _refresh_favorites()
        - 'blocknew.cfg' 变更 → 调用 _refresh_custom_blocks()
        """
        for logical_name, path in list(self._watched_paths.items()):
            if not path:
                continue

            current_mtime = self._get_file_mtime(path)
            last_mtime = self._watched_files.get(logical_name)

            if current_mtime is None:
                # 文件不存在或无法访问，跳过
                continue

            if last_mtime is None:
                # 首次记录修改时间
                self._watched_files[logical_name] = current_mtime
                continue

            if current_mtime != last_mtime:
                logger.info(
                    "文件监视器：检测到 %s (%s) 已变更 (mtime %s → %s)",
                    logical_name, path, last_mtime, current_mtime,
                )
                self._watched_files[logical_name] = current_mtime

                # 触发对应的刷新
                if logical_name == 'zxg.cfg':
                    await self._refresh_favorites()
                elif logical_name == 'blocknew.cfg':
                    await self._refresh_custom_blocks()

    # ------------------------------------------------------------------
    # 文件变更触发的刷新方法（Task 6）
    # ------------------------------------------------------------------

    @staticmethod
    def _stocks_to_member_list(stocks: List[Dict]) -> List[Dict]:
        """将标准股票列表转换为 storage 的成员格式。

        setcode → 市场前缀（SH/SZ/BJ），生成 [{'stock_code': 'SH600000'}, ...]

        Args:
            stocks: [{'setcode': 1, 'code': '600000', 'name': '...'}, ...]

        Returns:
            [{'stock_code': 'SH600000'}, ...]
        """
        market_map = {0: 'SZ', 1: 'SH', 2: 'BJ'}
        members = []
        for s in stocks:
            code = s.get('code', '')
            if not code:
                continue
            setcode = s.get('setcode', 0)
            market = market_map.get(setcode, 'SZ')
            members.append({'stock_code': f"{market}{code}"})
        return members

    async def _refresh_favorites(self) -> None:
        """重新从本地文件解析自选股，更新数据库，触发回调通知。

        流程：
        1. 通过 LocalFileProvider 重新解析自选股文件
        2. 通过 storage 更新数据库中的自选股记录
        3. 清除 resolver 缓存，确保后续 resolve 获取最新数据
        4. 更新内存快照（Copy-on-Write）
        5. 触发变更回调和刷新回调（WebSocket 推送等）
        """
        logger.info("_refresh_favorites: 开始刷新自选股")
        try:
            local_provider = self._get_local_provider()
            stocks: List[Dict] = []

            # 1. 从本地文件重新解析自选股
            if local_provider and hasattr(local_provider, 'get_user_sector'):
                try:
                    result = local_provider.get_user_sector()
                    if asyncio.iscoroutine(result):
                        result = await result
                    stocks = result.get('favorites', []) if result else []
                    logger.info(
                        "_refresh_favorites: 本地文件解析到 %d 只自选股", len(stocks),
                    )
                except Exception as e:
                    logger.warning("_refresh_favorites: 本地文件解析失败: %s", e)

            # 2. 更新数据库（通过 storage）
            if stocks:
                storage = getattr(self.resolver, '_storage', None)
                if storage is not None:
                    try:
                        members = self._stocks_to_member_list(stocks)
                        if hasattr(storage, 'upsert_user_block'):
                            storage.upsert_user_block(
                                block_code='ZXG',
                                block_name='自选股',
                                block_type='favorite',
                                source='local_file',
                            )
                        if hasattr(storage, 'update_user_block_members'):
                            storage.update_user_block_members(
                                block_code='ZXG',
                                members=members,
                                clear_existing=True,
                            )
                        logger.debug(
                            "_refresh_favorites: 已更新数据库，%d 条成员记录", len(members),
                        )
                    except Exception as e:
                        logger.warning("_refresh_favorites: 更新数据库失败: %s", e)

            # 3. 清除 resolver 缓存，确保后续 resolve 获取最新数据
            if hasattr(self.resolver, '_clear_cache'):
                self.resolver._clear_cache(3)

            # 4. 更新内存快照（Copy-on-Write）
            self._update_snapshot_cow('favorites', stocks)

            # 5. 触发变更回调和刷新回调
            self._notify_change('favorites', stocks)
            self._notify_refresh('favorites', stocks)

            logger.info("_refresh_favorites: 刷新完成，%d 只自选股", len(stocks))
        except Exception as e:
            logger.error("_refresh_favorites: 刷新失败: %s", e, exc_info=True)

    async def _refresh_custom_blocks(self) -> None:
        """重新从本地文件解析自定义板块，更新数据库，触发回调通知。

        流程：
        1. 通过 LocalFileProvider 重新解析自定义板块
        2. 对每个板块通过 storage 更新数据库记录
        3. 清除 resolver 缓存，确保后续 resolve 获取最新数据
        4. 更新各板块的内存快照（Copy-on-Write）
        5. 触发变更回调和刷新回调（WebSocket 推送等）
        """
        logger.info("_refresh_custom_blocks: 开始刷新自定义板块")
        try:
            local_provider = self._get_local_provider()
            custom_blocks: List[Dict] = []

            # 1. 从本地文件重新解析自定义板块
            if local_provider and hasattr(local_provider, 'get_user_sector'):
                try:
                    result = local_provider.get_user_sector()
                    if asyncio.iscoroutine(result):
                        result = await result
                    custom_blocks = result.get('custom_blocks', []) if result else []
                    logger.info(
                        "_refresh_custom_blocks: 本地文件解析到 %d 个自定义板块",
                        len(custom_blocks),
                    )
                except Exception as e:
                    logger.warning("_refresh_custom_blocks: 本地文件解析失败: %s", e)

            # 2. 更新数据库（通过 storage）
            storage = getattr(self.resolver, '_storage', None)
            all_stocks: List[Dict] = []
            for block in custom_blocks:
                block_code = block.get('block_code', '') or block.get('block_name', '')
                block_name = block.get('block_name', block_code)
                members_raw = block.get('members', [])

                if not block_code:
                    continue

                all_stocks.extend(members_raw)

                # 更新数据库
                if storage is not None and members_raw:
                    try:
                        members = self._stocks_to_member_list(members_raw)
                        if hasattr(storage, 'upsert_user_block'):
                            storage.upsert_user_block(
                                block_code=block_code,
                                block_name=block_name,
                                block_type='custom',
                                source='local_file',
                            )
                        if hasattr(storage, 'update_user_block_members'):
                            storage.update_user_block_members(
                                block_code=block_code,
                                members=members,
                                clear_existing=True,
                            )
                    except Exception as e:
                        logger.warning(
                            "_refresh_custom_blocks: 更新板块 '%s' 数据库失败: %s",
                            block_code, e,
                        )

                # 3. 更新该板块的内存快照
                task_key = f'block_{block_code}'
                self._update_snapshot_cow(task_key, members_raw)
                self._notify_change(task_key, members_raw)

            # 4. 清除 resolver 缓存，确保后续 resolve 获取最新数据
            if hasattr(self.resolver, '_clear_cache'):
                self.resolver._clear_cache(4)

            # 5. 触发刷新回调（WebSocket 推送等）
            self._notify_refresh('custom_blocks', all_stocks)

            logger.info(
                "_refresh_custom_blocks: 刷新完成，%d 个自定义板块，共 %d 只股票",
                len(custom_blocks), len(all_stocks),
            )
        except Exception as e:
            logger.error("_refresh_custom_blocks: 刷新失败: %s", e, exc_info=True)

    def _notify_refresh(self, entity_type: str, stocks: List[Dict]) -> None:
        """通过回调函数通知外部刷新发生（如 WebSocket 推送）。

        回调函数通过构造函数注入（refresh_callback），签名为：
            refresh_callback(entity_type: str, stock_list: List[Dict])

        若回调返回协程，则创建后台任务执行，不阻塞当前流程。

        Args:
            entity_type: 实体类型（'favorites' / 'custom_blocks'）
            stocks: 刷新后的股票列表
        """
        if self._refresh_callback is None:
            return

        try:
            result = self._refresh_callback(entity_type, stocks)
            if asyncio.iscoroutine(result):
                # 回调是协程，创建任务执行（不阻塞当前流程）
                asyncio.create_task(result)
            logger.debug(
                "_notify_refresh: 已通知刷新回调 entity_type=%s, %d 只股票",
                entity_type, len(stocks),
            )
        except Exception as e:
            logger.error("_notify_refresh: 回调调用失败: %s", e, exc_info=True)


# ═══════════════════════════════════════════════════════════════
# 备选池刷新检查（从 core/engine.py 剥离的非核心逻辑）
# ═══════════════════════════════════════════════════════════════

async def check_refreshed_pool_data(engine, nodes: Dict) -> None:
    """检查备选池是否有新数据可用（非阻塞）。

    在每次评估开始前检查刷新管理器是否有最新的备选池数据。
    仅对 data_config.json:refresh_rules 中声明的节点类型启用刷新。
    使用 asyncio 并发确保不阻塞主流程。

    Args:
        engine: PoolEngine 实例（提供 refresh_manager 与 _data_config）
        nodes: 节点配置字典
    """
    refresh_manager = getattr(engine, 'refresh_manager', None)
    if refresh_manager is None or not refresh_manager.is_running():
        return

    data_config = getattr(engine, '_data_config', {}) or {}
    refresh_rules = data_config.get('refresh_rules', {}) or {}

    try:
        for nid, node in nodes.items():
            if not isinstance(node, dict):
                continue

            node_type = node.get('type', '')
            params = node.get('params', {}) or {}

            rule = refresh_rules.get(node_type)
            if not rule:
                continue

            source = rule.get('source', '')
            key_param = rule.get('key_param', '')
            if key_param:
                key_val = params.get(key_param, '')
                if key_val:
                    task_key = f'{source}_{key_val}'
                    latest = await refresh_manager.get_latest_data(task_key)
                    if latest:
                        logger.debug("check_refreshed_pool_data: 节点 %s (type=%s/%s%s) 有 %d 条最新数据",
                                     nid, node_type, source, key_val, len(latest))
            else:
                latest = await refresh_manager.get_latest_data(source)
                if latest:
                    logger.debug("check_refreshed_pool_data: 节点 %s (type=%s/%s) 有 %d 条最新数据",
                                 nid, node_type, source, len(latest))
    except Exception as e:
        logger.warning("check_refreshed_pool_data: 检查备选池数据时出错（不影响主流程）: %s", e)
