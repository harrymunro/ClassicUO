"""Turn a raw client snapshot into the semantic state Jev reads, plus candidate lists.

Jev reads words better than numbers (it does not do arithmetic or compare
distances reliably), so code does the maths and describes the result:
"badly wounded", "adjacent", "to the north". Candidates get short ids
(t1, c1, i1) that map back to serials, so the model only ever picks from
options code has already checked.
"""

import math
from dataclasses import dataclass, field
from typing import Any

from .spells import (AREA_MIN, AREA_RADIUS, BLESSING_SPELLS, FIELD_SPELLS, SUMMON_SLOTS, WITHER_RADIUS, cast_chance,
                     chance_words, fight_spells, mana_words)

SPELL_RANGE = 10  # tiles; ModernUO's magery range from Mondain's Legacy on
QUAKE_RADIUS = 4  # Earthquake reaches 1 + Magery/15 tiles (AOS) or 1 + Magery/20: offered for this many round
FIELD_MIN = 3     # tiles to the creature before a field goes between: one for the field, one to spare
MANA_SPELLS = ("Feeblemind", "Mana Drain", "Mana Vampire", "Mind Rot")  # only worth it against a spellcaster
MELEE_SKILLS = ("Swordsmanship", "Mace Fighting", "Fencing", "Archery")
SONGS = ("Provocation", "Peacemaking", "Discordance")
CASTERS = ("mage", "mage-tamer", "warrior-mage", "necromancer")  # archetypes that cast attack spells
RANGED_CASTERS = ("mage", "mage-tamer", "necromancer")  # ...and fight with them from a distance
WEAK_IN_MELEE = ("mage", "mage-tamer", "necromancer", "archer", "bard", "tamer")  # can't take many hits
# Places to stand (cuo-fhf): the client's `spots`, as AgentSpots.cs works them out.
SPOT_SIGHT = 12      # AgentSpots.SIGHT_RANGE: a creature further from a spot than this can't reach it with spells
SPOT_DEAD_END = 12   # AgentSpots.DEAD_END: less room beyond a spot than this is a dead end
SPOT_OPEN = 100      # AgentSpots.OPEN_ROOM: this much is open ground
SIGHT_WORDS = ("yes: a wall or something else stands in the way, so the character has to walk round to where it can "
               "see it before a spell, arrow or song reaches it")


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


PARTY_TILES = 10  # companions this close fight beside the character, and count in its odds (cuo-4ar)


def strength_words(creature: dict[str, Any], your_hits: int, party: int = 1) -> str:
    """threat_words for the party beside the character: its hits are theirs together. Where that
    changes the words, both are said: "stronger than you: a hard fight, with your 4 companions beside
    you (far stronger than you alone)". The leave rules read the start, so a lich isn't "do not fight it
    alone" to five who aren't alone."""
    alone = threat_words(creature, your_hits)
    if party <= 1:
        return alone
    together = threat_words(creature, your_hits * party)
    if together == alone:
        return alone
    n = party - 1
    return f"{together}, with your {n} companion{'s' if n > 1 else ''} beside you ({alone.split(':')[0]} alone)"


def known_creature(bestiary: dict[int, dict[str, Any]] | None, m: dict[str, Any]) -> dict[str, Any] | None:
    """The bestiary's entry for a creature in view: the kind that shares its body graphic and its
    name, else the strongest kind with that body. By body alone a black bear read as "far stronger"
    in a soak run: it shares its graphic with a raging grizzly."""
    known = (bestiary or {}).get(m.get("body", 0))
    if not known:
        return None
    name = (m.get("name") or "").strip().lower()
    return next((k for k in known.get("kinds", ()) if (k.get("name") or "").lower() == name), known)


def threatening(m: dict[str, Any], bestiary: dict[int, dict[str, Any]] | None = None) -> bool:
    """A creature in view that could start a fight: a monster, alive, and not a kind the bestiary says only
    fights back (a bird, a deer) unless it is fighting already. A crossbill far off behind a wall had the
    planner rest 'with a hostile in sight' at the healer (cuo-s9p). Unknown kinds count."""
    if not m.get("monster") or m.get("dead"):
        return False
    if m.get("war_mode") or m.get("attacking_me"):
        return True
    known = known_creature(bestiary, m)
    return not (known and known.get("peaceful"))


