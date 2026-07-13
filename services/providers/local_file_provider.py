"""
本地文件数据源提供者。

解析通达信（TDX）/大智慧（DZH）/同花顺（THS）客户端本地配置文件，
作为备选池的基础数据库。支持自选股（zxg.blk）与自定义板块（blocknew 目录 .blk 文件）
的解析，依次尝试 TDX → DZH → THS，任一可用即返回结果。

路径探测优先级：配置文件（local_file_paths.json 显式指定）> 环境变量 > Windows 注册表。
"""

import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import DataSourceProvider

logger = logging.getLogger(__name__)

# 本地文件路径规则配置表（相对 meta_core 目录解析）
CONFIG_PATH = Path(__file__).resolve().parents[2] / 'config' / 'local_file_paths.json'

# TDX 市场标识位默认映射（配置缺失时兜底）
DEFAULT_TDX_MARKET_DIGIT = {
    '0': {'setcode': 0, 'market': 'SZ'},  # 深圳
    '1': {'setcode': 1, 'market': 'SH'},  # 上海
    '2': {'setcode': 2, 'market': 'BJ'},  # 北京
}

# 代码前缀推断市场默认映射（用于 THS，配置缺失时兜底）
DEFAULT_CODE_PREFIX_MAP = {
    '6': {'setcode': 1, 'market': 'SH'},
    '0': {'setcode': 0, 'market': 'SZ'},
    '3': {'setcode': 0, 'market': 'SZ'},
    '4': {'setcode': 2, 'market': 'BJ'},
    '8': {'setcode': 2, 'market': 'BJ'},
}

# 客户端探测顺序
_CLIENT_ORDER = ('tdx', 'dzh', 'ths')

# TDX 自选股 / 板块成分股行正则：1 位市场标识（0=深,1=沪,2=京） + 6 位代码
_TDX_LINE_RE = re.compile(r'^([012])(\d{6})$')
# DZH 自选股行正则：代码 Tab 市场 [Tab 名称]
_DZH_LINE_RE = re.compile(r'^(\S+)\t(\S+)(?:\t(\S+))?$')
# THS 自选股行正则：6 位数字代码
_THS_LINE_RE = re.compile(r'^(\d{6})$')
# THS 自选股行正则（ZXG.cfg）：可选市场前缀(SH/SZ/BJ) + 6 位数字代码
_THS_FAVORITES_RE = re.compile(r'^(SH|SZ|BJ)?(\d{6})$')

# 通达信系统板块类型映射（tdxbk.cfg 第一字段）
# 依据实际 tdxbk.cfg 文件内容确定：1=概念, 2=风格, 3=指数
# 注：通达信 tdxbk.cfg 未发现行业（industry）类型，仅 1/2/3 三种。
_TDX_SYSTEM_BLOCK_TYPES = {
    '1': {'name': 'concept', 'label': '概念'},
    '2': {'name': 'style', 'label': '风格'},
    '3': {'name': 'index', 'label': '指数'},
}

# 大智慧（DZH）行业板块名映射（cfg/block.ini 的 [BlockInfo]SysBlock 中含"行业"的板块名）
# 依据实际 DZH block.ini 文件内容确定。DZH 不使用数字类型字段，而是空格分隔的板块名。
# 当前 _parse_dzh_block_ini 返回所有 SysBlock 名称列表，未按分类分组；
# 如需按行业分类，可据此集合识别行业类板块。
_DZH_INDUSTRY_BLOCK_NAMES = {
    '所属行业',
    '证监会行业',
    '大智慧行业(经典)',
    '申万行业',
}

# 同花顺（THS）行业板块文件标识
# THS 在安装根目录下有 industry.ini 文件，格式为 INI：
#   [industry]
#   881101=603336,601996,...   (键=行业代码，值=逗号分隔的股票代码)
# 该文件包含完整的行业分类与成分股，但当前 _parse_ths_blocks 仅解析 Block.cfg，
# 未解析 industry.ini。如需支持 THS 行业板块，需新增解析逻辑。
_THS_INDUSTRY_FILE_KEY = 'industry_ini'


