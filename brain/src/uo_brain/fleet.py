"""Several agents on one server, each on its own models (cuo-m70.4).

The vision is a server with smart NPCs playing beside people, the player's character on the
stronger models and the NPCs on the cheapest that still play well. A fleet file names the
characters, each with its own client (an agent port), account, model profile (models.py) and
goal in words:

    name = "graveyard-three"
    server_port = 2593
    hours = 0.5

    [[agent]]
    name = "Brutus"                 # the character (created as a warrior if it doesn't exist)
    account = "warrior"
    password = "warrior"
    port = 5577                     # its client's agent port
    profile = "default"
    goal = "Hunt the undead at the Britain graveyard ..."
    prep = ["[AgentReset", "[AgentGo 1425 1690", "[AgentKit warrior", "[AgentSupplies bandages 30 gold 400"]
    headless = true                 # its client without a window (cuo-1k7); `headless` at the top sets the default

An agent's `env` table goes into its brain's environment (`env = { UO_BRAIN_ASK_EVERY_TICK = "1" }`
puts one agent on the fight loop as it was before cuo-df8, to compare the two in one fleet).

A party (cuo-4ar) plays on one plan: with `party = true` and `leader = "NAME"` at the top, the leader's
planner chooses the goals and the others follow them (party.py): they travel, hunt and rest with the
leader, fight beside it, and buy their own supplies when it calls a restock. Only the leader needs a
goal. Each run is its own party, so a run never follows an earlier one's goals.

An NPC can instead have a life of its own (cuo-x2q.1): a persona, put in its planner's system
prompt, and its own long goals, kept with its memories (longgoals.py) and added at the start if
it doesn't have them yet. With no `goal`, it chooses its own days from them.

    persona = { background = "...", temperament = "...", trade = "...", wants = "..." }
    long_goals = ["save 5000 gold for a smithy", "reach 90 swordsmanship"]

A fleet can be a hamlet's residents (cuo-m3n, hamlet.py): a `[hamlet]` table names a region of the world
store, and each agent its `home` (a place or "x,y"), `home_words` and `work`. Each is told it lives there,
the fleet samples where everyone is every 30 s, and `uo-brain hamlet-report DIR` measures them against it.
`--set hamlet=false` tells them nothing and still measures (the control).

`uo-brain fleet FILE` starts a client for each agent that hasn't one running (from bin/osx-arm64,
headless where the file says so; client.py),
logs it in, runs its prep commands, then runs `uo-brain session` for it as its own process, so
each brain has its own ledger: one character's AI costs. While they play it samples the CPU and
memory of every client, brain and the server. At the end, and with `uo-brain fleet-report DIR`, it
puts together what each character did and cost (soak.py's report of each log) and what the fleet
cost an hour together, with the judge errors (rate limits show there) and the load.
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import client, costs, logs, rules, soak
from .rpc import AgentRpc, RpcError

BRAIN_DIR = client.BRAIN_DIR


@dataclass
class Agent:
    name: str
    account: str
    password: str
    port: int
    goal: str
    profile: str = "default"
    prep: list[str] = field(default_factory=list)
    persona: dict[str, str] = field(default_factory=dict)
    long_goals: list[str] = field(default_factory=list)
    memories: list[str] = field(default_factory=list)   # seeded as events at the start (a test's setup)
    talk: bool = True                                    # hears and answers the others (with a persona)
    headless: bool = False  # its client hides its window and draws nothing (client.py)
    env: dict[str, str] = field(default_factory=dict)  # for its brain, e.g. UO_BRAIN_ASK_EVERY_TICK = "1"
    travel: str = ""        # walk or run: the pace of its trips, over the fleet's (cuo-der)
    strategy: str | None = None  # its standing strategy for the run, over the fleet's; None: the fleet's
    home: str = ""          # a hamlet's resident: its home, a place or "x,y" (cuo-m3n)
    home_words: str = ""    # ...how it speaks of it ("the healer's house")
    work: str = ""          # ...and its work, in words


@dataclass
class Fleet:
    name: str
    agents: list[Agent]
    hours: float = 0.5
    server_port: int = 2593
    host: str = "127.0.0.1"
    party: bool = False  # one plan for all, the leader's (cuo-4ar)
    leader: str = ""
    callouts: bool = True  # a party's members call out in fights (cuo-cod.2)
    help: bool = True      # ...and heal, bandage and take creatures off each other (cuo-4ti)
    small_talk: bool = True  # characters who talk also chat when they have time (cuo-cod.6)
    pace: bool = True        # ...and answer at a human pace, one speaker at a time (cuo-cod.9)
    # ...for this share of the time a person takes to read and type (`pace = 0.7` in the file; true is 1). A
    # person who leads is answered in four fifths of it whatever it is (talk.PERSON_PACE, cuo-cym.22).
    pace_factor: float = 1.0
    # A companion sent somewhere without the leader a person plays goes if it is willing: a yes/no on the
    # moment, and it may say no (Harry: "if I can persuade them to do so"). false: it goes when told (cuo-cym.19).
    persuade: bool = True
    # ...and which creatures beyond the escort rule's tiles are the leader's fight is put to system one (cuo-cym.25).
    judge_reach: bool = True
    # The leader's lines a wall kept from a companion are passed on from the leader's own client (cuo-cym.28); the
    # client is the one on this machine whose character is the leader, or the one on leader_port.
    hear_leader: bool = True
    leader_port: int = 0
    roleplay: bool = False   # a party's members emote and gesture in character (cuo-cod.8)
    memory: bool = True      # false: every brain runs without its character's memory, so repeat runs are independent
    plan: str = "leader"     # a party's one plan: the leader's (cuo-4ar), or agreed in a huddle, a planner each (cuo-bih)
    walk: bool = False       # a party walks its trips together and runs only in danger (cuo-cod.7)
    form_up: bool = False    # a party forms up at the edge of a hunt, goes in as one and falls back there (cuo-cym.1)
    rally_map: bool = False  # ...to a rally point chosen from the client's map, with a second one behind it (cuo-cym.13)
    game_party: bool = False  # the party is made in the game too: invitations, loot rights, party chat (cuo-cym.3)
    raise_fallen: bool = False  # a party raises its fallen where they fell (cuo-cym.4); `raise` in the file
    callouts_to: str = "say"  # where callouts are said: say (aloud), party (the game's party chat) or both
    party_id: str = ""       # this run's party in the store (set when the run starts)
    use: list[str] = field(default_factory=list)  # KIND=MODEL for every brain, over its profile (session --use)
    travel: str = ""         # walk or run: every trip's pace; "" leaves it to each client's agent_travel_pace (cuo-der)
    rules: str = ""          # the set of rules of play under these settings (brain/rules/, cuo-der)
    # Every character's standing strategy for the run ("" for none); None leaves each as its client has it.
    # A character keeps its strategy between runs (it is the player's own words, saved with its profile), so
    # a fleet that measures something says what it is: the lich scene's five had carried a video shoot's
    # "never flee: the party stands together" into every later run, some with old planner lines after it.
    strategy: str | None = None
    # A hamlet its agents live in (cuo-m3n): name (a region of the world store), edge (tiles round it that count),
    # told (false: the residents aren't told, and are still measured against it: the control).
    hamlet: dict[str, Any] | None = None


def parse_setting(spec: str) -> tuple[str, Any]:
    """KEY=VALUE from the command line, the value read as TOML would (true, 0.5, "words")."""
    key, sep, value = spec.partition("=")
    if not sep or not key.strip():
        raise ValueError(f"--set wants KEY=VALUE, not {spec!r}")
    try:
        return key.strip(), tomllib.loads(f"v = {value.strip()}")["v"]
    except tomllib.TOMLDecodeError:
        return key.strip(), value.strip()


def load(path: Path, settings: list[str] | None = None) -> Fleet:
    data = tomllib.loads(Path(path).read_text())
    hamlet = data.get("hamlet")  # the file's: `--set hamlet=false` keeps it to measure by, and tells nobody
    for spec in settings or []:  # this run's overrides of the file's top-level settings
        key, value = parse_setting(spec)
        data[key] = value
    if data.get("rules"):
        # A set of rules under the file's own settings: one the file (or --set) gives wins (cuo-der).
        data = {**rules.load(str(data["rules"])), **data}
    travel = str(data.get("travel") or "")
    for where, pace in [("travel", travel)] + [(f"{a.get('name')}'s travel", str(a.get("travel") or ""))
                                               for a in data.get("agent") or []]:
        if pace not in ("", "walk", "run"):
            raise ValueError(f"{path}: {where} should be walk or run, not {pace!r}")
    headless = bool(data.get("headless", False))
    agents = [Agent(str(a["name"]), str(a["account"]), str(a["password"]), int(a["port"]), str(a.get("goal") or ""),
                    str(a.get("profile", "default")), [str(c) for c in a.get("prep") or []],
                    {str(k): str(v) for k, v in (a.get("persona") or {}).items()},
                    [str(g) for g in a.get("long_goals") or []], [str(m) for m in a.get("memories") or []],
                    bool(a.get("talk", data.get("talk", True))), bool(a.get("headless", headless)),
                    {str(k): str(v) for k, v in (a.get("env") or {}).items()}, str(a.get("travel") or ""),
                    str(a["strategy"]) if "strategy" in a else None, str(a.get("home") or ""),
                    str(a.get("home_words") or ""), str(a.get("work") or ""))
              for a in data.get("agent") or []]
    if hamlet is not None:
        if not isinstance(hamlet, dict) or not hamlet.get("name"):
            raise ValueError(f"{path}: [hamlet] needs a name (a region of the world store, e.g. \"Cove\")")
        hamlet = {"name": str(hamlet["name"]), "edge": int(hamlet.get("edge", 10)),
                  "told": data.get("hamlet") is not False, "lands": [str(x) for x in hamlet.get("lands") or []],
                  **({"gather": str(hamlet["gather"]), "gather_words": str(hamlet.get("gather_words") or ""),
                      "day_minutes": float(hamlet.get("day_minutes", 60)),
                      "evening_minutes": float(hamlet.get("evening_minutes", 15))} if hamlet.get("gather") else {})}
        if homeless := [a.name for a in agents if not a.home]:
            raise ValueError(f"{path}: in a hamlet every agent needs a home: {', '.join(homeless)}")
    party = bool(data.get("party", False))
    leader = str(data.get("leader") or (agents[0].name if party and agents else ""))
    plan = str(data.get("plan", "leader"))
    if plan not in ("leader", "huddle", "player"):
        raise ValueError(f"{path}: plan should be leader, huddle or player, not {plan!r}")
    # The player's party (cuo-cym.6): its leader is a character a person plays, and not one of the agents.
    if party and plan == "player" and (not data.get("leader") or leader in {a.name for a in agents}):
        raise ValueError(f"{path}: with plan = \"player\" the leader is the character the person plays, by name, "
                         "and not one of the agents")
    if party and plan != "player" and leader not in {a.name for a in agents}:
        raise ValueError(f"{path}: the party's leader {leader!r} isn't one of its agents")
    callouts_to = str(data.get("callouts_to", "say"))
    if callouts_to not in ("say", "party", "both"):
        raise ValueError(f"{path}: callouts_to should be say, party or both, not {callouts_to!r}")
    pace = data.get("pace", True)  # true, false, or the share of a person's time a reply waits
    if not isinstance(pace, (bool, int, float)) or pace < 0:
        raise ValueError(f"{path}: pace should be true, false or a number from 0 up (0.7: seven tenths of the wait), "
                         f"not {pace!r}")
    if party and plan == "huddle":
        # A planner each: one who has no goal of its own is given the party's (the leader's).
        shared = next((a.goal for a in agents if a.name == leader), "")
        for a in agents:
            a.goal = a.goal or shared
    for a in agents:
        if not a.goal and not a.long_goals and not (party and a.name != leader):
            raise ValueError(f"{path}: {a.name} needs a goal or long_goals")  # a companion of the player needs none
    if not agents:
        raise ValueError(f"{path}: no [[agent]] entries")
    if len({a.port for a in agents}) != len(agents) or len({a.account for a in agents}) != len(agents):
        raise ValueError(f"{path}: each agent needs its own port and account")
    return Fleet(str(data.get("name") or Path(path).stem), agents, float(data.get("hours", 0.5)),
                 int(data.get("server_port", 2593)), str(data.get("host", "127.0.0.1")), party, leader,
                 callouts=bool(data.get("callouts", True)), help=bool(data.get("help", True)),
                 small_talk=bool(data.get("small_talk", True)), pace=bool(pace),
                 pace_factor=1.0 if isinstance(pace, bool) else float(pace),
                 persuade=bool(data.get("persuade", True)), judge_reach=bool(data.get("judge_reach", True)),
                 hear_leader=bool(data.get("hear_leader", True)), leader_port=int(data.get("leader_port", 0)),
                 roleplay=bool(data.get("roleplay", False)), walk=bool(data.get("walk", False)),
                 form_up=bool(data.get("form_up", False)), game_party=bool(data.get("game_party", False)),
                 rally_map=bool(data.get("rally_map", False)),
                 callouts_to=callouts_to, raise_fallen=bool(data.get("raise", False)),
                 memory=bool(data.get("memory", True)), plan=plan,
                 use=[str(u) for u in ([data["use"]] if isinstance(data.get("use"), str) else data.get("use") or [])],
                 travel=travel, rules=str(data.get("rules") or ""),
                 strategy=str(data["strategy"]) if "strategy" in data else None, hamlet=hamlet)


def hamlet_of(fleet: Fleet, shard: str = "local", root: Path | None = None) -> dict[str, Any] | None:
    """The fleet's hamlet with its area and each resident's home resolved from the world store: name, rects, edge,
    told, homes (name -> x, y, words), and each agent's Hamlet as it knows it (hamlet.Hamlet)."""
    if not fleet.hamlet:
        return None
    from .hamlet import Hamlet
    from .world import World, WorldError
    with World.open(shard, root) as w:
        region = next((r for m in w.regions().by_map.values() for r in m
                       if r.name.lower() == fleet.hamlet["name"].lower()), None)
        if region is None:
            raise ValueError(f"no region called {fleet.hamlet['name']!r} in the world store")
        homes = {}
        for a in fleet.agents:
            try:
                x, y, label = w.resolve(a.home)
            except WorldError as e:
                raise ValueError(f"{a.name}'s home: {e}") from None
            homes[a.name] = {"x": x, "y": y, "words": a.home_words or label}
        lands = [land_of(w, name) for name in fleet.hamlet.get("lands") or []]
        gather = None
        if fleet.hamlet.get("gather"):
            # Where the neighbours meet of an evening (cuo-m3n.5).
            try:
                gx, gy, glabel = w.resolve(fleet.hamlet["gather"])
            except WorldError as e:
                raise ValueError(f"the hamlet's gathering place: {e}") from None
            gather = {"name": glabel, "x": gx, "y": gy, "words": fleet.hamlet.get("gather_words") or glabel}
    rects = [tuple(r) for r in region.rects]
    began = time.time()  # the run's first day, the same clock for every resident
    known = {}
    for a in fleet.agents:
        others = [{"name": o.name, "trade": o.persona.get("trade", ""), "home_words": homes[o.name]["words"],
                   "home": f"{homes[o.name]['x']},{homes[o.name]['y']}", **({"work": o.work} if o.work else {})}
                  for o in fleet.agents if o.name != a.name]
        known[a.name] = Hamlet(region.name, rects, fleet.hamlet["edge"], a.name, (homes[a.name]["x"], homes[a.name]["y"]),
                               homes[a.name]["words"], a.work, others, lands, gather,
                               fleet.hamlet.get("day_minutes", 60) * 60, fleet.hamlet.get("evening_minutes", 15) * 60,
                               began)
    return {"name": region.name, "rects": [list(r) for r in rects], "edge": fleet.hamlet["edge"],
            "told": fleet.hamlet["told"], "homes": homes, **({"lands": lands} if lands else {}),
            **({"gather": gather, "began": began} if gather else {}), "known": known}


