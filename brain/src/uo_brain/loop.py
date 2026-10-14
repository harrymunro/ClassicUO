"""The decision loop: poll the client, ask Jev when something changed, act.

Jev isn't asked when its answer can't matter or can't be new (cuo-df8): with no creature, corpse or
item in view and nothing asked but the intent and the danger, the policy can only rest; and the
same state and questions as the last call get that call's answers again. Either way the policy
still decides, so its own timers and rules (leaving, songs, meditation, plans' time limits) run as
before. While all is quiet the clock stretches too: each decision that did nothing doubles the
wait, up to `quiet_max_s`, and the quiet ending (something close or coming, a fight, health
falling) decides at once."""

import asyncio
import json
import os
import statistics
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from . import calls, costs, policy, questions, state
from .machine import Runner
from . import strategy as strategies
from .facts import FactPicker
from .judge import Answers, ChoiceResult, Judge
from .rpc import AgentRpc, RpcError


@dataclass
class LoopConfig:
    poll_s: float = 0.25
    combat_interval_s: float = 1.0
    idle_interval_s: float = 3.0
    duration_s: float | None = None
    slow_factor: float = 4.0  # over a "slow" budget: decide this many times less often, and only on the clock
    quiet_max_s: float = 8.0  # while quiet, the clock stretches up to this
    reuse_s: float = 60.0     # the same state and questions within this: the last call's answers, not a new call
    # Ask Jev at every tick and never stretch the clock, as before cuo-df8 (UO_BRAIN_ASK_EVERY_TICK=1).
    ask_every_tick: bool = field(default_factory=lambda: os.environ.get("UO_BRAIN_ASK_EVERY_TICK", "") not in ("", "0"))


SAME_STATE = "same state as the last call"
NOTHING_TO_CHOOSE = "nothing to choose"


def quiet(sit: state.Situation) -> bool:
    """Nothing to watch closely: not fighting, no pet fighting, health not falling, nothing coming
    at the character and nothing hostile within state.WATCH_TILES ("nearby" in words)."""
    return not sit.agent.get("engaged") and not sit.agent.get("pet_target") and not sit.pack and not sit.hp_drop \
        and not any(h.distance <= state.WATCH_TILES for h in sit.hostiles)


def nothing_to_choose(sit: state.Situation, qs: dict[str, Any]) -> bool:
    """No creature, corpse or item in view, and nothing asked but the intent and the danger: whatever
    Jev answered, the policy could only rest (policy.masked_intent rules out every other intent)."""
    return not sit.hostiles and not sit.corpses and not sit.items and set(qs) <= {"intent", "in_danger"}


def resting(qs: dict[str, Any]) -> Answers:
    """What stands in for Jev's answers when there is nothing to choose."""
    options = {k: 1.0 if k == "rest" else 0.0 for k in qs["intent"]["criteria"]}
    return Answers(choices={"intent": ChoiceResult("rest", options, 1.0)}, nouls={"in_danger": 0.0}, model="none")


@dataclass
class Recall:
    """The last call's state and questions with its answers, to answer the same again without a call
    for `within` seconds."""
    within: float = 60.0
    key: str = ""
    answers: Answers | None = None
    at: float = 0.0

    @staticmethod
    def key_of(state_words: dict[str, Any], qs: dict[str, Any]) -> str:
        return json.dumps([state_words, qs], sort_keys=True, default=str)

    def get(self, key: str, now: float) -> Answers | None:
        return self.answers if self.answers is not None and key == self.key and now - self.at <= self.within else None

    def keep(self, key: str, answers: Answers, now: float) -> None:
        self.key, self.answers, self.at = key, answers, now

    def forget(self) -> None:
        self.answers = None


