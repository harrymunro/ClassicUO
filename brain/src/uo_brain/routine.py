"""Routine calls inside a hunt, asked of Jev instead of fixed thresholds.

Between planner calls a hunt makes small, recurring judgments: is it time to head back to
town, is this spot still worth hunting, should the character walk to another part of the
spawn while it's quiet. These were fixed numbers (back under 10 bandages or at 85% weight,
give up after 4 quiet minutes, patrol every 25 s). Now they are Jev yes/no questions
(Nouls), asked with the hunt so far in words, at the moments the answer can change:

- after a kill;
- when the supplies or the bag cross a level ("running low", "getting heavy");
- while it's quiet, every 30 s (only then is moving elsewhere in the spawn asked);
- otherwise once a minute.

Never more often than every 10 s, one question at a time, and never in the fight loop's way:
the question runs as its own task and the hunt reads the verdict on a later snapshot.

Code keeps the floors that need no judgment: death, no bandages and no heal potions left,
no reagents for any attack spell, no arrows (or bolts) for the bow in hand, a tamer's pet
gone (dead or out of sight), a full bag, and
10 minutes without a creature. A "head back" verdict waits up to 30 s for the fight at hand
to finish. A confident "walk elsewhere" outweighs doubts about the spot. When Jev is unsure
(an answer between 0.35 and 0.65) twice in a row, the hunt ends and the planner decides,
with Jev's numbers in the reason. Without Jev (the rule judge, or two failed questions in a row)
the old thresholds apply.

Each question and its answer goes to the session log as a `routine` record.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .judge import Answers, Judge
from .spells import ATTACK_SPELLS, mana_words
from .state import archetype_of, health_words


@dataclass
class RoutineConfig:
    yes: float = 0.65               # a Noul at or above this is a yes
    no: float = 0.35                # at or below, a no; in between, unsure
    min_gap_s: float = 10.0         # between two questions
    every_s: float = 60.0           # ask at least this often
    quiet_after_s: float = 20.0     # no creature in sight this long counts as quiet
    quiet_every_s: float = 30.0     # how often to ask while quiet
    unsure_handover: int = 2        # unsure answers in a row before the planner decides
    # Walks round the spawn in a row that found nothing before the planner decides: in a soak
    # run Jev said "walk elsewhere" (0.65-0.77) for 7 minutes round an emptied graveyard.
    max_patrols: int = 4
    defer_s: float = 30.0           # how long a stop waits for the fight at hand to end
    quiet_floor_s: float = 600.0    # with Jev: nothing to fight for this long ends the hunt anyway
    full_bag_pct: int = 98          # a floor: the bag can take no more
    price_per_million: float = 0.042  # Jev's price per million input tokens (loop.LoopConfig)
    # The rules used without Jev: the old fixed thresholds.
    min_bandages: int = 10
    min_reagents: int = 5
    min_ammo: int = 20
    max_weight_pct: int = 85
    quiet_rule_s: float = 240.0
    patrol_every_s: float = 25.0


@dataclass
class Look:
    """What a snapshot means for the hunt."""
    stop: str | None = None    # end the hunt now, and why
    ask: str | None = None     # the moment to ask Jev at ("kill", "supplies", "quiet", "minute")
    patrol: bool = False       # walk to another spot of the spawn


@dataclass
class Verdict:
    kind: str   # stay, head_back, leave_spot, patrol, wait, unsure, handover
    reason: str


ROLE = ("You are helping run a hunting trip for a {who} in the game Ultima Online. The character fights, heals "
        "and loots on its own; every so often you judge whether the trip should go on. Coming back to town costs "
        "a walk or a recall and the time to restock; staying out too long risks dying with nothing left to heal "
        "with, and a full bag can't carry more loot.")


@dataclass
class HuntWatch:
    """Watches one hunt's snapshots: when it should end, when to ask Jev, when to patrol."""
    area: str
    minutes: float
    judge: Judge | None
    cfg: RoutineConfig = field(default_factory=RoutineConfig)
    log: Callable[[dict[str, Any]], None] = lambda _: None
    archetype: str | None = None
    radius: int = 0  # how far the spawn spreads, in tiles, when known
    clock: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        self.jev = self.judge is not None and getattr(self.judge, "name", "") != "heuristic"
        now = self.clock()
        self.began = now
        self.first: dict[str, Any] | None = None
        self.last_hostile = now
        self.last_kill = now
        self.kills_seen: int | None = None
        self.levels: tuple | None = None
        self.last_ask = now  # the first minute question comes a minute in
        self.last_quiet_ask = float("-inf")
        self.last_patrol = float("-inf")
        self.moment: str | None = None  # a moment that came during the gap, asked after it
        self.in_flight = False
        self.unsure = 0
        self.patrols = 0                 # walks round the spawn since the last fight
        self.failed = 0                  # failed questions in a row
        self.pending: tuple[str, float] | None = None  # (why, since): a stop waiting for the fight to end
        self.patrol_due = False
        self.lowest_hp = 100
        self.asked = 0
        self.input_tokens = 0
        self.verdicts: dict[str, int] = {}

    # ------------------------------------------------------------ snapshots

    def mage(self, snap: dict[str, Any]) -> bool:
        return (self.archetype or archetype_of(snap)) in ("mage", "mage-tamer")

    def observe(self, snap: dict[str, Any]) -> Look:
        now = self.clock()
        self.first = self.first or snap
        p = snap["player"]
        if p.get("hits_max"):
            self.lowest_hp = min(self.lowest_hp, round(100 * p["hits"] / p["hits_max"]))
        kills = snap["agent"].get("stats", {}).get("kills", 0)
        killed = self.kills_seen is not None and kills > self.kills_seen
        self.kills_seen = kills
        if killed:
            self.last_kill = now
        hostile = any(m.get("monster") and not m.get("dead") for m in snap["mobiles"])
        busy = snap["agent"].get("engaged") or snap["agent"].get("looting")
        if hostile or busy:
            self.last_hostile = now
        quiet_s = now - self.last_hostile

        floor = self.floor(snap, quiet_s)
        if floor:
            return Look(stop=floor)
        if not self.jev or self.failed >= 2:
            return self.rules(snap, now, quiet_s)

        if self.pending:
            why, since = self.pending
            close = any(m.get("monster") and not m.get("dead") and m["distance"] <= 3 for m in snap["mobiles"])
            if not close and not snap["agent"].get("looting") or now - since >= self.cfg.defer_s:
                return Look(stop=why)
        look = Look()
        if self.patrol_due:
            self.patrol_due = False
            look.patrol = True

        levels = self.level_words(snap)
        if killed:
            self.moment = "kill"
        elif self.levels is not None and levels != self.levels:
            self.moment = self.moment or "supplies"
        self.levels = levels
        if quiet_s >= self.cfg.quiet_after_s and now - self.last_quiet_ask >= self.cfg.quiet_every_s:
            self.moment = self.moment or "quiet"
        elif now - self.last_ask >= self.cfg.every_s:
            self.moment = self.moment or "minute"
        if self.moment and not self.in_flight and not self.pending and now - self.last_ask >= self.cfg.min_gap_s:
            look.ask, self.moment = self.moment, None
            self.in_flight = True  # until ask() is done; observe() won't hand out another
            self.last_ask = now
            if quiet_s >= self.cfg.quiet_after_s:
                self.last_quiet_ask = now
        return look

    def floor(self, snap: dict[str, Any], quiet_s: float) -> str | None:
        """The stops that need no judgment."""
        p = snap["player"]
        s = p.get("supplies", {})
        if p["dead"]:
            return "died"
        if p.get("weight_max") and p["weight"] * 100 >= self.cfg.full_bag_pct * p["weight_max"]:
            return "the bag is full"
        if self.mage(snap):
            book = [sp for sp in (snap.get("magic") or {}).get("spells", []) if sp["name"] in ATTACK_SPELLS]
            if book and all(sp.get("missing") == "reagents" for sp in book):
                return "out of reagents for every attack spell"
        elif s.get("bandages", 0) == 0 and s.get("heal_potions", 0) == 0:
            return "out of bandages and heal potions"
        if (a := ammo(p)) and a[1] == 0:
            return f"out of {a[0]}"
        if self.first and (self.first.get("pets") or []) and not (snap.get("pets") or []) \
                and (p.get("skills") or {}).get("Animal Taming", 0) >= 50:
            return "the pet is gone"
        if self.jev and self.failed < 2 and quiet_s >= self.cfg.quiet_floor_s:
            return f"nothing to fight for {round(quiet_s / 60)} minutes"
        return None

    def rules(self, snap: dict[str, Any], now: float, quiet_s: float) -> Look:
        """The fixed thresholds, for hunts without Jev."""
        p = snap["player"]
        s = p.get("supplies", {})
        if p.get("weight_max") and p["weight"] * 100 >= self.cfg.max_weight_pct * p["weight_max"]:
            return Look(stop="bag is heavy")
        if not self.mage(snap) and s.get("bandages", 0) < self.cfg.min_bandages:
            return Look(stop="low on bandages")
        if self.mage(snap) and min((s.get("reagents") or {"x": 0}).values()) < self.cfg.min_reagents:
            return Look(stop="low on reagents")
        if (a := ammo(p)) and a[1] < self.cfg.min_ammo:
            return Look(stop=f"low on {a[0]}")
        if quiet_s > self.cfg.quiet_rule_s:
            return Look(stop=f"nothing to fight for {round(self.cfg.quiet_rule_s / 60)} minutes")
        if quiet_s > self.cfg.quiet_after_s and now - self.last_patrol >= self.cfg.patrol_every_s:
            self.last_patrol = now
            return Look(patrol=True)
        return Look()

    # ------------------------------------------------------------ the question

    async def ask(self, snap: dict[str, Any], moment: str) -> Verdict | None:
        """One Jev question about the hunt; its verdict takes effect on the next snapshots."""
        self.in_flight = True
        try:
            words = self.words(snap)
            quiet = moment == "quiet"
            if not quiet:
                self.patrols = 0
            qs = questions(words, who=words["you"]["character"], strategy=(snap["agent"].get("strategy") or "").strip(),
                           quiet=quiet)
            try:
                ans = await self.judge.ask(words, qs)
            except Exception as e:  # network, credits: the rules take over after two failures
                self.failed += 1
                self.log({"type": "routine", "t": time.time(), "area": self.area, "moment": moment,
                          "error": f"{type(e).__name__}: {e}"[:300]})
                return None
            self.failed = 0
            self.asked += 1
            self.input_tokens += ans.input_tokens
            v = self.verdict(ans, words)
            self.verdicts[v.kind] = self.verdicts.get(v.kind, 0) + 1
            if v.kind in ("head_back", "leave_spot", "handover"):
                self.pending = (v.reason, self.clock())
            elif v.kind == "patrol":
                self.patrol_due = True
            self.log({"type": "routine", "t": time.time(), "area": self.area, "moment": moment, "state": words,
                      "questions": qs, "answers": ans.to_log(), "verdict": v.kind, "reason": v.reason})
            return v
        finally:
            self.in_flight = False

    def verdict(self, ans: Answers, words: dict[str, Any]) -> Verdict:
        cfg = self.cfg
        back, stay, move = (ans.nouls.get(k) for k in ("head_back", "stay_here", "move_spot"))
        if back is not None and back >= cfg.yes:
            self.unsure = 0
            return Verdict("head_back", f"Jev: time to head back ({back:.2f}): {back_words(words)}")
        if stay is not None and stay <= cfg.no:
            self.unsure = 0
            return Verdict("leave_spot", f"Jev: not worth staying here ({stay:.2f}): {spot_words(words)}")
        # Walking elsewhere in the spawn is the cheap answer to a quiet spell, doubts about the spot
        # included; doubts about heading back are about supplies, so they still count.
        doubt = lambda v: v is not None and cfg.no < v < cfg.yes  # noqa: E731
        if move is not None and move >= cfg.yes and not doubt(back):
            self.unsure = 0
            self.patrols += 1
            if self.patrols > cfg.max_patrols:
                self.patrols = 0
                return Verdict("handover", f"handed back to the planner: walked round the spawn {cfg.max_patrols} "
                                           f"times and found nothing (stay here {stay:.2f}): {spot_words(words)}"
                               if stay is not None else f"handed back to the planner: walked round the spawn "
                               f"{cfg.max_patrols} times and found nothing: {spot_words(words)}")
            return Verdict("patrol", f"move {move:.2f}")
        unsure = [f"{k} {v:.2f}" for k, v in (("head back", back), ("stay here", stay)) if doubt(v)]
        if unsure:
            self.unsure += 1
            if self.unsure >= cfg.unsure_handover:
                self.unsure = 0
                return Verdict("handover", f"handed back to the planner: Jev was unsure twice running "
                                           f"({', '.join(unsure)}): {back_words(words)}; {spot_words(words)}")
            return Verdict("unsure", ", ".join(unsure))
        self.unsure = 0
        if move is not None:
            return Verdict("wait", f"move {move:.2f}")
        return Verdict("stay", f"head back {back:.2f}, stay here {stay:.2f}" if back is not None and stay is not None
                       else "stay")

    # ------------------------------------------------------------ in words

    def level_words(self, snap: dict[str, Any]) -> tuple:
        """The bands that make a question worth asking when they change."""
        p = snap["player"]
        a = ammo(p)
        return supply_level(p, self.mage(snap)), ammo_level(a[1]) if a else None, bag_words(p).split(" (")[0]

    def words(self, snap: dict[str, Any]) -> dict[str, Any]:
        """The hunt so far, in words, for Jev. Code does the arithmetic."""
        now = self.clock()
        p, p0 = snap["player"], (self.first or snap)["player"]
        s, s0 = p.get("supplies", {}), p0.get("supplies", {})
        st, st0 = snap["agent"].get("stats", {}), (self.first or snap)["agent"].get("stats", {})
        kills = st.get("kills", 0) - st0.get("kills", 0)
        mins = (now - self.began) / 60
        mage = self.mage(snap)
        hp = round(100 * p["hits"] / p["hits_max"]) if p.get("hits_max") else 100
        you: dict[str, Any] = {"character": character(p, mage), "health": f"{health_words(hp)} ({hp}%)"}
        if mage:
            mana = round(100 * p["mana"] / p["mana_max"]) if p.get("mana_max") else 100
            you["mana"] = f"{mana_words(mana)} ({mana}%)"
            you["reagents"] = reagent_words(s.get("reagents") or {})
            you["reagents_last"] = reagents_last(s.get("reagents") or {}, s0.get("reagents") or {}, kills)
            book = [sp for sp in (snap.get("magic") or {}).get("spells", []) if sp["name"] in ATTACK_SPELLS]
            ready = [sp["name"] for sp in book if sp.get("missing") != "reagents"]
            you["attack_spells_with_reagents"] = ", ".join(ready) if ready else "none"
            you["heal_potions_left"] = s.get("heal_potions", 0)
        else:
            you["supplies"] = f"{s.get('bandages', 0)} bandages and {s.get('heal_potions', 0)} heal potions left"
            you["supplies_last"] = lasts(s.get("bandages", 0), s0.get("bandages", 0) - s.get("bandages", 0), kills,
                                         "bandages")
        if a := ammo(p):
            what, left = a
            you["ammo"] = f"{left} {what} left"
            you["ammo_last"] = lasts(left, s0.get(what, 0) - left, kills, what)
        you["bag"] = bag_words(p)
        hunt: dict[str, Any] = {
            "area": self.area,
            "minutes_so_far": round(mins, 1),
            "minutes_left_of_the_planned_hunt": max(0, round(self.minutes - mins, 1)),
            "kills": kills,
            "minutes_since_last_kill": round((now - self.last_kill) / 60, 1) if kills else "no kill yet",
            "usual_minutes_between_kills": round(mins / kills, 1) if kills else "no kill yet",
            "lowest_health": f"{health_words(self.lowest_hp)} ({self.lowest_hp}%)",
            "flees": st.get("flees", 0) - st0.get("flees", 0),
            "gold_picked_up": p.get("gold", 0) - p0.get("gold", 0),
        }
        if self.radius:
            hunt["area_size"] = (f"creatures spawn over about {self.radius} tiles around the centre; the character "
                                 "sees about 18 tiles around itself")
        hostiles = [m for m in snap["mobiles"] if m.get("monster") and not m.get("dead")]
        close = sum(1 for m in hostiles if m["distance"] <= 3)
        quiet_s = now - self.last_hostile
        around = (f"{len(hostiles)} hostile creature{'s' if len(hostiles) != 1 else ''} in sight, {close} close"
                  if hostiles else f"nothing hostile in sight for {round(quiet_s)} seconds")
        return {"you": you, "this_hunt": hunt, "around_now": around}

    def summary(self) -> dict[str, Any]:
        hours = max((self.clock() - self.began) / 3600, 1e-9)
        cost = self.input_tokens / 1e6 * self.cfg.price_per_million
        return {"judged_by": "jev" if self.jev else "rules", "questions": self.asked,
                "input_tokens": self.input_tokens, "cost_usd": round(cost, 5),
                "cost_usd_per_hour": round(cost / hours, 4), "verdicts": self.verdicts}


