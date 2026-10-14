"""Stage a companion set upon away from the others (cuo-cym.10), for the check that a party goes to it.

  uv run python scripts/party_stray.py --port 5663 [--hold 30] [--idle] [--quiet 8] [--tiles 14] [--direction s] [--kind BoneKnight]

Run it beside a fleet whose party forms up (`uv run uo-brain fleet fleets/party-lich.toml --set form_up=true`).
It watches the client on --port. Once that character has been in the party's fight and has then seen nothing
in war mode for --quiet seconds (the fight is over: the others are looting or resting), it has the character
say two of the test server's commands: `[AgentGo` to a tile --tiles away in --direction (out of the 10 tiles
that make a party, still in view), and `[AgentSpawn` for one creature two tiles beyond it. Then it writes down,
twice a second for --watch seconds, the character's health, the creature, and how far each companion is.

With --hold N the character's own walking and looting are switched off for N seconds (the client's `move` and
`loot` authority, put back to auto afterwards). Left to itself it is back among the others in two seconds,
well ahead of the creature: the fight just over, it is looting, and the client walks it back to the corpse it
was at. Then there is no companion alone with something on it for the others to go to. Held, it still fights,
heals and is healed. With --idle it is moved only once it is neither looting nor walking: held or not, a walk
to a corpse that was under way when it was moved still finished, and brought it back among the others (three
staged runs of 2026-10-13). With --tiles 24 it is out of the others' view, which is the case the rule is for:
they know of it only by its own reports to the party.

Nothing else is touched: the character's own brain finds a bone knight beside it, and what the others do
about it is the thing being checked. Staged, and said so wherever the run is shown.
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from uo_brain.rpc import AgentRpc  # noqa: E402

HELD = {"move": "off", "loot": "off"}
FREE = {"move": "auto", "loot": "auto"}
STEP = {"n": (0, -1), "ne": (1, -1), "e": (1, 0), "se": (1, 1), "s": (0, 1), "sw": (-1, 1), "w": (-1, 0), "nw": (-1, -1)}


def hostile(snap: dict, within: int) -> list[dict]:
    return [m for m in snap.get("mobiles") or [] if m.get("monster") and not m.get("dead") and not m.get("pet")
            and m.get("war_mode") and m["distance"] <= within]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--quiet", type=float, default=8.0, help="seconds with nothing in war mode in view, after a fight")
    ap.add_argument("--tiles", type=int, default=14)
    ap.add_argument("--direction", default="s", choices=sorted(STEP))
    ap.add_argument("--kind", default="BoneKnight")
    ap.add_argument("--watch", type=float, default=45.0)
    ap.add_argument("--hold", type=float, default=0.0, help="seconds its own walking and looting are switched off")
    ap.add_argument("--idle", action="store_true", help="move it only once it is neither looting nor walking")
    ap.add_argument("--wait", type=float, default=420.0, help="give up after this long")
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
    fought, quiet_since = False, None
    while time.monotonic() - began < args.wait:
        try:
            snap = await r.call("snapshot", since=1 << 30, timeout=3)
        except Exception:  # noqa: BLE001 - a look that failed: the next one
            await asyncio.sleep(1)
            continue
        if not snap.get("in_game") or snap["player"].get("dead"):
            quiet_since = None
            await asyncio.sleep(1)
            continue
        p = snap["player"]
        if len(hostile(snap, 12)) >= 2:
            fought = True
        quiet_since = (quiet_since or time.monotonic()) if fought and not hostile(snap, 18) else None
        fit = p["hits"] >= 0.8 * max(1, p["hits_max"])
        idle = not args.idle or not ((snap.get("agent") or {}).get("looting") or p.get("walking"))
        if quiet_since is not None and time.monotonic() - quiet_since >= args.quiet and fit and idle:
            dx, dy = STEP[args.direction]
            x, y = p["x"] + dx * args.tiles, p["y"] + dy * args.tiles
            if args.hold > 0:
                await r.call("mode", authority=HELD)
            await r.call("act", verb="say", text=f"[AgentGo {x} {y}", source="manual")
            if args.hold > 0:
                await r.call("act", verb="halt", source="manual")  # a walk to a corpse may be under way
            await asyncio.sleep(0.8)
            await r.call("act", verb="say", text=f"[AgentSpawn {args.kind} 1 2 {args.direction}", source="manual")
            staged = time.time()
            print(json.dumps({"t": staged, "strayed": p.get("name"), "from": [p["x"], p["y"]], "to": [x, y],
                              "spawned": args.kind, "held_s": args.hold}), flush=True)
            held = args.hold > 0
            while time.time() - staged < max(args.watch, args.hold + 2):
                await asyncio.sleep(0.5)
                try:
                    if held:
                        # Said again each time round: a goal that begins sets the client's mode anew.
                        over = time.time() - staged >= args.hold
                        await r.call("mode", authority=FREE if over else HELD)
                        if not over and (await r.call("snapshot", since=1 << 30, timeout=3))["player"].get("walking"):
                            await r.call("act", verb="halt", source="manual")
                        held = not over
                    snap = await r.call("snapshot", since=1 << 30, timeout=3)
                except Exception:  # noqa: BLE001
                    continue
                me = snap["player"]
                foes = hostile(snap, 18)
                print(json.dumps({
                    "s": round(time.time() - staged, 1), "dead": bool(me.get("dead")),
                    "health_pct": round(100 * me["hits"] / max(1, me["hits_max"])), "at": [me["x"], me["y"]],
                    "foes": [[m.get("name"), m["distance"], m.get("hits_pct")] for m in foes],
                    "companions": {m["name"]: m["distance"] for m in snap.get("mobiles") or []
                                   if m.get("human") and not m.get("monster") and not m.get("dead")}}), flush=True)
            try:
                await r.call("mode", authority=FREE)  # whatever happened above, its legs are its own again
            except Exception:  # noqa: BLE001 - the client has gone: the run is over
                pass
            await r.close()
            return
        await asyncio.sleep(0.5)
    sys.exit("no fight came, or it never went quiet")


if __name__ == "__main__":
    asyncio.run(main())
