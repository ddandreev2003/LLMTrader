#!/usr/bin/env python3
"""Download MOEX OrderLog (Jan–Feb), aggregate tickers in parallel, write MD report."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.download_yandex_orderlog import (  # noqa: E402
    PUBLIC_KEY,
    SUBPATH,
    API_BASE,
    download_file,
    get_download_url,
    list_files,
)
from scripts.orderlog_aggregate import aggregate_trades  # noqa: E402

DEFAULT_OUT = ROOT / "datasets" / "moex" / "orderlog"
DEFAULT_REPORT = ROOT / "reports" / "orderlog_top_tickers_jan_feb_2025.md"


def filter_by_months(files: list[dict], prefixes: tuple[str, ...]) -> list[dict]:
    out = []
    for f in files:
        name = f["name"]
        if not name.startswith("OrderLog") or not name.endswith(".zip"):
            continue
        yyyymm = name[8:14]  # OrderLogYYYYMMDD.zip
        if any(yyyymm.startswith(p) for p in prefixes):
            out.append(f)
    return sorted(out, key=lambda x: x["name"])


def parallel_download(files: list[dict], out_dir: Path, workers: int) -> tuple[int, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ok, fail = 0, 0

    def _one(item: dict) -> bool:
        return download_file(item, out_dir)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_one, item): item["name"] for item in files}
        for future in as_completed(futures):
            name = futures[future]
            try:
                if future.result():
                    ok += 1
                else:
                    fail += 1
                    print(f"[fail] {name}")
            except Exception as exc:
                fail += 1
                print(f"[error] {name}: {exc}")
    return ok, fail


def parallel_aggregate(zip_paths: list[Path], workers: int) -> tuple[dict, dict, int, list]:
    total_turnover: dict[str, float] = defaultdict(float)
    total_volume: dict[str, float] = defaultdict(float)
    total_trades = 0
    day_stats: list[tuple[str, int, list[tuple[str, float]]]] = []

    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(aggregate_trades, str(p)): p for p in zip_paths}
        done = 0
        for future in as_completed(futures):
            name, day_turnover, day_volume, day_trades = future.result()
            done += 1
            for ticker, value in day_turnover.items():
                total_turnover[ticker] += value
            for ticker, value in day_volume.items():
                total_volume[ticker] += value
            total_trades += day_trades
            top_day = sorted(day_turnover.items(), key=lambda x: x[1], reverse=True)[:3]
            day_stats.append((name, day_trades, top_day))
            top_str = ", ".join(f"{t} ({v / 1e9:.2f} млрд ₽)" for t, v in top_day)
            print(f"[{done}/{len(zip_paths)}] {name}: {day_trades:,} сделок; топ: {top_str}")

    day_stats.sort(key=lambda x: x[0])
    return dict(total_turnover), dict(total_volume), total_trades, day_stats


def fmt_rub(value: float) -> str:
    if value >= 1e12:
        return f"{value / 1e12:.2f} трлн ₽"
    if value >= 1e9:
        return f"{value / 1e9:.2f} млрд ₽"
    if value >= 1e6:
        return f"{value / 1e6:.2f} млн ₽"
    return f"{value:,.0f} ₽"


def fmt_num(value: float) -> str:
    return f"{value:,.0f}"


def write_report(
    path: Path,
    *,
    months: tuple[str, ...],
    zip_files: list[Path],
    total_turnover: dict[str, float],
    total_volume: dict[str, float],
    total_trades: int,
    day_stats: list,
    elapsed_sec: float,
    download_workers: int,
    aggregate_workers: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    grand_total = sum(total_turnover.values())
    ranked = sorted(total_turnover.items(), key=lambda x: x[1], reverse=True)

    lines: list[str] = [
        "# Тикеры MOEX OrderLog — январь–февраль 2025",
        "",
        f"Источник: [Яндекс.Диск — SE_Акции (01.2025–12.2025)](https://disk.yandex.ru/d/BS0gr6vfX5Lcwg/Архивные%20данные%20Московской%20биржи%202025/SE_Акции%20(01.2025-12.2025))",
        "",
        "## Параметры",
        "",
        f"- **Период:** {', '.join(months)}",
        f"- **Торговых дней (архивов):** {len(zip_files)}",
        f"- **Всего сделок (ACTION=2):** {total_trades:,}",
        f"- **Уникальных тикеров:** {len(ranked)}",
        f"- **Суммарный оборот:** {fmt_rub(grand_total)}",
        f"- **Параллелизация:** загрузка — {download_workers} потоков, агрегация — {aggregate_workers} процессов",
        f"- **Время агрегации:** {elapsed_sec / 60:.1f} мин",
        "",
        "**Метрика сортировки:** оборот в рублях (`VOLUME × TRADEPRICE` по сделкам).",
        "",
        "## Топ-30 тикеров по обороту",
        "",
        "| # | Тикер | Оборот, ₽ | Объём, шт. | Доля оборота |",
        "|---:|---|---:|---:|---:|",
    ]

    for i, (ticker, rub) in enumerate(ranked[:30], 1):
        share = 100 * rub / grand_total if grand_total else 0
        vol = total_volume.get(ticker, 0)
        lines.append(
            f"| {i} | {ticker} | {fmt_num(rub)} | {fmt_num(vol)} | {share:.2f}% |"
        )

    lines.extend(
        [
            "",
            "## Полный рейтинг тикеров",
            "",
            "| # | Тикер | Оборот, ₽ | Объём, шт. | Доля оборота |",
            "|---:|---|---:|---:|---:|",
        ]
    )

    for i, (ticker, rub) in enumerate(ranked, 1):
        share = 100 * rub / grand_total if grand_total else 0
        vol = total_volume.get(ticker, 0)
        lines.append(
            f"| {i} | {ticker} | {fmt_num(rub)} | {fmt_num(vol)} | {share:.2f}% |"
        )

    lines.extend(["", "## Топ-3 по дням", ""])
    for name, trades, top_day in day_stats:
        date = name.replace("OrderLog", "").replace(".zip", "")
        top_str = ", ".join(f"**{t}** ({fmt_rub(v)})" for t, v in top_day)
        lines.append(f"- **{date}** — {trades:,} сделок: {top_str}")

    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nОтчёт: {path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--months",
        nargs="+",
        default=["202501", "202502"],
        help="Префиксы YYYYMM (по умолчанию янв–фев 2025)",
    )
    parser.add_argument("--download-workers", type=int, default=4)
    parser.add_argument("--aggregate-workers", type=int, default=0)
    parser.add_argument("--skip-download", action="store_true")
    args = parser.parse_args()

    months = tuple(args.months)
    print(f"Месяцы: {', '.join(months)}")

    all_files = list_files()
    selected = filter_by_months(all_files, months)
    if not selected:
        print("Нет файлов для выбранных месяцев", file=sys.stderr)
        return 1

    total_gb = sum(f["size"] for f in selected) / 1e9
    print(f"Архивов: {len(selected)}, ~{total_gb:.1f} GB")

    if not args.skip_download:
        print(f"\n=== Загрузка ({args.download_workers} потоков) ===")
        t0 = time.time()
        ok, fail = parallel_download(selected, args.out, args.download_workers)
        print(f"Загрузка: ok={ok}, fail={fail}, {time.time() - t0:.0f} с")
        if fail:
            return 1

    zip_paths = [args.out / f["name"] for f in selected]
    missing = [p for p in zip_paths if not p.exists()]
    if missing:
        print(f"Отсутствуют {len(missing)} файлов:", file=sys.stderr)
        for p in missing[:5]:
            print(f"  {p.name}", file=sys.stderr)
        return 1

    workers = args.aggregate_workers or min(os.cpu_count() or 4, len(zip_paths))
    print(f"\n=== Агрегация ({workers} процессов, {len(zip_paths)} файлов) ===")
    t0 = time.perf_counter()
    turnover, volume, trades, day_stats = parallel_aggregate(zip_paths, workers)
    elapsed = time.perf_counter() - t0
    print(f"Готово за {elapsed / 60:.1f} мин; сделок: {trades:,}; тикеров: {len(turnover)}")

    write_report(
        args.report,
        months=months,
        zip_files=zip_paths,
        total_turnover=turnover,
        total_volume=volume,
        total_trades=trades,
        day_stats=day_stats,
        elapsed_sec=elapsed,
        download_workers=args.download_workers,
        aggregate_workers=workers,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
