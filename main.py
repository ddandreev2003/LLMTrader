import asyncio
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

from agents.base_agent import get_llm_model_fast, get_llm_model_smart
from agents.coordinator_agent import create_intraday_agents_from_config
from agents.live_viz_agent import LiveVizAgent, start_viz_server
from agents.market_maker_agent import create_market_maker_agent_if_enabled
from agents.report_agent import ReportAgent
from agents.shock_agent import ShockAgent
from agents.strategy_agent import StrategyAgent
from agents.portfolio_strategy_agent import create_strategy_agents_from_config
from core.agent_portfolio import AgentPortfolioRegistry
from core.data.moex_loader import compute_warmup_ticks, load_from_portfolio_config
from core.data.orderlog_bars import compute_warmup_bars, load_orderlog_ohlc, resolve_sim_tick_count, resolve_stream_max_bars
from core.event_bus import EventBus
from core.intraday_market import IntradayMarketEngine, parse_intraday_engine_config
from core.orderlog_stream_market import OrderLogStreamMarketEngine
from core.data.orderlog_parser import resolve_stream_config
from core.market import MarketEngine
from core.multi_asset_market import MultiAssetMarketEngine
from core.portfolio import Portfolio
from core.sim_log import bind_log_file, log, log_done, log_step, reset_timer
from shachi_shock.bridge import ShachiShockBridge


ROOT = Path(__file__).resolve().parent


def check_api_key() -> bool:
    load_dotenv(ROOT / ".env")
    if os.environ.get("OPENAI_API_KEY"):
        return True
    print("Ошибка: OPENAI_API_KEY не задан. Скопируйте .env.example в .env")
    return False


def load_portfolio_config() -> dict:
    path = os.environ.get("PORTFOLIO_CONFIG", "config/portfolio_ru.yaml")
    market_mode = os.environ.get("MARKET_MODE", "moex").strip().lower()
    if market_mode == "orderlog_stream" and path == "config/portfolio_ru.yaml":
        path = "config/portfolio_orderlog_tgs.yaml"
    config_path = ROOT / path
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def create_shock_backend(bus, shock_interval: int):
    backend = os.environ.get("SHOCK_BACKEND", "shachi").strip().lower()
    if backend in ("none", "off", "disabled"):
        return None
    if backend == "legacy":
        return ShockAgent(bus, shock_interval_ticks=shock_interval)
    return ShachiShockBridge(bus, shock_interval_ticks=shock_interval)


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


