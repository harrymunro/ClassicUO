"""The party huddle: the members stop, agree a target, roles and a rally point, then each plans for itself (cuo-bih).

The other way to one plan. With the leader's plan (party.py, cuo-4ar) one planner sets every goal
and the others follow in code, which keeps five together but leaves a follower's persona, long
goals and memories out of what it does. Here every member keeps its own planner, and what they
agreed is in front of each of them:

- **The blackboard**: three lines (target, roles, rally point) in the party store, with a
  revision and a time. One system-two call (kind `huddle`, the cheap planner model) writes it,
  shown why the party stopped, every member's state, and what each brings from its own memory,
  so the tamer's "I died here" is said where the plan can answer it instead of acted on alone.
- **When**: on events, never on a timer, and only between fights (never with a hostile in
  reach): the party has no plan yet; a member's goal for the plan failed, or it had to leave the
  place; someone fell under a third of its health or died since the plan was made; someone ran
  out of what it needs; something stronger than the party had weighed was seen. Not more often
  than every MIN_GAP_S, except after a death.
- **What each member does with it**: its planner reads the blackboard before choosing a goal
  (a section of its prompt), a goal it was in the middle of is cut short when the plan changes,
  and it keeps its own memory of what was agreed. Memories stay private: what a member brings to
  the huddle is the two memories that bear on it most (Jev's pick), in its own words, and there
  is no shared party note.

A fleet file asks for it with `party = true` and `plan = "huddle"` (fleet.py).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any

from . import costs, llm
from . import memory as memories

MIN_GAP_S = 150.0   # between two huddles, unless someone has died since
BRING_S = 8.0       # how long the one who called it waits for the others to say what they bring
OPEN_S = 30.0       # a huddle called and not finished within this is dropped
REACH = 12          # tiles: a hostile this close, and nobody huddles
BRINGS = 2          # memories a member brings

SCHEMA = """
CREATE TABLE IF NOT EXISTS party_board (
    id INTEGER PRIMARY KEY, party TEXT NOT NULL, rev INTEGER NOT NULL, target TEXT NOT NULL, roles TEXT, rally TEXT,
    reason TEXT, by TEXT NOT NULL, t REAL NOT NULL);
CREATE TABLE IF NOT EXISTS party_huddle (
    id INTEGER PRIMARY KEY, party TEXT NOT NULL, hid INTEGER NOT NULL, who TEXT NOT NULL, kind TEXT NOT NULL,
    text TEXT, t REAL NOT NULL);
CREATE INDEX IF NOT EXISTS party_huddle_party ON party_huddle(party, hid, id);
"""

SYSTEM = """You are the shared plan of a party of adventurers in the game Ultima Online. They have stopped for
a moment to agree what to do next. Each of them plans for itself afterwards, with what you write in front
of it, so it must be one plan that all of them can follow at once. You get why they stopped, what the party
was asked to do, how each member is, and what each brings from its own memory, in its own words. Call
blackboard exactly once with three lines:

- target: what the party does next, all of them together: the place, and the creature or the errand
  ("Hunt the undead at the Britain Graveyard, the lich first, all five, now").
