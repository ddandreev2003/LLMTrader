"""OrderLog zip → intraday OHLCV bars for selected tickers."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, time
from pathlib import Path
from typing import Iterable
import zipfile

import pandas as pd

DEFAULT_TICKERS = ("T", "SBER")
DATE_RE = re.compile(r"OrderLog(\d{8})\.zip$")


def session_date_from_zip(path: Path) -> date:
    match = DATE_RE.search(path.name)
    if not match:
        raise ValueError(f"Cannot parse session date from {path.name}")
    return datetime.strptime(match.group(1), "%Y%m%d").date()


def parse_orderlog_time(time_str: str, session_date: date) -> datetime:
    """TIME field: HHMMSS + fractional part (MOEX OrderLog encoding)."""
    t = int(time_str)
    hours = t // 10_000_000_000
    remainder = t % 10_000_000_000
    minutes = remainder // 100_000_000
    remainder = remainder % 100_000_000
    seconds = remainder // 1_000_000
    micros = remainder % 1_000_000
    base = datetime.combine(session_date, time(hours, minutes, seconds, micros))
    return base


def floor_to_bar(ts: datetime, bar_minutes: int) -> datetime:
    minute = (ts.minute // bar_minutes) * bar_minutes
    return ts.replace(minute=minute, second=0, microsecond=0)


def bar_minutes_from_interval(interval: str) -> int:
    interval = interval.strip().lower()
    if interval.endswith("m"):
        return int(interval[:-1])
    if interval.endswith("h"):
        return int(interval[:-1]) * 60
    raise ValueError(f"Unsupported bar interval: {interval}")


class _BarBuilder:
    __slots__ = ("open", "high", "low", "close", "volume", "trade_count")

    def __init__(self, price: float, volume: float):
        self.open = price
        self.high = price
        self.low = price
        self.close = price
        self.volume = volume
        self.trade_count = 1

    def update(self, price: float, volume: float) -> None:
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += volume
        self.trade_count += 1

    def as_dict(self) -> dict:
        return {
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "trade_count": self.trade_count,
        }


def aggregate_zip_to_bars(
    zip_path: str | Path,
    tickers: Iterable[str] | None = None,
    bar_minutes: int = 5,
) -> pd.DataFrame:
    """Stream trades from one OrderLog zip into OHLCV bars per ticker."""
    zip_path = Path(zip_path)
    tickers_set = set(tickers or DEFAULT_TICKERS)
    session_date = session_date_from_zip(zip_path)
    buckets: dict[tuple[str, datetime], _BarBuilder] = {}

    with zipfile.ZipFile(zip_path) as zf:
        txt_name = zf.namelist()[0]
        with zf.open(txt_name) as f:
            header = f.readline().decode("utf-8").strip().split(",")
            idx = {name: i for i, name in enumerate(header)}
            action_i = idx["ACTION"]
            sec_i = idx["SECCODE"]
            time_i = idx["TIME"]
            vol_i = idx["VOLUME"]
            price_i = idx["TRADEPRICE"]

            for raw in f:
                parts = raw.decode("utf-8", errors="replace").strip().split(",")
                if len(parts) <= action_i or parts[action_i] != "2":
                    continue
                ticker = parts[sec_i]
                if ticker not in tickers_set:
                    continue

                ts = parse_orderlog_time(parts[time_i], session_date)
                bar_ts = floor_to_bar(ts, bar_minutes)
                price = float(parts[price_i])
                volume = float(parts[vol_i])
                key = (ticker, bar_ts)
                if key in buckets:
                    buckets[key].update(price, volume)
                else:
                    buckets[key] = _BarBuilder(price, volume)

    rows = []
    for (ticker, bar_ts), builder in buckets.items():
        row = builder.as_dict()
        row["datetime"] = bar_ts
        row["ticker"] = ticker
        row["session_date"] = session_date
        rows.append(row)

    if not rows:
        return pd.DataFrame(
            columns=["datetime", "ticker", "session_date", "open", "high", "low", "close", "volume", "trade_count"]
        )
    return pd.DataFrame(rows)


def bars_to_wide(df: pd.DataFrame, tickers: Iterable[str]) -> pd.DataFrame:
    """Pivot long bar frame to wide OHLCV columns per ticker."""
    tickers = list(tickers)
    if df.empty:
        cols = []
        for t in tickers:
            cols.extend([f"{t}_open", f"{t}_high", f"{t}_low", f"{t}_close", f"{t}_volume"])
        return pd.DataFrame(columns=cols)

    parts = []
    for field in ("open", "high", "low", "close", "volume"):
        piv = df.pivot(index="datetime", columns="ticker", values=field)
        piv.columns = [f"{c}_{field}" for c in piv.columns]
        parts.append(piv)
    wide = parts[0]
    for part in parts[1:]:
        wide = wide.join(part, how="outer")
    return wide.sort_index()


def _load_wide_parquet(portfolio_cfg: dict, root: Path | None = None) -> pd.DataFrame:
    data_cfg = portfolio_cfg.get("data", {})
    bars_path = data_cfg.get("bars_path", "data/local/orderlog_bars_tls_5m.parquet")
    root = root or Path(__file__).resolve().parents[2]
    path = root / bars_path
    if not path.exists():
        raise FileNotFoundError(
            f"Bars file not found: {path}. Run: python scripts/preprocess_orderlog_bars.py"
        )
    df = pd.read_parquet(path)
    if not isinstance(df.index, pd.DatetimeIndex):
        if "datetime" in df.columns:
            df = df.set_index("datetime")
        df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    date_from = data_cfg.get("from")
    date_till = data_cfg.get("till")
    if date_from:
        df = df[df.index >= pd.Timestamp(date_from)]
    if date_till:
        df = df[df.index <= pd.Timestamp(date_till) + pd.Timedelta(days=1)]
    return df


def load_orderlog_ohlc(
    portfolio_cfg: dict, root: Path | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (close_df for engine, full wide OHLCV frame)."""
    df = _load_wide_parquet(portfolio_cfg, root=root)
    tickers = [a["ticker"] for a in portfolio_cfg.get("assets", [])]
    if not tickers:
        tickers = sorted({c.rsplit("_", 1)[0] for c in df.columns if c.endswith("_close")})

    close_cols = [f"{t}_close" for t in tickers if f"{t}_close" in df.columns]
    close_df = df[close_cols].copy()
    close_df.columns = [c.replace("_close", "") for c in close_cols]
    close_df = close_df.dropna(how="all")
    if close_df.empty:
        raise ValueError("No bar data in configured date range")
    return close_df, df.loc[close_df.index]


