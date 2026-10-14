"""Whether to help a hurt companion now, and whom: a system-one question that weighs the moment (cuo-cym.26).

policy.help_mates decides by thresholds: a companion under 60% is healed (a mage's Greater Heal within spell range
and in sight) or bandaged (within 2 tiles), one being healed by another is left to them above 30%, a member under 50%
helps nobody, and nobody walks over to a hurt companion while anything hostile is within 8 tiles. Here the same
moment is put to system one instead: who is hurt and how fast, what is on whom, the character's own health, mana and
bandages, and what it is doing, with whom it could help and how as the options, or nobody.

Code keeps the floors, which need no judgment: a dead or running character helps nobody, nor one in combat assist
or with healing off; a companion is offered only when it is alive and hurt, and only by a way the character has now
(Greater Heal castable, with the mana and reagents for it, within spell range and in sight; bandages with the Healing
skill, within reach or a short walk away). The leader's order to heal someone (PolicyConfig.heal_now) and a
companion's pet stay with policy.help_mates.

Measured without the game by scripts/helping_check.py (docs/results.md, "A party that goes in together"), and not
part of the fight loop: on the moments held back from the wording, Jev did what was wanted in 26 of 28 and Decision 1
in 25, against the thresholds' 24, which is no clear gain for a request more at every decision with someone hurt.
What it got that the thresholds miss is a companion losing health fast above 60%, and a mage's last heal for the one
falling rather than the lowest; what it missed is a walk over that was wanted.
"""

from __future__ import annotations

from typing import Any

from .judge import ChoiceResult
from .policy import BANDAGE_REACH, HEALERS, HELP_WALK, attacking_me, coming
from .spells import GREATER_HEAL
from .state import SPELL_RANGE, Mate, Situation

HELP_CUT = 0.5          # a companion is helped when system one gives it this or more
OFFER_BELOW = 95        # health %: a companion below this is offered ("unhurt" above)
GREATER_HEAL_MANA = 11  # a 4th-circle spell
FALLING = 10            # points of health lost in the last few seconds that are worth saying


def ways(sit: Situation, mates: list[Mate] | None = None) -> list[tuple[Mate, str]]:
    """Whom this character could help now, and how: "heal" (Greater Heal from where it stands), "bandage" (within
    reach) or "walk" (a few steps over, then a bandage). The floors, as code: none for a dead character, in combat
    assist or with healing off, and none to a companion dead, unhurt, or out of every way it has."""
    if sit.player.get("dead") or sit.assisting or sit.authority("heal") == "off":
        return []
    skills = sit.player.get("skills") or {}
    # One bandage at a time: with one on someone (or on itself) there is nothing to bandage with until it is done.
    can_bandage = (sit.player.get("supplies") or {}).get("bandages", 0) > 0 and skills.get("Healing", 0) >= 30 \
        and not sit.agent.get("bandaging")
    can_walk = can_bandage and sit.authority("move") == "auto"
    spell = sit.archetype in HEALERS and sit.can_cast(GREATER_HEAL)
    out = []
    for m in sorted(mates if mates is not None else sit.mates, key=lambda m: m.distance):
        if m.dead or m.hits_pct is None or m.hits_pct >= OFFER_BELOW:
            continue
        if spell and m.distance <= SPELL_RANGE and m.in_sight:
            out.append((m, "heal"))
        elif can_bandage and m.distance <= BANDAGE_REACH:
            out.append((m, "bandage"))
        elif can_walk and m.distance <= HELP_WALK:
            out.append((m, "walk"))
    return out


def options(offered: list[tuple[Mate, str]]) -> dict[str, tuple[Mate, str]]:
    """The question's options: an id for each companion offered."""
    return {f"h{i + 1}": mw for i, mw in enumerate(offered)}


def _tiles(n: int) -> str:
    return f"{n} tile{'' if n == 1 else 's'}"


def _health(pct: int | None, drop: int) -> str:
    if pct is None:
        return "unknown"
    return f"{pct}%" + (f", down {drop} points in the last few seconds" if drop >= FALLING else ", not falling")


