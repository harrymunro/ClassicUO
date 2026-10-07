"""Packs sent at the soak character (the client on $SOAK_PORT, 5580 by default), quietly so the
agent can't read about them: three gargoyles 14 tiles from it at 8 minutes, a lich and two bone
knights at the graveyard at 25. Each waits for the character to be hunting near the graveyard, for
up to 15 minutes. argv: disruptions file to append {t, minute, what, player_at}, the session log
(to tell when it hunts), and optionally the session's start time."""
import asyncio, json, os, sys, time
from uo_brain.rpc import AgentRpc

PORT = int(os.environ.get("SOAK_PORT", "5580"))

GRAVEYARD = (1378, 1480)
PLAN = [
    (8 * 60, "three gargoyles 14 tiles from the character", "ahead"),
    (25 * 60, "a lich and two bone knights at the graveyard", "graveyard"),
]


def hunting(session_log):
    """Is the session's current goal a hunt? (uo-brain session doesn't set the client's goal step.)"""
    goal = None
    for line in open(session_log):
        if '"type": "goal"' in line:
            goal = json.loads(line).get("tool")
    return goal == "hunt"


def near(s, xy, d):
    return max(abs(s["player"]["x"] - xy[0]), abs(s["player"]["y"] - xy[1])) <= d


async def main():
    out, session_log = sys.argv[1], sys.argv[2]
    t0 = float(sys.argv[3]) if len(sys.argv) > 3 else time.time()
    log = open(out, "a")
    for at, what, where in PLAN:
        await asyncio.sleep(max(0, t0 + at - time.time()))
        r = AgentRpc(port=PORT)
        await r.connect()
        s = await r.call("snapshot", since=0)
        while not (near(s, GRAVEYARD, 25) and hunting(session_log)) and time.time() - t0 < at + 15 * 60 \
                and not s["player"]["dead"]:
            await asyncio.sleep(5)
            s = await r.call("snapshot", since=0)
        p = s["player"]
        cmds = [f"[AgentDisrupt strong {p['x']} {p['y'] - 14} Gargoyle 3 quiet"] if where == "ahead" else \
            [f"[AgentDisrupt strong {GRAVEYARD[0]} {GRAVEYARD[1]} Lich 1 quiet",
             f"[AgentDisrupt strong {GRAVEYARD[0] - 6} {GRAVEYARD[1] + 8} BoneKnight 2 quiet"]
        for c in cmds:
            await r.call("act", verb="say", text=c, source="manual")
            await asyncio.sleep(1)
        rec = {"t": time.time(), "minute": round((time.time() - t0) / 60, 1), "what": what,
               "player_at": [p["x"], p["y"]], "hunting": hunting(session_log)}
        log.write(json.dumps(rec) + "\n"); log.flush()
        print(json.dumps(rec), flush=True)
        await r.close()

asyncio.run(main())
