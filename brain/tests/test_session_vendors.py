import asyncio
import copy
from datetime import datetime, timedelta, timezone

from conftest import SNAPSHOT, mage_snapshot

from uo_brain.session import NOT_COUNTED, Session, describe
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


def healer_in_shop(snap):
    snap["mobiles"] = [{"serial": 0x500, "name": "Lucy", "label": "Lucy the healer", "human": True, "monster": False,
                        "notoriety": "invulnerable", "distance": 3, "dx": 3, "dy": 0}]
    snap["agent"]["errand"] = {"state": "done", "detail": "the healer has none left", "gold_change": 0}
    return snap


def test_a_sold_out_shop_doesnt_send_the_buy_to_another_town(tmp_path):
    """cuo-1kk: Britain's healer was sold out, and a fleet NPC's buy walked on to the Cove healer, 770
    tiles and 6.6 minutes away. The buy ends instead, naming the next shop and how far it is."""
    w = World.open("test", root=tmp_path)
    w.add_place("healer", "the Britain healer", 1006, 1000, sells=["bandages"])
    w.add_place("healer", "the Cove healer", 1770, 1000, sells=["bandages"])
    rpc = FakeRpc(healer_in_shop(copy.deepcopy(SNAPSHOT)))
    res = asyncio.run(Session(rpc, w, None).buy("bandage", 25))
    assert not res.ok and res.summary.startswith("the Britain healer: bought 0 of 25 bandage")
    assert "the nearest other healer selling bandage is the Cove healer, 770 tiles away; travel_to it first" in res.summary
    assert not any(m == "act" and p.get("verb") == "travel" for m, p in rpc.calls)
    w.close()


def test_a_guildmaster_in_the_shop_is_not_asked_for_its_goods(tmp_path):
    """A soak run's buys at the Britain healer went to the healer guildmistress on the next tile, who
    sells nothing, while the healer stood three tiles off."""
    w = World.open("test", root=tmp_path)
    w.add_place("healer", "the Britain healer", 1006, 1000, sells=["bandages"])
    snap = healer_in_shop(copy.deepcopy(SNAPSHOT))
    snap["mobiles"].insert(0, {**snap["mobiles"][0], "serial": 0x501, "name": "Devaki",
                               "label": "Devaki The Healer Guildmistress", "distance": 1})
    _, serial, failed = asyncio.run(Session(FakeRpc(snap), w, None).vendor("healer", ()))
    assert failed is None and serial == 0x500
    w.close()


def test_the_buy_record_says_which_vendor_was_asked_and_how(tmp_path):
    """cuo-k0d: a soak's first three buys failed with "the vendor did not show its goods" and the log
    couldn't say who had been asked. "vendor buy" had gone to the healer guildmistress, the nearer of
    the two."""
    w = World.open("test", root=tmp_path)
    w.add_place("healer", "the Britain healer", 1006, 1000, sells=["bandages"])
    snap = healer_in_shop(copy.deepcopy(SNAPSHOT))
    snap["agent"]["errand"] = {"state": "failed", "detail": "the vendor did not show its goods", "gold_change": 0,
                               "vendor": 0x500, "vendor_name": "Lucy the healer", "asks": 3, "said": "vendor buy",
                               "nearer": "Devaki The Healer Guildmistress"}
    logged = []
    res = asyncio.run(Session(FakeRpc(snap), w, None, log=logged.append).buy("bandage", 25))
    assert not res.ok
    buy = next(r for r in logged if r["event"] == "buy")
    assert buy["vendors"] == [{"serial": 0x500, "name": "Lucy the healer", "tiles": 3, "state": "failed", "asks": 3,
                               "said": "vendor buy", "nearer": "Devaki The Healer Guildmistress"}]
    # A client from before the change says nothing of the asking: who was chosen is still there.
    snap["agent"]["errand"] = {"state": "done", "detail": "bought 25 Clean Bandage", "gold_change": -125}
    logged.clear()
    asyncio.run(Session(FakeRpc(snap), w, None, log=logged.append).buy("bandage", 25))
    assert next(r for r in logged if r["event"] == "buy")["vendors"][0] == {
        "serial": 0x500, "name": "Lucy the healer", "tiles": 3, "state": "done"}
    w.close()


