"""Turn a raw client snapshot into the semantic state Jev reads, plus candidate lists.

Jev reads words better than numbers (it does not do arithmetic or compare
distances reliably), so code does the maths and describes the result:
"badly wounded", "adjacent", "to the north". Candidates get short ids
(t1, c1, i1) that map back to serials, so the model only ever picks from
options code has already checked.
"""

from dataclasses import dataclass, field
from typing import Any


# Body graphic -> what the creature is. Monsters often carry personal names
# ("Vorgak"), which tell the model nothing about the threat.
BODY_KINDS = {
    1: "ogre", 2: "ettin", 3: "zombie", 4: "gargoyle", 7: "orc captain", 8: "corpser", 9: "daemon",
    12: "dragon", 13: "air elemental", 14: "earth elemental", 15: "fire elemental", 16: "water elemental",
    17: "orc", 18: "ettin", 21: "giant serpent", 22: "gazer", 23: "wolf", 24: "lich", 26: "ghoul",
    28: "giant spider", 29: "gorilla", 30: "harpy", 31: "headless one", 33: "lizardman", 35: "lizardman",
    36: "lizardman", 39: "mongbat", 40: "balron", 42: "ratman", 44: "ratman", 45: "ratman", 47: "reaper",
    48: "scorpion", 50: "skeleton", 51: "slime", 52: "snake", 53: "troll", 54: "troll", 55: "troll",
    56: "skeleton", 57: "skeleton", 58: "wisp", 74: "imp", 98: "hell hound", 138: "orcish lord",
    140: "orcish mage", 181: "orc scout", 182: "orc bomber", 189: "orc brute", 211: "black bear",
    212: "grizzly bear", 213: "polar bear", 215: "giant rat", 225: "timber wolf", 238: "sewer rat",
}


def health_words(pct: int | None) -> str:
    if pct is None:
        return "unknown health"
    if pct >= 95:
        return "unhurt"
    if pct >= 75:
        return "lightly wounded"
    if pct >= 50:
        return "wounded"
    if pct >= 25:
        return "badly wounded"
    return "near death"


def distance_words(d: int) -> str:
    if d <= 1:
        return "adjacent, within weapon reach"
    if d <= 3:
        return "close, a few steps away"
    if d <= 7:
        return "nearby"
    return "far away"


def potion_words(count: int, ready_ms: int) -> str:
    if count == 0:
        return "none left"
    return "ready now" if ready_ms <= 0 else f"not for another {round(ready_ms / 1000)} seconds"


@dataclass
class Candidate:
    id: str
    serial: int
    name: str
    distance: int
    info: dict[str, Any]
    hits_pct: int | None = None


@dataclass
class Situation:
    raw: dict[str, Any]
    state: dict[str, Any]
    hostiles: list[Candidate] = field(default_factory=list)
    corpses: list[Candidate] = field(default_factory=list)
    items: list[Candidate] = field(default_factory=list)
    hp_pct: int = 100
    strategy: str = ""

    @property
    def player(self) -> dict[str, Any]:
        return self.raw["player"]

    @property
    def agent(self) -> dict[str, Any]:
        return self.raw["agent"]

    def hostile(self, cid: str) -> Candidate | None:
        return next((c for c in self.hostiles if c.id == cid), None)

    def corpse(self, cid: str) -> Candidate | None:
        return next((c for c in self.corpses if c.id == cid), None)

    def signature(self) -> tuple:
        """Changes when something worth re-deciding about happens."""
        return (
            tuple(sorted(c.serial for c in self.hostiles)),
            health_words(self.hp_pct),
            self.player.get("poisoned"),
            tuple(sorted(c.serial for c in self.corpses)),
            tuple(sorted(c.serial for c in self.items)),
            self.agent.get("engaged"),
        )


