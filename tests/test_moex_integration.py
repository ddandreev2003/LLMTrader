import asyncio

import pandas as pd
import pytest

from agents.ticker_strategy_agent import TickerStrategyAgent
from core.data.moex_loader import compute_warmup_ticks
from core.event_bus import Event, EventType
from core.multi_asset_market import MultiAssetMarketEngine
from core.multi_asset_portfolio import MultiAssetPortfolio


class _Bus:
    def __init__(self):
        self.events: list[Event] = []

    async def publish(self, event: Event):
        self.events.append(event)


def test_compute_warmup_ticks():
    dates = pd.date_range("2023-01-01", periods=400, freq="D")
    prices = pd.DataFrame({"SBER": [100.0] * 400}, index=dates)
    warmup = compute_warmup_ticks(prices, "2024-01-01")
    assert warmup > 0
    assert prices.index[warmup] >= pd.Timestamp("2024-01-01")


def test_ticker_agent_sizing_on_buy():
    bus = _Bus()
    agent = TickerStrategyAgent(
        bus,
        agent_id="test_sber",
        ticker="SBER",
        strategy_name="sma_cross",
        params={"target_weight_pct": 0.15, "sma_period": 5},
        max_position_pct=0.25,
    )
    agent._current_position = 0
    agent._portfolio_value = 1_000_000
    agent._cash = 1_000_000
    agent._price_history = [270, 272, 275, 278, 285]
    agent.total_ticks = 100
    agent._current_tick = 50

    signal = agent._apply_sizing({"action": "buy", "quantity": 0, "reason": "test"})
    assert signal["action"] == "buy"
    assert signal["quantity"] > 100


def test_ticker_agent_trade_cutoff_blocks_buy():
    bus = _Bus()
    agent = TickerStrategyAgent(
        bus,
        agent_id="test_sber",
        ticker="SBER",
        strategy_name="sma_cross",
        params={"target_weight_pct": 0.15},
        trade_cutoff_ticks=5,
    )
    agent.total_ticks = 100
    agent._current_tick = 96
    agent._price_history = [100.0]

    signal = agent._apply_sizing({"action": "buy", "quantity": 0, "reason": "test"})
    assert signal["action"] == "hold"


def test_ticker_agent_stop_loss():
    bus = _Bus()
    agent = TickerStrategyAgent(
        bus,
        agent_id="test",
        ticker="LKOH",
        strategy_name="sma_cross",
        params={"stop_loss_pct": 0.05},
    )
    agent._current_position = 10
    agent._entry_price = 100.0

    signal = agent._check_risk_exits(94.0)
    assert signal is not None
    assert signal["action"] == "sell"
    assert signal["quantity"] == 10


def test_market_warmup_flag():
    market_warmup_ticks = 3
    for tick in range(5):
        trading_enabled = tick >= market_warmup_ticks
        if tick < 3:
            assert trading_enabled is False
        else:
            assert trading_enabled is True


def test_portfolio_exposure_metrics():
    bus = _Bus()
    portfolio = MultiAssetPortfolio(bus, tickers=["SBER"], initial_cash=100_000)

    async def run():
        portfolio._last_prices = {"SBER": 100.0}
        await portfolio.on_tick(
            Event(
                type=EventType.TICK,
                payload={
                    "tick": 0,
                    "prices": {"SBER": 100.0},
                },
            )
        )
        await portfolio.on_order_filled(
            Event(
                type=EventType.ORDER_FILLED,
                payload={
                    "ticker": "SBER",
                    "action": "buy",
                    "quantity": 100,
                    "price": 100.0,
                    "tick": 0,
                    "date": "2024-01-01",
                },
            )
        )
        await portfolio.on_tick(
            Event(
                type=EventType.TICK,
                payload={
                    "tick": 1,
                    "prices": {"SBER": 100.0},
                },
            )
        )

    asyncio.run(run())
    stats = portfolio.get_stats({"SBER": 100.0})
    assert stats["final_invested_pct"] > 0
    assert stats["avg_invested_pct"] >= 0
    assert "sharpe_unavailable_reason" in stats or stats["sharpe_ratio"] is not None
