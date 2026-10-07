"""Turn Jev's answers into actions. Code owns this: it masks options that the
facts rule out, gates on confidence, and only sends actions the client can do."""

import time
from dataclasses import dataclass, field
from typing import Any

from .judge import Answers, ChoiceResult
from .spells import GREATER_HEAL, MEDITATION, PROTECTION
from .state import SPELL_RANGE, Candidate, Situation


@dataclass
class PolicyConfig:
    min_intent_confidence: float = 0.35
    min_target_confidence: float = 0.25
    min_spell_confidence: float = 0.3
    flee_danger: float = 0.5        # flee needs the intent *and* the danger judgment to agree
    panic_danger: float = 0.9       # ...unless danger is this sure and health is critical
    take_item: float = 0.6
    close_tiles: int = 3
    spell_range: int = 7            # a mage keeps its target within this many tiles
    bow_range: int = 8              # an archer shoots from this far, or its weapon's range if shorter
    meditate_below: int = 80        # mana %: a resting mage meditates below this
    avoid_players: bool = True      # auto mode leaves when a red or criminal player comes close
    kite: bool = True               # a mage or archer steps back from melee between spells or shots
    pet_heal_below: int = 70        # a tamer bandages its pet below this health %, when within reach
    # Between hunts (travel, rest, errands): fight only what is close or attacking, never seek or
    # loot, so the character isn't defenceless while the planner's other goals run.
    defend_only: bool = False
    pull_back: float = 0.6          # Jev's "call the pet back" at or above this does it
    pet_last_stand: int = 20        # ...and below this pet health % code calls it back anyway
    song_every: float = 6.0         # seconds between a bard's songs (the server's skill delay, with room)
    min_song_confidence: float = 0.3
    # Set from the player's strategy (strategy.py).
    allow_flee: bool = True
    target_priority: str = "current_first"
    looting: str = "valuables"
    opening_spell: str = ""
    main_spell: str = ""


@dataclass
class Memory:
    looted: set[int] = field(default_factory=set)
    loot_started: dict[int, float] = field(default_factory=dict)
    taken: set[int] = field(default_factory=set)
    declined: set[int] = field(default_factory=set)
    casts_at: dict[int, int] = field(default_factory=dict)  # spells cast at each creature
    last_meditate: float = 0.0
    hinted: dict[str, float] = field(default_factory=dict)  # combat assist: when each hint was last shown
    leaving_until: float = 0.0  # leaving the area: keep running from whatever is in sight until then
    last_kite: float = 0.0      # a mage's last step back from melee
    last_protection: float = -1e9  # when Protection was last cast: buff icons may not show it
    kite_casts: int = -1        # attack spells cast by then: the next step back waits for one more
    kite_ammo: int = 1 << 30    # an archer's arrows by then: the next step back waits for a shot
    last_pet_bandage: float = -1e9  # a tamer's last bandage on its pet
    pulling_until: float = 0.0      # the pet was called back: don't send it in again until then
    last_song: float = -1e9         # a bard's last song
    last_pet_call: float = -1e9     # a tamer's last "all follow me"
    sung: dict[int, float] = field(default_factory=dict)  # creature -> when a song last hit it (provoked or calmed)

    def hint_due(self, key: str, now: float, every: float = 12.0) -> bool:
        if now - self.hinted.get(key, -1e9) < every:
            return False
        self.hinted[key] = now
        return True

    @property
    def skip_items(self) -> set[int]:
        return self.taken | self.declined

    def update(self, agent: dict[str, Any], now: float) -> None:
        """A corpse counts as looted once the client has finished with it.
        Call before building the Situation, so finished corpses drop out of it."""
        looting = agent.get("looting", 0)
        for serial, started in list(self.loot_started.items()):
            if (serial != looting and now - started > 1.0) or now - started > 20:
                self.looted.add(serial)
                del self.loot_started[serial]


@dataclass
class Decision:
    intent: str
    confidence: float
    actions: list[dict[str, Any]]
    note: str
    masked: list[str] = field(default_factory=list)
    gated: bool = False
    target: Candidate | None = None
    target_confidence: float | None = None  # Jev's, when its pick was used; None when code chose
    spell: Candidate | None = None
    spell_confidence: float | None = None
    spell_why: str = ""  # "strategy" (the player's named spell), "jev", or "fallback"

    @property
    def next_move(self) -> dict[str, Any] | None:
        """The best combat move in this decision, for the client's next-move key."""
        return next((a for a in self.actions if a["verb"] in ("cast", "attack")), None)


