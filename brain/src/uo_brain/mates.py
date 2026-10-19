"""The party beside a character in a fight: what is on whom, and its callouts (cuo-cod.2, cuo-4ti).

One `Mates` per brain, handed to the fight loop as `PolicyConfig.link`. The loop calls it twice
around each decision:

- `before(sit)` writes this member's fight row to the party store (health, what is attacking it,
  its pets) and reads the others': the companions in view go into the situation as `sit.mates`,
  each creature on a companion says so in its own words ("attacking your companion Isolde (mage,
  badly wounded)"), and the companions' callouts of the last seconds go into the state as
  `party_calls`, so Jev's choice of target and intent can answer them and code can help
  (policy.help_mates).
- `after(rpc, sit, dec, mem)` calls out what just happened to this member: hurt (under a half)
  and badly hurt (under a third), back on its feet, the creature it starts on, a dangerous one on
  it, leaving, out of bandages, reagents or arrows, a companion down, the fight over. Code decides
  when, from what the fight loop already knows: no model call for a callout. The meaning goes into
  the party store at once (party_calls); the words come from the character's voice pack
  (voice.py), are said aloud (they show over its head and in the footage), and are held to one
  every GAP_S, except that "nearly dead" goes at once.

Local server only, as all talk.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .party import Party
from .state import Candidate, Mate, Situation
from .table import Story
from .voice import Voice

GAP_S = 6.0          # at most one spoken callout this often for one member; "nearly dead" goes anyway
PENDING_S = 8.0      # a callout not said within this has missed its moment
FAR_AGE_S = 12.0  # a companion out of view is gone to where its report said it was, no older than this
HEARD_S = 15.0       # the others' callouts stay in the fight state this long
HURT, BAD, WELL = 50, 33, 60  # health %: hurt below, badly hurt below, well again at or above
QUIET_S = 15.0       # a creature that isn't dangerous is only called when nothing was said for this long
AGAIN_S = 45.0       # "falling back" isn't called again within this: one who wavers between leaving and fighting
#                      shouted it four times in a minute
FALLEN_S = 20.0      # a companion in view this lately, whose own report now says dead, fell where it was last seen
RAISE_CALL_S = 60.0  # a raiser says whom it is raising once, not at every try (a try is every 7 to 10 s)
TAKES_BLOWS = ("warrior", "paladin", "warrior-mage")  # a point of these is the party's front (rally.POINT)
# A corpse named for what it is made of, as the creature it was ("a skeletal corpse").
CORPSE_NAMES = {"a skeletal": "a skeleton", "an orcish": "an orc", "a ratman's": "a ratman"}
# Which pending callout is said first.
ORDER = ("badly_hurt", "down", "on_me", "leaving", "hurt", "out_of_bandages", "out_of_reagents", "out_of_arrows",
         "on_target", "helping", "raising", "recovered", "clear")


@dataclass
class Pending:
    kind: str
    t: float
    slots: dict[str, str] = field(default_factory=dict)
    urgent: bool = False
    target: int = 0  # the creature it is about


def short(h: Candidate, hostiles: list[Candidate]) -> str:
    """A creature for a shout: "the lich" when it is the only one of its kind in view, else "a bone
    knight"; a named one by what it is ("Vorgak (an orc)" -> "the orc"), and one whose name says what it
    is by that ("a bone knight (a skeleton)" -> "a bone knight")."""
    own, _, body = h.name.partition(" (")
    name = own if own.lower().startswith(("a ", "an ")) or not body else body.rstrip(")")
    kind = name.removeprefix("an ").removeprefix("a ").strip() or "creature"
    same = sum(1 for o in hostiles if kind in o.name.lower())
    return f"the {kind}" if same <= 1 else f"{'an' if kind[:1] in 'aeiou' else 'a'} {kind}"


def dangerous(h: Candidate) -> bool:
    return str(h.info.get("strength", "")).startswith(("stronger", "far stronger")) or bool(h.info.get("casts_spells"))


class Mates:
    def __init__(self, party: Party, voice: Voice | None = None, callouts: bool = True,
                 log: Callable[[dict[str, Any]], None] | None = None, floor: Any = None):
        self.party = party
        self.floor = floor  # talk.Floor: a callout holds it while it is up, so chatter doesn't start over it
        self.voice = voice or Voice()
        self.callouts = callouts  # False: the fight rows and the others' view still work, nothing is called
        self.log = log or (lambda _: None)
        self.pending: dict[str, Pending] = {}
        self.last_said = -1e9
        self.said = 0
        self.called: dict[str, int] = {}
        self.band = "ok"
        self.target_called = 0
        self.on_me_called: set[int] = set()
        self.leaving = False
        self.left_at = -1e9
        self.out: set[str] = set()
        self.down: set[str] = set()
        self._raise_called: dict[str, float] = {}  # whom it has said it is raising, and when
        self.in_fight = False
        self.heard = 0
        self.roleplay = None   # roleplay.Roleplay: an emote after a fight, and by a fallen companion (cuo-cod.8)
        self.story = Story()   # this member's own part of the hunt, for the table at the inn (table.py)
        self.tactics: list[str] = []  # its own lines about how to fight, from earlier tables (cuo-cod.5)
        self._heard_ids: set[int] = set()
        self._calls_now = False  # the decision being made has companions' callouts in its state
        self.away = False        # dead, or back from the dead and not yet in a fight again: it calls nothing
        self._seen: dict[str, tuple[float, int, int]] = {}  # companion -> when last in view, how far, its serial
        self._health: dict[str, list[tuple[float, int]]] = {}  # companion -> its health (percent) over the last seconds
        self._stats: tuple[float, dict[str, dict[str, Any]]] = (0.0, {})
        self.rally = None        # rally.Rally: where the party formed up, for a run back to it (cuo-cym.1)
        self.aim = 0             # the creature its last decision went for (seeking or fighting), for the others
        self.running = False     # its last decision was a leave or a flee: not one still in the fight
        # Where a callout is said (cuo-cym.3): "say" aloud, over its head, as on the local server and in
        # footage; "party" in the game's party chat, which only the party reads; "both". Party chat needs the
        # game's own party (gameparty.GameParty): until the member is in it, a callout is said aloud.
        self.to = "say"
        self.game = None

    def died(self) -> None:
        """This member died: its fight is over. Without this its first decision after the healer saw "a fight
        that ended with nothing near" and called "all clear" (and "out of bandages") from a hundred tiles
        off, at 15% health, and the others' fight state said the fight was over while they were on the lich."""
        self.story.deaths += 1
        self.in_fight = False
        self.leaving = False
        self.target_called = 0
        self.on_me_called.clear()
        self.pending.clear()
        self.band = "bad"  # nothing about its health until it is well again
        # ...and nothing at all until it is in a fight again: resurrected, it shouted "Isolde is down!" and
        # "Out of bandages!" from the healer, half a minute late and a hundred tiles from the others.
        self.away = True
        self._seen.clear()

    def fall(self, name: str, pct: int | None, now: float | None = None) -> int:
        """Health (percent) a companion lost over the last HP_WINDOW_S seconds: the highest it was since, less
        now, as policy.Memory.health_drop measures this character's own (cuo-cym.31). Healing in between doesn't
        hide a fall that came after it."""
        from .policy import HP_WINDOW_S
        if pct is None:
            return 0
        now = time.time() if now is None else now
        trail = [(t, h) for t, h in self._health.get(name, []) if now - t <= HP_WINDOW_S] + [(now, pct)]
        self._health[name] = trail
        return max(0, max(h for _, h in trail) - pct)

    def statuses(self) -> dict[str, dict[str, Any]]:
        """The members' own reports, read at most once a second."""
        now = time.time()
        if now - self._stats[0] > 1.0:
            self._stats = (now, self.party.statuses())
        return {n: {**st, "age_s": st["age_s"] + now - self._stats[0]} for n, st in self._stats[1].items()}

    # ---- before a decision ---------------------------------------------------------

    def before(self, sit: Situation) -> None:
        snap, party = sit.raw, self.party
        p = snap["player"]
        mobiles = snap.get("mobiles") or []
        attackers = sorted(m["serial"] for m in mobiles if m.get("monster") and not m.get("dead") and m.get("attacking_me"))
        pets = [{"serial": x["serial"], "name": x.get("name") or "", "hits_pct": x.get("hits_pct")}
                for x in snap.get("pets") or []]
        party.fight({"hits": p.get("hits"), "hits_max": p.get("hits_max"), "dead": bool(p.get("dead")),
                     "attackers": attackers, "target": snap["agent"].get("engaged", 0), "pets": pets,
                     **({"aim": self.aim} if self.aim else {}), **({"running": True} if self.running else {})})
        rows = party.fights()
        by_serial = {m["serial"]: m for m in mobiles}
        calls = party.calls(HEARD_S)
        # One being healed by another is left to them. Not so one being raised: a try works one time in four,
        # so everyone who can raise tries (live, the mage's fifth cast brought the archer back after 63 s, with
        # the warrior and his bandages standing by because she had called it).
        helped = {c["about"] for c in calls if c["kind"] == "helping" and time.time() - c["t"] <= 5}
        sit.mates = []
        for m in mobiles:
            name = m.get("name")
            if name not in party.companions or m.get("monster"):
                continue
            row = rows.get(name) or {}
            pct = round(100 * row["hits"] / row["hits_max"]) if row.get("hits_max") else m.get("hits_pct")
            mate_pets = [{**pet, "distance": by_serial[pet["serial"]]["distance"],
                          "hits_pct": by_serial[pet["serial"]].get("hits_pct", pet.get("hits_pct"))}
                         for pet in row.get("pets") or [] if pet.get("serial") in by_serial]
            mate = Mate(name, m["serial"], party.companions[name], pct, m["distance"], m.get("in_sight") is not False,
                        bool(m.get("dead") or row.get("dead")), list(row.get("attackers") or []), mate_pets)
            mate.being_helped = name in helped
            mate.hits_drop = self.fall(name, pct)
            sit.mates.append(mate)
            self._seen[name] = (time.time(), m["distance"], m["serial"])
        # A dead companion's ghost isn't sent to the living: it just leaves the view. One seen a moment ago
        # whose own report says dead has fallen where it was last seen (in eight runs, 17 deaths and 2 "down"s).
        in_view = {mate.name for mate in sit.mates}
        gone = [n for n, (t, _, _) in self._seen.items() if n not in in_view and time.time() - t <= FALLEN_S]
        if p.get("dead"):
            self._seen.clear()  # a ghost sees the other ghosts: nothing it saw says who fell
        elif gone:
            stats = self.statuses()
            for name in gone:
                st = stats.get(name) or {}
                if (rows.get(name) or {}).get("dead") or (st.get("dead") and st["age_s"] < 30):
                    _, dist, serial = self._seen[name]
                    sit.mates.append(Mate(name, serial, party.companions[name], 0, dist, False, True))
                    if "your_party" in sit.state or sit.hostiles:
                        sit.state.setdefault("your_party", []).append(
                            {"name": name, "plays_as": party.companions[name], "health": "dead: fell a moment ago"})
        hostile = {h.serial: h for h in sit.hostiles}
        for mate in sorted(sit.mates, key=lambda x: 100 if x.hits_pct is None else x.hits_pct):
            on = [hostile[s] for s in mate.attackers if s in hostile]
            for h in on:
                h.info.setdefault("attacking_your_companion", mate.words())  # the worst hurt of two is named
            entry = next((e for e in sit.state.get("your_party", []) if e["name"] == mate.name), None)
            if entry is not None and on:
                entry["attacked_by"] = [h.name for h in on[:3]]
        words = self.heard_words(calls, hostile)
        self._calls_now = bool(words)
        if words:
            sit.state["party_calls"] = words
        if self.tactics and sit.hostiles:
            sit.state["your_own_lessons_for_fighting_with_this_party"] = self.tactics
        if self.rally is not None:
            sit.rally = self.rally.current()
            sit.rally_back = self.rally.back()  # the second point behind it, when the rally point was chosen (cuo-cym.13)
            # Who is still in the fight: the ones a member that falls back goes back in for (policy.decide_intent).
            # ...by each one's own fight row: a "leaving" callout is said once in 45 s and not at all with callouts
            # off, and one being chased has attackers, so two running members each took the other to be fighting.
            running = {name for name, row in rows.items() if row.get("running")}
            sit.party_fighting = sum(1 for name, row in rows.items() if not row.get("dead") and name not in running
                                     and (row.get("target") or row.get("attackers")))
            sit.party_running = sum(1 for name in running if name != party.me and not rows[name].get("dead"))
            # One of them out of view with something on it: where it last said it was, for those with nothing
            # of their own to fight (policy.to_far_companion). Not one that is only running: live, a tamer
            # who had called her pet back and walked off to her leader had the mage and the bard walk after
            # her, away from the bone knight the archer was shooting.
            away = [name for name, row in rows.items() if name not in in_view and not row.get("dead")
                    and row.get("attackers")]
            if away and not p.get("dead"):
                stats = self.statuses()
                spots = [(max(abs(st["x"] - p["x"]), abs(st["y"] - p["y"])), name, st["x"], st["y"]) for name in away
                         if (st := stats.get(name)) and "x" in st and st["age_s"] <= FAR_AGE_S and not st.get("dead")]
                if spots:
                    _, name, x, y = min(spots)
                    sit.mate_far = (name, x, y)
            # What the point is going for: the others go in on it too (policy.with_the_point).
            point = self.rally.point()
            if point and point != party.me and point not in running and not (rows.get(point) or {}).get("dead"):
                sit.point_name = point
                sit.point_aim = (rows.get(point) or {}).get("aim") or (rows.get(point) or {}).get("target") or 0
                if party.companions.get(point) in TAKES_BLOWS:
                    sit.point_serial = next((m.serial for m in sit.mates if m.name == point and not m.dead), 0)
        self.tell_story(sit, calls, words)

    def tell_story(self, sit: Situation, calls: list[dict[str, Any]], words: list[str]) -> None:
        """What this decision adds to the member's own story of the hunt (table.Story)."""
        st = self.story
        engaged = sit.hostile_by_serial(sit.agent.get("engaged", 0))
        if engaged is not None:
            name = short(engaged, sit.hostiles)
            st.fought[name] = st.fought.get(name, 0) + 1
        if sit.hostiles and (st.low is None or sit.hp_pct < st.low[0]):
            mobiles = {m["serial"]: m for m in sit.raw.get("mobiles") or []}
            on = next((short(h, sit.hostiles) for h in sit.hostiles if mobiles.get(h.serial, {}).get("attacking_me")), "")
            st.low = (sit.hp_pct, on, [m.name for m in sit.mates if not m.dead and m.distance <= 4])
        for c in sit.corpses:
            name = c.name.lower().replace("corpse of ", "").replace("'s corpse", "").replace(" corpse", "").strip()
            name = CORPSE_NAMES.get(name, name)
            if name and name not in st.dead_seen:
                st.dead_seen.append(name)
        for c in calls:
            if c["id"] in self._heard_ids:
                continue
            self._heard_ids.add(c["id"])
            if c["kind"] == "helping" and c["about"] == self.party.me:
                st.healed_by[c["who"]] = st.healed_by.get(c["who"], 0) + 1
            if c["kind"] == "raising" and c["about"] == self.party.me:
                st.raised_by = c["who"]
            if c["kind"] == "down" and c["about"] and c["about"] not in st.fell:
                st.fell.append(c["about"])
        for w in words:
            w = w.replace(", just now", "").replace(", a moment ago", "")
            if w not in st.heard and not w.endswith("fighting a creature"):
                st.heard.append(w)
        del st.heard[:-8]

    def heard_words(self, calls: list[dict[str, Any]], hostile: dict[int, Candidate]) -> list[str]:
        """The others' callouts in words, the latest of each kind for each member, oldest first:
        "Isolde (mage), just now: near death, 18/60 health". "Just now" and "a moment ago" only: a
        count of seconds would change the state every tick, and the loop would ask Jev again each time."""
        latest: dict[tuple[str, str], dict[str, Any]] = {}
        for c in calls:
            if c["kind"] != "seat":  # a seat taken at the table (companion.py): not a callout of a fight
                latest[(c["who"], c["kind"])] = c
        now = time.time()
        out = []
        for c in sorted(latest.values(), key=lambda c: c["t"])[-6:]:
            h = hostile.get(c["target"])
            what = h.name if h is not None else c["about"] or "a creature"
            health = f", {c['hits']}/{c['hits_max']} health" if c.get("hits_max") else ""
            said = {
                "hurt": f"hurt{health}", "badly_hurt": f"near death{health}", "recovered": "back on their feet",
                "on_target": f"fighting {what}", "on_me": f"{what} is attacking them", "leaving": "running from the fight",
                "out_of_bandages": "out of bandages", "out_of_reagents": "out of reagents: no more spells",
                "out_of_arrows": "out of arrows: the bow can't shoot", "down": f"{c['about']} is dead",
                "clear": "the fight is over, nothing left", "helping": f"healing {c['about']}",
                "form_up": "forming the party up before going in", "go_in": "gave the word to go in together",
                "raising": f"bringing {c['about']} back from the dead",
            }.get(c["kind"])
            if said:
                who = f"{c['who']} ({self.party.companions.get(c['who'], 'companion')})"
                out.append(f"{who}, {'just now' if now - c['t'] <= 5 else 'a moment ago'}: {said}")
        return out

    # ---- after a decision ----------------------------------------------------------

    def note(self, sit: Situation, kind: str, target: Candidate | None = None, about: str = "",
             urgent: bool = False) -> None:
        """A callout: its meaning into the party store now, its line said when the gap allows."""
        if self.away:
            return
        p = sit.player
        self.party.call(kind, target.serial if target else 0, about or (target.name if target else ""),
                        p.get("hits"), p.get("hits_max"))
        self.called[kind] = self.called.get(kind, 0) + 1
        st = self.story
        st.called[kind] = st.called.get(kind, 0) + 1
        if kind == "helping":
            st.healed[about] = st.healed.get(about, 0) + 1
        elif kind == "raising":
            st.raised.setdefault(about, 0)
            st.raised[about] += 1
        elif kind == "leaving":
            st.ran += 1
        elif kind.startswith("out_of_"):
            st.out_of.append(kind.removeprefix("out_of_"))
        elif kind == "down" and about not in st.fell:
            st.fell.append(about)
        slots = {"target": short(target, sit.hostiles)} if target is not None else {"name": about} if about else {}
        self.pending[kind] = Pending(kind, time.monotonic(), slots, urgent, target.serial if target else 0)

    def speaks_for_all(self, sit: Situation) -> bool:
        """Of the members who saw the same thing (a companion fall, the fight end), the first by name
        says it: five brains would otherwise shout it at once."""
        near = [m.name for m in sit.mates if not m.dead and m.distance <= 12]
        return self.party.me == min([self.party.me, *near])

    def events(self, sit: Situation, dec: Any, mem: Any) -> None:
        hp = sit.hp_pct
        band = "bad" if hp < BAD else "hurt" if hp < HURT else "ok" if hp >= WELL else self.band
        if band != self.band:
            if band == "bad":
                self.note(sit, "badly_hurt", urgent=True)
            elif band == "hurt" and self.band == "ok":
                self.note(sit, "hurt")
            elif band == "ok":
                self.note(sit, "recovered")
            self.band = band

        fighting = dec.intent == "fight" and dec.target is not None
        if self.away and (fighting or sit.agent.get("engaged")) and not sit.player.get("dead"):
            self.away = False  # back in a fight: it has things to say again

        mobiles = {m["serial"]: m for m in sit.raw.get("mobiles") or []}
        on_me = [h for h in sit.hostiles if mobiles.get(h.serial, {}).get("attacking_me") and dangerous(h)
                 and h.serial not in self.on_me_called]
        if on_me:
            worst = max(on_me, key=lambda h: (str(h.info.get("strength", "")).startswith("far"), -h.distance))
            self.on_me_called.add(worst.serial)
            self.note(sit, "on_me", target=worst)
        self.on_me_called &= set(mobiles)

        if fighting and dec.target.serial != self.target_called \
                and (dangerous(dec.target) or time.monotonic() - self.last_said >= QUIET_S):
            self.target_called = dec.target.serial
            self.note(sit, "on_target", target=dec.target)

        running = dec.intent in ("leave", "flee")
        if running and not self.leaving and time.monotonic() - self.left_at >= AGAIN_S:
            self.left_at = time.monotonic()
            self.note(sit, "leaving")
        self.leaving = running

        supplies = sit.player.get("supplies") or {}
        short_of = {
            "bandages": sit.archetype != "mage" and supplies.get("bandages", 0) <= 0,
            "arrows": sit.is_archer and sit.ammo <= 0,
            # Known only with something to cast at (the spells on offer are worked out against a target): with
            # nothing hostile in view it is left as it was, or it was shouted again each time one came by.
            "reagents": (sit.casts and not sit.is_necromancer
                         and str(sit.state["you"].get("reagents", "")).startswith("running out") and not sit.spells)
            if sit.threats else None,
        }
        for what, gone in short_of.items():
            if gone and what not in self.out and (sit.threats or self.in_fight) and not self.away:
                self.out.add(what)
                self.note(sit, f"out_of_{what}")
            elif gone is False:
                self.out.discard(what)

        for mate in sit.mates:
            if mate.dead and mate.name not in self.down:
                self.down.add(mate.name)
                if mate.name not in self.story.fell:
                    self.story.fell.append(mate.name)
                if self.speaks_for_all(sit):
                    self.note(sit, "down", about=mate.name)
            elif not mate.dead:
                self.down.discard(mate.name)

        if getattr(mem, "helped", None):
            whom, how = mem.helped[0], mem.helped[1]
            if how != "raise":
                self.note(sit, "helping", about=whom)
            elif time.monotonic() - self._raise_called.get(whom, -1e9) >= RAISE_CALL_S:
                self._raise_called[whom] = time.monotonic()
                self.note(sit, "raising", about=whom)

        near = [h for h in sit.threats if h.distance <= 12]
        if fighting or sit.agent.get("engaged"):
            self.in_fight = True
        elif self.in_fight and not near:
            self.in_fight = False
            self.target_called = 0
            if self.speaks_for_all(sit):
                self.note(sit, "clear")

    async def after(self, rpc: Any, sit: Situation, dec: Any, mem: Any = None) -> str | None:
        """Call out what this decision showed; the line said, if one was."""
        self.heard += self._calls_now  # decisions, not looks: `before` runs at every look
        self._calls_now = False
        self.aim = dec.target.serial if dec.intent in ("seek", "fight") and dec.target is not None else 0
        self.running = dec.intent in ("leave", "flee")
        if not self.callouts or sit.assisting:
            return None
        was_fighting = self.in_fight
        self.events(sit, dec, mem)
        said = await self.flush(rpc, sit)
        if self.roleplay is not None and not self.in_fight and not any(h.distance <= 10 for h in sit.threats):
            # Quiet again: a small thing done after the fight, or beside a companion who fell in it.
            fallen = next((m for m in sit.mates if m.dead and m.distance <= 3), None)
            if fallen is not None:
                await self.roleplay.act("fallen", name=fallen.name, key=f"fallen:{fallen.name}:{len(self.story.fell)}")
            elif was_fighting:
                await self.roleplay.act("kill")
        return said

    async def speak(self, rpc: Any, line: str) -> None:
        """A callout's line, where this party says them."""
        chat = self.to in ("party", "both") and self.game is not None and self.game.joined
        if chat:
            await rpc.call("act", verb="party", kind="say", text=line)
        if not chat or self.to == "both":
            await rpc.call("act", verb="say", text=line)

    async def shout(self, rpc: Any, kind: str, **slots: str) -> str | None:
        """A callout from outside a fight decision (the leader's "form up" and "in we go", rally.py): its
        meaning to the party store, its line aloud at once."""
        if not self.callouts or self.away:
            return None
        self.party.call(kind)
        self.called[kind] = self.called.get(kind, 0) + 1
        line = self.voice.pick(kind, **slots)
        if not line:
            return None
        await self.speak(rpc, line)
        self.last_said = time.monotonic()
        self.said += 1
        if self.floor is not None:
            self.floor.say(line, kind="callout")
        self.log({"type": "callout", "t": time.time(), "kind": kind, "text": line, **slots})
        return line

    async def flush(self, rpc: Any, sit: Situation | None = None) -> str | None:
        now = time.monotonic()
        self.pending = {k: p for k, p in self.pending.items() if now - p.t <= PENDING_S}
        if sit is not None and (p := self.pending.get("on_target")) and p.target != sit.agent.get("engaged", p.target) \
                and self.target_called != p.target:
            del self.pending["on_target"]  # it is on another creature by now: "I've got the lich" would be wrong
        if not self.pending:
            return None
        nxt = min(self.pending.values(), key=lambda p: (not p.urgent, ORDER.index(p.kind) if p.kind in ORDER else 99))
        if not nxt.urgent and now - self.last_said < GAP_S:
            return None
        del self.pending[nxt.kind]
        line = self.voice.pick(nxt.kind, **nxt.slots)
        if not line:
            return None
        try:
            await self.speak(rpc, line)
        except Exception as e:  # noqa: BLE001 - a callout is never worth a fight decision
            self.log({"type": "callout", "t": time.time(), "kind": nxt.kind, "error": f"{type(e).__name__}: {e}"[:200]})
            return None
        self.last_said = now
        self.said += 1
        if self.floor is not None:
            self.floor.say(line, kind="callout")
        self.log({"type": "callout", "t": time.time(), "kind": nxt.kind, "text": line,
                  **({"hp": sit.hp_pct} if sit is not None else {}), **nxt.slots})
        return line

    def to_log(self) -> dict[str, Any]:
        return {"callouts": dict(self.called), "callouts_said": self.said, "decisions_with_calls_heard": self.heard}
