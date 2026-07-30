"""Trade 模块：事件驱动的交易执行 + 模拟交易 + 历史记录。

SubTask 27.5：合并 ``core/trade_executor.py`` + ``services/trading_service.py``
为统一 ``TradeModule`` 类（Trade 模块唯一入口）。仅与 EventBus 交互，内部持有
2 个组件实例（不暴露给外部）。

订阅 ``Signal`` 事件，执行买入/卖出（live_order/paper_trade/noop 三接口），
发布 ``OrderPlaced`` / ``OrderFilled`` / ``PositionUpdated`` 事件。
支持 DZH tradeattr 19 字段精细交易控制 + TDX psatt 副作用。

原 ``TradeExecutor`` / ``PaperTradeEngine`` 公共方法签名不变，合并后改为
module-level 私有（``_`` 前缀）保留在本文件内，仍可被其他模块显式 import
引用（迁移期内）。本模块不调用 ``_TradeExecutor.subscribe()``，避免与
TradeModule 的事件订阅重复触发。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime as _dt
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.event_bus import (
    _event_handler,
    AlertRaised,
    DataChanged,
    EventBus,
    EVENT_SIGNAL,
    EventLogged,
    ModeChanged,
    OrderFilled,
    OrderPlaced,
    PositionUpdated,
    Signal,
    StockChanged,
    TTLDue,
    TransferExecuted,
    is_event_bus,
)
from core.domain import ActionSpec
from core.table_engine import get_global_config_store

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 历史记录管理（原 services/trading_service.py 顶部模块级函数）
# ═══════════════════════════════════════════════════════════════

_BASE = Path(__file__).parent.parent
_CONFIG = _BASE / "config"
_TDXPOOL_DIR = _BASE / "tdxpool"
_TDX_BLOCK_DIR = _BASE / "data" / "tdx_blocks"

# DZH tradeattr 进出场字段映射（BUY/SELL 仅前缀 enter*/exit* 不同，逻辑同构）
_TRADEATTR_FIELD_MAP = {
    "BUY": ["entertradetype", "enterrate", "enterqty", "entercond", "enterdelay", "enteronce", "enterlimit", "enterretry", "enterexpire"],
    "SELL": ["exittradetype", "exitrate", "exitqty", "exitcond", "exitdelay", "exitonce", "exitlimit", "exitretry", "exitexpire"],
}
_TRADEATTR_TARGET_KEYS = ["tradetype", "price", "qty", "condition", "delay", "once", "limit", "retry", "expire"]


# Task 9.5: _load_json_cache 已删除，改为通过 ConfigStore.get_table 加载
# Task 24 (C12): 统一 get_table 防御性调用，消除 get_global_config_store() 双调用 perf smell
def _get_table(name: str) -> Dict[str, Any]:
    cs = get_global_config_store()
    return cs.get_table(name) if cs else {}

_STOCK_NAMES = {}
try:
    with open(_CONFIG / "data" / "mock_data.json", encoding="utf-8") as f:
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
    defaults_cfg = _get_table("history_schema")['write_defaults']['non_dict_defaults']
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
    schema = _get_table("history_schema")
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
    schema = _get_table("history_schema")
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
    action_cfg = _get_table("action_table")
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
# TradeExecutor：交易执行器（原 core/trade_executor.py）
# 订阅 EventBus 的 Signal 事件，仿真模式执行模拟记账。
# 实盘模式预留券商接口（_execute_real）。
# ═══════════════════════════════════════════════════════════════


# _SIDE_SPECS: BUY/SELL 共享 _execute_trade 骨架的差异项（action / cash 方向 /
# trade dict amount 键 / log 格式）。多步差异（qty/precheck/position/pnl）按 side 内联。
_SIDE_SPECS: Dict[str, Dict[str, Any]] = {
    "BUY": {"action": "BUY", "sign": -1, "amount_key": "cost",
            "log": lambda c, q, p, a, pnl, cash: logger.info("BUY %s qty=%d price=%.2f cost=%.2f cash=%.2f", c, q, p, a, cash)},
    "SELL": {"action": "SELL", "sign": 1, "amount_key": "proceeds",
             "log": lambda c, q, p, a, pnl, cash: logger.info("SELL %s qty=%d price=%.2f proceeds=%.2f pnl=%.4f cash=%.2f", c, q, p, a, pnl, cash)},
}


class _TradeExecutor:
    """交易执行器（内部组件，对外请使用 ``TradeModule``）。

    订阅 EventBus 的 Signal 事件，仿真模式执行模拟记账。
    实盘模式预留券商接口（_execute_real）。

    属性（≤ 5）:
      - bus: EventBus
      - _positions: 模拟持仓 {code: {quantity, cost_price}}
      - _cash: 模拟资金
      - _trades: 交易记录
      - _enabled: 是否已订阅
    """

    def __init__(self, bus: Optional[Any] = None, initial_cash: float = 100_000_000.0,
                 storage: Optional[Any] = None, pool_id: str = "") -> None:
        self.bus = bus
        self._storage = storage
        self._pool_id = pool_id
        self._positions: Dict[str, Dict[str, Any]] = {}
        self._cash: float = initial_cash
        self._trades: List[Dict[str, Any]] = []
        self._enabled = False

    def subscribe(self) -> None:
        if self._enabled or not is_event_bus(self.bus):
            return
        self.bus.subscribe(EVENT_SIGNAL, self.on_signal)
        self._enabled = True

    def on_signal(self, event: Any) -> None:
        if not isinstance(event, Signal):
            return
        if event.signal_type in _SIDE_SPECS:
            self._execute_trade(event.signal_type, event)

    def _execute_trade(self, side: str, signal: Signal) -> None:
        """交易执行——合并 _execute_buy / _execute_sell，按 _SIDE_SPECS 分派差异。

        共享骨架：取持仓 → 按 side 计算 qty/amount/pnl 与 position 更新 → 更新 cash →
        append trade → persist → log。BUY 加仓（均价）/扣资金；SELL 减仓/加资金+pnl。
        """
        spec = _SIDE_SPECS[side]
        pos = self._positions.get(signal.code)
        if side == "SELL" and not pos:
            return
        if side == "BUY":
            quantity = signal.quantity if signal.quantity > 0 else 100
            amount = quantity * signal.price
            if amount > self._cash:
                logger.warning("insufficient cash for BUY %s: cost=%.2f cash=%.2f",
                               signal.code, amount, self._cash)
                return
            if pos:
                total_qty = pos["quantity"] + quantity
                pos["cost_price"] = round((pos["quantity"] * pos["cost_price"] + amount) / total_qty, 4)
                pos["quantity"] = total_qty
            else:
                self._positions[signal.code] = {"quantity": quantity, "cost_price": round(signal.price, 4)}
            pnl = None
        else:
            quantity = pos["quantity"] if signal.quantity <= 0 else min(signal.quantity, pos["quantity"])
            amount = quantity * signal.price
            pnl = (signal.price - pos["cost_price"]) * quantity
            remaining = pos["quantity"] - quantity
            if remaining <= 0:
                del self._positions[signal.code]
            else:
                pos["quantity"] = remaining
        self._cash += spec["sign"] * amount
        trade = {"action": spec["action"], "code": signal.code, "quantity": quantity,
                 "price": signal.price, spec["amount_key"]: amount}
        if side == "SELL":
            trade["pnl"] = round(pnl, 4)
        self._trades.append(trade)
        self._persist_trade(signal, spec["action"], quantity, signal.price, amount, pnl)
        spec["log"](signal.code, quantity, signal.price, amount, pnl, self._cash)

    def _persist_trade(self, signal: Signal, action: str, quantity: int,
                       price: float, amount: float, pnl: Optional[float]) -> None:
        """持久化交易记录到 storage。storage 不存在时静默跳过（兼容测试环境）。"""
        if self._storage is None:
            return
        try:
            if hasattr(self._storage, "log_trade"):
                self._storage.log_trade(
                    pool_id=self._pool_id or signal.pool_id,
                    node_id=signal.pool_id,
                    stock_code=signal.code,
                    action=action,
                    quantity=quantity,
                    price=price,
                    amount=amount,
                    cash_after=self._cash,
                    pnl=pnl,
                    condition=signal.condition,
                )
        except Exception as ex:
            logger.warning("trade_log 写入失败: %s", ex)

    def get_position(self, code: str) -> Optional[Dict[str, Any]]:
        return self._positions.get(code)

    def get_all_positions(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._positions)

    def get_cash(self) -> float:
        return self._cash

    def get_trades(self) -> List[Dict[str, Any]]:
        return list(self._trades)


# ═══════════════════════════════════════════════════════════════
# 模拟交易（原 services/trading_service.py 中的 PaperTradeEngine）
# PaperTradeEngine — 仿真模式模拟成交引擎
#
# 由 engine 在 post_tick 后按 trade_interfaces.json 配置调用。
# 维护虚拟持仓、资金、盈亏、手续费、滑点。
# ═══════════════════════════════════════════════════════════════


@dataclass
class _Position:
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
class _TradeRecord:
    """成交记录"""
    code: str
    direction: str  # "buy" / "sell"
    price: float
    quantity: int
    timestamp: float
    commission: float = 0.0
    slippage: float = 0.0
    profit: float = 0.0  # 仅sell时有值


# _PAPER_SIDE_SPECS: buy/sell 共享 _paper_trade 骨架的差异项（sign / direction / log）。
# sign=+1 买入（slippage 加价 / 成本含手续费 / cash 减），sign=-1 卖出（slippage 减价 /
# 收入扣手续费 / cash 加）；cash_sign = -sign。多步差异（qty/precheck/position/profit）按 side 内联。
_PAPER_SIDE_SPECS: Dict[str, Dict[str, Any]] = {
    "buy": {"sign": 1, "direction": "buy",
            "log": lambda c, q, ap, comm, prof: logger.info("模拟买入 %s ×%d @%.2f 手续费%.2f", c, q, ap, comm)},
    "sell": {"sign": -1, "direction": "sell",
             "log": lambda c, q, ap, comm, prof: logger.info("模拟卖出 %s ×%d @%.2f 盈亏%.2f", c, q, ap, prof)},
}


class _PaperTradeEngine:
    """模拟成交引擎（内部组件，对外请使用 ``TradeModule``）。

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

        self.positions: Dict[str, _Position] = {}
        self.trade_history: List[_TradeRecord] = []
        self._frozen: bool = False  # 仿真模式不允许副作用时冻结

    @classmethod
    def from_config(cls, config: dict) -> "_PaperTradeEngine":
        """从 trade_interfaces.json 配置创建实例"""
        methods = config.get("methods", {})
        return cls(
            initial_capital=methods.get("initial_capital", 1_000_000.0),
            commission_rate=methods.get("commission_rate", 0.0003),
            slippage_pct=methods.get("slippage_pct", 0.001),
            default_quantity=methods.get("default_quantity", 100),
        )

    def _paper_trade(self, side: str, code: str, price: float,
                     quantity: Optional[int] = None,
                     timestamp: Optional[float] = None) -> Optional[_TradeRecord]:
        """模拟交易——合并 buy/sell，按 _PAPER_SIDE_SPECS 分派差异。

        共享骨架：frozen 检查 → 查表得 sign/direction → slippage 按 sign 偏移 →
        commission → 资金/持仓校验 → 更新 cash + positions → 记录 + log。
        BUY 加仓/扣资金；SELL 减仓/加资金+profit。
        """
        spec = _PAPER_SIDE_SPECS[side]
        sign = spec["sign"]
        if self._frozen:
            logger.debug("PaperTrade冻结中，跳过%s %s", side, code)
            return None
        pos = self.positions.get(code)
        if sign < 0 and not pos:
            logger.warning("无持仓，无法卖出 %s", code)
            return None
        quantity = quantity or (self.default_quantity if sign > 0 else pos.quantity)
        slippage = price * self.slippage_pct
        actual_price = price + sign * slippage
        commission = actual_price * quantity * self.commission_rate
        profit = 0.0
        if sign > 0:
            total_cost = actual_price * quantity + commission
            if total_cost > self.cash:
                logger.warning("资金不足，无法买入 %s: 需%.2f 可用%.2f",
                               code, total_cost, self.cash)
                return None
            self.cash -= total_cost
            ts = timestamp or time.time()
            self.positions[code] = _Position(
                code=code, entry_price=actual_price, entry_time=ts,
                quantity=quantity, current_price=price,
            )
        else:
            profit = (actual_price - pos.entry_price) * quantity - commission
            self.cash += actual_price * quantity - commission
            ts = timestamp or time.time()
            if quantity >= pos.quantity:
                del self.positions[code]
            else:
                pos.quantity -= quantity
        record = _TradeRecord(
            code=code, direction=spec["direction"], price=actual_price,
            quantity=quantity, timestamp=ts,
            commission=commission, slippage=slippage, profit=profit,
        )
        self.trade_history.append(record)
        spec["log"](code, quantity, actual_price, commission, profit)
        return record

    def buy(self, code: str, price: float, quantity: Optional[int] = None,
            timestamp: Optional[float] = None) -> Optional[_TradeRecord]:
        """模拟买入（thin wrapper 委托 _paper_trade）。"""
        return self._paper_trade("buy", code, price, quantity, timestamp)

    def sell(self, code: str, price: float, quantity: Optional[int] = None,
             timestamp: Optional[float] = None) -> Optional[_TradeRecord]:
        """模拟卖出（thin wrapper 委托 _paper_trade）。"""
        return self._paper_trade("sell", code, price, quantity, timestamp)

    def update_prices(self, price_map: Dict[str, float]):
        """更新持仓的当前价格"""
        for code, price in price_map.items():
            if code in self.positions:
                self.positions[code].current_price = price

    def get_position(self, code: str) -> Optional[Dict[str, Any]]:
        """获取持仓信息（兼容接口，返回dict格式）"""
        pos = self.positions.get(code)
        if pos is None:
            return None
        return {
            "code": pos.code,
            "quantity": pos.quantity,
            "qty": pos.quantity,
            "entry_price": pos.entry_price,
            "cost_price": pos.entry_price,
            "current_price": pos.current_price,
            "entry_time": pos.entry_time,
        }

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


