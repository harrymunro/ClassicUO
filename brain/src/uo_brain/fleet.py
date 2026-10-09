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

`uo-brain fleet FILE` starts a client for each agent that hasn't one running (from bin/osx-arm64),
logs it in, runs its prep commands, then runs `uo-brain session` for it as its own process, so
each brain has its own ledger: one character's AI costs. While they play it samples the CPU and
memory of every client, brain and the server. At the end, and with `uo-brain fleet-report DIR`, it
puts together what each character did and cost (soak.py's report of each log) and what the fleet
cost an hour together, with the judge errors (rate limits show there) and the load.
"""

import asyncio
import json
import os
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import costs, logs, soak
from .rpc import AgentRpc

BRAIN_DIR = Path(__file__).resolve().parents[2]
CLIENT_DIR = BRAIN_DIR.parent / "bin" / "osx-arm64"
WARRIOR = {"str": 50, "dex": 30, "int": 10, "skills": {"Swordsmanship": 30, "Tactics": 30, "Healing": 30, "Anatomy": 30}}


@dataclass
class Agent:
    name: str
    account: str
    password: str
    port: int
    goal: str
    profile: str = "default"
    prep: list[str] = field(default_factory=list)


@dataclass
class Fleet:
    name: str
    agents: list[Agent]
    hours: float = 0.5
    server_port: int = 2593
    host: str = "127.0.0.1"


def load(path: Path) -> Fleet:
    data = tomllib.loads(Path(path).read_text())
    agents = [Agent(str(a["name"]), str(a["account"]), str(a["password"]), int(a["port"]), str(a["goal"]),
                    str(a.get("profile", "default")), [str(c) for c in a.get("prep") or []])
              for a in data.get("agent") or []]
    if not agents:
        raise ValueError(f"{path}: no [[agent]] entries")
    if len({a.port for a in agents}) != len(agents) or len({a.account for a in agents}) != len(agents):
        raise ValueError(f"{path}: each agent needs its own port and account")
    return Fleet(str(data.get("name") or Path(path).stem), agents, float(data.get("hours", 0.5)),
                 int(data.get("server_port", 2593)), str(data.get("host", "127.0.0.1")))


# ---------------------------------------------------------------- running it

def port_open(port: int) -> bool:
    import socket
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def start_client(agent: Agent, out_dir: Path) -> subprocess.Popen | None:
    """A client for the agent, unless one already listens on its port."""
    if port_open(agent.port):
        return None
    log = (out_dir / f"{agent.name}.client.log").open("w")
    return subprocess.Popen(["./cuo", "-agent_port", str(agent.port)], cwd=CLIENT_DIR, stdout=log,
                            stderr=subprocess.STDOUT)


async def log_in(fleet: Fleet, agent: Agent) -> str:
    """In game as the agent's character (created as a warrior if missing), its prep done."""
    rpc = AgentRpc(port=agent.port)
    await rpc.connect(attempts=90)
    try:
        st = await rpc.call("status")
        if not st.get("in_game"):
            await rpc.call("login", account=agent.account, password=agent.password, host=fleet.host,
                           port=fleet.server_port, character=agent.name, create={"name": agent.name, **WARRIOR})
            for _ in range(90):
                await asyncio.sleep(1)
                st = await rpc.call("status")
                if st.get("in_game") or st.get("login_error"):
                    break
        if not st.get("in_game"):
            return f"not in game: {st.get('login_error') or st}"
        await rpc.call("mode", mode="off")
        await rpc.call("goal", clear=True)
        for cmd in agent.prep:
            await rpc.call("act", verb="say", text=cmd, source="manual")
            await asyncio.sleep(1.5)
        return f"in game as {st.get('player')}"
    finally:
        await rpc.close()


def start_brain(fleet: Fleet, agent: Agent, out_dir: Path) -> subprocess.Popen:
    cmd = [sys.executable, "-m", "uo_brain.cli", "--port", str(agent.port), "session", agent.goal,
           "--hours", str(fleet.hours), "--profile", agent.profile, "--log", str(out_dir / f"{agent.name}.jsonl")]
    return subprocess.Popen(cmd, cwd=BRAIN_DIR, stdout=(out_dir / f"{agent.name}.out").open("w"),
                            stderr=subprocess.STDOUT)


def sample(pids: dict[str, int]) -> dict[str, dict[str, float]]:
    """CPU (percent of one core) and memory (MB) of each named process, from ps."""
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
    return {k: by_pid[p] for k, p in alive.items() if p in by_pid}


def pid_listening(port: int) -> int:
    """The process listening on a TCP port (the game server), or 0."""
    out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], capture_output=True, text=True).stdout
    return int(out.split()[0]) if out.split() else 0


