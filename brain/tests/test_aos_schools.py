"""Necromancers and paladins (cuo-cvl.5)."""

import copy

from uo_brain import policy, questions, state
from uo_brain.judge import Answers, ChoiceResult

from conftest import SNAPSHOT


def spell(sid, name, school, mana, target="harmful", missing="", tithing=None):
    out = {"id": sid, "name": name, "school": school, "circle": 0, "mana": mana, "target": target, "missing": missing}
    if tithing is not None:
        out["tithing"] = tithing
    return out


def necro_snapshot():
    snap = copy.deepcopy(SNAPSHOT)
    p = snap["player"]
    p.update(name="Morbus", hits=85, hits_max=85, mana=90, mana_max=90, weapon="", buffs=[])
    p["skills"] = {"Necromancy": 90.0, "Spirit Speak": 80.0, "Meditation": 70.0, "Healing": 70.0, "Wrestling": 70.0}
    p["supplies"] = {"bandages": 100, "heal_potions": 5, "cure_potions": 5, "refresh_potions": 0,
                     "reagents": {"black_pearl": 0, "blood_moss": 0},
                     "pagan_reagents": {"bat_wing": 100, "grave_dust": 100, "daemon_blood": 100, "nox_crystal": 3,
                                        "pig_iron": 100}}
    snap["mobiles"][0].update(distance=5, dx=5, dy=0)
    snap["mobiles"][1].update(distance=12, dx=-12, dy=2)
    snap["corpses"] = []
    snap["magic"] = {"book_known": True, "casting": "", "cast_ready_ms": 0, "tithing": 0, "schools": ["necromancy"],
                     "spells": [spell(109, "Pain Spike", "necromancy", 5), spell(110, "Poison Strike", "necromancy", 17),
                                spell(111, "Strangle", "necromancy", 29), spell(116, "Wither", "necromancy", 23),
                                spell(104, "Curse Weapon", "necromancy", 7, "neutral")]}
    snap["agent"].update(bandaging=False, engaged=0x100, engaged_range=7)
    return snap


def paladin_snapshot():
    snap = copy.deepcopy(SNAPSHOT)
    p = snap["player"]
    p.update(name="Galahad", hits=95, hits_max=95, mana=45, mana_max=45, buffs=[])
    p["skills"] = {"Swordsmanship": 80.0, "Tactics": 80.0, "Healing": 80.0, "Anatomy": 80.0, "Chivalry": 90.0}
    snap["corpses"] = []
    snap["magic"] = {"book_known": True, "casting": "", "cast_ready_ms": 0, "tithing": 10000, "schools": ["chivalry"],
                     "spells": [spell(203, "Consecrate Weapon", "chivalry", 10, "neutral", tithing=10),
                                spell(205, "Divine Fury", "chivalry", 15, "neutral", tithing=10),
                                spell(206, "Enemy of One", "chivalry", 20, "neutral", tithing=10),
                                spell(207, "Holy Light", "chivalry", 10, tithing=10),
                                spell(202, "Close Wounds", "chivalry", 10, "beneficial", tithing=10)]}
    return snap


def answers(intent="fight", **choices):
    a = Answers()
    a.choices["intent"] = ChoiceResult(intent, {intent: 1.0}, 0.9)
    a.choices["target"] = ChoiceResult("t1", {"t1": 0.9}, 0.9)
    for k, v in choices.items():
        a.choices[k] = ChoiceResult(v, {v: 0.8}, 0.8)
    a.nouls["in_danger"] = 0.1
    return a


def test_a_necromancer_and_a_paladin_are_told_from_their_skills_and_books():
    assert state.archetype_of(necro_snapshot()) == "necromancer"
    assert state.archetype_of(paladin_snapshot()) == "paladin"
    # Without its book, a paladin is a warrior; without the skill, a necromancer isn't one.
    no_book = paladin_snapshot()
    no_book["magic"]["schools"] = []
    assert state.archetype_of(no_book) == "warrior"
    weak = necro_snapshot()
    weak["player"]["skills"]["Necromancy"] = 30.0
    assert state.archetype_of(weak) != "necromancer"


