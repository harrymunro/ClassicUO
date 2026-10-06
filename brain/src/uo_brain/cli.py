"""uo-brain: drive a ClassicUO client's agent from the command line.

  uo-brain login --account warrior --password warrior --create-warrior Brutus
  uo-brain run --mode auto --judge jev --log logs/run.jsonl
  uo-brain scenario --rounds 3 --round-seconds 120 [--kit mage]
  uo-brain strategy add "Attack relentlessly and never flee."   (or: strategy load my-strategy.md)
  uo-brain strategy templates | strategy template relentless [--replace] | strategy drop relentless
  uo-brain status | snapshot [--semantic] | act attack target=0x1234 | say "[AgentKit" | shot out.png
  uo-brain report logs/run.jsonl
"""

import argparse
import asyncio
import json
import signal
import sys
import time
from pathlib import Path

from . import judge as judges
from . import loop, policy, state
from . import strategy as strategies
from .rpc import AgentRpc

WARRIOR_SKILLS = {"Swordsmanship": 30, "Tactics": 30, "Healing": 30, "Anatomy": 30}


def main() -> None:
    ap = argparse.ArgumentParser(prog="uo-brain", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=5577, help="client agent_port")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run the decision loop")
    add_run_args(r)

    lg = sub.add_parser("login", help="log the client in (and optionally create a warrior)")
    lg.add_argument("--account", required=True)
    lg.add_argument("--password", required=True)
    lg.add_argument("--host")
    lg.add_argument("--server-port", type=int)
    lg.add_argument("--character")
    lg.add_argument("--create-warrior", metavar="NAME", help="create this warrior if the character does not exist")

    sub.add_parser("status")
    sn = sub.add_parser("snapshot")
    sn.add_argument("--semantic", action="store_true", help="print the state Jev would see")

    a = sub.add_parser("act", help="send one manual action, e.g. act attack target=0x1234")
    a.add_argument("verb")
    a.add_argument("args", nargs="*", help="key=value")

    m = sub.add_parser("mode")
    m.add_argument("mode", choices=["off", "assist", "auto"])

    sub.add_parser("accept", help="accept the agent's pending suggestion")

    cm = sub.add_parser("cmd", help='run a client command as if typed, e.g. cmd "-agent status"')
    cm.add_argument("text")

    st = sub.add_parser("strategy", help="show or change the character's strategy (natural language)")
    st.add_argument("action", nargs="?", default="show",
                    choices=["show", "set", "add", "clear", "load", "explain", "templates", "template", "drop"])
    st.add_argument("text", nargs="?", help="text for set/add, a Markdown file for load, a template name for template/drop")
    st.add_argument("--replace", action="store_true", help="template: replace the strategy instead of adding to it")
    st.add_argument("--judge", choices=["jev", "heuristic"], default="jev", help="for explain")
    st.add_argument("--provider", choices=["auto", "openrouter", "typesafe"], default="auto")
    st.add_argument("--model")

    s = sub.add_parser("say")
    s.add_argument("text")

    sh = sub.add_parser("shot", help="save a screenshot of the client")
    sh.add_argument("path")

    sc = sub.add_parser("scenario", help="arena benchmark on the local test server")
    add_run_args(sc)
    sc.add_argument("--rounds", type=int, default=3)
    sc.add_argument("--round-seconds", type=float, default=120)
    sc.add_argument("--monsters", type=int, default=6)
    sc.add_argument("--kind", default="", help="monster kind for [AgentArena")
    sc.add_argument("--kit", choices=["warrior", "mage"], default="warrior", help="template for [AgentKit")

    rp = sub.add_parser("report", help="summarise a decision log")
    rp.add_argument("log")

    rpl = sub.add_parser("replay", help="re-ask a log's questions to another judge and compare answers")
    rpl.add_argument("log")
    rpl.add_argument("--judge", choices=["jev", "heuristic"], default="jev")
    rpl.add_argument("--provider", choices=["auto", "openrouter", "typesafe"], default="auto")
    rpl.add_argument("--model")
    rpl.add_argument("--limit", type=int, default=200)

    args = ap.parse_args()
    if args.cmd == "report":
        report(Path(args.log))
        return
    if args.cmd == "replay":
        asyncio.run(replay(args))
        return
    asyncio.run(dispatch(args))


