from typing import Callable

from shachi_shock.models import MarketSnapshot
from shachi_shock.vendor.base import Tool, ToolResponse
from pydantic import BaseModel


class MarketSnapshotParams(BaseModel):
    pass


class MarketSnapshotResponse(ToolResponse):
    snapshot: MarketSnapshot

    def format_as_prompt_text(self) -> str:
        s = self.snapshot
        return (
            f"Рынок: tick={s.tick}, price={s.price:.2f}, halted={s.halted}, "
            f"vol_mult={s.volatility_mult:.2f}"
        )


class PortfolioMetricsParams(BaseModel):
    pass


class PortfolioMetricsResponse(ToolResponse):
    pnl: float
    position: int
    total_trades: int

    def format_as_prompt_text(self) -> str:
        return (
            f"Портфель: PnL={self.pnl:.2f}, позиция={self.position}, "
            f"сделок={self.total_trades}"
        )


class ShockHistoryParams(BaseModel):
    limit: int = 5


class ShockHistoryResponse(ToolResponse):
    shocks: list[dict]

    def format_as_prompt_text(self) -> str:
        if not self.shocks:
            return "История шоков пуста."
        lines = ["Последние шоки:"]
        for i, shock in enumerate(self.shocks, 1):
            lines.append(
                f"  {i}. tick {shock.get('tick', '?')}: "
                f"{shock.get('type', '?')} — {shock.get('description', '')}"
            )
        return "\n".join(lines)


def build_regulator_tools(
    get_snapshot: Callable[[], MarketSnapshot],
    get_shock_history: Callable[[int], list[dict]],
) -> list[Tool]:
    def market_snapshot_fun(_: MarketSnapshotParams) -> MarketSnapshotResponse:
        return MarketSnapshotResponse(snapshot=get_snapshot())

    def portfolio_metrics_fun(_: PortfolioMetricsParams) -> PortfolioMetricsResponse:
        s = get_snapshot()
        return PortfolioMetricsResponse(
            pnl=s.portfolio_pnl,
            position=s.position,
            total_trades=s.total_trades,
        )

    def shock_history_fun(params: ShockHistoryParams) -> ShockHistoryResponse:
        return ShockHistoryResponse(shocks=get_shock_history(params.limit))

    return [
        Tool(
            name="get_market_snapshot",
            description="Текущее состояние рынка: цена, тик, остановка торгов, волатильность",
            parameters_type=MarketSnapshotParams,
            fun=market_snapshot_fun,
        ),
        Tool(
            name="get_portfolio_metrics",
            description="Метрики портфеля: PnL, позиция, число сделок",
            parameters_type=PortfolioMetricsParams,
            fun=portfolio_metrics_fun,
        ),
        Tool(
            name="get_shock_history",
            description="История последних регуляторных шоков",
            parameters_type=ShockHistoryParams,
            fun=shock_history_fun,
        ),
    ]