# 兼容别名（验证导入用）
_PaperTrade = _PaperTradeEngine


class _HistoryManager:
    """历史记录管理器（兼容别名，原 history_manager.py 中的函数仍以模块级函数形式提供）。"""
    pass


# _PSATT_SIDE_EFFECTS: 5 个 psatt 副作用共享 _apply_psatt_side_effects 骨架的差异项
# (flag_attr, event_factory)。flag_attr 为 ActionSpec 开关属性；event_factory 接收
# (action_spec, codes) 返回待 publish 事件列表。bsavehis 按 code 逐条发，其余单条。
_PSATT_SIDE_EFFECTS: List[Tuple[str, Callable[[ActionSpec, List[str]], List[Any]]]] = [
    ("bsavehis", lambda a, codes: [EventLogged(event={"action": "save_history", "code": c}, event_kind="psatt_bsavehis") for c in codes]),
    ("bsound", lambda a, codes: [EventLogged(event={"action": "play_sound", "soundfile": a.soundfile, "nsoundtype": a.nsoundtype}, event_kind="psatt_bsound")]),
    ("btip", lambda a, codes: [AlertRaised(alert={"rule_id": "psatt_btip", "code": ",".join(codes), "severity": "info", "message": "TDX tip"})]),
    ("bsavetoblock", lambda a, codes: [EventLogged(event={"action": "save_to_block", "blockfile": a.blockfile, "bclearblock": int(bool(a.bclearblock)), "codes": list(codes)}, event_kind="psatt_bsavetoblock")]),
    ("baimpool", lambda a, codes: [EventLogged(event={"action": "aim_pool", "codes": list(codes)}, event_kind="psatt_baimpool")]),
]


