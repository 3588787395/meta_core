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
import threading
import time
from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from .storage import Storage
from .providers.akshare_provider import AkShareProvider, DataSourceError
from .providers.tq import TqDllProvider

# === DataQuery ===
logger = logging.getLogger(__name__)

_MINUTE_PERIODS = ("1m", "5m", "15m", "30m", "60m")
_DAILY_PERIODS = ("1d", "1wk", "1mon")
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
            cfg_path = Path(__file__).parent.parent / "config" / "data_pipeline.json"
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
#   4. 暴露给 api/execution_api.py 在 /pools/{id}/run 启动前调用。
#
# 设计：
#   - DataSourceContract 负责读取契约配置，按 source_name 查找条目
#   - ProviderProbeRunner 负责实际执行 _probe() 调用，支持超时控制
#   - probe_or_raise() 统一入口：未就绪则按 on_unavailable 策略 raise / warn
#
# 禁止触碰 user/、sys/、Lib/ 等其他目录。

_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "data_source_contract.json"
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

    def __init__(self, config: Optional[Dict[str, Any]] = None, config_path: Optional[Path] = None):
        self._config = config if config is not None else _load_contract_config(config_path)
        self._sources: Dict[str, Dict[str, Any]] = self._config.get("sources", {}) or {}
        self._default_chain: List[str] = list(self._config.get("default_chain", []))
        self._global_policy: Dict[str, Any] = self._config.get("global_policy", {})
        # spec API: 内部 provider 缓存 + 显式 mock 同意标志
        self.providers: Dict[str, Any] = {}
        self._explicit_consent: bool = False

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
