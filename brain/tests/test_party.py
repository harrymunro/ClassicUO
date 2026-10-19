"""Party play (cuo-4ar): one plan for several characters, and companions known in the fights."""

import asyncio
import copy
import time
import json

from uo_brain import fleet, party, planner, policy, questions, state
from uo_brain.session import Result, Session, describe
from uo_brain.world import World

from conftest import SNAPSHOT, mage_snapshot
from test_planner import FakeSession, call, reply, scripted
from test_policy import answers

LICH = {24: {"type": "Lich", "name": "a lich", "hits": 120, "damage": "24-26", "difficulty": "strong", "caster": True},
        79: {"type": "LichLord", "name": "a lich lord", "hits": 303, "damage": "11-13", "difficulty": "deadly", "caster": True},
        57: {"type": "BoneKnight", "name": "a bone knight", "hits": 150, "damage": "8-18", "difficulty": "moderate",
             "caster": False},
        50: {"type": "Skeleton", "name": "a skeleton", "hits": 48, "damage": "3-7", "difficulty": "weak", "caster": False}}
NAMES = ["Roland", "Isolde", "Wystan", "Thea", "Corwin"]


def companion(name, distance, dx=0, dy=0, dead=False):
    return {"serial": 0x900 + NAMES.index(name), "name": name, "body": 400, "notoriety": "innocent", "human": True,
            "monster": False, "pet": False, "hits_pct": 100, "dead": dead, "distance": distance, "dx": dx, "dy": dy,
            "dir": "west"}


def monster(serial, name, body, distance):
    return {"serial": serial, "name": name, "body": body, "notoriety": "gray", "human": False, "pet": False,
            "monster": True, "hits_pct": 100, "dead": False, "poisoned": False, "war_mode": True,
            "distance": distance, "dx": distance, "dy": 0, "dir": "east", "my_target": False}


def lich_fight(with_party=True, lord=True):
    snap = copy.deepcopy(SNAPSHOT)
    snap["player"]["name"] = "Roland"
    snap["player"]["hits"] = snap["player"]["hits_max"] = 95
    snap["mobiles"] = [monster(0x300, "a lich lord", 79, 4) if lord else monster(0x300, "a lich", 24, 4)]
    if with_party:
        snap["mobiles"] += [companion("Isolde", 2), companion("Wystan", 3), companion("Thea", 5), companion("Corwin", 14)]
    snap["agent"]["engaged"] = 0
    return snap


COMPANIONS = {"Isolde": "mage", "Wystan": "archer", "Thea": "tamer", "Corwin": "bard"}


def test_a_lich_is_no_reason_to_leave_for_a_party_beside_the_character():
    """A lich lord alone is "far stronger: do not fight it alone"; with three companions beside the character,
    a hard fight, and no reason to leave."""
    alone = state.build(lich_fight(False), set(), [], bestiary=LICH)
    assert alone.hostiles[0].info["strength"].startswith("far stronger than you")  # a lich lord
    sit = state.build(lich_fight(), set(), [], bestiary=LICH, companions=COMPANIONS)
    words = sit.hostiles[0].info["strength"]
    assert words == ("stronger than you: a hard fight, with your 3 companions beside you (far stronger than you alone)")
    # Named as companions, with what each plays as; Corwin is too far off to count in the odds.
    assert [(c["name"], c["plays_as"]) for c in sit.state["your_party"]] == \
        [("Isolde", "mage"), ("Wystan", "archer"), ("Thea", "tamer"), ("Corwin", "bard")]
    assert all(o.get("kind") != "player" for o in sit.state["other_beings_nearby"])
    # Alone, Jev is asked whether to leave (a far stronger creature close); beside the party it isn't.
    assert "leave_now" in questions.build(alone) and "leave_now" not in questions.build(sit)
    dec = policy.decide(sit, answers("fight", target="t1", leave_now=0.3), policy.Memory(), policy.PolicyConfig())
    assert dec.intent == "fight"


