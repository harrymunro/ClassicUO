"""The spells a caster may choose from in a fight, described for Jev.

Single-target spells always; area spells (Chain Lightning, Meteor Swarm) only when three or
more creatures stand within 2 tiles of one of them and nobody else is near (state.py), since
they hit whatever is there. Names match the client's spell table (SpellsMagery.cs), which
spells Flamestrike as one word.
"""

# name -> what it does, in words. Ordered strongest first: code's fallback when
# Jev has no confident pick is the first one that can be cast.
ATTACK_SPELLS: dict[str, str] = {
    "Flamestrike": "very heavy fire damage; slow to cast and costs a lot of mana",
    "Energy Bolt": "heavy energy damage; fairly slow to cast",
    "Explosion": "heavy fire damage that lands a moment after it is cast; a classic opening spell",
    "Lightning": "good energy damage; quick to cast",
    "Mind Blast": "damage that depends on the mage's intelligence",
    "Fireball": "moderate fire damage",
    "Harm": "light cold damage, stronger when the target is close; quick to cast",
    "Magic Arrow": "weak fire damage; very quick and cheap",
    "Poison": "poisons the target so it takes damage over time; useless if it is already poisoned",
    "Paralyze": "freezes the target in place for a few seconds without damaging it",
}

# Every creature within 2 tiles of the target, the caster spared (ModernUO). Under AOS rules the
# damage is shared once more than two are hit, but twice over: three creatures each take about
# two thirds of a single hit, for 40 mana (cuo-cvl.4).
AREA_SPELLS: dict[str, str] = {
    "Chain Lightning": "energy damage to every creature within 2 tiles of the target, shared among them; "
                       "the best use of mana against three or more close together",
    "Meteor Swarm": "fire damage to every creature within 2 tiles of the target, shared among them",
}
AREA_RADIUS = 2
AREA_MIN = 3  # creatures within the radius before an area spell is offered

