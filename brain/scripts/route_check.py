"""A long trip's plan as travel would walk it, without and with the ground of stronger creatures priced
(cuo-m3n.13), hop by hop through a client's `nav` view. Each hop is planned from where the character
stands, so it is moved to each hop's end with [AgentGo (a test server only) and put back at the start
after each plan. Prints each hop (steps, steps on priced ground, planning time and nodes), then for each
plan its total, how many of its sampled points (every 32nd tile) lie on far-stronger and stronger ground,
and the sampled points themselves.

  uv run python scripts/route_check.py PORT START_X START_Y GOAL_X GOAL_Y

The squares are the ones a session would send for that trip (`World.dangers_between`, from the local
store, for the character's own hits).
"""
import asyncio
import sys

from uo_brain.rpc import AgentRpc
from uo_brain.world import World


async def go(rpc: AgentRpc, x: int, y: int) -> None:
    await rpc.call("act", verb="say", source="manual", text=f"[AgentGo {x} {y}")
    await asyncio.sleep(3)


def on(points: list[list[int]], squares: list[dict], cost: int) -> int:
    return sum(1 for x, y in points if any(d["cost"] == cost and max(abs(x - d["x"]), abs(y - d["y"])) <= d["r"]
                                           for d in squares))


async def main(port: int, sx: int, sy: int, gx: int, gy: int) -> None:
    rpc = AgentRpc(port=port)
    await rpc.connect()
    await go(rpc, sx, sy)
    p = (await rpc.call("snapshot"))["player"]
    print("at", p["x"], p["y"], "hits", p.get("hits_max"))
    squares = World("worlds/local/world.sqlite").dangers_between(p["x"], p["y"], gx, gy, p.get("hits_max") or 100)
    print("squares", len(squares))
    avoid = [[d["x"], d["y"], d["r"], d["cost"]] for d in squares]
    for label, priced in (("plain", None), ("priced", avoid)):
        total = steps_priced = 0
        sampled: list[list[int]] = []
        for hop in range(12):
            r = await rpc.call("nav", timeout=60, radius=4, goal_x=gx, goal_y=gy, route=True,
                               **({"avoid": priced} if priced else {}))
            route = r.get("route") or []
            if not route:
                print(label, "hop", hop, "no route:", r.get("why"))
                break
            total += r["path"]
            steps_priced += r.get("priced_steps", 0)
            sampled += route[::4]
            print(label, "hop", hop, "steps", r["path"], "priced", r.get("priced_steps"), "ms", r.get("plan_ms"),
                  "nodes", r.get("nodes"), "end", route[-1])
            if not r.get("hop"):
                break
            await go(rpc, *route[-1])
        print(label, "total", total, "on priced ground", steps_priced, "sampled points", len(sampled),
              "on far-stronger ground", on(sampled, squares, 4), "on stronger", on(sampled, squares, 2))
        print(label, "route", sampled)
        await go(rpc, sx, sy)
    await rpc.close()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]), *map(int, sys.argv[2:6])))