def masked_intent(sit: Situation, answer: ChoiceResult, cfg: PolicyConfig) -> tuple[str, float, list[str]]:
    close = [h for h in sit.hostiles if h.distance <= cfg.close_tiles]
    # A warrior seeks anything not yet close; a mage or archer only what it cannot reach.
    reach = SPELL_RANGE if sit.is_mage else shooting_range(sit, cfg) if sit.is_archer \
        else PET_REACH if sit.is_tamer else BARD_REACH if sit.is_bard else cfg.close_tiles
    # Whatever walks needs moving allowed: combat assist never moves the character.
    move = sit.authority("move") != "off"
    within_reach = any(c.distance <= 2 for c in sit.corpses) or bool(sit.items)
    valid = {
        # A bow without arrows shoots nothing; a tamer without its pet, or a bard without an
        # instrument, has nothing to fight with.
        "fight": bool(sit.targets) and not (sit.is_archer and sit.ammo <= 0) and not (sit.is_tamer and not sit.pet)
        and not (sit.is_bard and not sit.player.get("supplies", {}).get("instrument")),
        "flee": bool(close) and cfg.allow_flee and move,
        "loot": bool(sit.corpses or sit.items) and not close and cfg.looting != "nothing"
                and sit.authority("loot") != "off" and (move or within_reach) and not sit.traveling,
        "seek": bool(sit.targets) and not any(h.distance <= reach for h in sit.hostiles) and move
                and not sit.traveling,
        "leave": bool(sit.hostiles) and cfg.allow_flee and move,
        "rest": True,
    }
    if cfg.defend_only:
        valid["seek"] = valid["loot"] = False
    masked = [k for k, ok in valid.items() if not ok and k in answer.probabilities]
    probs = {k: p for k, p in answer.probabilities.items() if valid.get(k, False)}
    total = sum(probs.values())
    if total <= 0:
        # Everything the model wanted is ruled out (e.g. flee under a never-flee strategy):
        # code decides. Cornered means fight.
        return ("fight" if close and valid["fight"] else "rest"), 1.0, masked
    best = max(probs, key=probs.get)
    # Jev's confidence describes its whole distribution. It still holds when the
    # masked options had no real weight; otherwise use the winner's renormalised share.
    if best == answer.choice and total >= 0.95:
        return best, answer.confidence, masked
    return best, probs[best] / total, masked


def decide(sit: Situation, ans: Answers, mem: Memory, cfg: PolicyConfig, now: float | None = None) -> Decision:
    now = time.monotonic() if now is None else now
    if cfg.defend_only:
        for h in sit.hostiles:
            h.allowed = h.allowed and (h.distance <= cfg.close_tiles or bool(h.info.get("aggressive")) and h.distance <= 6)
    dec = decide_intent(sit, ans, mem, cfg, now)
    if sit.is_tamer:
        tend_pet(sit, dec, mem, cfg, now)
    return dec


