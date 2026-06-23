"""Classic technical indicators via TA-Lib — no LLM tool loops."""

from __future__ import annotations

import pandas as pd
import talib


def analyze_indicators(kline: dict) -> str:
    closes = kline.get("Close") or []
    if len(closes) < 14:
        return "Indicator report: insufficient data (< 14 bars)."

    df = pd.DataFrame(kline)
    c = df["Close"].astype(float)
    h = df["High"].astype(float)
    l = df["Low"].astype(float)

    rsi = talib.RSI(c, timeperiod=14)
    macd, macd_sig, macd_hist = talib.MACD(c, fastperiod=12, slowperiod=26, signalperiod=9)
    stoch_k, stoch_d = talib.STOCH(h, l, c, fastk_period=14, slowk_period=3, slowd_period=3)
    roc = talib.ROC(c, timeperiod=10)
    willr = talib.WILLR(h, l, c, timeperiod=14)

    def _last(series) -> float:
        s = series.dropna()
        return float(s.iloc[-1]) if len(s) else 0.0

    r = _last(rsi)
    m = _last(macd)
    ms = _last(macd_sig)
    mh = _last(macd_hist)
    sk = _last(stoch_k)
    sd = _last(stoch_d)
    rc = _last(roc)
    wr = _last(willr)

    signals: list[str] = []
    if r >= 70:
        signals.append(f"RSI {r:.1f} — overbought")
    elif r <= 30:
        signals.append(f"RSI {r:.1f} — oversold")
    else:
        signals.append(f"RSI {r:.1f} — neutral")

    if mh > 0 and m > ms:
        signals.append("MACD bullish (hist > 0, line above signal)")
    elif mh < 0 and m < ms:
        signals.append("MACD bearish (hist < 0, line below signal)")
    else:
        signals.append(f"MACD mixed (hist {mh:.4g})")

    if sk >= 80:
        signals.append(f"Stoch %K {sk:.1f} — overbought")
    elif sk <= 20:
        signals.append(f"Stoch %K {sk:.1f} — oversold")
    else:
        signals.append(f"Stoch %K/%D {sk:.1f}/{sd:.1f}")

    if rc > 0.5:
        signals.append(f"ROC {rc:.2f}% — positive momentum")
    elif rc < -0.5:
        signals.append(f"ROC {rc:.2f}% — negative momentum")

    if wr >= -20:
        signals.append(f"Williams %R {wr:.1f} — overbought zone")
    elif wr <= -80:
        signals.append(f"Williams %R {wr:.1f} — oversold zone")

    bull = sum(1 for s in signals if "bull" in s.lower() or "oversold" in s.lower() or "positive" in s.lower())
    bear = sum(1 for s in signals if "bear" in s.lower() or "overbought" in s.lower() or "negative" in s.lower())
    bias = "neutral"
    if bull > bear + 1:
        bias = "bullish"
    elif bear > bull + 1:
        bias = "bearish"

    lines = ["Indicator report (classic TA-Lib, rule-based):"]
    lines.extend(f"- {s}" for s in signals)
    lines.append(f"Overall indicator bias: {bias}")
    return "\n".join(lines)


def create_classic_indicator_agent():
    def indicator_agent_node(state):
        report = analyze_indicators(state["kline_data"])
        return {"indicator_report": report, "messages": []}

    return indicator_agent_node
