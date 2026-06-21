from strategies.base import BaseStrategy, Signal


def _compute_rsi(prices: list[float], period: int) -> float | None:
    if len(prices) < period + 1:
        return None
    deltas = [prices[i] - prices[i - 1] for i in range(-period, 0)]
    gains = [d for d in deltas if d > 0]
    losses = [-d for d in deltas if d < 0]
    avg_gain = sum(gains) / period if gains else 0.0
    avg_loss = sum(losses) / period if losses else 0.0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


class RsiStrategy(BaseStrategy):
    name = "rsi"

    def signal(self, prices: list[float], position: int, params: dict | None = None) -> Signal | None:
        p = params or {}
        period = int(p.get("period", 14))
        oversold = float(p.get("oversold", 30))
        overbought = float(p.get("overbought", 70))
        qty = int(p.get("default_quantity", 0))

        rsi = _compute_rsi(prices, period)
        if rsi is None:
            return None

        if rsi < oversold and position <= 0:
            return {"action": "buy", "quantity": qty, "reason": f"RSI={rsi:.1f} перепродан"}
        if rsi > overbought and position > 0:
            return {"action": "sell", "quantity": qty, "reason": f"RSI={rsi:.1f} перекуплен"}
        return {"action": "hold", "quantity": 0, "reason": f"RSI={rsi:.1f} нейтрален"}
