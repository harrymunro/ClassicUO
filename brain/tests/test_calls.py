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


def test_actions_are_put_in_words_with_where_a_move_goes():
    sit = state.build(copy.deepcopy(SNAPSHOT), set(), [])  # the player at 1000,1000; the orc 0x100 east
    acts = [{"verb": "flee", "target": 0, "tiles": 15}, {"verb": "walk_to", "x": 990, "y": 990},
            {"verb": "attack", "target": 0x100}, {"verb": "cast", "spell": "Protection", "target": "self"}]
    res = [{"status": "done", "detail": "15 tiles northwest"}, {"status": "done"}, {"status": "failed", "detail": "busy"}]
    assert calls.did(sit, acts, res) == [
        {"what": "run 15 tiles northwest from the pack", "result": ""},
        {"what": "walk 10 tiles northwest", "result": ""},
        {"what": "attack an orc", "result": "failed: busy"},
        {"what": "cast Protection at itself", "result": ""}]
    assert calls.compass(0, -3) == "north" and calls.compass(5, 5) == "southeast"
