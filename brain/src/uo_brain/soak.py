"""A report on an unattended session, from its logs (`uo-brain soak-report LOG`).

`uo-brain session` writes the planner's goals, what came of them and the session's own events
(travel, hunts, banking, buying, routine questions) to LOG, and Jev's fight decisions to
LOG.decisions.jsonl. This puts them together: which goals the planner chose and why, the hunt
loops, kills and deaths, where the time went, what was bought and banked, and what Jev and the
planner cost an hour. Disruptions applied from outside during the run (a JSONL file of
{t, what}) are matched to the goals that followed them, to see how the planner adapted.
"""

import json
import re
from pathlib import Path
from typing import Any

from . import logs
from .loop import LoopConfig


def report(log: Path, disruptions: Path | None = None, price_per_million: float = LoopConfig.price_per_million
           ) -> dict[str, Any]:
    records = logs.read(log)
    decisions_log = log.with_name(log.stem + logs.DECISIONS + log.suffix)
    decisions = [d for d in logs.read(decisions_log) if d["type"] == "decision"] if decisions_log.exists() else []
    if not records:
        return {"log": str(log), "error": "empty log"}
    t0, t1 = records[0]["t"], max(r.get("t", 0) for r in records)
    hours = max((t1 - t0) / 3600, 1e-6)
    minute = lambda t: round((t - t0) / 60, 1)  # noqa: E731

    goals = [r for r in records if r["type"] == "goal"]
    results = [r for r in records if r["type"] == "goal_result"]
    events = [r for r in records if r["type"] == "session"]
    hunts = [e for e in events if e.get("event") == "hunted"]
    travels = [e for e in events if e.get("event") == "travelled"]
    buys = [e for e in events if e.get("event") == "buy"]
    banks = [e for e in events if e.get("event") == "bank"]

    # Where the time went: each goal from its start to its result; what lies between is the
    # planner thinking (or anything else that isn't a goal).
    spans: dict[str, float] = {}
    for g in goals:
        done = next((r for r in results if r["t"] >= g["t"] and r.get("tool") == g.get("tool")), None)
        if done:
            spans[g["tool"]] = spans.get(g["tool"], 0.0) + (done["t"] - g["t"])
    busy = sum(spans.values())

    gold_banked = 0
    for b in banks:
        if m := re.search(r"gold -(\d+)", str(b.get("detail", ""))):
            gold_banked += int(m[1])
    bought: dict[str, int] = {}
    for b in buys:
        bought[b.get("item", "?")] = bought.get(b.get("item", "?"), 0) + int(b.get("got") or 0)

    planner_cost = sum((r.get("usage") or {}).get("cost") or 0.0 for r in records if r["type"] == "planner")
    planner_calls = sum(1 for r in records if r["type"] == "planner")
    jev_tokens = sum((d.get("answers") or {}).get("input_tokens", 0) for d in decisions)
    routine = [r for r in records if r["type"] == "routine"]
    routine_tokens = sum((r.get("answers") or {}).get("input_tokens", 0) for r in routine)
    jev_cost = (jev_tokens + routine_tokens) * price_per_million / 1e6

    out: dict[str, Any] = {
        "log": str(log),
        "minutes": round((t1 - t0) / 60, 1),
        "goals": [{"minute": minute(g["t"]), "goal": g["tool"], "args": {k: v for k, v in (g.get("args") or {}).items()
                                                                       if k != "why"},
                   "why": (g.get("args") or {}).get("why", "")} for g in goals],
        "goal_counts": {k: sum(1 for g in goals if g["tool"] == k) for k in sorted({g["tool"] for g in goals})},
        "goals_failed": [{"minute": minute(r["t"]), "goal": r["tool"], "result": (r.get("result") or {}).get("result", "")}
                         for r in results if not (r.get("result") or {}).get("ok", True)],
        "hunts": len(hunts),
        "kills": sum(h.get("kills", 0) for h in hunts),
        "deaths": sum(h.get("deaths", 0) for h in hunts) + sum(1 for r in results if "died" in str(r.get("result"))),
        "minutes_per_loop": round((t1 - t0) / 60 / len(hunts), 1) if hunts else None,
        "hunt_stops": [h.get("stopped_because", "") for h in hunts],
        "time_minutes": {**{k: round(v / 60, 1) for k, v in sorted(spans.items())},
                         "between goals (planner)": round(((t1 - t0) - busy) / 60, 1)},
        "travel": {"trips": len(travels), "arrived": sum(1 for t in travels if t.get("state") == "arrived"),
                   "stuck_spots": sum(len(t.get("stuck_at") or []) for t in travels),
                   "minutes": round(sum(t.get("seconds", 0) for t in travels) / 60, 1)},
        "bought": bought,
        "gold_banked": gold_banked,
        "gold_from_hunts": sum(h.get("gold_gained", 0) for h in hunts),
        "fight_decisions": len(decisions),
        "routine_questions": len(routine),
        "planner_calls": planner_calls,
        "cost_usd": {"planner": round(planner_cost, 4), "jev": round(jev_cost, 5)},
        "cost_per_hour_usd": {"planner": round(planner_cost / hours, 4), "jev": round(jev_cost / hours, 4)},
    }
    if disruptions and disruptions.exists():
        out["disruptions"] = []
        for d in _jsonl(disruptions):
            after = [g for g in goals if g["t"] >= d["t"]][:3]
            out["disruptions"].append({
                "minute": minute(d["t"]), "what": d.get("what", ""),
                "next_goals": [f"{g['tool']} {(g.get('args') or {}).get('area') or (g.get('args') or {}).get('place') or ''}"
                               .strip() + f": {(g.get('args') or {}).get('why', '')}" for g in after]})
    return out


def _jsonl(path: Path) -> list[dict[str, Any]]:
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def markdown(r: dict[str, Any]) -> str:
    """The report as a short Markdown summary."""
    lines = [f"{r['minutes']} minutes, {r['hunts']} hunts, {r['kills']} kills, {r['deaths']} deaths; "
             f"{r['gold_banked']} gold banked ({r['gold_from_hunts']} picked up in hunts); bought "
             + (", ".join(f"{n} {k}" for k, n in r["bought"].items()) or "nothing") + ".",
             f"Time: " + ", ".join(f"{k} {v} min" for k, v in r["time_minutes"].items()) + ".",
             f"Travel: {r['travel']['trips']} trips, {r['travel']['arrived']} arrived, "
             f"{r['travel']['stuck_spots']} stuck spots, {r['travel']['minutes']} min walking.",
             f"Cost: planner ${r['cost_usd']['planner']} ({r['planner_calls']} calls, "
             f"${r['cost_per_hour_usd']['planner']}/h), Jev ${r['cost_usd']['jev']} "
             f"({r['fight_decisions']} fight decisions, {r['routine_questions']} routine questions, "
             f"${r['cost_per_hour_usd']['jev']}/h).",
             "", "Goals:"]
    lines += [f"- {g['minute']} min: {g['goal']} {g['args']}: {g['why']}" for g in r["goals"]]
    if r["goals_failed"]:
        lines += ["", "Goals that failed:"] + [f"- {g['minute']} min: {g['goal']}: {g['result']}" for g in r["goals_failed"]]
    if r.get("hunt_stops"):
        lines += ["", "Why each hunt ended:"] + [f"- {s}" for s in r["hunt_stops"]]
    for d in r.get("disruptions", []):
        lines += ["", f"Disruption at {d['minute']} min: {d['what']}. Next goals:"] + [f"- {g}" for g in d["next_goals"]]
    return "\n".join(lines)
