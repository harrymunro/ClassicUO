"""uo-brain: drive a ClassicUO client's agent from the command line.

  uo-brain login --account warrior --password warrior --create-warrior Brutus
  uo-brain run --mode auto --judge jev --log logs/run.jsonl
  uo-brain scenario --rounds 3 --round-seconds 120 [--kit mage]
  uo-brain strategy add "Attack relentlessly and never flee."   (or: strategy load my-strategy.md)
  uo-brain strategy templates | strategy template relentless [--replace] | strategy drop relentless
  uo-brain status | snapshot [--semantic] | act attack target=0x1234 | say "[AgentKit" | shot out.png
  uo-brain report logs/run.jsonl   (costs: uo-brain costs logs/run.jsonl [--check])
  uo-brain review logs/run.jsonl   (the planner model proposes strategy lines; then: review ... --accept 2)
  uo-brain bench --scenarios core --judges heuristic,jev --rounds 10   (bench report bench/*.json)
  uo-brain world find bank --near-place "Britain graveyard" | world hunt warrior new | world note "..." --area Britain
  uo-brain world outcomes logs/session.jsonl   (what each hunt gave, per area and kit)
  uo-brain machine list | machine show pull-one | machine design "a mage swarmed in the open" --archetype mage
"""

import argparse
import asyncio
import json
import signal
import sys
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from . import bench as benchmark
from . import client, guides
from . import judge as judges
from . import markers
from . import costs, llm, logs, loop, models, outcomes, policy, rules, state
from . import machine as machines
from . import review as reviews
from . import strategy as strategies
from . import world as worlds
from . import sightcheck, world_import
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

    cl = sub.add_parser("client", help="start a client on --port from bin/osx-arm64 (--headless: no window) and log it in")
    cl.add_argument("--headless", action="store_true", help="hide the window and draw nothing (a running client: hide it)")
    add_account_args(cl)
    cl.add_argument("--log", type=Path, help="the client's output (default logs/client-<port>.log)")

    wn = sub.add_parser("window", help="show or hide a running client's window (hidden: headless, nothing drawn)")
    wn.add_argument("action", nargs="?", default="status", choices=["show", "hide", "status"])

    sc = sub.add_parser("scenario", help="arena benchmark on the local test server")
    add_run_args(sc)
    sc.add_argument("--rounds", type=int, default=3)
    sc.add_argument("--round-seconds", type=float, default=120)
    sc.add_argument("--monsters", type=int, default=6)
    sc.add_argument("--kind", default="", help="monster kind for [AgentArena")
    sc.add_argument("--kit", choices=["warrior", "mage"], default="warrior", help="template for [AgentKit")

    sg = sub.add_parser("sight-check", help="compare the client's line of sight with the server's around Britain "
                                            "(local test server; moves the character)")
    sg.add_argument("--spots", type=int, default=len(sightcheck.BRITAIN_SPOTS), help="how many of the Britain spots")
    sg.add_argument("--per-spot", type=int, default=40, help="tiles tested at each spot (two pairs each)")
    sg.add_argument("--radius", type=int, default=15)
    sg.add_argument("--seed", type=int, default=1)
    sg.add_argument("--here", action="store_true", help="test where the character stands instead of moving it")
    sg.add_argument("--out", type=Path, help="results JSON (default bench/<date>-sight-check.json)")

    bn = sub.add_parser("bench", help="judgment benchmark: scenarios where fixed rules fail (local test server)")
    bn.add_argument("what", nargs="?", default="run", choices=["run", "list", "report"])
    bn.add_argument("files", nargs="*", type=Path, help="for report: results JSON files to compare")
    bn.add_argument("--scenarios", default="core",
                    help="comma-separated names, 'core' (judge comparison), 'adherence', 'archetypes', "
                         "'world' (world facts), 'packs', 'sight' (line of sight), 'spots' (where to run: cuo-fhf) "
                         "or 'all'")
    bn.add_argument("--facts", default="none,all,jev",
                    help="world-fact scenarios: which conditions to play per judge (none, all, jev; default all three)")
    bn.add_argument("--judges", default="heuristic,jev",
                    help="comma-separated: heuristic, jev, a model alias (haiku, sonnet, or one from --alias or the "
                         "profile), each with +<template>, and optionally /nokite (no stepping back), "
                         "/nopack (code doesn't leave from a pack), /everytick (Jev asked at every tick, as before "
                         "cuo-df8), /nosight (the client's sight checks off: no "
                         "in_sight, it aims and walks as before them), /spots (runs, leaves and steps back go to the "
                         "place Jev picks from the client's places to stand), /spotsrev (the same, the places listed "
                         "the other way round), /codespots (code's pick of those places) or #<machine> (follow that "
                         "plan; uo-brain machine list)")
    bn.add_argument("--alias", action="append", default=[], metavar="NAME=MODEL",
                    help="a short name for a model, to use as a judge label (repeatable), e.g. qwen=qwen/qwen3.7-flash")
    add_model_args(bn)
    bn.add_argument("--rounds", type=int, default=10)
    bn.add_argument("--lane", type=int, default=0, help="test-field lane, so several clients can run at once")
    bn.add_argument("--out", type=Path, help="results JSON (default bench/<time>.json)")
    bn.add_argument("--min-confidence", type=float, default=policy.PolicyConfig.min_intent_confidence)
    bn.add_argument("--headless", action="store_true",
                    help="play without a window: hide the client's on --port, or start a headless one there if none "
                         "runs (logged in with --account and --character, and closed at the end)")
    add_account_args(bn)

    rp = sub.add_parser("report", help="summarise a decision log")
    rp.add_argument("log")

    co = sub.add_parser("costs", help="what the AI calls in one or more logs cost, by role, kind and model")
    co.add_argument("logs", nargs="*", type=Path, help="session or decision logs (a session's .decisions log is "
                                                       "read with it)")
    co.add_argument("--prices", action="store_true", help="update brain/prices.json from OpenRouter's list prices")
    co.add_argument("--key", action="store_true", help="show what the OpenRouter key has spent in all")
    co.add_argument("--json", action="store_true")

    rv = sub.add_parser("review", help="after-action review: the planner model proposes strategy lines from a log")
    rv.add_argument("log", type=Path)
    rv.add_argument("--accept", type=int, nargs="+", metavar="N",
                    help="add saved proposal N (or several) to the character's strategy; connects to the game, "
                         "doesn't ask the model again")
    rv.add_argument("--again", action="store_true", help="ask the model again although a review is saved")
    rv.add_argument("--digest", action="store_true", help="only print the digest the model would read")
    rv.add_argument("--shard", default="local", help="--accept: the store whose character memory keeps the lines")
    rv.add_argument("--planner-model", help=f"OpenRouter model (default {models.KIND_DEFAULTS['review']})")
    add_cost_log_arg(rv)

    sk = sub.add_parser("soak-report", help="report on an unattended session from its log (goals, loops, time, cost)")
    sk.add_argument("log", type=Path)
    sk.add_argument("--disruptions", type=Path, help="JSONL of {t, what} applied during the run")
    sk.add_argument("--json", action="store_true", help="print the report as JSON instead of Markdown")

    rpl = sub.add_parser("replay", help="re-ask a log's questions to another judge and compare answers")
    rpl.add_argument("log")
    rpl.add_argument("--judge", choices=["jev", "heuristic"], default="jev")
    rpl.add_argument("--provider", choices=["auto", "openrouter", "typesafe"], default="auto")
    rpl.add_argument("--model", help="Jev or any OpenRouter model, asked through the chat adapter (models.py)")
    rpl.add_argument("--limit", type=int, default=200)

    rpp = sub.add_parser("replay-planner", help="put a session log's planner moments to another model, offline")
    rpp.add_argument("log", type=Path)
    rpp.add_argument("--planner-model", required=True, help="any OpenRouter model with tool calling")
    rpp.add_argument("--limit", type=int, default=40)
    rpp.add_argument("--memories", metavar="CHARACTER",
                     help="ask each moment twice, as logged and with the memories Jev picks from what CHARACTER "
                          "remembered then (import earlier logs first: uo-brain memory import)")
    rpp.add_argument("--shard", default="local", help="--memories: the store (default local)")
    rpp.add_argument("--root", type=Path, help="--memories: folder that holds the shard stores")
    rpp.add_argument("--out", type=Path, help="--memories: also write the result as JSON here")

    fl = sub.add_parser("fleet", help="several agents on one server, each on its own models (a fleet file)")
    fl.add_argument("file", type=Path, help="e.g. brain/fleets/graveyard-three.toml")
    fl.add_argument("--hours", type=float, help="override the file's hours")
    fl.add_argument("--out", type=Path, help="folder for the logs (default logs/fleet/<time>-<name>)")
    fl.add_argument("--no-talk", action="store_true", help="nobody hears anybody: the control for a talk test")
    fl.add_argument("--windowed", action="store_true",
                    help="every client with its window, whatever the file says (to film a run: tools/video/film.py)")
    fl.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", dest="settings",
                    help="override one of the file's top-level settings for this run (repeatable), e.g. --set help=false "
                         "--set callouts=false for a party without either, to compare")
    flr = sub.add_parser("fleet-report", help="what each character of a fleet run did and cost, and the load")
    flr.add_argument("dir", type=Path)
    flr.add_argument("--json", action="store_true")
    pr = sub.add_parser("party-report", help="what a party did in a run as a party: each fight (who joined when, "
                                             "who fell, fall backs, who healed whom, callouts), deaths, forming up, cost")
    pr.add_argument("dirs", type=Path, nargs="+", metavar="DIR", help="a fleet run's folder, e.g. logs/fleet/RUN")
    pr.add_argument("--json", action="store_true", help="one JSON object a run, a line each")

    hr = sub.add_parser("hamlet-report", help="a hamlet's run (cuo-m3n): each resident's time in the hamlet and at "
                                               "home, homecomings, still spells away, work, talk by neighbour, cost")
    hr.add_argument("dirs", type=Path, nargs="+", metavar="DIR", help="a fleet run's folder, e.g. logs/fleet/RUN")
    hr.add_argument("--json", action="store_true", help="one JSON object a run, a line each")
    hr.add_argument("--shard", default="local", help="the store whose memories are read (default local)")
    hr.add_argument("--lands", action="append", default=[], metavar="AREA",
                    help="measure against these lands (hunting areas of the store; repeatable) a run whose file named "
                         "none, to set it beside one that did (cuo-m3n.3)")

    add_world_args(sub)
    add_memory_args(sub)
    add_goals_args(sub)

    mc = sub.add_parser("machine", help="plans for fights as state machines: list, show, or have the planner design one")
    mc.add_argument("action", choices=["list", "show", "design"])
    mc.add_argument("what", nargs="?", help="show: a name in brain/machines or a file; design: the situation in words")
    mc.add_argument("--archetype", default="warrior", help="design: how the character plays")
    mc.add_argument("--out", type=Path, help="design: save it here (default brain/machines/<name>.json)")
    mc.add_argument("--planner-model", help=f"OpenRouter model (default $PLANNER_MODEL or {llm.PLANNER_MODEL})")
    add_cost_log_arg(mc)

    ss = sub.add_parser("session", help="play towards a goal in words: the planner picks each goal, Jev fights")
    ss.add_argument("goal", nargs="?", default="",
                    help='e.g. "hunt the undead at the Britain graveyard, keep supplied with bandages, bank gold"; '
                         "left out, the planner chooses from the character's long goals (uo-brain goals)")
    ss.add_argument("--hours", type=float, default=1.0)
    ss.add_argument("--party", metavar="ID", help="play as one of a party (cuo-4ar): the run's party id, shared by its members")
    ss.add_argument("--party-members", metavar="NAMES", default="", help="the party's characters, comma-separated")
    ss.add_argument("--leader", metavar="NAME", help="the party's leader: it plans, the others follow its goals")
    ss.add_argument("--party-plan", choices=["leader", "huddle", "player"], default="leader",
                    help="how a party gets one plan: the leader's planner sets every goal and the others follow "
                         "(default); every member plans for itself with a plan they agree in a huddle (cuo-bih); or "
                         "the leader is a character a person plays, and this one its companion: it follows and "
                         "fights beside it with no planner (cuo-cym.6)")
    ss.add_argument("--no-callouts", action="store_true",
                    help="a party member that doesn't call out in fights (hurt, what it is on, out of supplies; cuo-cod.2)")
    ss.add_argument("--no-party-help", action="store_true",
                    help="a party member that doesn't heal or bandage companions or take what is on them (cuo-4ti)")
    ss.add_argument("--no-small-talk", action="store_true",
                    help="with --hear: talk only when there is something to say, no chit chat (cuo-cod.6)")
    ss.add_argument("--no-pace", action="store_true",
                    help="with --hear: say each line the moment it is written, as before cuo-cod.9")
    ss.add_argument("--pace-factor", type=float, default=1.0,
                    help="with --hear: the share of a person's time to read and type that a reply waits (1: all of "
                         "it; 0.7: seven tenths). A person who leads is answered in four fifths of that (cuo-cym.22)")
    ss.add_argument("--no-persuade", action="store_true",
                    help="with --party-plan player: a companion sent somewhere without the leader goes when told. "
                         "Without this, going is its own to agree to: a yes/no on the moment (cuo-cym.19)")
    ss.add_argument("--no-judge-reach", action="store_true",
                    help="a companion of a person's character fights only what the escort rule allows (in war mode within 4 "
                         "tiles of the leader or 6 of itself), and doesn't ask system one which creatures beyond it are the "
                         "leader's fight (cuo-cym.25)")
    ss.add_argument("--no-hear-leader", action="store_true",
                    help="a companion of a person's character hears only what its own client gets: the leader's lines a "
                         "wall keeps from it aren't passed on from the leader's own client (cuo-cym.28)")
    ss.add_argument("--leader-port", type=int, default=None,
                    help="the agent port of the client the person plays the leader in; without it, the client on this "
                         "machine whose character is the leader (cuo-cym.28)")
    ss.add_argument("--roleplay", action="store_true",
                    help="a party member that emotes and gestures in character: at the inn's table, on setting out, "
                         "resting, after a kill, over a fallen companion (cuo-cod.8)")
    ss.add_argument("--game-party", action="store_true",
                    help="make the party in the game too: the leader invites the others, each accepts its own "
                         "leader's invitation and no other, so all may loot the party's kills (cuo-cym.3)")
    ss.add_argument("--callouts-to", choices=["say", "party", "both"], default="say",
                    help="where a party's callouts are said: aloud (default), in the game's party chat (needs "
                         "--game-party), or both")
    ss.add_argument("--party-raise", action="store_true",
                    help="a party that raises its fallen where they fell: a ghost waits by its corpse for a companion "
                         "with the bandages and skill, or the Resurrection spell, before walking to a healer (cuo-cym.4)")
    ss.add_argument("--party-form-up", action="store_true",
                    help="a party that forms up at the edge of a hunt and goes in as one: the leader waits there for "
                         "the others, and a member that leaves falls back to that place (cuo-cym.1)")
    ss.add_argument("--party-rally-map", action="store_true",
                    help="with --party-form-up: the place the party falls back to is chosen from the client's map, a "
                         "few tiles back the way it came with a way out behind it, and a second point further back "
                         "for one badly hurt at the first (cuo-cym.13)")
    ss.add_argument("--party-walk", action="store_true",
                    help="a party that walks its trips together, the others beside the leader, and runs only in "
                         "danger or to catch up (cuo-cod.7)")
    ss.add_argument("--travel", choices=["walk", "run"],
                    help="the pace of every trip: walk (realistic: a run only with danger in view, to catch up, as a "
                         "ghost or for a corpse) or run; left out, the client's own setting (agent_travel_pace) (cuo-der)")
    ss.add_argument("--rules", metavar="NAME",
                    help="a set of rules of play from brain/rules/ (realistic, run-always): each flag not given takes "
                         "the set's value (cuo-der)")
    add_run_args(ss)

    do = sub.add_parser("do", help="one session goal: travel, bank, buy, sell, hunt or rest (uses the world store)")
    do.add_argument("--shard", default="local")
    dsub = do.add_subparsers(dest="goal", required=True)
    t = dsub.add_parser("travel", help="walk to a named place or x,y")
    t.add_argument("place")
    t.add_argument("--distance", type=int, default=2)
    t.add_argument("--timeout", type=float, default=600, help="give up after this many seconds (default 600)")
    t.add_argument("--defend", action="store_true",
                   help="fight what is close or attacking on the way, as the planner's trips do (Jev's defend-only loop; "
                        "never seeks or loots)")
    add_run_args(t, shard=False)
    b = dsub.add_parser("bank", help="at the nearest bank (or --at a town's): deposit gold/loot, withdraw supplies")
    b.add_argument("--deposit", default="gold,loot")
    b.add_argument("--withdraw", default="", help='e.g. "bandage:100,heal potion:5"')
    b.add_argument("--at", default=None, help="the town or place whose bank (default: the nearest)")
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
    add_run_args(h, shard=False)
    r = dsub.add_parser("rest")
    r.add_argument("seconds", type=float, nargs="?", default=30)
    r.add_argument("--defend", action="store_true",
                   help="fight what is close or attacking while resting, as the planner's rests do (Jev's defend-only loop)")
    add_run_args(r, shard=False)
    dsub.add_parser("resurrect", help="as a ghost, walk to the nearest healer and be resurrected")
    dsub.add_parser("recover-corpse", help="back to where the character died, take everything from its corpse")

    args = ap.parse_args()
    if args.cmd == "world":
        world_cmd(args)
        return
    if args.cmd == "memory":
        memory_cmd(args)
        return
    if args.cmd == "goals":
        goals_cmd(args)
        return
    if args.cmd == "report":
        report(Path(args.log))
        return
    if args.cmd == "costs":
        costs_cmd(args)
        return
    if args.cmd == "review" and not args.accept:
        review_cmd(args)
        return
    if args.cmd == "soak-report":
        from . import soak
        r = soak.report(args.log, args.disruptions)
        print(json.dumps(r, indent=2) if args.json else soak.markdown(r))
        return
    if args.cmd == "replay":
        asyncio.run(replay(args))
        return
    if args.cmd == "party-report":
        from . import partyreport
        for d in args.dirs:
            r = partyreport.report(d)
            print(json.dumps(r) if args.json else partyreport.render(r))
        return
    if args.cmd == "hamlet-report":
        from . import hamlet
        from .world import World
        with World.open(args.shard) as world:
            from .fleet import land_of
            lands = [land_of(world, name) for name in args.lands] or None
            for d in args.dirs:
                r = hamlet.report(d, world, lands=lands)
                print(json.dumps(r) if args.json else hamlet.markdown(r))
        return
    if args.cmd in ("fleet", "fleet-report"):
        from . import fleet
        try:
            r = fleet.main_run(args.file, args.out, args.hours, talk=not args.no_talk, windowed=args.windowed,
                               settings=args.settings) \
                if args.cmd == "fleet" \
                else fleet.report(args.dir)
        except (ValueError, OSError) as e:
            sys.exit(f"fleet: {e}" if str(e) else f"fleet: {type(e).__name__}")
        print(json.dumps(r, indent=2) if getattr(args, "json", False) else fleet.markdown(r))
        return
    if args.cmd == "replay-planner":
        from .planner import replay as replay_planner
        recs = [r for r in logs.read_session(args.log) if r.get("type") == "planner" and r.get("messages")]
        if not recs:
            sys.exit(f"no planner moments with their messages in {args.log} (logs from before 2026-10-08 have none)")
        if args.memories:
            print(json.dumps(asyncio.run(replay_with_memories(args, recs)), indent=2))
            return
        print(json.dumps(asyncio.run(replay_planner(recs[:args.limit], args.planner_model)), indent=2))
        return
    if args.cmd == "bench" and args.what != "run":
        bench_offline(args)
        return
    if args.cmd == "machine":
        machine_cmd(args)
        return
    if args.cmd == "client":
        asyncio.run(client_cmd(args))
        return
    started = None
    if args.cmd == "bench" and args.headless and not client.port_open(args.port):
        if not args.account or not args.character:
            sys.exit("bench --headless starts a client on --port when none runs there: it needs --account and --character")
        started = client.start(args.port, Path("logs") / "bench" / f"client-{args.port}.log", headless=True)
        print(f"started a headless client on port {args.port} (pid {started.pid})")
    try:
        asyncio.run(dispatch(args))
    finally:
        client.stop(started)


