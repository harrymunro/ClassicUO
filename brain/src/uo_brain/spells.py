"""The attack spells a mage may choose from, described for Jev.

Only single-target spells: area spells (chain lightning, meteor swarm) can hit
things the mage did not mean to hit. Names match the client's spell table
(SpellsMagery.cs), which spells Flamestrike as one word.
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
