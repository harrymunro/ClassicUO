"""uo-brain: drive a ClassicUO client's agent from the command line.

  uo-brain login --account warrior --password warrior --create-warrior Brutus
  uo-brain run --mode auto --judge jev --log logs/run.jsonl
  uo-brain scenario --rounds 3 --round-seconds 120 [--kit mage]
  uo-brain strategy add "Attack relentlessly and never flee."   (or: strategy load my-strategy.md)
  uo-brain strategy templates | strategy template relentless [--replace] | strategy drop relentless
  uo-brain status | snapshot [--semantic] | act attack target=0x1234 | say "[AgentKit" | shot out.png
  uo-brain report logs/run.jsonl
  uo-brain bench --scenarios core --judges heuristic,jev --rounds 10   (bench report bench/*.json)
  uo-brain world find bank --near-place "Britain graveyard" | world hunt warrior new | world note "..." --area Britain
  uo-brain world outcomes logs/session.jsonl   (what each hunt gave, per area and kit)
"""

import argparse
import asyncio
import json
import signal
import sys
import time
from pathlib import Path

from . import bench as benchmark
from . import guides
from . import judge as judges
from . import llm, loop, outcomes, policy, state
from . import strategy as strategies
from . import world as worlds
from . import world_import
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

    bn = sub.add_parser("bench", help="judgment benchmark: scenarios where fixed rules fail (local test server)")
    bn.add_argument("what", nargs="?", default="run", choices=["run", "list", "report"])
    bn.add_argument("files", nargs="*", type=Path, help="for report: results JSON files to compare")
    bn.add_argument("--scenarios", default="core",
                    help="comma-separated names, 'core' (judge comparison) or 'adherence' or 'all'")
    bn.add_argument("--judges", default="heuristic,jev", help="comma-separated: heuristic, jev, jev+<template>")
    bn.add_argument("--rounds", type=int, default=10)
    bn.add_argument("--lane", type=int, default=0, help="test-field lane, so several clients can run at once")
    bn.add_argument("--out", type=Path, help="results JSON (default bench/<time>.json)")
    bn.add_argument("--min-confidence", type=float, default=policy.PolicyConfig.min_intent_confidence)

    rp = sub.add_parser("report", help="summarise a decision log")
    rp.add_argument("log")

    rpl = sub.add_parser("replay", help="re-ask a log's questions to another judge and compare answers")
    rpl.add_argument("log")
    rpl.add_argument("--judge", choices=["jev", "heuristic"], default="jev")
    rpl.add_argument("--provider", choices=["auto", "openrouter", "typesafe"], default="auto")
    rpl.add_argument("--model")
    rpl.add_argument("--limit", type=int, default=200)

    add_world_args(sub)

    ss = sub.add_parser("session", help="play towards a goal in words: the planner picks each goal, Jev fights")
    ss.add_argument("goal", help='e.g. "hunt the undead at the Britain graveyard, keep supplied with bandages, bank gold"')
    ss.add_argument("--hours", type=float, default=1.0)
    add_run_args(ss)

    do = sub.add_parser("do", help="one session goal: travel, bank, buy, sell, hunt or rest (uses the world store)")
    do.add_argument("--shard", default="local")
    dsub = do.add_subparsers(dest="goal", required=True)
    t = dsub.add_parser("travel", help="walk to a named place or x,y")
    t.add_argument("place")
    t.add_argument("--distance", type=int, default=2)
    b = dsub.add_parser("bank", help="at the nearest bank: deposit gold/loot, withdraw supplies")
    b.add_argument("--deposit", default="gold,loot")
    b.add_argument("--withdraw", default="", help='e.g. "bandage:100,heal potion:5"')
    bu = dsub.add_parser("buy", help="buy an item from the nearest vendor that sells it")
    bu.add_argument("item")
    bu.add_argument("count", type=int)
    bu.add_argument("--vendor", help="vendor kind, e.g. healer, mage_shop")
    se = dsub.add_parser("sell", help="sell loot (or named items) to the nearest vendor of a kind")
    se.add_argument("items", nargs="?", default="loot")
    se.add_argument("--vendor", default="weaponsmith")
    h = dsub.add_parser("hunt", help="hunt an area with Jev until time, supplies or weight say stop")
    h.add_argument("area")
    h.add_argument("--minutes", type=float, default=10)
    add_run_args(h)
    r = dsub.add_parser("rest")
    r.add_argument("seconds", type=float, nargs="?", default=30)

    args = ap.parse_args()
    if args.cmd == "world":
        world_cmd(args)
        return
    if args.cmd == "report":
        report(Path(args.log))
        return
    if args.cmd == "replay":
        asyncio.run(replay(args))
        return
    if args.cmd == "bench" and args.what != "run":
        bench_offline(args)
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
    p.add_argument("--shard", default="local", help="world store for the planner (run with a goal in auto mode)")
    p.add_argument("--planner-model", help="default anthropic/claude-sonnet-5.5 (or PLANNER_MODEL)")


