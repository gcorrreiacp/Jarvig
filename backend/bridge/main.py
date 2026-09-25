"""FastAPI bridge between the HUD frontend and the configured agent.

WebSocket protocol (/ws?session=<id>):
  client -> server
    {"type": "user_message", "text": "..."}
    {"type": "cancel"}                 stop the reply in progress
    {"type": "reset"}                  clear this session's history
    {"type": "ping", "t": <ms>}        latency probe
    {"type": "incidents", "enabled": bool}   switch incident analysis on/off
  server -> client
    {"type": "hello", assistant, agent, session_id, turns}
    {"type": "incidents", enabled, dry_run, poll_seconds, last_run, last_error, totals}
    {"type": "start", id}
    {"type": "token", id, text}
    {"type": "done", id, text, first_token_ms, total_ms, turns}
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
from .incidents import IncidentAnalysis
from .sessions import SessionStore

log = logging.getLogger("bridge")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
settings = get_settings()
sessions = SessionStore(settings.history_limit)
incidents = IncidentAnalysis()


@asynccontextmanager
async def lifespan(app: FastAPI):
    agent = build_agent(settings)
    await agent.startup()
    app.state.agent = agent
    log.info("Agent ready: %s", agent.info())
    yield
    await incidents.stop()
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
        reply = await agent_of(app).complete(sessions.history(req.session_id), settings.system_prompt)
    except AgentError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    sessions.append(req.session_id, Message("assistant", reply))
    return {"reply": reply, "session_id": req.session_id}


class IncidentToggle(BaseModel):
    enabled: bool


@app.get("/api/incidents")
async def incidents_status():
    return incidents.status()


@app.post("/api/incidents")
async def incidents_toggle(req: IncidentToggle):
    reply = await (commands.enable(incidents) if req.enabled else commands.disable(incidents))
    return {"reply": reply, **incidents.status()}


@app.post("/api/sessions/{session_id}/reset")
async def reset(session_id: str):
    sessions.reset(session_id)
    return {"ok": True}


# --------------------------- WebSocket ---------------------------
async def safe_send(ws: WebSocket, payload: dict) -> None:
    with suppress(Exception):
        await ws.send_json(payload)


async def say(ws: WebSocket, session_id: str, text: str) -> None:
    """Send a reply the bridge wrote itself, shaped like an agent turn so the HUD shows and speaks it."""
    turn_id = uuid.uuid4().hex[:12]
    await safe_send(ws, {"type": "start", "id": turn_id})
    await safe_send(ws, {"type": "token", "id": turn_id, "text": text})
    await safe_send(ws, {"type": "done", "id": turn_id, "text": text, "turns": sessions.turns(session_id), "local": True})


async def toggle_incidents(ws: WebSocket, session_id: str, enabled: bool) -> None:
    """The HUD's on/off button."""
    reply = await (commands.enable(incidents) if enabled else commands.disable(incidents))
    await say(ws, session_id, reply)


async def run_turn(ws: WebSocket, session_id: str, text: str, offer_pending: bool = False) -> None:
    reply = await commands.handle(text, incidents, offer_pending)
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
        async for chunk in agent.stream(sessions.history(session_id), settings.system_prompt):
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
    unsubscribe = incidents.subscribe(lambda status: ws.send_json({"type": "incidents", **status}))
    await ws.send_json({"type": "incidents", **incidents.status()})

    # Greet a new conversation. Not stored in history: agents expect it to start with the user.
    offer_pending = False
    if sessions.turns(session_id) == 0:
        text, offer_pending = commands.greeting(settings.assistant_name, incidents)
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
            elif kind == "incidents":
                await cancel_current()
                current = asyncio.create_task(toggle_incidents(ws, session_id, bool(msg.get("enabled"))))
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
        unsubscribe()
        await cancel_current()
