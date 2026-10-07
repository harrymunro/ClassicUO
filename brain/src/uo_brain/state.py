"""Turn a raw client snapshot into the semantic state Jev reads, plus candidate lists.

Jev reads words better than numbers (it does not do arithmetic or compare
distances reliably), so code does the maths and describes the result:
"badly wounded", "adjacent", "to the north". Candidates get short ids
(t1, c1, i1) that map back to serials, so the model only ever picks from
options code has already checked.
"""

from dataclasses import dataclass, field
from typing import Any

from .spells import (AREA_MIN, AREA_RADIUS, AREA_SPELLS, ATTACK_SPELLS, BLESSING_SPELLS, NECRO_AREA_SPELLS,
                     NECRO_ATTACK_SPELLS, WITHER_RADIUS, mana_words)

SPELL_RANGE = 10  # tiles; ModernUO's magery range from Mondain's Legacy on
MELEE_SKILLS = ("Swordsmanship", "Mace Fighting", "Fencing", "Archery")
SONGS = ("Provocation", "Peacemaking", "Discordance")
CASTERS = ("mage", "mage-tamer", "warrior-mage", "necromancer")  # archetypes that cast attack spells
RANGED_CASTERS = ("mage", "mage-tamer", "necromancer")  # ...and fight with them from a distance


# Body graphic -> what the creature is. Monsters often carry personal names
# ("Vorgak"), which tell the model nothing about the threat.
BODY_KINDS = {
    1: "ogre", 2: "ettin", 3: "zombie", 4: "gargoyle", 7: "orc captain", 8: "corpser", 9: "daemon",
    12: "dragon", 13: "air elemental", 14: "earth elemental", 15: "fire elemental", 16: "water elemental",
    17: "orc", 18: "ettin", 21: "giant serpent", 22: "gazer", 23: "wolf", 24: "lich", 26: "ghoul",
    28: "giant spider", 29: "gorilla", 30: "harpy", 31: "headless one", 33: "lizardman", 35: "lizardman",
    36: "lizardman", 39: "mongbat", 40: "balron", 42: "ratman", 44: "ratman", 45: "ratman", 47: "reaper",
    48: "scorpion", 50: "skeleton", 51: "slime", 52: "snake", 53: "troll", 54: "troll", 55: "troll",
    56: "skeleton", 57: "skeleton", 58: "wisp", 74: "imp", 98: "hell hound", 138: "orcish lord",
    140: "orcish mage", 181: "orc scout", 182: "orc bomber", 189: "orc brute", 211: "black bear",
    212: "grizzly bear", 213: "polar bear", 215: "giant rat", 225: "timber wolf", 238: "sewer rat",
}


def threat_words(creature: dict[str, Any], your_hits: int) -> str:
    """How a creature measures up to the character, in words, from its stats."""
    hits, grade = creature["hits"], creature.get("difficulty") or ""
    if grade in ("deadly", "strong") and hits >= 2 * max(your_hits, 1):
        return "far stronger than you: do not fight it alone"
    if grade in ("deadly", "strong") or hits >= 1.5 * max(your_hits, 1):
        return "stronger than you: a hard fight"
    if grade == "moderate":
        return "a fair fight"
    return "weak: an easy kill"


def known_creature(bestiary: dict[int, dict[str, Any]] | None, m: dict[str, Any]) -> dict[str, Any] | None:
    """The bestiary's entry for a creature in view: the kind that shares its body graphic and its
    name, else the strongest kind with that body. By body alone a black bear read as "far stronger"
    in a soak run: it shares its graphic with a raging grizzly."""
    known = (bestiary or {}).get(m.get("body", 0))
    if not known:
        return None
    name = (m.get("name") or "").strip().lower()
    return next((k for k in known.get("kinds", ()) if (k.get("name") or "").lower() == name), known)


# What one creature coming at the character weighs against it, in fair fights, by its strength
# words; scaled by its health, since a badly hurt one is nearly done.
THREAT_WEIGHTS = {"far stronger": 4.0, "stronger": 2.0, "a fair fight": 1.0, "weak": 0.4}
UNKNOWN_WEIGHT = 0.5  # no stats in the bestiary
PACK_FAR = 3.0        # the group together outweighs the character: too many to fight at once
PACK_HARD = 2.0


