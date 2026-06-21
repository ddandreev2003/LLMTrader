"""Live simulation visualizer — streams ticks, shocks, and per-agent equity online."""

from __future__ import annotations

import json
import os
import threading
import time
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

    def __init__(self, bus, agents: list | None = None, state_callback=None):
        super().__init__(bus)
        self._state_callback = state_callback
        self._agents_meta = {}
        for a in agents or []:
            aid = getattr(a, "agent_id", None) or getattr(a, "name", type(a).__name__)
            strategy_name = getattr(getattr(a, "strategy", None), "name", "strategy")
            self._agents_meta[aid] = {
                "ticker": getattr(a, "ticker", "SYN"),
                "strategy": strategy_name,
            }
        self._state = {
            "status": "initializing",
            "tick": 0,
            "date": "",
            "halted": False,
            "benchmark_price": 0.0,
            "prices": {},
            "agents": {},
            "shocks": [],
            "active_shocks": [],
            "price_history": {"ticks": [], "dates": [], "benchmark": []},
            "agent_equity": {aid: [] for aid in self._agents_meta},
            "recent_events": [],
            "updated_at": 0.0,
        }
        self._max_history = 300
        self._max_events = 50
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

    def _push_event(self, kind: str, message: str, extra: dict | None = None):
        entry = {"ts": time.time(), "kind": kind, "message": message}
        if extra:
            entry.update(extra)
        events = self._state["recent_events"]
        events.append(entry)
        if len(events) > self._max_events:
            self._state["recent_events"] = events[-self._max_events :]

    async def on_sim_started(self, event: Event):
        self._state["status"] = "running"
        self._push_event("system", "Симуляция запущена")
        self._write_state()

    async def on_tick(self, event: Event):
        p = event.payload
        tick = int(p.get("tick", 0))
        self._state["tick"] = tick
        self._state["date"] = p.get("date", "")
        self._state["halted"] = bool(p.get("halted", False))
        self._state["benchmark_price"] = round(float(p.get("price", 0)), 2)
        self._state["prices"] = {k: round(float(v), 2) for k, v in p.get("prices", {}).items()}

        for agent_id, snap in p.get("agent_portfolios", {}).items():
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

    async def on_halt(self, event: Event):
        self._state["halted"] = True
        self._push_event("halt", "Торговля приостановлена")
        self._write_state()

    async def on_resume(self, event: Event):
        self._state["halted"] = False
        self._push_event("resume", "Торговля возобновлена")
        self._write_state()

    async def on_trade(self, event: Event):
        p = event.payload
        self._push_event(
            "trade",
            f"{p.get('agent_id')} {p.get('action')} {p.get('ticker')} "
            f"{p.get('quantity')} @ {p.get('price')}",
            {"tick": p.get("tick"), "agent_id": p.get("agent_id")},
        )
        self._expire_active_shocks()
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
        self.bus.subscribe(EventType.TRADING_HALTED, self.on_halt)
        self.bus.subscribe(EventType.TRADING_RESUMED, self.on_resume)
        self.bus.subscribe(EventType.ORDER_FILLED, self.on_trade)
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
