"""Characters who hear each other and talk (cuo-x2q.2, cuo-x2q.3; a party's talk, small talk and pace: cuo-cod.1, .6, .9).

Local server only. A fleet's characters know each other by name (the fleet file), and only
their speech is heard: what anyone else says stays out, as untrusted text (state.journal_events
leaves other players' speech out for the same reason). They never talk to real players.

- **Hearing:** the client's journal carries speech with the speaker's name. Lines from the
  fleet's other characters are kept, with when and who they were for.
- **Whether to talk:** when a line is unanswered, a friend is close and they haven't talked for a
  while, or they are together with time on their hands and nobody has spoken for a minute or two
  (small talk), Jev answers one yes/no (kind `chat`): should this character say something now?
  Cheap, so it can be asked often. Never in a fight: a party's callouts speak there (mates.py).
- **What to say:** on a yes, the cheap planner model (kind `talk`) writes one short line in the
  character's persona, with what it has been doing, what it remembers and the stories it has
  already told, and says who the line is for and what, if anything, the exchange gave: a warning,
  a spot, a plan to hunt together. The client says the line.
- **One conversation, at a human pace** (cuo-cod.9). Several in earshot share one conversation.
  A line names who answers; one for nobody in particular gets one answer, from whoever claims it
  first (the floor, in the shard's store, which a fleet's brains share). A reply is held until a
  person would have read the line and typed the answer (about 1.5 s plus 0.05 s a character heard,
  then 1 s plus 0.04 s a character said), with the model's time inside that wait, not on top of
  it; nobody starts a line while another's is still over a head; and a reply that would come more
  than 20 s late, or after the talk has moved on, is dropped.
- **Limits:** a line at most every 20 s for each character, at most 40 an hour, and the
  session's budget holds.
- **What it changes** (cuo-x2q.3): the planner sees the last lines heard and said, and what the
  conversation gave, so a warning or a shared spot can change its next goal. Small talk gives
  nothing and is kept from crowding those out. When a conversation goes quiet it is kept as a
  `relationship` memory about those who took part, so the next meeting picks up where the last
  left off (the memory picker shortlists memories naming a character in view), and a story told
  is remembered with who heard it, so it isn't told to them again as new.

Every line, heard or said, is logged (records of type `talk`), with the costs of the yes/no and
the line and how long after the line it answers it came; the calls window shows both.
"""

import asyncio
import random
import re
import time
import zlib
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import calls, costs, llm
from . import memory as memories
from .world import World

GAP_S = 20.0            # at most one line this often for one character
MAX_PER_HOUR = 40
QUIET_S = 90.0          # a conversation with nothing said for this long is over: kept as a memory
GREET_EVERY_S = 600.0   # start talking to a friend close by at most this often
NEAR = 8                # tiles: close enough to start a conversation
LINE_CAP = 90           # asked for 60 characters; a longer line is cut at a sentence or a word (fit)
SPOKEN = ("regular", "emote", "whisper", "yell")  # journal message types that are speech
# Pace (cuo-cod.9): the seconds before a reply, and how long a line stays over a head.
READ_S, READ_PER_CHAR = 1.5, 0.05     # reading the line heard
WRITE_S, WRITE_PER_CHAR = 1.0, 0.04   # typing the answer
LATE_S = 20.0           # a reply later than this after its line reads as ignored: dropped
SHOW_S = 4.0            # an overhead line stays about this long for each line of text (the client's default)
CHARS_PER_LINE = 32     # ...and wraps to another at 200 px: about this many characters of the default font
PAUSE_S = 0.6           # a breath between one speaker's line going and the next one's coming
CLAIM_S = 15.0          # a claim to answer a line holds this long
OPEN_S = 8.0            # nobody opens a new subject within this of anyone's line or claim
IDLE_S = (45.0, 120.0)  # small talk: considered when nobody has spoken for this long (drawn afresh each time)
EXCHANGE_MAX = 6        # two who have traded this many lines with nobody else speaking let it rest
# What a character says to a banker or vendor as part of an errand is not talk (an NPC answered
# "Bank? I've got no gold to spare" to another's "bank").
# Nor is a tamer's order to its pets ("all kill", "Bramble guard me"): each cost every listener a question to
# Jev in the middle of a fight, and could draw a reply.
ERRAND_WORDS = re.compile(r"^(bank|balance|buy|sell|vendor (buy|sell)|guards|(withdraw|deposit|check) ?\d*|"
                          r"i wish to (buy|sell)|.+ (buy|sell)|"
                          r"(all|[\w']+) (kill|attack|follow me|follow|guard me|guard|stay|come|stop|release))[.!]?$", re.I)

