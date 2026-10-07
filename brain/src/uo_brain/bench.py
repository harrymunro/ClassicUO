"""Judgment benchmark: fights with a known right behaviour where fixed rules fail.

The arena can't tell Jev from the rules (both win every round), so each scenario
here is built so that the obvious rule does the wrong thing: a dangerous monster
joins weak ones, a caster stands behind melee fodder, a corpse holds valuables and
junk while a monster walks up, supplies run out mid-fight, a mage is swarmed.

A scenario declares its setup (AgentTestKit commands on the local server) and what
counts as right. A world-fact scenario (cuo-5of.7) also seeds a throwaway world store with
the facts that decide it, among many that don't, and is played under three conditions per
judge: no world facts (`@none`), every shortlisted fact dumped into the state (`@all`), and
the few Jev picks (`@jev`, facts.py). The runner plays it many times per judge, records a trace of what
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
from . import machine as machines
from .facts import FactPicker
from .rpc import AgentRpc
from .state import BODY_KINDS
from .world import World

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
    weapon_end: str = ""
    decisions: list[dict[str, Any]] = field(default_factory=list)
    pets: set[int] = field(default_factory=set)  # a tamer's pets, seen in the round
    pet_seen_at: float = 0.0
    pet_died: bool = False
    pet_hp: list[int] = field(default_factory=list)
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

        for pet in snap.get("pets", []):
            self.pets.add(pet["serial"])
            self.pet_seen_at = t
            if pet.get("hits_pct") is not None:
                self.pet_hp.append(pet["hits_pct"])

        # Only creatures seen alive as monsters count (the loot scenario's carrier dies unseen).
        for d in snap.get("deaths", []):
            if d["time_ms"] >= self.start_ms and d["serial"] in self.pets:
                self.pet_died = True
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

    @property
    def pet_lost(self) -> bool:
        """The pet died, or went out of sight for good (more than 5 s before the end)."""
        return self.pet_died or bool(self.pets) and self.t() - self.pet_seen_at > 5

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
    seed: Callable[[World], None] | None = None  # world facts, for a throwaway store (world-fact scenarios)


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


def check_archer(tr: Trace) -> tuple[bool, dict[str, Any]]:
    kills = len(tr.kills())
    return (not tr.player_died and kills >= 4), {"kills": kills, "kites": len(tr.actions("kite"))}


def check_tamer_orcs(tr: Trace) -> tuple[bool, dict[str, Any]]:
    kills = len(tr.kills())
    return (not tr.player_died and not tr.pet_lost and kills >= 3), \
        {"kills": kills, "pet_lost": tr.pet_lost, "pet_min_hp": min(tr.pet_hp, default=None),
         "pet_bandages": sum(1 for a in tr.actions("bandage") if a.get("reason") == "pet"),
         "pull_backs": sum(1 for a in tr.actions("pet") if a.get("kind") == "follow")}


def check_tamer_survives(tr: Trace) -> tuple[bool, dict[str, Any]]:
    return not tr.player_died, \
        {"kills": len(tr.kills()), "pet_lost": tr.pet_lost, "pet_min_hp": min(tr.pet_hp, default=None),
         "pull_backs": sum(1 for a in tr.actions("pet") if a.get("kind") == "follow"),
         "sent_at": [a.get("target") for a in tr.actions("pet") if a.get("kind") == "kill"][:5]}


def check_bard(tr: Trace) -> tuple[bool, dict[str, Any]]:
    kills = len(tr.kills())
    songs = [a.get("name") for a in tr.actions("skill") if a.get("targets")]
    return (not tr.player_died and kills >= 2), {"kills": kills, "songs": {s: songs.count(s) for s in set(songs)}}


def check_warrior_mage(tr: Trace) -> tuple[bool, dict[str, Any]]:
    kills = len(tr.kills())
    openers = sum(1 for a in tr.actions("cast") if a.get("reason") == "opener")
    return (not tr.player_died and kills >= 2 and openers >= 1 and bool(tr.weapon_end)), \
        {"kills": kills, "openers": openers, "weapon_at_end": tr.weapon_end}


def check_mage_tamer(tr: Trace) -> tuple[bool, dict[str, Any]]:
    kills = len(tr.kills())
    heals = sum(1 for a in tr.actions("cast") if a.get("reason") == "pet")
    return (not tr.player_died and not tr.pet_lost and kills >= 3), \
        {"kills": kills, "pet_lost": tr.pet_lost, "pet_min_hp": min(tr.pet_hp, default=None), "pet_spell_heals": heals}


def check_attrition(tr: Trace) -> tuple[bool, dict[str, Any]]:
    return not tr.player_died, {"fled": bool(tr.flees), "kills": len(tr.kills())}


def check_swarm(tr: Trace) -> tuple[bool, dict[str, Any]]:
    kills = len(tr.kills())
    return (not tr.player_died and kills >= 4), {"kills": kills}


def check_pack(tr: Trace) -> tuple[bool, dict[str, Any]]:
    """Survive a pack: what counts is being alive at the end. When the agent decided to leave and
    at what health are reported, and how much of the pack it took on."""
    leaves = [d for d in tr.decisions if d.get("intent") == "leave" and d.get("leave_why")]
    pack = tr.serials_named("gargoyle", "bone knight", "skeleton")
    return not tr.player_died, {"left": bool(leaves), "left_at_hp": leaves[0]["state"]["you"]["health"] if leaves else None,
                                "why": leaves[0]["leave_why"][:120] if leaves else None,
                                "kills": len(tr.kills()), "engaged_pack": tr.engaged_any(pack),
                                "min_hp": min((h for _, h in tr.hp), default=100)}


def check_necro(tr: Trace) -> tuple[bool, dict[str, Any]]:
    kills = len(tr.kills())
    spells = [a.get("spell") for a in tr.actions("cast")]
    return (not tr.player_died and kills >= 3), {"kills": kills, "spells": {s: spells.count(s) for s in set(spells)}}


def check_paladin(tr: Trace) -> tuple[bool, dict[str, Any]]:
    kills = len(tr.kills())
    blessings = [a.get("spell") for a in tr.actions("cast") if str(a.get("reason", "")).startswith("blessing")]
    return (not tr.player_died and kills >= 4 and bool(blessings)), \
        {"kills": kills, "blessings": {s: blessings.count(s) for s in set(blessings)}}


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


def check_wisp_left_alone(tr: Trace) -> tuple[bool, dict[str, Any]]:
    wisps = tr.serials_named("wisp")
    attacked = tr.engaged_any(wisps)
    orcs = sum(1 for s in tr.kills() if s not in wisps)
    return (not tr.player_died and not attacked and orcs >= 2), {"attacked_wisp": attacked, "wisp_seen": bool(wisps),
                                                                "orcs_killed": orcs}


# ---------------------------------------------------------------- world facts (cuo-5of.7)

TEST_FIELD = "Green Acres test field"


def seed_test_field(w: World, decisive: list[tuple[str | None, str]]) -> None:
    """The test field as a region (lanes 0-9), notes about it and its usual creatures that don't
    decide anything, and the scenario's decisive notes in among them."""
    w.replace_source("regions", "bench", [{"name": TEST_FIELD, "map": "Felucca", "kind": "area", "guarded": 0,
                                           "rects": json.dumps([[5400, 1100, 6050, 1210]]), "go_x": 5445,
                                           "go_y": 1153, "go_z": 0}])
    w.refresh()
    filler = [
        (TEST_FIELD, "Green Acres test field has no guards: anything can attack you here and nobody comes to help."),
        (TEST_FIELD, "The test field is flat open grass with nothing to hide behind."),
        (TEST_FIELD, "There is no bank or healer within walking distance of the test field; recall to town."),
        (TEST_FIELD, "Orcs at the test field are an easy kill for the warrior kit: about 1 bandage a fight over 25 fights."),
        (TEST_FIELD, "Mongbats at the test field die in one or two hits."),
        (TEST_FIELD, "Creatures at the test field come from the test kit's commands, not from spawners."),
        (TEST_FIELD, "Green Acres was farmland before it became the test field; the fences in the north are decoration."),
        (TEST_FIELD, "Night and rain make no difference to fighting at the test field."),
        (TEST_FIELD, "The test field's lanes are 60 tiles apart, so test characters in other lanes never meet."),
        (TEST_FIELD, "Ratmen at the test field carry a little gold and sometimes a ring."),
        (TEST_FIELD, "Gold dropped at the test field is worth picking up; bones and hides are not."),
        (TEST_FIELD, "Headless ones sometimes wander into the test field from the east."),
        (None, "Wisps glow, so they are easy to spot at night."),
        (None, "A wisp's corpse often holds gems and plenty of gold."),
        (None, "Orcs are evil, so killing them never makes anyone a criminal."),
        (None, "Orcs carry clubs or axes and hit for 5 to 7; a warrior in ringmail takes little from them."),
        (None, "Orc camps north of Yew hold up to 12 orcs and an orc captain."),
        (None, "Orc lords drop more gold than plain orcs."),
        (None, "A warrior should carry at least 50 bandages before going hunting."),
        (None, "A warrior's bandages take about 5 seconds; Healing and Anatomy make them heal more."),
        (None, "Warriors in ringmail can't meditate, which doesn't matter to a warrior."),
        (None, "Warriors do well against skeletons with a mace."),
        ("Britain Graveyard", "Spectres and wraiths at the Britain graveyard cost a new warrior 3 to 4 bandages a fight."),
        ("Britain", "The Britain healer stocks 20 bandages at a time."),
        ("Covetous", "Liches on the lower levels of Covetous are far too strong for a new character."),
        (None, "A mage should carry at least 30 of each reagent before hunting."),
    ]
    rows = filler[:6] + decisive + filler[6:]
    w.replace_source("notes", "bench", [{"area": a, "text": t, "source": "bench"} for a, t in rows])
    w.add_note("Green Acres test field: 5.1 kills a loop for the warrior kit over 18 loops of about 1.4 min, 4 deaths "
               "in 18 loops, 0.8 bandages and 0.2 heal potions a kill; orcs cost the most bandages, 1 a fight over "
               "25 fights.", area=TEST_FIELD, source="outcomes")


