import pandas as pd
import pytest

from core.data.moex_loader import (
    compute_warmup_ticks,
    load_aligned_local,
    save_aligned_local,
    _aligned_local_path,
)


def test_save_and_load_aligned_local(tmp_path, monkeypatch):
    monkeypatch.setattr("core.data.moex_loader.LOCAL_DIR", tmp_path)

    tickers = ["SBER", "GAZP"]
    dates = pd.date_range("2024-01-01", periods=3, freq="D")
    prices = pd.DataFrame({"SBER": [280.0, 281.0, 282.0], "GAZP": [160.0, 161.0, 162.0]}, index=dates)

    save_aligned_local(prices, tickers, "2024-01-01", "2024-01-03", 24)
    loaded = load_aligned_local(tickers, "2024-01-01", "2024-01-03", 24)

    assert list(loaded.columns) == ["GAZP", "SBER"]
    assert len(loaded) == 3
    assert loaded.iloc[0]["SBER"] == 280.0


def test_compute_warmup_ticks_from_saved_data(tmp_path, monkeypatch):
    monkeypatch.setattr("core.data.moex_loader.LOCAL_DIR", tmp_path)
    tickers = ["SBER"]
    dates = pd.date_range("2023-01-01", periods=400, freq="D")
    prices = pd.DataFrame({"SBER": [280.0] * 400}, index=dates)
    save_aligned_local(prices, tickers, "2023-01-01", "2024-12-31", 24)
    loaded = load_aligned_local(tickers, "2023-01-01", "2024-12-31", 24)
    warmup = compute_warmup_ticks(loaded, "2024-01-01")
    assert warmup > 0


def test_load_aligned_local_missing_raises(tmp_path, monkeypatch):
    monkeypatch.setattr("core.data.moex_loader.LOCAL_DIR", tmp_path)
    with pytest.raises(FileNotFoundError, match="download_moex"):
        load_aligned_local(["SBER"], "2024-01-01", "2024-12-31", 24)
