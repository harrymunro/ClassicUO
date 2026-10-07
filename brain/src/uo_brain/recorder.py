"""Record the world from what the agent sees, on any shard.

While the agent plays, or while the player drives with the brain connected, this keeps the
shard's world store up to date: the townsfolk who bank, heal and sell (from the title in
their name, "Lucy the healer"), and which creatures turn up where. Travel records routes and
the spots where the character got stuck itself. A public shard's data files aren't available,
so this is how its store fills up: attended sessions double as mapping runs.

Jev keeps the store clean, asked once per batch of new sightings:

- which kind of townsperson a title means (choice), when it isn't a title code knows;
- whether a creature is a regular of the area, worth recording as something that spawns
  there, rather than passing through (noul);
- whether a sighting contradicts the stored fact about that kind of place nearby (noul),
  which marks the old fact stale.

Other players never go in.
"""

import time
from dataclasses import dataclass, field
from typing import Any

from .judge import Judge
from .state import BODY_KINDS
from .world import World, tiles

# A title word -> the place kind it means. Guards and criers keep no shop.
TITLES: dict[str, str | None] = {
    "banker": "bank", "minter": "bank", "healer": "healer", "mage": "mage_shop", "alchemist": "alchemist",
    "herbalist": "herbalist", "provisioner": "provisioner", "weaponsmith": "weapon_vendor",
    "armourer": "armour_vendor", "armorer": "armour_vendor", "blacksmith": "blacksmith", "jeweler": "jeweller",
    "jeweller": "jeweller", "innkeeper": "inn", "barkeeper": "tavern", "tavernkeeper": "tavern", "bowyer": "bowyer",
    "tailor": "tailor", "weaver": "tailor", "scribe": "scribe", "baker": "baker", "butcher": "butcher",
    "tinker": "tinker", "carpenter": "carpenter", "cobbler": "cobbler", "fisherman": "fisherman",
    "stable master": "stable", "animal trainer": "stable", "veterinarian": "stable",
    "guard": None, "town crier": None, "peasant": None, "beggar": None, "noble": None,
}
KINDS = ["bank", "healer", "mage_shop", "reagent_vendor", "provisioner", "weapon_vendor", "armour_vendor",
         "jeweller", "inn", "tavern", "stable", "other"]


@dataclass
class Sighting:
    area: str
    creature: str
    x: int
    y: int
    first: float = field(default_factory=time.monotonic)
    last: float = field(default_factory=time.monotonic)
    times: int = 0
    most_at_once: int = 0
    decided: bool = False


def title_of(label: str) -> str | None:
    """'Lucy the healer guildmistress' -> 'healer guildmistress'."""
    low = label.lower()
    return low.split(" the ", 1)[1].strip() if " the " in low else None


def kind_of(title: str) -> str | None | bool:
    """The place kind a title means; None for townsfolk without a shop; False when unknown."""
    if "guildmaster" in title or "guildmistress" in title:
        return None
    for word, kind in TITLES.items():
        if title == word or title.startswith(word + " ") or title.endswith(" " + word):
            return kind
    return False