def land_of(world: Any, name: str) -> dict[str, Any]:
    """One of a hamlet's lands (cuo-m3n.3): a hunting area of the world store, as the square round its spawns (their
    centre, and the farthest one's distance from it plus its spawn range and LAND_EDGE)."""
    from .hamlet import LAND_EDGE, chebyshev
    from .world import WorldError
    try:
        spawns = world.what_spawns(area=name, limit=100)
    except WorldError:
        spawns = []
    if not spawns:
        raise ValueError(f"the hamlet's land {name!r} is no hunting area of the world store")
    cx = round(sum(s["x"] for s in spawns) / len(spawns))
    cy = round(sum(s["y"] for s in spawns) / len(spawns))
    r = max(chebyshev(cx, cy, s["x"], s["y"]) + min(int(s.get("range") or 10), 15) for s in spawns) + LAND_EDGE
    return {"name": str(spawns[0].get("area") or name), "x": cx, "y": cy, "r": r}


# ---------------------------------------------------------------- running it

def start_client(agent: Agent, out_dir: Path) -> subprocess.Popen | None:
    """A client for the agent (headless if it says so), unless one already listens on its port."""
    return client.start(agent.port, out_dir / f"{agent.name}.client.log", headless=agent.headless)


async def log_in(fleet: Fleet, agent: Agent) -> str:
    """In game as the agent's character (created as a warrior if missing), its prep done."""
    rpc = AgentRpc(port=agent.port)
    await rpc.connect(attempts=90)
    try:
        result = await client.log_in(rpc, agent.account, agent.password, agent.name, fleet.host, fleet.server_port,
                                     create=client.warrior(agent.name))
        if not result.startswith("in game"):
            # Once more: a login can stick between the login server and the game server (the server
            # "force disconnecting stuck socket", 2026-10-10), and a party then ran without its fifth member,
            # whose prep spawns what the others were to fight.
            try:
                again = await client.log_in(rpc, agent.account, agent.password, agent.name, fleet.host, fleet.server_port,
                                            create=client.warrior(agent.name))
            except (asyncio.TimeoutError, RpcError) as e:
                # A client busy connecting doesn't answer for some seconds: give it a moment, then ask once more.
                await asyncio.sleep(10)
                try:
                    again = await client.log_in(rpc, agent.account, agent.password, agent.name, fleet.host,
                                                fleet.server_port, create=client.warrior(agent.name))
                except (asyncio.TimeoutError, RpcError):
                    again = f"not in game: {type(e).__name__}"
            if not again.startswith("in game"):
                return f"{result} (and at a second try: {again[:120]})"
            result = f"{again} (at the second try)"
        if agent.headless:
            # A client that was already running (not started here) goes headless too.
            await rpc.call("window", visible=False)
        await rpc.call("mode", mode="off")
        await rpc.call("goal", clear=True)
        strategy = agent.strategy if agent.strategy is not None else fleet.strategy
        if strategy is not None:
            await rpc.call("strategy", **({"text": strategy} if strategy else {"clear": True}))
        for cmd in agent.prep:
            await rpc.call("act", verb="say", text=cmd, source="manual")
            await asyncio.sleep(1.5)
        return result
    finally:
        await rpc.close()


