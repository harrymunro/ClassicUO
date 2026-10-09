"""After-action review: the planner model reads a digest of a decision log and proposes
strategy lines, each with the evidence for it.

A decision log holds every state, question and probability, far more than the model needs.
Code builds a digest instead: totals, each death with the decisions just before it, flees,
looting while monsters were close, low-health moments, supplies used, judge errors, how
sure Jev was of each intent, and the strategy in use. The planner model (Sonnet 5.5
through OpenRouter, llm.py) reads it and calls propose() with up to five lines, each with
its evidence and how sure it is.

Nothing changes until the player accepts. The proposals are saved to <log>.review.json and
printed numbered; `uo-brain review LOG --accept 2` adds line 2 to the character's strategy
through the client, from the saved file, without asking the model again. That is the only
step that connects to the game. A line already in the strategy isn't added twice.

The game logs no message when the character dies, so deaths are placed by inference. A
run's summary counts its deaths; each is put at a decision taken near death (25% health
or less) after which the run ended, or the next decision came 10 s or more later with
health back above 60%. The latest such decisions are taken, one per death; when none fits,
the run's last decision stands in.
"""

import json
import re
from collections import Counter
from datetime import datetime, timezone
from itertools import zip_longest
from pathlib import Path
from typing import Any

from . import costs, llm, logs
from . import strategy as strategies

MAX_LINES = 5
BEFORE_DEATH = 5      # decisions shown before each death
LIST_MAX = 8          # most flees, loots and low-health moments listed (all are counted)
LOW_HEALTH = 30       # percent
NEAR_DEATH = 25
SKILL_GAIN = re.compile(r"^Your skill in .* has (?:in|de)creased|^Your (?:strength|dexterity|intelligence) has changed")

PROPOSE = {"type": "function", "function": {
    "name": "propose",
    "description": "Propose new lines for the player's strategy, each with the evidence for it from the digest.",
    "parameters": {"type": "object", "additionalProperties": False, "required": ["lines"], "properties": {
        "lines": {"type": "array", "maxItems": MAX_LINES, "items": {
            "type": "object", "additionalProperties": False, "required": ["line", "evidence", "confidence"],
            "properties": {
                "line": {"type": "string", "description": "One short instruction in the player's voice, e.g. "
                                                          "\"Don't loot while a monster is within 3 tiles.\""},
                "evidence": {"type": "string", "description": "What in the digest supports it, with counts and "
                                                              "clock times, e.g. \"died twice while looting with a "
                                                              "monster within 3 tiles (13:02, 13:20)\"."},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1,
                               "description": "How sure you are that the line would have helped."}}}}}}}}

REVIEW_PROMPT = """You review a recorded session of an AI agent playing a character in the game Ultima \
Online, to help the player improve the character's strategy.

The strategy is plain text the player writes, one instruction per line. A small model (Jev) reads it at \
every decision in a fight, where it chooses what to do next (fight, flee from the creatures close by, leave the \
area altogether, loot a corpse, seek a creature, rest), which creature to attack and, for a mage, which spell. \
The text also sets a few switches: whether fleeing is allowed at all, how aggressive to be, which target first, \
how much to loot, and the spells to open with and keep casting. Jev sees health, distances and supplies in words \
(adjacent; close, 2-3 tiles; nearby; far away; unhurt, wounded, badly wounded, near death), creature names, \
health and strength, corpses and items. Bandages and potions are applied automatically below set health levels; \
that is not part of the strategy.

You get a digest of the log that code built: totals, each death with the decisions just before it (deaths are \
inferred from the health trail, as the game logs no death message), flees, looting while monsters were close, \
low-health moments, supplies used, judge errors, how sure Jev was of each intent, and the current strategy. \
Clock times are local.

Propose at most five new strategy lines that would have prevented what went wrong, or made what went right \
more reliable. Rules:
- Each line is one short instruction in the player's voice that Jev can follow from what it sees, e.g. \
"Don't loot while a monster is within 3 tiles."
- Base each line on the digest and give the evidence with counts and clock times, e.g. "died twice while \
looting with a monster within 3 tiles (13:02, 13:20)". Don't invent numbers.
- Don't repeat what the strategy already says. If a line changes something the strategy says, say so in the \
evidence.
- confidence is how sure you are that the line would have helped, from 0 to 1. One incident is weak evidence.
- Nothing about healing thresholds, the interface, or things the agent cannot see. If nothing in the digest is \
worth changing, propose no lines.
- Record your proposals by calling the propose tool once."""


