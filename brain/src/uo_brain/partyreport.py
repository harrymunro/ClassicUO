"""What a party did in a run, any run (cuo-cym.7): `uo-brain party-report logs/fleet/RUN`.

`fleet-report` says what each character did and cost; this says what the party did as a party,
from the same logs (fleet.jsonl, each member's session log and decision log, and where.jsonl when
positions were sampled through the run with scripts/party_walk.py):

- **The run:** its settings (plan, form-up, callouts, help, the game's own party, memory, the
  strategy it started with), who never logged in (a run without all its members is void for a
  measurement), and the leader's goals in order with how each ended.
- **Each fight:** when it began and how long it went on, what was fought and what lay dead after
  it, who joined it and how many seconds after the first of them, who fell and how low the others
  got, the leaves, flees and fall backs begun and the leaves held back with the party beside it,
  who healed whom (given and received), what was called out, and (with positions) how far those who fought stood from the leader as it began and for how many of its
  seconds one of them stood more than 10 tiles from every other.
- **Deaths:** who, in which fight, and how long until each was on its feet again.
- **Forming up:** how often the party went in from a rally point, how long its leader waited, and
  whom it went in without.
- **Tables:** each talk round the table, who sat at it, its lines by speaker and how long it took,
  and how many memories were kept of it.
- **Cost:** what the run's AI calls came to, by member and by kind of call.

A fight is found from the decisions themselves: a stretch in which some member acted against a
creature or had one on it, with no more than GAP_S of quiet inside it. scripts/party_scene.py
stays for the lich scene's own measures (when the lich died), which the results in
docs/results.md cite.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

GAP_S = 20.0     # quiet for this long ends a fight
MIN_S = 3.0      # a stretch shorter than this, with nobody hurt, isn't one
AFTER_S = 10.0   # what lies dead, and who is found dead, this long after a fight's last blow belongs to it
JOIN_S = 45.0    # joining later than this after the first is counted, but not in the spread
APART = 10       # tiles: companions nearer than this count in a member's odds (state.PARTY_TILES)
OFFENSIVE = ("attack", "cast", "skill", "pet")
RUNS = ("leave", "flee")
HELD = "held with the party"  # policy.decide's note on a leave it held back
SKIP = ("fleet.jsonl", "where.jsonl")


def load(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not path.exists():
        return out
    for line in path.open():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def health(r: dict[str, Any]) -> int | None:
    """A decision's health in percent; 0 for dead and for nothing else, None when the state doesn't say. Alive
    with a point or two left reads "near death (0%)": live, an archer at that was counted as the run's third
    death, "back after 3.2 s"."""
    hp = r["state"].get("you", {}).get("health", "")
    if "dead" in hp and "near" not in hp:
        return 0
    return max(1, int(hp.split("(")[-1].split("%")[0])) if "(" in hp and "%" in hp else None


def acts_against(r: dict[str, Any]) -> bool:
    return any(a.get("verb") in OFFENSIVE and a.get("reason") not in ("companion", "companion pet", "pet", "raise")
               and (a.get("target") or a.get("targets")) and a.get("target") != "self"
               and not (a.get("verb") == "pet" and a.get("kind") != "kill")
               for a in r.get("actions") or [])


def fighting(r: dict[str, Any]) -> bool:
    """Whether a decision was made in a fight: the member acted against a creature, was on one, or had one on it."""
    hostiles = r["state"].get("hostile_creatures") or []
    if not hostiles or health(r) == 0:
        return False
    return acts_against(r) or any(h.get("your_current_target") for h in hostiles) \
        or any(h.get("aggressive") and not str(h.get("distance", "")).startswith("far") for h in hostiles)


def stretches(times: list[float], gap_s: float = GAP_S) -> list[tuple[float, float]]:
    """Moments merged into stretches with no more than gap_s between two of them."""
    out: list[tuple[float, float]] = []
    for t in sorted(times):
        if out and t - out[-1][1] <= gap_s:
            out[-1] = (out[-1][0], t)
        else:
            out.append((t, t))
    return out


def creature(name: str) -> str:
    """A creature by what it is: "a bone knight (a skeleton)" -> "a bone knight"."""
    return name.split(" (")[0].strip().lower()


# A corpse named for what it is made of, as the creature it was (mates.CORPSE_NAMES).
CORPSE_NAMES = {"a skeletal": "a skeleton", "an orcish": "an orc", "a ratman's": "a ratman"}


def corpse(name: str) -> str:
    """A corpse as the creature it was: "A Lich's Corpse" -> "a lich", "A Skeletal Corpse" -> "a skeleton"."""
    n = name.lower().replace("corpse of ", "").replace("'s corpse", "").replace(" corpse", "").strip()
    return "" if n in ("", "a", "corpse") else CORPSE_NAMES.get(n, n)  # one whose name the client hasn't learnt yet


def deaths_of(name: str, dec: list[dict[str, Any]], session: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A member's deaths and when it was on its feet again. A member that dies in a hunt often has no
    decision that says so (its fight loop ends with it), so its session log is read too: a hunt that
    stopped because it died, and the resurrection that followed. Everything that says dead or alive is put
    in order, and a death is the first sign of one after a sign of life: a resurrection asked for a minute
    after the death, or asked for twice, is the same death."""
    signs: list[tuple[float, str, str]] = []  # (when, "dead" or "alive", how it came back)
    for r in dec:
        hp = health(r)
        if hp is not None:
            signs.append((r["t"], "dead" if hp == 0 else "alive", ""))
    for r in session:
        if r.get("type") == "session" and r.get("event") == "hunted" and str(r.get("stopped_because")) == "died":
            signs.append((r["t"], "dead", ""))
        elif r.get("tool") == "resurrect" and "rev" not in r:  # a follower's "rev" ones are the leader's
            if r.get("type") == "goal":
                signs.append((r["t"], "dead", ""))
            elif r.get("type") == "goal_result" and (r.get("result") or {}).get("ok"):
                how = str((r.get("result") or {}).get("result", ""))
                # How it came back: raised where it fell by a companion (cuo-cym.4), or at a healer.
                signs.append((r["t"], "alive", how.split(",")[0].split(";")[0] if how.startswith("raised") else "at a healer"))
    out: list[dict[str, Any]] = []
    down = False
    for t, what, how in sorted(signs):
        if what == "dead" and not down:
            down = True
            out.append({"who": name, "t": t, "back_t": None, "how": ""})
        elif what == "alive" and down:
            down = False
            out[-1]["back_t"], out[-1]["how"] = t, how
        elif what == "alive" and how and out and not out[-1]["how"] and t - (out[-1]["back_t"] or t) <= 30:
            out[-1]["how"] = how  # the resurrection's own record, a moment after the first decision alive
    return out


