"""MR summarizer and MR reviewer: GitHub pull requests explained and reviewed from their code.

Two independent features share this module, each run as its own on/off service:
  - summarizer: posts one general comment on the pull request summarizing what the code
                changes. When new commits arrive, that same comment is edited to match.
  - reviewer:   posts a GitHub review with a comment on each faulty line saying what to change
                (as a one-click "suggestion" when the fix is exact code). A pull request with
                no problems still gets a review saying so. When new commits arrive, only the
                changes since the last reviewed commit are reviewed.

GitHub is the record of what has been done: each summary comment and review carries a
hidden marker (<!-- jarvig:summary sha=... -->, <!-- jarvig:review sha=... -->) written
by the token's own account. Every poll looks at every open pull request, so one opened
while J.A.R.V.I.G. was off is still picked up, and a post that failed leaves no marker
and is simply retried.

The AI only sees the code diff: the PR description and commit messages are not sent,
so the text reflects what the code does, not what the author says it does.

Configure in backend/.env: GITHUB_TOKEN, GITHUB_REPO (owner/name), PR_POLL_SECONDS,
PR_MAX_DIFF_CHARS.

Usage (from backend/):
    python -m connectors.github_prs list                  # open PRs and what J.A.R.V.I.G. has posted
    python -m connectors.github_prs summarize <number>    # print a summary; posts nothing
    python -m connectors.github_prs review <number>       # print a review; posts nothing
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx

from .gmail import _backend_path

log = logging.getLogger("github_prs")
logging.getLogger("httpx").setLevel(logging.WARNING)  # otherwise every GitHub request is logged on each poll

API = "https://api.github.com"
MAX_PER_POLL = 3  # AI calls per feature per poll, so a repo with many open PRs catches up gradually

# Generated or vendored files: noise for the AI, and they eat the diff budget.
_SKIP_FILE = re.compile(
    r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|Pipfile\.lock|Cargo\.lock|go\.sum)$"
    r"|\.min\.(js|css)$|\.(png|jpe?g|gif|ico|pdf|zip|woff2?|ttf)$", re.I)
_MARKER = re.compile(r"<!-- jarvig:(summary|review) sha=([0-9a-f]{7,40}) -->")

SUMMARY_SYSTEM = """You are a senior software engineer explaining a pull request to a colleague.
You only see the code diff. Describe what the code actually does; the title only identifies it.

Reply in exactly this format:
SPOKEN: <one or two plain sentences for reading aloud; no code, no markdown>
## Summary
<3-6 bullet points: what changed and its effect on behaviour, grouped by area. Mention anything
risky (data migrations, config changes, removed behaviour). Do not review or suggest fixes.>
"""

REVIEW_SYSTEM = """You are a meticulous senior software engineer reviewing a pull request.
You only see the code diff; judge the change by what the code actually does. Every diff line
that exists in the new version of a file starts with its line number in that file; removed
lines have no number.

Reply in exactly this format:
SPOKEN: <one or two plain sentences for reading aloud; no code, no markdown>
```json
{"findings": [{"path": "<file path as shown after +++ b/>", "line": <first line number>,
  "end_line": <last line number, or null for one line>, "problem": "<what is wrong>",
  "fix": "<what to change>", "suggestion": "<exact replacement code for lines line..end_line, or null>"}],
 "note": "<optional short overall remark, or empty>"}
```
Rules:
- Only real issues: bugs, missing error handling, security problems, broken edge cases, misleading names.
  If there are none, return "findings": []. Do not invent issues or pad the list.
- line and end_line must be numbered lines of the diff, where the problem is.
- suggestion is the complete new code for exactly those lines, with the file's indentation. Use null
  when the fix isn't a direct replacement of those lines.
