"""A character's own memory: what happened to it and what it learned, kept across sessions.

The world store (world.py) is the shard's knowledge: places, spawns, notes anyone could
use. Memories are one character's: "died to a lich at the Britain graveyard", "the Britain
healer was sold out in the evening", "pulling one zombie at a time worked". They live in the
same file as the shard's world (brain/worlds/<shard>/world.sqlite), so they are scoped by
shard, and every row names its character.

Each memory has a kind:

    event     something that happened (a hunt that ended in a leave, a death, a refused goal)
    lesson    something learned that should change later choices
    order     a standing order from the player ("never hunt in Despise")
    relationship  a conversation with another character the NPC knows (talk.py)

and says where it came from (`source`): goal (code, when a goal ended), death, leave,
refusal, planner (the planner's remember tool), review (an accepted after-action review
line), flush (kept before old planner steps were folded away), player (uo-brain memory add).
`importance` runs from 1 (routine) to 5 (a death). A memory repeated within the hour is
counted on the row it repeats (`times`, `last_t`) rather than stored again, so a planner
that keeps walking into the same refusal shows up as one memory seen many times.

Forgetting marks a memory stale: it stops being shown but the history stays. As with
danger sightings, memories are for the planner and never reach fight-time facts.
"""

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from .world import World, WorldError, now

KINDS = ("event", "lesson", "order", "relationship")
SOURCES = ("goal", "death", "leave", "refusal", "planner", "review", "flush", "player", "heartbeat", "daily", "talk")
REPEAT_WITHIN = timedelta(hours=1)

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY, character TEXT NOT NULL, t TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
    area TEXT, x INTEGER, y INTEGER, tags TEXT, source TEXT NOT NULL, importance INTEGER NOT NULL DEFAULT 2,
    session TEXT, times INTEGER NOT NULL DEFAULT 1, last_t TEXT NOT NULL, stale INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS memories_character ON memories(character, stale, last_t);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(text, tags, area, content='memories', content_rowid='id',
    tokenize='porter unicode61');
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts(rowid, text, tags, area) VALUES (new.id, new.text, new.tags, new.area);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, text, tags, area) VALUES ('delete', old.id, old.text, old.tags, old.area);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE OF text, tags, area ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, text, tags, area) VALUES ('delete', old.id, old.text, old.tags, old.area);
  INSERT INTO memories_fts(rowid, text, tags, area) VALUES (new.id, new.text, new.tags, new.area);