def test_a_necromancer_casts_its_own_spells_from_range_and_counts_its_own_reagents():
    sit = state.build(necro_snapshot(), set(), [])
    assert sit.is_mage and sit.is_necromancer
    assert [c.name for c in sit.spells] == ["Poison Strike", "Strangle", "Pain Spike"]
    you = sit.state["you"]
    assert you["reagents"] == "running out of nox crystal" and "necromancy" in you["weapon"]
    assert you["bandages_left"] == 100  # a necromancer bandages: no healing spells
    qs = questions.build(sit)
    assert qs["intent"]["instructions"]["role"].startswith("You are deciding for a necromancer")
    assert "the necromancer" in qs["intent"]["criteria"]["fight"]["when"]
    dec = policy.decide(sit, answers(), policy.Memory(), policy.PolicyConfig())
    cast = next(a for a in dec.actions if a["verb"] == "cast")
    assert cast["spell"] == "Poison Strike" and cast["target"] == 0x100  # nothing sure from Jev: the strongest
    assert not any(a.get("spell") == "Protection" for a in dec.actions)  # not a necromancer's spell


def test_a_necromancer_withers_a_crowd_around_it():
    snap = necro_snapshot()
    for i, (dx, dy) in enumerate([(1, 0), (0, 2), (-2, 1)]):
        snap["mobiles"].append({**snap["mobiles"][0], "serial": 0x200 + i, "dx": dx, "dy": dy,
                                "distance": max(abs(dx), abs(dy)), "my_target": False})
    snap["mobiles"] = [m for m in snap["mobiles"] if m.get("monster") or m["distance"] > 6]
    snap["mobiles"][2]["distance"] = 8  # Lord British keeps his distance
    sit = state.build(snap, set(), [])
    assert [c.name for c in sit.area_spells] == ["Wither"] and sit.area_count >= 3
    dec = policy.decide(sit, answers(), policy.Memory(), policy.PolicyConfig())
    assert any(a.get("spell") == "Wither" for a in dec.actions)


def test_a_paladin_blesses_its_fighting_with_jev_or_by_rule():
    sit = state.build(paladin_snapshot(), set(), [])
    assert sit.is_paladin and set(sit.blessings) == {"consecrate", "divine_fury", "enemy_of_one", "holy_light"}
    assert sit.state["you"]["tithing_points"] == 10000
    qs = questions.build(sit)
    assert "consecrate" in qs["blessing"]["criteria"] and qs["intent"]["instructions"]["role"].startswith(
        "You are deciding for a paladin")
    mem = policy.Memory()
    # Jev's pick, sure enough.
    dec = policy.decide(sit, answers(blessing="divine_fury"), mem, policy.PolicyConfig(), now=100.0)
    cast = next(a for a in dec.actions if a["verb"] == "cast")
    assert cast["spell"] == "Divine Fury" and cast["target"] == "self" and cast["reason"] == "blessing jev"
    # Then the rule of thumb: Consecrate Weapon, for this creature, and again after 20 s (no buff icon).
    sit.player["buffs"] = ["DivineFury"]
    dec = policy.decide(sit, answers(), mem, policy.PolicyConfig(), now=103.0)
    assert [a["spell"] for a in dec.actions if a["verb"] == "cast"] == ["Consecrate Weapon"]
    assert not [a for a in policy.decide(sit, answers(), mem, policy.PolicyConfig(), now=110.0).actions
                if a["verb"] == "cast"]
    assert [a["spell"] for a in policy.decide(sit, answers(), mem, policy.PolicyConfig(), now=124.0).actions
            if a["verb"] == "cast"] == ["Consecrate Weapon"]
    # Low on mana: no Enemy of One or Divine Fury from the rule of thumb.
    sit.player["buffs"] = []
    sit.mana_pct = 20
    dec = policy.decide(sit, answers(), policy.Memory(), policy.PolicyConfig(), now=200.0)
    assert [a["spell"] for a in dec.actions if a["verb"] == "cast"] == ["Consecrate Weapon"]