CASTER_FACTOR = 1.5   # a spellcaster hurts from a distance, running or not: two gargoyles killed a warrior in 6 s


def threat_weight(info: dict[str, Any], hits_pct: int | None, size: float = 1.0) -> float:
    """size: the creature's hits over the character's. A fair fight is weighed by it (between half
    and one and a half): graded alike, a spectre has 60 hits and a gargoyle 105, and two spectres
    weighed as two gargoyles made a warrior leave the graveyard it hunts at."""
    words = str(info.get("strength", ""))
    base = next((w for k, w in THREAT_WEIGHTS.items() if words.startswith(k)), UNKNOWN_WEIGHT)
    if words.startswith("a fair fight"):
        base *= min(1.5, max(0.5, size))
    if info.get("casts_spells"):
        base *= CASTER_FACTOR
    return base * max(0.25, (100 if hits_pct is None else hits_pct) / 100)


def coming_at(m: dict[str, Any]) -> bool:
    """Is this monster coming at the character: within 12 tiles and fighting (war mode), attacking
    it, or already on it."""
    return m["distance"] <= 12 and (bool(m.get("war_mode")) or bool(m.get("attacking_me")) or m["distance"] <= 2)


def pack_words(pack: list["Candidate"], weight: float) -> str:
    """The creatures coming at the character, together, in words: "2 gargoyles (a fair fight each)
    and a reaper (a fair fight); together far stronger than you: too many to fight at once"."""
    kinds: dict[str, list[str]] = {}
    for h in pack:
        kind = h.name.split(" (", 1)[-1].rstrip(")") if " (" in h.name else h.name
        kinds.setdefault(kind.removeprefix("a ").removeprefix("an "), []).append(
            (str(h.info.get("strength", "")).split(":", 1)[0] or "unknown strength")
            + (" and a spellcaster" if h.info.get("casts_spells") else ""))
    parts = []
    for kind, strengths in kinds.items():
        n = len(strengths)
        what = strengths[0] + (" each" if n > 1 else "")
        parts.append(f"{n} {kind}{'s' if n > 1 and not kind.endswith('s') else ''} ({what})" if n > 1
                     else f"{'an' if kind[:1] in 'aeiou' else 'a'} {kind} ({what})")
    listed = ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1]
    together = "together far stronger than you: too many to fight at once" if weight >= PACK_FAR \
        else "together stronger than you: a hard fight" if weight >= PACK_HARD else "together a fair fight"
    return f"{listed}; {together}"


def health_words(pct: int | None) -> str:
    if pct is None:
        return "unknown health"
    if pct >= 95:
        return "unhurt"
    if pct >= 75:
        return "lightly wounded"
    if pct >= 50:
        return "wounded"
    if pct >= 25:
        return "badly wounded"
    return "near death"


def distance_words(d: int) -> str:
    if d <= 1:
        return "adjacent, within weapon reach"
    if d <= 3:
        return "close, a few steps away"
    if d <= 7:
        return "nearby"
    return "far away"


def potion_words(count: int, ready_ms: int) -> str:
    if count == 0:
        return "none left"
    return "ready now" if ready_ms <= 0 else f"not for another {round(ready_ms / 1000)} seconds"


@dataclass
class Candidate:
    id: str
    serial: int
    name: str
    distance: int
    info: dict[str, Any]
    hits_pct: int | None = None
    casts: int = 0  # spells this mage has cast at it
    allowed: bool = True  # combat assist: the engage setting lets the agent take it on
    max_hits: int = 0     # from the bestiary: how much of a creature there is, for "strongest first"