def test_what_is_coming_weighs_against_the_party():
    """The shoot's wave: a lich, two bone knights and four skeletons. Alone, far too many; for four together,
    a fair fight, so the pack rule doesn't send everyone away."""
    wave = [monster(0x301, "a bone knight", 57, 5), monster(0x302, "a bone knight", 57, 6)] + \
        [monster(0x310 + i, "a skeleton", 50, 6 + i) for i in range(4)]
    alone = lich_fight(False, lord=False)
    alone["mobiles"] += copy.deepcopy(wave)
    together = lich_fight(True, lord=False)
    together["mobiles"] += copy.deepcopy(wave)
    a = state.build(alone, set(), [], bestiary=LICH)
    t = state.build(together, set(), [], bestiary=LICH, companions=COMPANIONS)
    assert a.pack_weight >= state.PACK_FAR and "too many to fight at once" in a.state["coming_at_you"]
    assert t.pack_weight < state.PACK_HARD and t.state["coming_at_you"].endswith("together a fair fight for your party of 4")
    assert policy.decide(t, answers("fight", target="t1"), policy.Memory(), policy.PolicyConfig()).intent == "fight"
    assert policy.decide(a, answers("fight", target="t1"), policy.Memory(), policy.PolicyConfig()).intent == "leave"


def test_the_planner_hears_of_companions_not_strangers():
    snap = lich_fight()
    snap["agent"]["threats"] = [{"serial": 0x901, "kind": "unknown", "distance": 2},
                                {"serial": 0x777, "kind": "unknown", "distance": 6}]
    told = describe(snap, companions=COMPANIONS)
    assert told["players_near"] == ["an unknown player 6 tiles away"]  # Isolde isn't one, and "an", not "a"


def test_the_leader_posts_goals_and_each_reports_its_state(tmp_path):
    w = World.open("local", root=tmp_path)
    lead = party.Party(w, "p1", "Roland", "Roland", NAMES)
    isolde = party.Party(w, "p1", "Isolde", "Roland", NAMES)
    assert lead.leading and not isolde.leading and isolde.latest() is None
    assert lead.post("hunt", {"area": "Britain Graveyard", "minutes": 10}, "the party is stocked") == 1
    assert lead.post("rest", {"seconds": 30}) == 2
    assert isolde.latest()["tool"] == "rest" and isolde.latest()["rev"] == 2
    other = party.Party(w, "p2", "Isolde", "Roland", NAMES)
    assert other.latest() is None  # another run's party
    isolde.report(mage_snapshot() | {"in_game": True}, "mage", "hunting")
    assert lead.statuses()["Isolde"]["supplies"] == "at least 3 of each reagent"
    assert lead.companions["Isolde"] == "mage" and lead.companions["Wystan"] == "companion"
    text = lead.section((1000, 1000))
    assert text.startswith("Your party. You lead it") and "- Isolde (mage): 80/85 health, at least 3 of each reagent, " \
        "0 tiles from you, hunting" in text and "- Wystan: not heard from yet" in text
    w.close()


class Following(FakeSession):
    """A follower's session: records goals, keeps the interrupt, never dead."""

    def __init__(self, world):
        super().__init__(world)
        self.interrupt = asyncio.Event()

    async def defended(self, work):
        return await work

    async def travel_to(self, place=None, x=None, y=None, distance=2, refuse=True, pace=None):
        self.goals.append(("travel_to", place or (x, y), distance))
        return Result(True, "arrived")

    async def rest(self, seconds=30):
        self.goals.append(("rest", seconds))
        return Result(True, "rested")

    async def restock(self):
        self.goals.append(("restock",))
        return Result(True, "restocked")


def test_a_follower_carries_out_the_leaders_goals(tmp_path):
    w = World.open("local", root=tmp_path)
    lead = party.Party(w, "p1", "Roland", "Roland", NAMES)
    s = Following(w)
    f = party.Follower(s, party.Party(w, "p1", "Isolde", "Roland", NAMES))
    lead.post("travel_to", {"place": "Britain Graveyard"})
    asyncio.run(f.step())
    lead.post("hunt", {"area": "Britain Graveyard", "minutes": 12})
    asyncio.run(f.step())
    lead.post("buy", {"item": "bandage", "count": 50})  # the leader's own errand: a restock for each
    asyncio.run(f.step())
    lead.post("rest", {"seconds": 40})
    asyncio.run(f.step())
    assert s.goals == [("travel_to", "Britain Graveyard", 3), ("hunt", "Britain Graveyard", 12.0), ("restock",),
                       ("rest", 40.0)]
    lead.post("finish", {"summary": "done"})
    asyncio.run(f.step())
    assert f.finished and f.goals == 5
    w.close()


