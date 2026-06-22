"""Integration test: proposal → coordinator → market fill."""

import asyncio

import pandas as pd

from agents.coordinator_agent import PortfolioCoordinatorAgent
from core.agent_portfolio import AgentPortfolioRegistry
from core.event_bus import Event, EventType
from core.intraday_market import IntradayMarketEngine
from agents.ticker_strategy_agent import TickerStrategyAgent


def test_proposal_coordinator_fill_pipeline():
    idx = pd.date_range("2025-01-03 07:05", periods=2, freq="5min")
    bars = pd.DataFrame(
        {"T": [3000, 3005], "LQDT": [1.5, 1.51], "SBER": [250, 251]},
        index=idx,
    )
    filled = []

    class SyncBus:
        def __init__(self):
            self._handlers = {}

        def subscribe(self, event_type, handler):
            self._handlers.setdefault(event_type, []).append(handler)

        async def publish(self, event):
            for handler in self._handlers.get(event.type, []):
                await handler(event)
            if event.type == EventType.ORDER_FILLED:
                filled.append(event.payload)

        async def drain(self):
            pass

        def stop(self):
            pass

    async def run():
        bus = SyncBus()
        agent = TickerStrategyAgent(
            bus, "t_momentum", "T", "momentum", llm_enabled=False, proposal_mode=True
        )
        agent.initial_capital = 100_000
        coordinator = PortfolioCoordinatorAgent(
            bus,
            agent_id="tls_coordinator",
            universe=["T", "LQDT", "SBER"],
            ticker_agent_ids=["t_momentum"],
            llm_enabled=False,
            rebalance_interval_ticks=1,
        )
        portfolio = AgentPortfolioRegistry(bus, agents=[agent], total_capital=100_000)
        portfolio.register()
        coordinator.register()

        engine = IntradayMarketEngine(
            bus, bars, portfolio=portfolio, warmup_bars=0, trade_cutoff_bars=0
        )
        engine._ensure_subscribed()
        engine._current_tick = 0
        engine._prices = {"T": 3000.0, "LQDT": 1.5, "SBER": 250.0}

        await bus.publish(
            Event(
                type=EventType.PROPOSED_SIGNAL,
                payload={
                    "agent_id": "t_momentum",
                    "ticker": "T",
                    "action": "buy",
                    "quantity": 2,
                    "reason": "test",
                    "strategy": "momentum",
                    "tick": 0,
                },
            )
        )
        await bus.publish(
            Event(
                type=EventType.BAR_PROPOSALS_READY,
                payload={
                    "tick": 0,
                    "prices": {"T": 3000.0, "LQDT": 1.5, "SBER": 250.0},
                    "trading_enabled": True,
                    "halted": False,
                    "datetime": "2025-01-03T07:05:00",
                    "bar_interval": "5m",
                    "agent_portfolios": {},
                },
            )
        )

    asyncio.run(run())
    assert len(filled) == 1
    assert filled[0]["ticker"] == "T"
    assert filled[0]["quantity"] == 2