# ---- the digest -------------------------------------------------------------------------

def hp(d: dict[str, Any]) -> int:
    pct = logs.health_pct(d.get("state") or {})
    return 100 if pct is None else pct


def close_hostiles(d: dict[str, Any]) -> list[dict[str, Any]]:
    return [h for h in (d.get("state") or {}).get("hostile_creatures") or [] if logs.is_close(h)]


def moment(d: dict[str, Any]) -> str:
    """One decision in a line: what the character had, what was around, what Jev chose and what came of it."""
    st = d.get("state") or {}
    you = st.get("you") or {}
    have = []
    if "bandages_left" in you:
        have.append(f"{you['bandages_left']} bandages")
    have.append(f"{you.get('heal_potions_left', '?')} heal potions ({you.get('heal_potion_ready', '?')})")
    if "mana" in you:
        have.append(f"mana {you['mana']}")
    around = [f"{h.get('name')} {str(h.get('distance', '')).split(',')[0]} {h.get('health', '')}".strip()
              + (" [target]" if h.get("your_current_target") else "") for h in st.get("hostile_creatures") or []]
    corpses = len(st.get("corpses_not_yet_looted") or [])
    acts = [f"{a.get('verb', '?')}{' ' + r.get('status', '?') if r else ''}"
            for a, r in zip_longest(d.get("actions") or [], d.get("results") or []) if a]
    line = (f"{logs.clock(d.get('t'))} health {you.get('health', '?')}; {', '.join(have)}; "
            f"hostiles: {'; '.join(around) or 'none'}")
    if corpses:
        line += f"; {corpses} unlooted corpse{'s' if corpses > 1 else ''}"
    return line + f" -> {d.get('intent')} {d.get('confidence')} ({d.get('note', '')}); actions: {', '.join(acts) or 'none'}"


def events(d: dict[str, Any], n: int = 6) -> list[str]:
    """The decision's recent journal lines, without skill and stat gains."""
    return [e for e in (d.get("state") or {}).get("recent_events") or [] if not SKILL_GAIN.match(e)][-n:]


def death_points(run: logs.Run) -> list[int]:
    deaths = int(run.stats.get("deaths") or 0)
    ds = run.decisions
    if not deaths or not ds:
        return []
    found = []
    for i, d in enumerate(ds):
        if hp(d) > NEAR_DEATH:
            continue
        if i == len(ds) - 1:
            found.append(i)
        elif ds[i + 1].get("t", 0) - d.get("t", 0) >= 10 and hp(ds[i + 1]) >= 60:
            found.append(i)
    return found[-deaths:] or [len(ds) - 1]


def stretches(ds: list[dict[str, Any]], inside) -> list[list[dict[str, Any]]]:
    """Runs of consecutive decisions for which inside(d) holds."""
    out, cur = [], []
    for d in ds:
        if inside(d):
            cur.append(d)
        elif cur:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def after(t: float, deaths: list[float], within: float) -> float | None:
    """Seconds from t to the next death within `within` seconds, if there is one."""
    later = [x - t for x in deaths if 0 <= x - t <= within]
    return round(min(later)) if later else None


def strategy_in_use(records: list[dict[str, Any]]) -> dict[str, Any]:
    recs = [r for r in records if r["type"] == "strategy"]
    if not recs:
        return {"text": "(not in the log)"}
    last = recs[-1]
    out: dict[str, Any] = {"text": (last.get("text") or "").strip() or "(none)"}
    try:
        out["read_as"] = strategies.Knobs(**(last.get("knobs") or {})).describe()
    except TypeError:
        pass
    if len({(r.get("text") or "").strip() for r in recs}) > 1:
        out["changed_during_the_log"] = True
    return out