async def run_moex_mode(bus, portfolio_cfg: dict):
    offline = os.environ.get("MOEX_OFFLINE", "true").strip().lower() in ("1", "true", "yes", "on")
    print("Режим: MOEX (независимые агенты + общий рынок)")
    print(f"   Источник данных: {'локальный (data/local/)' if offline else 'MOEX ISS (сеть)'}")
    price_data = load_from_portfolio_config(portfolio_cfg, offline=offline)
    tickers = list(price_data.columns)
    data_cfg = portfolio_cfg.get("data", {})
    trade_from = data_cfg.get("from", "2024-01-01")
    warmup_ticks = compute_warmup_ticks(price_data, trade_from)
    print(f"   Активы: {', '.join(tickers)} | баров: {len(price_data)} | warmup: {warmup_ticks}")

    p_cfg = portfolio_cfg.get("portfolio", {})
    trade_cutoff_ticks = int(p_cfg.get("trade_cutoff_ticks", 5))
    total_capital = float(p_cfg.get("initial_cash_rub", 1_000_000))

    strategies_path = os.environ.get("STRATEGIES_CONFIG", "config/strategies_ru.yaml")
    strategy_agents = create_strategy_agents_from_config(
        bus, ROOT / strategies_path, portfolio_cfg=portfolio_cfg
    )
    print(f"   Стратегий-агентов: {len(strategy_agents)} (изолированный капитал)")
    for ag in strategy_agents:
        cap = getattr(ag, "initial_capital", total_capital / len(strategy_agents))
        universe = getattr(ag, "universe", [ag.ticker])
        if len(universe) > 1:
            targets = getattr(ag, "portfolio_targets", {})
            tgt = ", ".join(f"{t}:{w:.0%}" for t, w in targets.items())
            print(f"     - {ag.agent_id}: portfolio [{', '.join(universe)}] | {cap:,.0f} ₽ | {tgt}")
        else:
            print(f"     - {ag.agent_id}: {ag.ticker} / {ag.strategy.name} | {cap:,.0f} ₽")

    portfolio = AgentPortfolioRegistry(
        bus,
        agents=strategy_agents,
        total_capital=total_capital,
        max_position_pct=float(p_cfg.get("agent_max_position_pct", 0.95)),
        commission_pct=float(p_cfg.get("commission_pct", 0.0003)),
    )

    shock_interval = int(os.environ.get("SHOCK_INTERVAL", "30"))
    n_ticks = int(os.environ.get("SIM_N_TICKS", str(len(price_data))))

    for agent in strategy_agents:
        agent.total_ticks = n_ticks

    shock_backend = create_shock_backend(bus, shock_interval)
    report_agent = ReportAgent(bus)

    live_viz = None
    viz_server = None
    if _env_bool("LIVE_VIZ", default=True) and not _env_bool("WEB_UI", default=False):
        viz_server = start_viz_server()
        live_viz = LiveVizAgent(bus, agents=strategy_agents)

    portfolio.register()
    if shock_backend:
        shock_backend.register()
    for agent in strategy_agents:
        agent.register()
    report_agent.register()
    if live_viz:
        live_viz.register()

    market = MultiAssetMarketEngine(
        bus,
        price_data=price_data,
        portfolio=portfolio,
        warmup_ticks=warmup_ticks,
        trade_cutoff_ticks=trade_cutoff_ticks,
    )
    await asyncio.gather(bus.run(), market.run(n_ticks=n_ticks))

    if viz_server:
        viz_server.shutdown()


