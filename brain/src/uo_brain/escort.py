"""What the leader is fighting, judged per creature (cuo-cym.25).

A companion of a character a person plays (companion.py) fights what attacks it and what stands in war mode within
4 tiles of the leader or 6 of itself (policy's escort rule): a client isn't told whom another character attacks. A
creature the leader shoots or casts at from range isn't the companions' until it closes or turns on one of them.

This puts each creature in view to system one instead, with what the companion's own client can see of it and of the
leader: how far it is from each, whether it is in war mode, which way it faces and whether that is towards the leader,
whether it has come nearer the leader over the last seconds, whether it is wounded with no companion on it, and how
the leader stands (war mode, which way it faces) and what it said lately. One request, a yes/no per creature: is it
part of the party's fight? The rule is kept as the floor: what attacks the companion, or stands in war mode within
the rule's tiles, is fought whatever the answer.
"""

from __future__ import annotations

import math
import time
from collections import deque
from typing import Any

THEIRS_CUT = 0.5      # a creature is the party's fight when the yes/no on it gives this
CREATURES_MAX = 6
TRAIL_S = 4.0         # how far back "coming nearer the leader" looks
NEARER = 2            # ...this many tiles nearer the leader in that time
RULE_LEADER, RULE_OWN = 4, 6  # policy's escort_tiles and escort_own: the rule, as it stands
FACINGS = {"east": 0, "northeast": 45, "north": 90, "northwest": 135, "west": 180, "southwest": 225, "south": 270,
           "southeast": 315}


def angle(dx: int, dy: int) -> float:
    """The bearing of an offset in map space (y grows southward), in degrees, east 0 and north 90."""
    return math.degrees(math.atan2(-dy, dx)) % 360


def faces(facing: str | None, dx: int, dy: int) -> bool:
    """Whether one facing `facing` faces a point (dx, dy) away: within 45 degrees of its bearing."""
    if facing not in FACINGS or (dx, dy) == (0, 0):
        return False
    d = abs(FACINGS[facing] - angle(dx, dy)) % 360
    return min(d, 360 - d) <= 45


def tiles(dx: int, dy: int) -> int:
    return max(abs(dx), abs(dy))


class Trail:
    """Each creature's distance to the leader over the last TRAIL_S, to tell one coming at the party."""

    def __init__(self) -> None:
        self.seen: dict[int, deque[tuple[float, int]]] = {}

    def note(self, serial: int, to_leader: int, now: float | None = None) -> int:
        """How many tiles nearer the leader it has come over the last TRAIL_S (negative: further)."""
        now = time.monotonic() if now is None else now
        q = self.seen.setdefault(serial, deque(maxlen=20))
        q.append((now, to_leader))
        while q and now - q[0][0] > TRAIL_S:
            q.popleft()
        return q[0][1] - to_leader


