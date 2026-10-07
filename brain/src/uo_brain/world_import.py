"""Fill a shard's world store from a ModernUO server's data and source files.

The local test server is ModernUO. Its Distribution/Data folder lists the named places,
regions, spawners and teleporters, and its C# gives each creature's hits, damage and
fame. Importing them makes the local store complete from the start. On a public shard,
where none of this is available, the same tables fill from what the agent sees.

Every row is tagged "modernuo:<path relative to the ModernUO folder>". A re-import first
removes every modernuo row, so running it twice gives the same store, and rows from other
sources (notes, guides, what the agent saw) are left alone.

Only the files a server with the configured expansion loads are read: for Felucca that
is Spawns/shared/felucca plus Spawns/post-uoml/felucca (uoml before Stygian Abyss).
"""

import json
import re
from pathlib import Path
from typing import Any, Iterable

from .world import TABLES, Region, RegionIndex, World, compass, grade_creature, now, tiles

DEFAULT_MODERNUO = Path.home() / "Workspace" / "ModernUO"
SA_EXPANSION_ID = 8  # ModernUO's Expansion.SA; from here on it loads Spawns/post-uoml

# What a vendor type is and sells, written from ModernUO's SB*Info classes
# (Projects/UOContent/Mobiles/Vendors/SBInfo). Keys are spawn entry names, lower-cased.
VENDORS: dict[str, tuple[str, str, list[str]]] = {
    "banker": ("bank", "bank", ["bank box", "commodity deeds", "vendor contracts"]),
    "minter": ("bank", "bank", ["bank box", "commodity deeds", "vendor contracts"]),
    "healer": ("healer", "healer", ["resurrection", "bandages", "lesser heal potions", "refresh potions",
                                    "ginseng", "garlic"]),
    "wanderinghealer": ("healer", "wandering healer", ["resurrection", "bandages", "lesser heal potions",
                                                       "refresh potions", "ginseng", "garlic"]),
    "mage": ("mage_shop", "mage shop", ["reagents", "black pearl", "bloodmoss", "garlic", "ginseng",
                                        "mandrake root", "nightshade", "spiders silk", "sulfurous ash",
                                        "spellbook", "spell scrolls", "blank scrolls", "recall runes", "scribes pen",
                                        "lesser heal potions", "lesser cure potions", "refresh potions",
                                        "potions", "wizards hat"]),
    "alchemist": ("alchemist", "alchemist", ["reagents", "black pearl", "bloodmoss", "garlic", "ginseng",
                                             "mandrake root", "nightshade", "spiders silk", "sulfurous ash",
                                             "lesser heal potions", "lesser cure potions", "refresh potions",
                                             "potions", "empty bottles", "mortar and pestle"]),
    "herbalist": ("herbalist", "herbalist", ["ginseng", "garlic", "mandrake root", "nightshade", "bloodmoss",
                                             "mortar and pestle", "empty bottles"]),
    "provisioner": ("provisioner", "provisioner", ["backpacks", "bags", "pouches", "torches", "lanterns",
                                                   "candles", "arrows", "bolts", "lockpicks", "food", "hats",
                                                   "bedroll", "kindling", "keys", "garlic", "ginseng"]),
    "weaponsmith": ("weapon_vendor", "weaponsmith", ["weapons", "swords", "katana", "axes", "maces",
                                                     "spears", "daggers", "bows", "crossbows", "staves"]),
    "armorer": ("armour_vendor", "armourer", ["armour", "leather armour", "studded armour", "ringmail",
                                              "chainmail", "plate armour", "helmets", "shields"]),
    "blacksmith": ("blacksmith", "blacksmith", ["weapons", "armour", "shields", "iron ingots", "tongs",
                                                "smiths hammer"]),
    "innkeeper": ("inn", "inn", ["food", "torches", "candles", "backpacks"]),
    "tavernkeeper": ("tavern", "tavern", ["food", "drinks"]),
    "barkeeper": ("tavern", "tavern", ["food", "drinks"]),
    "bowyer": ("bowyer", "bowyer", ["bows", "crossbows", "arrows", "bolts", "fletching tools"]),
    "tailor": ("tailor", "tailor", ["cloth", "clothing", "sewing kits", "dyes", "dye tubs", "scissors"]),
    "weaver": ("tailor", "weaver", ["cloth", "dyes", "dye tubs"]),
    "scribe": ("scribe", "scribe", ["blank scrolls", "scribes pen", "books"]),
    "tinker": ("tinker", "tinker", ["tools", "tinker tools", "lockpicks", "pickaxes", "shovels", "sewing kits",
                                    "keys", "sextant"]),
    "butcher": ("butcher", "butcher", ["raw meat", "bacon", "ham", "sausages", "skinning knife", "cleaver"]),
    "baker": ("baker", "baker", ["bread", "cakes", "pies", "cookies", "flour"]),
    "cook": ("cook", "cook", ["food", "skillet", "rolling pin"]),
    "fisherman": ("fisherman", "fisherman", ["fishing pole", "fish"]),
    "animaltrainer": ("stable", "stable", ["stabling", "horses", "pack horses", "pack llamas", "pets"]),
    "veterinarian": ("stable", "veterinarian", ["bandages", "pack horses", "pack llamas"]),
    "jeweler": ("jeweller", "jeweller", ["jewellery", "rings", "gems"]),
    "carpenter": ("carpenter", "carpenter", ["boards", "logs", "carpentry tools", "furniture"]),
    "mapmaker": ("mapmaker", "mapmaker", ["blank maps", "maps", "blank scrolls"]),
    "shipwright": ("shipwright", "shipwright", ["boat deeds"]),
    "tanner": ("tanner", "tanner", ["leather", "leather armour", "studded armour", "backpacks", "bags",
                                    "pouches"]),
    "cobbler": ("cobbler", "cobbler", ["shoes", "boots", "sandals"]),
    "farmer": ("farmer", "farmer", ["fruit", "vegetables", "eggs"]),
    "furtrader": ("furtrader", "fur trader", ["hides", "furs"]),
}

