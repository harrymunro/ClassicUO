import asyncio
import copy
import json

import pytest

from uo_brain import bench, facts, loop, planner, policy, questions, state
from uo_brain.judge import Answers, ChoiceResult
from uo_brain.world import World

from conftest import SNAPSHOT
from test_planner import FakeSession, call, reply, scripted

LANE_1 = (5445 + 60, 1153)


class FactJudge:
    """Scores a fact or result by the words in it; answers decision questions with their first option."""

    name = "fake-jev"

    def __init__(self, good=("wisp",), fail=False):
        self.good = good
        self.fail = fail
        self.asked = []

    async def ask(self, state, qs):
        self.asked.append((state, qs))
        if self.fail:
            raise TimeoutError("no answer")
        a = Answers(model="fake", input_tokens=100 * len(qs), latency_ms=5.0)
        for q, spec in qs.items():
            if spec["type"] == "choice":
                first = next(iter(spec["criteria"]))
                a.choices[q] = ChoiceResult(first, {first: 1.0}, 1.0)
                continue
            ins = spec["instructions"]
            text = (ins.get("fact") or ins.get("result") or "") if isinstance(ins, dict) else ""
            hits = sum(w in text.lower() for w in self.good)
            a.nouls[q] = min(0.95, 0.3 * hits + 0.4) if text else 0.1
        return a

    async def close(self):
        pass


def wisp_snapshot(dx=0, kinds=("wisp", "orc")):
    snap = copy.deepcopy(SNAPSHOT)
    snap["player"].update(x=LANE_1[0] + dx, y=LANE_1[1])
    mobiles = []
    if "orc" in kinds:
        mobiles.append({"serial": 0x100, "name": "Gugrak", "body": 17, "monster": True, "hits_pct": 100, "dead": False,
                        "war_mode": True, "distance": 1, "dx": 1, "dy": 0, "dir": "east", "my_target": True})
    if "wisp" in kinds:
        mobiles.append({"serial": 0x101, "name": "a wisp", "body": 58, "monster": True, "hits_pct": 100,
                        "dead": False, "war_mode": False, "distance": 7, "dx": 0, "dy": -7, "dir": "north",
                        "my_target": False})
    if "ratman" in kinds:
        mobiles.append({"serial": 0x102, "name": "Vitavi", "body": 42, "monster": True, "hits_pct": 100,
                        "dead": False, "war_mode": True, "distance": 5, "dx": 5, "dy": 0, "dir": "east",
                        "my_target": False})
    snap["mobiles"] = mobiles
    snap["corpses"] = []
    return snap


@pytest.fixture
def w():
    w = World(":memory:")
    bench.seed_wisp(w)
    yield w
    w.close()


def run(coro):
    return asyncio.run(coro)


def test_the_shortlist_is_generous_and_from_every_angle(w):
    w.add_note("Wisps at the test field were seen in pairs.", area=bench.TEST_FIELD)
    old = w.add_note("Wisps here are harmless.", area=bench.TEST_FIELD)
    w.mark_stale("notes", old)
    fs = facts.shortlist(w, *LANE_1, {"wisp", "orc"}, goal="keep yourself supplied with bandages",
                         archetype="warrior")
    texts = [f.text for f in fs]
    assert 20 <= len(fs) <= 50 and len(set(texts)) == len(texts)
    assert any(f.why == f"area: {bench.TEST_FIELD}" and "never attack first" in f.text for f in fs)
    assert any(f.why == "creature: wisp" and "leave wisps alone" in f.text for f in fs)
    assert any(f.why == "archetype" for f in fs) and any(f.why == "goal" for f in fs)
    assert "Wisps here are harmless." not in texts  # stale rows stay out
    assert not any("Covetous" in t for t in texts)  # other places' notes don't come in
    assert len(facts.shortlist(w, *LANE_1, {"wisp", "orc"}, limit=5)) == 5


def test_danger_sightings_are_left_to_the_planner(w):
    """Picked in fights, "Had to leave" notes made every later hunt there end in a leave, each
    writing another such note."""
    w.add_note("Had to leave, 2026-10-07 14:41: 3 coming at once: 2 gargoyles and a zombie", area=bench.TEST_FIELD,
               tags=["danger"], source="seen")
    w.add_note("Stronger than a new character, seen 2026-10-07 04:55: 1 wisp.", area=bench.TEST_FIELD,
               tags=["danger"], source="seen")
    texts = [f.text for f in facts.shortlist(w, *LANE_1, {"wisp", "orc"})]
    assert not any(t.startswith(("Had to leave", "Stronger than a new character")) for t in texts)
    assert any("never attack first" in t for t in texts)