def add_account_args(p: argparse.ArgumentParser) -> None:
    """Which character a client logs in as, for commands that may start one."""
    p.add_argument("--account", help="log in with this account (created on first login)")
    p.add_argument("--password", help="default: the account name")
    p.add_argument("--character", help="play this character, made as a warrior if the account has none by that name")
    p.add_argument("--host", help="game server (default: the client's settings.json)")
    p.add_argument("--server-port", type=int, help="game server port (default: the client's settings.json)")


async def client_cmd(args) -> None:
    log = args.log or Path("logs") / f"client-{args.port}.log"
    try:
        proc = client.start(args.port, log, headless=args.headless, detach=True)
    except FileNotFoundError as e:
        sys.exit(str(e))
    print(f"started a{' headless' if args.headless else ''} client on port {args.port}, pid {proc.pid}, output in {log}"
          if proc else f"a client already listens on port {args.port}")
    rpc = AgentRpc(port=args.port)
    await rpc.connect()
    try:
        if args.headless and proc is None:
            await rpc.call("window", visible=False)
        if args.account:
            print(await client.log_in(rpc, args.account, args.password or args.account, args.character, args.host,
                                      args.server_port, create=client.warrior(args.character) if args.character else None))
        print(json.dumps(await rpc.call("status"), indent=2))
    finally:
        await rpc.close()


