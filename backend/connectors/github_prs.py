"""MR summarizer and MR reviewer: GitHub pull requests explained and reviewed from their code.

Two independent features share this module, each run as its own on/off service:
  - summarizer: what the pull request changes and its effect on behaviour
  - reviewer:   suggested corrections (bugs, missing error handling, security, edge cases)

Every poll a feature lists the repository's open pull requests and handles each one
that is new or has new commits since it last handled it (tracked per feature by head
commit SHA in data/pr_<feature>_state.json, so a restart doesn't redo everything).
The text is written by J.A.R.V.I.G.'s agent from the diff alone: the PR description
and commit messages are deliberately not sent, so it reflects what the code does, not
what the author says it does.

Results are announced in the HUD and, with PR_POST_COMMENTS=true, posted as a comment
on the pull request.

Configure in backend/.env: GITHUB_TOKEN, GITHUB_REPO (owner/name), PR_POLL_SECONDS,
PR_POST_COMMENTS, PR_MAX_DIFF_CHARS.

Usage (from backend/):
    python -m connectors.github_prs list                  # open PRs and what has been handled
    python -m connectors.github_prs summarize <number>    # print a summary; posts nothing
    python -m connectors.github_prs review <number>       # print a review; posts nothing
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx

from .gmail import _backend_path

log = logging.getLogger("github_prs")

API = "https://api.github.com"

# Generated or vendored files: noise for the AI, and they eat the diff budget.
_SKIP_FILE = re.compile(
    r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|Pipfile\.lock|Cargo\.lock|go\.sum)$"
    r"|\.min\.(js|css)$|\.(png|jpe?g|gif|ico|pdf|zip|woff2?|ttf)$", re.I)

_COMMON = """You only see the code diff of a GitHub pull request. Judge the change by what the code
actually does; the title is only there to identify it.

Reply in exactly this format:
SPOKEN: <one or two plain sentences for reading aloud; no code, no markdown>
"""

MODES = {
    "summarizer": {
        "title": "MR summarizer",
        "heading": "Summary",
        "system": "You are a senior software engineer explaining a pull request to a colleague.\n" + _COMMON + """\
## Summary
<3-6 bullet points: what changed and its effect on behaviour, grouped by area. Mention anything
risky (data migrations, config changes, removed behaviour). Do not review or suggest fixes.>
""",
    },
    "reviewer": {
        "title": "MR reviewer",
        "heading": "Suggested corrections",
        "system": "You are a meticulous senior software engineer reviewing a pull request.\n" + _COMMON + """\