LOGGING_IN = ("connecting", "server_selected", "entering", "creating")


async def relog(fleet: Fleet, agent: Agent) -> str:
    """A client that lost its connection (back at the login screen, cuo-m12) logged in again and put
    back in auto mode for its session; "" when it is in game, or busy logging in, or unreachable."""
    rpc = AgentRpc(port=agent.port)
    try:
        await rpc.connect(attempts=2)
    except OSError:
        return ""
    try:
        st = await rpc.call("status")
        # Back at the login screen with no login of the brain's under way (the last one's state stays
        # "in_game" after a lost connection).
        if st.get("in_game") or st.get("scene") != "login" or st.get("login_state") in LOGGING_IN:
            return ""
        result = await client.log_in(rpc, agent.account, agent.password, agent.name, fleet.host, fleet.server_port)
        if result.startswith("in game"):
            await rpc.call("mode", mode="auto")
        return result
    except Exception as e:  # noqa: BLE001 - try again at the next look
        return f"error: {type(e).__name__}: {e}"[:200]
    finally:
        await rpc.close()


def seed_long_goals(agent: Agent, shard: str = "local", root: Path | None = None) -> list[int]:
    """The agent's long goals from the fleet file, added to its store unless it has them already."""
    from .longgoals import LongGoals
    from .world import World
    if not agent.long_goals:
        return []
    with World.open(shard, root) as w:
        goals = LongGoals(w, agent.name)
        have = {g["text"].lower() for g in goals.list(status=None)}  # done or dropped ones stay so
        return [goals.add(t, source="fleet") for t in agent.long_goals if t.lower() not in have]


