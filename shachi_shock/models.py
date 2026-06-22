from typing import Literal

from pydantic import BaseModel, Field

from shachi_shock.vendor.base import Message

ShockType = Literal[
    "circuit_breaker",
    "short_ban",
    "position_limit",
    "news_spike",
    "rate_hike",
    "halt",
]

SHOCK_PRIORITY: dict[str, int] = {
    "halt": 100,
    "circuit_breaker": 90,
    "short_ban": 70,
    "position_limit": 60,
    "rate_hike": 50,
    "news_spike": 40,
}

AGENT_ALLOWED_SHOCKS: dict[int, set[str]] = {
    0: {"rate_hike", "position_limit"},
    1: {"circuit_breaker", "halt", "short_ban"},
    2: {"news_spike"},
}

AGENT_ROLES: dict[int, str] = {
    0: "Центральный банк",
    1: "Биржевой регулятор",
    2: "Медиа / новостной фон",
}


class MarketSnapshot(BaseModel):
    tick: int = 0
    price: float = 100.0
    portfolio_pnl: float = 0.0
    position: int = 0
    total_trades: int = 0
    halted: bool = False
    volatility_mult: float = 1.0
    datetime: str = ""
    session_date: str = ""
    bar_interval: str = "5m"
    intraday: bool = False
    prices: dict[str, float] = Field(default_factory=dict)
    tickers: list[str] = Field(default_factory=list)


class RegulatorMessage(Message):
    role: str = ""
    text: str = ""


class RegulatorShockResponse(BaseModel):
    shock_occurred: bool = False
    type: ShockType | None = None
    severity: int = Field(default=1, ge=1, le=10)
    duration_ticks: int = Field(default=10, ge=5, le=50)
    price_impact_pct: float = Field(default=0.0, ge=-20.0, le=20.0)
    volatility_multiplier: float = Field(default=1.0, ge=1.0, le=5.0)
    description: str = ""
    rationale: str = ""


class AgentVote(BaseModel):
    agent_id: int
    role: str
    response: RegulatorShockResponse


class AcceptedShock(BaseModel):
    type: ShockType
    severity: int
    duration_ticks: int
    price_impact_pct: float
    volatility_multiplier: float
    description: str
    proposed_by: list[int] = Field(default_factory=list)


class RegulatoryShockResult(BaseModel):
    tick: int = 0
    votes: list[AgentVote] = Field(default_factory=list)
    accepted_shocks: list[AcceptedShock] = Field(default_factory=list)
