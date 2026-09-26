"""Standard mode and private mode: which AI answers, switchable by voice.

- standard: the configured agent (AGENT_PROVIDER, normally Claude Code).
- private:  a model on this Mac through an OpenAI-compatible server (Ollama by default),
            so nothing leaves the machine. It is a beta feature (see features.json).

Everything that asks the AI (chat, skills, the MR summarizer and reviewer) uses
`brains.current`, so switching affects all of it. The bridge always starts in standard
mode. If the standard agent fails, the bridge can fall back to private mode itself.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from urllib.parse import urlparse

import httpx

from agent import Agent, build_agent
from agent.adapters.openai_compat import OpenAICompatAgent

log = logging.getLogger("brains")


class BrainError(Exception):
    """Private mode can't be used right now; the message says why and what to do."""


class Brains:
    def __init__(self, settings):
        self.settings = settings
        self.standard: Agent | None = None
        self.private: Agent | None = None
        self.mode = "standard"
        self._lock = asyncio.Lock()

    async def startup(self) -> None:
        self.standard = build_agent(self.settings)
        await self.standard.startup()

    async def shutdown(self) -> None:
        for agent in (self.standard, self.private):
            if agent is not None:
                await agent.shutdown()

    @property
    def current(self) -> Agent:
        return self.private if self.mode == "private" and self.private else self.standard

    @property
    def local_model(self) -> str:
        return self.settings.local_model

    def info(self) -> dict:
        return {"mode": self.mode, **self.current.info()}

    async def to_private(self) -> None:
        """Switch to the local model, starting Ollama if needed. Raises BrainError if it can't."""
        async with self._lock:
            if self.mode == "private":
                return
            await self._ensure_local()
            await self._check_answers()
            if self.private is None:
                agent = OpenAICompatAgent(self.settings.local_base_url, self.settings.local_api_key,
                                          self.settings.local_model)
                agent.provider = "ollama" if self._is_ollama() else "local"
                await agent.startup()
                self.private = agent
            self.mode = "private"
            log.info("Private mode on: %s", self.private.info())

    async def to_standard(self) -> None:
        async with self._lock:
            self.mode = "standard"
            log.info("Standard mode: %s", self.standard.info())

    # --- the local server
    def _is_ollama(self) -> bool:
        return urlparse(self.settings.local_base_url).port == 11434

    def _root(self) -> str:
        url = self.settings.local_base_url.rstrip("/")
        return url[:-3] if url.endswith("/v1") else url

    async def _ensure_local(self) -> None:
        async with httpx.AsyncClient(timeout=3) as http:
            if not await self._reachable(http):
                if not self._is_ollama():
                    raise BrainError(f"The local model server at {self.settings.local_base_url} isn't running. "
                                     "Start it (e.g. LM Studio's server) and try again.")
                self._start_ollama()
                for _ in range(30):  # up to ~15 s
                    await asyncio.sleep(0.5)
                    if await self._reachable(http):
                        break
                else:
                    raise BrainError("I couldn't start Ollama. Open the Ollama app, then try again.")
            if self._is_ollama():
                tags = (await http.get(f"{self._root()}/api/tags")).json().get("models", [])
                names = {m["name"] for m in tags} | {m["name"].split(":")[0] for m in tags}
                if self.settings.local_model not in names:
                    raise BrainError(f"The model {self.settings.local_model} isn't downloaded in Ollama. "
                                     f"Run: ollama pull {self.settings.local_model}")

    async def _check_answers(self) -> None:
        """Ask the model for one token: proves it really runs (a server can list a model whose files are
        gone) and loads it into memory, so the first real answer is quicker."""
        url = f"{self.settings.local_base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {self.settings.local_api_key}"} if self.settings.local_api_key else {}
        body = {"model": self.settings.local_model, "max_tokens": 1,
                "messages": [{"role": "user", "content": "Reply with OK."}]}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=5)) as http:
                r = await http.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise BrainError(f"The local model didn't respond ({exc}).") from exc
        if r.status_code != 200:
            try:
                detail = r.json().get("error", {})
                detail = detail.get("message", detail) if isinstance(detail, dict) else detail
            except ValueError:
                detail = r.text[:200]
            raise BrainError(f"The local model {self.settings.local_model} can't answer: {detail}")

    async def _reachable(self, http: httpx.AsyncClient) -> bool:
        try:
            return (await http.get(f"{self.settings.local_base_url.rstrip('/')}/models")).status_code == 200
        except httpx.HTTPError:
            return False

    @staticmethod
    def _start_ollama() -> None:
        exe = shutil.which("ollama") or "/usr/local/bin/ollama"
        log.info("Starting Ollama (%s serve)", exe)
        try:
            subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)  # keeps running on its own, like the Ollama app
        except OSError as exc:
            raise BrainError(f"I couldn't start Ollama ({exc}). Open the Ollama app, then try again.") from exc
