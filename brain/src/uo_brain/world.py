"""World knowledge for one shard, in a SQLite file the planner queries through tools.

The planner asks where things are: the nearest bank, what spawns in the Britain
graveyard, a hunting spot for a new mage. Those are questions about map distance and
small joins, so the store is plain tables rather than a vector database, and free-text
notes are searched by keyword. There is one file per shard,
brain/worlds/<shard>/world.sqlite, because a fact about the local ModernUO server says
nothing about UO Renaissance.

Every row says where it came from (`source`) and when it was last confirmed (`last_seen`):

    modernuo:<file>    the server's own data files (world_import.py)
    seen               observed in game by the agent
    note               typed in by the player (uo-brain world note)
    guide:<url>        summarised from a guide or wiki page (guides.py)
    model:unverified   the planner model's own knowledge, until the game confirms it
    outcomes           a note summing up the recorded outcomes of an area (outcomes.py)

A fact that a newer observation contradicts is marked stale instead of being deleted:
queries skip it, but the history stays. Other players' names and speech never go in.
A PK sighting is stored as "a red player was seen here", not who it was.

Distances are in tiles, counted the way a character walks: max(|dx|, |dy|).
"""

import difflib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

WORLDS_DIR = Path(__file__).resolve().parents[2] / "worlds"
DEFAULT_MAP = "Felucca"
TABLES = ("places", "regions", "spawns", "creatures", "routes", "outcomes", "notes")

_COMMON = "note TEXT, source TEXT NOT NULL, last_seen TEXT NOT NULL, stale INTEGER NOT NULL DEFAULT 0"

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS places (
    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, map TEXT NOT NULL,
    x INTEGER NOT NULL, y INTEGER NOT NULL, z INTEGER, region TEXT, sells TEXT, {_COMMON});
CREATE INDEX IF NOT EXISTS places_kind ON places(map, kind);
CREATE INDEX IF NOT EXISTS places_source ON places(source);

CREATE TABLE IF NOT EXISTS regions (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, map TEXT NOT NULL, kind TEXT NOT NULL,
    guarded INTEGER NOT NULL DEFAULT 0, rects TEXT NOT NULL, go_x INTEGER, go_y INTEGER, go_z INTEGER,
    entrance_x INTEGER, entrance_y INTEGER, entrance_z INTEGER, parent TEXT, {_COMMON});
CREATE INDEX IF NOT EXISTS regions_map ON regions(map);
CREATE INDEX IF NOT EXISTS regions_source ON regions(source);

CREATE TABLE IF NOT EXISTS spawns (
    id INTEGER PRIMARY KEY, area TEXT NOT NULL, region TEXT, map TEXT NOT NULL,
    x INTEGER NOT NULL, y INTEGER NOT NULL, z INTEGER, walk_range INTEGER, bounds TEXT,
    creature TEXT NOT NULL, max_count INTEGER, spawner TEXT, spawner_count INTEGER,
    min_delay_s INTEGER, max_delay_s INTEGER, {_COMMON});
CREATE INDEX IF NOT EXISTS spawns_area ON spawns(map, area);
CREATE INDEX IF NOT EXISTS spawns_creature ON spawns(creature);
CREATE INDEX IF NOT EXISTS spawns_source ON spawns(source);

CREATE TABLE IF NOT EXISTS creatures (
    id INTEGER PRIMARY KEY, type TEXT NOT NULL, name TEXT, body INTEGER,
    hits_min INTEGER, hits_max INTEGER, damage_min INTEGER, damage_max INTEGER,
    fame INTEGER, karma INTEGER, difficulty TEXT, {_COMMON});
