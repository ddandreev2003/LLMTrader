from pydantic import Field

from shachi_shock.aggregation import aggregate_shock_votes
from shachi_shock.models import (
    AGENT_ALLOWED_SHOCKS,
    AGENT_ROLES,
    AgentVote,
    MarketSnapshot,
    RegulatorMessage,
    RegulatorShockResponse,
    RegulatoryShockResult,
)
from shachi_shock.tools import build_regulator_tools
from shachi_shock.vendor.base import Environment, Observation


class RegulatorObservation(Observation[RegulatorMessage]):
    role: str = ""
    snapshot: MarketSnapshot
    allowed_shocks: list[str] = Field(default_factory=list)

    def format_as_prompt_text(self) -> str:
        s = self.snapshot
        lines = [
            f"Вы — {self.role}.",
            f"Тик: {s.tick}",
            f"Цена (benchmark): {s.price:.2f}",
            f"PnL портфеля: {s.portfolio_pnl:.2f}",
            f"Позиция: {s.position}",
            f"Сделок: {s.total_trades}",
            f"Торговля остановлена: {s.halted}",
            f"Множитель волатильности: {s.volatility_mult:.2f}",
            f"Допустимые типы шоков: {', '.join(self.allowed_shocks)}",
        ]
        if s.intraday:
            lines.extend(
                [
                    f"Intraday сессия: {s.session_date} {s.datetime}",
                    f"Bar interval: {s.bar_interval}",
                    f"Тикеры: {', '.join(s.tickers) if s.tickers else 'T, SBER'}",
                    f"Цены: {s.prices}",
                ]
            )
        lines.extend(
            [
                "",
                "Решите, нужен ли регуляторный шок. Ответ — строго JSON по схеме RegulatorShockResponse.",
                "Если шока нет — shock_occurred: false.",
            ]
        )
        for msg in self.messages:
            lines.append(f"[{msg.role}] {msg.text}")
        return "\n".join(lines)


DEFAULT_AGENT_CONFIGS = [
    {
        "role": AGENT_ROLES[0],
        "allowed_shocks": sorted(AGENT_ALLOWED_SHOCKS[0]),
        "intervention_bias": 0.25,
        "system_prompt": (
            "Ты — центральный банк. Реагируй на инфляционные риски и перегрев рынка. "
            "Используй rate_hike и position_limit. Будь сдержанным, но решительным при экстремумах."
        ),
    },
    {
        "role": AGENT_ROLES[1],
        "allowed_shocks": sorted(AGENT_ALLOWED_SHOCKS[1]),
        "intervention_bias": 0.35,
        "system_prompt": (
            "Ты — биржевой регулятор. Защищай рынок от паники и манипуляций. "
            "Используй circuit_breaker, halt, short_ban при высокой волатильности или аномалиях."
        ),
    },
    {
        "role": AGENT_ROLES[2],
        "allowed_shocks": sorted(AGENT_ALLOWED_SHOCKS[2]),
        "intervention_bias": 0.4,
        "system_prompt": (
            "Ты — медиа-агент. Генерируй новостные шоки (news_spike), усиливающие или "
            "смягчающие рыночные настроения. Учитывай контекст PnL и волатильности."
        ),
    },
]


class RegulatoryShockEnvironment(Environment[RegulatoryShockResult]):
    """Gym-style Shachi environment for multi-regulator shock decisions."""

    NUM_AGENTS = 3

    def __init__(self):
        self._snapshot = MarketSnapshot()
        self._shock_history: list[dict] = []
        self._last_result = RegulatoryShockResult()
        self._step_done = False
        self._tools = build_regulator_tools(
            get_snapshot=lambda: self._snapshot,
            get_shock_history=self._get_shock_history,
        )

    def update_snapshot(self, snapshot: MarketSnapshot) -> None:
        self._snapshot = snapshot

    def _get_shock_history(self, limit: int) -> list[dict]:
        return self._shock_history[-limit:]

    def num_agents(self) -> int:
        return self.NUM_AGENTS

    def get_default_agent_configs(self) -> list[dict]:
        return DEFAULT_AGENT_CONFIGS

    def done(self) -> bool:
        return self._step_done

    async def reset(self) -> dict[int, RegulatorObservation]:
        self._step_done = False
        self._last_result = RegulatoryShockResult(tick=self._snapshot.tick)
        return self._build_observations()

    async def step(
        self,
        responses: dict[int, str | RegulatorShockResponse | None],
    ) -> dict[int, RegulatorObservation]:
        votes: list[AgentVote] = []
        for agent_id, response in responses.items():
            if response is None:
                parsed = RegulatorShockResponse(shock_occurred=False)
            elif isinstance(response, RegulatorShockResponse):
                parsed = response
            elif isinstance(response, str):
                parsed = RegulatorShockResponse.model_validate_json(response)
            else:
                parsed = RegulatorShockResponse.model_validate(response)

            votes.append(
                AgentVote(
                    agent_id=agent_id,
                    role=AGENT_ROLES.get(agent_id, f"agent_{agent_id}"),
                    response=parsed,
                )
            )

        accepted = aggregate_shock_votes(votes)
        self._last_result = RegulatoryShockResult(
            tick=self._snapshot.tick,
            votes=votes,
            accepted_shocks=accepted,
        )

        for shock in accepted:
            record = {
                "tick": self._snapshot.tick,
                "type": shock.type,
                "severity": shock.severity,
                "description": shock.description,
                "proposed_by": shock.proposed_by,
            }
            self._shock_history.append(record)

        self._step_done = True
        return {}

    def get_result(self) -> RegulatoryShockResult:
        return self._last_result

    def get_last_accepted_shocks(self) -> list[dict]:
        """Payload dicts for EventBus SHOCK_TRIGGERED."""
        return [
            {
                "shock_occurred": True,
                "type": s.type,
                "severity": s.severity,
                "duration_ticks": s.duration_ticks,
                "price_impact_pct": s.price_impact_pct,
                "volatility_multiplier": s.volatility_multiplier,
                "description": s.description,
                "proposed_by": s.proposed_by,
            }
            for s in self._last_result.accepted_shocks
        ]

    def _build_observations(self) -> dict[int, RegulatorObservation]:
        obs: dict[int, RegulatorObservation] = {}
        for agent_id in range(self.NUM_AGENTS):
            allowed = sorted(AGENT_ALLOWED_SHOCKS.get(agent_id, set()))
            obs[agent_id] = RegulatorObservation(
                agent_id=agent_id,
                messages=[],
                response_type=RegulatorShockResponse,
                tools=self._tools,
                role=AGENT_ROLES.get(agent_id, ""),
                snapshot=self._snapshot,
                allowed_shocks=allowed,
            )
        return obs
