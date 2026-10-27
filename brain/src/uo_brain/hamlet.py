"""A hamlet of residents (cuo-m3n): a place agents live in, with a home each, neighbours and work.

NPCs with lives of their own (cuo-x2q) have a persona, long goals, memories and talk, and no place:
each chooses its day anywhere on the map, so they scatter and meet by chance. A fleet with a
`[hamlet]` gives its characters one:

    [hamlet]
    name = "Cove"     # a region of the world store: its area is the hamlet
    edge = 10         # tiles round it that still count as in it (its gate, the path by its walls)
    lands = ["south-west of Cove Cemetery"]   # the grounds its residents work: hunting areas of the world store
    gather = "Cove healer"   # where the neighbours meet of an evening (a place, or "x,y"); gather_words names it
    day_minutes = 60         # a day of the run, and its last evening_minutes the evening
    evening_minutes = 15

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

The hamlet's lands (cuo-m3n.3) are the grounds its residents work, each a hunting area round its spawns: a
hunter whose ground is 120 tiles out is at work there, not away. The planner is told them, each turn's
situation says when the resident is on them and how long it has been away from home, and the report counts
the run in the hamlet or its lands, and whether a resident came home between one piece of work and the next.

Its evenings (cuo-m3n.5): with `gather`, a run is days of `day_minutes`, and each day's last `evening_minutes`
are its evening. Each turn tells a resident the time of day, and of an evening its planner may choose the
`evening` goal: walk to the gathering place, where the first there keeps the turns of the party's table
(table.Table, its hamlet scene) and the others sit as they come; the neighbours talk the day over, each from
its own day, and each keeps its own memories of it. Nobody is sent: a resident out hunting can miss it.

With `--set hamlet=false` the residents are told nothing (persona and long goals only, as the
britain-lives NPCs are) and the fleet still measures them against the hamlet: the control.

The fleet samples where every resident is every 30 s (`where` records in fleet.jsonl), and
`uo-brain hamlet-report DIR` says, for each: the share of the run in the hamlet and at home, how often
it came home, its longest time away, spells of standing still away from home, its work (goals, kills,
sales, purchases, banking), its talk by neighbour, deaths and cost, and what it remembers of the others.
"""

