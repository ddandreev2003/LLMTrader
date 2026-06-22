"""Simulation duration helpers."""

import pandas as pd

from core.data.orderlog_bars import resolve_sim_tick_count, tick_count_for_trading_days


def _sample_price_data():
    idx = pd.date_range("2025-01-03 10:00", periods=400, freq="5min")
    return pd.DataFrame({"T": range(400), "SBER": range(400)}, index=idx)


def test_tick_count_for_trading_days():
    df = _sample_price_data()
    n = tick_count_for_trading_days(df, 3)
    unique_days = len({ts.date() for ts in df.index})
    assert n <= len(df)
    assert n >= 1
    if unique_days >= 3:
        days_in_slice = len({ts.date() for ts in df.index[:n]})
        assert days_in_slice == 3


def test_resolve_sim_tick_count_env_override():
    df = _sample_price_data()
    n = resolve_sim_tick_count(df, {}, env_sim_n_ticks=50)
    assert n == 50


def test_resolve_sim_tick_count_min_trading_days():
    df = _sample_price_data()
    n = resolve_sim_tick_count(df, {"sim": {"min_trading_days": 5}})
    assert n > 0
    assert n <= len(df)
