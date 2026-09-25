"""Offline agent for UI development. No keys, no network."""
import asyncio
from datetime import datetime

from ..base import Agent, Message


class EchoAgent(Agent):
    provider = "echo"
    model = "local-demo"

    async def stream(self, messages: list[Message], system: str):
        text = messages[-1].content.strip() if messages else ""
        lower = text.lower()
        now = datetime.now()
        if "time" in lower:
            reply = f"It's {now:%H:%M}."
        elif "date" in lower or "what day" in lower:
            reply = f"Today is {now:%A, %B %d}."
        elif lower.split(" ")[0] in ("hello", "hi", "hey"):
            reply = "Hello. All systems are running. Connect a real agent in the bridge's .env whenever you're ready."
        else:
            reply = (f"I heard: \"{text}\". I'm the demo agent, so I can only echo. "
                     "Set AGENT_PROVIDER in backend/.env to talk to a real model.")
        for word in reply.split(" "):
            yield word + " "
            await asyncio.sleep(0.035)
