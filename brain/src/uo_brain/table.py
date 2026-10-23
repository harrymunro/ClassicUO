"""R&R at the inn: after a hunt the party sits down, has a few drinks and talks it over (cuo-cod.3, .4, .5).

Harry: the adventurers go back to the inn after a hunt for some R&R, a few drinks, and to talk
over the adventure they have just had: reflect, learn from each other, and trade some top banter.
The drinks are acted out ("*has a swig of ale*"); nothing is bought or used.

- **The goal.** `inn` is a goal of the leader's planner (planner.py), which chooses it as it
  chooses every goal. The party walks to the nearest inn the world store knows, the others
  following (party.Follower), and gathers there.
- **The table.** A group conversation, kept in the party store (table party_table), which the
  brains share: the leader's brain keeps the turns (`Table.host`), naming who speaks next (the
  one just spoken to, else the one who has said least, never the same twice running) and where
  the evening is: a toast, the stories, the banter, then what each would do differently. Each
  line is written by the speaker's own brain (`Table.sit`), from its own persona, its own
  memories and its own story of the hunt, with what has been said so far. Memories stay private:
  what one shares is what it chooses to say.
- **The story of the hunt** (`Story`), for each member from what its own fight loop saw, in
  code: what it fought, the lowest its health got and what was on it then, who healed whom, its
  callouts, who fell, whether it ran or ran out. The banter is made of this.
- **Pace** (talk.py, cuo-cod.9): a line waits as long as a person would take to read the one
  before and type their own, and nobody speaks over a line still up. An emote goes just before
  its line.
- **What is kept** (cuo-cod.5). When the table closes each member writes its own memories of it
  (`Table.reflect`), private like every memory: what it would do differently (a lesson), what it
  learned about its companions (relationship memories), and at most one line about how to fight
  next time, which alone may reach its fights (never whether to fight: a memory that says "leave"
  once made every later hunt end in a leave).

Local server only, as all talk.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from collections.abc import Callable
from typing import Any

from . import costs, llm
from . import memory as memories
from .talk import PAUSE_S, Floor, fit, hold_s
from .world import World

LINE_CAP = 110       # a table line: asked for 90 characters; a longer one is cut at a sentence or a word
TURN_S = 40.0        # the keeper waits this long for the one named to speak, then names another
THINK_S = (0.8, 2.2)  # between having read the last line and speaking
GATHER_S = 120.0     # ...and this long for the party to arrive at the inn
GATHER_TILES = 8     # close enough to be at the table
PHASES = ("toast", "stories", "banter", "lessons")
TACTIC_TAG = "tactic"
# A line about how to fight may reach the fights; one about whether to isn't one (cuo-cod.5).
NOT_A_TACTIC = re.compile(r"\b(leave|leaving|flee|fleeing|run away|avoid|stay away|don't fight|do not fight|never fight|"
                          r"retreat|too dangerous|not go back|never go)\b", re.I)

SCHEMA = """
CREATE TABLE IF NOT EXISTS party_table (
    id INTEGER PRIMARY KEY, party TEXT NOT NULL, tid INTEGER NOT NULL, who TEXT NOT NULL, kind TEXT NOT NULL,
    text TEXT, phase TEXT, to_who TEXT, t REAL NOT NULL);
CREATE INDEX IF NOT EXISTS party_table_party ON party_table(party, tid, id);
"""

TRAILING = re.compile(r"(?:\s+(?:to|the|a|an|of|and|at|with|in|on|for|his|her|their|its|from|by|as))+$", re.I)

SYSTEM = """You write what one character says at a table in an inn, in the game Ultima Online, the evening
after a hunt with their party. They are tired, sore or pleased with themselves, and have a drink in front
of them. You get who they are, their own part of the hunt (what really happened to them: use it), what
they remember, who is at the table and everything said so far. Call table_say exactly once with:

- line: what they say now: ONE sentence of at most 16 words, never more than 90 characters, in their own
  voice. It is shown over their head for a few seconds: a long line is not read, and is cut short.
- emote: optional, one line in three at most: something small they do as they speak, in the third person
  and present tense, without asterisks or their own name ("has a swig of ale", "slams his tankard down",
  "raises her cup to Roland"), and never one they have done already tonight. The drinks are acted out;
  nobody orders or pays.
- to: who it is aimed at most: a name at the table, or "all".
- lesson: only when asked for (the evening winding down): what they would do differently next time, as
  one plain sentence for their own memory. Else "".

