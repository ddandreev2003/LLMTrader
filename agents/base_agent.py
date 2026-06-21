import json
import os
import re

from openai import AsyncOpenAI


def _using_routerai() -> bool:
    return bool(os.environ.get("OPENAI_BASE_URL", "").strip())


def get_llm_model_fast() -> str:
    env_model = os.environ.get("LLM_MODEL_FAST", "").strip()
    if env_model:
        return env_model
    return "qwen/qwen3.7-plus" if _using_routerai() else "gpt-4o-mini"


def get_llm_model_smart() -> str:
    env_model = os.environ.get("LLM_MODEL_SMART", "").strip()
    if env_model:
        return env_model
    return "qwen/qwen3.7-plus" if _using_routerai() else "gpt-4o"


class BaseAgent:
    """
    Общий каркас для всех агентов.
    Содержит клиент OpenAI и метод вызова LLM.
    """

    def __init__(self, bus, model: str | None = None, model_tier: str = "fast"):
        self.bus = bus
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError(
                "OPENAI_API_KEY не задан. Скопируйте .env.example в .env "
                "и укажите ваш ключ OpenAI API."
            )

        client_kwargs: dict = {"api_key": api_key}
        base_url = os.environ.get("OPENAI_BASE_URL", "").strip()
        if base_url:
            client_kwargs["base_url"] = base_url
            client_kwargs["timeout"] = 120.0

        self.client = AsyncOpenAI(**client_kwargs)
        if model_tier == "smart":
            self.model = model or get_llm_model_smart()
        else:
            self.model = model or get_llm_model_fast()

    async def ask_llm(self, system: str, user: str, max_tokens: int = 500) -> str:
        """Простой вызов ChatCompletion."""
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.7,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content.strip()

    @staticmethod
    def _parse_json_response(content: str) -> dict:
        content = content.strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
        if fenced:
            return json.loads(fenced.group(1).strip())

        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(content[start : end + 1])

        raise ValueError(f"Не удалось распарсить JSON из ответа LLM: {content[:200]}")

    async def ask_llm_json(self, system: str, user: str) -> dict:
        """Вызов с ответом в JSON (response_format с fallback для qwen/RouterAI)."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.5,
                max_tokens=300,
            )
            return self._parse_json_response(response.choices[0].message.content)
        except Exception:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.5,
                max_tokens=300,
            )
            return self._parse_json_response(response.choices[0].message.content)
