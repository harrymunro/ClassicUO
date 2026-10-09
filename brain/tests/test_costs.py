import asyncio
import json
from types import SimpleNamespace

import pytest

from uo_brain import costs, judge, llm


@pytest.fixture(autouse=True)
def fresh_ledger():
    costs.ledger.reset()
    yield
    costs.ledger.reset()


def test_a_call_is_costed_by_the_provider_else_by_the_price_table():
    reported = costs.make_call("fight", "typesafe/jev-1.13-20260917", 281, 20, reported=1.1802e-05)
    assert reported.cost == 1.1802e-05 and not reported.estimated
    est = costs.make_call("fight", "typesafe/jev-1.13-20260917", 1_000_000, 500)
    assert est.estimated and est.cost == pytest.approx(0.042)  # Jev's output tokens are free
    sonnet = costs.make_call("planner", "anthropic/claude-sonnet-5.5", 10_000, 1_000, cached_tokens=8_000)
    assert sonnet.cost == pytest.approx((2_000 * 2.0 + 8_000 * 0.2 + 1_000 * 10.0) / 1e6)
    unknown = costs.make_call("fight", "someone/new-model", 1000, 100)
    assert unknown.estimated and unknown.cost == 0.0
    assert sonnet.role == "system2" and est.role == "system1"


def test_the_ledger_totals_by_kind_and_model_and_since_a_mark():
    led = costs.ledger
    led.record(costs.make_call("fight", "jev", 1000, reported=0.001))
    mark = led.mark()
    led.record(costs.make_call("fight", "jev", 2000, reported=0.002))
    led.record(costs.make_call("planner", "anthropic/claude-sonnet-5.5", 4000, 100, reported=0.01))
    assert led.spent == pytest.approx(0.013)
    s = led.summary(mark, kills=2, decisions=1, hours=0.5)
    assert s["cost_usd"] == pytest.approx(0.012) and s["cost_per_hour_usd"] == pytest.approx(0.024)
    assert s["by_role"] == {"system1": 0.002, "system2": 0.01}
    assert s["by_kind"]["fight"]["calls"] == 1 and s["cost_per_kill_usd"] == 0.006
    assert list(s["by_model"]) == ["anthropic/claude-sonnet-5.5", "jev"]  # dearest first


def test_cost_records_go_to_the_innermost_log():
    outer, inner = [], []
    with costs.ledger.logging_to(outer.append):
        costs.ledger.record(costs.make_call("planner", "m", 10, reported=0.1))
        with costs.ledger.logging_to(inner.append):
            costs.ledger.record(costs.make_call("fight", "jev", 10, reported=0.01))
        costs.ledger.record(costs.make_call("planner", "m", 10, reported=0.1))
    costs.ledger.record(costs.make_call("fight", "jev", 10, reported=0.01))  # no log: counted, not written
    assert [r["kind"] for r in outer] == ["planner", "planner"] and [r["kind"] for r in inner] == ["fight"]
    assert inner[0]["type"] == "ai_cost" and inner[0]["role"] == "system1" and costs.ledger.total().calls == 4
    # A report rebuilds the same totals from the records.
    s = costs.from_records(outer + inner, hours=1.0)
    assert s["cost_usd"] == pytest.approx(0.21) and s["by_kind"]["planner"]["calls"] == 2


def test_the_kind_is_set_by_the_caller_and_kept_in_tasks():
    async def go():
        seen = []

        async def inner():
            seen.append(costs.current_kind())

        with costs.kind("facts"):
            await asyncio.gather(inner(), inner())
        seen.append(costs.current_kind("none"))
        return seen

    assert asyncio.run(go()) == ["facts", "facts", "none"]


def test_a_budget_is_judged_over_the_last_ten_minutes_never_less_than_five():
    led = costs.ledger
    led.budget = costs.Budget(0.50, "slow")
    t0 = led.started
    # One early planner call: $0.02 over the five-minute floor is $0.24/h, under the cap.
    led.record(costs.Call("planner", "m", cost=0.02, t=t0 + 30))
    assert led.rate(t0 + 60) == pytest.approx(0.24) and not led.over_budget(t0 + 60)
    led.record(costs.Call("planner", "m", cost=0.03, t=t0 + 90))
    assert led.over_budget(t0 + 120)  # $0.05 in five minutes is $0.60/h
    # Twenty minutes on, that spending has left the window.
    assert not led.over_budget(t0 + 1200) and led.rate(t0 + 1200) == 0.0
    with pytest.raises(ValueError):
        costs.Budget(1.0, "panic")
    live = led.live()
    assert live["budget_per_hour"] == 0.5 and live["total"] == 0.05


