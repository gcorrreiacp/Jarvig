"""Built-in commands the bridge answers itself, before anything reaches the agent.

Incident analysis, the incident dispatcher, the MR summarizer and the MR reviewer are
switched on and off here rather than by the LLM, so the toggles are instant, work
with every agent provider, and can't be misread.
"""
from __future__ import annotations

import random
import re
from datetime import datetime

from . import features
from .services import PollingService

# Which services a phrase names. "incident" alone means analysis, but not in "incident dispatcher".
_MENTIONS = {
    "analysis": re.compile(r"\b(analysis|analy[sz]ing|alerts?|monitoring)\b|\bincidents?\b(?!\s+dispatch)", re.I),
    "dispatcher": re.compile(r"\b(dispatch(er|ing)?|ftp)\b", re.I),
    "summarizer": re.compile(r"\bsummar(y|ies|i[sz]er|i[sz]ing|i[sz]es?)\b", re.I),
    "reviewer": re.compile(r"\breview(ers?|s|ing)?\b|\bcorrections?\b", re.I),
}
_MR = ("summarizer", "reviewer")
# "turn off the MR features": a pull request mention without saying which feature means both.
_MR_ANY = re.compile(r"\b(mrs?|merge requests?|pull requests?|prs?)\b", re.I)
_BOTH = re.compile(r"\bboth\b", re.I)               # "start both": the incident pair, unless MRs are named
_EVERY = re.compile(r"\b(all|everything)\b", re.I)  # "stop everything": all four
_ACTIONS = {                                                      # checked in this order
    "disable": re.compile(r"\b(disable|stop|turn off|switch off|deactivate|pause|end|shut down)\b|\boff\b\s*[.!]*$", re.I),
    "enable": re.compile(r"\b(enable|start|turn on|switch on|activate|resume|begin)\b|\bon\b\s*[.!]*$", re.I),
    "status": re.compile(r"\b(status|state|running|active|enabled)\b|\bon\?", re.I),
}
# "start the dispatcher and incident analysis" -> two clauses; a clause with no verb reuses the last one.
_CLAUSES = re.compile(r",|;|\band\b|\bbut\b|\bthen\b|\balso\b", re.I)
_NEGATED = re.compile(r"^\s*(don'?t|do not|never)\b", re.I)
_ORDER = ("analysis", "dispatcher", "summarizer", "reviewer")
# "which features are in beta?", "what's still in beta", "list the beta features"
_BETA_QUESTION = re.compile(r"\b(which|what|list|show|any)\b.*\bbeta\b|\bbeta\b.*\b(features?|skills?)\b.*\?", re.I)


def parse(text: str) -> list[tuple[str, str]]:
    """The actions a sentence asks for, e.g. [("enable", "dispatcher"), ("enable", "analysis")]."""
    actions: list[tuple[str, str]] = []
    verb = None
    for clause in (c.strip() for c in _CLAUSES.split(text)):
        if not clause:
            continue
        targets = [key for key in _ORDER if _MENTIONS[key].search(clause)]
        if _MR_ANY.search(clause) and not any(key in targets for key in _MR):
            targets += list(_MR)
        if not targets and _EVERY.search(clause):
            targets = list(_ORDER)
        elif not targets and _BOTH.search(clause):
            targets = ["analysis", "dispatcher"]
        if _NEGATED.search(clause):
            verb = None
            continue
        clause_verb = next((name for name, pattern in _ACTIONS.items() if pattern.search(clause)), None)
        verb = clause_verb or verb
        if not targets:
            continue
        if verb:
            actions += [(verb, key) for key in targets]
    return list(dict.fromkeys(actions))


# What each service does, for its replies.
_ON_TEXT = {
    "analysis": ("I'm checking your inbox every {poll} seconds: production errors go to candidates, "
                 "reports to analyzed, and everything else to discarded."),
    "dispatcher": ("Every {poll} seconds I'll upload new candidates to {target} as text files "
                   "and move them from candidates to dispatched."),
    "summarizer": ("Every {poll} seconds I'll check the open pull requests of {target}: any without my summary "
                   "gets a summary comment, and I update it when new commits arrive."),
    "reviewer": ("Every {poll} seconds I'll check the open pull requests of {target}: any I haven't reviewed gets "
                 "comments on the lines that need changing, or a review saying no problems were found. "
                 "New commits get a review of just the new changes."),
}
_PHRASE = {"analysis": "incident analysis", "dispatcher": "incident dispatcher",
           "summarizer": "MR summarizer", "reviewer": "MR reviewer"}
_HANDLED = {"summarizer": "summarized", "reviewer": "reviewed"}
_COUNT_NAMES = {"candidate": "candidates", "tidied": "already-dispatched removed from candidates"}


# ---- Private mode / standard mode (which AI answers)
_M_OFF = re.compile(
    r"\b(disable|turn off|switch off|exit|leave|stop|end|deactivate|quit)\b[^.?!]*\bprivate( mode)?\b"
    r"|\b(switch|go|change|move|back|return|get back)\b[^.?!]*\b(standard( mode)?|claude|the cloud|cloud mode)\b"
    r"|^\s*(standard mode|back to claude)\s*[.!]*\s*$", re.I)
