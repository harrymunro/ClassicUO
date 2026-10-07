"""Judgment benchmark: fights with a known right behaviour where fixed rules fail.

The arena can't tell Jev from the rules (both win every round), so each scenario
here is built so that the obvious rule does the wrong thing: a dangerous monster
joins weak ones, a caster stands behind melee fodder, a corpse holds valuables and
junk while a monster walks up, supplies run out mid-fight, a mage is swarmed.

A scenario declares its setup (AgentTestKit commands on the local server) and what
counts as right. The runner plays it many times per judge, records a trace of what
happened from the snapshots and the decision log, and reports success rates with
95% Wilson intervals, written to JSON so runs can be compared.
"""

import asyncio
import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import judge as judges
from . import loop, policy
from .rpc import AgentRpc
from .state import BODY_KINDS

# ---------------------------------------------------------------- trace


@dataclass
class Trace:
    """What happened in one round, reduced from every snapshot the loop saw."""

    started: float = field(default_factory=time.monotonic)
    start_ms: int = 0  # the client's clock when the round began; earlier deaths belong to an earlier round
    names: dict[int, str] = field(default_factory=dict)      # creature serial -> name (with kind)
    first_seen: dict[int, float] = field(default_factory=dict)
    died: list[tuple[float, int]] = field(default_factory=list)  # (t, serial), in order
    engaged: list[tuple[float, int]] = field(default_factory=list)  # each new target the agent took on
    flees: list[tuple[float, int]] = field(default_factory=list)  # (t, hp %) when a flee started
    hp: list[tuple[float, int]] = field(default_factory=list)
    player_died: bool = False
    died_at: float | None = None
    looting_with_monster_adjacent: float = 0.0  # seconds
    corpse_items: dict[int, str] = field(default_factory=dict)  # every item seen in a corpse
    pack_end: dict[int, str] = field(default_factory=dict)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    _last_t: float = 0.0
    _fleeing: bool = False
    _death_serials: set[int] = field(default_factory=set)

    def t(self) -> float:
        return time.monotonic() - self.started

    def add(self, snap: dict[str, Any]) -> None:
        t = self.t()
        dt = t - self._last_t if self._last_t else 0.0
        self._last_t = t
        p = snap["player"]
        hp = round(100 * p["hits"] / p["hits_max"]) if p.get("hits_max") else 100
        self.hp.append((t, hp))
        if p.get("dead") and not self.player_died:
            self.player_died, self.died_at = True, t

        nearest = 99
        for m in snap.get("mobiles", []):
            if not m.get("monster"):
                continue
            serial = m["serial"]
            if serial not in self.names:
                kind = BODY_KINDS.get(m.get("body", 0))
                name = m.get("name") or "a creature"
                self.names[serial] = f"{name} ({kind})" if kind and kind not in name.lower() else name
                self.first_seen[serial] = t
            if not m.get("dead"):
                nearest = min(nearest, m["distance"])

        # Only creatures seen alive as monsters count (the loot scenario's carrier dies unseen).
        for d in snap.get("deaths", []):
            if d["time_ms"] >= self.start_ms and d["serial"] in self.names and d["serial"] not in self._death_serials:
                self._death_serials.add(d["serial"])
                self.died.append((t, d["serial"]))

        agent = snap["agent"]
        engaged = agent.get("engaged", 0)
        if engaged and (not self.engaged or self.engaged[-1][1] != engaged):
            self.engaged.append((t, engaged))
        if agent.get("fleeing") and not self._fleeing:
            self.flees.append((t, hp))
        self._fleeing = bool(agent.get("fleeing"))
        if agent.get("looting") and nearest <= 1:
            self.looting_with_monster_adjacent += dt

        for c in snap.get("corpses", []):
            for it in c.get("items", []):
                self.corpse_items.setdefault(it["serial"], it.get("name") or "")

    # Helpers for the checks.

    def serials_named(self, *words: str) -> set[int]:
        return {s for s, n in self.names.items() if any(w in n.lower() for w in words)}

    def kills(self) -> list[int]:
        return [s for _, s in self.died]

    def engaged_any(self, serials: set[int]) -> bool:
        return any(s in serials for _, s in self.engaged)

    def taken(self, *words: str) -> list[str]:
        """Names of corpse items now in the pack that match any of the words."""
        return [n for s, n in self.pack_end.items() if s in self.corpse_items and any(w in n.lower() for w in words)]

    def actions(self, verb: str) -> list[dict[str, Any]]:
        return [a for d in self.decisions for a in d.get("actions", []) if a.get("verb") == verb]

    def summary(self) -> dict[str, Any]:
        return {
            "seconds": round(self.t(), 1),
            "player_died": self.player_died,
            "kills": [self.names.get(s, hex(s)) for s in self.kills()],
            "engaged": [self.names.get(s, hex(s)) for _, s in self.engaged],
            "flees_at_hp": [hp for _, hp in self.flees],
            "min_hp": min((h for _, h in self.hp), default=100),
            "decisions": len(self.decisions),
        }