# What one creature coming at the character weighs against it, in fair fights, by its strength
# words; scaled by its health, since a badly hurt one is nearly done.
THREAT_WEIGHTS = {"far stronger": 4.0, "stronger": 2.0, "a fair fight": 1.0, "weak": 0.4}
UNKNOWN_WEIGHT = 0.5  # no stats in the bestiary
# The group together outweighs the character: too many to fight at once. Six weak arena monsters
# (2.4) are won every time; a troll, a harpy and a ratman (about 2.5), or two gargoyles (3.3),
# killed a lone warrior in soak runs.
PACK_FAR = 2.5
PACK_HARD = 1.6


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


ROOTED_WORDS = "never: it is rooted to the spot and can't follow you; step out of its reach and it is left behind"
PEACEFUL_WORDS = "yes: it only fights back if attacked, and won't start a fight"


def coming_at(m: dict[str, Any]) -> bool:
    """Is this monster coming at the character: within 12 tiles and fighting (war mode), attacking
    it, or already on it."""
    return m["distance"] <= 12 and (bool(m.get("war_mode")) or bool(m.get("attacking_me")) or m["distance"] <= 2)


def pack_words(pack: list["Candidate"], weight: float, party: int = 1) -> str:
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
    if party > 1:
        together += f" for your party of {party}"
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
    # The character can see it, so shoot it or cast at it (cuo-ehm): the client's line of sight, with
    # the server's "Target can not be seen" as a correction. True when the client doesn't say.
    in_sight: bool = True


@dataclass
class Mate:
    """A party companion in view, as the fight sees it (cuo-4ti): how it is, what is on it, and its pets."""
    name: str
    serial: int
    plays_as: str
    hits_pct: int | None
    distance: int
    in_sight: bool = True
    dead: bool = False
    attackers: list[int] = field(default_factory=list)  # creatures attacking it, by its own report
    pets: list[dict[str, Any]] = field(default_factory=list)  # its pets: serial, name, hits_pct, distance from me
    being_helped: bool = False  # another companion called that it is healing this one, a moment ago

    @property
    def weak_in_melee(self) -> bool:
        return self.plays_as in WEAK_IN_MELEE

    def words(self) -> str:
        return f"{self.name} ({self.plays_as}, {'dead' if self.dead else health_words(self.hits_pct)})"


@dataclass
class Situation:
    raw: dict[str, Any]
    state: dict[str, Any]
    hostiles: list[Candidate] = field(default_factory=list)
    corpses: list[Candidate] = field(default_factory=list)
    items: list[Candidate] = field(default_factory=list)
    hp_pct: int = 100
    hp_drop: int = 0  # health (percent) lost over the last few seconds, set by the loop (policy.Memory.health_drop)

    def note_health_drop(self, drop: int) -> None:
        """The fall in health over the last few seconds, for code and, once it's a real fall, in words."""
        self.hp_drop = drop
        if drop >= 20:
            self.state["you"]["health_falling"] = f"down {drop} points in the last few seconds"
    strategy: str = ""
    archetype: str = "warrior"
    spells: list[Candidate] = field(default_factory=list)  # spells for the fight castable right now
    mana_pct: int = 100
    mode: str = "auto"         # "assist" is combat assist: the player drives, the agent only fights
    traveling: bool = False    # on a long walk (travel): no seeking or looting on the way
    party_near: int = 0        # companions within PARTY_TILES, alive (cuo-4ar)
    mates: list[Mate] = field(default_factory=list)  # the companions in view (mates.Mates.before fills it)
    engage: str = "defend"     # combat assist: follow (the player's target), defend (+ attackers), nearby
    known: list[str] = field(default_factory=list)  # world facts picked for this situation (facts.py)
    plan: dict[str, str] = field(default_factory=dict)  # the plan being followed and its step (machine.py)
    area_center: Candidate | None = None  # where an area spell hits most creatures and nobody else (casters)
    area_count: int = 0                   # ...and how many it hits there
    pack: list[Candidate] = field(default_factory=list)  # the creatures coming at the character
    pack_weight: float = 0.0  # ...and what they weigh against it together, in fair fights (THREAT_WEIGHTS)
    # Places it could walk to (cuo-fhf), in the order the "stand" question lists them: by bearing from
    # north, clockwise (or the reverse), "stay" last. Empty unless the snapshot asked for them.
    spots: list[Candidate] = field(default_factory=list)

    def spot(self, sid: str) -> Candidate | None:
        return next((c for c in self.spots if c.id == sid), None)

    @property
    def assisting(self) -> bool:
        return self.mode == "assist"

    @property
    def targets(self) -> list[Candidate]:
        """Hostiles the agent may take on: all of them, except in combat assist."""
        return [h for h in self.hostiles if h.allowed]

    @property
    def threats(self) -> list[Candidate]:
        """Hostiles that could start a fight: not the birds and deer that only fight back (cuo-s9p)."""
        return [h for h in self.hostiles if "peaceful" not in h.info]

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
        """The damage spells that hit several creatures at once, on offer now."""
        return [c for c in self.spells if c.info.get("area")]

    @property
    def focus(self) -> Candidate | None:
        """The creature a caster's next spell is about: its current target, else the nearest."""
        return focus_of(self.targets)

    def signature(self, defend_only: bool = False) -> tuple:
        """Changes when something worth re-deciding about happens. Defending only (beside travel,
        rest and errands: no seeking, no looting), a creature counts once it is within WATCH_TILES or
        in war mode, and corpses don't count: walking through town, every cat and dog that came into
        view and every corpse was a call (cuo-df8)."""
        watched = [c for c in self.hostiles if not defend_only or c.distance <= WATCH_TILES or c.info.get("aggressive")]
        return (
            tuple(sorted(c.serial for c in watched)),
            health_words(self.hp_pct),
            self.player.get("poisoned"),
            tuple(sorted(c.serial for c in self.corpses)) if not defend_only else (),
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
            # A creature going out of sight, or coming into it (cuo-ehm).
            tuple(sorted(c.serial for c in self.hostiles if not c.in_sight)),
        )