def test_a_buy_doesnt_set_off_for_a_far_shop_either(tmp_path):
    w = World.open("test", root=tmp_path)
    w.add_place("healer", "the Cove healer", 1770, 1000, sells=["bandages"])
    rpc = FakeRpc(copy.deepcopy(SNAPSHOT))
    res = asyncio.run(Session(rpc, w, None).buy("bandage", 25))
    assert not res.ok and res.summary == ("the nearest healer selling bandage is the Cove healer, 770 tiles away; "
                                          "travel_to it first if the trip is worth it")
    assert res.data["tiles"] == 770 and not any(m == "act" and p.get("verb") == "travel" for m, p in rpc.calls)
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
    s.left_from[0] = (1380, 1480, _time.monotonic() - 31 * 60, "Britain Graveyard", "old")
    assert s.avoided(1385, 1500) is None
    w.close()


def test_the_walk_to_a_hunts_area_is_defended(tmp_path):
    """cuo-sj2: the walk to a hunt had no fight loop beside it, and a warrior died 'on the way'."""
    import asyncio
    from uo_brain.session import Result, Session
    from uo_brain.world import World
    w = World(tmp_path / "w.sqlite")
    w.add_place("graveyard", "Britain Graveyard", 1369, 1475, source="test")
    s = Session(rpc=None, world=w)
    seen = []

    async def walk(*a, **kw):
        seen.append("walking")
        return Result(False, "died on the way")

    async def defended(work):
        seen.append("defended")
        return await work

    s.travel_to, s.defended = walk, defended
    r = asyncio.run(s.hunt("Britain Graveyard", 5))
    assert seen == ["defended", "walking"] and r.summary.startswith("could not get to Britain Graveyard")


def test_a_mage_is_told_of_its_reagents_not_bandages():
    """cuo-pz9: a new mage (reagents in a bag, no bandages) was told "bandages 0" and nothing of reagents,
    and its planner sent it to buy both before the party's fight."""
    mage = mage_snapshot()
    told = describe(mage)
    assert told["plays_as"] == "mage" and "bandages" not in told and told["reagents"]["black pearl"] == 90
    unread = mage_snapshot()
    unread["player"]["supplies"]["reagents"] = dict.fromkeys(unread["player"]["supplies"]["reagents"], 0)
    unread["player"]["supplies"]["unread_bags"] = 1
    assert describe(unread)["reagents"] == NOT_COUNTED
    unread["player"]["supplies"]["unread_bags"] = 0
    assert describe(unread)["reagents"] == "none"
    warrior = describe(copy.deepcopy(SNAPSHOT))
    assert warrior["plays_as"] == "warrior" and "bandages" in warrior and "reagents" not in warrior


def test_the_pack_is_read_before_the_first_plan():
    class Reading(FakeRpc):
        def __init__(self, snap, unread):
            super().__init__(snap)
            self.unread = list(unread)

        async def call(self, method, **params):
            if method == "snapshot":
                snap = copy.deepcopy(self.snap)
                snap["player"]["supplies"]["unread_bags"] = self.unread.pop(0) if len(self.unread) > 1 else self.unread[0]
                return snap
            return await super().call(method, **params)

    w = World(":memory:")
    assert asyncio.run(Session(Reading(mage_snapshot(), [2, 1, 0]), w, None).pack_read(timeout_s=5)) is True
    assert asyncio.run(Session(Reading(mage_snapshot(), [1]), w, None).pack_read(timeout_s=0.6)) is False
    assert asyncio.run(Session(FakeRpc(copy.deepcopy(SNAPSHOT)), w, None).pack_read()) is True  # an older client


