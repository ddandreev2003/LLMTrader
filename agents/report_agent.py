import json

from agents.base_agent import BaseAgent
from core.event_bus import Event, EventType

REPORT_SYSTEM_PROMPT = """
Ты — финансовый аналитик российского фондового рынка (MOEX).
Проанализируй результаты мульти-активной симуляции.
Дай структурированный отчёт на русском языке:

1. Итоги: финальный PnL, стоимость портфеля, количество сделок, Sharpe ratio
2. Позиции по тикерам (SBER, GAZP и др.) — что в портфеле на конец
3. Влияние регуляторных шоков на портфель
4. Слабые места стратегий по активам (отдельно по каждому агенту)
5. Рекомендации и вывод

У каждого агента свой изолированный капитал — анализируй agent_stats отдельно.

Будь конкретным, используй цифры из данных.
"""


def _format_sharpe(stats: dict) -> str:
    sharpe = stats.get("sharpe_ratio")
    if sharpe is not None:
        return str(sharpe)
    reason = stats.get("sharpe_unavailable_reason", "недостаточная волатильность доходности")
    return f"N/A ({reason})"


def _exposure_at_tick(exposure_history: list[dict], tick: int) -> float | None:
    for entry in exposure_history:
        if entry.get("tick") == tick:
            return entry.get("invested_pct")
    closest = None
    for entry in exposure_history:
        if entry.get("tick", -1) <= tick:
            closest = entry
        else:
            break
    return closest.get("invested_pct") if closest else None


def _timing_diagnostics(trades: list[dict], total_ticks: int) -> list[str]:
    lines = []
    if not trades:
        lines.append("   Сделок не было.")
        return lines

    ticks = [t.get("tick", 0) for t in trades]
    first_tick, last_tick = min(ticks), max(ticks)
    lines.append(f"   Первая сделка: tick {first_tick}")
    lines.append(f"   Последняя сделка: tick {last_tick}")

    if total_ticks > 0:
        cutoff = max(1, int(total_ticks * 0.95))
        late_trades = sum(1 for t in ticks if t >= cutoff)
        late_pct = late_trades / len(ticks) * 100
        if late_pct > 80:
            lines.append(
                f"   ⚠ Предупреждение: {late_pct:.0f}% сделок в последних 5% тиков "
                f"(возможен поздний вход)"
            )
    return lines


def _shock_impact_section(shocks: list[dict], exposure_history: list[dict]) -> list[str]:
    lines = [f"   Всего шоков: {len(shocks)}"]
    if not shocks:
        lines.append("   (шоки не зафиксированы)")
        return lines

    for i, shock in enumerate(shocks, 1):
        tick = shock.get("tick", "?")
        invested = _exposure_at_tick(exposure_history, tick) if isinstance(tick, int) else None
        exposure_str = f"{invested:.1f}%" if invested is not None else "N/A"
        lines.append(
            f"   {i}. tick {tick} | {shock.get('type', '?')} | "
            f"impact {shock.get('price_impact_pct', 0):+.1f}% | "
            f"exposure {exposure_str}"
        )
        lines.append(f"      {shock.get('description', '—')}")
    return lines


