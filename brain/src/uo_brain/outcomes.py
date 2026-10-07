"""What happened where: results per area and kit from session logs, in the world store.

The spawn data says what lives in an area, not how hunting there goes for this character.
A session log does. Each hunt ends with a `hunted` record (area, kit, minutes, kills,
deaths, bandages and heal potions used, gold gained), and the decisions logged during it
show the character's health, its supplies and what it was fighting.

import_logs() turns each hunt (one loop) into outcome rows, one per loop and metric:

    kills_per_loop, kills_per_hour, deaths, minutes_per_loop, gold_per_loop, gold_per_hour,
    bandages_per_kill, bandages_per_hour, heal_potions_per_kill, heal_potions_per_hour

and, for each kind of creature fought in the loop, that loop's totals fights_vs_<kind>,
bandages_vs_<kind>, heal_potions_vs_<kind> and health_pct_lost_vs_<kind>. Then it writes
one note per area in plain words, from every loop imported there: "Britain Graveyard: 12
kills a loop for the warrior kit over 3 loops ...; wraiths cost the most bandages, 4.2 a fight".

Rows carry the log's file name as their session, and importing a log again replaces that
session's rows, including the ones Session.hunt wrote live, so re-importing is safe. Logs
of plain tactical runs (`uo-brain run`, `scenario`, `bench`) have no hunts and no area;
given an area, each run in them counts as a loop there.

How costs are charged to creatures. Between two consecutive decisions of a loop, at most
30 s apart, the health lost (points of the character's maximum), bandages used and heal
potions drunk go to the creature the character was fighting at the first of the two, or, if
it was fighting nobody, to the nearest creature within 3 tiles; with nothing that close they
go to nobody. A fight is one creature attacked (one serial). The limits:
- health is net of any healing between the two decisions, so it undercounts damage taken
  while bandaging;
- while several creatures attack at once, everything goes to the one being fought;
- time spent dead, fleeing or looting, when the loop doesn't decide, is left out;
- creatures with personal names are grouped by kind only when the client knew their body
  ("Vitavi (a ratman)").
So it says what fights with each kind cost, not what each kind did, and a kind needs at
least three fights before the note names it.
"""

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import logs
from .world import World

MAX_GAP_S = 30.0      # longer between two decisions and the costs go to nobody
SLACK_S = 10.0        # a hunt's minutes are rounded; look this much further back for its decisions
MIN_FIGHTS = 3        # fights with a kind before the note names it as costly
NOTE_SOURCE = "outcomes"


@dataclass
class Costs:
    fights: int = 0
    bandages: int = 0
    heal_potions: int = 0
    health_pct_lost: int = 0


@dataclass
class Loop:
    area: str
    kit: str | None
    minutes: float
    kills: int
    deaths: int
    bandages: int | None
    heal_potions: int | None
    gold: int | None
    creatures: dict[str, Costs] = field(default_factory=dict)


def blame(state: dict[str, Any]) -> str | None:
    """The creature kind a moment's costs go to: the one being fought, else the nearest within 3 tiles."""
    fighting = (state.get("you") or {}).get("fighting")
    if fighting and fighting != "nobody":
        return logs.creature_kind(fighting)
    close = [h for h in state.get("hostile_creatures") or [] if logs.is_close(h)]
    if not close:
        return None
    nearest = min(close, key=lambda h: 0 if str(h["distance"]).startswith("adjacent") else 1)
    return logs.creature_kind(nearest["name"])


def creature_costs(decisions: list[dict[str, Any]]) -> dict[str, Costs]:
    out: dict[str, Costs] = {}
    fought: set[int] = set()
    for d in decisions:
        hit = logs.fight_target(d)
        if hit and hit[0] not in fought:
            fought.add(hit[0])
            out.setdefault(logs.creature_kind(hit[1]), Costs()).fights += 1
    for a, b in zip(decisions, decisions[1:]):
        if b.get("t", 0) - a.get("t", 0) > MAX_GAP_S:
            continue
        who = blame(a["state"])
        if who is None:
            continue
        c = out.setdefault(who, Costs())
        ha, hb = logs.health_pct(a["state"]), logs.health_pct(b["state"])
        if ha is not None and hb is not None:
            c.health_pct_lost += max(0, ha - hb)
        (ba, pa), (bb, pb) = logs.supplies(a["state"]), logs.supplies(b["state"])
        if ba is not None and bb is not None:
            c.bandages += max(0, ba - bb)
        if pa is not None and pb is not None:
            c.heal_potions += max(0, pa - pb)
    return out