AMMO_LOW = 25  # arrows or bolts: "running low" at or below this
# Within this many tiles ("nearby") a creature ends the loop's quiet, and, defending only, counts for
# the signature even out of war mode (cuo-df8).
WATCH_TILES = 7


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
          bestiary: dict[int, dict[str, Any]] | None = None, cooling: set[tuple[int, str]] | None = None,
          spot_order: str = "bearing", no_path: set[int] = frozenset(),
          companions: dict[str, str] | None = None, movement: dict[int, str] | None = None) -> Situation:
    """bestiary (World.bestiary): body -> creature stats, to say how strong each hostile is.
    cooling (Memory.cooling): (creature or 0, spell) pairs cast too recently to offer again.
    no_path (Memory.no_path): creatures the walk to seek found no path to, left out as the client's
    unreachable ones are.
    movement (Memory.movement, while a leave Jev may end goes on): creature -> how it has moved against
    the character lately ("falling behind: 9 to 14 tiles in the last 4 s"), as each one's `moving`.
    companions: a party's other characters, name -> what each plays as (cuo-4ar). They are named as
    companions, not "another player", and those within PARTY_TILES count in how strong a creature is
    and how much what is coming weighs: five against a lich isn't one.
    spot_order: how the snapshot's places to stand are listed (spot_candidates)."""
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
    companions = companions or {}
    party_info: list[dict[str, Any]] = []
    party = 1 + sum(1 for m in snapshot.get("mobiles", []) if m.get("name") in companions and not m.get("monster")
                    and not m.get("dead") and m["distance"] <= PARTY_TILES)
    your_hits = p.get("hits_max") or 100
    for m in snapshot.get("mobiles", []):
        if m.get("name") in companions and not m.get("monster"):
            party_info.append({"name": m["name"], "plays_as": companions[m["name"]],
                               "health": "dead (a ghost)" if m.get("dead") else health_words(m.get("hits_pct")),
                               "distance": distance_words(m["distance"])})
            continue
        if m.get("dead"):
            continue
        if m.get("monster"):
            # The client gave up on it as out of reach a moment ago (a rat in the sewer below): not a
            # target until it moves or comes at the character (cuo-2ct).
            if (m.get("unreachable") or m["serial"] in no_path) and not m.get("attacking_me"):
                continue
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
            rooted = bool(known and known.get("rooted"))
            if known:
                info["strength"] = strength_words(known, your_hits, party)
                if known.get("caster"):
                    info["casts_spells"] = "yes: it attacks with spells from a distance"
                if rooted:
                    info["moves"] = ROOTED_WORDS
            if not threatening(m, bestiary):
                info["peaceful"] = PEACEFUL_WORDS
            # On a trip, what can't move is walked past, not fought: every walk to Cove met a reaper by
            # the road east of Britain, and the defend loop stood fighting it, once down to 5% (cuo-evh).
            allowed = not traveling or not rooted and (m["distance"] <= 3 or (bool(m.get("war_mode")) and m["distance"] <= 6))
            if mode == "assist":
                info["the_players_target"] = bool(m.get("player_target"))
                info["attacking_you"] = bool(m.get("attacking_me"))
                allowed = info["the_players_target"] or engage == "nearby" or \
                    (engage == "defend" and info["attacking_you"])
            if movement and m["serial"] in movement:
                info["moving"] = movement[m["serial"]]
            if ranged:
                info["in_shooting_range"] = m["distance"] <= ranged.get("range", 10)
            if tamer:
                info["your_pet_is_fighting_it"] = m["serial"] == pet_target
            if mage:
                info["in_spell_range"] = m["distance"] <= SPELL_RANGE
                n = casts_at.get(m["serial"], 0)
                info["your_spells_at_it"] = "none yet" if n == 0 else f"{n} so far"
            seen = m.get("in_sight") is not False
            if not seen:
                info["out_of_sight"] = SIGHT_WORDS
            hostiles.append(Candidate(cid, m["serial"], info["name"], m["distance"], info, m.get("hits_pct"),
                                      casts_at.get(m["serial"], 0), allowed, known["hits"] if known else 0, seen))
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
            kind = "your pet" if m.get("pet") else "your summon" if m.get("summon") else "person" if m.get("human") \
                else "creature"
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
        necro = archetype == "necromancer"
        # Mages and necromancers only: a warrior-mage casts one opener at a creature that is still coming.
        quake: tuple[Candidate | None, int] = (None, 0)
        if archetype in ("mage", "mage-tamer"):
            area_center, area_count = area_target(snapshot, hostiles)
            quake = around_target(snapshot, hostiles, QUAKE_RADIUS, QUAKE_RADIUS + 2)
        elif necro:
            area_center, area_count = around_target(snapshot, hostiles, WITHER_RADIUS - 1, WITHER_RADIUS + 1)
        spells = spell_candidates(snapshot, archetype, hostiles, area_center, area_count, quake, cooling or set())

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
        you["spells_available"] = [c.name for c in spells] or ["none: not enough mana or reagents"]
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
    # What is coming weighs against the party beside the character, not the character alone.
    weight = round(sum(threat_weight(h.info, h.hits_pct, h.max_hits / (your_hits * party) if h.max_hits else 1.0)
                       for h in pack) / party, 2)
    if len(pack) >= 2:
        state["coming_at_you"] = pack_words(pack, weight, party)
    if party_info:
        state["your_party"] = party_info
    if mode == "assist":
        state["player_drives"] = ("The player is moving the character themselves; you only choose its fighting. "
                                  "It never walks on its own.")
    focus = focus_of([h for h in hostiles if h.allowed])
    spots = spot_candidates(snapshot, hostiles, focus, archetype in RANGED_CASTERS or archetype == "archer", spot_order)
    return Situation(snapshot, state, hostiles, corpses, items, hp_pct,
                     strategy=(snapshot["agent"].get("strategy") or "").strip(),
                     archetype=archetype, spells=spells, mana_pct=mana_pct, mode=mode, engage=engage,
                     traveling=traveling, party_near=party - 1, pack=pack, pack_weight=weight, area_center=area_center,
                     area_count=area_count, spots=spots)


