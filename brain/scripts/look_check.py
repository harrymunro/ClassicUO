"""Is what a huddle's plan waits for a look from the edge? Ways of asking it, on recorded plans (cuo-cym.14).

A plan that holds the party back for a sighting sends those it names to the edge to look (huddle.look_question). Live
on 2026-10-27 one plan, "Scout the Britain Graveyard from the western edge first. Enter only if no lich is seen", read
as a look (1.0), and the party looked and went in; the next run's "Hunt the undead ... Before entering, a fresh scout
from the western edge must show no lich" read as not (0.22 to 0.29), and nobody went. This puts the held-back plans of
the lich scene's huddles and today's (brain/bench/2026-10-27-look-plans.json, labelled before any model saw them) to
each way of asking.

  uv run python scripts/look_check.py [--models decision1,jev] [--out bench/2026-10-27-look-asked.json]
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

from uo_brain import costs, huddle  # noqa: E402
from uo_brain import judge as judges  # noqa: E402

AREA = "Britain Graveyard"


def first_wording(area: str) -> dict[str, Any]:
    """huddle.look_question as first built (2026-10-27 morning): Decision 1 24 of 29."""
    return {"type": "noul",
            "instructions": {"role": "A party of adventurers in the game Ultima Online agreed a plan in a huddle (`plan`). It "
                                     f"doesn't send them into the fight at {area} yet.",
                             "question": f"Is what the plan waits for something the party could see for itself by "
                                         f"standing together at the edge of {area} and looking?"},
            "criteria": {"true": "Yes: a scout, a sighting of what is there, how many there are, whether the way is clear, "
                                 "whether something it fears is in view or only a few at a time come.",
                         "false": "No: it waits for something else (someone to be raised, rested or healed, supplies, a "
                                  "member to come back, another errand first), or the hunt is off or elsewhere."}}


def noul_b(area: str) -> dict[str, Any]:
    return {"type": "noul",
            "instructions": {"role": "A party of adventurers in the game Ultima Online agreed a plan in a huddle (`plan`). It "
                                     f"doesn't send them into the fight at {area} straight away.",
                             "question": f"Before they go into {area}, is the one thing the plan waits for a look at the "
                                         "ground (a scout of it, a sighting, a count of what is there, whether a lich is "
                                         "in view), which the party could make together from the edge?"},
            "criteria": {"true": "Yes: scout first, go in only if no lich is seen, only if single or paired undead are in "
                                 "view, only once a clear window is seen, a clear count of what is there.",
                         "false": "No: it waits first for something else as well: someone raised or back, healed or rested, "
                                  "a pet, supplies, a regroup somewhere else; or the hunt is off or elsewhere."}}


def choice_c(area: str) -> dict[str, Any]:
    return {"type": "choice",
            "instructions": {"role": "A party of adventurers in the game Ultima Online agreed a plan in a huddle (`plan`). It "
                                     f"doesn't send them into the fight at {area} straight away.",
                             "question": f"What does the plan wait for before the party goes into {area}?"},
            "criteria": {"look": "Only a look at the ground: a scout, a sighting, a count of what is there, whether a lich is "
                                 "in view or only a few undead",
                         "other": "Something else first: someone raised or back in the fight, healed or rested, a pet, "
                                  "supplies, a regroup somewhere else",
                         "off": "Nothing: the hunt is off, or the plan is for somewhere else"}}


WAYS = {"A (first wording)": ("look", first_wording), "B (noul reworded, huddle.look_question since 2026-10-27)": ("look", huddle.look_question), "C (a choice)": ("look", choice_c)}


async def ask(judge: Any, plan: dict[str, Any], key: str, q: dict[str, Any]) -> Any:
    for attempt in range(3):
        try:
            with costs.kind("order"):
                return await judge.ask(huddle.plan_state(plan), {key: q})
        except Exception:  # noqa: BLE001
            await asyncio.sleep(1 + attempt)
    return None


def p_of(a: Any, key: str, q: dict[str, Any]) -> float:
    if q["type"] == "noul":
        return float(a.nouls.get(key, 0.0))
    c = a.choices.get(key)
    return float(c.probabilities.get("look", 0.0)) if c else 0.0


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="decision1")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    judges.load_dotenv(BRAIN / ".env")
    plans = json.loads((BRAIN / "bench" / "2026-10-27-look-plans.json").read_text())["plans"]
    result: dict[str, Any] = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "cut": huddle.GO_NOW_CUT, "plans": len(plans),
                              "models": {}}
    for name in [m.strip() for m in args.models.split(",") if m.strip()]:
        judge = judges.make("jev", model=None if name == "jev" else name)
        out: dict[str, Any] = {}
        try:
            for way, (key, make) in WAYS.items():
                q = make(AREA)
                answers = []
                for i in range(0, len(plans), 6):
                    answers += await asyncio.gather(*(ask(judge, p, key, q) for p in plans[i:i + 6]))
                got = [{"i": i, "want": p["want"], "p": round(p_of(a, key, q), 3), "target": p["target"][:140],
                        "cost": a.cost, "ms": a.latency_ms} for i, (p, a) in enumerate(zip(plans, answers)) if a is not None]
                for g in got:
                    g["model"] = g["p"] >= huddle.GO_NOW_CUT
                out[way] = {"right": f"{sum(g['model'] == g['want'] for g in got)}/{len(got)}",
                            "looked_when_it_waits_for_more": sum(g["model"] and not g["want"] for g in got),
                            "no_look_when_a_look_settles_it": sum(not g["model"] and g["want"] for g in got),
                            "wrong": [{k: g[k] for k in ("i", "want", "p", "target")} for g in got if g["model"] != g["want"]],
                            "ms": round(statistics.median(g["ms"] for g in got)) if got else None,
                            "cost_usd_a_plan": round(statistics.mean(g["cost"] for g in got), 7) if got else None}
        finally:
            await judge.close()
        result["models"][name] = out
    if args.out:
        args.out.write_text(json.dumps(result, indent=1))
    for name, out in result["models"].items():
        for way, s in out.items():
            print(f"{name} {way}: {s['right']} right; looked when it waits for more {s['looked_when_it_waits_for_more']}, "
                  f"no look when a look settles it {s['no_look_when_a_look_settles_it']}; {s['ms']} ms, ${s['cost_usd_a_plan']}")
            for w in s["wrong"]:
                print(f"    #{w['i']} wanted {w['want']} p {w['p']}: {w['target']}")


if __name__ == "__main__":
    asyncio.run(main())
