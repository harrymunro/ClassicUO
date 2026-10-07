"""Which world facts reach Jev's decisions and the planner, chosen by Jev.

TypeSafe's re-ranking pattern (https://docs.typesafe.ai/cookbooks/rerank_typesafe.md):
code shortlists generously, Jev answers one yes/no (a Noul) per candidate, and the best few
are kept. Jev can only pick what code hands it, so the shortlists are wide: up to 50 facts
for a decision, up to 30 results for a planner query.

- Tactical (FactPicker): on a situation change only (a new area, a new kind of creature in
  view, a new goal), never every decision, Jev is asked of each shortlisted fact "would
  knowing this change what the character should do in the next minute?". The best 3 above
  0.6 go into the decision state as `what_you_know_about_this_place` until the situation
  changes again. The request runs beside the decision loop, which never waits for it.
- Planner (Ranker): the world queries that return a list (notes, hunting_spots,
  what_spawns) fetch three times what was asked for, Jev scores each result against the
  planner's question, and the best come back first with their `relevance`. Without Jev
  (the rule judge, or an error) the store's own order is kept.

Every selection and re-ranking is logged (records of type "facts" and "rerank") with the
shortlist, the scores and the tokens, so a run can be inspected afterwards.
"""

import asyncio
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import questions
from . import calls
from .judge import Judge
from .logs import creature_kind
from .state import Situation
from .world import DEFAULT_MAP, World

KNOWN_KEY = "what_you_know_about_this_place"
PRICE_PER_MILLION = 0.042  # Jev's list price per million input tokens (loop.LoopConfig has the same)
CHUNK = 25                 # questions per Jev request; bigger shortlists go out as parallel requests
STOPWORDS = {"the", "and", "with", "from", "that", "this", "your", "yourself", "keep", "near", "into", "when",
             "then", "them", "there", "their", "have", "some", "more", "most", "until", "while", "about", "every",
             "each", "also", "back", "only", "just", "what", "where", "which", "after", "before", "over", "under"}


@dataclass(frozen=True)
class Fact:
    text: str
    why: str          # how it got on the shortlist: "area: X", "creature: wisp", "spawns", "goal", "archetype"
    source: str = ""  # where the row came from: note, guide:<url>, outcomes, model:unverified ...


def can_rank(judge: Judge | None) -> bool:
    """The rule judge answers decision questions only: re-ranking needs a model."""
    return judge is not None and getattr(judge, "name", "") != "heuristic"


def keywords(text: str, limit: int = 6) -> list[str]:
    words = []
    for w in re.findall(r"[a-z']+", text.lower()):
        w = w.strip("'")
        if len(w) >= 4 and w not in STOPWORDS and w not in words:
            words.append(w)
    return words[:limit]


def goal_text(snap: dict[str, Any]) -> str:
    goal = snap.get("agent", {}).get("goal") or {}
    return "" if goal.get("paused") else (goal.get("text") or "").strip()


def area_name(world: World, x: int, y: int, map: str = DEFAULT_MAP) -> str:
    """The place a tile is in, as the situation's key: its region, else a coarse patch of wilderness."""
    r = world.region_at(x, y, map)
    return r["name"] if r else f"wilderness near {x // 100 * 100},{y // 100 * 100}"


def areas_at(world: World, x: int, y: int, map: str = DEFAULT_MAP, focus: str | None = None) -> list[str]:
    """Area names whose notes may matter here: the region, the town or dungeon it is part of,
    the area a hunt was sent to, and spawn areas within 30 tiles."""
    out: list[str] = []
    r = world.region_at(x, y, map)
    for a in ([r["name"], r.get("part_of")] if r else []) + [focus]:
        if a and a not in out:
            out.append(a)
    for s in world.what_spawns(near=[x, y], radius=30, map=map, limit=3):
        if s["area"] not in out:
            out.append(s["area"])
    return out


def spawn_words(s: dict[str, Any]) -> str:
    creatures = ", ".join(f"up to {c.get('max_count') or '?'} {c.get('name') or c['type']} ({c['difficulty']})"
                          for c in s["creatures"])
    where = f"{s['distance']} tiles away" if "distance" in s else ""
    respawn = f", respawning in {s['respawn']}" if s.get("respawn") else ""
    return f"{s['area']} spawns {creatures}{respawn}" + (f" ({where})" if where else "") + "."


