"""Places from a shard's map markers: the files players load into the client's world map.

Most shards publish their banks, healers, moongates and dungeons as marker files for the
world map, and for a shard whose server data isn't available they are the cheapest
accurate list of places there is. Two formats are read, the two ClassicUO's world map reads
from Data/Client (WorldMapGump.LoadMarkers):

- UOAM .map files (UO Auto-Map): a first line "3" (the format version), then one marker a
  line, "+bank: 1425 1690 0 The First Bank of Britain". The first character is "+" for a
  place or "-" for a town building, the word before the colon is the marker type (it can
  have spaces: "+Body of Water:"), then x, y, the facet and the name (which can be empty).
- The client's CSV: x,y,facet,name,icon,colour[,zoom]. The icon name plays the type's part.
  ClassicUO keeps the player's own markers in this format too (userMarkers.usr).

The facet number means different things in the two. UOAM's own files count 0 for both
Felucca and Trammel, 1 Felucca, 2 Trammel, 3 Ilshenar and 4 Malas (UOAM's FAQ gives 0-2;
3 and 4 are what UOAM's Common.MAP uses for the Ilshenar shrines and Luna). The client's
CSV counts map indexes: 0 Felucca, 1 Trammel, 2 Ilshenar, 3 Malas, 4 Tokuno, 5 TerMur.
ClassicUO reads both formats with map indexes, so a UOAM file's Felucca markers show on
Trammel there. By default .map files are read the UOAM way and CSV files the client's way;
`facets` overrides it for a .map file a shard wrote with map indexes.

Each file's places are stored with the source "markers:<file name>", so importing the same
file again replaces only its own rows. A marker sits where its icon was drawn, which isn't
always a walkable tile, so markers rank below the server's data and what the agent has seen
(world.SOURCE_TRUST). Marker files are other people's work: they go in the repo only if
their licence allows; otherwise the import reads the player's own copy.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .world import World, tiles

FORMATS = ("uoam", "csv")
NUMBERINGS = ("auto", "uoam", "client")
SUFFIXES = {".map": "uoam", ".csv": "csv", ".usr": "csv"}

UOAM_FACETS: dict[int, tuple[str, ...]] = {0: ("Felucca", "Trammel"), 1: ("Felucca",), 2: ("Trammel",),
                                           3: ("Ilshenar",), 4: ("Malas",)}
CLIENT_FACETS: dict[int, tuple[str, ...]] = {0: ("Felucca",), 1: ("Trammel",), 2: ("Ilshenar",), 3: ("Malas",),
                                             4: ("Tokuno",), 5: ("TerMur",)}
MAP_SIZES = {"Felucca": (7168, 4096), "Trammel": (7168, 4096), "Ilshenar": (2304, 1600), "Malas": (2560, 2048),
             "Tokuno": (1448, 1448), "TerMur": (1280, 4096)}

# Marker type (UOAM category or CSV icon name, lower case) -> place kind. None: not a place
# to go (player houses, boats, treasure pins, rares, credits). A type not listed here is kept
# as a landmark with the type in its note. tests/test_markers.py checks every kind against
# world.PLACE_KINDS.
TYPE_KINDS: dict[str, str | None] = {
    "bank": "bank", "banker": "bank",
    "healer": "healer", "healers guild": "healer", "healersguild": "healer",
    "moongate": "moongate", "moon gate": "moongate",
    "dungeon": "dungeon_entrance", "dungeon entrance": "dungeon_entrance",
    "teleporter": "teleporter", "teleport": "teleporter",
    "stable": "stable", "stables": "stable",
    "inn": "inn", "tavern": "tavern",
    "mage": "mage_shop", "reagents": "mage_shop", "mage shop": "mage_shop",
    "alchemist": "alchemist", "herbalist": "herbalist",
    "provisioner": "provisioner",
    "arms": "arms_shop", "armaments": "arms_shop", "weapons": "weapon_vendor", "weaponsmith": "weapon_vendor",
    "armour": "armour_vendor", "armor": "armour_vendor", "armourer": "armour_vendor", "armorer": "armour_vendor",
    "blacksmith": "blacksmith", "smith": "blacksmith",
    "tailor": "tailor", "weaver": "tailor",
    "bowyer": "bowyer", "fletcher": "bowyer",
    "baker": "baker", "butcher": "butcher", "carpenter": "carpenter", "shipwright": "shipwright",
    "jeweler": "jeweller", "jeweller": "jeweller", "tinker": "tinker", "tanner": "tanner", "scribe": "scribe",
    "cobbler": "cobbler", "mapmaker": "mapmaker", "fisherman": "fisherman", "cook": "cook", "farmer": "farmer",
    "docks": "docks", "dock": "docks",
    "graveyard": "graveyard", "cemetery": "graveyard",
    "shrine": "shrine", "town": "town", "city": "town",
    "guild": "guildmaster",
    # Not places to go.
    "treasure": None, "daily rares": None, "credit": None, "pin": None, "point": None, "loot": None,
    "hand": None, "gem": None, "brass": None, "uoam": None, "uoam-l": None, "uoam-s": None, "tent": None,
    "small house": None, "large house": None, "two story house": None, "log cabin": None, "villa": None,
    "tower": None, "small tower": None, "keep": None, "castle": None, "fortress": None, "marble patio": None,
    "marble shop": None, "patio house": None, "sandstone patio": None, "stone shop": None, "ship": None,
}
# "<anything> guild" is a guild hall; boats come in sizes and headings: "small boat (N)".
_GUILD = re.compile(r"\bguild$")
_BOAT = re.compile(r"\bboat\b")

# Types that say nothing about what is there: their names are read for the few kinds that
# matter most ("+point of interest: 1 2 0 Britain Bank").
GENERIC = {"landmark", "point of interest", "other", "scenic", "point", "poi", "marker", "place"}
NAME_KINDS = [(re.compile(r"\bbank(er)?\b", re.I), "bank"), (re.compile(r"\bheal(er|ers|ing)\b", re.I), "healer"),
              (re.compile(r"\bmoongate\b", re.I), "moongate"), (re.compile(r"\binn\b", re.I), "inn"),
              (re.compile(r"\bstables?\b", re.I), "stable")]

# How close a marker and the store's place of the same kind must be to count as one place.
MATCH_TILES = 10
CHECK_KINDS = ("bank", "healer", "moongate", "dungeon_entrance")


@dataclass
class Marker:
    type: str
    x: int
    y: int
    facet: int
    name: str
    line: int
    town: bool = False  # UOAM's "-": a town building rather than a place out in the land


@dataclass
class Parsed:
    format: str
    markers: list[Marker]
    errors: list[dict[str, Any]]


def _bad(line_no: int, text: str, why: str) -> dict[str, Any]:
    return {"line": line_no, "text": text[:120], "why": why}


_UOAM_LINE = re.compile(r"(-?\d+)\s+(-?\d+)\s+(-?\d+)(?:\s+(.*))?$")


def parse_uoam(text: str) -> Parsed:
    """A UOAM .map file. Lines that don't read are reported, and the rest still import."""
    markers, errors = [], []
    first = True
    for no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if first:
            first = False
            if line.isdigit():
                if line != "3":
                    errors.append(_bad(no, line, f"format version {line}; UOAM's current format is 3"))
                continue
            errors.append(_bad(no, line, "no format version line (\"3\") at the top"))
        if line[0] not in "+-":
            errors.append(_bad(no, line, "a marker line starts with + or -"))
            continue
        kind, colon, rest = line[1:].partition(":")
        if not colon or not kind.strip():
            errors.append(_bad(no, line, "no marker type before a colon"))
            continue
        m = _UOAM_LINE.match(rest.strip())
        if not m:
            errors.append(_bad(no, line, "expected x y facet and a name after the colon"))
            continue
        markers.append(Marker(" ".join(kind.split()), int(m[1]), int(m[2]), int(m[3]), " ".join((m[4] or "").split()),
                              no, town=line[0] == "-"))
    return Parsed("uoam", markers, errors)


