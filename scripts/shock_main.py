"""Standalone Shachi-style regulatory shock simulation (Hydra entrypoint)."""

import asyncio
import logging
import os
import sys
from pathlib import Path

import hydra
from dotenv import load_dotenv
from hydra.core.hydra_config import HydraConfig
from omegaconf import OmegaConf

# Ensure trading_simulator root is on sys.path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

load_dotenv(_ROOT / ".env")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def chunked(aiter, size):
    batch = []
    async for item in aiter:
        batch.append(item)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


async def run_episode(env, agents):
    observations = await env.reset()
    while not env.done():
        futures = {
            agent_id: agents[agent_id].step(obs)
            for agent_id, obs in observations.items()
        }
        responses = dict(zip(futures.keys(), await asyncio.gather(*futures.values())))
        logger.info("votes: %s", {k: type(v).__name__ for k, v in responses.items()})
        observations = await env.step(responses)

    result = env.get_result()
    for shock in result.accepted_shocks:
        logger.info(
            "ACCEPTED shock tick=%s type=%s by=%s desc=%s",
            result.tick,
            shock.type,
            shock.proposed_by,
            shock.description,
        )
    return result


@hydra.main(version_base=None, config_path="../configs", config_name="config")
def main(cfg):
    if not os.environ.get("OPENAI_API_KEY"):
        print("Ошибка: OPENAI_API_KEY не задан в .env")
        sys.exit(1)

    async def run():
        task = hydra.utils.instantiate(cfg.task)
        batchsize = cfg.batchsize
        all_results = []

        async for env_batch in chunked(task.iterate_environments(), batchsize):
            tasks = []
            for env in env_batch:
                agent_cfg = OmegaConf.to_container(cfg.agent, resolve=True)
                agent_cfg["num_agents"] = env.num_agents()
                agents = hydra.utils.instantiate(OmegaConf.create(agent_cfg))
                configs = env.get_default_agent_configs()
                if configs:
                    for agent_id, agent in enumerate(agents):
                        agent.update_config(configs[agent_id])
                tasks.append(run_episode(env, agents))
            all_results.extend(await asyncio.gather(*tasks))

        aggregated = task.aggregate_results(all_results)
        logger.info("Aggregated: %s", aggregated.model_dump())
        run_dir = HydraConfig.get().run.dir
        out_path = os.path.join(run_dir, "shock_results.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(aggregated.model_dump_json(indent=2))
        logger.info("Results saved to %s", out_path)

    asyncio.run(run())


if __name__ == "__main__":
    main()
