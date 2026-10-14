"""Buying at the Britain healer, checked three ways (cuo-k0d). On a test server of your own: it moves the
character about with [AgentGo and restocks the shop.

  uv run python scripts/buy_check.py soak   OUT.jsonl [--runs 15] [--port 5580] [--shard local] [--count 30]
  uv run python scripts/buy_check.py words  OUT.jsonl [--runs 3]  [--port 5580]
  uv run python scripts/buy_check.py staged OUT.jsonl [--runs 5]  [--port 5580]
  uv run python scripts/buy_check.py report OUT.jsonl [--brief]

soak: a soak run's first goal, over and over: the prep of scripts/soak_prep.py (shop restocked, the
character at the West Britain bank with 30 bandages and 400 gold), then the session's `buy` of bandages
from the healer, while a watcher writes down where the character, the healer and the guildmistress stand
and what is said. `report` prints each run of such a file: the result, the vendor asked and how, and who
stood within two tiles at each ask.

words: who answers "vendor buy" and "NAME buy", by where the speaker stands. It puts the character on a
tile within one of both the healer and the healer guildmistress and says the words; the healer's
"Greetings. Have a look around." is its list opening.

staged: the client's own buy errand (`act buy`, 5 bandages) from a tile where the guildmistress hears
the character before the healer does, both within a tile.

ModernUO gives "vendor buy" to the vendor nearest the speaker within a tile, and the guildmistress is a
vendor who shows no list. `hears` in the records is the server's measure of who hears first
(LocationComparer: (11 dx)^2 + (11 dy)^2 on one floor), less is sooner.
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from uo_brain.rpc import AgentRpc  # noqa: E402
from uo_brain.session import Session  # noqa: E402
from uo_brain.world import World  # noqa: E402

PREP = ("[AgentReset", "[AgentGo 1471 1609", "[AgentRestock 60", "[AgentGo 1425 1690", "[AgentKit",
        "[AgentSupplies bandages 30 gold 400")
APART = ((1476, 1612), (1470, 1608), (1477, 1608), (1468, 1614), (1474, 1615))  # places in and by the shop


def hears(dx: int, dy: int) -> int:
    return (11 * dx) ** 2 + (11 * dy) ** 2


async def say(r: AgentRpc, text: str, pause: float = 1.5) -> None:
    await r.call("act", verb="say", text=text, source="manual")
    await asyncio.sleep(pause)


async def prep(r: AgentRpc) -> None:
    await r.call("mode", mode="off")
    await r.call("goal", clear=True)
    for c in PREP:
        await say(r, c)
    await say(r, "[AgentDisrupt restore quiet", 8)


async def step_away(r: AgentRpc, pause: float = 4) -> None:
    """To the place by the shop furthest from the healer and the guildmistress, and wait: a vendor that has
    just answered stays turned to a customer within two tiles of it, and the two have to wander until they
    stand a tile apart again. Near enough that they go on wandering (they stand still with nobody about)."""
    _, f, _ = await the_two(r)
    folk = [(m["x"], m["y"]) for m in f.values()] or [(1471, 1611)]
    x, y = max(APART, key=lambda p: min(max(abs(p[0] - fx), abs(p[1] - fy)) for fx, fy in folk))
    await say(r, f"[AgentGo {x} {y}", pause)


async def the_two(r: AgentRpc) -> tuple[tuple[int, int], dict, dict]:
    """Where the character stands, the healer and the guildmistress (serial, name, x, y), and the snapshot."""
    s = await r.call("snapshot", since=0)
    p = s["player"]
    out = {}
    for m in s["mobiles"]:
        label = (m.get("label") or "").lower()
        if "healer" in label:
            out["guild" if "guild" in label else "healer"] = {"serial": m["serial"], "name": m["name"],
                                                             "x": p["x"] + m["dx"], "y": p["y"] + m["dy"]}
    return (p["x"], p["y"]), out, s


async def stand(r: AgentRpc, nearer: str) -> tuple[dict, dict, tuple[int, int], dict] | None:
    """Put the character on a tile within one of both, where `nearer` ("guild" or "healer") hears it first.
    None when they don't stand so just now, or somebody moved meanwhile."""
    _, f, _ = await the_two(r)
    if "healer" not in f or "guild" not in f:
        await say(r, "[AgentGo 1472 1611", 2)
        return None
    h, g = f["healer"], f["guild"]
    spots = []
    for x in range(h["x"] - 1, h["x"] + 2):
        for y in range(h["y"] - 1, h["y"] + 2):
            if (x, y) in ((h["x"], h["y"]), (g["x"], g["y"])) or max(abs(x - g["x"]), abs(y - g["y"])) > 1:
                continue
            mh, mg = hears(h["x"] - x, h["y"] - y), hears(g["x"] - x, g["y"] - y)
            if mg < mh if nearer == "guild" else mh < mg:
                spots.append((x, y))
    if not spots:
        await step_away(r, 2.5)  # they wander: wait until they stand a tile apart
        return None
    x, y = spots[0]
    await say(r, f"[AgentGo {x} {y}", 0.9)
    at, f2, s = await the_two(r)
    h2, g2 = f2.get("healer"), f2.get("guild")
    if at != (x, y) or not h2 or not g2 or (h2["x"], h2["y"], g2["x"], g2["y"]) != (h["x"], h["y"], g["x"], g["y"]):
        return None
    return h, g, (x, y), s


