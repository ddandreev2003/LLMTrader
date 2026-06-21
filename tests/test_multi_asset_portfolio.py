import asyncio

import pytest

from core.event_bus import Event, EventType
from core.multi_asset_portfolio import MultiAssetPortfolio


class _Bus:
    pass


@pytest.mark.parametrize("action,qty,price,expected_reject", [
    ("buy", 10000, 100.0, True),
    ("buy", 1, 100.0, False),
])
def test_portfolio_rejects_oversized_buy(action, qty, price, expected_reject):
    bus = _Bus()
    portfolio = MultiAssetPortfolio(
        bus, tickers=["SBER"], initial_cash=10_000, max_position_pct=0.25
    )
    portfolio._last_prices = {"SBER": price}

    async def run():
        ev = Event(
            type=EventType.ORDER_PLACED,
            payload={"ticker": "SBER", "action": action, "quantity": qty, "price": price, "tick": 0},
        )
        return await portfolio.on_order_request(ev)

    rejection = asyncio.run(run())
    if expected_reject:
        assert rejection is not None
    else:
        assert rejection is None


def test_portfolio_cash_never_negative_after_fill():
    bus = _Bus()
    portfolio = MultiAssetPortfolio(bus, tickers=["SBER"], initial_cash=10_000)

    async def run():
        portfolio._last_prices = {"SBER": 100.0}
        await portfolio.on_order_filled(
            Event(
                type=EventType.ORDER_FILLED,
                payload={
                    "ticker": "SBER",
                    "action": "buy",
                    "quantity": 10,
                    "price": 100.0,
                    "tick": 0,
                    "date": "2024-01-01",
                },
            )
        )

    asyncio.run(run())
    assert portfolio.cash >= 0
    assert portfolio.positions["SBER"] == 10