def add_model_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--profile", metavar="NAME|FILE",
                   help="the models for each AI role, and a budget (brain/profiles/<name>.toml; see models.py)")
    p.add_argument("--use", action="append", default=[], metavar="KIND=MODEL",
                   help="the model for one kind of call or a whole system, over the profile (repeatable): "
                        "system1, system2, " + ", ".join(costs.SYSTEM_ONE + costs.SYSTEM_TWO)
                        + "; cheaper1/cheaper2 for over a 'cheaper' budget")


def add_cost_log_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--cost-log", type=Path, metavar="FILE",
                   help="append an ai_cost record for each model call to this JSONL file (uo-brain costs FILE)")


@contextmanager
def cost_log(path: Path | None):
    """The ai_cost records of the calls made inside, appended to `path` (nothing without one).
    Yields the writer, or None."""
    if not path:
        yield None
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        def write(rec: dict) -> None:
            f.write(json.dumps(rec) + "\n")
            f.flush()
        with costs.ledger.logging_to(write):
            yield write


def add_run_args(p: argparse.ArgumentParser, shard: bool = True) -> None:
    """The options of anything that runs the fight loop. shard=False under `do`, whose own --shard
    would otherwise be overwritten by the goal's default ("do --shard X hunt" ran on local)."""
    p.add_argument("--judge", choices=["jev", "heuristic"], default="jev")
    p.add_argument("--provider", choices=["auto", "openrouter", "typesafe"], default="auto")
    p.add_argument("--model", help="system one's model: Jev (default ~typesafe/jev-latest on OpenRouter, jev-latest "
                                   "direct) or any OpenRouter model, asked through the chat adapter")
    add_model_args(p)
    p.add_argument("--mode", choices=["keep", "off", "assist", "auto"], default="keep",
                   help="set the client's agent mode first (default: leave as is)")
    p.add_argument("--archetype", choices=["auto", "warrior", "mage", "archer", "tamer", "bard", "necromancer", "paladin"],
                   default="auto",
                   help="how to play the character (default: tell from its skills)")
    p.add_argument("--duration", type=float, help="stop after this many seconds")
    p.add_argument("--strategy", type=Path, metavar="FILE", help="load this Markdown strategy into the character first")
    p.add_argument("--template", action="append", default=[], metavar="NAME",
                   help="pull this strategy template in first (repeatable; see: uo-brain strategy templates)")
    p.add_argument("--log", type=Path, help="append decisions to this JSONL file")
    p.add_argument("--min-confidence", type=float, default=policy.PolicyConfig.min_intent_confidence)
    p.add_argument("--spots", choices=["off", "jev", "rule"], default="off",
                   help="where a run, a leave or a step back goes (cuo-fhf): off, the client's way away from the "
                        "creatures (default); jev, the place Jev picks from the client's places to stand; rule, "
                        "code's pick of those places")
    p.add_argument("--leave-stop", choices=["rule", "jev"], default="rule",
                   help="when a leave may stop (cuo-fhf.6): rule, after 40 s or once nothing is within 14 tiles "
                        "(default); jev, when Jev says it is safe, within code's floors, and up to 90 s while it says not yet")
    p.add_argument("--budget-per-hour", type=float, metavar="USD",
                   help="a cap on what the AI calls cost an hour (judged over the last ten minutes)")
    p.add_argument("--over-budget", choices=["slow", "cheaper", "stop"], default="slow",
                   help="over the cap: decide less often (default), switch to the profile's cheaper models, or stop")
    if shard:
        p.add_argument("--shard", default="local", help="world store for the planner (run with a goal in auto mode)")
    p.add_argument("--planner-model", help=f"system two's model (default {llm.PLANNER_MODEL}, or $PLANNER_MODEL; --profile sonnet for Sonnet)")
    p.add_argument("--facts", choices=["jev", "all", "none"], default="jev",
                   help="world facts in the fight decisions: the few Jev picks (default), every shortlisted one, or none")
    p.add_argument("--machine", metavar="NAME|FILE",
                   help="follow this plan in fights (a state machine; see: uo-brain machine list); the planner may replace it")
    p.add_argument("--no-machines", action="store_true", help="don't let the planner set plans for the fights")
    p.add_argument("--no-escalate", action="store_true",
                   help="never hand the planner's hard moments to the stronger model (the profile's escalate, Sonnet)")
    p.add_argument("--no-memory", action="store_true",
                   help="plan without the character's memory and long goals (for comparing)")
    p.add_argument("--fresh", action="store_true",
                   help="start afresh: don't carry on from the planner's saved state for this goal")
    p.add_argument("--persona", type=Path, metavar="FILE",
                   help="a JSON persona (name, background, temperament, trade, wants) for an NPC's planner (cuo-x2q.1)")
    p.add_argument("--hamlet", type=Path, metavar="FILE",
                   help="a JSON hamlet (hamlet.Hamlet) this NPC lives in: its home, work and neighbours (cuo-m3n)")
    p.add_argument("--hear", metavar="NAMES",
                   help="comma-separated: the fleet's other characters, whose speech this NPC hears and answers "
                        "(local server only; everyone else's speech stays out) (cuo-x2q.2)")


