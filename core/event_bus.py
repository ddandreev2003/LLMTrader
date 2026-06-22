from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable
import asyncio
import time
from collections import defaultdict


class EventType(Enum):
    # Рынок
    TICK = "tick"
    ORDER_PLACED = "order_placed"
    ORDER_FILLED = "order_filled"
    ORDER_REJECTED = "order_rejected"

    # Регулятор
    SHOCK_TRIGGERED = "shock_triggered"
    TRADING_HALTED = "trading_halted"
    TRADING_RESUMED = "trading_resumed"

    # Стратегия
    STRATEGY_SIGNAL = "strategy_signal"
    PROPOSED_SIGNAL = "proposed_signal"
    BAR_PROPOSALS_READY = "bar_proposals_ready"
    PRICES_UPDATED = "prices_updated"
    STRATEGY_UPDATED = "strategy_updated"

    # Система
    SIM_STARTED = "sim_started"
    SIM_ENDED = "sim_ended"
    REPORT_READY = "report_ready"


@dataclass
class Event:
    type: EventType
    payload: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    source: str = ""


class EventBus:
    """
    Простая pub-sub шина на asyncio.
    Агент подписывается на нужные типы событий.
    Публикация не блокирует отправителя.
    """

    def __init__(self):
        self._handlers: dict[EventType, list[Callable]] = defaultdict(list)
        self._queue: asyncio.Queue = asyncio.Queue()
        self._running = False

    def subscribe(self, event_type: EventType, handler: Callable[[Event], Awaitable[None]]):
        """Подписаться на тип события."""
        self._handlers[event_type].append(handler)

    async def publish(self, event: Event):
        """Поставить событие в очередь (неблокирующий вызов)."""
        await self._queue.put(event)

    async def run(self):
        """Главный цикл — раздаёт события подписчикам."""
        self._running = True
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=0.1)
                handlers = self._handlers.get(event.type, [])
                if handlers:
                    await asyncio.gather(*[h(event) for h in handlers])
                self._queue.task_done()
            except asyncio.TimeoutError:
                continue

    async def drain(self):
        """Дождаться обработки всех событий в очереди."""
        await self._queue.join()

    def stop(self):
        self._running = False