def majority_kit(decisions: list[dict[str, Any]]) -> str | None:
    kits = Counter(logs.kit_of(d["state"]) for d in decisions if d.get("state"))
    return kits.most_common(1)[0][0] if kits else None


def loops(records: list[dict[str, Any]], area: str | None = None) -> list[Loop]:
    """The loops in a log: its hunts, or, for a log without hunts and with `area` given,
    each tactical run that reached its summary."""
    hunts = [r for r in records if r["type"] == "session" and r.get("event") == "hunted"]
    decisions = [r for r in records if r["type"] == "decision"]
    summaries = [r for r in records if r["type"] == "summary"]
    out = []
    prev = float("-inf")
    for h in hunts:
        end = h.get("t", 0.0)
        minutes = float(h.get("minutes") or 0)
        start = max(prev, end - minutes * 60 - SLACK_S)
        ds = [d for d in decisions if start <= d.get("t", 0) <= end + 2]
        ss = [s for s in summaries if start <= s.get("t", 0) <= end + 2]
        stats = (ss[-1].get("client_stats") or {}) if ss else {}
        out.append(Loop(h.get("area") or "unknown area", h.get("kit") or majority_kit(ds), minutes,
                        int(h.get("kills") or 0), int(h.get("deaths") or 0),
                        stats.get("bandages", h.get("bandages_used")), stats.get("heal_potions", h.get("heal_potions_used")),
                        h.get("gold_gained"), creature_costs(ds)))
        prev = end
    if not hunts and area:
        for run in logs.runs(records):
            if run.summary is None:
                continue
            s = run.stats
            out.append(Loop(area, majority_kit(run.decisions), float(run.summary.get("minutes") or 0),
                            int(s.get("kills") or 0), int(s.get("deaths") or 0), s.get("bandages"),
                            s.get("heal_potions"), None, creature_costs(run.decisions)))
    return out


def rows(loop: Loop) -> list[dict[str, Any]]:
    hours = loop.minutes / 60
    m: dict[str, float] = {"kills_per_loop": loop.kills, "deaths": loop.deaths, "minutes_per_loop": loop.minutes}
    if hours:
        m["kills_per_hour"] = round(loop.kills / hours, 1)
    if loop.gold is not None:
        m["gold_per_loop"] = loop.gold
        if hours:
            m["gold_per_hour"] = round(loop.gold / hours)
    for name, used in (("bandages", loop.bandages), ("heal_potions", loop.heal_potions)):
        if used is None:
            continue
        if loop.kills:
            m[f"{name}_per_kill"] = round(used / loop.kills, 2)
        if hours:
            m[f"{name}_per_hour"] = round(used / hours, 1)
    for kind, c in sorted(loop.creatures.items()):
        k = logs.slug(kind)
        if not k or not (c.fights or c.bandages or c.heal_potions or c.health_pct_lost):
            continue
        m[f"fights_vs_{k}"] = c.fights
        for measure in ("bandages", "heal_potions", "health_pct_lost"):
            if getattr(c, measure):
                m[f"{measure}_vs_{k}"] = getattr(c, measure)
    return [{"area": loop.area, "kit": loop.kit, "metric": k, "value": v} for k, v in m.items()]


