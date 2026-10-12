"""One timed travel trip through a client's agent RPC: sets off (as a manual action), watches the
walk until it ends, and prints one JSON line: the outcome, seconds, seconds standing still, the
agent's own travel events (doors opened, stuck spots, replans) and the client's travel record.
The agent must be in assist or auto mode (it travels only then).

  uv run python scripts/travel_trip.py PORT X,Y[,DISTANCE] [--go X,Y] [--park X,Y] [--label TEXT] [--timeout S] [--defend]

--go teleports there first ([AgentGo), --park afterwards, so two clients can take turns on a route.
--defend runs the fight loop beside the walk as a session does, with the rule judge (no AI calls):
it fights what is close or attacking and never seeks or loots. A trip through the wilds needs it.
"""
import asyncio
import json
import sys
import time

from uo_brain import loop, policy
from uo_brain.judge import HeuristicJudge
from uo_brain.rpc import AgentRpc


def opt(name: str, default: str | None = None) -> str | None:
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


async def main() -> None:
    port = int(sys.argv[1])
    goal = [int(v) for v in sys.argv[2].split(",")]
    x, y, d = goal[0], goal[1], goal[2] if len(goal) > 2 else 2
    timeout = float(opt("--timeout", "300"))
    rpc = AgentRpc(port=port)
    await rpc.connect(attempts=3)
    stop, fight = asyncio.Event(), None
    if "--defend" in sys.argv:
        guard = AgentRpc(port=port)
        await guard.connect(attempts=3)
        fight = asyncio.create_task(loop.run(guard, HeuristicJudge(), loop.LoopConfig(), policy.PolicyConfig(defend_only=True),
                                             None, stop))
    try:
        if go := opt("--go"):
            await rpc.call("act", verb="say", text=f"[AgentGo {go.replace(',', ' ')}", source="manual")
            await asyncio.sleep(3)
        snap = await rpc.call("snapshot")
        seq = snap.get("journal_seq", 0)
        start = (snap["player"]["x"], snap["player"]["y"])
        t0 = time.perf_counter()
        res = await rpc.call("act", timeout=60, verb="travel", x=x, y=y, distance=d, source="manual")
        set_off_ms = (time.perf_counter() - t0) * 1000
        travel, events, still = {}, [], 0.0
        last, last_t = start, time.perf_counter()
        while res.get("status") == "done" and time.perf_counter() - t0 < timeout:
            await asyncio.sleep(0.25)
            snap = await rpc.call("snapshot")
            pos, now = (snap["player"]["x"], snap["player"]["y"]), time.perf_counter()
            if pos == last:
                still += now - last_t
            last, last_t = pos, now
            for j in snap.get("journal") or []:
                if j.get("seq", 0) > seq:
                    seq = j["seq"]
                    if j.get("source") == "agent":
                        events.append(j["text"])
            travel = (snap.get("agent") or {}).get("travel") or {}
            if travel.get("state") != "walking":
                break
        else:
            if res.get("status") == "done":
                await rpc.call("act", verb="stop", source="manual")
                travel = {**travel, "state": "timed out"}
        p = snap["player"]
        print(json.dumps({
            "label": opt("--label", ""), "from": list(start), "goal": [x, y, d], "set_off": res,
            "set_off_ms": round(set_off_ms, 1), "state": travel.get("state", res.get("status")),
            "seconds": round(time.perf_counter() - t0, 1), "still_s": round(still, 1), "end": [p["x"], p["y"]],
            "doors": sum("opening a door" in e for e in events), "stuck": sum(e.startswith("stuck") for e in events),
            "events": events, "travel": travel}))
        if park := opt("--park"):
            await rpc.call("act", verb="say", text=f"[AgentGo {park.replace(',', ' ')}", source="manual")
            await asyncio.sleep(1)
    finally:
        stop.set()
        if fight:
            await asyncio.wait_for(fight, 10)
            await guard.close()
        await rpc.close()


asyncio.run(main())
