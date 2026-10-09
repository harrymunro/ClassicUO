import asyncio
from types import SimpleNamespace

import pytest

from uo_brain import costs, llm, models
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
    assert p.model_for("planner") == "anthropic/claude-sonnet-5.5"
    monkeypatch.setenv("PLANNER_MODEL", "openai/gpt-oss-120b")
    assert p.model_for("review") == "openai/gpt-oss-120b"
    p.use("system2=anthropic/claude-opus-5.5")
    p.use("design=haiku")
    assert p.model_for("review") == "anthropic/claude-opus-5.5" and p.model_for("design") == "anthropic/claude-haiku-5.5"
    with pytest.raises(ValueError):
        p.use("planer=haiku")


def test_over_a_cheaper_budget_the_cheaper_models_answer():
    p = models.Profile(models={"system2": "sonnet"}, cheaper={"system2": "haiku"})
    costs.ledger.budget = costs.Budget(0.10, "cheaper")
    assert p.model_for("planner") == "anthropic/claude-sonnet-5.5"
    costs.ledger.record(costs.Call("planner", "m", cost=0.5))
    assert p.model_for("planner") == "anthropic/claude-haiku-5.5"
    assert p.model_for("fight") == "jev"  # no cheaper system one named: Jev stays
    costs.ledger.budget = costs.Budget(0.10, "slow")
    assert p.model_for("planner") == "anthropic/claude-sonnet-5.5"


def test_profiles_load_from_files():
    p = models.load("default")
    assert p.model_for("fight") == "jev" and p.model_for("planner") == "anthropic/claude-sonnet-5.5"
    assert p.cheaper["system2"] == "anthropic/claude-haiku-5.5" and p.budget is None
    npc = models.load("npc")
    assert npc.budget.per_hour == 0.15 and npc.model_for("planner") == "anthropic/claude-haiku-5.5"
    with pytest.raises(ValueError, match="profiles: default, npc"):
        models.load("nobody")
    args = SimpleNamespace(profile="npc", model="haiku", planner_model=None, use=["routine=jev"])
    p = models.from_args(args)
    assert p.model_for("fight") == "anthropic/claude-haiku-5.5" and p.model_for("routine") == "jev"


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
