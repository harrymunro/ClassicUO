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


def instructions(sit: Situation, question: str, **extra: str) -> dict[str, Any]:
    """Role, the player's own strategy when they wrote one, then the question."""
    out: dict[str, Any] = {"role": ROLE}
    if sit.strategy:
        out["player_strategy"] = sit.strategy
        out["using_the_strategy"] = ("These are the player's own instructions for how their warrior should play. "
                                     "Follow them wherever they bear on this question.")
    out["question"] = question
    out.update(extra)
    return out


def build(sit: Situation) -> dict[str, dict[str, Any]]:
    qs: dict[str, dict[str, Any]] = {
        "intent": {
            "type": "choice",
            "instructions": instructions(sit, "What should the warrior do next?"),
            "criteria": INTENTS,
        },
        # A factual risk judgment, so it does not see the strategy; code combines the two.
        "in_danger": {
            "type": "noul",
            "instructions": {
                "role": ROLE,
                "question": "Is the warrior at serious risk of dying in the next few seconds if they keep fighting?",
            },
            "criteria": {
                "true": "Health is near death or badly wounded while several hostile creatures are adjacent, "
                        "or health is low and no heal potion can be drunk right now.",
                "false": "Health is fine or the warrior is clearly winning, or nothing is attacking.",
            },
        },
    }

    if sit.hostiles:
        criteria = {h.id: describe_hostile(h.info) for h in sit.hostiles}
        criteria["none"] = "None of these creatures should be attacked."
        qs["target"] = {
            "type": "choice",
            "instructions": instructions(
                sit,
                "If the warrior fights, which hostile creature in `hostile_creatures` should they attack?",
                guidance="Unless the player's strategy says otherwise: prefer the creature already being fought "
                         "unless another is much more dangerous or much closer; prefer close, weakened creatures "
                         "over distant ones.",
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
            "instructions": instructions(sit, "If the warrior loots, which corpse in `corpses_not_yet_looted` should they loot first?"),
            "criteria": criteria,
        }

    for it in [] if fighting else sit.items:
        qs[f"take_{it.id}"] = {
            "type": "noul",
            "instructions": instructions(
                sit, f"Is the item with id {it.id} in `items_in_open_corpse` worth picking up for this warrior?"),
            "criteria": {
                "true": "Useful or valuable: weapons, armour, jewellery, gems, reagents, scrolls, magic items, "
                        "or anything with notable properties.",
                "false": "Junk such as bones, hair, ordinary food, worthless cloth, or anything too heavy to be worth it.",
            },
        }
    return qs


def describe_hostile(info: dict[str, Any]) -> str:
    parts = [info["name"], info["health"], info["distance"]]
    if info.get("direction"):
        parts.append(f"to the {info['direction']}")
    if info.get("your_current_target"):
        parts.append("the warrior is already fighting it")
    return ", ".join(parts)