def together(rows: list[dict[str, Any]], leader: str, t0: float, t1: float, joined: list[str]) -> dict[str, Any]:
    """From the positions sampled through the run: those who fought as the fight began (how far each stood
    from the leader, or from the first of them in when the leader wasn't in it), and for how many of its
    seconds one of them, alive, stood more than APART tiles from every other. A member away at a healer
    isn't in the fight, and isn't counted as apart from it."""
    def tiles(a: list[int], b: list[int]) -> int:
        return max(abs(a[0] - b[0]), abs(a[1] - b[1]))

    inside = [r for r in rows if t0 - 1 <= r["t"] <= t1]
    if not inside or len(joined) < 2:
        return {}
    first = min(inside, key=lambda r: abs(r["t"] - t0))
    anchor = leader if leader in joined else joined[0]
    at0 = first["at"].get(anchor)
    from_anchor = {n: tiles(at, at0) for n, at in first["at"].items() if n != anchor and n in joined and not at[2]} \
        if at0 else {}
    apart = seconds = 0
    for r in inside:
        alive = {n: at for n, at in r["at"].items() if n in joined and not at[2]}
        if len(alive) < 2:
            continue
        seconds += 1
        apart += any(min(tiles(at, o) for m, o in alive.items() if m != n) > APART for n, at in alive.items())
    return {"from": anchor, "tiles_from_it_at_start": from_anchor, "seconds_someone_apart": apart, "of_seconds": seconds}


