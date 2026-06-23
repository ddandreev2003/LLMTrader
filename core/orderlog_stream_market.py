"""Market engine replaying MOEX OrderLog streams with reconstructed order books."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from core.data.orderlog_parser import (
    RollingBarBuilder,
    merge_session_events,
    resolve_stream_config,
)
from core.event_bus import Event, EventType
from core.intraday_market import parse_intraday_engine_config
from core.market_maker import market_maker_from_config
from core.orderbook import OrderBookState
from core.sim_log import elapsed, format_bytes, format_count, log, log_done, log_step


class OrderLogStreamMarketEngine:
    """Replay OrderLog zip events; emit TICK on bar close."""

    def __init__(
        self,
        bus,
        portfolio_cfg: dict,
        root: Path,
        portfolio=None,
        tick_interval: float = 0.0,
        warmup_bars: int = 24,
        trade_cutoff_bars: int = 6,
        max_events: int | None = None,
        max_bars: int | None = None,
    ):
        self.bus = bus
        self.portfolio = portfolio
        self.tick_interval = tick_interval
        trading = portfolio_cfg.get("trading", {})
        data_cfg = portfolio_cfg.get("data", {})
        wb = trading.get("warmup_bars", data_cfg.get("warmup_bars", warmup_bars))
        self.warmup_bars = int(wb)
        self.min_kline_bars = int(trading.get("min_kline_bars", data_cfg.get("min_kline_bars", 14)))
        self.warmup_fast = bool(trading.get("warmup_fast", data_cfg.get("warmup_fast", True)))
        self.trade_cutoff_bars = trade_cutoff_bars
        self.max_events = max_events
        self.max_bars = max_bars
        self.kline_history_bars = int(
            data_cfg.get("kline_history_bars", trading.get("kline_history_bars", 60))
        )
        bar_history_size = max(self.kline_history_bars + 30, 120)

        stream = resolve_stream_config(portfolio_cfg, root)
        engine_cfg = parse_intraday_engine_config(portfolio_cfg)
        self.zip_paths = stream["zip_paths"]
        self.tickers = stream["tickers"]
        self.bar_minutes = stream["bar_minutes"]
        self.bar_interval = stream["bar_interval"]
        self.decision_on = stream["decision_on"]
        self.shock_price_persistent = engine_cfg["shock_price_persistent"]
        self.impact_decay_per_bar = engine_cfg["impact_decay_per_bar"]
        self.max_trades_per_step = engine_cfg["max_trades_per_step"]

        self._books = {t: OrderBookState(ticker=t) for t in self.tickers}
        self._bars = RollingBarBuilder(
            self.tickers, bar_minutes=self.bar_minutes, history_size=bar_history_size
        )
        self._halted = False
        self._volatility_mult = 1.0
        self._current_tick = 0
        self._current_bar = 0
        self._total_bars = 0
        self._shock_resume_tick: int | None = None
        self._prices: dict[str, float] = {t: 0.01 for t in self.tickers}
        self._price_multiplier: dict[str, float] = {t: 1.0 for t in self.tickers}
        self._step_trade_counts: dict[str, int] = {}
        self._last_datetime = ""
        self._last_session = ""
        self._subscribed = False
        self._event_count = 0
        self._warmup_skipped = 0
        self._stream_t0 = 0.0
        self._market_maker = market_maker_from_config(portfolio_cfg, self.tickers)
        if self._market_maker is not None:
            log_done(
                "Market Maker",
                f"активен: spread {self._market_maker.spread_bps} bps, "
                f"size {self._market_maker.quote_size}",
            )

        if portfolio is not None and hasattr(portfolio, "max_trades_per_step"):
            portfolio.max_trades_per_step = self.max_trades_per_step

    def _ensure_subscribed(self):
        if self._subscribed:
            return
        self.bus.subscribe(EventType.STRATEGY_SIGNAL, self.on_signal)
        self.bus.subscribe(EventType.SHOCK_TRIGGERED, self.on_shock)
        self._subscribed = True

    def _scaled_price(self, ticker: str, raw: float) -> float:
        mult = self._price_multiplier.get(ticker, 1.0)
        return round(max(raw * mult, 0.01), 4)

    def _update_prices_from_books(self):
        for t in self.tickers:
            book = self._books[t]
            lt = book.last_trade_price
            if lt is not None and lt > 0:
                self._prices[t] = self._scaled_price(t, lt)
                continue
            mid = book.mid_price()
            if mid is not None and mid > 0:
                self._prices[t] = self._scaled_price(t, mid)

    def _run_market_maker(
        self,
        closed_ticker: str,
        scaled_bar: dict,
        book_snap: dict,
    ) -> dict:
        """Update MM quotes/fills and merge into orderbook snapshots."""
        mm_payload: dict = {}
        if self._market_maker is None:
            return mm_payload

        ref_close = float(scaled_bar["close"])
        bar_fills: list[dict] = []
        for t in self.tickers:
            ref = ref_close if t == closed_ticker else float(self._prices.get(t, ref_close))
            quote = self._market_maker.update_quotes(t, self._books[t], ref)
            if t == closed_ticker:
                for f in self._market_maker.on_bar(
                    t,
                    open_=scaled_bar["open"],
                    high=scaled_bar["high"],
                    low=scaled_bar["low"],
                    close=scaled_bar["close"],
                    tick=self._current_tick,
                ):
                    bar_fills.append({**f, "ticker": t, "tick": self._current_tick})
                    log(
                        f"MM [{t}] {f['action']} {f['quantity']} @ {f['price']:.2f} "
                        f"({f['reason']})"
                    )
            native = book_snap[t]
            book_snap[t] = self._market_maker.merge_book_snapshot(t, native, quote)

        mm_payload = {
            "inventory": self._market_maker.inventory_summary(),
            "quotes": {t: book_snap.get(t, {}).get("mm_quote") for t in self.tickers},
            "bar_fills": bar_fills,
            "recent_fills": self._market_maker.recent_fills(20),
        }
        return mm_payload

    def _apply_event(self, ev) -> list[dict]:
        book = self._books.get(ev.ticker)
        if book is None:
            return []

        if ev.action == 0:
            book.add_order(ev.order_no, ev.side, ev.price, ev.volume)
        elif ev.action == 1:
            book.cancel_order(ev.order_no)
        elif ev.action == 2:
            trade_px = ev.trade_price if ev.trade_price > 0 else ev.price
            book.apply_trade(trade_px, ev.volume)
            return self._bars.on_trade(ev.ticker, ev.time, trade_px, ev.volume)

        return []

    def _orderbook_snapshot(self, top_n: int = 5) -> dict:
        return {t: self._books[t].snapshot(top_n) for t in self.tickers}

    def _bars_payload(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for t in self.tickers:
            cur = self._bars.current_bar(t)
            if cur:
                mult = self._price_multiplier.get(t, 1.0)
                out[t] = {
                    "open": round(cur["open"] * mult, 4),
                    "high": round(cur["high"] * mult, 4),
                    "low": round(cur["low"] * mult, 4),
                    "close": round(cur["close"] * mult, 4),
                    "volume": cur["volume"],
                }
            else:
                px = self._prices.get(t, 0.01)
                out[t] = {"open": px, "high": px, "low": px, "close": px, "volume": 0}
        return out

    def _kline_history(self) -> dict[str, list[dict]]:
        return {t: self._bars.history(t, self.kline_history_bars) for t in self.tickers}

    def _apply_impact_decay(self):
        if self.impact_decay_per_bar >= 1.0:
            return
        for t in self.tickers:
            self._price_multiplier[t] *= self.impact_decay_per_bar

    async def _maybe_resume_trading(self, tick: int):
        if self._halted and self._shock_resume_tick is not None and tick >= self._shock_resume_tick:
            self._halted = False
            self._shock_resume_tick = None
            await self.bus.publish(Event(type=EventType.TRADING_RESUMED, source="Market"))

    def _benchmark_price(self) -> float:
        if not self._prices:
            return 100.0
        vals = [v for v in self._prices.values() if v > 0]
        return sum(vals) / len(vals) if vals else 100.0

    def _scaled_bar(self, closed_bar: dict) -> tuple[str, dict]:
        ticker = closed_bar["ticker"]
        mult = self._price_multiplier.get(ticker, 1.0)
        scaled = {
            "open": round(closed_bar["open"] * mult, 4),
            "high": round(closed_bar["high"] * mult, 4),
            "low": round(closed_bar["low"] * mult, 4),
            "close": round(closed_bar["close"] * mult, 4),
            "volume": closed_bar["volume"],
            "ticker": ticker,
        }
        return ticker, scaled

    def _apply_closed_bar_state(self, closed_bar: dict, bar_index: int) -> tuple[str, dict]:
        """Update prices/session from a closed bar (no bus publish)."""
        self._current_tick = bar_index
        self._apply_impact_decay()
        ticker, scaled_bar = self._scaled_bar(closed_bar)
        self._prices[ticker] = scaled_bar["close"]
        self._update_prices_from_books()
        dt_str = closed_bar["datetime"]
        self._last_datetime = dt_str
        try:
            self._last_session = str(datetime.fromisoformat(dt_str).date())
        except ValueError:
            self._last_session = ""
        return ticker, scaled_bar

    def _trading_enabled_for(self, ticker: str, bar_index: int, total_bars_hint: int) -> bool:
        closed_hist = len(self._bars._history.get(ticker, []))
        if closed_hist < self.min_kline_bars:
            return False
        if bar_index < self.warmup_bars:
            return False
        if self.trade_cutoff_bars > 0 and total_bars_hint > 0:
            if bar_index >= total_bars_hint - self.trade_cutoff_bars:
                return False
        return True

    async def _publish_bar_tick(self, closed_bar: dict, bar_index: int, total_bars_hint: int):
        await self._maybe_resume_trading(bar_index)
        ticker, scaled_bar = self._apply_closed_bar_state(closed_bar, bar_index)

        trading_enabled = self._trading_enabled_for(ticker, bar_index, total_bars_hint)

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

        book_snap = self._orderbook_snapshot()
        mm_payload = self._run_market_maker(ticker, scaled_bar, book_snap)

        bars_scaled = self._bars_payload()
        dt_str = closed_bar["datetime"]
        tick_payload = {
            "tick": bar_index,
            "bar_index": bar_index,
            "date": self._last_session,
            "datetime": dt_str,
            "session_date": self._last_session,
            "bar_interval": self.bar_interval,
            "intraday": True,
            "orderlog_stream": True,
            "hft_mode": False,
            "micro_phase": "close",
            "bar_close": True,
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
            "warmup": not trading_enabled,
            "warmup_bars": self.warmup_bars,
            "min_kline_bars": self.min_kline_bars,
            "kline_bars": len(self._bars._history.get(ticker, [])),
            "total_ticks": total_bars_hint,
            "trade_cutoff_ticks": self.trade_cutoff_bars,
            "agent_portfolios": agent_portfolios,
            "tickers": list(self.tickers),
            "bars": bars_scaled,
            "closed_bar": scaled_bar,
            "closed_ticker": ticker,
            "price_multipliers": dict(self._price_multiplier),
            "orderbook": book_snap,
            "market_maker": mm_payload,
            "kline_history": self._kline_history(),
            "event_count": self._event_count,
        }

        await self.bus.publish(Event(type=EventType.TICK, payload=tick_payload, source="Market"))
        await self.bus.drain()
        await self.bus.publish(
            Event(type=EventType.BAR_PROPOSALS_READY, payload=dict(tick_payload), source="Market")
        )
        await self.bus.drain()

    async def _stream_events_to_bars(
        self,
        bar_cap: int | None,
        *,
        on_bar,
    ) -> int:
        """Read OrderLog events chronologically; invoke on_bar for each closed bar."""
        tickers_set = set(self.tickers)
        total_zip_bytes = sum(p.stat().st_size for p in self.zip_paths)
        log_step(
            "Чтение OrderLog",
            f"{len(self.zip_paths)} сессий, {format_bytes(total_zip_bytes)}, тикеры: {', '.join(self.tickers)}",
        )
        log("   Бары публикуются по мере чтения — график появится после первого закрытого бара")

        events = merge_session_events(self.zip_paths, tickers_set)
        bar_index = 0
        last_session = None
        progress_every = 500_000
        last_log_at = 0
        t_read = elapsed()
        self._stream_t0 = t_read
        stop_stream = False

        async def _handle_bar(cb: dict, idx: int, total_hint: int) -> bool:
            if self.warmup_fast and idx < self.warmup_bars:
                self._apply_closed_bar_state(cb, idx)
                self._warmup_skipped += 1
                if idx == 0:
                    log(
                        f"   Warmup fast: первые {self.warmup_bars} баров без публикации "
                        f"(нужно ≥{self.min_kline_bars} баров/тикер для торговли)"
                    )
                if idx == self.warmup_bars - 1:
                    log_done(
                        "Warmup",
                        f"{self.warmup_bars} баров за {elapsed() - self._stream_t0:.1f}s — "
                        "дашборд и агенты подключаются",
                    )
                return True
            return await on_bar(cb, idx, total_hint)

        for ev in events:
            if stop_stream:
                break
            self._event_count += 1
            if self.max_events and self._event_count > self.max_events:
                break

            if ev.session_date != last_session:
                last_session = ev.session_date
                log(f"   → торговая сессия {ev.session_date}")

            if self._event_count - last_log_at >= progress_every:
                rate = self._event_count / max(elapsed() - t_read, 0.1)
                log(
                    f"   … событий {format_count(self._event_count)}, "
                    f"баров {bar_index}, {format_count(int(rate))} evt/s"
                )
                last_log_at = self._event_count
                await asyncio.sleep(0)

            new_closed = self._apply_event(ev)
            for cb in new_closed:
                if bar_cap is not None and bar_index >= bar_cap:
                    break
                total_hint = bar_cap if bar_cap else max(bar_index + 1, 1)
                cont = await _handle_bar(cb, bar_index, total_hint)
                if cont is False:
                    stop_stream = True
                    break
                if bar_index == 0:
                    log_done("Первый бар опубликован", "откройте dashboard — свечи должны появиться")
                bar_index += 1

            if bar_cap is not None and bar_index >= bar_cap:
                break

        log_done(
            "OrderLog обработан",
            f"{format_count(self._event_count)} событий → {bar_index} баров за {elapsed() - t_read:.1f}s",
        )
        return bar_index

    async def run(self, n_ticks: int | None = None):
        self._ensure_subscribed()
        log_step("Движок рынка", "SIM_STARTED")
        await self.bus.publish(Event(type=EventType.SIM_STARTED, source="Market"))

        bar_cap = n_ticks if n_ticks else self.max_bars

        async def on_bar(cb, idx, total_hint):
            self._current_bar = idx
            await self._publish_bar_tick(cb, idx, total_hint)
            if self.tick_interval > 0:
                await asyncio.sleep(self.tick_interval)
            return True

        self._total_bars = await self._stream_events_to_bars(bar_cap, on_bar=on_bar)
        await self._finish()

    async def run_controlled(self, controller, n_ticks: int | None = None):
        from core.simulation_controller import SimStatus

        self._ensure_subscribed()
        bar_cap = n_ticks if n_ticks else self.max_bars
        controller.configure(total_ticks=bar_cap or 100_000)

        log_step("Движок рынка (controlled)", "SIM_STARTED")
        await self.bus.publish(Event(type=EventType.SIM_STARTED, source="Market"))

        stopped = False

        async def on_bar(cb, idx, total_hint):
            nonlocal stopped
            if not await controller.wait_for_next_tick():
                stopped = True
                return False
            self._current_bar = idx
            await self._publish_bar_tick(cb, idx, total_hint)
            controller.tick_completed(idx)
            if idx == 0:
                log_done("Первый бар (controlled)", "дашборд обновляется")
            if controller.status == SimStatus.RUNNING and controller.auto_interval_ms > 0:
                await asyncio.sleep(controller.auto_interval_ms / 1000.0)
            return True

        bar_index = await self._stream_events_to_bars(
            bar_cap,
            on_bar=on_bar,
        )
        self._total_bars = bar_index
        controller.configure(total_ticks=bar_index)

        if not controller._stop_requested and not stopped:
            await self._finish()
            controller.mark_finished()
        else:
            self.bus.stop()

    async def _finish(self):
        await self.bus.drain()
        stats = self._final_stats()
        stats["total_bars"] = self._total_bars
        stats["event_count"] = self._event_count
        await self.bus.publish(Event(type=EventType.SIM_ENDED, payload=stats, source="Market"))
        await self.bus.drain()
        self.bus.stop()

    def reset_market_state(self):
        self._halted = False
        self._volatility_mult = 1.0
        self._current_tick = 0
        self._shock_resume_tick = None
        self._price_multiplier = {t: 1.0 for t in self.tickers}
        self._step_trade_counts = {}

    async def on_signal(self, event: Event):
        if self._halted:
            await self.bus.publish(
                Event(type=EventType.ORDER_REJECTED, payload={"reason": "trading halted"}, source="Market")
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
        if len(self._bars._history.get(ticker, [])) < self.min_kline_bars:
            await self.bus.publish(
                Event(
                    type=EventType.ORDER_REJECTED,
                    payload={
                        "reason": f"warmup: need {self.min_kline_bars} bars for {ticker}",
                        "ticker": ticker,
                    },
                    source="Market",
                )
            )
            return

        agent_id = event.payload.get("agent_id", "")
        action = event.payload.get("action", "hold")
        qty = int(event.payload.get("quantity", 0))
        trade_key = f"{agent_id}:{ticker}" if agent_id else ticker

        if self._step_trade_counts.get(trade_key, 0) >= self.max_trades_per_step:
            await self.bus.publish(
                Event(
                    type=EventType.ORDER_REJECTED,
                    payload={"reason": f"max {self.max_trades_per_step} trades per step", "ticker": ticker},
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
                    "agent_id": agent_id,
                    "strategy": event.payload.get("strategy", ""),
                },
                source="Market",
            )
            rejection = await self.portfolio.on_order_request(check)
            if rejection is not None:
                await self.bus.publish(rejection)
                return

        filled = {
            "ticker": ticker,
            "agent_id": agent_id,
            "strategy": event.payload.get("strategy", ""),
            "action": action,
            "quantity": qty,
            "price": price,
            "tick": self._current_tick,
            "date": self._last_session,
            "datetime": self._last_datetime,
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

        await self.bus.publish(
            Event(
                type=EventType.PRICES_UPDATED,
                payload={
                    "tick": self._current_tick,
                    "prices": dict(self._prices),
                    "price_multipliers": dict(self._price_multiplier),
                },
                source="Market",
            )
        )

    def _final_stats(self) -> dict:
        if self.portfolio is not None:
            return self.portfolio.get_stats(self._prices)
        return {"final_prices": self._prices, "total_pnl": 0.0}
