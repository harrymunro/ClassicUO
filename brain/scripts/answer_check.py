"""Whether a companion answers the person who leads it, put to system one without the game (cuo-cym.16).

Talk here is two steps (talk.py): system one answers a yes/no (kind `chat`: should this character say something
now?), and on a yes the cheap planner model writes the line. The yes/no was written for agents talking to
agents, where saying nothing is the safe answer. In a party for the player the one speaking is a person, and in
the first live run nobody answered any of the four things it said to the party (the yes/no gave 0.07 to
0.41). This puts a set of the leader's lines to the yes/no as each of three companions would ask it (Isolde,
Wystan and Thea of fleets/party-player-talk.toml, led by Roland), with the question as it was and as it is for a
leader a person plays (talk.talk_question's `person`), on each model:

  uv run python scripts/answer_check.py --out bench/2026-10-13-answer.json
  uv run python scripts/answer_check.py --models jev,decision1

The lines, by group, and what is wanted of each:
- to the party: said to nobody by name and wanting an answer. Somebody should say yes (one answer is given,
  by whoever claims it first), so the count is of lines with at least one yes among the three.
- by name: said to one companion, who alone is asked (the others are left out by name, in code). A yes.
- nothing to answer: "brb", "lol", "one sec". No yes from anyone.
- following on: a line with no name in it, said straight after one companion's answer to the leader ("And
  your reagents?"). A yes from that one and from no other: the yes/no is told whose answer it follows
  (talk.said_to_words), and who the line is for is its to judge. Added after the first run of the rest.
- to the others: straight after one companion's answer too, and plainly not for that one ("What did the rest of
  you make of it?"). No yes from that one, and a yes from another. Added after a live run in which the one who
  had just answered took such a line and the other two left it.
- an order: each companion has already said its word on taking it. Reported, not counted: either is fine.
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

from uo_brain import companion, costs, fleet, talk  # noqa: E402
from uo_brain import judge as judges  # noqa: E402

LEADER = "Roland"
CUT = 0.5  # talk.Ears.consider

# (group, the line, the companion it names or None, the lines said just before it)
LINES: list[tuple[str, str, str | None, list[str]]] = [
    ("following on", "And your reagents?", None,
     ["Roland: Isolde, how are you holding up?", "Isolde: Bored. Find me something that burns."]),
    ("following on", "Enough for another fight?", None,
     ["Roland: Wystan, how many arrows have you left?", "Wystan: Forty, and every one a kill."]),
    ("following on", "What does he eat, anyway?", None, ["Roland: Thea, how's Bramble?", "Thea: Hungry. He's always hungry."]),
    ("following on", "Why north?", None, ["Roland: Which way now, do you think?", "Wystan: North. More bones, more glory."]),
    ("following on", "Modest as ever.", None, ["Roland: Isolde, nice Fireball.", "Isolde: I know."]),
    ("following on", "You'd say that if it had eaten you.", None,
     ["Roland: What did you all make of that bone knight?", "Thea: Bramble's seen worse."]),

    ("to the others", "What did the rest of you make of it?", None,
     ["Roland: Isolde, what did you make of that bone knight?", "Isolde: It took three Fireballs and a prayer."]),
    ("to the others", "Anyone else?", None, ["Roland: Wystan, how many arrows have you left?", "Wystan: Forty, and every one a kill."]),
    ("to the others", "And you two?", None, ["Roland: Thea, how are you holding up?", "Thea: Fine. Bramble's hungry."]),
    ("to the others", "What about the others, how are your supplies?", None,
     ["Roland: Wystan, how many arrows have you left?", "Wystan: Forty, and every one a kill."]),
    ("to the others", "Right, come along, you lot.", None,
     ["Roland: Isolde, how are you holding up?", "Isolde: Bored. Find me something that burns."]),

    ("to the party", "You'd have stayed in bed if I'd let you.", None, []),
    ("to the party", "That was a good fight. Well done, all of you.", None, []),
    ("to the party", "What did you all make of that bone knight?", None, []),
    ("to the party", "Anyone hurt?", None, []),
    ("to the party", "Which way now, do you think?", None, []),
    ("to the party", "I nearly died back there, you know.", None, []),
    ("to the party", "Who's got bandages to spare?", None, []),
    ("to the party", "Ready for another one?", None, []),
    ("to the party", "I've never seen so many skeletons in one place.", None, []),
    ("to the party", "Good evening, all.", None, []),
    ("to the party", "So what brought you lot out here in the first place?", None, []),
    ("to the party", "This graveyard gives me the creeps.", None, []),

    ("by name", "Isolde, how are you holding up?", "Isolde", []),
    ("by name", "Wystan, how many arrows have you left?", "Wystan", []),
    ("by name", "Thea, how's Bramble?", "Thea", []),
    ("by name", "Isolde, nice Fireball.", "Isolde", []),
    ("by name", "Wystan, run back to town and buy me some bandages, would you?", "Wystan", []),
    ("by name", "Thea, what's your story anyway?", "Thea", []),
    ("by name", "Isolde, and what did you make of that bone knight?", "Isolde",
     ["Roland: Isolde, how are you holding up?", "Isolde: Bored. Find me something that burns."]),
    ("by name", "Wystan, you call that a shot?", "Wystan", []),

    ("nothing to answer", "brb", None, []),
    ("nothing to answer", "lol", None, []),
    ("nothing to answer", "ok", None, []),
    ("nothing to answer", "one sec", None, []),
    ("nothing to answer", "hmm", None, []),
    ("nothing to answer", "afk a minute", None, []),
    ("nothing to answer", "right", None, []),
    ("nothing to answer", "oops, wrong key", None, []),

    ("an order", "Hold up a moment, I'll scout ahead.", None, []),
    ("an order", "All clear. Come along, you lot.", None, []),
    ("an order", "Right, on we go. Keep up.", None, []),
]


# Who a line is for (cuo-cym.21): one question with the party as its options (companion.for_question), asked before
# the yes/no, in place of the names found in the line and the hint of whose answer it follows. A line for several
# is then answered by each of them. (group, the line, who it is for, the lines said just before it.)
ALL = ("Isolde", "Thea", "Wystan")
PLAYS = {"Isolde": "mage", "Wystan": "archer", "Thea": "tamer"}  # as each reports itself to the party (the fleet file's kits)
MORE: list[tuple[str, str, tuple[str, ...], list[str]]] = [
    ("to the party", "So crew how are you all today?", ALL, []),
    ("to the party", "So, what do you all fancy doing next?", ALL, []),
    ("to the party", "Shall we head to the graveyard? Yay or nay?", ALL, []),
    ("to the party", "Wystan shot it first, I saw it.", ALL, []),
    ("by description", "Our archer, how many arrows have you left?", ("Wystan",), []),
    ("by description", "And what does the bear's mother think?", ("Thea",), []),
    ("by description", "Scholar, what is that thing?", ("Isolde",), []),
    ("two of them", "Thea, Wystan, are you two ready?", ("Thea", "Wystan"), []),
    ("two of them", "You two, how are your supplies?", ("Thea", "Wystan"),
     ["Roland: Isolde, how are your reagents?", "Isolde: Counted twice. Plenty."]),
    ("nobody", "bank", (), []),
    ("nobody", "vendor buy", (), []),
    ("nobody", "all kill", (), []),
    # Asked for what none of them does on command: it is talk to them, and wants an answer all the same (a no).
    # Added after two live runs in which "cover me" got one answer at 0.50 and then none (0.27 and 0.29): the
    # yes/no's wording had nothing to say of a request.
    ("a request", "cover me", ALL, []),
    ("a request", "heal me", ALL, []),
    ("a request", "Isolde, kill that one for me.", ("Isolde",), []),
    ("a request", "someone go and buy me some reagents", ALL, []),
]
# Written after the question was settled on the lines above and those of LINES, and run once.
HELD: list[tuple[str, str, tuple[str, ...] | None, list[str]]] = [
    ("to the party", "everyone alright?", ALL, []),
    ("to the party", "what do we think, left or right?", ALL, []),
    ("to the party", "Thoughts on that fight?", ALL, []),
    ("to the party", "who's hungry", ALL, []),
    ("to the party", "morning all", ALL, []),
    ("to the party", "Any of you ever been to Yew?", ALL, []),
    ("by name", "isolde you ok?", ("Isolde",), []),
    ("by name", "What about you, Thea?", ("Thea",), ["Roland: Wystan, ever been to Yew?", "Wystan: Born there. Left in a hurry."]),
    ("by description", "Hey archer, save some for the rest of us.", ("Wystan",), []),
    ("by description", "What does our mage make of this?", ("Isolde",), []),
    ("by description", "Tamer, keep that bear off my boots.", ("Thea",), []),
    ("following on", "How many is that now?", ("Isolde",), ["Roland: Isolde, still keeping your tally?", "Isolde: Naturally. I'm winning."]),
    ("following on", "Really? All of them?", ("Wystan",), ["Roland: Nice shooting back there.", "Wystan: Every arrow a kill, as usual."]),
    ("following on", "Does he bite?", ("Thea",), ["Roland: Thea, how's the bear?", "Thea: Bramble's sulking. No honey."]),
    ("two of them", "And the rest of you?", ("Thea", "Wystan"), ["Roland: Isolde, ready for another fight?", "Isolde: Always."]),
    ("two of them", "Isolde, Thea - what do you two make of it?", ("Isolde", "Thea"), []),
    ("two of them", "Not you, Wystan. The other two: any bandages left?", ("Isolde", "Thea"), []),
    ("two of them", "what about you others", ("Isolde", "Wystan"), ["Roland: Thea, are you hurt?", "Thea: A scratch."]),
    ("nobody", "buy", (), []),
    ("nobody", "guards!", (), []),
    ("nobody", "withdraw 500", (), []),
    ("nothing to answer", "sec, phone", None, []),
    ("nothing to answer", "k", None, []),
    ("nothing to answer", "haha", None, []),
]


def for_of(group: str, named: str | None, before: list[str]) -> tuple[str, ...] | None:
    """Who a line of LINES is for, by its group; None where that isn't counted ("brb" is for nobody or for all)."""
    after = before[-1].split(":")[0] if before else ""
    return {"by name": (named,), "following on": (after,), "to the others": tuple(n for n in ALL if n != after),
            "to the party": ALL, "an order": ALL}.get(group)


