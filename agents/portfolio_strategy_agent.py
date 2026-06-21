"""Strategy agent that manually assembles a multi-ticker portfolio via target weights."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from agents.base_agent import BaseAgent
from agents.ticker_strategy_agent import TickerStrategyAgent, load_yaml_config
from core.event_bus import Event, EventType
from core.portfolio_rebalance import compute_rebalance_orders
from strategies.registry import get_strategy


PORTFOLIO_LLM_PROMPT = """
Ты — портфельный менеджер в учебном симуляторе MOEX.
Управляешь портфелем из тикеров: {universe}.
Целевые веса (ручная сборка): {targets}

На основе рынка реши: сохранять целевые веса или сдвинуть allocation.
Отвечай ТОЛЬКО JSON:
{{
  "rebalance": true | false,
  "targets": {{"SBER": 0.3, "GAZP": 0.2}},
  "reason": "краткое обоснование"
}}
При circuit_breaker или halt — rebalance: false.
"""


class PortfolioStrategyAgent(BaseAgent):
    """
    Agent with isolated capital that trades multiple tickers.

    Manual mode: `portfolio_targets` in config — rebalance toward fixed weights.
    Signal mode: run TA strategy per ticker in `universe` (optional).
    """

    def __init__(
        self,
        bus,
        agent_id: str,
        universe: list[str],
        portfolio_targets: dict[str, float],
        strategy_name: str = "sma_cross",
        params: dict | None = None,
        llm_enabled: bool = False,
        rebalance_interval_ticks: int = 5,
        rebalance_threshold_pct: float = 0.03,
        max_position_pct: float = 0.95,
        commission_pct: float = 0.0003,
        trade_cutoff_ticks: int = 5,
        total_ticks: int = 0,
        signal_mode: bool = False,
    ):
        super().__init__(bus)
        self.agent_id = agent_id
        self.universe = universe
        self.ticker = universe[0] if universe else ""  # legacy compat
        self.portfolio_targets = _normalize_weights(portfolio_targets)
        self.strategy = get_strategy(strategy_name)
        self.params = params or {}
        self.llm_enabled = llm_enabled
        self.rebalance_interval = rebalance_interval_ticks
        self.rebalance_threshold = rebalance_threshold_pct
        self.max_position_pct = max_position_pct
        self.commission_pct = commission_pct
        self.trade_cutoff_ticks = trade_cutoff_ticks
        self.total_ticks = total_ticks
        self.signal_mode = signal_mode
        self.initial_capital = 0.0

        self._tick_counter = 0
        self._current_tick = 0
        self._trading_enabled = True
        self._active_shocks: list[dict] = []
        self._price_histories: dict[str, list[float]] = {t: [] for t in universe}
        self._positions: dict[str, int] = {t: 0 for t in universe}
        self._entry_prices: dict[str, float] = {}
        self._cash = 0.0
        self._portfolio_value = 0.0
        self._targets = dict(self.portfolio_targets)
        self._pending_orders: list[dict] = []

    async def on_tick(self, event: Event):
        prices = event.payload.get("prices", {})
        self._current_tick = int(event.payload.get("tick", 0))
        self._trading_enabled = bool(event.payload.get("trading_enabled", True))
        total_ticks = int(event.payload.get("total_ticks", 0))
        if total_ticks > 0:
            self.total_ticks = total_ticks

        agent_portfolios = event.payload.get("agent_portfolios", {})
        mine = agent_portfolios.get(self.agent_id, {})
        if mine:
            self._portfolio_value = float(mine.get("portfolio_value", 0))
            self._cash = float(mine.get("cash", 0))
            holdings = mine.get("holdings", {})
            for t in self.universe:
                self._positions[t] = int(holdings.get(t, self._positions.get(t, 0)))

        for t in self.universe:
            if t in prices:
                hist = self._price_histories[t]
                hist.append(float(prices[t]))
                if len(hist) > 200:
                    self._price_histories[t] = hist[-200:]

        self._tick_counter += 1
        self._expire_shocks()

        if not self._trading_enabled:
            return

        if self._is_in_trade_cutoff():
            return

        # Execute one pending order per tick (cooldown-friendly)
        if self._pending_orders:
            order = self._pending_orders.pop(0)
            await self._publish_signal(order)
            return

        if self.signal_mode:
            await self._signal_mode_tick(prices)
            return

        if self._tick_counter % self.rebalance_interval != 0:
            return

        if self.llm_enabled and (self._tick_counter % (self.rebalance_interval * 2) == 0):
            await self._llm_adjust_targets(prices)

        await self._schedule_rebalance(prices)

    async def _signal_mode_tick(self, prices: dict[str, float]):
        """Run TA per ticker; combine with manual targets as max weight cap."""
        for ticker in self.universe:
            if ticker not in prices:
                continue
            pos = self._positions.get(ticker, 0)
            signal = self.strategy.signal(
                self._price_histories.get(ticker, []), pos, self.params
            )
            if signal and signal.get("action") in ("buy", "sell"):
                signal["ticker"] = ticker
                await self._publish_signal(signal)
                return

    async def _schedule_rebalance(self, prices: dict[str, float]):
        pv = self._portfolio_value
        if pv <= 0:
            return

        orders = compute_rebalance_orders(
            positions=self._positions,
            prices=prices,
            portfolio_value=pv,
            cash=self._cash,
            target_weights=self._targets,
            commission_pct=self.commission_pct,
            threshold_pct=self.rebalance_threshold,
        )
        if orders:
            self._pending_orders = orders

    async def _llm_adjust_targets(self, prices: dict[str, float]):
        prompt = PORTFOLIO_LLM_PROMPT.format(
            universe=", ".join(self.universe),
            targets=json.dumps(self._targets, ensure_ascii=False),
        )
        user = f"Tick {self._current_tick}, PV={self._portfolio_value:.0f}, cash={self._cash:.0f}, prices={json.dumps({t: prices.get(t) for t in self.universe})}"
        try:
            result = await self.ask_llm_json(prompt, user)
            if result.get("rebalance") and result.get("targets"):
                self._targets = _normalize_weights(result["targets"])
        except Exception as exc:
            print(f"[{self.agent_id}] LLM portfolio ошибка: {exc}")

    async def on_shock(self, event: Event):
        shock = dict(event.payload)
        shock["_remaining_ticks"] = int(shock.get("duration_ticks", 10))
        self._active_shocks.append(shock)

    async def on_order_filled(self, event: Event):
        if event.payload.get("agent_id") != self.agent_id:
            return
        ticker = event.payload.get("ticker", "")
        if ticker not in self.universe:
            return
        action = event.payload.get("action")
        qty = int(event.payload.get("quantity", 0))
        price = float(event.payload.get("price", 0))
        if action == "buy":
            self._positions[ticker] = self._positions.get(ticker, 0) + qty
            self._entry_prices[ticker] = price
        elif action == "sell":
            self._positions[ticker] = max(0, self._positions.get(ticker, 0) - qty)

    def _expire_shocks(self):
        remaining = []
        for shock in self._active_shocks:
            shock["_remaining_ticks"] = shock.get("_remaining_ticks", 1) - 1
            if shock["_remaining_ticks"] > 0:
                remaining.append(shock)
        self._active_shocks = remaining

    def _is_in_trade_cutoff(self) -> bool:
        if self.total_ticks <= 0 or self.trade_cutoff_ticks <= 0:
            return False
        return self._current_tick >= self.total_ticks - self.trade_cutoff_ticks

    async def _publish_signal(self, signal: dict):
        payload = {
            "ticker": signal["ticker"],
            "action": signal.get("action", "hold"),
            "quantity": int(signal.get("quantity", 0)),
            "reason": signal.get("reason", ""),
            "agent_id": self.agent_id,
            "strategy": self.strategy.name,
        }
        if payload["action"] not in ("buy", "sell") or payload["quantity"] <= 0:
            return
        await self.bus.publish(
            Event(type=EventType.STRATEGY_SIGNAL, payload=payload, source=self.agent_id)
        )

    def register(self):
        self.bus.subscribe(EventType.TICK, self.on_tick)
        self.bus.subscribe(EventType.SHOCK_TRIGGERED, self.on_shock)
        self.bus.subscribe(EventType.ORDER_FILLED, self.on_order_filled)


def _normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    total = sum(float(v) for v in weights.values())
    if total <= 0:
        return weights
    return {k: float(v) / total for k, v in weights.items()}


def create_strategy_agents_from_config(
    bus,
    config_path: str | Path,
    portfolio_cfg: dict | None = None,
) -> list:
    """Create TickerStrategyAgent or PortfolioStrategyAgent from yaml."""
    cfg = load_yaml_config(config_path)
    p_cfg = (portfolio_cfg or {}).get("portfolio", {})
    commission_pct = float(p_cfg.get("commission_pct", 0.0003))
    trade_cutoff_ticks = int(p_cfg.get("trade_cutoff_ticks", 5))
    agent_max_pct = float(p_cfg.get("agent_max_position_pct", 0.95))
    total_capital = float(p_cfg.get("initial_cash_rub", 1_000_000))

    entries = cfg.get("strategy_agents", [])
    n = len(entries) or 1
    default_capital = total_capital / n
    agents = []

    for entry in entries:
        agent_type = entry.get("type", "single")
        targets = entry.get("portfolio_targets") or entry.get("holdings")
        universe = entry.get("universe") or ([entry["ticker"]] if entry.get("ticker") else [])

        if agent_type == "portfolio" or (targets and len(universe) > 1):
            if not targets:
                # equal weight across universe
                targets = {t: 1.0 / len(universe) for t in universe}
            agent = PortfolioStrategyAgent(
                bus=bus,
                agent_id=entry["id"],
                universe=universe,
                portfolio_targets=targets,
                strategy_name=entry.get("strategy", "sma_cross"),
                params=entry.get("params", {}),
                llm_enabled=bool(entry.get("llm_enabled", False)),
                rebalance_interval_ticks=int(entry.get("rebalance_interval_ticks", 5)),
                rebalance_threshold_pct=float(entry.get("rebalance_threshold_pct", 0.03)),
                max_position_pct=agent_max_pct,
                commission_pct=commission_pct,
                trade_cutoff_ticks=trade_cutoff_ticks,
                signal_mode=bool(entry.get("signal_mode", False)),
            )
        else:
            agent = TickerStrategyAgent(
                bus=bus,
                agent_id=entry["id"],
                ticker=entry["ticker"],
                strategy_name=entry["strategy"],
                params=entry.get("params", {}),
                llm_enabled=bool(entry.get("llm_enabled", False)),
                max_position_pct=agent_max_pct,
                commission_pct=commission_pct,
                trade_cutoff_ticks=trade_cutoff_ticks,
            )

        agent.initial_capital = float(entry.get("initial_capital_rub", default_capital))
        agents.append(agent)

    return agents
