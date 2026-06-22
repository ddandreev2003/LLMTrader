"""Tests for OrderLog → intraday bars."""

from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from core.data.orderlog_bars import (
    aggregate_zip_to_bars,
    bar_minutes_from_interval,
    bars_to_wide,
    floor_to_bar,
    parse_orderlog_time,
    session_date_from_zip,
)


def _make_zip(tmp_path: Path, session: str, rows: list[str]) -> Path:
    header = "NO,SECCODE,BUYSELL,TIME,ORDERNO,ACTION,PRICE,VOLUME,TRADENO,TRADEPRICE"
    content = header + "\n" + "\n".join(rows) + "\n"
    zpath = tmp_path / f"OrderLog{session}.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr(f"OrderLog{session}.txt", content)
    return zpath


def test_session_date_from_zip():
    assert session_date_from_zip(Path("OrderLog20250103.zip")) == date(2025, 1, 3)


def test_parse_orderlog_time_and_floor():
    ts = parse_orderlog_time("71050000000", date(2025, 1, 3))  # 07:10:50
    assert ts.hour == 7 and ts.minute == 10 and ts.second == 50
    assert floor_to_bar(ts, 5) == ts.replace(minute=10, second=0, microsecond=0)


def test_aggregate_zip_filters_tickers_and_builds_ohlcv(tmp_path):
    # Two trades for SBER in same 5m bar, one for GAZP (filtered out)
    rows = [
        "1,SBER,B,70100000000,1,2,250.0,10,1,250.0",
        "2,SBER,S,70103000000,2,2,252.0,5,2,252.0",
        "3,GAZP,B,70100000000,3,2,140.0,1,3,140.0",
    ]
    zpath = _make_zip(tmp_path, "20250103", rows)
    df = aggregate_zip_to_bars(zpath, tickers={"SBER"}, bar_minutes=5)
    assert len(df) == 1
    assert df.iloc[0]["ticker"] == "SBER"
    assert df.iloc[0]["open"] == 250.0
    assert df.iloc[0]["close"] == 252.0
    assert df.iloc[0]["high"] == 252.0
    assert df.iloc[0]["volume"] == 15.0
    assert df.iloc[0]["trade_count"] == 2


def test_bars_to_wide():
    df = pd.DataFrame(
        [
            {
                "datetime": pd.Timestamp("2025-01-03 07:05"),
                "ticker": "T",
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
                "volume": 10,
            },
            {
                "datetime": pd.Timestamp("2025-01-03 07:05"),
                "ticker": "SBER",
                "open": 250.0,
                "high": 252.0,
                "low": 249.0,
                "close": 251.0,
                "volume": 5,
            },
        ]
    )
    wide = bars_to_wide(df, ["T", "SBER"])
    assert "T_open" in wide.columns
    assert "T_high" in wide.columns
    assert "SBER_close" in wide.columns
    assert wide.loc[pd.Timestamp("2025-01-03 07:05"), "T_close"] == 101.0


def test_bar_minutes_from_interval():
    assert bar_minutes_from_interval("5m") == 5
    assert bar_minutes_from_interval("1h") == 60
