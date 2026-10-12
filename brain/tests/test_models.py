import asyncio
from types import SimpleNamespace

import pytest

from uo_brain import costs, llm, models
from uo_brain import judge as judges
from uo_brain.judge import Answers


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv("JEV_MODEL", raising=False)
    monkeypatch.delenv("PLANNER_MODEL", raising=False)
    costs.ledger.reset()
    saved = models.active
    yield
    models.active = saved
    costs.ledger.reset()


def test_a_kinds_model_comes_from_its_own_line_then_its_system_then_the_environment(monkeypatch):
    p = models.Profile(models={"system1": "jev", "routine": "haiku"})
    assert p.model_for("fight") == "jev" and p.model_for("routine") == "anthropic/claude-haiku-5.5"
    assert p.model_for("planner") == "anthropic/claude-haiku-5.5"
    monkeypatch.setenv("PLANNER_MODEL", "openai/gpt-oss-120b")
    assert p.model_for("compact") == "openai/gpt-oss-120b"
    p.use("system2=anthropic/claude-opus-5.5")
    p.use("design=haiku")
    assert p.model_for("compact") == "anthropic/claude-opus-5.5" and p.model_for("design") == "anthropic/claude-haiku-5.5"
    # A kind with a default of its own (KIND_DEFAULTS) keeps it until it is named.
    assert p.model_for("review") == "anthropic/claude-sonnet-5.5"
    p.use("review=opus")
    assert p.model_for("review") == "anthropic/claude-opus-5.5"
    with pytest.raises(ValueError):
        p.use("planer=haiku")


def test_over_a_cheaper_budget_the_cheaper_models_answer():
    p = models.Profile(models={"system2": "sonnet"}, cheaper={"system2": "haiku"})
    costs.ledger.budget = costs.Budget(0.10, "cheaper")
    assert p.model_for("planner") == "anthropic/claude-sonnet-5.5"
    costs.ledger.record(costs.Call("planner", "m", cost=0.5))
    assert p.model_for("planner") == "anthropic/claude-haiku-5.5"
    assert p.model_for("fight") == "microsoft/microsoft-decision-1"  # no cheaper system one named: it stays
    costs.ledger.budget = costs.Budget(0.10, "slow")
    assert p.model_for("planner") == "anthropic/claude-sonnet-5.5"


def test_profiles_load_from_files():
    p = models.load("default")
    assert p.model_for("planner") == "anthropic/claude-haiku-5.5"
    # Decision 1 for system one, Jev where it measured better (cuo-3ni).
    assert p.model_for("fight") == p.model_for("facts") == "microsoft/microsoft-decision-1"
    assert p.model_for("routine") == p.model_for("strategy") == "jev"
    assert set(models.load("jev").system_one().values()) == {"jev"}
    assert not p.cheaper and p.budget is None
    s = models.load("sonnet")
    assert s.model_for("planner") == "anthropic/claude-sonnet-5.5" and s.cheaper["system2"] == "anthropic/claude-haiku-5.5"
    npc = models.load("npc")
    assert npc.budget.per_hour == 0.50 and npc.model_for("planner") == "anthropic/claude-haiku-5.5"
    # Opus for the one-off kinds only (cuo-vpi); the rest as the default profile.
    o, d = models.load("opus"), models.load("default")
    for kind in (*costs.SYSTEM_ONE, *costs.SYSTEM_TWO):
        want = "anthropic/claude-opus-5.5" if kind in ("escalate", "design", "review") else d.model_for(kind)
        assert o.model_for(kind) == want, kind
    # Decision 1 for every system-one call (cuo-79x); system two as the default profile.
    d1 = models.load("decision1")
    for kind in (*costs.SYSTEM_ONE, *costs.SYSTEM_TWO):
        want = "microsoft/microsoft-decision-1" if kind in costs.SYSTEM_ONE else d.model_for(kind)
        assert d1.model_for(kind) == want, kind
    with pytest.raises(ValueError, match="profiles: decision1, default, jev, npc, opus, sonnet"):
        models.load("nobody")
    args = SimpleNamespace(profile="npc", model="haiku", planner_model=None, use=["routine=jev"])
    p = models.from_args(args)
    assert p.model_for("fight") == "anthropic/claude-haiku-5.5" and p.model_for("routine") == "jev"


