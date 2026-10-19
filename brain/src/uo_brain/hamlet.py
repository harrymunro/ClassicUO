"""A hamlet of residents (cuo-m3n): a place agents live in, with a home each, neighbours and work.

NPCs with lives of their own (cuo-x2q) have a persona, long goals, memories and talk, and no place:
each chooses its day anywhere on the map, so they scatter and meet by chance. A fleet with a
`[hamlet]` gives its characters one:

    [hamlet]
    name = "Cove"     # a region of the world store: its area is the hamlet
    edge = 10         # tiles round it that still count as in it (its gate, the path by its walls)

    [[agent]]
    name = "Maren"
    home = "Cove healer"                 # a place of the world store, or "x,y"
    home_words = "the healer's house"    # how she speaks of it
    work = "tends the healer's house: ..."

Each resident's planner is told it lives there: its home, its neighbours by name and trade, its work,
and that its days begin and end in the hamlet (`Hamlet.words`, in the system prompt so it caches). Each
turn's situation says where it is against home and the hamlet (`Hamlet.where`), and "home" is a place
for travel_to (`Hamlet.is_home`). Its talk knows where it lives and who its neighbours are
(`Hamlet.lives`). Nothing is scripted: the planner still chooses every goal (2026-10-06).

With `--set hamlet=false` the residents are told nothing (persona and long goals only, as the
britain-lives NPCs are) and the fleet still measures them against the hamlet: the control.

The fleet samples where every resident is every 30 s (`where` records in fleet.jsonl), and
`uo-brain hamlet-report DIR` says, for each: the share of the run in the hamlet and at home, how often
it came home, its longest time away, spells of standing still away from home, its work (goals, kills,
sales, purchases, banking), its talk by neighbour, deaths and cost, and what it remembers of the others.
"""

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import logs

EDGE = 10          # tiles round the hamlet's area that still count as in it
HOME_TILES = 6     # within this many tiles of home is at home (a house's rooms and its door)
STILL_S = 600      # standing still this long away from home is a spell worth naming in the report
STILL_TILES = 3    # ...within this many tiles of where the spell began
SAMPLE_S = 30.0    # the fleet looks where each resident is this often
HOME_WORDS = re.compile(r"^\s*(?:back\s+)?(?:to\s+)?(?:my\s+|our\s+|the\s+)?(?:home|house)\b(?:\s*\(.*\))?\s*$", re.I)


def chebyshev(x1: int, y1: int, x2: int, y2: int) -> int:
    return max(abs(x1 - x2), abs(y1 - y2))


