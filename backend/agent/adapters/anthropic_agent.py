"""Anthropic Messages API with streaming."""
import httpx

from ..base import Agent, AgentError, Message
from ._sse import raise_for_status, sse_events, try_json

API_URL = "https://api.anthropic.com/v1/messages"


class AnthropicAgent(Agent):
    provider = "anthropic"

    def __init__(self, api_key: str, model: str, max_tokens: int = 1024):
        self.model = model
        self.max_tokens = max_tokens
        self.client = httpx.AsyncClient(
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            timeout=httpx.Timeout(120, connect=10),
        )

    async def shutdown(self):
        await self.client.aclose()

    async def stream(self, messages: list[Message], system: str):
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "stream": True,
            "messages": [m.as_dict() for m in messages],
        }
        try:
            async with self.client.stream("POST", API_URL, json=payload) as r:
                await raise_for_status(r, "Anthropic API")
                async for event, data in sse_events(r):
                    body = try_json(data) or {}
                    if event == "content_block_delta":
                        delta = body.get("delta", {})
                        if delta.get("type") == "text_delta":
                            yield delta.get("text", "")
                    elif event == "error":
                        raise AgentError(body.get("error", {}).get("message", "Anthropic stream error"))
                    elif event == "message_stop":
                        break
        except httpx.HTTPError as exc:
            raise AgentError(f"Can't reach Anthropic API: {exc}") from exc
