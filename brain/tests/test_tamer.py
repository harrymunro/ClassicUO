import copy

import pytest
from conftest import SNAPSHOT

from uo_brain import policy, questions, state
from uo_brain.judge import Answers, ChoiceResult

CFG = policy.PolicyConfig()
BEAR = 0x200


@pytest.fixture
def tamer():
    snap = copy.deepcopy(SNAPSHOT)
    p = snap["player"]
    p.update({"name": "Ranger", "weapon": "", "hits": 70, "hits_max": 70})
    p["skills"] = {"Animal Taming": 90.0, "Animal Lore": 90.0, "Veterinary": 90.0, "Healing": 70.0, "Wrestling": 50.0}
    # The orc 6 tiles off, the captain 12; the bear at the tamer's side.
    snap["mobiles"][0].update({"distance": 6, "dx": 6, "dy": 0, "my_target": False})
    snap["mobiles"][1].update({"distance": 12, "dx": -12, "dy": 2})
    snap["mobiles"].append({"serial": BEAR, "name": "a grizzly bear", "body": 212, "notoriety": "innocent", "human": False,
                            "pet": True, "monster": False, "hits_pct": 100, "dead": False, "poisoned": False,
                            "war_mode": False, "distance": 1, "dx": 0, "dy": 1, "dir": "south", "my_target": False})
    snap["pets"] = [{"serial": BEAR, "name": "a grizzly bear", "body": 212, "hits_pct": 100, "poisoned": False,
                     "distance": 1, "dx": 0, "dy": 1, "dir": "south"}]
    snap["agent"].update({"engaged": 0, "bandaging": False, "pet_order": "follow", "pet_target": 0})
    return snap


def sit_of(snapshot):
    return state.build(snapshot, set(), [])


def answers(intent="fight", target="t1", conf=0.9, **nouls):
    a = Answers()
    a.choices["intent"] = ChoiceResult(intent, {k: (1.0 if k == intent else 0.0) for k in questions.TAMER_INTENTS}, conf)
    a.choices["target"] = ChoiceResult(target, {target: 1.0}, 0.9)
    a.nouls.update({"in_danger": 0.1, **nouls})
    return a


def fighting(snap, pct):
    snap["pets"][0]["hits_pct"] = pct
    snap["agent"].update({"pet_order": "kill", "pet_target": 0x100})


def test_taming_skills_make_a_tamer(tamer, snapshot):
    assert state.archetype_of(tamer) == "tamer"
    assert state.archetype_of(snapshot) == "warrior"


def test_tamer_state_describes_the_pet_not_as_a_bystander(tamer):
    fighting(tamer, 55)
    sit = sit_of(tamer)
    pet = sit.state["you"]["pet"]
    assert pet == {"name": "a grizzly bear", "health": "wounded (55%)", "distance": "adjacent, within weapon reach",
                   "fighting": "an orc"}
    assert all(o["kind"] != "your pet" for o in sit.state["other_beings_nearby"])
    assert sit.state["hostile_creatures"][0]["your_pet_is_fighting_it"] is True


def test_no_pet_no_fight(tamer):
    tamer["pets"] = []
    tamer["mobiles"].pop()
    sit = sit_of(tamer)
    assert sit.state["you"]["pet"].startswith("none in sight")
    dec = policy.decide(sit, answers(), policy.Memory(), CFG)
    assert dec.intent != "fight" and "fight" in dec.masked


def test_questions_speak_of_a_tamer_and_ask_about_the_pet_only_when_it_struggles(tamer):
    qs = questions.build(sit_of(tamer))
    assert "tamer" in qs["intent"]["instructions"]["role"]
    assert qs["intent"]["criteria"]["fight"]["what"].startswith("Send the pet")
    assert "pull_back" not in qs
    fighting(tamer, 45)
    qs = questions.build(sit_of(tamer))
    assert qs["pull_back"]["instructions"]["reason"] == "The pet is badly wounded (45%), fighting an orc (badly wounded)."


def test_tamer_sets_the_pet_on_the_target_and_stays_back(tamer):
    dec = policy.decide(sit_of(tamer), answers(), policy.Memory(), CFG)
    assert [a["verb"] for a in dec.actions] == ["pet"]
    assert dec.actions[0]["kind"] == "kill" and dec.actions[0]["target"] == 0x100
    tamer["agent"].update({"pet_order": "kill", "pet_target": 0x100})
    again = policy.decide(sit_of(tamer), answers(), policy.Memory(), CFG)
    assert again.actions == [] and again.note.startswith("pet fighting an orc")


def test_tamer_bandages_its_hurt_pet_within_reach(tamer):
    fighting(tamer, 62)
    mem = policy.Memory()
    dec = policy.decide(sit_of(tamer), answers(), mem, CFG, now=50.0)
    assert {"verb": "bandage", "target": BEAR, "confidence": 1.0, "reason": "pet"} in dec.actions
    soon = policy.decide(sit_of(tamer), answers(), mem, CFG, now=51.0)
    assert not any(a["verb"] == "bandage" for a in soon.actions)


def test_tamer_walks_to_a_hurt_pet_out_of_reach(tamer):
    fighting(tamer, 62)
    tamer["pets"][0].update({"distance": 5, "dx": 5, "dy": 0})
    dec = policy.decide(sit_of(tamer), answers(), policy.Memory(), CFG, now=50.0)
    walk = next(a for a in dec.actions if a["verb"] == "walk_to")
    assert (walk["x"], walk["y"], walk["distance"]) == (1005, 1000, 1)


