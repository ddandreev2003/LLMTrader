from typing import Any

from pydantic import BaseModel, Field


class PlayRequest(BaseModel):
    interval_ms: int = Field(default=500, ge=0, le=30_000)


class ConfigUpdate(BaseModel):
    market_mode: str | None = None
    shock_backend: str | None = None
    shock_interval: int | None = None
    sim_n_ticks: int | None = None
    initial_cash_rub: float | None = None
    portfolio_config: str | None = None
    strategies_config: str | None = None
    auto_play_interval_ms: int | None = None
    moex_offline: bool | None = None
    portfolio_targets: dict[str, float] | None = None


class StateResponse(BaseModel):
    status: str
    tick: int
    current_tick: int
    total_ticks: int
    date: str = ""
    halted: bool = False
    auto_interval_ms: int = 500
    config: dict[str, Any] = Field(default_factory=dict)
    agents: dict[str, Any] = Field(default_factory=dict)
    prices: dict[str, float] = Field(default_factory=dict)
    shocks: list[dict[str, Any]] = Field(default_factory=list)
    recent_events: list[dict[str, Any]] = Field(default_factory=list)
    price_history: dict[str, Any] = Field(default_factory=dict)
    agent_equity: dict[str, Any] = Field(default_factory=dict)
