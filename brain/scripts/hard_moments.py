"""The planner's hard moments in recorded sessions, each put to several models (cuo-vpi).

A hard moment is a planning step that went to the stronger model (an `escalation` record in the
log, then the planner record with the messages it was sent), one where code had just refused to
go back where the character left, or the first step after a resurrection: alive again on little
health, with the corpse and everything on it still where it died.

  uv run python scripts/hard_moments.py list LOG...
  uv run python scripts/hard_moments.py ask LOG... --models haiku,sonnet,opus --out FILE
      [--cost-logs DIR] [--world COPY.sqlite] [--kinds escalated,refused,resurrected] [--limit N]
  uv run python scripts/hard_moments.py table FILE [--rows | --all] [--rescore]

`ask` plays each moment out as Planner.choose does, once per model: today's system prompt and
tools, the user turn as it was logged, and the world queries the model asks answered until it
names a goal. Offline, so three things differ from play: the queries are answered from a copy of
the shard's store as it is now, in code's own order (no Jev re-rank), and "seen recently" in an
answer counts from now, not from the moment; remember and long_goal are acknowledged and not
kept; recall is answered by keyword from what the character remembered then. Neither the game
nor the store itself is touched.

`table` counts, per model: steps that ended in a goal code can carry out, the first move by kind
of moment, the moves that go back to a place the situation warns about, tokens, cost and latency.
Whether a move is sensible is `verdict` below: rules, written down so the counts can be redone
(--rescore applies them again to saved answers). A move the rules leave open can be read by hand:
put `read` ("right", "wrong" or "open") and `read_why` on the answer in the file, and it is counted.
"""
import argparse
import asyncio
import json
import re
import sqlite3
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from uo_brain import costs, llm, longgoals, models
from uo_brain import memory as memories
from uo_brain import world as worlds
from uo_brain.facts import keywords
from uo_brain.planner import GOALS, MAX_TOKENS, SYSTEM, goal_tools, machine_tool

MAX_QUERIES = 6      # as Planner
RECALL_MANA = 11     # Recall is a fourth-circle spell: with less the character walks
SHORT_WAIT_MIN = 10  # a refusal with this little left to wait: resting it out is a fair answer
KINDS = ("escalated", "refused", "resurrected", "dead", "corpse")
STEP = re.compile(r"^(\d+)\. (\w+)\((.*?)\) -> (\{.*)$")
# Dollars a million tokens in, out (OpenRouter's list prices, 2026-10-10): for what a step costs
# when nothing is cached, as an escalation in play finds it (they come minutes apart).
LIST_PRICE = {"anthropic/claude-haiku-5.5": (0.10, 0.50), "anthropic/claude-sonnet-5.5": (2.0, 10.0),
              "anthropic/claude-opus-5.5": (4.0, 20.0)}


