"""Tests for backtest metrics."""

from core.metrics import compute_backtest_metrics, max_drawdown, total_return


def test_total_return_and_drawdown():
    assert total_return(100, 110) == 0.1
    assert max_drawdown([100, 120, 90, 95]) == 0.25


def test_compute_backtest_metrics():
    m = compute_backtest_metrics(
        initial_capital=1_000_000,
        final_value=1_050_000,
        equity_curve=[1_000_000, 1_010_000, 1_050_000],
        trades=[{"pnl": 100}, {"pnl": -50}],
    )
    assert m["total_return_pct"] == 5.0
    assert m["trade_count"] == 2