class FakeSystemOne:
    def __init__(self, body):
        self.body = body

    async def system_one(self, state, questions):
        return SimpleNamespace(model=self.body["model"], usage=SimpleNamespace(input_tokens=281, output_tokens=20),
                               raw_http_response=SimpleNamespace(content=json.dumps(self.body).encode()),
                               choices={}, nouls={}, scores={})


def test_jev_reads_openrouters_cost_and_records_the_call():
    j = judge.JevJudge.__new__(judge.JevJudge)
    j.name = "jev/openrouter"
    j.client = FakeSystemOne({"model": "typesafe/jev-1.13-20260917",
                              "usage": {"input_tokens": 281, "output_tokens": 20, "cost": 1.1802e-05}})
    with costs.kind("routine"):
        a = asyncio.run(j.ask({}, {}))
    assert a.cost == 1.1802e-05 and a.output_tokens == 20 and a.to_log()["cost"] == 1.18e-05
    rec = costs.ledger.by_kind["routine"]
    assert rec.calls == 1 and rec.estimated == 0
    # TypeSafe's own API reports no cost: the price table's estimate, marked as such.
    j.client = FakeSystemOne({"model": "jev-1.13", "usage": {"input_tokens": 281, "output_tokens": 20}})
    a = asyncio.run(j.ask({}, {}))
    assert a.cost == pytest.approx(281 * 0.042 / 1e6) and costs.ledger.by_kind["other"].estimated == 1


def test_the_planners_chat_is_recorded_with_its_kind(monkeypatch):
    monkeypatch.setattr(llm, "api_key", lambda: "k")
    monkeypatch.setattr(llm, "_post", lambda body, key, timeout, retries: {
        "model": body["model"], "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3000, "completion_tokens": 50, "cost": 0.0065}})
    with costs.kind("review"):
        res = asyncio.run(llm.chat([{"role": "user", "content": "x"}], model="anthropic/claude-sonnet-5.5"))
    assert res.usage.cost == 0.0065
    t = costs.ledger.by_kind["review"]
    assert (t.calls, t.input_tokens, t.output_tokens, t.cost) == (1, 3000, 50, 0.0065)


def test_the_fight_loop_slows_or_stops_over_its_budget(tmp_path):
    from test_facts import FactJudge, FakeRpc, wisp_snapshot

    from uo_brain import loop, policy

    snap = wisp_snapshot()
    snap["journal"] = []
    cfg = loop.LoopConfig(poll_s=0.01, combat_interval_s=0.02, duration_s=0.6, slow_factor=10)
    fast = asyncio.run(loop.run(FakeRpc(snap), FactJudge(), cfg, policy.PolicyConfig(), None))
    costs.ledger.record(costs.Call("planner", "m", cost=1.0))  # $12 an hour so far
    costs.ledger.budget = costs.Budget(0.50, "slow")
    rpc = FakeRpc(snap)
    slow = asyncio.run(loop.run(rpc, FactJudge(), cfg, policy.PolicyConfig(), None))
    assert slow.decisions < fast.decisions / 3 and slow.slowed == slow.decisions
    assert any(m == "note" and p["text"].startswith("over budget: $") for m, p in rpc.calls)
    costs.ledger.budget = costs.Budget(0.50, "stop")
    stopped = asyncio.run(loop.run(FakeRpc(snap), FactJudge(), cfg, policy.PolicyConfig(), None))
    assert stopped.decisions == 0


def test_a_task_keeps_its_own_cost_log():
    """The planner thinks while the fight loop defends in another task: each writes to its own log."""
    session, fights = [], []

    async def fight_loop(started, go_on):
        with costs.ledger.logging_to(fights.append):
            started.set()
            await go_on.wait()
            costs.ledger.record(costs.make_call("fight", "jev", 10, reported=0.001))

    async def main():
        started, go_on = asyncio.Event(), asyncio.Event()
        with costs.ledger.logging_to(session.append):
            task = asyncio.create_task(fight_loop(started, go_on))
            await started.wait()
            costs.ledger.record(costs.make_call("planner", "sonnet", 10, reported=0.02))
            go_on.set()
            await task

    asyncio.run(main())
    assert [r["kind"] for r in session] == ["planner"] and [r["kind"] for r in fights] == ["fight"]
