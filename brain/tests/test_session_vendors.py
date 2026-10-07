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
    assert s.danger_words({}) == "1 lich at near 1000,1000"
    s.record_dangers(s.danger_words({}))
    assert w.notes(area="near 1000,1000")[0]["text"].startswith("Stronger than a new character, seen ")

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
    assert step.result["result"] == "rested 5 s; stronger creatures seen: 1 lich at near 1000,1000"
    w.close()


def test_after_leaving_a_hunt_it_recalls_to_a_bank_in_a_guarded_town(tmp_path):
    """cuo-d28.9: the pack may still be about, so a rune or runebook entry to a guarded town is used."""
    from test_world import region
    w = World.open("test", root=tmp_path)
    w.replace_source("regions", "fixture", [
        region("Britain", "town", [[1416, 1498, 1740, 1777]], go=(1495, 1629), guarded=True),
        region("Britain Graveyard", "area", [[1333, 1441, 1417, 1523]], go=(1384, 1492))])
    w.add_place("bank", "West Britain bank", 1425, 1690)
    w.add_place("healer", "Britain healer", 1471, 1611)
    w.add_place("graveyard", "Britain Cemetery", 1384, 1497)
    snap = copy.deepcopy(SNAPSHOT)
    snap["player"].update(x=1380, y=1480)
    snap["travel_items"] = {"runes": [{"serial": 0x700, "name": "Recall Rune: a recall rune for Britain Cemetery"}],
                            "runebooks": [{"serial": 0x701, "entries": ["Britain healer", "West Britain bank"]}]}
    rpc = FakeRpc(snap)

    async def call(method, **params):
        rpc.calls.append((method, params))
        if method == "act" and params.get("verb") == "recall":
            rpc.snap["player"].update(x=1425, y=1690)  # it worked
        return copy.deepcopy(rpc.snap) if method == "snapshot" else {"status": "done"}

    rpc.call = call
    s = Session(rpc, w, None)
    # With anything close or fighting it doesn't stop to recall: the cast takes seconds and a hit breaks it.
    assert asyncio.run(s.retreat()) == "didn't stop to recall: 2 creatures still about"
    rpc.snap["mobiles"] = [m for m in rpc.snap["mobiles"] if not m["monster"]]
    said = asyncio.run(s.retreat())
    assert said == "recalled to West Britain bank (via west britain bank)"
    recall = next(p for m, p in rpc.calls if m == "act" and p["verb"] == "recall")
    assert recall["target"] == 0x701 and recall["distance"] == 1 and recall["kind"] == "charge"
    # Nothing to recall by: nothing said.
    rpc.snap["travel_items"] = {}
    assert asyncio.run(s.retreat()) == ""
    w.close()


def test_goals_back_where_it_left_from_creatures_are_refused_for_a_while(tmp_path):
    """A soak run's planner sent its warrior back to another spot of the graveyard it had just left
    two gargoyles at, and it died on the way."""
    import time as _time
    w = World.open("test", root=tmp_path)
    s = Session(FakeRpc(copy.deepcopy(SNAPSHOT)), w, None)
    s.left_from.append((1380, 1480, _time.monotonic(), "Britain Graveyard", "2 coming at once: 2 gargoyles"))
    r = asyncio.run(s.travel_to(x=1385, y=1500))
    assert not r.ok and r.summary.startswith("not going back near Britain Graveyard yet")
    assert s.avoided(1425, 1690) is None  # the bank is far enough
    s.left_from[0] = (1380, 1480, _time.monotonic() - 16 * 60, "Britain Graveyard", "old")
    assert s.avoided(1385, 1500) is None
    w.close()
