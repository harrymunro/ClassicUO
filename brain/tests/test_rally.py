"""Going in together (cuo-cym.1): a party forms up at the edge of a hunt, enters as one, and falls back
to the place it formed up at."""

import asyncio
import copy

from uo_brain import mates, party, policy, rally, state
from uo_brain.session import Result, Session
from uo_brain.world import World

from conftest import SNAPSHOT
from test_party import COMPANIONS, LICH, NAMES, companion, lich_fight, monster
from test_policy import answers


def status(plays_as, x, y, **more):
    return {"plays_as": plays_as, "x": x, "y": y, "hits": 90, "hits_max": 90, "dead": False, "age_s": 2.0, **more}


STATS = {"Roland": status("warrior", 1000, 1000), "Isolde": status("mage", 1001, 1000),
         "Wystan": status("archer", 1009, 1000), "Thea": status("tamer", 1002, 1001),
         "Corwin": status("bard", 1100, 1000)}


def test_who_is_waited_for_and_who_goes_first():
    """Everyone alive, heard from, near enough and not on business of its own; the first who can take the
    blows goes in a step ahead."""
    stats = copy.deepcopy(STATS)
    assert rally.expected(stats, "Roland", (1000, 1000)) == ["Isolde", "Thea", "Wystan"]  # Corwin is 100 tiles off
    stats["Thea"]["busy"] = True     # gone back for its corpse
    stats["Wystan"]["dead"] = True
    stats["Isolde"]["age_s"] = 45.0  # not heard from: its client is gone
    assert rally.expected(stats, "Roland", (1000, 1000)) == []
    assert rally.point_of(NAMES, STATS, "Isolde") == "Roland"  # the warrior, whoever leads
    no_tank = {n: st for n, st in STATS.items() if n != "Roland"}
    assert rally.point_of(NAMES, no_tank, "Isolde") == "Isolde"
    dead_tank = {**STATS, "Roland": {**STATS["Roland"], "dead": True}}
    assert rally.point_of(NAMES, dead_tank, "Isolde") == "Isolde"


def test_who_is_missing_is_read_from_the_view_and_then_from_reports():
    snap = copy.deepcopy(SNAPSHOT)
    snap["mobiles"] = [companion("Isolde", 2), companion("Wystan", 7)]
    # Thea is out of view: by her own report, 2 tiles off. Wystan is in view and 7 off, whatever he reported.
    assert rally.missing(["Isolde", "Thea", "Wystan"], snap, STATS) == ["Wystan"]
    assert rally.missing(["Corwin"], snap, STATS) == ["Corwin"]
    assert rally.missing(["Nobody"], snap, {}) == ["Nobody"]


def test_what_ends_the_walk_in_and_the_wait():
    snap = copy.deepcopy(SNAPSHOT)
    snap["agent"]["engaged"] = 0
    snap["mobiles"] = [monster(0x300, "a lich", 24, 16)]
    assert rally.hostile_near(snap, rally.SIGHT_TILES, LICH) is None and not rally.contact(snap)
    snap["mobiles"][0]["distance"] = 12
    assert rally.hostile_near(snap, rally.SIGHT_TILES, LICH)["name"] == "a lich"
    snap["mobiles"][0]["attacking_me"] = True
    assert rally.contact(snap)
    # A companion's bear is nobody's enemy.
    bear = {**monster(0x400, "a grizzly bear", 212, 3), "pet": True, "war_mode": False}
    snap["mobiles"] = [bear]
    assert rally.hostile_near(snap, rally.SIGHT_TILES, LICH) is None


def test_the_rally_is_kept_in_the_party_store(tmp_path):
    w = World.open("local", root=tmp_path)
    lead = party.Party(w, "p1", "Roland", "Roland", NAMES)
    other = party.Party(w, "p1", "Isolde", "Roland", NAMES)
    assert other.rally("goal:1") is None and other.rally() is None
    lead.rally_open("goal:1", 1392, 1510, point="Roland", sighted=True)
    r = other.rally("goal:1")
    assert (r["x"], r["y"], r["point"], r["state"], r["sighted"]) == (1392, 1510, "Roland", "gathering", True)
    assert other.rally() is None  # nobody has gone in from it yet
    lead.rally_go("goal:1")
    assert other.rally("goal:1")["state"] == "go" and other.rally()["x"] == 1392
    # A hunt that isn't coming after all: the others are told, row or no row.
    lead.rally_go("goal:2", "off")
    assert other.rally("goal:2")["state"] == "off" and other.rally()["key"] == "goal:1"
    # Another run's party sees none of it.
    assert party.Party(w, "p2", "Isolde", "Roland", NAMES).rally() is None
    w.close()


class Field:
    """A session for forming up: a script of snapshots (the last one repeats), and what was asked of it."""

    def __init__(self, world, snaps):
        self.world, self.snaps, self.acts, self.trips = world, list(snaps), [], []
        self.interrupt = asyncio.Event()
        self.pace, self.travel_pace, self.rpc = "run", None, self
        self.said = []
        self.ground, self.asked, self.walk_s = None, [], 0.0  # the client's map, when a test has one; a walk's length

    async def snap(self, **_):
        snap = copy.deepcopy(self.snaps.pop(0) if len(self.snaps) > 1 else self.snaps[0])
        if any(v == "follow" for v, _ in self.acts):  # as the client says once it follows someone
            snap["agent"]["follow"] = {"state": "following"}
        return snap

    @staticmethod
    def where(snap):
        return snap["player"]["x"], snap["player"]["y"]

    def seen(self, snap):
        pass

    async def act(self, verb, **params):
        self.acts.append((verb, params))
        return {"status": "done"}

    async def call(self, method, **params):
        if params.get("verb") == "say":
            self.said.append(params["text"])
        if method == "nav":
            self.asked.append(params)
            if self.ground is not None:
                return self.ground
        return {"status": "done"}

    async def travel_to(self, place=None, x=None, y=None, distance=2, **kw):
        self.trips.append((place, x, y, distance))
        await asyncio.sleep(self.walk_s)
        return Result(True, "arrived")


def at(x, y, mobiles=(), name="Roland"):
    snap = copy.deepcopy(SNAPSHOT)
    snap["player"].update(name=name, x=x, y=y, hits=95, hits_max=95, dead=False)
    snap["mobiles"] = [copy.deepcopy(m) for m in mobiles]
    snap["agent"]["engaged"] = 0
    return snap


def report_all(w, where):
    """Every member's own report, as its brain would write it."""
    for name, (x, y) in where.items():
        p = party.Party(w, "p1", name, "Roland", NAMES)
        p.report(at(x, y, name=name), {**COMPANIONS, "Roland": "warrior"}[name])


def test_the_leader_waits_at_the_edge_and_gives_the_word(tmp_path, monkeypatch):
    """The leader walks to the edge of the area, not its centre, waits for the ones behind it, and gives the
    word once they are beside it; the others then go in a step behind the warrior."""
    monkeypatch.setattr(rally, "POINT_WAITS_S", 0.0)
    w = World.open("local", root=tmp_path)
    report_all(w, {"Roland": (1392, 1528), "Isolde": (1394, 1528), "Wystan": (1390, 1528), "Thea": (1392, 1530)})
    lead = party.Party(w, "p1", "Roland", "Roland", NAMES)
    # Before the walk; at the edge with the archer still 9 tiles behind; then all three beside it.
    s = Field(w, [at(1392, 1528), at(1380, 1498, [companion("Isolde", 2), companion("Wystan", 9), companion("Thea", 3)]),
                  at(1380, 1498, [companion("Isolde", 2), companion("Wystan", 9), companion("Thea", 3)]),
                  at(1380, 1498, [companion("Isolde", 2), companion("Wystan", 3), companion("Thea", 3)])])
    r = rally.Rally(s, lead)
    out = asyncio.run(r.before_hunt("goal:1", "Britain Graveyard", 1369, 1475, 15))
    assert s.trips == [("Britain Graveyard", 1369, 1475, 15 + rally.EDGE_TILES)]  # to the edge, not within 3 of the centre
    assert out["formed"] and out["point"] == "Roland" and out["with"] == ["Isolde", "Thea", "Wystan"] and not out["without"]
    assert out["sighted"] is None  # nothing in view: the walk to the centre goes on, together
    row = lead.rally("goal:1")
    assert row["state"] == "go" and (row["x"], row["y"]) == (1380, 1498) and r.formed == 1
    assert r.current() == (1380, 1498)
    w.close()


def test_the_leader_stops_at_the_first_sight_of_something_hostile(tmp_path, monkeypatch):
    """With a creature in view it doesn't walk on at all: the party forms up where it stands, and the hunt
    starts there. It doesn't wait for ever for one who isn't coming."""
    monkeypatch.setattr(rally, "WAIT_S", 0.6)
    w = World.open("local", root=tmp_path)
    report_all(w, {"Roland": (1392, 1528), "Isolde": (1394, 1528), "Corwin": (1394, 1540)})
    lead = party.Party(w, "p1", "Roland", "Roland", NAMES)
    shouted = []

    async def shout(_rpc, kind):
        shouted.append(kind)

    view = at(1392, 1518, [monster(0x300, "a lich", 24, 12), companion("Isolde", 2), companion("Corwin", 12)])
    s = Field(w, [view])
    r = rally.Rally(s, lead)
    r.shout = shout
    out = asyncio.run(r.before_hunt("goal:1", "Britain Graveyard", 1369, 1475, 15))
    assert s.trips == [] and out["formed"] and out["sighted"] == "a lich"
    assert out["with"] == ["Isolde"] and out["without"] == ["Corwin"] and 0.5 <= out["waited_s"] < 3
    assert lead.rally("goal:1")["sighted"] and shouted == ["go_in"]  # too short a wait to have called "form up"
    # A creature within sight of the road, a long way from the hunt, is the walk's own fight loop's: the trip goes on.
    far = Field(w, [at(1392, 1640, [monster(0x300, "a mongbat", 39, 9), companion("Isolde", 2)])])
    out = asyncio.run(rally.Rally(far, lead).before_hunt("goal:2", "Britain Graveyard", 1369, 1475, 15))
    assert far.trips == [("Britain Graveyard", 1369, 1475, 15 + rally.EDGE_TILES)] and out["sighted"] is None
    w.close()


def test_a_fight_that_starts_ends_the_wait(tmp_path):
    w = World.open("local", root=tmp_path)
    report_all(w, {"Roland": (1392, 1528), "Wystan": (1392, 1540)})
    lead = party.Party(w, "p1", "Roland", "Roland", NAMES)
    lich = {**monster(0x300, "a lich", 24, 8), "attacking_me": True}
    s = Field(w, [at(1392, 1528, [lich, companion("Wystan", 12)])])
    out = asyncio.run(rally.Rally(s, lead).before_hunt("goal:1", "Britain Graveyard", 1369, 1475, 15))
    assert out["formed"] and out["without"] == ["Wystan"] and out["waited_s"] < 1 and out["contact"]
    w.close()


def test_a_follower_closes_up_on_the_leader_and_waits_for_the_word(tmp_path, monkeypatch):
    monkeypatch.setattr(rally, "POINT_WAITS_S", 0.0)
    w = World.open("local", root=tmp_path)
    report_all(w, {"Roland": (1392, 1510), "Isolde": (1394, 1528)})
    lead = party.Party(w, "p1", "Roland", "Roland", NAMES)
    mine = party.Party(w, "p1", "Isolde", "Roland", NAMES)
    s = Field(w, [at(1394, 1528, [companion("Roland", 9, 0, -9)], name="Isolde")])
    r = rally.Rally(s, mine)

    async def run():
        joining = asyncio.get_running_loop().create_task(r.before_hunt("goal:1", "Britain Graveyard", 1369, 1475, 15))
        await asyncio.sleep(0.9)
        assert not joining.done()  # no word yet: it keeps beside the leader
        lead.rally_open("goal:1", 1392, 1510, point="Roland", sighted=True)
        lead.rally_go("goal:1")
        return await joining

    out = asyncio.run(run())
    assert out["formed"] and out["sighted"] and out["point"] == "Roland"
    follows = [p for v, p in s.acts if v == "follow"]
    assert len(follows) == 1 and follows[0]["target"] == 0x900 and follows[0]["pace"] == "run"
    assert s.acts[-1][0] == "halt" and s.trips == []  # it stops walking, and nothing else: a fight may be on
    w.close()


def test_a_follower_with_no_leader_to_form_on_goes_in_as_before(tmp_path):
    w = World.open("local", root=tmp_path)
    report_all(w, {"Isolde": (1394, 1528)})
    mine = party.Party(w, "p1", "Isolde", "Roland", NAMES)
    s = Field(w, [at(1394, 1528, name="Isolde")])
    out = asyncio.run(rally.Rally(s, mine).before_hunt("goal:1", "Britain Graveyard", 1369, 1475, 15))
    assert not out["formed"] and out["why"] == "no leader to form up on" and s.acts == []
    # ...and one whose leader's hunt was refused is told so, and doesn't wait.
    report_all(w, {"Roland": (1392, 1510)})
    party.Party(w, "p1", "Roland", "Roland", NAMES).rally_go("goal:2", "off")
    out = asyncio.run(rally.Rally(s, mine).before_hunt("goal:2", "Britain Graveyard", 1369, 1475, 15))
    assert not out["formed"] and "called off" in out["why"]
    w.close()


def test_a_hunt_formed_up_with_creatures_in_view_starts_where_it_stands(tmp_path):
    """The walk to the centre of the spawn took the first one in through the wave: with the party formed up
    and creatures in view, there is no such walk. Without a sighting the walk goes on as before."""
    w = World(tmp_path / "w.sqlite")
    w.add_place("graveyard", "Britain Graveyard", 1369, 1475, source="test")
    s = Session(rpc=None, world=w)
    walked = []

    class Formed:
        def __init__(self, out):
            self.out = out

        async def before_hunt(self, key, name, cx, cy, radius):
            walked.append(("formed", key, name))
            return self.out

        def off(self, key):
            walked.append(("off", key))

    async def walk(*a, **kw):
        walked.append("to the centre")
        return Result(False, "stopped here for the test")

    async def defended(work):
        return await work

    s.travel_to, s.defended = walk, defended
    s.rally = Formed({"formed": True, "sighted": True})
    r = asyncio.run(s.hunt("Britain Graveyard", 5, rally_key="goal:3"))
    assert walked == [("formed", "goal:3", "Britain Graveyard")] and r.summary == "no judge to fight with"
    walked.clear()
    s.rally = Formed({"formed": True, "sighted": None})
    r = asyncio.run(s.hunt("Britain Graveyard", 5, rally_key="goal:4"))
    assert walked == [("formed", "goal:4", "Britain Graveyard"), "to the centre"]
    # A hunt of its own (the leader down, a lone character) forms up for nothing.
    walked.clear()
    asyncio.run(s.hunt("Britain Graveyard", 5))
    assert walked == ["to the centre"]
    # A leader refused the place tells the others not to wait.
    import time as _time
    s.left_from.append((1369, 1475, _time.monotonic(), "Britain Graveyard", "2 coming at once"))
    s.leads_party = True
    walked.clear()
    r = asyncio.run(s.hunt("Britain Graveyard", 5, rally_key="goal:5"))
    assert not r.ok and walked == [("off", "goal:5")]
    w.close()


# ---------------------------------------------------------------- falling back to it

def scattered(hits=95):
    """The lich on the warrior with the others out of the 10 tiles that make a party."""
    snap = lich_fight(with_party=False)
    snap["player"].update(x=1380, y=1500, hits=hits)
    snap["mobiles"] += [companion("Isolde", 13, 0, 13), companion("Wystan", 14, 2, 14)]
    return snap


def leave(sit, mem, now=5.0):
    return policy.decide(sit, answers("leave", target="t1", leave_now=0.9, danger=0.8), mem, policy.PolicyConfig(), now=now)


def test_a_member_that_leaves_falls_back_to_the_rally_point():
    """The run goes to where the party formed up, a leg at a time, and isn't a leave for its hunt."""
    sit = state.build(scattered(), set(), [], bestiary=LICH, companions=COMPANIONS)
    sit.rally = (1380, 1520)  # 20 tiles south; the lich is 4 east of the warrior
    mem = policy.Memory()
    dec = leave(sit, mem)
    run = next(a for a in dec.actions if a["verb"] == "flee")
    assert dec.intent == "leave" and dec.regroup and mem.regrouping
    assert (run["x"], run["y"], run["spot"]) == (1380, 1512, "rally")  # one leg of 12 toward it
    assert dec.note.startswith("fall back to the rally point")
    # Without a rally point (a lone character, a party that didn't form up): the line away, and a leave.
    alone = state.build(scattered(), set(), [], bestiary=LICH, companions=COMPANIONS)
    dec = leave(alone, policy.Memory())
    assert dec.intent == "leave" and not dec.regroup and "x" not in next(a for a in dec.actions if a["verb"] == "flee")