"""


class PullRequestError(Exception):
    """Retrying cannot help: missing or rejected token, unknown repository, no permission to post."""


@dataclass
class Finding:
    path: str
    line: int
    end_line: int | None
    problem: str
    fix: str
    suggestion: str | None = None

    def as_text(self) -> str:
        where = f"{self.path}:{self.line}" + (f"-{self.end_line}" if self.end_line else "")
        return f"`{where}`: {self.problem} {self.fix}".strip()


@dataclass
class Result:
    mode: str
    number: int
    title: str
    author: str
    url: str
    sha: str
    spoken: str
    body: str                 # markdown for the HUD and follow-up questions
    corrections: int = 0      # reviewer: number of findings
    truncated: bool = False
    since: str = ""           # reviewer: previously reviewed commit (only newer changes reviewed)
    updated: bool = False     # summarizer: an existing summary comment was edited
    created_at: str = ""

    def announcement(self) -> dict:
        title = self.title.rstrip(" .!?")  # "Fix bug." would otherwise be read as "Fix bug.."
        if self.mode == "reviewer":
            scope = "the new commits on " if self.since else ""
            if self.corrections:
                found = f"{self.corrections} suggested correction{'s' if self.corrections != 1 else ''}, " \
                        "commented on the lines in GitHub"
            else:
                found = "no problems found; I've marked it as reviewed on GitHub"
            speak = f"Review of {scope}pull request {self.number}, {title}: {found}. {self.spoken}"
        else:
            verb = "Updated summary" if self.updated else "Summary"
            where = "I've updated the summary comment on GitHub." if self.updated else "I've posted it on GitHub."
            speak = f"{verb} of pull request {self.number}, {title}. {self.spoken} {where}"
        text = f"Pull request #{self.number}: {self.title} (by {self.author})\n{self.url}\n\n{self.body}"
        return {"text": text, "speak": speak}

    def as_context(self) -> str:
        label = "Review" if self.mode == "reviewer" else "Summary"
        return f"{label} of PR #{self.number} '{self.title}' by {self.author} ({self.url}), commit {self.sha[:7]}:\n{self.body}"


# ------------------------------------------------------------------ GitHub
class GitHub:
    def __init__(self, token: str, repo: str):
        if not token:
            raise PullRequestError("GITHUB_TOKEN is empty. Add a token to backend/.env (see the README).")
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo or ""):
            raise PullRequestError(f"GITHUB_REPO must look like owner/name, got '{repo}'.")
        self.repo = repo
        self._login: str | None = None
        self.http = httpx.Client(
            base_url=API, timeout=30,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "JARVIG"},
        )

    def _check(self, r: httpx.Response) -> None:
        if r.status_code == 401:
            raise PullRequestError("GitHub rejected the token (401). Check GITHUB_TOKEN in backend/.env.")
        if r.status_code == 404:
            raise PullRequestError(f"Repository '{self.repo}' not found, or the token can't see it (404).")
        if r.status_code in (403, 429) and (r.headers.get("x-ratelimit-remaining") == "0" or r.status_code == 429):
            raise RuntimeError("GitHub rate limit reached; retrying next poll.")
        r.raise_for_status()

    def _get(self, path: str, **kwargs) -> httpx.Response:
        r = self.http.get(path, **kwargs)
        self._check(r)
        return r

    def _get_all(self, path: str) -> list[dict]:
        items, page = [], 1
        while True:
            batch = self._get(path, params={"per_page": 100, "page": page}).json()
            items += batch
            if len(batch) < 100 or page >= 10:
                return items
            page += 1

    def _post(self, method: str, path: str, body: dict) -> httpx.Response:
        r = self.http.request(method, path, json=body)
        if r.status_code == 403 and r.headers.get("x-ratelimit-remaining") != "0":
            raise PullRequestError(
                "GitHub refused to post (403). The token needs 'Pull requests: Read and write' on this repository.")
        return r

    def check(self) -> None:
        self._get(f"/repos/{self.repo}")

    def login(self) -> str:
        """The token's account: only its markers count, so a copied marker can't hide a pull request."""
        if self._login is None:
            self._login = self._get("/user").json()["login"]
        return self._login

    def open_pulls(self) -> list[dict]:
        return self._get(f"/repos/{self.repo}/pulls",
                         params={"state": "open", "sort": "updated", "direction": "desc", "per_page": 50}).json()

    def pull(self, number: int) -> dict:
        return self._get(f"/repos/{self.repo}/pulls/{number}").json()

    def diff(self, number: int) -> str:
        r = self.http.get(f"/repos/{self.repo}/pulls/{number}", headers={"Accept": "application/vnd.github.diff"})
        if r.status_code == 406:  # GitHub refuses diffs over its size limit
            raise RuntimeError(f"PR #{number} is too large for GitHub to return a diff.")
        self._check(r)
        return r.text

    def compare_diff(self, base: str, head: str) -> str | None:
        """Diff between two commits, or None if the older one is gone (e.g. after a force-push)."""
        r = self.http.get(f"/repos/{self.repo}/compare/{base}...{head}", headers={"Accept": "application/vnd.github.diff"})
        if r.status_code in (404, 422):
            return None
        self._check(r)
        return r.text

    def my_markers(self, items: list[dict], kind: str, text_key: str = "body") -> list[tuple[dict, str]]:
        """(item, sha) for items written by the token's account that carry a J.A.R.V.I.G. marker of `kind`."""
        me = self.login()
        found = []
        for item in items:
            m = _MARKER.search(item.get(text_key) or "")
            if m and m.group(1) == kind and (item.get("user") or {}).get("login") == me:
                found.append((item, m.group(2)))
        return found

    def issue_comments(self, number: int) -> list[dict]:
        return self._get_all(f"/repos/{self.repo}/issues/{number}/comments")

    def reviews(self, number: int) -> list[dict]:
        return self._get_all(f"/repos/{self.repo}/pulls/{number}/reviews")

    def add_comment(self, number: int, body: str) -> None:
        self._check(self._post("POST", f"/repos/{self.repo}/issues/{number}/comments", {"body": body}))

    def edit_comment(self, comment_id: int, body: str) -> None:
        self._check(self._post("PATCH", f"/repos/{self.repo}/issues/comments/{comment_id}", {"body": body}))

    def add_review(self, number: int, commit: str, body: str, comments: list[dict]) -> bool:
        """Post a comment-only review (never approves or blocks). False if GitHub rejected a line comment."""
        r = self._post("POST", f"/repos/{self.repo}/pulls/{number}/reviews",
                       {"commit_id": commit, "event": "COMMENT", "body": body, "comments": comments})
        if r.status_code == 422 and comments:
            log.warning("GitHub rejected line comments on PR #%s: %s", number, r.text[:300])
            return False
        self._check(r)
        return True


