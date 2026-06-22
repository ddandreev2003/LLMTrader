"""Tests for HFT micro-step ticks within a 5m bar."""

import asyncio
from pathlib import Path

import pandas as pd
import yaml

from agents.coordinator_agent import create_intraday_agents_from_config
from core.event_bus import EventType
from core.intraday_market import IntradayMarketEngine, parse_intraday_engine_config


def test_hft_agents_use_direct_signals():
    root = Path(__file__).resolve().parents[1]
    portfolio_cfg = yaml.safe_load((root / "config/portfolio_intraday_hft.yaml").read_text())
    strategies_path = root / "config/strategies_intraday_hft.yaml"

    class DummyBus:
        def subscribe(self, *args, **kwargs):
            pass

    ticker_agents, _ = create_intraday_agents_from_config(
        DummyBus(), strategies_path, portfolio_cfg=portfolio_cfg
    )
    assert len(ticker_agents) >= 1
    for agent in ticker_agents:
        assert agent.proposal_mode is False
        assert agent.hft_mode is True


def test_hft_engine_default_max_trades_per_step():
    cfg = parse_intraday_engine_config({"trading": {"hft_mode": True}})
    assert cfg["max_trades_per_step"] == 6


def test_hft_mode_emits_four_micro_ticks():
    idx = pd.date_range("2025-01-03 07:05", periods=2, freq="5min")
    close = pd.DataFrame({"T": [100.0, 101.0]}, index=idx)
    ohlc = pd.DataFrame(
        {
            "T_open": [99.0, 100.0],
            "T_high": [101.0, 102.0],
            "T_low": [98.0, 99.5],
            "T_close": [100.0, 101.0],
            "T_volume": [1000.0, 1100.0],
        },
        index=idx,
    )
    bus_events = []

    class CollectBus:
        def __init__(self):
            self._handlers = {}

        def subscribe(self, event_type, handler):
            self._handlers.setdefault(event_type, []).append(handler)

        async def publish(self, event):
            bus_events.append(event)
            for h in self._handlers.get(event.type, []):
                await h(event)

        async def drain(self):
            pass

        def stop(self):
            pass

    bus = CollectBus()
    engine = IntradayMarketEngine(
        bus,
        close,
        ohlc_data=ohlc,
        hft_mode=True,
        warmup_bars=0,
        trade_cutoff_bars=0,
        max_trades_per_step=3,
    )

    async def run():
        await engine.advance_tick(0, 2)

    asyncio.run(run())

    ticks = [e for e in bus_events if e.type == EventType.TICK]
    assert len(ticks) == 4
    phases = [e.payload["micro_phase"] for e in ticks]
    assert phases == ["open", "high", "low", "close"]
    assert ticks[0].payload["prices"]["T"] == 99.0
    assert ticks[1].payload["prices"]["T"] == 101.0
    assert ticks[2].payload["prices"]["T"] == 98.0
    assert ticks[3].payload["prices"]["T"] == 100.0
