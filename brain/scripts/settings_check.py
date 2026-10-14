"""The leader's word for a companion's settings, read without the game (cuo-cym.18).

A companion of a character a person plays changes how it plays when the leader says so (standing.py): what it
loots, whether it runs, how much it talks. Two steps, both system one's: on every line the leader says, beside
the order question, which of this companion's settings does the line speak to, if any (one choice with the
settings as its options); and for each setting it names, that setting's own question (what does the line make
of it, or does it leave it as it is). This puts a set of lines to both steps as each of three companions would
ask them (Isolde the mage, Wystan the archer and Thea the tamer, led by Roland) and counts the readings that
come out as written below.

  uv run python scripts/settings_check.py --out bench/2026-10-13-settings.json
  uv run python scripts/settings_check.py --models jev,decision1 --sets first

Two sets of lines. The first was written with the questions, and the two cuts (standing.WHICH_CUT,
standing.SET_CUT) were chosen on it. The second was written after that and run once: its numbers are the ones
to go by.

The lines, by group: one for each setting (looting, fleeing, ...), `several` (a line that changes more than
one), and `none`: talk, an order, a request for one thing now ("heal me"), which change nothing.

For each model: readings right by group; settings missed (the line changes one and it wasn't changed), split
into those the first step didn't name and those the setting's own question left as they were; changes nobody
asked for; wrong values; the same totals at other cuts; what a reading costs and takes. To count at other cuts
the setting's question is put for every setting the first step gives 0.1 or more, which is more than a companion
asks. Every wrong reading is kept in the output.
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

from uo_brain import companion, costs, standing  # noqa: E402
from uo_brain import judge as judges  # noqa: E402

LEADER = "Roland"
PARTY = ["Isolde", "Wystan", "Thea"]
CASTERS = {"Isolde"}
LOOT = ("valuables", "everything")  # either will do where the line doesn't say which
ASKED_FROM = 0.1  # the setting's question is put from this share of the first step, to count at cuts under the one in use

# (group, the line, what it sets: for all, or by name with "*" for the rest; a tuple of values means any of them)
FIRST: list[tuple[str, str, dict[str, Any]]] = [
    ("looting", "feel free to loot", {"looting": "valuables"}),
    ("looting", "loot", {"looting": "valuables"}),
    ("looting", "Please go ahead and loot", {"looting": "valuables"}),
    ("looting", "Leave the rest.", {"looting": "nothing"}),
    ("looting", "Hands off the corpses, that one's mine.", {"looting": "nothing"}),
    ("looting", "Take everything, don't leave a scrap.", {"looting": "everything"}),
    ("looting", "Help yourselves to whatever they drop.", {"looting": LOOT}),
    ("looting", "Wystan, you loot; the rest of you keep off.", {"Wystan": {"looting": "valuables"}, "*": {"looting": "nothing"}}),
    ("looting", "Stop looting, Thea.", {"Thea": {"looting": "nothing"}, "*": {}}),

    ("fleeing", "Nobody runs. We stand and fight.", {"fleeing": "never"}),
    ("fleeing", "If it goes bad, run. Don't die for me.", {"fleeing": "allowed"}),
    ("fleeing", "No retreat, Wystan.", {"Wystan": {"fleeing": "never"}, "*": {}}),

    ("aggression", "Play it safe from here on.", {"aggression": "cautious"}),
    ("aggression", "Go all out, hold nothing back.", {"aggression": "relentless"}),

    ("target", "Always kill the weakest first.", {"target": "weakest_first"}),
    ("target", "From now on, biggest threat first.", {"target": "strongest_first"}),
    ("target", "Finish what you start before you switch.", {"target": "current_first"}),

    ("spell", "Isolde, open with Explosion every time.", {"Isolde": {"opening_spell": "Explosion"}, "*": {}}),
    ("spell", "Isolde, stick to Lightning once it's going.", {"Isolde": {"main_spell": "Lightning"}, "*": {}}),

    ("helping", "Don't waste your bandages on the rest of us.", {"helping": "off"}),
    ("helping", "Heal each other, that's what you're for.", {"helping": "on"}),
    ("helping", "Isolde, save your mana, no healing.", {"Isolde": {"helping": "off"}, "*": {}}),

    ("raising", "If someone drops, get them back up.", {"raising": "on"}),
    ("raising", "Don't bother raising anyone who falls.", {"raising": "off"}),

    ("callouts", "Stop shouting in fights, all of you.", {"callouts": "off"}),
    ("callouts", "Call it out when you're hurt.", {"callouts": "on"}),

    ("talk", "Quiet, all of you.", {"talk": "silent"}),
    ("talk", "You can talk again.", {"talk": ("chatty", "answers")}),
    ("talk", "Less chatter, please.", {"talk": ("answers", "silent")}),
    ("talk", "Shut up, Wystan.", {"Wystan": {"talk": "silent"}, "*": {}}),

    ("distance", "Give me some room, you're crowding me.", {"distance": "loose"}),
    ("distance", "Stay tight on me from now on.", {"distance": "close"}),

    ("reach", "Don't go chasing things. Only what attacks us.", {"reach": "defend"}),
    ("reach", "Kill everything you see.", {"reach": "wide"}),

    ("several", "Loot what you like, and no running.", {"looting": LOOT, "fleeing": "never"}),
    ("several", "Isolde, open with Explosion and leave the loot.", {"Isolde": {"opening_spell": "Explosion", "looting": "nothing"},
                                                                    "*": {}}),

    # Said when the setting has already been changed, with the talk that followed the change: the live state. Added
    # after the first live run (2026-10-13 22:48), where "Leave the rest." after "feel free to loot" was read as
    # "take what is worth having [and leave the rest]" by all three (0.55 to 0.64), which they were doing.
    ("after a change", "Leave the rest.", {"looting": "nothing"}, {"looting": "valuables"},
     ["Roland: feel free to loot", "Thea: Fine. I'll pick the corpses clean now.", "Wystan: I'll take what's worth having."]),
    ("after a change", "ok that's enough looting", {"looting": "nothing"}, {"looting": "valuables"},
     ["Roland: feel free to loot", "Isolde: Looting it is, Roland."]),
    ("after a change", "You can talk again.", {"talk": ("chatty", "answers")}, {"talk": "silent"},
     ["Roland: Quiet, all of you.", "Wystan: Not another word."]),
    ("after a change", "ok, you can run if you have to", {"fleeing": "allowed"}, {"fleeing": "never"},
     ["Roland: Nobody runs. We stand and fight.", "Thea: No running. Understood."]),
    ("after a change", "loot", {"looting": "valuables"}, {"looting": "valuables"}, []),

    ("none", "Nice loot on that one.", {}),
    ("none", "Did you loot the lich?", {}),
    ("none", "I never run from a fight, me.", {}),
    ("none", "Follow me.", {}),
    ("none", "Stay here.", {}),
    ("none", "Heal me!", {}),
    ("none", "cover me", {}),
    ("none", "That was a quiet one.", {}),
    ("none", "Off to the inn with you, see you there.", {}),
    ("none", "Who's got the gold?", {}),
    ("none", "Isolde, nice Fireball.", {}),
    ("none", "The weakest ones always die first.", {}),
    ("none", "So crew how are you all today?", {}),
    ("none", "here's the plan...", {}),
    ("none", "Shall we head to the graveyard? Yay or nay?", {}),
    ("none", "lol", {}),
]

# The second set: written after the first had been run and the questions settled, and run once.
SECOND: list[tuple[str, str, dict[str, Any]]] = [
    ("looting", "go on, grab the loot", {"looting": LOOT}),
    ("looting", "you can have whatever's on them", {"looting": LOOT}),
    ("looting", "nobody touches the loot but me", {"looting": "nothing"}),
    ("looting", "loot it all, I don't care", {"looting": "everything"}),
    ("looting", "Thea, you can loot now.", {"Thea": {"looting": LOOT}, "*": {}}),
    ("looting", "stop taking my kills' loot, all of you", {"looting": "nothing"}),
    ("looting", "ok stop looting", {"looting": "nothing"}),

    ("fleeing", "don't you dare run", {"fleeing": "never"}),
    ("fleeing", "we fight to the death here", {"fleeing": "never"}),
    ("fleeing", "if I go down, get out", {"fleeing": "allowed"}),
    ("fleeing", "Isolde, if it turns ugly you run, hear me?", {"Isolde": {"fleeing": "allowed"}, "*": {}}),

    ("aggression", "easy does it from now on", {"aggression": "cautious"}),
    ("aggression", "be careful in there, pull back early", {"aggression": "cautious"}),
    ("aggression", "no mercy, hit them with everything", {"aggression": "relentless"}),

    ("target", "pick off the hurt ones first", {"target": "weakest_first"}),
    ("target", "nearest one first, always", {"target": "closest_first"}),
    ("target", "go for the big one before the rest, every time", {"target": "strongest_first"}),
    ("target", "don't switch targets mid-fight", {"target": "current_first"}),

    ("spell", "Isolde, start every fight with Flamestrike.", {"Isolde": {"opening_spell": "Flamestrike"}, "*": {}}),
    ("spell", "Isolde, just Fireball them once it's started.", {"Isolde": {"main_spell": "Fireball"}, "*": {}}),

    ("helping", "keep each other patched up", {"helping": "on"}),
    ("helping", "no more healing the others, look after yourselves", {"helping": "off"}),
    ("helping", "Wystan, stop bandaging people.", {"Wystan": {"helping": "off"}, "*": {}}),

    ("raising", "anyone who dies, bring them back", {"raising": "on"}),
    ("raising", "if someone dies they can walk to the healer", {"raising": "off"}),

    ("callouts", "shout if something's on you", {"callouts": "on"}),
    ("callouts", "no need to yell every time you get hit", {"callouts": "off"}),

    ("talk", "silence!", {"talk": "silent"}),
    ("talk", "enough talking", {"talk": ("silent", "answers")}),
    ("talk", "ok you can chat", {"talk": ("chatty", "answers")}),
    ("talk", "Isolde, not another word out of you.", {"Isolde": {"talk": "silent"}, "*": {}}),
    ("talk", "only speak when I ask you something", {"talk": "answers"}),

    ("distance", "back off a bit, I can't see", {"distance": "loose"}),
    ("distance", "keep right next to me", {"distance": "close"}),

    ("reach", "leave the ones that aren't bothering us", {"reach": "defend"}),
    ("reach", "clear this whole place out", {"reach": "wide"}),

    ("several", "No looting and no running, understood?", {"looting": "nothing", "fleeing": "never"}),
    ("several", "Stay close and stay quiet.", {"distance": "close", "talk": "silent"}),

    ("none", "that skeleton hit like a cart", {}),
    ("none", "who looted the bone knight?", {}),
    ("none", "quiet night, isn't it", {}),
    ("none", "Wystan never shuts up, does he", {}),
    ("none", "run for it!", {}),
    ("none", "I always go for the biggest one", {}),
    ("none", "heal pls", {}),
    ("none", "sit down, drinks on me", {}),
    ("none", "wait here", {}),
    ("none", "nice and safe, that one", {}),
    ("none", "everything dies eventually", {}),
    ("none", "brb", {}),
    ("none", "right, on we go", {}),
    ("none", "Thea, how's Bramble?", {}),
]


def wanted(want: dict[str, Any], who: str) -> dict[str, Any]:
    by_name = any(k in PARTY or k == "*" for k in want)
    mine = want.get(who, want.get("*", {})) if by_name else want
    return {k: v for k, v in mine.items() if who in CASTERS or not standing.SETTINGS[k].casters}


def cases(lines: list[tuple]) -> list[dict[str, Any]]:
    """A fourth field is the settings as the leader has left them when the line is said (`now`), and a fifth the
    talk just before it."""
    return [{"group": group, "line": line, "who": who, "want": wanted(want, who), "now": more[0] if more else {},
             "before": more[1] if len(more) > 1 else []}
            for group, line, want, *more in lines for who in PARTY]


async def ask(judge: Any, case: dict[str, Any]) -> dict[str, Any]:
    """As a companion reads it: which settings the line speaks to, beside the order question, and then those
    settings' own questions."""
    who = case["who"]
    others = [n for n in PARTY if n != who]
    settings = standing.Standing(caster=who in CASTERS)
    settings.values.update(case["now"])
    first = {"order": companion.order_question(who, LEADER), "setting": standing.which_question(who, LEADER, who in CASTERS)}
    err = ""
    for attempt in range(3):
        try:
            with costs.kind("order"):
                a = await judge.ask(companion.order_state(case["line"], who, LEADER, others, f"following {LEADER}",
                                                          case["before"]), first)
            which = {k: round(v, 3) for k, v in a.choices["setting"].probabilities.items()}
            order = a.choices["order"]
            out = {"which": which, "order": order.choice, "order_p": order.probabilities.get(order.choice, order.confidence),
                   "latency_ms": a.latency_ms, "cost": a.cost, "model": a.model, "picks": {}}
            names = settings.spoken_to(which, ASKED_FROM)
            if names:
                with costs.kind("order"):
                    b = await judge.ask(settings.state(case["line"], who, LEADER, others, case["before"], names),
                                        settings.questions(who, LEADER, names))
                out["picks"] = {k: {"choice": c.choice, "p": round(c.probabilities.get(c.choice, c.confidence), 3)}
                                for k, c in b.choices.items() if c.choice != standing.UNCHANGED}
                if any(which[k] >= standing.WHICH_CUT for k in names):
                    n = len(names)  # what a companion would have asked: only those at the cut in use
                    asked = sum(1 for k in names if which[k] >= standing.WHICH_CUT)
                    out |= {"latency2_ms": b.latency_ms, "cost2": b.cost * asked / n, "input_tokens2": b.input_tokens * asked / n}
            return out
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"[:300]
            await asyncio.sleep(1 + attempt)
    return {"error": err}


