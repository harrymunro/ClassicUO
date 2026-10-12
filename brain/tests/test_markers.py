import json
import sys
from pathlib import Path

import pytest

from uo_brain import cli, markers, recorder, world_import
from uo_brain.markers import import_markers, parse_csv, parse_uoam, place_kind
from uo_brain.world import KIND_ALIASES, PLACE_KINDS, World, trust

FIXTURES = Path(__file__).parent / "fixtures" / "markers"
MAP_FILE = FIXTURES / "britain.map"
CSV_FILE = FIXTURES / "britain.csv"
SERVER = "modernuo:Distribution/Data/Spawns/shared/felucca/Vendors.json"


@pytest.fixture
def w(tmp_path):
    w = World.open("test", root=tmp_path)
    w.replace_source("regions", "modernuo:Distribution/Data/regions.json", [
        {"name": "Britain", "map": "Felucca", "kind": "town", "guarded": 1, "rects": json.dumps([[1416, 1498, 1740, 1777]]),
         "go_x": 1495, "go_y": 1629, "go_z": 0}])
    w.replace_source("places", SERVER, [
        {"kind": "bank", "name": "West Britain bank", "map": "Felucca", "x": 1427, "y": 1688, "region": "Britain"},
        {"kind": "bank", "name": "East Britain bank", "map": "Felucca", "x": 1650, "y": 1608, "region": "Britain"},
        {"kind": "healer", "name": "Britain healer", "map": "Felucca", "x": 1471, "y": 1611, "region": "Britain"},
        {"kind": "moongate", "name": "Britain moongate", "map": "Felucca", "x": 1336, "y": 1997},
        {"kind": "dungeon_entrance", "name": "Covetous Entrance", "map": "Felucca", "x": 2499, "y": 919},
    ])
    yield w
    w.close()


def test_uoam_lines_header_signs_spaces_and_bad_lines():
    parsed = parse_uoam(MAP_FILE.read_text())
    assert parsed.format == "uoam"
    assert len(parsed.markers) == 20  # every good line, though bad ones sit among them
    first = parsed.markers[0]
    assert (first.type, first.x, first.y, first.facet, first.name, first.line, first.town) == \
        ("bank", 1425, 1690, 0, "The First Bank of Britain", 2, False)
    healer = parsed.markers[1]
    assert healer.town and healer.type == "healer"  # "-" is a town building
    by_line = {m.line: m for m in parsed.markers}
    assert by_line[12].type == "warriors guild" and by_line[12].name == "Warriors Guild of Britain"
    assert by_line[14].type == "Body of Water" and by_line[14].name == "Arrow's Point Bay"
    assert by_line[15].name == ""  # a teleporter marker with no name
    assert [(e["line"], e["why"]) for e in parsed.errors] == [
        (19, "a marker line starts with + or -"),
        (20, "no marker type before a colon"),
        (21, "expected x y facet and a name after the colon"),
        (22, "expected x y facet and a name after the colon"),
    ]


def test_uoam_version_line_is_checked_but_not_required():
    other = parse_uoam("2\n+bank: 10 10 0 A Bank\n")
    assert len(other.markers) == 1 and "format version 2" in other.errors[0]["why"]
    none = parse_uoam("+bank: 10 10 0 A Bank\n+inn: 20 20 0 An Inn\n")
    assert len(none.markers) == 2 and none.errors[0]["line"] == 1 and "no format version" in none.errors[0]["why"]


def test_client_csv_fields_and_bad_lines():
    parsed = parse_csv(CSV_FILE.read_text())
    assert [(m.x, m.y, m.facet, m.name, m.type) for m in parsed.markers[:3]] == [
        (1425, 1690, 0, "West Britain Bank", "bank"), (1472, 1607, 0, "Britain Healer", "healer"),
        (1336, 1997, 0, "Britain Moongate", "moongate")]
    assert parsed.markers[5].type == "" and parsed.markers[5].name == "Britain Graveyard"  # x,y,facet,name only
    assert [e["line"] for e in parsed.errors] == [2, 9, 10, 11]
    assert "a comma in the name" in parsed.errors[1]["why"]


def test_marker_types_map_to_place_kinds():
    assert place_kind("bank", "The First Bank") == ("bank", False)
    assert place_kind("BANK", "x") == ("bank", False)
    assert place_kind("reagents", "Ethereal Goods") == ("mage_shop", False)
    assert place_kind("dungeon", "Covetous") == ("dungeon_entrance", False)
    assert place_kind("arms", "Heavy Metal Armorer") == ("arms_shop", False)
    assert place_kind("fishermans guild", "x") == ("guildmaster", False)
    assert place_kind("healers guild", "x") == ("healer", False)
    assert place_kind("small boat (N)", "x") == (None, False)
    assert place_kind("villa", "Someone's villa") == (None, False)
    assert place_kind("treasure", "NENWa1") == (None, False)
    assert place_kind("point of interest", "East Side Bank") == ("bank", True)
    assert place_kind("other", "Mess Hall") == ("landmark", False)
    assert place_kind("champion", "Champ Valor") == ("landmark", False)


