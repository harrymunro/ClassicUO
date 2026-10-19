"""Sent somewhere by name, among several places, on an errand, or one of several: put to system one without the game
(cuo-cym.27).

A companion sent off without the leader (companion.Companion.send_off) asks, in one request, where it is sent
(`place_question`: the nearest place of each kind, and now the places the line names by their own names,
`named_places`), what it is sent to do there (`errand_question`, with `goods_question` and `count_question` for a
buy), and whether just one of the companions is asked (`one_of_question`). This puts written lines to each, on each
model, beside what the phrases did before: the nearest of the kind the line's word names (`place_of`), no errand
ever (it went and waited), and everyone going.

  uv run python scripts/errand_check.py --out bench/2026-10-19-errands.json

Four cases, each with a first set and a held-back set. Both were written before any model saw them; the first set
is the one the wording may be changed on, the second is run once after that. The places are those a companion
standing south of the Britain graveyard (1392,1528) knows in the local world store.
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

from uo_brain import companion, costs, fleet  # noqa: E402
from uo_brain import judge as judges  # noqa: E402
from uo_brain.world import World  # noqa: E402

LEADER, ME = "Roland", "Isolde"
PARTY = ["Isolde", "Thea", "Wystan"]
HERE = (1392, 1528)
QUIET = {"own_health": "100%", "leader": "health 100%, 2 tiles away", "in_a_fight": False, "hostile_creatures_close": 0}

# Where: (the line, what is wanted: a kind of place, "name:<words in the place's name or how it is said>", or
# "unclear"). The first set is named places; then lines with several places.
NAMED = [
    ("Meet me at the east bank.", "name:east britain bank"),
    ("Go wait at the west bank, I'll be along.", "name:west britain bank"),
    ("Off to the inn in Skara Brae with you.", "name:skara brae"),
    ("See you in Yew.", "name:yew"),
    ("Head to Vesper and wait for me there.", "name:vesper"),
    ("Wait for me at the East Britain inn.", "name:east britain inn"),
    ("Off to Cove with you.", "name:cove"),
    ("Go to the inn.", "inn"),
]
NAMED_HELD = [
    ("Get yourself to the west bank.", "name:west britain bank"),
    ("I'll see you in Trinsic.", "name:trinsic"),
    ("wait at the east inn", "name:east britain inn"),
    ("Go to the healer in Vesper.", "name:vesper"),
    ("Meet me in Moonglow.", "name:moonglow"),
    ("go to the bank", "bank"),
    ("See you in Narnia.", "unclear"),
]
# Written after the place question was reworded on the first sets (the second sets had been run once with the first
# wording), before any model saw them, and run once.
NAMED_THIRD = [
    ("Go and wait for me at the west inn.", "inn"),  # the store knows no west inn near: the nearest inn
    ("See you in Minoc.", "name:minoc"),
    ("Head over to the east bank and wait.", "name:east britain bank"),
    ("Meet me at Jhelom's docks.", "name:jhelom"),
    ("go wait at the healer", "healer"),
    ("Off to Vesper with you, I'll catch you up.", "name:vesper"),
]
SEVERAL_THIRD = [
    ("I'll get the drinks at the inn, you go to the bank.", "bank"),
    ("Thea, the healer. Isolde, the stables. Wystan, with me.", "stable"),
    ("We're going to the bank; Isolde, you wait at the inn.", "inn"),
    ("I'm off to the smith's, you lot head to the healer's.", "healer"),
]
SEVERAL = [
    ("I'm going to the bank, you go to the healer.", "healer"),
    ("Isolde, go to the inn; Wystan, the bank.", "inn"),
    ("Wystan, go to the inn; Isolde, the bank.", "bank"),
    ("I'll check the bank, you head to the inn.", "inn"),
    ("You go to the healer while I sell this at the smith's.", "healer"),
    ("Thea and Wystan to the inn, Isolde to the bank.", "bank"),
]
SEVERAL_HELD = [
    ("I'm off to the healer. You lot wait at the inn.", "inn"),
    ("Isolde to the tailor's, the rest of you to the stables.", "tailor"),
    ("Wystan, bank. Isolde, healer.", "healer"),
    ("The rest of you go to the inn; Wystan, come with me.", "inn"),
    ("I'm going to the inn. Isolde, wait for me at the bank.", "bank"),
]
# What it is sent to do: (the line, the errand, what to buy, how many: a COUNTS key or "unsaid").
ERRANDS = [
    ("Go and get me some bandages from the healer.", "buy", "bandage", "unsaid"),
    ("Run to the healer and buy 10 bandages, then come back.", "buy", "bandage", "10"),
    ("Can you fetch 50 arrows from the bowyer?", "buy", "arrow", "50"),
    ("Go bank this gold for me and come back.", "bank", None, None),
    ("Take the loot to the smith and sell it.", "sell", None, None),
    ("Pick up a few heal potions while you're in town.", "buy", "heal potion", "unsaid"),
    ("Off to the inn with you, I'll meet you there.", "go", None, None),
    ("Go and stock up on reagents.", "buy", "reagents", "unsaid"),
    ("Wait for me at the bank.", "go", None, None),
    ("Get me 20 cure potions from the alchemist.", "buy", "cure potion", "20"),
]
ERRANDS_HELD = [
    ("nip to the healer and grab us 30 bandages", "buy", "bandage", "30"),
    ("go sell your loot then come find me", "sell", None, None),
    ("Deposit your gold at the bank, then meet me at the graveyard.", "bank", None, None),
    ("head to town and wait", "go", None, None),
    ("Fetch me five heal potions.", "buy", "heal potion", "5"),
    ("Go get some bolts for your crossbow.", "buy", "bolt", "unsaid"),
    ("Meet me at the inn.", "go", None, None),
    ("Could you go and buy 100 arrows?", "buy", "arrow", "100"),
]
# Just one of them, whichever? (the line, wanted)
ONE_OF = [
    ("One of you go and find a healer.", True),
    ("Someone fetch me bandages.", True),
    ("Can somebody run to the bank for me?", True),
    ("You lot head to the inn.", False),
    ("Isolde, go to the bank.", False),
    ("All of you, off to the inn.", False),
]
ONE_OF_HELD = [
    ("Anyone fancy running to the bank for me?", True),
    ("one of you nip to the healer for bandages", True),
    ("The rest of you go to the inn.", False),
    ("Wystan and Isolde, wait at the bank.", False),
    ("Whoever's quickest, go get arrows.", True),
    ("Go to the inn, all of you.", False),
]


async def ask(judge: Any, state: dict[str, Any], qs: dict[str, Any]) -> Any:
    for attempt in range(3):
        try:
            with costs.kind("order"):
                return await judge.ask(state, qs)
        except Exception:  # noqa: BLE001
            await asyncio.sleep(1 + attempt)
    return None


def placed(want: str, option: dict[str, Any] | None) -> bool:
    if option is None:
        return want == "unclear"
    if want.startswith("name:"):
        words = want[5:]
        return option["kind"].startswith("named") and (words in option["name"].lower() or words in option["says"].lower())
    return option["kind"] == want


def by_phrases(line: str, places: list[dict[str, Any]]) -> dict[str, Any] | None:
    """What the phrases did: the nearest of the kind the line's word names."""
    kind = companion.place_of(line)
    return next((p for p in places if p["kind"] == kind), None)


