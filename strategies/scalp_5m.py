from strategies.base import BaseStrategy, Signal


class Scalp5mStrategy(BaseStrategy):
    """Intraday scalp using 5m bar OHLC and micro-phase (open/high/low/close)."""

    name = "scalp_5m"

    def signal(self, prices: list[float], position: int, params: dict | None = None) -> Signal | None:
        p = params or {}
        phase = p.get("micro_phase", "close")
        bar = p.get("bar_ohlc") or {}
        qty = int(p.get("default_quantity", 5))
        impulse_thr = float(p.get("impulse_threshold", 0.0001))
        scale_out = float(p.get("scale_out_pct", 0.5))
        scale_qty = max(1, int(position * scale_out)) if position > 0 else max(1, int(qty * scale_out))

        if not bar:
            return None

        o = float(bar.get("open", prices[-1] if prices else 0))
        h = float(bar.get("high", o))
        l = float(bar.get("low", o))
        c = float(bar.get("close", prices[-1] if prices else o))
        mid = (h + l) / 2 if h and l else c
        up_barrier = 1 + impulse_thr
        down_barrier = 1 - impulse_thr

        if phase == "open":
            if c > o * up_barrier and position <= 0:
                return {"action": "buy", "quantity": qty, "reason": "scalp open impulse up"}
            if c < o * down_barrier and position > 0:
                return {"action": "sell", "quantity": min(position, qty), "reason": "scalp open impulse down"}
            if position <= 0 and c >= o:
                return {"action": "buy", "quantity": max(1, qty // 2), "reason": "scalp open micro-rotate long"}
            if position > 0 and c < o:
                return {
                    "action": "sell",
                    "quantity": min(position, scale_qty),
                    "reason": "scalp open micro-rotate trim",
                }

        elif phase == "high":
            if position > 0 and c >= h * (1 - impulse_thr * 2):
                return {
                    "action": "sell",
                    "quantity": min(position, scale_qty),
                    "reason": "scalp take-profit near high",
                }
            if position <= 0 and c > mid:
                return {"action": "buy", "quantity": qty, "reason": "scalp momentum at high"}
            if position > 0 and c < mid:
                return {
                    "action": "sell",
                    "quantity": min(position, scale_qty),
                    "reason": "scalp high fade below mid",
                }
            if position <= 0 and c >= l:
                return {"action": "buy", "quantity": max(1, qty // 2), "reason": "scalp high micro-rotate entry"}

        elif phase == "low":
            if position <= 0 and c <= l * (1 + impulse_thr * 2):
                return {"action": "buy", "quantity": qty, "reason": "scalp buy dip near low"}
            if position > 0 and c < mid * down_barrier:
                return {
                    "action": "sell",
                    "quantity": min(position, qty),
                    "reason": "scalp cut loss below mid",
                }
            if position <= 0 and c <= mid:
                return {"action": "buy", "quantity": max(1, qty // 2), "reason": "scalp low micro-rotate dip"}
            if position > 0 and c > mid:
                return {
                    "action": "sell",
                    "quantity": min(position, scale_qty),
                    "reason": "scalp low bounce take",
                }

        elif phase == "close":
            if position > 0 and c < o:
                return {
                    "action": "sell",
                    "quantity": min(position, qty),
                    "reason": "scalp close below open",
                }
            if position <= 0 and c > o * up_barrier:
                return {"action": "buy", "quantity": qty, "reason": "scalp close momentum"}
            if position > 0 and len(prices) >= 3 and prices[-1] > prices[-3]:
                return {
                    "action": "sell",
                    "quantity": min(position, scale_qty),
                    "reason": "scalp partial exit into strength",
                }
            if position > 0:
                return {
                    "action": "sell",
                    "quantity": min(position, max(1, qty // 3)),
                    "reason": "scalp close micro-rotate exit",
                }
            if position <= 0 and c > o:
                return {"action": "buy", "quantity": max(1, qty // 2), "reason": "scalp close micro-rotate entry"}

        return {"action": "hold", "quantity": 0, "reason": f"scalp {phase} no edge"}
