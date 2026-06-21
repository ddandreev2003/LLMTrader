"""Load and align MOEX ISS historical candles for Russian equities."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache"
LOCAL_DIR = Path(__file__).resolve().parents[2] / "data" / "local"
ISS_CANDLES_URL = (
    "https://iss.moex.com/iss/engines/stock/markets/shares/boards/{board}/"
    "securities/{ticker}/candles.json"
)


def _cache_path(ticker: str, date_from: str, date_till: str, interval: int) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe_from = date_from.replace("-", "")
    safe_till = date_till.replace("-", "")
    return CACHE_DIR / f"{ticker}_{safe_from}_{safe_till}_i{interval}.csv"


def _aligned_local_path(tickers: list[str], date_from: str, date_till: str, interval: int) -> Path:
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    tickers_key = "-".join(sorted(tickers))
    safe_from = date_from.replace("-", "")
    safe_till = date_till.replace("-", "")
    return LOCAL_DIR / f"aligned_{tickers_key}_{safe_from}_{safe_till}_i{interval}.csv"


def _manifest_path(tickers: list[str], date_from: str, date_till: str, interval: int) -> Path:
    return _aligned_local_path(tickers, date_from, date_till, interval).with_suffix(".json")


def _data_dates(data_cfg: dict) -> tuple[str, str, str, int]:
    """Return (effective_from, trade_from, till, interval)."""
    trade_from = data_cfg.get("from", "2024-01-01")
    till = data_cfg.get("till", "2024-12-31")
    interval = int(data_cfg.get("interval", 24))
    effective_from = data_cfg.get("warmup_from", trade_from)
    return effective_from, trade_from, till, interval


def compute_warmup_ticks(price_data: pd.DataFrame, trade_from: str) -> int:
    """Number of leading bars before trade_from (indicator warmup, no trading)."""
    trade_start = pd.Timestamp(trade_from)
    mask = price_data.index >= trade_start
    if not mask.any():
        return 0
    return int(mask.argmax())


def fetch_ticker_candles(
    ticker: str,
    date_from: str,
    date_till: str,
    board: str = "TQBR",
    interval: int = 24,
    use_cache: bool = True,
    allow_network: bool = True,
) -> pd.DataFrame:
    """Fetch daily candles for one ticker. Uses per-ticker CSV cache when available."""
    cache_file = _cache_path(ticker, date_from, date_till, interval)
    if use_cache and cache_file.exists():
        return pd.read_csv(cache_file, parse_dates=["begin"])

    if not allow_network:
        raise FileNotFoundError(
            f"Локальный кэш для {ticker} не найден: {cache_file}\n"
            "Запустите: python scripts/download_moex.py"
        )

    params = {"from": date_from, "till": date_till, "interval": interval}
    url = ISS_CANDLES_URL.format(board=board, ticker=ticker)
    resp = requests.get(url, params=params, timeout=60)
    resp.raise_for_status()
    payload = resp.json()

    columns = payload["candles"]["columns"]
    rows = payload["candles"]["data"]
    if not rows:
        raise ValueError(f"No MOEX data for {ticker} ({date_from}..{date_till})")

    df = pd.DataFrame(rows, columns=columns)
    df["begin"] = pd.to_datetime(df["begin"])
    df = df.sort_values("begin").reset_index(drop=True)

    if use_cache:
        df.to_csv(cache_file, index=False)

    return df


def load_aligned_prices(
    tickers: list[str],
    date_from: str,
    date_till: str,
    boards: dict[str, str] | None = None,
    interval: int = 24,
    use_cache: bool = True,
    allow_network: bool = True,
) -> pd.DataFrame:
    """Load aligned close prices; builds from per-ticker files if needed."""
    boards = boards or {}
    series: dict[str, pd.Series] = {}

    for ticker in tickers:
        board = boards.get(ticker, "TQBR")
        df = fetch_ticker_candles(
            ticker,
            date_from,
            date_till,
            board=board,
            interval=interval,
            use_cache=use_cache,
            allow_network=allow_network,
        )
        series[ticker] = df.set_index("begin")["close"].astype(float)

    prices = pd.DataFrame(series)
    prices = prices.dropna(how="any")
    return prices.sort_index()


def save_aligned_local(prices: pd.DataFrame, tickers: list[str], date_from: str, date_till: str, interval: int) -> Path:
    """Persist aligned portfolio prices to data/local/."""
    path = _aligned_local_path(tickers, date_from, date_till, interval)
    path.parent.mkdir(parents=True, exist_ok=True)
    prices.to_csv(path, index_label="begin")

    manifest = {
        "tickers": sorted(tickers),
        "from": date_from,
        "till": date_till,
        "interval": interval,
        "rows": len(prices),
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "file": path.name,
    }
    _manifest_path(tickers, date_from, date_till, interval).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def load_aligned_local(tickers: list[str], date_from: str, date_till: str, interval: int) -> pd.DataFrame:
    """Load pre-downloaded aligned prices from data/local/ only."""
    path = _aligned_local_path(tickers, date_from, date_till, interval)
    if not path.exists():
        raise FileNotFoundError(
            f"Локальные данные не найдены: {path}\n"
            "Скачайте один раз: python scripts/download_moex.py"
        )
    df = pd.read_csv(path, parse_dates=["begin"], index_col="begin")
    missing = set(tickers) - set(df.columns)
    if missing:
        raise ValueError(f"В локальном файле нет тикеров: {missing}")
    return df[sorted(tickers)].sort_index()


def download_from_portfolio_config(config: dict, force: bool = False) -> pd.DataFrame:
    """
    Download MOEX data from network and save to data/cache/ + data/local/.
    Call this once; simulations should use offline mode afterward.
    """
    assets = config.get("assets", [])
    tickers = [a["ticker"] for a in assets]
    boards = {a["ticker"]: a.get("board", "TQBR") for a in assets}
    data_cfg = config.get("data", {})
    effective_from, trade_from, date_till, interval = _data_dates(data_cfg)

    local_path = _aligned_local_path(tickers, effective_from, date_till, interval)
    if local_path.exists() and not force:
        print(f"[MOEX] Локальный файл уже есть: {local_path}")
        return load_aligned_local(tickers, effective_from, date_till, interval)

    print(
        f"[MOEX] Загрузка с биржи: {', '.join(tickers)} "
        f"({effective_from} .. {date_till}, торговля с {trade_from})"
    )
    prices = load_aligned_prices(
        tickers=tickers,
        date_from=effective_from,
        date_till=date_till,
        boards=boards,
        interval=interval,
        use_cache=True,
        allow_network=True,
    )
    saved = save_aligned_local(prices, tickers, effective_from, date_till, interval)
    print(f"[MOEX] Сохранено: {saved} ({len(prices)} баров)")
    return prices


def load_from_portfolio_config(config: dict, offline: bool | None = None) -> pd.DataFrame:
    """
    Load aligned prices for simulation.

    offline=True (default): только data/local/, без сети.
    offline=False: разрешить докачку с MOEX ISS.
    """
    if offline is None:
        offline = _env_bool("MOEX_OFFLINE", default=True)

    assets = config.get("assets", [])
    tickers = [a["ticker"] for a in assets]
    boards = {a["ticker"]: a.get("board", "TQBR") for a in assets}
    data_cfg = config.get("data", {})
    effective_from, trade_from, date_till, interval = _data_dates(data_cfg)

    if offline:
        try:
            return load_aligned_local(tickers, effective_from, date_till, interval)
        except FileNotFoundError:
            if effective_from != trade_from:
                print(
                    f"[MOEX] Файл с warmup ({effective_from}) не найден, "
                    f"используем {trade_from}. Запустите download_moex.py для полного warmup."
                )
                return load_aligned_local(tickers, trade_from, date_till, interval)
            raise

    local_path = _aligned_local_path(tickers, effective_from, date_till, interval)
    if local_path.exists():
        return load_aligned_local(tickers, effective_from, date_till, interval)

    return load_aligned_prices(
        tickers=tickers,
        date_from=effective_from,
        date_till=date_till,
        boards=boards,
        interval=interval,
        use_cache=True,
        allow_network=True,
    )


def _env_bool(name: str, default: bool) -> bool:
    import os

    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")