def test_a_hunt_named_by_a_place_goes_to_the_spawn_at_it_not_the_biggest_that_names_it(tmp_path):
    """cuo-821: 'Cove Orc Fort' went 813 tiles west to 'west of Cove Orc Fort', the area with the most creatures,
    instead of to the orcs 'near Cove Orc Fort'. An exact area name is taken as it was."""
    w = World.open("test", root=tmp_path)
    w.add_place("landmark", "Cove Orc Fort", 2171, 1332, source="test")

    def spawner(area, x, y, creature, n):
        w.insert("spawns", {"area": area, "map": "Felucca", "x": x, "y": y, "walk_range": 30, "creature": creature,
                            "max_count": n, "spawner": f"{area}:{creature}", "source": "test",
                            "last_seen": "2026-10-19T00:00:00+00:00"})

    spawner("west of Cove Orc Fort", 1958, 1305, "Boar", 7)
    spawner("west of Cove Orc Fort", 1958, 1305, "BlackBear", 5)
    spawner("near Cove Orc Fort", 2171, 1356, "Orc", 5)
    spawner("Britain Graveyard", 1369, 1475, "Skeleton", 7)
    spawner("Britain Graveyard", 1395, 1500, "Zombie", 2)
    w.add_place("graveyard", "Britain Graveyard", 1384, 1492, source="test")
    s = Session(None, w)
    assert s.hunt_spot("Cove Orc Fort")[:3] == (2171, 1356, "near Cove Orc Fort")
    assert s.hunt_spot("west of Cove Orc Fort")[:3] == (1958, 1305, "west of Cove Orc Fort")
    assert s.hunt_spot("Britain Graveyard")[:3] == (1369, 1475, "Britain Graveyard")  # the biggest, as before


