import asyncio
import copy
import time
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


def test_a_trip_sends_the_ground_of_stronger_creatures_for_the_client_to_go_round(tmp_path):
    """cuo-m3n.13: travel took the shortest road through the swamp where the hamlet's carrier died twice."""
    w = World.open("test", root=tmp_path)
    w.replace_source("creatures", "fixture", [
        {"type": "PlagueBeast", "name": "a plague beast", "hits_min": 318, "hits_max": 404, "difficulty": "deadly"},
        {"type": "Rabbit", "name": "a rabbit", "hits_min": 4, "hits_max": 6, "difficulty": "trivial"}])
    w.replace_source("spawns", "fixture", [
        {"area": "the swamp", "map": "Felucca", "x": 1100, "y": 1000, "walk_range": 30, "creature": "PlagueBeast",
         "max_count": 3, "spawner": "swamp"},
        {"area": "a field", "map": "Felucca", "x": 1150, "y": 1000, "walk_range": 20, "creature": "Rabbit",
         "max_count": 5, "spawner": "field"}])
    w.add_place("bank", "the far bank", 1200, 1000)
    rpc = FakeRpc(copy.deepcopy(SNAPSHOT))
    logged = []
    asyncio.run(Session(rpc, w, None, log=logged.append).travel_to("the far bank"))
    go = next(p for m, p in rpc.calls if m == "act" and p.get("verb") == "travel")
    assert go["avoid"] == [[1100, 1000, 30 + World.NOTICE_RANGE, 4]]
    assert next(r for r in logged if r["event"] == "travel")["avoiding"] == 1
    # A ghost on its way to a healer is attacked by nothing: no ground to go round.
    ghost = copy.deepcopy(SNAPSHOT)
    ghost["player"]["dead"] = True
    rpc = FakeRpc(ghost)
    asyncio.run(Session(rpc, w, None).travel_to("the far bank"))
    assert "avoid" not in next(p for m, p in rpc.calls if m == "act" and p.get("verb") == "travel")
    w.close()


def test_a_refused_hunt_names_the_nearest_grounds_where_nothing_is_stronger_than_the_character(tmp_path):
    """cuo-m3n.15: refused their emptied ground and told to hunt another, two of the hamlet's hunters went to the
    orc fort's savages and one to the liches east of Cove."""
    w = World.open("test", root=tmp_path)
    w.replace_source("creatures", "fixture", [
        {"type": "Boar", "name": "a boar", "hits_min": 15, "hits_max": 15, "difficulty": "weak", "fight_mode": "Aggressor"},
        {"type": "Lich", "name": "a lich", "hits_min": 103, "hits_max": 120, "difficulty": "strong", "fight_mode": "Closest"}])
    w.replace_source("spawns", "fixture", [
        {"area": area, "map": "Felucca", "x": x, "y": 1000, "walk_range": 20, "creature": kind, "max_count": 5,
         "spawner": area, "min_delay_s": 300, "max_delay_s": 600}
        for area, x, kind in (("the empty wood", 1020, "Boar"), ("the liches' ground", 1050, "Lich"),
                              ("the far wood", 1100, "Boar"))])
    s = Session(FakeRpc(copy.deepcopy(SNAPSHOT)), w, judge=object())
    s.emptied["the empty wood"] = (time.monotonic(), "nothing hostile in sight for 4 min", "5 min to 10 min")
    r = asyncio.run(s.hunt("the empty wood", 10))
    assert not r.ok and r.summary.startswith("not hunting the empty wood again yet")
    assert r.summary.endswith(". Grounds within 250 tiles where nothing is stronger than you: the far wood (100 tiles: boar)")
    w.close()


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


