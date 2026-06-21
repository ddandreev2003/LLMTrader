from strategies.base import BaseStrategy, Signal
from strategies.sma_cross import sma_cross_signal


class SmaCrossStrategy(BaseStrategy):
    name = "sma_cross"

    def signal(self, prices: list[float], position: int, params: dict | None = None) -> Signal | None:
        p = params or {}
        result = sma_cross_signal(
            prices,
            position,
            sma_period=int(p.get("sma_period", 20)),
            buy_threshold=float(p.get("buy_threshold", 1.01)),
            sell_threshold=float(p.get("sell_threshold", 0.99)),
            default_quantity=int(p.get("default_quantity", 0)),
        )
        return result  # type: ignore[return-value]
