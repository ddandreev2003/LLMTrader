#!/usr/bin/env python3
"""Download MOEX OrderLog archives from Yandex Disk public folder."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "datasets" / "moex" / "orderlog"
PUBLIC_KEY = "https://disk.yandex.ru/d/BS0gr6vfX5Lcwg"
SUBPATH = "/Архивные данные Московской биржи 2025/SE_Акции (01.2025-12.2025)"
API_BASE = "https://cloud-api.yandex.net/v1/disk/public/resources"


def list_files() -> list[dict]:
    items: list[dict] = []
    offset = 0
    while True:
        params = urllib.parse.urlencode(
            {"public_key": PUBLIC_KEY, "path": SUBPATH, "limit": 1000, "offset": offset}
        )
        with urllib.request.urlopen(f"{API_BASE}?{params}", timeout=120) as resp:
            data = json.load(resp)
        batch = data["_embedded"]["items"]
        items.extend(i for i in batch if i["type"] == "file")
        total = data["_embedded"]["total"]
        offset += len(batch)
        if offset >= total:
            break
    return sorted(items, key=lambda x: x["name"])


def get_download_url(file_path: str) -> str:
    params = urllib.parse.urlencode({"public_key": PUBLIC_KEY, "path": file_path})
    url = f"https://cloud-api.yandex.net/v1/disk/public/resources/download?{params}"
    with urllib.request.urlopen(url, timeout=120) as resp:
        data = json.load(resp)
    return data["href"]


def download_file(item: dict, out_dir: Path, resume: bool = True) -> bool:
    name = item["name"]
    dest = out_dir / name
    expected = item["size"]

    if dest.exists() and dest.stat().st_size == expected:
        print(f"[skip] {name} уже скачан ({expected / 1e9:.2f} GB)")
        return True

    if dest.exists() and dest.stat().st_size > expected:
        print(f"[warn] {name} больше ожидаемого, перекачиваем")
        dest.unlink()

    offset = dest.stat().st_size if resume and dest.exists() else 0
    if offset:
        print(f"[resume] {name} с байта {offset / 1e9:.2f} GB")

    href = get_download_url(item["path"])
    req = urllib.request.Request(href)
    if offset:
        req.add_header("Range", f"bytes={offset}-")

    t0 = time.time()
    mode = "ab" if offset else "wb"
    with urllib.request.urlopen(req, timeout=600) as resp, open(dest, mode) as f:
        chunk = 1024 * 1024
        downloaded = offset
        while True:
            block = resp.read(chunk)
            if not block:
                break
            f.write(block)
            downloaded += len(block)
            if downloaded % (100 * 1024 * 1024) < chunk:
                pct = 100 * downloaded / expected
                rate = (downloaded - offset) / max(time.time() - t0, 0.1) / 1e6
                print(f"  {name}: {pct:.1f}% ({downloaded/1e9:.2f}/{expected/1e9:.2f} GB) {rate:.1f} MB/s")

    final = dest.stat().st_size
    if final != expected:
        print(f"[fail] {name}: размер {final} != {expected}")
        return False

    elapsed = time.time() - t0
    print(f"[ok] {name} ({final/1e9:.2f} GB за {elapsed/60:.1f} мин)")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Скачать OrderLog с Яндекс.Диска")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--limit", type=int, default=0, help="Макс. число файлов (0 = все)")
    parser.add_argument("--names", nargs="*", help="Конкретные имена файлов")
    parser.add_argument("--list-only", action="store_true")
    args = parser.parse_args()

    files = list_files()
    total_gb = sum(f["size"] for f in files) / 1e9
    print(f"Найдено {len(files)} файлов, суммарно {total_gb:.2f} GB")

    if args.list_only:
        for f in files:
            print(f"  {f['name']}\t{f['size']/1e9:.3f} GB")
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "yandex_manifest.json").write_text(
        json.dumps(
            [{"name": f["name"], "size": f["size"], "path": f["path"]} for f in files],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    if args.names:
        selected = [f for f in files if f["name"] in args.names]
    else:
        selected = files[: args.limit] if args.limit else files

    ok, fail = 0, 0
    for item in selected:
        try:
            if download_file(item, args.out):
                ok += 1
            else:
                fail += 1
        except Exception as exc:
            print(f"[error] {item['name']}: {exc}")
            fail += 1

    print(f"\nИтого: ok={ok}, fail={fail}, out={args.out}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