@dataclass
class Situation:
    raw: dict[str, Any]
    state: dict[str, Any]
    hostiles: list[Candidate] = field(default_factory=list)
    corpses: list[Candidate] = field(default_factory=list)
    items: list[Candidate] = field(default_factory=list)
    hp_pct: int = 100
    strategy: str = ""
    archetype: str = "warrior"
    spells: list[Candidate] = field(default_factory=list)  # attack spells castable right now
    mana_pct: int = 100
    mode: str = "auto"         # "assist" is combat assist: the player drives, the agent only fights
    traveling: bool = False    # on a long walk (travel): no seeking or looting on the way
    engage: str = "defend"     # combat assist: follow (the player's target), defend (+ attackers), nearby
    known: list[str] = field(default_factory=list)  # world facts picked for this situation (facts.py)
    plan: dict[str, str] = field(default_factory=dict)  # the plan being followed and its step (machine.py)
    area_center: Candidate | None = None  # where an area spell hits most creatures and nobody else (casters)
    area_count: int = 0                   # ...and how many it hits there
    pack: list[Candidate] = field(default_factory=list)  # the creatures coming at the character
    pack_weight: float = 0.0  # ...and what they weigh against it together, in fair fights (THREAT_WEIGHTS)

    @property
    def assisting(self) -> bool:
        return self.mode == "assist"

    @property
    def targets(self) -> list[Candidate]:
        """Hostiles the agent may take on: all of them, except in combat assist."""
        return [h for h in self.hostiles if h.allowed]

    def authority(self, behaviour: str) -> str:
        return (self.raw["agent"].get("authority") or {}).get(behaviour, "auto")

    @property
    def is_mage(self) -> bool:
        """Fights with spells from a distance (a mage, a mage-tamer behind its pet, a necromancer)."""
        return self.archetype in RANGED_CASTERS

    @property
    def is_necromancer(self) -> bool:
        return self.archetype == "necromancer"

    @property
    def is_paladin(self) -> bool:
        return self.archetype == "paladin"

    @property
    def blessings(self) -> list[str]:
        """A paladin's blessings it can cast right now (spells.BLESSINGS keys)."""
        return [k for k, name in BLESSING_SPELLS.items() if self.can_cast(name)] if self.is_paladin else []

    @property
    def casts(self) -> bool:
        """Has attack spells to choose from: the mages, and a warrior-mage opening a fight."""
        return self.archetype in CASTERS

    @property
    def is_warrior_mage(self) -> bool:
        return self.archetype == "warrior-mage"

    @property
    def is_archer(self) -> bool:
        return self.archetype == "archer"

    @property
    def is_tamer(self) -> bool:
        return self.archetype in ("tamer", "mage-tamer")

    @property
    def is_bard(self) -> bool:
        return self.archetype == "bard"

    @property
    def pet(self) -> dict[str, Any] | None:
        """The nearest of the character's pets in sight (the snapshot's `pets`), or None."""
        pets = self.raw.get("pets") or []
        return pets[0] if pets else None

    @property
    def pet_pct(self) -> int:
        pet = self.pet
        return 100 if not pet or pet.get("hits_pct") is None else pet["hits_pct"]

    @property
    def ranged(self) -> dict[str, Any]:
        """The bow or crossbow in hand: kind, ammo ("arrows" or "bolts") and range; {} without one."""
        return self.player.get("ranged") or {}

    @property
    def ammo(self) -> int:
        """Arrows or bolts in the pack for the weapon in hand."""
        return self.player.get("supplies", {}).get(self.ranged.get("ammo", "arrows"), 0)

    def can_cast(self, name: str) -> bool:
        """In the book, with the mana and reagents for it right now."""
        spells = (self.raw.get("magic") or {}).get("spells", [])
        return any(s["name"] == name and not s.get("missing") for s in spells)

    @property
    def player(self) -> dict[str, Any]:
        return self.raw["player"]

    @property
    def agent(self) -> dict[str, Any]:
        return self.raw["agent"]

    def hostile(self, cid: str) -> Candidate | None:
        return next((c for c in self.hostiles if c.id == cid), None)

    def hostile_by_serial(self, serial: int) -> Candidate | None:
        return next((c for c in self.hostiles if c.serial == serial), None)

    def corpse(self, cid: str) -> Candidate | None:
        return next((c for c in self.corpses if c.id == cid), None)

    def spell(self, sid: str) -> Candidate | None:
        return next((c for c in self.spells if c.id == sid), None)

    @property
    def area_spells(self) -> list[Candidate]:
        return [c for c in self.spells if c.info.get("area")]

    def signature(self) -> tuple:
        """Changes when something worth re-deciding about happens."""
        return (
            tuple(sorted(c.serial for c in self.hostiles)),
            health_words(self.hp_pct),
            self.player.get("poisoned"),
            tuple(sorted(c.serial for c in self.corpses)),
            tuple(sorted(c.serial for c in self.items)),
            self.agent.get("engaged"),
            # A mage re-decides when mana crosses a band. Its next spell is queued in the
            # client, so the moment a cast becomes possible needs no new decision.
            mana_words(self.mana_pct) if self.casts else None,
            # An archer when its ammunition runs low or out.
            ammo_band(self.ammo) if self.is_archer else None,
            # A tamer when its pet's health changes band, or the pet is lost.
            (bool(self.pet), health_words(self.pet_pct)) if self.is_tamer else None,
            # When something starts coming at the character, or a pack grows past a band: decided
            # at once rather than at the next second's tick, while the creatures are still far off.
            tuple(sorted(h.serial for h in self.pack)),
            "far" if self.pack_weight >= PACK_FAR else "hard" if self.pack_weight >= PACK_HARD else "fair",
        )