def decide_intent(sit: Situation, ans: Answers, mem: Memory, cfg: PolicyConfig, now: float) -> Decision:
    intent, conf, masked = masked_intent(sit, ans.choices["intent"], cfg)
    danger = ans.nouls.get("in_danger", 0.0)
    close = [h for h in sit.hostiles if h.distance <= cfg.close_tiles]
    engaged = sit.agent.get("engaged", 0)

    if intent == "flee" and danger < cfg.flee_danger:
        intent = "fight"
    # Code's own emergency flee: nearly dead, the model is sure it is deadly, and no heal
    # potion can be drunk in the next couple of seconds (none left, or on cooldown).
    potion_soon = sit.player.get("supplies", {}).get("heal_potions", 0) > 0 \
        and sit.agent.get("heal_potion_ready_ms", 0) <= 2000
    if cfg.allow_flee and intent != "flee" and close and danger >= cfg.panic_danger and sit.hp_pct < 25 \
            and not potion_soon and sit.authority("move") != "off":
        intent, conf = "flee", danger

    # A red or criminal player close by: in auto mode, leave (the agent never fights players).
    # In combat assist the client has already warned the player.
    danger_player = next((t for t in sit.agent.get("threats", []) if t["kind"] in ("red", "criminal")), None)
    if danger_player and not sit.assisting and sit.authority("move") == "auto" and cfg.avoid_players:
        return Decision("flee", 1.0, [{"verb": "flee", "target": danger_player["serial"], "tiles": 15,
                                        "confidence": 1.0, "reason": "player"}],
                        f"leaving: a {danger_player['kind']} player is {danger_player['distance']} tiles away", masked=[])

    # Leaving: keep running from whatever is still in sight, then stay clear for a while. A
    # tamer goes in short legs and keeps calling its pet, which may still be fighting: a pet
    # left out of sight is lost.
    if mem.leaving_until > now and sit.authority("move") == "auto":
        near = [h for h in sit.hostiles if h.distance <= 14]
        call = [{"verb": "pet", "kind": "follow", "confidence": 1.0, "reason": "leave"}] \
            if sit.is_tamer and sit.pet and sit.pet["distance"] > 3 and now - mem.last_pet_call > 3 else []
        if call:
            mem.last_pet_call = now
        if not near:
            return Decision("leave", 1.0, call, "left: nothing in sight", masked)
        # Something stronger has caught up with a tamer: the pet covers the escape (it may not
        # come back; most monsters run as fast as a character, and the tamer can't take the hits).
        chaser = next((h for h in near if h.distance <= 3 and h.allowed
                       and str(h.info.get("strength", "")).startswith(("far stronger", "stronger"))), None)
        if sit.is_tamer and sit.pet and chaser and sit.agent.get("pet_target", 0) != chaser.serial:
            return Decision("leave", 1.0, [{"verb": "pet", "kind": "kill", "target": chaser.serial, "confidence": 1.0,
                                            "reason": "cover"},
                                           {"verb": "flee", "target": chaser.serial, "tiles": 15, "confidence": 1.0,
                                            "reason": "leave"}],
                            f"leaving: the pet holds off {chaser.name}", masked, target=chaser)
        if sit.agent.get("fleeing"):
            return Decision("leave", 1.0, call, "leaving", masked)
        if sit.is_tamer and sit.pet and sit.pet["distance"] > 6 and not any(h.distance <= 4 for h in near):
            return Decision("leave", 1.0, call, "leaving, waiting for the pet", masked)
        nearest = min(near, key=lambda h: h.distance)
        tiles = 8 if sit.is_tamer else 15
        return Decision("leave", 1.0, call + [{"verb": "flee", "target": nearest.serial, "tiles": tiles, "confidence": 1.0,
                                               "reason": "leave"}], f"leaving, away from {nearest.name}", masked,
                        target=nearest)

    # A cautious strategy gets out early: badly hurt with several creatures on the character.
    outmatched = any(str(h.info.get("strength", "")).startswith("far stronger") for h in sit.hostiles)
    low = str(sit.state["you"].get("supplies", "")).startswith("nearly gone")
    # A mage or archer with four or more on it is outnumbered: a reason to leave, as low supplies are.
    outnumbered = (sit.is_mage or sit.is_archer) and len(close) >= 4
    if cfg.allow_flee and cfg.flee_danger <= 0.45 and sit.hp_pct < 45 and len(close) >= 2 \
            and sit.authority("move") == "auto" and intent != "leave":
        intent, conf = "leave", 1.0
    # Jev's yes/no on leaving, asked when there is a reason to: it decides, not the intent vote.
    # A cautious strategy leaves on weaker signals; relentless ones never (allow_flee off).
    if ans.nouls.get("leave_now", 0.0) >= leave_cut(cfg) and cfg.allow_flee \
            and sit.authority("move") == "auto" and not sit.assisting:
        intent, conf = "leave", ans.nouls["leave_now"]
    # Leaving needs a reason the facts back up, as fleeing needs the danger judgment. A stored
    # fact Jev picked for this place counts when Jev's own yes/no on leaving agrees, and so does
    # having nothing to fight with (no pet in sight, no arrows, no instrument).
    known = bool(sit.known) and ans.nouls.get("leave_now", 0.0) >= leave_cut(cfg)
    cannot_fight = bool(sit.targets) and "fight" in masked
    if intent == "leave" and not (danger >= cfg.flee_danger or outmatched or low and len(close) >= 2 or known
                                  or cannot_fight or outnumbered):
        intent = "fight" if close and "fight" not in masked else "rest"

    # Combat assist never walks the character, so when it would flee it tells the player instead.
    hints: list[dict[str, Any]] = []
    wanted_flee = ans.choices["intent"].choice == "flee" and danger >= cfg.flee_danger \
        or danger >= cfg.panic_danger and sit.hp_pct < 25
    if sit.assisting and close and wanted_flee and cfg.allow_flee and mem.hint_due("danger", now):
        hints.append({"verb": "hint", "text": "this fight is going badly, get out", "reason": "danger"})
    if sit.assisting and sit.is_archer and sit.ammo <= 0 and sit.hostiles and mem.hint_due("ammo", now, 20):
        hints.append({"verb": "hint", "text": f"out of {sit.ranged.get('ammo', 'arrows')}", "reason": "ammo"})

    # A tamer calls its pet back before it dies: Jev's yes/no, or code when the pet is nearly dead.
    pet_fighting = sit.is_tamer and sit.pet and sit.agent.get("pet_target", 0)
    if pet_fighting and (ans.nouls.get("pull_back", 0.0) >= cfg.pull_back or sit.pet_pct < cfg.pet_last_stand):
        why = f"pet at {sit.pet_pct}%" + (f", pull back {ans.nouls['pull_back']:.2f}" if "pull_back" in ans.nouls else "")
        if sit.assisting:
            if mem.hint_due("pet", now, 8):
                hints.append({"verb": "hint", "text": "call your pet back, it is losing", "reason": "pet"})
            return Decision(intent, conf, hints, f"pet losing ({why})", masked)
        if sit.authority("move") == "auto":
            mem.pulling_until = now + 10
            foe = next((h for h in sit.hostiles if h.serial == sit.agent["pet_target"]), None)
            actions = [{"verb": "pet", "kind": "follow", "confidence": 1.0, "reason": "pull back"}]
            if foe:
                actions.append({"verb": "flee", "target": foe.serial, "tiles": 8, "confidence": 1.0, "reason": "pull back"})
            return Decision("flee", 1.0, hints + actions, f"call the pet back ({why})", masked, target=foe)

    if conf < cfg.min_intent_confidence:
        dec = Decision(intent, conf, hints, f"unsure ({conf:.2f}), keeping course", masked, gated=True)
        # Keeping course for a mage in a fight means casting again: the client does not
        # cast on its own the way a warrior's swings continue.
        current = next((h for h in sit.hostiles if h.serial == engaged), None)
        if sit.is_mage and current and current.distance <= SPELL_RANGE:
            dec.target = current
            dec.spell, dec.spell_confidence, dec.spell_why = pick_spell(sit, ans, cfg, current)
            if dec.spell:
                dec.actions.append({"verb": "cast", "spell": dec.spell.name, "target": current.serial, "queue": True,
                                    "confidence": round(conf, 3), "reason": "keep course"})
        return dec

    actions: list[dict[str, Any]] = hints
    meta = {"confidence": round(conf, 3), "reason": intent}
    dec = Decision(intent, conf, actions, "", masked)

    # Items in an open corpse within reach are picked up whenever nothing is close,
    # whatever the main intent: the corpse may already count as looted.
    if not close and intent != "flee" and cfg.looting != "nothing":
        actions.extend(take_items(sit, ans, mem, cfg, {"confidence": round(conf, 3), "reason": "take"}))

    if intent == "fight":
        dec.target, dec.target_confidence = pick_target(sit, ans, cfg)
        target = dec.target
        if target is None:
            dec.note = "no target"
            return dec
        pet_note = ""
        if sit.is_tamer:
            # The pet fights; the tamer stays back. Not while the pet is being called back.
            if now < mem.pulling_until:
                dec.note = f"pet called back, not sending it at {target.name}"
                return dec
            target, pet_note = set_pet_on(sit, dec, target, meta)
        if sit.is_mage:
            # Engage at range so the client follows the creature without closing to melee.
            if target.serial != engaged or sit.agent.get("engaged_range", 1) != cfg.spell_range:
                actions.append({"verb": "attack", "target": target.serial, "range": cfg.spell_range, **meta})
            # Two or more creatures in melee reach: step back between spells (kiting), since
            # every hit interrupts a spell. Not while a spell is being cast: casting roots the mage.
            # One step back per spell: kiting again before a spell has gone off only stops the
            # mage from ever casting.
            adjacent = [h for h in sit.hostiles if h.distance <= 1]
            stats = sit.agent.get("stats", {})
            attack_casts = stats.get("casts", 0) - stats.get("spell_heals", 0)
            if cfg.kite and len(adjacent) >= 2 and not (sit.raw.get("magic") or {}).get("casting") \
                    and sit.authority("move") == "auto" and now - mem.last_kite > 3 and attack_casts > mem.kite_casts:
                mem.last_kite, mem.kite_casts = now, attack_casts
                actions.append({"verb": "kite", "tiles": 5, **meta, "reason": "kite"})
            # With creatures in melee reach every hit interrupts a spell, unless Protection is up.
            # That is AOS: before it Protection only adds armour, and servers send no buff icons.
            if close and sit.raw.get("era", "aos") == "aos" and PROTECTION not in sit.player.get("buffs", []) \
                    and sit.can_cast(PROTECTION) and now - mem.last_protection > 20:
                mem.last_protection = now
                actions.append({"verb": "cast", "spell": PROTECTION, "target": "self", "queue": True,
                                **meta, "reason": "protection"})
                dec.note = f"fight {target.name}, Protection first ({conf:.2f})"
                return dec
            # Queued: the client casts it the moment the current spell and recovery allow.
            if target.distance <= SPELL_RANGE:
                dec.spell, dec.spell_confidence, dec.spell_why = pick_spell(sit, ans, cfg, target)
            elif sit.assisting and mem.hint_due(f"range{target.serial}", now, 8):
                actions.append({"verb": "hint", "text": f"{target.name} is out of spell range", "reason": "range"})
            if dec.spell:
                actions.append({"verb": "cast", "spell": dec.spell.name, "target": target.serial, "queue": True, **meta})
            dec.note = (f"{pet_note}, " if pet_note else "") + f"fight {target.name}" \
                + (f" with {dec.spell.name}" if dec.spell else "") + f" ({conf:.2f})"
        elif sit.is_bard:
            sing(sit, ans, mem, cfg, target, dec, meta, now)
        elif sit.is_tamer:
            dec.note = f"{pet_note} ({conf:.2f})"
        elif sit.is_archer:
            # Engage at range, as a mage does, so the client shoots without closing to melee.
            rng = shooting_range(sit, cfg)
            if target.serial != engaged or sit.agent.get("engaged_range", 1) != rng:
                actions.append({"verb": "attack", "target": target.serial, "range": rng, **meta})
            # Two or more in melee reach: step back, once per shot fired (arrows used) since the
            # last step, since a bow only fires once the archer has stood still for a moment.
            adjacent = [h for h in sit.hostiles if h.distance <= 1]
            if cfg.kite and len(adjacent) >= 2 and sit.authority("move") == "auto" and now - mem.last_kite > 3 \
                    and sit.ammo < mem.kite_ammo:
                mem.last_kite, mem.kite_ammo = now, sit.ammo
                actions.append({"verb": "kite", "tiles": 5, **meta, "reason": "kite"})
            if sit.assisting and target.distance > rng and mem.hint_due(f"range{target.serial}", now, 8):
                actions.append({"verb": "hint", "text": f"{target.name} is out of shooting range", "reason": "range"})
            dec.note = f"{'shoot' if target.serial != engaged else 'keep shooting'} {target.name} ({conf:.2f})"
        else:
            # A warrior-mage opens on a creature that is still coming with one spell (queued:
            # the client casts it as soon as it can, and re-arms the weapon after).
            if sit.is_warrior_mage and target.casts == 0 and 2 <= target.distance <= SPELL_RANGE:
                dec.spell, dec.spell_confidence, dec.spell_why = pick_spell(sit, ans, cfg, target)
                if dec.spell:
                    actions.append({"verb": "cast", "spell": dec.spell.name, "target": target.serial, "queue": True,
                                    **meta, "reason": "opener"})
            if target.serial != engaged:
                actions.append({"verb": "attack", "target": target.serial, **meta})
            dec.note = f"{'fight' if target.serial != engaged else 'keep fighting'} {target.name}" \
                + (f", opening with {dec.spell.name}" if dec.spell else "") + f" ({conf:.2f})"

    elif intent == "leave":
        mem.leaving_until = now + 40
        threat = max(sit.hostiles, key=lambda h: (str(h.info.get("strength", "")).startswith("far"), -h.distance))
        dec.target = threat
        if sit.is_tamer and sit.pet:
            mem.last_pet_call = now
            actions.append({"verb": "pet", "kind": "follow", **meta})
        actions.append({"verb": "flee", "target": threat.serial, "tiles": 8 if sit.is_tamer else 15, **meta})
        dec.note = f"leave, away from {threat.name} ({conf:.2f})"

    elif intent == "flee":
        threat = min(close, key=lambda h: h.distance)
        dec.target = threat
        if sit.is_tamer and sit.pet:
            actions.append({"verb": "pet", "kind": "follow", **meta})
        # A tamer runs a short way only, so its pet stays in sight and can follow.
        actions.append({"verb": "flee", "target": threat.serial, "tiles": 6 if sit.is_tamer else 10, **meta})
        dec.note = f"flee from {threat.name} (danger {danger:.2f})"

    elif intent == "loot":
        corpse = pick_corpse(sit, ans)
        looting = sit.agent.get("looting", 0)
        if corpse and corpse.serial != looting and corpse.serial not in mem.loot_started:
            actions.append({"verb": "loot", "target": corpse.serial, **meta})
        dec.note = f"loot {corpse.name if corpse else 'items'} ({conf:.2f})"

    elif intent == "seek":
        target, dec.target_confidence = pick_target(sit, ans, cfg)
        target = target or min(sit.hostiles, key=lambda h: h.distance)
        dec.target = target
        # A warrior-mage within spell range opens with a spell and lets the creature come: in a
        # calibration run it walked up to orcs 5 tiles off and never cast its opener.
        if sit.is_warrior_mage and target.casts == 0 and 2 <= target.distance <= SPELL_RANGE:
            dec.spell, dec.spell_confidence, dec.spell_why = pick_spell(sit, ans, cfg, target)
            if dec.spell:
                actions.append({"verb": "attack", "target": target.serial, **meta})
                actions.append({"verb": "cast", "spell": dec.spell.name, "target": target.serial, "queue": True,
                                **meta, "reason": "opener"})
                dec.note = f"open on {target.name} with {dec.spell.name} ({conf:.2f})"
                return dec
        raw = next(m for m in sit.raw["mobiles"] if m["serial"] == target.serial)
        p = sit.player
        stop = cfg.spell_range - 1 if sit.is_mage else shooting_range(sit, cfg) - 1 if sit.is_archer \
            else 5 if sit.is_tamer else 8 if sit.is_bard else 1
        actions.append({"verb": "walk_to", "x": p["x"] + raw["dx"], "y": p["y"] + raw["dy"], "distance": stop, **meta})
        dec.note = f"seek {target.name} ({conf:.2f})"

    else:
        dec.note = f"rest ({conf:.2f})"
        # A mage regains mana faster in a trance; the skill has a 10 s delay of its own.
        if sit.is_mage and sit.mana_pct < cfg.meditate_below and not close \
                and "ActiveMeditation" not in sit.player.get("buffs", []) and now - mem.last_meditate > 10:
            mem.last_meditate = now
            actions.append({"verb": "skill", "name": MEDITATION, **meta})
            dec.note = f"meditate ({conf:.2f})"

    return dec