REAGENTS = {"blackpearl": "black pearl", "bloodmoss": "bloodmoss", "garlic": "garlic", "ginseng": "ginseng",
            "mandrakeroot": "mandrake root", "nightshade": "nightshade", "spiderssilk": "spiders silk",
            "sulfurousash": "sulfurous ash"}

# Spawn files that hold people and crops rather than things to hunt.
NOT_HUNTING = {"vendors.json", "townspeople.json", "felcropsls.json"}

REGION_KINDS = {"TownRegion": "town", "GuardedRegion": "guarded", "DungeonRegion": "dungeon",
                "NoHousingRegion": "area", "BaseRegion": "area", "JailRegion": "jail", "GreenAcresRegion": "staff"}

LOCATION_KINDS = [(r"\b(cemetery|graveyard)\b", "graveyard"), (r"\bdocks?\b", "docks"), (r"\bbank\b", "bank"),
                  (r"\binn\b", "inn"), (r"\bmoongate\b", "moongate")]


def import_modernuo(world: World, modernuo_dir: Path | str = DEFAULT_MODERNUO,
                    maps: Iterable[str] = ("Felucca",)) -> dict[str, Any]:
    """Replace the store's modernuo rows with a fresh import. Returns counts per table."""
    root = Path(modernuo_dir).expanduser()
    data = root / "Distribution" / "Data"
    if not (data / "regions.json").exists():
        raise FileNotFoundError(f"no ModernUO data at {data} (expected regions.json there)")
    maps = list(maps)
    rows: dict[str, list[dict[str, Any]]] = {t: [] for t in TABLES}

    regions, region_rows = load_regions(data / "regions.json", maps, src(root, data / "regions.json"))
    rows["regions"] += region_rows
    index = RegionIndex(regions)
    places = Places(index)
    for r in regions:
        if r.kind == "town" and r.go:
            places.add("town", r.name, r.map, *r.go, source=src(root, data / "regions.json"))

    for m in maps:
        loc_file = data / "Locations" / f"{m.lower()}.json"
        if loc_file.exists():
            location_places(places, loc_file, m, src(root, loc_file))
    moongates = root / "Projects" / "UOContent" / "Items" / "Misc" / "PublicMoongate.cs"
    if moongates.exists():
        moongate_places(places, moongates.read_text(), maps, src(root, moongates))

    for m in maps:
        for f in spawn_files(root, m):
            rows["spawns"] += spawn_rows(places, json.loads(f.read_text()), f.name, src(root, f))

    tp_file = data / "teleporters.json"
    if tp_file.exists():
        rows["routes"] += teleporter_rows(places, json.loads(tp_file.read_text()), maps, src(root, tp_file))

    mobiles = root / "Projects" / "UOContent" / "Mobiles"
    if mobiles.exists():
        rows["creatures"] += parse_creatures(mobiles, root)

    rows["places"] = places.finish()
    stamp = now()
    with world.db:
        for t in TABLES:
            world.db.execute(f"DELETE FROM {t} WHERE source LIKE 'modernuo:%'")
        for t, batch in rows.items():
            for row in batch:
                world.insert(t, {"last_seen": stamp, **row})
        # A place the agent has seen elsewhere stays retired after a re-import.
        world.db.execute(
            "UPDATE places SET stale = 1 WHERE source LIKE 'modernuo:%' AND EXISTS (SELECT 1 FROM places s "
            "WHERE s.source = 'seen' AND s.stale = 0 AND s.map = places.map AND s.kind = places.kind "
            "AND s.name = places.name COLLATE NOCASE AND MAX(ABS(s.x - places.x), ABS(s.y - places.y)) > 3)")
    world.refresh()
    return {"modernuo_dir": str(root), "maps": maps, **{t: len(v) for t, v in rows.items() if v}}