def _fallback_report(stats: dict, shocks: list, trades: list) -> str:
    exposure_history = stats.get("exposure_history", [])
    total_ticks = len(exposure_history)

    lines = [
        "=== Отчёт (локальный fallback — LLM недоступен) ===",
        "",
        "1. Итоги",
        f"   PnL: {stats.get('total_pnl', 0)}",
        f"   Стоимость портфеля: {stats.get('portfolio_value', 0)}",
        f"   Сделок: {stats.get('total_trades', 0)} "
        f"(торговых дней: {stats.get('trading_days_with_trades', 0)})",
        f"   Sharpe: {_format_sharpe(stats)}",
        f"   Max drawdown: {stats.get('max_drawdown_pct', 0)}%",
        "",
        "2. Capital deployment",
        f"   Средняя доля в рынке: {stats.get('avg_invested_pct', 0)}%",
        f"   Финальная доля в рынке: {stats.get('final_invested_pct', 0)}%",
        f"   Финальный кэш: {stats.get('final_cash_pct', 0)}%",
        "",
        "3. Позиции по тикерам",
    ]

    positions = stats.get("positions", {})
    if positions:
        for ticker, info in positions.items():
            lines.append(
                f"   {ticker}: {info.get('position', 0)} шт. | "
                f"{info.get('market_value', 0)} руб. ({info.get('weight_pct', 0)}%)"
            )
    else:
        lines.append("   Позиций нет.")

    agent_stats = stats.get("agent_stats", {})
    if agent_stats:
        lines.extend(["", "4. Результаты по агентам (независимые портфели)"])
        for agent_id, ast in sorted(agent_stats.items()):
            sharpe = ast.get("sharpe_ratio")
            sharpe_str = str(sharpe) if sharpe is not None else "N/A"
            lines.append(
                f"   {agent_id} ({ast.get('ticker')}/{ast.get('strategy')}): "
                f"PnL {ast.get('total_pnl', 0)} ({ast.get('pnl_pct', 0)}%) | "
                f"value {ast.get('portfolio_value', 0)} | trades {ast.get('total_trades', 0)} | "
                f"Sharpe {sharpe_str}"
            )

    lines.extend([
        "",
        "5. Timing diagnostics",
    ])
    lines.extend(_timing_diagnostics(trades, total_ticks))

    lines.extend([
        "",
        "6. Влияние регуляторных шоков",
    ])
    lines.extend(_shock_impact_section(shocks, exposure_history))

    lines.extend([
        "",
        "7. Последние сделки",
    ])
    for t in trades[-10:]:
        ticker = t.get("ticker", "")
        prefix = f"{ticker} " if ticker else ""
        lines.append(
            f"   tick {t.get('tick')}: {prefix}{t.get('action')} "
            f"{t.get('quantity')} @ {t.get('price')}"
        )
    if not trades:
        lines.append("   Сделок не было.")

    lines.extend([
        "",
        "8. Вывод",
        "   Симуляция завершена; отчёт сформирован без LLM из собранной статистики.",
    ])
    return "\n".join(lines)


class ReportAgent(BaseAgent):

    def __init__(self, bus):
        super().__init__(bus, model_tier="smart")
        self._shocks_log: list[dict] = []
        self._trades_log: list[dict] = []

    async def on_shock(self, event: Event):
        self._shocks_log.append(event.payload)

    async def on_trade(self, event: Event):
        self._trades_log.append(event.payload)

    async def on_sim_ended(self, event: Event):
        stats = event.payload

        stats_for_llm = {k: v for k, v in stats.items() if k != "exposure_history"}
        user_prompt = f"""
Статистика симуляции:
{json.dumps(stats_for_llm, ensure_ascii=False, indent=2)}

Capital deployment:
  avg_invested_pct: {stats.get('avg_invested_pct')}%
  final_invested_pct: {stats.get('final_invested_pct')}%
  final_cash_pct: {stats.get('final_cash_pct')}%

Регуляторные шоки ({len(self._shocks_log)} шт.):
{json.dumps(self._shocks_log, ensure_ascii=False, indent=2)}

Сделки (последние 20):
{json.dumps(self._trades_log[-20:], ensure_ascii=False, indent=2)}
        """

        print("\n" + "=" * 60)
        print("ФОРМИРУЮ ОТЧЁТ (OpenAI)...")
        try:
            report = await self.ask_llm(REPORT_SYSTEM_PROMPT, user_prompt, max_tokens=1500)
        except Exception as e:
            print(f"[ReportAgent] LLM ошибка: {e}, используем локальный отчёт")
            report = _fallback_report(stats, self._shocks_log, self._trades_log)

        print("\n📊 ИТОГОВЫЙ ОТЧЁТ\n" + "=" * 60)
        print(report)

        with open("simulation_report.txt", "w", encoding="utf-8") as f:
            f.write(report)
        print("\n[ReportAgent] Отчёт сохранён в simulation_report.txt")

        await self.bus.publish(
            Event(
                type=EventType.REPORT_READY,
                payload={"report": report},
                source="ReportAgent",
            )
        )

    def register(self):
        self.bus.subscribe(EventType.SHOCK_TRIGGERED, self.on_shock)
        self.bus.subscribe(EventType.ORDER_FILLED, self.on_trade)
        self.bus.subscribe(EventType.SIM_ENDED, self.on_sim_ended)
