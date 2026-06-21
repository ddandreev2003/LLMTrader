"""Isolated per-agent portfolios — single or multi-ticker."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import numpy as np

from core.event_bus import Event, EventType


class AgentPortfolio:
    """Virtual account for one strategy agent (one or many tickers)."""

    def __init__(
        self,
        agent_id: str,
        tickers: list[str],
        strategy: str,
        initial_cash: float,
        max_position_pct: float = 0.95,
        commission_pct: float = 0.0003,
        target_weights: dict[str, float] | None = None,
    ):
        self.agent_id = agent_id
        self.tickers = list(tickers)
        self.ticker = self.tickers[0] if self.tickers else ""
        self.strategy = strategy
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.max_position_pct = max_position_pct
        self.commission_pct = commission_pct
        self.target_weights = target_weights or {}
        self.positions: dict[str, int] = {t: 0 for t in self.tickers}
        self.trades: list[dict] = []
        self._equity_curve: list[float] = []
        self._last_prices: dict[str, float] = {t: 0.0 for t in self.tickers}

    @property
    def position(self) -> int:
        """Legacy: position in primary ticker."""
        return self.positions.get(self.ticker, 0)

    def portfolio_value(self, prices: dict[str, float] | float) -> float:
        if isinstance(prices, (int, float)):
            prices = {self.ticker: float(prices)}
        value = self.cash
        for t, qty in self.positions.items():
            value += qty * prices.get(t, self._last_prices.get(t, 0.0))
        return value

    def total_pnl(self, prices: dict[str, float]) -> float:
        return self.portfolio_value(prices) - self.initial_cash

    def max_buy_quantity(self, ticker: str, price: float, prices: dict[str, float] | None = None) -> int:
        if price <= 0 or ticker not in self.positions:
            return 0
        prices = prices or self._last_prices
        pv = self.portfolio_value(prices)
        max_by_pct = math.floor(self.max_position_pct * pv / price)
        current = self.positions.get(ticker, 0)
        room = max(0, max_by_pct - current)
        max_by_cash = math.floor(self.cash / (price * (1 + self.commission_pct)))
        return max(0, min(room, max_by_cash))

    def can_sell(self, ticker: str, quantity: int) -> int:
        return min(quantity, self.positions.get(ticker, 0))

    def apply_fill(
        self, ticker: str, action: str, qty: int, price: float, tick: int, date: str
    ) -> bool:
        if ticker not in self.positions:
            return False

        if action == "buy" and qty > 0:
            cost = qty * price
            commission = cost * self.commission_pct
            total = cost + commission
            if total > self.cash:
                return False
            self.cash -= total
            self.positions[ticker] += qty
        elif action == "sell" and qty > 0:
            sell_qty = self.can_sell(ticker, qty)
            if sell_qty <= 0:
                return False
            proceeds = sell_qty * price
            commission = proceeds * self.commission_pct
            self.cash += proceeds - commission
            self.positions[ticker] -= sell_qty
            qty = sell_qty
        else:
            return False

        self.trades.append(
            {
                "tick": tick,
                "date": date,
                "agent_id": self.agent_id,
                "ticker": ticker,
                "strategy": self.strategy,
                "action": action,
                "quantity": qty,
                "price": round(price, 4),
                "position_after": self.positions[ticker],
                "cash_after": round(self.cash, 2),
            }
        )
        return True

    def record_tick(self, prices: dict[str, float]):
        self._last_prices.update(prices)
        self._equity_curve.append(self.portfolio_value(prices))

    def get_stats(self, prices: dict[str, float]) -> dict:
        pv = self.portfolio_value(prices)
        pnl = self.total_pnl(prices)
        invested = pv - self.cash
        invested_pct = (invested / pv * 100) if pv > 0 else 0.0

        holdings = {}
        for t, qty in self.positions.items():
            price = prices.get(t, self._last_prices.get(t, 0.0))
            mv = qty * price
            holdings[t] = {
                "position": qty,
                "price": round(price, 2),
                "market_value": round(mv, 2),
                "weight_pct": round(mv / pv * 100, 2) if pv > 0 else 0.0,
            }

        returns = []
        if len(self._equity_curve) > 1:
            for i in range(1, len(self._equity_curve)):
                prev = self._equity_curve[i - 1]
                if prev != 0:
                    returns.append((self._equity_curve[i] - prev) / prev)

        sharpe = None
        sharpe_unavailable_reason = None
        if returns:
            std_ret = float(np.std(returns))
            if std_ret > 1e-9:
                sharpe = round(float(np.mean(returns)) / std_ret * np.sqrt(252), 4)
            else:
                sharpe_unavailable_reason = "недостаточная волатильность"
        else:
            sharpe_unavailable_reason = "нет данных"

        return {
            "agent_id": self.agent_id,
            "tickers": self.tickers,
            "ticker": self.ticker,
            "strategy": self.strategy,
            "target_weights": self.target_weights,
            "holdings": holdings,
            "initial_cash": round(self.initial_cash, 2),
            "final_cash": round(self.cash, 2),
            "position": self.position,
            "portfolio_value": round(pv, 2),
            "total_pnl": round(pnl, 2),
            "pnl_pct": round(pnl / self.initial_cash * 100, 2) if self.initial_cash else 0.0,
            "total_trades": len(self.trades),
            "final_invested_pct": round(invested_pct, 2),
            "sharpe_ratio": sharpe,
            "sharpe_unavailable_reason": sharpe_unavailable_reason,
        }

    def snapshot(self, prices: dict[str, float]) -> dict:
        pv = self.portfolio_value(prices)
        return {
            "agent_id": self.agent_id,
            "ticker": self.ticker,
            "tickers": self.tickers,
            "strategy": self.strategy,
            "cash": round(self.cash, 2),
            "position": self.position,
            "holdings": dict(self.positions),
            "portfolio_value": round(pv, 2),
            "pnl": round(self.total_pnl(prices), 2),
            "target_weights": self.target_weights,
        }


class AgentPortfolioRegistry:
    """Manages isolated portfolios; supports multi-ticker agents."""

    def __init__(
        self,
        bus,
        agents: list,
        total_capital: float,
        max_position_pct: float = 0.95,
        commission_pct: float = 0.0003,
        export_dir: str = "output",
    ):
        self.bus = bus
        self.export_dir = Path(export_dir)
        self._portfolios: dict[str, AgentPortfolio] = {}
        self._traded_this_tick: set[str] = set()
        self._current_tick = -1
        self._last_prices: dict[str, float] = {}
        self._exposure_history: list[dict] = []

        n = len(agents) or 1
        default_capital = total_capital / n

        all_tickers: set[str] = set()
        for agent in agents:
            capital = float(getattr(agent, "initial_capital", None) or default_capital)
            universe = getattr(agent, "universe", None) or [agent.ticker]
            targets = getattr(agent, "portfolio_targets", None) or getattr(
                agent, "_targets", {}
            )
            pf = AgentPortfolio(
                agent_id=agent.agent_id,
                tickers=universe,
                strategy=agent.strategy.name,
                initial_cash=capital,
                max_position_pct=max_position_pct,
                commission_pct=commission_pct,
                target_weights=targets if isinstance(targets, dict) else {},
            )
            self._portfolios[agent.agent_id] = pf
            all_tickers.update(universe)

        self.trades: list[dict] = []
        self.positions = {t: 0 for t in all_tickers}
        self.cash = sum(p.cash for p in self._portfolios.values())
        self.initial_cash = total_capital
        self.max_position_pct = max_position_pct
        self.commission_pct = commission_pct

    def get_portfolio(self, agent_id: str) -> AgentPortfolio | None:
        return self._portfolios.get(agent_id)

    def agent_snapshots(self, prices: dict[str, float]) -> dict[str, dict]:
        return {
            agent_id: pf.snapshot(prices) for agent_id, pf in self._portfolios.items()
        }

    def portfolio_value(self, prices: dict[str, float]) -> float:
        return sum(pf.portfolio_value(prices) for pf in self._portfolios.values())

    def total_pnl(self, prices: dict[str, float]) -> float:
        return self.portfolio_value(prices) - self.initial_cash

    async def on_order_request(self, event: Event) -> Event | None:
        ticker = event.payload.get("ticker", "")
        agent_id = event.payload.get("agent_id", "")
        action = event.payload.get("action", "hold")
        qty = int(event.payload.get("quantity", 0))
        price = float(event.payload.get("price", 0))
        tick = event.payload.get("tick", 0)

        if tick != self._current_tick:
            self._traded_this_tick = set()
            self._current_tick = tick

        trade_key = f"{agent_id}:{ticker}"
        if trade_key in self._traded_this_tick:
            return Event(
                type=EventType.ORDER_REJECTED,
                payload={"reason": "cooldown: one trade per agent per tick", "ticker": ticker},
                source="PortfolioRegistry",
            )

        pf = self._portfolios.get(agent_id)
        if pf is None:
            return Event(
                type=EventType.ORDER_REJECTED,
                payload={"reason": f"unknown agent {agent_id}", "ticker": ticker},
                source="PortfolioRegistry",
            )

        if ticker not in pf.positions:
            return Event(
                type=EventType.ORDER_REJECTED,
                payload={"reason": f"ticker {ticker} not in agent universe", "agent_id": agent_id},
                source="PortfolioRegistry",
            )

        if action == "buy" and qty > 0:
            allowed = pf.max_buy_quantity(ticker, price, self._last_prices)
            if allowed <= 0 or qty > allowed:
                return Event(
                    type=EventType.ORDER_REJECTED,
                    payload={
                        "reason": f"insufficient cash or limit (max {allowed})",
                        "ticker": ticker,
                        "agent_id": agent_id,
                    },
                    source="PortfolioRegistry",
                )
        elif action == "sell" and qty > 0:
            if pf.can_sell(ticker, qty) <= 0:
                return Event(
                    type=EventType.ORDER_REJECTED,
                    payload={"reason": "no position to sell", "ticker": ticker, "agent_id": agent_id},
                    source="PortfolioRegistry",
                )
        return None

    async def on_order_filled(self, event: Event):
        ticker = event.payload.get("ticker", "")
        agent_id = event.payload.get("agent_id", "")
        pf = self._portfolios.get(agent_id)
        if pf is None:
            return

        action = event.payload.get("action")
        qty = int(event.payload.get("quantity", 0))
        price = float(event.payload.get("price", 0))
        tick = event.payload.get("tick", 0)
        trade_date = event.payload.get("date", "")

        if not pf.apply_fill(ticker, action, qty, price, tick, trade_date):
            return

        self._traded_this_tick.add(f"{agent_id}:{ticker}")
        self.trades.append(pf.trades[-1])
        self.positions[ticker] = sum(
            p.positions.get(ticker, 0) for p in self._portfolios.values()
        )
        self.cash = sum(p.cash for p in self._portfolios.values())

    async def on_tick(self, event: Event):
        prices = event.payload.get("prices", {})
        tick = int(event.payload.get("tick", 0))
        if prices:
            self._last_prices.update(prices)

        for pf in self._portfolios.values():
            pf.record_tick(prices)

        pv = self.portfolio_value(self._last_prices)
        invested = pv - sum(p.cash for p in self._portfolios.values())
        invested_pct = (invested / pv * 100) if pv > 0 else 0.0
        self._exposure_history.append(
            {"tick": tick, "invested_pct": round(invested_pct, 2), "portfolio_value": round(pv, 2)}
        )

    async def on_sim_ended(self, event: Event):
        self.export_csv()

    def export_csv(self):
        self.export_dir.mkdir(parents=True, exist_ok=True)
        fields = [
            "tick", "date", "agent_id", "ticker", "strategy",
            "action", "quantity", "price", "position_after", "cash_after",
        ]

        combined = self.export_dir / "trades_history.csv"
        with combined.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(self.trades)
        print(f"[PortfolioRegistry] Сделки: {combined} ({len(self.trades)} записей)")

        for agent_id, pf in self._portfolios.items():
            path = self.export_dir / f"trades_{agent_id}.csv"
            with path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                writer.writerows(pf.trades)

        stats_path = self.export_dir / "agent_stats.json"
        stats_path.write_text(
            json.dumps(self.get_agent_stats(self._last_prices), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[PortfolioRegistry] Статистика агентов: {stats_path}")

    def get_agent_stats(self, prices: dict[str, float]) -> dict[str, dict]:
        return {agent_id: pf.get_stats(prices) for agent_id, pf in self._portfolios.items()}

    def get_stats(self, final_prices: dict[str, float]) -> dict:
        agent_stats = self.get_agent_stats(final_prices)
        pv = self.portfolio_value(final_prices)
        pnl = self.total_pnl(final_prices)

        per_ticker = {}
        for agent_id, st in agent_stats.items():
            for ticker, h in st.get("holdings", {}).items():
                if h.get("position", 0) > 0 or h.get("market_value", 0) > 0:
                    per_ticker[ticker] = {
                        "agent_id": agent_id,
                        "strategy": st["strategy"],
                        "position": h["position"],
                        "price": h["price"],
                        "market_value": h["market_value"],
                        "weight_pct": h["weight_pct"],
                        "pnl": st["total_pnl"],
                    }

        avg_invested = 0.0
        if self._exposure_history:
            avg_invested = float(np.mean([e["invested_pct"] for e in self._exposure_history]))

        final_invested = pv - self.cash
        final_invested_pct = (final_invested / pv * 100) if pv > 0 else 0.0

        return {
            "initial_cash": round(self.initial_cash, 2),
            "final_cash": round(self.cash, 2),
            "positions": per_ticker,
            "agent_stats": agent_stats,
            "portfolio_value": round(pv, 2),
            "total_pnl": round(pnl, 2),
            "total_trades": len(self.trades),
            "trading_days_with_trades": len({t["tick"] for t in self.trades}),
            "avg_invested_pct": round(avg_invested, 2),
            "final_invested_pct": round(final_invested_pct, 2),
            "final_cash_pct": round(self.cash / pv * 100, 2) if pv > 0 else 100.0,
            "exposure_history": self._exposure_history,
            "sharpe_ratio": None,
            "sharpe_unavailable_reason": "используйте agent_stats для per-agent Sharpe",
            "max_drawdown_pct": 0.0,
        }

    def register(self):
        self.bus.subscribe(EventType.ORDER_FILLED, self.on_order_filled)
        self.bus.subscribe(EventType.TICK, self.on_tick)
        self.bus.subscribe(EventType.SIM_ENDED, self.on_sim_ended)