# The rest of the book a fight can use (cuo-ryt), by how code aims each:
#   "creature": at the creature fought (curses); "area": where it hits the most creatures, as the
#   area spells; "around": no cursor, it hits everything around the caster (cast "at" a creature
#   only because the client wants a monster for a harmful spell); "self": at the caster;
#   "summon": no cursor, the creature appears beside the caster; "ground": on the tile beside the
#   creature, between it and the caster (the client finds the tile).
# Left out: healing and curing (the client's reflexes), Protection (code raises it), travel and
# escape (Recall, Gate Travel, Teleport, Invisibility), Dispel and Mass Dispel (nothing says which
# creatures are summoned), and the spells that do nothing in a fight.
CURSES: dict[str, str] = {
    "Curse": "lowers the creature's strength, dexterity and intelligence together for a while, and its "
             "resistances under AOS rules",
    "Weaken": "lowers the creature's strength, and so its health, for a while; weak",
    "Clumsy": "lowers the creature's dexterity for a while, so it hits less surely; weak",
    "Feeblemind": "lowers the creature's intelligence, and so its mana, for a while; only worth it against a spellcaster",
    "Mana Drain": "drains the creature's mana for a while; only worth it against a spellcaster",
    "Mana Vampire": "takes the creature's mana for the mage; only worth it against a spellcaster",
}
AREA_CURSES: dict[str, str] = {
    "Mass Curse": "Curse on every creature within 2 tiles of the target",
}
AROUND_SPELLS: dict[str, str] = {
    "Earthquake": "shakes the ground: every creature near the mage loses a large share of its health, however "
                  "strong it is; costs a lot of mana",
}
SELF_SPELLS: dict[str, str] = {
    "Bless": "raises the mage's strength, dexterity and intelligence for a minute or two",
    "Strength": "raises the mage's strength, and so its health, for a minute or two",
    "Agility": "raises the mage's dexterity for a minute or two",
    "Cunning": "raises the mage's intelligence, and so its mana, for a minute or two",
    "Reactive Armor": "under AOS rules raises physical resistance and lowers the others until cast again; before "
                      "AOS, soaks up the next blows and hurts whoever lands them",
    "Magic Reflection": "under AOS rules raises the other resistances and lowers physical until cast again; before "
                        "AOS, turns the next spell cast at the mage back on its caster",
}
FIELD_SPELLS: dict[str, str] = {
    "Wall of Stone": "a short stone wall across the way between the mage and the creature, for some seconds: "
                     "a creature on foot must go round it while the mage casts",
    "Energy Field": "a wall of energy across the way between the mage and the creature that nothing can walk "
                    "through, for some seconds",
    "Paralyze Field": "a line across the way between the mage and the creature that freezes whatever walks into it "
                      "for a few seconds",
    "Fire Field": "a line of fire across the way between the mage and the creature that burns whatever walks through "
                  "or stands in it",
    "Poison Field": "a line of poison across the way between the mage and the creature that poisons whatever walks "
                    "through it",
}
# Placed beside the creature: they fight whatever is nearest where they stand, for a minute or so.
PLACED_SUMMONS: dict[str, str] = {
    "Energy Vortex": "summons a strong energy vortex beside the creature; it attacks whatever is nearest, which "
                     "can be the mage if it comes close",
    "Blade Spirits": "summons whirling blades beside the creature; they attack whatever is nearest, which can be the "
                     "mage if it comes close",
}
SUMMONS: dict[str, str] = {
    "Earth Elemental": "summons an earth elemental beside the mage that fights for it for a few minutes; tough, "
                       "good at holding creatures off",
    "Water Elemental": "summons a water elemental beside the mage that fights for it for a few minutes",
    "Fire Elemental": "summons a fire elemental beside the mage that fights for it for a few minutes; hits hard",
    "Air Elemental": "summons an air elemental beside the mage that fights for it for a few minutes",
    "Summon Daemon": "summons a daemon beside the mage that fights for it for a few minutes; strong, but costs karma",
    "Summon Creature": "summons an animal beside the mage that fights for it for a while; weak",
}
# Follower slots each summon takes (ModernUO, AOS rules; fewer before AOS).
SUMMON_SLOTS = {"Energy Vortex": 2, "Blade Spirits": 2, "Earth Elemental": 2, "Water Elemental": 3, "Fire Elemental": 4,
                "Air Elemental": 2, "Summon Daemon": 4, "Summon Creature": 2, "Vengeful Spirit": 3}

# Seconds before a spell is offered again for the same creature (curses) or at all (the rest):
# what it does lasts about that long, and a cast nothing confirms isn't repeated at once.
RECAST_AFTER = {"creature": 60.0, "area": 60.0, "self": 90.0, "ground": 12.0, "summon": 10.0}
# A buff icon says whether a self spell is up, where the server sends them (AOS): then a cast that
# failed is offered again soon.
RECAST_AFTER_WITH_ICON = 10.0

MEDITATION = "Meditation"
PROTECTION = "Protection"  # stops damage from interrupting the mage's spells (AOS)
GREATER_HEAL = "Greater Heal"


def find(text: str) -> str | None:
    """The attack spell a piece of text names ("flame strike", "an explosion spell"), if any."""
    flat = text.lower().replace(" ", "")
    return next((name for name in ATTACK_SPELLS if name.lower().replace(" ", "") in flat), None)


# ModernUO's chance to cast a magery spell from a book: none at the circle's low skill, certain 40
# points above it. Mondain's Legacy rules and later (taken for any AOS-era shard), else the older table.
MAGERY_LOW_ML = (-18, -4, 10, 24, 38, 52, 66, 80)
MAGERY_LOW_OLD = (0, 10, 20, 30, 40, 50, 60, 70)


def cast_chance(circle: int, magery: float, era: str = "aos") -> float:
    low = (MAGERY_LOW_ML if era == "aos" else MAGERY_LOW_OLD)[min(max(circle, 1), 8) - 1]
    return min(1.0, max(0.0, (magery - low) / 40))


def chance_words(chance: float) -> str:
    """How often a cast works, for a spell that fizzles often enough to matter; "" otherwise."""
    if chance >= 0.95:
        return ""
    pct = round(chance * 20) * 5
    return (f"; at this Magery about {pct}% of casts work" if pct else "; at this Magery almost every cast fizzles") \
        + ", and a fizzle loses the reagents and the time, not the mana"