import asyncio
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
LAND_EDGE = 10     # tiles round a land's spawns that are still that land
AWAY_SAID_MIN = 5  # away from home this many minutes or more, each turn says how long
WORK_GOALS = ("hunt", "sell", "buy", "bank", "restock", "travel_to")
GATHER_S = 150.0   # the first at an evening's gathering waits this long for the others
GATHER_SOME_S = 60.0  # ...or this long once one other has come
GATHER_TILES = 8   # close enough to the gathering place to be at it
EARLY_S = 180.0    # a resident may set off for the evening this long before it begins, and waits there
HOME_WORDS = re.compile(r"^\s*(?:back\s+)?(?:to\s+)?(?:my\s+|our\s+|the\s+)?(?:home|house)\b(?:\s*\(.*\))?\s*$", re.I)
NEIGHBOUR_HOME = re.compile(r"^\s*(?:to\s+)?(?P<name>[\w-]+)'s\s+(?:home|house|place|room|door)\b", re.I)


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
    lands: list[dict[str, Any]] = field(default_factory=list)       # name, x, y, r: the grounds its residents work
    gather: dict[str, Any] | None = None  # name, x, y, words: where the neighbours meet of an evening (cuo-m3n.5)
    day_s: float = 3600.0
    evening_s: float = 900.0
    began: float | None = None    # when the run's first day began (epoch), the same for every resident
    run: str = ""                 # the run's name, for the evening table's place in the shared store
    # When this resident was last seen at home (time.monotonic), for each turn's words: not part of what it knows.
    home_at: float | None = field(default=None, compare=False)

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

    def on_lands(self, x: int, y: int) -> str | None:
        """The name of the hamlet's land (cuo-m3n.3) this tile is on, or None."""
        return next((land["name"] for land in self.lands if chebyshev(x, y, land["x"], land["y"]) <= land["r"]), None)

    def at_work_or_home(self, x: int, y: int) -> bool:
        """In the hamlet or on its lands."""
        return self.inside(x, y) or self.on_lands(x, y) is not None

    def at_home(self, x: int, y: int) -> bool:
        return self.home is not None and chebyshev(x, y, *self.home) <= HOME_TILES

    def is_home(self, place: str | None) -> bool:
        """A travel_to place that means this resident's home: "home", "my home", "back home", "the house"."""
        return bool(place and self.home is not None and
                    (HOME_WORDS.match(place) or (self.home_words and place.strip().lower() == self.home_words.lower())))

    def neighbour_home(self, place: str | None, loose: bool = False) -> tuple[int, int, str] | None:
        """A travel_to place that means a neighbour's home: "Brannoc's home"; with `loose`, also the words for it
        with or without the hamlet's name ("Cove gatehouse" for "the gatehouse by the east gate"). Maren, taking the
        watchman the bandages she had promised, asked for "Cove east gate" and "Cove gatehouse", and neither was a
        place the store knew."""
        if not place:
            return None
        homed = [n for n in self.neighbours if n.get("home")]
        if m := NEIGHBOUR_HOME.match(place):
            n = next((n for n in homed if n["name"].lower() == m["name"].lower()), None)
        elif loose:
            said = set(re.findall(r"[a-z]+", place.lower())) - {"the", "a", "to", "by", "of", self.name.lower()}
            n = next((n for n in homed if said and said <= set(re.findall(r"[a-z]+", n.get("home_words", "").lower()))),
                     None)
        else:
            n = None
        if n is None:
            return None
        x, y = (int(v) for v in n["home"].split(","))
        return x, y, f"{n['name']}'s home"

    def where(self, x: int, y: int, now: float | None = None) -> str:
        """Where the resident is, against home, the hamlet and its lands, for each turn's situation; away from home
        AWAY_SAID_MIN minutes or more, how long (the first call counts as at home: a run starts there)."""
        now = time.monotonic() if now is None else now
        home = f"home ({self.home_words})" if self.home_words else "home"
        if self.home_at is None or self.at_home(x, y):
            self.home_at = now
        if self.at_home(x, y):
            return f"at {home}, in {self.name}"
        far = chebyshev(x, y, *self.home) if self.home else None
        away = int((now - self.home_at) // 60)
        since = f"; away from home {away} minutes" if away >= AWAY_SAID_MIN else ""
        if self.beyond(x, y) == 0:
            return f"in {self.name}" + (f", {far} tiles from {home}" if far is not None else "") + since
        if self.inside(x, y):
            return f"at the edge of {self.name}" + (f", {far} tiles from {home}" if far is not None else "") + since
        if land := self.on_lands(x, y):
            return (f"on {self.name}'s lands ({land}), {self.beyond(x, y)} tiles from {self.name}"
                    + (f" and {far} from {home}" if far is not None else "") + since)
        return (f"away from {self.name}: {self.beyond(x, y)} tiles from it"
                + (f" and {far} from {home}" if far is not None else "") + since)

    # ------------------------------------------------------------ the day (cuo-m3n.5)

    def day_of(self, now: float) -> tuple[int, float]:
        """The day of the run (1 the first) and how far into it, in seconds."""
        into = max(0.0, now - (self.began if self.began is not None else now))
        return int(into // self.day_s) + 1, into % self.day_s

    def is_evening(self, now: float) -> bool:
        return self.gather is not None and self.day_of(now)[1] >= self.day_s - self.evening_s

    def until_evening(self, now: float) -> float:
        """Seconds until the evening begins: 0 in one, else until today's (tomorrow's once it is over)."""
        return 0.0 if self.is_evening(now) else self.day_s - self.evening_s - self.day_of(now)[1]

    def evening_key(self, now: float) -> str:
        return f"evening {self.day_of(now)[0]}"

    def when(self, now: float) -> str:
        """The time of day for each turn's situation, and where the neighbours meet of an evening."""
        day, into = self.day_of(now)
        left = self.day_s - into
        if self.is_evening(now):
            return (f"day {day}, evening: the neighbours gather at {self.gather['words']} to talk the day over "
                    f"(the `evening` goal), for about {int(left // 60)} more minutes")
        part = "morning" if into < (self.day_s - self.evening_s) / 2 else "afternoon"
        soon = left - self.evening_s
        return (f"day {day}, {part}; evening (the gathering at {self.gather['words']}) "
                + (f"in about {int(soon // 60)} minutes" if soon >= 60 else "in under a minute"))

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
                + (f", at {n['home_words']}" if n.get("home_words") else "") for n in self.neighbours) + "."
                + (f" travel_to \"{self.neighbours[0]['name']}'s home\" goes to a neighbour's."
                   if any(n.get("home") for n in self.neighbours) else ""))
        if self.lands:
            lines.append(f"{self.name}'s lands, where its people work: "
                         + "; ".join(f"{land['name']} (round {land['x']},{land['y']})" for land in self.lands)
                         + f". Working there is part of life in {self.name}; between one piece of work and the next, "
                         "come home.")
        if self.gather:
            lines.append(f"Of an evening the neighbours gather at {self.gather['words']} to talk the day over: the "
                         f"`evening` goal, offered then. A day here is {self.day_s / 60:.0f} minutes, and its last "
                         f"{self.evening_s / 60:.0f} are the evening.")
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
                "neighbours": self.neighbours, **({"lands": self.lands} if self.lands else {}),
                **({"gather": self.gather, "day_s": self.day_s, "evening_s": self.evening_s, "began": self.began}
                   if self.gather else {}), **({"run": self.run} if self.run else {})}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "Hamlet":
        home = d.get("home")
        return cls(str(d["name"]), [tuple(int(v) for v in r) for r in d.get("rects") or []], int(d.get("edge", EDGE)),
                   str(d.get("me") or ""), (int(home[0]), int(home[1])) if home else None,
                   str(d.get("home_words") or ""), str(d.get("work") or ""),
                   [{str(k): str(v) for k, v in n.items()} for n in d.get("neighbours") or []],
                   [{"name": str(x["name"]), "x": int(x["x"]), "y": int(x["y"]), "r": int(x["r"])} for x in d.get("lands") or []],
                   dict(d["gather"]) if d.get("gather") else None, float(d.get("day_s") or 3600.0),
                   float(d.get("evening_s") or 900.0), float(d["began"]) if d.get("began") is not None else None,
                   str(d.get("run") or ""))