def fight_report(t0: float, t1: float, decisions: dict[str, list[dict[str, Any]]], sessions: dict[str, list[dict[str, Any]]],
                 where: list[dict[str, Any]], leader: str, began: float, died: list[dict[str, Any]]) -> dict[str, Any]:
    joined: dict[str, float] = {}
    lowest: dict[str, int] = {}
    hurt_at: dict[str, float] = {}       # when each member's health first fell under 90%
    base: dict[str, Counter[str]] = {}   # corpses each member had in view as the fight began, by kind
    peak: dict[str, Counter[str]] = {}   # ...and the most of each kind it had in view through it
    fell = [d["who"] for d in sorted(died, key=lambda d: d["t"]) if t0 - 2 <= d["t"] <= t1 + AFTER_S]
    runs: list[dict[str, Any]] = []
    helps: Counter[tuple[str, str]] = Counter()
    fought: Counter[str] = Counter()
    held: Counter[str] = Counter()       # leaves held back with the party beside it, by member (each time it began)
    for name, dec in decisions.items():
        last = ""
        holding = False
        streak: dict[str, Any] | None = None  # the run (leave or flee) going on
        for r in dec:
            t = r["t"]
            hp = health(r)
            corpses = Counter(k for c in r["state"].get("corpses_not_yet_looted") or [] if (k := corpse(c.get("name", ""))))
            if t < t0:
                base[name] = corpses
                last = r.get("intent") or ""
                continue
            if t > t1 + AFTER_S:
                break
            peak[name] = peak.get(name, Counter()) | corpses
            if hp and r["state"].get("hostile_creatures"):
                lowest[name] = min(lowest.get(name, 100), hp)
                if hp < 90 and name not in hurt_at and t <= t1:
                    hurt_at[name] = t
            if t <= t1 and name not in joined and acts_against(r) and hp != 0:
                joined[name] = t
            intent = r.get("intent") or ""
            now_held = HELD in str(r.get("note") or "")
            if now_held and not holding and t <= t1:
                held[name] += 1
            holding = now_held
            if intent in RUNS and last not in RUNS and t <= t1:
                streak = {"who": name, "s": round(t - t0, 1), "intent": intent, "health_pct": hp,
                          "why": str(r.get("leave_why") or r.get("note") or "")[:100],
                          **({"fall_back": True} if r.get("regroup") else {})}
                runs.append(streak)
            elif intent in RUNS and streak is not None and streak.get("fall_back") and not r.get("regroup") \
                    and r.get("leave_why"):
                # A fall back that became a leave for good (nobody left in the fight): its hunt ended with it.
                streak["became_a_leave"] = True
            elif intent not in RUNS:
                streak = None
            last = intent
            if t <= t1:
                for h in r["state"].get("hostile_creatures") or []:
                    if h.get("aggressive") or h.get("your_current_target"):
                        fought[creature(h["name"])] += 1
                for a in r.get("actions") or []:
                    # The heal or the bandage itself: the walk to a hurt or fallen companion has the same reason.
                    if a.get("reason") in ("companion", "companion pet") and a.get("verb") in ("cast", "bandage"):
                        whom = next((w.split(" (")[0] for w in str(r.get("note", "")).split("; ")
                                     if w.startswith(("heal ", "bandage "))), "")
                        helps[(name, whom.replace("heal ", "").replace("bandage ", "") or "a companion")] += 1
    calls: Counter[str] = Counter()
    for ses in sessions.values():
        for r in ses:
            if r.get("type") == "callout" and r.get("text") and t0 - 3 <= r["t"] <= t1 + 3:
                calls[r["kind"]] += 1
    first = min(joined.values(), default=t0)
    order = sorted(joined, key=joined.get)
    spread = [joined[n] - first for n in order if joined[n] - first <= JOIN_S]
    # What lay dead after it: by kind, the most any one member saw more of than it had in view as the fight
    # began (a corpse is counted by whoever sees it, and goes from a member's view once it has looted it).
    killed: Counter[str] = Counter()
    for name, seen in peak.items():
        for kind, n in (seen - base.get(name, Counter())).items():
            killed[kind] = max(killed[kind], n)
    return {
        "at_s": round(t0 - began, 1), "seconds": round(t1 - t0, 1),
        "fought": [n for n, _ in fought.most_common(6)],
        "dead_after": [f"{k} x{n}" if n > 1 else k for k, n in sorted(killed.items())],
        "joined": {n: round(joined[n] - first, 1) for n in order}, "first_in": order[0] if order else None,
        "joined_within_s": round(max(spread), 1) if len(spread) > 1 else None,
        "first_hurt": min(hurt_at, key=hurt_at.get) if hurt_at else None,
        "fell": fell, "lowest_health": dict(sorted(lowest.items())),
        "leaves": sum(1 for x in runs if x["intent"] == "leave" and (not x.get("fall_back") or x.get("became_a_leave"))),
        "fall_backs": sum(1 for x in runs if x.get("fall_back")), "flees": sum(1 for x in runs if x["intent"] == "flee"),
        "runs_begun": sorted(runs, key=lambda x: x["s"]),
        "leaves_held": dict(held.most_common()),
        "helps": [{"by": by, "to": to, "times": n} for (by, to), n in helps.most_common()],
        "helped": dict(Counter({to: sum(n for (_, t), n in helps.items() if t == to) for _, to in helps}).most_common()),
        "callouts": dict(calls.most_common()),
        **together(where, leader, t0, t1, order),
    }


