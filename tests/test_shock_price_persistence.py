"""Tests for persistent shock price multipliers in intraday replay."""

import asyncio

import pandas as pd
import pytest

from core.event_bus import Event, EventType
from core.intraday_market import IntradayMarketEngine


@pytest.fixture
def sample_bars():
    idx = pd.date_range("2025-01-03 07:05", periods=3, freq="5min")
    return pd.DataFrame({"T": [100.0, 101.0, 102.0]}, index=idx)


def test_shock_multiplier_persists_to_next_bar(sample_bars):
    bus_events = []

    class CollectBus:
        def __init__(self):
            self._handlers = {}

        def subscribe(self, event_type, handler):
            self._handlers.setdefault(event_type, []).append(handler)

        async def publish(self, event):
            bus_events.append(event)
            for h in self._handlers.get(event.type, []):
                await h(event)

        async def drain(self):
            pass

        def stop(self):
            pass

    bus = CollectBus()
    engine = IntradayMarketEngine(
        bus,
        sample_bars,
        shock_price_persistent=True,
        impact_decay_per_bar=1.0,
    )
    engine._ensure_subscribed()

    async def run():
        await engine.advance_tick(0, 3)
        await engine.on_shock(
            Event(type=EventType.SHOCK_TRIGGERED, payload={"price_impact_pct": 1.0, "type": "news_spike"})
        )
        await engine.advance_tick(1, 3)

    asyncio.run(run())

    tick_events = [e for e in bus_events if e.type == EventType.TICK]
    assert len(tick_events) >= 2
    last_tick = tick_events[-1].payload
    assert last_tick["prices"]["T"] == pytest.approx(101.0 * 1.01, rel=1e-4)
    assert engine._price_multiplier["T"] == pytest.approx(1.01, rel=1e-6)


def test_impact_decay_reduces_multiplier(sample_bars):
    class DummyBus:
        def subscribe(self, *args):
            pass

    engine = IntradayMarketEngine(
        DummyBus(),
        sample_bars,
        shock_price_persistent=True,
        impact_decay_per_bar=0.99,
    )
    engine._price_multiplier["T"] = 1.05
    engine._apply_impact_decay()
    assert engine._price_multiplier["T"] == pytest.approx(1.05 * 0.99, rel=1e-6)
