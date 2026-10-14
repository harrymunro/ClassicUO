"""The lich scene of a party run (cuo-4ar): from each member's decision log in a fleet run's folder,
who reached the fight, who died, and how long from the first sight of the lich to its death.

  uv run python scripts/party_scene.py logs/fleet/RUN [logs/fleet/RUN2 ...]
  uv run python scripts/party_scene.py --wave "skeletal mage,bone magi,ratman archer,skeleton" --boss "" logs/fleet/RUN

Another scene (cuo-cym.11) names its own wave with --wave, and its strongest creature with --boss ("" when it
has none: then the first sight is of any of the wave, a member reached the fight when it joined it, and
nothing is said of the boss's death).

A member reached the fight if it attacked the lich, or saw it within close range (4 tiles), while it
lived. The lich died at the first decision of any member that shows "A Lich's Corpse". A death is a
decision whose health reads dead, or a resurrect goal in the member's session log. Also given, since
deaths are few when a fight goes well: the lowest each member's health fell while anything hostile was
in its view, how many of them fell under a third, and how long after first sight of the lich any member
last had one of the wave (a lich, a bone knight, a skeleton) in view.

How together the party was (cuo-cym.1): when each member joined the fight (its first attack, spell, song or
pet order with the wave in view, or the first time one of the wave was on it), as seconds after the first of
them, and the spread between first and last; the leaves and flees begun, by whom and at what health, and how
many of them were fall backs to the party's rally point; and what each member's forming up came to (its
`rally` records). With the run's where.jsonl beside the logs (scripts/party_walk.py sample), from the
members' positions second by second: how far each was from the leader when the first of them joined the
fight, and for how many of the next 60 seconds some living member stood more than 10 tiles from every other
(outside the 10 tiles that make a party in the odds).
"""
import json
import sys
from pathlib import Path

CLOSE = ("adjacent", "close", "nearby")  # distance words up to 4 tiles (state.distance_words)
WAVE: tuple[str, ...] = ("lich", "bone knight", "skeleton")  # the lich scene's; --wave for another's
BOSS = "lich"                                                 # ...and --boss ("" for a wave without one)
OFFENSIVE = ("attack", "cast", "skill", "pet")
APART = 10       # tiles: companions nearer than this count in a member's odds (state.PARTY_TILES)
FIGHT_S = 60.0   # how long after the first join the party's keeping together is measured


def dead(r: dict) -> bool:
    hp = r["state"]["you"].get("health", "")
    return "dead" in hp and "near" not in hp


def joined_at(dec: list[dict]) -> float | None:
    """When a member joined the fight: its first act against the wave, or the first time one of it was on it."""
    for r in dec:
        wave = [h for h in r["state"].get("hostile_creatures", []) if any(w in h["name"].lower() for w in WAVE)]
        if not wave or dead(r):
            continue
        acted = any(a.get("verb") in OFFENSIVE and (a.get("target") or a.get("targets")) and a.get("target") != "self"
                    for a in r.get("actions") or [])
        if acted or any(h.get("your_current_target") for h in wave) \
                or any(h.get("aggressive") and not h["distance"].startswith("far") for h in wave):
            return r["t"]
    return None


def runs_begun(name: str, dec: list[dict], t0: float | None) -> list[dict]:
    """The leaves and flees a member began: the first decision of each run of them."""
    out, last = [], ""
    for r in dec:
        intent = r.get("intent") or ""
        if intent in ("leave", "flee") and last not in ("leave", "flee"):
            hp = r["state"]["you"].get("health", "")
            out.append({"who": name, "s": round(r["t"] - t0, 1) if t0 else None, "intent": intent,
                        "health_pct": int(hp.split("(")[-1].split("%")[0]) if "(" in hp and "%" in hp else None,
                        "why": str(r.get("leave_why") or r.get("note") or "")[:120],
                        **({"fall_back": True} if r.get("regroup") or "rally" in str(r.get("note")) else {})})
        last = intent
    return out


def together(run: Path, leader: str, t0: float) -> dict:
    """From the positions sampled through the run (where.jsonl): the party at the moment the fight began, and
    how long in the minute after it somebody alive stood apart from all the others."""
    rows = [r for r in load(run / "where.jsonl") if "at" in r]
    if not rows:
        return {}
    start = min(rows, key=lambda r: abs(r["t"] - t0))
    if abs(start["t"] - t0) > 3:
        return {}

    def tiles(a: list[int], b: list[int]) -> int:
        return max(abs(a[0] - b[0]), abs(a[1] - b[1]))

    lead = start["at"].get(leader)
    from_leader = {n: tiles(at, lead) for n, at in start["at"].items() if n != leader and not at[2]} if lead else {}
    apart = seconds = 0
    for r in rows:
        if not 0 <= r["t"] - t0 <= FIGHT_S:
            continue
        alive = {n: at for n, at in r["at"].items() if not at[2]}
        if len(alive) < 2:
            continue
        seconds += 1
        apart += any(min(tiles(at, other) for m, other in alive.items() if m != n) > APART for n, at in alive.items())
    return {"from_leader_at_first_join": from_leader, "furthest_from_leader_at_first_join": max(from_leader.values(), default=None),
            "seconds_someone_apart": apart, "of_seconds": seconds}


