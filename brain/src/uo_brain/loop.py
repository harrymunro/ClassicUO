"""The decision loop: poll the client, ask Jev when something changed, act."""

import asyncio
import json
import statistics
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import policy, questions, state
from . import strategy as strategies
from .facts import FactPicker
from .judge import Judge
from .rpc import AgentRpc, RpcError


@dataclass
class LoopConfig:
    poll_s: float = 0.25
    combat_interval_s: float = 1.0
    idle_interval_s: float = 3.0
    duration_s: float | None = None
    price_per_million: float = 0.042  # Jev 1.13 list price per million input tokens; check your provider


@dataclass
class RunStats:
    started: float = field(default_factory=time.monotonic)
    decisions: int = 0
    gated: int = 0
    errors: int = 0
    latencies: list[float] = field(default_factory=list)
    input_tokens: int = 0
    fact_tokens: int = 0  # of input_tokens, what choosing world facts cost (facts.py)
    fact_selections: int = 0
    intents: dict[str, int] = field(default_factory=dict)
    statuses: dict[str, int] = field(default_factory=dict)
    assist_agree: int = 0
    assist_disagree: int = 0
    first_agent_stats: dict[str, int] | None = None
    last_agent_stats: dict[str, int] | None = None

    def summary(self, price: float) -> dict[str, Any]:
        hours = max((time.monotonic() - self.started) / 3600, 1e-9)
        a0, a1 = self.first_agent_stats or {}, self.last_agent_stats or {}
        delta = {k: a1.get(k, 0) - a0.get(k, 0) for k in a1}
        lat = sorted(self.latencies)
        return {
            "minutes": round(hours * 60, 1),
            "decisions": self.decisions,
            "gated_low_confidence": self.gated,
            "errors": self.errors,
            "latency_ms_avg": round(statistics.fmean(lat), 1) if lat else None,
            "latency_ms_p95": round(lat[int(0.95 * (len(lat) - 1))], 1) if lat else None,
            "input_tokens": self.input_tokens,
            "fact_selections": self.fact_selections,
            "fact_input_tokens": self.fact_tokens,
            "est_cost_usd": round(self.input_tokens / 1e6 * price, 4),
            "est_cost_usd_per_hour": round(self.input_tokens / 1e6 * price / hours, 4),
            "intents": self.intents,
            "act_results": self.statuses,
            "client_stats": delta,
            "kills_per_hour": round(delta.get("kills", 0) / hours, 1),
            "assist_agreement": round(self.assist_agree / (self.assist_agree + self.assist_disagree), 3)
            if self.assist_agree + self.assist_disagree else None,
        }


