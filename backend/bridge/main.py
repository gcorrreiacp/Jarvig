"""FastAPI bridge between the HUD frontend and the configured agent.

WebSocket protocol (/ws?session=<id>):
  client -> server
    {"type": "user_message", "text": "..."}
    {"type": "cancel"}                 stop the reply in progress
    {"type": "reset"}                  clear this session's history
    {"type": "ping", "t": <ms>}        latency probe
    {"type": "toggle", "service": "analysis" | "dispatcher" | "summarizer" | "reviewer", "enabled": bool}
  server -> client
    {"type": "hello", assistant, agent, session_id, turns, tts}
    {"type": "mode", beta, skill, brain: {mode, provider, model}, agent}   beta skill or private mode on/off
    {"type": "service", service, enabled, dry_run, poll_seconds, last_run, last_error, totals}
    {"type": "start", id}
    {"type": "token", id, text}
    {"type": "done", id, text, first_token_ms, total_ms, turns}   (+ "speak", "local", "table" on bridge-written replies)
    {"type": "cancelled", id} | {"type": "error", id, message}
    {"type": "pong", t} | {"type": "reset_ok"}
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager, suppress

import httpx
from fastapi import FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agent import Agent, AgentError, Message, build_agent

from . import commands, features
from .brains import BrainError, Brains
from .config import get_settings
from .services import PollingService
from .sessions import SessionStore
from .skills import BETA_PREFIX, SKILLS, SkillContext, SkillSessions
from .timesheet_skill import TimesheetSkill

log = logging.getLogger("bridge")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
settings = get_settings()
sessions = SessionStore(settings.history_limit)


async def _skill_complete(prompt: str, system: str) -> str:
    return await brains.current.complete([Message("user", prompt)], system)


SKILLS.append(TimesheetSkill)
skills = SkillSessions(SkillContext(settings=settings, complete=_skill_complete))
brains = Brains(settings)
huds: dict[WebSocket, str] = {}   # every open HUD and its conversation, to announce mode changes


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
    agent = brains.current  # private mode applies to the background features too
    return asyncio.run_coroutine_threadsafe(agent.complete([Message("user", prompt)], system), _loop).result(timeout=900)


def _pr_builder(mode: str):
    def build():
        from connectors.github_prs import build_from_settings
        def agent_name() -> str:  # read at posting time: the mode may change while the feature runs
            info = brains.current.info()
            return f"{info['provider']}/{info['model']}"
        return build_from_settings(mode, settings, _complete, agent_name=agent_name)
    return build


def _pr_errors() -> tuple[type[Exception], ...]:
    from connectors.github_prs import PullRequestError
    return (PullRequestError,)


def system_prompt() -> str:
    """The configured prompt plus the latest pull request summaries/reviews, so the user can ask about them."""
    context = []
    try:
        from connectors.github_prs import PullRequestJob
        for mode in ("summarizer", "reviewer"):
            job = PullRequestJob(mode, github=None, complete=None)  # only reads its saved state
            context += [r.as_context() for r in job.recent(2)]
    except Exception:
        # Extra context is optional: never let it stop a normal chat reply.
        log.exception("Couldn't load recent pull request results for the prompt")
    if not context:
        return settings.system_prompt
    return (settings.system_prompt + "\n\nRecent pull request summaries and reviews you produced "
            "(use them if the user asks about pull requests):\n\n" + "\n\n".join(context))


services = {
    "analysis": PollingService("analysis", "Incident analysis", _build_watcher, feature="incident_analysis"),
    "dispatcher": PollingService("dispatcher", "Incident dispatcher", _build_dispatcher, fatal=_dispatch_errors(),
                                 feature="incident_dispatcher"),
    "summarizer": PollingService("summarizer", "MR summarizer", _pr_builder("summarizer"), fatal=_pr_errors(),
                                 feature="mr_summarizer"),
    "reviewer": PollingService("reviewer", "MR reviewer", _pr_builder("reviewer"), fatal=_pr_errors(),
                               feature="mr_reviewer"),
}


_loop: asyncio.AbstractEventLoop | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _loop
    _loop = asyncio.get_running_loop()
    await brains.startup()
    log.info("Agent ready: %s", brains.current.info())
    yield
    for service in services.values():
        await service.stop()
    await brains.shutdown()


app = FastAPI(title=f"{settings.assistant_name} bridge", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def agent_of(app_) -> Agent:
    return brains.current


# ----------------------------- REST -----------------------------
class ChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    session_id: str = "default"


@app.get("/api/health")
async def health():
    return {"ok": True}


@app.get("/api/agent")
async def agent_info():
    return {"assistant": settings.assistant_name, "mode": brains.mode, **agent_of(app).info()}


class BrainSwitch(BaseModel):
    mode: str = Field(pattern="^(private|standard)$")


@app.post("/api/brain")
async def brain_switch(req: BrainSwitch):
    """Switch between standard mode and private mode (the local model), like saying it."""
    reply = await switch_brain(req.mode)
    await broadcast_mode()
    return {"reply": reply, **brains.info()}


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


@app.get("/api/features")
async def features_list():
    """Every feature and whether it is in beta (from backend/features.json)."""
    from .features import load
    return [f.__dict__ for f in load().values()]


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


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


@app.post("/api/tts")
async def tts(req: TTSRequest):
    """Speech for the assistant's voice: POST {text} -> audio/mpeg. Keeps the ElevenLabs key off the browser."""
    if not settings.elevenlabs_api_key:
        raise HTTPException(status_code=404, detail="TTS is not configured (set ELEVENLABS_API_KEY)")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{settings.elevenlabs_voice_id}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            res = await client.post(
                url,
                params={"output_format": "mp3_44100_128"},
                headers={"xi-api-key": settings.elevenlabs_api_key, "Accept": "audio/mpeg"},
                json={
                    "text": req.text,
                    "model_id": settings.elevenlabs_model,
                    "voice_settings": {"stability": 0.6, "similarity_boost": 0.8, "style": 0.2},
                },
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"TTS unreachable: {exc}")
    if res.status_code != 200:
        raise HTTPException(status_code=502, detail=f"TTS failed ({res.status_code}): {res.text[:200]}")
    return Response(content=res.content, media_type="audio/mpeg")


