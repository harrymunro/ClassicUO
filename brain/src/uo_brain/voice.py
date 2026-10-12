"""A character's voice: the short things it shouts in a fight and the small things it does (cuo-cod.2, cuo-cod.8).

A fight has no time for a model call per line (a yes/no and a line take about 2 s, and the moment
has passed), so the lines are written once: at the start of a session the cheap planner model
(kind `talk`) writes a few lines for each kind of callout and emote in the character's persona,
with slots for the creature's and the companion's name, and code fills the slot and picks a line
not said lately. A pack is kept in the shard's store (table voice_packs) under the character and
its persona, so the next session of the same character doesn't pay again. Without a persona, or
when the call fails, the plain lines below are used.

Local server only, as all talk (talk.py): nothing here speaks on a real shard.
"""

import hashlib
import json
import random
import re
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from . import costs, llm
from .world import World

LINE_CAP = 60   # one overhead line; the client wraps at about that
VERSION = 3     # of the kinds and the prompt: a new one writes new packs
# A word before a slot that already says which one: the slot's own article is left out after it ("Get this
# {target} off me" with "the bone knight" was said as "this the bone knight").
DETERMINER = r"(?:the|this|that|these|those|a|an|my|your|his|her|its|our|their|some|another|every|each|no)"
ARTICLE = re.compile(r"^(?:the|a|an)\s+", re.I)

