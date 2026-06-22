"""Live simulation visualizer — streams ticks, shocks, and per-agent equity online."""

from __future__ import annotations

import json
import os
import threading
import time
from collections import Counter
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agents.base_agent import BaseAgent
from core.event_bus import Event, EventType

ROOT = Path(__file__).resolve().parent.parent
VIZ_DIR = ROOT / "viz"
OUTPUT_DIR = ROOT / "output"
STATE_FILE = OUTPUT_DIR / "live_state.json"
DASHBOARD_STATE = VIZ_DIR / "live_state.json"

SHOCK_COLORS = {
    "news_spike": "#f59e0b",
    "circuit_breaker": "#ef4444",
    "halt": "#dc2626",
    "rate_hike": "#8b5cf6",
    "short_ban": "#6366f1",
    "position_limit": "#64748b",
}


class LiveVizAgent(BaseAgent):
    """Records simulation events to live_state.json for the web dashboard."""

    def __init__(self, bus, agents: list | None = None, state_callback=None, max_history: int = 5000):
        super().__init__(bus)
        self._state_callback = state_callback
        self._agents_meta = {}
        tickers = []
        for a in agents or []:
            aid = getattr(a, "agent_id", None) or getattr(a, "name", type(a).__name__)
            strategy_name = getattr(getattr(a, "strategy", None), "name", "strategy")
            universe = list(getattr(a, "universe", None) or [getattr(a, "ticker", "SYN")])
            self._agents_meta[aid] = {
                "ticker": universe[0] if universe else "SYN",
                "tickers": universe,
                "universe": universe,
                "strategy": strategy_name,
                "display_name": getattr(a, "display_name", aid),
                "nft_color": getattr(a, "nft_color", "#3b82f6"),
            }
            for t in universe:
                if t and t not in tickers:
                    tickers.append(t)
        self._state = {
            "status": "initializing",
            "tick": 0,
            "date": "",
            "datetime": "",
            "bar_interval": "5m",
            "micro_phase": "",
            "hft_mode": False,
            "halted": False,
            "benchmark_price": 0.0,
            "prices": {},
            "agents": {},
            "agents_meta": dict(self._agents_meta),
            "agent_trades": {aid: [] for aid in self._agents_meta},
            "shocks": [],
            "active_shocks": [],
            "price_history": {"ticks": [], "dates": [], "benchmark": []},
            "bar_history": {
                "timestamps": [],
                "interval": "5m",
                "tickers": tickers,
                "candles": {t: [] for t in tickers},
            },
            "agent_equity": {aid: [] for aid in self._agents_meta},
            "sim_trading_days": 0,
            "sim_total_days": 0,
            "recent_events": [],
            "event_counts": {},
            "updated_at": 0.0,
        }
        self._max_history = max(300, int(max_history))
        self._max_agent_trades = 20
        self._max_events = 200
        self._last_datetime = ""
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self._write_state()

    def set_state_callback(self, callback):
        self._state_callback = callback

    def get_state(self) -> dict:
        return dict(self._state)

    def _write_state(self):
        self._state["updated_at"] = time.time()
        payload = json.dumps(self._state, ensure_ascii=False)
        STATE_FILE.write_text(payload, encoding="utf-8")
        VIZ_DIR.mkdir(parents=True, exist_ok=True)
        DASHBOARD_STATE.write_text(payload, encoding="utf-8")
        if self._state_callback:
            try:
                self._state_callback(dict(self._state))
            except Exception:
                pass

    def _update_event_counts(self):
        counts = Counter(e.get("kind", "other") for e in self._state["recent_events"])
        self._state["event_counts"] = dict(counts)

    def _push_event(self, kind: str, message: str, extra: dict | None = None):
        entry = {
            "ts": time.time(),
            "kind": kind,
            "message": message,
            "tick": self._state.get("tick", 0),
            "datetime": self._last_datetime,
            "agent_id": "",
            "ticker": "",
        }
        if extra:
            entry.update(extra)
        events = self._state["recent_events"]
        events.append(entry)
        if len(events) > self._max_events:
            self._state["recent_events"] = events[-self._max_events :]
        self._update_event_counts()

    async def on_sim_started(self, event: Event):
        self._state["status"] = "running"
        self._push_event("system", "Симуляция запущена")
        self._write_state()

    async def on_tick(self, event: Event):
        p = event.payload
        tick = int(p.get("tick", 0))
        self._state["tick"] = tick
        self._state["date"] = p.get("date", "")
        self._state["datetime"] = p.get("datetime", "")
        self._last_datetime = self._state["datetime"]
        self._state["bar_interval"] = p.get("bar_interval", "5m")
        self._state["micro_phase"] = p.get("micro_phase", "close")
        self._state["hft_mode"] = bool(p.get("hft_mode", False))
        self._state["halted"] = bool(p.get("halted", False))
        self._state["benchmark_price"] = round(float(p.get("price", 0)), 2)
        self._state["prices"] = {k: round(float(v), 2) for k, v in p.get("prices", {}).items()}

        tickers = list(p.get("tickers") or self._state["prices"].keys())
        bar_hist = self._state["bar_history"]
        bar_hist["interval"] = p.get("bar_interval", "5m")
        bar_hist["tickers"] = tickers
        for t in tickers:
            bar_hist["candles"].setdefault(t, [])

        ts_label = p.get("datetime", "")
        bars = p.get("bars") or {}
        append_candle = p.get("micro_phase", "close") == "close" or not p.get("hft_mode")
        if append_candle:
            bar_hist["timestamps"].append(ts_label)
            for t in tickers:
                b = bars.get(t) or {}
                candle = {
                    "o": b.get("open", self._state["prices"].get(t, 0)),
                    "h": b.get("high", self._state["prices"].get(t, 0)),
                    "l": b.get("low", self._state["prices"].get(t, 0)),
                    "c": b.get("close", self._state["prices"].get(t, 0)),
                    "v": b.get("volume", 0),
                }
                bar_hist["candles"][t].append(candle)
            if len(bar_hist["timestamps"]) > self._max_history:
                bar_hist["timestamps"] = bar_hist["timestamps"][-self._max_history :]
                for t in tickers:
                    bar_hist["candles"][t] = bar_hist["candles"][t][-self._max_history :]
        elif bar_hist["timestamps"] and bars:
            idx = len(bar_hist["timestamps"]) - 1
            for t in tickers:
                b = bars.get(t) or {}
                if t in bar_hist["candles"] and idx < len(bar_hist["candles"][t]):
                    bar_hist["candles"][t][idx] = {
                        "o": b.get("open", self._state["prices"].get(t, 0)),
                        "h": b.get("high", self._state["prices"].get(t, 0)),
                        "l": b.get("low", self._state["prices"].get(t, 0)),
                        "c": b.get("close", self._state["prices"].get(t, 0)),
                        "v": b.get("volume", 0),
                    }

        for agent_id, snap in p.get("agent_portfolios", {}).items():
            meta = self._agents_meta.get(agent_id, {})
            snap = dict(snap)
            snap["display_name"] = meta.get("display_name", agent_id)
            snap["nft_color"] = meta.get("nft_color", "#3b82f6")
            snap["universe"] = meta.get("universe", [])
            self._state["agents"][agent_id] = snap
            curve = self._state["agent_equity"].setdefault(agent_id, [])
            curve.append({"tick": tick, "value": snap.get("portfolio_value", 0), "pnl": snap.get("pnl", 0)})
            if len(curve) > self._max_history:
                self._state["agent_equity"][agent_id] = curve[-self._max_history :]

        hist = self._state["price_history"]
        hist["ticks"].append(tick)
        hist["dates"].append(p.get("date", ""))
        hist["benchmark"].append(self._state["benchmark_price"])
        if len(hist["ticks"]) > self._max_history:
            for key in ("ticks", "dates", "benchmark"):
                hist[key] = hist[key][-self._max_history :]

        unique_dates = {d for d in hist["dates"] if d}
        self._state["sim_trading_days"] = len(unique_dates)

        self._write_state()

    def set_sim_scope(self, total_bars: int, trading_days: int) -> None:
        self._state["sim_total_days"] = int(trading_days)
        self._state["sim_total_bars"] = int(total_bars)
        self._write_state()

    async def on_shock(self, event: Event):
        shock = dict(event.payload)
        shock["color"] = SHOCK_COLORS.get(shock.get("type", ""), "#94a3b8")
        shock["received_at"] = time.time()
        duration = int(shock.get("duration_ticks", 10))
        shock["expires_tick"] = shock.get("tick", self._state["tick"]) + duration
        self._state["shocks"].append(shock)
        self._state["active_shocks"].append(shock)

        impact = shock.get("price_impact_pct", 0)
        desc = shock.get("description", "")[:80]
        self._push_event(
            "shock",
            f"⚡ {shock.get('type')} {impact:+.1f}% — {desc}",
            {"tick": shock.get("tick"), "type": shock.get("type")},
        )
        self._write_state()

    async def on_prices_updated(self, event: Event):
        p = event.payload
        self._state["prices"] = {
            k: round(float(v), 2) for k, v in p.get("prices", {}).items()
        }
        self._state["benchmark_price"] = round(float(p.get("price", 0)), 2)
        self._write_state()

    async def on_halt(self, event: Event):
        self._state["halted"] = True
        self._push_event("halt", "Торговля приостановлена", {"tick": self._state["tick"]})
        self._write_state()

    async def on_resume(self, event: Event):
        self._state["halted"] = False
        self._push_event("resume", "Торговля возобновлена", {"tick": self._state["tick"]})
        self._write_state()

    async def on_trade(self, event: Event):
        p = event.payload
        agent_id = p.get("agent_id", "")
        trade_rec = {
            "tick": p.get("tick", self._state["tick"]),
            "datetime": self._last_datetime,
            "ticker": p.get("ticker", ""),
            "action": p.get("action", ""),
            "quantity": p.get("quantity", 0),
            "price": p.get("price", 0),
        }
        if agent_id:
            trades = self._state["agent_trades"].setdefault(agent_id, [])
            trades.append(trade_rec)
            if len(trades) > self._max_agent_trades:
                self._state["agent_trades"][agent_id] = trades[-self._max_agent_trades :]
        self._push_event(
            "trade",
            f"{p.get('agent_id')} {p.get('action')} {p.get('ticker')} "
            f"{p.get('quantity')} @ {p.get('price')}",
            {
                "tick": p.get("tick"),
                "agent_id": p.get("agent_id", ""),
                "ticker": p.get("ticker", ""),
            },
        )
        self._expire_active_shocks()
        self._write_state()

    async def on_reject(self, event: Event):
        p = event.payload
        self._push_event(
            "reject",
            f"Отклонено: {p.get('reason', '?')} ({p.get('ticker', '')})",
            {"tick": self._state["tick"], "ticker": p.get("ticker", "")},
        )
        self._write_state()

    async def on_signal(self, event: Event):
        p = event.payload
        action = p.get("action", "hold")
        if action == "hold":
            return
        self._push_event(
            "signal",
            f"{p.get('agent_id')} {action} {p.get('ticker')} qty={p.get('quantity')} — {p.get('reason', '')}",
            {
                "tick": p.get("tick", self._state["tick"]),
                "agent_id": p.get("agent_id", ""),
                "ticker": p.get("ticker", ""),
            },
        )
        self._write_state()

    async def on_proposal(self, event: Event):
        p = event.payload
        action = p.get("action", "hold")
        if action == "hold":
            return
        self._push_event(
            "proposal",
            f"{p.get('agent_id')} предложил {action} {p.get('ticker')} qty={p.get('quantity')}",
            {
                "tick": p.get("tick", self._state["tick"]),
                "agent_id": p.get("agent_id", ""),
                "ticker": p.get("ticker", ""),
            },
        )
        self._write_state()

    async def on_sim_ended(self, event: Event):
        self._state["status"] = "finished"
        self._state["final_stats"] = {
            k: v for k, v in event.payload.items() if k != "exposure_history"
        }
        self._push_event("system", "Симуляция завершена")
        self._write_state()

    def _expire_active_shocks(self):
        tick = self._state["tick"]
        self._state["active_shocks"] = [
            s for s in self._state["active_shocks"] if s.get("expires_tick", 0) > tick
        ]

    def register(self):
        self.bus.subscribe(EventType.SIM_STARTED, self.on_sim_started)
        self.bus.subscribe(EventType.TICK, self.on_tick)
        self.bus.subscribe(EventType.SHOCK_TRIGGERED, self.on_shock)
        self.bus.subscribe(EventType.PRICES_UPDATED, self.on_prices_updated)
        self.bus.subscribe(EventType.TRADING_HALTED, self.on_halt)
        self.bus.subscribe(EventType.TRADING_RESUMED, self.on_resume)
        self.bus.subscribe(EventType.ORDER_FILLED, self.on_trade)
        self.bus.subscribe(EventType.ORDER_REJECTED, self.on_reject)
        self.bus.subscribe(EventType.STRATEGY_SIGNAL, self.on_signal)
        self.bus.subscribe(EventType.PROPOSED_SIGNAL, self.on_proposal)
        self.bus.subscribe(EventType.SIM_ENDED, self.on_sim_ended)


def start_viz_server(port: int | None = None) -> ThreadingHTTPServer | None:
    """Start HTTP server for dashboard (runs in background thread).

    Skipped when WEB_UI=true — FastAPI serves static files instead.
    """
    if os.environ.get("WEB_UI", "").strip().lower() in ("1", "true", "yes", "on"):
        return None

    port = port or int(os.environ.get("VIZ_PORT", os.environ.get("WEB_PORT", "8765")))

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(VIZ_DIR), **kwargs)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"[LiveViz] Дашборд: http://127.0.0.1:{port}/dashboard.html")
    print(f"[LiveViz] Состояние: {STATE_FILE}")
    return server
