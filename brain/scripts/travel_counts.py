"""How travel went across many session logs (session, soak and fleet runs): set-offs and how
they failed, walk end states, stuck spots and where, time lost to them, and doors opened (from
the fight decisions' recent events, which the fight loop beside every goal logs).

  uv run python scripts/travel_counts.py LOGDIR [--since EPOCH] [--until EPOCH] [--json]

Logs from before the client said "too far" apart from "no route" are told apart here by the box
the old planner refused (over 900 tiles a side), from where the planner's last prompt put the
character.
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

POS = re.compile(r'"position": \{"x": (\d+), "y": (\d+)')
DOOR = re.compile(r"opening a door at (\d+),(\d+)")
STUCK_MS = 8000  # no progress for this long counts as stuck


def read(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(errors="replace").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


def old_box_too_far(start: tuple[int, int], goal: tuple[int, int]) -> bool:
    d = max(abs(start[0] - goal[0]), abs(start[1] - goal[1]))
    margin = 24 + d // 4
    return abs(start[0] - goal[0]) + 2 * margin > 900 or abs(start[1] - goal[1]) + 2 * margin > 900


def counts(root: Path, since: float = 0.0, until: float = float("inf")) -> dict:
    set_off: Counter = Counter()
    ends: Counter = Counter()
    spots: Counter = Counter()
    one_step = {"walks": 0, "seconds": 0}
    walks = stuck_walks = stuck_spots = seconds = files = 0
    for f in sorted(root.rglob("*.jsonl")):
        if f.name.endswith(".decisions.jsonl"):
            continue
        recs = [r for r in read(f) if since <= r.get("t", 0) < until]
        if not any(r.get("event") in ("travel", "travelled") for r in recs):
            continue
        files += 1
        pos, last = None, None
        for r in recs:
            if r.get("type") == "planner":
                for m in r.get("messages") or []:
                    if isinstance(m, dict) and (hit := POS.findall(str(m.get("content", "")))):
                        pos = tuple(map(int, hit[-1]))
            if r.get("type") != "session":
                continue
            if r.get("event") == "travel":
                detail = str(r.get("detail") or "")
                if r.get("status") == "done":
                    kind = "walked"
                elif detail.startswith("no route") and "to" in r and (pos is None or old_box_too_far(pos, (r["x"], r["y"]))) \
                        and ":" not in detail:
                    kind = "too far"  # the old client said "no route" for both
                else:
                    kind = f"{r.get('status')}: {detail.split(':')[0]}"
                set_off[kind] += 1
                last = r
            elif r.get("event") == "travelled":
                state = r.get("state", "?")
                ends["timed out" if state == "walking" else state] += 1
                walks += 1
                seconds += r.get("seconds", 0)
                n = len(r.get("stuck_at") or [])
                stuck_spots += n
                stuck_walks += n > 0
                for s in r.get("stuck_at") or []:
                    spots[tuple(s)] += 1
                if last and str(last.get("detail")) == "1 tiles" and n:
                    one_step["walks"] += 1
                    one_step["seconds"] += r.get("seconds", 0)
                if last:
                    pos = (last["x"], last["y"])

    doors: Counter = Counter()
    retries = 0
    for f in sorted(root.rglob("*.decisions.jsonl")):
        prev: list = []
        tried: dict = {}
        for d in read(f):
            if not since <= d.get("t", 0) < until:
                continue
            ev = (d.get("state") or {}).get("recent_events")
            if not isinstance(ev, list) or not ev:
                continue
            # recent_events is a window of the journal: the new lines follow its overlap with the last one.
            k = next((n for n in range(min(len(prev), len(ev)), 0, -1) if prev[-n:] == ev[:n]), 0)
            for e in ev[k:] if prev else ev:
                if m := DOOR.search(str(e)):
                    at = (int(m[1]), int(m[2]))
                    doors[at] += 1
                    retries += at in tried and d["t"] - tried[at] < 60
                    tried[at] = d["t"]
            prev = ev
    return {
        "files": files, "set_off": dict(set_off), "walks": walks, "ends": dict(ends), "walk_minutes": round(seconds / 60, 1),
        "stuck_walks": stuck_walks, "stuck_spots": stuck_spots, "stuck_seconds_at_least": stuck_spots * STUCK_MS // 1000,
        "one_step_plans_stuck": one_step, "stuck_spots_where": [[list(k), v] for k, v in spots.most_common(8)],
        "doors_opened": sum(doors.values()), "doors": len(doors), "door_retries_within_60s": retries,
    }


if __name__ == "__main__":
    since = float(sys.argv[sys.argv.index("--since") + 1]) if "--since" in sys.argv else 0.0
    until = float(sys.argv[sys.argv.index("--until") + 1]) if "--until" in sys.argv else float("inf")
    r = counts(Path(sys.argv[1]), since, until)
    if "--json" in sys.argv:
        print(json.dumps(r))
    else:
        for k, v in r.items():
            print(f"{k}: {v}")
