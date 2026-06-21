import logging
import os

import litellm
import pydantic

from shachi_shock.agents.memory import HistoryMemory
from shachi_shock.llm_config import litellm_kwargs, litellm_model_name
from shachi_shock.vendor.base import Agent, Observation

logging.getLogger("LiteLLM").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def _get_messages_from_observation(observation: Observation) -> list[dict]:
    return [
        {"role": "user", "content": observation.format_as_prompt_text()},
    ]


class RegulatorAgent(Agent):
    """Shachi-style regulator: Memory + Config + Tools + litellm structured output."""

    def __init__(
        self,
        memory: HistoryMemory,
        model: str | None = None,
        temperature: float = 0.5,
    ):
        self.memory = memory
        raw = model or os.environ.get("LLM_MODEL_FAST", "").strip()
        if not raw:
            raw = "qwen/qwen3.7-plus" if os.environ.get("OPENAI_BASE_URL", "").strip() else "gpt-4o-mini"
        self.model = litellm_model_name(raw)
        self.temperature = temperature
        self.system_prompt = ""
        self.role = ""
        self.allowed_shocks: list[str] = []
        self.intervention_bias = 0.3

    def update_config(self, config: dict) -> None:
        self.system_prompt = config.get("system_prompt", "")
        self.role = config.get("role", "")
        self.allowed_shocks = config.get("allowed_shocks", [])
        self.intervention_bias = float(config.get("intervention_bias", 0.3))

    async def step(self, observation: Observation) -> pydantic.BaseModel | None:
        response_type = observation.response_type
        if response_type is None:
            return None

        memory_text = self.memory.retrieve()
        messages = _get_messages_from_observation(observation)
        llm_kw = litellm_kwargs()

        if observation.tools:
            tools_for_llm = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters_type.model_json_schema(),
                    },
                }
                for tool in observation.tools
            ]
            completion = await litellm.acompletion(
                model=self.model,
                messages=messages,
                tools=tools_for_llm,
                tool_choice="auto",
                temperature=self.temperature,
                **llm_kw,
            )
            assistant_message = completion.choices[0].message
            if hasattr(assistant_message, "tool_calls") and assistant_message.tool_calls:
                for tool_call in assistant_message.tool_calls:
                    matching = next(
                        (t for t in observation.tools if t.name == tool_call.function.name),
                        None,
                    )
                    if matching is None:
                        continue
                    try:
                        args = tool_call.function.arguments
                        params = (
                            matching.parameters_type.model_validate_json(args)
                            if isinstance(args, str)
                            else matching.parameters_type.model_validate(args)
                        )
                        tool_response = matching.fun(params)
                        messages = [
                            {"role": "assistant", "content": tool_response.format_as_prompt_text()},
                        ] + messages
                    except Exception as exc:
                        logger.error("Tool error %s: %s", tool_call.function.name, exc)

        system_content = (
            f"Роль: {self.role}\n"
            f"Допустимые типы шоков: {', '.join(self.allowed_shocks)}\n"
            f"Склонность к вмешательству: {self.intervention_bias}\n"
            f"Память:\n{memory_text}\n\n"
            f"{self.system_prompt}"
        )

        try:
            action_completion = await litellm.acompletion(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_content},
                    *messages,
                ],
                temperature=self.temperature,
                response_format=response_type,
                **llm_kw,
            )
            content = action_completion.choices[0].message.content
            result = response_type.model_validate_json(content)
        except Exception:
            action_completion = await litellm.acompletion(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_content},
                    *messages,
                ],
                temperature=self.temperature,
                **llm_kw,
            )
            content = action_completion.choices[0].message.content or "{}"
            result = response_type.model_validate_json(content)

        self.memory.add_record(
            [
                {"role": "user", "content": messages[-1]["content"]},
                {"role": "assistant", "content": result.model_dump_json()},
            ]
        )
        return result


def create_regulator_agents(
    num_agents: int,
    model: str,
    temperature: float,
    memory_cls_path: str,
    memory_cls_kwargs: dict,
) -> list[RegulatorAgent]:
    import importlib

    module_path, class_name = memory_cls_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    memory_cls = getattr(module, class_name)

    agents = []
    for _ in range(num_agents):
        memory = memory_cls(**memory_cls_kwargs)
        agents.append(
            RegulatorAgent(model=model, temperature=temperature, memory=memory)
        )
    return agents