def picker_with(w, judge, **kw):
    p = facts.FactPicker(w, judge, min_interval_s=0, **kw)
    p.records = []
    p.log = p.records.append
    return p


async def step(p, snap):
    sit = state.build(snap, set(), [])
    p.update(snap, sit)
    if p._task:
        await p._task
    p.apply(sit)
    return sit


def test_jev_picks_the_few_facts_that_matter_and_they_reach_the_questions(w):
    judge = FactJudge()

    async def go():
        p = picker_with(w, judge)
        await step(p, wisp_snapshot())
        sit = await step(p, wisp_snapshot())
        return p, sit

    p, sit = run(go())
    assert len(judge.asked) == 1  # one fan-out request for the whole shortlist (under 25 facts)
    state_sent, qs = judge.asked[0]
    assert state_sent["where"] == bench.TEST_FIELD and any("wisp" in c for c in state_sent["creatures_in_view"])
    assert all(q["type"] == "noul" and q["instructions"]["fact"] for q in qs.values())
    assert 1 <= len(p.chosen) <= 3 and all(s >= 0.6 for _, s in p.chosen)
    assert all("wisp" in f.text.lower() for f, _ in p.chosen)
    assert sit.known and sit.state[facts.KNOWN_KEY] == sit.known
    # The decision questions see the facts, and leaving is asked about with them in mind.
    built = questions.build(sit)
    assert "leave_now" in built and facts.KNOWN_KEY in built["leave_now"]["instructions"]["reason"]
    rec = p.records[0]
    assert rec["type"] == "facts" and rec["trigger"] == "start" and rec["shortlist"] == len(rec["facts"])
    assert rec["input_tokens"] == 100 * rec["shortlist"] and p.take_tokens() == rec["input_tokens"]
    assert p.take_tokens() == 0


def test_a_new_selection_only_on_a_situation_change(w):
    judge = FactJudge()

    async def go():
        p = picker_with(w, judge)
        await step(p, wisp_snapshot(kinds=("orc",)))
        await step(p, wisp_snapshot(kinds=("orc",)))  # nothing new
        await step(p, wisp_snapshot(kinds=()))         # a kind left: nothing new either
        await step(p, wisp_snapshot(kinds=("orc", "wisp")))
        n_creature = len(judge.asked)
        snap = wisp_snapshot()
        snap["player"].update(x=1000, y=1000)  # somewhere else: the old facts go at once
        sit = state.build(snap, set(), [])
        p.update(snap, sit)
        cleared = list(p.chosen)
        await p._task
        return p, n_creature, cleared

    p, n_creature, cleared = run(go())
    assert n_creature == 2
    assert [r["trigger"] for r in p.records] == ["start", "new creature: wisp", "new area: wilderness near 1000,1000"]
    assert cleared == []


def test_selections_wait_for_the_minimum_interval(w):
    async def go():
        p = facts.FactPicker(w, FactJudge(), min_interval_s=60)
        await step(p, wisp_snapshot(kinds=("orc",)))
        await step(p, wisp_snapshot(kinds=("orc", "wisp")))  # a change, but too soon: it waits
        return p

    p = run(go())
    assert p.selections == 1 and p._wanted is not None


def test_all_mode_dumps_the_shortlist_and_the_rule_judge_gets_none(w):
    async def go(judge, mode):
        p = picker_with(w, judge, mode=mode)
        return p, await step(p, wisp_snapshot())

    judge = FactJudge()
    p, sit = run(go(judge, "all"))
    assert not judge.asked and len(sit.known) == p.records[0]["shortlist"] >= 20

    class Rules(FactJudge):
        name = "heuristic"

    rules = Rules()
    p, sit = run(go(rules, "jev"))
    assert not rules.asked and sit.known == [] and facts.KNOWN_KEY not in sit.state
    p, sit = run(go(FactJudge(), "none"))
    assert p.records == [] and sit.known == []


def test_a_failed_selection_leaves_no_facts_and_says_why(w):
    async def go():
        p = picker_with(w, FactJudge(fail=True))
        return p, await step(p, wisp_snapshot())

    p, sit = run(go())
    assert sit.known == [] and "TimeoutError" in p.records[0]["error"]


