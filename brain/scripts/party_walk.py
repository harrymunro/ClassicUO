"""How a party keeps together on a trip (cuo-cod.7): where every member is, second by second.

  uv run python scripts/party_walk.py sample --ports 5661-5665 --out logs/fleet/RUN/where.jsonl [--seconds 600]
  uv run python scripts/party_walk.py report logs/fleet/RUN/where.jsonl [--leader Roland]
  uv run python scripts/party_walk.py trips logs/fleet/RUN

`sample` asks each client for its character's position once a second (the agent RPC's snapshot,
read-only) and writes a line a second, until the time is up or no client answers any more; run it
beside a fleet run. `report` gives, over the time the leader was moving: how long that was and how
far it went, its pace (seconds a tile; on foot a walk is about 0.4 and a run 0.2), how far the
furthest member was from the leader (median and most), the share of that time everyone was within 4
tiles of it, and how far apart the two furthest members were. `trips` gives the same for each trip
the leader made with the party in a run (its travel_to and inn goals, from its session log and the
run's where.jsonl), with how long the leader stood waiting and what the party said on the way.
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

BRAIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRAIN / "src"))

from uo_brain.rpc import AgentRpc  # noqa: E402


def tiles(a: list[int], b: list[int]) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


async def sample(ports: list[int], out: Path, seconds: float) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    rpcs: dict[int, AgentRpc | None] = {p: None for p in ports}
    began, quiet = time.time(), 0
    with out.open("a") as f:
        while time.time() - began < seconds and quiet < 30:
            row: dict[str, list[int]] = {}
            for p in ports:
                try:
                    if rpcs[p] is None:
                        rpcs[p] = AgentRpc(port=p)
                        await rpcs[p].connect(attempts=1)
                    snap = await rpcs[p].call("snapshot", since=1 << 30, timeout=2)
                    if snap.get("in_game"):
                        pl = snap["player"]
                        travel = (snap["agent"].get("travel") or {})
                        follow = (snap["agent"].get("follow") or {})
                        row[pl.get("name") or str(p)] = [pl["x"], pl["y"], int(bool(pl.get("dead"))),
                                                         int(bool(travel.get("running") or follow.get("running")))]
                except Exception:  # noqa: BLE001 - a client not up yet, or gone
                    if rpcs[p] is not None:
                        try:
                            await rpcs[p].close()
                        except Exception:  # noqa: BLE001
                            pass
                    rpcs[p] = None
            quiet = 0 if row else quiet + 1
            if row:
                f.write(json.dumps({"t": round(time.time(), 2), "at": row}) + "\n")
                f.flush()
            await asyncio.sleep(1.0)
    for r in rpcs.values():
        if r is not None:
            await r.close()


def report(path: Path, leader: str | None = None, since: float = 0.0, until: float = 1e18) -> dict:
    rows = [json.loads(line) for line in path.open() if line.startswith("{")]
    rows = [r for r in rows if len(r["at"]) >= 2 and since <= r["t"] <= until]
    if not rows:
        return {"file": str(path), "error": "no samples with two or more members"}
    leader = leader or sorted(rows[0]["at"])[0]
    moving = []
    for a, b in zip(rows, rows[1:]):
        if leader in a["at"] and leader in b["at"] and 0 < tiles(a["at"][leader], b["at"][leader]) <= 6:
            moving.append((a, b))
    if not moving:
        return {"file": str(path), "leader": leader, "error": "the leader never moved"}
    secs = sum(b["t"] - a["t"] for a, b in moving)
    dist = sum(tiles(a["at"][leader], b["at"][leader]) for a, b in moving)
    far, spread, together, running = [], [], 0, 0
    for _, b in moving:
        alive = {n: p for n, p in b["at"].items() if not p[2]}
        if leader not in alive or len(alive) < 2:
            continue
        others = [tiles(p, alive[leader]) for n, p in alive.items() if n != leader]
        far.append(max(others))
        spread.append(max(tiles(p, q) for p in alive.values() for q in alive.values()))
        together += max(others) <= 4
        running += bool(alive[leader][3]) if len(alive[leader]) > 3 else 0
    n = max(len(far), 1)
    return {"file": str(path), "leader": leader, "members": max(len(r["at"]) for r in rows), "moving_s": round(secs), "tiles": dist,
            "seconds_a_tile": round(secs / dist, 2) if dist else None,
            "furthest_from_leader_median": statistics.median(far) if far else None,
            "furthest_from_leader_max": max(far) if far else None,
            "all_within_4_tiles_share": round(together / n, 2),
            "widest_apart_median": statistics.median(spread) if spread else None,
            "leader_running_share": round(running / n, 2)}


def trips(run: Path) -> list[dict]:
    """Each trip the leader made with the party in a fleet run: its travel_to and inn goals."""
    def load(p: Path) -> list[dict]:
        out = []
        if p.exists():
            for line in p.open(errors="replace"):
                if line.startswith("{"):
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        pass
        return out

    fleet = [r for r in load(run / "fleet.jsonl") if r.get("type") == "fleet"][-1]
    leader = fleet.get("leader") or fleet["agents"][0]["name"]
    recs = load(run / f"{leader}.jsonl")
    said = sorted((r["t"], a["name"]) for a in fleet["agents"] for r in load(run / f"{a['name']}.jsonl")
                  if r.get("type") == "talk" and r.get("said"))
    out, goal = [], None
    for r in recs:
        if r.get("type") == "goal" and r.get("tool") in ("travel_to", "inn") and not r.get("party"):
            goal = r
        elif r.get("type") == "goal_result" and goal is not None and r.get("tool") == goal["tool"]:
            res = r.get("result") or {}
            waits = [x for x in recs if x.get("type") == "session" and x.get("event") == "waited_for"
                     and goal["t"] <= x["t"] <= r["t"]]
            end = goal["t"] + (res.get("walk_s") or 1e9) if goal["tool"] == "inn" else r["t"]  # the walk, not the table
            w = report(run / "where.jsonl", leader, goal["t"], min(r["t"], end)) if (run / "where.jsonl").exists() else {}
            out.append({"run": run.name, "goal": goal["tool"], "to": (goal.get("args") or {}).get("place"),
                        "walking": bool(fleet.get("walk")), "ok": res.get("ok"), "result": str(res.get("result"))[:90],
                        "seconds": round(min(r["t"], end) - goal["t"]),
                        "waited_s": round(sum(x.get("seconds") or 0 for x in waits)), "waits": len(waits),
                        "went_on_without": sorted({n for x in waits for n in x.get("went_on_without") or []}),
                        "lines_said_on_the_way": sum(1 for t, _ in said if goal["t"] <= t <= min(r["t"], end)),
                        **{k: w.get(k) for k in ("tiles", "seconds_a_tile", "furthest_from_leader_median",
                                                 "furthest_from_leader_max", "all_within_4_tiles_share",
                                                 "widest_apart_median", "leader_running_share", "error") if k in w}})
            goal = None
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--ports", required=True, help="e.g. 5661-5665 or 5661,5662")
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("--seconds", type=float, default=900)
    r = sub.add_parser("report")
    r.add_argument("files", nargs="+", type=Path)
    r.add_argument("--leader")
    t = sub.add_parser("trips")
    t.add_argument("runs", nargs="+", type=Path)
    args = ap.parse_args()
    if args.cmd == "trips":
        for run in args.runs:
            for trip in trips(run):
                print(json.dumps(trip))
        return
    if args.cmd == "sample":
        lo, _, hi = args.ports.partition("-")
        ports = list(range(int(lo), int(hi) + 1)) if hi else [int(p) for p in args.ports.split(",")]
        asyncio.run(sample(ports, args.out, args.seconds))
    else:
        for f in args.files:
            print(json.dumps(report(f, args.leader)))


if __name__ == "__main__":
    main()
