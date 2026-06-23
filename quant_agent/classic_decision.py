"""Rule-based trade decision from classic TA reports — no LLM."""

from __future__ import annotations

import json
import re


def _bias_score(report: str, bullish_words: tuple[str, ...], bearish_words: tuple[str, ...]) -> int:
    text = report.lower()
    score = 0
    for w in bullish_words:
        if w in text:
            score += 1
    for w in bearish_words:
        if w in text:
            score -= 1
    m = re.search(r"overall \w+ bias:\s*(\w+)", text)
    if m:
        b = m.group(1)
        if b == "bullish":
            score += 2
        elif b == "bearish":
            score -= 2
    return score


def classic_trade_decision(
    indicator_report: str,
    pattern_report: str,
    trend_report: str,
    *,
    ticker: str = "",
    time_frame: str = "1min",
    entry_threshold: int = 3,
    forecast_bars: int = 3,
) -> tuple[str, int]:
    """Return (JSON string, TA score) compatible with bridge._parse_decision."""
    ind = _bias_score(
        indicator_report,
        ("bullish", "oversold", "positive momentum", "above signal"),
        ("bearish", "overbought", "negative momentum", "below signal"),
    )
    pat = _bias_score(
        pattern_report,
        ("bullish", "bottom", "hammer", "engulfing (confirmed", "white soldiers", "higher highs"),
        ("bearish", "top", "shooting star", "black crows", "lower lows"),
    )
    tr = _bias_score(
        trend_report,
        ("upward", "above sma", "breakout up", "bullish pressure"),
        ("downward", "below sma", "breakdown", "bearish pressure"),
    )

    total = ind + pat + tr
    thr = max(2, int(entry_threshold))
    if total >= thr:
        decision = "LONG"
        justification = (
            f"Classic TA score +{total} (ind {ind:+d}, pat {pat:+d}, trend {tr:+d}) — "
            f"aligned bullish signals on {ticker} {time_frame}."
        )
    elif total <= -thr:
        decision = "SHORT"
        justification = (
            f"Classic TA score {total} (ind {ind:+d}, pat {pat:+d}, trend {tr:+d}) — "
            f"aligned bearish signals on {ticker} {time_frame}."
        )
    else:
        decision = "HOLD"
        justification = (
            f"Classic TA score {total:+d} (ind {ind:+d}, pat {pat:+d}, trend {tr:+d}) — "
            "mixed or weak signals, no trade."
        )

    payload = json.dumps(
        {
            "forecast_horizon": f"next {max(1, int(forecast_bars))} bars ({time_frame})",
            "decision": decision,
            "justification": justification,
            "risk_reward_ratio": 1.5,
            "ta_score": total,
        },
        ensure_ascii=False,
    )
    return payload, total


def create_classic_decision_agent():
    def decision_agent_node(state):
        raw, _score = classic_trade_decision(
            state.get("indicator_report", ""),
            state.get("pattern_report", ""),
            state.get("trend_report", ""),
            ticker=str(state.get("stock_name", "")),
            time_frame=state.get("time_frame", "1min"),
        )
        return {"final_trade_decision": raw, "messages": []}

    return decision_agent_node
