"""The lich scene of a party run (cuo-4ar): from each member's decision log in a fleet run's folder,
who reached the fight, who died, and how long from the first sight of the lich to its death.

  uv run python scripts/party_scene.py logs/fleet/RUN [logs/fleet/RUN2 ...]

A member reached the fight if it attacked the lich, or saw it within close range (4 tiles), while it
lived. The lich died at the first decision of any member that shows "A Lich's Corpse". A death is a
decision whose health reads dead, or a resurrect goal in the member's session log. Also given, since
deaths are few when a fight goes well: the lowest each member's health fell while anything hostile was
in its view, how many of them fell under a third, and how long after first sight of the lich any member
last had one of the wave (a lich, a bone knight, a skeleton) in view.
"""
import json
import sys
from pathlib import Path

CLOSE = ("adjacent", "close", "nearby")  # distance words up to 4 tiles (state.distance_words)


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
                if any(w in h["name"].lower() for h in hs for w in ("lich", "bone knight", "skeleton")):
                    wave_last = max(wave_last, r["t"])
            lich = [h for h in hs if "lich" in h["name"].lower() and "lord" not in h["name"].lower()]
            if lich:
                saw = saw or r["t"]
                if any(h["distance"].startswith(CLOSE) for h in lich):
                    close = True
            for a in r.get("actions") or []:
                if a.get("verb") in ("attack", "cast") and r.get("target") and "lich" in str(r.get("note", "")).lower():
                    attacked = True
            if any("lich" in (c.get("name") or "").lower() for c in r["state"].get("corpses_not_yet_looted", [])):
                dead_at = min(dead_at or r["t"], r["t"])
        died = any("dead" in r["state"]["you"].get("health", "") and "near" not in r["state"]["you"].get("health", "")
                   for r in dec) or any(r.get("type") in ("goal", "goal_result") and r.get("tool") == "resurrect"
                                        and "rev" not in r for r in session)  # a follower's "rev" ones are the leader's
        if saw:
            first_sight = min(first_sight or saw, saw)
        goals = [r.get("tool") for r in session if r.get("type") == "goal"]
        per[name] = {"saw_lich": saw is not None, "reached": attacked or close, "attacked": attacked, "died": died,
                     "lowest_health": low, "goals": goals[:12]}
    return {"run": run.name, "members": len(members), "reached": sum(p["reached"] for p in per.values()),
            "deaths": sum(p["died"] for p in per.values()),
            "under_a_third": sum(p["lowest_health"] < 33 for p in per.values()),
            "lowest_health": {n: p["lowest_health"] for n, p in per.items()},
            "wave_last_seen_s": round(wave_last - first_sight, 1) if wave_last and first_sight else None,
            "lich_killed_s": round(dead_at - first_sight, 1) if dead_at and first_sight else None,
            "lich_killed": dead_at is not None, "per": per}


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        print(json.dumps(scene(Path(arg))))