def seed_memories(agent: Agent, shard: str = "local", root: Path | None = None) -> int:
    """A test's setup: memories the agent should start with (an hour old, importance 4), added once."""
    from . import memory as memories
    from .world import World
    if not agent.memories:
        return 0
    with World.open(shard, root) as w:
        mem = memories.Memory(w, agent.name)
        have = {m["text"] for m in mem.list(limit=200)}
        new = [t for t in agent.memories if t not in have]
        for t in new:
            mem.add(t, kind="event", source="player", importance=4, at=time.time() - 3600)
        return len(new)


def start_brain(fleet: Fleet, agent: Agent, out_dir: Path, hamlet: dict[str, Any] | None = None) -> subprocess.Popen:
    seed_long_goals(agent)
    seed_memories(agent)
    # --fresh: a fleet run doesn't pick up an earlier run's planner state, so repeats are independent.
    cmd = [sys.executable, "-m", "uo_brain.cli", "--port", str(agent.port), "session", agent.goal,
           "--hours", str(fleet.hours), "--profile", agent.profile, "--log", str(out_dir / f"{agent.name}.jsonl"),
           "--fresh"]
    for spec in fleet.use:
        cmd += ["--use", spec]
    if pace := agent.travel or fleet.travel:
        cmd += ["--travel", pace]
    if not fleet.memory:
        # A measurement's repeat runs: a leader that remembers the last run's deaths goes elsewhere in the next.
        cmd += ["--no-memory"]
    if fleet.party:
        # One party a run: its id is the fleet's name and the run's folder.
        members = [a.name for a in fleet.agents]
        if fleet.plan == "player":
            members.insert(0, fleet.leader)  # the character a person plays: one of the party, with no brain here
        cmd += ["--party", fleet.party_id or f"{fleet.name}-{out_dir.name}", "--party-members", ",".join(members),
                "--leader", fleet.leader]
        cmd += [] if fleet.callouts else ["--no-callouts"]
        cmd += [] if fleet.help else ["--no-party-help"]
        cmd += ["--roleplay"] if fleet.roleplay else []
        cmd += ["--party-walk"] if fleet.walk else []
        cmd += ["--party-form-up"] if fleet.form_up else []
        cmd += ["--party-rally-map"] if fleet.rally_map else []
        cmd += ["--game-party"] if fleet.game_party else []
        cmd += ["--party-raise"] if fleet.raise_fallen else []
        cmd += ["--callouts-to", fleet.callouts_to] if fleet.callouts_to != "say" else []
        cmd += ["--party-plan", fleet.plan] if fleet.plan != "leader" else []
        cmd += ["--no-persuade"] if fleet.plan == "player" and not fleet.persuade else []
        cmd += ["--no-judge-reach"] if fleet.plan == "player" and not fleet.judge_reach else []
        cmd += ["--no-hear-leader"] if fleet.plan == "player" and not fleet.hear_leader else []
        cmd += ["--leader-port", str(fleet.leader_port)] if fleet.plan == "player" and fleet.leader_port else []
    if agent.persona:
        persona = out_dir / f"{agent.name}.persona.json"
        persona.write_text(json.dumps({"name": agent.name, **agent.persona}, indent=2))
        cmd += ["--persona", str(persona)]
    if hamlet and hamlet["told"]:
        # A resident of the hamlet (cuo-m3n): its home, work and neighbours, for its planner and its talk.
        where = out_dir / f"{agent.name}.hamlet.json"
        hamlet["known"][agent.name].run = out_dir.name  # the evening's table: one for the run's residents
        where.write_text(json.dumps(hamlet["known"][agent.name].to_json(), indent=2))
        cmd += ["--hamlet", str(where)]
    # NPCs with lives of their own hear the fleet's other characters, and only them (cuo-x2q.2); a party
    # hears itself, persona or not (cuo-cod.1). `talk = false` in the file: nobody hears anybody.
    others = [a.name for a in fleet.agents if a.name != agent.name] + ([fleet.leader] if fleet.plan == "player" else [])
    if agent.talk and (agent.persona or fleet.party) and others:
        cmd += ["--hear", ",".join(others)]
        cmd += [] if fleet.small_talk else ["--no-small-talk"]
        cmd += [] if fleet.pace else ["--no-pace"]
        cmd += ["--pace-factor", str(fleet.pace_factor)] if fleet.pace and fleet.pace_factor != 1.0 else []
    return subprocess.Popen(cmd, cwd=BRAIN_DIR, stdout=(out_dir / f"{agent.name}.out").open("w"),
                            stderr=subprocess.STDOUT, env={**os.environ, **agent.env} if agent.env else None)