_M_ON = re.compile(
    r"\b(switch|go|change|move|turn on|enable|activate|start|enter)\b[^.?!]*\b(private( mode)?|local( model| mode)?)\b"
    r"|^\s*(private mode|go private|go local)( on| please)?\s*[.!]*\s*$", re.I)
_M_STATUS = re.compile(
    r"\b(are you|am i|are we)\b[^.?!]*\b(in )?(private|standard) mode\b"
    r"|\b(which|what)\b[^.?!]*\b(model|brain|llm|ai)\b[^.?!]*\b(are you|am i|is (this|it)|using|running)\b", re.I)


def parse_mode(text: str) -> str | None:
    """"private", "standard", "status", or None when the text isn't about the mode."""
    if _NEGATED.search(text):
        return None
    if _M_OFF.search(text):
        return "standard"
    if _M_ON.search(text):
        return "private"
    if _M_STATUS.search(text):
        return "status"
    return None


# Opening lines for a new conversation: a welcome and an invitation, never a feature pitch.
# "{name}" is the assistant's name and "{part}" the time of day.
GREETINGS = [
    "Good {part}. What would you like to do?",
    "Hello. What can I do for you today?",
    "{name} online. What would you like to know?",
    "Good {part}. Where shall we start?",
    "At your service. What's on your mind?",
    "Hello again. What would you like to work on?",
    "Ready when you are. What do you need?",
    "Good {part}. Is there anything you'd like to know, or shall we get to work?",
    "Systems are up. How can I help?",
    "Welcome back. What would you like to do first?",
]
_last_greeting: str | None = None


def greeting(name: str, now: datetime | None = None) -> str:
    """A random welcome for the HUD's first line, different from the previous one."""
    global _last_greeting
    hour = (now or datetime.now()).hour
    part = "morning" if 5 <= hour < 12 else "afternoon" if 12 <= hour < 18 else "evening"
    options = [g for g in GREETINGS if g != _last_greeting] or GREETINGS
    _last_greeting = random.choice(options)
    return _last_greeting.format(name=name, part=part)


async def handle(text: str, services: dict[str, PollingService]) -> str | None:
    """The reply if `text` is a built-in command, else None (the agent answers)."""
    if _BETA_QUESTION.search(text):
        return describe_beta()
    actions = parse(text)
    if not actions:
        return None
    run = {"enable": enable, "disable": disable}
    replies = []
    for verb, key in actions:
        replies.append(describe(services[key]) if verb == "status" else await run[verb](services[key]))
    return " ".join(replies)


async def enable(service: PollingService) -> str:
    if service.enabled:
        return f"{service.title} is already running. " + _totals_sentence(service)
    try:
        await service.start()
    except RuntimeError as exc:
        return f"I couldn't start {_PHRASE[service.key]}: {exc}"
    status = service.status()
    reply = f"{service.title} is on. " + _ON_TEXT[service.key].format(poll=status["poll_seconds"], target=_target(service))
    reply += f" Say \"turn off {_PHRASE[service.key]}\" to stop."
    if status["dry_run"]:
        reply += " Note: dry run is on in alert_filters.json, so I'm only logging, not moving emails."
    return reply


async def disable(service: PollingService) -> str:
    if not service.enabled:
        return f"{service.title} is already off."
    await service.stop()
    return f"{service.title} is off. Say \"enable {_PHRASE[service.key]}\" to switch it back on."


def describe(service: PollingService) -> str:
    status = service.status()
    if not status["enabled"]:
        text = f"{service.title} is off."
        if status["last_error"]:
            text += f" Last problem: {status['last_error']}"
        return text
    text = f"{service.title} is on. " + _totals_sentence(service)
    if status["last_run"]:
        text += f" Last check at {datetime.fromisoformat(status['last_run']).astimezone():%H:%M}."
    if status["last_error"]:
        text += f" Last problem: {status['last_error']}"
    return text


def describe_beta() -> str:
    """Which features are in beta, and what has to be true before each one leaves it."""
    try:
        beta = features.in_beta()
    except (OSError, ValueError) as exc:
        return f"I couldn't read the feature list: {exc}"
    if not beta:
        return "No features are in beta: everything is stable."
    parts = []
    for f in beta:
        since = f" since {datetime.fromisoformat(f.since):%-d %B}" if f.since else ""
        part = f"{f.title} ({f.kind}{since})"
        if f.ready_when:
            part += f". It's ready when: {f.ready_when.rstrip('.')}"
        parts.append(part)
    return f"{len(beta)} feature{'s are' if len(beta) != 1 else ' is'} in beta: " + "; ".join(parts) + \
        ". Everything else is stable."


def _target(service: PollingService) -> str:
    github = getattr(service.job, "github", None)
    if github is not None:
        return github.repo
    ftp = getattr(service.job, "ftp", None)
    return f"ftp://{ftp.host}{ftp.directory}" if ftp else "the FTP server"


def _totals_sentence(service: PollingService) -> str:
    totals = service.totals
    if not any(totals.values()):
        if service.key in _HANDLED:
            return "No new pull requests so far."
        return "Nothing new so far." if service.key == "dispatcher" else "No new alerts sorted yet."
    names = {**_COUNT_NAMES, "handled": _HANDLED.get(service.key, "handled")}
    parts = [f"{count} {names.get(name, name)}" for name, count in totals.items() if count]
    return "So far: " + ", ".join(parts) + "."
