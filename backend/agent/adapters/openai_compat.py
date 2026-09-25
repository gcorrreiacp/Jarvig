"""Any server that speaks the OpenAI Chat Completions API.

Works with OpenAI, Ollama, LM Studio, vLLM, Groq, OpenRouter, Together, etc.
"""
import httpx

from ..base import Agent, AgentError, Message
from ._sse import raise_for_status, sse_events, try_json


class OpenAICompatAgent(Agent):
    provider = "openai"

    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.model = model
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.client = httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(120, connect=10))

    async def shutdown(self):
        await self.client.aclose()

    async def stream(self, messages: list[Message], system: str):
        payload = {
            "model": self.model,
            "stream": True,
            "messages": [{"role": "system", "content": system}] + [m.as_dict() for m in messages],
        }
        try:
            async with self.client.stream("POST", f"{self.base_url}/chat/completions", json=payload) as r:
                await raise_for_status(r, "Model server")
                async for _, data in sse_events(r):
                    if data.strip() == "[DONE]":
                        break
                    chunk = try_json(data) or {}
                    for choice in chunk.get("choices") or []:
                        delta = (choice.get("delta") or {}).get("content")
                        if delta:
                            yield delta
        except httpx.HTTPError as exc:
            raise AgentError(f"Can't reach {self.base_url}: {exc}") from exc
