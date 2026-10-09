"""A report on an unattended session, from its logs (`uo-brain soak-report LOG`).

`uo-brain session` writes the planner's goals, what came of them and the session's own events
(travel, hunts, banking, buying, routine questions) to LOG, and Jev's fight decisions to
LOG.decisions.jsonl. This puts them together: which goals the planner chose and why, the hunt
loops, kills and deaths, where the time went, what was bought and banked, and what Jev and the
planner cost an hour. Disruptions applied from outside during the run (a JSONL file of
{t, what}) are matched to the goals that followed them, to see how the planner adapted.

Costs come from the logs' `ai_cost` records (costs.py): what each call cost as the provider
reported it. Logs from before those were written are costed the old way: the planner's
reported cost, and Jev's input tokens at its list price.
"""

import json
import re
from pathlib import Path
from typing import Any

from . import costs, logs

JEV_PRICE = costs.PRICES["typesafe/jev"][0]  # for logs without cost records


def report(log: Path, disruptions: Path | None = None, price_per_million: float = JEV_PRICE) -> dict[str, Any]:
    records = logs.read(log)
    decisions_log = log.with_name(log.stem + logs.DECISIONS + log.suffix)
    dlog = logs.read(decisions_log) if decisions_log.exists() else []
    decisions = [d for d in dlog if d["type"] == "decision"]
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

    planner_calls = sum(1 for r in records if r["type"] == "planner")
    routine = [r for r in records if r["type"] == "routine"]
    kills = sum(h.get("kills", 0) for h in hunts)
    recorded = costs.from_records(records + dlog, hours=hours, kills=kills or None, decisions=len(decisions) or None)
    if recorded:
        system2 = recorded["by_role"].get("system2", 0.0)
        system1 = recorded["by_role"].get("system1", 0.0)
    else:
        system2 = sum((r.get("usage") or {}).get("cost") or 0.0 for r in records if r["type"] == "planner")
        jev_tokens = sum((d.get("answers") or {}).get("input_tokens", 0) for d in decisions)
        routine_tokens = sum((r.get("answers") or {}).get("input_tokens", 0) for r in routine)
        system1 = (jev_tokens + routine_tokens) * price_per_million / 1e6

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
        "kills": kills,
        # A death outside a hunt ends the session: its summary says so, or a goal ends at 0 health.
        # One in a hunt is in the hunt's count already.
        "deaths": max(sum(h.get("deaths", 0) for h in hunts), 1 if any(
            "died" in str(r.get("finished", "")) for r in records if r["type"] == "session_summary") or any(
            str((r.get("result") or {}).get("health", "")).startswith("0/") for r in results) else 0),
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
        "cost_usd": {"system1": round(system1, 5), "system2": round(system2, 4),
                     "total": round(system1 + system2, 5)},
        "cost_per_hour_usd": {"system1": round(system1 / hours, 4), "system2": round(system2 / hours, 4),
                              "total": round((system1 + system2) / hours, 4)},
        "cost_per_kill_usd": round((system1 + system2) / kills, 5) if kills else None,
        "costs_recorded": recorded is not None,
    }
    if recorded:
        out["costs"] = recorded
    checks = [r for r in records if r["type"] == "openrouter_key"]
    if len(checks) >= 2:
        out["openrouter_key_spent_usd"] = round(checks[-1]["usage"] - checks[0]["usage"], 6)
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
             f"{r['gold_banked']} gold banked ({r['gold_from_hunts']} gold gained in hunts, less what was left on a corpse); bought "
             + (", ".join(f"{n} {k}" for k, n in r["bought"].items()) or "nothing") + ".",
             f"Time: " + ", ".join(f"{k} {v} min" for k, v in r["time_minutes"].items()) + ".",
             f"Travel: {r['travel']['trips']} trips, {r['travel']['arrived']} arrived, "
             f"{r['travel']['stuck_spots']} stuck spots, {r['travel']['minutes']} min walking.",
             f"Cost: ${r['cost_usd']['total']} (${r['cost_per_hour_usd']['total']}/h"
             + (f", ${r['cost_per_kill_usd']} a kill" if r.get("cost_per_kill_usd") else "") + "); "
             f"system two ${r['cost_usd']['system2']} ({r['planner_calls']} planner calls, "
             f"${r['cost_per_hour_usd']['system2']}/h), system one ${r['cost_usd']['system1']} "
             f"({r['fight_decisions']} fight decisions, {r['routine_questions']} routine questions, "
             f"${r['cost_per_hour_usd']['system1']}/h)"
             + ("" if r.get("costs_recorded") else ", estimated from tokens: the log has no cost records")
             + (f". OpenRouter's own count for the key: ${r['openrouter_key_spent_usd']}"
                if "openrouter_key_spent_usd" in r else "") + ".",
             "", "Goals:"]
    lines += [f"- {g['minute']} min: {g['goal']} {g['args']}: {g['why']}" for g in r["goals"]]
    if r["goals_failed"]:
        lines += ["", "Goals that failed:"] + [f"- {g['minute']} min: {g['goal']}: {g['result']}" for g in r["goals_failed"]]
    if r.get("hunt_stops"):
        lines += ["", "Why each hunt ended:"] + [f"- {s}" for s in r["hunt_stops"]]
    for d in r.get("disruptions", []):
        lines += ["", f"Disruption at {d['minute']} min: {d['what']}. Next goals:"] + [f"- {g}" for g in d["next_goals"]]
    return "\n".join(lines)
