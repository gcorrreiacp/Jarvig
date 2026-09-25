"""Watches Gmail for alert emails and sorts them into analysis candidates or discarded.

Every poll it looks for new emails matching `watch_query` (emails that don't match
are never touched), then applies the rules in alert_filters.json, first match wins:
  - a `folders` entry matches       -> moved to that folder (e.g. "analyzed" reports);
                                       these are never discarded
  - discard_if_any matches          -> discarded
  - candidate_if_any matches        -> candidate
  - otherwise                       -> discarded

Candidates stay in the inbox, get the "candidates" label and are appended to
data/candidates.jsonl for the analysis step. Folder and discarded emails get their
label and leave the inbox, which is how Gmail moves a message into a folder.

Setup:
    python -m connectors.gmail auth --modify
    cp alert_filters.example.json alert_filters.json    # then edit the rules

Usage (from backend/):
    python -m connectors.gmail_watcher watch            # keep listening
    python -m connectors.gmail_watcher once             # one pass, e.g. from cron
    python -m connectors.gmail_watcher test <message_id>   # show the decision, change nothing
Add --dry-run to log decisions without touching Gmail. "dry_run": true in the
filters file does the same until you switch it off.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from googleapiclient.errors import HttpError

from .gmail import MODIFY_SCOPES, Email, GmailConnector, _backend_path

log = logging.getLogger("gmail_watcher")

FILTERS_FILE = _backend_path(os.getenv("GMAIL_FILTERS_FILE", "alert_filters.json"))
CANDIDATES_FILE = _backend_path(os.getenv("GMAIL_CANDIDATES_FILE", "data/candidates.jsonl"))

_TEXT_FIELDS = ("from", "to", "subject", "body")
CANDIDATE, DISCARDED = "candidate", "discarded"
_RATE_LIMIT_REASONS = {"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"}


def is_rate_limit(exc: HttpError) -> bool:
    """A 403/429 that means "slow down", as opposed to a missing permission."""
    if exc.resp.status == 429:
        return True
    try:
        details = json.loads(exc.content.decode())["error"].get("errors", [])
    except (ValueError, KeyError, AttributeError):
        return False
    return any(d.get("reason") in _RATE_LIMIT_REASONS for d in details)


@dataclass
class Filters:
    watch_query: str
    candidate_if_any: list[dict] = field(default_factory=list)
    discard_if_any: list[dict] = field(default_factory=list)
    lookback: str = "1d"
    poll_seconds: int = 60
    candidate_label: str = "candidates"
    discarded_label: str = "discarded"
    dry_run: bool = False
    # [{"label": "analyzed", "if_any": [rule, ...]}]: checked before any discard rule.
    folders: list[dict] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path = FILTERS_FILE) -> "Filters":
        if not path.exists():
            raise FileNotFoundError(
                f"{path} not found. Create it with: cp alert_filters.example.json alert_filters.json"
            )
        raw = json.loads(path.read_text())
        labels = raw.get("labels", {})
        filters = cls(
            watch_query=raw["watch_query"],
            candidate_if_any=raw.get("candidate_if_any", []),
            discard_if_any=raw.get("discard_if_any", []),
            lookback=raw.get("lookback", "1d"),
            poll_seconds=int(raw.get("poll_seconds", 60)),
            candidate_label=labels.get("candidate", "candidates"),
            discarded_label=labels.get("discarded", "discarded"),
            dry_run=bool(raw.get("dry_run", False)),
            folders=raw.get("folders", []),
        )
        folder_rules = []
        for folder in filters.folders:
            if not folder.get("label") or not folder.get("if_any"):
                raise ValueError(f'Each folder needs "label" and "if_any": {folder}')
            folder_rules += folder["if_any"]
        for rule in filters.candidate_if_any + filters.discard_if_any + folder_rules:
            _validate_rule(rule)
        return filters

    def labels(self) -> list[str]:
        return [self.candidate_label, self.discarded_label] + [f["label"] for f in self.folders]

    def query(self) -> str:
        """New alert emails that have not been sorted yet."""
        skip = " ".join(f"-label:{_query_label(n)}" for n in self.labels())
        return f"({self.watch_query}) newer_than:{self.lookback} {skip}"


def _query_label(name: str) -> str:
    # Gmail search writes spaces and slashes in label names as hyphens.
    return re.sub(r"[\s/]+", "-", name)


def _validate_rule(rule: dict) -> None:
    known = set(_TEXT_FIELDS) | {"subject_regex", "body_regex", "has_attachment"}
    unknown = set(rule) - known
    if unknown:
        raise ValueError(f"Unknown rule field(s) {sorted(unknown)} in {rule}. Allowed: {sorted(known)}")
    for key in ("subject_regex", "body_regex"):
        if key in rule:
            re.compile(rule[key])


def _field_text(email: Email, name: str) -> str:
    return {"from": email.sender, "to": f"{email.to} {email.cc}", "subject": email.subject, "body": email.body}[name]


def rule_matches(rule: dict, email: Email) -> bool:
    """Every field in the rule must match; a list of words inside a field matches if any word is found."""
    for name in _TEXT_FIELDS:
        if name in rule:
            words = rule[name] if isinstance(rule[name], list) else [rule[name]]
            text = _field_text(email, name).lower()
            if not any(str(w).lower() in text for w in words):
                return False
    if "subject_regex" in rule and not re.search(rule["subject_regex"], email.subject, re.I):
        return False
    if "body_regex" in rule and not re.search(rule["body_regex"], email.body, re.I | re.M):
        return False
    if "has_attachment" in rule and bool(email.attachments) != bool(rule["has_attachment"]):
        return False
    return True


def classify(email: Email, filters: Filters) -> tuple[str, str]:
    """(destination, reason); destination is CANDIDATE, DISCARDED or a folder label."""
    for folder in filters.folders:
        for rule in folder["if_any"]:
            if rule_matches(rule, email):
                return folder["label"], f"folder '{folder['label']}' rule {rule}"
    for i, rule in enumerate(filters.discard_if_any):
        if rule_matches(rule, email):
            return DISCARDED, f"discard rule #{i + 1} {rule}"
    for i, rule in enumerate(filters.candidate_if_any):
        if rule_matches(rule, email):
            return CANDIDATE, f"candidate rule #{i + 1} {rule}"
    return DISCARDED, "no candidate rule matched"


def append_candidate(email: Email, reason: str, path: Path = CANDIDATES_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "sorted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reason": reason,
        **{k: v for k, v in email.as_dict().items() if k != "html"},
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


class AlertWatcher:
    def __init__(
        self,
        filters: Filters,
        gmail: GmailConnector | None = None,
        dry_run: bool = False,
        on_candidate: Callable[[Email, str], None] = append_candidate,
    ):
        self.filters = filters
        self.dry_run = dry_run or filters.dry_run
        self.gmail = gmail or GmailConnector(scopes=MODIFY_SCOPES)
        self.on_candidate = on_candidate
        # A dry run labels nothing, so the same emails keep matching; remember them instead.
        self._seen: set[str] = set()

    def run_once(self) -> dict:
        """Sort every unsorted alert email once. Returns counts."""
        ids = [i for i in self.gmail.list_ids(self.filters.query()) if i not in self._seen]
        sorted_: dict[str, list[tuple[Email, str]]] = {}
        for message_id in reversed(ids):  # oldest first, so the queue file stays in arrival order
            email = self.gmail.read(message_id)
            destination, reason = classify(email, self.filters)
            log.info("%-10s %s | %s | %s | %s", destination.upper(), message_id, email.sender[:40], email.subject[:60], reason)
            sorted_.setdefault(destination, []).append((email, reason))

        if self.dry_run:
            self._seen.update(ids)
        else:
            for destination, items in sorted_.items():
                message_ids = [e.id for e, _ in items]
                if destination == CANDIDATE:
                    self.gmail.relabel(message_ids, add=[self.gmail.label_id(self.filters.candidate_label)])
                    for email, reason in items:
                        self.on_candidate(email, reason)
                else:
                    label = self.filters.discarded_label if destination == DISCARDED else destination
                    self.gmail.relabel(message_ids, add=[self.gmail.label_id(label)], remove=["INBOX"])

        counts = {"checked": len(ids), **{d: len(items) for d, items in sorted_.items()}}
        if ids:
            log.info("%sSorted %s", "[dry run] " if self.dry_run else "", counts)
        return counts

    def watch(self, interval: int | None = None) -> None:
        interval = interval or self.filters.poll_seconds
        log.info("Watching Gmail every %ss for: %s%s", interval, self.filters.query(),
                 "  [dry run: nothing will be changed]" if self.dry_run else "")
        while True:
            try:
                self.run_once()
            except HttpError as exc:
                if exc.resp.status in (401, 403) and not is_rate_limit(exc):
                    raise SystemExit(f"Gmail refused access ({exc.resp.status}). Run: python -m connectors.gmail auth --modify")
                log.warning("Gmail error, retrying next poll: %s", exc.reason if hasattr(exc, "reason") else exc)
            except OSError as exc:  # network drop, DNS, timeouts
                log.warning("Network error, retrying next poll: %s", exc)
            time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m connectors.gmail_watcher", description="Sort Gmail alert emails")
    sub = parser.add_subparsers(dest="cmd", required=True)
    wa = sub.add_parser("watch", help="Keep listening and sort new alert emails")
    wa.add_argument("--interval", type=int, help="Seconds between polls (default: poll_seconds in the filters file)")
    wa.add_argument("--dry-run", action="store_true", help="Log decisions only, change nothing in Gmail")
    on = sub.add_parser("once", help="Sort unsorted alert emails once and exit")
    on.add_argument("--dry-run", action="store_true", help="Log decisions only, change nothing in Gmail")
    te = sub.add_parser("test", help="Show the decision for one message, change nothing")
    te.add_argument("message_id")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    filters = Filters.load()

    if args.cmd == "test":
        email = GmailConnector().read(args.message_id)
        destination, reason = classify(email, filters)
        print(f"{destination.upper()}: {email.subject}\n  from:   {email.sender}\n  reason: {reason}")
        return

    watcher = AlertWatcher(filters, dry_run=args.dry_run)
    if args.cmd == "once":
        watcher.run_once()
    else:
        try:
            watcher.watch(args.interval)
        except KeyboardInterrupt:
            log.info("Stopped.")


if __name__ == "__main__":
    main()
