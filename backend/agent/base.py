"""The contract every agent adapter implements.

The bridge only ever talks to this interface, so swapping agents never
touches the bridge or the frontend.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator, Literal

Role = Literal["user", "assistant"]


@dataclass
class Message:
    role: Role
    content: str

    def as_dict(self) -> dict:
        return {"role": self.role, "content": self.content}


class AgentError(RuntimeError):
    """Raised when the upstream agent fails; the message is shown to the user."""


class Agent(ABC):
    provider: str = "base"
    model: str | None = None

    async def startup(self) -> None:
        """Open clients, warm caches, load tools."""

    async def shutdown(self) -> None:
        """Close clients."""

    @abstractmethod
    def stream(self, messages: list[Message], system: str) -> AsyncIterator[str]:
        """Yield the reply as text chunks. Implement as `async def ... yield`."""

    async def complete(self, messages: list[Message], system: str) -> str:
        return "".join([chunk async for chunk in self.stream(messages, system)])

    def info(self) -> dict:
        return {"provider": self.provider, "model": self.model}