@dataclass
class Hamlet:
    """The hamlet as one resident knows it: its area, and this resident's home, work and neighbours."""
    name: str
    rects: list[tuple[int, int, int, int]]  # x1, y1, x2, y2 with x2 and y2 outside, as the world store's regions
    edge: int = EDGE
    me: str = ""
    home: tuple[int, int] | None = None
    home_words: str = ""      # "the healer's house"
    work: str = ""
    neighbours: list[dict[str, str]] = field(default_factory=list)  # name, trade, home_words, work

    # ------------------------------------------------------------ where

    def beyond(self, x: int, y: int) -> int:
        """Tiles from the hamlet's own area (0 inside it)."""
        best = None
        for x1, y1, x2, y2 in self.rects:
            dx = max(x1 - x, 0, x - (x2 - 1))
            dy = max(y1 - y, 0, y - (y2 - 1))
            d = max(dx, dy)
            best = d if best is None else min(best, d)
        return best if best is not None else 10**6

    def inside(self, x: int, y: int) -> bool:
        """In the hamlet or within its edge."""
        return self.beyond(x, y) <= self.edge

    def at_home(self, x: int, y: int) -> bool:
        return self.home is not None and chebyshev(x, y, *self.home) <= HOME_TILES

    def is_home(self, place: str | None) -> bool:
        """A travel_to place that means this resident's home: "home", "my home", "back home", "the house"."""
        return bool(place and self.home is not None and
                    (HOME_WORDS.match(place) or (self.home_words and place.strip().lower() == self.home_words.lower())))

    def where(self, x: int, y: int) -> str:
        """Where the resident is, against home and the hamlet, for each turn's situation."""
        home = f"home ({self.home_words})" if self.home_words else "home"
        if self.at_home(x, y):
            return f"at {home}, in {self.name}"
        far = chebyshev(x, y, *self.home) if self.home else None
        if self.beyond(x, y) == 0:
            return f"in {self.name}" + (f", {far} tiles from {home}" if far is not None else "")
        if self.inside(x, y):
            return f"at the edge of {self.name}" + (f", {far} tiles from {home}" if far is not None else "")
        return (f"away from {self.name}: {self.beyond(x, y)} tiles from it"
                + (f" and {far} from {home}" if far is not None else ""))

    # ------------------------------------------------------------ words

    def lives(self) -> str:
        """One line for the resident's talk: where it lives and who its neighbours are."""
        others = "; ".join(f"{n['name']}" + (f" ({n['trade']})" if n.get("trade") else "")
                           for n in self.neighbours)
        return (f"in the hamlet of {self.name}" + (f", at {self.home_words}" if self.home_words else "")
                + (f"; neighbours: {others}" if others else ""))

    def words(self) -> str:
        """The paragraph for the end of the resident's planner's system prompt."""
        me = self.me or "this character"
        home = (f"{self.home_words} ({self.home[0]},{self.home[1]})" if self.home_words else
                f"{self.home[0]},{self.home[1]}") if self.home else "none of its own"
        lines = [f"\n\nWhere {me} lives: the hamlet of {self.name}, a small place of a few houses and shops. "
                 f"{me}'s home is {home}; travel_to 'home' goes there."]
        if self.work:
            lines.append(f"{me}'s work: {self.work}")
        if self.neighbours:
            lines.append("Neighbours, who live in " + self.name + " too: " + "; ".join(
                f"{n['name']}" + (f", {n['trade']}" if n.get("trade") else "")
                + (f", at {n['home_words']}" if n.get("home_words") else "") for n in self.neighbours) + ".")
        lines.append(f"{self.name} is {me}'s life: the days begin and end there. Go out for work (a hunt nearby, "
                     "a trip to sell, buy or bank) and come back: when a piece of work is done, go home, and rest at "
                     f"home rather than out in the country. Free time is spent in {self.name}, where the neighbours "
                     "are. A long trip away (a bank in another town, a dungeon) is for a reason, not a day out. Each "
                     f"turn says where {me} is against home and the hamlet.")
        return " ".join(lines) + "\n"

    # ------------------------------------------------------------ files

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "rects": [list(r) for r in self.rects], "edge": self.edge, "me": self.me,
                "home": list(self.home) if self.home else None, "home_words": self.home_words, "work": self.work,
                "neighbours": self.neighbours}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "Hamlet":
        home = d.get("home")
        return cls(str(d["name"]), [tuple(int(v) for v in r) for r in d.get("rects") or []], int(d.get("edge", EDGE)),
                   str(d.get("me") or ""), (int(home[0]), int(home[1])) if home else None,
                   str(d.get("home_words") or ""), str(d.get("work") or ""),
                   [{str(k): str(v) for k, v in n.items()} for n in d.get("neighbours") or []])


def load(path: Path) -> Hamlet:
    return Hamlet.from_json(json.loads(Path(path).read_text()))


# ---------------------------------------------------------------- the report

def spells(samples: list[tuple[float, bool]]) -> list[tuple[float, float]]:
    """(start, end) of each run of samples that are True, by time."""
    out, start, last = [], None, None
    for t, on in samples:
        if on and start is None:
            start = t
        if not on and start is not None:
            out.append((start, last if last is not None else start))
            start = None
        last = t
    if start is not None:
        out.append((start, last))
    return out


def still_spells(wheres: list[dict[str, Any]], hamlet: Hamlet) -> list[dict[str, Any]]:
    """Spells of STILL_S or more standing within STILL_TILES of one tile away from home (a ghost's too)."""
    out = []
    i = 0
    while i < len(wheres):
        w0 = wheres[i]
        j = i
        while j + 1 < len(wheres) and chebyshev(wheres[j + 1]["x"], wheres[j + 1]["y"], w0["x"], w0["y"]) <= STILL_TILES:
            j += 1
        if wheres[j]["t"] - w0["t"] >= STILL_S and not hamlet.at_home(w0["x"], w0["y"]):
            out.append({"t": w0["t"], "minutes": round((wheres[j]["t"] - w0["t"]) / 60, 1), "x": w0["x"], "y": w0["y"],
                        "where": hamlet.where(w0["x"], w0["y"])})
        i = j + 1
    return out