def src(root: Path, path: Path) -> str:
    try:
        return "modernuo:" + path.relative_to(root).as_posix()
    except ValueError:
        return "modernuo:" + path.name


def spawn_files(root: Path, map_name: str) -> list[Path]:
    """The spawn files the server loads for this map and its expansion."""
    era = "post-uoml"
    cfg = root / "Distribution" / "Configuration" / "expansion.json"
    if cfg.exists():
        try:
            if json.loads(cfg.read_text()).get("Id", SA_EXPANSION_ID) < SA_EXPANSION_ID:
                era = "uoml"
        except ValueError:
            pass
    spawns = root / "Distribution" / "Data" / "Spawns"
    out = []
    for folder in ("shared", era):
        out += sorted((spawns / folder / map_name.lower()).glob("**/*.json"))
    return out


def load_regions(path: Path, maps: list[str], source: str) -> tuple[list[Region], list[dict[str, Any]]]:
    """Named regions. Unnamed ones are rooms inside a town and add nothing a planner needs."""
    wanted = {m.lower() for m in maps}
    regions, rows = [], []
    raw = [r for r in json.loads(path.read_text()) if r.get("Name") and r.get("Map", "").lower() in wanted]
    unguarded = {(r["Map"], r["Name"]) for r in raw if r.get("GuardsDisabled")}
    types = {(r["Map"], r["Name"]): r.get("$type") for r in raw}
    for r in raw:
        kind = REGION_KINDS.get(r.get("$type", ""), "area")
        parent = (r.get("Parent") or {}).get("Name")
        if kind == "town" and types.get((r["Map"], parent)) == "TownRegion":
            kind = "town_area"  # a field or farm inside a town
        guarded = (r.get("$type") in ("TownRegion", "GuardedRegion") and not r.get("GuardsDisabled")
                   and (r["Map"], parent) not in unguarded)
        rects = [(a["x1"], a["y1"], a["x2"], a["y2"]) for a in r.get("Area", []) if "x1" in a]
        if not rects:
            continue
        go = _point(r.get("GoLocation"))
        entrance = _point(r.get("Entrance"))
        regions.append(Region(r["Name"], r["Map"], kind, guarded, rects, go, entrance, parent))
        rows.append({"name": r["Name"], "map": r["Map"], "kind": kind, "guarded": int(guarded),
                     "rects": json.dumps(rects), "parent": parent, "source": source,
                     **_xyz("go", go), **_xyz("entrance", entrance)})
    return regions, rows


def _point(p: dict[str, Any] | None) -> tuple[int, int, int] | None:
    return (p["x"], p["y"], p.get("z", 0)) if p and "x" in p else None


def _xyz(prefix: str, p: tuple[int, int, int] | None) -> dict[str, Any]:
    return {f"{prefix}_x": p[0], f"{prefix}_y": p[1], f"{prefix}_z": p[2]} if p else {}


