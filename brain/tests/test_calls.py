import copy

from uo_brain import calls, loop, policy, questions, state
from uo_brain.judge import Answers, ChoiceResult

from conftest import SNAPSHOT


def test_each_question_shows_its_options_jevs_pick_and_what_code_used():
    sit = state.build(copy.deepcopy(SNAPSHOT), set(), [])
    qs = questions.build(sit)
    a = Answers()
    a.choices["intent"] = ChoiceResult("fight", {"fight": 0.7, "rest": 0.2, "seek": 0.1}, 0.7)
    a.choices["target"] = ChoiceResult("t2", {"t1": 0.3, "t2": 0.6, "none": 0.1}, 0.6)
    a.nouls.update(in_danger=0.62, leave_now=0.3)
    view = {b["q"]: b for b in calls.view(qs, a, cuts={"in_danger": 0.5}, used={"intent": "fight", "target": "t1"})}
    intent = view["intent"]
    assert intent["title"] == "what next?" and intent["options"][0] == {"id": "fight", "label": "fight", "p": 0.7}
    assert intent["picked"] == "fight" and "used" not in intent
    target = view["target"]
    assert target["picked"] == "t2" and target["used"] == "t1"  # code went with the current target
    assert [o["label"] for o in target["options"]][:2] == ["an orc captain", "an orc"]
    assert view["in_danger"] == {"q": "in_danger", "title": "in danger?", "kind": "yesno", "p": 0.62, "cut": 0.5,
                                 "verdict": "yes"}


def test_the_decision_payload_carries_the_calls_view(snapshot):
    sit = state.build(snapshot, set(), [])
    qs = questions.build(sit)
    a = Answers()
    a.choices["intent"] = ChoiceResult("fight", {"fight": 1.0}, 1.0)
    dec = policy.decide(sit, a, policy.Memory(), policy.PolicyConfig())
    assert loop.decision_payload(type("J", (), {"name": "jev"})(), sit, qs, a, dec, [])["intents"]
