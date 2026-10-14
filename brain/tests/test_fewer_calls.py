"""The fight loop asks Jev only when its answer can matter and can be new (cuo-df8)."""

import asyncio
import copy
import json
import time

from uo_brain import loop, policy, questions, state
from uo_brain.judge import Answers, ChoiceResult

from conftest import SNAPSHOT, mage_snapshot


class Rpc:
    """Serves snapshot_at(seconds since the start); records every call with its time."""

    def __init__(self, snapshot_at):
        self.snapshot_at = snapshot_at
        self.t0 = time.monotonic()
        self.calls = []

    async def call(self, method, **params):
        t = time.monotonic() - self.t0
        self.calls.append((t, method, params))
        if method == "snapshot":
            return self.snapshot_at(t)
        if method == "act":
            return {"status": "done"}
        return {}

    def decisions(self):
        return [(t, p) for t, m, p in self.calls if m == "decision"]


class Judge:
    """Answers the intent with `intent` at `confidence`, every target question with "none"."""

    name = "fake-jev"

    def __init__(self, intent="rest", confidence=0.9):
        self.intent, self.confidence = intent, confidence
        self.asked = []

    async def ask(self, state_words, qs):
        self.asked.append(copy.deepcopy(state_words))
        a = Answers(model="fake", input_tokens=100, latency_ms=5.0, cost=1e-4)
        for q, spec in qs.items():
            if spec["type"] == "choice":
                opts = list(spec["criteria"])
                pick = self.intent if q == "intent" else "none" if "none" in opts else opts[0]
                a.choices[q] = ChoiceResult(pick, {o: float(o == pick) for o in opts}, self.confidence)
            else:
                a.nouls[q] = 0.1
        return a

    async def close(self):
        pass


def fight():
    snap = copy.deepcopy(SNAPSHOT)
    snap["journal"] = []
    return snap


def far(distance=12):
    """A mongbat that pays the warrior no heed, `distance` tiles off; nothing else in view."""
    snap = fight()
    snap["player"].update(hits=100, war_mode=False)
    snap["mobiles"] = [{"serial": 0x200, "name": "a mongbat", "body": 39, "monster": True, "hits_pct": 100,
                        "dead": False, "war_mode": False, "distance": distance, "dx": distance, "dy": 0,
                        "dir": "east", "my_target": False}]
    snap["corpses"] = []
    snap["agent"].update(engaged=0, bandaging=False)
    return snap


def empty():
    snap = far()
    snap["mobiles"] = []
    return snap


def play(snapshot_at, judge, seconds=0.5, log=None, archetype=None, **cfg):
    cfg = loop.LoopConfig(poll_s=0.01, combat_interval_s=0.02, idle_interval_s=0.02, duration_s=seconds, **cfg)
    rpc = Rpc(snapshot_at)
    stats = asyncio.run(loop.run(rpc, judge, cfg, policy.PolicyConfig(), log, archetype=archetype))
    return rpc, stats


def test_the_same_state_is_decided_again_on_the_last_calls_answers(tmp_path):
    judge = Judge("fight")
    log = tmp_path / "run.jsonl"
    rpc, stats = play(lambda t: fight(), judge, log=log)
    assert len(judge.asked) == stats.decisions == 1 and stats.reused >= 5
    recs = [json.loads(line) for line in log.read_text().splitlines()]
    ds = [r for r in recs if r["type"] == "decision"]
    # Each one is still decided by the policy (and logged), but only the first was a call.
    assert len(ds) == stats.made and all(d["intent"] == "fight" for d in ds)
    assert "questions" in ds[0] and "not_asked" not in ds[0]
    assert all(d["not_asked"] == loop.SAME_STATE and "questions" not in d and d["answers"]["cost"] == 0 for d in ds[1:])
    payloads = [p for _, p in rpc.decisions()]
    assert "not_asked" not in payloads[0] and all(p["not_asked"] == loop.SAME_STATE for p in payloads[1:])
    s = recs[-1]
    assert s["type"] == "summary" and s["decisions"] == 1 and s["not_asked"]["same_state"] == stats.reused
    assert sum(1 for r in recs if r["type"] == "ai_cost") == 0  # the fake judge records no cost of its own


def test_a_new_state_is_asked_and_an_old_answer_is_asked_again_after_a_while():
    # Health moving within its band changes the state's words ("lightly wounded (80%)", "(79%)").
    judge = Judge("fight")

    def hurt(t):
        snap = fight()
        snap["player"]["hits"] = 80 - int(t * 40)
        return snap

    _, stats = play(hurt, judge)
    assert stats.decisions >= 5 and len(judge.asked) == stats.decisions
    judge = Judge("fight")
    _, stats = play(lambda t: fight(), judge, reuse_s=0.1)
    assert stats.decisions >= 3 and stats.reused >= 3


def test_an_unsure_answer_is_asked_again():
    judge = Judge("fight", confidence=0.3)  # under the policy's 0.35: "unsure, keeping course"
    _, stats = play(lambda t: fight(), judge)
    assert stats.gated == stats.decisions == len(judge.asked) >= 5 and stats.reused == 0


def test_with_nothing_in_view_jev_is_not_asked_and_the_policy_still_acts():
    def resting_mage(t):
        snap = mage_snapshot()
        snap["journal"] = []
        snap["mobiles"], snap["corpses"] = [], []
        snap["player"]["mana"] = 40
        snap["agent"].update(engaged=0)
        return snap

    judge = Judge("fight")
    rpc, stats = play(resting_mage, judge, archetype="mage")
    assert judge.asked == [] and stats.decisions == 0 and stats.unasked >= 3
    payloads = [p for _, p in rpc.decisions()]
    assert all(p["not_asked"] == loop.NOTHING_TO_CHOOSE and p["questions"] == [] and p["intents"] == {}
               for p in payloads)
    # Resting below 80% mana, the mage meditates: the policy's own rule, with no call.
    acts = [p for _, m, p in rpc.calls if m == "act"]
    assert acts and acts[0]["verb"] == "skill" and acts[0]["name"] == "Meditation"


