"""Two system-one models on the same recorded requests, offline (cuo-79x: Jev against Microsoft's Decision 1).

The judgment benchmark compares them live in fights. This puts the brain's other system-one calls to
both, from what the logs recorded, with no game: the fight decisions themselves (their state and
questions), the routine in-hunt calls, the fight-time facts, the planner's re-ranks of world
queries, the memory picks and the should-I-speak yes/no. Both models get each request at the same
moment, so neither answers a staler state.

  uv run python scripts/judge_compare.py --out bench/2026-10-10-decision1-offline.json
  uv run python scripts/judge_compare.py --a jev --b decision1 --fight 300 --routine 200 --facts 60 --rerank 40 --memory 40 --chat 40

For each kind it reports how often the two pick the same option (choices) or fall on the same side
of the cut code uses (yes/no questions), how far apart their probabilities sit (mean absolute
difference, each model's mean, and the correlation: a model can be as good and calibrated
differently, and the policy's cuts were tuned on Jev's numbers), latency, input tokens as each counts
them, and cost a request. The disagreements are kept in the output for reading.
"""

import argparse
import asyncio
import glob
import json
import random
import statistics
import sys
import time
from pathlib import Path
from typing import Any

BRAIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRAIN / "src"))

from uo_brain import costs, facts, memory as memories, questions, talk  # noqa: E402
from uo_brain import judge as judges  # noqa: E402
from uo_brain.world import World  # noqa: E402

# The cut code puts on each yes/no (policy.PolicyConfig, facts, memory, talk): which side an answer falls on
# is what changes behaviour. Anything else: 0.5.
CUTS = {"in_danger": 0.625, "leave_now": 0.425, "pull_back": 0.6, "stop_running": 0.6, "talk": 0.5}
KIND_CUTS = {"facts": 0.6, "memory": 0.5, "rerank": 0.5, "routine": 0.6}


def read(path: str) -> list[dict[str, Any]]:
    out = []
    try:
        for line in open(path, errors="replace"):
            if line.startswith("{"):
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return out


def recent_logs(days: float, pattern: str = "logs/**/*.jsonl") -> list[str]:
    since = time.time() - days * 86400
    return sorted(p for p in glob.glob(str(BRAIN / pattern), recursive=True) if Path(p).stat().st_mtime >= since)


def who_of(qs: dict[str, Any]) -> str:
    role = str(((qs.get("intent") or {}).get("instructions") or {}).get("role", ""))
    return next((w for w in ("mage", "archer", "tamer", "bard", "necromancer", "paladin") if f"for a {w}" in role
                 or f"for an {w}" in role), "warrior")


