"""Party play (cuo-4ar): several characters on one plan.

Filming a party of five (a warrior, a mage, an archer, a tamer and a bard) with the same goal and
a planner each, the mage went shopping twice and the tamer back to town after dying, so three met
a lich and two died. With one `do hunt` each and no planner, all five reached the lich, each picked
it by itself, and it died in 12 s with nobody lost. The fight loop already works side by side; the
plan didn't.

So a party has one plan. Its leader runs the planner (planner.py), which sees every member's state
and posts each goal it chooses here. The others follow (Follower): they travel, hunt and rest where
the leader does, fight beside it with Jev (who is told who its companions are, state.py), and buy
their own supplies when the leader calls a restock (or banks or buys). A member that dies is
resurrected and goes back for its corpse on its own, then rejoins; between goals they keep within
FOLLOW_TILES of the leader.

The goals and every member's state go in the shard's world store, which a fleet's brains share
(each its own process). A fleet file says `party = true` and `leader = "NAME"` (fleet.py); each run
has its own party id, so a run never follows an earlier one's goals.

In a fight each member also keeps a quick row of its own (party_fight: health, what is attacking it,
its pets) and writes its callouts with their meaning (party_calls: hurt, what it is on, what is on
it, out of supplies, a companion down), so the others' fight state can say what is on whom and Jev's
choices can answer it (mates.py: cuo-cod.2, cuo-4ti).

While the leader is down (dead, or seeing to its own resurrection and corpse) the others don't wait:
they carry on the hunt it last set, beside each other, in short spells (cuo-e1p).

Before a hunt the party forms up: the leader stops at the edge and gives the word once the others are
beside it, and the place is where they fall back to (party_rally; rally.py, cuo-cym.1).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from collections.abc import Callable
from typing import Any

from .world import World, tiles

SCHEMA = """
CREATE TABLE IF NOT EXISTS party_goals (
    id INTEGER PRIMARY KEY, party TEXT NOT NULL, rev INTEGER NOT NULL, by TEXT NOT NULL, tool TEXT NOT NULL,
    args TEXT, why TEXT, t REAL NOT NULL);
CREATE INDEX IF NOT EXISTS party_goals_party ON party_goals(party, rev);
CREATE TABLE IF NOT EXISTS party_members (
    party TEXT NOT NULL, name TEXT NOT NULL, status TEXT NOT NULL, t REAL NOT NULL, PRIMARY KEY (party, name));
CREATE TABLE IF NOT EXISTS party_fight (
    party TEXT NOT NULL, name TEXT NOT NULL, status TEXT NOT NULL, t REAL NOT NULL, PRIMARY KEY (party, name));
CREATE TABLE IF NOT EXISTS party_calls (
    id INTEGER PRIMARY KEY, party TEXT NOT NULL, who TEXT NOT NULL, kind TEXT NOT NULL, target INTEGER,
    about TEXT, hits INTEGER, hits_max INTEGER, said TEXT, t REAL NOT NULL);
CREATE INDEX IF NOT EXISTS party_calls_party ON party_calls(party, t);
CREATE TABLE IF NOT EXISTS party_rally (
    party TEXT NOT NULL, key TEXT NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL, point TEXT, state TEXT NOT NULL,
    sighted INTEGER NOT NULL DEFAULT 0, t REAL NOT NULL, go_t REAL, host TEXT, back_x INTEGER, back_y INTEGER,
    PRIMARY KEY (party, key));
"""

FOLLOW_TILES = 10    # a follower further than this from the leader between goals walks back to it
FOLLOW_TILES_WALKING = 5  # ...in a party that walks its trips: nearer than the leader's wait (Session.LAG_TILES),
#                           or it rests at 9 tiles while the leader waits 20 s for it, again and again
REPORT_EVERY_S = 5.0
STALE_S = 60.0       # a member's state older than this is said to be old
NEVER_JOINED_S = 120.0  # a member not heard from this long after the party set out hasn't joined
# The leader's goals a follower carries out as its own; the leader's errands (bank, buy, sell) and
# restock are a restock for each; anything else (its own resurrection, a strategy) is waited out.
FOLLOWED = ("travel_to", "hunt", "rest", "inn")
RESTOCK = ("restock", "bank", "buy", "sell")
FIGHT_STALE_S = 6.0  # a member's fight row older than this says nothing about now
CARRY_ON_MIN = 3.0   # minutes of hunting at a time while the leader is down (cuo-e1p)
CARRY_ON_TILES = 30  # ...by those this near the hunt, when the leader isn't dead but away for its corpse
TABLE_WAIT_S = 180.0  # a follower at the inn waits this long for the table: longer than the leader's gathering


def ensure(world: World) -> None:
    world.db.executescript(SCHEMA)
    # A store from before the huddle's rally (host), or the second point behind the rally point (cuo-cym.13).
    for column, kind in (("host", "TEXT"), ("back_x", "INTEGER"), ("back_y", "INTEGER")):
        add_column(world, "party_rally", column, kind)


def add_column(world: World, table: str, column: str, kind: str) -> None:
    """A column an older store's table hasn't got. A party's brains start together, each its own process: one
    of five adding the same column a moment after another was refused it ("duplicate column name"), and that
    brain's session ended there."""
    if column in {r["name"] for r in world.db.execute(f"PRAGMA table_info({table})")}:
        return
    try:
        world.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
        world.db.commit()
    except sqlite3.OperationalError as e:
        if "duplicate column" not in str(e):  # anything else (a store locked for too long) leaves it missing
            raise