def parse_csv(text: str) -> Parsed:
    """The client's CSV markers: x,y,facet,name[,icon[,colour[,zoom]]]. A comma in a name
    can't be told from a separator, so such a line is reported rather than guessed at."""
    markers, errors = [], []
    for no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            errors.append(_bad(no, line, "expected x,y,facet,name,icon,colour[,zoom]"))
            continue
        if len(parts) > 7:
            errors.append(_bad(no, line, f"{len(parts)} fields where 7 at most were expected (a comma in the name?)"))
            continue
        try:
            x, y, facet = int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            errors.append(_bad(no, line, "x, y and facet must be whole numbers"))
            continue
        icon = parts[4] if len(parts) > 4 else ""
        markers.append(Marker(" ".join(icon.split()), x, y, facet, " ".join(parts[3].split()), no))
    return Parsed("csv", markers, errors)


def read_text(path: Path) -> str:
    """Marker files are often saved in Windows' ANSI code page rather than UTF-8."""
    data = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("latin-1")


def format_of(path: Path, text: str) -> str:
    fmt = SUFFIXES.get(path.suffix.lower())
    if fmt:
        return fmt
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return "uoam" if first == "3" or first[:1] in ("+", "-") else "csv"


def parse_file(path: Path) -> Parsed:
    text = read_text(path)
    return parse_uoam(text) if format_of(path, text) == "uoam" else parse_csv(text)