# ---------------------------------------------------------------- scenarios


@dataclass
class Scenario:
    name: str
    bead: str
    right: str                      # the right behaviour, in words
    kit: str                        # "warrior" or "mage"
    setup: list[str]                # AgentTestKit commands after reset and kit
    seconds: float                  # round length
    check: Callable[[Trace], tuple[bool, dict[str, Any]]]
    quiet_after: float | None = None  # end early once no hostile is left after this many seconds
    template: str | None = None       # a fixed strategy template (adherence scenarios)


def check_mismatch(tr: Trace) -> tuple[bool, dict[str, Any]]:
    big = tr.serials_named("ogre lord")
    attacked = tr.engaged_any(big)
    return (not tr.player_died and not attacked), {"attacked_dangerous": attacked, "dangerous_seen": bool(big)}


def check_priority(tr: Trace) -> tuple[bool, dict[str, Any]]:
    casters = tr.serials_named("mage")
    kills = tr.kills()
    first = kills[0] if kills else None
    caster_first = first in casters
    return (caster_first and not tr.player_died), {"first_kill": tr.names.get(first, None) if first else None,
                                                  "caster_killed": any(k in casters for k in kills)}


VALUABLES = ("diamond", "ring", "longsword")
JUNK = ("bone", "head", "shirt", "kindling", "ribs")


def check_loot(tr: Trace) -> tuple[bool, dict[str, Any]]:
    good, junk = tr.taken(*VALUABLES), tr.taken(*JUNK)
    ok = len(good) >= 2 and len(junk) <= 1 and tr.looting_with_monster_adjacent <= 2.0 and not tr.player_died
    return ok, {"valuables_taken": good, "junk_taken": junk,
                "seconds_looting_with_monster_adjacent": round(tr.looting_with_monster_adjacent, 1)}


def check_attrition(tr: Trace) -> tuple[bool, dict[str, Any]]:
    return not tr.player_died, {"fled": bool(tr.flees), "kills": len(tr.kills())}


def check_swarm(tr: Trace) -> tuple[bool, dict[str, Any]]:
    kills = len(tr.kills())
    return (not tr.player_died and kills >= 4), {"kills": kills}


def check_never_flees(tr: Trace) -> tuple[bool, dict[str, Any]]:
    flees = len(tr.actions("flee"))
    return flees == 0, {"flee_actions": flees}


def check_no_loot(tr: Trace) -> tuple[bool, dict[str, Any]]:
    loots, takes = len(tr.actions("loot")), len(tr.actions("take"))
    taken = tr.taken(*VALUABLES, *JUNK)
    return loots == 0 and takes == 0 and not taken, {"loot_actions": loots, "take_actions": takes, "taken": taken}


def check_opens_with_explosion(tr: Trace) -> tuple[bool, dict[str, Any]]:
    first: dict[int, str] = {}
    for a in tr.actions("cast"):
        if a.get("reason") != "protection" and isinstance(a.get("target"), int):
            first.setdefault(a["target"], a.get("spell", ""))
    openers = list(first.values())
    ok = bool(openers) and all(s == "Explosion" for s in openers)
    return ok, {"openers": openers}


def check_strongest_first(tr: Trace) -> tuple[bool, dict[str, Any]]:
    strongest = tr.serials_named("troll")
    first = tr.engaged[0][1] if tr.engaged else None
    return first in strongest, {"first_engaged": tr.names.get(first) if first else None}


def check_flees(tr: Trace) -> tuple[bool, dict[str, Any]]:
    """For comparing templates: when (at what health) the first flee came, if any."""
    first = tr.flees[0][1] if tr.flees else None
    return not tr.player_died, {"first_flee_hp": first, "fled": first is not None}


