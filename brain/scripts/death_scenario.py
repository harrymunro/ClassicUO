"""Kill the character at a spot with 'still' daemons, then (as the operator) clear them once it has
been resurrected, so the corpse can be taken back. argv: port, x, y, timeout_s."""
import asyncio, sys, time
from uo_brain.rpc import AgentRpc

async def say(r, t):
    await r.call("act", verb="say", text=t, source="manual")
    await asyncio.sleep(2)

async def main():
    port, x, y, timeout = int(sys.argv[1]), sys.argv[2], sys.argv[3], float(sys.argv[4])
    r = AgentRpc(port=port); await r.connect()
    await r.call("mode", mode="off")
    for c in ("[AgentReset", "[AgentKit warrior", f"[AgentGo {x} {y}", "[AgentSpawn Daemon 3 1 n still"):
        await say(r, c)
    t0 = time.time()
    while not (await r.call("snapshot", since=0))["player"]["dead"]:
        await asyncio.sleep(1)
    print(f"died after {time.time() - t0:.0f} s", flush=True)
    await r.call("mode", mode="auto")
    print("DEAD", flush=True)
    # Wait for the resurrection, then clear the daemons (the corpse is far from the healer).
    while (await r.call("snapshot", since=0))["player"]["dead"] and time.time() - t0 < timeout:
        await asyncio.sleep(1)
    s = await r.call("snapshot", since=0)
    p = s["player"]
    print(f"alive at {p['x']},{p['y']} after {time.time() - t0:.0f} s", flush=True)
    if max(abs(p["x"] - int(x)), abs(p["y"] - int(y))) > 32:
        await say(r, "[AgentReset")
        print("daemons cleared", flush=True)
    else:
        print("too close to the corpse to clear the daemons without losing it", flush=True)
    await r.close()

asyncio.run(main())