def test_a_hunt_that_finds_its_ground_empty_says_so_and_isnt_sent_back_to_it_for_a_while(tmp_path, monkeypatch):
    """cuo-m3n.11: the hamlet's hunter stood 10 minutes at a time on her ground, 19 animals over 50 tiles round its
    centre. The hunt said the creatures spawned over 15 tiles (its radius, kept to 15 for the rally), so Jev said it
    all fit in sight and the patrols kept to 10 tiles; told "nothing to fight for 10 minutes", her planner sent her
    back to the same emptied ground three times."""
    from uo_brain import session as sessions
    from uo_brain.session import Result
    w = World(tmp_path / "w.sqlite")
    w.insert("spawns", {"area": "south-west of Cove Cemetery", "map": "Felucca", "x": 2416, "y": 1172, "walk_range": 50,
                        "creature": "Boar", "max_count": 7, "spawner": "a", "min_delay_s": 300, "max_delay_s": 600,
                        "source": "test", "last_seen": "2026-10-24T00:00:00+00:00"})
    snap = copy.deepcopy(SNAPSHOT)
    snap["mobiles"], snap["agent"]["engaged"] = [], 0
    s = Session(FakeRpc(snap), w, judge=object())
    assert s.ground("south-west of Cove Cemetery", 2416, 1172, 15) == (50, "5 min to 10 min")
    assert s.ground("south-west of Cove Cemetery", 2000, 1000, 15) == (15, "")  # not this spawner's centre
    clock, made = [1000.0], []
    real = sessions.HuntWatch

    def watch(*a, **kw):
        made.append(real(*a, clock=lambda: clock[0], **kw))
        return made[-1]

    async def walk(*a, **kw):
        return Result(True, "arrived")

    async def defended(work):
        return await work

    class Stats:
        def summary(self):
            return {"client_stats": {}}

    async def run(rpc, judge, lcfg, pcfg, log, stop, on_snapshot=None, **kw):
        on_snapshot(copy.deepcopy(snap))
        clock[0] += 601  # ten minutes with nothing in sight
        on_snapshot(copy.deepcopy(snap))
        return Stats()

    s.travel_to, s.defended = walk, defended
    monkeypatch.setattr(sessions, "HuntWatch", watch)
    monkeypatch.setattr(sessions.loop, "run", run)
    r = asyncio.run(s.hunt("south-west of Cove Cemetery", 12))
    assert made[0].words(snap)["this_hunt"]["area_size"].startswith("creatures spawn over about 50 tiles")
    assert r.data["stopped_because"] == "nothing to fight for 10 minutes"
    assert r.summary.endswith("; the ground is empty: its creatures come back one at a time, every 5 min to 10 min, "
                              "so it isn't hunted again for 15 min")
    again = asyncio.run(s.hunt("south-west of Cove Cemetery", 12))
    assert not again.ok and again.summary == (
        "not hunting south-west of Cove Cemetery again yet: 0 min ago a hunt there found it empty (nothing hostile "
        "in sight for 10 min); one of its creatures comes back every 5 min to 10 min. Hunt another ground, do other "
        "work, or come back in 15 min. No ground within 250 tiles holds only creatures no stronger than you")
    s.emptied["south-west of cove cemetery"] = (s.emptied["south-west of cove cemetery"][0] - 15 * 60, "x", "")
    assert s.empty_ground("south-west of Cove Cemetery") is None
    w.close()


def test_a_bank_at_a_named_town_is_that_towns_and_one_that_brings_no_gold_says_the_box_holds_none(tmp_path):
    """cuo-m3n.13: told to draw from the Vesper bank, the carrier went to Britain's, as near from Cove, and died
    on the way; the watchman went to the bank three times to be told "moved 0 items"."""
    w = World.open("test", root=tmp_path)
    w.add_place("bank", "Britain banker", 1300, 1000)
    w.add_place("bank", "Vesper minter", 1000, 1330)
    w.add_place("town", "Vesper", 1010, 1340)
    w.add_place("town", "Cove", 1000, 1000)
    snap = copy.deepcopy(SNAPSHOT)
    snap["agent"]["errand"] = {"state": "done", "detail": "moved 0 items", "moved": 0}
    s = Session(FakeRpc(snap), w, None)
    trips = []

    async def walk(name, x, y, **kw):
        trips.append(name)
        from uo_brain.session import Result
        return Result(True, "arrived")

    s.travel_to = walk
    r = asyncio.run(s.bank("", "gold:200", place="Vesper"))
    assert trips == ["Vesper minter"] and r.ok
    assert r.summary == "Vesper minter: moved 0 items; no gold came out: the bank box holds none"
    # Without a town, the nearest; a town with no bank of its own is said to have none, and nothing is walked.
    asyncio.run(s.bank("gold", ""))
    assert trips[-1] == "Britain banker"
    r = asyncio.run(s.bank("", "bandage:50", place="Cove"))
    assert not r.ok and r.summary == "no bank at Cove: the nearest to it is Britain banker, 300 tiles from it"
    assert len(trips) == 2
    w.close()


