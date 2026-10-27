"""The party huddle (cuo-bih): a plan agreed on events, read by every member's own planner, remembered by each."""

import asyncio
import copy
import json
import time

from uo_brain import fleet, huddle, memory, party, planner
from uo_brain.session import Result
from uo_brain.world import World

from conftest import SNAPSHOT
from test_party import NAMES, monster
from test_planner import FakeSession, call, reply, scripted

GOAL = "Hunt the undead at the Britain graveyard with your party. Stay together."


class Picker:
    """Jev's pick stood in for: the two newest memories."""

    def __init__(self, mem):
        self.mem = mem

    async def pick(self, situation, goal, recent, **_):
        return {"orders": [], "picked": self.mem.list(limit=2)}


class Sess(FakeSession):
    def __init__(self, world, snap=None):
        super().__init__(world)
        self.snapshot = snap or copy.deepcopy(SNAPSHOT) | {"mobiles": [], "in_game": True}
        self.snapshot["agent"]["engaged"] = 0
        self.interrupt = asyncio.Event()
        self.dangers = {}

    async def snap(self, **_):
        return json.loads(json.dumps(self.snapshot))

    async def defended(self, work):
        return await work


def member(w, name, chat=None, mem=True):
    p = party.Party(w, "p1", name, "Roland", NAMES[:3])
    m = memory.Memory(w, name) if mem else None
    s = Sess(w)
    return huddle.Huddle(p, s, GOAL, m, Picker(m) if m else None, chat_fn=chat or board_chat()), p, s


def board_chat(seen=None, target="Hunt the undead at the Britain Graveyard, the lich first, all three, now"):
    async def chat(messages, tools=None, **_):
        if seen is not None:
            seen.append(messages)
        return reply(call("blackboard", {"target": target, "roles": "Roland takes the lich; Isolde and Wystan shoot it",
                                         "rally": "Two down: back to the Britain healer"}))
    return chat


def report(p, x=1000, dead=False, plays="warrior"):
    snap = copy.deepcopy(SNAPSHOT) | {"in_game": True}
    snap["player"].update(x=x, y=1000, dead=dead)
    p.report(snap, plays)


def test_a_party_with_no_plan_huddles_and_each_brings_its_own_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(huddle, "BRING_S", 1.5)
    w = World.open("local", root=tmp_path)
    seen = []
    roland, rp, rs = member(w, "Roland", board_chat(seen))
    isolde, ip, _ = member(w, "Isolde")
    wystan, wp, _ = member(w, "Wystan")
    isolde.memory.add("Died at Britain Graveyard to a lich, alone, while the others were shopping.", source="death", importance=5)
    for p, plays in ((rp, "warrior"), (ip, "mage"), (wp, "archer")):
        report(p, plays=plays)
    snap = asyncio.run(rs.snap())
    assert roland.reason(snap) == "the party has come together and has no plan yet"

    async def go():
        calling = asyncio.create_task(roland.maybe(snap, {}))
        await asyncio.sleep(0.2)
        hid = isolde.open_huddle()
        await isolde.answer(hid, snap)   # Isolde's watcher sees the huddle and says what she brings
        return hid, await calling

    hid, changed = asyncio.run(go())
    assert hid == 1 and changed
    board = isolde.board()
    assert board["rev"] == 1 and board["target"].startswith("Hunt the undead at the Britain Graveyard") and board["by"] == "Roland"
    user = seen[0][1]["content"]
    assert "Why they stopped: the party has come together and has no plan yet" in user and GOAL in user
    assert "- Isolde (mage): 60/100 health" in user and "Brings: [event, just now] Died at Britain Graveyard to a lich" in user
    assert "- Wystan (archer)" in user and "(didn't answer)" in user   # he said nothing in time: not waited for
    assert "- Roland (warrior)" in user and "Brings: (nothing from memory)" in user
    # Each who reads it keeps its own memory of what was agreed; there is no shared note.
    assert isolde.read()["rev"] == 1 and wystan.read()["rev"] == 1
    for name in ("Roland", "Isolde", "Wystan"):
        own = [m for m in memory.Memory(w, name).list(limit=10) if "huddle" in m.get("tags", [])]
        assert len(own) == 1 and "target: Hunt the undead at the Britain Graveyard" in own[0]["text"]
    assert not w.notes(None, "agreed", limit=5)
    assert isolde.read()["rev"] == 1 and memory.Memory(w, "Isolde").count() == 2  # read again: not kept twice
    # The planner's section.
    assert "- Target: Hunt the undead" in wystan.section() and "- Rally point: Two down" in wystan.section()
    assert "the plan has answered it" in wystan.section()
    w.close()


