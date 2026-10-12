"""What a character does, besides speak, that shows who it is (cuo-cod.8).

Talk, callouts, walking together and the inn are the big parts of a party's roleplay; this is the
rest: a bow on meeting, a salute to the leader, and small things done in character, shown as
emotes ("*checks his bowstring*"): on setting out, while resting, after a fight, over a fallen
companion. The emotes come from the character's voice pack (voice.py), written once in its
persona, and code picks one on an event, so nothing here costs a model call.

They are kept sparse, so they read as character and not as noise: each event has a chance of
being acted on, and one character does at most one such thing every MIN_GAP_S. Never in a fight:
callers only raise events in quiet moments. On by a fleet file's `roleplay = true` (off in the
baseline fleets). Local server only, as all talk.
"""

import random
import time
from collections.abc import Callable
from typing import Any

MIN_GAP_S = 75.0   # between two things one character does
# event -> (the voice pack's emote, or a gesture: bow, salute), and the chance it is acted on.
EVENTS: dict[str, tuple[str, float]] = {
    "meet": ("bow", 1.0),           # the party comes together (a follower salutes its leader instead)
    "set_out": ("e_set_out", 0.5),  # a long trip begins
    "rest": ("e_rest", 0.5),
    "kill": ("e_kill", 0.4),        # a fight is over
    "fallen": ("e_fallen", 1.0),    # beside a companion's body, once the fight is over
    "vendor": ("bow", 0.4),         # a bow to a shopkeeper or banker
}
GESTURES = ("bow", "salute")


class Roleplay:
    def __init__(self, rpc: Any, voice: Any = None, floor: Any = None,
                 log: Callable[[dict[str, Any]], None] | None = None, follower: bool = False,
                 rnd: random.Random | None = None, min_gap_s: float = MIN_GAP_S):
        self.rpc, self.voice, self.floor = rpc, voice, floor
        self.log = log or (lambda _: None)
        self.follower = follower
        self.rnd = rnd or random.Random()
        self.min_gap_s = min_gap_s
        self.last = -1e9
        self.done: dict[str, int] = {}
        self.once: set[str] = set()

    async def act(self, event: str, name: str = "", key: str = "") -> str | None:
        """Maybe do something for this event; what was done ("bow", or the emote's words), or None.
        `key` makes it once only (a fallen companion is knelt by once)."""
        what, chance = EVENTS.get(event, ("", 0.0))
        now = time.monotonic()
        if not what or (key and key in self.once) or now - self.last < self.min_gap_s or self.rnd.random() >= chance:
            return None
        if event == "meet" and self.follower:
            what = "salute"
        try:
            if what in GESTURES:
                res = await self.rpc.call("act", verb=what)
                if isinstance(res, dict) and res.get("status") not in (None, "done"):
                    return None  # a client that doesn't know the gesture
                did = what
            else:
                line = self.voice.pick(what, name=name) if self.voice is not None else ""
                if not line:
                    return None
                await self.rpc.call("act", verb="say", text=f"*{line}*", kind="emote")
                if self.floor is not None:
                    self.floor.say(f"*{line}*", kind="emote")
                did = line
        except Exception as e:  # noqa: BLE001 - never worth a goal
            self.log({"type": "roleplay", "t": time.time(), "event": event, "error": f"{type(e).__name__}: {e}"[:200]})
            return None
        self.last = now
        if key:
            self.once.add(key)
        self.done[event] = self.done.get(event, 0) + 1
        self.log({"type": "roleplay", "t": time.time(), "event": event, "did": did, **({"name": name} if name else {})})
        return did

    def to_log(self) -> dict[str, Any]:
        return {"roleplay_acts": dict(self.done)}
