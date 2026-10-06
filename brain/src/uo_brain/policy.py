"""Turn Jev's answers into actions. Code owns this: it masks options that the
facts rule out, gates on confidence, and only sends actions the client can do."""

from dataclasses import dataclass, field
from typing import Any

from .judge import Answers, ChoiceResult
from .state import Candidate, Situation


@dataclass
class PolicyConfig:
    min_intent_confidence: float = 0.35
    min_target_confidence: float = 0.25
    flee_danger: float = 0.5        # flee needs the intent *and* the danger judgment to agree
    panic_danger: float = 0.9       # ...unless danger is this sure and health is critical
    take_item: float = 0.6
    close_tiles: int = 3
    # Set from the player's strategy (strategy.py).
    allow_flee: bool = True
    target_priority: str = "current_first"
    looting: str = "valuables"


@dataclass
class Memory:
    looted: set[int] = field(default_factory=set)
    loot_started: dict[int, float] = field(default_factory=dict)
    taken: set[int] = field(default_factory=set)
    declined: set[int] = field(default_factory=set)

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


def masked_intent(sit: Situation, answer: ChoiceResult, cfg: PolicyConfig) -> tuple[str, float, list[str]]:
    close = [h for h in sit.hostiles if h.distance <= cfg.close_tiles]
    valid = {
        "fight": bool(sit.hostiles),
        "flee": bool(close) and cfg.allow_flee,
        "loot": bool(sit.corpses or sit.items) and not close and cfg.looting != "nothing",
        "seek": bool(sit.hostiles) and not close,
        "rest": True,
    }
    masked = [k for k, ok in valid.items() if not ok and k in answer.probabilities]
    probs = {k: p for k, p in answer.probabilities.items() if valid.get(k, False)}
    total = sum(probs.values())
    if total <= 0:
        # Everything the model wanted is ruled out (e.g. flee under a never-flee strategy):
        # code decides. Cornered means fight.
        return ("fight" if close else "rest"), 1.0, masked
    best = max(probs, key=probs.get)
    # Jev's confidence describes its whole distribution. It still holds when the
    # masked options had no real weight; otherwise use the winner's renormalised share.
    if best == answer.choice and total >= 0.95:
        return best, answer.confidence, masked
    return best, probs[best] / total, masked


def decide(sit: Situation, ans: Answers, mem: Memory, cfg: PolicyConfig) -> Decision:
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
            and not potion_soon:
        intent, conf = "flee", danger

    if conf < cfg.min_intent_confidence:
        note = f"unsure ({conf:.2f}), keeping course"
        return Decision(intent, conf, [], note, masked, gated=True)

    actions: list[dict[str, Any]] = []
    meta = {"confidence": round(conf, 3), "reason": intent}

    # Items in an open corpse within reach are picked up whenever nothing is close,
    # whatever the main intent: the corpse may already count as looted.
    if not close and intent != "flee" and cfg.looting != "nothing":
        actions.extend(take_items(sit, ans, mem, cfg, {"confidence": round(conf, 3), "reason": "take"}))

    if intent == "fight":
        target = pick_target(sit, ans, cfg)
        if target is None:
            return Decision(intent, conf, [], "no target", masked)
        if target.serial != engaged:
            actions.append({"verb": "attack", "target": target.serial, **meta})
        note = f"fight {target.name} ({conf:.2f})"

    elif intent == "flee":
        threat = min(close, key=lambda h: h.distance)
        actions.append({"verb": "flee", "target": threat.serial, "tiles": 10, **meta})
        note = f"flee from {threat.name} (danger {danger:.2f})"

    elif intent == "loot":
        corpse = pick_corpse(sit, ans)
        looting = sit.agent.get("looting", 0)
        if corpse and corpse.serial != looting and corpse.serial not in mem.loot_started:
            actions.append({"verb": "loot", "target": corpse.serial, **meta})
        note = f"loot {corpse.name if corpse else 'items'} ({conf:.2f})"

    elif intent == "seek":
        target = pick_target(sit, ans, cfg) or min(sit.hostiles, key=lambda h: h.distance)
        raw = next(m for m in sit.raw["mobiles"] if m["serial"] == target.serial)
        p = sit.player
        actions.append({"verb": "walk_to", "x": p["x"] + raw["dx"], "y": p["y"] + raw["dy"], "distance": 1, **meta})
        note = f"seek {target.name} ({conf:.2f})"

    else:
        note = f"rest ({conf:.2f})"

    return Decision(intent, conf, actions, note, masked)


def pick_target(sit: Situation, ans: Answers, cfg: PolicyConfig) -> Candidate | None:
    choice = ans.choices.get("target")
    if choice and choice.choice != "none" and choice.confidence >= cfg.min_target_confidence:
        if (c := sit.hostile(choice.choice)) is not None:
            return c
    if not sit.hostiles:
        return None
    current = next((h for h in sit.hostiles if h.info["your_current_target"]), None)
    hp = lambda h: 100 if h.hits_pct is None else h.hits_pct  # noqa: E731
    match cfg.target_priority:
        case "weakest_first":
            return min(sit.hostiles, key=lambda h: (hp(h), h.distance))
        case "strongest_first":
            return max(sit.hostiles, key=lambda h: (hp(h), -h.distance))
        case "closest_first":
            return min(sit.hostiles, key=lambda h: h.distance)
    return current or min(sit.hostiles, key=lambda h: h.distance)


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