def load(path: Path) -> Hamlet:
    return Hamlet.from_json(json.loads(Path(path).read_text()))


# ---------------------------------------------------------------- the evening (cuo-m3n.5)

class DayStory:
    """A resident's own day for the evening's table, from its planner's goals since the last evening."""

    def __init__(self, planner: Any = None):
        self.planner = planner
        self.since = time.time()

    def reset(self) -> None:
        self.since = time.time()

    def words(self) -> str:
        steps = [h for h in getattr(self.planner, "history", []) if h.t >= self.since and h.tool != "evening"]
        if not steps:
            return "a quiet day: nothing much done"
        done = []
        for h in steps[-8:]:
            args = {k: v for k, v in h.args.items() if k not in ("why", "x", "y")}
            res = h.result if isinstance(h.result, dict) else {}
            done.append(f"{h.tool}({', '.join(f'{k}={v}' for k, v in args.items())}): "
                        f"{'done' if res.get('ok') else 'not done'}, {str(res.get('result') or res.get('summary') or '')[:120]}")
        return "; ".join(done)


async def gathering(session: Any, table: Any, hamlet: Hamlet, stop: Any = None) -> Any:
    """A resident's `evening` goal: walk to the gathering place; the first there keeps the turns of the evening's
    table once a neighbour has come, the others sit at it as they arrive; then each keeps its own memories."""
    from .session import Result
    now = time.time()
    if hamlet.gather is None:
        return Result(False, f"{hamlet.name} has no evening gathering")
    # The turn's words floor the minutes: "in about 3 minutes" is up to 3:59, and refused at 3:01 Wenna asked four
    # times in six seconds. So the goal goes while the words say 3 or fewer, and a refusal says from when it will.
    if hamlet.until_evening(now) >= EARLY_S + 60:
        return Result(False, f"it isn't evening yet: {hamlet.when(now)}; the `evening` goal can be set off for "
                             f"from about {EARLY_S / 60:.0f} minutes before it")
    place = hamlet.gather
    r = None
    for distance in (0, 1, 2):
        r = await session.travel_to(place["words"], place["x"], place["y"], distance=distance, refuse=False)
        if r.ok:
            break
    if r is None or not r.ok:
        return Result(False, f"did not reach {place['words']}: {r.summary if r else ''}", r.data if r else {})
    # Set off a little early, as a neighbour would, and wait there: asked a minute before it began, every resident was
    # told "it isn't evening yet" three to seven times running and asked again at once.
    if 0 < (early := hamlet.until_evening(time.time())) < EARLY_S + 60:
        await session.rest(int(early) + 1)
    if not hamlet.is_evening(time.time()):
        return Result(False, f"at {place['words']}, but the evening is over: {hamlet.when(time.time())}")
    key = hamlet.evening_key(time.time())  # the evening of the arrival: a trip can outlast one
    await table.arrive()
    table.come(key)
    data: dict[str, Any] = {"evening": key, "place": place["words"]}
    tid = table.current()
    if tid is None and table.claim(key, GATHER_S + 30):
        present, waited = await seen_here(session, table, place, key, stop)
        data |= {"hosted": True, "present": present, "gather_s": round(waited)}
        if len(present) < 2:
            return Result(False, f"at {place['words']}, but no neighbour came within {GATHER_S:.0f} s", data)
        seat = asyncio.get_running_loop().create_task(table.sit(stop, wait_s=20))
        try:
            held = await table.host(present, stop)
        finally:
            tid = await seat
        data |= held
    else:
        tid = await table.sit(stop, wait_s=GATHER_S + 60)
        if tid is None:
            return Result(False, f"at {place['words']}, and nobody held the evening's talk", data)
        data |= {"sat_at": tid}
    kept = await table.reflect(tid) if tid is not None else []
    table.story.reset()
    data["memories_kept"] = len(kept)
    return Result(True, f"the evening at {place['words']} with the neighbours: talked the day over"
                        + (f" ({data.get('lines')} lines)" if data.get("lines") is not None else "")
                        + "; each keeps its own memories of it", data)