def questions(words: dict[str, Any], who: str, strategy: str = "", quiet: bool = False) -> dict[str, dict[str, Any]]:
    """The routine questions, as Nouls. Moving elsewhere in the spawn is only asked while it's quiet."""
    def instructions(question: str) -> dict[str, Any]:
        out: dict[str, Any] = {"role": ROLE.format(who=who)}
        if strategy:
            out["player_strategy"] = strategy
            out["using_the_strategy"] = (f"These are the player's own instructions for how their {who} should play. "
                                         "Follow them wherever they bear on this question.")
        out["question"] = question
        return out

    supplies = {"mage": "reagents, mana and heal potions",
                "archer": "arrows or bolts, bandages and heal potions"}.get(who, "bandages and heal potions")
    qs: dict[str, dict[str, Any]] = {
        "head_back": {
            "type": "noul",
            "instructions": instructions(f"Should the {who} stop hunting now and head back to town to restock, "
                                         "heal up or bank?"),
            "criteria": {
                "true": f"Any one of these is enough: the {supplies} will run out within the next few kills at the "
                        "rate this hunt has used them; the bag is nearly full or full, so loot can't be carried; or "
                        "health keeps getting very low and there is little left to heal with.",
                "false": f"There are {supplies} for many more kills at this rate, room in the bag, and health "
                         "comes back between fights.",
            },
        },
        "stay_here": {
            "type": "noul",
            "instructions": instructions(f"Is this spot still worth hunting for the {who}?"),
            "criteria": {
                "true": "Creatures keep turning up and the fights here are being won without coming close to dying.",
                "false": "Nothing has turned up for a long while, or the fights here cost far more than they give: "
                         "health near death again and again, or several flees for few kills.",
            },
        },
    }
    if quiet:
        qs["move_spot"] = {
            "type": "noul",
            "instructions": instructions(f"It's quiet: nothing hostile is in sight. Should the {who} walk to another "
                                         "part of this hunting area to look for creatures, rather than wait here for "
                                         "them to come back?"),
            "criteria": {
                "true": "It has been quiet for longer than the usual time between kills on this hunt, and the area "
                        "is bigger than what the character can see from here.",
                "false": "It went quiet only a short while ago compared with the usual time between kills, or the "
                         "character should recover health or mana before the next fight.",
            },
        }
    return qs


