"""FastAPI bridge between the HUD frontend and the configured agent.

WebSocket protocol (/ws?session=<id>):
  client -> server
    {"type": "user_message", "text": "..."}
    {"type": "cancel"}                 stop the reply in progress
    {"type": "reset"}                  clear this session's history
    {"type": "ping", "t": <ms>}        latency probe
    {"type": "toggle", "service": "analysis" | "dispatcher" | "summarizer" | "reviewer", "enabled": bool}
  server -> client
    {"type": "hello", assistant, agent, session_id, turns}
    {"type": "service", service, enabled, dry_run, poll_seconds, last_run, last_error, totals}
    {"type": "start", id}
    {"type": "token", id, text}
    {"type": "done", id, text, first_token_ms, total_ms, turns}   (+ "speak", "local" on bridge-written replies)
    {"type": "cancelled", id} | {"type": "error", id, message}
    {"type": "pong", t} | {"type": "reset_ok"}
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agent import Agent, AgentError, Message, build_agent

from . import commands
from .config import get_settings
from .services import PollingService
from .sessions import SessionStore

log = logging.getLogger("bridge")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
settings = get_settings()
sessions = SessionStore(settings.history_limit)


def _build_watcher():
    from connectors.gmail_watcher import AlertWatcher, Filters
    return AlertWatcher(Filters.load())


def _build_dispatcher():
    from connectors.incident_dispatcher import build_from_settings
    return build_from_settings(settings)


def _dispatch_errors() -> tuple[type[Exception], ...]:
    from connectors.incident_dispatcher import DispatchError
    return (DispatchError,)


def _complete(prompt: str, system: str) -> str:
    """Ask the agent from a service's worker thread (the agent lives on the bridge's event loop)."""
    agent = app.state.agent
    return asyncio.run_coroutine_threadsafe(agent.complete([Message("user", prompt)], system), _loop).result(timeout=900)


def _pr_builder(mode: str):
    def build():
        from connectors.github_prs import build_from_settings
        info = app.state.agent.info()
        return build_from_settings(mode, settings, _complete, agent_name=f"{info['provider']}/{info['model']}")
    return build


def _pr_errors() -> tuple[type[Exception], ...]:
    from connectors.github_prs import PullRequestError
    return (PullRequestError,)


def system_prompt() -> str:
    """The configured prompt plus the latest pull request summaries/reviews, so the user can ask about them."""
    from connectors.github_prs import MODES, PullRequestJob
    context = []
    for mode in MODES:
        job = PullRequestJob(mode, github=None, complete=None)  # only reads its saved state
        context += [r.as_context() for r in job.recent(2)]
    if not context:
        return settings.system_prompt
    return (settings.system_prompt + "\n\nRecent pull request summaries and reviews you produced "
            "(use them if the user asks about pull requests):\n\n" + "\n\n".join(context))


services = {
    "analysis": PollingService("analysis", "Incident analysis", _build_watcher),
    "dispatcher": PollingService("dispatcher", "Incident dispatcher", _build_dispatcher, fatal=_dispatch_errors()),
    "summarizer": PollingService("summarizer", "MR summarizer", _pr_builder("summarizer"), fatal=_pr_errors()),
    "reviewer": PollingService("reviewer", "MR reviewer", _pr_builder("reviewer"), fatal=_pr_errors()),
}


_loop: asyncio.AbstractEventLoop | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _loop
    _loop = asyncio.get_running_loop()
    agent = build_agent(settings)
    await agent.startup()
    app.state.agent = agent
    log.info("Agent ready: %s", agent.info())
    yield
    for service in services.values():
        await service.stop()
    await agent.shutdown()


app = FastAPI(title=f"{settings.assistant_name} bridge", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def agent_of(app_) -> Agent:
    return app_.state.agent


# ----------------------------- REST -----------------------------
class ChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    session_id: str = "default"


@app.get("/api/health")
async def health():
    return {"ok": True}


@app.get("/api/agent")
async def agent_info():
    return {"assistant": settings.assistant_name, **agent_of(app).info()}


@app.post("/api/chat")
async def chat(req: ChatRequest):
    """Non-streaming endpoint for scripts, shortcuts, home automation, etc."""
    sessions.append(req.session_id, Message("user", req.text))
    try:
        reply = await agent_of(app).complete(sessions.history(req.session_id), system_prompt())
    except AgentError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    sessions.append(req.session_id, Message("assistant", reply))
    return {"reply": reply, "session_id": req.session_id}


class ServiceToggle(BaseModel):
    enabled: bool


async def _toggle(key: str, enabled: bool) -> str:
    service = services[key]
    return await (commands.enable(service) if enabled else commands.disable(service))


@app.get("/api/incidents")
async def analysis_status():
    return services["analysis"].status()


@app.post("/api/incidents")
async def analysis_toggle(req: ServiceToggle):
    return {"reply": await _toggle("analysis", req.enabled), **services["analysis"].status()}


@app.get("/api/dispatcher")
async def dispatcher_status():
    return services["dispatcher"].status()


@app.post("/api/dispatcher")
async def dispatcher_toggle(req: ServiceToggle):
    return {"reply": await _toggle("dispatcher", req.enabled), **services["dispatcher"].status()}


@app.post("/api/sessions/{session_id}/reset")
async def reset(session_id: str):
    sessions.reset(session_id)
    return {"ok": True}


# --------------------------- WebSocket ---------------------------
async def safe_send(ws: WebSocket, payload: dict) -> None:
    with suppress(Exception):
        await ws.send_json(payload)


async def say(ws: WebSocket, session_id: str, text: str, speak: str | None = None) -> None:
    """Send a reply the bridge wrote itself, shaped like an agent turn so the HUD shows and speaks it.
    `speak` is read aloud instead of `text` when the full text is too long to listen to."""
    turn_id = uuid.uuid4().hex[:12]
    await safe_send(ws, {"type": "start", "id": turn_id})
    await safe_send(ws, {"type": "token", "id": turn_id, "text": text})
    await safe_send(ws, {"type": "done", "id": turn_id, "text": text, "speak": speak or text,
                         "turns": sessions.turns(session_id), "local": True})


async def toggle_service(ws: WebSocket, session_id: str, key: str, enabled: bool) -> None:
    """The HUD's on/off buttons."""
    await say(ws, session_id, await _toggle(key, enabled))


