"""A companion hears its leader through walls (cuo-cym.28).

ModernUO sends a character's speech only to those in line of sight of it (Mobile.DoSpeech: regular speech
within 15 tiles, a yell 18, a whisper 1, and for players InLOS), so a companion inside an inn, or on the
other side of a wall on the way out of one, never gets the line its leader said a few tiles off: the line is
in no journal, nobody reads it and nobody answers it, and the person playing the leader can't tell why.

The leader of a party for the player plays in our own client, which has an agent port. A companion's brain
finds that client by its character's name among the clients on this machine (or by `leader_port` in the
fleet file), reads what the leader says there, and when its own client hasn't heard a line GRACE_S after it
was said, though it stood within the line's earshot, passes the line to its own client's journal (the
`hear` RPC), where it is read like any other line of the leader's. Earshot is the server's: a wall is all it
gets past, so a companion 30 tiles off still doesn't hear. `hear_leader = false` in the fleet file: off.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

# How far each kind of line carries on ModernUO (Mobile.DoSpeech, a square of this many tiles each way).
EARSHOT = {"regular": 15, "emote": 15, "yell": 18, "whisper": 1}
GRACE_S = 1.5        # how long its own client has to hear a line before the line is passed on
EVERY_S = 0.5
FIND_EVERY_S = 20.0  # a leader's client not found yet (the person logs in after the fleet starts) is looked for again


@dataclass
class Line:
    text: str
    type: str
    said_at: float
    serial: int
    where: tuple[int, int, int]   # the leader's x, y and map when it was said
    near: tuple[int, int, int]    # this companion's, at the same look


def cuo_ports() -> list[int]:
    """The agent ports of the ClassicUO clients on this machine (TCP listeners of processes named cuo)."""
    r = subprocess.run(["lsof", "-nP", "-a", "-c", "cuo", "-iTCP", "-sTCP:LISTEN"], capture_output=True, text=True)
    return sorted({int(m.group(1)) for m in re.finditer(r":(\d+) \(LISTEN\)", r.stdout)})


async def connect(port: int) -> Any:
    from .rpc import AgentRpc
    rpc = AgentRpc(port=port, reconnect=False)
    await rpc.connect(attempts=1)
    return rpc


def tiles(a: tuple[int, int, int], b: tuple[int, int, int]) -> int | None:
    """Tiles between two places the way the server measures earshot (the larger of the two axes); None on two maps."""
    return max(abs(a[0] - b[0]), abs(a[1] - b[1])) if a[2] == b[2] else None


class Relay:
    """Passes on to one companion's client the leader's lines a wall kept from it."""

    def __init__(self, leader: str, own: Any, port: int | None = None, me_port: int | None = None,
                 log: Callable[[dict[str, Any]], None] | None = None,
                 connect: Callable[[int], Awaitable[Any]] = connect, ports: Callable[[], list[int]] = cuo_ports):
        self.leader = leader
        self.own = own                 # this companion's client (rpc.AgentRpc)
        self.port = port               # the leader's client's agent port, given or found
        self.fixed = port is not None
        self.me_port = me_port         # this companion's own port: never the leader's
        self.log = log or (lambda _: None)
        self._connect, self._ports = connect, ports
        self.rpc: Any = None
        self._looked = -1e9
        self.leader_seq = self.own_seq = -1
        self.pending: list[Line] = []
        self.heard: deque[tuple[float, str]] = deque(maxlen=64)  # the leader's lines its own client got, with when
        self.relayed = self.direct = self.out_of_earshot = 0

    async def find(self) -> None:
        """The leader's client: the one given, or the one on this machine whose character is the leader."""
        for port in [self.port] if self.fixed else [p for p in self._ports() if p != self.me_port]:
            try:
                rpc = await self._connect(port)
                j = await rpc.call("journal", since=1 << 62)
            except Exception:  # noqa: BLE001 - a client starting, or gone: try the next
                continue
            if j.get("player") == self.leader:
                self.rpc, self.port, self.leader_seq = rpc, port, j.get("journal_seq", 0)
                self.log({"type": "relay", "t": time.time(), "event": "found", "leader": self.leader, "port": port})
                return
            await rpc.close()

    async def lost(self, why: str) -> None:
        self.log({"type": "relay", "t": time.time(), "event": "lost", "leader": self.leader, "port": self.port, "why": why})
        if self.rpc is not None:
            await self.rpc.close()
        self.rpc = None
        self.port = self.port if self.fixed else None

    async def step(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if self.rpc is None:
            if now - self._looked < FIND_EVERY_S:
                return
            self._looked = now
            await self.find()
            if self.rpc is None:
                return
        mine = await self.own.call("journal", since=max(self.own_seq, 0))
        near = (mine.get("x", 0), mine.get("y", 0), mine.get("map", -1))
        if self.own_seq < 0 or mine.get("journal_seq", 0) < self.own_seq:
            self.own_seq = mine.get("journal_seq", 0)  # the first look, or its client restarted
        else:
            for e in mine.get("journal") or []:
                if e.get("name") == self.leader and e.get("source") == "speech" and not e.get("relayed"):
                    self.heard.append((now, str(e.get("text") or "")))
            self.own_seq = mine.get("journal_seq", self.own_seq)
        try:
            theirs = await self.rpc.call("journal", since=max(self.leader_seq, 0))
        except Exception as e:  # noqa: BLE001 - the leader's client went
            await self.lost(str(e) or type(e).__name__)
            return
        if theirs.get("player") != self.leader:
            await self.lost(f"its client plays {theirs.get('player')!r}")
            return
        where = (theirs.get("x", 0), theirs.get("y", 0), theirs.get("map", -1))
        if theirs.get("journal_seq", 0) < self.leader_seq:
            self.leader_seq = theirs.get("journal_seq", 0)  # the leader's client restarted
        else:
            for e in theirs.get("journal") or []:
                if e.get("seq", 0) > self.leader_seq and e.get("source") == "self" and e.get("type") in EARSHOT \
                        and (text := str(e.get("text") or "").strip()):
                    self.pending.append(Line(text, e["type"], now, int(theirs.get("serial") or 0), where, near))
            self.leader_seq = theirs.get("journal_seq", self.leader_seq)
        await self.settle(now)

    async def settle(self, now: float) -> None:
        """Lines whose grace is over: heard by its own client already, too far to have been heard, or passed on."""
        due = [ln for ln in self.pending if now - ln.said_at >= GRACE_S]
        self.pending = [ln for ln in self.pending if now - ln.said_at < GRACE_S]
        for ln in due:
            if any(text == ln.text and at >= ln.said_at - GRACE_S for at, text in self.heard):
                self.direct += 1
                continue
            d = tiles(ln.where, ln.near)
            if d is None or d > EARSHOT[ln.type]:
                self.out_of_earshot += 1
                continue
            await self.own.call("hear", name=self.leader, serial=ln.serial, text=ln.text, type=ln.type)
            self.relayed += 1
            self.log({"type": "relay", "t": time.time(), "event": "relayed", "line": ln.text, "said_as": ln.type, "tiles": d,
                      "after_s": round(now - ln.said_at, 1)})

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.step()
            except Exception as e:  # noqa: BLE001 - its own client between lives: the next look tries again
                self.log({"type": "relay", "t": time.time(), "event": "error", "error": str(e) or type(e).__name__})
            try:
                await asyncio.wait_for(stop.wait(), EVERY_S)
            except TimeoutError:
                pass
        if self.rpc is not None:
            await self.rpc.close()

    def report(self) -> dict[str, Any]:
        return {"leader_port": self.port, "relayed": self.relayed, "heard_directly": self.direct,
                "out_of_earshot": self.out_of_earshot}