class Recorder:
    def __init__(self, world: World, judge: Judge | None = None, flush_every_s: float = 20.0):
        self.world = world
        self.judge = judge
        self.flush_every_s = flush_every_s
        self._next_flush = time.monotonic() + flush_every_s
        self._npcs_done: set[int] = set()
        self._npcs: list[dict[str, Any]] = []   # townsfolk waiting to be written
        self._signs_done: set[tuple[int, int]] = set()
        self._sightings: dict[tuple[str, str], Sighting] = {}
        self.written: list[str] = []            # what went into the store, for logs and tests

    def area(self, x: int, y: int) -> str:
        r = self.world.region_at(x, y)
        return r["name"] if r else f"wilderness near {x // 50 * 50},{y // 50 * 50}"

    def observe(self, snap: dict[str, Any]) -> None:
        if not snap.get("in_game"):
            return
        p = snap["player"]
        counts: dict[tuple[str, str], int] = {}
        for m in snap.get("mobiles", []):
            x, y = p["x"] + m.get("dx", 0), p["y"] + m.get("dy", 0)
            if m.get("monster") and not m.get("dead"):
                creature = BODY_KINDS.get(m.get("body", 0)) or (m.get("name") or "").lower().removeprefix("a ").removeprefix("an ")
                if creature:
                    key = (self.area(x, y), creature)
                    counts[key] = counts.get(key, 0) + 1
                    s = self._sightings.setdefault(key, Sighting(key[0], creature, x, y))
                    s.last = time.monotonic()
            elif m.get("human") and m.get("notoriety") == "invulnerable" and not m.get("threat"):
                label = m.get("label") or ""
                if m["serial"] in self._npcs_done or not title_of(label):
                    continue
                self._npcs_done.add(m["serial"])
                self._npcs.append({"label": label, "title": title_of(label), "x": x, "y": y, "area": self.area(x, y)})
        for sign in snap.get("signs", []):
            spot = (sign["x"], sign["y"])
            if spot not in self._signs_done:
                self._signs_done.add(spot)
                area = self.area(*spot)
                self.world.add_place("shop_sign", sign["text"], sign["x"], sign["y"], region=area,
                                     note=f"a shop sign in {area}", source="seen")
                self.written.append(f"sign: {sign['text']} at {sign['x']},{sign['y']}")
        for key, n in counts.items():
            s = self._sightings[key]
            s.times += 1
            s.most_at_once = max(s.most_at_once, n)

    def due(self) -> bool:
        return time.monotonic() >= self._next_flush and bool(self._npcs or self._ready())

    def _ready(self) -> list[Sighting]:
        # Seen on several looks over at least a minute: then it is worth a judgment.
        return [s for s in self._sightings.values() if not s.decided and s.times >= 6 and s.last - s.first >= 60]

    async def flush(self) -> list[str]:
        """Write what has been seen, with Jev's judgments where code can't tell."""
        self._next_flush = time.monotonic() + self.flush_every_s
        npcs, self._npcs = self._npcs, []
        ready = self._ready()
        questions: dict[str, Any] = {}
        state: dict[str, Any] = {"townsfolk": [], "creatures": [], "stored_places": []}
        unknown: dict[str, dict[str, Any]] = {}
        conflicts: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}

        for i, n in enumerate(npcs):
            kind = kind_of(n["title"])
            if kind is None:
                continue
            if kind is False:
                qid = f"kind_{i}"
                unknown[qid] = n
                state["townsfolk"].append({"id": qid, "title": n["title"], "area": n["area"]})
                questions[qid] = {"type": "choice",
                                  "instructions": f"In Ultima Online, a townsperson titled '{n['title']}' stands in "
                                                  f"{n['area']}. Which kind of place does that make where they stand?",
                                  "criteria": {k: k.replace("_", " ") for k in KINDS}}
                continue
            n["kind"] = kind
            # A stored place of this kind close by but not here: does the sighting contradict it?
            near = [q for q in self.world.find_place(kind, near=[n["x"], n["y"]], limit=3)
                    if q.get("source") != "seen" and q.get("region") == n["area"]]
            if near and 6 < tiles(n["x"], n["y"], near[0]["x"], near[0]["y"]) <= 40:
                qid = f"moved_{i}"
                conflicts[qid] = (n, near[0])
                state["stored_places"].append({"id": qid, "stored": f"{near[0]['name']} at {near[0]['x']},{near[0]['y']}",
                                               "seen": f"a {n['title']} at {n['x']},{n['y']} in {n['area']}"})
                questions[qid] = {"type": "noul",
                                  "instructions": f"Does the sighting with id {qid} in `stored_places` show the stored "
                                                  "place is wrong or has moved?",
                                  "criteria": {"true": "The stored place is wrong or out of date: this is where it is now.",
                                               "false": "Both can be true, for example two shops of the same kind in one town."}}

        for j, s in enumerate(ready):
            qid = f"regular_{j}"
            state["creatures"].append({"id": qid, "creature": s.creature, "area": s.area, "times_seen": s.times,
                                       "most_at_once": s.most_at_once,
                                       "minutes_watched": round((s.last - s.first) / 60, 1)})
            questions[qid] = {"type": "noul",
                              "instructions": f"Is the creature with id {qid} in `creatures` a regular of that area, worth "
                                              "recording as something that spawns there, rather than one passing through?",
                              "criteria": {"true": "Seen there again and again, or several at once: it lives there.",
                                           "false": "One stray seen briefly, or a creature that followed the character there."}}

        answers = None
        if questions and self.judge is not None:
            try:
                answers = await self.judge.ask(state, questions)
            except Exception:  # a failed judgment just leaves these for later
                self._npcs.extend(npcs)
                return []

        out: list[str] = []
        for n in npcs:
            kind = n.get("kind")
            qid = next((q for q, v in unknown.items() if v is n), None)
            if qid:
                c = answers.choices.get(qid) if answers else None
                kind = c.choice if c and c.choice != "other" and c.confidence >= 0.5 else None
            if not kind:
                continue
            name = f"{n['area']} {n['title']}"
            self.world.add_place(kind, name, n["x"], n["y"], region=n["area"], note=f"seen: {n['label']}", source="seen")
            out.append(f"place {kind}: {name} at {n['x']},{n['y']}")
            cid = next((q for q, v in conflicts.items() if v[0] is n), None)
            if cid and answers and answers.nouls.get(cid, 0) >= 0.5:
                old = conflicts[cid][1]
                row = self.world.db.execute("SELECT id FROM places WHERE name = ? AND x = ? AND y = ? AND source = ?",
                                            (old["name"], old["x"], old["y"], old["source"])).fetchone()
                if row:
                    self.world.mark_stale("places", row["id"])
                    out.append(f"stale: {old['name']}")
        for j, s in enumerate(ready):
            keep = answers.nouls.get(f"regular_{j}", 0) if answers else (1.0 if s.most_at_once >= 2 else 0.0)
            s.decided = True
            if keep >= 0.5:
                self.world.add_spawn_seen(s.area, s.creature, s.x, s.y, s.most_at_once)
                out.append(f"spawn: {s.creature} in {s.area}")
        self.written += out
        return out