async def run_orderlog_intraday_mode(bus, portfolio_cfg: dict):
    print("Режим: orderlog_intraday (T / SBER, 5m бары + LLM + координатор)")
    data_cfg = portfolio_cfg.get("data", {})
    bars_path = ROOT / data_cfg.get("bars_path", "data/local/orderlog_bars_tls_5m.parquet")
    if not bars_path.exists():
        print(f"   Ошибка: нет файла баров {bars_path}")
        print("   Запустите: python scripts/preprocess_orderlog_bars.py")
        sys.exit(1)

    price_data, ohlc_data = load_orderlog_ohlc(portfolio_cfg, root=ROOT)
    tickers = list(price_data.columns)
    p_cfg = portfolio_cfg.get("portfolio", {})
    trade_cutoff_bars = int(p_cfg.get("trade_cutoff_bars", p_cfg.get("trade_cutoff_ticks", 6)))
    engine_cfg = parse_intraday_engine_config(portfolio_cfg)
    warmup_bars = compute_warmup_bars(price_data, int(data_cfg.get("warmup_bars", 24)))
    if portfolio_cfg.get("trading", {}).get("warmup_bars") is not None:
        warmup_bars = int(portfolio_cfg["trading"]["warmup_bars"])
    if portfolio_cfg.get("trading", {}).get("trade_cutoff_bars") is not None:
        trade_cutoff_bars = int(portfolio_cfg["trading"]["trade_cutoff_bars"])
    bar_interval = data_cfg.get("bar_interval", "5m")
    print(
        f"   Активы: {', '.join(tickers)} | баров: {len(price_data)} | "
        f"warmup: {warmup_bars} | interval: {bar_interval}"
        f"{' | HFT micro-steps' if engine_cfg.get('hft_mode') else ''}"
    )

    p_cfg = portfolio_cfg.get("portfolio", {})
    total_capital = float(p_cfg.get("initial_cash_rub", 1_000_000))

    strategies_path = os.environ.get("STRATEGIES_CONFIG", "config/strategies_intraday_tls.yaml")
    strategy_agents, coordinator = create_intraday_agents_from_config(
        bus, ROOT / strategies_path, portfolio_cfg=portfolio_cfg
    )
    if coordinator:
        print(f"   Агентов: {len(strategy_agents)} + координатор {coordinator.agent_id}")
    else:
        print(f"   Агентов: {len(strategy_agents)} (без координатора)")
    for ag in strategy_agents:
        cap = getattr(ag, "initial_capital", total_capital / len(strategy_agents))
        name = getattr(ag, "display_name", ag.agent_id)
        universe = getattr(ag, "universe", [getattr(ag, "ticker", "")])
        print(f"     - {name} [{', '.join(universe)}] / {ag.strategy.name} | {cap:,.0f} ₽")

    portfolio = AgentPortfolioRegistry(
        bus,
        agents=strategy_agents,
        total_capital=total_capital,
        max_position_pct=float(p_cfg.get("agent_max_position_pct", 0.95)),
        commission_pct=float(p_cfg.get("commission_pct", 0.0003)),
    )
    portfolio.max_trades_per_step = engine_cfg["max_trades_per_step"]

    shock_interval = int(os.environ.get("SHOCK_INTERVAL", "36"))
    n_ticks = resolve_sim_tick_count(
        price_data,
        portfolio_cfg,
        env_sim_n_ticks=os.environ.get("SIM_N_TICKS"),
    )
    trading_days = len({ts.date() for ts in price_data.index[:n_ticks]})
    print(f"   Симуляция: {n_ticks} баров (~{trading_days} торг. дней)")

    for agent in strategy_agents:
        agent.total_ticks = n_ticks

    shock_backend = create_shock_backend(bus, shock_interval)
    report_agent = ReportAgent(bus)

    live_viz = None
    viz_server = None
    if _env_bool("LIVE_VIZ", default=True) and not _env_bool("WEB_UI", default=False):
        viz_server = start_viz_server()
        live_viz = LiveVizAgent(bus, agents=strategy_agents, max_history=min(n_ticks, 5000))

    portfolio.register()
    if shock_backend:
        shock_backend.register()
    for agent in strategy_agents:
        agent.register()
    if coordinator:
        coordinator.register()
    report_agent.register()
    if live_viz:
        live_viz.set_sim_scope(n_ticks, trading_days)
        live_viz.register()

    market = IntradayMarketEngine(
        bus,
        price_data=price_data,
        portfolio=portfolio,
        warmup_bars=warmup_bars,
        trade_cutoff_bars=trade_cutoff_bars,
        bar_interval=bar_interval,
        ohlc_data=ohlc_data,
        **engine_cfg,
    )
    await asyncio.gather(bus.run(), market.run(n_ticks=n_ticks))

    if viz_server:
        viz_server.shutdown()