def runner_of(args) -> "machines.Runner | None":
    if not getattr(args, "machine", None):
        return None
    try:
        return machines.Runner(machines.load(args.machine))
    except machines.MachineError as e:
        sys.exit(f"machine: {e}")


def machine_cmd(args) -> None:
    match args.action:
        case "list":
            for p in sorted(machines.MACHINES_DIR.glob("*.json")):
                m = machines.load(str(p))
                print(f"{p.stem:<16} {len(m.states)} states  {m.why}")
        case "show":
            if not args.what:
                sys.exit("machine show needs a name or a file")
            try:
                m = machines.load(args.what)
            except machines.MachineError as e:
                sys.exit(f"machine: {e}")
            print(m.describe())
        case "design":
            if not args.what:
                sys.exit('machine design needs the situation in words, e.g. "a mage swarmed in the open"')
            try:
                with cost_log(args.cost_log) as write:
                    m, usage = asyncio.run(machines.design(args.what, args.archetype, model=args.planner_model,
                                                           log=write))
            except (machines.MachineError, llm.LlmError) as e:
                sys.exit(f"machine: {e}")
            out = args.out or machines.MACHINES_DIR / f"{'-'.join(m.name.lower().split())[:40]}.json"
            out.write_text(json.dumps(m.to_json(), indent=2) + "\n")
            print(m.describe())
            print(f"\nsaved to {out} (${usage.cost or 0:.4f}, {usage.calls} call{'s' if usage.calls != 1 else ''})")


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

    mk = ws.add_parser("import-markers", help="places from a shard's world-map markers: UOAM .map or the client's CSV "
                                              "(each file replaces its own rows)")
    mk.add_argument("paths", nargs="+", type=Path, metavar="FILE|FOLDER",
                    help="marker files, or folders of them (.map, .csv, .usr)")
    mk.add_argument("--facets", choices=markers.NUMBERINGS, default="auto",
                    help="what the facet numbers mean: uoam (0 Felucca and Trammel, 1 Felucca, 2 Trammel, 3 Ilshenar, "
                         "4 Malas) or client (0 Felucca, 1 Trammel, 2 Ilshenar, 3 Malas, 4 Tokuno, 5 TerMur); "
                         "auto reads .map files the uoam way and CSV the client's way")
    mk.add_argument("--maps", help="comma-separated facets to keep (default all)")

    ig = ws.add_parser("import-guide", help="have the planner model turn a guide page into notes")
    ig.add_argument("source", metavar="URL|FILE")
    ig.add_argument("--area", help="the area the page is about")
    fg = ws.add_parser("fill-gaps", help="store what the planner model knows about an area, marked unverified")
    fg.add_argument("area")
    for p in (ig, fg):
        p.add_argument("--planner-model", help=f"OpenRouter model (default {models.KIND_DEFAULTS['guide']})")
        add_cost_log_arg(p)

    for p in (f, sp, h, pl, rt, nt):
        p.add_argument("--limit", type=int, default=5)


async def replay_with_memories(args, recs: list[dict]) -> dict:
    from . import memory as memories
    from .planner import replay_memories
    models.active = models.from_args(argparse.Namespace(profile=None, model=None, planner_model=None, use=[]))
    judge = models.make_judge("jev", "auto")
    world = worlds.World.open(args.shard, args.root)
    try:
        out = await replay_memories(recs[:args.limit], args.planner_model, memories.Memory(world, args.memories), judge)
    finally:
        await judge.close()
        world.close()
    out["log"] = str(args.log)
    if args.out:
        args.out.write_text(json.dumps(out, indent=2) + "\n")
    return out


