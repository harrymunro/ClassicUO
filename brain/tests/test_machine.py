import copy

import pytest

from uo_brain import machine, policy, questions, state
from uo_brain.judge import Answers, ChoiceResult

from conftest import SNAPSHOT

PULL = {
    "name": "pull one at a time",
    "why": "fight one creature at a time and get out before several are on the warrior",
    "start": "pull",
    "states": {
        "pull": {"says": "Take on the nearest creature; don't go looking for more.", "allow": ["fight", "rest"],
                 "target": "closest_first",
                 "transitions": [
                     {"to": "regroup", "when": "Health is falling faster than bandages bring it back.",
                      "requires": ["health below 60"]},
                     {"to": "out", "when": "Several are coming at once.", "requires": ["3+ coming"], "at": 0.5}]},
        "regroup": {"says": "Back off and heal; fight only what is on the warrior.", "allow": ["flee", "rest"],
                    "loot": "nothing", "max_s": 20, "then": "pull",
                    "transitions": [{"to": "pull", "when": "Health is back above three quarters."}]},
        "out": {"says": "Leave the area.", "allow": ["leave", "rest"],
                "transitions": [{"to": "pull", "when": "Nothing is coming any more.", "requires": ["none in sight"]}]},
    },
}


class Clock:
    t = 100.0

    def __call__(self):
        return self.t


def answers(intent="fight", **nouls):
    a = Answers()
    a.choices["intent"] = ChoiceResult(intent, {k: 1.0 if k == intent else 0.0 for k in questions.INTENTS}, 0.9)
    a.choices["target"] = ChoiceResult("t1", {"t1": 0.9}, 0.9)
    a.nouls = {"in_danger": 0.1, **nouls}
    return a


def test_a_machine_is_checked_and_described():
    m = machine.parse(PULL)
    assert m.start == "pull" and list(m.states) == ["pull", "regroup", "out"]
    assert "* pull [fight, rest]" in m.describe() and "regroup if health below 60" in m.describe()
    assert machine.parse(m.to_json()).to_json() == m.to_json()
    bad = copy.deepcopy(PULL)
    bad["states"]["pull"]["allow"] = ["dance"]
    bad["states"]["pull"]["transitions"][0]["to"] = "nowhere"
    bad["states"]["out"]["transitions"][0]["requires"] = ["when the moon is full"]
    with pytest.raises(machine.MachineError) as e:
        machine.parse(bad)
    assert "unknown intents ['dance']" in str(e.value) and "unknown state 'nowhere'" in str(e.value)
    assert "unknown condition 'when the moon is full'" in str(e.value)


def test_only_the_current_states_transitions_are_asked_and_only_when_code_allows():
    snap = copy.deepcopy(SNAPSHOT)  # 60% health, an orc adjacent and an orc captain coming
    r = machine.Runner(machine.parse(PULL), clock=Clock())
    qs = r.questions(state.build(snap, set(), []), "warrior", "role")
    assert list(qs) == []  # health isn't below 60, and only two are coming
    snap["player"]["hits"] = 50
    qs = r.questions(state.build(snap, set(), []), "warrior", "role")
    assert list(qs) == ["go_regroup"]
    q = qs["go_regroup"]
    assert q["type"] == "noul" and q["criteria"]["true"].startswith("Health is falling")
    assert "keep to 'pull'" in q["criteria"]["false"] and "plan_step" in q["instructions"]


def test_jev_moves_the_machine_and_the_state_limits_the_policy():
    clock = Clock()
    r = machine.Runner(machine.parse(PULL), clock=clock)
    snap = copy.deepcopy(SNAPSHOT)
    snap["player"]["hits"] = 50
    sit = state.build(snap, set(), [])
    assert r.advance({"go_regroup": 0.4}) is None and r.state == "pull"
    clock.t += 5
    assert r.advance({"go_regroup": 0.7}) == "pull -> regroup (0.70)" and r.state == "regroup"
    cfg = r.config(policy.PolicyConfig())
    assert cfg.allowed_intents == ("flee", "rest") and cfg.looting == "nothing"
    # Jev wanted to fight, but this step is about backing off; the orc on it gets fought only if it
    # can't get away, and here it can.
    dec = policy.decide(sit, answers("fight"), policy.Memory(), cfg)
    assert dec.intent != "fight" and "fight" in dec.masked
    # The time limit takes it back.
    clock.t += 21
    assert r.advance({}) == "regroup -> pull (after 20 s)"
    assert r.summary()["seconds_in"] == {"pull": 5.0, "regroup": 21.0}


def test_floors_hold_whatever_the_state_allows():
    """A plan can't stop the emergency flee or keep a character standing still while hit."""
    r = machine.Runner(machine.parse({"name": "stand", "states": {"stand": {"says": "Wait.", "allow": ["rest"]}}}))
    snap = copy.deepcopy(SNAPSHOT)
    snap["player"]["hits"] = 15
    sit = state.build(snap, set(), [])
    supplies = sit.player["supplies"]
    supplies["heal_potions"] = 0
    dec = policy.decide(sit, answers("rest", in_danger=0.95), policy.Memory(), r.config(policy.PolicyConfig()))
    assert dec.intent == "flee"  # near death, sure of the danger, no potion: code's own flee
    # Hit with no way out allowed: it fights back.
    snap["player"]["hits"] = 90
    dec = policy.decide(state.build(snap, set(), []), answers("fight"), policy.Memory(), r.config(policy.PolicyConfig()))
    assert dec.intent == "fight"


def test_the_plan_reaches_every_choice_question():
    r = machine.Runner(machine.parse(PULL))
    sit = state.build(copy.deepcopy(SNAPSHOT), set(), [])
    sit.plan = r.plan_words()
    qs = questions.build(sit)
    assert qs["intent"]["instructions"]["plan_step"].startswith("pull: Take on the nearest")
    assert "plan" not in qs["in_danger"]["instructions"]  # a factual judgment, as the strategy is kept out


def test_shipped_machines_parse():
    names = sorted(p.stem for p in machine.MACHINES_DIR.glob("*.json"))
    assert names, "brain/machines has examples"
    for n in names:
        machine.load(n)


def test_a_hunt_starts_the_plan_afresh():
    clock = Clock()
    r = machine.Runner(machine.parse(PULL), clock=clock)
    r.advance({"go_out": 0.9})
    clock.t += 10
    r.reset()
    assert r.state == "pull" and r.moves == 1 and r.summary()["seconds_in"]["out"] == 10.0