async def run_orderlog_stream_mode(bus, portfolio_cfg: dict):
    log_step("Режим orderlog_stream", "T / GAZP / SBER, поток OrderLog + стакан")
    stream = resolve_stream_config(portfolio_cfg, ROOT)
    if not stream["zip_paths"]:
        data = portfolio_cfg.get("data", {})
        print(f"   Ошибка: нет OrderLog zip в {stream['zip_dir']}")
        if data.get("from") or data.get("till"):
            print(f"   Диапазон дат: {data.get('from')} — {data.get('till')}")
            print("   Архивы на диске за 2025 год — проверьте from/till в PORTFOLIO_CONFIG")
        if portfolio_cfg.get("data", {}).get("source") != "orderlog_stream":
            print("   Подсказка: PORTFOLIO_CONFIG=config/portfolio_orderlog_tgs.yaml")
        n_on_disk = len(list(stream["zip_dir"].glob("OrderLog*.zip")))
        if n_on_disk:
            print(f"   На диске найдено {n_on_disk} zip, но ни один не попал в диапазон дат")
        sys.exit(1)

    tickers = stream["tickers"]
    engine_cfg = parse_intraday_engine_config(portfolio_cfg)
    data_cfg = portfolio_cfg.get("data", {})
    trading_cfg = portfolio_cfg.get("trading", {})
    warmup_bars = int(trading_cfg.get("warmup_bars", data_cfg.get("warmup_bars", 6)))
    warmup_fast = bool(trading_cfg.get("warmup_fast", data_cfg.get("warmup_fast", True)))
    min_kline = int(trading_cfg.get("min_kline_bars", data_cfg.get("min_kline_bars", 14)))

    p_cfg = portfolio_cfg.get("portfolio", {})
    trade_cutoff_bars = int(p_cfg.get("trade_cutoff_bars", p_cfg.get("trade_cutoff_ticks", 6)))
    if portfolio_cfg.get("trading", {}).get("trade_cutoff_bars") is not None:
        trade_cutoff_bars = int(portfolio_cfg["trading"]["trade_cutoff_bars"])
    total_capital = float(p_cfg.get("initial_cash_rub", 1_000_000))

    print(
        f"   Активы: {', '.join(tickers)} | сессий: {len(stream['zip_paths'])} | "
        f"bar: {stream['bar_interval']} | warmup: {warmup_bars}"
        f"{' (fast)' if warmup_fast else ''} | min kline: {min_kline}"
    )
    log(
        f"   Активы: {', '.join(tickers)} | сессий: {len(stream['zip_paths'])} | "
        f"bar: {stream['bar_interval']} | warmup: {warmup_bars}"
        f"{' (fast)' if warmup_fast else ''} | min kline: {min_kline}"
    )

    log_step("Загрузка агентов", strategies_path := os.environ.get(
        "STRATEGIES_CONFIG", "config/strategies_quantagent_tgs.yaml"
    ))
    strategy_agents, coordinator = create_intraday_agents_from_config(
        bus, ROOT / strategies_path, portfolio_cfg=portfolio_cfg
    )
    mm_agent = create_market_maker_agent_if_enabled(bus, portfolio_cfg)
    viz_agents = list(strategy_agents)
    if mm_agent:
        viz_agents.append(mm_agent)
        log("     · Market Maker — котировки в стакане")
    for ag in strategy_agents:
        cap = getattr(ag, "initial_capital", total_capital / len(strategy_agents))
        name = getattr(ag, "display_name", ag.agent_id)
        strat = getattr(getattr(ag, "strategy", None), "name", "quant_agent")
        log(f"     · {name} / {strat} | {cap:,.0f} ₽")
    log_done("Агенты", f"{len(strategy_agents)} + координатор" if coordinator else str(len(strategy_agents)))

    shock_interval = int(os.environ.get("SHOCK_INTERVAL", "36"))
    n_ticks = resolve_stream_max_bars(portfolio_cfg, os.environ.get("SIM_N_TICKS"))
    run_mode = os.environ.get("RUN_MODE", "").strip().lower()
    tick_interval = 0.0 if run_mode == "backtest" else float(os.environ.get("TICK_INTERVAL", "0"))

    log_step("Портфель и шоки", f"SHOCK_INTERVAL={shock_interval}")
    portfolio = AgentPortfolioRegistry(
        bus,
        agents=strategy_agents,
        total_capital=total_capital,
        max_position_pct=float(p_cfg.get("agent_max_position_pct", 0.95)),
        commission_pct=float(p_cfg.get("commission_pct", 0.0003)),
    )
    portfolio.max_trades_per_step = engine_cfg["max_trades_per_step"]

    shock_backend = create_shock_backend(bus, shock_interval)
    report_agent = ReportAgent(bus)

    live_viz = None
    viz_server = None
    if (
        _env_bool("LIVE_VIZ", default=True)
        and not _env_bool("WEB_UI", default=False)
        and run_mode != "backtest"
    ):
        log_step("Live-дашборд", f"http://127.0.0.1:{os.environ.get('VIZ_PORT', '8765')}/dashboard.html")
        viz_server = start_viz_server()
        live_viz = LiveVizAgent(bus, agents=viz_agents, max_history=5000)
        log_done("HTTP-сервер viz")

    log_step("Регистрация агентов на шине событий")
    portfolio.register()
    if shock_backend:
        shock_backend.register()
    for agent in strategy_agents:
        agent.register()
    if mm_agent:
        mm_agent.register()
    if coordinator:
        coordinator.register()
    report_agent.register()
    if live_viz:
        live_viz.register()
    log_done("Регистрация")

    log_step("Старт симуляции", "чтение OrderLog может занять несколько минут")
    market = OrderLogStreamMarketEngine(
        bus,
        portfolio_cfg=portfolio_cfg,
        root=ROOT,
        portfolio=portfolio,
        tick_interval=tick_interval,
        warmup_bars=warmup_bars,
        trade_cutoff_bars=trade_cutoff_bars,
        max_bars=n_ticks,
    )
    await asyncio.gather(bus.run(), market.run(n_ticks=n_ticks))
    log_done("Симуляция завершена")

    if viz_server:
        viz_server.shutdown()