def shortlist(world: World, x: int, y: int, kinds: set[str] | frozenset[str] = frozenset(), goal: str = "",
              archetype: str = "warrior", focus: str | None = None, map: str = DEFAULT_MAP,
              limit: int = 50) -> list[Fact]:
    """Facts that might matter here, generously: notes about the areas around (outcome notes
    included), notes naming the creatures in view, what spawns nearby, notes matching the goal
    and the archetype. Stale rows are left out; duplicates once. Danger sightings from play
    ("Had to leave...", "Stronger than a new character, seen...") are left to the planner: the
    fight sees the creatures themselves, and picked here they made every later hunt there end in
    a leave, each writing another such note."""
    facts: dict[str, Fact] = {}

    def add(text: str, why: str, source: str = "") -> None:
        text = " ".join(str(text).split())
        if text and text not in facts and len(facts) < limit:
            facts[text] = Fact(text, why, source)

    def sighting(n: dict) -> bool:
        return n.get("source") == "seen" and "danger" in (n.get("tags") or [])

    areas = areas_at(world, x, y, map, focus)
    for a in areas:
        for n in world.notes(area=a, limit=20):
            if not sighting(n):
                add(n["text"], f"area: {a}", n.get("source", ""))
    for kind in sorted(kinds):
        for n in world.notes(keywords=kind, limit=6):
            if not sighting(n):
                add(n["text"], f"creature: {kind}", n.get("source", ""))
    for s in world.what_spawns(near=[x, y], radius=40, map=map, limit=4):
        add(spawn_words(s), "spawns", s.get("source", ""))
    if goal and (words := keywords(goal)):
        for n in world.notes(keywords=words, limit=8):
            add(n["text"], "goal", n.get("source", ""))
    if archetype:
        for n in world.notes(keywords=archetype, limit=5):
            add(n["text"], "archetype", n.get("source", ""))
    return list(facts.values())


async def score(judge: Judge, state: dict[str, Any], texts: list[str],
                question: Callable[[str], dict[str, Any]]) -> tuple[list[float], int, float]:
    """One Noul per text, in requests of at most CHUNK questions sent together.
    Returns the scores in the texts' order, the input tokens and the slowest latency (ms)."""
    chunks = [list(range(i, min(i + CHUNK, len(texts)))) for i in range(0, len(texts), CHUNK)]
    answers = await asyncio.gather(*(judge.ask(state, {f"f{i + 1}": question(texts[i]) for i in ids})
                                     for ids in chunks))
    scores = [0.0] * len(texts)
    for ids, ans in zip(chunks, answers):
        for i in ids:
            scores[i] = float(ans.nouls.get(f"f{i + 1}", 0.0))
    return scores, sum(a.input_tokens for a in answers), max((a.latency_ms for a in answers), default=0.0)


# ---------------------------------------------------------------- tactical


def fact_question(sit: Situation) -> Callable[[str], dict[str, Any]]:
    who = questions.character(sit)

    def q(text: str) -> dict[str, Any]:
        return {
            "type": "noul",
            "instructions": {"role": questions.role(sit),
                             "question": f"Would knowing this fact change what the {who} should do in the next minute?",
                             "fact": text},
            # Asked whether a fact "bears on a choice", Jev scored everything about the place alike
            # (0.41-0.58) and never picked the two decisive wisp notes in a 10-round run
            # (2026-10-07); asked whether it says what to do about a creature in view, they came
            # first at 0.63 and the general ones fell to 0.46 or less.
            "criteria": {
                "true": f"It says what to do about one of the creatures in `creatures_in_view` (fight it, leave it "
                        f"alone, fight it first) or about something the {who} is choosing right now, and the "
                        "situation doesn't already show it.",
                "false": "It is general: about this place, other places or creatures not in view, loot or travel, or "
                         f"something the {who} can't act on in the next minute.",
            },
        }

    return q


def situation_words(sit: Situation, areas: list[str], goal: str) -> dict[str, Any]:
    """A short situation for the fact questions: who, where, what is in view, the goal."""
    you = sit.state["you"]
    who = questions.character(sit)
    return {
        "character": f"a {who}",
        "health": you["health"],
        "supplies": you.get("supplies", ""),
        "fighting": you["fighting"],
        "where": areas[0] if areas else "open country",
        "areas_around": areas[1:],
        "creatures_in_view": [questions.describe_hostile(h.info, who) for h in sit.hostiles] or ["none"],
        "goal": goal or "none: fighting whatever comes",
    }


