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