def seed_wisp(w: World) -> None:
    seed_test_field(w, [
        (TEST_FIELD, "Wisps at the test field never attack first: leave one alone and it leaves you alone. Attacked, "
                     "a wisp hits for 17 to 18, casts spells and has about 130 hits; it killed the warrior kit in "
                     "3 of 3 fights here."),
        (None, "New characters should leave wisps alone: a wisp only fights when attacked, and then it hits harder "
               "than an ogre and heals itself with magic."),
    ])


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
        "Three zombies close by and an orcish mage casting from range: kill the mage first.",
        # Zombies, not orcs: three orcs and the mage's spells killed the test warrior in most rounds
        # whatever it targeted, so the scenario measured luck rather than the choice.
        "warrior", ["[AgentSpawn Zombie 3 3 s", "[AgentSpawn OrcishMage 1 9 n"], 120, check_priority, quiet_after=20),
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
    # Packs (cuo-d28.9): several creatures that each look like a fair fight, or two stronger ones,
    # come at a warrior that is already fighting. The soak runs died this way.
    Scenario(
        "pack-gargoyles", "cuo-d28.9",
        "Two orcs fight a warrior; three gargoyles (a fair fight each, and spellcasters) come at it from 14 tiles: "
        "get away before they are on it, and survive.",
        "warrior", ["[AgentSpawn Orc 2 3 s", "[AgentSpawn Gargoyle 3 14 n 6"], 90, check_pack),
    Scenario(
        "pack-bone-knights", "cuo-d28.9",
        "Two orcs fight a warrior; two bone knights (each stronger than it) come at it from 14 tiles: get away "
        "before they are on it, and survive.",
        "warrior", ["[AgentSpawn Orc 2 3 s", "[AgentSpawn BoneKnight 2 14 n 6"], 90, check_pack),
    # Archetypes (cuo-cvl): the same judgments with another way of fighting.
    Scenario(
        "archer-kite", "cuo-cvl.1",
        "Four orcs come at an archer from 10 tiles: shoot them as they come, step back when two reach it, "
        "and kill all four without dying.",
        "archer", ["[AgentSpawn Orc 4 10 n"], 120, check_archer, quiet_after=20),
    Scenario(
        "tamer-orcs", "cuo-cvl.2",
        "Three orcs come at a tamer with a grizzly bear: set the bear on them, bandage it between hits, "
        "and call it back if it is losing; kill all three without losing the bear.",
        "tamer", ["[AgentSpawn Orc 3 8 n"], 120, check_tamer_orcs, quiet_after=20),
    Scenario(
        "tamer-ogre-lord", "cuo-cvl.2",
        "An ogre lord walks up to a tamer with a grizzly bear, two orcs with it. Neither can beat it, and it runs as "
        "fast as the tamer: leave early, and if it catches up, let the bear hold it off. The tamer must survive; "
        "losing the bear is reported.",
        # First written as "keep the bear too": every round of every judge lost both, since an ogre
        # lord outruns a tamer on foot; the bear holding it off is the only way out.
        "tamer", ["[AgentSpawn OgreLord 1 10 n", "[AgentSpawn Orc 2 6 n"], 90, check_tamer_survives,
        quiet_after=20),
    Scenario(
        "bard-provoke", "cuo-cvl.3",
        "An ogre and two orcs come at a bard: set them on each other and calm whatever reaches the bard; "
        "survive while at least two of them die.",
        "bard", ["[AgentSpawn Ogre 1 10 n", "[AgentSpawn Orc 2 8 n"], 120, check_bard, quiet_after=20),
    Scenario(
        "warrior-mage-opener", "cuo-cvl.3",
        "Two orcs come at a warrior-mage from 8 tiles: open on one with a spell while it comes, fight in melee, and "
        "have the sword back in hand after every cast; kill both.",
        "warriormage", ["[AgentSpawn Orc 2 8 n"], 90, check_warrior_mage, quiet_after=15),
    Scenario(
        "mage-tamer-orcs", "cuo-cvl.3",
        "Three orcs come at a mage-tamer with a grizzly bear: set the bear on them, cast at what it fights, heal it "
        "with spells; kill all three without losing the bear.",
        "magetamer", ["[AgentSpawn Orc 3 8 n"], 120, check_mage_tamer, quiet_after=20),
    # AOS schools (cuo-cvl.5): the necromancer casts from a distance as a mage does, the paladin
    # fights as a warrior and blesses its fighting.
    Scenario(
        "necro-orcs", "cuo-cvl.5",
        "Three orcs come at a necromancer from 8 tiles: kill them with necromancy without dying.",
        "necro", ["[AgentSpawn Orc 3 8 n"], 120, check_necro, quiet_after=20),
    Scenario(
        "paladin-orcs", "cuo-cvl.5",
        "Four orcs come at a paladin: fight them in melee with its blessings (at least one cast) and kill all four "
        "without dying.",
        "paladin", ["[AgentSpawn Orc 4 8 n"], 120, check_paladin, quiet_after=20),
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
    # World facts (cuo-5of.7): a stored fact decides the right move.
    Scenario(
        "wisp-leave-alone", "cuo-5of.7",
        "A wisp floats nearby while two orcs attack. The store says wisps never attack first and kill this kit "
        "when attacked: kill the orcs, never attack the wisp.",
        # The orcs first: with the wisp alone in sight for the first seconds, every judge (the rules
        # too) walked up to it and died within about 6 s, so the first run measured nothing.
        "warrior", ["[AgentSpawn Orc 2 3 s", "[AgentSpawn Wisp 1 9 n 4"], 75, check_wisp_left_alone, seed=seed_wisp),
]}