async def run(rpc: AgentRpc, judge: Judge, cfg: LoopConfig, pcfg: policy.PolicyConfig,
              log_path: Path | None, stop: asyncio.Event | None = None, archetype: str | None = None,
              on_snapshot: Callable[[dict[str, Any]], None] | None = None,
              bestiary: dict[int, dict[str, Any]] | None = None, facts: FactPicker | None = None) -> RunStats:
    """archetype: "warrior" or "mage", or None to tell from the character's skills.
    on_snapshot sees every in-game snapshot (the benchmark records a trace with it).
    facts picks the world facts that reach the decisions (facts.py); None for none."""
    stats = RunStats()
    mem = policy.Memory()
    events: deque[str] = deque(maxlen=20)
    since = 0
    last_sig = None
    next_decide = 0.0
    suggested: tuple[int, float] | None = None  # (target, when) of the last attack suggestion
    strategy_text: str | None = None
    active = pcfg
    last_target = 0
    arch = None
    reading = ""
    casts_seen: int | None = None  # the client's attack-spell count, to credit spells to targets
    log = log_path.open("a") if log_path else None
    stop = stop or asyncio.Event()
    if facts is not None and log:
        facts.log = lambda rec: log.write(json.dumps(rec) + "\n")

    try:
        while not stop.is_set():
            now = time.monotonic()
            if cfg.duration_s and now - stats.started > cfg.duration_s:
                break

            try:
                snap = await rpc.call("snapshot", since=since)
            except TimeoutError:
                stats.errors += 1
                continue
            if not snap.get("in_game"):
                await asyncio.sleep(1.0)
                continue

            since = snap["journal_seq"]
            if on_snapshot:
                on_snapshot(snap)
            events.extend(state.journal_events(snap["journal"]))
            agent_stats = snap["agent"]["stats"]
            stats.first_agent_stats = stats.first_agent_stats or dict(agent_stats)
            stats.last_agent_stats = dict(agent_stats)

            # Assist mode: when the player next picks a target, did they pick the suggested one?
            target = next((m["serial"] for m in snap["mobiles"] if m.get("my_target")), 0)
            if suggested and target and target != last_target:
                agree = target == suggested[0]
                stats.assist_agree += agree
                stats.assist_disagree += not agree
                if log:
                    log.write(json.dumps({"type": "assist_feedback", "t": time.time(), "suggested": suggested[0],
                                          "chosen": target, "agree": agree}) + "\n")
                suggested = None
            if suggested and now - suggested[1] > 10:
                suggested = None
            last_target = target

            # The player's strategy changed (or this is the first look): re-read it into settings.
            text = (snap["agent"].get("strategy") or "").strip()
            new_arch = archetype or state.archetype_of(snap)
            if text != strategy_text:
                strategy_text = text
                active, reading = await load_strategy(rpc, judge, text, pcfg, stats, log)
                arch = None  # resend brain_info with the new reading
            if new_arch != arch:
                arch = new_arch
                await rpc.call("brain_info", judge=judge.name, archetype=arch, strategy_reading=reading)

            # Spells the client cast since the last look (queued ones included) went at the engaged creature.
            casts = agent_stats.get("casts", 0) - agent_stats.get("spell_heals", 0)
            if casts_seen is not None and casts > casts_seen and snap["agent"].get("engaged"):
                engaged = snap["agent"]["engaged"]
                mem.casts_at[engaged] = mem.casts_at.get(engaged, 0) + casts - casts_seen
            casts_seen = casts

            mem.update(snap["agent"], now)
            sit = state.build(snap, mem.looted, list(events), mem.skip_items, archetype=arch, casts_at=mem.casts_at,
                              bestiary=bestiary)
            if facts is not None:
                facts.update(snap, sit)
                facts.apply(sit)
                spent = facts.take_tokens()
                stats.input_tokens += spent
                stats.fact_tokens += spent
                stats.fact_selections = facts.selections

            # With the agent off there is nothing to decide: don't spend tokens asking.
            busy = snap["player"]["dead"] or snap["agent"].get("fleeing") or snap["agent"].get("looting") \
                or snap["agent"].get("mode") == "off"
            sig = sit.signature()
            if not busy and (now >= next_decide or sig != last_sig):
                for action, res in await decide_once(rpc, judge, sit, mem, active, stats, log):
                    if action["verb"] == "attack" and res["status"] == "suggested":
                        suggested = (action["target"], time.monotonic())
                last_sig = sig
                interval = cfg.combat_interval_s if sit.hostiles else cfg.idle_interval_s
                next_decide = time.monotonic() + interval

            await asyncio.sleep(cfg.poll_s)
    finally:
        if facts is not None:
            facts.close()
            facts.log = lambda rec: None
        if log:
            log.write(json.dumps({"type": "summary", "t": time.time(), **stats.summary(cfg.price_per_million)}) + "\n")
            log.close()
    return stats


async def load_strategy(rpc: AgentRpc, judge: Judge, text: str, base: policy.PolicyConfig, stats: RunStats,
                        log) -> tuple[policy.PolicyConfig, str]:
    """The strategy as settings, and how it was read in words ("" when there is none)."""
    try:
        knobs, answers = await strategies.compile_strategy(judge, text)
    except Exception as e:
        stats.errors += 1
        await rpc.call("note", text=f"strategy not read: {type(e).__name__}")
        return base, "not read (judge error)"
    reading = knobs.describe() if answers else ""
    if answers:
        stats.input_tokens += answers.input_tokens
        await rpc.call("note", text=f"strategy: {reading}")
    if log:
        log.write(json.dumps({"type": "strategy", "t": time.time(), "text": text, "knobs": knobs.__dict__,
                              "answers": answers.to_log() if answers else None}) + "\n")
    return strategies.apply(base, knobs), reading


