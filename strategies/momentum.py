from strategies.base import BaseStrategy, Signal


class MomentumStrategy(BaseStrategy):
    name = "momentum"

    def signal(self, prices: list[float], position: int, params: dict | None = None) -> Signal | None:
        p = params or {}
        roc_period = int(p.get("roc_period", 10))
        lookback_high = int(p.get("lookback_high", 20))
        qty = int(p.get("default_quantity", 0))

        if len(prices) < max(roc_period, lookback_high) + 1:
            return None

        price = prices[-1]
        past = prices[-roc_period - 1]
        roc = (price - past) / past * 100 if past else 0.0
        recent_high = max(prices[-lookback_high:])
        recent_low = min(prices[-lookback_high:])

        if roc > 0 and price >= recent_high * 0.99 and position <= 0:
            return {"action": "buy", "quantity": qty, "reason": f"momentum ROC={roc:.2f}% breakout"}
        if roc < 0 and price <= recent_low * 1.01 and position > 0:
            return {"action": "sell", "quantity": qty, "reason": f"momentum ROC={roc:.2f}% разворот"}
        return {"action": "hold", "quantity": 0, "reason": "momentum без сигнала"}