def goal_at(session: list[dict[str, Any]], t: float) -> str:
    """The goal the resident was on at time t, in a few words."""
    g = None
    for x in session:
        if x.get("type") == "goal" and x["t"] <= t:
            g = x
    if not g:
        return ""
    args = {k: v for k, v in (g.get("args") or {}).items() if k != "why"}
    return f"{g.get('tool')}({', '.join(f'{k}={v}' for k, v in args.items())})"


def work(session: list[dict[str, Any]]) -> dict[str, Any]:
    """What the resident did: goals by tool, kills, gold from sales, purchases, banking, and trips by where to."""
    goals: dict[str, int] = {}
    trips: dict[str, int] = {}
    for x in session:
        if x.get("type") == "goal":
            tool = str(x.get("tool"))
            goals[tool] = goals.get(tool, 0) + 1
            if tool == "travel_to":
                place = str((x.get("args") or {}).get("place") or "")
                trips[place] = trips.get(place, 0) + 1
    sold = bought = banked = kills = 0
    for x in session:  # the session's own records of what each errand and hunt came to (session.Session.log)
        if x.get("type") != "session":
            continue
        event = x.get("event")
        if event == "hunted":
            kills += int(x.get("kills") or 0)
        elif event == "sell" and x.get("ok"):
            sold += max(int(x.get("gold") or 0), 0)
        elif event == "buy":
            bought += int(x.get("got") or 0)
        elif event == "bank" and (m := re.search(r"gold -(\d+)", str(x.get("detail", "")))):
            banked += int(m[1])
    return {"goals": dict(sorted(goals.items(), key=lambda kv: -kv[1])), "kills": kills, "gold_from_sales": sold,
            "items_bought": bought, "gold_banked": banked, "trips": dict(sorted(trips.items(), key=lambda kv: -kv[1]))}


def talk_by_neighbour(session: list[dict[str, Any]], neighbours: list[str]) -> dict[str, dict[str, int]]:
    """Lines said to and heard from each neighbour."""
    out = {n: {"said": 0, "heard": 0} for n in neighbours}
    for x in session:
        if x.get("type") != "talk":
            continue
        if x.get("said") and x.get("to") in out:
            out[x["to"]]["said"] += 1
        if x.get("heard") and x.get("who") in out:
            out[x["who"]]["heard"] += 1
    return out


def remembered(world: Any, me: str, neighbours: list[str], since: float) -> dict[str, list[str]]:
    """What the resident's memory keeps of each neighbour, from this run: memories naming them."""
    from datetime import datetime, timezone
    stamp = datetime.fromtimestamp(since, timezone.utc).isoformat(timespec="seconds")
    out: dict[str, list[str]] = {}
    for n in neighbours:
        rows = world.db.execute("SELECT kind, text FROM memories WHERE character = ? AND stale = 0 AND t >= ? "
                                "AND text LIKE ? ORDER BY t", (me, stamp, f"%{n}%")).fetchall()
        out[n] = [f"{r['kind']}: {r['text'][:200]}" for r in rows]
    return out