def test_every_kind_is_one_the_store_knows():
    """The type table, the aliases a planner can ask for and the other importers all name
    kinds from world.PLACE_KINDS."""
    kinds = set(PLACE_KINDS)
    assert {k for k in markers.TYPE_KINDS.values() if k} <= kinds
    assert {k for _, k in markers.NAME_KINDS} <= kinds
    assert {"guildmaster", "landmark"} <= kinds
    assert {k for v in KIND_ALIASES.values() for k in v} <= kinds
    assert {k for k, _, _ in world_import.VENDORS.values()} <= kinds
    assert {k for _, k in world_import.LOCATION_KINDS} <= kinds
    assert {k for k in recorder.TITLES.values() if k} <= kinds


def test_facet_numbers_uoam_and_client():
    assert markers.facet_maps(0, "uoam") == ("Felucca", "Trammel")
    assert markers.facet_maps(1, "uoam") == ("Felucca",)
    assert markers.facet_maps(3, "uoam") == ("Ilshenar",)
    assert markers.facet_maps(1, "client") == ("Trammel",)
    assert markers.facet_maps(5, "client") == ("TerMur",)
    assert markers.facet_maps(5, "uoam") is None


def test_import_uoam_file(w):
    out = import_markers(w, [MAP_FILE])
    f = out["files"][0]
    assert (f["source"], f["format"], f["facets"], f["markers"], f["places"]) == \
        ("markers:britain.map", "uoam", "uoam", 20, 28)
    assert f["by_map"] == {"Felucca": 13, "Trammel": 13, "Ilshenar": 2}
    assert f["skipped"] == {"treasure": 1, "small house": 1, "small boat (n)": 1}
    assert f["landmarks_by_type"] == {"exit": 2, "body of water": 2}
    assert f["error_count"] == 5 and f["errors"][-1]["why"] == "facet 7 means no map in uoam numbering"

    rows = {(r["map"], r["name"]): dict(r) for r in w.db.execute("SELECT * FROM places WHERE source = 'markers:britain.map'")}
    bank = rows[("Felucca", "The First Bank of Britain")]
    assert (bank["kind"], bank["x"], bank["y"], bank["region"]) == ("bank", 1425, 1690, "Britain")
    assert rows[("Trammel", "The First Bank of Britain")]["region"] is None  # no Trammel regions in this store
    assert rows[("Felucca", "Covetous (dungeon)")]["kind"] == "dungeon_entrance"
    assert rows[("Felucca", "Covetous (exit)")]["kind"] == "landmark"
    assert rows[("Felucca", "East Side Bank")]["note"] == "map marker (point of interest); kind from its name"
    assert ("Felucca", "Buccaneer's Den Moongate") in rows and ("Trammel", "Buccaneer's Den Moongate") not in rows
    assert ("Trammel", "Haven Moongate") in rows and ("Ilshenar", "Compassion") in rows
    assert ("Ilshenar", "teleporter at (791, 1548)") in rows
    assert ("Felucca", "Counselor's Guild (Britain) at (1418, 1503)") in rows
    assert ("Trammel", "Counselor's Guild at (1628, 1664)") in rows


def test_import_csv_file_with_client_facets(w):
    f = import_markers(w, [CSV_FILE])["files"][0]
    assert (f["format"], f["facets"], f["markers"], f["places"]) == ("csv", "client", 8, 6)
    assert f["by_map"] == {"Felucca": 5, "Trammel": 1}
    assert f["by_kind"]["dungeon_entrance"] == 1 and f["landmarks_by_type"] == {"no type": 1}
    assert [e["line"] for e in f["errors"]] == [2, 9, 10, 11, 12, 13]
    assert "off Felucca" in f["errors"][4]["why"]


def test_maps_filter_and_numbering_override(w):
    assert import_markers(w, [MAP_FILE], maps=["Felucca"])["files"][0]["places"] == 13
    # Read with map indexes, the Haven moongate's facet 2 is Ilshenar, which it lies off.
    f = import_markers(w, [MAP_FILE], numbering="client")["files"][0]
    assert f["by_map"] == {"Felucca": 12, "Trammel": 1, "Malas": 2}
    assert any("off Ilshenar" in e["why"] for e in f["errors"])
    with pytest.raises(ValueError):
        import_markers(w, [MAP_FILE], numbering="sideways")


def test_reimport_replaces_only_its_own_rows(w):
    import_markers(w, [CSV_FILE])
    import_markers(w, [MAP_FILE])
    count = w.db.execute("SELECT source, COUNT(*) FROM places GROUP BY source ORDER BY source").fetchall()
    import_markers(w, [MAP_FILE])
    again = w.db.execute("SELECT source, COUNT(*) FROM places GROUP BY source ORDER BY source").fetchall()
    assert [tuple(r) for r in again] == [tuple(r) for r in count] == [
        ("markers:britain.csv", 6), ("markers:britain.map", 28), (SERVER, 5)]