END;
"""


@dataclass
class Note:
    """A memory before it is stored: what code or the planner wants kept."""
    text: str
    kind: str = "event"
    area: str | None = None
    x: int | None = None
    y: int | None = None
    tags: tuple[str, ...] = ()
    source: str = "goal"
    importance: int = 2


def ensure(world: World) -> bool:
    """The memories table in a world store; whether keyword search (FTS5) is there."""
    world.db.executescript(SCHEMA)
    try:
        world.db.executescript(FTS_SCHEMA)
        fts = True
    except Exception:  # sqlite3.OperationalError: no FTS5 in this SQLite
        fts = False
    world.db.commit()
    return fts


def stamp_of(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")


def ago(stamp: str, at: datetime | None = None) -> str:
    """'12 min ago', '3 h ago', '2 days ago' for a stored time."""
    try:
        then = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return "some time ago"
    s = max(0.0, ((at or datetime.now(timezone.utc)) - then).total_seconds())
    if s < 90:
        return "just now"
    if s < 90 * 60:
        return f"{s / 60:.0f} min ago"
    if s < 36 * 3600:
        return f"{s / 3600:.0f} h ago"
    return f"{s / 86400:.0f} days ago"


class Memory:
    """One character's memories in a shard's store."""

    def __init__(self, world: World, character: str | None = None, session: str | None = None):
        self.world = world
        self.character = character
        self.session = session  # the log the memories came from, when there is one
        self.fts = ensure(world)
        self.written = 0        # memories written (or repeated) through this object

    @property
    def db(self):
        return self.world.db

    def _who(self, character: str | None) -> str:
        who = character or self.character
        if not who:
            raise WorldError("memories belong to a character: name one")
        return who

    # ---- writing ------------------------------------------------------------------

    def add(self, text: str, kind: str = "event", area: str | None = None, x: int | None = None, y: int | None = None,
            tags: Iterable[str] = (), source: str = "planner", importance: int = 2,
            character: str | None = None, at: float | None = None) -> int:
        """Keep a memory; the id. The same text for the same character within the hour is counted
        on the earlier row instead (its id comes back). `at` (epoch seconds) dates it, for imports."""
        who = self._who(character)
        text = " ".join(str(text).split())
        if not text:
            raise WorldError("a memory needs some text")
        if kind not in KINDS:
            raise WorldError(f"a memory's kind is one of {', '.join(KINDS)}, not {kind!r}")
        importance = max(1, min(5, int(importance)))
        stamp = stamp_of(at) if at else now()
        when = datetime.fromisoformat(stamp)
        since = (when - REPEAT_WITHIN).isoformat(timespec="seconds")
        until = (when + REPEAT_WITHIN).isoformat(timespec="seconds")  # an import can date a memory before others
        same = self.db.execute("SELECT id, importance FROM memories WHERE character = ? AND text = ? AND stale = 0 "
                               "AND last_t >= ? AND t <= ? ORDER BY id DESC LIMIT 1",
                               (who, text, since, until)).fetchone()
        self.written += 1
        if same:
            self.db.execute("UPDATE memories SET times = times + 1, last_t = MAX(last_t, ?), t = MIN(t, ?), "
                            "importance = MAX(importance, ?) WHERE id = ?", (stamp, stamp, importance, same["id"]))
            self.db.commit()
            return same["id"]
        tag_list = sorted({t.strip().lower() for t in tags if t and str(t).strip()})
        cur = self.db.execute(
            "INSERT INTO memories (character, t, kind, text, area, x, y, tags, source, importance, session, last_t) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (who, stamp, kind, text, area or None, x, y, json.dumps(tag_list) if tag_list else None, source,
             importance, self.session, stamp))
        self.db.commit()
        return cur.lastrowid

    def keep(self, note: Note, at: float | None = None) -> int:
        return self.add(note.text, note.kind, note.area, note.x, note.y, note.tags, note.source, note.importance,
                        at=at)

    def forget(self, memory_id: int, character: str | None = None) -> bool:
        """Mark a memory stale (kept in the history, never shown again). Another character's
        memories can't be forgotten through this one."""
        who = character or self.character
        sql, args = "UPDATE memories SET stale = 1 WHERE id = ? AND stale = 0", [memory_id]
        if who:
            sql += " AND character = ?"
            args.append(who)
        n = self.db.execute(sql, args).rowcount
        self.db.commit()
        return n > 0

    # ---- reading ------------------------------------------------------------------

    def list(self, kind: str | None = None, area: str | None = None, search: str | None = None,
             source: str | None = None, include_stale: bool = False, min_importance: int = 1,
             limit: int = 20, character: str | None = None, all_characters: bool = False,
             before: str | None = None) -> list[dict[str, Any]]:
        """Memories, newest first, or best keyword match first with `search`. `before` (a stored
        time) leaves out what was written later: what the character knew then, for replays."""
        where, args = [], []
        if before:
            where.append("m.t <= ?")
            args.append(before)
        if not all_characters:
            where.append("m.character = ?")
            args.append(self._who(character))
        if not include_stale:
            where.append("m.stale = 0")
        if kind:
            where.append("m.kind = ?")
            args.append(kind)
        if source:
            where.append("m.source = ?")
            args.append(source)
        if area:
            where.append("m.area LIKE ?")
            args.append(f"%{area}%")
        if min_importance > 1:
            where.append("m.importance >= ?")
            args.append(min_importance)
        words = [w for w in (re.sub(r"[^\w'-]", "", w) for w in (search or "").split()) if w]
        cond = "".join(" AND " + w for w in where)
        if words and self.fts:
            match = " OR ".join(f'"{w}"' for w in words)
            rows = self.db.execute(
                f"SELECT m.* FROM memories_fts JOIN memories m ON m.id = memories_fts.rowid WHERE memories_fts MATCH ?"
                f"{cond} ORDER BY bm25(memories_fts), m.last_t DESC LIMIT ?", [match, *args, limit]).fetchall()
        else:
            if words:
                where.append("(" + " OR ".join("m.text LIKE ?" for _ in words) + ")")
                args += [f"%{w}%" for w in words]
            rows = self.db.execute(
                f"SELECT m.* FROM memories m {'WHERE ' + ' AND '.join(where) if where else ''} "
                f"ORDER BY m.last_t DESC, m.id DESC LIMIT ?", [*args, limit]).fetchall()
        return [as_dict(r) for r in rows]

    def count(self, character: str | None = None, include_stale: bool = False) -> int:
        sql = "SELECT COUNT(*) FROM memories WHERE character = ?" + ("" if include_stale else " AND stale = 0")
        return self.db.execute(sql, (self._who(character),)).fetchone()[0]

    def characters(self) -> dict[str, int]:
        return {r[0]: r[1] for r in self.db.execute(
            "SELECT character, COUNT(*) FROM memories WHERE stale = 0 GROUP BY character ORDER BY 2 DESC")}