def report(out_dir: Path, world: Any = None) -> dict[str, Any]:
    """The hamlet report of a fleet run (cuo-m3n): each resident against the hamlet, its work and its talk."""
    from . import fleet as fleets
    records = logs.read(out_dir / "fleet.jsonl")
    head = next((r for r in records if r["type"] == "fleet"), {})
    spec = head.get("hamlet")
    if not spec:
        return {"error": f"{out_dir}: not a hamlet's run (no hamlet in fleet.jsonl's first record)"}
    base = fleets.report(out_dir)
    names = [a["name"] for a in head.get("agents") or []]
    residents: dict[str, Any] = {}
    began = min((r["t"] for r in records), default=time.time())
    for name in names:
        home = (spec.get("homes") or {}).get(name)
        h = Hamlet(spec["name"], [tuple(r) for r in spec["rects"]], int(spec.get("edge", EDGE)), name,
                   (int(home["x"]), int(home["y"])) if home else None, (home or {}).get("words", ""))
        wheres = [r for r in records if r["type"] == "where" and r.get("agent") == name and r.get("x") is not None]
        log = out_dir / f"{name}.jsonl"
        session = logs.read_session(log) if log.exists() else []
        entry: dict[str, Any] = {**{k: v for k, v in (base["agents"].get(name) or {}).items()
                                    if k in ("minutes", "kills", "deaths", "cost_usd", "cost_per_hour_usd", "error")}}
        if not wheres:
            residents[name] = {**entry, "samples": 0}
            continue
        n = len(wheres)
        inside = [(w["t"], h.inside(w["x"], w["y"])) for w in wheres]
        homes = [(w["t"], h.at_home(w["x"], w["y"])) for w in wheres]
        away = [(t, not on) for t, on in inside]
        arrivals = sum(1 for (_, a), (_, b) in zip(homes, homes[1:]) if b and not a)
        entry.update({
            "samples": n,
            "in_hamlet": round(sum(1 for _, on in inside if on) / n, 2),
            "at_home": round(sum(1 for _, on in homes if on) / n, 2),
            "came_home": arrivals,
            "longest_away_min": round(max((b - a for a, b in spells(away)), default=0) / 60, 1),
            "farthest": max(h.beyond(w["x"], w["y"]) for w in wheres),
            "still_away": [{**s, "goal": goal_at(session, s["t"])} for s in still_spells(wheres, h)],
            "work": work(session),
            "talk": talk_by_neighbour(session, [x for x in names if x != name]),
        })
        if world is not None:
            entry["remembers"] = remembered(world, name, [x for x in names if x != name], began)
        residents[name] = entry
    talked = {n: sum(1 for v in (r.get("talk") or {}).values() if v["said"] or v["heard"]) for n, r in residents.items()}
    return {"fleet": head.get("name"), "dir": str(out_dir), "hamlet": spec["name"], "told": spec.get("told", True),
            "residents": residents, "talked_with": talked,
            "together": {k: base["together"].get(k) for k in ("cost_usd", "cost_per_hour_usd", "kills", "deaths")}}


def markdown(r: dict[str, Any]) -> str:
    if "error" in r:
        return r["error"]
    t = r["together"]
    lines = [f"Hamlet {r['hamlet']} ({r['fleet']}, residents {'told' if r['told'] else 'not told: the control'}): "
             f"{len(r['residents'])} residents, {t['kills']} kills, {t['deaths']} deaths, ${t['cost_usd'] or 0:.4f} "
             f"together (${t['cost_per_hour_usd'] or 0:.3f}/h).", "",
             "| resident | minutes | in the hamlet | at home | came home | longest away (min) | farthest (tiles) | "
             "still away | talked with | $/h |", "|---|---|---|---|---|---|---|---|---|---|"]
    for name, a in r["residents"].items():
        if not a.get("samples"):
            lines.append(f"| {name} | {a.get('minutes') or ''} | no positions | | | | | | | {a.get('error', '')[:60]} |")
            continue
        cost = (a.get("cost_per_hour_usd") or {}).get("total", 0) if isinstance(a.get("cost_per_hour_usd"), dict) else 0
        lines.append(f"| {name} | {a.get('minutes')} | {a['in_hamlet']:.0%} | {a['at_home']:.0%} | {a['came_home']} | "
                     f"{a['longest_away_min']} | {a['farthest']} | {len(a['still_away'])} | "
                     f"{r['talked_with'].get(name, 0)} of {len(a['talk'])} | {cost:.3f} |")
    lines += ["", "| resident | goals | kills | gold from sales | bought | banked | deaths |", "|---|---|---|---|---|---|---|"]
    for name, a in r["residents"].items():
        if not a.get("samples"):
            continue
        w = a["work"]
        lines.append(f"| {name} | {', '.join(f'{k} {v}' for k, v in w['goals'].items())} | {w['kills']} | "
                     f"{w['gold_from_sales']} | {w['items_bought']} | {w['gold_banked']} | {a.get('deaths') or 0} |")
    lines += ["", "Talk, lines said / heard by neighbour:"]
    for name, a in r["residents"].items():
        if a.get("talk"):
            lines.append(f"- {name}: " + ", ".join(f"{n} {v['said']}/{v['heard']}" for n, v in a["talk"].items()))
    for name, a in r["residents"].items():
        for s in a.get("still_away") or []:
            lines.append(f"- {name} stood still {s['minutes']} min at {s['x']},{s['y']} ({s['where']}), on {s['goal'] or '?'}")
        for n, mems in (a.get("remembers") or {}).items():
            for m in mems[:3]:
                lines.append(f"- {name} remembers of {n}: {m}")
    return "\n".join(lines)
