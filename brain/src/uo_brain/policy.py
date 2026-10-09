"""Turn Jev's answers into actions. Code owns this: it masks options that the
facts rule out, gates on confidence, and only sends actions the client can do."""

import time
from dataclasses import dataclass, field
from typing import Any

from .judge import Answers, ChoiceResult
from .spells import (BLESSING_SPELLS, GREATER_HEAL, MEDITATION, PROTECTION, RECAST_AFTER, RECAST_AFTER_WITH_ICON,
                     is_damage)
from .questions import leave_reason
from .state import PACK_FAR, SPELL_RANGE, Candidate, Situation, around_target


@dataclass
class PolicyConfig:
    min_intent_confidence: float = 0.35
    min_target_confidence: float = 0.25
    min_spell_confidence: float = 0.3
    ward: bool = False  # a mage with a crowd coming is asked about walls and summons first (cuo-ev9)
    ward_at: float = 0.5  # ...and casts the likeliest when Jev's answers other than "none" add up to this
    ward_every: float = 15.0  # seconds after one before another: one wall a crowd, not one of each kind
    flee_danger: float = 0.5        # flee needs the intent *and* the danger judgment to agree
    panic_danger: float = 0.9       # ...unless danger is this sure and health is critical
    take_item: float = 0.6
    close_tiles: int = 3
    spell_range: int = 7            # a mage keeps its target within this many tiles
    bow_range: int = 8              # an archer shoots from this far, or its weapon's range if shorter
    meditate_below: int = 80        # mana %: a resting mage meditates below this
    avoid_players: bool = True      # auto mode leaves when a red or criminal player comes close
    kite: bool = True               # a mage or archer steps back from melee between spells or shots
    leave_packs: bool = True        # code leaves when a pack coming at the character outweighs it (pack_cut)
    # A plan's state (machine.py): the intents Jev may choose from; None for all. The floors (the
    # emergency flee, leaving from players and packs) apply whatever it allows.
    allowed_intents: tuple[str, ...] | None = None
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


HP_WINDOW_S = 6.0   # how far back a fall in health is measured
FAST_FALL = 30      # points of health lost in that time that count as falling fast (cuo-8vk)


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
    no_seek_until: float = 0.0  # ...and after it, don't go looking for creatures again until then
    last_kite: float = 0.0      # a mage's last step back from melee
    protection_asks: list[float] = field(default_factory=list)  # when Protection was last asked for
    kite_casts: int = -1        # attack spells cast by then: the next step back waits for one more
    kite_ammo: int = 1 << 30    # an archer's arrows by then: the next step back waits for a shot
    last_pet_bandage: float = -1e9  # a tamer's last bandage on its pet
    pulling_until: float = 0.0      # the pet was called back: don't send it in again until then
    last_song: float = -1e9         # a bard's last song
    last_pet_call: float = -1e9     # a tamer's last "all follow me"
    sung: dict[int, float] = field(default_factory=dict)  # creature -> when a song last hit it (provoked or calmed)
    last_blessing: float = -1e9     # a paladin's last blessing, and its Consecrate Weapon (no buff icon: it lasts 3-11 s)
    last_consecrate: float = -1e9
    consecrated_at: int = 0         # the creature it was cast for
    flee_failed: int = 0            # flee actions in a row the client couldn't do ("no path away")
    cornered_until: float = 0.0     # surrounded: code doesn't try to leave again until then
    blessing_action: dict[str, Any] = field(default_factory=dict)  # the last blessing cast, for the decision
    # (creature, or 0, spell) -> when it may be offered again: a curse, field, summon or blessing on
    # the caster lasts a while, and a cast nothing confirms isn't repeated at once (cuo-ryt).
    recast: dict[tuple[int, str], float] = field(default_factory=dict)
    last_ward: float = -1e9  # when a wall or summon was last cast on Jev's ward answer (cuo-ev9)
    hp_trail: list[tuple[float, int]] = field(default_factory=list)  # (when, health %) over the last seconds

    def cooling(self, now: float) -> set[tuple[int, str]]:
        """The (creature or 0, spell) pairs state.build shouldn't offer now."""
        return {k for k, until in self.recast.items() if until > now}

    def hint_due(self, key: str, now: float, every: float = 12.0) -> bool:
        if now - self.hinted.get(key, -1e9) < every:
            return False
        self.hinted[key] = now
        return True

    @property
    def skip_items(self) -> set[int]:
        return self.taken | self.declined

    def health_drop(self, hp: int, now: float, window: float = HP_WINDOW_S) -> int:
        """Health (percent) lost over the last `window` seconds: the highest it was since, less now.
        Healing in between doesn't hide a fall that came after it."""
        self.hp_trail = [(t, h) for t, h in self.hp_trail if now - t <= window] + [(now, hp)]
        return max(0, max(h for _, h in self.hp_trail) - hp)

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
    leave_why: str = ""  # why it is leaving the area, in words, when intent is "leave"

    @property
    def next_move(self) -> dict[str, Any] | None:
        """The best combat move in this decision, for the client's next-move key."""
        return next((a for a in self.actions if a["verb"] in ("cast", "attack")), None)