def sample(pids: dict[str, int]) -> dict[str, dict[str, float]]:
    """CPU (percent of one core) and memory (MB) of each named process, from ps, and its physical
    footprint (MB) where macOS's footprint tool gives it: what Activity Monitor calls memory,
    which counts the GPU memory a client's textures take and ps's resident size leaves out."""
    alive = {k: v for k, v in pids.items() if v}
    if not alive:
        return {}
    out = subprocess.run(["ps", "-o", "pid=,%cpu=,rss=", "-p", ",".join(str(p) for p in alive.values())],
                         capture_output=True, text=True).stdout
    by_pid = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3:
            by_pid[int(parts[0])] = {"cpu": float(parts[1]), "mb": round(int(parts[2]) / 1024, 1)}
    for pid, mb in footprints(list(by_pid)).items():
        by_pid[pid]["footprint_mb"] = mb
    return {k: by_pid[p] for k, p in alive.items() if p in by_pid}


def footprints(pids: list[int]) -> dict[int, float]:
    """Physical footprint (MB) of each process, from `footprint` (macOS); {} where it isn't there."""
    import re
    try:
        out = subprocess.run(["footprint", "--noCategories", "-f", "bytes", *map(str, pids)],
                             capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    return {int(m[1]): round(int(m[2]) / 2**20, 1) for m in re.finditer(r"\[(\d+)\]: .*?Footprint: (\d+) B", out)}


async def loop_rates(agents: list[Agent]) -> dict[str, dict[str, Any]]:
    """Each client's game loop over the last second (updates, frames drawn) and whether it is
    headless: a hidden client that macOS slowed down (App Nap) would show it here."""
    rates: dict[str, dict[str, Any]] = {}
    for a in agents:
        rpc = AgentRpc(port=a.port)
        try:
            await rpc.connect(attempts=1)
            st = await rpc.call("status", timeout=3)
            rates[f"{a.name}/client"] = {"ups": st.get("updates_per_s"), "draws": st.get("draws_per_s"),
                                         "headless": st.get("headless")}
        except (OSError, asyncio.TimeoutError, RuntimeError, RpcError):
            pass  # a client that doesn't answer has no rate this time
        finally:
            await rpc.close()
    return rates


async def positions(agents: list[Agent]) -> list[dict[str, Any]]:
    """Where each agent is now (a hamlet's run, cuo-m3n): a `where` record each, for hamlet.report."""
    out = []
    for a in agents:
        rpc = AgentRpc(port=a.port)
        try:
            await rpc.connect(attempts=1)
            snap = await rpc.call("snapshot", radius=1, timeout=5)
            if snap.get("in_game"):
                p = snap["player"]
                out.append({"type": "where", "agent": a.name, "x": p["x"], "y": p["y"], "map": p.get("map"),
                            "hits": p.get("hits"), "dead": p.get("dead")})
        except (OSError, asyncio.TimeoutError, RuntimeError, RpcError):
            pass  # a client that doesn't answer has no position this time
        finally:
            await rpc.close()
    return out


BRAIN_ENDS_S = 15.0  # a brain asked to stop has this long to write its summary before it is ended


def stop_brains(brains: dict[str, subprocess.Popen], within_s: float = BRAIN_ENDS_S) -> None:
    """The brains still running are asked to stop (Ctrl-C: each ends its run and writes its summary), and one
    that hasn't gone in time is ended. They were left to outlive the fleet before: stopped by hand, a run's
    brains played on against clients that had been closed, until someone ended each by its process id."""
    running = [b for b in brains.values() if b.poll() is None]
    for b in running:
        try:
            b.send_signal(signal.SIGINT)
        except OSError:
            pass
    gone_by = time.monotonic() + within_s
    ended = []
    for b in running:
        try:
            b.wait(max(0.1, gone_by - time.monotonic()))
        except subprocess.TimeoutExpired:
            b.terminate()
            ended.append(b)
    # A brain's own handler takes SIGTERM as a stop too (cli.ending): one that still hasn't gone is killed, or it plays
    # on after the fleet (cuo-7z4).
    gone_by = time.monotonic() + min(within_s, 5.0)
    for b in ended:
        try:
            b.wait(max(0.1, gone_by - time.monotonic()))
        except subprocess.TimeoutExpired:
            b.kill()


def pid_listening(port: int) -> int:
    """The process listening on a TCP port (the game server), or 0."""
    out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], capture_output=True, text=True).stdout
    return int(out.split()[0]) if out.split() else 0


