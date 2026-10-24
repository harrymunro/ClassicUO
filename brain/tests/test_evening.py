"""A hamlet's evenings (cuo-m3n.5): the day's clock, the neighbours' table in its hamlet scene, who keeps its turns,
the `evening` goal, and what each keeps of it."""

import asyncio
from types import SimpleNamespace

from uo_brain import fleet, hamlet, memory, planner, table
from uo_brain.hamlet import Hamlet
from uo_brain.session import Result
from uo_brain.world import World

from test_hamlet import COVE, cove_store
from test_planner import FakeSession, call, reply, scripted, world
from test_table import Rpc, talker

HEALERS = {"name": "Cove healer", "x": 2245, "y": 1231, "words": "the healer's house"}
NEIGHBOURS = [{"name": "Wenna", "trade": "hunter", "home_words": "the small house"},
              {"name": "Tamsin", "trade": "carrier", "home_words": "the room above the provisioner's"}]


def maren(began=0.0):
    return Hamlet("Cove", COVE, 10, "Maren", (2245, 1231), "the healer's house", "tends the hurt", NEIGHBOURS,
                  gather=HEALERS, day_s=3600, evening_s=900, began=began, run="run1")


def neighbourly(seen=None):
    """talker's lines, and of an evening a neighbour named in what is kept."""
    inner = talker(seen)

    async def chat(messages, tools=None, **kw):
        if tools[0]["function"]["name"] == "keep":
            return reply(call("keep", {"lessons": ["Go to the woods early tomorrow."],
                                       "companions": ["Tamsin saves every coin for a cart and a horse."], "tactic": ""}))
        return await inner(messages, tools=tools, **kw)

    return chat


def neighbour(w, name, chat=None, mem=True):
    hood = SimpleNamespace(world=w, me=name, members=["Maren", "Wenna", "Tamsin"], party="hamlet:Cove:run1")
    return table.Table(hood, Rpc(), {"temperament": f"{name}'s own"}, None, memory.Memory(w, name) if mem else None,
                       hamlet.DayStory(), chat_fn=chat or neighbourly(), pace=False, scene="hamlet", place="the healer's house")


def test_a_day_has_its_evening_and_each_turn_says_the_time_of_day():
    h = maren(began=1000.0)
    assert h.day_of(1000.0) == (1, 0.0) and h.day_of(1000.0 + 3600 + 60)[0] == 2
    assert not h.is_evening(1000.0 + 44 * 60) and h.is_evening(1000.0 + 46 * 60)
    assert h.evening_key(1000.0 + 50 * 60) == "evening 1" and h.evening_key(1000.0 + 110 * 60) == "evening 2"
    assert h.when(1000.0 + 5 * 60) == "day 1, morning; evening (the gathering at the healer's house) in about 40 minutes"
    assert h.when(1000.0 + 44.5 * 60).endswith("in under a minute")  # it said "in about 0 minutes"
    assert h.until_evening(1000.0 + 43 * 60) == 120 and h.until_evening(1000.0 + 50 * 60) == 0
    assert h.when(1000.0 + 30 * 60).startswith("day 1, afternoon")
    assert h.when(1000.0 + 50 * 60) == ("day 1, evening: the neighbours gather at the healer's house to talk the day "
                                        "over (the `evening` goal), for about 10 more minutes")
    words = h.words()
    assert "Of an evening the neighbours gather at the healer's house" in words and "A day here is 60 minutes" in words
    assert Hamlet.from_json(h.to_json()) == h and Hamlet.from_json(h.to_json()).run == "run1"
    plain = Hamlet("Cove", COVE)
    assert not plain.is_evening(10**9) and "gather" not in plain.to_json() and "evening" not in plain.words()


def test_the_fleet_reads_the_gathering_and_gives_every_resident_one_clock(tmp_path):
    cove_store(tmp_path)
    f = fleet.load(fleet.BRAIN_DIR / "fleets" / "hamlet-cove-evenings.toml")
    assert f.hamlet["gather"] == "Cove healer" and f.hamlet["day_minutes"] == 60 and f.hours == 3.0
    f.hamlet = {**f.hamlet, "lands": [], "day_minutes": 30, "evening_minutes": 10}  # a store with no spawns
    h = fleet.hamlet_of(f, root=tmp_path)
    assert h["gather"] == {"name": "Cove healer", "x": 2245, "y": 1231, "words": "the healer's house"}
    known = list(h["known"].values())
    assert {k.began for k in known} == {h["began"]} and all(k.day_s == 1800 and k.evening_s == 600 for k in known)


def test_the_first_at_the_gathering_keeps_the_turns_and_a_late_claim_wins_once_the_window_is_over(tmp_path):
    w = World.open("local", root=tmp_path)
    m, wn = neighbour(w, "Maren"), neighbour(w, "Wenna")
    assert m.claim("evening 1", 180) and not wn.claim("evening 1", 180) and m.claim("evening 1", 180)
    assert wn.claim("evening 2", 180)  # another evening: another host
    w.db.execute("UPDATE party_table SET t = t - 1000 WHERE kind = 'claim'")
    assert wn.claim("evening 1", 180)  # the first host never held it: a claim long gone is no claim
    w.close()