def as_dict(r: Any) -> dict[str, Any]:
    out = {"id": r["id"], "character": r["character"], "kind": r["kind"], "text": r["text"], "area": r["area"],
           "x": r["x"], "y": r["y"], "tags": json.loads(r["tags"]) if r["tags"] else [], "source": r["source"],
           "importance": r["importance"], "t": r["t"], "last_t": r["last_t"], "times": r["times"],
           "stale": bool(r["stale"])}
    return {k: v for k, v in out.items() if v not in (None, [], False) or k == "stale" and v}


def words(m: dict[str, Any], at: datetime | None = None) -> str:
    """One memory as the planner reads it: '[lesson, 2 days ago] ...' with how often it happened."""
    when = ago(m["last_t"], at)
    seen = f", {m['times']} times" if m.get("times", 1) > 1 else ""
    where = f" ({m['area']})" if m.get("area") and m["area"].lower() not in m["text"].lower() else ""
    return f"[{m['kind']}, {when}{seen}] {m['text']}{where}"


# ---- what code remembers when a goal ends ------------------------------------------

def from_goal(tool: str, args: dict[str, Any], result: dict[str, Any], at: float | None = None) -> list[Note]:
    """The memories a finished goal leaves, decided by code: hunts (how they went, and above all
    leaves and deaths), refusals, and goals that failed. Errands that went as planned leave
    nothing: the world store and the long goals' progress keep those."""
    text = str(result.get("result", ""))
    ok = bool(result.get("ok"))
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(at))
    notes: list[Note] = []
    if text.startswith("not going back near "):
        area = text.removeprefix("not going back near ").split(" yet", 1)[0]
        notes.append(Note(f"Tried to go back to {area} too soon after leaving it, and code refused.", area=area,
                          tags=("refusal",), source="refusal", importance=3))
        return notes
    if tool == "hunt":
        area = str(args.get("area") or "")
        kills, deaths = int(result.get("kills") or 0), int(result.get("deaths") or 0)
        why = str(result.get("stopped_because") or "")
        if deaths:
            notes.append(Note(f"Died hunting at {area} ({stamp}): {kills} kills first; {why}.", area=area,
                              tags=("death",), source="death", importance=5))
        elif why.startswith("left the area"):
            notes.append(Note(f"Had to leave {area} ({stamp}) after {kills} kills: {why.removeprefix('left the area').strip(' :')}.",
                              area=area, tags=("leave", "danger"), source="leave", importance=4))
        elif "minutes" in result:
            gold = int(result.get("gold_gained") or 0)
            notes.append(Note(f"Hunted {area} for {result.get('minutes')} min ({stamp}): {kills} kills, {gold} gold, "
                              f"{int(result.get('bandages_used') or 0)} bandages used; stopped because {why}.",
                              area=area, tags=("hunt",), source="goal", importance=2 if kills else 3))
        elif not ok:
            notes.append(Note(f"Couldn't hunt {area}: {text}", area=area, tags=("failed",), source="goal", importance=3))
        if seen := text.split("; stronger creatures seen: ", 1)[1] if "; stronger creatures seen: " in text else "":
            notes.append(Note(f"Stronger creatures seen: {seen}.", tags=("danger",), source="goal", importance=3))
        return notes
    if not ok and tool in ("travel_to", "bank", "buy", "sell"):
        what = {"travel_to": f"travel to {args.get('place')}", "bank": "bank",
                "buy": f"buy {args.get('count')} {args.get('item')}", "sell": f"sell {args.get('items', 'loot')}"}[tool]
        notes.append(Note(f"Failed to {what}: {text[:200]}", tags=("failed", tool), source="goal", importance=3))
    if "; stronger creatures seen: " in text:
        notes.append(Note(f"Stronger creatures seen: {text.split('; stronger creatures seen: ', 1)[1]}.",
                          tags=("danger",), source="goal", importance=3))
    return notes


