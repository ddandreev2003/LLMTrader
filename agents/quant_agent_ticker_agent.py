"""QuantAgent-powered ticker strategy for OrderLog stream mode."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import yaml

from agents.base_agent import BaseAgent
from core.event_bus import Event, EventType
from core.position_sizing import compute_order_quantity, compute_sell_quantity
from core.sim_log import elapsed, log, log_done, log_step
from quant_agent.bridge import run_quant_decision


class QuantAgentTickerAgent(BaseAgent):
    """One QuantAgent graph per ticker; decisions on bar close."""

    name = "quant_agent"

    def __init__(
        self,
        bus,
        agent_id: str,
        ticker: str,
        params: dict | None = None,
        call_interval_bars: int = 1,
        max_position_pct: float = 0.25,
        commission_pct: float = 0.0003,
        trade_cutoff_ticks: int = 5,
        total_ticks: int = 0,
        proposal_mode: bool = True,
        bar_interval: str = "1m",
    ):
        super().__init__(bus)
        self.agent_id = agent_id
        self.ticker = ticker
        self.params = params or {}
        self.interval = max(1, call_interval_bars)
        self.max_position_pct = max_position_pct
        self.commission_pct = commission_pct
        self.trade_cutoff_ticks = trade_cutoff_ticks
        self.total_ticks = total_ticks
        self.proposal_mode = proposal_mode
        self._bar_interval = bar_interval
        self._tick_counter = 0
        self._current_tick = 0
        self._active_shocks: list[dict] = []
        self._current_position = 0
        self._entry_price: float | None = None
        self._portfolio_value = 0.0
        self._cash = 0.0
        self._trading_enabled = True
        self._last_price = 0.0
        self._last_reports: dict = {}
        self._llm_sem = asyncio.Semaphore(1)
        self._bars_in_position = 0
        self._last_buy_tick = -1

    @property
    def strategy(self):
        return self

    async def on_tick(self, event: Event):
        if not event.payload.get("bar_close", True):
            return
        if event.payload.get("closed_ticker") and event.payload.get("closed_ticker") != self.ticker:
            return

        prices = event.payload.get("prices", {})
        if self.ticker not in prices:
            return

        self._current_tick = int(event.payload.get("tick", 0))
        self._trading_enabled = bool(event.payload.get("trading_enabled", True))
        self._bar_interval = event.payload.get("bar_interval", self._bar_interval)
        self._last_price = float(prices[self.ticker])

        agent_portfolios = event.payload.get("agent_portfolios", {})
        mine = agent_portfolios.get(self.agent_id, {})
        if mine:
            self._portfolio_value = float(mine.get("portfolio_value", 0))
            self._cash = float(mine.get("cash", 0))
            self._current_position = int(mine.get("position", self._current_position))

        total_ticks = int(event.payload.get("total_ticks", 0))
        if total_ticks > 0:
            self.total_ticks = total_ticks

        self._tick_counter += 1
        self._expire_shocks()

        if self._current_position > 0:
            self._bars_in_position += 1
        else:
            self._bars_in_position = 0

        shock_exit = self._check_shock_exit(event.payload)
        if shock_exit:
            await self._publish_signal(shock_exit)
            return

        max_hold = self._check_max_hold_exit()
        if max_hold:
            await self._publish_signal(max_hold)
            return

        risk = self._check_risk_exits(self._last_price)
        if risk:
            await self._publish_signal(risk)
            return

        if not self._trading_enabled:
            return

        shock_types = {s.get("type") for s in self._active_shocks}
        if shock_types & {"circuit_breaker", "halt"} or event.payload.get("halted"):
            return

        if self._tick_counter % self.interval != 0:
            return

        kline_history = event.payload.get("kline_history", {}).get(self.ticker, [])
        if not kline_history:
            return

        time_frame = self._bar_interval.replace("m", "min").replace("h", "hour")
        use_llm = os.environ.get("QUANT_USE_LLM", "").strip().lower() in ("1", "true", "yes")
        label = "LLM" if use_llm else "TA"
        log_step(
            f"QuantAgent [{self.ticker}]",
            f"бар {self._current_tick} (сим {elapsed():.0f}s) — {label}-анализ…",
        )
        t0 = elapsed()
        async with self._llm_sem:
            try:
                entry_thr = int(self.params.get("ta_entry_threshold", self.params.get("min_ta_score", 3)))
                forecast_bars = int(self.params.get("forecast_bars", 3))
                result = await asyncio.to_thread(
                    run_quant_decision,
                    self.ticker,
                    kline_history,
                    time_frame,
                    entry_thr,
                    forecast_bars,
                )
            except Exception as exc:
                log(f"QuantAgent [{self.ticker}] ошибка: {exc}")
                return

        dt = elapsed() - t0
        decision = result.get("decision", "HOLD")
        log_done(f"QuantAgent [{self.ticker}]", f"{decision} за {dt:.2f}s")

        self._last_reports = result.get("reports", {})
        ta_score = int(result.get("ta_score", 0))
        min_score = int(self.params.get("min_ta_score", 3))
        signal = result.get("signal") or {}
        signal = self._filter_signal(signal, ta_score, min_score)
        signal = self._apply_sizing(signal)
        if signal.get("action") in ("buy", "sell") and signal.get("quantity", 0) > 0:
            await self._publish_signal(signal, quant_reports=self._last_reports)

    async def on_shock(self, event: Event):
        shock = dict(event.payload)
        shock["_remaining_ticks"] = int(shock.get("duration_ticks", 10))
        self._active_shocks.append(shock)

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
            if self._entry_price is None or self._current_position == qty:
                self._entry_price = fill_price
            else:
                prev_qty = self._current_position - qty
                self._entry_price = (
                    (self._entry_price or fill_price) * prev_qty + fill_price * qty
                ) / max(self._current_position, 1)
            self._last_buy_tick = int(event.payload.get("tick", self._current_tick))
            self._bars_in_position = 0
        elif action == "sell":
            self._current_position = max(0, self._current_position - qty)
            if self._current_position <= 0:
                self._entry_price = None
                self._bars_in_position = 0
                self._last_buy_tick = -1

    def _expire_shocks(self):
        remaining = []
        for shock in self._active_shocks:
            shock["_remaining_ticks"] = shock.get("_remaining_ticks", 1) - 1
            if shock["_remaining_ticks"] > 0:
                remaining.append(shock)
        self._active_shocks = remaining

    def _check_shock_exit(self, tick_payload: dict) -> dict | None:
        if self._current_position <= 0:
            return None
        threshold_neg = float(self.params.get("shock_exit_impact_pct", -0.3))
        threshold_pos = float(self.params.get("shock_take_profit_impact_pct", 0.15))
        min_gain = float(self.params.get("shock_take_profit_min_gain_pct", 0.003))
        price = self._last_price or float(tick_payload.get("prices", {}).get(self.ticker, 0))

        for shock in self._active_shocks:
            impact = float(shock.get("price_impact_pct", 0))
            target = shock.get("ticker")
            if target and target != self.ticker:
                continue

            if impact <= threshold_neg:
                return {
                    "action": "sell",
                    "quantity": self._current_position,
                    "reason": f"shock exit {shock.get('type', 'shock')} {impact:+.1f}%",
                }

            if (
                impact >= threshold_pos
                and self._entry_price
                and price >= self._entry_price * (1 + min_gain)
            ):
                return {
                    "action": "sell",
                    "quantity": self._current_position,
                    "reason": (
                        f"shock take-profit {shock.get('type', 'shock')} {impact:+.1f}% "
                        f"(+{(price / self._entry_price - 1) * 100:.2f}%)"
                    ),
                }
        return None

    def _check_max_hold_exit(self) -> dict | None:
        max_hold = self.params.get("max_hold_bars")
        if max_hold is None or self._current_position <= 0:
            return None
        if self._bars_in_position >= int(max_hold):
            return {
                "action": "sell",
                "quantity": self._current_position,
                "reason": f"max hold {int(max_hold)} bars",
            }
        return None

    def _filter_signal(self, signal: dict, ta_score: int, min_score: int) -> dict:
        action = signal.get("action", "hold")
        if action == "hold":
            return signal

        if action == "buy":
            if abs(ta_score) < min_score:
                return {"action": "hold", "quantity": 0, "reason": f"weak TA score {ta_score}"}
            if self.params.get("no_add_to_position") and self._current_position > 0:
                return {"action": "hold", "quantity": 0, "reason": "no averaging into position"}
            return signal

        if action == "sell":
            if self._current_position <= 0:
                return {"action": "hold", "quantity": 0, "reason": "flat — skip short"}
            min_hold = int(self.params.get("min_hold_bars", 8))
            if self._bars_in_position < min_hold:
                return {
                    "action": "hold",
                    "quantity": 0,
                    "reason": f"min hold {min_hold} bars ({self._bars_in_position})",
                }
            if abs(ta_score) < min_score:
                return {"action": "hold", "quantity": 0, "reason": f"weak TA score {ta_score}"}

        return signal

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

    def _apply_sizing(self, signal: dict) -> dict:
        action = signal.get("action", "hold")
        if action not in ("buy", "sell"):
            return signal
        price = self._last_price
        if price <= 0:
            return {"action": "hold", "quantity": 0, "reason": "no price"}
        target_weight = float(self.params.get("target_weight_pct", 0.15))
        if action == "buy":
            if self._is_in_trade_cutoff():
                return {"action": "hold", "quantity": 0, "reason": "trade cutoff"}
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
            signal["quantity"] = max(qty, 0)
        elif action == "sell":
            signal["quantity"] = compute_sell_quantity(int(signal.get("quantity", 0)), self._current_position)
        if signal.get("quantity", 0) <= 0:
            return {"action": "hold", "quantity": 0, "reason": "zero size"}
        return signal

    def _is_in_trade_cutoff(self) -> bool:
        if self.total_ticks <= 0 or self.trade_cutoff_ticks <= 0:
            return False
        return self._current_tick >= self.total_ticks - self.trade_cutoff_ticks

    async def _publish_signal(self, signal: dict, quant_reports: dict | None = None):
        payload = {
            "ticker": self.ticker,
            "action": signal.get("action", "hold"),
            "quantity": int(signal.get("quantity", 0)),
            "reason": signal.get("reason", ""),
            "agent_id": self.agent_id,
            "strategy": self.name,
            "tick": self._current_tick,
            "quant_reports": quant_reports or self._last_reports,
        }
        event_type = EventType.PROPOSED_SIGNAL if self.proposal_mode else EventType.STRATEGY_SIGNAL
        if payload["action"] not in ("buy", "sell") or payload["quantity"] <= 0:
            return
        await self.bus.publish(Event(type=event_type, payload=payload, source=self.agent_id))

    def register(self):
        self.bus.subscribe(EventType.TICK, self.on_tick)
        self.bus.subscribe(EventType.SHOCK_TRIGGERED, self.on_shock)
        self.bus.subscribe(EventType.ORDER_FILLED, self.on_order_filled)

    def get_last_reports(self) -> dict:
        return dict(self._last_reports)


def load_yaml_config(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)
