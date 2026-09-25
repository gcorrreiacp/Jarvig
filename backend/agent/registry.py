from __future__ import annotations

import importlib

from .base import Agent, AgentError


def _load_custom(path: str) -> Agent:
    if ":" not in path:
        raise AgentError("CUSTOM_AGENT must look like 'package.module:ClassName'")
    module_name, class_name = path.split(":", 1)
    cls = getattr(importlib.import_module(module_name), class_name)
    agent = cls()
    if not isinstance(agent, Agent):
        raise AgentError(f"{path} does not subclass agent.base.Agent")
    return agent


def build_agent(settings) -> Agent:
    provider = settings.agent_provider.lower().strip()

    if provider == "echo":
        from .adapters.echo import EchoAgent
        return EchoAgent()

    if provider == "openai":
        from .adapters.openai_compat import OpenAICompatAgent
        return OpenAICompatAgent(settings.openai_base_url, settings.openai_api_key, settings.openai_model)

    if provider == "anthropic":
        from .adapters.anthropic_agent import AnthropicAgent
        if not settings.anthropic_api_key:
            raise AgentError("ANTHROPIC_API_KEY is empty")
        return AnthropicAgent(settings.anthropic_api_key, settings.anthropic_model)

    if provider == "claude_code":
        from .adapters.claude_code import ClaudeCodeAgent
        return ClaudeCodeAgent(settings.claude_code_cli, settings.claude_code_model)

    if provider == "webhook":
        from .adapters.webhook import WebhookAgent
        if not settings.webhook_url:
            raise AgentError("WEBHOOK_URL is empty")
        return WebhookAgent(settings.webhook_url, settings.webhook_token)

    if provider == "custom":
        return _load_custom(settings.custom_agent)

    raise AgentError(f"Unknown AGENT_PROVIDER '{provider}'")