async def run(fleet: Fleet, out_dir: Path, every_s: float = 30.0) -> dict[str, Any]:
    # The brains are started from the brain folder: a folder given relative to somewhere else (the repository's
    # root, with `uv run --project brain`) was made here and then not found by them, and nobody played.
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    # One party a run. A second run into a folder used before (`--out`) is another party: with the first one's id
    # its followers would carry out that run's last goal at once (a "finish" ends all of them).
    again = (out_dir / "fleet.jsonl").exists() and (out_dir / "fleet.jsonl").stat().st_size > 0
    fleet.party_id = f"{fleet.name}-{out_dir.name}" + (f"-{int(time.time())}" if again else "")
    events = (out_dir / "fleet.jsonl").open("a")

    def note(rec: dict[str, Any]) -> None:
        events.write(json.dumps({"t": time.time(), **rec}) + "\n")
        events.flush()

    hamlet = hamlet_of(fleet)
    note({"type": "fleet", "name": fleet.name, "hours": fleet.hours,
          **({"hamlet": {k: v for k, v in hamlet.items() if k != "known"}} if hamlet else {}),
          **({"party": True, "party_id": fleet.party_id, "leader": fleet.leader, "callouts": fleet.callouts, "help": fleet.help,
              "roleplay": fleet.roleplay, "walk": fleet.walk, "form_up": fleet.form_up, "game_party": fleet.game_party,
              **({"rally_map": True} if fleet.rally_map else {}),
              "raise": fleet.raise_fallen,
              **({"callouts_to": fleet.callouts_to} if fleet.callouts_to != "say" else {}),
              "small_talk": fleet.small_talk, "pace": fleet.pace if fleet.pace_factor == 1.0 or not fleet.pace else fleet.pace_factor,
              **({"persuade": fleet.persuade, "judge_reach": fleet.judge_reach, "hear_leader": fleet.hear_leader}
                 if fleet.plan == "player" else {}),
              "travel": fleet.travel, "rules": fleet.rules}
             if fleet.party else {}), **({} if fleet.memory else {"memory": False}),
          **({"plan": fleet.plan} if fleet.plan != "leader" else {}), **({"use": fleet.use} if fleet.use else {}),
          **({"strategy": fleet.strategy} if fleet.strategy is not None else {}),
          "agents": [{"name": a.name, "port": a.port, "profile": a.profile, "goal": a.goal, "headless": a.headless,
                      **({"env": a.env} if a.env else {})}
                     for a in fleet.agents]})
    clients = {a.name: start_client(a, out_dir) for a in fleet.agents}
    # Ctrl-C, a closed terminal or a `kill` ends the run in order: the brains first, each with its summary, then
    # the clients. A playtest's run that went with its terminal left no summaries at all (cuo-cym.23).
    stopping = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, stopping.set)
        except (NotImplementedError, RuntimeError, ValueError):  # not the main thread (a test): Ctrl-C as before
            pass
    brains: dict[str, subprocess.Popen] = {}
    stopped = asyncio.ensure_future(stopping.wait())
    try:
        for a in fleet.agents:
            if stopping.is_set():
                break
            try:
                # A login takes up to minutes (a client still starting, a stuck login tried again): stopped
                # meanwhile, the run ends now and not when the last of them is in.
                login = asyncio.ensure_future(log_in(fleet, a))
                await asyncio.wait({login, stopped}, return_when=asyncio.FIRST_COMPLETED)
                if not login.done():
                    login.cancel()
                    break
                result = login.result()
            except (OSError, RpcError, RuntimeError) as e:
                # A client that didn't answer in time (a TimeoutError, whose text is empty) ended a whole fleet
                # with the message "fleet: " and nothing else; that agent sits the run out and the others play.
                result = f"not in game: {type(e).__name__}: {e}".rstrip(": ")
            note({"type": "login", "agent": a.name, "result": result})
            if fleet.party:
                # A party's brains start one by one, each as soon as its character is in and prepared: it fights
                # back from then on, and its leader's planner waits for the others (planner.wait_for_party).
                # Started together after the last login, a leader stood with its agent off for half a minute
                # while a stray bone knight killed it and the archer beside it.
                brains[a.name] = start_brain(fleet, a, out_dir, hamlet)
        for a in fleet.agents:
            if a.name not in brains and not stopping.is_set():
                brains[a.name] = start_brain(fleet, a, out_dir, hamlet)
        pids = {"server": pid_listening(fleet.server_port)}
        for a in fleet.agents:
            proc = clients[a.name]
            pids[f"{a.name}/client"] = proc.pid if proc else pid_listening(a.port)
            if a.name in brains:
                pids[f"{a.name}/brain"] = brains[a.name].pid
        while any(b.poll() is None for b in brains.values()) and not stopping.is_set():
            try:
                await asyncio.wait_for(stopping.wait(), every_s)
                break
            except asyncio.TimeoutError:
                pass
            note({"type": "load", "processes": sample(pids), "loops": await loop_rates(fleet.agents)})
            if hamlet:
                for rec in await positions(fleet.agents):
                    note(rec)
            for a in fleet.agents:
                if brains[a.name].poll() is None and (result := await relog(fleet, a)):
                    note({"type": "relogin", "agent": a.name, "result": result})
    finally:
        stopped.cancel()
        if stopping.is_set():
            note({"type": "stopped", "brains_running": sorted(n for n, b in brains.items() if b.poll() is None)})
        stop_brains(brains)
        # Whatever ended the run, the clients it started go with it: left running, three of them held their
        # ports and the next run waited on them for good.
        for c in clients.values():
            if c is not None:
                c.terminate()
        # ...and one that doesn't go when asked is killed: a client hung in a stuck login ignored the request
        # and held its port (2026-10-12), so the next run waited on it until someone looked.
        gone_by = time.monotonic() + 10
        for c in clients.values():
            if c is not None:
                try:
                    c.wait(max(0.1, gone_by - time.monotonic()))
                except subprocess.TimeoutExpired:
                    c.kill()
        events.close()
    return report(out_dir)