def facet_maps(facet: int, numbering: str) -> tuple[str, ...] | None:
    return (UOAM_FACETS if numbering == "uoam" else CLIENT_FACETS).get(facet)


def place_kind(marker_type: str, name: str) -> tuple[str | None, bool]:
    """(kind, from_name) for a marker. kind None: not a place to go. A generic or unknown
    type is a landmark unless its name says bank, healer, moongate, inn or stable."""
    t = marker_type.strip().lower()
    if t in TYPE_KINDS:
        if TYPE_KINDS[t] is None:
            return None, False
        return TYPE_KINDS[t], False
    if _BOAT.search(t):
        return None, False
    if _GUILD.search(t):
        return "guildmaster", False
    for rx, kind in NAME_KINDS:
        if rx.search(name):
            return kind, True
    return "landmark", False


def find_files(paths: Iterable[Path | str]) -> list[Path]:
    """The marker files named, and those in folders named (.map, .csv and .usr)."""
    out: list[Path] = []
    for p in paths:
        p = Path(p).expanduser()
        if p.is_dir():
            out += sorted(f for f in p.iterdir() if f.is_file() and f.suffix.lower() in SUFFIXES)
        elif p.is_file():
            out.append(p)
        else:
            raise FileNotFoundError(f"no marker file or folder at {p}")
    return out