class Places:
    """Collects places during an import, names areas from them, and gives vendors names
    a player would use ("West Britain bank") once every place is known."""

    def __init__(self, regions: RegionIndex):
        self.regions = regions
        self.rows: list[dict[str, Any]] = []
        self.hints: list[tuple[str, int, int, str]] = []  # names given to spots in catch-all regions

    def add(self, kind: str, name: str, map_name: str, x: int, y: int, z: int | None = 0,
            source: str = "", sells: list[str] | None = None, note: str | None = None,
            town: str | None = None) -> dict[str, Any]:
        r = self.regions.at(map_name, x, y)
        row = {"kind": kind, "name": name, "map": map_name, "x": x, "y": y, "z": z,
               "region": r.name if r else None, "sells": json.dumps(sells) if sells else None,
               "note": note, "source": source, "_town": town}
        self.rows.append(row)
        return row

    def landmarks(self, map_name: str) -> list[dict[str, Any]]:
        keep = {"town", "landmark", "graveyard", "dungeon_entrance", "shrine", "moongate", "docks"}
        return [p for p in self.rows if p["map"] == map_name and p["kind"] in keep]

    def town_of(self, map_name: str, x: int, y: int) -> Region | None:
        """The outermost town around a tile (Britain for a wheatfield inside it; Jhelom,
        not the "Jhelom Islands" area that contains the town)."""
        r, town = self.regions.at(map_name, x, y), None
        while r:
            if r.kind == "town":
                town = r
            r = self.regions.named(map_name, r.parent) if r.parent else None
        return town

    def area(self, map_name: str, x: int, y: int, hint: str | None = None) -> tuple[str, str | None]:
        """Name the spot a spawner (or teleporter) is in: its region, the dungeon level
        it is on, or failing those its bearing from the nearest landmark. `hint` names
        spawns in a catch-all region such as "Misc Dungeons" (from the spawn file)."""
        r = self.regions.at(map_name, x, y)
        if r and r.name.lower().startswith("misc"):
            if hint:
                self.hints.append((map_name, x, y, hint))
                return hint, r.name
            near = [h for h in self.hints if h[0] == map_name and r.contains(h[1], h[2])]
            if near:
                return min(near, key=lambda h: tiles(h[1], h[2], x, y))[3], r.name
        if r and r.kind == "dungeon":
            levels = [p for p in self.rows if p["map"] == map_name and p["region"] == r.name
                      and p["kind"] in ("dungeon_level", "dungeon_spot")]
            best = min(levels, key=lambda p: tiles(p["x"], p["y"], x, y), default=None)
            if best and tiles(best["x"], best["y"], x, y) <= 80:
                return best["name"], r.name
            return r.name, r.name
        if r:
            return r.name, r.name
        marks = self.landmarks(map_name)
        best = min(marks, key=lambda p: tiles(p["x"], p["y"], x, y), default=None)
        if best is None:
            return f"wilderness ({x // 100 * 100}, {y // 100 * 100})", None
        d = tiles(best["x"], best["y"], x, y)
        if d <= 40:
            return f"near {best['name']}", None
        if d <= 250:
            return f"{compass(x - best['x'], y - best['y'])} of {best['name']}", None
        return f"wilderness ({x // 100 * 100}, {y // 100 * 100})", None

    def finish(self) -> list[dict[str, Any]]:
        """Unique names within each map. A town's second bank gets a bearing from the town
        centre ("West Britain bank"), eight-way if four isn't enough, then coordinates."""
        for p in self.rows:
            p["_base"] = p["name"]
        for eight in (False, True):
            for group in self._clashes():
                for p in group:
                    p["name"] = self._bearing(p, eight) or p["name"]
        for group in self._clashes():
            for p in group:
                p["name"] = f"{p['name']} at ({p['x']}, {p['y']})"
        return [{k: v for k, v in p.items() if not k.startswith("_")} for p in self.rows]

    def _clashes(self) -> list[list[dict[str, Any]]]:
        by_name: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for p in self.rows:
            by_name.setdefault((p["map"], p["name"].lower()), []).append(p)
        return [g for g in by_name.values() if len(g) > 1]

    def _bearing(self, p: dict[str, Any], eight: bool) -> str | None:
        town = self.regions.named(p["map"], p["_town"]) if p["_town"] else None
        if not town:
            return None
        cx, cy, _ = town.centre
        return f"{compass(p['x'] - cx, p['y'] - cy, eight).capitalize()} {p['_base']}"