def test_a_run_goes_to_the_rally_point_whatever_stands_on_the_way():
    """A first version kept the line away whenever a creature stood between the member and the rally point,
    which in a fight at the line is always: live, three members fell back north, west and north-east through
    the graveyard, away from each other. The client walks round what is in the way."""
    sit = state.build(scattered(), set(), [], bestiary=LICH, companions=COMPANIONS)
    sit.rally = (1400, 1500)  # east, past the lich 4 tiles east of the warrior
    dec = leave(sit, policy.Memory())
    run = next(a for a in dec.actions if a["verb"] == "flee")
    assert dec.regroup and (run["x"], run["y"], run["spot"]) == (1392, 1500, "rally")
    sit.rally = (1380, 1560)  # too far to run to: the line away, as alone
    assert policy.rally_run(sit) is None
    sit.rally = (1380, 1502)  # at it already
    assert policy.rally_run(sit) is None


def test_with_no_rally_point_in_reach_it_still_falls_back_while_the_others_fight():
    """The line away, as alone, but its hunt goes on: it is coming back."""
    sit = state.build(scattered(), set(), [], bestiary=LICH, companions=COMPANIONS)
    sit.rally, sit.party_fighting = (1380, 1560), 2
    mem = policy.Memory()
    dec = leave(sit, mem)
    assert dec.intent == "leave" and dec.regroup and mem.regrouping and dec.note.startswith("fall back, away from")
    assert "x" not in next(a for a in dec.actions if a["verb"] == "flee")
    assert mem.no_seek_until == mem.leaving_until  # no staying clear afterwards


def test_a_member_that_fell_back_goes_in_again_once_it_has_its_health_back():
    """Live, 2026-10-12: the warrior left the lich at 34%, was at 92% seven seconds later (his bandage, the
    mage's heal) and stood by at full health for the rest of the fight, his hunt over. While companions are
    still in the fight a member that fell back goes in again at REJOIN_HP."""
    mem = policy.Memory()
    hurt = scattered(hits=32)
    first = state.build(hurt, set(), [], bestiary=LICH, companions=COMPANIONS)
    first.rally, first.party_fighting = (1380, 1520), 3
    assert leave(first, mem, now=5.0).regroup
    # Still hurt, out of reach: it keeps clear, and its hunt doesn't hear of a leave.
    away = scattered(hits=50)
    away["player"].update(y=1512)
    away["mobiles"][0].update(distance=12, dx=4, dy=-12)
    sit = state.build(away, set(), [], bestiary=LICH, companions=COMPANIONS)
    sit.rally, sit.party_fighting = (1380, 1520), 3
    dec = policy.decide(sit, answers("fight", target="t1"), mem, policy.PolicyConfig(), now=9.0)
    assert dec.intent == "leave" and dec.regroup and not dec.leave_why
    # Healed, but with nobody beside it yet: on to the rally point, where the others fall back to.
    healed = scattered(hits=90)
    healed["player"].update(y=1512)
    healed["mobiles"][0].update(distance=12, dx=4, dy=-12)
    sit = state.build(healed, set(), [], bestiary=LICH, companions=COMPANIONS)
    sit.rally, sit.party_fighting = (1380, 1520), 3
    dec = policy.decide(sit, answers("seek", target="t1"), mem, policy.PolicyConfig(), now=12.0)
    assert dec.intent == "leave" and dec.regroup and mem.regrouping
    # Healed, and beside a companion: back in, free to go looking for the fight again, and staying a while.
    healed["mobiles"][1].update(distance=4, dx=0, dy=4)
    sit = state.build(healed, set(), [], bestiary=LICH, companions=COMPANIONS)
    sit.rally, sit.party_fighting = (1380, 1520), 3
    dec = policy.decide(sit, answers("seek", target="t1"), mem, policy.PolicyConfig(), now=13.0)
    assert dec.intent == "seek" and not mem.regrouping and mem.no_seek_until <= 13.0
    assert mem.no_leave_until == 13.0 + policy.STAND_S
    # ...so the reason it left for (a pack, Jev's leave) doesn't send it off again at the next decision,
    held = policy.decide(sit, answers("leave", target="t1", leave_now=0.9, danger=0.8), mem, policy.PolicyConfig(), now=14.0)
    assert held.intent != "leave" and not mem.regrouping
    # unless it is badly hurt.
    hurt_again = scattered(hits=30)
    hurt_again["player"].update(y=1512)
    hurt_again["mobiles"][1].update(distance=4, dx=0, dy=4)
    sit = state.build(hurt_again, set(), [], bestiary=LICH, companions=COMPANIONS)
    sit.rally, sit.party_fighting = (1380, 1520), 3
    again = policy.decide(sit, answers("leave", target="t1", leave_now=0.9, danger=0.8), mem, policy.PolicyConfig(), now=15.0)
    assert again.intent == "leave" and again.regroup
    # With nobody left in the fight and no rally point in reach, it is a leave for good, said once.
    mem = policy.Memory()
    assert leave(first, mem, now=5.0).regroup
    sit = state.build(scattered(hits=50), set(), [], bestiary=LICH, companions=COMPANIONS)
    sit.rally, sit.party_fighting = (1380, 1560), 0
    dec = policy.decide(sit, answers("fight", target="t1"), mem, policy.PolicyConfig(), now=9.0)
    assert dec.intent == "leave" and not dec.regroup and dec.leave_why and not mem.regrouping


def test_at_the_rally_with_the_party_the_run_is_over():
    """Back with two or more of the others, it stands with them; alone there, it is a leave for good, and its
    hunt hears why."""
    mem = policy.Memory()
    first = state.build(scattered(), set(), [], bestiary=LICH, companions=COMPANIONS)
    first.rally = (1380, 1520)
    assert leave(first, mem, now=5.0).regroup
    # On the way, still short of it: it keeps to the rally.
    going = scattered()
    going["player"].update(y=1510)
    going["mobiles"][0].update(distance=10, dx=4, dy=-10)
    sit = state.build(going, set(), [], bestiary=LICH, companions=COMPANIONS)
    sit.rally = (1380, 1520)
    dec = leave(sit, mem, now=8.0)
    run = next(a for a in dec.actions if a["verb"] == "flee")
    assert dec.intent == "leave" and dec.regroup and (run["x"], run["y"]) == (1380, 1520) and not dec.leave_why
    # At it, with the mage and the archer beside it: the leave is over, and it fights what has come after it
    # (and for a while only that, as after any leave).
    there = lich_fight()
    there["player"].update(x=1380, y=1519)
    there["mobiles"][0].update(distance=2, dx=2)
    sit = state.build(there, set(), [], bestiary=LICH, companions=COMPANIONS)
    sit.rally = (1380, 1520)
    dec = policy.decide(sit, answers("fight", target="t1"), mem, policy.PolicyConfig(), now=12.0)
    assert dec.intent == "fight" and not mem.regrouping and mem.leaving_until <= 12.0
    assert 12.0 < mem.no_seek_until <= 12.0 + policy.RALLY_STAND_S
    # At it with nobody there: a leave like any other from here, said once.
    mem = policy.Memory()
    assert leave(first, mem, now=5.0).regroup
    lonely = lich_fight(with_party=False)
    lonely["player"].update(x=1380, y=1519)
    sit = state.build(lonely, set(), [], bestiary=LICH, companions=COMPANIONS)
    sit.rally = (1380, 1520)
    dec = policy.decide(sit, answers("fight", target="t1"), mem, policy.PolicyConfig(), now=12.0)
    assert dec.intent == "leave" and not dec.regroup and dec.leave_why and not mem.regrouping
    again = policy.decide(sit, answers("fight", target="t1"), mem, policy.PolicyConfig(), now=13.0)
    assert again.intent == "leave" and not again.leave_why


def test_a_leave_from_the_rally_itself_is_a_leave():
    there = lich_fight(with_party=False)
    there["player"].update(x=1380, y=1519)
    sit = state.build(there, set(), [], bestiary=LICH, companions=COMPANIONS)
    sit.rally = (1380, 1520)
    dec = leave(sit, policy.Memory())
    assert dec.intent == "leave" and not dec.regroup and dec.leave_why


def test_the_fight_state_gets_the_rally_point_from_the_party(tmp_path):
    w = World.open("local", root=tmp_path)
    mine = party.Party(w, "p1", "Roland", "Roland", NAMES)
    mine.companions.update(COMPANIONS)
    link = mates.Mates(mine)
    snap = lich_fight()
    snap["agent"]["bandaging"] = False
    sit = state.build(snap, set(), [], bestiary=LICH, companions=mine.companions)
    link.before(sit)
    assert sit.rally is None  # a party that doesn't form up has none
    link.rally = rally.Rally(Field(w, [snap]), mine)
    mine.rally_open("goal:1", 1392, 1510)
    link.before(sit)
    assert sit.rally is None  # nobody has gone in from it yet
    mine.rally_go("goal:1")
    link.rally._current = (0.0, None)
    link.before(sit)
    assert sit.rally == (1392, 1510)
    w.close()


def test_the_leaders_words_are_callouts_not_talk(tmp_path):
    """"Form up" and "in we go" are said aloud in the leader's voice and kept as callouts."""
    w = World.open("local", root=tmp_path)
    mine = party.Party(w, "p1", "Roland", "Roland", NAMES)
    theirs = party.Party(w, "p1", "Isolde", "Roland", NAMES)
    link = mates.Mates(mine)
    s = Field(w, [at(1392, 1528)])
    line = asyncio.run(link.shout(s, "form_up"))
    assert line and s.said == [line] and theirs.calls(15)[0]["kind"] == "form_up"
    quiet = mates.Mates(mine, callouts=False)
    assert asyncio.run(quiet.shout(s, "go_in")) is None and len(s.said) == 1
    w.close()


# ---------------------------------------------------------------- in together, and out together

def idle_wave():
    """A skeleton and a bone knight 12 tiles off that haven't noticed anyone, seen by the mage beside her party."""
    snap = lich_fight(with_party=False)
    snap["player"]["name"] = "Isolde"
    snap["mobiles"] = [{**monster(0x310, "a skeleton", 50, 12), "war_mode": False},
                       {**monster(0x311, "a bone knight", 57, 13), "war_mode": False},
                       companion("Roland", 4), companion("Wystan", 2)]
    return snap


def test_the_others_go_for_what_the_point_goes_for():
    """Live, 2026-10-12: the word to go was given, the warrior went for a skeleton, and the other four rested
    for four seconds ("not seeking: Jev would attack none of these") until the creatures turned on him."""
    sit = state.build(idle_wave(), set(), [], bestiary=LICH, companions={"Roland": "warrior", "Wystan": "archer"})
    none = answers("seek", target="none")
    alone = policy.decide(sit, none, policy.Memory(), policy.PolicyConfig())
    assert alone.intent == "rest" and "would attack none of these" in alone.note
    sit = state.build(idle_wave(), set(), [], bestiary=LICH, companions={"Roland": "warrior", "Wystan": "archer"})
    sit.point_name, sit.point_aim = "Roland", 0x311
    dec = policy.decide(sit, none, policy.Memory(), policy.PolicyConfig())
    assert dec.intent == "seek" and dec.target.serial == 0x311 and dec.note.startswith("with Roland: seek a bone knight")
    # Its own pick still comes first, and a point going for something it can't see changes nothing.
    own = policy.decide(sit, answers("seek", target="t1"), policy.Memory(), policy.PolicyConfig())
    assert own.target.serial == 0x310 and not own.note.startswith("with")
    sit.point_aim = 0x999
    assert policy.decide(sit, none, policy.Memory(), policy.PolicyConfig()).intent == "rest"


def test_the_points_aim_reaches_the_others_through_the_party_store(tmp_path):
    w = World.open("local", root=tmp_path)
    roland = party.Party(w, "p1", "Roland", "Roland", NAMES)
    isolde = party.Party(w, "p1", "Isolde", "Roland", NAMES)
    isolde.companions.update({"Roland": "warrior", "Wystan": "archer"})
    roland.rally_open("goal:1", 1392, 1517, point="Roland")
    roland.rally_go("goal:1")
    # The warrior's decision went for the bone knight: his next fight row says so.
    his = mates.Mates(roland)
    his.rally = rally.Rally(Field(w, [at(1392, 1517)]), roland)
    seen = lich_fight(with_party=False)
    seen["agent"]["bandaging"] = False
    rsit = state.build(seen, set(), [], bestiary=LICH, companions=roland.companions)
    his.before(rsit)
    asyncio.run(his.after(Field(w, [seen]), rsit, policy.Decision("seek", 0.9, [], "", target=rsit.hostiles[0])))
    roland._fight_sent = ("", 0.0)  # the next look writes the row again
    his.before(rsit)
    assert roland.fights(60) == {} and isolde.fights(60)["Roland"]["aim"] == rsit.hostiles[0].serial
    hers = mates.Mates(isolde)
    hers.rally = rally.Rally(Field(w, [at(1394, 1519, name="Isolde")]), isolde)
    sit = state.build(idle_wave(), set(), [], bestiary=LICH, companions=isolde.companions)
    hers.before(sit)
    assert sit.point_name == "Roland" and sit.point_aim == rsit.hostiles[0].serial and sit.rally == (1392, 1517)
    # The point itself follows nobody.
    assert rsit.point_aim == 0
    w.close()


def test_a_hunts_stop_waits_while_the_party_is_still_fighting(tmp_path):
    """The leader's hunt is the party's: ended mid-fight by a routine call, the planner's next goal cut the
    other four off. A death doesn't wait, nor does a party that didn't form up, nor a lone character."""
    w = World.open("local", root=tmp_path)
    roland = party.Party(w, "p1", "Roland", "Roland", NAMES)
    isolde = party.Party(w, "p1", "Isolde", "Roland", NAMES)
    s = Session(rpc=None, world=w)
    held = []
    assert not s.party_fights_on("Jev: not worth staying here (0.21)", held)  # no party
    link = mates.Mates(roland)
    s.pcfg = policy.PolicyConfig(link=link)
    assert not s.party_fights_on("Jev: not worth staying here (0.21)", held)  # a party that doesn't form up
    link.rally = rally.Rally(Field(w, [at(1392, 1517)]), roland)
    assert not s.party_fights_on("Jev: not worth staying here (0.21)", held)  # nobody fighting
    isolde.fight({"hits": 60, "hits_max": 85, "dead": False, "attackers": [0x310], "target": 0x300, "pets": []})
    assert s.party_fights_on("Jev: not worth staying here (0.21)", held) and len(held) == 1
    assert not s.party_fights_on("died", held)
    held[0] -= Session.PARTY_FIGHT_S + 1
    assert not s.party_fights_on("Jev: not worth staying here (0.21)", held)  # not for ever
    w.close()


# ---------------------------------------------------------------- holding the line

def the_wave_ahead(skeleton_on_the_mage=True, knight=True):
    """The warrior a few steps ahead of the mage and the archer; a lich 8 tiles further on, a bone knight
    coming, and (with skeleton_on_the_mage) a skeleton beside the mage behind him."""
    snap = lich_fight(with_party=False, lord=False)
    snap["mobiles"] = [{**monster(0x300, "a lich", 24, 8), "dx": 0, "dy": -8}]
    if knight:
        snap["mobiles"].append({**monster(0x301, "a bone knight", 57, 5), "dx": 1, "dy": -5})
    if skeleton_on_the_mage:
        snap["mobiles"].append({**monster(0x302, "a skeleton", 50, 5), "dx": 1, "dy": 5})
    snap["mobiles"] += [companion("Isolde", 6, 0, 6), companion("Wystan", 7, 1, 7)]
    snap["agent"]["bandaging"] = False
    return snap


def lined_up(snap, rally_at=(1000, 1008), archetype=None):
    sit = state.build(snap, set(), [], bestiary=LICH, companions=COMPANIONS)
    sit.rally = rally_at
    sit.mates = [state.Mate("Isolde", 0x901, "mage", 100, 6), state.Mate("Wystan", 0x902, "archer", 100, 7)]
    return sit