def test_a_follower_left_behind_walks_back_to_the_leader(tmp_path):
    w = World.open("local", root=tmp_path)
    lead = party.Party(w, "p1", "Roland", "Roland", NAMES)
    far = copy.deepcopy(SNAPSHOT) | {"in_game": True}
    far["player"].update(x=1040, y=1000)
    lead.report(far, "warrior")
    s = Following(w)
    asyncio.run(party.Follower(s, party.Party(w, "p1", "Isolde", "Roland", NAMES)).step())
    assert s.goals == [("travel_to", (1040, 1000), 3)]  # the snapshot is at 1000,1000: 40 tiles away
    lead.report(copy.deepcopy(SNAPSHOT) | {"in_game": True}, "warrior")
    s.goals.clear()
    asyncio.run(party.Follower(s, party.Party(w, "p1", "Isolde", "Roland", NAMES)).step())
    assert s.goals == [("rest", 5)]
    w.close()


def test_a_new_goal_from_the_leader_cuts_the_followers_goal_short(tmp_path):
    w = World.open("local", root=tmp_path)
    lead = party.Party(w, "p1", "Roland", "Roland", NAMES)
    s = Following(w)
    f = party.Follower(s, party.Party(w, "p1", "Isolde", "Roland", NAMES))

    async def go():
        stop = asyncio.Event()
        watcher = asyncio.create_task(f.watch(stop))
        lead.post("hunt", {"area": "Britain Graveyard", "minutes": 12})
        await asyncio.sleep(0.1)
        stop.set()
        watcher.cancel()

    asyncio.run(go())
    assert s.interrupt.is_set()
    w.close()


def test_the_leaders_planner_sees_the_party_and_posts_its_goals(tmp_path):
    w = World.open("local", root=tmp_path)
    lead = party.Party(w, "p1", "Brutus", "Brutus", ["Brutus", "Isolde"])
    s = FakeSession(w)

    async def restock():
        s.goals.append(("restock",))
        return Result(True, "restocked")

    async def defended(work):
        return await work

    s.restock, s.defended = restock, defended
    chat = scripted(reply(call("restock", {"why": "Isolde is low on reagents"})))
    p = planner.Planner(s, "hunt the graveyard", chat_fn=chat, party=lead)
    step = asyncio.run(p.step())
    assert step.tool == "restock" and ("restock",) in s.goals
    assert "restock" in chat.seen[0]["tools"]
    user = chat.seen[0]["messages"][1]["content"]
    assert "Your party. You lead it" in user and "- Isolde: not heard from yet" in user
    assert lead.latest()["tool"] == "restock" and lead.latest()["why"] == "Isolde is low on reagents"
    w.close()


def test_a_fleet_party_runs_one_plan(tmp_path, monkeypatch):
    f = tmp_path / "party.toml"
    f.write_text('party = true\nleader = "Roland"\n'
                 '[[agent]]\nname="Roland"\naccount="a"\npassword="a"\nport=1\ngoal="hunt the graveyard"\n'
                 '[[agent]]\nname="Isolde"\naccount="b"\npassword="b"\nport=2\n')  # a follower needs no goal
    fl = fleet.load(f)
    assert fl.party and fl.leader == "Roland"
    seen = []
    monkeypatch.setattr(fleet.subprocess, "Popen", lambda cmd, **kw: seen.append(cmd))
    (tmp_path / "run1").mkdir()
    fleet.start_brain(fl, fl.agents[1], tmp_path / "run1")
    cmd = seen[0]
    assert cmd[cmd.index("--party") + 1] == f"party-run1" and cmd[cmd.index("--leader") + 1] == "Roland"
    assert cmd[cmd.index("--party-members") + 1] == "Roland,Isolde"