def location_places(places: Places, path: Path, map_name: str, source: str) -> None:
    """Locations/<map>.json: the categories become place kinds (towns' named spots,
    dungeon entrances and levels, shrines). Faction stones and staff areas are skipped."""
    def walk(node: dict[str, Any], trail: list[str]) -> None:
        for c in node.get("categories", []):
            walk(c, trail + [c["name"]])
        for loc in node.get("locations", []):
            x, y, *rest = loc["location"]
            kind, name = location_kind(trail, loc["name"])
            if kind:
                places.add(kind, name, map_name, x, y, rest[0] if rest else 0, source)

    walk(json.loads(path.read_text()), [])


def location_kind(trail: list[str], name: str) -> tuple[str | None, str]:
    if not trail or trail[0] in ("Factions", "Internal"):
        return None, name
    top, rest = trail[0], trail[1:]
    if top == "Shrines":
        return "shrine", f"Shrine of {name}"
    if top == "Dungeons":
        if rest and rest[0] == "Miscellaneous":
            return "landmark", name
        where = " ".join(rest)
        full = f"{where} {name}".strip()
        if re.search(r"\bentrance\b", name, re.I):
            return "dungeon_entrance", full
        if re.match(r"level\b", name, re.I):
            return "dungeon_level", full
        return "dungeon_spot", full
    if top == "Towns" and rest:
        town = rest[0]
        full = name if town.lower() in name.lower() else f"{town} {name}"
        for pattern, kind in LOCATION_KINDS:
            if re.search(pattern, name, re.I):
                return kind, full
        return "landmark", full
    return "landmark", name


def moongate_places(places: Places, cs: str, maps: list[str], source: str) -> None:
    """Public moongates, from the PMList tables in PublicMoongate.cs."""
    for m in maps:
        block = re.search(rf"PMList\s+{m}\s*=(.*?)\]\s*\)\s*;", cs, re.S)
        if not block:
            continue
        for x, y, z, town in re.findall(r"new Point3D\((\d+),\s*(\d+),\s*(-?\d+|[^()]*\([^)]*\))\)\s*,\s*\d+\)\s*,?\s*//\s*([^\n]+)",
                                        block.group(1)):
            town = re.sub(r"\(.*?\)\s*", "", town).strip()
            zi = int(z) if re.fullmatch(r"-?\d+", z.strip()) else 0
            places.add("moongate", f"{town} moongate", m, int(x), int(y), zi, source)


def _seconds(span: str | None) -> int | None:
    """A .NET TimeSpan string ("00:05:00", "1.02:00:00") in seconds."""
    m = re.fullmatch(r"(?:(\d+)\.)?(\d+):(\d+):(\d+)(?:\.\d+)?", (span or "").strip())
    if not m:
        return None
    days, h, mins, secs = (int(g or 0) for g in m.groups())
    return ((days * 24 + h) * 60 + mins) * 60 + secs


def spawn_rows(places: Places, spawners: list[dict[str, Any]], filename: str, source: str) -> list[dict[str, Any]]:
    """One row per creature type per spawner. Vendors become places (with what they
    sell) and reagent spawns become places too; people and crops are skipped."""
    rows = []
    for s in spawners:
        map_name = s.get("map", "Felucca")
        x, y, *rest = s["location"]
        z = rest[0] if rest else 0
        area, region = places.area(map_name, x, y, hint=_words(Path(filename).stem))
        hunting = []
        reagents = []
        for e in s.get("entries", []):
            t = e["name"].lower()
            if t in VENDORS or t.endswith("guildmaster"):
                vendor_place(places, e["name"], map_name, x, y, z, area, source)
            elif t in REAGENTS:
                reagents.append((REAGENTS[t], e.get("maxCount")))
            elif filename.lower() not in NOT_HUNTING:
                hunting.append(e)
        if reagents:
            what = ", ".join(n for n, _ in reagents)
            places.add("reagent_spawn", f"{what} {where(area)}", map_name, x, y, z, source,
                       note=f"Reagents grow here: {what}; respawn {_delay_words(s)}.")
        bounds = s.get("spawnBounds")
        walk = s.get("walkingRange")
        if walk is None or walk < 0:
            walk = s.get("homeRange")
        for e in hunting:
            rows.append({"area": area, "region": region, "map": map_name, "x": x, "y": y, "z": z,
                         "walk_range": walk, "creature": e["name"], "max_count": e.get("maxCount"),
                         "bounds": json.dumps([[bounds["start"]["x"], bounds["start"]["y"]],
                                               [bounds["end"]["x"], bounds["end"]["y"]]]) if bounds else None,
                         "spawner": s.get("guid"), "spawner_count": s.get("count"),
                         "min_delay_s": _seconds(s.get("minDelay")), "max_delay_s": _seconds(s.get("maxDelay")),
                         "note": f"spawns in region {s['region']}" if s.get("region") else None,
                         "source": source})
    return rows


