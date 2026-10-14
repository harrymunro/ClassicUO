"""Orders for the fight itself, read without the game (cuo-cym.24).

A companion of a character a person plays read "kill the mage first", "fall back" and "heal me" as requests it
doesn't carry out. With creatures in view, each line the leader says is now also put to system one as a question of
its own (companion.fight_question): an order to go for one creature now, to fall back to the leader, to heal someone
now, or none of them; with it, which of the creatures in view is meant (companion.target_question), and on a heal,
whom (companion.whom_question). This puts a set of lines to the models, each in a written scene of creatures about,
for each of three companions (Isolde the mage, Wystan the archer and Thea the tamer, led by Roland), and counts how
often each reads the line as written below.

  uv run python scripts/fight_orders_check.py --out bench/2026-10-14-fight-orders.json
  uv run python scripts/fight_orders_check.py --sets second --out bench/2026-10-14-fight-orders-held-back.json

Two sets of lines. The first was written before any model had seen it, and the questions' wording was settled on
it. The second was written after that and run once: its numbers are the ones to go by. One rule came after that
run, from it, and is applied here when scoring (taken): a heal whom_question can't place is the leader's when the
line names nobody of the party ("heal pls"), and no order for a companion that is the only one named
(companion.heal_whom). The saved results of both sets (bench/2026-10-14-fight-orders*.json) were scored without it.
Every scene's state says the companion is fighting beside the leader, as it is live once it is engaged.

What each line wants, for each companion: no order for the fight (None), "fall_back", ("attack", the creature),
or ("heal", whom). A reading is right when the kind is right at the cut (companion.FIGHT_CUT) and, for an attack,
the creature at its cut (companion.TARGET_CUT), for a heal whom (companion.WHOM_CUT). Counted apart: an order
missed, talk taken for an order, one kind for another, the wrong creature (the harm: it goes for another), an
attack with no creature it could tell (it asks "Which one?"), and the wrong one to heal. The creature options are
built as live, from a snapshot of the scene seen from each companion (companion.creatures_in_view).
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

from uo_brain import companion, costs  # noqa: E402
from uo_brain import judge as judges  # noqa: E402

LEADER = "Roland"
PARTY = ["Isolde", "Wystan", "Thea"]
FB = "fall_back"


def A(key: str) -> tuple[str, str]:
    return ("attack", key)


def H(whom: str) -> tuple[str, str]:
    return ("heal", whom)


# The scenes: where everyone stands (tiles east and south of the leader), and the creatures about: what each is
# called, whether it is in war mode, whom it attacks, and its health.
SCENES: dict[str, dict[str, Any]] = {
    # A lich on the leader, a bone knight on the mage, a skeleton on the archer, another skeleton loose, and an evil
    # mage casting from further off. Thea's bear stands by her.
    "graveyard": {
        "party": {"Roland": (0, 0), "Isolde": (-2, 3), "Wystan": (-5, 2), "Thea": (3, 3)},
        "pets": [("Bramble", (2, 1))],
        "creatures": {"lich": ("a lich", (1, -1), True, "Roland", 80),
                      "bone knight": ("a bone knight", (-1, 4), True, "Isolde", 100),
                      "skeleton on Wystan": ("a skeleton", (-6, 3), True, "Wystan", 100),
                      "skeleton": ("a skeleton", (6, -2), True, None, 100),
                      "mage": ("an evil mage", (8, -7), True, None, 100)}},
    # One ogre, on the leader.
    "ogre": {
        "party": {"Roland": (0, 0), "Isolde": (-3, 2), "Wystan": (-4, -1), "Thea": (2, 3)},
        "pets": [("Bramble", (1, 2))],
        "creatures": {"ogre": ("an ogre", (1, 0), True, "Roland", 55)}},
    # Before a fight: three orcs that haven't seen the party, and a deer.
    "orcs": {
        "party": {"Roland": (0, 0), "Isolde": (-1, 2), "Wystan": (-3, 1), "Thea": (1, 2)},
        "pets": [("Bramble", (2, 2))],
        "creatures": {"orc": ("an orc", (7, -3), False, None, 100),
                      "far orc": ("an orc", (9, -2), False, None, 100),
                      "captain": ("an orc captain", (10, -5), False, None, 100),
                      "deer": ("a deer", (-8, 6), False, None, 100)}},
    # The second set's (written with it, after the wording was settled): a wraith on the leader, a zombie on the
    # archer and one loose, a ghoul on the tamer, a lich lord and a skeletal mage casting from further off.
    "crypt": {
        "party": {"Roland": (0, 0), "Isolde": (2, 2), "Wystan": (-3, 4), "Thea": (-1, -3)},
        "pets": [("Bramble", (0, -2))],
        "creatures": {"wraith": ("a wraith", (1, 1), True, "Roland", 70),
                      "zombie on Wystan": ("a zombie", (-3, 5), True, "Wystan", 100),
                      "zombie": ("a zombie", (5, -1), True, None, 100),
                      "ghoul": ("a ghoul", (-2, -4), True, "Thea", 60),
                      "lich lord": ("a lich lord", (7, 6), True, None, 100),
                      "skeletal mage": ("a skeletal mage", (9, -5), True, None, 100)}},
    # An ettin on the tamer, the leader a few steps off.
    "ettin": {
        "party": {"Roland": (0, 0), "Isolde": (-2, 1), "Wystan": (-4, -2), "Thea": (3, 1)},
        "pets": [("Bramble", (4, 2))],
        "creatures": {"ettin": ("an ettin", (4, 0), True, "Thea", 85)}},
}

# (scene, group, the line, what each companion should take: one for all, or by name with "*" for the rest)
FIRST: list[tuple] = [
    ("graveyard", "attack", "Kill the mage first!", A("mage")),
    ("graveyard", "attack", "Get the one on me!", A("lich")),
    ("graveyard", "attack", "That one!", A("lich")),
    ("graveyard", "attack", "Wystan, shoot the lich", {"Wystan": A("lich"), "*": None}),
    ("graveyard", "attack", "Isolde, the bone knight!", {"Isolde": A("bone knight"), "*": None}),
    ("graveyard", "attack", "tank that", A("lich")),
    ("graveyard", "attack", "Thea, send the bear at the lich", {"Thea": A("lich"), "*": None}),
    ("graveyard", "attack", "Everyone on the lich!", A("lich")),
    ("graveyard", "attack", "Get that skeleton off Wystan!", A("skeleton on Wystan")),
    ("graveyard", "attack", "focus the evil mage", A("mage")),
    ("graveyard", "attack", "Take out the bone knight before it gets Isolde", A("bone knight")),
    ("ogre", "attack", "kill it!", A("ogre")),
    ("ogre", "attack", "It's nearly dead, finish it!", A("ogre")),
    ("orcs", "attack", "Wystan, shoot the captain", {"Wystan": A("captain"), "*": None}),
    ("orcs", "attack", "Kill that deer.", A("deer")),

    ("graveyard", "fall back", "Fall back!", FB),
    ("graveyard", "fall back", "Get out of there!", FB),
    ("graveyard", "fall back", "Back to me, now!", FB),
    ("graveyard", "fall back", "Retreat, retreat!", FB),
    ("graveyard", "fall back", "Leave it, come here.", FB),
    ("graveyard", "fall back", "Wystan, pull back!", {"Wystan": FB, "*": None}),
    ("graveyard", "fall back", "Run!", FB),
    ("ogre", "fall back", "Fall back, it's too strong", FB),

    ("graveyard", "heal", "Heal me!", H("Roland")),
    ("graveyard", "heal", "Isolde, heal Thea", {"Isolde": H("Thea"), "*": None}),
    ("graveyard", "heal", "bandage me, quick", H("Roland")),
    ("graveyard", "heal", "I need a heal here!", H("Roland")),

    ("graveyard", "stays what it is", "cover me", None),
    ("graveyard", "stays what it is", "Nice shot!", None),
    ("graveyard", "stays what it is", "Nice shot, Wystan!", None),
    ("graveyard", "stays what it is", "kill me now, I forgot the reagents", None),
    ("graveyard", "stays what it is", "all kill", None),
    ("graveyard", "stays what it is", "all guard me", None),
    ("graveyard", "stays what it is", "any bandages left?", None),
    ("graveyard", "stays what it is", "How many arrows have you got, Wystan?", None),
    ("graveyard", "stays what it is", "Follow me.", None),
    ("graveyard", "stays what it is", "Stay here.", None),
    ("graveyard", "stays what it is", "Off to the inn with you, I'll deal with these.", None),
    ("graveyard", "stays what it is", "Hold them off!", None),
    ("graveyard", "stays what it is", "watch my back", None),
    ("graveyard", "stays what it is", "kill anything that moves", None),
    ("graveyard", "stays what it is", "weakest first", None),
    ("graveyard", "stays what it is", "That lich nearly had me.", None),
    ("graveyard", "stays what it is", "Kill them all!", None),
    ("graveyard", "stays what it is", "Don't run, whatever happens.", None),
    ("graveyard", "stays what it is", "Wystan, nice shot on that lich", None),
    ("graveyard", "stays what it is", "Heal thyself, as they say.", None),
    ("ogre", "stays what it is", "Get off me, you brute!", None),
    ("ogre", "stays what it is", "Ogre's mine, you lot keep out of it.", None),
    ("orcs", "stays what it is", "Let's take them.", None),
    ("orcs", "stays what it is", "Quietly now, they haven't seen us.", None),
]

# The second set: written after the wording was settled on the first, before any model had seen it, and run once.
SECOND: list[tuple] = [
    ("crypt", "attack", "the mage! the mage!", A("skeletal mage")),
    ("crypt", "attack", "kill the lich lord", A("lich lord")),
    ("crypt", "attack", "this one, quick", A("wraith")),
    ("crypt", "attack", "someone get this wraith off me", A("wraith")),
    ("crypt", "attack", "Isolde, fireball the zombie on Wystan", {"Isolde": A("zombie on Wystan"), "*": None}),
    ("crypt", "attack", "Wystan, the ghoul on Thea!", {"Wystan": A("ghoul"), "*": None}),
    ("crypt", "attack", "Thea, sic Bramble on the lord", {"Thea": A("lich lord"), "*": None}),
    ("crypt", "attack", "all of you on the lich lord, now", A("lich lord")),
    ("crypt", "attack", "help Thea with that ghoul", A("ghoul")),
    ("crypt", "attack", "drop what you're doing and kill the skeletal mage", A("skeletal mage")),
    ("ettin", "attack", "Get it off Thea!", A("ettin")),
    ("ettin", "attack", "smash it", A("ettin")),
    ("orcs", "attack", "Wystan, pull the near one", {"Wystan": A("orc"), "*": None}),

    ("crypt", "fall back", "back off!", FB),
    ("crypt", "fall back", "everyone out, NOW", FB),
    ("crypt", "fall back", "disengage", FB),
    ("crypt", "fall back", "fall back to me", FB),
    ("crypt", "fall back", "Isolde get away from it, come to me", {"Isolde": FB, "*": None}),
    ("crypt", "fall back", "too many, pull back", FB),
    ("ettin", "fall back", "leave it, leave it!", FB),

    ("crypt", "heal", "heal pls", H("Roland")),
    ("crypt", "heal", "patch me up Thea", {"Thea": H("Roland"), "*": None}),
    # Wystan can't be ordered to heal himself (whom isn't offered him): nothing, for him.
    ("crypt", "heal", "someone heal Wystan", {"Wystan": None, "*": H("Wystan")}),
    ("crypt", "heal", "I'm dying here, heals!", H("Roland")),
    ("ettin", "heal", "Isolde, Thea needs healing", {"Isolde": H("Thea"), "*": None}),

    ("crypt", "stays what it is", "keep them busy", None),
    ("crypt", "stays what it is", "good kill", None),
    ("crypt", "stays what it is", "lol that wraith", None),
    ("crypt", "stays what it is", "who's got the most mana?", None),
    ("crypt", "stays what it is", "all follow me", None),
    ("crypt", "stays what it is", "come on then, you bag of bones!", None),
    ("crypt", "stays what it is", "leave the lord to me", None),
    ("crypt", "stays what it is", "I'll tank, you lot do damage", None),
    ("crypt", "stays what it is", "protect Isolde", None),
    ("crypt", "stays what it is", "I'm fine, keep going", None),
    ("crypt", "stays what it is", "Wait here.", None),
    ("crypt", "stays what it is", "heal up when you can", None),
    ("crypt", "stays what it is", "don't heal me, I've got potions", None),
    ("ettin", "stays what it is", "it hits hard, careful", None),
    ("ettin", "stays what it is", "Bramble's doing well", None),
    ("orcs", "stays what it is", "Nobody move yet.", None),
    ("orcs", "stays what it is", "On my mark...", None),
]


def wanted(want: Any, who: str) -> Any:
    return want.get(who, want.get("*")) if isinstance(want, dict) else want


def snapshot(scene: dict[str, Any], who: str) -> dict[str, Any]:
    """What the scene looks like to one companion's client: everyone else as mobiles, from where it stands."""
    me = scene["party"][who]
    mobiles = []

    def at(pos: tuple[int, int]) -> dict[str, int]:
        dx, dy = pos[0] - me[0], pos[1] - me[1]
        return {"dx": dx, "dy": dy, "distance": max(abs(dx), abs(dy))}

    serial = 0x1000
    for name, pos in scene["party"].items():
        if name != who:
            serial += 1
            mobiles.append({"serial": serial, "name": name, "monster": False, "pet": False, "dead": False,
                            "hits_pct": 100, "war_mode": True, **at(pos)})
    for name, pos in scene["pets"]:
        serial += 1
        mobiles.append({"serial": serial, "name": name, "monster": True, "pet": True, "dead": False, "hits_pct": 100,
                        "war_mode": True, **at(pos)})
    for i, (key, (name, pos, war, on, hp)) in enumerate(scene["creatures"].items()):
        mobiles.append({"serial": 0x2000 + i, "key": key, "name": name, "monster": True, "pet": False, "dead": False,
                        "hits_pct": hp, "war_mode": war, "attacking_me": on == who, **at(pos)})
    mobiles.sort(key=lambda m: m["distance"])
    return {"player": {"name": who}, "mobiles": mobiles}


