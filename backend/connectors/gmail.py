"""Gmail connector: reads messages in full, and can label/move them when authorised to.

Setup (once):
    1. In Google Cloud Console, enable the Gmail API and create an OAuth client
       of type "Desktop app". Download the JSON as backend/credentials.json.
    2. From backend/, run:  python -m connectors.gmail auth
       (or `auth --modify` to also allow labelling and moving messages, which the
       alert watcher needs). A browser opens for consent; the token is saved to token.json.

Usage:
    python -m connectors.gmail list --query "is:unread" --max 10
    python -m connectors.gmail read <message_id>

    from connectors.gmail import GmailConnector
    gmail = GmailConnector()
    for summary in gmail.search("from:alice newer_than:7d"):
        email = gmail.read(summary["id"])
        print(email.subject, email.body)

The Google client is synchronous; from async code call it with
`await asyncio.to_thread(gmail.read, message_id)`.
"""
from __future__ import annotations

import argparse
import base64
import email
import os
from dataclasses import asdict, dataclass, field
from email.message import EmailMessage
from email.policy import default as default_policy
from html.parser import HTMLParser
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

READ_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
MODIFY_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
SCOPES = READ_SCOPES

# A scope also satisfies every scope it is broader than.
_IMPLIED = {
    "https://mail.google.com/": {READ_SCOPES[0], MODIFY_SCOPES[0]},
    MODIFY_SCOPES[0]: {READ_SCOPES[0]},
}

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Gmail limits calls per user per minute; the client backs off exponentially on
# rate-limit and server errors (up to about two minutes in total).
RETRIES = 7


def _backend_path(value: str) -> Path:
    """Relative paths are taken from backend/, so the CLI works from any folder."""
    path = Path(value).expanduser()
    return path if path.is_absolute() else BACKEND_DIR / path


CREDENTIALS_FILE = _backend_path(os.getenv("GMAIL_CREDENTIALS_FILE", "credentials.json"))
TOKEN_FILE = _backend_path(os.getenv("GMAIL_TOKEN_FILE", "token.json"))


@dataclass
class Attachment:
    filename: str
    mime_type: str
    size: int


@dataclass
class Email:
    id: str
    thread_id: str
    labels: list[str]
    subject: str
    sender: str
    to: str
    cc: str
    date: str
    body: str
    html: str | None
    attachments: list[Attachment] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


class _TextExtractor(HTMLParser):
    """Turns HTML into readable text for messages that have no plain-text part."""

    _BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table"}
    _SKIP = {"script", "style", "head"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skipping += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skipping:
            self._skipping -= 1

    def handle_data(self, data):
        if not self._skipping:
            self.parts.append(data)

    def text(self) -> str:
        lines = (line.strip() for line in "".join(self.parts).splitlines())
        return "\n".join(line for line in lines if line)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return parser.text()


def _covers(granted, wanted: list[str]) -> bool:
    have = set(granted or [])
    for scope in list(have):
        have |= _IMPLIED.get(scope, set())
    return set(wanted) <= have


def get_credentials(interactive: bool = False, scopes: list[str] = READ_SCOPES) -> Credentials:
    creds = None
    if TOKEN_FILE.exists():
        # Load with the scopes the token was granted, then check they cover what is needed.
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE))
        if not _covers(creds.scopes, scopes):
            creds = None
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif interactive:
        if not CREDENTIALS_FILE.exists():
            raise FileNotFoundError(f"OAuth client file not found: {CREDENTIALS_FILE}")
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), scopes)
        creds = flow.run_local_server(port=0)
    else:
        flag = " --modify" if scopes != READ_SCOPES else ""
        raise RuntimeError(f"Gmail is not authorised for this yet. Run: python -m connectors.gmail auth{flag}")
    TOKEN_FILE.write_text(creds.to_json())
    return creds


