import json
from typing import AsyncIterator

import httpx


async def sse_events(response: httpx.Response) -> AsyncIterator[tuple[str | None, str]]:
    """Yield (event, data) pairs from a text/event-stream response."""
    event, data_lines = None, []
    async for line in response.aiter_lines():
        if line == "":
            if data_lines:
                yield event, "\n".join(data_lines)
            event, data_lines = None, []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "event":
            event = value
        elif field == "data":
            data_lines.append(value)
    if data_lines:
        yield event, "\n".join(data_lines)


def try_json(data: str):
    try:
        return json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return None


async def raise_for_status(response: httpx.Response, label: str) -> None:
    if response.status_code >= 400:
        body = (await response.aread()).decode(errors="replace")[:400]
        from ..base import AgentError
        raise AgentError(f"{label} returned {response.status_code}: {body}")