def test_restock_buys_what_the_character_is_short_of(tmp_path):
    class Shop(Session):
        def __init__(self, snap):
            super().__init__(None, World(":memory:"))
            self.snapshot, self.bought, self.banked = snap, [], []

        async def snap(self, **_):
            return copy.deepcopy(self.snapshot)

        async def pack_read(self, timeout_s=10.0):
            return True

        async def bank(self, deposit="gold,loot", withdraw=""):
            self.banked.append(withdraw)
            return Result(True, "banked")

        async def buy(self, item, count, vendor_kind=None):
            self.bought.append((item, count))
            return Result(True, "bought", {"bought": count})

    mage = mage_snapshot()
    mage["player"]["gold"] = 0
    s = Shop(mage)
    r = asyncio.run(s.restock())
    assert r.ok and s.bought == [("nightshade", 47)] and s.banked == ["gold:382"]  # 3 nightshade: buy up to 50
    warrior = copy.deepcopy(SNAPSHOT)
    warrior["player"]["gold"] = 1000
    s = Shop(warrior)
    asyncio.run(s.restock())
    assert s.bought == [("bandage", 40)] and s.banked == []  # 40 bandages: up to 80, gold enough


def test_a_dead_follower_comes_back_for_its_kit_and_isnt_cut_short(tmp_path):
    """The first party run: the leader's goals cut a ghost's walk to the healer short every 2 s, and then
    its corpse was refused (danger seen there), so it played on with no bow and no bandages."""
    w = World.open("local", root=tmp_path)
    lead = party.Party(w, "p1", "Roland", "Roland", NAMES)

    class Ghost(Following):
        async def snap(self, **_):
            snap = json.loads(json.dumps(SNAPSHOT))
            snap["player"]["dead"] = True
            return snap

        async def resurrect(self):
            lead.post("rest", {"seconds": 30})  # the leader moves on meanwhile
            await asyncio.sleep(2.2)            # the watcher looks every 2 s
            self.goals.append(("resurrect", self.interrupt.is_set()))
            return Result(True, "resurrected")

        async def recover_corpse(self, wait_s=20.0, go_anyway=False):
            self.goals.append(("recover_corpse", go_anyway))
            return Result(True, "took back 12 items")

    s = Ghost(w)
    f = party.Follower(s, party.Party(w, "p1", "Wystan", "Roland", NAMES))

    async def go():
        stop = asyncio.Event()
        watcher = asyncio.create_task(f.watch(stop))
        await f.step()
        stop.set()
        watcher.cancel()

    asyncio.run(go())
    assert s.goals == [("resurrect", False), ("recover_corpse", True)]
    w.close()


def test_a_follower_left_behind_rests_beside_the_leader(tmp_path):
    w = World.open("local", root=tmp_path)
    lead = party.Party(w, "p1", "Roland", "Roland", NAMES)
    far = copy.deepcopy(SNAPSHOT) | {"in_game": True}
    far["player"].update(x=1060, y=1000)
    lead.report(far, "warrior")
    lead.post("rest", {"seconds": 60})
    s = Following(w)
    asyncio.run(party.Follower(s, party.Party(w, "p1", "Wystan", "Roland", NAMES)).step())
    assert s.goals == [("travel_to", (1060, 1000), 3), ("rest", 60.0)]
    w.close()


def test_a_member_doesnt_leave_on_its_own_while_the_party_fights():
    """cuo-e1p: in a party run the members that left ran off each its own way, lost the companions that made
    the odds good, and three of five died. With two or more beside it, Jev's leave is held back."""
    with_party = state.build(lich_fight(), set(), [], bestiary=LICH, companions=COMPANIONS)
    assert with_party.party_near == 3
    dec = policy.decide(with_party, answers("leave", target="t1", leave_now=0.9, danger=0.8), policy.Memory(),
                        policy.PolicyConfig(), now=5.0)
    assert dec.intent == "fight" and dec.note.endswith("held with the party: 3 companions beside it")
    # Badly hurt, it still goes; and alone, as before.
    hurt = lich_fight()
    hurt["player"]["hits"] = 30
    sit = state.build(hurt, set(), [], bestiary=LICH, companions=COMPANIONS)
    assert policy.decide(sit, answers("leave", target="t1", leave_now=0.9, danger=0.8), policy.Memory(),
                         policy.PolicyConfig()).intent in ("leave", "flee")
    alone = state.build(lich_fight(False), set(), [], bestiary=LICH)
    assert policy.decide(alone, answers("leave", target="t1", leave_now=0.9, danger=0.8), policy.Memory(),
                         policy.PolicyConfig()).intent == "leave"


