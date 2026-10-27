"""Going in together (cuo-cym.1): a party forms up at the edge of a hunt and enters as one, and the
place it formed up at is where it falls back to.

On the lich scene the party's deaths came in the first minute of a fight all five had reached, and
the logs of 2026-10-11 show how. The leader's hunt walked to the centre of the spawn, through the
wave, each follower two seconds behind it on a path of its own. The warrior met the lich and the
bone knights with the others 8 tiles and more behind (100% to 42% in half a second in one run).
Then the ones who left (a mage at 89%, an archer at 94%) ran each along its own line away, out of
the 10 tiles that make a party in the odds, and their hunts ended "left the area", which refuses
them that place for half an hour: the warrior fought the second bone knight with one companion
near and died of it.

So before a hunt a party forms up:

- The leader walks to the edge of the hunting area (EDGE_TILES outside the spawn's own spread), or
  stops sooner when something hostile comes within SIGHT_TILES, and waits there for the others:
  everyone alive, heard from, near enough and not on business of its own, within GATHER_TILES of
  it, for WAIT_S at most. A fight that starts ends the wait.
- The others close up on the leader (the client's follow) instead of each walking to the centre.
- Then the leader gives the word (party_rally, state "go"), and everyone sets off at once: whoever
  takes the blows (the party's point: its first warrior or paladin) waits a beat for the others' fight
  loops to start, and then never goes more than a few tiles ahead of the nearest of them (policy).
- With creatures in view from where they formed up, the hunt starts there: nobody walks on to the
  centre of the spawn through them.

The place is kept as the party's rally point for the fight that follows. A member that leaves
runs there, not along its own line away (policy.fall_back), and stands with the others when they
are there; only a leave from the rally itself ends its hunt.

Where the leader stopped is where it happened to stop, and formed up the party fights there: in
the runs of 2026-10-13 the warrior stood 1 to 8 tiles from the rally point, so a fall back was a
few steps to the place the wave arrives at, and he died there twice. With `by_map` (cuo-cym.13)
the rally point is chosen instead (`choose`): a few tiles back the way the party came, on ground
the client's map says can be walked to, with a way out behind it that leads away from the hunt,
and with a second point further back for one who is badly hurt at the first (policy.goes_on). The
party still forms up where the leader stopped.

A party that plans by huddle (huddle.py) has no leader's goal to form up for: each member's own
planner chooses its hunt, half a minute apart in the runs of 2026-10-11 (cuo-cym.5). There the
first to reach the edge holds the rally (`gather`), the others close up on it, and it waits for
those whose own reports say they are on their way to the same hunt, and for those the plan sends
there (the blackboard's `going`) whatever they have said yet (cuo-cym.14). In the runs of
2026-10-13 a bard was first at the edge twice. Once he heard of nobody in his four seconds and
went in, with the mage 9 s and the archer 15 s behind him; once he was made the point with the
warrior a second behind, on a walk to the graveyard that his report didn't call a hunt. So one who
can't take blows doesn't give the word while a fighter it waits for is still coming (FIGHTER_S).
"""

from __future__ import annotations

import asyncio
import heapq
import math
import time
from collections.abc import Callable, Iterable
from typing import Any

from .party import Party
from .state import threatening
from .world import tiles

EDGE_TILES = 8        # the leader stops this far outside the spawn's own spread
SIGHT_TILES = 13      # ...or sooner, with something hostile this close: nearer, and it is on the first one in
GATHER_TILES = 4      # a member this close to the leader is with it
WAIT_S = 25.0         # how long the leader waits at the edge for the others
SET_OUT_S = 4.0       # with a planner each: how long the first there gives the others to say they have set out
FIGHTER_S = 45.0      # ...and how long one who can't take blows waits for a fighter who is coming: longer than for
#                       the others, and under their own wait for the word (JOIN_WAIT_S)
FAR_TILES = 60        # one further off than this is on business of its own: not waited for
HEARD_S = 30.0        # ...and so is one not heard from for this long
POINT_WAITS_S = 1.2   # the one who takes the blows sets off this long after the word: the others hear it up to
#                       half a second late, and each fight loop takes a moment to start (live, the others' first
#                       move came 1.5 s after the warrior's, which at a run is eight tiles)
JOIN_WAIT_S = 60.0    # once the rally is held, a follower waits this long for the word, then goes in as before
TRIP_WAIT_S = 600.0   # ...and before that, while the leader is still on its way there, this long (a trip's own limit)
NEAR_TILES = 10       # a member this close to the rally point when the word has been given goes in from where it is
REACH_TILES = 30      # sightings count from this far outside the edge: a creature by the road on the way isn't the hunt
REUSE_TILES = 40      # a leader already inside the area keeps the party's last rally point when it is this near
RECALL_TILES = 40     # a hunt further off than this, with the leader out of view: its own way there first
HOLDS_S = 900.0       # a rally point is where the party falls back to for this long after it went in
POINT = ("warrior", "paladin", "warrior-mage")  # who can take the blows
# A rally point chosen from the map (cuo-cym.13).
BACK_TILES = 5        # it is this far back from where the party formed up, the way it came: behind those gathered on
#                       the leader (GATHER_TILES), and near enough to be one place with them
SECOND_TILES = 12     # ...and the second point this much further back again: one leg of a run (policy.RALLY_LEG)
NUDGE_TILES = 3       # a point may be put this far from the place aimed at, to find ground that will do
OUT_TILES = 8         # a point has a way out when a walk from it gets this much further from the hunt
ROOM = 5              # ...and room for a party when this many of the nine tiles round it can be stood on
MAP_TILES = 32        # how far round the leader the client's map is asked for: the second point and its way out
MAP_S = 3.0           # ...and how long the client is given to answer: the party stands at the edge meanwhile
SPAWN_TILES = 120     # ...and how far round the hunt the world store's other spawns are looked for
# A plan that holds the party back until it has seen the ground (a huddle's, cuo-cym.14): the party looks from the edge.
LOOK_S = 40.0         # how long the one holding the rally looks before the plan is made again with what was seen
LOOK_EVERY_S = 4.0    # ...asking again this often when what is in view has changed
LOOK_CUT = 0.5        # system one's yes to "does what they see meet the plan's condition" at or above this: in
LOOK_COMING_TILES = 8  # ...and something coming at the party this close ends the look: the fight has come to them
TRAIL = 80            # the leader's last steps on the way in that are kept (one each 0.4 s of its walk)
JUMP_TILES = 20       # two of them further apart than this: a recall, and what came before isn't the way in