def test_long_shortlists_go_out_as_parallel_requests(w):
    for i in range(40):
        w.add_note(f"Wisp sighting number {i} at the test field.", area=bench.TEST_FIELD)
    judge = FactJudge()

    async def go():
        p = picker_with(w, judge)
        await step(p, wisp_snapshot())
        return p

    p = run(go())
    n = p.records[0]["shortlist"]
    assert n > 25 and sorted(len(qs) for _, qs in judge.asked) == sorted([25, n - 25])
    assert len(p.chosen) == 3


def test_a_picked_fact_backs_leaving_when_jev_says_leave():
    snap = wisp_snapshot()
    sit = state.build(snap, set(), [])
    ans = Answers(choices={"intent": ChoiceResult("leave", {"leave": 0.9, "fight": 0.1}, 0.9)},
                  nouls={"in_danger": 0.1, "leave_now": 0.8})
    cfg = policy.PolicyConfig()
    # No stored fact: nothing in the state backs leaving, so the warrior fights on.
    assert policy.decide(sit, ans, policy.Memory(), cfg, now=0).intent == "fight"
    sit.known = ["Wisps at the test field never attack first."]
    assert policy.decide(sit, ans, policy.Memory(), cfg, now=0).intent == "leave"
    # A fact alone isn't enough: Jev's own yes/no on leaving must agree.
    ans.nouls["leave_now"] = 0.2
    assert policy.decide(sit, ans, policy.Memory(), cfg, now=0).intent == "fight"


# ---------------------------------------------------------------- the planner


def test_the_planner_queries_come_back_best_answer_first(w):
    for text in ("The Britain healer sells bandages, 20 at a time.", "Wisps glow at night.",
                 "Bandages are cheapest at the Britain healer."):
        w.add_note(text, area="Britain")
    ranker = facts.Ranker(FactJudge(good=("bandage",)))
    out = run(ranker.call_tool(w, "notes", {"area": "Britain", "limit": 2, "question": "Where can I buy bandages?"},
                               goal="keep yourself supplied with bandages"))
    assert len(out) == 2 and all("andage" in n["text"] for n in out)
    assert all(0 <= n["relevance"] <= 1 for n in out) and out[0]["relevance"] >= out[1]["relevance"]
    asked_state, qs = ranker.judge.asked[0]
    assert asked_state["planner_question"] == "Where can I buy bandages?"
    # The store was asked for 3x the limit (at least 15): every Britain note.
    assert len(qs) == len(w.call_tool("notes", {"area": "Britain", "limit": 30})) == 5
    assert ranker.calls == 1 and ranker.input_tokens == 500


def test_without_jev_the_store_order_is_kept_and_question_is_ignored(w):
    class Rules(FactJudge):
        name = "heuristic"

    for i in range(4):
        w.add_note(f"Britain note {i}", area="Britain")
    plain = w.call_tool("notes", {"area": "Britain", "limit": 2})
    rules = facts.Ranker(Rules())
    assert run(rules.call_tool(w, "notes", {"area": "Britain", "limit": 2, "question": "anything"})) == plain
    assert not rules.judge.asked
    failing = facts.Ranker(FactJudge(fail=True), log=(records := []).append)
    assert run(failing.call_tool(w, "notes", {"area": "Britain", "limit": 2})) == plain
    assert "TimeoutError" in records[0]["error"]
    # Queries that aren't lists of candidates pass straight through.
    judge = FactJudge()
    assert run(facts.Ranker(judge).call_tool(w, "region_at", {"x": 5445, "y": 1153}))["name"] == bench.TEST_FIELD
    assert not judge.asked
    assert w.call_tool("notes", {"area": "Britain", "question": "x"}) == w.call_tool("notes", {"area": "Britain"})


