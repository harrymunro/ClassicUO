"""Session skills: the goals the planner hands out, carried out by code.

Real play is a loop of travelling, hunting, banking and restocking. The planner (a larger
model, planner.py) chooses which goal comes next and with what arguments; each goal here
runs until it is done, fails or is interrupted, and returns a short result in words and
numbers that goes back to the planner. Code does what has a right answer (the route, which
vendor sells bandages, moving items); Jev keeps the tactical decisions inside a hunt, and on
the way when something attacks.

Every skill reads the world store for places and writes back what it learned: the route it
walked (or where it got stuck), and what happened in a hunt.
"""

import asyncio
import json
import math
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from . import calls, loop, policy, state
from .facts import FactPicker
from .judge import Judge
from .logs import session_name
from .routine import HuntWatch, RoutineConfig
from .rpc import AgentRpc, RpcError
from .world import World, WorldError, name_score, near_xy, tiles, tokens, wilds_area

# A vendor place's kind -> words its title carries in game ("Lucy the healer").
VENDOR_TITLES: dict[str, tuple[str, ...]] = {
    "healer": ("healer",),
    "mage_shop": ("mage", "alchemist", "herbalist"),
    "reagent_vendor": ("mage", "alchemist", "herbalist"),
    "alchemist": ("alchemist",),
    "herbalist": ("herbalist",),
    "provisioner": ("provisioner",),
    "weapon_vendor": ("weaponsmith", "blacksmith", "weapon"),
    "armour_vendor": ("armourer", "armorer", "blacksmith", "armor"),
    "blacksmith": ("blacksmith", "weaponsmith", "armourer", "armorer"),
    "jeweller": ("jeweler", "jeweller"),
    "bank": ("banker", "minter"),
    "bowyer": ("bowyer",),
    "tailor": ("tailor", "weaver"),
}
GUILDMASTER = re.compile(r"guild(master|mistress)")


@dataclass
class Result:
    ok: bool
    summary: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_tool(self) -> dict[str, Any]:
        return {"ok": self.ok, "result": self.summary, **self.data}