def test_the_warrior_keeps_to_the_line():
    """Live, 2026-10-12: he went on to the lich at the back of the wave, stood among six of them 6 to 8 tiles
    ahead of the others, and was dead in thirteen seconds. He fights what is on him or beside a companion."""
    sit = lined_up(the_wave_ahead())
    assert [h.name for h in policy.on_the_line(sit)] == ["a skeleton"]
    # Told to take the lich (t1), he takes the skeleton beside the mage.
    dec = policy.decide(sit, answers("fight", target="t1"), policy.Memory(), policy.PolicyConfig())
    assert dec.intent == "fight" and dec.target.name == "a skeleton"
    # With nothing on the line yet, he holds it, and closes up on the others when he is too far ahead of them.
    sit = lined_up(the_wave_ahead(skeleton_on_the_mage=False))
    dec = policy.decide(sit, answers("fight", target="t1"), policy.Memory(), policy.PolicyConfig())
    assert dec.target is None and dec.note == policy.HOLDING
    back = next(a for a in dec.actions if a["verb"] == "walk_to")
    assert (back["x"], back["y"], back["reason"]) == (sit.player["x"], sit.player["y"] + 6, "line")
    seek = policy.decide(lined_up(the_wave_ahead(skeleton_on_the_mage=False)), answers("seek", target="t1"),
                         policy.Memory(), policy.PolicyConfig())
    assert seek.intent == "rest" and seek.note == policy.HOLDING and not seek.target
    # What comes at him is his: the bone knight, once it is on him.
    on_him = the_wave_ahead(skeleton_on_the_mage=False)
    on_him["mobiles"][1].update(distance=1, dx=1, dy=0, attacking_me=True)
    dec = policy.decide(lined_up(on_him), answers("fight", target="t1"), policy.Memory(), policy.PolicyConfig())
    assert dec.target.name.startswith("a bone knight")


def test_the_line_is_for_hand_to_hand_and_for_a_wave():
    # With only the spellcaster left there is no line to hold: he goes for it.
    alone_lich = lined_up(the_wave_ahead(skeleton_on_the_mage=False, knight=False))
    assert policy.on_the_line(alone_lich) is None
    dec = policy.decide(alone_lich, answers("seek", target="t1"), policy.Memory(), policy.PolicyConfig())
    assert dec.intent == "seek" and dec.target.name == "a lich"
    # A party that didn't form up, a lone character, and those who fight from where they stand have no line.
    no_rally = lined_up(the_wave_ahead(), rally_at=None)
    assert policy.on_the_line(no_rally) is None
    lone = lined_up(the_wave_ahead())
    lone.mates = []
    assert policy.on_the_line(lone) is None
    from conftest import mage_snapshot
    mage = mage_snapshot()
    mage["mobiles"] = the_wave_ahead()["mobiles"]
    msit = state.build(mage, set(), [], bestiary=LICH, companions=COMPANIONS)
    msit.rally, msit.mates = (1000, 1008), [state.Mate("Wystan", 0x902, "archer", 100, 7)]
    assert msit.is_mage and policy.on_the_line(msit) is None
    # A warrior beside the point doesn't follow it off the line either.
    sit = lined_up(the_wave_ahead(skeleton_on_the_mage=False))
    sit.point_name, sit.point_aim = "Gareth", 0x300
    assert policy.with_the_point(sit) is None


def test_falling_fast_with_three_on_it_a_member_falls_back_though_the_party_is_beside_it():
    """Held at 69%, 66% and 49% with the party beside him, the warrior was at 14% a second later."""
    snap = lich_fight()  # the lich lord 4 tiles off, three companions within 10
    snap["mobiles"] += [monster(0x310 + i, "a skeleton", 50, 1) for i in range(3)]
    sit = state.build(snap, set(), [], bestiary=LICH, companions=COMPANIONS)
    sit.rally, sit.party_fighting = (1000, 1010), 3
    held = policy.decide(sit, answers("leave", target="t1", leave_now=0.9, danger=0.8), policy.Memory(),
                         policy.PolicyConfig(), now=5.0)
    assert held.intent == "fight" and "held with the party" in held.note
    sit = state.build(snap, set(), [], bestiary=LICH, companions=COMPANIONS)
    sit.rally, sit.party_fighting = (1000, 1010), 3
    sit.note_health_drop(35)
    dec = policy.decide(sit, answers("leave", target="t1", leave_now=0.9, danger=0.8), policy.Memory(),
                        policy.PolicyConfig(), now=5.0)
    assert dec.intent == "leave" and dec.regroup


def test_the_point_seeks_in_legs_and_never_far_ahead_of_the_others():
    """It waits for them to come up, its aim still said, so that they do; and a walk begun earlier is stopped:
    live, his decisions said "waiting" while that walk carried him 13 tiles to the creature."""
    idle = the_wave_ahead(skeleton_on_the_mage=False, knight=False)  # the lich alone, 8 tiles on; the mage 6 behind
    idle["mobiles"][0]["war_mode"] = False
    idle["player"]["walking"] = True
    dec = policy.decide(lined_up(idle), answers("seek", target="t1"), policy.Memory(), policy.PolicyConfig())
    assert dec.intent == "seek" and dec.target.name == "a lich" and "waiting for the party to close up" in dec.note
    assert [a["verb"] for a in dec.actions] == ["halt"]
    idle["player"]["walking"] = False
    assert not policy.decide(lined_up(idle), answers("seek", target="t1"), policy.Memory(), policy.PolicyConfig()).actions
    # With the mage three tiles behind him he goes: a leg of three, to look again from there.
    idle["mobiles"][1].update(distance=3, dx=0, dy=3)
    sit = lined_up(idle)
    dec = policy.decide(sit, answers("seek", target="t1"), policy.Memory(), policy.PolicyConfig())
    walk = next(a for a in dec.actions if a["verb"] == "walk_to")
    assert (walk["x"], walk["y"], walk["distance"]) == (sit.player["x"], sit.player["y"] - policy.SEEK_LEG, 1)
    assert walk["leg"] and "target" not in walk  # a tile in the way of a leg isn't counted against the creature
    # A leg that found no path: the next walks go the whole way, as before, for a few seconds.
    mem = policy.Memory(leg_failed_at=99.0)
    walk = next(a for a in policy.decide(lined_up(idle), answers("seek", target="t1"), mem, policy.PolicyConfig(), now=100.0).actions
                if a["verb"] == "walk_to")
    assert walk["target"] == 0x300 and (walk["y"], walk["distance"]) == (sit.player["y"] - 8, 1)
    # The last few tiles in one walk, to weapon reach, when that leaves him within six of a companion.
    idle["mobiles"][0].update(distance=4, dx=0, dy=-4)
    idle["mobiles"][1].update(distance=2, dx=0, dy=2)
    sit = lined_up(idle)
    walk = next(a for a in policy.decide(sit, answers("seek", target="t1"), policy.Memory(), policy.PolicyConfig()).actions
                if a["verb"] == "walk_to")
    assert (walk["x"], walk["y"], walk["distance"], walk["target"]) == (sit.player["x"], sit.player["y"] - 4, 1, 0x300)
    # Behind the others (they are nearer the creature than he is), there is nothing to wait for.
    behind = the_wave_ahead(skeleton_on_the_mage=False, knight=False)
    behind["mobiles"][0].update(war_mode=False, distance=14, dx=0, dy=-14)
    behind["mobiles"][1].update(distance=8, dx=0, dy=-8)   # Isolde, 8 ahead of him and 6 from the lich
    dec = policy.decide(lined_up(behind), answers("seek", target="t1"), policy.Memory(), policy.PolicyConfig())
    assert [a for a in dec.actions if a["verb"] == "walk_to"]
    # One who fights from where it stands, and a lone warrior, walk as before.
    lone = lined_up(the_wave_ahead(skeleton_on_the_mage=False, knight=False))
    lone.mates = []
    lone.raw["mobiles"] = [m for m in lone.raw["mobiles"] if m.get("monster")]
    assert policy.seek_leg(lone, lone.hostiles[0]) is None


def test_holding_the_line_stops_a_walk_under_way():
    snap = the_wave_ahead(skeleton_on_the_mage=False)
    snap["player"]["walking"] = True
    snap["mobiles"][2].update(distance=3, dx=0, dy=3)  # the mage three behind: nothing to walk back for
    sit = lined_up(snap)
    sit.mates = [state.Mate("Isolde", 0x901, "mage", 100, 3), state.Mate("Wystan", 0x902, "archer", 100, 7)]
    dec = policy.decide(sit, answers("fight", target="t1"), policy.Memory(), policy.PolicyConfig())
    assert dec.note == policy.HOLDING and [a["verb"] for a in dec.actions] == ["halt"]


def test_at_the_rally_point_it_stays_while_the_others_fight_and_leaves_only_when_none_do():
    """Live: a tamer fell back to the rally point for a pack, and left it for good a second later, her hunt
    over, with four companions still fighting. And who is "still fighting" is each one's own fight row: two
    members running from the same creatures each took the other, being chased, to be fighting."""
    there = lich_fight(with_party=False)
    there["player"].update(x=1380, y=1519)
    sit = state.build(there, set(), [], bestiary=LICH, companions=COMPANIONS)
    sit.rally, sit.party_fighting = (1380, 1520), 2
    mem = policy.Memory()
    dec = leave(sit, mem)
    assert dec.intent != "leave" and "held with the party" in dec.note
    # Badly hurt there, it runs on, and its hunt still goes on while they fight.
    hurt = lich_fight(with_party=False)
    hurt["player"].update(x=1380, y=1519, hits=25)
    sit = state.build(hurt, set(), [], bestiary=LICH, companions=COMPANIONS)
    sit.rally, sit.party_fighting = (1380, 1520), 2
    dec = leave(sit, policy.Memory())
    assert dec.intent in ("leave", "flee") and (dec.regroup or dec.intent == "flee")
    # With nobody left in the fight, a leave from the rally point is a leave.
    sit = state.build(there, set(), [], bestiary=LICH, companions=COMPANIONS)
    sit.rally, sit.party_fighting = (1380, 1520), 0
    dec = leave(sit, policy.Memory())
    assert dec.intent == "leave" and not dec.regroup and dec.leave_why


def test_one_who_is_running_isnt_counted_as_still_fighting(tmp_path):
    w = World.open("local", root=tmp_path)
    roland = party.Party(w, "p1", "Roland", "Roland", NAMES)
    isolde = party.Party(w, "p1", "Isolde", "Roland", NAMES)
    thea = party.Party(w, "p1", "Thea", "Roland", NAMES)
    for pty in (roland, isolde, thea):
        pty.companions.update({n: c for n, c in {**COMPANIONS, "Roland": "warrior"}.items() if n != pty.me})
    roland.rally_open("goal:1", 1392, 1517, point="Roland")
    roland.rally_go("goal:1")
    # The mage is chased as she runs (attackers, and her last decision a leave); the tamer fights on.
    hers = mates.Mates(isolde, callouts=False)
    hers.rally = rally.Rally(Field(w, [at(1394, 1519, name="Isolde")]), isolde)
    chased = lich_fight(with_party=False)
    chased["player"]["name"] = "Isolde"
    chased["mobiles"][0]["attacking_me"] = True
    chased["agent"]["bandaging"] = False
    isit = state.build(chased, set(), [], bestiary=LICH, companions=isolde.companions)
    hers.before(isit)
    asyncio.run(hers.after(Field(w, [chased]), isit, policy.Decision("leave", 1.0, [], "leaving")))
    isolde._fight_sent = ("", 0.0)
    hers.before(isit)
    thea.fight({"hits": 80, "hits_max": 85, "dead": False, "attackers": [], "target": 0x300, "pets": []})
    his = mates.Mates(roland)
    his.rally = rally.Rally(Field(w, [at(1392, 1517)]), roland)
    seen = lich_fight(with_party=False)
    seen["agent"]["bandaging"] = False
    rsit = state.build(seen, set(), [], bestiary=LICH, companions=roland.companions)
    his.before(rsit)
    assert roland.fights(60)["Isolde"]["running"] and rsit.party_fighting == 1  # the tamer, not the running mage
    w.close()


# ---------------------------------------------------------------- with a planner each (the huddle)

def hunting(w, where, area="Britain Graveyard"):
    """Every member's own report, on its way to the same hunt."""
    for name, (x, y) in where.items():
        p = party.Party(w, "p1", name, "Roland", NAMES)
        p.report(at(x, y, name=name), {**COMPANIONS, "Roland": "warrior"}[name], hunting=area)


def test_with_a_planner_each_the_first_to_arrive_holds_the_rally(tmp_path, monkeypatch):
    """The huddle's planners set out half a minute apart (cuo-cym.5): the first at the edge waits for those
    whose own reports say they are on their way to the same hunt, and they close up on it."""
    monkeypatch.setattr(rally, "POINT_WAITS_S", 0.0)
    w = World.open("local", root=tmp_path)
    hunting(w, {"Isolde": (1394, 1516), "Roland": (1392, 1540), "Corwin": (1300, 1500)})
    party.Party(w, "p1", "Thea", "Roland", NAMES).report(at(1395, 1517, name="Thea"), "tamer")  # at the bank, say
    isolde = party.Party(w, "p1", "Isolde", "Roland", NAMES)
    roland = party.Party(w, "p1", "Roland", "Roland", NAMES)
    lich = monster(0x300, "a lich", 24, 12)
    # The mage is there first, with the lich in view; the warrior is 24 tiles behind, then beside her.
    hers = Field(w, [at(1394, 1516, [lich], name="Isolde"), at(1394, 1516, [lich], name="Isolde"),
                     at(1394, 1516, [lich, companion("Roland", 3)], name="Isolde")])
    his = Field(w, [at(1392, 1540, name="Roland"), at(1393, 1518, [companion("Isolde", 2)], name="Roland")])
    key = "hunt:britain graveyard"

    async def run():
        holding = asyncio.get_running_loop().create_task(
            rally.Rally(hers, isolde, plan="huddle").before_hunt(key, "Britain Graveyard", 1369, 1475, 15))
        await asyncio.sleep(0.3)
        assert isolde.rally(key)["host"] == "Isolde" and isolde.rally(key)["state"] == "gathering"
        joining = asyncio.get_running_loop().create_task(
            rally.Rally(his, roland, plan="huddle").before_hunt(key, "Britain Graveyard", 1369, 1475, 15))
        return await holding, await joining

    held, joined = asyncio.run(run())
    # She waited for the warrior, who is on the same hunt; not for the tamer beside her (not hunting) nor the bard (far).
    assert held["formed"] and held["held"] and held["with"] == ["Roland"] and held["sighted"] == "a lich"
    assert held["point"] == "Roland"  # the warrior goes first, whoever held the rally
    assert joined["formed"] and joined["sighted"] and joined["point"] == "Roland"
    assert his.trips == [("Britain Graveyard", 1369, 1475, 15 + rally.EDGE_TILES)]  # his own way to the edge, then to her
    w.close()


def test_the_first_there_gives_the_others_a_moment_to_say_they_have_set_out(tmp_path, monkeypatch):
    """Live (2026-10-13) five planners chose the same hunt within three seconds, the warrior reached the edge
    before anyone's five-second report said so, saw nobody on the way and went in alone. A member now says it
    has set out the moment its hunt begins, and the first there waits a few seconds to hear it."""
    monkeypatch.setattr(rally, "POINT_WAITS_S", 0.0)
    monkeypatch.setattr(rally, "SET_OUT_S", 2.0)
    w = World.open("local", root=tmp_path)
    roland = party.Party(w, "p1", "Roland", "Roland", NAMES)
    isolde = party.Party(w, "p1", "Isolde", "Roland", NAMES)
    roland.report(at(1392, 1517, name="Roland"), "warrior")   # their last reports: nobody hunting yet
    isolde.report(at(1394, 1530, name="Isolde"), "mage")
    skeleton = monster(0x301, "a skeleton", 50, 12)
    his = Field(w, [at(1392, 1517, [skeleton], name="Roland"), at(1392, 1517, [skeleton], name="Roland"),
                    at(1392, 1517, [skeleton], name="Roland"), at(1392, 1517, [skeleton, companion("Isolde", 2)], name="Roland")])
    key = "hunt:britain graveyard"

    async def run():
        holding = asyncio.get_running_loop().create_task(
            rally.Rally(his, roland, plan="huddle").before_hunt(key, "Britain Graveyard", 1369, 1475, 15))
        await asyncio.sleep(0.3)
        assert roland.statuses()["Roland"]["hunting"] == "Britain Graveyard"  # said at once, not at his next report
        assert roland.rally(key)["state"] == "gathering"                      # nobody on the way yet: he gives it a moment
        isolde.setting_out("Britain Graveyard")                               # her planner, a moment behind his
        return await holding

    held = asyncio.run(run())
    assert held["formed"] and held["with"] == ["Isolde"] and held["without"] == [] and held["waited_s"] < 2.0
    # With nobody setting out, the wait is that moment and no longer.
    w.db.execute("DELETE FROM party_rally")
    isolde.report(at(1394, 1530, name="Isolde"), "mage")
    his.snaps = [at(1392, 1517, [skeleton], name="Roland")]
    alone = asyncio.run(rally.Rally(his, roland, plan="huddle").before_hunt(key, "Britain Graveyard", 1369, 1475, 15))
    assert alone["formed"] and alone["with"] == [] and 1.9 <= alone["waited_s"] < 3.0
    w.close()