@app.post("/api/sessions/{session_id}/reset")
async def reset(session_id: str):
    sessions.reset(session_id)
    skills.end(session_id)
    return {"ok": True}


# --------------------------- WebSocket ---------------------------
async def safe_send(ws: WebSocket, payload: dict) -> None:
    with suppress(Exception):
        await ws.send_json(payload)


async def say(ws: WebSocket, session_id: str, text: str, speak: str | None = None, table: dict | None = None) -> None:
    """Send a reply the bridge wrote itself, shaped like an agent turn so the HUD shows and speaks it.
    `speak` is read aloud instead of `text` when the full text is too long to listen to."""
    turn_id = uuid.uuid4().hex[:12]
    await safe_send(ws, {"type": "start", "id": turn_id})
    await safe_send(ws, {"type": "token", "id": turn_id, "text": text})
    done = {"type": "done", "id": turn_id, "text": text, "speak": speak or text,
            "turns": sessions.turns(session_id), "local": True}
    if table:
        done["table"] = table
    await safe_send(ws, done)


async def toggle_service(ws: WebSocket, session_id: str, key: str, enabled: bool) -> None:
    """The HUD's on/off buttons."""
    await say(ws, session_id, await _toggle(key, enabled))


async def send_mode(ws: WebSocket, session_id: str) -> None:
    """Tell the HUD whether it should be in beta colours (a beta skill in this conversation, or
    private mode while it is beta) and which AI is answering."""
    skill = skills.active(session_id)
    skill_beta = bool(skill and skill.beta)
    private_beta = brains.mode == "private" and features.is_beta("private_mode")
    await safe_send(ws, {"type": "mode", "beta": skill_beta or private_beta,
                         "skill": skill.title if skill_beta else None,
                         "brain": brains.info(), "agent": brains.current.info()})


async def broadcast_mode() -> None:
    """Private/standard mode is shared by every open HUD."""
    for ws, session_id in list(huds.items()):
        await send_mode(ws, session_id)


def _standard_name() -> str:
    provider = brains.standard.info().get("provider", "")
    return "Claude" if provider in ("claude_code", "anthropic") else provider


async def switch_brain(mode: str) -> str:
    """Do a spoken "private mode" / "standard mode" / "which model?" and return the reply."""
    model = brains.local_model
    if mode == "status":
        if brains.mode == "private":
            return f"I'm in private mode, running {model} on this Mac. Say \"standard mode\" to go back to {_standard_name()}."
        return f"I'm in standard mode, running on {_standard_name()}. Say \"private mode\" to run on this Mac instead."
    if mode == "private":
        if brains.mode == "private":
            return f"I'm already in private mode, running {model} on this Mac."
        try:
            await brains.to_private()
        except BrainError as exc:
            return f"I can't switch to private mode: {exc} I'm staying on {_standard_name()}."
        prefix = f"{BETA_PREFIX} " if features.is_beta("private_mode") else ""
        return (f"{prefix}Private mode: I'm running on {model}, and your conversation stays on this Mac. "
                "Chat, skills and the pull request features all use it until you say \"standard mode\".")
    if brains.mode == "standard":
        return f"I'm already in standard mode, on {_standard_name()}."
    await brains.to_standard()
    return f"Standard mode: back on {_standard_name()}."


