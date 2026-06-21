from strategies.base import BaseStrategy, Signal


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2 / (period + 1)
    ema_vals = [values[0]]
    for v in values[1:]:
        ema_vals.append(v * k + ema_vals[-1] * (1 - k))
    return ema_vals


class MacdStrategy(BaseStrategy):
    name = "macd"

    def signal(self, prices: list[float], position: int, params: dict | None = None) -> Signal | None:
        p = params or {}
        fast = int(p.get("fast", 12))
        slow = int(p.get("slow", 26))
        signal_period = int(p.get("signal_period", 9))
        qty = int(p.get("default_quantity", 0))

        min_len = slow + signal_period
        if len(prices) < min_len:
            return None

        ema_fast = _ema(prices, fast)
        ema_slow = _ema(prices, slow)
        macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
        signal_line = _ema(macd_line, signal_period)

        prev_macd, curr_macd = macd_line[-2], macd_line[-1]
        prev_sig, curr_sig = signal_line[-2], signal_line[-1]

        crossed_up = prev_macd <= prev_sig and curr_macd > curr_sig
        crossed_down = prev_macd >= prev_sig and curr_macd < curr_sig

        if crossed_up and position <= 0:
            return {"action": "buy", "quantity": qty, "reason": "MACD пересёк сигнальную снизу вверх"}
        if crossed_down and position > 0:
            return {"action": "sell", "quantity": qty, "reason": "MACD пересёк сигнальную сверху вниз"}
        return {"action": "hold", "quantity": 0, "reason": "нет пересечения MACD"}
