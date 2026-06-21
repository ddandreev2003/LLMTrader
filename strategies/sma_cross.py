def sma_cross_signal(
    price_history: list[float],
    current_position: int,
    sma_period: int = 20,
    buy_threshold: float = 1.01,
    sell_threshold: float = 0.99,
    default_quantity: int = 0,
) -> dict | None:
    """
    Простая SMA-cross стратегия без LLM.
    Возвращает None, если недостаточно истории для расчёта SMA.
    """
    if len(price_history) < sma_period:
        return None

    price = price_history[-1]
    sma = sum(price_history[-sma_period:]) / sma_period

    if price > sma * buy_threshold and current_position <= 0:
        return {
            "action": "buy",
            "quantity": default_quantity,
            "reason": "цена выше SMA",
        }
    if price < sma * sell_threshold and current_position > 0:
        return {
            "action": "sell",
            "quantity": default_quantity,
            "reason": "цена ниже SMA",
        }
    return {"action": "hold", "quantity": 0, "reason": "нет сигнала"}
