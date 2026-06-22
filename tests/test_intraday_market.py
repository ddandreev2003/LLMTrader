"""Tests for IntradayMarketEngine."""

import asyncio

import pandas as pd
import pytest

from core.agent_portfolio import AgentPortfolioRegistry
from core.event_bus import Event, EventType
from core.intraday_market import IntradayMarketEngine
from agents.ticker_strategy_agent import TickerStrategyAgent


@pytest.fixture
def sample_bars():
    idx = pd.date_range("2025-01-03 07:05", periods=5, freq="5min")
    return pd.DataFrame(
        {"T": [3000, 3005, 3010, 3008, 3012], "LQDT": [1.5, 1.51, 1.52, 1.51, 1.53], "SBER": [250, 251, 252, 251, 253]},
        index=idx,
    )


def test_intraday_tick_payload(sample_bars):
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
    engine = IntradayMarketEngine(bus, sample_bars, warmup_bars=1, trade_cutoff_bars=0)

    async def run():
        await engine.advance_tick(1, 5)

    asyncio.run(run())

    tick_events = [e for e in bus_events if e.type == EventType.TICK]
    bar_ready = [e for e in bus_events if e.type == EventType.BAR_PROPOSALS_READY]
    assert len(tick_events) == 1
    assert len(bar_ready) == 1
    payload = tick_events[0].payload
    assert payload["intraday"] is True
    assert "datetime" in payload
    assert payload["bar_interval"] == "5m"
    assert set(payload["prices"].keys()) == {"T", "LQDT", "SBER"}


def test_intraday_engine_fills_order(sample_bars):
    filled = []

    class SimpleBus:
        def __init__(self):
            self._handlers = {}

        def subscribe(self, event_type, handler):
            self._handlers.setdefault(event_type, []).append(handler)

        async def publish(self, event):
            for h in self._handlers.get(event.type, []):
                await h(event)
            if event.type == EventType.ORDER_FILLED:
                filled.append(event.payload)

        async def drain(self):
            pass

        def stop(self):
            pass

    async def run():
        bus = SimpleBus()
        agent = TickerStrategyAgent(
            bus, "t_test", "T", "momentum", llm_enabled=False, proposal_mode=False
        )
        agent.initial_capital = 100_000
        agents = [agent]
        portfolio = AgentPortfolioRegistry(bus, agents=agents, total_capital=100_000)
        portfolio.register()
        agent.register()

        engine = IntradayMarketEngine(
            bus, sample_bars, portfolio=portfolio, warmup_bars=0, trade_cutoff_bars=0
        )
        engine._ensure_subscribed()
        engine._current_tick = 0
        engine._prices = {"T": 3000.0, "LQDT": 1.5, "SBER": 250.0}

        await engine.on_signal(
            Event(
                type=EventType.STRATEGY_SIGNAL,
                payload={
                    "ticker": "T",
                    "action": "buy",
                    "quantity": 1,
                    "agent_id": "t_test",
                    "strategy": "momentum",
                },
            )
        )

    asyncio.run(run())
    assert len(filled) == 1
    assert filled[0]["ticker"] == "T"
    assert filled[0]["datetime"]
