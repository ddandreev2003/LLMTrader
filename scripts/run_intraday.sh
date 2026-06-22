#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="${ROOT}/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "Создайте venv: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

if [[ ! -f .env ]]; then
  echo "Скопируйте .env: cp .env.example .env и укажите OPENAI_API_KEY"
  exit 1
fi

PARQUET="${ROOT}/data/local/orderlog_bars_tls_5m.parquet"
if [[ ! -f "$PARQUET" ]]; then
  echo "Preprocess OrderLog → 5m bars..."
  "$PY" scripts/preprocess_orderlog_bars.py --workers 4
fi

export MARKET_MODE="${MARKET_MODE:-orderlog_intraday}"
export PORTFOLIO_CONFIG="${PORTFOLIO_CONFIG:-config/portfolio_intraday_tls.yaml}"
export STRATEGIES_CONFIG="${STRATEGIES_CONFIG:-config/strategies_intraday_tls.yaml}"
export SHOCK_INTERVAL="${SHOCK_INTERVAL:-36}"
export LIVE_VIZ="${LIVE_VIZ:-true}"
export VIZ_PORT="${VIZ_PORT:-8765}"

if [[ "${NFT:-0}" == "1" ]]; then
  export PORTFOLIO_CONFIG="config/portfolio_intraday_nft.yaml"
  export STRATEGIES_CONFIG="config/strategies_intraday_nft.yaml"
  echo "  NFT mode: 4 agents (CryptoPunks/BAYC/Azuki/Pudgy), T+SBER, ~5-7 торг. дней"
fi

if [[ "${HFT:-0}" == "1" ]]; then
  export PORTFOLIO_CONFIG="config/portfolio_intraday_hft.yaml"
  export STRATEGIES_CONFIG="config/strategies_intraday_hft.yaml"
  echo "  HFT mode: micro-steps OHLC, max 8 trades/step, warmup 2 bars"
fi

echo "Запуск intraday-симуляции..."
echo "  MARKET_MODE=$MARKET_MODE"
echo "  PORTFOLIO_CONFIG=$PORTFOLIO_CONFIG"
echo "  STRATEGIES_CONFIG=$STRATEGIES_CONFIG"

if [[ "${WEB_UI:-0}" == "1" ]]; then
  export WEB_UI=true
  echo "  Web-консоль: http://127.0.0.1:${VIZ_PORT}/ (Start/Pause/Step)"
  echo "  Presets: Normal / HFT / Dev в Settings"
  exec "$PY" scripts/web_server.py
fi

echo "  Симуляция: полный диапазон баров из portfolio (sim.use_full_range)"
echo "  Batch + дашборд: http://127.0.0.1:${VIZ_PORT}/dashboard.html"
echo "  Web UI: WEB_UI=1 $0"

exec "$PY" main.py
