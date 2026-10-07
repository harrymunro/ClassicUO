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
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import loop, policy
from .facts import FactPicker
from .judge import Judge
from .logs import session_name
from .routine import HuntWatch, RoutineConfig
from .rpc import AgentRpc
from .world import World, name_score, tiles, tokens

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
        self.decisions_log = decisions_log  # where hunts append Jev's decisions
        self.world = world
        self.judge = judge
        self.pcfg = pcfg or policy.PolicyConfig()
        self.archetype = archetype
        self._log = log or (lambda _: None)
        self.interrupt = asyncio.Event()  # set to cut the current goal short (handover, shutdown)
        self._threat_noted: dict[str, float] = {}
        self.recorder = None  # recorder.Recorder: what the character sees goes into the world store
        self.routine = {"questions": 0, "input_tokens": 0, "cost_usd": 0.0, "hunt_minutes": 0.0}  # all hunts
        self.facts_mode = "jev"  # which world facts reach the fights: jev (Jev picks), all, none (facts.py)

    def seen(self, snap: dict[str, Any]) -> None:
        if self.recorder is None:
            return
        self.recorder.observe(snap)
        if self.recorder.due():
            asyncio.get_running_loop().create_task(self.recorder.flush())

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
        found = self.world.place(place, limit=1)
        return (found[0]["x"], found[0]["y"], found[0]["name"]) if found else None

    # ------------------------------------------------------------ travel

    async def travel_to(self, place: str | None = None, x: int | None = None, y: int | None = None,
                        distance: int = 2, timeout_s: float = 600, recall: bool = True) -> Result:
        """Get to a named place or a tile: by Recall when a rune or runebook goes there,
        walking the rest (or all of it), fighting what attacks on the way."""
        target = self.resolve(place, x, y)
        if target is None:
            return Result(False, f"no place called {place!r} in the world store")
        tx, ty, name = target
        snap = await self.snap()
        start = self.where(snap)
        if tiles(*start, tx, ty) <= distance:
            return Result(True, f"already at {name}", {"x": tx, "y": ty})
        recalled = await self.recall_to(name, tx, ty, snap) if recall and tiles(*start, tx, ty) > 40 else None
        if recalled is not None:
            snap = await self.snap()
            start = self.where(snap)
            if tiles(*start, tx, ty) <= distance:
                return Result(True, f"recalled to {name} ({recalled})", {"x": start[0], "y": start[1], "by": "recall"})
        here = self.world.region_at(*start)
        from_name = here["name"] if here else f"{start[0]},{start[1]}"

        res = await self.act("travel", x=tx, y=ty, distance=distance)
        self.log("travel", to=name, x=tx, y=ty, status=res.get("status"), detail=res.get("detail"))
        if res.get("status") != "done":
            return Result(False, f"could not set off for {name}: {res.get('detail') or res.get('status')}")

        began = time.monotonic()
        trail: list[list[int]] = [list(start)]
        travel: dict[str, Any] = {}
        while time.monotonic() - began < timeout_s and not self.interrupt.is_set():
            await asyncio.sleep(1.0)
            snap = await self.snap()
            self.seen(snap)
            if snap["player"]["dead"]:
                return Result(False, "died on the way", {"at": list(self.where(snap))})
            pos = list(self.where(snap))
            if tiles(*pos, *trail[-1]) >= 8:
                trail.append(pos)
            travel = snap["agent"].get("travel") or {}
            if travel.get("state") != "walking":
                break
        else:
            await self.act("stop")
            travel = {"state": "interrupted" if self.interrupt.is_set() else "timed out", **travel}

        seconds = round(time.monotonic() - began)
        state = travel.get("state", "?")
        ok = state == "arrived"
        stuck = travel.get("stuck_at") or []
        trail.append(list(self.where(snap)))
        self.world.add_route(from_name, name, trail, outcome="ok" if ok else "stuck",
                             stuck_at=stuck or None, duration_s=seconds)
        self.log("travelled", to=name, state=state, seconds=seconds, stuck_at=stuck)
        if ok:
            return Result(True, f"arrived at {name} in {seconds} s", {"seconds": seconds, "replans": travel.get("replans", 0)})
        return Result(False, f"did not reach {name}: {state}, {travel.get('left', '?')} tiles short",
                      {"seconds": seconds, "stuck_at": stuck, "position": list(self.where(snap))})

    def rune_for(self, name: str, tx: int, ty: int, snap: dict[str, Any]) -> tuple[int, int, str] | None:
        """(serial, runebook entry or -1, label) of the rune or runebook entry that goes to a
        place: by its name, or by a name the world store puts within 20 tiles of it."""
        items = snap.get("travel_items") or {}
        options = [(r["serial"], -1, r["name"]) for r in items.get("runes", [])]
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
                      sells: str | None = None) -> tuple[dict[str, Any] | None, Result | None]:
        """The nearest place of a kind (or selling an item), walked to if further than `within`.
        With `sells`, places known not to sell that item are left out (a wandering healer is a
        healer that sells no bandages), and those known to sell it come first."""
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
        if tiles(*self.where(snap), place["x"], place["y"]) > within:
            r = await self.travel_to(place["name"], place["x"], place["y"], distance=max(1, within - 2))
            if not r.ok:
                return place, Result(False, f"could not get to {place['name']}: {r.summary}", r.data)
        return place, None

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
        return Result(ok, f"{place['name']}: {e.get('detail', e.get('state'))}", data | {"weight_before": before.get("weight")})

    async def vendor(self, kind: str, words: tuple[str, ...], exclude: set[int] = frozenset(),
                     skip: set[str] = frozenset(), sells: str | None = None) -> tuple[dict[str, Any] | None, int, Result | None]:
        place, failed = await self.go_near(kind, 8, skip, sells)
        if failed:
            return place, 0, failed
        snap = await self.snap()
        titles = words or VENDOR_TITLES.get(place["kind"], (place["kind"],))
        best = None
        for m in snap["mobiles"]:
            label = (m.get("label") or m.get("name") or "").lower()
            if m.get("human") and not m.get("monster") and m["notoriety"] == "invulnerable" and \
                    any(w in label for w in titles) and m["serial"] not in exclude and m["distance"] <= 14:
                if best is None or m["distance"] < best["distance"]:
                    best = m
        if best is None:
            return place, 0, Result(False, f"no {'/'.join(titles)} in sight at {place['name']}")
        return place, best["serial"], None

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

    async def buy(self, item: str, count: int, vendor_kind: str | None = None) -> Result:
        """Buy `count` of an item ("bandage", "black pearl") from the nearest vendors that sell it.
        A vendor only stocks so many (a healer has 20 bandages), so it goes round the vendors in
        sight, each once, then on to the next shop, until it has enough."""
        start = await self.held(item)
        tried: set[int] = set()
        done_places: set[str] = set()
        spent, notes, place = 0, [], None
        while await self.held(item) - start < count and len(tried) < 8 and len(done_places) < 3:
            place, serial, failed = await self.vendor(vendor_kind or item, (), exclude=tried, skip=done_places, sells=item)
            if failed:
                if place is None or "in sight" not in failed.summary:
                    if not tried:
                        return failed
                    break
                done_places.add(place["name"])  # nobody left to buy from here: next shop
                continue
            tried.add(serial)
            want = count - (await self.held(item) - start)
            e = await self.errand("buy", target=serial, items=f"{item}:{want}")
            spent += -(e.get("gold_change") or 0)
            notes.append(e.get("detail") or e.get("state", "?"))
        got = await self.held(item) - start
        ok = got >= count
        self.log("buy", place=place["name"] if place else None, item=item, count=count, got=got, ok=ok,
                 detail=notes, gold=-spent)
        if got and place:
            self.world.add_place(place["kind"], place["name"], place["x"], place["y"], z=place.get("z"),
                                 sells=place.get("sells"), source="seen")
        where = place["name"] if place else "no vendor"
        return Result(ok, f"{where}: bought {got} of {count} {item} for {spent} gold"
                          + ("" if ok else f" ({'; '.join(notes)})"), {"bought": got, "gold_spent": spent})

    async def sell(self, items: str = "loot", vendor_kind: str = "weaponsmith") -> Result:
        """Sell loot (or named items) to the nearest vendor of a kind."""
        place, serial, failed = await self.vendor(vendor_kind, ())
        if failed:
            return failed
        e = await self.errand("sell", target=serial, items=items)
        ok = e.get("state") == "done"
        self.log("sell", place=place["name"], items=items, ok=ok, detail=e.get("detail"), gold=e.get("gold_change"))
        return Result(ok, f"{place['name']}: {e.get('detail', e.get('state'))}", {"gold_change": e.get("gold_change")})

    async def rest(self, seconds: float = 30) -> Result:
        await self.rpc.call("mode", mode="auto")
        began = time.monotonic()
        while time.monotonic() - began < seconds and not self.interrupt.is_set():
            await asyncio.sleep(1)
        p = (await self.snap())["player"]
        return Result(True, f"rested {round(time.monotonic() - began)} s",
                      {"health": f"{p['hits']}/{p['hits_max']}", "mana": f"{p['mana']}/{p['mana_max']}"})

    # ------------------------------------------------------------ hunting

    async def hunt(self, area: str, minutes: float = 10, min_bandages: int = 10, max_weight_pct: int = 85,
                   log_path: Path | None = None) -> Result:
        """Go to a hunting area and let Jev fight there until time is up, or until Jev judges it's
        time to head back or the spot isn't worth it (routine.py). Code still stops on the floors
        (dead, out of supplies, a full bag); without Jev, min_bandages and max_weight_pct are the
        old fixed rules. Walks around the spawn when it is quiet."""
        spawns = self.world.what_spawns(area=area, limit=1) or self.world.what_spawns(near=area, limit=1)
        target = self.resolve(area, None, None)
        if spawns:
            cx, cy, name = spawns[0]["x"], spawns[0]["y"], spawns[0]["area"]
            radius = min(int(spawns[0].get("range") or 10), 15)
        elif target:
            cx, cy, name = target
            radius = 10
        else:
            return Result(False, f"no hunting area called {area!r} in the world store")

        r = await self.travel_to(name, cx, cy, distance=3)
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
                       log=self._log, archetype=self.archetype, radius=radius)
        asking: list[asyncio.Task] = []

        def watch(snap: dict[str, Any]) -> None:
            nonlocal first, last
            first = first or snap
            last = snap
            look = hw.observe(snap)
            if look.stop:
                why.append(look.stop)
            elif self.interrupt.is_set():
                why.append("interrupted")
            elif any(t["kind"] in ("red", "criminal") for t in snap["agent"].get("threats", [])):
                why.append("a red or criminal player came close")
            note_threats(self.world, snap, self._threat_noted)
            self.seen(snap)
            if look.ask:
                asking.append(asyncio.get_running_loop().create_task(hw.ask(snap, look.ask)))
            if look.patrol and not (snap["agent"].get("travel") or {}).get("state") == "walking":
                # Quiet: wander to another spot of the spawn to find something.
                a = random.uniform(0, 6.283)
                tx, ty = cx + round(radius * 0.7 * math.cos(a)), cy + round(radius * 0.7 * math.sin(a))
                asyncio.get_running_loop().create_task(self.act("travel", x=tx, y=ty, distance=1))
            if why:
                stop.set()

        lcfg = loop.LoopConfig(duration_s=minutes * 60)
        began = time.monotonic()
        facts = FactPicker(self.world, self.judge, mode=self.facts_mode, focus=name)
        stats = await loop.run(self.rpc, self.judge, lcfg, self.pcfg, log_path or self.decisions_log, stop,
                               archetype=self.archetype, on_snapshot=watch, bestiary=self.world.bestiary(),
                               facts=facts)
        for task in asking:
            task.cancel()  # a question still out when the hunt ends has nothing left to decide
        s = stats.summary(lcfg.price_per_million)["client_stats"]
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
        from .state import archetype_of
        kit = self.archetype or (archetype_of(last) if last else "warrior")
        # Named after the log, so importing the log later (outcomes.py) replaces these rows.
        session = session_name(self.decisions_log) if self.decisions_log else None
        self.world.add_outcome(name, kit, "kills_per_hour", round(data["kills"] / max(mins / 60, 1e-6), 1), session=session)
        self.world.add_outcome(name, kit, "deaths", data["deaths"], session=session)
        self.log("hunted", area=name, kit=kit, routine=routine, **data)
        return Result(data["deaths"] == 0, f"hunted {name} for {mins} min: {data['kills']} kills, "
                                           f"stopped because {data['stopped_because']}", data)

    @staticmethod
    def mage(snap: dict[str, Any]) -> bool:
        from .state import archetype_of
        return archetype_of(snap) in ("mage", "mage-tamer")

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


