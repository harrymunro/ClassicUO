import json
from pathlib import Path

import pytest

from uo_brain.world import World
from uo_brain.world_import import DEFAULT_MODERNUO, import_modernuo, parse_creatures

REGIONS = [
    {"$type": "TownRegion", "Map": "Felucca", "Name": "Britain", "Priority": 50,
     "Area": [{"x1": 1416, "y1": 1498, "z1": -10, "x2": 1740, "y2": 1777, "z2": 128}],
     "GoLocation": {"x": 1495, "y": 1629, "z": 10}},
    {"$type": "TownRegion", "Map": "Felucca", "Name": "A Wheatfield in Britain 1",
     "Area": [{"x1": 1700, "y1": 1700, "x2": 1720, "y2": 1720}], "GoLocation": {"x": 1710, "y": 1710, "z": 0},
     "Parent": {"Name": "Britain", "Map": "Felucca"}},
    {"$type": "TownRegion", "Map": "Felucca", "NoLogoutDelay": True, "Area": [{"x1": 1500, "y1": 1600, "x2": 1510, "y2": 1610}],
     "Parent": {"Name": "Britain", "Map": "Felucca"}},
    {"$type": "NoHousingRegion", "Map": "Felucca", "Name": "Britain Graveyard",
     "Area": [{"x1": 1333, "y1": 1441, "x2": 1417, "y2": 1523}], "GoLocation": {"x": 1384, "y": 1492, "z": 10}},
    {"$type": "DungeonRegion", "Map": "Felucca", "Name": "Covetous", "Entrance": {"x": 2499, "y": 916, "z": 0},
     "Area": [{"x1": 5376, "y1": 1793, "x2": 5632, "y2": 2048}], "GoLocation": {"x": 5456, "y": 1862, "z": 0}},
    {"$type": "TownRegion", "Map": "Felucca", "Name": "Buccaneer's Den", "GuardsDisabled": True,
     "Area": [{"x1": 2612, "y1": 2057, "x2": 2776, "y2": 2267}], "GoLocation": {"x": 2706, "y": 2163, "z": 0}},
    {"$type": "NoHousingRegion", "Map": "Felucca", "Name": "Jhelom Islands",
     "Area": [{"x1": 1100, "y1": 3500, "x2": 1600, "y2": 4100}], "GoLocation": {"x": 1383, "y": 3815, "z": 0}},
    {"$type": "TownRegion", "Map": "Felucca", "Name": "Jhelom", "Area": [{"x1": 1300, "y1": 3700, "x2": 1500, "y2": 3900}],
     "GoLocation": {"x": 1383, "y": 3815, "z": 0}, "Parent": {"Name": "Jhelom Islands", "Map": "Felucca"}},
    {"$type": "DungeonRegion", "Map": "Felucca", "Name": "Misc Dungeons", "Entrance": {"x": 1492, "y": 1641, "z": 0},
     "Area": [{"x1": 5885, "y1": 1281, "x2": 6127, "y2": 1730}], "GoLocation": {"x": 6032, "y": 1499, "z": 0}},
    {"$type": "TownRegion", "Map": "Trammel", "Name": "Britain", "Area": [{"x1": 1416, "y1": 1498, "x2": 1740, "y2": 1777}]},
]

LOCATIONS = {"name": "Felucca", "categories": [
    {"name": "Factions", "locations": [{"name": "True Britannians", "location": [1419, 1622, 20]}]},
    {"name": "Dungeons", "categories": [{"name": "Covetous", "locations": [
        {"name": "Entrance", "location": [2499, 919, 0]}, {"name": "Level 1", "location": [5456, 1863, 0]},
        {"name": "Level 2", "location": [5614, 1997, 0]}]}]},
    {"name": "Internal", "locations": [{"name": "Green Acres", "location": [5445, 1153, 0]}]},
    {"name": "Shrines", "locations": [{"name": "Spirituality", "location": [1589, 2485, 5]}]},
    {"name": "Towns", "categories": [{"name": "Britain", "locations": [
        {"name": "Cemetery", "location": [1384, 1497, 10]}, {"name": "Center", "location": [1475, 1645, 20]}]}]},
]}


def spawner(x, y, entries, count=None, guid=None, z=0):
    return {"$type": "Spawner", "guid": guid or f"g-{x}-{y}", "name": "Spawner", "location": [x, y, z], "map": "Felucca",
            "count": count or sum(n for _, n in entries), "minDelay": "00:05:00", "maxDelay": "00:10:00",
            "homeRange": 20, "walkingRange": 30,
            "entries": [{"name": t, "maxCount": n, "probability": 100} for t, n in entries]}


