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

ARCHER_ROLE = (
    "You are deciding for an archer in the game Ultima Online. The archer shoots monsters from a distance "
    "with a bow or crossbow. Every shot uses an arrow or bolt from the pack, so it cannot fight without them, "
    "and it has to stand still for a moment to shoot. It is weaker in melee than a warrior and heals with "
    "bandages. Bandages and potions are applied automatically when health is low, so you do not need to choose "
    "healing; choose what the archer does next."
)

TAMER_ROLE = (
    "You are deciding for a tamer in the game Ultima Online. The tamer fights by sending its pet, an animal it "
    "tamed, to attack a creature, while the tamer stays back: it is weak in a fight itself. Its pet is described "
    "under `you.pet`. A pet that dies is gone for good, so a tamer calls it back before it loses. Bandaging the "
    "tamer and its pet is done automatically when either is hurt and close, so you do not need to choose healing; "
    "choose what the tamer does next."
)

BARD_ROLE = (
    "You are deciding for a bard in the game Ultima Online. The bard fights with music played on an instrument: "
    "provocation turns one monster against another so they fight each other, peacemaking calms a monster so it "
    "stops attacking for a while, and discordance weakens one. Songs can fail, more often against strong "
    "creatures, and it is weak in a fight itself. Bandages and potions are applied automatically when health is "
    "low, so you do not need to choose healing; choose what the bard does next."
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


ARCHER_INTENTS: dict[str, Any] = {
    "fight": {
        "what": "Shoot one of the hostile creatures, or keep shooting it.",
        "when": "A hostile creature is within shooting range and there are arrows or bolts left, "
                "or a creature is already attacking the archer.",
        "not_for": "When no arrows or bolts are left: the bow cannot shoot.",
    },
    "flee": {
        "what": "Run away from the hostile creatures.",
        "when": "Staying would probably get the archer killed: health is near death or falling fast while several "
                "creatures are attacking at once, and bandages and potions cannot heal fast enough.",
        "not_for": "Ordinary fights the archer is winning, or being lightly or moderately wounded.",
    },
    "leave": {
        "what": "Get away from this place altogether: run until the hostile creatures are out of sight, and stay away "
                "for now.",
        "when": "A creature far stronger than the archer is here or coming, or arrows, bandages or potions are nearly "
                "gone while creatures are still fighting, so the fight cannot be won.",
        "not_for": "Fights the archer is winning, or a single weak creature that is nearly dead.",
    },
    "loot": {
        "what": "Go to a corpse that has not been looted yet and take what is in it, including arrows: monsters that "
                "were hit often carry some back.",
        "when": "No hostile creature is close and there is an unlooted corpse nearby.",
        "not_for": "While a hostile creature is adjacent or close.",
    },
    "seek": {
        "what": "Walk towards a hostile creature that is out of shooting range, to start the next fight.",
        "when": "Hostile creatures are around but none is within shooting range, and the archer has health and arrows.",
    },
    "rest": INTENTS["rest"],
}


TAMER_INTENTS: dict[str, Any] = {
    "fight": {
        "what": "Send the pet to attack one of the hostile creatures, or keep it attacking.",
        "when": "The pet is in sight and healthy enough to fight, and a hostile creature is near, or one is attacking "
                "the tamer or its pet.",
        "not_for": "When the tamer has no pet in sight.",
    },
    "flee": {
        "what": "Run away from the hostile creatures, calling the pet along.",
        "when": "Staying would probably get the tamer killed: the tamer itself is being attacked and its health is "
                "near death or falling fast.",
        "not_for": "Ordinary fights the pet is winning.",
    },
    "leave": {
        "what": "Get away from this place altogether with the pet: run until the hostile creatures are out of sight, "
                "and stay away for now.",
        "when": "A creature far stronger than the pet is here or coming, or the pet is gone, or bandages are nearly "
                "gone while creatures are still fighting.",
        "not_for": "Fights the pet is winning, or a single weak creature that is nearly dead.",
    },
    "loot": INTENTS["loot"],
    "seek": {
        "what": "Walk, with the pet following, towards a hostile creature that is far away, to start the next fight.",
        "when": "Hostile creatures are around but far off, and the tamer and its pet are healthy.",
    },
    "rest": {
        "what": "Stay put with the pet and wait.",
        "when": "Nothing needs doing, or the pet should recover health before the next fight.",
    },
}


MAGE_TAMER = ("It is also a mage: it casts attack spells at the creature its pet fights, from a distance, and "
              "heals its pet with spells.")
WARRIOR_MAGE = ("It is also a mage: it can open a fight with an attack spell while a creature is still coming, "
                "then fights in melee.")

# "Casters first" alone sent a warrior at an idle wisp the world store said to leave alone
# (wisp scenario, 2026-10-07): only a caster that is fighting comes first, and idle creatures
# are said to be "not fighting".
TARGET_GUIDANCE = (
    "Unless the player's strategy says otherwise: a spellcaster that is fighting hurts from any distance and can "
    "paralyse, so go for it first, even past closer ones, unless the creature being fought is nearly dead. Prefer "
    "creatures that are fighting over ones that are not. Otherwise prefer the creature already being fought unless "
    "another is much more dangerous or much closer, and close, weakened creatures over distant ones."
)
TAMER_TARGETS = "For a tamer this is the creature to set its pet on; anything attacking the tamer itself comes first. "

BARD_INTENTS: dict[str, Any] = {
    "fight": {
        "what": "Use a song on the hostile creatures: incite one against another, calm one, or weaken one.",
        "when": "Hostile creatures are near and the bard has an instrument.",
        "not_for": "When the bard has no instrument.",
    },
    "flee": {
        "what": "Run away from the hostile creatures.",
        "when": "Staying would probably get the bard killed: creatures are attacking it and its health is near death "
                "or falling fast.",
        "not_for": "Fights where the creatures are busy with each other or calmed.",
    },
    "leave": INTENTS["leave"],
    "loot": INTENTS["loot"],
    "seek": {
        "what": "Walk towards hostile creatures that are far away, to start the next fight.",
        "when": "Hostile creatures are around but far off, and the bard is healthy.",
    },
    "rest": {
        "what": "Stay put and wait, for example while incited creatures fight each other.",
        "when": "Nothing needs doing, or the creatures are busy fighting each other.",
    },
}

SONG_CHOICES = {
    "provoke": "Provocation: incite the target to attack another hostile creature, so they fight each other. Best "
               "with two or more creatures, inciting the strongest against another.",
    "peace": "Peacemaking: calm the target so it stops attacking for a while. Best when one creature is on the bard "
             "and nothing else can be set against it.",
    "discord": "Discordance: weaken the target, lowering its strength and skills. Best before a hard fight with a "
               "single strong creature.",
    "none": "Play nothing right now, for example while creatures already fight each other.",
}


def role(sit: Situation) -> str:
    if sit.archetype == "mage-tamer":
        return f"{TAMER_ROLE} {MAGE_TAMER}"
    if sit.is_warrior_mage:
        return f"{ROLE} {WARRIOR_MAGE}"
    return MAGE_ROLE if sit.is_mage else ARCHER_ROLE if sit.is_archer else TAMER_ROLE if sit.is_tamer \
        else BARD_ROLE if sit.is_bard else ROLE


def character(sit: Situation) -> str:
    """The word the questions use for the character; a hybrid goes by its main way of fighting."""
    return "tamer" if sit.is_tamer else "mage" if sit.is_mage else "archer" if sit.is_archer \
        else "bard" if sit.is_bard else "warrior"


def instructions(sit: Situation, question: str, **extra: str) -> dict[str, Any]:
    """Role, the player's own strategy when they wrote one, then the question."""
    out: dict[str, Any] = {"role": role(sit)}
    if sit.strategy:
        out["player_strategy"] = sit.strategy
        out["using_the_strategy"] = (f"These are the player's own instructions for how their {character(sit)} should "
                                     "play. Follow them wherever they bear on this question.")
    if sit.known:
        out["what_you_know"] = ("`what_you_know_about_this_place` holds facts from earlier play and guides about "
                                "this place and these creatures. Use them wherever they bear on this question.")
    out["question"] = question
    out.update(extra)
    return out


def build(sit: Situation) -> dict[str, dict[str, Any]]:
    who = character(sit)
    qs: dict[str, dict[str, Any]] = {
        "intent": {
            "type": "choice",
            "instructions": instructions(sit, f"What should the {who} do next?"),
            "criteria": TAMER_INTENTS if sit.is_tamer else MAGE_INTENTS if sit.is_mage
            else ARCHER_INTENTS if sit.is_archer else BARD_INTENTS if sit.is_bard else INTENTS,
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

    # Leaving is a judgment Jev makes poorly as one of six intents (with an ogre lord adjacent
    # it still put fight at 80%), so when there is a reason to consider it, it is asked
    # on its own, as a yes/no.
    if leave_reason(sit):
        qs["leave_now"] = {
            "type": "noul",
            "instructions": instructions(
                sit, f"Should the {who} leave this place now, running until nothing hostile is in sight?",
                reason=leave_reason(sit)),
            # Asked as "supplies are nearly gone with several creatures still attacking", Jev put
            # 0.24-0.30 on leaving in attrition rounds that went on to die (2026-10-07); worded as not
            # enough healing to outlast them, 0.40-0.60, with 0.2 where staying won.
            "criteria": {
                "true": f"Staying means dying: there isn't enough healing left to outlast the creatures attacking "
                        f"(supplies nearly gone with three or more close), a creature far stronger than the {who} is "
                        f"close or coming for it, or what is known about this place says the {who} can't win against "
                        "what is here.",
                "false": f"The {who} can win here: only one or two weak or nearly dead creatures are left, or supplies "
                         "are plentiful, or the strong creature is far off and not coming.",
            },
        }

    # A tamer's pet is lost for good if it dies, so whether to call it back is asked on its own.
    if sit.is_tamer and (reason := pull_back_reason(sit)):
        qs["pull_back"] = {
            "type": "noul",
            "instructions": instructions(
                sit, "Should the tamer call its pet back now, before the pet dies?", reason=reason),
            "criteria": {
                "true": "The pet is losing: its health is falling while it fights something stronger than it, or "
                        "several creatures at once, and it will die if it stays.",
                "false": "The pet can still win: its health is holding, or the creature it fights is nearly dead "
                         "or weak.",
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
                guidance=(TAMER_TARGETS if sit.is_tamer else "") + TARGET_GUIDANCE,
            ),
            "criteria": criteria,
        }

    # A bard's song, asked alongside the target; provocation also needs whom the target attacks.
    if sit.is_bard and sit.targets:
        qs["song"] = {
            "type": "choice",
            "instructions": instructions(sit, "Which song should the bard play at the creature it targets?"),
            "criteria": SONG_CHOICES,
        }
        if len(sit.targets) >= 2:
            qs["onto"] = {
                "type": "choice",
                "instructions": instructions(
                    sit, "If the bard incites one creature against another, which creature in `hostile_creatures` "
                         "should be attacked by it?",
                    guidance="The one that would otherwise hurt the bard most, so the two keep each other busy."),
                "criteria": {h.id: describe_hostile(h.info, who) for h in sit.targets},
            }

    # Which spell, asked alongside the target so a cast needs no second round trip. The
    # question says where the fight stands, so "open with ..." strategies have a hook.
    if sit.casts and sit.targets and sit.spells:
        focus = next((h for h in sit.targets if h.info["your_current_target"]), None) \
            or min(sit.targets, key=lambda h: h.distance)
        if focus.casts == 0:
            question = (f"The {who} is about to open the fight against {focus.name} ({focus.info['health']}): no spell "
                        "has been cast at it yet. Which spell from `you.attack_spells_available` should open the fight?")
        else:
            question = (f"The {who} has already cast {focus.casts} spell{'s' if focus.casts > 1 else ''} at "
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


def pull_back_reason(sit: Situation) -> str:
    """Why the pet might need calling back, or "" when it is fine (or not fighting)."""
    pet = sit.pet
    fighting = sit.agent.get("pet_target", 0)
    if not pet or not fighting:
        return ""
    foe = next((h for h in sit.hostiles if h.serial == fighting), None)
    on_it = [h for h in sit.hostiles if h.distance <= 12]
    if sit.pet_pct < 60:
        return f"The pet is {sit.state['you']['pet']['health']}" + (f", fighting {foe.name} ({foe.info['health']})"
                                                                     if foe else "") + "."
    if foe and str(foe.info.get("strength", "")).startswith(("far stronger", "stronger")):
        return f"The pet is fighting {foe.name}, {foe.info['strength']}."
    if len(on_it) >= 3:
        return f"{len(on_it)} creatures are near the fight."
    return ""


def leave_reason(sit: Situation) -> str:
    """Why leaving might be right, in words, or "" when there is no reason to ask."""
    strong = [h for h in sit.hostiles if str(h.info.get("strength", "")).startswith("far stronger") and h.distance <= 12]
    if strong:
        return f"{strong[0].name} is {strong[0].info['distance']}, and far stronger than the character."
    close = [h for h in sit.hostiles if h.distance <= CLOSE_TILES]
    if str(sit.state["you"].get("supplies", "")).startswith("nearly gone") and len(close) >= 2:
        return f"Supplies are {sit.state['you']['supplies']}, with {len(close)} creatures close."
    # A mage or an archer can't take many hits: four or more at once outnumber it.
    if (sit.is_mage or sit.is_archer) and len(close) >= 4:
        return f"{len(close)} creatures are close to the {character(sit)}, who is weak in melee."
    if sit.known and sit.hostiles:
        return "Facts from earlier play and guides about this place and these creatures are in " \
               "`what_you_know_about_this_place`."
    return ""


def describe_hostile(info: dict[str, Any], who: str = "warrior") -> str:
    parts = [info["name"], info["health"], info["distance"]]
    if info.get("strength"):
        parts.append(info["strength"])
    if info.get("casts_spells"):
        parts.append("a spellcaster")
    if info.get("aggressive") is False and not info.get("your_current_target"):
        parts.append("not fighting")
    if info.get("direction"):
        parts.append(f"to the {info['direction']}")
    if "in_spell_range" in info:
        parts.append("within spell range" if info["in_spell_range"] else "out of spell range")
    if "in_shooting_range" in info:
        parts.append("within shooting range" if info["in_shooting_range"] else "out of shooting range")
    if info.get("your_pet_is_fighting_it"):
        parts.append("the pet is fighting it")
    if info.get("your_current_target"):
        parts.append(f"the {who} is already fighting it")
    if info.get("the_players_target"):
        parts.append("the player's own target")
    elif info.get("attacking_you"):
        parts.append("attacking the player")
    return ", ".join(parts)