def report(run: Path) -> dict[str, Any]:
    run = Path(run)
    fleet = load(run / "fleet.jsonl")
    head = next((r for r in fleet if r.get("type") == "fleet"), {})
    logins = {r["agent"]: r.get("result", "") for r in fleet if r.get("type") == "login"}
    names = sorted(p.name.removesuffix(".decisions.jsonl") for p in run.glob("*.decisions.jsonl"))
    names = [a["name"] for a in head.get("agents") or [] if a["name"] in names] or names
    sessions = {n: load(run / f"{n}.jsonl") for n in names}
    decisions = {n: [r for r in load(run / f"{n}.decisions.jsonl") if r.get("type") == "decision"] for n in names}
    where = [r for r in load(run / "where.jsonl") if "at" in r]
    leader = head.get("leader") or (names[0] if names else "")
    began = min((d[0]["t"] for d in decisions.values() if d), default=head.get("t", 0.0))

    died = sorted((d for n in names for d in deaths_of(n, decisions[n], sessions[n])), key=lambda d: d["t"])
    moments = [r["t"] for dec in decisions.values() for r in dec if fighting(r)]
    fights = []
    for t0, t1 in stretches(moments):
        f = fight_report(t0, t1, decisions, sessions, where, leader, began, died)
        if t1 - t0 >= MIN_S or f["fell"] or f["dead_after"]:
            fights.append(f)
    deaths = [{"who": d["who"], "at_s": round(d["t"] - began, 1),
               "fight": next((i + 1 for i, f in enumerate(fights)
                              if f["at_s"] - 2 <= d["t"] - began <= f["at_s"] + f["seconds"] + AFTER_S), None),
               "back_after_s": round(d["back_t"] - d["t"], 1) if d["back_t"] else None,
               **({"how": d["how"]} if d.get("how") else {})} for d in died]

    goals = []
    ses = sessions.get(leader, [])
    results = [r for r in ses if r.get("type") == "goal_result"]
    for g in (r for r in ses if r.get("type") == "goal"):
        res = next((r for r in results if r["t"] >= g["t"] and r.get("tool") == g.get("tool")), None)
        goals.append({"at_s": round(g["t"] - began, 1), "tool": g.get("tool"),
                      "what": str((g.get("args") or {}).get("area") or (g.get("args") or {}).get("place") or "")[:40],
                      "ok": (res or {}).get("result", {}).get("ok") if res else None,
                      "result": str((res or {}).get("result", {}).get("result", ""))[:140] if res else "not finished"})

    # Whoever held each rally says how it went: the leader, or with a planner each whoever arrived first.
    rallies = sorted((r for n in names for r in sessions[n] if r.get("type") == "rally" and "closed_up_s" not in r),
                     key=lambda r: r["t"])
    formed = [r for r in rallies if r.get("formed")]
    cost_by: Counter[str] = Counter()
    cost_kind: Counter[str] = Counter()
    for n in names:
        for r in sessions[n] + [r for r in load(run / f"{n}.decisions.jsonl") if r.get("type") == "ai_cost"]:
            if r.get("type") == "ai_cost":
                cost_by[n] += r.get("cost") or 0
                cost_kind[r.get("kind") or "?"] += r.get("cost") or 0
    game = [r for s in sessions.values() for r in s if r.get("type") == "game_party"]
    # Each table is written up by whoever kept its turns; what each member kept of it, by itself.
    kept: Counter[int] = Counter()
    for ses_ in sessions.values():
        for r in ses_:
            if r.get("type") == "table" and "kept" in r:
                kept[r.get("table")] += len(r.get("kept") or [])
    tables = [{"at_s": round(r["t"] - 60 * (r.get("minutes") or 0) - began, 1), "minutes": r.get("minutes"), "lines": r.get("lines"),
               "speakers": r.get("speakers") or {}, "skipped": r.get("skipped") or {}, "memories_kept": kept.get(r["closed"], 0)}
              for n in names for r in sessions[n] if r.get("type") == "table" and "closed" in r]
    end = max((d[-1]["t"] for d in decisions.values() if d), default=began)
    return {
        "run": run.name, "leader": leader, "members": names, "minutes": round((end - began) / 60, 1),
        "settings": {k: head[k] for k in ("plan", "form_up", "rally_map", "callouts", "help", "game_party", "callouts_to",
                                           "walk", "roleplay", "memory", "strategy", "use", "rules") if k in head},
        "not_in_game": sorted(n for n, r in logins.items() if not r.startswith("in game")),
        "void": any(not r.startswith("in game") for r in logins.values()),
        "goals": goals, "fights": fights, "deaths": deaths,
        "forming_up": {"formed": len(formed), "waited_s": [r.get("waited_s") for r in formed],
                       "went_in_without": [r["without"] for r in formed if r.get("without")],
                       "not_formed": [str(r.get("why", ""))[:80] for r in rallies if not r.get("formed")]} if rallies else {},
        "game_party": dict(Counter(r.get("event") for r in game)) if game else {},
        "tables": sorted(tables, key=lambda x: x["at_s"]),
        "cost_usd": round(sum(cost_by.values()), 4), "cost_by_member": {n: round(c, 4) for n, c in cost_by.items()},
        "cost_by_kind": {k: round(c, 4) for k, c in cost_kind.most_common()},
    }


