"""Compute rebalance orders toward manual target weights."""

from __future__ import annotations

import math


def compute_rebalance_orders(
    positions: dict[str, int],
    prices: dict[str, float],
    portfolio_value: float,
    cash: float,
    target_weights: dict[str, float],
    commission_pct: float = 0.0003,
    threshold_pct: float = 0.03,
) -> list[dict]:
    """
    Return buy/sell orders to move positions toward target_weights.

    threshold_pct: min weight drift before trading (e.g. 0.03 = 3pp).
    Sells are returned before buys so cash is freed first.
    """
    if portfolio_value <= 0 or not target_weights:
        return []

    sells: list[dict] = []
    buys: list[dict] = []

    for ticker, target_w in target_weights.items():
        price = prices.get(ticker, 0.0)
        if price <= 0:
            continue

        current_value = positions.get(ticker, 0) * price
        current_w = current_value / portfolio_value
        drift = target_w - current_w

        if abs(drift) < threshold_pct:
            continue

        target_value = portfolio_value * target_w
        delta_value = target_value - current_value

        if delta_value < 0:
            qty = min(positions.get(ticker, 0), math.ceil(abs(delta_value) / price))
            if qty > 0:
                sells.append(
                    {
                        "ticker": ticker,
                        "action": "sell",
                        "quantity": qty,
                        "reason": f"ребаланс: {current_w:.1%} → {target_w:.1%}",
                    }
                )
        else:
            max_spend = delta_value
            affordable = cash / (1 + commission_pct) if cash > 0 else 0
            order_value = min(max_spend, affordable)
            qty = math.floor(order_value / price)
            if qty > 0:
                buys.append(
                    {
                        "ticker": ticker,
                        "action": "buy",
                        "quantity": qty,
                        "reason": f"ребаланс: {current_w:.1%} → {target_w:.1%}",
                    }
                )
                cash -= qty * price * (1 + commission_pct)

    return sells + buys