AMMO_LOW = 25  # arrows or bolts: "running low" at or below this


def ammo_band(n: int) -> str:
    return "none" if n <= 0 else "low" if n <= AMMO_LOW else "plenty"


def archetype_of(snapshot: dict[str, Any]) -> str:
    """From the skills and what is in hand. A paladin (Chivalry 50, its book and a melee weapon with
    a weapon skill of 50) or a necromancer (Necromancy 50, its book, and no higher Magery or weapon
    skill) first. Then with a spellbook and Magery 50 or more: a mage-tamer
    if Animal Taming is 50 too, a warrior-mage with a melee weapon in hand and a weapon skill of
    50, else a mage if Magery is at least as high as any weapon skill. Then a tamer, a bard, an
    archer (a bow or crossbow in hand) or a warrior."""
    p = snapshot["player"]
    skills = p.get("skills", {})
    magery = skills.get("Magery", 0)
    weapon_skill = max((skills.get(s, 0) for s in MELEE_SKILLS), default=0)
    taming = skills.get("Animal Taming", 0)
    schools = (snapshot.get("magic") or {}).get("schools") or (["magery"] if snapshot.get("magic") else [])
    # AOS-era schools (cuo-cvl.5): a paladin is a warrior with Chivalry and its book; a
    # necromancer casts necromancy from a distance, as a mage does magery.
    if "chivalry" in schools and skills.get("Chivalry", 0) >= 50 and weapon_skill >= 50 and p.get("weapon") \
            and not p.get("ranged"):
        return "paladin"
    if "necromancy" in schools and skills.get("Necromancy", 0) >= 50 and skills.get("Necromancy", 0) >= magery \
            and skills.get("Necromancy", 0) >= weapon_skill:
        return "necromancer"
    if snapshot.get("magic") and magery >= 50:
        if taming >= 50:
            return "mage-tamer"
        if weapon_skill >= 50 and p.get("weapon") and not p.get("ranged"):
            return "warrior-mage"
        if magery >= weapon_skill:
            return "mage"
    if taming >= 50 and taming >= max((skills.get(s, 0) for s in MELEE_SKILLS), default=0):
        return "tamer"
    song = max((skills.get(s, 0) for s in SONGS), default=0)
    if skills.get("Musicianship", 0) >= 50 and song >= 50 and song >= max((skills.get(s, 0) for s in MELEE_SKILLS), default=0):
        return "bard"
    if snapshot["player"].get("ranged"):
        return "archer"
    return "warrior"