CHASE_TILES = 8  # nearly dead, an aggressive creature this close is still after the character


def fighting_reach(sit: Situation, cfg: PolicyConfig) -> int:
    """How far off the character fights from where it stands: spells, arrows, a pet, songs, or a
    weapon's few tiles."""
    return SPELL_RANGE if sit.is_mage else shooting_range(sit, cfg) if sit.is_archer \
        else PET_REACH if sit.is_tamer else BARD_REACH if sit.is_bard else cfg.close_tiles


def chasers(sit: Situation, cfg: PolicyConfig) -> list[Candidate]:
    """What there is to run from: anything close and, nearly dead, anything aggressive within
    CHASE_TILES. After a 10-tile flee the creature run from is "nearby", not close; with fleeing
    then off the menu, a warrior at 11% walked back to fight the gargoyle it had fled (cuo-d28.10)."""
    return [h for h in sit.hostiles if h.distance <= cfg.close_tiles
            or sit.hp_pct < 25 and h.distance <= CHASE_TILES and h.info.get("aggressive")]


def masked_intent(sit: Situation, answer: ChoiceResult, cfg: PolicyConfig) -> tuple[str, float, list[str]]:
    close = [h for h in sit.hostiles if h.distance <= cfg.close_tiles]
    # A warrior seeks anything not yet close; a mage or archer only what it cannot reach.
    reach = fighting_reach(sit, cfg)
    # Whatever walks needs moving allowed: combat assist never moves the character.
    move = sit.authority("move") != "off"
    within_reach = any(c.distance <= 2 for c in sit.corpses) or bool(sit.items)
    valid = {
        # A bow without arrows shoots nothing; a tamer without its pet, or a bard without an
        # instrument, has nothing to fight with.
        "fight": bool(sit.targets) and not (sit.is_archer and sit.ammo <= 0) and not (sit.is_tamer and not sit.pet)
        and not (sit.is_bard and not sit.player.get("supplies", {}).get("instrument")),
        "flee": bool(chasers(sit, cfg)) and cfg.allow_flee and move,
        "loot": bool(sit.corpses or sit.items) and not close and cfg.looting != "nothing"
                and sit.authority("loot") != "off" and (move or within_reach) and not sit.traveling,
        "seek": bool(sit.targets) and not any(h.distance <= reach for h in sit.hostiles) and move
                and not sit.traveling,
        "leave": bool(sit.hostiles) and cfg.allow_flee and move,
        "rest": True,
    }
    if cfg.defend_only:
        valid["seek"] = valid["loot"] = False
    if cfg.allowed_intents is not None:
        # A plan's state limits the choice; something on the character is still fought back
        # when the state gives no way to get away from it.
        on_me = any(h.distance <= 1 and h.info.get("aggressive") for h in sit.hostiles)
        away = any(k in cfg.allowed_intents and valid[k] for k in ("flee", "leave"))
        for k in valid:
            if k != "rest" and k not in cfg.allowed_intents and not (k == "fight" and on_me and not away):
                valid[k] = False
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
        # Only what is attacking: in war mode close by, or hitting the character. "Anything close"
        # had a soak run's warrior chase a crossbill while its walk waited.
        for h in sit.hostiles:
            h.allowed = h.allowed and (bool(h.info.get("aggressive")) and h.distance <= 6 or attacking_me(sit, h))
    if now < mem.no_seek_until:
        # After leaving, only what is close or attacking is fought: going after one further off took a
        # warrior in a pack round back to the gargoyles it had left, as seeking would have. What comes
        # at the character within the reach it fights at from where it stands is fought too: held to
        # 3 tiles, a mage in swarm rounds stood resting until the monsters were on it.
        reach = fighting_reach(sit, cfg)
        for h in sit.hostiles:
            h.allowed = h.allowed and (h.distance <= cfg.close_tiles or attacking_me(sit, h)
                                       or h.distance <= reach and bool(h.info.get("aggressive")))
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
    chasing = chasers(sit, cfg)
    # Falling fast, a little less sureness will do: a lone gargoyle took a warrior from 58% to 20% in a
    # second, and it died at danger 0.86-0.87, under the 0.9 (cuo-8vk).
    panic = danger >= cfg.panic_danger or danger >= cfg.panic_danger - 0.1 and sit.hp_drop >= FAST_FALL
    if cfg.allow_flee and intent != "flee" and chasing and panic and sit.hp_pct < 25 \
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
    # Cornered: every way out is blocked (in swarm rounds a mage kept "leaving" for 40 s, each flee
    # failing, while six monsters hit it). Fight instead, and don't try to leave for a while.
    # Only once something has reached it, though: in a soak run a warrior that had left three gargoyles
    # three times found no path twice by the graveyard wall with them still far off, was held in the
    # fight for 15 s while Jev said leave at 0.8-0.9, and died (cuo-y4l). With nothing close it keeps
    # leaving, straight away from the nearest instead of from them all, which is another heading;
    # four failures in a row count as cornered all the same.
    if mem.flee_failed >= 2 and (any(h.distance <= cfg.close_tiles for h in sit.hostiles) or mem.flee_failed >= 4):
        mem.flee_failed, mem.leaving_until, mem.cornered_until = 0, 0.0, now + 15
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
        if keep_travelling(sit):
            return Decision("leave", 1.0, call, "leaving by the road: the trip goes on", masked, target=nearest)
        tiles = 8 if sit.is_tamer else 15
        # From them all, weighed by the client, when there are two or more; after runs that found no
        # path, from the nearest alone, which points another way.
        away = nearest.serial if mem.flee_failed >= 2 else flee_from(sit, nearest)
        return Decision("leave", 1.0, call + [{"verb": "flee", "target": away, "tiles": tiles, "confidence": 1.0,
                                               "reason": "leave"}],
                        f"leaving, away from {away_words(sit, nearest)}", masked, target=nearest)

    # A cautious strategy gets out early: badly hurt with several creatures on the character.
    outmatched = any(str(h.info.get("strength", "")).startswith("far stronger") for h in sit.hostiles)
    low = str(sit.state["you"].get("supplies", "")).startswith("nearly gone")
    # A mage or archer with four or more on it is outnumbered: a reason to leave, as low supplies are,
    # unless an area spell can hit them where they stand (cuo-cvl.4), or every way out is blocked.
    cornered = now < mem.cornered_until
    area_ready = bool(sit.area_center and sit.area_spells)
    outnumbered = (sit.is_mage or sit.is_archer) and len(close) >= 4 and not area_ready and not cornered
    why_leave = ""  # the code rule that chose to leave, in words, for the hunt's result and the log
    if cfg.allow_flee and cfg.flee_danger <= 0.45 and sit.hp_pct < 45 and len(close) >= 2 \
            and sit.authority("move") == "auto" and intent != "leave":
        intent, conf = "leave", 1.0
        why_leave = f"badly hurt ({sit.hp_pct}%) with {len(close)} close, and the strategy is cautious"
    # Two or more creatures stronger than the character on it, and Jev judging it in danger: in a
    # soak run two bone knights took a warrior from 100% to 25% in 10 s while Jev's intent stayed
    # on fighting (danger 0.88).
    strong_close = [h for h in close if str(h.info.get("strength", "")).startswith(("far stronger", "stronger"))]
    if len(strong_close) >= 2 and danger >= cfg.flee_danger and cfg.allow_flee and sit.authority("move") == "auto" \
            and not sit.assisting and intent != "leave":
        intent, conf = "leave", danger
        why_leave = f"{len(strong_close)} stronger creatures close: " + ", ".join(h.name for h in strong_close[:3])
    # A pack coming at the character that together outweighs it: leave while it is still coming
    # (cuo-d28.9). Leaving once it was on the character came too late, since most monsters run as
    # fast as a character: two gargoyles and a reaper, "a fair fight" each, killed a warrior in a
    # soak run after Jev's intent stayed on fighting (leave 0.39-0.44).
    pack = cfg.leave_packs and len(sit.pack) >= 2 and sit.pack_weight >= pack_cut(cfg) and not cornered
    if pack and cfg.allow_flee and sit.authority("move") == "auto" and not sit.assisting and intent != "leave":
        intent, conf = "leave", 1.0
    # Code's call for a mage or archer with four or more on it once Jev judges it in danger: its
    # spells or shots are interrupted by every hit, and Jev, seeing each creature as "an easy
    # kill", put only 0.2-0.4 on leaving in swarm rounds that ended in death (2026-10-07).
    if outnumbered and len([h for h in close if h.distance <= 1]) >= 4 and danger >= 0.5 and cfg.allow_flee \
            and sit.authority("move") == "auto" and not sit.assisting and intent != "leave":
        intent, conf = "leave", danger
        why_leave = f"{len([h for h in close if h.distance <= 1])} on the {sit.archetype}, who is weak in melee"
    # Jev's yes/no on leaving, asked when there is a reason to: it decides, not the intent vote.
    # A cautious strategy leaves on weaker signals; relentless ones never (allow_flee off).
    leave_allowed = cfg.allowed_intents is None or "leave" in cfg.allowed_intents
    if ans.nouls.get("leave_now", 0.0) >= leave_cut(cfg) and cfg.allow_flee and leave_allowed \
            and sit.authority("move") == "auto" and not sit.assisting and not cornered:
        intent, conf = "leave", ans.nouls["leave_now"]
    # Leaving needs a reason the facts back up, as fleeing needs the danger judgment. A stored
    # fact Jev picked for this place counts when Jev's own yes/no on leaving agrees, and so does
    # having nothing to fight with (no pet in sight, no arrows, no instrument).
    known = bool(sit.known) and ans.nouls.get("leave_now", 0.0) >= leave_cut(cfg)
    # Jev's own yes on leaving counts with whatever reason it was asked for: checking only for
    # stronger creatures that are close overruled it (0.43-0.45) while two bone knights came on.
    asked = ans.nouls.get("leave_now", 0.0) >= leave_cut(cfg) and bool(leave_reason(sit))
    cannot_fight = bool(sit.targets) and "fight" in masked
    if intent == "leave" and not (danger >= cfg.flee_danger or outmatched or low and len(close) >= 2 or known
                                  or cannot_fight or outnumbered or len(strong_close) >= 2 or asked or pack):
        intent = "fight" if close and "fight" not in masked else "rest"
    # Cornered, Jev's own pick of leaving or fleeing fails as code's did: in a pack round a warrior
    # tried nine times in a row ("no path away") while three gargoyles closed in, and died standing.
    if cornered and intent in ("leave", "flee"):
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

    note_rest = ""
    if intent == "seek" and now < mem.no_seek_until:
        intent, conf = "rest", 1.0
        note_rest = "not going back after leaving"
    # Seek only what Jev would attack: with its target answer "none of these", seeking the nearest
    # anyway walked a warrior to the wraith it had said to leave (cuo-8ga).
    seek_target, seek_conf = pick_target(sit, ans, cfg) if intent == "seek" else (None, None)
    if intent == "seek" and seek_target is None:
        intent = "rest"
        note_rest = f"not seeking: Jev would attack none of these ({seek_conf or 0:.2f})"

    if conf < cfg.min_intent_confidence:
        dec = Decision(intent, conf, hints, f"unsure ({conf:.2f}), keeping course", masked, gated=True)
        # Keeping course for a mage in a fight means casting again: the client does not
        # cast on its own the way a warrior's swings continue.
        current = next((h for h in sit.hostiles if h.serial == engaged), None)
        if sit.is_mage and current and current.distance <= SPELL_RANGE:
            dec.target = current
            dec.spell, dec.spell_confidence, dec.spell_why = pick_spell(sit, ans, cfg, current)
            if dec.spell:
                dec.actions.append(cast_action(sit, mem, dec.spell, current, now, confidence=round(conf, 3),
                                               reason="keep course"))
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
            # Not with an area spell to cast: they are where it hits them all.
            if cfg.kite and len(adjacent) >= 2 and not (sit.raw.get("magic") or {}).get("casting") and not area_ready \
                    and sit.authority("move") == "auto" and now - mem.last_kite > 3 and attack_casts > mem.kite_casts:
                mem.last_kite, mem.kite_casts = now, attack_casts
                actions.append({"verb": "kite", "tiles": 5, **meta, "reason": "kite"})
            # With creatures in melee reach every hit interrupts a spell, unless Protection is up.
            # That is AOS: before it Protection only adds armour, and servers send no buff icons.
            # Raised as soon as melee creatures are coming, not once they arrive: in a swarm round
            # cast with one already adjacent, it and every spell after it were broken (cuo-cvl.4).
            # It is a toggle, so it is never cast while its buff icon shows.
            coming = close or any(h.info.get("aggressive") and h.distance <= 10 and not h.info.get("casts_spells")
                                  for h in sit.hostiles)
            # Asked for again until its icon shows (a hit can break the cast), but no more than three
            # times in 20 s: a server that sends no icon would otherwise have it toggled off again.
            mem.protection_asks = [t for t in mem.protection_asks if now - t < 20]
            if coming and sit.raw.get("era", "aos") == "aos" and PROTECTION not in sit.player.get("buffs", []) \
                    and sit.can_cast(PROTECTION) and len(mem.protection_asks) < 3 \
                    and (not mem.protection_asks or now - mem.protection_asks[-1] >= 1.5):
                mem.protection_asks.append(now)
                actions.append({"verb": "cast", "spell": PROTECTION, "target": "self", "queue": True,
                                **meta, "reason": "protection"})
                dec.note = f"fight {target.name}, Protection first ({conf:.2f})"
                return dec
            # Queued: the client casts it the moment the current spell and recovery allow. A wall or a
            # summon first when Jev says so with a crowd still coming (cuo-ev9).
            if now - mem.last_ward >= cfg.ward_every and (w := pick_ward(sit, ans, cfg)) is not None:
                mem.last_ward = now
                dec.spell, dec.spell_confidence, dec.spell_why = w, 1.0 - ans.choices["ward"].probabilities.get(
                    "none", 0.0), "ward"
            elif target.distance <= SPELL_RANGE:
                dec.spell, dec.spell_confidence, dec.spell_why = pick_spell(sit, ans, cfg, target)
            elif sit.assisting and mem.hint_due(f"range{target.serial}", now, 8):
                actions.append({"verb": "hint", "text": f"{target.name} is out of spell range", "reason": "range"})
            if dec.spell:
                actions.append(cast_action(sit, mem, dec.spell, target, now, **meta))
            dec.note = (f"{pet_note}, " if pet_note else "") + f"fight {target.name}" \
                + (f" with {dec.spell.name}" if dec.spell else "") \
                + (f" on {dec.spell.info.get('hits', sit.area_count)}" if dec.spell and dec.spell.info.get("area") else "") \
                + f" ({conf:.2f})"
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
                    actions.append(cast_action(sit, mem, dec.spell, target, now, **meta, opener=True))
            if target.serial != engaged:
                actions.append({"verb": "attack", "target": target.serial, **meta})
            blessed = bless(sit, ans, mem, cfg, target, meta, now) if sit.is_paladin else ""
            dec.note = f"{'fight' if target.serial != engaged else 'keep fighting'} {target.name}" \
                + (f", opening with {dec.spell.name}" if dec.spell else "") + (f", {blessed}" if blessed else "") \
                + f" ({conf:.2f})"
            if blessed:
                actions.append(mem.blessing_action)

    elif intent == "leave":
        mem.leaving_until = now + 40
        # Not back to where it ran from: in a pack round the warrior got away untouched, then went
        # after the orc it had left once the 40 s were up and walked into the gargoyles again.
        mem.no_seek_until = mem.leaving_until + 120
        threat = max(sit.hostiles, key=lambda h: (str(h.info.get("strength", "")).startswith("far"), -h.distance))
        dec.target = threat
        dec.leave_why = (f"{len(sit.pack)} coming at once: {sit.state['coming_at_you']}" if pack
                         else why_leave or leave_reason(sit) or f"Jev: leave ({conf:.2f})")
        if sit.is_tamer and sit.pet:
            mem.last_pet_call = now
            actions.append({"verb": "pet", "kind": "follow", **meta})
        if keep_travelling(sit):
            dec.note = f"leave by the road: the trip goes on ({conf:.2f})"
        else:
            # Away from all of them (the client weighs every creature in view), whichever rule chose
            # to leave: Jev's leave_now with an orc adjacent ran from the orc alone, 15 tiles north
            # into three gargoyles, and the warrior died there (cuo-d28.11).
            actions.append({"verb": "flee", "target": flee_from(sit, threat), "tiles": 8 if sit.is_tamer else 15,
                            **meta})
            dec.note = f"leave, away from {away_words(sit, threat)} ({conf:.2f})"

    elif intent == "flee":
        # From what is after the character: a timber wolf a few steps off that wasn't once took the
        # run's direction from the gargoyle that was.
        threat = min(chasing, key=lambda h: (not h.info.get("aggressive"), h.distance))
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
        target, dec.target_confidence = seek_target, seek_conf
        dec.target = target
        # A warrior-mage within spell range opens with a spell and lets the creature come: in a
        # calibration run it walked up to orcs 5 tiles off and never cast its opener.
        if sit.is_warrior_mage and target.casts == 0 and 2 <= target.distance <= SPELL_RANGE:
            dec.spell, dec.spell_confidence, dec.spell_why = pick_spell(sit, ans, cfg, target)
            if dec.spell:
                actions.append({"verb": "attack", "target": target.serial, **meta})
                actions.append(cast_action(sit, mem, dec.spell, target, now, **meta, opener=True))
                dec.note = f"open on {target.name} with {dec.spell.name} ({conf:.2f})"
                return dec
        raw = next(m for m in sit.raw["mobiles"] if m["serial"] == target.serial)
        p = sit.player
        stop = cfg.spell_range - 1 if sit.is_mage else shooting_range(sit, cfg) - 1 if sit.is_archer \
            else 5 if sit.is_tamer else 8 if sit.is_bard else 1
        actions.append({"verb": "walk_to", "x": p["x"] + raw["dx"], "y": p["y"] + raw["dy"], "distance": stop, **meta})
        dec.note = f"seek {target.name} ({conf:.2f})"

    else:
        dec.note = note_rest or f"rest ({conf:.2f})"
        # A mage regains mana faster in a trance; the skill has a 10 s delay of its own.
        if sit.is_mage and sit.mana_pct < cfg.meditate_below and not close \
                and "ActiveMeditation" not in sit.player.get("buffs", []) and now - mem.last_meditate > 10:
            mem.last_meditate = now
            actions.append({"verb": "skill", "name": MEDITATION, **meta})
            dec.note = f"meditate ({conf:.2f})"

    return dec