def digest(records: list[dict[str, Any]], name: str = "") -> dict[str, Any]:
    ds = [r for r in records if r["type"] == "decision"]
    runs = logs.runs(records)
    times = [r["t"] for r in records if r.get("t")]
    out: dict[str, Any] = {"log": name}
    if times:
        out["span"] = {"from": logs.clock(min(times)), "to": logs.clock(max(times)),
                       "minutes": round((max(times) - min(times)) / 60, 1)}
    kits = Counter(logs.kit_of(d["state"]) for d in ds if d.get("state"))
    out["kit"] = kits.most_common(1)[0][0] if kits else "unknown"
    out["strategy"] = strategy_in_use(records)

    stats: Counter = Counter()
    for run in runs:
        stats.update({k: v for k, v in run.stats.items() if isinstance(v, (int, float))})
    errors = [r for r in records if r["type"] == "error"]
    out["totals"] = {"decisions": len(ds), "runs": len(runs), "kills": stats["kills"], "deaths": stats["deaths"],
                     "flees": stats["flees"], "bandages_used": stats["bandages"],
                     "heal_potions_used": stats["heal_potions"], "cure_potions_used": stats["cure_potions"],
                     "corpses_looted": stats["corpses_looted"], "items_taken": stats["items_taken"],
                     "gated_low_confidence": sum(1 for d in ds if d.get("gated")), "judge_errors": len(errors)}

    intents: dict[str, list[float]] = {}
    for d in ds:
        intents.setdefault(d.get("intent", "?"), []).append(float(d.get("confidence") or 0))
    out["intents"] = {k: {"count": len(v), "average_confidence": round(sum(v) / len(v), 2),
                          "below_0.5": sum(1 for c in v if c < 0.5)}
                      for k, v in sorted(intents.items(), key=lambda kv: -len(kv[1]))}

    deaths, death_times = [], []
    for run in runs:
        for i in death_points(run):
            d = run.decisions[i]
            death_times.append(d.get("t", 0))
            deaths.append({"time": logs.clock(d.get("t")),
                           "decisions_before": [moment(x) for x in run.decisions[max(0, i - BEFORE_DEATH + 1): i + 1]],
                           "journal": events(d)})
    out["deaths"] = {"count": len(deaths), "first": deaths[:LIST_MAX]}

    flee_runs = stretches(ds, lambda d: d.get("intent") in ("flee", "leave"))
    out["flees"] = {"count": len(flee_runs), "first": [
        {"time": logs.clock(s[0].get("t")), "intent": s[0].get("intent"), "health": hp(s[0]),
         "close": [h.get("name") for h in close_hostiles(s[0])], "decisions": len(s),
         "died_within_s": after(s[-1].get("t", 0), death_times, 60)} for s in flee_runs[:LIST_MAX]]}

    loots = [d for d in ds if close_hostiles(d) and any(a.get("verb") in ("loot", "take") for a in d.get("actions") or [])]
    out["looting_with_monsters_close"] = {"count": len(loots), "first": [
        {"time": logs.clock(d.get("t")), "health": hp(d),
         "actions": [a.get("verb") for a in d.get("actions") or []],
         "close": [f"{h.get('name')} {str(h.get('distance', '')).split(',')[0]}" for h in close_hostiles(d)],
         "died_within_s": after(d.get("t", 0), death_times, 60)} for d in loots[:LIST_MAX]]}

    lows = stretches(ds, lambda d: hp(d) <= LOW_HEALTH)
    out["low_health"] = {"count": len(lows), "first": [
        {"lowest": moment(min(s, key=hp)), "decisions": len(s),
         "then": "died" if after(min(s, key=hp).get("t", 0), death_times, 60) is not None else "recovered"}
        for s in lows[:LIST_MAX]]}

    out["supplies"] = supplies(ds)
    if errors:
        out["judge_errors"] = {"count": len(errors),
                               "examples": list(dict.fromkeys(str(e.get("error", ""))[:200] for e in errors))[:3]}
    session = session_part(records)
    if session:
        out["session"] = session
    return out