def build(snapshot: dict[str, Any], looted: set[int], events: list[str], skip_items: set[int] = frozenset(),
          max_hostiles: int = 6, archetype: str | None = None, casts_at: dict[int, int] | None = None,
          bestiary: dict[int, dict[str, Any]] | None = None) -> Situation:
    """bestiary (World.bestiary): body -> creature stats, to say how strong each hostile is."""
    p = snapshot["player"]
    archetype = archetype or archetype_of(snapshot)
    mage = archetype in CASTERS
    ranged = (p.get("ranged") or {}) if archetype == "archer" else {}
    tamer = archetype in ("tamer", "mage-tamer")
    pet_target = snapshot["agent"].get("pet_target", 0) if tamer else 0
    casts_at = casts_at or {}
    hp_pct = round(100 * p["hits"] / p["hits_max"]) if p.get("hits_max") else 100
    mana_pct = round(100 * p["mana"] / p["mana_max"]) if p.get("mana_max") else 100
    stam_pct = round(100 * p["stam"] / p["stam_max"]) if p.get("stam_max") else 100
    engaged = snapshot["agent"].get("engaged", 0)
    supplies = p.get("supplies", {})
    mode = snapshot["agent"].get("mode", "auto")
    engage = snapshot["agent"].get("engage", "defend")
    # On a long walk only what is close or coming for the character is worth stopping for.
    traveling = (snapshot["agent"].get("travel") or {}).get("state") == "walking"

    hostiles: list[Candidate] = []
    pack: list[Candidate] = []
    others: list[dict[str, Any]] = []
    for m in snapshot.get("mobiles", []):
        if m.get("dead"):
            continue
        if m.get("monster"):
            if len(hostiles) >= max_hostiles:
                continue
            cid = f"t{len(hostiles) + 1}"
            name = m.get("name") or "an unknown creature"
            kind = BODY_KINDS.get(m.get("body", 0))
            if kind and kind not in name.lower():
                name = f"{name} (a {kind})" if not kind[0] in "aeiou" else f"{name} (an {kind})"
            info = {
                "id": cid,
                "name": name,
                "health": health_words(m.get("hits_pct")),
                "distance": distance_words(m["distance"]),
                "direction": m.get("dir", ""),
                "your_current_target": bool(m.get("my_target")) or m["serial"] == engaged,
                "aggressive": bool(m.get("war_mode")),
            }
            known = known_creature(bestiary, m)
            if known:
                info["strength"] = threat_words(known, p.get("hits_max") or 100)
                if known.get("caster"):
                    info["casts_spells"] = "yes: it attacks with spells from a distance"
            allowed = not traveling or m["distance"] <= 3 or (bool(m.get("war_mode")) and m["distance"] <= 6)
            if mode == "assist":
                info["the_players_target"] = bool(m.get("player_target"))
                info["attacking_you"] = bool(m.get("attacking_me"))
                allowed = info["the_players_target"] or engage == "nearby" or \
                    (engage == "defend" and info["attacking_you"])
            if ranged:
                info["in_shooting_range"] = m["distance"] <= ranged.get("range", 10)
            if tamer:
                info["your_pet_is_fighting_it"] = m["serial"] == pet_target
            if mage:
                info["in_spell_range"] = m["distance"] <= SPELL_RANGE
                n = casts_at.get(m["serial"], 0)
                info["your_spells_at_it"] = "none yet" if n == 0 else f"{n} so far"
            hostiles.append(Candidate(cid, m["serial"], info["name"], m["distance"], info, m.get("hits_pct"),
                                      casts_at.get(m["serial"], 0), allowed, known["hits"] if known else 0))
            if coming_at(m):
                pack.append(hostiles[-1])
        elif m.get("pet") and tamer:
            continue  # a tamer's own pets are described under `you`
        elif len(others) < 5:
            # Other players go in by what they are, never by name.
            if m.get("threat"):
                kind = {"red": "a red player (a murderer)", "criminal": "a criminal player"}.get(m["threat"], "another player")
                others.append({"name": kind, "kind": "player", "distance": distance_words(m["distance"])})
                continue
            kind = "your pet" if m.get("pet") else "person" if m.get("human") else "creature"
            others.append({"name": m.get("label") or m.get("name") or "someone", "kind": kind,
                           "distance": distance_words(m["distance"])})

    corpses: list[Candidate] = []
    items: list[Candidate] = []
    for c in snapshot.get("corpses", []):
        # Only corpses of monsters the client saw die: looting anything else can be a crime.
        if c.get("monster") is not True:
            continue
        if c["serial"] not in looted and c["distance"] <= 10:
            cid = f"c{len(corpses) + 1}"
            info = {"id": cid, "name": c.get("name") or "a corpse", "distance": distance_words(c["distance"]),
                    "direction": c.get("dir", ""), "opened": c.get("opened", False)}
            corpses.append(Candidate(cid, c["serial"], info["name"], c["distance"], info))
        # Items in any open corpse within reach, looted or not: the client takes gold and
        # supplies itself, and the rest needs a judgment.
        if c["distance"] <= 2:
            for it in c.get("items", []):
                if it.get("auto_loot") or it["serial"] in skip_items or len(items) >= 6:
                    continue
                iid = f"i{len(items) + 1}"
                iinfo = {"id": iid, "name": it.get("name") or "an item", "amount": it.get("amount", 1),
                         "properties": it.get("props", "")}
                items.append(Candidate(iid, it["serial"], iinfo["name"], c["distance"], iinfo))

    spells: list[Candidate] = []
    magic = snapshot.get("magic") or {}
    area_center, area_count = None, 0
    if mage:
        known = {s["name"]: s for s in magic.get("spells", [])}
        necro = archetype == "necromancer"
        for name, what in (NECRO_ATTACK_SPELLS if necro else ATTACK_SPELLS).items():
            s = known.get(name)
            if s and not s.get("missing"):
                sid = f"s{len(spells) + 1}"
                info = {"id": sid, "name": name, "effect": what, "mana": s["mana"], "circle": s["circle"]}
                spells.append(Candidate(sid, s["id"], name, 0, info))
        # Mages and necromancers only: a warrior-mage casts one opener at a creature that is still coming.
        if archetype in ("mage", "mage-tamer"):
            area_center, area_count = area_target(snapshot, hostiles)
        elif necro:
            area_center, area_count = around_target(snapshot, hostiles, WITHER_RADIUS - 1, WITHER_RADIUS + 1)
        for name, what in (NECRO_AREA_SPELLS if necro else AREA_SPELLS).items() if area_center else ():
            s = known.get(name)
            if s and not s.get("missing"):
                sid = f"s{len(spells) + 1}"
                info = {"id": sid, "name": name, "mana": s["mana"], "circle": s["circle"], "area": True,
                        "effect": f"{what} ({area_count} creatures there now)" if necro else
                        f"{what} (cast at {area_center.name} it hits {area_count} creatures now)"}
                spells.append(Candidate(sid, s["id"], name, 0, info))

    you = {
        "health": f"{health_words(hp_pct)} ({hp_pct}%)",
        "stamina": "tired" if stam_pct < 30 else "fine",
        "poisoned": bool(p.get("poisoned")),
        "bandaging_now": bool(snapshot["agent"].get("bandaging")),
        "bandages_left": supplies.get("bandages", 0),
        "heal_potions_left": supplies.get("heal_potions", 0),
        "heal_potion_ready": potion_words(supplies.get("heal_potions", 0), snapshot["agent"].get("heal_potion_ready_ms", 0)),
        "cure_potions_left": supplies.get("cure_potions", 0),
        "weapon": p.get("weapon") or "bare hands",
        "fighting": next((h.name for h in hostiles if h.info["your_current_target"]), "nobody"),
        "carrying": "nearly overloaded" if p.get("weight_max") and p["weight"] > 0.9 * p["weight_max"] else "light load",
    }
    bandages, potions = supplies.get("bandages", 0), supplies.get("heal_potions", 0)
    if bandages <= 10 and potions <= 1:
        you["supplies"] = f"nearly gone: {bandages} bandages and {potions} heal potions left"
    elif bandages <= 25 or potions == 0:
        you["supplies"] = f"running low: {bandages} bandages and {potions} heal potions left"
    else:
        you["supplies"] = "plenty"
    if ranged:
        # A bow or crossbow shoots nothing without its ammunition, so that comes first.
        what, n = ranged.get("ammo", "arrows"), supplies.get(ranged.get("ammo", "arrows"), 0)
        you["weapon"] = f"{p.get('weapon') or 'a ' + ranged.get('kind', 'bow')} (shoots up to {ranged.get('range', 10)} " \
                        f"tiles; needs {what})"
        you[f"{what}_left"] = n
        if n <= 0:
            you["supplies"] = f"nearly gone: no {what} left, so the {ranged.get('kind', 'bow')} cannot shoot"
        elif n <= AMMO_LOW and not you["supplies"].startswith("nearly gone"):
            you["supplies"] = f"running low: {n} {what}, {bandages} bandages and {potions} heal potions left"
    if archetype == "bard":
        you["weapon"] = "music: provocation, peacemaking and discordance (the bard is weak in a fight itself)"
        you["instrument"] = "in the pack" if supplies.get("instrument") else "none: no songs without one"
    if tamer:
        pets = snapshot.get("pets") or []
        you["weapon"] = "its pet (the tamer is weak in a fight itself)"
        if pets:
            pet = pets[0]
            kind = BODY_KINDS.get(pet.get("body", 0))
            name = pet.get("name") or "the pet"
            if kind and kind not in name.lower():
                name = f"{name} (a {kind})" if kind[0] not in "aeiou" else f"{name} (an {kind})"
            fighting = next((h.name for h in hostiles if h.serial == pet_target), "nothing")
            you["pet"] = {"name": name, "health": f"{health_words(pet.get('hits_pct'))} ({pet.get('hits_pct')}%)",
                          "distance": distance_words(pet["distance"]), "fighting": fighting}
        else:
            you["pet"] = "none in sight: without its pet the tamer cannot fight"
    if archetype == "mage":
        del you["bandages_left"]
        you["supplies"] = "plenty" if potions > 1 else f"{potions} heal potions left"
        you["weapon"] = "spells (weak in melee)"
    elif archetype == "necromancer":
        you["weapon"] = "necromancy spells (weak in melee); it heals with bandages and potions, not spells"
    elif archetype == "paladin":
        you["weapon"] = f"{you['weapon']}, and chivalry: blessings for its fighting, and it heals itself with " \
                        "Close Wounds"
        you["tithing_points"] = (snapshot.get("magic") or {}).get("tithing", 0)
        you["mana"] = f"{mana_words(mana_pct)} ({mana_pct}%)"
        active = [n for n, b in (("Divine Fury", "DivineFury"), ("Enemy of One", "EnemyOfOne"))
                  if b in p.get("buffs", [])]
        you["blessings_active"] = active or ["none"]
    elif archetype == "mage-tamer":
        you["weapon"] = "its pet, and spells (the tamer is weak in a fight itself)"
    elif archetype == "warrior-mage":
        you["weapon"] = f"{you['weapon']}, and spells to open a fight"
    if mage:
        you["mana"] = f"{mana_words(mana_pct)} ({mana_pct}%)"
        you["casting_now"] = magic.get("casting") or "nothing"
        ready_ms = magic.get("cast_ready_ms", 0)
        secs = max(1, round(ready_ms / 1000))
        you["next_spell"] = "can cast now" if ready_ms <= 0 and not magic.get("casting") \
            else f"in about {secs} second{'s' if secs > 1 else ''}"
        you["attack_spells_available"] = [c.name for c in spells] or ["none: not enough mana or reagents"]
        regs = reagents_of(p, archetype)
        low = sorted(k.replace("_", " ") for k, v in regs.items() if v < 5)
        you["reagents"] = "plenty" if not low else "running out of " + ", ".join(low)

    state = {
        "you": you,
        "hostile_creatures": [h.info for h in hostiles],
        "corpses_not_yet_looted": [c.info for c in corpses],
        "items_in_open_corpse": [i.info for i in items],
        "other_beings_nearby": others,
        "recent_events": events[-8:],
    }
    your_hits = p.get("hits_max") or 100
    weight = round(sum(threat_weight(h.info, h.hits_pct, h.max_hits / your_hits if h.max_hits else 1.0)
                       for h in pack), 2)
    if len(pack) >= 2:
        state["coming_at_you"] = pack_words(pack, weight)
    if mode == "assist":
        state["player_drives"] = ("The player is moving the character themselves; you only choose its fighting. "
                                  "It never walks on its own.")
    return Situation(snapshot, state, hostiles, corpses, items, hp_pct,
                     strategy=(snapshot["agent"].get("strategy") or "").strip(),
                     archetype=archetype, spells=spells, mana_pct=mana_pct, mode=mode, engage=engage,
                     traveling=traveling, pack=pack, pack_weight=weight, area_center=area_center,
                     area_count=area_count)


