import os


def litellm_model_name(model: str) -> str:
    """RouterAI/OpenAI-compatible proxy requires openai/ prefix for litellm."""
    base_url = os.environ.get("OPENAI_BASE_URL", "").strip()
    if base_url and not model.startswith(("openai/", "azure/", "anthropic/")):
        return f"openai/{model}"
    return model


def litellm_kwargs() -> dict:
    kwargs: dict = {}
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        kwargs["api_key"] = api_key
    base_url = os.environ.get("OPENAI_BASE_URL", "").strip()
    if base_url:
        kwargs["api_base"] = base_url
        kwargs["timeout"] = 120
    return kwargs


def default_fast_model() -> str:
    env_model = os.environ.get("LLM_MODEL_FAST", "").strip()
    if env_model:
        return litellm_model_name(env_model)
    base_url = os.environ.get("OPENAI_BASE_URL", "").strip()
    return litellm_model_name("qwen/qwen3.7-plus" if base_url else "gpt-4o-mini")