SCENARIOS: dict[str, Scenario] = {s.name: s for s in [
    Scenario(
        "mismatch", "cuo-46e.2",
        "An ogre lord walks up while four weak monsters fight the warrior: leave, or at least don't engage it.",
        "warrior", ["[AgentArena 4 mix", "[AgentSpawn OgreLord 1 13 n 8"], 75, check_mismatch),
    Scenario(
        "priority", "cuo-46e.3",
        "Three orcs close by and an orcish mage casting from range: kill the mage first.",
        "warrior", ["[AgentSpawn Orc 3 3 s", "[AgentSpawn OrcishMage 1 9 n"], 120, check_priority, quiet_after=20),
    Scenario(
        "loot", "cuo-46e.4",
        "A corpse holds valuables and junk while an orc walks up: take the valuables, skip the junk, "
        "stop looting when the orc arrives.",
        "warrior", ["[AgentLoot 2 n", "[AgentSpawn Orc 1 15 s 10"], 60, check_loot),
    Scenario(
        "attrition", "cuo-46e.5",
        "A long fight on six bandages and one heal potion: disengage before the supplies are gone, "
        "unless the last monster is nearly dead.",
        "warrior", ["[AgentSupplies bandages 6 heal 1 cure 0", "[AgentArena 8 mix"], 120, check_attrition,
        quiet_after=20),
    Scenario(
        "swarm", "cuo-46e.6",
        "Six melee monsters on a mage: survive and kill at least four.",
        "mage", ["[AgentArena 6 mix"], 120, check_swarm, quiet_after=20),
    # Strategy adherence (cuo-46e.7): a template should change behaviour as written.
    Scenario(
        "relentless-never-flees", "cuo-46e.7", "With the relentless template, never flee, even running out of supplies.",
        "warrior", ["[AgentSupplies bandages 6 heal 1 cure 0", "[AgentArena 8 mix"], 120, check_never_flees,
        quiet_after=20, template="relentless"),
    Scenario(
        "survivor-flees", "cuo-46e.7", "With the survivor template, flee earlier (at higher health) than relentless.",
        "warrior", ["[AgentSupplies bandages 6 heal 1 cure 0", "[AgentArena 8 mix"], 120, check_flees,
        quiet_after=20, template="survivor"),
    Scenario(
        "no-loot-never-loots", "cuo-46e.7", "With the no-loot template, never loot, even with valuables at hand.",
        "warrior", ["[AgentLoot 2 n", "[AgentSpawn Orc 1 15 s 10"], 60, check_no_loot, template="no-loot"),
    Scenario(
        "nuker-opens-with-explosion", "cuo-46e.7", "With the nuker template, open on each creature with Explosion.",
        "mage", ["[AgentArena 3 mix"], 90, check_opens_with_explosion, quiet_after=15, template="nuker"),
    Scenario(
        "champion-strongest-first", "cuo-46e.7", "With the champion template, go for the troll before the weaker ones.",
        "warrior", ["[AgentSpawn Mongbat 2 6 e", "[AgentSpawn Orc 1 6 w", "[AgentSpawn Troll 1 7 n"], 45,
        check_strongest_first, quiet_after=15, template="champion"),
]}

# The core set that compares judges; adherence scenarios fix their own template.
CORE = ["mismatch", "priority", "loot", "attrition", "swarm"]


