import asyncio

import pandas as pd

from core.event_bus import Event, EventType


class MultiAssetMarketEngine:
    """Replay historical MOEX daily bars for multiple tickers."""

    def __init__(
        self,
        bus,
        price_data: pd.DataFrame,
        portfolio=None,
        tick_interval: float = 0.0,
        warmup_ticks: int = 0,
        trade_cutoff_ticks: int = 0,
    ):
        self.bus = bus
        self.price_data = price_data
        self.portfolio = portfolio
        self.tick_interval = tick_interval
        self.warmup_ticks = warmup_ticks
        self.trade_cutoff_ticks = trade_cutoff_ticks
        self.tickers = list(price_data.columns)
        self._halted = False
        self._volatility_mult = 1.0
        self._current_tick = 0
        self._shock_resume_tick: int | None = None
        self._prices: dict[str, float] = {}
        self._traded_this_tick: set[str] = set()
        self._subscribed = False

    def _ensure_subscribed(self):
        if self._subscribed:
            return
        self.bus.subscribe(EventType.STRATEGY_SIGNAL, self.on_signal)
        self.bus.subscribe(EventType.SHOCK_TRIGGERED, self.on_shock)
        self._subscribed = True

    async def run(self, n_ticks: int | None = None):
        self._ensure_subscribed()
        total = n_ticks if n_ticks is not None else len(self.price_data)
        total = min(total, len(self.price_data))

        await self.bus.publish(Event(type=EventType.SIM_STARTED, source="Market"))

        for i in range(total):
            if not await self.advance_tick(i, total):
                break
            if self.tick_interval > 0:
                await asyncio.sleep(self.tick_interval)

        await self._finish()

    async def run_controlled(self, controller, n_ticks: int | None = None):
        """Run simulation with pause/step/play via SimulationController."""
        from core.simulation_controller import SimStatus

        self._ensure_subscribed()
        total = n_ticks if n_ticks is not None else len(self.price_data)
        total = min(total, len(self.price_data))
        controller.configure(total_ticks=total)

        await self.bus.publish(Event(type=EventType.SIM_STARTED, source="Market"))

        i = 0
        while i < total:
            if not await controller.wait_for_next_tick():
                break

            if not await self.advance_tick(i, total):
                break

            controller.tick_completed(i)
            i += 1

            if controller.status == SimStatus.RUNNING and controller.auto_interval_ms > 0:
                await asyncio.sleep(controller.auto_interval_ms / 1000.0)

        if not controller._stop_requested:
            await self._finish()
            controller.mark_finished()
        else:
            self.bus.stop()

    async def advance_tick(self, i: int, total: int) -> bool:
        self._current_tick = i
        self._traded_this_tick = set()
        await self._maybe_resume_trading(i)

        row = self.price_data.iloc[i]
        date_str = str(self.price_data.index[i].date())
        self._prices = {t: float(row[t]) for t in self.tickers}

        portfolio_pnl = 0.0
        portfolio_value = 0.0
        cash = 0.0
        total_trades = 0
        if self.portfolio is not None:
            portfolio_pnl = self.portfolio.total_pnl(self._prices)
            portfolio_value = self.portfolio.portfolio_value(self._prices)
            cash = self.portfolio.cash
            total_trades = len(self.portfolio.trades)

        trading_enabled = i >= self.warmup_ticks

        agent_portfolios = {}
        if self.portfolio is not None and hasattr(self.portfolio, "agent_snapshots"):
            agent_portfolios = self.portfolio.agent_snapshots(self._prices)

        await self.bus.publish(
            Event(
                type=EventType.TICK,
                payload={
                    "tick": i,
                    "date": date_str,
                    "prices": dict(self._prices),
                    "price": self._benchmark_price(),
                    "halted": self._halted,
                    "portfolio_pnl": portfolio_pnl,
                    "portfolio_value": portfolio_value,
                    "cash": cash,
                    "position": sum(self.portfolio.positions.values()) if self.portfolio else 0,
                    "total_trades": total_trades,
                    "volatility_mult": self._volatility_mult,
                    "trading_enabled": trading_enabled,
                    "warmup": i < self.warmup_ticks,
                    "total_ticks": total,
                    "trade_cutoff_ticks": self.trade_cutoff_ticks,
                    "agent_portfolios": agent_portfolios,
                },
                source="Market",
            )
        )

        await self.bus.drain()
        return True

    async def _finish(self):
        await self.bus.drain()
        stats = self._final_stats()
        await self.bus.publish(
            Event(type=EventType.SIM_ENDED, payload=stats, source="Market")
        )
        await self.bus.drain()
        self.bus.stop()

    def reset_market_state(self):
        self._halted = False
        self._volatility_mult = 1.0
        self._current_tick = 0
        self._shock_resume_tick = None
        self._prices = {}
        self._traded_this_tick = set()

    def _benchmark_price(self) -> float:
        if not self._prices:
            return 100.0
        return sum(self._prices.values()) / len(self._prices)

    async def _maybe_resume_trading(self, tick: int):
        if self._halted and self._shock_resume_tick is not None and tick >= self._shock_resume_tick:
            self._halted = False
            self._shock_resume_tick = None
            await self.bus.publish(Event(type=EventType.TRADING_RESUMED, source="Market"))

    async def on_signal(self, event: Event):
        if self._halted:
            await self.bus.publish(
                Event(
                    type=EventType.ORDER_REJECTED,
                    payload={"reason": "trading halted"},
                    source="Market",
                )
            )
            return

        if self._current_tick < self.warmup_ticks:
            await self.bus.publish(
                Event(
                    type=EventType.ORDER_REJECTED,
                    payload={"reason": "warmup period: trading disabled"},
                    source="Market",
                )
            )
            return

        ticker = event.payload.get("ticker", self.tickers[0] if self.tickers else "")
        agent_id = event.payload.get("agent_id", "")
        action = event.payload.get("action", "hold")
        qty = int(event.payload.get("quantity", 0))
        trade_key = f"{agent_id}:{ticker}" if agent_id else ticker

        if trade_key in self._traded_this_tick or ticker in self._traded_this_tick:
            await self.bus.publish(
                Event(
                    type=EventType.ORDER_REJECTED,
                    payload={"reason": "cooldown: one trade per ticker per tick", "ticker": ticker},
                    source="Market",
                )
            )
            return

        if action not in ("buy", "sell") or qty <= 0:
            return

        price = self._prices.get(ticker)
        if price is None:
            await self.bus.publish(
                Event(
                    type=EventType.ORDER_REJECTED,
                    payload={"reason": f"unknown ticker {ticker}", "ticker": ticker},
                    source="Market",
                )
            )
            return

        if self.portfolio is not None:
            check = Event(
                type=EventType.ORDER_PLACED,
                payload={
                    "ticker": ticker,
                    "action": action,
                    "quantity": qty,
                    "price": price,
                    "tick": self._current_tick,
                    "agent_id": agent_id,
                    "strategy": event.payload.get("strategy", ""),
                },
                source="Market",
            )
            rejection = await self.portfolio.on_order_request(check)
            if rejection is not None:
                await self.bus.publish(rejection)
                return

        filled = {
            "ticker": ticker,
            "agent_id": agent_id,
            "strategy": event.payload.get("strategy", ""),
            "action": action,
            "quantity": qty,
            "price": price,
            "tick": self._current_tick,
            "date": str(self.price_data.index[self._current_tick].date()),
        }
        self._traded_this_tick.add(trade_key)
        self._traded_this_tick.add(ticker)
        await self.bus.publish(
            Event(type=EventType.ORDER_FILLED, payload=filled, source="Market")
        )

    async def on_shock(self, event: Event):
        shock_type = event.payload.get("type", "")
        impact = event.payload.get("price_impact_pct", 0) / 100
        target_ticker = event.payload.get("ticker")

        if target_ticker and target_ticker in self._prices:
            self._prices[target_ticker] *= 1 + impact
            self._prices[target_ticker] = max(self._prices[target_ticker], 0.01)
        else:
            for t in self._prices:
                self._prices[t] *= 1 + impact
                self._prices[t] = max(self._prices[t], 0.01)

        self._volatility_mult = event.payload.get("volatility_multiplier", 1.0)

        duration = int(event.payload.get("duration_ticks", 10))
        if shock_type in ("halt", "circuit_breaker"):
            self._halted = True
            self._shock_resume_tick = self._current_tick + duration
            await self.bus.publish(Event(type=EventType.TRADING_HALTED, source="Market"))

    def _final_stats(self) -> dict:
        if self.portfolio is not None:
            return self.portfolio.get_stats(self._prices)
        return {"final_prices": self._prices, "total_pnl": 0.0}