def place_rows(world: World, parsed: Parsed, source: str, numbering: str,
               maps: set[str] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Place rows for one file's markers, with counts of what was kept, skipped and why."""
    skipped: dict[str, int] = {}
    landmarks: dict[str, int] = {}
    errors = list(parsed.errors)
    rows: list[dict[str, Any]] = []
    regions = world.regions()
    for m in parsed.markers:
        kind, from_name = place_kind(m.type, m.name)
        label = m.type.lower() or "no type"
        if kind is None:
            skipped[label] = skipped.get(label, 0) + 1
            continue
        on = facet_maps(m.facet, numbering)
        if on is None:
            errors.append(_bad(m.line, f"{m.type}: {m.x} {m.y} {m.facet} {m.name}",
                               f"facet {m.facet} means no map in {numbering} numbering"))
            continue
        for map_name in on:
            if maps is not None and map_name.lower() not in maps:
                continue
            w, h = MAP_SIZES[map_name]
            if not (0 < m.x < w and 0 < m.y < h):
                errors.append(_bad(m.line, f"{m.type}: {m.x} {m.y} {m.facet} {m.name}",
                                   f"({m.x}, {m.y}) is off {map_name} ({w}x{h})"))
                continue
            if kind == "landmark" and label != "landmark":
                landmarks[label] = landmarks.get(label, 0) + 1
            r = regions.at(map_name, m.x, m.y)
            note = f"map marker ({m.type or 'no type'})" + ("; kind from its name" if from_name else "")
            rows.append({"kind": kind, "name": m.name or f"{label} at ({m.x}, {m.y})", "map": map_name,
                         "x": m.x, "y": m.y, "z": None, "region": r.name if r else None, "note": note,
                         "source": source, "_type": label})
    _unique_names(rows)
    for r in rows:
        del r["_type"]
    return rows, {"skipped": dict(sorted(skipped.items(), key=lambda kv: -kv[1])),
                  "landmarks_by_type": dict(sorted(landmarks.items(), key=lambda kv: -kv[1])), "errors": errors}


def _unique_names(rows: list[dict[str, Any]]) -> None:
    """Names unique within each map, as the store's own imports keep them: a clash between
    types gets the type ("Covetous (dungeon)", "Covetous (exit)"), one within a type its
    region ("Counselor's Guild (Britain)"), and what still clashes its coordinates."""
    def clashes() -> list[list[dict[str, Any]]]:
        by: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for r in rows:
            by.setdefault((r["map"], r["name"].lower()), []).append(r)
        return [g for g in by.values() if len(g) > 1]

    for group in clashes():
        if len({r["_type"] for r in group}) > 1:
            for r in group:
                if r["_type"] not in r["name"].lower():
                    r["name"] = f"{r['name']} ({r['_type']})"
    for group in clashes():
        for r in group:
            if r["region"] and r["region"].lower() not in r["name"].lower():
                r["name"] = f"{r['name']} ({r['region']})"
    for group in clashes():
        for r in group:
            r["name"] = f"{r['name']} at ({r['x']}, {r['y']})"


def import_markers(world: World, paths: Iterable[Path | str], numbering: str = "auto",
                   maps: Iterable[str] | None = None, check_within: int = MATCH_TILES) -> dict[str, Any]:
    """Replace each marker file's rows in the store with a fresh import. Returns per file
    what went in, what was skipped, the lines that didn't read, and how the banks, healers,
    moongates and dungeon entrances compare with places the store already had."""
    if numbering not in NUMBERINGS:
        raise ValueError(f"facet numbering is one of {', '.join(NUMBERINGS)}, not {numbering!r}")
    wanted = {m.strip().lower() for m in maps} if maps else None
    files = find_files(paths)
    if not files:
        raise FileNotFoundError("no marker files (.map, .csv or .usr) there")
    out: list[dict[str, Any]] = []
    sources: list[str] = []
    for f in files:
        parsed = parse_file(f)
        num = numbering if numbering != "auto" else ("uoam" if parsed.format == "uoam" else "client")
        source = f"markers:{f.name}"
        rows, info = place_rows(world, parsed, source, num, wanted)
        n = world.replace_source("places", source, rows)
        sources.append(source)
        by_kind: dict[str, int] = {}
        by_map: dict[str, int] = {}
        for r in rows:
            by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
            by_map[r["map"]] = by_map.get(r["map"], 0) + 1
        out.append({"file": str(f), "source": source, "format": parsed.format, "facets": num,
                    "markers": len(parsed.markers), "places": n, "by_map": by_map,
                    "by_kind": dict(sorted(by_kind.items(), key=lambda kv: -kv[1])),
                    "skipped": info["skipped"], "landmarks_by_type": info["landmarks_by_type"],
                    "error_count": len(info["errors"]), "errors": info["errors"][:20]})
    return {"files": out, "agreement": agreement(world, sources, within=check_within)}


def agreement(world: World, sources: list[str], kinds: Iterable[str] = CHECK_KINDS,
              within: int = MATCH_TILES) -> dict[str, Any]:
    """How the imported markers line up with places of the same kind the store had from
    other sources, per map: how many match within `within` tiles, and the ones that don't."""
    if not sources:
        return {}
    marks = ", ".join("?" for _ in sources)
    out: dict[str, Any] = {}
    for kind in kinds:
        mine = world.db.execute(f"SELECT * FROM places WHERE kind = ? AND stale = 0 AND source IN ({marks})",
                                [kind, *sources]).fetchall()
        for map_name in sorted({r["map"] for r in mine}):
            here = [r for r in mine if r["map"] == map_name]
            theirs = world.db.execute("SELECT * FROM places WHERE kind = ? AND map = ? AND stale = 0 "
                                      "AND source NOT LIKE 'markers:%'", (kind, map_name)).fetchall()
            if not theirs:
                continue

            def nearest(r, pool):
                best = min(pool, key=lambda o: tiles(r["x"], r["y"], o["x"], o["y"]))
                return best, tiles(r["x"], r["y"], best["x"], best["y"])

            matched, dists, unmatched = 0, [], []
            for r in here:
                o, d = nearest(r, theirs)
                if d <= within:
                    matched += 1
                    dists.append(d)
                else:
                    unmatched.append({"marker": r["name"], "at": [r["x"], r["y"]], "nearest": o["name"],
                                      "nearest_at": [o["x"], o["y"]], "tiles": d, "source": o["source"]})
            missing = []
            for o in theirs:
                r, d = nearest(o, here)
                if d > within:
                    missing.append({"place": o["name"], "at": [o["x"], o["y"]], "nearest_marker": r["name"],
                                    "tiles": d, "source": o["source"]})
            dists.sort()
            out.setdefault(kind, {})[map_name] = {
                "markers": len(here), "matched": matched, "store_places": len(theirs),
                "store_places_with_a_marker": len(theirs) - len(missing),
                "median_tiles_when_matched": dists[len(dists) // 2] if dists else None,
                "max_tiles_when_matched": dists[-1] if dists else None,
                "markers_without_a_match": sorted(unmatched, key=lambda u: u["tiles"]),
                "store_places_without_a_marker": sorted(missing, key=lambda u: u["tiles"])}
    return out
