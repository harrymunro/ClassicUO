"""Stage a death in the middle of a party's fight (cuo-cym.4), for the check of raising the fallen.

  uv run python scripts/party_slay.py --port 5663 [--after 6] [--hostiles 2] [--within 12] [--wait 300]

Run it beside a fleet (`uv run uo-brain fleet fleets/party-raise.toml`). It watches the client on --port,
and once that character has had --hostiles creatures in war mode within --within tiles for --after seconds
(the party's fight is on), it has the character say `[AgentSlay`, the test server's command that kills
whoever says it where it stands: a corpse with its things on it, and a ghost. Nothing else is touched: the
character's own brain finds itself dead, and what its party does next is the thing being checked.

A staged death, and said so wherever the run is shown: nothing in the fight killed it.
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from uo_brain.rpc import AgentRpc  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--after", type=float, default=6.0, help="seconds of fight before the death")
    ap.add_argument("--hostiles", type=int, default=2)
    ap.add_argument("--within", type=int, default=12)
    ap.add_argument("--wait", type=float, default=300.0, help="give up after this long with no fight")
    args = ap.parse_args()
    r = AgentRpc(port=args.port)
    began = time.monotonic()
    while True:
        try:
            await r.connect(attempts=2)
            break
        except Exception:  # noqa: BLE001 - the fleet hasn't started that client yet
            if time.monotonic() - began > args.wait:
                sys.exit(f"no client on port {args.port}")
            await asyncio.sleep(2)
    since = None
    while time.monotonic() - began < args.wait:
        try:
            snap = await r.call("snapshot", since=1 << 30, timeout=3)
        except Exception:  # noqa: BLE001 - a look that failed: the next one
            await asyncio.sleep(1)
            continue
        if not snap.get("in_game") or snap["player"].get("dead"):
            since = None
            await asyncio.sleep(1)
            continue
        near = [m for m in snap.get("mobiles") or [] if m.get("monster") and not m.get("dead") and not m.get("pet")
                and m.get("war_mode") and m["distance"] <= args.within]
        since = (since or time.monotonic()) if len(near) >= args.hostiles else None
        if since is not None and time.monotonic() - since >= args.after:
            p = snap["player"]
            await r.call("act", verb="say", text="[AgentSlay", source="manual")
            await asyncio.sleep(1.5)
            dead = (await r.call("snapshot", since=1 << 30, timeout=3))["player"].get("dead")
            print(json.dumps({"t": time.time(), "slain": p.get("name"), "at": [p["x"], p["y"]], "dead": bool(dead),
                              "health_pct": round(100 * p["hits"] / max(1, p["hits_max"])),
                              "hostile_near": [m.get("name") for m in near]}), flush=True)
            await r.close()
            return
        await asyncio.sleep(0.5)
    sys.exit("no fight came")


if __name__ == "__main__":
    asyncio.run(main())