- roles: who does what there, by name ("Roland takes the lich; Isolde and Wystan kill it from range; Thea's
  bear holds the bone knights; Corwin sets the skeletons on each other and bandages").
- rally: where they regroup if it goes wrong, and what "wrong" is ("Two down, or Roland under a third: back
  to the Britain healer, together").

Rules. Keep them together: nobody is sent on an errand of their own unless they cannot fight without it,
and then the line says who, what, and where the others wait. Answer what the members bring: when someone
died or had to leave the place before, say what is different this time (all of them at once, who stands
where), or choose somewhere they can win. Plan for what they are: a mage, an archer, a bard and a tamer
are weak in melee; a warrior can take the blows. Short lines, plain words, real place names from what you
were given.
"""


def board_tool() -> dict[str, Any]:
    s = {"type": "string"}
    return {"type": "function", "function": {
        "name": "blackboard", "description": "The party's plan in three lines.",
        "parameters": {"type": "object", "properties": {"target": s, "roles": s, "rally": s},
                       "required": ["target", "roles", "rally"], "additionalProperties": False}}}


class Huddle:
    """One member's part in the party's huddles: calling one when it is due, saying what it brings,
    and reading the blackboard."""

    def __init__(self, party: Any, session: Any, goal: str = "", memory: "memories.Memory | None" = None,
                 picker: Any = None, chat_fn: llm.ChatFn = llm.chat,
                 log: Callable[[dict[str, Any]], None] | None = None):
        self.party, self.session, self.goal = party, session, goal
        self.world = party.world
        self.me = party.me
        self.memory, self.picker = memory, picker
        self.chat = chat_fn
        self.log = log or (lambda _: None)
        self.world.db.executescript(SCHEMA)
        self.seen_rev = 0        # the blackboard revision this member has read (and kept a memory of)
        self.answered: set[int] = set()
        self.called = 0
        self.brought = 0
        self.cost = 0.0
        self.dangers_seen: set[str] = set()

    # ---- the store ------------------------------------------------------------------

    def board(self) -> dict[str, Any] | None:
        r = self.world.db.execute("SELECT * FROM party_board WHERE party = ? ORDER BY rev DESC LIMIT 1",
                                  (self.party.party,)).fetchone()
        return dict(r) if r else None

    def rows(self, hid: int) -> list[dict[str, Any]]:
        return [dict(r) for r in self.world.db.execute(
            "SELECT * FROM party_huddle WHERE party = ? AND hid = ? ORDER BY id", (self.party.party, hid))]

    def open_huddle(self) -> int | None:
        """A huddle called in the last OPEN_S seconds and not yet done."""
        r = self.world.db.execute("SELECT hid, MAX(t) AS t FROM party_huddle WHERE party = ? AND kind = 'call' "
                                  "GROUP BY hid ORDER BY hid DESC LIMIT 1", (self.party.party,)).fetchone()
        if not r or r["hid"] is None or time.time() - r["t"] > OPEN_S:
            return None
        return None if any(x["kind"] == "done" for x in self.rows(r["hid"])) else r["hid"]

    def add(self, hid: int, kind: str, text: str = "") -> None:
        self.world.db.execute("INSERT INTO party_huddle (party, hid, who, kind, text, t) VALUES (?, ?, ?, ?, ?, ?)",
                              (self.party.party, hid, self.me, kind, text or None, time.time()))
        self.world.db.commit()

    # ---- when ------------------------------------------------------------------------

    @staticmethod
    def in_reach(snap: dict[str, Any]) -> bool:
        """A hostile in reach, or a fight on: no huddle now."""
        return bool((snap.get("agent") or {}).get("engaged")) or any(
            m.get("monster") and not m.get("dead") and m.get("distance", 99) <= REACH for m in snap.get("mobiles") or [])

    def reason(self, snap: dict[str, Any], last: Any = None) -> str:
        """Why a huddle is due now, in words, or "" when it isn't. `last`: the planner's last step."""
        if self.in_reach(snap) or (snap.get("player") or {}).get("dead"):
            return ""
        board = self.board()
        if board is None:
            return "the party has come together and has no plan yet"
        age = time.time() - board["t"]
        calls = self.party.calls(within_s=age, mine=True)
        stats = self.party.statuses()
        # A death the plan was made after is answered by that plan: counted for as long as the member was a
        # ghost, every planner step of every member called a new huddle (four steps, four blackboards).
        died_at = {n: st.get("died_at") or 0.0 for n, st in stats.items() if st.get("dead") and st["age_s"] < 60}
        died = [n for n, t in died_at.items() if t > board["t"]]
        died += [c["about"] for c in calls if c["kind"] == "down" and died_at.get(c["about"], c["t"]) > board["t"]]
        if died:
            return f"{', '.join(dict.fromkeys(died))} died since the plan was made"
        if age < MIN_GAP_S:
            return ""
        result = str((getattr(last, "result", None) or {}).get("result", "")) if last is not None else ""
        ok = bool((getattr(last, "result", None) or {}).get("ok", True)) if last is not None else True
        if last is not None and getattr(last, "tool", "") in ("hunt", "travel_to") and (not ok or "left the area" in result):
            return f"{self.me}'s goal for the plan went wrong: {result[:200]}"
        low = [c["who"] for c in calls if c["kind"] == "badly_hurt"]
        if low:
            return f"{', '.join(dict.fromkeys(low))} came near death in the last fight"
        out = [f"{c['who']} is out of {c['kind'].removeprefix('out_of_')}" for c in calls if c["kind"].startswith("out_of_")]
        if out:
            return "; ".join(dict.fromkeys(out))
        new = {f"{kind} at {area}" for area, seen in getattr(self.session, "dangers", {}).items() for kind in seen.values()}
        fresh = sorted(new - self.dangers_seen)
        self.dangers_seen |= new
        if fresh and not any(f.split(" at ")[0] in (board["target"] or "").lower() for f in fresh):
            return "something the plan hadn't weighed was seen: " + ", ".join(fresh[:3])
        return ""

    # ---- taking part -----------------------------------------------------------------

    async def bring(self, hid: int, why: str, situation: dict[str, Any] | None = None) -> None:
        """Say what this member brings: how it is, and the memories that bear on the huddle most."""
        if hid in self.answered:
            return
        self.answered.add(hid)
        lines: list[str] = []
        if self.picker is not None and self.memory is not None and self.memory.character:
            try:
                chosen = await self.picker.pick(situation or {}, f"{self.goal} (the party is deciding what to do: {why})", [])
                lines = [memories.words(m)[:240] for m in chosen.get("picked", [])[:BRINGS]]
            except Exception as e:  # noqa: BLE001 - it brings nothing rather than holding the party up
                self.log({"type": "huddle", "t": time.time(), "error": f"bring: {type(e).__name__}: {e}"[:200]})
        self.add(hid, "bring", json.dumps(lines))
        self.brought += 1
        self.log({"type": "huddle", "t": time.time(), "hid": hid, "brought": lines})

    async def call(self, why: str, situation: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Call a huddle: claim it, hear what the others bring, write the blackboard. None when another
        member called one first, or the call failed."""
        hid = (self.world.db.execute("SELECT MAX(hid) FROM party_huddle WHERE party = ?", (self.party.party,)).fetchone()[0]
               or 0) + 1
        self.add(hid, "call", why)
        first = min((r for r in self.rows(hid) if r["kind"] == "call"), key=lambda r: (r["id"]))
        if first["who"] != self.me or self.open_huddle() != hid:
            return None  # another member called it in the same moment
        self.called += 1
        await self.bring(hid, why, situation)
        want = set(self.party.members)
        deadline = time.time() + BRING_S
        while time.time() < deadline and not {r["who"] for r in self.rows(hid) if r["kind"] == "bring"} >= want:
            await asyncio.sleep(0.5)
        brings = {r["who"]: json.loads(r["text"] or "[]") for r in self.rows(hid) if r["kind"] == "bring"}
        stats = self.party.statuses()
        members = []
        for name in self.party.members:
            st = stats.get(name)
            if st is None:
                members.append(f"- {name}: not heard from")
                continue
            health = "dead (a ghost)" if st.get("dead") else f"{st.get('hits')}/{st.get('hits_max')} health"
            said = "; ".join(brings.get(name) or []) or ("(nothing from memory)" if name in brings else "(didn't answer)")
            members.append(f"- {name} ({st.get('plays_as') or 'companion'}): {health}, {st.get('supplies', '')}, at "
                           f"{st.get('x')},{st.get('y')}. Brings: {said}")
        board = self.board()
        was = (f"The plan until now (made {(time.time() - board['t']) / 60:.0f} min ago): target: {board['target']}; "
               f"roles: {board['roles']}; rally: {board['rally']}\n\n") if board else ""
        user = (f"Why they stopped: {why}\n\nWhat the party was asked to do: {self.goal or '(nothing in particular)'}\n\n"
                + was + "The members:\n" + "\n".join(members) + "\n\nCall blackboard.")
        try:
            with costs.kind("huddle"):
                res = await self.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                                      tools=[board_tool()], tool_choice="blackboard", model=llm.planner_model(None),
                                      max_tokens=600)
        except llm.LlmError as e:
            self.add(hid, "done", "failed")
            self.log({"type": "huddle", "t": time.time(), "hid": hid, "error": str(e)[:300]})
            return None
        call = next((c for c in res.tool_calls if c.name == "blackboard"), None)
        args = (call.arguments if call else None) or {}
        target = " ".join(str(args.get("target") or "").split())[:300]
        if not target:
            self.add(hid, "done", "empty")
            return None
        rev = (board["rev"] if board else 0) + 1
        roles, rally = (" ".join(str(args.get(k) or "").split())[:400] for k in ("roles", "rally"))
        self.world.db.execute("INSERT INTO party_board (party, rev, target, roles, rally, reason, by, t) "
                              "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (self.party.party, rev, target, roles, rally, why, self.me, time.time()))
        self.world.db.commit()
        self.add(hid, "done", str(rev))
        self.cost += res.usage.cost or 0.0
        out = {"rev": rev, "target": target, "roles": roles, "rally": rally, "reason": why,
               "answered": sorted(brings), "brought": {k: v for k, v in brings.items() if v}}
        self.log({"type": "huddle", "t": time.time(), "hid": hid, "called": True, **out, "usage": res.usage.to_log()})
        return out

    async def maybe(self, snap: dict[str, Any], situation: dict[str, Any] | None = None, last: Any = None) -> bool:
        """At a planner step: take part in a huddle that is open, or call one when it is due. True when
        the blackboard changed."""
        before = (self.board() or {}).get("rev", 0)
        hid = self.open_huddle()
        if hid is None and (why := self.reason(snap, last)):
            await self.call(why, situation)
            hid = self.open_huddle()  # another's, called in the same moment
        if hid is not None:
            await self.answer(hid, snap, situation)
            deadline = time.time() + BRING_S + 6
            while time.time() < deadline and self.open_huddle() == hid:
                await asyncio.sleep(0.5)  # the plan is being written: choose after it
        self.read()
        return (self.board() or {}).get("rev", 0) != before

    async def answer(self, hid: int, snap: dict[str, Any], situation: dict[str, Any] | None = None) -> None:
        if hid in self.answered or self.in_reach(snap):
            return  # in a fight it says nothing: the others don't wait for it
        why = next((r["text"] for r in self.rows(hid) if r["kind"] == "call"), "") or ""
        await self.bring(hid, why, situation)

    def read(self) -> dict[str, Any] | None:
        """Read the blackboard; a revision not seen before is kept as this member's own memory."""
        board = self.board()
        if board is None or board["rev"] <= self.seen_rev:
            return board
        self.seen_rev = board["rev"]
        if self.memory is not None and self.memory.character:
            self.memory.add(f"The party agreed ({time.strftime('%Y-%m-%d %H:%M')}, because {board['reason']}): target: "
                            f"{board['target']}; roles: {board['roles']}; rally: {board['rally']}"[:700],
                            kind="event", tags=("huddle",), source="talk", importance=3)
        return board

    async def watch(self, stop: asyncio.Event, every_s: float = 2.0) -> None:
        """While a goal is going on: say what this member brings to a huddle another called, and cut the
        goal short when the plan changes, so its planner chooses again with the new plan. Not in a fight."""
        pending = 0
        while not stop.is_set():
            try:
                snap = await self.session.snap()
                if snap.get("in_game") and not (snap.get("player") or {}).get("dead"):
                    hid = self.open_huddle()
                    if hid is not None:
                        await self.answer(hid, snap)
                    board = self.board()
                    if board is not None and board["rev"] > self.seen_rev:
                        pending = board["rev"]
                    if pending and not self.in_reach(snap) and pending > self.seen_rev \
                            and not self.session.interrupt.is_set():
                        self.session.interrupt.set()
            except Exception as e:  # noqa: BLE001 - the watcher never stops a session
                self.log({"type": "huddle", "t": time.time(), "error": f"watch: {type(e).__name__}: {e}"[:200]})
            await asyncio.sleep(every_s)

    # ---- for the planner ---------------------------------------------------------------

    def section(self) -> str:
        board = self.board()
        if board is None:
            return ("Your party plans by huddle: each of you plans for yourself, with a plan you all agree in front of "
                    "you. There is no plan yet: one is being agreed. Until it is, stay where you are (rest a short "
                    "while).")
        mins = (time.time() - board["t"]) / 60
        return (f"Your party's plan, agreed in a huddle {mins:.0f} min ago (because {board['reason']}). Each of you plans "
                "for yourself, and every one of you has this in front of them:\n"
                f"- Target: {board['target']}\n- Roles: {board['roles']}\n- Rally point: {board['rally']}\n"
                "Choose your own next goal so that it carries out this plan with the others: go where the party goes, "
                "now, and do your part of it. Don't go off on an errand of your own unless the plan gives you one or "
                "you can't fight without it; what you remember was said in the huddle, and the plan has answered it. "
                "When the plan can't be kept (the place refuses you, you had to leave it, the target is dead), rest a "
                "short while: the party will huddle again.")

    def to_log(self) -> dict[str, Any]:
        return {"huddles_called": self.called, "huddles_answered": self.brought, "huddle_cost_usd": round(self.cost, 5),
                "blackboard_rev": self.seen_rev}
