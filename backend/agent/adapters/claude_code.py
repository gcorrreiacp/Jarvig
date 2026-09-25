"""Claude through the Claude Code CLI, using your Claude account login instead of an API key.

Setup: install the CLI (curl -fsSL https://claude.ai/install.sh | bash), then run
`claude auth login` once. Personal, local use only: replies run on your own account.

Each reply runs `claude -p` with every tool disabled, so it answers questions only
and never touches files or runs commands.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from contextlib import suppress
from pathlib import Path

from ..base import Agent, AgentError, Message

_STREAM_LIMIT = 16 * 1024 * 1024  # a final assistant event can be one long JSON line


def find_cli(configured: str) -> str:
    """Resolve the CLI path; the installer puts it in ~/.local/bin, which is often not on PATH."""
    for candidate in (configured, str(Path.home() / ".local/bin/claude")):
        found = shutil.which(candidate)
        if found:
            return found
    raise AgentError(
        "Claude Code CLI not found. Install it with: curl -fsSL https://claude.ai/install.sh | bash"
    )


def build_prompt(messages: list[Message]) -> str:
    """The CLI takes one prompt per run, so earlier turns are passed as a transcript."""
    *history, latest = messages
    if not history:
        return latest.content
    lines = ["Conversation so far:"]
    for m in history:
        lines.append(f"{'User' if m.role == 'user' else 'Assistant'}: {m.content}")
    lines += ["", "Reply to the user's latest message:", latest.content]
    return "\n".join(lines)


class ClaudeCodeAgent(Agent):
    provider = "claude_code"

    def __init__(self, cli: str = "claude", model: str = ""):
        self.cli = find_cli(cli)
        self.model = model or "default"
        self._model_arg = model
        # Run from an empty folder so no project CLAUDE.md or settings leak into replies.
        self._workdir = tempfile.mkdtemp(prefix="aura-claude-")

    async def shutdown(self):
        shutil.rmtree(self._workdir, ignore_errors=True)

    def _args(self, system: str) -> list[str]:
        args = [
            self.cli, "-p",
            "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            "--tools", "",
            "--strict-mcp-config",
            "--no-session-persistence",
            "--system-prompt", system,
        ]
        if self._model_arg:
            args += ["--model", self._model_arg]
        return args

    async def stream(self, messages: list[Message], system: str):
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}  # force the account login
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._args(system),
                cwd=self._workdir, env=env, limit=_STREAM_LIMIT,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise AgentError(f"Can't start Claude Code CLI: {exc}") from exc

        streamed = False
        try:
            proc.stdin.write(build_prompt(messages).encode())
            await proc.stdin.drain()
            proc.stdin.close()

            async for raw in proc.stdout:
                event = _parse(raw)
                if event is None:
                    continue
                kind = event.get("type")
                if kind == "stream_event":
                    inner = event.get("event", {})
                    delta = inner.get("delta", {})
                    if inner.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                        streamed = True
                        yield delta.get("text", "")
                elif kind == "result":
                    if event.get("is_error"):
                        raise AgentError(_error_text(event.get("result") or event.get("subtype")))
                    if not streamed and event.get("result"):
                        yield event["result"]
                    break

            await proc.wait()
            if proc.returncode and not streamed:
                stderr = (await proc.stderr.read()).decode(errors="replace").strip()
                raise AgentError(_error_text(stderr or f"exit code {proc.returncode}"))
        finally:
            # Covers cancel/Esc from the HUD as well as normal completion.
            if proc.returncode is None:
                with suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()


def _parse(raw: bytes) -> dict | None:
    try:
        event = json.loads(raw)
    except ValueError:
        return None
    return event if isinstance(event, dict) else None


def _error_text(detail) -> str:
    text = str(detail or "unknown error")
    if "log in" in text.lower() or "login" in text.lower() or "auth" in text.lower():
        return f"Claude Code is not signed in. Run `claude auth login`. ({text})"
    return f"Claude Code error: {text}"
