#!/usr/bin/env python3
"""Preprocess MOEX OrderLog zips into aligned intraday bar parquet."""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.data.orderlog_bars import (  # noqa: E402
    aggregate_zip_to_bars,
    bar_minutes_from_interval,
    bars_to_wide,
    save_bars_parquet,
    session_date_from_zip,
)


def _process_one(args_tuple):
    zip_path_str, tickers, bar_minutes = args_tuple
    return aggregate_zip_to_bars(zip_path_str, tickers=tickers, bar_minutes=bar_minutes)


def _load_meta(out: Path) -> dict:
    sidecar = out.with_suffix(".json")
    if sidecar.exists():
        return json.loads(sidecar.read_text(encoding="utf-8"))
    return {}


def _save_meta(out: Path, meta: dict) -> None:
    out.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="OrderLog → intraday bars parquet")
    parser.add_argument("--tickers", nargs="+", default=["T", "SBER"])
    parser.add_argument("--interval", default="5m", help="Bar size, e.g. 5m")
    parser.add_argument("--zip-dir", type=Path, default=ROOT / "datasets" / "moex" / "orderlog")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data" / "local" / "orderlog_bars_tls_5m.parquet",
    )
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--force", action="store_true", help="Reprocess all zips")
    args = parser.parse_args()

    zip_paths = sorted(args.zip_dir.glob("OrderLog*.zip"))
    if not zip_paths:
        print(f"No zips in {args.zip_dir}")
        return 1

    bar_minutes = bar_minutes_from_interval(args.interval)
    meta = _load_meta(args.out)
    processed_dates = set(meta.get("processed_dates", []))
    if args.force:
        processed_dates = set()

    pending = [p for p in zip_paths if session_date_from_zip(p).isoformat() not in processed_dates]
    print(f"Total zips: {len(zip_paths)}, to process: {len(pending)}, skip: {len(zip_paths) - len(pending)}")

    if not pending:
        print(f"Up to date: {args.out}")
        return 0

    workers = args.workers or min(8, len(pending))
    long_frames: list[pd.DataFrame] = []
    tasks = [(str(p), tuple(args.tickers), bar_minutes) for p in pending]

    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_process_one, t): t[0] for t in tasks}
        done = 0
        for future in as_completed(futures):
            zip_name = Path(futures[future]).name
            df = future.result()
            done += 1
            print(f"[{done}/{len(pending)}] {zip_name}: {len(df)} bar rows")
            if not df.empty:
                long_frames.append(df)
                processed_dates.add(session_date_from_zip(Path(futures[future])).isoformat())

    if not long_frames:
        print("No bar data produced")
        return 1

    new_wide = bars_to_wide(pd.concat(long_frames, ignore_index=True), args.tickers)
    if args.out.exists() and not args.force:
        existing = pd.read_parquet(args.out)
        if not isinstance(existing.index, pd.DatetimeIndex):
            existing.index = pd.to_datetime(existing.index)
        wide = pd.concat([existing, new_wide]).sort_index()
        wide = wide[~wide.index.duplicated(keep="last")]
    else:
        wide = new_wide.sort_index()

    save_bars_parquet(wide, args.out)
    meta.update(
        {
            "tickers": args.tickers,
            "interval": args.interval,
            "processed_dates": sorted(processed_dates),
            "bar_count": len(wide),
        }
    )
    _save_meta(args.out, meta)
    print(f"Saved: {args.out} ({len(wide)} bars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