# ---------------------------------------------------------------- statistics


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a success rate; sensible for small n and rates near 0 or 1."""
    if n == 0:
        return 0.0, 1.0
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


# ---------------------------------------------------------------- runner


@dataclass
class JudgeSpec:
    """"heuristic", "jev", or "jev+<template>" (Jev with that strategy template)."""

    label: str

    @property
    def kind(self) -> str:
        return self.label.split("+", 1)[0]

    @property
    def template(self) -> str | None:
        return self.label.split("+", 1)[1] if "+" in self.label else None


async def say(rpc: AgentRpc, text: str, pause: float = 0.6) -> None:
    await rpc.call("act", verb="say", text=text, source="manual")
    await asyncio.sleep(pause)


async def prepare(rpc: AgentRpc, sc: Scenario, template: str | None, lane: int) -> None:
    """Reset, move to the lane, re-kit, set the strategy, and wait until the kit is known."""
    await rpc.call("mode", mode="off")
    await say(rpc, "[AgentReset")
    await say(rpc, f"[AgentGo {lane}")
    await say(rpc, f"[AgentKit {sc.kit}", 1.5)
    if template:
        await rpc.call("strategy", template=template, replace=True)
    else:
        await rpc.call("strategy", clear=True)
    # A mage's new spellbook and reagent bag are only known once the client has peeked inside.
    for _ in range(20):
        snap = await rpc.call("snapshot", since=0)
        magic = snap.get("magic")
        if sc.kit != "mage" or (magic and magic.get("book_known") and
                                sum(snap["player"]["supplies"].get("reagents", {}).values()) > 0):
            break
        await asyncio.sleep(0.5)
    await rpc.call("mode", mode="auto")


async def play_round(rpc: AgentRpc, sc: Scenario, spec: JudgeSpec, lane: int, log_path: Path,
                     price: float, min_confidence: float) -> dict[str, Any]:
    template = sc.template or spec.template
    await prepare(rpc, sc, template, lane)
    tr = Trace()
    stop = asyncio.Event()
    quiet_since: list[float | None] = [None]

    def watch(snap: dict[str, Any]) -> None:
        tr.add(snap)
        t = tr.t()
        if tr.player_died and tr.died_at is not None and t - tr.died_at > 2:
            stop.set()
        hostiles = [m for m in snap.get("mobiles", []) if m.get("monster") and not m.get("dead")]
        if sc.quiet_after is not None and t > sc.quiet_after:
            if hostiles or snap["agent"].get("looting"):
                quiet_since[0] = None
            elif quiet_since[0] is None:
                quiet_since[0] = t
            elif t - quiet_since[0] > 4:
                stop.set()

    tr.start_ms = (await rpc.call("snapshot", since=0))["time_ms"]
    for cmd in sc.setup:
        await say(rpc, cmd)
    tr.started = time.monotonic()

    judge = judges.make(spec.kind)
    lcfg = loop.LoopConfig(duration_s=sc.seconds, price_per_million=price)
    pcfg = policy.PolicyConfig(min_intent_confidence=min_confidence)
    try:
        stats = await loop.run(rpc, judge, lcfg, pcfg, log_path, stop, archetype=sc.kit, on_snapshot=watch)
    finally:
        await judge.close()
    await rpc.call("mode", mode="off")

    end = await rpc.call("snapshot", since=0, pack=True)
    tr.pack_end = {it["serial"]: it.get("name", "") for it in end.get("pack", [])}
    lines = log_path.read_text().splitlines() if log_path.exists() else []
    tr.decisions = [d for d in map(json.loads, lines) if d.get("type") == "decision"]
    ok, details = sc.check(tr)
    summary = stats.summary(price)
    return {"success": ok, **details, **tr.summary(), "log": str(log_path),
            "input_tokens": summary["input_tokens"], "est_cost_usd": summary["est_cost_usd"]}


async def run(rpc: AgentRpc, names: list[str], judge_labels: list[str], rounds: int, lane: int,
              out: Path, log_dir: Path, price: float = loop.LoopConfig.price_per_million,
              min_confidence: float = policy.PolicyConfig.min_intent_confidence,
              progress: Callable[[str], None] = print) -> dict[str, Any]:
    """Plays every scenario for every judge, rounds times, interleaving the judges so a slow
    drift in the server or the client hits them alike. Writes the JSON after every round."""
    result: dict[str, Any] = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "rounds": rounds, "lane": lane,
                              "scenarios": {}}
    log_dir.mkdir(parents=True, exist_ok=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    for name in names:
        sc = SCENARIOS[name]
        labels = [f"jev+{sc.template}"] if sc.template else judge_labels
        entry = result["scenarios"].setdefault(name, {"bead": sc.bead, "right": sc.right, "judges": {}})
        for n in range(1, rounds + 1):
            for label in labels:
                spec = JudgeSpec(label)
                log_path = log_dir / f"{name}-{label.replace('+', '_')}-{n:02d}.jsonl"
                log_path.unlink(missing_ok=True)
                try:
                    r = await play_round(rpc, sc, spec, lane, log_path, price, min_confidence)
                except Exception as e:  # a round that could not be played is not a failure of the judge
                    r = {"error": f"{type(e).__name__}: {e}"[:300]}
                j = entry["judges"].setdefault(label, {"runs": []})
                j["runs"].append(r)
                tally(j)
                progress(f"{name} {label} {n}/{rounds}: "
                         f"{'error ' + r['error'] if 'error' in r else ('right' if r['success'] else 'wrong')}"
                         f"  ({j['successes']}/{j['n']})")
                out.write_text(json.dumps(result, indent=2))
    await say(rpc, "[AgentReset")
    return result


def tally(j: dict[str, Any]) -> None:
    played = [r for r in j["runs"] if "error" not in r]
    j["n"] = len(played)
    j["successes"] = sum(r["success"] for r in played)
    j["rate"] = round(j["successes"] / j["n"], 3) if j["n"] else None
    lo, hi = wilson(j["successes"], j["n"])
    j["ci95"] = [round(lo, 3), round(hi, 3)]
    j["deaths"] = sum(r.get("player_died", False) for r in played)
    j["cost_usd"] = round(sum(r.get("est_cost_usd", 0) for r in played), 4)
    j["errors"] = len(j["runs"]) - len(played)


def table(results: list[dict[str, Any]], labels: list[str] | None = None) -> str:
    """A plain-text table of rates per scenario and judge, one column per results file."""
    rows = []
    for i, res in enumerate(results):
        for name, entry in res["scenarios"].items():
            for label, j in entry["judges"].items():
                if j.get("n"):
                    tag = labels[i] if labels else res.get("started", str(i))
                    rows.append(f"{name:<28} {label:<16} {j['successes']:>2}/{j['n']:<2} "
                                f"{100 * j['rate']:>5.0f}%  [{100 * j['ci95'][0]:.0f}-{100 * j['ci95'][1]:.0f}%]"
                                f"  deaths {j['deaths']:<2} ${j['cost_usd']:.3f}  {tag}")
    return "\n".join(rows)