def test_the_planner_hands_its_question_to_the_ranker(tmp_path):
    w = World(tmp_path / "w.sqlite")
    w.add_note("Liches at Covetous are far too strong for a new character.", area="Covetous")
    w.add_note("Covetous has a bank near its entrance.", area="Covetous")
    s = FakeSession(w)
    s.judge = FactJudge(good=("lich",))
    chat = scripted(
        reply(call("notes", {"area": "Covetous", "question": "Is Covetous safe for a new warrior?"})),
        reply(call("rest", {"seconds": 10, "why": "wait"}, "c2")),
    )
    p = planner.Planner(s, "hunt somewhere safe", chat_fn=chat)
    run(p.step())
    answer = json.loads(chat.seen[1]["messages"][-1]["content"])
    assert "Liches" in answer[0]["text"] and answer[0]["relevance"] > answer[1]["relevance"]
    assert p.summary()["reranked_queries"] == 1
    notes_tool = next(t for t in p.tools if t["function"]["name"] == "notes")
    assert "question" in notes_tool["function"]["parameters"]["properties"]


# ---------------------------------------------------------------- the loop


class FakeRpc:
    def __init__(self, snap):
        self.snap = snap
        self.calls = []

    async def call(self, method, **params):
        self.calls.append((method, params))
        if method == "snapshot":
            return copy.deepcopy(self.snap)
        if method == "act":
            return {"status": "done"}
        return {}


def test_the_loop_puts_picked_facts_into_its_decisions_and_logs_them(w, tmp_path):
    snap = wisp_snapshot()
    snap["journal"] = []
    rpc = FakeRpc(snap)
    log = tmp_path / "run.jsonl"
    p = facts.FactPicker(w, FactJudge())
    cfg = loop.LoopConfig(poll_s=0.01, combat_interval_s=0.05, duration_s=0.5)
    stats = run(loop.run(rpc, FactJudge(), cfg, policy.PolicyConfig(), log, facts=p))
    recs = [json.loads(line) for line in log.read_text().splitlines()]
    kinds = [r["type"] for r in recs]
    assert kinds.count("facts") == 1 and "decision" in kinds
    later = [r for r in recs if r["type"] == "decision" and facts.KNOWN_KEY in r["state"]]
    assert later and all("wisp" in t.lower() for t in later[-1]["state"][facts.KNOWN_KEY])
    summary = recs[-1]
    assert summary["type"] == "summary" and summary["fact_selections"] == 1
    assert summary["fact_input_tokens"] == stats.fact_tokens > 0


def test_the_wisp_scenario_plays_three_fact_conditions_per_model_judge():
    sc = bench.SCENARIOS["wisp-leave-alone"]
    assert sc.seed is bench.seed_wisp and "wisp-leave-alone" in bench.WORLD
    spec = bench.JudgeSpec("jev+survivor@all")
    assert (spec.kind, spec.template, spec.facts) == ("jev", "survivor", "all")
    assert bench.JudgeSpec("jev").facts == "none"


def test_a_world_fact_round_runs_end_to_end_with_its_own_store(tmp_path, monkeypatch):
    import dataclasses

    from uo_brain import judge as judges

    async def say(rpc, text, pause=0.6):
        await rpc.call("act", verb="say", text=text, source="manual")

    snap = wisp_snapshot()
    snap["journal"] = []
    monkeypatch.setattr(bench, "say", say)
    monkeypatch.setattr(judges, "make", lambda kind, *a, **k: FactJudge() if kind == "jev" else judges.HeuristicJudge())
    quick = dataclasses.replace(bench.SCENARIOS["wisp-leave-alone"], seconds=0.4)
    monkeypatch.setitem(bench.SCENARIOS, "wisp-leave-alone", quick)
    out = tmp_path / "result.json"
    result = run(bench.run(FakeRpc(snap), ["wisp-leave-alone"], ["heuristic", "jev"], 1, 2, out, tmp_path / "logs",
                           progress=lambda line: None))
    judges_run = result["scenarios"]["wisp-leave-alone"]["judges"]
    assert list(judges_run) == ["heuristic", "jev@none", "jev@all", "jev@jev"]
    assert all("error" not in j["runs"][0] for j in judges_run.values()), judges_run
    assert judges_run["jev@jev"]["runs"][0]["facts"] == "jev" and judges_run["jev@jev"]["runs"][0]["fact_selections"] == 1
    assert judges_run["jev@all"]["runs"][0]["facts_in_state"] >= 20
    assert "fact_selections" not in judges_run["jev@none"]["runs"][0]
    assert (tmp_path / "logs" / "worlds" / "wisp-leave-alone" / "world.sqlite").exists()
    assert (tmp_path / "logs" / "wisp-leave-alone-jev_jev-01.jsonl").exists() and json.loads(out.read_text())
