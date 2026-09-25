"""Incident analysis: runs the Gmail alert watcher in the background while enabled.

One watcher serves the whole bridge. It starts switched off every time the bridge
starts; the HUD greeting offers to switch it on, and it can be toggled by chat,
by the HUD button, or via /api/incidents.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable

log = logging.getLogger("incidents")

Listener = Callable[[dict], Awaitable[None]]


class IncidentAnalysis:
    def __init__(self):
        self._task: asyncio.Task | None = None
        self._watcher = None
        self._listeners: set[Listener] = set()
        self.last_run: str | None = None
        self.last_error: str | None = None
        self.totals: dict[str, int] = {}

    @property
    def enabled(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> dict:
        filters = self._watcher.filters if self._watcher else None
        return {
            "enabled": self.enabled,
            "dry_run": bool(self._watcher and self._watcher.dry_run),
            "poll_seconds": filters.poll_seconds if filters else None,
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
        """Connect to Gmail and start polling. Raises RuntimeError with a readable message on failure."""
        if self.enabled:
            return
        try:
            self._watcher = await asyncio.to_thread(_build_watcher)
        except Exception as exc:
            self.last_error = str(exc)
            await self._publish()
            raise RuntimeError(str(exc)) from exc
        self.last_error = None
        self.totals = {}
        self._task = asyncio.create_task(self._loop())
        log.info("Incident analysis enabled%s", " (dry run)" if self._watcher.dry_run else "")
        await self._publish()

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            log.info("Incident analysis disabled")
        await self._publish()

    async def _loop(self) -> None:
        from connectors.gmail_watcher import is_rate_limit
        from googleapiclient.errors import HttpError

        while True:
            try:
                counts = await asyncio.to_thread(self._watcher.run_once)
                for key, value in counts.items():
                    if key != "checked":
                        self.totals[key] = self.totals.get(key, 0) + value
                self.last_error = None
            except HttpError as exc:
                if exc.resp.status in (401, 403) and not is_rate_limit(exc):
                    self.last_error = "Gmail refused access. Run: python -m connectors.gmail auth --modify"
                    log.error(self.last_error)
                    self._task = None
                    await self._publish()
                    return
                self.last_error = f"Gmail error, retrying: {exc.reason}"
                log.warning(self.last_error)
            except Exception as exc:  # network drops etc.: keep polling
                self.last_error = f"Retrying after error: {exc}"
                log.warning(self.last_error)
            self.last_run = datetime.now(timezone.utc).isoformat(timespec="seconds")
            await self._publish()
            await asyncio.sleep(self._watcher.filters.poll_seconds)


def _build_watcher():
    from connectors.gmail_watcher import AlertWatcher, Filters
    return AlertWatcher(Filters.load())