def where(h: dict, g: dict, at: tuple[int, int]) -> dict:
    return {"at": list(at), "healer": [h["x"], h["y"]], "guild": [g["x"], g["y"]],
            "healer_hears": hears(h["x"] - at[0], h["y"] - at[1]), "guild_hears": hears(g["x"] - at[0], g["y"] - at[1])}


# ------------------------------------------------------------------ soak

async def watch(port: int, out, stop: asyncio.Event, run: int) -> None:
    r = AgentRpc(port=port)
    await r.connect()
    last = None
    try:
        since = (await r.call("snapshot", since=0))["journal_seq"]
        while not stop.is_set():
            s = await r.call("snapshot", since=since)
            p = s["player"]
            folk = [{"serial": m["serial"], "label": m.get("label") or m.get("name"), "dx": m["dx"], "dy": m["dy"],
                     "d": m["distance"], "hears": hears(m["dx"], m["dy"])}
                    for m in s["mobiles"] if m.get("human") and m.get("notoriety") == "invulnerable" and m["distance"] <= 8]
            folk.sort(key=lambda f: f["hears"])
            e = s["agent"].get("errand") or {}
            lines = [{"seq": j["seq"], "name": j.get("name"), "text": j.get("text")} for j in s.get("journal", [])]
            if lines:
                since = max(j["seq"] for j in lines)
            key = (p["x"], p["y"], json.dumps(folk), e.get("state"), e.get("detail"))
            if key != last or lines:
                last = key
                out.write(json.dumps({"type": "watch", "run": run, "t": time.time(), "at": [p["x"], p["y"]], "errand": e,
                                      "folk": folk, "journal": lines}) + "\n")
                out.flush()
            await asyncio.sleep(0.12)
    finally:
        await r.close()


async def soak(args) -> None:
    out = args.out.open("a")
    r = AgentRpc(port=args.port)
    await r.connect()
    world = World.open(args.shard)
    bought = 0
    for run in range(1, args.runs + 1):
        await prep(r)
        p = (await r.call("snapshot", since=0))["player"]
        out.write(json.dumps({"type": "start", "run": run, "t": time.time(), "at": [p["x"], p["y"]], "gold": p.get("gold"),
                              "bandages": p["supplies"].get("bandages")}) + "\n")
        session = Session(r, world, None, log=lambda rec: (out.write(json.dumps({**rec, "run": run}) + "\n"), out.flush()))
        await r.call("mode", mode="auto")
        stop = asyncio.Event()
        watcher = asyncio.create_task(watch(args.port, out, stop, run))
        t0 = time.time()
        res = await session.buy("bandage", args.count, "healer")
        stop.set()
        await watcher
        out.write(json.dumps({"type": "result", "run": run, "t": time.time(), "seconds": round(time.time() - t0, 1),
                              **res.to_tool()}) + "\n")
        out.flush()
        bought += res.ok
        print(run, res.ok, res.summary, flush=True)
    print(f"{bought} of {args.runs} buys bought everything")
    world.close()
    await r.close()


def report(args) -> None:
    runs: list[list[dict]] = []
    for line in args.out.open():
        rec = json.loads(line)
        if rec.get("type") == "start":
            runs.append([])
        if runs:
            runs[-1].append(rec)
    ok = 0
    for n, rr in enumerate(runs, 1):
        res = next((r for r in rr if r.get("type") == "result"), {})
        buy = next((r for r in rr if r.get("type") == "session" and r.get("event") == "buy"), {})
        ok += bool(res.get("ok"))
        print(f"run {n}: ok={res.get('ok')} {res.get('seconds')} s  {res.get('result')}")
        if buy.get("vendors"):
            print("   vendors:", json.dumps(buy["vendors"]))
        if args.brief:
            continue
        for r in rr:
            for j in r.get("journal") or []:
                t = j.get("text") or ""
                if j.get("name") not in (None, "System", "agent") and t.lower().endswith((" buy", " sell")):
                    folk = ", ".join(f"{f['label']} ({f['dx']:+d},{f['dy']:+d}) d{f['d']}" for f in r["folk"] if f["d"] <= 2)
                    print(f"   {t!r} drawn at {r['at']}; within 2 tiles: {folk}")
                elif "Have a look around" in t:
                    print(f"     -> {j.get('name')} showed its list")
    print(f"{ok} of {len(runs)} buys bought everything")


