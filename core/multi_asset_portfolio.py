import csv
import math
from pathlib import Path

import numpy as np

from core.event_bus import Event, EventType


class MultiAssetPortfolio:
    """Multi-ticker portfolio with cash, position limits, and commission."""

    def __init__(
        self,
        bus,
        tickers: list[str],
        initial_cash: float = 1_000_000.0,
        max_position_pct: float = 0.25,
        commission_pct: float = 0.0003,
        export_path: str = "trades_history.csv",
    ):
        self.bus = bus
        self.tickers = tickers
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.max_position_pct = max_position_pct
        self.commission_pct = commission_pct
        self.positions: dict[str, int] = {t: 0 for t in tickers}
        self.trades: list[dict] = []
        self._equity_curve: list[float] = []
        self._exposure_history: list[dict] = []
        self.export_path = export_path
        self._last_prices: dict[str, float] = {t: 0.0 for t in tickers}
        self._traded_this_tick: set[str] = set()
        self._current_tick = -1

    def invested_value(self, prices: dict[str, float]) -> float:
        return sum(
            self.positions.get(ticker, 0) * prices.get(ticker, self._last_prices.get(ticker, 0.0))
            for ticker in self.tickers
        )

    def portfolio_value(self, prices: dict[str, float]) -> float:
        return self.cash + self.invested_value(prices)

    def total_pnl(self, prices: dict[str, float]) -> float:
        return self.portfolio_value(prices) - self.initial_cash

    def max_buy_quantity(self, ticker: str, price: float, prices: dict[str, float]) -> int:
        if price <= 0:
            return 0
        pv = self.portfolio_value(prices)
        max_by_pct = math.floor(self.max_position_pct * pv / price)
        current = self.positions.get(ticker, 0)
        room = max(0, max_by_pct - current)
        max_by_cash = math.floor(self.cash / (price * (1 + self.commission_pct)))
        return max(0, min(room, max_by_cash))

    def can_sell(self, ticker: str, quantity: int) -> int:
        return min(quantity, self.positions.get(ticker, 0))

    async def on_order_request(self, event: Event) -> Event | None:
        """Validate order before fill; returns rejection event or None."""
        ticker = event.payload.get("ticker", "")
        action = event.payload.get("action", "hold")
        qty = int(event.payload.get("quantity", 0))
        price = float(event.payload.get("price", 0))
        tick = event.payload.get("tick", 0)

        if tick != self._current_tick:
            self._traded_this_tick = set()
            self._current_tick = tick

        if ticker in self._traded_this_tick:
            return Event(
                type=EventType.ORDER_REJECTED,
                payload={"reason": "cooldown: one trade per ticker per tick", "ticker": ticker},
                source="Portfolio",
            )

        if action == "buy" and qty > 0:
            allowed = self.max_buy_quantity(ticker, price, self._last_prices)
            if allowed <= 0 or qty > allowed:
                return Event(
                    type=EventType.ORDER_REJECTED,
                    payload={
                        "reason": f"insufficient cash or position limit (max {allowed})",
                        "ticker": ticker,
                    },
                    source="Portfolio",
                )
        elif action == "sell" and qty > 0:
            sellable = self.can_sell(ticker, qty)
            if sellable <= 0:
                return Event(
                    type=EventType.ORDER_REJECTED,
                    payload={"reason": "no position to sell", "ticker": ticker},
                    source="Portfolio",
                )
        return None

    async def on_order_filled(self, event: Event):
        action = event.payload.get("action")
        ticker = event.payload.get("ticker", "")
        qty = int(event.payload.get("quantity", 0))
        price = float(event.payload.get("price", 0))
        tick = event.payload.get("tick", 0)
        trade_date = event.payload.get("date", "")

        if action == "buy" and qty > 0:
            cost = qty * price
            commission = cost * self.commission_pct
            total = cost + commission
            if total > self.cash:
                return
            self.cash -= total
            self.positions[ticker] = self.positions.get(ticker, 0) + qty
        elif action == "sell" and qty > 0:
            sell_qty = self.can_sell(ticker, qty)
            if sell_qty <= 0:
                return
            proceeds = sell_qty * price
            commission = proceeds * self.commission_pct
            self.cash += proceeds - commission
            self.positions[ticker] -= sell_qty
            qty = sell_qty
        else:
            return

        self._traded_this_tick.add(ticker)
        self.trades.append(
            {
                "tick": tick,
                "date": trade_date,
                "ticker": ticker,
                "action": action,
                "quantity": qty,
                "price": round(price, 4),
                "position_after": self.positions.get(ticker, 0),
                "cash_after": round(self.cash, 2),
            }
        )

    async def on_tick(self, event: Event):
        prices = event.payload.get("prices", {})
        tick = int(event.payload.get("tick", 0))
        if prices:
            self._last_prices.update(prices)
        pv = self.portfolio_value(self._last_prices)
        invested = self.invested_value(self._last_prices)
        invested_pct = (invested / pv * 100) if pv > 0 else 0.0
        self._equity_curve.append(pv)
        self._exposure_history.append(
            {
                "tick": tick,
                "portfolio_value": round(pv, 2),
                "invested_pct": round(invested_pct, 2),
                "cash_pct": round(100 - invested_pct, 2),
            }
        )

    async def on_sim_ended(self, event: Event):
        self.export_csv()

    def export_csv(self):
        path = Path(self.export_path)
        fields = ["tick", "date", "ticker", "action", "quantity", "price", "position_after", "cash_after"]
        if not self.trades:
            path.write_text(",".join(fields) + "\n", encoding="utf-8")
            print(f"[Portfolio] Нет сделок. Пустой файл: {path}")
            return
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(self.trades)
        print(f"[Portfolio] История сделок сохранена в {path}")

    def get_stats(self, final_prices: dict[str, float]) -> dict:
        pnl = self.total_pnl(final_prices)
        returns = []
        if len(self._equity_curve) > 1:
            for i in range(1, len(self._equity_curve)):
                prev = self._equity_curve[i - 1]
                if prev != 0:
                    returns.append((self._equity_curve[i] - prev) / prev)

        sharpe = None
        sharpe_unavailable_reason = None
        if returns:
            mean_ret = float(np.mean(returns))
            std_ret = float(np.std(returns))
            if std_ret > 1e-9:
                sharpe = round(mean_ret / std_ret * np.sqrt(252), 4)
            else:
                sharpe_unavailable_reason = "недостаточная волатильность доходности"
        else:
            sharpe_unavailable_reason = "нет данных о доходности"

        peak = self.initial_cash
        max_drawdown = 0.0
        for value in self._equity_curve:
            peak = max(peak, value)
            if peak > 0:
                dd = (peak - value) / peak
                max_drawdown = max(max_drawdown, dd)

        final_pv = self.portfolio_value(final_prices)
        final_invested = self.invested_value(final_prices)
        final_invested_pct = (final_invested / final_pv * 100) if final_pv > 0 else 0.0
        final_cash_pct = (self.cash / final_pv * 100) if final_pv > 0 else 100.0

        avg_invested_pct = 0.0
        if self._exposure_history:
            avg_invested_pct = float(np.mean([e["invested_pct"] for e in self._exposure_history]))

        trade_ticks = {t["tick"] for t in self.trades}
        per_ticker = {}
        for ticker in self.tickers:
            qty = self.positions.get(ticker, 0)
            price = final_prices.get(ticker, 0.0)
            market_value = qty * price
            weight_pct = (market_value / final_pv * 100) if final_pv > 0 else 0.0
            per_ticker[ticker] = {
                "position": qty,
                "price": round(price, 2),
                "market_value": round(market_value, 2),
                "weight_pct": round(weight_pct, 2),
            }

        return {
            "initial_cash": round(self.initial_cash, 2),
            "final_cash": round(self.cash, 2),
            "positions": per_ticker,
            "portfolio_value": round(final_pv, 2),
            "total_pnl": round(pnl, 2),
            "total_trades": len(self.trades),
            "trading_days_with_trades": len(trade_ticks),
            "sharpe_ratio": sharpe,
            "sharpe_unavailable_reason": sharpe_unavailable_reason,
            "max_drawdown_pct": round(max_drawdown * 100, 2),
            "avg_invested_pct": round(avg_invested_pct, 2),
            "final_invested_pct": round(final_invested_pct, 2),
            "final_cash_pct": round(final_cash_pct, 2),
            "exposure_history": self._exposure_history,
        }

    def register(self):
        self.bus.subscribe(EventType.ORDER_FILLED, self.on_order_filled)
        self.bus.subscribe(EventType.TICK, self.on_tick)
        self.bus.subscribe(EventType.SIM_ENDED, self.on_sim_ended)
