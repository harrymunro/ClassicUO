"""A vendor nobody can stand next to, staged (cuo-cgy): the character on PORT is put 3 or 4 tiles from the Vesper
healer and walled in ([AgentWall on the eight tiles round it), so no walk takes it next to him. Then buy or sell:

  uv run python scripts/vendor_wall.py PORT            stage it
  uv run uo-brain --port PORT do buy bandage 5 --vendor healer
  uv run python scripts/vendor_wall.py PORT clear      take the walls away ([AgentWall clear, the same server session)
"""
import asyncio, sys
from uo_brain.rpc import AgentRpc

async def say(r, t):
    await r.call("act", verb="say", text=t, source="manual")
    await asyncio.sleep(1.5)

async def main(port, mode):
    r = AgentRpc(port=port); await r.connect(attempts=60)
    for _ in range(60):
        if (await r.call("status")).get("in_game"):
            break
        await asyncio.sleep(1)
    if mode == "clear":
        await say(r, "[AgentWall clear"); await r.close(); return
    for c in ("[AgentReset", "[AgentKit warrior", "[AgentSupplies gold 500", "[AgentGo 2920 854"):
        await say(r, c)
    await asyncio.sleep(2)
    snap = await r.call("snapshot", radius=12)
    p = snap["player"]
    heal = [m for m in snap["mobiles"] if "healer" in (m.get("label") or m.get("name") or "").lower() and "guild" not in (m.get("label") or "").lower()]
    print("at", p["x"], p["y"], "healers", [(m.get("label"), m["dx"], m["dy"]) for m in heal])
    if not heal:
        return
    h = heal[0]; hx, hy = p["x"] + h["dx"], p["y"] + h["dy"]
    for dx, dy in ((3, 0), (-3, 0), (0, 3), (0, -3), (3, 3), (-3, -3), (4, 0), (-4, 0)):
        await say(r, f"[AgentGo {hx + dx} {hy + dy}")
        snap = await r.call("snapshot", radius=12); p = snap["player"]
        if (p["x"], p["y"]) == (hx + dx, hy + dy):
            break
    x, y = p["x"], p["y"]
    for a in (f"[AgentWall {x-1} {y-1} {x+1} {y-1}", f"[AgentWall {x-1} {y+1} {x+1} {y+1}", f"[AgentWall {x-1} {y} {x-1} {y}", f"[AgentWall {x+1} {y} {x+1} {y}"):
        await say(r, a)
    snap = await r.call("snapshot", radius=12)
    h = [m for m in snap["mobiles"] if m["serial"] == h["serial"]][0]
    print("walled in at", x, y, "the healer", h.get("label"), "at distance", h["distance"])
    await r.close()

asyncio.run(main(int(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else "stage"))