How they talk. These are companions who know each other well and don't hold back. The banter is heavy:
they rib each other hard about what happened in the hunt (who died first, who ran, who missed, who had to
be healed three times, whose pet did the work) and about who each of them is, and an insult is answered
in kind. No polite turn-taking, no apologies, no "all in good fun", no summing up, no speeches about
friendship. Swearing is fine. It stays in character and in the game world: no real-world slurs, and the
insults are about the hunt and each other.

Keep to what happened: don't invent kills, deaths or rescues that aren't in their part of the hunt or in
what the others have said. Don't repeat a point or a joke already made at the table. When the last line
was aimed at them, answer it. Never mention being an AI or a program.
"""

ASK = {
    "toast": "Open the table with a toast, in their own way: to the hunt, to someone at the table, or against someone.",
    "stories": "Tell their own part of the hunt, or answer what was just said about it. One thing that happened, not a list.",
    "banter": "Banter: go at someone at the table over what they did in the hunt or who they are, or throw back what "
              "was thrown at them.",
    "lessons": "The evening is winding down. Say what they would do differently next time: one concrete thing about "
               "the fight, in their own words; it can still have an edge. Put the same thing in `lesson` as a plain "
               "sentence.",
}

# A hamlet's evening (cuo-m3n.5): neighbours who live in one small place, gathered of an evening at one of their
# houses to talk the day over. The same table, turns and pace; what is said and kept is a day's, not a hunt's.
HAMLET_SYSTEM = """You write what one character says of an evening in the game Ultima Online, sitting with their
neighbours at {place} in the small hamlet where they all live, talking the day over. You get who they are,
their own day (what they really did: use it), what they remember, who is there and everything said so far.
Call table_say exactly once with:

- line: what they say now: ONE sentence of at most 16 words, never more than 90 characters, in their own
  voice. It is shown over their head for a few seconds: a long line is not read, and is cut short.
- emote: optional, one line in three at most: something small they do as they speak, in the third person
  and present tense, without asterisks or their own name ("pours herself some tea", "stretches by the fire"),
  and never one they have done already tonight.
- to: who it is aimed at most: a name there, or "all".
- lesson: only when asked for (the evening winding down): what they mean to do tomorrow, as one plain
  sentence for their own memory. Else "".

How they talk: as neighbours who see each other every day, each in their own temper: news of the day, a
worry, a bit of gossip, a tease, an offer of help, a complaint about the orcs or the prices. They answer what
is said to them. No speeches, no summing up. It stays in character and in the game world.

Keep to what happened: don't invent kills, deaths, sales or meetings that aren't in their own day or in what
the others have said. Don't repeat a point already made. Never mention being an AI or a program.
"""

HAMLET_ASK = {
    "toast": "Open the evening: a greeting to the neighbours, in their own way.",
    "stories": "Tell one thing from their own day, or answer what was just said about someone's day.",
    "banter": "Talk with a neighbour: news, a worry, gossip, a tease, an offer of help, or throw back what was said.",
    "lessons": "The evening is winding down. Say what they mean to do tomorrow, in their own words. Put the same thing "
               "in `lesson` as a plain sentence.",
}

HAMLET_REFLECT = """You are a character's own thoughts after an evening with their neighbours in the hamlet where
they live, in the game Ultima Online. You get who they are, their own day, and everything said that evening.
Call keep exactly once with what they take away, for their own memory only:

- lessons: one or two plans for tomorrow or things they mean to remember from the evening, each a plain sentence.
- companions: up to three things they learned about a neighbour tonight, each a plain sentence naming them
  ("Wenna hunts the woods east of Cove alone; she says the bears are thicker there this week").
- tactic: "".
"""

REFLECT = """You are a character's own thoughts after an evening at the inn with their party, in the game
Ultima Online, the night after a hunt. You get who they are, their own part of the hunt, and everything
that was said at the table. Call keep exactly once with what they take away, for their own memory only:

- lessons: one or two things they would do differently or want to remember about the hunt, each a plain
  sentence with the place and the reason.
- companions: up to three things they learned about a companion tonight, each a plain sentence naming them
  ("Isolde nearly died on the lich; she says she will stand behind Roland next time").
- tactic: at most one short sentence about HOW to fight next time with this party (where to stand, whom to
  go for first, when to heal whom), or "". Never about whether to fight, when to leave or what to avoid.
