"""Portfolio-based position sizing for strategy agents."""

from __future__ import annotations

import math


def compute_order_quantity(
    portfolio_value: float,
    cash: float,
    price: float,
    target_weight_pct: float,
    max_position_pct: float,
    current_position_value: float = 0.0,
    commission_pct: float = 0.0003,
) -> int:
    """
    Compute buy quantity from target portfolio weight.

    Caps by max_position_pct per ticker and available cash (incl. commission buffer).
    """
    if price <= 0 or portfolio_value <= 0 or target_weight_pct <= 0:
        return 0

    target_value = portfolio_value * target_weight_pct
    max_position_value = portfolio_value * max_position_pct
    room_value = max(0.0, max_position_value - current_position_value)
    order_value = min(target_value, room_value)

    if cash > 0:
        max_by_cash = cash / (1.0 + commission_pct)
        order_value = min(order_value, max_by_cash)

    return max(0, math.floor(order_value / price))


def compute_sell_quantity(
    signal_quantity: int,
    current_position: int,
) -> int:
    """Sell up to signal quantity, never more than held."""
    if current_position <= 0:
        return 0
    if signal_quantity <= 0:
        return current_position
    return min(signal_quantity, current_position)
