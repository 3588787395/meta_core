"""trading_service.py - 历史记录与模拟交易（合并自 history_manager / paper_trade）。"""

import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime as _dt
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 历史记录管理（原 history_manager.py）
# ═══════════════════════════════════════════════════════════════

_BASE = Path(__file__).parent.parent
_CONFIG = _BASE / "config"
_TDXPOOL_DIR = _BASE / "tdxpool"
_TDX_BLOCK_DIR = _BASE / "data" / "tdx_blocks"


def _load_json_cache(attr_name):
    cache = globals().get(attr_name)
    if cache is None:
        try:
            fname = {'_XML_MAP': 'xml_mapping.json', '_HIST_SCHEMA': 'history_schema.json', '_ACTION_CFG': 'action_table.json'}[attr_name]
            with open(_CONFIG / fname, encoding="utf-8-sig" if 'xml' in fname else "utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}
        globals()[attr_name] = cache
    return cache


_get_history_schema = lambda: _load_json_cache('_HIST_SCHEMA')
_get_action_table = lambda: _load_json_cache('_ACTION_CFG')

_STOCK_NAMES = {}
try:
    with open(_CONFIG / "mock_data.json", encoding="utf-8") as f:
        _STOCK_NAMES = json.load(f).get('stock_names', {})
except Exception:
    pass


def _get_stock_name(market, code):
    return _STOCK_NAMES.get(code, code)


def _code_to_market(code):
    if not code:
        return 0
    c = code.strip()
    if c.startswith(('6', '9')) and len(c) == 6:
        return 2 if c.startswith('920') or c.startswith('8') else 1
    return 0


def _normalize_code(code):
    if not code:
        return ''
    code = str(code).strip().split('.')[0].lstrip('0')
    return code.zfill(6) if len(code) < 4 else (code[-6:] if len(code) > 6 and code.isdigit() else code)


def _quote_filename(fn):
    from urllib.parse import quote
    return quote(fn, safe='')


def _extract_stk_fields(stk, schema_fields, today):
    defaults_cfg = _get_history_schema()['write_defaults']['non_dict_defaults']
    is_dict = isinstance(stk, dict)
    code = (stk.get('code', '') or stk.get('label', '')) if is_dict else str(stk)
    result = {}
    for fd in schema_fields:
        dv = today if fd['default'] == '{today}' else fd['default']
        raw = stk.get(fd['python_key'], dv) if is_dict else (today if fd['python_key'] == 'indate' else defaults_cfg.get(fd['python_key'], dv))
        val = float(raw or 0) if fd['type'] == 'float' else (int(raw or 0) if fd['type'] == 'int' else (raw if raw is not None else dv))
        result[fd['name']] = f"{val:.2f}" if fd['format'] == '.2f' else (str(val).zfill(6) if fd['format'] == 'zfill6' else str(val))
    result['market'], result['code'] = str(_code_to_market(code)), code
    return result


def _write_stk_xml(path, fields, stocks, today, schema):
    if path.exists():
        try:
            root = ET.parse(str(path)).getroot()
            data_el = root.find(schema['data_element']) or ET.SubElement(root, schema['data_element'])
        except Exception:
            root, data_el = ET.Element(schema['root_element']), None
    else:
        root, data_el = ET.Element(schema['root_element']), None
    if data_el is None:
        data_el = ET.SubElement(root, schema['data_element'])
    for stk in stocks:
        ET.SubElement(data_el, schema['stock_element'], attrib=_extract_stk_fields(stk, fields, today, schema))
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    with open(path, 'w', encoding=schema['xml_encoding']) as f:
        f.write(schema['xml_declaration'] + '\n')
        tree.write(f, encoding='unicode')


def _read_history_log(pool_name, node_id, date_str):
    schema = _get_history_schema()
    base = _TDXPOOL_DIR / pool_name / node_id
    if not base.exists():
        return []
    for ext in ['.dat', '.log']:
        fpath = base / f"{date_str}{ext}"
        if not fpath.exists():
            continue
        try:
            stocks = []
            for stk in ET.parse(str(fpath)).getroot().iter(schema['stock_element']):
                attr = dict(stk.attrib)
                entry = {}
                for fd in schema['read_mapping']['fields']:
                    val = attr.get(fd['xml_attr'], fd['default'])
                    if fd.get('transform') == 'format_intime':
                        try:
                            it, ic = int(val), schema['intime_format']
                            val = f"{it // 10000:02d}:{(it // 100) % 100:02d}" if 0 <= it <= ic['hhmmss_threshold'] and len(val) >= ic['hhmmss_min_length'] else f"{it // 3600:02d}:{(it % 3600) // 60:02d}"
                        except Exception:
                            pass
                    entry[fd['python_key']] = val
                entry.update(market=attr.get('market', '0'), code=attr.get('code', ''), name=_get_stock_name(attr.get('market', '0'), attr.get('code', '')))
                stocks.append(entry)
            return stocks
        except Exception:
            continue
    return []


def _write_history_for_node(pool_name, node_id, stocks, today):
    schema = _get_history_schema()
    d = _TDXPOOL_DIR / pool_name / node_id
    d.mkdir(parents=True, exist_ok=True)
    for ek in ('dat_format', 'log_format'):
        _write_stk_xml(d / f"{today}.{ek[:3]}", schema[ek]['fields'], stocks, today, schema)


def _save_pool_history(pool_name, pool_config, execution_result):
    today = _dt.now().strftime("%Y%m%d")
    nodes = {n.get('id', ''): n for n in pool_config.get('nodes', [])}
    node_states = execution_result.get('node_states', execution_result) or {}
    saved = []
    for nid, node in nodes.items():
        if node.get('type', '') != 'tdx_state_pool':
            continue
        psatt = node.get('params', {}).get('tdx_psatt', node.get('params', {}))
        if int(psatt.get('bsavehis', 0)) != 1:
            continue
        stocks = node_states.get(nid, {}).get('stocks', [])
        if not stocks:
            continue
        _write_history_for_node(pool_name, nid, stocks, today)
        saved.append({"node_id": nid, "count": len(stocks), "files": [f"{pool_name}/{nid}/{today}.dat", f"{pool_name}/{nid}/{today}.log"]})
        logger.info("已保存历史数据: %s → %d 只", nid, len(stocks))
    return {"saved": saved}


def _append_history_entry(pool_name, node_id, node, new_stocks):
    psatt = node.get('params', {}).get('tdx_psatt', node.get('params', {}))
    if int(psatt.get('bsavehis', 0)) != 1 or not new_stocks:
        return 0
    _write_history_for_node(pool_name, node_id, new_stocks, _dt.now().strftime("%Y%m%d"))
    logger.info("实时追加入池记录: %s → +%d只", node_id, len(new_stocks))
    return len(new_stocks)


def _play_sound_alert(nsoundtype, soundfile, node_id, new_stocks):
    logger.info("[声音预警] 池%s 有%d只新股票入池, %s", node_id, len(new_stocks),
                "播放系统提示音" if nsoundtype == 0 else f"播放自定义声音: {soundfile}" if nsoundtype == 1 and soundfile else "")


def _show_popup_alert(node_id, new_stocks):
    logger.info("[弹窗提示] 池%s 新入池股票: %s", node_id, ', '.join(s.get('code', '') if isinstance(s, dict) else str(s) for s in new_stocks))


def _save_to_tdx_block(blockfile, new_stocks, bclearblock=0):
    if not blockfile or not new_stocks:
        return 0
    _TDX_BLOCK_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r'[\\/:*?"<>|]', '_', blockfile)
    fp = _TDX_BLOCK_DIR / f"{safe_name}.txt"
    codes = [c for s in new_stocks for c in [_normalize_code(s.get('code', '') if isinstance(s, dict) else str(s))] if c and len(c) >= 4]
    if bclearblock != 1:
        try:
            existing = {l.strip() for l in open(fp, 'r', encoding='gb2312', errors='replace') if l.strip()} if fp.exists() else set()
        except Exception:
            existing = set()
        codes = [c for c in codes if c not in existing] + sorted(existing)
    try:
        with open(fp, 'w', encoding='gb2312', errors='replace') as f:
            for code in codes:
                f.write(code + '\n')
        logger.info("板块保存: %s → %d只", blockfile, len(codes))
        return len(codes)
    except Exception as ex:
        logger.warning("板块保存失败(%s): %s", blockfile, ex)
        return 0