def test_no_huddle_with_a_hostile_in_reach_or_too_soon(tmp_path):
    w = World.open("local", root=tmp_path)
    roland, rp, rs = member(w, "Roland")
    fighting = copy.deepcopy(rs.snapshot)
    fighting["mobiles"] = [monster(0x300, "a lich", 24, 6)]
    assert roland.reason(fighting) == "" and huddle.Huddle.in_reach(fighting)
    engaged = copy.deepcopy(rs.snapshot)
    engaged["agent"]["engaged"] = 0x300
    assert roland.reason(engaged) == ""
    far = copy.deepcopy(rs.snapshot)
    far["mobiles"] = [monster(0x300, "a lich", 24, 16)]
    assert roland.reason(far) != ""
    # With a plan just made: nothing calls another, except a death.
    asyncio.run(roland.call("the party has come together and has no plan yet"))
    quiet = asyncio.run(rs.snap())
    failed = planner.PlanStep("hunt", {"area": "Britain Graveyard"}, {"ok": False, "result": "could not get to Britain Graveyard"})
    assert roland.reason(quiet, failed) == ""
    rp.call("down", about="Isolde")
    assert roland.reason(quiet) == "Isolde died since the plan was made"
    w.close()


def test_what_calls_a_huddle_once_the_plan_has_stood_a_while(tmp_path):
    w = World.open("local", root=tmp_path)
    roland, rp, rs = member(w, "Roland")
    asyncio.run(roland.call("no plan yet"))
    w.db.execute("UPDATE party_board SET t = t - 400")
    w.db.commit()
    quiet = asyncio.run(rs.snap())
    ok = planner.PlanStep("hunt", {"area": "Britain Graveyard"}, {"ok": True, "result": "hunted Britain Graveyard for 5 min: 4 kills"})
    assert roland.reason(quiet, ok) == ""  # a hunt that went as planned: carry on
    left = planner.PlanStep("hunt", {}, {"ok": True, "result": "hunted Britain Graveyard for 1 min: 0 kills, stopped because "
                                                              "left the area: 3 coming at once"})
    assert roland.reason(quiet, left).startswith("Roland's goal for the plan went wrong: hunted Britain Graveyard")
    rp.call("badly_hurt", hits=10, hits_max=95)
    assert roland.reason(quiet, ok) == "Roland came near death in the last fight"
    w.db.execute("DELETE FROM party_calls")
    rp.call("out_of_arrows")
    assert roland.reason(quiet, ok) == "Roland is out of arrows"
    w.db.execute("DELETE FROM party_calls")
    rs.dangers = {"Britain Graveyard": {1: "lich lord"}}
    assert roland.reason(quiet, ok) == "something the plan hadn't weighed was seen: lich lord at Britain Graveyard"
    assert roland.reason(quiet, ok) == ""  # said once
    w.close()


def test_two_calling_at_once_make_one_plan_and_a_new_plan_cuts_a_goal_short(tmp_path, monkeypatch):
    monkeypatch.setattr(huddle, "BRING_S", 0.6)
    w = World.open("local", root=tmp_path)
    roland, _, rs = member(w, "Roland")
    isolde, _, is_ = member(w, "Isolde")

    async def go():
        snap = await rs.snap()
        a, b = await asyncio.gather(roland.maybe(snap, {}), isolde.maybe(snap, {}))
        return a, b

    a, b = asyncio.run(go())
    assert a and b and roland.board()["rev"] == 1 and roland.called + isolde.called == 1
    # A member in the middle of a goal: its watcher sees a new plan and cuts the goal short, but not in a fight.
    wystan, _, ws = member(w, "Wystan")

    async def watch(snap_edit=None):
        if snap_edit:
            snap_edit(ws.snapshot)
        ws.interrupt.clear()
        stop = asyncio.Event()
        task = asyncio.create_task(wystan.watch(stop, every_s=0.05))
        await asyncio.sleep(0.2)
        stop.set()
        task.cancel()
        return ws.interrupt.is_set()

    assert asyncio.run(watch(lambda s: s.update(mobiles=[monster(0x300, "a lich", 24, 3)]))) is False
    assert asyncio.run(watch(lambda s: s.update(mobiles=[]))) is True
    wystan.read()
    assert asyncio.run(watch()) is False  # read: nothing new to cut a goal short for
    w.close()


