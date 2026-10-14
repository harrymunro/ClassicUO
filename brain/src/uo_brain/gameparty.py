"""UO's own party for a fleet's party (cuo-cym.3).

A party was a party only in the brain's store. To the game its five were strangers: each was told
"Looting this monster corpse will be a criminal act!" at a companion's kill (788 times in the party
runs of 2026-10-10 and 11, by all five), their callouts were overhead speech for everyone on the
screen, and the client's own party list, which keeps a companion who turned criminal out of the
"red or criminal player" threats, was empty.

So the party is made in the game too, as players make one:

- **The leader invites** each companion it can see within INVITE_TILES (the server's reach for the
  invitation's target) who isn't in the game's party yet: one at a time, never in a fight (the
  invitation is a target cursor), and again after INVITE_EVERY_S if nothing came of it (the server
  lets an invitation lapse after 30 s).
- **A member accepts its own leader's invitation and no other.** The leader is known by the serial
  its own brain reports to the party store, not by its name: names aren't unique on a shard.
  Anyone else's invitation is declined, and a member found in somebody else's party leaves it.
- **Callouts can go to party chat** (mates.Mates.to: "say", "party" or "both"): on a real shard a
  party's shouts are for the party, not for the screen. Until the member is in the game's party
  they are said aloud as before.

What the game's party gives on ModernUO: every member may loot the party's kills (a corpse isn't a
crime to open for anyone in the party of someone with a right to it), fame and karma are shared,
the server tells the party of a death, and party chat. Looked at once a few seconds from each
brain; the client does the acts (`act party`, AgentParty.cs).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

from .party import Party
from .rally import contact

INVITE_TILES = 8        # the server's reach for the invitation's target cursor
INVITE_EVERY_S = 35.0   # the server lets an invitation lapse after 30 s
LOOK_EVERY_S = 3.0


class GameParty:
    """One member's side of the game's own party."""

    def __init__(self, rpc: Any, party: Party, log: Callable[[dict[str, Any]], None] | None = None):
        self.rpc, self.party = rpc, party
        self.log = log or (lambda _: None)
        self.leader_serial = 0                # a leader a person plays reports nothing: its companion says who it is
        self.invited: dict[str, float] = {}   # companion -> when last invited
        self.joined = False                   # in the game's party of its own leader
        self.invites = self.accepted = self.declined = self.left = 0

    async def act(self, kind: str, **params: Any) -> dict[str, Any]:
        return await self.rpc.call("act", verb="party", kind=kind, **params)

    def note(self, event: str, **data: Any) -> None:
        self.log({"type": "game_party", "t": time.time(), "event": event, **data})

    async def look(self, snap: dict[str, Any]) -> str | None:
        """One look at the game's party, and at most one act on it; what was done, if anything."""
        if not snap.get("in_game") or snap["player"].get("dead"):
            return None
        game = snap.get("party") or {}
        me = snap["player"].get("serial")
        members = {m["serial"] for m in game.get("members") or []}
        stats = self.party.statuses()
        leader = (stats.get(self.party.leader) or {}).get("serial") or self.leader_serial

        if self.party.leading:
            if game.get("leader") and not game.get("leading"):
                # In somebody else's party: out of it, so its own can be made.
                self.left += 1
                await self.act("leave")
                self.note("left", why="in another's party")
                return "left"
            if (inv := game.get("invited_by")) is not None:
                self.declined += 1
                await self.act("decline", target=inv["serial"])
                self.note("declined", who=inv.get("name") or str(inv["serial"]))
                return "declined"
            self.joined = len(members) > 1
            if contact(snap):
                return None  # the invitation is a target cursor: not while something is on it
            now = time.monotonic()
            for m in snap.get("mobiles") or []:
                name = m.get("name")
                if name not in self.party.companions or m.get("monster") or m.get("dead") or m["serial"] in members \
                        or m["distance"] > INVITE_TILES or now - self.invited.get(name, -1e9) < INVITE_EVERY_S:
                    continue
                res = await self.act("invite", target=m["serial"])
                self.invited[name] = now
                self.invites += 1
                self.note("invited", who=name, **({} if res.get("status") == "done" else {"failed": res.get("detail")}))
                return "invited"  # one invitation a look: each is a target cursor
            return None

        # A member: its own leader's invitation, and nobody else's.
        mine = bool(leader) and game.get("leader") == leader and me in members
        if game.get("leader") and not mine and leader:
            self.left += 1
            self.joined = False
            await self.act("leave")
            self.note("left", why="in another's party")
            return "left"
        if mine and not self.joined:
            self.note("joined", members=len(members))
        self.joined = mine
        if (inv := game.get("invited_by")) is None or not leader:
            return None  # nobody asked; or its leader hasn't reported yet: the invitation keeps for half a minute
        if inv["serial"] == leader:
            self.accepted += 1
            await self.act("accept", target=leader)
            self.note("accepted", who=self.party.leader)
            return "accepted"
        self.declined += 1
        await self.act("decline", target=inv["serial"])
        self.note("declined", who=inv.get("name") or str(inv["serial"]), why="not the party's leader")
        return "declined"

    async def keep(self, snap: Callable[[], Any], stop: asyncio.Event) -> None:
        """Look every LOOK_EVERY_S until stopped."""
        while not stop.is_set():
            try:
                await self.look(await snap())
            except Exception as e:  # noqa: BLE001 - the next look will do
                self.note("error", error=f"{type(e).__name__}: {e}"[:200])
            try:
                await asyncio.wait_for(stop.wait(), LOOK_EVERY_S)
            except asyncio.TimeoutError:
                pass

    def to_log(self) -> dict[str, Any]:
        return {"game_party": {"joined": self.joined, "invited": self.invites, "accepted": self.accepted,
                               "declined": self.declined, "left": self.left}}