SPAWNS = {
    "shared/felucca/Graveyards.json": [
        spawner(1369, 1475, [("Spectre", 2), ("Wraith", 2), ("Skeleton", 3), ("Zombie", 4)], count=9, z=10)],
    "shared/felucca/Vendors.json": [
        spawner(1425, 1690, [("Banker", 1), ("Minter", 1)]), spawner(1650, 1608, [("Banker", 1), ("Minter", 1)]),
        spawner(1471, 1611, [("Healer", 1), ("HealerGuildmaster", 1)]), spawner(1485, 1550, [("Mage", 1)]),
        spawner(1420, 3800, [("Banker", 1)]), spawner(1710, 1710, [("Farmer", 1)]), spawner(1500, 1700, [("Rat", 1)])],
    "shared/felucca/Covetous.json": [spawner(5456, 1870, [("Lich", 2), ("Skeleton", 4)])],
    "shared/felucca/BritainSewer.json": [spawner(6040, 1500, [("Rat", 6)])],
    "shared/felucca/Reagents.json": [spawner(1300, 1650, [("BlackPearl", 5)])],
    "shared/felucca/TownsPeople.json": [spawner(1480, 1620, [("Noble", 2)])],
    "shared/felucca/WildLife.json": [spawner(1231, 1462, [("WanderingHealer", 1), ("Rat", 3)])],
    "post-uoml/felucca/Vendors.json": [spawner(1602, 1712, [("Provisioner", 1)])],
    "uoml/felucca/Old.json": [spawner(100, 100, [("Dragon", 1)])],
    "shared/trammel/Graveyards.json": [spawner(1369, 1475, [("Skeleton", 3)])],
}

TELEPORTERS = [
    {"src": {"map": "Felucca", "loc": [2499 + i, 916, 0]}, "dst": {"map": "Felucca", "loc": [5456 + i, 1864, 0]},
     "back": True} for i in range(3)
] + [
    {"src": {"map": "Felucca", "loc": [1491, 1640, 24]}, "dst": {"map": "Felucca", "loc": [6032, 1499, 31]}, "back": False},
    {"src": {"map": "Felucca", "loc": [1491, 1642, 24]}, "dst": {"map": "Felucca", "loc": [6032, 1501, 31]}, "back": False},
    {"src": {"map": "Felucca", "loc": [4000, 4000, 0]}, "dst": {"map": "TerMur", "loc": [10, 10, 0]}, "back": False},
    {"src": {"map": "Trammel", "loc": [2499, 916, 0]}, "dst": {"map": "Trammel", "loc": [5456, 1864, 0]}, "back": True},
]

MOBILES = '''
namespace Server.Mobiles
{
    public partial class Skeleton : BaseCreature
    {
        public Skeleton() : base(AIType.AI_Melee)
        {
            Body = Utility.RandomList(50, 56);
            SetStr(56, 80);
            SetHits(34, 48);
            SetDamage(3, 7);
            Fame = 450;
            Karma = -450;
        }
        public override string DefaultName => "a skeleton";
    }

    public partial class Zombie : BaseCreature
    {
        public Zombie() : base(AIType.AI_Melee)
        {
            Body = 3;
            SetHits(28, 42);
            SetDamage(3, 7);
            Fame = 600;
            Karma = -600;
        }
        public override string DefaultName => "a zombie";
    }

    public partial class Wraith : BaseCreature
    {
        public Wraith() : base(AIType.AI_Mage)
        {
            Body = 26;
            SetHits(46, 60);
            SetDamage(7, 11);
            Fame = 4000;
            Karma = -4000;
        }
        public override string DefaultName => "a wraith";
    }

    public partial class Spectre : Wraith
    {
        public override string DefaultName => "a spectre";
    }

    public class Lich : BaseCreature
    {
        public Lich()
        {
            Body = 0x18;
            SetHits(103, 120);
            SetDamage(24, 26);
            Fame = 8000;
            Karma = -8000;
        }
    }

    public class Rat : BaseCreature
    {
        public Rat()
        {
            SetStr(9);
            SetDamage(1, 2);
            Fame = 150;
        }
    }

    public class Banker : BaseVendor
    {
        public Banker() : base("the banker") { }
    }
}
'''

MOONGATES = '''
public class PMList
{
    public static readonly PMList Trammel =
        new(1012000, 1012012, Map.Trammel,
            [
                new PMEntry(new Point3D(1336, 1997, 5), 1012004),   // Britain
            ]
        );

    public static readonly PMList Felucca =
        new(1012001, 1012013, Map.Felucca,
            [
                new PMEntry(new Point3D(1336, 1997, 5), 1012004),   // Britain
                new PMEntry(new Point3D(3563, 2139, Map.Felucca.GetAverageZ(3563, 2139)), 1012010), // (New) Magincia
                new PMEntry(new Point3D(2711, 2234, 0), 1019001)                                    // Buccaneer's Den
            ]
        );
}
'''