def add_goals_args(sub) -> None:
    g = sub.add_parser("goals", help="a character's long goals, kept across sessions (does not connect to the game)")
    g.add_argument("--shard", default="local", help="which store: brain/worlds/<shard>/world.sqlite (default local)")
    g.add_argument("--root", type=Path, help="folder that holds the shard stores (default brain/worlds)")
    g.add_argument("--character", help="whose goals (list: everyone's when left out)")
    gs = g.add_subparsers(dest="goals_cmd", required=True)
    gl = gs.add_parser("list", help="the active ones with their progress")
    gl.add_argument("--all", action="store_true", help="done and dropped ones too")
    gl.add_argument("--json", action="store_true")
    ga = gs.add_parser("add", help='e.g. "reach 90 swordsmanship", "save 20000 gold", "learn the dungeons near Britain"')
    ga.add_argument("text")
    for name, what in (("done", "mark a goal done"), ("drop", "give a goal up")):
        gd = gs.add_parser(name, help=what)
        gd.add_argument("id", type=int)
        gd.add_argument("--why", default="")


def goals_cmd(args) -> None:
    from . import longgoals
    try:
        with worlds.World.open(args.shard, args.root) as w:
            lg = longgoals.LongGoals(w, args.character)
            if args.goals_cmd != "list" and not args.character:
                sys.exit(f"goals {args.goals_cmd}: name the character with --character")
            match args.goals_cmd:
                case "add":
                    gid = lg.add(args.text, source="player")
                    g = lg.get(gid)
                    print(f"{gid}. {g['text']} (measured by: {g['kind']}{', ' + json.dumps(g['target']) if g['target'] else ''})")
                case "done" | "drop":
                    ok = (lg.done if args.goals_cmd == "done" else lg.drop)(args.id, args.why or f"the player: {args.goals_cmd}")
                    print(f"{args.id}: {args.goals_cmd}" if ok else f"no long goal {args.id} for {args.character}")
                case _:
                    rows = lg.list(status=None if args.all else "active", all_characters=not args.character)
                    if args.json:
                        print(json.dumps(rows, indent=2))
                        return
                    for g in rows:
                        who = "" if args.character else f"{g['character']}: "
                        state = "" if g["status"] == "active" else f" [{g['status']}{': ' + g['why'] if g.get('why') else ''}]"
                        print(f"{g['id']:>4}. {who}{g['text']}: {longgoals.words(g)}{state}")
                    if not rows:
                        print("no long goals" + (f" for {args.character}" if args.character else "") + " yet")
    except (worlds.WorldError, OSError, ValueError) as e:
        sys.exit(f"goals: {e}")


def add_memory_args(sub) -> None:
    m = sub.add_parser("memory", help="a character's own memories, kept across sessions (does not connect to the game)")
    m.add_argument("--shard", default="local", help="which store: brain/worlds/<shard>/world.sqlite (default local)")
    m.add_argument("--root", type=Path, help="folder that holds the shard stores (default brain/worlds)")
    m.add_argument("--character", help="whose memories (list: everyone's when left out)")
    ms = m.add_subparsers(dest="memory_cmd", required=True)
    ls = ms.add_parser("list", help="newest first, or best match first with --search")
    ls.add_argument("--kind", choices=["event", "lesson", "order"])
    ls.add_argument("--area")
    ls.add_argument("--source", help="goal, death, leave, refusal, planner, review, flush or player")
    ls.add_argument("--search", help="words to look for")
    ls.add_argument("--stale", action="store_true", help="forgotten ones too")
    ls.add_argument("--limit", type=int, default=20)
    ls.add_argument("--json", action="store_true")
    ad = ms.add_parser("add", help="give the character a memory: a lesson, an event, or a standing order")
    ad.add_argument("text")
    ad.add_argument("--kind", choices=["event", "lesson", "order"], default="lesson")
    ad.add_argument("--area")
    ad.add_argument("--tag", action="append", default=[], help="repeatable")
    ad.add_argument("--importance", type=int, default=3, help="1 (minor) to 5 (never forget)")
    fg = ms.add_parser("forget", help="stop showing memories (they stay in the history, marked stale)")
    fg.add_argument("ids", type=int, nargs="+")
    im = ms.add_parser("import", help="backfill memories from earlier session logs (what code keeps as goals end)")
    im.add_argument("logs", type=Path, nargs="+", help="session logs (uo-brain session/run), oldest first")


def memory_cmd(args) -> None:
    from . import memory as memories
    try:
        with worlds.World.open(args.shard, args.root) as w:
            mem = memories.Memory(w, args.character)
            match args.memory_cmd:
                case "add":
                    if not args.character:
                        sys.exit("memory add: name the character with --character")
                    mid = mem.add(args.text, kind=args.kind, area=args.area, tags=args.tag, source="player",
                                  importance=args.importance)
                    print(json.dumps({"id": mid, "character": args.character, "kind": args.kind}))
                case "forget":
                    for i in args.ids:
                        print(f"{i}: {'forgotten' if mem.forget(i) else 'no such memory (or already forgotten)'}")
                case "import":
                    if not args.character:
                        sys.exit("memory import: name the character with --character")
                    for path in args.logs:
                        # A fleet's logs are named after their characters: keep the run's folder in the key.
                        name = logs.session_name(path)
                        key = name if path.parent.name == "logs" else f"{path.parent.name}/{name}"
                        print(json.dumps(memories.import_log(mem, logs.read_session(path), key)))
                case _:
                    rows = mem.list(kind=args.kind, area=args.area, search=args.search, source=args.source,
                                    include_stale=args.stale, limit=args.limit, all_characters=not args.character)
                    if args.json:
                        print(json.dumps(rows, indent=2))
                        return
                    for m in rows:
                        who = "" if args.character else f"{m['character']}: "
                        print(f"{m['id']:>5} {who}{memories.words(m)}  ({m['source']}, importance {m['importance']}"
                              f"{', forgotten' if m.get('stale') else ''})")
                    if not rows:
                        print("no memories" + (f" for {args.character}" if args.character else "") + " yet")
    except (worlds.WorldError, OSError, ValueError) as e:
        sys.exit(f"memory: {e}")


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
                case "import-markers":
                    out = markers.import_markers(w, args.paths, args.facets,
                                                 [m.strip() for m in args.maps.split(",")] if args.maps else None)
                case "import-guide":
                    with cost_log(args.cost_log):
                        out = asyncio.run(guides.import_guide(w, args.source, args.area, model=args.planner_model))
                case "fill-gaps":
                    with cost_log(args.cost_log):
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
            case "window":
                params = {} if args.action == "status" else {"visible": args.action == "show"}
                print(json.dumps(await rpc.call("window", **params)))
            case "login":
                await login(rpc, args)
            case "run":
                await run_loop(rpc, args)
            case "scenario":
                await scenario(rpc, args)
            case "bench":
                await bench(rpc, args)
            case "sight-check":
                await sight_check(rpc, args)
            case "do":
                await do_goal(rpc, args)
            case "session":
                await session_cmd(rpc, args)
            case "review":
                from . import memory as memories
                snap = await rpc.call("snapshot", since=0)
                who = (snap.get("player") or {}).get("name") if snap.get("in_game") else None
                world = worlds.World.open(args.shard)
                try:
                    said, now = await reviews.accept(rpc, args.log, args.accept, memories.Memory(world, who))
                except ValueError as e:
                    sys.exit(f"review: {e}")
                finally:
                    world.close()
                if who:
                    said.append(f"(kept in {who}'s memory as lessons: uo-brain memory --character {who} list --source review)")
                print("\n".join(said))
                print(f"\nThe strategy now:\n{now or '(none)'}")
    finally:
        await rpc.close()


