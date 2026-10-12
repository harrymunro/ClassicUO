"""Long goals: what a character works towards across sessions (cuo-6w8.5).

A session goal is one sitting's ("hunt the graveyard, bank the gold"). A long goal outlives
it: "reach 90 swordsmanship", "save 20,000 gold for a house", "learn the dungeons near
Britain". They are kept with the character's memories, in the shard's store
(brain/worlds/<shard>/world.sqlite, table long_goals), one row per goal and character.

Code measures progress where it can:

    skill    a skill's value, from the snapshot ("reach 90 swordsmanship")
    gold     gold banked since the goal was set, from the bank goals' results ("save 20000 gold")
    places   places visited: arrived at or hunted in ("visit Covetous, Shame and Despise", or
             "learn the dungeons near Britain": dungeon entrances within 300 tiles of it)
    other    nothing to measure: the planner says when it is done

and notes milestones on the way (a quarter, half, three quarters), each kept as a memory.
A measured goal that reaches its target is marked done by code. The planner sees the
active goals and their progress at every call, chooses the session's goal from them when
the player gave none, and can add, change, finish or drop one (the `long_goal` tool), with
its reason logged. The player sets them from the panel, `-agent longgoal`, or
`uo-brain goals`; the client shows them as the brain sends them (the `long_goals` RPC).
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from .world import World, WorldError, now, tiles

SCHEMA = """
CREATE TABLE IF NOT EXISTS long_goals (
    id INTEGER PRIMARY KEY, character TEXT NOT NULL, text TEXT NOT NULL, kind TEXT NOT NULL,
    target TEXT, progress TEXT, status TEXT NOT NULL DEFAULT 'active', source TEXT NOT NULL,
    created TEXT NOT NULL, updated TEXT NOT NULL, done_t TEXT, why TEXT);