# The core set that compares judges; adherence scenarios fix their own template.
CORE = ["mismatch", "priority", "loot", "attrition", "swarm"]
KIT_ARCHETYPES = {"warriormage": "warrior-mage", "magetamer": "mage-tamer", "necro": "necromancer"}  # [AgentKit -> archetype
ARCHETYPES = ["archer-kite", "tamer-orcs", "tamer-ogre-lord", "bard-provoke", "warrior-mage-opener", "mage-tamer-orcs",
              "necro-orcs", "paladin-orcs"]
PACKS = ["pack-gargoyles", "pack-bone-knights"]
WORLD = [n for n, s in SCENARIOS.items() if s.seed]  # world-fact scenarios
FACT_MODES = ["none", "all", "jev"]


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

# Creature stats for the state's strength words, from the local world store when it exists.
BESTIARY: dict[str, dict[int, dict[str, Any]]] = {}


def load_bestiary(shard: str = "local") -> None:
    from .world import WORLDS_DIR, World

    if (WORLDS_DIR / shard / "world.sqlite").exists():
        with World.open(shard) as w:
            BESTIARY[shard] = w.bestiary()


@dataclass
class JudgeSpec:
    """"heuristic", "jev", or "jev+<template>" (Jev with that strategy template), each with an
    optional "/nokite" (no stepping back from melee), to measure what kiting is worth, "/nopack"
    (code doesn't leave from a pack; Jev's own leave still counts), "#<machine>" (follow that
    plan, a state machine from brain/machines), and for
    world-fact scenarios "@none", "@all" or "@jev" after it: which world facts reach Jev."""

    label: str

    @property
    def base(self) -> str:
        return self.label.split("@", 1)[0].split("#", 1)[0]

    @property
    def machine(self) -> str | None:
        """"jev#mage-swarm": follow the plan in brain/machines/mage-swarm.json (cuo-6om)."""
        return self.label.split("@", 1)[0].split("#", 1)[1] if "#" in self.label else None

    @property
    def kind(self) -> str:
        return self.base.split("/", 1)[0].split("+", 1)[0]

    @property
    def template(self) -> str | None:
        judge = self.base.split("/", 1)[0]
        return judge.split("+", 1)[1] if "+" in judge else None

    @property
    def kite(self) -> bool:
        return "/nokite" not in self.base

    @property
    def pack(self) -> bool:
        return "/nopack" not in self.base

    @property
    def facts(self) -> str:
        return self.label.split("@", 1)[1] if "@" in self.label else "none"


