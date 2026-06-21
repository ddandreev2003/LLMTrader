from strategies.base import BaseStrategy
from strategies.macd import MacdStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.momentum import MomentumStrategy
from strategies.rsi import RsiStrategy
from strategies.sma_cross_strategy import SmaCrossStrategy

_REGISTRY: dict[str, BaseStrategy] = {
    "sma_cross": SmaCrossStrategy(),
    "mean_reversion": MeanReversionStrategy(),
    "rsi": RsiStrategy(),
    "macd": MacdStrategy(),
    "momentum": MomentumStrategy(),
}


def get_strategy(name: str) -> BaseStrategy:
    if name not in _REGISTRY:
        raise KeyError(f"Unknown strategy: {name}. Available: {list(_REGISTRY)}")
    return _REGISTRY[name]


def list_strategies() -> list[str]:
    return list(_REGISTRY.keys())