def import_logs(world: World, paths: list[Path | str], area: str | None = None) -> dict[str, Any]:
    """Record the loops of each log, replacing what an earlier import (or the live hunt) of
    the same log recorded, then rewrite the note of every area touched."""
    report = []
    touched: set[str] = set()
    for p in paths:
        name = logs.session_name(p)
        found = loops(logs.read_session(p), area)
        if not found:
            why = "no hunts in this log" + ("" if area else "; give an area to count its runs as loops")
            report.append({"log": name, "loops": 0, "note": why})
            continue
        touched.update(r["area"] for r in world.db.execute("SELECT DISTINCT area FROM outcomes WHERE session = ?", (name,)))
        world.db.execute("DELETE FROM outcomes WHERE session = ?", (name,))
        n = 0
        for lp in found:
            for row in rows(lp):
                world.insert("outcomes", {**row, "session": name, "source": "seen"})
                n += 1
            touched.add(lp.area)
        world.db.commit()
        report.append({"log": name, "loops": len(found), "rows": n,
                       "areas": sorted({lp.area for lp in found})})
    areas = []
    for a in sorted(touched):
        world.delete_source("notes", NOTE_SOURCE, area=a)
        summaries = world.outcomes(area=a, exact=True)
        if not summaries:
            continue
        text = note_text(a, summaries)
        world.add_note(text, area=a, tags=["outcome", *{s["kit"] for s in summaries if s["kit"]}], source=NOTE_SOURCE)
        areas.append({"area": a, "note": text, "kits": [{k: v for k, v in s.items() if k not in ("area", "note")}
                                                        for s in summaries]})
    return {"logs": report, "areas": areas}


# ---- the note ---------------------------------------------------------------------------

def plural(kind: str) -> str:
    head, _, last = kind.rpartition(" ")
    if last.endswith("man"):
        last = last[:-3] + "men"
    elif last.endswith(("s", "x", "ch", "sh")):
        last += "es"
    elif last.endswith("y") and last[-2:-1] not in ("a", "e", "i", "o", "u"):
        last = last[:-1] + "ies"
    elif last.endswith("lf"):
        last = last[:-1] + "ves"
    elif last not in ("sheep", "deer"):
        last += "s"
    return f"{head} {last}".strip()


def num(x: float) -> str:
    return f"{x:.0f}" if abs(x) >= 10 or float(x).is_integer() else f"{x:.1f}".rstrip("0").rstrip(".")


def count(n: float, word: str) -> str:
    return f"{num(n)} {word}{'' if n == 1 else 's'}"


def costliest(creatures: list[dict[str, Any]], by: str) -> list[dict[str, Any]]:
    key = f"{by}_per_fight"
    ranked = [c for c in creatures if c.get("fights", 0) >= MIN_FIGHTS and c.get(key, 0) > 0]
    return sorted(ranked, key=lambda c: -c[key])


def kit_words(s: dict[str, Any]) -> str:
    avg = s["averages"]
    kit = f"the {s['kit']} kit" if s["kit"] else "an unknown kit"
    loops = s["loops"]
    parts = [f"{num(avg.get('kills_per_loop', 0))} kills a loop for {kit} over {count(loops, 'loop')}"
             + (f" of about {num(avg['minutes_per_loop'])} min" if avg.get("minutes_per_loop") else "")]
    deaths = s.get("deaths", 0)
    parts.append(f"{count(deaths, 'death')} in {count(loops, 'loop')}" if deaths else "no deaths")
    supplies = [f"{num(avg[m])} {w}" for m, w in (("bandages_per_kill", "bandages"), ("heal_potions_per_kill", "heal potions"))
                if avg.get(m)]
    if supplies:
        parts.append(" and ".join(supplies) + " a kill")
    if avg.get("gold_per_loop"):
        parts.append(f"{num(avg['gold_per_loop'])} gold a loop")
    text = ", ".join(parts)
    by, word, unit = ("bandages", "bandages", "") if any(c.get("bandages") for c in s["creatures"]) else \
        ("health_pct_lost", "health", "%")
    top = costliest(s["creatures"], by)
    if top:
        first = top[0]
        text += (f"; {plural(first['creature'])} cost the most {word}, {num(first[by + '_per_fight'])}{unit} a fight "
                 f"over {count(first['fights'], 'fight')}")
        if len(top) > 1:
            text += f", then {plural(top[1]['creature'])} at {num(top[1][by + '_per_fight'])}{unit} a fight"
    return text


def note_text(area: str, summaries: list[dict[str, Any]]) -> str:
    return f"{area}: " + ". ".join(kit_words(s) for s in summaries) + "."