def load(path: Path) -> list[dict]:
    out = []
    if not path.exists():
        return out
    for line in path.open():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def scene(run: Path) -> dict:
    members = sorted(p.name.removesuffix(".decisions.jsonl") for p in run.glob("*.decisions.jsonl"))
    first_sight, dead_at, per = None, None, {}
    wave_last = 0.0
    for name in members:
        dec = [r for r in load(run / f"{name}.decisions.jsonl") if r.get("type") == "decision"]
        session = load(run / f"{name}.jsonl")
        lich_serials, saw, attacked, close = set(), None, False, False
        low = 100
        for r in dec:
            hs = r["state"].get("hostile_creatures", [])
            if hs:
                hp = r["state"]["you"].get("health", "")
                if "(" in hp and "%" in hp:
                    low = min(low, int(hp.split("(")[-1].split("%")[0]))
                if any(w in h["name"].lower() for h in hs for w in WAVE):
                    wave_last = max(wave_last, r["t"])
            # The boss (the lich, and not a lich lord), or with none any of the wave.
            lich = [h for h in hs if BOSS in h["name"].lower() and "lord" not in h["name"].lower()] if BOSS else                 [h for h in hs if any(w in h["name"].lower() for w in WAVE)]
            if lich:
                saw = saw or r["t"]
                if any(h["distance"].startswith(CLOSE) for h in lich):
                    close = True
            for a in r.get("actions") or []:
                if BOSS and a.get("verb") in ("attack", "cast") and r.get("target") and BOSS in str(r.get("note", "")).lower():
                    attacked = True
            if BOSS and any(BOSS in (c.get("name") or "").lower() for c in r["state"].get("corpses_not_yet_looted", [])):
                dead_at = min(dead_at or r["t"], r["t"])
        died = any("dead" in r["state"]["you"].get("health", "") and "near" not in r["state"]["you"].get("health", "")
                   for r in dec) or any(r.get("type") in ("goal", "goal_result") and r.get("tool") == "resurrect"
                                        and "rev" not in r for r in session)  # a follower's "rev" ones are the leader's
        if saw:
            first_sight = min(first_sight or saw, saw)
        goals = [r.get("tool") for r in session if r.get("type") == "goal"]
        joined = joined_at(dec)
        per[name] = {"saw_lich": saw is not None, "reached": attacked or close if BOSS else joined is not None,
                     "attacked": attacked, "died": died,
                     "lowest_health": low, "goals": goals[:12], "joined": joined, "dec": dec,
                     "rallies": [{k: v for k, v in r.items() if k not in ("type", "t")} for r in session
                                 if r.get("type") == "rally"]}
    joined = {n: p["joined"] for n, p in per.items() if p["joined"]}
    began = min(joined.values(), default=None)
    head = next((r for r in load(run / "fleet.jsonl") if r.get("type") == "fleet"), {})
    leader = head.get("leader") or (members[0] if members else "")
    runs = sorted((x for n, p in per.items() for x in runs_begun(n, p.pop("dec"), began)), key=lambda x: x["s"] or 0)
    for p in per.values():
        p["joined"] = round(p["joined"] - began, 1) if p["joined"] and began else None
    order = sorted(joined, key=joined.get)
    formed = [r for r in per.get(leader, {}).get("rallies", []) if r.get("formed")]
    return {"run": run.name, "members": len(members), "reached": sum(p["reached"] for p in per.values()),
            "form_up": bool(head.get("form_up")), "first_in": order[0] if order else None,
            "joined_within_s": round(max(joined.values()) - began, 1) if len(joined) > 1 else None,
            "leaves": sum(1 for x in runs if x["intent"] == "leave"), "flees": sum(1 for x in runs if x["intent"] == "flee"),
            "fall_backs": sum(1 for x in runs if x.get("fall_back")), "runs_begun": runs[:20],
            **({"formed_up": len(formed), "waited_for_the_party_s": [r.get("waited_s") for r in formed],
                "went_in_without": [r.get("without") for r in formed if r.get("without")]} if head.get("form_up") else {}),
            **(together(run, leader, began) if began else {}),
            "deaths": sum(p["died"] for p in per.values()),
            "under_a_third": sum(p["lowest_health"] < 33 for p in per.values()),
            "lowest_health": {n: p["lowest_health"] for n, p in per.items()},
            "wave_last_seen_s": round(wave_last - first_sight, 1) if wave_last and first_sight else None,
            **({"lich_killed_s": round(dead_at - first_sight, 1) if dead_at and first_sight else None,
                "lich_killed": dead_at is not None} if BOSS else {}), "per": per}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+", type=Path)
    ap.add_argument("--wave", help="the creatures of the scene's wave, by name, comma-separated (default: the lich scene's)")
    ap.add_argument("--boss", help='the strongest creature of the wave, whose death is timed ("" for none; default: lich)')
    args = ap.parse_args()
    if args.wave:
        WAVE = tuple(w.strip().lower() for w in args.wave.split(",") if w.strip())
    if args.boss is not None:
        BOSS = args.boss.strip().lower()
    for run in args.runs:
        print(json.dumps(scene(run)))
