import pytest

from core.position_sizing import compute_order_quantity, compute_sell_quantity


def test_compute_order_quantity_basic():
    qty = compute_order_quantity(
        portfolio_value=1_000_000,
        cash=1_000_000,
        price=280.0,
        target_weight_pct=0.15,
        max_position_pct=0.25,
    )
    # 15% of 1M = 150k / 280 ≈ 535
    assert qty == 535


def test_compute_order_quantity_capped_by_max_position():
    qty = compute_order_quantity(
        portfolio_value=1_000_000,
        cash=1_000_000,
        price=100.0,
        target_weight_pct=0.30,
        max_position_pct=0.25,
        current_position_value=200_000,
    )
    # room = 25% - 20% = 5% = 50k / 100 = 500
    assert qty == 500


def test_compute_order_quantity_capped_by_cash():
    qty = compute_order_quantity(
        portfolio_value=1_000_000,
        cash=10_000,
        price=100.0,
        target_weight_pct=0.15,
        max_position_pct=0.25,
    )
    assert qty == 99


def test_compute_order_quantity_zero_price():
    assert compute_order_quantity(1_000_000, 1_000_000, 0, 0.15, 0.25) == 0


def test_compute_sell_quantity_full_position():
    assert compute_sell_quantity(0, 100) == 100
    assert compute_sell_quantity(50, 100) == 50
    assert compute_sell_quantity(150, 100) == 100
    assert compute_sell_quantity(10, 0) == 0