def ammo(p: dict[str, Any]) -> tuple[str, int] | None:
    """(arrows or bolts, how many) for a character with a bow or crossbow equipped."""
    ranged = p.get("ranged")
    if not ranged or not ranged.get("ammo"):
        return None
    return ranged["ammo"], p.get("supplies", {}).get(ranged["ammo"], 0)


def character(p: dict[str, Any], mage: bool) -> str:
    return "mage" if mage else "archer" if ammo(p) else "warrior"


def ammo_level(n: int) -> str:
    return "plenty" if n > 100 else "some" if n > 50 else "running low" if n > 20 else "nearly gone"


def supply_level(p: dict[str, Any], mage: bool) -> str:
    s = p.get("supplies", {})
    if mage:
        return reagent_words(s.get("reagents") or {}).split(":")[0]
    bandages = s.get("bandages", 0)
    return "plenty" if bandages > 50 else "some" if bandages > 25 else "running low" if bandages > 10 else "nearly gone"


def reagent_words(regs: dict[str, int]) -> str:
    if not regs or not any(regs.values()):
        return "none"
    low = sorted(f"{k.replace('_', ' ')} ({v} left)" for k, v in regs.items() if v < 10)
    out = sorted(k.replace("_", " ") for k, v in regs.items() if v == 0)
    if out:
        return "out of some: " + ", ".join(out)
    return "running low: " + ", ".join(low) if low else "plenty"


