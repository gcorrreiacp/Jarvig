"""Which J.A.R.V.I.G. features are in beta, read from backend/features.json.

That file is the single place to change: set a feature's "status" from "beta" to
"stable" and its beta look (violet HUD, reply prefix, BETA tag) is gone. It is
re-read whenever it changes, so no restart is needed.

    python -m bridge.features        # list every feature and its status
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("features")

FEATURES_FILE = Path(__file__).resolve().parent.parent / "features.json"
STATUSES = ("beta", "stable")
KINDS = ("skill", "service", "mode")


@dataclass(frozen=True)
class Feature:
    key: str
    title: str
    kind: str
    status: str
    since: str = ""
    ready_when: str = ""

    @property
    def beta(self) -> bool:
        return self.status == "beta"


_cache: tuple[tuple[Path, float], dict[str, Feature]] | None = None


def load(path: Path | None = None) -> dict[str, Feature]:
    """All features, re-read when the file changes. Raises ValueError on a mistake in the file."""
    global _cache
    path = path or FEATURES_FILE
    mtime = path.stat().st_mtime
    if _cache and _cache[0] == (path, mtime):
        return _cache[1]
    raw = json.loads(path.read_text())
    features = {}
    for key, item in raw.items():
        if key.startswith("_"):
            continue
        feature = Feature(key=key, title=item["title"], kind=item.get("kind", "skill"), status=item["status"],
                          since=item.get("since", ""), ready_when=item.get("ready_when", ""))
        if feature.status not in STATUSES:
            raise ValueError(f'features.json: "{key}" has status "{feature.status}"; use one of {STATUSES}')
        if feature.kind not in KINDS:
            raise ValueError(f'features.json: "{key}" has kind "{feature.kind}"; use one of {KINDS}')
        features[key] = feature
    _cache = ((path, mtime), features)
    return features


def get(key: str) -> Feature:
    try:
        return load()[key]
    except KeyError:
        raise KeyError(f'"{key}" is not listed in backend/features.json') from None


def is_beta(key: str) -> bool:
    """True if the feature is in beta. An unknown key, or a broken features.json, counts as
    beta: a feature is never presented as finished by mistake, and a typo in the file can't
    break the HUD."""
    try:
        return get(key).beta
    except KeyError:
        return True
    except (OSError, ValueError) as exc:
        log.warning("Treating %s as beta: %s", key, exc)
        return True


def in_beta() -> list[Feature]:
    return [f for f in load().values() if f.beta]


def main() -> None:
    features = load()
    width = max(len(f.title) for f in features.values())
    for f in sorted(features.values(), key=lambda f: (f.status, f.title)):
        line = f"{f.title:<{width}}  {f.status.upper():6}  {f.kind:7}  since {f.since or '?'}"
        print(line + (f"\n{'':<{width}}  ready when: {f.ready_when}" if f.beta and f.ready_when else ""))


if __name__ == "__main__":
    main()
