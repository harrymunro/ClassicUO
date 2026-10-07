"""Real-Jev smoke check of the routine hunt questions (routine.py) on hand-made situations.

Not a pytest test: it calls Jev through OpenRouter (the key in brain/.env) and costs about
$0.0005. Run from brain/: `uv run python tests/smoke_routine.py`.
"""
import asyncio
import copy
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from conftest import SNAPSHOT, mage_snapshot  # noqa: E402
from uo_brain import judge as judges  # noqa: E402
from uo_brain.routine import HuntWatch, questions  # noqa: E402


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def warrior(bandages, potions=2, weight=120, kills=0, hostiles=True, flees=0, hits=60, strategy=""):
    s = copy.deepcopy(SNAPSHOT)
    s["player"]["supplies"].update(bandages=bandages, heal_potions=potions)
    s["player"].update(weight=weight, hits=hits)
    s["agent"]["stats"] = {"kills": kills, "deaths": 0, "flees": flees}
    s["agent"]["strategy"] = strategy
    if not hostiles:
        s["mobiles"] = [m for m in s["mobiles"] if not m["monster"]]
        s["agent"]["engaged"] = 0
    return s


def case(name, start, now, minutes_in, quiet_s=0.0, last_kill_min=None, lowest=None, expect=None, mage=False):
    """A hunt that began with `start` and looks like `now` after `minutes_in` minutes."""
    c = Clock()
    hw = HuntWatch("Britain Graveyard", 15, None, clock=c, archetype="mage" if mage else None, radius=12)
    hw.observe(start)
    c.t = minutes_in * 60
    hw.last_hostile = c.t - quiet_s
    hw.last_kill = c.t - 60 * (last_kill_min if last_kill_min is not None else 0.5)
    hw.observe(now)
    if quiet_s:
        hw.last_hostile = c.t - quiet_s  # observe() reset it if `now` had hostiles
    if lowest is not None:
        hw.lowest_hp = lowest
    return name, hw.words(now), (now["agent"].get("strategy") or ""), quiet_s >= 20, expect


def mage(regs, mana, kills):
    m = mage_snapshot()
    m["player"]["supplies"]["reagents"] = regs
    m["player"]["mana"] = mana
    m["agent"]["stats"] = {"kills": kills, "deaths": 0, "flees": 0}
    return m


def archer(arrows, kills):
    s = warrior(60, kills=kills)
    s["player"]["ranged"] = {"kind": "bow", "ammo": "arrows", "range": 10}
    s["player"]["supplies"]["arrows"] = arrows
    return s


full = {k: 60 for k in ("black_pearl", "blood_moss", "garlic", "ginseng", "mandrake_root", "nightshade",
                         "sulfurous_ash", "spiders_silk")}
CASES = [
    case("going well", warrior(80), warrior(70, kills=4), 3, expect={"head_back": False, "stay_here": True}),
    case("bandages nearly gone", warrior(45, potions=0), warrior(9, potions=0, kills=6), 8,
         expect={"head_back": True}),
    case("bag nearly full", warrior(80), warrior(70, weight=372, kills=9), 9, expect={"head_back": True}),
    case("dead quiet", warrior(80, hostiles=False), warrior(76, kills=1, hostiles=False), 12, quiet_s=300,
         last_kill_min=10, expect={"head_back": False, "stay_here": False}),
    case("just gone quiet", warrior(80, hostiles=False), warrior(66, kills=6, hostiles=False), 5, quiet_s=25,
         last_kill_min=0.4, expect={"stay_here": True, "move_spot": False}),
    case("quiet for a while, kills steady", warrior(80, hostiles=False), warrior(60, kills=8, hostiles=False), 10,
         quiet_s=120, last_kill_min=2, expect={"stay_here": True, "move_spot": True}),
    case("brutal spot", warrior(60, flees=0), warrior(40, kills=1, flees=2, hits=35), 4, lowest=8,
         expect={"stay_here": False}),
    case("strategy: stay until the bandages are gone",
         warrior(30, strategy="Stay out hunting until the bandages are completely gone; never go back early."),
         warrior(12, kills=9, strategy="Stay out hunting until the bandages are completely gone; never go back early."),
         9, expect={"head_back": False}),
    case("mage short of reagents", mage(full, 90, 0),
         mage(full | {"nightshade": 3, "black_pearl": 4}, 25, 7), 9, mage=True, expect={"head_back": True}),
    case("archer nearly out of arrows", archer(110, 0), archer(6, 26), 9, expect={"head_back": True}),
]


VERDICTS = {  # what the hunt should do; "unsure" is a step towards asking the planner
    "going well": {"stay"},
    "bandages nearly gone": {"head_back"},
    "bag nearly full": {"head_back"},
    "dead quiet": {"leave_spot", "unsure"},
    "just gone quiet": {"wait"},
    "quiet for a while, kills steady": {"patrol"},
    "brutal spot": {"head_back", "leave_spot"},
    "strategy: stay until the bandages are gone": {"stay"},
    "mage short of reagents": {"head_back"},
    "archer nearly out of arrows": {"head_back"},
}


async def main():
    jev = judges.make("jev")
    ok = total = vok = 0
    lat, tokens, out = [], 0, []
    try:
        for name, words, strategy, quiet, expect in CASES:
            qs = questions(words, words["you"]["character"], strategy=strategy, quiet=quiet)
            t = time.perf_counter()
            a = await jev.ask(words, qs)
            lat.append((time.perf_counter() - t) * 1000)
            tokens += a.input_tokens
            got = {k: round(v, 2) for k, v in a.nouls.items()}
            hits = {k: (got[k] >= 0.5) == v for k, v in expect.items()}
            ok += sum(hits.values())
            total += len(hits)
            v = HuntWatch("x", 15, None).verdict(a, words).kind
            vok += v in VERDICTS[name]
            out.append({"case": name, "answers": got, "expected": expect, "right": all(hits.values()),
                        "tokens": a.input_tokens})
            print(f"{'ok ' if all(hits.values()) else 'XX '} {name}: {got} expected {expect} -> {v}"
                  f"{'' if v in VERDICTS[name] else ' (WRONG VERDICT)'}")
    finally:
        await jev.close()
    cost = tokens / 1e6 * 0.042
    print(json.dumps({"date": time.strftime("%Y-%m-%d"), "cases": len(CASES), "answers_right": f"{ok}/{total}",
                      "cases_right": sum(o["right"] for o in out), "verdicts_right": f"{vok}/{len(CASES)}",
                      "latency_ms_median": round(sorted(lat)[len(lat) // 2]),
                      "input_tokens": tokens, "tokens_per_question": round(tokens / len(CASES)),
                      "cost_usd": round(cost, 5)}, indent=1))


asyncio.run(main())