def supplies(ds: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, label in (("bandages_left", "bandages"), ("heal_potions_left", "heal_potions"),
                       ("cure_potions_left", "cure_potions")):
        seen = [d["state"]["you"][key] for d in ds if key in ((d.get("state") or {}).get("you") or {})]
        if seen:
            out[label] = {"first": seen[0], "last": seen[-1], "lowest": min(seen)}
    low = next((d for d in ds if str(((d.get("state") or {}).get("you") or {}).get("supplies", "plenty"))
                .startswith(("running low", "nearly gone"))), None)
    if low:
        out["first_ran_low"] = f"{logs.clock(low.get('t'))}: {low['state']['you']['supplies']}"
    return out


def session_part(records: list[dict[str, Any]]) -> dict[str, Any]:
    hunts = [r for r in records if r["type"] == "session" and r.get("event") == "hunted"]
    failed = [r for r in records if r["type"] == "goal_result" and not (r.get("result") or {}).get("ok", True)]
    out: dict[str, Any] = {}
    if hunts:
        out["hunts"] = [{k: h.get(k) for k in ("area", "minutes", "kills", "deaths", "bandages_used", "stopped_because")}
                        | {"ended": logs.clock(h.get("t"))} for h in hunts[-LIST_MAX:]]
    if failed:
        out["failed_goals"] = [f"{logs.clock(r.get('t'))} {r.get('tool')}: {str(r['result'].get('result', ''))[:150]}"
                               for r in failed[-5:]]
    return out


# ---- asking the model -------------------------------------------------------------------

def lines_from(result: llm.ChatResult) -> list[dict[str, Any]] | None:
    """The proposed lines, from the tool call or from JSON in the reply; None when there are neither."""
    found: list[Any] | None = None
    for call in result.tool_calls:
        if call.name == "propose":
            found = (found or []) + list(call.arguments.get("lines") or [])
    if found is None and result.content and "{" in result.content:
        try:
            data = json.loads(result.content[result.content.index("{"): result.content.rindex("}") + 1])
            found = data.get("lines") if isinstance(data, dict) else None
        except ValueError:
            pass
    if found is None:
        return None
    out = []
    for item in found:
        if not isinstance(item, dict) or not str(item.get("line", "")).strip():
            continue
        try:
            conf: float | None = round(min(1.0, max(0.0, float(item["confidence"]))), 2)
        except (KeyError, TypeError, ValueError):
            conf = None  # not given: shown as such rather than guessed
        out.append({"line": " ".join(str(item["line"]).split()), "evidence": " ".join(str(item.get("evidence", "")).split()),
                    "confidence": conf})
    return out[:MAX_LINES]


async def propose(dig: dict[str, Any], chat_fn: llm.ChatFn = llm.chat,
                  model: str | None = None) -> tuple[list[dict[str, Any]], dict[str, Any], llm.LlmUsage]:
    """(lines, the reply as the model wrote it, usage). Asks once more if the reply has no proposals."""
    msgs = [{"role": "system", "content": REVIEW_PROMPT},
            {"role": "user", "content": "The digest:\n" + json.dumps(dig, indent=1)}]
    usage = llm.LlmUsage(calls=0)
    for _ in range(2):
        with costs.kind("review"):
            res = await chat_fn(msgs, tools=[PROPOSE], tool_choice="auto", model=model, max_tokens=2000)
        usage = usage + res.usage
        lines = lines_from(res)
        if lines is not None:
            reply = {"content": (res.content or "").strip()[:1000] or None,
                     "tool_calls": [c.raw_arguments[:6000] for c in res.tool_calls]}
            return lines, reply, usage
        msgs = msgs + [res.message, {"role": "user", "content": "Record your proposals by calling the propose tool once."}]
    raise ValueError("the planner model proposed nothing (no propose call); nothing was saved")


def review_path(log: Path) -> Path:
    return Path(log).with_suffix(".review.json")


async def review(log: Path, chat_fn: llm.ChatFn = llm.chat, model: str | None = None) -> dict[str, Any]:
    """Digest the log, ask the model, save and return the review."""
    log = Path(log)
    records = logs.read_session(log)
    if not any(r["type"] == "decision" for r in records):
        raise ValueError(f"no decisions in {log}")
    dig = digest(records, logs.session_name(log))
    lines, reply, usage = await propose(dig, chat_fn, model)
    have = {line.strip().lower() for line in dig["strategy"]["text"].splitlines()}
    lines = [p for p in lines if p["line"].lower() not in have]
    rv = {"log": str(log), "reviewed": datetime.now(timezone.utc).isoformat(timespec="seconds"),
          "model": usage.model, "usage": usage.to_log(), "strategy": dig["strategy"]["text"],
          "proposals": [{"n": i + 1, **p, "accepted": None} for i, p in enumerate(lines)],
          "reply": reply, "digest": dig}
    save(rv, review_path(log))
    return rv


def save(rv: dict[str, Any], path: Path) -> None:
    path.write_text(json.dumps(rv, indent=2) + "\n")


def load(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"no saved review at {path}: run uo-brain review on the log first")
    return json.loads(path.read_text())


# ---- accepting --------------------------------------------------------------------------

def choose(rv: dict[str, Any], numbers: list[int], strategy: str) -> tuple[list[dict[str, Any]], list[str]]:
    """The proposals to add, and a word on each one that won't be. Raises on a number that
    isn't in the review."""
    by_n = {p["n"]: p for p in rv.get("proposals", [])}
    unknown = [n for n in numbers if n not in by_n]
    if unknown:
        raise ValueError(f"no proposal {', '.join(map(str, unknown))} in this review (it has 1 to {len(by_n)})")
    have = {line.strip().lower() for line in strategy.splitlines()}
    add, skipped = [], []
    for n in dict.fromkeys(numbers):
        p = by_n[n]
        if p["line"].strip().lower() in have:
            skipped.append(f"{n}. already in the strategy: {p['line']}")
            p["accepted"] = p.get("accepted") or "already there"
        else:
            add.append(p)
            have.add(p["line"].strip().lower())
    return add, skipped


async def accept(rpc: Any, log: Path, numbers: list[int]) -> tuple[list[str], str]:
    """Add the chosen saved proposals to the character's strategy. Returns what was done, and the strategy now."""
    path = review_path(log)
    rv = load(path)
    strategy = (await rpc.call("strategy")).get("strategy") or ""
    add, said = choose(rv, numbers, strategy)
    for p in add:
        strategy = (await rpc.call("strategy", add=p["line"])).get("strategy") or strategy
        p["accepted"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        said.append(f"{p['n']}. added: {p['line']}")
    save(rv, path)
    return said, strategy


def show(rv: dict[str, Any], saved: bool = False) -> str:
    cost = rv.get("usage", {}).get("cost")
    head = (f"{'Saved review' if saved else 'Review'} of {rv['log']} by {rv.get('model') or 'the planner model'}"
            + (f" (${cost:.4f})" if isinstance(cost, (int, float)) else "") + f", in {review_path(Path(rv['log']))}")
    out = [head]
    for p in rv.get("proposals", []):
        mark = f"  [accepted {p['accepted']}]" if p.get("accepted") else ""
        conf = "not given" if p.get("confidence") is None else f"{p['confidence']:.2f}"
        out.append(f"{p['n']:>2}. {p['line']}  (confidence {conf}){mark}")
        out.append(f"    evidence: {p['evidence']}")
    said = (rv.get("reply") or {}).get("content")
    if not rv.get("proposals"):
        out.append("No lines proposed." + (f" The model said: {said}" if said else ""))
    else:
        out.append(f"Add one to the character's strategy with: uo-brain review {rv['log']} --accept N [N...]")
    if saved:
        out.append("(--again asks the model again)")
    return "\n".join(out)