async def decide_once(rpc: AgentRpc, judge: Judge, sit: state.Situation, mem: policy.Memory,
                      pcfg: policy.PolicyConfig, stats: RunStats, log) -> list[tuple[dict, dict]]:
    """Ask, decide, act. Returns (action, result) pairs."""
    qs = questions.build(sit)
    try:
        answers = await judge.ask(sit.state, qs)
    except Exception as e:  # network, rate limit, credits: keep the loop alive
        stats.errors += 1
        await rpc.call("note", text=f"judge error: {type(e).__name__}")
        if log:
            log.write(json.dumps({"type": "error", "t": time.time(), "error": str(e)[:500]}) + "\n")
        return []

    dec = policy.decide(sit, answers, mem, pcfg)
    stats.decisions += 1
    stats.gated += dec.gated
    stats.latencies.append(answers.latency_ms)
    stats.input_tokens += answers.input_tokens
    stats.intents[dec.intent] = stats.intents.get(dec.intent, 0) + 1

    results = []
    for action in dec.actions:
        try:
            res = await rpc.call("act", **action)
        except RpcError as e:
            res = {"status": "error", "detail": str(e)}
        results.append(res)
        stats.statuses[res["status"]] = stats.statuses.get(res["status"], 0) + 1
        if action["verb"] == "loot" and res["status"] == "done":
            mem.loot_started[action["target"]] = time.monotonic()

    # What was decided and why, for the in-game panel.
    await rpc.call("decision", **decision_payload(judge, sit, qs, answers, dec, results))

    if log:
        log.write(json.dumps({
            "type": "decision",
            "t": time.time(),
            "judge": judge.name,
            "state": sit.state,
            "questions": qs,
            "answers": answers.to_log(),
            "intent": dec.intent,
            "confidence": round(dec.confidence, 3),
            "masked": dec.masked,
            "gated": dec.gated,
            "actions": dec.actions,
            "results": results,
            "note": dec.note,
            "target": dec.target.serial if dec.target else None,
            "spell": dec.spell.name if dec.spell else None,
        }) + "\n")
        log.flush()
    return list(zip(dec.actions, results))


def decision_payload(judge: Judge, sit: state.Situation, qs: dict[str, Any], answers, dec: policy.Decision,
                     results: list[dict]) -> dict[str, Any]:
    """The decision as the client's agent panel shows it: Jev's probabilities in the
    order the options were asked, what code made of them, and what happened."""
    intent = answers.choices.get("intent")
    order = list(qs["intent"]["criteria"])
    out: dict[str, Any] = {
        "judge": judge.name,
        "archetype": sit.archetype,
        "latency_ms": round(answers.latency_ms, 1),
        "intent": dec.intent,
        "confidence": round(dec.confidence, 3),
        "gated": dec.gated,
        "masked": dec.masked,
        "intents": {k: round(intent.probabilities.get(k, 0.0), 3) for k in order} if intent else {},
        "actions": dec.actions,
        "results": [r.get("status", "?") for r in results],
        "note": dec.note,
    }
    if "in_danger" in answers.nouls:
        out["danger"] = round(answers.nouls["in_danger"], 3)
    if dec.target:
        out["target"] = {"serial": dec.target.serial, "name": dec.target.name}
        if dec.target_confidence is not None:
            out["target"]["confidence"] = round(dec.target_confidence, 3)
    if dec.next_move:
        out["next"] = dec.next_move
    if dec.spell:
        out["spell"] = {"name": dec.spell.name, "why": dec.spell_why}
        if dec.spell_confidence is not None:
            out["spell"]["confidence"] = round(dec.spell_confidence, 3)
    return out