def describe(snap: dict[str, Any], world: World | None = None) -> dict[str, Any]:
    """The character's situation in words, for the planner."""
    p = snap["player"]
    s = p.get("supplies", {})
    here = world.region_at(p["x"], p["y"]) if world else None
    out = {
        "position": {"x": p["x"], "y": p["y"], "area": here["name"] if here else "open country"},
        "health": f"{p['hits']}/{p['hits_max']}",
        "gold_carried": p.get("gold", 0),
        "weight": f"{p.get('weight')}/{p.get('weight_max')} stones",
        "bandages": s.get("bandages", 0),
        "heal_potions": s.get("heal_potions", 0),
        "cure_potions": s.get("cure_potions", 0),
        "dead": p["dead"],
        "hostiles_in_sight": sum(1 for m in snap["mobiles"] if m.get("monster") and not m.get("dead")),
    }
    threats = snap["agent"].get("threats") or []
    if threats:
        out["players_near"] = [f"a {t['kind']} player {t['distance']} tiles away" for t in threats]
    if p.get("mana_max"):
        out["mana"] = f"{p['mana']}/{p['mana_max']}"
    regs = s.get("reagents") or {}
    if regs and any(regs.values()):
        out["reagents"] = {k.replace("_", " "): v for k, v in regs.items()}
    return out


def dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"))
