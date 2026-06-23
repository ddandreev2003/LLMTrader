"""Numeric summaries of OHLC for text-only QuantAgent analysts."""

from __future__ import annotations

import numpy as np


def summarize_candlesticks(kline: dict) -> str:
    closes = kline.get("Close") or []
    opens = kline.get("Open") or []
    highs = kline.get("High") or []
    lows = kline.get("Low") or []
    n = len(closes)
    if n < 2:
        return "insufficient bars for pattern analysis"

    lines: list[str] = []
    start = max(0, n - 8)
    for i in range(start, n):
        body = closes[i] - opens[i]
        if abs(body) < 1e-9:
            direction = "doji"
        elif body > 0:
            direction = "bullish"
        else:
            direction = "bearish"
        span = highs[i] - lows[i]
        body_pct = (abs(body) / span * 100) if span > 0 else 0
        dt = (kline.get("Datetime") or [""] * n)[i]
        lines.append(
            f"{dt}: {direction} candle O={opens[i]:.4g} H={highs[i]:.4g} "
            f"L={lows[i]:.4g} C={closes[i]:.4g} (body {body_pct:.0f}% of range)"
        )

    if n >= 2:
        o1, c1 = opens[-2], closes[-2]
        o2, c2 = opens[-1], closes[-1]
        if c1 < o1 and c2 > o2 and c2 > o1 and o2 < c1:
            lines.append("hint: possible bullish engulfing on last bar")
        if c1 > o1 and c2 < o2 and c2 < o1 and o2 > c1:
            lines.append("hint: possible bearish engulfing on last bar")
        if closes[-1] > closes[-2] > closes[-3]:
            lines.append("hint: three consecutive higher closes")
        if closes[-1] < closes[-2] < closes[-3]:
            lines.append("hint: three consecutive lower closes")

    window = closes[-min(10, n) :]
    if len(window) >= 3:
        if window[-1] == max(window) and window[-1] > window[-2]:
            lines.append("hint: last close at local high of recent window")
        if window[-1] == min(window) and window[-1] < window[-2]:
            lines.append("hint: last close at local low of recent window")

    return "\n".join(lines)


def summarize_trend(kline: dict) -> str:
    closes = np.array(kline.get("Close") or [], dtype=float)
    highs = kline.get("High") or []
    lows = kline.get("Low") or []
    n = len(closes)
    if n < 5:
        return "insufficient bars for trend analysis"

    x = np.arange(n, dtype=float)
    slope = float(np.polyfit(x, closes, 1)[0])
    short = closes[-min(5, n) :]
    short_slope = float(np.polyfit(np.arange(len(short)), short, 1)[0]) if len(short) >= 2 else slope

    w = min(10, n)
    recent_low = float(min(lows[-w:]))
    recent_high = float(max(highs[-w:]))
    last = float(closes[-1])
    pos_in_range = (last - recent_low) / (recent_high - recent_low) if recent_high > recent_low else 0.5

    bias = "sideways"
    if short_slope > 0 and slope > 0:
        bias = "upward"
    elif short_slope < 0 and slope < 0:
        bias = "downward"

    return (
        f"full-window close slope: {slope:.6g} per bar\n"
        f"recent {len(short)}-bar slope: {short_slope:.6g} per bar\n"
        f"recent {w}-bar range: support ~{recent_low:.4g}, resistance ~{recent_high:.4g}\n"
        f"last close {last:.4g} ({pos_in_range * 100:.0f}% of range from support)\n"
        f"bias: {bias}"
    )
