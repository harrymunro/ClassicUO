import json
import sys

import pytest

from uo_brain import cli
from uo_brain import world as worlds
from uo_brain.world import World, grade_creature


def region(name, kind, rects, go=None, entrance=None, guarded=False, parent=None):
    row = {"name": name, "map": "Felucca", "kind": kind, "guarded": int(guarded), "rects": json.dumps(rects),
           "parent": parent}
    if go:
        row.update(go_x=go[0], go_y=go[1], go_z=0)
    if entrance:
        row.update(entrance_x=entrance[0], entrance_y=entrance[1], entrance_z=0)
    return row


def spawn(area, region_name, x, y, guid, entries, count=None):
    return [{"area": area, "region": region_name, "map": "Felucca", "x": x, "y": y, "z": 0, "walk_range": 10,
             "creature": c, "max_count": n, "spawner": guid, "spawner_count": count or sum(m for _, m in entries),
             "min_delay_s": 300, "max_delay_s": 600} for c, n in entries]


def creature(t, hits, damage, fame, karma=None):
    return {"type": t, "name": f"a {t.lower()}", "hits_min": hits[0], "hits_max": hits[1], "damage_min": damage[0],
            "damage_max": damage[1], "fame": fame, "karma": -fame if karma is None else karma,
            "difficulty": grade_creature(hits[0], hits[1], damage[0], damage[1], fame)}


@pytest.fixture
def w(tmp_path):
    w = World.open("test", root=tmp_path)
    w.replace_source("regions", "fixture", [
        region("Britain", "town", [[1416, 1498, 1740, 1777]], go=(1495, 1629), guarded=True),
        region("Britain Graveyard", "area", [[1333, 1441, 1417, 1523]], go=(1384, 1492)),
        region("Covetous", "dungeon", [[5376, 1793, 5632, 2048]], go=(5456, 1862), entrance=(2499, 916)),
    ])
    w.replace_source("places", "fixture", [
        {"kind": "bank", "name": "West Britain bank", "map": "Felucca", "x": 1425, "y": 1690, "region": "Britain",
         "sells": json.dumps(["bank box"])},
        {"kind": "bank", "name": "East Britain bank", "map": "Felucca", "x": 1650, "y": 1608, "region": "Britain"},
        {"kind": "healer", "name": "Britain healer", "map": "Felucca", "x": 1471, "y": 1611, "region": "Britain",
         "sells": json.dumps(["resurrection", "bandages", "lesser heal potions"])},
        {"kind": "mage_shop", "name": "Britain mage shop", "map": "Felucca", "x": 1485, "y": 1550, "region": "Britain",
         "sells": json.dumps(["reagents", "black pearl", "spellbook"])},
        {"kind": "graveyard", "name": "Britain Cemetery", "map": "Felucca", "x": 1384, "y": 1497,
         "region": "Britain Graveyard"},
        {"kind": "moongate", "name": "Britain moongate", "map": "Felucca", "x": 1336, "y": 1997},
        {"kind": "bank", "name": "Trammel Britain bank", "map": "Trammel", "x": 1425, "y": 1690},
    ])
    w.replace_source("creatures", "fixture", [
        creature("Skeleton", (34, 48), (3, 7), 450), creature("Zombie", (28, 42), (3, 7), 600),
        creature("Wraith", (46, 60), (7, 11), 4000), creature("Spectre", (46, 60), (7, 11), 4000),
        creature("Orc", (58, 72), (5, 7), 1500), creature("Dragon", (478, 495), (16, 22), 15000),
        creature("Lich", (103, 120), (24, 26), 8000), creature("Rabbit", (4, 6), (1, 1), 150, karma=0),
    ])
    w.replace_source("spawns", "fixture",
                     spawn("Britain Graveyard", "Britain Graveyard", 1369, 1475, "gy",
                           [("Spectre", 2), ("Wraith", 2), ("Skeleton", 3), ("Zombie", 4)], count=9)
                     + spawn("near Britain moongate", None, 1300, 1950, "orcs", [("Orc", 6)])
                     + spawn("Britain", "Britain", 1500, 1600, "town-rabbits", [("Rabbit", 5)])
                     + spawn("Covetous Level 1", "Covetous", 5456, 1870, "cov", [("Lich", 3), ("Skeleton", 4)])
                     + spawn("Dragon cave", None, 1200, 1400, "drag", [("Dragon", 1), ("Orc", 9)]))
    yield w
    w.close()


