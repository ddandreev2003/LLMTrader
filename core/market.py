import asyncio
import math
import random

from core.event_bus import Event, EventType


class MarketEngine:
    """
    Генерирует синтетические тики.
    Обрабатывает ордера от трейдера.
    Чистый Python, без LLM.
    """

    def __init__(
        self,
        bus,
        portfolio=None,
        start_price: float = 100.0,
        tick_interval: float = 0.05,
    ):
        self.bus = bus
        self.portfolio = portfolio
        self.price = start_price
        self.tick_interval = tick_interval
        self._halted = False
        self._volatility_mult = 1.0
        self._current_tick = 0
        self._shock_resume_tick: int | None = None

    async def run(self, n_ticks: int = 500):
        self._ensure_subscribed()
        await self.bus.publish(Event(type=EventType.SIM_STARTED, source="Market"))

        for i in range(n_ticks):
            if not await self.advance_tick(i, n_ticks):
                break
            if self.tick_interval > 0:
                await asyncio.sleep(self.tick_interval)

        await self._finish()

    async def run_controlled(self, controller, n_ticks: int = 500):
        from core.simulation_controller import SimStatus

        self._ensure_subscribed()
        controller.configure(total_ticks=n_ticks)
        await self.bus.publish(Event(type=EventType.SIM_STARTED, source="Market"))

        i = 0
        while i < n_ticks:
            if not await controller.wait_for_next_tick():
                break
            if not await self.advance_tick(i, n_ticks):
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

    def _ensure_subscribed(self):
        self.bus.subscribe(EventType.STRATEGY_SIGNAL, self.on_signal)
        self.bus.subscribe(EventType.SHOCK_TRIGGERED, self.on_shock)

    async def advance_tick(self, i: int, total: int) -> bool:
        self._current_tick = i
        await self._maybe_resume_trading(i)

        if not self._halted:
            self._update_price()

        portfolio_pnl = 0.0
        position = 0
        total_trades = 0
        if self.portfolio is not None:
            portfolio_pnl = self.portfolio.total_pnl(self.price)
            position = self.portfolio.position
            total_trades = len(self.portfolio.trades)

        await self.bus.publish(
            Event(
                type=EventType.TICK,
                payload={
                    "tick": i,
                    "price": self.price,
                    "halted": self._halted,
                    "portfolio_pnl": portfolio_pnl,
                    "position": position,
                    "total_trades": total_trades,
                    "volatility_mult": self._volatility_mult,
                    "total_ticks": total,
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
        self.price = 100.0
        self._halted = False
        self._volatility_mult = 1.0
        self._current_tick = 0
        self._shock_resume_tick = None

    async def _maybe_resume_trading(self, tick: int):
        if self._halted and self._shock_resume_tick is not None and tick >= self._shock_resume_tick:
            self._halted = False
            self._shock_resume_tick = None
            await self.bus.publish(Event(type=EventType.TRADING_RESUMED, source="Market"))

    def _update_price(self):
        """Геометрическое броуновское движение."""
        drift = 0.0001
        sigma = 0.002 * self._volatility_mult
        shock_price = random.gauss(drift, sigma)
        self.price *= math.exp(shock_price)
        self.price = max(self.price, 0.01)

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

        action = event.payload.get("action", "hold")
        qty = event.payload.get("quantity", 0)
        if action in ("buy", "sell") and qty > 0:
            filled = {
                "action": action,
                "quantity": qty,
                "price": self.price,
                "tick": self._current_tick,
            }
            await self.bus.publish(
                Event(
                    type=EventType.ORDER_FILLED,
                    payload=filled,
                    source="Market",
                )
            )

    async def on_shock(self, event: Event):
        shock_type = event.payload.get("type", "")
        impact = event.payload.get("price_impact_pct", 0) / 100
        self.price *= 1 + impact
        self.price = max(self.price, 0.01)
        self._volatility_mult = event.payload.get("volatility_multiplier", 1.0)

        duration = int(event.payload.get("duration_ticks", 10))
        if shock_type in ("halt", "circuit_breaker"):
            self._halted = True
            self._shock_resume_tick = self._current_tick + duration
            await self.bus.publish(Event(type=EventType.TRADING_HALTED, source="Market"))

    def _final_stats(self) -> dict:
        if self.portfolio is not None:
            return self.portfolio.get_stats(self.price)
        return {
            "final_price": round(self.price, 2),
            "total_pnl": 0.0,
        }
