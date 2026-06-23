"""Portfolio and backtest performance metrics."""

from __future__ import annotations

import math
from typing import Iterable


def total_return(initial: float, final: float) -> float:
    if initial <= 0:
        return 0.0
    return (final - initial) / initial


def max_drawdown(equity_curve: Iterable[float]) -> float:
    peak = None
    max_dd = 0.0
    for v in equity_curve:
        if peak is None or v > peak:
            peak = v
        if peak and peak > 0:
            dd = (peak - v) / peak
            max_dd = max(max_dd, dd)
    return max_dd


def sharpe_ratio(returns: list[float], periods_per_year: float = 252.0) -> float | None:
    if len(returns) < 2:
        return None
    mean_r = sum(returns) / len(returns)
    var = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    if var <= 0:
        return None
    std = math.sqrt(var)
    if std == 0:
        return None
    return (mean_r / std) * math.sqrt(periods_per_year)


def win_rate(trades: list[dict]) -> float | None:
    if not trades:
        return None
    wins = 0
    for t in trades:
        pnl = t.get("pnl")
        if pnl is not None:
            wins += 1 if float(pnl) > 0 else 0
    return wins / len(trades) if trades else None


def compute_backtest_metrics(
    *,
    initial_capital: float,
    final_value: float,
    equity_curve: list[float],
    trades: list[dict],
    agent_stats: dict | None = None,
) -> dict:
    returns = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i - 1]
        cur = equity_curve[i]
        if prev > 0:
            returns.append((cur - prev) / prev)

    sharpe = sharpe_ratio(returns)
    return {
        "initial_capital": initial_capital,
        "final_value": final_value,
        "total_return_pct": round(100 * total_return(initial_capital, final_value), 4),
        "max_drawdown_pct": round(100 * max_drawdown(equity_curve), 4),
        "sharpe_ratio": round(sharpe, 4) if sharpe is not None else None,
        "trade_count": len(trades),
        "win_rate_pct": round(100 * win_rate(trades), 2) if win_rate(trades) is not None else None,
        "agent_stats": agent_stats or {},
    }
