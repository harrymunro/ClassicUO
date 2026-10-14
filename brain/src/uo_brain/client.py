"""Starting a ClassicUO client for the agent, windowed or headless (cuo-1k7).

A client runs from bin/osx-arm64 (it reads settings.json there) with the agent's RPC port. A
headless one (`-headless`) hides its window and draws nothing, while the game loop, network,
world, agent and RPC run as usual: for NPCs and benchmark lanes. The `window` RPC shows a
running client's window (to watch an NPC) and hides it again:

    uo-brain --port 5610 client --headless --account headless1 --character Hedda --server-port 2595
    uo-brain --port 5610 window show      (window hide; window: report only)

The fleet (fleet.py, `headless = true` in a fleet file) and the bench (`bench --headless`) start
their clients through here.
"""

import asyncio
import os
import socket
import subprocess
from pathlib import Path
from typing import Any

from .rpc import AgentRpc

BRAIN_DIR = Path(__file__).resolve().parents[2]
# UO_CLIENT_DIR: another published client (a test build beside a soak running from bin/osx-arm64).
CLIENT_DIR = Path(os.environ.get("UO_CLIENT_DIR") or BRAIN_DIR.parent / "bin" / "osx-arm64")
WARRIOR = {"str": 50, "dex": 30, "int": 10, "skills": {"Swordsmanship": 30, "Tactics": 30, "Healing": 30, "Anatomy": 30}}


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def command(port: int, headless: bool = False) -> list[str]:
    """The command line for a client on this agent port. The brain runs on its own (fleet,
    bench, session), so the client doesn't start one."""
    args = ["./cuo", "-agent_port", str(port), "-agent_start_brain", "false"]
    if headless:
        args.append("-headless")
    return args


def start(port: int, log: Path, headless: bool = False, detach: bool = False) -> subprocess.Popen | None:
    """A client listening on the agent port, unless one already does (then None). With detach it
    keeps running after this process ends."""
    if port_open(port):
        return None
    if not (CLIENT_DIR / "cuo").exists():
        raise FileNotFoundError(f"no client at {CLIENT_DIR / 'cuo'} (dotnet publish ... -o bin/osx-arm64)")
    log.parent.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(command(port, headless), cwd=CLIENT_DIR, stdout=log.open("w"), stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, start_new_session=detach)


def stop(proc: subprocess.Popen | None, timeout: float = 15.0) -> None:
    """Close a client this process started (it saves its settings on the way out)."""
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout)
    except subprocess.TimeoutExpired:
        proc.kill()


def clients_running() -> int:
    """How many clients run on this machine (anyone's): results move with the load."""
    out = subprocess.run(["pgrep", "-x", "cuo"], capture_output=True, text=True).stdout
    return len(out.split())


STUCK_S = 25  # a login this long at "server selected" isn't coming: the caller tries again


async def log_in(rpc: AgentRpc, account: str, password: str, character: str | None = None,
                 host: str | None = None, server_port: int | None = None, create: dict[str, Any] | None = None,
                 wait_s: int = 90) -> str:
    """In game as the character (made with `create`, e.g. a warrior, if missing). Returns
    "in game as NAME", or why not."""
    st = await rpc.call("status")
    if not st.get("in_game"):
        params: dict[str, Any] = {"account": account, "password": password}
        if host:
            params["host"] = host
        if server_port:
            params["port"] = server_port
        if character:
            params["character"] = character
        if create:
            params["create"] = create
        await rpc.call("login", **params)
        stuck = 0
        for _ in range(wait_s):
            await asyncio.sleep(1)
            st = await rpc.call("status")
            if st.get("in_game") or st.get("login_error"):
                break
            # Stuck between the login server and the game server: the server drops such a socket after about
            # 18 s and the client waits on. Waited out in full, one stuck login held a fleet's other logins
            # back a minute and a half, past its leader's wait for the party.
            stuck = stuck + 1 if st.get("login_state") == "server_selected" else 0
            if stuck >= STUCK_S:
                break
    if not st.get("in_game"):
        return f"not in game: {st.get('login_error') or st}"
    return f"in game as {st.get('player')}"


def warrior(name: str) -> dict[str, Any]:
    return {"name": name, **WARRIOR}
