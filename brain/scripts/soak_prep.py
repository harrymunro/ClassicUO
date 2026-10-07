"""Prepare the soak character (the client on $SOAK_PORT, 5580 by default): vendors restocked,
then at the West Britain bank with the warrior kit, 30 bandages and 400 gold, disruptions undone.
`runes` adds a runebook to town and the graveyard."""
import asyncio, os, sys
from uo_brain.rpc import AgentRpc

PORT = int(os.environ.get("SOAK_PORT", "5580"))

async def say(r, t, pause=1.5):
    await r.call("act", verb="say", text=t, source="manual")
    await asyncio.sleep(pause)

async def main():
    r = AgentRpc(port=PORT)
    await r.connect()
    await r.call("mode", mode="off")
    await r.call("goal", clear=True)
    # The healers and shops restocked first, so a run measures fighting rather than an earlier run's purchases.
    for c in ("[AgentReset", "[AgentGo 1471 1609", "[AgentRestock 60", "[AgentGo 1425 1690", "[AgentKit",
              "[AgentSupplies bandages 30 gold 400"):
        await say(r, c)
    if "runes" in sys.argv:
        await say(r, "[AgentRunes", 2)
    await say(r, "[AgentDisrupt restore quiet", 2)
    await asyncio.sleep(6)
    s = await r.call("snapshot", since=0)
    p = s["player"]
    print("at", p["x"], p["y"], "bandages", p["supplies"]["bandages"], "gold", p["gold"], "hits", p["hits"], "/", p["hits_max"],
          "runes", s.get("travel_items"))
    await r.close()

asyncio.run(main())