@pytest.fixture
def modernuo(tmp_path) -> Path:
    root = tmp_path / "ModernUO"
    data = root / "Distribution" / "Data"
    files = {
        data / "regions.json": json.dumps(REGIONS),
        data / "Locations" / "felucca.json": json.dumps(LOCATIONS),
        data / "teleporters.json": json.dumps(TELEPORTERS),
        root / "Distribution" / "Configuration" / "expansion.json": json.dumps({"Id": 11, "Name": "Endless Journey"}),
        root / "Projects" / "UOContent" / "Mobiles" / "Monsters" / "Undead.cs": MOBILES,
        root / "Projects" / "UOContent" / "Items" / "Misc" / "PublicMoongate.cs": MOONGATES,
    }
    for rel, spawners in SPAWNS.items():
        files[data / "Spawns" / rel] = json.dumps(spawners)
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


@pytest.fixture
def w(tmp_path, modernuo):
    w = World.open("local", root=tmp_path / "worlds")
    w.counts = import_modernuo(w, modernuo)
    yield w
    w.close()


def names(rows):
    return [r["name"] for r in rows]


def test_graveyard_spawner_and_creature_grades(w):
    gy = w.what_spawns("Britain Graveyard")
    assert len(gy) == 1 and (gy[0]["x"], gy[0]["y"], gy[0]["z"]) == (1369, 1475, 10)
    assert gy[0]["count"] == 9 and gy[0]["range"] == 30 and gy[0]["respawn"] == "5 min to 10 min"
    by_type = {c["type"]: c for c in gy[0]["creatures"]}
    assert set(by_type) == {"Spectre", "Wraith", "Skeleton", "Zombie"}
    assert by_type["Spectre"] == {"type": "Spectre", "name": "a spectre", "max_count": 2, "difficulty": "moderate",
                                  "hits": "46-60", "damage": "7-11", "evil": True}  # stats inherited from Wraith
    assert by_type["Skeleton"]["difficulty"] == "weak"
    assert gy[0]["source"] == "modernuo:Distribution/Data/Spawns/shared/felucca/Graveyards.json"


def test_only_the_files_the_server_loads(w):
    creatures = {c["type"] for s in w.what_spawns(near="100,100", radius=50) for c in s["creatures"]}
    assert "Dragon" not in creatures  # uoml folder is for pre-Stygian Abyss servers
    assert w.db.execute("SELECT COUNT(*) FROM spawns WHERE map != 'Felucca'").fetchone()[0] == 0
    assert w.db.execute("SELECT COUNT(*) FROM regions WHERE map = 'Trammel'").fetchone()[0] == 0
    assert not w.db.execute("SELECT 1 FROM spawns WHERE creature IN ('Noble', 'Banker', 'BlackPearl')").fetchall()


def test_vendors_become_named_places(w):
    banks = w.find_place("bank", near="Britain graveyard")
    assert names(banks) == ["West Britain bank", "East Britain bank", "Jhelom bank"]  # banker + minter = one bank
    assert banks[0]["x"] == 1425 and banks[0]["region"] == "Britain"
    healer = w.find_place("healer", near="Britain graveyard", limit=1)[0]
    assert healer["name"] == "Britain healer" and "bandages" in healer["sells"] and healer["note"] == "Resurrects ghosts."
    assert w.find_place("healer", near="1231,1462", limit=1)[0]["name"].startswith("wandering healer")
    assert names(w.find_place("guildmaster")) == ["Britain healer guildmaster"]
    assert w.find_place("reagent_vendor", near="Britain")[0]["name"] == "Britain mage shop"
    assert w.find_place("provisioner")[0]["source"].endswith("post-uoml/felucca/Vendors.json")
    assert names(w.find_place("farmer")) == ["Britain farmer"]  # a field inside the town is still Britain
    assert w.place("britain bank")[0]["name"] == "West Britain bank"


def test_regions_towns_and_guards(w):
    assert w.region_at(1495, 1629)["guarded"] is True
    assert w.region_at(2700, 2160) == {"name": "Buccaneer's Den", "kind": "town", "guarded": False, "map": "Felucca"}
    assert w.region_at(1710, 1710)["part_of"] == "Britain"
    assert w.region_at(1420, 3800)["kind"] == "town"  # Jhelom sits inside the Jhelom Islands area
    towns = names(w.find_place("town"))
    assert {"Britain", "Jhelom", "Buccaneer's Den"} <= set(towns) and "A Wheatfield in Britain 1" not in towns