async def fall_back(exc: Exception) -> str | None:
    """Standard agent failed: switch to private mode by itself. Returns the notice, or None if it can't."""
    try:
        await brains.to_private()
    except BrainError as err:
        log.warning("Fallback to private mode failed: %s", err)
        return None
    await broadcast_mode()
    reason = str(exc).strip().splitlines()[0][:140]
    return (f"({_standard_name()} isn't available right now: {reason}. I've switched to private mode, a BETA "
            f"feature, and I'm answering with {brains.local_model} on this Mac. Say \"standard mode\" to go back.)\n\n")


async def run_turn(ws: WebSocket, session_id: str, text: str) -> None:
    # "Switch to private mode" / "standard mode" first: it must work even when the AI doesn't.
    mode = commands.parse_mode(text)
    if mode:
        reply = await switch_brain(mode)
        sessions.append(session_id, Message("user", text))
        sessions.append(session_id, Message("assistant", reply))
        await broadcast_mode()
        await say(ws, session_id, reply)
        return

    # A skill in progress (or starting) gets the message first: it asks and confirms by conversation.
    was_active = skills.active(session_id) is not None
    handled = await skills.handle(session_id, text)
    if handled is not None:
        skill, skill_reply = handled
        sessions.append(session_id, Message("user", text))
        sessions.append(session_id, Message("assistant", skill_reply.text))
        if not was_active:
            await send_mode(ws, session_id)   # started: turn violet before the first reply
        await say(ws, session_id, skill_reply.text, skill_reply.speak, skill_reply.table)
        if skill_reply.done:
            await send_mode(ws, session_id)   # finished: back to normal
        return

    reply = await commands.handle(text, services)
    if reply is not None:
        sessions.append(session_id, Message("user", text))
        sessions.append(session_id, Message("assistant", reply))
        await say(ws, session_id, reply)
        return

    turn_id = uuid.uuid4().hex[:12]
    sessions.append(session_id, Message("user", text))
    await safe_send(ws, {"type": "start", "id": turn_id})

    started = time.perf_counter()
    first_token_ms: int | None = None
    parts: list[str] = []
    for attempt in range(2):
        agent = brains.current
        try:
            async for chunk in agent.stream(sessions.history(session_id), system_prompt()):
                if not chunk:
                    continue
                if first_token_ms is None:
                    first_token_ms = int((time.perf_counter() - started) * 1000)
                parts.append(chunk)
                await ws.send_json({"type": "token", "id": turn_id, "text": chunk})
            break
        except asyncio.CancelledError:
            if parts:
                sessions.append(session_id, Message("assistant", "".join(parts) + " [interrupted]"))
            await safe_send(ws, {"type": "cancelled", "id": turn_id})
            raise
        except AgentError as exc:
            # Standard agent failed before answering: fall back to private mode and answer locally.
            if attempt == 0 and not parts and brains.mode == "standard":
                notice = await fall_back(exc)
                if notice:
                    parts.append(notice)
                    await safe_send(ws, {"type": "token", "id": turn_id, "text": notice})
                    continue
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
    huds[ws] = session_id
    await ws.send_json({
        "type": "hello",
        "assistant": settings.assistant_name,
        "agent": agent_of(ws.app).info(),
        "session_id": session_id,
        "turns": sessions.turns(session_id),
        "tts": bool(settings.elevenlabs_api_key),
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
    await send_mode(ws, session_id)  # still violet after a page reload in the middle of a beta skill

    # Greet a new conversation. Not stored in history: agents expect it to start with the user.
    if sessions.turns(session_id) == 0:
        text = commands.greeting(settings.assistant_name)
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
                current = asyncio.create_task(run_turn(ws, session_id, text[:8000]))
            elif kind == "toggle" and msg.get("service") in services:
                await cancel_current()
                current = asyncio.create_task(
                    toggle_service(ws, session_id, msg["service"], bool(msg.get("enabled")))
                )
            elif kind == "cancel":
                await cancel_current()
            elif kind == "reset":
                await cancel_current()
                sessions.reset(session_id)
                if skills.end(session_id):
                    await send_mode(ws, session_id)
                await ws.send_json({"type": "reset_ok"})
    except WebSocketDisconnect:
        pass
    finally:
        huds.pop(ws, None)
        for unsubscribe in unsubscribers:
            unsubscribe()
        await cancel_current()