def test_every_member_plans_for_itself_with_the_plan_in_front_of_it(tmp_path, monkeypatch):
    monkeypatch.setattr(huddle, "BRING_S", 0.3)
    w = World.open("local", root=tmp_path)
    h, p, s = member(w, "Isolde", mem=False)
    report(p, plays="mage")
    report(party.Party(w, "p1", "Roland", "Roland", NAMES[:3]), x=1004)
    chat = scripted(reply(call("hunt", {"area": "Britain Graveyard", "minutes": 10, "why": "the plan"})))
    plan = planner.Planner(s, GOAL, chat_fn=chat, escalate=False, memory=False, party=p, huddle=h)
    step = asyncio.run(plan.step())
    user = chat.seen[0]["messages"][1]["content"]
    assert "Your party's plan, agreed in a huddle 0 min ago" in user and "- Roles: Roland takes the lich" in user
    assert "each of you plans for yourself" in user and "You lead it" not in user
    assert "- Roland (warrior): 60/100 health" in user
    assert step.tool == "hunt" and p.latest() is None  # its goal is its own: nothing is posted for others to follow
    restock = next(t for t in plan.tools if t["function"]["name"] == "restock")["function"]["description"]
    assert restock.startswith("Buy what the character is short of") and plan.summary()["huddles_called"] == 1
    w.close()


def test_a_fleet_file_asks_for_a_huddle(tmp_path):
    f = tmp_path / "p.toml"
    f.write_text('name = "p"\nparty = true\nleader = "A"\nplan = "huddle"\n[[agent]]\nname = "A"\naccount = "a"\npassword = "a"\n'
                 'port = 1\ngoal = "hunt together"\n[[agent]]\nname = "B"\naccount = "b"\npassword = "b"\nport = 2\n')
    fl = fleet.load(f)
    assert fl.plan == "huddle" and [a.goal for a in fl.agents] == ["hunt together", "hunt together"]
    started = []
    import subprocess
    real = subprocess.Popen
    try:
        subprocess.Popen = lambda cmd, **kw: started.append(cmd)
        fleet.start_brain(fl, fl.agents[1], tmp_path)
    finally:
        subprocess.Popen = real
    cmd = started[0]
    assert cmd[cmd.index("--party-plan") + 1] == "huddle" and cmd[cmd.index("session") + 1] == "hunt together"


# ---------------------------------------------------------------- found in review (2026-10-11)

def test_a_death_calls_one_huddle_not_one_at_every_step(tmp_path, monkeypatch):
    """While a member was a ghost every planner step of every other member wrote a new blackboard."""
    monkeypatch.setattr(huddle, "BRING_S", 0.2)
    w = World.open("local", root=tmp_path)
    calls = []
    roland, rp, rs = member(w, "Roland", board_chat(calls))
    isolde, ip, _ = member(w, "Isolde", board_chat(calls))
    _, wp, _ = member(w, "Wystan", board_chat(calls))
    report(rp), report(ip, plays="mage"), report(wp, plays="archer")
    snap = asyncio.run(rs.snap())
    assert asyncio.run(roland.maybe(snap, {})) and len(calls) == 1
    time.sleep(0.02)
    report(wp, plays="archer", dead=True)   # Wystan dies after the plan was made
    assert roland.reason(snap) == "Wystan died since the plan was made"
    assert asyncio.run(isolde.maybe(snap, {})) and len(calls) == 2
    for who in (roland, isolde, roland, isolde):   # still a ghost, reporting every 5 s: the new plan has answered it
        report(wp, plays="archer", dead=True)
        assert who.reason(snap) == "" and not asyncio.run(who.maybe(snap, {}))
    assert len(calls) == 2 and roland.board()["rev"] == 2
    # Alive again, and killed again: that is a new death.
    report(wp, plays="archer")
    time.sleep(0.02)
    report(wp, plays="archer", dead=True)
    assert roland.reason(snap) == "Wystan died since the plan was made"
    w.close()


def test_a_goal_cut_short_by_a_new_plan_doesnt_cut_the_next_one(tmp_path, monkeypatch):
    """Nothing cleared the interrupt in a huddle party (only a follower's step did): after the first change
    of plan every goal ended the moment it began."""
    monkeypatch.setattr(huddle, "BRING_S", 0.2)
    w = World.open("local", root=tmp_path)
    h, p, s = member(w, "Isolde", mem=False)
    report(p, plays="mage")
    asyncio.run(h.maybe(asyncio.run(s.snap()), {}))
    rested = []

    async def rest(seconds=30):
        rested.append(s.interrupt.is_set())
        return Result(True, "rested")

    s.rest = rest
    s.interrupt.set()   # as Huddle.watch leaves it when the plan changes
    plan = planner.Planner(s, GOAL, chat_fn=scripted(reply(call("rest", {"seconds": 30, "why": "wait"}))),
                           escalate=False, memory=False, party=p, huddle=h)
    plan.party_waited = plan.pack_checked = True
    asyncio.run(plan.step())
    assert rested == [False]
    w.close()


