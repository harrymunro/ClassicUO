import pytest
from conftest import mage_snapshot

from uo_brain import policy, questions, state
from uo_brain.judge import Answers, ChoiceResult

CFG = policy.PolicyConfig()
BEAR = 0x200


@pytest.fixture
def mage_tamer():
    snap = mage_snapshot()
    snap["player"]["skills"].update({"Animal Taming": 85.0, "Animal Lore": 85.0, "Veterinary": 60.0})
    snap["pets"] = [{"serial": BEAR, "name": "a grizzly bear", "body": 212, "hits_pct": 100, "poisoned": False,
                     "distance": 2, "dx": 2, "dy": 0, "dir": "east"}]
    snap["agent"].update({"engaged": 0, "pet_order": "follow", "pet_target": 0, "bandaging": False})
    return snap


@pytest.fixture
def warrior_mage():
    snap = mage_snapshot()
    snap["player"]["skills"].update({"Swordsmanship": 85.0, "Tactics": 85.0, "Magery": 75.0})
    snap["player"]["weapon"] = "a katana"
    snap["player"]["supplies"]["bandages"] = 60
    snap["agent"]["engaged"] = 0
    return snap


def sit_of(snapshot):
    return state.build(snapshot, set(), [])


def answers(intent="fight", target="t1", spell="s2", intents=questions.INTENTS):
    a = Answers()
    a.choices["intent"] = ChoiceResult(intent, {k: (1.0 if k == intent else 0.0) for k in intents}, 0.9)
    a.choices["target"] = ChoiceResult(target, {target: 1.0}, 0.9)
    a.choices["spell"] = ChoiceResult(spell, {spell: 0.8}, 0.8)
    a.nouls["in_danger"] = 0.1
    return a


def test_hybrids_are_told_apart_by_skills_and_what_is_in_hand(mage_tamer, warrior_mage):
    assert state.archetype_of(mage_tamer) == "mage-tamer"
    assert state.archetype_of(warrior_mage) == "warrior-mage"
    warrior_mage["player"]["weapon"] = ""
    assert state.archetype_of(warrior_mage) == "warrior"  # no weapon, and Magery below the sword skill
    assert state.archetype_of(mage_snapshot()) == "mage"


def test_mage_tamer_sets_the_pet_on_the_target_and_casts_at_it(mage_tamer):
    sit = sit_of(mage_tamer)
    assert sit.is_tamer and sit.is_mage and "also a mage" in questions.role(sit)
    assert "spell" in questions.build(sit) and "pull_back" not in questions.build(sit)
    dec = policy.decide(sit, answers(intents=questions.TAMER_INTENTS), policy.Memory(), CFG, now=10.0)
    verbs = [(a["verb"], a.get("kind") or a.get("spell")) for a in dec.actions]
    assert verbs == [("pet", "kill"), ("attack", None), ("cast", "Explosion")]
    assert dec.note.startswith("set the pet on an orc, fight an orc with Explosion")


def test_mage_tamer_heals_its_pet_with_a_spell_from_a_distance(mage_tamer):
    mage_tamer["pets"][0].update({"hits_pct": 50, "distance": 6, "dx": 6})
    mage_tamer["agent"].update({"pet_order": "kill", "pet_target": 0x100})
    dec = policy.decide(sit_of(mage_tamer), answers(intents=questions.TAMER_INTENTS), policy.Memory(), CFG, now=10.0)
    assert dec.actions[-1] == {"verb": "cast", "spell": "Greater Heal", "target": BEAR, "queue": True,
                               "confidence": 1.0, "reason": "pet"}


def test_warrior_mage_opens_on_a_creature_still_coming_then_fights_in_melee(warrior_mage):
    sit = sit_of(warrior_mage)
    assert sit.is_warrior_mage and not sit.is_mage and sit.casts
    you = sit.state["you"]
    assert you["weapon"] == "a katana, and spells to open a fight" and "bandages_left" in you and "mana" in you
    assert "also a mage" in questions.role(sit) and "spell" in questions.build(sit)
    dec = policy.decide(sit, answers(), policy.Memory(), CFG, now=10.0)
    assert [a["verb"] for a in dec.actions] == ["cast", "attack"]
    assert dec.actions[0]["reason"] == "opener" and "range" not in dec.actions[1]
    # Adjacent: no spell, the sword.
    warrior_mage["mobiles"][0].update({"distance": 1, "dx": 1})
    dec = policy.decide(sit_of(warrior_mage), answers(), policy.Memory(), CFG, now=10.0)
    assert [a["verb"] for a in dec.actions] == ["attack"]


def test_a_seeking_warrior_mage_opens_with_a_spell_instead_of_walking_up(warrior_mage):
    dec = policy.decide(sit_of(warrior_mage), answers(intent="seek"), policy.Memory(), CFG, now=10.0)
    assert [a["verb"] for a in dec.actions] == ["attack", "cast"] and dec.actions[1]["reason"] == "opener"