# ═══════════════════════════════════════════════════════════════
# TradeModule：对外统一入口（原 core/trade_module.py）
# ═══════════════════════════════════════════════════════════════


class TradeModule:
    """Trade 模块：交易执行 + 模拟交易 + 历史记录。仅与 EventBus 交互。

    订阅 Signal 事件，执行买入/卖出（live_order/paper_trade/noop 三接口），
    发布 OrderPlaced/OrderFilled/PositionUpdated 事件。
    支持 DZH tradeattr 19 字段精细交易控制 + TDX psatt 副作用。

    属性（实例级，≤ 5）:
      - _bus: EventBus
      - _config: 配置 dict
      - _trade_executor: _TradeExecutor 实例（仿真记账，向后兼容）
      - _trading_service: _PaperTradeEngine 实例（模拟成交引擎）
      - _trackers: 持仓跟踪表 {(pool_id, code): tracker}
    """

    # 表驱动：交易接口类型 → handler 方法名（无 if/elif 链）
    _INTERFACE_HANDLERS: Dict[str, str] = {
        "live_order": "_live_execute",
        "paper_trade": "_paper_execute",
        "noop": "_noop_execute",
    }

    def __init__(self, bus: EventBus, config: Optional[Dict[str, Any]] = None) -> None:
        self._bus = bus
        self._config = config or {}
        # 持有原 2 个组件实例（不暴露给外部）
        self._trade_executor = _TradeExecutor(
            bus,
            storage=self._config.get("storage"),
            pool_id=self._config.get("pool_id", ""),
            initial_cash=self._config.get("initial_cash", 100_000_000.0),
        )
        self._trading_service = _PaperTradeEngine(
            initial_capital=self._config.get("initial_capital", 1_000_000.0),
            commission_rate=self._config.get("commission_rate", 0.0003),
            slippage_pct=self._config.get("slippage_pct", 0.001),
            default_quantity=self._config.get("default_quantity", 100),
        )
        # 交易接口类型（live_order/paper_trade/noop）
        self._interface_type = self._config.get("trade_interface", "paper_trade")
        # 持仓跟踪表（更新 StockTracker）
        self._trackers: Dict[Tuple[str, str], Dict[str, Any]] = {}
        # 最新价格缓存（code -> price），从DataChanged(tick)事件更新
        self._latest_prices: Dict[str, float] = {}
        # 注册事件订阅
        self._register_subscribers()

    def _register_subscribers(self) -> None:
        self._bus.subscribe(Signal, self._on_signal)
        self._bus.subscribe(OrderPlaced, self._on_order_placed)
        # SubTask 19.7：订阅 OrderFilled 触发 psatt 副作用
        self._bus.subscribe(OrderFilled, self._on_order_filled)
        # SubTask 20.3：订阅 ModeChanged 切换交易接口
        self._bus.subscribe(ModeChanged, self._on_mode_changed)
        # SubTask 21.4：订阅 TransferExecuted 支持入池即买入/出池即卖出语义
        self._bus.subscribe(TransferExecuted, self._on_transfer_executed)
        # 仿真链路修复：订阅DataChanged(tick)更新最新价缓存
        self._bus.subscribe(DataChanged, self._on_data_changed)
        # G2：订阅 TTLDue 事件，TradeModule 自行处理 TTL 到期卖出/删除
        self._bus.subscribe(TTLDue, self._on_ttl_due)

    # ------------------------------------------------------------------
    # SubTask 20.3：ModeChanged → 切换交易接口类型
    # ------------------------------------------------------------------
    @_event_handler("_on_mode_changed")
    def _on_mode_changed(self, event: ModeChanged) -> None:
        """模式切换时切换 ``self._interface_type`` 并 freeze/unfreeze paper_trade。

        三种模式与交易接口映射：
          - ``live``:        ``live_order``（委托真实券商接口下单）→ unfreeze
          - ``replay``:      ``noop``（仅记录日志，不实际下单；回放历史数据
                              不应触发真实交易）→ freeze paper_trade
          - ``simulation``:  ``paper_trade``（PaperTradeEngine 模拟成交记账）→ unfreeze

        ``_INTERFACE_HANDLERS`` 表驱动分派保持不变，仅切换 ``_interface_type``
        即可在下次 ``_on_signal`` 时按新模式分派 handler。

        仿真模式 side_effects_scope="simulation" 允许 paper_order，
        所以 simulation 模式下 unfreeze _trading_service。
        """
        mode_id = event.mode_id or "live"
        mapping = {
            "live": "live_order",
            "replay": "noop",
            "simulation": "paper_trade",
        }
        new_iface = mapping.get(mode_id, "paper_trade")
        prev = self._interface_type
        self._interface_type = new_iface

        if hasattr(self._trading_service, "freeze") and hasattr(self._trading_service, "unfreeze"):
            if mode_id == "replay":
                self._trading_service.freeze()
                logger.info("TradeModule: replay模式，冻结paper_trade")
            else:
                self._trading_service.unfreeze()
                logger.info("TradeModule: %s模式，解冻paper_trade", mode_id)

        logger.info(
            "TradeModule 交易接口切换: %s -> %s（mode=%s）",
            prev, new_iface, mode_id,
        )

    # === SubTask 9.2：订阅 Signal 事件执行买卖 ===

    @_event_handler("_on_data_changed")
    def _on_data_changed(self, event: DataChanged) -> None:
        """DataChanged事件处理：更新最新价缓存 + paper_trade持仓价格。

        只处理source=tick的事件，支持单 tick dict 或 {code: tick_dict} 批量格式，
        从tick中提取close/price/lastprice字段更新缓存，
        同时更新_PaperTradeEngine中的持仓当前价格。
        兼容 fz 前缀仿真代码（8字符）与真实6位代码。
        """
        if event.source != "tick":
            return
        data = event.data or {}
        if not isinstance(data, dict):
            return

        # 支持两种数据格式：单 tick dict 或 {code: tick_dict} 批量
        if "code" in data:
            ticks = [data]
        else:
            ticks = []
            for code, tick in data.items():
                if isinstance(tick, dict):
                    tick_copy = dict(tick)
                    tick_copy["code"] = code
                    ticks.append(tick_copy)
                else:
                    ticks.append({"code": code, "price": tick})

        for tick in ticks:
            code = str(tick.get("code", "")).strip()
            if not code:
                continue
            price = float(
                tick.get("close", tick.get("price", tick.get("lastprice", 0.0)))
                or 0.0
            )
            if price > 0:
                self._latest_prices[code] = price
                if hasattr(self._trading_service, "update_prices"):
                    self._trading_service.update_prices({code: price})

    @_event_handler("_on_ttl_due")
    def _on_ttl_due(self, event: TTLDue) -> None:
        """TTLDue 事件处理：对 auto_sell_pools 中的池发布 SELL Signal（G2）。

        G2：引擎只发事件不执行计算，TTL 到期时 EventDriver action 仅发布 TTLDue，
        TradeModule 订阅后自行完成卖出逻辑。无持仓时仍发布 Signal(sell_all)
       （quantity=0），由下游 _on_signal 优雅降级。
        """
        auto_sell_pools = self._config.get("auto_sell_pools", []) or []
        if event.node_id not in auto_sell_pools:
            return
        code = event.code
        if not code:
            return
        qty = self._get_position_qty(code, event.node_id)
        price = self._get_latest_price(code, fallback=0.0)
        # 无持仓时仍发布 Signal(sell_all)（quantity=0），
        # 不在此跳过 — 下游 _on_signal 负责优雅降级。
        self._bus.publish(Signal(
            signal_type="SELL", code=code, pool_id=event.node_id,
            price=price, ts=event.ts, quantity=qty,
        ))

    def _get_latest_price(self, code: str, fallback: float = 0.0) -> float:
        """获取股票最新价，优先从_latest_prices缓存，无缓存返回fallback。"""
        price = self._latest_prices.get(code, 0.0)
        return price if price > 0 else fallback

    def _get_position_qty(self, code: str, pool_id: str = "") -> int:
        """获取股票当前持仓数量（用于SELL quantity=0时卖全部）。
        
        优先从_PaperTradeEngine获取持仓，兼容_trade_executor和_tracker。
        """
        try:
            if hasattr(self._trading_service, "get_position"):
                pos = self._trading_service.get_position(code)
                if pos:
                    qty = int(pos.get("qty", pos.get("quantity", 0)) or 0)
                    if qty > 0:
                        return qty
            if hasattr(self._trading_service, "positions"):
                pos = self._trading_service.positions.get(code)
                if pos is not None and hasattr(pos, "quantity"):
                    return int(pos.quantity)
            if hasattr(self._trade_executor, "get_position"):
                pos = self._trade_executor.get_position(code)
                if pos:
                    return int(pos.get("quantity", 0) or 0)
        except Exception:
            pass
        key = (pool_id, code)
        tracker = self._trackers.get(key, {})
        return int(tracker.get("qty", 0) or 0)

    @_event_handler("_on_signal")
    def _on_signal(self, event: Signal) -> None:
        """收到交易信号，执行买入/卖出。

        仿真链路修复：
        - price=0（市价单）时取最新tick价格
        - SELL quantity<=0时卖全部持仓
        - spec L142-143: SELL 无持仓时发布 rejected OrderPlaced（quantity=0，
          不实际下单），而非静默跳过 — 确保 Signal 已发出的下游可观测。
        """
        code = event.code
        side = event.signal_type.upper()
        price = float(event.price or 0.0)
        qty = int(event.quantity or 0)

        if price <= 0:
            price = self._get_latest_price(code, fallback=price)

        if side == "SELL" and qty <= 0:
            qty = self._get_position_qty(code, event.pool_id)
            if qty <= 0:
                # spec L142-143: Signal(sell_all) 已发出，
                # OrderPlaced 失败或为空（quantity=0, status=rejected 不实际下单）
                logger.info(
                    "TradeModule SELL信号无持仓：%s（pool=%s）发布 rejected OrderPlaced",
                    code, event.pool_id,
                )
                self._bus.publish(OrderPlaced(
                    order={
                        "code": code, "side": "SELL", "qty": 0,
                        "price": price, "pool_id": event.pool_id,
                        "order_type": "market", "condition": event.condition,
                        "status": "rejected", "reason": "no_position",
                        "ts": event.ts,
                    },
                    ts=event.ts,
                ))
                return
        elif side == "BUY" and qty <= 0:
            qty = 100

        order: Dict[str, Any] = {
            "code": code,
            "side": side,
            "qty": qty,
            "price": price,
            "order_type": "market" if price <= 0 else "limit",
            "pool_id": event.pool_id,
            "condition": event.condition,
            "ts": event.ts,
        }
        # 应用 DZH tradeattr 19 字段精细交易控制（如配置提供）
        tradeattr = self._config.get("tradeattr")
        if tradeattr:
            order = self._apply_tradeattr(order, tradeattr)
        # SubTask 19.7：携带 psatt 配置到 order，供 _on_order_filled 构造 ActionSpec
        psatt = self._config.get("psatt")
        if psatt:
            order["psatt"] = psatt
        # 按交易接口分派（表驱动，无 if/elif）
        handler_name = self._INTERFACE_HANDLERS.get(
            self._interface_type, "_noop_execute"
        )
        handler = getattr(self, handler_name, self._noop_execute)
        result = handler(order)
        if result:
            self._bus.publish(OrderPlaced(order=order, ts=event.ts))

    def _live_execute(self, order: Dict[str, Any]) -> bool:
        """实盘下单（委托 TradingService 调用真实券商接口）。

        真实券商适配器尚未接入时，降级为占位接受（流程继续，发布 OrderPlaced）。
        """
        place = getattr(self._trading_service, "place_order", None)
        if callable(place):
            try:
                return bool(place(order))
            except Exception as ex:
                logger.warning("TradeModule live_order place failed: %s", ex)
                return False
        logger.warning(
            "TradeModule live_order: 真实券商适配器未配置，降级为占位接受 %s %s",
            order.get("side"), order.get("code"),
        )
        return True

    def _paper_execute(self, order: Dict[str, Any]) -> bool:
        """模拟交易（委托 PaperTradeEngine 更新虚拟持仓/资金，返回成交）。"""
        code = order.get("code", "")
        price = float(order.get("price", 0.0) or 0.0)
        qty = int(order.get("qty", 100) or 100)
        ts = order.get("ts")
        side = str(order.get("side", "")).upper()
        try:
            if price <= 0:
                price = self._get_latest_price(code, fallback=price)
            if price <= 0:
                logger.warning(
                    "TradeModule paper_trade: %s %s 无有效最新价，跳过",
                    side, code,
                )
                return False
            if side == "BUY":
                rec = self._trading_service.buy(code, price, qty, ts)
            elif side == "SELL":
                rec = self._trading_service.sell(code, price, qty, ts)
            else:
                logger.warning("TradeModule paper_trade: unknown side %s", side)
                return False
            return rec is not None
        except Exception as ex:
            logger.warning("TradeModule paper_trade failed: %s", ex)
            return False

    def _noop_execute(self, order: Dict[str, Any]) -> bool:
        """空操作（仅记录日志，不实际下单）。"""
        logger.info(
            "TradeModule noop: %s %s %d",
            order.get("side"), order.get("code"), order.get("qty", 0),
        )
        return True

    # === SubTask 9.3：订单成交后发布 OrderFilled + PositionUpdated ===

    @_event_handler("_on_order_placed")
    def _on_order_placed(self, event: OrderPlaced) -> None:
        """订单提交后，模拟成交（实盘模式下需等待真实成交回报）。"""
        order = event.order
        # spec L143: rejected 订单不实际成交（OrderPlaced 失败或为空）
        if order.get("status") == "rejected":
            logger.info(
                "TradeModule OrderPlaced rejected: %s %s (qty=0, reason=%s)",
                order.get("side"), order.get("code"), order.get("reason", ""),
            )
            return
        if self._interface_type == "live_order":
            # 实盘模式：等待真实成交回报（适配器未接入时简化为立即成交）
            wait = getattr(self._trading_service, "wait_fill", None)
            fill = wait(order) if callable(wait) else self._immediate_fill(order, event.ts)
        else:
            # 模拟/空操作模式：立即生成成交
            fill = self._immediate_fill(order, event.ts)
        # SubTask 19.7：将 psatt 从 order 透传到 fill，供 _on_order_filled 使用
        psatt = order.get("psatt")
        if psatt:
            fill = dict(fill)
            fill["psatt"] = psatt
        self._bus.publish(OrderFilled(fill=fill, ts=event.ts))
        tracker = self._update_tracker(order, fill)
        self._bus.publish(PositionUpdated(tracker=tracker, ts=event.ts))

    @_event_handler("_on_order_filled")
    def _on_order_filled(self, event: OrderFilled) -> None:
        """成交后触发 TDX psatt 副作用（SubTask 19.7）。

        ``_apply_psatt_side_effects`` 原为公开方法但从未被调用，导致
        bsavehis/bsound/btip/bsavetoblock/baimpool 五种副作用链路断裂。
        本 handler 订阅 OrderFilled 事件，从 fill 中提取 psatt 配置，
        构造 ActionSpec 并调用 ``_apply_psatt_side_effects`` 补全链路。
        若无 psatt 配置则跳过（向后兼容）。
        """
        fill = event.fill or {}
        psatt = fill.get("psatt")
        if not psatt:
            return
        code = fill.get("code", "")
        action_spec = ActionSpec.from_dict(psatt)
        self._apply_psatt_side_effects(action_spec, [code] if code else [])

    # === SubTask 21.4：订阅 TransferExecuted 事件，支持入池即买入/出池即卖出 ===

    @_event_handler("_on_transfer_executed")
    def _on_transfer_executed(self, event: TransferExecuted) -> None:
        """转移执行完成 handler：支持入池即买入 / 出池即卖出语义。

        - ``event.mode == "move"`` 且 ``event.tgt`` 在 ``auto_buy_pools`` 配置中：
          发布 BUY Signal（qty=100）。
        - ``event.src`` 在 ``auto_sell_pools`` 配置中：
          查持仓 tracker 发布 SELL Signal（qty=当前持仓量）。
        配置键从 ``self._config`` 读取（``auto_buy_pools`` / ``auto_sell_pools``），
        未配置时为空列表，handler 退化为仅记录日志。
        """
        auto_buy_pools = self._config.get("auto_buy_pools", []) or []
        auto_sell_pools = self._config.get("auto_sell_pools", []) or []
        # 入池即买入：股票进入 auto_buy_pools 目标池时发布 BUY Signal。
        # Spec: C 池入池买入链 TransferExecuted → Signal(buy,100)。
        # 适用 copy/move/overwrite 全模式——股票进入目标池即触发买入，
        # 与是否离开源池无关（copy 模式源池保留但目标池也入池）。
        if event.tgt in auto_buy_pools and event.codes:
            for code in event.codes:
                self._bus.publish(Signal(
                    signal_type="BUY", code=code, pool_id=event.tgt,
                    price=0.0, ts=event.ts, quantity=100,
                ))
        # 出池即卖出：仅 move/overwrite 模式（股票实际离开源池）时，
        # 对 auto_sell_pools 源池发布 SELL Signal。copy 模式股票未离开源池不卖。
        # 使用 exited_codes（实际离开源池的代码），而非 entered_codes。
        if event.mode in ("move", "overwrite") and event.src in auto_sell_pools:
            exited_codes = getattr(event, "exited_codes", None) or event.codes
            for code in exited_codes:
                key = (event.src, code)
                tracker = self._trackers.get(key, {})
                qty = int(tracker.get("qty", 0) or 0)
                if qty > 0:
                    self._bus.publish(Signal(
                        signal_type="SELL", code=code, pool_id=event.src,
                        price=float(tracker.get("cur_price", 0.0) or 0.0),
                        ts=event.ts, quantity=qty,
                    ))

    def _immediate_fill(self, order: Dict[str, Any], ts: float) -> Dict[str, Any]:
        """立即生成成交回报（模拟/空操作模式）。

        仿真链路修复：price为0时取_latest_prices中的最新价。
        """
        code = order.get("code", "")
        price = float(order.get("price", 0.0) or 0.0)
        if price <= 0:
            price = self._get_latest_price(code, fallback=price)
        return {
            "code": code,
            "side": order.get("side", ""),
            "qty": order.get("qty", 0),
            "price": price,
            "order_id": order.get("order_id", ""),
            "fill_ts": ts,
        }

    def _update_tracker(self, order: Dict[str, Any], fill: Dict[str, Any]) -> Dict[str, Any]:
        """更新持仓跟踪表，返回最新 tracker 快照。"""
        key = (order.get("pool_id", ""), order.get("code", ""))
        prev = self._trackers.get(key, {"qty": 0, "entry_price": 0.0, "cur_price": 0.0})
        side = str(order.get("side", "")).upper()
        fill_qty = int(fill.get("qty", 0) or 0)
        fill_price = float(fill.get("price", 0.0) or 0.0)
        if side == "BUY":
            # 买入：增加持仓，按加权平均更新成本
            new_qty = prev["qty"] + fill_qty
            new_entry = (
                (prev["entry_price"] * prev["qty"] + fill_price * fill_qty) / new_qty
                if new_qty > 0 else 0.0
            )
            tracker = {
                "node_id": order.get("pool_id", ""),
                "code": order.get("code", ""),
                "entry_price": round(new_entry, 4),
                "cur_price": fill_price,
                "qty": new_qty,
                "pnl": 0.0,
            }
        else:
            # 卖出：减少持仓，按成本价计算已实现盈亏
            new_qty = max(0, prev["qty"] - fill_qty)
            pnl = (fill_price - prev["entry_price"]) * fill_qty
            tracker = {
                "node_id": order.get("pool_id", ""),
                "code": order.get("code", ""),
                "entry_price": prev["entry_price"] if new_qty > 0 else 0.0,
                "cur_price": fill_price,
                "qty": new_qty,
                "pnl": round(pnl, 4),
            }
        self._trackers[key] = tracker
        return tracker

    # === SubTask 9.4：DZH tradeattr 19 字段 + TDX psatt 副作用 ===

    def _apply_tradeattr(self, order: Dict[str, Any], tradeattr: Dict[str, Any]) -> Dict[str, Any]:
        """应用 DZH tradeattr 19 字段精细交易控制。

        19 字段（从 ActionSpec.tradeattr 读取）:
          accountno / entertradetype / enterrate / exittradetype / exitrate /
          enterqty / exitqty / entercond / exitcond / enterdelay / exitdelay /
          enteronce / exitonce / enterlimit / exitlimit / enterretry / exitretry /
          enterexpire / exitexpire
        """
        enriched = dict(order)
        side = str(order.get("side", "")).upper()
        # 账户号
        if tradeattr.get("accountno"):
            enriched["accountno"] = tradeattr.get("accountno")
        # 进出场类型与限价：0=市价, 1=限价（BUY/SELL 同构，仅字段前缀 enter*/exit* 不同）
        fields = _TRADEATTR_FIELD_MAP.get(side, [])
        for src_field, target_key in zip(fields, _TRADEATTR_TARGET_KEYS):
            val = tradeattr.get(src_field)
            if target_key == "tradetype":
                if int(val or 0) == 1:
                    enriched["order_type"] = "limit"
            elif target_key == "price":
                if int(tradeattr.get(fields[0], 0) or 0) == 1:
                    enriched["price"] = float(
                        tradeattr.get(src_field, order.get("price", 0.0)) or 0.0
                    )
            elif target_key == "once":
                if val:
                    enriched["once"] = True
            elif target_key == "condition":
                if val:
                    enriched[target_key] = str(val)
            else:  # qty / delay / limit / retry / expire
                if val:
                    enriched[target_key] = int(val)
        return enriched

    def _apply_psatt_side_effects(
        self, action_spec: ActionSpec, codes: List[str]
    ) -> None:
        """应用 TDX psatt 副作用——迭代 _PSATT_SIDE_EFFECTS 表逐项查 flag 分派。

        通过 EventLogged / AlertRaised 事件通知下游模块（Database/Monitoring）处理，
        本模块不直接执行文件 IO 或播放声音，保持事件驱动纯度。
        """
        if not action_spec:
            return
        codes = codes or []
        for flag_attr, factory in _PSATT_SIDE_EFFECTS:
            if getattr(action_spec, flag_attr, False):
                for ev in factory(action_spec, codes):
                    self._bus.publish(ev)


