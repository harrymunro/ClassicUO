import asyncio
import copy

from conftest import SNAPSHOT

from uo_brain.session import Session
from uo_brain.world import World


class FakeRpc:
    def __init__(self, snap):
        self.snap = snap
        self.calls = []

    async def call(self, method, **params):
        self.calls.append((method, params))
        if method == "snapshot":
            return copy.deepcopy(self.snap)
        return {"status": "done"}


def test_buying_skips_healers_known_not_to_sell_it_and_prefers_known_sellers(tmp_path):
    w = World.open("test", root=tmp_path)
    # Nearest first: a wandering healer (resurrects only), a healer of unknown stock, a healer that sells bandages.
    w.add_place("healer", "wandering healer", 1002, 1000, sells=["resurrection"])
    w.add_place("healer", "a healer we know nothing about", 1004, 1000)
    w.add_place("healer", "the Britain healer", 1006, 1000, sells=["bandages", "potions"])
    s = Session(FakeRpc(copy.deepcopy(SNAPSHOT)), w, None)
    place, failed = asyncio.run(s.go_near("healer", 10, sells="bandage"))
    assert failed is None and place["name"] == "the Britain healer"
    place, _ = asyncio.run(s.go_near("healer", 10, skip={"the Britain healer"}, sells="bandage"))
    assert place["name"] == "a healer we know nothing about"
    place, _ = asyncio.run(s.go_near("healer", 10))
    assert place["name"] == "wandering healer"  # without an item, the nearest of the kind
    w.close()


def test_an_errand_that_times_out_is_stopped_in_the_client(tmp_path):
    w = World.open("test", root=tmp_path)
    snap = copy.deepcopy(SNAPSHOT)
    snap["agent"]["errand"] = {"state": "approaching"}
    rpc = FakeRpc(snap)
    s = Session(rpc, w, None)
    e = asyncio.run(s.errand("buy", timeout_s=0.6, target=1, items="bandage:10"))
    assert e == {"state": "failed", "detail": "timed out"}
    assert rpc.calls[-1][0] == "act" and rpc.calls[-1][1]["verb"] == "stop"
    w.close()


def test_a_defended_goal_returns_its_result_and_stops_the_fight_loop(tmp_path):
    from uo_brain.judge import HeuristicJudge
    from uo_brain.session import Result
    w = World.open("test", root=tmp_path)
    rpc = FakeRpc(copy.deepcopy(SNAPSHOT))
    s = Session(rpc, w, HeuristicJudge())

    async def work():
        await asyncio.sleep(1.5)
        return Result(True, "rested 1 s")

    async def go():
        r = await s.defended(work())
        await asyncio.sleep(0.3)
        return r, len([c for c in rpc.calls if c[0] == "snapshot"])

    r, polls = asyncio.run(go())
    assert r.ok and r.summary == "rested 1 s"
    assert polls > 0  # the fight loop looked at the game meanwhile
    after = len([c for c in rpc.calls if c[0] == "snapshot"])
    assert after == polls  # and stopped with the goal
    w.close()


def test_stronger_creatures_seen_go_into_the_goals_result_and_the_world_store(tmp_path):
    from uo_brain.planner import Planner
    w = World.open("test", root=tmp_path)
    snap = copy.deepcopy(SNAPSHOT)
    snap["mobiles"][1].update({"body": 24, "name": "a lich", "distance": 5, "dx": 5, "dy": 0})  # body 24: a lich
    s = Session(FakeRpc(snap), w, None)
    w.bestiary = lambda: {24: {"hits": 300, "difficulty": "deadly", "name": "a lich"}}
    s.note_dangers(snap)
    assert s.danger_words({}) == "1 lich at 1005,1000"
    s.record_dangers(s.danger_words({}))
    assert w.notes(area="1005,1000")[0]["text"].startswith("Stronger than a new character, seen ")

    async def go():
        p = Planner(s, "hunt", log=lambda r: None)
        s.dangers.clear()

        async def rest(seconds):
            s.note_dangers(snap)
            from uo_brain.session import Result
            return Result(True, "rested 5 s")
        s.rest = rest
        s.defended = lambda work: work
        return await p.carry_out("rest", {"seconds": 5})

    step = asyncio.run(go())
    assert step.result["result"] == "rested 5 s; stronger creatures seen: 1 lich at 1005,1000"
    w.close()