def bearing16(dx: int, dy: int) -> int:
    """Which of 16 bearings (dx, dy) points along, 0 north, clockwise; north is -y (AgentSpots.Bearing16)."""
    return round(((math.degrees(math.atan2(dx, -dy)) + 360) % 360) / 22.5) % 16


def short_name(m: dict[str, Any]) -> str:
    """A creature in a spot's words: its name when that says what it is ("a bone knight"), else what
    its body is ("an orc" for "Vorgak"), else its name."""
    name = (m.get("name") or "").strip()
    if name.lower().startswith(("a ", "an ")):
        return name
    kind = BODY_KINDS.get(m.get("body", 0))
    if kind:
        return f"{'an' if kind[0] in 'aeiou' else 'a'} {kind}"
    return name or "a creature"


def spot_words(spot: dict[str, Any], names: dict[int, str], casters: set[int], focus: Candidate | None = None,
               ranged: bool = False) -> str:
    """A place to stand in words (cuo-fhf.2), from the client's facts: "8 steps west-southwest:
    nearest hostile an orc 8 tiles away, none within 3; out of sight of the gargoyle; open ground
    beyond". names: serial -> short name of every hostile; casters: the spellcasters among them.
    For a character that fights from a distance, its target's distance and sight from there too."""
    steps, where = spot.get("steps", 0), spot.get("dir", "")
    kind, name = spot.get("kind", "ring"), spot.get("name", "")
    walk = f"{steps} step{'s' if steps != 1 else ''} {where}"
    head = {"stay": "stay here", "doorway": f"the doorway {walk}", "corridor": f"the mouth of a narrow way {walk}",
            "pet": f"beside your pet {name} {walk}", "npc": f"beside {name} {walk}"}.get(kind, walk)
    parts = []
    near = [(s, d) for s, d in spot.get("near", [])]
    if near:
        serial, d = near[0]
        far = "adjacent" if d <= 1 else f"{d} tiles away"
        within = sum(1 for _, dd in near if dd <= 3)
        parts.append(f"nearest hostile {names.get(serial, 'a creature')} {far}, "
                     + ("none within 3" if within == 0 else f"{within} within 3"))
    else:
        parts.append("no hostile in view")
    if ranged and focus is not None:
        d = next((dd for s, dd in near if s == focus.serial), None)
        if d is not None:
            seen = focus.serial in spot.get("seen_by", [])
            parts.append(f"{names.get(focus.serial, focus.name)} (its target) "
                         + ("adjacent" if d <= 1 else f"{d} tiles away and " + ("in sight" if seen else "out of sight")))
    reach = [s for s, d in near if s in casters and d <= SPOT_SIGHT]
    if reach:
        seeing = [s for s in reach if s in spot.get("seen_by", [])]
        if seeing:
            parts.append("in sight of " + ", ".join(names.get(s, "a spellcaster") for s in seeing[:3])
                         + (" (spellcasters)" if len(seeing) > 1 else " (a spellcaster)"))
        else:
            parts.append("out of sight of " + ("the spellcaster" if len(reach) == 1 else f"all {len(reach)} spellcasters"))
    passes = spot.get("passes", [])
    if passes:
        parts.append("the way there passes next to " + ", ".join(names.get(s, "a creature") for s in passes[:2]))
    room = spot.get("room", SPOT_OPEN)
    parts.append("a dead end" if room < SPOT_DEAD_END else "closed in, little room beyond" if room < SPOT_OPEN
                 else "open ground beyond")
    return f"{head}: " + "; ".join(parts)