def gather_requests(args) -> dict[str, list[dict[str, Any]]]:
    """Requests of each kind from the logs: {"kind": [{"state", "questions", "from"}]}."""
    rnd = random.Random(args.seed)
    out: dict[str, list[dict[str, Any]]] = {k: [] for k in ("fight", "routine", "facts", "rerank", "memory", "chat")}
    files = recent_logs(args.days)
    rnd.shuffle(files)
    world = World.open(args.shard)
    want = {"fight": 2 * args.fight, "routine": 2 * args.routine, "facts": 2 * args.facts, "rerank": 2 * args.rerank,
            "memory": 2 * args.memory, "chat": 2 * args.chat}
    for path in files:
        if all(len(out[k]) >= n for k, n in want.items()) or Path(path).stat().st_size > 60_000_000:
            continue  # enough of every kind, or a soak's log too big to read for a few samples
        recs = read(path)
        if not recs:
            continue
        name = Path(path).name
        asked = [r for r in recs if r.get("type") == "decision" and isinstance(r.get("questions"), dict)]
        # A few fight decisions from each log, so no one run or character fills the sample; those with a
        # choice to make (something hostile in view) rather than the rests.
        live = [r for r in asked if r["state"].get("hostile_creatures")]
        for r in rnd.sample(live, min(len(live), args.per_log)):
            out["fight"].append({"state": r["state"], "questions": r["questions"], "from": name, "who": who_of(r["questions"])})
        for r in recs:
            if r.get("type") == "routine" and isinstance(r.get("questions"), dict):
                out["routine"].append({"state": r["state"], "questions": r["questions"], "from": name})
        # Fight-time facts: the facts scored then, against the state of the next decision in the same log.
        for i, r in enumerate(recs):
            if r.get("type") == "facts" and len(r.get("facts") or []) >= 3:
                nxt = next((d for d in recs[i + 1:i + 40] if d.get("type") == "decision" and isinstance(d.get("questions"), dict)), None)
                if nxt is None:
                    continue
                who = who_of(nxt["questions"])
                you = nxt["state"]["you"]
                state = {"character": f"a {who}", "health": you.get("health"), "supplies": you.get("supplies", ""),
                         "fighting": you.get("fighting"), "where": (r.get("areas") or ["open country"])[0],
                         "areas_around": (r.get("areas") or [])[1:],
                         "creatures_in_view": [questions.describe_hostile(h, who) for h in nxt["state"].get("hostile_creatures", [])] or ["none"],
                         "goal": r.get("goal") or "none: fighting whatever comes"}
                role = nxt["questions"]["intent"]["instructions"]["role"]
                qs = {f"f{j + 1}": {"type": "noul", "instructions": {
                    "role": role, "question": f"Would knowing this fact change what the {who} should do in the next minute?",
                    "fact": f["text"]}, "criteria": facts.fact_question(_Sit(who, role))("x")["criteria"]}
                    for j, f in enumerate(r["facts"][:facts.CHUNK])}
                out["facts"].append({"state": state, "questions": qs, "from": name})
        # The planner's re-ranks: the same query against the store as it is now.
        goal = next((str(r["messages"][0]["content"]).split("\n", 1)[0].removeprefix("The player's goal: ")
                     for r in recs if r.get("type") == "planner" and r.get("messages")), "")
        for r in recs:
            if r.get("type") == "rerank" and r.get("question") and len(out["rerank"]) < 4 * args.rerank:
                try:
                    found = world.call_tool(r["tool"], {**(r.get("args") or {}), "limit": 25})
                except Exception:  # noqa: BLE001 - a query the store no longer answers
                    continue
                if isinstance(found, list) and len(found) >= 3:
                    q = facts.rerank_question(r["question"], goal)
                    out["rerank"].append({"state": {"planner_question": r["question"], "player_goal": goal},
                                          "questions": {f"f{j + 1}": q(facts.result_words(r["tool"], x))
                                                        for j, x in enumerate(found[:facts.CHUNK])}, "from": name})
        # Memory picks: each planner moment with what its character remembered then.
        who = next((r.get("character") for r in recs if r.get("type") == "memories" and r.get("character")), None)
        if who and len(out["memory"]) < 4 * args.memory:
            mem = memories.Memory(world, who)
            for r in recs:
                if r.get("type") != "planner" or not r.get("messages") or len(r["messages"]) != 1:
                    continue
                user = str(r["messages"][0]["content"])
                head, sep, tail = user.partition("The character now: ")
                try:
                    situation = json.loads(tail.split("\n\n", 1)[0]) if sep else {}
                except ValueError:
                    continue
                steps = [ln.split(". ", 1)[-1] for ln in head.splitlines() if ln[:1].isdigit()][-3:]
                rows = memories.candidates(mem, (situation.get("position") or {}).get("area"), goal, steps,
                                           before=memories.stamp_of(r["t"]))
                if len(rows) >= 4:
                    q = memories.memory_question(goal)
                    out["memory"].append({
                        "state": {"player_goal": goal or "(none given)", "character_now": situation,
                                  "last_steps": [s[:300] for s in steps] or ["(nothing yet)"]},
                        "questions": {f"f{j + 1}": q(memories.words(m)[:memories.TEXT_CAP]) for j, m in enumerate(rows[:facts.CHUNK])},
                        "from": name})
        # The should-I-speak yes/no: rebuilt from the lines heard and said before each time it was asked.
        lines: list[str] = []
        me = Path(path).stem
        for r in recs:
            if r.get("type") != "talk":
                continue
            if r.get("heard"):
                lines.append(f"{r['who']}: {r['text']}")
            elif r.get("said"):
                lines.append(f"{me}: {r['text']}")
            friend = r.get("considered") or (r.get("to") if r.get("said") else None)
            if friend and "p" in r:
                before = lines[:-1] if r.get("said") else lines
                out["chat"].append({"state": {"who": me, "doing": "nothing in particular", "fighting": False,
                                              "conversation": before[-8:] or ["(they haven't spoken yet)"],
                                              "why_now": r.get("why") or "answer", "said_to": "no one by name"},
                                    "questions": {"talk": talk.talk_question(me, friend)}, "from": name,
                                    "logged_p": r["p"]})
    world.close()
    limits = {"fight": args.fight, "routine": args.routine, "facts": args.facts, "rerank": args.rerank,
              "memory": args.memory, "chat": args.chat}
    return {k: rnd.sample(v, min(len(v), limits[k])) for k, v in out.items()}