def add_run_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--judge", choices=["jev", "heuristic"], default="jev")
    p.add_argument("--provider", choices=["auto", "openrouter", "typesafe"], default="auto")
    p.add_argument("--model", help="model id (default: ~typesafe/jev-latest on OpenRouter, jev-latest direct)")
    p.add_argument("--mode", choices=["keep", "off", "assist", "auto"], default="keep",
                   help="set the client's agent mode first (default: leave as is)")
    p.add_argument("--archetype", choices=["auto", "warrior", "mage"], default="auto",
                   help="how to play the character (default: tell from its skills)")
    p.add_argument("--duration", type=float, help="stop after this many seconds")
    p.add_argument("--strategy", type=Path, metavar="FILE", help="load this Markdown strategy into the character first")
    p.add_argument("--template", action="append", default=[], metavar="NAME",
                   help="pull this strategy template in first (repeatable; see: uo-brain strategy templates)")
    p.add_argument("--log", type=Path, help="append decisions to this JSONL file")
    p.add_argument("--min-confidence", type=float, default=policy.PolicyConfig.min_intent_confidence)
    p.add_argument("--price-per-million", type=float, default=loop.LoopConfig.price_per_million)


async def dispatch(args) -> None:
    rpc = AgentRpc(port=args.port)
    await rpc.connect()
    try:
        match args.cmd:
            case "status":
                print(json.dumps(await rpc.call("status"), indent=2))
            case "snapshot":
                snap = await rpc.call("snapshot")
                if args.semantic and snap.get("in_game"):
                    sit = state.build(snap, set(), state.journal_events(snap["journal"]))
                    print(json.dumps(sit.state, indent=2))
                else:
                    print(json.dumps(snap, indent=2))
            case "act":
                params = {"verb": args.verb, "source": "manual"}
                for kv in args.args:
                    k, v = kv.split("=", 1)
                    params[k] = parse_value(v)
                print(json.dumps(await rpc.call("act", **params)))
            case "mode":
                print(json.dumps(await rpc.call("mode", mode=args.mode)))
            case "strategy":
                await strategy_cmd(rpc, args)
            case "cmd":
                print(json.dumps(await rpc.call("command", text=args.text)))
            case "accept":
                print(json.dumps(await rpc.call("accept")))
            case "say":
                print(json.dumps(await rpc.call("act", verb="say", text=args.text, source="manual")))
            case "shot":
                print(json.dumps(await rpc.call("capture", path=str(Path(args.path).resolve()))))
            case "login":
                await login(rpc, args)
            case "run":
                await run_loop(rpc, args)
            case "scenario":
                await scenario(rpc, args)
    finally:
        await rpc.close()


def parse_value(v: str):
    if v.startswith("0x"):
        return int(v, 16)
    if v.lstrip("-").isdigit():
        return int(v)
    if v in ("true", "false"):
        return v == "true"
    return v


async def login(rpc: AgentRpc, args) -> None:
    params = {"account": args.account, "password": args.password}
    if args.host:
        params["host"] = args.host
    if args.server_port:
        params["port"] = args.server_port
    if args.character:
        params["character"] = args.character
    if args.create_warrior:
        params["create"] = {"name": args.create_warrior, "str": 50, "dex": 30, "int": 10, "skills": WARRIOR_SKILLS}
    print(json.dumps(await rpc.call("login", **params)))
    for _ in range(60):
        await asyncio.sleep(1)
        st = await rpc.call("status")
        if st.get("in_game"):
            print(f"in game as {st['player']}")
            return
        if st.get("login_error"):
            sys.exit(f"login failed: {st['login_error']}")
    sys.exit(f"login timed out: {st}")


def make_judge(args) -> judges.Judge:
    return judges.make(args.judge, args.provider, args.model)


async def strategy_cmd(rpc: AgentRpc, args) -> None:
    match args.action:
        case "templates":
            for t in await rpc.call("templates"):
                mark = "*" if t.get("in_use") else " "
                print(f"{mark} {t['name']:<14} {t['for']:<8} {t['summary']}{'  (yours)' if t['user'] else ''}")
            print("\n* in use.  uo-brain strategy template NAME pulls one in (--replace to start over).")
            return
        case "set" | "add" | "template" | "drop" if not args.text:
            sys.exit(f"strategy {args.action} needs {'a template name' if args.action in ('template', 'drop') else 'text'}")
        case "template":
            res = await rpc.call("strategy", template=args.text, replace=args.replace)
        case "drop":
            res = await rpc.call("strategy", remove_template=args.text)
        case "set":
            res = await rpc.call("strategy", text=args.text)
        case "add":
            res = await rpc.call("strategy", add=args.text)
        case "clear":
            res = await rpc.call("strategy", clear=True)
        case "load":
            res = await rpc.call("strategy", text=Path(args.text).read_text())
        case _:
            res = await rpc.call("strategy")
    print(res["strategy"] or "(no strategy)")
    if args.action == "explain":
        judge = judges.make(args.judge, args.provider, args.model)
        try:
            knobs, answers = await strategies.compile_strategy(judge, res["strategy"])
        finally:
            await judge.close()
        print(json.dumps({"settings": knobs.__dict__, "summary": knobs.describe(),
                          "answers": answers.to_log() if answers else None}, indent=2))


