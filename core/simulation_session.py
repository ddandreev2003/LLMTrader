"""Build and run a controllable simulation session for the web API."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Callable

import yaml

from agents.live_viz_agent import LiveVizAgent
from agents.portfolio_strategy_agent import create_strategy_agents_from_config
from agents.report_agent import ReportAgent
from agents.shock_agent import ShockAgent
from agents.strategy_agent import StrategyAgent
from core.agent_portfolio import AgentPortfolioRegistry
from core.data.moex_loader import compute_warmup_ticks, load_from_portfolio_config
from core.event_bus import EventBus
from core.market import MarketEngine
from core.multi_asset_market import MultiAssetMarketEngine
from core.portfolio import Portfolio
from core.simulation_controller import SimulationController, SimStatus
from shachi_shock.bridge import ShachiShockBridge

ROOT = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def list_config_presets() -> list[dict]:
    config_dir = ROOT / "config"
    presets = []
    for path in sorted(config_dir.glob("*.yaml")):
        presets.append({"name": path.name, "path": f"config/{path.name}"})
    return presets


class SimulationSession:
    """Single simulation instance: bus, agents, market, controller."""

    def __init__(self, on_state_change: Callable[[dict], Any] | None = None):
        self.root = ROOT
        self.on_state_change = on_state_change
        self.bus: EventBus | None = None
        self.controller = SimulationController()
        self.market = None
        self.live_viz: LiveVizAgent | None = None
        self._run_task: asyncio.Task | None = None
        self._config = self._default_config()
        self.price_data = None
        self.warmup_ticks = 0
        self.strategy_agents: list = []

    def _on_viz_state(self, viz_state: dict):
        if self.on_state_change:
            self.on_state_change(self.get_state())

    def _default_config(self) -> dict:
        return {
            "market_mode": os.environ.get("MARKET_MODE", "moex"),
            "shock_backend": os.environ.get("SHOCK_BACKEND", "legacy"),
            "shock_interval": int(os.environ.get("SHOCK_INTERVAL", "30")),
            "sim_n_ticks": None,
            "initial_cash_rub": 1_000_000,
            "portfolio_config": os.environ.get("PORTFOLIO_CONFIG", "config/portfolio_ru.yaml"),
            "strategies_config": os.environ.get("STRATEGIES_CONFIG", "config/strategies_ru.yaml"),
            "auto_play_interval_ms": int(os.environ.get("WEB_AUTO_INTERVAL_MS", "500")),
            "moex_offline": _env_bool("MOEX_OFFLINE", default=True),
            "portfolio_targets": {},
        }

    def can_edit_config(self) -> bool:
        status = self.controller.status
        tick = self.controller.current_tick
        return status in (SimStatus.IDLE, SimStatus.PAUSED) and tick == 0

    def get_config(self) -> dict:
        return dict(self._config)

    def update_config(self, updates: dict) -> dict:
        if not self.can_edit_config():
            raise ValueError("Config can only be changed in idle or paused at tick 0")
        allowed = {
            "market_mode",
            "shock_backend",
            "shock_interval",
            "sim_n_ticks",
            "initial_cash_rub",
            "portfolio_config",
            "strategies_config",
            "auto_play_interval_ms",
            "moex_offline",
            "portfolio_targets",
        }
        for key, value in updates.items():
            if key in allowed and value is not None:
                self._config[key] = value
        if "auto_play_interval_ms" in updates and updates["auto_play_interval_ms"] is not None:
            self.controller.auto_interval_ms = int(updates["auto_play_interval_ms"])
        return self.get_config()

    def _load_portfolio_yaml(self) -> dict:
        path = self.root / self._config["portfolio_config"]
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        p_cfg = cfg.setdefault("portfolio", {})
        if self._config.get("initial_cash_rub"):
            p_cfg["initial_cash_rub"] = float(self._config["initial_cash_rub"])
        return cfg

    def _create_shock_backend(self, bus, shock_interval: int):
        backend = str(self._config.get("shock_backend", "legacy")).strip().lower()
        if backend == "legacy":
            return ShockAgent(bus, shock_interval_ticks=shock_interval)
        return ShachiShockBridge(bus, shock_interval_ticks=shock_interval)

    def build(self):
        """Construct bus, agents, portfolio, market (does not start loop)."""
        self.bus = EventBus()
        bus = self.bus
        market_mode = str(self._config.get("market_mode", "moex")).strip().lower()

        if market_mode == "moex":
            portfolio_cfg = self._load_portfolio_yaml()
            offline = bool(self._config.get("moex_offline", True))
            self.price_data = load_from_portfolio_config(portfolio_cfg, offline=offline)
            data_cfg = portfolio_cfg.get("data", {})
            trade_from = data_cfg.get("from", "2024-01-01")
            self.warmup_ticks = compute_warmup_ticks(self.price_data, trade_from)

            p_cfg = portfolio_cfg.get("portfolio", {})
            trade_cutoff_ticks = int(p_cfg.get("trade_cutoff_ticks", 5))
            total_capital = float(p_cfg.get("initial_cash_rub", 1_000_000))

            strategies_path = self._config.get("strategies_config", "config/strategies_ru.yaml")
            self.strategy_agents = create_strategy_agents_from_config(
                bus, self.root / strategies_path, portfolio_cfg=portfolio_cfg
            )

            n_ticks = self._config.get("sim_n_ticks") or len(self.price_data)
            n_ticks = int(n_ticks)

            portfolio = AgentPortfolioRegistry(
                bus,
                agents=self.strategy_agents,
                total_capital=total_capital,
                max_position_pct=float(p_cfg.get("agent_max_position_pct", 0.95)),
                commission_pct=float(p_cfg.get("commission_pct", 0.0003)),
            )

            shock_interval = int(self._config.get("shock_interval", 30))
            for agent in self.strategy_agents:
                agent.total_ticks = n_ticks

            shock_backend = self._create_shock_backend(bus, shock_interval)
            report_agent = ReportAgent(bus)
            self.live_viz = LiveVizAgent(bus, agents=self.strategy_agents)
            if self.on_state_change:
                self.live_viz.set_state_callback(self._on_viz_state)

            portfolio.register()
            shock_backend.register()
            for agent in self.strategy_agents:
                agent.register()
            report_agent.register()
            self.live_viz.register()

            self.market = MultiAssetMarketEngine(
                bus,
                price_data=self.price_data,
                portfolio=portfolio,
                warmup_ticks=self.warmup_ticks,
                trade_cutoff_ticks=trade_cutoff_ticks,
            )
            self.controller.configure(
                total_ticks=n_ticks,
                auto_interval_ms=int(self._config.get("auto_play_interval_ms", 500)),
            )
        else:
            n_ticks = int(self._config.get("sim_n_ticks") or 300)
            shock_interval = int(self._config.get("shock_interval", 60))
            portfolio = Portfolio(bus)
            shock_backend = self._create_shock_backend(bus, shock_interval)
            strategy_agent = StrategyAgent(bus, call_interval_ticks=10)
            report_agent = ReportAgent(bus)
            self.strategy_agents = [strategy_agent]
            self.live_viz = LiveVizAgent(bus, agents=self.strategy_agents)
            if self.on_state_change:
                self.live_viz.set_state_callback(self._on_viz_state)

            portfolio.register()
            shock_backend.register()
            strategy_agent.register()
            report_agent.register()
            self.live_viz.register()

            self.market = MarketEngine(bus, portfolio=portfolio, start_price=100.0, tick_interval=0.02)
            self.controller.configure(
                total_ticks=n_ticks,
                auto_interval_ms=int(self._config.get("auto_play_interval_ms", 500)),
            )

    async def _run_loop(self):
        assert self.bus is not None and self.market is not None
        n_ticks = self.controller.total_ticks
        if hasattr(self.market, "run_controlled"):
            await asyncio.gather(
                self.bus.run(),
                self.market.run_controlled(self.controller, n_ticks=n_ticks),
            )
        else:
            await asyncio.gather(self.bus.run(), self.market.run(n_ticks=n_ticks))
            self.controller.mark_finished()
        self._notify_state()

    def _notify_state(self):
        if self.on_state_change and self.live_viz:
            self.on_state_change(self.get_state())

    def get_state(self) -> dict:
        ctrl = self.controller.snapshot()
        viz_state = self.live_viz.get_state() if self.live_viz else {}
        return {
            **ctrl,
            "tick": ctrl["current_tick"],
            "date": viz_state.get("date", ""),
            "halted": viz_state.get("halted", False),
            "benchmark_price": viz_state.get("benchmark_price", 0),
            "prices": viz_state.get("prices", {}),
            "agents": viz_state.get("agents", {}),
            "shocks": viz_state.get("shocks", []),
            "active_shocks": viz_state.get("active_shocks", []),
            "price_history": viz_state.get("price_history", {}),
            "agent_equity": viz_state.get("agent_equity", {}),
            "recent_events": viz_state.get("recent_events", []),
            "config": self.get_config(),
            "warmup_ticks": self.warmup_ticks,
        }

    async def start(self):
        if self._run_task and not self._run_task.done():
            return
        if self.bus is None:
            self.build()
        await self.controller.start()
        self._run_task = asyncio.create_task(self._run_loop())

    async def pause(self):
        await self.controller.pause()

    async def step(self):
        return await self.controller.step()

    async def play(self, interval_ms: int | None = None):
        await self.controller.play(interval_ms)

    async def stop(self):
        await self.controller.stop()
        if self._run_task and not self._run_task.done():
            try:
                await asyncio.wait_for(self._run_task, timeout=5.0)
            except asyncio.TimeoutError:
                self._run_task.cancel()
        self._notify_state()

    async def reset(self):
        await self.stop()
        await self.controller.reset()
        self.bus = None
        self.market = None
        self.live_viz = None
        self._run_task = None
        self.price_data = None
        self.strategy_agents = []
        self._notify_state()