async def run_synthetic_mode(bus):
    print("Режим: synthetic (один актив)")
    n_ticks = int(os.environ.get("SIM_N_TICKS", "300"))
    shock_interval = int(os.environ.get("SHOCK_INTERVAL", "60"))

    portfolio = Portfolio(bus)
    shock_backend = create_shock_backend(bus, shock_interval)
    strategy_agent = StrategyAgent(bus, call_interval_ticks=10)
    report_agent = ReportAgent(bus)

    portfolio.register()
    if shock_backend:
        shock_backend.register()
    strategy_agent.register()
    report_agent.register()

    market = MarketEngine(bus, portfolio=portfolio, start_price=100.0, tick_interval=0.02)
    await asyncio.gather(bus.run(), market.run(n_ticks=n_ticks))


async def main():
    reset_timer()
    bind_log_file(ROOT / "output" / "sim_startup.log")
    if not check_api_key():
        sys.exit(1)

    market_mode = os.environ.get("MARKET_MODE", "moex").strip().lower()
    provider = "RouterAI" if os.environ.get("OPENAI_BASE_URL", "").strip() else "OpenAI"
    fast_model = get_llm_model_fast()
    smart_model = get_llm_model_smart()
    shock_backend = os.environ.get("SHOCK_BACKEND", "shachi")

    log("=" * 50)
    log_step("Запуск торгового симулятора")
    log(f"   MARKET_MODE={market_mode} | SHOCK_BACKEND={shock_backend}")
    log(f"   LLM ({provider}): {fast_model} / {smart_model}")
    log(f"   Лог: {ROOT / 'output' / 'sim_startup.log'}")
    if _env_bool("LIVE_VIZ", default=True):
        log(f"   LIVE_VIZ → http://127.0.0.1:{os.environ.get('VIZ_PORT', '8765')}/dashboard.html")
    log("=" * 50)

    bus = EventBus()

    if market_mode == "moex":
        portfolio_cfg = load_portfolio_config()
        await run_moex_mode(bus, portfolio_cfg)
    elif market_mode == "orderlog_intraday":
        portfolio_cfg = load_portfolio_config()
        await run_orderlog_intraday_mode(bus, portfolio_cfg)
    elif market_mode == "orderlog_stream":
        portfolio_cfg = load_portfolio_config()
        await run_orderlog_stream_mode(bus, portfolio_cfg)
    else:
        await run_synthetic_mode(bus)


if __name__ == "__main__":
    asyncio.run(main())