def leave_cut(cfg: PolicyConfig) -> float:
    """Where Jev's yes/no on leaving decides: 0.2 below the strategy's danger threshold, never
    under 0.4. Jev rarely goes past 0.6 on it even when staying kills the character, while a
    fight it can win stays near 0.2, so the cut sits between (0.425 with no strategy)."""
    return max(0.4, cfg.flee_danger - 0.2)


PET_REACH = 8   # tiles: a tamer sets its pet on what is this close, and seeks what is further
BARD_REACH = 12  # a bard's songs carry 8 tiles plus one per 15 skill points: 14 at 90
SONG_SKILLS = {"provoke": "Provocation", "peace": "Peacemaking", "discord": "Discordance"}


def sing(sit: Situation, ans: Answers, mem: Memory, cfg: PolicyConfig, target: Candidate, dec: Decision,
         meta: dict[str, Any], now: float) -> None:
    """A bard's turn in a fight: Jev's song when it is sure, else the rule of thumb: incite the
    strongest against another when there are two, calm one on the bard, weaken a lone one.
    One song every few seconds; a creature a song just hit is left alone for a while."""
    if now - mem.last_song < cfg.song_every:
        dec.note = f"between songs ({target.name})"
        return
    fresh = [h for h in sit.targets if now - mem.sung.get(h.serial, -1e9) > 20]
    if not fresh:
        dec.note = "the creatures are busy with each other"
        return
    choice = ans.choices.get("song")
    song = choice.choice if choice and choice.confidence >= cfg.min_song_confidence else None
    why = "jev" if song else "rule"
    if target.serial not in {h.serial for h in fresh}:
        target = max(fresh, key=lambda h: (h.max_hits, -h.distance))
    if song is None:
        song = "provoke" if len(sit.targets) >= 2 else "peace" if target.distance <= 2 else "discord"
    if song == "none":
        dec.note = "no song for now"
        return
    targets = [target.serial]
    if song == "provoke":
        onto = pick_onto(sit, ans, target)
        if onto is None:
            song = "peace" if target.distance <= 2 else "discord"
        else:
            targets.append(onto.serial)
    mem.last_song = now
    for t in targets:
        mem.sung[t] = now
    dec.actions.append({"verb": "skill", "name": SONG_SKILLS[song], "targets": targets, **meta, "reason": f"song {why}"})
    dec.note = f"{SONG_SKILLS[song].lower()} on {target.name}" + (f" against {sit.hostile_by_serial(targets[1]).name}"
                                                                    if len(targets) > 1 else "") + f" ({why})"


