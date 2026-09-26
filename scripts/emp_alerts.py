#!/usr/bin/env python3
"""Mock MeinePlattform (EMP Alarm v2.0) alert generator for the Jarvis dojo (Brevo edition).
  --history N       send N alerts with fired times spread over the last 30 days
  --live N          send N alerts fired now
  --metric NAME     only generate this metric (full name or unique part, e.g. KafkaConsumerLag)
  --list-metrics    print the available metrics and exit
  --to ADDR         recipient (default: jarvig120@gmail.com, or $DOJO_ALERTS)
  --dry-run         print the emails instead of sending them
Sending needs DOJO_SENDER (a sender verified in Brevo), DOJO_SMTP_USER and DOJO_SMTP_KEY
in the environment or in a .env file next to this script.
"""
import argparse, os, random, smtplib, time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

def load_dotenv():
    """Read KEY=value lines from .env next to this script or in the cwd; real env vars win."""
    for path in {os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), ".env"}:
        if not os.path.isfile(path):
            continue
        for line in open(path):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.removeprefix("export ").split("=", 1)
            os.environ.setdefault(key.strip(), val.strip().strip("'\""))

load_dotenv()
DEFAULT_TO = os.environ.get("DOJO_ALERTS", "jarvig120@gmail.com")
GROUPS = ["cg_slitherine", "cg_hufflepuff", "cg_avengers", "cg_gryffindor", "cg_ravenclaw"]
JOBS = ["thestuff-job", "das-batch-job", "nightly-export-job", "abrechnung-job"]
STAGES = ["PROD"] * 8 + ["ABN", "TEST"]                         # mostly PROD, some noise

# (metric, value generator, description, object pool)
METRICS = [
    ("sampler.KafkaConsumerLag", lambda: random.randint(5000, 250000),
     "Anzahl nicht verarbeiteter Nachrichten im Consumer", ["my-kafka-consumer"]),
    ("sampler.GenericJobErfolgloseAusfuehrung", lambda: 0,
     "Letzte Ausfuehrung des Jobs war nicht erfolgreich", JOBS),
    ("sampler.GenericJobLaufzeit", lambda: random.choice([0, random.randint(3600, 20000)]),
     "Laufzeit des letzten Laufs des Jobs in Sekunden", JOBS),
    ("sampler.cool-vertical.some-feature.FehlerhafteAbrufe", lambda: random.randint(1, 40),
     "Existieren Vermittler mit fehlerhaftem Aequivalenzdatenabruf", ["cool-vertical"]),
]

def pick_metric(name):
    """Return the METRICS entries matching name (exact or substring), or all of them."""
    if not name:
        return METRICS
    exact = [m for m in METRICS if m[0] in (name, f"sampler.{name}")]
    found = exact or [m for m in METRICS if name.lower() in m[0].lower()]
    if len(found) != 1:
        names = ", ".join(m[0] for m in (found or METRICS))
        raise SystemExit(f"--metric {name!r} matches {'nothing' if not found else 'several'}; pick one of: {names}")
    return found

def emp_alert(fired, metrics=METRICS):
    metric, value, desc, pool = random.choice(metrics)
    stage = random.choice(STAGES)
    obj = f"{random.choice(pool)}_{stage.lower()}"
    status = "FEHLER" if random.random() < 0.8 else "OK"          # some recoveries
    v = value()
    body = (f"MeinePlattform ALERT\n\n"
            f"Stage:   {stage}\n"
            f"Status: {status}\n"
            f"Objekt: {obj}\n"
            f"Metrik:  {metric}\n"
            f"Wert:    {v}\n"
            f"Beschreibung:  {desc}\n\n"
            f"________________________________________\n\n"
            f"EMP Alarm v2.0 | {fired:%Y-%m-%dT%H:%M:%S}.{fired.microsecond // 1000:03d}Z"
            f" | Contact Group(s): {random.choice(GROUPS)}\n")
    subject = f"MeinePlattform ALERT {stage} {status}: {obj} {metric} | {fired:%Y-%m-%d %H:%M:%S}"
    return subject, body

def build(sender, to, fired, metrics=METRICS):
    subject, body = emp_alert(fired, metrics)
    m = EmailMessage()
    m["From"], m["To"] = formataddr(("EMP Alarm", sender)), to
    m["Subject"] = subject                                          # unique subject, no Gmail threading
    m["Message-ID"] = make_msgid()
    m.set_content(body)
    return m

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--history", type=int, default=0)
    p.add_argument("--live", type=int, default=0)
    p.add_argument("--metric")
    p.add_argument("--list-metrics", action="store_true")
    p.add_argument("--to", default=DEFAULT_TO)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    if a.list_metrics:
        for m in METRICS:
            print(m[0])
        return
    metrics = pick_metric(a.metric)
    now = datetime.now(timezone.utc)
    times = [now - timedelta(seconds=random.randint(0, 30 * 24 * 3600)) for _ in range(a.history)]
    times = sorted(times) + [now] * a.live
    sender = os.environ.get("DOJO_SENDER", "sender@example.com")
    msgs = [build(sender, a.to, t, metrics) for t in times]
    if a.dry_run:
        for m in msgs:
            print(f"Subject: {m['Subject']}\n\n{m.get_content()}\n{'=' * 60}")
        return
    with smtplib.SMTP("smtp-relay.brevo.com", 587) as smtp:
        smtp.starttls(); smtp.login(os.environ["DOJO_SMTP_USER"], os.environ["DOJO_SMTP_KEY"].strip())
        for m in msgs:
            smtp.send_message(m)
            time.sleep(1)                                           # stay well below Brevo rate limits
    print(f"sent {len(msgs)} alerts to {a.to}")

if __name__ == "__main__":
    main()
