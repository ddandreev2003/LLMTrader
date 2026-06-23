"""Stream MOEX OrderLog zip archives event-by-event."""

from __future__ import annotations

import heapq
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterator, Literal

from core.data.orderlog_bars import (
    bar_minutes_from_interval,
    floor_to_bar,
    parse_orderlog_time,
    session_date_from_zip,
)

Action = Literal[0, 1, 2]
Side = Literal["B", "S"]


@dataclass(frozen=True, slots=True)
class OrderLogEvent:
    seq: int
    ticker: str
    side: Side
    time: datetime
    order_no: int
    action: Action
    price: float
    volume: float
    trade_no: int
    trade_price: float
    session_date: date


def _parse_row(parts: list[str], idx: dict[str, int], session_date: date, seq: int) -> OrderLogEvent | None:
    if len(parts) <= idx["ACTION"]:
        return None
    action = int(parts[idx["ACTION"]])
    if action not in (0, 1, 2):
        return None
    ticker = parts[idx["SECCODE"]]
    side = parts[idx["BUYSELL"]]
    if side not in ("B", "S"):
        return None
    ts = parse_orderlog_time(parts[idx["TIME"]], session_date)
    order_no = int(parts[idx["ORDERNO"]])
    price = float(parts[idx["PRICE"]])
    volume = float(parts[idx["VOLUME"]])
    trade_no = int(parts[idx["TRADENO"]]) if parts[idx["TRADENO"]] else 0
    trade_price = float(parts[idx["TRADEPRICE"]]) if parts[idx["TRADEPRICE"]] else 0.0
    return OrderLogEvent(
        seq=seq,
        ticker=ticker,
        side=side,
        time=ts,
        order_no=order_no,
        action=action,
        price=price,
        volume=volume,
        trade_no=trade_no,
        trade_price=trade_price,
        session_date=session_date,
    )


def iter_zip_events(
    zip_path: Path,
    tickers: set[str] | None = None,
) -> Iterator[OrderLogEvent]:
    """Yield events from one OrderLog zip in file order (time-sorted within session)."""
    zip_path = Path(zip_path)
    session_date = session_date_from_zip(zip_path)
    tickers_set = tickers
    seq = 0

    with zipfile.ZipFile(zip_path) as zf:
        txt_name = zf.namelist()[0]
        with zf.open(txt_name) as f:
            header = f.readline().decode("utf-8").strip().split(",")
            idx = {name: i for i, name in enumerate(header)}
            for raw in f:
                parts = raw.decode("utf-8", errors="replace").strip().split(",")
                seq += 1
                ev = _parse_row(parts, idx, session_date, seq)
                if ev is None:
                    continue
                if tickers_set is not None and ev.ticker not in tickers_set:
                    continue
                yield ev


def list_session_zips(
    zip_dir: Path,
    date_from: date | None = None,
    date_till: date | None = None,
) -> list[Path]:
    paths = sorted(Path(zip_dir).glob("OrderLog*.zip"))
    out: list[Path] = []
    for p in paths:
        try:
            d = session_date_from_zip(p)
        except ValueError:
            continue
        if date_from and d < date_from:
            continue
        if date_till and d > date_till:
            continue
        out.append(p)
    return out


def merge_session_events(
    zip_paths: list[Path],
    tickers: set[str] | None = None,
) -> Iterator[OrderLogEvent]:
    """Merge multiple session zips by event timestamp (global chronological order)."""
    iters: list[Iterator[OrderLogEvent]] = [iter_zip_events(p, tickers) for p in zip_paths]
    heap: list[tuple[datetime, int, int, OrderLogEvent]] = []
    for i, it in enumerate(iters):
        try:
            ev = next(it)
            heapq.heappush(heap, (ev.time, ev.seq, i, ev))
        except StopIteration:
            pass

    while heap:
        _, _, i, ev = heapq.heappop(heap)
        yield ev
        try:
            nxt = next(iters[i])
            heapq.heappush(heap, (nxt.time, nxt.seq, i, nxt))
        except StopIteration:
            pass


