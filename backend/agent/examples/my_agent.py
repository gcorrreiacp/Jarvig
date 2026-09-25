"""Template for plugging in any Python agent (LangChain, LangGraph, CrewAI,
LlamaIndex, your own tool loop...).

Enable with:
    AGENT_PROVIDER=custom
    CUSTOM_AGENT=agent.examples.my_agent:MyAgent
"""
import asyncio

from agent.base import Agent, Message


class MyAgent(Agent):
    provider = "custom"
    model = "my-agent"

    async def startup(self):
        # e.g. self.graph = build_langgraph_app()
        pass

    async def stream(self, messages: list[Message], system: str):
        # LangGraph example:
        # async for event in self.graph.astream_events({"messages": [...]}, version="v2"):
        #     if event["event"] == "on_chat_model_stream":
        #         yield event["data"]["chunk"].content
        reply = f"Custom agent received {len(messages)} message(s). Replace me in agent/examples/my_agent.py."
        for word in reply.split():
            yield word + " "
            await asyncio.sleep(0.03)
