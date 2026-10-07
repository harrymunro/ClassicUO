"""Turn Jev's answers into actions. Code owns this: it masks options that the
facts rule out, gates on confidence, and only sends actions the client can do."""

import time
from dataclasses import dataclass, field
from typing import Any

from .judge import Answers, ChoiceResult
from .spells import MEDITATION, PROTECTION
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
    reach = SPELL_RANGE if sit.is_mage else shooting_range(sit, cfg) if sit.is_archer else cfg.close_tiles
    # Whatever walks needs moving allowed: combat assist never moves the character.
    move = sit.authority("move") != "off"
    within_reach = any(c.distance <= 2 for c in sit.corpses) or bool(sit.items)
    valid = {
        # A bow without arrows shoots nothing.
        "fight": bool(sit.targets) and not (sit.is_archer and sit.ammo <= 0),
        "flee": bool(close) and cfg.allow_flee and move,
        "loot": bool(sit.corpses or sit.items) and not close and cfg.looting != "nothing"
                and sit.authority("loot") != "off" and (move or within_reach) and not sit.traveling,
        "seek": bool(sit.targets) and not any(h.distance <= reach for h in sit.hostiles) and move
                and not sit.traveling,
        "leave": bool(sit.hostiles) and cfg.allow_flee and move,
        "rest": True,
    }
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

    # Leaving: keep running from whatever is still in sight, then stay clear for a while.
    if mem.leaving_until > now and sit.authority("move") == "auto":
        near = [h for h in sit.hostiles if h.distance <= 14]
        if not near:
            return Decision("leave", 1.0, [], "left: nothing in sight", masked)
        if sit.agent.get("fleeing"):
            return Decision("leave", 1.0, [], "leaving", masked)
        nearest = min(near, key=lambda h: h.distance)
        return Decision("leave", 1.0, [{"verb": "flee", "target": nearest.serial, "tiles": 15, "confidence": 1.0,
                                        "reason": "leave"}], f"leaving, away from {nearest.name}", masked, target=nearest)

    # A cautious strategy gets out early: badly hurt with several creatures on the character.
    outmatched = any(str(h.info.get("strength", "")).startswith("far stronger") for h in sit.hostiles)
    low = str(sit.state["you"].get("supplies", "")).startswith("nearly gone")
    if cfg.allow_flee and cfg.flee_danger <= 0.45 and sit.hp_pct < 45 and len(close) >= 2 \
            and sit.authority("move") == "auto" and intent != "leave":
        intent, conf = "leave", 1.0
    # Jev's yes/no on leaving, asked when there is a reason to: it decides, not the intent vote.
    # A cautious strategy leaves on weaker signals; relentless ones never (allow_flee off).
    if ans.nouls.get("leave_now", 0.0) >= max(0.5, cfg.flee_danger) and cfg.allow_flee \
            and sit.authority("move") == "auto" and not sit.assisting:
        intent, conf = "leave", ans.nouls["leave_now"]
    # Leaving needs a reason the facts back up, as fleeing needs the danger judgment. A stored
    # fact Jev picked for this place counts when Jev's own yes/no on leaving agrees.
    known = bool(sit.known) and ans.nouls.get("leave_now", 0.0) >= max(0.5, cfg.flee_danger)
    if intent == "leave" and not (danger >= cfg.flee_danger or outmatched or low and len(close) >= 2 or known):
        intent = "fight" if close else "rest"

    # Combat assist never walks the character, so when it would flee it tells the player instead.
    hints: list[dict[str, Any]] = []
    wanted_flee = ans.choices["intent"].choice == "flee" and danger >= cfg.flee_danger \
        or danger >= cfg.panic_danger and sit.hp_pct < 25
    if sit.assisting and close and wanted_flee and cfg.allow_flee and mem.hint_due("danger", now):
        hints.append({"verb": "hint", "text": "this fight is going badly, get out", "reason": "danger"})
    if sit.assisting and sit.is_archer and sit.ammo <= 0 and sit.hostiles and mem.hint_due("ammo", now, 20):
        hints.append({"verb": "hint", "text": f"out of {sit.ranged.get('ammo', 'arrows')}", "reason": "ammo"})

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
            dec.note = f"fight {target.name}" + (f" with {dec.spell.name}" if dec.spell else "") + f" ({conf:.2f})"
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
            if target.serial != engaged:
                actions.append({"verb": "attack", "target": target.serial, **meta})
            dec.note = f"{'fight' if target.serial != engaged else 'keep fighting'} {target.name} ({conf:.2f})"

    elif intent == "leave":
        mem.leaving_until = now + 40
        threat = max(sit.hostiles, key=lambda h: (str(h.info.get("strength", "")).startswith("far"), -h.distance))
        dec.target = threat
        actions.append({"verb": "flee", "target": threat.serial, "tiles": 15, **meta})
        dec.note = f"leave, away from {threat.name} ({conf:.2f})"

    elif intent == "flee":
        threat = min(close, key=lambda h: h.distance)
        dec.target = threat
        actions.append({"verb": "flee", "target": threat.serial, "tiles": 10, **meta})
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
        raw = next(m for m in sit.raw["mobiles"] if m["serial"] == target.serial)
        p = sit.player
        stop = cfg.spell_range - 1 if sit.is_mage else shooting_range(sit, cfg) - 1 if sit.is_archer else 1
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


def shooting_range(sit: Situation, cfg: PolicyConfig) -> int:
    return max(1, min(cfg.bow_range, sit.ranged.get("range", cfg.bow_range)))


def pick_target(sit: Situation, ans: Answers, cfg: PolicyConfig) -> tuple[Candidate | None, float | None]:
    """Jev's pick when it is confident, otherwise the strategy's priority."""
    choice = ans.choices.get("target")
    targets = sit.targets
    if choice and choice.choice != "none" and choice.confidence >= cfg.min_target_confidence:
        if (c := sit.hostile(choice.choice)) is not None and c.allowed:
            return c, choice.confidence
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