def test_a_rally_that_went_a_while_ago_is_held_again_unless_the_party_is_still_fighting_from_it(tmp_path, monkeypatch):
    monkeypatch.setattr(rally, "POINT_WAITS_S", 0.0)
    monkeypatch.setattr(rally, "WAIT_S", 0.2)
    w = World.open("local", root=tmp_path)
    isolde = party.Party(w, "p1", "Isolde", "Roland", NAMES)
    wystan = party.Party(w, "p1", "Wystan", "Roland", NAMES)
    key = "hunt:britain graveyard"
    row = isolde.rally_claim(key, 1394, 1516)
    assert row["host"] == "Isolde" and row["state"] == "gathering"
    assert wystan.rally_claim(key, 1390, 1520)["host"] == "Isolde"  # still gathering: hers
    isolde.rally_go(key)
    assert wystan.rally_claim(key, 1390, 1520)["state"] == "go"     # just went: he joins it
    w.db.execute("UPDATE party_rally SET go_t = go_t - 60")
    w.db.commit()
    # A minute on, with the mage still fighting from it: the archer, arriving late, joins her. The rally point
    # the party falls back to isn't taken from under it, and nobody waits at the edge for those already inside.
    hunting(w, {"Isolde": (1394, 1512), "Wystan": (1390, 1520)})
    isolde.fight({"hits": 70, "hits_max": 85, "dead": False, "attackers": [0x310], "target": 0x300, "pets": []})
    his = Field(w, [at(1390, 1520, [companion("Isolde", 8)], name="Wystan")])
    out = asyncio.run(rally.Rally(his, wystan, plan="huddle").before_hunt(key, "Britain Graveyard", 1369, 1475, 15))
    assert out["formed"] and "held" not in out and wystan.rally(key)["host"] == "Isolde" and wystan.rally()["x"] == 1394
    # Between two fights, with the mage still on that hunt: he joins it all the same. Live, each member's next
    # hunt of the place, a minute on, held a new rally where it stood and waited there for those already inside.
    w.db.execute("DELETE FROM party_fight")
    w.db.commit()
    out = asyncio.run(rally.Rally(his, wystan, plan="huddle").before_hunt(key, "Britain Graveyard", 1369, 1475, 15))
    assert out["formed"] and "held" not in out and wystan.rally(key)["host"] == "Isolde"
    # With nobody on that hunt any more, the place's next hunt is held anew by the first there. He is inside the
    # area by then, so the place to fall back to stays the one the party went in from.
    isolde.report(at(1394, 1512, name="Isolde"), "mage")
    monkeypatch.setattr(rally, "SET_OUT_S", 0.1)
    inside = Field(w, [at(1380, 1490, name="Wystan")])
    out = asyncio.run(rally.Rally(inside, wystan, plan="huddle").before_hunt(key, "Britain Graveyard", 1369, 1475, 15))
    assert out["formed"] and out["held"] and wystan.rally(key)["host"] == "Wystan"
    assert (wystan.rally()["x"], wystan.rally()["y"]) == (1394, 1516) and out["at"] == [1394, 1516]
    w.close()


def test_one_the_party_went_in_without_goes_to_where_they_went_in_from(tmp_path, monkeypatch):
    """Given the word with creatures in view, a member 18 tiles off began its hunt where it stood, with nothing
    in view, and stood idle until its next patrol."""
    monkeypatch.setattr(rally, "POINT_WAITS_S", 0.0)
    w = World.open("local", root=tmp_path)
    report_all(w, {"Roland": (1392, 1510), "Corwin": (1394, 1540)})
    lead = party.Party(w, "p1", "Roland", "Roland", NAMES)
    mine = party.Party(w, "p1", "Corwin", "Roland", NAMES)
    lead.rally_open("goal:1", 1392, 1510, point="Roland", sighted=True)
    lead.rally_go("goal:1")
    s = Field(w, [at(1394, 1540, name="Corwin")])
    out = asyncio.run(rally.Rally(s, mine).before_hunt("goal:1", "Britain Graveyard", 1369, 1475, 15))
    assert out["formed"] and out["caught_up"] and s.trips == [(None, 1392, 1510, 3)]
    # One already beside the others goes in from where it is.
    near = Field(w, [at(1394, 1516, [companion("Roland", 6)], name="Corwin")])
    out = asyncio.run(rally.Rally(near, mine).before_hunt("goal:1", "Britain Graveyard", 1369, 1475, 15))
    assert out["formed"] and "caught_up" not in out and near.trips == []
    w.close()


def test_a_leader_already_inside_the_area_keeps_the_rally_point_it_went_in_from(tmp_path, monkeypatch):
    """Live: the third and fifth hunts of a run rallied three tiles from the spawn's centre."""
    monkeypatch.setattr(rally, "POINT_WAITS_S", 0.0)
    w = World.open("local", root=tmp_path)
    report_all(w, {"Roland": (1366, 1478), "Isolde": (1367, 1479)})
    lead = party.Party(w, "p1", "Roland", "Roland", NAMES)
    lead.rally_open("goal:1", 1393, 1516, point="Roland", sighted=True)
    lead.rally_go("goal:1")
    s = Field(w, [at(1366, 1478, [companion("Isolde", 1)])])
    out = asyncio.run(rally.Rally(s, lead).before_hunt("goal:3", "Britain Graveyard", 1369, 1475, 15))
    assert out["formed"] and out["at"] == [1393, 1516] and out["with"] == ["Isolde"]
    assert (lead.rally("goal:3")["x"], lead.rally("goal:3")["y"]) == (1393, 1516) and s.trips == []
    w.close()


def test_nobody_stands_in_front_of_the_point():
    """Live: with the warrior holding the line at the rally point, the tamer, the mage and the archer walked
    forward to their own reach of the lich and were the party's front; the tamer was at 45% in eight seconds."""
    from conftest import mage_snapshot

    def mage_behind(lich_at, roland_at):
        snap = mage_snapshot()
        snap["player"]["name"] = "Isolde"
        snap["mobiles"] = [{**monster(0x300, "a lich", 24, max(abs(lich_at[0]), abs(lich_at[1]))), "dx": lich_at[0], "dy": lich_at[1]},
                           companion("Roland", max(abs(roland_at[0]), abs(roland_at[1])), *roland_at)]
        snap["agent"].update(engaged=0)
        sit = state.build(snap, set(), [], bestiary=LICH, companions={"Roland": "warrior", "Wystan": "archer"})
        sit.rally, sit.point_name, sit.point_serial = (1000, 1000), "Roland", 0x900
        sit.mates = [state.Mate("Roland", 0x900, "warrior", 100, max(abs(roland_at[0]), abs(roland_at[1])))]
        return sit

    cfg = policy.PolicyConfig()
    # The lich 13 tiles on, the warrior beside her holding: out of reach from behind him, so she waits.
    sit = mage_behind((0, -13), (1, 0))
    assert policy.behind_the_point(sit, sit.hostiles[0]) == 14
    for intent in ("fight", "seek"):
        dec = policy.decide(mage_behind((0, -13), (1, 0)), answers(intent, target="t1"), policy.Memory(), cfg)
        assert not [a for a in dec.actions if a["verb"] in ("attack", "walk_to")] and "keeping behind Roland" in dec.note
    # The lich 9 tiles on: within a spell's reach from where she stands, so she casts from there, engaged at
    # that range and not at her usual seven, which would walk her two tiles in front of him.
    dec = policy.decide(mage_behind((0, -9), (1, 0)), answers("fight", target="t1"), policy.Memory(), cfg)
    attack = next(a for a in dec.actions if a["verb"] == "attack")
    assert attack["range"] == 10 and "keeping behind" not in dec.note
    # The warrior on the lich: she closes to her usual range, behind him.
    dec = policy.decide(mage_behind((0, -9), (0, -8)), answers("fight", target="t1"), policy.Memory(), cfg)
    assert next(a for a in dec.actions if a["verb"] == "attack")["range"] == cfg.spell_range
    # With no point that takes blows (it fell, it is running, or the party has none), as before.
    free = mage_behind((0, -13), (1, 0))
    free.point_serial = 0
    assert policy.behind_the_point(free, free.hostiles[0]) is None
    dec = policy.decide(free, answers("seek", target="t1"), policy.Memory(), cfg)
    assert [a for a in dec.actions if a["verb"] == "walk_to"]


def test_an_archer_behind_the_point_shoots_from_as_far_as_its_bow_carries():
    """It shoots from 8 tiles by choice, and from behind the point from the bow's own 10. Live, the test for
    "out of reach" used the 8: in 17 of the archer's 20 such decisions the creature was in shooting range."""
    def archer_behind(lich_at, roland_at):
        snap = copy.deepcopy(SNAPSHOT)
        snap["player"].update(name="Wystan", weapon="a bow", ranged={"kind": "bow", "ammo": "arrows", "range": 10},
                              skills={"Archery": 80.0, "Tactics": 80.0})
        snap["player"]["supplies"].update({"arrows": 120, "bolts": 0})
        snap["mobiles"] = [{**monster(0x300, "a lich", 24, max(abs(lich_at[0]), abs(lich_at[1]))), "dx": lich_at[0], "dy": lich_at[1]},
                           companion("Roland", max(abs(roland_at[0]), abs(roland_at[1])), *roland_at)]
        snap["agent"].update(engaged=0)
        sit = state.build(snap, set(), [], bestiary=LICH, companions={"Roland": "warrior", "Isolde": "mage"})
        assert sit.is_archer
        sit.rally, sit.point_name, sit.point_serial = (1000, 1000), "Roland", 0x900
        sit.mates = [state.Mate("Roland", 0x900, "warrior", 100, max(abs(roland_at[0]), abs(roland_at[1])))]
        return sit

    cfg = policy.PolicyConfig()
    # The lich 10 tiles on, the warrior beside him: in the bow's reach from where he stands, so he shoots from there.
    dec = policy.decide(archer_behind((0, -10), (1, 0)), answers("fight", target="t1"), policy.Memory(), cfg)
    assert next(a for a in dec.actions if a["verb"] == "attack")["range"] == 10 and "keeping behind" not in dec.note
    # At 12, out of it: he waits behind the warrior.
    dec = policy.decide(archer_behind((0, -12), (1, 0)), answers("fight", target="t1"), policy.Memory(), cfg)
    assert not [a for a in dec.actions if a["verb"] in ("attack", "walk_to")] and "keeping behind Roland" in dec.note


def test_a_tamer_keeps_its_pet_and_itself_behind_the_point():
    """Live: seven decisions said "keeping behind Roland: a lich is out of reach" and sent the pet at the lich all
    the same, and with the pet out there "back to the pet" was a walk to the bone knight."""
    from test_tamer import BEAR

    def tamer_behind(lich_at, roland_at, pet_at=(1, 1), on_her=False):
        snap = copy.deepcopy(SNAPSHOT)
        snap["player"].update(name="Thea", weapon="a shepherd's crook",
                              skills={"Animal Taming": 90.0, "Animal Lore": 90.0, "Veterinary": 80.0})
        d = lambda xy: max(abs(xy[0]), abs(xy[1]))  # noqa: E731
        snap["mobiles"] = [{**monster(0x300, "a lich", 24, d(lich_at)), "dx": lich_at[0], "dy": lich_at[1],
                            "attacking_me": on_her},
                           companion("Roland", d(roland_at), *roland_at)]
        snap["pets"] = [{"serial": BEAR, "name": "a grizzly bear", "body": 212, "hits_pct": 100, "poisoned": False,
                         "distance": d(pet_at), "dx": pet_at[0], "dy": pet_at[1]}]
        snap["agent"].update(engaged=0, pet_target=0)
        sit = state.build(snap, set(), [], bestiary=LICH, companions={"Roland": "warrior", "Isolde": "mage"})
        assert sit.is_tamer
        sit.rally, sit.point_name, sit.point_serial = (1000, 1000), "Roland", 0x900
        sit.mates = [state.Mate("Roland", 0x900, "warrior", 100, d(roland_at))]
        return sit

    cfg = policy.PolicyConfig()
    # The lich 13 tiles on, the warrior beside her: the pet isn't sent ahead of him.
    dec = policy.decide(tamer_behind((0, -13), (1, 0)), answers("fight", target="t1"), policy.Memory(), cfg)
    assert "keeping behind Roland" in dec.note and not [a for a in dec.actions if a["verb"] in ("pet", "walk_to")]
    # ...unless the lich is on her: what is on the tamer comes first, wherever the point stands.
    dec = policy.decide(tamer_behind((0, -13), (1, 0), on_her=True), answers("fight", target="t1"), policy.Memory(), cfg)
    assert [a["kind"] for a in dec.actions if a["verb"] == "pet"] == ["kill"]
    # The warrior on the lich, 8 tiles from her: the pet goes in.
    dec = policy.decide(tamer_behind((0, -8), (0, -7)), answers("fight", target="t1"), policy.Memory(), cfg)
    assert [a["kind"] for a in dec.actions if a["verb"] == "pet"] == ["kill"]
    # Her pet is out at the lich, 9 tiles on, and the warrior beside her: she doesn't walk out to it.
    far = tamer_behind((0, -10), (1, 0), pet_at=(0, -9))
    assert policy.pet_ahead_of_the_point(far, policy.Decision("fight", 1.0, [], "", [], target=far.hostiles[0]), far.pet)
    dec = policy.decide(far, answers("fight", target="t1"), policy.Memory(), cfg)
    assert not [a for a in dec.actions if a["verb"] == "walk_to"] and "back to the pet" not in dec.note
    # With the warrior out there too, she goes to it as before.
    near = tamer_behind((0, -10), (0, -9), pet_at=(0, -9))
    assert not policy.pet_ahead_of_the_point(near, policy.Decision("fight", 1.0, [], "", [], target=near.hostiles[0]), near.pet)


def test_at_the_rally_point_a_member_that_fell_back_stands_there():
    """Live: an archer at 37% fell back to the rally point, at the next decision ran 15 tiles on, away from all
    five in sight (from the rally point there is no way "to the rally"), and at the one after turned round."""
    snap = scattered(hits=30)
    snap["mobiles"][0].update(distance=6, dx=6, dy=0, attacking_me=False)  # the lich, off him now
    sit = state.build(snap, set(), [], bestiary=LICH, companions=COMPANIONS)
    sit.rally, sit.party_fighting = (1380, 1501), 2  # he is on it
    mem = policy.Memory()
    dec = leave(sit, mem)
    assert dec.intent == "leave" and dec.regroup and mem.regrouping and "standing at the rally point" in dec.note
    assert not [a for a in dec.actions if a["verb"] == "flee"]
    # The decisions after it, while the run's time lasts: the same.
    dec = leave(sit, mem, now=6.5)
    assert dec.intent == "leave" and dec.regroup and not [a for a in dec.actions if a["verb"] == "flee"]
    # With the lich on him there, he runs.
    snap["mobiles"][0].update(distance=1, dx=1, dy=0, attacking_me=True)
    hit = state.build(snap, set(), [], bestiary=LICH, companions=COMPANIONS)
    hit.rally, hit.party_fighting = (1380, 1501), 2
    assert [a for a in leave(hit, policy.Memory()).actions if a["verb"] == "flee"]


def test_the_first_back_at_the_rally_point_waits_for_those_falling_back_behind_it():
    """The last of them fall back together. The first to reach the rally point has nobody beside it yet and
    nobody "fighting" (one who is running isn't): its fall back was a leave for good, its hunt over, with the
    others seconds behind."""
    mem = policy.Memory()
    first = state.build(scattered(hits=32), set(), [], bestiary=LICH, companions=COMPANIONS)
    first.rally, first.party_fighting = (1380, 1520), 2
    assert leave(first, mem, now=5.0).regroup
    there = scattered(hits=40)
    there["player"].update(y=1519)   # at the rally point, the lich 18 tiles behind
    there["mobiles"][0].update(distance=18, dx=4, dy=-18)
    sit = state.build(there, set(), [], bestiary=LICH, companions=COMPANIONS)
    sit.rally, sit.party_fighting, sit.party_running = (1380, 1520), 0, 2
    dec = policy.decide(sit, answers("rest"), mem, policy.PolicyConfig(), now=12.0)
    assert dec.intent == "leave" and dec.regroup and mem.regrouping and not dec.leave_why
    # With nobody coming either, it is a leave like any other.
    sit = state.build(there, set(), [], bestiary=LICH, companions=COMPANIONS)
    sit.rally, sit.party_fighting, sit.party_running = (1380, 1520), 0, 0
    dec = policy.decide(sit, answers("rest"), mem, policy.PolicyConfig(), now=13.0)
    assert dec.intent == "leave" and not dec.regroup and dec.leave_why and not mem.regrouping


# ---------------------------------------------------------------- to a companion that is set upon (cuo-cym.10)

