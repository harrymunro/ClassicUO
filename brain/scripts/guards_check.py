"""Calling the guards on ModernUO (cuo-m3n.14), on a test server: a creature spawned 5 tiles east of the character in
Cove's street, the agent off so the character doesn't strike first, and what is about logged each second. With
--say the character says "Guards!" at the creature's first strike; without it (the control), only after 30 s or below
45 hits, to save it.

  uv run python scripts/guards_check.py PORT KIND [--say]       # e.g. 5580 Lich --say
"""
import asyncio
import sys
import time

from uo_brain.rpc import AgentRpc

AT = (2262, 1212)  # in Cove's guarded ground, 9 tiles from the nearest townsperson


async def main(port: int, kind: str, say: bool) -> None:
    rpc = AgentRpc(port=port)
    await rpc.connect()
    await rpc.call("mode", mode="off")
    for line in ("[AgentReset", f"[AgentGo {AT[0]} {AT[1]}"):
        await rpc.call("act", verb="say", source="manual", text=line)
    await asyncio.sleep(3)
    snap = await rpc.call("snapshot", since=0)
    print("people in view:", [(m.get("name"), m["distance"]) for m in snap["mobiles"] if m.get("human")])
    await rpc.call("act", verb="say", source="manual", text=f"[AgentSpawn {kind} 1 5 e")
    began = time.monotonic()
    hits0 = snap["player"]["hits"]
    first = said = 0.0
    while time.monotonic() - began < 150:
        await asyncio.sleep(1)
        snap = await rpc.call("snapshot", since=0)
        p = snap["player"]
        if p["dead"]:
            print("dead")
            break
        about = [f"{m.get('name')}@{m['distance']} {m.get('hits_pct')}% {m.get('notoriety')}" for m in snap["mobiles"]
                 if not m.get("human") or "guard" in (m.get("name") or "").lower()]
        print(f"{time.monotonic() - began:5.0f}s hits {p['hits']}/{p['hits_max']}", about[:5],
              [e.get("text") for e in (snap.get("journal") or [])[-2:]], flush=True)
        if p["hits"] < hits0 and not first:
            first = time.monotonic()
        if first and not said and (say or time.monotonic() - first > 30 or p["hits"] < 45):
            await rpc.call("act", verb="say", source="manual", text="Guards!")
            said = time.monotonic()
            print(f"  said Guards! {said - first:.0f} s after the first strike", flush=True)
        if said and time.monotonic() - said > 10:
            break
    await rpc.close()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]), sys.argv[2], "--say" in sys.argv))