# ---------------------------------------------------------------- who goes (cuo-cym.14)

class GoNow:
    """System one stood in for on whether the plan sends the party in now: yes unless its target says to hold."""
    name = "decision/test"

    def __init__(self):
        self.asked = 0

    async def ask(self, state, questions):
        from uo_brain import judge
        self.asked += 1
        out = judge.Answers(model="test", cost=0.00001)
        out.nouls["go_now"] = 0.1 if "hold" in state["plan"]["target"].lower() else 0.9
        return out


def going_chat(going, target="Hunt the undead at the Britain Graveyard, the lich first, all three, now", seen=None):
    async def chat(messages, tools=None, **_):
        if seen is not None:
            seen.append(tools)
        return reply(call("blackboard", {"target": target, "roles": "Roland takes the lich", "rally": "Two down: back to the "
                                         "Britain healer", "going": going}))
    return chat


def test_the_plan_names_who_goes(tmp_path, monkeypatch):
    """The blackboard's fourth entry: who goes into the hunt, for the rally that waits for them at its edge."""
    monkeypatch.setattr(huddle, "BRING_S", 0.2)
    w = World.open("local", root=tmp_path)
    tools = []
    roland, rp, rs = member(w, "Roland", going_chat(["Wystan", "Roland", "Gandalf"], seen=tools), mem=False)
    isolde, _, _ = member(w, "Isolde", mem=False)
    assert roland.going() is None  # no plan yet
    out = asyncio.run(roland.call("no plan yet"))
    # The model chooses among the party's own names, and a name it made up is nobody's.
    names = tools[0][0]["function"]["parameters"]["properties"]["going"]
    assert names["items"]["enum"] == NAMES[:3] and "going" in tools[0][0]["function"]["parameters"]["required"]
    assert out["going"] == ["Roland", "Wystan"] and isolde.board()["going"] == ["Roland", "Wystan"]
    # Every member reads it, for the hunt the plan names and no other.
    assert isolde.going() == ["Roland", "Wystan"] and isolde.going("Britain Graveyard") == ["Roland", "Wystan"]
    assert isolde.going("britain graveyards") == ["Roland", "Wystan"] and isolde.going("Covetous") is None
    assert "- Going in, together: Roland, Wystan\n" in isolde.section()
    # The next plan is shown who went under the last one.
    seen = []
    again = huddle.Huddle(rp, rs, GOAL, chat_fn=board_chat(seen))
    asyncio.run(again.call("Wystan died since the plan was made"))
    assert "rally: Two down: back to the Britain healer; going: Roland, Wystan" in seen[0][1]["content"]
    # A plan that names nobody (not to fight now, or a model that left it out) says nothing of who goes.
    assert again.board()["rev"] == 2 and again.board()["going"] == [] and again.going("Britain Graveyard") is None
    assert "Going in" not in again.section()
    w.close()


def test_who_goes_is_read_as_loosely_as_it_comes():
    assert huddle.named(["Roland", "isolde"], NAMES) == ["Roland", "Isolde"]
    assert huddle.named('["Thea", "Corwin"]', NAMES) == ["Thea", "Corwin"]      # a list sent as one string
    assert huddle.named("Roland, Isolde and Wystan", NAMES) == ["Roland", "Isolde", "Wystan"]
    assert huddle.named("all five", NAMES) == [] and huddle.named(None, NAMES) == []
    assert huddle.named("Rolando and Theatre", NAMES) == []  # a name inside another word isn't it


def test_a_store_from_before_the_plan_named_who_goes(tmp_path):
    w = World.open("local", root=tmp_path)
    w.db.execute("CREATE TABLE party_board (id INTEGER PRIMARY KEY, party TEXT NOT NULL, rev INTEGER NOT NULL, target TEXT "
                 "NOT NULL, roles TEXT, rally TEXT, reason TEXT, by TEXT NOT NULL, t REAL NOT NULL)")
    w.db.execute("INSERT INTO party_board (party, rev, target, roles, rally, reason, by, t) VALUES ('p1', 1, 'Hunt the "
                 "undead at the Britain Graveyard', '', '', 'no plan yet', 'Roland', ?)", (time.time(),))
    w.db.commit()
    h, _, _ = member(w, "Isolde", mem=False)
    assert h.board()["going"] == [] and h.going("Britain Graveyard") is None
    huddle.ensure(w)  # a second brain starting: nothing to add
    asyncio.run(member(w, "Roland", going_chat(NAMES), mem=False)[0].call("no plan yet"))
    assert h.going("Britain Graveyard") == NAMES[:3]
    w.close()