def death(snap: dict[str, Any], area: str, last_goal: str = "", at: float | None = None) -> Note:
    p = snap.get("player") or {}
    about = sorted({str(m.get("name") or "a creature") for m in snap.get("mobiles", [])
                    if m.get("monster") and not m.get("dead") and m.get("distance", 99) <= 12})
    return Note(f"Died at {area} ({time.strftime('%Y-%m-%d %H:%M', time.localtime(at))})"
                + (f", with {', '.join(about[:5])} about" if about else "")
                + (f"; the goal was {last_goal}" if last_goal else "") + ".",
                area=area, x=p.get("x"), y=p.get("y"), tags=("death",), source="death", importance=5)


# ---- the planner's tool -----------------------------------------------------------

def remember_tool() -> dict[str, Any]:
    return {"type": "function", "function": {
        "name": "remember",
        "description": "Keep something for this character's later sessions: a lesson learned (what worked, what to "
                       "avoid, and why) or an event worth recalling. Code already keeps hunts, leaves, deaths and "
                       "refusals, so keep what code can't see: your conclusions. One fact per call, in a sentence. "
                       "It is not a goal: choose the next goal after.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string", "description": "The memory in one plain sentence, with the place and the reason."},
            "kind": {"type": "string", "enum": ["lesson", "event"]},
            "area": {"type": "string", "description": "The area it is about, if any (e.g. \"Britain Graveyard\")."},
            "importance": {"type": "integer", "description": "1 (minor) to 5 (must not be forgotten). Default 3."}},
            "required": ["text", "kind"], "additionalProperties": False}}}


def call_remember(mem: "Memory | None", args: dict[str, Any] | str | None) -> dict[str, Any]:
    """Run the planner's remember tool. Errors come back as {"error": ...} for the model to correct."""
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except ValueError:
            return {"error": "the arguments weren't JSON"}
    args = args or {}
    if mem is None:
        return {"error": "no memory store in this session"}
    kind = str(args.get("kind") or "lesson")
    try:
        mid = mem.add(str(args.get("text") or ""), kind=kind if kind in ("lesson", "event") else "lesson",
                      area=args.get("area") or None, source="planner", importance=int(args.get("importance") or 3))
    except (WorldError, ValueError, TypeError) as e:
        return {"error": str(e)}
    return {"ok": True, "id": mid, "kept": "remembered for later sessions"}


def recall_tool() -> dict[str, Any]:
    return {"type": "function", "function": {
        "name": "recall",
        "description": "Search this character's memories from all its sessions (hunts and how they ended, deaths, "
                       "refused or failed goals, lessons, the player's standing orders, earlier sessions' summaries) "
                       "for what bears on a question. Best matches first, with how long ago and how often.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "What you want to remember, e.g. \"what went wrong at the "
                                                        "Britain graveyard\" or \"where bandages were sold out\"."},
            "limit": {"type": "integer", "description": "How many to return (default 5)."}},
            "required": ["query"], "additionalProperties": False}}}


# ---- Jev picks what the planner sees -----------------------------------------------

KEEP = 5             # memories shown to the planner each call, besides the player's standing orders
ORDERS = 5           # standing orders, always shown
SHORTLIST = 20       # candidates Jev scores
THRESHOLD = 0.5      # Jev's yes needed to show a memory
TEXT_CAP = 300       # characters of one memory in the prompt