def tally(rows: list[dict[str, Any]], key: str = "ok") -> dict[str, str]:
    return {k: f"{sum(r[key] for r in rows if r['set'] == k)}/{sum(1 for r in rows if r['set'] == k)}"
            for k in ("first", "second", "third") if any(r["set"] == k for r in rows)}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="jev,decision1")
    ap.add_argument("--fleet", type=Path, default=BRAIN / "fleets" / "party-player-talk.toml")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    judges.load_dotenv(BRAIN / ".env")
    personas = {a.name: a.persona for a in fleet.load(args.fleet).agents}
    who = "; ".join(f"{k} {v}" for k, v in personas[ME].items() if k != "name")
    world = World.open("local")
    kinds = companion.places_near(world, HERE)
    offered = {line: kinds + companion.named_places(world, [line], HERE)
               for line, *_ in [*NAMED, *NAMED_HELD, *NAMED_THIRD, *SEVERAL, *SEVERAL_HELD, *SEVERAL_THIRD, *ERRANDS,
                                *ERRANDS_HELD, *ONE_OF, *ONE_OF_HELD]}
    world.close()
    held = {x[0] for x in [*NAMED_HELD, *SEVERAL_HELD, *ERRANDS_HELD, *ONE_OF_HELD]}
    third = {x[0] for x in [*NAMED_THIRD, *SEVERAL_THIRD]}

    def which(line: str) -> str:
        return "third" if line in third else "second" if line in held else "first"

    base = {"party": {"leader": LEADER, "companions": PARTY}, "companion": ME, "who": who, "doing_now": f"following {LEADER}",
            "moment": QUIET, "refused_before": 0, "said_before": []}
    result: dict[str, Any] = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "here": list(HERE),
                              "named_offered": {line: [p["says"] for p in v if p["kind"].startswith("named")]
                                                for line, v in offered.items() if any(p["kind"].startswith("named") for p in v)},
                              "phrases": {}, "models": {}}
    # What the phrases did, as the baseline.
    ph_places = [{"set": which(line), "case": case, "line": line, "want": want, "ok": placed(want, by_phrases(line, offered[line]))}
                 for case, lines in (("named", NAMED + NAMED_HELD + NAMED_THIRD), ("several", SEVERAL + SEVERAL_HELD + SEVERAL_THIRD))
                 for line, want in lines]
    result["phrases"] = {
        "named": tally([r for r in ph_places if r["case"] == "named"]),
        "several": tally([r for r in ph_places if r["case"] == "several"]),
        "errands (none ever)": tally([{"set": "second" if x[0] in held else "first", "ok": x[1] == "go"} for x in ERRANDS + ERRANDS_HELD]),
        "one of (all go)": tally([{"set": "second" if x[0] in held else "first", "ok": not x[1]} for x in ONE_OF + ONE_OF_HELD])}
    for name in [m.strip() for m in args.models.split(",") if m.strip()]:
        judge = judges.make("jev", model=None if name == "jev" else name)
        rows: list[dict[str, Any]] = []
        try:
            jobs = []
            for case, lines in (("named", NAMED + NAMED_HELD + NAMED_THIRD), ("several", SEVERAL + SEVERAL_HELD + SEVERAL_THIRD),
                                ("errands", [(x[0], x) for x in ERRANDS + ERRANDS_HELD]),
                                ("one_of", [(x[0], x[1]) for x in ONE_OF + ONE_OF_HELD])):
                for line, want in lines:
                    places = offered[line]
                    qs = {"place": companion.place_question(ME, LEADER, places), "errand": companion.errand_question(ME, LEADER),
                          "goods": companion.goods_question(ME, LEADER), "count": companion.count_question(ME, LEADER),
                          "one_of": companion.one_of_question(ME, LEADER)}
                    jobs.append((case, line, want, places, ask(judge, {**base, "line": line}, qs)))
            answers = []
            for i in range(0, len(jobs), 6):
                answers += await asyncio.gather(*(j[4] for j in jobs[i:i + 6]))
            for (case, line, want, places, _), a in zip(jobs, answers):
                if a is None:
                    continue
                row: dict[str, Any] = {"case": case, "set": which(line), "line": line,
                                       "latency_ms": a.latency_ms, "cost": a.cost}
                if case in ("named", "several"):
                    c = a.choices["place"]
                    p = c.probabilities.get(c.choice, c.confidence)
                    option = next((x for x in places if x["kind"] == c.choice and p >= companion.PLACE_CUT), None)
                    row |= {"want": want, "got": option["says"] if option else "unclear", "p": round(p, 3), "ok": placed(want, option),
                            "top": {k: round(v, 2) for k, v in sorted(c.probabilities.items(), key=lambda kv: -kv[1])[:3]}}
                elif case == "errands":
                    _, errand, goods, count = want
                    job = companion.errand_of(a.choices, None)
                    got = job["kind"] if job else "go"
                    row |= {"want": [errand, goods, count], "got": [got, (job or {}).get("item"),
                                                                    a.choices["count"].choice if job and got == "buy" else None],
                            "errand_ok": got == errand,
                            "goods_ok": errand != "buy" or (job or {}).get("item") == goods,
                            "count_ok": errand != "buy" or a.choices["count"].choice == count,
                            "errand_p": {k: round(v, 2) for k, v in sorted(a.choices["errand"].probabilities.items(), key=lambda kv: -kv[1])[:2]}}
                    row["ok"] = row["errand_ok"] and row["goods_ok"]
                else:
                    p = float(a.nouls.get("one_of", 0.0))
                    row |= {"want": want, "p": round(p, 3), "ok": (p >= companion.ONE_OF_CUT) == want}
                rows.append(row)
            lat = [r["latency_ms"] for r in rows]
            result["models"][name] = {
                "named": tally([r for r in rows if r["case"] == "named"]),
                "several": tally([r for r in rows if r["case"] == "several"]),
                "errands (what, and what to buy)": tally([r for r in rows if r["case"] == "errands"]),
                "errands, how many right": tally([r for r in rows if r["case"] == "errands"], "count_ok"),
                "one of": tally([r for r in rows if r["case"] == "one_of"]),
                "latency_ms": round(statistics.median(lat)) if lat else None,
                "cost_usd_a_request": round(statistics.mean(r["cost"] for r in rows), 7) if rows else None,
                "rows": rows}
            if args.out:
                args.out.write_text(json.dumps(result, indent=1))
        finally:
            await judge.close()
    result["costs"] = costs.ledger.summary()
    if args.out:
        args.out.write_text(json.dumps(result, indent=1))
    print("phrases:", json.dumps(result["phrases"]))
    for name, s in result["models"].items():
        print(f"\n{name}: " + ", ".join(f"{k} {v}" for k, v in s.items() if k not in ("rows", "latency_ms", "cost_usd_a_request"))
              + f"; {s['latency_ms']} ms, ${s['cost_usd_a_request']} a request")
        for r in s["rows"]:
            if not r["ok"] or r["case"] == "errands" and not r["count_ok"]:
                print(f"    {r['set']} {r['case']}: {r['line']!r} wanted {r['want']}, got {r.get('got', r.get('p'))}"
                      f"  {r.get('top') or r.get('errand_p') or ''}")
    print("\nnamed places offered:", json.dumps(result["named_offered"], indent=1))
    if args.out:
        print(f"results: {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
