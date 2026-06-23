"""Map LLMTrader .env to QuantAgent TradingGraph config."""

from __future__ import annotations

import os
import sys
from pathlib import Path

VENDOR = Path(__file__).resolve().parent.parent / "vendor" / "QuantAgent"


def ensure_vendor_path() -> Path:
    vendor = VENDOR
    if not vendor.exists():
        raise FileNotFoundError(
            f"QuantAgent not found at {vendor}. "
            "Run: git clone https://github.com/Y-Research-SBU/QuantAgent.git vendor/QuantAgent"
        )
    path = str(vendor)
    if path not in sys.path:
        sys.path.insert(0, path)
    _stub_optional_langchain_providers()
    return vendor


def _stub_optional_langchain_providers() -> None:
    """QuantAgent imports all LLM providers at import time; we only use OpenAI/RouterAI."""
    import types

    stubs = {
        "langchain_anthropic": "ChatAnthropic",
        "langchain_qwq": "ChatQwen",
    }
    for mod_name, class_name in stubs.items():
        if mod_name in sys.modules:
            continue
        mod = types.ModuleType(mod_name)

        def _unavailable_init(self, *args, _name=class_name, **kwargs):
            raise ImportError(
                f"{_name} requires optional package {mod_name}. "
                "Set agent_llm_provider/graph_llm_provider to openai (RouterAI)."
            )

        cls = type(class_name, (), {"__init__": _unavailable_init})
        setattr(mod, class_name, cls)
        sys.modules[mod_name] = mod


def get_llm_model_vision() -> str:
    """Vision model — only used when QUANT_ANALYSIS_MODE=vision."""
    env_model = os.environ.get("LLM_MODEL_VISION", "").strip()
    if env_model:
        return env_model
    if os.environ.get("OPENAI_BASE_URL", "").strip():
        return "qwen/qwen-vl-plus"
    return "gpt-4o"


def quant_analysis_mode() -> str:
    """text = classic TA for pattern/trend + LLM indicators (default); vision = chart images."""
    return os.environ.get("QUANT_ANALYSIS_MODE", "text").strip().lower()


def build_quant_config() -> dict:
    ensure_vendor_path()
    from default_config import DEFAULT_CONFIG

    cfg = DEFAULT_CONFIG.copy()
    api_key = os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "").strip()

    fast = os.environ.get("LLM_MODEL_FAST", "gpt-4o-mini").strip()
    smart = os.environ.get("LLM_MODEL_SMART", "gpt-4o").strip()
    mode = quant_analysis_mode()
    vision = get_llm_model_vision() if mode == "vision" else fast

    cfg["api_key"] = api_key
    cfg["agent_llm_provider"] = "openai"
    cfg["graph_llm_provider"] = "openai"
    cfg["agent_llm_model"] = fast
    cfg["graph_llm_model"] = vision
    cfg["agent_llm_temperature"] = float(os.environ.get("QUANT_AGENT_TEMPERATURE", "0.1"))
    cfg["graph_llm_temperature"] = float(os.environ.get("QUANT_GRAPH_TEMPERATURE", "0.1"))
    cfg["openai_base_url"] = base_url
    cfg["decision_model"] = smart
    cfg["quant_analysis_mode"] = mode
    return cfg


def validate_quant_env() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY required for QuantAgent")
    if quant_analysis_mode() == "vision" and not get_llm_model_vision():
        raise ValueError("LLM_MODEL_VISION required when QUANT_ANALYSIS_MODE=vision")
