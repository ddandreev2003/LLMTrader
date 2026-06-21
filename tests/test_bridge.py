import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from core.event_bus import Event, EventType
from shachi_shock.bridge import ShachiShockBridge


def test_bridge_publishes_shock_triggered():
    bus = MagicMock()
    bus.publish = AsyncMock()

    bridge = ShachiShockBridge(bus, shock_interval_ticks=1)

    fake_shocks = [
        {
            "shock_occurred": True,
            "type": "news_spike",
            "severity": 5,
            "duration_ticks": 10,
            "price_impact_pct": -3.0,
            "volatility_multiplier": 2.0,
            "description": "test shock",
            "proposed_by": [2],
        }
    ]

    async def run():
        with patch(
            "shachi_shock.bridge.run_shock_cycle",
            new=AsyncMock(return_value=fake_shocks),
        ):
            event = Event(
                type=EventType.TICK,
                payload={
                    "tick": 60,
                    "price": 101.5,
                    "portfolio_pnl": 10.0,
                    "position": 5,
                    "total_trades": 2,
                    "halted": False,
                    "volatility_mult": 1.0,
                },
            )
            await bridge.on_tick(event)

    asyncio.run(run())

    bus.publish.assert_called_once()
    published = bus.publish.call_args[0][0]
    assert published.type == EventType.SHOCK_TRIGGERED
    assert published.payload["type"] == "news_spike"
    assert published.source == "ShachiShockBridge"


def test_bridge_skips_non_interval_ticks():
    bus = MagicMock()
    bus.publish = AsyncMock()
    bridge = ShachiShockBridge(bus, shock_interval_ticks=60)

    async def run():
        event = Event(type=EventType.TICK, payload={"tick": 1, "price": 100.0})
        await bridge.on_tick(event)

    asyncio.run(run())
    bus.publish.assert_not_called()