def after_the_fight(knight_at=(13, 0), thea_at=(14, 0), hits=95, name="Roland", **player):
    """Their own bone knight dead at their feet, another one out by the tamer."""
    snap = copy.deepcopy(SNAPSHOT)
    snap["player"].update(name=name, hits=hits, hits_max=95, **player)
    d = lambda xy: max(abs(xy[0]), abs(xy[1]))  # noqa: E731
    snap["mobiles"] = [{**monster(0x301, "a bone knight", 57, d(knight_at)), "dx": knight_at[0], "dy": knight_at[1]},
                       companion("Thea", d(thea_at), *thea_at), companion("Isolde", 2, 0, 2)]
    snap["agent"].update(engaged=0, bandaging=False)
    return snap


def test_one_with_nothing_to_fight_goes_to_a_companion_that_is_set_upon():
    """Live: a tamer ran from a bone knight three times in nine seconds, 15 tiles from the others and in view of
    all four, who had killed their own and looted. She died."""
    cfg, loot = policy.PolicyConfig(), answers("loot")
    sit = state.build(after_the_fight(), set(), [], bestiary=LICH, companions=COMPANIONS)
    assert sit.corpses and policy.decide(sit, loot, policy.Memory(), cfg).intent == "loot"  # no party formed up: as before
    sit.rally = (1000, 1010)
    sit.mates = [state.Mate("Thea", 0x903, "tamer", 61, 14), state.Mate("Isolde", 0x901, "mage", 100, 2)]
    dec = policy.decide(sit, loot, policy.Memory(), cfg)
    walk = next(a for a in dec.actions if a["verb"] == "walk_to")
    # The warrior goes all the way, not a leg at a time: a companion stands where he is going.
    assert dec.intent == "seek" and dec.target.serial == 0x301 and dec.note.startswith("to Thea: seek a bone knight")
    assert walk["target"] == 0x301 and "leg" not in walk
    assert policy.decide(sit, answers("rest"), policy.Memory(), cfg).intent == "seek"
    # Not one who is hurt itself, nor one that fell back and stands at the rally point.
    hurt = state.build(after_the_fight(hits=40), set(), [], bestiary=LICH, companions=COMPANIONS)
    hurt.rally, hurt.mates = sit.rally, sit.mates
    assert policy.decide(hurt, loot, policy.Memory(), cfg).intent == "loot"
    assert policy.decide(sit, loot, policy.Memory(no_seek_until=50.0), cfg, now=10.0).intent == "loot"
    # A bone knight that is nobody's business yet: 8 tiles from her, and her row doesn't name it.
    idle = state.build(after_the_fight(knight_at=(13, 8)), set(), [], bestiary=LICH, companions=COMPANIONS)
    idle.rally, idle.mates = sit.rally, sit.mates
    assert policy.on_a_companion(idle) is None and policy.decide(idle, loot, policy.Memory(), cfg).intent == "loot"
    # ...until her own fight row says it is on her.
    idle.mates = [state.Mate("Thea", 0x903, "tamer", 61, 14, attackers=[0x301]), sit.mates[1]]
    assert policy.decide(idle, loot, policy.Memory(), cfg).intent == "seek"


def test_a_mage_short_of_mana_keeps_her_rest(mage):
    """Live, the first run with the rule: at 2% to 10% of her mana the mage's rest was turned into a fight at
    every decision for 40 seconds, eleven Magic Arrows and seven decisions that cast nothing."""
    def with_mana(mana):
        snap = copy.deepcopy(mage)
        snap["player"].update(name="Isolde", mana=mana, hits=85)
        snap["mobiles"] = [monster(0x301, "a bone knight", 57, 7), companion("Thea", 8, 8, 0)]
        snap["agent"].update(engaged=0, bandaging=False)
        sit = state.build(snap, set(), [], bestiary=LICH, companions={"Thea": "tamer", "Roland": "warrior"})
        assert sit.is_mage and sit.mana_pct == mana
        sit.rally = (1000, 1010)
        sit.mates = [state.Mate("Thea", 0x903, "tamer", 61, 8)]
        return sit

    from test_mage import answers as mage_answers
    cfg = policy.PolicyConfig()
    assert policy.decide(with_mana(8), mage_answers("rest"), policy.Memory(), cfg).intent == "rest"
    dec = policy.decide(with_mana(60), mage_answers("rest"), policy.Memory(), cfg)
    assert dec.intent == "fight" and dec.target.serial == 0x301 and dec.note.startswith("to Thea: ")


def test_one_that_fights_from_where_it_stands_shoots_what_is_on_a_companion():
    snap = after_the_fight(knight_at=(7, 0), thea_at=(8, 0), name="Wystan", weapon="a bow",
                           ranged={"kind": "bow", "ammo": "arrows", "range": 10},
                           skills={"Archery": 90.0, "Tactics": 90.0})
    snap["player"]["supplies"].update({"arrows": 100, "bolts": 0})
    snap["mobiles"] = [m for m in snap["mobiles"] if m["name"] != "Isolde"]
    sit = state.build(snap, set(), [], bestiary=LICH, companions={"Thea": "tamer", "Roland": "warrior"})
    assert sit.is_archer
    sit.rally = (1000, 1010)
    sit.mates = [state.Mate("Thea", 0x903, "tamer", 61, 8)]
    dec = policy.decide(sit, answers("loot"), policy.Memory(), policy.PolicyConfig())
    # In bow shot from where he stands: he shoots it, and doesn't walk.
    assert dec.intent == "fight" and dec.target.serial == 0x301 and dec.note.startswith("to Thea: ")
    assert [a["verb"] for a in dec.actions if a["verb"] in ("attack", "walk_to")] == ["attack"]
    # Out of bow shot: he goes nearer, as far as his bow needs.
    snap = after_the_fight(knight_at=(15, 0), thea_at=(16, 0), name="Wystan", weapon="a bow",
                           ranged={"kind": "bow", "ammo": "arrows", "range": 10},
                           skills={"Archery": 90.0, "Tactics": 90.0})
    snap["player"]["supplies"].update({"arrows": 100, "bolts": 0})
    sit = state.build(snap, set(), [], bestiary=LICH, companions={"Thea": "tamer", "Roland": "warrior", "Isolde": "mage"})
    sit.rally = (1000, 1010)
    sit.mates = [state.Mate("Thea", 0x903, "tamer", 61, 16), state.Mate("Isolde", 0x901, "mage", 100, 2)]
    dec = policy.decide(sit, answers("loot"), policy.Memory(), policy.PolicyConfig())
    walk = next(a for a in dec.actions if a["verb"] == "walk_to")
    assert dec.intent == "seek" and walk["target"] == 0x301 and walk["distance"] > 1


def test_a_companion_set_upon_out_of_view_is_walked_to(tmp_path):
    cfg = policy.PolicyConfig()
    snap = after_the_fight()
    snap["mobiles"] = [m for m in snap["mobiles"] if m["name"] == "Isolde"]  # the tamer and her bone knight out of view
    sit = state.build(snap, set(), [], bestiary=LICH, companions=COMPANIONS)
    sit.rally = (1000, 1010)
    sit.mates = [state.Mate("Isolde", 0x901, "mage", 100, 2)]
    sit.mate_far = ("Thea", 1030, 1000)
    dec = policy.decide(sit, answers("loot"), policy.Memory(), cfg)
    walk = next(a for a in dec.actions if a["verb"] == "walk_to")
    assert dec.intent == "seek" and dec.note == "to Thea: set upon out of view" and (walk["x"], walk["y"]) == (1012, 1000)
    sit.mate_far = ("Thea", 1060, 1000)  # too far to go to
    assert policy.decide(sit, answers("loot"), policy.Memory(), cfg).intent == "loot"
    # Not while something in war mode is in view here: live, the mage and the bard walked off after a tamer
    # who was out of view, away from the bone knight the archer beside them was shooting.
    here = after_the_fight(knight_at=(13, 8))
    here["mobiles"] = [m for m in here["mobiles"] if m["name"] != "Thea"]
    busy = state.build(here, set(), [], bestiary=LICH, companions=COMPANIONS)
    busy.rally, busy.mates, busy.mate_far = sit.rally, sit.mates, ("Thea", 1030, 1000)
    assert policy.decide(busy, answers("rest"), policy.Memory(), cfg).intent == "rest"

    # Where she is comes from her own reports: her fight row says something is on her, her status where.
    w = World.open("local", root=tmp_path)
    roland = party.Party(w, "p1", "Roland", "Roland", NAMES)
    thea = party.Party(w, "p1", "Thea", "Roland", NAMES)
    isolde = party.Party(w, "p1", "Isolde", "Roland", NAMES)
    roland.companions.update(COMPANIONS)
    roland.rally_open("goal:1", 1000, 1010, point="Roland")
    roland.rally_go("goal:1")
    thea.report(at(1030, 1000, name="Thea"), "tamer")
    thea.fight({"hits": 50, "hits_max": 85, "dead": False, "attackers": [0x301], "target": 0, "pets": []})
    isolde.report(at(1020, 1000, name="Isolde"), "mage")  # nearer, and running, but nothing is on her
    isolde.fight({"hits": 80, "hits_max": 80, "dead": False, "attackers": [], "target": 0, "pets": [], "running": True})
    his = mates.Mates(roland, callouts=False)
    his.rally = rally.Rally(Field(w, [at(1000, 1000)]), roland)
    alone = after_the_fight()
    alone["mobiles"] = []
    rsit = state.build(alone, set(), [], bestiary=LICH, companions=roland.companions)
    his.before(rsit)
    assert rsit.mate_far == ("Thea", 1030, 1000)
    w.close()


def test_a_tamer_tending_a_hurt_pet_keeps_at_it():
    """Live: a tamer bandaging a pet at 2%, called back, was walked to within a few steps of the lich that was on
    the warrior, with nothing to send at it."""
    from test_tamer import BEAR

    def tamer(pet_pct):
        snap = after_the_fight(knight_at=(12, 0), thea_at=(0, 2), name="Thea", weapon="a shepherd's crook",
                               skills={"Animal Taming": 90.0, "Animal Lore": 90.0, "Veterinary": 80.0})
        snap["mobiles"] = [m for m in snap["mobiles"] if m.get("monster")] + [companion("Roland", 13, 13, 0)]
        snap["pets"] = [{"serial": BEAR, "name": "a grizzly bear", "body": 212, "hits_pct": pet_pct, "poisoned": False,
                         "distance": 1, "dx": 1, "dy": 1}]
        snap["agent"].update(pet_target=0, pet_order="follow")
        sit = state.build(snap, set(), [], bestiary=LICH, companions={"Roland": "warrior", "Isolde": "mage"})
        assert sit.is_tamer and sit.pet_pct == pet_pct
        sit.rally = (1000, 1010)
        sit.mates = [state.Mate("Roland", 0x900, "warrior", 70, 13)]
        return sit

    cfg = policy.PolicyConfig()
    assert policy.decide(tamer(20), answers("rest"), policy.Memory(), cfg, now=50.0).intent == "rest"
    # Her pet whole, she goes; unless she has just called it back.
    assert policy.decide(tamer(95), answers("rest"), policy.Memory(), cfg, now=50.0).note.startswith("to Roland: ")
    assert policy.decide(tamer(95), answers("rest"), policy.Memory(pulling_until=60.0), cfg, now=50.0).intent == "rest"


def test_one_with_nothing_to_fight_with_isnt_held_where_something_is_on_it():
    """Live: a tamer whose pet was gone stood at the rally point, "held with the party: 4 companions beside it",
    for eight seconds with a bone knight beside her, from 100% of her health to 22%, and died."""
    def tamer(knight_at):
        snap = after_the_fight(knight_at=knight_at, thea_at=(0, 3), name="Thea", weapon="a shepherd's crook",
                               skills={"Animal Taming": 90.0, "Animal Lore": 90.0, "Veterinary": 80.0})
        snap["mobiles"] = [m for m in snap["mobiles"] if m.get("monster")] + \
            [companion("Roland", 3, 3, 0), companion("Isolde", 2, 0, 2), companion("Corwin", 3, -3, 0)]
        snap["mobiles"][0]["attacking_me"] = max(abs(knight_at[0]), abs(knight_at[1])) <= 1
        snap["pets"] = []
        sit = state.build(snap, set(), [], bestiary=LICH, companions={"Roland": "warrior", "Isolde": "mage", "Corwin": "bard"})
        assert sit.is_tamer and not sit.pet
        sit.rally, sit.party_fighting = (1000, 1000), 3
        sit.mates = [state.Mate("Roland", 0x900, "warrior", 80, 3), state.Mate("Isolde", 0x901, "mage", 100, 2),
                     state.Mate("Corwin", 0x904, "bard", 100, 3)]
        return sit

    cfg = policy.PolicyConfig()
    # A bone knight six tiles off: she stays with the party, as anyone would.
    dec = leave(tamer((6, 0)), policy.Memory())
    assert dec.intent == "rest"
    # Beside her: nothing holds her there. She runs, and it is a fall back.
    dec = leave(tamer((1, 0)), policy.Memory())
    assert dec.intent == "leave" and [a for a in dec.actions if a["verb"] == "flee"]


# ---------------------------------------------------------------- whom the plan sends (cuo-cym.14)

def test_who_the_plan_sends_is_waited_for_when_alive_free_and_near():
    stats = copy.deepcopy(STATS)
    stats["Isolde"]["hunting"] = "Britain Graveyard"
    here = (1000, 1000)
    # By their own word only: the mage. With the plan's: the warrior and the tamer too, who haven't said so yet.
    assert rally.expected(stats, "Wystan", here, "Britain Graveyard") == ["Isolde"]
    assert rally.expected(stats, "Wystan", here, "Britain Graveyard", NAMES) == ["Isolde", "Roland", "Thea"]  # Corwin is far
    stats["Thea"]["busy"] = True   # at the shop
    stats["Roland"]["dead"] = True
    assert rally.expected(stats, "Wystan", here, "Britain Graveyard", NAMES) == ["Isolde"]
    # The plan's names change nothing for a leader's party, which waits for everyone anyway.
    assert rally.expected(STATS, "Roland", here, None, ["Isolde"]) == ["Isolde", "Thea", "Wystan"]
    # A fighter still coming is waited for longer, by a party with nobody beside the rally who can take blows.
    assert rally.fighter_coming(["Roland", "Thea"], ["Isolde", "Corwin"], STATS) == "Roland"
    assert rally.fighter_coming(["Thea"], ["Isolde", "Corwin"], STATS) == ""
    paladin = {**STATS, "Thea": status("paladin", 1002, 1001)}
    assert rally.fighter_coming(["Roland"], ["Thea", "Corwin"], paladin) == ""


def test_who_has_gone_on_past_the_rally():
    centre = (1369, 1475)
    snap = at(1394, 1516, [companion("Roland", 9, -6, -9), companion("Isolde", 7, 0, 7)], name="Corwin")
    stats = {"Wystan": status("archer", 1380, 1495), "Thea": status("tamer", 1394, 1530)}
    # In view, by where the client sees them; out of view, by their own reports. Behind or beside is not ahead.
    assert rally.gone_ahead(["Isolde", "Roland", "Thea", "Wystan"], snap, stats, centre) == ["Roland", "Wystan"]
    near = at(1394, 1516, [companion("Roland", 3, -1, -3)], name="Corwin")
    assert rally.gone_ahead(["Roland", "Nobody"], near, {}, centre) == []
    # From the rally point, not from where its holder now stands: one who ran from something during the wait
    # (here 20 tiles back) doesn't make one standing behind the point "ahead".
    fled = at(1404, 1536, [], name="Roland")
    behind = {"Wystan": status("archer", 1390, 1528)}
    assert rally.gone_ahead(["Wystan"], fled, behind, centre) == ["Wystan"]  # by the holder: wrong
    assert rally.gone_ahead(["Wystan"], fled, behind, centre, (1393, 1515)) == []


def test_the_first_there_waits_for_whom_the_plan_sends_whatever_they_have_said(tmp_path, monkeypatch):
    """Live (2026-10-13) the bard was first at the edge with the mage beside him and the warrior a second
    behind, on a walk to the graveyard that his own report didn't call a hunt: the bard was made the point,
    and the warrior died. The plan names who goes, and the first there waits for them."""
    monkeypatch.setattr(rally, "POINT_WAITS_S", 0.0)
    w = World.open("local", root=tmp_path)
    hunting(w, {"Corwin": (1394, 1516), "Isolde": (1393, 1517)})
    report_all(w, {"Roland": (1392, 1524)})  # walking there: not hunting, by his report
    corwin = party.Party(w, "p1", "Corwin", "Roland", NAMES)
    key = "hunt:britain graveyard"
    mage = [companion("Isolde", 1)]
    snaps = [at(1394, 1516, mage, name="Corwin"), at(1394, 1516, mage, name="Corwin"),
             at(1394, 1516, [*mage, companion("Roland", 6)], name="Corwin"),
             at(1394, 1516, [*mage, companion("Roland", 2)], name="Corwin")]
    # With a plan that doesn't say who goes: those who have said they set out (the mage, beside him), as before.
    r = rally.Rally(Field(w, snaps), corwin, plan="huddle")
    r.sends = lambda area: None
    out = asyncio.run(r.before_hunt(key, "Britain Graveyard", 1369, 1475, 15))
    assert out["with"] == ["Isolde"] and out["point"] == "Corwin" and out["waited_s"] < 0.5 and "plan_sends" not in out
    # With one that does: he waits for the warrior too, and the warrior goes first.
    w.db.execute("DELETE FROM party_rally")
    r = rally.Rally(Field(w, snaps), corwin, plan="huddle")
    r.sends = lambda area: NAMES if area == "Britain Graveyard" else None
    out = asyncio.run(r.before_hunt(key, "Britain Graveyard", 1369, 1475, 15))
    assert out["formed"] and out["with"] == ["Isolde", "Roland"] and not out["without"] and out["point"] == "Roland"
    assert out["plan_sends"] == NAMES and "waited_for_fighter" not in out and 0.9 <= out["waited_s"] < 3
    w.close()