def test_a_corpse_out_in_the_wilds_is_left_while_danger_was_seen_around_it(tmp_path):
    """cuo-m3n.6: only a region's dangers were looked at, so the hamlet's watchman, dead to savages by the
    Cove Orc Fort where no region is, walked straight back to them and died on the way."""
    from uo_brain.session import Result
    w = World.open("test", root=tmp_path)
    snap = copy.deepcopy(SNAPSHOT)
    snap["player"].update(dead=False, last_death={"x": 2204, "y": 1255})
    s = Session(FakeRpc(snap), w, None)

    async def travel_to(**_):
        return Result(False, "went")
    s.travel_to = travel_to
    w.add_note("Stronger than a new character, seen 2026-10-23 15:33: 1 lich.", area="near 2400,1100",
               tags=["danger"], source="seen")
    assert asyncio.run(s.recover_corpse()).summary == "could not get back to the corpse: went"  # far off
    w.add_note("Stronger than a new character, seen 2026-10-23 15:33: 1 Masena.", area="near 2150,1300",
               tags=["danger"], source="seen")  # the place beside the corpse's
    r = asyncio.run(s.recover_corpse())
    assert not r.ok and r.summary.startswith("not going back yet: danger was seen by the corpse, at near 2150,1300 "
                                             "0 min ago: Stronger than a new character")
    assert asyncio.run(s.recover_corpse(go_anyway=True)).summary == "could not get back to the corpse: went"
    w.db.execute("UPDATE notes SET last_seen = ?", ((datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat(),))
    assert asyncio.run(s.recover_corpse()).summary == "could not get back to the corpse: went"
    w.close()



def test_a_corpse_lasts_fourteen_minutes_and_its_bones_are_still_its_own(tmp_path):
    """cuo-m3n.6: a corpse turns to bones after 7 minutes, with everything still on it for 7 more. The client counted
    no bones as a corpse, so every walk back after 7 minutes found "the corpse is gone"; and the watchman walked back
    27 minutes after a death, to a corpse long gone, and died among the savages by it."""
    from uo_brain.session import Result
    w = World.open("test", root=tmp_path)
    snap = copy.deepcopy(SNAPSHOT)
    snap["player"].update(dead=False, last_death={"x": 2204, "y": 1255, "ago_s": 900})
    rpc = FakeRpc(snap)
    s = Session(rpc, w, None)
    walks = []

    async def travel_to(**kw):
        walks.append(kw)
        return Result(True, "arrived")
    s.travel_to = travel_to
    r = asyncio.run(s.recover_corpse())
    assert not r.ok and r.summary == ("the corpse will have decayed by now: the death was 15 min ago, and a corpse "
                                      "lasts about 14 minutes (7, then 7 as bones)") and walks == []
    snap["player"]["last_death"]["ago_s"] = 360
    w.add_note("Stronger than a new character, seen 2026-10-24 04:26: 3 savages.", area="near 2200,1250",
               tags=["danger"], source="seen")
    r = asyncio.run(s.recover_corpse())
    assert "danger was seen by the corpse" in r.summary and "wait (it lasts about 8 more minutes), let it go" in r.summary
    # Ten minutes on, at its bones: opened, and everything comes back.
    snap["player"]["last_death"]["ago_s"] = 600
    snap["corpses"] = [{"serial": 7, "name": "bones", "distance": 1, "own": True, "bones": True, "opened": False, "items": []}]
    snap["journal"] = [{"text": "You quickly gather all of your belongings."}]
    r = asyncio.run(s.recover_corpse(go_anyway=True))
    assert r.ok and ("act", {"verb": "use", "target": 7}) in rpc.calls
    w.close()

def test_a_ghost_stands_off_where_the_step_in_is_in_the_healers_sight(tmp_path):
    """cuo-m3n.9: the Cove healer stands in her east doorway; from the north the step to within 4 of her fell
    behind her wall and she never offered. The stand-off is where that step is in her sight, our side first."""
    w = World.open("test", root=tmp_path)
    rpc = FakeRpc(copy.deepcopy(SNAPSHOT))
    asked = []

    async def call(method, **params):
        if method == "sight":
            asked.append(params["pairs"])
            # She sees east and north-east only.
            return {"in_sight": [x2 > x1 for x1, _, _, x2, _, _ in params["pairs"]]}
        return {"status": "done"}
    rpc.call = call
    s = Session(rpc, w, None)
    # The ghost is north of her: its own side (north) is behind the wall, so the nearest seen spot, north-east.
    assert asyncio.run(s.stand_off(100, 100, 100, 90, 0)) == (106, 94)
    assert asyncio.run(s.stand_off(100, 100, 100, 90, 0, attempt=1)) == (106, 100)  # the next, east
    assert asyncio.run(s.stand_off(100, 100, 100, 90, 0, attempt=2)) == (106, 106)
    assert asyncio.run(s.stand_off(100, 100, 100, 90, 0, attempt=3)) == (105, 95)  # then 5 off, for a small house
    # Our own side first, the spot and the step to within 4 on the way in, eye to eye.
    assert asked[0][:2] == [[100, 100, 14, 100, 94, 14], [100, 100, 14, 100, 96, 14]]
    # Seen from our own side, that's where.
    assert asyncio.run(s.stand_off(100, 100, 110, 100, 0)) == (106, 100)

    async def across_her_wall(method, **params):
        # She sees the step in from the west but not the spot 6 off (outside her wall), only 5 off (inside).
        return {"in_sight": [x2 >= 95 if x2 < 100 else False for _, _, _, x2, _, _ in params["pairs"]]}
    rpc.call = across_her_wall
    assert asyncio.run(s.stand_off(100, 100, 90, 100, 0)) == (95, 100)
    # A client that doesn't answer: our own side, as before.
    s.rpc = FakeRpc(copy.deepcopy(SNAPSHOT))
    assert asyncio.run(s.stand_off(100, 100, 100, 90, 0)) == (100, 94)
    w.close()


def test_a_hunt_a_red_player_ended_keeps_the_character_off_that_spot_for_a_while(tmp_path, monkeypatch):
    """cuo-m3n.6: the watchman's hunt by the orc fort ended as a red player came close, savages stronger than him
    in view; two minutes later he went back to hunt there and died in 50 s. A leave kept a character off a spot
    for 30 minutes; this didn't."""
    from uo_brain import session as sessions
    from uo_brain.session import Result
    w = World(tmp_path / "w.sqlite")
    w.add_place("graveyard", "Britain Graveyard", 1369, 1475, source="test")
    snap = copy.deepcopy(SNAPSHOT)
    snap["agent"]["threats"] = [{"kind": "red", "name": "Ogger", "distance": 6}]
    s = Session(FakeRpc(snap), w, judge=object())

    async def walk(*a, **kw):
        return Result(True, "arrived")

    async def defended(work):
        return await work

    class Stats:
        def summary(self):
            return {"client_stats": {}}

    async def run(rpc, judge, lcfg, pcfg, log, stop, on_snapshot=None, **kw):
        on_snapshot(copy.deepcopy(snap))
        return Stats()

    s.travel_to, s.defended = walk, defended
    monkeypatch.setattr(sessions.loop, "run", run)
    r = asyncio.run(s.hunt("Britain Graveyard", 5))
    assert r.data["stopped_because"] == "a red or criminal player came close"
    again = asyncio.run(s.hunt("Britain Graveyard", 5))
    assert not again.ok and again.summary.startswith("not going back near Britain Graveyard yet: it left there 0 min "
                                                     "ago (a red or criminal player came close)")
    w.close()
