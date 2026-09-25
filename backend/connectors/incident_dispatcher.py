"""Incident dispatcher: uploads candidate alert emails to an FTP server as text files.

Every poll it finds emails labelled "candidates" (by the alert watcher), writes each
one as a .txt file (incident date, email headers and full content), uploads it, then
moves the email from "candidates" to "dispatched" so it is never sent twice. When the
analysis report for a file arrives, the alert watcher moves that email on to
"analyzed". It only depends on those Gmail labels, so it runs independently of
incident analysis. Label names come from alert_filters.json when it exists.

Configure in backend/.env: FTP_HOST, FTP_PORT, FTP_USER, FTP_PASSWORD, FTP_DIR, FTP_TLS.

Usage (from backend/):
    python -m connectors.incident_dispatcher preview <message_id>   # print the file, upload nothing
    python -m connectors.incident_dispatcher once                   # dispatch what is waiting
    python -m connectors.incident_dispatcher watch                  # keep dispatching
"""
from __future__ import annotations

import argparse
import ftplib
import io
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from .gmail import MODIFY_SCOPES, Email, GmailConnector

log = logging.getLogger("incident_dispatcher")


class DispatchError(Exception):
    """Retrying cannot help: bad FTP login, TLS not supported, missing settings."""


@dataclass
class FtpConfig:
    host: str
    port: int = 21
    user: str = ""
    password: str = ""
    directory: str = "/incidents"
    tls: bool = True
    timeout: int = 30

    @classmethod
    def from_settings(cls, settings) -> "FtpConfig":
        if not settings.ftp_host:
            raise DispatchError("FTP_HOST is empty. Set the FTP_* values in backend/.env.")
        return cls(
            host=settings.ftp_host, port=settings.ftp_port, user=settings.ftp_user,
            password=settings.ftp_password, directory=settings.ftp_dir, tls=settings.ftp_tls,
        )


# ------------------------------------------------------------------ file content
_BODY_ISO = re.compile(r"\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)\b")
_SUBJECT_TS = re.compile(r"\|\s*(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})\s*$")
_OBJECT = re.compile(r"^\s*Objekt:\s*(\S+)", re.M | re.I)


def incident_date(email: Email) -> tuple[datetime, str]:
    """When the incident happened, and where that came from."""
    if m := _BODY_ISO.search(email.body):
        return datetime.fromisoformat(m.group(1).replace("Z", "+00:00")), "alert body"
    if m := _SUBJECT_TS.search(email.subject):
        return datetime.fromisoformat(m.group(1).replace(" ", "T")).replace(tzinfo=timezone.utc), "subject"
    try:
        return parsedate_to_datetime(email.date).astimezone(timezone.utc), "email date"
    except (TypeError, ValueError):
        return datetime.now(timezone.utc), "time of dispatch (no date found)"


def _slug(text: str, limit: int = 60) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")[:limit] or "incident"


def _iso(when: datetime) -> str:
    spec = "milliseconds" if when.microsecond else "seconds"
    return when.astimezone(timezone.utc).isoformat(timespec=spec).replace("+00:00", "Z")


def render(email: Email) -> tuple[str, str]:
    """(filename, text) for one candidate email."""
    when, source = incident_date(email)
    obj = _OBJECT.search(email.body)
    name = f"{when:%Y%m%dT%H%M%SZ}_{_slug(obj.group(1) if obj else email.subject)}_{email.id}.txt"
    lines = [
        f"Incident date: {_iso(when)}  (from {source})",
        f"Gmail ID:      {email.id}",
        f"Thread ID:     {email.thread_id}",
        f"Message-ID:    {email.message_id}",
        f"From:          {email.sender}",
        f"To:            {email.to}",
    ]
    if email.cc:
        lines.append(f"Cc:            {email.cc}")
    lines += [
        f"Date:          {email.date}",
        f"Subject:       {email.subject}",
    ]
    for a in email.attachments:
        lines.append(f"Attachment:    {a.filename} ({a.mime_type}, {a.size} bytes)")
    lines += ["=" * 72, email.body, ""]
    return name, "\n".join(lines)


# ------------------------------------------------------------------ FTP
def _connect(cfg: FtpConfig) -> ftplib.FTP:
    ftp = ftplib.FTP_TLS(timeout=cfg.timeout) if cfg.tls else ftplib.FTP(timeout=cfg.timeout)
    ftp.connect(cfg.host, cfg.port)
    try:
        ftp.login(cfg.user or "anonymous", cfg.password)
    except ftplib.error_perm as exc:
        ftp.close()
        text = str(exc)
        if cfg.tls and not text.startswith("530"):
            raise DispatchError(f"FTP server refused TLS ({text}). If it has no TLS, set FTP_TLS=false.") from exc
        raise DispatchError(f"FTP login failed for user '{cfg.user}': {text}") from exc
    if cfg.tls:
        ftp.prot_p()  # encrypt file transfers too, not just the login
    _enter_dir(ftp, cfg.directory)
    return ftp


def _enter_dir(ftp: ftplib.FTP, directory: str) -> None:
    if directory.startswith("/"):
        ftp.cwd("/")
    for part in [p for p in directory.split("/") if p]:
        try:
            ftp.cwd(part)
        except ftplib.error_perm:
            ftp.mkd(part)
            ftp.cwd(part)