async def seen_here(session: Any, table: Any, place: dict[str, Any], key: str,
                    stop: Any = None) -> tuple[list[str], float]:
    """Who of the neighbours has come for the evening `key` and is at the gathering place, by this resident's own
    client: everyone, or one other after GATHER_SOME_S, or whoever has come by GATHER_S. One who only stands there
    (at home, or resting after an earlier table) isn't at the table."""
    began = time.time()
    others = [n for n in table.party.members if n != table.party.me]
    present: list[str] = []
    while not (stop is not None and stop.is_set()):
        snap = await session.snap()
        p = snap.get("player") or {}
        came = table.comers(key)
        present = [m["name"] for m in snap.get("mobiles") or [] if m.get("name") in others and not m.get("dead")
                   and m["name"] in came
                   and chebyshev(p.get("x", 0) + m.get("dx", 0), p.get("y", 0) + m.get("dy", 0), place["x"], place["y"])
                   <= GATHER_TILES]
        waited = time.time() - began
        if len(present) == len(others) or (present and waited >= GATHER_SOME_S) or waited >= GATHER_S:
            break
        await asyncio.sleep(3.0)
    return [table.party.me, *present], time.time() - began


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


def home_arrivals(session: list[dict[str, Any]]) -> list[tuple[float, bool]]:
    """The moments the session says the resident got home (a trip home arrived, or found it there already): the
    fleet's look at where each is comes every 30 s, and a resident who set out again within that wasn't seen at home.
    In the lands hour of 2026-10-24 Wenna arrived home 3 s before her next hunt, and the report had her not home
    between them."""
    out = []
    for x in session:
        if x.get("type") == "session" and x.get("event") == "travelled" and x.get("to") == "home" \
                and x.get("state") == "arrived":
            out.append((x["t"], True))
        elif x.get("type") == "goal_result" and x.get("tool") == "travel_to" \
                and str((x.get("result") or {}).get("result", "")).startswith("already at home"):
            out.append((x["t"], True))
    return out


