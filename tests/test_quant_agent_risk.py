"""Risk filters for QuantAgentTickerAgent."""

import asyncio

from agents.quant_agent_ticker_agent import QuantAgentTickerAgent
from core.event_bus import Event, EventType


class DummyBus:
    def subscribe(self, *args):
        pass


def test_filter_blocks_quick_round_trip_sell():
    agent = QuantAgentTickerAgent(
        DummyBus(), agent_id="quant_t", ticker="T", params={"min_hold_bars": 8, "min_ta_score": 3}
    )
    agent._current_position = 100
    agent._bars_in_position = 2
    out = agent._filter_signal({"action": "sell", "quantity": 100}, ta_score=-4, min_score=3)
    assert out["action"] == "hold"
    assert "min hold" in out["reason"]


def test_filter_blocks_weak_buy():
    agent = QuantAgentTickerAgent(DummyBus(), agent_id="quant_t", ticker="T", params={"min_ta_score": 3})
    out = agent._filter_signal({"action": "buy", "quantity": 10}, ta_score=2, min_score=3)
    assert out["action"] == "hold"


def test_shock_exit_on_negative_impact():
    agent = QuantAgentTickerAgent(
        DummyBus(),
        agent_id="quant_gazp",
        ticker="GAZP",
        params={"shock_exit_impact_pct": -0.2},
    )
    agent._current_position = 1000
    agent._active_shocks = [
        {"type": "news_spike", "price_impact_pct": -0.8, "ticker": "GAZP", "_remaining_ticks": 5}
    ]
    exit_sig = agent._check_shock_exit({})
    assert exit_sig is not None
    assert exit_sig["action"] == "sell"
    assert exit_sig["quantity"] == 1000


def test_gazp_no_add_to_position():
    agent = QuantAgentTickerAgent(
        DummyBus(),
        agent_id="quant_gazp",
        ticker="GAZP",
        params={"no_add_to_position": True, "min_ta_score": 3},
    )
    agent._current_position = 500
    out = agent._filter_signal({"action": "buy", "quantity": 100}, ta_score=5, min_score=3)
    assert out["action"] == "hold"


def test_coordinator_blocks_buy_on_shock():
    from agents.coordinator_agent import PortfolioCoordinatorAgent

    coord = PortfolioCoordinatorAgent(
        DummyBus(),
        agent_id="c",
        universe=["GAZP"],
        ticker_agent_ids=["quant_gazp"],
        shock_block_buys=True,
        shock_block_buys_while_active=True,
    )
    coord._active_shocks = [{"type": "news_spike", "price_impact_pct": -0.8, "ticker": "GAZP"}]
    decisions = coord._apply_risk_rules(
        [
            {
                "agent_id": "quant_gazp",
                "ticker": "GAZP",
                "approved": True,
                "action": "buy",
                "quantity": 100,
            }
        ]
    )
    assert decisions[0]["approved"] is False


def test_coordinator_blocks_buy_on_positive_shock():
    from agents.coordinator_agent import PortfolioCoordinatorAgent

    coord = PortfolioCoordinatorAgent(
        DummyBus(),
        agent_id="c",
        universe=["T", "GAZP", "SBER"],
        ticker_agent_ids=["quant_t"],
        shock_block_buys=True,
        shock_block_buys_while_active=True,
    )
    coord._active_shocks = [
        {"type": "news_spike", "price_impact_pct": 0.3, "_remaining_ticks": 10}
    ]
    decisions = coord._apply_risk_rules(
        [
            {
                "agent_id": "quant_t",
                "ticker": "T",
                "approved": True,
                "action": "buy",
                "quantity": 70,
            }
        ]
    )
    assert decisions[0]["approved"] is False


def test_shock_take_profit_on_positive_impact():
    agent = QuantAgentTickerAgent(
        DummyBus(),
        agent_id="quant_t",
        ticker="T",
        params={
            "shock_take_profit_impact_pct": 0.15,
            "shock_take_profit_min_gain_pct": 0.002,
        },
    )
    agent._current_position = 70
    agent._entry_price = 1500.0
    agent._last_price = 1507.0
    agent._active_shocks = [
        {"type": "news_spike", "price_impact_pct": 0.3, "_remaining_ticks": 12}
    ]
    exit_sig = agent._check_shock_exit({})
    assert exit_sig is not None
    assert exit_sig["action"] == "sell"


def test_max_hold_exit():
    agent = QuantAgentTickerAgent(
        DummyBus(), agent_id="quant_t", ticker="T", params={"max_hold_bars": 28}
    )
    agent._current_position = 50
    agent._bars_in_position = 28
    exit_sig = agent._check_max_hold_exit()
    assert exit_sig is not None
    assert exit_sig["action"] == "sell"