# ---------------------------------------------------------------- the report

def talk_report(session: list[dict[str, Any]], others: list[str]) -> dict[str, Any]:
    """An NPC's talk (talk.py, cuo-x2q.2/.3): lines heard and said, what talk cost, and the goals that
    followed from a conversation: chosen within 10 minutes of talk, naming the other character in its
    reason, or going to the place a conversation was about."""
    talk = [x for x in session if x.get("type") == "talk"]
    said = [x for x in talk if x.get("said")]
    heard = [x for x in talk if x.get("heard")]
    if not talk:
        return {}
    cost = sum(x.get("cost") or 0 for x in session if x.get("type") == "ai_cost" and x.get("kind") in ("chat", "talk"))
    places = {str(x.get("about") or "").lower() for x in said if x.get("about")}
    changed = []
    for g in (x for x in session if x.get("type") == "goal"):
        if not any(0 <= g["t"] - h["t"] <= 600 for h in heard + said):
            continue
        args = g.get("args") or {}
        why = str(args.get("why", "")).lower()
        target = str(args.get("area") or args.get("place") or "").lower()
        # The other character named in the reason, or a goal going to (or kept from) the place a talk was about.
        if any(n.lower() in why for n in others) or (target and target in places):
            changed.append({"t": g["t"], "tool": g.get("tool"), "why": str(args.get("why", ""))[:200]})
    minutes = max((session[-1]["t"] - session[0]["t"]) / 60, 1e-9) if session else 1e-9
    return {"lines_said": len(said), "lines_heard": len(heard), "lines_per_hour": round(len(said) * 60 / minutes, 1),
            "talk_cost_usd": round(cost, 5), "talk_cost_per_hour_usd": round(cost * 60 / minutes, 4),
            "talk_gave": [x.get("gives") for x in said if x.get("gives") not in (None, "none")],
            "goals_after_talk": changed}