def _on(sit: Situation, serials: list[int]) -> list[str]:
    hostile = {h.serial: h for h in sit.hostiles}
    return [f"{hostile[s].name} ({_tiles(hostile[s].distance)} from you)" for s in serials if s in hostile]


def _between(sit: Situation, m: Mate) -> list[str]:
    """Creatures by the way to a companion: within 2 tiles of it, or of the straight line to it."""
    raw = {x["serial"]: x for x in sit.raw.get("mobiles", [])}
    to = raw.get(m.serial)
    if to is None:
        return []
    out = []
    steps = max(1, m.distance)
    line = [(round(to["dx"] * i / steps), round(to["dy"] * i / steps)) for i in range(1, steps + 1)]
    for h in sit.hostiles:
        r = raw.get(h.serial)
        if r is not None and any(max(abs(r["dx"] - x), abs(r["dy"] - y)) <= 2 for x, y in line):
            out.append(h.name)
    return out


def doing(sit: Situation) -> str:
    """What the character is doing in the fight, in words."""
    engaged = sit.hostile_by_serial(sit.agent.get("engaged", 0))
    pet_on = sit.hostile_by_serial(sit.agent.get("pet_target", 0)) if sit.is_tamer else None
    if engaged is not None:
        what = "casting at" if sit.is_mage else "shooting" if sit.is_archer else "fighting"
        return f"{what} {engaged.name}, {_tiles(engaged.distance)} away"
    if pet_on is not None:
        return f"its pet is fighting {pet_on.name}; the tamer itself fights nothing"
    if sit.is_bard and sit.hostiles:
        return "playing songs on the creatures (provocation, peacemaking) between other things"
    return "nothing: no creature of its own to fight" if sit.hostiles else "nothing: the fight is over"


def help_state(sit: Situation, drops: dict[str, int] | None = None) -> dict[str, Any]:
    """The moment in words, for help_question: the character, its companions, the creatures in view. `drops` is
    each companion's fall in health over the last few seconds (points), by name."""
    drops = drops or {}
    p = sit.player
    you: dict[str, Any] = {"name": p.get("name", ""), "plays_as": sit.archetype, "health": _health(sit.hp_pct, sit.hp_drop),
                           "doing": doing(sit)}
    if sit.archetype in HEALERS:
        mana = p.get("mana")
        you["mana"] = f"{sit.mana_pct}%" + (f" ({mana} points; a Greater Heal costs {GREATER_HEAL_MANA})" if mana is not None else "")
    skills = p.get("skills") or {}
    if skills.get("Healing", 0) >= 30:
        n = (p.get("supplies") or {}).get("bandages", 0)
        you["bandages"] = f"{n} left" + ("; one is on someone now" if sit.agent.get("bandaging") else "")
    on_me = [h for h in sit.hostiles if attacking_me(sit, h)]
    if on_me:
        you["attacked_by"] = [f"{h.name} ({_tiles(h.distance)} away)" for h in on_me]
    companions = []
    for m in sorted(sit.mates, key=lambda m: m.distance):
        if m.dead:
            companions.append({"name": m.name, "plays_as": m.plays_as, "health": "dead"})
            continue
        c: dict[str, Any] = {"name": m.name, "plays_as": m.plays_as, "health": _health(m.hits_pct, drops.get(m.name, 0)),
                             "distance": _tiles(m.distance) + ("" if m.in_sight else ", out of sight")}
        on = _on(sit, m.attackers)
        c["attacked_by"] = on or "nothing"
        if m.being_helped:
            c["being_healed"] = "another companion called a moment ago that they are healing them"
        companions.append(c)
    fighting = coming(sit)
    creatures = []
    on_whom = {s: m.name for m in sit.mates for s in m.attackers}
    for h in sorted(sit.hostiles, key=lambda h: h.distance)[:6]:
        who = "you" if attacking_me(sit, h) else on_whom.get(h.serial, "")
        creatures.append(f"{h.name}: {_tiles(h.distance)} from you" + (f", attacking {who}" if who else "")
                         + ("" if h in fighting else ", not in war mode")
                         + (f", {h.hits_pct}% health" if h.hits_pct is not None and h.hits_pct < 100 else ""))
    near = [h for h in fighting if h.distance <= 8]
    return {"you": you, "companions": companions, "creatures": creatures or ["none in view"],
            "the_fight": f"on: {len(near)} creature{'' if len(near) == 1 else 's'} in war mode within 8 tiles"
            if near else "over here: nothing in war mode within 8 tiles"}