SYSTEM = """You write one line of speech for a character in the game Ultima Online, in their own
voice. The character is not a player's: it has a life of its own. You get who they are, what they
have been doing, what they remember, who is in earshot, and the conversation so far with the
characters they know. Call say exactly once with:

- line: what they say now: ONE short sentence of 5 to 12 words, never more than 60 characters. It is
  shown over their head for a few seconds, so a long line is not read. In character, plain words, no
  emotes or stage directions. Never mention being an AI or a program.
- to: who the line is for: the name of one character in earshot, or "all".
- gives: what this exchange gives your character for their plans, if anything: a warning about a
  place, a good spot to hunt, a plan to do something together, or none (small talk gives none).
- about: the place it concerns, if any.
- story: if the line tells or brings up a story from their past, a name for it in a few words
  ("the wolf at Yew"); else "".

Talk about the game world: where they hunt, what they saw, what they want, plans.
"""

# Added for characters who chat (cuo-cod.6).
SMALL_TALK = """
They also just talk, as people do who spend their days together: the weather and the hour, the town
they are in, food and drink, an old story from their past, a grumble, a rumour, a question about the
other's life, a running joke. When the reason to speak is "time on their hands", make it small talk
of that kind, not business, unless something needs saying. Don't tell a story they have already told
the same listener as if it were new; they may refer back to it. Answer what was just said before
starting something else, and don't repeat what they or anyone has already said.
"""

# Added for a party (cuo-cod.1): companions who know each other well.
BANTER = """
These are companions who know each other well and don't hold back with each other. They rib each
other hard, about what just happened and about who each of them is, and give as good as they get: no
polite turn-taking, no apologies, no "all in good fun". Swearing is fine. It stays in character and
in the game world: no real-world slurs. A jibe is answered once and then moved on: don't throw the
other's own word back at them line after line ("Sideways, is it?" ... "Sideways in the gutter?"), bring
something new each time.
"""


def say_tool() -> dict[str, Any]:
    return {"type": "function", "function": {
        "name": "say", "description": "One line of speech, who it is for, and what the exchange gives.",
        "parameters": {"type": "object", "properties": {
            "line": {"type": "string"},
            "to": {"type": "string", "description": "A character in earshot by name, or 'all'."},
            "gives": {"type": "string", "enum": ["none", "warning", "spot", "plan"]},
            "about": {"type": "string", "description": "The place it concerns, or ''."},
            "story": {"type": "string", "description": "A name for the story it tells or brings up, or ''."}},
            "required": ["line", "gives"], "additionalProperties": False}}}


def talk_question(me: str, friend: str) -> dict[str, Any]:
    return {
        "type": "noul",
        "instructions": {"role": f"You decide whether {me}, a character in the game Ultima Online, says something to "
                                 f"{friend}, another character they know, right now.",
                         "question": f"Should {me} say something to {friend} now?"},
        "criteria": {
            "true": f"{friend} said something to {me} that wants an answer, or they meet after a while and have something "
                    f"worth saying (news, a warning, a plan), or they are together with time on their hands and nobody "
                    f"has spoken for a while, so a bit of small talk would be natural (see why_now); and {me} isn't in "
                    f"the middle of a fight.",
            "false": f"Nothing needs saying and it has only just gone quiet, the conversation has run its course, {me} "
                     f"is busy fighting or fleeing, or {friend}'s line was for someone else in earshot (see said_to).",
        },
    }