def test_something_to_choose_is_asked():
    snap = fight()
    sit = state.build(snap, set(), [], set())
    assert not loop.nothing_to_choose(sit, questions.build(sit))
    snap = empty()
    snap["corpses"] = copy.deepcopy(SNAPSHOT["corpses"])  # a corpse to loot
    sit = state.build(snap, set(), [], set())
    assert sit.corpses and not loop.nothing_to_choose(sit, questions.build(sit))
    sit = state.build(empty(), set(), [], set())
    qs = questions.build(sit)
    assert loop.nothing_to_choose(sit, qs)
    assert not loop.nothing_to_choose(sit, {**qs, "go_fight": {"type": "noul"}})  # a plan's move to ask


def test_the_clock_stretches_while_quiet_and_the_quiet_ending_decides_at_once():
    _, every = play(lambda t: far(), Judge(), seconds=1.2, ask_every_tick=True)
    rpc, quiet = play(lambda t: far(), Judge(), seconds=1.2, quiet_max_s=0.3)
    assert quiet.made < every.made / 3
    gaps = [b - a for (a, _), (b, _) in zip(rpc.decisions(), rpc.decisions()[1:])]
    assert max(gaps) >= 0.25  # stretched to quiet_max_s
    # The mongbat comes within 7 tiles at 0.8 s: decided at once, not when the stretched clock runs out.
    rpc, _ = play(lambda t: far(5 if t >= 0.8 else 12), Judge(), seconds=1.2, quiet_max_s=0.6)
    first = next(t for t, p in rpc.decisions() if t >= 0.8)
    assert first - 0.8 < 0.15


def test_asking_every_tick_keeps_the_old_loop():
    judge = Judge("fight")
    _, stats = play(lambda t: fight(), judge, ask_every_tick=True)
    assert stats.decisions == len(judge.asked) == stats.made >= 5
    judge = Judge()
    _, stats = play(lambda t: empty(), judge, ask_every_tick=True)
    assert stats.decisions == len(judge.asked) >= 5 and stats.unasked == 0


def test_the_bench_plays_the_old_loop_beside_the_new():
    from uo_brain.bench import JudgeSpec
    old = JudgeSpec("jev/everytick")
    assert (old.kind, old.every_tick, old.kite) == ("jev", True, True)
    assert not JudgeSpec("jev").every_tick and JudgeSpec("jev+survivor/everytick").template == "survivor"


def test_switched_off_and_on_while_quiet_it_decides_at_once():
    def toggled(t):
        snap = far()
        snap["agent"]["mode"] = "off" if 0.35 <= t < 0.4 else "auto"
        return snap

    # Decisions at about 0, 0.02, 0.06, 0.15 and 0.31 s, the next due at 0.63 s; off and on in between.
    rpc, _ = play(toggled, Judge(), seconds=1.0, quiet_max_s=0.6)
    first = next(t for t, p in rpc.decisions() if t >= 0.4)
    assert first - 0.4 < 0.15


def test_defending_only_a_far_creature_out_of_war_mode_or_a_corpse_is_no_change():
    calm = state.build(far(), set(), [], set())
    cat = far()
    cat["mobiles"].append({"serial": 0x201, "name": "a cat", "body": 201, "monster": True, "hits_pct": 100,
                           "dead": False, "war_mode": False, "distance": 10, "dx": 0, "dy": 10, "dir": "south"})
    cat["corpses"] = copy.deepcopy(SNAPSHOT["corpses"])
    cat["corpses"][0].update(opened=False, items=[])  # not opened: an open one's items still count
    seen = state.build(cat, set(), [], set())
    assert seen.signature(defend_only=True) == calm.signature(defend_only=True)
    assert seen.signature() != calm.signature()  # hunting, both count
    cat["mobiles"][-1].update(distance=6, dy=6)  # nearby now
    assert state.build(cat, set(), [], set()).signature(defend_only=True) != calm.signature(defend_only=True)
    cat["mobiles"][-1].update(distance=11, dy=11, war_mode=True)  # far, but in war mode
    assert state.build(cat, set(), [], set()).signature(defend_only=True) != calm.signature(defend_only=True)


def test_a_setting_changed_while_the_loop_runs_is_decided_on_at_once():
    """A companion told to loot in the quiet after a fight (standing.py, cuo-cym.18): what its leader's word has
    changed is laid over the loop's settings, and it isn't left standing until the stretched clock runs out."""
    from dataclasses import replace

    def quiet(t):
        snap = far()
        snap["mobiles"], snap["corpses"] = [], copy.deepcopy(SNAPSHOT["corpses"])
        return snap

    began = time.monotonic()

    def told_to_loot(cfg):
        return replace(cfg, looting="valuables", take_item=0.6) if time.monotonic() - began >= 0.4 else cfg

    cfg = loop.LoopConfig(poll_s=0.01, combat_interval_s=0.02, idle_interval_s=0.02, duration_s=1.0, quiet_max_s=0.6)
    rpc = Rpc(quiet)
    asyncio.run(loop.run(rpc, Judge("loot"), cfg, policy.PolicyConfig(escort="Roland", looting="nothing", live=told_to_loot), None))
    looted = [t for t, m, p in rpc.calls if m == "act" and p.get("verb") == "loot"]
    # Decisions at about 0, 0.02, 0.06, 0.15 and 0.31 s, the next due at 0.63 s; told to loot in between.
    assert looted and 0.4 <= looted[0] < 0.55