# === Task 13/14: Three-layer orthogonal architecture (Event / Signal / Action) ===


class SignalDeriver:
    """Derives BUY/SELL signals from StockChanged events based on node role.

    Task 13 (Signal layer): reuses event_bus.Signal (field signal_type,
    not spec draft kind) to stay aligned with existing EventBus Signal
    subscription chain (TradeModule etc.). Decoupled from ActionDispatcher -
    this layer only derives event->signal, executes no side effects.
    """

    def __init__(self, event_bus, node_roles_config: dict):
        self._bus = event_bus
        self._roles = node_roles_config
        self._bus.subscribe(StockChanged, self._on_stock_changed)

    def _on_stock_changed(self, event: StockChanged):
        """Derive signal from stock change based on node role."""
        node_id = event.node_id
        role = self._get_node_role(node_id)
        role_config = self._roles.get(role, {})
        if event.action == "enter":
            actions = role_config.get("on_enter", [])
            if "publish_buy_signal" in actions:
                self._bus.publish(Signal(signal_type="BUY", code=event.code, pool_id=node_id, price=0.0, ts=event.ts))
        elif event.action == "exit":
            actions = role_config.get("on_exit", [])
            if "publish_sell_signal" in actions:
                self._bus.publish(Signal(signal_type="SELL", code=event.code, pool_id=node_id, price=0.0, ts=event.ts))

    def _get_node_role(self, node_id: str) -> str:
        """Get node role - to be connected to CompiledPool.node_role."""
        return "state"  # Default, will be overridden


