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
FOLLOW_ON_S = 45.0      # a person's line with no name in it follows on from an answer given this lately
# A person who leads the party (companion.py). Harry, after playing it: "the text replies could be maybe like 20%
# faster than they actually were" (6.3 s at the median; cuo-cym.22).
PERSON_PACE = 0.8       # the wait before an answer to that person, as a share of the wait among agents
READ_WAIT_S = 3.0       # an answer waits this long for the companion's own reading of the line (an order? for whom?)
TURN_HOLD_S = 14.0      # a line that is for several: each ahead in the turns has this long to say its answer
TURN_GAP_S = 2.5        # ...and the next speaks this long after the one before
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

# Added for the companion of a character a person plays (companion.py, cuo-cym.16).
LED = """
{person} leads this party and is played by a person; {me} is one of {person}'s companions. Answer what
{person} says as readily as anyone's, in the same voice: a companion, not a servant.
What {me} does for {person}: keeps beside them, fights what attacks the party and heals or bandages
whoever is hurt, all without being told. At {person}'s word {me} waits where they are, comes along again,
sits down with the party for a drink, or goes on ahead to a place (an inn, the bank) and waits there; and
{me} changes how they play when told: whether to loot, whether to run from a fight, how much to talk
("What {me} is doing" says which they are at now). Nothing else is done on command. Asked to fetch or buy
something, scout ahead or attack something nobody is fighting, {me} says in their own way that they won't,
and never promises it.
When the prompt says how {me} took {person}'s last line, the answer agrees with it: {me} never says they
will do what they aren't doing, and never speaks as if they had been told something else.
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


def talk_question(me: str, friend: str, person: bool = False) -> dict[str, Any]:
    """The yes/no before a line: should this character say something to that one now? person: the other is the
    leader of its party and a person plays them (cuo-cym.16), who is answered more readily than an agent: among
    agents saying nothing is the safe answer, and to a person it is being ignored."""
    if person:
        return {
            "type": "noul",
            "instructions": {"role": f"You decide whether {me}, a character in the game Ultima Online, answers {friend} "
                                     f"right now. {friend} leads the party {me} travels with and is played by a person, "
                                     f"who is talking with their companions as people do with friends in a game.",
                             "question": f"Should {me} say something back to {friend} now?"},
            "criteria": {
                "true": f"{friend}'s last line is something a companion would answer: a question, a remark, a joke or a "
                        f"jibe, thanks or praise, a greeting, something about the fight or the road, or a request "
                        f"for something {me} can't or won't do on command ('cover me', 'heal me': the answer is then "
                        f"a no, said in {me}'s own way), said to {me} by name, to the party at large, or following on "
                        f"from what {me} has just answered (see said_to). A person who speaks to their party and "
                        f"hears nothing back is being ignored. And {me} isn't in the middle of a fight.",
                "false": f"{friend}'s last line has nothing in it to answer: a word to themselves or about their own "
                         f"keyboard or absence ('brb', 'lol', 'ok', 'one sec'), an order that {me} has simply carried "
                         f"out, or words for a shopkeeper or a pet; or it is said to another companion, whose answer "
                         f"it follows on from (see said_to); or {me} has already answered it (see conversation); or "
                         f"{me} is busy fighting or fleeing.",
            },
        }
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


def said_to_words(me: str, friend: str, to: list[str], after: str, others: list[str], judged: bool = False) -> str:
    """Who a line of a friend's was for, in words, for the yes/no. to: the names in it. after: whose answer to that
    friend it follows straight on, when it names nobody. others: who else is in earshot. judged: `to` is system
    one's own reading of who the line is for (companion.for_question), not the names found in it."""
    if judged and me in to and len(to) > 1:
        return f"{', '.join(to)}: each of them is spoken to, and each answers for themselves"
    if judged and me in to:
        return f"{me}, and nobody else"
    if me in to:
        return f"{me}, by name"
    if after:
        # Said flatly, so the one who answered takes "And your reagents?" and the others leave it (6 lines of 6,
        # scripts/answer_check.py). It also takes "What did the rest of you make of it?", which isn't for it. Told
        # instead to judge that from the line, the yes/no got those wrong another way (all three said yes, or
        # nobody) and one of the six besides: who a line is for wants one question with the party as its
        # options, not a yes/no from each (cuo-cym.17).
        whose = f"{me}'s own" if after == me else f"{after}'s"
        return (f"no one by name; it follows straight on from {whose} answer to {friend}, so it is most likely said "
                f"to {after}")
    return f"no one by name; {len(others)} other{'s' if len(others) != 1 else ''} in earshot ({', '.join(others)})" \
        if others else f"no one by name, and only {me} in earshot"


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

    def busy_until(self, since: float = 0.0) -> float:
        """When the last line now over anyone's head (this character's included) will have gone; 0 when none is.
        since: only lines said from then on count (an answer to a line needn't wait for what was said before it)."""
        now = time.time()
        return max((r["until"] for r in self._since(max(now - 30, since), ("line", "callout", "emote")) if r["until"] > now),
                   default=0.0)

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
            self.release(mine)
            return False
        return True

    def release(self, row: int) -> None:
        """A claim or a turn given up."""
        if self.world is None:
            self.rows = [r for r in self.rows if r["id"] != row]
        else:
            self.world.db.execute("DELETE FROM talk_floor WHERE id = ?", (row,))
            self.world.db.commit()

    def turn(self, key: str) -> int:
        """A turn to answer a line that is for several (its key), and its number: they answer in the order they
        took their turns, each having heard the one before (cuo-cym.21). A line for nobody in particular went to
        whoever claimed it first, so "how are you all today?" got one answer and two companions left it."""
        return self._add("", "turn", key, None, time.time() + TURN_HOLD_S)

    def ahead(self, key: str, row: int) -> list[str]:
        """Who is still to speak before the holder of a turn: those who took one for the same line earlier, have
        said nothing since, and whose turn still holds."""
        now = time.time()
        rows = self._since(now - 60)
        spoke: dict[str, float] = {}
        for r in rows:
            if r["kind"] == "line":
                spoke[r["who"]] = max(spoke.get(r["who"], 0.0), r["t"])
        return [r["who"] for r in rows if r["kind"] == "turn" and r["answers"] == key and r["id"] < row
                and r["who"] != self.me and r["until"] > now and spoke.get(r["who"], 0.0) < r["t"]]


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
    since: float | None = None     # what its lateness is counted from: the line it answers, or the answer before it
    turn: bool = False             # one of several answers to a line: it follows the one before, not the floor
    owed: bool = False             # it has to be said (a setting changed, a refusal): not dropped for the talk moving on