class LocalFileProvider(DataSourceProvider):
    """本地文件数据源提供者。

    解析通达信/大智慧/同花顺本地配置文件，提供自选股与自定义板块数据。
    文件不存在时返回空列表，不抛出异常；解析结果按文件路径缓存，
    文件修改时间变化时自动刷新。
    """

    def __init__(self):
        # 实例级缓存：key=文件路径(str)，value=(解析结果, mtime)
        self._cache: Dict[str, Tuple[Any, float]] = {}
        # 路径规则配置表
        self._paths_config: Dict[str, Any] = self._load_paths_config()
        # 市场代码映射规则（从配置读取，缺失用默认值）
        market_rules = self._paths_config.get('market_code_rules', {})
        self._tdx_market_digit: Dict[str, Dict] = market_rules.get(
            'tdx_market_digit', DEFAULT_TDX_MARKET_DIGIT)
        self._code_prefix_map: Dict[str, Dict] = market_rules.get(
            'code_prefix_to_market', DEFAULT_CODE_PREFIX_MAP)
        # 探测各客户端安装根目录
        self._homes: Dict[str, str] = self._detect_homes()

    # ------------------------------------------------------------------
    # 配置加载
    # ------------------------------------------------------------------

    def _load_paths_config(self) -> Dict[str, Any]:
        """加载 local_file_paths.json 路径规则配置表。"""
        try:
            with open(CONFIG_PATH, encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.warning("加载本地路径配置 %s 失败: %s", CONFIG_PATH, e)
            return {}

    # ------------------------------------------------------------------
    # 路径探测
    # ------------------------------------------------------------------

    def _detect_homes(self) -> Dict[str, str]:
        """探测各客户端安装根目录。

        优先级：配置文件显式指定 > 环境变量 > Windows 注册表。
        """
        homes: Dict[str, str] = {}
        clients_cfg = self._paths_config.get('clients', {})
        for client in _CLIENT_ORDER:
            cfg = clients_cfg.get(client, {})
            home = None

            # 1. 配置文件显式指定（config_key 指向 default_homes 下的字段，如 tdx_home）
            config_key = cfg.get('config_key')
            if config_key:
                home = self._paths_config.get('default_homes', {}).get(config_key)

            # 2. 环境变量
            if not home:
                env_var = cfg.get('env_var')
                if env_var:
                    home = os.environ.get(env_var)

            # 3. Windows 注册表
            if not home:
                home = self._detect_from_registry(cfg.get('registry_keys', []))

            # 4. 默认安装路径（配置/环境变量/注册表均未命中时依次尝试）
            if not home:
                for default_path in cfg.get('default_paths', []):
                    if default_path and Path(default_path).exists():
                        home = default_path
                        break

            if home:
                home_path = Path(home)
                if home_path.exists():
                    homes[client] = str(home_path)
                    logger.debug("探测到 %s 安装目录: %s", client, home)
                else:
                    logger.debug("探测到 %s 路径不存在: %s", client, home)
        return homes

    def _detect_from_registry(self, registry_keys: List[Dict]) -> Optional[str]:
        """从 Windows 注册表探测安装路径（非 Windows 平台跳过）。"""
        if not sys.platform.startswith('win'):
            return None
        if not registry_keys:
            return None
        try:
            import winreg
        except ImportError:
            return None

        hive_map = {
            'HKLM': winreg.HKEY_LOCAL_MACHINE,
            'HKCU': winreg.HKEY_CURRENT_USER,
            'HKEY_LOCAL_MACHINE': winreg.HKEY_LOCAL_MACHINE,
            'HKEY_CURRENT_USER': winreg.HKEY_CURRENT_USER,
        }

        for key_spec in registry_keys:
            hive = key_spec.get('hive', 'HKLM')
            subpath = key_spec.get('path', '')
            value_name = key_spec.get('value', 'InstallPath')
            hkey = hive_map.get(hive)
            if hkey is None:
                continue
            try:
                with winreg.OpenKey(hkey, subpath) as k:
                    val, _ = winreg.QueryValueEx(k, value_name)
                    if val:
                        return str(val)
            except OSError:
                # 键不存在，尝试下一个
                continue
            except Exception as e:
                logger.debug("注册表查询失败 %s\\%s: %s", hive, subpath, e)
                continue
        return None

    # ------------------------------------------------------------------
    # 基础状态
    # ------------------------------------------------------------------

    def is_ready(self) -> bool:
        """检测本地配置文件是否存在（通达信/大智慧/同花顺任一可用即就绪）。"""
        return bool(self._homes)

    def _probe(self) -> Dict[str, Any]:
        """契约探测方法，供 data_source_contract.json 调用。

        Returns:
            {"ready": bool, "provider": "local_file", "error"?: str}
        """
        try:
            ready = self.is_ready()
        except Exception as e:
            return {"ready": False, "provider": "local_file", "error": str(e)}
        if not ready:
            return {
                "ready": False,
                "provider": "local_file",
                "error": "未找到通达信/大智慧/同花顺本地配置文件",
            }
        return {"ready": True, "provider": "local_file"}

    def get_mode_info(self) -> str:
        """返回当前提供者的模式描述字符串。"""
        return "local_file"

    # ------------------------------------------------------------------
    # 文件读取与缓存
    # ------------------------------------------------------------------

    def get_file_mtime(self, path) -> Optional[float]:
        """获取文件修改时间，用于缓存失效判断。文件不存在返回 None。"""
        try:
            return os.path.getmtime(str(path))
        except OSError:
            return None

    def _read_text(self, path: str, encoding: str) -> Optional[str]:
        """读取文件文本，解码失败时依次降级尝试 gbk / utf-8。"""
        try:
            with open(path, 'rb') as f:
                raw = f.read()
        except OSError as e:
            logger.warning("读取文件失败 %s: %s", path, e)
            return None

        # 依次尝试：指定编码 → gb18030 → gbk → utf-8
        tried = []
        for enc in (encoding, 'gb18030', 'gbk', 'utf-8'):
            if not enc or enc in tried:
                continue
            tried.append(enc)
            try:
                return raw.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        # 全部严格解码失败，用 gb18030 + replace 兜底（保留中文，仅替换少数无效字节）
        logger.warning("文件 %s 严格解码失败，使用 gb18030 replace 模式", path)
        return raw.decode('gb18030', errors='replace')

    def _read_file_cached(self, path: str, encoding: str,
                          parser: Callable[[str], Any]) -> Any:
        """读取文件并缓存解析结果，文件修改时间变化时刷新缓存。

        Args:
            path: 文件绝对路径
            encoding: 首选编码
            parser: 解析函数，接收文本返回解析结果

        Returns:
            解析结果；文件不存在时返回 None。
        """
        mtime = self.get_file_mtime(path)
        if mtime is None:
            logger.warning("文件未找到: %s", path)
            return None

        cached = self._cache.get(path)
        if cached is not None and cached[1] == mtime:
            logger.debug("命中缓存: %s", path)
            return cached[0]

        text = self._read_text(path, encoding)
        if text is None:
            return None
        result = parser(text)
        self._cache[path] = (result, mtime)
        logger.debug("已解析文件: %s", path)
        return result

    def _read_binary_cached(self, path: str,
                            parser: Callable[[bytes], Any]) -> Any:
        """读取二进制文件并缓存解析结果，文件修改时间变化时刷新缓存。

        Args:
            path: 文件绝对路径
            parser: 解析函数，接收 bytes 返回解析结果

        Returns:
            解析结果；文件不存在时返回 None。
        """
        mtime = self.get_file_mtime(path)
        if mtime is None:
            logger.warning("文件未找到: %s", path)
            return None

        cached = self._cache.get(path)
        if cached is not None and cached[1] == mtime:
            logger.debug("命中缓存: %s", path)
            return cached[0]

        try:
            with open(path, 'rb') as f:
                raw = f.read()
        except OSError as e:
            logger.warning("读取二进制文件失败 %s: %s", path, e)
            return None

        try:
            result = parser(raw)
        except Exception as e:
            logger.warning("解析二进制文件失败 %s: %s", path, e)
            return None

        self._cache[path] = (result, mtime)
        logger.debug("已解析二进制文件: %s", path)
        return result

    # ------------------------------------------------------------------
    # 路径解析辅助
    # ------------------------------------------------------------------

    def _get_file_path(self, client: str, file_key: str) -> Optional[str]:
        """根据配置获取客户端某文件的完整路径。"""
        home = self._homes.get(client)
        if not home:
            return None
        client_cfg = self._paths_config.get('clients', {}).get(client, {})
        file_cfg = client_cfg.get('files', {}).get(file_key, {})
        rel_path = file_cfg.get('path')
        if not rel_path:
            return None
        return str(Path(home) / rel_path)

    def _get_encoding(self, client: str, file_key: str) -> str:
        """从配置获取客户端某文件的首选编码。"""
        client_cfg = self._paths_config.get('clients', {}).get(client, {})
        file_cfg = client_cfg.get('files', {}).get(file_key, {})
        return file_cfg.get('encoding', 'gb2312')

    def _get_block_file_path(self, client: str, block_filename: str) -> Optional[str]:
        """根据配置获取板块成分股文件完整路径（替换占位符）。"""
        home = self._homes.get(client)
        if not home:
            return None
        client_cfg = self._paths_config.get('clients', {}).get(client, {})
        file_cfg = client_cfg.get('files', {}).get('block_members_pattern', {})
        rel_pattern = file_cfg.get('path', '')
        if not rel_pattern:
            return None
        # 替换占位符 {block_filename} / {block_name}
        rel_path = rel_pattern.replace('{block_filename}', block_filename) \
                              .replace('{block_name}', block_filename)
        return str(Path(home) / rel_path)

    # ------------------------------------------------------------------
    # 市场映射辅助
    # ------------------------------------------------------------------

    def _market_to_setcode(self, market_field: str, code: str = '') -> Tuple[int, str]:
        """解析市场字段，返回 (setcode, market)。

        支持数字标识（0/1/2）、字母标识（SH/SZ/BJ），无法识别时按代码前缀推断。
        """
        market_field = str(market_field).strip().upper()
        # 数字标识（TDX 风格）
        if market_field in self._tdx_market_digit:
            info = self._tdx_market_digit[market_field]
            return info['setcode'], info['market']
        # 字母标识
        letter_map = {'SH': (1, 'SH'), 'SZ': (0, 'SZ'), 'BJ': (2, 'BJ')}
        if market_field in letter_map:
            return letter_map[market_field]
        # 按代码前缀推断
        if code and code[0] in self._code_prefix_map:
            info = self._code_prefix_map[code[0]]
            return info['setcode'], info['market']
        return 0, 'SZ'

    @staticmethod
    def _to_tq_code(code: str, setcode: int) -> str:
        """将纯数字代码 + setcode 转换为 XXXXXX.SH 格式。"""
        market_map = {0: 'SZ', 1: 'SH', 2: 'BJ'}
        market = market_map.get(setcode, 'SZ')
        return f"{code}.{market}"

    # ------------------------------------------------------------------
    # 文件解析器
    # ------------------------------------------------------------------

    def _parse_tdx_zxg(self, text: str) -> List[Dict]:
        """解析通达信自选股文件。每行：市场标识+代码（如 1600141 = 600141.SH）。"""
        favorites: List[Dict] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            m = _TDX_LINE_RE.match(line)
            if not m:
                logger.debug("TDX zxg 跳过无法匹配的行: %s", line)
                continue
            market_digit, code = m.group(1), m.group(2)
            info = self._tdx_market_digit.get(market_digit)
            if not info:
                continue
            favorites.append({
                'setcode': info['setcode'],
                'code': code,
                'name': '',  # zxg.blk 不含名称
            })
        return favorites

    def _parse_tdx_blocknew(self, text: str) -> List[Dict]:
        """解析通达信自定义板块索引。每行用 | 分隔：板块名|板块文件名|类型|..."""
        blocks: List[Dict] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split('|')
            if len(parts) < 2:
                continue
            block_name = parts[0].strip()
            block_filename = parts[1].strip()
            if not block_filename:
                continue
            blocks.append({
                'block_code': block_filename,
                'block_name': block_name,
                'block_filename': block_filename,
            })
        return blocks

    def _parse_tdx_blk(self, text: str) -> List[Dict]:
        """解析通达信板块成分股文件。每行：市场标识+代码（如 1600141 = 600141.SH）。"""
        members: List[Dict] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            m = _TDX_LINE_RE.match(line)
            if not m:
                continue
            market_digit, code = m.group(1), m.group(2)
            info = self._tdx_market_digit.get(market_digit)
            if not info:
                continue
            members.append({
                'setcode': info['setcode'],
                'code': code,
                'name': '',
            })
        return members

    def _parse_dzh_zxg(self, text: str) -> List[Dict]:
        """解析大智慧自选股文件。Tab 分隔：代码\\t市场[\\t名称]。"""
        favorites: List[Dict] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            m = _DZH_LINE_RE.match(line)
            if not m:
                logger.debug("DZH zxg 跳过无法匹配的行: %s", line)
                continue
            code = m.group(1).strip()
            market_field = m.group(2).strip()
            name = m.group(3).strip() if m.group(3) else ''
            setcode, _ = self._market_to_setcode(market_field, code)
            favorites.append({
                'setcode': setcode,
                'code': code,
                'name': name,
            })
        return favorites

    def _parse_dzh_blk_binary(self, raw_bytes: bytes) -> List[Dict]:
        """解析大智慧二进制 BLK 板块文件。

        BLK 文件格式（二进制，GBK编码）：
        - 前2字节可能是记录数（小端 uint16），但不同版本格式有差异
        - 股票代码以 SZ/SH + 6位数字 的形式存储
        - 采用扫描方式匹配所有代码，兼容不同记录长度的格式

        返回：[{'setcode': int, 'code': str, 'name': str}, ...]
        """
        members: List[Dict] = []
        if len(raw_bytes) < 8:
            return members

        # 扫描整个文件，匹配 SZ/SH + 6位数字 的模式
        i = 0
        size = len(raw_bytes)
        seen = set()
        while i < size - 8:
            # 尝试匹配 SZ 或 SH 开头
            prefix = raw_bytes[i:i+2]
            if prefix in (b'SZ', b'SH', b'BJ'):
                code_bytes = raw_bytes[i+2:i+8]
                try:
                    code = code_bytes.decode('ascii')
                except UnicodeDecodeError:
                    i += 1
                    continue
                if len(code) == 6 and code.isdigit():
                    market = prefix.decode('ascii')
                    key = (market, code)
                    if key not in seen:
                        seen.add(key)
                        setcode, _ = self._market_to_setcode(market, code)
                        members.append({
                            'setcode': setcode,
                            'code': code,
                            'name': '',
                        })
                    i += 8
                    continue
            i += 1

        return members

    def _parse_ths_zxg(self, text: str) -> List[Dict]:
        """解析同花顺自选股文件。INI 风格，含 [ZXG] 段，每行 6 位代码。"""
        favorites: List[Dict] = []
        in_zxg = False
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            # 段头判断
            if line.startswith('[') and line.endswith(']'):
                in_zxg = (line.upper() == '[ZXG]')
                continue
            if not in_zxg:
                continue
            m = _THS_LINE_RE.match(line)
            if not m:
                continue
            code = m.group(1)
            info = self._code_prefix_map.get(code[0])
            if not info:
                continue
            favorites.append({
                'setcode': info['setcode'],
                'code': code,
                'name': '',
            })
        return favorites

    def _parse_ths_favorites(self, text: str) -> List[Dict]:
        """解析同花顺自选股文件（hexin/ZXG.cfg）。

        GBK 编码，每行一个股票代码，格式可能是纯代码 600000 或带市场前缀 SH600000。
        根据代码前缀判断市场：6开头=SH, 0/3开头=SZ, 8/4开头=BJ。
        若存在 INI 段头（如 [ZXG]）则跳过，兼容纯文本与 INI 风格。
        """
        favorites: List[Dict] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            # 跳过 INI 段头（如 [ZXG]）
            if line.startswith('[') and line.endswith(']'):
                continue
            m = _THS_FAVORITES_RE.match(line)
            if not m:
                logger.debug("THS ZXG.cfg 跳过无法匹配的行: %s", line)
                continue
            market_prefix, code = m.group(1), m.group(2)
            if market_prefix:
                setcode, _ = self._market_to_setcode(market_prefix, code)
            else:
                info = self._code_prefix_map.get(code[0])
                if not info:
                    logger.debug("THS ZXG.cfg 无法识别市场，跳过代码: %s", code)
                    continue
                setcode = info['setcode']
            favorites.append({
                'setcode': setcode,
                'code': code,
                'name': '',
            })
        return favorites

    def _parse_ths_blocks(self, text: str) -> List[Dict]:
        """解析同花顺板块配置文件（hexin/Block.cfg）。

        格式可能是 INI 或自定义格式。支持 INI 风格的板块定义：
        - [板块名] 段头定义一个板块
        - 段内每行为成员代码（纯代码 600000 或带前缀 SH600000）
        无法识别的行跳过，无法确定格式时返回空列表。
        """
        blocks: List[Dict] = []
        current_block: Optional[str] = None
        current_members: List[Dict] = []

        def _flush_block():
            """保存当前板块到 blocks 列表。"""
            if current_block is not None:
                blocks.append({
                    'block_code': current_block,
                    'block_name': current_block,
                    'members': current_members[:],
                })

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            # INI 段头判断：[板块名]
            if line.startswith('[') and line.endswith(']'):
                _flush_block()
                current_block = line[1:-1].strip()
                current_members = []
                continue
            # 未进入任何板块段，跳过
            if current_block is None:
                continue
            # 尝试解析成员代码
            m = _THS_FAVORITES_RE.match(line)
            if not m:
                continue
            market_prefix, code = m.group(1), m.group(2)
            if market_prefix:
                setcode, _ = self._market_to_setcode(market_prefix, code)
            else:
                info = self._code_prefix_map.get(code[0])
                if not info:
                    continue
                setcode = info['setcode']
            current_members.append({
                'setcode': setcode,
                'code': code,
                'name': '',
            })
        # 保存最后一个板块
        _flush_block()
        return blocks

    # ------------------------------------------------------------------
    # 同花顺 BlockUpdate INI 解析（板块名称 + 成分股）
    # ------------------------------------------------------------------

    # THS BlockUpdate market 代码到市场标识的映射
    # A股：17=SH, 33=SZ, -105=BJ
    # 港股：-79=港股主板/国企H股, -78=港股, -76=香港ETF
    # 美股：-87=美股(纽交所/纳斯达克), -71=美股, -70=美股, -85=美股, -86=美股
    # 英股：-95=英股
    _THS_MARKET_MAP = {
        '17': 'SH',
        '33': 'SZ',
        '-105': 'BJ',
        # 港股
        '-79': 'HK',
        '-78': 'HK',
        '-76': 'HK',
        # 美股
        '-87': 'US',
        '-71': 'US',
        '-70': 'US',
        '-85': 'US',
        '-86': 'US',
        # 英股
        '-95': 'UK',
    }

    # THS BlockUpdate 文件名到分类的映射
    # 依据每个文件 [BLOCK_NAME_MAP_TABLE] 第一条（根节点名）确定分类：
    #   block_DFF8.ini 首条 DFF8=行业 → A股行业（真正的行业分类）
    #   block_2B.ini   首条 2B=概念 → A股概念
    #   block_CC2B.ini 首条 CC2B=同花顺英股行业 → 英股行业（独立分类，不混入行业）
    #   block_D8FA.ini 首条 D8FA=同花顺美股行业 → 美股行业（归入 us）
    #   block_DACC.ini 首条 DACC=新三板行业 → 新三板行业（归入 neeq）
    # ConfigName 全为 "stockblock_同花顺方案"，无法用于分类识别。
    _THS_BLOCKUPDATE_FILE_MAP = {
        # A股概念板块
        'block_2B.ini': 'concept',      # 概念（A股概念，含地域类子板块）
        'block_C4BC.ini': 'concept',    # 概念索引（一级/二级概念）
        # A股行业板块（仅 block_DFF8 归为 industry，其他市场行业独立分类）
        'block_DFF8.ini': 'industry',   # 行业（A股一/二/三级行业）
        # 港股板块（含港股概念、港股行业、港股特色指数）
        'block_7.ini': 'hk',            # 港股（所有港股/AH股/港股通等）
        'block_CD3D.ini': 'hk',         # 同花顺港股概念
        'block_BA07.ini': 'hk',         # 港股特色指数
        # 美股板块（含美股行业、美股概念、美股ETF）
        'block_D8FA.ini': 'us',         # 同花顺美股行业
        'block_CD3C.ini': 'us',         # 同花顺美股概念
        'block_D2DB.ini': 'us',         # 美股ETF分类
        # 英股板块
        'block_CC2B.ini': 'uk',         # 同花顺英股行业
        # 新三板板块（含新三板行业）
        'block_D8CF.ini': 'neeq',       # 股转(新三板)
        'block_DACC.ini': 'neeq',       # 新三板行业
        # 地区板块
        'block_47.ini': 'region',       # 地域（安徽、北京等）
        # 指数板块（含行业索引、特色指数、统计指数等）
        'block_C4B7.ini': 'index',      # 行业索引（一级/二级行业索引）
        'block_C4BB.ini': 'index',      # 同花顺行业索引
        'block_2.ini': 'index',         # 基金指数（ETF/LOF等）
        'block_C6.ini': 'index',        # 指标股/指数（中证500等）
        'block_D3.ini': 'index',        # 指数类（沪深300等）
        'block_D18F.ini': 'index',      # 上海指数（上证50/180/380等）
        'block_BC0F.ini': 'index',      # 综合指数/风格指数/策略指数
        'block_BF4D.ini': 'index',      # 统计指数
        'block_C0C5.ini': 'index',      # 特色指数（成交前十/资金前十等）
        'block_BA04.ini': 'index',      # 可转债特色指数
        'block_CE32.ini': 'index',      # 大盘风向/统计指数
        'block_C7B2.ini': 'index',      # REITs
        'block_CBBE.ini': 'index',      # 科创板
        'block_CFE3.ini': 'index',      # 深创业板
        'block_DA61.ini': 'index',      # 沪港通/上证380/上证180
        'block_EFFE.ini': 'index',      # 深中小板
        'block_F049.ini': 'index',      # 跳转板块集合（Level2等）
        'block_C2D5.ini': 'index',      # 其他树根节点（A股指数/退市可转债等）
        # 期货板块
        'block_EFA2.ini': 'futures',    # 期货（豆粕/玉米等）
        'block_D9FE.ini': 'futures',    # 期股联动（商品猪肉/煤炭等）
        # 市场分类
        'block_D.ini': 'market',        # 沪深京A股/B股/债券/基金
        # 自定义板块
        'block_22.ini': 'custom',       # 自定义板块（板块1-8）
        # 其他
        'block_DB56.ini': 'other',      # 分时突破预警
    }

    # 空文件列表（80字节，仅含 ConfigInfo 头，无板块数据，跳过）
    _THS_BLOCKUPDATE_EMPTY_FILES = {
        'block_B995.ini', 'block_B996.ini', 'block_B997.ini', 'block_B998.ini',
        'block_B999.ini', 'block_B99A.ini', 'block_B99B.ini', 'block_B99C.ini',
        'block_B99D.ini', 'block_B99E.ini', 'block_B99F.ini', 'block_B9A0.ini',
        'block_B9A9.ini', 'block_B9AB.ini', 'block_B9AC.ini',
        'block_B9C1.ini', 'block_B9C2.ini', 'block_B9C3.ini', 'block_B9C4.ini',
        'block_B9C5.ini', 'block_B9A8.ini',  # hk_industry 索引，无实际成分股
    }

    def _parse_ths_blockupdate_ini(self, text: str) -> Dict:
        """解析同花顺 BlockUpdate INI 文件，返回板块名称和成分股。

        INI 格式：
            [ConfigInfo]
            ConfigName=stockblock_概念板块
            [BLOCK_NAME_MAP_TABLE]
            2B=概念
            DBD0=新能源
            [BLOCK_STOCK_CONTEXT]
            DA4F=17:605567,33:300666,...
            [SUBDIVISION_BLOCK_STOCK_CONTEXT]
            B225=33:001316,...

        返回：{'config_name': str, 'blocks': {block_id: {'name': str, 'members': [str]}},
               'subdivisions': {block_id: {'members': [str]}}}
        """
        result: Dict = {
            'config_name': '',
            'blocks': {},
            'subdivisions': {},
        }
        current_section: Optional[str] = None

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            # section header
            if line.startswith('[') and line.endswith(']'):
                current_section = line[1:-1].strip()
                continue
            if '=' not in line:
                continue
            key, _, value = line.partition('=')
            key = key.strip()
            value = value.strip()

            if current_section == 'ConfigInfo':
                if key == 'ConfigName':
                    result['config_name'] = value
            elif current_section == 'BLOCK_NAME_MAP_TABLE':
                # block_id = block_name
                result['blocks'].setdefault(key, {
                    'name': value, 'members': []})
            elif current_section == 'BLOCK_STOCK_CONTEXT':
                # block_id = market:code,market:code,...
                members = self._parse_ths_stock_context(value)
                if key in result['blocks']:
                    result['blocks'][key]['members'] = members
                else:
                    result['blocks'][key] = {'name': key, 'members': members}
            elif current_section == 'SUBDIVISION_BLOCK_STOCK_CONTEXT':
                # 细分板块成分股
                members = self._parse_ths_stock_context(value)
                result['subdivisions'][key] = {'members': members}

        return result

    def _parse_ths_stock_context(self, value: str) -> List[str]:
        """解析 THS BLOCK_STOCK_CONTEXT 值，返回成分股代码列表。

        格式：market:code,market:code,...
        如：17:605567,33:300666,-105:920351,-79:HK9995,-87:FCOM
        market 17=SH, 33=SZ, -105=BJ, -79=HK, -87=US, -95=UK

        返回代码格式：
            A股：SH600000 / SZ000001 / BJ920706（市场前缀+6位代码）
            港股：HK9995（保留原始 HK 前缀代码）
            美股：US.FCOM（加 US. 前缀区分）
            英股：UK.SRT
        """
        members: List[str] = []
        for item in value.split(','):
            item = item.strip()
            if not item or ':' not in item:
                continue
            market_code, _, code = item.partition(':')
            market_code = market_code.strip()
            code = code.strip()
            if not code:
                continue
            market = self._THS_MARKET_MAP.get(market_code)
            if market:
                if market in ('SH', 'SZ', 'BJ'):
                    # A股：市场前缀 + 6位代码
                    members.append(f"{market}{code}")
                elif market == 'HK':
                    # 港股：代码已含 HK 前缀（如 HK9995），直接使用
                    members.append(code)
                else:
                    # 美股/英股：加市场前缀区分（如 US.FCOM, UK.SRT）
                    members.append(f"{market}.{code}")
            else:
                # 未知市场代码，仅对纯数字代码按前缀推断（A股兜底）
                if code.isdigit():
                    info = self._code_prefix_map.get(code[0]) if code else None
                    if info:
                        members.append(f"{info['market']}{code}")
                    else:
                        logger.debug("THS 未知 market 代码: %s (code=%s)", market_code, code)
        return members

    # THS block_tree.ini 缓存的层级映射（block_id → {level, parent_id}）
    _ths_block_tree_cache: Dict[str, Dict] = None

    def _parse_ths_block_tree(self, text: str) -> Dict[str, Dict]:
        """解析同花顺 block_tree.ini，构建 block_id → {level, parent_id} 层级映射。

        block_tree.ini 结构：
            [BLOCK_TREE_ROOT]
            1=@10001              (虚拟根 → @10001)

            [@10001]              (顶层板块列表)
            DFF8=@67336           (DFF8=行业，子节点列表 @67336)
            2B=@10043             (2B=概念，子节点列表 @10043)

            [@67336]              (DFF8 的子节点 = 一级行业)
            DFCE=@67294           (DFCE=种植业与林业，有子节点 @67294)
            DFCC=@67292           (DFCC=养殖业，有子节点 @67292)

            [@67294]              (DFCE 的子节点 = 二级行业)
            D118=536871427        (D118=种子生产，叶子节点，无子节点)
            D117=536871427        (D117=粮食种植，叶子节点)

        level 约定（相对于顶层分类板块，如 DFF8=行业）：
            - DFF8（根分类）本身不计入结果
            - DFF8 的直接子节点（DFCE 等）level=1（一级行业）
            - DFCE 的子节点（D118 等）level=2（二级行业）
            - 更深层级 level=3（三级行业）

        Returns:
            {block_id: {'level': int, 'parent_id': str}, ...}
        """
        # 第一遍：解析所有 section 为 {section_name: {key: value}}
        sections: Dict[str, Dict[str, str]] = {}
        current_section: Optional[str] = None
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith('[') and line.endswith(']'):
                current_section = line[1:-1].strip()
                sections.setdefault(current_section, {})
                continue
            if current_section and '=' in line:
                key, _, value = line.partition('=')
                sections[current_section][key.strip()] = value.strip()

        # 第二遍：建立 @xxx → parent_block_id 反向映射
        # 在某个 [@yyy] section 中，若 key=block_id value=@xxx，
        # 则 @xxx 的父节点是 block_id
        ref_to_parent: Dict[str, str] = {}
        for section_name, items in sections.items():
            if section_name in ('ConfigInfo', 'SYSTEM', 'BLOCK_TREE_ROOT'):
                continue
            for key, value in items.items():
                if value.startswith('@'):
                    ref_to_parent[value] = key

        # 第三遍：构建 block_id → children 列表 的正向映射
        block_children: Dict[str, List[str]] = {}
        for section_name, items in sections.items():
            if section_name in ('ConfigInfo', 'SYSTEM', 'BLOCK_TREE_ROOT'):
                continue
            parent_block_id = ref_to_parent.get(section_name)
            if parent_block_id:
                block_children.setdefault(parent_block_id, [])
                block_children[parent_block_id].extend(items.keys())

        # 第四遍：从顶层板块开始 DFS 分配 level
        # 顶层板块（DFF8, 2B, CC2B 等）在 [BLOCK_TREE_ROOT]→[@10001] 中
        # 顶层板块本身 level=0（不加入结果），其子节点 level=1
        result: Dict[str, Dict] = {}

        def _dfs(block_id: str, level: int, parent_id: str):
            # 避免循环引用
            if block_id in result:
                return
            result[block_id] = {'level': level, 'parent_id': parent_id}
            for child_id in block_children.get(block_id, []):
                _dfs(child_id, level + 1, block_id)

        root_items = sections.get('BLOCK_TREE_ROOT', {})
        for root_value in root_items.values():
            if not root_value.startswith('@'):
                continue
            section_data = sections.get(root_value, {})
            for top_block_id in section_data.keys():
                # top_block_id 是顶层板块（如 DFF8），level=0，不加入结果
                # 从其子节点开始 level=1
                for child_id in block_children.get(top_block_id, []):
                    _dfs(child_id, 1, top_block_id)

        return result

    def _get_ths_block_tree_map(self) -> Dict[str, Dict]:
        """获取（带缓存的）THS block_tree.ini 层级映射。

        从 BlockUpdate 目录读取 block_tree.ini 并解析为
        {block_id: {'level': int, 'parent_id': str}}。
        文件不存在或解析失败时返回空字典。
        """
        if self._ths_block_tree_cache is not None:
            return self._ths_block_tree_cache

        blockupdate_dir = self._get_file_path('ths', 'blockupdate_dir')
        if not blockupdate_dir:
            self._ths_block_tree_cache = {}
            return self._ths_block_tree_cache

        tree_file = Path(blockupdate_dir) / 'block_tree.ini'
        if not tree_file.is_file():
            self._ths_block_tree_cache = {}
            return self._ths_block_tree_cache

        text = self._read_text(str(tree_file), self._get_encoding('ths', 'blockupdate_dir'))
        if not text:
            self._ths_block_tree_cache = {}
            return self._ths_block_tree_cache

        try:
            self._ths_block_tree_cache = self._parse_ths_block_tree(text)
        except Exception as e:
            logger.warning("解析 THS block_tree.ini 失败: %s", e)
            self._ths_block_tree_cache = {}
        return self._ths_block_tree_cache

    def _scan_ths_blockupdate_dir(self) -> List[Dict]:
        """扫描同花顺 BlockUpdate 目录，返回文件列表与分类。

        返回：[{'filename': 'block_2B.ini', 'filepath': '/path/to/file', 'category': 'concept'}, ...]
        """
        blockupdate_dir = self._get_file_path('ths', 'blockupdate_dir')
        if not blockupdate_dir:
            return []
        dir_path = Path(blockupdate_dir)
        if not dir_path.is_dir():
            logger.debug("THS BlockUpdate 目录不存在: %s", blockupdate_dir)
            return []

        files: List[Dict] = []
        for entry in dir_path.iterdir():
            if not entry.is_file():
                continue
            fname = entry.name
            if not fname.lower().startswith('block_') or not fname.lower().endswith('.ini'):
                continue
            # 跳过 block_tree.ini（索引文件，非板块数据）
            if fname.lower() == 'block_tree.ini':
                continue
            # 跳过空文件（80字节，仅含 ConfigInfo 头，无板块数据）
            if fname in self._THS_BLOCKUPDATE_EMPTY_FILES:
                continue
            # 跳过未在映射表中的未知文件（避免 other 分类混乱）
            category = self._THS_BLOCKUPDATE_FILE_MAP.get(fname)
            if not category:
                logger.debug("THS 跳过未映射的文件: %s", fname)
                continue
            files.append({
                'filename': fname,
                'filepath': str(entry),
                'category': category,
            })
        return files

    def _get_ths_blockupdate_sectors(self, category: str = None) -> List[Dict]:
        """获取同花顺 BlockUpdate 板块列表（含名称、成分股与层级信息）。

        Args:
            category: 板块分类（concept/industry/index/region/hk/us/uk/futures/
                      neeq/market/custom），None 表示获取所有分类

        返回：[{'block_id': '2B', 'block_name': '概念', 'members': ['SH605567', ...],
                'category': 'concept', 'source_file': 'block_2B.ini',
                'level': 1, 'parent_id': '', 'parent_name': ''}, ...]

        分类通过 _THS_BLOCKUPDATE_FILE_MAP 文件名映射确定（ConfigName 全为
        "stockblock_同花顺方案"，无法用于分类识别）。

        层级信息来自 block_tree.ini：
            - level=1 一级分类（如种植业与林业、养殖业，90 个一级行业）
            - level=2 二级分类（如种子生产、粮食种植，257 个二级行业叶子节点）
            - level=0 未在树中找到（兜底）

        保留策略（参考同花顺官方一级/二级/三级行业分类展示）：
            - level>=1 的分类节点（一级行业等）即使无成分股也保留，is_category=True，
              前端可据此构建"一级→二级"树形展示；
            - 根节点（DFF8/2B 等，level=0 且无成分股）与无层级且无成分股的节点跳过。
        """
        files = self._scan_ths_blockupdate_dir()
        if not files:
            return []

        # 获取层级映射（block_id → {level, parent_id}）
        level_map = self._get_ths_block_tree_map()

        # 第一遍：读取所有文件，构建 block_id → name 全局映射（用于查找 parent_name）
        file_results: List[Tuple[Dict, Dict]] = []
        block_name_map: Dict[str, str] = {}
        for file_info in files:
            fname = file_info['filename']
            filepath = file_info['filepath']
            file_category = file_info['category']

            # 分类过滤
            if category and file_category != category:
                continue

            result = self._read_file_cached(
                filepath, self._get_encoding('ths', 'blockupdate_dir'),
                self._parse_ths_blockupdate_ini)
            if not result:
                continue

            file_results.append((file_info, result))

            # 收集 block_id → name（用于后续查找 parent_name）
            for bid, binfo in result.get('blocks', {}).items():
                bname = binfo.get('name', bid)
                if bname:
                    block_name_map[bid] = bname

        # 第二遍：为每个板块添加层级信息
        sectors: List[Dict] = []
        for file_info, result in file_results:
            fname = file_info['filename']
            detected_category = file_info['category']

            # 提取板块（保留 level>=1 的分类节点，跳过根节点与无层级且无成分股的节点）
            blocks = result.get('blocks', {})
            for block_id, block_info in blocks.items():
                members = block_info.get('members', [])
                level_info = level_map.get(block_id, {})
                level = level_info.get('level', 0)
                parent_id = level_info.get('parent_id', '')
                parent_name = block_name_map.get(parent_id, '')

                # 跳过根节点（DFF8/2B 等，level=0 且无成分股）与无层级且无成分股的节点
                if not members and level < 1:
                    continue

                # level>=1 且无成分股的节点 = 一级行业分类节点（is_category）
                is_category = (not members) and (level >= 1)

                sectors.append({
                    'code': block_id,
                    'name': block_info.get('name', block_id),
                    'block_id': block_id,
                    'block_name': block_info.get('name', block_id),
                    'members': members,
                    'member_count': len(members),
                    'category': detected_category,
                    'source_file': fname,
                    'level': level,
                    'parent_id': parent_id,
                    'parent_name': parent_name,
                    'is_category': is_category,
                })

            # 提取细分板块
            subdivisions = result.get('subdivisions', {})
            for sub_id, sub_info in subdivisions.items():
                members = sub_info.get('members', [])

                level_info = level_map.get(sub_id, {})
                parent_id = level_info.get('parent_id', '')
                parent_name = block_name_map.get(parent_id, '')

                sectors.append({
                    'code': sub_id,
                    'name': sub_id,  # 细分板块无独立名称
                    'block_id': sub_id,
                    'block_name': sub_id,
                    'members': members,
                    'member_count': len(members),
                    'category': detected_category,
                    'source_file': fname,
                    'is_subdivision': True,
                    'level': level_info.get('level', 0),
                    'parent_id': parent_id,
                    'parent_name': parent_name,
                })

        return sectors

    def _parse_tdx_blocknew_cfg(self, data: bytes) -> Dict[str, str]:
        """解析通达信 blocknew.cfg，返回 {板块代码: 板块名称} 映射。

        blocknew.cfg 为固定 120 字节/条的二进制记录，
        每条记录前半部分为板块名称（GBK编码），约50字节处为板块代码（ASCII）。
        """
        result: Dict[str, str] = {}
        record_size = 120
        num_records = len(data) // record_size
        for i in range(num_records):
            offset = i * record_size
            record = data[offset:offset + record_size]
            name_end = 0
            while name_end < min(50, len(record)) and record[name_end] != 0:
                name_end += 1
            if name_end == 0:
                continue
            try:
                name = record[:name_end].decode('gbk')
            except (UnicodeDecodeError, LookupError):
                try:
                    name = record[:name_end].decode('gb2312', errors='ignore')
                except Exception:
                    continue
            code_start = 50
            code_end = code_start
            while code_end < min(100, len(record)) and record[code_end] != 0:
                code_end += 1
            if code_end <= code_start:
                continue
            try:
                code = record[code_start:code_end].decode('ascii', errors='ignore').strip()
            except Exception:
                continue
            if code and name:
                result[code] = name
        return result

    def _scan_tdx_blocknew_dir(self) -> List[Dict]:
        """扫描通达信 blocknew 目录下的 .blk 文件，返回自定义板块列表。

        优先从 blocknew.cfg 读取板块名称（120字节/条二进制记录），
        若 cfg 文件不可用则用文件名作为板块名。
        排除系统文件（zxg.blk / tjg.blk 等）。
        """
        blocknew_dir = self._get_file_path('tdx', 'custom_blocks_index')
        if not blocknew_dir:
            return []
        dir_path = Path(blocknew_dir)
        if not dir_path.is_dir():
            logger.debug("TDX blocknew 目录不存在: %s", blocknew_dir)
            return []

        name_map: Dict[str, str] = {}
        cfg_path = dir_path / 'blocknew.cfg'
        if cfg_path.is_file():
            try:
                name_map = self._read_binary_cached(
                    str(cfg_path), self._parse_tdx_blocknew_cfg)
            except Exception as e:
                logger.debug("解析 TDX blocknew.cfg 失败: %s", e)

        client_cfg = self._paths_config.get('clients', {}).get('tdx', {})
        file_cfg = client_cfg.get('files', {}).get('custom_blocks_index', {})
        exclude_files = set(file_cfg.get(
            'exclude_files', ['zxg.blk', 'tjg.blk', 'error.cfg', 'time.cfg', 'blocknew.cfg']))
        exclude_lower = {f.lower() for f in exclude_files}

        blocks: List[Dict] = []
        try:
            for entry in sorted(dir_path.iterdir()):
                if not entry.is_file():
                    continue
                if entry.suffix.lower() != '.blk':
                    continue
                if entry.name.lower() in exclude_lower:
                    continue
                block_code = entry.stem
                block_name = name_map.get(block_code, block_code)
                blocks.append({
                    'block_code': block_code,
                    'block_name': block_name,
                    'block_filename': block_code,
                })
        except OSError as e:
            logger.warning("扫描 TDX blocknew 目录失败 %s: %s", blocknew_dir, e)
            return []
        return blocks

    def _scan_dzh_userdata_block_dir(self) -> List[Dict]:
        """扫描大智慧 USERDATA/block 目录下的 .BLK 文件，返回自定义板块列表。

        大智慧 USERDATA/block 目录下每个 .BLK 文件是一个板块，
        文件名（不含 .BLK）即为板块名，排除文件名包含"自选股"的文件。
        BLK 文件为二进制格式，用 _parse_dzh_blk_binary 解析。
        """
        block_dir = self._get_file_path('dzh', 'userdata_block_dir')
        if not block_dir:
            return []
        dir_path = Path(block_dir)
        if not dir_path.is_dir():
            logger.debug("DZH USERDATA/block 目录不存在: %s", block_dir)
            return []

        # 从配置读取排除关键词
        client_cfg = self._paths_config.get('clients', {}).get('dzh', {})
        file_cfg = client_cfg.get('files', {}).get('userdata_block_dir', {})
        exclude_keywords = [kw.lower() for kw in file_cfg.get('exclude_keywords', ['自选股'])]

        blocks: List[Dict] = []
        try:
            for entry in sorted(dir_path.iterdir()):
                if not entry.is_file():
                    continue
                if entry.suffix.lower() != '.blk':
                    continue
                block_name = entry.stem  # 文件名（不含 .BLK）
                # 排除包含指定关键词的文件（如自选股）
                name_lower = block_name.lower()
                if any(kw in name_lower for kw in exclude_keywords):
                    continue
                blocks.append({
                    'block_code': block_name,
                    'block_name': block_name,
                    'block_filename': entry.name,
                })
        except OSError as e:
            logger.warning("扫描 DZH USERDATA/block 目录失败 %s: %s", block_dir, e)
            return []
        return blocks

    def _parse_tdx_system_blocks(self, text: str) -> List[Dict]:
        """解析通达信系统板块文件（tdxbk.cfg）。

        管道分隔格式：类型|板块名|板块描述|标志（GB2312编码）。
        示例：1|概念板块|概念板块描述|0

        返回的每个板块字典含 type（类型编号）、type_name（映射后的分类名）、
        type_label（分类中文标签）、block_name、block_desc、flag 字段。
        """
        blocks: List[Dict] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split('|')
            if len(parts) < 4:
                continue
            block_type = parts[0].strip()
            block_name = parts[1].strip()
            block_desc = parts[2].strip()
            block_flag = parts[3].strip()
            if not block_name:
                continue
            type_info = _TDX_SYSTEM_BLOCK_TYPES.get(block_type, {})
            blocks.append({
                'type': block_type,
                'type_name': type_info.get('name', 'other'),
                'type_label': type_info.get('label', '其他'),
                'block_name': block_name,
                'block_desc': block_desc,
                'flag': block_flag,
            })
        return blocks

    def _get_tdx_system_blocks(self) -> List[Dict]:
        """读取并解析通达信系统板块文件（tdxbk.cfg）。"""
        path = self._get_file_path('tdx', 'system_blocks')
        if not path:
            return []
        result = self._read_file_cached(
            path, self._get_encoding('tdx', 'system_blocks'),
            self._parse_tdx_system_blocks)
        return result or []

    # ------------------------------------------------------------------
    # 通达信 tdxhy.cfg + tdxzs.cfg 解析（行业板块成分股）
    # ------------------------------------------------------------------

    # tdxhy.cfg 行正则：market|code|tdx_industry|||sw_industry
    _TDX_HY_RE = re.compile(r'^([012])\|(\d{6})\|([^|]+)\|\|\|([^|]+)$')
    # tdxzs.cfg 行正则：name|code|type|category|?|desc
    _TDX_ZS_RE = re.compile(r'^([^|]+)\|(\d+)\|(\d+)\|(\d+)\|(\d+)\|([^|]*)$')
    # tdxzs.cfg type 字段到子分类的映射（用于 sector_index 分类下的 sub_type 标识）
    # type=2 行业(145)/type=3 地区(32)/type=4 概念(270)/type=5 风格(158)
    _TDX_ZS_TYPE_MAP = {
        '2': 'industry',
        '3': 'region',
        '4': 'concept',
        '5': 'style',
    }

    def _parse_tdx_industry_mapping(self, text: str) -> Dict[str, List[str]]:
        """解析通达信 tdxhy.cfg，返回按行业代码分组的成分股字典。

        格式：market|code|tdx_industry|||sw_industry
        返回：{'T1001': ['SZ000001', 'SZ000002', ...], ...}
        """
        mapping: Dict[str, List[str]] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            m = self._TDX_HY_RE.match(line)
            if not m:
                continue
            market_digit, code, tdx_industry, _sw_industry = (
                m.group(1), m.group(2), m.group(3), m.group(4))
            info = self._tdx_market_digit.get(market_digit)
            if not info:
                continue
            stock_code = f"{info['market']}{code}"
            mapping.setdefault(tdx_industry, []).append(stock_code)
        return mapping

    def _get_tdx_industry_members(self) -> Dict[str, List[str]]:
        """读取 tdxhy.cfg 并返回按行业代码分组的成分股字典。

        返回：{'T1001': ['SZ000001', ...], 'T110201': [...], ...}
        文件不存在时返回空字典。
        """
        path = self._get_file_path('tdx', 'tdxhy_cfg')
        if not path:
            return {}
        result = self._read_file_cached(
            path, self._get_encoding('tdx', 'tdxhy_cfg'),
            self._parse_tdx_industry_mapping)
        return result or {}

    def _parse_tdx_sector_indices(self, text: str) -> List[Dict]:
        """解析通达信 tdxzs.cfg，返回板块指数列表。

        格式：name|code|type|category|?|desc
        返回：[{'code': '880201', 'name': '农业种植', 'type': '3', 'category': '1', 'desc': '1'}, ...]
        """
        indices: List[Dict] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            m = self._TDX_ZS_RE.match(line)
            if not m:
                continue
            indices.append({
                'code': m.group(2),
                'name': m.group(1),
                'type': m.group(3),
                'category': m.group(4),
                'desc': m.group(6),
            })
        return indices

    def _get_tdx_sector_indices(self) -> List[Dict]:
        """读取 tdxzs.cfg 并返回板块指数列表。

        返回：[{'code': '880201', 'name': '农业种植', 'type': '3', ...}, ...]
        文件不存在时返回空列表。
        """
        path = self._get_file_path('tdx', 'tdxzs_cfg')
        if not path:
            return []
        result = self._read_file_cached(
            path, self._get_encoding('tdx', 'tdxzs_cfg'),
            self._parse_tdx_sector_indices)
        return result or []

    # ------------------------------------------------------------------
    # 通达信 infoharbor_block.dat 实时板块成分股解析
    # 格式：头行 #XX_板块名,数量,指数代码,创建日期,更新日期,,
    #       数据行 市场位#6位代码（0=深,1=沪,2=京），逗号分隔
    # 前缀：#GN_=概念, #FG_=风格, #ZS_=指数
    # ------------------------------------------------------------------

    # infoharbor 头行正则：#XX_名称,数量,指数代码,创建日期,更新日期,,
    _TDX_INFOHARBOR_HEAD_RE = re.compile(
        r'^#(GN|FG|ZS)_([^,]+),(\d+),([^,]*),([^,]*),([^,]*),,')

    # 前缀到分类的映射
    _TDX_INFOHARBOR_PREFIX_MAP = {
        'GN': 'concept',
        'FG': 'style',
        'ZS': 'index',
    }

    def _parse_tdx_infoharbor_block(self, text: str) -> Dict[str, List[Dict]]:
        """解析通达信 infoharbor_block.dat 实时板块成分股文件。

        返回按分类分组的板块列表：
            {'concept': [{'code': '880534', 'name': '锂电池', 'members': ['SZ000040', ...]}, ...],
             'style': [...], 'index': [...]}
        市场位映射：0=SZ, 1=SH, 2=BJ（独立解析，不跨数据源匹配）
        """
        grouped: Dict[str, List[Dict]] = {}
        current_block: Optional[Dict] = None
        current_cat = ''
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            m = self._TDX_INFOHARBOR_HEAD_RE.match(line)
            if m:
                prefix = m.group(1)
                block_name = m.group(2)
                count = int(m.group(3) or 0)
                idx_code = m.group(4)
                # 更新日期 = m.group(6)
                current_cat = self._TDX_INFOHARBOR_PREFIX_MAP.get(prefix, 'other')
                current_block = {
                    'code': idx_code,
                    'name': block_name,
                    'members': [],
                    'count_hint': count,
                }
                grouped.setdefault(current_cat, []).append(current_block)
                continue
            # 数据行：0#000040,0#000049,...,1#600072,...,2#920019,
            if current_block is None:
                continue
            for tok in line.split(','):
                tok = tok.strip()
                if '#' not in tok:
                    continue
                market_digit, _, code = tok.partition('#')
                if len(code) != 6:
                    continue
                info = self._tdx_market_digit.get(market_digit)
                if not info:
                    continue
                full_code = f"{info['market']}{code}"
                current_block['members'].append(full_code)
        return grouped

    def _get_tdx_infoharbor_blocks(self) -> Dict[str, List[Dict]]:
        """读取并缓存 infoharbor_block.dat，返回按分类分组的板块列表。"""
        path = self._get_file_path('tdx', 'infoharbor_block')
        if not path:
            return {}
        result = self._read_file_cached(
            path, self._get_encoding('tdx', 'infoharbor_block'),
            self._parse_tdx_infoharbor_block)
        return result or {}

    # ------------------------------------------------------------------
    # 通达信 base.dbf 地区板块成分股解析（DBF 格式）
    # ------------------------------------------------------------------

    def _parse_tdx_base_dbf_region(self, raw: bytes) -> Dict[str, List[str]]:
        """解析 base.dbf 的 DY 字段，返回地区代码→成分股列表映射。

        DBF/dBASE III 格式：
        - 头部 32 字节，记录数在偏移 4-7（小端），记录长度在偏移 10-11
        - 字段定义每字段 32 字节，以 0x0D 结束
        - 记录首字节为删除标志（0x20=正常, 0x2A=删除）
        - SC 字段（市场标志）在记录内偏移 1，长度 1（'0'=深圳, '1'=上海, '2'=北京）
        - GPDM 字段（股票代码）在记录内偏移 2，长度 6
        - DY 字段（地区代码）在记录内偏移 450，长度 3

        DY 代码 1-32 对应 tdxzs.cfg type=3 的 32 个地区（序列号=DY代码）
        DY=0 表示非 A 股股票（指数/债券/基金等），跳过

        返回：{'1': ['SH600001', ...], '7': ['SZ000008', ...], ...}
        """
        if len(raw) < 32:
            return {}
        # 解析 DBF 头部
        record_count = int.from_bytes(raw[4:8], 'little')
        header_size = int.from_bytes(raw[8:10], 'little')
        record_size = int.from_bytes(raw[10:12], 'little')
        if record_size == 0 or record_count == 0:
            return {}

        # 字段偏移（依据 base.dbf 实际字段定义）：
        # SC(市场,offset=1,len=1) + GPDM(代码,offset=2,len=6) + ... + DY(地区,offset=450,len=3)
        sc_offset = 1
        code_offset = 2
        code_len = 6
        dy_offset = 450
        dy_len = 3

        region_map: Dict[str, List[str]] = {}
        data_start = header_size
        for i in range(record_count):
            rec_start = data_start + i * record_size
            if rec_start + record_size > len(raw):
                break
            # 删除标志
            del_flag = raw[rec_start]
            if del_flag == 0x2A:  # 已删除
                continue
            # 市场标志 SC（'0'=深圳, '1'=上海, '2'=北京）
            sc_byte = raw[rec_start + sc_offset: rec_start + sc_offset + 1]
            sc = sc_byte.decode('ascii', errors='replace').strip()
            # 股票代码 GPDM
            code_bytes = raw[rec_start + code_offset: rec_start + code_offset + code_len]
            code = code_bytes.decode('ascii', errors='replace').strip()
            if not code or not code.isdigit() or len(code) != 6:
                continue
            # 地区代码 DY
            dy_bytes = raw[rec_start + dy_offset: rec_start + dy_offset + dy_len]
            dy = dy_bytes.decode('ascii', errors='replace').strip()
            if not dy or not dy.isdigit() or dy == '0':
                continue
            # 市场判断：优先用 SC 字段，兜底用代码前缀
            market = None
            if sc == '0':
                market = 'SZ'
            elif sc == '1':
                market = 'SH'
            elif sc == '2':
                market = 'BJ'
            if not market:
                first = code[0]
                info = self._code_prefix_map.get(first)
                if info:
                    market = info['market']
                elif first == '9':
                    market = 'SH'
                else:
                    market = 'SZ'
            full_code = f"{market}{code}"
            region_map.setdefault(dy, []).append(full_code)
        return region_map

    def _get_tdx_base_dbf_region_members(self) -> Dict[str, List[str]]:
        """读取 base.dbf 并返回地区代码→成分股列表映射。

        返回：{'1': ['SH600001', ...], '7': ['SZ000008', ...], ...}
        """
        path = self._get_file_path('tdx', 'base_dbf')
        if not path:
            return {}
        try:
            with open(path, 'rb') as f:
                raw = f.read()
        except OSError as e:
            logger.warning("读取 base.dbf 失败: %s", e)
            return {}
        return self._parse_tdx_base_dbf_region(raw)

    # ------------------------------------------------------------------
    # 股票名称映射（infoharbor_ex.code）
    # ------------------------------------------------------------------

    def _parse_tdx_stock_names(self, text: str) -> Dict[str, str]:
        """解析 infoharbor_ex.code 文件，返回 {full_code: name} 映射。

        格式：股票代码|股票名称|关键词列表（如 000001|平安银行|平安保险,谢永林）
        股票代码 6 位数字按前缀推断市场（6→SH, 0/3→SZ, 4/8→BJ）
        """
        name_map: Dict[str, str] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or '|' not in line:
                continue
            parts = line.split('|', 2)
            if len(parts) < 2:
                continue
            code = parts[0].strip()
            name = parts[1].strip()
            if len(code) != 6 or not code.isdigit():
                continue
            # 按代码前缀推断市场
            first = code[0]
            info = self._code_prefix_map.get(first)
            if not info:
                if first == '9':
                    market = 'SH'
                elif first == '2':
                    market = 'SZ'
                else:
                    market = 'SZ'
            else:
                market = info['market']
            full_code = f"{market}{code}"
            if name:
                name_map[full_code] = name
        return name_map

    def _parse_tdx_bj_stock_names(self, text: str) -> Dict[str, str]:
        """解析 addedcode_bj.cfg 文件，返回 {full_code: name} 映射。

        格式：市场标志|旧代码|新代码|股票名称(含状态)|日期
        如：44|832000|920000|安徽凤凰(已切换)|20251009

        同时注册旧代码和新代码（均以 BJ 为市场前缀），名称去除状态后缀。
        """
        name_map: Dict[str, str] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or '|' not in line:
                continue
            parts = line.split('|')
            if len(parts) < 4:
                continue
            old_code = parts[1].strip()
            new_code = parts[2].strip()
            raw_name = parts[3].strip()
            # 去除状态后缀（如 "(已切换)"、"(已转板)"）
            name = re.sub(r'\([^)]*\)$', '', raw_name).strip()
            if not name:
                continue
            # 旧代码（430xxx, 830xxx, 870xxx, 874xxx 等）
            if len(old_code) == 6 and old_code.isdigit():
                name_map[f"BJ{old_code}"] = name
            # 新代码（920xxx）
            if len(new_code) == 6 and new_code.isdigit():
                name_map[f"BJ{new_code}"] = name
        return name_map

    def _parse_ths_stockspirit_json(self, text: str) -> Dict[str, str]:
        """解析同花顺 stockspirit.json 文件，返回 {full_code: name} 映射。

        格式：JSON 字典 {"股票名": "代码.市场", ...}
        如：{"达瑞电子": "301696.SZ", "浦发银行": "600000.SH", ...}

        反转为 {MARKET+code: name} 格式，与 TDX 代码格式一致。
        """
        import json as _json
        name_map: Dict[str, str] = {}
        try:
            data = _json.loads(text)
        except Exception as e:
            logger.warning("解析 THS stockspirit.json 失败: %s", e)
            return name_map
        if not isinstance(data, dict):
            return name_map
        # 反转 name→code 为 code→name
        for name, code_val in data.items():
            name = str(name).strip()
            code_val = str(code_val).strip()
            if not name or not code_val or '.' not in code_val:
                continue
            # 格式: "301696.SZ" → "SZ301696"
            parts = code_val.split('.')
            if len(parts) != 2:
                continue
            num_code, market = parts[0].strip(), parts[1].strip().upper()
            if len(num_code) == 6 and num_code.isdigit() and market in ('SH', 'SZ', 'BJ'):
                full_code = f"{market}{num_code}"
                name_map[full_code] = name
        return name_map

    _stock_name_cache: Optional[Dict[str, str]] = None

    # profile.dat 固定记录长度（字节）
    _TDX_PROFILE_RECORD_SIZE = 64

    def _parse_tdx_profile_dat(self, raw: bytes) -> Dict[str, str]:
        """解析 profile.dat 二进制文件，返回 {SZ+code: name} 映射。

        格式：固定 64 字节记录，每条 \\x00 + 6位代码 + \\x00 + 名称(GBK, 填充\\x00) + 其他数据。
        含历史名称（ST/*ST/S 前缀），取最后一条记录作为当前名称。
        profile.dat 仅含深圳市场股票（SZ0/3 A 股 + SZ2 B 股）。
        """
        name_map: Dict[str, str] = {}
        size = len(raw)
        record_size = self._TDX_PROFILE_RECORD_SIZE
        # 按固定记录长度遍历
        for offset in range(0, size - 8, record_size):
            # 记录首字节应为 \x00，之后是 6 位数字代码
            if raw[offset] != 0:
                continue
            code_bytes = raw[offset + 1: offset + 7]
            try:
                code_str = code_bytes.decode('ascii')
            except UnicodeDecodeError:
                continue
            if not (len(code_str) == 6 and code_str.isdigit()):
                continue
            # 代码后应为 \x00，之后是名称（到下一个 \x00）
            if raw[offset + 7] != 0:
                continue
            name_start = offset + 8
            name_end = raw.find(b'\x00', name_start)
            if name_end < 0 or name_end >= offset + record_size:
                name_end = offset + record_size
            name_bytes = raw[name_start:name_end].rstrip(b'\x00')
            if not name_bytes:
                continue
            try:
                name = name_bytes.decode('gbk')
            except UnicodeDecodeError:
                try:
                    name = name_bytes.decode('gb18030', errors='replace')
                except Exception:
                    continue
            # profile.dat 只含深圳股票：0/3 开头 A 股，20 开头 B 股
            # 取最后一条记录（最新名称），覆盖之前的历史名称
            name_map[f"SZ{code_str}"] = name
        return name_map

    def _get_tdx_profile_names(self) -> Dict[str, str]:
        """读取 profile.dat 并返回 {SZ+code: name} 映射。

        profile.dat 含深圳股票历史名称，可补全 infoharbor_ex.code 缺失的 SZ2 B 股名。
        """
        path = self._get_file_path('tdx', 'profile_dat')
        if not path:
            return {}
        try:
            with open(path, 'rb') as f:
                raw = f.read()
        except OSError as e:
            logger.warning("读取 profile.dat 失败: %s", e)
            return {}
        try:
            return self._parse_tdx_profile_dat(raw)
        except Exception as e:
            logger.warning("解析 profile.dat 失败: %s", e)
            return {}

    def get_stock_name_map(self) -> Dict[str, str]:
        """获取股票代码→名称映射（供 API 层补全个股名）。

        数据来源（合并多个文件以覆盖所有股票）：
        1. TDX infoharbor_ex.code（5500+ 只 A 股名称，不含北交所）
        2. TDX addedcode_bj.cfg（300+ 只北交所股票名称，补全 BJ 股票）
        3. THS stockspirit.json（6287 条股票名，补充 TDX 缺失的名称）
        4. TDX profile.dat（929 只深圳股票历史名称，补全 SZ2 B 股等缺失名称）

        返回：{'SH600000': '浦发银行', 'SZ000001': '平安银行', 'BJ920017': '星昊医药', ...}
        """
        if self._stock_name_cache is not None:
            return self._stock_name_cache

        name_map: Dict[str, str] = {}

        # 1. 主数据源：TDX infoharbor_ex.code（A股名称，不含BJ）
        path = self._get_file_path('tdx', 'infoharbor_ex_code')
        if path:
            result = self._read_file_cached(
                path, self._get_encoding('tdx', 'infoharbor_ex_code'),
                self._parse_tdx_stock_names)
            if result:
                name_map.update(result)

        # 2. 补充数据源：TDX addedcode_bj.cfg（北交所股票名称）
        bj_path = self._get_file_path('tdx', 'addedcode_bj')
        if bj_path:
            bj_result = self._read_file_cached(
                bj_path, self._get_encoding('tdx', 'addedcode_bj'),
                self._parse_tdx_bj_stock_names)
            if bj_result:
                name_map.update(bj_result)

        # 3. 补充数据源：THS stockspirit.json（补充 TDX 缺失的名称）
        ths_path = self._get_file_path('ths', 'stockspirit_json')
        if ths_path:
            ths_result = self._read_file_cached(
                ths_path, self._get_encoding('ths', 'stockspirit_json'),
                self._parse_ths_stockspirit_json)
            if ths_result:
                # 仅补充缺失的名称，不覆盖已有名称
                for code, name in ths_result.items():
                    if code not in name_map and name:
                        name_map[code] = name

        # 4. 补充数据源：TDX profile.dat（深圳股票历史名称，补全 SZ2 B 股等）
        profile_result = self._get_tdx_profile_names()
        if profile_result:
            for code, name in profile_result.items():
                if code not in name_map and name:
                    name_map[code] = name

        self._stock_name_cache = name_map
        return self._stock_name_cache

    def _parse_dzh_block_ini(self, text: str) -> List[str]:
        """解析大智慧板块配置文件（block.ini）。

        INI 格式，[BlockInfo] 段的 SysBlock 键含空格分隔的板块名列表。
        """
        block_names: List[str] = []
        in_block_info = False
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            # 段头判断
            if line.startswith('[') and line.endswith(']'):
                in_block_info = (line.upper() == '[BLOCKINFO]')
                continue
            if not in_block_info:
                continue
            # 键值对
            if '=' not in line:
                continue
            key, _, value = line.partition('=')
            if key.strip().upper() == 'SYSBLOCK':
                names = value.strip().split()
                block_names.extend(names)
        return block_names

    # ------------------------------------------------------------------
    # 大智慧 classtree XML 解析（分类树结构）
    # ------------------------------------------------------------------

    # classtree XML 文件名前缀到分类的映射
    _DZH_CLASSTREE_PREFIX_MAP = {
        'bkgn': 'concept',
        'bkhy': 'industry',
        'bkzs': 'index',
        'gphq_dzhhy': 'industry',
        'gphq': 'stock_class',
        'indextree': 'index',
        'hszs': 'index',
        'jjbk': 'fund',
        'etf': 'fund',
    }

    # classtree XML 文件到分类的精确映射（优先于前缀匹配）
    _DZH_CLASSTREE_FILE_MAP = {
        'bkgnzf.xml': 'concept',
        'bkhyzf.xml': 'industry',
        'bkzs.xml': 'index',
        'gphq_dzhhy.xml': 'industry',
    }

    def _parse_dzh_classtree_xml(self, text: str, filename: str = '') -> List[Dict]:
        """解析大智慧 classtree XML 文件，返回分类树结构。

        XML 格式：<classes><class name="..."><subclass name="..." node="..."/></class></classes>
        返回：[{'class_name': '...', 'subclasses': [{'name': '...', 'node': '...'}, ...]}]
        """
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(text)
        except ET.ParseError as e:
            logger.warning("DZH classtree XML 解析失败 (%s): %s", filename, e)
            return []

        classes: List[Dict] = []
        for cls in root.findall('class'):
            class_name = cls.get('name', '')
            subclasses: List[Dict] = []
            for sub in cls.findall('subclass'):
                subclasses.append({
                    'name': sub.get('name', ''),
                    'node': sub.get('node', ''),
                    'scriptfile': sub.get('scriptfile', ''),
                })
            classes.append({
                'class_name': class_name,
                'subclasses': subclasses,
            })
        return classes

    def _get_dzh_classtree(self, category: str) -> List[Dict]:
        """获取指定分类的大智慧分类树。

        根据 category 选择对应 XML 文件：
        - concept → bkgnzf.xml
        - industry → gphq_dzhhy.xml
        - index → bkzs.xml

        返回分类树结构列表，文件不存在时返回空列表。
        """
        file_map = {
            'concept': 'bkgnzf.xml',
            'industry': 'gphq_dzhhy.xml',
            'index': 'bkzs.xml',
        }
        filename = file_map.get(category)
        if not filename:
            return []

        classtree_dir = self._get_file_path('dzh', 'classtree_dir')
        if not classtree_dir:
            return []
        xml_path = os.path.join(classtree_dir, filename)
        if not os.path.isfile(xml_path):
            logger.debug("DZH classtree XML 不存在: %s", xml_path)
            return []

        result = self._read_file_cached(
            xml_path, self._get_encoding('dzh', 'classtree_dir'),
            lambda text: self._parse_dzh_classtree_xml(text, filename))
        return result or []

    def _scan_dzh_classtree_dir(self) -> Dict[str, List[str]]:
        """扫描大智慧 classtree 目录，按分类分组返回文件名列表。

        返回：{'concept': ['bkgnzf.xml', ...], 'industry': ['gphq_dzhhy.xml', ...], ...}
        """
        classtree_dir = self._get_file_path('dzh', 'classtree_dir')
        if not classtree_dir:
            return {}
        dir_path = Path(classtree_dir)
        if not dir_path.is_dir():
            return {}

        result: Dict[str, List[str]] = {}
        for entry in dir_path.iterdir():
            if not entry.is_file() or entry.suffix.lower() != '.xml':
                continue
            fname = entry.name.lower()
            # 精确映射优先
            category = self._DZH_CLASSTREE_FILE_MAP.get(fname)
            if not category:
                # 前缀匹配
                for prefix, cat in self._DZH_CLASSTREE_PREFIX_MAP.items():
                    if fname.startswith(prefix):
                        category = cat
                        break
            if category:
                result.setdefault(category, []).append(entry.name)
        return result

    # ------------------------------------------------------------------
    # 大智慧 full.ABK 解析（板块成分股）
    # ------------------------------------------------------------------

    # full.ABK 条目正则：<板块名><7位数字板块ID>= <空格分隔的代码>
    _DZH_ABK_ENTRY_RE = re.compile(r'^(.+?)(\d{7})?$')
    # full.ABK section 名称到分类的映射
    _DZH_ABK_SECTION_MAP = {
        '大智慧概念': 'concept',
        '大智慧行业(经典)': 'industry',
        '申万行业': 'industry',
        '证监会行业': 'industry',
        '恒生行业分类': 'industry',
        '申万指数': 'index',
        '中证指数': 'index',
        '沪市指数': 'index',
        '深市指数': 'index',
        '国证指数': 'index',
        '恒生指数': 'index',
        '恒生主题指数': 'index',
        '富时指数': 'index',
        '地区板块': 'region',
        '市场分类': 'market',
        '自定义板块': 'custom',
    }

    def _parse_dzh_abk(self, text: str) -> Dict[str, Dict[str, Dict]]:
        """解析大智慧 full.ABK 文本内容，返回按 section 和板块ID组织的字典。

        格式：
            [section_name]
            板块名板块ID= 股票代码1 股票代码2 ...

        返回：{section_name: {block_id: {'name': str, 'codes': [str], 'id': str}}}
        无数字ID的条目以板块名为 key。
        """
        sections: Dict[str, Dict[str, Dict]] = {}
        current_section: Optional[str] = None

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            # section header
            if line.startswith('[') and line.endswith(']'):
                current_section = line[1:-1].strip()
                sections.setdefault(current_section, {})
                continue
            if current_section is None or '=' not in line:
                continue
            left, _, right = line.partition('=')
            left = left.strip()
            codes = right.strip().split()
            # 分离板块名和数字ID
            m = self._DZH_ABK_ENTRY_RE.match(left)
            if not m:
                continue
            name = m.group(1)
            block_id = m.group(2)
            key = block_id if block_id else name
            sections[current_section][key] = {
                'name': name,
                'id': block_id,
                'codes': codes,
            }
        return sections

    def _get_dzh_abk_data(self) -> Dict[str, Dict[str, Dict]]:
        """读取 full.ABK 并返回解析后的字典。

        先加载 full.ABK，再用 inc.ABK 的条目覆盖（增量更新）。
        文件不存在时返回空字典。
        """
        path = self._get_file_path('dzh', 'full_abk')
        if not path:
            return {}
        result = self._read_file_cached(
            path, self._get_encoding('dzh', 'full_abk'),
            self._parse_dzh_abk)
        if not result:
            return {}

        # 增量更新：用 inc.ABK 覆盖同 section 同 block_id 的条目
        client_cfg = self._paths_config.get('clients', {}).get('dzh', {})
        file_cfg = client_cfg.get('files', {}).get('full_abk', {})
        inc_path_rel = file_cfg.get('inc_abk_path')
        if inc_path_rel:
            dzh_home = self._homes.get('dzh', '')
            if dzh_home:
                inc_path = os.path.join(dzh_home, inc_path_rel)
                if os.path.isfile(inc_path):
                    inc_data = self._read_file_cached(
                        inc_path, 'gbk', self._parse_dzh_abk)
                    if inc_data:
                        for section, blocks in inc_data.items():
                            if section not in result:
                                result[section] = {}
                            result[section].update(blocks)
                        logger.info("DZH inc.ABK 增量更新已合并 (%d sections)",
                                    len(inc_data))
        return result

    def _get_dzh_abk_members(self, node_id: str) -> List[str]:
        """根据 classtree XML 的 node ID 获取板块成分股。

        node ID 转换规则：补 "0" 前缀变成 7 位 ABK block ID
        （如 "600019" → "0600019"）。

        返回成分股代码列表（如 ['SH600000', 'SZ000001', ...]）。
        找不到时返回空列表。
        """
        abk_data = self._get_dzh_abk_data()
        if not abk_data:
            return []
        abk_id = '0' + node_id if len(node_id) == 6 else node_id
        for section_name, blocks in abk_data.items():
            block = blocks.get(abk_id)
            if block:
                return block.get('codes', [])
        logger.debug("DZH ABK 未找到 node=%s (abk_id=%s)", node_id, abk_id)
        return []

    def _get_dzh_abk_sectors_by_category(self, category: str) -> List[Dict]:
        """按分类获取大智慧 ABK 板块列表（含名称和成分股）。

        根据 section 名称映射到分类，返回该分类下所有板块。
        每个板块含 block_id、name、members（成分股列表）。
        """
        abk_data = self._get_dzh_abk_data()
        if not abk_data:
            return []

        # 找到属于该分类的所有 section
        target_sections = [
            sec for sec, cat in self._DZH_ABK_SECTION_MAP.items()
            if cat == category
        ]

        sectors: List[Dict] = []
        for section_name in target_sections:
            blocks = abk_data.get(section_name, {})
            for block_id, block_info in blocks.items():
                codes = block_info.get('codes', [])
                sectors.append({
                    'code': block_id,
                    'name': block_info.get('name', ''),
                    'block_id': block_id,
                    'block_name': block_info.get('name', ''),
                    'members': codes,
                    'member_count': len(codes),
                    'section': section_name,
                })
        return sectors

    # ------------------------------------------------------------------
    # 自选股 / 自定义板块接口
    # ------------------------------------------------------------------

    def get_user_sector(self) -> Dict[str, List[Dict]]:
        """解析自选股文件，返回 {'favorites': [...], 'custom_blocks': [...]}。

        依次尝试 TDX → DZH → THS，任一可用即返回结果。
        - favorites 元素：{'setcode': int, 'code': str, 'name': str}
        - custom_blocks 元素：{'block_code': str, 'block_name': str, 'members': [...]}
        """
        for client in _CLIENT_ORDER:
            if client not in self._homes:
                continue
            result = self._get_user_sector_for_client(client)
            if result['favorites'] or result['custom_blocks']:
                logger.debug("get_user_sector 使用 %s，自选股 %d 个，自定义板块 %d 个",
                             client, len(result['favorites']), len(result['custom_blocks']))
                return result
        return {'favorites': [], 'custom_blocks': []}

    def _get_user_sector_for_client(self, client: str) -> Dict[str, List[Dict]]:
        """获取指定客户端的自选股与自定义板块。"""
        if client == 'tdx':
            return self._get_user_sector_tdx()
        if client == 'dzh':
            return self._get_user_sector_dzh()
        if client == 'ths':
            return self._get_user_sector_ths()
        return {'favorites': [], 'custom_blocks': []}

    def _get_user_sector_tdx(self) -> Dict[str, List[Dict]]:
        """获取通达信自选股与自定义板块。"""
        favorites: List[Dict] = []
        custom_blocks: List[Dict] = []

        # 自选股（zxg.blk，与 .blk 格式相同：市场位+6位代码）
        zxg_path = self._get_file_path('tdx', 'favorites')
        if zxg_path:
            parsed = self._read_file_cached(
                zxg_path, self._get_encoding('tdx', 'favorites'), self._parse_tdx_zxg)
            if parsed:
                favorites = parsed

        # 自定义板块：扫描 blocknew 目录下的 .blk 文件
        blocks = self._scan_tdx_blocknew_dir()
        for blk in blocks:
            block_filename = blk.get('block_filename', '')
            if not block_filename:
                continue
            blk_path = self._get_block_file_path('tdx', block_filename)
            if not blk_path:
                continue
            members = self._read_file_cached(
                blk_path, self._get_encoding('tdx', 'block_members_pattern'),
                self._parse_tdx_blk)
            custom_blocks.append({
                'block_code': blk.get('block_code', block_filename),
                'block_name': blk.get('block_name', ''),
                'members': members or [],
            })
        return {'favorites': favorites, 'custom_blocks': custom_blocks}

    def _get_user_sector_dzh(self) -> Dict[str, List[Dict]]:
        """获取大智慧自选股与自定义板块。

        自选股优先解析 USERDATA/block/自选股.BLK（二进制格式），降级到 cfg/zxg.cfg；
        自定义板块优先从 ABK 文件的"自定义板块"section 获取（权威来源），
        降级到 USERDATA/block/ 目录扫描（需排除系统板块）。
        """
        favorites: List[Dict] = []

        # 自选股：优先 USERDATA/block/自选股.BLK（二进制）
        userdata_block_dir = self._get_file_path('dzh', 'userdata_block_dir')
        if userdata_block_dir:
            client_cfg = self._paths_config.get('clients', {}).get('dzh', {})
            file_cfg = client_cfg.get('files', {}).get('userdata_block_dir', {})
            fav_filename = file_cfg.get('favorites_filename', '自选股.BLK')
            fav_path = Path(userdata_block_dir) / fav_filename
            if fav_path.is_file():
                parsed = self._read_binary_cached(
                    str(fav_path), self._parse_dzh_blk_binary)
                if parsed:
                    favorites = parsed

        # 降级：cfg/zxg.cfg（文本格式）
        if not favorites:
            zxg_path = self._get_file_path('dzh', 'favorites')
            if zxg_path:
                parsed = self._read_file_cached(
                    zxg_path, self._get_encoding('dzh', 'favorites'),
                    self._parse_dzh_zxg)
                if parsed:
                    favorites = parsed

        # 自定义板块：优先从 ABK 的"自定义板块"section 获取（权威来源）
        custom_blocks: List[Dict] = []
        abk_data = self._get_dzh_abk_data()
        if abk_data:
            for sec_name, blks in abk_data.items():
                if '自定义' not in sec_name:
                    continue
                for blk_id, blk_info in blks.items():
                    blk_name = blk_info.get('name', '')
                    if not blk_name:
                        continue
                    codes = blk_info.get('codes', [])
                    members = []
                    for c in codes:
                        if isinstance(c, str) and len(c) >= 6:
                            code = c[-6:] if len(c) > 6 else c
                            info = self._code_prefix_map.get(code[0])
                            setcode = info['setcode'] if info else 0
                            members.append({
                                'setcode': setcode,
                                'code': code,
                                'name': '',
                            })
                    custom_blocks.append({
                        'block_code': blk_id,
                        'block_name': blk_name,
                        'members': members,
                    })

        # 降级：USERDATA/block 目录扫描（排除明显的系统板块）
        if not custom_blocks and userdata_block_dir:
            blocks = self._scan_dzh_userdata_block_dir()
            if blocks:
                for blk in blocks:
                    block_filename = blk.get('block_filename', '')
                    if not block_filename:
                        continue
                    blk_path = Path(userdata_block_dir) / block_filename
                    if not blk_path.is_file():
                        continue
                    members = self._read_binary_cached(
                        str(blk_path), self._parse_dzh_blk_binary)
                    custom_blocks.append({
                        'block_code': blk.get('block_code', ''),
                        'block_name': blk.get('block_name', ''),
                        'members': members or [],
                    })

        return {'favorites': favorites, 'custom_blocks': custom_blocks}

    def _find_ths_self_stock_json(self) -> Optional[str]:
        """查找同花顺 SelfStockInfo.json 文件（游客或登录用户）。"""
        ths_home = self._homes.get('ths')
        if not ths_home:
            return None
        home_path = Path(ths_home)
        candidates = []
        try:
            for entry in home_path.iterdir():
                if not entry.is_dir():
                    continue
                if (entry.name.startswith('thsguest_')
                        or entry.name.startswith('userdata')
                        or entry.name == '股神之师'):
                    candidates.append(entry / 'SelfStockInfo.json')
        except OSError:
            pass
        best_path = None
        best_size = 0
        for cand in candidates:
            if cand.is_file():
                try:
                    size = cand.stat().st_size
                    if size > best_size:
                        best_size = size
                        best_path = str(cand)
                except OSError:
                    pass
        if best_path and best_size > 10:
            return best_path
        return None

    def _parse_ths_self_stock_json(self, content: str) -> List[Dict]:
        """解析同花顺 SelfStockInfo.json，返回自选股列表。"""
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return []
        if not data or not isinstance(data, list):
            return []
        result: List[Dict] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            code = str(item.get('C', '') or item.get('code', '')).strip()
            market = item.get('M', '') or item.get('market', '')
            name = str(item.get('N', '') or item.get('name', '')).strip()
            if not code:
                continue
            setcode = 0
            if market == 17 or market == 'SH':
                setcode = 1
            elif market == 33 or market == 'SZ':
                setcode = 0
            elif market == -105 or market == 'BJ':
                setcode = 2
            else:
                info = self._code_prefix_map.get(code[0])
                if info:
                    setcode = info['setcode']
            result.append({
                'setcode': setcode,
                'code': code,
                'name': name,
            })
        return result

    def _get_user_sector_ths(self) -> Dict[str, List[Dict]]:
        """获取同花顺自选股与自定义板块。

        自选股：优先从 SelfStockInfo.json 获取（游客/登录用户目录），
               再尝试 BlockUpdate 自定义板块中的"自选股"相关板块，
               降级到 hexin/ZXG.cfg；
        自定义板块：优先从 BlockUpdate 的 custom 分类获取（block_22.ini），
                   降级到 hexin/Block.cfg 或 custom_block/ 目录。
        """
        favorites: List[Dict] = []
        custom_blocks: List[Dict] = []

        # 0. 自选股：优先从 SelfStockInfo.json 获取
        self_stock_path = self._find_ths_self_stock_json()
        if self_stock_path:
            parsed = self._read_file_cached(
                self_stock_path, 'utf-8', self._parse_ths_self_stock_json)
            if parsed:
                favorites = parsed

        # 1. 自定义板块：优先从 BlockUpdate 的 custom 分类获取
        custom_sectors = self._get_ths_blockupdate_sectors('custom')
        if custom_sectors:
            for sec in custom_sectors:
                members_raw = []
                for m in sec.get('members', []):
                    # 从 SH600000 / SZ000001 格式解析
                    if len(m) >= 8 and m[:2] in ('SH', 'SZ', 'BJ'):
                        code = m[2:8]
                        market = m[:2]
                        setcode, _ = self._market_to_setcode(market, code)
                        members_raw.append({
                            'setcode': setcode,
                            'code': code,
                            'name': '',
                        })
                    elif len(m) == 6 and m.isdigit():
                        info = self._code_prefix_map.get(m[0])
                        if info:
                            members_raw.append({
                                'setcode': info['setcode'],
                                'code': m,
                                'name': '',
                            })
                custom_blocks.append({
                    'block_code': sec.get('code', ''),
                    'block_name': sec.get('name', ''),
                    'members': members_raw,
                })

        # 2. 自选股：尝试从 BlockUpdate 自定义板块中找"自选股"相关板块
        if not favorites and custom_sectors:
            for sec in custom_sectors:
                sec_name = sec.get('name', '')
                if '自选股' in sec_name or 'zxg' in sec_name.lower():
                    for m in sec.get('members', []):
                        if len(m) >= 8 and m[:2] in ('SH', 'SZ', 'BJ'):
                            code = m[2:8]
                            market = m[:2]
                            setcode, _ = self._market_to_setcode(market, code)
                            favorites.append({
                                'setcode': setcode,
                                'code': code,
                                'name': '',
                            })
                        elif len(m) == 6 and m.isdigit():
                            info = self._code_prefix_map.get(m[0])
                            if info:
                                favorites.append({
                                    'setcode': info['setcode'],
                                    'code': m,
                                    'name': '',
                                })
                    if favorites:
                        break

        # 3. 自选股降级：hexin/ZXG.cfg（文本格式）
        if not favorites:
            zxg_path = self._get_file_path('ths', 'favorites_zxg')
            if zxg_path:
                parsed = self._read_file_cached(
                    zxg_path, self._get_encoding('ths', 'favorites_zxg'),
                    self._parse_ths_favorites)
                if parsed:
                    favorites = parsed

        # 4. 自定义板块降级：Block.cfg 或 custom_block/ 目录
        if not custom_blocks:
            # 4a. 优先解析 hexin/Block.cfg
            block_cfg_path = self._get_file_path('ths', 'block_cfg')
            if block_cfg_path:
                parsed = self._read_file_cached(
                    block_cfg_path, self._get_encoding('ths', 'block_cfg'),
                    self._parse_ths_blocks)
                if parsed:
                    custom_blocks = parsed

            # 4b. 降级：扫描 custom_block/ 目录
            if not custom_blocks:
                block_dir = self._get_file_path('ths', 'custom_blocks_index')
                if block_dir:
                    dir_path = Path(block_dir)
                    if dir_path.is_dir():
                        try:
                            for entry in sorted(dir_path.iterdir()):
                                if not entry.is_file():
                                    continue
                                block_code = entry.name
                                custom_blocks.append({
                                    'block_code': block_code,
                                    'block_name': block_code,
                                    'members': [],
                                })
                        except OSError as e:
                            logger.warning("扫描 THS custom_block 目录失败 %s: %s",
                                           block_dir, e)

        return {'favorites': favorites, 'custom_blocks': custom_blocks}

    # ------------------------------------------------------------------
    # 板块成分股接口
    # ------------------------------------------------------------------

    def get_block_members(self, block_code) -> List[str]:
        """解析板块成分股，返回股票代码列表（如 ['600000.SH', '000001.SZ']）。

        依次尝试 TDX → DZH → THS 的自定义板块文件。
        若自定义板块文件未匹配，降级从系统板块（get_system_sectors）中按名称查找。
        block_code 可为板块文件名或板块名。
        """
        if not block_code:
            return []
        block_code = str(block_code)
        # 1. 依次尝试各客户端的自定义板块文件
        for client in _CLIENT_ORDER:
            if client not in self._homes:
                continue
            codes = self._get_block_members_for_client(client, block_code)
            if codes:
                return codes
        # 2. 降级：从系统板块中按名称查找（覆盖行业/概念/指数等系统板块）
        try:
            all_sectors = self.get_system_sectors()
            for source_key, categories in all_sectors.items():
                for cat, sec_list in categories.items():
                    for sec in sec_list:
                        sec_name = sec.get('name', '') or sec.get('code', '')
                        if sec_name == block_code:
                            members = sec.get('members', [])
                            if members:
                                return members
        except Exception:
            pass
        return []

    def _get_block_members_for_client(self, client: str, block_code: str) -> List[str]:
        """获取指定客户端的板块成分股（返回 XXXXXX.SH 格式列表）。"""
        members = self._get_block_members_raw(client, block_code)
        return [self._to_tq_code(m['code'], m['setcode']) for m in members]

    def _get_block_members_raw(self, client: str, block_code: str) -> List[Dict]:
        """获取指定客户端的板块成分股（返回原始成员字典列表）。"""
        if client == 'tdx':
            return self._get_block_members_tdx(block_code)
        if client == 'ths':
            return self._get_block_members_ths(block_code)
        # DZH 板块成分股文件格式未标准化，暂不支持按 block_code 解析
        return []

    def _get_block_members_ths(self, block_code: str) -> List[Dict]:
        """获取同花顺板块成分股。

        从 hexin/Block.cfg 中查找匹配 block_code 的板块成员。
        若 Block.cfg 不存在则返回空列表。
        """
        block_cfg_path = self._get_file_path('ths', 'block_cfg')
        if not block_cfg_path:
            return []
        blocks = self._read_file_cached(
            block_cfg_path, self._get_encoding('ths', 'block_cfg'),
            self._parse_ths_blocks)
        if not blocks:
            return []
        for blk in blocks:
            if block_code in (blk.get('block_code', ''), blk.get('block_name', '')):
                return blk.get('members', [])
        return []

    def _get_block_members_tdx(self, block_code: str) -> List[Dict]:
        """获取通达信板块成分股。

        先尝试直接以 block_code 作为文件名读取 .blk；
        若失败则解析 blocknew.cfg 查找匹配的板块（按文件名或板块名）。
        """
        # 1. 直接以 block_code 作为文件名
        blk_path = self._get_block_file_path('tdx', block_code)
        if blk_path:
            members = self._read_file_cached(
                blk_path, self._get_encoding('tdx', 'block_members_pattern'),
                self._parse_tdx_blk)
            if members:
                return members

        # 2. 解析 blocknew.cfg 查找匹配板块
        blocknew_path = self._get_file_path('tdx', 'custom_blocks_index')
        if blocknew_path:
            blocks = self._read_file_cached(
                blocknew_path, self._get_encoding('tdx', 'custom_blocks_index'),
                self._parse_tdx_blocknew)
            if blocks:
                for blk in blocks:
                    filename = blk.get('block_filename', '')
                    name = blk.get('block_name', '')
                    if block_code == filename or block_code == name:
                        target = filename or block_code
                        target_path = self._get_block_file_path('tdx', target)
                        if target_path:
                            members = self._read_file_cached(
                                target_path,
                                self._get_encoding('tdx', 'block_members_pattern'),
                                self._parse_tdx_blk)
                            if members:
                                return members
        return []

    # ------------------------------------------------------------------
    # 板块列表接口
    # ------------------------------------------------------------------

    def get_sector_list(self, list_type=1) -> List[Dict]:
        """返回板块列表。

        Args:
            list_type: 列表类型
                - 0：系统板块（来自 tdxbk.cfg，按类型分组返回全部系统板块）
                - 1（默认）：自定义板块（向后兼容，保持原有行为）

        Returns:
            [{'code': str, 'name': str, 'block_code': str, 'block_name': str,
              'member_count': int, 'type': str}, ...]
            其中 type 字段为系统板块类型编号（list_type=0 时有值）。
        """
        # list_type=0：返回系统板块（来自 tdxbk.cfg）
        try:
            lt_int = int(list_type)
        except (ValueError, TypeError):
            lt_int = 1
        if lt_int == 0:
            return self._get_system_sector_list()

        # list_type=1（默认）：返回自定义板块（向后兼容）
        for client in _CLIENT_ORDER:
            if client not in self._homes:
                continue
            sectors = self._get_sector_list_for_client(client)
            if sectors:
                return sectors
        return []

    def _get_system_sector_list(self) -> List[Dict]:
        """获取系统板块列表，统一为 get_sector_list 输出格式。

        系统板块无独立文件代码，以板块名作为 code/block_code。
        成员数暂不统计（系统板块成分股文件命名规则未标准化）。
        """
        blocks = self._get_tdx_system_blocks()
        if not blocks:
            return []
        result: List[Dict] = []
        for blk in blocks:
            block_name = blk.get('block_name', '')
            block_type = blk.get('type', '')
            result.append({
                'code': block_name,
                'name': block_name,
                'block_code': block_name,
                'block_name': block_name,
                'member_count': 0,
                'type': block_type,
            })
        return result

    def get_system_sectors(self) -> Dict[str, Dict[str, List[Dict]]]:
        """返回按数据源和分类分组的系统板块字典。

        仅返回**系统板块**（concept/industry/index/style/region），
        不包含板块指数、自定义板块、自选股。
        - 板块指数请用 `get_sector_index_list()`
        - 自定义板块请用 `get_custom_blocks_grouped()`
        - 自选股请用 `get_favorites_grouped()`

        Returns:
            {
                'tdx': {'concept': [...], 'industry': [...], 'index': [...],
                        'style': [...], 'region': [...]},
                'dzh': {'concept': [...], 'industry': [...], 'index': [...]},
                'ths': {'concept': [...], 'industry': [...], 'index': [...]},
            }
            每个板块元素含：
                - code: 板块代码/标识
                - name: 板块名称
                - source: 数据源（tdx_local/dzh_local/ths_local）
                - members: 成分股代码列表（如 ['SH600000', 'SZ000001', ...]）
                - member_count: 成分股数量
        """
        result: Dict[str, Dict[str, List[Dict]]] = {
            'tdx': {}, 'dzh': {}, 'ths': {},
        }

        # --- 通达信 ---
        result['tdx'] = self._get_tdx_system_sectors_grouped()

        # --- 大智慧 ---
        result['dzh'] = self._get_dzh_system_sectors_grouped()

        # --- 同花顺 ---
        result['ths'] = self._get_ths_system_sectors_grouped()

        return result

    # ------------------------------------------------------------------
    # 板块指数 / 自定义板块 / 自选股 独立查询接口
    # 三类数据各自独立方法 + 独立 API 端点，不混入 get_system_sectors
    # ------------------------------------------------------------------

    def get_sector_index_list(self, source: Optional[str] = None) -> Dict[str, List[Dict]]:
        """返回各软件的板块指数列表（按数据源分组）。

        板块指数代码（880xxx）是衡量板块走势的指数代码，与板块本身一一对应。
        本方法仅返回板块指数**定义**（code/name/type/sub_type），不含成分股。
        - TDX: tdxzs.cfg 板块指数定义
        - DZH: 从 ABK 的指数分类 section（申万指数、中证指数、沪市指数、深市指数等）提取
        - THS: 从 BlockUpdate 的 index 分类板块提取

        Args:
            source: 限定数据源（'tdx'/'dzh'/'ths'，None=返回全部数据源）

        Returns:
            {'tdx': [{'code': '880201', 'name': '农业种植',
                      'type': '4', 'sub_type': 'concept', ...}, ...],
             'dzh': [...], 'ths': [...]}
        """
        if source is not None and source not in ('tdx', 'dzh', 'ths'):
            return {}
        sources = [source] if source else ['tdx', 'dzh', 'ths']
        result: Dict[str, List[Dict]] = {s: [] for s in sources}
        src_name_map = {'tdx': 'tdx_local', 'dzh': 'dzh_local', 'ths': 'ths_local'}

        # TDX: tdxzs.cfg
        if 'tdx' in sources:
            tdx_indices = self._get_tdx_sector_indices()
            tdx_list: List[Dict] = []
            for idx in tdx_indices:
                idx_code = idx.get('code', '')
                idx_type = idx.get('type', '')
                tdx_list.append({
                    'code': idx_code,
                    'name': idx.get('name', ''),
                    'sector_index_code': idx_code,
                    'source': 'tdx_local',
                    'type': idx_type,
                    'sub_type': self._TDX_ZS_TYPE_MAP.get(idx_type, 'other'),
                    'description': f'TDX板块指数 {idx_code} {idx.get("name", "")}',
                    'member_count': 0,
                })
            result['tdx'] = tdx_list

        # DZH: 从 ABK 的指数分类 section 提取
        if 'dzh' in sources:
            dzh_list: List[Dict] = []
            abk_data = self._get_dzh_abk_data()
            if abk_data:
                index_sections = [
                    sec for sec, cat in self._DZH_ABK_SECTION_MAP.items()
                    if cat == 'index'
                ]
                seen_codes = set()
                for sec_name in index_sections:
                    blocks = abk_data.get(sec_name, {})
                    for block_id, block_info in blocks.items():
                        code = block_id
                        if code in seen_codes:
                            continue
                        seen_codes.add(code)
                        name = block_info.get('name', '')
                        member_count = len(block_info.get('codes', []))
                        dzh_list.append({
                            'code': code,
                            'name': name,
                            'sector_index_code': code,
                            'source': 'dzh_local',
                            'type': 'index',
                            'sub_type': sec_name,
                            'description': f'DZH板块指数 {sec_name} - {name}',
                            'member_count': member_count,
                        })
            result['dzh'] = dzh_list

        # THS: 从 BlockUpdate 的 index 分类提取
        if 'ths' in sources:
            ths_list: List[Dict] = []
            ths_index_sectors = self._get_ths_blockupdate_sectors('index')
            if ths_index_sectors:
                for sec in ths_index_sectors:
                    code = sec.get('code', '')
                    name = sec.get('name', '')
                    member_count = sec.get('member_count', 0)
                    source_file = sec.get('source_file', '')
                    ths_list.append({
                        'code': code,
                        'name': name,
                        'sector_index_code': code,
                        'source': 'ths_local',
                        'type': 'index',
                        'sub_type': source_file,
                        'description': f'THS板块指数 {name}',
                        'member_count': member_count,
                    })
            result['ths'] = ths_list

        return result

    def _fill_member_names(self, members: List[Dict]) -> List[Dict]:
        """为成分股列表填充名称（使用 get_stock_name_map）。

        Args:
            members: [{code, setcode, name?}, ...] 格式的成分股列表

        Returns:
            同样格式但 name 字段已填充的列表
        """
        if not members:
            return members
        try:
            name_map = self.get_stock_name_map()
        except Exception:
            return members
        if not name_map:
            return members
        result = []
        for m in members:
            if not isinstance(m, dict):
                result.append(m)
                continue
            code = m.get('code', '')
            setcode = m.get('setcode', 0)
            name = m.get('name', '')
            if not name and code:
                market_map = {0: 'SZ', 1: 'SH', 2: 'BJ'}
                market = market_map.get(setcode, 'SZ')
                full_code = f"{market}{code}"
                name = name_map.get(full_code, '')
                if not name:
                    alt_codes = [f"SH{code}", f"SZ{code}", f"BJ{code}"]
                    for ac in alt_codes:
                        if ac in name_map:
                            name = name_map[ac]
                            break
            new_m = dict(m)
            new_m['name'] = name
            result.append(new_m)
        return result

    def get_custom_blocks_grouped(self, source: Optional[str] = None) -> Dict[str, List[Dict]]:
        """返回各软件的自定义板块（按数据源分组，含成分股）。

        各数据源独立解析，互不混淆：
        - TDX: blocknew/*.blk（含成分股）
        - DZH: cfg/block.ini SysBlock + ABK"自定义板块"section 成分股
        - THS: hexin/Block.cfg 或 custom_block/ 目录

        Args:
            source: 限定数据源（'tdx'/'dzh'/'ths'，None=返回全部数据源）

        Returns:
            {'tdx': [{'code': 'blk文件名', 'name': '板块名',
                      'members': [{'stock_code': 'SH600000', 'name': '浦发银行'}, ...],
                      'member_count': N}, ...],
             'dzh': [...], 'ths': [...]}
        """
        if source is not None and source not in ('tdx', 'dzh', 'ths'):
            return {}
        sources = [source] if source else ['tdx', 'dzh', 'ths']
        result: Dict[str, List[Dict]] = {s: [] for s in sources}
        src_name_map = {'tdx': 'tdx_local', 'dzh': 'dzh_local', 'ths': 'ths_local'}

        name_map = {}
        try:
            name_map = self.get_stock_name_map()
        except Exception:
            pass

        def _lookup_name(code: str, setcode: int) -> str:
            if not name_map or not code:
                return ''
            market_map = {0: 'SZ', 1: 'SH', 2: 'BJ'}
            market = market_map.get(setcode, 'SZ')
            full_code = f"{market}{code}"
            if full_code in name_map:
                return name_map[full_code]
            for alt in (f"SH{code}", f"SZ{code}", f"BJ{code}"):
                if alt in name_map:
                    return name_map[alt]
            return ''

        for client in sources:
            if client not in self._homes:
                continue
            source_name = src_name_map[client]
            user_sector = self._get_user_sector_for_client(client)
            custom_blocks = user_sector.get('custom_blocks', []) or []
            if not custom_blocks:
                continue
            custom_list: List[Dict] = []
            for blk in custom_blocks:
                blk_code = blk.get('block_code', '') or blk.get('block_name', '')
                blk_name = blk.get('block_name', '') or blk_code
                raw_members = blk.get('members', []) or []
                member_list: List[Dict] = []
                for m in raw_members:
                    if isinstance(m, dict):
                        code = m.get('code', '')
                        if not code:
                            continue
                        setcode = m.get('setcode', 0)
                        mname = m.get('name', '') or _lookup_name(code, setcode)
                        tq_code = self._to_tq_code(code, setcode)
                        member_list.append({
                            'stock_code': tq_code,
                            'code': tq_code,
                            'name': mname,
                        })
                    elif isinstance(m, str) and len(m) >= 6:
                        info = self._code_prefix_map.get(m[0])
                        setcode = info['setcode'] if info else 0
                        mname = _lookup_name(m, setcode)
                        tq_code = self._to_tq_code(m, setcode)
                        member_list.append({
                            'stock_code': tq_code,
                            'code': tq_code,
                            'name': mname,
                        })
                custom_list.append({
                    'code': blk_code,
                    'name': blk_name,
                    'source': source_name,
                    'members': member_list,
                    'member_count': len(member_list),
                })
            result[client] = custom_list
        return result

    def get_favorites_grouped(self, source: Optional[str] = None) -> Dict[str, List[Dict]]:
        """返回各软件的自选股（按数据源分组，含完整成员列表）。

        各数据源独立解析：
        - TDX: zxg.blk
        - DZH: cfg/zxg.cfg
        - THS: hexin/ZXG.cfg

        Args:
            source: 限定数据源（'tdx'/'dzh'/'ths'，None=返回全部数据源）

        Returns:
            {'tdx': [{'code': 'tdx_favorites', 'name': 'TDX自选股',
                      'members': [{'stock_code': 'SH600000', 'name': '浦发银行'}, ...],
                      'member_count': N}],
             'dzh': [...], 'ths': [...]}
            每个数据源仅返回一条记录（该客户端的全部自选股）。
        """
        if source is not None and source not in ('tdx', 'dzh', 'ths'):
            return {}
        sources = [source] if source else ['tdx', 'dzh', 'ths']
        result: Dict[str, List[Dict]] = {s: [] for s in sources}
        src_name_map = {'tdx': 'tdx_local', 'dzh': 'dzh_local', 'ths': 'ths_local'}

        name_map = {}
        try:
            name_map = self.get_stock_name_map()
        except Exception:
            pass

        def _lookup_name(code: str, setcode: int) -> str:
            if not name_map or not code:
                return ''
            market_map = {0: 'SZ', 1: 'SH', 2: 'BJ'}
            market = market_map.get(setcode, 'SZ')
            full_code = f"{market}{code}"
            if full_code in name_map:
                return name_map[full_code]
            for alt in (f"SH{code}", f"SZ{code}", f"BJ{code}"):
                if alt in name_map:
                    return name_map[alt]
            return ''

        for client in sources:
            if client not in self._homes:
                continue
            source_name = src_name_map[client]
            user_sector = self._get_user_sector_for_client(client)
            favorites = user_sector.get('favorites', []) or []
            if not favorites:
                continue
            member_list: List[Dict] = []
            for fav in favorites:
                if isinstance(fav, dict):
                    code = fav.get('code', '')
                    if not code:
                        continue
                    setcode = fav.get('setcode', 0)
                    mname = fav.get('name', '') or _lookup_name(code, setcode)
                    tq_code = self._to_tq_code(code, setcode)
                    member_list.append({
                        'stock_code': tq_code,
                        'code': tq_code,
                        'name': mname,
                    })
                elif isinstance(fav, str) and len(fav) >= 6:
                    info = self._code_prefix_map.get(fav[0])
                    setcode = info['setcode'] if info else 0
                    mname = _lookup_name(fav, setcode)
                    tq_code = self._to_tq_code(fav, setcode)
                    member_list.append({
                        'stock_code': tq_code,
                        'code': tq_code,
                        'name': mname,
                    })
            result[client] = [{
                'code': f'{client}_favorites',
                'name': f'{client.upper()}自选股',
                'source': source_name,
                'members': member_list,
                'member_count': len(member_list),
            }]
        return result

    def get_system_sectors_flat(self) -> Dict[str, List[Dict]]:
        """向后兼容：返回扁平分类的系统板块字典（合并所有数据源）。

        返回格式与旧版 get_system_sectors() 一致：
            {'concept': [...], 'industry': [...], 'index': [...], ...}
        """
        grouped_data = self.get_system_sectors()
        flat: Dict[str, List[Dict]] = {}
        for source_key, categories in grouped_data.items():
            source_name = {
                'tdx': 'tdx_local', 'dzh': 'dzh_local', 'ths': 'ths_local'
            }.get(source_key, source_key)
            for cat, sectors in categories.items():
                flat.setdefault(cat, []).extend(sectors)
        return flat

    # ------------------------------------------------------------------
    # 板块 ↔ 板块指数代码 ↔ 个股 双向映射求解
    # ------------------------------------------------------------------

    def get_members_by_sector_index_code(self, index_code: str,
                                         source: Optional[str] = None) -> Optional[Dict]:
        """由板块指数代码输出板块成分股。

        板块指数代码（如 880201）是衡量板块走势的指数代码，与板块名一一对应。
        本方法建立"板块指数代码 → 成分股"的正确求解关系：
        - 概念/风格/指数: 直接从 infoharbor_block.dat 头行第4字段匹配
        - 行业: 通过 tdxzs.cfg type=2 的 desc(Txxxxxx) → tdxhy.cfg 成分股
        - 地区: 通过 tdxzs.cfg type=3 的 desc(序列号) → base.dbf DY字段成分股

        Args:
            index_code: 板块指数代码（如 '880201', '880302', '880515'）
            source: 限定数据源（'tdx'/'dzh'/'ths'，None=自动搜索所有数据源）

        Returns:
            {'sector_index_code': str, 'sector_name': str, 'category': str,
             'source': str, 'members': List[str], 'member_count': int}
            未找到返回 None。
        """
        if not index_code:
            return None
        index_code = str(index_code).strip()
        # 严格数据源过滤：None=搜索所有，无效值=返回 None
        if source is not None:
            if source not in ('tdx', 'dzh', 'ths'):
                return None
            sources = [source]
        else:
            sources = ['tdx', 'dzh', 'ths']
        grouped_all = self.get_system_sectors()
        for src in sources:
            categories = grouped_all.get(src, {})
            src_name = {'tdx': 'tdx_local', 'dzh': 'dzh_local', 'ths': 'ths_local'}.get(src, src)
            for cat, sec_list in categories.items():
                # 跳过 sector_index 分类（仅含板块指数定义，无真实成分股）
                # 该分类用于前端展示完整板块指数列表，不能用于成分股求解
                if cat == 'sector_index':
                    continue
                for sec in sec_list:
                    # 优先匹配 sector_index_code，兼容匹配 code/sector_code
                    if (sec.get('sector_index_code', '') == index_code
                            or sec.get('code', '') == index_code
                            or sec.get('sector_code', '') == index_code):
                        members = sec.get('members', [])
                        return {
                            'sector_index_code': sec.get('sector_index_code', index_code),
                            'sector_code': sec.get('sector_code', index_code),
                            'sector_name': sec.get('name', ''),
                            'category': cat,
                            'source': src_name,
                            'members': list(members),
                            'member_count': len(members),
                        }
        return None

    def get_sectors_by_stock(self, stock_code: str,
                             source: Optional[str] = None) -> List[Dict]:
        """由个股代码反查所属所有板块（板块↔个股反向映射）。

        遍历所有板块的成分股，返回包含该个股的板块列表。
        支持多数据源独立查询，不跨数据源匹配。

        Args:
            stock_code: 股票代码（如 'SH600000', 'SZ000001'），自动归一化
            source: 限定数据源（'tdx'/'dzh'/'ths'，None=搜索所有数据源）

        Returns:
            [{'sector_index_code': str, 'sector_name': str, 'category': str,
              'source': str, 'member_count': int}, ...]
            按数据源分组，每个数据源独立返回。
        """
        if not stock_code:
            return []
        target = self._to_market_prefixed_code(stock_code)
        if not target:
            return []
        # 严格数据源过滤：None=搜索所有，无效值=返回空列表
        if source is not None:
            if source not in ('tdx', 'dzh', 'ths'):
                return []
            sources = [source]
        else:
            sources = ['tdx', 'dzh', 'ths']
        grouped_all = self.get_system_sectors()
        results: List[Dict] = []
        for src in sources:
            categories = grouped_all.get(src, {})
            src_name = {'tdx': 'tdx_local', 'dzh': 'dzh_local', 'ths': 'ths_local'}.get(src, src)
            for cat, sec_list in categories.items():
                # 跳过 sector_index 分类（无真实成分股，无法反查个股）
                if cat == 'sector_index':
                    continue
                for sec in sec_list:
                    members = sec.get('members', [])
                    if not members:
                        continue
                    # 成分股匹配（归一化后比较，兼容 SH/SZ 前缀大小写）
                    if any(self._to_market_prefixed_code(m) == target for m in members):
                        results.append({
                            'sector_index_code': sec.get('sector_index_code', ''),
                            'sector_code': sec.get('sector_code', sec.get('code', '')),
                            'sector_name': sec.get('name', ''),
                            'category': cat,
                            'source': src_name,
                            'member_count': len(members),
                        })
        return results

    @staticmethod
    def _to_market_prefixed_code(code: str) -> str:
        """转成「市场前缀+数字」归一形式用于成分股匹配。

        I38 改名：与 core/_market_utils._normalize_stock_code（STRIP 前后缀→纯数字）语义相反，
        此处是 ADD 前缀→「SH600000」匹配键，故名 _to_market_prefixed_code。
        'sh600000' / 'SH600000' / '600000.SH' → 'SH600000'
        'sz000001' / 'SZ000001' / '000001.SZ' → 'SZ000001'
        """
        if not code:
            return ''
        s = str(code).strip().upper()
        # 处理 XXXXXX.XX 格式
        if '.' in s:
            parts = s.split('.')
            if len(parts) == 2:
                code_part, market_part = parts
                if market_part in ('SH', 'SZ', 'BJ'):
                    return f"{market_part}{code_part}"
                if code_part in ('SH', 'SZ', 'BJ'):
                    return f"{code_part}{market_part}"
        # 纯数字 → 按前缀推断市场
        if s.isdigit() and len(s) == 6:
            first = s[0]
            if first == '6':
                return f"SH{s}"
            if first in ('0', '3'):
                return f"SZ{s}"
            if first in ('4', '8'):
                return f"BJ{s}"
        # 已带市场前缀
        if s.startswith(('SH', 'SZ', 'BJ')) and len(s) == 8:
            return s
        return s

    def _get_tdx_system_sectors_grouped(self) -> Dict[str, List[Dict]]:
        """获取通达信系统板块（按分类分组，含完整成分股）。

        各分类成分股数据源（各自独立解析，不跨数据源匹配，使用程序实时数据）：
        - 概念板块 (concept): infoharbor_block.dat 的 #GN_ 前缀板块（含成分股）
        - 风格板块 (style): infoharbor_block.dat 的 #FG_ 前缀板块（含成分股）
        - 指数板块 (index): infoharbor_block.dat 的 #ZS_ 前缀板块（含成分股）
        - 行业板块 (industry): tdxhy.cfg 的 Txxxxxx 行业代码聚合（含成分股）
        - 地区板块 (region): base.dbf 的 DY 字段 + tdxzs.cfg type=3 地区名称映射

        板块 vs 板块指数代码（关键区分）：
        - 板块 = 一组股票的集合（如"农业种植"概念板块）
        - 板块指数代码 = 衡量板块走势的指数代码（如 880201），来自 tdxzs.cfg
        - 概念/风格/指数板块: sector_index_code = infoharbor 头行第4字段（880xxx 或空）
        - 行业板块: sector_index_code = tdxzs.cfg type=2 的 code（880xxx），industry_code = Txxxxxx
        - 地区板块: sector_index_code = tdxzs.cfg type=3 的 code（880xxx），region_seq = 序列号(1-32)

        降级策略（实时文件缺失时）：
        - tdxbk.cfg: 仅板块名，无成分股
        - tdxzs.cfg: 板块指数定义，无成分股
        """
        grouped: Dict[str, List[Dict]] = {}

        # 1. infoharbor_block.dat：概念/风格/指数板块（含完整成分股，实时数据）
        # 头行第4字段 = 板块指数代码（880xxx），与 tdxzs.cfg type=4/5 的 code 对应
        harbor_blocks = self._get_tdx_infoharbor_blocks()
        for cat, sec_list in harbor_blocks.items():
            for sec in sec_list:
                members = sec.get('members', [])
                idx_code = sec.get('code', '')
                grouped.setdefault(cat, []).append({
                    'code': idx_code,
                    'name': sec.get('name', ''),
                    'sector_index_code': idx_code,  # 板块指数代码（880xxx，可能为空）
                    'sector_code': idx_code,         # 板块代码（=板块指数代码）
                    'source': 'tdx_local',
                    'type': cat,
                    'category': cat,
                    'description': f'TDX{cat}板块 {idx_code}',
                    'members': members,
                    'member_count': len(members),
                })

        # 2. 行业板块：tdxhy.cfg 的 Txxxxxx 行业代码聚合（含成分股）
        # tdxzs.cfg type=2: code=板块指数代码(880xxx), name=中文名, desc=Txxxxxx行业代码
        # 建立 Txxxxxx → (板块指数代码, 中文名) 映射（desc 字段作 key）
        industry_idx_map = {
            idx.get('desc', '').strip(): {
                'index_code': idx.get('code', ''),
                'name': idx.get('name', ''),
            }
            for idx in self._get_tdx_sector_indices()
            if idx.get('type', '') == '2' and idx.get('desc', '').strip()
        }
        industry_members = self._get_tdx_industry_members()
        for ind_code, members in industry_members.items():
            idx_info = industry_idx_map.get(ind_code, {})
            idx_code = idx_info.get('index_code', '')
            ind_name = idx_info.get('name', ind_code)
            grouped.setdefault('industry', []).append({
                'code': ind_code,                # 向后兼容: 保持 Txxxxxx
                'name': ind_name,
                'sector_index_code': idx_code,   # 板块指数代码（880xxx）
                'sector_code': ind_code,         # 行业代码（Txxxxxx）
                'industry_code': ind_code,       # 行业代码（Txxxxxx，明确字段）
                'source': 'tdx_local',
                'type': 'industry',
                'category': 'industry',
                'description': f'TDX行业板块 {ind_code} (指数={idx_code})',
                'members': members,
                'member_count': len(members),
            })

        # 3. 地区板块：base.dbf 的 DY 字段 + tdxzs.cfg type=3 地区名称映射
        # tdxzs.cfg type=3: code=板块指数代码(880xxx), name=地区名, desc=地区序列号(1-32)
        # DY 代码 = tdxzs.cfg type=3 的序列号（desc 字段）
        region_indices = [
            idx for idx in self._get_tdx_sector_indices()
            if idx.get('type', '') == '3' and idx.get('category', '') == '1'
        ]
        region_members_map = self._get_tdx_base_dbf_region_members()
        if region_indices and region_members_map:
            # 构建序列号 → 地区信息映射
            for idx in region_indices:
                seq = idx.get('desc', '').strip()
                if not seq:
                    continue
                members = region_members_map.get(seq, [])
                idx_code = idx.get('code', '')
                grouped.setdefault('region', []).append({
                    'code': idx_code,                # 板块指数代码（880xxx）
                    'name': idx.get('name', ''),
                    'sector_index_code': idx_code,   # 板块指数代码（880xxx）
                    'sector_code': idx_code,         # 板块代码
                    'region_seq': seq,               # 地区序列号（1-32，用于关联 base.dbf）
                    'source': 'tdx_local',
                    'type': 'region',
                    'category': 'region',
                    'description': f'TDX地区板块 {idx_code} (DY={seq})',
                    'members': members,
                    'member_count': len(members),
                })

        # 4. 降级路径：当所有实时文件都缺失时，使用 tdxbk.cfg + tdxzs.cfg（无成分股）
        if not grouped:
            logger.warning("TDX 实时数据文件缺失，降级到 tdxbk.cfg + tdxzs.cfg（无成分股）")
            blocks = self._get_tdx_system_blocks()
            for blk in blocks:
                type_name = blk.get('type_name', 'other')
                block_name = blk.get('block_name', '')
                grouped.setdefault(type_name, []).append({
                    'code': block_name,
                    'name': block_name,
                    'source': 'tdx_local',
                    'type': blk.get('type', ''),
                    'description': blk.get('block_desc', ''),
                    'members': [],
                    'member_count': 0,
                })
            for idx in self._get_tdx_sector_indices():
                idx_type = idx.get('type', '')
                idx_category = idx.get('category', '')
                if idx_type == '3' and idx_category == '1':
                    cat_name = 'region'
                elif idx_type == '5' and idx_category == '2':
                    cat_name = 'style'
                else:
                    cat_name = 'index'
                grouped.setdefault(cat_name, []).append({
                    'code': idx.get('code', ''),
                    'name': idx.get('name', ''),
                    'source': 'tdx_local',
                    'type': idx_type,
                    'description': idx.get('desc', ''),
                    'members': [],
                    'member_count': 0,
                })

        return grouped

    def _get_dzh_system_sectors_grouped(self) -> Dict[str, List[Dict]]:
        """获取大智慧系统板块（按分类分组，含成分股）。

        数据来源：
        - full.ABK: 完整板块数据（含名称和成分股），优先
        - classtree XML: 分类树结构（降级，仅板块名无成分股）
        """
        grouped: Dict[str, List[Dict]] = {}

        # 1. 优先从 full.ABK 获取（含成分股）
        abk_categories = ['concept', 'industry', 'index', 'region']
        for cat in abk_categories:
            sectors = self._get_dzh_abk_sectors_by_category(cat)
            if sectors:
                for sec in sectors:
                    sec['source'] = 'dzh_local'
                    grouped.setdefault(cat, []).append(sec)

        # 2. 降级：从 classtree XML 获取（仅板块名，无成分股）
        # 仅当 ABK 无数据时使用
        if not grouped:
            xml_categories = ['concept', 'industry', 'index']
            for cat in xml_categories:
                classtree = self._get_dzh_classtree(cat)
                if not classtree:
                    continue
                for cls in classtree:
                    class_name = cls.get('class_name', '')
                    for sub in cls.get('subclasses', []):
                        node_id = sub.get('node', '')
                        sub_name = sub.get('name', '')
                        # 尝试从 ABK 获取成分股
                        members = self._get_dzh_abk_members(node_id) if node_id else []
                        grouped.setdefault(cat, []).append({
                            'code': node_id,
                            'name': sub_name,
                            'source': 'dzh_local',
                            'members': members,
                            'member_count': len(members),
                            'class_name': class_name,
                        })

        return grouped

    def _get_ths_system_sectors_grouped(self) -> Dict[str, List[Dict]]:
        """获取同花顺系统板块（按分类分组，含成分股）。

        数据来源：BlockUpdate/block_*.ini（同时含板块名称和成分股）
        """
        grouped: Dict[str, List[Dict]] = {}

        # 获取所有分类的板块
        sectors = self._get_ths_blockupdate_sectors()
        for sec in sectors:
            cat = sec.get('category', 'other')
            sec['source'] = 'ths_local'
            grouped.setdefault(cat, []).append(sec)

        return grouped

    def _get_sector_list_for_client(self, client: str) -> List[Dict]:
        """获取指定客户端的自定义板块列表。"""
        if client == 'tdx':
            return self._get_sector_list_tdx()
        if client == 'dzh':
            return self._get_sector_list_dzh()
        if client == 'ths':
            return self._get_sector_list_ths()
        return []

    def _get_sector_list_tdx(self) -> List[Dict]:
        """获取通达信自定义板块列表。"""
        blocks = self._scan_tdx_blocknew_dir()
        if not blocks:
            return []
        result: List[Dict] = []
        for blk in blocks:
            block_code = blk.get('block_code', '')
            block_name = blk.get('block_name', '')
            # 统计成员数（读取 .blk 文件）
            member_count = 0
            if block_code:
                blk_path = self._get_block_file_path('tdx', block_code)
                if blk_path:
                    members = self._read_file_cached(
                        blk_path, self._get_encoding('tdx', 'block_members_pattern'),
                        self._parse_tdx_blk)
                    member_count = len(members) if members else 0
            result.append({
                'code': block_code,
                'name': block_name,
                'block_code': block_code,
                'block_name': block_name,
                'member_count': member_count,
            })
        return result

    def _get_sector_list_dzh(self) -> List[Dict]:
        """获取大智慧自定义板块列表（含成分股数量）。

        优先扫描 USERDATA/block/ 目录，降级到 block.ini + ABK。
        """
        # 优先：USERDATA/block/ 目录
        blocks = self._scan_dzh_userdata_block_dir()
        if blocks:
            userdata_block_dir = self._get_file_path('dzh', 'userdata_block_dir')
            result: List[Dict] = []
            for blk in blocks:
                block_name = blk.get('block_name', '')
                block_filename = blk.get('block_filename', '')
                member_count = 0
                if userdata_block_dir and block_filename:
                    blk_path = Path(userdata_block_dir) / block_filename
                    if blk_path.is_file():
                        members = self._read_binary_cached(
                            str(blk_path), self._parse_dzh_blk_binary)
                        member_count = len(members) if members else 0
                result.append({
                    'code': block_name,
                    'name': block_name,
                    'block_code': block_name,
                    'block_name': block_name,
                    'member_count': member_count,
                })
            return result

        # 降级：cfg/block.ini + ABK
        block_ini_path = self._get_file_path('dzh', 'custom_blocks_index')
        if not block_ini_path:
            return []
        block_names = self._read_file_cached(
            block_ini_path, self._get_encoding('dzh', 'custom_blocks_index'),
            self._parse_dzh_block_ini)
        if not block_names:
            return []
        abk_custom_map: Dict[str, List[str]] = {}
        try:
            abk_data = self._get_dzh_abk_data()
            if abk_data:
                for sec_name, blks in abk_data.items():
                    if '自定义' not in sec_name:
                        continue
                    for _blk_id, blk_info in blks.items():
                        name_abk = blk_info.get('name', '')
                        if name_abk:
                            abk_custom_map[name_abk] = blk_info.get('codes', [])
        except Exception as e:
            logger.debug("DZH _get_sector_list_dzh ABK 映射构建失败: %s", e)
        result: List[Dict] = []
        for name in block_names:
            members = abk_custom_map.get(name, [])
            result.append({
                'code': name,
                'name': name,
                'block_code': name,
                'block_name': name,
                'member_count': len(members),
            })
        return result

    def _get_sector_list_ths(self) -> List[Dict]:
        """获取同花顺自定义板块列表。

        优先从 BlockUpdate 的 custom 分类获取（block_22.ini），
        降级到 hexin/Block.cfg 或 custom_block/ 目录。
        """
        # 1. 优先从 BlockUpdate 的 custom 分类获取
        custom_sectors = self._get_ths_blockupdate_sectors('custom')
        if custom_sectors:
            return [{
                'code': sec.get('code', ''),
                'name': sec.get('name', ''),
                'block_code': sec.get('code', ''),
                'block_name': sec.get('name', ''),
                'member_count': sec.get('member_count', 0),
            } for sec in custom_sectors]

        # 2. 降级：hexin/Block.cfg
        block_cfg_path = self._get_file_path('ths', 'block_cfg')
        if block_cfg_path:
            blocks = self._read_file_cached(
                block_cfg_path, self._get_encoding('ths', 'block_cfg'),
                self._parse_ths_blocks)
            if blocks:
                return [{
                    'code': blk.get('block_code', ''),
                    'name': blk.get('block_name', ''),
                    'block_code': blk.get('block_code', ''),
                    'block_name': blk.get('block_name', ''),
                    'member_count': len(blk.get('members', [])),
                } for blk in blocks]

        # 3. 降级：扫描 custom_block/ 目录
        result: List[Dict] = []
        block_dir = self._get_file_path('ths', 'custom_blocks_index')
        if block_dir:
            dir_path = Path(block_dir)
            if dir_path.is_dir():
                try:
                    for entry in sorted(dir_path.iterdir()):
                        if not entry.is_file():
                            continue
                        block_code = entry.name
                        result.append({
                            'code': block_code,
                            'name': block_code,
                            'block_code': block_code,
                            'block_name': block_code,
                            'member_count': 0,
                        })
                except OSError as e:
                    logger.warning("扫描 THS custom_block 目录失败 %s: %s", block_dir, e)
        return result

    # ------------------------------------------------------------------
    # 按类型获取股票列表
    # ------------------------------------------------------------------

    def get_stock_list_by_type(self, list_type, customblockname='', **kwargs) -> List[Dict]:
        """按类型获取股票列表。

        本地文件仅包含自选股与自定义板块，支持：
        - list_type=4（自定义板块）：返回 customblockname 对应板块成员
        - list_type=0/2 或其他：返回自选股（favorites）

        Returns:
            [{'setcode': int, 'code': str, 'name': str}, ...]
        """
        # 判断是否为 spinfo.type 整数值
        spinfo_type = None
        try:
            lt_int = int(list_type)
            if lt_int in (0, 2, 4):
                spinfo_type = lt_int
        except (ValueError, TypeError):
            pass

        # 自定义板块
        if spinfo_type == 4:
            if customblockname:
                for client in _CLIENT_ORDER:
                    if client not in self._homes:
                        continue
                    members = self._get_block_members_raw(client, str(customblockname))
                    if members:
                        return [{'setcode': m['setcode'], 'code': m['code'],
                                 'name': m.get('name', '')} for m in members]
            return []

        # 其他类型：返回自选股
        user_sector = self.get_user_sector()
        return list(user_sector.get('favorites', []))
