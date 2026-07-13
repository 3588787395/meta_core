"""candidate_pool.py - 备选池解析与刷新管理（合并自 candidate_pool_resolver / candidate_pool_refresh_manager）。"""

import asyncio
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, time as dt_time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from ..converters.dzh import load_dzh_market_mappings, _DZH_RELOAD_SCHEDULE

logger = logging.getLogger(__name__)


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

    def __init__(self, storage: Any, providers: Dict[str, Any]):
        """初始化备选池解析器。

        Args:
            storage: Storage 实例（用于数据库操作，如 user_blocks 表查询）
            providers: 数据源提供者字典 {
                'tq_dll': TqDllProvider 实例,
                'akshare': AkShareProvider 实例,
                ...
            }
        """
        self._storage = storage
        self._providers = providers
        self._cache: Dict[int, Tuple[List[Dict], datetime]] = {}
        self._cache_lock = asyncio.Lock()

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

        mappings = load_dzh_market_mappings()
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
        scheduling = _DZH_RELOAD_SCHEDULE.get("scheduling", {})
        entry = scheduling.get(reload_mode)
        if entry is None:
            logger.warning("未知 reload_mode=%s，回退为 on_startup", reload_mode)
            entry = scheduling.get(
                _DZH_RELOAD_SCHEDULE.get("default_mode", "on_startup"), {})
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
        engine: MetaEngine 实例（提供 refresh_manager 与 _data_config）
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
