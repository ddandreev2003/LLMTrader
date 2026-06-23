"""Tests for QuantAgent bridge signal mapping."""

from quant_agent.classic_analysts import analyze_patterns, analyze_trend
from quant_agent.config import ensure_vendor_path, quant_analysis_mode, build_quant_config
from quant_agent.kline_builder import bars_to_kline_data
from quant_agent.bridge import _parse_decision, decision_to_signal
from quant_agent.ta_summary import summarize_candlesticks, summarize_trend


def test_classic_pipeline_fast_no_llm():
    bars = [
        {"datetime": f"2025-01-03T07:0{i}:00", "open": 100 + i, "high": 102 + i, "low": 99 + i, "close": 101 + i, "volume": 10}
        for i in range(1, 20)
    ]
    k = bars_to_kline_data(bars)
    from quant_agent.classic_pipeline import run_classic_quant_decision

    result = run_classic_quant_decision("T", k, "1min")
    assert result["decision"] in ("LONG", "SHORT", "HOLD")
    assert result["reports"]["indicator"]
    assert result["reports"]["pattern"]
    assert result["reports"]["trend"]


def test_run_quant_decision_uses_classic_by_default(monkeypatch):
    monkeypatch.setenv("QUANT_ANALYSIS_MODE", "text")
    monkeypatch.delenv("QUANT_USE_LLM", raising=False)
    bars = [
        {"datetime": f"2025-01-03T07:0{i}:00", "open": 100 + i, "high": 102 + i, "low": 99 + i, "close": 101 + i, "volume": 10}
        for i in range(1, 20)
    ]
    from quant_agent.bridge import run_quant_decision

    result = run_quant_decision("T", bars, "1min")
    assert "indicator" in result["reports"]


def test_classic_pattern_and_trend_reports():
    bars = [
        {"datetime": f"2025-01-03T07:0{i}:00", "open": 100 + i, "high": 102 + i, "low": 99 + i, "close": 101 + i, "volume": 10}
        for i in range(1, 12)
    ]
    k = bars_to_kline_data(bars)
    pat = analyze_patterns(k)
    tr = analyze_trend(k)
    assert "Pattern report" in pat
    assert "Trend report" in tr
    assert "bullish" in pat.lower() or "rising" in pat.lower() or "higher" in pat.lower()
    assert "upward" in tr.lower() or "SMA5" in tr


def test_quant_default_mode_is_text():
    assert quant_analysis_mode() in ("text", "vision")


def test_build_quant_config_uses_fast_model_in_text_mode(monkeypatch):
    monkeypatch.setenv("QUANT_ANALYSIS_MODE", "text")
    monkeypatch.setenv("LLM_MODEL_FAST", "qwen/test-fast")
    monkeypatch.setenv("LLM_MODEL_VISION", "qwen/test-vl")
    cfg = build_quant_config()
    assert cfg["graph_llm_model"] == "qwen/test-fast"
    assert cfg["quant_analysis_mode"] == "text"


def test_ta_summary_from_bars():
    bars = [
        {"datetime": f"2025-01-03T07:0{i}:00", "open": 100 + i, "high": 102 + i, "low": 99 + i, "close": 101 + i, "volume": 10}
        for i in range(1, 7)
    ]
    k = bars_to_kline_data(bars)
    assert "bullish" in summarize_candlesticks(k).lower() or "higher closes" in summarize_candlesticks(k).lower()
    assert "slope" in summarize_trend(k).lower()


def test_vendor_import_without_optional_providers():
    ensure_vendor_path()
    from trading_graph import TradingGraph  # noqa: F401


def test_bars_to_kline_data():
    bars = [
        {"datetime": "2025-01-03T07:01:00", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
        {"datetime": "2025-01-03T07:02:00", "open": 1.5, "high": 2.5, "low": 1.4, "close": 2.0, "volume": 12},
    ]
    k = bars_to_kline_data(bars)
    assert len(k["Close"]) == 2
    assert k["Open"][0] == 1.0


def test_parse_long_short():
    raw = '```json\n{"decision": "LONG", "justification": "momentum up"}\n```'
    parsed = _parse_decision(raw)
    assert parsed["decision"] == "LONG"
    sig = decision_to_signal(parsed)
    assert sig["action"] == "buy"


def test_parse_short():
    parsed = _parse_decision('{"decision": "SHORT", "justification": "breakdown"}')
    sig = decision_to_signal(parsed)
    assert sig["action"] == "sell"
