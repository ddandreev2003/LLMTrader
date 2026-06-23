"""Build and run a controllable simulation session for the web API."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Callable

import yaml

from agents.coordinator_agent import create_intraday_agents_from_config
from agents.live_viz_agent import LiveVizAgent
from agents.market_maker_agent import create_market_maker_agent_if_enabled
from agents.portfolio_strategy_agent import create_strategy_agents_from_config
from agents.report_agent import ReportAgent
from agents.shock_agent import ShockAgent
from agents.strategy_agent import StrategyAgent
from core.agent_portfolio import AgentPortfolioRegistry
from core.data.moex_loader import compute_warmup_ticks, load_from_portfolio_config
from core.data.orderlog_bars import compute_warmup_bars, load_orderlog_ohlc, resolve_sim_tick_count, resolve_stream_max_bars
from core.event_bus import EventBus
from core.intraday_market import IntradayMarketEngine, parse_intraday_engine_config
from core.orderlog_stream_market import OrderLogStreamMarketEngine
from core.data.orderlog_parser import resolve_stream_config
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
        self.coordinator = None

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
        if backend in ("none", "off", "disabled"):
            return None
        if backend == "legacy":
            return ShockAgent(bus, shock_interval_ticks=shock_interval)
        return ShachiShockBridge(
            bus, shock_interval_ticks=shock_interval, controller=self.controller
        )

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
            if shock_backend:
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
        elif market_mode == "orderlog_intraday":
            portfolio_cfg = self._load_portfolio_yaml()
            data_cfg = portfolio_cfg.get("data", {})
            bars_path = self.root / data_cfg.get("bars_path", "data/local/orderlog_bars_tls_5m.parquet")
            if not bars_path.exists():
                raise FileNotFoundError(
                    f"Bars file not found: {bars_path}. Run scripts/preprocess_orderlog_bars.py"
                )
            self.price_data, ohlc_data = load_orderlog_ohlc(portfolio_cfg, root=self.root)
            engine_cfg = parse_intraday_engine_config(portfolio_cfg)
            warmup_bars = compute_warmup_bars(
                self.price_data, int(data_cfg.get("warmup_bars", 24))
            )
            if portfolio_cfg.get("trading", {}).get("warmup_bars") is not None:
                warmup_bars = int(portfolio_cfg["trading"]["warmup_bars"])
            self.warmup_ticks = warmup_bars

            p_cfg = portfolio_cfg.get("portfolio", {})
            trade_cutoff_bars = int(
                p_cfg.get("trade_cutoff_bars", p_cfg.get("trade_cutoff_ticks", 6))
            )
            if portfolio_cfg.get("trading", {}).get("trade_cutoff_bars") is not None:
                trade_cutoff_bars = int(portfolio_cfg["trading"]["trade_cutoff_bars"])
            total_capital = float(p_cfg.get("initial_cash_rub", 1_000_000))
            bar_interval = data_cfg.get("bar_interval", "5m")

            strategies_path = self._config.get(
                "strategies_config", "config/strategies_intraday_tls.yaml"
            )
            ticker_agents, self.coordinator = create_intraday_agents_from_config(
                bus, self.root / strategies_path, portfolio_cfg=portfolio_cfg
            )
            self.strategy_agents = ticker_agents

            n_ticks = resolve_sim_tick_count(
                self.price_data,
                portfolio_cfg,
                env_sim_n_ticks=os.environ.get("SIM_N_TICKS"),
                config_sim_n_ticks=self._config.get("sim_n_ticks"),
            )

            portfolio = AgentPortfolioRegistry(
                bus,
                agents=self.strategy_agents,
                total_capital=total_capital,
                max_position_pct=float(p_cfg.get("agent_max_position_pct", 0.95)),
                commission_pct=float(p_cfg.get("commission_pct", 0.0003)),
            )
            portfolio.max_trades_per_step = engine_cfg["max_trades_per_step"]

            shock_interval = int(self._config.get("shock_interval", 36))
            for agent in self.strategy_agents:
                agent.total_ticks = n_ticks

            shock_backend = self._create_shock_backend(bus, shock_interval)
            report_agent = ReportAgent(bus)
            self.live_viz = LiveVizAgent(
                bus, agents=self.strategy_agents, max_history=min(n_ticks, 5000)
            )
            if self.on_state_change:
                self.live_viz.set_state_callback(self._on_viz_state)

            portfolio.register()
            if shock_backend:
                shock_backend.register()
            for agent in self.strategy_agents:
                agent.register()
            if self.coordinator:
                self.coordinator.register()
            report_agent.register()
            self.live_viz.register()

            trading_days = len({ts.date() for ts in self.price_data.index[:n_ticks]})
            self.live_viz.set_sim_scope(n_ticks, trading_days)

            self.market = IntradayMarketEngine(
                bus,
                price_data=self.price_data,
                portfolio=portfolio,
                warmup_bars=warmup_bars,
                trade_cutoff_bars=trade_cutoff_bars,
                bar_interval=bar_interval,
                ohlc_data=ohlc_data,
                **engine_cfg,
            )
            self.controller.configure(
                total_ticks=n_ticks,
                auto_interval_ms=int(self._config.get("auto_play_interval_ms", 500)),
            )
        elif market_mode == "orderlog_stream":
            portfolio_cfg = self._load_portfolio_yaml()
            stream = resolve_stream_config(portfolio_cfg, self.root)
            if not stream["zip_paths"]:
                raise FileNotFoundError(f"No OrderLog zips in {stream['zip_dir']}")

            engine_cfg = parse_intraday_engine_config(portfolio_cfg)
            data_cfg = portfolio_cfg.get("data", {})
            warmup_bars = int(data_cfg.get("warmup_bars", 24))
            if portfolio_cfg.get("trading", {}).get("warmup_bars") is not None:
                warmup_bars = int(portfolio_cfg["trading"]["warmup_bars"])

            p_cfg = portfolio_cfg.get("portfolio", {})
            trade_cutoff_bars = int(
                p_cfg.get("trade_cutoff_bars", p_cfg.get("trade_cutoff_ticks", 6))
            )
            if portfolio_cfg.get("trading", {}).get("trade_cutoff_bars") is not None:
                trade_cutoff_bars = int(portfolio_cfg["trading"]["trade_cutoff_bars"])
            total_capital = float(p_cfg.get("initial_cash_rub", 1_000_000))
            self.warmup_ticks = warmup_bars

            strategies_path = self._config.get(
                "strategies_config", "config/strategies_quantagent_tgs.yaml"
            )
            ticker_agents, self.coordinator = create_intraday_agents_from_config(
                bus, self.root / strategies_path, portfolio_cfg=portfolio_cfg
            )
            self.strategy_agents = ticker_agents
            mm_agent = create_market_maker_agent_if_enabled(bus, portfolio_cfg)
            viz_agents = list(ticker_agents)
            if mm_agent:
                viz_agents.append(mm_agent)

            n_ticks = resolve_stream_max_bars(
                portfolio_cfg,
                self._config.get("sim_n_ticks") or os.environ.get("SIM_N_TICKS"),
            )

            portfolio = AgentPortfolioRegistry(
                bus,
                agents=self.strategy_agents,
                total_capital=total_capital,
                max_position_pct=float(p_cfg.get("agent_max_position_pct", 0.95)),
                commission_pct=float(p_cfg.get("commission_pct", 0.0003)),
            )
            portfolio.max_trades_per_step = engine_cfg["max_trades_per_step"]

            shock_interval = int(self._config.get("shock_interval", 36))
            for agent in self.strategy_agents:
                if n_ticks:
                    agent.total_ticks = n_ticks

            shock_backend = self._create_shock_backend(bus, shock_interval)
            report_agent = ReportAgent(bus)
            self.live_viz = LiveVizAgent(bus, agents=viz_agents, max_history=5000)
            if self.on_state_change:
                self.live_viz.set_state_callback(self._on_viz_state)

            portfolio.register()
            if shock_backend:
                shock_backend.register()
            for agent in self.strategy_agents:
                agent.register()
            if mm_agent:
                mm_agent.register()
            if self.coordinator:
                self.coordinator.register()
            report_agent.register()
            self.live_viz.register()

            self.market = OrderLogStreamMarketEngine(
                bus,
                portfolio_cfg=portfolio_cfg,
                root=self.root,
                portfolio=portfolio,
                warmup_bars=warmup_bars,
                trade_cutoff_bars=trade_cutoff_bars,
                max_bars=n_ticks,
            )
            est_ticks = n_ticks or 5000
            self.controller.configure(
                total_ticks=est_ticks,
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
            if shock_backend:
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
            "datetime": viz_state.get("datetime", ""),
            "bar_interval": viz_state.get("bar_interval", "5m"),
            "micro_phase": viz_state.get("micro_phase", ""),
            "hft_mode": viz_state.get("hft_mode", False),
            "halted": viz_state.get("halted", False),
            "benchmark_price": viz_state.get("benchmark_price", 0),
            "prices": viz_state.get("prices", {}),
            "agents": viz_state.get("agents", {}),
            "shocks": viz_state.get("shocks", []),
            "active_shocks": viz_state.get("active_shocks", []),
            "price_history": viz_state.get("price_history", {}),
            "bar_history": viz_state.get("bar_history", {}),
            "agent_equity": viz_state.get("agent_equity", {}),
            "agent_trades": viz_state.get("agent_trades", {}),
            "agents_meta": viz_state.get("agents_meta", {}),
            "sim_trading_days": viz_state.get("sim_trading_days", 0),
            "sim_total_days": viz_state.get("sim_total_days", 0),
            "recent_events": viz_state.get("recent_events", []),
            "event_counts": viz_state.get("event_counts", {}),
            "orderbook": viz_state.get("orderbook", {}),
            "quant_reports": viz_state.get("quant_reports", {}),
            "orderlog_stream": viz_state.get("orderlog_stream", False),
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
        self.coordinator = None
        self._notify_state()