class Session:
    """The character's skills on one client. One goal at a time."""

    def __init__(self, rpc: AgentRpc, world: World, judge: Judge | None = None,
                 log: Callable[[dict[str, Any]], None] | None = None,
                 pcfg: policy.PolicyConfig | None = None, archetype: str | None = None,
                 decisions_log: Path | None = None):
        self.rpc = rpc
        calls.sink = lambda rec: rpc.call("ai_call", **rec)  # the client's live view of every model call
        self.decisions_log = decisions_log  # where hunts append Jev's decisions
        self.world = world
        self.judge = judge
        self.pcfg = pcfg or policy.PolicyConfig()
        self.archetype = archetype
        self._log = log or (lambda _: None)
        self.interrupt = asyncio.Event()  # set to cut the current goal short (handover, shutdown)
        self.chosen: dict[str, Any] | None = None  # the vendor the last shop errand picked: serial, name, tiles off
        self._threat_noted: dict[str, float] = {}
        self.recorder = None  # recorder.Recorder: what the character sees goes into the world store
        self.routine = {"questions": 0, "input_tokens": 0, "cost_usd": 0.0, "hunt_minutes": 0.0}  # all hunts
        self.dangers: dict[str, dict[int, str]] = {}  # area -> creature serial -> kind, stronger than the character
        self.facts_mode = "jev"  # which world facts reach the fights: jev (Jev picks), all, none (facts.py)
        self.machine = None      # machine.Runner: a plan for the fights in hunts (cuo-6om); None for the default
        # Where hunts ended in a leave from creatures: (x, y, monotonic time, area, why). Goals back
        # there are refused for a while: told not to, a soak run's planner still sent its warrior to
        # "the skeleton spawner" of the graveyard it had just left two gargoyles at, and it died.
        self.left_from: list[tuple[int, int, float, str, str]] = []
        # A party that walks its trips together (cuo-cod.7): "walk" makes every trip a walk (the client runs
        # while there is danger, and to catch up); `leads` has the leader wait for anyone who falls behind.
        self.pace = "run"
        # Every trip's pace (cuo-der: --travel, a fleet's `travel`, a set of rules): "walk" or "run";
        # None leaves it to the client's own setting (agent_travel_pace), read from the snapshot.
        self.travel_pace: str | None = None
        self.leads = False
        self.leads_party = False        # its goals are a party's (the leader on the leader's plan)
        self.beside: str | None = None  # a follower: the leader it walks beside
        self.roleplay = None  # roleplay.Roleplay: small things done in character on events (cuo-cod.8)
        self.rally = None     # rally.Rally: a party forms up at the edge of a hunt and goes in as one (cuo-cym.1)
        self.hunting_area = ""  # the hunt it is on or on its way to: whom a huddle's rally waits for (cuo-cym.5)
        self.raise_fallen = False  # a party raises its fallen where they fell: a ghost waits for it (cuo-cym.4)
        self.hamlet = None  # hamlet.Hamlet: where a resident lives, its home a place for travel_to (cuo-m3n)

    def paced(self) -> dict[str, str]:
        """The pace for a short walk of the session's own, when every trip has one (travel_pace)."""
        return {"pace": self.travel_pace} if self.travel_pace else {}

    async def play(self, event: str, **kw: Any) -> None:
        if self.roleplay is not None:
            await self.roleplay.act(event, **kw)

    LAG_TILES = 8      # a companion further behind the leader than this is waited for
    LAG_WAIT_S = 20.0  # ...this long at a time
    LAG_FAR = 60       # one further off than this is on business of its own: not waited for

    def laggards(self, snap: dict[str, Any], to: tuple[int, int] | None = None) -> list[str]:
        """The party's members who have fallen behind the leader on a walk: alive, more than LAG_TILES
        away and no more than LAG_FAR, and (with the trip's end in `to`) further from it than the
        leader is: one who has gone ahead isn't waited for. By what the client sees; for those out of
        view, by their own last report."""
        link = self.pcfg.link
        if link is None or not self.leads:
            return []
        here = self.where(snap)
        left = tiles(*here, *to) if to else 0

        def behind(x: int, y: int) -> bool:
            return self.LAG_TILES < tiles(*here, x, y) <= self.LAG_FAR and (to is None or tiles(x, y, *to) > left)

        seen = {m["name"]: m for m in snap.get("mobiles") or [] if m.get("name") in self.pcfg.companions}
        out = [n for n, m in seen.items() if not m.get("dead") and behind(here[0] + m.get("dx", 0), here[1] + m.get("dy", 0))]
        for name, st in link.party.statuses().items():
            if name not in seen and name != link.party.me and not st.get("dead") and st["age_s"] < 30 \
                    and behind(st["x"], st["y"]):
                out.append(name)
        return out

    AVOID_TILES = 30
    AVOID_MINUTES = 30
    # On ModernUO and RunUO a corpse turns to bones 7 minutes after the death and is gone 7 minutes after that; the
    # client keeps its own as bones (AgentController.IsOwnRemains). A shard with other times would want its own.
    CORPSE_LASTS_S = 14 * 60

    def last_leave(self) -> dict[str, Any] | None:
        """Where this character last left creatures behind, and how long ago: for its party's reports."""
        if not self.left_from:
            return None
        x, y, at, _, why = self.left_from[-1]
        return {"x": x, "y": y, "ago_s": round(time.monotonic() - at), "why": why[:80]}

    def avoided(self, x: int, y: int) -> str | None:
        """Why a goal at (x, y) is refused, or None: within AVOID_TILES of where a hunt ended in a
        leave from creatures in the last AVOID_MINUTES."""
        now = time.monotonic()
        for lx, ly, at, area, why in self.left_from:
            ago = (now - at) / 60
            if ago < self.AVOID_MINUTES and tiles(x, y, lx, ly) <= self.AVOID_TILES:
                return (f"not going back near {area} yet: it left there {ago:.0f} min ago ({why}); choose somewhere "
                        f"at least {self.AVOID_TILES} tiles away, or wait {self.AVOID_MINUTES - ago:.0f} min")
        return None

    def seen(self, snap: dict[str, Any]) -> None:
        self.note_dangers(snap)
        if self.recorder is None:
            return
        self.recorder.observe(snap)
        if self.recorder.due():
            asyncio.get_running_loop().create_task(self.recorder.flush())

    def note_dangers(self, snap: dict[str, Any]) -> None:
        """Creatures stronger than the character, by kind and area, for the goal's result and the
        world store. A soak run's planner sent its character back to a graveyard where a lich and
        two bone knights had driven it off, because nothing it could see or query said so."""
        if not snap.get("in_game") or "player" not in snap:
            return
        from .state import BODY_KINDS, known_creature, threat_words
        p = snap["player"]
        bestiary = self.world.bestiary()
        for m in snap.get("mobiles", []):
            known = known_creature(bestiary, m) if m.get("monster") and not m.get("dead") else None
            if not known or not threat_words(known, p.get("hits_max") or 100).startswith(("stronger", "far stronger")):
                continue
            kind = BODY_KINDS.get(m.get("body", 0)) or str(known.get("name") or m.get("name") or "a creature")
            x, y = p["x"] + m.get("dx", 0), p["y"] + m.get("dy", 0)
            region = self.world.region_at(x, y)
            area = region["name"] if region else wilds_area(x, y)  # out in the wilds there is no region name
            self.dangers.setdefault(area, {})[m["serial"]] = kind.removeprefix("a ").removeprefix("an ")

    def danger_words(self, since: dict[str, dict[int, str]]) -> str:
        """The stronger creatures seen since `since` (a copy of self.dangers), in words."""
        parts = []
        for area, seen in self.dangers.items():
            new = {s: k for s, k in seen.items() if s not in since.get(area, {})}
            if new:
                kinds: dict[str, int] = {}
                for k in new.values():
                    kinds[k] = kinds.get(k, 0) + 1
                parts.append(", ".join(f"{n} {k}{'s' if n > 1 else ''}" for k, n in kinds.items()) + f" at {area}")
        return "; ".join(parts)

    def record_dangers(self, words: str) -> None:
        """Note what was seen in the world store, once per area and wording."""
        for part in words.split("; "):
            if " at " in part:
                what, area = part.rsplit(" at ", 1)
                self.world.add_note(f"Stronger than a new character, seen {time.strftime('%Y-%m-%d %H:%M')}: {what}.",
                                    area=area, tags=["danger"], source="seen")

    # ------------------------------------------------------------ helpers

    async def snap(self, **kw: Any) -> dict[str, Any]:
        return await self.rpc.call("snapshot", since=0, **kw)

    def log(self, kind: str, **data: Any) -> None:
        self._log({"type": "session", "event": kind, "t": time.time(), **data})

    async def act(self, verb: str, **params: Any) -> dict[str, Any]:
        return await self.rpc.call("act", verb=verb, **params)

    @staticmethod
    def where(snap: dict[str, Any]) -> tuple[int, int]:
        return snap["player"]["x"], snap["player"]["y"]

    def resolve(self, place: str | None, x: int | None, y: int | None) -> tuple[int, int, str] | None:
        if x is not None and y is not None:
            return x, y, place or f"{x},{y}"
        if not place:
            return None
        if (hamlet := getattr(self, "hamlet", None)) is not None and hamlet.is_home(place):
            return *hamlet.home, "home"
        found = self.world.place(place, limit=1)
        if found:
            return found[0]["x"], found[0]["y"], found[0]["name"]
        xy = near_xy(place)
        return (xy[0], xy[1], place) if xy else None

    # ------------------------------------------------------------ travel

    async def travel_to(self, place: str | None = None, x: int | None = None, y: int | None = None,
                        distance: int = 2, timeout_s: float = 600, recall: bool = True,
                        refuse: bool = True, pace: str | None = None, together: bool = False) -> Result:
        """Get to a named place or a tile: by Recall when a rune or runebook goes there,
        walking the rest (or all of it), fighting what attacks on the way. together: a trip the party
        makes as one (the leader's travel, a hunt's walk, the walk to the inn): a party that walks its
        trips walks it, its leader waits for anyone who falls behind, and nobody recalls away from the
        others. A member's own errands (a shop, the bank, a healer, its corpse) are run as ever, unless every
        trip walks (travel_pace). pace: "walk" or "run", to say so outright."""
        pace = pace or ("walk" if together and self.pace == "walk" else self.travel_pace)
        target = self.resolve(place, x, y)
        if target is None:
            return Result(False, f"no place called {place!r} in the world store")
        tx, ty, name = target
        # The refusal after a leave is about hunting there; a trip to a healer or a corpse passes refuse=False.
        if refuse and (refused := self.avoided(tx, ty)):
            return Result(False, refused)
        snap = await self.snap()
        if not snap.get("in_game"):
            return Result(False, "not in game (the connection was lost?)")
        start = self.where(snap)
        pace = pace or snap["agent"].get("travel_pace") or "run"
        ghost = bool(snap["player"]["dead"])  # a ghost walks to a healer or its corpse; it can't recall
        if tiles(*start, tx, ty) <= distance:
            return Result(True, f"already at {name}", {"x": tx, "y": ty})
        walking = pace == "walk" and not ghost
        recalled = await self.recall_to(name, tx, ty, snap) \
            if recall and not ghost and not walking and tiles(*start, tx, ty) > 40 else None
        if recalled is not None:
            snap = await self.snap()
            start = self.where(snap)
            if tiles(*start, tx, ty) <= distance:
                return Result(True, f"recalled to {name} ({recalled})", {"x": start[0], "y": start[1], "by": "recall"})
        here = self.world.region_at(*start)
        from_name = here["name"] if here else f"{start[0]},{start[1]}"

        if tiles(*start, tx, ty) > 30 and not ghost:
            await self.play("set_out")
        go = {"x": tx, "y": ty, "distance": distance, "pace": "walk" if walking else "run"}
        res = await self.act("travel", **go)
        self.log("travel", to=name, x=tx, y=ty, status=res.get("status"), detail=res.get("detail"),
                 **({"pace": "walk"} if walking else {}))
        if res.get("status") != "done":
            return Result(False, f"could not set off for {name}: {res.get('detail') or res.get('status')}")
        waited = 0.0
        left_behind: set[str] = set()  # waited for once, and still behind: not waited for again until they catch up

        began = time.monotonic()
        trail: list[list[int]] = [list(start)]
        travel: dict[str, Any] = {}
        while time.monotonic() - began < timeout_s and not self.interrupt.is_set():
            await asyncio.sleep(1.0)
            snap = await self.snap()
            if not snap.get("in_game"):
                return Result(False, "lost the connection on the way", {"at": trail[-1]})
            self.seen(snap)
            if snap["player"]["dead"] and not ghost:
                return Result(False, "died on the way", {"at": list(self.where(snap))})
            pos = list(self.where(snap))
            if tiles(*pos, *trail[-1]) >= 8:
                trail.append(pos)
            travel = snap["agent"].get("travel") or {}
            if travel.get("state") != "walking":
                break
            lagging = set(self.laggards(snap, (tx, ty))) if walking and together else set()
            left_behind &= lagging
            if behind := sorted(lagging - left_behind):
                # The leader waits for anyone who has fallen behind (a door, a fight on the way), then walks on.
                # Once for each: one who is still behind after a full wait is on business of its own, and
                # waiting again and again made a crawl of a trip (40 waits, 179 tiles unfinished in 9 minutes).
                await self.act("stop")
                held = time.monotonic()
                still = set(behind)
                while time.monotonic() - held < self.LAG_WAIT_S and not self.interrupt.is_set() \
                        and (still := set(behind) & set(self.laggards(await self.snap(), (tx, ty)))):
                    await asyncio.sleep(1.0)
                left_behind |= still
                waited += time.monotonic() - held
                self.log("waited_for", who=behind, seconds=round(time.monotonic() - held, 1),
                         **({"went_on_without": sorted(still)} if still else {}))
                if (await self.act("travel", **go)).get("status") != "done":
                    travel = {"state": "stopped", "why": "could not set off again after waiting for the party"}
                    break
        else:
            await self.act("stop")
            # The client still says "walking": the session's own reason wins.
            travel = {**travel, "state": "interrupted" if self.interrupt.is_set() else "timed out"}

        seconds = round(time.monotonic() - began)
        state = travel.get("state", "?")
        ok = state == "arrived"
        stuck = travel.get("stuck_at") or []
        trail.append(list(self.where(snap)))
        self.world.add_route(from_name, name, trail, outcome="ok" if ok else "stuck",
                             stuck_at=stuck or None, duration_s=seconds)
        self.log("travelled", to=name, state=state, seconds=seconds, stuck_at=stuck,
                 **{k: travel[k] for k in ("why", "stuck_why", "refused", "replans", "doors", "hops", "plans", "plan_ms_max")
                    if travel.get(k) not in (None, [])})
        if ok:
            return Result(True, f"arrived at {name} in {seconds} s" + (", at a walk with the party" if walking else ""),
                          {"seconds": seconds, "replans": travel.get("replans", 0),
                           **({"waited_for_the_party_s": round(waited)} if waited else {})})
        why = f" ({travel['why']})" if travel.get("why") else ""
        return Result(False, f"did not reach {name}: {state}{why}, {travel.get('left', '?')} tiles short",
                      {"seconds": seconds, "stuck_at": stuck, "position": list(self.where(snap))})

    async def walk_with(self, leader: str, place: str | None = None, x: int | None = None, y: int | None = None,
                        distance: int = 3, timeout_s: float = 600) -> Result:
        """Walk to a place beside the party's leader (cuo-cod.7): the client keeps within two tiles of it
        as it goes (`act follow`: at a walk, running in danger and to catch up). With the leader out of
        view, already there, or dead, or on a client that can't follow, it makes its own way there."""
        target = self.resolve(place, x, y)
        if target is None:
            return Result(False, f"no place called {place!r} in the world store")
        tx, ty, name = target
        began = time.monotonic()
        following = 0
        snap: dict[str, Any] = {}

        async def alone(pace: str = "run") -> Result:
            if following:
                await self.act("stop")
            return await self.travel_to(name, tx, ty, distance=distance, refuse=False, pace=pace,
                                        timeout_s=max(30.0, timeout_s - (time.monotonic() - began)))

        while time.monotonic() - began < timeout_s and not self.interrupt.is_set():
            snap = await self.snap()
            if not snap.get("in_game"):
                return Result(False, "lost the connection on the way")
            self.seen(snap)
            if snap["player"]["dead"]:
                return Result(False, "died on the way", {"at": list(self.where(snap))})
            here = self.where(snap)
            if tiles(*here, tx, ty) <= distance + 1:
                if following:
                    await self.act("stop")
                secs = round(time.monotonic() - began)
                return Result(True, f"walked to {name} with {leader} in {secs} s", {"seconds": secs, "with": leader})
            m = next((m for m in snap.get("mobiles") or [] if m.get("name") == leader and not m.get("dead")), None)
            if m is None:
                return await alone("run")  # out of view: catch up
            if tiles(here[0] + m["dx"], here[1] + m["dy"], tx, ty) <= distance + 1:
                return await alone()       # the leader is there already
            state = (snap["agent"].get("follow") or {}).get("state")
            if following != m["serial"] or state in (None, "lost"):
                res = await self.act("follow", target=m["serial"], distance=2, pace="walk")
                if res.get("status") != "done":
                    return await alone(self.pace)   # a client that can't follow: its own way, at the party's pace
                following = m["serial"]
            await asyncio.sleep(1.0)
        if following:
            await self.act("stop")
        return Result(False, f"did not reach {name}: {'interrupted' if self.interrupt.is_set() else 'timed out'}",
                      {"position": list(self.where(snap)) if snap.get("in_game") else None})

    def rune_for(self, name: str, tx: int, ty: int, snap: dict[str, Any]) -> tuple[int, int, str] | None:
        """(serial, runebook entry or -1, label) of the rune or runebook entry that goes to a
        place: by its name, or by a name the world store puts within 20 tiles of it. A loose rune
        needs the Recall spell; without it only runebook entries (charges) count."""
        items = snap.get("travel_items") or {}
        spells = {sp["name"]: sp for sp in (snap.get("magic") or {}).get("spells", [])}
        castable = "Recall" in spells and not spells["Recall"].get("missing")
        options = [(r["serial"], -1, r["name"]) for r in items.get("runes", [])] if castable else []
        for book in items.get("runebooks", []):
            options += [(book["serial"], i, e) for i, e in enumerate(book.get("entries", []))]
        best, best_score = None, 0.0
        for serial, entry, label in options:
            text = label.split(":", 1)[-1]  # "Recall Rune: ... for West Britain Bank (Felucca)"
            text = text.lower().split(" for ", 1)[-1].replace("(felucca)", "").strip()
            score = name_score(name, text)
            if score < 0.6:
                hit = self.world.place(text, limit=1)
                score = 0.7 if hit and tiles(hit[0]["x"], hit[0]["y"], tx, ty) <= 20 else 0.0
            if score > best_score:
                best, best_score = (serial, entry, text), score
        return best

    async def recall_to(self, name: str, tx: int, ty: int, snap: dict[str, Any]) -> str | None:
        """Recall towards a place; what it used, or None when it couldn't (no rune, no mana or
        reagents, a fizzle, a place you can't recall from)."""
        rune = self.rune_for(name, tx, ty, snap)
        if rune is None:
            return None
        serial, entry, label = rune
        spells = {sp["name"]: sp for sp in (snap.get("magic") or {}).get("spells", [])}
        castable = "Recall" in spells and not spells["Recall"].get("missing")
        if not castable and entry < 0:
            return None  # a loose rune needs the spell; a runebook can use a charge
        start = self.where(snap)
        for attempt in range(2):  # Recall can fizzle
            res = await self.act("recall", target=serial, distance=max(entry, 0), kind="spell" if castable else "charge")
            if res.get("status") not in ("done", "queued"):
                return None
            for _ in range(16):
                await asyncio.sleep(0.5)
                now = await self.snap()
                if tiles(*self.where(now), *start) > 20:
                    self.log("recalled", to=name, via=label, at=list(self.where(now)))
                    return f"via {label}"
            self.log("recall_failed", to=name, via=label, attempt=attempt + 1)
        return None

    # ------------------------------------------------------------ errands

    async def errand(self, verb: str, timeout_s: float = 60, **params: Any) -> dict[str, Any]:
        res = await self.act(verb, **params)
        if res.get("status") != "done":
            return {"state": "failed", "detail": res.get("detail") or res.get("status")}
        began = time.monotonic()
        while time.monotonic() - began < timeout_s:
            await asyncio.sleep(0.5)
            e = (await self.snap())["agent"].get("errand") or {}
            if e.get("state") in ("done", "failed"):
                return e
        await self.act("stop")  # the client's errand would go on, and take over the next walk
        return {"state": "failed", "detail": "timed out"}

    async def go_near(self, kind: str, within: int, skip: set[str] = frozenset(),
                      sells: str | None = None, max_walk: int | None = None) -> tuple[dict[str, Any] | None, Result | None]:
        """The nearest place of a kind (or selling an item), walked to if further than `within`.
        With `sells`, places known not to sell that item are left out (a wandering healer is a
        healer that sells no bandages), and those known to sell it come first. With `max_walk`, a
        place further than that isn't walked to: the result names it and how far it is."""
        snap = await self.snap()
        found = [p for p in self.world.find_place(kind, near=list(self.where(snap)), limit=12) if p["name"] not in skip]
        if sells:
            want = tokens(sells)
            def stocks(p: dict[str, Any]) -> bool | None:
                listed = p.get("sells")
                return None if not listed else any(all(w in tokens(s) for w in want) for s in listed)
            found = [p for p in found if stocks(p) is not False]
            found.sort(key=lambda p: stocks(p) is not True)  # stable: nearest first within each group
        if not found:
            return None, Result(False, f"the world store knows no {'other ' if skip else ''}{kind}")
        place = found[0]
        far = tiles(*self.where(snap), place["x"], place["y"])
        if max_walk is not None and far > max_walk:
            what = f"{place['kind']} selling {sells}" if sells else place["kind"]
            return place, Result(False, f"the nearest {'other ' if skip else ''}{what} is {place['name']}, {far} tiles "
                                        f"away", {"too_far": True, "place": place["name"], "tiles": far})
        if far > within:
            r = await self.travel_to(place["name"], place["x"], place["y"], distance=max(1, within - 2))
            if not r.ok:
                return place, Result(False, f"could not get to {place['name']}: {r.summary}", r.data)
        return place, None

    async def find_inn(self) -> dict[str, Any] | None:
        """The nearest inn the world store knows (an innkeeper's place), else the nearest tavern."""
        snap = await self.snap()
        here = list(self.where(snap))
        for kind in ("inn", "tavern"):
            found = self.world.find_place(kind, near=here, limit=6)
            if found and found[0].get("distance", 99) <= 12:
                return found[0]  # standing at one already (a first run walked on to another inn 135 tiles off)
            # Else places from the shard's own data first: a "seen" one is where an innkeeper happened to stand.
            known = [p for p in found if p.get("source") != "seen"] or found
            if known:
                return known[0]
        return None

    async def bank(self, deposit: str = "gold,loot", withdraw: str = "") -> Result:
        """Deposit gold and loot, withdraw supplies ("bandage:100"), at the nearest bank."""
        place, failed = await self.go_near("bank", 10)
        if failed:
            return failed
        before = (await self.snap())["player"]
        e = await self.errand("bank", deposit=deposit, withdraw=withdraw)
        after = (await self.snap())["player"]
        ok = e.get("state") == "done"
        data = {"gold_carried": after.get("gold"), "weight": f"{after.get('weight')}/{after.get('weight_max')}",
                "bandages": after["supplies"].get("bandages"), "moved": e.get("moved", 0)}
        self.log("bank", place=place["name"], ok=ok, detail=e.get("detail"), **data)
        if ok:
            self.world.add_place("bank", place["name"], place["x"], place["y"], z=place.get("z"), source="seen")
        return Result(ok, f"{place['name']}: {e.get('detail', e.get('state'))}",
                      data | {"weight_before": before.get("weight"), "gold_before": before.get("gold")})

    async def vendor(self, kind: str, words: tuple[str, ...], exclude: set[int] = frozenset(),
                     skip: set[str] = frozenset(), sells: str | None = None,
                     max_walk: int | None = None) -> tuple[dict[str, Any] | None, int, Result | None]:
        place, failed = await self.go_near(kind, 8, skip, sells, max_walk)
        if failed:
            return place, 0, failed
        snap = await self.snap()
        titles = words or VENDOR_TITLES.get(place["kind"], (place["kind"],))
        best = None
        for m in snap["mobiles"]:
            label = (m.get("label") or m.get("name") or "").lower()
            # A guildmaster stands in the shop but sells nothing (ModernUO's BaseGuildmaster is no
            # active vendor): a soak run's buys at the Britain healer went to the healer guildmistress
            # on the next tile and got no list.
            if m.get("human") and not m.get("monster") and m["notoriety"] == "invulnerable" and \
                    any(w in label for w in titles) and not GUILDMASTER.search(label) and \
                    m["serial"] not in exclude and m["distance"] <= 14:
                if best is None or m["distance"] < best["distance"]:
                    best = m
        if best is None:
            return place, 0, Result(False, f"no {'/'.join(titles)} in sight at {place['name']}")
        self.chosen = {"serial": best["serial"], "name": best.get("label") or best.get("name"), "tiles": best["distance"]}
        return place, best["serial"], None

    def asked(self, serial: int, e: dict[str, Any]) -> dict[str, Any]:
        """Which vendor an errand went to, for the log: its name and how far off it stood when it was
        chosen, how the errand ended, how many times the client asked it and its last words, and who else
        within a tile heard them no later than it did (`nearer`). "vendor buy" goes to the nearest vendor
        of any kind: a soak's first three buys went to the healer guildmistress, who shows no list, and
        the log couldn't say who had been asked (cuo-k0d)."""
        who = self.chosen if self.chosen and self.chosen["serial"] == serial else {"serial": serial}
        return {k: v for k, v in {**who, "state": e.get("state"), "asks": e.get("asks"), "said": e.get("said"),
                                  "nearer": e.get("nearer")}.items() if v is not None}

    async def pack_read(self, timeout_s: float = 10.0) -> bool:
        """Wait (up to timeout_s) until the client has looked into every bag in the pack, so the
        supplies it counts are all there; it opens them one a second while the agent is on. False
        when some are still unread; True at once with a client that doesn't say (no unread_bags)."""
        began = time.monotonic()
        while True:
            snap = await self.snap()
            if not snap.get("in_game") or snap["player"].get("supplies", {}).get("unread_bags", 0) == 0:
                return True
            if time.monotonic() - began >= timeout_s:
                return False
            await asyncio.sleep(0.5)

    async def held(self, item: str) -> int:
        """How many of an item the backpack holds, by the client's supply counts or by name."""
        snap = await self.snap(pack=True)
        supplies = snap["player"].get("supplies", {})
        key = {"bandage": "bandages", "bandages": "bandages", "heal potion": "heal_potions",
               "cure potion": "cure_potions", "arrow": "arrows", "arrows": "arrows", "bolt": "bolts",
               "bolts": "bolts"}.get(item.lower())
        if key:
            return supplies.get(key, 0)
        reg = item.lower().replace(" ", "_")
        if reg in (supplies.get("reagents") or {}):
            return supplies["reagents"][reg]
        word = item.lower().rstrip("s")
        return sum(i.get("amount", 1) for i in snap.get("pack", []) if word in i.get("name", "").lower())

    BUY_WALK_TILES = 200  # a buy walks to a shop no further than this; a further one is the planner's call

    async def buy(self, item: str, count: int, vendor_kind: str | None = None) -> Result:
        """Buy `count` of an item ("bandage", "black pearl") from the nearest vendors that sell it.
        A vendor only stocks so many (a healer has 20 bandages), so it goes round the vendors in
        sight, each once, then on to the next shop, until it has enough. It walks to no shop further
        than BUY_WALK_TILES: with Britain's healer sold out, a fleet NPC's buy went on to the Cove
        healer, 770 tiles and 6.6 minutes away past a bog thing and a plague beast (cuo-1kk). The
        result names the next shop and how far it is, and the planner decides."""
        start = await self.held(item)
        gold0 = (await self.snap())["player"].get("gold", 0)
        tried: set[int] = set()
        done_places: set[str] = set()
        spent, notes, place = 0, [], None
        asked: list[dict[str, Any]] = []
        last_place = None
        while await self.held(item) - start < count and len(tried) < 8 and len(done_places) < 3:
            place, serial, failed = await self.vendor(vendor_kind or item, (), exclude=tried, skip=done_places, sells=item,
                                                      max_walk=self.BUY_WALK_TILES)
            if failed and failed.data.get("too_far"):
                trip = f"{failed.summary}; travel_to it first if the trip is worth it"
                if not tried:
                    return Result(False, trip, failed.data)
                notes.append(f"nothing more at {last_place['name'] if last_place else 'the shops near'}; {trip}")
                place = last_place
                break
            last_place = place or last_place
            if failed:
                if place is None or "in sight" not in failed.summary:
                    if not tried:
                        return failed
                    if place is None:
                        notes.append(f"the world store knows no other {vendor_kind or 'vendor'} selling {item} nearby")
                    break
                done_places.add(place["name"])  # nobody left to buy from here: next shop
                continue
            tried.add(serial)
            want = count - (await self.held(item) - start)
            e = await self.errand("buy", target=serial, items=f"{item}:{want}")
            spent += -(e.get("gold_change") or 0)
            notes.append(e.get("detail") or e.get("state", "?"))
            asked.append(self.asked(serial, e))
        got = await self.held(item) - start
        # The gold carried before and after: the client's count at the moment an errand ends can
        # come before the server's gold update (a soak run logged 20 bandages "for 0 gold").
        await asyncio.sleep(1.0)
        spent = max(spent, gold0 - (await self.snap())["player"].get("gold", gold0))
        ok = got >= count
        self.log("buy", place=place["name"] if place else None, item=item, count=count, got=got, ok=ok,
                 detail=notes, gold=-spent, vendors=asked)
        if got and place:
            self.world.add_place(place["kind"], place["name"], place["x"], place["y"], z=place.get("z"),
                                 sells=place.get("sells"), source="seen")
        place = place or last_place
        where = place["name"] if place else "no vendor"
        carried = (await self.snap())["player"].get("gold", 0)
        if not ok and carried < 50:
            notes.append(f"carrying only {carried} gold: withdraw some at a bank first")
        return Result(ok, f"{where}: bought {got} of {count} {item} for {spent} gold"
                          + ("" if ok else f" ({'; '.join(notes)})"), {"bought": got, "gold_spent": spent})

    # What each kind of character carries into a hunt: (at least, buy up to), as the planner's prompt
    # asks for (planner.SYSTEM). A party's followers restock by these when the leader calls a restock.
    REAGENTS = ("black pearl", "blood moss", "garlic", "ginseng", "mandrake root", "nightshade", "sulfurous ash",
                "spiders silk")
    PAGAN = ("bat wing", "grave dust", "daemon blood", "nox crystal", "pig iron")
    PRICE = 6  # gold an item, about: bandages and reagents are 3-7 at ModernUO's vendors

    def wants(self, plays: str, snap: dict[str, Any]) -> dict[str, tuple[int, int]]:
        bandages = {"tamer": (80, 120), "mage-tamer": (80, 120), "necromancer": (30, 50)}.get(plays, (50, 80))
        out: dict[str, tuple[int, int]] = {} if plays == "mage" else {"bandage": bandages}
        if plays in MAGERY_PLAYS:
            out |= {r: (30, 50) for r in self.REAGENTS}
        if plays == "necromancer":
            out |= {r: (30, 50) for r in self.PAGAN}
        if plays == "archer":
            ammo = "bolt" if "bolt" in str((snap["player"].get("ranged") or {}).get("ammo", "")).lower() else "arrow"
            out[ammo] = (150, 200)
        return out

    async def restock(self) -> Result:
        """Buy what the character is short of for what it plays as (wants), drawing gold from the bank
        first when it carries too little. A party's followers do this when the leader calls a restock."""
        await self.pack_read()
        snap = await self.snap(pack=True)
        plays = state.archetype_of(snap)
        needs = []
        for item, (least, up_to) in self.wants(plays, snap).items():
            have = await self.held(item)
            if have < least:
                needs.append((item, up_to - have))
        if not needs:
            return Result(True, f"supplies enough for a {plays}: nothing to buy", {"bought": {}})
        notes = []
        gold = snap["player"].get("gold", 0)
        cost = sum(n for _, n in needs) * self.PRICE
        if gold < cost:
            b = await self.bank("loot", f"gold:{cost - gold + 100}")
            notes.append(b.summary)
        bought: dict[str, int] = {}
        for item, n in needs:
            r = await self.buy(item, n)
            bought[item] = r.data.get("bought", 0)
            if not r.ok:
                notes.append(r.summary)
        ok = all(bought[i] >= n for i, n in needs)
        got = ", ".join(f"{bought[i]} {i}" for i, _ in needs)
        return Result(ok, f"restocked as a {plays}: bought {got}" + (f" ({'; '.join(notes)})" if notes else ""),
                      {"bought": bought})

    async def sell(self, items: str = "loot", vendor_kind: str = "weaponsmith") -> Result:
        """Sell loot (or named items) to the nearest vendor of a kind."""
        place, serial, failed = await self.vendor(vendor_kind, ())
        if failed:
            return failed
        e = await self.errand("sell", target=serial, items=items)
        ok = e.get("state") == "done"
        self.log("sell", place=place["name"], items=items, ok=ok, detail=e.get("detail"), gold=e.get("gold_change"),
                 vendors=[self.asked(serial, e)])
        return Result(ok, f"{place['name']}: {e.get('detail', e.get('state'))}", {"gold_change": e.get("gold_change")})

    async def defended(self, work: Any) -> Any:
        """Run a goal (a coroutine: travel, rest, an errand) with Jev fighting beside it, but only
        what is close or attacking, and never seeking or looting. In a soak run the character
        rested at the graveyard while a lich walked up, and nothing fought back."""
        if self.judge is None:
            return await work
        stop = asyncio.Event()
        fight = asyncio.get_running_loop().create_task(loop.run(
            self.rpc, self.judge, loop.LoopConfig(), replace(self.pcfg, defend_only=True), self.decisions_log, stop,
            archetype=self.archetype, on_snapshot=self.seen, bestiary=self.world.bestiary()))
        try:
            return await work
        finally:
            stop.set()
            try:
                await asyncio.wait_for(fight, 5)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001 - the goal's result matters, not the guard's
                fight.cancel()

    async def rest(self, seconds: float = 30) -> Result:
        await self.rpc.call("mode", mode="auto")
        if seconds >= 20:
            await self.play("rest")
        began = looked = time.monotonic()
        snap = await self.snap()
        ghost = bool(snap.get("in_game") and snap["player"].get("dead"))
        while time.monotonic() - began < seconds and not self.interrupt.is_set():
            await asyncio.sleep(1)
            if time.monotonic() - looked >= 3 and not ghost:
                looked = time.monotonic()
                snap = await self.snap()
                if snap.get("in_game") and snap["player"].get("dead"):
                    # Killed while it rested: its resurrection can't wait for the rest to run out.
                    return Result(False, f"died while resting, after {round(time.monotonic() - began)} s", {"died": True})
        p = (await self.snap())["player"]
        return Result(True, f"rested {round(time.monotonic() - began)} s",
                      {"health": f"{p['hits']}/{p['hits_max']}", "mana": f"{p['mana']}/{p['mana_max']}"})

    # ------------------------------------------------------------ hunting

    def fallen_beside(self, snap: dict[str, Any]) -> bool:
        """A companion's ghost in view, in a party that raises its fallen: no wandering off round the spawn
        while one of them lies there (a walk further than two tiles breaks a bandage on the dead)."""
        return self.raise_fallen and any(m.get("dead") and m.get("name") in self.pcfg.companions and m["distance"] <= 20
                                         for m in snap.get("mobiles") or [])

    PARTY_FIGHT_S = 90.0  # how long a hunt's stop waits for the party's fight to end

    def party_fights_on(self, stop: str, held: list[float]) -> bool:
        """Whether a hunt's stop waits: a party that formed up is still in the fight (cuo-cym.1). Its leader's
        hunt is the party's: live, the warrior had run from a bone knight at 22% when Jev's "not worth
        staying here" (asked at a kill, with four creatures about) ended his hunt, his planner chose the
        next goal, and that cut the other four off in the middle of the fight they were winning. A death
        doesn't wait, and nothing waits longer than PARTY_FIGHT_S."""
        link = self.pcfg.link
        if stop == "died" or link is None or getattr(link, "rally", None) is None:
            return False
        try:
            rows = link.party.fights()
        except Exception:  # noqa: BLE001 - a store busy for a moment: the stop stands
            return False
        if not any(not r.get("dead") and (r.get("target") or r.get("attackers")) for r in rows.values()):
            return False
        now = time.monotonic()
        if not held:
            held.append(now)
            self.log("hunt_goes_on", stop=stop, why="the party is still fighting")
        return now - held[0] < self.PARTY_FIGHT_S

    def hunt_spot(self, area: str) -> tuple[int, int, str, int] | None:
        """Where a hunt of an area goes: the centre of its spawn (or the place of that name), its name, and
        how far round it the creatures spawn."""
        spawns = self.world.what_spawns(area=area, limit=1)
        if spawns and area.strip().lower() not in (str(spawns[0].get("area") or "").lower(),
                                                   str(spawns[0].get("region") or "").lower()) \
                and self.world.place(area, limit=1):
            # The name of a place inside several areas' names ("near", "west of", "south of Cove Orc Fort"): the one
            # nearest the place, not the one with the most creatures. A hunt of the Cove Orc Fort went 813 tiles west
            # to boars and bears instead of to the orcs by the fort (cuo-821).
            try:
                spawns = self.world.what_spawns(area=area, near=area, limit=1) or spawns
            except WorldError:
                pass
        spawns = spawns or self.world.what_spawns(near=area, limit=1)
        if spawns:
            return spawns[0]["x"], spawns[0]["y"], spawns[0]["area"], min(int(spawns[0].get("range") or 10), 15)
        target = self.resolve(area, None, None)
        return (*target, 10) if target else None

    async def to_the_rally(self, name: str, cx: int, cy: int, radius: int) -> Result:
        """A trip of this member's own into the hunt its party's plan sends it to (a party with a planner each that
        forms up, cuo-cym.14): it goes no further than the party's rally point at the hunt's edge, or holds the
        rally there when it is the first, and is one of those gathered until the word to go in. Its planner
        chooses its next goal after that, the hunt they went in on as a rule. A trip to a place it left
        creatures at lately is refused, as any trip there is."""
        if refused := self.avoided(cx, cy):
            return Result(False, refused)
        self.hunting_area = name  # on its way to that hunt, for the others' reports of whom to wait for
        try:
            formed = await self.defended(self.rally.before_hunt(f"hunt:{name.lower()}", name, cx, cy, radius))
        finally:
            self.hunting_area = ""
        data = {"formed_up": formed}
        if self.interrupt.is_set():
            return Result(False, f"did not get to {name}: interrupted while the party formed up", data)
        if not formed.get("formed"):
            why = formed.get("why") or formed.get("error") or "no word to go in"
            return Result(False, f"stopped at the edge of {name} to go in with the party, and didn't: {why}", data)
        return Result(True, f"at the party's rally point at the edge of {name}, and the word to go in has been given: "
                            f"hunt {name} now to go in with them", data)

    async def hunt(self, area: str, minutes: float = 10, min_bandages: int = 10, max_weight_pct: int = 85,
                   log_path: Path | None = None, rally_key: str | None = None) -> Result:
        """Go to a hunting area and let Jev fight there until time is up, or until Jev judges it's
        time to head back or the spot isn't worth it (routine.py). Code still stops on the floors
        (dead, out of supplies, a full bag); without Jev, min_bandages and max_weight_pct are the
        old fixed rules. Walks around the spawn when it is quiet. rally_key: a party's hunt, which it
        forms up for at the edge and goes into as one (rally.py)."""
        forming = self.rally if rally_key else None
        spot = self.hunt_spot(area)
        if spot is not None and rally_key == "area":
            # A party with a planner each: one rally for the place, whatever each planner called it.
            rally_key = f"hunt:{spot[2].lower()}"
        if spot is None or (refused := self.avoided(spot[0], spot[1])):
            if forming is not None and self.leads_party:
                forming.off(rally_key)  # the others don't wait at the edge for a hunt that isn't coming
            return Result(False, refused if spot else f"no hunting area called {area!r} in the world store")
        cx, cy, name, radius = spot
        self.hunting_area = name
        try:
            return await self._hunt(name, cx, cy, radius, minutes, min_bandages, max_weight_pct, log_path, forming, rally_key)
        finally:
            self.hunting_area = ""

    async def _hunt(self, name: str, cx: int, cy: int, radius: int, minutes: float, min_bandages: int,
                    max_weight_pct: int, log_path: Path | None, forming: Any, rally_key: str | None) -> Result:
        # A party forms up at the edge first, and with creatures in view from there the hunt starts where it
        # stands: the walk to the centre of the spawn took the first one in through the wave (cuo-cym.1).
        formed: dict[str, Any] = {}
        if forming is not None:
            formed = await self.defended(forming.before_hunt(rally_key, name, cx, cy, radius))
            if self.interrupt.is_set():
                return Result(False, f"did not hunt {name}: interrupted while the party formed up", {"formed_up": formed})
        if formed.get("formed") and formed.get("sighted"):
            r = Result(True, f"formed up with the party at the edge of {name}")
        else:
            # The walk there fights what attacks, as every other goal's walk does (cuo-sj2: a warrior walked
            # into the graveyard's lich with no decision for 32 s and died "on the way"). A party's follower
            # walks it beside the leader (cuo-cod.7).
            r = await self.defended(self.walk_with(self.beside, name, cx, cy) if self.beside and self.pace == "walk"
                                    else self.travel_to(name, cx, cy, distance=3, together=True))
        if not r.ok:
            return Result(False, f"could not get to {name}: {r.summary}", r.data)
        if self.judge is None:
            return Result(False, "no judge to fight with")

        await self.rpc.call("mode", mode="auto")
        stop = asyncio.Event()
        why: list[str] = []
        first: dict[str, Any] = {}
        last: dict[str, Any] = {}
        # When to head back, whether to stay, whether to walk elsewhere in the spawn: Jev's calls.
        hw = HuntWatch(name, minutes, self.judge, RoutineConfig(min_bandages=min_bandages, max_weight_pct=max_weight_pct),
                       log=self._log, archetype=self.archetype, radius=radius, bestiary=self.world.bestiary())
        asking: list[asyncio.Task] = []

        held: list[float] = []  # when a stop was first held back for the party's fight

        def watch(snap: dict[str, Any]) -> None:
            nonlocal first, last
            first = first or snap
            last = snap
            look = hw.observe(snap)
            if look.stop and not self.party_fights_on(look.stop, held):
                why.append(look.stop)
            elif self.interrupt.is_set():
                why.append("interrupted")
            elif any(t["kind"] in ("red", "criminal") for t in snap["agent"].get("threats", [])):
                why.append("a red or criminal player came close")
            note_threats(self.world, snap, self._threat_noted)
            self.seen(snap)
            if look.ask:
                asking.append(asyncio.get_running_loop().create_task(hw.ask(snap, look.ask)))
            if look.patrol and not (snap["agent"].get("travel") or {}).get("state") == "walking" \
                    and not self.fallen_beside(snap):
                # Quiet: wander to another spot of the spawn to find something.
                a = random.uniform(0, 6.283)
                tx, ty = cx + round(radius * 0.7 * math.cos(a)), cy + round(radius * 0.7 * math.sin(a))
                asyncio.get_running_loop().create_task(self.act("travel", x=tx, y=ty, distance=1, **self.paced()))
            if why:
                stop.set()

        def decided(_sit: Any, dec: policy.Decision) -> None:
            # A fall back to the party's rally point isn't a leave: the hunt goes on from there (cuo-cym.1).
            if dec.intent == "leave" and dec.leave_why and not dec.regroup:
                hw.left(dec.leave_why)

        lcfg = loop.LoopConfig(duration_s=minutes * 60)
        began = time.monotonic()
        if self.machine is not None:
            self.machine.reset()
        facts = FactPicker(self.world, self.judge, mode=self.facts_mode, focus=name)
        stats = await loop.run(self.rpc, self.judge, lcfg, self.pcfg, log_path or self.decisions_log, stop,
                               archetype=self.archetype, on_snapshot=watch, bestiary=self.world.bestiary(),
                               facts=facts, on_decision=decided, machine=self.machine)
        for task in asking:
            task.cancel()  # a question still out when the hunt ends has nothing left to decide
        retreat = ""
        if why and why[0].startswith("left the area"):
            # A pack of creatures that are each "a fair fight" isn't in the stronger-creature notes:
            # note what drove the character off, so the planner and hunting_spots know. Only creatures:
            # low supplies or a cautious strategy say nothing about the place.
            if hw.left_why and (" coming at once: " in hw.left_why or "stronger" in hw.left_why):
                self.world.add_note(f"Had to leave, {time.strftime('%Y-%m-%d %H:%M')}: {hw.left_why}", area=name,
                                    tags=["danger"], source="seen")
                p = (last or first).get("player") or {}
                for lx, ly in {(cx, cy), (p.get("x", cx), p.get("y", cy))}:
                    self.left_from.append((lx, ly, time.monotonic(), name, hw.left_why.split(";")[0]))
            retreat = await self.defended(self.retreat())
        elif why and why[0] == "a red or criminal player came close":
            # Driven off as surely as by a leave: the watchman went back to the orc fort two minutes after a red
            # player there ended his hunt, savages stronger than him in view, and died in 50 s (cuo-m3n.6).
            p = (last or first).get("player") or {}
            for lx, ly in {(cx, cy), (p.get("x", cx), p.get("y", cy))}:
                self.left_from.append((lx, ly, time.monotonic(), name, why[0]))
        s = stats.summary()["client_stats"]
        mins = round((time.monotonic() - began) / 60, 1)
        routine = hw.summary()
        for k in ("questions", "input_tokens", "cost_usd"):
            self.routine[k] += routine[k]
        self.routine["hunt_minutes"] += mins
        p0, p1 = (first or last).get("player", {}), last.get("player", {})
        s0, s1 = p0.get("supplies", {}), p1.get("supplies", {})
        data = {
            "minutes": mins, "kills": s.get("kills", 0), "deaths": s.get("deaths", 0),
            "bandages_used": s0.get("bandages", 0) - s1.get("bandages", 0),
            "heal_potions_used": s0.get("heal_potions", 0) - s1.get("heal_potions", 0),
            "gold_gained": p1.get("gold", 0) - p0.get("gold", 0),
            "weight": f"{p1.get('weight')}/{p1.get('weight_max')}",
            "health": f"{p1.get('hits')}/{p1.get('hits_max')}",
            "stopped_because": why[0] if why else f"{minutes} minutes up",
        }
        if retreat:
            data["then"] = retreat
        if formed:
            data["formed_up"] = formed
        if self.machine is not None:
            data["plan"] = self.machine.summary()
        from .state import archetype_of
        kit = self.archetype or (archetype_of(last) if last else "warrior")
        # Named after the log, so importing the log later (outcomes.py) replaces these rows.
        session = session_name(self.decisions_log) if self.decisions_log else None
        self.world.add_outcome(name, kit, "kills_per_hour", round(data["kills"] / max(mins / 60, 1e-6), 1), session=session)
        self.world.add_outcome(name, kit, "deaths", data["deaths"], session=session)
        self.log("hunted", area=name, kit=kit, routine=routine, **data)
        return Result(data["deaths"] == 0, f"hunted {name} for {mins} min: {data['kills']} kills, "
                                           f"stopped because {data['stopped_because']}"
                                           + (f"; then {retreat}" if retreat else ""), data)

    # ------------------------------------------------------------ after a death (cuo-4b3)

    HEALER_WORDS = ("healer", "mystic")

    def healer_in_view(self, snap: dict[str, Any]) -> dict[str, Any] | None:
        """The nearest healer NPC in view (its title says so), as a mobile from the snapshot."""
        found = [m for m in snap.get("mobiles", []) if m.get("human") and not m.get("dead")
                 and any(w in f"{m.get('name', '')} {m.get('label', '')}".lower() for w in self.HEALER_WORDS)]
        return min(found, key=lambda m: m["distance"]) if found else None

    STAND_OFF = 6  # tiles from a healer a ghost walks in from
    AROUND = ((0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1))

    async def stand_off(self, hx: int, hy: int, px: int, py: int, z: int, attempt: int = 0) -> tuple[int, int]:
        """Where a ghost stands before walking in to a healer at (hx, hy): 6 tiles off (or 5, in a small house) where
        the healer sees it, and sees the step to within 4 tiles on the way in (the server's rule, asked of the client),
        our own side first and then the nearest; each attempt the next. The Cove healer stands in her house's east
        doorway and walks about inside it: from the north the step to within 4 of her fell behind her wall, she never
        offered, and a ghost walked to five healers over 15 minutes (cuo-m3n.9). A spot she sees only across her wall
        is walked in from by her door, out of her sight, so the spot itself must be seen too. Without the client's
        answer, our own side at 6, as before."""
        d = max(1, tiles(px, py, hx, hy))
        ours = (hx + round((px - hx) * self.STAND_OFF / d), hy + round((py - hy) * self.STAND_OFF / d))
        around = {(hx + dx * k, hy + dy * k) for k in (self.STAND_OFF, self.STAND_OFF - 1) for dx, dy in self.AROUND}
        around = sorted(around - {ours}, key=lambda c: (tiles(hx, hy, *c) != self.STAND_OFF, tiles(px, py, *c),
                                                        abs(c[0] - px) + abs(c[1] - py)))
        spots = [ours, *around]

        def crossing(c: tuple[int, int]) -> list[int]:
            k = tiles(hx, hy, *c)
            return [hx + round((c[0] - hx) * 4 / k), hy + round((c[1] - hy) * 4 / k), z + 14]
        pairs = [pair for c in spots for pair in ([hx, hy, z + 14, *c, z + 14], [hx, hy, z + 14, *crossing(c)])]
        try:
            seen = (await self.rpc.call("sight", pairs=pairs))["in_sight"]
        except (RpcError, KeyError, TypeError):
            return ours
        good = [c for i, c in enumerate(spots) if seen[2 * i] and seen[2 * i + 1]] or [ours]
        return good[attempt % len(good)]

    async def resurrect(self, tries: int = 3, wait_s: float = 8.0) -> Result:
        """As a ghost, walk to the nearest healer and step up to it until it offers to resurrect
        (the client accepts in auto mode). On ModernUO a healer offers only when a ghost steps to
        within 4 tiles of it from further out, in its sight, and not more than once every 2 s, so
        arriving isn't enough: the ghost stands off at 6 tiles where that step is in the healer's
        sight (`stand_off`) and walks in, up to `tries` times per healer, at most three healers."""
        snap = await self.snap()
        if not snap["player"]["dead"]:
            return Result(True, "already alive")
        began = time.monotonic()
        # In a party that raises its fallen, by its corpse first, for a companion to bring it back (cuo-cym.4).
        if (by := await self.wait_to_be_raised()):
            secs = round(time.monotonic() - began)
            self.log("resurrected", by=by, seconds=secs)
            healed = await self.defended(self.recover_health())
            return Result(True, f"raised where it fell by {by}, in {secs} s; {healed}",
                          {"seconds": secs, "raised_by": by})
        snap = await self.snap()
        if not snap.get("in_game") or not snap["player"]["dead"]:
            return Result(bool(snap.get("in_game")), "already alive" if snap.get("in_game") else "not in game")
        here = self.where(snap)
        healers = self.world.find_place("healer", near=list(here), limit=12)
        # A ghost can't be hurt on the way, but a character resurrected out in the wilds comes back with
        # little health among whatever killed it: a soak character died five times running that way
        # (2026-10-09). Healers in a guarded town first, the rest after, nearest first within each.
        def guarded(h: dict[str, Any]) -> bool:
            region = self.world.region_at(h["x"], h["y"])
            return bool(region and region.get("guarded"))
        healers = sorted(healers, key=lambda h: (not guarded(h), h.get("distance", 0)))
        tried: list[str] = []
        for h in healers[:3]:
            r = await self.travel_to(h["name"], h["x"], h["y"], distance=5, refuse=False, pace="run")
            if not r.ok:
                tried.append(f"{h['name']}: {r.summary}")
                continue
            for attempt in range(tries):
                snap = await self.snap()
                if not snap["player"]["dead"]:
                    break
                mob = self.healer_in_view(snap)
                px, py = self.where(snap)
                hx, hy = (px + mob["dx"], py + mob["dy"]) if mob else (h["x"], h["y"])
                # Stand off at 6 tiles, then walk in to 1.
                ox, oy = await self.stand_off(hx, hy, px, py, snap["player"].get("z") or 0, attempt)
                if tiles(px, py, hx, hy) < 5 or attempt:
                    await self.walk_to(ox, oy, 1, pace="run")
                await self.walk_to(hx, hy, 1, pace="run")
                for _ in range(int(wait_s)):
                    await asyncio.sleep(1)
                    snap = await self.snap()
                    if not snap["player"]["dead"]:
                        break
                if not snap["player"]["dead"]:
                    break
            if not snap["player"]["dead"]:
                secs = round(time.monotonic() - began)
                self.log("resurrected", healer=h["name"], seconds=secs)
                healed = await self.defended(self.recover_health())
                return Result(True, f"resurrected at {h['name']} in {secs} s; {healed}",
                              {"seconds": secs, "healer": h["name"]})
            said = [e.get("text", "") for e in (snap.get("journal") or [])[-8:]]
            why = next((t for t in said if "resurrect" in t.lower() or "bank" in t.lower()), "no offer came")
            tried.append(f"{h['name']}: {why}")
        return Result(False, "not resurrected: " + ("; ".join(tried) or "no healer in the world store"),
                      {"seconds": round(time.monotonic() - began)})

    RAISE_WAIT_S = 60.0    # a ghost waits this long by its corpse to be raised, once the party's fight is over
    RAISE_MAX_S = 240.0    # ...and no longer than this in all, however long the fight goes on
    RAISE_NOBODY_S = 6.0   # ...nor more than this once nobody who could raise it is near and alive
    RAISE_TILES = 20       # near: a companion this close to the ghost (as far as a raiser walks, policy.RAISE_WALK)
    RAISE_BESIDE = 3       # ...and one this close is at it: the wait doesn't run out under its hands

    def raisers(self, here: tuple[int, int]) -> list[str]:
        """The companions who could raise this character now: alive, heard from, near, with the bandages
        and skill or the spell for it (by their own reports, party.raises_with)."""
        link = self.pcfg.link
        if link is None:
            return []
        return sorted(n for n, st in link.party.statuses().items()
                      if n != link.party.me and not st.get("dead") and st.get("raises") and st["age_s"] < 30
                      and tiles(st["x"], st["y"], *here) <= self.RAISE_TILES)

    async def wait_to_be_raised(self) -> str:
        """A ghost in a party that raises its fallen stays where it fell, in war mode so that the living can
        see it (ModernUO shows a ghost to the living only then), while a companion who could raise it is near
        and alive; the client accepts the offer. Nobody raises anyone in a fight, so the wait is counted from
        when the party's fight is over: RAISE_WAIT_S of quiet, RAISE_MAX_S in all. Who raised it ("a
        companion" when nobody called it), or "" when it is still a ghost: nobody could, nobody did, or the
        goal was cut short. Then it walks to a healer as before. Before this a member that died was out of
        the fight for the walk to town and back, alone, and a dead leader took the party's plan with it."""
        link = self.pcfg.link
        if link is None or not self.raise_fallen:
            return ""
        began, died = time.monotonic(), time.time()
        quiet = 0.0
        nobody_since: float | None = None
        shown = False
        while quiet < self.RAISE_WAIT_S and time.monotonic() - began < self.RAISE_MAX_S and not self.interrupt.is_set():
            snap = await self.snap()
            if not snap.get("in_game"):
                return ""
            if not snap["player"]["dead"]:
                # Whoever said they were at it: a raiser says so once, and the ghost can't tell whose try it was.
                calls = link.party.calls(within_s=self.RAISE_MAX_S + 30, kind="raising")
                who = list(dict.fromkeys(c["who"] for c in calls if c["about"] == link.party.me and c["t"] >= died - 5))
                return " and ".join(who) or "a companion"
            if self.raisers(self.where(snap)):
                nobody_since = None
                if not shown:
                    shown = True
                    await self.act("war_mode", on=True, source="manual")
                    self.log("waiting_to_be_raised", who=self.raisers(self.where(snap)))
            else:
                nobody_since = nobody_since or time.monotonic()
                if time.monotonic() - nobody_since >= self.RAISE_NOBODY_S:
                    break
            try:
                fighting = any(not r.get("dead") and not r.get("running") and (r.get("target") or r.get("attackers"))
                               for r in link.party.fights().values())
            except Exception:  # noqa: BLE001 - a store busy for a moment: counted as quiet
                fighting = False
            # A raiser beside it is trying: at one try in four, eight tries fit in the minute, and about one
            # ghost in ten would have walked off with a bandage on it. RAISE_MAX_S still ends the wait.
            can = self.raisers(self.where(snap))
            at_it = any(m.get("name") in can and not m.get("dead") and m["distance"] <= self.RAISE_BESIDE
                        for m in snap.get("mobiles") or [])
            await asyncio.sleep(1.0)
            quiet += 0.0 if fighting or at_it else 1.0
        if shown:
            await self.act("war_mode", on=False, source="manual")
        return ""

    async def recover_health(self, pct: float = 70, timeout_s: float = 120, mana_pct: float = 50) -> str:
        """Stand and heal (the client's reflexes bandage) until health is back to `pct`, and a caster's mana
        to `mana_pct`, or the time is up. Resurrected with no mana to recall, at a healer out in the open,
        every planner model sometimes set off on foot, and in the logs that walk died 3 times in 4 (cuo-tyv)."""
        began = time.monotonic()

        def words(p: dict[str, Any], secs: float) -> str:
            mana = f", mana {p.get('mana')}/{p.get('mana_max')}" if caster else ""
            return f"healed to {p.get('hits')}/{p.get('hits_max')}{mana} in {secs:.0f} s"

        caster = False
        while time.monotonic() - began < timeout_s:
            snap = await self.snap()
            p = snap.get("player") or {}
            if p.get("dead"):
                return "died while healing"
            caster = bool(p.get("mana_max")) and state.archetype_of(snap) in CASTER_PLAYS
            mana_back = not caster or p["mana"] * 100 >= mana_pct * p["mana_max"]
            if p.get("hits_max") and p["hits"] * 100 >= pct * p["hits_max"] and mana_back:
                return words(p, time.monotonic() - began)
            await asyncio.sleep(2)
        return words((await self.snap()).get("player") or {}, timeout_s)

    async def walk_to(self, x: int, y: int, distance: int = 1, timeout_s: float = 30, pace: str | None = None) -> str:
        """A short walk (no recall, no route record): the travel state it ended in."""
        res = await self.act("travel", x=x, y=y, distance=distance, **({"pace": pace} if pace else self.paced()))
        if res.get("status") != "done":
            return "refused"
        began = time.monotonic()
        while time.monotonic() - began < timeout_s:
            await asyncio.sleep(0.5)
            travel = (await self.snap())["agent"].get("travel") or {}
            if travel.get("state") != "walking":
                return str(travel.get("state"))
        await self.act("stop")
        return "timed out"

    async def recover_corpse(self, wait_s: float = 20.0, go_anyway: bool = False) -> Result:
        """Back to where the character died, open its corpse and take everything back. On an
        AOS-era server opening its own corpse puts everything back and on (ModernUO's self-loot);
        on an older one the client takes each item into the pack and puts back on what was worn."""
        snap = await self.snap()
        if snap["player"]["dead"]:
            return Result(False, "still a ghost: resurrect first")
        death = snap["player"].get("last_death")
        if not death:
            return Result(False, "no death this session to go back to")
        # The watchman walked back 27 minutes after a death, to a corpse long gone, and died among the savages
        # by it (cuo-m3n.6).
        ago = death.get("ago_s")
        if ago is not None and ago > self.CORPSE_LASTS_S:
            return Result(False, f"the corpse will have decayed by now: the death was {ago / 60:.0f} min ago, and a "
                                 f"corpse lasts about {self.CORPSE_LASTS_S // 60} minutes (7, then 7 as bones)")
        # In a soak a second death came walking back to a corpse with the lich that made it still there. Out in
        # the wilds, where only a region was looked at, the hamlet's watchman walked back to savages and died
        # on the way, more than once an hour (cuo-m3n.6).
        if not go_anyway and (danger := self.world.danger_near(death["x"], death["y"], minutes=15)):
            left = "" if ago is None else f" (it lasts about {(self.CORPSE_LASTS_S - ago) / 60:.0f} more minutes)"
            return Result(False, f"not going back yet: danger was seen by the corpse, {danger}; wait{left}, let it "
                                 "go, or call recover_corpse with go_anyway when it is worth the risk")
        began = time.monotonic()
        r = await self.travel_to(x=death["x"], y=death["y"], distance=1, refuse=False, pace="run")
        if not r.ok:
            return Result(False, f"could not get back to the corpse: {r.summary}", r.data)
        snap = await self.snap()
        corpse = next((c for c in snap.get("corpses", []) if c.get("own")), None)
        if corpse is None:
            return Result(False, "the corpse is gone (decayed or taken)", {"seconds": round(time.monotonic() - began)})
        used = await self.act("use", target=corpse["serial"])
        if used.get("status") not in (None, "done"):
            return Result(False, f"could not open the corpse: {used.get('detail') or used.get('status')}")
        taken = 0
        done = ""
        for _ in range(int(wait_s)):
            await asyncio.sleep(1)
            snap = await self.snap()
            said = " ".join(e.get("text", "") for e in (snap.get("journal") or [])[-6:]).lower()
            if "gather all of your belongings" in said:
                done = "gathered"
                break
            c = next((c for c in snap.get("corpses", []) if c.get("serial") == corpse["serial"]), None)
            items = (c or {}).get("items") or []
            if c is None or c.get("bones") and not corpse.get("bones"):  # an AOS self-loot leaves bones
                done = "the corpse turned to bones"
                break
            if c.get("opened") and not items:
                done = "emptied"
                break
            if "gather some of your belongings" in said:
                return Result(False, "took back what fitted; the rest is still on the corpse",
                              {"seconds": round(time.monotonic() - began)})
            if c.get("opened") and items and snap.get("era") == "pre-aos":
                for it in items:  # pre-AOS: no self-loot, take each item by hand
                    await self.act("take", target=it["serial"])
                    taken += 1
                await asyncio.sleep(2 + 0.6 * len(items))
                await self.act("rewear")
        secs = round(time.monotonic() - began)
        if not done:
            return Result(False, f"the corpse didn't give its things back in {secs} s (it never opened, or kept them)",
                          {"seconds": secs, "taken": taken})
        self.log("corpse_recovered", seconds=secs, taken=taken)
        return Result(True, f"took everything back from the corpse in {secs} s", {"seconds": secs, "taken": taken})

    async def retreat(self) -> str:
        """After leaving a hunt from danger, recall to a guarded town when a rune or runebook entry goes
        to one (a bank first, then the nearest), since the creatures may still be about. What
        happened, in words; "" with nothing to recall by."""
        snap = await self.snap()
        here = self.where(snap)
        # A recall takes seconds standing still, and a hit breaks it: not with anything close or fighting.
        bestiary = self.world.bestiary()
        about = [m for m in snap.get("mobiles", []) if state.threatening(m, bestiary)
                 and (m["distance"] <= 10 or m.get("war_mode"))]
        if about:
            return f"didn't stop to recall: {len(about)} creature{'s' if len(about) > 1 else ''} still about"
        items = snap.get("travel_items") or {}
        labels = [r["name"] for r in items.get("runes", [])] + \
            [e for book in items.get("runebooks", []) for e in book.get("entries", [])]
        best = None
        for label in labels:
            text = label.split(":", 1)[-1].lower().split(" for ", 1)[-1].replace("(felucca)", "").strip()
            hit = self.world.place(text, limit=1)
            region = self.world.region_at(hit[0]["x"], hit[0]["y"]) if hit else None
            if not region or not region.get("guarded"):
                continue
            rank = ("bank" not in text, tiles(*here, hit[0]["x"], hit[0]["y"]))
            if best is None or rank < best[0]:
                best = (rank, hit[0])
        if best is None:
            return ""
        place = best[1]
        await self.act("stop")
        how = await self.recall_to(place["name"], place["x"], place["y"], snap)
        self.log("retreat", to=place["name"], recalled=bool(how))
        return f"recalled to {place['name']} ({how})" if how else f"could not recall to {place['name']}"

    @staticmethod
    def mage(snap: dict[str, Any]) -> bool:
        from .state import archetype_of
        return archetype_of(snap) in ("mage", "mage-tamer", "necromancer")

    def routine_summary(self) -> dict[str, Any]:
        """Jev's routine questions over all hunts so far, and what they cost an hour of hunting."""
        return routine_totals(self.routine)