def test_the_neighbours_talk_their_day_over_and_keep_what_they_learned_of_each_other(tmp_path):
    w = World.open("local", root=tmp_path)
    seen = []
    chat = neighbourly(seen)
    seats = {n: neighbour(w, n, chat) for n in ("Maren", "Wenna", "Tamsin")}
    seats["Wenna"].story.planner = SimpleNamespace(history=[
        planner.PlanStep("hunt", {"area": "south-west of Cove Cemetery", "why": "x"},
                         {"ok": True, "result": "4 kills"}, t=seats["Wenna"].story.since + 1)])

    async def go():
        sitting = [asyncio.create_task(seats[n].sit(wait_s=5)) for n in seats]
        held = await seats["Maren"].host(list(seats))
        await asyncio.gather(*sitting)
        return held, await seats["Wenna"].reflect(1)

    held, kept = asyncio.run(go())
    assert held["lines"] >= 8
    system = seen[0][0]["content"]
    assert "neighbours at the healer's house in the small hamlet" in system and "Swearing" not in system
    hers = next(m for m in seen if "Who you speak for: Wenna" in m[1]["content"])[1]["content"]
    assert "Wenna's own day: hunt(area=south-west of Cove Cemetery): done, 4 kills" in hers
    assert sum("what they mean to do tomorrow" in m[1]["content"] for m in seen) == 3
    mine = memory.Memory(w, "Wenna").list(limit=20)
    assert len(kept) == 2 and {m["kind"] for m in mine} == {"event", "relationship"}
    plan = next(m for m in mine if m["kind"] == "event")
    assert plan["text"].startswith("From the evening at the healer's house") and "plan" in plan["tags"]
    assert table.tactics(memory.Memory(w, "Wenna")) == []  # the hamlet's evening keeps no line for the fights
    w.close()


class Walker:
    """A resident's session: its trips, and what its client sees at the gathering place. A rest moves the hamlet's
    clock on (`clock`) as if the time had passed."""

    def __init__(self, seen=(), clock=None):
        self.trips, self.seen, self.rested, self.clock = [], list(seen), [], clock

    async def travel_to(self, *a, **kw):
        self.trips.append((a, kw))
        return Result(True, "arrived")

    async def rest(self, seconds):
        self.rested.append(seconds)
        if self.clock is not None:
            self.clock.began -= seconds
        return Result(True, f"rested {seconds} s")

    async def snap(self):
        return {"player": {"x": 2245, "y": 1231},
                "mobiles": [{"name": n, "dx": 1, "dy": 0} for n in self.seen]}


def test_the_evening_goal_hosts_when_first_and_sits_when_a_table_is_open(tmp_path, monkeypatch):
    monkeypatch.setattr(hamlet, "GATHER_SOME_S", 0.0)
    w = World.open("local", root=tmp_path)
    h = maren(began=0.0)
    early = asyncio.run(hamlet.gathering(Walker(), neighbour(w, "Maren"), Hamlet.from_json({**h.to_json(), "began": 10**10})))
    assert not early.ok and "isn't evening yet" in early.summary and "from about 3 minutes before it" in early.summary
    # "In about 3 minutes" is up to 3:59 away, and the goal goes then, as the words say it may.
    words = Hamlet.from_json({**h.to_json(), "began": __import__("time").time() - 41.5 * 60})
    assert words.when(__import__("time").time()).endswith("in about 3 minutes")
    walker = Walker(clock=words)
    monkeypatch.setattr(hamlet, "GATHER_S", 0.0)
    asyncio.run(hamlet.gathering(walker, neighbour(w, "Maren"), words))
    assert walker.trips and 205 <= walker.rested[0] <= 215
    four = Hamlet.from_json({**h.to_json(), "began": __import__("time").time() - 40.5 * 60})
    assert "isn't evening yet" in asyncio.run(hamlet.gathering(Walker(), neighbour(w, "Maren"), four)).summary
    # Two minutes before it begins she sets off and waits there for it, rather than be told to wait and ask again.
    soon = Hamlet.from_json({**h.to_json(), "began": __import__("time").time() - 43 * 60})
    walker = Walker(clock=soon)
    alone = asyncio.run(hamlet.gathering(walker, neighbour(w, "Maren"), soon))
    assert walker.trips and 115 <= walker.rested[0] <= 125
    assert "no neighbour came" in alone.summary
    monkeypatch.setattr(hamlet, "GATHER_S", 150.0)
    h.began = __import__("time").time() - 50 * 60  # 50 minutes into the day: evening
    seats = {n: neighbour(w, n) for n in ("Maren", "Wenna")}

    async def go():
        first = asyncio.create_task(hamlet.gathering(Walker(["Wenna"]), seats["Maren"], h))
        await asyncio.sleep(0.3)
        second = asyncio.create_task(hamlet.gathering(Walker(["Maren"]), seats["Wenna"], h))
        return await first, await second

    first, second = asyncio.run(go())
    assert first.ok and first.data["hosted"] and first.data["present"] == ["Maren", "Wenna"] and first.data["lines"] >= 6
    assert second.ok and second.data["sat_at"] == 1 and second.data["memories_kept"] == 2
    w.close()