def _dispatch_pool_enter_actions(pool_name, node_id, node, new_stocks, saved_counter=None):
    psatt = (node.get('params', {}) if isinstance(node, dict) else {}).get('tdx_psatt', {}) or {}
    if isinstance(psatt, dict) and not psatt:
        psatt = node.get('params', {}) if isinstance(node, dict) else {}
    ctx = {'$pool_name': pool_name, '$node_id': node_id, '$node': node, '$new_stocks': new_stocks}
    total = 0
    action_cfg = _get_action_table()
    ops_table = action_cfg.get('callback_ops', {})
    for ak, ad in action_cfg.get('pool_enter_actions', {}).items():
        if int(psatt.get(ak, 0)) != 1:
            continue
        op = ad.get('op')
        method_name = ops_table.get(op, {}).get('method') if op else None
        fn = globals().get(method_name) if method_name else None
        if not fn or not callable(fn):
            logger.warning("回调op未找到: %s", op)
            continue
        args = [ctx.get(s) if s.startswith('$') else (int(psatt.get(s.partition(':')[0], 0)) if s.partition(':')[2] == 'int' else str(psatt.get(s.partition(':')[0], 0))) for s in ad.get('args', [])]
        try:
            result = fn(*args)
            lt = ad.get('log_on_success', '')
            if lt == 'history_saved' and isinstance(result, int) and result > 0:
                total += result
            elif lt and isinstance(result, int) and result > 0:
                lv = {k.lstrip('$'): v for k, v in ctx.items()} | {s.partition(':')[0]: psatt.get(s.partition(':')[0], '') for s in ad.get('args', []) if not s.startswith('$')} | {'result': result}
                try:
                    logger.info(re.sub(r'\{\$(\w+)\}', lambda m: str(lv.get(m.group(1), m.group(0))), lt))
                except Exception:
                    logger.info(lt)
        except Exception as e:
            logger.warning("回调执行失败(%s): %s", op, e)
    if saved_counter is not None and total > 0:
        saved_counter[0] += total
    return total


