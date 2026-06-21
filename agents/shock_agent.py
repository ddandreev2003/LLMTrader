from agents.base_agent import BaseAgent
from core.event_bus import Event, EventType

SHOCK_SYSTEM_PROMPT = """
Ты — регулятор финансового рынка в учебном симуляторе.
Твоя задача: решить, произошёл ли регуляторный шок, и если да — описать его.

Отвечай ТОЛЬКО JSON в формате:
{
  "shock_occurred": true/false,
  "type": "circuit_breaker" | "short_ban" | "position_limit" | "news_spike" | "rate_hike" | "halt",
  "severity": 1-10,
  "duration_ticks": 5-50,
  "price_impact_pct": -20.0 до 20.0,
  "volatility_multiplier": 1.0-5.0,
  "description": "краткое описание на русском"
}
Если шока нет — верни {"shock_occurred": false}.
"""


class ShockAgent(BaseAgent):

    def __init__(self, bus, shock_interval_ticks: int = 50, shock_probability: float = 0.3):
        super().__init__(bus)
        self.interval = shock_interval_ticks
        self.probability = shock_probability
        self._tick_counter = 0

    async def on_tick(self, event: Event):
        self._tick_counter += 1

        if self._tick_counter % self.interval != 0:
            return

        price = event.payload.get("price", 100)
        pnl = event.payload.get("portfolio_pnl", 0)

        user_prompt = f"""
Состояние рынка:
- Текущая цена: {price:.2f}
- PnL портфеля: {pnl:.2f}
- Тиков прошло: {self._tick_counter}
- Вероятность шока в этом цикле: {self.probability}

Реши: произошёл ли регуляторный шок?
        """

        try:
            result = await self.ask_llm_json(SHOCK_SYSTEM_PROMPT, user_prompt)
        except Exception as e:
            print(f"[ShockAgent] LLM ошибка: {e}")
            return

        if result.get("shock_occurred"):
            result["tick"] = event.payload.get("tick", self._tick_counter)
            shock_event = Event(
                type=EventType.SHOCK_TRIGGERED,
                payload=result,
                source="ShockAgent",
            )
            await self.bus.publish(shock_event)
            print(f"[ShockAgent] Шок: {result.get('type')} — {result.get('description', '')}")

    def register(self):
        self.bus.subscribe(EventType.TICK, self.on_tick)
