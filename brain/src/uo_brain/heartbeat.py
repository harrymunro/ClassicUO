"""Always on: the planner's heartbeat, a daily digest, and resuming after a restart (cuo-6w8.6).

For long unattended runs on the local server. Three things:

- **Resume.** After every step the planner's state (the session goal, the running summary,
  its last steps, its focus) is saved in the shard's store (table planner_state, one row per
  character). A planner that starts for the same character and goal within RESUME_HOURS picks
  it up: a restarted brain or client carries on with the same goal and summary, told that it
  restarted and during which goal.
- **Heartbeat.** Every HEARTBEAT_MIN minutes, and on events (the session starting, a death, a
  long goal's milestone, a new area), a short reflection by the cheap model (kind `heartbeat`):
  it reads the long goals and their progress, the summary, the most important recent memories
  and the character now, writes lessons to memory, and sets the planner's focus for the next
  while (shown at every planner call; the player's goal stays as given). Skipped over budget.
- **Daily digest** (OpenClaw's daily log). At the first heartbeat of a day, the character's
  memories from an earlier day not yet digested are summed up in one memory of that day
  (kind `daily`), so weeks of play stay findable without reading every row.
"""

import json
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from . import costs, llm
from . import memory as memories
from .world import World, now

HEARTBEAT_MIN = 30
MIN_GAP_MIN = 10   # events wait until at least this long after the last reflection
RESUME_HOURS = 6.0
HISTORY_KEPT = 15
FOCUS_CAP = 300

SCHEMA = """
CREATE TABLE IF NOT EXISTS planner_state (
    character TEXT PRIMARY KEY, goal TEXT NOT NULL, summary TEXT, folded INTEGER NOT NULL DEFAULT 0,
    history TEXT NOT NULL, focus TEXT, focus_t TEXT, current TEXT, updated TEXT NOT NULL);
"""


# ---- resume ---------------------------------------------------------------------------

def ensure(world: World) -> None:
    world.db.executescript(SCHEMA)
    world.db.commit()


def save(world: World, character: str, goal: str, summary: str, folded: int, history: list[dict[str, Any]],
         focus: str = "", focus_t: str = "", current: str = "") -> None:
    ensure(world)
    world.db.execute(
        "INSERT INTO planner_state (character, goal, summary, folded, history, focus, focus_t, current, updated) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(character) DO UPDATE SET goal = excluded.goal, "
        "summary = excluded.summary, folded = excluded.folded, history = excluded.history, focus = excluded.focus, "
        "focus_t = excluded.focus_t, current = excluded.current, updated = excluded.updated",
        (character, goal, summary, folded, json.dumps(history[-HISTORY_KEPT:]), focus or None, focus_t or None,
         current or None, now()))
    world.db.commit()


def load(world: World, character: str, goal: str, hours: float = RESUME_HOURS) -> dict[str, Any] | None:
    """The saved state for this character and goal, if saved within `hours`."""
    ensure(world)
    r = world.db.execute("SELECT * FROM planner_state WHERE character = ? AND goal = ?", (character, goal)).fetchone()
    if not r:
        return None
    try:
        age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(r["updated"])).total_seconds() / 3600
    except (TypeError, ValueError):
        return None
    if age_h > hours:
        return None
    return {"goal": r["goal"], "summary": r["summary"] or "", "folded": r["folded"], "history": json.loads(r["history"]),
            "focus": r["focus"] or "", "focus_t": r["focus_t"] or "", "current": r["current"] or "",
            "age_min": round(age_h * 60, 1)}


# ---- the heartbeat ------------------------------------------------------------------------

SYSTEM = """You are a character's reflection in the game Ultima Online, between the goals its planner
chooses. Every so often, and after events (a death, a long goal's milestone, a new area), you look
at the character's long goals and their progress, how this session has gone, and its most
important recent memories, and call reflect exactly once with:

- lessons: up to three things learned that should change later choices, each one plain sentence
  with the place and the reason; only what isn't already among the memories. Usually none or one.
- focus: one or two sentences for the planner about what to concentrate on for the next while,
  within the player's goal: which long goal to push, what to avoid, what to fix first (supplies,
  a place that keeps going wrong). The player's goal always comes first.
"""

DAILY = """You keep a character's diary in the game Ultima Online. You get the character's memories from
one day (events and lessons, oldest first). Call digest exactly once with the day in at most 120
words: where it went, how it fared (kills, leaves, deaths), what it learned, how its long goals
moved. Plain words, place names and numbers kept."""


def reflect_tool() -> dict[str, Any]:
    return {"type": "function", "function": {
        "name": "reflect", "description": "Lessons to keep, and the planner's focus for the next while.",
        "parameters": {"type": "object", "properties": {
            "lessons": {"type": "array", "maxItems": 3, "items": {"type": "object", "properties": {
                "text": {"type": "string"}, "area": {"type": "string"},
                "importance": {"type": "integer", "description": "1 (minor) to 5."}}, "required": ["text"]}},
            "focus": {"type": "string"}},
            "required": ["lessons", "focus"], "additionalProperties": False}}}


def digest_tool() -> dict[str, Any]:
    return {"type": "function", "function": {
        "name": "digest", "description": "The day in a few sentences.",
        "parameters": {"type": "object", "properties": {"day": {"type": "string"}}, "required": ["day"],
                       "additionalProperties": False}}}


