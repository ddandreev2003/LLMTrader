import csv
from pathlib import Path

import numpy as np

from core.event_bus import Event, EventType


class Portfolio:
    """Отслеживает позиции, PnL и историю сделок."""

    def __init__(self, bus, initial_cash: float = 10_000.0, export_path: str = "trades_history.csv"):
        self.bus = bus
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.position = 0
        self.trades: list[dict] = []
        self._equity_curve: list[float] = []
        self.export_path = export_path

    def portfolio_value(self, current_price: float) -> float:
        return self.cash + self.position * current_price

    def total_pnl(self, current_price: float) -> float:
        return self.portfolio_value(current_price) - self.initial_cash

    async def on_order_filled(self, event: Event):
        action = event.payload.get("action")
        qty = int(event.payload.get("quantity", 0))
        price = float(event.payload.get("price", 0))
        tick = event.payload.get("tick", 0)

        if action == "buy" and qty > 0:
            cost = qty * price
            self.cash -= cost
            self.position += qty
        elif action == "sell" and qty > 0:
            sell_qty = min(qty, self.position)
            if sell_qty <= 0:
                return
            self.cash += sell_qty * price
            self.position -= sell_qty
            qty = sell_qty
        else:
            return

        trade = {
            "tick": tick,
            "action": action,
            "quantity": qty,
            "price": round(price, 4),
            "position_after": self.position,
            "cash_after": round(self.cash, 2),
        }
        self.trades.append(trade)

    async def on_tick(self, event: Event):
        price = float(event.payload.get("price", 0))
        self._equity_curve.append(self.portfolio_value(price))

    async def on_sim_ended(self, event: Event):
        self.export_csv()

    def export_csv(self):
        path = Path(self.export_path)
        if not self.trades:
            path.write_text("tick,action,quantity,price,position_after,cash_after\n", encoding="utf-8")
            print(f"[Portfolio] Нет сделок. Пустой файл: {path}")
            return

        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["tick", "action", "quantity", "price", "position_after", "cash_after"],
            )
            writer.writeheader()
            writer.writerows(self.trades)
        print(f"[Portfolio] История сделок сохранена в {path}")

    def get_stats(self, final_price: float) -> dict:
        pnl = self.total_pnl(final_price)
        returns = []
        if len(self._equity_curve) > 1:
            for i in range(1, len(self._equity_curve)):
                prev = self._equity_curve[i - 1]
                if prev != 0:
                    returns.append((self._equity_curve[i] - prev) / prev)

        sharpe = 0.0
        if returns:
            mean_ret = float(np.mean(returns))
            std_ret = float(np.std(returns))
            if std_ret > 0:
                sharpe = round(mean_ret / std_ret * np.sqrt(252), 4)

        peak = self.initial_cash
        max_drawdown = 0.0
        for value in self._equity_curve:
            peak = max(peak, value)
            if peak > 0:
                dd = (peak - value) / peak
                max_drawdown = max(max_drawdown, dd)

        return {
            "initial_cash": round(self.initial_cash, 2),
            "final_cash": round(self.cash, 2),
            "final_position": self.position,
            "final_price": round(final_price, 2),
            "portfolio_value": round(self.portfolio_value(final_price), 2),
            "total_pnl": round(pnl, 2),
            "total_trades": len(self.trades),
            "sharpe_ratio": sharpe,
            "max_drawdown_pct": round(max_drawdown * 100, 2),
        }

    def register(self):
        self.bus.subscribe(EventType.ORDER_FILLED, self.on_order_filled)
        self.bus.subscribe(EventType.TICK, self.on_tick)
        self.bus.subscribe(EventType.SIM_ENDED, self.on_sim_ended)