def ok(want: dict[str, Any], got: dict[str, str]) -> bool:
    return set(want) == set(got) and all(got[k] in (v if isinstance(v, tuple) else (v,)) for k, v in want.items())


def score(rows: list[dict[str, Any]], which_cut: float, set_cut: float) -> dict[str, Any]:
    groups: dict[str, list[int]] = {}
    not_named = left = unasked = wrong_value = order_too = 0
    bad = []
    for r in rows:
        named = [k for k, p in r["which"].items() if k != standing.NONE and p >= which_cut]
        got = {k: v["choice"] for k, v in r["picks"].items() if k in named and v["p"] >= set_cut}
        right = ok(r["want"], got)
        g = groups.setdefault(r["group"], [0, 0])
        g[0] += right
        g[1] += 1
        order_too += bool(r["want"]) and r["order"] != "none" and r["order_p"] >= companion.ORDER_CUT
        if not right:
            missing = [k for k in r["want"] if k not in got]
            not_named += any(k not in named for k in missing)
            left += any(k in named for k in missing)
            unasked += any(k not in r["want"] for k in got)
            wrong_value += any(k in got and got[k] not in (v if isinstance(v, tuple) else (v,)) for k, v in r["want"].items())
            bad.append({"group": r["group"], "line": r["line"], "who": r["who"], "want": {k: list(v) if isinstance(v, tuple)
                        else v for k, v in r["want"].items()}, "got": got,
                        "which": {k: p for k, p in r["which"].items() if p >= 0.05}, "picks": r["picks"]})
    right = sum(g[0] for g in groups.values())
    return {"right": right, "of": len(rows), "pct": round(100 * right / max(1, len(rows)), 1),
            "by_group": {k: f"{a}/{b}" for k, (a, b) in groups.items()},
            "missed_not_named": not_named, "missed_left_as_it_was": left, "changes_nobody_asked_for": unasked,
            "wrong_values": wrong_value, "also_read_as_an_order": order_too, "wrong": bad}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="jev,decision1", help="aliases or model ids, comma-separated")
    ap.add_argument("--which-cut", type=float, default=standing.WHICH_CUT)
    ap.add_argument("--set-cut", type=float, default=standing.SET_CUT)
    ap.add_argument("--sets", default="first,second")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    judges.load_dotenv(BRAIN / ".env")
    sets = {k: cases(v) for k, v in (("first", FIRST), ("second", SECOND)) if k in args.sets.split(",") and v}
    result: dict[str, Any] = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "which_cut": args.which_cut, "set_cut": args.set_cut,
                              "lines": {k: len(v) // len(PARTY) for k, v in sets.items()}, "models": {}}
    keys = ("right", "missed_not_named", "missed_left_as_it_was", "changes_nobody_asked_for", "wrong_values")
    for name in [m.strip() for m in args.models.split(",") if m.strip()]:
        judge = judges.make("jev", model=None if name == "jev" else name)
        result["models"][name] = {}
        try:
            for which, todo in sets.items():
                got = []
                for i in range(0, len(todo), 6):  # six readings at a time
                    got += await asyncio.gather(*(ask(judge, c) for c in todo[i:i + 6]))
                rows = [{**c, **g} for c, g in zip(todo, got) if "error" not in g]
                errors = [g["error"] for g in got if "error" in g]
                s = score(rows, args.which_cut, args.set_cut)
                s["by_cut"] = {f"{w}/{c}": {k: v for k, v in score(rows, w, c).items() if k in keys}
                               for w in (0.15, 0.2, 0.25, 0.3, 0.4) for c in (0.5, 0.6, 0.7)}
                lat = sorted(r["latency_ms"] for r in rows)
                second = [r for r in rows if "latency2_ms" in r]
                s |= {"judge": judge.name, "served_by": sorted({r["model"] for r in rows}),
                      "latency_ms": {"the_first_step": round(statistics.median(lat)) if lat else None,
                                     "the_settings": round(statistics.median(r["latency2_ms"] for r in second)) if second else None},
                      "cost_usd": {"the_first_step_with_the_order": round(statistics.mean(r["cost"] for r in rows), 7) if rows else None,
                                   "the_settings": round(statistics.mean(r["cost2"] for r in second), 7) if second else None},
                      "readings_that_went_on_to_the_settings": len(second),
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
            print(f"\n{name}, {which} set: {s['right']}/{s['of']} right ({s['pct']}%)  missed: not named "
                  f"{s['missed_not_named']}, left as it was {s['missed_left_as_it_was']}; changes nobody asked for "
                  f"{s['changes_nobody_asked_for']}, wrong values {s['wrong_values']}, also read as an order "
                  f"{s['also_read_as_an_order']}")
            print("  " + "  ".join(f"{k} {v}" for k, v in s["by_group"].items()))
            print("  right (changes nobody asked for) by cuts, which/set: " + "  ".join(
                f"{c}: {v['right']} ({v['changes_nobody_asked_for']})" for c, v in s["by_cut"].items()))
            print(f"  {s['latency_ms']} ms, ${s['cost_usd']}, {s['readings_that_went_on_to_the_settings']} of {s['of']} readings "
                  f"went on to the settings, {s['errors']} errors")
            for b in s["wrong"]:
                print(f"    {b['group']:10} {b['who']:7} {b['line']!r}: wanted {b['want']}, got {b['got']}  "
                      f"(which {b['which']}, picks {b['picks']})")
    if args.out:
        print(f"\nresults: {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
