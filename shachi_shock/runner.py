import asyncio
import os

from shachi_shock.agents.memory import HistoryMemory
from shachi_shock.agents.regulator_agent import RegulatorAgent
from shachi_shock.env.regulatory_env import RegulatoryShockEnvironment
from shachi_shock.models import MarketSnapshot


def _default_model() -> str:
    base_url = os.environ.get("OPENAI_BASE_URL", "").strip()
    env_model = os.environ.get("LLM_MODEL_FAST", "").strip()
    if env_model:
        return env_model
    return "qwen/qwen3.7-plus" if base_url else "gpt-4o-mini"


def create_regulator_agents_for_bridge(
    env: RegulatoryShockEnvironment,
    model: str | None = None,
    temperature: float = 0.5,
    memory_length: int = 5,
) -> list[RegulatorAgent]:
    model = model or _default_model()
    configs = env.get_default_agent_configs() or []
    agents = []
    for cfg in configs:
        agent = RegulatorAgent(
            memory=HistoryMemory(history_length=memory_length),
            model=model,
            temperature=temperature,
        )
        agent.update_config(cfg)
        agents.append(agent)
    return agents


async def run_shock_cycle(
    env: RegulatoryShockEnvironment,
    agents: list[RegulatorAgent],
    snapshot: MarketSnapshot,
) -> list[dict]:
    """One Shachi step: observations -> parallel agent.step -> env.step -> shocks."""
    env.update_snapshot(snapshot)
    observations = await env.reset()

    futures = {
        agent_id: agents[agent_id].step(obs)
        for agent_id, obs in observations.items()
        if agent_id < len(agents)
    }
    responses = dict(zip(futures.keys(), await asyncio.gather(*futures.values())))
    await env.step(responses)
    return env.get_last_accepted_shocks()