def pick_onto(sit: Situation, ans: Answers, incited: Candidate) -> Candidate | None:
    """Whom the incited creature should attack: Jev's pick, else the nearest other creature."""
    others = [h for h in sit.targets if h.serial != incited.serial]
    if not others:
        return None
    choice = ans.choices.get("onto")
    if choice:
        for cid, _ in sorted(choice.probabilities.items(), key=lambda kv: -kv[1]):
            c = sit.hostile(cid)
            if c is not None and c.serial != incited.serial and c.allowed:
                return c
    return min(others, key=lambda h: h.distance)


def tend_pet(sit: Situation, dec: Decision, mem: Memory, cfg: PolicyConfig, now: float) -> None:
    """A tamer bandages its pet when it is hurt and within reach, whatever else it is doing
    (not while running), and walks over to it when it is hurt a little further off."""
    pet = sit.pet
    if not pet or dec.intent in ("flee", "leave"):
        return
    moving = any(a["verb"] in ("walk_to", "flee", "kite") for a in dec.actions)
    # Stay together: a pet far off can't be bandaged and is lost once out of sight.
    if pet["distance"] > 7 and not moving and sit.authority("move") == "auto" \
            and not any(h.distance <= 1 for h in sit.hostiles):
        p = sit.player
        dec.actions.append({"verb": "walk_to", "x": p["x"] + pet["dx"], "y": p["y"] + pet["dy"], "distance": 2,
                            "confidence": 1.0, "reason": "pet"})
        dec.note += "; back to the pet"
        return
    if sit.pet_pct >= cfg.pet_heal_below:
        return
    if now - mem.last_pet_bandage < 3 or sit.authority("heal") == "off":
        return
    # A mage-tamer heals from a distance; queued after any attack spell, so it goes first.
    if sit.is_mage and sit.can_cast(GREATER_HEAL) and pet["distance"] <= 10:
        mem.last_pet_bandage = now
        dec.actions.append({"verb": "cast", "spell": GREATER_HEAL, "target": pet["serial"], "queue": True,
                            "confidence": 1.0, "reason": "pet"})
        dec.note += f"; heal the pet ({sit.pet_pct}%)"
        return
    if sit.player.get("supplies", {}).get("bandages", 0) <= 0 or sit.agent.get("bandaging"):
        return
    if pet["distance"] <= 2:
        mem.last_pet_bandage = now
        dec.actions.append({"verb": "bandage", "target": pet["serial"], "confidence": 1.0, "reason": "pet"})
        dec.note += f"; bandage the pet ({sit.pet_pct}%)"
    elif pet["distance"] <= 10 and sit.authority("move") == "auto" and not any(h.distance <= 1 for h in sit.hostiles) \
            and not moving:
        p = sit.player
        dec.actions.append({"verb": "walk_to", "x": p["x"] + pet["dx"], "y": p["y"] + pet["dy"], "distance": 1,
                            "confidence": 1.0, "reason": "pet"})
        dec.note += f"; go to the hurt pet ({sit.pet_pct}%)"