def bag_words(p: dict[str, Any]) -> str:
    if not p.get("weight_max"):
        return "unknown"
    pct = round(100 * p["weight"] / p["weight_max"])
    word = "light" if pct < 50 else "half full" if pct < 70 else "getting heavy" if pct < 85 else \
        "nearly full" if pct < 95 else "full"
    room = ": little room for more loot" if pct >= 85 else ""
    return f"{word} ({pct}% of what the character can carry){room}"


def reagents_last(regs: dict[str, int], start: dict[str, int], kills: int) -> str:
    """How many more kills the scarcest reagent lasts at this hunt's rate."""
    rates = {k: (start.get(k, 0) - v) / kills for k, v in regs.items() if kills and start.get(k, 0) > v}
    if not rates:
        return "no reagents used per kill yet on this hunt" if kills else "no kills yet on this hunt"
    k = min(rates, key=lambda r: regs[r] / rates[r])
    more = int(regs[k] / rates[k])
    return (f"{k.replace('_', ' ')} runs out first: about {rates[k]:.1f} a kill so far, enough for about {more} more "
            f"kill{'s' if more != 1 else ''}")


def lasts(left: int, used: int, kills: int, what: str) -> str:
    """How long the supplies last at this hunt's rate, worked out by code."""
    if used <= 0 or kills <= 0:
        return f"no {what} used per kill yet on this hunt" if kills else "no kills yet on this hunt"
    per_kill = used / kills
    more = int(left / per_kill)
    return f"about {per_kill:.1f} {what} a kill so far: enough for about {more} more kill{'s' if more != 1 else ''}"


def back_words(words: dict[str, Any]) -> str:
    you = words["you"]
    if you["character"] == "mage":
        return f"reagents {you['reagents']}, mana {you['mana']}, bag {you['bag']}"
    shots = f"{you['ammo']}, {you['ammo_last']}; " if "ammo" in you else ""
    return f"{shots}{you['supplies']}, {you['supplies_last']}; bag {you['bag']}"


def spot_words(words: dict[str, Any]) -> str:
    h = words["this_hunt"]
    return (f"{h['kills']} kills in {h['minutes_so_far']} min, last kill {h['minutes_since_last_kill']} min ago"
            if h["kills"] else f"no kill in {h['minutes_so_far']} min") + f", {words['around_now']}"
