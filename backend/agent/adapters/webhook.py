"""Forward turns to your own agent service over HTTP.

Request (POST, JSON):
    {"system": "...", "messages": [{"role": "user", "content": "..."}, ...]}

Accepted responses:
    * text/event-stream  -> each `data:` is raw text or JSON {"token": "..."}
    * application/json   -> {"reply": "..."}  (also "output", "text", "response", "content")
    * anything else      -> streamed as plain text
"""
import httpx

from ..base import Agent, AgentError, Message
from ._sse import raise_for_status, sse_events, try_json

REPLY_KEYS = ("reply", "output", "text", "response", "content")


def _pick(body: dict) -> str | None:
    for key in ("token",) + REPLY_KEYS:
        if key in body and body[key] is not None:
            return str(body[key])
    return None


class WebhookAgent(Agent):
    provider = "webhook"

    def __init__(self, url: str, token: str = ""):
        self.url = url
        self.model = url
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.client = httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(180, connect=10))

    async def shutdown(self):
        await self.client.aclose()

    async def stream(self, messages: list[Message], system: str):
        payload = {"system": system, "messages": [m.as_dict() for m in messages]}
        try:
            async with self.client.stream("POST", self.url, json=payload) as r:
                await raise_for_status(r, "Webhook agent")
                ctype = r.headers.get("content-type", "")
                if "text/event-stream" in ctype:
                    async for _, data in sse_events(r):
                        if data.strip() == "[DONE]":
                            break
                        body = try_json(data)
                        yield (_pick(body) or "") if isinstance(body, dict) else data
                elif "application/json" in ctype:
                    body = try_json((await r.aread()).decode())
                    reply = _pick(body) if isinstance(body, dict) else None
                    if reply is None:
                        raise AgentError(f"Webhook JSON needs one of {REPLY_KEYS}")
                    yield reply
                else:
                    async for text in r.aiter_text():
                        yield text
        except httpx.HTTPError as exc:
            raise AgentError(f"Can't reach webhook {self.url}: {exc}") from exc