def build_aligned_bars(
    zip_paths: list[Path],
    tickers: Iterable[str] | None = None,
    bar_interval: str = "5m",
) -> pd.DataFrame:
    tickers = list(tickers or DEFAULT_TICKERS)
    bar_minutes = bar_minutes_from_interval(bar_interval)
    frames = []
    for path in sorted(zip_paths):
        long_df = aggregate_zip_to_bars(path, tickers=tickers, bar_minutes=bar_minutes)
        if not long_df.empty:
            frames.append(long_df)
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    return bars_to_wide(combined, tickers)


def save_bars_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)


def load_orderlog_bars(portfolio_cfg: dict, root: Path | None = None) -> pd.DataFrame:
    """Load preprocessed bars; return DataFrame indexed by datetime with ticker close columns."""
    close_df, _ = load_orderlog_ohlc(portfolio_cfg, root=root)
    return close_df


def compute_warmup_bars(price_data: pd.DataFrame, warmup_bars: int) -> int:
    return min(int(warmup_bars), max(0, len(price_data) - 1))


def tick_count_for_trading_days(price_data: pd.DataFrame, n_days: int) -> int:
    """Return bar count covering the first n unique session dates."""
    if price_data.empty or n_days <= 0:
        return 0
    dates = sorted({ts.date() for ts in price_data.index})
    if len(dates) <= n_days:
        return len(price_data)
    cutoff = dates[n_days - 1]
    return int((price_data.index.date <= cutoff).sum())


def resolve_sim_tick_count(
    price_data: pd.DataFrame,
    portfolio_cfg: dict,
    env_sim_n_ticks: str | int | None = None,
    config_sim_n_ticks: int | None = None,
) -> int:
    """Priority: env SIM_N_TICKS → web sim_n_ticks → sim.use_full_range → min_trading_days → all bars."""
    if env_sim_n_ticks not in (None, ""):
        return min(int(env_sim_n_ticks), len(price_data))
    if config_sim_n_ticks is not None:
        return min(int(config_sim_n_ticks), len(price_data))

    sim_cfg = portfolio_cfg.get("sim", {})
    if sim_cfg.get("use_full_range"):
        return len(price_data)

    min_days = sim_cfg.get("min_trading_days")
    if min_days is not None:
        n = tick_count_for_trading_days(price_data, int(min_days))
        if n > 0:
            return n

    return len(price_data)