## Suggested corrections
<numbered list. Each item: `path:line` - the problem - a concrete fix (a short code snippet when it helps).
Only real issues: bugs, missing error handling, security problems, broken edge cases, misleading names.
If there are none, write "None found." Do not invent issues or pad the list.>
""",
    },
}


class PullRequestError(Exception):
    """Retrying cannot help: missing token, bad token, unknown repository."""


@dataclass
class Result:
    mode: str
    number: int
    title: str
    author: str
    url: str
    sha: str
    spoken: str
    body: str            # markdown section: "## Summary ..." or "## Suggested corrections ..."
    corrections: int     # reviewer only
    truncated: bool
    created_at: str

    def announcement(self, commented: bool) -> dict:
        where = " I've posted it on GitHub." if commented else ""
        title = self.title.rstrip(" .!?")  # "Fix bug." would otherwise be read as "Fix bug.."
        if self.mode == "reviewer":
            found = "no corrections to suggest" if not self.corrections else (
                f"{self.corrections} suggested correction{'s' if self.corrections != 1 else ''}")
            speak = f"Review of pull request {self.number}, {title}: {found}. {self.spoken}{where}"
        else:
            speak = f"Summary of pull request {self.number}, {title}. {self.spoken}{where}"
        text = f"Pull request #{self.number}: {self.title} (by {self.author})\n{self.url}\n\n{self.body}"
        return {"text": text, "speak": speak}

    def as_context(self) -> str:
        label = MODES[self.mode]["heading"]
        return f"{label} of PR #{self.number} '{self.title}' by {self.author} ({self.url}), head {self.sha[:7]}:\n{self.body}"


# ------------------------------------------------------------------ GitHub
class GitHub:
    def __init__(self, token: str, repo: str):
        if not token:
            raise PullRequestError("GITHUB_TOKEN is empty. Add a token to backend/.env (see the README).")
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo or ""):
            raise PullRequestError(f"GITHUB_REPO must look like owner/name, got '{repo}'.")
        self.repo = repo
        self.http = httpx.Client(
            base_url=API, timeout=30,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "JARVIG"},
        )

    def _get(self, path: str, **kwargs) -> httpx.Response:
        r = self.http.get(path, **kwargs)
        self._check(r)
        return r

    def _check(self, r: httpx.Response) -> None:
        if r.status_code == 401:
            raise PullRequestError("GitHub rejected the token (401). Check GITHUB_TOKEN in backend/.env.")
        if r.status_code == 404:
            raise PullRequestError(f"Repository '{self.repo}' not found, or the token can't see it (404).")
        if r.status_code == 403 and r.headers.get("x-ratelimit-remaining") == "0":
            raise RuntimeError("GitHub rate limit reached; retrying next poll.")
        r.raise_for_status()

    def check(self) -> None:
        self._get(f"/repos/{self.repo}")

    def open_pulls(self) -> list[dict]:
        return self._get(f"/repos/{self.repo}/pulls",
                         params={"state": "open", "sort": "updated", "direction": "desc", "per_page": 30}).json()

    def pull(self, number: int) -> dict:
        return self._get(f"/repos/{self.repo}/pulls/{number}").json()

    def diff(self, number: int) -> str:
        r = self.http.get(f"/repos/{self.repo}/pulls/{number}", headers={"Accept": "application/vnd.github.diff"})
        if r.status_code == 406:  # GitHub refuses diffs over its size limit
            raise RuntimeError(f"PR #{number} is too large for GitHub to return a diff.")
        self._check(r)
        return r.text

    def comment(self, number: int, body: str) -> None:
        r = self.http.post(f"/repos/{self.repo}/issues/{number}/comments", json={"body": body})
        if r.status_code in (403, 404):
            raise PermissionError(
                f"GitHub refused the comment ({r.status_code}). The token needs 'Pull requests: Read and write'.")
        self._check(r)


# ------------------------------------------------------------------ prompts and parsing
def trim_diff(diff: str, limit: int) -> tuple[str, list[str], bool]:
    """(diff without generated files, skipped file names, truncated?)."""
    kept, skipped = [], []
    for section in re.split(r"(?m)^(?=diff --git )", diff):
        m = re.match(r"diff --git a/(\S+)", section)
        if m and _SKIP_FILE.search(m.group(1)):
            skipped.append(m.group(1))
            continue
        kept.append(section)
    text = "".join(kept)
    if len(text) <= limit:
        return text, skipped, False
    return text[:limit] + "\n\n[... diff truncated: too long to include in full ...]\n", skipped, True


def build_prompt(pr: dict, diff: str, skipped: list[str], truncated: bool) -> str:
    notes = []
    if skipped:
        notes.append(f"Generated/binary files not shown: {', '.join(skipped)}.")
    if truncated:
        notes.append("The diff was truncated; cover only what is shown and say the result is partial.")
    return (
        f"Pull request #{pr['number']} in {pr['base']['repo']['full_name']} "
        f"({pr['head']['ref']} -> {pr['base']['ref']}).\n"
        f"Title: {pr['title']}\n"
        + ("\n".join(notes) + "\n" if notes else "")
        + f"\nDiff:\n```diff\n{diff}\n```"
    )


def parse_reply(raw: str, mode: str) -> tuple[str, str, int]:
    """(spoken line, markdown body, number of corrections) from the agent's reply."""
    raw = raw.strip()
    spoken = ""
    m = re.match(r"SPOKEN:\s*(.+?)\s*(?=^##|\Z)", raw, re.S | re.M)
    if m:
        spoken = " ".join(m.group(1).split())
        raw = raw[m.end():].strip()
    heading = MODES[mode]["heading"]
    if not re.match(rf"##\s*{heading}", raw, re.I):
        raw = f"## {heading}\n{raw}"
    corrections = 0
    if mode == "reviewer":
        section = re.split(rf"(?im)^##\s*{heading}\s*$", raw)[-1]
        if not re.match(r"\s*None found", section, re.I):
            corrections = len(re.findall(r"(?m)^\s*\d+[.)]\s", section))
    if not spoken:
        first = re.search(r"(?m)^\s*(?:[-*]|\d+[.)])\s+(.+)$", raw)
        spoken = first.group(1) if first else "It's ready in the conversation."
    return spoken, raw, corrections


def comment_body(result: Result, agent_name: str) -> str:
    kind = "summary" if result.mode == "summarizer" else "review"
    partial = " The diff was too long, so this is partial." if result.truncated else ""
    return (
        f"### 🤖 J.A.R.V.I.G. {kind} · `{result.sha[:7]}`\n\n{result.body}\n\n"
        f"<sub>Written by J.A.R.V.I.G. ({agent_name}) from the code diff only; commit messages and the PR "
        f"description were not used.{partial} It can be wrong: check before relying on it.</sub>"
    )