@dataclass
class RunStats:
    started: float = field(default_factory=time.monotonic)
    decisions: int = 0  # put to Jev
    reused: int = 0     # decided again on the last call's answers: the same state and questions
    unasked: int = 0    # decided without Jev: nothing to choose
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
    cost_mark: costs.Mark = field(default_factory=costs.ledger.mark)  # the ledger when the run began
    slowed: int = 0  # decisions put off because the session was over its budget

    @property
    def made(self) -> int:
        """Every decision, asked or not."""
        return self.decisions + self.reused + self.unasked

    def summary(self) -> dict[str, Any]:
        hours = max((time.monotonic() - self.started) / 3600, 1e-9)
        a0, a1 = self.first_agent_stats or {}, self.last_agent_stats or {}
        delta = {k: a1.get(k, 0) - a0.get(k, 0) for k in a1}
        lat = sorted(self.latencies)
        spent = costs.ledger.summary(self.cost_mark, kills=delta.get("kills"), decisions=self.decisions, hours=hours)
        return {
            "minutes": round(hours * 60, 1),
            "decisions": self.decisions,
            "not_asked": {"same_state": self.reused, "nothing_to_choose": self.unasked},
            "decisions_per_hour": round(self.decisions / hours, 1),
            "gated_low_confidence": self.gated,
            "errors": self.errors,
            "latency_ms_avg": round(statistics.fmean(lat), 1) if lat else None,
            "latency_ms_p95": round(lat[int(0.95 * (len(lat) - 1))], 1) if lat else None,
            "input_tokens": self.input_tokens,
            "fact_selections": self.fact_selections,
            "fact_input_tokens": self.fact_tokens,
            "cost_usd": spent["cost_usd"],
            "cost_usd_per_hour": spent["cost_per_hour_usd"],
            "costs": spent,
            **({"slowed_for_budget": self.slowed} if self.slowed else {}),
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
              bestiary: dict[int, dict[str, Any]] | None = None, facts: FactPicker | None = None,
              on_decision: Callable[[state.Situation, policy.Decision], None] | None = None,
              machine: Runner | None = None) -> RunStats:
    """archetype: "warrior", "mage", "archer" or "tamer", or None to tell from the character's skills and weapon.
    on_snapshot sees every in-game snapshot (the benchmark records a trace with it); on_decision
    every decision with its situation (a hunt ends after leaving from a pack). machine: a plan's
    state machine to follow (machine.py), carried over from one loop to the next by the caller.
    facts picks the world facts that reach the decisions (facts.py); None for none."""
    stats = RunStats()
    mem = policy.Memory()
    calls.sink = lambda rec: rpc.call("ai_call", **rec)
    events: deque[str] = deque(maxlen=20)
    since = 0
    last_sig = None
    next_decide = 0.0
    suggested: tuple[int, float] | None = None  # (target, when) of the last attack suggestion
    strategy_text: str | None = None
    active = pcfg
    last_live: policy.PolicyConfig | None = None
    last_target = 0
    arch = None
    reading = ""
    casts_seen: int | None = None  # the client's attack-spell count, to credit spells to targets
    log = log_path.open("a") if log_path else None
    stop = stop or asyncio.Event()
    if facts is not None and log:
        facts.log = lambda rec: log.write(json.dumps(rec) + "\n")
    cost_log = (lambda rec: log.write(json.dumps(rec) + "\n")) if log else None
    was_over = False
    recall = None if cfg.ask_every_tick else Recall(cfg.reuse_s)
    calm = 0  # quiet decisions in a row that did nothing, on the clock: each doubles the wait

    try:
        with costs.ledger.logging_to(cost_log):
            while not stop.is_set():
                now = time.monotonic()
                if cfg.duration_s and now - stats.started > cfg.duration_s:
                    break

                try:
                    # Places to stand (cuo-fhf) only when they are used: the client works them out
                    # on the game thread, and only with a monster in view.
                    snap = await rpc.call("snapshot", since=since, **({"spots": True} if active.spots != "off" else {}))
                except TimeoutError:
                    stats.errors += 1
                    continue
                if not snap.get("in_game"):
                    await asyncio.sleep(1.0)
                    continue

                since = snap["journal_seq"]
                costs.ledger.character = snap["player"].get("name") or costs.ledger.character
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
                mem.track(snap.get("mobiles") or [], now)
                sit = state.build(snap, mem.looted, list(events), mem.skip_items, archetype=arch, casts_at=mem.casts_at,
                                  bestiary=bestiary, cooling=mem.cooling(now), spot_order=active.spot_order,
                                  no_path=mem.no_path, companions=active.companions,
                                  movement=mem.movement(now) if active.leave_stop == "jev" and mem.leaving_until > now
                                  else None)
                sit.note_health_drop(mem.health_drop(sit.hp_pct, now))
                if active.link is not None:
                    # A party: what is on each companion and what they called, into the situation (mates.py).
                    try:
                        active.link.before(sit)
                    except Exception as e:  # noqa: BLE001 - the store busy for a moment: fight on without it
                        if log:
                            log.write(json.dumps({"type": "error", "t": time.time(), "error": f"party: {e}"[:300]}) + "\n")
                if facts is not None:
                    facts.update(snap, sit)
                    facts.apply(sit)
                    spent = facts.take_tokens()
                    stats.input_tokens += spent
                    stats.fact_tokens += spent
                    stats.fact_selections = facts.selections

                # Over the session's budget (costs.Budget): decide less often, or stop.
                over = costs.ledger.over_budget()
                budget = costs.ledger.budget
                if over != was_over and budget:
                    was_over = over
                    await rpc.call("note", text=budget_note(over, budget))
                if over and budget and budget.action == "stop":
                    break
                slow = over and budget is not None and budget.action == "slow"

                # With the agent off there is nothing to decide: don't spend tokens asking.
                busy = snap["player"]["dead"] or snap["agent"].get("fleeing") or snap["agent"].get("looting") \
                    or snap["agent"].get("mode") == "off"
                sig = sit.signature(defend_only=active.defend_only and not cfg.ask_every_tick)
                if calm and (busy or not quiet(sit)):
                    # The quiet is over, or the agent is busy (running, looting, switched off): decide
                    # as soon as it can, not when the stretched clock runs out.
                    calm, next_decide = 0, 0.0
                # What changes while the loop runs (a companion's settings at its leader's word, standing.py) is
                # laid over the configuration, and a change is decided on at once: told to loot in the quiet
                # after a fight, a companion shouldn't stand until the stretched clock runs out.
                live = active.live(active) if active.live is not None else active
                if live is not active and live != last_live:
                    calm, next_decide = 0, 0.0
                last_live = live
                if not busy and (now >= next_decide or (sig != last_sig and not slow)):
                    done = await decide_once(rpc, judge, sit, mem, live, stats, log, on_decision, machine, recall)
                    for action, res in done:
                        if action["verb"] == "attack" and res["status"] == "suggested":
                            suggested = (action["target"], time.monotonic())
                    # On the clock with nothing changed, nothing done and all quiet: wait twice as long
                    # each time, up to quiet_max_s.
                    calm = min(calm + 1, 8) if sig == last_sig and not done and quiet(sit) and not cfg.ask_every_tick \
                        else 0
                    last_sig = sig
                    interval = cfg.combat_interval_s if sit.hostiles else cfg.idle_interval_s
                    if calm:
                        interval = max(interval, min(interval * 2 ** calm, cfg.quiet_max_s))
                    stats.slowed += slow
                    next_decide = time.monotonic() + interval * (cfg.slow_factor if slow else 1.0)

                await asyncio.sleep(cfg.poll_s)
    finally:
        if facts is not None:
            facts.close()
            facts.log = lambda rec: None
        if log:
            log.write(json.dumps({"type": "summary", "t": time.time(), **stats.summary(),
                                  **({"machine": machine.summary()} if machine else {})}) + "\n")
            log.close()
    return stats