"""


def clean_line(value: Any) -> str:
    """A one-line field as a model sent it: a list gives its first line, and a string written like a list
    (Sonnet once sent `["Hold the front..."][0]`) what is inside it."""
    if isinstance(value, list):
        value = value[0] if value else ""
    text = " ".join(str(value or "").split())
    if m := re.match(r'^\[\s*["\'](.*)["\']\s*\](?:\[0\])?$', text):
        text = m[1]
    return text.strip()


def say_tool() -> dict[str, Any]:
    s = {"type": "string"}
    return {"type": "function", "function": {
        "name": "table_say", "description": "One line at the table, with a small action if there is one.",
        "parameters": {"type": "object", "properties": {"line": s, "emote": s, "to": s, "lesson": s},
                       "required": ["line"], "additionalProperties": False}}}


def keep_tool() -> dict[str, Any]:
    arr = {"type": "array", "items": {"type": "string"}, "maxItems": 3}
    return {"type": "function", "function": {
        "name": "keep", "description": "What the character takes away from the evening.",
        "parameters": {"type": "object", "properties": {"lessons": arr, "companions": arr, "tactic": {"type": "string"}},
                       "required": ["lessons", "companions"], "additionalProperties": False}}}


class Story:
    """One member's own part of a hunt, gathered by its fight loop (mates.Mates) and its session:
    the material its table talk is made of. Reset when a table closes."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.began = time.time()
        self.fought: dict[str, int] = {}      # creature (short name) -> decisions spent on it
        self.low: tuple[int, str, list[str]] | None = None  # lowest health %, what was on it, who was beside it
        self.deaths = 0
        self.called: dict[str, int] = {}      # its callouts, by kind
        self.healed: dict[str, int] = {}      # companions it healed or bandaged -> times
        self.healed_by: dict[str, int] = {}   # ...and who healed it
        self.raised: dict[str, int] = {}      # companions it tried to bring back from the dead -> tries
        self.raised_by = ""                   # ...and who brought it back
        self.fell: list[str] = []             # companions who died
        self.ran = 0
        self.out_of: list[str] = []
        self.heard: list[str] = []            # companions' callouts, in words (the last few)
        self.dead_seen: list[str] = []        # creatures whose corpses it saw
        self.kills = 0

    def empty(self) -> bool:
        return not (self.fought or self.deaths or self.called or self.healed or self.fell)

    def words(self) -> str:
        """The member's part of the hunt in plain sentences, for its own table lines."""
        if self.empty():
            return "Nothing much happened to them: no fight worth the name."
        out = []
        if self.fought:
            top = sorted(self.fought.items(), key=lambda kv: -kv[1])[:4]
            out.append("They fought " + ", ".join(k for k, _ in top) + ".")
        if self.dead_seen:
            out.append("Dead at the end of it: " + ", ".join(dict.fromkeys(self.dead_seen)) + ".")
        if self.low is not None and self.low[0] >= 95:
            # Told "their health fell to 100% at its lowest", the speaker boasted of "a hundred percent low".
            out.append("They were not hurt at all.")
        elif self.low is not None:
            pct, on, beside = self.low
            how = "a little hurt" if pct >= 70 else "hurt" if pct >= 50 else "badly hurt" if pct >= 25 else "nearly dead"
            out.append(f"At the worst of it they were {how} ({pct}% of their health left)"
                       + (f", with {on} on them" if on else "")
                       + (f"; {', '.join(beside)} beside them then" if beside else "; nobody beside them then") + ".")
        out.append(f"They died {self.deaths} time{'s' if self.deaths != 1 else ''}." if self.deaths
                   else "They did not die.")
        if self.fell:
            out.append("Companions who fell: " + ", ".join(dict.fromkeys(self.fell)) + ".")
        if self.healed:
            out.append("They healed or bandaged " + ", ".join(f"{n} ({c} time{'s' if c != 1 else ''})"
                                                               for n, c in self.healed.items()) + ".")
        if self.raised:
            out.append("They worked to bring " + ", ".join(self.raised) + " back from the dead where they fell.")
        if self.raised_by:
            out.append(f"{self.raised_by} brought them back from the dead where they fell.")
        if self.healed_by:
            out.append("Healed or bandaged by " + ", ".join(f"{n} ({c} time{'s' if c != 1 else ''})"
                                                             for n, c in self.healed_by.items()) + ".")
        if self.ran:
            out.append(f"They ran from the fight {self.ran} time{'s' if self.ran != 1 else ''}.")
        if self.out_of:
            out.append("They ran out of " + ", ".join(dict.fromkeys(self.out_of)) + ".")
        shouts = {"badly_hurt": "that they were nearly dead", "hurt": "that they were hurt", "on_me": "for help with what "
                  "was on them", "leaving": "that they were falling back"}
        said = [f"{shouts[k]} ({n}x)" for k, n in self.called.items() if k in shouts]
        if said:
            out.append("In the fight they shouted " + ", ".join(said) + ".")
        if self.heard:
            out.append("What they heard the others shout: " + "; ".join(self.heard[-5:]) + ".")
        return " ".join(out)