async def owes(judge: Any, me: str, names: list[str], line: str, before: list[str]) -> str:
    """What a companion's own reading of the line leaves it owing an answer for (companion.Companion.interpret):
    "asked" for a request that is none of its orders, "unsure" for one that was nearly an order, else ""."""
    qs = {"order": companion.order_question(me, LEADER), "asked": companion.asked_question(me, LEADER)}
    state = companion.order_state(line, me, LEADER, [n for n in names if n != me], f"following {LEADER}", before)
    for attempt in range(3):
        try:
            with costs.kind("order"):
                a = await judge.ask(state, qs)
            p = a.choices["order"].probabilities
            return "" if companion.order_in(p)[0] else companion.left_over(p, float(a.nouls.get("asked", 0.0)))
        except Exception:  # noqa: BLE001
            await asyncio.sleep(1 + attempt)
    return ""


async def judged(judge: Any, personas: dict[str, dict[str, str]], lines: list[tuple]) -> list[dict[str, Any]]:
    """Each line as the companions now take it: who it is for, by one question the three share, then the yes/no of
    each it is for, told who that is; and what each one's own reading of it leaves it owing an answer for (a
    request it doesn't do at a word), which it answers whatever the yes/no says."""
    names = sorted(personas)
    out = []
    for i in range(0, len(lines), 6):
        async def one(item: tuple) -> dict[str, Any]:
            group, line, to, before = item
            who = companion.for_question(LEADER, names, PLAYS)
            p: dict[str, float] = {}
            for attempt in range(3):
                try:
                    with costs.kind("order"):
                        a = await judge.ask(companion.for_state(line, LEADER, names, before), {"for": who})
                    p = a.choices["for"].probabilities
                    break
                except Exception:  # noqa: BLE001
                    await asyncio.sleep(1 + attempt)
            whom = companion.for_whom(p, names)
            got = [n for n in names if whom[n] >= companion.FOR_CUT]
            yes: dict[str, float] = {}
            for me in got:
                state = {"who": f"{me}: " + "; ".join(f"{k} {v}" for k, v in personas[me].items() if k != "name"),
                         "doing": f"with {LEADER}", "fighting": False, "conversation": [*before, f"{LEADER}: {line}"],
                         "why_now": "answer",
                         "said_to": talk.said_to_words(me, LEADER, got, "", [n for n in names if n != me], judged=True)}
                r = await ask(judge, {"state": state, "questions": {"talk": talk.talk_question(me, LEADER, person=True)}})
                yes[me] = r.get("p", 0.0)
            # ...which only those the line is for are left owing (companion.Companion.interpret).
            owed = {n: "" for n in names} | dict(zip(got, await asyncio.gather(
                *(owes(judge, me, names, line, before) for me in got))))
            return {"group": group, "line": line, "want": list(to) if to is not None else None, "for": got,
                    "for_p": {k: round(v, 2) for k, v in p.items() if v >= 0.05}, "yes": {k: round(v, 2) for k, v in yes.items()},
                    "owed": {k: v for k, v in owed.items() if v},
                    "answer": [n for n in names if yes.get(n, 0.0) >= CUT or owed[n]]}
        out += await asyncio.gather(*(one(item) for item in lines[i:i + 6]))
    return out


