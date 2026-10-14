"""Where a companion is sent, and whether it goes, put to system one without the game (cuo-cym.19).

A companion told to go somewhere without the leader (companion.py) asks two things in one request once the
order is read: which of the places it knows is meant (`place_question`: the nearest of each kind in the world
store, as the options), and whether it agrees to go now (`willing_question`: a yes/no on the moment, since going
is its own to agree to). This puts lines and moments to both, on each model.

  uv run python scripts/go_check.py --out bench/2026-10-13-go.json

The places are those a companion standing south of the Britain graveyard is offered (the local world store).
- places: lines that send it somewhere, with the kind of place meant, or "unclear" where none is said. Two
  sets: the first was written before any model saw it, and the place question was then reworded on it once
  (15 of 20 right on Decision 1 before, 17 after: a place named only in the talk before the line, a place of the
  same kind in another town); the second was written after that and run once.
- willing: the same request to go to the inn, in moments where a companion would go (all quiet; asked again
  after a refusal) and where it wouldn't (badly hurt, in a fight, hostile creatures close, the leader near
  death). Written before any model saw them; the question was not changed after.
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

# (the line, the kind of place meant or "unclear", the talk before it)
PLACES: list[tuple[str, str, list[str]]] = [
    ("Right - off to the inn for you all - see you there", "inn", []),
    ("Go on ahead to the bank, I'll catch you up.", "bank", []),
    ("Wait for me at the stables.", "stable", []),
    ("You lot head to the tavern, I'll be along.", "tavern", []),
    ("Off to the healer with you.", "healer", []),
    ("Meet me at the smith's.", "blacksmith", []),
    ("go get a drink, I'll find you", "tavern", []),
    ("Get some rest, I'll see you at the inn.", "inn", []),
    ("Head back to town and wait for me.", "town", []),
    ("Go and wait by the moongate.", "moongate", []),
    ("Off you go then", "inn", ["Roland: Right, who wants to go off to the inn?", "Thea: Sounds good."]),
    ("I'll follow you", "inn", ["Roland: Can you go off to the inn? I'll meet you there in a minute."]),
    ("yes, there", "bank", ["Roland: Go and wait for me at the bank.", "Isolde: The west one?"]),
    ("Go on ahead, I'll catch up.", "unclear", []),
    ("Off you go.", "unclear", []),
    ("Wait for me at the castle.", "unclear", []),
    ("See you in Yew.", "unclear", []),
    ("you guys go to the inn, brb", "inn", []),
    ("I need the bank. You lot go to the inn.", "inn", []),
    ("Go see the healer about that arm, I'll wait at the bank.", "healer", []),
]

# Written after the place question was reworded on the lines above, and run once.
PLACES_HELD: list[tuple[str, str, list[str]]] = [
    ("ok off to the bank with you, I'll be there in a bit", "bank", []),
    ("take yourselves to the inn", "inn", []),
    ("Meet me at the tailor's.", "tailor", []),
    ("Go on, I'll be right behind you.", "healer", ["Roland: You should all go and see the healer.", "Wystan: It's a scratch."]),
    ("Wait at the alchemist's while I sort this out.", "alchemist", []),
    ("off you trot", "unclear", []),
    ("I'm going to the bank, you go to the healer.", "healer", []),
    ("see you at the inn in Skara Brae", "unclear", []),
    ("That one.", "bank", ["Roland: Go and wait for me somewhere.", "Thea: Where? The bank?"]),
    ("Bowyer's. Go. I'll catch up.", "bowyer", []),
]

QUIET = {"own_health": "100%", "leader": "health 100%, 2 tiles away", "in_a_fight": False, "hostile_creatures_close": 0}
# (what the moment is, the moment, refused before, the talk before, whether it should go)
MOMENTS: list[tuple[str, dict[str, Any], int, list[str], bool]] = [
    ("all quiet", QUIET, 0, [], True),
    ("all quiet, the leader a little hurt", {**QUIET, "leader": "health 80%, 2 tiles away"}, 0, [], True),
    ("all quiet, itself a little hurt", {**QUIET, "own_health": "75%"}, 0, [], True),
    ("after a fight, nothing about", {**QUIET, "own_health": "90%", "leader": "health 85%, 3 tiles away"}, 0, [], True),
    ("badly hurt itself", {**QUIET, "own_health": "20%"}, 0, [], False),
    ("the leader near death", {**QUIET, "leader": "health 15%, 2 tiles away"}, 0, [], False),
    ("in a fight", {**QUIET, "in_a_fight": True, "hostile_creatures_close": 2, "own_health": "70%"}, 0, [], False),
    ("three hostile creatures close", {**QUIET, "hostile_creatures_close": 3}, 0, [], False),
    ("the leader in a fight alone", {**QUIET, "leader": "health 50%, 6 tiles away", "hostile_creatures_close": 1}, 0, [], False),
    ("asked again after a refusal, all quiet now", QUIET, 1,
     ["Roland: Off to the inn with you, I'll meet you there.", "Isolde: Not while you're bleeding, Roland."], True),
    ("asked again with a reason, one creature far off", {**QUIET, "hostile_creatures_close": 1}, 1,
     ["Roland: Off to the inn with you.", "Isolde: And leave you with that thing? No.",
      "Roland: It's one skeleton. I can manage."], True),
    ("asked a third time, the leader hurt", {**QUIET, "leader": "health 45%, 2 tiles away"}, 2,
     ["Roland: Go to the inn.", "Isolde: You're hurt. No.", "Roland: I said go, I'm fine."], True),
]
ASK = "Right - off to the inn for you all - see you there"


async def ask(judge: Any, state: dict[str, Any], qs: dict[str, Any]) -> Any:
    for attempt in range(3):
        try:
            with costs.kind("order"):
                return await judge.ask(state, qs)
        except Exception:  # noqa: BLE001
            await asyncio.sleep(1 + attempt)
    return None


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
    places = companion.places_near(world, HERE)
    world.close()
    result: dict[str, Any] = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "here": list(HERE),
                              "places_offered": {p["kind"]: f"{p['name']}, {p['tiles']} tiles {p['way']}" for p in places},
                              "models": {}}
    base = {"party": {"leader": LEADER, "companions": PARTY}, "companion": ME, "who": who, "doing_now": f"following {LEADER}"}
    for name in [m.strip() for m in args.models.split(",") if m.strip()]:
        judge = judges.make("jev", model=None if name == "jev" else name)
        try:
            qs = {"place": companion.place_question(ME, LEADER, places), "willing": companion.willing_question(ME, LEADER)}
            rows = []
            for line, want, before in [*PLACES, *PLACES_HELD]:
                a = await ask(judge, {**base, "moment": QUIET, "refused_before": 0, "said_before": before, "line": line}, qs)
                if a is None:
                    continue
                c = a.choices["place"]
                p = c.probabilities.get(c.choice, c.confidence)
                got = c.choice if p >= companion.PLACE_CUT and c.choice != "unclear" else "unclear"
                rows.append({"line": line, "want": want, "got": got, "p": round(p, 3), "ok": got == want,
                             "set": "second" if (line, want, before) in PLACES_HELD else "first",
                             "latency_ms": a.latency_ms, "cost": a.cost,
                             "top": {k: round(v, 2) for k, v in sorted(c.probabilities.items(), key=lambda kv: -kv[1])[:3]}})
            will = []
            for what, moment, refused, before, want in MOMENTS:
                a = await ask(judge, {**base, "moment": moment, "refused_before": refused, "said_before": before, "line": ASK}, qs)
                if a is None:
                    continue
                p = float(a.nouls.get("willing", 0.0))
                will.append({"moment": what, "want": want, "p": round(p, 3), "ok": (p >= companion.WILLING_CUT) == want})
            lat = [r["latency_ms"] for r in rows]
            result["models"][name] = {
                "places_right": {k: f"{sum(r['ok'] for r in rows if r['set'] == k)}/{sum(1 for r in rows if r['set'] == k)}"
                                 for k in ("first", "second")},
                "sent_to_a_wrong_place": sum(1 for r in rows if not r["ok"] and r["got"] != "unclear"),
                "couldnt_tell_where_it_was_said": sum(1 for r in rows if not r["ok"] and r["got"] == "unclear"),
                "willing_right": f"{sum(w['ok'] for w in will)}/{len(will)}",
                "latency_ms": round(statistics.median(lat)) if lat else None,
                "cost_usd_a_request": round(statistics.mean(r["cost"] for r in rows), 7) if rows else None,
                "places": rows, "willing": will}
            if args.out:
                args.out.write_text(json.dumps(result, indent=1))
        finally:
            await judge.close()
    for name, s in result["models"].items():
        print(f"\n{name}: places {s['places_right']} (a wrong place {s['sent_to_a_wrong_place']}, couldn't tell "
              f"{s['couldnt_tell_where_it_was_said']}), willing {s['willing_right']}; {s['latency_ms']} ms, "
              f"${s['cost_usd_a_request']} a request")
        for r in s["places"]:
            if not r["ok"]:
                print(f"    {r['set']} set, {r['line']!r}: wanted {r['want']}, got {r['got']}  {r['top']}")
        for w in s["willing"]:
            print(f"    {'ok ' if w['ok'] else 'NOT'} {w['moment']}: {w['p']} (wanted {'go' if w['want'] else 'no'})")
    print("\nplaces offered:", json.dumps(result["places_offered"], indent=1))
    if args.out:
        print(f"results: {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