class _Sit:
    """Just enough of a Situation for the questions' wording."""

    def __init__(self, who: str, role: str):
        self.is_tamer = who == "tamer"
        self.is_necromancer = who == "necromancer"
        self.is_mage = who in ("mage", "necromancer")
        self.is_archer, self.is_bard, self.is_paladin = who == "archer", who == "bard", who == "paladin"
        self.is_warrior_mage = False
        self.archetype = who


async def ask(judge: Any, req: dict[str, Any], kind: str) -> dict[str, Any]:
    for attempt in range(3):
        try:
            with costs.kind("replay"):
                a = await judge.ask(req["state"], req["questions"])
            return {"choices": {k: (v.choice, v.confidence, v.probabilities) for k, v in a.choices.items()},
                    "nouls": dict(a.nouls), "scores": dict(a.scores), "latency_ms": a.latency_ms,
                    "input_tokens": a.input_tokens, "cost": a.cost, "model": a.model}
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"[:300]
            await asyncio.sleep(1 + attempt)
    return {"error": err}


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or statistics.pstdev(xs) == 0 or statistics.pstdev(ys) == 0:
        return None
    return round(statistics.correlation(xs, ys), 3)


def summarise(kind: str, rows: list[dict[str, Any]], a_name: str, b_name: str) -> dict[str, Any]:
    ok = [r for r in rows if "error" not in r["a"] and "error" not in r["b"]]
    out: dict[str, Any] = {"requests": len(rows), "answered_by_both": len(ok),
                           "errors": {a_name: sum("error" in r["a"] for r in rows), b_name: sum("error" in r["b"] for r in rows)}}
    if not ok:
        return out
    for label, key in ((a_name, "a"), (b_name, "b")):
        lat = sorted(r[key]["latency_ms"] for r in ok)
        out[label] = {"latency_ms_avg": round(statistics.fmean(lat), 1), "latency_ms_p95": round(lat[int(0.95 * (len(lat) - 1))], 1),
                      "input_tokens_avg": round(statistics.fmean(r[key]["input_tokens"] for r in ok)),
                      "input_tokens_max": max(r[key]["input_tokens"] for r in ok),
                      "cost_per_request_usd": round(statistics.fmean(r[key]["cost"] for r in ok), 7),
                      "model": ok[0][key]["model"]}
    choices: dict[str, dict[str, Any]] = {}
    nouls: dict[str, dict[str, list[float]]] = {}
    differ = []
    for r in ok:
        for q, (pick, conf, probs) in r["a"]["choices"].items():
            if q not in r["b"]["choices"]:
                continue
            bp, bconf, bprobs = r["b"]["choices"][q]
            c = choices.setdefault(q, {"n": 0, "same": 0, "conf_a": [], "conf_b": [], "pairs": {}})
            c["n"] += 1
            c["same"] += pick == bp
            c["conf_a"].append(conf)
            c["conf_b"].append(bconf)
            if pick != bp:
                c["pairs"][f"{pick}->{bp}"] = c["pairs"].get(f"{pick}->{bp}", 0) + 1
                differ.append({"question": q, a_name: {"pick": pick, "p": round(conf, 2)}, b_name: {"pick": bp, "p": round(bconf, 2)},
                               "options": r["req"]["questions"][q].get("criteria"), "state": r["req"]["state"], "from": r["req"]["from"]})
        for q, p in r["a"]["nouls"].items():
            if q not in r["b"]["nouls"]:
                continue
            base = "take" if q.startswith("take_") else "f" if q[:1] == "f" and q[1:].isdigit() else \
                "go" if q.startswith("go_") else q
            n = nouls.setdefault(base, {"a": [], "b": []})
            n["a"].append(p)
            n["b"].append(r["b"]["nouls"][q])
            cut = CUTS.get(base, KIND_CUTS.get(kind, 0.5))
            if (p >= cut) != (r["b"]["nouls"][q] >= cut) and len(differ) < 400:
                differ.append({"question": q, "cut": cut, a_name: round(p, 2), b_name: round(r["b"]["nouls"][q], 2),
                               "asked": r["req"]["questions"][q]["instructions"], "state": r["req"]["state"], "from": r["req"]["from"]})
    out["choices"] = {q: {"n": c["n"], "same_pick": round(c["same"] / c["n"], 3),
                          f"confidence_avg_{a_name}": round(statistics.fmean(c["conf_a"]), 3),
                          f"confidence_avg_{b_name}": round(statistics.fmean(c["conf_b"]), 3),
                          "differ": dict(sorted(c["pairs"].items(), key=lambda kv: -kv[1])[:8])}
                      for q, c in choices.items()}
    out["yes_no"] = {}
    for q, n in nouls.items():
        cut = CUTS.get(q, KIND_CUTS.get(kind, 0.5))
        a, b = n["a"], n["b"]
        out["yes_no"][q] = {"n": len(a), "cut": cut,
                            "same_side_of_cut": round(sum((x >= cut) == (y >= cut) for x, y in zip(a, b)) / len(a), 3),
                            f"yes_rate_{a_name}": round(sum(x >= cut for x in a) / len(a), 3),
                            f"yes_rate_{b_name}": round(sum(y >= cut for y in b) / len(b), 3),
                            f"mean_{a_name}": round(statistics.fmean(a), 3), f"mean_{b_name}": round(statistics.fmean(b), 3),
                            "mean_abs_difference": round(statistics.fmean(abs(x - y) for x, y in zip(a, b)), 3),
                            "correlation": pearson(a, b)}
    # For the kinds that rank a list (facts, re-ranks, memories): do they put the same ones on top?
    if kind in ("facts", "rerank", "memory"):
        top_same, top3 = 0, []
        for r in ok:
            a = sorted(r["a"]["nouls"], key=lambda k: -r["a"]["nouls"][k])
            b = sorted(r["b"]["nouls"], key=lambda k: -r["b"]["nouls"].get(k, 0))
            if a and b:
                top_same += a[0] == b[0]
                top3.append(len(set(a[:3]) & set(b[:3])) / min(3, len(a)))
        out["ranking"] = {"same_top_one": round(top_same / len(ok), 3), "top_three_overlap": round(statistics.fmean(top3), 3)}
    out["disagreements"] = differ[:60]
    out["disagreements_total"] = len(differ)
    return out


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--a", default="jev")
    ap.add_argument("--b", default="decision1")
    ap.add_argument("--out", type=Path, default=BRAIN / "bench" / f"{time.strftime('%Y-%m-%d')}-judge-compare.json")
    ap.add_argument("--days", type=float, default=4.0, help="logs changed in the last this many days")
    ap.add_argument("--shard", default="local")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--per-log", type=int, default=3, help="fight decisions sampled from each log")
    for kind, n in (("fight", 300), ("routine", 200), ("facts", 60), ("rerank", 40), ("memory", 40), ("chat", 40)):
        ap.add_argument(f"--{kind}", type=int, default=n)
    args = ap.parse_args()

    judges.load_dotenv(BRAIN / ".env")
    a, b = judges.make("jev", model=None if args.a == "jev" else args.a), judges.make("jev", model=args.b)
    a_name, b_name = args.a, args.b
    reqs = gather_requests(args)
    print({k: len(v) for k, v in reqs.items()}, file=sys.stderr)
    result: dict[str, Any] = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "a": a_name, "b": b_name,
                              "judges": {a_name: a.name, b_name: b.name}, "kinds": {}}
    try:
        for kind, items in reqs.items():
            rows = []
            for i in range(0, len(items), 4):  # four requests at a time, each to both models at once
                batch = items[i:i + 4]
                got = await asyncio.gather(*(asyncio.gather(ask(a, r, kind), ask(b, r, kind)) for r in batch))
                rows += [{"req": r, "a": x, "b": y} for r, (x, y) in zip(batch, got)]
            result["kinds"][kind] = summarise(kind, rows, a_name, b_name)
            print(kind, json.dumps({k: v for k, v in result["kinds"][kind].items() if k != "disagreements"})[:900], file=sys.stderr)
            args.out.write_text(json.dumps(result, indent=1))
    finally:
        await a.close()
        await b.close()
    result["costs"] = costs.ledger.summary()
    args.out.write_text(json.dumps(result, indent=1))
    print(f"results: {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