@dataclass
class Turn:
    """An answer to a line that is for several, waiting for those ahead of it to say theirs before it is written."""
    key: str
    row: int
    friend: str
    why: str
    p: float
    heard: dict[str, Any]
    near: list[str]
    t: float
    reading: Any = None


def story_name(value: Any) -> str:
    """The name of the story a line tells, or "": a model's "no story" can come as two quote marks or the word none, and
    Haiku's now and then as a stray ">" or a piece of tool-call markup ("> </invoke>"), which was kept as a memory of
    a story told (cuo-dmt). A name has a word in it."""
    story = " ".join(str(value or "").split()).strip("\"' ")[:80]
    if story.lower() in ("none", "n/a", "no") or "<" in story or not re.search(r"[A-Za-z]{3}", story):
        return ""
    return story


class Ears:
    """Hearing and talking for one character, beside its planner (or, for a party's follower, on its own)."""

    def __init__(self, rpc: Any, me: str, friends: set[str], judge: Any = None, persona: dict[str, str] | None = None,
                 memory: "memories.Memory | None" = None, chat_fn: llm.ChatFn = llm.chat,
                 log: Callable[[dict[str, Any]], None] | None = None, doing: Callable[[], str] | None = None,
                 world: World | None = None, pace: bool = False, small_talk: bool = False, banter: bool = False,
                 person: str | None = None, pace_factor: float = 1.0):
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
        self.pace_factor = pace_factor  # ...for this share of that time (a fleet file's `pace = 0.7`)
        self.small_talk = small_talk  # they chat when they have time (cuo-cod.6)
        self.muted = False            # told to be quiet by a leader a person plays: only what it owes is said
        self.leader_party = False     # ...whose last line came in party chat: answers to it go there too
        # How its companion half read a line of the leader's (companion.Companion.reading, by the line's number
        # in the journal): an order taken, a setting changed, who the line is for. None: nobody reads them.
        self.reader: Callable[[int], Any] | None = None
        self.turn: Turn | None = None
        # One of those it hears whom a person plays, and who leads it (a party for the player): answered when
        # spoken to by name without the gap between lines, never cut short, and the writer knows who they are.
        self.person = person if person in self.friends else None
        self.system = SYSTEM + (SMALL_TALK if small_talk else "") + (BANTER if banter else "") \
            + (LED.format(person=self.person, me=me) if self.person else "")
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
        self.turns = 0          # answers given in turn to a line that was for several
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
            # Speech only: a name shown over a head (a label, "Torv (Young)") comes in from the same mobile. Words
            # for a banker, a vendor or a pet aren't talk; but what a leader a person plays says is read for whom
            # it is for (its companion half's reading), and by their pattern "Wystan stay" and "thea come", which
            # are orders to companions, were dropped from the talk as a pet's.
            judged = e.get("name") == self.person and self.reader is not None
            # Party chat from the person who leads is speech too: out of earshot it is the only way to reach them
            # (cuo-cym.29). Not the agents' own: a party's callouts can go there (callouts_to), and aren't talk.
            spoken = e.get("type", "regular") in SPOKEN or e.get("type") == "party" and e.get("name") == self.person
            if e.get("source") == "speech" and e.get("name") in self.friends and text \
                    and spoken and not text.startswith(e["name"]) \
                    and (judged or not ERRAND_WORDS.match(text)):
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
                # A person's line with no name in it, straight after a companion's answer to them ("And your
                # reagents?"): whose answer it follows is told to the yes/no, which judges who it is for.
                after = ""
                if e["name"] == self.person and not to and not aside:
                    last = [ln for ln in self.lines if not ln.get("aside")][-2:]
                    if len(last) == 2 and last[0]["who"] == self.person != last[1]["who"] and t - last[1]["t"] <= FOLLOW_ON_S:
                        after = last[1]["who"]
                # Who a line of the leader's is for is its companion half's reading where there is one (consider):
                # by the names in it, a line that only mentions a companion ("Wystan shot it first") was that
                # one's alone, and "you two" was anyone's.
                if e["name"] == self.person:
                    # The person who leads spoke in party chat (out of earshot, a call back): answered there too
                    # (cuo-cym.29). Called back that way, three companions answered aloud, and the leader heard none.
                    self.leader_party = e.get("type") == "party"
                line = {"t": t, "who": e["name"], "text": said, "to": to, "seq": e.get("seq", 0),
                        "answered": bool(to) and self.me not in to and not judged or aside or now - t > LATE_S,
                        **({"aside": True} if aside else {}), **({"after": after} if after else {}),
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
        others = sorted(n for n in self.near(snap) if n not in (friend, self.me))
        return said_to_words(self.me, friend, last.get("to") or [], last.get("after") or "", others)

    def near(self, snap: dict[str, Any]) -> list[str]:
        self.in_view = [m["name"] for m in snap.get("mobiles") or [] if m.get("name") in self.friends]
        return [m["name"] for m in snap.get("mobiles") or [] if m.get("name") in self.friends and m.get("distance", 99) <= NEAR]

    # ---- talking ------------------------------------------------------------------

    def allowed(self, direct: bool = False) -> bool:
        """Whether it may speak now. direct: in answer to a person who spoke to it by name, for whom the gap
        between lines doesn't hold (it keeps agents from chattering at each other; a person can't be made to wait
        20 s for the answer to a second question). The hour's cap and the budget hold for all."""
        now = time.time()
        if not direct and self.said_at and now - self.said_at[-1] < GAP_S:
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
        just met, or time on their hands (small talk). A person who leads comes first: its line isn't left
        waiting behind the companions' talk among themselves."""
        if self.person is not None and any(ln["who"] == self.person and not ln["answered"] and time.time() - ln["t"] < 60
                                           for ln in self.lines):
            return self.person, "answer"
        for line in reversed(self.lines):
            if line["who"] != self.me and not line["answered"] and time.time() - line["t"] < 60:
                if line["who"] != self.person and self.run_its_course(line["who"]):  # a person says when it is over
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
        due = self.due(snap)
        if due is None:
            return None
        friend, why = due
        led = self.person is not None and due == (self.person, "answer")  # a line of the person who leads
        heard = next((ln for ln in reversed(self.lines) if ln["who"] == friend and not ln["answered"]), None) \
            if why == "answer" else None
        waiting = self.ready or self.turn
        if waiting is not None:
            # An answer is in the making. Only a newer line of the leader's is looked at meanwhile: a yes to it
            # takes the place of the answer that waits (below), and anything else leaves that answer be. What
            # is owed is said first whatever comes.
            older = waiting.heard_t if isinstance(waiting, Ready) else waiting.heard["t"]
            owes = waiting.owed if isinstance(waiting, Ready) else bool(waiting.reading is not None and waiting.reading.owed)
            if owes or not led or heard is None or waiting.friend != self.person or older is None or heard["t"] <= older + 0.05:
                return None
        now = time.time()
        # How its companion half took the line (companion.Reading): an order it has said its word on, a setting
        # changed, who the line is for.
        reading = None
        if led and heard is not None and self.reader is not None:
            reading = self.reader(heard.get("seq", -1))
            if reading is None and now - heard["t"] < READ_WAIT_S:
                return None  # still being read
        owed = bool(reading is not None and reading.owed)
        judged = reading is not None and reading.to is not None
        if reading is not None and not owed:
            if reading.said:
                return self.settle(friend)  # its word on taking the order was its answer
            if judged and self.me not in reading.to:
                self.settle(friend)
                return self.drop("for another", friend=friend, text=heard["text"], to=list(reading.to))
        if judged:
            heard["to"] = list(reading.to)
        fighting = self.fighting(snap)
        if owed and (fighting or self.muted or not self.allowed(True)):
            # No line can be written now, or it was told to be quiet: its plain word. Told "shh keep it down for a
            # bit", each of three companions answered with a written sentence about being silent (cuo-cym.29).
            self.settle(friend)
            return await self.say_plain(reading, friend)
        if led and heard is not None and not owed and (self.muted or now - heard["t"] > LATE_S):
            return self.settle(friend)  # told to be quiet; or too old to answer (a fight came between)
        direct = led and (owed or judged or any(
            ln["who"] == self.person and not ln["answered"] and (self.me in (ln.get("to") or []) or ln.get("after") == self.me)
            for ln in self.lines))
        if self.muted and not owed or not self.allowed(direct):
            return None
        if fighting and (self.pace or why == "chat"):
            return None  # a fight has its callouts; talk waits
        self.greeted[friend] = now
        if why == "meet":
            for name in self.near(snap):  # one greeting for those who are there, not a question about each
                self.greeted[name] = now
        if why == "chat":
            self.chat_asked, self.idle_s = now, random.uniform(*IDLE_S)
        self.settle(friend)
        near = sorted(n for n in self.near(snap) if n != friend)
        state = {"who": f"{self.me}: " + "; ".join(f"{k} {v}" for k, v in self.persona.items() if k != "name"),
                 "doing": self.doing() or "nothing in particular", "fighting": fighting,
                 "conversation": self.conversation(friend) or ["(they haven't spoken yet)"],
                 "why_now": {"answer": why, "meet": why}.get(why, f"time on their hands: nobody has said anything for "
                                                                   f"{self.quiet_for():.0f} seconds"),
                 "said_to": said_to_words(self.me, friend, list(reading.to), "", near, judged=True) if judged
                 else self.said_to(friend, snap)}
        p = 1.0
        if owed:
            pass  # it has to be said: no yes/no on it
        elif self.judge is not None and getattr(self.judge, "name", "") != "heuristic":
            try:
                with costs.kind("chat"):
                    ans = await self.judge.ask(state, {"talk": talk_question(self.me, friend, friend == self.person)})
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
        key = "open" if heard is None else f"{friend}:{zlib.crc32(heard['text'].encode()):08x}"
        row, ahead = 0, []
        if judged and len(reading.to) > 1:
            # A line for several is answered by each it is for, in turn: the first to take a turn now, the next
            # once that one has spoken (next_turn).
            row = self.floor.turn(key)
            ahead = self.floor.ahead(key, row)
        elif not judged and not owed and (near or self.floor.world is not None) and not self.floor.claim(key):
            # One answer to a line nobody in particular was given, one opening at a time: the first of those in
            # earshot to claim it has it.
            self.drop("left to another", friend=friend, why=why)
            return None
        if waiting is not None:
            # The leader has said something else that this one answers: the answer to the line before gives way.
            if isinstance(waiting, Turn):
                self.floor.release(waiting.row)
            self.ready = self.turn = None
            self.drop("the leader said something else", to=friend, **({"text": waiting.text} if isinstance(waiting, Ready) else {}))
        if ahead:
            self.turn = Turn(key, row, friend, why, p, heard, near, now, reading)
            return None
        return await self.answer(friend, why, p, heard, near, reading, row=row)

    def settle(self, friend: str) -> None:
        """What a friend has said so far is dealt with: answered, or not this character's to answer."""
        for line in self.lines:
            if line["who"] == friend:
                line["answered"] = True

    async def answer(self, friend: str, why: str, p: float, heard: dict[str, Any] | None, near: list[str],
                     reading: Any = None, row: int = 0, after: bool = False) -> str | None:
        """Write the line and say it, or hold it for its moment (pace). What is owed and can't be written is
        said in its plain words."""
        ready = await self.write(friend, why, p, heard, near, reading=reading, after=after)
        if ready is None:
            if row:
                self.floor.release(row)
            return await self.say_plain(reading, friend) if reading is not None and reading.owed else None
        if not self.pace:
            return await self.say(ready)
        self.ready = ready
        return None

    async def next_turn(self, snap: dict[str, Any]) -> str | None:
        """One of several answers to a line: written once those ahead of it have said theirs (or had their time),
        so that it has heard them, and said a moment after the last."""
        t = self.turn
        if t is None or self.ready is not None:
            return None
        owed = bool(t.reading is not None and t.reading.owed)
        if self.fighting(snap):
            self.turn = None
            self.floor.release(t.row)
            return await self.say_plain(t.reading, t.friend) if owed else self.drop("a fight began", to=t.friend)
        if self.floor.ahead(t.key, t.row) and time.time() - t.t < TURN_HOLD_S:
            return None
        self.turn = None
        self.turns += 1
        return await self.answer(t.friend, t.why, t.p, t.heard, t.near, t.reading, row=t.row, after=True)

    async def say_plain(self, reading: Any, friend: str) -> str | None:
        """What this character owes on a line (a setting changed, a refusal), in the plain words that came with
        its reading: for when no line of its own can be written (a fight, the hour's cap, a failed call)."""
        text = getattr(reading, "plain", "")
        if not text:
            return None
        await self.out_loud(text, friend)
        now = time.time()
        self.floor.say(text, kind="callout")  # heard by the others, and not theirs to answer
        self.lines.append({"t": now, "who": self.me, "text": text, "answered": True, "aside": True})
        self.log({"type": "talk", "t": now, "said": True, "to": friend, "text": text, "why": "owed", "plain": True})
        return text

    def stories_told(self, friend: str) -> list[str]:
        if self.memory is not None and self.memory.character:
            rows = self.memory.list(kind="relationship", search="told", limit=40)
            return [r["text"].split(": ", 1)[-1] for r in rows
                    if "told" in (r.get("tags") or []) and friend.lower() in (r.get("tags") or [])][:8]
        return self.told.get(friend, [])[-8:]

    async def write(self, friend: str, why: str, p: float, heard: dict[str, Any] | None = None,
                    near: list[str] | None = None, reading: Any = None, after: bool = False) -> Ready | None:
        """The line, written by the cheap model; when it should be said. reading: how this character's companion
        half took the line it answers (companion.Reading), which the line has to agree with. after: one of
        several answers to a line, written after those before it."""
        owed = getattr(reading, "owed", "") if reading is not None else ""
        if owed:
            took = f"\n\nWhat {self.me}'s answer has to get across, whatever else it says: {owed}"
        elif reading is not None and friend == self.person:
            took = (f"\n\nHow {self.me} took {friend}'s last line: "
                    + (reading.note or f"as talk. It is no order {self.me} can carry out and changes nothing in how "
                                       f"{self.me} plays: {self.me} carries on as before ({self.doing() or 'beside them'})") + ".")
        else:
            took = ""
        if after:
            # Live, the second of three to answer "how are you all today?" began as the first had ("Right as rain,
            # Roland").
            took += (f"\n\n{friend}'s line was for several, and others have answered it just now (the last lines of "
                     f"the conversation). {self.me} answers for themselves: not in the words the others used, and "
                     f"not beginning as they began.")
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
                + took + f"\n\nWhy now: {reason}. Call say.")
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
        since = heard_t
        if after:
            # One of several answers: a moment after the last of the others', which it has heard.
            others = [ln["t"] for ln in self.lines if ln["who"] not in (self.me, friend) and not ln.get("aside")
                      and heard_t is not None and ln["t"] > heard_t]
            since = max(others, default=heard_t)
            say_at = max(now, max(others, default=now) + TURN_GAP_S) if self.pace else now
        elif owed or not self.pace:
            say_at = now  # its word on what it was told isn't held back
        else:
            # A person who leads is answered sooner than a companion (PERSON_PACE), and a fleet can ask for
            # more or less of the wait (pace_factor).
            share = self.pace_factor * (PERSON_PACE if friend == self.person else 1.0)
            say_at = (heard_t or now) + reply_delay(heard["text"] if heard else "", text) * share
        return Ready(text, friend, why, p, say_at, now, heard_t, bool(heard and self.me in (heard.get("to") or [])),
                     args, res.usage, since=since, turn=after, owed=bool(owed))

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
        since = r.since if r.since is not None else r.heard_t if r.heard_t is not None else r.made_at
        if self.fighting(snap) and not r.owed:
            self.ready = None
            return self.drop("a fight began", text=r.text)
        # An answer to the person who leads outlives what the others say meanwhile, and that person's next line
        # too unless this character answers that one instead (consider): three answers written for the leader
        # were thrown away in five minutes of play because it, or a companion, had spoken again (cuo-cym.21).
        if not r.addressed and r.friend != self.person and not r.owed \
                and any(ln["who"] != self.me and ln["t"] > since + 0.05 and not ln.get("aside") for ln in self.lines):
            self.ready = None
            return self.drop("the talk moved on", text=r.text, to=r.friend)
        if now - since > LATE_S and not r.owed:
            self.ready = None
            return self.drop("too late", text=r.text, to=r.friend)
        # One of several answers follows the one before it by its own clock; any other line waits for the floor.
        # An answer to the person who leads waits only for what was said after that person's line: the person
        # spoke knowing what was over the companions' heads, and live an answer due at once was held 6 s by the
        # line its own speaker had said just before the leader spoke.
        said_since = r.heard_t if r.friend == self.person and r.heard_t is not None else 0.0
        if now < r.say_at or not r.turn and self.floor.busy_until(said_since) + PAUSE_S > now:
            return None
        self.ready = None
        return await self.say(r)

    async def out_loud(self, text: str, friend: str) -> None:
        """A line aloud, or in party chat to a person who leads and last spoke there."""
        if friend == self.person and self.leader_party:
            await self.rpc.call("act", verb="party", kind="say", text=text)
        else:
            await self.rpc.call("act", verb="say", text=text)

    async def say(self, r: Ready) -> str:
        friend, text, args = r.friend, r.text, r.args
        await self.out_loud(text, friend)
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
        story = story_name(args.get("story"))
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
                  # One of several answers to a line (after the others'), or one it owed (a setting changed):
                  # neither waits as a first answer does, so they are told apart when the pace is measured.
                  **({"turn": True} if r.turn else {}), **({"owed": True} if r.owed else {}),
                  **({"delay_s": round(now - r.heard_t, 1)} if r.heard_t is not None else {}),
                  # ...and of that, when it was due and when its line was ready (cuo-cym.30: some answers came
                  # later than their pace allows).
                  **({"due_s": round(r.say_at - r.heard_t, 1), "written_s": round(r.made_at - r.heard_t, 1)}
                     if r.heard_t is not None else {}),
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
                **({"answers_in_turn": self.turns} if self.turns else {}),
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
                        self.ready = self.turn = None
                    else:
                        await self.deliver(snap)
                        await self.next_turn(snap)
                        await self.consider(snap)
                    self.close_quiet()
            except Exception as e:  # noqa: BLE001 - talk is never worth stopping play for
                self.log({"type": "talk", "t": time.time(), "error": f"{type(e).__name__}: {e}"[:200]})
            # A line that is ready is said at its moment, not at the next look: waiting the whole second said every
            # answer 0.6 to 1.0 s after it was due (cuo-cym.30).
            await asyncio.sleep(self.pause_s(every_s))

    def pause_s(self, every_s: float) -> float:
        """How long to wait before the next look: every_s, or less when a line ready to be said is due sooner."""
        if self.ready is None:
            return every_s
        return min(every_s, max(0.05, self.ready.say_at - time.time()))