def read(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(errors="replace").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


def parse_user(user: str) -> tuple[dict, list[dict]]:
    """The situation and the numbered steps of a planner's user turn."""
    head, _, tail = user.partition("The character now: ")
    try:
        situation = json.loads(tail.split("\n\nChoose", 1)[0])
    except ValueError:
        situation = {}
    steps = []
    for line in head.splitlines():
        if m := STEP.match(line):
            ok = '"ok":true' in m[4][:12]
            result = re.search(r'"result":"(.*)', m[4])
            steps.append({"n": int(m[1]), "tool": m[2], "args": m[3], "ok": ok,
                          "result": result[1] if result else m[4], "line": line})
    return situation, steps


def health(situation: dict) -> float | None:
    m = re.match(r"(\d+)/(\d+)", str(situation.get("health", "")))
    return int(m[1]) / int(m[2]) if m and int(m[2]) else None


def moments(path: Path) -> list[dict]:
    """Every first call of a planning step in a log that is one of KINDS."""
    out, reason = [], None
    recs = read(path)
    who = next((r["character"] for r in recs if r.get("type") == "ai_cost" and r.get("character")), None)
    for i, r in enumerate(recs):
        if r.get("type") == "escalation":
            reason = r.get("reason")
            continue
        if r.get("type") != "planner" or not r.get("messages") or r.get("calls") is None or len(r["messages"]) != 1:
            continue
        situation, steps = parse_user(r["messages"][0]["content"])
        last = steps[-1] if steps else None
        if situation.get("dead"):
            kind = "dead"
        elif last and last["result"].startswith("not going back near "):
            kind = "refused"
        elif "your_corpse" in situation:
            kind = "resurrected" if last and last["tool"] == "resurrect" and last["ok"] else "corpse"
        else:
            kind = "other"
        if reason or kind != "other":
            logged = r["calls"][0] if r["calls"] else {"name": None, "args": {}}
            out.append({"id": f"{path.stem}#{i}", "log": path.name, "record": i, "t": r["t"], "character": who,
                        "kind": kind, "escalated": reason, "situation": situation,
                        "last_steps": [s["line"][:500] for s in steps[-3:]],
                        "logged": {"model": (r.get("usage") or {}).get("model"), "tool": logged["name"],
                                   "args": logged.get("args")},
                        "messages": r["messages"]})
        reason = None
    return out


def chosen(found: list[dict], kinds: list[str]) -> list[dict]:
    return [m for m in found if ("escalated" in kinds and m["escalated"]) or m["kind"] in kinds]


# ---- what a move is, by rule ---------------------------------------------------------

REQUIRED = {t["function"]["name"]: [a for a in t["function"]["parameters"]["required"] if a != "why"]
            for t in goal_tools()}


def usable(goal: dict | None) -> bool:
    """A goal code can carry out: one of the planner's goals with the arguments it needs."""
    if not goal or goal.get("tool") not in GOALS:
        return False
    args = goal.get("args") or {}
    return all(args.get(a) not in (None, "") for a in REQUIRED.get(goal["tool"], []))


def warned(m: dict) -> dict[str, str]:
    """Places the moment's own text warns about (lower case -> why): where code refused to go back,
    and where the character died or left a hunt in the steps shown."""
    out: dict[str, str] = {}
    _, steps = parse_user(m["messages"][0]["content"])
    for s in steps[-6:]:
        if hit := re.match(r"not going back near (.+?) yet", s["result"]):
            out[hit[1].lower()] = "refused"
        area = re.search(r"area='([^']+)'", s["args"])
        if s["tool"] == "hunt" and area and ("stopped because died" in s["result"] or "died on the way" in s["result"]):
            out.setdefault(area[1].lower(), "died")
        if s["tool"] == "hunt" and area and "left the area" in s["result"]:
            out.setdefault(area[1].lower(), "left")
    return out


def goes_back(m: dict, goal: dict | None) -> str | None:
    """Whether a goal goes back to a place the moment warns about: which, or None. Going for the
    corpse counts when code had just refused it for danger seen there, or the last try died on the way."""
    if not goal or not goal.get("tool"):
        return None
    args = goal.get("args") or {}
    _, steps = parse_user(m["messages"][0]["content"])
    if goal["tool"] == "recover_corpse":
        for s in reversed(steps[-4:]):
            if s["tool"] == "recover_corpse" and s["result"].startswith("not going back yet") and args.get("go_anyway"):
                return "the corpse, refused for danger there"
            if s["tool"] == "recover_corpse" and "died on the way" in s["result"]:
                return "the corpse, where it died going back"
        return None
    if goal["tool"] not in ("hunt", "travel_to"):
        return None
    text = " ".join(str(v) for k, v in args.items() if k != "why").lower()
    return next((place for place in warned(m) if place in text), None)


def again(m: dict, goal: dict | None) -> bool:
    """The goal that just failed, chosen again with the same arguments."""
    _, steps = parse_user(m["messages"][0]["content"])
    if not goal or not steps or steps[-1]["ok"] or goal.get("tool") != steps[-1]["tool"] or goal["tool"] == "resurrect":
        return False
    now = {f"{k}={v!r}" for k, v in (goal.get("args") or {}).items() if k != "why"}
    was = {f"{k}={v}" for k, v in re.findall(r"(\w+)=('(?:[^'\\]|\\.)*'|[^,]+)", steps[-1]["args"])}
    return now == was


def verdict(m: dict, goal: dict | None) -> tuple[str, str]:
    """("right" | "wrong" | "open", why) for a model's first move at a moment, by rule. "open" is a
    move the rules don't settle; a reading by hand, where there is one, is in the results file
    (`read`, `read_why`) and counted instead."""
    if not usable(goal):
        return "wrong", "no goal code can carry out"
    tool = goal["tool"]
    s = m["situation"]
    hp = health(s)
    if back := goes_back(m, goal):
        return "wrong", f"goes back to {back}"
    if m["kind"] == "dead":
        return ("right", "a ghost can only be resurrected") if tool == "resurrect" else ("open", f"{tool} while dead")
    if tool == "resurrect":
        return "wrong", "resurrect while alive"
    if "your_corpse" in s and hp is not None and hp < 0.8:
        # The prompt's rule: rest until nearly full before going back for the corpse or hunting.
        if tool == "rest":
            return "right", "rests first on little health"
        if tool in ("recover_corpse", "hunt"):
            return "wrong", f"{tool} at {hp:.0%} health"
        mana = re.match(r"(\d+)/", str(s.get("mana", "")))
        if tool in ("travel_to", "bank", "buy", "sell") and (s.get("position") or {}).get("area") == "open country" \
                and mana and int(mana[1]) < RECALL_MANA:
            return "wrong", f"sets off on foot across open country at {hp:.0%} health (no mana to recall)"
        return "open", f"{tool} at {hp:.0%} health"
    if m["kind"] == "resurrected":
        return ("right", f"{tool} at full health") if tool in ("recover_corpse", "rest") else ("open", tool)
    if m["kind"] == "refused":
        if tool in ("hunt", "travel_to"):
            return "right", "somewhere else"
        _, steps = parse_user(m["messages"][0]["content"])
        wait = re.search(r"or wait (\d+) min", steps[-1]["result"])
        if tool == "rest" and wait and int(wait[1]) <= SHORT_WAIT_MIN:
            return "right", f"waits out the {wait[1]} min left"
        return "open", f"{tool} with {wait[1] if wait else '?'} min left on the refusal"
    return "open", tool


# ---- asking -----------------------------------------------------------------------------

def copy_store(to: Path) -> Path:
    """A copy of the local shard's store to answer from, so the store itself is never opened for writing."""
    src = worlds.WORLDS_DIR / "local" / "world.sqlite"
    to.parent.mkdir(parents=True, exist_ok=True)
    to.unlink(missing_ok=True)
    with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as a, sqlite3.connect(to) as b:
        a.backup(b)
    return to


def answer(world: worlds.World, m: dict, call: llm.ToolCall) -> Any:
    args = call.arguments or {}
    if call.name == "remember":
        return {"ok": True, "id": 0, "kept": "remembered for later sessions"}
    if call.name == "long_goal":
        return {"ok": True}
    if call.name == "recall":
        if not m.get("character"):
            return {"memories": [], "note": "nothing remembered about that"}
        mem = memories.Memory(world, m["character"])
        query = str(args.get("query", ""))
        found = mem.list(search=" ".join(keywords(query, limit=8)) or query, limit=5, before=memories.stamp_of(m["t"]))
        at = datetime.fromtimestamp(m["t"], timezone.utc)
        return [{"memory": memories.words(r, at), "id": r["id"]} for r in found] \
            or {"memories": [], "note": "nothing remembered about that"}
    return world.call_tool(call.name, {k: v for k, v in args.items() if k != "question"})


async def play(m: dict, model: str, world: worlds.World, tools: list[dict]) -> dict:
    """One moment put to one model, to its first goal: the goal and every call on the way."""
    msgs = [{"role": "system", "content": SYSTEM}, *m["messages"]]
    calls: list[dict] = []
    goal = None
    error = None
    for _ in range(MAX_QUERIES + 1):
        try:
            with costs.kind("escalate"):
                res = await llm.chat(msgs, tools=tools, tool_choice="auto", model=model, max_tokens=MAX_TOKENS)
        except llm.LlmError as e:
            error = str(e)[:300]
            break
        c = res.tool_calls[0] if res.tool_calls else None
        calls.append({"tool": c.name if c else None, "args": c.arguments if c else None,
                      "said": (res.content or "")[:300], "finish": res.finish_reason, **res.usage.to_log()})
        if c is None:
            msgs += [res.message, {"role": "user", "content": "Answer by calling exactly one tool."}]
            continue
        if c.name in GOALS:
            goal = {"tool": c.name, "args": c.arguments}
            break
        msgs += [{**res.message, "tool_calls": res.message.get("tool_calls", [])[:1]},
                 llm.tool_result(c, answer(world, m, c))]
    v, why = verdict(m, goal)
    return {"goal": goal, "usable": usable(goal), "verdict": v, "verdict_why": why, "back": goes_back(m, goal),
            "again": again(m, goal),
            "queries": [c["tool"] for c in calls if c["tool"] and c["tool"] not in GOALS],
            "calls": calls, "error": error,
            "prompt_tokens": sum(c["prompt_tokens"] for c in calls),
            "completion_tokens": sum(c["completion_tokens"] for c in calls),
            "cost": round(sum(c.get("cost") or 0 for c in calls), 6),
            "cost_uncached": uncached(model, calls),
            "latency_s": round(sum(c["latency_ms"] for c in calls) / 1000, 1)}


def uncached(model: str, calls: list[dict]) -> float | None:
    """What the step costs at list prices with nothing cached before it: its first call writes the
    cache (the fixed prompt and tools, a quarter more than plain input) and its later calls read it
    (a tenth)."""
    if model not in LIST_PRICE or not calls:
        return None
    p_in, p_out = LIST_PRICE[model]
    fixed = max((c.get("cached_tokens") or 0) + (c.get("cache_write_tokens") or 0) for c in calls)
    total = 0.0
    for i, c in enumerate(calls):
        rest = max(0, c["prompt_tokens"] - fixed)
        total += (rest + fixed * (1.25 if i == 0 else 0.1)) * p_in / 1e6 + c["completion_tokens"] * p_out / 1e6
    return round(total, 6)


async def ask(found: list[dict], names: list[str], world: worlds.World, cost_logs: Path | None,
              parallel: int = 4) -> dict[str, list[dict]]:
    tools = goal_tools() + [machine_tool()] + worlds.tool_schemas() \
        + [memories.remember_tool(), memories.recall_tool(), longgoals.tool()]

    async def one_model(name: str) -> list[dict]:
        model = models.ALIASES.get(name, name)
        gate = asyncio.Semaphore(parallel)

        async def one(m: dict) -> dict:
            async with gate:
                row = await play(m, model, world, tools)
                print(f"{name:<7} {m['id']:<18} {m['kind']:<11} -> "
                      f"{row['goal']['tool'] if row['goal'] else '(no goal)'} [{row['verdict']}]", file=sys.stderr)
                return row

        async def run() -> list[dict]:
            first = await one(found[0])  # alone, so the calls after it find the fixed prompt cached
            return [first, *await asyncio.gather(*(one(m) for m in found[1:]))]

        if not cost_logs:
            return await run()
        cost_logs.mkdir(parents=True, exist_ok=True)
        with (cost_logs / f"hard-{name}.jsonl").open("a") as f:
            def write(rec: dict) -> None:
                f.write(json.dumps(rec) + "\n")
                f.flush()
            with costs.ledger.logging_to(write):
                return await run()

    rows = await asyncio.gather(*(one_model(n) for n in names))
    return dict(zip(names, rows))


# ---- counting ---------------------------------------------------------------------------

def table(result: dict) -> dict:
    ms = result["moments"]
    out: dict[str, Any] = {"moments": len(ms), "by_kind": {}, "escalated": sum(1 for m in ms if m["escalated"])}
    for m in ms:
        out["by_kind"][m["kind"]] = out["by_kind"].get(m["kind"], 0) + 1
    names = list(result["models"])
    per = {}
    for name in names:
        rows = [m["answers"][name] for m in ms]
        first = [r["calls"][0] for r in rows if r["calls"]]
        moves: dict[str, dict[str, int]] = {}
        for m, r in zip(ms, rows):
            tool = r["goal"]["tool"] if r["goal"] else "(no goal)"
            moves.setdefault(m["kind"], {})
            moves[m["kind"]][tool] = moves[m["kind"]].get(tool, 0) + 1
        v = [r.get("read") or r["verdict"] for r in rows]
        per[name] = {
            "usable": sum(r["usable"] for r in rows),
            "right": v.count("right"), "wrong": v.count("wrong"), "open": v.count("open"),
            "goes_back": sum(1 for r in rows if r["back"]),
            "the_failed_goal_again": sum(1 for r in rows if r.get("again")),
            "errors": sum(1 for r in rows if r.get("error")),
            "asked_the_world_first": sum(1 for r in rows if r["queries"]),
            "calls_a_step": round(sum(len(r["calls"]) for r in rows) / len(rows), 2),
            "cost_a_step": round(sum(r["cost"] for r in rows) / len(rows), 5),
            "cost_a_step_uncached": round(sum(r["cost_uncached"] or 0 for r in rows) / len(rows), 5),
            "cost_total": round(sum(r["cost"] for r in rows), 4),
            "latency_s_a_step_median": round(statistics.median(r["latency_s"] for r in rows), 1),
            "latency_s_a_step_p90": round(sorted(r["latency_s"] for r in rows)[int(0.9 * (len(rows) - 1))], 1),
            "latency_s_first_call_median": round(statistics.median(c["latency_ms"] for c in first) / 1000, 1),
            "output_tokens_a_step": round(sum(r["completion_tokens"] for r in rows) / len(rows)),
            "moves": moves}
    out["models"] = per
    same = [m for m in ms if len({(m["answers"][n]["goal"] or {}).get("tool") for n in names}) == 1]
    out["all_the_same_tool"] = len(same)
    return out


def rescore(result: dict) -> dict:
    """The rules applied again to saved answers (after `verdict` changes): the moments' user turns
    are read from the logs once more."""
    logged = {m["id"]: m for log in result["logs"] for m in moments(Path(log))}
    for m in result["moments"]:
        full = logged[m["id"]]
        for a in m["answers"].values():
            a["usable"], a["back"], a["again"] = usable(a["goal"]), goes_back(full, a["goal"]), again(full, a["goal"])
            a["verdict"], a["verdict_why"] = verdict(full, a["goal"])
    result["table"] = table(result)
    return result


def show_rows(result: dict, only_differing: bool = True) -> None:
    names = list(result["models"])
    for m in result["moments"]:
        goals = [m["answers"][n]["goal"] for n in names]
        if only_differing and len({(g or {}).get("tool") for g in goals}) == 1:
            continue
        print(f"\n{m['id']}  {m['kind']}" + (f"  escalated: {m['escalated']}" if m["escalated"] else ""))
        print(f"  now: {json.dumps(m['situation'])[:300]}")
        print(f"  last: {m['last_steps'][-1][:300] if m['last_steps'] else '(nothing yet)'}")
        for n in names:
            a = m["answers"][n]
            g = a["goal"] or {"tool": "(no goal)", "args": {}}
            args = ", ".join(f"{k}={v!r}" for k, v in (g.get("args") or {}).items() if k != "why")
            q = f" after {', '.join(a['queries'])}" if a["queries"] else ""
            print(f"  {n:<7} {a.get('read') or a['verdict']:<5} {g['tool']}({args[:160]}){q}")
            print(f"          why: {str((g.get('args') or {}).get('why', ''))[:220]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("list", "ask"):
        p = sub.add_parser(name)
        p.add_argument("logs", type=Path, nargs="+")
        p.add_argument("--kinds", default="escalated,refused,resurrected",
                       help=f"comma list from {', '.join(KINDS)} (default escalated,refused,resurrected)")
        p.add_argument("--limit", type=int)
    p.add_argument("--models", default="haiku,sonnet,opus")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--cost-logs", type=Path, help="folder for hard-<model>.jsonl, the ai_cost records (uo-brain costs)")
    p.add_argument("--world", type=Path, help="where the copy of the store goes (default beside --out)")
    t = sub.add_parser("table")
    t.add_argument("file", type=Path)
    t.add_argument("--rows", action="store_true", help="also the moments where the models chose different tools")
    t.add_argument("--all", action="store_true", help="--rows: every moment")
    t.add_argument("--rescore", action="store_true", help="apply the rules again and save (run from brain/)")
    args = ap.parse_args()

    if args.cmd == "table":
        result = json.loads(args.file.read_text())
        if args.rescore:
            args.file.write_text(json.dumps(rescore(result), indent=1) + "\n")
        print(json.dumps(table(result), indent=2))
        if args.rows or args.all:
            show_rows(result, only_differing=not args.all)
        return
    found = chosen([m for log in args.logs for m in moments(log)], args.kinds.split(","))[:args.limit]
    if args.cmd == "list":
        for m in found:
            print(f"{m['id']:<18} {m['kind']:<11} {m['escalated'] or '':<45.45} health {m['situation'].get('health')}"
                  f"  logged: {m['logged']['tool']}")
        print(f"{len(found)} moments")
        return
    if not found:
        sys.exit("no hard moments in those logs")
    names = [n.strip() for n in args.models.split(",") if n.strip()]
    world = worlds.World(copy_store(args.world or args.out.with_suffix(".world.sqlite")))
    started = time.time()
    try:
        answers = asyncio.run(ask(found, names, world, args.cost_logs))
    finally:
        world.close()
    result = {"what": "The planner's hard moments in recorded sessions, each put once to every model (cuo-vpi)",
              "asked": datetime.now(timezone.utc).isoformat(timespec="seconds"), "seconds": round(time.time() - started),
              "logs": [str(p) for p in args.logs], "kinds": args.kinds.split(","),
              "models": {n: models.ALIASES.get(n, n) for n in names},
              "moments": [{**{k: v for k, v in m.items() if k != "messages"},
                           "answers": {n: answers[n][i] for n in names}} for i, m in enumerate(found)]}
    result["table"] = table(result)
    args.out.write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps(result["table"], indent=2))


if __name__ == "__main__":
    main()