def report(out_dir: Path) -> dict[str, Any]:
    """What each character did and cost, and the fleet together: cost an hour, per character and per
    kill, judge errors, and the load on the machine."""
    records = logs.read(out_dir / "fleet.jsonl")
    head = next((r for r in records if r["type"] == "fleet"), {"agents": []})
    agents = {}
    all_costs: list[dict[str, Any]] = []
    logins = {x["agent"]: x["result"] for x in records if x["type"] == "login"}
    for a in head["agents"]:
        log = out_dir / f"{a['name']}.jsonl"
        login = logins.get(a["name"], "")
        r = soak.report(log) if log.exists() else {"error": "no session log"}
        if "error" in r or not login.startswith("in game"):
            # Never played: say why (a refused login, say: "ip already has 10 accounts").
            agents[a["name"]] = {"profile": a.get("profile"), "error": login or r.get("error", "")}
            continue
        session = [x for x in logs.read_session(log)]
        all_costs += [x for x in session if x.get("type") == "ai_cost"]
        errors = sum(1 for x in session if x.get("type") == "error")
        summary = next((x for x in reversed(session) if x.get("type") == "session_summary"), {})
        agents[a["name"]] = {"profile": a["profile"], "minutes": r.get("minutes"), "hunts": r.get("hunts"),
                             "kills": r.get("kills"), "deaths": r.get("deaths"), "gold_banked": r.get("gold_banked"),
                             "finished": summary.get("finished"), "cost_usd": r.get("cost_usd"),
                             "cost_per_hour_usd": r.get("cost_per_hour_usd"), "judge_errors": errors,
                             "fight_decisions": r.get("fight_decisions"),
                             "fight_decisions_per_hour": r.get("fight_decisions_per_hour"),
                             "fight_decisions_not_asked": r.get("fight_decisions_not_asked"),
                             "models": sorted((r.get("costs") or {}).get("by_model", {})),
                             **talk_report(session, [x["name"] for x in head["agents"] if x["name"] != a["name"]])}
    loads = [x["processes"] for x in records if x["type"] == "load"]
    load: dict[str, dict[str, float]] = {}
    for name in sorted({k for s in loads for k in s}):
        cpu = [s[name]["cpu"] for s in loads if name in s]
        mb = [s[name]["mb"] for s in loads if name in s]
        load[name] = {"cpu_avg": round(sum(cpu) / len(cpu), 1), "cpu_max": max(cpu), "mb_max": max(mb)}
        if fp := [s[name]["footprint_mb"] for s in loads if "footprint_mb" in s.get(name, {})]:
            load[name]["footprint_mb_max"] = max(fp)
    for a in head["agents"]:
        if a.get("headless") and f"{a['name']}/client" in load:
            load[f"{a['name']}/client"]["headless"] = True
    loops = [x.get("loops") or {} for x in records if x["type"] == "load"]
    for name in load:
        ups = [s[name]["ups"] for s in loops if name in s and s[name].get("ups") is not None]
        if ups:
            load[name]["ups_avg"], load[name]["ups_min"] = round(sum(ups) / len(ups)), min(ups)
    ts = [x["t"] for x in all_costs]
    hours = max((max(ts) - min(ts)) / 3600, 1e-9) if ts else None
    together = costs.from_records(all_costs, hours=hours) if all_costs else None
    kills = sum(a.get("kills") or 0 for a in agents.values())
    return {"fleet": head.get("name"), "dir": str(out_dir), "agents": agents,
            "together": {"cost_usd": together["cost_usd"] if together else 0.0,
                         "cost_per_hour_usd": together["cost_per_hour_usd"] if together else None,
                         "cost_per_kill_usd": round(together["cost_usd"] / kills, 5) if together and kills else None,
                         "by_character": (together or {}).get("by_character"),
                         "by_model": {m: t["cost_usd"] for m, t in ((together or {}).get("by_model") or {}).items()},
                         "kills": kills, "deaths": sum(a.get("deaths") or 0 for a in agents.values()),
                         "judge_errors": sum(a.get("judge_errors") or 0 for a in agents.values())},
            "load": load}


def markdown(r: dict[str, Any]) -> str:
    t = r["together"]
    lines = [f"Fleet {r['fleet']}: {len(r['agents'])} agents, {t['kills']} kills, {t['deaths']} deaths, "
             f"${t['cost_usd']:.4f} together (${t['cost_per_hour_usd'] or 0:.3f}/h"
             + (f", ${t['cost_per_kill_usd']:.4f} a kill" if t.get("cost_per_kill_usd") else "")
             + f"), {t['judge_errors']} judge errors.", "",
             "| agent | profile | minutes | kills | deaths | gold banked | fight decisions/h | $/h | models |",
             "|---|---|---|---|---|---|---|---|---|"]
    for name, a in r["agents"].items():
        if "error" in a:
            lines.append(f"| {name} | {a.get('profile') or ''} | | | | | | | did not play: {a['error'][:80]} |")
            continue
        lines.append(f"| {name} | {a['profile']} | {a['minutes']} | {a['kills']} | {a['deaths']} | {a['gold_banked']} | "
                     f"{a.get('fight_decisions_per_hour') or ''} | "
                     f"{(a['cost_per_hour_usd'] or {}).get('total', 0):.3f} | {', '.join(a['models'])} |")
    talkers = {n: a for n, a in r["agents"].items() if a.get("lines_said") is not None}
    if talkers:
        lines += ["", "| agent | lines said | heard | lines an hour | talk $/h | it gave | goals after talk |",
                  "|---|---|---|---|---|---|---|"]
        for n, a in talkers.items():
            lines.append(f"| {n} | {a['lines_said']} | {a['lines_heard']} | {a['lines_per_hour']} | "
                         f"{a['talk_cost_per_hour_usd']:.4f} | {', '.join(a['talk_gave']) or 'nothing'} | "
                         f"{len(a['goals_after_talk'])} |")
        for n, a in talkers.items():
            for g in a["goals_after_talk"]:
                lines.append(f"- {n}, {g['tool']}: {g['why']}")
    if r["load"]:
        lines += ["", "| process | CPU avg % | CPU max % | resident max MB | footprint max MB | client updates/s avg (min) |",
                  "|---|---|---|---|---|---|"]
        lines += [f"| {k}{' (headless)' if v.get('headless') else ''} | {v['cpu_avg']} | {v['cpu_max']} | {v['mb_max']} | "
                  f"{v.get('footprint_mb_max', '')} | " + (f"{v['ups_avg']} ({v['ups_min']})" if "ups_avg" in v else "") + " |"
                  for k, v in r["load"].items()]
    return "\n".join(lines)


def main_run(path: Path, out: Path | None, hours: float | None, talk: bool = True,
             windowed: bool = False, settings: list[str] | None = None) -> dict[str, Any]:
    fleet = load(path, settings)
    if hours:
        fleet.hours = hours
    if windowed:  # to film a run (tools/video/film.py): a headless client has no window
        for a in fleet.agents:
            a.headless = False
    if not talk:  # the control for a talk test: same characters, nobody hears anybody
        for a in fleet.agents:
            a.talk = False
    out = out or BRAIN_DIR / "logs" / "fleet" / f"{time.strftime('%Y%m%d-%H%M%S')}-{fleet.name}{'' if talk else '-quiet'}"
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    return asyncio.run(run(fleet, out))
