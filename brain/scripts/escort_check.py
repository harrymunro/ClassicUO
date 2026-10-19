"""What the leader a person plays is fighting, per creature: the 4-tile rule against system one, without the game
(cuo-cym.25).

A companion of a character a person plays fights what attacks it and what stands in war mode within 4 tiles of the
leader or 6 of itself (policy's escort rule). escort.theirs_questions puts each creature in view to system one
instead, with what the companion's client sees: distances, war mode, which way it faces and whether at the leader,
whether it came nearer the leader, whether it is wounded with no companion on it, how the leader stands and what it
said lately. This puts written moments to the rule and to both models and counts, per creature, how often each gives
the answer wanted (written before any model saw the moment, as a player who leads would want it).

  uv run python scripts/escort_check.py --out bench/2026-10-19-escort.json

Positions are in tiles east (dx) and south (dy) of the companion. A moment's creatures: (name, dx, dy, war mode,
facing, health %, attacking the companion, tiles nearer the leader than 4 s ago). The leader: (dx, dy, war mode,
facing). Companions: name -> (dx, dy).
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

BRAIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRAIN / "src"))

from uo_brain import costs, escort  # noqa: E402
from uo_brain import judge as judges  # noqa: E402

ME, LEADER = "Isolde", "Roland"
MATES = ["Isolde", "Wystan", "Thea"]


def M(mid: str, leader: tuple, creatures: list[tuple], want: set[str], why: str, said: list[str] | None = None,
      mates: dict[str, tuple[int, int]] | None = None) -> dict[str, Any]:
    return {"id": mid, "leader": leader, "creatures": creatures, "want": want, "why": why, "said": said or [],
            "mates": mates or {}}


FIRST = [
    M("f01", (1, 2, True, "north"), [("a skeleton", 1, -8, True, "south", 70, False, 3)], {"c1"},
      "the leader shoots at a skeleton 10 tiles north that is wounded and coming at it"),
    M("f02", (1, 2, False, "north"), [("a skeleton", 1, -8, False, "east", 100, False, 0)], set(),
      "a skeleton wandering, nobody in war mode"),
    M("f03", (1, 2, False, "west"), [("an orc", 9, 1, True, "east", 100, False, -1)], set(),
      "an orc in war mode far off facing away, the leader not fighting"),
    M("f04", (1, 2, True, "east"), [("a zombie", 4, 2, True, "west", 100, False, 1)], {"c1"},
      "a zombie in war mode 3 tiles from the leader, facing it"),
    M("f05", (0, 2, True, "north"), [("a mongbat", 0, -9, True, "south", 100, False, 4), ("a hind", 5, 0, False, "north", 100, False, 0)],
      {"c1"}, "a mongbat charging the leader from 11 tiles; a hind grazing"),
    M("f06", (0, 1, True, "west"), [("an orc", -7, 1, True, "east", 60, False, 0), ("an orc", 11, 2, False, "south", 100, False, 0)],
      {"c1"}, "the leader faces an orc 7 west, wounded with nobody on it, that faces the leader; another orc east, at peace"),
    M("f07", (0, 2, True, "north"), [("a lich", 3, -8, True, "south", 90, False, 0)], {"c1"},
      "the leader called for the lich; Wystan is on it", said=["Get the lich!"], mates={"Wystan": (3, -7)}),
    M("f08", (1, 1, True, "north"), [("a skeletal mage", 1, -9, True, "south", 100, False, 0)], {"c1"},
      "a skeletal mage casting at the leader from 10 tiles: in war mode, facing it, the leader facing it"),
    M("f09", (0, 0, True, "south"), [("a ratman", 0, -9, True, "south", 100, False, 3)], {"c1"},
      "a ratman coming at the party from behind"),
    M("f10", (0, 1, False, "east"), [("a wolf", -9, 4, True, "west", 100, False, -3)], set(),
      "a wolf in war mode going away west, the leader at peace"),
    M("f11", (1, 0, False, "north"), [("a sparrow", 2, -2, False, "east", 100, False, 0)], set(), "a bird"),
    M("f12", (0, 1, True, "north"), [("a troll", 2, -6, True, "south", 100, False, 1), ("a troll", 12, -3, True, "east", 100, False, 0)],
      {"c1"}, "a troll 7 from the leader facing it; another 12 off facing away"),
    M("f13", (1, 1, True, "north"), [("a skeleton", 2, -2, True, "south", 100, False, 1), ("a zombie", 0, -3, True, "south", 100, False, 1),
                                     ("an ettin", 13, -4, True, "east", 100, False, -2)],
      {"c1", "c2"}, "two on the leader's doorstep, an ettin far off minding its own business"),
    M("f14", (1, 2, True, "north"), [("a troll", 1, -7, True, "south", 80, False, 0)], set(),
      "the leader says to leave the troll", said=["Leave the troll alone, it's not worth it."]),
    M("f15", (0, 1, True, "east"), [("an ettin", 11, 1, True, "west", 100, False, 1)], {"c1"},
      "the leader pulls an ettin", said=["I'll pull the ettin, get ready."]),
    M("f16", (0, 2, False, "south"), [("a headless one", 3, 3, True, "north", 100, True, 0)], {"c1"},
      "a headless one attacking the companion"),
]

HELD = [
    M("h01", (1, 1, True, "west"), [("a gazer", -8, 1, True, "east", 55, False, 1)], {"c1"},
      "the leader's arrows on a gazer 9 west, wounded, nobody on it"),
    M("h02", (0, 2, False, "north"), [("a zombie", 4, -9, True, "west", 100, False, 0)], set(),
      "a zombie in war mode facing away, the leader not in war mode"),
    M("h03", (1, 0, True, "south"), [("a bone knight", 1, 7, True, "north", 100, False, 3)], {"c1"},
      "a bone knight charging the leader from 7 tiles"),
    M("h04", (0, 1, True, "east"), [("an orc lord", 8, 1, True, "west", 100, False, 0), ("an orc", 8, -6, False, "north", 100, False, 0)],
      {"c1"}, "an orc lord facing the leader 8 east in war mode; an orc at peace"),
    M("h05", (0, 1, False, "north"), [("a rabbit", 3, -3, False, "south", 100, False, 0), ("a deer", -6, 2, False, "east", 100, False, 0)],
      set(), "harmless wildlife"),
    M("h06", (1, 1, True, "north"), [("a spectre", 1, -10, True, "south", 100, False, 2)], {"c1"},
      "a spectre gliding at the leader from 11 tiles"),
    M("h07", (0, 1, True, "west"), [("a lizardman", -9, 0, True, "north", 100, False, -2)], set(),
      "a lizardman in war mode going north, away"),
    M("h08", (0, 2, True, "north"), [("a skeleton", 1, -4, True, "south", 100, False, 0), ("a lich", 4, -11, True, "south", 100, False, 1)],
      {"c1", "c2"}, "a skeleton 6 from the leader and a lich 13 off, both facing the leader in war mode"),
    M("h09", (0, 1, True, "east"), [("an ogre", 9, 1, True, "west", 100, False, 0)], {"c1"},
      "the leader said to get the ogre", said=["Kill the ogre!"]),
    M("h10", (0, 1, True, "north"), [("a ratman archer", 2, -9, True, "south", 100, False, 0)], {"c1"},
      "a ratman archer shooting at the leader from 10 tiles"),
    M("h11", (1, 1, False, "south"), [("a wraith", 1, -12, True, "east", 100, False, 0)], set(),
      "a wraith far off in war mode, facing away, the leader walking"),
    M("h12", (0, 1, True, "south"), [("a slime", 1, 4, True, "north", 100, False, 0)], {"c1"},
      "a slime in war mode 3 from the leader"),
]


def snap_of(m: dict[str, Any]) -> dict[str, Any]:
    ldx, ldy, lwar, lface = m["leader"]
    mobiles = [{"serial": 0x700, "name": LEADER, "monster": False, "human": True, "dead": False, "dx": ldx, "dy": ldy,
                "distance": max(abs(ldx), abs(ldy)), "war_mode": lwar, "facing": lface, "hits_pct": 100}]
    for i, (name, (dx, dy)) in enumerate(m["mates"].items()):
        mobiles.append({"serial": 0x710 + i, "name": name, "monster": False, "human": True, "dead": False, "dx": dx, "dy": dy,
                        "distance": max(abs(dx), abs(dy)), "war_mode": True, "facing": "north", "hits_pct": 100})
    for i, (name, dx, dy, war, face, hp, on_me, _nearer) in enumerate(m["creatures"]):
        mobiles.append({"serial": 0x4000 + i, "name": name, "monster": True, "dead": False, "pet": False, "dx": dx, "dy": dy,
                        "distance": max(abs(dx), abs(dy)), "war_mode": war, "facing": face, "hits_pct": hp,
                        "attacking_me": on_me})
    return {"mobiles": mobiles}


def moment(m: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """The moment as the companion would see it, with each creature's approach as written (the trail stood in for),
    and the ids wanted as the scene gives them (it lists the creatures nearest first; the moment, as written)."""
    who, creatures = escort.scene(snap_of(m), ME, LEADER, MATES, said=m["said"])
    by_serial = {c["serial"]: c for c in creatures}
    for i, raw in enumerate(m["creatures"]):
        by_serial[0x4000 + i]["nearer_leader"] = raw[7]
    want = sorted(by_serial[0x4000 + int(w[1:]) - 1]["id"] for w in m["want"])
    return who, creatures, want


async def ask(judge: Any, st: dict[str, Any], qs: dict[str, Any]) -> Any:
    for attempt in range(3):
        try:
            with costs.kind("order"):
                return await judge.ask(st, qs)
        except Exception:  # noqa: BLE001
            await asyncio.sleep(1 + attempt)
    return None


def score(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    creatures = [(r["id"], c, r["want"], r[key]) for r in rows for c in r["ids"]]
    right = sum((c in want) == (c in got) for _, c, want, got in creatures)
    missed = sum(c in want and c not in got for _, c, want, got in creatures)
    extra = sum(c not in want and c in got for _, c, want, got in creatures)
    whole = sum(set(r["want"]) == set(r[key]) for r in rows)
    return {"creatures_right": f"{right}/{len(creatures)}", "missed": missed, "not_wanted": extra,
            "moments_right": f"{whole}/{len(rows)}",
            "wrong": [{"id": r["id"], "want": sorted(r["want"]), "got": sorted(r[key]), "why": r["why"]}
                      for r in rows if set(r["want"]) != set(r[key])]}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="jev,decision1")
    ap.add_argument("--sets", default="first,held")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    judges.load_dotenv(BRAIN / ".env")
    sets = {k: v for k, v in (("first", FIRST), ("held", HELD)) if k in args.sets.split(",")}
    result: dict[str, Any] = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "cut": escort.THEIRS_CUT, "rule": {}, "models": {}}
    built = {k: [(m, *moment(m)) for m in v] for k, v in sets.items()}
    for which, ms in built.items():
        rows = [{"id": m["id"], "why": m["why"], "want": want, "ids": [c["id"] for c in cs],
                 "rule": sorted(c["id"] for c in cs if escort.rule(c))} for m, _, cs, want in ms]
        result["rule"][which] = score(rows, "rule")
    for name in [x.strip() for x in args.models.split(",") if x.strip()]:
        judge = judges.make("jev", model=None if name == "jev" else name)
        result["models"][name] = {}
        try:
            for which, ms in built.items():
                answers = await asyncio.gather(*(ask(judge, escort.state(who, cs, ME, LEADER), escort.theirs_questions(ME, LEADER, cs))
                                                 for m, who, cs, _ in ms))
                rows = []
                for (m, who, cs, want), a in zip(ms, answers):
                    if a is None:
                        continue
                    p = {c["id"]: round(float(a.nouls.get(c["id"], 0.0)), 3) for c in cs}
                    rows.append({"id": m["id"], "why": m["why"], "want": want, "ids": [c["id"] for c in cs],
                                 "model": sorted(k for k, v in p.items() if v >= escort.THEIRS_CUT), "p": p,
                                 "latency_ms": a.latency_ms, "cost": a.cost})
                s = score(rows, "model")
                for w in s["wrong"]:
                    w["p"] = next(r["p"] for r in rows if r["id"] == w["id"])
                lat = [r["latency_ms"] for r in rows]
                s |= {"latency_ms": round(statistics.median(lat)) if lat else None,
                      "cost_usd_a_moment": round(statistics.mean(r["cost"] for r in rows), 7) if rows else None}
                result["models"][name][which] = s
        finally:
            await judge.close()
    result["costs"] = costs.ledger.summary()
    if args.out:
        args.out.write_text(json.dumps(result, indent=1))
    for which, s in result["rule"].items():
        print(f"rule, {which}: creatures {s['creatures_right']}, moments {s['moments_right']} (missed {s['missed']}, "
              f"not wanted {s['not_wanted']})")
        for w in s["wrong"]:
            print(f"    {w['id']} wanted {w['want']} got {w['got']}  [{w['why']}]")
    for name, by in result["models"].items():
        for which, s in by.items():
            print(f"{name}, {which}: creatures {s['creatures_right']}, moments {s['moments_right']} (missed {s['missed']}, "
                  f"not wanted {s['not_wanted']}); {s['latency_ms']} ms, ${s['cost_usd_a_moment']} a moment")
            for w in s["wrong"]:
                print(f"    {w['id']} wanted {w['want']} got {w['got']} {w['p']}  [{w['why']}]")
    if args.out:
        print(f"results: {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
