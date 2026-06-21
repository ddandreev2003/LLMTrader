"""Pause/step/play control for the simulation market loop."""

from __future__ import annotations

import asyncio
from enum import Enum


class SimStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    WAITING_LLM = "waiting_llm"
    FINISHED = "finished"
    STOPPED = "stopped"


class SimulationController:
    """Coordinates tick-by-tick or auto advancement of the market engine."""

    def __init__(self, auto_interval_ms: int = 500):
        self.status = SimStatus.IDLE
        self.auto_interval_ms = auto_interval_ms
        self.current_tick = 0
        self.total_ticks = 0
        self._advance_event = asyncio.Event()
        self._stop_requested = False
        self._steps_requested = 0
        self._lock = asyncio.Lock()

    def configure(self, total_ticks: int, auto_interval_ms: int | None = None):
        self.total_ticks = total_ticks
        self.current_tick = 0
        if auto_interval_ms is not None:
            self.auto_interval_ms = auto_interval_ms

    async def start(self):
        async with self._lock:
            self._stop_requested = False
            self.status = SimStatus.PAUSED
            self._advance_event.set()

    async def pause(self):
        async with self._lock:
            if self.status == SimStatus.RUNNING:
                self.status = SimStatus.PAUSED

    async def step(self):
        async with self._lock:
            if self.status not in (SimStatus.PAUSED, SimStatus.IDLE, SimStatus.RUNNING):
                return False
            self.status = SimStatus.PAUSED
            self._steps_requested += 1
            self._advance_event.set()
            return True

    async def play(self, interval_ms: int | None = None):
        async with self._lock:
            if interval_ms is not None:
                self.auto_interval_ms = interval_ms
            self._stop_requested = False
            self.status = SimStatus.RUNNING
            self._advance_event.set()

    async def stop(self):
        async with self._lock:
            self._stop_requested = True
            self.status = SimStatus.STOPPED
            self._advance_event.set()

    async def reset(self):
        async with self._lock:
            self._stop_requested = True
            self.status = SimStatus.IDLE
            self.current_tick = 0
            self._steps_requested = 0
            self._advance_event.set()

    def mark_finished(self):
        self.status = SimStatus.FINISHED
        self._advance_event.set()

    def set_waiting_llm(self, waiting: bool):
        if waiting and self.status == SimStatus.RUNNING:
            self.status = SimStatus.WAITING_LLM
        elif not waiting and self.status == SimStatus.WAITING_LLM:
            self.status = SimStatus.RUNNING

    def tick_completed(self, tick: int):
        self.current_tick = tick + 1

    async def wait_for_next_tick(self) -> bool:
        """Block until the next tick should run. Returns False if simulation should end."""
        while True:
            if self._stop_requested:
                return False
            if self.status == SimStatus.FINISHED:
                return False

            if self.status == SimStatus.RUNNING:
                return True

            if self.status == SimStatus.PAUSED and self._steps_requested > 0:
                self._steps_requested -= 1
                return True

            if self.status == SimStatus.WAITING_LLM:
                await asyncio.sleep(0.05)
                continue

            self._advance_event.clear()
            await self._advance_event.wait()

    def snapshot(self) -> dict:
        return {
            "status": self.status.value,
            "current_tick": self.current_tick,
            "total_ticks": self.total_ticks,
            "auto_interval_ms": self.auto_interval_ms,
        }
