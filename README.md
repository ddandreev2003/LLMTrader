# Торговый симулятор с регуляторными шоками

Учебный симулятор алгоритмической торговли на MOEX: независимые TA-агенты по активам, мульти-агентные регуляторные шоки (Shachi-style), live-дашборд и итоговый аналитический отчёт.

## Быстрый старт

```bash
cd trading_simulator
python3.12 -m venv .venv
source .venv/bin/activate          # Linux / macOS
# .venv\Scripts\activate           # Windows

pip install -r requirements.txt
cp .env.example .env               # укажите OPENAI_API_KEY
python scripts/download_moex.py      # однократно: скачать данные
python main.py                       # симуляция + отчёт
```

Откройте в браузере **live-дашборд** (обновляется во время симуляции):

**http://127.0.0.1:8765/dashboard.html**

### Веб-консоль (пошаговое управление)

Интерактивный UI с кнопками Start / Pause / Step / Play / Stop, настройкой параметров и WebSocket-обновлением:

```bash
python scripts/web_server.py
# или
python -m web.server
```

Откройте **http://127.0.0.1:8765/** — панель управления, графики и вкладка «Настройки».

Переменные окружения:

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `WEB_PORT` | `8765` | Порт веб-сервера (тот же, что `VIZ_PORT`) |
| `WEB_UI` | `true` при web_server | Отключает встроенный ThreadingHTTPServer в `main.py` |
| `WEB_AUTO_INTERVAL_MS` | `500` | Интервал авто-режима Play |

Batch-режим без UI (как раньше):

```bash
python main.py
# или
SIM_BATCH=true python main.py
```

---

## Требования