def test_grades_follow_hits_damage_and_fame():
    assert grade_creature(4, 6, 1, 2, 150) == "trivial"
    assert grade_creature(34, 48, 3, 7, 450) == "weak"
    assert grade_creature(46, 60, 7, 11, 4000) == "moderate"  # a wraith: weak in melee, but it casts
    assert grade_creature(103, 120, 24, 26, 8000) == "strong"
    assert grade_creature(478, 495, 16, 22, 15000) == "deadly"
    assert grade_creature(None, None, None, None, None) is None


def test_store_lives_per_shard(tmp_path):
    with World.open("uor", root=tmp_path) as w:
        w.add_note("Reds camp the Britain moongate after dark.", area="Britain")
    assert (tmp_path / "uor" / "world.sqlite").exists()
    with pytest.raises(worlds.WorldError):
        World.open("../evil", root=tmp_path)


def test_place_resolves_loose_names(w):
    assert w.place("britain bank")[0]["name"] == "West Britain bank"  # tie goes to the one nearer the centre
    assert w.place("Britain graveyard")[0]["name"] == "Britain Graveyard"
    typo = w.place("britian cemetary")  # typos and synonyms: both the region and the spot inside it
    assert {p["name"] for p in typo[:2]} == {"Britain Graveyard", "Britain Cemetery"}
    assert typo[0]["match"] < 1.0
    assert w.place("britain bank", map="Trammel")[0]["name"] == "Trammel Britain bank"
    assert w.place("nowhere at all") == []
    hit = w.place("Covetous")[0]
    assert hit["type"] == "region" and hit["entrance"] == [2499, 916, 0]


def test_find_place_nearest_first(w):
    banks = w.find_place("bank", near="Britain graveyard")
    assert [b["name"] for b in banks] == ["West Britain bank", "East Britain bank"]
    assert banks[0]["distance"] == max(abs(1425 - 1384), abs(1690 - 1492))
    assert w.find_place("bank", near="1640,1600")[0]["name"] == "East Britain bank"
    assert w.find_place("bank", near=[1640, 1600], map="Trammel")[0]["name"] == "Trammel Britain bank"


def test_find_place_by_alias_or_item(w):
    assert w.find_place("reagent_vendor", near="Britain healer")[0]["name"] == "Britain mage shop"
    assert w.find_place("bandages", near="1400,1600")[0]["name"] == "Britain healer"
    assert w.find_place("heal potion", near="1400,1600")[0]["name"] == "Britain healer"
    assert w.find_place("black pearl")[0]["kind"] == "mage_shop"
    with pytest.raises(worlds.WorldError):
        w.find_place("bank", near="the moon")


def test_what_spawns_by_area_and_near(w):
    gy = w.what_spawns("britain graveyard")
    assert len(gy) == 1
    types = {c["type"]: c for c in gy[0]["creatures"]}
    assert set(types) == {"Spectre", "Wraith", "Skeleton", "Zombie"}
    assert types["Skeleton"]["difficulty"] == "weak" and types["Wraith"]["difficulty"] == "moderate"
    assert gy[0]["respawn"] == "5 min to 10 min" and (gy[0]["x"], gy[0]["y"]) == (1369, 1475)
    near = w.what_spawns(near="Britain moongate", radius=100)
    assert near[0]["area"] == "near Britain moongate" and near[0]["distance"] == 47
    with pytest.raises(worlds.WorldError):
        w.what_spawns()