async def say(rpc: AgentRpc, text: str, pause: float = 0.6) -> None:
    await rpc.call("act", verb="say", text=text, source="manual")
    await asyncio.sleep(pause)


async def prepare(rpc: AgentRpc, sc: Scenario, template: str | None, lane: int) -> None:
    """Reset, move to the lane, re-kit, set the strategy, and wait until the kit is known."""
    await rpc.call("mode", mode="off")
    # The lane first: the reset sweeps leftovers around where the character stands, and after a
    # test elsewhere (a town) an ogre lord left on the lane killed the first round's warrior.
    await say(rpc, f"[AgentGo {lane}")
    await say(rpc, "[AgentReset")
    await say(rpc, f"[AgentKit {sc.kit}", 1.5)
    # Combat assist while the kit is read: the client only opens a new book or reagent bag while the
    # agent is on, and assist never moves the character. With the agent off, a caster started its
    # rounds with no reagents until the bag was opened a few seconds in.
    await rpc.call("mode", mode="assist")
    if template:
        await rpc.call("strategy", template=template, replace=True)
    else:
        await rpc.call("strategy", clear=True)
    # A mage's new spellbook and reagent bag are only known once the client has peeked inside.
    for _ in range(20):
        snap = await rpc.call("snapshot", since=0)
        magic = snap.get("magic")
        book = bool(magic and magic.get("book_known") and sum(snap["player"]["supplies"].get("reagents", {}).values()))
        necro = bool(magic and magic.get("book_known") and sum((snap["player"]["supplies"].get("pagan_reagents")
                                                                    or {}).values()))
        ready = {"mage": book, "warriormage": book, "magetamer": book and bool(snap.get("pets")), "necro": necro,
                 "paladin": bool(magic and magic.get("book_known") and "chivalry" in (magic.get("schools") or [])),
                 "archer": bool(snap["player"].get("ranged")), "tamer": bool(snap.get("pets")),
                 "bard": bool(snap["player"]["supplies"].get("instrument"))}
        if ready.get(sc.kit, True):
            break
        await asyncio.sleep(0.5)
    await rpc.call("mode", mode="auto")