# ------------------------------------------------------------------ words

async def heard(r: AgentRpc, since: int, secs: float = 2.5) -> list[str]:
    began, lines = time.monotonic(), []
    while time.monotonic() - began < secs:
        s = await r.call("snapshot", since=since)
        for j in s.get("journal", []):
            since = max(since, j["seq"])
            if j.get("name") not in ("System", "agent"):
                lines.append(f"{j.get('name')}: {j.get('text')}")
        await asyncio.sleep(0.15)
    return lines


async def words(args) -> None:
    out = args.out.open("a")
    r = AgentRpc(port=args.port)
    await r.connect()
    await r.call("mode", mode="off")
    done = {"plain_guild_nearer": 0, "named_guild_nearer": 0, "plain_healer_nearer": 0}
    for _ in range(80):
        if min(done.values()) >= args.runs:
            break
        case = min(done, key=done.get)
        stood = await stand(r, "guild" if case.endswith("guild_nearer") else "healer")
        if stood is None:
            continue
        h, g, at, s = stood
        said = f"{h['name']} buy" if case.startswith("named") else "vendor buy"
        await r.call("act", verb="say", text=said, source="manual")
        lines = await heard(r, s["journal_seq"])
        rec = {"case": case, "said": said, **where(h, g, at), "list_shown": any("Have a look around" in l for l in lines),
               "heard": lines}
        out.write(json.dumps(rec) + "\n")
        out.flush()
        print(json.dumps(rec), flush=True)
        done[case] += 1
        await step_away(r)
    await r.close()


# ------------------------------------------------------------------ staged

async def staged(args) -> None:
    out = args.out.open("a")
    r = AgentRpc(port=args.port)
    await r.connect()
    await r.call("mode", mode="off")
    for c in ("[AgentGo 1472 1611", "[AgentRestock 60", "[AgentSupplies bandages 30 gold 400"):
        await say(r, c)
    done = 0
    for _ in range(120):
        if done >= args.runs:
            break
        await r.call("mode", mode="off")
        stood = await stand(r, "guild")
        if stood is None:
            continue
        h, g, at, s = stood
        seq, before = s["journal_seq"], s["player"]["supplies"].get("bandages")
        await r.call("mode", mode="auto")
        began = time.monotonic()
        res = await r.call("act", verb="buy", target=h["serial"], items="bandage:5")
        e, said = {}, []
        while time.monotonic() - began < 40:
            await asyncio.sleep(0.2)
            s = await r.call("snapshot", since=seq)
            for j in s.get("journal", []):
                seq = max(seq, j["seq"])
                if j.get("name") not in ("System", "agent"):
                    said.append(f"{j.get('name')}: {j.get('text')}")
            e = s["agent"].get("errand") or {}
            if e.get("state") in ("done", "failed"):
                break
        rec = {**where(h, g, at), "act": res.get("status"), "errand": e, "seconds": round(time.monotonic() - began, 1),
               "bandages": [before, s["player"]["supplies"].get("bandages")], "heard": said}
        out.write(json.dumps(rec) + "\n")
        out.flush()
        print(json.dumps(rec), flush=True)
        done += 1
        await r.call("mode", mode="off")
        await step_away(r, 5)
    await r.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("what", choices=["soak", "words", "staged", "report"])
    ap.add_argument("out", type=Path, help="JSON lines, appended to (report: read)")
    ap.add_argument("--runs", type=int)
    ap.add_argument("--port", type=int, default=5580)
    ap.add_argument("--shard", default="local", help="soak: the world store the session reads and writes")
    ap.add_argument("--count", type=int, default=30, help="soak: bandages to buy")
    ap.add_argument("--brief", action="store_true", help="report: one or two lines a run")
    args = ap.parse_args()
    if args.what == "report":
        report(args)
        return
    args.runs = args.runs or {"soak": 15, "words": 3, "staged": 5}[args.what]
    asyncio.run({"soak": soak, "words": words, "staged": staged}[args.what](args))


if __name__ == "__main__":
    main()
