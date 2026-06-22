"""Aggregate MOEX OrderLog zip archives (parallel worker-friendly)."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import zipfile


def aggregate_trades(zip_path: str | Path) -> tuple[str, dict[str, float], dict[str, float], int]:
    """Return file name, turnover (RUB), share volume, and trade count per ticker."""
    zip_path = Path(zip_path)
    turnover: dict[str, float] = defaultdict(float)
    volume: dict[str, float] = defaultdict(float)
    trades = 0

    with zipfile.ZipFile(zip_path) as zf:
        txt_name = zf.namelist()[0]
        with zf.open(txt_name) as f:
            header = f.readline().decode("utf-8").strip().split(",")
            idx = {name: i for i, name in enumerate(header)}
            action_i = idx["ACTION"]
            sec_i = idx["SECCODE"]
            vol_i = idx["VOLUME"]
            price_i = idx["TRADEPRICE"]

            for raw in f:
                parts = raw.decode("utf-8", errors="replace").strip().split(",")
                if len(parts) <= action_i or parts[action_i] != "2":
                    continue

                ticker = parts[sec_i]
                qty = float(parts[vol_i])
                price = float(parts[price_i])

                volume[ticker] += qty
                turnover[ticker] += qty * price
                trades += 1

    return zip_path.name, dict(turnover), dict(volume), trades
