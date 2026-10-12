"""A party's callouts and help in a fleet run (cuo-cod.2, cuo-4ti): what was called, who heard it and how soon, who helped whom.

  uv run python scripts/party_calls.py logs/fleet/RUN [RUN2 ...] [--json]

- **Callouts:** from the party store (table party_calls, where each callout's meaning is written the
  moment code decides on it) and each member's session log (the `callout` records: what was said
  aloud, and when). By kind and by member, and how many were said aloud.
- **Heard:** a callout reaches a companion when one of that companion's fight decisions has it in its
  state (`party_calls`). For each callout and each companion who had the caller in view then
  (`your_party`), the time from the callout to that decision; the share within 2 s.
- **Help:** the actions sent for a companion (reason "companion": a bandage, a Greater Heal, a walk
  over to one; "companion pet": a bandage on another's pet), by helper and by kind, and the
  decisions in which a member's target was a creature on a companion.
"""

import argparse
import json
import sqlite3
import statistics
import sys
from pathlib import Path

BRAIN = Path(__file__).resolve().parents[1]
WORDS = {"hurt": "hurt", "badly_hurt": "near death", "recovered": "back on their feet", "on_target": "fighting",
         "on_me": "is attacking them", "leaving": "running from the fight", "out_of_bandages": "out of bandages",
         "out_of_reagents": "out of reagents", "out_of_arrows": "out of arrows", "down": "is dead",
         "clear": "the fight is over", "helping": "healing"}


def load(path: Path) -> list[dict]:
    out = []
    if path.exists():
        for line in path.open(errors="replace"):
            if line.startswith("{"):
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


def report(run: Path, store: Path) -> dict:
    fleet = [r for r in load(run / "fleet.jsonl") if r.get("type") == "fleet"][-1:]
    fleet = fleet[0] if fleet else {}
    party = fleet.get("party_id") or f"{fleet.get('name')}-{run.name}"
    members = [a["name"] for a in fleet.get("agents", [])]
    calls = []
    if store.exists():
        db = sqlite3.connect(str(store))
        db.row_factory = sqlite3.Row
        try:
            calls = [dict(r) for r in db.execute("SELECT * FROM party_calls WHERE party = ? ORDER BY t", (party,))]
        except sqlite3.OperationalError:
            calls = []
        db.close()
    decisions = {m: [r for r in load(run / f"{m}.decisions.jsonl") if r.get("type") == "decision"] for m in members}
    said = {m: [r for r in load(run / f"{m}.jsonl") if r.get("type") == "callout" and r.get("text")] for m in members}
    by_kind: dict[str, int] = {}
    by_member = {m: 0 for m in members}
    for c in calls:
        by_kind[c["kind"]] = by_kind.get(c["kind"], 0) + 1
        by_member[c["who"]] = by_member.get(c["who"], 0) + 1
    # Heard: the first decision of each companion, after the callout, whose state carries it.
    delays, missed = [], 0
    for c in calls:
        phrase = WORDS.get(c["kind"], c["kind"])
        for m in members:
            if m == c["who"]:
                continue
            after = [d for d in decisions[m] if c["t"] <= d["t"] <= c["t"] + 15]
            in_view = [d for d in after if any(p.get("name") == c["who"] for p in d["state"].get("your_party", []))]
            if not in_view:
                continue  # that companion didn't have the caller in view, or made no decision then
            hit = next((d for d in in_view if any(w.startswith(f"{c['who']} (") and phrase in w
                                                   for w in d["state"].get("party_calls", []))), None)
            if hit is None:
                missed += 1
            else:
                delays.append(hit["t"] - c["t"])
    helps: dict[str, dict[str, int]] = {}
    rescues = 0
    for m in members:
        for d in decisions[m]:
            for a in d.get("actions") or []:
                if str(a.get("reason", "")).startswith("companion"):
                    kind = {"bandage": "bandage", "cast": "heal spell", "walk_to": "walk over"}.get(a["verb"], a["verb"])
                    if a.get("reason") == "companion pet":
                        kind = "bandage a pet"
                    helps.setdefault(m, {})
                    helps[m][kind] = helps[m].get(kind, 0) + 1
            target = d.get("target")
            if target and any(h.get("attacking_your_companion") and h.get("your_current_target")
                              for h in d["state"].get("hostile_creatures", [])) and d.get("intent") == "fight":
                rescues += 1
    fights = sum(1 for m in members for d in decisions[m] if d.get("intent") == "fight")
    return {"run": run.name, "party": party, "callouts": len(calls), "by_kind": dict(sorted(by_kind.items(), key=lambda kv: -kv[1])),
            "by_member": by_member, "said_aloud": sum(len(v) for v in said.values()),
            "heard_by_a_companion_in_view": len(delays), "not_heard_within_15_s": missed,
            "heard_within_2_s": round(sum(1 for x in delays if x <= 2.0) / len(delays), 3) if delays else None,
            "heard_delay_s_median": round(statistics.median(delays), 2) if delays else None,
            "helps": helps, "helps_total": sum(sum(v.values()) for v in helps.values()),
            "decisions_fighting_what_is_on_a_companion": rescues, "fight_decisions": fights}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", type=Path)
    ap.add_argument("--store", type=Path, default=BRAIN / "worlds" / "local" / "world.sqlite")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    for run in args.runs:
        r = report(run, args.store)
        if args.json:
            print(json.dumps(r))
            continue
        print(f"{r['run']}: {r['callouts']} callouts ({r['said_aloud']} said aloud): {r['by_kind']}")
        print(f"  heard by a companion in view: {r['heard_by_a_companion_in_view']} times, {r['heard_within_2_s']} within 2 s "
              f"(median {r['heard_delay_s_median']} s); not heard within 15 s: {r['not_heard_within_15_s']}")
        print(f"  help: {r['helps_total']} actions {r['helps']}; fight decisions on what is on a companion: "
              f"{r['decisions_fighting_what_is_on_a_companion']} of {r['fight_decisions']}")


if __name__ == "__main__":
    sys.exit(main())
