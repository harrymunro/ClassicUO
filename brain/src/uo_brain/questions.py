"""The judgments Jev makes each decision, asked together in one request.

One request fans out to every question that could matter (TypeSafe's
"speculative fan-out"): the intent, plus which target, which corpse and which
items, so the code can act on whichever branch wins without a second round trip.
Questions only see the state; each one is complete on its own.
"""

from typing import Any

from .state import Situation

CLOSE_TILES = 3  # matches policy.PolicyConfig.close_tiles

ROLE = (
    "You are deciding for a warrior in the game Ultima Online. The warrior fights monsters "
    "in melee with a weapon, heals with bandages, and loots what monsters leave behind. "
    "Bandages and potions are applied automatically when health is low, so you do not need "
    "to choose healing; choose what the warrior does next."
)

MAGE_ROLE = (
    "You are deciding for a mage in the game Ultima Online. The mage fights monsters by casting "
    "attack spells from a distance; it is weak in melee and has little health. Every spell costs mana, "
    "which comes back slowly, faster while resting. Healing and curing spells and potions are used "
    "automatically when health is low or the mage is poisoned, so you do not need to choose healing; "
    "choose what the mage does next."
)

INTENTS: dict[str, Any] = {
    "fight": {
        "what": "Attack, or keep attacking, one of the hostile creatures.",
        "when": "A hostile creature is close or adjacent and the warrior can win or is already engaged.",
    },
    "flee": {
        "what": "Run away from the hostile creatures.",
        "when": "Staying would probably get the warrior killed: health is near death or falling fast while "
                "several creatures are attacking at once, so bandages and potions cannot heal fast enough "
                "(supplies in the pack do not help if they cannot be used in time).",
        "not_for": "Ordinary fights the warrior is winning, or being lightly or moderately wounded.",
    },
    "leave": {
        "what": "Get away from this place altogether: run until the hostile creatures are out of sight, and stay away "
                "for now.",
        "when": "A creature far stronger than the warrior is here or coming, or bandages and potions are nearly gone "
                "while several creatures are still fighting, so the fight cannot be won.",
        "not_for": "Fights the warrior is winning, or a single weak creature that is nearly dead.",
    },
    "loot": {
        "what": "Go to a corpse that has not been looted yet and take what is in it.",
        "when": "No hostile creature is close and there is an unlooted corpse nearby.",
        "not_for": "While a hostile creature is adjacent or close.",
    },
    "seek": {
        "what": "Walk towards a hostile creature that is visible but not yet close, to start the next fight.",
        "when": "Hostile creatures are nearby or far away, none is close, and the warrior is in good health.",
    },
    "rest": {
        "what": "Stay put and wait.",
        "when": "Nothing needs doing, or the warrior should recover health before the next fight.",
    },
}


MAGE_INTENTS: dict[str, Any] = {
    "fight": {
        "what": "Attack one of the hostile creatures with spells, or keep attacking it.",
        "when": "A hostile creature is within spell range and there is mana for an attack spell, "
                "or a creature is already attacking the mage.",
    },
    "flee": {
        "what": "Run away from the hostile creatures.",
        "when": "Staying would probably get the mage killed: health is near death or falling fast while creatures "
                "are attacking it, and heals cannot keep up (supplies do not help if they cannot be used in time).",
        "not_for": "Ordinary fights the mage is winning, or being lightly or moderately wounded.",
    },
    "leave": {
        "what": "Get away from this place altogether: run until the hostile creatures are out of sight, and stay away "
                "for now.",
        "when": "A creature far stronger than the mage is here or coming, or mana, reagents and potions are nearly gone "
                "while several creatures are still attacking, so the fight cannot be won.",
        "not_for": "Fights the mage is winning, or a single weak creature that is nearly dead.",
    },
    "loot": INTENTS["loot"],
    "seek": {
        "what": "Walk towards a hostile creature that is out of spell range, to start the next fight.",
        "when": "Hostile creatures are around but none is within spell range, and the mage has health and mana to fight.",
    },
    "rest": {
        "what": "Stay put and meditate to regain mana, or wait.",
        "when": "Nothing needs doing, or mana is low and no creature is close enough to attack the mage.",
    },
}


def role(sit: Situation) -> str:
    return MAGE_ROLE if sit.is_mage else ROLE


def character(sit: Situation) -> str:
    return "mage" if sit.is_mage else "warrior"


def instructions(sit: Situation, question: str, **extra: str) -> dict[str, Any]:
    """Role, the player's own strategy when they wrote one, then the question."""
    out: dict[str, Any] = {"role": role(sit)}
    if sit.strategy:
        out["player_strategy"] = sit.strategy
        out["using_the_strategy"] = (f"These are the player's own instructions for how their {character(sit)} should "
                                     "play. Follow them wherever they bear on this question.")
    out["question"] = question
    out.update(extra)
    return out