class GmailConnector:
    def __init__(self, creds: Credentials | None = None, scopes: list[str] = READ_SCOPES):
        creds = creds or get_credentials(scopes=scopes)
        self.service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        self.users = self.service.users()
        self._label_ids: dict[str, str] = {}

    def list_ids(self, query: str, limit: int = 500) -> list[str]:
        """IDs of messages matching a Gmail query, newest first."""
        ids: list[str] = []
        page_token = None
        while len(ids) < limit:
            resp = self.users.messages().list(
                userId="me", q=query, maxResults=min(limit - len(ids), 500), pageToken=page_token
            ).execute(num_retries=RETRIES)
            ids.extend(m["id"] for m in resp.get("messages", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return ids

    def label_id(self, name: str) -> str:
        """ID of the user label with this name, created if it does not exist. Needs the modify scope."""
        if name in self._label_ids:
            return self._label_ids[name]
        for label in self.users.labels().list(userId="me").execute(num_retries=RETRIES).get("labels", []):
            if label["name"].lower() == name.lower():
                self._label_ids[name] = label["id"]
                return label["id"]
        created = self.users.labels().create(
            userId="me", body={"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"}
        ).execute(num_retries=RETRIES)
        self._label_ids[name] = created["id"]
        return created["id"]

    def relabel(self, message_ids: list[str], add: list[str] = (), remove: list[str] = ()) -> None:
        """Add/remove label IDs on many messages at once. Needs the modify scope."""
        for start in range(0, len(message_ids), 1000):
            self.users.messages().batchModify(
                userId="me",
                body={"ids": message_ids[start:start + 1000], "addLabelIds": list(add), "removeLabelIds": list(remove)},
            ).execute(num_retries=RETRIES)

    def search(self, query: str = "", max_results: int = 10, label_ids: list[str] | None = None) -> list[dict]:
        """Return summaries (id, threadId, from, subject, date, snippet) for messages matching a Gmail query."""
        ids: list[dict] = []
        page_token = None
        while len(ids) < max_results:
            resp = self.users.messages().list(
                userId="me",
                q=query or None,
                labelIds=label_ids,
                maxResults=min(max_results - len(ids), 500),
                pageToken=page_token,
            ).execute(num_retries=RETRIES)
            ids.extend(resp.get("messages", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        summaries = []
        for ref in ids[:max_results]:
            msg = self.users.messages().get(
                userId="me", id=ref["id"], format="metadata", metadataHeaders=["From", "Subject", "Date"]
            ).execute(num_retries=RETRIES)
            headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
            summaries.append({
                "id": msg["id"],
                "thread_id": msg["threadId"],
                "from": headers.get("from", ""),
                "subject": headers.get("subject", ""),
                "date": headers.get("date", ""),
                "snippet": msg.get("snippet", ""),
            })
        return summaries

    def read(self, message_id: str) -> Email:
        """Fetch one message in full: all headers, the complete body, and attachment details."""
        msg = self.users.messages().get(userId="me", id=message_id, format="raw").execute(num_retries=RETRIES)
        raw = base64.urlsafe_b64decode(msg["raw"])
        parsed: EmailMessage = email.message_from_bytes(raw, policy=default_policy)

        plain_part = parsed.get_body(preferencelist=("plain",))
        html_part = parsed.get_body(preferencelist=("html",))
        html = html_part.get_content() if html_part else None
        if plain_part:
            body = plain_part.get_content()
        elif html:
            body = html_to_text(html)
        else:
            body = ""

        attachments = [
            Attachment(
                filename=part.get_filename() or "(unnamed)",
                mime_type=part.get_content_type(),
                size=len(part.get_payload(decode=True) or b""),
            )
            for part in parsed.iter_attachments()
        ]

        return Email(
            id=msg["id"],
            thread_id=msg["threadId"],
            labels=msg.get("labelIds", []),
            subject=str(parsed.get("Subject", "")),
            sender=str(parsed.get("From", "")),
            to=str(parsed.get("To", "")),
            cc=str(parsed.get("Cc", "")),
            date=str(parsed.get("Date", "")),
            body=body.strip(),
            html=html,
            attachments=attachments,
        )

    def read_thread(self, thread_id: str) -> list[Email]:
        """Fetch every message in a conversation, oldest first."""
        thread = self.users.threads().get(userId="me", id=thread_id, format="minimal").execute(num_retries=RETRIES)
        return [self.read(m["id"]) for m in thread.get("messages", [])]


def _print_email(e: Email) -> None:
    print(f"From:    {e.sender}\nTo:      {e.to}")
    if e.cc:
        print(f"Cc:      {e.cc}")
    print(f"Date:    {e.date}\nSubject: {e.subject}\nLabels:  {', '.join(e.labels)}")
    for a in e.attachments:
        print(f"Attach:  {a.filename} ({a.mime_type}, {a.size} bytes)")
    print("-" * 72)
    print(e.body)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m connectors.gmail", description="Gmail connector")
    sub = parser.add_subparsers(dest="cmd", required=True)
    au = sub.add_parser("auth", help="Run the OAuth consent flow and save token.json")
    au.add_argument("--modify", action="store_true", help="Also allow labelling and moving messages")
    ls = sub.add_parser("list", help="List messages matching a Gmail search query")
    ls.add_argument("--query", "-q", default="in:inbox")
    ls.add_argument("--max", "-n", type=int, default=10)
    rd = sub.add_parser("read", help="Print a message in full")
    rd.add_argument("message_id")
    th = sub.add_parser("thread", help="Print every message in a thread")
    th.add_argument("thread_id")
    args = parser.parse_args()

    if args.cmd == "auth":
        get_credentials(interactive=True, scopes=MODIFY_SCOPES if args.modify else READ_SCOPES)
        print(f"Authorised. Token saved to {TOKEN_FILE}")
        return

    gmail = GmailConnector()
    if args.cmd == "list":
        for m in gmail.search(args.query, args.max):
            print(f"{m['id']}  {m['date'][:25]:25}  {m['from'][:30]:30}  {m['subject']}")
    elif args.cmd == "read":
        _print_email(gmail.read(args.message_id))
    elif args.cmd == "thread":
        for i, e in enumerate(gmail.read_thread(args.thread_id)):
            if i:
                print("\n" + "=" * 72 + "\n")
            _print_email(e)


if __name__ == "__main__":
    main()
