"""Skills: conversations J.A.R.V.I.G. handles itself when you ask for them, with no HUD controls.

A skill recognises the request that starts it (`wants`), then owns the conversation
until it is done: follow-up questions, a preview, your "yes". Whether a skill is in beta
comes from backend/features.json (see bridge/features.py): beta skills turn the HUD
violet while they run and start every reply with BETA_PREFIX.

Saying "cancel" (or "never mind", "stop that") during a skill ends it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from . import features

BETA_PREFIX = "You've requested a BETA Feature."

_CANCEL = re.compile(r"^\s*(cancel|never ?mind|forget it|stop (that|this)|abort|quit)\b", re.I)


@dataclass
class Reply:
    text: str
    done: bool = False     # the skill has finished; the conversation goes back to normal
    speak: str | None = None
    table: dict | None = None   # {"title", "columns", "rows": [{"cells", "kind", "status"}]}, shown in the HUD


@dataclass
class SkillContext:
    """What skills may use: the settings, and the agent for understanding free text."""
    settings: Any = None
    complete: Callable[[str, str], Awaitable[str]] | None = None   # (prompt, system) -> reply


class Skill:
    """One conversation. A new instance is created each time the skill starts."""

    feature = ""           # its key in backend/features.json, e.g. "timesheet"

    def __init__(self, context: SkillContext | None = None):
        self.context = context or SkillContext()

    @property
    def title(self) -> str:
        """Shown in the HUD tag and in replies, e.g. "Timesheet"."""
        try:
            return features.get(self.feature).title
        except (KeyError, OSError, ValueError):
            return self.feature.replace("_", " ").title() or "Skill"

    @property
    def beta(self) -> bool:
        return features.is_beta(self.feature)

    @classmethod
    def wants(cls, text: str) -> bool:
        """Does this message start the skill?"""
        return False

    async def start(self, text: str) -> Reply:
        raise NotImplementedError

    async def reply(self, text: str) -> Reply:
        """The user's next message while the skill is active."""
        raise NotImplementedError


# Registered skills, checked in order. The timesheet skill is added here when it exists.
SKILLS: list[type[Skill]] = []


class SkillSessions:
    """Which skill, if any, each conversation is in."""

    def __init__(self, context: SkillContext | None = None):
        self._active: dict[str, Skill] = {}
        self.context = context or SkillContext()

    def active(self, session_id: str) -> Skill | None:
        return self._active.get(session_id)

    def end(self, session_id: str) -> Skill | None:
        return self._active.pop(session_id, None)

    async def handle(self, session_id: str, text: str) -> tuple[Skill, Reply] | None:
        """(skill, reply) if a skill takes this message, else None (normal handling)."""
        skill = self._active.get(session_id)
        if skill is not None:
            if _CANCEL.search(text):
                self.end(session_id)
                return skill, self._format(skill, Reply(f"Okay, I've cancelled the {skill.title.lower()} request. "
                                                        "Nothing was changed.", done=True))
            reply = await skill.reply(text)
        else:
            cls = next((s for s in SKILLS if s.wants(text)), None)
            if cls is None:
                return None
            skill = cls(self.context)
            self._active[session_id] = skill
            reply = await skill.start(text)
        if reply.done:
            self.end(session_id)
        return skill, self._format(skill, reply)

    @staticmethod
    def _format(skill: Skill, reply: Reply) -> Reply:
        if not skill.beta:
            return reply
        text = f"{BETA_PREFIX} {reply.text}"
        speak = f"{BETA_PREFIX} {reply.speak}" if reply.speak else None
        return Reply(text, reply.done, speak, reply.table)