class RollingBarBuilder:
    """Build OHLCV bars per ticker from trade events."""

    def __init__(self, tickers: list[str], bar_minutes: int = 1, history_size: int = 120):
        self.tickers = tickers
        self.bar_minutes = bar_minutes
        self.history_size = history_size
        self._current_bar_ts: dict[str, datetime | None] = {t: None for t in tickers}
        self._builders: dict[str, dict] = {}
        self._history: dict[str, list[dict]] = {t: [] for t in tickers}

    def _bar_dict(self, ticker: str, bar_ts: datetime, o: float, h: float, l: float, c: float, v: float) -> dict:
        return {
            "datetime": bar_ts.isoformat(),
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": v,
            "ticker": ticker,
        }

    def on_trade(self, ticker: str, ts: datetime, price: float, volume: float) -> list[dict]:
        """Update bar; return list of closed bars (0 or 1 per ticker)."""
        closed: list[dict] = []
        bar_ts = floor_to_bar(ts, self.bar_minutes)
        cur_ts = self._current_bar_ts.get(ticker)

        if cur_ts is None:
            self._current_bar_ts[ticker] = bar_ts
            self._builders[ticker] = {"open": price, "high": price, "low": price, "close": price, "volume": volume}
            return closed

        if bar_ts != cur_ts:
            b = self._builders[ticker]
            closed_bar = self._bar_dict(ticker, cur_ts, b["open"], b["high"], b["low"], b["close"], b["volume"])
            self._history[ticker].append(closed_bar)
            if len(self._history[ticker]) > self.history_size:
                self._history[ticker].pop(0)
            closed.append(closed_bar)
            self._current_bar_ts[ticker] = bar_ts
            self._builders[ticker] = {"open": price, "high": price, "low": price, "close": price, "volume": volume}
        else:
            b = self._builders[ticker]
            b["high"] = max(b["high"], price)
            b["low"] = min(b["low"], price)
            b["close"] = price
            b["volume"] += volume

        return closed

    def current_bar(self, ticker: str) -> dict | None:
        ts = self._current_bar_ts.get(ticker)
        b = self._builders.get(ticker)
        if ts is None or not b:
            return None
        return self._bar_dict(ticker, ts, b["open"], b["high"], b["low"], b["close"], b["volume"])

    def history(self, ticker: str, n: int = 30) -> list[dict]:
        hist = list(self._history.get(ticker, []))
        cur = self.current_bar(ticker)
        if cur:
            hist = hist + [cur]
        return hist[-n:]


def parse_date_str(s: str) -> date:
    return datetime.strptime(s.strip()[:10], "%Y-%m-%d").date()


def bar_interval_label(bar_minutes: int) -> str:
    if bar_minutes < 60:
        return f"{bar_minutes}m"
    return f"{bar_minutes // 60}h"


def resolve_stream_config(portfolio_cfg: dict, root: Path) -> dict:
    data = portfolio_cfg.get("data", {})
    zip_dir = Path(data.get("zip_dir", "datasets/moex/orderlog"))
    if not zip_dir.is_absolute():
        zip_dir = root / zip_dir
    bar_interval = data.get("bar_interval", "1m")
    bar_minutes = bar_minutes_from_interval(bar_interval)
    assets = portfolio_cfg.get("assets", [])
    tickers = [a["ticker"] if isinstance(a, dict) else str(a) for a in assets]
    date_from = parse_date_str(data["from"]) if data.get("from") else None
    date_till = parse_date_str(data["till"]) if data.get("till") else None
    zip_paths = list_session_zips(zip_dir, date_from, date_till)
    return {
        "zip_paths": zip_paths,
        "tickers": tickers,
        "bar_minutes": bar_minutes,
        "bar_interval": bar_interval,
        "decision_on": data.get("decision_on", "bar_close"),
        "zip_dir": zip_dir,
    }