class Party:
    """One run's party, from one member's side."""

    def __init__(self, world: World, party: str, me: str, leader: str, members: list[str]):
        ensure(world)
        self.world, self.party, self.me, self.leader = world, party, me, leader
        self.members = [m for m in members if m]
        # Who the others are, by what each plays as once it has said (its first report): the fight
        # loop's state names them as companions (PolicyConfig.companions, the same dict).
        self.companions: dict[str, str] = {m: "companion" for m in self.members if m != me}
        self.started = time.time()
        self._fight_sent: tuple[str, float] = ("", 0.0)  # the last fight row written, and when
        self.inn = False         # the leader has the inn goal (a table to sit at: cuo-cod.3)
        self.deaths = 0          # this member's own deaths, seen as it reports
        self._dead = False
        self._died_at = 0.0
        self.on_death: Callable[[], None] | None = None

    @property
    def leading(self) -> bool:
        return self.me == self.leader

    # ---- the plan ----------------------------------------------------------------

    def post(self, tool: str, args: dict[str, Any], why: str = "") -> int:
        """The leader's goal for the party; its revision."""
        rev = (self.world.db.execute("SELECT MAX(rev) FROM party_goals WHERE party = ?", (self.party,)).fetchone()[0]
               or 0) + 1
        self.world.db.execute("INSERT INTO party_goals (party, rev, by, tool, args, why, t) VALUES (?, ?, ?, ?, ?, ?, ?)",
                              (self.party, rev, self.me, tool, json.dumps(args), why, time.time()))
        self.world.db.commit()
        return rev

    def latest(self) -> dict[str, Any] | None:
        r = self.world.db.execute("SELECT * FROM party_goals WHERE party = ? ORDER BY rev DESC LIMIT 1",
                                  (self.party,)).fetchone()
        if r is None:
            return None
        return {"rev": r["rev"], "by": r["by"], "tool": r["tool"], "args": json.loads(r["args"] or "{}"),
                "why": r["why"] or "", "t": r["t"]}

    # ---- the members' state ------------------------------------------------------

    def report(self, snap: dict[str, Any], plays_as: str, doing: str = "", left: dict[str, Any] | None = None,
               busy: bool = False, hunting: str = "") -> None:
        """This member's state, for the leader's planner and the followers' "stay with the leader". left: where
        it last left creatures behind (Session.last_leave), so the others know a place wasn't won. busy: on
        business of its own (its resurrection, its corpse, a restock), so a party forming up doesn't wait for it.
        hunting: the area of the hunt it is on or on its way to, which is whom a huddle's rally waits for."""
        if not snap.get("in_game"):
            return
        p = snap["player"]
        s = p.get("supplies") or {}
        dead = bool(p.get("dead"))
        if dead and not self._dead:
            self.deaths += 1
            self._died_at = time.time()
            if self.on_death:
                self.on_death()
        self._dead = dead
        status = {"plays_as": plays_as, "serial": p.get("serial") or 0, "x": p["x"], "y": p["y"],
                  "hits": p.get("hits"), "hits_max": p.get("hits_max"),
                  "dead": bool(p.get("dead")), "supplies": supplies_words(plays_as, s), "doing": doing[:120],
                  **({"busy": True} if busy else {}), **({"hunting": hunting} if hunting else {}),
                  **({"raises": how} if (how := raises_with(snap)) and not dead else {}),
                  **({"died_at": self._died_at} if dead else {}),
                  **({"last_death": self._died_at} if self._died_at else {}),
                  **({"left": {**left, "t": time.time() - left.get("ago_s", 0)}} if left else {})}
        self.world.db.execute("INSERT OR REPLACE INTO party_members (party, name, status, t) VALUES (?, ?, ?, ?)",
                              (self.party, self.me, json.dumps(status), time.time()))
        self.world.db.commit()

    def setting_out(self, area: str) -> None:
        """Say at once that this member is on its way to a hunt, without waiting for its next report: a huddle's
        rally waits for those whose reports say so (cuo-cym.5), and with a report every REPORT_EVERY_S the first
        to reach the edge saw nobody on the way and went in alone (live, 2026-10-13). Only that one word of its
        last report changes: where it stood then is still where it is said to be."""
        row = self.world.db.execute("SELECT status FROM party_members WHERE party = ? AND name = ?",
                                    (self.party, self.me)).fetchone()
        if row is None:
            return
        status = json.loads(row["status"])
        if status.get("hunting") == area:
            return
        status["hunting"] = area
        self.world.db.execute("UPDATE party_members SET status = ? WHERE party = ? AND name = ?",
                              (json.dumps(status), self.party, self.me))
        self.world.db.commit()

    def statuses(self) -> dict[str, dict[str, Any]]:
        out = {}
        for r in self.world.db.execute("SELECT * FROM party_members WHERE party = ?", (self.party,)):
            out[r["name"]] = {**json.loads(r["status"]), "age_s": time.time() - r["t"]}
            if r["name"] != self.me and out[r["name"]].get("plays_as"):
                self.companions[r["name"]] = out[r["name"]]["plays_as"]
        return out

    # ---- in a fight ----------------------------------------------------------------

    def fight(self, status: dict[str, Any], every_s: float = 2.0) -> bool:
        """This member's fight row: written when it changes, or every `every_s` so it doesn't go stale."""
        body = json.dumps(status, sort_keys=True)
        now = time.time()
        if body == self._fight_sent[0] and now - self._fight_sent[1] < every_s:
            return False
        self._fight_sent = (body, now)
        self.world.db.execute("INSERT OR REPLACE INTO party_fight (party, name, status, t) VALUES (?, ?, ?, ?)",
                              (self.party, self.me, body, now))
        self.world.db.commit()
        return True

    def fights(self, max_age_s: float = FIGHT_STALE_S) -> dict[str, dict[str, Any]]:
        """The others' fight rows, fresh ones only."""
        now = time.time()
        return {r["name"]: json.loads(r["status"]) for r in self.world.db.execute(
            "SELECT name, status, t FROM party_fight WHERE party = ? AND name != ? AND t >= ?",
            (self.party, self.me, now - max_age_s))}

    def call(self, kind: str, target: int = 0, about: str = "", hits: int | None = None, hits_max: int | None = None,
             said: str = "") -> int:
        """A callout's meaning, for the others' brains (the speech is for the players watching)."""
        cur = self.world.db.execute(
            "INSERT INTO party_calls (party, who, kind, target, about, hits, hits_max, said, t) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.party, self.me, kind, target or None, about or None, hits, hits_max, said or None, time.time()))
        self.world.db.commit()
        return cur.lastrowid

    def calls(self, within_s: float = 15.0, mine: bool = False, kind: str | None = None) -> list[dict[str, Any]]:
        """Callouts of the last `within_s` seconds, oldest first: the others', or everyone's with `mine`."""
        sql = "SELECT * FROM party_calls WHERE party = ? AND t >= ?"
        args: list[Any] = [self.party, time.time() - within_s]
        if not mine:
            sql += " AND who != ?"
            args.append(self.me)
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        return [{"id": r["id"], "who": r["who"], "kind": r["kind"], "target": r["target"] or 0, "about": r["about"] or "",
                 "hits": r["hits"], "hits_max": r["hits_max"], "said": r["said"] or "", "t": r["t"]}
                for r in self.world.db.execute(sql + " ORDER BY t", args)]

    # ---- the rally point (rally.py) -------------------------------------------------

    def rally_open(self, key: str, x: int, y: int, point: str = "", sighted: bool = False,
                   back: tuple[int, int] | None = None) -> None:
        """The party forms up here for the goal `key`: the others close up on whoever opened it. back: a second
        point further from the hunt, for one badly hurt at this one (cuo-cym.13)."""
        self.world.db.execute(
            "INSERT OR REPLACE INTO party_rally (party, key, x, y, point, state, sighted, t, go_t, host, back_x, back_y) "
            "VALUES (?, ?, ?, ?, ?, 'gathering', ?, ?, NULL, ?, ?, ?)",
            (self.party, key, x, y, point or None, int(sighted), time.time(), self.me, *(back or (None, None))))
        self.world.db.commit()

    def rally_claim(self, key: str, x: int, y: int, sighted: bool = False, stale_s: float = 20.0,
                    back: tuple[int, int] | None = None) -> dict[str, Any]:
        """A rally with no leader to open it (a party that plans by huddle): the first to arrive holds it here,
        and the others close up on that one. The row either way. One that went more than `stale_s` ago, or that
        nobody gave the word for in two minutes, is over: the next to arrive holds a new one."""
        now = time.time()
        made = self.world.db.execute(
            "INSERT OR IGNORE INTO party_rally (party, key, x, y, host, state, sighted, t, back_x, back_y) VALUES (?, ?, "
            "?, ?, ?, 'gathering', ?, ?, ?, ?)",
            (self.party, key, x, y, self.me, int(sighted), now, *(back or (None, None)))).rowcount
        if not made:
            self.world.db.execute(
                "UPDATE party_rally SET x = ?, y = ?, host = ?, point = NULL, state = 'gathering', sighted = ?, t = ?, "
                "go_t = NULL, back_x = ?, back_y = ? WHERE party = ? AND key = ? AND ((state != 'gathering' AND "
                "COALESCE(go_t, t) < ?) OR (state = 'gathering' AND t < ?))",
                (x, y, self.me, int(sighted), now, *(back or (None, None)), self.party, key, now - stale_s, now - 120.0))
        self.world.db.commit()
        return self.rally(key)  # type: ignore[return-value]

    def rally_go(self, key: str, state: str = "go") -> None:
        """The word to go in ("go"), or that there is nothing to form up for after all ("off")."""
        self.world.db.execute("UPDATE party_rally SET state = ?, go_t = ? WHERE party = ? AND key = ?",
                              (state, time.time(), self.party, key))
        if state == "off":
            self.world.db.execute(
                "INSERT OR IGNORE INTO party_rally (party, key, x, y, state, t, go_t) VALUES (?, ?, 0, 0, 'off', ?, ?)",
                (self.party, key, time.time(), time.time()))
        self.world.db.commit()

    def rally(self, key: str | None = None) -> dict[str, Any] | None:
        """The rally for a goal, or with no key the last one the party went in from."""
        if key is None:
            r = self.world.db.execute("SELECT * FROM party_rally WHERE party = ? AND state = 'go' ORDER BY go_t DESC "
                                      "LIMIT 1", (self.party,)).fetchone()
        else:
            r = self.world.db.execute("SELECT * FROM party_rally WHERE party = ? AND key = ?", (self.party, key)).fetchone()
        if r is None:
            return None
        return {"key": r["key"], "x": r["x"], "y": r["y"], "point": r["point"] or "", "state": r["state"],
                "sighted": bool(r["sighted"]), "t": r["t"], "go_t": r["go_t"], "host": r["host"] or "",
                "back": (r["back_x"], r["back_y"]) if r["back_x"] is not None and r["back_y"] is not None else None}

    def section(self, me_at: tuple[int, int] | None = None, leading: bool = True) -> str:
        """The party, for a planner: for its leader, who follows it and how each is; for a member of a
        party that plans by huddle (leading=False), how the others are."""
        stats = self.statuses()
        lines = []
        for name in self.members:
            if name == self.me:
                continue
            st = stats.get(name)
            if st is None:
                # One whose client never logged in: the leader waited the whole run for "Corwin, not heard from".
                gone = time.time() - self.started
                lines.append(f"- {name}: hasn't joined: no word in the {gone / 60:.0f} min since the party set out; "
                             "go on without them" if gone > NEVER_JOINED_S else f"- {name}: not heard from yet")
                continue
            where = f"{tiles(st['x'], st['y'], *me_at)} tiles from you" if me_at else f"at {st['x']},{st['y']}"
            health = "dead: a ghost, resurrecting" if st["dead"] else f"{st['hits']}/{st['hits_max']} health"
            old = f" (as of {st['age_s']:.0f} s ago)" if st["age_s"] > STALE_S else ""
            doing = f", {st['doing']}" if st.get("doing") else ""
            lines.append(f"- {name} ({st['plays_as']}): {health}, {st['supplies']}, {where}{doing}{old}")
        if not leading:
            return ("Your party, as each last reported (each of you plans for yourself; the plan you agreed is "
                    "below):\n" + "\n".join(lines))
        return ("Your party. You lead it, and the others follow every goal you set: they travel, hunt and rest with "
                "you and fight beside you. Each buys its own supplies when you call restock (or bank or buy). Plan "
                "for all of them: hunt only where the party can win, call restock when anyone runs low, and wait "
                "(rest) for anyone dead or far behind." + (" After a hunt, take them to the inn to talk it over: it is how "
                "the party learns from a fight. Call the inn goal for that, never travel_to: inn walks you all "
                "there and holds the talk round the table." if self.inn else "")
                + "\n" + "\n".join(lines))

    async def keep_reporting(self, snap: Callable[[], Any], plays_as: Callable[[dict[str, Any]], str],
                             doing: Callable[[], str], stop: asyncio.Event,
                             left: Callable[[], dict[str, Any] | None] | None = None,
                             busy: Callable[[], bool] | None = None,
                             hunting: Callable[[], str] | None = None) -> None:
        """Report this member's state every REPORT_EVERY_S until stopped."""
        while not stop.is_set():
            try:
                s = await snap()
                self.report(s, plays_as(s) if s.get("in_game") else "", doing(), left() if left else None,
                            busy() if busy else False, hunting() if hunting else "")
            except Exception:  # noqa: BLE001 - the next report will do
                pass
            try:
                await asyncio.wait_for(stop.wait(), REPORT_EVERY_S)
            except asyncio.TimeoutError:
                pass


RAISE_SKILL = 80.0  # ModernUO: a bandage raises the dead only with Healing and Anatomy both at this


def raises_with(snap: dict[str, Any]) -> str:
    """How this character could raise a fallen companion right now (cuo-cym.4): "bandages" (Healing and
    Anatomy at RAISE_SKILL, and a bandage), "spell" (Resurrection in its book, with the mana and reagents for
    it), or "" for not at all."""
    p = snap.get("player") or {}
    skills = p.get("skills") or {}
    if skills.get("Healing", 0) >= RAISE_SKILL and skills.get("Anatomy", 0) >= RAISE_SKILL \
            and (p.get("supplies") or {}).get("bandages", 0) > 0:
        return "bandages"
    # In its book with the reagents for it. Short of mana for the moment still counts: just after a fight a mage
    # often is, and a ghost whose only raiser she was walked off to the healer six seconds later.
    if any(sp.get("name") == "Resurrection" and sp.get("missing") in (None, "", "mana")
           for sp in (snap.get("magic") or {}).get("spells") or []):
        return "spell"
    return ""


def number(value: Any, default: float) -> float:
    """A goal's number as the leader's planner sent it: `null` and "ten" give the default."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def supplies_words(plays_as: str, s: dict[str, Any]) -> str:
    """The supplies that matter for what a member plays as, short."""
    regs = s.get("reagents") or {}
    pagan = s.get("pagan_reagents") or {}
    parts = []
    if plays_as != "mage":
        parts.append(f"{s.get('bandages', 0)} bandages")
    if plays_as in ("mage", "mage-tamer", "warrior-mage"):
        parts.append("reagents not counted yet" if not any(regs.values()) and s.get("unread_bags")
                     else f"at least {min(regs.values(), default=0)} of each reagent")
    if plays_as == "necromancer":
        parts.append(f"at least {min(pagan.values(), default=0)} of each necromancy reagent")
    if plays_as == "archer":
        parts.append(f"{s.get('arrows', 0)} arrows, {s.get('bolts', 0)} bolts")
    return ", ".join(parts) or "nothing to count"


class Follower:
    """A party member that isn't the leader: it carries out the leader's goals as its own."""

    def __init__(self, session: Any, party: Party, log: Callable[[dict[str, Any]], None] | None = None,
                 table: Any = None):
        self.session, self.party = session, party
        self.table = table  # table.Table: its place at the party's table at the inn (cuo-cod.4)
        self.log = log or (lambda _: None)
        self.rev = 0
        self.doing = "waiting for the leader's first goal"
        self.goals = 0
        self.finished = False
        self.ends_at: float | None = None  # when its session's hours are up (run): no hunt outlasts it
        # Its own business, not cut short by the leader's goals: being resurrected and taking its corpse
        # back. In the first party run the leader's goals cut a ghost's walk to the healer short every 2 s,
        # and it took 2.5 minutes to come back.
        self.own = False
        self.last_hunt: dict[str, Any] | None = None  # the leader's last hunt, carried on while it is down
        self.hunting = False
        self.carried_on = 0

    async def watch(self, stop: asyncio.Event) -> None:
        """Cut the goal going on short when the leader posts a new one. Not a hunt for the leader's own
        business (its resurrection, its corpse, a strategy): the others fight on where they are (cuo-e1p)."""
        while not stop.is_set():
            try:
                g = self.party.latest()
                if g is not None and g["rev"] > self.rev and not self.own and not self.session.interrupt.is_set() \
                        and not (self.hunting and g["tool"] not in (*FOLLOWED, *RESTOCK, "finish")):
                    self.session.interrupt.set()
            except Exception as e:  # noqa: BLE001 - a store that is busy for a moment: look again
                self.log({"type": "party", "t": time.time(), "error": f"watch: {type(e).__name__}: {e}"[:200]})
            await asyncio.sleep(2.0)

    def leader_dead(self) -> bool:
        lead = self.party.statuses().get(self.party.leader)
        return bool(lead and lead.get("dead") and lead["age_s"] < STALE_S)

    def leader_down(self, g: dict[str, Any] | None) -> bool:
        """The leader is dead, or busy with its own death (its latest goal is its resurrection or corpse)."""
        return self.leader_dead() or bool(g and g["tool"] in ("resurrect", "recover_corpse"))

    @property
    def follow_tiles(self) -> int:
        return FOLLOW_TILES_WALKING if getattr(self.session, "pace", "run") == "walk" else FOLLOW_TILES

    def anchor(self) -> dict[str, Any] | None:
        """Whom to keep beside between goals: the leader, or while it is down its deputy, the first of the
        others alive (None for the deputy itself, and when nobody is heard from)."""
        stats = self.party.statuses()
        lead = stats.get(self.party.leader)
        if lead and not lead["dead"] and lead["age_s"] < STALE_S:
            return lead
        for name in self.party.members:
            if name == self.party.leader:
                continue
            if name == self.party.me:
                return None
            st = stats.get(name)
            if st and not st["dead"] and st["age_s"] < STALE_S:
                return st
        return None

    async def carry_on(self) -> bool:
        """While the leader is down: another spell of the hunt it last set. In a run whose leader died
        first the others waited through its resurrection and its walk back, and never returned to the
        fight (cuo-e1p). False when there is no hunt to carry on."""
        if self.last_hunt is None:
            return False
        area = str(self.last_hunt.get("area", ""))
        if not self.leader_dead():
            # The leader's latest goal is its corpse or its resurrection, and it may be long over (refused in
            # 0.4 s, and three in town set off for a graveyard 130 tiles away): only those still at the hunt.
            spot = self.session.hunt_spot(area) if hasattr(self.session, "hunt_spot") else None
            snap = await self.session.snap() if spot is not None else {}
            if spot is not None and (not snap.get("in_game") or tiles(
                    snap["player"]["x"], snap["player"]["y"], spot[0], spot[1]) > CARRY_ON_TILES):
                return False
        self.doing = f"carrying on the hunt at {area} while the leader is down"
        self.carried_on += 1
        self.log({"type": "goal", "t": time.time(), "tool": "hunt", "args": {"area": area, "minutes": CARRY_ON_MIN},
                  "party": True, "why": "the leader is down: carrying on its last hunt"})
        self.hunting = True
        try:
            r = await self.session.hunt(area, CARRY_ON_MIN)
        finally:
            self.hunting = False
        self.log({"type": "goal_result", "t": time.time(), "tool": "hunt", "result": r.to_tool(), "party": True,
                  "carried_on": True})
        if not r.ok and "minutes" not in r.data:
            self.last_hunt = None  # refused or unreachable: wait like before
        return True

    async def step(self) -> None:
        s = self.session
        s.interrupt.clear()
        snap = await s.snap()
        if not snap.get("in_game"):
            await asyncio.sleep(5)
            return
        if snap["player"]["dead"]:
            self.doing = "dead: being resurrected"
            self.own = True
            try:
                self.log({"type": "goal", "t": time.time(), "tool": "resurrect", "args": {}, "party": True})
                r = await self.guarded(s.resurrect())
                self.log({"type": "goal_result", "t": time.time(), "tool": "resurrect", "result": r.to_tool(),
                          "party": True})
                if r.ok:
                    # Back for its kit whatever danger was seen there: the party is fighting there. Refused
                    # for that reason, a follower played on with no bow and no bandages.
                    self.doing = "going back for its corpse"
                    c = await self.guarded(s.recover_corpse(go_anyway=True))
                    self.log({"type": "goal_result", "t": time.time(), "tool": "recover_corpse", "result": c.to_tool(),
                              "party": True})
            finally:
                self.own = False
            return
        g = self.party.latest()
        if g is not None and g["rev"] > self.rev:
            self.rev = g["rev"]
            # The watcher's interrupt, set while this step was looking, was for this very goal. Left set, a
            # member that joined after the leader's first goal had it cut short at once ("interrupted") and
            # then only kept up with the party until the next one.
            s.interrupt.clear()
            await self.carry_out(g)
            return
        if g is not None and g["tool"] == "finish":
            self.finished = True
            return
        if self.leader_down(g) and await self.carry_on():
            return
        await self.keep_up(snap)

    @staticmethod
    async def guarded(goal: Any) -> Any:
        """A goal carried out, with a crash reported as its failure. The leader's planner does the same
        (Planner.carry_out); without it one bad argument from the planner's model (`minutes: null`), or a
        timeout in a travel poll, ended every follower's session, and they stood idle for the rest of the run."""
        from .session import Result
        try:
            return await goal
        except Exception as e:  # noqa: BLE001
            return Result(False, f"error: {type(e).__name__}: {e}"[:300])

    async def carry_out(self, g: dict[str, Any]) -> None:
        tool, args = g["tool"], g["args"] if isinstance(g["args"], dict) else {}
        self.goals += 1
        self.log({"type": "goal", "t": time.time(), "tool": tool, "args": args, "party": True, "rev": g["rev"],
                  "why": f"the leader's goal: {g['why']}" if g["why"] else "the leader's goal"})
        r = await self.guarded(self.follow(g, tool, args))
        if r is None:
            return  # logged where it was done (the leader finished; its hunt carried on)
        self.log({"type": "goal_result", "t": time.time(), "tool": tool, "result": r.to_tool(), "party": True,
                  "rev": g["rev"]})

    async def follow(self, g: dict[str, Any], tool: str, args: dict[str, Any]) -> Any:
        """The leader's goal as this member's own; its result, or None when there is none to log."""
        from .session import Result
        s = self.session
        if tool == "travel_to":
            place = str(args.get("place") or "")
            xy = place.split(",")
            at = {"x": int(xy[0]), "y": int(xy[1])} if len(xy) == 2 and all(
                v.strip().lstrip("-").isdigit() for v in xy) else {"place": place}
            self.doing = f"travelling with the party to {place}"
            # A party that walks its trips: beside the leader, not each on its own path (cuo-cod.7).
            walks = getattr(s, "pace", "run") == "walk" and getattr(s, "beside", None)
            r = await s.defended(s.walk_with(s.beside, **at) if walks else s.travel_to(**at, distance=3))
        elif tool == "hunt":
            self.doing = f"hunting {args.get('area')} with the party"
            self.last_hunt = dict(args)
            self.hunting = True
            try:
                # Formed up for with the leader, under the goal's own number (rally.py).
                forming = {"rally_key": f"goal:{g['rev']}"} if getattr(s, "rally", None) is not None else {}
                minutes = number(args.get("minutes"), 10.0)
                if self.ends_at is not None:
                    # Not past this member's own session: its leader's brain may be gone by then, and nothing
                    # else would end the hunt (live, a nine-minute run's followers hunted on to 15.6 minutes).
                    minutes = min(minutes, max(0.5, (self.ends_at - time.monotonic()) / 60))
                r = await s.hunt(str(args.get("area", "")), minutes, **forming)
            finally:
                self.hunting = False
        elif tool == "rest":
            # Rest beside the leader: one left behind rested 50-90 tiles off while the leader waited for it.
            if await self.far_from_leader():
                await self.keep_up(await s.snap())
                if (await s.snap()).get("player", {}).get("dead"):
                    # Died on the way: its resurrection comes first (it "rested 68 s, health 0/85" as a ghost).
                    return Result(False, "died on the way to the party")
            self.doing = "resting with the party"
            r = await s.defended(s.rest(max(5.0, min(300.0, number(args.get("seconds"), 30.0)))))
        elif tool == "inn":
            r = await s.defended(self.inn(args))
        elif tool in RESTOCK:
            # Its own business, like its resurrection: the leader's next goal (often the rest it calls to wait
            # for the shoppers) doesn't cut it short, or it would hunt on without what it went for.
            self.doing = "restocking its own supplies"
            self.own = True
            try:
                r = await s.defended(s.restock())
            finally:
                self.own = False
        elif tool == "finish":
            self.finished = True
            self.doing = "done"
            self.log({"type": "goal_result", "t": time.time(), "tool": tool, "result": {"ok": True, "result": "the "
                      "leader finished"}, "party": True})
            return None
        else:
            # The leader's own business. Its corpse, from elsewhere: back there with it, as a party would go (alone
            # it is refused the trip for the danger seen there, and waited in town till the corpse had decayed).
            if tool == "recover_corpse" and (at := await self.far_from(args)) is not None:
                self.doing = "going back with the leader for its corpse"
                return await s.defended(s.travel_to(x=at[0], y=at[1], distance=3, refuse=False, pace="run"))
            # Its resurrection or corpse: carry on the hunt it last set (cuo-e1p); a strategy or a plan, or with
            # no hunt to carry on: wait it out where it is.
            if self.leader_down(g) and await self.carry_on():
                return None
            self.doing = "waiting for the leader"
            r = await s.defended(s.rest(15))
        return r

    async def inn(self, args: dict[str, Any]) -> Any:
        """To the inn with the party (cuo-cod.3), a seat at its table until the talk is done (cuo-cod.4), then
        this member's own memories of it (cuo-cod.5)."""
        from . import table as tables
        from .session import Result
        s = self.session
        place = str(args.get("place") or "the inn")
        self.doing = f"walking to {place} with the party"
        at = {"x": int(number(args["x"], 0)), "y": int(number(args["y"], 0))} \
            if number(args.get("x"), 0) and number(args.get("y"), 0) else {}
        if getattr(s, "pace", "run") == "walk" and getattr(s, "beside", None):
            r = await s.walk_with(s.beside, place, **at)
        else:
            r = await s.travel_to(place, **at, distance=3, refuse=False)
        if not r.ok:
            return Result(False, f"did not reach the inn: {r.summary}", r.data)
        if at:
            await self.come_in(at["x"], at["y"])
        if self.table is None:
            self.doing = "at the inn with the party"
            return Result(True, f"at {place} with the party")
        self.doing = f"at the table in {place}, talking the hunt over"
        await self.table.arrive()
        tid = await self.table.sit(wait_s=TABLE_WAIT_S)
        if tid is None:
            return Result(True, f"at {place}; no table was opened")
        # The hunt talked over, for the memories kept: the leader's last one.
        self.table.area = str(self.last_hunt.get("area")) if self.last_hunt else None
        kept = await self.table.reflect(tid, self.table.area)
        self.table.story.reset()
        tables.learn(s, self.table)
        self.doing = "at the inn with the party"
        return Result(True, f"sat at the party's table in {place}: said {self.table.spoken} lines, kept {len(kept)} "
                            f"memories of it")

    async def come_in(self, x: int, y: int) -> None:
        """Indoors with the others: close to the table's spot and in sight of the leader. Three tiles from
        the spot can be outside the wall, where the leader is in view but not in sight."""
        s = self.session
        for distance in (2, 1):
            await s.travel_to(x=x, y=y, distance=distance, refuse=False)
            snap = await s.snap()
            lead = next((m for m in snap.get("mobiles") or [] if m.get("name") == self.party.leader), None)
            if lead is not None and lead.get("in_sight") is not False:
                return

    async def far_from(self, args: dict[str, Any], within: int = 12) -> tuple[int, int] | None:
        """The place a goal names (x, y), when this member is further than `within` tiles from it."""
        x, y = int(number(args.get("x"), 0)), int(number(args.get("y"), 0))
        snap = await self.session.snap()
        if not (x and y) or not snap.get("in_game"):
            return None
        p = snap["player"]
        return (x, y) if tiles(p["x"], p["y"], x, y) > within else None

    async def far_from_leader(self) -> bool:
        snap = await self.session.snap()
        lead = self.party.statuses().get(self.party.leader)
        if not snap.get("in_game") or not lead or lead["dead"] or lead["age_s"] >= STALE_S:
            return False
        p = snap["player"]
        return tiles(p["x"], p["y"], lead["x"], lead["y"]) > self.follow_tiles

    async def keep_up(self, snap: dict[str, Any]) -> None:
        """Between goals: walk back to the leader when it is further than FOLLOW_TILES, else wait."""
        s = self.session
        lead = self.anchor()  # the leader, or its deputy while it is down
        p = snap["player"]
        if lead and tiles(p["x"], p["y"], lead["x"], lead["y"]) > self.follow_tiles:
            self.doing = "catching up with the party"
            r = await s.defended(s.travel_to(x=lead["x"], y=lead["y"], distance=3, refuse=False, pace="run"))
            self.log({"type": "goal_result", "t": time.time(), "tool": "keep_up", "result": r.to_tool(), "party": True})
            return
        self.doing = "with the party, waiting for the leader's next goal"
        await s.defended(s.rest(5))

    async def run(self, stop: asyncio.Event, hours: float) -> dict[str, Any]:
        began = time.monotonic()
        self.ends_at = began + hours * 3600
        watcher = asyncio.get_running_loop().create_task(self.watch(stop))
        try:
            while not stop.is_set() and not self.finished and time.monotonic() - began < hours * 3600:
                try:
                    await self.step()
                except Exception as e:  # noqa: BLE001 - a step that broke (a lost client, a busy store): the next one
                    self.log({"type": "party", "t": time.time(), "error": f"step: {type(e).__name__}: {e}"[:300]})
                    await asyncio.sleep(2.0)
        finally:
            watcher.cancel()
        return {"goals_followed": self.goals, "carried_on_without_leader": self.carried_on,
                "finished": "finished" if self.finished else "stopped" if stop.is_set() else "time up"}