def set_pet_on(sit: Situation, dec: Decision, target: Candidate, meta: dict[str, Any]) -> tuple[Candidate, str]:
    """Send the pet at the target, or at whatever is on the tamer itself, which comes first: the
    tamer can't take many hits, and stepping back would only take it away from its pet."""
    on_me = [h for h in sit.targets if h.distance <= 1 or attacking_me(sit, h)]
    pet_on = next((h for h in sit.hostiles if h.serial == sit.agent.get("pet_target", 0)), None)
    if on_me and (pet_on is None or pet_on not in on_me) and target not in on_me:
        target = dec.target = min(on_me, key=lambda h: h.distance)
    if sit.agent.get("pet_target", 0) != target.serial:
        dec.actions.append({"verb": "pet", "kind": "kill", "target": target.serial, **meta})
        return target, f"set the pet on {target.name}"
    return target, f"pet fighting {target.name}"


def attacking_me(sit: Situation, h: Candidate) -> bool:
    raw = next((m for m in sit.raw.get("mobiles", []) if m["serial"] == h.serial), {})
    return bool(raw.get("attacking_me"))


def shooting_range(sit: Situation, cfg: PolicyConfig) -> int:
    return max(1, min(cfg.bow_range, sit.ranged.get("range", cfg.bow_range)))


