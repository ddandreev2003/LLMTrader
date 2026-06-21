import pytest

from core.portfolio_rebalance import compute_rebalance_orders


def test_rebalance_buy_when_underweight():
    orders = compute_rebalance_orders(
        positions={"SBER": 0, "GAZP": 0},
        prices={"SBER": 100.0, "GAZP": 50.0},
        portfolio_value=100_000,
        cash=100_000,
        target_weights={"SBER": 0.5, "GAZP": 0.5},
        threshold_pct=0.01,
    )
    assert any(o["ticker"] == "SBER" and o["action"] == "buy" for o in orders)
    assert any(o["ticker"] == "GAZP" and o["action"] == "buy" for o in orders)


def test_rebalance_sell_when_overweight():
    orders = compute_rebalance_orders(
        positions={"SBER": 1000, "GAZP": 0},
        prices={"SBER": 100.0, "GAZP": 50.0},
        portfolio_value=100_000,
        cash=0,
        target_weights={"SBER": 0.5, "GAZP": 0.5},
        threshold_pct=0.01,
    )
    assert orders[0]["action"] == "sell"
    assert orders[0]["ticker"] == "SBER"


def test_no_rebalance_within_threshold():
    orders = compute_rebalance_orders(
        positions={"SBER": 500},
        prices={"SBER": 100.0},
        portfolio_value=100_000,
        cash=50_000,
        target_weights={"SBER": 0.5},
        threshold_pct=0.05,
    )
    assert orders == []