def judged_score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """By group: lines read as for whom they are for; lines answered by everyone they are for and nobody else;
    and, to set beside the numbers of the yes/no alone, lines "as wanted" by that count (somebody answers a line
    to the party; the one meant and no other answers one that follows on; nobody answers "brb")."""
    out: dict[str, Any] = {}
    for group in dict.fromkeys(r["group"] for r in rows):
        mine = [r for r in rows if r["group"] == group]
        counted = [r for r in mine if r["want"] is not None]
        loose = 0
        for r in mine:
            want, got = set(r["want"] or ()), set(r["answer"])
            loose += (not got if group in ("nothing to answer", "nobody") else bool(got) and got <= want)
        out[group] = {"lines": len(mine),
                      "for_right": sum(1 for r in counted if sorted(r["for"]) == sorted(r["want"])) if counted else None,
                      "answered_by_each_it_is_for": sum(1 for r in counted if sorted(r["answer"]) == sorted(r["want"]))
                      if counted else None,
                      "as_wanted": loose,
                      "owed_an_answer": sum(len(r["owed"]) for r in mine),
                      "by_line": {r["line"]: {"for": r["for"], "answer": r["answer"], "for_p": r["for_p"], "yes": r["yes"],
                                              **({"owed": r["owed"]} if r["owed"] else {})} for r in mine}}
    scored = [g for g in out if g != "an order"]
    out["as_wanted"] = f"{sum(out[g]['as_wanted'] for g in scored)}/{sum(out[g]['lines'] for g in scored)}"
    both = [g for g in scored if out[g]["for_right"] is not None]
    out["for_right"] = f"{sum(out[g]['for_right'] for g in both)}/{sum(out[g]['lines'] for g in both)}"
    out["answered_by_each_it_is_for"] = (f"{sum(out[g]['answered_by_each_it_is_for'] for g in both)}/"
                                         f"{sum(out[g]['lines'] for g in both)}")
    return out


