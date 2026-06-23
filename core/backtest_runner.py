"""Run orderlog_stream backtest and write reports."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


async def run_backtest(
    *,
    portfolio_config: str = "config/portfolio_orderlog_tgs.yaml",
    strategies_config: str = "config/strategies_quantagent_tgs.yaml",
    with_shocks: bool = False,
    shock_interval: int = 36,
    max_bars: int | None = 120,
    from_date: str | None = None,
    till_date: str | None = None,
) -> dict:
    load_dotenv(ROOT / ".env")
    os.environ["RUN_MODE"] = "backtest"
    os.environ["MARKET_MODE"] = "orderlog_stream"
    os.environ["LIVE_VIZ"] = "false"
    os.environ["WEB_UI"] = "false"
    os.environ["PORTFOLIO_CONFIG"] = portfolio_config
    os.environ["STRATEGIES_CONFIG"] = strategies_config
    os.environ["SHOCK_BACKEND"] = "shachi" if with_shocks else "none"
    os.environ["SHOCK_INTERVAL"] = str(shock_interval)
    if max_bars:
        os.environ["SIM_N_TICKS"] = str(max_bars)

    if from_date or till_date:
        import yaml

        cfg_path = ROOT / portfolio_config
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        if from_date:
            cfg.setdefault("data", {})["from"] = from_date
        if till_date:
            cfg.setdefault("data", {})["till"] = till_date
        tmp = ROOT / "config" / "_backtest_portfolio_tmp.yaml"
        tmp.write_text(yaml.dump(cfg, allow_unicode=True), encoding="utf-8")
        os.environ["PORTFOLIO_CONFIG"] = str(tmp.relative_to(ROOT))

    from main import main as sim_main

    await sim_main()

    stats_path = ROOT / "output" / "agent_stats.json"
    trades_path = ROOT / "output" / "trades_history.csv"
    metrics: dict = {"with_shocks": with_shocks, "max_bars": max_bars}
    if stats_path.exists():
        metrics["agent_stats"] = json.loads(stats_path.read_text(encoding="utf-8"))
    if trades_path.exists():
        metrics["trades_file"] = str(trades_path)
    return metrics


def write_backtest_report(metrics: dict, out_path: Path) -> None:
    lines = [
        "# Backtest: QuantAgent T / GAZP / SBER (OrderLog stream)",
        "",
        f"- **Shocks:** {'enabled' if metrics.get('with_shocks') else 'disabled'}",
        f"- **Max bars:** {metrics.get('max_bars', 'all')}",
        "",
    ]
    agent_stats = metrics.get("agent_stats", {})
    if agent_stats:
        lines.append("## Agent results")
        lines.append("")
        lines.append("| Agent | Final value | PnL | Trades | Sharpe |")
        lines.append("|---|---:|---:|---:|---:|")
        for aid, st in agent_stats.items():
            lines.append(
                f"| {aid} | {st.get('final_value', 0):,.0f} | "
                f"{st.get('total_pnl', 0):,.0f} | {st.get('trade_count', 0)} | "
                f"{st.get('sharpe_ratio', 'N/A')} |"
            )
    lines.append("")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


def main_sync():
    import argparse

    parser = argparse.ArgumentParser(description="Backtest QuantAgent on OrderLog stream")
    parser.add_argument("--portfolio", default="config/portfolio_orderlog_tgs.yaml")
    parser.add_argument("--strategies", default="config/strategies_quantagent_tgs.yaml")
    parser.add_argument("--with-shocks", action="store_true")
    parser.add_argument("--no-shocks", action="store_true")
    parser.add_argument("--shock-interval", type=int, default=36)
    parser.add_argument("--max-bars", type=int, default=120)
    parser.add_argument("--from", dest="from_date", default=None)
    parser.add_argument("--till", dest="till_date", default=None)
    parser.add_argument("--report", default="reports/backtest_quant_tgs.md")
    args = parser.parse_args()

    with_shocks = args.with_shocks and not args.no_shocks
    metrics = asyncio.run(
        run_backtest(
            portfolio_config=args.portfolio,
            strategies_config=args.strategies,
            with_shocks=with_shocks,
            shock_interval=args.shock_interval,
            max_bars=args.max_bars,
            from_date=args.from_date,
            till_date=args.till_date,
        )
    )
    report_path = ROOT / args.report
    write_backtest_report(metrics, report_path)
    json_path = ROOT / "output" / "backtest_metrics.json"
    json_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Backtest done. Report: {report_path}")
    print(f"Metrics: {json_path}")


if __name__ == "__main__":
    main_sync()
