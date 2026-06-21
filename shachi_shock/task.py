from collections.abc import AsyncIterator, Sequence

from shachi_shock.env.regulatory_env import RegulatoryShockEnvironment
from shachi_shock.models import MarketSnapshot, RegulatoryShockResult
from shachi_shock.vendor.base import Environment, Task
from pydantic import BaseModel, Field


class AggregatedShockResults(BaseModel):
    episodes: int = 0
    total_shocks: int = 0
    shock_types: dict[str, int] = Field(default_factory=dict)
    results: list[RegulatoryShockResult] = Field(default_factory=list)


class RegulatoryShockTask(Task[RegulatoryShockResult, AggregatedShockResults]):
    """Shachi Task for standalone shock episodes."""

    def __init__(
        self,
        num_episodes: int = 1,
        ticks_per_episode: int = 1,
        start_price: float = 100.0,
    ):
        self.num_episodes = num_episodes
        self.ticks_per_episode = ticks_per_episode
        self.start_price = start_price

    async def iterate_environments(self) -> AsyncIterator[Environment[RegulatoryShockResult]]:
        for ep in range(self.num_episodes):
            env = RegulatoryShockEnvironment()
            env.update_snapshot(
                MarketSnapshot(
                    tick=ep * self.ticks_per_episode,
                    price=self.start_price,
                    portfolio_pnl=0.0,
                )
            )
            yield env

    def aggregate_results(
        self, results: Sequence[RegulatoryShockResult]
    ) -> AggregatedShockResults:
        shock_types: dict[str, int] = {}
        total = 0
        for result in results:
            for shock in result.accepted_shocks:
                total += 1
                shock_types[shock.type] = shock_types.get(shock.type, 0) + 1
        return AggregatedShockResults(
            episodes=len(results),
            total_shocks=total,
            shock_types=shock_types,
            results=list(results),
        )