def candidates(mem: Memory, area: str | None, goal: str, recent: Iterable[str], before: str | None = None,
               limit: int = SHORTLIST, people: Iterable[str] = ()) -> list[dict[str, Any]]:
    """Memories that might matter now, generously: about the area the character is in, about
    the places in the last few steps, matching the goal's words, the most important ones, and
    the newest. Standing orders are left out: they are always shown."""
    from .facts import keywords
    out: dict[int, dict[str, Any]] = {}

    def add(rows: Iterable[dict[str, Any]]) -> None:
        for r in rows:
            if r["kind"] != "order" and len(out) < limit:
                out.setdefault(r["id"], r)

    for name in people:  # characters in view: what was said with them before
        add(mem.list(search=name, kind="relationship", limit=3, before=before))
    if area and area != "open country":
        add(mem.list(area=area, limit=10, before=before))
    for line in recent:
        if words := keywords(line, limit=4):
            add(mem.list(search=" ".join(words), limit=4, before=before))
    if goal and (words := keywords(goal)):
        add(mem.list(search=" ".join(words), limit=8, before=before))
    add(mem.list(min_importance=4, limit=8, before=before))
    add(mem.list(limit=8, before=before))
    return list(out.values())


def memory_question(goal: str) -> Any:
    def q(text: str) -> dict[str, Any]:
        return {
            "type": "noul",
            "instructions": {"role": "You help plan a character's session in the game Ultima Online by choosing which "
                                     "of its memories the planner should have in front of it.",
                             "question": "Should the planner keep this memory in mind for the character's next goal?",
                             "player_goal": goal or "(none given)", "memory": text},
            "criteria": {
                "true": "It bears on the next choice: it is about a place, vendor, route or creature the next goal may "
                        "involve, a mistake the planner could repeat now, or a lesson about what just happened.",
                "false": "It is about something the next choice won't touch, or the situation and recent steps "
                         "already show it.",
            },
        }

    return q


