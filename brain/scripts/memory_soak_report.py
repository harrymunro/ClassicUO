"""What long-range memory did in a long soak (cuo-6w8.7): the planner's prompt size over the run,
cost an hour, what carried across the brain's restart, and repeated mistakes.

Usage: uv run python scripts/memory_soak_report.py logs/NAME.jsonl [more logs...]
"""
import json
import statistics
import sys
from pathlib import Path


def load(path: Path) -> list[dict]:
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def report(path: Path) -> dict:
    recs = load(path)
    t0 = recs[0]["t"]
    t1 = max(r.get("t", t0) for r in recs)
    hours = (t1 - t0) / 3600
    restart = next((r["t"] for r in recs if r.get("type") == "restart"), None)
    planner = [r for r in recs if r.get("type") == "planner" and r.get("usage")]
    first = [r for r in planner if len(r.get("messages") or []) == 1]  # a step's first call
    tokens = [r["usage"]["prompt_tokens"] for r in first if r["usage"].get("prompt_tokens")]
    by_hour = {}
    for r in first:
        by_hour.setdefault(int((r["t"] - t0) // 3600), []).append(r["usage"].get("prompt_tokens") or 0)
    # Jev's fight calls are costed in the decisions log beside the session log.
    dlog = path.with_name(path.stem + ".decisions" + path.suffix)
    costs = [r for r in recs + (load(dlog) if dlog.exists() else []) if r.get("type") == "ai_cost"]
    goals = [r for r in recs if r.get("type") == "goal"]
    by_kind = {}
    for c in costs:
        by_kind[c["kind"]] = round(by_kind.get(c["kind"], 0) + (c.get("cost") or 0), 5)
    results = [r for r in recs if r.get("type") == "goal_result"]
    refusals = [r for r in results if str((r.get("result") or {}).get("result", "")).startswith("not going back near")]
    hunts = [r for r in results if r.get("tool") == "hunt" and (r.get("result") or {}).get("minutes") is not None]
    # Every death, wherever it came (a hunt, on the way): each leaves a death memory, or with memory
    # off, a resurrect goal follows it.
    deaths = max(sum(1 for r in recs if r.get("type") == "memory" and r.get("source") == "death"),
                 sum(1 for g in goals if g.get("tool") == "resurrect"),
                 sum((r.get("result") or {}).get("deaths", 0) for r in hunts))
    # The same goal failing again right after it failed.
    repeats = 0
    for a, b in zip(results, results[1:]):
        ra, rb = a.get("result") or {}, b.get("result") or {}
        if not ra.get("ok") and not rb.get("ok") and a.get("tool") == b.get("tool") and \
                str(ra.get("result", ""))[:60] == str(rb.get("result", ""))[:60]:
            repeats += 1
    resumed = [r for r in recs if r.get("type") == "resumed"]
    after = [g for g in goals if restart and g["t"] > restart]
    return {
        "log": str(path), "hours": round(hours, 2), "restart_at_h": round((restart - t0) / 3600, 2) if restart else None,
        "planner_calls": len(planner), "steps": len(first),
        "prompt_tokens": {"mean": round(statistics.mean(tokens)) if tokens else None, "max": max(tokens, default=None),
                          "by_hour_mean": {h: round(statistics.mean(v)) for h, v in sorted(by_hour.items())}},
        "cost_usd": round(sum(c.get("cost") or 0 for c in costs), 4),
        "cost_per_hour_usd": round(sum(c.get("cost") or 0 for c in costs) / max(hours, 1e-6), 4),
        "cost_by_kind": dict(sorted(by_kind.items(), key=lambda kv: -kv[1])),
        "hunts": len(hunts), "kills": sum((r.get("result") or {}).get("kills", 0) for r in hunts), "deaths": deaths,
        "refused_goals": len(refusals), "same_failure_twice_running": repeats,
        "failed_goals": sum(1 for r in results if not (r.get("result") or {}).get("ok")),
        "resumed": resumed[0] if resumed else None,
        "first_goal_after_restart": (after[0].get("args") or {}).get("why") if after else None,
        "folds": sum(1 for r in recs if r.get("type") == "fold" and r.get("summary")),
        "heartbeats": sum(1 for r in recs if r.get("type") == "heartbeat" and r.get("usage")),
        "memories_written": sum(1 for r in recs if r.get("type") == "memory"),
        "escalations": sum(1 for r in recs if r.get("type") == "escalation"),
        "strategy_changes": sum(1 for r in results if r.get("tool") == "set_strategy" and (r.get("result") or {}).get("ok")),
    }


if __name__ == "__main__":
    for p in sys.argv[1:]:
        print(json.dumps(report(Path(p)), indent=2))