def spot_candidates(snapshot: dict[str, Any], hostiles: list[Candidate], focus: Candidate | None, ranged: bool,
                    order: str = "bearing") -> list[Candidate]:
    """The snapshot's spots as options (ids p1, p2, ...; "stay" for staying put), listed by bearing
    from north, clockwise, nearest first on each bearing ("reversed": the other way round), with
    "stay" last: Jev leans towards the first option, so the order is fixed by geometry alone."""
    raw = snapshot.get("spots") or []
    if not raw:
        return []
    names = {m["serial"]: short_name(m) for m in snapshot.get("mobiles", []) if m.get("monster")}
    casters = {h.serial for h in hostiles if h.info.get("casts_spells")}
    moves = sorted((s for s in raw if s.get("kind") != "stay"),
                   key=lambda s: (bearing16(s.get("dx", 0), s.get("dy", 0)), s.get("steps", 0)))
    if order == "reversed":
        moves.reverse()
    out = []
    for i, s in enumerate(moves + [s for s in raw if s.get("kind") == "stay"][:1]):
        sid = "stay" if s.get("kind") == "stay" else f"p{i + 1}"
        info = {k: s.get(k) for k in ("kind", "ring", "x", "y", "dx", "dy", "steps", "room", "near", "seen_by", "passes")}
        info["words"] = spot_words(s, names, casters, focus, ranged)
        near = s.get("near") or []
        info["nearest"] = near[0][1] if near else 99
        info["within3"] = sum(1 for _, d in near if d <= 3)
        info["casters_seeing"] = len(set(s.get("seen_by") or []) & casters)
        out.append(Candidate(sid, 0, info["words"].split(":", 1)[0], s.get("steps", 0), info))
    return out