def mana_words(pct: int) -> str:
    if pct >= 90:
        return "full"
    if pct >= 60:
        return "plenty"
    if pct >= 35:
        return "half"
    if pct >= 15:
        return "low"
    return "nearly empty"


# Necromancy (AOS-era shards, cuo-cvl.5): a necromancer's attack spells, strongest first, and its
# area spell, which hits every creature around the necromancer itself. Damage scales with Spirit
# Speak. Reagents are the necromancer's own (bat wing, grave dust, daemon blood, nox crystal, pig iron).
NECRO_ATTACK_SPELLS: dict[str, str] = {
    "Poison Strike": "heavy poison damage to the target, and some to every creature next to it",
    "Strangle": "poison damage over several seconds that grows as the target tires; slow to cast and costly",
    "Pain Spike": "quick, cheap direct damage that armour doesn't stop",
}
NECRO_CURSES: dict[str, str] = {
    "Evil Omen": "the next harm done to the creature is worse: the next blow or spell hurts more, a poison is stronger",
    "Corpse Skin": "the creature takes more fire and poison damage for a while, and less physical and cold",
    "Blood Oath": "for a while, the creature takes back the damage it does to the necromancer",
    "Mind Rot": "the creature's spells cost more mana for a while; only worth it against a spellcaster",
    "Vengeful Spirit": "summons a revenant that hunts the creature until it dies; takes three follower slots",
}
NECRO_AREA_SPELLS: dict[str, str] = {
    "Wither": "cold damage to every creature within 4 tiles of the necromancer, the most to those closest",
}
WITHER_RADIUS = 4

# Chivalry: a paladin's blessings for a fight, paid in mana and tithing points. Close Wounds and
# Cleanse by Fire are the client's healing reflexes, like bandages.
BLESSINGS: dict[str, str] = {
    "consecrate": "Consecrate Weapon: for a few seconds the weapon hits each creature where it resists least. "
                  "Cheap; worth renewing in any real fight.",
    "divine_fury": "Divine Fury: faster, harder and surer swings and stamina back for a while, at some cost to "
                   "defence. For a hard fight or several creatures at once.",
    "enemy_of_one": "Enemy of One: much more damage against one kind of creature for a few minutes, but more "
                    "damage taken from every other kind. For a strong creature, or several of the same kind.",
    "holy_light": "Holy Light: holy damage to every creature within 3 tiles of the paladin. For three or more "
                  "on it at once.",
    "none": "No blessing right now: keep fighting.",
}
BLESSING_SPELLS = {"consecrate": "Consecrate Weapon", "divine_fury": "Divine Fury", "enemy_of_one": "Enemy of One",
                   "holy_light": "Holy Light"}


def fight_spells(necro: bool) -> list[tuple[str, str, str]]:
    """(name, how it is aimed, what it does) for every spell a fight can use, attack spells first,
    strongest first (code's fallback is the first damage spell that can be cast)."""
    if necro:
        # Not Curse Weapon: the necromancer fights from a distance, and it only works on weapon hits.
        groups = [(NECRO_ATTACK_SPELLS, "creature"), (NECRO_AREA_SPELLS, "around"), (NECRO_CURSES, "creature")]
    else:
        groups = [(ATTACK_SPELLS, "creature"), (AREA_SPELLS, "area"), (AROUND_SPELLS, "around"), (CURSES, "creature"),
                  (AREA_CURSES, "area"), (FIELD_SPELLS, "ground"), (PLACED_SUMMONS, "ground"), (SUMMONS, "summon"),
                  (SELF_SPELLS, "self")]
    return [(name, aim, what) for group, aim in groups for name, what in group.items()]


def is_damage(name: str) -> bool:
    """A spell that hurts the creature it is cast at, for code's fallback."""
    return name in ATTACK_SPELLS and name not in ("Poison", "Paralyze") or name in NECRO_ATTACK_SPELLS