def pick_target(sit: Situation, ans: Answers, cfg: PolicyConfig) -> tuple[Candidate | None, float | None]:
    """Jev's pick when it is confident, otherwise the strategy's priority."""
    choice = ans.choices.get("target")
    targets = sit.targets
    if choice and choice.choice != "none" and choice.confidence >= cfg.min_target_confidence:
        if (c := sit.hostile(choice.choice)) is not None and c.allowed:
            return c, choice.confidence
    # "None of these" is an answer too, unless something is on the character already: taken as no
    # answer, it sent a warrior at the idle wisp Jev had said to leave (none 0.56).
    if choice and choice.choice == "none" and choice.confidence >= max(cfg.min_target_confidence, 0.5) \
            and not any(h.distance <= 1 and h.info.get("aggressive") for h in targets):
        return None, choice.confidence
    if not targets:
        return None, None
    # In combat assist the player's own target comes first.
    current = next((h for h in targets if h.info.get("the_players_target")), None) \
        or next((h for h in targets if h.info["your_current_target"]), None)
    hp = lambda h: 100 if h.hits_pct is None else h.hits_pct  # noqa: E731
    match cfg.target_priority:
        case "weakest_first":
            return min(targets, key=lambda h: (hp(h), h.distance)), None
        case "strongest_first":
            # The most dangerous: the creature with the most to it, then the healthiest.
            return max(targets, key=lambda h: (h.max_hits, hp(h), -h.distance)), None
        case "closest_first":
            return min(targets, key=lambda h: h.distance), None
    return current or min(targets, key=lambda h: h.distance), None


