"""Does a huddle's plan send the party in now? The plan's list of who goes against system one, on recorded plans
(cuo-cym.14).

A huddle member the plan names for its hunt is sent into it without its planner being asked (planner.sent_in). Live
on 2026-10-19 the plans said "hold at the western edge, no entry yet" and listed all five as going, and the five sent
in by the list walked into the lich. huddle.go_now_question puts the plan to system one: does it send the party into
the fight now? This puts the plans the huddles of the lich scene agreed (the world store's party_board, 2026-10-11 to
2026-10-19, each once) to it, with what is wanted written before any model saw them (brain/bench/2026-10-19-plans.json),
beside the list alone.

  uv run python scripts/plan_check.py --out bench/2026-10-19-plans-asked.json

The first set is the plans from before the plan named who goes (2026-10-11 to 13, the question was settled on it);
the second, those that name who goes (2026-10-14 and 19), run once after.
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


async def ask(judge: Any, board: dict[str, Any]) -> Any:
    for attempt in range(3):
        try:
            with costs.kind("order"):
                return await judge.ask(huddle.plan_state(board), {"go_now": huddle.go_now_question("Britain Graveyard")})
        except Exception:  # noqa: BLE001
            await asyncio.sleep(1 + attempt)
    return None


def score(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    out = {}
    for which in ("first", "second"):
        rs = [r for r in rows if r["set"] == which]
        out[which] = {"right": f"{sum(r[key] == r['want'] for r in rs)}/{len(rs)}",
                      "sent_in_when_it_holds": sum(r[key] and not r["want"] for r in rs),
                      "held_when_it_sends": sum(not r[key] and r["want"] for r in rs)}
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="jev,decision1")
    ap.add_argument("--sets", default="first,second")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    judges.load_dotenv(BRAIN / ".env")
    data = json.loads((BRAIN / "bench" / "2026-10-19-plans.json").read_text())
    plans = [{**p, "set": "second" if p["going"] is not None else "first"} for p in data["plans"]]
    plans = [p for p in plans if p["set"] in args.sets.split(",")]
    rows = [{"i": i, "set": p["set"], "want": p["want"], "target": p["target"][:160], "list": bool(p["going"])}
            for i, p in enumerate(plans)]
    result: dict[str, Any] = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "cut": huddle.GO_NOW_CUT,
                              "the_list": score([r for r in rows if r["set"] == "second"] + [{**r, "list": False} for r in rows if r["set"] == "first"], "list"),
                              "models": {}}
    for name in [m.strip() for m in args.models.split(",") if m.strip()]:
        judge = judges.make("jev", model=None if name == "jev" else name)
        try:
            answers = []
            for i in range(0, len(plans), 6):
                answers += await asyncio.gather(*(ask(judge, p) for p in plans[i:i + 6]))
            got = []
            for r, a in zip(rows, answers):
                if a is None:
                    continue
                p = float(a.nouls.get("go_now", 0.0))
                got.append({**r, "p": round(p, 3), "model": p >= huddle.GO_NOW_CUT, "latency_ms": a.latency_ms, "cost": a.cost})
            s = score(got, "model")
            s["wrong"] = [{k: r[k] for k in ("set", "i", "want", "p", "target")} for r in got if r["model"] != r["want"]]
            s["latency_ms"] = round(statistics.median(r["latency_ms"] for r in got)) if got else None
            s["cost_usd_a_plan"] = round(statistics.mean(r["cost"] for r in got), 7) if got else None
            result["models"][name] = s
        finally:
            await judge.close()
    if args.out:
        args.out.write_text(json.dumps(result, indent=1))
    print("the list alone:", json.dumps(result["the_list"]))
    for name, s in result["models"].items():
        print(f"{name}: " + json.dumps({k: v for k, v in s.items() if k in ("first", "second")}) +
              f"; {s['latency_ms']} ms, ${s['cost_usd_a_plan']} a plan")
        for w in s["wrong"]:
            print(f"    {w['set']} #{w['i']} wanted {w['want']} p {w['p']}: {w['target']}")


if __name__ == "__main__":
    asyncio.run(main())