CREATE INDEX IF NOT EXISTS long_goals_character ON long_goals(character, status);
"""

KINDS = ("skill", "gold", "places", "other")
MILESTONES = (0.25, 0.5, 0.75)
NEAREST = 3            # "the dungeons near Britain": the three entrances nearest it
DUNGEON_RADIUS = 700   # without a store to pick them from: any dungeon this close to the town

SKILLS = ("Swordsmanship", "Mace Fighting", "Fencing", "Archery", "Wrestling", "Tactics", "Anatomy", "Healing",
          "Parrying", "Focus", "Resisting Spells", "Magery", "Evaluating Intelligence", "Meditation",
          "Animal Taming", "Animal Lore", "Veterinary", "Musicianship", "Provocation", "Peacemaking",
          "Discordance", "Necromancy", "Spirit Speak", "Chivalry")
SKILL_WORDS = {s.lower(): s for s in SKILLS} | {
    "swords": "Swordsmanship", "sword": "Swordsmanship", "macing": "Mace Fighting", "mace": "Mace Fighting",
    "resist": "Resisting Spells", "resisting": "Resisting Spells", "eval int": "Evaluating Intelligence",
    "eval": "Evaluating Intelligence", "evaluate intelligence": "Evaluating Intelligence", "med": "Meditation",
    "taming": "Animal Taming", "lore": "Animal Lore", "vet": "Veterinary", "music": "Musicianship",
    "provo": "Provocation", "peace": "Peacemaking", "discord": "Discordance", "necro": "Necromancy",
    "spirit": "Spirit Speak", "parry": "Parrying"}


def ensure(world: World) -> None:
    world.db.executescript(SCHEMA)
    world.db.commit()


def parse(text: str, world: World | None = None) -> tuple[str, dict[str, Any]]:
    """What a long goal in words can be measured by: (kind, target)."""
    t = " ".join(text.lower().split())
    # Gold first: "save 10,000 gold for a vet's stable" is about gold, not Veterinary 10.
    if m := re.search(r"(\d{1,3}(?:[,.]\d{3})+|\d+(?:\.\d+)?)\s*(k|thousand)?\s*(?:gp|gold)", t):
        raw = m.group(1)
        amount = float(re.sub(r"[,.](?=\d{3}\b)", "", raw))  # 20,000 and 20.000 are both twenty thousand
        if m.group(2):
            amount *= 1000
        if amount >= 1:
            return "gold", {"gold": int(amount)}
    # A skill and its number side by side: "90 swordsmanship", "swordsmanship to 90", "magery 100".
    for word in sorted(SKILL_WORDS, key=len, reverse=True):
        w = re.escape(word)
        m = re.search(rf"\b(\d{{2,3}}(?:\.\d)?)\s*(?:in\s+|points?\s+(?:of|in)\s+)?{w}\b", t) or \
            re.search(rf"\b{w}\s*(?:to|at|of|up to)?\s*(\d{{2,3}}(?:\.\d)?)\b", t)
        if m and 0 < float(m.group(1)) <= 120:
            return "skill", {"skill": SKILL_WORDS[word], "value": float(m.group(1))}
    if re.search(r"\b(visit|explore|learn|see|find)\b", t):
        near = re.search(r"dungeons?\s+(?:near|around|by|of)\s+([a-z' ]+?)(?:[,.;]|$)", t)
        if near:
            town = near.group(1).strip().title()
            # The few dungeon entrances nearest the town, fixed when the goal is set.
            if world is not None:
                try:
                    found = world.find_place("dungeon_entrance", near=town, limit=NEAREST)
                except WorldError:
                    found = []
                if found:
                    return "places", {"places": [f["name"] for f in found], "near": town}
            return "places", {"place_kind": "dungeon_entrance", "near": town, "count": NEAREST}
        if world is not None:
            after = re.split(r"\b(?:visit|explore|learn|see|find)\b", t, maxsplit=1)[1]
            names = [n.strip(" .") for n in re.split(r",|\band\b", after) if n.strip(" .")]
            found = []
            for n in names:
                hit = world.place(n, limit=1)
                if hit and hit[0].get("match", 0) >= 0.8:
                    found.append(hit[0]["name"])
            if found:
                return "places", {"places": found}
    return "other", {}


class LongGoals:
    """One character's long goals in a shard's store."""

    def __init__(self, world: World, character: str | None = None):
        self.world = world
        self.character = character
        ensure(world)

    @property
    def db(self):
        return self.world.db

    def _who(self) -> str:
        if not self.character:
            raise WorldError("long goals belong to a character: name one")
        return self.character

    # ---- writing ------------------------------------------------------------------

    def add(self, text: str, source: str = "player", why: str = "", kind: str | None = None,
            target: dict[str, Any] | None = None) -> int:
        text = " ".join(str(text).split())
        if not text:
            raise WorldError("a long goal needs some text")
        if kind is None:
            kind, target = parse(text, self.world)
        if kind not in KINDS:
            raise WorldError(f"a long goal's kind is one of {', '.join(KINDS)}, not {kind!r}")
        stamp = now()
        cur = self.db.execute(
            "INSERT INTO long_goals (character, text, kind, target, progress, source, created, updated, why) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self._who(), text, kind, json.dumps(target or {}), json.dumps({"milestones": []}), source, stamp,
             stamp, why or None))
        self.db.commit()
        return cur.lastrowid

    def _set(self, goal_id: int, **cols: Any) -> bool:
        cols["updated"] = now()
        sets = ", ".join(f"{k} = ?" for k in cols)
        n = self.db.execute(f"UPDATE long_goals SET {sets} WHERE id = ? AND character = ?",
                            (*cols.values(), goal_id, self._who())).rowcount
        self.db.commit()
        return n > 0

    def done(self, goal_id: int, why: str = "") -> bool:
        return self._set(goal_id, status="done", done_t=now(), why=why or None)

    def drop(self, goal_id: int, why: str = "") -> bool:
        return self._set(goal_id, status="dropped", done_t=now(), why=why or None)

    def change(self, goal_id: int, text: str, why: str = "") -> bool:
        """New words for a goal; what it is measured by is read again from them, and progress starts over."""
        text = " ".join(str(text).split())
        if not text:
            raise WorldError("a long goal needs some text")
        kind, target = parse(text, self.world)
        return self._set(goal_id, text=text, kind=kind, target=json.dumps(target), why=why or None,
                         progress=json.dumps({"milestones": []}))

    # ---- reading ------------------------------------------------------------------

    def list(self, status: str | None = "active", all_characters: bool = False) -> list[dict[str, Any]]:
        where, args = [], []
        if not all_characters:
            where.append("character = ?")
            args.append(self._who())
        if status:
            where.append("status = ?")
            args.append(status)
        rows = self.db.execute(f"SELECT * FROM long_goals {'WHERE ' + ' AND '.join(where) if where else ''} "
                               f"ORDER BY id", args).fetchall()
        return [{"id": r["id"], "character": r["character"], "text": r["text"], "kind": r["kind"],
                 "target": json.loads(r["target"] or "{}"), "progress": json.loads(r["progress"] or "{}"),
                 "status": r["status"], "source": r["source"], "created": r["created"], "updated": r["updated"],
                 "done_t": r["done_t"], "why": r["why"]} for r in rows]

    def get(self, goal_id: int) -> dict[str, Any] | None:
        return next((g for g in self.list(status=None) if g["id"] == goal_id), None)

    # ---- progress -----------------------------------------------------------------

    def _progress(self, g: dict[str, Any], prog: dict[str, Any]) -> list[dict[str, Any]]:
        """Store progress; the milestones it newly passed, and done when the target is reached."""
        events = []
        frac = fraction(g["kind"], g["target"], prog)
        if frac is not None:
            for m in MILESTONES:
                if frac >= m and m not in prog["milestones"]:
                    prog["milestones"].append(m)
                    events.append({"goal": g["id"], "text": g["text"], "milestone": m, "progress": words(g | {"progress": prog})})
        self._set(g["id"], progress=json.dumps(prog))
        if frac is not None and frac >= 1.0:
            self.done(g["id"], "reached, measured by code")
            events.append({"goal": g["id"], "text": g["text"], "milestone": 1.0, "done": True,
                           "progress": words(g | {"progress": prog})})
        return events

    def observe(self, snap: dict[str, Any]) -> list[dict[str, Any]]:
        """Skill goals' progress from a snapshot."""
        if not self.character or not snap.get("in_game"):
            return []
        skills = (snap.get("player") or {}).get("skills") or {}
        events = []
        for g in self.list():
            if g["kind"] != "skill":
                continue
            value = skills.get(g["target"].get("skill"))
            if value is None:
                continue
            prog = g["progress"]
            if prog.get("now") == value and "start" in prog:
                continue
            prog.setdefault("start", value)
            prog["now"] = value
            events += self._progress(g, prog)
        return events

    def note_result(self, tool: str, args: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
        """Gold and places goals' progress from a goal's result."""
        if not self.character or not result.get("ok", False) and tool != "hunt":
            return []
        events = []
        deposited = 0
        if tool == "bank" and isinstance(result.get("gold_before"), int) and isinstance(result.get("gold_carried"), int):
            deposited = max(0, result["gold_before"] - result["gold_carried"])
        place = None
        if tool == "travel_to" and result.get("ok"):
            place = str(args.get("place") or "")
        elif tool == "hunt" and result.get("minutes"):
            place = str(args.get("area") or "")
        for g in self.list():
            prog = g["progress"]
            if g["kind"] == "gold" and deposited:
                prog["banked"] = prog.get("banked", 0) + deposited
                events += self._progress(g, prog)
            elif g["kind"] == "places" and place and visits(self.world, g["target"], place, prog):
                events += self._progress(g, prog)
        return events

    # ---- for the planner and the panel -------------------------------------------

    def section(self, session_goal: str = "") -> str:
        goals = self.list() if self.character else []
        if not goals:
            return ""
        lines = ["The character's long goals (kept across sessions; ids for the long_goal tool):"]
        lines += [f"{g['id']}. {g['text']}: {words(g)}" for g in goals]
        if not session_goal:
            lines.append("The player gave no goal for this session: choose what to work on from these.")
        return "\n".join(lines)

    def panel(self) -> list[dict[str, Any]]:
        return [{"id": g["id"], "text": g["text"], "progress": words(g)} for g in self.list()] if self.character else []

    async def sync(self, rpc: Any, log: Any = None) -> list[dict[str, Any]]:
        """Show the goals in the client's panel and apply what the player asked there (add, done,
        drop). Returns the requests applied. A client without the RPC is left alone."""
        if not self.character:
            return []
        try:
            res = await rpc.call("long_goals", goals=self.panel())
        except Exception:
            return []
        applied = []
        for r in (res or {}).get("requests") or []:
            action, gid, text = r.get("action"), r.get("id"), str(r.get("text") or "")
            try:
                if action == "add" and text:
                    if any(g["text"].lower() == " ".join(text.split()).lower() for g in self.list()):
                        continue  # already there: two syncs fetched the same request before either acked it
                    gid = self.add(text, source="player")
                elif action == "done" and gid:
                    self.done(int(gid), "the player marked it done")
                elif action == "drop" and gid:
                    self.drop(int(gid), "the player dropped it")
                else:
                    continue
            except (WorldError, ValueError):
                continue
            applied.append({"action": action, "id": gid, "text": text})
            if log:
                log({"type": "long_goal", "t": time.time(), "by": "player", "action": action, "id": gid, "text": text})
        acked = max((int(r.get("seq", 0)) for r in (res or {}).get("requests") or []), default=0)
        if acked:
            try:
                await rpc.call("long_goals", goals=self.panel(), ack=acked)
            except Exception:
                pass
        return applied


def fraction(kind: str, target: dict[str, Any], prog: dict[str, Any]) -> float | None:
    """How far along a measured goal is, 0 to 1; None when it can't be measured (yet)."""
    if kind == "skill" and "now" in prog and target.get("value"):
        start, goal = prog.get("start", prog["now"]), float(target["value"])
        if goal <= start:
            return 1.0 if prog["now"] >= goal else 0.0
        return max(0.0, min(1.0, (prog["now"] - start) / (goal - start)))
    if kind == "gold" and target.get("gold"):
        return max(0.0, min(1.0, prog.get("banked", 0) / float(target["gold"])))
    if kind == "places":
        need = len(target.get("places") or []) or int(target.get("count") or 0)
        return min(1.0, len(prog.get("visited") or []) / need) if need else None
    return None


def visits(world: World, target: dict[str, Any], place: str, prog: dict[str, Any]) -> bool:
    """Count a place visited towards a places goal; whether it counted."""
    visited = prog.setdefault("visited", [])
    if names := target.get("places"):
        for n in names:
            if n.lower() in place.lower() or place.lower() in n.lower():
                if n not in visited:
                    visited.append(n)
                    return True
        return False
    if target.get("place_kind") == "dungeon_entrance":
        hit = world.place(place, limit=1)
        if not hit:
            return False
        h = hit[0]
        region = world.region_at(h["x"], h["y"])
        if h.get("kind") not in ("dungeon_entrance", "dungeon", "dungeon_level") and \
                not (region and region.get("kind") == "dungeon"):
            return False
        centre = world.place(target.get("near") or "", limit=1)
        if centre and tiles(h["x"], h["y"], centre[0]["x"], centre[0]["y"]) > DUNGEON_RADIUS:
            return False
        name = (region or {}).get("part_of") or (region or {}).get("name") or h["name"]
        if name not in visited:
            visited.append(name)
            return True
    return False


def words(g: dict[str, Any]) -> str:
    """Progress in words: 'now 82.4 (from 80.0), half way', '6,200 of 20,000 gold banked'."""
    prog, target = g.get("progress") or {}, g.get("target") or {}
    if g.get("status") == "done":
        return "done"
    match g.get("kind"):
        case "skill":
            if "now" not in prog:
                return f"{target.get('skill')} to {target.get('value')}: not measured yet"
            out = f"{target['skill']} now {prog['now']} (from {prog.get('start', prog['now'])}, target {target['value']})"
        case "gold":
            out = f"{prog.get('banked', 0):,} of {target.get('gold', 0):,} gold banked since it was set"
        case "places":
            visited = prog.get("visited") or []
            need = target.get("places") or f"{target.get('count')} {target.get('place_kind', 'place').replace('_', ' ')}s near {target.get('near')}"
            if target.get("places") and target.get("near"):
                need = f"{', '.join(target['places'])} (the dungeons nearest {target['near']})"
            out = f"visited {', '.join(visited) or 'none yet'} of {need if isinstance(need, str) else ', '.join(need)}"
        case _:
            return "not measured: say when it is done"
    if ms := prog.get("milestones"):
        out += f"; past {int(max(ms) * 100)}%"
    return out


def tool() -> dict[str, Any]:
    return {"type": "function", "function": {
        "name": "long_goal",
        "description": "Change the character's long goals (the ones that outlive this session): add one, mark one "
                       "done, reword it, or drop it, with the reason. Code measures skills, gold banked and places "
                       "visited and marks those done itself; use done for goals code can't measure. Not a goal for "
                       "now: choose the next goal after.",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["add", "done", "change", "drop"]},
            "id": {"type": "integer", "description": "The long goal's id (not for add)."},
            "text": {"type": "string", "description": "add and change: the goal in words, e.g. \"reach 90 swordsmanship\"."},
            "why": {"type": "string", "description": "One sentence: the reason."}},
            "required": ["action", "why"], "additionalProperties": False}}}


