"""Attended play live (cuo-49t): a client whose settings.json has agent_attended_minutes, in assist mode with nobody at
the keyboard. Waits until the agent says it is away, sets an orc on the character, watches 20 s, then turns auto mode
on (which counts as the player's input) and watches again; ends with the agent off and the character reset.

  uv run python scripts/attended_check.py [PORT]      # default 5590
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from uo_brain.rpc import AgentRpc

async def main():
    r = AgentRpc(port=int(sys.argv[1]) if len(sys.argv) > 1 else 5590); await r.connect(attempts=10)
    async def look(tag):
        s = await r.call("snapshot", since=0)
        p, a = s["player"], s["agent"]
        orcs = [(m["name"], m["distance"], m.get("hits_pct")) for m in s.get("mobiles") or [] if m.get("monster") and not m.get("dead")]
        print(f"{time.strftime('%H:%M:%S')} {tag}: away={a.get('away')} mode={a.get('mode')} hits={p['hits']}/{p['hits_max']} "
              f"engaged={a.get('engaged')} bandaging={a.get('bandaging')} ms_since_human={a.get('ms_since_human')} near={orcs[:3]}", flush=True)
        return s
    s = await look("start")
    while not s["agent"].get("away"):
        await asyncio.sleep(5); s = await look("waiting")
    await r.call("act", verb="say", text="[AgentSpawn Orc 1 2 n", source="manual")
    for i in range(10):
        await asyncio.sleep(2); s = await look("away, an orc on it")
    ev = [e.get("text") for e in s.get("journal") or [] if "away" in str(e.get("text", "")) or "back" in str(e.get("text", ""))][-3:]
    print("journal:", ev)
    await r.call("mode", mode="auto")  # turning a mode on counts as the player's input
    for i in range(8):
        await asyncio.sleep(2); s = await look("mode turned on again")
    await r.call("mode", mode="off")
    await r.call("act", verb="say", text="[AgentReset", source="manual")
    await r.close()

asyncio.run(main())