def flee_from(sit: Situation, one: Candidate) -> int:
    """What a leave runs from: 0, every creature in view (the client weighs them), when there are two
    or more; else the one."""
    return 0 if len(sit.hostiles) >= 2 else one.serial


def away_words(sit: Situation, one: Candidate) -> str:
    return f"all {len(sit.hostiles)} in sight" if flee_from(sit, one) == 0 else one.name


def keep_travelling(sit: Situation) -> bool:
    """Leaving while on a trip (to a bank, say): the trip goes on, at a run, unless something within
    10 tiles is ahead on it (within 60 degrees of the way). Fleeing away from whatever was nearest
    took an open-goal soak run's warrior off its road to the bank into the wilds, where it gathered a
    dire wolf, a gazer, an ettin and an ogre, and died."""
    travel = sit.agent.get("travel") or {}
    if travel.get("state") != "walking":
        return False
    p = sit.player
    gx, gy = travel.get("x", p["x"]) - p["x"], travel.get("y", p["y"]) - p["y"]
    g = (gx * gx + gy * gy) ** 0.5
    if g < 3:
        return False
    for m in sit.raw.get("mobiles", []):
        if not m.get("monster") or m.get("dead") or m["distance"] > 10:
            continue
        hx, hy = m.get("dx", 0), m.get("dy", 0)
        h = (hx * hx + hy * hy) ** 0.5 or 1.0
        if (gx * hx + gy * hy) / (g * h) > 0.5:
            return False
    return True