def budget_note(over: bool, budget: costs.Budget) -> str:
    """What the panel says when the session goes over its budget, or back under it."""
    rate = costs.ledger.rate()
    if not over:
        return f"back under budget (${rate:.3f}/h, cap ${budget.per_hour:.3f}/h)"
    then = {"slow": "deciding less often", "cheaper": "on the cheaper models", "stop": "stopping"}[budget.action]
    return f"over budget: ${rate:.3f}/h against a cap of ${budget.per_hour:.3f}/h; {then}"


# Strategy text -> how it was last read, in words: the autopilot tells the panel before any fight.
READINGS: dict[str, str] = {}
# (judge, strategy text) -> its settings, so the fight loops a session starts for each goal (and
# while the planner thinks) don't ask Jev to read the same text again.
COMPILED: dict[tuple[str, str], strategies.Knobs] = {}


async def load_strategy(rpc: AgentRpc, judge: Judge, text: str, base: policy.PolicyConfig, stats: RunStats,
                        log) -> tuple[policy.PolicyConfig, str]:
    """The strategy as settings, and how it was read in words ("" when there is none)."""
    if (judge.name, text) in COMPILED:
        knobs = COMPILED[(judge.name, text)]
        if log:
            log.write(json.dumps({"type": "strategy", "t": time.time(), "text": text, "knobs": knobs.__dict__,
                                  "answers": None, "cached": True}) + "\n")
        return strategies.apply(base, knobs), READINGS.get(text, "")
    try:
        knobs, answers = await strategies.compile_strategy(judge, text)
    except Exception as e:
        stats.errors += 1
        await rpc.call("note", text=f"strategy not read: {type(e).__name__}")
        return base, "not read (judge error)"
    reading = knobs.describe() if answers else ""
    READINGS[text] = reading
    COMPILED[(judge.name, text)] = knobs
    if answers:
        calls.emit({"kind": "strategy", "title": "reading the strategy", "model": answers.model,
                    "latency_ms": round(answers.latency_ms, 1), "cost": round(answers.cost, 7),
                    "questions": calls.view(strategies.QUESTIONS, answers), "note": reading})
    if answers:
        stats.input_tokens += answers.input_tokens
        await rpc.call("note", text=f"strategy: {reading}")
    if log:
        log.write(json.dumps({"type": "strategy", "t": time.time(), "text": text, "knobs": knobs.__dict__,
                              "answers": answers.to_log() if answers else None}) + "\n")
    return strategies.apply(base, knobs), reading