def routine_totals(r: dict[str, Any]) -> dict[str, Any]:
    hours = r["hunt_minutes"] / 60
    return {"routine_questions": r["questions"], "routine_input_tokens": r["input_tokens"],
            "routine_cost_usd": round(r["cost_usd"], 5),
            "routine_cost_per_hunt_hour": round(r["cost_usd"] / hours, 4) if hours else None}


def note_threats(world: World, snap: dict[str, Any], noted: dict[str, float], every_s: float = 600) -> None:
    """Record that a red or criminal player was seen here, once per area every ten minutes.
    Who it was never goes in: only what they were and where."""
    p = snap["player"]
    for t in snap["agent"].get("threats", []):
        if t["kind"] not in ("red", "criminal"):
            continue
        region = world.region_at(p["x"], p["y"])
        area = region["name"] if region else f"near {p['x']},{p['y']}"
        key = f"{area}:{t['kind']}"
        if time.monotonic() - noted.get(key, -1e9) > every_s:
            noted[key] = time.monotonic()
            world.add_note(f"A {t['kind']} player was seen here.", area=area, tags=("pk", "danger"), source="seen")


MAGERY_PLAYS = ("mage", "mage-tamer", "warrior-mage")
CASTER_PLAYS = (*MAGERY_PLAYS, "necromancer", "paladin")  # wait for mana after a resurrection
NOT_COUNTED = "not counted yet: a bag in the pack hasn't been looked into"