# ------------------------------------------------------------------ diffs
def drop_generated(diff: str) -> tuple[str, list[str]]:
    kept, skipped = [], []
    for section in re.split(r"(?m)^(?=diff --git )", diff):
        m = re.match(r"diff --git a/(\S+)", section)
        if m and _SKIP_FILE.search(m.group(1)):
            skipped.append(m.group(1))
            continue
        kept.append(section)
    return "".join(kept), skipped


def number_diff(diff: str) -> tuple[str, dict[str, set[int]]]:
    """(diff with new-file line numbers, {path: lines GitHub accepts comments on})."""
    out, lines_by_file = [], {}
    path, new_line = None, 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = None if line[4:] == "/dev/null" else re.sub(r"^b/", "", line[4:])
            if path:
                lines_by_file.setdefault(path, set())
            out.append(line)
        elif line.startswith("@@"):
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)", line)
            new_line = int(m.group(1)) if m else 0
            out.append(line)
        elif path and line[:1] in ("+", " ") and not line.startswith("+++"):
            lines_by_file[path].add(new_line)
            out.append(f"{new_line:>5} {line}")
            new_line += 1
        elif line[:1] == "-" and not line.startswith("---"):
            out.append(f"{'':>5} {line}")
        else:
            out.append(line)
    return "\n".join(out), lines_by_file