def requests(personas: dict[str, dict[str, str]], person: bool) -> list[dict[str, Any]]:
    """Each line as each companion's yes/no would be asked: the state talk.Ears.consider builds, and the question."""
    out = []
    names = sorted(personas)
    for group, line, named, before in LINES:
        for me in names:
            if named and named != me:
                continue  # a line that names another and not this one isn't considered (talk.Ears.hear)
            others = [n for n in names if n != me]
            # Whose answer it follows: the one who spoke last, straight after the leader (talk.Ears.hear).
            after = before[-1].split(":")[0] if group in ("following on", "to the others") else ""
            said_to = talk.said_to_words(me, LEADER, [named] if named else [], after, others)
            state = {"who": f"{me}: " + "; ".join(f"{k} {v}" for k, v in personas[me].items() if k != "name"),
                     "doing": f"with {LEADER}", "fighting": False,
                     "conversation": [*before, f"{LEADER}: {line}"], "why_now": "answer", "said_to": said_to}
            out.append({"group": group, "line": line, "who": me, "after": after,
                        "state": state, "questions": {"talk": talk.talk_question(me, LEADER, person=person)}})
    return out


async def ask(judge: Any, req: dict[str, Any]) -> dict[str, Any]:
    err = ""
    for attempt in range(3):
        try:
            with costs.kind("chat"):
                a = await judge.ask(req["state"], req["questions"])
            return {"p": a.nouls["talk"], "latency_ms": a.latency_ms, "cost": a.cost}
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"[:300]
            await asyncio.sleep(1 + attempt)
    return {"error": err}


