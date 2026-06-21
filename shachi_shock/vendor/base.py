# Vendored from SakanaAI/shachi (Apache-2.0) — base interfaces only.
# Full shachi package is not installable (camel-ai==0.2.2 yanked).

import abc
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Generic, TypeVar

import pydantic


class Message(pydantic.BaseModel, abc.ABC):
    time: int = pydantic.Field(description="The time step when the message was sent.")
    src_agent_id: int | None = pydantic.Field(
        default=None,
        description="ID of the source agent. None means message from environment.",
    )
    dst_agent_id: int | None = pydantic.Field(
        default=None,
        description="ID of the destination agent. None means broadcast.",
    )


TMessage = TypeVar("TMessage", bound=Message)
TParameters = TypeVar("TParameters", bound=pydantic.BaseModel)


class ToolResponse(pydantic.BaseModel, abc.ABC):
    @abc.abstractmethod
    def format_as_prompt_text(self) -> str:
        raise NotImplementedError()


class Tool(pydantic.BaseModel, Generic[TParameters]):
    name: str
    description: str
    parameters_type: type[TParameters]
    fun: Callable[[TParameters], ToolResponse]


class Observation(pydantic.BaseModel, abc.ABC, Generic[TMessage]):
    agent_id: int
    messages: list[TMessage]
    reward: float | None = None
    response_type: type[pydantic.BaseModel] | None = None
    tools: list[Tool] = pydantic.Field(default_factory=list)

    @abc.abstractmethod
    def format_as_prompt_text(self) -> str:
        raise NotImplementedError()

    def format_as_prompt_payload(self) -> list[dict]:
        return [{"type": "text", "text": self.format_as_prompt_text()}]


TResult = TypeVar("TResult", bound=pydantic.BaseModel)
TAggregatedResult = TypeVar("TAggregatedResult", bound=pydantic.BaseModel)


class Environment(abc.ABC, Generic[TResult]):
    @abc.abstractmethod
    def num_agents(self) -> int:
        raise NotImplementedError()

    def get_default_agent_configs(self) -> list[dict] | None:
        return None

    @abc.abstractmethod
    def done(self) -> bool:
        raise NotImplementedError()

    @abc.abstractmethod
    async def reset(self) -> dict[int, Observation]:
        raise NotImplementedError()

    @abc.abstractmethod
    async def step(
        self,
        responses: dict[int, str | pydantic.BaseModel | None],
    ) -> dict[int, Observation]:
        raise NotImplementedError()

    @abc.abstractmethod
    def get_result(self) -> TResult:
        raise NotImplementedError()


class Task(abc.ABC, Generic[TResult, TAggregatedResult]):
    @abc.abstractmethod
    async def iterate_environments(self) -> AsyncIterator[Environment[TResult]]:
        raise NotImplementedError()
        yield None  # type: ignore[misc]

    @abc.abstractmethod
    def aggregate_results(self, results: Sequence[TResult]) -> TAggregatedResult:
        raise NotImplementedError()


class BaseMemory(abc.ABC):
    @abc.abstractmethod
    def add_record(self, messages: list[dict[str, str]]) -> None:
        pass

    @abc.abstractmethod
    def retrieve(self, query: str | None = None) -> str:
        pass

    @abc.abstractmethod
    def clear(self) -> None:
        pass


class Agent(abc.ABC):
    @abc.abstractmethod
    async def step(self, observation: Observation) -> str | pydantic.BaseModel | None:
        raise NotImplementedError()

    def update_config(self, kwargs_as_dict: dict) -> None:
        pass