def cases(lines: list[tuple]) -> list[dict[str, Any]]:
    out = []
    for scene, group, line, want, *more in lines:
        for who in PARTY:
            out.append({"scene": scene, "group": group, "line": line, "who": who, "want": wanted(want, who),
                        "before": more[0] if more else []})
    return out


def taken(row: dict[str, Any], cut: float, target_cut: float) -> Any:
    """What a companion carries out from the answers, as it does live (Companion.fight_reading): the kind at the cut,
    with the creature or whom; ("attack", None) is an attack with no creature it could tell."""
    probs = row["fight_p"]
    pick = max(probs, key=probs.get)
    if pick not in companion.FIGHT_ORDERS or probs[pick] < cut:
        return None
    if pick == "attack":
        tp = row["target_p"]
        t = max(tp, key=tp.get)
        return ("attack", row["ids"].get(t) if tp[t] >= target_cut else None)
    if pick == "heal":
        # Whom the line couldn't tell, by the rule added after the second set's run (companion.heal_whom): the leader
        # when nobody of the party is named, and no order when only this companion is.
        whom = row.get("whom") or companion.heal_whom(row["line"], row["who"], LEADER, sorted(PARTY))
        return None if whom == row["who"] else ("heal", whom or None)
    return pick


async def ask(judge: Any, case: dict[str, Any]) -> dict[str, Any]:
    scene = SCENES[case["scene"]]
    who = case["who"]
    others = [n for n in PARTY if n != who]
    snap = snapshot(scene, who)
    creatures = companion.creatures_in_view(snap, who, LEADER, others)
    keys = {m["serial"]: m["key"] for m in snap["mobiles"] if "key" in m}
    ids = {c["id"]: keys[c["serial"]] for c in creatures}
    st = companion.fight_state(case["line"], who, LEADER, others, f"fighting beside {LEADER}", creatures, case["before"])
    qs = {"fight": companion.fight_question(who, LEADER), "target": companion.target_question(who, LEADER, creatures)}
    err = ""
    for attempt in range(3):
        try:
            with costs.kind("order"):
                a = await judge.ask(st, qs)
            f, t = a.choices["fight"], a.choices["target"]
            row = {"fight_p": {k: round(v, 3) for k, v in f.probabilities.items()},
                   "target_p": {k: round(v, 3) for k, v in t.probabilities.items()}, "ids": ids,
                   "latency_ms": a.latency_ms, "cost": a.cost, "input_tokens": a.input_tokens, "model": a.model}
            pick = max(row["fight_p"], key=row["fight_p"].get)
            if pick == "heal" and row["fight_p"][pick] >= 0.3:
                # Whom: asked live once a heal is read (at the cut); here at any reading of one, for the cuts below.
                with costs.kind("order"):
                    w = await judge.ask({"party": {"leader": LEADER, "companions": sorted(PARTY)}, "companion": who,
                                         "line": case["line"]},
                                        {"whom": companion.whom_question(who, LEADER, sorted(PARTY))})
                c = w.choices["whom"]
                p = c.probabilities.get(c.choice, c.confidence)
                row["whom"] = c.choice if c.choice != "unclear" and p >= companion.WHOM_CUT else None
                row["whom_p"] = round(p, 3)
                row["cost"] += w.cost
            return row
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"[:300]
            await asyncio.sleep(1 + attempt)
    return {"error": err}


