"""数据库同步服务：编排各 Provider 数据同步到 SQLite 数据库。

职责：
    - 从各 Provider（local_file/dfcf/akshare/tq_dll）获取股票、板块、自选股等数据
    - 转换为 Storage 层所需格式（统一股票代码为 SH600000/SZ000001/BJ430047 格式）
    - 通过 Storage 对象写入数据库（不直接操作 sqlite3）
    - 记录同步日志到 data_source_sync_log 表
    - 每个 provider 的同步独立 try/except，一个失败不影响其他

Provider 接口约定：
    - local_file: get_user_sector() / get_sector_list() / get_block_members() / get_system_sectors()
    - dfcf:       get_sector_list() / get_sector_stocks() / get_stock_list_by_type()
    - akshare:    get_sector_list() / get_sector_stocks() / get_stock_list_by_type()
    - tq_dll:     get_stock_list_by_type() 等
"""

import logging
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# setcode 到市场标识的映射：0=SZ(深圳), 1=SH(上海), 2=BJ(北京)
_SETCODE_TO_MARKET = {0: 'SZ', 1: 'SH', 2: 'BJ'}


class DatabaseSyncService:
    """数据库同步服务：编排各 Provider 数据同步到 SQLite 数据库。

    通过 storage 对象执行所有数据库操作，不直接操作 sqlite3。
    provider 不可用时（is_ready() 返回 False）跳过并记录 warning，不抛出异常。
    """

    def __init__(self, storage, providers: Dict[str, Any]):
        """
        Args:
            storage: Storage 实例（用于数据库操作）
            providers: 数据源提供者字典，如
                {'local_file': ..., 'dfcf': ..., 'akshare': ..., 'tq_dll': ...}
        """
        self._storage = storage
        self._providers = providers or {}

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _get_providers(self, source: str = 'all') -> List[Tuple[str, Any]]:
        """获取可用的 provider 列表。

        Args:
            source: 'all' 遍历所有 provider；否则仅取指定名称的 provider

        Returns:
            [(source_name, provider), ...] 仅包含 is_ready() 为 True 的 provider
        """
        if source == 'all':
            result = []
            for name, provider in self._providers.items():
                if self._is_provider_ready(name, provider):
                    result.append((name, provider))
            return result
        provider = self._providers.get(source)
        if provider is None:
            logger.warning("未找到 provider: %s", source)
            return []
        if self._is_provider_ready(source, provider):
            return [(source, provider)]
        return []

    @staticmethod
    def _is_provider_ready(name: str, provider) -> bool:
        """检查 provider 是否就绪，不可用时记录 warning。"""
        if provider is None:
            logger.warning("provider %s 为 None，跳过同步", name)
            return False
        try:
            ready = provider.is_ready()
        except Exception as e:
            logger.warning("provider %s is_ready() 异常: %s", name, e)
            return False
        if not ready:
            logger.warning("provider %s 不可用，跳过同步", name)
        return ready

    @staticmethod
    def _setcode_to_market(setcode: int) -> str:
        """setcode 转市场标识：1=SH, 0=SZ, 2=BJ。无法识别时默认 SZ。"""
        try:
            sc = int(setcode)
        except (ValueError, TypeError):
            sc = 0
        return _SETCODE_TO_MARKET.get(sc, 'SZ')

    @staticmethod
    def _tq_code_to_stock_code(tq_code: str) -> str:
        """将 provider 返回的 '600000.SH' 格式转为 storage 的 'SH600000' 格式。

        若输入已是 'SH600000' 格式或纯代码，则原样返回。
        """
        tq_code = str(tq_code).strip()
        if not tq_code:
            return tq_code
        if '.' in tq_code:
            parts = tq_code.split('.', 1)
            code = parts[0]
            market = parts[1].upper()
            return f"{market}{code}"
        return tq_code

    def _convert_stock(self, item: Dict) -> Optional[Dict]:
        """将 provider 返回的股票字典转为 storage.upsert_stocks() 所需格式。

        Provider 格式: {'setcode': 1, 'code': '600000', 'name': '浦发银行'}
        Storage 格式:  {'stock_code': 'SH600000', 'raw_code': '600000',
                        'name': '浦发银行', 'market': 'SH'}
        """
        code = str(item.get('code', '')).strip()
        if not code:
            return None
        setcode = item.get('setcode', 0)
        market = self._setcode_to_market(setcode)
        return {
            'stock_code': f"{market}{code}",
            'raw_code': code,
            'name': item.get('name', ''),
            'market': market,
        }

    def _ensure_stocks_exist(self, stock_codes: List[str]) -> int:
        """确保股票代码在 stocks 表中存在，不存在则插入最小记录。

        用于在写入 sector_members / user_block_members 关系表前，
        先 upsert 缺失的股票记录，避免 FK 约束失败。

        Args:
            stock_codes: 股票代码列表（'SH600000' 格式）

        Returns:
            新插入的记录数
        """
        if not stock_codes:
            return 0
        stocks_data: List[Dict] = []
        for sc in stock_codes:
            sc = str(sc).strip()
            if not sc or len(sc) < 3:
                continue
            market = sc[:2] if sc[:2] in ('SH', 'SZ', 'BJ') else ''
            raw_code = sc[2:] if market else sc
            if not raw_code:
                continue
            stocks_data.append({
                'stock_code': sc,
                'raw_code': raw_code,
                'name': '',
                'market': market or 'SH',
            })
        if stocks_data:
            # 插入的股票名称均为空，记录 warning 便于追踪（后续由 _backfill_stock_names 补全）
            sample_codes = [s['stock_code'] for s in stocks_data[:5]]
            logger.warning(
                "_ensure_stocks_exist: 插入 %d 只股票名称为空（示例: %s），"
                "后续将通过 _backfill_stock_names 补全",
                len(stocks_data), sample_codes)
            return self._storage.upsert_stocks(stocks_data)
        return 0

    def _backfill_stock_names(self, batch_codes: List[str]) -> int:
        """批量从 DFCF/AKShare provider 查询股票名称并补全到 stocks 表。

        用于在 sync_sector_members 完成后，为通过 _ensure_stocks_exist
        插入的空名称股票补全 name 字段，确保展开板块时能显示"SH600000 浦发银行"。

        Args:
            batch_codes: 股票代码列表（'SH600000' 格式）

        Returns:
            成功补全名称的记录数
        """
        if not batch_codes:
            return 0

        # 优先使用 DFCF provider，其次 AKShare
        provider = None
        provider_name = ''
        for pname in ('dfcf', 'akshare'):
            p = self._providers.get(pname)
            if self._is_provider_ready(pname, p):
                provider = p
                provider_name = pname
                break

        if provider is None:
            logger.warning("补全股票名称失败：无可用 provider（dfcf/akshare）")
            return 0

        # 从 provider 获取全部 A 股列表（含名称）
        try:
            stocks = provider.get_stock_list_by_type(list_type=2)
        except Exception as e:
            logger.warning("从 %s 获取股票列表失败: %s", provider_name, e)
            return 0

        if not stocks:
            logger.warning("从 %s 获取股票列表为空，无法补全名称", provider_name)
            return 0

        # 构建 stock_code -> name 映射（复用 _convert_stock 统一代码格式）
        code_to_name: Dict[str, str] = {}
        for item in stocks:
            converted = self._convert_stock(item)
            if converted and converted['name']:
                code_to_name[converted['stock_code']] = converted['name']

        if not code_to_name:
            logger.warning("从 %s 获取的股票列表均无名称，无法补全", provider_name)
            return 0

        # 过滤出 batch_codes 中需要补全且能匹配到名称的股票
        batch_set = {str(c).strip() for c in batch_codes}
        updates = {
            sc: name for sc, name in code_to_name.items()
            if sc in batch_set
        }

        if not updates:
            logger.warning(
                "批量补全名称：batch_codes 共 %d 个，但未匹配到任何名称",
                len(batch_codes))
            return 0

        # 直接 UPDATE stocks 表的 name 字段（仅更新空名称行，避免覆盖已有名称）
        count = 0
        conn = None
        try:
            conn = sqlite3.connect(self._storage.db_path)
            for stock_code, name in updates.items():
                cursor = conn.execute(
                    "UPDATE stocks SET name = ?, "
                    "updated_at = datetime('now','localtime') "
                    "WHERE stock_code = ? AND (name IS NULL OR name = '')",
                    (name, stock_code))
                count += cursor.rowcount
            conn.commit()
        except Exception as e:
            logger.warning("更新股票名称到数据库失败: %s", e)
            if conn:
                conn.rollback()
            return 0
        finally:
            if conn:
                conn.close()

        logger.info("从 %s 批量补全股票名称 %d 条（共 %d 个待补全）",
                    provider_name, count, len(batch_codes))
        return count

    # ------------------------------------------------------------------
    # 全量同步
    # ------------------------------------------------------------------

    async def sync_all(self) -> Dict:
        """全量同步所有数据源，返回汇总报告。

        依次调用 sync_stocks / sync_sectors / sync_sector_members /
        sync_favorites / sync_custom_blocks，每个阶段独立 try/except。
        """
        report: Dict[str, Any] = {
            'started_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'stocks': {},
            'sectors': {},
            'sector_members': {},
            'favorites': {},
            'custom_blocks': {},
        }

        for phase, method in [
            ('stocks', self.sync_stocks),
            ('sectors', self.sync_sectors),
            ('sector_members', self.sync_sector_members),
            ('favorites', self.sync_favorites),
            ('custom_blocks', self.sync_custom_blocks),
        ]:
            try:
                report[phase] = await method()
            except Exception as e:
                logger.error("sync_all 阶段 %s 失败: %s", phase, e)
                report[phase] = {'success': False, 'error': str(e)}

        report['completed_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        return report

    # ------------------------------------------------------------------
    # 同步股票列表
    # ------------------------------------------------------------------

    async def sync_stocks(self, source: str = 'all') -> Dict:
        """同步股票列表到 stocks 表。

        从 provider.get_stock_list_by_type(list_type=2) 获取全部 A 股，
        转换格式后调用 storage.upsert_stocks() 写入。

        Args:
            source: 'all' 遍历所有 provider；或指定 provider 名称
        """
        providers = self._get_providers(source)
        result: Dict[str, Any] = {'source': source, 'providers': {}}

        for src_name, provider in providers:
            try:
                # list_type=2 表示全部 A 股
                stocks = provider.get_stock_list_by_type(list_type=2)
                if not stocks:
                    self._storage.record_sync_log(src_name, 'stocks', 'success', 0)
                    result['providers'][src_name] = {'success': True, 'count': 0}
                    continue

                stocks_data: List[Dict] = []
                for item in stocks:
                    converted = self._convert_stock(item)
                    if converted:
                        stocks_data.append(converted)

                count = self._storage.upsert_stocks(stocks_data)
                self._storage.record_sync_log(src_name, 'stocks', 'success', count)
                result['providers'][src_name] = {'success': True, 'count': count}
                # 统计含名称的股票数量，便于确认 name 字段已写入
                named_count = sum(1 for s in stocks_data if s.get('name'))
                logger.info("从 %s 同步股票 %d 只（其中 %d 只有名称）",
                            src_name, count, named_count)
            except Exception as e:
                logger.error("从 %s 同步股票失败: %s", src_name, e)
                self._storage.record_sync_log(src_name, 'stocks', 'failed', 0, str(e))
                result['providers'][src_name] = {'success': False, 'error': str(e)}

        return result

    # ------------------------------------------------------------------
    # 同步板块列表
    # ------------------------------------------------------------------

    async def sync_sectors(self, source: str = 'all', category: str = None) -> Dict:
        """同步板块列表到 sectors 表。

        - local_file: get_system_sectors() 获取系统板块 + get_sector_list(list_type=1) 获取自定义板块
        - dfcf: get_sector_list(list_type='industry') 和 get_sector_list(list_type='concept')
        - 其他 provider: get_sector_list(list_type=1)

        Args:
            source: 'all' 遍历所有 provider；或指定 provider 名称；
                    也支持子数据源 'tdx_local'/'dzh_local'/'ths_local'（映射到 local_file provider）
            category: 板块分类过滤（industry/concept/custom 等），None 表示全部
        """
        # 子数据源映射到 local_file provider
        sub_source_map = {
            'tdx_local': 'local_file',
            'dzh_local': 'local_file',
            'ths_local': 'local_file',
        }
        actual_source = sub_source_map.get(source, source)
        providers = self._get_providers(actual_source)
        result: Dict[str, Any] = {
            'source': source, 'category': category, 'providers': {}}

        for src_name, provider in providers:
            try:
                if src_name == 'local_file':
                    sectors_data = self._collect_local_file_sectors(provider, category)
                    # 如果指定了子数据源，过滤只保留该 source 的板块
                    if source in sub_source_map:
                        sectors_data = [
                            s for s in sectors_data
                            if s.get('source') == source
                        ]
                elif src_name == 'dfcf':
                    sectors_data = self._collect_dfcf_sectors(provider, category)
                else:
                    sectors_data = self._collect_generic_sectors(
                        src_name, provider, category)

                count = self._storage.upsert_sectors(sectors_data)
                # 记录同步日志时使用原始 source 名称（支持子数据源）
                log_source = source if source in sub_source_map else src_name
                self._storage.record_sync_log(log_source, 'sectors', 'success', count)
                result['providers'][log_source] = {'success': True, 'count': count}
                if count == 0:
                    logger.warning(
                        "从 %s 同步板块 0 个（可能数据源不可用或网络异常）",
                        log_source)
                else:
                    logger.info("从 %s 同步板块 %d 个", log_source, count)
            except Exception as e:
                logger.error("从 %s 同步板块失败: %s", source, e,
                             exc_info=True)
                log_source = source if source in sub_source_map else src_name
                self._storage.record_sync_log(log_source, 'sectors', 'failed', 0, str(e))
                result['providers'][log_source] = {'success': False, 'error': str(e)}

        return result

    def _collect_local_file_sectors(self, provider, category: str) -> List[Dict]:
        """收集 local_file provider 的板块（系统板块 + 自定义板块）。

        系统板块按数据源分组（tdx_local/dzh_local/ths_local），
        source 字段细化以区分来源。
        """
        sectors_data: List[Dict] = []

        # 系统板块（来自 get_system_sectors，按数据源和类型分组）
        if category is None or category != 'custom':
            try:
                grouped_sectors = provider.get_system_sectors()
            except Exception as e:
                logger.warning("local_file get_system_sectors 失败: %s", e)
                grouped_sectors = {}
            # 新格式：{source_key: {cat: [items]}}
            source_map = {
                'tdx': 'tdx_local', 'dzh': 'dzh_local', 'ths': 'ths_local'
            }
            for source_key, categories in grouped_sectors.items():
                source_name = source_map.get(source_key, source_key)
                for cat, items in categories.items():
                    if category is not None and cat != category:
                        continue
                    for item in items:
                        sector_name = item.get('name', '')
                        sector_code = item.get('code', sector_name)
                        if not sector_name:
                            continue
                        sectors_data.append({
                            'sector_id': f"{source_name}_{cat}_{sector_name}",
                            'sector_code': sector_code,
                            'sector_name': sector_name,
                            'category': cat,
                            'source': source_name,
                            'description': item.get('description'),
                            'member_count': item.get('member_count', 0),
                        })

        # 自定义板块（来自 get_sector_list(list_type=1)）
        if category is None or category == 'custom':
            try:
                custom_blocks = provider.get_sector_list(list_type=1)
            except Exception as e:
                logger.warning("local_file get_sector_list 失败: %s", e)
                custom_blocks = []
            for blk in custom_blocks:
                sector_code = blk.get('block_code') or blk.get('code', '')
                sector_name = blk.get('block_name') or blk.get('name', '')
                if not sector_code:
                    continue
                sectors_data.append({
                    'sector_id': f"local_file_custom_{sector_code}",
                    'sector_code': sector_code,
                    'sector_name': sector_name,
                    'category': 'custom',
                    'source': 'local_file',
                    'member_count': blk.get('member_count', 0),
                })

        return sectors_data

    def _collect_dfcf_sectors(self, provider, category: str) -> List[Dict]:
        """收集 dfcf provider 的板块（行业板块 + 概念板块）。"""
        sectors_data: List[Dict] = []
        if category is None:
            categories = ['industry', 'concept']
        elif category in ('industry', 'concept'):
            categories = [category]
        else:
            return sectors_data

        for cat in categories:
            try:
                sectors = provider.get_sector_list(list_type=cat)
            except Exception as e:
                logger.warning("dfcf get_sector_list(%s) 失败: %s",
                               cat, e, exc_info=True)
                continue
            if not sectors:
                logger.warning(
                    "dfcf %s 板块列表为空（可能网络不可用或 AKShare 接口异常）",
                    cat)
                continue
            for sec in sectors:
                sector_code = sec.get('sector_code', '')
                sector_name = sec.get('sector_name', '')
                if not sector_name:
                    continue
                sectors_data.append({
                    'sector_id': f"dfcf_{sector_name}",
                    'sector_code': sector_code,
                    'sector_name': sector_name,
                    'category': cat,
                    'source': 'dfcf',
                    'member_count': sec.get('member_count', 0),
                })

        return sectors_data

    def _collect_generic_sectors(self, src_name: str, provider,
                                  category: str) -> List[Dict]:
        """收集通用 provider 的板块（akshare/tq_dll 等）。"""
        sectors_data: List[Dict] = []
        try:
            sectors = provider.get_sector_list(list_type=1)
        except Exception as e:
            logger.warning("%s get_sector_list 失败: %s", src_name, e)
            return sectors_data

        for sec in sectors:
            sector_code = sec.get('sector_code') or sec.get('code', '')
            sector_name = sec.get('sector_name') or sec.get('name', '')
            if not sector_name:
                continue
            cat = category or sec.get('category', 'other')
            sectors_data.append({
                'sector_id': f"{src_name}_{sector_name}",
                'sector_code': sector_code,
                'sector_name': sector_name,
                'category': cat,
                'source': src_name,
                'member_count': sec.get('member_count', 0),
            })

        return sectors_data

    # ------------------------------------------------------------------
    # 同步板块成分股
    # ------------------------------------------------------------------

    async def sync_sector_members(self, source: str = 'all',
                                   sector_id: str = None) -> Dict:
        """同步板块成分股到 sector_members 表。

        从 storage.get_sectors_catalog() 获取已同步的板块列表，
        对每个板块从 provider 获取成分股，调用 storage.upsert_sector_members() 写入。

        Args:
            source: 'all' 遍历所有 provider；或指定 provider 名称；
                    也支持子数据源 'tdx_local'/'dzh_local'/'ths_local'
            sector_id: 指定板块ID，None 表示同步全部板块
        """
        # 子数据源映射到 local_file provider
        sub_source_map = {
            'tdx_local': 'local_file',
            'dzh_local': 'local_file',
            'ths_local': 'local_file',
        }
        actual_source = sub_source_map.get(source, source)
        providers = self._get_providers(actual_source)
        result: Dict[str, Any] = {
            'source': source, 'sector_id': sector_id, 'providers': {}}

        for src_name, provider in providers:
            try:
                # 获取该数据源已同步的板块列表
                # 如果指定了子数据源，按子数据源 source 字段过滤
                if source in sub_source_map:
                    catalog = self._storage.get_sectors_catalog(source=source)
                else:
                    catalog = self._storage.get_sectors_catalog(source=src_name)
                if sector_id is not None:
                    catalog = [s for s in catalog
                               if s.get('sector_id') == sector_id]

                total = 0
                failed_sectors = 0
                for sec in catalog:
                    sid = sec.get('sector_id', '')
                    if not sid:
                        continue
                    try:
                        members = self._get_sector_members(
                            provider, src_name, sec)
                        member_data = [
                            {'stock_code': self._tq_code_to_stock_code(c)}
                            for c in (members or [])
                        ]
                        # 先确保股票记录存在，避免 FK 约束失败
                        if member_data:
                            self._ensure_stocks_exist(
                                [m['stock_code'] for m in member_data])
                        total += self._storage.upsert_sector_members(sid, member_data)
                    except Exception as e:
                        logger.warning("获取板块 %s 成分股失败: %s", sid, e)
                        failed_sectors += 1

                status = 'success' if failed_sectors == 0 else 'partial'
                log_source = source if source in sub_source_map else src_name
                self._storage.record_sync_log(
                    log_source, 'sector_members', status, total)
                result['providers'][log_source] = {
                    'success': True,
                    'count': total,
                    'failed_sectors': failed_sectors,
                }
                logger.info("从 %s 同步板块成分股 %d 条（失败板块 %d 个）",
                            log_source, total, failed_sectors)
            except Exception as e:
                logger.error("从 %s 同步板块成分股失败: %s", source, e)
                log_source = source if source in sub_source_map else src_name
                self._storage.record_sync_log(
                    log_source, 'sector_members', 'failed', 0, str(e))
                result['providers'][log_source] = {'success': False, 'error': str(e)}

        # 同步完成后，检查并补全空名称股票（storage.py 无查询方法，通过 db_path 只读查询）
        try:
            conn = sqlite3.connect(self._storage.db_path)
            try:
                rows = conn.execute(
                    "SELECT stock_code FROM stocks WHERE name IS NULL OR name = ''"
                ).fetchall()
            finally:
                conn.close()
            empty_name_codes = [row[0] for row in rows if row[0]]
            if empty_name_codes:
                logger.info("发现 %d 只空名称股票，开始批量补全",
                            len(empty_name_codes))
                backfilled = self._backfill_stock_names(empty_name_codes)
                result['backfilled_names'] = backfilled
            else:
                result['backfilled_names'] = 0
        except Exception as e:
            logger.warning("检查/补全空名称股票失败: %s", e)
            result['backfilled_names'] = 0

        return result

    async def sync_sector_members_by_source(self, source: str) -> Dict:
        """按数据源同步板块成分股（便捷方法）。

        先同步板块列表，再同步成分股。

        Args:
            source: 数据源名称（tdx_local/dzh_local/ths_local/dfcf/all）
        """
        report: Dict[str, Any] = {'source': source}
        # 1. 同步板块列表
        report['sectors'] = await self.sync_sectors(source=source)
        # 2. 同步成分股
        report['sector_members'] = await self.sync_sector_members(source=source)
        return report

    def _get_sector_members(self, provider, src_name: str,
                             sector: Dict) -> List[str]:
        """从 provider 获取板块成分股代码列表（'600000.SH' 格式）。

        根据 provider 类型选择合适的接口：
        - dfcf: get_sector_stocks(sector_name, block_type)
        - local_file / tdx_local / dzh_local / ths_local: 优先使用板块记录中
          已有的 members 字段（来自 get_system_sectors 的新解析方法），
          降级到 get_block_members，再降级到 DFCF
        - 其他: get_sector_stocks(sector_code, block_type)
        """
        sector_code = sector.get('sector_code', '')
        sector_name = sector.get('sector_name', '')
        category = sector.get('category', '')
        sector_source = sector.get('source', src_name)

        # 本地文件数据源（含子数据源 tdx_local/dzh_local/ths_local）
        local_sources = {'local_file', 'tdx_local', 'dzh_local', 'ths_local'}
        if src_name == 'local_file' or sector_source in local_sources:
            # 1. 优先使用板块记录中已有的 members 字段
            # （来自 get_system_sectors 的新解析方法，如 THS BlockUpdate/DZH ABK）
            existing_members = sector.get('members')
            if existing_members and isinstance(existing_members, list):
                # members 格式为 ['SH600000', 'SZ000001', ...]
                # 转换为 '600000.SH' 格式
                converted = []
                for m in existing_members:
                    m = str(m).strip()
                    if not m or len(m) < 3:
                        continue
                    if '.' in m:
                        converted.append(m)
                    elif m[:2] in ('SH', 'SZ', 'BJ'):
                        converted.append(f"{m[2:]}.{m[:2]}")
                    else:
                        converted.append(m)
                if converted:
                    return converted

            # 2. 降级到 get_block_members
            members = provider.get_block_members(sector_code or sector_name)
            if members:
                return members

            # 3. 系统板块本地文件无成分股，降级到 DFCF
            if category in ('concept', 'industry', 'index', 'style', 'region'):
                dfcf = self._providers.get('dfcf')
                if self._is_provider_ready('dfcf', dfcf):
                    try:
                        block_type = 1 if category == 'concept' else 0
                        dfcf_members = dfcf.get_sector_stocks(
                            sector_name, block_type=block_type)
                        if dfcf_members:
                            logger.info(
                                "板块 %s(%s) 从 DFCF 获取 %d 只成分股",
                                sector_name, category, len(dfcf_members))
                            return dfcf_members
                        logger.debug(
                            "板块 %s(%s) DFCF 也无成分股数据", sector_name, category)
                    except Exception as e:
                        logger.warning(
                            "板块 %s(%s) 从 DFCF 获取成分股失败: %s",
                            sector_name, category, e)
            return []

        # dfcf / akshare 等：get_sector_stocks(sector_code, block_type)
        # block_type: 0=行业, 1=概念
        block_type = 1 if category == 'concept' else 0
        # dfcf 的 get_sector_stocks 接受板块名称
        symbol = sector_name if src_name == 'dfcf' else (sector_code or sector_name)
        return provider.get_sector_stocks(symbol, block_type=block_type)

    # ------------------------------------------------------------------
    # 同步自选股
    # ------------------------------------------------------------------

    async def sync_favorites(self) -> Dict:
        """同步自选股到 user_blocks 和 user_block_members 表。

        仅从 local_file provider.get_user_sector() 获取自选股。
        写入 user_blocks（block_code='favorites', block_type='favorite'）
        和 user_block_members 表。
        """
        result: Dict[str, Any] = {'providers': {}}
        provider = self._providers.get('local_file')

        if not self._is_provider_ready('local_file', provider):
            result['providers']['local_file'] = {
                'success': False, 'error': 'local_file provider 不可用'}
            return result

        try:
            user_sector = provider.get_user_sector()
            favorites = user_sector.get('favorites', [])

            # 转换为 storage 格式
            member_data: List[Dict] = []
            stocks_to_upsert: List[Dict] = []
            for item in favorites:
                converted = self._convert_stock(item)
                if converted:
                    member_data.append({'stock_code': converted['stock_code']})
                    stocks_to_upsert.append(converted)

            # 先 upsert 股票记录，避免 FK 约束失败
            if stocks_to_upsert:
                self._storage.upsert_stocks(stocks_to_upsert)

            # 写入 user_blocks 表
            self._storage.upsert_user_block(
                block_code='favorites',
                block_name='自选股',
                block_type='favorite',
                source='local_file',
            )

            # 写入 user_block_members 表
            count = self._storage.update_user_block_members(
                'favorites', member_data, clear_existing=True)

            self._storage.record_sync_log(
                'local_file', 'favorites', 'success', count)
            result['providers']['local_file'] = {
                'success': True, 'count': count}
            logger.info("同步自选股 %d 只", count)
        except Exception as e:
            logger.error("同步自选股失败: %s", e)
            self._storage.record_sync_log(
                'local_file', 'favorites', 'failed', 0, str(e))
            result['providers']['local_file'] = {
                'success': False, 'error': str(e)}

        return result

    # ------------------------------------------------------------------
    # 同步自定义板块
    # ------------------------------------------------------------------

    async def sync_custom_blocks(self) -> Dict:
        """同步自定义板块到 user_blocks 和 user_block_members 表。

        仅从 local_file provider 获取自定义板块：
        - get_sector_list(list_type=1) 获取板块列表
        - get_block_members() 获取每个板块的成分股

        写入 user_blocks（block_type='custom'）和 user_block_members 表。
        """
        result: Dict[str, Any] = {'providers': {}}
        provider = self._providers.get('local_file')

        if not self._is_provider_ready('local_file', provider):
            result['providers']['local_file'] = {
                'success': False, 'error': 'local_file provider 不可用'}
            return result

        try:
            # 获取自定义板块列表
            custom_blocks = provider.get_sector_list(list_type=1)
            total_members = 0
            block_count = 0

            for blk in custom_blocks:
                block_code = blk.get('block_code') or blk.get('code', '')
                block_name = blk.get('block_name') or blk.get('name', '')
                if not block_code:
                    continue

                # 获取板块成分股
                try:
                    member_codes = provider.get_block_members(block_code)
                except Exception as e:
                    logger.warning("获取自定义板块 %s 成分股失败: %s",
                                   block_code, e)
                    member_codes = []

                member_data = [
                    {'stock_code': self._tq_code_to_stock_code(c)}
                    for c in (member_codes or [])
                ]

                # 先确保股票记录存在，避免 FK 约束失败
                if member_data:
                    self._ensure_stocks_exist(
                        [m['stock_code'] for m in member_data])

                # 写入 user_blocks 表
                self._storage.upsert_user_block(
                    block_code=block_code,
                    block_name=block_name,
                    block_type='custom',
                    source='local_file',
                )

                # 写入 user_block_members 表
                count = self._storage.update_user_block_members(
                    block_code, member_data, clear_existing=True)
                total_members += count
                block_count += 1

            self._storage.record_sync_log(
                'local_file', 'custom_blocks', 'success', total_members)
            result['providers']['local_file'] = {
                'success': True,
                'block_count': block_count,
                'member_count': total_members,
            }
            logger.info("同步自定义板块 %d 个，成分股 %d 只",
                        block_count, total_members)
        except Exception as e:
            logger.error("同步自定义板块失败: %s", e)
            self._storage.record_sync_log(
                'local_file', 'custom_blocks', 'failed', 0, str(e))
            result['providers']['local_file'] = {
                'success': False, 'error': str(e)}

        return result