# ------------------------------------------------------------------ the job
class PullRequestJob:
    """One feature (summarizer or reviewer) polled by a bridge service."""

    dry_run = False

    def __init__(
        self,
        mode: str,
        github: GitHub,
        complete: Callable[[str, str], str],
        agent_name: str = "AI",
        post_comments: bool = True,
        max_diff_chars: int = 60000,
        poll_seconds: int = 120,
        state_file: Path | None = None,
    ):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {sorted(MODES)}")
        self.mode = mode
        self.github = github
        self.complete = complete          # (prompt, system) -> reply text
        self.agent_name = agent_name
        self.post_comments = post_comments
        self.max_diff_chars = max_diff_chars
        self.poll_seconds = poll_seconds
        self.state_file = state_file or _backend_path(f"data/pr_{mode}_state.json")
        self.state = self._load_state()

    # state: {"done": {"<number>": "<head sha>"}, "recent": [Result dicts, newest first]}
    def _load_state(self) -> dict:
        try:
            return json.loads(self.state_file.read_text())
        except (FileNotFoundError, ValueError):
            return {"done": {}, "recent": []}

    def _save_state(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(self.state, indent=2))

    def recent(self, limit: int = 3) -> list[Result]:
        return [Result(**r) for r in self.state.get("recent", [])[:limit]]

    def handle(self, pr: dict) -> Result:
        """Write the summary or review for one pull request (no posting, no state change)."""
        diff, skipped, truncated = trim_diff(self.github.diff(pr["number"]), self.max_diff_chars)
        if not diff.strip():
            heading = MODES[self.mode]["heading"]
            spoken, body, corrections = ("It only changes generated or binary files.",
                                         f"## {heading}\n- Only generated or binary files changed.", 0)
        else:
            reply = self.complete(build_prompt(pr, diff, skipped, truncated), MODES[self.mode]["system"])
            spoken, body, corrections = parse_reply(reply, self.mode)
        return Result(
            mode=self.mode, number=pr["number"], title=pr["title"], author=pr["user"]["login"],
            url=pr["html_url"], sha=pr["head"]["sha"], spoken=spoken, body=body, corrections=corrections,
            truncated=truncated, created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    def run_once(self) -> dict:
        done = self.state.setdefault("done", {})
        due = [pr for pr in self.github.open_pulls() if done.get(str(pr["number"])) != pr["head"]["sha"]]
        counts, announce, error = {"handled": 0}, [], None
        for pr in reversed(due):  # oldest update first
            result = self.handle(pr)
            commented = False
            if self.post_comments:
                try:
                    self.github.comment(pr["number"], comment_body(result, self.agent_name))
                    commented = True
                    counts["commented"] = counts.get("commented", 0) + 1
                except PermissionError as exc:
                    error = str(exc)
                    log.warning(error)
            # Mark done even if the comment failed, so the same diff isn't sent to the AI every poll.
            done[str(pr["number"])] = pr["head"]["sha"]
            self.state["recent"] = ([asdict(result)] + [r for r in self.state.get("recent", [])
                                                         if r["number"] != result.number])[:10]
            self._save_state()
            counts["handled"] += 1
            announce.append(result.announcement(commented))
            log.info("%-10s PR #%s %s%s", self.mode.upper(), result.number, result.sha[:7],
                     " + comment" if commented else "")
        if announce:
            counts["announce"] = announce
        if error:
            counts["error"] = error
        return counts


def build_from_settings(mode: str, settings, complete: Callable[[str, str], str], agent_name: str) -> PullRequestJob:
    github = GitHub(settings.github_token, settings.github_repo)
    github.check()  # a bad token or repo is reported when the feature is switched on
    return PullRequestJob(
        mode, github, complete, agent_name=agent_name, post_comments=settings.pr_post_comments,
        max_diff_chars=settings.pr_max_diff_chars, poll_seconds=settings.pr_poll_seconds,
    )


def main() -> None:
    import asyncio

    from agent import Message, build_agent
    from bridge.config import get_settings

    parser = argparse.ArgumentParser(prog="python -m connectors.github_prs",
                                     description="Summarize and review GitHub pull requests")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="Open pull requests and whether each feature has handled them")
    for name, verb in (("summarize", "a summary"), ("review", "a review")):
        p = sub.add_parser(name, help=f"Print {verb} of one pull request; posts nothing, changes no state")
        p.add_argument("number", type=int)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    settings = get_settings()
    github = GitHub(settings.github_token, settings.github_repo)
    if args.cmd == "list":
        done = {m: PullRequestJob(m, github, complete=lambda p, s: "").state.get("done", {}) for m in MODES}
        for pr in github.open_pulls():
            marks = "  ".join(
                f"{m}:{'done' if done[m].get(str(pr['number'])) == pr['head']['sha'] else 'due '}" for m in MODES)
            print(f"#{pr['number']:<5} {pr['head']['sha'][:7]}  {marks}  {pr['user']['login']:15}  {pr['title']}")
        return

    agent = build_agent(settings)

    def complete(prompt: str, system: str) -> str:
        return asyncio.run(agent.complete([Message("user", prompt)], system))

    mode = "summarizer" if args.cmd == "summarize" else "reviewer"
    result = PullRequestJob(mode, github, complete, agent_name=agent.info()["provider"]).handle(github.pull(args.number))
    print(f"SPOKEN: {result.spoken}\n\n{result.body}")


if __name__ == "__main__":
    main()