def render(r: dict[str, Any]) -> str:
    s = r["settings"]
    on = [k for k in ("form_up", "rally_map", "callouts", "help", "game_party", "walk", "roleplay") if s.get(k)]
    lines = [f"{r['run']}: {len(r['members'])} members led by {r['leader']}, {r['minutes']} min"
             + (f", plan {s['plan']}" if s.get("plan") else "") + (f"; on: {', '.join(on)}" if on else "")
             + ("; memory off" if s.get("memory") is False else "")
             + (f"; strategy {s['strategy']!r}" if "strategy" in s else "")]
    if r["void"]:
        lines.append(f"  VOID for a measurement: never in game: {', '.join(r['not_in_game'])}")
    if r["goals"]:
        lines.append("  the leader's goals: " + "; ".join(
            f"{g['tool']}{' ' + g['what'] if g['what'] else ''} ({'ok' if g['ok'] else 'no' if g['ok'] is False else '...'})"
            for g in r["goals"]))
    fu = r["forming_up"]
    if fu:
        lines.append(f"  formed up {fu['formed']} time(s), waited {fu['waited_s']} s"
                     + (f", went in without {fu['went_in_without']}" if fu["went_in_without"] else "")
                     + (f"; not formed: {fu['not_formed']}" if fu["not_formed"] else ""))
    if r["game_party"]:
        lines.append(f"  the game's party: {r['game_party']}")
    for i, f in enumerate(r["fights"], 1):
        lines.append(f"  fight {i} at {f['at_s']:.1f} s, {f['seconds']:.1f} s: {', '.join(f['fought']) or 'creatures'}"
                     + (f"; dead after it: {', '.join(f['dead_after'])}" if f["dead_after"] else ""))
        if f["joined"]:
            lines.append("    joined: " + ", ".join(f"{n} +{s:.1f}" for n, s in f["joined"].items())
                         + (f" (all within {f['joined_within_s']:.1f} s)" if f["joined_within_s"] is not None else ""))
        lines.append("    lowest health: " + ", ".join(f"{n} {h}%" for n, h in f["lowest_health"].items())
                     + (f"; first hurt: {f['first_hurt']}" if f.get("first_hurt") else "")
                     + (f"; fell: {', '.join(f['fell'])}" if f["fell"] else "; nobody fell"))
        if f["runs_begun"]:
            lines.append(f"    {f['leaves']} leaves, {f['fall_backs']} fall backs, {f['flees']} flees: " + "; ".join(
                f"{x['who']} at {x['s']:.1f} s ({x['health_pct']}%{', fell back' if x.get('fall_back') else ''}"
                f"{', then left for good' if x.get('became_a_leave') else ''})"
                for x in f["runs_begun"][:8]))
        if f.get("leaves_held"):
            lines.append("    leaves held back with the party: " + ", ".join(f"{n} x{c}" for n, c in f["leaves_held"].items()))
        if f["helps"]:
            lines.append("    helped: " + ", ".join(f"{h['by']} -> {h['to']} x{h['times']}" for h in f["helps"][:8])
                         + "; received: " + ", ".join(f"{n} x{c}" for n, c in (f.get("helped") or {}).items()))
        if f["callouts"]:
            lines.append("    called: " + ", ".join(f"{k} {n}" for k, n in f["callouts"].items()))
        if "of_seconds" in f:
            lines.append(f"    one of them stood apart for {f['seconds_someone_apart']} of {f['of_seconds']} s; tiles from "
                         f"{f['from']} as it began: " + ", ".join(f"{n} {d}" for n, d in f["tiles_from_it_at_start"].items()))
    if r["deaths"]:
        lines.append("  deaths: " + "; ".join(
            f"{d['who']} at {d['at_s']:.1f} s" + (f" (fight {d['fight']})" if d["fight"] else "")
            + (f", back after {d['back_after_s']:.1f} s" + (f" ({d['how']})" if d.get("how") else "")
               if d["back_after_s"] is not None else ", not back by the end")
            for d in r["deaths"]))
    else:
        lines.append("  no deaths")
    for t in r.get("tables") or []:
        lines.append(f"  table at {t['at_s']:.1f} s, {t['minutes']} min: {t['lines']} lines ("
                     + ", ".join(f"{n} {c}" for n, c in t["speakers"].items()) + ")"
                     + (f"; passed over: {', '.join(t['skipped'])}" if t["skipped"] else "")
                     + f"; {t['memories_kept']} memories kept of it")
    lines.append(f"  cost ${r['cost_usd']:.4f}: " + ", ".join(f"{k} ${c:.4f}" for k, c in list(r["cost_by_kind"].items())[:6]))
    return "\n".join(lines)