class FactPicker:
    """Keeps the few world facts that matter right now in the decision state.

    mode "jev": Jev picks (the default); "all": every shortlisted fact (the benchmark's
    comparison); "none": nothing. Call update() and apply() on every situation the loop
    builds; a selection only starts when the situation changed, at most every
    `min_interval_s`, and runs as its own task."""

    def __init__(self, world: World | None, judge: Judge | None, mode: str = "jev", keep: int = 3,
                 threshold: float = 0.6, min_interval_s: float = 5.0, focus: str | None = None,
                 map: str = DEFAULT_MAP, price_per_million: float = PRICE_PER_MILLION):
        if mode not in ("jev", "all", "none"):
            raise ValueError(f"facts mode should be jev, all or none, not {mode!r}")
        self.world = world
        self.judge = judge
        self.mode = mode
        self.keep = keep
        self.threshold = threshold
        self.min_interval_s = min_interval_s
        self.focus = focus      # a hunt's area name, which may be a spawn area rather than a region
        self.map = map
        self.price = price_per_million
        self.log: Callable[[dict[str, Any]], None] = lambda rec: None
        self.chosen: list[tuple[Fact, float | None]] = []
        self.selections = 0
        self.input_tokens = 0
        self._untaken = 0
        self._key: tuple[str, str] | None = None
        self._kinds: set[str] = set()       # creature kinds in view since the last area or goal change
        self._wanted: tuple | None = None   # a change waiting for its selection
        self._task: asyncio.Task | None = None
        self._next_ok = 0.0

    @property
    def active(self) -> bool:
        return self.mode != "none" and self.world is not None

    def update(self, snap: dict[str, Any], sit: Situation) -> None:
        if not self.active:
            return
        p = snap["player"]
        key = (area_name(self.world, p["x"], p["y"], self.map), goal_text(snap))
        kinds = {creature_kind(h.name) for h in sit.hostiles}
        trigger = None
        if key != self._key:
            if self._key is None:
                trigger = "start"
            elif key[0] != self._key[0]:
                trigger = f"new area: {key[0]}"
                self.chosen = []  # what was picked was about the last place
            else:
                trigger = "new goal"
            self._key, self._kinds = key, set(kinds)
        elif new := kinds - self._kinds:
            trigger = "new creature: " + ", ".join(sorted(new))
            self._kinds |= new
        if trigger:
            self._wanted = (trigger, sit, p["x"], p["y"], key[1], frozenset(self._kinds))
        now = time.monotonic()
        if self._wanted and (self._task is None or self._task.done()) and now >= self._next_ok:
            wanted, self._wanted = self._wanted, None
            self._next_ok = now + self.min_interval_s
            self._task = asyncio.get_running_loop().create_task(self.select(*wanted))

    def apply(self, sit: Situation) -> None:
        """Puts the chosen facts into the situation for this decision's questions."""
        if self.chosen:
            sit.known = [f.text for f, _ in self.chosen]
            sit.state[KNOWN_KEY] = sit.known

    def take_tokens(self) -> int:
        """Input tokens spent since the last call, for the loop's cost."""
        n, self._untaken = self._untaken, 0
        return n

    async def select(self, trigger: str, sit: Situation, x: int, y: int, goal: str, kinds: frozenset[str]) -> None:
        t0 = time.perf_counter()
        areas = areas_at(self.world, x, y, self.map, self.focus)
        facts = shortlist(self.world, x, y, kinds, goal, sit.archetype, self.focus, self.map)
        rec: dict[str, Any] = {"type": "facts", "t": time.time(), "mode": self.mode, "trigger": trigger,
                               "areas": areas, "creatures": sorted(kinds), "goal": goal, "shortlist": len(facts)}
        scores: list[float | None] = [None] * len(facts)
        tokens = 0
        if self.mode == "all":
            chosen = [(f, None) for f in facts]
        elif not facts or not can_rank(self.judge):
            chosen = []
        else:
            try:
                got, tokens, latency = await score(self.judge, situation_words(sit, areas, goal),
                                                   [f.text for f in facts], fact_question(sit))
                scores = list(got)
                rec["latency_ms"] = round(latency, 1)
            except Exception as e:  # a failed selection leaves the decisions without facts, not stuck
                rec["error"] = f"{type(e).__name__}: {e}"[:300]
                got = []
            ranked = sorted(zip(facts, got), key=lambda fs: -fs[1])
            chosen = [(f, s) for f, s in ranked if s >= self.threshold][:self.keep]
        self.chosen = chosen
        self.selections += 1
        self.input_tokens += tokens
        self._untaken += tokens
        rec.update({
            "chosen": [{"text": f.text, "score": None if s is None else round(s, 3)} for f, s in chosen],
            "facts": [{"text": f.text, "why": f.why, "score": None if s is None else round(s, 3)}
                      for f, s in zip(facts, scores)],
            "input_tokens": tokens, "est_cost_usd": round(tokens / 1e6 * self.price, 5),
            "seconds": round(time.perf_counter() - t0, 2)})
        self.log(rec)
        if any(s is not None for s in scores):
            kept = {id(f) for f, _ in chosen}
            ranked = sorted(zip(facts, scores), key=lambda fs: -(fs[1] or 0.0))[:6]
            calls.emit({"kind": "facts", "title": f"which facts matter here? ({len(facts)} on the shortlist)",
                        "model": getattr(self.judge, "name", ""), "latency_ms": rec.get("latency_ms", 0),
                        "questions": [{"q": "facts", "title": f"a yes/no for each, kept at {self.threshold}",
                                       "kind": "many", "options": [{"label": f.text[:70], "p": round(s or 0.0, 3),
                                                                     "kept": id(f) in kept} for f, s in ranked]}],
                        "note": f"{len(chosen)} kept ({trigger})"})

    def close(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()


# ---------------------------------------------------------------- planner

RANKED_TOOLS = {"notes": 10, "hunting_spots": 5, "what_spawns": 10}  # tool -> its default limit


def result_words(tool: str, item: Any) -> str:
    """One result as Jev reads it: a note's text, otherwise the result itself, coordinates left out."""
    if tool == "notes" and isinstance(item, dict):
        return f"{item.get('area') + ': ' if item.get('area') else ''}{item.get('text', '')}"
    if isinstance(item, dict):
        item = {k: v for k, v in item.items() if k not in ("x", "y", "z", "map", "source", "last_seen", "score")}
    return json.dumps(item, separators=(",", ":"))[:800]


def rerank_question(question: str, goal: str) -> Callable[[str], dict[str, Any]]:
    def q(text: str) -> dict[str, Any]:
        return {
            "type": "noul",
            "instructions": {"role": "You help plan a character's session in the game Ultima Online by judging "
                                     "what a world-store query found.",
                             "question": "Does this result help answer the planner's question, for the player's goal?",
                             "planner_question": question, "player_goal": goal or "(none given)", "result": text},
            "criteria": {
                "true": "It is what the question is after, or a fact that should change the plan: the right place, "
                        "creatures, vendor, danger or past result for this question and goal.",
                "false": "It is about something else, or of no use for this question and goal.",
            },
        }

    return q


class Ranker:
    """The planner's world queries, with list results re-ranked by Jev against its question."""

    def __init__(self, judge: Judge | None, log: Callable[[dict[str, Any]], None] | None = None,
                 price_per_million: float = PRICE_PER_MILLION):
        self.judge = judge
        self.log = log or (lambda rec: None)
        self.price = price_per_million
        self.calls = 0
        self.input_tokens = 0

    @property
    def cost(self) -> float:
        return self.input_tokens / 1e6 * self.price

    async def call_tool(self, world: World, name: str, args: dict[str, Any] | str | None, goal: str = "",
                        context: str = "") -> Any:
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except json.JSONDecodeError:
                args = {}
        args = dict(args or {})
        question = str(args.pop("question", "") or "").strip()
        if name not in RANKED_TOOLS or not can_rank(self.judge):
            return world.call_tool(name, args)
        limit = int(args.get("limit") or RANKED_TOOLS[name])
        found = world.call_tool(name, {**args, "limit": min(30, max(3 * limit, 15))})
        if not isinstance(found, list) or len(found) <= 1:
            return found
        if not question:
            asked = ", ".join(f"{k}={v!r}" for k, v in args.items() if k != "limit")
            question = f"What {name}({asked}) can tell the planner" + (f": {context[:300]}" if context else ".")
        rec: dict[str, Any] = {"type": "rerank", "t": time.time(), "tool": name, "args": args, "question": question,
                               "shortlist": len(found)}
        try:
            scores, tokens, latency = await score(self.judge, {"planner_question": question, "player_goal": goal},
                                                  [result_words(name, r) for r in found], rerank_question(question, goal))
        except Exception as e:  # the store's own order, rather than no answer
            rec["error"] = f"{type(e).__name__}: {e}"[:300]
            self.log(rec)
            return found[:limit]
        order = sorted(range(len(found)), key=lambda i: -scores[i])
        out = [{**found[i], "relevance": round(scores[i], 2)} if isinstance(found[i], dict) else found[i]
               for i in order[:limit]]
        self.calls += 1
        self.input_tokens += tokens
        rec.update({"order": order, "scores": [round(s, 3) for s in scores], "kept": len(out),
                    "input_tokens": tokens, "est_cost_usd": round(tokens / 1e6 * self.price, 5),
                    "latency_ms": round(latency, 1)})
        self.log(rec)
        return out
