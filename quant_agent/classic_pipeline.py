"""Fast classic-TA pipeline — no LLM, no LangGraph overhead."""

from __future__ import annotations

from quant_agent.classic_analysts import analyze_patterns, analyze_trend
from quant_agent.classic_decision import classic_trade_decision
from quant_agent.classic_indicators import analyze_indicators
from quant_agent.bridge import _parse_decision, decision_to_signal


def run_classic_quant_decision(
    ticker: str,
    kline: dict,
    time_frame: str = "1min",
    entry_threshold: int = 3,
    forecast_bars: int = 3,
) -> dict:
    indicator = analyze_indicators(kline)
    pattern = analyze_patterns(kline)
    trend = analyze_trend(kline)
    raw, ta_score = classic_trade_decision(
        indicator,
        pattern,
        trend,
        ticker=ticker,
        time_frame=time_frame,
        entry_threshold=entry_threshold,
        forecast_bars=forecast_bars,
    )
    parsed = _parse_decision(raw)
    return {
        "decision": parsed.get("decision", "HOLD"),
        "justification": parsed.get("justification", ""),
        "risk_reward_ratio": parsed.get("risk_reward_ratio"),
        "ta_score": ta_score,
        "signal": decision_to_signal(parsed),
        "reports": {
            "indicator": indicator,
            "pattern": pattern,
            "trend": trend,
            "raw_decision": raw,
        },
    }