def test_a_folder_imports_each_file(w, tmp_path):
    folder = tmp_path / "Client"
    folder.mkdir()
    (folder / "a.map").write_bytes(MAP_FILE.read_bytes())
    (folder / "b.csv").write_bytes(CSV_FILE.read_bytes())
    (folder / "notes.txt").write_text("not a marker file")
    out = import_markers(w, [folder], maps=["Felucca"])
    assert [f["source"] for f in out["files"]] == ["markers:a.map", "markers:b.csv"]
    with pytest.raises(FileNotFoundError):
        import_markers(w, [tmp_path / "missing.map"])


def test_ansi_names_read(w, tmp_path):
    f = tmp_path / "ansi.map"
    f.write_bytes("3\r\n+inn: 1500 1600 0 Caf\xe9 du Lac\r\n".encode("cp1252"))
    import_markers(w, [f], maps=["Felucca"])
    assert w.db.execute("SELECT name FROM places WHERE source = 'markers:ansi.map'").fetchone()[0] == "Caf\xe9 du Lac"


def test_agreement_with_the_store(w):
    agree = import_markers(w, [MAP_FILE])["agreement"]
    bank = agree["bank"]["Felucca"]
    assert (bank["markers"], bank["matched"], bank["store_places"], bank["store_places_with_a_marker"]) == (2, 1, 2, 1)
    assert bank["markers_without_a_match"][0]["marker"] == "East Side Bank"
    assert bank["markers_without_a_match"][0]["tiles"] == 42
    assert bank["store_places_without_a_marker"][0]["place"] == "East Britain bank"
    assert "Trammel" not in agree["bank"]  # nothing else known there to compare with
    assert agree["healer"]["Felucca"]["matched"] == 1 and agree["healer"]["Felucca"]["max_tiles_when_matched"] == 4
    assert agree["moongate"]["Felucca"]["matched"] == 1
    assert agree["moongate"]["Felucca"]["markers_without_a_match"][0]["marker"] == "Buccaneer's Den Moongate"
    assert agree["dungeon_entrance"]["Felucca"]["matched"] == 1


def test_markers_rank_below_the_server_and_what_was_seen(w):
    import_markers(w, [MAP_FILE], maps=["Felucca"])
    assert trust("markers:britain.map") < trust(SERVER) < trust("seen")
    assert trust("model:unverified") < trust("markers:britain.map")
    # The marker for the bank the server already has is the same place: listed once, the server's.
    banks = w.find_place("bank", near="1425,1690")
    assert [(b["name"], b["source"]) for b in banks] == [
        ("West Britain bank", SERVER), ("East Side Bank", "markers:britain.map"), ("East Britain bank", SERVER)]
    # Same name from both: the server's comes first.
    gates = w.place("britain moongate", limit=2)
    assert [g["source"] for g in gates] == [SERVER, "markers:britain.map"]
    # A place only the markers know is found.
    assert w.find_place("mage_shop", near="1500,1650")[0]["name"] == "Ethereal Goods"
    assert w.find_place("weapon_vendor", near="1500,1650")[0]["name"] == "Heavy Metal Armorer"
    assert w.place("covetous", limit=1)[0]["kind"] == "dungeon_entrance"


def test_seen_place_confirms_a_marker_nearby(w):
    import_markers(w, [MAP_FILE], maps=["Felucca"])
    w.db.execute("UPDATE places SET last_seen = '2026-01-01T00:00:00+00:00' WHERE source = 'markers:britain.map'")
    w.db.commit()
    w.add_place("bank", "Britain bank", 1612, 1653, source="seen")
    row = w.db.execute("SELECT last_seen, stale FROM places WHERE name = 'East Side Bank'").fetchone()
    assert row["last_seen"] > "2026-01-02" and row["stale"] == 0
    far = w.db.execute("SELECT last_seen FROM places WHERE name = 'Ethereal Goods'").fetchone()
    assert far["last_seen"].startswith("2026-01-01")
    banks = w.find_place("bank", near="1610,1650", limit=2)
    assert banks[0]["source"] == "seen" and all(b["name"] != "East Side Bank" for b in banks)


def test_cli_import_markers(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["uo-brain", "world", "--root", str(tmp_path), "import-markers", str(MAP_FILE),
                                      str(CSV_FILE), "--maps", "Felucca"])
    cli.main()
    out = json.loads(capsys.readouterr().out)
    assert [(f["source"], f["places"]) for f in out["files"]] == [("markers:britain.map", 13), ("markers:britain.csv", 5)]
    assert out["agreement"] == {}  # an empty store has nothing to compare with
    monkeypatch.setattr(sys, "argv", ["uo-brain", "world", "--root", str(tmp_path), "find", "healer"])
    cli.main()
    assert {p["name"] for p in json.loads(capsys.readouterr().out)} == {"Healer of Britain", "Britain Healer"}
