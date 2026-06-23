"""Market maker agent metadata for dashboard (quotes run inside stream engine)."""

from __future__ import annotations

from agents.base_agent import BaseAgent
from core.event_bus import Event, EventType


class MarketMakerAgent(BaseAgent):
    """Passive agent: MM logic lives in OrderLogStreamMarketEngine; this tracks state for viz."""

    name = "market_maker"

    def __init__(
        self,
        bus,
        agent_id: str = "mm_agent",
        universe: list[str] | None = None,
        display_name: str = "Market Maker",
        nft_color: str = "#14b8a6",
    ):
        super().__init__(bus)
        self.agent_id = agent_id
        self.universe = universe or ["T", "GAZP", "SBER"]
        self.display_name = display_name
        self.nft_color = nft_color
        self.initial_capital = 0.0

    @property
    def strategy(self):
        return self

    async def on_tick(self, event: Event):
        pass

    def register(self):
        self.bus.subscribe(EventType.TICK, self.on_tick)


def create_market_maker_agent_if_enabled(bus, portfolio_cfg: dict) -> MarketMakerAgent | None:
    mm_cfg = portfolio_cfg.get("market_maker") or {}
    if not mm_cfg.get("enabled", False):
        return None
    tickers = [a.get("ticker") for a in portfolio_cfg.get("assets", []) if a.get("ticker")]
    if not tickers:
        tickers = ["T", "GAZP", "SBER"]
    return MarketMakerAgent(
        bus,
        agent_id=mm_cfg.get("id", "mm_agent"),
        universe=tickers,
        display_name=mm_cfg.get("display_name", "Market Maker"),
        nft_color=mm_cfg.get("nft_color", "#14b8a6"),
    )
