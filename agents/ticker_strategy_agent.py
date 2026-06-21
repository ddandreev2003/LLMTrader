import json
import os
from pathlib import Path

import yaml

from agents.base_agent import BaseAgent
from core.event_bus import Event, EventType
from core.position_sizing import compute_order_quantity, compute_sell_quantity
from strategies.registry import get_strategy


STRATEGY_SYSTEM_PROMPT = """
Ты — алгоритмический трейдер в учебном симуляторе российского рынка.
Торгуешь только тикер {ticker}. На основе рыночных данных прими решение.

Отвечай ТОЛЬКО JSON:
{{
  "action": "buy" | "sell" | "hold",
  "quantity": 1-100,
  "reason": "краткое обоснование"
}}
При circuit_breaker или halt — всегда "hold".
"""


class TickerStrategyAgent(BaseAgent):
    """Independent strategy agent for one ticker (TA + optional LLM)."""

    def __init__(
        self,
        bus,
        agent_id: str,
        ticker: str,
        strategy_name: str,
        params: dict | None = None,
        llm_enabled: bool = False,
        call_interval_ticks: int = 10,
        max_position_pct: float = 0.25,
        commission_pct: float = 0.0003,
        trade_cutoff_ticks: int = 5,
        total_ticks: int = 0,
    ):
        super().__init__(bus)
        self.agent_id = agent_id
        self.ticker = ticker
        self.strategy = get_strategy(strategy_name)
        self.params = params or {}
        self.llm_enabled = llm_enabled
        self.interval = call_interval_ticks
        self.max_position_pct = max_position_pct
        self.commission_pct = commission_pct
        self.trade_cutoff_ticks = trade_cutoff_ticks
        self.total_ticks = total_ticks
        self._tick_counter = 0
        self._current_tick = 0
        self._active_shocks: list[dict] = []
        self._price_history: list[float] = []
        self._current_position = 0
        self._entry_price: float | None = None
        self._portfolio_value = 0.0
        self._cash = 0.0
        self._trading_enabled = True
        self._signal_debug = os.environ.get("SIGNAL_DEBUG", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

    async def on_tick(self, event: Event):
        prices = event.payload.get("prices", {})
        if self.ticker not in prices:
            return

        self._current_tick = int(event.payload.get("tick", 0))
        self._trading_enabled = bool(event.payload.get("trading_enabled", True))

        agent_portfolios = event.payload.get("agent_portfolios", {})
        mine = agent_portfolios.get(self.agent_id, {})
        if mine:
            self._portfolio_value = float(mine.get("portfolio_value", 0))
            self._cash = float(mine.get("cash", 0))
            self._current_position = int(mine.get("position", self._current_position))
        else:
            self._portfolio_value = float(event.payload.get("portfolio_value", 0))
            self._cash = float(event.payload.get("cash", 0))
        total_ticks = int(event.payload.get("total_ticks", 0))
        if total_ticks > 0:
            self.total_ticks = total_ticks

        price = float(prices[self.ticker])
        self._price_history.append(price)
        if len(self._price_history) > 200:
            self._price_history.pop(0)

        self._tick_counter += 1
        self._expire_shocks()

        risk_signal = self._check_risk_exits(price)
        if risk_signal:
            await self._publish_signal(risk_signal)
            return

        if not self._trading_enabled:
            return

        signal = self._ta_signal()
        if self.llm_enabled and (
            self._tick_counter % self.interval == 0 or self._active_shocks
        ):
            signal = await self._llm_decision(price) or signal

        if signal and signal.get("action") != "hold":
            await self._publish_signal(signal)

    async def on_shock(self, event: Event):
        shock = dict(event.payload)
        shock["_remaining_ticks"] = int(shock.get("duration_ticks", 10))
        self._active_shocks.append(shock)

        if self.llm_enabled and self._price_history and self._trading_enabled:
            signal = await self._llm_decision(self._price_history[-1])
            if signal and signal.get("action") != "hold":
                await self._publish_signal(signal)

    async def on_order_filled(self, event: Event):
        if event.payload.get("agent_id") and event.payload.get("agent_id") != self.agent_id:
            return
        if event.payload.get("ticker") != self.ticker:
            return
        action = event.payload.get("action")
        qty = int(event.payload.get("quantity", 0))
        fill_price = float(event.payload.get("price", 0))
        if action == "buy":
            self._current_position += qty
            self._entry_price = fill_price
        elif action == "sell":
            self._current_position = max(0, self._current_position - qty)
            if self._current_position <= 0:
                self._entry_price = None

    def _expire_shocks(self):
        remaining = []
        for shock in self._active_shocks:
            shock["_remaining_ticks"] = shock.get("_remaining_ticks", 1) - 1
            if shock["_remaining_ticks"] > 0:
                remaining.append(shock)
        self._active_shocks = remaining

    def _check_risk_exits(self, price: float) -> dict | None:
        if self._current_position <= 0 or self._entry_price is None:
            return None

        stop_loss = self.params.get("stop_loss_pct")
        take_profit = self.params.get("take_profit_pct")

        if stop_loss is not None and price < self._entry_price * (1 - float(stop_loss)):
            return {
                "action": "sell",
                "quantity": self._current_position,
                "reason": f"stop-loss {float(stop_loss) * 100:.1f}%",
            }
        if take_profit is not None and price > self._entry_price * (1 + float(take_profit)):
            return {
                "action": "sell",
                "quantity": self._current_position,
                "reason": f"take-profit {float(take_profit) * 100:.1f}%",
            }
        return None

    def _ta_signal(self) -> dict | None:
        signal = self.strategy.signal(self._price_history, self._current_position, self.params)
        if signal is None:
            return None
        return self._apply_constraints(signal)

    async def _llm_decision(self, price: float) -> dict | None:
        user_prompt = f"""
Тикер: {self.ticker}
Текущая цена: {price:.2f}
Позиция: {self._current_position}
История (последние 10): {self._price_history[-10:]}
Активные шоки: {json.dumps(self._active_shocks, ensure_ascii=False)}
        """
        prompt = STRATEGY_SYSTEM_PROMPT.format(ticker=self.ticker)
        try:
            result = await self.ask_llm_json(prompt, user_prompt)
            return self._apply_constraints(result)
        except Exception as exc:
            print(f"[{self.agent_id}] LLM ошибка: {exc}, fallback TA")
            return self._ta_signal()

    def _apply_constraints(self, signal: dict) -> dict:
        action = signal.get("action", "hold")
        shock_types = {s.get("type") for s in self._active_shocks}

        if shock_types & {"circuit_breaker", "halt"}:
            return {"action": "hold", "quantity": 0, "reason": "торговля приостановлена"}

        if "short_ban" in shock_types and action == "sell" and self._current_position <= 0:
            return {"action": "hold", "quantity": 0, "reason": "short_ban"}

        if action in ("buy", "sell"):
            signal = self._apply_sizing(signal)

        return signal

    def _apply_sizing(self, signal: dict) -> dict:
        action = signal.get("action", "hold")
        if not self._price_history:
            return {"action": "hold", "quantity": 0, "reason": "нет цены для sizing"}

        price = self._price_history[-1]
        signal_qty = int(signal.get("quantity", 0))
        target_weight = float(self.params.get("target_weight_pct", 0.15))

        if action == "buy":
            if self._is_in_trade_cutoff():
                return {
                    "action": "hold",
                    "quantity": 0,
                    "reason": "trade cutoff: запрет новых входов в конце симуляции",
                }
            if signal_qty > 0:
                qty = signal_qty
            else:
                pos_value = self._current_position * price
                qty = compute_order_quantity(
                    portfolio_value=self._portfolio_value,
                    cash=self._cash,
                    price=price,
                    target_weight_pct=target_weight,
                    max_position_pct=self.max_position_pct,
                    current_position_value=pos_value,
                    commission_pct=self.commission_pct,
                )
            if qty <= 0:
                return {"action": "hold", "quantity": 0, "reason": "недостаточный размер позиции"}
            signal["quantity"] = qty
        elif action == "sell":
            signal["quantity"] = compute_sell_quantity(signal_qty, self._current_position)
            if signal["quantity"] <= 0:
                return {"action": "hold", "quantity": 0, "reason": "нет позиции для продажи"}

        return signal

    def _is_in_trade_cutoff(self) -> bool:
        if self.total_ticks <= 0 or self.trade_cutoff_ticks <= 0:
            return False
        return self._current_tick >= self.total_ticks - self.trade_cutoff_ticks

    async def _publish_signal(self, signal: dict):
        payload = {
            "ticker": self.ticker,
            "action": signal.get("action", "hold"),
            "quantity": int(signal.get("quantity", 0)),
            "reason": signal.get("reason", ""),
            "agent_id": self.agent_id,
            "strategy": self.strategy.name,
        }
        if self._signal_debug and payload["action"] in ("buy", "sell"):
            print(
                f"[{self.agent_id}] tick={self._current_tick} "
                f"{payload['action']} qty={payload['quantity']} "
                f"reason={payload['reason']}"
            )
        await self.bus.publish(
            Event(
                type=EventType.STRATEGY_SIGNAL,
                payload=payload,
                source=self.agent_id,
            )
        )

    def register(self):
        self.bus.subscribe(EventType.TICK, self.on_tick)
        self.bus.subscribe(EventType.SHOCK_TRIGGERED, self.on_shock)
        self.bus.subscribe(EventType.ORDER_FILLED, self.on_order_filled)


def load_yaml_config(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)