def where(area: str) -> str:
    """ "near Yew moongate" and "north of Britain" read on their own; a region needs "in"."""
    return area if area.startswith(("near ", "wilderness")) or " of " in area else f"in {area}"


def _delay_words(s: dict[str, Any]) -> str:
    lo, hi = _seconds(s.get("minDelay")), _seconds(s.get("maxDelay"))
    if lo is None:
        return "unknown"
    return f"{lo // 60}-{(hi or lo) // 60} min" if lo >= 60 else f"{lo}-{hi or lo} s"


def vendor_place(places: Places, type_name: str, map_name: str, x: int, y: int, z: int, area: str,
                 source: str) -> None:
    t = type_name.lower()
    if t in VENDORS:
        kind, label, sells = VENDORS[t]
    else:
        guild = re.sub(r"guildmaster$", "", type_name, flags=re.I)
        kind, label, sells = "guildmaster", f"{_words(guild).lower()} guildmaster", ["skill training"]
    # A banker and a minter at one spot are one bank.
    for p in places.rows:
        if p["kind"] == kind and p["x"] == x and p["y"] == y and p["map"] == map_name:
            return
    town = places.town_of(map_name, x, y)
    if town:
        name = f"{town.name} {label}"
    elif where(area) == area:
        name = f"{label} {area}"
    else:
        name = f"{area} {label}"
    places.add(kind, name, map_name, x, y, z, source, sells=sells, town=town.name if town else None,
               note="Resurrects ghosts." if kind == "healer" else None)


def _words(camel: str) -> str:
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", camel)