NEUTRAL_FLEE_DANGER = 0.625  # what strategy.apply sets for a neutral strategy, and with no text at all


def pack_cut(cfg: PolicyConfig) -> float:
    """What a pack coming at the character must weigh, in fair fights, before code leaves: 2.5 with
    no strategy (three fair fights at once, two stronger creatures, an ogre lord and anything else),
    less for a cautious strategy, more for an aggressive one."""
    return min(5.0, max(1.8, PACK_FAR + 4 * (cfg.flee_danger - NEUTRAL_FLEE_DANGER)))


def bless(sit: Situation, ans: Answers, mem: Memory, cfg: PolicyConfig, target: Candidate, meta: dict[str, Any],
          now: float) -> str:
    """A paladin's blessing for the fight at hand (cuo-cvl.5): Jev's pick when it is sure, else the
    rule of thumb: Holy Light with three or more on it, Divine Fury for several or a strong one,
    Enemy of One for a strong one, else Consecrate Weapon, renewed as it wears off. One every 2 s at
    most. Sets mem.blessing_action and returns what it does in words, or "" for none."""
    if not sit.blessings or target.distance > cfg.close_tiles or now - mem.last_blessing < 2.0:
        return ""
    buffs = sit.player.get("buffs", [])
    crowd, n = around_target(sit.raw, sit.hostiles, 3, 4)
    # Consecrate Weapon lasts seconds and shows no icon: renewed on a new target, or after 20 s. The
    # longer blessings only with a quarter of the mana left, which also pays for Close Wounds.
    fresh = target.serial != mem.consecrated_at
    mana = sit.mana_pct >= 25
    ready = {"consecrate": fresh or now - mem.last_consecrate > 20, "divine_fury": "DivineFury" not in buffs and mana,
             "enemy_of_one": "EnemyOfOne" not in buffs and mana, "holy_light": crowd is not None, "none": True}
    usable = {k for k, ok in ready.items() if ok and (k == "none" or k in sit.blessings)}
    choice = ans.choices.get("blessing")
    pick, why = (choice.choice, "jev") if choice and choice.confidence >= cfg.min_spell_confidence \
        and choice.choice in usable else (None, "rule")
    if pick is None:
        strong = str(target.info.get("strength", "")).startswith(("stronger", "far stronger"))
        several = sum(1 for h in sit.hostiles if h.distance <= cfg.close_tiles) >= 2
        pick = next((k for k, want in (("holy_light", True), ("divine_fury", several or strong),
                                         ("enemy_of_one", strong), ("consecrate", True)) if want and k in usable), "none")
    if pick == "none":
        return ""
    mem.last_blessing = now
    if pick == "consecrate":
        mem.last_consecrate, mem.consecrated_at = now, target.serial
    spell = BLESSING_SPELLS[pick]
    # Holy Light hits all around; the client wants a creature to cast a harmful spell at.
    mem.blessing_action = {"verb": "cast", "spell": spell, "target": crowd.serial if pick == "holy_light" else "self",
                           "queue": True, **meta, "reason": f"blessing {why}"}
    return f"{spell}" + (f" on {n}" if pick == "holy_light" else "") + f" ({why})"


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
    hp = lambda h: 100 if h.hits_pct is None else h.hits_pct  # noqa: E731
    # A caster with three or more on it finishes the one with least left first: every kill is one
    # fewer hitting it and interrupting its spells (cuo-cvl.4).
    # The player's own target still comes first in combat assist.
    if sit.casts and sum(1 for h in sit.hostiles if h.distance <= cfg.close_tiles) >= 3 \
            and cfg.target_priority == "current_first" and not any(h.info.get("the_players_target") for h in targets):
        return min(targets, key=lambda h: (hp(h) * (h.max_hits or 100), h.distance)), None
    # In combat assist the player's own target comes first.
    current = next((h for h in targets if h.info.get("the_players_target")), None) \
        or next((h for h in targets if h.info["your_current_target"]), None)
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
    # Three or more close together: an area spell hits them all for less mana a creature, unless it
    # mostly fizzles (an eighth-circle one at Magery 90).
    area = [c for c in sit.area_spells if c.info.get("chance", 1.0) >= 0.5]
    if area and sit.area_count >= 3:
        return area[0], None, "fallback"
    # The strongest damage spell that works 9 times in 10: Flamestrike works 6 in 10 at Magery 90,
    # and Jev, told so, stopped picking it while this fallback still cast it (cuo-x2x).
    damage = [c for c in sit.spells if is_damage(c.name)]
    sure = [c for c in damage if c.info.get("chance", 1.0) >= 0.9]
    return ((sure or damage)[0] if damage else None), None, "fallback"