def reagents_of(p: dict[str, Any], archetype: str | None) -> dict[str, int]:
    """The reagent counts that matter to this character: a necromancer's own, or a mage's."""
    supplies = p.get("supplies", {})
    return (supplies.get("pagan_reagents") if archetype == "necromancer" else supplies.get("reagents")) or {}


def around_target(snapshot: dict[str, Any], hostiles: list[Candidate], radius: int, clear: int
                  ) -> tuple[Candidate | None, int]:
    """For a spell that hits everything around the caster (Wither, Holy Light): how many monsters
    are within `radius`, with the nearest one as the cast's target. None unless AREA_MIN or more,
    or when anyone else (a player, a pet, a townsperson) is within `clear` tiles."""
    alive = [m for m in snapshot.get("mobiles", []) if not m.get("dead")]
    if any(not m.get("monster") and m["distance"] <= clear for m in alive):
        return None, 0
    n = sum(1 for m in alive if m.get("monster") and m["distance"] <= radius)
    near = [h for h in hostiles if h.allowed and h.distance <= radius]
    return (min(near, key=lambda h: h.distance), n) if n >= AREA_MIN and near else (None, 0)


def area_target(snapshot: dict[str, Any], hostiles: list[Candidate]) -> tuple[Candidate | None, int]:
    """The creature to aim an area spell at: the one with the most monsters within AREA_RADIUS
    tiles of it (itself included), in spell range, the nearest on a tie. None unless that is
    AREA_MIN or more, or when anyone else (a player, a pet, a townsperson) is within a tile of the
    blast: an area spell hits whatever stands there."""
    pos = {m["serial"]: (m.get("dx", 0), m.get("dy", 0)) for m in snapshot.get("mobiles", []) if not m.get("dead")}
    monsters = [pos[m["serial"]] for m in snapshot.get("mobiles", []) if m.get("monster") and not m.get("dead")]
    others = [pos[m["serial"]] for m in snapshot.get("mobiles", []) if not m.get("monster") and not m.get("dead")]
    best, best_n = None, 0
    for h in hostiles:
        if not h.allowed or h.distance > SPELL_RANGE or h.serial not in pos:
            continue
        x, y = pos[h.serial]
        if any(max(abs(ox - x), abs(oy - y)) <= AREA_RADIUS + 1 for ox, oy in others):
            continue
        n = sum(1 for mx, my in monsters if max(abs(mx - x), abs(my - y)) <= AREA_RADIUS)
        if n > best_n or n == best_n and best is not None and h.distance < best.distance:
            best, best_n = h, n
    return (best, best_n) if best_n >= AREA_MIN else (None, 0)


def journal_events(entries: list[dict[str, Any]]) -> list[str]:
    """System and agent lines only. Other players' speech is left out of the
    model's state: it is untrusted text and a warrior's decisions do not need it."""
    out = []
    for e in entries:
        if e.get("source") in ("system", "agent", "client") or e.get("type") == "system":
            text = e.get("text", "").strip()
            if text:
                out.append(text if e.get("source") != "agent" else f"(you) {text}")
    return out