def cap(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n\n[... diff truncated: too long to include in full ...]\n", True


def build_prompt(pr: dict, diff: str, skipped: list[str], truncated: bool, since: str = "") -> str:
    notes = []
    if since:
        notes.append(f"Only the changes since commit {since[:7]} are shown; earlier code was already reviewed.")
    if skipped:
        notes.append(f"Generated/binary files not shown: {', '.join(skipped)}.")
    if truncated:
        notes.append("The diff was truncated; cover only what is shown and say the result is partial.")
    return (
        f"Pull request #{pr['number']} in {pr['base']['repo']['full_name']} "
        f"({pr['head']['ref']} -> {pr['base']['ref']}).\nTitle: {pr['title']}\n"
        + ("\n".join(notes) + "\n" if notes else "")
        + f"\nDiff:\n```diff\n{diff}\n```"
    )


# ------------------------------------------------------------------ parsing the AI's replies
def _split_spoken(raw: str) -> tuple[str, str]:
    raw = raw.strip()
    m = re.match(r"SPOKEN:\s*(.+?)\s*(?=^##|^```|^\{|\Z)", raw, re.S | re.M)
    if not m:
        return "", raw
    return " ".join(m.group(1).split()), raw[m.end():].strip()


def parse_summary(raw: str) -> tuple[str, str]:
    """(spoken line, markdown body)."""
    spoken, body = _split_spoken(raw)
    if not re.match(r"##\s*Summary", body, re.I):
        body = f"## Summary\n{body}"
    if not spoken:
        first = re.search(r"(?m)^\s*[-*]\s+(.+)$", body)
        spoken = first.group(1) if first else "It's ready in the conversation."
    return spoken, body


def parse_review(raw: str) -> tuple[str, list[Finding], str]:
    """(spoken line, findings, overall note). An unreadable reply becomes the note, with no findings."""
    spoken, rest = _split_spoken(raw)
    block = re.search(r"```(?:json)?\s*(\{.*\})\s*```", rest, re.S) or re.search(r"(\{.*\})", rest, re.S)
    try:
        data = json.loads(block.group(1)) if block else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return spoken or "I couldn't read my own review format, so it's all in one comment.", [], rest
    findings = []
    for f in data.get("findings") or []:
        try:
            end = f.get("end_line")
            findings.append(Finding(
                path=str(f["path"]).removeprefix("b/"), line=int(f["line"]),
                end_line=int(end) if end not in (None, "", f["line"]) else None,
                problem=str(f.get("problem", "")).strip(), fix=str(f.get("fix", "")).strip(),
                suggestion=f.get("suggestion") if isinstance(f.get("suggestion"), str) else None,
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return spoken or ("No problems found." if not findings else ""), findings, str(data.get("note") or "").strip()


def place(findings: list[Finding], commentable: dict[str, set[int]]) -> tuple[list[tuple[dict, Finding]], list[Finding]]:
    """Split findings into GitHub line comments (with their finding) and ones that go in the review's
    overall text, because GitHub only accepts comments on lines that are part of the diff."""
    comments, general = [], []
    for f in findings:
        lines = commentable.get(f.path, set())
        span = range(f.line, (f.end_line or f.line) + 1)
        if f.line not in lines:
            general.append(f)
            continue
        if f.end_line and (f.end_line < f.line or not all(n in lines for n in span)):
            f.end_line, f.suggestion = None, None  # keep the comment on the first line only
        body = f"**{f.problem}**\n\n{f.fix}".strip()
        if f.suggestion is not None:
            fence = "````" if "```" in f.suggestion else "```"
            body += f"\n\n{fence}suggestion\n{f.suggestion.rstrip()}\n{fence}"
        comment = {"path": f.path, "line": f.end_line or f.line, "side": "RIGHT", "body": body}
        if f.end_line:
            comment.update(start_line=f.line, start_side="RIGHT")
        comments.append((comment, f))
    return comments, general


# ------------------------------------------------------------------ comment bodies
def _footer(agent_name: str, truncated: bool, marker: str) -> str:
    partial = " The diff was too long, so this is partial." if truncated else ""
    return (f"<sub>Written by J.A.R.V.I.G. ({agent_name}) from the code diff only; commit messages and the PR "
            f"description were not used.{partial} It can be wrong: check before relying on it.</sub>\n{marker}")


def summary_comment(result: Result, agent_name: str) -> str:
    updated = " · updated after new commits" if result.updated else ""
    return (f"### 🤖 J.A.R.V.I.G. summary · `{result.sha[:7]}`{updated}\n\n{result.body}\n\n"
            + _footer(agent_name, result.truncated, f"<!-- jarvig:summary sha={result.sha} -->"))


def review_body(sha: str, since: str, inline: int, general: list[Finding], note: str,
                agent_name: str, truncated: bool) -> str:
    scope = f"the changes since `{since[:7]}`" if since else "this pull request"
    lines = [f"### 🤖 J.A.R.V.I.G. review · `{sha[:7]}`", ""]
    if not inline and not general:
        lines.append(f"Reviewed {scope}: **no problems found.** ✅")
    else:
        total = inline + len(general)
        lines.append(f"Reviewed {scope}: **{total} suggested correction{'s' if total != 1 else ''}**"
                     + (f", {inline} of them commented on the lines below." if inline else "."))
    if general:
        lines += ["", "Findings that couldn't be attached to a changed line:", ""]
        lines += [f"{i}. {f.as_text()}" for i, f in enumerate(general, 1)]
    if note:
        lines += ["", note]
    lines += ["", _footer(agent_name, truncated, f"<!-- jarvig:review sha={sha} -->")]
    return "\n".join(lines)


# ------------------------------------------------------------------ the job
class PullRequestJob:
    """One feature (summarizer or reviewer) polled by a bridge service."""

    dry_run = False

    def __init__(
        self,
        mode: str,
        github: GitHub | None,
        complete: Callable[[str, str], str] | None,
        agent_name: str = "AI",
        max_diff_chars: int = 60000,
        poll_seconds: int = 120,
        state_file: Path | None = None,
    ):
        if mode not in ("summarizer", "reviewer"):
            raise ValueError("mode must be 'summarizer' or 'reviewer'")
        self.mode = mode
        self.github = github
        self.complete = complete          # (prompt, system) -> reply text
        self.agent_name = agent_name
        self.max_diff_chars = max_diff_chars
        self.poll_seconds = poll_seconds
        # Only the latest results, for follow-up questions. What has been done is read from GitHub.
        self.state_file = state_file or _backend_path(f"data/pr_{mode}_state.json")

    def recent(self, limit: int = 3) -> list[Result]:
        try:
            items = json.loads(self.state_file.read_text()).get("recent", [])
        except (FileNotFoundError, ValueError):
            return []
        known = {f.name for f in fields(Result)}
        results = []
        for item in items[:limit]:
            try:
                results.append(Result(**{k: v for k, v in item.items() if k in known}))
            except TypeError:
                continue
        return results

    def _remember(self, result: Result) -> None:
        recent = [asdict(r) for r in self.recent(10) if r.number != result.number]
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps({"recent": [asdict(result)] + recent[:9]}, indent=2))

    # --- what needs doing, read from GitHub
    def due(self, pr: dict) -> tuple[bool, dict | None, str]:
        """(needs work?, existing J.A.R.V.I.G. item or None, sha it covers)."""
        if self.mode == "summarizer":
            mine = self.github.my_markers(self.github.issue_comments(pr["number"]), "summary")
        else:
            mine = self.github.my_markers(self.github.reviews(pr["number"]), "review")
        if not mine:
            return True, None, ""
        item, sha = mine[-1]  # the latest one
        return not pr["head"]["sha"].startswith(sha), item, sha

    # --- writing
    def summarize(self, pr: dict, updated: bool = False) -> Result:
        diff, skipped = drop_generated(self.github.diff(pr["number"]))
        diff, truncated = cap(diff, self.max_diff_chars)
        if diff.strip():
            spoken, body = parse_summary(self.complete(build_prompt(pr, diff, skipped, truncated), SUMMARY_SYSTEM))
        else:
            spoken, body = "It only changes generated or binary files.", "## Summary\n- Only generated or binary files changed."
        return self._result(pr, spoken, body, truncated=truncated, updated=updated)

    def review(self, pr: dict, since: str = "") -> tuple[Result, list[tuple[dict, Finding]], list[Finding], str]:
        """(result, line comments with their findings, findings for the overall text, overall note)."""
        raw = self.github.compare_diff(since, pr["head"]["sha"]) if since else None
        if since and raw is None:
            log.info("PR #%s: commit %s is gone (force-push?), reviewing the whole pull request", pr["number"], since[:7])
            since = ""
        raw = raw if raw is not None else self.github.diff(pr["number"])
        diff, skipped = drop_generated(raw)
        numbered, commentable = number_diff(diff)
        numbered, truncated = cap(numbered, self.max_diff_chars)
        if diff.strip():
            reply = self.complete(build_prompt(pr, numbered, skipped, truncated, since), REVIEW_SYSTEM)
            spoken, findings, note = parse_review(reply)
        else:
            spoken, findings, note = "Nothing to review: only generated or binary files changed.", [], ""
        comments, general = place(findings, commentable)
        body = "## Suggested corrections\n" + (
            "\n".join(f"{i}. {f.as_text()}" for i, f in enumerate(findings, 1)) if findings else "None found.")
        if note:
            body += f"\n\n{note}"
        result = self._result(pr, spoken, body, corrections=len(findings), truncated=truncated, since=since)
        return result, comments, general, note

    def _result(self, pr: dict, spoken: str, body: str, **extra) -> Result:
        return Result(mode=self.mode, number=pr["number"], title=pr["title"], author=pr["user"]["login"],
                      url=pr["html_url"], sha=pr["head"]["sha"], spoken=spoken, body=body,
                      created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), **extra)

    # --- one poll
    def handle(self, pr: dict) -> Result | None:
        """Summarize or review one pull request if GitHub shows it still needs it; post; return the result."""
        needed, existing, covered = self.due(pr)
        if not needed:
            return None
        if self.mode == "summarizer":
            result = self.summarize(pr, updated=existing is not None)
            body = summary_comment(result, self.agent_name)
            if existing:
                self.github.edit_comment(existing["id"], body)
            else:
                self.github.add_comment(pr["number"], body)
        else:
            result, placed, general, note = self.review(pr, since=covered)
            args = (pr["number"], result.sha)
            comments = [c for c, _ in placed]
            if not self.github.add_review(*args, review_body(result.sha, result.since, len(comments), general, note,
                                                             self.agent_name, result.truncated), comments):
                # GitHub refused a line position: put every finding in the overall text instead.
                everything = [f for _, f in placed] + general
                self.github.add_review(*args, review_body(result.sha, result.since, 0, everything, note,
                                                          self.agent_name, result.truncated), [])
        self._remember(result)
        log.info("%-10s PR #%s %s%s", self.mode.upper(), result.number, result.sha[:7],
                 f" ({result.corrections} corrections)" if self.mode == "reviewer" else
                 (" (updated)" if result.updated else ""))
        return result

    def run_once(self) -> dict:
        counts, announce, error = {"handled": 0}, [], None
        for pr in reversed(self.github.open_pulls()):  # oldest update first
            if counts["handled"] >= MAX_PER_POLL:
                break  # the rest next poll
            try:
                result = self.handle(pr)
            except PullRequestError:
                raise  # the service stops and says why
            except Exception as exc:  # AI timeout, network: this PR is retried next poll
                error = f"PR #{pr['number']}: {exc}"
                log.warning(error)
                continue
            if result:
                counts["handled"] += 1
                announce.append(result.announcement())
        if announce:
            counts["announce"] = announce
        if error:
            counts["error"] = error
        return counts


def build_from_settings(mode: str, settings, complete: Callable[[str, str], str], agent_name: str) -> PullRequestJob:
    github = GitHub(settings.github_token, settings.github_repo)
    github.check()   # a bad token or repo is reported when the feature is switched on
    github.login()
    return PullRequestJob(mode, github, complete, agent_name=agent_name,
                          max_diff_chars=settings.pr_max_diff_chars, poll_seconds=settings.pr_poll_seconds)


def main() -> None:
    import asyncio

    from agent import Message, build_agent
    from bridge.config import get_settings

    parser = argparse.ArgumentParser(prog="python -m connectors.github_prs",
                                     description="Summarize and review GitHub pull requests")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="Open pull requests and what J.A.R.V.I.G. has posted on them")
    for name, what in (("summarize", "a summary"), ("review", "a review")):
        p = sub.add_parser(name, help=f"Print {what} of one pull request; posts nothing")
        p.add_argument("number", type=int)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    settings = get_settings()
    github = GitHub(settings.github_token, settings.github_repo)
    if args.cmd == "list":
        jobs = [PullRequestJob(m, github, None) for m in ("summarizer", "reviewer")]
        for pr in github.open_pulls():
            marks = []
            for job in jobs:
                needed, existing, sha = job.due(pr)
                marks.append(f"{job.mode}: " + ("none" if not existing else f"{sha[:7]}" + (" (outdated)" if needed else "")))
            print(f"#{pr['number']:<5} {pr['head']['sha'][:7]}  {' | '.join(marks):45}  {pr['title']}")
        return

    agent = build_agent(settings)

    def complete(prompt: str, system: str) -> str:
        return asyncio.run(agent.complete([Message("user", prompt)], system))

    pr = github.pull(args.number)
    if args.cmd == "summarize":
        result = PullRequestJob("summarizer", github, complete).summarize(pr)
        print(f"SPOKEN: {result.spoken}\n\n{result.body}")
        return
    result, placed, general, note = PullRequestJob("reviewer", github, complete).review(pr)
    print(f"SPOKEN: {result.spoken}\n")
    comments = [c for c, _ in placed]
    for c in comments:
        where = f"{c['path']}:{c.get('start_line', c['line'])}" + (f"-{c['line']}" if "start_line" in c else "")
        print(f"--- line comment on {where}\n{c['body']}\n")
    for f in general:
        print(f"--- in the overall text (line not in the diff): {f.as_text()}\n")
    if not comments and not general:
        print("No problems found.")
    if note:
        print(f"Note: {note}")


if __name__ == "__main__":
    main()