def test_with_no_profile_system_two_is_haiku_but_review_and_guide_are_sonnet():
    p = models.from_args(SimpleNamespace(profile=None, model=None, planner_model=None, use=[]))
    for kind in ("planner", "design", "compact", "heartbeat"):
        assert p.model_for(kind) == "anthropic/claude-haiku-5.5"
    for kind in ("review", "guide", "escalate"):  # where Haiku fell short (cuo-twd), and hard moments
        assert p.model_for(kind) == "anthropic/claude-sonnet-5.5"
    assert p.model_for("fight") == "microsoft/microsoft-decision-1" and p.model_for("routine") == "jev"
    p.use("review=haiku")
    assert p.model_for("review") == "anthropic/claude-haiku-5.5"


def test_the_default_profile_file_says_what_the_built_in_default_does():
    built_in, file = models.Profile(), models.load("default")
    for kind in (*costs.SYSTEM_ONE, *costs.SYSTEM_TWO):
        assert file.model_for(kind, cheap=False) == built_in.model_for(kind, cheap=False), kind


QS = {
    "intent": {"type": "choice", "instructions": "what next?", "criteria": {"fight": "f", "flee": "r", "loot": "l"}},
    "in_danger": {"type": "noul", "instructions": "in danger?", "criteria": {"true": "yes", "false": "no"}},
    "aggression": {"type": "score", "instructions": "how aggressive?", "criteria": ["cautious", "normal", "relentless"]},
}


def test_a_chat_models_stated_probabilities_become_answers():
    a, missing = models.parse_answers(models.first_json(
        'Sure:\n```json\n{"intent": {"fight": 0.6, "flee": 0.3, "loot": 0.1}, "in_danger": "35%", '
        '"aggression": {"0": 0, "1": 0.5, "2": 0.5}}\n```'), QS)
    assert a.choices["intent"].choice == "fight" and a.choices["intent"].probabilities["flee"] == pytest.approx(0.3)
    assert a.nouls["in_danger"] == pytest.approx(0.35) and a.scores["aggression"] == pytest.approx(0.75)
    assert not missing
    # A bare pick is certain; probabilities that don't sum to 1 are scaled; a missing answer is even.
    a, missing = models.parse_answers({"intent": "flee", "in_danger": {"true": 0.9}}, QS)
    assert a.choices["intent"].probabilities == {"fight": 0.0, "flee": 1.0, "loot": 0.0}
    assert a.nouls["in_danger"] == 0.9 and a.scores["aggression"] == 0.5 and missing == ["aggression"]
    a, _ = models.parse_answers({"intent": {"fight": 2, "flee": 2, "loot": 0}}, QS)
    assert a.choices["intent"].probabilities["fight"] == 0.5
    schema = models.answer_schema(QS)["json_schema"]["schema"]
    assert schema["properties"]["intent"]["required"] == ["fight", "flee", "loot"]
    assert schema["properties"]["aggression"]["required"] == ["0", "1", "2"]


