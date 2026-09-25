"""The Timesheet skill (beta): fill the Excel timesheet by describing your month.

    "For this month I worked every day from 8 to 17, on the 13th I had training,
     I was sick on the 17th and 18th and on vacation on the 20th."

1. The AI turns what you said into a structured description (month, normal hours,
   exceptions). Only that sentence, the allowed day types and today's date are sent.
2. connectors.timesheet applies the sheet's rules and shows a day-by-day table.
3. You confirm it or say what to change; corrections rebuild the table.
4. It asks whether to update the file, and saves only after your yes.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from connectors import timesheet as ts

from .skills import Reply, Skill

_TRIGGER = re.compile(
    r"\b(time ?sheet|ficha de tempo|arbeitszeit\w*)\b"
    r"|\b(i )?worked\b.*\bfrom\b.*\d.*\b(to|until|till)\b.*\d", re.I)
_YES = re.compile(r"^\s*(yes|yeah|yep|yup|sure|ok(ay)?|correct|right|perfect|looks (good|right|fine)|"
                  r"that'?s (right|correct|it)|go ahead|do it|save( it)?|update( it)?|confirm(ed)?|sim|ja)\b", re.I)
_NO = re.compile(r"^\s*(no|nope|not yet|wait|hold on|n[aã]o|nein)\s*[.!]*\s*$", re.I)

SYSTEM = """You turn a person's spoken description of their working time into JSON for their timesheet.
Reply with ONLY a JSON object, no prose:
{{"month": <1-12>,
 "work": {{"start": "HH:MM", "end": "HH:MM", "location": <one of the locations, or null>}} or null,
 "days": [{{"day": <day of month>, "type": "<one of the day types, copied exactly>",
           "start": "HH:MM" or null, "end": "HH:MM" or null, "location": <location or null>}}],
 "questions": ["<only if something essential is missing or contradictory>"]}}

Rules:
- "month": the month they mean. "this month" is {this_month}; "last month" is {last_month}.
- "work": their normal working hours when they describe them ("every day from 8 to 17" -> 08:00-17:00).
  "8 to 5" also means 08:00-17:00. null if they only mention specific days.
- "days": ONLY the days that differ from normal, one entry per day ("17th and 18th" -> two entries,
  "from the 3rd to the 5th" -> three). Leave start/end null to use the normal hours.
- Do NOT list weekends or public holidays unless the person mentions them.
- Map words to day types: training/course -> "Treinamento - Training"; sick/ill/doctor ->
  "Baixa Médica - Krankschreibung Arzt"; vacation/holidays they took/days off -> "Férias - Urlaub";
  public/bank holiday -> "Feriado - Feiertag"; worked/work -> "Trabalho - Istarbeit".
- Worked in Germany -> location "Alemanha"; in Portugal -> "Portugal".
- Later statements correct earlier ones.

Today is {today}. Day types: {types}. Locations: {locations}."""


class TimesheetSkill(Skill):
    feature = "timesheet"

    @classmethod
    def wants(cls, text: str) -> bool:
        return bool(_TRIGGER.search(text))

    def __init__(self, context=None):
        super().__init__(context)
        self.statements: list[str] = []
        self.plan: ts.Plan | None = None
        self.stage = "review"          # review -> confirm_save
        self.read_at: float | None = None

    # --- conversation
    async def start(self, text: str) -> Reply:
        self.statements = [text]
        return await self._propose(first=True)

    async def reply(self, text: str) -> Reply:
        if self.plan and not self.plan.questions and _YES.search(text):
            if self.stage == "review":
                self.stage = "confirm_save"
                return Reply(f"Great. Shall I update the timesheet file now? I'll save a backup of it first. "
                             f"({self._path().name})",
                             speak="Great. Shall I update the timesheet file now? I'll keep a backup.")
            return self._save()
        if _NO.search(text):
            self.stage = "review"
            return Reply("Okay, nothing is saved. Tell me what to change, or say cancel.")
        # Anything else is an answer or a correction: rebuild the table with it.
        self.statements.append(text)
        self.stage = "review"
        return await self._propose()

    # --- steps
    def _path(self) -> Path:
        raw = getattr(self.context.settings, "timesheet_path", "") or ""
        if not raw:
            raise ts.TimesheetError("TIMESHEET_PATH is empty in backend/.env.")
        return Path(raw).expanduser()

    async def _propose(self, first: bool = False) -> Reply:
        try:
            path = self._path()
            spec = await self._understand(path)
            month = ts.read_month(path, dt.date.today().year if not spec.get("year") else int(spec["year"]),
                                  int(spec.get("month") or dt.date.today().month))
            lunch = tuple(getattr(self.context.settings, "timesheet_lunch", "12:00-13:00").split("-", 1))
            location = getattr(self.context.settings, "timesheet_location", "Portugal")
            self.plan = ts.build_plan(month, spec, lunch, location)
            self.read_at = path.stat().st_mtime
        except ts.TimesheetError as exc:
            return Reply(f"I can't do that yet: {exc}", done=first)
        except (ValueError, KeyError) as exc:
            return Reply(f"I didn't quite get that ({exc}). Could you say it again, e.g. "
                         "\"this month I worked every day from 8 to 17, and on the 13th I had training\"?")
        summary, say = ts.describe(self.plan)
        grid = ts.table(self.plan)
        if self.plan.questions:
            questions = " ".join(self.plan.questions)
            return Reply(f"Before I fill it in: {questions}\n\nWhat I have so far:\n{summary}",
                         speak=f"Before I fill it in: {questions}", table=grid)
        if not self.plan.changes():
            return Reply(f"{self.plan.month.label} already matches what you said, so there's nothing to update.",
                         done=True, table=grid)
        return Reply(f"Here's what I'd put in {self.plan.month.label}:\n{summary}\n\n"
                     "Does this look right? Say yes, or tell me what to change.",
                     speak=f"{say} Does this look right?", table=grid)

    async def _understand(self, path: Path) -> dict:
        if self.context.complete is None:
            raise ts.TimesheetError("no AI is available to understand the description.")
        today = dt.date.today()
        last = (today.replace(day=1) - dt.timedelta(days=1))
        preview = ts.read_month(path, today.year, today.month)  # for the allowed values only
        system = SYSTEM.format(
            this_month=today.month, last_month=last.month, today=today.isoformat(),
            types=", ".join(f'"{t.strip()}"' for t in preview.day_types),
            locations=", ".join(f'"{loc}"' for loc in preview.locations))
        said = "\n".join(f"{i}. {s}" for i, s in enumerate(self.statements, 1))
        raw = await self.context.complete(f"What the person said (in order):\n{said}", system)
        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            raise ValueError("no JSON in the AI's answer")
        return json.loads(match.group(0))

    def _save(self) -> Reply:
        path = self._path()
        try:
            if self.read_at is not None and path.stat().st_mtime != self.read_at:
                self.stage = "review"
                return Reply("The timesheet changed since I read it. Say the same again and I'll rebuild the "
                             "table from the current file.")
            changed = len(self.plan.changes())
            backup = ts.save(path, self.plan)
        except ts.TimesheetError as exc:
            return Reply(str(exc))
        return Reply(f"Done: I've updated {changed} day{'s' if changed != 1 else ''} in {self.plan.month.label} "
                     f"({path.name}). The backup is {backup.name}. Excel recalculates the totals when you open it.",
                     speak=f"Done. I've updated {changed} days in {self.plan.month.label} and kept a backup.",
                     done=True)