def test_hunting_spots_fit_the_level(w):
    new = {s["area"]: s for s in w.hunting_spots("warrior", "new")}
    assert "Britain" not in new  # towns are never hunting spots
    assert "Dragon cave" not in new  # a dragon is two grades too strong
    assert "Covetous Level 1" not in new
    assert new["near Britain moongate"]["fit"] == "good"
    assert new["Britain Graveyard"]["fit"] == "risky" and new["Britain Graveyard"]["risky"] == ["Spectre", "Wraith"]
    assert list(new)[0] == "near Britain moongate"
    moderate = [s["area"] for s in w.hunting_spots("warrior", "moderate")]
    assert moderate[0] == "Britain Graveyard"
    strong = {s["area"]: s for s in w.hunting_spots("mage", "strong", near="Britain")}
    assert strong["Covetous Level 1"]["entrance"] == [2499, 916, 0]
    assert strong["Covetous Level 1"]["distance"] == max(abs(2499 - 1495), abs(916 - 1629))
    with pytest.raises(worlds.WorldError):
        w.hunting_spots("warrior", "godlike")


def test_hunting_spots_show_past_outcomes(w):
    w.add_outcome("Britain Graveyard", "warrior", "kills_per_hour", 40, session="s1")
    w.add_outcome("Britain Graveyard", "warrior", "kills_per_hour", 60, session="s2")
    w.add_outcome("Britain Graveyard", "mage", "kills_per_hour", 5, session="s3")
    spot = next(s for s in w.hunting_spots("warrior", "moderate") if s["area"] == "Britain Graveyard")
    assert spot["outcomes"] == [{"metric": "kills_per_hour", "average": 50.0, "runs": 2}]
    # Per-creature totals stay out of the averages; the area's outcome note comes along in words.
    w.add_outcome("Britain Graveyard", "warrior", "bandages_vs_wraith", 9, session="s1")
    w.add_note("Britain Graveyard: 12 kills a loop for the warrior kit.", area="Britain Graveyard",
               tags=["outcome"], source="outcomes")
    spot = next(s for s in w.hunting_spots("warrior", "moderate") if s["area"] == "Britain Graveyard")
    assert [o["metric"] for o in spot["outcomes"]] == ["kills_per_hour"]
    assert spot["past_results"] == "Britain Graveyard: 12 kills a loop for the warrior kit."
    assert "past_results" not in next(s for s in w.hunting_spots("warrior", "new") if s["area"] != "Britain Graveyard")


def test_route_lists_stored_paths_and_teleporters(w):
    w.add_route("West Britain bank", "Britain Graveyard", [[1425, 1690, 0], [1400, 1600, 0], [1384, 1492, 10]],
                duration_s=95)
    w.add_route("West Britain bank", "Britain Graveyard", [[1425, 1690, 0], [1410, 1560, 0]], outcome="stuck",
                stuck_at=[1410, 1560, 0])
    w.add_route("Covetous Entrance", "Covetous Level 1", [[2499, 916, 0], [5456, 1864, 0]], kind="teleporter",
                source="fixture")
    r = w.route("West Britain bank", "Britain graveyard")
    assert r["straight_line_tiles"] == 198
    assert [s["outcome"] for s in r["stored_routes"]] == ["ok", "stuck"]
    assert r["stored_routes"][1]["stuck_at"] == [1410, 1560, 0]
    cov = w.route("Britain", "5456,1870")
    assert cov["to"]["inside"] == "Covetous" and cov["to"]["entrance"] == [2499, 916, 0]
    assert cov["teleporters"][0]["arrive_at"] == [5456, 1864, 0]


