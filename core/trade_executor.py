"""TradeExecutor：交易执行器，消费 Signal 事件，仿真模式执行模拟记账。"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .event_bus import EVENT_SIGNAL, Signal, is_event_bus

logger = logging.getLogger(__name__)


class TradeExecutor:
    """交易执行器。

    订阅 EventBus 的 Signal 事件，仿真模式执行模拟记账。
    实盘模式预留券商接口（_execute_real）。

    属性（≤ 5）:
      - bus: EventBus
      - _positions: 模拟持仓 {code: {quantity, cost_price}}
      - _cash: 模拟资金
      - _trades: 交易记录
      - _enabled: 是否已订阅
    """

    def __init__(self, bus: Optional[Any] = None, initial_cash: float = 1_000_000.0) -> None:
        self.bus = bus
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
        if event.signal_type == "BUY":
            self._execute_buy(event)
        elif event.signal_type == "SELL":
            self._execute_sell(event)

    def _execute_buy(self, signal: Signal) -> None:
        quantity = signal.quantity if signal.quantity > 0 else 100
        price = signal.price
        cost = quantity * price
        if cost > self._cash:
            logger.warning("insufficient cash for BUY %s: cost=%.2f cash=%.2f",
                           signal.code, cost, self._cash)
            return
        pos = self._positions.get(signal.code)
        if pos:
            total_qty = pos["quantity"] + quantity
            avg_cost = (pos["quantity"] * pos["cost_price"] + cost) / total_qty
            pos["quantity"] = total_qty
            pos["cost_price"] = round(avg_cost, 4)
        else:
            self._positions[signal.code] = {
                "quantity": quantity,
                "cost_price": round(price, 4),
            }
        self._cash -= cost
        self._trades.append({
            "action": "BUY",
            "code": signal.code,
            "quantity": quantity,
            "price": price,
            "cost": cost,
        })
        logger.info("BUY %s qty=%d price=%.2f cost=%.2f cash=%.2f",
                     signal.code, quantity, price, cost, self._cash)

    def _execute_sell(self, signal: Signal) -> None:
        pos = self._positions.get(signal.code)
        if not pos:
            return
        quantity = pos["quantity"] if signal.quantity <= 0 else min(signal.quantity, pos["quantity"])
        price = signal.price
        proceeds = quantity * price
        pnl = (price - pos["cost_price"]) * quantity
        remaining = pos["quantity"] - quantity
        if remaining <= 0:
            del self._positions[signal.code]
        else:
            pos["quantity"] = remaining
        self._cash += proceeds
        self._trades.append({
            "action": "SELL",
            "code": signal.code,
            "quantity": quantity,
            "price": price,
            "proceeds": proceeds,
            "pnl": round(pnl, 4),
        })
        logger.info("SELL %s qty=%d price=%.2f proceeds=%.2f pnl=%.4f cash=%.2f",
                     signal.code, quantity, price, proceeds, pnl, self._cash)

    def get_position(self, code: str) -> Optional[Dict[str, Any]]:
        return self._positions.get(code)

    def get_all_positions(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._positions)

    def get_cash(self) -> float:
        return self._cash

    def get_trades(self) -> List[Dict[str, Any]]:
        return list(self._trades)


__all__ = ["TradeExecutor"]
