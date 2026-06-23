"""Simulated market maker: quotes around mid, inventory and spread capture."""

from __future__ import annotations

from dataclasses import dataclass, field

from core.orderbook import OrderBookState


@dataclass
class MMQuote:
    bid_price: float
    ask_price: float
    bid_size: float
    ask_size: float
    mid: float
    spread: float


@dataclass
class MMInventory:
    position: float = 0.0
    cash_pnl: float = 0.0
    trades: int = 0


class MarketMaker:
    """Two-sided quotes per ticker; simulates fills when bar range crosses quotes."""

    def __init__(
        self,
        tickers: list[str],
        spread_bps: float = 8.0,
        quote_size: float = 50.0,
        max_inventory: float = 5000.0,
        skew_bps_per_unit: float = 0.5,
    ):
        self.tickers = tickers
        self.spread_bps = spread_bps
        self.quote_size = quote_size
        self.max_inventory = max_inventory
        self.skew_bps_per_unit = skew_bps_per_unit
        self._quotes: dict[str, MMQuote] = {}
        self._inventory: dict[str, MMInventory] = {t: MMInventory() for t in tickers}
        self._fill_history: list[dict] = []

    def _record_fill(self, ticker: str, tick: int, fill: dict) -> None:
        self._fill_history.append(
            {
                "ticker": ticker,
                "tick": tick,
                "action": fill["action"],
                "price": fill["price"],
                "quantity": fill["quantity"],
                "reason": fill.get("reason", ""),
            }
        )
        if len(self._fill_history) > 500:
            self._fill_history = self._fill_history[-500:]

    def recent_fills(self, n: int = 20) -> list[dict]:
        return list(self._fill_history[-n:])

    def _mid_from_book(self, book: OrderBookState, fallback: float) -> float:
        """Prefer bar close (fallback); use book mid only when consistent with it."""
        ref = fallback if fallback > 0 else None
        bid, ask = book.best_bid(), book.best_ask()
        if bid and ask and ask >= bid:
            mid = (bid + ask) / 2
            if ref is None or 0.85 <= mid / ref <= 1.15:
                return mid
        if book.last_trade_price and book.last_trade_price > 0:
            lt = book.last_trade_price
            if ref is None or 0.85 <= lt / ref <= 1.15:
                return lt
        return ref if ref and ref > 0 else (book.mid_price() or 0.01)

    def update_quotes(self, ticker: str, book: OrderBookState, ref_price: float) -> MMQuote:
        mid = self._mid_from_book(book, ref_price)
        inv = self._inventory[ticker].position
        skew = (inv / max(self.max_inventory, 1)) * self.skew_bps_per_unit
        half_spread = mid * (self.spread_bps + skew) / 10_000 / 2
        bid = max(mid - half_spread, 0.01)
        ask = max(mid + half_spread, bid + 0.01)
        q = MMQuote(
            bid_price=round(bid, 4),
            ask_price=round(ask, 4),
            bid_size=self.quote_size,
            ask_size=self.quote_size,
            mid=round(mid, 4),
            spread=round(ask - bid, 4),
        )
        self._quotes[ticker] = q
        return q

    def on_bar(
        self,
        ticker: str,
        *,
        open_: float,
        high: float,
        low: float,
        close: float,
        tick: int = 0,
    ) -> list[dict]:
        """Simulate fills if bar range traded through our quotes. Returns fill records."""
        q = self._quotes.get(ticker)
        if not q:
            return []
        inv = self._inventory[ticker]
        fills: list[dict] = []

        if low <= q.bid_price and inv.position < self.max_inventory:
            qty = min(self.quote_size, self.max_inventory - inv.position)
            if qty > 0:
                inv.position += qty
                inv.cash_pnl -= qty * q.bid_price
                inv.trades += 1
                fills.append(
                    {"action": "buy", "price": q.bid_price, "quantity": qty, "reason": "mm bid hit"}
                )
                self._record_fill(ticker, tick, fills[-1])

        if high >= q.ask_price and inv.position > 0:
            qty = min(self.quote_size, inv.position)
            if qty > 0:
                inv.position -= qty
                inv.cash_pnl += qty * q.ask_price
                inv.trades += 1
                fills.append(
                    {"action": "sell", "price": q.ask_price, "quantity": qty, "reason": "mm ask hit"}
                )
                self._record_fill(ticker, tick, fills[-1])

        return fills

    def merge_book_snapshot(self, ticker: str, native: dict, quote: MMQuote | None = None) -> dict:
        """Merge MM quotes into orderbook snapshot for viz."""
        q = quote or self._quotes.get(ticker)
        if not q:
            return native
        out = dict(native)
        bids = list(out.get("bids") or [])
        asks = list(out.get("asks") or [])
        bids.insert(0, {"price": q.bid_price, "volume": q.bid_size, "mm": True})
        asks.insert(0, {"price": q.ask_price, "volume": q.ask_size, "mm": True})
        out["bids"] = bids[:6]
        out["asks"] = asks[:6]
        out["best_bid"] = bids[0]["price"] if bids else None
        out["best_ask"] = asks[0]["price"] if asks else None
        out["mid"] = q.mid
        out["spread"] = q.spread
        out["mm_quote"] = {
            "bid": q.bid_price,
            "ask": q.ask_price,
            "spread": q.spread,
            "bid_size": q.bid_size,
            "ask_size": q.ask_size,
        }
        inv = self._inventory.get(ticker)
        if inv:
            out["mm_inventory"] = inv.position
            out["mm_trades"] = inv.trades
        return out

    def snapshot_all(self) -> dict[str, dict]:
        return {t: self.merge_book_snapshot(t, {"ticker": t}) for t in self.tickers}

    def get_quote(self, ticker: str) -> MMQuote | None:
        return self._quotes.get(ticker)

    def inventory_summary(self) -> dict[str, dict]:
        return {
            t: {
                "position": inv.position,
                "trades": inv.trades,
                "cash_pnl": round(inv.cash_pnl, 2),
            }
            for t, inv in self._inventory.items()
        }


def market_maker_from_config(portfolio_cfg: dict, tickers: list[str]) -> MarketMaker | None:
    mm_cfg = portfolio_cfg.get("market_maker") or {}
    if not mm_cfg.get("enabled", False):
        return None
    return MarketMaker(
        tickers,
        spread_bps=float(mm_cfg.get("spread_bps", 8.0)),
        quote_size=float(mm_cfg.get("quote_size", 50.0)),
        max_inventory=float(mm_cfg.get("max_inventory", 5000.0)),
        skew_bps_per_unit=float(mm_cfg.get("skew_bps_per_unit", 0.5)),
    )