async def sight_check(rpc: AgentRpc, args) -> None:
    spots = [None] if args.here else sightcheck.BRITAIN_SPOTS[:args.spots]
    res = await sightcheck.run(rpc, spots, args.per_spot, args.radius, args.seed)
    out = args.out or Path(__file__).resolve().parents[2] / "bench" / f"{time.strftime('%Y-%m-%d')}-sight-check.json"
    sightcheck.write(res, out)
    kinds = ", ".join(f"{k} {v['agree']}/{v['pairs']}" for k, v in res["by_kind"].items())
    print(f"{res['agree']}/{res['pairs']} pairs agree ({100 * (res['rate'] or 0):.1f}%): {kinds}; {out}")


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
    """The judge for a run, after making its profile the active one (models.py): the models for
    each kind of call, and the profile's budget unless --budget-per-hour gave one."""
    try:
        models.active = models.from_args(args)
    except ValueError as e:
        sys.exit(str(e))
    if models.active.budget and getattr(args, "budget_per_hour", None) is None:
        costs.ledger.budget = models.active.budget
    return models.make_judge(args.judge, args.provider)


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
    lcfg = loop.LoopConfig(duration_s=args.duration)
    set_budget(args)
    pcfg = policy.PolicyConfig(min_intent_confidence=args.min_confidence, spots=args.spots, leave_stop=args.leave_stop)
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
                await Autopilot(rpc, judge, world, lcfg, pcfg, args.log, archetype, args.planner_model,
                                facts_mode=args.facts, machine=runner_of(args),
                                machines_allowed=not args.no_machines, escalate=not args.no_escalate,
                                memory=not args.no_memory).run(stop)
            finally:
                world.close()
            return loop.RunStats()
        stats = await loop.run(rpc, judge, lcfg, pcfg, args.log, stop, archetype=archetype, machine=runner_of(args))
    finally:
        await judge.close()
    print(json.dumps(stats.summary(), indent=2))
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
        results.append(stats.summary())
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

    applied = rules.apply(args, args.rules) if args.rules else {}
    world = World.open(args.shard)
    judge = make_judge(args)
    set_budget(args)
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
                      pcfg=policy.PolicyConfig(spots=args.spots, leave_stop=args.leave_stop),
                      decisions_log=args.log.with_suffix(".decisions.jsonl") if args.log else None)
    session.facts_mode = args.facts
    session.machine = runner_of(args)
    session.travel_pace = args.travel
    if args.rules or args.travel:
        write({"type": "rules", "t": time.time(), "rules": args.rules, "applied": applied, "travel": args.travel})
    from .recorder import Recorder
    session.recorder = Recorder(world, judge)
    party = follower = link = None
    me = ""
    persona = json.loads(args.persona.read_text()) if args.persona else None
    if args.hamlet:
        # A resident of a hamlet (cuo-m3n): its planner knows the place, travel_to 'home' goes home, and its talk
        # knows where it lives and who its neighbours are.
        from . import hamlet as hamlets
        session.hamlet = hamlets.load(args.hamlet)
        write({"type": "hamlet", "t": time.time(), **session.hamlet.to_json()})
    if args.party or args.hear:
        me = (await rpc.call("status")).get("player") or ""
    if args.party:
        from . import voice as voices
        from .mates import Mates
        from .party import Follower, Party
        members = [n.strip() for n in args.party_members.split(",") if n.strip()]
        party = Party(world, args.party, me, args.leader or me, members or [me])
        # The party in a fight (mates.py): what is on whom, helping each other, and this member's callouts
        # in its own voice (written once, kept in the store).
        with costs.ledger.logging_to(write):
            # Its voice is its emotes as well as its callouts: with callouts off and roleplay on it still has one.
            spoken = await voices.make(world, me, persona, [m for m in party.members if m != me], log=write) \
                if not args.no_callouts or args.roleplay else None
        from .talk import Floor
        link = Mates(party, spoken, callouts=not args.no_callouts, log=write,
                     floor=Floor(world, me, set(party.members)))
        # companions: the same dict, filled as they report.
        session.pcfg = replace(session.pcfg, companions=party.companions, link=link,
                               help_mates=not args.no_party_help,
                               raise_fallen=args.party_raise and not args.no_party_help)
        session.raise_fallen = args.party_raise and not args.no_party_help  # nobody raises with helping off
        party.on_death = link.died
        if args.party_walk:
            # The party walks its trips together (cuo-cod.7): the leader waits for the others, who walk beside it.
            session.pace = "walk"
            session.leads = party.leading and args.party_plan == "leader"
            session.beside = None if party.leading or args.party_plan == "huddle" else party.leader
        if args.game_party:
            # The party in the game too (gameparty.py, cuo-cym.3); its callouts where the fleet says.
            from .gameparty import GameParty
            link.game = GameParty(rpc, party, log=write)
            link.to = args.callouts_to
        if args.party_form_up and args.party_plan != "player":
            # The party forms up at the edge of a hunt and goes in as one (rally.py, cuo-cym.1): on its leader,
            # or with a planner each on whoever gets there first (cuo-cym.5).
            from .rally import Rally
            session.rally = link.rally = Rally(session, party, log=write, plan=args.party_plan,
                                               by_map=args.party_rally_map)
            session.rally.shout = link.shout
            session.leads_party = party.leading and args.party_plan == "leader"
        rp = None
        if args.roleplay:
            from .roleplay import Roleplay
            rp = Roleplay(rpc, link.voice, link.floor, log=write, follower=not party.leading)
            session.roleplay = link.roleplay = rp
        write({"type": "party", "t": time.time(), "party": args.party, "me": me, "leader": party.leader,
               "members": party.members, "callouts": not args.no_callouts, "help": not args.no_party_help,
               "form_up": session.rally is not None, "game_party": link.game is not None, "raise": args.party_raise,
               **({"callouts_to": link.to} if link.to != "say" else {}),
               "voice": "plain" if spoken is None or spoken.plain else "its own"})
    huddling = party is not None and args.party_plan == "huddle"
    table = None
    if party is not None and args.hear and not huddling:
        # A party that talks has a table at the inn (table.py): the leader's planner gets the inn goal. A
        # party for the player holds it at the leader's word (companion.py).
        from .table import Table
        table = Table(party, rpc, persona, link.voice, None, link.story, log=write, floor=link.floor,
                      pace=not args.no_pace, emotes=args.roleplay)
        party.inn = party.leading and args.party_plan != "player"
        table.guest = party.leader if args.party_plan == "player" else None
    elif party is None and args.hear and session.hamlet is not None and session.hamlet.gather:
        # A hamlet's evenings (cuo-m3n.5): the neighbours' table at the gathering place, in its hamlet scene, shared
        # in the store by the run's residents; what each says there is from its own day (hamlet.DayStory).
        from types import SimpleNamespace
        from .hamlet import DayStory
        from .table import Table
        hamlet = session.hamlet
        run = hamlet.run or (Path(args.log).parent.name if args.log else "")  # the run's folder: the same for each
        neighbourhood = SimpleNamespace(world=world, me=me, members=[me, *(n["name"] for n in hamlet.neighbours)],
                                        party=f"hamlet:{hamlet.name}:{run}")
        table = Table(neighbourhood, rpc, persona, None, None, DayStory(), log=write, pace=not args.no_pace,
                      scene="hamlet", place=hamlet.gather["words"])
    huddle = None
    if huddling:
        # Every member plans for itself, with the plan they agree in front of it (huddle.py, cuo-bih).
        from .huddle import Huddle
        huddle = Huddle(party, session, args.goal, log=write)
        if session.rally is not None:
            session.rally.sends = huddle.going  # the rally waits for whom the plan sends (cuo-cym.14)
    escorting = party is not None and args.party_plan == "player"
    plan = Planner(session, args.goal, model=args.planner_model, log=write, on_goal=show,
                   machines_allowed=not args.no_machines, escalate=not args.no_escalate,
                   memory=False if args.no_memory else None,
                   persona=persona, resume=not args.fresh,
                   party=party, table=table, huddle=huddle) \
        if (party is None or party.leading or huddling) and not escorting else None
    own_memory = plan.memory if plan else None
    if huddle is not None:
        if own_memory is not None and not own_memory.character:
            own_memory.character = me or None
        huddle.memory, huddle.picker = own_memory, plan.picker  # what it brings is from its own memory
    companion = None
    if escorting:
        # A companion of a character a person plays: no planner and no leader's goals (companion.py, cuo-cym.6).
        from .companion import Companion
        # Its word on taking an order is its own when it has a voice of its own, and on the floor either way, so
        # the others hear it without answering it.
        companion = Companion(session, party, log=write, game=link.game, table=table, floor=link.floor,
                              voice=link.voice if not link.voice.plain else None, link=link,
                              persuade=not args.no_persuade, judge_reach=not args.no_judge_reach)
    relay = None
    if escorting and not args.no_hear_leader:
        # The leader's lines a wall kept from this companion, from the leader's own client (relay.py, cuo-cym.28).
        from .relay import Relay
        relay = Relay(party.leader, rpc, port=args.leader_port, me_port=args.port, log=write)
    if party is not None and not party.leading and not huddling:
        follower = Follower(session, party, log=write, table=table) if not escorting else None
        if not args.no_memory:
            # A follower has no planner, and still a memory of its own (cuo-cod.1): what it hears and says is
            # kept as every character's is, private to it.
            from . import memory as memories
            own_memory = memories.Memory(world, me, session=logs.session_name(args.log) if args.log else None)
    if own_memory is not None and not own_memory.character:
        own_memory.character = me or None
    if table is not None:
        from .table import tactics
        table.memory = own_memory
        if link is not None:
            link.tactics = tactics(own_memory)  # its own lines on how to fight, from earlier tables (cuo-cod.5)
        if table.scene == "hamlet":
            table.story.planner = plan  # a resident's day, from its planner's goals since the last evening
    stop = asyncio.Event()

    def ending() -> None:
        # ...and the goal under way is cut short: the planner reads `stop` only between goals, and asked to stop at
        # a fleet's end, two brains finished a 300 s rest and a walk to a bank first, after the fleet had gone (cuo-7z4).
        stop.set()
        session.interrupt.set()

    # A closed terminal (SIGHUP) or a `kill` (SIGTERM) ends the run as Ctrl-C does, with its summary: a
    # playtest's three brains went with their terminal and left none (cuo-cym.23).
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        asyncio.get_running_loop().add_signal_handler(sig, ending)
    await rpc.call("mode", mode="auto")
    mark = costs.ledger.mark()
    key_check(write)
    listening = reporting = watching = ears = partying = relaying = None

    def doing() -> str:
        if companion is not None:
            return companion.doing
        if follower is not None:
            return follower.doing
        return plan.history[-1].line()[:200] if plan.history else ""

    if args.hear:
        from .talk import Ears
        ears = Ears(rpc, me, {n.strip() for n in args.hear.split(",")}, judge=judge,
                    persona={**(persona or {}), "lives": session.hamlet.lives()} if session.hamlet else persona,
                    memory=own_memory, log=write, doing=doing, world=world,
                    pace=not args.no_pace, small_talk=not args.no_small_talk, banter=party is not None,
                    person=party.leader if escorting else None, pace_factor=args.pace_factor)
        if companion is not None:
            # What it says of a line of the leader's goes by how its companion half read the line, and how much
            # it talks is a setting the leader can change.
            companion.hears(ears)
        if plan:
            plan.ears = ears
        if table is not None:
            table.ears = ears
    try:
        with costs.ledger.logging_to(write):
            if session.roleplay is not None:
                await session.play("meet")  # a bow as the party comes together; the others salute their leader
            if ears:
                listening = asyncio.create_task(ears.run(stop))
            if party is not None:
                reporting = asyncio.create_task(party.keep_reporting(
                    session.snap, state.archetype_of, doing, stop, left=session.last_leave,
                    busy=lambda: bool(follower is not None and follower.own or companion is not None and companion.own
                                      or huddling and plan is not None and plan.on_errand),
                    hunting=lambda: session.hunting_area))
            if link is not None and link.game is not None:
                partying = asyncio.create_task(link.game.keep(session.snap, stop))
            if huddling:
                watching = asyncio.create_task(plan.huddle.watch(stop))
            if relay is not None:
                relaying = asyncio.create_task(relay.run(stop))
            summary = await (companion.run(stop, args.hours) if companion else follower.run(stop, args.hours)
                             if follower else plan.run(hours=args.hours, stop=stop))
    finally:
        for task in (listening, reporting, watching, partying, relaying):
            if task:
                task.cancel()
        await judge.close()
        world.close()
    summary |= session.routine_summary()
    if link is not None:
        summary |= link.to_log()
    if session.rally is not None:
        summary |= session.rally.to_log()
    if link is not None and link.game is not None:
        summary |= link.game.to_log()
    if ears is not None and plan is None:
        summary |= ears.to_log()  # a follower's talk; a planner's summary has its own
    if table is not None:
        summary |= table.to_log()
    if relay is not None:
        summary["relay"] = relay.report()
    if session.roleplay is not None:
        summary |= session.roleplay.to_log()
    summary["costs"] = costs.ledger.summary(mark, kills=summary.get("kills"))
    key_check(write)
    write({"type": "session_summary", "t": time.time(), **summary})
    if log:
        log.close()
    print(json.dumps(summary, indent=2))


