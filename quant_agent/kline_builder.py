"""Convert rolling bar history to QuantAgent kline_data dict."""

from __future__ import annotations


def bars_to_kline_data(bars: list[dict]) -> dict:
    """QuantAgent expects Open/High/Low/Close/Datetime lists."""
    if not bars:
        return {"Datetime": [], "Open": [], "High": [], "Low": [], "Close": [], "Volume": []}

    out = {"Datetime": [], "Open": [], "High": [], "Low": [], "Close": [], "Volume": []}
    for b in bars:
        dt = b.get("datetime", "")
        if hasattr(dt, "isoformat"):
            dt = dt.isoformat()
        out["Datetime"].append(str(dt))
        out["Open"].append(float(b["open"]))
        out["High"].append(float(b["high"]))
        out["Low"].append(float(b["low"]))
        out["Close"].append(float(b["close"]))
        out["Volume"].append(float(b.get("volume", 0)))
    return out
