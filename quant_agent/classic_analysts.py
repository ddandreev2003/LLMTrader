"""Classic rule-based pattern and trend analysis — no LLM, no chart images."""

from __future__ import annotations

import numpy as np


def _arr(kline: dict, key: str) -> np.ndarray:
    return np.array(kline.get(key) or [], dtype=float)


def _body(o: float, c: float) -> float:
    return c - o


def _range(h: float, l: float) -> float:
    return max(h - l, 1e-12)


def _is_bullish_engulfing(o1, c1, o2, c2) -> bool:
    return c1 < o1 and c2 > o2 and c2 > o1 and o2 < c1


def _is_bearish_engulfing(o1, c1, o2, c2) -> bool:
    return c1 > o1 and c2 < o2 and c2 < o1 and o2 > c1


def _is_hammer(o, h, l, c) -> bool:
    body = abs(_body(o, c))
    lower_wick = min(o, c) - l
    upper_wick = h - max(o, c)
    span = _range(h, l)
    return lower_wick >= 2 * body and upper_wick <= body * 0.5 and body / span < 0.35


def _is_shooting_star(o, h, l, c) -> bool:
    body = abs(_body(o, c))
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    span = _range(h, l)
    return upper_wick >= 2 * body and lower_wick <= body * 0.5 and body / span < 0.35


def _find_double_top(highs: np.ndarray, tol_pct: float = 0.004) -> bool:
    if len(highs) < 6:
        return False
    w = highs[-12:]
    peak_idx = np.argsort(w)[-2:]
    peak_idx.sort()
    if peak_idx[1] - peak_idx[0] < 2:
        return False
    p1, p2 = w[peak_idx[0]], w[peak_idx[1]]
    if abs(p1 - p2) / max(p1, 1e-9) > tol_pct:
        return False
    between = w[peak_idx[0] + 1 : peak_idx[1]]
    return len(between) > 0 and float(np.min(between)) < min(p1, p2) * (1 - tol_pct)


def _find_double_bottom(lows: np.ndarray, tol_pct: float = 0.004) -> bool:
    if len(lows) < 6:
        return False
    w = lows[-12:]
    trough_idx = np.argsort(w)[:2]
    trough_idx.sort()
    if trough_idx[1] - trough_idx[0] < 2:
        return False
    t1, t2 = w[trough_idx[0]], w[trough_idx[1]]
    if abs(t1 - t2) / max(t1, 1e-9) > tol_pct:
        return False
    between = w[trough_idx[0] + 1 : trough_idx[1]]
    return len(between) > 0 and float(np.max(between)) > max(t1, t2) * (1 + tol_pct)


def _swing_structure(highs: np.ndarray, lows: np.ndarray) -> str:
    if len(highs) < 6:
        return "insufficient swings"
    h = highs[-6:]
    l = lows[-6:]
    hh = h[-1] > h[-3] > h[-5]
    hl = l[-1] > l[-3] > l[-5]
    lh = h[-1] < h[-3] < h[-5]
    ll = l[-1] < l[-3] < l[-5]
    if hh and hl:
        return "higher highs + higher lows (bullish structure)"
    if lh and ll:
        return "lower highs + lower lows (bearish structure)"
    if hh and ll:
        return "expanding triangle / broadening"
    if lh and hl:
        return "contracting range (symmetrical triangle bias)"
    return "mixed swing structure"


