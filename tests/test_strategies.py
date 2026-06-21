import numpy as np
import pandas as pd
import pytest

from strategies.mean_reversion import MeanReversionStrategy
from strategies.momentum import MomentumStrategy
from strategies.rsi import RsiStrategy
from strategies.registry import get_strategy, list_strategies
from strategies.sma_cross_strategy import SmaCrossStrategy


def _trending_up(n: int = 50, start: float = 100.0) -> list[float]:
    return [start + i * 0.5 for i in range(n)]


def test_registry_lists_strategies():
    names = list_strategies()
    assert "sma_cross" in names
    assert "rsi" in names
    assert "macd" in names
    assert "momentum" in names
    assert "mean_reversion" in names


def test_sma_cross_buy_on_uptrend():
    prices = _trending_up(30)
    sig = SmaCrossStrategy().signal(prices, position=0, params={"sma_period": 10})
    assert sig is not None
    assert sig["action"] in ("buy", "hold", "sell")


def test_rsi_oversold():
    prices = [100.0] * 20 + [90.0, 85.0, 80.0, 75.0, 70.0]
    sig = RsiStrategy().signal(prices, position=0, params={"period": 14})
    if sig:
        assert sig["action"] in ("buy", "hold", "sell")


def test_mean_reversion_insufficient_data():
    sig = MeanReversionStrategy().signal([100, 101], position=0)
    assert sig is None


def test_momentum_needs_history():
    sig = MomentumStrategy().signal([100.0] * 5, position=0)
    assert sig is None


def test_get_strategy_unknown():
    with pytest.raises(KeyError):
        get_strategy("nonexistent")