def point_of(members: list[str], stats: dict[str, dict[str, Any]], leader: str) -> str:
    """Who goes in a step ahead: the first of the party, in its own order, alive and able to take the
    blows; the leader when it has nobody like that."""
    for name in members:
        st = stats.get(name)
        if st and not st.get("dead") and st.get("plays_as") in POINT and st.get("age_s", 0) < HEARD_S:
            return name
    return leader


def expected(stats: dict[str, dict[str, Any]], me: str, at: tuple[int, int], hunting: str | None = None,
             sent: list[str] | None = None) -> list[str]:
    """The members a party forming up waits for: alive, heard from lately, not on business of their own
    (a resurrection, a corpse, a restock), and near enough to come. hunting: only those whose own report
    says they are on that hunt (a party whose members each plan for themselves), and those its plan sends
    there (`sent`), who may not have said so yet."""
    return sorted(n for n, st in stats.items()
                  if n != me and not st.get("dead") and not st.get("busy") and st.get("age_s", 0) < HEARD_S
                  and tiles(st["x"], st["y"], *at) <= FAR_TILES
                  and (hunting is None or st.get("hunting") == hunting or n in (sent or ())))


def gone_ahead(names: list[str], snap: dict[str, Any], stats: dict[str, dict[str, Any]], centre: tuple[int, int],
               point: tuple[int, int] | None = None) -> list[str]:
    """Of `names`, who has gone on past the rally point (`point`; where the one holding it stands, without one),
    toward the hunt: by what its client sees, and for one out of view by its own last report. A member whose
    planner chose a walk to the place, not a hunt, walks on to its centre, and waiting at the edge for it leaves
    it in there alone. Measured from the point and not from the holder: live (2026-10-14) the warrior holding it
    ran from a lich during the wait, and an archer standing still 13 tiles behind the point counted as ahead."""
    p = snap["player"]
    mine = tiles(*(point or (p["x"], p["y"])), *centre)
    seen = {m["name"]: (p["x"] + m.get("dx", 0), p["y"] + m.get("dy", 0)) for m in snap.get("mobiles") or []
            if m.get("name") in names and not m.get("monster") and not m.get("dead") and "dx" in m}
    out = []
    for name in names:
        st = stats.get(name) or {}
        at = seen.get(name) or ((st["x"], st["y"]) if "x" in st else None)
        if at is not None and tiles(*at, *centre) < mine - GATHER_TILES:
            out.append(name)
    return out


def fighter_coming(still: list[str], beside: list[str], stats: dict[str, dict[str, Any]]) -> str:
    """The fighter a party with nobody to take the blows yet is waiting for: the first of those still coming
    who can, when none of those beside the rally can; "" otherwise."""
    if any((stats.get(n) or {}).get("plays_as") in POINT for n in beside):
        return ""
    return next((n for n in still if (stats.get(n) or {}).get("plays_as") in POINT), "")


def missing(want: list[str], snap: dict[str, Any], stats: dict[str, dict[str, Any]]) -> list[str]:
    """Of those waited for, who isn't beside the leader yet: by what its client sees, and for one out of
    view by its own last report."""
    p = snap["player"]
    seen = {m["name"]: m["distance"] for m in snap.get("mobiles") or []
            if m.get("name") in want and not m.get("monster") and not m.get("dead")}
    out = []
    for name in want:
        d = seen.get(name)
        if d is None:
            st = stats.get(name) or {}
            d = tiles(st["x"], st["y"], p["x"], p["y"]) if "x" in st else FAR_TILES
        if d > GATHER_TILES:
            out.append(name)
    return out