async def decide_once(rpc: AgentRpc, judge: Judge, sit: state.Situation, mem: policy.Memory,
                      pcfg: policy.PolicyConfig, stats: RunStats, log,
                      on_decision: Callable[[state.Situation, policy.Decision], None] | None = None,
                      machine: Runner | None = None, recall: Recall | None = None) -> list[tuple[dict, dict]]:
    """Ask, decide, act. Returns (action, result) pairs. With a machine, its current state's
    transitions go in the same request, and its state limits what the policy may choose. With a
    recall, Jev isn't asked when there is nothing to choose or the state and questions are the
    same as the last call's (module docstring)."""
    if machine:
        sit.plan = machine.plan_words()
    now = time.monotonic()
    active = machine.config(pcfg) if machine else pcfg
    qs = questions.build(sit, ward=pcfg.ward, stand=policy.stand_wanted(sit, mem, active, now),
                         stop=policy.stop_wanted(sit, mem, active, now))
    if machine:
        qs.update(machine.questions(sit, questions.character(sit), questions.role(sit)))
    not_asked = ""
    key = Recall.key_of(sit.state, qs) if recall else ""
    if recall and nothing_to_choose(sit, qs):
        answers, not_asked = resting(qs), NOTHING_TO_CHOOSE
    elif recall and (again := recall.get(key, now)) is not None:
        answers, not_asked = replace(again, input_tokens=0, output_tokens=0, latency_ms=0.0, cost=0.0), SAME_STATE
    else:
        try:
            with costs.kind("fight"):
                answers = await judge.ask(sit.state, qs)
        except Exception as e:  # network, rate limit, credits: keep the loop alive
            stats.errors += 1
            await rpc.call("note", text=f"judge error: {type(e).__name__}")
            if log:
                log.write(json.dumps({"type": "error", "t": time.time(), "error": str(e)[:500]}) + "\n")
            return []
        if recall:
            recall.keep(key, answers, now)

    moved = machine.advance(answers.nouls) if machine else None
    dec = policy.decide(sit, answers, mem, machine.config(pcfg) if machine else pcfg)
    called = None
    if recall and dec.gated:
        recall.forget()  # unsure: ask again next time, as before
    if on_decision:
        on_decision(sit, dec)
    if not_asked == SAME_STATE:
        stats.reused += 1
    elif not_asked:
        stats.unasked += 1
    else:
        stats.decisions += 1
        stats.latencies.append(answers.latency_ms)
        stats.input_tokens += answers.input_tokens
    stats.gated += dec.gated
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
        if action["verb"] == "flee":
            mem.flee_failed = 0 if res["status"] == "done" else mem.flee_failed + 1
        if action["verb"] == "walk_to" and action.get("reason") == "seek" and action.get("target"):
            mem.seek_result(action["target"], res["status"], str(res.get("detail", "")))
        if action["verb"] == "walk_to" and action.get("leg") and res["status"] != "done":
            mem.leg_failed_at = time.monotonic()  # the next walks to it go the whole way (policy.seek_leg)

    if pcfg.link is not None:
        # A party member's callout for what this decision showed (mates.py): said after its actions went.
        try:
            called = await pcfg.link.after(rpc, sit, dec, mem)
        except Exception as e:  # noqa: BLE001 - never worth a decision
            if log:
                log.write(json.dumps({"type": "error", "t": time.time(), "error": f"callout: {e}"[:300]}) + "\n")

    # What was decided and why, for the in-game panel.
    payload = decision_payload(judge, sit, qs, answers, dec, results)
    # Every question of the call, with Jev's options and pick, and what code used, for the live view.
    cfg = machine.config(pcfg) if machine else pcfg
    cuts = {"in_danger": cfg.flee_danger, "leave_now": policy.leave_cut(cfg), "pull_back": cfg.pull_back,
            "stop_running": policy.STOP_AT,
            **{k: cfg.take_item for k in qs if k.startswith("take_")}}
    if machine:
        cuts.update({f"go_{t.to}": t.at for t in machine.machine.states[machine.state].transitions})
    used = {"intent": dec.intent}
    if dec.target is not None:
        used["target"] = dec.target.id
    if dec.spell is not None:
        used["spell"] = dec.spell.id
    if dec.spot is not None:
        used["stand"] = dec.spot.id
    payload["questions"] = calls.view(qs, answers, cuts=cuts, used=used) if not_asked != NOTHING_TO_CHOOSE else []
    payload["did"] = calls.did(sit, dec.actions, results)
    if machine:
        payload["machine"] = machine.panel()
    if not_asked:
        # The client counts it as a decision but not as a call.
        payload["not_asked"] = not_asked
        if not_asked == NOTHING_TO_CHOOSE:
            payload["intents"] = {}
            payload.pop("danger", None)
    await rpc.call("decision", **payload)

    if log:
        log.write(json.dumps({
            "type": "decision",
            "t": time.time(),
            "judge": judge.name,
            "state": sit.state,
            # Not asked: the questions are the last call's, or none were put; replays skip it.
            **({"not_asked": not_asked} if not_asked else {"questions": qs}),
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
            **({"leave_why": dec.leave_why} if dec.leave_why else {}), **({"regroup": True} if dec.regroup else {}),
            **({"spot": spot_log(dec, answers)} if dec.spot is not None else {}),
            **({"cornered": True} if dec.cornered else {}),
            **({"called": called} if called else {}),
            **({"spots": len(sit.spots), "spots_ms": sit.raw.get("spots_ms")} if sit.spots else {}),
            **({"machine": {"state": machine.state, "moved": moved}} if machine else {}),
        }) + "\n")
        log.flush()
    return list(zip(dec.actions, results))


def spot_log(dec: policy.Decision, answers: Answers) -> dict[str, Any]:
    """Where a run or step back went (cuo-fhf), for the decision log: the place, who picked it, Jev's
    probability for it, and Jev's own pick when that was a place not allowed (staying put, say)."""
    stand = answers.choices.get("stand")
    out = {"id": dec.spot.id, "by": dec.spot_why, "x": dec.spot.info["x"], "y": dec.spot.info["y"],
           "steps": dec.spot.distance, "words": dec.spot.info["words"]}
    if dec.spot_confidence is not None:
        out["p"] = round(dec.spot_confidence, 3)
    if stand is not None and stand.choice != dec.spot.id:
        out["jev_pick"] = stand.choice
    return out


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
        "cost": round(answers.cost, 7),
        "spent": costs.ledger.live(),
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
    if dec.spot is not None:
        out["spot"] = {"words": dec.spot.info["words"], "why": dec.spot_why}
        if dec.spot_confidence is not None:
            out["spot"]["confidence"] = round(dec.spot_confidence, 3)
    return out