# ═══════════════════════════════════════════════════════════════
# 模拟交易（原 paper_trade.py）
# ═══════════════════════════════════════════════════════════════

# PaperTradeEngine — 仿真模式模拟成交引擎
#
# 由 engine 在 post_tick 后按 trade_interfaces.json 配置调用。
# 维护虚拟持仓、资金、盈亏、手续费、滑点。


@dataclass
class Position:
    """虚拟持仓"""
    code: str
    entry_price: float
    entry_time: float
    quantity: int = 100  # 默认1手=100股
    current_price: float = 0.0

    @property
    def market_value(self) -> float:
        return self.current_price * self.quantity

    @property
    def profit(self) -> float:
        return (self.current_price - self.entry_price) * self.quantity

    @property
    def profit_pct(self) -> float:
        if self.entry_price <= 0:
            return 0.0
        return (self.current_price - self.entry_price) / self.entry_price * 100


@dataclass
class TradeRecord:
    """成交记录"""
    code: str
    direction: str  # "buy" / "sell"
    price: float
    quantity: int
    timestamp: float
    commission: float = 0.0
    slippage: float = 0.0
    profit: float = 0.0  # 仅sell时有值


class PaperTradeEngine:
    """模拟成交引擎

    配置来自 trade_interfaces.json → simulation 条目。
    """

    def __init__(self, initial_capital: float = 1_000_000.0,
                 commission_rate: float = 0.0003,
                 slippage_pct: float = 0.001,
                 default_quantity: int = 100):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.commission_rate = commission_rate
        self.slippage_pct = slippage_pct
        self.default_quantity = default_quantity

        self.positions: Dict[str, Position] = {}
        self.trade_history: List[TradeRecord] = []
        self._frozen: bool = False  # 仿真模式不允许副作用时冻结

    @classmethod
    def from_config(cls, config: dict) -> "PaperTradeEngine":
        """从 trade_interfaces.json 配置创建实例"""
        methods = config.get("methods", {})
        return cls(
            initial_capital=methods.get("initial_capital", 1_000_000.0),
            commission_rate=methods.get("commission_rate", 0.0003),
            slippage_pct=methods.get("slippage_pct", 0.001),
            default_quantity=methods.get("default_quantity", 100),
        )

    def buy(self, code: str, price: float, quantity: Optional[int] = None,
            timestamp: Optional[float] = None) -> Optional[TradeRecord]:
        """模拟买入"""
        if self._frozen:
            logger.debug("PaperTrade冻结中，跳过买入 %s", code)
            return None

        quantity = quantity or self.default_quantity
        slippage = price * self.slippage_pct
        actual_price = price + slippage
        commission = actual_price * quantity * self.commission_rate
        total_cost = actual_price * quantity + commission

        if total_cost > self.cash:
            logger.warning("资金不足，无法买入 %s: 需%.2f 可用%.2f",
                           code, total_cost, self.cash)
            return None

        self.cash -= total_cost
        ts = timestamp or time.time()
        pos = Position(
            code=code,
            entry_price=actual_price,
            entry_time=ts,
            quantity=quantity,
            current_price=price,
        )
        self.positions[code] = pos

        record = TradeRecord(
            code=code, direction="buy", price=actual_price,
            quantity=quantity, timestamp=ts,
            commission=commission, slippage=slippage,
        )
        self.trade_history.append(record)
        logger.info("模拟买入 %s ×%d @%.2f 手续费%.2f", code, quantity, actual_price, commission)
        return record

    def sell(self, code: str, price: float, quantity: Optional[int] = None,
             timestamp: Optional[float] = None) -> Optional[TradeRecord]:
        """模拟卖出"""
        if self._frozen:
            logger.debug("PaperTrade冻结中，跳过卖出 %s", code)
            return None

        pos = self.positions.get(code)
        if not pos:
            logger.warning("无持仓，无法卖出 %s", code)
            return None

        quantity = quantity or pos.quantity
        slippage = price * self.slippage_pct
        actual_price = price - slippage
        commission = actual_price * quantity * self.commission_rate
        total_income = actual_price * quantity - commission

        profit = (actual_price - pos.entry_price) * quantity - commission
        self.cash += total_income
        ts = timestamp or time.time()

        # 清仓
        if quantity >= pos.quantity:
            del self.positions[code]
        else:
            pos.quantity -= quantity

        record = TradeRecord(
            code=code, direction="sell", price=actual_price,
            quantity=quantity, timestamp=ts,
            commission=commission, slippage=slippage,
            profit=profit,
        )
        self.trade_history.append(record)
        logger.info("模拟卖出 %s ×%d @%.2f 盈亏%.2f", code, quantity, actual_price, profit)
        return record

    def update_prices(self, price_map: Dict[str, float]):
        """更新持仓的当前价格"""
        for code, price in price_map.items():
            if code in self.positions:
                self.positions[code].current_price = price

    def get_portfolio_summary(self) -> dict:
        """获取组合摘要"""
        total_market_value = sum(p.market_value for p in self.positions.values())
        total_profit = sum(p.profit for p in self.positions.values())
        return {
            "initial_capital": self.initial_capital,
            "cash": round(self.cash, 2),
            "market_value": round(total_market_value, 2),
            "total_assets": round(self.cash + total_market_value, 2),
            "total_profit": round(total_profit, 2),
            "total_profit_pct": round(
                (self.cash + total_market_value - self.initial_capital) / self.initial_capital * 100, 2
            ) if self.initial_capital > 0 else 0.0,
            "position_count": len(self.positions),
            "trade_count": len(self.trade_history),
        }

    def freeze(self):
        """冻结交易（仿真模式不允许副作用时调用）"""
        self._frozen = True

    def unfreeze(self):
        """解冻交易"""
        self._frozen = False


# ═══════════════════════════════════════════════════════════════
# 兼容别名（验证导入用）
# ═══════════════════════════════════════════════════════════════

PaperTrade = PaperTradeEngine


class HistoryManager:
    """历史记录管理器（兼容别名，原 history_manager.py 中的函数仍以模块级函数形式提供）。"""
    pass