class Heartbeat:
    """When to reflect, the reflection itself, and the daily digest, for one planner."""

    def __init__(self, memory: "memories.Memory", chat_fn: llm.ChatFn = llm.chat,
                 log: Callable[[dict[str, Any]], None] | None = None, every_min: float = HEARTBEAT_MIN):
        self.memory = memory
        self.chat = chat_fn
        self.log = log or (lambda _: None)
        self.every_s = every_min * 60
        self.last = -1e9            # monotonic time of the last reflection
        self.pending: list[str] = ["the session started"]
        self.area = ""
        self.visited: set[str] = set()
        self.focus = ""
        self.focus_t = ""
        self.beats = 0
        self.digests = 0
        self.cost = 0.0

    def note(self, event: str) -> None:
        """An event worth reflecting on at the next step."""
        if event not in self.pending:
            self.pending.append(event)

    def saw_area(self, area: str) -> None:
        """A first visit this session to an area is an event; coming back to one isn't."""
        if area and area != "open country" and area not in self.visited:
            if self.visited:
                self.note(f"a new area: {area}")
            self.visited.add(area)
        self.area = area or self.area

    def due(self) -> str:
        """Why a reflection is due now, or ""."""
        since = time.monotonic() - self.last
        if self.pending and since >= MIN_GAP_MIN * 60:
            return "; ".join(self.pending)
        if since >= self.every_s:
            return f"{self.every_s / 60:.0f} minutes since the last"
        return ""

    async def beat(self, situation: dict[str, Any], goal: str, long_goals: str, summary: str) -> bool:
        why = self.due()
        if not why or not self.memory.character:
            return False
        if costs.ledger.over_budget():
            self.pending.clear()
            self.last = time.monotonic()
            self.log({"type": "heartbeat", "t": time.time(), "why": why, "skipped": "over budget"})
            return False
        self.pending.clear()
        self.last = time.monotonic()
        await self.daily()
        recent = self.memory.list(min_importance=3, limit=12)
        user = (f"Why now: {why}\n\nThe player's goal: {goal or '(none for this session)'}\n\n"
                f"{long_goals or 'No long goals.'}\n\n"
                f"This session so far: {summary or '(just started)'}\n\n"
                f"Your focus until now: {self.focus or '(none yet)'}\n\n"
                "The most important recent memories, newest first:\n"
                + ("\n".join(f"- {memories.words(m)}" for m in recent) or "(none)")
                + f"\n\nThe character now: {json.dumps(situation)}\n\nCall reflect.")
        rec: dict[str, Any] = {"type": "heartbeat", "t": time.time(), "why": why}
        try:
            with costs.kind("heartbeat"):
                res = await self.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                                      tools=[reflect_tool()], tool_choice="reflect", model=llm.planner_model(None),
                                      max_tokens=1000)
        except llm.LlmError as e:
            self.log(rec | {"error": str(e)[:300]})
            return False
        call = next((c for c in res.tool_calls if c.name == "reflect"), None)
        args = (call.arguments if call else None) or {}
        kept = []
        for lesson in (args.get("lessons") or [])[:3]:
            if str(lesson.get("text") or "").strip():
                try:
                    kept.append(self.memory.add(str(lesson["text"]), kind="lesson", area=lesson.get("area") or None,
                                                source="heartbeat", importance=int(lesson.get("importance") or 3)))
                except (ValueError, TypeError):
                    continue
        if focus := " ".join(str(args.get("focus") or "").split())[:FOCUS_CAP]:
            self.focus, self.focus_t = focus, now()
        self.beats += 1
        self.cost += res.usage.cost or 0.0
        self.log(rec | {"lessons": kept, "focus": self.focus, "usage": res.usage.to_log()})
        return True

    async def daily(self) -> int:
        """Digest each earlier day of memories not yet digested (at most three days at once)."""
        today = datetime.now().strftime("%Y-%m-%d")
        rows = self.memory.db.execute(
            "SELECT * FROM memories WHERE character = ? AND stale = 0 AND source != 'daily' ORDER BY t",
            (self.memory.character,)).fetchall()
        by_day: dict[str, list[Any]] = {}
        for r in rows:
            day = datetime.fromisoformat(r["t"]).astimezone().strftime("%Y-%m-%d")
            if day < today:
                by_day.setdefault(day, []).append(r)
        done = {r["text"].split(":", 1)[0].removeprefix("Day ") for r in self.memory.db.execute(
            "SELECT text FROM memories WHERE character = ? AND source = 'daily'", (self.memory.character,))}
        n = 0
        for day in sorted(d for d in by_day if d not in done)[-3:]:
            lines = "\n".join(f"- [{r['kind']}, {datetime.fromisoformat(r['t']).astimezone().strftime('%H:%M')}] {r['text']}"
                              for r in by_day[day][-80:])
            try:
                with costs.kind("daily"):
                    res = await self.chat([{"role": "system", "content": DAILY},
                                           {"role": "user", "content": f"The memories of {day}:\n{lines}\n\nCall digest."}],
                                          tools=[digest_tool()], tool_choice="digest", model=llm.planner_model(None),
                                          max_tokens=800)
            except llm.LlmError as e:
                self.log({"type": "daily", "t": time.time(), "day": day, "error": str(e)[:300]})
                break
            call = next((c for c in res.tool_calls if c.name == "digest"), None)
            text = " ".join(str(((call.arguments if call else None) or {}).get("day") or "").split())
            if not text:
                continue
            self.memory.add(f"Day {day}: {text}", kind="event", tags=("day",), source="daily", importance=3)
            self.digests += 1
            n += 1
            self.cost += res.usage.cost or 0.0
            self.log({"type": "daily", "t": time.time(), "day": day, "memories": len(by_day[day]), "text": text,
                      "usage": res.usage.to_log()})
        return n

    def section(self) -> str:
        if not self.focus:
            return ""
        return f"Your focus, from your last reflection ({memories.ago(self.focus_t)}): {self.focus}"

    def to_log(self) -> dict[str, Any]:
        return {"heartbeats": self.beats, "daily_digests": self.digests, "heartbeat_cost_usd": round(self.cost, 5)}