def _upload(ftp: ftplib.FTP, name: str, text: str) -> None:
    """Upload under a temporary name, then rename, so readers never see a half-written file."""
    temp = name + ".part"
    ftp.storbinary(f"STOR {temp}", io.BytesIO(text.encode("utf-8")))
    try:
        ftp.rename(temp, name)
    except ftplib.error_perm:  # some servers won't rename over an existing file
        ftp.delete(name)
        ftp.rename(temp, name)


# ------------------------------------------------------------------ dispatcher
class IncidentDispatcher:
    dry_run = False

    def __init__(
        self,
        ftp: FtpConfig,
        gmail: GmailConnector | None = None,
        candidate_label: str = "candidates",
        dispatched_label: str = "dispatched",
        poll_seconds: int = 60,
    ):
        self.ftp = ftp
        self.gmail = gmail or GmailConnector(scopes=MODIFY_SCOPES)
        self.candidate_label = candidate_label
        self.dispatched_label = dispatched_label
        self.poll_seconds = poll_seconds

    def query(self) -> str:
        return f"label:{_query_label(self.candidate_label)} -label:{_query_label(self.dispatched_label)}"

    def _tidy(self) -> dict:
        """A dispatched email is no longer a candidate: drop "candidates" wherever both labels are set
        (e.g. emails dispatched before candidates were moved out on dispatch)."""
        both = self.gmail.list_ids(f"label:{_query_label(self.candidate_label)} label:{_query_label(self.dispatched_label)}")
        if not both:
            return {}
        self.gmail.relabel(both, remove=[self.gmail.label_id(self.candidate_label)])
        log.info("Moved %d already-dispatched email(s) out of %s", len(both), self.candidate_label)
        return {"tidied": len(both)}

    def check_connection(self) -> None:
        """Log in once so a wrong host/password is reported when the dispatcher is switched on."""
        _connect(self.ftp).quit()

    def run_once(self) -> dict:
        counts = self._tidy()
        ids = self.gmail.list_ids(self.query())
        if not ids:
            return counts
        dispatched_label = self.gmail.label_id(self.dispatched_label)
        candidate_label = self.gmail.label_id(self.candidate_label)
        sent, failed, error = 0, 0, None
        ftp = _connect(self.ftp)
        try:
            for message_id in reversed(ids):  # oldest first
                email = self.gmail.read(message_id)
                name, text = render(email)
                try:
                    _upload(ftp, name, text)
                except (ftplib.Error, OSError) as exc:
                    failed += 1
                    error = f"Upload of {name} failed: {exc}"
                    log.warning(error)
                    if isinstance(exc, OSError):  # connection lost: try the rest next poll
                        break
                    continue
                # Move only after a successful upload, so a failure stays a candidate and is retried.
                self.gmail.relabel([message_id], add=[dispatched_label], remove=[candidate_label])
                sent += 1
                log.info("DISPATCHED %s -> %s/%s", message_id, self.ftp.directory.rstrip("/"), name)
        finally:
            try:
                ftp.quit()
            except (ftplib.Error, OSError):
                ftp.close()
        counts["dispatched"] = sent
        if failed:
            counts["failed"] = failed
            counts["error"] = error
        log.info("Dispatch pass: %s", {k: v for k, v in counts.items() if k != "error"})
        return counts


def _query_label(name: str) -> str:
    return re.sub(r"[\s/]+", "-", name)


def build_from_settings(settings) -> IncidentDispatcher:
    """Dispatcher using the FTP settings in backend/.env and the label names in alert_filters.json."""
    candidate_label, dispatched_label = "candidates", "dispatched"
    try:
        from .gmail_watcher import Filters
        filters = Filters.load()
        candidate_label, dispatched_label = filters.candidate_label, filters.dispatched_label
    except FileNotFoundError:
        pass
    dispatcher = IncidentDispatcher(
        FtpConfig.from_settings(settings),
        candidate_label=candidate_label,
        dispatched_label=dispatched_label,
        poll_seconds=settings.dispatch_poll_seconds,
    )
    dispatcher.check_connection()
    return dispatcher


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m connectors.incident_dispatcher",
                                     description="Upload candidate alert emails to FTP")
    sub = parser.add_subparsers(dest="cmd", required=True)
    pv = sub.add_parser("preview", help="Print the text file for one email; uploads nothing")
    pv.add_argument("message_id")
    sub.add_parser("once", help="Dispatch every waiting candidate once and exit")
    wa = sub.add_parser("watch", help="Keep dispatching new candidates")
    wa.add_argument("--interval", type=int, help="Seconds between polls (default: DISPATCH_POLL_SECONDS)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.cmd == "preview":
        name, text = render(GmailConnector().read(args.message_id))
        print(f"--- {name}\n{text}")
        return

    from bridge.config import get_settings
    dispatcher = build_from_settings(get_settings())
    if args.cmd == "once":
        dispatcher.run_once()
        return
    interval = args.interval or dispatcher.poll_seconds
    log.info("Dispatching every %ss: %s -> ftp://%s%s", interval, dispatcher.query(), dispatcher.ftp.host, dispatcher.ftp.directory)
    try:
        while True:
            dispatcher.run_once()
            time.sleep(interval)
    except KeyboardInterrupt:
        log.info("Stopped.")


if __name__ == "__main__":
    main()