def scene(snap: dict[str, Any], me: str, leader: str, companions: list[str], trail: Trail | None = None,
          said: list[str] | None = None, limit: int = CREATURES_MAX) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The leader and the creatures in view, as facts for the question: (leader, creatures). Each creature with an id
    (c1...), its serial, and `to_leader`, `to_me`, `war_mode`, `attacking_me` for the rule."""
    mobiles = snap.get("mobiles") or []
    lead = next((m for m in mobiles if m.get("name") == leader and not m.get("monster") and not m.get("dead")), None)
    mates = {m["name"]: m for m in mobiles if m.get("name") in companions and m.get("name") != me and not m.get("monster")}
    lx, ly = (lead.get("dx", 0), lead.get("dy", 0)) if lead else (None, None)
    who = {"name": leader, "seen": lead is not None}
    if lead:
        who |= {"tiles_from_you": lead.get("distance", tiles(lx, ly)), "war_mode": bool(lead.get("war_mode")),
                "facing": lead.get("facing"), "health": f"{lead.get('hits_pct', 100)}%"}
    if said:
        who["said_lately"] = said[-3:]
    found = [m for m in mobiles if m.get("monster") and not m.get("dead") and not m.get("pet") and not m.get("summon")]
    found.sort(key=lambda m: m.get("distance", 99))
    out = []
    for i, m in enumerate(found[:limit]):
        dx, dy = m.get("dx", 0), m.get("dy", 0)
        c: dict[str, Any] = {"id": f"c{i + 1}", "serial": m["serial"], "name": m.get("name") or "a creature",
                             "to_me": m.get("distance", tiles(dx, dy)), "war_mode": bool(m.get("war_mode")),
                             "attacking_me": bool(m.get("attacking_me")), "facing": m.get("facing")}
        if lead:
            c["to_leader"] = tiles(dx - lx, dy - ly)
            c["faces_leader"] = faces(m.get("facing"), lx - dx, ly - dy)
            c["leader_faces_it"] = faces(lead.get("facing"), dx - lx, dy - ly)
            if trail is not None:
                c["nearer_leader"] = trail.note(m["serial"], c["to_leader"])
        on = [n for n, o in mates.items() if tiles(dx - o.get("dx", 0), dy - o.get("dy", 0)) <= 1]
        c["companions_on_it"] = on
        if m.get("hits_pct") is not None and m["hits_pct"] < 100:
            c["health"] = f"{m['hits_pct']}%"
        out.append(c)
    return who, out


def words(c: dict[str, Any], me: str, leader: str) -> str:
    """One creature in words, for the question's state."""
    name = c["name"]
    parts = [f"{name[:1].upper()}{name[1:]}: {c['to_me']} tiles from {me}"]
    if "to_leader" in c:
        parts.append(f"{c['to_leader']} from {leader}")
    parts.append("in war mode" if c["war_mode"] else "not in war mode")
    if c.get("attacking_me"):
        parts.append(f"attacking {me}")
    if "faces_leader" in c:
        parts.append(f"facing {leader}" if c["faces_leader"] else f"facing {c.get('facing') or 'away'}, not {leader}")
    if c.get("leader_faces_it"):
        parts.append(f"{leader} faces it")
    if (n := c.get("nearer_leader")) is not None and n >= NEARER:
        parts.append(f"{n} tiles nearer {leader} than a few seconds ago")
    elif n is not None and n <= -NEARER:
        parts.append(f"{-n} tiles further from {leader} than a few seconds ago")
    if c.get("health"):
        parts.append(f"wounded ({c['health']})" + ("" if c.get("companions_on_it") else ", and no companion is on it"))
    if c.get("companions_on_it"):
        parts.append(f"{', '.join(c['companions_on_it'])} on it")
    return "; ".join(parts) + "."


def state(who: dict[str, Any], creatures: list[dict[str, Any]], me: str, leader: str) -> dict[str, Any]:
    lead = (f"{leader}: {who['tiles_from_you']} tiles from {me}, " + ("in war mode" if who["war_mode"] else "not in war mode")
            + (f", facing {who['facing']}" if who.get("facing") else "") + f", health {who['health']}") \
        if who.get("seen") else f"{leader} is out of view"
    return {"companion": me, "leader": lead, **({"leader_said_lately": who["said_lately"]} if who.get("said_lately") else {}),
            "creatures": {c["id"]: words(c, me, leader) for c in creatures}}


def theirs_questions(me: str, leader: str, creatures: list[dict[str, Any]]) -> dict[str, Any]:
    """A yes/no per creature: is it part of the party's fight, for this companion to take on?"""
    role = (f"{leader} leads a party of adventurers in the game Ultima Online and is played by a person. {me} is one of "
            f"the companions who fight beside {leader}: they take on what {leader} is fighting and whatever comes at the "
            f"party, and leave alone what is no business of the party's. A client isn't told whom another character "
            f"attacks: `creatures` says what can be seen of each creature in view, `leader` how {leader} stands, and "
            f"`leader_said_lately` what {leader} said in the last moments. {leader} may fight from range (a bow, a "
            f"spell), so a creature far from {leader} can still be {leader}'s fight: facing {leader}, coming at "
            f"{leader}, wounded with no companion on it, or named by {leader}.")
    return {c["id"]: {"type": "noul", "instructions": {"role": role, "question": f"Is {c['id']} ({c['name']}) part of "
                                                                                 f"the party's fight, for {me} to take on now?"},
                      "criteria": {"true": f"{leader} is fighting it, or it is coming at {leader} or a companion: in war "
                                           f"mode and facing {leader} or coming nearer, wounded with no companion on it "
                                           f"while {leader} faces it in war mode, or named by {leader} as one to fight.",
                                   "false": f"It isn't in the party's fight yet: not in war mode, or in war mode but facing "
                                            f"away and not coming nearer, or {leader} isn't fighting and nothing of it "
                                            f"comes at the party; or {leader} said to leave it alone."}}
            for c in creatures}


def rule(c: dict[str, Any], leader_tiles: int = RULE_LEADER, own_tiles: int = RULE_OWN) -> bool:
    """policy's escort rule on a creature: attacking this companion, or in war mode within own_tiles of it or
    leader_tiles of the leader."""
    return bool(c.get("attacking_me")) or bool(c.get("war_mode")) and (
        c.get("to_me", 99) <= own_tiles or c.get("to_leader", 99) <= leader_tiles)
