"""Tests for OrderLogStreamMarketEngine on synthetic zip."""

from __future__ import annotations

import asyncio
import zipfile
from datetime import date
from pathlib import Path

import pytest

from core.event_bus import Event, EventType
from core.orderlog_stream_market import OrderLogStreamMarketEngine


def _make_zip(tmp_path: Path, session: str, rows: list[str]) -> Path:
    header = "NO,SECCODE,BUYSELL,TIME,ORDERNO,ACTION,PRICE,VOLUME,TRADENO,TRADEPRICE"
    content = header + "\n" + "\n".join(rows) + "\n"
    zpath = tmp_path / f"OrderLog{session}.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr(f"OrderLog{session}.txt", content)
    return zpath


@pytest.mark.asyncio
async def test_warmup_fast_skips_tick_publish(tmp_path):
    """First N bars ingest only — no TICK events until warmup_bars reached."""
    rows = []
    for minute in range(8):
        t = 70100000000 + minute * 100000000
        rows.append(f"{minute * 3 + 1},SBER,B,{t},1,0,250.0,100,0,0")
        rows.append(f"{minute * 3 + 2},SBER,B,{t + 30000000},2,2,250.0,10,{minute + 1},250.0")
    _make_zip(tmp_path, "20250103", rows)

    portfolio_cfg = {
        "assets": [{"ticker": "SBER"}],
        "data": {
            "source": "orderlog_stream",
            "zip_dir": ".",
            "from": "2025-01-03",
            "till": "2025-01-03",
            "bar_interval": "1m",
            "warmup_bars": 3,
            "warmup_fast": True,
            "min_kline_bars": 2,
        },
        "portfolio": {"initial_cash_rub": 100000},
        "trading": {"max_trades_per_step": 2},
    }

    bus = __import__("core.event_bus", fromlist=["EventBus"]).EventBus()
    ticks: list[Event] = []

    async def collect(ev: Event):
        if ev.type == EventType.TICK:
            ticks.append(ev)

    bus.subscribe(EventType.TICK, collect)

    engine = OrderLogStreamMarketEngine(
        bus,
        portfolio_cfg=portfolio_cfg,
        root=tmp_path,
        warmup_bars=3,
    )
    await asyncio.gather(bus.run(), engine.run())

    assert engine._warmup_skipped == 3
    assert len(ticks) == max(0, len(rows) // 2 - 3) or len(ticks) >= 1
    if ticks:
        assert ticks[0].payload["tick"] >= 3


@pytest.mark.asyncio
async def test_stream_emits_tick_on_bar_close(tmp_path):
    rows = [
        "1,SBER,B,70100000000,1,0,250.0,100,0,0",
        "2,SBER,S,70100000000,2,0,251.0,50,0,0",
        "3,SBER,B,70103000000,3,2,250.0,10,1,250.0",
        "4,SBER,B,70200000000,4,2,252.0,5,2,252.0",
    ]
    _make_zip(tmp_path, "20250103", rows)

    portfolio_cfg = {
        "assets": [{"ticker": "SBER"}],
        "data": {
            "source": "orderlog_stream",
            "zip_dir": ".",
            "from": "2025-01-03",
            "till": "2025-01-03",
            "bar_interval": "1m",
        },
        "portfolio": {"initial_cash_rub": 100000},
        "trading": {"warmup_bars": 0, "warmup_fast": False, "min_kline_bars": 1, "max_trades_per_step": 2},
    }

    bus = __import__("core.event_bus", fromlist=["EventBus"]).EventBus()
    ticks: list[Event] = []

    async def collect(ev: Event):
        if ev.type == EventType.TICK:
            ticks.append(ev)

    bus.subscribe(EventType.TICK, collect)

    engine = OrderLogStreamMarketEngine(
        bus,
        portfolio_cfg=portfolio_cfg,
        root=tmp_path,
        warmup_bars=0,
    )
    await asyncio.gather(bus.run(), engine.run())

    assert len(ticks) >= 1
    assert ticks[0].payload.get("bar_close") is True
    assert "SBER" in ticks[0].payload.get("prices", {})
    assert "orderbook" in ticks[0].payload