def teleporter_rows(places: Places, entries: list[dict[str, Any]], maps: list[str], source: str) -> list[dict[str, Any]]:
    """Teleporters come as one entry per tile; adjacent tiles that lead to adjacent
    tiles are one teleporter. Each becomes a place and a route (two if it works back)."""
    wanted = {m.lower() for m in maps}
    tps = [e for e in entries if e["src"]["map"].lower() in wanted]
    at = {(e["src"]["map"], e["src"]["loc"][0], e["src"]["loc"][1]): i for i, e in enumerate(tps)}
    parent = list(range(len(tps)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, e in enumerate(tps):
        m, x, y = e["src"]["map"], e["src"]["loc"][0], e["src"]["loc"][1]
        for dx in (-2, -1, 0, 1, 2):
            for dy in (-2, -1, 0, 1, 2):
                j = at.get((m, x + dx, y + dy))
                if j is None or j == i:
                    continue
                o = tps[j]
                if o["dst"]["map"] == e["dst"]["map"] and tiles(*o["dst"]["loc"][:2], *e["dst"]["loc"][:2]) <= 2:
                    parent[find(i)] = find(j)
    clusters: dict[int, list[dict[str, Any]]] = {}
    for i, e in enumerate(tps):
        clusters.setdefault(find(i), []).append(e)

    routes = []
    for group in clusters.values():
        group.sort(key=lambda e: (e["src"]["loc"][0], e["src"]["loc"][1]))
        e = group[len(group) // 2]
        smap, dmap = e["src"]["map"], e["dst"]["map"]
        sx, sy, sz = (list(e["src"]["loc"]) + [0])[:3]
        dx, dy, dz = (list(e["dst"]["loc"]) + [0])[:3]
        from_area, _ = places.area(smap, sx, sy)
        to_area, _ = places.area(dmap, dx, dy) if dmap.lower() in wanted else (dmap, None)
        two_way = any(g.get("back") for g in group)
        name = f"{from_area} teleporter to {to_area}"
        places.add("teleporter", name, smap, sx, sy, sz, source,
                   note=f"Leads to {to_area} at ({dx}, {dy}, {dz}){'' if dmap == smap else ' on ' + dmap}"
                        f"{'; works both ways' if two_way else ''}.")
        routes.append({"from_name": from_area, "to_name": to_area, "map": smap, "kind": "teleporter",
                       "waypoints": json.dumps([[sx, sy, sz], [dx, dy, dz]]), "outcome": "ok", "duration_s": 0,
                       "note": None if dmap == smap else f"arrives on {dmap}", "source": source})
        if two_way and dmap == smap:
            routes.append({"from_name": to_area, "to_name": from_area, "map": smap, "kind": "teleporter",
                           "waypoints": json.dumps([[dx, dy, dz], [sx, sy, sz]]), "outcome": "ok", "duration_s": 0,
                           "source": source})
    return routes


_CLASS = re.compile(r"\bclass\s+(\w+)\s*(?:<[^>{]*>)?\s*:\s*(\w+)")
_RANGE = r"\(\s*(-?\d+)\s*(?:,\s*(-?\d+)\s*)?\)"
_STATS = {
    "hits": re.compile(r"\bSetHits" + _RANGE),
    "str": re.compile(r"\bSetStr" + _RANGE),
    "damage": re.compile(r"\bSetDamage" + _RANGE),
}
_FAME = re.compile(r"\bFame\s*=\s*(-?\d+)\s*;")
_KARMA = re.compile(r"\bKarma\s*=\s*(-?\d+)\s*;")
_BODY = re.compile(r"\bBody\s*=\s*(?:Utility\.RandomList\(\s*)?(0x[0-9A-Fa-f]+|\d+)")
_NAME = re.compile(r"\bDefaultName\s*=>\s*\"([^\"]+)\"|\bName\s*=\s*\"([^\"]+)\"\s*;")


def parse_creatures(mobiles: Path, root: Path) -> list[dict[str, Any]]:
    """Hits, damage, fame, karma and body per creature class, by regex over the C#.
    Best effort: what doesn't parse is left out, and a class without its own numbers
    takes its base class's. With no SetHits, hits are the strength range, which is how
    BaseCreature works it out."""
    found: dict[str, dict[str, Any]] = {}
    for f in sorted(mobiles.rglob("*.cs")):
        text = f.read_text(errors="replace")
        heads = list(_CLASS.finditer(text))
        for i, m in enumerate(heads):
            body = text[m.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
            c: dict[str, Any] = {"type": m.group(1), "base": m.group(2), "source": src(root, f)}
            for key, rx in _STATS.items():
                if hit := rx.search(body):
                    lo = int(hit.group(1))
                    c[key] = (lo, int(hit.group(2)) if hit.group(2) else lo)
            if hit := _FAME.search(body):
                c["fame"] = int(hit.group(1))
            if hit := _KARMA.search(body):
                c["karma"] = int(hit.group(1))
            if hit := _BODY.search(body):
                c["body"] = int(hit.group(1), 0)
            if hit := _NAME.search(body):
                c["name"] = hit.group(1) or hit.group(2)
            found.setdefault(c["type"], c)

    def resolved(t: str, depth: int = 0) -> dict[str, Any]:
        c = found[t]
        base = c.get("base")
        if depth < 8 and base in found and base != t:
            b = resolved(base, depth + 1)
            for k in ("hits", "str", "damage", "fame", "karma", "body", "name"):
                c.setdefault(k, b.get(k))
        return c

    rows = []
    for t in found:
        c = resolved(t)
        hits, note = c.get("hits"), None
        if hits is None and c.get("str"):
            hits, note = c["str"], "hits taken from strength"
        if hits is None or (c.get("damage") is None and c.get("fame") is None):
            continue
        dmg = c.get("damage") or (None, None)
        rows.append({"type": t, "name": c.get("name"), "body": c.get("body"),
                     "hits_min": hits[0], "hits_max": hits[1], "damage_min": dmg[0], "damage_max": dmg[1],
                     "fame": c.get("fame"), "karma": c.get("karma"),
                     "difficulty": grade_creature(hits[0], hits[1], dmg[0], dmg[1], c.get("fame")),
                     "note": note, "source": c["source"]})
    return rows
