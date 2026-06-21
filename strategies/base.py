from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal, TypedDict


class Signal(TypedDict):
    action: Literal["buy", "sell", "hold"]
    quantity: int
    reason: str


class BaseStrategy(ABC):
    name: str = "base"

    @abstractmethod
    def signal(
        self,
        prices: list[float],
        position: int,
        params: dict | None = None,
    ) -> Signal | None:
        raise NotImplementedError()