def fallback_order(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Without Jev: the most important first, then the newest."""
    newest = sorted(rows, key=lambda r: r["last_t"], reverse=True)
    return sorted(newest, key=lambda r: -r["importance"])


class Picker:
    """Chooses the few memories each planner call sees. Code shortlists (memories about the area,
    the recent steps, the goal, the most important and the newest), Jev answers one yes/no per
    candidate, and the best KEEP above THRESHOLD go into the user turn. The player's standing
    orders always do. Without a model judge (the rule judge, or an error) the most important
    and newest come instead. Every pick is logged (records of type "memories")."""

    def __init__(self, memory: Memory, judge: Any = None, log: Any = None, keep: int = KEEP,
                 threshold: float = THRESHOLD):
        self.memory = memory
        self.judge = judge
        self.log = log or (lambda rec: None)
        self.keep = keep
        self.threshold = threshold
        self.calls = 0
        self.input_tokens = 0
        self.cost = 0.0
        self.shown = 0

    async def pick(self, situation: dict[str, Any], goal: str, recent: list[str],
                   before: str | None = None, people: Iterable[str] = ()) -> dict[str, list[dict[str, Any]]]:
        """{"orders": [...], "picked": [...]} for this moment."""
        from . import costs
        from .facts import can_rank, score
        mem = self.memory
        if not mem.character:
            return {"orders": [], "picked": []}
        orders = mem.list(kind="order", limit=ORDERS, before=before)
        area = (situation.get("position") or {}).get("area")
        rows = candidates(mem, area, goal, recent[-3:], before=before, people=people)
        rec: dict[str, Any] = {"type": "memories", "t": time.time(), "character": mem.character,
                               "shortlist": [r["id"] for r in rows], "orders": [r["id"] for r in orders]}
        picked: list[dict[str, Any]] = []
        if rows and can_rank(self.judge):
            state = {"player_goal": goal or "(none given)", "character_now": situation,
                     "last_steps": [line[:300] for line in recent[-3:]] or ["(nothing yet)"]}
            try:
                with costs.kind("memory"):
                    scores, tokens, latency, cost = await score(self.judge, state, [words(r)[:TEXT_CAP] for r in rows],
                                                                memory_question(goal))
                order = sorted(range(len(rows)), key=lambda i: -scores[i])
                picked = [{**rows[i], "relevance": round(scores[i], 2)} for i in order[:self.keep]
                          if scores[i] >= self.threshold]
                self.calls += 1
                self.input_tokens += tokens
                self.cost += cost
                rec.update({"scores": {rows[i]["id"]: round(scores[i], 3) for i in range(len(rows))},
                            "input_tokens": tokens, "cost_usd": round(cost, 6), "latency_ms": round(latency, 1)})
            except Exception as e:  # the most important and newest, rather than nothing
                rec["error"] = f"{type(e).__name__}: {e}"[:300]
                picked = fallback_order(rows)[:self.keep]
        elif rows:
            picked = fallback_order(rows)[:self.keep]
            rec["fallback"] = True
        rec["picked"] = [r["id"] for r in picked]
        self.shown += len(picked) + len(orders)
        self.log(rec)
        return {"orders": orders, "picked": picked}

    async def recall(self, query: str, goal: str = "", limit: int = 5) -> list[dict[str, Any]] | dict[str, Any]:
        """The planner's recall tool: keyword matches in the memories, Jev re-ranking them against
        the query when there are more than asked for."""
        from . import costs
        from .facts import can_rank, keywords, rerank_question, score
        mem = self.memory
        if not mem.character:
            return {"error": "the character isn't known yet"}
        try:
            limit = max(1, min(int(limit or 5), 15))
        except (TypeError, ValueError):
            limit = 5  # "limit": "all" and the like
        found = mem.list(search=" ".join(keywords(query, limit=8)) or query, limit=max(3 * limit, 15))
        rec: dict[str, Any] = {"type": "recall", "t": time.time(), "query": query, "found": len(found)}
        if len(found) > limit and can_rank(self.judge):
            try:
                with costs.kind("memory"):
                    scores, tokens, _, cost = await score(self.judge, {"planner_question": query, "player_goal": goal},
                                                          [words(r)[:TEXT_CAP] for r in found],
                                                          rerank_question(query, goal))
                found = [found[i] for i in sorted(range(len(found)), key=lambda i: -scores[i])]
                self.calls += 1
                self.input_tokens += tokens
                self.cost += cost
                rec.update({"input_tokens": tokens, "cost_usd": round(cost, 6)})
            except Exception as e:
                rec["error"] = f"{type(e).__name__}: {e}"[:300]
        out = [{"memory": words(r), "id": r["id"]} for r in found[:limit]]
        rec["returned"] = [o["id"] for o in out]
        self.log(rec)
        return out or {"memories": [], "note": "nothing remembered about that"}


def section(chosen: dict[str, list[dict[str, Any]]], at: datetime | None = None) -> str:
    """The memories for the planner's user turn, or "" when there are none."""
    lines = []
    if chosen.get("orders"):
        lines.append("The player's standing orders:")
        lines += [f"- {r['text'][:TEXT_CAP]}" for r in chosen["orders"]]
    if chosen.get("picked"):
        lines.append("What you remember that bears on this (from earlier goals and sessions):")
        lines += [f"- {words(r, at)[:TEXT_CAP]}" for r in chosen["picked"]]
    return "\n".join(lines)


# ---- earlier sessions' logs ---------------------------------------------------------

CODE_SOURCES = ("goal", "death", "leave", "refusal")


def import_log(mem: Memory, records: list[dict[str, Any]], session: str) -> dict[str, Any]:
    """Backfill a character's memories from an earlier session's log: what code would have kept
    as each goal ended, dated when it happened. Importing the same log again replaces its rows.
    A log whose session kept memories as it ran is left alone: those are already there, with more
    in them (a death's position, milestones) than an import can rebuild."""
    who = mem._who(None)
    if mem.db.execute("SELECT 1 FROM memories WHERE character = ? AND session = ? LIMIT 1", (who, session)).fetchone():
        return {"session": session, "memories": 0, "skipped": "this session's memories were kept as it ran"}
    key = f"import:{session}"
    mem.db.execute(f"DELETE FROM memories WHERE character = ? AND session = ? AND source IN "
                   f"({', '.join('?' for _ in CODE_SOURCES)})", (who, key, *CODE_SOURCES))
    mem.db.commit()
    saved, mem.session = mem.session, key
    n = 0
    args: dict[str, Any] = {}
    try:
        for r in records:
            if r.get("type") == "goal":
                args = r.get("args") or {}
            elif r.get("type") == "goal_result":
                for note in from_goal(r.get("tool", ""), args, r.get("result") or {}, at=r.get("t")):
                    mem.keep(note, at=r.get("t"))
                    n += 1
                args = {}
    finally:
        mem.session = saved
    return {"session": session, "memories": n}
