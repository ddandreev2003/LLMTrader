"""Tests for MarketMaker quotes and inventory."""

from core.market_maker import MarketMaker, market_maker_from_config
from core.orderbook import OrderBookState


def test_market_maker_quotes_around_mid():
    mm = MarketMaker(["T"], spread_bps=10, quote_size=10)
    book = OrderBookState(ticker="T")
    book.add_order(1, "B", 100.0, 100)
    book.add_order(2, "S", 102.0, 100)
    q = mm.update_quotes("T", book, 101.0)
    assert q.bid_price < q.mid < q.ask_price
    assert q.spread > 0


def test_market_maker_fill_on_bar_cross():
    mm = MarketMaker(["T"], spread_bps=20, quote_size=10, max_inventory=100)
    book = OrderBookState(ticker="T")
    book.add_order(1, "B", 100.0, 100)
    book.add_order(2, "S", 100.5, 100)
    mm.update_quotes("T", book, 100.25)
    fills = mm.on_bar("T", open_=100.0, high=100.6, low=99.5, close=100.3, tick=5)
    assert any(f["action"] == "buy" for f in fills)
    assert mm.recent_fills(5)
    assert mm.recent_fills(5)[0]["tick"] == 5


def test_market_maker_from_config():
    cfg = {
        "market_maker": {"enabled": True, "spread_bps": 5},
        "assets": [{"ticker": "T"}, {"ticker": "GAZP"}],
    }
    mm = market_maker_from_config(cfg, ["T", "GAZP"])
    assert mm is not None
    assert mm.tickers == ["T", "GAZP"]

    cfg["market_maker"]["enabled"] = False
    assert market_maker_from_config(cfg, ["T"]) is None


def test_orderbook_ignores_zero_price_levels():
    book = OrderBookState("T")
    book.add_order(1, "B", 2750.0, 100)
    book.add_order(2, "S", 0.0, 1)
    book.add_order(3, "S", 2755.0, 50)
    assert book.best_ask() == 2755.0
    assert book.mid_price() == 2752.5


def test_market_maker_uses_bar_close_when_book_mid_corrupt():
    mm = MarketMaker(["T"], spread_bps=8, quote_size=10)
    book = OrderBookState(ticker="T")
    book.add_order(1, "B", 3005.0, 100)
    book.add_order(2, "S", 0.0, 1)
    bar_close = 2755.0
    q = mm.update_quotes("T", book, bar_close)
    assert 2700 < q.mid < 2800
    assert q.bid_price < bar_close < q.ask_price
    fills = mm.on_bar("T", open_=2754, high=2756, low=2753, close=bar_close)
    assert fills, "MM should fill when quotes align with bar range"


def test_merge_book_snapshot_marks_mm_levels():
    mm = MarketMaker(["T"])
    book = OrderBookState(ticker="T")
    book.add_order(1, "B", 50.0, 10)
    book.add_order(2, "S", 51.0, 10)
    q = mm.update_quotes("T", book, 50.5)
    snap = mm.merge_book_snapshot("T", book.snapshot(), q)
    assert snap["bids"][0]["mm"] is True
    assert snap["mm_quote"]["bid"] == q.bid_price
