#!/usr/bin/env python3
"""One-time MOEX data download for offline simulation."""

import argparse
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.data.moex_loader import download_from_portfolio_config  # noqa: E402


def main():
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Скачать данные MOEX в data/local/")
    parser.add_argument(
        "--config",
        default=os.environ.get("PORTFOLIO_CONFIG", "config/portfolio_ru.yaml"),
        help="Путь к portfolio yaml",
    )
    parser.add_argument("--force", action="store_true", help="Перекачать даже если файл есть")
    args = parser.parse_args()

    config_path = ROOT / args.config
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    prices = download_from_portfolio_config(cfg, force=args.force)
    print(f"Готово: {len(prices)} торговых дней, тикеры: {list(prices.columns)}")


if __name__ == "__main__":
    main()