def pick_spell(sit: Situation, ans: Answers, cfg: PolicyConfig,
               target: Candidate) -> tuple[Candidate | None, float | None, str]:
    """(spell, Jev's confidence, why). A spell the player's strategy names comes first: the
    opener while nothing has been cast at the target, then the main spell. Otherwise Jev's
    pick when it is confident ("none" means hold), else the strongest spell castable now.
    Candidates are already limited to spells with mana and reagents."""
    planned = (cfg.opening_spell if target.casts == 0 else "") or cfg.main_spell
    if planned and (c := next((s for s in sit.spells if s.name == planned), None)):
        return c, None, "strategy"
    choice = ans.choices.get("spell")
    if choice and choice.confidence >= cfg.min_spell_confidence:
        if choice.choice == "none":
            return None, choice.confidence, "jev"
        if (c := sit.spell(choice.choice)) is not None:
            return c, choice.confidence, "jev"
    damage = [c for c in sit.spells if c.name not in ("Poison", "Paralyze")]
    return (damage[0] if damage else None), None, "fallback"


def pick_corpse(sit: Situation, ans: Answers) -> Candidate | None:
    choice = ans.choices.get("corpse")
    if choice and choice.choice != "none" and (c := sit.corpse(choice.choice)):
        return c
    return min(sit.corpses, key=lambda c: c.distance) if sit.corpses else None


def take_items(sit: Situation, ans: Answers, mem: Memory, cfg: PolicyConfig, meta: dict) -> list[dict[str, Any]]:
    out = []
    for it in sit.items:
        p = ans.nouls.get(f"take_{it.id}")
        if it.serial in mem.skip_items or p is None:
            continue
        if p >= cfg.take_item:
            mem.taken.add(it.serial)
            out.append({"verb": "take", "target": it.serial, **meta})
        else:
            mem.declined.add(it.serial)
    return out