def home_between_work(session: list[dict[str, Any]], homes: list[tuple[float, bool]]) -> dict[str, int]:
    """Of each piece of work and the next (goals of WORK_GOALS but a trip home or to a neighbour's), how many had the
    resident at home between them (cuo-m3n.3). A visit next door isn't going out to work: in the lands hour of
    2026-10-27 Maren's eight walks to Tamsin's and Wenna's homes, a few houses off, made eight more pieces."""
    def visit(place: str) -> bool:
        return bool(HOME_WORDS.match(place) or NEIGHBOUR_HOME.match(place))
    starts = [x["t"] for x in session if x.get("type") == "goal" and x.get("tool") in WORK_GOALS
              and not (x.get("tool") == "travel_to" and visit(str((x.get("args") or {}).get("place") or "")))]
    pairs = list(zip(starts, starts[1:]))
    return {"pairs": len(pairs), "home_between": sum(1 for a, b in pairs if any(on and a < t < b for t, on in homes))}


def evenings(session: list[dict[str, Any]]) -> dict[str, int]:
    """The evening gatherings (cuo-m3n.5): tables kept, lines said at them, memories kept of them."""
    held = [x for x in session if x.get("type") == "table" and x.get("closed")]
    said = [x for x in session if x.get("type") == "table" and x.get("said")]
    kept = [x for x in session if x.get("type") == "table" and x.get("kept") is not None]
    goals = [x for x in session if x.get("type") == "goal" and x.get("tool") == "evening"]
    return {"went": len(goals), "hosted": len(held), "lines": len(said), "memories": sum(len(x["kept"]) for x in kept)}


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


def remembered(world: Any, me: str, neighbours: list[str], since: float, until: float) -> dict[str, list[str]]:
    """What the resident's memory kept of each neighbour in this run: memories naming them, kept between its first
    record and its last, and those set aside since too (cuo-m3n.4 set each run's aside before the next, and every
    run's report showed the last run's)."""
    from datetime import datetime, timezone
    stamp = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")
    out: dict[str, list[str]] = {}
    for n in neighbours:
        rows = world.db.execute("SELECT kind, text FROM memories WHERE character = ? AND t >= ? AND t <= ? "
                                "AND text LIKE ? ORDER BY t", (me, stamp(since), stamp(until), f"%{n}%")).fetchall()
        out[n] = [f"{r['kind']}: {r['text'][:200]}" for r in rows]
    return out