def score(rows: list[dict[str, Any]], cut: float, target_cut: float) -> dict[str, Any]:
    groups: dict[str, list[int]] = {}
    counts = {"orders_missed": 0, "talk_taken_for_an_order": 0, "one_kind_for_another": 0, "wrong_creature": 0,
              "attack_with_no_creature_told": 0, "wrong_one_to_heal": 0}
    bad = []
    for r in rows:
        got = taken(r, cut, target_cut)
        want = r["want"]
        ok = got == want
        g = groups.setdefault(r["group"], [0, 0])
        g[0] += ok
        g[1] += 1
        if ok:
            continue
        kind_w = want[0] if isinstance(want, tuple) else want
        kind_g = got[0] if isinstance(got, tuple) else got
        if kind_w is not None and kind_g is None:
            counts["orders_missed"] += 1
        elif kind_w is None:
            counts["talk_taken_for_an_order"] += 1
        elif kind_w != kind_g:
            counts["one_kind_for_another"] += 1
        elif kind_w == "attack" and got[1] is None:
            counts["attack_with_no_creature_told"] += 1
        elif kind_w == "attack":
            counts["wrong_creature"] += 1
        else:
            counts["wrong_one_to_heal"] += 1
        bad.append({k: r[k] for k in ("scene", "group", "line", "who")} | {"want": want, "got": got,
                                                                            "fight": r["fight_p"]}
                   | ({"target": {r["ids"].get(k, k): v for k, v in r["target_p"].items() if v >= 0.05}}
                      if kind_w == "attack" or kind_g == "attack" else {})
                   | ({"whom_p": r["whom_p"]} if "whom_p" in r else {}))
    right = sum(g[0] for g in groups.values())
    return {"right": right, "of": len(rows), "pct": round(100 * right / max(1, len(rows)), 1),
            "by_group": {k: f"{a}/{b}" for k, (a, b) in groups.items()}, **counts, "wrong": bad}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="jev,decision1", help="aliases or model ids, comma-separated")
    ap.add_argument("--sets", default="first")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    judges.load_dotenv(BRAIN / ".env")
    sets = {k: cases(v) for k, v in (("first", FIRST), ("second", SECOND)) if k in args.sets.split(",") and v}
    result: dict[str, Any] = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "cut": companion.FIGHT_CUT,
                              "target_cut": companion.TARGET_CUT, "whom_cut": companion.WHOM_CUT,
                              "lines": {k: len(v) // len(PARTY) for k, v in sets.items()}, "models": {}}
    for name in [m.strip() for m in args.models.split(",") if m.strip()]:
        judge = judges.make("jev", model=None if name == "jev" else name)
        result["models"][name] = {}
        try:
            for which, todo in sets.items():
                got = []
                for i in range(0, len(todo), 6):  # six questions at a time
                    got += await asyncio.gather(*(ask(judge, c) for c in todo[i:i + 6]))
                rows = [{**c, **g} for c, g in zip(todo, got) if "error" not in g]
                errors = [g["error"] for g in got if "error" in g]
                s = score(rows, companion.FIGHT_CUT, companion.TARGET_CUT)
                s["cuts"] = {f"{c}/{t}": {k: v for k, v in score(rows, c, t).items() if k not in ("wrong", "by_group", "of", "pct")}
                             for c in (0.4, 0.5, 0.6, 0.7, 0.8) for t in (0.3, 0.5, 0.7)}
                lat = sorted(r["latency_ms"] for r in rows)
                s |= {"judge": judge.name, "served_by": sorted({r["model"] for r in rows}),
                      "latency_ms": {"median": round(statistics.median(lat)), "p90": round(lat[int(0.9 * (len(lat) - 1))])}
                      if lat else {},
                      "cost_usd_a_line": round(statistics.mean(r["cost"] for r in rows), 7) if rows else None,
                      "input_tokens": round(statistics.mean(r["input_tokens"] for r in rows)) if rows else None,
                      "errors": len(errors), **({"first_error": errors[0]} if errors else {})}
                result["models"][name][which] = s
                if args.out:
                    args.out.write_text(json.dumps(result, indent=1))
        finally:
            await judge.close()
    result["costs"] = costs.ledger.summary()
    if args.out:
        args.out.write_text(json.dumps(result, indent=1))
    for name, by_set in result["models"].items():
        for which, s in by_set.items():
            print(f"\n{name}, {which} set: {s['right']}/{s['of']} right ({s['pct']}%)  missed {s['orders_missed']}, talk "
                  f"taken for an order {s['talk_taken_for_an_order']}, one kind for another {s['one_kind_for_another']}, "
                  f"wrong creature {s['wrong_creature']}, attack with none told {s['attack_with_no_creature_told']}, "
                  f"wrong one to heal {s['wrong_one_to_heal']}")
            print("  " + "  ".join(f"{k} {v}" for k, v in s["by_group"].items()))
            print("  by cut (fight/target): " + "  ".join(f"{c}: {v['right']}" for c, v in s["cuts"].items()))
            print(f"  {s['latency_ms']} ms, ${s['cost_usd_a_line']} a line, {s['input_tokens']} input tokens, {s['errors']} errors")
            for b in s["wrong"]:
                print(f"    {b['scene']:9} {b['who']:7} {b['line']!r}: wanted {b['want']}, got {b['got']}  {b['fight']}"
                      + (f"  target {b['target']}" if "target" in b else "") + (f"  whom {b['whom_p']}" if "whom_p" in b else ""))
    if args.out:
        print(f"\nresults: {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