def test_a_member_on_an_errand_of_its_own_says_so(tmp_path, monkeypatch):
    """A huddle's rally doesn't wait for one who is at the shop or going back for its corpse: its planner says
    so while such a goal is going on (the report's `busy`), as a follower does of its own business."""
    monkeypatch.setattr(huddle, "BRING_S", 0.2)
    w = World.open("local", root=tmp_path)
    h, p, s = member(w, "Isolde", mem=False)
    report(p, plays="mage")
    asyncio.run(h.maybe(asyncio.run(s.snap()), {}))
    during = []

    async def restock():
        during.append(plan.on_errand)
        return Result(True, "restocked")

    async def rest(seconds=30):
        during.append(plan.on_errand)
        return Result(True, "rested")

    s.restock, s.rest = restock, rest
    plan = planner.Planner(s, GOAL, chat_fn=scripted(reply(call("restock", {"why": "no reagents"})),
                                                     reply(call("rest", {"seconds": 30, "why": "wait"}))),
                           escalate=False, memory=False, party=p, huddle=h)
    plan.party_waited = plan.pack_checked = True
    asyncio.run(plan.step())
    assert during == [True] and not plan.on_errand
    asyncio.run(plan.step())
    assert during == [True, False]
    w.close()


def test_a_members_own_trip_into_the_plans_hunt_is_one_to_the_rally_point(tmp_path, monkeypatch):
    """With a planner each and the party forming up, a trip of a member's own into the hunt the plan sends it
    to ends at the party's rally point (Session.to_the_rally); any other trip is made as before."""
    from uo_brain.session import Session
    monkeypatch.setattr(huddle, "BRING_S", 0.2)
    w = World.open("local", root=tmp_path)
    w.insert("spawns", {"area": "Britain Graveyard", "map": "Felucca", "x": 1369, "y": 1475, "walk_range": 30,
                        "creature": "Skeleton", "max_count": 3, "spawner": "a", "source": "modernuo:Graveyards.json",
                        "last_seen": "2026-10-14T00:00:00+00:00"})
    w.add_place("graveyard", "Britain Graveyard", 1384, 1492, source="test")  # 17 tiles from its spawn's centre
    w.add_place("healer", "Britain healer", 1472, 1610, source="test")

    class Trip(Sess):
        hunt_spot, resolve = Session.hunt_spot, Session.resolve

        def __init__(self, world):
            super().__init__(world)
            self.rally = object()  # the party forms up
            self.to_rally = []

        async def to_the_rally(self, name, cx, cy, radius):
            self.to_rally.append((name, cx, cy, radius))
            return Result(True, f"at the party's rally point at the edge of {name}")

    roland, _, _ = member(w, "Roland", going_chat(NAMES[:3]), mem=False)
    asyncio.run(roland.call("the party has come together and has no plan yet"))
    h, p, _ = member(w, "Isolde", mem=False)
    report(p, plays="mage")
    assert h.hunt_area() == "Britain Graveyard"  # the longest of the store's areas named in the target

    def goes(place, who=h, party_=p, rally_=True):
        s = Trip(w)
        s.rally = object() if rally_ else None
        plan = planner.Planner(s, GOAL, chat_fn=scripted(reply(call("travel_to", {"place": place, "why": "with them"}))),
                               escalate=False, memory=False, party=party_, huddle=who)
        plan.party_waited = plan.pack_checked = True
        board = (who or h).board()
        if board is not None:
            plan.steered.add(board["rev"])  # already sent on this plan's hunt once: its planner chooses (cuo-cym.14)
        asyncio.run(plan.step())
        return s.to_rally, [g for g in s.goals if g[0] == "travel_to"]

    # To the graveyard by its name, or to a tile within its edge: as far as the rally point.
    assert goes("Britain Graveyard") == ([("Britain Graveyard", 1369, 1475, 15)], [])
    assert goes("1380,1495") == ([("Britain Graveyard", 1369, 1475, 15)], [])
    # Elsewhere, or with the party not forming up: the trip as it was.
    assert goes("Britain healer") == ([], [("travel_to", "Britain healer")])
    assert goes("Britain Graveyard", rally_=False) == ([], [("travel_to", "Britain Graveyard")])
    # One the plan doesn't send there: the trip as it was.
    others = huddle.Huddle(party.Party(w, "p1", "Isolde", "Roland", NAMES[:3]), Sess(w), GOAL, chat_fn=going_chat(["Roland"]))
    asyncio.run(others.call("Isolde is out of reagents"))
    assert others.going("Britain Graveyard") == ["Roland"]
    assert goes("Britain Graveyard", who=others) == ([], [("travel_to", "Britain Graveyard")])
    w.close()