def test_one_who_cant_take_blows_doesnt_give_the_word_while_a_fighter_is_coming(tmp_path, monkeypatch):
    """The others are waited for WAIT_S. A fighter, by a party with nobody beside the rally who can take the
    blows, for FIGHTER_S: live, the bard gave the word and went in as the point."""
    monkeypatch.setattr(rally, "POINT_WAITS_S", 0.0)
    monkeypatch.setattr(rally, "WAIT_S", 0.4)
    monkeypatch.setattr(rally, "FIGHTER_S", 3.0)
    w = World.open("local", root=tmp_path)
    hunting(w, {"Corwin": (1394, 1516)})
    report_all(w, {"Roland": (1392, 1540), "Wystan": (1390, 1545)})
    corwin = party.Party(w, "p1", "Corwin", "Roland", NAMES)
    key = "hunt:britain graveyard"
    alone = at(1394, 1516, name="Corwin")
    s = Field(w, [alone, alone, alone, alone, at(1394, 1516, [companion("Roland", 2)], name="Corwin")])
    r = rally.Rally(s, corwin, plan="huddle")
    r.sends = lambda area: NAMES
    out = asyncio.run(r.before_hunt(key, "Britain Graveyard", 1369, 1475, 15))
    # The archer's time ran out at 0.4 s; the warrior was beside him a second later, and goes first.
    assert out["with"] == ["Roland"] and out["without"] == ["Wystan"] and out["point"] == "Roland"
    assert out["waited_for_fighter"] == "Roland" and 1.0 <= out["waited_s"] < 3.0
    # A fighter who doesn't come is waited for that long and no longer.
    w.db.execute("DELETE FROM party_rally")
    monkeypatch.setattr(rally, "FIGHTER_S", 1.2)
    r = rally.Rally(Field(w, [alone]), corwin, plan="huddle")
    r.sends = lambda area: NAMES
    out = asyncio.run(r.before_hunt(key, "Britain Graveyard", 1369, 1475, 15))
    assert out["formed"] and out["without"] == ["Roland", "Wystan"] and out["waited_for_fighter"] == "Roland"
    assert 1.2 <= out["waited_s"] < 2.2
    # The warrior himself, first there, waits for the others as long as anyone does.
    w.db.execute("DELETE FROM party_rally")
    roland = party.Party(w, "p1", "Roland", "Roland", NAMES)
    r = rally.Rally(Field(w, [at(1392, 1517, name="Roland")]), roland, plan="huddle")
    r.sends = lambda area: NAMES
    out = asyncio.run(r.before_hunt(key, "Britain Graveyard", 1369, 1475, 15))
    assert out["with"] == ["Corwin"] and out["without"] == ["Wystan"] and out["point"] == "Roland"
    assert "waited_for_fighter" not in out and 0.4 <= out["waited_s"] < 1.2
    w.close()


def test_one_who_walked_on_past_the_rally_isnt_waited_for_at_it(tmp_path, monkeypatch):
    """A member whose planner chose a walk to the place, not a hunt, walks on to its centre. Waited for at the
    edge, the warrior would be in there alone for as long as the wait lasts: the word is given instead."""
    monkeypatch.setattr(rally, "POINT_WAITS_S", 0.0)
    w = World.open("local", root=tmp_path)
    hunting(w, {"Corwin": (1394, 1516)})
    report_all(w, {"Roland": (1392, 1524), "Isolde": (1394, 1530)})
    corwin = party.Party(w, "p1", "Corwin", "Roland", NAMES)
    behind = at(1394, 1516, name="Corwin")
    passed = at(1394, 1516, [companion("Roland", 9, -6, -9)], name="Corwin")  # in view, nine tiles on toward the graveyard
    r = rally.Rally(Field(w, [behind, behind, behind, passed]), corwin, plan="huddle")
    r.sends = lambda area: NAMES
    out = asyncio.run(r.before_hunt("hunt:britain graveyard", "Britain Graveyard", 1369, 1475, 15))
    assert out["formed"] and out["gone_ahead"] == ["Roland"] and out["without"] == ["Isolde", "Roland"]
    assert out["point"] == "Roland" and 0.9 <= out["waited_s"] < 2.5
    w.close()


# ---------------------------------------------------------------- a rally point that is chosen (cuo-cym.13)

HERE, CENTRE = (1392, 1517), (1369, 1475)       # where the lich scene's leader stops, and the graveyard's centre
WAY_IN = [(1392, 1528), (1392, 1523), (1392, 1517)]  # its steps there, from the south


def drawn(walls=(), here=HERE, doors=()):
    """The client's map round `here`, as its `nav` reply has it: ground that can be walked to (','), and none
    ('#') where a wall is drawn, each (x1, y1, x2, y2)."""
    r = rally.MAP_TILES
    x0, y0 = here[0] - r, here[1] - r
    blocked = {(x, y) for x1, y1, x2, y2 in walls for x in range(x1, x2 + 1) for y in range(y1, y2 + 1)}
    return {"x0": x0, "y0": y0, "path": -1,
            "rows": ["".join("@" if (x, y) == here else "d" if (x, y) in doors else "#" if (x, y) in blocked else ","
                             for x in range(x0, x0 + 2 * r + 1)) for y in range(y0, y0 + 2 * r + 1)]}


def chosen(walls=(), trail=(), spawns=(), doors=()):
    return rally.choose(HERE, CENTRE, rally.Ground.read(drawn(walls, doors=doors)), trail, spawns)


def test_the_clients_map_is_read_for_what_can_be_walked_to():
    g = rally.Ground.read({"x0": 10, "y0": 20, "rows": [",,#x", ",@d.", "hD,,"]})
    assert g.ok(10, 20) and g.ok(11, 21) and g.ok(12, 22)  # ground it can walk to, and where it stands
    # Not a wall, an item in the way, a door (open or blocked), a house, nor ground the planner can't get to.
    assert not any(g.ok(x, y) for x, y in [(12, 20), (13, 20), (12, 21), (13, 21), (10, 22), (11, 22), (9, 20), (10, 23)])
    assert g.room(11, 21) == 5 and g.room(13, 22) == 2
    assert rally.Ground.read({"status": "done"}) is None and rally.Ground.read(None) is None


def test_on_open_ground_the_rally_point_is_back_the_way_the_party_came():
    """Five tiles back along the leader's own steps, and the second point twelve further: the last of them
    beyond where its steps began, straight away from the hunt's centre."""
    first, second = chosen(trail=WAY_IN)
    assert first == (1392, 1522) and second == (1395, 1534)
    assert rally.tiles(*first, *HERE) == rally.BACK_TILES and rally.tiles(*second, *first) == rally.SECOND_TILES
    # With no steps to go by (a hunt that begins where the party stands): straight away from the centre.
    assert chosen() == ((1395, 1522), (1401, 1534))
    assert rally.way_back(HERE, CENTRE)[:3] == [HERE, (1393, 1518), (1393, 1519)]
    # A tree on the very tile: the ground beside it, further from the hunt.
    first, second = chosen([(1392, 1522, 1392, 1522)], WAY_IN)
    assert first == (1391, 1523) and second == (1395, 1534)
    # Steps that jumped (a recall) aren't the way in, and the leader's map isn't asked about them: see lead.


def test_behind_a_wall_the_points_are_where_there_is_a_way_out():
    """The second map shape: a wall behind where the party formed up."""
    # With a gate in it where the party came in: this side of the gate, on its steps; the second point through it.
    wall = [(1375, 1523, 1399, 1523), (1401, 1523, 1424, 1523)]
    came = [(1400, 1530), (1400, 1526), (1400, 1523), (1398, 1521), (1395, 1519), HERE]
    first, second = chosen(wall, came)
    assert first == (1397, 1520) and second == (1401, 1532)
    g = rally.Ground.read(drawn(wall))
    assert rally.way_out(g, first, CENTRE) and rally.way_out(g, second, CENTRE)
    # Shut (a door is no ground to fall back through), the yard is a dead end: no rally point is chosen, with
    # the party's steps or without. Nor in a pocket walled to the south and the west with the way in from the east.
    assert chosen(wall, came, doors={(1400, 1523)}) is None and chosen(wall, doors={(1400, 1523)}) is None
    assert not rally.way_out(rally.Ground.read(drawn(wall, doors={(1400, 1523)})), (1395, 1522), CENTRE)
    pocket = [(1375, 1523, 1424, 1523), (1375, 1500, 1375, 1523)]
    assert chosen(pocket, [(1420, 1519), (1410, 1518), (1400, 1517), HERE]) is None and chosen(pocket) is None
    # A wall that ends a few tiles to the east is walked round.
    assert chosen([(1360, 1523, 1398, 1523)]) == ((1395, 1522), (1401, 1534))
    # Water all to the south: nothing behind at all.
    assert chosen([(1360, 1520, 1424, 1549)], WAY_IN) is None
    # A rally point with a way out and no second point behind it is still used: the ground ends 14 tiles back.
    first, second = chosen([(1360, 1532, 1424, 1549)], WAY_IN)
    assert first == (1392, 1522) and second is None


def test_a_walk_doesnt_cut_a_corner_or_come_nearer_the_hunt():
    # A gap between two walls that only a step across the corner would pass.
    g = rally.Ground.read(drawn([(1375, 1523, 1392, 1523), (1393, 1524, 1424, 1524)]))
    reach = rally.walks(g, (1392, 1522), CENTRE, 6)
    assert (1393, 1523) in reach and (1392, 1524) not in reach and (1393, 1525) not in reach
    # Round a wall by its far end is a way out only when the hunt isn't nearer on the way.
    open_ground = rally.Ground.read(drawn())
    reach = rally.walks(open_ground, HERE, CENTRE, 10)
    assert (1392, 1527) in reach and reach[(1392, 1527)] == 10 and (1392, 1512) not in reach
    assert min(rally.tiles(*t, *CENTRE) for t in reach) == rally.tiles(*HERE, *CENTRE) - 1


def test_a_point_beside_another_spawn_comes_last():
    """By the world store's spawners: where each is and how far its creatures walk."""
    spawn = (1377, 1540, 5)  # its creatures within sight of everything south-west of 1395,1522
    first, second = chosen(spawns=[spawn])
    assert first == (1396, 1523) and second == (1401, 1534)
    assert all(rally.tiles(*t, 1377, 1540) > 5 + rally.SIGHT_TILES for t in (first, second))
    # With nothing clear of it in reach, a point beside it is better than none.
    first, second = chosen(spawns=[(1395, 1530, 30)])
    assert first == (1395, 1522) and second == (1401, 1534)


def formed(w, by_map=True, ground=None, walk_s=0.9):
    """The leader walks in from the south, sees the lich from HERE, and forms the party up there."""
    report_all(w, {"Roland": (1392, 1528), "Isolde": (1394, 1528)})
    lead = party.Party(w, "p1", "Roland", "Roland", NAMES)
    lich = monster(0x300, "a lich", 24, 12)
    there = at(*HERE, [lich, companion("Isolde", 2)])
    s = Field(w, [at(1392, 1528), at(1392, 1523, [companion("Isolde", 2)]), there, there])
    s.ground, s.walk_s = ground, walk_s
    r = rally.Rally(s, lead, by_map=by_map)
    return lead, s, r


def test_the_leader_forms_the_party_up_where_it_stopped_and_the_rally_point_is_behind_it(tmp_path, monkeypatch):
    monkeypatch.setattr(rally, "POINT_WAITS_S", 0.0)
    w = World.open("local", root=tmp_path)
    lead, s, r = formed(w, ground=drawn())
    out = asyncio.run(r.before_hunt("goal:1", "Britain Graveyard", *CENTRE, 15))
    # It stops at the first sight of the lich, as before, and the others close up on it there.
    assert out["formed"] and out["sighted"] == "a lich" and out["with"] == ["Isolde"] and out["formed_at"] == list(HERE)
    assert s.trips == [("Britain Graveyard", *CENTRE, 15 + rally.EDGE_TILES)]  # no walk back to the point
    # The place to fall back to is five tiles back along its own steps, with a second point behind that.
    assert out["at"] == [1392, 1522] and out["back"] == [1395, 1534] and "unchosen" not in out and out["map_ms"] < 500
    assert s.asked == [{"timeout": rally.MAP_S, "radius": rally.MAP_TILES, "reach": True}]
    row = lead.rally("goal:1")
    assert (row["x"], row["y"], row["back"], row["state"]) == (1392, 1522, (1395, 1534), "go")
    assert r.current() == (1392, 1522) and r.back() == (1395, 1534)
    # The party's next hunt of the place, from inside it, keeps both.
    inside = Field(w, [at(1366, 1478, [companion("Isolde", 1)])])
    inside.ground = drawn(here=(1366, 1478))
    out = asyncio.run(rally.Rally(inside, lead, by_map=True).before_hunt("goal:2", "Britain Graveyard", *CENTRE, 15))
    assert out["at"] == [1392, 1522] and out["back"] == [1395, 1534] and inside.asked == []
    assert lead.rally("goal:2")["back"] == (1395, 1534)
    w.close()


def test_without_the_setting_or_without_a_map_the_rally_point_is_where_the_leader_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(rally, "POINT_WAITS_S", 0.0)
    w = World.open("local", root=tmp_path)
    # Not asked for (the default): as it was, and the client isn't asked for its map.
    lead, s, r = formed(w, by_map=False, ground=drawn())
    out = asyncio.run(r.before_hunt("goal:1", "Britain Graveyard", *CENTRE, 15))
    assert out["at"] == list(HERE) and s.asked == [] and not {"back", "formed_at", "unchosen", "map_ms"} & set(out)
    assert lead.rally("goal:1")["back"] is None and r.back() is None
    # Asked for, from a client with no map to give, and on ground with no way out behind: the same, and why.
    for ground, why in ((None, "no map from the client"),
                        (drawn([(1360, 1520, 1424, 1549)]), "no ground behind with a way out")):
        w.db.execute("DELETE FROM party_rally")
        lead, s, r = formed(w, ground=ground)
        out = asyncio.run(r.before_hunt("goal:1", "Britain Graveyard", *CENTRE, 15))
        assert out["formed"] and out["at"] == list(HERE) and "back" not in out and out["unchosen"] == why
        assert lead.rally("goal:1")["back"] is None
    w.close()


def test_steps_before_a_recall_arent_the_way_in(tmp_path, monkeypatch):
    monkeypatch.setattr(rally, "POINT_WAITS_S", 0.0)
    w = World.open("local", root=tmp_path)
    report_all(w, {"Roland": (1430, 1690)})
    lead = party.Party(w, "p1", "Roland", "Roland", NAMES)
    there = at(*HERE, [monster(0x300, "a lich", 24, 12)])
    # From Britain's bank by a rune to a few tiles south of the graveyard, then on foot.
    s = Field(w, [at(1430, 1690), at(1392, 1523), there, there])
    s.ground, s.walk_s = drawn(), 0.9
    out = asyncio.run(rally.Rally(s, lead, by_map=True).before_hunt("goal:1", "Britain Graveyard", *CENTRE, 15))
    # As walked from 1392,1523, six tiles of steps and then straight away from the graveyard: the bank isn't behind it.
    assert out["at"] == [1392, 1522] and out["back"] == [1397, 1534]
    w.close()


def test_the_other_spawns_are_the_world_stores_spawners_not_its_sightings(tmp_path):
    w = World.open("local", root=tmp_path)
    common = {"map": "Felucca", "creature": "Skeleton", "max_count": 3, "last_seen": "2026-10-13T00:00:00+00:00"}
    w.insert("spawns", {**common, "area": "Britain Graveyard", "x": 1369, "y": 1475, "walk_range": 30, "spawner": "a",
                        "source": "modernuo:Graveyards.json"})
    w.insert("spawns", {**common, "area": "Britain", "x": 1420, "y": 1524, "walk_range": 8, "spawner": "b",
                        "source": "modernuo:Britain.json"})
    w.add_spawn_seen("wilderness near 1350,1500", "lich", 1396, 1523, 1)  # where the last wave was fought
    r = rally.Rally(Field(w, [at(*HERE)]), party.Party(w, "p1", "Roland", "Roland", NAMES))
    assert r.other_spawns(*CENTRE) == [(1420, 1524, 8)]  # not the hunt's own, and not the sighting
    w.close()