async def run_loop(rpc: AgentRpc, args) -> loop.RunStats:
    if args.strategy:
        await rpc.call("strategy", text=args.strategy.read_text())
    for name in args.template:
        await rpc.call("strategy", template=name)
    if args.mode != "keep":
        await rpc.call("mode", mode=args.mode)
    judge = make_judge(args)
    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGINT, stop.set)
    lcfg = loop.LoopConfig(duration_s=args.duration, price_per_million=args.price_per_million)
    pcfg = policy.PolicyConfig(min_intent_confidence=args.min_confidence)
    print(f"running with {judge.name}; Ctrl-C to stop")
    try:
        stats = await loop.run(rpc, judge, lcfg, pcfg, args.log, stop,
                               archetype=None if args.archetype == "auto" else args.archetype)
    finally:
        await judge.close()
    print(json.dumps(stats.summary(lcfg.price_per_million), indent=2))
    return stats


async def scenario(rpc: AgentRpc, args) -> None:
    """Repeatable arena rounds: reset, re-kit, spawn monsters, let the agent play."""
    if args.mode == "keep":
        args.mode = "auto"
    results = []
    for n in range(1, args.rounds + 1):
        for cmd in ("[AgentReset", f"[AgentKit {args.kit}", f"[AgentArena {args.monsters} {args.kind}".strip()):
            await rpc.call("act", verb="say", text=cmd, source="manual")
            await asyncio.sleep(1.0)
        print(f"round {n}: {args.monsters} monsters, {args.round_seconds:.0f}s")
        args.duration = args.round_seconds
        stats = await run_loop(rpc, args)
        results.append(stats.summary(args.price_per_million))
    await rpc.call("act", verb="say", text="[AgentReset", source="manual")
    kills = sum(r["client_stats"].get("kills", 0) for r in results)
    deaths = sum(r["client_stats"].get("deaths", 0) for r in results)
    minutes = sum(r["minutes"] for r in results)
    print(json.dumps({"rounds": len(results), "kills": kills, "deaths": deaths, "minutes": round(minutes, 1),
                      "kills_per_hour": round(kills / max(minutes / 60, 1e-9), 1)}, indent=2))


async def replay(args) -> None:
    """Offline comparison: same states and questions, different judge. No game needed."""
    records = [json.loads(line) for line in Path(args.log).read_text().splitlines() if line.strip()]
    ds = [d for d in records if d["type"] == "decision" and isinstance(d.get("questions"), dict)][: args.limit]
    judge = judges.make(args.judge, args.provider, args.model)
    same = {"intent": 0, "target": 0}
    asked = {"intent": 0, "target": 0}
    confusion: dict[str, int] = {}
    latencies = []
    try:
        for d in ds:
            ans = await judge.ask(d["state"], d["questions"])
            latencies.append(ans.latency_ms)
            for q in same:
                old = d["answers"]["choices"].get(q, {}).get("choice")
                new = ans.choices.get(q)
                if old is None or new is None:
                    continue
                asked[q] += 1
                same[q] += old == new.choice
                if q == "intent":
                    key = f"{old}->{new.choice}"
                    confusion[key] = confusion.get(key, 0) + 1
    finally:
        await judge.close()
    print(json.dumps({
        "decisions": len(ds),
        "judge": judge.name,
        "agreement": {q: round(same[q] / asked[q], 3) if asked[q] else None for q in same},
        "intent_logged_to_new": dict(sorted(confusion.items(), key=lambda kv: -kv[1])),
        "latency_ms_avg": round(sum(latencies) / len(latencies), 1) if latencies else None,
    }, indent=2))


def report(path: Path) -> None:
    decisions = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    ds = [d for d in decisions if d["type"] == "decision"]
    if not ds:
        print("no decisions")
        return
    lat = sorted(d["answers"]["latency_ms"] for d in ds)
    intents: dict[str, int] = {}
    for d in ds:
        intents[d["intent"]] = intents.get(d["intent"], 0) + 1
    conf = [d["confidence"] for d in ds]
    print(json.dumps({
        "decisions": len(ds),
        "judge": sorted({d["judge"] for d in ds}),
        "intents": intents,
        "gated": sum(d["gated"] for d in ds),
        "confidence_avg": round(sum(conf) / len(conf), 3),
        "latency_ms_p50": lat[len(lat) // 2],
        "latency_ms_p95": lat[int(0.95 * (len(lat) - 1))],
        "input_tokens": sum(d["answers"]["input_tokens"] for d in ds),
        "summaries": [d for d in decisions if d["type"] == "summary"],
    }, indent=2))


if __name__ == "__main__":
    main()
