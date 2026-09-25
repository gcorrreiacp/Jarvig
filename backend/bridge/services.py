"""Background services the bridge can switch on and off: incident analysis and the incident dispatcher.

Each service wraps a job object with `run_once() -> dict of counts`, `poll_seconds`
and `dry_run`. The job is built when the service starts (so a bad config or login
shows up as a readable error), then polled until the service is stopped. Services
always start switched off when the bridge starts.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

log = logging.getLogger("services")

Listener = Callable[[dict], Awaitable[None]]


class StopService(Exception):
    """Raised by a job when retrying cannot help (bad login, missing permission)."""


class PollingService:
    def __init__(self, key: str, title: str, build: Callable[[], Any], fatal: tuple[type[Exception], ...] = ()):
        self.key = key          # "analysis" | "dispatcher", used in the protocol
        self.title = title      # "Incident analysis", used in replies
        self._build = build
        self._fatal = (StopService, *fatal)  # errors that stop the service instead of retrying
        self._task: asyncio.Task | None = None
        self._job = None
        self._listeners: set[Listener] = set()
        self.last_run: str | None = None
        self.last_error: str | None = None
        self.totals: dict[str, int] = {}

    @property
    def enabled(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def job(self):
        return self._job

    def status(self) -> dict:
        return {
            "service": self.key,
            "enabled": self.enabled,
            "dry_run": bool(self._job and self._job.dry_run),
            "poll_seconds": self._job.poll_seconds if self._job else None,
            "last_run": self.last_run,
            "last_error": self.last_error,
            "totals": self.totals,
        }

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    async def _publish(self) -> None:
        status = self.status()
        for listener in list(self._listeners):
            try:
                await listener(status)
            except Exception:  # a closed socket must not stop the others
                self._listeners.discard(listener)

    async def start(self) -> None:
        """Build the job and start polling. Raises RuntimeError with a readable message on failure."""
        if self.enabled:
            return
        try:
            self._job = await asyncio.to_thread(self._build)
        except Exception as exc:
            self.last_error = str(exc)
            await self._publish()
            raise RuntimeError(str(exc)) from exc
        self.last_error = None
        self.totals = {}
        self._task = asyncio.create_task(self._loop())
        log.info("%s enabled%s", self.title, " (dry run)" if self._job.dry_run else "")
        await self._publish()

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            log.info("%s disabled", self.title)
        await self._publish()

    async def _loop(self) -> None:
        from googleapiclient.errors import HttpError

        from connectors.gmail_watcher import is_rate_limit

        while True:
            try:
                counts = await asyncio.to_thread(self._job.run_once)
                for key, value in counts.items():
                    if key != "checked" and isinstance(value, int):
                        self.totals[key] = self.totals.get(key, 0) + value
                self.last_error = counts.get("error")
            except self._fatal as exc:
                await self._fail(str(exc))
                return
            except HttpError as exc:
                if exc.resp.status in (401, 403) and not is_rate_limit(exc):
                    await self._fail("Gmail refused access. Run: python -m connectors.gmail auth --modify")
                    return
                self.last_error = f"Gmail error, retrying: {exc.reason}"
                log.warning("%s: %s", self.title, self.last_error)
            except Exception as exc:  # network drops etc.: keep polling
                self.last_error = f"Retrying after error: {exc}"
                log.warning("%s: %s", self.title, self.last_error)
            self.last_run = datetime.now(timezone.utc).isoformat(timespec="seconds")
            await self._publish()
            await asyncio.sleep(self._job.poll_seconds)

    async def _fail(self, message: str) -> None:
        self.last_error = message
        log.error("%s stopped: %s", self.title, message)
        self._task = None
        await self._publish()