def build(snapshot: dict[str, Any], looted: set[int], events: list[str], skip_items: set[int] = frozenset(),
          max_hostiles: int = 6) -> Situation:
    p = snapshot["player"]
    hp_pct = round(100 * p["hits"] / p["hits_max"]) if p.get("hits_max") else 100
    stam_pct = round(100 * p["stam"] / p["stam_max"]) if p.get("stam_max") else 100
    engaged = snapshot["agent"].get("engaged", 0)
    supplies = p.get("supplies", {})

    hostiles: list[Candidate] = []
    others: list[dict[str, Any]] = []
    for m in snapshot.get("mobiles", []):
        if m.get("dead"):
            continue
        if m.get("monster"):
            if len(hostiles) >= max_hostiles:
                continue
            cid = f"t{len(hostiles) + 1}"
            name = m.get("name") or "an unknown creature"
            kind = BODY_KINDS.get(m.get("body", 0))
            if kind and kind not in name.lower():
                name = f"{name} (a {kind})" if not kind[0] in "aeiou" else f"{name} (an {kind})"
            info = {
                "id": cid,
                "name": name,
                "health": health_words(m.get("hits_pct")),
                "distance": distance_words(m["distance"]),
                "direction": m.get("dir", ""),
                "your_current_target": bool(m.get("my_target")) or m["serial"] == engaged,
                "aggressive": bool(m.get("war_mode")),
            }
            hostiles.append(Candidate(cid, m["serial"], info["name"], m["distance"], info, m.get("hits_pct")))
        elif len(others) < 5:
            kind = "your pet" if m.get("pet") else "person" if m.get("human") else "creature"
            others.append({"name": m.get("name") or "someone", "kind": kind, "distance": distance_words(m["distance"])})

    corpses: list[Candidate] = []
    items: list[Candidate] = []
    for c in snapshot.get("corpses", []):
        if c["serial"] not in looted and c["distance"] <= 10:
            cid = f"c{len(corpses) + 1}"
            info = {"id": cid, "name": c.get("name") or "a corpse", "distance": distance_words(c["distance"]),
                    "direction": c.get("dir", ""), "opened": c.get("opened", False)}
            corpses.append(Candidate(cid, c["serial"], info["name"], c["distance"], info))
        # Items in any open corpse within reach, looted or not: the client takes gold and
        # supplies itself, and the rest needs a judgment.
        if c["distance"] <= 2:
            for it in c.get("items", []):
                if it.get("auto_loot") or it["serial"] in skip_items or len(items) >= 6:
                    continue
                iid = f"i{len(items) + 1}"
                iinfo = {"id": iid, "name": it.get("name") or "an item", "amount": it.get("amount", 1),
                         "properties": it.get("props", "")}
                items.append(Candidate(iid, it["serial"], iinfo["name"], c["distance"], iinfo))

    you = {
        "health": f"{health_words(hp_pct)} ({hp_pct}%)",
        "stamina": "tired" if stam_pct < 30 else "fine",
        "poisoned": bool(p.get("poisoned")),
        "bandaging_now": bool(snapshot["agent"].get("bandaging")),
        "bandages_left": supplies.get("bandages", 0),
        "heal_potions_left": supplies.get("heal_potions", 0),
        "heal_potion_ready": potion_words(supplies.get("heal_potions", 0), snapshot["agent"].get("heal_potion_ready_ms", 0)),
        "cure_potions_left": supplies.get("cure_potions", 0),
        "weapon": p.get("weapon") or "bare hands",
        "fighting": next((h.name for h in hostiles if h.info["your_current_target"]), "nobody"),
        "carrying": "nearly overloaded" if p.get("weight_max") and p["weight"] > 0.9 * p["weight_max"] else "light load",
    }

    state = {
        "you": you,
        "hostile_creatures": [h.info for h in hostiles],
        "corpses_not_yet_looted": [c.info for c in corpses],
        "items_in_open_corpse": [i.info for i in items],
        "other_beings_nearby": others,
        "recent_events": events[-8:],
    }
    return Situation(snapshot, state, hostiles, corpses, items, hp_pct,
                     strategy=(snapshot["agent"].get("strategy") or "").strip())


def journal_events(entries: list[dict[str, Any]]) -> list[str]:
    """System and agent lines only. Other players' speech is left out of the
    model's state: it is untrusted text and a warrior's decisions do not need it."""
    out = []
    for e in entries:
        if e.get("source") in ("system", "agent", "client") or e.get("type") == "system":
            text = e.get("text", "").strip()
            if text:
                out.append(text if e.get("source") != "agent" else f"(you) {text}")
    return out
