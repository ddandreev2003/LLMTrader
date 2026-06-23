"""Elapsed-time logging for simulation startup and runtime."""

from __future__ import annotations

import sys
import time
from typing import TextIO

_start: float | None = None
_log_file: TextIO | None = None


def reset_timer() -> None:
    global _start
    _start = time.perf_counter()


def elapsed() -> float:
    if _start is None:
        reset_timer()
    return time.perf_counter() - _start


def _ts() -> str:
    return f"[+{elapsed():6.1f}s]"


def bind_log_file(path) -> None:
    global _log_file
    from pathlib import Path

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    _log_file = p.open("a", encoding="utf-8")


def log(msg: str, *, flush: bool = True) -> None:
    line = f"{_ts()} {msg}"
    print(line, flush=flush)
    if _log_file is not None:
        _log_file.write(line + "\n")
        if flush:
            _log_file.flush()


def log_step(step: str, detail: str = "") -> None:
    suffix = f" — {detail}" if detail else ""
    log(f"▶ {step}{suffix}")


def log_done(step: str, detail: str = "") -> None:
    suffix = f" — {detail}" if detail else ""
    log(f"✓ {step}{suffix}")


def log_warn(msg: str) -> None:
    log(f"⚠ {msg}")


def format_count(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1e6:.2f}M"
    if n >= 1_000:
        return f"{n / 1e3:.1f}K"
    return str(n)


def format_bytes(n: int) -> str:
    if n >= 1e9:
        return f"{n / 1e9:.2f} GB"
    if n >= 1e6:
        return f"{n / 1e6:.1f} MB"
    return f"{n / 1e3:.1f} KB"
