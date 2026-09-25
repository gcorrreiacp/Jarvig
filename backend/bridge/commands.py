"""Built-in commands the bridge answers itself, before anything reaches the agent.

Incident analysis is switched on and off here rather than by the LLM, so the
toggle is instant, works with every agent provider, and can't be misread.
"""
from __future__ import annotations

import re
from datetime import datetime

from .incidents import IncidentAnalysis

_TOPIC = r"(incident|analysis|alert|monitoring)"
_NEGATED = re.compile(r"^\s*(don'?t|do not|never)\b", re.I)
_YES = re.compile(
    r"^\s*(yes|yeah|yep|yup|sure|ok(ay)?|please|go ahead|do it|affirmative|absolutely|of course|"
    r"enable( it)?|turn it on|switch it on|start( it)?|sim|claro|ja)\b", re.I)
_NO = re.compile(r"^\s*(no|nope|nah|not now|not yet|later|no thanks|n[aã]o|nein)\b", re.I)
_DISABLE = re.compile(rf"\b(disable|stop|turn off|switch off|deactivate|pause|end)\b.*{_TOPIC}|{_TOPIC}.*\boff\b", re.I)
_ENABLE = re.compile(rf"\b(enable|start|turn on|switch on|activate|resume|begin)\b.*{_TOPIC}|{_TOPIC}.*\bon\b\s*[.!]*$", re.I)
_STATUS = re.compile(rf"{_TOPIC}.*\b(status|running|active|enabled|on\?)|\b(status|state)\b.*{_TOPIC}", re.I)


def greeting(name: str, incidents: IncidentAnalysis) -> tuple[str, bool]:
    """(text, is_asking): the HUD's opening line and whether it offers incident analysis."""
    end = "" if name.endswith(".") else "."  # "J.A.R.V.I.G." already ends a sentence
    if incidents.enabled:
        return f"Welcome back. {name} online. Incident analysis is running.".replace("..", "."), False
    return (
        f"Hello, I'm {name}{end} Would you like me to enable incident analysis? "
        "I'll watch your Gmail for alerts: production errors are queued for analysis, "
        "reports go to analyzed, and the rest is discarded.",
        True,
    )


async def handle(text: str, incidents: IncidentAnalysis, offer_pending: bool) -> str | None:
    """The reply if `text` is a built-in command, else None (the agent answers)."""
    if offer_pending:
        if _NO.search(text) or _NEGATED.search(text):
            return "Understood, incident analysis stays off. Just say \"enable incident analysis\" if you change your mind."
        if _YES.search(text):
            return await enable(incidents)

    if _NEGATED.search(text):
        return None
    if _DISABLE.search(text):
        return await disable(incidents)
    if _ENABLE.search(text):
        return await enable(incidents)
    if _STATUS.search(text):
        return describe(incidents)
    return None


async def enable(incidents: IncidentAnalysis) -> str:
    if incidents.enabled:
        return "Incident analysis is already running. " + _totals_sentence(incidents)
    try:
        await incidents.start()
    except RuntimeError as exc:
        return f"I couldn't start incident analysis: {exc}"
    status = incidents.status()
    reply = (
        f"Incident analysis is on. I'm checking your inbox every {status['poll_seconds']} seconds: "
        "production errors go to candidates, reports to analyzed, and everything else to discarded. "
        "Say \"turn off incident analysis\" to stop."
    )
    if status["dry_run"]:
        reply += " Note: dry run is on in alert_filters.json, so I'm only logging, not moving emails."
    return reply


async def disable(incidents: IncidentAnalysis) -> str:
    if not incidents.enabled:
        return "Incident analysis is already off."
    await incidents.stop()
    return "Incident analysis is off. Your inbox won't be sorted until you say \"enable incident analysis\"."


def describe(incidents: IncidentAnalysis) -> str:
    status = incidents.status()
    if not status["enabled"]:
        text = "Incident analysis is off."
        if status["last_error"]:
            text += f" Last problem: {status['last_error']}"
        return text
    text = "Incident analysis is on. " + _totals_sentence(incidents)
    if status["last_run"]:
        text += f" Last check at {datetime.fromisoformat(status['last_run']).astimezone():%H:%M}."
    if status["last_error"]:
        text += f" Last problem: {status['last_error']}"
    return text


def _totals_sentence(incidents: IncidentAnalysis) -> str:
    totals = incidents.totals
    if not any(totals.values()):
        return "No new alerts sorted yet."
    names = {"candidate": "candidates"}
    parts = [f"{count} {names.get(name, name)}" for name, count in totals.items() if count]
    return "Sorted so far: " + ", ".join(parts) + "."