class Table:
    """One member's place at the party's table, and for the leader the keeping of its turns."""

    def __init__(self, party: Any, rpc: Any, persona: dict[str, str] | None = None, voice: Any = None,
                 memory: "memories.Memory | None" = None, story: Story | None = None,
                 chat_fn: llm.ChatFn = llm.chat, log: Callable[[dict[str, Any]], None] | None = None,
                 floor: Floor | None = None, lines: int | None = None, pace: bool = True, emotes: bool = True,
                 scene: str = "inn", place: str = ""):
        self.party, self.rpc = party, rpc
        # "inn": a party's evening after a hunt; "hamlet": neighbours' evening at one of their houses (cuo-m3n.5),
        # at `place` ("the healer's house").
        self.scene, self.place = scene, place
        self.world: World = party.world
        self.me = party.me
        self.persona = persona or {}
        self.voice = voice
        self.memory = memory
        self.story = story or Story()
        self.chat = chat_fn
        self.log = log or (lambda _: None)
        self.floor = floor or Floor(self.world, self.me, set(party.members))
        self.lines_wanted = lines
        self.pace = pace
        self.emotes = emotes
        self.world.db.executescript(SCHEMA)
        self.spoken = 0
        self.cost = 0.0
        self.tables = 0
        self.kept = 0
        self.area: str | None = None  # where the hunt was that the table talks over, for the memories kept
        self.ears: Any = None         # talk.Ears: it listens at the table, and the table's turns do the talking
        self.emoted: list[str] = []   # the small things done at this table, so none is done twice
        self.last_emoted = False
        # A party for the player (companion.py): the character a person plays sits with them. Its lines are on
        # the table as anyone's, and it is never named for a turn.
        self.guest: str | None = None

    async def arrive(self) -> None:
        """Sitting down: a small thing done, in the character's own way (from its voice pack)."""
        if not self.emotes or self.voice is None:
            return
        if line := self.voice.pick("e_arrive"):
            await self.rpc.call("act", verb="say", text=f"*{line}*", kind="emote")
            self.floor.say(f"*{line}*", kind="emote")

    def system(self) -> str:
        return HAMLET_SYSTEM.format(place=self.place or "one of their houses") if self.scene == "hamlet" else SYSTEM

    def own_part(self) -> str:
        return "own day" if self.scene == "hamlet" else "own part of the hunt"

    def claim(self, key: str, window_s: float) -> bool:
        """Whether this member keeps the turns of a hamlet's evening (cuo-m3n.5): the first to claim `key` in the
        last window_s, the claims in the shared store deciding a race between brains."""
        now = time.time()
        rows = self.world.db.execute("SELECT who, t FROM party_table WHERE party = ? AND tid = 0 AND kind = 'claim' "
                                     "AND text = ? ORDER BY id", (self.party.party, key)).fetchall()
        if not any(now - r["t"] < window_s for r in rows):
            self.add(0, "claim", key)
            rows = self.world.db.execute("SELECT who, t FROM party_table WHERE party = ? AND tid = 0 AND kind = 'claim' "
                                         "AND text = ? ORDER BY id", (self.party.party, key)).fetchall()
        live = [r for r in rows if now - r["t"] < window_s]
        return bool(live) and live[0]["who"] == self.me

    # ---- the store ------------------------------------------------------------------

    def add(self, tid: int, kind: str, text: str = "", phase: str = "", to: str = "", who: str | None = None) -> int:
        cur = self.world.db.execute(
            "INSERT INTO party_table (party, tid, who, kind, text, phase, to_who, t) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (self.party.party, tid, who or self.me, kind, text or None, phase or None, to or None, time.time()))
        self.world.db.commit()
        return cur.lastrowid

    def rows(self, tid: int, after: int = 0) -> list[dict[str, Any]]:
        return [dict(r) for r in self.world.db.execute(
            "SELECT * FROM party_table WHERE party = ? AND tid = ? AND id > ? ORDER BY id", (self.party.party, tid, after))]

    def current(self) -> int | None:
        """The table open now, if one is."""
        r = self.world.db.execute("SELECT tid, kind FROM party_table WHERE party = ? ORDER BY id DESC LIMIT 1",
                                  (self.party.party,)).fetchone()
        return r["tid"] if r and r["kind"] != "close" else None

    def last_tid(self) -> int:
        return self.world.db.execute("SELECT MAX(tid) FROM party_table WHERE party = ?", (self.party.party,)).fetchone()[0] or 0

    def transcript(self, tid: int) -> list[dict[str, Any]]:
        return [r for r in self.rows(tid) if r["kind"] in ("line", "emote")]

    # ---- the keeper of the turns (the leader's brain) --------------------------------

    @staticmethod
    def plan(n: int, lines: int | None = None) -> list[str]:
        """The phase of each line of an evening for n at the table: a toast, the stories, the banter,
        then one each on what they would do differently. About four lines each, 12 to 30 in all."""
        total = max(12, min(30, lines or 4 * n))
        middle = max(2, total - 1 - n)
        stories = (middle + 1) // 2
        return ["toast"] + ["stories"] * stories + ["banter"] * (middle - stories) + ["lessons"] * n

    @staticmethod
    def next_speaker(present: list[str], said: list[dict[str, Any]], phase: str, skip: set[str] = frozenset(),
                     rnd: random.Random | None = None) -> str | None:
        """Who speaks next: in the last round, whoever hasn't said their piece yet, in seating order;
        else the one the last line was aimed at, else the one who has said least. Never the same twice
        running, and nobody who has stopped answering."""
        rnd = rnd or random
        lines = [r for r in said if r["kind"] == "line"]
        last = lines[-1]["who"] if lines else None
        seats = [p for p in present if p not in skip]
        if not seats:
            return None
        if phase == "lessons":
            done = {r["who"] for r in lines if r.get("phase") == "lessons"}
            left = [p for p in seats if p not in done]
            return next((p for p in left if p != last), left[0] if left else None)
        options = [p for p in seats if p != last] or seats
        aimed = (lines[-1].get("to_who") or "") if lines else ""
        counts = {p: sum(1 for r in lines if r["who"] == p) for p in options}
        if aimed in options and counts[aimed] <= min(counts.values()) + 1:
            return aimed
        least = min(counts.values())
        return rnd.choice([p for p in options if counts[p] == least])

    async def host(self, present: list[str], stop: asyncio.Event | None = None) -> dict[str, Any]:
        """Open a table for those present, keep its turns, close it. Returns what was said and how long it took."""
        tid = self.last_tid() + 1
        began = time.time()
        self.add(tid, "open", json.dumps(present))
        self.log({"type": "table", "t": began, "open": tid, "present": present})
        skipped: dict[str, int] = {}
        for phase in self.plan(len(present), self.lines_wanted):
            if stop is not None and stop.is_set():
                break
            said = self.rows(tid)
            out = {p for p, n in skipped.items() if n >= 2}
            who = self.next_speaker(present, said, phase, out)
            if who is None:
                continue
            asked = self.add(tid, "next", phase=phase, who=who)
            deadline, spoke = time.time() + TURN_S, False
            while time.time() < deadline and not spoke and not (stop is not None and stop.is_set()):
                spoke = any(r["kind"] == "line" and r["who"] == who for r in self.rows(tid, asked))
                if not spoke:
                    await asyncio.sleep(0.4)
            if not spoke:
                skipped[who] = skipped.get(who, 0) + 1
                self.add(tid, "skip", who=who, phase=phase)
        lines = [r for r in self.rows(tid) if r["kind"] == "line"]
        self.add(tid, "close")
        self.tables += 1
        out = {"table": tid, "lines": len(lines), "minutes": round((time.time() - began) / 60, 1),
               "speakers": {p: sum(1 for r in lines if r["who"] == p) for p in present},
               "skipped": {p: n for p, n in skipped.items() if n}}
        self.log({"type": "table", "t": time.time(), "closed": tid, **out})
        return out

    # ---- every member ----------------------------------------------------------------

    async def sit(self, stop: asyncio.Event | None = None, wait_s: float = 90.0) -> int | None:
        """Take part in the table that is open (waiting up to wait_s for one to open): speak when named,
        until it closes. Returns the table sat at, or None when none opened."""
        began = time.time()
        tid = self.current()
        while tid is None and time.time() - began < wait_s and not (stop is not None and stop.is_set()):
            await asyncio.sleep(1.0)
            tid = self.current()
        if tid is None:
            return None
        seen, answered, quiet_since = 0, set(), time.time()
        self.emoted, self.last_emoted = [], False
        if self.ears is not None:
            self.ears.paused = True
        try:
            while not (stop is not None and stop.is_set()):
                rows = self.rows(tid, seen)
                if rows:
                    seen, quiet_since = rows[-1]["id"], time.time()
                if any(r["kind"] == "close" for r in rows):
                    break
                for r in rows:
                    if r["kind"] == "next" and r["who"] == self.me and r["id"] not in answered:
                        answered.add(r["id"])
                        await self.speak(tid, r)
                if time.time() - quiet_since > 3 * TURN_S:
                    break  # the keeper is gone
                await asyncio.sleep(0.25)
        finally:
            if self.ears is not None:
                self.ears.paused = False
        return tid

    def prompt(self, tid: int, phase: str) -> str:
        said = self.transcript(tid)
        opened = next((r for r in self.rows(tid) if r["kind"] == "open"), None)
        present = json.loads(opened["text"]) if opened and opened["text"] else self.party.members
        remembered = []
        if self.memory is not None and self.memory.character:
            remembered = [memories.words(m) for m in self.memory.list(min_importance=3, limit=5)]
            remembered += [memories.words(m) for m in self.memory.list(kind="relationship", limit=3)
                           if "told" not in (m.get("tags") or [])]
        lines = "\n".join(f"{r['who']}: {r['text']}" if r["kind"] == "line" else f"({r['who']} {r['text']})" for r in said)
        return (f"Who you speak for: {self.me}. " + " ".join(f"{k.replace('_', ' ').capitalize()}: {v}."
                                                              for k, v in self.persona.items() if k != "name")
                + f"\n\n{self.me}'s {self.own_part()}: {self.story.words()}\n\n"
                + "What they remember:\n" + ("\n".join(f"- {m}" for m in remembered) or "(nothing much)")
                + f"\n\nAt the table: {', '.join(present)}."
                + (f" {self.guest}, who leads the party, sits with them and speaks when they like: what {self.guest} "
                   f"says is answered like anyone's." if self.guest else "")
                + (f"\n\nThe small things {self.me} has done tonight already: {'; '.join(self.emoted)}." if self.emoted else "")
                + "\n\nSaid at the table so far:\n" + (lines or "(nothing yet: they have just sat down)")
                + f"\n\nNow: {self.ask(phase)} Call table_say.")

    def ask(self, phase: str) -> str:
        asks = HAMLET_ASK if self.scene == "hamlet" else ASK
        return asks.get(phase, asks["banter"])

    async def speak(self, tid: int, asked: dict[str, Any]) -> str | None:
        phase = asked.get("phase") or "banter"
        text, args, res = "", {}, None
        for _ in range(2):
            # Twice at most: a call cut off at its token limit comes back with no line (Sonnet, twice in one
            # table at 500 tokens), and the turn then stood empty for the 40 s a speaker is given.
            try:
                with costs.kind("table"):
                    res = await self.chat([{"role": "system", "content": self.system()}, {"role": "user", "content": self.prompt(tid, phase)}],
                                          tools=[say_tool()], tool_choice="table_say", model=llm.planner_model(None),
                                          max_tokens=1000)
            except llm.LlmError as e:
                self.log({"type": "table", "t": time.time(), "error": str(e)[:200]})
                return None
            self.cost += res.usage.cost or 0.0
            call = next((c for c in res.tool_calls if c.name == "table_say"), None)
            args = (call.arguments if call else None) or {}
            text = fit(str(args.get("line") or ""), LINE_CAP)
            if text:
                break
            self.log({"type": "table", "t": time.time(), "error": "no line in the reply", "usage": res.usage.to_log()})
        if not text:
            return None
        emote = fit(" ".join(str(args.get("emote") or "").split()).strip("*").strip(), 60).rstrip(".") if self.emotes else ""
        # Cut short, an emote can end on a word that leads nowhere ("...and passes it down to").
        emote = TRAILING.sub("", emote)
        # Asked for one line in three, the model sends one with every line, and the same one again: at most
        # every other line of this speaker's, and never one it has done tonight.
        if emote and (self.last_emoted or any(emote.lower()[:24] == e.lower()[:24] for e in self.emoted)):
            emote = ""
        self.last_emoted = bool(emote)
        if emote:
            self.emoted.append(emote)
        to = str(args.get("to") or "").strip()
        to = to if to in self.party.members and to != self.me else ""
        said = self.transcript(tid)
        prev = next((r for r in reversed(said) if r["kind"] == "line"), None)
        if self.pace:
            # As long as it takes to read the last line, and a breath: round a table they speak, they don't
            # type, and twenty lines should take two or three minutes. Counted from that line, so the
            # model's own time is inside the wait.
            due = (prev["t"] + hold_s(prev["text"]) if prev else time.time()) + random.uniform(*THINK_S)
            while time.time() < due or self.floor.busy_until() + PAUSE_S > time.time():
                await asyncio.sleep(0.2)
        if emote:
            await self.rpc.call("act", verb="say", text=f"*{emote}*", kind="emote")
            self.floor.say(f"*{emote}*", kind="emote")
            self.add(tid, "emote", emote, phase)
            if self.pace:
                await asyncio.sleep(1.2)
        await self.rpc.call("act", verb="say", text=text)
        self.floor.say(text)
        self.add(tid, "line", text, phase, to)
        self.spoken += 1
        lesson = " ".join(str(args.get("lesson") or "").split())[:300]
        self.log({"type": "table", "t": time.time(), "said": True, "table": tid, "phase": phase, "text": text,
                  **({"emote": emote} if emote else {}), **({"to": to} if to else {}),
                  **({"lesson": lesson} if lesson else {}), "usage": res.usage.to_log()})
        return text

    # ---- what is kept (cuo-cod.5) ------------------------------------------------------

    async def reflect(self, tid: int, area: str | None = None) -> list[int]:
        """After the table: what this member takes from it, into its own memory. The ids kept."""
        if self.memory is None or not self.memory.character:
            return []
        said = self.transcript(tid)
        if not said:
            return []
        lines = "\n".join(f"{r['who']}: {r['text']}" for r in said if r["kind"] == "line")
        user = (f"Who you are: {self.me}. " + " ".join(f"{k.replace('_', ' ').capitalize()}: {v}."
                                                       for k, v in self.persona.items() if k != "name")
                + f"\n\nYour {self.own_part()}: {self.story.words()}\n\nSaid at the table:\n{lines}\n\nCall keep.")
        hamlet = self.scene == "hamlet"
        try:
            with costs.kind("table"):
                res = await self.chat([{"role": "system", "content": HAMLET_REFLECT if hamlet else REFLECT},
                                       {"role": "user", "content": user}],
                                      tools=[keep_tool()], tool_choice="keep", model=llm.planner_model(None), max_tokens=700)
        except llm.LlmError as e:
            self.log({"type": "table", "t": time.time(), "error": f"reflect: {e}"[:200]})
            return []
        call = next((c for c in res.tool_calls if c.name == "keep"), None)
        args = (call.arguments if call else None) or {}
        self.cost += res.usage.cost or 0.0
        when = time.strftime("%Y-%m-%d")
        ids = []
        where = f"at {self.place or 'the evening gathering'}" if hamlet else "at the inn"
        for text in [str(x) for x in args.get("lessons") or []][:2]:
            if text.strip():
                ids.append(self.memory.add(f"From the evening {where} ({when}): {text.strip()}" if hamlet else
                                           f"From the inn after the hunt ({when}): {text.strip()}",
                                           kind="event" if hamlet else "lesson", area=area,
                                           tags=("evening", "plan") if hamlet else ("inn", "table talk"), source="talk",
                                           importance=3 if hamlet else 4))
        for text in [str(x) for x in args.get("companions") or []][:3]:
            names = [n for n in self.party.members if n != self.me and re.search(rf"\b{re.escape(n)}\b", text)]
            if text.strip() and names:
                ids.append(self.memory.add(f"{where[0].upper()}{where[1:]} ({when}): {text.strip()}", kind="relationship",
                                           tags=("evening" if hamlet else "inn", *(n.lower() for n in names)),
                                           source="talk", importance=3))
        tactic = "" if hamlet else clean_line(args.get("tactic"))[:200]
        if tactic and not NOT_A_TACTIC.search(tactic):
            ids.append(self.memory.add(tactic, kind="lesson", area=area, tags=(TACTIC_TAG, "inn"), source="talk",
                                       importance=4))
        self.kept += len(ids)
        self.log({"type": "table", "t": time.time(), "kept": ids, "table": tid, "lessons": args.get("lessons"),
                  "companions": args.get("companions"), "tactic": tactic or None, "usage": res.usage.to_log()})
        return ids

    def to_log(self) -> dict[str, Any]:
        return {"tables": self.tables, "table_lines_said": self.spoken, "table_memories_kept": self.kept,
                "table_cost_usd": round(self.cost, 5)}


async def evening(session: Any, table: Table, place: dict[str, Any], stop: asyncio.Event | None = None) -> Any:
    """The leader's goal (cuo-cod.3): walk to the inn, wait for the party, hold the table talk and keep
    its own memories of it. The others follow the same goal (party.Follower.inn)."""
    from .session import Result
    party = table.party
    began = time.time()
    # Onto the innkeeper's own tile, which is indoors: within two tiles of it can be the other side of the wall,
    # and the first tables were held standing against the inn from outside. Further off only when it must be.
    for distance in (0, 1, 2):
        r = await session.travel_to(place["name"], place["x"], place["y"], distance=distance, refuse=False, together=True)
        if r.ok:
            break
    if not r.ok:
        return Result(False, f"did not reach {place['name']}: {r.summary}", r.data)
    walked = time.time() - began
    await table.arrive()
    present, waited = await gather(party, (place["x"], place["y"]), stop=stop)
    missing = [n for n in party.members if n not in present]
    data: dict[str, Any] = {"inn": place["name"], "walk_s": round(walked), "gather_s": round(waited),
                            "present": present, "missing": missing}
    if len(present) < 2:
        return Result(False, f"at {place['name']}, but nobody else of the party came within {GATHER_S:.0f} s", data)
    seat = asyncio.get_running_loop().create_task(table.sit(stop, wait_s=20))
    try:
        held = await table.host(present, stop)
    finally:
        tid = await seat
    kept = await table.reflect(tid, table.area) if tid is not None else []
    table.story.reset()
    learn(session, table)
    data |= {**held, "memories_kept": len(kept)}
    return Result(True, f"R&R at {place['name']}: {len(present)} of {len(party.members)} at the table"
                        + (f" ({', '.join(missing)} missing)" if missing else "")
                        + f", {held['lines']} lines in {held['minutes']} min; each keeps its own memories of it", data)


def learn(session: Any, table: Table) -> None:
    """What the table taught about how to fight goes into the fights that follow in the same session
    (mates.Mates.tactics): read once at start, a table's tactic only reached the next session's hunts."""
    link = getattr(getattr(session, "pcfg", None), "link", None)
    if link is not None:
        link.tactics = tactics(table.memory)


def tactics(memory: "memories.Memory | None", limit: int = 2, days: float = 14.0) -> list[str]:
    """A member's own lines about how to fight, from its table talks (cuo-cod.5): the newest few, for its
    fight state. Only these reach a fight; a memory about whether to fight never does."""
    if memory is None or not memory.character:
        return []
    since = memories.stamp_of(time.time() - days * 86400)
    rows = [m for m in memory.list(kind="lesson", search=TACTIC_TAG, limit=20)
            if TACTIC_TAG in (m.get("tags") or []) and m["last_t"] >= since and not NOT_A_TACTIC.search(m["text"])]
    return [m["text"] for m in rows[:limit]]


async def gather(party: Any, here: tuple[int, int], timeout_s: float = GATHER_S,
                 stop: asyncio.Event | None = None) -> tuple[list[str], float]:
    """Wait for the party to arrive at the table: those within GATHER_TILES of `here` (and alive), and
    how long it took for the last of them."""
    from .world import tiles
    began = time.time()
    present: list[str] = []
    while time.time() - began < timeout_s and not (stop is not None and stop.is_set()):
        stats = party.statuses()
        present = [n for n in party.members if n != party.me and (st := stats.get(n)) and not st.get("dead")
                   and st["age_s"] < 30 and tiles(st["x"], st["y"], *here) <= GATHER_TILES]
        if len(present) >= len(party.members) - 1:
            break
        await asyncio.sleep(2.0)
    return [party.me, *present], time.time() - began
