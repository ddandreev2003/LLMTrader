import asyncio

import pytest

from core.agent_portfolio import AgentPortfolio, AgentPortfolioRegistry
from core.event_bus import Event, EventType


class _Bus:
    pass


class _FakeAgent:
    def __init__(self, agent_id, tickers, strategy_name="sma_cross", targets=None):
        self.agent_id = agent_id
        self.ticker = tickers[0]
        self.universe = tickers
        self.portfolio_targets = targets or {}
        self.initial_capital = 100_000
        self.strategy = type("S", (), {"name": strategy_name})()


def test_multi_ticker_portfolio_value():
    pf = AgentPortfolio(
        "mix", ["SBER", "GAZP"], "manual", initial_cash=100_000,
        target_weights={"SBER": 0.6, "GAZP": 0.4},
    )
    pf.apply_fill("SBER", "buy", 100, 200.0, 0, "2024-01-01")
    pf.apply_fill("GAZP", "buy", 200, 50.0, 0, "2024-01-01")
    prices = {"SBER": 210.0, "GAZP": 52.0}
    assert pf.portfolio_value(prices) > 0
    stats = pf.get_stats(prices)
    assert "SBER" in stats["holdings"]
    assert "GAZP" in stats["holdings"]


def test_registry_multi_ticker_agent():
    bus = _Bus()
    agents = [
        _FakeAgent("mix", ["SBER", "GAZP"], targets={"SBER": 0.5, "GAZP": 0.5}),
    ]
    reg = AgentPortfolioRegistry(bus, agents, total_capital=100_000)

    async def run():
        reg._last_prices = {"SBER": 100.0, "GAZP": 50.0}
        await reg.on_order_filled(
            Event(
                type=EventType.ORDER_FILLED,
                payload={
                    "ticker": "SBER",
                    "agent_id": "mix",
                    "action": "buy",
                    "quantity": 100,
                    "price": 100.0,
                    "tick": 0,
                    "date": "2024-01-01",
                },
            )
        )

    asyncio.run(run())
    pf = reg.get_portfolio("mix")
    assert pf.positions["SBER"] == 100
    assert pf.positions["GAZP"] == 0
