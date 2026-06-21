import numpy as np

from strategies.base import BaseStrategy, Signal


class MeanReversionStrategy(BaseStrategy):
    name = "mean_reversion"

    def signal(self, prices: list[float], position: int, params: dict | None = None) -> Signal | None:
        p = params or {}
        period = int(p.get("bb_period", 20))
        std_mult = float(p.get("std_mult", 2.0))
        qty = int(p.get("default_quantity", 0))

        if len(prices) < period:
            return None

        window = prices[-period:]
        mean = float(np.mean(window))
        std = float(np.std(window))
        if std == 0:
            return {"action": "hold", "quantity": 0, "reason": "нулевая волатильность"}

        price = prices[-1]
        upper = mean + std_mult * std
        lower = mean - std_mult * std

        if price <= lower and position <= 0:
            return {"action": "buy", "quantity": qty, "reason": "цена у нижней полосы Bollinger"}
        if price >= upper and position > 0:
            return {"action": "sell", "quantity": qty, "reason": "цена у верхней полосы Bollinger"}
        return {"action": "hold", "quantity": 0, "reason": "внутри полос Bollinger"}