def score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    lines: dict[tuple[str, str], list[tuple[str, float]]] = {}
    after = {r["line"]: r["after"] for r in rows if r.get("after")}
    for r in rows:
        lines.setdefault((r["group"], r["line"]), []).append((r["who"], r["p"]))
    out: dict[str, Any] = {}
    for group in dict.fromkeys(g for g, _ in lines):
        mine = {line: ps for (g, line), ps in lines.items() if g == group}
        yes = {line: [w for w, p in ps if p >= CUT] for line, ps in mine.items()}
        ok = [line for line, who in yes.items() if (not who if group == "nothing to answer" else
                                                    who == [after[line]] if group == "following on" else
                                                    bool(who) and after[line] not in who if group == "to the others"
                                                    else bool(who))]
        out[group] = {"lines": len(mine), "as_wanted": len(ok) if group != "an order" else None,
                      "lines_with_a_yes": sum(1 for who in yes.values() if who),
                      "yes_of_all_asked": f"{sum(len(w) for w in yes.values())}/{sum(len(ps) for ps in mine.values())}",
                      "mean_p": round(statistics.mean(p for ps in mine.values() for _, p in ps), 3),
                      "by_line": {line: {w: round(p, 2) for w, p in ps} for line, ps in mine.items()}}
    counted = [g for g in out if g != "an order"]
    out["as_wanted"] = f"{sum(out[g]['as_wanted'] for g in counted)}/{sum(out[g]['lines'] for g in counted)}"
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="jev,decision1")
    ap.add_argument("--fleet", type=Path, default=BRAIN / "fleets" / "party-player-talk.toml")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--judged", default="the same lines,more lines,lines held back",
                    help="which sets to run with who a line is for judged first ('' for none)")
    ap.add_argument("--no-yes-no", action="store_true", help="skip the yes/no alone, as it was measured on 2026-10-13")
    args = ap.parse_args()
    judges.load_dotenv(BRAIN / ".env")
    personas = {a.name: a.persona for a in fleet.load(args.fleet).agents}
    result: dict[str, Any] = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "cut": CUT, "lines": len(LINES), "models": {}}
    for name in [m.strip() for m in args.models.split(",") if m.strip()]:
        judge = judges.make("jev", model=None if name == "jev" else name)
        result["models"][name] = {}
        try:
            for label, person in () if args.no_yes_no else (("as it was", False), ("for a person who leads", True)):
                todo = requests(personas, person)
                got = []
                for i in range(0, len(todo), 6):
                    got += await asyncio.gather(*(ask(judge, r) for r in todo[i:i + 6]))
                rows = [{**r, **g} for r, g in zip(todo, got) if "error" not in g]
                s = score(rows)
                lat = [r["latency_ms"] for r in rows]
                s |= {"asked": len(rows), "errors": len(got) - len(rows),
                      "latency_ms_median": round(statistics.median(lat)) if lat else None,
                      "cost_usd_a_question": round(statistics.mean(r["cost"] for r in rows), 7) if rows else None}
                result["models"][name][label] = s
                if args.out:
                    args.out.write_text(json.dumps(result, indent=1))
            # ...and with who the line is for judged first, by the one question the three share (cuo-cym.21).
            sets = {"the same lines": [(g, line, for_of(g, named, before), before) for g, line, named, before in LINES],
                    "more lines": MORE, "lines held back": HELD}
            result.setdefault("judged", {})[name] = {}
            for label, lines in sets.items():
                if lines and label in args.judged.split(","):
                    rows = await judged(judge, personas, lines)
                    result["judged"][name][label] = judged_score(rows)
                    if args.out:
                        args.out.write_text(json.dumps(result, indent=1))
        finally:
            await judge.close()
    result["costs"] = costs.ledger.summary()
    if args.out:
        args.out.write_text(json.dumps(result, indent=1))
    for name, by in result.get("judged", {}).items():
        for label, s in by.items():
            print(f"\n{name}, who a line is for judged first, {label}: for whom right on {s['for_right']} lines, answered "
                  f"by each it is for on {s['answered_by_each_it_is_for']}, as wanted by the yes/no's count on {s['as_wanted']}")
            for group, g in s.items():
                if not isinstance(g, dict):
                    continue
                print(f"  {group}: {g['lines']} lines; for whom right {g['for_right']}, answered by each {g['answered_by_each_it_is_for']}, "
                      f"as wanted {g['as_wanted']}")
                for line, v in g["by_line"].items():
                    print(f"      {line!r}: for {v['for']} {v['for_p']}  answers {v['answer']} {v['yes']}"
                          + (f"  owed {v['owed']}" if v.get("owed") else ""))
    for name, by in result["models"].items():
        for label, s in by.items():
            print(f"\n{name}, the question {label}: {s['as_wanted']} lines as wanted "
                  f"({s['latency_ms_median']} ms, ${s['cost_usd_a_question']} a question)")
            for group in ("to the party", "by name", "following on", "to the others", "nothing to answer", "an order"):
                g = s[group]
                print(f"  {group}: {g['as_wanted']} of {g['lines']} lines as wanted; {g['lines_with_a_yes']} with a yes, "
                      f"{g['yes_of_all_asked']} of those asked said yes, mean {g['mean_p']}")
                for line, ps in g["by_line"].items():
                    print(f"      {line!r}: " + "  ".join(f"{w} {p}" for w, p in ps.items()))
    if args.out:
        print(f"\nresults: {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
