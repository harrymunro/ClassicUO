import copy

import pytest

from uo_brain import policy, questions, state
from uo_brain.judge import Answers, ChoiceResult

from conftest import SNAPSHOT

CFG = policy.PolicyConfig()


@pytest.fixture
def archer():
    snap = copy.deepcopy(SNAPSHOT)
    p = snap["player"]
    p.update({"name": "Robin", "weapon": "a bow", "ranged": {"kind": "bow", "ammo": "arrows", "range": 10}})
    p["skills"] = {"Archery": 80.0, "Tactics": 80.0, "Healing": 80.0, "Anatomy": 80.0}
    p["supplies"].update({"arrows": 120, "bolts": 0})
    # t1 (the orc) 6 tiles off, t2 (the captain) 12: out of a bow's reach.
    snap["mobiles"][0].update({"distance": 6, "dx": 6, "dy": 0, "my_target": False})
    snap["mobiles"][1].update({"distance": 12, "dx": -12, "dy": 2})
    snap["agent"]["engaged"] = 0
    return snap


def sit_of(snapshot):
    return state.build(snapshot, set(), [])


def answers(intent="fight", target="t1", conf=0.9):
    a = Answers()
    a.choices["intent"] = ChoiceResult(intent, {k: (1.0 if k == intent else 0.0) for k in questions.ARCHER_INTENTS}, conf)
    a.choices["target"] = ChoiceResult(target, {target: 1.0}, 0.9)
    a.nouls["in_danger"] = 0.1
    return a


def adjacent(snap, n):
    for m in snap["mobiles"][:n]:
        m.update({"distance": 1, "dx": 1, "dy": 0})


def test_a_bow_in_hand_makes_an_archer(archer, snapshot):
    assert state.archetype_of(archer) == "archer"
    assert state.archetype_of(snapshot) == "warrior"
    sit = sit_of(archer)
    assert sit.is_archer and sit.ammo == 120


def test_archer_state_words_weapon_ammo_and_range(archer):
    sit = sit_of(archer)
    you = sit.state["you"]
    assert you["weapon"] == "a bow (shoots up to 10 tiles; needs arrows)"
    assert you["arrows_left"] == 120 and you["supplies"] == "plenty"
    t1, t2 = sit.state["hostile_creatures"]
    assert t1["in_shooting_range"] is True and t2["in_shooting_range"] is False


@pytest.mark.parametrize("arrows, words", [
    (12, "running low: 12 arrows, 40 bandages and 2 heal potions left"),
    (0, "nearly gone: no arrows left, so the bow cannot shoot"),
])
def test_ammo_counts_as_supplies(archer, arrows, words):
    archer["player"]["supplies"]["arrows"] = arrows
    assert sit_of(archer).state["you"]["supplies"] == words


def test_questions_speak_of_an_archer(archer):
    qs = questions.build(sit_of(archer))
    assert "archer" in qs["intent"]["instructions"]["role"]
    assert qs["intent"]["criteria"]["fight"]["what"].startswith("Shoot")
    assert "within shooting range" in qs["target"]["criteria"]["t1"]
    assert "out of shooting range" in qs["target"]["criteria"]["t2"]


def test_archer_engages_from_range(archer):
    dec = policy.decide(sit_of(archer), answers(), policy.Memory(), CFG)
    assert [a["verb"] for a in dec.actions] == ["attack"]
    assert dec.actions[0]["target"] == 0x100 and dec.actions[0]["range"] == CFG.bow_range
    assert dec.note.startswith("shoot an orc")


def test_a_crossbow_shoots_no_further_than_it_reaches(archer):
    archer["player"]["ranged"] = {"kind": "crossbow", "ammo": "bolts", "range": 7}
    archer["player"]["supplies"]["bolts"] = 50
    dec = policy.decide(sit_of(archer), answers(), policy.Memory(), CFG)
    assert dec.actions[0]["range"] == 7


def test_no_arrows_no_fight(archer):
    archer["player"]["supplies"]["arrows"] = 0
    dec = policy.decide(sit_of(archer), answers(), policy.Memory(), CFG)
    assert dec.intent != "fight" and "fight" in dec.masked
    assert not any(a["verb"] == "attack" for a in dec.actions)


def test_combat_assist_says_when_the_arrows_are_gone(archer):
    archer["agent"]["mode"] = "assist"
    archer["player"]["supplies"]["arrows"] = 0
    dec = policy.decide(sit_of(archer), answers(), policy.Memory(), CFG, now=100.0)
    assert {"verb": "hint", "text": "out of arrows", "reason": "ammo"} in dec.actions


def test_archer_steps_back_once_per_shot(archer):
    adjacent(archer, 2)
    archer["agent"]["engaged"] = 0x100
    archer["agent"]["engaged_range"] = CFG.bow_range
    mem = policy.Memory()
    first = policy.decide(sit_of(archer), answers(), mem, CFG, now=100.0)
    assert [a["verb"] for a in first.actions] == ["kite"]
    # No shot since (the arrows haven't gone down): no second step, however long it waits.
    again = policy.decide(sit_of(archer), answers(), mem, CFG, now=110.0)
    assert "kite" not in [a["verb"] for a in again.actions]
    archer["player"]["supplies"]["arrows"] -= 1
    after_shot = policy.decide(sit_of(archer), answers(), mem, CFG, now=114.0)
    assert "kite" in [a["verb"] for a in after_shot.actions]


def test_archer_seeks_to_within_range(archer):
    for m in archer["mobiles"][:2]:
        m.update({"distance": 14, "dx": 14, "dy": 0})
    dec = policy.decide(sit_of(archer), answers(intent="seek"), policy.Memory(), CFG)
    assert dec.actions[0]["verb"] == "walk_to" and dec.actions[0]["distance"] == CFG.bow_range - 1


def test_bench_judge_labels_can_turn_kiting_off():
    from uo_brain.bench import JudgeSpec
    j = JudgeSpec("jev+survivor/nokite")
    assert (j.kind, j.template, j.kite) == ("jev", "survivor", False)
    assert (JudgeSpec("heuristic").kind, JudgeSpec("heuristic").kite) == ("heuristic", True)
