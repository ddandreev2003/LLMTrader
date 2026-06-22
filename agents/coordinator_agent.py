"""Portfolio coordinator: gates ticker agent proposals via LLM."""

from __future__ import annotations

import json
from pathlib import Path

from agents.base_agent import BaseAgent
from agents.portfolio_strategy_agent import PortfolioStrategyAgent
from agents.ticker_strategy_agent import TickerStrategyAgent, load_yaml_config
from core.event_bus import Event, EventType


COORDINATOR_SYSTEM_PROMPT = """
Ты — портфельный координатор intraday-симуляции MOEX.
Universe: {universe}. Целевые веса по умолчанию: {targets}.

На каждом баре получаешь proposals от независимых агентов по тикерам.
Реши, какие сделки одобрить, с учётом риска, шоков и баланса портфеля.

Отвечай ТОЛЬКО JSON:
{{
  "decisions": [
    {{
      "agent_id": "t_momentum",
      "ticker": "T",
      "approved": true,
      "action": "buy" | "sell" | "hold",
      "quantity": 0,
      "reason": "кратко"
    }}
  ],
  "targets": {{"T": 0.50, "SBER": 0.50}},
  "notes": "общий комментарий"
}}
При halt или circuit_breaker — все approved=false, action=hold.
"""


class PortfolioCoordinatorAgent(BaseAgent):
    """LLM coordinator over ticker agent proposals."""

    def __init__(
        self,
        bus,
        agent_id: str,
        universe: list[str],
        ticker_agent_ids: list[str],
        default_targets: dict[str, float] | None = None,
        llm_enabled: bool = True,
        rebalance_interval_ticks: int = 6,
        max_total_exposure_pct: float = 0.95,
        hft_mode: bool = False,
    ):
        super().__init__(bus)
        self.agent_id = agent_id
        self.universe = universe
        self.ticker_agent_ids = set(ticker_agent_ids)
        self.default_targets = default_targets or {t: 1.0 / len(universe) for t in universe}
        self.llm_enabled = llm_enabled
        self.rebalance_interval = rebalance_interval_ticks
        self.max_total_exposure_pct = max_total_exposure_pct
        self.hft_mode = hft_mode

        self._tick_counter = 0
        self._current_tick = 0
        self._proposals: dict[str, dict] = {}
        self._active_shocks: list[dict] = []
        self._last_prices: dict[str, float] = {}
        self._agent_portfolios: dict = {}
        self._halted = False
        self._trading_enabled = True
        self._datetime = ""
        self._bar_interval = "5m"
        self.hft_mode = False
        self._micro_phase = "close"

    async def on_proposed_signal(self, event: Event):
        agent_id = event.payload.get("agent_id", "")
        if agent_id not in self.ticker_agent_ids:
            return
        self._proposals[agent_id] = dict(event.payload)

    async def on_bar_proposals_ready(self, event: Event):
        self._current_tick = int(event.payload.get("tick", 0))
        self._last_prices = dict(event.payload.get("prices", {}))
        self._agent_portfolios = dict(event.payload.get("agent_portfolios", {}))
        self._halted = bool(event.payload.get("halted", False))
        self._trading_enabled = bool(event.payload.get("trading_enabled", True))
        self._datetime = event.payload.get("datetime", "")
        self._bar_interval = event.payload.get("bar_interval", "5m")
        self._micro_phase = event.payload.get("micro_phase", "close")
        self.hft_mode = bool(event.payload.get("hft_mode", False))
        self._tick_counter += 1
        self._expire_shocks()

        if not self._trading_enabled:
            self._proposals.clear()
            return

        shock_types = {s.get("type") for s in self._active_shocks}
        if shock_types & {"halt", "circuit_breaker"} or self._halted:
            self._proposals.clear()
            return

        if not self._proposals:
            return

        use_llm = self.llm_enabled and self._tick_counter % self.rebalance_interval == 0
        if self.hft_mode and self._micro_phase in ("open", "high", "low"):
            use_llm = False

        if use_llm:
            decisions = await self._llm_gate()
        else:
            decisions = self._passthrough_decisions()

        for decision in decisions:
            if not decision.get("approved"):
                continue
            action = decision.get("action", "hold")
            qty = int(decision.get("quantity", 0))
            if action not in ("buy", "sell") or qty <= 0:
                continue
            agent_id = decision.get("agent_id", "")
            ticker = decision.get("ticker", "")
            proposal = self._proposals.get(agent_id, {})
            await self.bus.publish(
                Event(
                    type=EventType.STRATEGY_SIGNAL,
                    payload={
                        "ticker": ticker,
                        "action": action,
                        "quantity": qty,
                        "reason": decision.get("reason", proposal.get("reason", "")),
                        "agent_id": agent_id,
                        "strategy": proposal.get("strategy", "coordinated"),
                        "coordinator": self.agent_id,
                    },
                    source=self.agent_id,
                )
            )

        self._proposals.clear()

    async def on_shock(self, event: Event):
        shock = dict(event.payload)
        shock["_remaining_ticks"] = int(shock.get("duration_ticks", 10))
        self._active_shocks.append(shock)

    def _expire_shocks(self):
        remaining = []
        for shock in self._active_shocks:
            shock["_remaining_ticks"] = shock.get("_remaining_ticks", 1) - 1
            if shock["_remaining_ticks"] > 0:
                remaining.append(shock)
        self._active_shocks = remaining

    def _passthrough_decisions(self) -> list[dict]:
        out = []
        for agent_id, proposal in self._proposals.items():
            action = proposal.get("action", "hold")
            qty = int(proposal.get("quantity", 0))
            out.append(
                {
                    "agent_id": agent_id,
                    "ticker": proposal.get("ticker", ""),
                    "approved": action in ("buy", "sell") and qty > 0,
                    "action": action,
                    "quantity": qty,
                    "reason": proposal.get("reason", "passthrough"),
                }
            )
        return out

    async def _llm_gate(self) -> list[dict]:
        user_prompt = f"""
Bar tick: {self._current_tick}
Datetime: {self._datetime}
Bar interval: {self._bar_interval}
Prices: {json.dumps(self._last_prices, ensure_ascii=False)}
Agent portfolios: {json.dumps(self._agent_portfolios, ensure_ascii=False)}
Proposals: {json.dumps(list(self._proposals.values()), ensure_ascii=False)}
Active shocks: {json.dumps(self._active_shocks, ensure_ascii=False)}
Max total exposure: {self.max_total_exposure_pct:.0%}
"""
        prompt = COORDINATOR_SYSTEM_PROMPT.format(
            universe=", ".join(self.universe),
            targets=json.dumps(self.default_targets, ensure_ascii=False),
        )
        try:
            result = await self.ask_llm_json(prompt, user_prompt)
            decisions = result.get("decisions", [])
            if decisions:
                return decisions
        except Exception as exc:
            print(f"[{self.agent_id}] LLM gate error: {exc}, passthrough")
        return self._passthrough_decisions()

    def register(self):
        self.bus.subscribe(EventType.PROPOSED_SIGNAL, self.on_proposed_signal)
        self.bus.subscribe(EventType.BAR_PROPOSALS_READY, self.on_bar_proposals_ready)
        self.bus.subscribe(EventType.SHOCK_TRIGGERED, self.on_shock)