def test_a_member_the_plan_sends_into_its_hunt_goes_without_its_planner_being_asked(tmp_path, monkeypatch):
    """cuo-cym.14: with memory on, members' planners asked again after a plan that sent all five chose rest, the
    healer and the shops. A member the plan names goes into the hunt once for each plan, as a follower would."""
    monkeypatch.setattr(huddle, "BRING_S", 0.2)
    w = World.open("local", root=tmp_path)
    w.insert("spawns", {"area": "Britain Graveyard", "map": "Felucca", "x": 1369, "y": 1475, "walk_range": 30,
                        "creature": "Skeleton", "max_count": 3, "spawner": "a", "source": "modernuo:Graveyards.json",
                        "last_seen": "2026-10-19T00:00:00+00:00"})
    roland, _, _ = member(w, "Roland", going_chat(NAMES[:2]), mem=False)  # Roland and Isolde go; Wystan doesn't
    asyncio.run(roland.call("the party has come together and has no plan yet"))
    go_now = GoNow()

    def step(name, rally=True, hits=None, bandages=None, steered=()):
        h, p, s = member(w, name, mem=False)
        s.judge = go_now
        s.rally = object() if rally else None

        async def hunt(area, minutes, **_):  # a party's hunt is formed up for (rally_key)
            s.goals.append(("hunt", area, minutes))
            return Result(True, "hunted", {"kills": 0})

        s.hunt = hunt
        if hits is not None:
            s.snapshot["player"]["hits"] = hits
        if bandages is not None:
            s.snapshot["player"]["supplies"]["bandages"] = bandages
        chat = scripted(reply(call("rest", {"seconds": 30, "why": "my own idea"})))
        plan = planner.Planner(s, GOAL, chat_fn=chat, escalate=False, memory=False, party=p, huddle=h)
        plan.party_waited = plan.pack_checked = True
        plan.steered.update(steered)
        asyncio.run(plan.step())
        return [st.tool for st in plan.history], len(chat.seen), plan

    goals, asked, plan = step("Isolde")
    assert (goals, asked) == (["hunt"], 0) and plan.history[-1].args["area"] == "Britain Graveyard"
    assert plan.history[-1].args["minutes"] == planner.STEER_MINUTES and plan.steered == {roland.board()["rev"]}
    # The plan it was sent on counts as read: the plan's watcher had cut the hunt short two seconds in, live.
    assert plan.huddle.seen_rev == roland.board()["rev"]
    # Once for each plan: after that its planner chooses.
    assert step("Isolde", steered={roland.board()["rev"]})[:2] == (["rest"], 1)
    # Not one the plan doesn't send, not without a rally, not when hurt. Out of bandages it still goes: they are its
    # healing, and a fighter fights without them (only a mage without reagents is kept back).
    assert step("Wystan")[:2] == (["rest"], 1)
    assert step("Isolde", rally=False)[:2] == (["rest"], 1)
    hits_max = SNAPSHOT["player"]["hits_max"]
    assert step("Isolde", hits=hits_max // 3)[:2] == (["rest"], 1)
    assert step("Isolde", bandages=0)[:2] == (["hunt"], 0)
    # Not by a plan that holds the party back, whoever it lists as going (live: "Hold at the western edge... No
    # entry yet" with all five listed, and those sent in by the list alone walked into the lich).
    asyncio.run(member(w, "Roland", going_chat(NAMES[:2], target="Hold at the western edge of the Britain Graveyard. "
                                                             "No entry yet."), mem=False)[0].call("Wystan died"))
    assert step("Isolde")[:2] == (["rest"], 1)
    w.close()


def test_the_first_plan_agreed_inside_a_step_sends_its_members_in_at_once(tmp_path, monkeypatch):
    """cuo-cym.14, live run 20261019-150653: the first plan is agreed inside the members' first step (huddle.maybe in
    the planner's think), and asked then, two of five rested a minute and went in 18 s behind the others. A hunt a
    member chose itself under the plan counts as the plan's: it isn't sent in again afterwards."""
    monkeypatch.setattr(huddle, "BRING_S", 0.2)
    w = World.open("local", root=tmp_path)
    w.insert("spawns", {"area": "Britain Graveyard", "map": "Felucca", "x": 1369, "y": 1475, "walk_range": 30,
                        "creature": "Skeleton", "max_count": 3, "spawner": "a", "source": "modernuo:Graveyards.json",
                        "last_seen": "2026-10-19T00:00:00+00:00"})
    h, p, s = member(w, "Isolde", going_chat(NAMES[:3]), mem=False)
    report(p, plays="mage")
    s.rally, s.judge = object(), GoNow()

    async def hunt(area, minutes, **_):
        s.goals.append(("hunt", area, minutes))
        return Result(True, "hunted", {"kills": 0})

    s.hunt = hunt
    chat = scripted(reply(call("rest", {"seconds": 60, "why": "watch first"})),
                    reply(call("hunt", {"area": "Britain Graveyard", "minutes": 5, "why": "my own"})),
                    reply(call("rest", {"seconds": 30, "why": "after"})))
    plan = planner.Planner(s, GOAL, chat_fn=chat, escalate=False, memory=False, party=p, huddle=h)
    plan.party_waited = plan.pack_checked = True
    asyncio.run(plan.step())
    assert h.board()["rev"] == 1 and plan.history[-1].tool == "hunt" and len(chat.seen) == 0  # agreed, then sent in
    # A second member chose the hunt itself under the plan: not sent in again once it is over.
    h2, p2, s2 = member(w, "Wystan", mem=False)
    s2.rally, s2.hunt, s2.judge = object(), hunt, GoNow()
    plan2 = planner.Planner(s2, GOAL, chat_fn=chat, escalate=False, memory=False, party=p2, huddle=h2)
    plan2.party_waited = plan2.pack_checked = True
    plan2.steered.add(99)  # some earlier plan
    chat_turns = len(chat.seen)
    asyncio.run(plan2.carry_out("hunt", {"area": "Britain Graveyard", "minutes": 5, "why": "my own"}))
    assert 1 in plan2.steered
    asyncio.run(plan2.step())
    assert len(chat.seen) == chat_turns + 1  # its planner was asked: no second hunt sent by the plan
    w.close()


def test_a_member_doesnt_hunt_the_plans_area_while_the_plan_holds_the_party_back(tmp_path, monkeypatch):
    """cuo-cym.14, live run 20261019-162307: the plan said to enter only after a scout, and the bard's own planner
    hunted the graveyard alone, the first in by 15 s."""
    monkeypatch.setattr(huddle, "BRING_S", 0.2)
    w = World.open("local", root=tmp_path)
    w.insert("spawns", {"area": "Britain Graveyard", "map": "Felucca", "x": 1369, "y": 1475, "walk_range": 30,
                        "creature": "Skeleton", "max_count": 3, "spawner": "a", "source": "modernuo:Graveyards.json",
                        "last_seen": "2026-10-19T00:00:00+00:00"})
    from uo_brain.session import Session
    held = "Hold at the western edge of the Britain Graveyard. Enter only after a fresh scout shows no lich."
    roland, _, _ = member(w, "Roland", going_chat(NAMES[:3], target=held), mem=False)
    asyncio.run(roland.call("the party has come together and has no plan yet"))
    h, p, s = member(w, "Corwin", mem=False)
    s.rally, s.judge = object(), GoNow()
    s.hunt_spot = lambda area: Session.hunt_spot(s, area)
    hunted = []

    async def hunt(area, minutes, **_):
        hunted.append(area)
        return Result(True, "hunted", {"kills": 0})

    s.hunt = hunt
    plan = planner.Planner(s, GOAL, chat_fn=scripted(), escalate=False, memory=False, party=p, huddle=h)
    plan.party_waited = plan.pack_checked = True
    step = asyncio.run(plan.carry_out("hunt", {"area": "Britain Graveyard", "minutes": 5, "why": "my own idea"}))
    assert not hunted and not step.result["ok"] and "holds the party back" in step.result["result"]
    # Somewhere else is its own business; and a plan that sends them in lets it hunt.
    asyncio.run(plan.carry_out("hunt", {"area": "Covetous", "minutes": 5, "why": "elsewhere"}))
    assert hunted == ["Covetous"]
    asyncio.run(member(w, "Roland", going_chat(NAMES[:3]), mem=False)[0].call("Wystan died"))
    asyncio.run(plan.carry_out("hunt", {"area": "Britain Graveyard", "minutes": 5, "why": "now"}))
    assert hunted == ["Covetous", "Britain Graveyard"]
    w.close()


class Looks(GoNow):
    """System one stood in for: the plan holds them back (go_now no), for a look when its target says to scout."""

    async def ask(self, state, questions):
        from uo_brain import judge
        self.asked += 1
        out = judge.Answers(model="test", cost=0.00001)
        target = state["plan"]["target"].lower()
        out.nouls.update({"go_now": 0.1, "look": 0.9 if "scout" in target else 0.1,
                          "clear": 0.8 if "nothing hostile" in state.get("seen", "") else 0.2})
        return out


def test_a_plan_that_holds_for_a_look_sends_its_members_to_the_edge_to_look(tmp_path, monkeypatch):
    """cuo-cym.14: with memory on, every plan said "enter only after a fresh scout", and nobody was sent. Now those it
    names go to the edge together to look (the rally looks, rally.Rally.look_from_edge); their own hunts of it are
    still refused while it holds; and after LOOKS looks that didn't send them in, the hold is the planners' again."""
    monkeypatch.setattr(huddle, "BRING_S", 0.2)
    w = World.open("local", root=tmp_path)
    w.insert("spawns", {"area": "Britain Graveyard", "map": "Felucca", "x": 1369, "y": 1475, "walk_range": 30,
                        "creature": "Skeleton", "max_count": 3, "spawner": "a", "source": "modernuo:Graveyards.json",
                        "last_seen": "2026-10-19T00:00:00+00:00"})
    scout = "Hold at the Britain Graveyard's west edge; enter only after a fresh scout shows the lich alone."
    roland, _, _ = member(w, "Roland", going_chat(NAMES[:2], target=scout), mem=False)
    asyncio.run(roland.call("the party has come together and has no plan yet"))
    looks = Looks()

    from uo_brain.session import Session

    def step(name):
        h, p, s = member(w, name, mem=False)
        s.judge, s.rally = looks, object()
        s.hunt_spot = lambda area: Session.hunt_spot(s, area)

        async def hunt(area, minutes, **_):
            s.goals.append(("hunt", area, minutes))
            return Result(True, "hunted", {"kills": 0})

        s.hunt = hunt
        chat = scripted(reply(call("hunt", {"area": "Britain Graveyard", "minutes": 5, "why": "my own idea"})))
        plan = planner.Planner(s, GOAL, chat_fn=chat, escalate=False, memory=False, party=p, huddle=h)
        plan.party_waited = plan.pack_checked = True
        asyncio.run(plan.step())
        return plan, h, len(chat.seen)

    plan, h, asked = step("Isolde")
    rev = roland.board()["rev"]
    assert asked == 0 and plan.looking == rev and plan.history[-1].tool == "hunt"
    assert "to the edge of Britain Graveyard, to look" in plan.history[-1].args["why"]
    assert asyncio.run(plan.held_back("Britain Graveyard")) == ""  # the rally holds it at the edge
    # Wystan isn't named: his own hunt of it is refused while the plan holds.
    plan, _, asked = step("Wystan")
    assert asked == 1 and plan.looking is None and plan.history[-1].result["result"].startswith(
        "the party's plan holds the party back from Britain Graveyard")
    # The look from the edge: system one on what they see; one that doesn't send them in calls a huddle at once.
    assert asyncio.run(h.clear_to_go(looks, "Britain Graveyard", "nothing hostile in view. The party here: Isolde.")) == 0.8
    assert asyncio.run(h.clear_to_go(looks, "Britain Graveyard", "a lich (14 tiles off)")) == 0.2
    # After LOOKS looks that didn't send them in, the plan's hold is left to each planner.
    assert asyncio.run(h.holds_for_a_look(looks, "Britain Graveyard"))
    h.looked.extend([(time.time(), "Britain Graveyard")] * huddle.LOOKS)
    assert not asyncio.run(h.holds_for_a_look(looks, "Britain Graveyard"))
    h.looked.clear()
    out = asyncio.run(h.seen_from_edge("Britain Graveyard", "a lich (14 tiles off)"))
    assert out["reason"].startswith("the party looked from the edge of Britain Graveyard as the plan said") \
        and out["reason"].endswith("a lich (14 tiles off)") and h.board()["rev"] == rev + 1 and len(h.looked) == 1
    # Not a plan that holds them for something else (a raise, a rest).
    rest = "Hold at the Britain healer until Wystan is raised; then the graveyard."
    asyncio.run(member(w, "Roland", going_chat(NAMES[:2], target=rest), mem=False)[0].call("Wystan died"))
    assert not asyncio.run(member(w, "Isolde", mem=False)[0].holds_for_a_look(looks, "Britain Graveyard"))
    w.close()