class ActionDispatcher:
    """Dispatches actions from Signal events based on action_table.json.

    Task 14 (Action layer): reuses event_bus.Signal (field signal_type,
    not spec draft kind). Decoupled from SignalDeriver - this layer only
    executes signal->action, does not care how signals are derived.
    """

    def __init__(self, event_bus, action_table: dict):
        self._bus = event_bus
        self._action_table = action_table
        self._bus.subscribe(Signal, self._on_signal)

    def _on_signal(self, signal: Signal):
        """Execute actions for signal."""
        actions = self._action_table.get(signal.signal_type, [])
        for action_name in actions:
            self._execute_action(action_name, signal)

    def _execute_action(self, action_name: str, signal: Signal):
        """Execute a single action by name."""
        _ACTION_FNS = {
            "play_sound": lambda s: print(f"[SOUND] {s.signal_type} {s.code}"),
            "show_popup": lambda s: print(f"[POPUP] {s.signal_type} {s.code}"),
            "save_history": lambda s: print(f"[HISTORY] {s.signal_type} {s.code}"),
            "update_tdx_board": lambda s: print(f"[TDX] {s.signal_type} {s.code}"),
        }
        fn = _ACTION_FNS.get(action_name)
        if fn:
            fn(signal)


__all__ = ["TradeModule", "SignalDeriver", "ActionDispatcher"]