async def play_round(rpc: AgentRpc, sc: Scenario, spec: JudgeSpec, lane: int, log_path: Path,
                     price: float, min_confidence: float, world: World | None = None) -> dict[str, Any]:
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
    pcfg = policy.PolicyConfig(min_intent_confidence=min_confidence, kite=spec.kite, leave_packs=spec.pack)
    facts = FactPicker(world, judge, mode=spec.facts, price_per_million=price) \
        if world is not None and spec.facts != "none" else None
    try:
        runner = machines.Runner(machines.load(spec.machine)) if spec.machine else None
        stats = await loop.run(rpc, judge, lcfg, pcfg, log_path, stop, archetype=KIT_ARCHETYPES.get(sc.kit, sc.kit),
                               on_snapshot=watch,
                               bestiary=BESTIARY.get("local"), facts=facts, machine=runner)
    finally:
        await judge.close()
    await rpc.call("mode", mode="off")

    end = await rpc.call("snapshot", since=0, pack=True)
    tr.pack_end = {it["serial"]: it.get("name", "") for it in end.get("pack", [])}
    tr.weapon_end = end["player"].get("weapon") or ""
    lines = log_path.read_text().splitlines() if log_path.exists() else []
    tr.decisions = [d for d in map(json.loads, lines) if d.get("type") == "decision"]
    ok, details = sc.check(tr)
    summary = stats.summary(price)
    out = {"success": ok, **details, **tr.summary(), "log": str(log_path),
           "input_tokens": summary["input_tokens"], "est_cost_usd": summary["est_cost_usd"]}
    if runner is not None:
        out["plan"] = runner.summary()
    if world is not None:
        out["facts"] = spec.facts
        if facts is not None:
            out.update(fact_selections=facts.selections, fact_input_tokens=facts.input_tokens,
                       facts_in_state=len(facts.chosen), facts_chosen=[f.text for f, _ in facts.chosen][:5])
    return out


