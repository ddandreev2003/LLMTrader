import asyncio

from core.event_bus import Event, EventType
from shachi_shock.env.regulatory_env import RegulatoryShockEnvironment
from shachi_shock.models import MarketSnapshot
from shachi_shock.runner import create_regulator_agents_for_bridge, run_shock_cycle


class ShachiShockBridge:
    """Adapter: TICK events -> Shachi multi-agent shock cycle -> SHOCK_TRIGGERED."""

    def __init__(
        self,
        bus,
        shock_interval_ticks: int = 60,
        model: str | None = None,
        temperature: float = 0.5,
    ):
        self.bus = bus
        self.interval = shock_interval_ticks
        self._tick_counter = 0
        self._env = RegulatoryShockEnvironment()
        self._agents = create_regulator_agents_for_bridge(
            self._env, model=model, temperature=temperature
        )
        self._lock = asyncio.Lock()

    async def on_tick(self, event: Event):
        self._tick_counter += 1
        if self._tick_counter % self.interval != 0:
            return

        snapshot = MarketSnapshot(
            tick=event.payload.get("tick", self._tick_counter),
            price=float(event.payload.get("price", event.payload.get("portfolio_value", 100) / 1000)),
            portfolio_pnl=float(event.payload.get("portfolio_pnl", 0)),
            position=int(event.payload.get("position", 0)),
            total_trades=int(event.payload.get("total_trades", 0)),
            halted=bool(event.payload.get("halted", False)),
            volatility_mult=float(event.payload.get("volatility_mult", 1.0)),
        )

        async with self._lock:
            try:
                shocks = await run_shock_cycle(self._env, self._agents, snapshot)
            except Exception as exc:
                print(f"[ShachiShockBridge] Ошибка LLM/агентов: {exc}")
                return

        for shock in shocks:
            shock["tick"] = snapshot.tick
            role_ids = shock.get("proposed_by", [])
            print(
                f"[ShachiShockBridge] Шок: {shock.get('type')} "
                f"(агенты {role_ids}) — {shock.get('description', '')}"
            )
            await self.bus.publish(
                Event(
                    type=EventType.SHOCK_TRIGGERED,
                    payload=shock,
                    source="ShachiShockBridge",
                )
            )

    def register(self):
        self.bus.subscribe(EventType.TICK, self.on_tick)
