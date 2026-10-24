"""The planner grid's reach against the client's own walker's, at a few places, for one client (cuo-5y8; the inn by its steps, cuo-u4j;
the foot of the bridge to Vesper's shops, cuo-m3n.8).

  uv run python scripts/nav_check.py PORT OUT.json      (a client logged in on PORT)
'-' = the grid reaches a tile the client's walker can't; '+' = the client reaches one the grid can't."""
import asyncio, json, sys
from uo_brain.rpc import AgentRpc

PLACES = [("tuft west of Britain", 1347, 1512), ("West Britain bank", 1425, 1690), ("Britain healer", 1471, 1611),
          ("Castle British", 1323, 1624), ("Britain inn", 1494, 1656), ("Cove", 2243, 1195),
          ("Britain Mine 1", 1443, 1228), ("Britain graveyard", 1384, 1492), ("Minoc", 2466, 544),
          ("Vesper bridge", 2875, 760), ("Sweet Dreams inn, the street west of it", 1487, 1621),
          ("Vesper, the foot of the bridge to its shops", 2848, 851)]

async def main(port, out):
    r = AgentRpc(port=port)
    await r.connect(attempts=60)
    for _ in range(60):
        st = await r.call("status")
        if st.get("in_game"):
            break
        await asyncio.sleep(1)
    res = {}
    for name, x, y in PLACES:
        await r.call("act", verb="say", text=f"[AgentGo {x} {y}", source="manual")
        await asyncio.sleep(4)
        n = await r.call("nav", radius=30, check=True, timeout=60)
        rows = n["rows"]
        c = {k: sum(row.count(k) for row in rows) for k in "-+,"}
        snap = await r.call("snapshot", radius=1)
        p = snap["player"]
        res[name] = {"at": [p["x"], p["y"], p["z"]], **c,
                     "minus_at": [[n["x0"] + i, n["y0"] + j] for j, row in enumerate(rows) for i, ch in enumerate(row) if ch == "-"][:20],
                     "plus_at": [[n["x0"] + i, n["y0"] + j] for j, row in enumerate(rows) for i, ch in enumerate(row) if ch == "+"][:20]}
        print(name, res[name]["at"], {k: c[k] for k in "-+,"}, flush=True)
    json.dump(res, open(out, "w"), indent=1)
    await r.close()

asyncio.run(main(int(sys.argv[1]), sys.argv[2]))