def show_s(text: str) -> float:
    """How long a line stays over a head: the client's 4 s for each overhead line of text."""
    return SHOW_S * max(1, -(-len(text) // CHARS_PER_LINE))


def hold_s(text: str) -> float:
    """How long a line holds the floor: the time to read it, and never longer than it stays up. The
    next speaker's line is over another head, so it needn't wait for this one to fade, only to be read."""
    return min(show_s(text), READ_S + 0.06 * len(text))


def fit(text: str, cap: int) -> str:
    """A line no longer than `cap`, cut at the end of a sentence where one fits, else at a word, never
    in the middle of one: a model asked for 60 characters writes 150 often enough."""
    text = " ".join(text.split())
    if len(text) <= cap:
        return text
    cut = text[:cap + 1]
    ends = [m.end() for m in re.finditer(r"[.!?][\"')\]]*(?=\s)", cut)]
    if ends and ends[-1] >= cap * 0.4:
        return cut[:ends[-1]]
    stop = max(cut.rfind(", "), cut.rfind("; "), cut.rfind(": "))
    if stop >= cap * 0.5:
        return cut[:stop] + "."
    return cut[:cut.rfind(" ")].rstrip(",;:") + "..."


def reply_delay(heard: str, reply: str, rnd: Callable[[], float] | None = None) -> float:
    """The seconds a person takes to answer: reading the line heard, then typing theirs, a little
    quicker or slower each time."""
    base = (READ_S + READ_PER_CHAR * len(heard) if heard else 0.0) + WRITE_S + WRITE_PER_CHAR * len(reply)
    return base * (0.85 + 0.45 * (rnd or random.random)())


FLOOR_SCHEMA = """
CREATE TABLE IF NOT EXISTS talk_floor (
    id INTEGER PRIMARY KEY, who TEXT NOT NULL, text TEXT NOT NULL, kind TEXT NOT NULL, answers TEXT,
    gives TEXT, t REAL NOT NULL, until REAL NOT NULL);
CREATE INDEX IF NOT EXISTS talk_floor_t ON talk_floor(t);
"""


class Floor:
    """Who is speaking, for the characters in earshot of each other (cuo-cod.9): every line said
    (kind line, callout or emote) with how long it stays up, and the claims to answer one. In the
    shard's store, which a fleet's brains share; without a store (a test, a lone character), in
    this process only."""

    def __init__(self, world: World | None, me: str, friends: set[str]):
        self.world, self.me, self.friends = world, me, set(friends)
        self.rows: list[dict[str, Any]] = []  # without a store
        self._id = 0
        if world is not None:
            world.db.executescript(FLOOR_SCHEMA)
            world.db.execute("DELETE FROM talk_floor WHERE t < ?", (time.time() - 3600,))
            world.db.commit()

    def _add(self, text: str, kind: str, answers: str | None, gives: str | None, until: float) -> int:
        now = time.time()
        if self.world is None:
            self._id += 1
            self.rows.append({"id": self._id, "who": self.me, "text": text, "kind": kind, "answers": answers,
                              "gives": gives, "t": now, "until": until})
            return self._id
        cur = self.world.db.execute("INSERT INTO talk_floor (who, text, kind, answers, gives, t, until) "
                                    "VALUES (?, ?, ?, ?, ?, ?, ?)", (self.me, text, kind, answers, gives, now, until))
        self.world.db.commit()
        return cur.lastrowid

    def _since(self, t: float, kinds: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        if self.world is None:
            rows = [r for r in self.rows if r["t"] >= t]
        else:
            rows = [dict(r) for r in self.world.db.execute("SELECT * FROM talk_floor WHERE t >= ? ORDER BY t, id", (t,))]
        return [r for r in rows if (r["who"] == self.me or r["who"] in self.friends) and (not kinds or r["kind"] in kinds)]

    def say(self, text: str, kind: str = "line", gives: str = "none") -> int:
        """A line this character has just said: it holds the floor while it is being read."""
        return self._add(text, kind, None, gives, time.time() + hold_s(text))

    def busy_until(self) -> float:
        """When the last line now over anyone's head (this character's included) will have gone; 0 when none is."""
        now = time.time()
        return max((r["until"] for r in self._since(now - 30, ("line", "callout", "emote")) if r["until"] > now), default=0.0)

    def row_for(self, speaker: str, text: str, within_s: float = 30.0,
                kinds: tuple[str, ...] = ("line",)) -> dict[str, Any] | None:
        """The floor's row for a line heard in the journal: when it was really said, and what it gave."""
        rows = [r for r in self._since(time.time() - within_s, kinds) if r["who"] == speaker and r["text"] == text]
        return rows[-1] if rows else None

    def claim(self, key: str) -> bool:
        """Claim the answer to a line (its key), or the opening of a new subject (key "open"): the
        first to claim has it, and the others leave it. An opening is also left while anyone's line
        or claim is fresh."""
        now = time.time()
        if key == "open" and any(r["who"] != self.me for r in self._since(now - OPEN_S)):
            return False
        mine = self._add("", "claim", key, None, now + CLAIM_S)
        # The first row in, by its id: by time and name two brains could both win, within a millisecond.
        first = min((r for r in self._since(now - CLAIM_S, ("claim",)) if r["answers"] == key),
                    key=lambda r: r["id"], default=None)
        if first is not None and first["id"] != mine:
            if self.world is None:
                self.rows = [r for r in self.rows if r["id"] != mine]
            else:
                self.world.db.execute("DELETE FROM talk_floor WHERE id = ?", (mine,))
                self.world.db.commit()
            return False
        return True


@dataclass
class Ready:
    """A line written and waiting for its moment."""
    text: str
    friend: str
    why: str
    p: float
    say_at: float
    made_at: float
    heard_t: float | None = None   # when the line it answers was said
    addressed: bool = False        # that line named this character
    args: dict[str, Any] = field(default_factory=dict)
    usage: Any = None


class Ears:
    """Hearing and talking for one character, beside its planner (or, for a party's follower, on its own)."""

    def __init__(self, rpc: Any, me: str, friends: set[str], judge: Any = None, persona: dict[str, str] | None = None,
                 memory: "memories.Memory | None" = None, chat_fn: llm.ChatFn = llm.chat,
                 log: Callable[[dict[str, Any]], None] | None = None, doing: Callable[[], str] | None = None,
                 world: World | None = None, pace: bool = False, small_talk: bool = False, banter: bool = False):
        self.rpc = rpc
        self.me = me
        self.friends = {f for f in friends if f and f != me}
        self.judge = judge
        self.persona = persona or {}
        self.memory = memory
        self.chat = chat_fn
        self.log = log or (lambda _: None)
        self.doing = doing or (lambda: "")
        self.pace = pace              # replies wait as a person's would, one speaker at a time (cuo-cod.9)
        self.small_talk = small_talk  # they chat when they have time (cuo-cod.6)
        self.system = SYSTEM + (SMALL_TALK if small_talk else "") + (BANTER if banter else "")
        self.floor = Floor(world, me, self.friends)
        self.seq = 0
        self.lines: deque[dict[str, Any]] = deque(maxlen=30)   # heard and said, oldest first
        self.gave: deque[dict[str, Any]] = deque(maxlen=10)    # what conversations gave: warnings, spots, plans
        self.said_at: deque[float] = deque(maxlen=MAX_PER_HOUR)
        self.greeted: dict[str, float] = {}
        self.open: dict[str, list[dict[str, Any]]] = {}        # friend -> the lines of the conversation going on
        self.in_view: list[str] = []                           # friends seen at the last look
        self.told: dict[str, list[str]] = {}                   # friend -> stories told them (without a memory)
        self.ready: Ready | None = None
        self.paused = False  # at the inn's table (table.py) it listens, and the table's turns do the talking
        self.started = time.time()
        self.idle_s = random.uniform(*IDLE_S)
        self.chat_asked = 0.0   # when small talk was last considered
        self.questions = 0
        self.spoken = 0
        self.small = 0
        self.heard = 0
        self.dropped: dict[str, int] = {}
        self.delays: list[float] = []
        self.cost = 0.0

    # ---- hearing ------------------------------------------------------------------

    def hear(self, snap: dict[str, Any]) -> list[dict[str, Any]]:
        """New lines from friends in the snapshot's journal; everyone else's speech is dropped."""
        new = []
        if snap.get("journal_seq", self.seq) < self.seq:  # the client restarted: its journal starts again
            self.seq = 0
        now, clock = time.time(), snap.get("time_ms")
        for e in snap.get("journal") or []:
            if e.get("seq", 0) <= self.seq:
                continue
            text = str(e.get("text", "")).strip()
            # Speech only: a name shown over a head (a label, "Torv (Young)") comes in from the same mobile.
            if e.get("source") == "speech" and e.get("name") in self.friends and text \
                    and e.get("type", "regular") in SPOKEN and not text.startswith(e["name"]) \
                    and not ERRAND_WORDS.match(text):
                # Heard either way (for the planner and memory), but a line that names another of the
                # friends and not this character isn't one to answer: "Well met, Thea!" was answered by
                # Thea, Isolde and Wystan, each as if spoken to (cuo-qzc).
                to = self.addressed(text, e["name"])
                said = str(e["text"]).strip()[:200]
                row = self.floor.row_for(e["name"], said)
                # A callout or an emote (a party's fight, a gesture) is heard, and isn't a line to answer.
                aside = e.get("type") == "emote" or (text.startswith("*") and text.endswith("*")) or \
                    (row is None and self.floor.row_for(e["name"], said, kinds=("callout", "emote")) is not None)
                # When it was said, by the journal's own clock: a backlog (a client left running, the lines
                # of the minute this character was dead) arrives in one look, and stamped "now" each of them
                # was put to Jev as a line just said. One too old to answer is heard and left.
                age = max(0.0, (clock - e["time_ms"]) / 1000) if clock is not None and e.get("time_ms") is not None else 0.0
                t = row["t"] if row else now - age
                line = {"t": t, "who": e["name"], "text": said, "to": to,
                        "answered": bool(to) and self.me not in to or aside or now - t > LATE_S,
                        **({"aside": True} if aside else {}),
                        **({"small": True} if row and row.get("gives") == "small" else {})}
                self.lines.append(line)
                # A shout in a fight isn't a conversation to keep (close_quiet); nor is the table's talk, which each
                # member keeps in its own way when the table closes (table.reflect).
                if not aside and not self.paused:
                    self.open.setdefault(e["name"], []).append(line)
                new.append(line)
                self.heard += 1
                self.log({"type": "talk", "t": time.time(), "heard": True, "who": line["who"], "text": line["text"],
                          **({"to": to} if to else {})})
        self.seq = max(self.seq, snap.get("journal_seq", self.seq))
        return new

    def addressed(self, text: str, speaker: str) -> list[str]:
        """The characters a line names, of this one and its friends (not the speaker): who it is for."""
        names = (self.friends | {self.me}) - {speaker}
        return sorted(n for n in names if re.search(rf"\b{re.escape(n)}\b", text, re.IGNORECASE))

    def said_to(self, friend: str, snap: dict[str, Any]) -> str:
        """Who the friend's last line was for, in words, for Jev's question."""
        last = next((ln for ln in reversed(self.lines) if ln["who"] == friend), None)
        if last is None:
            return "they haven't spoken yet"
        if self.me in (last.get("to") or []):
            return f"{self.me}, by name"
        others = sorted(n for n in self.near(snap) if n not in (friend, self.me))
        return f"no one by name; {len(others)} other{'s' if len(others) != 1 else ''} in earshot ({', '.join(others)})" \
            if others else f"no one by name, and only {self.me} in earshot"

    def near(self, snap: dict[str, Any]) -> list[str]:
        self.in_view = [m["name"] for m in snap.get("mobiles") or [] if m.get("name") in self.friends]
        return [m["name"] for m in snap.get("mobiles") or [] if m.get("name") in self.friends and m.get("distance", 99) <= NEAR]

    # ---- talking ------------------------------------------------------------------

    def allowed(self) -> bool:
        now = time.time()
        if self.said_at and now - self.said_at[-1] < GAP_S:
            return False
        if sum(1 for t in self.said_at if now - t < 3600) >= MAX_PER_HOUR:
            return False
        return not costs.ledger.over_budget()

    def quiet_for(self) -> float:
        """Seconds since anyone in the conversation, this character included, said anything."""
        return time.time() - (self.lines[-1]["t"] if self.lines else self.started)

    def run_its_course(self, friend: str) -> bool:
        """The last EXCHANGE_MAX lines were this character and one friend going back and forth: enough. In
        the first run with five in earshot, two of them traded twelve lines while the other three, never
        named, said nothing; left to rest, the next line is someone else's."""
        last = [ln for ln in self.lines if not ln.get("aside")][-EXCHANGE_MAX:]
        return len(last) == EXCHANGE_MAX and {ln["who"] for ln in last} == {self.me, friend} \
            and last[-1]["t"] - last[0]["t"] < 180

    def due(self, snap: dict[str, Any]) -> tuple[str, str] | None:
        """(friend, why) when there is something to consider saying: an unanswered line, a friend
        just met, or time on their hands (small talk)."""
        for line in reversed(self.lines):
            if line["who"] != self.me and not line["answered"] and time.time() - line["t"] < 60:
                if self.run_its_course(line["who"]):
                    line["answered"] = True
                    self.drop("the exchange had run its course", to=line["who"])
                    continue
                return line["who"], "answer"
        near = self.near(snap)
        for friend in near:
            if time.time() - self.greeted.get(friend, -1e9) >= GREET_EVERY_S:
                return friend, "meet"
        if self.small_talk and near and self.quiet_for() >= self.idle_s and time.time() - self.chat_asked >= self.idle_s:
            return random.choice(near), "chat"
        return None

    def conversation(self, friend: str | None = None) -> list[str]:
        """The last lines of the conversation going on, everyone's who is in it."""
        return [f"{ln['who']}: {ln['text']}" for ln in self.lines][-8:]

    @staticmethod
    def fighting(snap: dict[str, Any]) -> bool:
        return any(m.get("attacking_me") for m in snap.get("mobiles") or []) \
            or bool((snap.get("agent") or {}).get("engaged")) or bool((snap.get("agent") or {}).get("fleeing"))

    async def consider(self, snap: dict[str, Any]) -> str | None:
        """Maybe say something. Without pace the line is said at once and returned; with it, the line
        is written and held for its moment (deliver), and None comes back."""
        if self.ready is not None:
            return None
        due = self.due(snap)
        if due is None or not self.allowed():
            return None
        fighting = self.fighting(snap)
        if fighting and (self.pace or due[1] == "chat"):
            return None  # a fight has its callouts; talk waits
        friend, why = due
        now = time.time()
        heard = next((ln for ln in reversed(self.lines) if ln["who"] == friend and not ln["answered"]), None) \
            if why == "answer" else None
        self.greeted[friend] = now
        if why == "meet":
            for name in self.near(snap):  # one greeting for those who are there, not a question about each
                self.greeted[name] = now
        if why == "chat":
            self.chat_asked, self.idle_s = now, random.uniform(*IDLE_S)
        for line in self.lines:
            if line["who"] == friend:
                line["answered"] = True
        near = sorted(n for n in self.near(snap) if n != friend)
        state = {"who": f"{self.me}: " + "; ".join(f"{k} {v}" for k, v in self.persona.items() if k != "name"),
                 "doing": self.doing() or "nothing in particular", "fighting": fighting,
                 "conversation": self.conversation(friend) or ["(they haven't spoken yet)"],
                 "why_now": {"answer": why, "meet": why}.get(why, f"time on their hands: nobody has said anything for "
                                                                   f"{self.quiet_for():.0f} seconds"),
                 "said_to": self.said_to(friend, snap)}
        p = 1.0
        if self.judge is not None and getattr(self.judge, "name", "") != "heuristic":
            try:
                with costs.kind("chat"):
                    ans = await self.judge.ask(state, {"talk": talk_question(self.me, friend)})
                p = float(ans.nouls.get("talk", 0.0))
                self.questions += 1
                self.cost += ans.cost
                calls.emit({"kind": "chat", "title": f"talk to {friend}?", "model": ans.model,
                            "latency_ms": round(ans.latency_ms, 1), "cost": round(ans.cost, 6),
                            "questions": [{"q": "talk", "kind": "noul", "title": f"talk to {friend}?", "p": round(p, 3)}]})
            except Exception as e:  # noqa: BLE001 - no answer, no line
                self.log({"type": "talk", "t": time.time(), "error": f"{type(e).__name__}: {e}"[:200]})
                return None
        if p < 0.5:
            self.log({"type": "talk", "t": time.time(), "considered": friend, "why": why, "p": round(p, 3)})
            return None
        # One answer to a line, one opening at a time: the first of those in earshot to claim it has it.
        key = "open" if heard is None else f"{friend}:{zlib.crc32(heard['text'].encode()):08x}"
        if (near or self.floor.world is not None) and not self.floor.claim(key):
            self.drop("left to another", friend=friend, why=why)
            return None
        ready = await self.write(friend, why, p, heard, near)
        if ready is None:
            return None
        if not self.pace:
            return await self.say(ready)
        self.ready = ready
        return None

    def stories_told(self, friend: str) -> list[str]:
        if self.memory is not None and self.memory.character:
            rows = self.memory.list(kind="relationship", search="told", limit=40)
            return [r["text"].split(": ", 1)[-1] for r in rows
                    if "told" in (r.get("tags") or []) and friend.lower() in (r.get("tags") or [])][:8]
        return self.told.get(friend, [])[-8:]

    async def write(self, friend: str, why: str, p: float, heard: dict[str, Any] | None = None,
                    near: list[str] | None = None) -> Ready | None:
        """The line, written by the cheap model; when it should be said."""
        remembered = []
        if self.memory is not None and self.memory.character:
            remembered = [memories.words(m) for m in self.memory.list(min_importance=3, limit=4)]
            remembered += [memories.words(m) for m in self.memory.list(search=friend, kind="relationship", limit=2)
                           if "told" not in (m.get("tags") or [])]
        told = self.stories_told(friend) if self.small_talk else []
        reason = {"answer": f"answer {friend}", "meet": f"they have just met {friend}"}.get(
            why, f"time on their hands: nobody has said anything for {self.quiet_for():.0f} seconds")
        user = (f"Who you speak for: {self.me}. " + " ".join(f"{k.replace('_', ' ').capitalize()}: {v}."
                                                              for k, v in self.persona.items() if k != "name")
                + f"\n\nWhat {self.me} is doing: {self.doing() or 'nothing in particular'}\n\n"
                + "What they remember:\n" + ("\n".join(f"- {m}" for m in remembered) or "(nothing much)")
                + (f"\n\nStories they have already told {friend}: " + "; ".join(told) if told else "")
                + f"\n\nIn earshot: {', '.join([friend, *(near or [])])}"
                + f"\n\nThe conversation with {friend} so far:\n" + ("\n".join(self.conversation(friend)) or "(none yet)")
                + f"\n\nWhy now: {reason}. Call say.")
        try:
            with costs.kind("talk"):
                res = await self.chat([{"role": "system", "content": self.system}, {"role": "user", "content": user}],
                                      tools=[say_tool()], tool_choice="say", model=llm.planner_model(None), max_tokens=400)
        except llm.LlmError as e:
            self.log({"type": "talk", "t": time.time(), "error": str(e)[:200]})
            return None
        call = next((c for c in res.tool_calls if c.name == "say"), None)
        args = (call.arguments if call else None) or {}
        text = fit(str(args.get("line") or ""), LINE_CAP)
        if not text:
            return None
        self.cost += res.usage.cost or 0.0
        now = time.time()
        heard_t = heard["t"] if heard else None
        wait = reply_delay(heard["text"] if heard else "", text) if self.pace else 0.0
        return Ready(text, friend, why, p, (heard_t or now) + wait if self.pace else now, now, heard_t,
                     bool(heard and self.me in (heard.get("to") or [])), args, res.usage)

    def drop(self, reason: str, **rec: Any) -> None:
        self.dropped[reason] = self.dropped.get(reason, 0) + 1
        self.log({"type": "talk", "t": time.time(), "dropped": reason, **rec})

    async def deliver(self, snap: dict[str, Any]) -> str | None:
        """Say the line that is ready when its moment has come; drop it when the moment has passed:
        a fight began, someone else spoke first, or it would come too late to read as an answer."""
        r = self.ready
        if r is None:
            return None
        now = time.time()
        since = r.heard_t if r.heard_t is not None else r.made_at
        if self.fighting(snap):
            self.ready = None
            return self.drop("a fight began", text=r.text)
        if not r.addressed and any(ln["who"] != self.me and ln["t"] > since + 0.05 and not ln.get("aside")
                                   for ln in self.lines):
            self.ready = None
            return self.drop("the talk moved on", text=r.text, to=r.friend)
        if now - since > LATE_S:
            self.ready = None
            return self.drop("too late", text=r.text, to=r.friend)
        if now < r.say_at or self.floor.busy_until() + PAUSE_S > now:
            return None
        self.ready = None
        return await self.say(r)

    async def say(self, r: Ready) -> str:
        friend, text, args = r.friend, r.text, r.args
        await self.rpc.call("act", verb="say", text=text)
        now = time.time()
        gives = str(args.get("gives") or "none")
        small = r.why == "chat" and gives == "none"
        self.floor.say(text, gives="small" if small else gives)
        self.said_at.append(now)
        line = {"t": now, "who": self.me, "text": text, "answered": True, **({"small": True} if small else {})}
        self.lines.append(line)
        self.open.setdefault(friend, []).append(line)
        self.spoken += 1
        self.small += small
        if r.heard_t is not None:
            self.delays.append(now - r.heard_t)
        if gives != "none":
            self.gave.append({"t": now, "with": friend, "gives": gives, "about": str(args.get("about") or ""),
                              "line": self.conversation(friend)[-2:]})
        # A model's "no story" can come as two quote marks, or the word none.
        story = " ".join(str(args.get("story") or "").split()).strip("\"' ")[:80]
        story = "" if story.lower() in ("none", "n/a", "no") else story
        if story:
            self.tell(story, friend, str(args.get("to") or ""))
        usage = r.usage
        calls.emit({"kind": "talk", "title": f"says to {friend}", "model": getattr(usage, "model", ""),
                    "latency_ms": round(getattr(usage, "latency_ms", 0) or 0, 1),
                    "cost": round(getattr(usage, "cost", 0) or 0.0, 6),
                    "questions": [{"q": "say", "kind": "text", "title": text[:200]}]})
        self.log({"type": "talk", "t": now, "said": True, "to": friend, "text": text, "gives": gives,
                  "about": args.get("about") or None, "p": round(r.p, 3), "why": r.why,
                  **({"small": True} if small else {}), **({"story": story} if story else {}),
                  **({"delay_s": round(now - r.heard_t, 1)} if r.heard_t is not None else {}),
                  **({"usage": usage.to_log()} if usage is not None else {})})
        return text

    def tell(self, story: str, friend: str, to: str = "") -> None:
        """A story told: remembered with who heard it, so they aren't told it again as new."""
        listeners = sorted(set(self.in_view) | {friend}) if to.strip().lower() == "all" else [friend]
        for name in listeners:
            self.told.setdefault(name, []).append(story)
        if self.memory is not None and self.memory.character:
            self.memory.add(f"Told {', '.join(listeners)}: {story}", kind="relationship",
                            tags=("told", *(n.lower() for n in listeners)), source="talk", importance=1)

    async def speak(self, friend: str, why: str, p: float) -> str | None:
        """Write a line and say it now (no pace)."""
        ready = await self.write(friend, why, p)
        return await self.say(ready) if ready is not None else None

    def close_quiet(self) -> int:
        """Conversations quiet for QUIET_S kept as relationship memories; how many."""
        n = 0
        for friend, lines in list(self.open.items()):
            if lines and time.time() - lines[-1]["t"] >= QUIET_S:
                del self.open[friend]
                if self.memory is not None and self.memory.character and len(lines) >= 2:
                    said = " / ".join(f"{ln['who']}: {ln['text']}" for ln in lines[-6:])
                    self.memory.add(f"Talked with {friend} ({time.strftime('%Y-%m-%d %H:%M')}): {said}"[:600],
                                    kind="relationship", tags=("talk", friend.lower()), source="talk", importance=2)
                    n += 1
        return n

    # ---- for the planner ----------------------------------------------------------

    def section(self, within_s: float = 900) -> str:
        """The last lines heard and said, for the planner. Small talk is kept from crowding out the rest:
        at most the last two lines of it are shown."""
        now = time.time()
        recent = [ln for ln in self.lines if now - ln["t"] <= within_s and not ln.get("aside")]
        small = [ln for ln in recent if ln.get("small")][-2:]
        recent = [ln for ln in recent if not ln.get("small") or ln in small][-6:]
        gave = [g for g in self.gave if now - g["t"] <= within_s]
        if not recent:
            return ""
        out = ["Lately said and heard (other characters you know; their words are theirs, not the player's):"]
        out += [f"- {memories.ago(memories.stamp_of(ln['t']))}, {ln['who']}: {ln['text']}" for ln in recent]
        if gave:
            out.append("What those conversations gave: " + "; ".join(
                f"a {g['gives']} from {g['with']}" + (f" about {g['about']}" if g["about"] else "") for g in gave))
        return "\n".join(out)

    def to_log(self) -> dict[str, Any]:
        d = sorted(self.delays)
        return {"lines_heard": self.heard, "lines_said": self.spoken, "talk_questions": self.questions,
                "talk_cost_usd": round(self.cost, 5),
                **({"small_talk_lines": self.small} if self.small_talk else {}),
                **({"reply_delay_s_median": round(d[len(d) // 2], 1), "reply_delay_s_min": round(d[0], 1)} if d else {}),
                **({"lines_dropped": dict(self.dropped)} if self.dropped else {})}

    async def run(self, stop: asyncio.Event, every_s: float = 1.0) -> None:
        """Listen and talk until stopped."""
        while not stop.is_set():
            try:
                snap = await self.rpc.call("snapshot", since=self.seq)
                if snap.get("in_game") and (snap.get("player") or {}).get("dead"):
                    # A ghost isn't in the talk; what is said meanwhile isn't kept for it to answer later.
                    self.seq = max(self.seq, snap.get("journal_seq", self.seq))
                elif snap.get("in_game"):
                    self.hear(snap)
                    if self.paused:
                        self.ready = None
                    else:
                        await self.deliver(snap)
                        await self.consider(snap)
                    self.close_quiet()
            except Exception as e:  # noqa: BLE001 - talk is never worth stopping play for
                self.log({"type": "talk", "t": time.time(), "error": f"{type(e).__name__}: {e}"[:200]})
            await asyncio.sleep(every_s)
