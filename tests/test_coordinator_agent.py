"""Tests for PortfolioCoordinatorAgent."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from core.event_bus import Event, EventType
from agents.coordinator_agent import PortfolioCoordinatorAgent


def test_coordinator_passthrough_proposals():
    bus = MagicMock()
    bus.publish = AsyncMock()

    coordinator = PortfolioCoordinatorAgent(
        bus,
        agent_id="coord",
        universe=["T", "SBER"],
        ticker_agent_ids=["t_momentum", "sber_sma"],
        llm_enabled=False,
        rebalance_interval_ticks=1,
    )

    async def run():
        await coordinator.on_proposed_signal(
            Event(
                type=EventType.PROPOSED_SIGNAL,
                payload={
                    "agent_id": "t_momentum",
                    "ticker": "T",
                    "action": "buy",
                    "quantity": 5,
                    "reason": "momentum",
                    "strategy": "momentum",
                    "tick": 10,
                },
            )
        )
        await coordinator.on_bar_proposals_ready(
            Event(
                type=EventType.BAR_PROPOSALS_READY,
                payload={
                    "tick": 10,
                    "prices": {"T": 3000.0, "SBER": 250.0},
                    "trading_enabled": True,
                    "halted": False,
                    "datetime": "2025-01-03T07:05:00",
                    "bar_interval": "5m",
                    "agent_portfolios": {},
                },
            )
        )

    asyncio.run(run())

    bus.publish.assert_called_once()
    published = bus.publish.call_args[0][0]
    assert published.type == EventType.STRATEGY_SIGNAL
    assert published.payload["ticker"] == "T"
    assert published.payload["agent_id"] == "t_momentum"
    assert published.payload["quantity"] == 5


def test_coordinator_blocks_on_halt():
    bus = MagicMock()
    bus.publish = AsyncMock()

    coordinator = PortfolioCoordinatorAgent(
        bus,
        agent_id="coord",
        universe=["T"],
        ticker_agent_ids=["t_momentum"],
        llm_enabled=False,
    )

    async def run():
        await coordinator.on_proposed_signal(
            Event(
                type=EventType.PROPOSED_SIGNAL,
                payload={
                    "agent_id": "t_momentum",
                    "ticker": "T",
                    "action": "buy",
                    "quantity": 5,
                    "tick": 1,
                },
            )
        )
        await coordinator.on_bar_proposals_ready(
            Event(
                type=EventType.BAR_PROPOSALS_READY,
                payload={
                    "tick": 1,
                    "prading_enabled": True,
                    "halted": True,
                    "trading_enabled": True,
                    "prices": {"T": 3000.0},
                },
            )
        )

    asyncio.run(run())
    bus.publish.assert_not_called()
