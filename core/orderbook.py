"""Reconstructed order book from MOEX OrderLog events (ACTION 0/1/2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


Side = Literal["B", "S"]


@dataclass
class OrderLevel:
    price: float
    volume: float


@dataclass
class OrderBookState:
    """Per-ticker order book built from add/cancel/trade events."""

    ticker: str
    _orders: dict[int, tuple[Side, float, float]] = field(default_factory=dict)
    _bids: dict[float, float] = field(default_factory=dict)
    _asks: dict[float, float] = field(default_factory=dict)
    last_trade_price: float | None = None
    last_trade_volume: float = 0.0

    def add_order(self, order_no: int, side: Side, price: float, volume: float) -> None:
        if order_no in self._orders:
            self.cancel_order(order_no)
        self._orders[order_no] = (side, price, volume)
        book = self._bids if side == "B" else self._asks
        book[price] = book.get(price, 0.0) + volume

    def cancel_order(self, order_no: int) -> None:
        entry = self._orders.pop(order_no, None)
        if not entry:
            return
        side, price, volume = entry
        book = self._bids if side == "B" else self._asks
        if price in book:
            book[price] -= volume
            if book[price] <= 0:
                del book[price]

    def apply_trade(self, price: float, volume: float) -> None:
        self.last_trade_price = price
        self.last_trade_volume = volume

    def _valid_prices(self, book: dict[float, float]) -> dict[float, float]:
        return {p: v for p, v in book.items() if p > 0 and v > 0}

    def best_bid(self) -> float | None:
        bids = self._valid_prices(self._bids)
        if not bids:
            return None
        return max(bids.keys())

    def best_ask(self) -> float | None:
        asks = self._valid_prices(self._asks)
        if not asks:
            return None
        return min(asks.keys())

    def mid_price(self) -> float | None:
        bid, ask = self.best_bid(), self.best_ask()
        if bid is not None and ask is not None and ask >= bid:
            return (bid + ask) / 2
        if self.last_trade_price is not None and self.last_trade_price > 0:
            return self.last_trade_price
        return bid if bid and bid > 0 else (ask if ask and ask > 0 else None)

    def spread(self) -> float | None:
        bid, ask = self.best_bid(), self.best_ask()
        if bid is not None and ask is not None:
            return ask - bid
        return None

    def top_levels(self, n: int = 5) -> dict:
        bids = sorted(self._valid_prices(self._bids).items(), key=lambda x: x[0], reverse=True)[:n]
        asks = sorted(self._valid_prices(self._asks).items(), key=lambda x: x[0])[:n]
        return {
            "bids": [{"price": p, "volume": v} for p, v in bids],
            "asks": [{"price": p, "volume": v} for p, v in asks],
            "best_bid": self.best_bid(),
            "best_ask": self.best_ask(),
            "mid": self.mid_price(),
            "spread": self.spread(),
            "last_trade": self.last_trade_price,
        }

    def snapshot(self, n: int = 5) -> dict:
        return {"ticker": self.ticker, **self.top_levels(n)}
