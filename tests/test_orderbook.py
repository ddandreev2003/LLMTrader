"""Tests for OrderBookState."""

from core.orderbook import OrderBookState


def test_add_cancel_trade():
    book = OrderBookState("SBER")
    book.add_order(1, "B", 250.0, 100)
    book.add_order(2, "S", 251.0, 50)
    assert book.best_bid() == 250.0
    assert book.best_ask() == 251.0
    book.apply_trade(250.5, 10)
    assert book.last_trade_price == 250.5
    book.cancel_order(1)
    assert book.best_bid() is None


def test_snapshot_top_levels():
    book = OrderBookState("T")
    book.add_order(10, "B", 3000.0, 5)
    book.add_order(11, "B", 2999.0, 3)
    book.add_order(20, "S", 3001.0, 4)
    book.add_order(21, "S", 0.0, 1)
    snap = book.snapshot(2)
    assert snap["ticker"] == "T"
    assert len(snap["bids"]) == 2
    assert len(snap["asks"]) == 1
    assert snap["best_ask"] == 3001.0
    assert snap["spread"] == 1.0