async def run(fleet: Fleet, out_dir: Path, every_s: float = 30.0) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    events = (out_dir / "fleet.jsonl").open("a")

    def note(rec: dict[str, Any]) -> None:
        events.write(json.dumps({"t": time.time(), **rec}) + "\n")
        events.flush()

    note({"type": "fleet", "name": fleet.name, "hours": fleet.hours,
          "agents": [{"name": a.name, "port": a.port, "profile": a.profile, "goal": a.goal} for a in fleet.agents]})
    clients = {a.name: start_client(a, out_dir) for a in fleet.agents}
    for a in fleet.agents:
        note({"type": "login", "agent": a.name, "result": await log_in(fleet, a)})
    brains = {a.name: start_brain(fleet, a, out_dir) for a in fleet.agents}
    pids = {"server": pid_listening(fleet.server_port)}
    for a in fleet.agents:
        client = clients[a.name]
        pids[f"{a.name}/client"] = client.pid if client else pid_listening(a.port)
        pids[f"{a.name}/brain"] = brains[a.name].pid
    while any(b.poll() is None for b in brains.values()):
        await asyncio.sleep(every_s)
        note({"type": "load", "processes": sample(pids)})
    for c in clients.values():
        if c is not None:
            c.terminate()
    events.close()
    return report(out_dir)


# ---------------------------------------------------------------- the report

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
                             "models": sorted((r.get("costs") or {}).get("by_model", {}))}
    loads = [x["processes"] for x in records if x["type"] == "load"]
    load: dict[str, dict[str, float]] = {}
    for name in sorted({k for s in loads for k in s}):
        cpu = [s[name]["cpu"] for s in loads if name in s]
        mb = [s[name]["mb"] for s in loads if name in s]
        load[name] = {"cpu_avg": round(sum(cpu) / len(cpu), 1), "cpu_max": max(cpu), "mb_max": max(mb)}
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
             "| agent | profile | minutes | kills | deaths | gold banked | $/h | models |", "|---|---|---|---|---|---|---|---|"]
    for name, a in r["agents"].items():
        if "error" in a:
            lines.append(f"| {name} | {a.get('profile') or ''} | | | | | | did not play: {a['error'][:80]} |")
            continue
        lines.append(f"| {name} | {a['profile']} | {a['minutes']} | {a['kills']} | {a['deaths']} | {a['gold_banked']} | "
                     f"{(a['cost_per_hour_usd'] or {}).get('total', 0):.3f} | {', '.join(a['models'])} |")
    if r["load"]:
        lines += ["", "| process | CPU avg % | CPU max % | memory max MB |", "|---|---|---|---|"]
        lines += [f"| {k} | {v['cpu_avg']} | {v['cpu_max']} | {v['mb_max']} |" for k, v in r["load"].items()]
    return "\n".join(lines)


def main_run(path: Path, out: Path | None, hours: float | None) -> dict[str, Any]:
    fleet = load(path)
    if hours:
        fleet.hours = hours
    out = out or BRAIN_DIR / "logs" / "fleet" / f"{time.strftime('%Y%m%d-%H%M%S')}-{fleet.name}"
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    return asyncio.run(run(fleet, out))