async def run_turn(ws: WebSocket, session_id: str, text: str, offer_pending: bool = False) -> None:
    reply = await commands.handle(text, services, offer_pending)
    if reply is not None:
        sessions.append(session_id, Message("user", text))
        sessions.append(session_id, Message("assistant", reply))
        await say(ws, session_id, reply)
        return

    turn_id = uuid.uuid4().hex[:12]
    agent = agent_of(ws.app)
    sessions.append(session_id, Message("user", text))
    await safe_send(ws, {"type": "start", "id": turn_id})

    started = time.perf_counter()
    first_token_ms: int | None = None
    parts: list[str] = []
    try:
        async for chunk in agent.stream(sessions.history(session_id), system_prompt()):
            if not chunk:
                continue
            if first_token_ms is None:
                first_token_ms = int((time.perf_counter() - started) * 1000)
            parts.append(chunk)
            await ws.send_json({"type": "token", "id": turn_id, "text": chunk})
    except asyncio.CancelledError:
        if parts:
            sessions.append(session_id, Message("assistant", "".join(parts) + " [interrupted]"))
        await safe_send(ws, {"type": "cancelled", "id": turn_id})
        raise
    except AgentError as exc:
        await safe_send(ws, {"type": "error", "id": turn_id, "message": str(exc)})
        return
    except Exception:
        log.exception("Agent crashed")
        await safe_send(ws, {"type": "error", "id": turn_id, "message": "The agent crashed. Check the bridge logs."})
        return

    reply = "".join(parts)
    sessions.append(session_id, Message("assistant", reply))
    await safe_send(ws, {
        "type": "done",
        "id": turn_id,
        "text": reply,
        "first_token_ms": first_token_ms,
        "total_ms": int((time.perf_counter() - started) * 1000),
        "turns": sessions.turns(session_id),
    })


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    session_id = ws.query_params.get("session") or uuid.uuid4().hex
    await ws.send_json({
        "type": "hello",
        "assistant": settings.assistant_name,
        "agent": agent_of(ws.app).info(),
        "session_id": session_id,
        "turns": sessions.turns(session_id),
    })
    unsubscribers = [
        service.subscribe(lambda status: ws.send_json({"type": "service", **status}))
        for service in services.values()
    ] + [
        service.on_announce(lambda item: say(ws, session_id, item["text"], item.get("speak")))
        for service in services.values()
    ]
    for service in services.values():
        await ws.send_json({"type": "service", **service.status()})

    # Greet a new conversation. Not stored in history: agents expect it to start with the user.
    offer_pending = False
    if sessions.turns(session_id) == 0:
        text, offer_pending = commands.greeting(settings.assistant_name, services["analysis"])
        await say(ws, session_id, text)

    current: asyncio.Task | None = None

    async def cancel_current():
        nonlocal current
        if current and not current.done():
            current.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await current
        current = None

    try:
        while True:
            msg = await ws.receive_json()
            kind = msg.get("type")
            if kind == "ping":
                await ws.send_json({"type": "pong", "t": msg.get("t")})
            elif kind == "user_message":
                text = str(msg.get("text", "")).strip()
                if not text:
                    continue
                await cancel_current()
                current = asyncio.create_task(run_turn(ws, session_id, text[:8000], offer_pending))
                offer_pending = False  # only the first reply can answer the greeting
            elif kind == "toggle" and msg.get("service") in services:
                await cancel_current()
                current = asyncio.create_task(
                    toggle_service(ws, session_id, msg["service"], bool(msg.get("enabled")))
                )
                offer_pending = False
            elif kind == "cancel":
                await cancel_current()
            elif kind == "reset":
                await cancel_current()
                sessions.reset(session_id)
                await ws.send_json({"type": "reset_ok"})
    except WebSocketDisconnect:
        pass
    finally:
        for unsubscribe in unsubscribers:
            unsubscribe()
        await cancel_current()