def report(out_dir: Path, world: Any = None, lands: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The hamlet report of a fleet run (cuo-m3n): each resident against the hamlet, its work and its talk. `lands`:
    measure against these (land_of) whatever the run's file named, to set a run without lands beside one with."""
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
    ended = max((r["t"] for r in records), default=time.time()) + 60  # what the last reflection keeps
    for name in names:
        home = (spec.get("homes") or {}).get(name)
        h = Hamlet(spec["name"], [tuple(r) for r in spec["rects"]], int(spec.get("edge", EDGE)), name,
                   (int(home["x"]), int(home["y"])) if home else None, (home or {}).get("words", ""),
                   lands=[dict(x) for x in (lands if lands is not None else spec.get("lands") or [])])
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
        seen_home = [(w["t"], h.at_home(w["x"], w["y"])) for w in wheres]
        homes = sorted(seen_home + home_arrivals(session))  # with the arrivals between two looks
        away = [(t, not on) for t, on in inside]
        arrivals = sum(1 for (_, a), (_, b) in zip(homes, homes[1:]) if b and not a)
        entry.update({
            "samples": n,
            "in_hamlet": round(sum(1 for _, on in inside if on) / n, 2),
            "in_hamlet_or_lands": round(sum(1 for w in wheres if h.at_work_or_home(w["x"], w["y"])) / n, 2),
            "home_between_work": home_between_work(session, homes),
            "at_home": round(sum(1 for _, on in seen_home if on) / n, 2),
            "came_home": arrivals,
            "longest_away_min": round(max((b - a for a, b in spells(away)), default=0) / 60, 1),
            "farthest": max(h.beyond(w["x"], w["y"]) for w in wheres),
            "still_away": [{**s, "goal": goal_at(session, s["t"])} for s in still_spells(wheres, h)],
            "work": work(session),
            "talk": talk_by_neighbour(session, [x for x in names if x != name]),
            "evenings": evenings(session),
        })
        if world is not None:
            entry["remembers"] = remembered(world, name, [x for x in names if x != name], began, ended)
        residents[name] = entry
    talked = {n: sum(1 for v in (r.get("talk") or {}).values() if v["said"] or v["heard"]) for n, r in residents.items()}
    return {"fleet": head.get("name"), "dir": str(out_dir), "hamlet": spec["name"], "told": spec.get("told", True),
            **({"lands_given": [x["name"] for x in lands]} if lands is not None else {}),
            "residents": residents, "talked_with": talked,
            "together": {k: base["together"].get(k) for k in ("cost_usd", "cost_per_hour_usd", "kills", "deaths")}}


def markdown(r: dict[str, Any]) -> str:
    if "error" in r:
        return r["error"]
    t = r["together"]
    lines = [f"Hamlet {r['hamlet']} ({r['fleet']}, residents {'told' if r['told'] else 'not told: the control'}): "
             f"{len(r['residents'])} residents, {t['kills']} kills, {t['deaths']} deaths, ${t['cost_usd'] or 0:.4f} "
             f"together (${t['cost_per_hour_usd'] or 0:.3f}/h).", "",
             "| resident | minutes | in the hamlet | ...or its lands | at home | came home | home between work | "
             "longest away (min) | farthest (tiles) | still away | talked with | $/h |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, a in r["residents"].items():
        if not a.get("samples"):
            lines.append(f"| {name} | {a.get('minutes') or ''} | no positions | | | | | | | | | {a.get('error', '')[:60]} |")
            continue
        cost = (a.get("cost_per_hour_usd") or {}).get("total", 0) if isinstance(a.get("cost_per_hour_usd"), dict) else 0
        hb = a.get("home_between_work") or {"pairs": 0, "home_between": 0}
        lands = f"{a['in_hamlet_or_lands']:.0%}" if "in_hamlet_or_lands" in a else ""
        lines.append(f"| {name} | {a.get('minutes')} | {a['in_hamlet']:.0%} | {lands} | {a['at_home']:.0%} | {a['came_home']} | "
                     f"{hb['home_between']} of {hb['pairs']} | "
                     f"{a['longest_away_min']} | {a['farthest']} | {len(a['still_away'])} | "
                     f"{r['talked_with'].get(name, 0)} of {len(a['talk'])} | {cost:.3f} |")
    lines += ["", "| resident | goals | kills | gold from sales | bought | banked | deaths |", "|---|---|---|---|---|---|---|"]
    for name, a in r["residents"].items():
        if not a.get("samples"):
            continue
        w = a["work"]
        lines.append(f"| {name} | {', '.join(f'{k} {v}' for k, v in w['goals'].items())} | {w['kills']} | "
                     f"{w['gold_from_sales']} | {w['items_bought']} | {w['gold_banked']} | {a.get('deaths') or 0} |")
    if any((a.get("evenings") or {}).get("went") for a in r["residents"].values()):
        lines += ["", "| resident | evening goals | tables kept | lines at them | memories kept of them |", "|---|---|---|---|---|"]
        for name, a in r["residents"].items():
            e = a.get("evenings") or {}
            if a.get("samples"):
                lines.append(f"| {name} | {e.get('went', 0)} | {e.get('hosted', 0)} | {e.get('lines', 0)} | {e.get('memories', 0)} |")
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