def test_the_huddles_rally_chooses_its_point_too(tmp_path, monkeypatch):
    monkeypatch.setattr(rally, "POINT_WAITS_S", 0.0)
    monkeypatch.setattr(rally, "SET_OUT_S", 0.1)
    w = World.open("local", root=tmp_path)
    hunting(w, {"Isolde": HERE})
    isolde = party.Party(w, "p1", "Isolde", "Roland", NAMES)
    s = Field(w, [at(*HERE, [monster(0x300, "a lich", 24, 12)], name="Isolde")])
    s.ground = drawn()
    out = asyncio.run(rally.Rally(s, isolde, plan="huddle", by_map=True).before_hunt(
        "hunt:britain graveyard", "Britain Graveyard", *CENTRE, 15))
    assert out["held"] and out["at"] == [1395, 1522] and out["back"] == [1401, 1534] and out["formed_at"] == list(HERE)
    assert isolde.rally("hunt:britain graveyard")["back"] == (1401, 1534)
    w.close()


def fallen_back_to(dec):
    run = next((a for a in dec.actions if a["verb"] == "flee"), None)
    return (run.get("x"), run.get("y"), run.get("spot")) if run else None


def test_one_badly_hurt_at_the_rally_point_goes_on_to_the_second_point():
    """Formed up, the party fights at its rally point (the warrior stood 1 to 8 tiles from it), so a fall back
    was a few steps to where the wave arrives, and he died there twice. Badly hurt there with companions still
    fighting, he goes on to the second point, the way the party came, and comes back in from it."""
    cfg = policy.PolicyConfig()

    def he(y, hits, lich=6, on_him=False):
        snap = scattered(hits=hits)
        snap["player"].update(y=y)
        snap["mobiles"][0].update(distance=lich, dx=lich, dy=0, attacking_me=on_him)
        sit = state.build(snap, set(), [], bestiary=LICH, companions=COMPANIONS)
        sit.rally, sit.rally_back, sit.party_fighting = (1380, 1501), (1380, 1513), 2
        return sit

    mem = policy.Memory()
    dec = leave(he(1500, 30), mem, now=5.0)
    assert dec.intent == "leave" and dec.regroup and mem.regrouping
    assert fallen_back_to(dec) == (1380, 1512, "back")  # a leg of twelve toward it, thirteen tiles off
    assert dec.note.startswith("fall back to the second point behind the rally point: ")
    # Past the rally point he doesn't turn round for it, whatever his health is by then.
    on = policy.decide(he(1507, 45, lich=12), answers("fight", target="t1"), mem, cfg, now=7.0)
    assert on.intent == "leave" and on.regroup and fallen_back_to(on) == (1380, 1513, "back")
    # At the second point he stands, as he would have at the first.
    there = policy.decide(he(1512, 45, lich=12), answers("fight", target="t1"), mem, cfg, now=9.0)
    assert there.intent == "leave" and there.regroup and fallen_back_to(there) is None
    assert there.note == "fallen back: standing at the second point behind the rally point" and not there.leave_why
    # Healed there, with the others still at it: back in.
    back_in = policy.decide(he(1512, 90, lich=12), answers("seek", target="t1"), mem, cfg, now=12.0)
    assert back_in.intent == "seek" and not mem.regrouping
    # With something on him at the second point there is nowhere further: the line away, as it was at the rally point.
    last = policy.decide(he(1512, 30, lich=1, on_him=True), answers("fight", target="t1"), policy.Memory(leaving_until=50.0, regrouping=True),
                         cfg, now=9.0)
    assert last.intent == "leave" and fallen_back_to(last) == (None, None, None)
    # Not badly hurt at the rally point, with nothing on him: he stands there, as before.
    stays = leave(he(1500, 60), policy.Memory())
    assert stays.intent != "leave" and "held with the party" in stays.note
    # With nobody left in the fight a hurt member has nothing to run on from: it stands at the rally point.
    alone = he(1500, 30)
    alone.party_fighting = 0
    assert not policy.goes_on(alone) and policy.stands_at_rally(alone) and policy.rally_run(alone) is None


def test_ahead_of_the_rally_point_a_run_goes_to_it_first():
    sit = state.build(scattered(hits=30), set(), [], bestiary=LICH, companions=COMPANIONS)
    sit.rally, sit.rally_back, sit.party_fighting = (1380, 1508), (1380, 1520), 2
    assert not policy.goes_on(sit) and fallen_back_to(leave(sit, policy.Memory())) == (1380, 1508, "rally")
    # A party that has no second point: every run is for the rally point, as before.
    sit.rally_back = None
    sit.rally = (1380, 1501)
    assert not policy.goes_on(sit) and policy.rally_run(sit) is None and policy.stands_at_rally(sit)


def test_one_with_something_on_it_at_the_rally_point_runs_to_the_second_point():
    """From the rally point itself there was no way "to the rally": the run was the line away, each member's
    own. Live, a tamer whose pet was gone had a bone knight beside her there."""
    snap = after_the_fight(knight_at=(1, 0), thea_at=(0, 3), name="Thea", weapon="a shepherd's crook",
                           skills={"Animal Taming": 90.0, "Animal Lore": 90.0, "Veterinary": 80.0})
    snap["mobiles"] = [m for m in snap["mobiles"] if m.get("monster")] + \
        [companion("Roland", 3, 3, 0), companion("Isolde", 2, 0, 2), companion("Corwin", 3, -3, 0)]
    snap["mobiles"][0]["attacking_me"] = True
    snap["pets"] = []
    sit = state.build(snap, set(), [], bestiary=LICH, companions={"Roland": "warrior", "Isolde": "mage", "Corwin": "bard"})
    sit.rally, sit.rally_back, sit.party_fighting = (1000, 1000), (1000, 1012), 3
    dec = leave(sit, policy.Memory())
    assert dec.intent == "leave" and dec.regroup and fallen_back_to(dec) == (1000, 1012, "back")


def test_back_at_the_rally_point_with_the_party_a_chased_member_stands_with_them_unless_badly_hurt():
    def back(hits):
        there = lich_fight()  # the mage, the archer and the tamer within five tiles
        there["player"].update(x=1380, y=1519, hits=hits)
        there["mobiles"][0].update(distance=2, dx=2, attacking_me=True)
        sit = state.build(there, set(), [], bestiary=LICH, companions=COMPANIONS)
        sit.rally, sit.rally_back, sit.party_fighting = (1380, 1520), (1380, 1532), 2
        return sit

    cfg = policy.PolicyConfig()
    # At the rally point with what came after him: he fights it there, beside them, as before.
    mem = policy.Memory(leaving_until=50.0, leave_started=5.0, regrouping=True)
    dec = policy.decide(back(95), answers("fight", target="t1"), mem, cfg, now=12.0)
    assert dec.intent == "fight" and not mem.regrouping
    # Badly hurt, his run isn't over there: on to the second point, a leg at a time.
    mem = policy.Memory(leaving_until=50.0, leave_started=5.0, regrouping=True)
    dec = policy.decide(back(30), answers("fight", target="t1"), mem, cfg, now=12.0)
    assert dec.intent == "leave" and dec.regroup and mem.regrouping and fallen_back_to(dec) == (1380, 1531, "back")


def test_the_fight_state_gets_the_second_point_from_the_party(tmp_path):
    w = World.open("local", root=tmp_path)
    mine = party.Party(w, "p1", "Roland", "Roland", NAMES)
    mine.companions.update(COMPANIONS)
    link = mates.Mates(mine)
    link.rally = rally.Rally(Field(w, [at(1392, 1517)]), mine)
    snap = lich_fight()
    snap["agent"]["bandaging"] = False
    sit = state.build(snap, set(), [], bestiary=LICH, companions=mine.companions)
    mine.rally_open("goal:1", 1392, 1522, back=(1395, 1534))
    link.before(sit)
    assert sit.rally is None and sit.rally_back is None  # nobody has gone in from it yet
    mine.rally_go("goal:1")
    link.rally._current = (0.0, None)
    link.before(sit)
    assert sit.rally == (1392, 1522) and sit.rally_back == (1395, 1534)
    # A rally point with nothing behind it, and a store from before there was a second point.
    mine.rally_open("goal:2", 1392, 1517)
    mine.rally_go("goal:2")
    link.rally._current = (0.0, None)
    link.before(sit)
    assert sit.rally == (1392, 1517) and sit.rally_back is None
    old = World(tmp_path / "old.sqlite")
    old.db.execute("CREATE TABLE party_rally (party TEXT NOT NULL, key TEXT NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL, "
                   "point TEXT, state TEXT NOT NULL, sighted INTEGER NOT NULL DEFAULT 0, t REAL NOT NULL, go_t REAL, "
                   "host TEXT, PRIMARY KEY (party, key))")
    theirs = party.Party(old, "p1", "Roland", "Roland", NAMES)
    party.ensure(old)  # a second brain starting: nothing to add
    theirs.rally_open("goal:1", 1392, 1522, back=(1395, 1534))
    assert theirs.rally("goal:1")["back"] == (1395, 1534)
    old.close()
    w.close()


def test_a_fleet_file_asks_for_the_chosen_rally_point(tmp_path, monkeypatch):
    """Off unless asked for, as forming up is: a run can be measured with and without it."""
    from uo_brain import fleet
    f = tmp_path / "p.toml"
    f.write_text('name = "p"\nparty = true\nleader = "A"\nform_up = true\n[[agent]]\nname = "A"\naccount = "a"\npassword = "a"\n'
                 'port = 1\ngoal = "hunt"\n[[agent]]\nname = "B"\naccount = "b"\npassword = "b"\nport = 2\n')
    assert fleet.load(f).form_up and not fleet.load(f).rally_map
    started = []
    monkeypatch.setattr(fleet.subprocess, "Popen", lambda cmd, **kw: started.append(cmd))
    fleet.start_brain(fleet.load(f), fleet.load(f).agents[1], tmp_path)
    fl = fleet.load(f, ["rally_map=true"])
    fleet.start_brain(fl, fl.agents[1], tmp_path)
    assert fl.rally_map and "--party-rally-map" not in started[0] and "--party-rally-map" in started[1]
    assert "--party-form-up" in started[1] and rally.Rally(None, None).by_map is False


# ---------------------------------------------------------------- found in review (2026-10-13)

def test_only_one_sent_on_from_the_rally_point_runs_to_the_second_point():
    """A first version took anyone nearer the second point than the rally point is to be on its way there. By
    the game's square distances that was anyone a tile behind the rally point and up to eleven to its side: a
    member at nine tenths of its health, four tiles behind the rally point, ran eight tiles further from its
    party instead of four to it."""
    def member(x, y, hits=86):
        snap = scattered(hits=hits)
        snap["player"].update(x=x, y=y)
        snap["mobiles"][0].update(distance=8, dx=8, dy=0)
        sit = state.build(snap, set(), [], bestiary=LICH, companions=COMPANIONS)
        sit.rally, sit.rally_back, sit.party_fighting = (1380, 1501), (1380, 1513), 3
        return sit

    # Behind the rally point, beside it, ahead of it, and beyond the second point: every run is for the rally point.
    for x, y in ((1380, 1505), (1390, 1502), (1391, 1502), (1389, 1499), (1380, 1507), (1380, 1528)):
        sit = member(x, y)
        assert not policy.goes_on(sit), (x, y)
        to = policy.rally_run(sit)
        assert to is not None and rally.tiles(*to, 1380, 1501) < rally.tiles(x, y, 1380, 1501), (x, y)
    # Four tiles behind it in good health, leaving for a pack: four tiles to it, as with no second point.
    dec = leave(member(1380, 1505), policy.Memory())
    assert fallen_back_to(dec) == (1380, 1501, "rally") and "second point" not in dec.note
    # Hurt there too, when nothing sent it on.
    assert fallen_back_to(leave(member(1380, 1507, hits=45), policy.Memory())) == (1380, 1501, "rally")
    # One whose last run was for the second point keeps to it for a few seconds, and no longer.
    cfg = policy.PolicyConfig()
    mem = policy.Memory()
    assert fallen_back_to(leave(member(1380, 1501, hits=30), mem, now=5.0)) == (1380, 1513, "back")
    assert mem.back_until == 5.0 + policy.BACK_RUN_S
    on = policy.decide(member(1380, 1506, hits=45), answers("fight", target="t1"), mem, cfg, now=6.0)
    assert fallen_back_to(on) == (1380, 1513, "back") and mem.back_until == 6.0 + policy.BACK_RUN_S
    later = member(1380, 1506, hits=45)
    policy.decide(later, answers("fight", target="t1"), mem, cfg, now=6.0 + policy.BACK_RUN_S + 1)
    assert not later.past_rally and not policy.goes_on(later)
    # Back in from it (healed, the others still fighting): its next run begins from the rally point's rules.
    mem = policy.Memory(leaving_until=50.0, leave_started=5.0, regrouping=True, back_until=30.0)
    healed = member(1380, 1512, hits=95)
    dec = policy.decide(healed, answers("seek", target="t1"), mem, cfg, now=12.0)
    assert dec.intent == "seek" and not mem.regrouping and mem.back_until == 0.0 and not healed.past_rally


def test_at_the_second_point_a_fight_that_is_over_doesnt_end_the_hunt():
    """Twelve tiles behind the rally point the party is out of a member's ten tiles. The rule for one with
    nobody to go back to took it for that: the others had only to win before it was back at 60% of its
    health, and its hunt ended "left the area", with a recall away and the place refused for half an hour."""
    cfg = policy.PolicyConfig()

    def he(y, lich=16, on_him=False, sent_on=True):
        snap = scattered(hits=43)
        snap["player"].update(y=y)
        snap["mobiles"][0].update(distance=lich, dx=lich, dy=0, attacking_me=on_him)
        sit = state.build(snap, set(), [], bestiary=LICH, companions=COMPANIONS)
        sit.rally, sit.rally_back, sit.party_fighting, sit.party_running = (1380, 1501), (1380, 1513), 0, 0
        mem = policy.Memory(leaving_until=50.0, leave_started=5.0, regrouping=True, leave_why="badly hurt",
                            back_until=30.0 if sent_on else 0.0)
        return sit, mem

    # At the second point, and half way to it: the run is over where he is, and nothing says he left.
    for y in (1512, 1507):
        sit, mem = he(y)
        dec = policy.decide(sit, answers("fight", target="t1"), mem, cfg, now=12.0)
        assert dec.intent != "leave" and not dec.leave_why and not dec.regroup, y
        assert not mem.regrouping and not mem.fall_back_ended and mem.leaving_until == 12.0, y
        assert mem.no_seek_until == 12.0 + policy.RALLY_STAND_S and mem.no_leave_until == 0.0, y
    # While the others still fight he stays fallen back there, as before.
    sit, mem = he(1512)
    sit.party_fighting = 2
    dec = policy.decide(sit, answers("fight", target="t1"), mem, cfg, now=12.0)
    assert dec.intent == "leave" and dec.regroup and mem.regrouping
    # With something on him there and nobody left in the fight, it is a leave like any other, as it was at the
    # rally point.
    sit, mem = he(1512, lich=1, on_him=True)
    dec = policy.decide(sit, answers("fight", target="t1"), mem, cfg, now=12.0)
    assert dec.intent == "leave" and not dec.regroup and dec.leave_why == "badly hurt" and not mem.regrouping
    # One on its way to the rally point, not sent on from it, goes there as before, and the rules there decide.
    sit, mem = he(1507, sent_on=False)
    dec = policy.decide(sit, answers("fight", target="t1"), mem, cfg, now=12.0)
    assert dec.intent == "leave" and dec.regroup and mem.regrouping