def describe(snap: dict[str, Any], world: World | None = None, companions: dict[str, str] | None = None) -> dict[str, Any]:
    """The character's situation in words, for the planner: what it plays as, and the supplies
    that matter for that. A mage heals with spells, so it isn't told of bandages; reagents that
    can't be counted yet (a bag not looked into) are said to be uncounted, not left out: a new
    mage told of 0 bandages and no reagents was sent shopping for both (cuo-pz9)."""
    p = snap["player"]
    s = p.get("supplies", {})
    plays = state.archetype_of(snap)
    unread = s.get("unread_bags", 0) > 0
    here = world.region_at(p["x"], p["y"]) if world else None
    out = {
        "plays_as": plays,
        "position": {"x": p["x"], "y": p["y"], "area": here["name"] if here else "open country"},
        "health": f"{p['hits']}/{p['hits_max']}",
        "gold_carried": p.get("gold", 0),
        "weight": f"{p.get('weight')}/{p.get('weight_max')} stones",
        "bandages": s.get("bandages", 0),
        "heal_potions": s.get("heal_potions", 0),
        "cure_potions": s.get("cure_potions", 0),
        "dead": p["dead"],
        "hostiles_in_sight": 0,
    }
    # Birds and deer only fight back: named apart, not counted as hostiles (cuo-s9p).
    bestiary = world.bestiary() if world else None
    calm = []
    for m in snap["mobiles"]:
        if state.threatening(m, bestiary):
            out["hostiles_in_sight"] += 1
        elif m.get("monster") and not m.get("dead"):
            calm.append(m.get("name") or "a creature")
    if calm:
        out["harmless_wildlife_in_sight"] = sorted(set(calm))[:3]
    if plays == "mage":
        del out["bandages"]
    if ranged := p.get("ranged"):
        # A bow shoots nothing without them: told nothing of her 300 arrows, the hamlet's hunter went to buy some,
        # found no seller near and gave up her archery (cuo-m3n.4).
        out[ranged.get("ammo", "arrows")] = s.get(ranged.get("ammo", "arrows"), 0)
    # A party's companions aren't strangers: "a unknown player 2 tiles away" was the mage beside it (cuo-4ar).
    names = {m["serial"]: m.get("name") for m in snap.get("mobiles") or []}
    threats = [t for t in snap["agent"].get("threats") or [] if names.get(t["serial"]) not in (companions or {})]
    if threats:
        out["players_near"] = [f"{'an' if t['kind'][:1] in 'aeiou' else 'a'} {t['kind']} player {t['distance']} tiles away"
                               for t in threats]
    if p.get("mana_max"):
        out["mana"] = f"{p['mana']}/{p['mana_max']}"
    regs = s.get("reagents") or {}
    if regs and any(regs.values()):
        out["reagents"] = {k.replace("_", " "): v for k, v in regs.items()}
    elif plays in MAGERY_PLAYS:
        out["reagents"] = NOT_COUNTED if unread else "none"
    if (pagan := s.get("pagan_reagents")) is not None:
        out["necromancer_reagents"] = {k.replace("_", " "): v for k, v in pagan.items()} \
            if any(pagan.values()) or not unread else NOT_COUNTED
    if "chivalry" in ((snap.get("magic") or {}).get("schools") or []):
        out["tithing_points"] = snap["magic"].get("tithing", 0)
    return out


def dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"))