def test_a_member_who_never_joined_is_said_to_have_not_joined(tmp_path):
    """A member's client never logged in, and the leader waited the whole run for "Corwin, not heard from"."""
    w = World.open("local", root=tmp_path)
    lead = party.Party(w, "p1", "Roland", "Roland", ["Roland", "Corwin"])
    assert "- Corwin: not heard from yet" in lead.section()
    lead.started -= 300
    assert "- Corwin: hasn't joined: no word in the 5 min since the party set out; go on without them" in lead.section()
    w.close()


def test_the_leader_waits_for_the_party_to_join_before_its_first_goal(tmp_path, monkeypatch):
    """A fleet starts each of a party's brains as its character comes in: the leader holds its first goal
    until all are heard from (or the wait runs out), fighting what attacks meanwhile."""
    monkeypatch.setattr(planner.Planner, "JOIN_WAIT_S", 3.0)
    w = World.open("local", root=tmp_path)
    lead = party.Party(w, "p1", "Roland", "Roland", NAMES[:2])
    s = Following(w)
    logged = []
    chat = scripted(reply(call("rest", {"seconds": 5, "why": "w"})), reply(call("rest", {"seconds": 5, "why": "w"})))
    p = planner.Planner(s, "hunt", chat_fn=chat, escalate=False, memory=False, party=lead, log=logged.append)

    async def go():
        stepping = asyncio.create_task(p.step())
        await asyncio.sleep(0.4)
        assert not chat.seen  # still waiting: nothing planned yet
        party.Party(w, "p1", "Isolde", "Roland", NAMES[:2]).report(copy.deepcopy(SNAPSHOT) | {"in_game": True}, "mage")
        return await stepping

    step = asyncio.run(go())
    waited = next(r for r in logged if "waited_for_party_s" in r)
    assert step.tool == "rest" and 0.3 < waited["waited_for_party_s"] < 2.5 and waited["joined"] == ["Isolde"]
    assert "- Isolde (mage)" in chat.seen[0]["messages"][1]["content"]
    asyncio.run(p.step())
    assert sum("waited_for_party_s" in r for r in logged) == 1  # once, at the start
    w.close()


def test_a_fleets_party_brains_start_as_each_character_comes_in(tmp_path, monkeypatch):
    f = tmp_path / "p.toml"
    f.write_text('name = "p"\nparty = true\nleader = "A"\n[[agent]]\nname = "A"\naccount = "a"\npassword = "a"\nport = 1\n'
                 'goal = "hunt"\n[[agent]]\nname = "B"\naccount = "b"\npassword = "b"\nport = 2\n')
    order = []

    async def log_in(fl, a):
        order.append(("login", a.name))
        return "in game as " + a.name

    class Brain:
        pid = 1

        def poll(self):
            return 0

    monkeypatch.setattr(fleet, "start_client", lambda a, out: None)
    monkeypatch.setattr(fleet, "log_in", log_in)
    monkeypatch.setattr(fleet, "start_brain", lambda fl, a, out, *_: order.append(("brain", a.name)) or Brain())
    monkeypatch.setattr(fleet, "pid_listening", lambda port: 0)
    monkeypatch.setattr(fleet, "report", lambda out: {})
    asyncio.run(fleet.run(fleet.load(f), tmp_path / "run"))
    assert order == [("login", "A"), ("brain", "A"), ("login", "B"), ("brain", "B")]
    order.clear()
    f.write_text(f.read_text().replace("party = true\nleader = \"A\"\n", "").replace('port = 2\n', 'port = 2\ngoal = "x"\n'))
    asyncio.run(fleet.run(fleet.load(f), tmp_path / "run2"))
    assert order == [("login", "A"), ("login", "B"), ("brain", "A"), ("brain", "B")]