def test_one_on_the_hunt_who_passes_the_rally_is_coming_back_to_it(tmp_path, monkeypatch):
    """Each member walks to its own stop at the edge before it looks for the rally, and that stop may be
    nearer the hunt than whoever holds it. Taken for one who had walked on, the warrior nine tiles past the
    bard had the word given after a second, with the mage behind not waited for."""
    monkeypatch.setattr(rally, "POINT_WAITS_S", 0.0)
    monkeypatch.setattr(rally, "WAIT_S", 1.2)
    monkeypatch.setattr(rally, "FIGHTER_S", 2.0)
    w = World.open("local", root=tmp_path)
    hunting(w, {"Corwin": (1394, 1516), "Roland": (1392, 1524), "Isolde": (1394, 1530)})
    corwin = party.Party(w, "p1", "Corwin", "Roland", NAMES)
    behind = at(1394, 1516, name="Corwin")
    passed = at(1394, 1516, [companion("Roland", 9, -6, -9)], name="Corwin")
    r = rally.Rally(Field(w, [behind, behind, passed]), corwin, plan="huddle")
    r.sends = lambda area: NAMES
    out = asyncio.run(r.before_hunt("hunt:britain graveyard", "Britain Graveyard", 1369, 1475, 15))
    # He is waited for, the whole of a fighter's time, and so is the mage for hers.
    assert out["formed"] and "gone_ahead" not in out and out["without"] == ["Isolde", "Roland"]
    assert out["waited_for_fighter"] == "Roland" and 2.0 <= out["waited_s"] < 3.2
    # One the plan sends who is nine tiles on by a walk of his own (his report names no hunt) still ends the wait.
    w.db.execute("DELETE FROM party_rally")
    report_all(w, {"Roland": (1392, 1524)})
    r = rally.Rally(Field(w, [behind, behind, passed]), corwin, plan="huddle")
    r.sends = lambda area: NAMES
    out = asyncio.run(r.before_hunt("hunt:britain graveyard", "Britain Graveyard", 1369, 1475, 15))
    assert out["gone_ahead"] == ["Roland"] and out["waited_s"] < 1.2
    w.close()


def test_a_yard_with_its_only_opening_toward_the_hunt_has_no_way_out():
    """A walk that gets eight tiles further from the hunt was the whole test, and any dead end deeper than that
    passed: a yard walled on three sides had both points put inside it. A way out also leads on to the edge
    of the map."""
    yard = [(1384, 1510, 1384, 1546), (1400, 1510, 1400, 1546), (1384, 1546, 1400, 1546)]
    g = rally.Ground.read(drawn(yard))
    inside = (1392, 1524)
    assert any(rally.tiles(*t, *CENTRE) >= rally.tiles(*inside, *CENTRE) + rally.OUT_TILES
               for t in rally.walks(g, inside, CENTRE, 2 * rally.OUT_TILES))  # deep enough for the first test alone
    assert inside not in g.out(CENTRE) or g.out(CENTRE)[inside] < rally.tiles(*inside, *CENTRE) - 1
    assert not rally.way_out(g, inside, CENTRE) and chosen(yard) is None and chosen(yard, WAY_IN) is None
    # With a gap in its far wall the same ground will do.
    gap = [(1384, 1510, 1384, 1546), (1400, 1510, 1400, 1546), (1384, 1546, 1391, 1546), (1393, 1546, 1400, 1546)]
    assert rally.way_out(rally.Ground.read(drawn(gap)), inside, CENTRE) and chosen(gap, WAY_IN) is not None
    # On open ground every tile leads out, by a walk that need come no nearer the hunt than it is.
    open_ground = rally.Ground.read(drawn())
    assert all(open_ground.out(CENTRE)[t] == rally.tiles(*t, *CENTRE) for t in ((1392, 1522), (1395, 1534), HERE))


def test_a_reused_rally_point_brings_its_second_point_only_when_that_is_behind_it_for_this_hunt(tmp_path, monkeypatch):
    monkeypatch.setattr(rally, "POINT_WAITS_S", 0.0)
    w = World.open("local", root=tmp_path)
    report_all(w, {"Roland": (1366, 1478)})
    lead = party.Party(w, "p1", "Roland", "Roland", NAMES)
    # The last hunt's points, chosen for a centre to the north-east: for this hunt its second point is the nearer.
    lead.rally_open("goal:1", 1393, 1516, point="Roland", back=(1381, 1504))
    lead.rally_go("goal:1")
    s = Field(w, [at(1366, 1478)])
    out = asyncio.run(rally.Rally(s, lead).before_hunt("goal:2", "Britain Graveyard", 1369, 1475, 15))
    assert out["at"] == [1393, 1516] and "back" not in out and lead.rally("goal:2")["back"] is None
    w.close()


def test_a_store_that_cant_be_given_its_column_says_so(tmp_path):
    """Five brains start together on one store. One refused a column another had just added went on; one
    refused for any other reason doesn't, or every look at the rally would fail for the rest of its run."""
    import sqlite3

    class Db:
        def __init__(self, why):
            self.why = why

        def execute(self, sql, *a):
            if sql.startswith("ALTER"):
                raise sqlite3.OperationalError(self.why)
            return []

        def commit(self):
            pass

    class Store:
        def __init__(self, why):
            self.db = Db(why)

    party.add_column(Store("duplicate column name: back_x"), "party_rally", "back_x", "INTEGER")
    try:
        party.add_column(Store("database is locked"), "party_rally", "back_x", "INTEGER")
        raise AssertionError("a locked store went unsaid")
    except sqlite3.OperationalError as e:
        assert "locked" in str(e)


# ---------------------------------------------------------------- a trip of its own into the hunt (live, 2026-10-14)

def test_a_trip_into_the_hunt_the_plan_sends_it_to_stops_at_the_rally_point(tmp_path, monkeypatch):
    """Live, the mage's planner chose to walk to the graveyard rather than hunt it: she walked through the rally
    point toward the spawn's centre, the warrior who held the rally went in after her alone, and two died. Her
    trip now goes no further than the rally: she closes up on whoever holds it, and waits for the word."""
    monkeypatch.setattr(rally, "POINT_WAITS_S", 0.0)
    w = World.open("local", root=tmp_path)
    hunting(w, {"Roland": (1392, 1516)})
    report_all(w, {"Isolde": (1394, 1530)})
    roland = party.Party(w, "p1", "Roland", "Roland", NAMES)
    isolde = party.Party(w, "p1", "Isolde", "Roland", NAMES)
    key = "hunt:britain graveyard"
    roland.rally_claim(key, 1392, 1516)  # the warrior holds the rally at the edge
    field = Field(w, [at(1394, 1530, name="Isolde"), at(1393, 1519, [companion("Roland", 3, -1, -3)], name="Isolde")])
    s = Session(rpc=None, world=w)
    s.rally = rally.Rally(field, isolde, plan="huddle")
    s.rally.sends = lambda area: NAMES
    seen_hunting = []

    async def run():
        trip = asyncio.get_running_loop().create_task(s.to_the_rally("Britain Graveyard", 1369, 1475, 15))
        await asyncio.sleep(0.6)
        seen_hunting.append((s.hunting_area, isolde.statuses()["Isolde"].get("hunting")))
        assert not trip.done()  # with him, waiting for the word
        roland.rally_open(key, 1392, 1516, point="Roland", sighted=True)
        roland.rally_go(key)
        return await trip

    r = asyncio.run(run())
    assert r.ok and "the word to go in has been given" in r.summary and r.data["formed_up"]["point"] == "Roland"
    # As far as the edge, then on to him: never to the centre of the spawn.
    assert field.trips == [("Britain Graveyard", 1369, 1475, 15 + rally.EDGE_TILES)]
    assert [p["target"] for v, p in field.acts if v == "follow"] == [0x900]
    # On its way to that hunt by its own report, so whoever holds the rally waits for it; and no longer after.
    assert seen_hunting == [("Britain Graveyard", "Britain Graveyard")] and s.hunting_area == ""
    # A place it left creatures at lately is refused, as any trip there is.
    import time as _time
    s.left_from.append((1369, 1475, _time.monotonic(), "Britain Graveyard", "3 coming at once"))
    refused = asyncio.run(s.to_the_rally("Britain Graveyard", 1369, 1475, 15))
    assert not refused.ok and refused.summary.startswith("not going back near Britain Graveyard")
    w.close()


def test_one_who_walked_on_doesnt_make_one_who_cant_take_blows_go_in_first(tmp_path, monkeypatch):
    """Going in after one who walked on past the rally, the party went in one at a time. With a fighter on his
    way the one holding the rally, a bard, waits for him (within FIGHTER_S) and he goes first."""
    monkeypatch.setattr(rally, "POINT_WAITS_S", 0.0)
    w = World.open("local", root=tmp_path)
    hunting(w, {"Corwin": (1394, 1516)})
    report_all(w, {"Isolde": (1394, 1530), "Roland": (1392, 1540)})  # neither has said the hunt
    corwin = party.Party(w, "p1", "Corwin", "Roland", NAMES)
    key = "hunt:britain graveyard"
    her = companion("Isolde", 9, -6, -9)  # nine tiles on toward the graveyard, by a walk of her own
    snaps = [at(1394, 1516, name="Corwin"), at(1394, 1516, [her], name="Corwin"), at(1394, 1516, [her], name="Corwin"),
             at(1394, 1516, [her], name="Corwin"), at(1394, 1516, [her, companion("Roland", 2)], name="Corwin")]
    r = rally.Rally(Field(w, snaps), corwin, plan="huddle")
    r.sends = lambda area: NAMES
    out = asyncio.run(r.before_hunt(key, "Britain Graveyard", 1369, 1475, 15))
    assert out["gone_ahead"] == ["Isolde"] and out["with"] == ["Roland"] and out["point"] == "Roland"
    assert 1.4 <= out["waited_s"] < 3.0  # until the warrior was beside him, not the moment she passed
    # With no fighter coming, the party goes in after her at once: there is nobody to go first.
    w.db.execute("DELETE FROM party_rally")
    r = rally.Rally(Field(w, snaps), corwin, plan="huddle")
    r.sends = lambda area: ["Corwin", "Isolde"]
    out = asyncio.run(r.before_hunt(key, "Britain Graveyard", 1369, 1475, 15))
    assert out["gone_ahead"] == ["Isolde"] and out["waited_s"] < 1.0
    # One who can take blows here (the host, a warrior) goes in after her at once, first.
    w.db.execute("DELETE FROM party_rally")
    hunting(w, {"Roland": (1394, 1516)})
    report_all(w, {"Corwin": (1392, 1545)})
    roland = party.Party(w, "p1", "Roland", "Roland", NAMES)
    mine = [at(1394, 1516, name="Roland"), at(1394, 1516, [her], name="Roland"), at(1394, 1516, [her], name="Roland")]
    r = rally.Rally(Field(w, mine), roland, plan="huddle")
    r.sends = lambda area: NAMES
    out = asyncio.run(r.before_hunt(key, "Britain Graveyard", 1369, 1475, 15))
    assert out["gone_ahead"] == ["Isolde"] and out["point"] == "Roland" and out["waited_s"] < 1.0
    # And the warrior who walked on himself: they go in after him, and he is the point.
    w.db.execute("DELETE FROM party_rally")
    report_all(w, {"Roland": (1392, 1540)})
    him = companion("Roland", 9, -6, -9)
    r = rally.Rally(Field(w, [at(1394, 1516, name="Corwin"), at(1394, 1516, [him], name="Corwin"),
                              at(1394, 1516, [him], name="Corwin")]), corwin, plan="huddle")
    r.sends = lambda area: NAMES
    out = asyncio.run(r.before_hunt(key, "Britain Graveyard", 1369, 1475, 15))
    assert out["gone_ahead"] == ["Roland"] and out["point"] == "Roland" and out["waited_s"] < 1.0
    w.close()


def test_a_plan_that_holds_the_party_back_until_it_has_looked_is_looked_at_from_the_edge(tmp_path, monkeypatch):
    """cuo-cym.14: with memory on every plan the huddle agreed said to go in only after a fresh scout, nobody was ever
    sent, and the lich was never fought. Those the plan names go to the edge together; the first there looks, and the
    party goes in only if what it sees meets the plan's condition. Otherwise the rally is called off with what was
    seen, the others are told, and the plan is made again on it."""
    monkeypatch.setattr(rally, "POINT_WAITS_S", 0.0)
    monkeypatch.setattr(rally, "LOOK_EVERY_S", 0.0)
    monkeypatch.setattr(rally, "LOOK_S", 0.3)
    w = World.open("local", root=tmp_path)
    w.replace_source("creatures", "fixture", [{"type": "Lich", "name": "a lich", "body": 24, "hits_min": 103, "hits_max": 120,
                                                "difficulty": "strong"}])
    hunting(w, {"Roland": (1392, 1517), "Isolde": (1393, 1517)})
    roland = party.Party(w, "p1", "Roland", "Roland", NAMES)
    key = "hunt:britain graveyard"
    lich = {**monster(0x300, "a lich", 24, 14), "war_mode": False}
    here = at(1392, 1517, [companion("Isolde", 1), lich], name="Roland")

    class Look:
        def __init__(self, ps):
            self.ps, self.asked, self.seen = list(ps), [], []

        async def holds_for_a_look(self, judge, area):
            return area == "Britain Graveyard"

        async def clear_to_go(self, judge, area, seen):
            self.asked.append(seen)
            return self.ps.pop(0) if len(self.ps) > 1 else self.ps[0]

        async def seen_from_edge(self, area, seen):
            self.seen.append(seen)

    r = rally.Rally(Field(w, [here]), roland, plan="huddle")
    r.sends, r.look = (lambda area: ["Roland", "Isolde"]), Look([0.2])
    out = asyncio.run(r.before_hunt(key, "Britain Graveyard", 1369, 1475, 15))
    seen = "a lich (14 tiles off, stronger than the party here: a hard fight, not coming at the party). The party here: Roland, Isolde."
    assert not out["formed"] and out["called_off"] and out["looked"]["seen"] == seen and r.look.asked == [seen]
    assert out["why"] == f"seen from the edge of Britain Graveyard: {seen}; the plan's condition for going in isn't met"
    assert r.look.seen == [seen] and roland.rally(key)["state"] == "off" and roland.rally(key)["why"] == out["why"]
    # The one who closed up on him is told why, and doesn't go in.
    isolde = party.Party(w, "p1", "Isolde", "Roland", NAMES)
    j = asyncio.run(rally.Rally(Field(w, [at(1393, 1517, name="Isolde")]), isolde, plan="huddle")
                    .join(key, "Britain Graveyard", 1369, 1475, 15, on="Roland"))
    assert not j["formed"] and j["called_off"] and j["why"] == out["why"]
    # What they see meets it: the word is given, as for any hunt.
    w.db.execute("DELETE FROM party_rally")
    r = rally.Rally(Field(w, [here]), roland, plan="huddle")
    r.sends, r.look = (lambda area: ["Roland", "Isolde"]), Look([0.3, 0.7])
    lich_coming = at(1392, 1517, [companion("Isolde", 1), {**lich, "war_mode": True, "distance": 12, "dx": 12}], name="Roland")
    r.s.snaps = [here, here, lich_coming]
    out = asyncio.run(r.before_hunt(key, "Britain Graveyard", 1369, 1475, 15))
    assert out["formed"] and out["looked"]["clear"] and out["looked"]["p"] == [0.3, 0.7]
    assert "1 coming at the party" in out["looked"]["seen"] and roland.rally(key)["state"] == "go"
    # ...and the fight coming to them while they look: in as one, whatever the yes/no says.
    w.db.execute("DELETE FROM party_rally")
    r = rally.Rally(Field(w, [here]), roland, plan="huddle")
    r.sends, r.look = (lambda area: ["Roland", "Isolde"]), Look([0.1])
    r.s.snaps = [here, here, at(1392, 1517, [companion("Isolde", 1), {**lich, "war_mode": True, "distance": 7, "dx": 7}],
                                name="Roland")]
    out = asyncio.run(r.before_hunt(key, "Britain Graveyard", 1369, 1475, 15))
    assert out["formed"] and out["looked"]["clear"] and out["looked"]["contact"] and roland.rally(key)["state"] == "go"
    # A plan that sends them in now, or a leader's party: nothing is looked at.
    w.db.execute("DELETE FROM party_rally")
    r = rally.Rally(Field(w, [here]), roland, plan="huddle")
    r.sends, r.look = (lambda area: ["Roland", "Isolde"]), Look([0.0])
    r.look.holds_for_a_look = lambda judge, area: asyncio.sleep(0, False)
    out = asyncio.run(r.before_hunt(key, "Britain Graveyard", 1369, 1475, 15))
    assert out["formed"] and "looked" not in out and r.look.asked == []
    w.close()


def test_a_hunt_whose_rally_was_called_off_isnt_walked_into(tmp_path):
    """A rally called off (a look that didn't send them in, a leader's hunt refused) ends the hunt: before, a member
    whose rally didn't form walked on to the centre of the spawn as if there had been no rally."""
    w = World(tmp_path / "w.sqlite")
    w.add_place("graveyard", "Britain Graveyard", 1369, 1475, source="test")
    s = Session(rpc=None, world=w)
    walked = []

    class Off:
        async def before_hunt(self, *a):
            return {"formed": False, "called_off": True, "why": "seen from the edge: a lich"}

    async def walk(*a, **kw):
        walked.append(a)
        return Result(True, "arrived")

    async def defended(work):
        return await work

    s.travel_to, s.defended, s.judge = walk, defended, object()
    r = asyncio.run(s._hunt("Britain Graveyard", 1369, 1475, 15, 5, 10, 85, None, Off(), "hunt:britain graveyard"))
    assert not r.ok and r.summary == "did not hunt Britain Graveyard: seen from the edge: a lich" and walked == []
    w.close()