def build(sit: Situation) -> dict[str, dict[str, Any]]:
    who = character(sit)
    qs: dict[str, dict[str, Any]] = {
        "intent": {
            "type": "choice",
            "instructions": instructions(sit, f"What should the {who} do next?"),
            "criteria": MAGE_INTENTS if sit.is_mage else INTENTS,
        },
        # A factual risk judgment, so it does not see the strategy; code combines the two.
        "in_danger": {
            "type": "noul",
            "instructions": {
                "role": role(sit),
                "question": f"Is the {who} at serious risk of dying in the next few seconds if they keep fighting?",
            },
            "criteria": {
                "true": "Health is near death or badly wounded while several hostile creatures are adjacent, "
                        "or health is low and no heal potion can be drunk right now, or a creature far stronger "
                        "than the character is close, or supplies are nearly gone with several creatures attacking.",
                "false": "Health is fine or the warrior is clearly winning, or nothing is attacking.",
            },
        },
    }

    if sit.targets:
        criteria = {h.id: describe_hostile(h.info, who) for h in sit.targets}
        criteria["none"] = "None of these creatures should be attacked."
        qs["target"] = {
            "type": "choice",
            "instructions": instructions(
                sit,
                f"If the {who} fights, which hostile creature in `hostile_creatures` should they attack?",
                guidance="Unless the player's strategy says otherwise: prefer the creature already being fought "
                         "unless another is much more dangerous or much closer; prefer close, weakened creatures "
                         "over distant ones.",
            ),
            "criteria": criteria,
        }

    # Which spell, asked alongside the target so a cast needs no second round trip. The
    # question says where the fight stands, so "open with ..." strategies have a hook.
    if sit.is_mage and sit.targets and sit.spells:
        focus = next((h for h in sit.targets if h.info["your_current_target"]), None) \
            or min(sit.targets, key=lambda h: h.distance)
        if focus.casts == 0:
            question = (f"The mage is about to open the fight against {focus.name} ({focus.info['health']}): no spell "
                        "has been cast at it yet. Which spell from `you.attack_spells_available` should open the fight?")
        else:
            question = (f"The mage has already cast {focus.casts} spell{'s' if focus.casts > 1 else ''} at "
                        f"{focus.name}, which is now {focus.info['health']}. Which spell from "
                        "`you.attack_spells_available` should it cast at it next?")
        criteria = {c.id: f"{c.name}: {c.info['effect']} ({c.info['mana']} mana)" for c in sit.spells}
        criteria["none"] = "Cast nothing right now, for example to save mana for later."
        qs["spell"] = {
            "type": "choice",
            "instructions": instructions(
                sit, question,
                guidance="Unless the player's strategy says otherwise: use strong spells while mana is plentiful and "
                         "cheaper ones when mana runs low; finish a nearly dead creature with a quick, cheap spell.",
            ),
            "criteria": criteria,
        }

    # Looting questions only matter with no hostile close (policy masks loot otherwise),
    # so they are not asked mid-fight: unused answers would be re-asked every second.
    fighting = any(h.distance <= CLOSE_TILES for h in sit.hostiles)

    if sit.corpses and not fighting:
        criteria = {c.id: f"{c.info['name']}, {c.info['distance']}" for c in sit.corpses}
        criteria["none"] = "None of these corpses is worth going to now."
        qs["corpse"] = {
            "type": "choice",
            "instructions": instructions(sit, f"If the {who} loots, which corpse in `corpses_not_yet_looted` should they loot first?"),
            "criteria": criteria,
        }

    for it in [] if fighting else sit.items:
        qs[f"take_{it.id}"] = {
            "type": "noul",
            "instructions": instructions(
                sit, f"Is the item with id {it.id} in `items_in_open_corpse` worth picking up for this {who}?"),
            "criteria": {
                "true": "Useful or valuable: weapons, armour, jewellery, gems, reagents, scrolls, magic items, "
                        "or anything with notable properties.",
                "false": "Junk such as bones, hair, ordinary food, worthless cloth, or anything too heavy to be worth it.",
            },
        }
    return qs


def describe_hostile(info: dict[str, Any], who: str = "warrior") -> str:
    parts = [info["name"], info["health"], info["distance"]]
    if info.get("strength"):
        parts.append(info["strength"])
    if info.get("casts_spells"):
        parts.append("a spellcaster")
    if info.get("direction"):
        parts.append(f"to the {info['direction']}")
    if "in_spell_range" in info:
        parts.append("within spell range" if info["in_spell_range"] else "out of spell range")
    if info.get("your_current_target"):
        parts.append(f"the {who} is already fighting it")
    if info.get("the_players_target"):
        parts.append("the player's own target")
    elif info.get("attacking_you"):
        parts.append("attacking the player")
    return ", ".join(parts)
