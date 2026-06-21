import json

from agents.base_agent import BaseAgent
from core.event_bus import Event, EventType
from strategies.sma_cross import sma_cross_signal

STRATEGY_SYSTEM_PROMPT = """
Ты — алгоритмический трейдер в учебном симуляторе.
На основе рыночных данных и активных ограничений прими торговое решение.

Отвечай ТОЛЬКО JSON:
{
  "action": "buy" | "sell" | "hold",
  "quantity": 1-100,
  "reason": "краткое обоснование",
  "new_sma_period": null или число (если хочешь скорректировать параметр)
}
При активном шоке circuit_breaker или halt — всегда "hold".
При short_ban — нельзя "sell" при отсутствии позиции.
"""


class StrategyAgent(BaseAgent):

    def __init__(self, bus, call_interval_ticks: int = 10):
        super().__init__(bus)
        self.interval = call_interval_ticks
        self._tick_counter = 0
        self._active_shocks: list[dict] = []
        self._price_history: list[float] = []
        self._current_position = 0
        self._sma_period = 20

    async def on_tick(self, event: Event):
        price = event.payload.get("price", 0)
        self._price_history.append(price)
        if len(self._price_history) > 100:
            self._price_history.pop(0)

        self._tick_counter += 1
        self._expire_shocks()

        signal = self._sma_signal(price)

        if self._tick_counter % self.interval == 0 or self._active_shocks:
            signal = await self._llm_decision(price)

        if signal:
            await self.bus.publish(
                Event(
                    type=EventType.STRATEGY_SIGNAL,
                    payload=signal,
                    source="StrategyAgent",
                )
            )

    async def on_shock(self, event: Event):
        shock = dict(event.payload)
        shock["_remaining_ticks"] = int(shock.get("duration_ticks", 10))
        self._active_shocks.append(shock)

        if self._price_history:
            signal = await self._llm_decision(self._price_history[-1])
            if signal:
                await self.bus.publish(
                    Event(
                        type=EventType.STRATEGY_SIGNAL,
                        payload=signal,
                        source="StrategyAgent",
                    )
                )

    async def on_order_filled(self, event: Event):
        action = event.payload.get("action")
        qty = int(event.payload.get("quantity", 0))
        if action == "buy":
            self._current_position += qty
        elif action == "sell":
            self._current_position = max(0, self._current_position - qty)

    def _expire_shocks(self):
        remaining = []
        for shock in self._active_shocks:
            shock["_remaining_ticks"] = shock.get("_remaining_ticks", 1) - 1
            if shock["_remaining_ticks"] > 0:
                remaining.append(shock)
        self._active_shocks = remaining

    async def _llm_decision(self, price: float) -> dict | None:
        prices = self._price_history[-20:]
        sma = sum(prices) / len(prices) if prices else price

        user_prompt = f"""
Рыночные данные:
- Текущая цена: {price:.2f}
- SMA({self._sma_period}): {sma:.2f}
- Текущая позиция: {self._current_position} лотов
- Активные ограничения: {json.dumps(self._active_shocks, ensure_ascii=False)}

Прими торговое решение.
        """
        try:
            result = await self.ask_llm_json(STRATEGY_SYSTEM_PROMPT, user_prompt)
            new_period = result.get("new_sma_period")
            if new_period and isinstance(new_period, (int, float)) and new_period > 0:
                self._sma_period = int(new_period)
            return self._apply_constraints(result)
        except Exception as e:
            print(f"[StrategyAgent] LLM ошибка: {e}, используем fallback")
            return self._sma_signal(price)

    def _apply_constraints(self, signal: dict) -> dict:
        action = signal.get("action", "hold")
        shock_types = {s.get("type") for s in self._active_shocks}

        if shock_types & {"circuit_breaker", "halt"}:
            return {"action": "hold", "quantity": 0, "reason": "торговля приостановлена шоком"}

        if "short_ban" in shock_types and action == "sell" and self._current_position <= 0:
            return {"action": "hold", "quantity": 0, "reason": "short_ban активен"}

        return signal

    def _sma_signal(self, price: float) -> dict | None:
        """Встроенная SMA-стратегия — не требует LLM."""
        signal = sma_cross_signal(
            self._price_history,
            self._current_position,
            sma_period=self._sma_period,
        )
        if signal is None:
            return None
        return self._apply_constraints(signal)

    def register(self):
        self.bus.subscribe(EventType.TICK, self.on_tick)
        self.bus.subscribe(EventType.SHOCK_TRIGGERED, self.on_shock)
        self.bus.subscribe(EventType.ORDER_FILLED, self.on_order_filled)