def test_jev_calls_the_pet_back(tamer):
    fighting(tamer, 45)
    mem = policy.Memory()
    dec = policy.decide(sit_of(tamer), answers(pull_back=0.8), mem, CFG, now=10.0)
    assert [a["verb"] for a in dec.actions][:2] == ["pet", "flee"]
    assert dec.actions[0]["kind"] == "follow" and dec.actions[1]["target"] == 0x100
    # While it comes back the pet isn't sent in again.
    tamer["agent"].update({"pet_order": "follow", "pet_target": 0})
    after = policy.decide(sit_of(tamer), answers(), mem, CFG, now=12.0)
    assert not any(a["verb"] == "pet" and a["kind"] == "kill" for a in after.actions)


def test_code_calls_a_nearly_dead_pet_back(tamer):
    fighting(tamer, 15)
    dec = policy.decide(sit_of(tamer), answers(pull_back=0.2), policy.Memory(), CFG, now=10.0)
    assert dec.actions[0] == {"verb": "pet", "kind": "follow", "confidence": 1.0, "reason": "pull back"}


def test_combat_assist_tells_the_player_to_call_the_pet(tamer):
    tamer["agent"]["mode"] = "assist"
    fighting(tamer, 45)
    dec = policy.decide(sit_of(tamer), answers(pull_back=0.8), policy.Memory(), CFG, now=10.0)
    assert {"verb": "hint", "text": "call your pet back, it is losing", "reason": "pet"} in dec.actions
    assert not any(a["verb"] in ("flee", "pet") for a in dec.actions)


def test_leaving_takes_the_pet_along(tamer):
    tamer["mobiles"][1]["name"] = "an ogre lord"
    dec = policy.decide(sit_of(tamer), answers(intent="leave", conf=0.9, in_danger=0.9), policy.Memory(), CFG, now=10.0)
    assert [a["verb"] for a in dec.actions][:2] == ["pet", "flee"] and dec.actions[0]["kind"] == "follow"


def test_a_hunt_ends_when_the_pet_is_gone(tamer):
    from uo_brain.routine import HuntWatch, RoutineConfig
    hw = HuntWatch("Test field", 10, None, RoutineConfig(), log=lambda *a, **k: None)
    hw.observe(tamer)
    gone = copy.deepcopy(tamer)
    gone["pets"] = []
    assert hw.floor(gone, 0) == "the pet is gone"


def test_whatever_attacks_the_tamer_gets_the_pet(tamer):
    fighting(tamer, 90)
    tamer["mobiles"][1].update({"distance": 1, "dx": -1, "dy": 0, "attacking_me": True})
    dec = policy.decide(sit_of(tamer), answers(target="t1"), policy.Memory(), CFG, now=10.0)
    assert dec.actions[0] == {"verb": "pet", "kind": "kill", "target": 0x101, "confidence": 0.9, "reason": "fight"}
    assert not any(a["verb"] == "kite" for a in dec.actions)


def test_tamer_keeps_close_to_a_distant_pet(tamer):
    tamer["pets"][0].update({"distance": 9, "dx": 9, "dy": 0})
    dec = policy.decide(sit_of(tamer), answers(intent="rest"), policy.Memory(), CFG, now=10.0)
    walk = next(a for a in dec.actions if a["verb"] == "walk_to")
    assert (walk["x"], walk["distance"]) == (1009, 2)


def test_without_its_pet_a_tamer_leaves_rather_than_fights(tamer):
    tamer["pets"] = []
    tamer["mobiles"].pop()
    tamer["mobiles"][0].update({"distance": 2, "dx": 2})  # an orc close, nothing far stronger
    a = answers(intent="fight", in_danger=0.2)
    a.choices["intent"] = ChoiceResult("fight", {"fight": 0.48, "leave": 0.43, "flee": 0.07, "rest": 0.02}, 0.36)
    dec = policy.decide(sit_of(tamer), a, policy.Memory(), CFG, now=10.0)
    assert dec.intent == "leave" and not any(a["verb"] == "pet" and a["kind"] == "kill" for a in dec.actions)


def test_a_leaving_tamer_waits_for_and_calls_a_lagging_pet(tamer):
    mem = policy.Memory(leaving_until=100.0)
    tamer["mobiles"][0].update({"distance": 5, "dx": 5})
    tamer["pets"][0].update({"distance": 8, "dx": -8})
    dec = policy.decide(sit_of(tamer), answers(), mem, CFG, now=50.0)
    assert dec.note == "leaving, waiting for the pet"
    assert dec.actions == [{"verb": "pet", "kind": "follow", "confidence": 1.0, "reason": "leave"}]


def test_a_tamer_caught_while_leaving_lets_the_pet_hold_off_the_chaser(tamer):
    mem = policy.Memory(leaving_until=100.0)
    tamer["mobiles"][1].update({"name": "an ogre lord", "distance": 2, "dx": -2})
    sit = state.build(tamer, set(), [], bestiary={7: {"hits": 500, "difficulty": "deadly"}})
    dec = policy.decide(sit, answers(), mem, CFG, now=50.0)
    assert [a["verb"] for a in dec.actions] == ["pet", "flee"]
    assert dec.actions[0]["kind"] == "kill" and dec.actions[0]["target"] == 0x101