def add_world_args(sub) -> None:
    w = sub.add_parser("world", help="query or fill the shard's world store (does not connect to the game)")
    w.add_argument("--shard", default="local", help="which store: brain/worlds/<shard>/world.sqlite (default local)")
    w.add_argument("--root", type=Path, help="folder that holds the shard stores (default brain/worlds)")
    w.add_argument("--map", default=worlds.DEFAULT_MAP, help="facet for map queries (default Felucca)")
    ws = w.add_subparsers(dest="world_cmd", required=True)

    n = ws.add_parser("note", help="add a note in your own words (never other players' names)")
    n.add_argument("text")
    n.add_argument("--area", help="the area it applies to, e.g. Britain")
    n.add_argument("--tag", action="append", default=[], help="repeatable")

    f = ws.add_parser("find", help="nearest places of a kind (bank, healer, moongate, ...) or vendors of an item")
    f.add_argument("kind")
    f.add_argument("--near", help='"x,y" or a place name')
    f.add_argument("--near-place", metavar="NAME", help="a place name to measure from")

    sp = ws.add_parser("spawns", help="what spawns in an area, or near a point")
    sp.add_argument("area", nargs="?")
    sp.add_argument("--near", help='"x,y" or a place name')
    sp.add_argument("--radius", type=int, default=60)

    h = ws.add_parser("hunt", help="hunting spots for an archetype and level")
    h.add_argument("archetype", help="warrior or mage")
    h.add_argument("level", help=", ".join(worlds.LEVELS))
    h.add_argument("--near", help='rank closer spots higher: "x,y" or a place name')

    pl = ws.add_parser("place", help="resolve a place name to coordinates")
    pl.add_argument("name")

    rt = ws.add_parser("route", help="stored routes, teleporters and distance between two places")
    rt.add_argument("start")
    rt.add_argument("end")

    nt = ws.add_parser("notes", help="search notes by keyword and/or area")
    nt.add_argument("keywords", nargs="*")
    nt.add_argument("--area")

    ws.add_parser("stats", help="row counts by table and source")

    oc = ws.add_parser("outcomes", help="record what happened per area and kit from session logs (re-import replaces)")
    oc.add_argument("logs", nargs="+", type=Path, metavar="LOG")
    oc.add_argument("--area", help="for logs without hunts (run, scenario, bench): count each run as a loop here")

    im = ws.add_parser("import-modernuo", help="fill the store from a ModernUO server's data (replaces modernuo rows)")
    im.add_argument("--modernuo-dir", type=Path, default=world_import.DEFAULT_MODERNUO,
                    help="ModernUO checkout (default ~/Workspace/ModernUO)")
    im.add_argument("--maps", default="Felucca", help="comma-separated facets to import (default Felucca)")

    ig = ws.add_parser("import-guide", help="have the planner model turn a guide page into notes")
    ig.add_argument("source", metavar="URL|FILE")
    ig.add_argument("--area", help="the area the page is about")
    fg = ws.add_parser("fill-gaps", help="store what the planner model knows about an area, marked unverified")
    fg.add_argument("area")
    for p in (ig, fg):
        p.add_argument("--planner-model", help=f"OpenRouter model (default $PLANNER_MODEL or {llm.PLANNER_MODEL})")

    for p in (f, sp, h, pl, rt, nt):
        p.add_argument("--limit", type=int, default=5)


def world_cmd(args) -> None:
    """World commands read and write the store only, so they work without the client."""
    try:
        with worlds.World.open(args.shard, args.root) as w:
            match args.world_cmd:
                case "note":
                    out = {"id": w.add_note(args.text, args.area, args.tag), "area": args.area, "source": "note"}
                case "find":
                    out = w.find_place(args.kind, near=args.near_place or args.near, map=args.map, limit=args.limit)
                case "spawns":
                    out = w.what_spawns(args.area, near=args.near, map=args.map, radius=args.radius, limit=args.limit)
                case "hunt":
                    out = w.hunting_spots(args.archetype, args.level, near=args.near, map=args.map, limit=args.limit)
                case "place":
                    out = w.place(args.name, map=args.map, limit=args.limit)
                case "route":
                    out = w.route(args.start, args.end, map=args.map, limit=args.limit)
                case "notes":
                    out = w.notes(args.area, " ".join(args.keywords) or None, limit=args.limit)
                case "import-modernuo":
                    out = world_import.import_modernuo(w, args.modernuo_dir, [m.strip() for m in args.maps.split(",")])
                case "import-guide":
                    out = asyncio.run(guides.import_guide(w, args.source, args.area, model=args.planner_model))
                case "fill-gaps":
                    out = asyncio.run(guides.fill_gaps(w, args.area, model=args.planner_model))
                case "outcomes":
                    out = outcomes.import_logs(w, args.logs, area=args.area)
                case _:
                    out = w.stats()
    except (worlds.WorldError, llm.LlmError, OSError, ValueError) as e:
        sys.exit(f"world: {e}")
    print(json.dumps(out, indent=2))


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
            case "bench":
                await bench(rpc, args)
            case "do":
                await do_goal(rpc, args)
            case "session":
                await session_cmd(rpc, args)
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
    archetype = None if args.archetype == "auto" else args.archetype
    print(f"running with {judge.name}; Ctrl-C to stop")
    try:
        if args.cmd == "run":
            # Fights, and works towards the panel's goal in auto mode (autopilot.py).
            from .autopilot import Autopilot
            from .world import World

            world = World.open(args.shard)
            if args.duration:
                asyncio.get_running_loop().call_later(args.duration, stop.set)
            lcfg.duration_s = None
            try:
                await Autopilot(rpc, judge, world, lcfg, pcfg, args.log, archetype, args.planner_model).run(stop)
            finally:
                world.close()
            return loop.RunStats()
        stats = await loop.run(rpc, judge, lcfg, pcfg, args.log, stop, archetype=archetype)
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