def focus_of(targets: list[Candidate]) -> Candidate | None:
    """The current target, else the nearest in sight, else the nearest."""
    return next((h for h in targets if h.info["your_current_target"]), None) \
        or (min(targets, key=lambda h: (not h.in_sight, h.distance)) if targets else None)


def spell_candidates(snapshot: dict[str, Any], archetype: str, hostiles: list[Candidate], area_center: Candidate | None,
                     area_count: int, quake: tuple[Candidate | None, int], cooling: set[tuple[int, str]]
                     ) -> list[Candidate]:
    """Every spell in the book a fight can use right now (cuo-ryt), attack spells first. Each is
    offered only where it can do something: an area spell with a crowd, a curse once on a creature
    while it lasts (one on mana only against a spellcaster), a field with room between the caster
    and the creature, a summon with follower slots free, a blessing on the caster while it is down."""
    p = snapshot["player"]
    known = {s["name"]: s for s in (snapshot.get("magic") or {}).get("spells", [])}
    targets = [h for h in hostiles if h.allowed]
    focus = focus_of(targets)
    casters = any(h.info.get("casts_spells") for h in targets)
    free = (p.get("followers_max") or 5) - (p.get("followers") or 0)
    buffs = set(p.get("buffs", []))
    magery = (p.get("skills") or {}).get("Magery", 100.0)
    necro = archetype == "necromancer"
    out: list[Candidate] = []
    for name, aim, what in fight_spells(necro):
        s = known.get(name)
        if not s or s.get("missing"):
            continue
        info = {"name": name, "effect": what, "mana": s["mana"], "circle": s["circle"], "aim": aim}
        if name in SUMMON_SLOTS and SUMMON_SLOTS[name] > free:
            continue
        if aim == "area":
            if not area_center or (area_center.serial, name) in cooling:
                continue
            if name != "Mass Curse":
                info["area"] = True  # damage: what an area spell is for (policy)
            info["hits"] = area_count
            info["effect"] = f"{what} (cast at {area_center.name} it hits {area_count} creatures now)"
        elif aim == "around":
            center, n = (area_center, area_count) if necro else quake
            if not center:
                continue
            info["area"], info["hits"] = True, n
            info["effect"] = f"{what} ({n} creatures {'there' if necro else 'around the mage'} now)"
        elif aim == "creature":
            if name in MANA_SPELLS and not casters:
                continue
            if focus and (focus.serial, name) in cooling:
                continue
        elif aim == "ground":
            # Not a field for a warrior-mage: it would wall itself off from the creature it goes to fight.
            wall = name in FIELD_SPELLS
            reach = (FIELD_MIN if wall else 1, SPELL_RANGE)
            if not focus or not focus.in_sight or not reach[0] <= focus.distance <= reach[1] or (0, name) in cooling \
                    or wall and archetype == "warrior-mage":
                continue
        elif aim == "summon":
            if not targets or (0, name) in cooling:
                continue
        elif aim == "self":
            if name.replace(" ", "") in buffs or (0, name) in cooling:
                continue
        if s.get("circle"):  # magery: a high circle fizzles often at middling skill
            info["chance"] = cast_chance(s["circle"], magery, snapshot.get("era", "aos"))
            info["effect"] += chance_words(info["chance"])
        sid = info["id"] = f"s{len(out) + 1}"
        out.append(Candidate(sid, s["id"], name, 0, {"id": sid, **info}))
    return out


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
    n = sum(1 for m in alive if m.get("monster") and m["distance"] <= radius and m.get("in_sight") is not False)
    near = [h for h in hostiles if h.allowed and h.in_sight and h.distance <= radius]
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
        if not h.allowed or not h.in_sight or h.distance > SPELL_RANGE or h.serial not in pos:
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