def test_a_box_that_held_no_gold_isnt_asked_for_gold_again_until_gold_goes_in(tmp_path):
    """The watchman went to the bank for gold three times, ten minutes a trip, to be told "moved 0 items"."""
    w = World.open("test", root=tmp_path)
    w.add_place("bank", "Vesper minter", 1000, 1030)
    snap = copy.deepcopy(SNAPSHOT)
    snap["player"]["gold"] = 0
    snap["agent"]["errand"] = {"state": "done", "detail": "moved 0 items", "moved": 0}
    rpc = FakeRpc(snap)
    s = Session(rpc, w, None)
    trips = []

    async def walk(name, x, y, **kw):
        trips.append(name)
        from uo_brain.session import Result
        return Result(True, "arrived")

    s.travel_to = walk
    assert asyncio.run(s.bank("", "gold:200")).summary.endswith("no gold came out: the bank box holds none")
    r = asyncio.run(s.bank("", "gold:200"))
    assert not r.ok and r.summary == "not going to the bank for gold: 0 min ago the box held none, and none has gone in since"
    assert len(trips) == 1
    # With other work there, the trip goes on without the gold.
    r = asyncio.run(s.bank("", "gold:200,bandage:50"))
    assert len(trips) == 2 and r.summary.endswith("; gold left out: 0 min ago the box held none, and none has gone in since")
    errand = [p for m, p in rpc.calls if m == "act" and p.get("verb") == "bank"][-1]
    assert errand["withdraw"] == "bandage:50"
    # Gold put in: asked for again.
    banked = []

    async def call(method, **params):
        rpc.calls.append((method, params))
        if method == "snapshot":
            out = copy.deepcopy(snap)
            out["player"]["gold"] = 0 if banked else 300
            return out
        banked.append(params.get("verb") == "bank" or None)
        return {"status": "done"}

    rpc.call = call
    asyncio.run(s.bank("gold", ""))
    assert s.box_no_gold is None
    w.close()


def test_a_bank_at_a_town_is_any_of_its_banks_the_nearest_first(tmp_path):
    """cuo-m3n.13: 62 tiles short of Britain's east bank, the carrier told "Britain" was told it had none: its
    west bank, the nearest to the town's middle, was 70 tiles from it. She walked 282 tiles to it."""
    w = World.open("test", root=tmp_path)
    w.add_place("town", "Britain", 1000, 1000, region="Britain")
    w.add_place("bank", "West Britain bank", 930, 1060, region="Britain")
    w.add_place("bank", "East Britain bank", 1155, 980, region="Britain")
    w.add_place("bank", "Moonglow bank", 1500, 1000, region="Moonglow")
    snap = copy.deepcopy(SNAPSHOT)
    snap["player"].update(x=1200, y=1000)
    snap["agent"]["errand"] = {"state": "done", "detail": "moved 1 item", "moved": 1}
    s = Session(FakeRpc(snap), w, None)
    trips = []

    async def walk(name, x, y, **kw):
        trips.append(name)
        from uo_brain.session import Result
        return Result(True, "arrived")

    s.travel_to = walk
    assert asyncio.run(s.bank("gold", "", place="Britain")).ok
    assert trips == ["East Britain bank"]
    w.close()


class Walking(FakeRpc):
    """A trip that moves `step` tiles a second, and arrives at the `arrive`th look, if given."""

    def __init__(self, snap, step, arrive=None):
        super().__init__(snap)
        self.step, self.arrive, self.looks = step, arrive, 0

    async def call(self, method, **params):
        if method != "snapshot":
            return await super().call(method, **params)
        snap = copy.deepcopy(self.snap)
        snap["player"]["x"] += self.step * self.looks
        done = self.arrive is not None and self.looks >= self.arrive
        snap["agent"]["travel"] = {"state": "arrived" if done else "walking", "left": 40}
        self.looks += 1
        return snap


def test_a_trip_still_on_its_way_at_its_time_limit_goes_on(tmp_path):
    """cuo-m3n.13: walked at a resident's pace round the swamp, the carrier's trip to the bank was cut off at 600 s,
    62 tiles short."""
    w = World.open("test", root=tmp_path)
    r = asyncio.run(Session(Walking(copy.deepcopy(SNAPSHOT), 25, arrive=2), w, None).travel_to(x=1100, y=1000,
                                                                                              timeout_s=1))
    assert r.ok
    # One that has stopped getting anywhere times out at the limit; one that goes on and on, at GOING_TIMES it.
    began = time.monotonic()
    r = asyncio.run(Session(Walking(copy.deepcopy(SNAPSHOT), 0), w, None).travel_to(x=1100, y=1000, timeout_s=1))
    assert not r.ok and "timed out" in r.summary and time.monotonic() - began < 1.8
    s = Session(Walking(copy.deepcopy(SNAPSHOT), 25), w, None)
    s.GOING_TIMES = 2
    r = asyncio.run(s.travel_to(x=1100, y=1000, timeout_s=1))
    assert not r.ok and "timed out" in r.summary and r.data["seconds"] == 2
    w.close()


