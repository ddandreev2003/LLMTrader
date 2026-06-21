from collections import deque

from shachi_shock.vendor.base import BaseMemory


class HistoryMemory(BaseMemory):
    """Sliding-window memory (Shachi stockagent pattern)."""

    def __init__(self, history_length: int = 5):
        self.history_length = history_length
        self.memory: list[dict[str, str]] = []

    def add_record(self, messages: list[dict[str, str]]) -> None:
        self.memory.extend(messages)

    def retrieve(self, query: str | None = None) -> str:
        messages = self.memory[-self.history_length :]
        return "\n".join(f"{m['role']}: {m['content']}" for m in messages)

    def clear(self) -> None:
        self.memory = []