def option_words(sit: Situation, m: Mate, how: str, drop: int = 0) -> str:
    """One companion as an option: how it is, what is on it, and the way to help it."""
    on = [h.name for h in sit.hostiles if h.serial in m.attackers]
    who = (f"{m.name} ({m.plays_as}, {_health(m.hits_pct, drop)}; "
           + (f"attacked by {', '.join(on)}" if on else "nothing attacking them")
           + ("; another companion is healing them" if m.being_helped else "") + ")")
    if how == "heal":
        return f"{who}: cast Greater Heal on them from where you stand, {_tiles(m.distance)} away."
    if how == "bandage":
        return f"{who}: bandage them; they are {_tiles(m.distance)} away, within reach."
    between = _between(sit, m)
    return (f"{who}: walk the {_tiles(m.distance)} over to them, then bandage them"
            + (f"; by the way or beside them: {', '.join(between)}." if between else "; nothing hostile on the way."))


def help_question(sit: Situation, opts: dict[str, tuple[Mate, str]], drops: dict[str, int] | None = None) -> dict[str, Any]:
    """Whom to help now, or nobody: the companions offered (options) as the choices, with the costs and what a help
    saves in the role. Its wording was settled on the first set of helping_check.py's moments."""
    me = sit.player.get("name") or "the character"
    drops = drops or {}
    role = (f"You judge a moment of a fight in the game Ultima Online for {me}, a {sit.archetype} in a party of "
            f"adventurers: should {me} help a hurt companion right now, and if so whom? Weigh what the help costs "
            f"{me} against what it saves. "
            f"What it costs: a bandage on a companion within reach costs almost nothing: it takes a few seconds, {me} "
            f"fights on meanwhile, and only its own next bandage waits. A Greater Heal costs a mage {GREATER_HEAL_MANA} "
            f"mana and one cast: with mana to spare that is cheap; with little left (a heal or two) it is worth it only "
            f"for a companion near death or losing health fast. A walk over to a companion while creatures fight costs "
            f"a great deal when it goes into what is on them or by it: in one fight three ranged members walked into "
            f"the melee to bandage the warrior, and the whole party died. A warrior can take the hits; the others "
            f"can't. With nothing on the way and nothing on the companion, or once the fight is over, a walk costs "
            f"little: a companion badly hurt a short walk away once the fight is over is worth walking to and "
            f"bandaging, and so is one near death a few steps off with nothing on them and nothing on the way, while "
            f"the fight goes on elsewhere. "
            f"What it saves: a companion near death, or losing health fast (some 15 points or more in the last few "
            f"seconds), can die before it heals itself: help them. One hurt but not losing health, or only lightly "
            f"hurt, heals itself (every companion has bandages, potions or spells) and needs nobody, though once the "
            f"fight is over a bandage within reach does no harm. One another companion is healing already is in their "
            f"hands, unless near death or still losing health fast: one heal doesn't keep up with that, so help them "
            f"too. "
            f"{me} itself: badly hurt, or losing health with creatures on it, it sees to itself first, unless a "
            f"companion beside it is near death.")
    criteria = {k: option_words(sit, m, how, drops.get(m.name, 0)) for k, (m, how) in opts.items()}
    criteria["none"] = (f"Help nobody now: no companion {me} can reach is near death or losing health fast, or those "
                        f"who are are in others' hands, or the only way to them is a walk into the fight, or {me} "
                        f"must see to itself.")
    return {"type": "choice",
            "instructions": {"role": role, "question": f"Should {me} help one of its companions now, and if so whom?"},
            "criteria": criteria}


def pick(answer: ChoiceResult, opts: dict[str, tuple[Mate, str]], cut: float = HELP_CUT) -> tuple[Mate, str] | None:
    """The companion to help and how, from the answer: its likeliest option when that is a companion at `cut`."""
    probs = answer.probabilities or {answer.choice: answer.confidence}
    best = max(probs, key=probs.get)
    return opts[best] if best in opts and probs[best] >= cut else None
