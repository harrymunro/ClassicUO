"""Reading the brain's JSONL logs back, for the outcome importer and the after-action review.

The logs were written for people and the benchmark, so some numbers sit inside words: the
character's health is "wounded (57%)", a creature is "Vitavi (a ratman)", a fight decision's
note is "keep fighting an orc (0.82)". These helpers take them back out.

`uo-brain session` writes goals and session events to its log and Jev's decisions to
`<log>.decisions.jsonl` next to it; `uo-brain run` writes both to one file. read_session()
reads either way. Lines that don't parse are skipped, so a log cut off by a crash still reads.
"""

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DECISIONS = ".decisions"


def read(path: Path | str) -> list[dict[str, Any]]:
    out = []
    for line in Path(path).read_text(errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and "type" in rec:
            out.append(rec)
    return out


def companion(path: Path) -> Path:
    """The other file of a `uo-brain session` pair: s.jsonl <-> s.decisions.jsonl."""
    if path.stem.endswith(DECISIONS):
        return path.with_name(path.stem.removesuffix(DECISIONS) + path.suffix)
    return path.with_name(path.stem + DECISIONS + path.suffix)


def read_session(path: Path | str) -> list[dict[str, Any]]:
    """A log's records together with its companion file's, oldest first."""
    path = Path(path)
    recs = read(path)
    other = companion(path)
    if other.exists():
        recs += read(other)
    return sorted(recs, key=lambda r: r.get("t") or 0.0)


def session_name(path: Path | str) -> str:
    """The name both files of a session share: the session log's file name."""
    path = Path(path)
    return companion(path).name if path.stem.endswith(DECISIONS) else path.name


def clock(t: float | None) -> str:
    return time.strftime("%H:%M:%S", time.localtime(t)) if t else "?"


def health_pct(state: dict[str, Any]) -> int | None:
    m = re.search(r"\((\d+)%\)", str((state.get("you") or {}).get("health", "")))
    return int(m[1]) if m else None


def supplies(state: dict[str, Any]) -> tuple[int | None, int | None]:
    """(bandages, heal potions) left. A mage's state has no bandage count."""
    you = state.get("you") or {}
    return you.get("bandages_left"), you.get("heal_potions_left")


def kit_of(state: dict[str, Any]) -> str:
    """A mage's state carries its mana; a warrior's doesn't."""
    return "mage" if "mana" in (state.get("you") or {}) else "warrior"


def creature_kind(name: str) -> str:
    """What a creature is, from the name the state gave it: "Vitavi (a ratman)" -> "ratman",
    "an ogre lord" -> "ogre lord". A personal name the client knew no body for stays as it is."""
    name = " ".join(str(name).split())
    m = re.search(r"\((?:a|an) ([^()]+)\)\s*$", name)
    if m:
        return m[1].lower()
    return re.sub(r"^(?:a|an|the) ", "", name, flags=re.I).lower()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def is_close(hostile: dict[str, Any]) -> bool:
    """Within 3 tiles: the state says "adjacent" (1) or "close" (2-3)."""
    return str(hostile.get("distance", "")).startswith(("adjacent", "close"))


_FIGHT_NOTE = re.compile(r"^(?:keep fighting|fight) (.+?)(?: with [A-Za-z ]+|, Protection first)? \(\d")


def fight_target(decision: dict[str, Any]) -> tuple[int, str] | None:
    """(serial, name) of the creature a fight decision attacked, or None. The name comes
    from the note policy.py writes ("fight NAME (0.82)", "keep fighting NAME (0.82)")."""
    if decision.get("intent") != "fight" or not decision.get("target"):
        return None
    m = _FIGHT_NOTE.match(decision.get("note") or "")
    return (decision["target"], m[1]) if m else None


@dataclass
class Run:
    """One stretch of the tactical loop: the decisions up to the summary it ended with
    (None when the log stops first, as after a crash)."""

    decisions: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] | None = None

    @property
    def stats(self) -> dict[str, Any]:
        return (self.summary or {}).get("client_stats") or {}


def runs(records: list[dict[str, Any]]) -> list[Run]:
    out: list[Run] = []
    cur = Run()
    for r in records:
        if r["type"] == "decision":
            cur.decisions.append(r)
        elif r["type"] == "summary":
            cur.summary = r
            out.append(cur)
            cur = Run()
    if cur.decisions:
        out.append(cur)
    return out