- **Python 3.12** (см. `.python-version`)
- Ключ OpenAI API **или** [RouterAI](https://routerai.ru) (OpenAI-совместимый прокси)

---

## Установка

```bash
cd trading_simulator
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Настройка API-ключа

```bash
cp .env.example .env
```

### Вариант A: прямой OpenAI API

```
OPENAI_API_KEY=sk-...
```

По умолчанию: `gpt-4o-mini` (агенты) и `gpt-4o` (отчёт).

### Вариант B: RouterAI (рекомендуется для WSL/РФ)

```
OPENAI_API_KEY=ваш-ключ-routerai
OPENAI_BASE_URL=https://routerai.ru/api/v1
LLM_MODEL_FAST=qwen/qwen3.7-plus
LLM_MODEL_SMART=qwen/qwen3.7-plus
```

Если `OPENAI_BASE_URL` задан, а `LLM_MODEL_*` не указаны — автоматически используется `qwen/qwen3.7-plus`.

---

## Процедуры запуска

### 1. Загрузка данных MOEX (один раз)

```bash
python scripts/download_moex.py
```

Скачивает дневные свечи (борд TQBR) и сохраняет:

| Путь | Содержимое |
|------|------------|
| `data/cache/{TICKER}_*.csv` | сырые свечи по тикерам |
| `data/local/aligned_*.csv` | выровненный портфель для симуляции |

Период задаётся в [`config/portfolio_ru.yaml`](config/portfolio_ru.yaml):

```yaml
data:
  warmup_from: "2023-01-01"   # история для индикаторов (без торговли)
  from: "2024-01-01"          # начало торгового периода
  till: "2024-12-31"
```

Принудительно перекачать данные:

```bash
python scripts/download_moex.py --force
```

Другой конфиг портфеля:

```bash
python scripts/download_moex.py --config config/portfolio_ru.yaml
```

---

### 2. Основная симуляция (MOEX)

```bash
python main.py
```

По умолчанию:
- `MARKET_MODE=moex` — портфель российских акций, 1 тик = 1 торговый день
- `MOEX_OFFLINE=true` — только локальные данные из `data/local/`, без сети
- `LIVE_VIZ=true` — поднимает HTTP-дашборд на порту 8765
- `SHOCK_BACKEND=shachi` — 3 LLM-регулятора (ЦБ, биржа, медиа)

**Рекомендуемый порядок** (дашборд + симуляция):

1. Терминал 1 — откройте http://127.0.0.1:8765/dashboard.html
2. Терминал 2 — `python main.py`

На дашборде в реальном времени:
- график рынка (benchmark)
- equity-кривые **каждого агента** (независимые портфели)
- всплывающие уведомления при регуляторных шоках
- лента событий (шоки, сделки, halt/resume)

Отключить дашборд:

```bash
LIVE_VIZ=false python main.py
```

Сменить порт:

```bash
VIZ_PORT=9000 python main.py
```

---

### 3. Синтетический режим (один актив, legacy)

```bash
MARKET_MODE=synthetic python main.py
```

Один синтетический актив, `StrategyAgent` + SMA/LLM — как в ранних версиях MVP.

---

### 4. Standalone-запуск только шоков (Hydra)

Без торговли — только цикл регуляторных агентов:

```bash
python scripts/shock_main.py task=regulatory_shock agent=regulator_multi
```

---

### 5. Тесты

```bash
pytest tests/ -q
```

---

### 6. Отладка сигналов стратегий

Логирование buy/sell с tick, quantity и reason:

```bash
SIGNAL_DEBUG=true python main.py
```

---

## Портфель российских активов (MOEX)

Конфиг [`config/portfolio_ru.yaml`](config/portfolio_ru.yaml):

| Тикер | Описание |
|-------|----------|
| SBER | Сбербанк |
| GAZP | Газпром |
| LKOH | Лукойл |
| YDEX | Яндекс |
| GMKN | Норникель |
| NVTK | Новатэк |

Параметры портфеля:

| Параметр | Значение | Описание |
|----------|----------|----------|
| `initial_cash_rub` | 1 000 000 | Суммарный капитал (делится между агентами) |
| `agent_max_position_pct` | 0.95 | Макс. доля капитала агента в своём тикере |
| `commission_pct` | 0.0003 | Комиссия 0.03% |
| `trade_cutoff_ticks` | 5 | Запрет новых входов в последние N тиков |

---

## TA-стратегии (независимые агенты)

Конфиг [`config/strategies_ru.yaml`](config/strategies_ru.yaml) — **каждый агент = свой капитал + свой тикер + своя стратегия**:

| Агент | Тикер | Стратегия | Капитал* |
|-------|-------|-----------|----------|
| sber_sma | SBER | SMA Cross | ~166 667 ₽ |
| gazp_rsi | GAZP | RSI(14) | ~166 667 ₽ |
| lkoh_macd | LKOH | MACD | ~166 667 ₽ |
| ydex_momentum | YDEX | Momentum (ROC) | ~166 667 ₽ |
| gmkn_mean_rev | GMKN | Mean Reversion (Bollinger) | ~166 667 ₽ |
| nvtk_sma | NVTK | SMA Cross | ~166 667 ₽ |

\* Равное деление `initial_cash_rub / число агентов`. Переопределение: `initial_capital_rub` в записи агента.

Параметры стратегии (`params`):

| Параметр | Описание |
|----------|----------|
| `target_weight_pct` | Доля капитала агента на вход (0.90 = 90%) |
| `stop_loss_pct` | Стоп-лосс (0.05 = 5%) |
| `take_profit_pct` | Тейк-профит (0.10 = 10%) |
| `llm_enabled` | LLM-решения поверх TA (по умолчанию `false`) |

Реестр стратегий: [`strategies/registry.py`](strategies/registry.py).

### Ручная сборка портфеля (multi-ticker агент)

Агент может **самостоятельно собирать портфель** из нескольких тикеров — задаёте целевые веса в конфиге, агент ребалансирует к ним.

Пример [`config/strategies_portfolio_manual.yaml`](config/strategies_portfolio_manual.yaml):

```yaml
strategy_agents:
  - id: blue_chips_manual
    type: portfolio
    initial_capital_rub: 500000
    universe: [SBER, GAZP, LKOH]
    portfolio_targets:      # ручные веса (сумма → 100%)
      SBER: 0.40
      GAZP: 0.35
      LKOH: 0.25
    rebalance_interval_ticks: 5
    rebalance_threshold_pct: 0.03   # не торговать при дрифте < 3pp
```

Запуск с ручными портфелями:

```bash
STRATEGIES_CONFIG=config/strategies_portfolio_manual.yaml python main.py
```

| Поле | Описание |
|------|----------|
| `type: portfolio` | Мульти-тикерный агент |
| `universe` | Тикеры, которые агент может держать |
| `portfolio_targets` | Целевые доли капитала (ручная сборка) |
| `rebalance_interval_ticks` | Как часто проверять ребаланс |
| `rebalance_threshold_pct` | Минимальный дрифт веса для сделки |
| `signal_mode: true` | Опционально: TA-сигнал на каждый тикер вместо чистого ребаланса |
| `llm_enabled: true` | LLM может корректировать веса (`targets` в ответе) |

Режимы агентов:

| Режим | Конфиг | Поведение |
|-------|--------|-----------|
| **single** | `ticker: SBER` | Один тикер + TA (по умолчанию) |
| **portfolio** | `universe` + `portfolio_targets` | Ручная сборка, ребаланс к весам |

---

## Результаты

После завершения симуляции:

| Файл | Описание |
|------|----------|
| `simulation_report.txt` | Итоговый отчёт (LLM или fallback) |
| `output/trades_history.csv` | Все сделки с `agent_id` и `strategy` |
| `output/trades_{agent_id}.csv` | Сделки по каждому агенту |
| `output/agent_stats.json` | PnL, Sharpe, сделки — по агентам |
| `output/live_state.json` | Снимок состояния для дашборда |
| `viz/live_state.json` | Копия для HTTP-дашборда |

---

## Архитектура

```
MOEX data → MultiAssetMarketEngine (replay баров)
                ↓ TICK
    ┌───────────┼───────────┐
    ↓           ↓           ↓
TickerStrategy  ShachiShock  LiveVizAgent
Agent ×6        Bridge       (дашборд)
    ↓ SHOCK_TRIGGERED
    ↓ STRATEGY_SIGNAL
AgentPortfolioRegistry (изолированный капитал на агента)
    ↓ SIM_ENDED
ReportAgent → simulation_report.txt
```

Компоненты:

| Компонент | Роль |
|-----------|------|
| **MultiAssetMarketEngine** | Replay дневных баров MOEX, исполнение ордеров |
| **AgentPortfolioRegistry** | Независимые портфели: капитал, PnL, Sharpe на агента |
| **PortfolioStrategyAgent** | Ручная сборка портфеля (multi-ticker, ребаланс) |
| **TickerStrategyAgent** | TA + опциональный LLM, один тикер |
| **ShachiShockBridge** | 3 LLM-регулятора → `SHOCK_TRIGGERED` |
| **LiveVizAgent** | Онлайн-стрим в дашборд (шоки, equity, события) |
| **ReportAgent** | Итоговый отчёт с разбивкой по агентам |
| **MarketEngine / Portfolio** | Legacy режим `MARKET_MODE=synthetic` |

Все компоненты общаются через **EventBus** (`core/event_bus.py`).

### Shachi-style генерация шоков

Полный пакет `shachi` с PyPI не устанавливается (`camel-ai==0.2.2` yanked).  
Используется **vendored** интерфейс в `shachi_shock/vendor/base.py` (Apache-2.0).

Три регулятора:

| Агент | Роль | Типы шоков |
|-------|------|------------|
| 0 | Центральный банк | `rate_hike`, `position_limit` |
| 1 | Биржа | `circuit_breaker`, `halt`, `short_ban` |
| 2 | Медиа | `news_spike` |

---

## Переменные окружения

| Переменная | По умолчанию | Описание |
|---|---|---|
| `OPENAI_API_KEY` | — | API-ключ (OpenAI или RouterAI) |
| `OPENAI_BASE_URL` | пусто | Для RouterAI: `https://routerai.ru/api/v1` |
| `LLM_MODEL_FAST` | `gpt-4o-mini` / `qwen/qwen3.7-plus`* | Регуляторы, StrategyAgent |
| `LLM_MODEL_SMART` | `gpt-4o` / `qwen/qwen3.7-plus`* | ReportAgent |
| `MARKET_MODE` | `moex` | `moex` или `synthetic` |
| `PORTFOLIO_CONFIG` | `config/portfolio_ru.yaml` | Состав портфеля и период данных |
| `STRATEGIES_CONFIG` | `config/strategies_ru.yaml` | Агенты и стратегии |
| `MOEX_OFFLINE` | `true` | Только `data/local/`, без сети |
| `SHOCK_BACKEND` | `shachi` | `shachi` или `legacy` |
| `SHOCK_INTERVAL` | `30` | Проверка шоков каждые N тиков |
| `SIM_N_TICKS` | все бары | Число торговых дней симуляции |
| `LIVE_VIZ` | `true` | Live-дашборд при симуляции |
| `VIZ_PORT` | `8765` | Порт HTTP-дашборда |
| `SIGNAL_DEBUG` | `false` | Лог buy/sell сигналов в консоль |

\* Если задан `OPENAI_BASE_URL`, дефолт моделей — `qwen/qwen3.7-plus`.

---

## Примеры команд

```bash
# Ручная сборка портфеля (2 агента × 3 тикера)
STRATEGIES_CONFIG=config/strategies_portfolio_manual.yaml python main.py

# Полный цикл: данные → симуляция с дашбордом
python scripts/download_moex.py --force
python main.py

# Симуляция на 60 дней, шоки каждые 20 тиков
SIM_N_TICKS=60 SHOCK_INTERVAL=20 python main.py

# Без дашборда, с отладкой сигналов
LIVE_VIZ=false SIGNAL_DEBUG=true python main.py

# Legacy-шоки (один ShockAgent вместо Shachi)
SHOCK_BACKEND=legacy python main.py

# Докачка данных из сети во время симуляции
MOEX_OFFLINE=false python main.py

# Тесты
pytest tests/ -v
```

---

## Оценка стоимости API

Один прогон MOEX (~105 тиков, `SHOCK_INTERVAL=30`): порядка 5–10 вызовов LLM (шоки + отчёт).  
При OpenAI `gpt-4o-mini` — обычно менее $0.05; тарифы RouterAI см. на [routerai.ru](https://routerai.ru).

---

## Структура проекта

```
trading_simulator/
├── main.py                    # точка входа
├── config/
│   ├── portfolio_ru.yaml      # активы, капитал, период данных
│   └── strategies_ru.yaml     # агенты и стратегии
├── core/
│   ├── event_bus.py
│   ├── multi_asset_market.py
│   ├── simulation_controller.py  # pause/step/play
│   ├── simulation_session.py     # сборка сессии для web API
│   ├── agent_portfolio.py     # изолированные портфели агентов
│   └── data/moex_loader.py
├── agents/
│   ├── ticker_strategy_agent.py
│   ├── live_viz_agent.py      # онлайн-дашборд
│   └── report_agent.py
├── strategies/                # SMA, RSI, MACD, Momentum, Mean Reversion
├── shachi_shock/              # регуляторные шоки
├── viz/
│   ├── control.html           # веб-консоль (управление + графики)
│   └── dashboard.html         # read-only live UI
├── web/
│   ├── server.py              # FastAPI app
│   └── routes.py              # REST + WebSocket
├── scripts/
│   ├── download_moex.py
│   ├── web_server.py          # запуск веб-консоли
│   └── shock_main.py
├── data/local/                # выровненные цены MOEX
├── output/                    # сделки, статистика, live_state
└── tests/
```
# LLMTrader
