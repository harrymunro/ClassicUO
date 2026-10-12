"""A party's talk in a fleet run (epic cuo-cod): who said what and when, what it cost, and whether it could be followed.

  uv run python scripts/party_talk.py logs/fleet/RUN [--transcript] [--json]

From each member's session log: the lines it said in conversation (talk.py), at the inn's table
(table.py), its callouts in fights (mates.py) and its emotes and gestures (roleplay.py). Reports,
for each member and together: lines an hour of each kind, the share that was small talk, stories
told, what the talk cost (the chat, talk and table kinds) an hour, the gap between a line and its
answer (median and shortest), lines dropped and why, and how many lines started before the last
speaker's line could be read (talk.hold_s), or while it was still over its head at all (talk.show_s:
about 4 s for each overhead line of text).
--transcript prints everything said, in order.
"""

import argparse
import json
import statistics
import sys
from pathlib import Path

BRAIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRAIN / "src"))

from uo_brain.talk import hold_s, show_s  # noqa: E402

TALK_KINDS = ("chat", "talk", "table")


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


def report(run: Path) -> dict:
    members = sorted(p.name.removesuffix(".jsonl") for p in run.glob("*.jsonl")
                     if not p.name.endswith(".decisions.jsonl") and p.name not in ("fleet.jsonl", "where.jsonl"))
    said: list[dict] = []   # everything shown over a head: t, who, text, kind
    per: dict[str, dict] = {}
    for name in members:
        recs = load(run / f"{name}.jsonl")
        ts = [r["t"] for r in recs if r.get("t")]
        hours = max((max(ts) - min(ts)) / 3600, 1e-9) if ts else 1e-9
        talk = [r for r in recs if r.get("type") == "talk"]
        lines = [r for r in talk if r.get("said")]
        table = [r for r in recs if r.get("type") == "table" and r.get("said")]
        calls = [r for r in recs if r.get("type") == "callout" and r.get("text")]
        acts = [r for r in recs if r.get("type") == "roleplay" and r.get("did")]
        dropped: dict[str, int] = {}
        for r in talk:
            if r.get("dropped"):
                dropped[r["dropped"]] = dropped.get(r["dropped"], 0) + 1
        cost = sum(r.get("cost") or 0 for r in recs if r.get("type") == "ai_cost" and r.get("kind") in TALK_KINDS)
        voice = sum((r.get("usage") or {}).get("cost") or 0 for r in recs if r.get("type") == "voice")
        delays = [r["delay_s"] for r in lines if r.get("delay_s") is not None]
        per[name] = {
            "minutes": round(hours * 60, 1), "lines": len(lines), "lines_per_hour": round(len(lines) / hours, 1),
            "small_talk": sum(1 for r in lines if r.get("small")), "stories": [r["story"] for r in lines if r.get("story")],
            "table_lines": len(table), "callouts": len(calls), "callouts_per_hour": round(len(calls) / hours, 1),
            "emotes_and_gestures": len(acts) + sum(1 for r in table if r.get("emote")),
            "roleplay_per_hour": round(len(acts) / hours, 1),
            "asked_jev": sum(1 for r in talk if r.get("considered") or r.get("said")),
            "dropped": dropped, "talk_cost_usd": round(cost, 5), "talk_cost_per_hour_usd": round(cost / hours, 4),
            "voice_pack_cost_usd": round(voice, 5),
            "reply_delay_s_median": round(statistics.median(delays), 1) if delays else None,
            "reply_delay_s_min": round(min(delays), 1) if delays else None,
            "chars_per_line_avg": round(statistics.fmean(len(r["text"]) for r in lines)) if lines else None,
        }
        said += [{"t": r["t"], "who": name, "text": r["text"], "kind": "small talk" if r.get("small") else "talk",
                  "delay_s": r.get("delay_s"), "to": r.get("to")} for r in lines]
        for r in table:
            if r.get("emote"):
                said.append({"t": r["t"] - 1.5, "who": name, "text": f"*{r['emote']}*", "kind": "emote"})
            said.append({"t": r["t"], "who": name, "text": r["text"], "kind": f"table ({r.get('phase')})"})
        said += [{"t": r["t"], "who": name, "text": r["text"], "kind": f"callout ({r.get('kind')})"} for r in calls]
        said += [{"t": r["t"], "who": name, "text": f"*{r['did']}*" if r["did"] not in ("bow", "salute") else f"({r['did']}s)",
                  "kind": f"roleplay ({r.get('event')})"} for r in acts]
    said.sort(key=lambda r: r["t"])
    # Conversation lines (talk and table) that started while another speaker's line was still up. Callouts aren't
    # held back, so they aren't counted.
    convo = [r for r in said if r["kind"].startswith(("talk", "small talk", "table"))]
    # ...before the last one could be read (talk.hold_s: 1.5 s and 0.06 s a character), and, more strictly, while it
    # was still over its speaker's head at all (the client keeps it about 4 s for each overhead line of text).
    overlaps = sum(1 for a, b in zip(convo, convo[1:]) if a["who"] != b["who"] and b["t"] < a["t"] + hold_s(a["text"]) - 0.3)
    still_up = sum(1 for a, b in zip(convo, convo[1:]) if a["who"] != b["who"] and b["t"] < a["t"] + show_s(a["text"]) - 0.5)
    gaps = [b["t"] - a["t"] for a, b in zip(convo, convo[1:]) if b["t"] - a["t"] < 60]
    tables = [r for name in members for r in load(run / f"{name}.jsonl") if r.get("type") == "table" and "closed" in r]
    total_lines = sum(p["lines"] for p in per.values())
    return {"run": run.name, "members": len(members), "per": per, "together": {
        "lines": total_lines, "small_talk_share": round(sum(p["small_talk"] for p in per.values()) / total_lines, 2) if total_lines else None,
        "table_lines": sum(p["table_lines"] for p in per.values()), "callouts": sum(p["callouts"] for p in per.values()),
        "emotes_and_gestures": sum(p["emotes_and_gestures"] for p in per.values()),
        "talk_cost_usd": round(sum(p["talk_cost_usd"] for p in per.values()), 5),
        "gap_between_lines_s_median": round(statistics.median(gaps), 1) if gaps else None,
        "gap_between_lines_s_min": round(min(gaps), 1) if gaps else None,
        "lines_started_before_the_last_was_read": overlaps, "lines_started_while_the_last_was_still_up": still_up,
        "conversation_lines": len(convo),
        "tables": [{k: v for k, v in t.items() if k not in ("t", "type")} for t in tables]},
        "transcript": said}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", type=Path)
    ap.add_argument("--transcript", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    for run in args.runs:
        r = report(run)
        if args.json:
            print(json.dumps(r if args.transcript else {k: v for k, v in r.items() if k != "transcript"}))
            continue
        t = r["together"]
        print(f"{r['run']}: {t['lines']} lines of talk ({t['small_talk_share'] or 0:.0%} small talk), {t['table_lines']} at "
              f"the table, {t['callouts']} callouts, {t['emotes_and_gestures']} emotes and gestures; talk cost "
              f"${t['talk_cost_usd']:.4f}; between lines {t['gap_between_lines_s_median']} s (shortest "
              f"{t['gap_between_lines_s_min']}); {t['lines_started_before_the_last_was_read']} of {t['conversation_lines']} lines "
              f"started before the last could be read ({t['lines_started_while_the_last_was_still_up']} while it was still up)")
        for name, p in r["per"].items():
            print(f"  {name:<8} {p['minutes']:>5} min  talk {p['lines']:>2} ({p['lines_per_hour']}/h, {p['small_talk']} small)  "
                  f"table {p['table_lines']:>2}  callouts {p['callouts']:>2}  roleplay {p['emotes_and_gestures']:>2}  "
                  f"${p['talk_cost_per_hour_usd']:.4f}/h  reply {p['reply_delay_s_median']} s  dropped {p['dropped'] or '-'}")
        for tb in t["tables"]:
            print(f"  table: {json.dumps(tb)}")
        if args.transcript:
            t0 = r["transcript"][0]["t"] if r["transcript"] else 0
            for ln in r["transcript"]:
                m, s = divmod(int(ln["t"] - t0), 60)
                print(f"  {m:02d}:{s:02d} {ln['who']:<7} {ln['text']}   [{ln['kind']}]")


if __name__ == "__main__":
    main()