def _is_nft_portfolio_mode(cfg: dict) -> bool:
    if cfg.get("agent_mode") == "nft_portfolio":
        return True
    entries = cfg.get("strategy_agents", [])
    if not entries:
        return False
    return all(
        e.get("type") == "portfolio" or (e.get("universe") and len(e["universe"]) > 1)
        for e in entries
    )


def create_intraday_agents_from_config(
    bus,
    config_path: str | Path,
    portfolio_cfg: dict | None = None,
) -> tuple[list, PortfolioCoordinatorAgent | None]:
    cfg = load_yaml_config(config_path)
    p_cfg = (portfolio_cfg or {}).get("portfolio", {})
    trading = (portfolio_cfg or {}).get("trading", {})
    global_hft = bool(trading.get("hft_mode", False))
    commission_pct = float(p_cfg.get("commission_pct", 0.0003))
    trade_cutoff = int(p_cfg.get("trade_cutoff_bars", p_cfg.get("trade_cutoff_ticks", 6)))
    agent_max_pct = float(p_cfg.get("agent_max_position_pct", 0.95))
    total_capital = float(p_cfg.get("initial_cash_rub", 1_000_000))

    entries = cfg.get("strategy_agents", [])
    n = len(entries) or 1
    default_capital = total_capital / n

    if _is_nft_portfolio_mode(cfg):
        agents: list[PortfolioStrategyAgent] = []
        for entry in entries:
            universe = entry.get("universe") or []
            targets = entry.get("portfolio_targets") or {t: 1.0 / len(universe) for t in universe}
            agent_hft = bool(entry.get("hft_mode", global_hft))
            agent = PortfolioStrategyAgent(
                bus=bus,
                agent_id=entry["id"],
                universe=universe,
                portfolio_targets=targets,
                strategy_name=entry.get("strategy", "sma_cross"),
                params=entry.get("params", {}),
                llm_enabled=bool(entry.get("llm_enabled", False)),
                rebalance_interval_ticks=int(entry.get("rebalance_interval_ticks", 1)),
                rebalance_threshold_pct=float(entry.get("rebalance_threshold_pct", 0.03)),
                max_position_pct=agent_max_pct,
                commission_pct=commission_pct,
                trade_cutoff_ticks=trade_cutoff,
                signal_mode=bool(entry.get("signal_mode", True)),
                display_name=entry.get("display_name", entry["id"]),
                nft_color=entry.get("nft_color", "#3b82f6"),
                intraday_mode=True,
                hft_mode=agent_hft,
            )
            agent.initial_capital = float(entry.get("initial_capital_rub", default_capital))
            agents.append(agent)
        return agents, None

    ticker_agents: list[TickerStrategyAgent] = []
    for entry in entries:
        agent = TickerStrategyAgent(
            bus=bus,
            agent_id=entry["id"],
            ticker=entry["ticker"],
            strategy_name=entry["strategy"],
            params=entry.get("params", {}),
            llm_enabled=bool(entry.get("llm_enabled", False)),
            call_interval_ticks=int(entry.get("call_interval_ticks", 12)),
            max_position_pct=agent_max_pct,
            commission_pct=commission_pct,
            trade_cutoff_ticks=trade_cutoff,
            proposal_mode=not global_hft,
            intraday_mode=True,
            hft_mode=global_hft,
        )
        agent.initial_capital = float(entry.get("initial_capital_rub", default_capital))
        ticker_agents.append(agent)

    coord_cfg = cfg.get("coordinator") or {}
    if coord_cfg is None or cfg.get("coordinator") is None:
        return ticker_agents, None

    universe = coord_cfg.get("universe") or [a.ticker for a in ticker_agents]
    coordinator = PortfolioCoordinatorAgent(
        bus=bus,
        agent_id=coord_cfg.get("id", "tls_coordinator"),
        universe=universe,
        ticker_agent_ids=[a.agent_id for a in ticker_agents],
        default_targets=coord_cfg.get("default_targets"),
        llm_enabled=bool(coord_cfg.get("llm_enabled", True)),
        rebalance_interval_ticks=int(coord_cfg.get("rebalance_interval_ticks", 6)),
        max_total_exposure_pct=float(coord_cfg.get("max_total_exposure_pct", 0.95)),
        hft_mode=global_hft or bool(coord_cfg.get("hft_mode", False)),
    )
    return ticker_agents, coordinator