def pick_ward(sit: Situation, ans: Answers, cfg: PolicyConfig) -> Candidate | None:
    """The wall or summon to cast first, or None (cuo-ev9). The options are alike, so Jev's yes is
    spread over them: in a swarm round it put 0.77 on four fields against 0.16 on attacking, none
    of them above 0.3. So it is yes when all but "none" add up to `ward_at`, then the likeliest."""
    ward = ans.choices.get("ward")
    if not ward:
        return None
    options = {k: p for k, p in ward.probabilities.items() if k != "none"}
    if not options or sum(options.values()) < cfg.ward_at:
        return None
    return sit.spell(max(options, key=options.get))


def cast_action(sit: Situation, mem: Memory, spell: Candidate, target: Candidate, now: float, opener: bool = False,
                **meta: Any) -> dict[str, Any]:
    """The cast, aimed as the spell needs (spells.fight_spells): at the creature (a field or a placed
    summon too: the client puts it on the tile beside it, between it and the caster), where an area
    spell hits the most of them, or at the caster. Notes when it may be offered again."""
    aim = spell.info.get("aim", "creature")
    at: int | str = "self" if aim in ("self", "summon") \
        else (sit.area_center or target).serial if aim == "area" or spell.info.get("area") else target.serial
    # How many an area spell hits: Earthquake counts those around the caster, not Chain Lightning's crowd.
    reason = "opener" if opener else f"area {spell.info.get('hits', sit.area_count)}" if spell.info.get("area") \
        else meta.get("reason", "")
    if not is_damage(spell.name) and not spell.info.get("area") and aim in RECAST_AFTER:
        wait = RECAST_AFTER_WITH_ICON if aim == "self" and sit.raw.get("era", "aos") == "aos" else RECAST_AFTER[aim]
        mem.recast[(at if isinstance(at, int) and aim in ("creature", "area") else 0, spell.name)] = now + wait
    return {"verb": "cast", "spell": spell.name, "target": at, "queue": True, **meta,
            **({"reason": reason} if reason else {})}


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