def test_a_residents_planner_is_offered_the_evening_and_told_the_time(tmp_path):
    session = FakeSession(world(tmp_path))
    session.hamlet = maren(began=__import__("time").time() - 50 * 60)
    chat = scripted(reply(call("rest", {"seconds": 30, "why": "waiting for the evening"})))
    p = planner.Planner(session, "live", chat_fn=chat, memory=False, table=neighbour(session.world, "Maren", mem=False))
    assert "evening" in [t["function"]["name"] for t in p.tools]
    asyncio.run(p.step())
    assert "day 1, evening: the neighbours gather at the healer's house" in chat.seen[0]["messages"][1]["content"]
    bare = planner.Planner(FakeSession(world(tmp_path / "b")), "live", chat_fn=scripted(), memory=False)
    assert "evening" not in [t["function"]["name"] for t in bare.tools]


def test_the_report_counts_each_residents_evenings():
    session = [{"type": "goal", "t": 1, "tool": "evening"}, {"type": "table", "t": 2, "closed": 1, "lines": 9},
               {"type": "table", "t": 2, "said": True}, {"type": "table", "t": 3, "said": True},
               {"type": "table", "t": 4, "kept": [5, 6]}]
    assert hamlet.evenings(session) == {"went": 1, "hosted": 1, "lines": 2, "memories": 2}


def test_one_who_comes_while_the_host_waits_sits_at_the_table_it_then_opens(tmp_path, monkeypatch):
    # The review of 2026-10-23: a host's claim is a row of table 0, and current() took it for an open table, so one
    # who came while the host waited sat at table 0, never heard its turn, and the host spoke alone.
    monkeypatch.setattr(hamlet, "GATHER_SOME_S", 1.0)
    w = World.open("local", root=tmp_path)
    h = maren(began=__import__("time").time() - 50 * 60)
    seats = {n: neighbour(w, n) for n in ("Maren", "Wenna")}
    assert seats["Maren"].claim("evening 0", 180) and seats["Maren"].current() is None

    async def go():
        first = asyncio.create_task(hamlet.gathering(Walker(["Wenna"]), seats["Maren"], h))
        await asyncio.sleep(0.2)  # the host is waiting for the others
        second = asyncio.create_task(hamlet.gathering(Walker(["Maren"]), seats["Wenna"], h))
        return await first, await second

    first, second = asyncio.run(go())
    assert first.data["hosted"] and first.data["skipped"] == {} and second.data["sat_at"] == first.data["table"]
    assert first.data["speakers"]["Wenna"] >= 3
    w.close()


def test_a_host_nobody_came_to_leaves_the_next_evening_free(tmp_path, monkeypatch):
    monkeypatch.setattr(hamlet, "GATHER_S", 0.5)
    w = World.open("local", root=tmp_path)
    h = maren(began=__import__("time").time() - 50 * 60)
    alone = asyncio.run(hamlet.gathering(Walker(), neighbour(w, "Maren"), h))
    assert not alone.ok and "no neighbour came" in alone.summary
    later = neighbour(w, "Wenna")
    assert later.current() is None and later.claim("evening 2", 180)
    w.close()


def test_a_neighbour_who_only_stands_by_the_house_isnt_at_the_table(tmp_path, monkeypatch):
    # The evenings run of 2026-10-24: Maren lives at the healer's house and rested there after the evening's first
    # table; Wenna, coming after it, counted her present twice and spoke nine lines to herself each time.
    monkeypatch.setattr(hamlet, "GATHER_S", 0.5)
    monkeypatch.setattr(hamlet, "GATHER_SOME_S", 0.0)
    w = World.open("local", root=tmp_path)
    h = maren(began=__import__("time").time() - 50 * 60)
    seats = {n: neighbour(w, n) for n in ("Maren", "Wenna")}
    alone = asyncio.run(hamlet.gathering(Walker(["Maren"]), seats["Wenna"], h))
    assert not alone.ok and "no neighbour came" in alone.summary and alone.data["present"] == ["Wenna"]
    # One who came for the evening and sat at its first table isn't waiting for the next.
    seats["Maren"].come("evening 1")
    assert seats["Wenna"].comers("evening 1") == {"Maren", "Wenna"}
    seats["Maren"].add(1, "close")
    assert seats["Wenna"].comers("evening 1") == set() and seats["Wenna"].comers("evening 2") == set()
    w.close()