async def do_goal(rpc: AgentRpc, args) -> None:
    from .session import Session
    from .world import World

    world = World.open(args.shard)
    judge = make_judge(args) if args.goal == "hunt" or getattr(args, "defend", False) else None
    if judge is not None:
        set_budget(args)
    log = args.log.open("a") if getattr(args, "log", None) else None
    archetype = getattr(args, "archetype", "auto")
    session = Session(rpc, world, judge, pcfg=policy.PolicyConfig(spots=getattr(args, "spots", "off"),
                                                                  leave_stop=getattr(args, "leave_stop", "rule")),
                      log=lambda rec: print(json.dumps(rec)) if not log else
                      log.write(json.dumps(rec) + "\n"),
                      archetype=None if archetype == "auto" else archetype,
                      decisions_log=args.log.with_suffix(".decisions.jsonl") if log else None)
    await rpc.call("mode", mode="auto")  # a session goal is auto-mode play
    try:
        match args.goal:
            case "travel":
                xy = args.place.split(",")
                if len(xy) == 2 and all(v.strip().lstrip("-").isdigit() for v in xy):
                    trip = session.travel_to(x=int(xy[0]), y=int(xy[1]), distance=args.distance, timeout_s=args.timeout)
                else:
                    trip = session.travel_to(args.place, distance=args.distance, timeout_s=args.timeout)
                res = await (session.defended(trip) if args.defend else trip)
            case "bank":
                res = await session.bank(args.deposit, args.withdraw, args.at)
            case "buy":
                res = await session.buy(args.item, args.count, args.vendor)
            case "sell":
                res = await session.sell(args.items, args.vendor)
            case "hunt":
                session.facts_mode = args.facts
                session.machine = runner_of(args)
                res = await session.hunt(args.area, args.minutes)
            case "resurrect":
                res = await session.resurrect()
            case "recover-corpse":
                res = await session.recover_corpse()
            case _:
                res = await (session.defended(session.rest(args.seconds)) if args.defend else session.rest(args.seconds))
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
        case "archetypes":
            return list(benchmark.ARCHETYPES)
        case "world":
            return list(benchmark.WORLD)
        case "packs":
            return list(benchmark.PACKS)
        case "sight":
            return list(benchmark.SIGHT)
        case "spots":
            return list(benchmark.SPOTS)
        case "all":
            return list(benchmark.SCENARIOS)
    names = [n.strip() for n in spec.split(",") if n.strip()]
    if unknown := [n for n in names if n not in benchmark.SCENARIOS]:
        sys.exit(f"unknown scenario(s): {', '.join(unknown)} (see: uo-brain bench list)")
    return names