def hostile_near(snap: dict[str, Any], within: int, bestiary: dict[int, dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """The nearest creature in view that could start a fight, within `within` tiles."""
    near = [m for m in snap.get("mobiles") or [] if threatening(m, bestiary) and not m.get("pet")
            and not m.get("summon") and m["distance"] <= within]
    return min(near, key=lambda m: m["distance"]) if near else None


def sight_words(snap: dict[str, Any], party: Party, bestiary: dict[int, dict[str, Any]] | None = None) -> str:
    """What the party sees from where it stands, in words for a yes/no: the creatures in view by kind (how many, how
    near, how strong for this character, whether any is coming at the party), and who of the party is here."""
    from .state import known_creature, threat_words
    p = snap.get("player") or {}
    kinds: dict[str, list[dict[str, Any]]] = {}
    for m in snap.get("mobiles") or []:
        if m.get("monster") and not m.get("dead"):
            kinds.setdefault(str(m.get("name") or "a creature"), []).append(m)
    here = [party.me, *(m["name"] for m in snap.get("mobiles") or []
                        if m.get("name") in party.companions and not m.get("dead") and m.get("distance", 99) <= GATHER_TILES + 4)]
    parts = []
    for name, ms in sorted(kinds.items(), key=lambda kv: min(m.get("distance", 99) for m in kv[1])):
        near = min(m.get("distance", 99) for m in ms)
        known = known_creature(bestiary or {}, ms[0]) if bestiary else None
        how = threat_words(known, (p.get("hits_max") or 100) * len(set(here))).replace("you", "the party here") \
            if known else ""
        coming = sum(1 for m in ms if m.get("war_mode") or m.get("attacking_me"))
        parts.append(f"{len(ms)} x {name.removeprefix('a ').removeprefix('an ')}" if len(ms) > 1 else name)
        parts[-1] += f" ({near} tiles off" + (f", {how}" if how else "") + (f", {coming} coming at the party" if coming
                                                                           else ", not coming at the party") + ")"
    return (("; ".join(parts) if parts else "nothing hostile in view")
            + f". The party here: {', '.join(dict.fromkeys(here))}.")


def coming_at(snap: dict[str, Any], within: int) -> bool:
    """A creature in war mode within `within` tiles: something is coming at the party."""
    return any(m.get("monster") and not m.get("dead") and not m.get("pet") and m.get("war_mode")
               and m.get("distance", 99) <= within for m in snap.get("mobiles") or [])


def contact(snap: dict[str, Any]) -> bool:
    """The fight has started: the client is on something, or something is on the character."""
    return bool(snap["agent"].get("engaged")) or any(
        m.get("monster") and not m.get("dead") and m.get("attacking_me") for m in snap.get("mobiles") or [])


# ---- a rally point chosen from the map (cuo-cym.13) -------------------------------------

class Ground:
    """What a character can walk to from where it stands, as its client's travel planner has it (the `nav`
    RPC with `reach`: a row of characters for each row of tiles). A door is no ground to fall back through,
    nor is a tile the planner knows of and can't get to."""

    def __init__(self, x0: int, y0: int, rows: list[str]):
        self.walk = {(x0 + c, y0 + r) for r, row in enumerate(rows) for c, ch in enumerate(row) if ch in ",@"}
        # The ground within a tile of the map's own edge: as far as the client was asked.
        x1, y1 = x0 + max((len(row) for row in rows), default=0) - 1, y0 + len(rows) - 1
        self.rim = {(x, y) for x, y in self.walk if x <= x0 + 1 or y <= y0 + 1 or x >= x1 - 1 or y >= y1 - 1}
        self._out: dict[tuple[int, int], dict[tuple[int, int], int]] = {}

    @classmethod
    def read(cls, reply: Any) -> "Ground | None":
        """From the client's reply; None for one that has no map in it."""
        if not isinstance(reply, dict) or not reply.get("rows") or "x0" not in reply or "y0" not in reply:
            return None
        return cls(int(reply["x0"]), int(reply["y0"]), [str(row) for row in reply["rows"]])

    def ok(self, x: int, y: int) -> bool:
        return (x, y) in self.walk

    def room(self, x: int, y: int) -> int:
        """How many of the nine tiles round a place can be stood on."""
        return sum((x + dx, y + dy) in self.walk for dx in (-1, 0, 1) for dy in (-1, 0, 1))

    def out(self, centre: tuple[int, int]) -> dict[tuple[int, int], int]:
        """For each tile a walk leads from to the edge of the map: how near the hunt's centre that walk has to
        come at its nearest, by the best way there is. A tile in ground closed on every side isn't in it. Worked
        out once for a centre, from the edge inward."""
        if centre not in self._out:
            best = {t: tiles(*t, *centre) for t in self.rim}
            heap = [(-d, t) for t, d in best.items()]
            heapq.heapify(heap)
            while heap:
                d, (x, y) = heapq.heappop(heap)
                if -d < best[(x, y)]:
                    continue
                for dx, dy in STEPS:
                    t = (x + dx, y + dy)
                    if t not in self.walk or dx and dy and not ((x + dx, y) in self.walk and (x, y + dy) in self.walk):
                        continue
                    least = min(-d, tiles(*t, *centre))
                    if least > best.get(t, -1):
                        best[t] = least
                        heapq.heappush(heap, (-least, t))
            self._out[centre] = best
        return self._out[centre]


STEPS = ((0, -1), (1, 0), (0, 1), (-1, 0), (1, -1), (1, 1), (-1, 1), (-1, -1))


def walks(ground: Ground, start: tuple[int, int], centre: tuple[int, int], steps: int) -> dict[tuple[int, int], int]:
    """The tiles a walk of `steps` from `start` reaches without coming nearer the hunt's centre than it began
    (less a tile, to step round a gravestone), and how many steps each is. A step across a corner needs both
    tiles beside it free, as the game's own walking does."""
    least = tiles(*start, *centre) - 1
    reached = {start: 0}
    edge = [start]
    for n in range(1, steps + 1):
        nxt = []
        for x, y in edge:
            for dx, dy in STEPS:
                t = (x + dx, y + dy)
                if t in reached or t not in ground.walk or tiles(*t, *centre) < least:
                    continue
                if dx and dy and not ((x + dx, y) in ground.walk and (x, y + dy) in ground.walk):
                    continue
                reached[t] = n
                nxt.append(t)
        edge = nxt
    return reached


def way_out(ground: Ground, at: tuple[int, int], centre: tuple[int, int]) -> bool:
    """Whether a place has a way out behind it: a walk that gets OUT_TILES further from the hunt without coming
    nearer to it on the way, in no more than twice that many steps, and a walk that leads on from it to the
    edge of the map, as far as the client was asked, without coming nearer the hunt either. A dead end has
    none. By the first alone a yard walled on three sides, its only opening toward the hunt, had both points
    put inside it: any dead end deeper than OUT_TILES passed."""
    here = tiles(*at, *centre)
    if ground.out(centre).get(at, -1) < here - 1:
        return False
    return any(tiles(*t, *centre) >= here + OUT_TILES for t in walks(ground, at, centre, 2 * OUT_TILES))


def way_back(here: tuple[int, int], centre: tuple[int, int], trail: Iterable[tuple[int, int]] = (),
             far: int = BACK_TILES + SECOND_TILES, turn: int | None = None) -> list[tuple[int, int]]:
    """The way back from `here`, a tile for each tile of it, `here` first: along the leader's own steps on the
    way in (`trail`, the latest last), and beyond the first of them straight away from the hunt's centre.
    turn: not by its steps but straight away from the centre, turned that many eighths of a circle."""
    path = [here]
    for x, y in reversed(list(trail)) if turn is None else ():
        px, py = path[-1]
        n = tiles(px, py, x, y)
        path += [(px + round((x - px) * i / n), py + round((y - py) * i / n)) for i in range(1, n + 1)]
    px, py = path[-1]
    a = math.atan2(py - centre[1], px - centre[0]) + (turn or 0) * math.pi / 4
    dx, dy = math.cos(a), math.sin(a)
    longer = max(abs(dx), abs(dy))
    path += [(px + round(i * dx / longer), py + round(i * dy / longer)) for i in range(1, far + 1)]
    return path[:far + 1]


def spot(ground: Ground, aim: tuple[int, int], centre: tuple[int, int], within: dict[tuple[int, int], int], least: int,
         spawns: Iterable[tuple[int, int, int]] = ()) -> tuple[int, int] | None:
    """The ground to use for a point aimed at `aim`: within NUDGE_TILES of it, among the tiles a walk gets to
    (`within`), at least `least` tiles from the hunt's centre, and with a way out. Of those: clear of the other
    spawns the world store knows (`spawns`: where each is and how far its creatures walk), with room for a
    party, nearest the place aimed at, and of two as near it the one further from the hunt."""
    spawns = list(spawns)

    def by_a_spawn(t: tuple[int, int]) -> bool:
        return any(tiles(*t, x, y) <= reach + SIGHT_TILES for x, y, reach in spawns)

    found = sorted((t for t in within if tiles(*t, *aim) <= NUDGE_TILES and tiles(*t, *centre) >= least),
                   key=lambda t: (by_a_spawn(t), ground.room(*t) < ROOM, tiles(*t, *aim), -tiles(*t, *centre), t))
    return next((t for t in found if way_out(ground, t, centre)), None)


def choose(here: tuple[int, int], centre: tuple[int, int], ground: Ground, trail: Iterable[tuple[int, int]] = (),
           spawns: Iterable[tuple[int, int, int]] = ()) -> tuple[tuple[int, int], tuple[int, int] | None] | None:
    """The rally point for a party that formed up at `here`, and the second point behind it; None when the
    ground gives no rally point (the party then falls back to where it formed up, as before).

    The rally point: BACK_TILES back the way the leader came, and with no steps of its own to go by, or none
    that lead to ground that will do, straight away from the hunt's centre, then an eighth of a circle to
    either side of that. It is further from the hunt than where the party formed up, a short walk from there
    that never comes nearer the hunt, and has a way out behind it. The second point: SECOND_TILES further back
    the same way, reached from the first by a walk that never comes nearer the hunt, and with a way out of
    its own. A rally point with no second point behind it is still used."""
    spawns = list(spawns)
    ways = [way_back(here, centre, trail)] + [way_back(here, centre, turn=turn) for turn in (0, 1, -1)]
    near = walks(ground, here, centre, 2 * (BACK_TILES + NUDGE_TILES))
    least = tiles(*here, *centre) + BACK_TILES - NUDGE_TILES
    for i, way in enumerate(ways):
        first = spot(ground, way[BACK_TILES], centre, near, least, spawns)
        if first is None:
            continue
        behind = walks(ground, first, centre, 2 * (SECOND_TILES + NUDGE_TILES))
        further = tiles(*first, *centre) + SECOND_TILES // 2
        for other in ways[i:] + ways[:i]:
            second = spot(ground, other[BACK_TILES + SECOND_TILES], centre, behind, further, spawns)
            if second is not None:
                return first, second
        return first, None
    return None


class Rally:
    """One member's side of forming up, and the party's rally point as its fights see it."""

    def __init__(self, session: Any, party: Party, log: Callable[[dict[str, Any]], None] | None = None,
                 plan: str = "leader", by_map: bool = False):
        self.s, self.party = session, party
        self.plan = plan  # "leader": the leader opens the rally; "huddle": the first to arrive holds it
        self.by_map = by_map  # the rally point is chosen from the client's map, with a second one behind it (choose)
        self.log = log or (lambda _: None)
        self.shout: Callable[..., Any] | None = None  # mates.Mates.shout: the leader's "form up" and "in we go"
        # huddle.Huddle.going: whom the party's plan sends to a hunt (its area's name), or None when it doesn't say
        self.sends: Callable[[str], list[str] | None] | None = None
        # huddle.Huddle: whether the party's plan holds it back from a hunt until it has looked, and the yes/no on what
        # it sees from the edge (cuo-cym.14); None, a leader's party: nothing is looked at first.
        self.look: Any = None
        self.formed = 0       # hunts this member went into from a rally
        self.waited_s = 0.0   # a leader's waits at the edge, together
        self._current: tuple[float, dict[str, Any] | None] = (0.0, None)

    def current(self) -> tuple[int, int] | None:
        """Where the party last went in from, while that still holds: read at most every 2 s."""
        now = time.time()
        if now - self._current[0] > 2.0:
            try:
                self._current = (now, self.party.rally())
            except Exception:  # noqa: BLE001 - a store busy for a moment: the last answer stands
                self._current = (now, self._current[1])
        r = self._current[1]
        if r is None or not (r["x"] or r["y"]) or now - (r["go_t"] or r["t"]) > HOLDS_S:
            return None
        return r["x"], r["y"]

    def point(self) -> str:
        """Who went in a step ahead from the rally point that holds now; "" with none."""
        self.current()
        r = self._current[1]
        return (r.get("point") or "") if r is not None and self.current() is not None else ""

    def back(self) -> tuple[int, int] | None:
        """The second point, behind the rally point that holds now; None when it has none."""
        if self.current() is None:
            return None
        return (self._current[1] or {}).get("back")

    async def halt(self, snap: dict[str, Any] | None = None) -> None:
        """Stop walking and leave the rest (a fight, a spell) as it is. A client without the verb gets the
        plain stop, which ends those too: only when nothing is being fought."""
        res = await self.s.act("halt")
        if res.get("status") != "done" and not (snap is not None and contact(snap)):
            await self.s.act("stop")

    def fighting(self) -> bool:
        """Whether any companion is in a fight right now, by the party's fight rows."""
        try:
            rows = self.party.fights()
        except Exception:  # noqa: BLE001 - a store busy for a moment
            return False
        return any(not r.get("dead") and not r.get("running") and (r.get("target") or r.get("attackers"))
                   for r in rows.values())

    async def say(self, kind: str) -> None:
        if self.shout is not None:
            try:
                await self.shout(self.s.rpc, kind)
            except Exception:  # noqa: BLE001 - a callout is never worth the hunt
                pass

    async def before_hunt(self, key: str, name: str, cx: int, cy: int, radius: int) -> dict[str, Any]:
        """Form up for the hunt `key` at the area (its centre and spread). What came of it: formed (the
        word was given), sighted (creatures were in view from the rally, so the hunt starts there)."""
        try:
            out = await (self.gather(key, name, cx, cy, radius) if self.plan == "huddle"
                         else self.lead(key, name, cx, cy, radius) if self.party.leading
                         else self.join(key, name, cx, cy, radius))
        except Exception as e:  # noqa: BLE001 - forming up is never worth the hunt: in as before
            if self.party.leading and self.plan != "huddle":
                self.off(key)
            out = {"formed": False, "error": f"{type(e).__name__}: {e}"[:200]}
        self.formed += bool(out.get("formed"))
        self.log({"type": "rally", "t": time.time(), "key": key, "area": name, **out})
        self._current = (0.0, self._current[1])
        return out

    def off(self, key: str) -> None:
        """The leader's hunt isn't going in after all (refused, no such place): the others don't wait."""
        try:
            self.party.rally_go(key, "off")
        except Exception:  # noqa: BLE001
            pass

    # ---- the leader ----------------------------------------------------------------

    async def to_the_edge(self, name: str, cx: int, cy: int, radius: int,
                          seen: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """Walk to the edge of the area, as any trip the party makes together, stopping at the first sight of
        something hostile (which the walk to the centre used to carry on through). The snapshot there and "",
        or None and why not. seen["sighted"]: what was in view, if anything was."""
        s = self.s
        bestiary = s.world.bestiary()
        snap = await s.snap()
        if not snap.get("in_game") or snap["player"].get("dead"):
            return None, "dead or out of the game"
        edge = radius + EDGE_TILES

        def look(snap: dict[str, Any]) -> bool:
            # Its steps on the way in, for a rally point back along them (choose). Not across a recall.
            trail, at = seen.setdefault("trail", []), s.where(snap)
            if trail and tiles(*trail[-1], *at) > JUMP_TILES:
                trail.clear()
            if not trail or trail[-1] != at:
                trail.append(at)
                del trail[:-TRAIL]
            # Only by the hunting area: a creature within sight of the road on the way there is the walk's own
            # fight loop's, and stopping for it would have the party form up and hunt wherever it met one.
            if tiles(*s.where(snap), cx, cy) > edge + REACH_TILES:
                return False
            near = hostile_near(snap, SIGHT_TILES, bestiary)
            if near is not None:
                seen["sighted"] = near.get("name") or "a creature"
            return near is not None or contact(snap)

        seen["look"] = look
        if not look(snap) and tiles(*s.where(snap), cx, cy) > edge:
            async def watch() -> None:
                while True:
                    await asyncio.sleep(0.4)
                    now = await s.snap()
                    if now.get("in_game") and look(now):
                        seen["stopped"] = True
                        await self.halt(now)
                        return

            watching = asyncio.get_running_loop().create_task(watch())
            try:
                r = await s.travel_to(name, cx, cy, distance=edge, together=True, refuse=False)
            finally:
                watching.cancel()
            if not r.ok and not seen.get("stopped"):
                return None, f"did not reach the edge: {r.summary}"[:200]
            snap = await s.snap()
            if not snap.get("in_game") or snap["player"].get("dead"):
                return None, "died on the way"
        return snap, ""

    async def hold(self, key: str, snap: dict[str, Any], seen: dict[str, Any], hunting: str | None = None,
                   at: tuple[int, int] | None = None, centre: tuple[int, int] | None = None,
                   back: tuple[int, int] | None = None) -> dict[str, Any]:
        """Hold the rally where this member stands: wait for the others, give the word, and say who goes first.
        at, back: the rally point to fall back to, when that isn't where it stands, and the second point behind
        it (points). hunting, centre: with a planner each, the hunt's area and its centre."""
        s, party = self.s, self.party
        here = s.where(snap)
        at = at or here
        stats = party.statuses()
        # Whom the party's plan sends to this hunt, when it says (a huddle's blackboard): waited for whatever each
        # has said yet. Live, the warrior was a second behind the first one there, on a walk to the place that his
        # own report didn't call a hunt, and the party went in behind a bard.
        sent = (self.sends(hunting) if self.sends is not None else None) if hunting is not None else None
        want = expected(stats, party.me, here, hunting, sent)
        still = missing(want, snap, stats)

        def beside() -> list[str]:
            return [n for n in party.members if n == party.me or n in want and n not in still]

        def past() -> list[str]:
            # Who has walked on past the rally, on a walk the code didn't stop (a planner's own trip into the
            # hunt stops at the rally point: Planner.into_the_hunt). Not one whose own report says this hunt: it
            # walks to its own stop at the edge before it looks for the rally, which may be nearer the hunt than
            # this one, and then closes up on whoever holds it (join).
            walking = [n for n in still if (stats.get(n) or {}).get("hunting") != hunting]
            return gone_ahead(walking, snap, stats, centre, at) if centre is not None else []

        def walked_on() -> bool:
            # One who has walked on isn't coming to the rally, and is in there alone for as long as the wait lasts:
            # the word is given, when the party has one here to go in first or none is coming. Not ahead of a
            # fighter who is coming: live (2026-10-14), going in after a mage who had walked on, the party went
            # in one at a time, two died and the lich lived.
            return bool(ahead) and not fighter_coming([n for n in still if n not in ahead], beside(), stats)

        ahead = past()
        fighter = ""  # the one who takes the blows, when the wait went on for it
        held = time.monotonic()
        called = False

        def waiting() -> bool:
            nonlocal fighter
            waited = time.monotonic() - held
            if still and waited < WAIT_S:
                return True
            # With a planner each, nobody on the way yet may only mean their planners are a moment behind this one.
            if hunting is not None and not want and waited < SET_OUT_S:
                return True
            # ...and with nobody here who can take the blows and one who can still coming, the wait goes on: live,
            # a bard went in as the party's point with the warrior on his way, and the warrior died.
            coming = fighter_coming(still, beside(), stats) if hunting is not None else ""
            fighter = coming or fighter
            return bool(coming) and waited < FIGHTER_S

        while waiting() and not walked_on() and not s.interrupt.is_set() and not contact(snap):
            if not called and time.monotonic() - held >= 1.0:
                called = True
                await self.say("form_up")
            await asyncio.sleep(0.5)
            snap = await s.snap()
            if not snap.get("in_game") or snap["player"].get("dead"):
                break
            s.seen(snap)
            seen["look"](snap)
            stats = party.statuses()
            coming = expected(stats, party.me, here, hunting, sent)
            # One who died or went about its own business isn't waited for; one who set out for this hunt since is.
            want = sorted({n for n in want if n in coming} | (set(coming) if hunting is not None else set()))
            still = missing(want, snap, stats)
            ahead = past()
        waited = time.monotonic() - held
        self.waited_s += waited
        looked: dict[str, Any] = {}
        if hunting is not None and self.look is not None and snap.get("in_game") and not snap["player"].get("dead") \
                and await self.look.holds_for_a_look(getattr(s, "judge", None), hunting):
            # The plan holds the party back until it has seen the ground: they look from here, together.
            looked = await self.look_from_edge(hunting, snap, seen)
            if not looked.get("clear"):
                why = f"seen from the edge of {hunting}: {looked['seen']}; the plan's condition for going in isn't met"
                party.rally_go(key, "off", why)
                await self.look.seen_from_edge(hunting, looked["seen"])
                return {"formed": False, "called_off": True, "why": why, "looked": looked, "waited_s": round(waited, 1),
                        "with": sorted(set(want) - set(still)), "without": still}
        sighted = "sighted" in seen
        if hunting is None:
            point = point_of(party.members, stats, party.leader)
        else:
            # With a planner each: a fighter beside the rally first, then one still coming, of those on this hunt.
            # Picked only from those beside it, a bard who held the rally alone was the point, and nobody keeps
            # behind a bard.
            point = point_of(beside(), stats, "") \
                or point_of([n for n in party.members if n == party.me or n in want], stats, party.me)
        party.rally_open(key, *at, point=point, sighted=sighted, back=back)
        party.rally_go(key)
        if called or sighted:
            await self.say("go_in")
        if point == party.me:
            await asyncio.sleep(POINT_WAITS_S)
        return {"formed": True, "at": list(at), "point": point, "sighted": seen.get("sighted"),
                **({"formed_at": list(here)} if at != here else {}), **({"back": list(back)} if back else {}),
                "waited_s": round(waited, 1), "with": sorted(set(want) - set(still)), "without": still,
                **({"plan_sends": sent} if sent else {}), **({"waited_for_fighter": fighter} if fighter else {}),
                **({"gone_ahead": ahead} if ahead else {}), **({"looked": looked} if looked else {}),
                **({"contact": True} if snap.get("in_game") and contact(snap) else {})}

    async def look_from_edge(self, area: str, snap: dict[str, Any], seen: dict[str, Any]) -> dict[str, Any]:
        """From the rally at the edge, look at the ground the plan holds the party back from: what is in view, put to
        system one with the plan (huddle.edge_question), again whenever what is in view changes, for LOOK_S. clear: yes
        came (or a fight came to them, and they go in as one); seen: what was in view, in words. With memory on every
        plan the huddle agreed held the party back until a scout had looked, nobody was ever sent, and the lich was
        never fought (cuo-cym.14)."""
        s = self.s
        began = time.monotonic()
        asked: list[float] = []
        words, said = "", None
        while True:
            words = sight_words(snap, self.party, s.world.bestiary())
            if words != said:
                said = words
                p = await self.look.clear_to_go(getattr(s, "judge", None), area, words)
                asked.append(round(p, 3))
                if p >= LOOK_CUT:
                    return {"clear": True, "seen": words, "p": asked, "s": round(time.monotonic() - began, 1)}
            if contact(snap) or self.fighting() or coming_at(snap, LOOK_COMING_TILES):
                # The fight came to them while they looked: they go in as one. Live (run 161525), the bard looked on for
                # 30 s while the lich and two bone knights came at the party, and the warrior died at the edge.
                return {"clear": True, "seen": words, "p": asked, "contact": True, "s": round(time.monotonic() - began, 1)}
            if time.monotonic() - began >= LOOK_S or s.interrupt.is_set():
                break
            await asyncio.sleep(LOOK_EVERY_S)
            snap = await s.snap()
            if not snap.get("in_game") or snap["player"].get("dead"):
                break
            s.seen(snap)
            seen["look"](snap)
        return {"clear": False, "seen": words, "p": asked, "s": round(time.monotonic() - began, 1)}

    async def lead(self, key: str, name: str, cx: int, cy: int, radius: int) -> dict[str, Any]:
        seen: dict[str, Any] = {}
        snap, why = await self.to_the_edge(name, cx, cy, radius, seen)
        if snap is None:
            self.off(key)
            return {"formed": False, "why": why}
        at, back, note = await self.points(self.s.where(snap), cx, cy, radius, seen)
        self.party.rally_open(key, *at, point=point_of(self.party.members, self.party.statuses(), self.party.leader),
                              sighted="sighted" in seen, back=back)
        return {**await self.hold(key, snap, seen, at=at, back=back), **note}

    async def points(self, here: tuple[int, int], cx: int, cy: int, radius: int,
                     seen: dict[str, Any]) -> tuple[tuple[int, int], tuple[int, int] | None, dict[str, Any]]:
        """Where a party that forms up at `here` falls back to: its rally point, the second point behind it when
        it has one, and for the log why the rally point wasn't chosen when it was meant to be."""
        # Already inside the area (the second hunt of a place, and every one after it): the others close up on
        # the leader where it stands, but the place to fall back to stays the one the party went in from. Live,
        # the third and fifth hunts of a run rallied three tiles from the spawn's centre, and a fall back
        # from there would have run into the spawn.
        last = self.party.rally()
        near = REUSE_TILES + (BACK_TILES if self.by_map else 0)  # a chosen point is that much further out
        if last is not None and tiles(*here, cx, cy) <= radius + EDGE_TILES // 2 and (last["x"] or last["y"]) \
                and tiles(last["x"], last["y"], *here) <= near \
                and tiles(last["x"], last["y"], cx, cy) > tiles(*here, cx, cy):
            # ...and its second point with it, when that is behind it for this hunt too (it was chosen for the
            # last one's centre, which a neighbouring area's hunt doesn't share).
            back = last["back"]
            if back is not None and tiles(*back, cx, cy) <= tiles(last["x"], last["y"], cx, cy):
                back = None
            return (last["x"], last["y"]), back, {}
        if not self.by_map:
            return here, None, {}
        # Chosen from the client's map and the world store (choose). With no map, or no ground behind that will
        # do, it is where the party formed up, as it was before there was a choice.
        asked = time.monotonic()
        try:
            ground = Ground.read(await self.s.rpc.call("nav", timeout=MAP_S, radius=MAP_TILES, reach=True))
            found = choose(here, (cx, cy), ground, seen.get("trail") or (), self.other_spawns(cx, cy)) \
                if ground is not None else None
        except Exception as e:  # noqa: BLE001 - a client without the map, or busy: where it stands will do
            return here, None, {"unchosen": f"no map: {type(e).__name__}: {e}"[:120]}
        took = {"map_ms": round(1000 * (time.monotonic() - asked))}  # the client's answer and the choice together
        if found is None:
            return here, None, {**took, "unchosen": "no map from the client" if ground is None
                                else "no ground behind with a way out"}
        return found[0], found[1], took

    def other_spawns(self, cx: int, cy: int) -> list[tuple[int, int, int]]:
        """The spawns round a hunt that aren't the hunt's own: where each is and how far its creatures walk, by
        the world store's spawners. Not by its sightings: those are of creatures, the last wave the party fought
        among them, and say where one was met, not where it lives."""
        try:
            rows = self.s.world.what_spawns(near=(cx, cy), radius=SPAWN_TILES, limit=500)
        except Exception:  # noqa: BLE001 - a store busy for a moment, or one that knows no such place
            return []
        return [(r["x"], r["y"], int(r["range"])) for r in rows
                if r.get("range") is not None and (r["x"], r["y"]) != (cx, cy)]

    # ---- a party with a planner each (the huddle) -----------------------------------

    async def gather(self, key: str, name: str, cx: int, cy: int, radius: int) -> dict[str, Any]:
        """With no leader's goal to form up for: the first to reach the edge holds the rally there and waits for
        those on their way to the same hunt (by their own reports, and by the party's plan when it names who
        goes); the others close up on it."""
        s, party = self.s, self.party
        seen: dict[str, Any] = {}
        party.setting_out(name)
        snap, why = await self.to_the_edge(name, cx, cy, radius, seen)
        if snap is None:
            return {"formed": False, "why": why}
        here = s.where(snap)
        row = party.rally(key)
        if row is not None and row["state"] == "go" and (self.fighting() or expected(party.statuses(), party.me, here, name)):
            # The others went in from it and are still at it, in a fight or between two: one arriving late, or
            # beginning its next hunt of the place, joins them. Claiming the rally anew would take the party's
            # rally point from under them and wait at the edge for those inside. Live, every member's planner
            # chose the same hunt again every minute or so, and each time one of them held a new rally where it
            # stood: 25 s waits for companions already hunting, the rally point three tiles from the spawn's
            # centre, and the mage, the tamer and the bard the point in turn.
            return await self.join(key, name, cx, cy, radius, on=row["host"] or party.leader)
        # Already inside the area, the place to fall back to stays the one the party went in from (as lead).
        at, back, note = await self.points(here, cx, cy, radius, seen)
        row = party.rally_claim(key, *at, sighted="sighted" in seen, back=back)
        if row["host"] == party.me and row["state"] == "gathering":
            return {**await self.hold(key, snap, seen, hunting=name, at=at, centre=(cx, cy), back=back), **note,
                    "held": True}
        return await self.join(key, name, cx, cy, radius, on=row["host"] or party.leader)

    # ---- the others ----------------------------------------------------------------

    async def join(self, key: str, name: str, cx: int, cy: int, radius: int, on: str | None = None) -> dict[str, Any]:
        """Close up on whoever holds the rally (`on`: the leader, unless said) and wait for the word."""
        s, party = self.s, self.party
        on = on or party.leader
        began = time.monotonic()
        held_since: float | None = None  # when the rally was first seen held: the wait for the word runs from then
        following = 0
        going: tuple[int, int] | None = None
        went = -1e9
        snap: dict[str, Any] = {}
        out: dict[str, Any] = {"formed": False, "why": "no word from the leader"}
        while not s.interrupt.is_set():
            waited = time.monotonic() - (held_since if held_since is not None else began)
            if waited >= (JOIN_WAIT_S if held_since is not None else TRIP_WAIT_S):
                break
            r = party.rally(key)
            if r is not None and r["state"] in ("go", "off"):
                out = {"formed": r["state"] == "go", "sighted": r["sighted"], "point": r["point"]} \
                    if r["state"] == "go" else {"formed": False, "called_off": True,
                                                "why": r.get("why") or "the leader's hunt was called off"}
                break
            if r is not None and held_since is None:
                held_since = time.monotonic()
            snap = await s.snap()
            if not snap.get("in_game") or snap["player"].get("dead"):
                out = {"formed": False, "why": "dead or out of the game"}
                break
            s.seen(snap)
            lead = party.statuses().get(on)
            if not lead or lead.get("dead") or lead["age_s"] > HEARD_S:
                out = {"formed": False, "why": "no leader to form up on" if on == party.leader
                       else f"{on}, who held the rally, is gone"}
                break
            here = s.where(snap)
            m = next((m for m in snap.get("mobiles") or [] if m.get("name") == on and not m.get("dead")), None)
            if m is not None:
                going = None
                state = (snap["agent"].get("follow") or {}).get("state")
                if m["distance"] > 2 and (following != m["serial"] or state in (None, "lost")):
                    res = await s.act("follow", target=m["serial"], distance=2,
                                      pace="walk" if s.pace == "walk" else "run")
                    following = m["serial"] if res.get("status") == "done" else 0
            else:
                following = 0
                to = (r["x"], r["y"]) if r is not None else (lead["x"], lead["y"])
                if r is None and tiles(*here, cx, cy) > RECALL_TILES and tiles(*here, *to) > RECALL_TILES:
                    # A long way off, and the leader gone ahead (a recall, a head start): its own way to the edge.
                    await s.travel_to(name, cx, cy, distance=radius + EDGE_TILES, together=True, refuse=False)
                    await asyncio.sleep(1.0)
                    continue
                walking = (snap["agent"].get("travel") or {}).get("state") == "walking"
                # Not asked again within 3 s: a walk that finds no path holds the client's game thread a while.
                if tiles(*here, *to) > 2 and (going is None or tiles(*going, *to) > 4 or not walking) \
                        and time.monotonic() - went >= 3.0:
                    await s.act("travel", x=to[0], y=to[1], distance=2, pace="run")
                    going, went = to, time.monotonic()
            await asyncio.sleep(0.4)
        if following or going:
            await self.halt(snap or None)
        if out.get("formed") and (r := party.rally(key)) is not None and (r["x"] or r["y"]):
            # The word has been given. One the party went in without, or that took the goal up late, isn't where
            # they went in from: it goes there first, and its hunt starts there as theirs did. Left where it
            # stood, it would begin its hunt with nothing in view and stand idle until its next patrol.
            snap = await s.snap()
            if snap.get("in_game") and not snap["player"].get("dead") \
                    and tiles(*s.where(snap), r["x"], r["y"]) > NEAR_TILES \
                    and not any(m.get("name") in party.companions and not m.get("dead") and m["distance"] <= NEAR_TILES
                                for m in snap.get("mobiles") or []):
                t = await s.travel_to(x=r["x"], y=r["y"], distance=3, refuse=False, pace="run")
                out["caught_up"] = t.ok
        out["closed_up_s"] = round(time.monotonic() - began, 1)
        if out.get("formed") and out.get("point") == party.me:
            await asyncio.sleep(POINT_WAITS_S)
        return out

    def to_log(self) -> dict[str, Any]:
        return {"formed_up": self.formed, **({"waited_for_the_party_s": round(self.waited_s, 1)} if self.waited_s else {})}