def call_tool(goals: "LongGoals | None", args: dict[str, Any] | str | None) -> dict[str, Any]:
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except ValueError:
            return {"error": "the arguments weren't JSON"}
    args = args or {}
    if goals is None or not goals.character:
        return {"error": "no long goals in this session"}
    action, why = str(args.get("action") or ""), str(args.get("why") or "")
    try:
        match action:
            case "add":
                gid = goals.add(str(args.get("text") or ""), source="planner", why=why)
                return {"ok": True, "id": gid, "kind": goals.get(gid)["kind"]}
            case "done" | "drop" | "change":
                gid = int(args.get("id") or 0)
                ok = goals.done(gid, why) if action == "done" else goals.drop(gid, why) if action == "drop" \
                    else goals.change(gid, str(args.get("text") or ""), why)
                return {"ok": True, "id": gid} if ok else {"error": f"no long goal {gid} for this character"}
    except (WorldError, ValueError, TypeError) as e:
        return {"error": str(e)}
    return {"error": "action is one of add, done, change, drop"}


def milestone_note(event: dict[str, Any]) -> str:
    if event.get("done"):
        return f"Reached a long goal: {event['text']} ({event['progress']})."
    return f"{int(event['milestone'] * 100)}% of the way to a long goal: {event['text']} ({event['progress']})."

