"""Checks the client's line of sight against the server's (cuo-ehm).

The character is moved to a list of spots (the test kit's [AgentGo x y]); at each, the server's
[AgentSight sample] picks tiles around it and says whether the character sees a mobile standing
there and the ground there; the client's `sight` method answers the same points by its own rule
(AgentSight.cs, a copy of ModernUO's Map.LineOfSight over the client's map). The report gives the
agreement rate and every disagreement, for the README and for finding where the rules part.
"""

import asyncio
import re
import time
from pathlib import Path
from typing import Any

from .rpc import AgentRpc

# Around Britain (Felucca): streets, squares, shop fronts, the bank, the park, the river and the
# graveyard, so walls, windows, trees, floors and the town wall all come up.
BRITAIN_SPOTS: list[tuple[int, int]] = [
    (1424, 1694), (1438, 1686), (1455, 1640), (1475, 1617), (1478, 1601), (1490, 1650),
    (1500, 1625), (1466, 1676), (1430, 1600), (1445, 1560), (1490, 1560), (1520, 1660),
    (1386, 1494), (1405, 1625),
]

LINE = re.compile(r"AgentSight (-?\d+) (-?\d+) (-?\d+): (.*)")
EYE = 14


def parse(lines: list[str]) -> list[dict[str, Any]]:
    """The server's sample lines: one dict per tile with the origin, the two target heights and the
    server's answers."""
    out = []
    for text in lines:
        m = LINE.match(text.strip())
        if not m:
            continue
        ox, oy, oz = (int(v) for v in m.group(1, 2, 3))
        for item in m.group(4).split(";"):
            if not item.strip():
                continue
            x, y, zm, sm, zg, sg = (int(v) for v in item.split(","))
            out.append({"from": [ox, oy, oz + EYE], "x": x, "y": y, "zm": zm, "mobile": bool(sm), "zg": zg,
                        "ground": bool(sg)})
    return out


async def journal_until(rpc: AgentRpc, since: int, done: str, timeout: float = 10.0) -> tuple[list[str], int]:
    """Journal lines after `since` until one starts with `done` (or the timeout)."""
    lines: list[str] = []
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        snap = await rpc.call("snapshot", since=since, radius=1)
        for e in snap.get("journal", []):
            since = max(since, e.get("seq", since))
            lines.append(e.get("text", ""))
        if any(t.startswith(done) for t in lines):
            break
        await asyncio.sleep(0.3)
    return lines, since


async def run(rpc: AgentRpc, spots: list[tuple[int, int] | None], per_spot: int = 40, radius: int = 15, seed: int = 1,
              progress=print) -> dict[str, Any]:
    """Each spot is x, y to go to with [AgentGo, or None for where the character stands (after laying
    walls with [AgentBuild, say)."""
    result: dict[str, Any] = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "per_spot": per_spot, "radius": radius,
                              "seed": seed, "spots": [], "disagreements": []}
    total = agree = 0
    by_kind = {"mobile": [0, 0], "ground": [0, 0]}
    for i, spot in enumerate(spots):
        snap = await rpc.call("snapshot", since=0, radius=1)
        since = snap.get("journal_seq", 0)
        x, y = spot or (snap["player"]["x"], snap["player"]["y"])
        if spot is not None:
            await rpc.call("act", verb="say", text=f"[AgentGo {x} {y}", source="manual")
            await asyncio.sleep(2.0)  # the move, and the client loading the map around the new spot
        snap = await rpc.call("snapshot", since=since, radius=1)
        since = max([since] + [e.get("seq", since) for e in snap.get("journal", [])])
        await rpc.call("act", verb="say", text=f"[AgentSight sample {per_spot} {radius} {seed + i}", source="manual")
        lines, since = await journal_until(rpc, since, "AgentSight done")
        samples = parse(lines)
        if not samples:
            progress(f"spot {x},{y}: no answer from the server")
            continue
        pairs = []
        for s in samples:
            pairs.append([*s["from"], s["x"], s["y"], s["zm"]])
            pairs.append([*s["from"], s["x"], s["y"], s["zg"]])
        client = (await rpc.call("sight", pairs=pairs))["in_sight"]
        n = ok = 0
        for k, s in enumerate(samples):
            for kind, mine in (("mobile", client[2 * k]), ("ground", client[2 * k + 1])):
                n += 1
                by_kind[kind][1] += 1
                if mine == s[kind]:
                    ok += 1
                    by_kind[kind][0] += 1
                else:
                    result["disagreements"].append({"kind": kind, "from": s["from"], "to": [s["x"], s["y"],
                                                    s["zm" if kind == "mobile" else "zg"]], "server": s[kind],
                                                    "client": mine})
        total += n
        agree += ok
        in_sight = sum(s["mobile"] for s in samples) + sum(s["ground"] for s in samples)
        result["spots"].append({"x": x, "y": y, "from": samples[0]["from"], "pairs": n, "agree": ok,
                                "in_sight_by_server": in_sight})
        progress(f"spot {x},{y} (eye z {samples[0]['from'][2]}): {ok}/{n} agree, {in_sight} in sight")
    result["pairs"] = total
    result["agree"] = agree
    result["rate"] = round(agree / total, 4) if total else None
    result["by_kind"] = {k: {"agree": a, "pairs": n, "rate": round(a / n, 4) if n else None}
                         for k, (a, n) in by_kind.items()}
    return result


def write(result: dict[str, Any], out: Path) -> None:
    import json

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2))