CREATE INDEX IF NOT EXISTS creatures_type ON creatures(type COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS creatures_source ON creatures(source);

CREATE TABLE IF NOT EXISTS routes (
    id INTEGER PRIMARY KEY, from_name TEXT NOT NULL, to_name TEXT NOT NULL, map TEXT NOT NULL,
    waypoints TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'walk', outcome TEXT NOT NULL DEFAULT 'ok',
    stuck_at TEXT, duration_s REAL, {_COMMON});
CREATE INDEX IF NOT EXISTS routes_source ON routes(source);

CREATE TABLE IF NOT EXISTS outcomes (
    id INTEGER PRIMARY KEY, area TEXT NOT NULL, kit TEXT, metric TEXT NOT NULL, value REAL,
    session TEXT, {_COMMON});

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY, area TEXT, text TEXT NOT NULL, tags TEXT, source TEXT NOT NULL,
    last_seen TEXT NOT NULL, stale INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS notes_source ON notes(source);
"""

# Keyword search over notes, stemmed so "lich" finds "liches". External-content FTS5 kept
# in step by triggers; if this SQLite has no FTS5, notes() falls back to LIKE.
FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(text, tags, area, content='notes', content_rowid='id',
    tokenize='porter unicode61');
CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
  INSERT INTO notes_fts(rowid, text, tags, area) VALUES (new.id, new.text, new.tags, new.area);
END;
CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
  INSERT INTO notes_fts(notes_fts, rowid, text, tags, area) VALUES ('delete', old.id, old.text, old.tags, old.area);
END;
CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE ON notes BEGIN
  INSERT INTO notes_fts(notes_fts, rowid, text, tags, area) VALUES ('delete', old.id, old.text, old.tags, old.area);
  INSERT INTO notes_fts(rowid, text, tags, area) VALUES (new.id, new.text, new.tags, new.area);
END;
"""

# Creature difficulty, weakest first. A new character (skills around 50) handles weak
# creatures; "deadly" is dragons, daemons and balrons.
GRADES = ("trivial", "weak", "moderate", "strong", "deadly")

# Hunting level -> the strongest grade that suits it.
LEVELS = {"new": 1, "moderate": 2, "strong": 3, "expert": 4}
LEVEL_WORDS = {"beginner": "new", "novice": "new", "newbie": "new", "low": "new",
               "medium": "moderate", "intermediate": "moderate", "mid": "moderate",
               "veteran": "strong", "high": "strong", "advanced": "strong",
               "elite": "expert", "gm": "expert", "grandmaster": "expert"}

# Town-like region kinds: no hunting there, and guards.
TOWN_KINDS = ("town", "town_area", "guarded", "jail", "staff")

# Kinds a planner may ask for that cover several stored kinds.
KIND_ALIASES = {
    "reagent_vendor": ("mage_shop", "alchemist", "herbalist"),
    "reagents": ("mage_shop", "alchemist", "herbalist"),
    "banker": ("bank",),
    "dungeon": ("dungeon_entrance",),
    "armor_vendor": ("armour_vendor",),
    "weapon_shop": ("weapon_vendor",),
    "armour_shop": ("armour_vendor",),
    "armor_shop": ("armour_vendor",),
    "weaponsmith": ("weapon_vendor", "blacksmith"),
    "armourer": ("armour_vendor", "blacksmith"),
    "armorer": ("armour_vendor", "blacksmith"),
    "jeweler": ("jeweller",),
    "cemetery": ("graveyard",),
    "gate": ("moongate",),
    "resurrection": ("healer",),
}

# Words that mean the same thing in place names.
SYNONYMS = {"cemetery": "graveyard", "banker": "bank", "armor": "armour", "armorer": "armourer",
            "brit": "britain", "gy": "graveyard", "moon": "moongate", "tele": "teleporter",
            "lvl": "level"}


class WorldError(ValueError):
    """A query the store can't answer as asked (unknown place, bad coordinates)."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def tiles(ax: int, ay: int, bx: int, by: int) -> int:
    return max(abs(ax - bx), abs(ay - by))


def grade_creature(hits_min: int | None, hits_max: int | None, damage_min: int | None,
                   damage_max: int | None, fame: int | None) -> str | None:
    """A difficulty word from hits x damage, raised by fame for casters whose spells
    the melee numbers don't show (a wraith has skeleton-like hits but casts)."""
    if hits_min is None and fame is None:
        return None
    hits = ((hits_min or 0) + (hits_max or hits_min or 0)) / 2
    damage = ((damage_min or 1) + (damage_max or damage_min or 1)) / 2
    threat = hits * damage
    by_threat = 0 if threat < 60 else 1 if threat < 600 else 2 if threat < 2000 else 3 if threat < 6000 else 4
    f = fame or 0
    by_fame = 0 if f < 300 else 1 if f < 2000 else 2 if f < 6000 else 3 if f < 14000 else 4
    return GRADES[max(by_threat, by_fame)]


def compass(dx: int, dy: int, eight: bool = True) -> str:
    """Direction of an offset in UO map space, where y grows southward."""
    if dx == 0 and dy == 0:
        return "centre"
    ns = "north" if dy < 0 else "south"
    ew = "west" if dx < 0 else "east"
    if not eight:
        return ew if abs(dx) >= abs(dy) else ns
    if abs(dx) > 2 * abs(dy):
        return ew
    if abs(dy) > 2 * abs(dx):
        return ns
    return f"{ns}-{ew}"


@dataclass
class Region:
    name: str
    map: str
    kind: str
    guarded: bool
    rects: list[tuple[int, int, int, int]]
    go: tuple[int, int, int] | None = None
    entrance: tuple[int, int, int] | None = None
    parent: str | None = None

    def contains(self, x: int, y: int) -> bool:
        return any(x1 <= x < x2 and y1 <= y < y2 for x1, y1, x2, y2 in self.rects)

    @property
    def size(self) -> int:
        return sum((x2 - x1) * (y2 - y1) for x1, y1, x2, y2 in self.rects)

    @property
    def centre(self) -> tuple[int, int, int]:
        if self.go:
            return self.go
        x1, y1, x2, y2 = self.rects[0]
        return ((x1 + x2) // 2, (y1 + y2) // 2, 0)


class RegionIndex:
    """Which named region a tile is in: the smallest one containing it, which is how
    child regions (a graveyard next to a town, a farm inside one) win over their parent."""

    def __init__(self, regions: Iterable[Region]):
        self.by_map: dict[str, list[Region]] = {}
        for r in sorted(regions, key=lambda r: r.size):
            self.by_map.setdefault(r.map.lower(), []).append(r)

    def at(self, map_name: str, x: int, y: int) -> Region | None:
        for r in self.by_map.get(map_name.lower(), ()):
            if r.contains(x, y):
                return r
        return None

    def named(self, map_name: str, name: str) -> Region | None:
        for r in self.by_map.get(map_name.lower(), ()):
            if r.name.lower() == name.lower():
                return r
        return None


def tokens(text: str) -> list[str]:
    """Lower-case words with plurals, possessives and synonyms folded, for fuzzy names."""
    out = []
    for w in re.findall(r"[a-z0-9]+", text.lower().replace("'s", "").replace("_", " ")):
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        if w not in SYNONYMS and len(w) >= 5:  # "cemetary" still means graveyard
            w = next(iter(difflib.get_close_matches(w, SYNONYMS, n=1, cutoff=0.85)), w)
        out.append(SYNONYMS.get(w, w))
    return out


def _word_match(q: str, words: list[str]) -> float:
    if q in words:
        return 1.0
    best = max((difflib.SequenceMatcher(None, q, w).ratio() for w in words), default=0.0)
    return best if best >= 0.8 else 0.0


def name_score(query: str, name: str, extra: str = "") -> float:
    """How well a query names a thing: 2 for the exact name, otherwise the share of query
    words found (typos allowed) less a little for each unasked-for word in the name."""
    if query.strip().lower() == name.strip().lower():
        return 2.0
    q = tokens(query)
    if not q:
        return 0.0
    name_words = tokens(name)
    words = name_words + tokens(extra)
    matched = [_word_match(w, words) for w in q]
    if min(matched) == 0.0:
        return 0.0
    unasked = sum(1 for w in name_words if _word_match(w, q) == 0.0)
    return sum(matched) / len(q) - 0.05 * unasked


def _load_json(text: str | None, default: Any = None) -> Any:
    if text is None or text == "":
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


def _compact(d: dict[str, Any]) -> dict[str, Any]:
    """Drop empty fields so tool results stay short for the model."""
    return {k: v for k, v in d.items() if v is not None and v != [] and v != ""}


class World:
    """One shard's store. Query methods return plain JSON-able dicts, best match first."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), timeout=15.0)
        self.db.row_factory = sqlite3.Row
        if str(path) != ":memory:":
            # Several brains on one machine share a shard's store (a fleet, cuo-m70.4): readers don't
            # wait for a writer.
            self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        try:
            self.db.executescript(FTS_SCHEMA)
            self.fts = True
        except sqlite3.OperationalError:
            self.fts = False
        self.db.commit()
        self._regions: RegionIndex | None = None

    @classmethod
    def open(cls, shard: str = "local", root: Path | str | None = None) -> "World":
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", shard):
            raise WorldError(f"shard names are short lower-case slugs such as 'local' or 'uor', not {shard!r}")
        return cls(Path(root or WORLDS_DIR) / shard / "world.sqlite")

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "World":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- writing ---------------------------------------------------------------------

    def refresh(self) -> None:
        """Forget cached regions after rows were written directly (an import)."""
        self._regions = None

    def insert(self, table: str, row: dict[str, Any]) -> int:
        """One raw row, for importers. The caller commits."""
        row = {"last_seen": now(), **row}
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        cur = self.db.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(row.values()))
        return cur.lastrowid

    def replace_source(self, table: str, source: str, rows: Iterable[dict[str, Any]]) -> int:
        """Swap every row from one source for a fresh set: how re-imports stay idempotent."""
        if table not in TABLES:
            raise WorldError(f"no table {table!r}")
        self.db.execute(f"DELETE FROM {table} WHERE source = ?", (source,))
        n = 0
        stamp = now()
        for row in rows:
            self.insert(table, {"last_seen": stamp, **row, "source": source})
            n += 1
        self.db.commit()
        self._regions = None
        return n

    def delete_source(self, table: str, source: str, **where: Any) -> int:
        if table not in TABLES:
            raise WorldError(f"no table {table!r}")
        sql = f"DELETE FROM {table} WHERE source = ?"
        args: list[Any] = [source]
        for k, v in where.items():
            if not k.isidentifier():
                raise WorldError(f"bad column {k!r}")
            sql += f" AND {k} = ?"
            args.append(v)
        n = self.db.execute(sql, args).rowcount
        self.db.commit()
        return n

    def add_note(self, text: str, area: str | None = None, tags: Iterable[str] = (), source: str = "note") -> int:
        """One fact in plain words. Never put another player's name or speech here."""
        text = " ".join(text.split())
        if not text:
            raise WorldError("a note needs some text")
        tag_list = sorted({t.strip().lower() for t in tags if t and t.strip()})
        rid = self.insert("notes", {"area": area or None, "text": text,
                                     "tags": json.dumps(tag_list) if tag_list else None, "source": source})
        self.db.commit()
        return rid

    def add_place(self, kind: str, name: str, x: int, y: int, map: str = DEFAULT_MAP, z: int | None = None,
                  region: str | None = None, sells: Iterable[str] | None = None, note: str | None = None,
                  source: str = "seen") -> int:
        """Record a place. The same (map, kind, name) from the same source is updated in
        place. A game observation (source "seen") marks other sources' copies of the place
        stale when they put it more than 3 tiles away."""
        kind = kind.strip().lower().replace(" ", "_")
        if region is None:
            hit = self.region_at(x, y, map)
            region = hit["name"] if hit else None
        fields = {"x": x, "y": y, "z": z, "region": region, "note": note,
                  "sells": json.dumps(list(sells)) if sells else None, "last_seen": now(), "stale": 0}
        same = self.db.execute("SELECT id FROM places WHERE map = ? AND kind = ? AND name = ? COLLATE NOCASE "
                               "AND source = ?", (map, kind, name, source)).fetchone()
        if same:
            sets = ", ".join(f"{k} = ?" for k in fields)
            self.db.execute(f"UPDATE places SET {sets} WHERE id = ?", [*fields.values(), same["id"]])
            rid = same["id"]
        else:
            rid = self.insert("places", {"kind": kind, "name": name, "map": map, "source": source, **fields})
        if source == "seen":
            self.db.execute("UPDATE places SET stale = 1 WHERE map = ? AND kind = ? AND name = ? COLLATE NOCASE "
                            "AND source != 'seen' AND MAX(ABS(x - ?), ABS(y - ?)) > 3", (map, kind, name, x, y))
        self.db.commit()
        return rid

    def bestiary(self) -> dict[int, dict[str, Any]]:
        """What each body graphic is, for describing creatures in fights: body -> {name, hits,
        damage, difficulty, caster}. Where several creature types share a body, the strongest."""
        if getattr(self, "_bestiary", None) is not None:
            return self._bestiary
        out: dict[int, dict[str, Any]] = {}
        self._bestiary = out
        for r in self.db.execute("SELECT * FROM creatures WHERE body IS NOT NULL AND stale = 0"):
            hits = r["hits_max"] or r["hits_min"]
            if not hits:
                continue
            entry = {"type": r["type"], "name": r["name"], "hits": hits,
                     "damage": f"{r['damage_min']}-{r['damage_max']}" if r["damage_min"] else None,
                     "difficulty": r["difficulty"], "caster": "/Magic/" in (r["source"] or "")}
            kinds = (out[r["body"]]["kinds"] if r["body"] in out else []) + [entry]
            if r["body"] not in out or hits > out[r["body"]]["hits"]:
                out[r["body"]] = dict(entry)
            out[r["body"]]["kinds"] = kinds  # every kind with this body, to tell them apart by name
        return out

    def add_spawn_seen(self, area: str, creature: str, x: int, y: int, count: int, map: str = DEFAULT_MAP) -> int:
        """A creature seen living somewhere (recorder.py): one row per area and creature,
        keeping the most seen at once."""
        same = self.db.execute("SELECT id, max_count FROM spawns WHERE map = ? AND area = ? AND creature = ? "
                               "COLLATE NOCASE AND source = 'seen'", (map, area, creature)).fetchone()
        if same:
            self.db.execute("UPDATE spawns SET max_count = ?, last_seen = ?, stale = 0 WHERE id = ?",
                            (max(same["max_count"] or 0, count), now(), same["id"]))
            rid = same["id"]
        else:
            hit = self.region_at(x, y, map)
            rid = self.insert("spawns", {"area": area, "region": hit["name"] if hit else None, "map": map, "x": x, "y": y,
                                          "creature": creature, "max_count": count, "source": "seen"})
        self.db.commit()
        return rid

    def add_route(self, from_name: str, to_name: str, waypoints: list[list[int]], map: str = DEFAULT_MAP,
                  kind: str = "walk", outcome: str = "ok", stuck_at: list[int] | None = None,
                  duration_s: float | None = None, note: str | None = None, source: str = "seen") -> int:
        """A path that was tried: waypoints [[x, y, z], ...]; outcome "ok" or "stuck"."""
        rid = self.insert("routes", {"from_name": from_name, "to_name": to_name, "map": map,
                                      "waypoints": json.dumps(waypoints), "kind": kind, "outcome": outcome,
                                      "stuck_at": json.dumps(stuck_at) if stuck_at else None,
                                      "duration_s": duration_s, "note": note, "source": source})
        self.db.commit()
        return rid

    def add_outcome(self, area: str, kit: str | None, metric: str, value: float, session: str | None = None,
                    note: str | None = None, source: str = "seen") -> int:
        """A measured result of playing somewhere, e.g. kills_per_hour for a warrior."""
        rid = self.insert("outcomes", {"area": area, "kit": kit, "metric": metric, "value": value,
                                        "session": session, "note": note, "source": source})
        self.db.commit()
        return rid

    def mark_stale(self, table: str, row_id: int) -> bool:
        """Retire a fact a newer observation contradicts. It stays for the record."""
        if table not in TABLES:
            raise WorldError(f"no table {table!r}")
        n = self.db.execute(f"UPDATE {table} SET stale = 1 WHERE id = ?", (row_id,)).rowcount
        self.db.commit()
        self._regions = None
        return n > 0

    # ---- lookups shared by the queries ------------------------------------------------

    def regions(self) -> RegionIndex:
        if self._regions is None:
            rows = self.db.execute("SELECT * FROM regions WHERE stale = 0").fetchall()
            self._regions = RegionIndex(region_from_row(r) for r in rows)
        return self._regions

    def region_at(self, x: int, y: int, map: str = DEFAULT_MAP) -> dict[str, Any] | None:
        """The named region a tile is in, with whether guards protect it."""
        r = self.regions().at(map, x, y)
        if not r:
            return None
        top = r
        while top.parent and (p := self.regions().named(map, top.parent)):
            top = p
        return _compact({"name": r.name, "kind": r.kind, "guarded": r.guarded, "map": r.map,
                         "part_of": top.name if top is not r else None,
                         "entrance": list(r.entrance) if r.entrance else None})

    def _creatures(self) -> dict[str, sqlite3.Row]:
        rows = self.db.execute("SELECT * FROM creatures WHERE stale = 0 ORDER BY last_seen").fetchall()
        return {r["type"].lower(): r for r in rows}

    def resolve(self, near: Any, map: str = DEFAULT_MAP) -> tuple[int, int, str]:
        """Coordinates for a place name, "x,y", [x, y] or {"x":, "y":}, with a label."""
        if isinstance(near, dict) and "x" in near:
            return int(near["x"]), int(near["y"]), f"({near['x']}, {near['y']})"
        if isinstance(near, (list, tuple)) and len(near) >= 2:
            return int(near[0]), int(near[1]), f"({near[0]}, {near[1]})"
        if isinstance(near, str):
            m = re.fullmatch(r"\s*\(?\s*(\d+)\s*[, ]\s*(\d+)(?:\s*[, ]\s*-?\d+)?\s*\)?\s*", near)
            if m:
                return int(m[1]), int(m[2]), f"({m[1]}, {m[2]})"
            hits = self.place(near, map=map, limit=1)
            if hits:
                return hits[0]["x"], hits[0]["y"], hits[0]["name"]
            raise WorldError(f"no place called {near!r} is known on {map}")
        raise WorldError(f"can't read a location from {near!r}")

    # ---- the planner's queries --------------------------------------------------------

    def place(self, name: str, map: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
        """Named places and regions matching a loose name ("britain bank", "Britain
        graveyard"), best first. Ties go to the one nearest its town's centre."""
        cands: list[tuple[float, int, dict[str, Any]]] = []
        args: list[Any] = []
        where = "stale = 0"
        if map:
            where += " AND map = ? COLLATE NOCASE"
            args.append(map)
        for r in self.db.execute(f"SELECT * FROM places WHERE {where}", args):
            s = name_score(name, r["name"], f"{r['kind']} {r['region'] or ''}")
            if s > 0.5:
                cands.append((s, self._from_centre(r), self._place_dict(r)))
        for r in self.db.execute(f"SELECT * FROM regions WHERE {where}", args):
            s = name_score(name, r["name"], r["kind"])
            if s > 0.5:
                reg = region_from_row(r)
                x, y, z = reg.centre
                d = _compact({"type": "region", "kind": r["kind"], "name": r["name"], "map": r["map"],
                              "x": x, "y": y, "z": z, "guarded": bool(r["guarded"]),
                              "entrance": list(reg.entrance) if reg.entrance else None,
                              "source": r["source"], "last_seen": r["last_seen"]})
                cands.append((s + 0.01, 0, d))  # a region is the more general answer to a tie
        cands.sort(key=lambda c: (-c[0], c[1], len(c[2]["name"])))
        return [{**d, "match": 1.0 if s >= 2 else min(round(s, 2), 0.99)} for s, _, d in cands[:limit]]

    def _from_centre(self, r: sqlite3.Row) -> int:
        if not r["region"]:
            return 10_000
        reg = self.regions().named(r["map"], r["region"])
        if not reg:
            return 10_000
        cx, cy, _ = reg.centre
        return tiles(r["x"], r["y"], cx, cy)

    def _place_dict(self, r: sqlite3.Row, distance: int | None = None) -> dict[str, Any]:
        return _compact({"type": "place", "kind": r["kind"], "name": r["name"], "map": r["map"],
                         "x": r["x"], "y": r["y"], "z": r["z"], "region": r["region"],
                         "sells": _load_json(r["sells"], []), "note": r["note"], "distance": distance,
                         "source": r["source"], "last_seen": r["last_seen"]})

    def find_place(self, kind: str, near: Any = None, map: str = DEFAULT_MAP, limit: int = 5) -> list[dict[str, Any]]:
        """Places of a kind (bank, healer, moongate, ...) or vendors selling an item
        ("bandages", "reagents"), nearest to `near` first."""
        k = kind.strip().lower().replace(" ", "_")
        kinds = {k, *KIND_ALIASES.get(k, ())}
        rows = self.db.execute(
            f"SELECT * FROM places WHERE stale = 0 AND map = ? COLLATE NOCASE "
            f"AND kind IN ({', '.join('?' for _ in kinds)})", [map, *kinds]).fetchall()
        if not rows:
            item = tokens(kind)
            rows = [r for r in self.db.execute("SELECT * FROM places WHERE stale = 0 AND map = ? COLLATE NOCASE "
                                               "AND sells IS NOT NULL", (map,))
                    if any(all(w in tokens(s) for w in item) for s in _load_json(r["sells"], []))]
        if near is None:
            rows = sorted(rows, key=lambda r: r["name"])
            return [self._place_dict(r) for r in rows[:limit]]
        nx, ny, _ = self.resolve(near, map)
        ranked = sorted(rows, key=lambda r: tiles(r["x"], r["y"], nx, ny))
        out: list[dict[str, Any]] = []
        for r in ranked:
            # The same place from two sources (imported, then seen) is listed once.
            if any(o["kind"] == r["kind"] and o["name"].lower() == r["name"].lower()
                   and tiles(o["x"], o["y"], r["x"], r["y"]) <= 3 for o in out):
                continue
            out.append(self._place_dict(r, tiles(r["x"], r["y"], nx, ny)))
            if len(out) >= limit:
                break
        return out

    def _spawners(self, rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
        """Group spawn rows (one per creature type) back into spawners."""
        creatures = self._creatures()
        by_key: dict[str, dict[str, Any]] = {}
        for r in rows:
            key = r["spawner"] or f"{r['map']}:{r['x']}:{r['y']}:{r['source']}"
            s = by_key.setdefault(key, {
                "area": r["area"], "region": r["region"], "map": r["map"], "x": r["x"], "y": r["y"], "z": r["z"],
                "range": r["walk_range"], "count": r["spawner_count"], "creatures": [],
                "respawn": _respawn(r["min_delay_s"], r["max_delay_s"]), "source": r["source"],
                "last_seen": r["last_seen"]})
            c = creatures.get(r["creature"].lower())
            s["creatures"].append(_compact({
                "type": r["creature"], "name": c["name"] if c else None, "max_count": r["max_count"],
                "difficulty": (c["difficulty"] if c else None) or "unknown",
                "hits": _span(c["hits_min"], c["hits_max"]) if c else None,
                "damage": _span(c["damage_min"], c["damage_max"]) if c else None,
                "evil": (c["karma"] < 0) if c and c["karma"] is not None else None}))
        return list(by_key.values())

    def what_spawns(self, area: str | None = None, near: Any = None, map: str = DEFAULT_MAP,
                    radius: int = 60, limit: int = 10) -> list[dict[str, Any]]:
        """Spawners in an area (by name) or within `radius` tiles of `near`, nearest first,
        each with its creatures, their difficulty and the respawn time."""
        if area is None and near is None:
            raise WorldError("what_spawns needs an area or a place to look near")
        rows: list[sqlite3.Row] = []
        if area is not None:
            rows = self.db.execute("SELECT * FROM spawns WHERE stale = 0 AND map = ? COLLATE NOCASE "
                                   "AND (area = ? COLLATE NOCASE OR region = ? COLLATE NOCASE)",
                                   (map, area, area)).fetchall()
            if not rows:
                rows = self.db.execute("SELECT * FROM spawns WHERE stale = 0 AND map = ? COLLATE NOCASE "
                                       "AND (area LIKE ? OR region LIKE ?)",
                                       (map, f"%{area}%", f"%{area}%")).fetchall()
            if not rows and near is None:
                near = area  # perhaps a place name: look around it
        nx = ny = None
        if near is not None:
            nx, ny, _ = self.resolve(near, map)
            if not rows:
                rows = self.db.execute("SELECT * FROM spawns WHERE stale = 0 AND map = ? COLLATE NOCASE "
                                       "AND MAX(ABS(x - ?), ABS(y - ?)) <= ?", (map, nx, ny, radius)).fetchall()
        spawners = self._spawners(rows)
        if nx is not None:
            for s in spawners:
                s["distance"] = tiles(s["x"], s["y"], nx, ny)
            spawners.sort(key=lambda s: s["distance"])
        else:
            spawners.sort(key=lambda s: -sum(c.get("max_count") or 0 for c in s["creatures"]))
        return [_compact(s) for s in spawners[:limit]]

    def hunting_spots(self, archetype: str = "warrior", level: str = "new", near: Any = None,
                      map: str = DEFAULT_MAP, limit: int = 5) -> list[dict[str, Any]]:
        """Areas whose spawns suit a character, best first. Towns are left out. Creatures
        one grade above the level make a spot "risky"; two grades above rule it out."""
        lvl = LEVEL_WORDS.get(level.strip().lower(), level.strip().lower())
        if lvl not in LEVELS:
            raise WorldError(f"level should be one of {', '.join(LEVELS)}, not {level!r}")
        top = LEVELS[lvl]
        mage = archetype.strip().lower() in ("mage", "caster", "magery")
        rows = self.db.execute("SELECT * FROM spawns WHERE stale = 0 AND map = ? COLLATE NOCASE", (map,)).fetchall()
        areas: dict[str, dict[str, Any]] = {}
        for s in self._spawners(rows):
            reg = self.regions().named(map, s["region"]) if s["region"] else None
            if reg and (reg.kind in TOWN_KINDS or reg.guarded):
                continue
            rated = spawner_score(s, top, mage)
            if rated is None:
                continue
            score, risky = rated
            a = areas.setdefault(s["area"], {"area": s["area"], "map": s["map"], "score": 0.0, "spawners": 0,
                                             "creatures": {}, "risky": set(), "region_kind": reg.kind if reg else None,
                                             "entrance": list(reg.entrance) if reg and reg.entrance else None})
            a["spawners"] += 1
            a["risky"].update(risky)
            if score > a["score"]:
                a.update(score=score, x=s["x"], y=s["y"], z=s["z"], respawn=s["respawn"])
            for c in s["creatures"]:
                e = a["creatures"].setdefault(c["type"], {"type": c["type"], "name": c.get("name"),
                                                          "difficulty": c["difficulty"], "at_once": 0.0})
                e["at_once"] += c["at_once"]
        nx = ny = None
        if near is not None:
            nx, ny, _ = self.resolve(near, map)
        out = []
        for a in areas.values():
            ranked = sorted(a["creatures"].values(), key=lambda c: -c["at_once"])[:8]
            a["creatures"] = [_compact({**c, "at_once": max(1, round(c["at_once"]))}) for c in ranked]
            a["risky"] = sorted(a["risky"])
            a["fit"] = "risky" if a["risky"] else "good"
            rank = a["score"]
            if nx is not None:
                gx, gy = (a["entrance"][0], a["entrance"][1]) if a["entrance"] else (a["x"], a["y"])
                a["distance"] = tiles(gx, gy, nx, ny)
                rank = a["score"] / (1 + a["distance"] / 400)  # a spot across the map is worth less
            a["score"] = round(a["score"], 1)
            a["outcomes"] = self._outcomes(a["area"], archetype)
            a["past_results"] = self._outcome_note(a["area"])
            # Stronger creatures seen there in play (Session.note_dangers): not in the spawn data.
            a["dangers_seen"] = [n["text"] for n in self.notes(area=a["area"], limit=10)
                                 if "danger" in (n.get("tags") or [])][:3]
            # Seen within the hour: likely still there, so the spot goes to the back (cuo-d28.9).
            if recent := self.recent_danger(a["area"]):
                a["recent_danger"] = recent
                rank *= 0.25
            out.append((rank, a))
        out.sort(key=lambda t: -t[0])
        return [_compact(a) for _, a in out[:limit]]

    def recent_danger(self, area: str, minutes: float = 60) -> str | None:
        """The latest danger seen in an area within the last `minutes`, with how long ago."""
        since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds")
        r = self.db.execute("SELECT text, last_seen FROM notes WHERE stale = 0 AND source = 'seen' AND tags LIKE "
                            "'%\"danger\"%' AND area = ? COLLATE NOCASE AND last_seen >= ? ORDER BY last_seen DESC "
                            "LIMIT 1", (area, since)).fetchone()
        if not r:
            return None
        ago = (datetime.now(timezone.utc) - datetime.fromisoformat(r["last_seen"])).total_seconds() / 60
        return f"{round(ago)} min ago: {r['text']}"

    def _outcomes(self, area: str, kit: str) -> list[dict[str, Any]]:
        """Averages per loop, without the per-creature totals (those are in outcomes())."""
        rows = self.db.execute("SELECT metric, AVG(value) AS avg, COUNT(*) AS n FROM outcomes WHERE stale = 0 "
                               "AND area = ? COLLATE NOCASE AND (kit IS NULL OR kit = ? COLLATE NOCASE) "
                               "AND metric NOT LIKE '%\\_vs\\_%' ESCAPE '\\' GROUP BY metric", (area, kit)).fetchall()
        return [{"metric": r["metric"], "average": round(r["avg"], 2), "runs": r["n"]} for r in rows]

    def _outcome_note(self, area: str) -> str | None:
        r = self.db.execute("SELECT text FROM notes WHERE source = 'outcomes' AND stale = 0 AND area = ? COLLATE NOCASE "
                            "ORDER BY id DESC LIMIT 1", (area,)).fetchone()
        return r["text"] if r else None

    def outcomes(self, area: str | None = None, kit: str | None = None, exact: bool = False,
                 limit: int = 10) -> list[dict[str, Any]]:
        """What playing in an area gave, per area and kit: how many loops (hunts) were
        recorded, the average per loop of each measure, the deaths in all, and what fights
        with each creature kind cost (summed over the loops, then per fight). Most loops first."""
        where, args = ["stale = 0"], []
        if area:
            where.append("area = ? COLLATE NOCASE")
            args.append(area)
        if kit:
            where.append("(kit IS NULL OR kit = ? COLLATE NOCASE)")
            args.append(kit)
        sql = "SELECT * FROM outcomes WHERE {} ORDER BY id"
        rows = self.db.execute(sql.format(" AND ".join(where)), args).fetchall()
        if not rows and area and not exact:
            where[1] = "area LIKE ?"
            args[0] = f"%{area}%"
            rows = self.db.execute(sql.format(" AND ".join(where)), args).fetchall()
        groups: dict[tuple[str, str | None], list[sqlite3.Row]] = {}
        for r in rows:
            groups.setdefault((r["area"], r["kit"]), []).append(r)
        out = []
        for (a, k), rs in groups.items():
            per: dict[str, list[float]] = {}
            creatures: dict[str, dict[str, float]] = {}
            for r in rs:
                measure, vs, who = r["metric"].partition("_vs_")
                if vs:
                    c = creatures.setdefault(who, {"fights": 0})
                    c[measure] = c.get(measure, 0) + (r["value"] or 0)
                else:
                    per.setdefault(measure, []).append(r["value"] or 0)
            loops = max((len(per.get(m, [])) for m in ("kills_per_hour", "kills_per_loop", "deaths")), default=0)
            costs = []
            for who, c in creatures.items():
                entry: dict[str, Any] = {"creature": who.replace("_", " "), "fights": int(c.pop("fights"))}
                for measure, total in sorted(c.items()):
                    entry[measure] = round(total, 1)
                    if entry["fights"]:
                        entry[f"{measure}_per_fight"] = round(total / entry["fights"], 2)
                costs.append(entry)
            costs.sort(key=lambda c: (-c.get("bandages_per_fight", 0), -c.get("health_pct_lost_per_fight", 0)))
            s = {"area": a, "kit": k, "loops": loops, "sessions": len({r["session"] for r in rs if r["session"]}),
                 "averages": {m: round(sum(v) / len(v), 2) for m, v in sorted(per.items())},
                 "deaths": int(sum(per.get("deaths", []))), "creatures": costs[:8],
                 "last_seen": max(r["last_seen"] for r in rs)}
            if note := self._outcome_note(a):
                s["note"] = note
            out.append(s)
        out.sort(key=lambda s: -s["loops"])
        return out[:limit]

    def route(self, start: Any, end: Any, map: str = DEFAULT_MAP, limit: int = 5) -> dict[str, Any]:
        """Stored routes between two places, teleporters near either end, and the
        straight-line distance. Routes that got stuck are listed with where."""
        sx, sy, slabel = self.resolve(start, map)
        ex, ey, elabel = self.resolve(end, map)
        out: dict[str, Any] = {"from": {"name": slabel, "x": sx, "y": sy}, "to": {"name": elabel, "x": ex, "y": ey},
                               "straight_line_tiles": tiles(sx, sy, ex, ey)}
        end_region = self.regions().at(map, ex, ey)
        start_region = self.regions().at(map, sx, sy)
        if end_region and end_region.entrance and (not start_region or start_region.name != end_region.name):
            ent = end_region.entrance
            out["to"]["inside"] = end_region.name
            out["to"]["entrance"] = list(ent)
            out["straight_line_tiles_to_entrance"] = tiles(sx, sy, ent[0], ent[1])
        stored = []
        for r in self.db.execute("SELECT * FROM routes WHERE stale = 0 AND kind != 'teleporter' "
                                 "AND map = ? COLLATE NOCASE ORDER BY last_seen DESC", (map,)):
            wp = _load_json(r["waypoints"], [])
            by_name = (name_score(slabel, r["from_name"]) >= 0.9 and name_score(elabel, r["to_name"]) >= 0.9)
            by_ends = bool(wp) and tiles(wp[0][0], wp[0][1], sx, sy) <= 10 and tiles(wp[-1][0], wp[-1][1], ex, ey) <= 10
            if by_name or by_ends:
                stored.append(_compact({"from": r["from_name"], "to": r["to_name"], "kind": r["kind"],
                                        "outcome": r["outcome"], "stuck_at": _load_json(r["stuck_at"]),
                                        "waypoints": wp, "duration_s": r["duration_s"], "note": r["note"],
                                        "source": r["source"], "last_seen": r["last_seen"]}))
        stored.sort(key=lambda r: r["outcome"] != "ok")
        out["stored_routes"] = stored[:limit]
        teleporters = []
        target = (out["to"].get("entrance") or [ex, ey])[:2]
        for r in self.db.execute("SELECT * FROM routes WHERE stale = 0 AND kind = 'teleporter' "
                                 "AND map = ? COLLATE NOCASE", (map,)):
            wp = _load_json(r["waypoints"], [])
            if len(wp) < 2:
                continue
            (ax, ay), (bx, by) = wp[0][:2], wp[-1][:2]
            d_in = tiles(ax, ay, sx, sy)
            lands_in = end_region is not None and end_region.contains(bx, by)
            if d_in <= 40 or tiles(bx, by, ex, ey) <= 40 or (lands_in and not end_region.contains(ax, ay)):
                teleporters.append((min(d_in, tiles(ax, ay, *target)), _compact({
                    "from": r["from_name"], "to": r["to_name"], "enter_at": wp[0], "arrive_at": wp[-1],
                    "note": r["note"], "source": r["source"]})))
        teleporters.sort(key=lambda t: t[0])
        out["teleporters"] = [t for _, t in teleporters[:limit]]
        return _compact(out)

    def notes(self, area: str | None = None, keywords: str | Iterable[str] | None = None,
              include_stale: bool = False, limit: int = 10) -> list[dict[str, Any]]:
        """Free-text notes, filtered by area and/or keywords, best keyword match first and
        otherwise most recent first."""
        words = keywords.split() if isinstance(keywords, str) else list(keywords or [])
        words = [w for w in (re.sub(r"[^\w'-]", "", w) for w in words) if w]
        where, args = [], []
        if not include_stale:
            where.append("n.stale = 0")
        if area:
            where.append("n.area LIKE ?")
            args.append(f"%{area}%")
        if words and self.fts:
            match = " OR ".join(f'"{w}"' for w in words)
            sql = (f"SELECT n.* FROM notes_fts JOIN notes n ON n.id = notes_fts.rowid WHERE notes_fts MATCH ? "
                   f"{''.join(' AND ' + w for w in where)} ORDER BY bm25(notes_fts), n.last_seen DESC LIMIT ?")
            rows = self.db.execute(sql, [match, *args, limit]).fetchall()
        else:
            if words:
                where.append("(" + " OR ".join("(n.text LIKE ? OR n.tags LIKE ?)" for _ in words) + ")")
                for w in words:
                    args += [f"%{w}%", f"%{w}%"]
            sql = (f"SELECT n.* FROM notes n {'WHERE ' + ' AND '.join(where) if where else ''} "
                   f"ORDER BY n.last_seen DESC, n.id DESC LIMIT ?")
            rows = self.db.execute(sql, [*args, limit]).fetchall()
        return [_compact({"id": r["id"], "area": r["area"], "text": r["text"], "tags": _load_json(r["tags"], []),
                          "source": r["source"], "last_seen": r["last_seen"], "stale": bool(r["stale"]) or None})
                for r in rows]

    def stats(self) -> dict[str, Any]:
        out: dict[str, Any] = {"path": str(self.path), "keyword_search": "fts5" if self.fts else "like"}
        for t in TABLES:
            total = self.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            stale = self.db.execute(f"SELECT COUNT(*) FROM {t} WHERE stale = 1").fetchone()[0]
            by = self.db.execute(
                f"SELECT CASE WHEN instr(source, ':') > 0 AND source NOT LIKE 'model:%' "
                f"THEN substr(source, 1, instr(source, ':') - 1) ELSE source END AS s, COUNT(*) "
                f"FROM {t} GROUP BY s ORDER BY 2 DESC").fetchall()
            out[t] = {"rows": total, "stale": stale, "by_source": {r[0]: r[1] for r in by}}
        out["place_kinds"] = {r[0]: r[1] for r in self.db.execute(
            "SELECT kind, COUNT(*) FROM places WHERE stale = 0 GROUP BY kind ORDER BY 2 DESC")}
        out["creature_difficulty"] = {r[0]: r[1] for r in self.db.execute(
            "SELECT difficulty, COUNT(*) FROM creatures WHERE stale = 0 GROUP BY difficulty ORDER BY 2 DESC")}
        return out

    # ---- tool calling -----------------------------------------------------------------

    def call_tool(self, name: str, args: dict[str, Any] | str | None) -> Any:
        """Run one of tool_schemas()'s tools for the planner. Errors come back as
        {"error": ...} so the model can correct itself instead of the loop failing."""
        if isinstance(args, str):
            args = _load_json(args, {}) if args.strip() else {}
        # `question` is for re-ranking the results (facts.Ranker), not for the query itself.
        args = {k: v for k, v in (args or {}).items() if v is not None and k != "question"}
        fn = {"find_place": self.find_place, "hunting_spots": self.hunting_spots, "what_spawns": self.what_spawns,
              "route": lambda **a: self.route(a.pop("from"), a.pop("to"), **a), "notes": self.notes,
              "place": self.place, "region_at": lambda **a: self.region_at(**a) or {"region": None},
              "outcomes": lambda **a: self.outcomes(**a) or {"outcomes": [], "note": "nothing recorded yet"}}.get(name)
        if fn is None:
            return {"error": f"no tool called {name!r}"}
        try:
            return fn(**args)
        except (WorldError, TypeError, KeyError, ValueError) as e:
            return {"error": str(e)}


def spawner_score(s: dict[str, Any], top: int, mage: bool) -> tuple[float, list[str]] | None:
    """How good one spawner is for a character whose level suits grade `top`, from 0 to 10.

    Mostly the share of what is alive there that suits the character: creatures at the
    level count fully, weaker ones less, animals less again, and creatures one grade too
    strong count against it. More targets help up to about ten. Anything two grades too
    strong rules the spawner out. Sets each creature's "at_once": a spawner keeps at most
    `count` creatures alive, shared among its entries."""
    listed = sum(c.get("max_count") or 1 for c in s["creatures"])
    share = min(1.0, (s.get("count") or listed) / listed)
    good = bad = alive = 0.0
    risky = []
    for c in s["creatures"]:
        n = c["at_once"] = (c.get("max_count") or 1) * share
        if c["difficulty"] == "unknown":
            continue
        gap = GRADES.index(c["difficulty"]) - top
        if gap >= 2:
            return None
        alive += n
        if gap == 1:
            risky.append(c["type"])
            bad += n
        else:
            good += n * (1.0 if gap == 0 else 0.6 if gap == -1 else 0.2) * (1.0 if c.get("evil", True) else 0.3)
    if not alive:
        return None
    quality = (good - (1.5 if mage else 1.0) * bad) / alive
    if quality <= 0.05:
        return None
    score = 10 * quality * (0.5 + 0.5 * min(alive - bad, 10) / 10)
    if mage and alive > 8:
        score *= 0.8  # a mage stands and casts, and gets swarmed
    return score, risky


def region_from_row(r: sqlite3.Row) -> Region:
    def point(prefix: str) -> tuple[int, int, int] | None:
        if r[f"{prefix}_x"] is None:
            return None
        return (r[f"{prefix}_x"], r[f"{prefix}_y"], r[f"{prefix}_z"] or 0)

    rects = [tuple(rc) for rc in _load_json(r["rects"], [])]
    return Region(r["name"], r["map"], r["kind"], bool(r["guarded"]), rects, point("go"), point("entrance"), r["parent"])


def _span(a: int | None, b: int | None) -> str | None:
    if a is None:
        return None
    return str(a) if b is None or a == b else f"{a}-{b}"


def _respawn(lo: int | None, hi: int | None) -> str | None:
    if lo is None:
        return None
    def words(s: int) -> str:
        return f"{s // 60} min" if s >= 60 and s % 60 == 0 else f"{s} s"
    return words(lo) if hi in (None, lo) else f"{words(lo)} to {words(hi)}"


_NEAR = {"type": "string", "description": "A place name (\"Britain graveyard\", \"West Britain bank\") "
                                          "or coordinates \"x,y\"."}
_MAP = {"type": "string", "description": "Facet. Default Felucca.", "default": DEFAULT_MAP}
_LIMIT = {"type": "integer", "description": "Most results to return.", "minimum": 1, "maximum": 25}
_QUESTION = {"type": "string", "description": "Optional: what you want to find out, in one sentence. The results "
                                              "then come back best answer first, each with its relevance (0-1)."}


def tool_schemas() -> list[dict[str, Any]]:
    """OpenAI-style function definitions for the store's queries, written for the model."""
    def fn(name: str, description: str, props: dict[str, Any], required: list[str]) -> dict[str, Any]:
        return {"type": "function", "function": {"name": name, "description": description, "parameters": {
            "type": "object", "properties": props, "required": required, "additionalProperties": False}}}

    return [
        fn("place",
           "Look up a named place or region and get its coordinates. Accepts loose names and typos: "
           "\"britain bank\", \"Britain graveyard\", \"covetous entrance\", \"Yew moongate\". Returns matches best "
           "first, each with kind, map, x, y, z, its region, and a match score from 0 to 1. Use it before "
           "travelling somewhere you only know by name.",
           {"name": {"type": "string", "description": "The place's name, as a player would say it."},
            "map": _MAP, "limit": _LIMIT}, ["name"]),
        fn("find_place",
           "Find places of one kind nearest to a point: bank, healer (resurrects and sells bandages), "
           "mage_shop, alchemist, herbalist, reagent_vendor (any of those three), provisioner, weapon_vendor, "
           "armour_vendor, blacksmith, tailor, inn, tavern, stable, moongate, teleporter, dungeon_entrance, "
           "town, shrine, graveyard, docks, guildmaster, landmark. Or pass an item word (\"bandages\", "
           "\"heal potion\", \"black pearl\") to find vendors that sell it. Returns places nearest first with "
           "distance in tiles and, for vendors, what they sell.",
           {"kind": {"type": "string", "description": "A place kind from the list, or an item to buy."},
            "near": _NEAR, "map": _MAP, "limit": _LIMIT}, ["kind"]),
        fn("hunting_spots",
           "Suggest areas to hunt for a character, best first. Each spot has the area name, coordinates of "
           "its best spawner (and the dungeon entrance if it is inside one), the creatures with how many "
           "spawn and how hard they are (trivial, weak, moderate, strong, deadly), respawn time, fit "
           "(\"good\", or \"risky\" when some creatures are a grade above the level), distance from `near`, "
           "and past results there if any. Towns are excluded. Use it when choosing where to fight.",
           {"archetype": {"type": "string", "description": "warrior or mage."},
            "level": {"type": "string", "enum": list(LEVELS),
                      "description": "new (skills around 30-50), moderate (60-80), strong (90+), expert (GM, "
                                     "well equipped)."},
            "near": {**_NEAR, "description": "Optional: rank closer spots higher. " + _NEAR["description"]},
            "question": _QUESTION, "map": _MAP, "limit": _LIMIT}, ["archetype", "level"]),
        fn("what_spawns",
           "List the spawners in an area, or within a radius of a point: for each, where it is, the "
           "creatures it spawns (type, in-game name, max count, difficulty, hits, damage), its walking "
           "range and respawn time. Nearest first when `near` is given. Use it to check what to expect "
           "before going somewhere.",
           {"area": {"type": "string", "description": "Area or region name, e.g. \"Britain Graveyard\", "
                                                     "\"Covetous\", \"Shame\"."},
            "near": _NEAR, "radius": {"type": "integer", "description": "Tiles around `near`. Default 60."},
            "question": _QUESTION, "map": _MAP, "limit": _LIMIT}, []),
        fn("route",
           "How to get from one place to another: the straight-line distance in tiles, routes the agent "
           "has walked before (with \"stuck\" ones and where they got stuck), the dungeon entrance when "
           "the destination is inside a dungeon, and teleporters near either end.",
           {"from": _NEAR, "to": _NEAR, "map": _MAP, "limit": _LIMIT}, ["from", "to"]),
        fn("notes",
           "Search free-text notes about the shard: tips from guides and wikis, the player's own notes, "
           "dangers seen in game. Filter by area and/or keywords; best matches first. Notes from the "
           "source \"model:unverified\" are unconfirmed guesses.",
           {"area": {"type": "string", "description": "Area name to filter by, e.g. \"Britain\"."},
            "keywords": {"type": "string", "description": "Words to search for, e.g. \"lich reagents\"."},
            "question": _QUESTION, "limit": _LIMIT}, []),
        fn("outcomes",
           "What hunting in an area gave this character before, from its session logs: per area and kit, how "
           "many loops (one hunt each) were recorded, the average per loop of kills, deaths, minutes, gold, and "
           "bandages and heal potions per kill, which creatures cost the most (bandages, heal potions and "
           "health lost per fight, health in percent of the character's maximum), and a note in words. Use it "
           "to choose between areas and to plan supplies.",
           {"area": {"type": "string", "description": "Area name, e.g. \"Britain Graveyard\". Leave out for all."},
            "kit": {"type": "string", "description": "warrior or mage. Leave out for both."},
            "limit": _LIMIT}, []),
        fn("region_at",
           "Which named region a tile is in (town, dungeon, graveyard, cave...), whether guards protect it, "
           "and the town or dungeon it is part of. Use it to tell whether a spot is safe from other players' "
           "attacks under guard rules.",
           {"x": {"type": "integer"}, "y": {"type": "integer"}, "map": _MAP}, ["x", "y"]),
    ]
