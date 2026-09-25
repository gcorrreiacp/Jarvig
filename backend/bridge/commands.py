"""Built-in commands the bridge answers itself, before anything reaches the agent.

Incident analysis, the incident dispatcher, the MR summarizer and the MR reviewer are
switched on and off here rather than by the LLM, so the toggles are instant, work
with every agent provider, and can't be misread.
"""
from __future__ import annotations

import re
from datetime import datetime

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
_YES = re.compile(
    r"^\s*(yes|yeah|yep|yup|sure|ok(ay)?|please|go ahead|do it|affirmative|absolutely|of course|"
    r"enable( it)?|turn it on|switch it on|start( it)?|sim|claro|ja)\b", re.I)
_NO = re.compile(r"^\s*(no|nope|nah|not now|not yet|later|no thanks|n[aã]o|nein)\b", re.I)
_ORDER = ("analysis", "dispatcher", "summarizer", "reviewer")


def parse(text: str, offer_pending: bool = False) -> tuple[list[tuple[str, str]], bool]:
    """(actions, declined): e.g. ([("enable", "dispatcher"), ("enable", "analysis")], False).

    `declined` is True when the text answers the greeting's offer with no.
    """
    actions: list[tuple[str, str]] = []
    declined = False
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
            declined = declined or (offer_pending and not targets)
            verb = None
            continue
        clause_verb = next((name for name, pattern in _ACTIONS.items() if pattern.search(clause)), None)
        verb = clause_verb or verb
        if not targets:
            # A bare "yes" / "no" (or "enable it") answers the greeting's question about incident analysis.
            if offer_pending and _NO.search(clause):
                declined = True
            elif offer_pending and _YES.search(clause):
                actions.append(("enable", "analysis"))
            continue
        if verb:
            actions += [(verb, key) for key in targets]
    return list(dict.fromkeys(actions)), declined and not actions


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


def greeting(name: str, analysis: PollingService) -> tuple[str, bool]:
    """(text, is_asking): the HUD's opening line and whether it offers incident analysis."""
    end = "" if name.endswith(".") else "."  # "J.A.R.V.I.G." already ends a sentence
    if analysis.enabled:
        return f"Welcome back. {name} online. Incident analysis is running.".replace("..", "."), False
    return (
        f"Hello, I'm {name}{end} Would you like me to enable incident analysis? "
        "I'll watch your Gmail for alerts: production errors are queued for analysis, "
        "reports go to analyzed, and the rest is discarded.",
        True,
    )


async def handle(text: str, services: dict[str, PollingService], offer_pending: bool) -> str | None:
    """The reply if `text` is a built-in command, else None (the agent answers)."""
    actions, declined = parse(text, offer_pending)
    if declined:
        return "Understood, incident analysis stays off. Just say \"enable incident analysis\" if you change your mind."
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