def test_the_chat_judge_asks_through_openrouter_and_falls_back_without_a_schema(monkeypatch):
    sent = []

    async def chat(messages, **kw):
        sent.append(kw)
        if kw.get("response_format"):
            raise llm.LlmError("OpenRouter HTTP 400: response_format not supported", 400)
        return llm.ChatResult({}, '{"intent": {"fight": 0.9, "flee": 0.1, "loot": 0}, "in_danger": 0.2, '
                                  '"aggression": {"0": 1, "1": 0, "2": 0}}', [],
                              usage=llm.LlmUsage(model="qwen/qwen3.7-flash", prompt_tokens=900, completion_tokens=60,
                                                 cost=0.00004))

    monkeypatch.setattr(llm, "chat", chat)
    j = models.ChatJudge("qwen/qwen3.7-flash")
    a = asyncio.run(j.ask({"you": "a warrior"}, QS))
    assert a.choices["intent"].choice == "fight" and a.nouls["in_danger"] == 0.2 and a.scores["aggression"] == 0.0
    assert (a.model, a.input_tokens, a.output_tokens, a.cost) == ("qwen/qwen3.7-flash", 900, 60, 0.00004)
    assert sent[0]["response_format"] and "response_format" not in sent[1] and not j.schema_ok
    asyncio.run(j.ask({}, QS))
    assert len(sent) == 3  # the schema isn't tried again


class Fake:
    def __init__(self, name):
        self.name = name
        self.kinds = []

    async def ask(self, state, qs):
        self.kinds.append(costs.current_kind())
        return Answers(model=self.name)

    async def close(self):
        pass


def test_the_routed_judge_sends_each_kind_to_its_model():
    p = models.Profile(models={"system1": "jev", "routine": "haiku"})
    r = models.RoutedJudge.__new__(models.RoutedJudge)
    r.profile, r.provider = p, "auto"
    r._judges = {"jev": Fake("jev"), "anthropic/claude-haiku-5.5": Fake("haiku")}

    async def go():
        with costs.kind("fight"):
            a = await r.ask({}, {})
        with costs.kind("routine"):
            b = await r.ask({}, {})
        return a.model, b.model

    assert asyncio.run(go()) == ("jev", "haiku")
    assert models.is_jev("~typesafe/jev-latest") and models.is_jev("jev") and not models.is_jev("openai/gpt-oss-20b")


def test_a_model_of_jevs_kind_goes_the_way_jev_does(monkeypatch):
    """Microsoft's Decision 1 takes Jev's request unchanged (cuo-79x): the same judge with its id, not
    the chat adapter."""
    made = []

    class Client:
        def __init__(self, **kw):
            made.append(kw)

    import typesafe_sdk
    monkeypatch.setattr(typesafe_sdk, "AsyncTypeSafeClient", Client)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.delenv("JEV_MODEL", raising=False)
    assert judges.speaks_decisions("microsoft/microsoft-decision-1") and judges.speaks_decisions("~typesafe/jev-latest")
    assert not judges.speaks_decisions("anthropic/claude-haiku-5.5")
    r = models.make_judge("decision1", profile=models.Profile())
    assert r.name == "microsoft-decision-1/openrouter" and made[-1]["model"] == "microsoft/microsoft-decision-1"
    assert models.make_judge("jev", profile=models.Profile(), pin=True).name == "jev/openrouter"
    assert made[-1]["model"] == judges.OPENROUTER_MODEL
    assert isinstance(models.make_judge("haiku", profile=models.Profile()).judge_for("anthropic/claude-haiku-5.5"),
                      models.ChatJudge)
    # The older door (replay, strategy explain) too.
    j = judges.make("jev", model="decision1")
    assert isinstance(j, judges.JevJudge) and j.name == "microsoft-decision-1/openrouter"
    assert isinstance(judges.make("jev", model="haiku"), models.ChatJudge)
    assert costs.price_of("microsoft/microsoft-decision-1-20261009") == (0.042, 0.0, 0.042)


def test_a_pinned_jev_is_jev_in_every_call_whatever_the_profile_splits():
    p = models.Profile(models={"system1": "decision1", "routine": "jev", "strategy": "jev"})
    routed = models.make_judge("jev", profile=p)
    assert routed.profile.model_for("fight") == "microsoft/microsoft-decision-1"
    assert routed.profile.model_for("routine") == "jev"
    pinned = models.make_judge("jev", profile=p, pin=True)
    assert {pinned.profile.model_for(k) for k in costs.SYSTEM_ONE} == {"jev"}