async def run(rpc: AgentRpc, names: list[str], judge_labels: list[str], rounds: int, lane: int,
              out: Path, log_dir: Path, price: float = loop.LoopConfig.price_per_million,
              min_confidence: float = policy.PolicyConfig.min_intent_confidence,
              progress: Callable[[str], None] = print, fact_modes: list[str] | None = None) -> dict[str, Any]:
    """Plays every scenario for every judge, rounds times, interleaving the judges so a slow
    drift in the server or the client hits them alike. Writes the JSON after every round.
    A world-fact scenario plays each model judge once per fact mode (default none, all, jev),
    against its own store under log_dir/worlds/<scenario>/."""
    load_bestiary()
    result: dict[str, Any] = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "rounds": rounds, "lane": lane,
                              "bestiary": bool(BESTIARY.get("local")), "scenarios": {}}
    log_dir.mkdir(parents=True, exist_ok=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    for name in names:
        sc = SCENARIOS[name]
        labels = [f"jev+{sc.template}"] if sc.template else judge_labels
        world = None
        if sc.seed:
            labels = [label if JudgeSpec(label).kind == "heuristic" else f"{label}@{mode}"
                      for label in labels for mode in fact_modes or FACT_MODES]
            store = log_dir / "worlds" / name / "world.sqlite"
            store.unlink(missing_ok=True)
            world = World(store)
            sc.seed(world)
        entry = result["scenarios"].setdefault(name, {"bead": sc.bead, "right": sc.right, "judges": {}})
        for n in range(1, rounds + 1):
            for label in dict.fromkeys(labels):
                spec = JudgeSpec(label)
                log_path = log_dir / f"{name}-{label.replace('+', '_').replace('/', '_').replace('@', '_')}-{n:02d}.jsonl"
                log_path.unlink(missing_ok=True)
                try:
                    r = await play_round(rpc, sc, spec, lane, log_path, price, min_confidence, world)
                except Exception as e:  # a round that could not be played is not a failure of the judge
                    r = {"error": f"{type(e).__name__}: {e}"[:300]}
                j = entry["judges"].setdefault(label, {"runs": []})
                j["runs"].append(r)
                tally(j)
                progress(f"{name} {label} {n}/{rounds}: "
                         f"{'error ' + r['error'] if 'error' in r else ('right' if r['success'] else 'wrong')}"
                         f"  ({j['successes']}/{j['n']})")
                out.write_text(json.dumps(result, indent=2))
        if world is not None:
            world.close()
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
