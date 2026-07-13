"""
东方财富在线数据源提供者。

基于 AKShare 的东方财富接口获取板块和成分股数据，
作为 AkShareProvider 的专用补充数据源。

在线数据源，网络不可用时返回空列表，不抛出异常。
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from . import DataSourceProvider

logger = logging.getLogger(__name__)


_CONFIG_CACHE: Dict[str, Any] = {}


def _load_config(name: str) -> Dict[str, Any]:
    """加载 meta_core/config 下的 JSON 配置表（带缓存）。"""
    if name in _CONFIG_CACHE:
        return _CONFIG_CACHE[name]
    cfg_path = Path(__file__).resolve().parent.parent.parent / "config" / name
    try:
        with open(cfg_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("加载配置 %s 失败: %s", name, e)
        data = {}
    _CONFIG_CACHE[name] = data
    return data


class DfcfProvider(DataSourceProvider):
    """东方财富在线数据源提供者。

    通过 AKShare 的东方财富接口获取板块和成分股数据。
    在线数据源，网络不可用时返回空列表，不抛出异常。
    """

    def __init__(self):
        self._ready = False
        self._ak = None
        try:
            import akshare as ak
            self._ak = ak
            self._ready = True
            logger.info("DfcfProvider 初始化成功")
        except ImportError:
            logger.warning("akshare 未安装，DfcfProvider 不可用")
        except Exception as e:
            logger.warning("DfcfProvider 初始化失败: %s", e)

    # ------------------------------------------------------------------
    # 基础状态
    # ------------------------------------------------------------------

    def is_ready(self) -> bool:
        """检查 akshare 是否可导入。不可用时记录 warning 日志。"""
        if not self._ready:
            logger.warning("DfcfProvider 不可用：akshare 未安装或初始化失败")
        return self._ready

    def get_mode_info(self) -> str:
        return "dfcf"

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_to_tq_code(code: str) -> str:
        """将纯数字代码标准化为 XXXXXX.SH / XXXXXX.SZ / XXXXXX.BJ 格式。

        依据 config/market_classifications.json 的 code_prefix_rules：
        6 → SH, 0/3 → SZ, 4/8 → BJ
        """
        code = str(code).strip()
        if not code:
            return code
        if '.' in code:
            return code.upper()
        for rule in _load_config("market_classifications.json").get("code_prefix_rules", []):
            prefix = rule.get("prefix", "")
            market = rule.get("market", "")
            if prefix and market and code.startswith(prefix):
                return f"{code}.{market}"
        return code

    @staticmethod
    def _get_setcode_by_code(code: str) -> int:
        """根据股票代码判断市场编号。setcode: 0=深圳, 1=上海, 2=北交。"""
        for rule in _load_config("market_classifications.json").get("code_prefix_rules", []):
            prefix = rule.get("prefix", "")
            if prefix and code.startswith(prefix):
                return rule.get("setcode", 0)
        return 0

    @staticmethod
    def _resolve_list_type(list_type) -> str:
        """将 list_type 解析为 'industry' 或 'concept'。

        - 'industry' 或 11: 行业板块
        - 'concept' 或 12: 概念板块
        """
        if isinstance(list_type, str):
            lt = list_type.lower()
            if lt in ('industry', 'concept'):
                return lt
            try:
                lt_int = int(list_type)
            except (ValueError, TypeError):
                return 'industry'
        else:
            try:
                lt_int = int(list_type)
            except (ValueError, TypeError):
                return 'industry'
        # HARDCODED: 不可剥离，理由：11/12 是东方财富板块类型协议常量
        if lt_int == 12:
            return 'concept'
        return 'industry'

    @staticmethod
    def _resolve_block_type(block_type) -> str:
        """将 block_type 解析为 'industry' 或 'concept'。

        - 0 或 'industry': 行业板块
        - 1 或 'concept': 概念板块
        """
        if isinstance(block_type, str):
            bt = block_type.lower()
            if bt in ('industry', 'concept'):
                return bt
            try:
                bt_int = int(block_type)
            except (ValueError, TypeError):
                return 'industry'
        else:
            try:
                bt_int = int(block_type)
            except (ValueError, TypeError):
                return 'industry'
        # HARDCODED: 不可剥离，理由：0/1 是板块类型协议常量
        if bt_int == 1:
            return 'concept'
        return 'industry'

    # ------------------------------------------------------------------
    # 板块列表
    # ------------------------------------------------------------------

    def get_sector_list(self, list_type=1) -> List[Dict]:
        """获取东方财富板块列表。

        Args:
            list_type:
                'industry' 或 11: 行业板块 (ak.stock_board_industry_name_em)
                'concept' 或 12: 概念板块 (ak.stock_board_concept_name_em)

        Returns:
            [{'sector_code': '...', 'sector_name': '银行',
              'category': 'industry', 'source': 'dfcf', 'member_count': 45}, ...]
        """
        if not self._ready:
            return []

        category = self._resolve_list_type(list_type)
        if category == 'concept':
            method_name = 'stock_board_concept_name_em'
        else:
            method_name = 'stock_board_industry_name_em'

        method = getattr(self._ak, method_name, None)
        if method is None:
            logger.warning("akshare 未找到板块列表方法: %s", method_name)
            return []

        try:
            df = method()
        except Exception as e:
            logger.warning("获取东方财富%s板块列表失败: %s", category, e, exc_info=True)
            return []

        if df is None or df.empty:
            logger.warning("东方财富%s板块列表返回空数据", category)
            return []

        sectors: List[Dict] = []
        try:
            for _, row in df.iterrows():
                sector_code = str(row.get('板块代码', ''))
                sector_name = str(row.get('板块名称', ''))
                # 成分股数量：使用上涨家数 + 下跌家数估算
                member_count = 0
                try:
                    up_count = int(row.get('上涨家数', 0) or 0)
                    down_count = int(row.get('下跌家数', 0) or 0)
                    member_count = up_count + down_count
                except (ValueError, TypeError):
                    member_count = 0

                sectors.append({
                    'sector_code': sector_code,
                    'sector_name': sector_name,
                    'category': category,
                    'source': 'dfcf',
                    'member_count': member_count,
                })
            logger.info("获取东方财富%s板块列表成功，共 %d 个", category, len(sectors))
            return sectors
        except Exception as e:
            logger.warning("解析东方财富%s板块列表失败: %s", category, e, exc_info=True)
            return []

    # ------------------------------------------------------------------
    # 板块成分股
    # ------------------------------------------------------------------

    def get_sector_stocks(self, sector_code, block_type=0) -> List[str]:
        """获取东方财富板块成分股代码列表。

        Args:
            sector_code: 板块代码或板块名称
            block_type:
                0 或 'industry': 行业板块 (ak.stock_board_industry_cons_em)
                1 或 'concept': 概念板块 (ak.stock_board_concept_cons_em)

        Returns:
            标准化代码格式 ['600000.SH', '600015.SH', ...]
        """
        if not self._ready:
            return []

        category = self._resolve_block_type(block_type)
        if category == 'concept':
            method_name = 'stock_board_concept_cons_em'
        else:
            method_name = 'stock_board_industry_cons_em'

        method = getattr(self._ak, method_name, None)
        if method is None:
            logger.warning("akshare 未找到板块成分股方法: %s", method_name)
            return []

        # AKShare 需要 symbol=板块名称
        sector_name = str(sector_code)

        # 如果传入的是纯数字板块代码，尝试通过 get_sector_list 查找名称
        if sector_name.isdigit():
            list_type = 'concept' if category == 'concept' else 'industry'
            try:
                sectors = self.get_sector_list(list_type=list_type)
                for s in sectors:
                    if s.get('sector_code') == sector_name:
                        sector_name = s.get('sector_name', sector_name)
                        break
            except Exception as e:
                logger.warning("解析板块代码 %s 名称失败: %s", sector_code, e)

        try:
            df = method(symbol=sector_name)
        except Exception as e:
            logger.warning("获取东方财富%s板块 %s 成分股失败: %s",
                           category, sector_name, e, exc_info=True)
            return []

        if df is None or df.empty:
            logger.warning("东方财富%s板块 %s 成分股返回空数据",
                           category, sector_name)
            return []

        codes: List[str] = []
        try:
            for _, row in df.iterrows():
                raw_code = str(row.get('代码', ''))
                if not raw_code:
                    continue
                tq_code = self._normalize_to_tq_code(raw_code)
                codes.append(tq_code)
            logger.info("获取东方财富%s板块 %s 成分股成功，共 %d 只",
                        category, sector_name, len(codes))
            return codes
        except Exception as e:
            logger.warning("解析东方财富%s板块 %s 成分股失败: %s",
                           category, sector_name, e, exc_info=True)
            return []

    # ------------------------------------------------------------------
    # 股票列表
    # ------------------------------------------------------------------

    def get_stock_list_by_type(self, list_type, customblockname='', **kwargs) -> List[Dict]:
        """按类型获取股票列表。

        Args:
            list_type:
                2 或 'all_a': 全部 A 股 (ak.stock_zh_a_spot_em)
            customblockname: 自定义板块名称（本数据源暂不支持）

        Returns:
            [{'setcode': 1, 'code': '600000', 'name': '浦发银行'}, ...]
            setcode: 0=深圳, 1=上海, 2=北交
            code: 纯数字（不含 .SH/.SZ 后缀）
        """
        if not self._ready:
            return []

        # 解析 list_type 是否为全部 A 股请求
        should_fetch_all_a = False
        if isinstance(list_type, str):
            if list_type.lower() == 'all_a':
                should_fetch_all_a = True
            else:
                try:
                    # HARDCODED: 不可剥离，理由：2 是 DZH spinfo.type 全 A 股协议常量
                    if int(list_type) == 2:
                        should_fetch_all_a = True
                except (ValueError, TypeError):
                    pass
        else:
            try:
                # HARDCODED: 不可剥离，理由：2 是 DZH spinfo.type 全 A 股协议常量
                if int(list_type) == 2:
                    should_fetch_all_a = True
            except (ValueError, TypeError):
                pass

        if not should_fetch_all_a:
            return []

        try:
            df = self._ak.stock_zh_a_spot_em()
        except Exception as e:
            logger.warning("获取全部 A 股列表失败: %s", e)
            return []

        if df is None or df.empty:
            return []

        stocks: List[Dict] = []
        try:
            for _, row in df.iterrows():
                raw_code = str(row.get('代码', ''))
                if not raw_code:
                    continue
                name = str(row.get('名称', ''))
                setcode = self._get_setcode_by_code(raw_code)
                stocks.append({
                    'setcode': setcode,
                    'code': raw_code,
                    'name': name,
                })
            return stocks
        except Exception as e:
            logger.warning("解析全部 A 股列表失败: %s", e)
            return []
