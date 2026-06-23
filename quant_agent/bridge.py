"""Bridge LLMTrader ↔ QuantAgent TradingGraph."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
from pathlib import Path

from quant_agent.config import build_quant_config, ensure_vendor_path, quant_analysis_mode, validate_quant_env
from quant_agent.kline_builder import bars_to_kline_data

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "cache" / "quant_decisions"

_graph_lock = threading.Lock()
_graph_instance = None


def _make_trading_graph(config: dict):
    ensure_vendor_path()
    from langchain_openai import ChatOpenAI
    from graph_util import TechnicalTools

    base_url = config.get("openai_base_url", "").strip()
    mode = config.get("quant_analysis_mode", "text")

    class _RouterLLMFactory:
        def __init__(self, cfg: dict):
            self.config = cfg

        def create(self, model: str, temperature: float) -> ChatOpenAI:
            return ChatOpenAI(
                model=model,
                temperature=temperature,
                api_key=self.config.get("api_key"),
                base_url=base_url or None,
            )

    factory = _RouterLLMFactory(config)
    agent_llm = factory.create(
        config.get("agent_llm_model", "gpt-4o-mini"),
        config.get("agent_llm_temperature", 0.1),
    )
    graph_llm = factory.create(
        config.get("graph_llm_model", "gpt-4o-mini"),
        config.get("graph_llm_temperature", 0.1),
    )
    decision_llm = factory.create(
        config.get("decision_model", config.get("graph_llm_model", "gpt-4o")),
        config.get("graph_llm_temperature", 0.1),
    )
    toolkit = TechnicalTools()

    if mode == "vision":
        from trading_graph import TradingGraph

        class _RouterTradingGraph(TradingGraph):
            def _create_llm(self, provider: str, model: str, temperature: float):
                if provider == "openai" and base_url:
                    return factory.create(model, temperature)
                return super()._create_llm(provider, model, temperature)

        return _RouterTradingGraph(config=config)

    from quant_agent.text_graph import build_text_trading_graph

    class TextTradingGraph:
        def __init__(self):
            self.graph = build_text_trading_graph(agent_llm, decision_llm, toolkit)

    return TextTradingGraph()


def get_trading_graph():
    global _graph_instance
    with _graph_lock:
        if _graph_instance is None:
            validate_quant_env()
            _graph_instance = _make_trading_graph(build_quant_config())
        return _graph_instance


def _cache_key(ticker: str, time_frame: str, kline: dict) -> str:
    payload = json.dumps(kline, sort_keys=True, default=str)
    h = hashlib.sha256(f"{ticker}:{time_frame}:{payload}".encode()).hexdigest()[:24]
    return f"{ticker}_{time_frame}_{h}"


def _load_cache(key: str) -> dict | None:
    if os.environ.get("QUANT_CACHE_DECISIONS", "").strip().lower() not in ("1", "true", "yes"):
        return None
    path = CACHE_DIR / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def _save_cache(key: str, result: dict) -> None:
    if os.environ.get("QUANT_CACHE_DECISIONS", "").strip().lower() not in ("1", "true", "yes"):
        return
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{key}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_decision(raw: str) -> dict:
    text = raw.strip()
    if "```" in text:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if match:
            text = match.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        upper = text.upper()
        if "LONG" in upper:
            return {"decision": "LONG", "justification": text[:500]}
        if "SHORT" in upper:
            return {"decision": "SHORT", "justification": text[:500]}
        return {"decision": "HOLD", "justification": text[:500]}

    decision = str(data.get("decision", "HOLD")).upper()
    if decision not in ("LONG", "SHORT"):
        if "LONG" in decision:
            decision = "LONG"
        elif "SHORT" in decision:
            decision = "SHORT"
        else:
            decision = "HOLD"
    data["decision"] = decision
    return data


def decision_to_signal(parsed: dict) -> dict:
    d = parsed.get("decision", "HOLD").upper()
    if d == "LONG":
        return {"action": "buy", "quantity": 0, "reason": parsed.get("justification", "QuantAgent LONG")}
    if d == "SHORT":
        return {"action": "sell", "quantity": 0, "reason": parsed.get("justification", "QuantAgent SHORT")}
    return {"action": "hold", "quantity": 0, "reason": parsed.get("justification", "QuantAgent HOLD")}


def run_quant_decision(
    ticker: str,
    bars: list[dict],
    time_frame: str = "1min",
    entry_threshold: int = 3,
    forecast_bars: int = 3,
) -> dict:
    """Run full QuantAgent graph. Returns decision, reports, and trade signal."""
    kline = bars_to_kline_data(bars)
    if len(kline["Close"]) < 5:
        return {
            "decision": "HOLD",
            "signal": {"action": "hold", "quantity": 0, "reason": "insufficient bars"},
            "reports": {},
        }

    cache_key = _cache_key(ticker, time_frame, kline)
    cached = _load_cache(cache_key)
    if cached:
        return cached

    mode = quant_analysis_mode()
    use_llm = os.environ.get("QUANT_USE_LLM", "").strip().lower() in ("1", "true", "yes")

    if mode == "text" and not use_llm:
        from quant_agent.classic_pipeline import run_classic_quant_decision

        result = run_classic_quant_decision(
            ticker, kline, time_frame, entry_threshold=entry_threshold, forecast_bars=forecast_bars
        )
        _save_cache(cache_key, result)
        return result

    tg = get_trading_graph()
    initial_state = {
        "kline_data": kline,
        "analysis_results": None,
        "messages": [],
        "time_frame": time_frame,
        "stock_name": ticker,
    }

    final_state = tg.graph.invoke(initial_state)
    raw_decision = final_state.get("final_trade_decision", "")
    if hasattr(raw_decision, "content"):
        raw_decision = raw_decision.content

    parsed = _parse_decision(str(raw_decision))
    result = {
        "decision": parsed.get("decision", "HOLD"),
        "justification": parsed.get("justification", ""),
        "risk_reward_ratio": parsed.get("risk_reward_ratio"),
        "signal": decision_to_signal(parsed),
        "reports": {
            "indicator": final_state.get("indicator_report", ""),
            "pattern": final_state.get("pattern_report", ""),
            "trend": final_state.get("trend_report", ""),
            "raw_decision": str(raw_decision),
        },
    }
    _save_cache(cache_key, result)
    return result