# kind -> (the slot it needs or "", what it is for, plain lines). Callouts first, then emotes: an
# emote is said as *...*, so its lines are what the character does, without the asterisks.
KINDS: dict[str, tuple[str, str, list[str]]] = {
    "hurt": ("", "shouted when badly wounded, under half health",
             ["I'm hurt!", "Taking a beating here!", "I'm bleeding!"]),
    "badly_hurt": ("", "shouted when near death, under a third of its health",
                   ["I'm nearly dead!", "I'm going down!", "Help me, now!"]),
    "recovered": ("", "said when back on its feet after being badly wounded",
                  ["I'm all right.", "Back in it.", "Still standing."]),
    "on_target": ("target", "called when it starts on a creature, so the others know which one it has",
                  ["I've got {target}.", "I'm on {target}!", "{target} is mine."]),
    "on_me": ("target", "shouted when a dangerous creature is attacking it",
              ["{target} on me!", "{target} is on me, get it off!", "Someone take {target}!"]),
    "leaving": ("", "shouted when it runs from the fight",
                ["Falling back!", "I'm out, run!", "Pulling back!"]),
    "out_of_bandages": ("", "shouted when its bandages run out",
                        ["Out of bandages!", "No bandages left!"]),
    "out_of_reagents": ("", "shouted when a mage's reagents run out",
                        ["Out of reagents!", "No reagents, no spells!"]),
    "out_of_arrows": ("", "shouted when an archer's arrows run out",
                      ["Out of arrows!", "My quiver's empty!"]),
    "down": ("name", "shouted when a companion falls dead",
             ["{name} is down!", "We've lost {name}!", "{name} has fallen!"]),
    "clear": ("", "said when the last creature of a fight is dead",
              ["All clear.", "That's the last of them.", "It's done."]),
    "helping": ("name", "said to a hurt companion while bandaging or healing them",
                ["Hold still, {name}.", "I've got you, {name}.", "Patching you up, {name}."]),
    # Emotes (cuo-cod.8): what the character does, shown as *...*.
    "e_set_out": ("", "an emote on setting out on a trip", ["shoulders their pack", "checks their gear"]),
    "e_rest": ("", "an emote while resting", ["sits down for a moment", "stretches and looks about"]),
    "e_kill": ("", "an emote after a kill", ["wipes their brow", "nods, satisfied"]),
    "e_fallen": ("name", "an emote over a fallen companion's body", ["kneels by {name}", "bows their head over {name}"]),
    "e_drink": ("", "an emote at an inn table: a drink, acted out", ["has a swig of ale", "drains their tankard",
                                                                     "pours another"]),
    "e_toast": ("name", "an emote at an inn table: a toast to a companion", ["raises a tankard to {name}"]),
    "e_arrive": ("", "an emote on arriving at the inn after a hunt", ["drops into a chair", "waves for ale"]),
}
CALLOUTS = tuple(k for k in KINDS if not k.startswith("e_"))
EMOTES = tuple(k for k in KINDS if k.startswith("e_"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS voice_packs (
    character TEXT NOT NULL, key TEXT NOT NULL, pack TEXT NOT NULL, model TEXT, t REAL NOT NULL,
    PRIMARY KEY (character, key));
"""

SYSTEM = """You write a character's voice for the game Ultima Online: the short things they shout to their
party in a fight, and the small things they do, in their own way of talking. You get who they are and who
their companions are. Call voice exactly once, with three or four lines for every kind asked.

- Each line is at most 45 characters, one breath: it is shouted in a fight or shown over the character's
  head for a few seconds.
- In character and in the game world. Blunt, rude and funny is good; they don't hold back with each other,
  and swearing is fine. No real-world slurs. Never mention being an AI or a program.
- Where a kind names a slot, every line of it must contain that slot exactly as written: {target} is the
  creature (code puts in "the lich", "a bone knight"), {name} a companion's name. No other slots, and no
  companion's name written out: code fills it in. {target} brings its own article: never write "the", "this",
  "that" or an adjective in front of it ("{target} is mine!", not "the {target} is mine!").
- The kinds beginning e_ are emotes: what the character does, in the third person and present tense,
  without asterisks or the character's own name ("has a swig of ale", "checks his bowstring"). Use the
  pronoun that fits the character.
- The meaning of each line must stay plain to someone hearing it in a fight: a line for "out of arrows"
  says there are no arrows left, however it is put.
"""


def voice_tool() -> dict[str, Any]:
    props = {k: {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 4,
                 "description": what + (f" (every line contains {{{slot}}})" if slot else "")}
             for k, (slot, what, _) in KINDS.items()}
    return {"type": "function", "function": {
        "name": "voice", "description": "The character's lines, a few for each kind.",
        "parameters": {"type": "object", "properties": props, "required": list(KINDS), "additionalProperties": False}}}


def clean(lines: Any, slot: str) -> list[str]:
    """The lines of one kind that can be used: text, short enough, with the slot it needs and no other."""
    out = []
    for line in lines if isinstance(lines, list) else []:
        text = " ".join(str(line).split()).strip("*").strip()
        if not text or len(text) > LINE_CAP + 10:
            continue
        slots = {s.split("}")[0] for s in text.split("{")[1:]}
        if slots != ({slot} if slot else set()):
            continue
        out.append(text)
    return out


class Voice:
    """A character's lines by kind, and which were said lately."""

    def __init__(self, lines: dict[str, list[str]] | None = None, plain: bool = True):
        self.lines = {k: list(d) for k, (_, _, d) in KINDS.items()}
        self.plain = plain
        for k, v in (lines or {}).items():
            if k in KINDS and (ok := clean(v, KINDS[k][0])):
                self.lines[k] = ok
                self.plain = False
        self.said: deque[str] = deque(maxlen=6)

    def pick(self, kind: str, **slots: str) -> str:
        """A line of this kind not said lately, its slot filled in; "" for a kind it doesn't have."""
        options = self.lines.get(kind) or []
        if not options:
            return ""
        fresh = [o for o in options if o not in self.said] or options
        line = random.choice(fresh)
        self.said.append(line)
        slot = KINDS[kind][0]
        if slot:
            value = slots.get(slot) or {"target": "it", "name": "them"}[slot]
            bare = ARTICLE.sub("", value)
            line = re.sub(rf"\b({DETERMINER})\s+\{{{slot}\}}", lambda m: f"{m[1]} {bare}", line, flags=re.I)
            line = line.replace("{" + slot + "}", value)
        line = line[:1].upper() + line[1:] if not kind.startswith("e_") else line
        return line[:LINE_CAP + 20]

    def to_json(self) -> dict[str, list[str]]:
        return self.lines


def key_of(persona: dict[str, str] | None, companions: list[str] | None = None) -> str:
    body = json.dumps([VERSION, persona or {}, sorted(companions or [])], sort_keys=True)
    return hashlib.sha1(body.encode()).hexdigest()[:16]


def stored(world: World, character: str, key: str) -> Voice | None:
    world.db.executescript(SCHEMA)
    r = world.db.execute("SELECT pack FROM voice_packs WHERE character = ? AND key = ?", (character, key)).fetchone()
    if not r:
        return None
    try:
        return Voice(json.loads(r["pack"]))
    except (ValueError, TypeError):
        return None


async def make(world: World | None, character: str, persona: dict[str, str] | None,
               companions: list[str] | None = None, chat_fn: llm.ChatFn = llm.chat,
               log: Callable[[dict[str, Any]], None] | None = None) -> Voice:
    """The character's voice: the plain lines without a persona; else its stored pack, or a new one
    written by the cheap model and stored. A failed call gives the plain lines, for this session."""
    log = log or (lambda _: None)
    if not persona:
        return Voice()
    key = key_of(persona, companions)
    if world is not None and (v := stored(world, character, key)) is not None:
        log({"type": "voice", "t": time.time(), "character": character, "stored": True})
        return v
    who = " ".join(f"{k.replace('_', ' ').capitalize()}: {v}." for k, v in persona.items() if k != "name" and v)
    mates = ", ".join(companions or []) or "none named"
    user = f"The character: {character}. {who}\n\nTheir companions: {mates}.\n\nCall voice."
    try:
        with costs.kind("talk"):
            res = await chat_fn([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                                tools=[voice_tool()], tool_choice="voice", model=llm.planner_model(None), max_tokens=2500)
    except llm.LlmError as e:
        log({"type": "voice", "t": time.time(), "character": character, "error": str(e)[:300]})
        return Voice()
    call = next((c for c in res.tool_calls if c.name == "voice"), None)
    args = (call.arguments if call else None) or {}
    if isinstance(args.get("lines"), dict):  # a model that nests them
        args = args["lines"]
    v = Voice({k: args.get(k) for k in KINDS})
    kept = {k: v.lines[k] for k in KINDS if v.lines[k] != KINDS[k][2]}
    log({"type": "voice", "t": time.time(), "character": character, "kinds_written": len(kept), "of": len(KINDS),
         "usage": res.usage.to_log()})
    if world is not None and kept:
        world.db.execute("INSERT OR REPLACE INTO voice_packs (character, key, pack, model, t) VALUES (?, ?, ?, ?, ?)",
                         (character, key, json.dumps(kept), res.usage.model, time.time()))
        world.db.commit()
    return v
