"""Tests for LiveVizAgent bar history."""

import asyncio

from agents.live_viz_agent import LiveVizAgent
from core.event_bus import Event, EventType


def test_live_viz_records_bar_history():
    bus_events = []

    class CollectBus:
        def subscribe(self, event_type, handler):
            pass

        async def publish(self, event):
            bus_events.append(event)

    agent = LiveVizAgent(CollectBus(), agents=[])

    async def run():
        await agent.on_tick(
            Event(
                type=EventType.TICK,
                payload={
                    "tick": 5,
                    "date": "2025-01-03",
                    "datetime": "2025-01-03T07:10:00",
                    "bar_interval": "5m",
                    "price": 1000.0,
                    "prices": {"T": 3000.0, "LQDT": 1.5, "SBER": 250.0},
                    "tickers": ["T", "LQDT", "SBER"],
                    "bars": {
                        "T": {"open": 2990, "high": 3010, "low": 2985, "close": 3000, "volume": 1000},
                        "LQDT": {"open": 1.49, "high": 1.51, "low": 1.48, "close": 1.5, "volume": 500},
                        "SBER": {"open": 249, "high": 251, "low": 248, "close": 250, "volume": 800},
                    },
                    "agent_portfolios": {},
                    "halted": False,
                },
            )
        )

    asyncio.run(run())
    state = agent.get_state()
    bh = state["bar_history"]
    assert bh["timestamps"] == ["2025-01-03T07:10:00"]
    assert bh["candles"]["T"][0]["c"] == 3000
    assert bh["candles"]["SBER"][0]["h"] == 251
    assert state["datetime"] == "2025-01-03T07:10:00"


def test_live_viz_orderlog_stream_per_ticker_candles():
    class DummyBus:
        def subscribe(self, *args):
            pass

    agent = LiveVizAgent(DummyBus(), agents=[])

    async def run():
        for tick, ticker, dt, close in [
            (1, "T", "2025-01-03T07:00:00", 3000),
            (2, "GAZP", "2025-01-03T07:01:00", 150),
            (3, "SBER", "2025-01-03T07:02:00", 250),
            (4, "T", "2025-01-03T07:03:00", 3010),
        ]:
            await agent.on_tick(
                Event(
                    type=EventType.TICK,
                    payload={
                        "tick": tick,
                        "datetime": dt,
                        "orderlog_stream": True,
                        "closed_ticker": ticker,
                        "closed_bar": {
                            "open": close - 5,
                            "high": close + 5,
                            "low": close - 10,
                            "close": close,
                            "volume": 100,
                        },
                        "prices": {"T": 3000, "GAZP": 150, "SBER": 250},
                        "tickers": ["T", "GAZP", "SBER"],
                        "bars": {},
                        "agent_portfolios": {},
                    },
                )
            )

    asyncio.run(run())
    bh = agent.get_state()["bar_history"]
    assert len(bh["candles"]["T"]) == 2
    assert len(bh["candles"]["GAZP"]) == 1
    assert len(bh["timestamps_by_ticker"]["T"]) == 2
    assert bh["timestamps_by_ticker"]["T"][0] == "2025-01-03T07:00:00"
    assert bh["candles"]["T"][1]["c"] == 3010


def test_live_viz_logs_proposal_and_reject():
    class DummyBus:
        def subscribe(self, *args):
            pass

    agent = LiveVizAgent(DummyBus(), agents=[])

    async def run():
        await agent.on_proposal(
            Event(
                type=EventType.PROPOSED_SIGNAL,
                payload={
                    "agent_id": "t_momentum",
                    "ticker": "T",
                    "action": "buy",
                    "quantity": 10,
                    "tick": 1,
                },
            )
        )
        await agent.on_reject(
            Event(
                type=EventType.ORDER_REJECTED,
                payload={"reason": "trading halted", "ticker": "T"},
            )
        )

    asyncio.run(run())
    kinds = [e["kind"] for e in agent.get_state()["recent_events"]]
    assert "proposal" in kinds
    assert "reject" in kinds
