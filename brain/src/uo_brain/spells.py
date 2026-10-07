"""The attack spells a mage may choose from, described for Jev.

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

MEDITATION = "Meditation"
PROTECTION = "Protection"  # stops damage from interrupting the mage's spells (AOS)
GREATER_HEAL = "Greater Heal"


def find(text: str) -> str | None:
    """The attack spell a piece of text names ("flame strike", "an explosion spell"), if any."""
    flat = text.lower().replace(" ", "")
    return next((name for name in ATTACK_SPELLS if name.lower().replace(" ", "") in flat), None)


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