async def bench(rpc: AgentRpc, args) -> None:
    try:
        models.active = models.from_args(args)
        for spec in args.alias:
            name, _, model = spec.partition("=")
            if not model.strip() or "/" in name or "+" in name or "#" in name or "@" in name:
                raise ValueError(f"--alias wants NAME=MODEL, a name without / + # @; not {spec!r}")
            models.active.aliases[name.strip()] = model.strip()
    except ValueError as e:
        sys.exit(str(e))
    if args.headless:
        await rpc.call("window", visible=False)
    if args.account:
        said = await client.log_in(rpc, args.account, args.password or args.account, args.character, args.host,
                                   args.server_port, create=client.warrior(args.character) if args.character else None)
        if not said.startswith("in game"):
            sys.exit(f"bench: {said}")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = args.out or Path("bench") / f"{stamp}.json"
    # Lanes run side by side: two benches started in the same second shared one log folder.
    logs = Path("logs") / "bench" / (stamp if args.lane == 0 else f"{stamp}-lane{args.lane}")
    st = await rpc.call("status")
    result = await benchmark.run(rpc, bench_names(args.scenarios), [j.strip() for j in args.judges.split(",")],
                                 args.rounds, args.lane, out, logs,
                                 min_confidence=args.min_confidence,
                                 fact_modes=[m.strip() for m in args.facts.split(",") if m.strip()],
                                 meta={"headless": bool(st.get("headless")), "clients_at_start": client.clients_running()})
    print(benchmark.table([result]))
    print(f"results: {out}")


def bench_offline(args) -> None:
    if args.what == "list":
        for name, sc in benchmark.SCENARIOS.items():
            fixed = f" [template {sc.template}]" if sc.template else " [world facts: none, all, jev]" if sc.seed else ""
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
            with costs.kind("replay"):
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
    lat = sorted(latencies)
    print(json.dumps({
        "decisions": len(ds),
        "judge": judge.name,
        "agreement": {q: round(same[q] / asked[q], 3) if asked[q] else None for q in same},
        "intent_logged_to_new": dict(sorted(confusion.items(), key=lambda kv: -kv[1])),
        "latency_ms_avg": round(sum(lat) / len(lat), 1) if lat else None,
        "latency_ms_p95": round(lat[int(0.95 * (len(lat) - 1))], 1) if lat else None,
        "unanswered_questions": getattr(judge, "missing", 0),
        "costs": costs.ledger.summary(decisions=len(ds)),
    }, indent=2))


def review_cmd(args) -> None:
    """The review without the game: a saved one is shown again unless --again."""
    if not args.log.exists():
        sys.exit(f"review: no log at {args.log}")
    if args.digest:
        print(json.dumps(reviews.digest(logs.read_session(args.log), logs.session_name(args.log)), indent=2))
        return
    saved = reviews.review_path(args.log)
    if saved.exists() and not args.again:
        print(reviews.show(reviews.load(saved), saved=True))
        return
    try:
        with cost_log(args.cost_log):
            rv = asyncio.run(reviews.review(args.log, model=args.planner_model))
    except (llm.LlmError, ValueError) as e:
        sys.exit(f"review: {e}")
    print(reviews.show(rv))


def key_check(write) -> None:
    """OpenRouter's own count of what the key has spent, into the log at a session's start and
    end, so `uo-brain costs LOG` can check the recorded costs against it."""
    if (usage := costs.openrouter_usage()) is not None:
        write({"type": "openrouter_key", "t": time.time(), "usage": usage})


def set_budget(args) -> None:
    if getattr(args, "budget_per_hour", None) is not None:
        costs.ledger.budget = costs.Budget(args.budget_per_hour, args.over_budget)


def costs_cmd(args) -> None:
    if args.prices:
        print(f"{costs.update_prices()} model prices saved to {costs.PRICES_FILE}")
    if args.key:
        spent = costs.openrouter_usage()
        print("OpenRouter key: " + ("no key or no answer" if spent is None else f"${spent:.4f} spent in all"))
    if not args.logs:
        if not (args.prices or args.key):
            sys.exit("costs needs a log (or --prices, --key)")
        return
    files = {f.resolve() for p in args.logs for f in (p, logs.companion(p)) if f.exists()}
    records = sorted((r for f in files for r in logs.read(f)), key=lambda r: r.get("t") or 0.0)
    s = costs.from_records(records, kills=None)
    checks = [r for r in records if r.get("type") == "openrouter_key"]
    if s and len(checks) >= 2:
        key_spent = checks[-1]["usage"] - checks[0]["usage"]
        s["openrouter_key_spent_usd"] = round(key_spent, 6)
        s["recorded_vs_key"] = round(s["cost_usd"] / key_spent, 3) if key_spent > 0 else None
    print(json.dumps(s, indent=2) if args.json else costs.describe(s) + (
        f"\nOpenRouter's own count for the key over the same run: ${s['openrouter_key_spent_usd']:.4f}"
        if s and "openrouter_key_spent_usd" in s else ""))


def report(path: Path) -> None:
    decisions = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    made = [d for d in decisions if d["type"] == "decision"]
    ds = [d for d in made if not d.get("not_asked")]  # put to Jev; the rest were decided on no call (cuo-df8)
    if not made:
        print("no decisions")
        return
    kills = sum((s.get("client_stats") or {}).get("kills", 0) for s in decisions if s["type"] == "summary")
    lat = sorted(d["answers"]["latency_ms"] for d in ds) or [0.0]
    intents: dict[str, int] = {}
    for d in made:
        intents[d["intent"]] = intents.get(d["intent"], 0) + 1
    conf = [d["confidence"] for d in made]
    not_asked: dict[str, int] = {}
    for d in made:
        if d.get("not_asked"):
            not_asked[d["not_asked"]] = not_asked.get(d["not_asked"], 0) + 1
    print(json.dumps({
        "decisions": len(ds),
        "not_asked": not_asked,
        "judge": sorted({d["judge"] for d in made}),
        "intents": intents,
        "gated": sum(d["gated"] for d in made),
        "confidence_avg": round(sum(conf) / len(conf), 3),
        "latency_ms_p50": lat[len(lat) // 2],
        "latency_ms_p95": lat[int(0.95 * (len(lat) - 1))],
        "input_tokens": sum(d["answers"]["input_tokens"] for d in ds),
        "costs": costs.from_records(decisions, kills=kills or None, decisions=len(ds)),
        "summaries": [d for d in decisions if d["type"] == "summary"],
    }, indent=2))


if __name__ == "__main__":
    main()