def analyze_patterns(kline: dict) -> str:
    """Classical candlestick / chart pattern report from OHLC."""
    opens = _arr(kline, "Open")
    highs = _arr(kline, "High")
    lows = _arr(kline, "Low")
    closes = _arr(kline, "Close")
    n = len(closes)
    if n < 3:
        return "Pattern: insufficient data (< 3 bars)."

    signals: list[str] = []
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]

    if abs(_body(o, c)) / _range(h, l) < 0.08:
        signals.append("doji / indecision on last bar")
    if n >= 2 and _is_bullish_engulfing(opens[-2], closes[-2], o, c):
        signals.append("bullish engulfing (confirmed on last bar)")
    if n >= 2 and _is_bearish_engulfing(opens[-2], closes[-2], o, c):
        signals.append("bearish engulfing (confirmed on last bar)")
    if _is_hammer(o, h, l, c):
        signals.append("hammer (bullish reversal hint)")
    if _is_shooting_star(o, h, l, c):
        signals.append("shooting star (bearish reversal hint)")

    if n >= 3:
        if closes[-1] > closes[-2] > closes[-3]:
            signals.append("three white soldiers / rising closes")
        if closes[-1] < closes[-2] < closes[-3]:
            signals.append("three black crows / falling closes")

    if _find_double_top(highs):
        signals.append("double top forming in recent window")
    if _find_double_bottom(lows):
        signals.append("double bottom forming in recent window")

    signals.append(_swing_structure(highs, lows))

    w = min(15, n)
    ch = closes[-w:]
    if float(np.std(ch)) / max(float(np.mean(ch)), 1e-9) < 0.002:
        signals.append("tight rectangle / consolidation")

    bias = "neutral"
    bull = sum(1 for s in signals if "bull" in s.lower() or "hammer" in s or "bottom" in s or "white soldiers" in s)
    bear = sum(1 for s in signals if "bear" in s.lower() or "shooting" in s or "top" in s or "black crows" in s)
    if bull > bear + 1:
        bias = "bullish"
    elif bear > bull + 1:
        bias = "bearish"

    lines = ["Pattern report (classic TA, rule-based):"]
    if signals:
        lines.extend(f"- {s}" for s in signals)
    else:
        lines.append("- no dominant classical pattern")
    lines.append(f"Overall pattern bias: {bias}")
    return "\n".join(lines)


def _sma(values: np.ndarray, period: int) -> float | None:
    if len(values) < period:
        return None
    return float(np.mean(values[-period:]))


def analyze_trend(kline: dict) -> str:
    """Classic trend: MAs, slope, support/resistance, breakouts."""
    closes = _arr(kline, "Close")
    highs = _arr(kline, "High")
    lows = _arr(kline, "Low")
    n = len(closes)
    if n < 5:
        return "Trend: insufficient data (< 5 bars)."

    last = float(closes[-1])
    x = np.arange(n, dtype=float)
    slope = float(np.polyfit(x, closes, 1)[0])
    short_n = min(5, n)
    short_slope = float(np.polyfit(np.arange(short_n), closes[-short_n:], 1)[0])

    w = min(20, n)
    support = float(np.min(lows[-w:]))
    resistance = float(np.max(highs[-w:]))
    range_pct = (resistance - support) / max(support, 1e-9) * 100

    sma5 = _sma(closes, 5)
    sma10 = _sma(closes, 10)
    sma20 = _sma(closes, 20)

    bias = "sideways"
    if short_slope > 0 and slope >= 0:
        bias = "upward"
    elif short_slope < 0 and slope <= 0:
        bias = "downward"

    ma_signals: list[str] = []
    if sma5 is not None and sma10 is not None:
        if sma5 > sma10:
            ma_signals.append("SMA5 > SMA10 (short-term bullish)")
        elif sma5 < sma10:
            ma_signals.append("SMA5 < SMA10 (short-term bearish)")
    if sma10 is not None and sma20 is not None:
        if sma10 > sma20:
            ma_signals.append("SMA10 > SMA20 (medium-term bullish)")
        elif sma10 < sma20:
            ma_signals.append("SMA10 < SMA20 (medium-term bearish)")
    if sma20 is not None:
        if last > sma20:
            ma_signals.append("price above SMA20")
        elif last < sma20:
            ma_signals.append("price below SMA20")

    action = "inside range"
    if last >= resistance * 0.998:
        action = "testing resistance / potential breakout up"
    elif last <= support * 1.002:
        action = "testing support / potential breakdown"
    elif last > (support + resistance) / 2:
        action = "upper half of range (bullish pressure)"
    else:
        action = "lower half of range (bearish pressure)"

    lines = [
        "Trend report (classic TA, rule-based):",
        f"- regression slope: {slope:.6g}/bar (recent {short_n}-bar: {short_slope:.6g}/bar)",
        f"- support ~{support:.4g}, resistance ~{resistance:.4g} (range {range_pct:.2f}%)",
        f"- last close {last:.4g}; {action}",
    ]
    lines.extend(f"- {s}" for s in ma_signals)
    lines.append(f"Overall trend bias: {bias}")
    return "\n".join(lines)


def create_classic_pattern_agent():
    def pattern_agent_node(state):
        report = analyze_patterns(state["kline_data"])
        return {"pattern_report": report, "messages": []}

    return pattern_agent_node


def create_classic_trend_agent():
    def trend_agent_node(state):
        report = analyze_trend(state["kline_data"])
        return {"trend_report": report, "messages": []}

    return trend_agent_node