def test_a_hunt_with_nothing_to_heal_by_is_refused_before_the_walk(tmp_path):
    """The watchman walked 345 tiles to hunt by Vesper with no bandages or potions, and stopped at once."""
    w = World.open("test", root=tmp_path)
    w.add_place("graveyard", "Britain Graveyard", 1369, 1475, source="test")
    snap = copy.deepcopy(SNAPSHOT)
    snap["player"]["supplies"] = {"bandages": 0, "heal_potions": 0}
    s = Session(FakeRpc(snap), w, None)
    walked = []

    async def walk(*a, **kw):
        walked.append(a)

    s.travel_to = walk
    r = asyncio.run(s.hunt("Britain Graveyard", 5))
    assert not r.ok and not walked
    assert r.summary.startswith("not hunting Britain Graveyard: out of bandages and heal potions; restock first")


def test_a_corpse_that_two_deaths_lie_by_is_let_go_even_with_go_anyway(tmp_path):
    """The watchman went back for his corpse with go_anyway three times, to the savages by it, and died each
    time: four deaths in twenty minutes."""
    w = World.open("test", root=tmp_path)
    snap = copy.deepcopy(SNAPSHOT)
    snap["player"]["last_death"] = {"x": 1100, "y": 1000, "ago_s": 300}
    rpc = FakeRpc(snap)
    s = Session(rpc, w, None)
    trips = []

    async def walk(*a, **kw):
        trips.append(kw)
        from uo_brain.session import Result
        return Result(False, "died on the way")

    s.travel_to = walk
    assert asyncio.run(s.recover_corpse(go_anyway=True)).summary.startswith("could not get back to the corpse")
    # Asked again about the same death: still one death there.
    assert asyncio.run(s.recover_corpse(go_anyway=True)).summary.startswith("could not get back to the corpse")
    rpc.snap["player"]["last_death"] = {"x": 1104, "y": 1003, "ago_s": 20}
    r = asyncio.run(s.recover_corpse(go_anyway=True))
    assert r.summary == "not going back, even with go_anyway: died 2 times by the corpse in 5 min; let it go"
    assert len(trips) == 2


def test_in_a_guarded_town_a_creature_turned_criminal_close_by_is_called_the_guards_on(tmp_path):
    """cuo-m3n.14: a lich that followed Maren home killed her at the healer's house in Cove, and nobody called the
    guards. Staged there, a lich took a warrior to 25 hits of 95 in 8 s with no guard; called, one came in a second."""
    import json
    w = World.open("test", root=tmp_path)
    w.replace_source("regions", "fixture", [{"name": "Cove", "map": "Felucca", "kind": "town", "guarded": 1,
                                             "rects": json.dumps([[950, 950, 1050, 1050]])}])
    snap = copy.deepcopy(SNAPSHOT)
    snap["mobiles"] = [{"serial": 0x200, "name": "a lich", "monster": True, "notoriety": "criminal", "distance": 5,
                        "dead": False, "dx": 5, "dy": 0},
                       {"serial": 0x201, "name": "a cat", "monster": False, "notoriety": "gray", "distance": 2,
                        "dead": False, "dx": 2, "dy": 0}]
    rpc = FakeRpc(snap)
    logged = []
    s = Session(rpc, w, None, log=logged.append)

    def said():
        return [p["text"] for m, p in rpc.calls if m == "act" and p.get("verb") == "say"]

    async def look(snap):
        s.seen(snap)
        await asyncio.sleep(0)

    asyncio.run(look(snap))
    assert said() == ["Guards!"] and next(r for r in logged if r["event"] == "guards")["on"] == ["a lich"]
    asyncio.run(look(snap))
    assert said() == ["Guards!"]  # not again within GUARDS_EVERY_S
    s._guards_called -= Session.GUARDS_EVERY_S
    asyncio.run(look(snap))
    assert said() == ["Guards!", "Guards!"]  # still about: called again
    # A fresh hit is called on at once, not after GUARDS_EVERY_S: a creature is a candidate only from its first harm
    # inside the town.
    s._guards_called = time.monotonic() - Session.GUARDS_HIT_S
    snap["player"]["hits"] -= 20
    asyncio.run(look(snap))
    assert said() == ["Guards!"] * 3
    s._guards_called = time.monotonic() - Session.GUARDS_HIT_S
    asyncio.run(look(snap))
    assert said() == ["Guards!"] * 3  # no new hit: the 8 s wait again
    # Out of the guards' ground, or with nothing criminal near (a lich still gray, or out of their reach): no call.
    for x, noto, far in ((1100, "criminal", 5), (1000, "gray", 5), (1000, "criminal", 16)):
        s._guards_called -= Session.GUARDS_EVERY_S
        snap["player"]["x"], snap["mobiles"][0]["notoriety"], snap["mobiles"][0]["distance"] = x, noto, far
        asyncio.run(look(snap))
    assert len(said()) == 3
