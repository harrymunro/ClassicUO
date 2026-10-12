import asyncio

from uo_brain import costs, models, planner
from uo_brain.planner import PlanStep
from uo_brain.world import World

from test_planner import FakeSession, call, reply, scripted


def make(tmp_path, **kw):
    return planner.Planner(FakeSession(World(tmp_path / "w.sqlite")), "hunt", chat_fn=scripted(), memory=False, **kw)


def test_hard_moments_are_told_apart(tmp_path):
    p = make(tmp_path)
    assert p.hard_moment() == ""
    p.history.append(PlanStep("hunt", {"area": "Britain Graveyard"}, {
        "ok": True, "result": "hunted", "stopped_because": "left the area: 3 coming at once"}))
    assert p.hard_moment() == ""  # a leave by itself isn't escalated; going back and being refused is
    p.history.append(PlanStep("hunt", {"area": "Britain Graveyard"}, {
        "ok": False, "result": "not going back near Britain Graveyard yet: it left there 2 min ago"}))
    assert p.hard_moment().startswith("code refused")
    p.history = [PlanStep("buy", {"item": "bandage"}, {"ok": False, "result": "sold out"}),
                 PlanStep("buy", {"item": "bandage"}, {"ok": False, "result": "sold out"})]
    assert p.hard_moment() == "the same goal failed twice running: buy bandage"
    p.history = [PlanStep("buy", {"item": "bandage"}, {"ok": False, "result": "x"}),
                 PlanStep("travel_to", {"place": "Yew"}, {"ok": False, "result": "x"}),
                 PlanStep("bank", {}, {"ok": False, "result": "x"})]
    assert p.hard_moment() == "three goals failed in a row"
    p.history = []
    p.no_tool = 2
    assert p.hard_moment() == "no usable goal from the planner twice running"
    p.escalated_last = True
    assert p.hard_moment() == ""  # never twice in a row
    assert make(tmp_path, escalate=False).hard_moment() == ""


def test_a_death_is_not_a_hard_moment(tmp_path):
    p = make(tmp_path)
    p.history.append(PlanStep("hunt", {"area": "Britain Graveyard"}, {
        "ok": False, "result": "the character died", "deaths": 1}))
    assert p.hard_moment() == ""  # every model resurrects, and code picks the healer
    p.history += [PlanStep("resurrect", {}, {"ok": False, "result": "no healer reached"}),
                  PlanStep("resurrect", {}, {"ok": False, "result": "no healer reached"})]
    assert p.hard_moment() == "the same goal failed twice running: resurrect "


def test_a_hard_moment_goes_to_the_stronger_model_under_its_own_kind(tmp_path):
    saved = models.active
    models.active = models.Profile()
    costs.ledger.reset()
    try:
        seen = []

        async def chat(messages, tools=None, model=None, **_):
            seen.append((model, costs.current_kind()))
            return reply(call("rest", {"seconds": 30, "why": "w"}))

        p = planner.Planner(FakeSession(World(tmp_path / "w.sqlite")), "hunt", chat_fn=chat, memory=False)
        p.history.append(PlanStep("hunt", {"area": "Britain Graveyard"}, {
            "ok": False, "result": "not going back near Britain Graveyard yet: it left there 2 min ago"}))
        logged = []
        p.log = logged.append
        asyncio.run(p.step())
        assert seen[0] == ("anthropic/claude-sonnet-5.5", "escalate")
        assert any(r["type"] == "escalation" for r in logged)
        asyncio.run(p.step())  # the rest went fine, and never twice running: back to Haiku
        assert seen[1] == ("anthropic/claude-haiku-5.5", "planner")
        s = p.summary()
        assert s["escalations"] == 1 and list(s["escalation_reasons"]) == ["code refused to go back where it left: somewhere new is needed"]
        # The npc profile names Haiku for escalate: the same model as the planner's turns it off.
        models.active = models.load("npc")
        assert planner.Planner(FakeSession(World(tmp_path / "w2.sqlite")), "x", chat_fn=chat,
                               memory=False).escalation_model() is None
    finally:
        models.active = saved
        costs.ledger.reset()


def test_a_failed_planner_call_rests_instead_of_ending_the_session(tmp_path):
    from uo_brain import llm

    async def broken(*_, **__):
        raise llm.LlmError("OpenRouter unreachable: SSLError: [SSL: SSLV3_ALERT_BAD_RECORD_MAC]")

    logged = []
    p = planner.Planner(FakeSession(World(tmp_path / "w.sqlite")), "hunt", chat_fn=broken, memory=False,
                        escalate=False, log=logged.append)
    step = asyncio.run(p.step())
    assert step.tool == "rest" and "the planner model failed" in step.args["why"]
    assert any(r["type"] == "planner" and "SSL" in r.get("error", "") for r in logged)


def test_an_ssl_error_reading_the_answer_is_retried_then_an_llm_error(monkeypatch):
    import ssl
    from uo_brain import llm
    tries = []

    def urlopen(*_, **__):
        tries.append(1)
        raise ssl.SSLError("SSLV3_ALERT_BAD_RECORD_MAC")

    monkeypatch.setattr(llm.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(llm.time, "sleep", lambda _: None)
    import pytest
    with pytest.raises(llm.LlmError, match="SSLError"):
        llm._post({"model": "m"}, "key", 1.0, retries=2)
    assert len(tries) == 3