def test_a_leader_goes_back_for_its_corpse_when_the_party_holds_the_place(tmp_path):
    """Refused its corpse for a danger the party had already killed, a leader rested ten minutes "to let
    the danger age" and the corpse decayed: no inn, no table, no second hunt."""
    w = World.open("local", root=tmp_path)
    lead = party.Party(w, "p1", "Roland", "Roland", ["Roland", "Isolde", "Wystan"])
    there = copy.deepcopy(SNAPSHOT) | {"in_game": True}
    there["player"].update(x=1382, y=1496)
    party.Party(w, "p1", "Isolde", "Roland", ["Roland", "Isolde", "Wystan"]).report(there, "mage")
    s = FakeSession(w)
    went = []

    async def recover_corpse(go_anyway=False):
        went.append(go_anyway)
        return Result(True, "took everything back")

    async def defended(work):
        return await work

    s.recover_corpse, s.defended = recover_corpse, defended
    chat = scripted(reply(call("recover_corpse", {"why": "my kit"})), reply(call("recover_corpse", {"why": "my kit"})))
    p = planner.Planner(s, "hunt the graveyard", chat_fn=chat, party=lead, memory=False, escalate=False)
    p.corpse_at = (1379, 1498, 0.0)
    asyncio.run(p.step())
    assert went == [True] and p.corpse_at is None
    assert "Isolde of your party is there now, so the place is held" in chat.seen[0]["messages"][1]["content"]
    # With nobody of the party there, and only one beside the leader, the danger seen is weighed as for anyone.
    p.corpse_at = (2000, 2000, 0.0)
    asyncio.run(p.step())
    assert went == [True, False]
    assert lead.latest()["args"]["x"] == 2000   # where, for the others
    w.close()


def test_a_party_beside_its_leader_goes_back_for_the_leaders_corpse_with_it(tmp_path):
    """Resurrected in town with the whole party beside it, a leader waited for the danger to age (the
    party had killed it) until the corpse decayed. Two or more beside it: they go back together."""
    w = World.open("local", root=tmp_path)
    names = ["Roland", "Isolde", "Wystan"]
    lead = party.Party(w, "p1", "Roland", "Roland", names)
    town = copy.deepcopy(SNAPSHOT) | {"in_game": True}
    for n, plays in (("Isolde", "mage"), ("Wystan", "archer")):
        party.Party(w, "p1", n, "Roland", names).report(town, plays)   # both where the leader stands
    s = FakeSession(w)
    went = []

    async def recover_corpse(go_anyway=False):
        went.append(go_anyway)
        return Result(True, "took everything back")

    async def defended(work):
        return await work

    s.recover_corpse, s.defended = recover_corpse, defended
    chat = scripted(reply(call("recover_corpse", {"why": "my kit"})))
    p = planner.Planner(s, "hunt the graveyard", chat_fn=chat, party=lead, memory=False, escalate=False)
    far = (town["player"]["x"] + 150, town["player"]["y"])
    p.corpse_at = (*far, 0.0)
    p.died_at_wall = time.time()
    asyncio.run(p.step())
    assert went == [True]
    assert "Isolde, Wystan of your party came through that fight alive and are with you" in chat.seen[0]["messages"][1]["content"]
    # A party that was wiped has no such escort: the two beside the leader died after it did.
    dead = copy.deepcopy(town)
    dead["player"].update(dead=True)
    for n, plays in (("Isolde", "mage"), ("Wystan", "archer")):
        m = party.Party(w, "p1", n, "Roland", names)
        m.report(dead, plays)
        m.report(town, plays)   # resurrected, beside the leader again
    p.died_at_wall = time.time() - 60
    p.corpse_at = (*far, 0.0)
    chat2 = scripted(reply(call("recover_corpse", {"why": "my kit"})))
    p.chat = chat2
    asyncio.run(p.step())
    assert went == [True, False] and "came through that fight" not in chat2.seen[0]["messages"][1]["content"]
    # Nor a party that lived by leaving: one of them was driven off from there, so the place wasn't won.
    p.died_at_wall = time.time()
    p.corpse_at = (*far, 0.0)
    for n, plays in (("Isolde", "mage"), ("Wystan", "archer")):
        m = party.Party(w, "p1", n, "Roland", names)
        m._died_at = 0.0
        m.report(town, plays, left={"x": far[0] + 3, "y": far[1], "ago_s": 30, "why": "6 coming at once"} if n == "Wystan" else None)
    chat3 = scripted(reply(call("recover_corpse", {"why": "my kit"})))
    p.chat = chat3
    asyncio.run(p.step())
    assert went == [True, False, False]
    assert "Wystan of your party had to leave that place, so it has not been won" in chat3.seen[0]["messages"][1]["content"]
    # A follower in town goes there with it.
    f = party.Follower(Following(w), party.Party(w, "p1", "Isolde", "Roland", names))
    asyncio.run(f.step())
    assert f.session.goals == [("travel_to", far, 3)] and f.doing == "going back with the leader for its corpse"
    w.close()