async def session_cmd(rpc: AgentRpc, args) -> None:
    from .planner import Planner
    from .session import Session
    from .world import World

    world = World.open(args.shard)
    judge = make_judge(args)
    log = args.log.open("a") if args.log else None

    def write(rec: dict) -> None:
        if log:
            log.write(json.dumps(rec) + "\n")
            log.flush()
        if rec.get("type") in ("goal", "goal_result"):
            print(json.dumps(rec))

    async def show(goal: str, step: str, why: str) -> None:
        await rpc.call("note", text=f"goal: {step}" + (f" ({why})" if why else ""))

    archetype = None if args.archetype == "auto" else args.archetype
    session = Session(rpc, world, judge, log=write, archetype=archetype,
                      decisions_log=args.log.with_suffix(".decisions.jsonl") if args.log else None)
    from .recorder import Recorder
    session.recorder = Recorder(world, judge)
    plan = Planner(session, args.goal, model=args.planner_model, log=write, on_goal=show)
    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGINT, stop.set)
    await rpc.call("mode", mode="auto")
    try:
        summary = await plan.run(hours=args.hours, stop=stop)
    finally:
        await judge.close()
        world.close()
    write({"type": "session_summary", "t": time.time(), **summary})
    if log:
        log.close()
    print(json.dumps(summary, indent=2))


async def do_goal(rpc: AgentRpc, args) -> None:
    from .session import Session
    from .world import World

    world = World.open(args.shard)
    judge = make_judge(args) if args.goal == "hunt" else None
    log = args.log.open("a") if getattr(args, "log", None) else None
    session = Session(rpc, world, judge, log=lambda rec: print(json.dumps(rec)) if not log else
                      log.write(json.dumps(rec) + "\n"))
    await rpc.call("mode", mode="auto")  # a session goal is auto-mode play
    try:
        match args.goal:
            case "travel":
                xy = args.place.split(",")
                if len(xy) == 2 and all(v.strip().lstrip("-").isdigit() for v in xy):
                    res = await session.travel_to(x=int(xy[0]), y=int(xy[1]), distance=args.distance)
                else:
                    res = await session.travel_to(args.place, distance=args.distance)
            case "bank":
                res = await session.bank(args.deposit, args.withdraw)
            case "buy":
                res = await session.buy(args.item, args.count, args.vendor)
            case "sell":
                res = await session.sell(args.items, args.vendor)
            case "hunt":
                res = await session.hunt(args.area, args.minutes)
            case _:
                res = await session.rest(args.seconds)
    finally:
        if judge:
            await judge.close()
        if log:
            log.close()
        world.close()
    print(json.dumps(res.to_tool(), indent=2))


def bench_names(spec: str) -> list[str]:
    adherence = [n for n, s in benchmark.SCENARIOS.items() if s.template]
    match spec:
        case "core":
            return list(benchmark.CORE)
        case "adherence":
            return adherence
        case "all":
            return list(benchmark.SCENARIOS)
    names = [n.strip() for n in spec.split(",") if n.strip()]
    if unknown := [n for n in names if n not in benchmark.SCENARIOS]:
        sys.exit(f"unknown scenario(s): {', '.join(unknown)} (see: uo-brain bench list)")
    return names


async def bench(rpc: AgentRpc, args) -> None:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = args.out or Path("bench") / f"{stamp}.json"
    result = await benchmark.run(rpc, bench_names(args.scenarios), [j.strip() for j in args.judges.split(",")],
                                 args.rounds, args.lane, out, Path("logs") / "bench" / stamp,
                                 min_confidence=args.min_confidence)
    print(benchmark.table([result]))
    print(f"results: {out}")


def bench_offline(args) -> None:
    if args.what == "list":
        for name, sc in benchmark.SCENARIOS.items():
            fixed = f" [template {sc.template}]" if sc.template else ""
            print(f"{name:<28} {sc.kit:<8} {sc.bead}{fixed}\n    {sc.right}")
        return
    if not args.files:
        sys.exit("bench report needs one or more results files")
    print(benchmark.table([json.loads(f.read_text()) for f in args.files], [f.stem for f in args.files]))


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
