import asyncio

import pandas as pd

from core.event_bus import Event, EventType

MICRO_PHASES_DEFAULT = ["open", "high", "low", "close"]


def parse_intraday_engine_config(portfolio_cfg: dict | None) -> dict:
    """Extract shock/trading engine settings from portfolio YAML."""
    cfg = portfolio_cfg or {}
    shock = cfg.get("shock_price", {})
    trading = cfg.get("trading", {})
    hft = bool(trading.get("hft_mode", False))
    return {
        "shock_price_persistent": bool(shock.get("persistent", True)),
        "impact_decay_per_bar": float(shock.get("impact_decay_per_bar", 1.0)),
        "hft_mode": hft,
        "micro_phases": list(trading.get("micro_phases") or MICRO_PHASES_DEFAULT),
        "max_trades_per_step": int(trading.get("max_trades_per_step", 6 if hft else 1)),
    }


class IntradayMarketEngine:
    """Replay intraday OHLCV bars (5m) for multiple tickers."""

    def __init__(
        self,
        bus,
        price_data: pd.DataFrame,
        portfolio=None,
        tick_interval: float = 0.0,
        warmup_bars: int = 0,
        trade_cutoff_bars: int = 0,
        bar_interval: str = "5m",
        ohlc_data: pd.DataFrame | None = None,
        shock_price_persistent: bool = True,
        impact_decay_per_bar: float = 1.0,
        hft_mode: bool = False,
        micro_phases: list[str] | None = None,
        max_trades_per_step: int = 1,
    ):
        self.bus = bus
        self.price_data = price_data
        self.ohlc_data = ohlc_data
        self.portfolio = portfolio
        self.tick_interval = tick_interval
        self.warmup_bars = warmup_bars
        self.trade_cutoff_bars = trade_cutoff_bars
        self.bar_interval = bar_interval
        self.tickers = list(price_data.columns)
        self.shock_price_persistent = shock_price_persistent
        self.impact_decay_per_bar = impact_decay_per_bar
        self.hft_mode = hft_mode
        self.micro_phases = list(micro_phases or MICRO_PHASES_DEFAULT)
        self.max_trades_per_step = max(1, max_trades_per_step)

        self._halted = False
        self._volatility_mult = 1.0
        self._current_tick = 0
        self._current_micro_step = 0
        self._current_micro_phase = "close"
        self._shock_resume_tick: int | None = None
        self._prices: dict[str, float] = {}
        self._price_multiplier: dict[str, float] = {t: 1.0 for t in self.tickers}
        self._step_trade_counts: dict[str, int] = {}
        self._subscribed = False

        if portfolio is not None and hasattr(portfolio, "max_trades_per_step"):
            portfolio.max_trades_per_step = self.max_trades_per_step

    def _ensure_subscribed(self):
        if self._subscribed:
            return
        self.bus.subscribe(EventType.STRATEGY_SIGNAL, self.on_signal)
        self.bus.subscribe(EventType.SHOCK_TRIGGERED, self.on_shock)
        self._subscribed = True

    async def run(self, n_ticks: int | None = None):
        self._ensure_subscribed()
        total = n_ticks if n_ticks is not None else len(self.price_data)
        total = min(total, len(self.price_data))

        await self.bus.publish(Event(type=EventType.SIM_STARTED, source="Market"))

        for i in range(total):
            if not await self.advance_tick(i, total):
                break
            if self.tick_interval > 0:
                await asyncio.sleep(self.tick_interval)

        await self._finish()

    async def run_controlled(self, controller, n_ticks: int | None = None):
        from core.simulation_controller import SimStatus

        self._ensure_subscribed()
        total = n_ticks if n_ticks is not None else len(self.price_data)
        total = min(total, len(self.price_data))
        controller.configure(total_ticks=total)

        await self.bus.publish(Event(type=EventType.SIM_STARTED, source="Market"))

        i = 0
        while i < total:
            if not await controller.wait_for_next_tick():
                break
            if not await self.advance_tick(i, total):
                break
            controller.tick_completed(i)
            i += 1
            if controller.status == SimStatus.RUNNING and controller.auto_interval_ms > 0:
                await asyncio.sleep(controller.auto_interval_ms / 1000.0)

        if not controller._stop_requested:
            await self._finish()
            controller.mark_finished()
        else:
            self.bus.stop()

    def _apply_impact_decay(self):
        if self.impact_decay_per_bar >= 1.0:
            return
        for t in self.tickers:
            self._price_multiplier[t] *= self.impact_decay_per_bar

    def _base_prices_at(self, i: int) -> dict[str, float]:
        row = self.price_data.iloc[i]
        return {t: float(row[t]) for t in self.tickers if pd.notna(row[t])}

    def _scaled_prices(self, base: dict[str, float]) -> dict[str, float]:
        out = {}
        for t, v in base.items():
            mult = self._price_multiplier.get(t, 1.0)
            out[t] = round(max(v * mult, 0.01), 4)
        return out

    def _bars_raw_for_tick(self, i: int) -> dict[str, dict]:
        bars: dict[str, dict] = {}
        if self.ohlc_data is not None and i < len(self.ohlc_data):
            row = self.ohlc_data.iloc[i]
            for t in self.tickers:
                prefix = f"{t}_"
                try:
                    bars[t] = {
                        "open": float(row[f"{prefix}open"]),
                        "high": float(row[f"{prefix}high"]),
                        "low": float(row[f"{prefix}low"]),
                        "close": float(row[f"{prefix}close"]),
                        "volume": float(row[f"{prefix}volume"]),
                    }
                except (KeyError, TypeError, ValueError):
                    c = float(self.price_data.iloc[i][t])
                    bars[t] = {"open": c, "high": c, "low": c, "close": c, "volume": 0}
        else:
            for t in self.tickers:
                c = float(self.price_data.iloc[i][t])
                bars[t] = {"open": c, "high": c, "low": c, "close": c, "volume": 0}
        return bars

    def _bars_for_tick(self, i: int) -> dict[str, dict]:
        bars = self._bars_raw_for_tick(i)
        scaled: dict[str, dict] = {}
        for t, b in bars.items():
            mult = self._price_multiplier.get(t, 1.0)
            scaled[t] = {
                "open": round(b["open"] * mult, 4),
                "high": round(b["high"] * mult, 4),
                "low": round(b["low"] * mult, 4),
                "close": round(b["close"] * mult, 4),
                "volume": round(b["volume"], 2),
            }
        return scaled

    def _prices_for_phase(self, bars_raw: dict[str, dict], base: dict[str, float], phase: str) -> dict[str, float]:
        key = phase if phase in ("open", "high", "low", "close") else "close"
        out: dict[str, float] = {}
        for t in self.tickers:
            if t in bars_raw:
                raw = bars_raw[t][key]
            else:
                raw = base.get(t, 0)
            mult = self._price_multiplier.get(t, 1.0)
            out[t] = round(max(raw * mult, 0.01), 4)
        return out

    async def advance_tick(self, i: int, total: int) -> bool:
        self._current_tick = i
        self._apply_impact_decay()
        await self._maybe_resume_trading(i)

        ts = pd.Timestamp(self.price_data.index[i])
        datetime_str = ts.isoformat()
        session_date = str(ts.date())
        base = self._base_prices_at(i)
        bars_raw = self._bars_raw_for_tick(i)
        bars_scaled = self._bars_for_tick(i)

        trading_enabled = i >= self.warmup_bars
        if self.trade_cutoff_bars > 0 and i >= total - self.trade_cutoff_bars:
            trading_enabled = False

        phases = self.micro_phases if self.hft_mode else ["close"]

        for step_idx, phase in enumerate(phases):
            self._current_micro_step = step_idx
            self._current_micro_phase = phase
            self._step_trade_counts = {}
            self._prices = self._prices_for_phase(bars_raw, base, phase)

            portfolio_pnl = 0.0
            portfolio_value = 0.0
            cash = 0.0
            total_trades = 0
            if self.portfolio is not None:
                portfolio_pnl = self.portfolio.total_pnl(self._prices)
                portfolio_value = self.portfolio.portfolio_value(self._prices)
                cash = self.portfolio.cash
                total_trades = len(self.portfolio.trades)

            agent_portfolios = {}
            if self.portfolio is not None and hasattr(self.portfolio, "agent_snapshots"):
                agent_portfolios = self.portfolio.agent_snapshots(self._prices)

            tick_payload = {
                "tick": i,
                "date": session_date,
                "datetime": datetime_str,
                "session_date": session_date,
                "bar_interval": self.bar_interval,
                "intraday": True,
                "hft_mode": self.hft_mode,
                "micro_phase": phase,
                "micro_step": step_idx,
                "micro_steps_total": len(phases),
                "prices": dict(self._prices),
                "price": self._benchmark_price(),
                "halted": self._halted,
                "portfolio_pnl": portfolio_pnl,
                "portfolio_value": portfolio_value,
                "cash": cash,
                "position": sum(self.portfolio.positions.values()) if self.portfolio else 0,
                "total_trades": total_trades,
                "volatility_mult": self._volatility_mult,
                "trading_enabled": trading_enabled,
                "warmup": i < self.warmup_bars,
                "total_ticks": total,
                "trade_cutoff_ticks": self.trade_cutoff_bars,
                "agent_portfolios": agent_portfolios,
                "tickers": list(self.tickers),
                "bars": bars_scaled,
                "price_multipliers": dict(self._price_multiplier),
            }

            await self.bus.publish(Event(type=EventType.TICK, payload=tick_payload, source="Market"))
            await self.bus.drain()

            await self.bus.publish(
                Event(type=EventType.BAR_PROPOSALS_READY, payload=dict(tick_payload), source="Market")
            )
            await self.bus.drain()

        return True

    async def _finish(self):
        await self.bus.drain()
        stats = self._final_stats()
        await self.bus.publish(Event(type=EventType.SIM_ENDED, payload=stats, source="Market"))
        await self.bus.drain()
        self.bus.stop()

    def reset_market_state(self):
        self._halted = False
        self._volatility_mult = 1.0
        self._current_tick = 0
        self._current_micro_step = 0
        self._current_micro_phase = "close"
        self._shock_resume_tick = None
        self._prices = {}
        self._price_multiplier = {t: 1.0 for t in self.tickers}
        self._step_trade_counts = {}

    def _benchmark_price(self) -> float:
        if not self._prices:
            return 100.0
        return sum(self._prices.values()) / len(self._prices)

    async def _maybe_resume_trading(self, tick: int):
        if self._halted and self._shock_resume_tick is not None and tick >= self._shock_resume_tick:
            self._halted = False
            self._shock_resume_tick = None
            await self.bus.publish(Event(type=EventType.TRADING_RESUMED, source="Market"))

    async def _publish_prices_updated(self):
        await self.bus.publish(
            Event(
                type=EventType.PRICES_UPDATED,
                payload={
                    "tick": self._current_tick,
                    "micro_step": self._current_micro_step,
                    "micro_phase": self._current_micro_phase,
                    "prices": dict(self._prices),
                    "price": self._benchmark_price(),
                    "price_multipliers": dict(self._price_multiplier),
                },
                source="Market",
            )
        )

    async def on_signal(self, event: Event):
        if self._halted:
            await self.bus.publish(
                Event(
                    type=EventType.ORDER_REJECTED,
                    payload={"reason": "trading halted"},
                    source="Market",
                )
            )
            return

        if self._current_tick < self.warmup_bars:
            await self.bus.publish(
                Event(
                    type=EventType.ORDER_REJECTED,
                    payload={"reason": "warmup period: trading disabled"},
                    source="Market",
                )
            )
            return

        ticker = event.payload.get("ticker", self.tickers[0] if self.tickers else "")
        agent_id = event.payload.get("agent_id", "")
        action = event.payload.get("action", "hold")
        qty = int(event.payload.get("quantity", 0))
        trade_key = f"{agent_id}:{ticker}" if agent_id else ticker

        if self._step_trade_counts.get(trade_key, 0) >= self.max_trades_per_step:
            await self.bus.publish(
                Event(
                    type=EventType.ORDER_REJECTED,
                    payload={
                        "reason": f"max {self.max_trades_per_step} trades per micro-step",
                        "ticker": ticker,
                    },
                    source="Market",
                )
            )
            return

        if action not in ("buy", "sell") or qty <= 0:
            return

        price = self._prices.get(ticker)
        if price is None:
            await self.bus.publish(
                Event(
                    type=EventType.ORDER_REJECTED,
                    payload={"reason": f"unknown ticker {ticker}", "ticker": ticker},
                    source="Market",
                )
            )
            return

        if self.portfolio is not None:
            check = Event(
                type=EventType.ORDER_PLACED,
                payload={
                    "ticker": ticker,
                    "action": action,
                    "quantity": qty,
                    "price": price,
                    "tick": self._current_tick,
                    "micro_step": self._current_micro_step,
                    "agent_id": agent_id,
                    "strategy": event.payload.get("strategy", ""),
                },
                source="Market",
            )
            rejection = await self.portfolio.on_order_request(check)
            if rejection is not None:
                await self.bus.publish(rejection)
                return

        ts = pd.Timestamp(self.price_data.index[self._current_tick])
        filled = {
            "ticker": ticker,
            "agent_id": agent_id,
            "strategy": event.payload.get("strategy", ""),
            "action": action,
            "quantity": qty,
            "price": price,
            "tick": self._current_tick,
            "micro_step": self._current_micro_step,
            "micro_phase": self._current_micro_phase,
            "date": str(ts.date()),
            "datetime": ts.isoformat(),
        }
        self._step_trade_counts[trade_key] = self._step_trade_counts.get(trade_key, 0) + 1
        await self.bus.publish(Event(type=EventType.ORDER_FILLED, payload=filled, source="Market"))

    async def on_shock(self, event: Event):
        shock_type = event.payload.get("type", "")
        impact = event.payload.get("price_impact_pct", 0) / 100
        target_ticker = event.payload.get("ticker")

        if self.shock_price_persistent:
            if target_ticker and target_ticker in self._price_multiplier:
                self._price_multiplier[target_ticker] *= 1 + impact
            else:
                for t in self.tickers:
                    self._price_multiplier[t] *= 1 + impact

        if target_ticker and target_ticker in self._prices:
            self._prices[target_ticker] *= 1 + impact
            self._prices[target_ticker] = max(self._prices[target_ticker], 0.01)
        else:
            for t in self._prices:
                self._prices[t] *= 1 + impact
                self._prices[t] = max(self._prices[t], 0.01)

        self._volatility_mult = event.payload.get("volatility_multiplier", 1.0)

        duration = int(event.payload.get("duration_ticks", 10))
        if shock_type in ("halt", "circuit_breaker"):
            self._halted = True
            self._shock_resume_tick = self._current_tick + duration
            await self.bus.publish(Event(type=EventType.TRADING_HALTED, source="Market"))

        await self._publish_prices_updated()

    def _final_stats(self) -> dict:
        if self.portfolio is not None:
            return self.portfolio.get_stats(self._prices)
        return {"final_prices": self._prices, "total_pnl": 0.0}