def test_notes_keyword_search_with_and_without_fts(w):
    w.add_note("Liches in Covetous level 3 cast  hard;\nbring  reagents.", area="Covetous", tags=["Danger"])
    w.add_note("The West Britain bank is crowded in the evening.", area="Britain")
    old = w.add_note("The healer stands by the castle.", area="Britain")
    assert w.mark_stale("notes", old)
    hit = w.notes(keywords="lich reagents")
    assert hit[0]["area"] == "Covetous" and hit[0]["tags"] == ["danger"]
    assert hit[0]["text"] == "Liches in Covetous level 3 cast hard; bring reagents."
    assert [n["text"][:12] for n in w.notes(area="britain")] == ["The West Bri"]
    assert len(w.notes(area="Britain", include_stale=True)) == 2
    w.fts = False
    assert w.notes(keywords="reagents")[0]["area"] == "Covetous"
    with pytest.raises(worlds.WorldError):
        w.add_note("   ")


def test_seen_place_retires_a_contradicted_fact(w):
    w.add_place("healer", "Britain healer", 1520, 1611, source="seen")
    rows = w.find_place("healer", near="1500,1600")
    assert [(r["source"], r["x"]) for r in rows] == [("seen", 1520)]
    again = w.add_place("healer", "Britain healer", 1521, 1611, source="seen")
    assert w.db.execute("SELECT COUNT(*) FROM places WHERE source = 'seen'").fetchone()[0] == 1
    assert w.db.execute("SELECT x FROM places WHERE id = ?", (again,)).fetchone()[0] == 1521
    assert w.stats()["places"]["stale"] == 1


def test_seen_place_in_the_same_spot_is_listed_once(w):
    w.add_place("bank", "West Britain bank", 1426, 1690, source="seen")
    banks = w.find_place("bank", near="1425,1690")
    assert [b["name"] for b in banks] == ["West Britain bank", "East Britain bank"]
    assert w.place("west britain bank")[0]["region"] == "Britain"


def test_region_at_and_guards(w):
    assert w.region_at(1495, 1629) == {"name": "Britain", "kind": "town", "guarded": True, "map": "Felucca"}
    assert w.region_at(1369, 1475)["guarded"] is False
    assert w.region_at(10, 10) is None


def test_tool_schemas_and_dispatch(w):
    schemas = worlds.tool_schemas()
    names = [s["function"]["name"] for s in schemas]
    assert names == ["place", "find_place", "hunting_spots", "what_spawns", "route", "notes", "outcomes", "region_at"]
    for s in schemas:
        params = s["function"]["parameters"]
        assert s["type"] == "function" and len(s["function"]["description"]) > 80
        assert params["type"] == "object" and set(params["required"]) <= set(params["properties"])
    json.dumps(schemas)
    assert w.call_tool("find_place", {"kind": "bank", "near": "Britain graveyard", "limit": 1})[0]["name"] == \
        "West Britain bank"
    assert w.call_tool("route", '{"from": "Britain", "to": "Britain graveyard"}')["straight_line_tiles"] > 0
    assert w.call_tool("region_at", {"x": 10, "y": 10}) == {"region": None}
    assert "error" in w.call_tool("find_place", {"kind": "bank", "near": "the moon"})
    assert "error" in w.call_tool("nope", {})
    assert "error" in w.call_tool("place", {"wrong": 1})


def test_stats_counts_by_source(w):
    w.add_note("A note.")
    w.add_note("A guess.", source="model:unverified")
    w.add_note("From a wiki.", source="guide:https://example.org/page")
    st = w.stats()
    assert st["notes"]["by_source"] == {"note": 1, "model:unverified": 1, "guide": 1}
    assert st["places"]["by_source"] == {"fixture": 7}


def test_cli_world_commands_do_not_need_the_game(tmp_path, monkeypatch, capsys):
    def run(*argv):
        monkeypatch.setattr(sys, "argv", ["uo-brain", "world", "--root", str(tmp_path), *argv])
        cli.main()
        return json.loads(capsys.readouterr().out)

    assert run("note", "Bank is west of the graveyard.", "--area", "Britain")["id"] == 1
    assert run("notes", "graveyard")[0]["area"] == "Britain"
    assert run("stats")["notes"]["rows"] == 1
    assert run("find", "bank") == []