def test_the_leader_hears_what_the_party_killed(tmp_path):
    """Its own hunt said "0 kills" (the others killed the lich), and it hunted the emptied graveyard five
    times over instead of taking the party to the inn."""
    from types import SimpleNamespace
    from uo_brain import mates
    w = World.open("local", root=tmp_path)
    lead = party.Party(w, "p1", "Roland", "Roland", ["Roland", "Isolde"])
    s = FakeSession(w)
    link = mates.Mates(lead)
    s.pcfg = SimpleNamespace(link=link)

    async def hunt(area, minutes):
        link.story.dead_seen += ["a lich", "a skeleton"]
        return Result(True, "hunted Britain Graveyard for 0.9 min: 0 kills", {"kills": 0})

    s.hunt = hunt
    chat = scripted(reply(call("hunt", {"area": "Britain Graveyard", "minutes": 10, "why": "the goal"})))
    p = planner.Planner(s, "hunt the graveyard", chat_fn=chat, party=lead, memory=False, escalate=False)
    step = asyncio.run(p.step())
    assert step.result["result"].endswith("dead here now, killed by you and your party: a lich, a skeleton")

    async def emptied(area, minutes):
        link.story.dead_seen += ["a bone knight"]
        return Result(True, "hunted Britain Graveyard for 0.9 min: 0 kills; nothing hostile in sight for 50 seconds", {"kills": 0})

    s.hunt = emptied
    p.chat = scripted(reply(call("hunt", {"area": "Britain Graveyard", "minutes": 10, "why": "the goal"})))
    step = asyncio.run(p.step())
    assert step.result["result"].endswith("your party: a bone knight; nothing hostile is left here: this hunt is won")
    # ...and it stays in front of the leader at every step after, not in one long result it reads once (told once,
    # it hunted the emptied graveyard for the rest of two sessions with "take them to the inn" in its goal).
    quiet = copy.deepcopy(SNAPSHOT) | {"in_game": True, "mobiles": []}

    async def snap(**_):
        return copy.deepcopy(quiet)

    s.snap = snap
    assert p.won and p.won[0] == ["a bone knight"]
    chat = scripted(reply(call("rest", {"seconds": 5, "why": "wait"})))
    p.chat = chat
    asyncio.run(p.step())
    said = chat.seen[0]["messages"][1]["content"]
    assert '"the_hunt_here": "won: you and your party killed a bone knight here 0 min ago' in said
    w.close()


def test_a_member_that_joins_late_carries_out_the_goal_already_posted(tmp_path):
    """Its watcher sees the leader's goal while its first step is still looking, and sets the interrupt for
    it: left set, the goal was cut short at once and the member only kept up with the party until the next."""
    w = World.open("local", root=tmp_path)
    party.Party(w, "p1", "Roland", "Roland", NAMES).post("travel_to", {"place": "Britain"})

    class Late(Following):
        """The watcher's interrupt lands while the step is looking at the client, after its own clearing."""

        seen_set = []

        async def snap(self, **kw):
            self.interrupt.set()
            return await super().snap(**kw)

        async def travel_to(self, place=None, x=None, y=None, distance=2, refuse=True, pace=None):
            self.seen_set.append(self.interrupt.is_set())  # what the goal would find when it starts
            return await super().travel_to(place, x, y, distance, refuse, pace)

    s = Late(w)
    f = party.Follower(s, party.Party(w, "p1", "Thea", "Roland", NAMES))
    asyncio.run(f.step())
    assert s.goals == [("travel_to", "Britain", 3)] and f.rev == 1 and s.seen_set == [False]
    w.close()