def test_locations_moongates_and_reagents(w):
    assert w.place("covetous entrance")[0]["kind"] == "dungeon_entrance"
    assert w.place("Covetous level 2")[0]["kind"] == "dungeon_level"
    assert w.place("shrine of spirituality")[0]["kind"] == "shrine"
    assert w.place("Britain Cemetery")[0]["kind"] == "graveyard"
    assert not w.place("True Britannians") and not w.place("Green Acres")  # faction and staff spots
    gates = {p["name"]: p for p in w.find_place("moongate", limit=10)}
    assert set(gates) == {"Britain moongate", "Magincia moongate", "Buccaneer's Den moongate"}
    assert gates["Magincia moongate"]["z"] == 0
    reagent = w.find_place("reagent_spawn")[0]
    assert reagent["name"].startswith("black pearl") and "respawn 5-10 min" in reagent["note"]


def test_teleporters_are_grouped_and_routed(w):
    tps = {p["name"]: p for p in w.find_place("teleporter", limit=10)}
    assert "near Covetous Entrance teleporter to Covetous Level 1" in tps
    assert "works both ways" in tps["near Covetous Entrance teleporter to Covetous Level 1"]["note"]
    assert "Britain teleporter to Britain Sewer" in tps  # two tiles a step apart, one teleporter
    assert any("TerMur" in p["note"] for p in tps.values())
    route = w.route("Britain", "Covetous level 1")
    assert route["to"]["entrance"] == [2499, 916, 0]
    assert route["teleporters"][0]["to"] == "Covetous Level 1"
    kinds = [r[0] for r in w.db.execute("SELECT from_name || ' > ' || to_name FROM routes ORDER BY id")]
    assert "Covetous Level 1 > near Covetous Entrance" in kinds  # the way back


def test_spawn_areas_in_dungeons_and_catch_alls(w):
    assert w.what_spawns("Covetous")[0]["area"] == "Covetous Level 1"
    assert w.what_spawns("Britain Sewer")[0]["region"] == "Misc Dungeons"
    spots = {s["area"]: s for s in w.hunting_spots("warrior", "strong", near="Britain")}
    assert spots["Covetous Level 1"]["entrance"] == [2499, 916, 0]


def test_reimport_is_idempotent_and_keeps_other_sources(w, modernuo):
    w.add_note("Bring a lantern into the sewer.", area="Britain Sewer")
    w.add_place("bank", "West Britain bank", 1430, 1700, source="seen")  # moved, as seen in game
    before = w.stats()
    again = import_modernuo(w, modernuo)
    after = w.stats()
    assert again == w.counts
    for t in ("places", "regions", "spawns", "creatures", "routes", "notes"):
        assert after[t]["rows"] == before[t]["rows"], t
    assert w.notes(keywords="lantern")[0]["area"] == "Britain Sewer"
    west = w.find_place("bank", near="1425,1690", limit=1)[0]
    assert (west["source"], west["x"]) == ("seen", 1430)  # the imported copy stays retired


def test_creature_parsing(modernuo):
    rows = {r["type"]: r for r in parse_creatures(modernuo / "Projects" / "UOContent" / "Mobiles", modernuo)}
    assert "Banker" not in rows
    assert rows["Skeleton"]["body"] == 50 and rows["Lich"]["body"] == 0x18
    assert rows["Rat"]["hits_min"] == rows["Rat"]["hits_max"] == 9 and rows["Rat"]["note"] == "hits taken from strength"
    assert rows["Lich"]["difficulty"] == "strong" and rows["Lich"]["name"] is None
    assert rows["Zombie"]["source"] == "modernuo:Projects/UOContent/Mobiles/Monsters/Undead.cs"


def test_missing_folder_is_reported(tmp_path):
    with World.open("local", root=tmp_path) as w, pytest.raises(FileNotFoundError):
        import_modernuo(w, tmp_path / "nope")


REAL = DEFAULT_MODERNUO / "Distribution" / "Data" / "regions.json"


@pytest.mark.skipif(not REAL.exists(), reason="no ModernUO checkout at ~/Workspace/ModernUO")
def test_real_modernuo_data(tmp_path):
    with World.open("local", root=tmp_path) as w:
        import_modernuo(w)
        gy = [s for s in w.what_spawns("Britain Graveyard") if (s["x"], s["y"]) == (1369, 1475)]
        assert gy and {"Skeleton", "Zombie", "Wraith", "Spectre"} <= {c["type"] for c in gy[0]["creatures"]}
        assert w.find_place("bank", near="Britain")[0]["region"] == "Britain"
        assert w.find_place("healer", near="Britain")[0]["region"] == "Britain"
        bank = w.find_place("bank", near="Britain graveyard", limit=1)[0]
        assert (bank["name"], bank["x"], bank["y"]) == ("West Britain bank", 1425, 1690)
